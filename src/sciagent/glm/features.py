"""Feature precomputation (SPEC §2.2): the design matrix the likelihood reads.

For a :class:`~sciagent.glm.grammar.Structure` and a ψ assignment, every
feature's columns φ(t) are evaluated

- at the event times, as **left limits**: only events strictly before t count,
  so φ is predictable and ``λ(tᵢ)`` never sees event i itself;
- at the nodes of a composite Gauss-Legendre rule on ``[0, T]``, which the
  compensator needs under the exp and softplus links (``∫ g(Xθ)`` is not linear
  in per-column integrals) and which the fitter uses under the identity link
  to impose ``λ ≥ 0`` between events, not only at them;

and every column's integral ``∫₀ᵀ φ(t) dt`` is computed **in closed form**
wherever the feature is a leaf (``Excite``, ``Periodic``, ``Trend``) under any
chain of ``Gate`` s. ``Excite`` integrates through the kernel CDF; a gate
restricts the leaf's antiderivative to a union of intervals (a
``LastMarkAbove`` window switches only at events, a ``PhaseWindow`` at known
times). Columns under a ``Product`` have no closed form here and are
integrated with the rule; :attr:`Design.integral_exact` says which is which.

Sums over history: the exponential kernel by the O(n) recursion; the gamma
kernel, for integer shapes (every grid value), by an O(n) recursion on the
moments ``Σ wⱼ (t - tⱼ)ˡ e^{-r(t - tⱼ)}``; the Lomax kernel (and gamma off the
integer shapes) by direct O(n·m) sums, folded event by event with O(m)
memory. Every fold runs in a fixed order, so output is byte-identical for
identical input.

The quadrature rule is built so that the integrand is smooth on every panel.
Panels break at every event (where history sums jump and ``LastMarkAbove``
switches), at every ``PhaseWindow`` switch, and on a geometric grid just after
each event, starting at ``first_panel`` times the sharpest kernel scale
(``1/β``; Lomax ``c``; gamma ``mean/shape``) and growing by ``growth``, so a
sharp kernel's onset is resolved. No panel is longer than ``max_panel`` or
than ``1/panels_per_period`` of any period. Scales, periods and switches are
taken over the **whole ψ grid** of every node in the structure, so one rule
serves every on-grid ψ and a profile can cache per-feature
:class:`FeatureBlock` s. An a-posteriori error estimate (the rule against one
with doubled nodes per panel) is in ``likelihood.compensator_error``.

Interventional data (:class:`~sciagent.glm.data.Dataset`): every event is
history; only endogenous events outside the excluded windows get a row in
``at_events``; integrals and the rule cover ``[0, T]`` minus the windows.

ψ must assign every slot of every feature, each value on its grid
(``grids.grid``): the framework profiles over the grid, so an off-grid value
is a bug and raises :class:`~sciagent.core.errors.OffGridParameterError`.
``allow_off_grid=True`` exists for tests and the truth simulator only.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import accumulate
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy import special

from sciagent.core import reductions
from sciagent.core.errors import GrammarError, OffGridParameterError, SciAgentError
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.grammar import (
    KERNEL_PSI,
    Above,
    ChannelSpec,
    Cond,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
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
from sciagent.glm.grids import grid

type PsiAssignment = tuple[Mapping[PsiSlot, float], ...]
#: Observational data is a bare log; interventional data a :class:`Dataset`.
type Data = EventLog | Dataset
type Bools = npt.NDArray[np.bool_]
type Ints = npt.NDArray[np.intp]

#: Largest exponent allowed in ``ExpOf``; ``exp(700)`` is still finite.
MAX_EXP_ARG: Final = 700.0
#: Relative distance below which two panel edges are merged.
_EDGE_TOL: Final = 1e-12


class PsiAssignmentError(GrammarError):
    """A ψ assignment does not match the structure's slots, or is out of domain."""


class FeatureDataError(SciAgentError):
    """The event log cannot carry the structure's features.

    A channel the structure reads is missing from the log, a sign channel holds
    a value other than ±1, or ``Pow`` meets a non-positive mark.
    """


class FeatureNumericsError(SciAgentError):
    """A feature value is not finite (for example ``ExpOf`` overflowing)."""


class QuadratureSpecError(SciAgentError):
    """A quadrature specification is not a usable rule."""


# --------------------------------------------------------------------------
# Value types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class QuadratureSpec:
    """How the composite Gauss-Legendre rule on ``[0, T]`` is laid out.

    ``nodes_per_panel`` Gauss-Legendre nodes on each panel. After each event,
    breakpoints at ``first_panel · s · growthᵏ`` (k = 0, 1, …) with ``s`` the
    sharpest kernel scale in the structure. Panels longer than ``max_panel``,
    or than ``P / panels_per_period`` for any period P, are split evenly.
    """

    nodes_per_panel: int = 8
    first_panel: float = 0.25
    growth: float = 3.0
    max_panel: float = 1.0
    panels_per_period: int = 8

    def __post_init__(self) -> None:
        if self.nodes_per_panel < 1:
            raise QuadratureSpecError(f"nodes_per_panel {self.nodes_per_panel} < 1")
        if not (math.isfinite(self.first_panel) and self.first_panel > 0):
            raise QuadratureSpecError(f"first_panel must be > 0: {self.first_panel}")
        if not (math.isfinite(self.growth) and self.growth > 1):
            raise QuadratureSpecError(f"growth must be > 1: {self.growth}")
        if not (math.isfinite(self.max_panel) and self.max_panel > 0):
            raise QuadratureSpecError(f"max_panel must be > 0: {self.max_panel}")
        if self.panels_per_period < 1:
            raise QuadratureSpecError("panels_per_period must be >= 1")

    def refined(self) -> QuadratureSpec:
        """The same panels with twice the nodes: the error-estimate reference."""
        return QuadratureSpec(
            nodes_per_panel=2 * self.nodes_per_panel,
            first_panel=self.first_panel,
            growth=self.growth,
            max_panel=self.max_panel,
            panels_per_period=self.panels_per_period,
        )


