r"""Wiener-Hopf kernel estimation: the model-free reference (SPEC §2.3).

A linear (marked) Hawkes process is a linear filter driven by point processes.
Following Bacry & Muzy (2016), "First- and second-order statistics
characterization of Hawkes processes and non-parametric estimation" (IEEE
Trans. Inf. Theory 62(4)), its kernels solve a Wiener-Hopf system in the
process's second-order statistics, so they can be estimated from data without
proposing any structure. This module does that for the v2 setting: one target,
several drivers that share event times.

Model
-----
The target is the **arrival** process N (the ground intensity of all events).
The drivers are D signed measures on the same event times ``t_k``:

- ``X_0 = N``: ``dX_0 = Σ_k δ_{t_k}`` (weight 1), driver name ``"arrival"``;
- one per channel c, in the order given, named ``c.name``:
  ``dX_c = Σ_k w_{k,c} δ_{t_k}`` with ``w = (m - location) / scale`` (the
  :class:`~sciagent.glm.grammar.ChannelSpec` standardisation) for positive and
  real channels, and ``w = s ∈ {-1, +1}`` for sign channels.

The estimated filter is ``λ(t) = μ + Σ_d ∫_{(0,∞)} φ_d(u) dX_d(t - u)``. So
``φ_arrival`` is self-excitation and ``φ_size`` is the size→arrival
cross-kernel (S11's signature). In grammar terms, φ_arrival + φ_c is
``Excite(·, One) + Excite(·, Mark(c))`` with unnormalised, free-shape kernels.

The normal equations (the crux)
-------------------------------
Assume stationarity and write ``Λ_d = E dX_d / dt`` and
``S_{ed} = E[Σ_k w_{k,e} w_{k,d}] / T`` (the rate of *simultaneous* weight
products). The cross-covariance measure of the drivers,
``Cov(dX_e(s), dX_d(s+τ)) = (S_{ed} δ(τ) + c_{ed}(τ)) ds dτ``, has a singular
part at τ = 0 **for every pair of drivers**, not just on the diagonal, because
all drivers jump at the same times; ``S_{ed}`` is its weight (``Λ`` for
arrival with arrival, ``Λ E[z]`` for arrival with z, ``Λ E[z²]`` for z with z,
``Λ`` for sign with sign). ``c_{ed}`` is the regular part (pairs of distinct
events).

λ is predictable, so for u > 0, ``E[dX_e(t-u) dN(t)] = E[dX_e(t-u) λ(t)] dt``.
Subtracting means (``Λ_0 = μ + Σ_d ‖φ_d‖ Λ_d``) and substituting the filter,

``c_{e0}(u) = Σ_d S_{ed} φ_d(u) + Σ_d ∫_0^∞ φ_d(v) c_{ed}(u - v) dv``,
for all u > 0 and every driver e.

With one driver this is Bacry-Muzy's ``g = φ + φ * g`` for ``g = c/Λ``. These
are exactly the orthogonality conditions of the least-squares linear predictor
of dN from the drivers' past. The derivation uses only predictability of λ and
second-order stationarity, so it holds for any mark law in which each event's
mark is adapted (known at its event time, possibly history-dependent) and has
``E w² < ∞``, with the process stationary and ergodic so that the empirical
moments converge. It does *not* need marks independent of the past. Their
operator (``S δ + c``, the drivers' covariance operator) is positive
semidefinite.

The estimand, and when it is the truth
--------------------------------------
What is estimated is the **grid-projected best linear predictor**: the φ,
piecewise constant on the lag grid and zero beyond ``max_lag``, that solves the
Galerkin system below. It equals the true kernels only when the truth is a
linear filter in these drivers whose kernels are themselves piecewise constant
on the grid; otherwise it is the Galerkin projection of the truth's normal
equations onto the grid, and it converges to the true φ only as the grid
refines and the support grows. Consistency of the estimate for that projected
estimand needs:

(a) stationarity and ergodicity of the event-and-mark process;
(b) adapted marks with ``E w² < ∞`` (fourth moments for a √n rate);
(c) a true intensity that is nonnegative without clipping (else the "linear"
    truth is not the filter);
(d) G positive definite (see Regularisation);
(e) ridge → 0;
(f) for the projection to be the truth: φ piecewise constant on the grid with
    support ≤ ``max_lag``.

Discretisation (Galerkin, exact pair sums)
------------------------------------------
φ_d is piecewise constant on bins ``[e_j, e_{j+1})`` of the lag grid; the
equations are integrated over each bin (Galerkin with the same indicator
basis, which keeps the matrix symmetric). Entry by entry:

- ``G[(e,i),(d,j)] = S_{ed} Δ_j 1[i=j] + ∫ c_{ed}(τ) K_{ij}(τ) dτ`` with
  ``K_{ij}(τ) = |[e_i, e_{i+1}) ∩ ([e_j, e_{j+1}) + τ)|``, a trapezoid;
- ``B[(e,i)] = ∫_{e_i}^{e_{i+1}} c_{e0}(u) du``;
- ``G φ = B``.

Both are estimated directly from event pairs, with no intermediate histogram
of the covariance density:
``∫ c_{ed} K ≈ Σ_{k≠k'} w_{k,e} w_{k',d} K(t_{k'} - t_k) / (T - |τ|)
- Λ_e Λ_d Δ_i Δ_j``. ``1/(T-|τ|)`` corrects the finite-window edge effect
(the expected number of pairs at lag τ is proportional to ``T - |τ|``).
``K`` is a sum of four ramps ``r(x) = max(x, 0)`` at its kinks, so every entry
is four evaluations of ``E(x) = Σ_p ω_p r(τ_p - x)``. Every kink is a
difference of two edges, so E is needed at only ``(L+1)²`` points; pairs are
sorted by lag, grouped between consecutive kink points, and each E is an exact
(``math.fsum``) fold of group totals: ``O(P log P + D² L⁴)`` with tiny
constants for P pairs. ``build_system`` returns G and B, and a brute-force test
checks them against the definition.

Determinism (invariant 3): every reported fold goes through
:mod:`sciagent.core.reductions` (exactly rounded, order-free). The predictive
intensity adds each query's terms elementwise in a fixed order. The one
library-dispatched step is the dense solve (LAPACK), which is not a fold the
invariant guard covers.

Lag grid and support
--------------------
Lags are in units of the data's mean inter-event time ``T/n`` (so the estimate
is time-scale equivariant). The support is ``[0, max_lag)``; the first bin is
``[0, first_edge)`` and the remaining edges are log-spaced up to ``max_lag``.
Defaults: ``max_lag = 10``, ``first_edge = 0.01``, 24 bins. The support
trades coverage against noise: a null kernel's norm has an sd growing like
``sqrt(max_lag / n)``, because every bin's pair count is noisy (measured on
Poisson arrivals, n ≈ 3000, 20 seeds: 0.038 at max_lag 10, 0.076 at 20). Ten
mean inter-event times hold 99% of ``ExpK`` mass for rates ≥ 0.5 and 92% at
rate 0.25; heavy Lomax tails are cut (see Limits).

``first_edge`` was set by simulation against the sharpest kernels the ψ grid
allows (``glm/grids.py``): ExpK at exp_rate 8 and PowerK at power_c 0.05,
η = 0.5 with a size cross-kernel of 0.4, n ≈ 20000, 8 seeds. With the former
first_edge 0.05, PowerK(c=0.05, p=2) norms were off their grid-converged values
by (-0.006, +0.005), a resolution bias, while a 5x finer first bin with 40 bins
moved them by < 3e-4 from first_edge 0.01. ExpK(8) was already resolved at
0.05. PowerK(p=1.2) and ExpK(0.25) are support-limited (65% and 92% of mass
within 10), not resolution-limited: no grid refinement changes them. The finer
first bin leaves the null-norm sd unchanged (40 seeds, n ≈ 3000: identical to
3 decimals), because that noise is set by the support, not the binning.

Log spacing is chosen over uniform because the grammar's kernels range from
sharp (exp rate 8, Lomax c = 0.05) to heavy-tailed (Lomax p = 1.2): narrow bins
resolve the peak, wide bins keep the tail's per-bin variance bounded (a bin's
variance scales as ``1/(n Δ)``). With non-uniform bins the matrix is not
Toeplitz, which the exact pair sums above do not need. ``lags`` are bin
midpoints; ``edges`` are the bin edges; the kernel is constant on each bin and
zero beyond the last edge.

Regularisation
--------------
G is the drivers' covariance operator tested on the bins. With one driver
(a stable univariate Hawkes process) its spectral density ``Λ / |1 - φ̂(ω)|²``
is at least ``Λ / (1 + ‖φ‖)²``, so the population G is bounded below by a
multiple of ``diag(Δ)``. For several drivers this is **assumed, not proved**:
we require ``S = E[w wᵀ] Λ`` nonsingular (no driver an exact linear combination
of the others) and the population G ≻ 0. Collinear drivers (a constant sign
channel duplicates arrivals) violate it.

The *empirical* Ĝ need not be PSD at all: the ``1/(T - |τ|)`` edge correction
and the subtraction of estimated means each break the Gram structure that makes
the population operator PSD. The ridge ``ridge · diag(S_{dd} Δ_j)``
(dimensionless, fixed in the config: no data-dependent tuning, so no randomness
and no search) covers both cases: it makes a collinear system solvable
(splitting the norm evenly) and keeps a slightly indefinite Ĝ from being
singular. Its bias is O(ridge): it inflates the diagonal by a relative factor
of at most ``ridge``, so norms shrink by ≲ ``ridge · ‖φ‖`` (measured at the
default 1e-3 vs 1e-9, n ≈ 20000: -1.4e-4 self, -3e-4 cross). Variance is
controlled by the bin widths, not by shrinkage, because a larger ridge biases
the norms that the tests and B-np read.

Baseline: ``μ = Λ_0 - Σ_d ‖φ_d‖ Λ_d`` (Bacry-Muzy's ``(I - ‖φ‖) Λ`` for this
single-output case), with the empirical ``Λ_d = Σ_k w_{k,d} / T``.

Limits (stated, not hidden; SPEC §2.3)
--------------------------------------
- Second-order and linear only. It cannot see gates, thresholds, a nonlinear
  link, or mark effects that are not linear in the standardised mark (e.g.
  ``ExpOf``): it returns their best linear approximation. That is where
  structure proposal can beat it.
- Kernels are truncated at ``max_lag``; mass beyond it (a heavy tail) is lost
  and partly absorbed into μ.
- **Negative kernel values are reported, not clipped.** The least-squares
  linear filter is the estimand, a negative value can be real (inhibition, a
  negative mark effect, a sign channel), and clipping would bias the norms of
  noisy-but-null kernels upward. Positivity is enforced where it matters, on
  the predictive intensity: :func:`intensity` returns ``max(λ, floor)`` with
  ``floor = intensity_floor · mean_rate`` (default 1e-6 of the mean rate).
- Stationarity is assumed; trends and periodicity leak into the kernels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from sciagent.core.errors import InferenceError
from sciagent.core.reductions import dot, matvec, row_totals, total
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import ChannelKind, ChannelSpec

type Floats = npt.NDArray[np.float64]
type Ints = npt.NDArray[np.int64]

ARRIVAL: Final = "arrival"


class WienerHopfError(InferenceError):
    """The Wiener-Hopf estimate cannot be formed for these inputs."""


@dataclass(frozen=True)
class WienerHopfConfig:
    """Estimator settings. Lags are in mean inter-event times of the data.

    ``max_lag`` is the support, ``first_edge`` the right edge of the first bin,
    ``n_bins`` the number of bins (log-spaced after the first), ``ridge`` the
    dimensionless Tikhonov strength, ``intensity_floor`` the predictive
    intensity floor relative to the mean rate, ``min_events`` the smallest log
    accepted.
    """

    max_lag: float = 10.0
    first_edge: float = 0.01
    n_bins: int = 24
    ridge: float = 1e-3
    intensity_floor: float = 1e-6
    min_events: int = 50

    def __post_init__(self) -> None:
        if self.n_bins < 1:
            raise WienerHopfError(f"n_bins must be >= 1: {self.n_bins}")
        if not 0.0 < self.first_edge < self.max_lag < np.inf:
            raise WienerHopfError("need 0 < first_edge < max_lag < inf")
        if not 0.0 <= self.ridge < np.inf:
            raise WienerHopfError(f"ridge must be finite and >= 0: {self.ridge}")
        if not 0.0 < self.intensity_floor < 1.0:
            raise WienerHopfError("intensity_floor must be in (0, 1)")
        if self.min_events < 2:
            raise WienerHopfError("min_events must be >= 2")


DEFAULT: Final = WienerHopfConfig()


@dataclass(frozen=True, eq=False)
class KernelEstimate:
    """A fitted linear filter. Arrays are read-only.

    ``kernels[d, j]`` is φ_d (density units: events per unit time per unit
    driver weight) on ``[edges[j], edges[j+1])``; ``lags`` are the bin
    midpoints; ``norms[d] = Σ_j kernels[d, j] Δ_j``. ``floor`` is the absolute
    intensity floor used by :func:`intensity`.
    """

    drivers: tuple[str, ...]
    lags: Floats
    edges: Floats
    kernels: Floats
    norms: Floats
    baseline: float
    mean_rate: float
    floor: float


@dataclass(frozen=True, eq=False)
class WienerHopfSystem:
    """The discretised normal equations ``gram @ vec(φ) = rhs`` (driver-major).

    ``rates[d] = Λ_d`` and ``moments[e, d] = S_{ed}`` as in the module
    docstring; ``edges`` in the data's time units.
    """

    drivers: tuple[str, ...]
    edges: Floats
    gram: Floats
    rhs: Floats
    rates: Floats
    moments: Floats
    mean_rate: float


# --------------------------------------------------------------------------
# Drivers and grid
# --------------------------------------------------------------------------


def driver_weights(
    log: EventLog, channels: tuple[ChannelSpec, ...]
) -> tuple[tuple[str, ...], Floats]:
    """Driver names and the (D, n) weight matrix: arrivals first, then channels."""
    names = [ARRIVAL]
    rows = [np.ones(log.n)]
    for spec in channels:
        if spec.name in names:
            raise WienerHopfError(f"duplicate or reserved driver name {spec.name!r}")
        if spec.name not in log.marks:
            raise WienerHopfError(f"event log has no mark channel {spec.name!r}")
        m = np.asarray(log.marks[spec.name], dtype=np.float64)
        if spec.kind is ChannelKind.SIGN:
            if not np.all(np.abs(m) == 1.0):
                raise WienerHopfError(f"sign channel {spec.name!r} not in {{-1, +1}}")
            rows.append(m.copy())
        else:
            if not spec.scale > 0.0:
                raise WienerHopfError(f"channel {spec.name!r} has scale <= 0")
            rows.append((m - spec.location) / spec.scale)
        names.append(spec.name)
    return tuple(names), np.stack(rows)


def lag_edges(log: EventLog, config: WienerHopfConfig = DEFAULT) -> Floats:
    """Bin edges in the data's time units (see the module docstring)."""
    unit = log.horizon / max(log.n, 1)
    if config.n_bins == 1:
        rel = np.array([0.0, config.max_lag])
    else:
        k = np.arange(config.n_bins, dtype=np.float64) / (config.n_bins - 1)
        rel = np.concatenate(
            [[0.0], config.first_edge * (config.max_lag / config.first_edge) ** k]
        )
        rel[-1] = config.max_lag
    edges: Floats = rel * unit
    return edges


