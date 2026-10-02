"""Instrument tests for the exact GLM point-process simulator (SPEC §2, §6.3).

The simulator generates every truth's data, so it is an instrument. These tests
check it against closed forms (Poisson), against an independent construction
(the Hawkes cluster representation), against itself through the time-rescaling
theorem, and check that its thinning bound is a bound.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import pairwise

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from numpy.polynomial.legendre import leggauss
from scipy import integrate, stats

from sciagent.glm import simulate as sim
from sciagent.glm.data import EventLog, Floats
from sciagent.glm.grammar import (
    ALL,
    Above,
    ChannelKind,
    ChannelSpec,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
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
)
from sciagent.glm.simulate import (
    BoundViolationError,
    Coefficients,
    ExplosionError,
    InvalidSimulationInputError,
    NegativeIntensityError,
    PsiAssignment,
    intensity,
    simulate,
)

CHANNELS = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
    ChannelSpec("sign", ChannelKind.SIGN, location=0.0, scale=1.0),
)


def marks_exp(rng: np.random.Generator) -> Mapping[str, float]:
    """Sizes Exponential(mean 1), signs a fair coin."""
    size = float(rng.exponential(1.0))
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"size": size, "sign": sign}


def marks_lognormal(rng: np.random.Generator) -> Mapping[str, float]:
    """Sizes lognormal with mean 1, signs a fair coin."""
    sigma = 0.5
    size = float(rng.lognormal(-0.5 * sigma**2, sigma))
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"size": size, "sign": sign}


EXP_K = Excite(KernelKind.EXP, One(), ALL)
POS = Source(SourceKind.POSITIVE, "sign")
NEG = Source(SourceKind.NEGATIVE, "sign")


def psi_for(feature: Feature, values: Mapping[str, float]) -> dict[PsiSlot, float]:
    """Assign ψ by parameter name (every slot of that name gets the value)."""
    return {slot: values[slot.name] for slot in psi_slots(feature)}


def model(
    features: tuple[Feature, ...],
    psi_values: tuple[Mapping[str, float], ...],
    intercept: float,
    per_feature: tuple[tuple[float, ...], ...],
    link: Link = Link.IDENTITY,
) -> tuple[Structure, PsiAssignment, Coefficients]:
    structure = Structure(features, link)
    psi = tuple(psi_for(f, v) for f, v in zip(features, psi_values, strict=True))
    return structure, psi, Coefficients(intercept, per_feature)


def poisson(mu: float) -> tuple[Structure, PsiAssignment, Coefficients]:
    return model((EXP_K,), ({"exp_rate": 1.0},), mu, ((0.0,),))


def hawkes(
    mu: float, eta: float, beta: float
) -> tuple[Structure, PsiAssignment, Coefficients]:
    return model((EXP_K,), ({"exp_rate": beta},), mu, ((eta,),))


# --------------------------------------------------------------------------
# Closed forms and an independent construction
# --------------------------------------------------------------------------


def test_homogeneous_poisson_counts_and_interarrivals() -> None:
    structure, psi, coef = poisson(2.0)
    horizon = 500.0
    counts = []
    gaps = []
    for seed in range(10):
        log = simulate(
            structure,
            psi,
            coef,
            CHANNELS,
            marks_exp,
            horizon,
            np.random.default_rng(seed),
        )
        counts.append(log.n)
        gaps.append(np.diff(np.concatenate([[0.0], log.times])))
    mean = float(np.mean(counts))
    # Mean of 10 Poisson(1000) counts has sd 10; 4 sd tolerance.
    assert abs(mean - 1000.0) < 40.0
    pooled = np.concatenate(gaps)
    assert stats.kstest(pooled, "expon", args=(0.0, 0.5)).pvalue > 1e-3


def cluster_hawkes(
    mu: float, eta: float, beta: float, horizon: float, rng: np.random.Generator
) -> Floats:
    """Exponential Hawkes by the branching construction (Hawkes & Oakes 1974)."""
    n0 = rng.poisson(mu * horizon)
    generation = rng.uniform(0.0, horizon, n0)
    out = [generation]
    while generation.size:
        n_children = rng.poisson(eta, generation.size)
        parents = np.repeat(generation, n_children)
        children = parents + rng.exponential(1.0 / beta, parents.size)
        generation = children[children <= horizon]
        out.append(generation)
    return np.sort(np.concatenate(out))


@pytest.mark.slow
def test_exponential_hawkes_matches_cluster_construction() -> None:
    mu, eta, beta, horizon = 0.5, 0.5, 2.0, 2000.0
    structure, psi, coef = hawkes(mu, eta, beta)
    ours_counts, ref_counts, ours_gaps, ref_gaps = [], [], [], []
    for seed in range(12):
        log = simulate(
            structure,
            psi,
            coef,
            CHANNELS,
            marks_exp,
            horizon,
            np.random.default_rng(seed),
        )
        ref = cluster_hawkes(mu, eta, beta, horizon, np.random.default_rng(1000 + seed))
        ours_counts.append(log.n)
        ref_counts.append(ref.size)
        ours_gaps.append(np.diff(log.times))
        ref_gaps.append(np.diff(ref))
    expected = mu / (1.0 - eta) * horizon  # 2000
    # Var N(T) ≈ μT/(1-η)^3 = 8000, sd ≈ 89; mean of 12 has sd ≈ 26.
    assert abs(np.mean(ours_counts) - expected) < 4 * 26
    assert abs(np.mean(ref_counts) - expected) < 4 * 26
    # Same inter-arrival law (clustering shape), not just the same mean.
    p = stats.ks_2samp(np.concatenate(ours_gaps), np.concatenate(ref_gaps)).pvalue
    assert p > 1e-3


# --------------------------------------------------------------------------
# Time-rescaling self-check
# --------------------------------------------------------------------------

_GL_X, _GL_W = leggauss(12)


def compensator_increments(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    log: EventLog,
    breaks: Floats,
) -> Floats:
    """Λ(t_i) - Λ(t_{i-1}) by Gauss-Legendre on pieces of length ≤ 0.1.

    Pieces are split at every event and at every point in ``breaks`` (where the
    intensity jumps), so the integrand is smooth on each piece.
    """
    times = log.times
    knots = np.unique(np.concatenate([[0.0], times, breaks[breaks < times[-1]]]))
    fine: list[float] = []
    for a, b in pairwise(knots):
        k = max(1, math.ceil((b - a) / 0.1))
        fine.extend(np.linspace(a, b, k + 1)[:-1].tolist())
    fine.append(float(knots[-1]))
    edges = np.array(fine)
    lo, hi = edges[:-1], edges[1:]
    nodes = 0.5 * (hi - lo)[:, None] * _GL_X[None, :] + 0.5 * (hi + lo)[:, None]
    lam = intensity(structure, psi, coef, CHANNELS, log, nodes.ravel())
    piece = 0.5 * (hi - lo) * (lam.reshape(nodes.shape) @ _GL_W)
    cum = np.concatenate([[0.0], np.cumsum(piece)])
    at_events = cum[np.searchsorted(edges, times)]
    return np.diff(np.concatenate([[0.0], at_events]))


def phase_breaks(period: float, phase: float, horizon: float) -> Floats:
    """Zeros of sin(2πt/P - φ) on [0, horizon]."""
    k = np.arange(-1, math.ceil(2 * horizon / period) + 2)
    z = (k * math.pi + phase) * period / (2 * math.pi)
    return np.asarray(z[(z > 0) & (z < horizon)], dtype=np.float64)


# (id, features, ψ by name, intercept, per-feature θ, link, marks, breaks)
RESCALING_CASES: list[
    tuple[
        str,
        tuple[Feature, ...],
        tuple[dict[str, float], ...],
        float,
        tuple[tuple[float, ...], ...],
        Link,
    ]
] = [
    ("expk-identity", (EXP_K,), ({"exp_rate": 2.0},), 0.5, ((0.5,),), Link.IDENTITY),
    (
        "powerk-identity",
        (Excite(KernelKind.POWER, One(), ALL),),
        ({"power_c": 0.2, "power_p": 2.0},),
        0.5,
        ((0.4,),),
        Link.IDENTITY,
    ),
    (
        "gammak-identity",
        (Excite(KernelKind.GAMMA, One(), ALL),),
        ({"gamma_shape": 3.0, "gamma_mean": 1.0},),
        0.5,
        ((0.5,),),
        Link.IDENTITY,
    ),
    (
        "expk-exp-link-inhibition",
        (EXP_K,),
        ({"exp_rate": 1.0},),
        math.log(1.5),
        ((-0.8,),),
        Link.EXP,
    ),
    (
        "powerk-softplus",
        (Excite(KernelKind.POWER, One(), ALL),),
        ({"power_c": 0.05, "power_p": 3.0},),
        0.3,
        ((0.4,),),
        Link.SOFTPLUS,
    ),
    (
        "gammak-exp-link",
        (Excite(KernelKind.GAMMA, One(), ALL),),
        ({"gamma_shape": 2.0, "gamma_mean": 2.0},),
        math.log(0.6),
        ((0.3,),),
        Link.EXP,
    ),
    (
        "periodic-trend",
        (Periodic(), Trend()),
        ({"period": 10.0}, {}),
        1.0,
        ((0.5, 0.3), (0.5,)),
        Link.IDENTITY,
    ),
    (
        "product-excite-periodic",
        (EXP_K, Product(EXP_K, Periodic())),
        ({"exp_rate": 1.0}, {"exp_rate": 1.0, "period": 25.0}),
        0.8,
        ((0.3,), (0.2, 0.1)),
        Link.IDENTITY,
    ),
    (
        "gate-last-mark-above",
        (Gate(EXP_K, LastMarkAbove("size")),),
        ({"exp_rate": 2.0, "above_z": 0.5},),
        0.6,
        ((0.9,),),
        Link.IDENTITY,
    ),
    (
        "gate-phase-window",
        (Gate(EXP_K, PhaseWindow()),),
        ({"exp_rate": 1.0, "period": 10.0, "phase": 0.5 * math.pi},),
        0.6,
        ((0.6,),),
        Link.IDENTITY,
    ),
    (
        "signed-sources",
        (Excite(KernelKind.EXP, One(), POS), Excite(KernelKind.EXP, One(), NEG)),
        ({"exp_rate": 2.0}, {"exp_rate": 1.0}),
        0.8,
        ((0.6,), (-0.6,)),
        Link.SOFTPLUS,
    ),
    (
        "mark-sign",
        (Excite(KernelKind.EXP, Mark("sign"), ALL),),
        ({"exp_rate": 1.0},),
        0.7,
        ((0.6,),),
        Link.SOFTPLUS,
    ),
    (
        "mark-size",
        (Excite(KernelKind.EXP, Mark("size"), ALL),),
        ({"exp_rate": 1.0},),
        0.5,
        ((0.5,),),
        Link.IDENTITY,
    ),
    (
        "pow-size",
        (Excite(KernelKind.EXP, Pow("size"), ALL),),
        ({"exp_rate": 1.0, "pow_exponent": 1.5},),
        0.5,
        ((0.3,),),
        Link.IDENTITY,
    ),
    (
        "expof-size",
        (Excite(KernelKind.EXP, ExpOf("size"), ALL),),
        ({"exp_rate": 2.0, "exp_coef": 0.5},),
        0.5,
        ((0.4,),),
        Link.IDENTITY,
    ),
    (
        "above-size",
        (Excite(KernelKind.EXP, Above("size"), ALL),),
        ({"exp_rate": 2.0, "above_z": 0.5},),
        0.5,
        ((1.5,),),
        Link.IDENTITY,
    ),
]


@pytest.mark.slow
@pytest.mark.parametrize(
    ("features", "psi_values", "intercept", "theta", "link"),
    [case[1:] for case in RESCALING_CASES],
    ids=[case[0] for case in RESCALING_CASES],
)
def test_time_rescaling(
    features: tuple[Feature, ...],
    psi_values: tuple[dict[str, float], ...],
    intercept: float,
    theta: tuple[tuple[float, ...], ...],
    link: Link,
) -> None:
    structure, psi, coef = model(features, psi_values, intercept, theta, link)
    horizon = 400.0
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, horizon, np.random.default_rng(7)
    )
    assert log.n > 150
    breaks = np.empty(0)
    for v in psi_values:
        if "phase" in v:
            breaks = phase_breaks(v["period"], v["phase"], horizon)
    tau = compensator_increments(structure, psi, coef, log, breaks)
    assert stats.kstest(tau, "expon").pvalue > 1e-3


@pytest.mark.slow
def test_time_rescaling_lognormal_marks() -> None:
    structure, psi, coef = model(
        (Excite(KernelKind.GAMMA, Mark("size"), ALL),),
        ({"gamma_shape": 2.0, "gamma_mean": 0.5},),
        0.5,
        ((0.5,),),
    )
    log = simulate(
        structure, psi, coef, CHANNELS, marks_lognormal, 400.0, np.random.default_rng(3)
    )
    tau = compensator_increments(structure, psi, coef, log, np.empty(0))
    assert stats.kstest(tau, "expon").pvalue > 1e-3


@pytest.mark.slow
def test_time_rescaling_has_power() -> None:
    """Positive control: the KS check rejects a moderately wrong compensator."""
    structure, psi, coef = hawkes(0.5, 0.5, 2.0)
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 400.0, np.random.default_rng(7)
    )
    right = compensator_increments(structure, psi, coef, log, np.empty(0))
    assert stats.kstest(right, "expon").pvalue > 1e-3
    # Same mean rate, no excitation: Poisson at μ/(1-η).
    _, psi_w, coef_w = poisson(1.0)
    wrong = compensator_increments(structure, psi_w, coef_w, log, np.empty(0))
    assert stats.kstest(wrong, "expon").pvalue < 1e-3
    # Excitation present but with the wrong decay.
    _, psi_w, coef_w = hawkes(0.5, 0.5, 0.5)
    wrong = compensator_increments(structure, psi_w, coef_w, log, np.empty(0))
    assert stats.kstest(wrong, "expon").pvalue < 1e-3


def test_gauss_legendre_compensator_matches_quad() -> None:
    """The test's own integrator agrees with adaptive quadrature."""
    structure, psi, coef = model(
        (Gate(EXP_K, PhaseWindow()),),
        ({"exp_rate": 1.0, "period": 10.0, "phase": 0.5 * math.pi},),
        0.6,
        ((0.6,),),
    )
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 30.0, np.random.default_rng(1)
    )
    breaks = phase_breaks(10.0, 0.5 * math.pi, 30.0)
    tau = compensator_increments(structure, psi, coef, log, breaks)

    def lam(x: float) -> float:
        return float(intensity(structure, psi, coef, CHANNELS, log, np.array([x]))[0])

    edges = np.concatenate([[0.0], log.times])
    ref = []
    for a, b in pairwise(edges):
        inner = breaks[(breaks > a) & (breaks < b)]
        val, _ = integrate.quad(lam, a, b, points=inner if inner.size else None)
        ref.append(val)
    np.testing.assert_allclose(tau, ref, rtol=1e-8, atol=1e-10)


