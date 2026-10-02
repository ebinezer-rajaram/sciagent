"""Exact simulation of a GLM point process by Ogata thinning (SPEC §2).

Given a :class:`~sciagent.glm.grammar.Structure`, a ψ assignment and
coefficients θ, :func:`simulate` draws an event log from
``λ(t | H_t) = g(θ₀ + Σₖ θₖ · φₖ(t; H_t, ψₖ))`` on ``[0, horizon]``, with each
accepted event's marks drawn iid from an environment-supplied
:class:`MarkSampler`. :func:`intensity` evaluates the same λ pointwise on a
given log. Every truth's data comes from here, so this is an instrument
(SPEC §6.3).

**Independence.** This module evaluates φ from the semantics in
``grammar.py``'s docstring with its own code. It deliberately does not import
``features.py`` or ``likelihood.py``: the cross-check that rescales simulated
logs through *their* compensator only tests something if the two
implementations share nothing but the grammar.

**Semantics** (from ``grammar.py``). History is a left limit: at time t only
events with ``t_j < t`` count. ``Excite`` sums ``mark(m_j) · kernel(t - t_j)``
over its source; ``LastMarkAbove`` reads the last event strictly before t.

**Thinning bound.** Each step takes a look-ahead window ``(s, e]`` that holds no
event of the current history after s, and bounds every design column on it by
interval arithmetic:

- ``Excite``: per event, the kernel's range over the event's lag interval
  ``[s - t_j, e - t_j]``. ``ExpK`` and ``PowerK`` are decreasing, so the range
  is the two endpoint values; ``GammaK`` (shape k ≥ 1) is unimodal with mode
  ``(k - 1) · mean / k``, so the maximum is the mode's value when the mode lies
  in the interval. Each event's range is multiplied by its (signed) weight and
  the ranges are summed: valid for any sign of mark.
- ``Periodic``: the exact range of sin / cos on the angle interval.
- ``Trend``: increasing, so its endpoint values.
- ``Product``: interval multiplication; ``Gate``: the condition's set of values
  on the window ({0}, {1} or {0, 1}) times the inner range.

The column ranges and the signs of θ give an upper bound on the linear
predictor, and the link is increasing, so ``g`` of that bounds λ on the window.
An accepted event starts a new window (it changes the history). A candidate at
which λ exceeds the bound is a bug and raises :class:`BoundViolationError`;
a negative λ under the identity link means an invalid truth and raises
:class:`NegativeIntensityError`. Floating-point rounding is the only slack
allowed (``_BOUND_RTOL``, ``_TRIG_TOL``).

**Cost.** ``ExpK`` columns use the exact O(1) recursion during simulation
(:class:`_History`); ``PowerK`` and ``GammaK`` sum directly over the whole
history (no truncation), so they are O(n) per evaluation. Every sum is an
exactly rounded ``math.fsum`` (``sciagent.core.reductions``), so the output
does not depend on numpy's summation order. :func:`intensity` never uses the
recursion, which lets the time-rescaling tests check one path against the other.

**Plans (interventions).** :func:`simulate_planned` runs the same loop under a
:class:`Plan`, the simulator-level form of an intervention (the language is in
``interventions.py``; censoring is observation only, so it is not here):

- *Forced events* are inserted into history at their scheduled times. Every
  look-ahead window ends at the next forced time, and is then treated as open
  at that end, so no generated event can coincide with a forced one. A forced
  event excites like any other; it is flagged in :class:`PlannedRun`.
- *Rate clamps* set ``λ := c`` on ``[start, end)``. Windows also end at every
  clamp boundary, so each window is wholly inside or outside a clamp; inside,
  ``c`` is both the thinning bound and the rate, and the model's λ is not
  evaluated at all (so an explosive or negative λ there is not an error).
  Events generated under a clamp enter history and are flagged.
- *Mark overrides* replace one channel's mark of every generated (not forced)
  event in ``[start, end)``, after the sampler has drawn it and before the
  event enters history.

The sampler is called once for every event, forced ones included, and
overrides are applied after the call, so a plan never shifts the generator's
stream: draws are, in time order, a candidate gap, an acceptance uniform, and
on acceptance (or at a forced time) the sampler's marks. With an empty plan the
loop is exactly :func:`simulate`'s.

ψ values need not lie on the grids in ``grids.py`` (a truth may be off-grid);
they must be assigned for every slot, finite and inside the parameter's domain.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Final, Protocol

import numpy as np
import numpy.typing as npt
from scipy.special import gammaln, xlogy

from sciagent.core.errors import SciAgentError
from sciagent.core.reductions import matvec, total
from sciagent.glm.data import EventLog, Floats
from sciagent.glm.grammar import (
    Above,
    ChannelKind,
    ChannelSpec,
    Cond,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    MarkFn,
    One,
    Periodic,
    PhaseWindow,
    Pow,
    Product,
    PsiSlot,
    Source,
    SourceKind,
    Structure,
    Trend,
    n_columns,
    psi_slots,
    validate,
)

#: Relative slack for comparing an evaluated λ against its bound (rounding only).
_BOUND_RTOL: Final = 1e-9
#: Absolute widening of trigonometric ranges, to cover rounding of the angle.
_TRIG_TOL: Final = 1e-9
#: Window adaptation: aim for between 1 and 4 expected candidates per window.
_MAX_EXPECTED: Final = 4.0
_MIN_EXPECTED: Final = 1.0
_MIN_WINDOW: Final = 1e-6
#: exp(709) is near the float64 limit; a larger bound on η cannot be thinned.
_MAX_EXP_ETA: Final = 700.0
#: Cap on the (rows x events) matrix built when evaluating many times at once.
_CHUNK_ENTRIES: Final = 2_000_000
_TWO_PI: Final = 2.0 * math.pi


class SimulationError(SciAgentError):
    """Base for faults raised by the simulator."""


class InvalidSimulationInputError(SimulationError):
    """ψ, θ, channels, horizon or a sampled mark is not a valid input."""


class BoundViolationError(SimulationError):
    """λ exceeded its thinning bound at an evaluated point: a simulator bug."""


class NegativeIntensityError(SimulationError):
    """λ < 0 under the identity link: the truth is not a valid point process."""


class ExplosionError(SimulationError):
    """More than ``max_events`` events, or an intensity bound that overflows."""


class MarkSampler(Protocol):
    """Draws one event's marks (every channel) from the passed generator."""

    def __call__(self, rng: np.random.Generator, /) -> Mapping[str, float]: ...


