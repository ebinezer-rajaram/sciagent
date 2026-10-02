"""θ for a sampled truth, and its calibration to the operating point (SPEC §3).

**θ prior.** Coefficients are drawn on the scale of each design column at the
operating point, measured on a *reference log*: a homogeneous Poisson process
of rate 1 with the environment's marks (:func:`column_scales`). Per column it
records the time-average of ``|φ|``, the standard deviation and the maximum of
``|φ|`` over ``[burn_in · H, H]``.

- **Identity link.** A total ``T ~ U(identity_total)`` is split over the K
  features by a flat Dirichlet, ``w ~ Dir(1, …, 1)``; feature k gets the share
  ``T · w_k``. Its columns get a direction ``u`` of unit L1 norm (a single
  column takes ``+1``, so an excitation excites; several columns take a
  normalised standard normal draw), and ``θ_kj = T w_k u_j / s_kj``, where
  ``s_kj`` is the column's mean ``|φ|`` if the feature contains an ``Excite``
  (so the share is a branching-ratio-like rate contribution) and its maximum
  ``|φ|`` otherwise (so an exogenous feature never drives λ below 0 by
  itself). θ₀ starts at 1.
- **Exp and softplus links.** Each feature draws an effect size
  ``e ~ U(effect)`` in log-rate units and a sign (negative with probability
  ``negative_prob``), and a direction ``u`` of unit L2 norm;
  ``θ_kj = ±e u_j / sd_kj``. θ₀ starts where the null has rate 1 (0 for exp,
  ``log(e - 1)`` for softplus).

Draws, in order: identity ``T``, then ``w``, then per feature its direction;
exp/softplus per feature ``e``, the sign uniform, then its direction. A column
with zero spread at the operating point is degenerate and the candidate is
rejected.

**The knob.** :func:`apply_knob` moves one scalar ``x``. Under the identity
link it multiplies θ₀ and every *exogenous* feature's θ (no ``Excite`` inside)
by ``e^x``; a linear process's rate is then proportional to ``e^x`` while its
excitation (branching) is unchanged. Under exp and softplus it adds ``x`` to
θ₀.

**Calibration** (:func:`calibrate`) finds ``x`` with mean rate within
``rate_tols`` of 1, in stages of increasing pilot horizon. Within a stage every
pilot uses the same generator state (common random numbers), so the measured
rate is a deterministic, nearly smooth function of ``x``, and a secant search
on ``log rate`` (first step of slope 1, steps clipped to ±``_STEP_CLIP``)
converges in a few pilots. Each pilot is capped at
``event_cap_factor · H`` events: hitting the cap (or an overflowing thinning
bound) is an explosion, read as a rate of at least ``event_cap_factor``, so
the search steps down. A pilot never runs unbounded, which is the guard
against the slow exp-link explosions found in P2 (LOG 2026-10-02).

**Stationarity** has three parts:

1. identity link: :func:`branching_bound`, an upper bound on the expected
   number of direct offspring per event, must be below ``max_branching``
   whenever every feature is linear in history (computable analytically);
2. every link: the final pilot must finish under the cap without a negative
   identity intensity;
3. every link: the rate on the second half of the final pilot (after burn-in)
   over the rate on the first half must lie within
   ``[1/max_drift, max_drift]``, which catches slow explosions and decays that
   finish inside the cap.

**Dispersion.** The final pilot's Fano factor of counts in windows of width
``fano_window`` must lie in ``fano_band``.

Rejections raise :class:`CandidateRejectedError` with a reason code. Wall
time is never consulted, so accepting or rejecting a candidate is a pure
function of its inputs (invariant 3).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import (
    Above,
    ChannelSpec,
    Excite,
    ExpOf,
    Feature,
    Gate,
    Link,
    MarkFn,
    Periodic,
    Pow,
    Product,
    PsiSlot,
    Source,
    Structure,
    Trend,
    n_columns,
)
from sciagent.glm.simulate import (
    Coefficients,
    ExplosionError,
    MarkSampler,
    NegativeIntensityError,
    PsiAssignment,
    columns,
    simulate,
)

#: Largest |Δx| per secant step.
_STEP_CLIP: Final = 2.0
#: A secant slope below this is replaced by 1 (a flat or inverted estimate).
_MIN_SLOPE: Final = 0.1
#: Reference-log evaluation spacing, in mean inter-event times.
_REFERENCE_STEP: Final = 0.5


class CalibrationConfigError(SciAgentError):
    """A θ prior or calibration setting is invalid."""


class CandidateRejectedError(SciAgentError):
    """A candidate truth failed a constraint. ``reason`` is a short code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class MarkMean(Protocol):
    """``E[|f(m)| · 1[event in source]]`` under the environment's mark law.

    ``psi`` is the mark function's ψ value (None for ``One`` and ``Mark``).
    May return ``inf``.
    """

    def __call__(self, mark: MarkFn, psi: float | None, source: Source, /) -> float: ...