# --------------------------------------------------------------------------
# Intensity semantics (hand-computed values)
# --------------------------------------------------------------------------


def test_intensity_left_limit_and_kernels() -> None:
    log = EventLog.create(
        [1.0, 2.0], {"size": [2.0, 0.5], "sign": [1.0, -1.0]}, horizon=10.0
    )
    t = np.array([0.5, 1.0, 1.5, 2.0, 3.0])

    def excite_only(f: Feature, values: dict[str, float]) -> Floats:
        structure, psi, coef = model((f,), (values,), 0.0, ((1.0,),))
        return intensity(structure, psi, coef, CHANNELS, log, t)

    beta = 2.0

    def expk(x: Floats) -> Floats:
        return np.where(x > 0, beta * np.exp(-beta * np.maximum(x, 0.0)), 0.0)

    lag1, lag2 = t - 1.0, t - 2.0
    np.testing.assert_allclose(
        excite_only(EXP_K, {"exp_rate": beta}), expk(lag1) + expk(lag2), rtol=1e-12
    )
    # Mark(size) uses the raw mark; Mark(sign) is signed.
    np.testing.assert_allclose(
        excite_only(Excite(KernelKind.EXP, Mark("sign"), ALL), {"exp_rate": beta}),
        expk(lag1) - expk(lag2),
        rtol=1e-12,
    )
    # ExpOf: exp(a z), z = (m - 1) / 1.
    np.testing.assert_allclose(
        excite_only(
            Excite(KernelKind.EXP, ExpOf("size"), ALL),
            {"exp_rate": beta, "exp_coef": 2.0},
        ),
        np.exp(2.0) * expk(lag1) + np.exp(-1.0) * expk(lag2),
        rtol=1e-12,
    )
    # Pow: (m / location)^a.
    np.testing.assert_allclose(
        excite_only(
            Excite(KernelKind.EXP, Pow("size"), ALL),
            {"exp_rate": beta, "pow_exponent": 2.0},
        ),
        4.0 * expk(lag1) + 0.25 * expk(lag2),
        rtol=1e-12,
    )
    # Above: 1[z > q]; only the first event (z = 1) passes q = 0.5.
    np.testing.assert_allclose(
        excite_only(
            Excite(KernelKind.EXP, Above("size"), ALL),
            {"exp_rate": beta, "above_z": 0.5},
        ),
        expk(lag1),
        rtol=1e-12,
    )
    # Signed sources.
    np.testing.assert_allclose(
        excite_only(Excite(KernelKind.EXP, One(), NEG), {"exp_rate": beta}),
        expk(lag2),
        rtol=1e-12,
    )
    # Lomax and gamma densities.
    c, p = 0.5, 2.5

    def lomax(x: Floats) -> Floats:
        return np.where(x > 0, (p - 1) / c * (1 + np.maximum(x, 0) / c) ** -p, 0.0)

    np.testing.assert_allclose(
        excite_only(Excite(KernelKind.POWER, One(), ALL), {"power_c": c, "power_p": p}),
        lomax(lag1) + lomax(lag2),
        rtol=1e-12,
    )
    k, mean = 3.0, 1.5
    gpdf = stats.gamma(a=k, scale=mean / k).pdf
    np.testing.assert_allclose(
        excite_only(
            Excite(KernelKind.GAMMA, One(), ALL),
            {"gamma_shape": k, "gamma_mean": mean},
        ),
        np.where(lag1 > 0, gpdf(lag1), 0.0) + np.where(lag2 > 0, gpdf(lag2), 0.0),
        rtol=1e-12,
    )
    # LastMarkAbove: last event strictly before t; false before the first.
    np.testing.assert_allclose(
        excite_only(
            Gate(EXP_K, LastMarkAbove("size")), {"exp_rate": beta, "above_z": 0.5}
        ),
        np.where((t > 1.0) & (t <= 2.0), expk(lag1) + expk(lag2), 0.0),
        rtol=1e-12,
    )


