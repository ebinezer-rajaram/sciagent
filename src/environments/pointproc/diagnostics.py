"""Dispersion diagnostics for the point-process slice.

Only the two SPEC §4.2 relies on to state its confounding claim are implemented
here: inter-arrival dispersion and the count Fano factor. The remaining six of
SPEC §4.3 arrive with the versioned metric registry (backlog item 4), which is
where their version identifiers will live.

Both are pure functions of an :class:`~sciagent.core.types.EventLog`.
"""

from __future__ import annotations

import numpy as np

from environments.pointproc.components import ARRIVAL
from sciagent.core.errors import ExecutionError
from sciagent.core.types import EventLog, Floats


def arrival_times(log: EventLog) -> Floats:
    """Return the arrival times of ``log``."""
    try:
        return log.values[ARRIVAL]
    except KeyError as exc:
        raise ExecutionError("event log has no arrival component") from exc


def inter_arrival_times(log: EventLog) -> Floats:
    """Return the ``n-1`` gaps between consecutive arrivals."""
    times = arrival_times(log)
    if times.size < 2:
        raise ExecutionError("inter-arrival times need at least two events")
    return np.diff(times)


def inter_arrival_dispersion(log: EventLog) -> float:
    """Return the squared coefficient of variation of the inter-arrival times.

    Equals 1 for a homogeneous Poisson process; above 1 means overdispersed,
    i.e. more clustered than Poisson. This is the first diagnostic any
    investigator runs and, by construction, it does not separate the four
    mechanisms of SPEC §4.2.
    """
    gaps = inter_arrival_times(log)
    mean = float(np.mean(gaps))
    if mean <= 0.0:
        raise ExecutionError("mean inter-arrival time must be positive")
    return float(np.var(gaps, ddof=1)) / (mean * mean)


def mean_rate(log: EventLog) -> float:
    """Return the realised mean arrival rate, events per unit time."""
    times = arrival_times(log)
    span = float(times[-1])
    if span <= 0.0:
        raise ExecutionError("observation span must be positive")
    return float(times.size) / span


def counts_in_windows(log: EventLog, window: float) -> Floats:
    """Return the event counts in consecutive windows of width ``window``.

    Windows tile ``[0, T]`` from the origin; the final partial window is
    discarded so that every count is drawn from an equal-width interval.
    """
    if window <= 0.0:
        raise ExecutionError(f"window must be positive, got {window}")
    times = arrival_times(log)
    span = float(times[-1])
    n_windows = int(span // window)
    if n_windows < 2:
        raise ExecutionError(
            f"window {window} yields {n_windows} complete windows over a span of "
            f"{span}; at least 2 are needed"
        )
    edges = np.arange(n_windows + 1, dtype=np.float64) * window
    counts, _ = np.histogram(times, bins=edges)
    return counts.astype(np.float64)


def fano_factor(log: EventLog, window: float) -> float:
    """Return the count Fano factor ``Var(N)/E(N)`` at the given window width.

    Equals 1 for a homogeneous Poisson process at every window. Its *profile*
    across window widths is what eventually separates the mechanisms; its value
    at any single window does not, which is the point of the confounding.
    """
    counts = counts_in_windows(log, window)
    mean = float(np.mean(counts))
    if mean <= 0.0:
        raise ExecutionError("mean count must be positive")
    return float(np.var(counts, ddof=1)) / mean


def count_autocorrelation(log: EventLog, window: float, lag: int = 1) -> float:
    """Return the lag-``lag`` autocorrelation of windowed counts.

    Zero for a renewal process at windows above its correlation length, positive
    for genuinely temporally clustered mechanisms. This is the diagnostic that
    actually separates the Poisson mixture from the other three, in place of the
    "flat Fano factor" of SPEC §4.2, which does not hold for a renewal process.
    """
    counts = counts_in_windows(log, window)
    if counts.size <= lag + 1:
        raise ExecutionError("not enough windows for the requested lag")
    centred = counts - float(np.mean(counts))
    denominator = float(np.dot(centred, centred))
    if denominator <= 0.0:
        return 0.0
    return float(np.dot(centred[:-lag], centred[lag:]) / denominator)
