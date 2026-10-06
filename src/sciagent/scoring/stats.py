"""Paired statistics for contrasts (SPEC §6.2: "paired bootstrap 95% CI").

A contrast pairs two systems on the same units (truths, or truth x seed), so
the statistic is the mean of the per-unit differences ``x_i - y_i``.

- :func:`paired_mean_se` is that mean and its standard error,
  ``sd(d, ddof=1) / sqrt(n)``.
- :func:`paired_bootstrap_ci` is the percentile bootstrap of the mean
  difference: ``resamples`` draws of n unit indices with replacement from the
  explicitly passed generator, one ``rng.integers`` call for all of them, so
  the interval is a deterministic function of the inputs and the generator's
  state. Quantiles are numpy's default linear interpolation over the sorted
  resampled means. Every float fold is exactly rounded
  (:mod:`sciagent.core.reductions`), so the mean does not depend on summation
  order.

**Rank statistics** (control 2, P4-PROPOSAL Δ10). A held-out score can be
``-inf`` (a model that gives a held-out event zero intensity), so a paired
difference can be ``±inf``; a mean of such differences says nothing, and
:func:`paired_bootstrap_ci` refuses them. Three functions read the differences
by order instead, with ``-inf`` the lowest value and ``+inf`` the highest
(``nan`` is refused: it has no rank):

- :func:`signed_rank_greater`: the one-sided Wilcoxon signed-rank p-value
  (alternative: the differences are shifted above 0). Zero differences are
  dropped (Wilcoxon's rule); ties in ``|d|`` take their average rank. The
  p-value is the *exact* conditional null distribution (every sign flip of the
  observed ranks equally likely), counted with integers over doubled ranks, so
  it is exact with ties too and has no platform-dependent arithmetic.
  ``±inf`` simply holds the largest ``|d|`` rank, with its sign.
- :func:`order_median`: the median by order statistics. For an even count the
  two middle values are averaged, except that a middle pair ``(-inf, +inf)``
  has no midpoint and is read as 0.
- :func:`bootstrap_median_ci`: the percentile bootstrap of :func:`order_median`
  over units resampled with replacement (one ``rng.integers`` call). Its ends
  are order statistics of the resampled medians (numpy's ``lower`` and
  ``higher`` quantile methods: no interpolation, so an infinite resampled
  median never meets arithmetic and the interval is never ``nan``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import numpy as np

from sciagent.core import reductions
from sciagent.scoring.errors import ScoringError

#: Bootstrap resamples used unless a caller asks otherwise.
DEFAULT_RESAMPLES: Final = 10_000
#: Two-sided confidence level of every preregistered contrast (SPEC §6.2).
DEFAULT_LEVEL: Final = 0.95


@dataclass(frozen=True)
class BootstrapCI:
    """Mean paired difference ``x - y`` and its percentile bootstrap interval."""

    mean: float
    low: float
    high: float
    level: float
    n: int
    resamples: int

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0.0 or self.high < 0.0


def _differences(x: Sequence[float], y: Sequence[float]) -> np.ndarray:
    if len(x) != len(y):
        raise ScoringError(f"paired samples differ in length: {len(x)} vs {len(y)}")
    if not x:
        raise ScoringError("paired samples are empty")
    d = np.asarray(x, dtype=np.float64) - np.asarray(y, dtype=np.float64)
    if not bool(np.all(np.isfinite(d))):
        raise ScoringError("paired samples must be finite")
    return d


def paired_mean_se(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """Mean of ``x - y`` and its standard error (needs at least two pairs)."""
    d = _differences(x, y)
    if d.size < 2:
        raise ScoringError("a paired standard error needs at least two pairs")
    return reductions.mean(d), reductions.deviation(d) / math.sqrt(d.size)


def paired_bootstrap_ci(
    x: Sequence[float],
    y: Sequence[float],
    rng: np.random.Generator,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    level: float = DEFAULT_LEVEL,
) -> BootstrapCI:
    """Percentile bootstrap CI of the mean of ``x - y`` (module docstring)."""
    d = _differences(x, y)
    if not 0.0 < level < 1.0:
        raise ScoringError(f"level must be in (0, 1), got {level}")
    if resamples < 1:
        raise ScoringError(f"resamples must be positive, got {resamples}")
    index = rng.integers(0, d.size, size=(resamples, d.size))
    means = np.array([reductions.mean(row) for row in d[index]], dtype=np.float64)
    alpha = (1.0 - level) / 2.0
    low, high = np.quantile(means, [alpha, 1.0 - alpha])
    return BootstrapCI(
        mean=reductions.mean(d),
        low=float(low),
        high=float(high),
        level=level,
        n=int(d.size),
        resamples=resamples,
    )


# --------------------------------------------------------------------------
# Rank statistics (module docstring)
# --------------------------------------------------------------------------


def _ordered(values: Sequence[float]) -> np.ndarray:
    d = np.asarray(values, dtype=np.float64)
    if d.ndim != 1 or d.size == 0:
        raise ScoringError("rank statistics need a non-empty 1-D sample")
    if bool(np.any(np.isnan(d))):
        raise ScoringError("rank statistics refuse nan: it has no rank")
    return d


def _doubled_ranks(magnitudes: np.ndarray) -> list[int]:
    """Average ranks (1-based) of ``magnitudes``, doubled so they are integers."""
    order = np.argsort(magnitudes, kind="stable")
    ranks = [0] * magnitudes.size
    i = 0
    while i < magnitudes.size:
        j = i
        while (
            j + 1 < magnitudes.size and magnitudes[order[j + 1]] == magnitudes[order[i]]
        ):
            j += 1
        for k in range(i, j + 1):
            ranks[int(order[k])] = (i + 1) + (j + 1)  # twice the average rank
        i = j + 1
    return ranks


def signed_rank_greater(differences: Sequence[float]) -> float:
    """One-sided exact Wilcoxon signed-rank p-value (module docstring)."""
    d = _ordered(differences)
    d = d[d != 0.0]
    if d.size == 0:
        return 1.0
    ranks = _doubled_ranks(np.abs(d))
    observed = sum(r for r, x in zip(ranks, d.tolist(), strict=True) if x > 0.0)
    counts = [0] * (sum(ranks) + 1)
    counts[0] = 1
    top = 0
    for r in ranks:
        for total in range(top, -1, -1):
            if counts[total]:
                counts[total + r] += counts[total]
        top += r
    tail = sum(counts[observed:])
    return float(Fraction(tail, 2 ** len(ranks)))


def order_median(values: Sequence[float]) -> float:
    """The median by order statistics, ``±inf`` allowed (module docstring)."""
    d = np.sort(_ordered(values))
    n = d.size
    if n % 2:
        return float(d[n // 2])
    lo, hi = float(d[n // 2 - 1]), float(d[n // 2])
    if math.isinf(lo) and math.isinf(hi) and lo != hi:
        return 0.0
    return (lo + hi) / 2.0


@dataclass(frozen=True)
class MedianCI:
    """The order median of a sample and its percentile bootstrap interval."""

    median: float
    low: float
    high: float
    level: float
    n: int
    resamples: int


def bootstrap_median_ci(
    values: Sequence[float],
    rng: np.random.Generator,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    level: float = DEFAULT_LEVEL,
) -> MedianCI:
    """Percentile bootstrap CI of :func:`order_median` (module docstring)."""
    d = _ordered(values)
    if not 0.0 < level < 1.0:
        raise ScoringError(f"level must be in (0, 1), got {level}")
    if resamples < 1:
        raise ScoringError(f"resamples must be positive, got {resamples}")
    n = d.size
    index = rng.integers(0, n, size=(resamples, n))
    samples = np.sort(d[index], axis=1)
    if n % 2:
        medians = samples[:, n // 2].copy()
    else:
        lo, hi = samples[:, n // 2 - 1], samples[:, n // 2]
        opposite = np.isinf(lo) & np.isinf(hi) & (lo != hi)
        medians = np.zeros(resamples, dtype=np.float64)
        keep = ~opposite
        medians[keep] = (lo[keep] + hi[keep]) / 2.0
    alpha = (1.0 - level) / 2.0
    low = float(np.quantile(medians, alpha, method="lower"))
    high = float(np.quantile(medians, 1.0 - alpha, method="higher"))
    return MedianCI(
        median=order_median(d.tolist()),
        low=low,
        high=high,
        level=level,
        n=int(n),
        resamples=resamples,
    )