#: ψ for each feature of a structure, keyed by slot; one mapping per feature.
type PsiAssignment = tuple[Mapping[PsiSlot, float], ...]


@dataclass(frozen=True)
class Coefficients:
    """θ₀ and, per feature, one coefficient per design column (``n_columns``)."""

    intercept: float
    per_feature: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class ForcedEvent:
    """An exogenous event at ``time``; ``marks`` sets some or all of its channels.

    Channels not given are drawn from the sampler.
    """

    time: float
    marks: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RateClamp:
    """``λ := rate`` on ``[start, end)``."""

    start: float
    end: float
    rate: float


@dataclass(frozen=True)
class MarkOverride:
    """``channel := value`` for every generated event in ``[start, end)``."""

    start: float
    end: float
    channel: str
    value: float


@dataclass(frozen=True)
class Plan:
    """A simulator-level intervention schedule (see the module docstring).

    ``forced`` strictly increasing in time; ``clamps`` sorted and disjoint;
    ``overrides`` on one channel disjoint. All inside ``[0, horizon]``.
    """

    forced: tuple[ForcedEvent, ...] = ()
    clamps: tuple[RateClamp, ...] = ()
    overrides: tuple[MarkOverride, ...] = ()


@dataclass(frozen=True, eq=False)
class PlannedRun:
    """Every event of a planned run, with which were forced and which clamped."""

    log: EventLog
    forced: npt.NDArray[np.bool_]
    clamped: npt.NDArray[np.bool_]


# --------------------------------------------------------------------------
# Kernels
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Kernel:
    """A kernel density on [0, ∞) with its ψ resolved.

    ``a``/``b``: ExpK (β, unused); PowerK (c, p); GammaK (shape k, scale μ/k).
    """

    kind: KernelKind
    a: float
    b: float

    def value(self, lag: Floats) -> Floats:
        """Density at ``lag ≥ 0`` (finite at 0 for every allowed ψ)."""
        match self.kind:
            case KernelKind.EXP:
                out: Floats = self.a * np.exp(-self.a * lag)
            case KernelKind.POWER:
                out = (self.b - 1.0) / self.a * (1.0 + lag / self.a) ** (-self.b)
            case KernelKind.GAMMA:
                k, scale = self.a, self.b
                log_norm = float(gammaln(k)) + k * math.log(scale)
                out = np.exp(xlogy(k - 1.0, lag) - lag / scale - log_norm)
        return out

    def mode(self) -> float:
        if self.kind is KernelKind.GAMMA:
            return (self.a - 1.0) * self.b
        return 0.0

    def range(self, lo: Floats, hi: Floats) -> tuple[Floats, Floats]:
        """Min and max of the density over each lag interval ``[lo, hi]``."""
        v_lo, v_hi = self.value(lo), self.value(hi)
        top = np.maximum(v_lo, v_hi)
        mode = self.mode()
        if mode > 0.0:
            inside = (lo <= mode) & (mode <= hi)
            peak = float(self.value(np.array([mode]))[0])
            top = np.where(inside, peak, top)
        return np.minimum(v_lo, v_hi), top


# --------------------------------------------------------------------------
# Compiled feature trees (ψ resolved, validated)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Weights:
    """Per-event weight of one ``Excite``: ``mark(m_j) · 1[j ∈ source]``.

    ``exp_rate`` is the ``Excite``'s β when its kernel is ``ExpK`` (nan
    otherwise), so a history can keep the O(1) recursion for it.
    """

    mark: MarkFn
    source: Source
    param: float  # the mark function's ψ, or nan when it has none
    spec: ChannelSpec | None  # the mark channel's spec, None for One
    exp_rate: float

    def of(self, marks: Mapping[str, Floats], n: int) -> Floats:
        match self.mark:
            case One():
                w = np.ones(n)
            case Mark(channel=c):
                w = np.array(marks[c], dtype=np.float64)
            case Pow(channel=c):
                assert self.spec is not None
                w = (marks[c] / self.spec.location) ** self.param
            case ExpOf(channel=c):
                assert self.spec is not None
                w = np.exp(
                    self.param * (marks[c] - self.spec.location) / self.spec.scale
                )
            case Above(channel=c):
                assert self.spec is not None
                z = (marks[c] - self.spec.location) / self.spec.scale
                w = (z > self.param).astype(np.float64)
        match self.source.kind:
            case SourceKind.ALL:
                return w
            case SourceKind.POSITIVE:
                assert self.source.channel is not None
                return np.where(marks[self.source.channel] == 1.0, w, 0.0)
            case SourceKind.NEGATIVE:
                assert self.source.channel is not None
                return np.where(marks[self.source.channel] == -1.0, w, 0.0)


