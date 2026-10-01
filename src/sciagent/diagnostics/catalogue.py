"""The v2 diagnostic catalogue (SPEC §4.0 ``diagnostic``, §2.2, §4.2, §4.4).

A registry of named statistics of a :class:`~sciagent.glm.data.EventLog`. Each
is a pure, deterministic function ``(log, channels, args) -> float``. They are
instruments (SPEC §6.3): ``fit`` reports predictive p-values on them, the oracle
picks each truth's *telling diagnostic* among them (§4.2), and committed
predictions are evaluated with them (§4.4).

**Domain independence.** Nothing here knows the environment. Channels are named
only through arguments (``channel``), and descriptions use no domain words, so
the anonymised condition (SPEC §5) needs to rename only the diagnostic names and
the channel names.

**Arguments.** Every argument has a kind (:class:`ArgKind`):

- ``TIME`` arguments (window, period, bin width, scale) are in the log's time
  units. Their defaults are *multiples of the mean inter-event time*
  ``τ̄ = horizon / n`` (:func:`mean_gap`), resolved per log by
  :func:`resolve_args`; their bounds are ``[low · τ̄, high · horizon]``.
- ``INTEGER`` (lag, bin counts) and ``FRACTION`` (quantiles) have fixed bounds.
- ``CHANNEL`` names a mark channel of the log, restricted by channel kind.

:func:`args_schema` renders the arguments as a JSON schema (with absolute time
bounds when given a log); :func:`resolve_args` validates and fills defaults.

**Time rescaling** (SPEC §5). Under ``t → c·t`` (times and horizon scaled by c)
with every explicit ``TIME`` argument scaled by c (:func:`rescale_args`), each
diagnostic transforms by its declared :class:`Covariance`: ``INVARIANT`` values
are unchanged, ``INVERSE_TIME`` values (rates, frequencies) are divided by c
(:func:`transform_value`). Defaults and bounds scale automatically because τ̄
does. For a power-of-two c every step is exact in floating point (bins land on
the same events), so the transform holds to the last bit up to transcendental
rounding; for other c, an event lying exactly on a window edge can change bin.
An anonymiser should therefore use a power-of-two time factor.

**Degenerate input.** Each diagnostic declares ``min_events``; fewer raises
:class:`InsufficientDataError`, as does a statistic that is undefined on the
data (fewer than two complete windows, an empty phase bin, no runs). A
correlation or autocorrelation whose variance is zero returns ``0.0``, a
dispersion of constant data returns ``0.0``; nothing returns NaN or ∞
(:func:`compute` checks).

**Determinism and cost.** Every float fold goes through
:mod:`sciagent.core.reductions` (exactly rounded); ties are broken by event
order. Every diagnostic is O(n log n) or O(n · k) for its small integer args,
except the two periodogram diagnostics, which are an FFT over ``horizon /
bin_width`` bins.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

import numpy as np
from scipy.stats import rankdata

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError
from sciagent.glm.data import EventLog, Floats
from sciagent.glm.grammar import ChannelKind, ChannelSpec

type ArgValue = float | int | str
type Args = Mapping[str, ArgValue]


class DiagnosticError(SciAgentError):
    """Base for faults raised by the diagnostic catalogue."""


class UnknownDiagnosticError(DiagnosticError):
    """No diagnostic of that name is in the catalogue."""


class DiagnosticArgumentError(DiagnosticError):
    """An argument is unknown, missing, mistyped, out of bounds, or a bad channel."""


class InsufficientDataError(DiagnosticError):
    """The log has too few events, or too little variation, for the statistic."""


class ArgKind(Enum):
    TIME = "time"
    INTEGER = "integer"
    FRACTION = "fraction"
    CHANNEL = "channel"


class Covariance(Enum):
    """How a diagnostic's value transforms under ``t → c·t`` (see module docstring)."""

    INVARIANT = "invariant"
    INVERSE_TIME = "inverse_time"


ALL_KINDS: Final = (ChannelKind.POSITIVE, ChannelKind.REAL, ChannelKind.SIGN)


@dataclass(frozen=True)
class ArgSpec:
    """One argument of a diagnostic.

    ``default`` is None for a required argument. For ``TIME`` it is in units of
    the mean gap τ̄, ``low`` is in units of τ̄ and ``high`` is a fraction of the
    horizon. For ``INTEGER`` and ``FRACTION``, ``low`` and ``high`` are the
    inclusive bounds. ``channel_kinds`` restricts a ``CHANNEL`` argument.
    """

    name: str
    kind: ArgKind
    description: str
    default: float | int | None = None
    low: float = 0.0
    high: float = 0.0
    channel_kinds: tuple[ChannelKind, ...] = ALL_KINDS