def test_intensity_links_periodic_trend_product() -> None:
    log = EventLog.create([], {"size": [], "sign": []}, horizon=20.0)
    t = np.array([0.0, 1.0, 7.5, 20.0])
    structure, psi, coef = model(
        (Periodic(), Trend()), ({"period": 10.0}, {}), 0.5, ((0.3, -0.2), (1.0,))
    )
    eta = (
        0.5
        + 0.3 * np.sin(2 * np.pi * t / 10)
        - 0.2 * np.cos(2 * np.pi * t / 10)
        + t / 20.0
    )
    np.testing.assert_allclose(
        intensity(structure, psi, coef, CHANNELS, log, t), eta, rtol=1e-12
    )
    for link, g in ((Link.EXP, np.exp), (Link.SOFTPLUS, lambda x: np.log1p(np.exp(x)))):
        s2 = Structure(structure.features, link)
        np.testing.assert_allclose(
            intensity(s2, psi, coef, CHANNELS, log, t), g(eta), rtol=1e-12
        )
    # Product: row-major, a's columns outer.
    prod = Product(Periodic(), Trend())
    structure, psi, coef = model((prod,), ({"period": 10.0},), 0.0, ((2.0, 3.0),))
    expected = (2.0 * np.sin(2 * np.pi * t / 10) + 3.0 * np.cos(2 * np.pi * t / 10)) * (
        t / 20.0
    )
    np.testing.assert_allclose(
        intensity(structure, psi, coef, CHANNELS, log, t), expected, atol=1e-14
    )
    # PhaseWindow: sin(2πt/P - φ) ≥ 0.
    gate = Gate(Trend(), PhaseWindow())
    structure, psi, coef = model(
        (gate,), ({"period": 10.0, "phase": math.pi},), 0.0, ((1.0,),)
    )
    on = np.sin(2 * np.pi * t / 10 - math.pi) >= 0
    np.testing.assert_allclose(
        intensity(structure, psi, coef, CHANNELS, log, t), np.where(on, t / 20, 0.0)
    )


