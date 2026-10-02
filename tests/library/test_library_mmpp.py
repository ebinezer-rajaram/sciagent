"""Instrument tests for the two-state MMPP (regime switching) library member.

The likelihood is checked against closed forms (equal rates is Poisson), an
independent brute-force forward algorithm (``scipy.linalg.expm`` step by step),
and the simulator (the probability of an empty window). The fit is checked for
determinism, nesting and parameter recovery.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.linalg import expm

from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import (
    Censor,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
    InjectMarks,
)
from sciagent.library.base import counted_mask
from sciagent.library.mmpp import (
    MMPP2,
    MMPP2Params,
    mmpp2_log_likelihood,
    simulate_mmpp2,
)

#: v1's regime-switching truth (``mechanisms.REGIME_SWITCHING``) at base rate 1.
V1 = MMPP2Params(
    rate_low=0.2714417616594907,
    rate_high=2.8737714334780025,
    switch_up=0.2924017738212867 * 0.27904761904761904,
    switch_down=0.2924017738212867 * (1 - 0.27904761904761904),
)


def _marks(rng: np.random.Generator) -> dict[str, float]:
    return {"size": -math.log1p(-rng.random())}


def _dataset(seed: int) -> Dataset:
    """A log with forced events and two excluded windows, some events inside."""
    rng = np.random.default_rng(seed)
    times = np.sort(rng.uniform(0.0, 40.0, size=60))
    endogenous = rng.random(60) < 0.8
    log = EventLog.create(times, {"size": rng.exponential(size=60)}, 40.0)
    return Dataset.create(log, endogenous, ((5.0, 9.5), (20.0, 26.0)), "mixed")


def _brute(params: MMPP2Params, data: Dataset) -> float:
    """Forward algorithm one step at a time with scipy's matrix exponential."""
    q = np.array(
        [
            [-params.switch_up, params.switch_up],
            [params.switch_down, -params.switch_down],
        ]
    )
    lam = np.diag([params.rate_low, params.rate_high])
    pi = np.array([params.switch_down, params.switch_up])
    alpha = pi / pi.sum()
    log_scale = 0.0
    counted = data.log.times[counted_mask(data)]
    # Breakpoints: counted events and window edges, in time order.
    points = sorted(
        [(float(t), "event") for t in counted]
        + [(a, "start") for a, _ in data.excluded]
        + [(b, "end") for _, b in data.excluded]
    )
    pos, hidden = 0.0, False
    for t, kind in points:
        gen = q if hidden else q - lam
        alpha = alpha @ expm(gen * (t - pos))
        pos = t
        if kind == "event":
            alpha = alpha @ lam
        else:
            hidden = kind == "start"
        s = alpha.sum()
        log_scale += math.log(s)
        alpha = alpha / s
    alpha = alpha @ expm((q - lam) * (data.log.horizon - pos))
    return log_scale + math.log(alpha.sum())


def test_equal_rates_is_poisson() -> None:
    data = _dataset(1)
    rate = 0.83
    params = MMPP2Params(rate, rate, 0.37, 1.9)
    n = int(counted_mask(data).sum())
    exposure = data.log.horizon - sum(b - a for a, b in data.excluded)
    expected = n * math.log(rate) - rate * exposure
    (got,) = mmpp2_log_likelihood(params, [data])
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-10)


@pytest.mark.parametrize("seed", [2, 3, 4])
def test_matches_brute_force_forward_algorithm(seed: int) -> None:
    data = _dataset(seed)
    params = MMPP2Params(0.3, 4.2, 0.15, 0.6)
    (got,) = mmpp2_log_likelihood(params, [data])
    assert got == pytest.approx(_brute(params, data), rel=1e-10)


def test_matches_brute_force_on_observational_log() -> None:
    log = simulate_mmpp2(V1, 300.0, np.random.default_rng(5))
    data = Dataset.observational(log)
    (got,) = mmpp2_log_likelihood(V1, [data])
    assert got == pytest.approx(_brute(V1, data), rel=1e-10)


def test_forced_events_carry_no_information() -> None:
    data = _dataset(6)
    params = MMPP2Params(0.3, 4.2, 0.15, 0.6)
    keep = data.endogenous
    log = EventLog.create(
        data.log.times[keep],
        {k: v[keep] for k, v in data.log.marks.items()},
        data.log.horizon,
    )
    stripped = Dataset.create(log, np.ones(log.n, bool), data.excluded, "stripped")
    assert mmpp2_log_likelihood(params, [data]) == mmpp2_log_likelihood(
        params, [stripped]
    )