DEFAULT_QUADRATURE: Final = QuadratureSpec()


@dataclass(frozen=True, eq=False)
class Design:
    """The precomputed design for one (structure, ψ, event log).

    Column 0 is the intercept (all ones, label ``"θ0"``). ``feature_columns[k]``
    lists the columns of ``structure.features[k]``, contiguous and in feature
    order; a ``Product`` lays its columns out row-major (left outer).

    ``integrals[c]`` is ``∫`` of column c over ``[0, T]`` minus the data's
    excluded windows; exact (closed form) where ``integral_exact[c]``,
    otherwise the quadrature rule's sum ``Σ_q weights[q] · at_nodes[q, c]``.

    ``at_events`` has one row per event that counts in ``Σ log λ(tᵢ)``: the
    endogenous events outside every excluded window (all events, for a bare
    :class:`EventLog`). ``event_index`` gives each row's index in the log;
    every event, counted or not, is history for the features. The rule's
    nodes avoid the excluded windows. Arrays are read-only.
    """

    labels: tuple[str, ...]
    at_events: Floats
    integrals: Floats
    integral_exact: Bools
    nodes: Floats
    weights: Floats
    at_nodes: Floats
    feature_columns: tuple[tuple[int, ...], ...]
    horizon: float
    event_index: Ints

    @property
    def n_columns(self) -> int:
        return len(self.labels)


# --------------------------------------------------------------------------
# Kernels
# --------------------------------------------------------------------------


def kernel_scale(kernel: KernelKind, params: Mapping[str, float]) -> float:
    """The time scale on which the kernel varies near zero lag."""
    match kernel:
        case KernelKind.EXP:
            return 1.0 / params["exp_rate"]
        case KernelKind.POWER:
            return params["power_c"]
        case KernelKind.GAMMA:
            return params["gamma_mean"] / params["gamma_shape"]


def kernel_pdf(kernel: KernelKind, params: Mapping[str, float], x: Floats) -> Floats:
    """The kernel density at lags ``x``; zero for ``x ≤ 0``."""
    x = np.asarray(x, dtype=np.float64)
    pos = x > 0
    xp = np.where(pos, x, 1.0)
    match kernel:
        case KernelKind.EXP:
            b = params["exp_rate"]
            v = b * np.exp(-b * xp)
        case KernelKind.POWER:
            c, p = params["power_c"], params["power_p"]
            v = (p - 1.0) / c * np.exp(-p * np.log1p(xp / c))
        case KernelKind.GAMMA:
            k, mu = params["gamma_shape"], params["gamma_mean"]
            rate = k / mu
            v = np.exp(
                (k - 1.0) * np.log(xp)
                - rate * xp
                + k * math.log(rate)
                - special.gammaln(k)
            )
    out: Floats = np.where(pos, v, 0.0)
    return out


def kernel_cdf(kernel: KernelKind, params: Mapping[str, float], x: Floats) -> Floats:
    """``∫₀ˣ kernel``: the kernel's CDF at lags ``x``; zero for ``x ≤ 0``."""
    x = np.asarray(x, dtype=np.float64)
    pos = x > 0
    xp = np.where(pos, x, 0.0)
    match kernel:
        case KernelKind.EXP:
            v = -np.expm1(-params["exp_rate"] * xp)
        case KernelKind.POWER:
            c, p = params["power_c"], params["power_p"]
            v = -np.expm1(-(p - 1.0) * np.log1p(xp / c))
        case KernelKind.GAMMA:
            k, mu = params["gamma_shape"], params["gamma_mean"]
            v = special.gammainc(k, (k / mu) * xp)
    out: Floats = np.where(pos, v, 0.0)
    return out


# --------------------------------------------------------------------------
# ψ validation
# --------------------------------------------------------------------------

_POSITIVE: Final = frozenset(
    {"exp_rate", "power_c", "gamma_shape", "gamma_mean", "period"}
)


def check_psi(
    structure: Structure, psi: PsiAssignment, *, allow_off_grid: bool = False
) -> tuple[dict[PsiSlot, float], ...]:
    """Validate ψ against the structure; return it as plain float dicts.

    Every slot of every feature must be assigned (and nothing else), every
    value finite and in its domain, and unless ``allow_off_grid`` on its grid.
    """
    if len(psi) != len(structure.features):
        raise PsiAssignmentError(
            f"{len(psi)} ψ mappings for {len(structure.features)} features"
        )
    out: list[dict[PsiSlot, float]] = []
    for k, (feature, mapping) in enumerate(zip(structure.features, psi, strict=True)):
        want = psi_slots(feature)
        have = set(mapping)
        missing = [s for s in want if s not in have]
        extra = sorted(have - set(want))
        if missing or extra:
            raise PsiAssignmentError(
                f"feature {k}: missing ψ {missing}, unexpected ψ {extra}"
            )
        clean: dict[PsiSlot, float] = {}
        for slot in want:
            v = float(mapping[slot])
            if not math.isfinite(v):
                raise PsiAssignmentError(f"feature {k}: {slot} = {v} is not finite")
            if slot.name in _POSITIVE and v <= 0:
                raise PsiAssignmentError(f"feature {k}: {slot} = {v} must be > 0")
            if slot.name == "power_p" and v <= 1:
                raise PsiAssignmentError(f"feature {k}: {slot} = {v} must be > 1")
            if not allow_off_grid and v not in grid(slot.name):
                raise OffGridParameterError(
                    f"feature {k}: {slot} = {v!r} is not on grid {grid(slot.name)}"
                )
            clean[slot] = v
        out.append(clean)
    return tuple(out)