# --------------------------------------------------------------------------
# Bounds: every column stays inside its window interval
# --------------------------------------------------------------------------

BOUND_FEATURES: list[tuple[Feature, dict[str, float]]] = [
    (EXP_K, {"exp_rate": 4.0}),
    (Excite(KernelKind.POWER, Mark("sign"), ALL), {"power_c": 0.05, "power_p": 1.2}),
    (
        Excite(KernelKind.GAMMA, Mark("size"), POS),
        {"gamma_shape": 5.0, "gamma_mean": 1.0},
    ),
    (
        Excite(KernelKind.GAMMA, ExpOf("size"), NEG),
        {"gamma_shape": 2.0, "gamma_mean": 0.5, "exp_coef": 2.5},
    ),
    (Periodic(), {"period": 5.0}),
    (Trend(), {}),
    (
        Product(Excite(KernelKind.GAMMA, Mark("sign"), ALL), Periodic()),
        {"gamma_shape": 3.0, "gamma_mean": 2.0, "period": 5.0},
    ),
    (
        Gate(Excite(KernelKind.EXP, Mark("sign"), ALL), PhaseWindow()),
        {"exp_rate": 1.0, "period": 5.0, "phase": 1.0},
    ),
    (Gate(Periodic(), LastMarkAbove("size")), {"period": 5.0, "above_z": 0.0}),
    (
        Product(Excite(KernelKind.EXP, Mark("size"), NEG), Trend()),
        {"exp_rate": 8.0},
    ),
]


