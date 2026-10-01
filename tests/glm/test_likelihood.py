"""Instrument tests for the exact likelihood (SPEC §2.2, §6.3 test 1, analytic part).

``log L = Σᵢ log λ(tᵢ) - ∫₀ᵀ λ(t) dt``. Checked against closed forms written
here independently (homogeneous Poisson; exponential Hawkes by Ozaki's
recursion), and, for the links whose compensator needs quadrature, against
adaptive ``scipy.integrate.quad`` of the brute-force intensity, with the
quadrature's own a-posteriori error estimate required to cover the error.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import integrate
from test_features import (
    CHANNELS,
    PICK,
    SHARP,
    brute_columns,
    phase_switches,
    pieces,
    psi_for,
    small_log,
)

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import EventLog, Floats
from sciagent.glm.features import design
from sciagent.glm.grammar import (
    ALL,
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
    Product,
    PsiSlot,
    Structure,
    Trend,
)
from sciagent.glm.likelihood import (
    LikelihoodError,
    compensator,
    compensator_error,
    integrated_intensity,
    log_likelihood,
)


def inverse_link(link: Link, rate: float) -> float:
    match link:
        case Link.IDENTITY:
            return rate
        case Link.EXP:
            return math.log(rate)
        case Link.SOFTPLUS:
            return math.log(math.expm1(rate))


def apply_link(link: Link, eta: float) -> float:
    match link:
        case Link.IDENTITY:
            return eta
        case Link.EXP:
            return math.exp(eta)
        case Link.SOFTPLUS:
            return (
                math.log1p(math.exp(eta))
                if eta < 30
                else eta + math.log1p(math.exp(-eta))
            )


def brute_intensity(
    features: tuple[Feature, ...],
    psi: tuple[dict[PsiSlot, float], ...],
    log: EventLog,
    link: Link,
    theta: Floats,
    t: float,
) -> float:
    row = [1.0]
    for f, s in zip(features, psi, strict=True):
        row.extend(brute_columns(f, s, log, t))
    return apply_link(link, float(np.dot(theta, row)))


# --------------------------------------------------------------------------
# Homogeneous Poisson: n log μ - μT under every link
# --------------------------------------------------------------------------


@pytest.mark.parametrize("link", list(Link))
@pytest.mark.parametrize("feature", [Trend(), Excite(KernelKind.POWER, One(), ALL)])
def test_homogeneous_poisson(link: Link, feature: Feature) -> None:
    log = small_log(seed=3, n=20, horizon=17.0)
    mu = 1.3
    psi = (psi_for(feature),)
    d = design(Structure((feature,), link), psi, log, CHANNELS)
    theta = np.array([inverse_link(link, mu), 0.0])
    want = log.n * math.log(mu) - mu * log.horizon
    assert log_likelihood(theta, d, link) == pytest.approx(want, rel=1e-12)
    assert integrated_intensity(theta, d, link) == pytest.approx(
        mu * log.horizon, rel=1e-12
    )
    lam = compensator(
        theta,
        Structure((feature,), link),
        psi,
        log,
        CHANNELS,
        link,
        np.array([0.0, 4.0, log.horizon]),
    )
    np.testing.assert_allclose(
        lam, [0.0, 4.0 * mu, mu * log.horizon], rtol=1e-12, atol=1e-14
    )


# --------------------------------------------------------------------------
# Exponential Hawkes, identity link: Ozaki's recursion
# --------------------------------------------------------------------------


def ozaki_loglik(
    times: list[float], horizon: float, mu: float, alpha: float, beta: float
) -> float:
    """``mu + alpha Σ β e^{-β(t - tⱼ)}``: Ozaki's (1979) recursion."""
    a = 0.0
    total = 0.0
    for i, t in enumerate(times):
        if i > 0:
            a = math.exp(-beta * (t - times[i - 1])) * (1.0 + a)
        total += math.log(mu + alpha * beta * a)
    total -= mu * horizon
    total -= alpha * sum(1.0 - math.exp(-beta * (horizon - t)) for t in times)
    return total