@dataclass(frozen=True)
class _CExcite:
    kernel: _Kernel
    slot: int  # index into the history's weight buffers


@dataclass(frozen=True)
class _CPeriodic:
    period: float


@dataclass(frozen=True)
class _CTrend:
    pass


@dataclass(frozen=True)
class _CProduct:
    left: _CNode
    right: _CNode


@dataclass(frozen=True)
class _CLastAbove:
    channel: str
    threshold: float
    location: float
    scale: float


@dataclass(frozen=True)
class _CPhase:
    period: float
    phase: float


@dataclass(frozen=True)
class _CGate:
    inner: _CNode
    cond: _CLastAbove | _CPhase


type _CNode = _CExcite | _CPeriodic | _CTrend | _CProduct | _CGate


def _finite(value: float, what: str) -> float:
    v = float(value)
    if not math.isfinite(v):
        raise InvalidSimulationInputError(f"{what} must be finite, got {value}")
    return v


def _positive(value: float, what: str) -> float:
    v = _finite(value, what)
    if v <= 0.0:
        raise InvalidSimulationInputError(f"{what} must be > 0, got {value}")
    return v


class _Compiler:
    """Resolves ψ into one feature tree and collects its ``Excite`` weights."""

    def __init__(
        self,
        psi: Mapping[PsiSlot, float],
        by_name: Mapping[str, ChannelSpec],
        weights: list[_Weights],
    ) -> None:
        self.psi = psi
        self.by_name = by_name
        self.weights = weights  # shared across features: one slot numbering

    def get(self, path: tuple[int, ...], name: str) -> float:
        return _finite(self.psi[PsiSlot(path, name)], f"ψ {name} at {path}")

    def node(self, feature: Feature, path: tuple[int, ...]) -> _CNode:
        match feature:
            case Excite(kernel=kernel, mark=mark, source=source):
                kpath = (*path, 0)
                match kernel:
                    case KernelKind.EXP:
                        beta = _positive(self.get(kpath, "exp_rate"), "exp_rate")
                        k = _Kernel(kernel, beta, math.nan)
                    case KernelKind.POWER:
                        c = _positive(self.get(kpath, "power_c"), "power_c")
                        p = self.get(kpath, "power_p")
                        if p <= 1.0:
                            raise InvalidSimulationInputError(
                                f"power_p must be > 1, got {p}"
                            )
                        k = _Kernel(kernel, c, p)
                    case KernelKind.GAMMA:
                        shape = self.get(kpath, "gamma_shape")
                        if shape < 1.0:
                            raise InvalidSimulationInputError(
                                f"gamma_shape must be ≥ 1 (bounded), got {shape}"
                            )
                        mean = _positive(self.get(kpath, "gamma_mean"), "gamma_mean")
                        k = _Kernel(kernel, shape, mean / shape)
                rate = k.a if kernel is KernelKind.EXP else math.nan
                self.weights.append(self._weights(mark, source, (*path, 1), rate))
                return _CExcite(k, len(self.weights) - 1)
            case Periodic():
                return _CPeriodic(_positive(self.get(path, "period"), "period"))
            case Trend():
                return _CTrend()
            case Product(left=left, right=right):
                return _CProduct(
                    self.node(left, (*path, 0)), self.node(right, (*path, 1))
                )
            case Gate(feature=inner, cond=cond):
                c_inner = self.node(inner, (*path, 0))
                return _CGate(c_inner, self._cond(cond, (*path, 1)))

    def _weights(
        self, mark: MarkFn, source: Source, path: tuple[int, ...], rate: float
    ) -> _Weights:
        match mark:
            case One():
                return _Weights(mark, source, math.nan, None, rate)
            case Mark(channel=c):
                return _Weights(mark, source, math.nan, self.by_name[c], rate)
            case Pow(channel=c):
                spec = self.by_name[c]
                _positive(spec.location, f"location of {c} (Pow divides by it)")
                a = self.get(path, "pow_exponent")
                return _Weights(mark, source, a, spec, rate)
            case ExpOf(channel=c):
                a = self.get(path, "exp_coef")
                return _Weights(mark, source, a, self.by_name[c], rate)
            case Above(channel=c):
                q = self.get(path, "above_z")
                return _Weights(mark, source, q, self.by_name[c], rate)

    def _cond(self, cond: Cond, path: tuple[int, ...]) -> _CLastAbove | _CPhase:
        match cond:
            case LastMarkAbove(channel=c):
                spec = self.by_name[c]
                q = self.get(path, "above_z")
                return _CLastAbove(c, q, spec.location, spec.scale)
            case PhaseWindow():
                period = _positive(self.get(path, "period"), "period")
                return _CPhase(period, self.get(path, "phase"))


def _check_channels(channels: tuple[ChannelSpec, ...]) -> dict[str, ChannelSpec]:
    by_name: dict[str, ChannelSpec] = {}
    for spec in channels:
        if spec.name in by_name:
            raise InvalidSimulationInputError(f"duplicate channel {spec.name!r}")
        _finite(spec.location, f"location of {spec.name}")
        _positive(spec.scale, f"scale of {spec.name}")
        by_name[spec.name] = spec
    return by_name


