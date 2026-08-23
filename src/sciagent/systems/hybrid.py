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
investigation continues. So does a third case, which only an arm that proposes
outside the library can reach: a candidate that is well-formed and executes, but
that some design in the table's set cannot be measured on
(:class:`~sciagent.core.errors.StructureNotMeasurableError`). It is recorded as
``"unmeasurable"`` and costs the proposal, matching
``BeamSearch``'s ``_UNSCORABLE``, which costs an unscorable candidate its rank
rather than the search. Until 2026-08-18 it was uncaught, and it stopped item
15's first LLM cells mid-campaign.

A :class:`~sciagent.core.errors.TranscriptMissError` is
**not** caught: that one means the harness was asked to replay a call nobody
recorded, which is a configuration fault rather than a scientific event, and
swallowing it would turn a broken replay into a quietly worse result.

Gate A35 narrowed what a miss can mean without touching this clause. A refusal is
now recorded as a transcript in its own right and re-raised from
:meth:`~sciagent.systems.llm.transcripts.TranscriptStore.resolve` on replay, so a
replicate scored as refused replays and reaches the same ``except`` below with
the same :attr:`~sciagent.core.errors.ProviderError.cause`. A miss therefore no
longer has a legitimate reading at all -- it was one before, since a refused
address was absent from the corpus by construction.

Neither is a fault of the *machine*. Gate A44 moved five conditions out of the
recorded tier for the same reason -- a contaminated environment, a call from
inside a running event loop, a turn served by another provider or another model,
a session whose provenance cannot be verified -- and they now raise
:class:`~sciagent.core.errors.SystemConfigurationError`, which sits outside
``ProposalError`` and so cannot be caught here at all.

Five tiers were not enough to say what happened
-----------------------------------------------

Every attempt also carries a :attr:`ProposalAttempt.cause`, in a vocabulary
finer than :attr:`~ProposalAttempt.outcome`: ``"refused"`` alone held a model
declining, an output ceiling, a dead session, an unreadable payload and an
exhausted expansion budget, and only the first is unambiguously a fact about the
model. The causes are counted **beside** the scored record and never inside it
(:class:`~sciagent.eval.agency.ProposalCauses`) -- ``ProposalRecord``'s five
fields are what SPEC §12 criterion 11 reads, so a sixth would have moved
``yield_fraction``'s denominator and re-scored 1,120 frozen rows for a change
touching no estimator. ``docs/DECISIONS.md`` (2026-08-21) records that as **T3**.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from sciagent.core.edits import Defect
from sciagent.core.errors import (
    BudgetExhaustedError,
    MalformedProposalError,
    ProviderError,
    StructureNotMeasurableError,
    SystemConfigurationError,
)
from sciagent.core.types import Diagnosis, ExperimentTemplateId, HypothesisId
from sciagent.experiments import boed
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.hypothesis.validator import find_duplicate
from sciagent.systems.base import Investigation, entertain
from sciagent.systems.llm.provider import Proposal, slug_hypothesis_name

__all__ = ["MAX_PROPOSALS", "Hybrid", "ProposalAttempt", "ProposalSource"]

#: The largest proposal allowance a :class:`Hybrid` may be built with.
#:
#: A ceiling rather than a default, because of an asymmetry in
#: :meth:`Hybrid._extend`: it breaks on ``"refused"`` and continues on the other
#: four outcomes, so :attr:`~sciagent.eval.agency.ProposalRecord.requested` --
#: and therefore ``yield_fraction`` -- depends on which outcome arrives. At 2
#: this is harmless, since declining can only lower the ratio. At 3 or more a
#: model unable to produce a second admission would score **strictly higher by
#: declining**, which is a metric rewarding the thing it exists to measure the
#: absence of.
#:
#: Gate A44 was required to settle this in the same change as the retiering
#: (``docs/OPEN-DECISIONS.md`` §2, ``docs/DECISIONS.md`` 2026-08-21). It is
#: settled by *not* changing the break rule -- which outcome breaks decides how
#: many requests a run makes, so moving it would move a number the recorded
#: matrix reported, which is the whole of what T3 declines to spend -- and by
#: making the allowance a guarded ceiling instead. Raising it is then a decision
#: somebody takes with the asymmetry in front of them, rather than a constructor
#: argument nobody reads.
MAX_PROPOSALS: Final = 2