@settings(max_examples=60)
@given(
    gaps=st.lists(st.floats(1e-4, 3.0), min_size=1, max_size=60),
    tail=st.floats(0.0, 5.0),
    beta=st.sampled_from([0.25, 0.5, 1.0, 2.0, 4.0, 8.0]),
    mu=st.floats(0.05, 3.0),
    alpha=st.floats(0.0, 0.95),
)
def test_exponential_hawkes_matches_ozaki(
    gaps: list[float], tail: float, beta: float, mu: float, alpha: float
) -> None:
    times = np.cumsum(gaps)
    horizon = float(times[-1]) + tail
    log = EventLog.create(times, {}, horizon)
    feature = Excite(KernelKind.EXP, One(), ALL)
    psi = ({PsiSlot((0,), "exp_rate"): beta},)
    d = design(Structure((feature,)), psi, log, ())
    got = log_likelihood(np.array([mu, alpha]), d, Link.IDENTITY)
    want = ozaki_loglik([float(t) for t in times], horizon, mu, alpha, beta)
    assert got == pytest.approx(want, rel=1e-10, abs=1e-10)


def test_identity_non_positive_intensity_is_minus_infinity() -> None:
    log = small_log()
    feature = Excite(KernelKind.EXP, One(), ALL)
    d = design(Structure((feature,)), (psi_for(feature),), log, CHANNELS)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert log_likelihood(np.array([0.0, 0.5]), d, Link.IDENTITY) == -math.inf
        assert log_likelihood(np.array([-1.0, 0.5]), d, Link.IDENTITY) == -math.inf


def test_exp_link_overflow_is_minus_infinity_not_nan() -> None:
    log = small_log()
    feature = Trend()
    d = design(Structure((feature,), Link.EXP), ({},), log, CHANNELS)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert log_likelihood(np.array([800.0, 0.0]), d, Link.EXP) == -math.inf
        # Very negative η under softplus: finite, and close to the exp link.
        ll_sp = log_likelihood(np.array([-800.0, 0.0]), d, Link.SOFTPLUS)
        ll_ex = log_likelihood(np.array([-800.0, 0.0]), d, Link.EXP)
        assert math.isfinite(ll_sp)
        assert ll_sp == pytest.approx(ll_ex, rel=1e-12)


def test_theta_length_is_checked() -> None:
    log = small_log()
    d = design(Structure((Periodic(),)), (psi_for(Periodic()),), log, CHANNELS)
    with pytest.raises(LikelihoodError):
        log_likelihood(np.array([1.0, 0.0]), d, Link.IDENTITY)
    assert issubclass(LikelihoodError, SciAgentError)


# --------------------------------------------------------------------------
# Quadrature compensator against adaptive quad of the brute-force intensity
# --------------------------------------------------------------------------

QUAD_CASES: list[
    tuple[str, tuple[Feature, ...], Link, list[float], dict[str, float]]
] = [
    (
        "exp-power-periodic",
        (Excite(KernelKind.POWER, One(), ALL), Periodic()),
        Link.EXP,
        [-0.3, 0.6, 0.4, -0.2],
        SHARP,
    ),
    (
        "softplus-exp-sharp",
        (Excite(KernelKind.EXP, Mark("size"), ALL),),
        Link.SOFTPLUS,
        [0.2, 0.9],
        SHARP,
    ),
    (
        "exp-gamma-gate",
        (Gate(Excite(KernelKind.GAMMA, One(), ALL), PhaseWindow()), Trend()),
        Link.EXP,
        [0.1, 0.5, -0.4],
        PICK,
    ),
    (
        "softplus-lma-expof",
        (Gate(Excite(KernelKind.EXP, ExpOf("mag"), ALL), LastMarkAbove("mag")),),
        Link.SOFTPLUS,
        [-0.5, 1.2],
        PICK,
    ),
    (
        "identity-product",
        (Product(Excite(KernelKind.POWER, One(), ALL), Periodic()), Trend()),
        Link.IDENTITY,
        [2.0, 0.05, -0.05, 0.4],
        SHARP,
    ),
]