@given(
    which=st.integers(0, len(BOUND_FEATURES) - 1),
    seed=st.integers(0, 2**32 - 1),
    gap=st.floats(0.0, 3.0),
    width=st.floats(1e-6, 5.0),
)
def test_column_ranges_contain_column_values(
    which: int, seed: int, gap: float, width: float
) -> None:
    feature, values = BOUND_FEATURES[which]
    rng = np.random.default_rng(seed)
    n = int(rng.integers(0, 30))
    times = np.cumsum(rng.exponential(0.3, n))
    marks = [marks_exp(rng) for _ in range(n)]
    log = EventLog.create(
        times,
        {c.name: [m[c.name] for m in marks] for c in CHANNELS},
        horizon=float(times[-1] if n else 0.0) + gap + width + 1.0,
    )
    s = float(times[-1] if n else 0.0) + gap
    end = s + width
    psi = psi_for(feature, values)
    lo, hi = sim.column_ranges(feature, psi, CHANNELS, log, s, end)
    grid = np.concatenate([np.linspace(s, end, 401)[1:], [np.nextafter(s, np.inf)]])
    cols = sim.columns(feature, psi, CHANNELS, log, grid)
    assert cols.shape == (n_columns(feature), grid.size)
    tol = 1e-9 * (1.0 + np.abs(cols))
    assert np.all(cols >= lo[:, None] - tol)
    assert np.all(cols <= hi[:, None] + tol)

    # The simulator's path: ExpK by recursion, evaluated one time at a time.
    rlo, rhi = sim.column_ranges(feature, psi, CHANNELS, log, s, end, recursive=True)
    rcols = np.stack(
        [
            sim.columns(feature, psi, CHANNELS, log, grid[i : i + 1], recursive=True)[
                :, 0
            ]
            for i in range(0, grid.size, 20)
        ],
        axis=1,
    )
    np.testing.assert_allclose(rcols, cols[:, ::20], rtol=1e-9, atol=1e-12)
    rtol = 1e-9 * (1.0 + np.abs(rcols))
    assert np.all(rcols >= rlo[:, None] - rtol)
    assert np.all(rcols <= rhi[:, None] + rtol)


