"""Instrument tests for the certified fitter (SPEC §2.2, §6.3 test 2).

What is checked, and against what:

- **Determinism.** Two fits of the same input are equal, θ byte for byte.
- **Certificates.** Every certified fit in this file has a duality gap below
  the configured tolerance (:func:`checked`); a forced solver failure is
  flagged, never raised silently and never reported as certified.
- **Independent optimiser.** At the chosen ψ, an unconstrained scipy BFGS on
  the negative log-likelihood, written here from the design arrays with no
  fitter code, reaches the certified objective and θ, and never beats the
  certified bound.
- **Recovery.** Parameters and ψ of simulated truths (``sciagent.glm.simulate``
  shares no code with the fitter) are recovered within a few standard errors.
- **ψ profiling.** Exhaustive and coordinate-wise search agree where both apply.
- **Ordering.** On held-out data the true structure beats a wrong one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import cache

import numpy as np
import pytest
from scipy import optimize, stats

from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.features import Design, design, evaluate_columns
from sciagent.glm.fit import (
    PSI_FULL_GRID_MAX,
    FitConfig,
    FitConfigError,
    FitResult,
    UncertifiedFitError,
    evaluate_log_likelihood,
    fit,
    fit_cache_key,
    predictive_pvalues,
    to_coefficients,
)
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
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
    psi_slots,
)
from sciagent.glm.likelihood import compensator, log_likelihood
from sciagent.glm.simulate import Coefficients, PsiAssignment, intensity, simulate

CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
)

EXP_K = Excite(KernelKind.EXP, One(), ALL)
SIZE_K = Excite(KernelKind.EXP, Mark("size"), ALL)
POWER_K = Excite(KernelKind.POWER, One(), ALL)
HAWKES = Structure((EXP_K,), Link.IDENTITY)


def marks(rng: np.random.Generator) -> Mapping[str, float]:
    """Sizes Exponential(mean 1)."""
    return {"size": float(rng.exponential(1.0))}


def psi_of(feature: Feature, values: Mapping[str, float]) -> dict[PsiSlot, float]:
    return {slot: values[slot.name] for slot in psi_slots(feature)}


def draw(
    features: tuple[Feature, ...],
    values: tuple[Mapping[str, float], ...],
    coef: Coefficients,
    link: Link,
    horizon: float,
    seed: int,
) -> EventLog:
    structure = Structure(features, link)
    psi = tuple(psi_of(f, v) for f, v in zip(features, values, strict=True))
    return simulate(
        structure, psi, coef, CHANNELS, marks, horizon, np.random.default_rng(seed)
    )


@cache
def hawkes_log(horizon: float = 400.0, seed: int = 1) -> EventLog:
    """μ = 0.5, η = 0.5, β = 1: mean rate 1."""
    coef = Coefficients(0.5, ((0.5,),))
    return draw((EXP_K,), ({"exp_rate": 1.0},), coef, Link.IDENTITY, horizon, seed)


@cache
def exp_link_log(horizon: float = 400.0, seed: int = 2) -> EventLog:
    """``λ = exp(0.3 - 0.6·φ_ExpK(β=2) + 0.5 sin - 0.3 cos)``, P = 25.

    Self-*inhibiting*: under the exp link, positive self-excitation makes λ
    grow exponentially in the event count, and the process explodes.
    """
    coef = Coefficients(0.3, ((-0.6,), (0.5, -0.3)))
    values: tuple[Mapping[str, float], ...] = ({"exp_rate": 2.0}, {"period": 25.0})
    return draw((EXP_K, Periodic()), values, coef, Link.EXP, horizon, seed)


def observational(log: EventLog) -> tuple[Dataset, ...]:
    return (Dataset.observational(log),)


def checked(result: FitResult, config: FitConfig | None = None) -> FitResult:
    """Every certified fit must carry a gap within tolerance (§6.3 test 2)."""
    tol = (config or FitConfig()).gap_tol_rel
    if result.certified:
        assert result.duality_gap_rel <= tol, result
        assert result.solver_status in ("optimal", "optimal_inaccurate")
        assert result.n_uncertified_points == 0
        assert math.isfinite(result.log_likelihood)
    return result


def designs_at(result: FitResult, datasets: tuple[Dataset, ...]) -> list[Design]:
    return [design(result.structure, result.psi, d, CHANNELS) for d in datasets]


# --------------------------------------------------------------------------
# An independent objective, written from the design arrays alone
# --------------------------------------------------------------------------


def _softplus(x: Floats) -> Floats:
    out: Floats = np.logaddexp(0.0, x)
    return out


def _sigmoid(x: Floats) -> Floats:
    out: Floats = 0.5 * (1.0 + np.tanh(0.5 * x))
    return out


def negative_log_likelihood(
    theta: Floats, designs: list[Design], link: Link
) -> tuple[float, Floats]:
    """``-log L`` and its gradient over datasets, with plain numpy folds."""
    value = 0.0
    grad = np.zeros_like(theta)
    for d in designs:
        x, a, w = d.at_events, d.at_nodes, d.weights
        ze = x @ theta
        match link:
            case Link.IDENTITY:
                if np.min(ze) <= 0:
                    return math.inf, grad
                value += float(d.integrals @ theta - np.sum(np.log(ze)))
                grad += d.integrals - x.T @ (1.0 / ze)
            case Link.EXP:
                zn = a @ theta
                if np.max(zn) > 700:
                    return math.inf, grad
                en = np.exp(zn)
                value += float(w @ en - np.sum(ze))
                grad += a.T @ (w * en) - np.sum(x, axis=0)
            case Link.SOFTPLUS:
                zn = a @ theta
                sp_e = _softplus(ze)
                value += float(w @ _softplus(zn) - np.sum(np.log(sp_e)))
                grad += a.T @ (w * _sigmoid(zn)) - x.T @ (_sigmoid(ze) / sp_e)
    return value, grad


def independent_minimum(
    result: FitResult, datasets: tuple[Dataset, ...]
) -> tuple[float, Floats]:
    """BFGS from a perturbed start, on the objective above."""
    designs = designs_at(result, datasets)
    theta = np.array(result.theta)
    start = theta * 0.9 + 0.02
    sol = optimize.minimize(
        negative_log_likelihood,
        start,
        args=(designs, result.link),
        jac=True,
        method="BFGS",
        options={"gtol": 1e-9, "maxiter": 10_000},
    )
    return float(sol.fun), np.asarray(sol.x, dtype=np.float64)


# --------------------------------------------------------------------------
# Determinism, certificates, the independent optimiser
# --------------------------------------------------------------------------


def test_fit_is_deterministic_and_certified() -> None:
    data = observational(hawkes_log())
    a = checked(fit(HAWKES, data, CHANNELS))
    b = fit(HAWKES, data, CHANNELS)
    assert a == b
    assert np.array(a.theta).tobytes() == np.array(b.theta).tobytes()
    assert a.log_likelihood.hex() == b.log_likelihood.hex()
    assert a.certified
    assert a.psi_search == "exhaustive"
    assert a.n_psi_points == 6
    assert a.theta_labels == ("θ0", "φ1")


@pytest.mark.parametrize("link", [Link.IDENTITY, Link.EXP, Link.SOFTPLUS])
def test_certified_optimum_matches_independent_optimiser(link: Link) -> None:
    log = exp_link_log() if link is not Link.IDENTITY else hawkes_log()
    features: tuple[Feature, ...] = (
        (EXP_K,) if link is Link.IDENTITY else (EXP_K, Periodic())
    )
    data = observational(log)
    result = checked(fit(Structure(features, link), data, CHANNELS))
    assert result.certified
    value, theta = independent_minimum(result, data)
    certified_value = -result.log_likelihood
    scale = max(1.0, abs(certified_value))
    # The independent optimiser cannot beat the certified lower bound ...
    assert value >= certified_value - result.duality_gap - 1e-9 * scale
    # ... and reaches the same optimum.
    assert abs(value - certified_value) <= 1e-7 * scale
    np.testing.assert_allclose(theta, result.theta, rtol=1e-4, atol=1e-4)


def test_reported_log_likelihood_is_the_likelihood_module_value() -> None:
    log_a = hawkes_log()
    log_b = hawkes_log(300.0, 5)
    data = (Dataset.observational(log_a, "a"), Dataset.observational(log_b, "b"))
    result = checked(fit(HAWKES, data, CHANNELS))
    per = [
        log_likelihood(np.array(result.theta), d, Link.IDENTITY)
        for d in designs_at(result, data)
    ]
    assert result.log_likelihood_per_dataset == tuple(per)
    assert result.log_likelihood == math.fsum(per)
    assert result.dataset_labels == ("a", "b")
    assert result.n_events == log_a.n + log_b.n
    k = 2 + 1  # θ0, η and the exp_rate slot
    assert result.n_params == k
    assert result.bic == k * math.log(result.n_events) - 2.0 * result.log_likelihood
    assert result.aic == 2 * k - 2.0 * result.log_likelihood
    held = evaluate_log_likelihood(result, data, CHANNELS)
    assert held == result.log_likelihood_per_dataset


def test_uncertified_fit_is_flagged_not_hidden() -> None:
    data = observational(exp_link_log())
    config = FitConfig(max_iter=1)
    result = checked(fit(Structure((EXP_K,), Link.EXP), data, CHANNELS, config=config))
    assert not result.certified
    assert result.solver_status != "optimal"
    assert result.n_uncertified_points > 0
    with pytest.raises(UncertifiedFitError):
        result.require_certified()
    good = fit(Structure((EXP_K,), Link.EXP), data, CHANNELS)
    assert good.require_certified() is good


def test_config_is_validated() -> None:
    with pytest.raises(FitConfigError):
        FitConfig(max_iter=0)
    with pytest.raises(FitConfigError):
        FitConfig(gap_tol_rel=0.0)
    with pytest.raises(FitConfigError):
        FitConfig(inner_solver="ipopt")
    with pytest.raises(FitConfigError):
        fit(
            Structure((EXP_K,), Link.SOFTPLUS),
            observational(hawkes_log()),
            CHANNELS,
            config=FitConfig(inner_solver="clarabel"),
        )
    with pytest.raises(FitConfigError):
        fit(HAWKES, (), CHANNELS)


@pytest.mark.parametrize("link", [Link.IDENTITY, Link.EXP])
def test_clarabel_and_newton_agree(link: Link) -> None:
    """The conic solver the SPEC names, certified by the same dual construction."""
    data = observational(hawkes_log(200.0, 3))
    structure = Structure((EXP_K,), link)
    newton = checked(fit(structure, data, CHANNELS))
    conic_config = FitConfig(inner_solver="clarabel")
    conic = checked(fit(structure, data, CHANNELS, config=conic_config), conic_config)
    assert newton.certified and conic.certified
    assert conic.solver == "clarabel"
    assert conic.psi == newton.psi
    assert abs(conic.log_likelihood - newton.log_likelihood) <= 1e-7 * abs(
        newton.log_likelihood
    )
    np.testing.assert_allclose(conic.theta, newton.theta, rtol=1e-5, atol=1e-6)


def test_identity_link_keeps_the_intensity_non_negative() -> None:
    """λ = 0.6 + 0.6 sin(2πt/25) touches zero: the unconstrained ML would not
    stay non-negative between events, so node constraints become active."""
    coef = Coefficients(0.6, ((0.6, 0.0),))
    log = draw((Periodic(),), ({"period": 25.0},), coef, Link.IDENTITY, 600.0, 11)
    data = observational(log)
    structure = Structure((Periodic(),), Link.IDENTITY)
    result = checked(fit(structure, data, CHANNELS))
    assert result.certified
    assert result.psi[0][PsiSlot((), "period")] == 25.0
    (d,) = designs_at(result, data)
    lam = d.at_nodes @ np.array(result.theta)
    assert float(np.min(lam)) >= 0.0
    # Active: the minimum over nodes is (numerically) zero.
    assert float(np.min(lam)) <= 1e-6
    conic_config = FitConfig(inner_solver="clarabel")
    conic = checked(fit(structure, data, CHANNELS, config=conic_config), conic_config)
    assert abs(conic.log_likelihood - result.log_likelihood) <= 1e-7 * abs(
        result.log_likelihood
    )


def test_interventional_datasets() -> None:
    """Forced events excite but are not evidence; excluded windows drop out."""
    log = hawkes_log(300.0, 9)
    endogenous = np.ones(log.n, dtype=np.bool_)
    endogenous[::7] = False
    forced = Dataset.create(log, endogenous, ((50.0, 80.0), (200.0, 210.0)), "exp1")
    data = (Dataset.observational(hawkes_log()), forced)
    result = checked(fit(HAWKES, data, CHANNELS))
    assert result.certified
    value, _ = independent_minimum(result, data)
    assert abs(value + result.log_likelihood) <= 1e-7 * abs(result.log_likelihood)
    counted = designs_at(result, data)[1].at_events.shape[0]
    assert result.n_events == hawkes_log().n + counted


# --------------------------------------------------------------------------
# ψ profiling
# --------------------------------------------------------------------------


def test_exhaustive_and_coordinate_search_agree() -> None:
    data = observational(exp_link_log())
    structure = Structure((EXP_K, Periodic()), Link.EXP)
    full = checked(fit(structure, data, CHANNELS))
    assert full.psi_search == "exhaustive"
    assert full.n_psi_points == 6 * 5
    coord = checked(
        fit(structure, data, CHANNELS, config=FitConfig(psi_full_grid_max=1))
    )
    assert coord.psi_search == "coordinate"
    assert coord.n_psi_points < full.n_psi_points
    assert coord.psi == full.psi
    assert coord.theta == full.theta
    assert coord.log_likelihood == full.log_likelihood
    assert full.psi[0][PsiSlot((0,), "exp_rate")] == 2.0
    assert full.psi[1][PsiSlot((), "period")] == 25.0


@pytest.mark.slow
def test_large_grid_is_profiled_coordinate_wise() -> None:
    """6 · 6 · 5 · 4 = 720 > 512 points: coordinate search, deterministic."""
    features: tuple[Feature, ...] = (
        EXP_K,
        Excite(KernelKind.EXP, Mark("size"), ALL),
        Gate(Periodic(), PhaseWindow()),
    )
    assert PSI_FULL_GRID_MAX == 512
    structure = Structure(features, Link.EXP)
    data = observational(exp_link_log(200.0, 4))
    a = checked(fit(structure, data, CHANNELS))
    b = fit(structure, data, CHANNELS)
    assert a.psi_search == "coordinate"
    assert a == b
    assert a.n_params == len(a.theta) + 1 + 1 + 3


def test_null_structure_is_poisson() -> None:
    log = hawkes_log()
    result = checked(fit(Structure((), Link.IDENTITY), observational(log), CHANNELS))
    assert result.certified
    assert result.n_psi_points == 1
    np.testing.assert_allclose(result.theta, [log.n / log.horizon], rtol=1e-9)


# --------------------------------------------------------------------------
# Goodness of fit and the bridge to the simulator
# --------------------------------------------------------------------------


def test_ks_uses_the_likelihood_compensator() -> None:
    data = observational(exp_link_log())
    result = checked(fit(Structure((EXP_K, Periodic()), Link.EXP), data, CHANNELS))
    log = data[0].log
    big = compensator(
        np.array(result.theta),
        result.structure,
        result.psi,
        data[0],
        CHANNELS,
        Link.EXP,
        log.times,
    )
    tau = np.diff(np.concatenate([[0.0], big]))
    ks = stats.kstest(tau, "expon")
    assert result.ks_statistic == pytest.approx(ks.statistic, abs=1e-9)
    assert result.ks_pvalue == pytest.approx(ks.pvalue, rel=1e-6, abs=1e-12)
    assert result.ks_pvalue > 0.01
    assert 0.0 <= result.quadrature_error < 1e-6


def test_ks_detects_a_wrong_structure() -> None:
    log = draw(
        (EXP_K,),
        ({"exp_rate": 0.5},),
        Coefficients(0.2, ((0.8,),)),
        Link.IDENTITY,
        800.0,
        21,
    )
    poisson = checked(fit(Structure((), Link.IDENTITY), observational(log), CHANNELS))
    truth = checked(fit(HAWKES, observational(log), CHANNELS))
    assert poisson.ks_pvalue < 1e-3
    assert truth.ks_pvalue > 0.01


def test_to_coefficients_reproduces_the_fitted_intensity() -> None:
    features: tuple[Feature, ...] = (
        SIZE_K,
        Product(Periodic(), Gate(Trend(), LastMarkAbove("size"))),
    )
    structure = Structure(features, Link.SOFTPLUS)
    log = exp_link_log(200.0, 6)
    result = checked(fit(structure, observational(log), CHANNELS))
    psi, coef = to_coefficients(result)
    assert psi == result.psi
    assert len(coef.per_feature) == 2
    assert len(coef.per_feature[1]) == 2
    t = np.sort(np.random.default_rng(3).uniform(0.0, log.horizon, 200))
    sim = intensity(structure, psi, coef, CHANNELS, log, t)
    eta = evaluate_columns(structure, result.psi, log, CHANNELS, t) @ np.array(
        result.theta
    )
    np.testing.assert_allclose(sim, _softplus(eta), rtol=1e-10, atol=1e-12)


def test_predictive_pvalues_are_deterministic() -> None:
    data = observational(hawkes_log())
    result = checked(fit(HAWKES, data, CHANNELS))

    def count(log: EventLog) -> float:
        return float(log.n)

    def max_gap(log: EventLog) -> float:
        return float(np.max(np.diff(log.times)))

    def simulate_fn(
        structure: Structure,
        psi: PsiAssignment,
        coef: Coefficients,
        dataset: Dataset,
        rng: np.random.Generator,
    ) -> EventLog:
        return simulate(structure, psi, coef, CHANNELS, marks, dataset.log.horizon, rng)

    diagnostics = {"count": count, "max_gap": max_gap}
    a = predictive_pvalues(
        result, data, diagnostics, simulate_fn, 19, np.random.default_rng(0)
    )
    b = predictive_pvalues(
        result, data, diagnostics, simulate_fn, 19, np.random.default_rng(0)
    )
    assert a == b
    assert [c.name for c in a] == ["count", "max_gap"]
    for c in a:
        assert 0.0 < c.p_value <= 1.0
        assert c.n_rep == 19
        assert c.dataset == "observational"
    assert a[0].observed == float(data[0].log.n)


def test_fit_cache_key() -> None:
    data = observational(hawkes_log())
    s1 = Structure((EXP_K, Periodic()), Link.EXP)
    s2 = Structure((Periodic(), EXP_K), Link.EXP)
    key = fit_cache_key(s1, data, CHANNELS)
    assert key == fit_cache_key(s1, data, CHANNELS)
    assert key == fit_cache_key(s2, data, CHANNELS)  # canonical structure
    assert len(key) == 64
    assert key != fit_cache_key(Structure(s1.features, Link.IDENTITY), data, CHANNELS)
    assert key != fit_cache_key(s1, observational(hawkes_log(300.0, 5)), CHANNELS)
    assert key != fit_cache_key(s1, data, CHANNELS, FitConfig(gap_tol_rel=1e-9))
    other = (ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=2.0),)
    assert key != fit_cache_key(s1, data, other)
    # Process parallelism does not change results, so not the key either.
    assert key == fit_cache_key(s1, data, CHANNELS, FitConfig(workers=4))


# --------------------------------------------------------------------------
# Recovery and ordering on simulated truths (slow)
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_recovers_hawkes() -> None:
    log = hawkes_log(5000.0, 13)
    assert 4000 < log.n < 6500
    result = checked(fit(HAWKES, observational(log), CHANNELS))
    assert result.certified
    assert result.psi[0][PsiSlot((0,), "exp_rate")] == 1.0
    mu, eta = result.theta
    assert abs(eta - 0.5) < 0.08
    assert abs(mu - 0.5) < 0.1


@pytest.mark.slow
def test_recovers_size_excitation() -> None:
    coef = Coefficients(0.4, ((0.5,),))
    log = draw((SIZE_K,), ({"exp_rate": 2.0},), coef, Link.IDENTITY, 4000.0, 17)
    data = observational(log)
    result = checked(fit(Structure((SIZE_K,), Link.IDENTITY), data, CHANNELS))
    assert result.certified
    assert result.psi[0][PsiSlot((0,), "exp_rate")] == 2.0
    mu, eta = result.theta
    assert abs(eta - 0.5) < 0.08
    assert abs(mu - 0.4) < 0.1
    # Size excitation beats plain Hawkes on BIC.
    hawkes = checked(fit(HAWKES, data, CHANNELS))
    assert result.bic < hawkes.bic


@pytest.mark.slow
def test_recovers_exp_link_with_periodic() -> None:
    log = exp_link_log(3000.0, 19)
    structure = Structure((EXP_K, Periodic()), Link.EXP)
    result = checked(fit(structure, observational(log), CHANNELS))
    assert result.certified
    assert result.psi[0][PsiSlot((0,), "exp_rate")] == 2.0
    assert result.psi[1][PsiSlot((), "period")] == 25.0
    np.testing.assert_allclose(result.theta, [0.3, -0.6, 0.5, -0.3], atol=0.12)


@pytest.mark.slow
def test_true_structure_wins_on_held_out_data() -> None:
    coef = Coefficients(0.4, ((0.5,),))
    train = draw((SIZE_K,), ({"exp_rate": 2.0},), coef, Link.IDENTITY, 2000.0, 23)
    test = draw((SIZE_K,), ({"exp_rate": 2.0},), coef, Link.IDENTITY, 2000.0, 29)
    held = observational(test)
    scores = {}
    for name, structure in {
        "truth": Structure((SIZE_K,), Link.IDENTITY),
        "hawkes": HAWKES,
        "periodic": Structure((Periodic(),), Link.EXP),
    }.items():
        result = checked(fit(structure, observational(train), CHANNELS))
        assert result.certified
        scores[name] = math.fsum(evaluate_log_likelihood(result, held, CHANNELS))
    assert scores["truth"] > scores["hawkes"] > scores["periodic"]


@pytest.mark.slow
def test_power_kernel_exp_link_parallel_blocks_are_identical() -> None:
    """Process-parallel block computation changes nothing but wall time."""
    log = exp_link_log(500.0, 31)
    structure = Structure((POWER_K, Periodic()), Link.EXP)
    serial = checked(fit(structure, observational(log), CHANNELS))
    parallel = fit(structure, observational(log), CHANNELS, config=FitConfig(workers=3))
    assert serial == parallel
    assert serial.certified