def _brute_compensator(
    features: tuple[Feature, ...],
    psi: tuple[dict[PsiSlot, float], ...],
    log: EventLog,
    link: Link,
    theta: Floats,
    upto: float,
) -> float:
    extra: list[float] = []
    for f, s in zip(features, psi, strict=True):
        extra.extend(phase_switches(f, s, log.horizon))
    total = 0.0
    for a, b in pieces(log, extra):
        if a >= upto:
            break
        total += integrate.quad(
            lambda t: brute_intensity(features, psi, log, link, theta, t),
            a,
            min(b, upto),
            epsabs=1e-14,
            epsrel=1e-13,
            limit=400,
        )[0]
    return total


@pytest.mark.parametrize(
    ("features", "link", "theta", "pick"),
    [c[1:] for c in QUAD_CASES],
    ids=[c[0] for c in QUAD_CASES],
)
def test_quadrature_compensator_is_within_its_error_estimate(
    features: tuple[Feature, ...],
    link: Link,
    theta: list[float],
    pick: dict[str, float],
) -> None:
    log = small_log()
    psi = tuple(psi_for(f, pick) for f in features)
    structure = Structure(features, link)
    th = np.array(theta)
    d = design(structure, psi, log, CHANNELS)
    got = integrated_intensity(th, d, link)
    want = _brute_compensator(features, psi, log, link, th, log.horizon)
    err = abs(got - want)
    est = compensator_error(th, structure, psi, log, CHANNELS, link)
    # The estimate covers the actual error ...
    assert err <= est + 1e-12, (err, est)
    # ... is small in absolute terms at the default rule ...
    assert est < 1e-6 * log.horizon
    # ... and is not wildly pessimistic.
    assert est <= max(1e3 * err, 1e-11), (err, est)
    # The log-likelihood uses the same compensator.
    events = sum(
        math.log(brute_intensity(features, psi, log, link, th, float(t)))
        for t in log.times
    )
    assert log_likelihood(th, d, link) == pytest.approx(
        events - want, abs=10 * est + 1e-9
    )


@pytest.mark.parametrize(
    ("features", "link", "theta", "pick"),
    [c[1:] for c in QUAD_CASES]
    + [
        (
            (
                Gate(Excite(KernelKind.POWER, Mark("size"), ALL), PhaseWindow()),
                Periodic(),
                Gate(
                    Gate(Excite(KernelKind.GAMMA, One(), ALL), LastMarkAbove("mag")),
                    PhaseWindow(),
                ),
            ),
            Link.IDENTITY,
            [0.8, 0.3, 0.2, 0.1, 0.4],
            PICK,
        )
    ],
    ids=[c[0] for c in QUAD_CASES] + ["identity-closed-form"],
)
def test_compensator_at_arbitrary_times(
    features: tuple[Feature, ...],
    link: Link,
    theta: list[float],
    pick: dict[str, float],
) -> None:
    log = small_log()
    psi = tuple(psi_for(f, pick) for f in features)
    structure = Structure(features, link)
    th = np.array(theta)
    t = np.array(
        [log.horizon, 3.3, float(log.times[2]), 0.0, float(log.times[3]) + 1e-4, 9.0]
    )
    got = compensator(th, structure, psi, log, CHANNELS, link, t)
    for i, ti in enumerate(t):
        want = _brute_compensator(features, psi, log, link, th, float(ti))
        assert got[i] == pytest.approx(want, rel=1e-8, abs=1e-9), ti
    # Monotone for a positive intensity, and Λ(T) agrees with the design.
    d = design(structure, psi, log, CHANNELS)
    assert got[0] == pytest.approx(integrated_intensity(th, d, link), rel=1e-9)