# --------------------------------------------------------------------------
# Determinism and guards
# --------------------------------------------------------------------------


def test_determinism() -> None:
    structure, psi, coef = model(
        (EXP_K, Excite(KernelKind.EXP, Mark("sign"), ALL)),
        ({"exp_rate": 2.0}, {"exp_rate": 1.0}),
        0.7,
        ((0.4,), (0.2,)),
    )

    def run(seed: int) -> EventLog:
        return simulate(
            structure,
            psi,
            coef,
            CHANNELS,
            marks_exp,
            200.0,
            np.random.default_rng(seed),
        )

    a, b, c = run(11), run(11), run(12)
    assert a.times.tobytes() == b.times.tobytes()
    assert sorted(a.marks) == sorted(b.marks) == ["sign", "size"]
    for name in a.marks:
        assert a.marks[name].tobytes() == b.marks[name].tobytes()
    assert a.times.tobytes() != c.times.tobytes()


def test_fast_enough_for_5000_exp_events() -> None:
    """n ≈ 5,000 with an exp kernel; a smoke test, the time is reported not asserted."""
    structure, psi, coef = hawkes(0.5, 0.5, 2.0)
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 5000.0, np.random.default_rng(0)
    )
    assert 4000 < log.n < 6000


def test_bound_violation_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    structure, psi, coef = poisson(2.0)
    real = sim._Evaluator.eta_upper

    def too_low(self: sim._Evaluator, s: float, end: float) -> float:
        return real(self, s, end) - 1.0

    monkeypatch.setattr(sim._Evaluator, "eta_upper", too_low)
    with pytest.raises(BoundViolationError):
        simulate(
            structure, psi, coef, CHANNELS, marks_exp, 100.0, np.random.default_rng(0)
        )


def test_negative_intensity_guard() -> None:
    # λ = 1 - 2·Σ 2e^{-2(t - t_j)} is negative just after any event.
    structure, psi, coef = model((EXP_K,), ({"exp_rate": 2.0},), 1.0, ((-2.0,),))
    with pytest.raises(NegativeIntensityError):
        simulate(
            structure, psi, coef, CHANNELS, marks_exp, 100.0, np.random.default_rng(0)
        )
    # Negative from the start (intercept < 0) is caught without any event.
    structure, psi, coef = poisson(-0.1)
    with pytest.raises(NegativeIntensityError):
        simulate(
            structure, psi, coef, CHANNELS, marks_exp, 100.0, np.random.default_rng(0)
        )


