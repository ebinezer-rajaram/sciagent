"""The diagnostic catalogue for the point-process slice (SPEC §4.3).

All eight diagnostics, each a pure function of an
:class:`~sciagent.core.types.EventLog`. Their *version identifiers* belong to
the metric registry (backlog item 4); the computations belong here, with the
environment whose event logs they read.

Three describe dispersion and are matched across the four mechanisms by
construction, so they cannot discriminate:
:func:`inter_arrival_dispersion`, :func:`fano_factor`,
:func:`count_autocorrelation` at the reference window.

Five are the discriminators SPEC §4.2 assigns to the three-stage plan:

* :func:`spectral_peak_prominence` and :func:`spectral_peak_frequency` find the
  fixed period of a deterministic seasonal rate.
* :func:`phase_conditioned_dispersion` collapses to 1 for that mechanism and
  stays high for the others, which is what rules seasonality in or out.
* :func:`mean_high_run_length` and :func:`run_length_geometric_deviation`
  characterise the runs of above-average windows, separating the mechanisms that
  cluster in time from those that do not. See the latter's docstring for why
  this is weaker than SPEC §4.2's regime-sojourn discriminator, which needs
  state inference and therefore the posterior engine.
* :func:`size_dispersion` and :func:`sign_autocorrelation` cover the
  non-arrival components, which the compound and garden-path scenarios disturb.
"""

from __future__ import annotations

import math

import numpy as np

from environments.pointproc.components import ARRIVAL, SIGN, SIZE
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


# --------------------------------------------------------------------------
# PowerSpectrum
# --------------------------------------------------------------------------


def power_spectrum(log: EventLog, bin_width: float = 0.25) -> tuple[Floats, Floats]:
    """Return ``(frequencies, power)`` of the binned counting process.

    The counts are binned at ``bin_width``, mean-centred and transformed by a
    real FFT; the zero frequency is dropped, since a mean-centred series carries
    no information there. Power is normalised by the series length so that its
    scale does not depend on the observation span.

    Guarantees frequencies in cycles per unit time, strictly ascending, and
    power of the same length. Deterministic: no windowing, detrending or
    smoothing is applied, so the result is a function of the log alone.
    """
    counts = counts_in_windows(log, bin_width)
    centred = counts - float(np.mean(counts))
    spectrum = np.fft.rfft(centred)
    power = (np.abs(spectrum) ** 2) / float(counts.size)
    frequencies = np.fft.rfftfreq(counts.size, d=bin_width)
    return frequencies[1:], power[1:]


def spectral_peak_frequency(log: EventLog, bin_width: float = 0.25) -> float:
    """Return the frequency carrying the most power, in cycles per unit time.

    For a deterministic periodic rate this is ``1/period`` up to the frequency
    resolution ``1/span``. For the other three mechanisms the location of the
    maximum is not stable across seeds, which is the point: a *reproducible*
    peak is what distinguishes seasonality (SPEC §4.2).
    """
    frequencies, power = power_spectrum(log, bin_width)
    return float(frequencies[int(np.argmax(power))])


def spectral_peak_prominence(log: EventLog, bin_width: float = 0.25) -> float:
    """Return the peak power divided by the median power.

    Scale-free, so it compares across mechanisms and run lengths. A process with
    no periodic component gives a small value; the ratio grows without bound as
    a genuine line dominates the noise floor. The median rather than the mean is
    the denominator precisely because a strong line would inflate the mean and
    mask itself.
    """
    _, power = power_spectrum(log, bin_width)
    floor = float(np.median(power))
    if floor <= 0.0:
        raise ExecutionError("power spectrum has a degenerate noise floor")
    return float(np.max(power)) / floor


# --------------------------------------------------------------------------
# PhaseConditionedDispersion
# --------------------------------------------------------------------------


def phase_conditioned_dispersion(
    log: EventLog, period: float, window: float = 1.0, n_phase_bins: int = 8
) -> float:
    """Return the Fano factor of counts pooled within phase bins.

    Each window is assigned to a phase bin by the phase of its midpoint modulo
    ``period``; the Fano factor is computed within each bin and averaged,
    weighted by the number of windows in the bin.

    This is the diagnostic that settles seasonality. If the rate is a
    deterministic function of phase, conditioning on phase leaves a homogeneous
    Poisson process, so the result falls to about 1 while the unconditioned Fano
    factor stays high. For a mechanism whose clustering is not phase-locked --
    Hawkes, regime switching, a mixture -- conditioning removes nothing and the
    value is unchanged.

    Guarantees a positive result, and raises if any phase bin is too sparse to
    admit a variance.
    """
    if period <= 0.0:
        raise ExecutionError(f"period must be positive, got {period}")
    if n_phase_bins < 2:
        raise ExecutionError(f"need at least 2 phase bins, got {n_phase_bins}")
    counts = counts_in_windows(log, window)
    midpoints = (np.arange(counts.size, dtype=np.float64) + 0.5) * window
    phase = np.mod(midpoints, period) / period
    assignment = np.minimum((phase * n_phase_bins).astype(np.int64), n_phase_bins - 1)

    total_weight = 0
    weighted = 0.0
    for index in range(n_phase_bins):
        members = counts[assignment == index]
        if members.size < 2:
            raise ExecutionError(
                f"phase bin {index} holds {members.size} window(s); widen the "
                f"window, lengthen the run, or use fewer bins"
            )
        mean = float(np.mean(members))
        if mean <= 0.0:
            continue
        weighted += members.size * float(np.var(members, ddof=1)) / mean
        total_weight += members.size
    if total_weight == 0:
        raise ExecutionError("every phase bin was empty of events")
    return weighted / total_weight


