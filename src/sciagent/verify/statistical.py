"""Does the evidence reach the strength the claim asserts?

SPEC §3.3 gives a claim four strengths and does not say what earns each. This
module fixes a ladder, and the reading is recorded in ``docs/DECISIONS.md``
rather than left implicit, because every figure the verifier reports about claim
quality depends on it.

Two channels, because claims come in two shapes
-----------------------------------------------

A claim carrying an :class:`~sciagent.core.types.EffectEstimate` is judged on the
interval. A claim carrying none, about a hypothesis that made predictions, is
judged on those predictions: item 5 already decides exactly whether a diagnostic
value satisfies a condition or its refutation, so "the experiment came out as the
hypothesis said" is a mechanical question and is answered mechanically.

A claim with neither is **referred**, not refused. A qualitative assertion about
a component with no measured effect and no prediction to test is a judgement, and
SPEC F8 says prose faithfulness is not mechanical. Referring it is the honest
answer and is exactly what acceptance test A23 counts against coverage.

Zero standard error is not certainty
------------------------------------

One observation per arm gives a sample variance of zero, hence an interval of
zero width, which "excludes zero" for any non-zero point estimate. Granting
``supports`` on that would let a single pair of runs establish anything, so an
effect with no sampling variability reaches ``suggests`` and no further.
"""

from __future__ import annotations

from dataclasses import dataclass

from sciagent.core.conditions import evaluate
from sciagent.core.errors import MalformedClaimError
from sciagent.core.types import Claim, Direction, EffectEstimate, HypothesisId
from sciagent.verify.verdict import CheckClass, ClaimContext, Finding, Outcome

__all__ = ["PredictionEvidence", "check", "excludes_zero", "prediction_evidence"]

#: Experiments an arm needs before a claim may say ``establishes``. Two, so that
#: the strongest strength always rests on a replication rather than on one run
#: that happened to fall the right way.
REPLICATION = 2


def excludes_zero(effect: EffectEstimate) -> bool:
    """Return whether the interval lies wholly above or wholly below zero."""
    return effect.low > 0.0 or effect.high < 0.0


def _informative(effect: EffectEstimate) -> bool:
    """Return whether the effect carries any sampling variability at all."""
    return effect.standard_error > 0.0


@dataclass(frozen=True, slots=True)
class PredictionEvidence:
    """How the cited experiments fell against a hypothesis's own predictions."""

    evaluated: int
    confirmed: int
    refuted: int

    authored_confirmed: int = 0
    """How many of :attr:`confirmed` fell against a threshold the system set.

    A subset of :attr:`confirmed`, never a separate tally, so
    ``confirmed - authored_confirmed`` is the count that rests on a condition the
    framework derived from the structure's own table row.
    """

    authored_refuted: int = 0
    """The same, for :attr:`refuted`.

    **Split, against a first instinct that was wrong.** The reasoning for not
    splitting it was that a system whose own experiment refuted its own condition
    has argued against itself — true of a claim *supporting* that hypothesis, and
    false of a claim `"refutes"` it. There, clearing a self-authored refutation
    bar is the *favourable* outcome: a system can propose a rival or decoy
    structure with a wide authored refutation and have `"refutes"` accepted on a
    threshold it set. `claims_from_run` emits a claim per hypothesis carrying
    mass, so that path is exercised on every scored cell.
    """

    def __post_init__(self) -> None:
        """Refuse a tally that cannot describe any run.

        Four relations the docstrings above state as facts, made false-able:
        every count is non-negative, neither outcome exceeds what was evaluated,
        and each authored count is a subset of the tally it qualifies.

        The subset relations are the ones worth asserting rather than trusting.
        :func:`_from_predictions` decides the discount by ``confirmed ==
        authored_confirmed``; if ``authored_confirmed`` could ever exceed
        ``confirmed``, that equality would silently stop holding and the whole
        discount would disappear with nothing raising. CLAUDE.md's second
        invariant asks for runtime assertions rather than comments, and a frozen
        dataclass of five bare ints is exactly where a comment would otherwise
        have been the whole of it — the same discipline
        :class:`~sciagent.eval.campaign.AdjudicationCounts` applies one level up.
        """
        counts = (
            self.evaluated,
            self.confirmed,
            self.refuted,
            self.authored_confirmed,
            self.authored_refuted,
        )
        if min(counts) < 0:
            raise MalformedClaimError(
                f"prediction evidence reports a negative count: {self!r}"
            )
        if self.confirmed > self.evaluated or self.refuted > self.evaluated:
            raise MalformedClaimError(
                f"prediction evidence reports more outcomes than it evaluated: {self!r}"
            )
        if (
            self.authored_confirmed > self.confirmed
            or self.authored_refuted > self.refuted
        ):
            raise MalformedClaimError(
                f"prediction evidence reports more agent-authored outcomes than "
                f"outcomes: {self!r}"
            )


