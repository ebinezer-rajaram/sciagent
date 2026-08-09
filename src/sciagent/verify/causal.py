"""Causal licensing by estimand (SPEC §7.2, §6.5 A21).

SPEC §0 settles the principle: "collateral effects are not required to vanish.
The estimand is typed and the verifier checks alignment between intervention,
graph structure, declared assumptions and claimed estimand." §7.2 gives the four
rows, one per estimand type, and this module is those four rows.

Downgrade, do not refuse
------------------------

§7.2: "Misalignment triggers automatic downgrade to the strongest licensed
estimand rather than rejection, because the weaker claim is usually true and
rejecting outright would push the agent away from causal language entirely." So
:func:`license` returns the strongest estimand the record supports, which may be
weaker than the one claimed, and only refuses when nothing at all is licensed.

The ladder it descends is built from the claim, not invented: a controlled direct
effect falls back to the direct effect over the same held-fixed set, and every
estimand falls back to the total effect between the same endpoints. There is no
rung the claimant did not describe, because a verifier that proposed a mediator
set of its own would be authoring the claim it is judging.

Broad interventions, broad claims
---------------------------------

§7.2 again: "If ``manipulated`` covers three components, a total-effect claim
about their union is licensed; a component-specific direct-effect claim is not."
That falls out of the rows rather than needing its own rule -- a total effect
asks only that its target was manipulated, and a direct effect asks in addition
that every mediating path was blocked, which a broad intervention does not do.

One experiment at a time
------------------------

An estimand is licensed by an experiment, not by a citation list: two experiments
that each do half of what a controlled direct effect needs do not add up to one
that did all of it. :func:`license` therefore takes the best licence any single
cited record grants.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    AssumptionCode,
    Claim,
    ComponentId,
    ControlledDirectEffect,
    DirectEffect,
    Estimand,
    PathSpecificEffect,
    TotalEffect,
    estimand_endpoints,
)
from sciagent.verify.relevance import EvidenceRecord
from sciagent.verify.verdict import CheckClass, Finding, Outcome

__all__ = ["STRENGTH_ORDER", "Licence", "ladder", "license", "licensed_by"]

#: The estimand types from weakest to strongest, in the order SPEC §7.2's table
#: lists them. Each row demands everything the row above it does and more, which
#: is what makes "the strongest licensed estimand" well defined.
STRENGTH_ORDER: Final[tuple[type, ...]] = (
    TotalEffect,
    DirectEffect,
    ControlledDirectEffect,
    PathSpecificEffect,
)


def _rank(estimand: Estimand) -> int:
    return STRENGTH_ORDER.index(type(estimand))


@dataclass(frozen=True, slots=True)
class Licence:
    """What SPEC §7.2 permits a claim to say, given what was actually done."""

    claimed: Estimand | None
    licensed: Estimand | None
    findings: tuple[Finding, ...]

    @property
    def granted(self) -> bool:
        """Return whether the claim may stand at the estimand it claimed."""
        return self.licensed is not None and self.licensed == self.claimed


def _paths(
    program: GenerativeProgram, source: ComponentId, sink: ComponentId
) -> tuple[tuple[ComponentId, ...], ...]:
    """Return every directed path from ``source`` to ``sink``, in a fixed order.

    Enumerated in full rather than counted: the direct-effect row asks whether
    *every* mediating path is blocked, which is a statement about paths and not
    about reachability. The slice's DAG has four components, and the enumeration
    is exponential in the general case -- a limitation recorded in
    ``docs/DECISIONS.md`` rather than hidden behind an approximation that would
    license a claim it should not.
    """
    found: list[tuple[ComponentId, ...]] = []
    stack: list[tuple[ComponentId, ...]] = [(source,)]
    while stack:
        path = stack.pop()
        for parent, target in sorted(program.edges):
            if parent != path[-1] or target in path:
                continue
            extended = (*path, target)
            if target == sink:
                found.append(extended)
            else:
                stack.append(extended)
    return tuple(sorted(found))


def _mediated_paths(
    program: GenerativeProgram, source: ComponentId, sink: ComponentId
) -> tuple[tuple[ComponentId, ...], ...]:
    """Return the paths from source to sink that pass through something else."""
    return tuple(path for path in _paths(program, source, sink) if len(path) > 2)


def _is_edge(
    program: GenerativeProgram, source: ComponentId, target: ComponentId
) -> bool:
    return (source, target) in program.edges


def licensed_by(
    estimand: Estimand,
    assumptions: frozenset[AssumptionCode],
    program: GenerativeProgram,
    record: EvidenceRecord,
) -> str | None:
    """Return why ``record`` does not license ``estimand``, or ``None`` if it does.

    A string rather than a bool so that a refusal can say which row of SPEC §7.2
    it failed, which is the difference between a verdict a reader can act on and
    one they have to reverse-engineer.
    """
    target, outcome = estimand_endpoints(estimand)
    if target not in record.manipulated:
        return (
            f"experiment {record.experiment!r} did not manipulate {target!r}; it "
            f"manipulated {sorted(record.manipulated)!r}"
        )
    if outcome not in program.descendants(target):
        return (
            f"{outcome!r} is not downstream of {target!r} in the programme, so no "
            f"intervention on {target!r} could have an effect on it"
        )

    match estimand:
        case TotalEffect():
            return None

        case DirectEffect():
            unblocked = [
                path
                for path in _mediated_paths(program, target, outcome)
                if not (set(path[1:-1]) & estimand.mediators_blocked)
            ]
            if unblocked:
                return (
                    f"the path(s) {unblocked!r} from {target!r} to {outcome!r} carry "
                    f"mediation that {sorted(estimand.mediators_blocked)!r} does not "
                    f"block"
                )
            if AssumptionCode.MEDIATORS_BLOCKED not in assumptions:
                return (
                    f"a direct effect requires the blocking assumption to be listed; "
                    f"the intervention lists {sorted(a.value for a in assumptions)!r}"
                )
            return None

        case ControlledDirectEffect():
            unblocked = [
                path
                for path in _mediated_paths(program, target, outcome)
                if not (set(path[1:-1]) & estimand.held_fixed)
            ]
            if unblocked:
                return (
                    f"the path(s) {unblocked!r} from {target!r} to {outcome!r} are "
                    f"not covered by the held-fixed set "
                    f"{sorted(estimand.held_fixed)!r}"
                )
            if not estimand.held_fixed <= record.held_fixed:
                return (
                    f"{sorted(estimand.held_fixed - record.held_fixed)!r} were "
                    f"declared held fixed but experiment {record.experiment!r} held "
                    f"{sorted(record.held_fixed)!r} fixed"
                )
            missing = {
                AssumptionCode.MEDIATORS_BLOCKED,
                AssumptionCode.HELD_FIXED,
            } - assumptions
            if missing:
                return (
                    f"a controlled direct effect requires "
                    f"{sorted(code.value for code in missing)!r} to be listed"
                )
            return None

        case PathSpecificEffect():
            for source, following in zip(
                estimand.path, estimand.path[1:], strict=False
            ):
                if not _is_edge(program, source, following):
                    return (
                        f"{(source, following)!r} is not an edge of the programme, "
                        f"so the claimed path does not exist"
                    )
            off_path = program.descendants(target) - set(estimand.path)
            # Parenthesised: `-` binds tighter than `&`, so the unbracketed form
            # meant this and read as though it meant `(off_path & manipulated)`.
            loose = off_path & (record.manipulated - record.held_fixed)
            if loose:
                return (
                    f"the off-path descendant(s) {sorted(loose)!r} were manipulated "
                    f"and not held fixed"
                )
            if (off_path & record.held_fixed) and (
                AssumptionCode.OFF_PATH_CONTROLLED not in assumptions
            ):
                return (
                    f"off-path descendants were held fixed, which requires "
                    f"{AssumptionCode.OFF_PATH_CONTROLLED.value!r} to be listed"
                )
            return None


def ladder(estimand: Estimand) -> tuple[Estimand, ...]:
    """Return the estimands to try, strongest first, ending at the total effect.

    Every rung is built from what the claim already declared. Nothing is invented:
    a verifier that supplied its own mediator set would be authoring the claim.
    """
    target, outcome = estimand_endpoints(estimand)
    rungs: list[Estimand] = [estimand]
    if isinstance(estimand, ControlledDirectEffect) and estimand.held_fixed:
        rungs.append(DirectEffect(target, outcome, estimand.held_fixed))
    if not isinstance(estimand, TotalEffect):
        rungs.append(TotalEffect(target, outcome))
    return tuple(sorted(rungs, key=_rank, reverse=True))


def license(
    claim: Claim, program: GenerativeProgram, records: Sequence[EvidenceRecord]
) -> Licence:
    """Return the strongest estimand SPEC §7.2 licenses for ``claim``.

    Guarantees a non-causal claim is licensed nothing and refused nothing: this
    module governs causal language only. Guarantees also that a claim is granted
    its own estimand only when some single cited experiment satisfies that
    estimand's whole row, and that a claim whose estimand no experiment supports
    is refused rather than quietly downgraded to nothing.
    """
    if claim.modality != "causal" or claim.estimand is None:
        return Licence(claimed=claim.estimand, licensed=None, findings=())
    if claim.intervention is None:
        return Licence(
            claimed=claim.estimand,
            licensed=None,
            findings=(
                Finding(
                    check=CheckClass.CAUSAL,
                    outcome=Outcome.REJECT,
                    message=(
                        f"claim {claim.id!r} is causal and declares no intervention, "
                        f"so nothing states what was done or what was assumed"
                    ),
                ),
            ),
        )

    assumptions = frozenset(claim.intervention.assumptions)
    if not records:
        return Licence(
            claimed=claim.estimand,
            licensed=None,
            findings=(
                Finding(
                    check=CheckClass.CAUSAL,
                    outcome=Outcome.REJECT,
                    message=(
                        f"claim {claim.id!r} is causal and cites no experiment; an "
                        f"estimand is licensed by something that was done, and "
                        f"nothing was"
                    ),
                ),
            ),
        )
    refusals: list[str] = []
    for candidate in ladder(claim.estimand):
        reasons = [
            licensed_by(candidate, assumptions, program, record) for record in records
        ]
        if any(reason is None for reason in reasons):
            if candidate == claim.estimand:
                return Licence(claimed=claim.estimand, licensed=candidate, findings=())
            return Licence(
                claimed=claim.estimand,
                licensed=candidate,
                findings=(
                    Finding(
                        check=CheckClass.CAUSAL,
                        outcome=Outcome.DOWNGRADE,
                        message=(
                            f"claim {claim.id!r} claims {claim.estimand!r} but the "
                            f"record licenses only {candidate!r}: "
                            f"{'; '.join(r for r in refusals if r)}"
                        ),
                    ),
                ),
            )
        refusals.extend(reason for reason in reasons if reason is not None)

    return Licence(
        claimed=claim.estimand,
        licensed=None,
        findings=(
            Finding(
                check=CheckClass.CAUSAL,
                outcome=Outcome.REJECT,
                message=(
                    f"claim {claim.id!r} claims {claim.estimand!r} and the cited "
                    f"record licenses no causal estimand at all between those "
                    f"components: {'; '.join(r for r in refusals if r)}"
                ),
            ),
        ),
    )
