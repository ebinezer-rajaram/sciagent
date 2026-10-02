"""Instrument tests for the two-component Poisson mixture (renewal) library member.

The likelihood is checked against closed forms (equal rates is Poisson), an
explicit gap-by-gap computation of the documented excluded-window rule, and the
simulator (the mean gap). The fit is checked for determinism and recovery.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import (
    Censor,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
)
from sciagent.library.mixture import (
    MixtureParams,
    PoissonMixture2,
    mixture_log_likelihood,
    simulate_mixture,
)

#: v1's Poisson-mixture truth (``mechanisms.POISSON_MIXTURE``).
V1 = MixtureParams(
    rate_low=0.47227444482419484,
    rate_high=32.247333855188096,
    weight_high=0.5380952380952381,
)


def _density(p: MixtureParams, g: float) -> float:
    return p.weight_high * p.rate_high * math.exp(-p.rate_high * g) + (
        1 - p.weight_high
    ) * p.rate_low * math.exp(-p.rate_low * g)


def _survival(p: MixtureParams, g: float) -> float:
    return p.weight_high * math.exp(-p.rate_high * g) + (1 - p.weight_high) * math.exp(
        -p.rate_low * g
    )


def test_equal_rates_is_poisson() -> None:
    rng = np.random.default_rng(1)
    times = np.sort(rng.uniform(0.0, 50.0, size=40))
    data = Dataset.observational(EventLog.create(times, {}, 50.0))
    rate = 0.7
    (got,) = mixture_log_likelihood(MixtureParams(rate, rate, 0.3), [data])
    assert got == pytest.approx(40 * math.log(rate) - rate * 50.0, rel=1e-12)


def test_observational_is_renewal_from_zero_with_censored_tail() -> None:
    p = MixtureParams(0.5, 6.0, 0.4)
    times = [0.3, 0.5, 2.0, 2.1, 7.0]
    data = Dataset.observational(EventLog.create(times, {}, 9.0))
    gaps = np.diff([0.0, *times])
    expected = math.fsum(math.log(_density(p, g)) for g in gaps) + math.log(
        _survival(p, 9.0 - 7.0)
    )
    (got,) = mixture_log_likelihood(p, [data])
    assert got == pytest.approx(expected, rel=1e-12)


def test_excluded_windows_and_forced_events_follow_the_documented_rule() -> None:
    """Forced events are ignored; a gap crossing a window is dropped; the first
    counted event after a window is conditioned on; the gap before a window
    contributes its survival up to the window's start."""
    p = MixtureParams(0.5, 6.0, 0.4)
    times = [1.0, 1.5, 2.5, 3.5, 5.0, 6.0, 6.2, 9.0, 12.0]
    endo = [True, False, True, True, True, True, True, True, True]
    # 3.5 lies in [3.0, 4.0]; window 2 is [8.0, 10.0] and holds 9.0.
    log = EventLog.create(times, {}, 14.0)
    data = Dataset.create(log, endo, ((3.0, 4.0), (8.0, 10.0)), "x")
    f, s = _density, _survival
    expected = math.fsum(
        [
            math.log(f(p, 1.0)),  # 0 -> 1.0
            math.log(f(p, 1.5)),  # 1.0 -> 2.5 (1.5 forced, ignored)
            math.log(s(p, 0.5)),  # 2.5 -> window start 3.0
            # 5.0 is the first event after window 1: conditioned on
            math.log(f(p, 1.0)),  # 5.0 -> 6.0
            math.log(f(p, 0.2)),  # 6.0 -> 6.2
            math.log(s(p, 1.8)),  # 6.2 -> window start 8.0
            # 12.0 first after window 2: conditioned on
            math.log(s(p, 2.0)),  # 12.0 -> horizon 14.0
        ]
    )
    (got,) = mixture_log_likelihood(p, [data])
    assert got == pytest.approx(expected, rel=1e-12)


def test_segment_after_window_without_events_contributes_nothing() -> None:
    p = MixtureParams(0.5, 6.0, 0.4)
    log = EventLog.create([1.0], {}, 10.0)
    data = Dataset.create(log, [True], ((2.0, 4.0),), "x")
    (got,) = mixture_log_likelihood(p, [data])
    expected = math.log(_density(p, 1.0)) + math.log(_survival(p, 1.0))
    assert got == pytest.approx(expected, rel=1e-12)


def test_simulated_mean_gap() -> None:
    log = simulate_mixture(V1, 20_000.0, np.random.default_rng(2))
    mean_gap = V1.weight_high / V1.rate_high + (1 - V1.weight_high) / V1.rate_low
    assert log.horizon / log.n == pytest.approx(mean_gap, rel=0.04)


def test_fit_is_deterministic_and_counts_parameters() -> None:
    data = [
        Dataset.observational(simulate_mixture(V1, 800.0, np.random.default_rng(3)))
    ]
    a = PoissonMixture2().fit(data)
    b = PoissonMixture2().fit(data)
    assert a.params == b.params
    assert a.log_likelihood == b.log_likelihood
    assert a.n_params == 3
    assert a.bic == pytest.approx(3 * math.log(data[0].log.n) - 2 * a.log_likelihood)


def test_recovers_v1_mixture() -> None:
    data = [
        Dataset.observational(simulate_mixture(V1, 10_000.0, np.random.default_rng(4)))
    ]
    p = PoissonMixture2().fit(data).params
    assert p.rate_low == pytest.approx(V1.rate_low, rel=0.1)
    assert p.rate_high == pytest.approx(V1.rate_high, rel=0.1)
    assert p.weight_high == pytest.approx(V1.weight_high, abs=0.03)


def test_simulate_experiment_semantics() -> None:
    data = [
        Dataset.observational(
            simulate_mixture(
                V1,
                300.0,
                np.random.default_rng(5),
                marks=lambda r: {"size": -math.log1p(-r.random())},
            )
        )
    ]
    fitted = PoissonMixture2().fit(data)
    experiment = Experiment(
        Compose(
            (
                ForceEvents((10.0,)),
                Censor((50.0, 80.0)),
                ClampRate((100.0, 110.0), 0.0),
            )
        ),
        horizon=300.0,
    )
    out = fitted.simulate_experiment(experiment, np.random.default_rng(6))
    t = out.log.times
    assert out.excluded == ((50.0, 80.0), (100.0, 110.0))
    assert not ((t >= 50.0) & (t < 80.0)).any()
    assert not ((t >= 100.0) & (t < 110.0)).any()
    assert 10.0 in t.tolist()
    assert not out.endogenous[t == 10.0].any()
    assert set(out.log.marks) == {"size"}