def _pairs(times: Floats, reach: float) -> tuple[Ints, Ints]:
    """All index pairs k < k' with ``t_{k'} - t_k < reach``, offset-major order."""
    idx = np.arange(times.size, dtype=np.int64)
    counts = np.searchsorted(times, times + reach, side="left") - idx - 1
    firsts: list[Ints] = [np.zeros(0, dtype=np.int64)]
    for j in range(1, int(counts.max(initial=0)) + 1):
        firsts.append(idx[counts >= j])
    first = np.concatenate(firsts)
    offsets = np.concatenate(
        [np.full(f.size, j, dtype=np.int64) for j, f in enumerate(firsts)]
    )
    return first, first + offsets


def _ramp_sums(lags: Floats, omega: Floats, at: Floats) -> Floats:
    """``E(c) = Σ_{τ_p > c} ω_p (τ_p - c)`` at each ``c`` of sorted ``at``.

    ``lags`` must be sorted ascending. Pairs are grouped by the interval of
    ``at`` they fall in, each group is summed exactly, and each E is an exact
    fold of the groups above it, so the result is independent of pair order.
    """
    group = np.searchsorted(at, lags, side="left")
    bounds = np.searchsorted(group, np.arange(at.size + 2), side="left")
    weighted = omega * lags
    first_moment = [
        total(weighted[bounds[g] : bounds[g + 1]]) for g in range(at.size + 1)
    ]
    mass = [total(omega[bounds[g] : bounds[g + 1]]) for g in range(at.size + 1)]
    out = np.empty(at.size)
    for m in range(at.size):
        out[m] = math.fsum(first_moment[m + 1 :]) - at[m] * math.fsum(mass[m + 1 :])
    return out