type DiagnosticFn = Callable[[EventLog, tuple[ChannelSpec, ...], Args], float]


@dataclass(frozen=True)
class Diagnostic:
    """A catalogue entry. ``function`` receives fully resolved arguments."""

    name: str
    description: str
    args: tuple[ArgSpec, ...]
    covariance: Covariance
    min_events: int
    function: DiagnosticFn


# --------------------------------------------------------------------------
# Argument access and shared helpers
# --------------------------------------------------------------------------


def _time(args: Args, name: str) -> float:
    value = args[name]
    if isinstance(value, str):
        raise DiagnosticArgumentError(f"{name} must be a number")
    return float(value)


def _int(args: Args, name: str) -> int:
    value = args[name]
    if not isinstance(value, int):
        raise DiagnosticArgumentError(f"{name} must be an integer")
    return value


def _str(args: Args, name: str) -> str:
    value = args[name]
    if not isinstance(value, str):
        raise DiagnosticArgumentError(f"{name} must be a channel name")
    return value


def mean_gap(log: EventLog) -> float:
    """The mean inter-event time ``τ̄ = horizon / n``: the unit of every time default."""
    if log.n == 0:
        raise InsufficientDataError("an empty log has no mean inter-event time")
    return log.horizon / log.n


def _gaps(log: EventLog) -> Floats:
    return np.diff(log.times)


def _counts(log: EventLog, window: float) -> Floats:
    """Counts in the complete windows ``[k·w, (k+1)·w)`` tiling ``[0, horizon]``.

    The final partial window is dropped, so every count is over an equal width.
    """
    if not window > 0.0:
        raise DiagnosticArgumentError(f"window must be positive, got {window}")
    k = int(log.horizon // window)
    if k < 2:
        raise InsufficientDataError(
            f"window {window} gives {k} complete window(s) over horizon "
            f"{log.horizon}; at least 2 are needed"
        )
    edges = np.arange(k + 1, dtype=np.float64) * window
    return np.diff(np.searchsorted(log.times, edges, side="left")).astype(np.float64)


def _fano(counts: Floats) -> float:
    mean = reductions.mean(counts)
    if mean <= 0.0:
        raise InsufficientDataError("no events fall in the complete windows")
    return reductions.variance(counts) / mean


def _autocorrelation(values: Floats, lag: int) -> float:
    if values.size <= lag + 1:
        raise InsufficientDataError(f"{values.size} values are too few for lag {lag}")
    centred = values - reductions.mean(values)
    denominator = reductions.dot(centred, centred)
    if denominator <= 0.0:
        return 0.0
    return reductions.dot(centred[:-lag], centred[lag:]) / denominator


def _correlation(x: Floats, y: Floats) -> float:
    """Pearson correlation; 0.0 when either side is constant."""
    if x.size < 2:
        raise InsufficientDataError("a correlation needs at least two pairs")
    cx = x - reductions.mean(x)
    cy = y - reductions.mean(y)
    sxx = reductions.dot(cx, cx)
    syy = reductions.dot(cy, cy)
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, reductions.dot(cx, cy) / math.sqrt(sxx * syy)))


def _ols_slope(x: Floats, y: Floats) -> float:
    """Least-squares slope of y on x; 0.0 when x is constant."""
    cx = x - reductions.mean(x)
    sxx = reductions.dot(cx, cx)
    if sxx <= 0.0:
        return 0.0
    return reductions.dot(cx, y - reductions.mean(y)) / sxx