# --------------------------------------------------------------------------
# Evaluation context
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Ctx:
    log: EventLog
    channels: Mapping[str, ChannelSpec]

    def raw(self, channel: str) -> Floats:
        marks = self.log.marks.get(channel)
        if marks is None:
            raise FeatureDataError(f"event log has no mark channel {channel!r}")
        return marks

    def z(self, channel: str) -> Floats:
        spec = self.channels[channel]
        if not (math.isfinite(spec.scale) and spec.scale > 0):
            raise FeatureDataError(f"channel {channel!r} has scale {spec.scale}")
        out: Floats = (self.raw(channel) - spec.location) / spec.scale
        return out


def _local(psi: Mapping[PsiSlot, float], path: tuple[int, ...]) -> dict[str, float]:
    return {s.name: v for s, v in psi.items() if s.path == path}


def _prepare(
    structure: Structure,
    psi: PsiAssignment,
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    allow_off_grid: bool,
) -> tuple[_Ctx, tuple[dict[PsiSlot, float], ...]]:
    validate(structure, channels)
    clean = check_psi(structure, psi, allow_off_grid=allow_off_grid)
    return _Ctx(log, {c.name: c for c in channels}), clean


# --------------------------------------------------------------------------
# Marks and sources
# --------------------------------------------------------------------------


def _mark_values(mark: MarkFn, params: Mapping[str, float], ctx: _Ctx) -> Floats:
    n = ctx.log.n
    match mark:
        case One():
            return np.ones(n)
        case Mark(channel=c):
            return np.array(ctx.raw(c), dtype=np.float64)
        case Pow(channel=c):
            m = ctx.raw(c)
            loc = ctx.channels[c].location
            if loc <= 0 or np.any(m <= 0):
                raise FeatureDataError(f"Pow needs positive marks and location: {c}")
            out: Floats = np.exp(params["pow_exponent"] * np.log(m / loc))
            return out
        case ExpOf(channel=c):
            arg = params["exp_coef"] * ctx.z(c)
            if arg.size and float(np.max(arg)) > MAX_EXP_ARG:
                raise FeatureNumericsError(
                    f"ExpOf({c}) overflows: exponent {float(np.max(arg))}"
                )
            out = np.exp(arg)
            return out
        case Above(channel=c):
            out = (ctx.z(c) > params["above_z"]).astype(np.float64)
            return out


def _source_mask(source: Source, ctx: _Ctx) -> Bools:
    if source.kind is SourceKind.ALL or source.channel is None:
        return np.ones(ctx.log.n, dtype=np.bool_)
    s = ctx.raw(source.channel)
    if not np.all((s == 1.0) | (s == -1.0)):
        raise FeatureDataError(
            f"sign channel {source.channel!r} holds values other than ±1"
        )
    want = 1.0 if source.kind is SourceKind.POSITIVE else -1.0
    mask: Bools = s == want
    return mask


# --------------------------------------------------------------------------
# History sums: Σ_{tⱼ < t} wⱼ f(t - tⱼ)
# --------------------------------------------------------------------------


def _exp_history(
    beta: float, times: Floats, w: Floats, t: Floats, *, cdf: bool
) -> Floats:
    """O(n + m log n) via the recursion ``Sᵢ = wᵢ + Sᵢ₋₁ e^{-β(tᵢ - tᵢ₋₁)}``.

    ``Sᵢ`` and the running mark total ``Wᵢ`` are folded in event order, so the
    result does not depend on any CPU-chosen summation order.
    """
    out = np.zeros(t.size)
    if times.size == 0:
        return out
    decay = np.exp(-beta * np.diff(times))
    s = np.empty(times.size)
    running = np.empty(times.size)
    acc = 0.0
    mass = 0.0
    for i in range(times.size):
        acc = (acc * float(decay[i - 1]) if i else 0.0) + float(w[i])
        mass += float(w[i])
        s[i] = acc
        running[i] = mass
    k = np.searchsorted(times, t, side="left")
    has = k > 0
    idx = k[has] - 1
    tail = s[idx] * np.exp(-beta * (t[has] - times[idx]))
    if cdf:
        out[has] = running[idx] - tail
    else:
        out[has] = beta * tail
    return out


#: Largest integer gamma shape handled by the moment recursion.
_MAX_RECURSIVE_SHAPE: Final = 20


def _moment_history(
    rate: float, order: int, times: Floats, w: Floats, t: Floats
) -> Floats:
    """``Mₗ(t) = Σ_{tⱼ < t} wⱼ (t - tⱼ)ˡ e^{-r(t - tⱼ)}``, l = 0..order, in O(n + m).

    The state at event i, ``Aₗ⁽ⁱ⁾`` (the same sum at ``tᵢ``, event i included),
    moves to the next event by the binomial expansion of
    ``(tᵢ₊₁ - tⱼ)ˡ = Σₐ C(l, a) Δˡ⁻ᵃ (tᵢ - tⱼ)ᵃ``. Every term is non-negative,
    so there is no cancellation; folds run in a fixed order. Shape
    ``(order + 1, len(t))``.
    """
    out = np.zeros((order + 1, t.size))
    n = times.size
    if n == 0:
        return out
    binom = [
        [float(math.comb(deg, a)) for a in range(order + 1)] for deg in range(order + 1)
    ]
    state = np.zeros((n, order + 1))
    prev = [0.0] * (order + 1)
    for i in range(n):
        if i == 0:
            cur = [0.0] * (order + 1)
        else:
            gap = float(times[i] - times[i - 1])
            decay = math.exp(-rate * gap)
            powers = [gap**q for q in range(order + 1)]
            cur = [
                decay
                * math.fsum(
                    binom[deg][a] * powers[deg - a] * prev[a] for a in range(deg + 1)
                )
                for deg in range(order + 1)
            ]
        cur[0] += float(w[i])
        state[i] = cur
        prev = cur
    k = np.searchsorted(times, t, side="left")
    has = k > 0
    idx = k[has] - 1
    lag = t[has] - times[idx]
    decay_q = np.exp(-rate * lag)
    lag_powers = [lag**q for q in range(order + 1)]
    for deg in range(order + 1):
        acc = np.zeros(lag.size)
        for a in range(deg + 1):
            acc = acc + binom[deg][a] * lag_powers[deg - a] * state[idx, a]
        out[deg, has] = decay_q * acc
    return out


