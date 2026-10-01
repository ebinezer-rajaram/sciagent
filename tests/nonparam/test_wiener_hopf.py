"""Instrument tests for the Wiener-Hopf kernel estimator (SPEC §2.3, §6.3 test 5).

Data come from the independent cluster simulator in ``nonparam_sim.py``. Kernel
errors are measured against the truth's *bin averages* on the estimate's own
grid (the estimator returns a piecewise-constant kernel, so that is the quantity
it can recover), plus the truth's mass beyond the support, which it cannot.

Tolerances were calibrated across seeds; each test states the spread it saw.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nonparam_sim import (
    SIGN,
    SIZE,
    Cdf,
    exp_cdf,
    exp_sampler,
    lomax_cdf,
    lomax_sampler,
    simulate,
)

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import EventLog
from sciagent.nonparam.wiener_hopf import (
    DEFAULT,
    KernelEstimate,
    WienerHopfConfig,
    WienerHopfError,
    build_system,
    estimate_kernels,
    intensity,
    log_likelihood,
)

Floats = np.ndarray


def _rel_l1(est: KernelEstimate, d: int, scale: float, cdf: Cdf) -> float:
    """``∫|φ̂ - φ| / ‖φ‖`` with φ = scale·h, against φ's bin averages."""
    e = est.edges
    mass = scale * np.diff(cdf(e))
    err = np.abs(est.kernels[d] * np.diff(e) - mass).sum()
    tail = scale * (1.0 - cdf(e[-1:]))[0]
    return float((err + tail) / scale)


def _exp_log(
    seed: int, horizon: float, *, mark_coef: float = 0.0, with_sign: bool = False
) -> EventLog:
    """μ = 0.5, η = 0.5, β = 1: mean rate 1."""
    rng = np.random.default_rng(seed)
    return simulate(
        rng,
        mu=0.5,
        eta=0.5,
        lags=exp_sampler(1.0),
        horizon=horizon,
        mark_coef=mark_coef,
        with_sign=with_sign,
    )


# --------------------------------------------------------------------------
# The discretised system is the exact Galerkin system (brute force check)
# --------------------------------------------------------------------------


def test_system_matches_brute_force_pair_sums() -> None:
    log = _exp_log(3, 60.0, mark_coef=0.5)
    cfg = WienerHopfConfig(n_bins=6, max_lag=4.0, first_edge=0.2)
    sys = build_system(log, (SIZE,), config=cfg)
    t, T = log.times, log.horizon
    w = np.stack([np.ones_like(t), log.marks["size"] - 1.0])
    e = sys.edges
    lam = w.sum(axis=1) / T
    L = e.size - 1
    gram = np.zeros((2, L, 2, L))
    rhs = np.zeros((2, L))
    for a in range(2):
        for b in range(2):
            s_ab = (w[a] * w[b]).sum() / T
            for i in range(L):
                for j in range(L):
                    acc = 0.0
                    for k in range(t.size):
                        for kk in range(t.size):
                            tau = t[kk] - t[k]
                            if k == kk or abs(tau) >= e[-1]:
                                continue
                            over = min(e[i + 1], e[j + 1] + tau) - max(e[i], e[j] + tau)
                            acc += w[a, k] * w[b, kk] * max(over, 0.0) / (T - abs(tau))
                    acc -= lam[a] * lam[b] * (e[i + 1] - e[i]) * (e[j + 1] - e[j])
                    if i == j:
                        acc += s_ab * (e[i + 1] - e[i])
                    gram[a, i, b, j] = acc
        for i in range(L):
            acc = 0.0
            for k in range(t.size):
                for kk in range(k + 1, t.size):
                    tau = t[kk] - t[k]
                    if e[i] <= tau < e[i + 1]:
                        acc += w[a, k] / (T - tau)
            rhs[a, i] = acc - lam[a] * lam[0] * (e[i + 1] - e[i])
    np.testing.assert_allclose(sys.gram, gram.reshape(2 * L, 2 * L), atol=1e-11)
    np.testing.assert_allclose(sys.rhs, rhs.reshape(-1), atol=1e-11)
    np.testing.assert_allclose(sys.gram, sys.gram.T, atol=1e-12)


