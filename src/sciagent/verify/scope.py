"""Does the claim hold where it says it holds?

A claim declares a :class:`~sciagent.core.types.Scope`: the families, the
parameter ranges and the environment version it asserts over. The evidence it
cites was gathered in one place. This module refuses the gap between them, which
is extrapolation reported as measurement.

The rule is containment, not overlap. SPEC §7.1 clause 3 uses *overlap* to decide
whether an experiment is relevant to a claim, which is the right test for "should
this have been cited" and the wrong one for "does this support that": an
experiment overlapping the edge of a claimed range is relevant to it and does not
establish it. The two are different questions and are answered by different
predicates on purpose.
"""

from __future__ import annotations

from sciagent.core.types import Claim, FamilyId, FrozenDict, Scope
from sciagent.verify.relevance import EvidenceIndex
from sciagent.verify.verdict import CheckClass, Finding, Outcome

__all__ = ["check", "covers"]


def covers(evidence: Scope, claimed: Scope) -> tuple[str, ...]:
    """Return the axes on which ``evidence`` fails to cover ``claimed``.

    Empty when the evidence reaches everywhere the claim asserts.
    """
    unsupported: list[str] = []
    if evidence.env_version != claimed.env_version:
        unsupported.append(
            f"environment version ({evidence.env_version} vs {claimed.env_version})"
        )
    missing_families = claimed.families - evidence.families
    if missing_families:
        unsupported.append(f"families {sorted(missing_families)!r}")
    for name in sorted(claimed.parameters):
        low, high = claimed.parameters[name]
        if name not in evidence.parameters:
            unsupported.append(f"parameter {name!r}, unmeasured")
            continue
        evidence_low, evidence_high = evidence.parameters[name]
        if low < evidence_low or high > evidence_high:
            unsupported.append(
                f"parameter {name!r} over {low}..{high}, measured only over "
                f"{evidence_low}..{evidence_high}"
            )
    return tuple(unsupported)


def _union(claim: Claim, index: EvidenceIndex) -> Scope | None:
    """Return the widest scope the cited evidence jointly covers.

    ``None`` when nothing is cited, or when the citations disagree about the
    environment version -- a claim resting on two environment versions is
    supported by neither, and merging them here would invent a scope no
    experiment ran in.
    """
    scopes = [index.record(experiment).scope for experiment in claim.evidence]
    if not scopes:
        return None
    versions = {scope.env_version for scope in scopes}
    if len(versions) != 1:
        return None
    families: set[FamilyId] = set()
    parameters: dict[str, tuple[float, float]] = {}
    for scope in scopes:
        families.update(scope.families)
        for name in sorted(scope.parameters):
            low, high = scope.parameters[name]
            if name in parameters:
                held_low, held_high = parameters[name]
                parameters[name] = (min(held_low, low), max(held_high, high))
            else:
                parameters[name] = (low, high)
    first = scopes[0]
    return Scope(
        families=frozenset(families),
        parameters=FrozenDict[str, tuple[float, float]](parameters),
        env_version=first.env_version,
        metric_version=first.metric_version,
        grammar_version=first.grammar_version,
    )


def check(claim: Claim, index: EvidenceIndex) -> tuple[Finding, ...]:
    """Return a refusal for every axis on which the claim outruns its evidence."""
    reach = _union(claim, index)
    if reach is None:
        if not claim.evidence:
            return ()  # logical.check refuses an unevidenced claim; not twice.
        return (
            Finding(
                check=CheckClass.SCOPE,
                outcome=Outcome.REJECT,
                message=(
                    f"claim {claim.id!r} cites experiments from more than one "
                    f"environment version, so no single scope supports it"
                ),
            ),
        )
    unsupported = covers(reach, claim.scope)
    if not unsupported:
        return ()
    return (
        Finding(
            check=CheckClass.SCOPE,
            outcome=Outcome.REJECT,
            message=(
                f"claim {claim.id!r} asserts beyond where its evidence reaches: "
                f"{'; '.join(unsupported)}"
            ),
        ),
    )