def _gamma_history(
    params: Mapping[str, float], times: Floats, w: Floats, t: Floats, *, cdf: bool
) -> Floats:
    """Gamma kernel with integer shape k via :func:`_moment_history`.

    Density ``rᵏ xᵏ⁻¹ e^{-rx} / (k-1)!``; CDF ``1 - e^{-rx} Σ_{l<k} (rx)ˡ / l!``.
    """
    k = round(params["gamma_shape"])
    rate = k / params["gamma_mean"]
    moments = _moment_history(rate, k - 1, times, w, t)
    if not cdf:
        out: Floats = rate**k / math.factorial(k - 1) * moments[k - 1]
        return out
    running = np.array([0.0, *accumulate(float(x) for x in w)])
    mass = running[np.searchsorted(times, t, side="left")]
    tail = np.zeros(t.size)
    for deg in range(k):
        tail = tail + rate**deg / math.factorial(deg) * moments[deg]
    out = mass - tail
    return out


def _direct_history(
    fn: Callable[[Floats], None], times: Floats, w: Floats, t: Floats
) -> Floats:
    """Direct O(n·m) sums, folded event by event in event order.

    Each event j adds ``wⱼ f(t - tⱼ)`` to every query strictly after it, one
    elementwise (correctly rounded) add per query, so every entry is the same
    sequential sum whatever the CPU. ``fn`` maps a buffer of positive lags to
    kernel values **in place**. Memory is O(m).
    """
    out = np.zeros(t.size)
    if times.size == 0 or t.size == 0:
        return out
    order = np.argsort(t, kind="stable")
    ts = t[order]
    first = np.searchsorted(ts, times, side="right")  # first query after tⱼ
    acc = np.zeros(ts.size)
    buffer = np.empty(ts.size)
    for j in range(times.size):
        start = int(first[j])
        if start == ts.size:
            break
        wj = float(w[j])
        if wj == 0.0:
            continue
        buf = buffer[: ts.size - start]
        np.subtract(ts[start:], times[j], out=buf)
        fn(buf)
        np.multiply(buf, wj, out=buf)
        np.add(acc[start:], buf, out=acc[start:])
    out[order] = acc
    return out


def _lomax_into(c: float, p: float, *, cdf: bool) -> Callable[[Floats], None]:
    """In-place Lomax density or CDF, with the same operations, in the same
    order, as :func:`kernel_pdf` / :func:`kernel_cdf` (identical values)."""
    coef = (p - 1.0) / c

    def pdf(buf: Floats) -> None:
        np.divide(buf, c, out=buf)
        np.log1p(buf, out=buf)
        np.multiply(buf, -p, out=buf)
        np.exp(buf, out=buf)
        np.multiply(buf, coef, out=buf)

    def cdf_(buf: Floats) -> None:
        np.divide(buf, c, out=buf)
        np.log1p(buf, out=buf)
        np.multiply(buf, -(p - 1.0), out=buf)
        np.expm1(buf, out=buf)
        np.negative(buf, out=buf)

    return cdf_ if cdf else pdf


def _history(
    kernel: KernelKind,
    params: Mapping[str, float],
    times: Floats,
    w: Floats,
    t: Floats,
    *,
    cdf: bool,
) -> Floats:
    """``Σ_{tⱼ < t} wⱼ K(t - tⱼ)`` (or the CDF ``K̄``) at each t.

    Exponential: O(n) recursion. Gamma with integer shape (every grid value):
    O(n) moment recursion. Lomax, and gamma off the integer shapes: direct.
    """
    if kernel is KernelKind.EXP:
        return _exp_history(params["exp_rate"], times, w, t, cdf=cdf)
    if kernel is KernelKind.POWER:
        fn = _lomax_into(params["power_c"], params["power_p"], cdf=cdf)
        return _direct_history(fn, times, w, t)
    shape = params["gamma_shape"]
    if shape == round(shape) and 1 <= shape <= _MAX_RECURSIVE_SHAPE:
        return _gamma_history(params, times, w, t, cdf=cdf)
    f = kernel_cdf if cdf else kernel_pdf

    def generic(buf: Floats) -> None:
        buf[...] = f(kernel, params, buf)

    return _direct_history(generic, times, w, t)


def _excite(
    feature: Excite,
    psi: Mapping[PsiSlot, float],
    path: tuple[int, ...],
    ctx: _Ctx,
    t: Floats,
    *,
    cdf: bool,
) -> Floats:
    mask = _source_mask(feature.source, ctx)
    w = _mark_values(feature.mark, _local(psi, (*path, 1)), ctx)[mask]
    times = ctx.log.times[mask]
    kparams = _local(psi, (*path, 0))
    return _history(feature.kernel, kparams, times, w, t, cdf=cdf)[:, None]


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------


def _cond_at(cond: Cond, params: Mapping[str, float], ctx: _Ctx, t: Floats) -> Bools:
    match cond:
        case LastMarkAbove(channel=c):
            z = ctx.z(c)
            k = np.searchsorted(ctx.log.times, t, side="left")
            on = np.zeros(t.size, dtype=np.bool_)
            has = k > 0
            on[has] = z[k[has] - 1] > params["above_z"]
            return on
        case PhaseWindow():
            u = t / params["period"] - params["phase"] / (2.0 * math.pi)
            frac: Bools = (u - np.floor(u)) <= 0.5
            return frac


