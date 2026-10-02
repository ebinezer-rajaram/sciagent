"""SPEC §4.3 score 3: interventional similarity on a fixed held-out battery.

v1's D3, kept: the submitted model and the truth are put through the same
interventions, and their responses are compared. It credits a structure that
is interventionally equivalent to the truth without being the truth (v1's R7).
Every choice below is fixed before the campaign (SPEC §6.1) and is a function
of the environment's channels only, never of the truth or of a submission.

**Timeline.** Each battery experiment is a fresh run on ``[0, H]``,
``H = BATTERY_HORIZON`` = 120 (native units, nominal mean rate 1, so about
120 events unintervened). Every time is a fraction of ``H``:

- ``[0, 0.4)`` warm-up, unintervened: the process leaves its empty-history
  start; ``[0.1, 0.4)`` is read as an observational segment;
- ``W = [0.4, 0.6)`` the intervention window (24 units);
- ``[0.4, 0.45)`` right after a burst at the start of W;
- ``[0.6, 0.65)`` the release, ``[0.6, 0.75)`` early after, ``[0.6, 1.0)``
  all after.

Windows are long enough (24 units) that a Hawkes-like process's own count
noise (Fano factor ≈ 1/(1-n)², about 11 at n = 0.7) does not swamp the
response; a 12-unit window was tried first and separated S11 from Hawkes by
only 0.36 on its strongest readout.

**The experiments** (:func:`standard_battery`), in this order:

1. ``burst``: :data:`BURST_SIZE` forced events at the start of W, spaced
   ``BURST_SPACING · H``, marks drawn from the environment's sampler. Read:
   the rate just after the burst, and ``mark_gap[c]`` on the observational
   segment.
2. For each channel c, in channel order, ``inject_<c>_high`` and
   ``inject_<c>_low``: every generated event in W gets mark c set to a high or
   low level (:func:`injection_levels`): ``±1`` for a sign channel;
   ``location ± scale`` for a real channel; ``location + scale`` and
   ``max(location - scale, location / 4)`` for a positive one (values must be
   > 0). Read: the rate in W and early after.
3. ``clamp_release``: λ clamped to :data:`CLAMP_RATE` on W, then released.
   Clamp events enter history, so their excitation shows after release. Read:
   the rate at release and early after, and the Fano factor after.
4. ``silence_release``: λ clamped to 0 on W (the history empties), then
   released. Read: the rate at release and early after.
5. ``censor_marked_burst``: W censored, plus the forced burst at the *end* of
   W with every channel at its high level: excitation by unseen events and by
   extreme marks, read at release and early after.

With pointproc's two channels that is 8 experiments.

**Event cap.** A battery run past :data:`BATTERY_MAX_EVENTS` (about 8x the
nominal count) is stopped and counts as *exploded*. Without the cap, one
explosive response (S11 under ``size = 2``: branching 1.4 for 24 units)
averaged 9,662 events and 1 s per replicate. The cap is applied by
:func:`simulate_capped` to the truth, to grammar fits and to B-np (the
simulators that can explode); see its docstring.

**Readouts.** Each readout is a catalogue diagnostic
(``sciagent.diagnostics.catalogue``) computed on one segment's *endogenous*
events (forced and clamped events are designs, not responses), re-based to
start at 0 with the segment's length as horizon. Time arguments are explicit
native values (:data:`FANO_WINDOW`), not the catalogue's log-relative
defaults, so the statistic is the same function for truth and model:

- ``rate_*``: ``mean_rate``, read as ``log1p(rate)`` (variance-stabilising, so
  an excited window's heavy tail does not dominate the Gaussian moments
  below). An empty segment has rate 0, not "undefined": a silent window is a
  response.
- ``fano_after``: ``fano_factor`` with windows of :data:`FANO_WINDOW`.
- ``mark_gap[c]``: ``mark_gap_correlation`` of channel c (the cross-mark
  signature).

A statistic the catalogue cannot compute on a replicate
(``InsufficientDataError``), and every statistic of a replicate whose
simulation is stopped (past the event cap, or a negative identity-link
intensity under the intervention), is *undefined* on that replicate.

**Discrepancy** (:func:`hellinger2`). Per readout, the R truth values and the
R model values are compared as distributions: a point mass on "undefined"
with weight p (truth) or q (model), and a Gaussian with the defined values'
mean and variance on the rest. The squared Hellinger distance between those
two mixtures is exact:

    H² = 1 - √(pq) - √((1-p)(1-q)) · BC,
    BC = √(2 s₁ s₂ / (s₁² + s₂²)) · exp(-(μ₁ - μ₂)² / (4(s₁² + s₂²))),

the Gaussians' Bhattacharyya coefficient. It lies in [0, 1], is 0 for
identical samples and 1 for disjoint ones, and is the same bounded metric
FSD uses (LOG 2026-10-01). Variances are sample variances (ddof 1; 0 for one
value) floored at ``(1e-9 · (1 + |μ₁| + |μ₂|))²`` so that two equal constants
give 0 and two different constants give 1. Under the null (model = truth)
its expectation is about ``1 / (4R)`` per readout from sampling noise alone,
about 0.01 at R = 32.

**Similarity** = 1 - the mean over experiments of the mean over that
experiment's readouts of H². Experiments weigh equally, whatever their
number of readouts.

**Randomness.** Replicate r of experiment j on side s (0 truth, 1 model) is
drawn from ``scorer_seed(BATTERY, truth_id, seed, s, j, r)``
(``scoring/seeds.py``): independent of each other, of the held-out stream and
of the investigation; the same truth draws for every system scored on a
(truth, seed), so similarity contrasts are paired.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from sciagent.core import reductions
from sciagent.diagnostics import catalogue
from sciagent.diagnostics.catalogue import ArgValue, InsufficientDataError
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import to_coefficients
from sciagent.glm.grammar import ChannelKind, ChannelSpec
from sciagent.glm.interventions import (
    Censor,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
    InjectMarks,
    Intervention,
    run_experiment,
)
from sciagent.glm.simulate import ExplosionError, NegativeIntensityError
from sciagent.library.base import LibrarySimulationError
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.seeds import BATTERY, generator, scorer_seed
from sciagent.scoring.truth import TruthModel, simulate_truth
from sciagent.systems.v2.models import GLMModel, ModelError, WienerHopfModel

#: Horizon of every battery experiment (native units).
BATTERY_HORIZON: Final = 120.0
#: Replicates per experiment and side.
REPLICATES: Final = 32
#: Event cap per battery run; a run past it is "exploded" (undefined readouts).
BATTERY_MAX_EVENTS: Final = 1_000
#: Segments, as fractions of the horizon.
OBSERVATIONAL_SEGMENT: Final = (0.1, 0.4)
INTERVENTION_WINDOW: Final = (0.4, 0.6)
POST_BURST: Final = (0.4, 0.45)
RELEASE: Final = (0.6, 0.65)
EARLY_AFTER: Final = (0.6, 0.75)
AFTER: Final = (0.6, 1.0)
#: Forced events in a burst, and their spacing as a fraction of the horizon.
BURST_SIZE: Final = 10
BURST_SPACING: Final = 0.002
#: Clamped rate of ``clamp_release`` (native units: 4 x the nominal mean rate).
CLAMP_RATE: Final = 4.0
#: Counting window of the ``fano`` readout (native units: 2 nominal mean gaps).
FANO_WINDOW: Final = 2.0
#: Positive-channel low level is at least ``location / LOW_DIVISOR``.
LOW_DIVISOR: Final = 4.0
#: Relative variance floor of :func:`hellinger2`.
_FLOOR: Final = 1e-9

_RATE: Final = "mean_rate"
_FANO: Final = "fano_factor"
_MARK_GAP: Final = "mark_gap_correlation"


class Simulator(Protocol):
    """What the battery reads from a model or a truth: data under an experiment."""

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset: ...


@dataclass(frozen=True)
class Readout:
    """One statistic of one segment (module docstring).

    ``args`` are the diagnostic's explicit arguments, sorted by name;
    ``log1p`` reads the value as ``log(1 + value)``.
    """

    name: str
    diagnostic: str
    segment: tuple[float, float]
    args: tuple[tuple[str, ArgValue], ...] = ()
    log1p: bool = False


@dataclass(frozen=True)
class BatteryExperiment:
    """A named experiment of the battery and the readouts it is scored on."""

    name: str
    experiment: Experiment
    readouts: tuple[Readout, ...]


# --------------------------------------------------------------------------
# The battery
# --------------------------------------------------------------------------


def injection_levels(spec: ChannelSpec) -> tuple[float, float]:
    """``(high, low)`` injected values for a channel (module docstring)."""
    match spec.kind:
        case ChannelKind.SIGN:
            return 1.0, -1.0
        case ChannelKind.REAL:
            return spec.location + spec.scale, spec.location - spec.scale
        case ChannelKind.POSITIVE:
            low = max(spec.location - spec.scale, spec.location / LOW_DIVISOR)
            if not low > 0.0:
                raise ScoringError(
                    f"channel {spec.name!r} has no positive low level "
                    f"(location {spec.location}, scale {spec.scale})"
                )
            return spec.location + spec.scale, low


def _rate(name: str, segment: tuple[float, float]) -> Readout:
    return Readout(name, _RATE, segment, (), log1p=True)


def _window(h: float, fraction: tuple[float, float]) -> tuple[float, float]:
    return (fraction[0] * h, fraction[1] * h)


def standard_battery(
    channels: tuple[ChannelSpec, ...], *, horizon: float = BATTERY_HORIZON
) -> tuple[BatteryExperiment, ...]:
    """The preregistered battery for an environment's channels (module docstring)."""
    if not channels:
        raise ScoringError("the battery needs at least one mark channel")
    h = horizon
    w = _window(h, INTERVENTION_WINDOW)
    spacing = BURST_SPACING * h
    burst = tuple(w[0] + k * spacing for k in range(BURST_SIZE))
    late_burst = tuple(w[1] - (BURST_SIZE - k) * spacing for k in range(BURST_SIZE))
    post_burst = _rate("rate_post_burst", POST_BURST)
    during = _rate("rate_window", INTERVENTION_WINDOW)
    release = _rate("rate_release", RELEASE)
    early = _rate("rate_early_after", EARLY_AFTER)
    mark_gap = tuple(
        Readout(
            f"mark_gap[{c.name}]",
            _MARK_GAP,
            OBSERVATIONAL_SEGMENT,
            (("channel", c.name),),
        )
        for c in channels
    )

    def experiment(intervention: Intervention) -> Experiment:
        return Experiment(intervention, h)

    out: list[BatteryExperiment] = [
        BatteryExperiment(
            "burst", experiment(ForceEvents(burst)), (post_burst, *mark_gap)
        )
    ]
    for c in channels:
        high, low = injection_levels(c)
        for level, value in (("high", high), ("low", low)):
            out.append(
                BatteryExperiment(
                    f"inject_{c.name}_{level}",
                    experiment(InjectMarks(w, c.name, value)),
                    (during, early),
                )
            )
    out.append(
        BatteryExperiment(
            "clamp_release",
            experiment(ClampRate(w, CLAMP_RATE)),
            (
                release,
                early,
                Readout("fano_after", _FANO, AFTER, (("window", FANO_WINDOW),)),
            ),
        )
    )
    out.append(
        BatteryExperiment(
            "silence_release", experiment(ClampRate(w, 0.0)), (release, early)
        )
    )
    marks = {c.name: (injection_levels(c)[0],) * BURST_SIZE for c in channels}
    out.append(
        BatteryExperiment(
            "censor_marked_burst",
            experiment(Compose((Censor(w), ForceEvents(late_burst, marks)))),
            (release, early),
        )
    )
    return tuple(out)