type GeneratorFactory = Callable[[str], np.random.Generator]


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def _interval(name: str, lo: float, hi: float, *, positive: bool = True) -> None:
    if not (math.isfinite(lo) and math.isfinite(hi) and lo <= hi):
        raise CalibrationConfigError(f"{name} must be a finite interval: {(lo, hi)}")
    if positive and lo <= 0.0:
        raise CalibrationConfigError(f"{name} must be positive: {(lo, hi)}")


@dataclass(frozen=True)
class ThetaPrior:
    """The θ prior (module docstring). Intervals are ``(lo, hi)``."""

    identity_total: tuple[float, float]
    effect: tuple[float, float]
    negative_prob: float
    reference_horizon: float

    def __post_init__(self) -> None:
        _interval("identity_total", *self.identity_total)
        _interval("effect", *self.effect)
        if not 0.0 <= self.negative_prob <= 1.0:
            raise CalibrationConfigError(f"negative_prob {self.negative_prob}")
        if not self.reference_horizon > 0.0:
            raise CalibrationConfigError("reference_horizon must be positive")


@dataclass(frozen=True)
class CalibrationSettings:
    """Pilot stages and acceptance bands (module docstring)."""

    pilot_horizons: tuple[float, ...]
    rate_tols: tuple[float, ...]
    max_iter: int
    event_cap_factor: float
    burn_in: float
    fano_window: float
    fano_band: tuple[float, float]
    max_drift: float
    max_branching: float
    draws_per_event: int = 64

    def __post_init__(self) -> None:
        if not self.pilot_horizons or len(self.pilot_horizons) != len(self.rate_tols):
            raise CalibrationConfigError("one rate tolerance per pilot stage")
        if any(not (math.isfinite(h) and h > 0) for h in self.pilot_horizons):
            raise CalibrationConfigError("pilot horizons must be positive")
        if any(not 0.0 < t < 1.0 for t in self.rate_tols):
            raise CalibrationConfigError("rate tolerances must lie in (0, 1)")
        if self.draws_per_event < 4:
            raise CalibrationConfigError("draws_per_event must be ≥ 4")
        if self.max_iter < 1 or not self.event_cap_factor > 1.0:
            raise CalibrationConfigError("max_iter ≥ 1 and event_cap_factor > 1")
        if not 0.0 <= self.burn_in < 0.5:
            raise CalibrationConfigError("burn_in must lie in [0, 0.5)")
        if not self.fano_window > 0.0:
            raise CalibrationConfigError("fano_window must be positive")
        _interval("fano_band", *self.fano_band, positive=False)
        if not self.max_drift > 1.0 or not 0.0 < self.max_branching < 1.0:
            raise CalibrationConfigError("max_drift > 1 and max_branching in (0, 1)")


# --------------------------------------------------------------------------
# Tree walks
# --------------------------------------------------------------------------


def _excites(
    feature: Feature, path: tuple[int, ...] = ()
) -> Iterator[tuple[tuple[int, ...], Excite]]:
    """Every ``Excite`` atom of a feature with its ψ path (grammar's addressing)."""
    match feature:
        case Excite():
            yield path, feature
        case Periodic() | Trend():
            return
        case Product(left=left, right=right):
            yield from _excites(left, (*path, 0))
            yield from _excites(right, (*path, 1))
        case Gate(feature=inner):
            yield from _excites(inner, (*path, 0))


def has_excite(feature: Feature) -> bool:
    return any(True for _ in _excites(feature))


def _mark_psi(
    mark: MarkFn, path: tuple[int, ...], psi: Mapping[PsiSlot, float]
) -> float | None:
    match mark:
        case Pow():
            return psi[PsiSlot((*path, 1), "pow_exponent")]
        case ExpOf():
            return psi[PsiSlot((*path, 1), "exp_coef")]
        case Above():
            return psi[PsiSlot((*path, 1), "above_z")]
        case _:
            return None