def _check_log(log: EventLog, config: WienerHopfConfig) -> None:
    if log.n < config.min_events:
        raise WienerHopfError(f"{log.n} events; need at least {config.min_events}")
    if config.max_lag * log.horizon / log.n >= log.horizon:
        raise WienerHopfError("support max_lag reaches the horizon; too few events")


# --------------------------------------------------------------------------
# The system
# --------------------------------------------------------------------------


def build_system(
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    *,
    config: WienerHopfConfig = DEFAULT,
) -> WienerHopfSystem:
    """Assemble the Galerkin normal equations from exact pair sums."""
    _check_log(log, config)
    names, w = driver_weights(log, channels)
    n_drv = len(names)
    t, horizon = log.times, log.horizon
    edges = lag_edges(log, config)
    widths = np.diff(edges)
    n_bins = widths.size
    reach = float(edges[-1])

    rates = row_totals(w) / horizon
    moments = (
        np.array([[dot(w[e], w[d]) for d in range(n_drv)] for e in range(n_drv)])
        / horizon
    )
    first, second = _pairs(t, reach)
    tau = t[second] - t[first]
    corr = 1.0 / (horizon - tau)

    # Every kink of every K_ij is a difference of two edges: K_ij has kinks
    # a_i - b_j, a_i - a_j, b_i - b_j, b_i - a_j with signs +, -, -, +, which
    # are diffs[i, j+1], diffs[i, j], diffs[i+1, j+1], diffs[i+1, j].
    diffs = edges[:, None] - edges[None, :]
    kink_values, inverse = np.unique(diffs, return_inverse=True)
    inverse = inverse.reshape(diffs.shape)
    lags_all = np.concatenate([tau, -tau])
    order = np.argsort(lags_all, kind="stable")
    sorted_lags = lags_all[order]

    gram = np.zeros((n_drv, n_bins, n_drv, n_bins))
    for e in range(n_drv):
        for d in range(n_drv):
            # X_e at t_first, X_d at t_second: lag +τ; and the mirrored pairs.
            omega = np.concatenate(
                [w[e, first] * w[d, second] * corr, w[e, second] * w[d, first] * corr]
            )[order]
            ramp = _ramp_sums(sorted_lags, omega, kink_values)[inverse]
            block = ramp[:-1, 1:] - ramp[:-1, :-1] - ramp[1:, 1:] + ramp[1:, :-1]
            block -= rates[e] * rates[d] * np.outer(widths, widths)
            block += np.diag(moments[e, d] * widths)
            gram[e, :, d, :] = block
    flat = gram.reshape(n_drv * n_bins, n_drv * n_bins)
    flat = 0.5 * (flat + flat.T)

    pos = np.argsort(tau, kind="stable")
    bounds = np.searchsorted(tau[pos], edges, side="left")
    rhs = np.zeros((n_drv, n_bins))
    for e in range(n_drv):
        weights = (w[e, first] * corr)[pos]
        summed = [total(weights[bounds[i] : bounds[i + 1]]) for i in range(n_bins)]
        rhs[e] = np.array(summed) - rates[e] * rates[0] * widths
    return WienerHopfSystem(
        drivers=names,
        edges=_frozen(edges),
        gram=_frozen(flat),
        rhs=_frozen(rhs.reshape(-1)),
        rates=_frozen(rates),
        moments=_frozen(moments),
        mean_rate=log.n / horizon,
    )


