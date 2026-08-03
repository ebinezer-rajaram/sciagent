"""The claim verifier: SPEC F8's six mechanical check classes, plus causal.

A claim is a typed object and prose is a rendering of it (SPEC F8). Everything
typed is decided here; nothing about the prose is, because whether prose is
faithful to the structure beside it is research question R6 and not a mechanical
question. A claim the checks cannot decide is **referred** rather than guessed
at, and the fraction never referred is what acceptance test A23 measures.

:func:`verify` lives here rather than in :mod:`sciagent.verify.verdict` so that
every check module can import the shared vocabulary without importing the thing
that calls them.

Order of checks
---------------

Fixed, and every check runs: a verdict names every ground on which a claim
failed, not the first. Numerical goes first only because its recomputation is
carried on the verdict for a reader to compare against, and causal last because
its result is the licence the verdict reports.
"""

from __future__ import annotations

from sciagent.core.types import Claim
from sciagent.verify import (
    causal,
    completeness,
    contradiction,
    logical,
    numerical,
    scope,
    statistical,
)
from sciagent.verify.relevance import (
    EvidenceIndex,
    EvidenceRecord,
    RelevanceClause,
    RelevanceSurvey,
    survey,
)
from sciagent.verify.verdict import (
    CheckClass,
    ClaimContext,
    Finding,
    Outcome,
    Verdict,
    worst,
)

__all__ = [
    "CheckClass",
    "ClaimContext",
    "EvidenceIndex",
    "EvidenceRecord",
    "Finding",
    "Outcome",
    "RelevanceClause",
    "RelevanceSurvey",
    "Verdict",
    "survey",
    "verify",
    "worst",
]


def verify(claim: Claim, context: ClaimContext) -> Verdict:
    """Adjudicate one claim against the record, and return why.

    Guarantees the result is a pure function of the claim and the context, that
    every check runs whatever the others found, and that the verdict's outcome is
    the most decisive outcome any check reached. A claim no check refuses, refers
    or downgrades is accepted.
    """
    findings: list[Finding] = []
    findings.extend(numerical.check(claim, context.evidence))
    findings.extend(completeness.check(claim, context.evidence, context.graph))
    findings.extend(scope.check(claim, context.evidence))
    findings.extend(statistical.check(claim, context))
    findings.extend(logical.check(claim, context))
    findings.extend(contradiction.check(claim, context))

    cited = tuple(context.evidence.record(experiment) for experiment in claim.evidence)
    licence = causal.license(claim, context.program, cited)
    findings.extend(licence.findings)

    return Verdict(
        claim=claim.id,
        outcome=worst(finding.outcome for finding in findings),
        findings=tuple(findings),
        licensed=licence.licensed,
        recomputed=numerical.recompute(claim, context.evidence),
    )
