"""Posterior predictive checking: does anything explain what was seen?

This is Stage A of SPEC F6, and the whole of baseline B1. It asks a question the
posterior cannot: the posterior is a distribution *over* the hypothesis set and
sums to one however badly every member fits, so a hypothesis space that contains
no adequate explanation still yields a confident-looking answer. The check
compares the observation against what the posterior predicts, and reports when
the two are irreconcilable.

The test
--------

For one experiment, the posterior predictive distribution over outcome cells is
``sum over h of posterior(h) * p(cell | h, template)``. The observation fell in
one cell. Its p-value is the total predictive mass of every cell *no more likely*
than the observed one::

    p = sum over cells c with q(c) <= q(observed) of q(c)

This is the exact discrete tail probability under a likelihood ordering, which is
the ordering that makes no assumption about the outcome space having a direction.
A ranked ordering matters here: a diagnostic can be surprising by being too low
as easily as by being too high, and half of the mechanisms in SPEC §4.2 are
detectable only in one of those directions.

Where the predictive comes from
-------------------------------

The distribution passed in must be built from
:meth:`~sciagent.inference.empirical.EmpiricalTable.resolved_probabilities` and
not from the likelihood's point estimate. This was measured rather than assumed.
A check is a question about tails, and the tails of a simulated table are exactly
where a point estimate is least trustworthy: a cell no replicate reached is not
six times less likely than one replicate would have made it, it is unresolved.
Fed the point estimate, the check's realised false-positive rate was **8.7%**
against a nominal 5%; floored at the rule-of-three bound it is **3.3%**, with no
loss of power against a detectable defect. The numbers are in
``docs/DECISIONS.md``.

Two further things then push the same way, and both are wanted. Discreteness
means the attainable p-values are a finite set, so the realised size is at most
the nominal one rather than equal to it. And the posterior was fitted to the same
data the check evaluates, which shrinks the discrepancy -- the usual property of
posterior predictive p-values. A9 bounds the false-positive rate at twice nominal
and reports power separately, which is the right shape for a test whose size is
bounded but not exact: a conservative detector understates how much inadequacy is
present, and understating is the direction that refuses to credit the framework
with a detection it did not earn.

Combining experiments
---------------------

An investigation checks several experiments at once, so the per-experiment tail
probabilities have to become one number. They are combined by the **harmonic
mean p-value**, ``n / sum(1 / p_i)``, scaled by ``1 + ln(n)``.

This replaces a Sidak correction on the smallest p-value, which was measured to
fail in the direction that matters. Under min-p, evidence and penalty grow
together and the penalty wins: B1's per-experiment probability against an
inadequate hypothesis space is 0.0133 whatever the budget, while the correction
takes the combined value from 0.0133 at one experiment to 0.1928 at sixteen.
**A system that ran more experiments detected less.** The harmonic mean grows
logarithmically instead -- 0.0133, 0.0225, 0.0317, 0.0410, 0.0502 at budgets
1, 2, 4, 8, 16 -- so agreement accumulates. Some growth is correct, since ``n``
tests genuinely offer ``n`` chances; what was wrong was growth fast enough to
cross alpha by the fourth experiment.

Why not the two obvious alternatives, both measured rather than argued:

* *Fisher's method* (``-2 sum ln p`` on ``2n`` degrees of freedom) pays two
  degrees of freedom per experiment whether or not it carried evidence. On this
  slice the evidence is concentrated: against a defect in the mark component the
  median per-experiment p-value is 0.67 while the median *smallest* is 0.0030.
  Fisher's realised size came out at 0.130 -- above A9's bound -- and its power
  against that defect collapsed from 1.000 to 0.070.
* *The Cauchy combination* returns the single p-value unchanged for any number
  of identical inputs, so it applies no multiplicity penalty at all, and it
  cannot represent the ``p = 1`` atom a discrete tail regularly produces.

The scale factor is ``1 + ln(n)`` rather than ``ln(n)``: it must be 1 at ``n =
1``, where there is nothing to correct, and the ``ln(n)`` form leaves the
realised size at 0.100 for three experiments -- exactly A9's ``2 alpha`` bound,
with no margin. Item 6 made the same call for the same reason when it floored
the predictive. The calibration is approximate; the realised size is what A9
measures, at most 0.045 over 200 correctly-specified scenarios at every number
of experiments from one to five. Numbers in ``docs/DECISIONS.md``.

Two assumptions do not hold exactly, and both fail in the safe direction:

* *Non-uniform.* The per-experiment values are conservative for the three
  reasons above -- discreteness, the rule-of-three floor, the posterior having
  been fitted to the data it is checked against -- so the combined value comes
  out too large. A conservative detector understates how much inadequacy is
  present, which is the direction that refuses to credit the framework with a
  detection it did not earn.
* *Non-independent.* Each experiment is an execution under its own seed, so the
  *observations* are independent. The predictive they are judged against is a
  function of the posterior, which was fitted to all of them, so the *tests* are
  not -- except where the belief is a single hypothesis, as in B1, where
  independence is exact. A9's false-positive arm measures the realistic case:
  the full closed set over correctly-specified scenarios.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from sciagent.core.errors import InferenceError
from sciagent.core.types import ExperimentId, FrozenDict
from sciagent.inference.interface import PPCResult

#: Cells whose predictive probabilities differ by less than this are treated as
#: equally likely when the tail is accumulated. Without it, two cells that are
#: equally probable by construction -- a symmetric outcome space, or two cells no
#: replicate reached -- would be separated by floating-point noise, and which of
#: them entered the tail would depend on rounding rather than on the data.
_TIE_TOLERANCE = 1e-12


def tail_probability(predictive: Sequence[float], cell: int) -> float:
    """Return the predictive mass of every cell no more likely than ``cell``.

    Guarantees a result in ``(0, 1]``: the observed cell is always counted, so a
    p-value of exactly zero is unreachable and no observation can be called
    infinitely surprising on finite evidence.
    """
    if not 0 <= cell < len(predictive):
        raise InferenceError(
            f"observed cell {cell} is outside the {len(predictive)} cells of the "
            f"predictive distribution"
        )
    total = math.fsum(predictive)
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise InferenceError(f"predictive distribution sums to {total!r}, not 1")
    threshold = predictive[cell]
    return min(
        1.0,
        math.fsum(
            value for value in predictive if value <= threshold * (1.0 + _TIE_TOLERANCE)
        ),
    )


def harmonic_mean_combined(p_values: Sequence[float]) -> float:
    """Return the p-values combined by the scaled harmonic mean.

    ``(1 + ln n) * n / sum(1 / p_i)``. Guarantees a result in ``(0, 1]``, and
    that combining a single p-value returns it unchanged -- the scale factor is
    exactly 1 at ``n = 1``, so the correction is an identity where there is
    nothing to correct.

    The harmonic mean is dominated by its smallest term, so evidence concentrated
    in one experiment is preserved rather than diluted; adding further small
    terms pulls it lower still, so evidence spread across several experiments
    accumulates. This module's docstring records what that replaced, what else
    was measured, and where the scale factor comes from.

    Reciprocals are summed with :func:`math.fsum` over sorted values, so the
    result does not depend on the order the caller supplied them in -- which is
    what lets two systems that ran the same experiments report the same verdict.
    """
    if not p_values:
        raise InferenceError("combining needs at least one p-value")
    for value in p_values:
        if not 0.0 < value <= 1.0:
            raise InferenceError(f"a p-value must lie in (0, 1], got {value!r}")
    count = len(p_values)
    harmonic = count / math.fsum(1.0 / value for value in sorted(p_values))
    return min(1.0, (1.0 + math.log(count)) * harmonic)


def posterior_predictive_check(
    experiments: Sequence[tuple[ExperimentId, Sequence[float], int]],
    *,
    alpha: float,
) -> PPCResult:
    """Return the check's verdict over a set of experiments.

    Each entry is ``(experiment id, posterior predictive distribution over
    cells, observed cell)``. Guarantees the reported ``p_value`` combines every
    experiment checked -- by :func:`harmonic_mean_combined`, so agreement
    accumulates -- and that ``inadequate`` is exactly ``p_value < alpha``: there
    is no second criterion and no threshold anywhere else.

    With no experiments the verdict is ``p_value = 1`` and not inadequate: no
    evidence of inadequacy is not evidence of adequacy, and this function reports
    the former.
    """
    if not 0.0 < alpha < 1.0:
        raise InferenceError(f"alpha must lie in (0, 1), got {alpha}")
    if not experiments:
        return PPCResult(
            alpha=alpha,
            p_value=1.0,
            per_experiment=FrozenDict[ExperimentId, float](),
            inadequate=False,
        )
    per_experiment = {
        experiment: tail_probability(predictive, cell)
        for experiment, predictive, cell in experiments
    }
    if len(per_experiment) != len(experiments):
        raise InferenceError("an experiment id was offered to the check twice")
    combined = harmonic_mean_combined(
        [per_experiment[key] for key in sorted(per_experiment)]
    )
    return PPCResult(
        alpha=alpha,
        p_value=combined,
        per_experiment=FrozenDict[ExperimentId, float](per_experiment),
        inadequate=combined < alpha,
    )