# --------------------------------------------------------------------------
# Recovery: univariate exponential Hawkes (μ=0.5, η=0.5, β=1; mean rate 1)
# --------------------------------------------------------------------------


def test_exponential_recovery_fast() -> None:
    # ~5000 events. Spread over 20 seeds at n≈3000: norm sd 0.036, baseline
    # sd 0.032, relative L¹ 0.37 ± 0.07; tolerances are >= 3 sd at n≈5000.
    log = _exp_log(11, 5000.0)
    est = estimate_kernels(log, ())
    assert est.drivers == ("arrival",)
    assert abs(est.norms[0] - 0.5) < 0.1
    assert abs(est.baseline - 0.5) < 0.1
    assert _rel_l1(est, 0, 0.5, exp_cdf(1.0)) < 0.5


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_exponential_recovery(seed: int) -> None:
    # n≈20000; over 6 seeds: norm sd 0.007, relative L¹ ≤ 0.20, sup ≤ 0.052.
    log = _exp_log(seed, 20000.0)
    est = estimate_kernels(log, ())
    assert abs(est.norms[0] - 0.5) < 0.04
    assert abs(est.baseline - 0.5) < 0.04
    assert abs(est.mean_rate - 1.0) < 0.06
    assert _rel_l1(est, 0, 0.5, exp_cdf(1.0)) < 0.25
    # sup error on bins wider than 0.1 (narrow bins near 0 are noisy by design)
    e = est.edges
    wide = np.diff(e) > 0.1
    truth = 0.5 * np.diff(exp_cdf(1.0)(e)) / np.diff(e)
    assert np.max(np.abs(est.kernels[0] - truth)[wide]) < 0.08


@pytest.mark.slow
def test_power_law_recovery() -> None:
    c, p = 0.2, 2.0
    rng = np.random.default_rng(5)
    log = simulate(rng, mu=0.5, eta=0.5, lags=lomax_sampler(c, p), horizon=20000.0)
    est = estimate_kernels(log, ())
    # over 6 seeds: norm 0.488 ± 0.018, relative L¹ ≤ 0.18 (incl. ~5% tail
    # mass beyond the support, which the estimate cannot see)
    assert abs(est.norms[0] - 0.5) < 0.06
    assert _rel_l1(est, 0, 0.5, lomax_cdf(c, p)) < 0.3
    # the shape is power-law, not exponential: the early bins carry the mass
    e = est.edges
    early = e[1:] <= 0.2
    early_mass = float((est.kernels[0] * np.diff(e))[early].sum())
    true_early = 0.5 * float(lomax_cdf(c, p)(np.array([e[1:][early][-1]]))[0])
    assert abs(early_mass - true_early) < 0.08


# --------------------------------------------------------------------------
# Cross-kernels: size -> arrival
# --------------------------------------------------------------------------


def test_cross_kernel_present_fast() -> None:
    # truth: φ_size = η a h with η a = 0.4. Cross-norm sd ≈ 0.10 at n≈3000
    # (20 seeds), ≈ 0.055 at n≈10000; tolerance ≈ 3.5 sd.
    log = _exp_log(21, 10000.0, mark_coef=0.8)
    est = estimate_kernels(log, (SIZE,))
    assert est.drivers == ("arrival", "size")
    assert abs(est.norms[1] - 0.4) < 0.2
    assert abs(est.norms[0] - 0.5) < 0.1


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cross_kernel_present(seed: int) -> None:
    log = _exp_log(100 + seed, 20000.0, mark_coef=0.8)
    est = estimate_kernels(log, (SIZE,))
    # over 6 seeds at n≈20000: cross norm 0.398 ± 0.026, self 0.506 ± 0.007
    assert abs(est.norms[1] - 0.4) < 0.08
    assert abs(est.norms[0] - 0.5) < 0.05
    assert _rel_l1(est, 1, 0.4, exp_cdf(1.0)) < 0.4