def _compile(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
) -> tuple[tuple[_CNode, ...], tuple[_Weights, ...], float, tuple[Floats, ...]]:
    """Validate inputs and resolve ψ; returns nodes, weights, θ₀ and θ per feature."""
    validate(structure, channels)
    by_name = _check_channels(channels)
    n = len(structure.features)
    if len(psi) != n:
        raise InvalidSimulationInputError(f"{len(psi)} ψ mappings for {n} features")
    if len(coef.per_feature) != n:
        raise InvalidSimulationInputError(
            f"{len(coef.per_feature)} coefficient tuples for {n} features"
        )
    intercept = _finite(coef.intercept, "θ₀")
    nodes: list[_CNode] = []
    weights: list[_Weights] = []
    thetas: list[Floats] = []
    for i, (feature, given, theta) in enumerate(
        zip(structure.features, psi, coef.per_feature, strict=True)
    ):
        expected = set(psi_slots(feature))
        missing = sorted(expected - set(given))
        extra = sorted(set(given) - expected)
        if missing or extra:
            raise InvalidSimulationInputError(
                f"feature {i}: ψ missing {missing}, unexpected {extra}"
            )
        if len(theta) != n_columns(feature):
            raise InvalidSimulationInputError(
                f"feature {i}: {len(theta)} coefficients, {n_columns(feature)} columns"
            )
        thetas.append(np.array([_finite(v, f"θ of feature {i}") for v in theta]))
        nodes.append(_Compiler(given, by_name, weights).node(feature, ()))
    return tuple(nodes), tuple(weights), intercept, tuple(thetas)


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def _check_marks(
    marks: Mapping[str, Floats], channels: tuple[ChannelSpec, ...], where: str
) -> None:
    """Every channel present; values finite and in the channel kind's domain."""
    names = sorted(spec.name for spec in channels)
    if sorted(marks) != names:
        raise InvalidSimulationInputError(
            f"{where}: mark channels {sorted(marks)}, expected {names}"
        )
    for spec in channels:
        m = np.asarray(marks[spec.name], dtype=np.float64)
        if not np.all(np.isfinite(m)):
            raise InvalidSimulationInputError(f"{where}: non-finite {spec.name}")
        if spec.kind is ChannelKind.POSITIVE and not np.all(m > 0.0):
            raise InvalidSimulationInputError(f"{where}: {spec.name} must be > 0")
        if spec.kind is ChannelKind.SIGN and not np.all((m == 1.0) | (m == -1.0)):
            raise InvalidSimulationInputError(f"{where}: {spec.name} must be ±1")


class _History:
    """Growing event buffers: times, marks per channel, weights per ``Excite``.

    With ``recursive`` set (histories built event by event, as the simulator
    builds them), each ``ExpK`` slot also carries the exact recursion
    ``S_n = Σ_{j ≤ n} w_j β e^{-β (t_n - t_j)}``, updated as
    ``S_n = S_{n-1} e^{-β (t_n - t_{n-1})} + β w_n``, so the column at any
    ``t > t_n`` is ``S_n e^{-β (t - t_n)}`` in O(1). Without it every kernel
    sum is taken directly over the history.
    """

    def __init__(
        self,
        channels: tuple[ChannelSpec, ...],
        weights: tuple[_Weights, ...],
        *,
        recursive: bool,
        capacity: int = 1024,
    ) -> None:
        self.channels = channels
        self.weight_specs = weights
        self.recursive = recursive
        self.names = tuple(sorted(spec.name for spec in channels))
        self.n = 0
        self._times = np.empty(capacity)
        self._marks = {name: np.empty(capacity) for name in self.names}
        self._weights = [np.empty(capacity) for _ in weights]
        self._exp_sums = [0.0 for _ in weights]

    @staticmethod
    def of_log(
        log: EventLog,
        channels: tuple[ChannelSpec, ...],
        weights: tuple[_Weights, ...],
        *,
        recursive: bool,
    ) -> _History:
        marks = {
            spec.name: log.marks[spec.name]
            for spec in channels
            if spec.name in log.marks
        }
        _check_marks(marks, channels, "event log")
        if recursive:  # replay, exactly as the simulator would have built it
            h = _History(channels, weights, recursive=True)
            for i, t in enumerate(log.times.tolist()):
                h.append(t, {name: float(marks[name][i]) for name in h.names})
            return h
        h = _History(channels, weights, recursive=False, capacity=max(1, log.n))
        h.n = log.n
        h._times[: log.n] = log.times
        for name in h.names:
            h._marks[name][: log.n] = marks[name]
        for buf, spec in zip(h._weights, weights, strict=True):
            buf[: log.n] = spec.of(marks, log.n)
        return h

    def append(self, t: float, marks: Mapping[str, float]) -> None:
        if sorted(marks) != list(self.names):
            raise InvalidSimulationInputError(
                f"marks of event at t={t}: channels {sorted(marks)}, "
                f"expected {list(self.names)}"
            )
        one = {name: np.array([float(marks[name])]) for name in self.names}
        _check_marks(one, self.channels, f"marks of event at t={t}")
        if self.n == self._times.size:
            self._grow()
        i = self.n
        gap = t - float(self._times[i - 1]) if i else 0.0
        self._times[i] = t
        for name in self.names:
            self._marks[name][i] = one[name][0]
        for slot, spec in enumerate(self.weight_specs):
            w = float(spec.of(one, 1)[0])
            self._weights[slot][i] = w
            if self.recursive and math.isfinite(spec.exp_rate):
                beta = spec.exp_rate
                decayed = self._exp_sums[slot] * math.exp(-beta * gap)
                self._exp_sums[slot] = decayed + beta * w
        self.n += 1

    def _grow(self) -> None:
        size = 2 * self._times.size

        def bigger(a: Floats) -> Floats:
            out = np.empty(size)
            out[: a.size] = a
            return out

        self._times = bigger(self._times)
        self._marks = {k: bigger(v) for k, v in self._marks.items()}
        self._weights = [bigger(w) for w in self._weights]

    @property
    def times(self) -> Floats:
        return self._times[: self.n]

    def mark(self, name: str) -> Floats:
        return self._marks[name][: self.n]

    def weights(self, slot: int) -> Floats:
        return self._weights[slot][: self.n]

    def exp_sum(self, slot: int) -> float | None:
        """``S_n`` for an ``ExpK`` slot of a recursive history, else None."""
        if self.recursive and math.isfinite(self.weight_specs[slot].exp_rate):
            return self._exp_sums[slot]
        return None

    def to_log(self, horizon: float) -> EventLog:
        return EventLog.create(
            self.times.copy(),
            {name: self.mark(name).copy() for name in self.names},
            horizon,
        )