def _cond_intervals(
    cond: Cond, params: Mapping[str, float], ctx: _Ctx
) -> tuple[Floats, Floats]:
    """Sorted, non-overlapping intervals ``[a, b]`` ⊂ [0, T] where ``cond`` holds."""
    horizon = ctx.log.horizon
    match cond:
        case LastMarkAbove(channel=c):
            times = ctx.log.times
            on = ctx.z(c) > params["above_z"]
            ends = np.append(times[1:], horizon)
            a, b = times[on], ends[on]
        case PhaseWindow():
            return _phase_intervals(params["period"], params["phase"], horizon)
    keep = b > a
    return a[keep], b[keep]


def _phase_intervals(
    period: float, phase: float, horizon: float
) -> tuple[Floats, Floats]:
    """Where ``sin(2πt/P - φ) ≥ 0`` on [0, T]: ``[P(j + φ/2π), P(j + φ/2π + ½)]``."""
    offset = period * phase / (2.0 * math.pi)
    j0 = math.floor(-offset / period) - 1
    j1 = math.ceil((horizon - offset) / period) + 1
    starts = offset + period * np.arange(j0, j1 + 1, dtype=np.float64)
    a = np.clip(starts, 0.0, horizon)
    b = np.clip(starts + 0.5 * period, 0.0, horizon)
    keep = b > a
    return a[keep], b[keep]


def _intersect(
    x: tuple[Floats, Floats], y: tuple[Floats, Floats]
) -> tuple[Floats, Floats]:
    """Intersection of two sorted interval unions (two-pointer merge)."""
    (xa, xb), (ya, yb) = x, y
    out_a: list[float] = []
    out_b: list[float] = []
    i = j = 0
    while i < xa.size and j < ya.size:
        lo = max(float(xa[i]), float(ya[j]))
        hi = min(float(xb[i]), float(yb[j]))
        if hi > lo:
            out_a.append(lo)
            out_b.append(hi)
        if xb[i] < yb[j]:
            i += 1
        else:
            j += 1
    return np.array(out_a, dtype=np.float64), np.array(out_b, dtype=np.float64)


# --------------------------------------------------------------------------
# Column values and antiderivatives
# --------------------------------------------------------------------------


def _values(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    path: tuple[int, ...],
    ctx: _Ctx,
    t: Floats,
) -> Floats:
    """``(len(t), n_columns(feature))`` column values at ``t`` (left limits)."""
    match feature:
        case Excite():
            return _excite(feature, psi, path, ctx, t, cdf=False)
        case Periodic():
            arg = 2.0 * math.pi * t / psi[PsiSlot(path, "period")]
            return np.stack([np.sin(arg), np.cos(arg)], axis=1)
        case Trend():
            out: Floats = (t / ctx.log.horizon)[:, None]
            return out
        case Product(left=left, right=right):
            a = _values(left, psi, (*path, 0), ctx, t)
            b = _values(right, psi, (*path, 1), ctx, t)
            width = a.shape[1] * b.shape[1]
            out = (a[:, :, None] * b[:, None, :]).reshape(t.size, width)
            return out
        case Gate(feature=inner, cond=cond):
            cols = _values(inner, psi, (*path, 0), ctx, t)
            on = _cond_at(cond, _local(psi, (*path, 1)), ctx, t)
            out = np.where(on[:, None], cols, 0.0)
            return out


def _gate_chain(
    feature: Feature, path: tuple[int, ...]
) -> tuple[Feature, tuple[int, ...], list[tuple[Cond, tuple[int, ...]]]] | None:
    """``Gate(…Gate(leaf, c₁)…, cₖ)`` → (leaf, its path, conds).

    None if a ``Product`` sits under the gates (no closed form)."""
    conds: list[tuple[Cond, tuple[int, ...]]] = []
    while isinstance(feature, Gate):
        conds.append((feature.cond, (*path, 1)))
        feature, path = feature.feature, (*path, 0)
    if isinstance(feature, Product):
        return None
    return feature, path, conds


def has_closed_form(feature: Feature) -> bool:
    """Whether the feature's column integrals are closed form: a leaf under
    any chain of gates. Anything under a ``Product`` is integrated by quadrature."""
    return _gate_chain(feature, ()) is not None


def _leaf_antiderivative(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    path: tuple[int, ...],
    ctx: _Ctx,
    t: Floats,
) -> Floats:
    """``∫₀ᵗ`` of a leaf's columns, in closed form."""
    match feature:
        case Excite():
            return _excite(feature, psi, path, ctx, t, cdf=True)
        case Periodic():
            period = psi[PsiSlot(path, "period")]
            arg = 2.0 * math.pi * t / period
            scale = period / (2.0 * math.pi)
            # 1 - cos(x) = 2 sin²(x/2), without cancellation near x = 0.
            first = 2.0 * scale * np.sin(0.5 * arg) ** 2
            return np.stack([first, scale * np.sin(arg)], axis=1)
        case Trend():
            out: Floats = (t * t / (2.0 * ctx.log.horizon))[:, None]
            return out
        case Product() | Gate():
            raise AssertionError("not a leaf")


def _running_rows(rows: Floats) -> Floats:
    """Exclusive running totals of ``rows`` along axis 0, folded in row order."""
    out = np.zeros((rows.shape[0] + 1, rows.shape[1]))
    for i in range(rows.shape[0]):
        out[i + 1] = out[i] + rows[i]
    return out


def _antiderivative(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    ctx: _Ctx,
    t: Floats,
) -> Floats | None:
    """``∫₀ᵗ`` of every column in closed form, or None if a Product is involved."""
    chain = _gate_chain(feature, ())
    if chain is None:
        return None
    leaf, leaf_path, conds = chain
    if not conds:
        return _leaf_antiderivative(leaf, psi, leaf_path, ctx, t)
    on: tuple[Floats, Floats] = (
        np.array([0.0]),
        np.array([ctx.log.horizon]),
    )
    for cond, cpath in conds:
        on = _intersect(on, _cond_intervals(cond, _local(psi, cpath), ctx))
    a, b = on
    ncol = n_columns(leaf)
    if a.size == 0:
        return np.zeros((t.size, ncol))
    ca = _leaf_antiderivative(leaf, psi, leaf_path, ctx, a)
    cb = _leaf_antiderivative(leaf, psi, leaf_path, ctx, b)
    before = _running_rows(cb - ca)
    i = np.searchsorted(a, t, side="left")  # intervals starting strictly before t
    out = np.zeros((t.size, ncol))
    has = i > 0
    last = i[has] - 1
    upto = np.minimum(b[last], t[has])
    out[has] = (
        before[last] + _leaf_antiderivative(leaf, psi, leaf_path, ctx, upto) - ca[last]
    )
    return out


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