@pytest.mark.parametrize("seed", [31, 32, 33, 34])
def test_cross_kernel_absent(seed: int) -> None:
    """No mark dependence: cross norms are noise of sd ≈ sqrt(max_lag / n).

    Calibration (40 seeds, n≈3000): size sd 0.067, sign sd 0.070, max |norm|
    0.167; at n≈20000 (10 seeds): sd ≤ 0.034, max 0.071. At n≈10000 the sd is
    ≈ 0.04, so 0.15 is ≈ 4 sd, and far below the present-case norm 0.4.
    """
    log = _exp_log(seed, 10000.0, mark_coef=0.0, with_sign=True)
    est = estimate_kernels(log, (SIZE, SIGN))
    assert est.drivers == ("arrival", "size", "sign")
    assert abs(est.norms[1]) < 0.15
    assert abs(est.norms[2]) < 0.15
    assert abs(est.norms[0] - 0.5) < 0.1


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cross_kernel_absent_large(seed: int) -> None:
    log = _exp_log(200 + seed, 20000.0, mark_coef=0.0, with_sign=True)
    est = estimate_kernels(log, (SIZE, SIGN))
    assert abs(est.norms[1]) < 0.1
    assert abs(est.norms[2]) < 0.1


def test_poisson_gives_zero_kernels() -> None:
    rng = np.random.default_rng(41)
    log = simulate(
        rng, mu=1.0, eta=0.0, lags=exp_sampler(1.0), horizon=10000.0, with_sign=True
    )
    est = estimate_kernels(log, (SIZE, SIGN))
    # 40 seeds at n≈3000: norm sd ≤ 0.058, max 0.139; ≈ 0.03 at n≈10000
    assert np.all(np.abs(est.norms) < 0.15)
    assert abs(est.baseline - est.mean_rate) < 0.1


# --------------------------------------------------------------------------
# Determinism, runtime, equivariance
# --------------------------------------------------------------------------


def test_deterministic_byte_identical() -> None:
    a = estimate_kernels(_exp_log(7, 1500.0, mark_coef=0.5), (SIZE,))
    b = estimate_kernels(_exp_log(7, 1500.0, mark_coef=0.5), (SIZE,))
    for x, y in [
        (a.kernels, b.kernels),
        (a.edges, b.edges),
        (a.lags, b.lags),
        (a.norms, b.norms),
    ]:
        assert x.tobytes() == y.tobytes()
    assert (a.baseline, a.mean_rate) == (b.baseline, b.mean_rate)


def test_runtime_n2000() -> None:
    log = _exp_log(8, 2000.0, mark_coef=0.5, with_sign=True)
    assert 1500 < log.n < 2600
    start = time.perf_counter()
    est = estimate_kernels(log, (SIZE, SIGN))
    log_likelihood(est, log, (SIZE, SIGN))
    assert time.perf_counter() - start < 2.0


@settings(max_examples=15)
@given(
    scale=st.floats(min_value=0.1, max_value=10.0),
    seed=st.integers(min_value=0, max_value=2**16),
)
def test_time_rescaling_equivariance(scale: float, seed: int) -> None:
    """Rescaling time by c rescales lags by c and kernels by 1/c; norms fixed."""
    log = _exp_log(seed, 300.0, mark_coef=0.5)
    scaled = EventLog.create(log.times * scale, log.marks, log.horizon * scale)
    a = estimate_kernels(log, (SIZE,))
    b = estimate_kernels(scaled, (SIZE,))
    np.testing.assert_allclose(b.edges, a.edges * scale, rtol=1e-9)
    np.testing.assert_allclose(b.kernels * scale, a.kernels, rtol=1e-6, atol=1e-9)
    np.testing.assert_allclose(b.norms, a.norms, rtol=1e-6, atol=1e-9)
    assert b.baseline == pytest.approx(a.baseline / scale, rel=1e-6)