# --------------------------------------------------------------------------
# Pointwise evaluation and window ranges
# --------------------------------------------------------------------------


def _contains_point(a: float, b: float, x0: float) -> bool:
    """Whether ``[a, b]`` contains ``x0 + 2πk`` for some integer k."""
    k = math.ceil((a - x0) / _TWO_PI)
    return x0 + _TWO_PI * k <= b


def _trig_range(a: float, b: float, *, cosine: bool) -> tuple[float, float]:
    """Range of sin (or cos) on the angle interval ``[a, b]``, widened by rounding."""
    if b - a >= _TWO_PI:
        return -1.0 - _TRIG_TOL, 1.0 + _TRIG_TOL
    fn = math.cos if cosine else math.sin
    peak, trough = (0.0, math.pi) if cosine else (0.5 * math.pi, -0.5 * math.pi)
    va, vb = fn(a), fn(b)
    lo, hi = min(va, vb), max(va, vb)
    if _contains_point(a, b, peak):
        hi = 1.0
    if _contains_point(a, b, trough):
        lo = -1.0
    return lo - _TRIG_TOL, hi + _TRIG_TOL


class _Evaluator:
    """λ, design columns and their window ranges, for one compiled structure."""

    def __init__(
        self,
        nodes: tuple[_CNode, ...],
        intercept: float,
        thetas: tuple[Floats, ...],
        link: Link,
        horizon: float,
        history: _History,
    ) -> None:
        self.nodes = nodes
        self.intercept = intercept
        self.thetas = thetas
        self.link = link
        self.horizon = horizon
        self.history = history

    # --- pointwise -------------------------------------------------------

    def columns(self, node: _CNode, t: Floats) -> Floats:
        """Design columns of ``node`` at times ``t``: shape (n_columns, len(t))."""
        match node:
            case _CExcite(kernel=kernel, slot=slot):
                return self._excite(kernel, slot, t)[None, :]
            case _CPeriodic(period=period):
                x = _TWO_PI * t / period
                return np.stack([np.sin(x), np.cos(x)])
            case _CTrend():
                return (t / self.horizon)[None, :]
            case _CProduct(left=left, right=right):
                a, b = self.columns(left, t), self.columns(right, t)
                return (a[:, None, :] * b[None, :, :]).reshape(-1, t.size)
            case _CGate(inner=inner, cond=cond):
                return self.columns(inner, t) * self._cond(cond, t)[None, :]

    def _excite(self, kernel: _Kernel, slot: int, t: Floats) -> Floats:
        times = self.history.times
        w = self.history.weights(slot)
        n = times.size
        out = np.zeros(t.size)
        if n == 0:
            return out
        if t.size == 1:
            recursion = self.history.exp_sum(slot)
            last = float(times[-1])
            if recursion is not None and t[0] > last:
                out[0] = recursion * math.exp(-kernel.a * (float(t[0]) - last))
                return out
            k = int(np.searchsorted(times, t[0], side="left"))
            out[0] = total(w[:k] * kernel.value(t[0] - times[:k]))
            return out
        rows = max(1, _CHUNK_ENTRIES // n)
        for start in range(0, t.size, rows):
            tt = t[start : start + rows]
            lag = tt[:, None] - times[None, :]
            before = lag > 0.0
            vals = kernel.value(np.where(before, lag, 0.0))
            out[start : start + rows] = matvec(np.where(before, vals, 0.0), w)
        return out

    def _cond(self, cond: _CLastAbove | _CPhase, t: Floats) -> Floats:
        match cond:
            case _CLastAbove(channel=c, threshold=q, location=loc, scale=scale):
                times = self.history.times
                last = np.searchsorted(times, t, side="left") - 1
                if times.size == 0:
                    return np.zeros(t.size)
                z = (self.history.mark(c)[np.maximum(last, 0)] - loc) / scale
                return ((last >= 0) & (z > q)).astype(np.float64)
            case _CPhase(period=period, phase=phase):
                on = np.sin(_TWO_PI * t / period - phase) >= 0.0
                return on.astype(np.float64)

    def eta(self, t: Floats) -> Floats:
        out = np.full(t.size, self.intercept)
        for node, theta in zip(self.nodes, self.thetas, strict=True):
            cols = self.columns(node, t)
            for c in range(theta.size):  # fixed order, elementwise: deterministic
                out += theta[c] * cols[c]
        return out

    def apply_link(self, eta: Floats) -> Floats:
        match self.link:
            case Link.IDENTITY:
                return eta
            case Link.EXP:
                return np.exp(eta)
            case Link.SOFTPLUS:
                return np.logaddexp(0.0, eta)

    # --- window ranges ---------------------------------------------------

    def column_range(self, node: _CNode, s: float, end: float) -> tuple[Floats, Floats]:
        """Bounds ``(lo, hi)`` per column of ``node`` over ``t ∈ (s, end]``.

        Requires that no history event lies after ``s``.
        """
        match node:
            case _CExcite(kernel=kernel, slot=slot):
                times = self.history.times
                recursion = self.history.exp_sum(slot)
                if recursion is not None and times.size:
                    # S e^{-β(t - t_n)} is monotone in t: its endpoint values.
                    last = float(times[-1])
                    at_s = recursion * math.exp(-kernel.a * (s - last))
                    at_end = recursion * math.exp(-kernel.a * (end - last))
                    lo_hi = (min(at_s, at_end), max(at_s, at_end))
                    return np.array([lo_hi[0]]), np.array([lo_hi[1]])
                w = self.history.weights(slot)
                kmin, kmax = kernel.range(s - times, end - times)
                low = total(np.where(w >= 0.0, w * kmin, w * kmax))
                high = total(np.where(w >= 0.0, w * kmax, w * kmin))
                return np.array([low]), np.array([high])
            case _CPeriodic(period=period):
                a, b = _TWO_PI * s / period, _TWO_PI * end / period
                slo, shi = _trig_range(a, b, cosine=False)
                clo, chi = _trig_range(a, b, cosine=True)
                return np.array([slo, clo]), np.array([shi, chi])
            case _CTrend():
                return np.array([s / self.horizon]), np.array([end / self.horizon])
            case _CProduct(left=left, right=right):
                alo, ahi = self.column_range(left, s, end)
                blo, bhi = self.column_range(right, s, end)
                corners = np.stack(
                    [np.outer(x, y).ravel() for x in (alo, ahi) for y in (blo, bhi)]
                )
                return corners.min(axis=0), corners.max(axis=0)
            case _CGate(inner=inner, cond=cond):
                lo, hi = self.column_range(inner, s, end)
                can_off, can_on = self._cond_range(cond, s, end)
                if not can_on:
                    return np.zeros_like(lo), np.zeros_like(hi)
                if can_off:
                    return np.minimum(lo, 0.0), np.maximum(hi, 0.0)
                return lo, hi

    def _cond_range(
        self, cond: _CLastAbove | _CPhase, s: float, end: float
    ) -> tuple[bool, bool]:
        """Whether the condition can be off / on somewhere in ``(s, end]``."""
        match cond:
            case _CLastAbove(channel=c, threshold=q, location=loc, scale=scale):
                if self.history.n == 0:
                    return True, False
                on = bool((self.history.mark(c)[-1] - loc) / scale > q)
                return not on, on
            case _CPhase(period=period, phase=phase):
                a = _TWO_PI * s / period - phase
                b = _TWO_PI * end / period - phase
                lo, hi = _trig_range(a, b, cosine=False)
                return lo < 0.0, hi >= 0.0

    def eta_upper(self, s: float, end: float) -> float:
        """An upper bound on the linear predictor over ``(s, end]``."""
        terms = [self.intercept]
        for node, theta in zip(self.nodes, self.thetas, strict=True):
            lo, hi = self.column_range(node, s, end)
            terms.extend(np.where(theta >= 0.0, theta * hi, theta * lo).tolist())
        return math.fsum(terms)


# --------------------------------------------------------------------------
# Thinning
# --------------------------------------------------------------------------


class _Thinning:
    """Ogata's thinning with locally bounded, adaptively sized windows.

    Under a :class:`Plan` (see the module docstring), windows also end at
    forced times and clamp boundaries; ``forced`` and ``clamped`` record, per
    event in history order, how it arose.
    """

    def __init__(
        self,
        evaluator: _Evaluator,
        marks: MarkSampler,
        horizon: float,
        rng: np.random.Generator,
        max_events: int,
        plan: Plan,
    ) -> None:
        self.ev = evaluator
        self.marks = marks
        self.horizon = horizon
        self.rng = rng
        self.max_events = max_events
        self.plan = plan
        self.forced: list[bool] = []
        self.clamped: list[bool] = []
        self._next_forced = 0
        self._edges = [x for c in plan.clamps for x in (c.start, c.end)]
        self._clamp: float | None = None  # the clamp rate on the current window
        self._open_end = False  # whether the current window excludes its end
        # A deterministic cap on thinning work (runs accept ~1.2 candidates per
        # event); it turns a slow explosion into an error instead of a hang.
        self._candidates = 0
        self._max_candidates = 50 * max_events + 10_000

    def run(self) -> None:
        s, width = 0.0, 1.0
        while True:
            self._insert_forced(s)
            if s >= self.horizon:
                return
            self._clamp = self._clamp_at(s)
            end = self._window_end(s, width)
            bound = self._rate_bound(s, end)
            while bound * (end - s) > _MAX_EXPECTED and end - s > _MIN_WINDOW:
                end = s + 0.5 * (end - s)
                bound = self._rate_bound(s, end)
            if bound * (end - s) > _MAX_EXPECTED:
                # Even the smallest window expects more than _MAX_EXPECTED
                # candidates: the bound exceeds _MAX_EXPECTED / _MIN_WINDOW,
                # millions of times the unit operating rate. Without this the
                # loop thins at that rate and hangs below max_events.
                raise ExplosionError(
                    f"intensity bound {bound} on ({s}, {end}] is runaway"
                )
            width = end - s
            if bound * width < _MIN_EXPECTED:
                width *= 2.0
            self._open_end = end == self._forced_time()
            accepted = self._thin(s, end, bound)
            s = end if accepted is None else accepted

    def _forced_time(self) -> float:
        """Time of the next forced event not yet inserted (inf when none)."""
        if self._next_forced < len(self.plan.forced):
            return self.plan.forced[self._next_forced].time
        return math.inf

    def _insert_forced(self, s: float) -> None:
        """Insert every forced event scheduled at or before ``s`` (only at ``s``)."""
        while self._forced_time() <= s:
            event = self.plan.forced[self._next_forced]
            marks = dict(self.marks(self.rng))
            marks.update(event.marks)
            self._append(event.time, marks, forced=True, clamped=False)
            self._next_forced += 1

    def _clamp_at(self, s: float) -> float | None:
        """The clamp rate on ``[s, next edge)``, or None outside every clamp."""
        i = bisect_right(self._edges, s)
        return self.plan.clamps[i // 2].rate if i % 2 else None

    def _window_end(self, s: float, width: float) -> float:
        """End of the next look-ahead window, also capped at forced times and edges."""
        end = min(s + width, self.horizon, self._forced_time())
        i = bisect_right(self._edges, s)
        if i < len(self._edges):
            end = min(end, self._edges[i])
        return end

    def _rate(self, t: float) -> float:
        """λ at t; the clamp rate inside a clamp."""
        if self._clamp is not None:
            return self._clamp
        return float(self.ev.apply_link(self.ev.eta(np.array([t])))[0])

    def _rate_bound(self, s: float, end: float) -> float:
        """Upper bound on λ over ``(s, end]``; exactly the clamp rate in a clamp."""
        if self._clamp is not None:
            return self._clamp
        eta_hi = self.ev.eta_upper(s, end)
        if self.ev.link is Link.IDENTITY and eta_hi < 0.0:
            raise NegativeIntensityError(
                f"identity-link λ ≤ {eta_hi} < 0 on ({s}, {end}]"
            )
        if self.ev.link is Link.EXP and eta_hi > _MAX_EXP_ETA:
            raise ExplosionError(f"exp-link bound on η is {eta_hi} on ({s}, {end}]")
        return float(self.ev.apply_link(np.array([eta_hi]))[0])

    def _thin(self, s: float, end: float, bound: float) -> float | None:
        """Thin candidates on ``(s, end]``; the time of the first accepted event."""
        if not math.isfinite(bound):
            raise ExplosionError(f"intensity bound {bound} on ({s}, {end}]")
        if bound <= 0.0:
            return None
        t = s
        while True:
            self._candidates += 1
            if self._candidates > self._max_candidates:
                raise ExplosionError(
                    f"more than {self._max_candidates} thinning candidates by "
                    f"t={t}: the process is (near-)explosive"
                )
            t = t + float(self.rng.exponential(1.0 / bound))
            if t > end or (self._open_end and t >= end):
                return None
            if t <= s:  # a zero draw: λ at s itself excludes the event at s
                continue
            lam = self._rate(t)
            if self.ev.link is Link.IDENTITY and lam < 0.0:
                raise NegativeIntensityError(f"identity-link λ({t}) = {lam} < 0")
            if lam > bound * (1.0 + _BOUND_RTOL) + _BOUND_RTOL:
                raise BoundViolationError(
                    f"λ({t}) = {lam} exceeds its thinning bound {bound} on ({s}, {end}]"
                )
            if float(self.rng.random()) * bound < lam:
                if self.ev.history.n >= self.max_events:
                    raise ExplosionError(f"more than {self.max_events} events by t={t}")
                marks = dict(self.marks(self.rng))
                for override in self.plan.overrides:
                    if override.start <= t < override.end:
                        marks[override.channel] = override.value
                clamped = self._clamp is not None
                self._append(t, marks, forced=False, clamped=clamped)
                return t

    def _append(
        self, t: float, marks: Mapping[str, float], *, forced: bool, clamped: bool
    ) -> None:
        if self.ev.history.n >= self.max_events:
            raise ExplosionError(f"more than {self.max_events} events by t={t}")
        self.ev.history.append(t, marks)
        self.forced.append(forced)
        self.clamped.append(clamped)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _evaluator(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    log: EventLog | None,
    horizon: float,
    *,
    recursive: bool,
) -> _Evaluator:
    nodes, weights, intercept, thetas = _compile(structure, psi, coef, channels)
    history = (
        _History(channels, weights, recursive=recursive)
        if log is None
        else _History.of_log(log, channels, weights, recursive=recursive)
    )
    return _Evaluator(nodes, intercept, thetas, structure.link, horizon, history)


def _times(t: Floats) -> Floats:
    arr = np.asarray(t, dtype=np.float64)
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        raise InvalidSimulationInputError("t must be a finite one-dimensional array")
    return arr


def intensity(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    log: EventLog,
    t: Floats,
) -> Floats:
    """λ at times ``t``, given the events of ``log`` strictly before each time.

    ``Trend`` uses ``log.horizon``. Values are returned as computed: under the
    identity link a negative λ is returned, not raised (the simulator raises).
    """
    ev = _evaluator(structure, psi, coef, channels, log, log.horizon, recursive=False)
    return ev.apply_link(ev.eta(_times(t)))


def simulate(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    marks: MarkSampler,
    horizon: float,
    rng: np.random.Generator,
    *,
    max_events: int = 100_000,
) -> EventLog:
    """Draw an event log on ``[0, horizon]`` by thinning (see the module docstring).

    Deterministic given the generator's state: draws are, in order, a candidate
    gap, an acceptance uniform, and on acceptance the sampler's marks.
    Raises :class:`NegativeIntensityError` (identity link, λ < 0 somewhere it
    was evaluated or bounded), :class:`ExplosionError` (more than
    ``max_events`` events or an overflowing bound), and
    :class:`InvalidSimulationInputError` for invalid ψ, θ, horizon or marks.
    """
    if not math.isfinite(horizon) or horizon <= 0.0:
        raise InvalidSimulationInputError(f"horizon must be positive, got {horizon}")
    if max_events < 0:
        raise InvalidSimulationInputError(f"max_events must be ≥ 0, got {max_events}")
    return simulate_planned(
        structure,
        psi,
        coef,
        channels,
        marks,
        horizon,
        rng,
        Plan(),
        max_events=max_events,
    ).log


def _check_plan(plan: Plan, horizon: float, channels: tuple[ChannelSpec, ...]) -> None:
    """Times finite and inside ``[0, horizon]``; ordering; known channels."""
    names = sorted(spec.name for spec in channels)

    def inside(t: float, what: str) -> float:
        v = _finite(t, what)
        if not 0.0 <= v <= horizon:
            raise InvalidSimulationInputError(f"{what} {v} is outside [0, {horizon}]")
        return v

    previous = -math.inf
    for event in plan.forced:
        t = inside(event.time, "forced event time")
        if t <= previous:
            raise InvalidSimulationInputError("forced times must strictly increase")
        previous = t
        unknown = sorted(c for c in event.marks if c not in names)
        if unknown:
            raise InvalidSimulationInputError(f"forced marks on unknown {unknown}")
    previous = 0.0
    for clamp in plan.clamps:
        a, b = inside(clamp.start, "clamp start"), inside(clamp.end, "clamp end")
        if not previous <= a < b:
            raise InvalidSimulationInputError("clamps must be sorted and disjoint")
        if _finite(clamp.rate, "clamp rate") < 0.0:
            raise InvalidSimulationInputError(f"clamp rate {clamp.rate} < 0")
        previous = b
    by_channel: dict[str, list[tuple[float, float]]] = {}
    for o in plan.overrides:
        a, b = inside(o.start, "override start"), inside(o.end, "override end")
        if not a < b:
            raise InvalidSimulationInputError(f"empty override window [{a}, {b})")
        if o.channel not in names:
            raise InvalidSimulationInputError(f"override on unknown {o.channel!r}")
        _finite(o.value, f"override value of {o.channel}")
        by_channel.setdefault(o.channel, []).append((a, b))
    for channel in sorted(by_channel):
        spans = sorted(by_channel[channel])
        for (_, b0), (a1, _) in pairwise(spans):
            if a1 < b0:
                raise InvalidSimulationInputError(f"overlapping overrides on {channel}")


def simulate_planned(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    marks: MarkSampler,
    horizon: float,
    rng: np.random.Generator,
    plan: Plan,
    *,
    max_events: int = 100_000,
) -> PlannedRun:
    """:func:`simulate` under a :class:`Plan`; every event, with how it arose.

    ``max_events`` counts forced events too. Raises as :func:`simulate`, and
    :class:`InvalidSimulationInputError` for a malformed plan (a mark value is
    checked when its event enters history).
    """
    if not math.isfinite(horizon) or horizon <= 0.0:
        raise InvalidSimulationInputError(f"horizon must be positive, got {horizon}")
    if max_events < 0:
        raise InvalidSimulationInputError(f"max_events must be ≥ 0, got {max_events}")
    ev = _evaluator(structure, psi, coef, channels, None, horizon, recursive=True)
    _check_plan(plan, horizon, channels)
    thinning = _Thinning(ev, marks, horizon, rng, max_events, plan)
    thinning.run()
    forced = np.array(thinning.forced, dtype=np.bool_)
    clamped = np.array(thinning.clamped, dtype=np.bool_)
    forced.setflags(write=False)
    clamped.setflags(write=False)
    return PlannedRun(ev.history.to_log(horizon), forced, clamped)


def _single(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    channels: tuple[ChannelSpec, ...],
    log: EventLog,
    recursive: bool,
) -> tuple[_Evaluator, _CNode]:
    coef = Coefficients(0.0, ((0.0,) * n_columns(feature),))
    structure = Structure((feature,))
    ev = _evaluator(
        structure, (psi,), coef, channels, log, log.horizon, recursive=recursive
    )
    return ev, ev.nodes[0]


def columns(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    channels: tuple[ChannelSpec, ...],
    log: EventLog,
    t: Floats,
    *,
    recursive: bool = False,
) -> Floats:
    """One feature's design columns at ``t`` on ``log``: shape (n_columns, len(t)).

    The simulator's own evaluation, exposed for tests and diagnostics.
    ``recursive`` replays ``log`` event by event and uses the ``ExpK``
    recursion where it applies (a single time after the last event), as
    :func:`simulate` does; otherwise every sum is direct.
    """
    ev, node = _single(feature, psi, channels, log, recursive)
    return ev.columns(node, _times(t))


def column_ranges(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    channels: tuple[ChannelSpec, ...],
    log: EventLog,
    s: float,
    end: float,
    *,
    recursive: bool = False,
) -> tuple[Floats, Floats]:
    """The thinning bounds ``(lo, hi)`` per column over ``(s, end]``.

    ``s`` must not precede the last event of ``log``. ``recursive`` as in
    :func:`columns`.
    """
    if log.n and s < log.times[-1]:
        raise InvalidSimulationInputError(f"window start {s} precedes the last event")
    if not s < end:
        raise InvalidSimulationInputError(f"empty window ({s}, {end}]")
    ev, node = _single(feature, psi, channels, log, recursive)
    return ev.column_range(node, s, end)
