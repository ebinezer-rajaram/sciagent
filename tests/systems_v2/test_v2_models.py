"""The common fitted-model interface (``sciagent.systems.v2.models``).

The GLM and library wrappers must be pure delegation (checked for equality
with the underlying instruments). The Wiener-Hopf model's dataset likelihood
and its simulator are new instruments, so they are checked against
``wiener_hopf.log_likelihood``, a fine Riemann sum and the time-rescaling
theorem.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from environments.pointproc import v2
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import evaluate_log_likelihood, fit, to_coefficients
from sciagent.glm.interventions import (
    Censor,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
    InjectMarks,
    run_experiment,
)
from sciagent.library.base import EmpiricalMarks, counted_mask
from sciagent.library.mmpp import MMPP2
from sciagent.nonparam import wiener_hopf
from sciagent.systems.v2.models import (
    GLMModel,
    LibraryFittedModel,
    WienerHopfModel,
    wiener_hopf_log_likelihood,
)

CH = v2.CHANNELS


def _hawkes_log(seed: int, horizon: float = 1500.0) -> EventLog:
    return v2.TRUTHS["hawkes"].simulate(horizon, np.random.default_rng(seed))


@pytest.fixture(scope="module")
def wh() -> WienerHopfModel:
    return WienerHopfModel.estimate(_hawkes_log(1), CH)


def test_glm_model_delegates() -> None:
    train = [Dataset.observational(_hawkes_log(2, 500.0))]
    test = [Dataset.observational(_hawkes_log(3, 500.0))]
    result = fit(v2.HAWKES, train, CH)
    model = GLMModel("hawkes", result, CH, v2.mark_sampler)
    expected = evaluate_log_likelihood(result, test, CH)
    assert model.log_likelihoods(test) == expected
    assert model.held_out_log_likelihood(test) == math.fsum(expected)
    n = int(counted_mask(test[0]).sum())
    assert model.held_out_per_event(test) == math.fsum(expected) / n
    assert model.n_params == result.n_params
    exp = Experiment(ForceEvents((5.0, 6.0)), horizon=100.0)
    psi, coef = to_coefficients(result)
    direct = run_experiment(
        result.structure, psi, coef, CH, v2.mark_sampler, exp, np.random.default_rng(4)
    )
    got = model.simulate_experiment(exp, np.random.default_rng(4))
    assert np.array_equal(got.log.times, direct.log.times)
    assert np.array_equal(got.endogenous, direct.endogenous)


def test_library_model_delegates() -> None:
    train = [Dataset.observational(_hawkes_log(5, 300.0))]
    fitted = MMPP2().fit(train)
    model = LibraryFittedModel(fitted, CH, v2.mark_sampler)
    test = [Dataset.observational(_hawkes_log(6, 300.0))]
    assert model.held_out_log_likelihood(test) == fitted.held_out_log_likelihood(test)
    assert model.name == "regime_switching"
    exp = Experiment(Censor((10.0, 20.0)), horizon=100.0)
    a = model.simulate_experiment(exp, np.random.default_rng(7))
    b = fitted.simulate_experiment(exp, np.random.default_rng(7), marks=v2.mark_sampler)
    assert np.array_equal(a.log.times, b.log.times)


def test_wiener_hopf_dataset_likelihood_matches_log_likelihood(
    wh: WienerHopfModel,
) -> None:
    log = _hawkes_log(8, 800.0)
    expected = wiener_hopf.log_likelihood(wh.estimate_, log, CH)
    got = wiener_hopf_log_likelihood(wh.estimate_, Dataset.observational(log), CH)
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-9)


def test_wiener_hopf_dataset_likelihood_with_windows_and_forced(
    wh: WienerHopfModel,
) -> None:
    """Counted events only; excluded windows removed from the compensator."""
    log = _hawkes_log(9, 300.0)
    rng = np.random.default_rng(10)
    endo = rng.random(log.n) < 0.85
    windows = ((40.0, 70.0), (150.0, 151.5))
    data = Dataset.create(log, endo, windows, "x")
    got = wiener_hopf_log_likelihood(wh.estimate_, data, CH)
    keep = counted_mask(data)
    events = math.fsum(
        np.log(wiener_hopf.intensity(wh.estimate_, log, CH, log.times[keep])).tolist()
    )
    step = 1e-3
    grid = np.arange(0.5 * step, log.horizon, step)
    lam = wiener_hopf.intensity(wh.estimate_, log, CH, grid)
    observed = np.ones(grid.size, dtype=bool)
    for a, b in windows:
        observed &= ~((grid >= a) & (grid <= b))
    riemann = math.fsum((lam[observed] * step).tolist())
    assert got == pytest.approx(events - riemann, abs=0.05)


def test_wiener_hopf_simulation_passes_time_rescaling(wh: WienerHopfModel) -> None:
    """Rescaled gaps of a simulated log are Exp(1) under the model's own λ."""
    exp = Experiment(Compose(()), horizon=1500.0)
    data = wh.simulate_experiment(exp, np.random.default_rng(11))
    log = data.log
    assert log.n > 500
    est = wh.estimate_
    times = log.times
    breaks = np.unique(
        np.concatenate([[0.0], (times[:, None] + est.edges[None, :]).ravel(), times])
    )
    breaks = breaks[breaks <= log.horizon]
    mids = 0.5 * (breaks[:-1] + breaks[1:])
    lam = wiener_hopf.intensity(est, log, CH, mids)
    cum = np.concatenate([[0.0], np.cumsum(lam * np.diff(breaks))])
    big = np.interp(times, breaks, cum)
    tau = np.diff(np.concatenate([[0.0], big]))
    assert stats.kstest(tau, "expon").pvalue > 0.01
    assert log.n / log.horizon == pytest.approx(est.mean_rate, rel=0.15)


