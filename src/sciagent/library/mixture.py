"""Two-component Poisson mixture: v1's Poisson mixture as a renewal process (SPEC §2.1).

Every inter-arrival gap is drawn iid from the hyperexponential density
``f(g) = w λ_h e^{-λ_h g} + (1 - w) λ_l e^{-λ_l g}`` (``w = weight_high``), with
survival ``S(g) = w e^{-λ_h g} + (1 - w) e^{-λ_l g}``: the rate is redrawn at
every event, as v1's ``arrival_mixture_of_poisson_2``. The process starts with a
renewal at time 0 (v1's first gap is measured from 0), so it is an *ordinary*,
not an equilibrium, renewal process.

**Likelihood** (decided here; LOG-worthy).

- *Observational data* is exact: ``Σ log f(gap) + log S(T - t_n)``, the first
  gap measured from 0 and the last one censored at the horizon.
- *Forced events* are ignored: they are not renewal points. v1 defines the
  mixture as having "no response to a forced arrival", so the renewal clock
  runs from the previous endogenous event straight through a forced one.
- *Excluded windows* break the renewal chain: what happened inside one (hidden
  events under censoring, a replaced process under a clamp) is unknown, so the
  age of the process when the window ends is unknown. The likelihood is
  therefore **conditional**: the observation is split into segments between
  the (closed) windows; in each segment, gaps between consecutive counted
  events contribute ``log f``, and the stretch from the last counted event to
  the segment's end contributes ``log S`` (no event was observed there). The
  first counted event of a segment that begins at a window's end is
  conditioned on (it contributes nothing), and such a segment with no counted
  event contributes nothing. The segment that begins at 0 has its first gap
  from 0 (``log f``, or ``log S`` up to its end if it has no event). So every
  gap that crosses a window is dropped. This is not the full likelihood (that
  would need the age distribution at the window's end), and it conditions on
  more than the GLM likelihood does; per-event comparisons on data with
  windows should keep that in mind. Observational held-out data has none.

**Fit.** ``x = (log λ_l, log(λ_h - λ_l), logit w)`` (λ_h > λ_l identifies the
labels), L-BFGS-B in a box around the counted-event rate r̄ from a fixed grid
of 6 starts (:data:`_WEIGHT_STARTS` by :data:`_RATIO_STARTS`), best start
wins, ties to the lowest index. 3 parameters.

**Simulation.** A gap takes two uniforms: one picks the component (high with
probability w), one is the exponential. Under an experiment: censoring hides
events but the renewal process runs on; a clamp window replaces the process by
Poisson(c) events, the pending renewal is abandoned at the clamp's start and
the process restarts with a renewal at the clamp's end; forced events do not
touch the clock.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Final

import numpy as np

from sciagent.core import reductions
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.interventions import Experiment
from sciagent.glm.simulate import MarkSampler, RateClamp
from sciagent.library.base import (
    MAX_EVENTS,
    Bools,
    EmpiricalMarks,
    LibraryInputError,
    LibrarySimulationError,
    Schedule,
    check_datasets,
    counted_mask,
    exponential,
    multistart,
    n_counted,
    no_marks,
    observed_time,
    run_arrival_experiment,
)

NAME: Final = "poisson_mixture"
N_PARAMS: Final = 3
#: Starts: weight_high by (rate_high / rate_low), with the mean gap set to 1/r̄.
_WEIGHT_STARTS: Final = (0.2, 0.5, 0.8)
_RATIO_STARTS: Final = (4.0, 50.0)
_LOG_LO: Final = -12.0
_LOG_HI: Final = 8.0
_LOGIT_BOX: Final = 15.0


@dataclass(frozen=True)
class MixtureParams:
    """The two rates (> 0) and the high component's weight (in [0, 1])."""

    rate_low: float
    rate_high: float
    weight_high: float

    def __post_init__(self) -> None:
        for name in ("rate_low", "rate_high"):
            v = getattr(self, name)
            if not (math.isfinite(v) and v > 0.0):
                raise LibraryInputError(f"{name} must be positive and finite: {v}")
        if not 0.0 <= self.weight_high <= 1.0:
            raise LibraryInputError(
                f"weight_high must be in [0, 1]: {self.weight_high}"
            )

    @property
    def mean_gap(self) -> float:
        w = self.weight_high
        return w / self.rate_high + (1.0 - w) / self.rate_low