def test_explosion_guard() -> None:
    structure, psi, coef = hawkes(1.0, 1.5, 2.0)
    with pytest.raises(ExplosionError):
        simulate(
            structure,
            psi,
            coef,
            CHANNELS,
            marks_exp,
            1000.0,
            np.random.default_rng(0),
            max_events=2000,
        )


def test_zero_intensity_is_valid() -> None:
    structure, psi, coef = poisson(0.0)
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 50.0, np.random.default_rng(0)
    )
    assert log.n == 0
    assert sorted(log.marks) == ["sign", "size"]


@pytest.mark.parametrize(
    ("feature", "values"),
    [
        (EXP_K, {"exp_rate": 0.0}),
        (EXP_K, {"exp_rate": math.inf}),
        (Excite(KernelKind.POWER, One(), ALL), {"power_c": 0.2, "power_p": 1.0}),
        (Excite(KernelKind.POWER, One(), ALL), {"power_c": -1.0, "power_p": 2.0}),
        (Excite(KernelKind.GAMMA, One(), ALL), {"gamma_shape": 0.5, "gamma_mean": 1.0}),
        (Periodic(), {"period": 0.0}),
        (Gate(Trend(), PhaseWindow()), {"period": 5.0, "phase": math.nan}),
        (Gate(Trend(), LastMarkAbove("size")), {"above_z": math.nan}),
    ],
)
def test_invalid_psi_rejected(feature: Feature, values: dict[str, float]) -> None:
    structure, psi, coef = model(
        (feature,), (values,), 1.0, ((0.1,) * n_columns(feature),)
    )
    with pytest.raises(InvalidSimulationInputError):
        simulate(
            structure, psi, coef, CHANNELS, marks_exp, 10.0, np.random.default_rng(0)
        )


def test_off_grid_psi_accepted() -> None:
    structure, psi, coef = hawkes(0.5, 0.3, 1.7)  # 1.7 is not on the exp_rate grid
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 50.0, np.random.default_rng(0)
    )
    assert log.n > 0


def test_structural_input_errors() -> None:
    structure, psi, coef = hawkes(0.5, 0.3, 1.0)
    rng = np.random.default_rng(0)
    with pytest.raises(InvalidSimulationInputError):  # missing slot
        simulate(structure, ({},), coef, CHANNELS, marks_exp, 10.0, rng)
    extra = {**psi[0], PsiSlot((9,), "exp_rate"): 1.0}
    with pytest.raises(InvalidSimulationInputError):  # unknown slot
        simulate(structure, (extra,), coef, CHANNELS, marks_exp, 10.0, rng)
    with pytest.raises(InvalidSimulationInputError):  # wrong θ length
        simulate(
            structure,
            psi,
            Coefficients(0.5, ((0.1, 0.2),)),
            CHANNELS,
            marks_exp,
            10.0,
            rng,
        )
    with pytest.raises(InvalidSimulationInputError):  # non-finite θ
        simulate(
            structure,
            psi,
            Coefficients(math.nan, ((0.1,),)),
            CHANNELS,
            marks_exp,
            10.0,
            rng,
        )

    def bad_sign(r: np.random.Generator) -> Mapping[str, float]:
        return {"size": 1.0, "sign": 0.5}

    with pytest.raises(InvalidSimulationInputError):
        simulate(structure, psi, coef, CHANNELS, bad_sign, 10.0, rng)

    def missing(r: np.random.Generator) -> Mapping[str, float]:
        return {"size": 1.0}

    with pytest.raises(InvalidSimulationInputError):
        simulate(structure, psi, coef, CHANNELS, missing, 10.0, rng)


# --------------------------------------------------------------------------
# Plans (the simulator side of interventions; semantics in test_interventions)
# --------------------------------------------------------------------------


def _planned(
    model_: tuple[Structure, PsiAssignment, Coefficients],
    plan: sim.Plan,
    horizon: float = 100.0,
    seed: int = 0,
) -> sim.PlannedRun:
    structure, psi, coef = model_
    return sim.simulate_planned(
        structure,
        psi,
        coef,
        CHANNELS,
        marks_exp,
        horizon,
        np.random.default_rng(seed),
        plan,
    )


def test_empty_plan_is_simulate() -> None:
    structure, psi, coef = hawkes(0.5, 0.5, 2.0)
    run = _planned((structure, psi, coef), sim.Plan(), 200.0, seed=3)
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 200.0, np.random.default_rng(3)
    )
    assert run.log.times.tobytes() == log.times.tobytes()
    assert not run.forced.any()
    assert not run.clamped.any()
    assert run.forced.shape == run.clamped.shape == (log.n,)


