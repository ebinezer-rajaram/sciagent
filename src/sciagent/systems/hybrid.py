"""V7: the LLM proposes and revises, BOED selects (SPEC §5).

The proposed architecture, and the only system in the suite that can extend its
own hypothesis space. Everything it does that V1 does, it does *the same way* --
same library, same one-step-greedy selection through
:func:`sciagent.experiments.boed.plan`, same framework-computed posterior -- so
the difference between the two is the proposal step and nothing else. That is
deliberate: SPEC §9's preregistered contrast is about extension quality, and a
V7 that also selected differently would confound the two.

The loop, and why it has this shape
-----------------------------------

Half the budget, then a check, then a proposal, then the rest. B4 and B5 already
split their budgets that way and for the same reason -- a proposal made before
any observation is a proposal made from the prior alone -- so V7 splitting it
differently would make the three incomparable on how much evidence informed the
proposal.

The check is the gate. SPEC F6 splits out-of-library evaluation into Stage A
(detection, conventional) and Stage B (extension quality, **conditional on
detection**), and F5 assigns detection to conventional methods. So V7 proposes
only when :meth:`~sciagent.systems.base.Investigation.ppc` says the entertained
hypotheses do not explain what was seen. On a scenario whose truth is in the
library the check passes, no proposal is made, and V7 *is* V1 -- which is the
honest architecture rather than a shortcut, because a system that proposed
regardless would be reporting Stage B performance on runs where Stage A never
fired, and §9's contrast conditions on exactly that.

What it declares that no baseline does
--------------------------------------

``targets`` on every experiment. Item 10 recorded that no SPEC §5 baseline passes
it, so §7.1's clauses 1 and 6 never fire on real slice runs and evidence
relevance rests on clauses 2 to 5. BOED already knows the set it is separating,
so V7 supplies it for free.

Failure is an outcome, not a crash
----------------------------------

A provider that refuses, or a draft that does not decode, leaves V7 with a budget
still to spend and a diagnosis still to give, so both are recorded and the
investigation continues. A :class:`~sciagent.core.errors.TranscriptMissError` is
**not** caught: that one means the harness was asked to replay a call nobody
recorded, which is a configuration fault rather than a scientific event, and
swallowing it would turn a broken replay into a quietly worse result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sciagent.core.edits import Defect
from sciagent.core.errors import (
    BudgetExhaustedError,
    MalformedProposalError,
    ProviderError,
    SystemConfigurationError,
)
from sciagent.core.types import Diagnosis, ExperimentTemplateId, HypothesisId
from sciagent.experiments import boed
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.hypothesis.validator import find_duplicate
from sciagent.systems.base import Investigation, entertain
from sciagent.systems.llm.provider import Proposal, ProposalLayer

__all__ = ["Hybrid", "ProposalAttempt"]


@dataclass(frozen=True, slots=True)
class ProposalAttempt:
    """One request to the proposal layer, and what became of it.

    Kept so that a run reports how many times the model was asked and what
    happened, rather than only what survived. A system that asked five times and
    used one proposal did something different from one that asked once, and
    SPEC §12 criterion 11's autonomy fraction will need the distinction.
    """

    address: str | None
    """The transcript address, or ``None`` if the call never produced one."""

    node_id: HypothesisId | None
    """The hypothesis admitted, or ``None`` if nothing was."""

    outcome: str
    """``"admitted"``, ``"duplicate"``, ``"refused"`` or ``"malformed"``."""

    detail: str
    """The rationale for an admitted proposal, or the reason it failed."""


class Hybrid:
    """SPEC §5's V7.

    Guarantees the library is entertained in full before anything is selected or
    proposed, that every experiment is chosen by expected information gain, that
    a proposal is made only when the posterior predictive check reports the
    hypothesis space inadequate, and that no structure outside the injected
    grammar is ever entertained -- the proposal layer's menu is derived from that
    grammar, so an unlicensed structure has no name a model could use.
    """

    __slots__ = ("_attempts", "_layer", "_library", "_max_proposals", "_name")

    def __init__(
        self,
        library: Mapping[str, Defect],
        layer: ProposalLayer,
        *,
        max_proposals: int = 2,
        name: str = "V7",
    ) -> None:
        if max_proposals < 0:
            raise SystemConfigurationError(
                f"a system cannot make {max_proposals} proposals; pass 0 for a V7 "
                f"that never extends its hypothesis space"
            )
        if not name.strip():
            raise SystemConfigurationError(
                "a system needs a SPEC §5 identifier; an unnamed one would be "
                "scored under an empty label and could not be told apart from "
                "another arm of the same ablation"
            )
        self._library = dict(library)
        self._layer = layer
        self._max_proposals = max_proposals
        self._name = name.strip()
        self._attempts: tuple[ProposalAttempt, ...] = ()

    @property
    def name(self) -> str:
        """Return SPEC §5's identifier.

        ``"V7"`` unless the caller said otherwise. SPEC §11 item 13's ablation
        arms are this same architecture with a different memory representation
        in the brief, so they are this class under the names ``"V3"`` and
        ``"V4"`` -- see :func:`sciagent.systems.ablation.memory_ablation`. A
        separate class per arm would have made "they differ only in memory" a
        claim to audit rather than a fact about how they are built.
        """
        return self._name

    @property
    def attempts(self) -> tuple[ProposalAttempt, ...]:
        """Return every proposal requested in the last :meth:`investigate`.

        The record of what the model was asked and what became of it, in the
        order it happened. Empty before the first run, and empty after a run
        whose posterior predictive check never fired -- which is the honest
        report that no proposal was made, distinct from proposals that were made
        and refused.

        A :class:`~sciagent.core.types.Diagnosis` cannot carry this: SPEC §3.4
        has no field for it, and the two failure outcomes admit no hypothesis, so
        there is nothing about them a distribution could say. SPEC §12 criterion
        11's autonomy fraction is computed over runs, and this is what it reads.
        """
        return self._attempts

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Entertain, observe, extend if the check says to, then spend the rest.

        Records the proposal attempts on the system, where :attr:`attempts`
        publishes them; a run that asked five times and used one did something
        different from one that asked once, and the diagnosis alone cannot say so.
        """
        # Cleared first, so a run that raises before the extension phase leaves
        # no attempts rather than the previous run's.
        self._attempts = ()
        entertain(investigation, self._library)
        total = int(investigation.budget.remaining)
        self._select(investigation, (total + 1) // 2)

        if investigation.ppc().inadequate:
            self._attempts = tuple(self._extend(investigation))

        self._select(investigation, int(investigation.budget.remaining))
        return investigation.conclude(residual_candidates=self._residual_candidates())

    def _residual_candidates(self) -> tuple[HypothesisId, ...]:
        """Return the hypotheses this run surfaced without committing to.

        The structures the model proposed that the graph already held. They are
        entertained, so the posterior already prices them; what makes them
        *residual* is that the model pointed at one while the check said the
        entertained set explains nothing -- it is asking for a second look at a
        hypothesis the evidence has not settled, which is what SPEC §3.4's field
        is for. An admitted proposal is not residual: it is in the distribution
        on its own account. A refused or malformed one names no hypothesis at all.
        """
        return tuple(
            attempt.node_id
            for attempt in self._attempts
            if attempt.node_id is not None and attempt.outcome == "duplicate"
        )

    # -- internals ---------------------------------------------------------

    def _select(self, investigation: Investigation, steps: int) -> None:
        """Spend ``steps`` experiments by expected information gain.

        The plan runs as one trajectory rather than step by step so that the
        belief BOED selects against is the belief it updates -- see
        :func:`sciagent.experiments.boed.greedy` on why designs are chosen with
        replacement.
        """
        if steps < 1:
            return
        by_id: dict[ExperimentTemplateId, ExperimentDesign] = {
            design.id: design for design in investigation.designs
        }

        def observe(_index: int, template: ExperimentTemplateId) -> int:
            design = by_id[template]
            result = investigation.run(design, targets=self._live(investigation))
            return design.template().outcome.cell_of(result.result)

        boed.plan(investigation.engine, tuple(by_id), observe, steps=steps)

    @staticmethod
    def _live(investigation: Investigation) -> tuple[HypothesisId, ...]:
        """Return the hypotheses an experiment chosen now is aimed at.

        Every live hypothesis, because one-step-greedy selection maximises
        expected information gain over the whole belief rather than over a
        nominated pair. Declaring a narrower set would be declaring something
        untrue about what the experiment was for, and SPEC §7.1 clause 1 reads
        this to decide relevance.
        """
        return tuple(sorted(investigation.engine.live))

    def _extend(self, investigation: Investigation) -> list[ProposalAttempt]:
        """Ask for structure, up to ``max_proposals`` times, and entertain it."""
        attempts: list[ProposalAttempt] = []
        for _ in range(self._max_proposals):
            attempt = self._propose_once(investigation)
            attempts.append(attempt)
            if attempt.outcome == "refused":
                break
        return attempts

    def _propose_once(self, investigation: Investigation) -> ProposalAttempt:
        """Make one proposal, converting a failure into a recorded outcome."""
        try:
            proposal = self._layer.propose(investigation)
        except ProviderError as error:
            return ProposalAttempt(None, None, "refused", str(error))
        except MalformedProposalError as error:
            return ProposalAttempt(None, None, "malformed", str(error))
        return self._admit(investigation, proposal)

    @staticmethod
    def _admit(investigation: Investigation, proposal: Proposal) -> ProposalAttempt:
        """Entertain a proposal, unless the graph already holds its structure.

        A duplicate is skipped rather than proposed, because SPEC §6.4 A18 makes
        re-proposing a structure an error and §12 asks for zero zombie
        hypotheses. It is still *reported*, since a model that keeps proposing
        what is already entertained is doing something worth seeing.
        """
        node_id = HypothesisId(proposal.name)
        existing = find_duplicate(investigation.graph, proposal.program_edit)
        if existing is not None:
            return ProposalAttempt(
                proposal.address, existing, "duplicate", proposal.rationale
            )
        if node_id in investigation.graph.nodes:
            node_id = HypothesisId(f"{proposal.name}/{len(investigation.proposed)}")
        try:
            investigation.propose(
                node_id,
                program_edit=proposal.program_edit,
                rationale=proposal.rationale,
            )
        except BudgetExhaustedError as error:
            return ProposalAttempt(proposal.address, None, "refused", str(error))
        return ProposalAttempt(
            proposal.address, node_id, "admitted", proposal.rationale
        )


def library_of(
    names: Sequence[str], library: Mapping[str, Defect]
) -> dict[str, Defect]:
    """Return the named subset of a library, in the order given.

    A convenience for building a V7 whose closed set differs from V1's, which
    the ablations of SPEC §11 item 13 will want. Raises
    :class:`~sciagent.core.errors.SystemConfigurationError` on an unknown name
    rather than silently returning a smaller library, since a system
    entertaining fewer structures than it was configured with would be scored as
    though the omission were a choice.
    """
    missing = [name for name in names if name not in library]
    if missing:
        raise SystemConfigurationError(
            f"library has no structure(s) {missing!r}; it holds "
            f"{sorted(library)!r}. A system configured with a structure that "
            f"does not exist would be scored as though not entertaining it were "
            f"a choice"
        )
    return {name: library[name] for name in names}