def estimate_kernels(
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    *,
    config: WienerHopfConfig = DEFAULT,
) -> KernelEstimate:
    """Solve the ridge-regularised Wiener-Hopf system for every driver's kernel."""
    sysm = build_system(log, channels, config=config)
    widths = np.diff(sysm.edges)
    n_drv, n_bins = len(sysm.drivers), widths.size
    diag_moments = np.diag(sysm.moments)
    if np.any(diag_moments <= 0.0):
        bad = [n for n, m in zip(sysm.drivers, diag_moments, strict=True) if m <= 0]
        raise WienerHopfError(f"drivers with all-zero weights: {bad}")
    penalty = config.ridge * np.repeat(diag_moments, n_bins) * np.tile(widths, n_drv)
    try:
        solution = np.linalg.solve(sysm.gram + np.diag(penalty), sysm.rhs)
    except np.linalg.LinAlgError as exc:
        raise WienerHopfError(f"singular Wiener-Hopf system: {exc}") from exc
    kernels = solution.reshape(n_drv, n_bins)
    norms = matvec(kernels, widths)
    baseline = float(sysm.rates[0] - dot(norms, sysm.rates))
    edges = sysm.edges
    return KernelEstimate(
        drivers=sysm.drivers,
        lags=_frozen(0.5 * (edges[:-1] + edges[1:])),
        edges=edges,
        kernels=_frozen(kernels),
        norms=_frozen(norms),
        baseline=baseline,
        mean_rate=sysm.mean_rate,
        floor=config.intensity_floor * sysm.mean_rate,
    )