def _sublabels(feature: Feature) -> tuple[str, ...]:
    match feature:
        case Excite() | Trend():
            return ("",)
        case Periodic():
            return ("sin", "cos")
        case Product(left=left, right=right):
            return tuple(
                "*".join(p for p in (x, y) if p)
                for x in _sublabels(left)
                for y in _sublabels(right)
            )
        case Gate(feature=inner):
            return _sublabels(inner)


def _labels(structure: Structure) -> tuple[str, ...]:
    out = ["θ0"]
    for k, feature in enumerate(structure.features, start=1):
        for sub in _sublabels(feature):
            out.append(f"φ{k}.{sub}" if sub else f"φ{k}")
    return tuple(out)


def _layout(structure: Structure) -> tuple[tuple[int, ...], ...]:
    cols: list[tuple[int, ...]] = []
    start = 1
    for feature in structure.features:
        width = n_columns(feature)
        cols.append(tuple(range(start, start + width)))
        start += width
    return tuple(cols)


# --------------------------------------------------------------------------
# Quadrature
# --------------------------------------------------------------------------


def _walk(
    feature: Feature, path: tuple[int, ...]
) -> list[tuple[Feature, tuple[int, ...]]]:
    match feature:
        case Product(left=left, right=right):
            return [
                (feature, path),
                *_walk(left, (*path, 0)),
                *_walk(right, (*path, 1)),
            ]
        case Gate(feature=inner):
            return [(feature, path), *_walk(inner, (*path, 0))]
        case Excite() | Periodic() | Trend():
            return [(feature, path)]


def _sharpest_on_grid(kernel: KernelKind) -> float:
    """The smallest :func:`kernel_scale` over the kernel's ψ grid."""
    names = KERNEL_PSI[kernel]
    combos: list[dict[str, float]] = [{}]
    for name in names:
        combos = [{**c, name: v} for c in combos for v in grid(name)]
    return min(kernel_scale(kernel, c) for c in combos)


def _rule_inputs(
    structure: Structure,
    psi: tuple[dict[PsiSlot, float], ...] | None,
    horizon: float,
) -> tuple[float | None, list[float], list[Floats]]:
    """Sharpest kernel scale, shortest period, and every PhaseWindow switch.

    Taken over the **whole ψ grid** of each node the structure contains (plus
    ``psi`` itself, which matters only off the grid), so for on-grid ψ the
    rule depends on the structure alone and per-feature columns can be cached
    across a profile over ψ.
    """
    scales: list[float] = []
    periods: list[float] = []
    switches: list[Floats] = []
    for k, feature in enumerate(structure.features):
        slots = psi[k] if psi is not None else None
        for node, path in _walk(feature, ()):
            if isinstance(node, Excite):
                scales.append(_sharpest_on_grid(node.kernel))
                if slots is not None:
                    params = _local(slots, (*path, 0))
                    scales.append(kernel_scale(node.kernel, params))
            elif isinstance(node, Periodic):
                periods.append(min(grid("period")))
                if slots is not None:
                    periods.append(slots[PsiSlot(path, "period")])
            elif isinstance(node, Gate) and isinstance(node.cond, PhaseWindow):
                combos = [(p, f) for p in grid("period") for f in grid("phase")]
                if slots is not None:
                    params = _local(slots, (*path, 1))
                    combos.append((params["period"], params["phase"]))
                for period, phase in combos:
                    periods.append(period)
                    switches.extend(_phase_intervals(period, phase, horizon))
    return (min(scales) if scales else None), periods, switches


def _rule(
    structure: Structure,
    psi: tuple[dict[PsiSlot, float], ...] | None,
    data: _Unpacked,
    spec: QuadratureSpec,
    extra: Floats,
) -> tuple[Floats, Floats]:
    horizon = data.log.horizon
    times = data.log.times
    scale, periods, switches = _rule_inputs(structure, psi, horizon)
    cuts: list[Floats] = [np.array([0.0, horizon]), times, extra, *switches]
    cuts.extend([data.ex_a, data.ex_b])
    if scale is not None and times.size:
        gaps = np.append(times[1:], horizon) - times
        longest = float(np.max(gaps))
        first = spec.first_panel * scale
        if longest > first:
            count = math.ceil(math.log(longest / first) / math.log(spec.growth)) + 1
            offsets = first * spec.growth ** np.arange(count, dtype=np.float64)
            grid_pts = times[:, None] + offsets[None, :]
            cuts.append(grid_pts[offsets[None, :] < gaps[:, None]])
    edges = np.unique(np.concatenate(cuts))
    edges = edges[(edges >= 0.0) & (edges <= horizon)]
    # Switch points of different grid (period, phase) pairs can coincide up to
    # rounding; merge edges closer than this so no panel is degenerate.
    tol = _EDGE_TOL * max(1.0, horizon)
    edges = edges[np.concatenate([[True], np.diff(edges) > tol])]
    edges[-1] = horizon
    h = spec.max_panel
    for p in periods:
        h = min(h, p / spec.panels_per_period)
    widths = np.diff(edges)
    pieces = np.maximum(1, np.ceil(widths / h)).astype(np.intp)
    starts = np.repeat(edges[:-1], pieces)
    lengths = np.repeat(widths / pieces, pieces)
    first_piece = np.array([0, *accumulate(pieces.tolist())][:-1], dtype=np.intp)
    within = np.arange(starts.size) - np.repeat(first_piece, pieces)
    a = starts + within * lengths
    keep = lengths > 0
    a, lengths = a[keep], lengths[keep]
    x, wx = np.polynomial.legendre.leggauss(spec.nodes_per_panel)
    nodes = (a[:, None] + lengths[:, None] * (0.5 * (x[None, :] + 1.0))).ravel()
    weights = (0.5 * lengths[:, None] * wx[None, :]).ravel()
    # Window ends are panel edges, so a node is strictly in or strictly out.
    keep_nodes = ~_inside(nodes, data.ex_a, data.ex_b)
    return nodes[keep_nodes], weights[keep_nodes]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Unpacked:
    log: EventLog
    rows: Ints  # indices of the events counted in Σ log λ
    ex_a: Floats  # excluded windows [a, b], sorted and disjoint
    ex_b: Floats