def test_plan_flags_forced_and_clamped_events() -> None:
    plan = sim.Plan(
        forced=(sim.ForcedEvent(5.0, {"size": 2.0}), sim.ForcedEvent(50.0)),
        clamps=(sim.RateClamp(20.0, 30.0, 5.0),),
        overrides=(sim.MarkOverride(60.0, 70.0, "sign", 1.0),),
    )
    run = _planned(hawkes(0.5, 0.5, 2.0), plan, seed=1)
    t = run.log.times
    np.testing.assert_array_equal(t[run.forced], [5.0, 50.0])
    assert run.log.marks["size"][t == 5.0][0] == 2.0
    assert np.array_equal(run.clamped, (t >= 20.0) & (t < 30.0))
    in_override = (t >= 60.0) & (t < 70.0)
    assert np.all(run.log.marks["sign"][in_override] == 1.0)


def test_clamp_replaces_an_invalid_intensity() -> None:
    """Inside a clamp the model's λ is never evaluated: a negative one is fine."""
    plan = sim.Plan(clamps=(sim.RateClamp(0.0, 100.0, 1.0),))
    run = _planned(poisson(-1.0), plan, seed=2)
    assert run.log.n > 50
    assert run.clamped.all()


def test_forced_events_respect_the_thinning_bound() -> None:
    """A dense forced schedule on a PowerK truth: windows end at every forced
    time, and λ never exceeds its bound (BoundViolationError would be raised)."""
    model_ = model(
        (Excite(KernelKind.POWER, Mark("size"), ALL),),
        ({"power_c": 0.05, "power_p": 2.5},),
        0.3,
        ((0.4,),),
        Link.SOFTPLUS,
    )
    forced = tuple(sim.ForcedEvent(1.0 + 0.37 * k) for k in range(200))
    run = _planned(model_, sim.Plan(forced=forced), 100.0, seed=4)
    assert int(run.forced.sum()) == 200
    assert np.all(np.diff(run.log.times) > 0.0)


@pytest.mark.parametrize(
    "plan",
    [
        sim.Plan(forced=(sim.ForcedEvent(2.0), sim.ForcedEvent(1.0))),
        sim.Plan(forced=(sim.ForcedEvent(1.0), sim.ForcedEvent(1.0))),
        sim.Plan(forced=(sim.ForcedEvent(101.0),)),
        sim.Plan(forced=(sim.ForcedEvent(-1.0),)),
        sim.Plan(forced=(sim.ForcedEvent(1.0, {"colour": 1.0}),)),
        sim.Plan(clamps=(sim.RateClamp(0.0, 2.0, 1.0), sim.RateClamp(1.0, 3.0, 1.0))),
        sim.Plan(clamps=(sim.RateClamp(2.0, 1.0, 1.0),)),
        sim.Plan(clamps=(sim.RateClamp(0.0, 1.0, -1.0),)),
        sim.Plan(clamps=(sim.RateClamp(0.0, 1.0, math.nan),)),
        sim.Plan(overrides=(sim.MarkOverride(0.0, 1.0, "colour", 1.0),)),
        sim.Plan(
            overrides=(
                sim.MarkOverride(0.0, 2.0, "size", 1.0),
                sim.MarkOverride(1.0, 3.0, "size", 2.0),
            )
        ),
    ],
)
def test_malformed_plans_rejected(plan: sim.Plan) -> None:
    with pytest.raises(InvalidSimulationInputError):
        _planned(poisson(1.0), plan)


def test_forced_mark_values_are_checked() -> None:
    plan = sim.Plan(forced=(sim.ForcedEvent(1.0, {"sign": 0.5}),))
    with pytest.raises(InvalidSimulationInputError):
        _planned(poisson(1.0), plan)


def test_a_slowly_exploding_exp_link_truth_raises_instead_of_hanging() -> None:
    """Positive self-excitation under the exp link has no stationary regime.

    Found by three agents independently: the bound grew while windows shrank
    to the minimum, so thinning ran at a runaway rate below ``max_events``
    and hung for minutes. It must raise :class:`ExplosionError` promptly.
    """
    import time

    feature = Excite(KernelKind.EXP, One(), ALL)
    structure = Structure((feature,), Link.EXP)
    psi: PsiAssignment = ({PsiSlot((0,), "exp_rate"): 1.0},)
    coef = Coefficients(intercept=0.0, per_feature=((1.5,),))
    started = time.perf_counter()
    with pytest.raises(ExplosionError):
        simulate(
            structure,
            psi,
            coef,
            CHANNELS,
            marks_exp,
            400.0,
            np.random.default_rng(7),
            max_events=20_000,
        )
    assert time.perf_counter() - started < 30.0
