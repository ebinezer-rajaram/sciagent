"""The agent view: names and units in the anonymised condition are exact images
of the native ones (SPEC §5).

The fitted-number conversions are checked against a brute-force likelihood
computed directly on the rescaled data, so the conversion rules in
``view.py`` are verified rather than restated.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from investigation_support import lab

from sciagent.diagnostics import catalogue
from sciagent.glm.data import Dataset
from sciagent.glm.fit import fit
from sciagent.glm.grammar import (
    ALL,
    Excite,
    Gate,
    KernelKind,
    Link,
    One,
    Periodic,
    PhaseWindow,
    Product,
    PsiSlot,
    Structure,
    Trend,
)
from sciagent.glm.interventions import (
    ClampRate,
    Compose,
    InjectMarks,
    InvalidInterventionError,
)
from sciagent.investigation.view import excite_count

C = 8.0


def _exp_hawkes_loglik(
    times: np.ndarray, horizon: float, mu: float, alpha: float, beta: float
) -> float:
    """Brute-force log L of λ(t) = mu + alpha Σ β e^{-β(t - t_j)}."""
    total = 0.0
    for i, t in enumerate(times):
        lags = t - times[:i]
        total += math.log(mu + alpha * float(np.sum(beta * np.exp(-beta * lags))))
    compensator = mu * horizon + alpha * float(
        np.sum(1.0 - np.exp(-beta * (horizon - times)))
    )
    return total - compensator


def test_excite_count() -> None:
    e = Excite(KernelKind.EXP, One(), ALL)
    assert excite_count(e) == 1
    assert excite_count(Periodic()) == 0
    assert excite_count(Product(e, Gate(e, PhaseWindow()))) == 2
    assert excite_count(Product(Trend(), Periodic())) == 0


def test_identity_null_loglik_converts_exactly() -> None:
    anon = lab("AG-c", "anon")
    data = anon.world.datasets["obs"]
    result = fit(Structure((), Link.IDENTITY), [data], anon.world.channels)
    (theta0,), exact = anon.view.theta(result.structure, result.theta)
    assert exact
    log = data.log
    direct = log.n * math.log(theta0) - theta0 * (C * log.horizon)
    shown = anon.view.log_likelihood(result.log_likelihood, log.n)
    assert shown == pytest.approx(direct, rel=1e-10)


def test_exp_null_loglik_converts_exactly() -> None:
    anon = lab("AG-c", "anon")
    data = anon.world.datasets["obs"]
    result = fit(Structure((), Link.EXP), [data], anon.world.channels)
    (theta0,), _ = anon.view.theta(result.structure, result.theta)
    log = data.log
    direct = log.n * theta0 - math.exp(theta0) * (C * log.horizon)
    shown = anon.view.log_likelihood(result.log_likelihood, log.n)
    assert shown == pytest.approx(direct, rel=1e-10)


def test_excitation_theta_and_psi_convert_exactly() -> None:
    """Fitted (θ, ψ) converted to agent units give the same model on agent data."""
    anon = lab("AG-c", "anon")
    data = anon.world.datasets["obs"]
    hawkes = Structure((Excite(KernelKind.EXP, One(), ALL),), Link.IDENTITY)
    result = fit(hawkes, [data], anon.world.channels)
    beta = result.psi[0][PsiSlot((0,), "exp_rate")]
    mu, alpha = result.theta
    native = _exp_hawkes_loglik(data.log.times, data.log.horizon, mu, alpha, beta)
    assert native == pytest.approx(result.log_likelihood, rel=1e-8)
    (mu_a, alpha_a), exact = anon.view.theta(result.structure, result.theta)
    assert exact
    beta_a = anon.view.psi_value("exp_rate", beta)
    assert alpha_a == alpha
    assert beta_a == beta / C
    agent = anon.view.dataset(data).log
    direct = _exp_hawkes_loglik(agent.times, agent.horizon, mu_a, alpha_a, beta_a)
    shown = anon.view.log_likelihood(result.log_likelihood, data.log.n)
    assert shown == pytest.approx(direct, rel=1e-8)


def test_named_view_is_the_identity() -> None:
    named = lab("AG-c", "named")
    view = named.view
    assert view.factor == 1.0
    assert view.channel("size") == "size"
    assert view.diagnostic("mean_rate") == "mean_rate"
    data = named.world.datasets["obs"]
    assert view.dataset(data) is data
    assert view.log_likelihood(-5.0, 100) == -5.0
    assert view.theta(Structure((Periodic(),), Link.EXP), (1.0, 2.0, 3.0)) == (
        (1.0, 2.0, 3.0),
        True,
    )


def test_anon_names_and_diagnostics_are_covariant() -> None:
    named, anon = lab("AG-c", "named"), lab("AG-c", "anon")
    assert [c.name for c in anon.view.channels] == ["m1", "m2"]
    assert anon.view.channel_native("m1") == "size"
    obs_named = named.view.dataset(named.world.datasets["obs"]).log
    obs_anon = anon.view.dataset(anon.world.datasets["obs"]).log
    for native in ("mean_rate", "fano_factor", "mark_gap_correlation"):
        args = {"channel": "size"} if native == "mark_gap_correlation" else {}
        anon_args = {"channel": "m1"} if args else {}
        v_named = catalogue.compute(native, obs_named, named.view.channels, args)
        v_anon = catalogue.compute(native, obs_anon, anon.view.channels, anon_args)
        assert v_anon == pytest.approx(
            catalogue.transform_value(native, v_named, C), rel=1e-12
        )


def test_experiment_designs_map_back_exactly() -> None:
    view = lab("AG-c", "anon").view
    native = view.experiment_native(
        {
            "intervention": {
                "type": "compose",
                "parts": [
                    {
                        "type": "inject_marks",
                        "window": [8.0, 16.0],
                        "channel": "m1",
                        "value": 3.0,
                    },
                    {"type": "clamp_rate", "window": [16.0, 24.0], "rate": 0.125},
                ],
            },
            "horizon": 800.0,
        }
    )
    assert native.horizon == 100.0
    assert isinstance(native.intervention, Compose)
    parts = set(native.intervention.parts)
    assert InjectMarks((1.0, 2.0), "size", 3.0) in parts
    assert ClampRate((2.0, 3.0), 1.0) in parts
    with pytest.raises(InvalidInterventionError, match="unknown mark channel"):
        view.experiment_native(
            {
                "intervention": {
                    "type": "inject_marks",
                    "window": [0.0, 8.0],
                    "channel": "size",
                    "value": 1.0,
                },
                "horizon": 80.0,
            }
        )


def test_default_horizon_is_in_agent_units() -> None:
    view = lab("AG-c", "anon").view
    native = view.experiment_native({"intervention": {"type": "compose", "parts": []}})
    assert native.horizon == 2000.0


def test_dataset_label_and_mask_survive() -> None:
    anon = lab("AG-c", "anon")
    data: Dataset = anon.world.datasets["obs"]
    shown = anon.view.dataset(data)
    assert shown.label == "obs"
    assert np.array_equal(shown.endogenous, data.endogenous)
    assert np.array_equal(shown.log.times, data.log.times * C)