# --------------------------------------------------------------------------
# Readouts
# --------------------------------------------------------------------------


def segment_log(data: Dataset, segment: tuple[float, float]) -> EventLog:
    """The endogenous events of ``segment`` (fractions), re-based to start at 0."""
    horizon = data.log.horizon
    start, end = segment[0] * horizon, segment[1] * horizon
    times = data.log.times
    keep = data.endogenous & (times >= start) & (times < end)
    return EventLog.create(
        times[keep] - start,
        {name: values[keep] for name, values in data.log.marks.items()},
        end - start,
    )


def readout_value(
    readout: Readout, data: Dataset, channels: tuple[ChannelSpec, ...]
) -> float | None:
    """The readout on one replicate's data; None when undefined."""
    log = segment_log(data, readout.segment)
    if readout.diagnostic == _RATE and log.n == 0:
        value = 0.0
    else:
        try:
            value = catalogue.compute(
                readout.diagnostic,
                log,
                channels,
                dict(readout.args),
                check_bounds=False,
            )
        except InsufficientDataError:
            return None
    return math.log1p(value) if readout.log1p else value


# --------------------------------------------------------------------------
# Discrepancy
# --------------------------------------------------------------------------


def _moments(values: list[float]) -> tuple[float, float]:
    x = np.asarray(values, dtype=np.float64)
    mean = reductions.mean(x)
    var = reductions.variance(x) if x.size > 1 else 0.0
    return mean, var


