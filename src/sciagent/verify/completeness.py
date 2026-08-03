"""Registry-relative evidence completeness (SPEC §7.1, §6.5 A20).

One rule: every registered experiment the relevance query surfaces must be cited.
An uncited relevant experiment is a refusal, not a warning -- a claim that rests
on the subset of the record that suits it is not a weaker claim, it is a
different one.

The third category is separate. An experiment relevant under a superseded metric
or grammar version is reported and does not refuse the claim: SPEC §7.1 says such
rows are "surfaced as relevant-but-version-mismatched, a third category, reported
rather than silently resolved", and refusing on them would be resolving them in
the strictest available direction.
"""

from __future__ import annotations

from sciagent.core.types import Claim
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.verify.relevance import EvidenceIndex, survey
from sciagent.verify.verdict import CheckClass, Finding, Outcome

__all__ = ["check"]


def check(
    claim: Claim, index: EvidenceIndex, graph: HypothesisGraph
) -> tuple[Finding, ...]:
    """Return a refusal for each relevant experiment the claim failed to cite.

    Guarantees the answer is exactly what
    :func:`sciagent.verify.relevance.survey` found, so the criterion the verifier
    enforces and the query acceptance test A20 measures are the same code.
    """
    found = survey(claim, index, graph)
    findings: list[Finding] = []
    if found.uncited_relevant:
        missed = ", ".join(
            f"{experiment} "
            f"({', '.join(sorted(c.value for c in found.reasons[experiment]))})"
            for experiment in found.uncited_relevant
        )
        findings.append(
            Finding(
                check=CheckClass.COMPLETENESS,
                outcome=Outcome.REJECT,
                message=(
                    f"claim {claim.id!r} does not cite {len(found.uncited_relevant)} "
                    f"registered experiment(s) the relevance query surfaces: {missed}"
                ),
            )
        )
    if found.version_mismatched:
        findings.append(
            Finding(
                check=CheckClass.COMPLETENESS,
                outcome=Outcome.NOTE,
                message=(
                    f"claim {claim.id!r} has "
                    f"{len(found.version_mismatched)} relevant-but-version-mismatched "
                    f"experiment(s): "
                    f"{', '.join(str(e) for e in found.version_mismatched)}. Reported "
                    f"under SPEC §7.1, not resolved"
                ),
            )
        )
    return tuple(findings)