def branching_bound(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    mark_mean: MarkMean,
) -> float | None:
    """An upper bound on the expected direct offspring per event (identity link).

    For a feature with exactly one ``Excite`` atom, every column is that
    excitation times factors bounded by 1 in absolute value (sines, cosines,
    gate indicators), so its contribution to λ is at most
    ``Σⱼ |θⱼ| · Σ_{t_i} |f(m_i)| k(t - t_i)`` over the source's events: a
    dominating linear Hawkes process with offspring mean
    ``Σⱼ |θⱼ| · E[|f(m)| 1[source]]``. Features without ``Excite`` add none.
    Returns None under exp or softplus (no branching structure) or when a
    feature multiplies two or more excitations (not linear in history).
    """
    if structure.link is not Link.IDENTITY:
        return None
    parts: list[float] = []
    for feature, assignment, theta in zip(
        structure.features, psi, coef.per_feature, strict=True
    ):
        found = list(_excites(feature))
        if not found:
            continue
        if len(found) > 1:
            return None
        path, atom = found[0]
        moment = mark_mean(
            atom.mark, _mark_psi(atom.mark, path, assignment), atom.source
        )
        parts.append(math.fsum(abs(t) for t in theta) * moment)
    return math.fsum(parts)


# --------------------------------------------------------------------------
# Operating point
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatingPoint:
    """Summary of a pilot log on ``[burn_in · H, H]``.

    ``fano`` is the variance over the mean of counts in consecutive windows of
    the given width (population variance; whole windows only); ``cv`` the
    standard deviation over the mean of the gaps between consecutive events in
    the span; ``drift`` the rate on the span's second half over its first.
    """

    mean_rate: float
    fano: float
    cv: float
    drift: float
    n_events: int


def _mean_var(x: np.ndarray) -> tuple[float, float]:
    n = x.size
    mean = math.fsum(x.tolist()) / n
    var = math.fsum(((x - mean) ** 2).tolist()) / n
    return mean, var