def hellinger2(a: Sequence[float | None], b: Sequence[float | None]) -> float:
    """Squared Hellinger distance of the atom-plus-Gaussian fits (module docstring)."""
    if not a or not b:
        raise ScoringError("hellinger2 needs at least one value on each side")
    da = [v for v in a if v is not None]
    db = [v for v in b if v is not None]
    if not all(math.isfinite(v) for v in (*da, *db)):
        raise ScoringError("readout values must be finite or None")
    p = (len(a) - len(da)) / len(a)
    q = (len(b) - len(db)) / len(b)
    bc = 0.0
    if da and db:
        m1, v1 = _moments(da)
        m2, v2 = _moments(db)
        floor = (_FLOOR * (1.0 + abs(m1) + abs(m2))) ** 2
        v1, v2 = max(v1, floor), max(v2, floor)
        total = v1 + v2
        bc = math.sqrt(2.0 * math.sqrt(v1 * v2) / total) * math.exp(
            -((m1 - m2) ** 2) / (4.0 * total)
        )
    overlap = math.sqrt(p * q) + math.sqrt((1.0 - p) * (1.0 - q)) * bc
    return min(1.0, max(0.0, 1.0 - overlap))


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadoutDiscrepancy:
    """H² of one readout, with each side's share of undefined replicates."""

    name: str
    discrepancy: float
    truth_undefined: float
    model_undefined: float


