"""What a check returns, and what a claim's adjudication amounts to.

SPEC §10 lists the seven check modules and not this one. It exists because the
seven need a shared vocabulary -- an outcome, a finding, the bundle of things a
check reads -- and a module that defines it is better than seven that each
import from whichever of the others happened to declare it first. The orchestrator
:func:`sciagent.verify.verify` lives in the package's ``__init__`` rather than
here, so that this module can be imported by every check without a cycle.

Five outcomes, not two
----------------------

A verifier with only "pass" and "fail" cannot express SPEC §7.2, which requires a
misaligned causal claim to be *downgraded to the strongest licensed estimand*
rather than refused -- "because the weaker claim is usually true and rejecting
outright would push the agent away from causal language entirely". It also
cannot express §7.1's third category, which is reported rather than resolved. And
it cannot express the thing acceptance test A23 measures, which is whether a
claim was decided at all.

So: :attr:`Outcome.ACCEPT`, :attr:`Outcome.NOTE` (recorded, decides nothing),
:attr:`Outcome.DOWNGRADE` (accepted, but weaker than claimed),
:attr:`Outcome.REFER` (not mechanically decidable) and :attr:`Outcome.REJECT`.

A verdict takes the most decisive outcome among its findings, in that order, so
a claim that is refused on one ground and referred on another counts as
adjudicated: the verifier reached a decision without a human, which is exactly
what A23 asks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    Claim,
    ClaimId,
    EffectEstimate,
    Estimand,
    FrozenDict,
    HypothesisId,
    Probability,
)
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.verify.relevance import EvidenceIndex, EvidenceRecord

__all__ = [
    "DECISIVENESS",
    "CheckClass",
    "ClaimContext",
    "Finding",
    "Outcome",
    "Verdict",
    "worst",
]


class Outcome(Enum):
    """What one check, or one whole adjudication, came to."""

    ACCEPT = "accept"
    NOTE = "note"
    DOWNGRADE = "downgrade"
    REFER = "refer"
    REJECT = "reject"


#: The outcomes in increasing decisiveness. A verdict is the last one reached by
#: any of its findings. ``REJECT`` outranks ``REFER`` deliberately: a claim
#: refused on a mechanical ground has been decided, whatever else about it a
#: human would have had to settle.
DECISIVENESS: Final[tuple[Outcome, ...]] = (
    Outcome.ACCEPT,
    Outcome.NOTE,
    Outcome.DOWNGRADE,
    Outcome.REFER,
    Outcome.REJECT,
)


def worst(outcomes: Iterable[Outcome]) -> Outcome:
    """Return the most decisive of ``outcomes``, or ``ACCEPT`` if there are none."""
    return max(outcomes, key=DECISIVENESS.index, default=Outcome.ACCEPT)


class CheckClass(Enum):
    """SPEC F8's six mechanical check classes, plus causal licensing (§7.2)."""

    NUMERICAL = "numerical"
    COMPLETENESS = "completeness"
    SCOPE = "scope"
    STATISTICAL = "statistical"
    LOGICAL = "logical"
    CONTRADICTION = "contradiction"
    CAUSAL = "causal"


@dataclass(frozen=True, slots=True)
class Finding:
    """One check's report on one claim.

    ``message`` is for a human reading the record; nothing branches on it. What
    a caller may branch on is :attr:`check` and :attr:`outcome`, both typed.
    """

    check: CheckClass
    outcome: Outcome
    message: str


@dataclass(frozen=True, slots=True)
class ClaimContext:
    """Everything the checks read. Assembled by the caller, never by a system.

    ``accepted`` is the claims already admitted in this investigation, which is
    what :mod:`sciagent.verify.contradiction` compares against; ``posterior`` is
    the engine's belief, which is what an ``exclusive`` uniqueness assertion is
    judged against. Both default to empty, and a check that needs one it was not
    given refers the claim rather than guessing.
    """

    graph: HypothesisGraph
    evidence: EvidenceIndex
    program: GenerativeProgram
    accepted: tuple[Claim, ...] = ()
    posterior: FrozenDict[HypothesisId, Probability] = field(default_factory=FrozenDict)

    def cited(self, claim: Claim) -> tuple[EvidenceRecord, ...]:
        """Return the evidence records ``claim`` cites, in citation order."""
        return tuple(self.evidence.record(experiment) for experiment in claim.evidence)


@dataclass(frozen=True, slots=True)
class Verdict:
    """The adjudication of one claim.

    Guarantees :attr:`outcome` is the most decisive outcome among
    :attr:`findings`, so a verdict cannot be gentler than something it reports.
    """

    claim: ClaimId
    outcome: Outcome
    findings: tuple[Finding, ...]
    licensed: Estimand | None
    """The strongest estimand SPEC §7.2 licenses, which may be weaker than the
    one claimed. ``None`` for a non-causal claim and for a causal claim nothing
    licenses."""

    recomputed: EffectEstimate | None
    """What the registry says the claim's effect is. ``None`` when the claim
    carries none, or when its evidence cannot produce one."""

    def __post_init__(self) -> None:
        implied = worst(finding.outcome for finding in self.findings)
        if self.outcome is not implied:
            raise AssertionError(
                f"verdict on {self.claim!r} reports {self.outcome.value!r} but its "
                f"findings imply {implied.value!r}"
            )

    @property
    def adjudicated(self) -> bool:
        """Return whether the verifier decided this claim without human input.

        The quantity SPEC §6.5 A23 measures.
        """
        return self.outcome is not Outcome.REFER

    @property
    def accepted(self) -> bool:
        """Return whether the claim stands, possibly at a weaker estimand."""
        return self.outcome in (Outcome.ACCEPT, Outcome.NOTE, Outcome.DOWNGRADE)

    def by_check(self) -> Mapping[CheckClass, tuple[Finding, ...]]:
        """Return the findings grouped by the check that made them."""
        grouped: dict[CheckClass, list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault(finding.check, []).append(finding)
        return {check: tuple(found) for check, found in grouped.items()}
