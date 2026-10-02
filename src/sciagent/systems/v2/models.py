"""One interface for every fitted model a system can submit (SPEC §4.3).

The scorer reads three things from a submission's fitted model: its held-out
log-likelihood on fresh data from the truth (the predictive gap, total and per
counted event), and data simulated from it under an experiment (interventional
similarity). :class:`FittedModel` is that interface. Three implementations:

- :class:`GLMModel` wraps a grammar fit (:class:`~sciagent.glm.fit.FitResult`):
  held-out log L is :func:`~sciagent.glm.fit.evaluate_log_likelihood` at the
  fitted θ and ψ; experiments run on the GLM simulator through
  :func:`~sciagent.glm.interventions.run_experiment`. Every intervention is
  supported.
- :class:`LibraryFittedModel` wraps an out-of-grammar library fit
  (``sciagent.library``). Its intervention semantics are in
  ``library/mmpp.py`` and ``library/mixture.py``; every intervention is
  supported (forced events do not touch either model's dynamics).
- :class:`WienerHopfModel` is B-np: the Wiener-Hopf linear filter estimated on
  the training log (``nonparam/wiener_hopf.py``), used as a predictive model.

**B-np on a Dataset.** ``wiener_hopf.log_likelihood`` takes a bare
:class:`EventLog`, so :func:`wiener_hopf_log_likelihood` extends it to a
:class:`Dataset` with the GLM's conventions: every event (forced ones
included) is history for the filter; only counted events
(:func:`~sciagent.library.base.counted_mask`) contribute ``log λ``; the
excluded windows are removed from the compensator. λ is
``max(raw, floor)`` from the public :func:`wiener_hopf.intensity`, constant
between the breakpoints ``{t_k + e_j}`` and the window edges, so the
compensator is exact (midpoint per piece). The kernels are on absolute lags
(the estimate's ``edges`` are in the training data's time units), so the
estimate transfers to a new log unchanged.

**B-np simulation** is exact, not thinned: λ is piecewise constant, so between
consecutive change points (a kernel bin edge of some past event, a forced
time, a clamp edge, the horizon) a wait is drawn from Exp(λ) (one uniform), and
a wait that crosses the change point is discarded (memorylessness). λ is
recomputed at every change as ``max(fsum(μ, contributions), floor)``, where an
event's contribution in bin j is ``Σ_d w_d φ_d[j]`` with the estimator's driver
weights. Forced events and clamped events enter history and excite, as in the
GLM simulator. Marks: by default B-np resamples its training log's marks iid
(:class:`~sciagent.library.base.EmpiricalMarks`): it is model-free, so it does
not get the environment's mark law.

``n_params`` of B-np is ``D·L + 1`` (every bin of every driver's kernel plus
the baseline); it is reported, not used for selection.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import FitResult, evaluate_log_likelihood, to_coefficients
from sciagent.glm.grammar import ChannelKind, ChannelSpec
from sciagent.glm.interventions import Experiment, run_experiment, validate_experiment
from sciagent.glm.simulate import MarkSampler
from sciagent.library.base import (
    MAX_EVENTS,
    EmpiricalMarks,
    FittedLibraryModel,
    Schedule,
    check_datasets,
    clamp_at,
    counted_mask,
    event_marks,
    exponential,
    finish,
    n_counted,
)
from sciagent.nonparam import wiener_hopf
from sciagent.nonparam.wiener_hopf import (
    ARRIVAL,
    DEFAULT,
    KernelEstimate,
    WienerHopfConfig,
)

NONPARAM_NAME: Final = "wiener_hopf"


class ModelError(SciAgentError):
    """A fitted model cannot do what was asked."""


class FittedModel(Protocol):
    """What the scorer reads from any submitted model (module docstring)."""

    @property
    def name(self) -> str: ...

    @property
    def n_params(self) -> int: ...

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]:
        """Log-likelihood of each dataset at the fitted parameters."""
        ...

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        """The total over ``datasets`` (exactly folded)."""
        ...

    def held_out_per_event(self, datasets: Sequence[Dataset]) -> float:
        """The total divided by the number of counted events."""
        ...

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset:
        """Data from the model under ``experiment``, as ``run_experiment`` returns."""
        ...


def per_event(total: float, datasets: Sequence[Dataset]) -> float:
    """``total`` over the counted events of ``datasets``."""
    n = n_counted(datasets)
    if n == 0:
        raise ModelError("no counted events to normalise by")
    return total / n


# --------------------------------------------------------------------------
# GLM
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GLMModel:
    """A grammar fit as a :class:`FittedModel`; marks from the environment."""

    name: str
    fit: FitResult
    channels: tuple[ChannelSpec, ...]
    marks: MarkSampler

    @property
    def n_params(self) -> int:
        return self.fit.n_params

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]:
        return evaluate_log_likelihood(
            self.fit, check_datasets(datasets), self.channels
        )

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        return math.fsum(self.log_likelihoods(datasets))

    def held_out_per_event(self, datasets: Sequence[Dataset]) -> float:
        return per_event(self.held_out_log_likelihood(datasets), datasets)

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset:
        psi, coef = to_coefficients(self.fit)
        return run_experiment(
            self.fit.structure,
            psi,
            coef,
            self.channels,
            self.marks,
            experiment,
            rng,
            label=label,
        )


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryFittedModel:
    """An out-of-grammar library fit as a :class:`FittedModel`."""

    fitted: FittedLibraryModel
    channels: tuple[ChannelSpec, ...]
    marks: MarkSampler

    @property
    def name(self) -> str:
        return self.fitted.name

    @property
    def n_params(self) -> int:
        return self.fitted.n_params

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]:
        return self.fitted.log_likelihoods(datasets)

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        return self.fitted.held_out_log_likelihood(datasets)

    def held_out_per_event(self, datasets: Sequence[Dataset]) -> float:
        return per_event(self.held_out_log_likelihood(datasets), datasets)

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset:
        validate_experiment(experiment, self.channels)
        return self.fitted.simulate_experiment(
            experiment, rng, marks=self.marks, label=label
        )


# --------------------------------------------------------------------------
# Wiener-Hopf (B-np)
# --------------------------------------------------------------------------


def wiener_hopf_log_likelihood(
    est: KernelEstimate, data: Dataset, channels: tuple[ChannelSpec, ...]
) -> float:
    """``Σ_counted log λ(tᵢ) - ∫_observed λ`` under the filter (module docstring)."""
    log = data.log
    times, horizon = log.times, log.horizon
    keep = counted_mask(data)
    at_events = wiener_hopf.intensity(est, log, channels, times[keep])
    shifted = (times[:, None] + est.edges[None, :]).reshape(-1)
    edges = [x for w in data.excluded for x in w]
    breaks = np.unique(
        np.concatenate([[0.0, horizon], edges, shifted[shifted < horizon]])
    )
    mids = 0.5 * (breaks[:-1] + breaks[1:])
    widths = np.diff(breaks)
    observed = np.ones(mids.size, dtype=np.bool_)
    for a, b in data.excluded:
        observed &= ~((mids > a) & (mids < b))
    lam = wiener_hopf.intensity(est, log, channels, mids[observed])
    return reductions.total(np.log(at_events)) - reductions.dot(lam, widths[observed])


def _driver_weights(
    marks: Mapping[str, float], channels: tuple[ChannelSpec, ...]
) -> list[float]:
    """One event's driver weights, as ``wiener_hopf.driver_weights`` computes them."""
    out = [1.0]
    for spec in channels:
        m = marks[spec.name]
        out.append(
            m if spec.kind is ChannelKind.SIGN else (m - spec.location) / spec.scale
        )
    return out