# --------------------------------------------------------------------------
# Likelihood
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Gaps:
    """Complete gaps (density terms) and censored gaps (survival terms)."""

    complete: Floats
    censored: Floats


def _gaps(data: Dataset) -> _Gaps:
    times = data.log.times[counted_mask(data)]
    windows = data.excluded
    # Counted events are outside every closed window, so the number of window
    # starts before an event is the index of its observed segment.
    segment = np.searchsorted(np.array([a for a, _ in windows]), times, side="left")
    complete: list[float] = []
    censored: list[float] = []
    for k in range(len(windows) + 1):
        start = 0.0 if k == 0 else windows[k - 1][1]
        end = windows[k][0] if k < len(windows) else data.log.horizon
        events = times[segment == k].tolist()
        if k == 0:
            # The ordinary renewal from 0: every gap and the tail are known.
            events = [0.0, *events]
        elif not events:
            continue
        complete.extend(y - x for x, y in pairwise(events))
        if end > start:
            censored.append(end - events[-1])
    return _Gaps(np.array(complete), np.array(censored))


def _log_mix(p: MixtureParams, g: Floats, *, density: bool) -> Floats:
    """``log f(g)`` or ``log S(g)`` by log-sum-exp; a zero weight drops its term."""
    terms = []
    for w, rate in ((p.weight_high, p.rate_high), (1.0 - p.weight_high, p.rate_low)):
        if w > 0.0:
            base = math.log(w) + (math.log(rate) if density else 0.0)
            terms.append(base - rate * g)
    if len(terms) == 1:
        out: Floats = terms[0]
        return out
    out = np.logaddexp(terms[0], terms[1])
    return out


def _log_likelihood(p: MixtureParams, gaps: _Gaps) -> float:
    return math.fsum(
        [
            reductions.total(_log_mix(p, gaps.complete, density=True)),
            reductions.total(_log_mix(p, gaps.censored, density=False)),
        ]
    )


def mixture_log_likelihood(
    params: MixtureParams, datasets: Sequence[Dataset]
) -> tuple[float, ...]:
    """Each dataset's (conditional, with windows) log-likelihood; module docstring."""
    return tuple(_log_likelihood(params, _gaps(d)) for d in check_datasets(datasets))


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def _gap(p: MixtureParams, rng: np.random.Generator) -> float:
    rate = p.rate_high if rng.random() < p.weight_high else p.rate_low
    return exponential(rng, rate)


def _generate(
    params: MixtureParams,
    horizon: float,
    clamps: tuple[RateClamp, ...],
    rng: np.random.Generator,
    max_events: int,
) -> tuple[Floats, Bools]:
    if not (math.isfinite(horizon) and horizon > 0.0):
        raise LibraryInputError(f"horizon must be positive and finite: {horizon}")
    segments: list[tuple[float, float, float | None]] = []
    pos = 0.0
    for c in clamps:
        if c.start > pos:
            segments.append((pos, c.start, None))
        segments.append((c.start, c.end, c.rate))
        pos = c.end
    if horizon > pos:
        segments.append((pos, horizon, None))
    times: list[float] = []
    clamped: list[bool] = []
    for start, end, rate in segments:
        t = start
        while True:
            t += _gap(params, rng) if rate is None else exponential(rng, rate)
            if t >= end:
                break
            times.append(t)
            clamped.append(rate is not None)
            if len(times) > max_events:
                raise LibrarySimulationError(f"more than {max_events} events")
    return np.array(times, dtype=np.float64), np.array(clamped, dtype=np.bool_)


def simulate_mixture(
    params: MixtureParams,
    horizon: float,
    rng: np.random.Generator,
    *,
    marks: MarkSampler | None = None,
    max_events: int = MAX_EVENTS,
) -> EventLog:
    """An unintervened log on ``[0, horizon]`` (no channels if ``marks`` is None)."""
    return simulate_mixture_experiment(
        params,
        Schedule.empty(horizon),
        rng,
        marks=marks or no_marks,
        max_events=max_events,
    ).log