def prediction_evidence(claim: Claim, context: ClaimContext) -> PredictionEvidence:
    """Return the tally of cited experiments against the subject's predictions.

    An experiment counts only where it instantiates the template the prediction
    was made under and measured the diagnostic the prediction names. Everything
    else is silent rather than neutral: a prediction about one design says
    nothing about another, and counting it as unconfirmed would penalise a
    hypothesis for an experiment that could not have borne on it.
    """
    if claim.subject_kind != "hypothesis":
        return PredictionEvidence(0, 0, 0)
    subject = HypothesisId(str(claim.subject))
    if subject not in context.graph.nodes:
        return PredictionEvidence(0, 0, 0)
    node = context.graph.node(subject)
    evaluated = confirmed = refuted = 0
    authored_confirmed = authored_refuted = 0
    for prediction_id in node.predictions:
        prediction = context.graph.predictions[prediction_id]
        for experiment in sorted(set(claim.evidence)):
            record = context.evidence.record(experiment)
            if record.template != prediction.under:
                continue
            if prediction.diagnostic.name not in record.metrics:
                continue
            value = record.value(prediction.diagnostic.name)
            evaluated += 1
            met = int(evaluate(prediction.condition, value))
            against = int(evaluate(prediction.refutation, value))
            confirmed += met
            refuted += against
            authored_confirmed += met * int(prediction.authored)
            authored_refuted += against * int(prediction.authored)
    return PredictionEvidence(
        evaluated, confirmed, refuted, authored_confirmed, authored_refuted
    )


def _reject(claim: Claim, why: str) -> Finding:
    return Finding(
        check=CheckClass.STATISTICAL,
        outcome=Outcome.REJECT,
        message=f"claim {claim.id!r} says {claim.strength!r}, but {why}",
    )


def _refer(claim: Claim, why: str) -> Finding:
    return Finding(
        check=CheckClass.STATISTICAL,
        outcome=Outcome.REFER,
        message=f"claim {claim.id!r} cannot be graded mechanically: {why}",
    )


def _from_effect(claim: Claim, effect: EffectEstimate) -> tuple[Finding, ...]:
    """Grade a claim that carries a measured effect."""
    if claim.strength == "refutes":
        expected = (
            claim.intervention.expected_direction
            if claim.intervention is not None
            else None
        )
        if expected is None:
            return (
                _refer(
                    claim,
                    "it reports an effect but preregisters no expected direction, "
                    "so there is nothing for the measurement to have contradicted",
                ),
            )
        if effect.direction is expected:
            return (
                _reject(
                    claim,
                    f"the measured effect went {effect.direction.value!r}, which is "
                    f"the direction preregistered",
                ),
            )
        if not (_informative(effect) and excludes_zero(effect)):
            return (
                _reject(
                    claim,
                    "the interval it reports includes zero, so the measurement is "
                    "consistent with the preregistered direction",
                ),
            )
        return ()

    if effect.direction is Direction.NO_CHANGE:
        return (_reject(claim, "the measured effect is exactly zero"),)
    if claim.strength == "suggests":
        return ()
    if not _informative(effect):
        return (
            _reject(
                claim,
                "its effect has a standard error of zero, so the interval carries "
                "no information about sampling variability; one arm was measured "
                "once",
            ),
        )
    if not excludes_zero(effect):
        return (_reject(claim, "the interval it reports includes zero"),)
    if claim.strength == "establishes" and (
        effect.n_treated < REPLICATION or effect.n_control < REPLICATION
    ):
        return (
            _reject(
                claim,
                f"the strongest strength rests on {effect.n_treated} treated and "
                f"{effect.n_control} control experiment(s); {REPLICATION} of each "
                f"is the minimum a replication can be",
            ),
        )
    return ()