# --------------------------------------------------------------------------
# RunLengthDistribution
# --------------------------------------------------------------------------


def high_run_lengths(log: EventLog, window: float = 1.0) -> Floats:
    """Return the lengths, in windows, of consecutive above-average windows.

    A "high" window is one whose count strictly exceeds the mean count. The
    resulting runs are the observable proxy for the high-rate periods of a
    latent regime, which is what SPEC §4.2 says identifies regime switching.
    """
    counts = counts_in_windows(log, window)
    high = counts > float(np.mean(counts))
    lengths: list[float] = []
    current = 0
    for flag in high:
        if flag:
            current += 1
        elif current:
            lengths.append(float(current))
            current = 0
    if current:
        lengths.append(float(current))
    if not lengths:
        raise ExecutionError("no above-average windows; widen the window")
    return np.array(lengths, dtype=np.float64)


def mean_high_run_length(log: EventLog, window: float = 1.0) -> float:
    """Return the mean length of an above-average run, in windows."""
    return float(np.mean(high_run_lengths(log, window)))


def run_length_geometric_deviation(log: EventLog, window: float = 1.0) -> float:
    """Return how far above-average run lengths depart from the geometric law.

    If windows were independent, whether each exceeds the mean would be an iid
    Bernoulli trial, so runs of consecutive above-average windows would be
    exactly geometric -- and a geometric distribution on ``{1, 2, ...}`` with
    mean ``m`` has squared coefficient of variation ``1 - 1/m``. This returns the
    absolute difference between the observed dispersion and that value, so what
    it measures is *temporal dependence in the run pattern*.

    Near zero for a homogeneous Poisson process and for a renewal mixture, whose
    windows carry no temporal correlation; well above zero for any mechanism
    that clusters in time.

    This is a weaker statement than SPEC §4.2's "geometric run-length
    distribution of high-rate periods", which is about the *latent regime's* own
    sojourns. Those cannot be recovered by thresholding at the mean: Poisson
    noise breaks a single long high-rate period into several short runs, so the
    observed runs are a thinned version of the regime's and are not geometric
    even when the regime's are. Measuring the regime's own sojourn law requires
    inferring the state, which belongs with the posterior engine rather than
    here.
    """
    lengths = high_run_lengths(log, window)
    if lengths.size < 2:
        raise ExecutionError("need at least two runs to estimate dispersion")
    mean = float(np.mean(lengths))
    if mean <= 0.0:
        raise ExecutionError("mean run length must be positive")
    observed = float(np.var(lengths, ddof=1)) / (mean * mean)
    return abs(observed - (1.0 - 1.0 / mean))


# --------------------------------------------------------------------------
# SizeDistributionMoments
# --------------------------------------------------------------------------


def mark_sizes(log: EventLog) -> Floats:
    """Return the mark sizes of ``log``."""
    try:
        return log.values[SIZE]
    except KeyError as exc:
        raise ExecutionError("event log has no size component") from exc


def size_mean(log: EventLog) -> float:
    """Return the mean mark size."""
    return float(np.mean(mark_sizes(log)))


def size_dispersion(log: EventLog) -> float:
    """Return the squared coefficient of variation of the mark sizes.

    Equals 1 for the reference exponential sizes, and exceeds 1 for the
    two-component mixture that scenario S8 applies to the size component. Since
    the arrival mechanisms leave sizes untouched, a value away from 1 localises
    the defect to a component other than ``arrival``.
    """
    sizes = mark_sizes(log)
    mean = float(np.mean(sizes))
    if mean <= 0.0:
        raise ExecutionError("mean mark size must be positive")
    return float(np.var(sizes, ddof=1)) / (mean * mean)


def size_skewness(log: EventLog) -> float:
    """Return the sample skewness of the mark sizes.

    Equals 2 for an exponential distribution. Reported alongside
    :func:`size_dispersion` because a mixture can match one moment while
    missing the other.
    """
    sizes = mark_sizes(log)
    centred = sizes - float(np.mean(sizes))
    variance = float(np.mean(centred**2))
    if variance <= 0.0:
        raise ExecutionError("mark sizes have zero variance")
    return float(np.mean(centred**3)) / math.pow(variance, 1.5)


# --------------------------------------------------------------------------
# SignAutocorrelation
# --------------------------------------------------------------------------


def signs(log: EventLog) -> Floats:
    """Return the sign indicators of ``log``, one per event, in ``{0, 1}``."""
    try:
        return log.values[SIGN]
    except KeyError as exc:
        raise ExecutionError("event log has no sign component") from exc


def sign_autocorrelation(log: EventLog, lag: int = 1) -> float:
    """Return the lag-``lag`` autocorrelation of the sign sequence.

    Zero for the reference iid Bernoulli signs. Included in the catalogue as a
    negative control: no mechanism in SPEC §4.2 touches the sign component, so a
    non-zero value indicates either a defect elsewhere in the programme or a
    fault in the framework.
    """
    if lag < 1:
        raise ExecutionError(f"lag must be at least 1, got {lag}")
    values = signs(log)
    if values.size <= lag + 1:
        raise ExecutionError("not enough events for the requested lag")
    centred = values - float(np.mean(values))
    denominator = float(np.dot(centred, centred))
    if denominator <= 0.0:
        return 0.0
    return float(np.dot(centred[:-lag], centred[lag:]) / denominator)