def test_channel_order_permutes_drivers() -> None:
    log = _exp_log(9, 800.0, mark_coef=0.5, with_sign=True)
    a = estimate_kernels(log, (SIZE, SIGN))
    b = estimate_kernels(log, (SIGN, SIZE))
    assert b.drivers == ("arrival", "sign", "size")
    np.testing.assert_allclose(b.kernels[[0, 2, 1]], a.kernels, atol=1e-10)


# --------------------------------------------------------------------------
# Predictive intensity and log-likelihood
# --------------------------------------------------------------------------


def test_intensity_is_the_linear_filter() -> None:
    log = _exp_log(12, 400.0, mark_coef=0.5)
    est = estimate_kernels(log, (SIZE,))
    q = np.array([0.0, 3.3, 57.1, 211.0, 399.9])
    lam = intensity(est, log, (SIZE,), q)
    w = np.stack([np.ones(log.n), log.marks["size"] - 1.0])
    for i, ti in enumerate(q):
        ref = est.baseline
        for k in range(log.n):
            lag = ti - log.times[k]
            if 0 < lag < est.edges[-1]:
                b = int(np.searchsorted(est.edges, lag, side="right")) - 1
                ref += float(w[:, k] @ est.kernels[:, b])
        assert lam[i] == pytest.approx(max(ref, est.floor), rel=1e-12, abs=1e-12)


def test_log_likelihood_matches_closed_form_compensator() -> None:
    """Floor inactive: ∫λ = μT + Σ_k Σ_d w_kd ∫_0^{min(M, T - t_k)} φ_d exactly."""
    train = _exp_log(13, 4000.0)
    test = _exp_log(14, 500.0)
    est = estimate_kernels(train, ())
    lam_ev = intensity(est, test, (), test.times)
    grid = np.linspace(0.0, test.horizon, 100001)
    assert np.all(intensity(est, test, (), grid) > est.floor)
    pc = np.concatenate([[0.0], np.cumsum(est.kernels[0] * np.diff(est.edges))])
    comp = est.baseline * test.horizon
    for k in range(test.n):
        reach = min(est.edges[-1], test.horizon - test.times[k])
        comp += float(np.interp(reach, est.edges, pc))
    expected = float(np.log(lam_ev).sum()) - comp
    assert log_likelihood(est, test, ()) == pytest.approx(expected, rel=1e-10)


def test_log_likelihood_with_active_floor_matches_quadrature() -> None:
    """Floor active somewhere: compare with a midpoint rule of known error bound.

    λ is piecewise constant, so the midpoint rule on cells of width h errs by
    at most h · TV(λ); TV is estimated from the grid itself (doubled for safety).
    """
    train = _exp_log(13, 2000.0, mark_coef=0.5)
    test = _exp_log(14, 500.0, mark_coef=0.5)
    est = estimate_kernels(train, (SIZE,))
    h = test.horizon / 400000
    mids = (np.arange(400000) + 0.5) * h
    lam = intensity(est, test, (SIZE,), mids)
    assert np.any(lam == est.floor)
    bound = 2.0 * h * float(np.abs(np.diff(lam)).sum())
    lam_ev = intensity(est, test, (SIZE,), test.times)
    approx = float(np.log(lam_ev).sum()) - h * float(lam.sum())
    assert abs(log_likelihood(est, test, (SIZE,)) - approx) < bound


def test_heldout_likelihood_beats_poisson_on_hawkes() -> None:
    train = _exp_log(15, 3000.0)
    test = _exp_log(16, 1000.0)
    est = estimate_kernels(train, ())
    ll = log_likelihood(est, test, ())
    rate = test.n / test.horizon
    ll_poisson = test.n * np.log(rate) - rate * test.horizon
    assert ll > ll_poisson + 20.0