def test_empty_window_probability_matches_simulation() -> None:
    """P(no event on [0, T]) = exp(log L(empty log)), checked by simulation."""
    horizon = 1.5
    empty = Dataset.observational(EventLog.create([], {}, horizon))
    (ll,) = mmpp2_log_likelihood(V1, [empty])
    rng = np.random.default_rng(7)
    n = 4000
    zeros = sum(simulate_mmpp2(V1, horizon, rng).n == 0 for _ in range(n))
    p = math.exp(ll)
    se = math.sqrt(p * (1 - p) / n)
    assert abs(zeros / n - p) < 4 * se


def test_simulated_mean_rate_is_stationary_rate() -> None:
    log = simulate_mmpp2(V1, 20_000.0, np.random.default_rng(8))
    stationary = (V1.rate_low * V1.switch_down + V1.rate_high * V1.switch_up) / (
        V1.switch_up + V1.switch_down
    )
    assert log.n / log.horizon == pytest.approx(stationary, rel=0.05)


def _fit_data(seed: int, horizon: float) -> list[Dataset]:
    return [
        Dataset.observational(
            simulate_mmpp2(V1, horizon, np.random.default_rng(seed), marks=_marks)
        )
    ]


def test_fit_is_deterministic() -> None:
    data = _fit_data(9, 600.0)
    a = MMPP2().fit(data)
    b = MMPP2().fit(data)
    assert a.params == b.params
    assert a.log_likelihood == b.log_likelihood
    assert a.log_likelihood == math.fsum(mmpp2_log_likelihood(a.params, data))


@pytest.mark.slow
def test_fit_nests_poisson() -> None:
    rng = np.random.default_rng(10)
    times = np.sort(rng.uniform(0.0, 500.0, size=480))
    data = [Dataset.observational(EventLog.create(times, {}, 500.0))]
    fitted = MMPP2().fit(data)
    poisson = 480 * math.log(480 / 500.0) - 480.0
    assert fitted.log_likelihood >= poisson - 1e-3
    assert fitted.n_params == 4
    assert fitted.bic == pytest.approx(4 * math.log(480) - 2 * fitted.log_likelihood)


@pytest.mark.slow
def test_recovers_v1_regime_switching() -> None:
    fitted = MMPP2().fit(_fit_data(11, 20_000.0))
    p = fitted.params
    assert p.rate_low == pytest.approx(V1.rate_low, rel=0.15)
    assert p.rate_high == pytest.approx(V1.rate_high, rel=0.1)
    assert p.switch_up == pytest.approx(V1.switch_up, rel=0.3)
    assert p.switch_down == pytest.approx(V1.switch_down, rel=0.3)


def test_held_out_log_likelihood_is_evaluation_at_fitted_params() -> None:
    fitted = MMPP2().fit(_fit_data(12, 400.0))
    fresh = _fit_data(13, 400.0)
    assert fitted.held_out_log_likelihood(fresh) == math.fsum(
        mmpp2_log_likelihood(fitted.params, fresh)
    )


def test_simulate_experiment_semantics() -> None:
    fitted = MMPP2().fit(_fit_data(14, 400.0))
    experiment = Experiment(
        Compose(
            (
                ForceEvents((10.0, 50.0), {"size": (3.0, 4.0)}),
                Censor((100.0, 150.0)),
                ClampRate((200.0, 220.0), 5.0),
                InjectMarks((300.0, 350.0), "size", 7.0),
            )
        ),
        horizon=400.0,
    )
    a = fitted.simulate_experiment(experiment, np.random.default_rng(15))
    b = fitted.simulate_experiment(experiment, np.random.default_rng(15))
    t = a.log.times
    assert np.array_equal(t, b.log.times)
    assert a.excluded == ((100.0, 150.0), (200.0, 220.0))
    forced = np.isin(t, [10.0, 50.0])
    assert forced.sum() == 2
    assert not a.endogenous[forced].any()
    assert a.log.marks["size"][forced].tolist() == [3.0, 4.0]
    assert not ((t >= 100.0) & (t < 150.0)).any()
    in_clamp = (t >= 200.0) & (t < 220.0)
    assert in_clamp.sum() > 40  # rate 5 over 20 time units
    assert not a.endogenous[in_clamp].any()
    injected = (t >= 300.0) & (t < 350.0)
    assert np.all(a.log.marks["size"][injected] == 7.0)
    assert a.endogenous[~forced & ~in_clamp].all()
