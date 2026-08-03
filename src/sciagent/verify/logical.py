"""Is the claim coherent with itself and with the record (SPEC §6.5 A22)?

Three groups of rule.

**Field coherence.** SPEC §3.3 says an estimand is required when the modality is
causal. A causal claim also needs an :class:`~sciagent.core.types.Intervention`,
because §7.2's licensing table reads declarations -- which mediators are blocked,
which assumptions are listed -- that no experiment record can supply. A claim
citing nothing is refused: it is not a weak claim, it is an unevidenced one.

**Uniqueness.** ``exclusive`` asserts that no rival explains the evidence as
well. That is a statement about the posterior, so it is judged against the
posterior: the subject must hold more than half the mass, which is the reading
:class:`~sciagent.eval.scoring.ClosedWorldScore` already uses for "recovered the
diagnosis". With no posterior in the context the assertion is referred rather
than waved through.

**Prospective confirmation (A22).** SPEC F9: a late-proposed hypothesis takes no
evidential disadvantage in likelihood, "but cannot support a confirmatory claim
without a prospectively registered discriminating experiment". Both adjectives
are checked. *Prospectively registered*: the experiment's registry sequence is
after the sequence of the experiment the hypothesis was proposed at.
*Discriminating*: the hypothesis carries a prediction under that experiment's
template whose refutation some attainable value of the diagnostic would satisfy
-- that is, the experiment was one that could have sunk it. A hypothesis proposed
after an experiment may still cite that experiment; what it may not do is rest a
*confirmatory* claim on nothing else.
"""

from __future__ import annotations

from sciagent.core.conditions import satisfiable_over
from sciagent.core.types import Claim, HypothesisId
from sciagent.verify.relevance import EvidenceRecord
from sciagent.verify.verdict import CheckClass, ClaimContext, Finding, Outcome

__all__ = ["EXCLUSIVE_MASS", "check", "discriminating", "prospective_support"]

#: Posterior mass an ``exclusive`` claim's subject must hold. More than half, so
#: that no other hypothesis can hold as much.
EXCLUSIVE_MASS = 0.5


def _reject(claim: Claim, why: str) -> Finding:
    return Finding(
        check=CheckClass.LOGICAL,
        outcome=Outcome.REJECT,
        message=f"claim {claim.id!r} {why}",
    )


def discriminating(claim: Claim, record: EvidenceRecord, context: ClaimContext) -> bool:
    """Return whether the claim's subject could have been refuted by ``record``.

    The subject must carry a prediction made under this experiment's template,
    on a diagnostic the experiment measured, whose refutation some attainable
    value of that diagnostic satisfies. A16 already guarantees the last of these
    for any prediction in the graph; it is re-decided here rather than assumed,
    so this check stands on its own if the graph's guarantee ever weakens.
    """
    subject = HypothesisId(str(claim.subject))
    if subject not in context.graph.nodes:
        return False
    for prediction_id in context.graph.node(subject).predictions:
        prediction = context.graph.predictions[prediction_id]
        if prediction.under != record.template:
            continue
        if prediction.diagnostic.name not in record.metrics:
            continue
        spec = context.graph.metrics.spec(str(prediction.diagnostic.name))
        if satisfiable_over(prediction.refutation, spec.low, spec.high):
            return True
    return False


def prospective_support(
    claim: Claim, context: ClaimContext
) -> tuple[EvidenceRecord, ...]:
    """Return the cited experiments that are prospective *and* discriminating.

    Empty for a hypothesis that was present from the start, since nothing about
    such a claim is retrospective and the question does not arise.
    """
    subject = HypothesisId(str(claim.subject))
    node = context.graph.node(subject)
    if node.proposed_at is None:
        return ()
    proposal = context.evidence.records.get(node.proposed_at)
    since = proposal.sequence if proposal is not None else 0
    return tuple(
        record
        for record in (
            context.evidence.record(experiment)
            for experiment in sorted(set(claim.evidence))
        )
        if record.sequence > since and discriminating(claim, record, context)
    )


def check(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return every incoherence between the claim, its own fields and the record."""
    findings: list[Finding] = []

    if not claim.evidence:
        findings.append(_reject(claim, "cites no experiment at all"))

    if claim.modality == "causal":
        if claim.estimand is None:
            findings.append(
                _reject(
                    claim,
                    "is causal but names no estimand; SPEC §3.3 requires one, and "
                    "§7.2 licenses causal language by estimand and nothing else",
                )
            )
        if claim.intervention is None:
            findings.append(
                _reject(
                    claim,
                    "is causal but declares no intervention, so the assumptions "
                    "SPEC §7.2 requires to be listed are not listed",
                )
            )
    elif claim.estimand is not None:
        findings.append(
            Finding(
                check=CheckClass.LOGICAL,
                outcome=Outcome.NOTE,
                message=(
                    f"claim {claim.id!r} names an estimand but calls itself "
                    f"{claim.modality!r}; the estimand carries no licence here"
                ),
            )
        )

    if claim.uniqueness == "exclusive":
        findings.extend(_check_exclusive(claim, context))

    if claim.subject_kind == "hypothesis" and str(claim.subject) in {
        str(node_id) for node_id in context.graph.nodes
    }:
        findings.extend(_check_prospective(claim, context))

    return tuple(findings)


def _check_exclusive(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return a verdict on an ``exclusive`` uniqueness assertion."""
    if claim.subject_kind != "hypothesis":
        return (
            Finding(
                check=CheckClass.LOGICAL,
                outcome=Outcome.REFER,
                message=(
                    f"claim {claim.id!r} asserts exclusivity about a component; "
                    f"exclusivity is a statement about rival hypotheses"
                ),
            ),
        )
    subject = HypothesisId(str(claim.subject))
    if not context.posterior:
        return (
            Finding(
                check=CheckClass.LOGICAL,
                outcome=Outcome.REFER,
                message=(
                    f"claim {claim.id!r} asserts exclusivity, and no posterior was "
                    f"supplied to judge it against"
                ),
            ),
        )
    mass = float(context.posterior.get(subject, 0.0))
    if mass <= EXCLUSIVE_MASS:
        return (
            _reject(
                claim,
                f"asserts exclusivity while its subject holds {mass:.3f} of the "
                f"posterior; at or below {EXCLUSIVE_MASS} some rival holds as much",
            ),
        )
    return ()


def _check_prospective(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return SPEC F9's verdict on a confirmatory claim about a late hypothesis."""
    if claim.partition != "confirmatory":
        return ()
    node = context.graph.node(HypothesisId(str(claim.subject)))
    if node.proposed_at is None:
        return ()
    if prospective_support(claim, context):
        return ()
    return (
        _reject(
            claim,
            f"is confirmatory about {claim.subject!r}, which was proposed at "
            f"{node.proposed_at!r}, and cites no experiment registered after that "
            f"proposal that could have refuted it. SPEC F9 costs lateness no "
            f"likelihood and does cost it this",
        ),
    )
