"""Instrument tests for posterior/plug-in predictive p-values (SPEC §2.2)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from sciagent.diagnostics.catalogue import (
    DiagnosticArgumentError,
    InsufficientDataError,
)
from sciagent.diagnostics.predictive import predictive_check, predictive_pvalue
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import ChannelKind, ChannelSpec

CHANNELS = (ChannelSpec("b", ChannelKind.POSITIVE, 1.0, 1.0),)


def uniform_log(n: int, horizon: float = 10.0) -> EventLog:
    times = (np.arange(n) + 0.5) * horizon / max(n, 1)
    return EventLog.create(times, {"b": np.ones(n)}, horizon)


type Simulator = Callable[[np.random.Generator], EventLog]


def scripted(counts: list[int]) -> Simulator:
    """Replicates with prescribed event counts; ignores the generator."""
    it = iter(counts)

    def simulate_fn(rng: np.random.Generator) -> EventLog:
        return uniform_log(next(it))

    return simulate_fn


def test_two_sided_pvalue_formula() -> None:
    counts = [1, 2, 3, 4, 6, 7, 8, 9, 10]  # rates 0.1 … 1.0, skipping 0.5

    # Observed rate 0.2: le = 2, ge = 8, R = 9 → p = 2·3/10.
    check = predictive_check(
        "mean_rate",
        {},
        uniform_log(2),
        scripted(counts),
        9,
        np.random.default_rng(0),
        channels=CHANNELS,
    )
    assert check.replicates == tuple(c / 10.0 for c in counts)
    assert check.n_failed == 0
    assert check.p_value == pytest.approx(0.6)
    # Observed rate 0.5 sits in the middle: p capped at 1.
    p = predictive_pvalue(
        "mean_rate",
        {},
        uniform_log(5),
        scripted(counts),
        9,
        np.random.default_rng(0),
        channels=CHANNELS,
    )
    assert p == 1.0


def poisson(rate: float, horizon: float) -> Simulator:
    def simulate_fn(rng: np.random.Generator) -> EventLog:
        n = rng.poisson(rate * horizon)
        times = np.sort(rng.random(n)) * horizon
        return EventLog.create(times, {"b": rng.exponential(1.0, n)}, horizon)

    return simulate_fn


def test_failed_replicates_are_counted_not_scored() -> None:
    fn = scripted([0, 5, 6])
    check = predictive_check(
        "mean_rate",
        {},
        uniform_log(5),
        fn,
        3,
        np.random.default_rng(0),
        channels=CHANNELS,
    )
    assert check.n_failed == 1
    assert len(check.replicates) == 2


def test_all_failed_raises() -> None:
    fn = scripted([0, 0])
    with pytest.raises(InsufficientDataError):
        predictive_check(
            "mean_rate",
            {},
            uniform_log(5),
            fn,
            2,
            np.random.default_rng(0),
            channels=CHANNELS,
        )


def test_defaults_resolve_on_the_observed_log() -> None:
    observed = uniform_log(4, horizon=8.0)  # mean gap 2 → default window 4
    check = predictive_check(
        "fano_factor",
        {},
        observed,
        poisson(1.0, 50.0),
        5,
        np.random.default_rng(1),
        channels=CHANNELS,
    )
    assert check.args == {"window": 4.0}


def test_deterministic_given_the_generator() -> None:
    observed = poisson(1.0, 300.0)(np.random.default_rng(9))
    a = predictive_check(
        "fano_factor",
        {},
        observed,
        poisson(1.0, 300.0),
        19,
        np.random.default_rng(3),
        channels=CHANNELS,
    )
    b = predictive_check(
        "fano_factor",
        {},
        observed,
        poisson(1.0, 300.0),
        19,
        np.random.default_rng(3),
        channels=CHANNELS,
    )
    assert a == b


def test_a_clustered_log_is_rejected_by_poisson_replicates() -> None:
    """Positive control: the p-value can separate the two."""
    rng = np.random.default_rng(4)
    centres = np.sort(rng.random(60)) * 600.0
    times = np.sort(np.concatenate([c + rng.random(10) for c in centres]))
    times = np.unique(times[times < 600.0])
    observed = EventLog.create(times, {"b": np.ones(times.size)}, 600.0)
    rate = times.size / 600.0
    p_clustered = predictive_pvalue(
        "fano_factor",
        {},
        observed,
        poisson(rate, 600.0),
        99,
        np.random.default_rng(5),
        channels=CHANNELS,
    )
    null = poisson(rate, 600.0)(np.random.default_rng(6))
    p_null = predictive_pvalue(
        "fano_factor",
        {},
        null,
        poisson(rate, 600.0),
        99,
        np.random.default_rng(5),
        channels=CHANNELS,
    )
    assert p_clustered <= 0.02
    assert p_null > 0.05


def test_rejects_bad_replicate_counts() -> None:
    with pytest.raises(DiagnosticArgumentError):
        predictive_check(
            "mean_rate",
            {},
            uniform_log(5),
            poisson(1.0, 10.0),
            0,
            np.random.default_rng(0),
            channels=CHANNELS,
        )