def _median(values: Floats) -> float:
    ordered = np.sort(values)
    m = ordered.size
    if m % 2:
        return float(ordered[m // 2])
    return (float(ordered[m // 2 - 1]) + float(ordered[m // 2])) / 2.0


def _ks_uniform(u: Floats) -> float:
    """Kolmogorov-Smirnov distance of sorted values in [0, 1] from Uniform(0, 1)."""
    n = u.size
    i = np.arange(1, n + 1, dtype=np.float64)
    return float(max(np.max(i / n - u), np.max(u - (i - 1.0) / n)))


def _marks(log: EventLog, args: Args, name: str = "channel") -> Floats:
    return log.marks[_str(args, name)]


# --------------------------------------------------------------------------
# Arrival-only diagnostics (ports of v1)
# --------------------------------------------------------------------------


def _mean_rate(log: EventLog, channels: tuple[ChannelSpec, ...], args: Args) -> float:
    return log.n / log.horizon


def _inter_arrival_dispersion(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    gaps = _gaps(log)
    mean = reductions.mean(gaps)
    return reductions.variance(gaps) / (mean * mean)


def _fano_factor(log: EventLog, channels: tuple[ChannelSpec, ...], args: Args) -> float:
    return _fano(_counts(log, _time(args, "window")))


def _count_autocorrelation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    return _autocorrelation(_counts(log, _time(args, "window")), _int(args, "lag"))


def _power_spectrum(log: EventLog, bin_width: float) -> tuple[Floats, Floats]:
    """``(frequencies, power)`` of the centred binned counts, zero frequency dropped."""
    counts = _counts(log, bin_width)
    centred = counts - reductions.mean(counts)
    power = np.abs(np.fft.rfft(centred)) ** 2 / float(counts.size)
    frequencies = np.fft.rfftfreq(counts.size, d=bin_width)
    return frequencies[1:], power[1:]


def _spectral_peak_frequency(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    frequencies, power = _power_spectrum(log, _time(args, "bin_width"))
    return float(frequencies[int(np.argmax(power))])


def _spectral_peak_prominence(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    _, power = _power_spectrum(log, _time(args, "bin_width"))
    peak = float(np.max(power))
    if peak <= 0.0:
        return 1.0  # a flat (all-zero) spectrum has no peak
    floor = _median(power)
    if floor <= 0.0:
        raise InsufficientDataError("the power spectrum has a zero noise floor")
    return peak / floor


def _phase_conditioned_dispersion(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    period = _time(args, "period")
    window = _time(args, "window")
    n_bins = _int(args, "n_phase_bins")
    counts = _counts(log, window)
    midpoints = (np.arange(counts.size, dtype=np.float64) + 0.5) * window
    phase = np.mod(midpoints, period) / period
    assignment = np.minimum((phase * n_bins).astype(np.int64), n_bins - 1)
    weighted = 0.0
    total_weight = 0
    for index in range(n_bins):
        members = counts[assignment == index]
        if members.size < 2:
            raise InsufficientDataError(
                f"phase bin {index} holds {members.size} window(s); widen the "
                f"window, use fewer bins, or a longer log"
            )
        mean = reductions.mean(members)
        if mean <= 0.0:
            continue
        weighted += members.size * reductions.variance(members) / mean
        total_weight += members.size
    if total_weight == 0:
        raise InsufficientDataError("every phase bin is empty of events")
    return weighted / total_weight


def _high_runs(log: EventLog, window: float) -> Floats:
    counts = _counts(log, window)
    high = counts > reductions.mean(counts)
    lengths: list[float] = []
    current = 0
    for flag in high.tolist():
        if flag:
            current += 1
        elif current:
            lengths.append(float(current))
            current = 0
    if current:
        lengths.append(float(current))
    if not lengths:
        raise InsufficientDataError("no above-average windows")
    return np.array(lengths, dtype=np.float64)


def _mean_high_run_length(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    return reductions.mean(_high_runs(log, _time(args, "window")))


def _run_length_geometric_deviation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    lengths = _high_runs(log, _time(args, "window"))
    if lengths.size < 2:
        raise InsufficientDataError("need at least two above-average runs")
    mean = reductions.mean(lengths)
    observed = reductions.variance(lengths) / (mean * mean)
    return abs(observed - (1.0 - 1.0 / mean))


# --------------------------------------------------------------------------
# Mark diagnostics (ports of v1, any channel)
# --------------------------------------------------------------------------


def _mark_mean(log: EventLog, channels: tuple[ChannelSpec, ...], args: Args) -> float:
    return reductions.mean(_marks(log, args))


def _mark_dispersion(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    values = _marks(log, args)
    mean = reductions.mean(values)
    if mean <= 0.0:
        raise InsufficientDataError("mark dispersion needs a positive mean")
    return reductions.variance(values) / (mean * mean)


def _mark_skewness(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    values = _marks(log, args)
    centred = values - reductions.mean(values)
    variance = reductions.mean(centred**2)
    if variance <= 0.0:
        return 0.0
    return reductions.mean(centred**3) / math.pow(variance, 1.5)


def _mark_autocorrelation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    return _autocorrelation(_marks(log, args), _int(args, "lag"))


def _mark_gap_correlation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    return _correlation(_marks(log, args)[:-1], _gaps(log))


# --------------------------------------------------------------------------
# Cross-mark diagnostics (new in v2)
# --------------------------------------------------------------------------


def _next_gap_after_large_mark(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    values = _marks(log, args)[:-1]
    gaps = _gaps(log)
    m = values.size
    share = round((1.0 - float(args["quantile"])) * m, 9)
    k = max(1, math.floor(share))
    order = np.argsort(values, kind="stable")
    top, rest = order[m - k :], order[: m - k]
    return reductions.mean(gaps[top]) / reductions.mean(gaps[rest])


def _following_counts(log: EventLog, window: float) -> tuple[Floats, Floats]:
    """Events with ``t_i + w ≤ horizon``: indices, and counts in ``(t_i, t_i + w]``."""
    times = log.times
    keep = np.flatnonzero(times + window <= log.horizon)
    if keep.size == 0:
        raise InsufficientDataError("no event is followed by a complete window")
    ends = np.searchsorted(times, times[keep] + window, side="right")
    return keep.astype(np.float64), (ends - keep - 1).astype(np.float64)


def _rate_after_mark_slope(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    index, counts = _following_counts(log, _time(args, "window"))
    if counts.size < 3:
        raise InsufficientDataError("need three events followed by a complete window")
    values = _marks(log, args)[index.astype(np.int64)]
    ranks = (np.asarray(rankdata(values), dtype=np.float64) - 1.0) / (counts.size - 1)
    mean = reductions.mean(counts)
    if mean <= 0.0:
        return 0.0
    return _ols_slope(ranks, counts) / mean


def _mark_intensity_correlation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    window = _time(args, "window")
    times = log.times
    keep = np.flatnonzero(times >= window)
    if keep.size < 3:
        raise InsufficientDataError("need three events preceded by a complete window")
    starts = np.searchsorted(times, times[keep] - window, side="left")
    preceding = (keep - starts).astype(np.float64)
    return _correlation(_marks(log, args)[keep], preceding)


def _mark_cross_correlation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    lag = _int(args, "lag")
    first = _marks(log, args)
    second = _marks(log, args, "other")
    if log.n - lag < 3:
        raise InsufficientDataError(f"{log.n} events are too few for lag {lag}")
    return _correlation(first[: log.n - lag], second[lag:])


# --------------------------------------------------------------------------
# Spectral, clustering and residual diagnostics (new in v2)
# --------------------------------------------------------------------------


def _spectral_power_at_period(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    bin_width = _time(args, "bin_width")
    counts = _counts(log, bin_width)
    variance = reductions.variance(counts)
    if variance <= 0.0:
        return 0.0
    centred = counts - reductions.mean(counts)
    cycles = bin_width / _time(args, "period")
    angle = 2.0 * math.pi * cycles * (np.arange(counts.size, dtype=np.float64) + 0.5)
    real = reductions.dot(centred, np.cos(angle))
    imag = reductions.dot(centred, np.sin(angle))
    return (real * real + imag * imag) / counts.size / variance


def _pair_clustering_ratio(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    scale = _time(args, "scale")
    times = log.times
    n = log.n
    within = np.searchsorted(times, times + scale, side="right") - np.arange(1, n + 1)
    pairs = reductions.total(within.astype(np.float64))
    r = scale / log.horizon
    expected = 0.5 * n * (n - 1) * (2.0 * r - r * r)
    return pairs / expected


def _log_gap_cv(log: EventLog, channels: tuple[ChannelSpec, ...], args: Args) -> float:
    gaps = _gaps(log)
    logs = np.log(gaps / reductions.mean(gaps))
    centre = reductions.mean(logs)
    if centre == 0.0:
        return 0.0
    return reductions.deviation(logs) / abs(centre)


def _count_variance_time_slope(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    window = _time(args, "window")
    n_scales = _int(args, "n_scales")
    fanos = np.array(
        [_fano(_counts(log, window * 2.0**j)) for j in range(n_scales)],
        dtype=np.float64,
    )
    return _ols_slope(np.arange(n_scales, dtype=np.float64), fanos)


def _burstiness(log: EventLog, channels: tuple[ChannelSpec, ...], args: Args) -> float:
    gaps = _gaps(log)
    mu = reductions.mean(gaps)
    sigma = reductions.deviation(gaps)
    return (sigma - mu) / (sigma + mu)


def _post_event_rate_ratio(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    window = _time(args, "window")
    _, counts = _following_counts(log, window)
    return reductions.mean(counts) / (window * log.n / log.horizon)


def _count_trend_slope(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    counts = _counts(log, _time(args, "window"))
    mean = reductions.mean(counts)
    if mean <= 0.0:
        raise InsufficientDataError("no events fall in the complete windows")
    index = np.arange(counts.size, dtype=np.float64)
    return _ols_slope(index, counts) * counts.size / mean


def _uniformity_ks(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    return _ks_uniform(log.times / log.horizon)


def _gap_exponential_ks(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    gaps = np.sort(_gaps(log))
    return _ks_uniform(-np.expm1(-gaps / reductions.mean(gaps)))


def _gap_autocorrelation(
    log: EventLog, channels: tuple[ChannelSpec, ...], args: Args
) -> float:
    return _autocorrelation(_gaps(log), _int(args, "lag"))


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------


def _window(default: float, what: str = "Width of the counting windows.") -> ArgSpec:
    return ArgSpec("window", ArgKind.TIME, what, default, low=0.05, high=0.5)


def _lag(default: int, low: int = 1, what: str = "Lag, in steps.") -> ArgSpec:
    return ArgSpec("lag", ArgKind.INTEGER, what, default, low=low, high=50)


_CHANNEL: Final = ArgSpec("channel", ArgKind.CHANNEL, "Mark channel to read.")
_BIN_WIDTH: Final = ArgSpec(
    "bin_width", ArgKind.TIME, "Bin width for the binned counts.", 0.25, 0.01, 0.25
)
_PERIOD: Final = ArgSpec(
    "period", ArgKind.TIME, "Candidate period.", None, low=0.5, high=0.5
)

_INV: Final = Covariance.INVARIANT

CATALOGUE: Final[Mapping[str, Diagnostic]] = {
    d.name: d
    for d in (
        # -- ports of v1 ---------------------------------------------------
        Diagnostic(
            "mean_rate",
            "Events per unit time over the whole observation horizon.",
            (),
            Covariance.INVERSE_TIME,
            1,
            _mean_rate,
        ),
        Diagnostic(
            "inter_arrival_dispersion",
            "Squared coefficient of variation of the gaps between events "
            "(1 for a homogeneous Poisson process).",
            (),
            _INV,
            3,
            _inter_arrival_dispersion,
        ),
        Diagnostic(
            "fano_factor",
            "Variance over mean of event counts in equal windows (1 for Poisson).",
            (_window(2.0),),
            _INV,
            1,
            _fano_factor,
        ),
        Diagnostic(
            "count_autocorrelation",
            "Autocorrelation of windowed event counts at a lag in windows.",
            (_window(2.0), _lag(1, what="Lag, in windows.")),
            _INV,
            1,
            _count_autocorrelation,
        ),
        Diagnostic(
            "spectral_peak_frequency",
            "Frequency (cycles per unit time) of the largest periodogram ordinate "
            "of the binned counts.",
            (_BIN_WIDTH,),
            Covariance.INVERSE_TIME,
            1,
            _spectral_peak_frequency,
        ),
        Diagnostic(
            "spectral_peak_prominence",
            "Largest periodogram ordinate of the binned counts over the median one.",
            (_BIN_WIDTH,),
            _INV,
            1,
            _spectral_peak_prominence,
        ),
        Diagnostic(
            "phase_conditioned_dispersion",
            "Fano factor of windowed counts within phase bins of a candidate "
            "period, averaged over bins (near 1 if the period explains the "
            "clustering).",
            (
                _PERIOD,
                _window(1.0),
                ArgSpec(
                    "n_phase_bins", ArgKind.INTEGER, "Number of phase bins.", 8, 2, 32
                ),
            ),
            _INV,
            1,
            _phase_conditioned_dispersion,
        ),
        Diagnostic(
            "mean_high_run_length",
            "Mean length, in windows, of runs of windows whose count exceeds "
            "the mean count.",
            (_window(1.0),),
            _INV,
            1,
            _mean_high_run_length,
        ),
        Diagnostic(
            "run_length_geometric_deviation",
            "Distance of the above-mean run-length dispersion from the geometric "
            "law that independent windows would give.",
            (_window(1.0),),
            _INV,
            1,
            _run_length_geometric_deviation,
        ),
        Diagnostic(
            "mark_mean",
            "Mean of a mark channel.",
            (_CHANNEL,),
            _INV,
            1,
            _mark_mean,
        ),
        Diagnostic(
            "mark_dispersion",
            "Squared coefficient of variation of a positive mark channel "
            "(1 for exponential marks).",
            (
                ArgSpec(
                    "channel",
                    ArgKind.CHANNEL,
                    "Positive mark channel to read.",
                    channel_kinds=(ChannelKind.POSITIVE,),
                ),
            ),
            _INV,
            2,
            _mark_dispersion,
        ),
        Diagnostic(
            "mark_skewness",
            "Sample skewness of a mark channel (2 for exponential marks).",
            (_CHANNEL,),
            _INV,
            2,
            _mark_skewness,
        ),
        Diagnostic(
            "mark_autocorrelation",
            "Autocorrelation of a mark channel across successive events.",
            (_CHANNEL, _lag(1, what="Lag, in events.")),
            _INV,
            1,
            _mark_autocorrelation,
        ),
        Diagnostic(
            "mark_gap_correlation",
            "Correlation of each event's mark with the gap to the next event "
            "(negative if large marks shorten the following gap).",
            (_CHANNEL,),
            _INV,
            3,
            _mark_gap_correlation,
        ),
        # -- cross-mark ----------------------------------------------------
        Diagnostic(
            "next_gap_after_large_mark",
            "Mean gap following the events with the largest marks over the mean "
            "gap following the rest (1 if marks do not affect timing).",
            (
                _CHANNEL,
                ArgSpec(
                    "quantile",
                    ArgKind.FRACTION,
                    "Events above this mark quantile count as large.",
                    0.9,
                    0.5,
                    0.99,
                ),
            ),
            _INV,
            3,
            _next_gap_after_large_mark,
        ),
        Diagnostic(
            "rate_after_mark_slope",
            "Slope of the event count in a window after each event against that "
            "event's mark rank (0 to 1), relative to the mean count.",
            (_CHANNEL, _window(2.0, "Width of the window after each event.")),
            _INV,
            3,
            _rate_after_mark_slope,
        ),
        Diagnostic(
            "mark_intensity_correlation",
            "Correlation of each event's mark with the number of events in a "
            "window before it.",
            (_CHANNEL, _window(1.0, "Width of the window before each event.")),
            _INV,
            3,
            _mark_intensity_correlation,
        ),
        Diagnostic(
            "mark_cross_correlation",
            "Correlation of one mark channel with another, the second read a "
            "number of events later.",
            (
                _CHANNEL,
                ArgSpec("other", ArgKind.CHANNEL, "Second mark channel to read."),
                _lag(0, low=0, what="Lag of the second channel, in events."),
            ),
            _INV,
            3,
            _mark_cross_correlation,
        ),
        # -- spectral, clustering and residual -----------------------------
        Diagnostic(
            "spectral_power_at_period",
            "Periodogram ordinate of the binned counts at a given period, over "
            "the count variance (about 1 without periodicity).",
            (_PERIOD, _BIN_WIDTH),
            _INV,
            1,
            _spectral_power_at_period,
        ),
        Diagnostic(
            "pair_clustering_ratio",
            "Pairs of events within a time scale of each other over the number "
            "expected for uniformly scattered events (1 without clustering).",
            (
                ArgSpec(
                    "scale",
                    ArgKind.TIME,
                    "Pair separation scale.",
                    1.0,
                    low=0.01,
                    high=0.5,
                ),
            ),
            _INV,
            2,
            _pair_clustering_ratio,
        ),
        Diagnostic(
            "log_gap_cv",
            "Standard deviation over absolute mean of the log of gaps normalised "
            "by their mean.",
            (),
            _INV,
            3,
            _log_gap_cv,
        ),
        Diagnostic(
            "count_variance_time_slope",
            "Slope of the Fano factor against log2 of the window over doubling "
            "windows (0 for Poisson, positive for clustering).",
            (
                _window(0.5, "Smallest window; each further scale doubles it."),
                ArgSpec(
                    "n_scales", ArgKind.INTEGER, "Number of window scales.", 6, 3, 10
                ),
            ),
            _INV,
            1,
            _count_variance_time_slope,
        ),
        Diagnostic(
            "burstiness",
            "Burstiness of the gaps, (sd - mean) / (sd + mean): -1 regular, "
            "0 Poisson, toward 1 bursty.",
            (),
            _INV,
            3,
            _burstiness,
        ),
        Diagnostic(
            "post_event_rate_ratio",
            "Mean event count in a window after each event over the count the "
            "mean rate predicts (1 without excitation).",
            (_window(1.0, "Width of the window after each event."),),
            _INV,
            1,
            _post_event_rate_ratio,
        ),
        Diagnostic(
            "count_trend_slope",
            "Least-squares trend of windowed counts, as the relative change in "
            "count over the whole horizon.",
            (_window(5.0),),
            _INV,
            1,
            _count_trend_slope,
        ),
        Diagnostic(
            "uniformity_ks",
            "Kolmogorov-Smirnov distance of event times from uniform on the "
            "horizon (residual against a constant rate).",
            (),
            _INV,
            1,
            _uniformity_ks,
        ),
        Diagnostic(
            "gap_exponential_ks",
            "Kolmogorov-Smirnov distance of the mean-normalised gaps from a unit "
            "exponential.",
            (),
            _INV,
            3,
            _gap_exponential_ks,
        ),
        Diagnostic(
            "gap_autocorrelation",
            "Autocorrelation of successive gaps between events (0 for a renewal "
            "process).",
            (_lag(1, what="Lag, in gaps."),),
            _INV,
            3,
            _gap_autocorrelation,
        ),
    )
}


def names() -> tuple[str, ...]:
    """Every diagnostic name, in catalogue order (fixed)."""
    return tuple(CATALOGUE)


def get(name: str) -> Diagnostic:
    try:
        return CATALOGUE[name]
    except KeyError as exc:
        raise UnknownDiagnosticError(f"no diagnostic named {name!r}") from exc


# --------------------------------------------------------------------------
# Arguments: schema, resolution, rescaling
# --------------------------------------------------------------------------


def _allowed_channels(arg: ArgSpec, channels: tuple[ChannelSpec, ...]) -> list[str]:
    return [c.name for c in channels if c.kind in arg.channel_kinds]


def args_schema(
    name: str, channels: tuple[ChannelSpec, ...], log: EventLog | None = None
) -> dict[str, object]:
    """The JSON schema of a diagnostic's ``args``.

    Time arguments always carry ``exclusiveMinimum: 0``; with ``log`` they also
    carry the absolute ``minimum``, ``maximum`` and ``default`` for that log.
    """
    spec = get(name)
    tau = mean_gap(log) if log is not None else None
    properties: dict[str, object] = {}
    for arg in spec.args:
        prop: dict[str, object]
        match arg.kind:
            case ArgKind.TIME:
                text = (
                    f"{arg.description} In time units; "
                    f"between {arg.low:g} mean gaps and {arg.high:g} of the horizon"
                )
                if arg.default is not None:
                    text += f"; default {arg.default:g} mean gaps"
                prop = {"type": "number", "exclusiveMinimum": 0, "description": text}
                if log is not None and tau is not None:
                    prop["minimum"] = arg.low * tau
                    prop["maximum"] = arg.high * log.horizon
                    if arg.default is not None:
                        prop["default"] = arg.default * tau
            case ArgKind.INTEGER:
                prop = {
                    "type": "integer",
                    "minimum": int(arg.low),
                    "maximum": int(arg.high),
                    "description": arg.description,
                }
                if arg.default is not None:
                    prop["default"] = arg.default
            case ArgKind.FRACTION:
                prop = {
                    "type": "number",
                    "minimum": arg.low,
                    "maximum": arg.high,
                    "description": arg.description,
                }
                if arg.default is not None:
                    prop["default"] = arg.default
            case ArgKind.CHANNEL:
                prop = {
                    "type": "string",
                    "enum": _allowed_channels(arg, channels),
                    "description": arg.description,
                }
        properties[arg.name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": [a.name for a in spec.args if a.default is None],
        "additionalProperties": False,
    }


def _number(arg: ArgSpec, value: ArgValue) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DiagnosticArgumentError(f"{arg.name} must be a number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise DiagnosticArgumentError(f"{arg.name} must be finite, got {value!r}")
    return result


def _resolve_one(
    arg: ArgSpec,
    value: ArgValue | None,
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    check_bounds: bool,
) -> ArgValue:
    if value is None:
        if arg.default is None:
            raise DiagnosticArgumentError(f"argument {arg.name!r} is required")
        if arg.kind is ArgKind.TIME:
            return arg.default * mean_gap(log)
        return arg.default
    match arg.kind:
        case ArgKind.TIME:
            x = _number(arg, value)
            if not x > 0.0:
                raise DiagnosticArgumentError(f"{arg.name} must be positive, got {x}")
            if check_bounds:
                lo, hi = arg.low * mean_gap(log), arg.high * log.horizon
                if not lo <= x <= hi:
                    raise DiagnosticArgumentError(
                        f"{arg.name}={x} outside [{lo}, {hi}] "
                        f"({arg.low:g} mean gaps to {arg.high:g} of the horizon)"
                    )
            return x
        case ArgKind.INTEGER:
            x = _number(arg, value)
            if not x.is_integer():
                raise DiagnosticArgumentError(f"{arg.name} must be an integer")
            if not arg.low <= x <= arg.high:
                raise DiagnosticArgumentError(
                    f"{arg.name}={int(x)} outside [{int(arg.low)}, {int(arg.high)}]"
                )
            return int(x)
        case ArgKind.FRACTION:
            x = _number(arg, value)
            if not arg.low <= x <= arg.high:
                raise DiagnosticArgumentError(
                    f"{arg.name}={x} outside [{arg.low}, {arg.high}]"
                )
            return x
        case ArgKind.CHANNEL:
            allowed = _allowed_channels(arg, channels)
            if not isinstance(value, str) or value not in allowed:
                raise DiagnosticArgumentError(
                    f"{arg.name}={value!r} is not one of {allowed}"
                )
            if value not in log.marks:
                raise DiagnosticArgumentError(f"the log has no channel {value!r}")
            return value


def resolve_args(
    name: str,
    args: Args,
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    *,
    check_bounds: bool = True,
) -> dict[str, ArgValue]:
    """Validate ``args`` for ``log`` and fill every default (time defaults in τ̄ units).

    Raises :class:`InsufficientDataError` if the log has fewer than the
    diagnostic's ``min_events``, and :class:`DiagnosticArgumentError` for an
    unknown, missing, mistyped or out-of-bounds argument. ``check_bounds=False``
    skips only the log-relative bounds on time arguments (used when an argument
    resolved on one log is applied to replicates of it).
    """
    spec = get(name)
    if log.n < spec.min_events:
        raise InsufficientDataError(
            f"{name} needs at least {spec.min_events} event(s), the log has {log.n}"
        )
    known = [a.name for a in spec.args]
    unknown = sorted(k for k in args if k not in known)
    if unknown:
        raise DiagnosticArgumentError(f"{name} takes {known}; unknown: {unknown}")
    return {
        arg.name: _resolve_one(arg, args.get(arg.name), log, channels, check_bounds)
        for arg in spec.args
    }


def compute(
    name: str,
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    args: Args,
    *,
    check_bounds: bool = True,
) -> float:
    """Evaluate diagnostic ``name`` on ``log``. Always finite, or a typed error."""
    resolved = resolve_args(name, args, log, channels, check_bounds=check_bounds)
    result = float(get(name).function(log, channels, resolved))
    if not math.isfinite(result):
        raise DiagnosticError(f"{name} produced a non-finite value: a catalogue bug")
    return result


def _factor(factor: float) -> float:
    if not (math.isfinite(factor) and factor > 0.0):
        raise DiagnosticArgumentError(f"time factor must be positive, got {factor}")
    return factor


def rescale_args(name: str, args: Args, factor: float) -> dict[str, ArgValue]:
    """The arguments that ask the same question of the log rescaled by ``t → c·t``.

    Explicit ``TIME`` arguments are multiplied by ``factor``; everything else is
    unchanged. Omitted arguments stay omitted (their τ̄-relative defaults rescale
    by themselves).
    """
    spec = get(name)
    c = _factor(factor)
    kinds = {a.name: a.kind for a in spec.args}
    out: dict[str, ArgValue] = {}
    for key, value in args.items():
        if kinds.get(key) is ArgKind.TIME and not isinstance(value, str):
            out[key] = float(value) * c
        else:
            out[key] = value
    return out


def transform_value(name: str, value: float, factor: float) -> float:
    """The value a diagnostic takes on the log rescaled by ``t → c·t``."""
    c = _factor(factor)
    match get(name).covariance:
        case Covariance.INVARIANT:
            return value
        case Covariance.INVERSE_TIME:
            return value / c