# --------------------------------------------------------------------------
# Typed errors
# --------------------------------------------------------------------------


def test_errors_are_typed() -> None:
    assert issubclass(WienerHopfError, SciAgentError)
    log = _exp_log(17, 500.0)
    with pytest.raises(WienerHopfError):
        estimate_kernels(log, (SIGN,))  # log has no sign channel
    tiny = EventLog.create([1.0, 2.0], {"size": [1.0, 1.0]}, 3.0)
    with pytest.raises(WienerHopfError):
        estimate_kernels(tiny, (SIZE,))
    with pytest.raises(WienerHopfError):
        WienerHopfConfig(n_bins=0)
    assert WienerHopfConfig() == DEFAULT


def test_ridge_resolves_a_collinear_driver() -> None:
    """A constant sign channel duplicates arrivals; the ridge splits the norm."""
    base = _exp_log(18, 3000.0)
    log = EventLog.create(
        base.times, {**base.marks, "sign": np.ones(base.n)}, base.horizon
    )
    est = estimate_kernels(log, (SIGN,))
    assert np.all(np.isfinite(est.kernels))
    assert est.norms[0] == pytest.approx(est.norms[1], abs=1e-6)
    assert float(est.norms.sum()) == pytest.approx(0.5, abs=0.1)


SHARPEST = {
    # the sharpest kernels the ψ grid allows (grids.py): ExpK at exp_rate 8,
    # PowerK at power_c 0.05 with the lightest and heaviest power_p
    "exp_rate=8": (exp_sampler(8.0), exp_cdf(8.0)),
    "power_c=0.05,p=2": (lomax_sampler(0.05, 2.0), lomax_cdf(0.05, 2.0)),
    "power_c=0.05,p=1.2": (lomax_sampler(0.05, 1.2), lomax_cdf(0.05, 1.2)),
}


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(SHARPEST))
def test_sharpest_grid_kernels_are_resolved(name: str) -> None:
    """The default grid resolves the sharpest ψ-grid kernels' norms.

    Two checks over 4 seeds at n≈20000 (η = 0.5, cross 0.4):

    - Resolution, paired and so free of sampling noise: the default grid's
      norms agree with a 5x finer first bin and 40 bins on the same data. The
      former default (first_edge 0.05) failed this for PowerK(c=0.05, p=2) by
      0.006 in the self norm; refining past first_edge 0.01 moves it < 3e-4.
    - Accuracy against the truth's mass *inside the support* (η·F(max_lag)):
      PowerK(p=1.2) keeps only 65% of its mass within 10 mean inter-event
      times, a support limit, not a resolution one. Measured means: ExpK(8)
      0.505 / 0.395, PowerK(p=2) 0.490 / 0.400 (targets 0.4975 / 0.398),
      PowerK(p=1.2) 0.328 / 0.250 (targets 0.327 / 0.262). Per-seed sd ≤ 0.025,
      so the mean of 4 has sd ≤ 0.013.
    """
    sampler, cdf = SHARPEST[name]
    fine = WienerHopfConfig(first_edge=DEFAULT.first_edge / 5, n_bins=40)
    norms, fine_norms = [], []
    for seed in range(4):
        rng = np.random.default_rng(700 + seed)
        log = simulate(
            rng, mu=0.5, eta=0.5, lags=sampler, horizon=20000.0, mark_coef=0.8
        )
        norms.append(estimate_kernels(log, (SIZE,)).norms)
        fine_norms.append(estimate_kernels(log, (SIZE,), config=fine).norms)
    mean = np.mean(norms, axis=0)
    np.testing.assert_allclose(mean, np.mean(fine_norms, axis=0), atol=2e-3)
    inside = float(cdf(np.array([DEFAULT.max_lag]))[0])
    assert abs(mean[0] - 0.5 * inside) < 0.03
    assert abs(mean[1] - 0.4 * inside) < 0.04