def _inside(t: Floats, a: Floats, b: Floats) -> Bools:
    """Whether each t lies in one of the closed intervals ``[a_k, b_k]``."""
    k = np.searchsorted(a, t, side="right") - 1
    out = np.zeros(t.size, dtype=np.bool_)
    has = k >= 0
    out[has] = t[has] <= b[k[has]]
    return out


def _unpack(data: Data) -> _Unpacked:
    if isinstance(data, EventLog):
        empty = np.empty(0)
        return _Unpacked(data, np.arange(data.n, dtype=np.intp), empty, empty)
    a = np.array([w[0] for w in data.excluded], dtype=np.float64)
    b = np.array([w[1] for w in data.excluded], dtype=np.float64)
    keep = data.endogenous & ~_inside(data.log.times, a, b)
    return _Unpacked(data.log, np.flatnonzero(keep).astype(np.intp), a, b)


def _excluded_cumulative(
    anti: Callable[[Floats], Floats], t: Floats, ex_a: Floats, ex_b: Floats
) -> Floats:
    """``∫₀ᵗ`` minus the excluded windows, from a cumulative ``anti`` (``∫₀ᵗ``).

    ``F(t) - Σₖ [F(min(bₖ, t)) - F(min(aₖ, t))]``, each entry folded exactly.
    """
    base = anti(t)
    if ex_a.size == 0:
        return base
    terms = [base]
    for a, b in zip(ex_a.tolist(), ex_b.tolist(), strict=True):
        terms.append(anti(np.minimum(a, t)))
        terms.append(-anti(np.minimum(b, t)))
    stacked = np.stack(terms, axis=2)  # (len(t), w, 1 + 2K)
    rows, width = base.shape
    flat = reductions.row_totals(stacked.reshape(rows * width, -1))
    out: Floats = flat.reshape(rows, width)
    return out


def _feature_values(
    feature: Feature, psi: Mapping[PsiSlot, float], ctx: _Ctx, t: Floats
) -> Floats:
    out = _values(feature, psi, (), ctx, t)
    if not np.all(np.isfinite(out)):
        raise FeatureNumericsError(f"a value of {feature} is not finite")
    return out


def _matrix(
    structure: Structure,
    psi: tuple[dict[PsiSlot, float], ...],
    ctx: _Ctx,
    t: Floats,
) -> Floats:
    blocks = [np.ones((t.size, 1))]
    for feature, slots in zip(structure.features, psi, strict=True):
        blocks.append(_feature_values(feature, slots, ctx, t))
    return np.concatenate(blocks, axis=1)


def _as_times(t: npt.ArrayLike, horizon: float | None) -> Floats:
    """Query times as a flat float array; within ``[0, horizon]`` if given."""
    arr = np.array(t, dtype=np.float64).reshape(-1)
    if not np.all(np.isfinite(arr)):
        raise FeatureDataError("query times must be finite")
    if horizon is not None and not np.all((arr >= 0.0) & (arr <= horizon)):
        raise FeatureDataError(f"times must lie in [0, {horizon}]")
    return arr


def _frozen(*arrays: npt.NDArray[np.generic]) -> None:
    for a in arrays:
        a.setflags(write=False)


def evaluate_columns(
    structure: Structure,
    psi: PsiAssignment,
    data: Data,
    channels: tuple[ChannelSpec, ...],
    t: npt.ArrayLike,
    *,
    allow_off_grid: bool = False,
) -> Floats:
    """``(len(t), p)`` design columns at times ``t`` (any order).

    Each row uses every event of the log strictly before its time (forced
    events included). Column 0 is the intercept.
    """
    log = _unpack(data).log
    ctx, clean = _prepare(structure, psi, log, channels, allow_off_grid)
    return _matrix(structure, clean, ctx, _as_times(t, None))


def cumulative_columns(
    structure: Structure,
    psi: PsiAssignment,
    data: Data,
    channels: tuple[ChannelSpec, ...],
    t: npt.ArrayLike,
    *,
    allow_off_grid: bool = False,
) -> Floats | None:
    """``(len(t), p)`` closed-form integrals of every column over ``[0, t]``
    minus the excluded windows (t in [0, T]).

    None if any feature has no closed form (one under a ``Product``).
    """
    u = _unpack(data)
    ctx, clean = _prepare(structure, psi, u.log, channels, allow_off_grid)
    tt = _as_times(t, u.log.horizon)

    def intercept(x: Floats) -> Floats:
        out: Floats = x[:, None]
        return out

    blocks = [_excluded_cumulative(intercept, tt, u.ex_a, u.ex_b)]
    for feature, slots in zip(structure.features, clean, strict=True):
        if not has_closed_form(feature):
            return None

        def anti(
            x: Floats, feature: Feature = feature, slots: dict[PsiSlot, float] = slots
        ) -> Floats:
            block = _antiderivative(feature, slots, ctx, x)
            if block is None:
                raise AssertionError("closed form checked above")
            return block

        blocks.append(_excluded_cumulative(anti, tt, u.ex_a, u.ex_b))
    out = np.concatenate(blocks, axis=1)
    if not np.all(np.isfinite(out)):
        raise FeatureNumericsError("a column integral is not finite")
    return out