def test_identity_compensator_error_is_zero_when_integrals_are_exact() -> None:
    log = small_log()
    features: tuple[Feature, ...] = (
        Excite(KernelKind.POWER, One(), ALL),
        Gate(Periodic(), PhaseWindow()),
    )
    psi = tuple(psi_for(f) for f in features)
    th = np.array([1.0, 0.4, 0.1, 0.1])
    assert (
        compensator_error(th, Structure(features), psi, log, CHANNELS, Link.IDENTITY)
        == 0.0
    )


def test_log_likelihood_is_deterministic() -> None:
    log = small_log(seed=5, n=40, horizon=30.0)
    features: tuple[Feature, ...] = (
        Excite(KernelKind.POWER, ExpOf("mag"), ALL),
        Periodic(),
    )
    psi = tuple(psi_for(f) for f in features)
    th = np.array([-0.2, 0.3, 0.2, 0.1])
    for link in Link:
        d1 = design(Structure(features, link), psi, log, CHANNELS)
        d2 = design(Structure(features, link), psi, log, CHANNELS)
        a = log_likelihood(
            th if link is not Link.IDENTITY else th + np.array([1.5, 0, 0, 0]), d1, link
        )
        b = log_likelihood(
            th if link is not Link.IDENTITY else th + np.array([1.5, 0, 0, 0]), d2, link
        )
        assert np.float64(a).tobytes() == np.float64(b).tobytes()


def test_dataset_log_likelihood_and_compensator() -> None:
    from test_features import EXCLUDED, complement_pieces, small_dataset

    data = small_dataset()
    log = data.log
    features: tuple[Feature, ...] = (
        Excite(KernelKind.POWER, Mark("size"), ALL),
        Gate(Periodic(), PhaseWindow()),
    )
    psi = tuple(psi_for(f) for f in features)
    for link, theta in [
        (Link.IDENTITY, [0.8, 0.3, 0.2, 0.1]),
        (Link.EXP, [-0.2, 0.3, 0.2, 0.1]),
        (Link.SOFTPLUS, [0.5, 0.3, -0.2, 0.1]),
    ]:
        th = np.array(theta)
        structure = Structure(features, link)
        d = design(structure, psi, data, CHANNELS)
        extra = [
            x
            for f, s in zip(features, psi, strict=True)
            for x in phase_switches(f, s, log.horizon)
        ]

        def lam(t: float, th: Floats = th, link: Link = link) -> float:
            return brute_intensity(features, psi, log, link, th, t)

        parts = complement_pieces(log, extra)
        comp = sum(
            integrate.quad(lam, a, b, epsabs=1e-14, epsrel=1e-13, limit=400)[0]
            for a, b in parts
        )
        counted = [
            float(t)
            for i, t in enumerate(log.times)
            if data.endogenous[i]
            and not any(lo <= float(t) <= hi for lo, hi in EXCLUDED)
        ]
        want = sum(math.log(lam(t)) for t in counted) - comp
        assert log_likelihood(th, d, link) == pytest.approx(want, rel=1e-9), link
        # Λ(t) skips the excluded windows: flat across them.
        t = np.array([2.0, 2.7, 3.5, 6.0, log.horizon])
        got = compensator(th, structure, psi, data, CHANNELS, link, t)
        assert got[0] == pytest.approx(got[1], rel=1e-12) and got[1] == pytest.approx(
            got[2], rel=1e-12
        )
        assert got[-1] == pytest.approx(comp, rel=1e-9)
        upto = sum(
            integrate.quad(lam, a, min(b, 6.0), epsabs=1e-14, epsrel=1e-13, limit=400)[
                0
            ]
            for a, b in parts
            if a < 6.0
        )
        assert got[3] == pytest.approx(upto, rel=1e-9)
