"""Does the claim contradict the graph, or a claim already accepted?

SPEC §4.6 requirement 5 asks every system to "maintain coherent evidence: zero
graph contradictions, no zombie hypotheses", and SPEC §12 criterion 8 makes zero
of both an exit condition for the slice. Neither is checkable without somewhere
that says what a contradiction *is*, which is here.

Three ways a claim can be incoherent with what is already settled:

**Zombie.** Supporting a hypothesis the graph has rejected. A rejection is a
statement that the evidence went against it; a supporting claim about it is the
same investigation asserting both. Refuting a rejected hypothesis is fine, and so
is any claim about a *suspended* one -- suspension is explicitly "pending further
evidence" and re-arguing it is what further evidence is for.

**Cross-contradiction.** Supporting a hypothesis while an accepted claim supports
another that ``CONTRADICTS`` it. This is the relation earning its place: without
typed relations the two claims are merely about different subjects.

**Reversal.** An accepted claim about the same subject and the same estimand
whose measured effect went the other way. Two directions from one estimand is not
a refinement, it is a disagreement, and the record should not carry both silently.
"""

from __future__ import annotations

from sciagent.core.types import Claim, Direction, HypothesisId
from sciagent.hypothesis.graph import HypothesisGraph, Relation
from sciagent.verify.verdict import CheckClass, ClaimContext, Finding, Outcome

__all__ = ["SUPPORTING_STRENGTHS", "check", "zombie"]

#: The strengths that assert *for* a subject. ``refutes`` asserts against it, and
#: every rule here is about asserting for something already settled otherwise.
SUPPORTING_STRENGTHS: frozenset[str] = frozenset(
    {"suggests", "supports", "establishes"}
)


def _reject(claim: Claim, why: str) -> Finding:
    return Finding(
        check=CheckClass.CONTRADICTION,
        outcome=Outcome.REJECT,
        message=f"claim {claim.id!r} {why}",
    )


def zombie(claim: Claim, graph: HypothesisGraph) -> bool:
    """Return whether ``claim`` supports a hypothesis ``graph`` has rejected.

    Guarantees the answer is the exact condition :func:`check` refuses a claim
    on, so a caller counting zombies and a caller reading findings cannot come
    to different totals.

    Exported for that reason and no other. SPEC §12 criterion 8 asks for two
    quantities -- *"zero graph contradictions and zero zombie hypotheses"* --
    and a count of :data:`~sciagent.verify.verdict.CheckClass.CONTRADICTION`
    findings pools them, so a nonzero figure could be a reversal and the second
    half of the criterion stays underivable. The alternative was for the caller
    to recognise a zombie by its :attr:`~sciagent.verify.verdict.Finding.message`,
    which :mod:`sciagent.verify.verdict` says is for a human reading the record
    and that nothing branches on, or to restate the rule in a second place --
    where it would drift from this one silently.

    Refuting a rejected hypothesis is not a zombie and neither is any claim
    about a *suspended* one; see this module's docstring for both.
    """
    if claim.subject_kind != "hypothesis":
        return False
    if claim.strength not in SUPPORTING_STRENGTHS:
        return False
    subject = HypothesisId(str(claim.subject))
    return subject in graph.nodes and graph.node(subject).status == "rejected"


def check(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return every contradiction between this claim, the graph and the record."""
    findings: list[Finding] = []
    supporting = claim.strength in SUPPORTING_STRENGTHS

    if claim.subject_kind == "hypothesis":
        subject = HypothesisId(str(claim.subject))
        if subject in context.graph.nodes:
            node = context.graph.node(subject)
            if zombie(claim, context.graph):
                findings.append(
                    _reject(
                        claim,
                        f"supports {subject!r}, which the graph rejected for "
                        f"{node.rejection_reason!r}; a supporting claim about a "
                        f"rejected hypothesis is a zombie (SPEC §4.6)",
                    )
                )
            findings.extend(_cross_contradictions(claim, context, supporting))

    findings.extend(_reversals(claim, context))
    return tuple(findings)


def _cross_contradictions(
    claim: Claim, context: ClaimContext, supporting: bool
) -> tuple[Finding, ...]:
    """Return a refusal if an accepted claim supports something incompatible."""
    if not supporting:
        return ()
    subject = HypothesisId(str(claim.subject))
    found: list[Finding] = []
    for accepted in context.accepted:
        if accepted.subject_kind != "hypothesis":
            continue
        if accepted.strength not in SUPPORTING_STRENGTHS:
            continue
        other = HypothesisId(str(accepted.subject))
        if other == subject or other not in context.graph.nodes:
            continue
        if context.graph.relation(subject, other) is Relation.CONTRADICTS:
            found.append(
                _reject(
                    claim,
                    f"supports {subject!r} while claim {accepted.id!r} supports "
                    f"{other!r}, and the graph holds those two to contradict",
                )
            )
    return tuple(found)


def _reversals(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return a refusal if an accepted claim measured the same effect the other way."""
    if claim.effect is None or claim.estimand is None:
        return ()
    if claim.effect.direction is Direction.NO_CHANGE:
        return ()
    found: list[Finding] = []
    for accepted in context.accepted:
        if accepted.id == claim.id:
            continue
        if accepted.subject != claim.subject or accepted.estimand != claim.estimand:
            continue
        if accepted.effect is None:
            continue
        if accepted.effect.direction is Direction.NO_CHANGE:
            continue
        if accepted.effect.direction is not claim.effect.direction:
            found.append(
                _reject(
                    claim,
                    f"reports {claim.effect.direction.value!r} for the same estimand "
                    f"on which accepted claim {accepted.id!r} reports "
                    f"{accepted.effect.direction.value!r}",
                )
            )
    return tuple(found)