@runtime_checkable
class ProposalSource(Protocol):
    """Where a :class:`Hybrid` gets structure from.

    Structural rather than nominal, and consumer-side, for the reason
    :class:`~sciagent.eval.campaign.Proposing` is both: this class has to be able
    to hold more than one kind of source, and must not thereby depend on which.
    Two exist. :class:`~sciagent.systems.llm.provider.ProposalLayer` is V7's, and
    goes to a model through a transcript store.
    :class:`~sciagent.systems.baselines.uniform.UniformProposer` is B6's, and
    draws from the grammar's structural menu at random.

    That B6 is this class holding the second is what makes SPEC §12 criterion 5's
    comparison clean: the two arms differ in how a point in the action space is
    chosen and in nothing else, because everything else -- the library, the Stage
    A gate, the budget split, the selection policy -- is :meth:`Hybrid.investigate`
    either way. The annotation used to name ``ProposalLayer`` concretely, which
    made "same gate, same split" a thing to reimplement rather than to inherit.

    One method, deliberately. A source that also reported, say, how many calls it
    had made would be a source the harness could read differently per arm.
    :attr:`Hybrid.attempts` is where a run's record of asking lives, and it is
    written by this class from what :meth:`propose` returned or raised.
    """

    def propose(self, investigation: Investigation) -> Proposal:
        """Return one proposal for the state ``investigation`` is in.

        May raise :class:`~sciagent.core.errors.ProviderError` -- the backend was
        reached and declined -- or
        :class:`~sciagent.core.errors.MalformedProposalError`, a payload that does
        not denote a licensed structure. :meth:`Hybrid._propose_once` converts
        both into recorded outcomes, because for a research system a refusal is a
        result rather than a crash. A source that can raise neither, as a uniform
        draw cannot, simply never produces those outcomes.
        """
        ...