# --------------------------------------------------------------------------
# Prediction
# --------------------------------------------------------------------------


def _weights_for(
    est: KernelEstimate, log: EventLog, channels: tuple[ChannelSpec, ...]
) -> Floats:
    names, w = driver_weights(log, channels)
    if names != est.drivers:
        raise WienerHopfError(
            f"channels give drivers {names}, estimate has {est.drivers}"
        )
    return w


def _raw_intensity(est: KernelEstimate, w: Floats, times: Floats, q: Floats) -> Floats:
    """``μ + Σ_{t_k < q} Σ_d w_{k,d} φ_d(q - t_k)``, unclipped.

    Each query's terms are added elementwise in a fixed order (past events by
    increasing time, drivers in order), so the value is deterministic; there
    is no CPU-dispatched fold.
    """
    reach = float(est.edges[-1])
    n_bins = est.kernels.shape[1]
    lo = np.searchsorted(times, q - reach, side="right")
    counts = np.maximum(np.searchsorted(times, q, side="left") - lo, 0)
    acc = np.zeros(q.size)
    for j in range(int(counts.max(initial=0))):
        live = counts > j
        k = lo[live] + j
        lag = q[live] - times[k]
        bins = np.clip(np.searchsorted(est.edges, lag, side="right") - 1, 0, n_bins - 1)
        part = acc[live]
        for d in range(w.shape[0]):
            part = part + w[d, k] * est.kernels[d, bins]
        acc[live] = part
    out: Floats = est.baseline + acc
    return out