def simulate_mixture_experiment(
    params: MixtureParams,
    schedule: Schedule,
    rng: np.random.Generator,
    *,
    marks: MarkSampler,
    label: str = "experiment",
    max_events: int = MAX_EVENTS,
) -> Dataset:
    """A run under ``schedule`` (draw order: ``base`` and this module's docstring)."""

    def generate(
        horizon: float, clamps: tuple[RateClamp, ...], r: np.random.Generator
    ) -> tuple[Floats, Bools]:
        return _generate(params, horizon, clamps, r, max_events)

    return run_arrival_experiment(generate, schedule, rng, marks, label)


# --------------------------------------------------------------------------
# Fit
# --------------------------------------------------------------------------


def _params(x: Sequence[float]) -> MixtureParams:
    low = math.exp(x[0])
    weight = 1.0 / (1.0 + math.exp(-x[2]))
    return MixtureParams(low, low + math.exp(x[1]), weight)


@dataclass(frozen=True)
class FittedPoissonMixture2:
    """A fitted Poisson mixture. Equality compares every field but ``marks``."""

    params: MixtureParams
    log_likelihood: float
    log_likelihood_per_dataset: tuple[float, ...]
    n_events: int
    n_starts: int
    n_converged: int
    best_start: int
    marks: EmpiricalMarks = field(compare=False, repr=False)
    name: str = NAME
    n_params: int = N_PARAMS

    @property
    def bic(self) -> float:
        return (
            self.n_params * math.log(max(self.n_events, 1)) - 2.0 * self.log_likelihood
        )

    @property
    def aic(self) -> float:
        return 2.0 * self.n_params - 2.0 * self.log_likelihood

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]:
        return mixture_log_likelihood(self.params, datasets)

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        return math.fsum(self.log_likelihoods(datasets))

    def simulate(
        self,
        horizon: float,
        rng: np.random.Generator,
        *,
        marks: MarkSampler | None = None,
    ) -> EventLog:
        return simulate_mixture(self.params, horizon, rng, marks=marks or self.marks)

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        marks: MarkSampler | None = None,
        label: str = "experiment",
    ) -> Dataset:
        """Run ``experiment`` (not validated here; the caller has the channels)."""
        return simulate_mixture_experiment(
            self.params,
            Schedule.of(experiment),
            rng,
            marks=marks or self.marks,
            label=label,
        )


@dataclass(frozen=True)
class PoissonMixture2:
    """The Poisson-mixture library member (``LibraryModel``)."""

    name: str = NAME

    def fit(self, datasets: Sequence[Dataset]) -> FittedPoissonMixture2:
        data = check_datasets(datasets)
        gaps = [_gaps(d) for d in data]
        n = n_counted(data)
        exposure = math.fsum(observed_time(d) for d in data)
        if n < 2 or exposure <= 0.0:
            raise LibraryInputError(f"need ≥ 2 counted events to fit; have {n}")
        r = n / exposure
        log_r = math.log(r)

        def objective(x: Floats) -> float:
            p = _params(x.tolist())
            return -math.fsum(_log_likelihood(p, g) for g in gaps) / n

        starts: list[tuple[float, ...]] = []
        for w in _WEIGHT_STARTS:
            for ratio in _RATIO_STARTS:
                # Mean gap w/(ratio·low) + (1-w)/low = 1/r.
                low = r * (w / ratio + (1.0 - w))
                starts.append(
                    (
                        math.log(low),
                        math.log(low * (ratio - 1.0)),
                        math.log(w / (1 - w)),
                    )
                )
        box = (log_r + _LOG_LO, log_r + _LOG_HI)
        logit = (-_LOGIT_BOX, _LOGIT_BOX)
        res = multistart(objective, starts, [box, box, logit])
        params = _params(res.x)
        per = mixture_log_likelihood(params, data)
        return FittedPoissonMixture2(
            params=params,
            log_likelihood=math.fsum(per),
            log_likelihood_per_dataset=per,
            n_events=n,
            n_starts=res.n_starts,
            n_converged=res.n_converged,
            best_start=res.start_index,
            marks=EmpiricalMarks.from_datasets(data),
        )