def test_wiener_hopf_simulation_semantics(wh: WienerHopfModel) -> None:
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
    a = wh.simulate_experiment(experiment, np.random.default_rng(12))
    b = wh.simulate_experiment(experiment, np.random.default_rng(12))
    t = a.log.times
    assert np.array_equal(t, b.log.times)
    assert a.excluded == ((100.0, 150.0), (200.0, 220.0))
    forced = np.isin(t, [10.0, 50.0])
    assert forced.sum() == 2
    assert not a.endogenous[forced].any()
    assert a.log.marks["size"][forced].tolist() == [3.0, 4.0]
    assert not ((t >= 100.0) & (t < 150.0)).any()
    in_clamp = (t >= 200.0) & (t < 220.0)
    assert in_clamp.sum() > 40
    assert not a.endogenous[in_clamp].any()
    injected = (t >= 300.0) & (t < 350.0)
    assert np.all(a.log.marks["size"][injected] == 7.0)


def test_wiener_hopf_marks_resample_training_marks(wh: WienerHopfModel) -> None:
    assert isinstance(wh.marks, EmpiricalMarks)
    data = wh.simulate_experiment(
        Experiment(Compose(()), horizon=200.0), np.random.default_rng(13)
    )
    train = set(wh.marks.rows[:, wh.marks.channels.index("size")].tolist())
    assert set(data.log.marks["size"].tolist()) <= train


def test_wiener_hopf_forced_events_excite(wh: WienerHopfModel) -> None:
    """Under the Hawkes estimate, a forced burst raises the following count."""
    burst = tuple(100.0 + 0.01 * i for i in range(50))
    rng = np.random.default_rng(14)
    after, base = 0, 0
    for _ in range(20):
        hit = wh.simulate_experiment(Experiment(ForceEvents(burst), horizon=110.0), rng)
        calm = wh.simulate_experiment(Experiment(Compose(()), horizon=110.0), rng)
        after += int(((hit.log.times > 100.5) & hit.endogenous).sum())
        base += int((calm.log.times > 100.5).sum())
    assert after > 2 * base