@dataclass(frozen=True)
class WienerHopfModel:
    """B-np's predictive model: a Wiener-Hopf estimate plus a mark sampler."""

    estimate_: KernelEstimate
    channels: tuple[ChannelSpec, ...]
    marks: MarkSampler
    name: str = NONPARAM_NAME

    def __post_init__(self) -> None:
        expected = (ARRIVAL, *(c.name for c in self.channels))
        if self.estimate_.drivers != expected:
            raise ModelError(
                f"estimate drivers {self.estimate_.drivers} do not match {expected}"
            )

    @staticmethod
    def estimate(
        log: EventLog,
        channels: tuple[ChannelSpec, ...],
        *,
        config: WienerHopfConfig = DEFAULT,
    ) -> WienerHopfModel:
        """Estimate on ``log``; marks resample ``log``'s marks."""
        est = wiener_hopf.estimate_kernels(log, channels, config=config)
        marks = EmpiricalMarks.from_datasets([Dataset.observational(log)])
        return WienerHopfModel(est, channels, marks)

    @property
    def n_params(self) -> int:
        k = self.estimate_.kernels
        return int(k.shape[0] * k.shape[1]) + 1

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]:
        return tuple(
            wiener_hopf_log_likelihood(self.estimate_, d, self.channels)
            for d in check_datasets(datasets)
        )

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        return math.fsum(self.log_likelihoods(datasets))

    def held_out_per_event(self, datasets: Sequence[Dataset]) -> float:
        return per_event(self.held_out_log_likelihood(datasets), datasets)

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
        max_events: int = MAX_EVENTS,
    ) -> Dataset:
        validate_experiment(experiment, self.channels)
        return _simulate_filter(
            self.estimate_,
            self.channels,
            Schedule.of(experiment),
            rng,
            self.marks,
            label,
            max_events,
        )


