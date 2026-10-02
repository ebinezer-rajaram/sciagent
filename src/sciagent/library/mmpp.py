"""Two-state Markov-modulated Poisson process: v1's regime switching (SPEC §2.1).

A hidden chain X(t) ∈ {low, high} switches low → high at rate ``switch_up``
and high → low at ``switch_down``; arrivals are Poisson at ``rate_low`` or
``rate_high`` given the state. Each dataset's chain starts from its stationary
law ``π = (switch_down, switch_up) / (switch_up + switch_down)`` at time 0. This
is v1's ``arrival_poisson_modulated_2state`` with ``rate_k = rate · mult_k``,
``switch_up = switch_rate · p_high``, ``switch_down = switch_rate · (1 - p_high)``.

**Likelihood** (exact; the forward algorithm on the continuous-time chain).
With generator Q and ``Λ = diag(rate_low, rate_high)``,
``L = π · Π_steps M_step · 1`` where an observed stretch of length τ ending in a
counted event is ``exp((Q - Λ) τ) Λ``, one ending without an event (at a
window start or the horizon) is ``exp((Q - Λ) τ)``, and an excluded window of
length τ is ``exp(Q τ)``: the chain runs on, but nothing about arrivals is
observed there. That is exact for censoring (hidden arrivals do not affect the
chain) and for clamps (``do(λ = c)`` replaces the arrival rate, not the chain).
Forced events carry no information about λ and do not touch the chain, so
they are ignored. The counting rule is :func:`~sciagent.library.base.counted_mask`.

The 2x2 exponential is in closed form. For ``A = [[a, b], [c, d]]`` with
``b, c > 0``, ``m = (a + d)/2``, ``h = (a - d)/2``, ``s = √(h² + bc)``:
``exp(Aτ) = e^{(m+s)τ} [ (1 + e^{-2sτ})/2 · I + g · (A - mI) ]`` with
``g = -expm1(-2sτ) / (2s)``. Every entry is written as a sum of non-negative
terms (the smaller diagonal entry uses ``s - |h| = bc / (s + |h|)``), so no
entry suffers cancellation or goes negative; ``e^{(m+s)τ}`` is kept as a log
scale. The step matrices are multiplied by a fixed pairwise tree (independent
of parameters), each product renormalised by its largest entry with the logs
summed exactly (``reductions.total``), so the value is deterministic.

**Fit.** Unconstrained ``x = (log rate_low, log(rate_high - rate_low),
log switch_up, log switch_down)`` (so ``rate_high > rate_low``: the labels are
identified), L-BFGS-B in a box around the counted-event rate r̄ of the data,
from a fixed grid of 9 starts (rate ratios by switching time scales, see
:data:`_RATE_STARTS`, :data:`_SWITCH_STARTS`); the best start wins, ties to
the lowest index. 4 parameters.

**Simulation** by competing exponentials: in state k with arrival rate r (λ_k,
or a clamp's rate inside a clamp window) and leaving rate q_k, draw a wait
from Exp(r + q_k) (one uniform), then one uniform to choose arrival vs switch.
A wait that crosses a clamp boundary is discarded and the clock restarts there
(exact by memorylessness). The initial state takes one uniform first.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
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
    clamp_at,
    counted_mask,
    exponential,
    multistart,
    n_counted,
    no_marks,
    observed_time,
    run_arrival_experiment,
)

NAME: Final = "regime_switching"
N_PARAMS: Final = 4
#: Starts: (rate_low / r̄, rate_high / r̄) by switching rate (switch_up +
#: switch_down) / r̄, with the high state's stationary share fixed at 0.3.
_RATE_STARTS: Final = ((0.5, 2.0), (0.2, 3.0), (0.05, 10.0))
_SWITCH_STARTS: Final = (0.01, 0.1, 1.0)
_HIGH_SHARE: Final = 0.3
#: Box half-widths (in log units) around log r̄.
_LOG_LO: Final = -12.0
_LOG_HI: Final = 8.0


@dataclass(frozen=True)
class MMPP2Params:
    """Arrival rates in the two states and the switching rates (all > 0)."""

    rate_low: float
    rate_high: float
    switch_up: float
    switch_down: float

    def __post_init__(self) -> None:
        for name in ("rate_low", "rate_high", "switch_up", "switch_down"):
            v = getattr(self, name)
            if not (math.isfinite(v) and v > 0.0):
                raise LibraryInputError(f"{name} must be positive and finite: {v}")

    @property
    def stationary_high(self) -> float:
        return self.switch_up / (self.switch_up + self.switch_down)

    @property
    def mean_rate(self) -> float:
        p = self.stationary_high
        return (1.0 - p) * self.rate_low + p * self.rate_high


# --------------------------------------------------------------------------
# Likelihood
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Steps:
    """A dataset as steps: duration, hidden (excluded) flag, ends in an event."""

    tau: Floats
    hidden: Bools
    event: Bools


def _steps(data: Dataset) -> _Steps:
    counted = data.log.times[counted_mask(data)].tolist()
    tau: list[float] = []
    hidden: list[bool] = []
    event: list[bool] = []
    pos = 0.0
    windows = list(data.excluded)
    w = 0

    def window(a: float, b: float) -> None:
        nonlocal pos
        tau.extend((a - pos, b - a))
        hidden.extend((False, True))
        event.extend((False, False))
        pos = b

    for t in counted:
        while w < len(windows) and windows[w][0] < t:
            window(*windows[w])
            w += 1
        tau.append(t - pos)
        hidden.append(False)
        event.append(True)
        pos = t
    while w < len(windows):
        window(*windows[w])
        w += 1
    tau.append(data.log.horizon - pos)
    hidden.append(False)
    event.append(False)
    return _Steps(
        np.array(tau, dtype=np.float64),
        np.array(hidden, dtype=np.bool_),
        np.array(event, dtype=np.bool_),
    )


def _expm_steps(
    a: Floats, b: float, c: float, d: Floats, tau: Floats
) -> tuple[Floats, Floats, Floats, Floats, Floats]:
    """``exp(Aτ)`` entrywise for ``A = [[a, b], [c, d]]`` (b, c > 0), and log scale."""
    m = 0.5 * (a + d)
    h = 0.5 * (a - d)
    habs = np.abs(h)
    bc = b * c
    s = np.sqrt(h * h + bc)
    e = np.exp(-2.0 * s * tau)
    g = -np.expm1(-2.0 * s * tau) / (2.0 * s)
    big = 0.5 * (1.0 + e) + g * habs
    # (1 + e)/2 - g|h| = ((s - |h|) + e (s + |h|)) / (2s), s - |h| = bc/(s + |h|).
    small = (bc / (s + habs) + e * (s + habs)) / (2.0 * s)
    m00 = np.where(h >= 0.0, big, small)
    m11 = np.where(h >= 0.0, small, big)
    return m00, g * b, g * c, m11, (m + s) * tau


def _chain_log(
    m00: Floats, m01: Floats, m10: Floats, m11: Floats, pi0: float, pi1: float
) -> float:
    """``log(π · Π M · 1)`` by a fixed pairwise tree with exact log-scale sums."""
    logs: list[float] = []
    while m00.size > 1:
        if m00.size % 2:
            m00 = np.append(m00, 1.0)
            m01 = np.append(m01, 0.0)
            m10 = np.append(m10, 0.0)
            m11 = np.append(m11, 1.0)
        a00, a01, a10, a11 = m00[0::2], m01[0::2], m10[0::2], m11[0::2]
        b00, b01, b10, b11 = m00[1::2], m01[1::2], m10[1::2], m11[1::2]
        p00 = a00 * b00 + a01 * b10
        p01 = a00 * b01 + a01 * b11
        p10 = a10 * b00 + a11 * b10
        p11 = a10 * b01 + a11 * b11
        scale = np.maximum(np.maximum(p00, p01), np.maximum(p10, p11))
        if np.any(scale <= 0.0):
            return -math.inf
        logs.append(reductions.total(np.log(scale)))
        m00, m01, m10, m11 = p00 / scale, p01 / scale, p10 / scale, p11 / scale
    final = pi0 * (m00[0] + m01[0]) + pi1 * (m10[0] + m11[0])
    if final <= 0.0:
        return -math.inf
    logs.append(math.log(final))
    return math.fsum(logs)


def _log_likelihood(p: MMPP2Params, steps: _Steps) -> float:
    up, down = p.switch_up, p.switch_down
    hid = steps.hidden
    a = np.where(hid, -up, -up - p.rate_low)
    d = np.where(hid, -down, -down - p.rate_high)
    m00, m01, m10, m11, logs = _expm_steps(a, up, down, d, steps.tau)
    ev = steps.event
    # Right-multiplying by Λ scales column 0 by rate_low and column 1 by rate_high.
    col0 = np.where(ev, p.rate_low, 1.0)
    col1 = np.where(ev, p.rate_high, 1.0)
    m00, m10 = m00 * col0, m10 * col0
    m01, m11 = m01 * col1, m11 * col1
    scale = np.maximum(np.maximum(m00, m01), np.maximum(m10, m11))
    if np.any(scale <= 0.0):
        return -math.inf
    total = reductions.total(logs) + reductions.total(np.log(scale))
    pi1 = p.stationary_high
    chain = _chain_log(
        m00 / scale, m01 / scale, m10 / scale, m11 / scale, 1.0 - pi1, pi1
    )
    return math.fsum([total, chain])


def mmpp2_log_likelihood(
    params: MMPP2Params, datasets: Sequence[Dataset]
) -> tuple[float, ...]:
    """The exact log-likelihood of each dataset (module docstring)."""
    return tuple(_log_likelihood(params, _steps(d)) for d in check_datasets(datasets))


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def _generate(
    params: MMPP2Params,
    horizon: float,
    clamps: tuple[RateClamp, ...],
    rng: np.random.Generator,
    max_events: int,
) -> tuple[Floats, Bools]:
    if not (math.isfinite(horizon) and horizon > 0.0):
        raise LibraryInputError(f"horizon must be positive and finite: {horizon}")
    edges = sorted({c.start for c in clamps} | {c.end for c in clamps} | {horizon})
    edges = [e for e in edges if 0.0 < e <= horizon]
    high = rng.random() < params.stationary_high
    times: list[float] = []
    clamped: list[bool] = []
    t = 0.0
    k = 0
    while True:
        while edges[k] <= t:
            k += 1
        seg_end = edges[k]
        clamp = clamp_at(clamps, t)
        rate = (
            clamp.rate
            if clamp is not None
            else (params.rate_high if high else params.rate_low)
        )
        leave = params.switch_down if high else params.switch_up
        total = rate + leave
        wait = exponential(rng, total)
        if t + wait >= seg_end:
            t = seg_end
            if t >= horizon:
                break
            continue
        t += wait
        if rng.random() * total < rate:
            times.append(t)
            clamped.append(clamp is not None)
            if len(times) > max_events:
                raise LibrarySimulationError(f"more than {max_events} events")
        else:
            high = not high
    return np.array(times, dtype=np.float64), np.array(clamped, dtype=np.bool_)


def simulate_mmpp2(
    params: MMPP2Params,
    horizon: float,
    rng: np.random.Generator,
    *,
    marks: MarkSampler | None = None,
    max_events: int = MAX_EVENTS,
) -> EventLog:
    """An unintervened log on ``[0, horizon]`` (no channels if ``marks`` is None)."""
    data = simulate_mmpp2_experiment(
        params,
        Schedule.empty(horizon),
        rng,
        marks=marks or no_marks,
        max_events=max_events,
    )
    return data.log


def simulate_mmpp2_experiment(
    params: MMPP2Params,
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


def _params(x: Sequence[float]) -> MMPP2Params:
    low = math.exp(x[0])
    return MMPP2Params(low, low + math.exp(x[1]), math.exp(x[2]), math.exp(x[3]))


@dataclass(frozen=True)
class FittedMMPP2:
    """A fitted two-state MMPP. Equality compares every field but ``marks``."""

    params: MMPP2Params
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
        return mmpp2_log_likelihood(self.params, datasets)

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        return math.fsum(self.log_likelihoods(datasets))

    def simulate(
        self,
        horizon: float,
        rng: np.random.Generator,
        *,
        marks: MarkSampler | None = None,
    ) -> EventLog:
        return simulate_mmpp2(self.params, horizon, rng, marks=marks or self.marks)

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        marks: MarkSampler | None = None,
        label: str = "experiment",
    ) -> Dataset:
        """Run ``experiment`` (not validated here; the caller has the channels)."""
        return simulate_mmpp2_experiment(
            self.params,
            Schedule.of(experiment),
            rng,
            marks=marks or self.marks,
            label=label,
        )


@dataclass(frozen=True)
class MMPP2:
    """The regime-switching library member (``LibraryModel``)."""

    name: str = NAME

    def fit(self, datasets: Sequence[Dataset]) -> FittedMMPP2:
        data = check_datasets(datasets)
        steps = [_steps(d) for d in data]
        n = n_counted(data)
        exposure = math.fsum(observed_time(d) for d in data)
        if n < 2 or exposure <= 0.0:
            raise LibraryInputError(f"need ≥ 2 counted events to fit; have {n}")
        log_r = math.log(n / exposure)

        def objective(x: Floats) -> float:
            p = _params(x.tolist())
            return -math.fsum(_log_likelihood(p, s) for s in steps) / n

        starts: list[tuple[float, ...]] = []
        for lo, hi in _RATE_STARTS:
            for sw in _SWITCH_STARTS:
                starts.append(
                    (
                        log_r + math.log(lo),
                        log_r + math.log(hi - lo),
                        log_r + math.log(sw * _HIGH_SHARE),
                        log_r + math.log(sw * (1.0 - _HIGH_SHARE)),
                    )
                )
        box = (log_r + _LOG_LO, log_r + _LOG_HI)
        res = multistart(objective, starts, [box] * 4)
        params = _params(res.x)
        per = mmpp2_log_likelihood(params, data)
        return FittedMMPP2(
            params=params,
            log_likelihood=math.fsum(per),
            log_likelihood_per_dataset=per,
            n_events=n,
            n_starts=res.n_starts,
            n_converged=res.n_converged,
            best_start=res.start_index,
            marks=EmpiricalMarks.from_datasets(data),
        )
