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

Multiplicity
------------

An investigation checks several experiments at once, so the smallest p-value is
not itself a p-value. It is corrected by Sidak, ``1 - (1 - p_min) ** n``, which
is exact for independent tests -- and the tests *are* independent under the null,
because each experiment is an execution under its own seed.
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


def sidak(p_min: float, n_tests: int) -> float:
    """Return the Sidak-corrected p-value for the smallest of ``n_tests``.

    Exact when the tests are independent, which they are here: each experiment is
    a separate execution under its own seed.
    """
    if n_tests < 1:
        raise InferenceError(f"n_tests must be at least 1, got {n_tests}")
    if not 0.0 < p_min <= 1.0:
        raise InferenceError(f"p_min must lie in (0, 1], got {p_min}")
    if p_min == 1.0:
        # A discrete tail can be exactly 1 -- an observation in the single most
        # likely cell of a two-cell space reaches every cell. ``log1p(-1)`` is a
        # domain error rather than the -inf the limit would want, so the case is
        # answered directly.
        return 1.0
    return -math.expm1(n_tests * math.log1p(-p_min))


def posterior_predictive_check(
    experiments: Sequence[tuple[ExperimentId, Sequence[float], int]],
    *,
    alpha: float,
) -> PPCResult:
    """Return the check's verdict over a set of experiments.

    Each entry is ``(experiment id, posterior predictive distribution over
    cells, observed cell)``. Guarantees the reported ``p_value`` accounts for the
    number of experiments checked, and that ``inadequate`` is exactly
    ``p_value < alpha`` -- there is no second criterion and no threshold anywhere
    else.

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
    smallest = min(per_experiment[key] for key in sorted(per_experiment))
    combined = sidak(smallest, len(per_experiment))
    return PPCResult(
        alpha=alpha,
        p_value=combined,
        per_experiment=FrozenDict[ExperimentId, float](per_experiment),
        inadequate=combined < alpha,
    )
