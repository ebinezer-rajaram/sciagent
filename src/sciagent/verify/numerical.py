"""Did this number come from where it claims to (SPEC §6.5 A19)?

The framework writes numbers and agents write structure (SPEC F7). For a
:class:`~sciagent.core.types.Claim` this module is where that stops being a
convention: an :class:`~sciagent.core.types.EffectEstimate` is produced by
:func:`recompute` and by nothing else, and :func:`check` re-derives whatever a
claim carries from the registered rows the claim cites and refuses the claim
unless the two agree.

Bit-exactly, not approximately
------------------------------

The comparison is equality on every field. Both sides run the same derivation
over the same rows, so any difference at all means one of them did not come from
there -- the same argument
:func:`sciagent.eval.campaign.run_scenario` already makes about a diagnosis. A
tolerance would be a licence to be wrong by less than the tolerance, and A19
corrupts figures across fourteen orders of magnitude precisely so that a
tolerance cannot pass it.

What an effect is
-----------------

A difference of means between an interventional arm and an observational one, on
one diagnostic, with a Welch standard error and a normal interval. That is the
only estimator the framework computes, deliberately: an estimator chosen per
claim is a degree of freedom, and a claim that wanted a different one would be
choosing its own number by choosing its own method.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from sciagent.core.types import (
    Claim,
    Direction,
    EffectEstimate,
    MetricName,
)
from sciagent.verify.relevance import EvidenceIndex, EvidenceRecord, claim_targets
from sciagent.verify.verdict import CheckClass, Finding, Outcome

__all__ = ["CONFIDENCE_LEVEL", "Z_TWO_SIDED", "arms", "check", "recompute"]
"""``Z_TWO_SIDED`` is exported because it is the project's one normal quantile and
:mod:`sciagent.eval.report` derives every reported interval from it. It was
omitted while this module was the only consumer."""

#: Nominal coverage of every interval this module derives.
CONFIDENCE_LEVEL: Final = 0.95

#: The double nearest the standard normal's 0.975 quantile. Written out rather
#: than obtained from ``scipy.stats.norm.ppf`` so the derivation is a pure
#: function of this source file: a library that changed its quantile in the last
#: place would change every recomputed interval, and every claim in the registry
#: would then fail A19 for a reason that has nothing to do with the claims.
Z_TWO_SIDED: Final = 1.959963984540054


def arms(
    claim: Claim, index: EvidenceIndex, metric: MetricName
) -> tuple[tuple[EvidenceRecord, ...], tuple[EvidenceRecord, ...]]:
    """Return the (treated, control) records a claim's effect is computed from.

    Treated: cited experiments that manipulated something the claim is about, or
    -- for a claim with no causal target set -- that manipulated anything at all.
    Control: cited experiments that manipulated nothing. Experiments that did not
    measure ``metric`` are in neither, since they carry no reading to difference.

    Both tuples are in experiment-id order, which is what makes the sums below
    reproducible.
    """
    targets = claim_targets(claim)
    treated: list[EvidenceRecord] = []
    control: list[EvidenceRecord] = []
    for experiment in sorted(set(claim.evidence)):
        record = index.record(experiment)
        if metric not in record.metrics:
            continue
        if not record.manipulated:
            control.append(record)
        elif not targets or record.manipulated & targets:
            treated.append(record)
    return tuple(treated), tuple(control)


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _variance(values: Sequence[float]) -> float:
    """Return the sample variance, or ``0.0`` for fewer than two observations."""
    if len(values) < 2:
        return 0.0
    centre = _mean(values)
    return math.fsum((value - centre) ** 2 for value in values) / (len(values) - 1)


def recompute(
    claim: Claim, index: EvidenceIndex, *, metric: MetricName | None = None
) -> EffectEstimate | None:
    """Return the effect the cited registry rows imply, or ``None``.

    ``None`` means the citation cannot produce an effect at all -- one of the two
    arms is empty -- which is a fact about the claim and is turned into a refusal
    by :func:`check` rather than into a zero here.

    Guarantees the result is a pure function of the cited rows: sums run over
    experiment ids in sorted order with :func:`math.fsum`, so the same citation
    yields the same double on every run and in every process.
    """
    wanted = (
        metric
        if metric is not None
        else (claim.effect.metric if claim.effect is not None else None)
    )
    if wanted is None:
        return None
    treated, control = arms(claim, index, wanted)
    if not treated or not control:
        return None

    treated_values = [record.value(wanted) for record in treated]
    control_values = [record.value(wanted) for record in control]
    point = _mean(treated_values) - _mean(control_values)
    standard_error = math.sqrt(
        _variance(treated_values) / len(treated_values)
        + _variance(control_values) / len(control_values)
    )
    half_width = Z_TWO_SIDED * standard_error
    return EffectEstimate(
        metric=wanted,
        point=point,
        standard_error=standard_error,
        low=point - half_width,
        high=point + half_width,
        level=CONFIDENCE_LEVEL,
        n_treated=len(treated_values),
        n_control=len(control_values),
        direction=(
            Direction.INCREASE
            if point > 0.0
            else Direction.DECREASE
            if point < 0.0
            else Direction.NO_CHANGE
        ),
    )


def _differences(claimed: EffectEstimate, derived: EffectEstimate) -> tuple[str, ...]:
    """Return the names of every field on which the two disagree."""
    return tuple(
        name
        for name in (
            "metric",
            "point",
            "standard_error",
            "low",
            "high",
            "level",
            "n_treated",
            "n_control",
            "direction",
        )
        if getattr(claimed, name) != getattr(derived, name)
    )


def check(claim: Claim, index: EvidenceIndex) -> tuple[Finding, ...]:
    """Return a refusal for every figure the cited evidence does not reproduce.

    Guarantees a claim carrying no effect draws no finding -- there is nothing
    numerical to check -- and that a claim whose effect the registry reproduces
    exactly draws none either. This is acceptance test A19.
    """
    if claim.effect is None:
        return ()
    derived = recompute(claim, index)
    if derived is None:
        return (
            Finding(
                check=CheckClass.NUMERICAL,
                outcome=Outcome.REJECT,
                message=(
                    f"claim {claim.id!r} reports an effect on "
                    f"{claim.effect.metric!r}, but its evidence cannot produce one: "
                    f"an effect is a difference between an interventional and an "
                    f"observational arm and the citation supplies fewer than both"
                ),
            ),
        )
    differing = _differences(claim.effect, derived)
    if not differing:
        return ()
    return (
        Finding(
            check=CheckClass.NUMERICAL,
            outcome=Outcome.REJECT,
            message=(
                f"claim {claim.id!r} reports {differing!r} that the registered rows "
                f"it cites do not reproduce; the rows give {derived!r}"
            ),
        ),
    )
