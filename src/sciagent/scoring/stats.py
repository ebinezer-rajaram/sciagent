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
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
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