def _simulate_filter(
    est: KernelEstimate,
    channels: tuple[ChannelSpec, ...],
    schedule: Schedule,
    rng: np.random.Generator,
    sampler: MarkSampler,
    label: str,
    max_events: int,
) -> Dataset:
    """Exact simulation of the piecewise-constant filter (module docstring)."""
    horizon = schedule.horizon
    edges = est.edges.tolist()
    n_bins = len(edges) - 1
    kernels = est.kernels
    clamp_edges = sorted(
        {c.start for c in schedule.clamps} | {c.end for c in schedule.clamps}
    )
    forced = schedule.forced

    times: list[float] = []
    marks: list[Mapping[str, float]] = []
    is_forced: list[bool] = []
    is_clamped: list[bool] = []
    values: list[list[float]] = []
    active: dict[int, float] = {}
    heap: list[tuple[float, int, int]] = []

    def add(t: float, m: Mapping[str, float], was_forced: bool, clamped: bool) -> None:
        k = len(times)
        if k >= max_events:
            raise ModelError(f"more than {max_events} events")
        times.append(t)
        marks.append(m)
        is_forced.append(was_forced)
        is_clamped.append(clamped)
        w = _driver_weights(m, channels)
        per_bin = [
            math.fsum(w[d] * float(kernels[d, j]) for d in range(len(w)))
            for j in range(n_bins)
        ]
        values.append(per_bin)
        active[k] = per_bin[0]
        for j in range(1, n_bins + 1):
            heapq.heappush(heap, (t + edges[j], k, j))

    t = 0.0
    fi = 0
    while True:
        clamp = clamp_at(schedule.clamps, t)
        if clamp is not None:
            rate = clamp.rate
        else:
            rate = max(math.fsum([est.baseline, *active.values()]), est.floor)
        nxt = horizon
        if heap:
            nxt = min(nxt, heap[0][0])
        if fi < len(forced):
            nxt = min(nxt, forced[fi].time)
        for e in clamp_edges:
            if e > t:
                nxt = min(nxt, e)
                break
        wait = exponential(rng, rate)
        if t + wait < nxt:
            t += wait
            m = event_marks(sampler, rng, t, schedule, None)
            add(t, m, False, clamp is not None)
            continue
        t = nxt
        while heap and heap[0][0] <= t:
            _, k, j = heapq.heappop(heap)
            if j == n_bins:
                del active[k]
            else:
                active[k] = values[k][j]
        while fi < len(forced) and forced[fi].time <= t:
            event = forced[fi]
            m = event_marks(sampler, rng, event.time, schedule, dict(event.marks))
            add(event.time, m, True, False)
            fi += 1
        if t >= horizon:
            break
    return finish(schedule, times, marks, is_forced, is_clamped, label)