def quadrature_rule(
    structure: Structure,
    data: Data,
    *,
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
    breakpoints: npt.ArrayLike = (),
    psi: PsiAssignment | None = None,
    allow_off_grid: bool = False,
) -> tuple[Floats, Floats]:
    """Nodes (sorted, interior to panels) and weights of the rule on ``[0, T]``
    minus the data's excluded windows.

    Depends on the structure, the data and the ψ **grid**, not on the ψ
    values, so every on-grid ψ of one structure shares one rule. Pass an
    off-grid ``psi`` (with ``allow_off_grid``) to resolve its scales too.
    ``breakpoints`` (in [0, T]) become panel edges, so the nodes below any of
    them form the rule on ``[0, breakpoint]``.
    """
    u = _unpack(data)
    clean = None
    if psi is not None:
        clean = check_psi(structure, psi, allow_off_grid=allow_off_grid)
    extra = _as_times(breakpoints, u.log.horizon)
    return _rule(structure, clean, u, quadrature, extra)


@dataclass(frozen=True, eq=False)
class FeatureBlock:
    """One feature's columns for one ψ: the cacheable unit of a :class:`Design`.

    A feature's columns depend only on that feature's ψ, and the rule only on
    the structure and the data, so a profile over ψ can compute each
    (feature, ψₖ) block once and :func:`assemble` designs from them.
    """

    at_events: Floats  # (counted events, w)
    at_nodes: Floats  # (m, w)
    integrals: Floats  # (w,)
    exact: bool


def feature_block(
    feature: Feature,
    psi: Mapping[PsiSlot, float],
    data: Data,
    channels: tuple[ChannelSpec, ...],
    nodes: Floats,
    weights: Floats,
    *,
    allow_off_grid: bool = False,
) -> FeatureBlock:
    """The block of ``feature`` at ψ, on the rule ``(nodes, weights)``."""
    u = _unpack(data)
    single = Structure((feature,))
    ctx, clean = _prepare(single, (psi,), u.log, channels, allow_off_grid)
    slots = clean[0]
    at_events = _feature_values(feature, slots, ctx, u.log.times[u.rows])
    at_nodes = _feature_values(feature, slots, ctx, nodes)
    exact = has_closed_form(feature)
    if exact:

        def anti(x: Floats) -> Floats:
            block = _antiderivative(feature, slots, ctx, x)
            if block is None:
                raise AssertionError("closed form checked above")
            return block

        end = np.array([u.log.horizon])
        integrals = _excluded_cumulative(anti, end, u.ex_a, u.ex_b)[0]
    else:
        width = at_nodes.shape[1]
        integrals = np.array(
            [reductions.total(weights * at_nodes[:, c]) for c in range(width)]
        )
    if not np.all(np.isfinite(integrals)):
        raise FeatureNumericsError(f"an integral of {feature} is not finite")
    _frozen(at_events, at_nodes, integrals)
    return FeatureBlock(at_events, at_nodes, integrals, exact)


def assemble(
    structure: Structure,
    blocks: tuple[FeatureBlock, ...],
    data: Data,
    nodes: Floats,
    weights: Floats,
) -> Design:
    """A :class:`Design` from one block per feature, in feature order.

    The blocks must have been computed on this data and this rule.
    """
    u = _unpack(data)
    layout = _layout(structure)
    n = u.rows.size
    if len(blocks) != len(layout) or any(
        b.at_events.shape != (n, len(cols))
        or b.at_nodes.shape != (nodes.size, len(cols))
        for b, cols in zip(blocks, layout, strict=False)
    ):
        raise FeatureDataError("blocks do not match the structure, data or rule")
    ones_e = np.ones((n, 1))
    ones_n = np.ones((nodes.size, 1))
    at_events = np.concatenate([ones_e, *(b.at_events for b in blocks)], axis=1)
    at_nodes = np.concatenate([ones_n, *(b.at_nodes for b in blocks)], axis=1)

    def identity(x: Floats) -> Floats:
        out: Floats = x[:, None]
        return out

    span = _excluded_cumulative(identity, np.array([u.log.horizon]), u.ex_a, u.ex_b)
    integrals = np.concatenate([span[0], *(b.integrals for b in blocks)])
    flags = [True]
    for b, cols in zip(blocks, layout, strict=True):
        flags.extend([b.exact] * len(cols))
    exact = np.array(flags, dtype=np.bool_)
    nodes = np.array(nodes, dtype=np.float64)
    weights = np.array(weights, dtype=np.float64)
    rows = np.array(u.rows, dtype=np.intp)
    _frozen(at_events, integrals, exact, nodes, weights, at_nodes, rows)
    return Design(
        labels=_labels(structure),
        at_events=at_events,
        integrals=integrals,
        integral_exact=exact,
        nodes=nodes,
        weights=weights,
        at_nodes=at_nodes,
        feature_columns=layout,
        horizon=u.log.horizon,
        event_index=rows,
    )


def design(
    structure: Structure,
    psi: PsiAssignment,
    data: Data,
    channels: tuple[ChannelSpec, ...],
    *,
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
    allow_off_grid: bool = False,
) -> Design:
    """Precompute everything the likelihood and the fitter need for one ψ.

    ``data`` is a bare :class:`EventLog` (observational) or a
    :class:`Dataset` (forced events are history only; excluded windows leave
    both the event sum and the compensator).
    """
    u = _unpack(data)
    _, clean = _prepare(structure, psi, u.log, channels, allow_off_grid)
    nodes, weights = _rule(structure, clean, u, quadrature, np.empty(0))
    blocks = tuple(
        feature_block(
            feature,
            slots,
            data,
            channels,
            nodes,
            weights,
            allow_off_grid=allow_off_grid,
        )
        for feature, slots in zip(structure.features, clean, strict=True)
    )
    return assemble(structure, blocks, data, nodes, weights)