def operating_point(log: EventLog, *, burn_in: float, window: float) -> OperatingPoint:
    """The operating-point summary of ``log`` (see :class:`OperatingPoint`)."""
    start = burn_in * log.horizon
    span = log.horizon - start
    t = log.times[log.times >= start]
    n = int(t.size)
    rate = n / span
    n_windows = int(span // window)
    if n_windows >= 2:
        edges = start + window * np.arange(n_windows + 1, dtype=np.float64)
        counts = np.histogram(t, bins=edges)[0].astype(np.float64)
        mean, var = _mean_var(counts)
        fano = var / mean if mean > 0 else math.nan
    else:
        fano = math.nan
    if n >= 3:
        gm, gv = _mean_var(np.diff(t))
        cv = math.sqrt(gv) / gm
    else:
        cv = math.nan
    mid = start + 0.5 * span
    first = int(np.count_nonzero(t < mid))
    second = n - first
    drift = second / first if first > 0 else (math.inf if second else 1.0)
    return OperatingPoint(rate, fano, cv, drift, n)


# --------------------------------------------------------------------------
# θ
# --------------------------------------------------------------------------

#: Per feature, per column: (mean |φ|, sd φ, max |φ|) on the reference log.
type ColumnScales = tuple[tuple[tuple[float, float, float], ...], ...]


def column_scales(
    structure: Structure,
    psi: PsiAssignment,
    channels: tuple[ChannelSpec, ...],
    mark_sampler: MarkSampler,
    rng: np.random.Generator,
    horizon: float,
    *,
    burn_in: float = 0.1,
) -> ColumnScales:
    """Column scales at the operating point (module docstring)."""
    reference = simulate(
        Structure(()), (), Coefficients(1.0, ()), channels, mark_sampler, horizon, rng
    )
    t = burn_in * horizon + _REFERENCE_STEP * np.arange(
        int((1.0 - burn_in) * horizon / _REFERENCE_STEP), dtype=np.float64
    )
    out: list[tuple[tuple[float, float, float], ...]] = []
    for feature, assignment in zip(structure.features, psi, strict=True):
        cols = columns(feature, assignment, channels, reference, t)
        per: list[tuple[float, float, float]] = []
        for row in cols:
            _, var = _mean_var(row)
            mean_abs, _ = _mean_var(np.abs(row))
            per.append((mean_abs, math.sqrt(var), float(np.max(np.abs(row)))))
        out.append(tuple(per))
    return tuple(out)


def _direction(n: int, rng: np.random.Generator, order: int) -> tuple[float, ...]:
    if n == 1:
        return (1.0,)
    g = rng.standard_normal(n)
    norm = (
        math.fsum(np.abs(g).tolist())
        if order == 1
        else math.sqrt(math.fsum((g * g).tolist()))
    )
    return tuple(float(v) / norm for v in g)


def sample_theta(
    structure: Structure,
    scales: ColumnScales,
    prior: ThetaPrior,
    rng: np.random.Generator,
) -> Coefficients:
    """θ drawn from the θ prior (module docstring)."""
    for per in scales:
        for mean_abs, sd, max_abs in per:
            if not (mean_abs > 0.0 and sd > 0.0 and max_abs > 0.0):
                raise CandidateRejectedError(
                    "degenerate_column", "a column is constant at the operating point"
                )
    k = len(structure.features)
    per_feature: list[tuple[float, ...]] = []
    if structure.link is Link.IDENTITY:
        total = float(rng.uniform(*prior.identity_total))
        shares = rng.dirichlet(np.ones(k)) if k > 1 else np.ones(1)
        for feature, share, per in zip(structure.features, shares, scales, strict=True):
            u = _direction(n_columns(feature), rng, 1)
            excite = has_excite(feature)
            per_feature.append(
                tuple(
                    total * float(share) * uj / (s[0] if excite else s[2])
                    for uj, s in zip(u, per, strict=True)
                )
            )
        return Coefficients(1.0, tuple(per_feature))
    for feature, per in zip(structure.features, scales, strict=True):
        effect = float(rng.uniform(*prior.effect))
        sign = -1.0 if rng.random() < prior.negative_prob else 1.0
        u = _direction(n_columns(feature), rng, 2)
        per_feature.append(
            tuple(sign * effect * uj / s[1] for uj, s in zip(u, per, strict=True))
        )
    start = 0.0 if structure.link is Link.EXP else math.log(math.e - 1.0)
    return Coefficients(start, tuple(per_feature))


def apply_knob(structure: Structure, coef: Coefficients, x: float) -> Coefficients:
    """θ with the calibration knob at ``x`` (module docstring)."""
    if structure.link is Link.IDENTITY:
        scale = math.exp(x)
        return Coefficients(
            coef.intercept * scale,
            tuple(
                theta if has_excite(f) else tuple(t * scale for t in theta)
                for f, theta in zip(structure.features, coef.per_feature, strict=True)
            ),
        )
    return Coefficients(coef.intercept + x, coef.per_feature)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Calibration:
    """A calibrated θ and what was measured.

    ``knob`` is the final ``x``; ``iterations`` counts pilots over all stages;
    ``point`` summarises the last stage's final pilot (horizon ``horizon``);
    ``branching`` is :func:`branching_bound` of ``coef`` (None if unavailable).
    """

    coef: Coefficients
    knob: float
    iterations: int
    point: OperatingPoint
    horizon: float
    branching: float | None


class WorkBudgetExceededError(SciAgentError):
    """A bounded simulation drew more random numbers than its budget allows."""


class BudgetedGenerator(np.random.Generator):
    """A generator that raises after ``budget`` calls to ``random``/``exponential``.

    The simulator draws, per thinning candidate, one exponential gap and one
    acceptance uniform, and per accepted event the sampler's uniforms. Counting
    those calls bounds a simulation's work **deterministically**, where a wall
    clock would make acceptance depend on the machine. It also stops a known
    simulator hang: when the thinning bound is so large that the exponential
    gap falls below the floating-point spacing of t, the candidate time never
    advances and the loop draws forever without accepting an event. The
    stream is the wrapped bit generator's, unchanged.
    """

    def __init__(self, rng: np.random.Generator, budget: int) -> None:
        super().__init__(rng.bit_generator)
        self.remaining = budget

    def _spend(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise WorkBudgetExceededError("simulation draw budget exhausted")

    def random(self, *args: Any, **kwargs: Any) -> Any:
        self._spend()
        return super().random(*args, **kwargs)

    def exponential(self, *args: Any, **kwargs: Any) -> Any:
        self._spend()
        return super().exponential(*args, **kwargs)


def bounded_simulate(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    mark_sampler: MarkSampler,
    horizon: float,
    rng: np.random.Generator,
    settings: CalibrationSettings,
) -> EventLog | None:
    """Simulate under the event cap and draw budget; None if either is hit.

    The cap is ``event_cap_factor · horizon`` events and the budget
    ``draws_per_event`` draws per capped event. Exceeding either, or an
    overflowing thinning bound, is an explosion (None). A negative identity
    intensity raises :class:`CandidateRejectedError`.
    """
    cap = int(settings.event_cap_factor * horizon)
    budgeted = BudgetedGenerator(rng, settings.draws_per_event * (cap + 1))
    try:
        return simulate(
            structure,
            psi,
            coef,
            channels,
            mark_sampler,
            horizon,
            budgeted,
            max_events=cap,
        )
    except (ExplosionError, WorkBudgetExceededError):
        return None
    except NegativeIntensityError as exc:
        raise CandidateRejectedError("negative_intensity", str(exc)) from exc


def _pilot(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    mark_sampler: MarkSampler,
    horizon: float,
    rng: np.random.Generator,
    settings: CalibrationSettings,
) -> OperatingPoint | None:
    """The pilot's operating point, or None if it exploded."""
    log = bounded_simulate(
        structure, psi, coef, channels, mark_sampler, horizon, rng, settings
    )
    if log is None:
        return None
    return operating_point(log, burn_in=settings.burn_in, window=settings.fano_window)


def calibrate(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    mark_sampler: MarkSampler,
    settings: CalibrationSettings,
    generators: GeneratorFactory,
    mark_mean: MarkMean,
) -> Calibration:
    """Move θ to the operating point and check stationarity and dispersion.

    ``generators(stage)`` must return a fresh generator in the same state on
    every call with the same stage name (``"pilot0"``, ``"pilot1"``, …); the
    pilots of a stage share it (common random numbers). Raises
    :class:`CandidateRejectedError`.
    """
    bound = branching_bound(structure, psi, coef, mark_mean)
    if bound is not None and not bound < settings.max_branching:
        raise CandidateRejectedError(
            "branching", f"bound {bound:.4g} ≥ {settings.max_branching}"
        )
    x = 0.0
    iterations = 0
    point: OperatingPoint | None = None
    stage_count = len(settings.pilot_horizons)
    for stage, (horizon, tol) in enumerate(
        zip(settings.pilot_horizons, settings.rate_tols, strict=True)
    ):
        history: list[tuple[float, float]] = []
        converged = False
        for _ in range(settings.max_iter):
            iterations += 1
            point = _pilot(
                structure,
                psi,
                apply_knob(structure, coef, x),
                channels,
                mark_sampler,
                horizon,
                generators(f"pilot{stage}"),
                settings,
            )
            if point is None:
                f = math.log(settings.event_cap_factor)
            elif point.n_events == 0:
                f = math.log(0.5 / ((1.0 - settings.burn_in) * horizon))
            else:
                f = math.log(point.mean_rate)
            if point is not None and abs(point.mean_rate - 1.0) <= tol:
                converged = True
                break
            history.append((x, f))
            slope = 1.0
            if len(history) >= 2:
                (x0, f0), (x1, f1) = history[-2], history[-1]
                if x1 != x0:
                    s = (f1 - f0) / (x1 - x0)
                    if math.isfinite(s) and s >= _MIN_SLOPE:
                        slope = s
            x += max(-_STEP_CLIP, min(_STEP_CLIP, -f / slope))
        if not converged:
            reason = "explosion" if point is None else "no_convergence"
            raise CandidateRejectedError(
                reason, f"stage {stage + 1}/{stage_count} did not reach rate 1 ± {tol}"
            )
    if point is None:  # unreachable: a converged stage has a point
        raise CandidateRejectedError("explosion", "no pilot completed")
    if not 1.0 / settings.max_drift <= point.drift <= settings.max_drift:
        raise CandidateRejectedError("drift", f"rate drift {point.drift:.4g}")
    lo, hi = settings.fano_band
    if not lo <= point.fano <= hi:
        raise CandidateRejectedError(
            "dispersion", f"Fano {point.fano:.4g} outside {(lo, hi)}"
        )
    final = apply_knob(structure, coef, x)
    return Calibration(
        coef=final,
        knob=x,
        iterations=iterations,
        point=point,
        horizon=settings.pilot_horizons[-1],
        branching=branching_bound(structure, psi, final, mark_mean),
    )