def intensity(
    est: KernelEstimate,
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    t: npt.ArrayLike,
) -> Floats:
    """Predictive intensity at times ``t`` given ``log``'s history strictly before.

    Returns ``max(λ, est.floor)``: the linear filter can go negative (its
    kernels are not clipped), and a likelihood needs λ > 0.
    """
    q = np.asarray(t, dtype=np.float64).reshape(-1)
    w = _weights_for(est, log, channels)
    out: Floats = np.maximum(_raw_intensity(est, w, log.times, q), est.floor)
    return out


def log_likelihood(
    est: KernelEstimate, log: EventLog, channels: tuple[ChannelSpec, ...]
) -> float:
    """``Σ_i log λ(t_i) - ∫_0^T λ dt`` of ``log`` under the fitted filter.

    The compensator is exact up to floating-point rounding, not approximate:
    with piecewise-constant kernels, λ (and so ``max(λ, floor)``) is constant
    between consecutive breakpoints ``{t_k + e_j}``, so it is integrated by
    evaluating λ at each segment's midpoint. Cost ``O(n L · events per
    support)``.
    """
    w = _weights_for(est, log, channels)
    times, horizon = log.times, log.horizon
    at_events = np.maximum(_raw_intensity(est, w, times, times), est.floor)
    breaks = (times[:, None] + est.edges[None, :]).reshape(-1)
    breaks = np.unique(np.concatenate([[0.0, horizon], breaks[breaks < horizon]]))
    mids = 0.5 * (breaks[:-1] + breaks[1:])
    lam = np.maximum(_raw_intensity(est, w, times, mids), est.floor)
    compensator = dot(lam, np.diff(breaks))
    return total(np.log(at_events)) - compensator


def _frozen(a: Floats) -> Floats:
    out = np.array(a, dtype=np.float64)
    out.setflags(write=False)
    return out