@dataclass(frozen=True)
class ExperimentDiscrepancy:
    """Mean H² over an experiment's readouts."""

    name: str
    discrepancy: float
    readouts: tuple[ReadoutDiscrepancy, ...]


@dataclass(frozen=True)
class SimilarityResult:
    """Score 3: ``1 - mean discrepancy``, with the per-experiment breakdown."""

    similarity: float
    experiments: tuple[ExperimentDiscrepancy, ...]
    replicates: int


def simulate_capped(
    simulator: Simulator,
    experiment: Experiment,
    rng: np.random.Generator,
    label: str,
    max_events: int = BATTERY_MAX_EVENTS,
) -> Dataset:
    """One battery run, stopped past ``max_events`` (module docstring).

    The cap reaches the simulators that can explode: the truth, a grammar fit
    (run exactly as ``GLMModel.simulate_experiment`` runs it, with the cap
    added) and B-np's filter. Library models (MMPP, renewal mixture) have no
    self-excitation and run through their own method.
    """
    if isinstance(simulator, TruthModel):
        return simulate_truth(
            simulator.truth, experiment, rng, label=label, max_events=max_events
        )
    if isinstance(simulator, GLMModel):
        psi, coef = to_coefficients(simulator.fit)
        return run_experiment(
            simulator.fit.structure,
            psi,
            coef,
            simulator.channels,
            simulator.marks,
            experiment,
            rng,
            max_events=max_events,
            label=label,
        )
    if isinstance(simulator, WienerHopfModel):
        return simulator.simulate_experiment(
            experiment, rng, label=label, max_events=max_events
        )
    return simulator.simulate_experiment(experiment, rng, label=label)


def _replicates(
    simulator: Simulator,
    battery_experiment: BatteryExperiment,
    channels: tuple[ChannelSpec, ...],
    truth_id: str,
    seed: int,
    side: int,
    index: int,
    replicates: int,
) -> list[list[float | None]]:
    """``values[k][r]``: readout k on replicate r."""
    values: list[list[float | None]] = [[] for _ in battery_experiment.readouts]
    for r in range(replicates):
        rng = generator(scorer_seed(BATTERY, truth_id, seed, side, index, r))
        try:
            data: Dataset | None = simulate_capped(
                simulator,
                battery_experiment.experiment,
                rng,
                battery_experiment.name,
            )
        except (
            ExplosionError,
            NegativeIntensityError,
            LibrarySimulationError,
            ModelError,
        ):
            data = None
        for k, readout in enumerate(battery_experiment.readouts):
            values[k].append(
                None if data is None else readout_value(readout, data, channels)
            )
    return values


def interventional_similarity(
    truth: Simulator,
    model: Simulator,
    channels: tuple[ChannelSpec, ...],
    truth_id: str,
    seed: int,
    *,
    replicates: int = REPLICATES,
    battery: Sequence[BatteryExperiment] | None = None,
) -> SimilarityResult:
    """Score 3 of ``model`` against ``truth`` on the battery (module docstring)."""
    if replicates < 2:
        raise ScoringError(f"need at least 2 replicates, got {replicates}")
    experiments = tuple(battery) if battery is not None else standard_battery(channels)
    if not experiments:
        raise ScoringError("the battery is empty")
    results: list[ExperimentDiscrepancy] = []
    for j, be in enumerate(experiments):
        if not be.readouts:
            raise ScoringError(f"battery experiment {be.name!r} has no readouts")
        t = _replicates(truth, be, channels, truth_id, seed, 0, j, replicates)
        m = _replicates(model, be, channels, truth_id, seed, 1, j, replicates)
        per = tuple(
            ReadoutDiscrepancy(
                readout.name,
                hellinger2(tv, mv),
                sum(v is None for v in tv) / replicates,
                sum(v is None for v in mv) / replicates,
            )
            for readout, tv, mv in zip(be.readouts, t, m, strict=True)
        )
        mean = reductions.mean(np.array([r.discrepancy for r in per], dtype=np.float64))
        results.append(ExperimentDiscrepancy(be.name, mean, per))
    overall = reductions.mean(
        np.array([e.discrepancy for e in results], dtype=np.float64)
    )
    return SimilarityResult(1.0 - overall, tuple(results), replicates)