@dataclass(frozen=True, slots=True)
class ProposalAttempt:
    """One request to the proposal layer, and what became of it.

    Kept so that a run reports how many times the model was asked and what
    happened, rather than only what survived. A system that asked five times and
    used one proposal did something different from one that asked once, and
    SPEC §12 criterion 11's autonomy fraction will need the distinction.
    """

    address: str | None
    """What produced the proposal, or ``None`` if nothing did.

    The transcript address of the model call where a
    :class:`~sciagent.systems.llm.provider.ProposalLayer` produced it. A
    :class:`ProposalSource` that reaches no provider writes its own
    provenance instead -- B6's reads ``uniform:{seed}:{index}`` -- so this
    is not resolvable against a transcript store in general. ``None`` means
    the attempt produced no proposal at all: a refusal, or a draft that did
    not decode. Nothing in the framework reads this for a number; see
    :func:`~sciagent.eval.agency.proposal_record`, which counts
    :attr:`outcome` alone."""

    node_id: HypothesisId | None
    """The hypothesis admitted, or ``None`` if nothing was."""

    outcome: str
    """``"admitted"``, ``"duplicate"``, ``"refused"``, ``"malformed"`` or
    ``"unmeasurable"``.

    The tiers are not free-form: :data:`sciagent.eval.agency.PROPOSAL_OUTCOMES`
    is derived from :class:`~sciagent.eval.agency.ProposalRecord`'s fields and
    :func:`~sciagent.eval.agency.proposal_record` raises on anything outside it,
    so a new outcome here needs a field there in the same change.
    """

    cause: str
    """Why the attempt ended, in a vocabulary finer than :attr:`outcome`.

    The scored side of the record has five tiers and five is not enough to say
    what happened: ``"refused"`` alone held a model declining, an output ceiling,
    a dead session, an unreadable payload and an exhausted expansion budget.
    :data:`sciagent.eval.agency.PROPOSAL_CAUSES` is the vocabulary and
    :data:`~sciagent.eval.agency.OUTCOME_OF_CAUSE` maps each cause to the one
    tier it belongs to; :func:`~sciagent.eval.agency.proposal_causes` raises both
    on an unknown cause and on a cause disagreeing with the outcome beside it.

    **This is not a second outcome, and nothing scored may read it.** Gate A44's
    T3 keeps ``ProposalRecord``'s five fields frozen precisely so
    ``yield_fraction``'s denominator cannot move; a cause that reached a score
    would reintroduce the denominator change T3 exists to avoid
    (``docs/DECISIONS.md``, 2026-08-21).

    Written by the framework, never by a source. For a failure it is the raise
    site's own tag, read off the exception rather than parsed out of its message
    -- prose a provider authored may not decide a number the framework reports
    (SPEC's second invariant), and :attr:`detail` is that prose.
    """

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
        layer: ProposalSource,
        *,
        max_proposals: int = MAX_PROPOSALS,
        name: str = "V7",
    ) -> None:
        if max_proposals < 0:
            raise SystemConfigurationError(
                f"a system cannot make {max_proposals} proposals; pass 0 for a V7 "
                f"that never extends its hypothesis space"
            )
        if max_proposals > MAX_PROPOSALS:
            raise SystemConfigurationError(
                f"a system cannot be built with {max_proposals} proposals while "
                f"_extend breaks on 'refused' and continues on the other four "
                f"outcomes: the break asymmetry makes declining score strictly "
                f"higher than failing to admit at any allowance above "
                f"{MAX_PROPOSALS}. Raising the ceiling means settling which "
                f"outcomes break, which moves yield_fraction on every recorded "
                f"run -- see MAX_PROPOSALS"
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
            # The tag comes off the exception, not off its message. Both classes
            # carry one: ``ProviderError``'s is required at every raise site, and
            # ``MalformedProposalError``'s is fixed, since every way that one is
            # raised is the same diagnostic fact.
            return ProposalAttempt(None, None, "refused", error.cause, str(error))
        except MalformedProposalError as error:
            return ProposalAttempt(
                None, None, "malformed", MalformedProposalError.cause, str(error)
            )
        return self._admit(investigation, proposal)

    @staticmethod
    def _admit(investigation: Investigation, proposal: Proposal) -> ProposalAttempt:
        """Entertain a proposal, unless the graph already holds its structure.

        A duplicate is skipped rather than proposed, because SPEC §6.4 A18 makes
        re-proposing a structure an error and §12 asks for zero zombie
        hypotheses. It is still *reported*, since a model that keeps proposing
        what is already entertained is doing something worth seeing.

        The name is slugged **here** and not left to the source. It used to be
        left to :class:`~sciagent.systems.llm.provider.ProposalLayer`, which was
        sound while that was the only thing ``Hybrid`` could hold; under
        :class:`ProposalSource` it would be a guarantee each source had to
        remember separately. What it protects is not cosmetic:
        :func:`~sciagent.eval.scoring._enabled_value` reserves the id
        ``__candidate__`` for D5's candidate slot, and an entertained hypothesis
        carrying that id would have its structure overwritten and its mass
        dropped from the comparison belief -- a scored dimension moved by a name
        a source chose. Slugging is idempotent, so ``ProposalLayer``'s own call
        is unaffected and V7's node ids do not move.
        """
        node_id = HypothesisId(slug_hypothesis_name(proposal.name))
        existing = find_duplicate(investigation.graph, proposal.program_edit)
        if existing is not None:
            return ProposalAttempt(
                proposal.address,
                existing,
                "duplicate",
                "duplicate",
                proposal.rationale,
            )
        if node_id in investigation.graph.nodes:
            node_id = HypothesisId(f"{node_id}/{len(investigation.proposed)}")
        try:
            investigation.propose(
                node_id,
                program_edit=proposal.program_edit,
                rationale=proposal.rationale,
            )
        except BudgetExhaustedError as error:
            return ProposalAttempt(
                proposal.address, None, "refused", "budget", str(error)
            )
        except StructureNotMeasurableError as error:
            # The candidate is well-formed, on-grid and executes; what fails is
            # a *design* in the table's set, which yields no row on it. That is
            # a fact about the pair, not about the model, so it costs the
            # proposal rather than the run -- exactly as
            # ``BeamSearch._UNSCORABLE`` costs a candidate its rank. Counted in
            # its own tier because folding it into "refused" would attribute a
            # measurement limit to a provider that declined nothing.
            return ProposalAttempt(
                proposal.address, None, "unmeasurable", "measurement", str(error)
            )
        return ProposalAttempt(
            proposal.address, node_id, "admitted", "admitted", proposal.rationale
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