def _from_predictions(claim: Claim, tally: PredictionEvidence) -> tuple[Finding, ...]:
    """Grade a claim with no effect, against the subject's own predictions.

    Applies at every strength, the weakest included. What the last clause
    discounts is the *provenance of the threshold*, and that is the same question
    whether a claim says "suggests" or "establishes" -- a weaker word does not
    make a self-set bar any more checkable.
    """
    if tally.evaluated == 0:
        return (
            _refer(
                claim,
                "it carries no measured effect, and none of the experiments it "
                "cites bears on a prediction its subject made",
            ),
        )
    if claim.strength == "refutes":
        if tally.refuted == 0:
            return (
                _reject(
                    claim,
                    f"none of the {tally.evaluated} bearing experiment(s) satisfied "
                    f"the subject's refutation condition",
                ),
            )
        if tally.refuted == tally.authored_refuted:
            # The symmetric case, and the one the first cut of this missed.
            # Refuting a hypothesis is favourable to a system that wants a rival
            # structure out of the way, so a self-authored refutation bar is
            # self-serving here exactly as a self-authored condition is for a
            # supporting claim.
            return (
                _refer(
                    claim,
                    f"all {tally.refuted} refuting experiment(s) fell against a "
                    f"refutation condition the proposing system authored rather "
                    f"than one derived from the structure's table row, so the "
                    f"threshold they cleared is not the framework's",
                ),
            )
        return ()
    if tally.confirmed == 0:
        return (
            _reject(
                claim,
                f"none of the {tally.evaluated} bearing experiment(s) satisfied the "
                f"subject's prediction",
            ),
        )
    if claim.strength in ("supports", "establishes") and tally.refuted:
        return (
            _reject(
                claim,
                f"{tally.refuted} of the cited experiments refuted the subject; an "
                f"experiment that refuted a hypothesis is not evidence supporting it",
            ),
        )
    if claim.strength == "establishes" and tally.confirmed < REPLICATION:
        return (
            _reject(
                claim,
                f"only {tally.confirmed} experiment(s) confirmed the subject's "
                f"prediction; the strongest strength rests on {REPLICATION}",
            ),
        )
    if tally.confirmed == tally.authored_confirmed:
        # Today this is equivalent to ``authored_confirmed > 0``: ``propose``
        # stamps a node's predictions in one call and nothing adds more to a node
        # afterwards, so a node's predictions are all authored or none are. It is
        # written as the equality anyway, because that is the condition actually
        # meant -- "no confirmation rests on a threshold the framework derived" --
        # and it stays correct if a node ever comes to hold both kinds.
        #
        # Last, so that anything decidable is still decided: a claim its own
        # cited experiments refuted is rejected above whether or not it wrote its
        # own condition, and this reaches only a claim that would otherwise have
        # been accepted.
        #
        # Referred rather than rejected, because nothing here is false. The
        # experiments did land inside the stated condition; what cannot be
        # settled mechanically is whether that condition was drawn where the
        # structure implies or where the claim needed it. A framework-derived
        # prediction comes off the structure's own table row and raises no such
        # question, which is why only this case is referred -- and why every
        # claim in the recorded campaign is unaffected.
        return (
            _refer(
                claim,
                f"all {tally.confirmed} confirming experiment(s) fell against a "
                f"condition the proposing system authored rather than one derived "
                f"from the structure's table row, so the threshold they cleared is "
                f"not the framework's",
            ),
        )
    return ()


def check(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return a refusal or a referral unless the evidence reaches the strength."""
    if claim.effect is not None:
        return _from_effect(claim, claim.effect)
    return _from_predictions(claim, prediction_evidence(claim, context))
