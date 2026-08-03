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
            confirmed += int(evaluate(prediction.condition, value))
            refuted += int(evaluate(prediction.refutation, value))
    return PredictionEvidence(evaluated, confirmed, refuted)


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
    """Grade a claim with no effect, against the subject's own predictions."""
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
    return ()


def check(claim: Claim, context: ClaimContext) -> tuple[Finding, ...]:
    """Return a refusal or a referral unless the evidence reaches the strength."""
    if claim.effect is not None:
        return _from_effect(claim, claim.effect)
    return _from_predictions(claim, prediction_evidence(claim, context))
