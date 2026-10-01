"""Tests for the pointproc v2 environment definition (SPEC §3, §2.1)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.special import i0

from environments.pointproc import v2
from sciagent.diagnostics.catalogue import compute
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    Excite,
    KernelKind,
    Link,
    Mark,
    One,
    Periodic,
    Structure,
    validate,
)


def test_channels() -> None:
    assert [c.name for c in v2.CHANNELS] == ["sign", "size"]
    sign, size = v2.CHANNELS
    assert sign.kind is ChannelKind.SIGN
    assert (sign.location, sign.scale) == (0.0, 1.0)
    assert size.kind is ChannelKind.POSITIVE
    assert (size.location, size.scale) == (1.0, 1.0)


def test_mark_sampler_is_seeded_and_ordered() -> None:
    a = [v2.mark_sampler(np.random.default_rng(3)) for _ in range(1)]
    b = [v2.mark_sampler(np.random.default_rng(3)) for _ in range(1)]
    assert a == b
    rng = np.random.default_rng(0)
    u_size, u_sign = rng.random(), rng.random()
    got = v2.mark_sampler(np.random.default_rng(0))
    assert got["size"] == -math.log1p(-u_size)
    assert got["sign"] == (1.0 if u_sign < 0.5 else -1.0)


def test_mark_sampler_distribution() -> None:
    rng = np.random.default_rng(1)
    draws = [v2.mark_sampler(rng) for _ in range(20_000)]
    sizes = np.array([d["size"] for d in draws])
    signs = np.array([d["sign"] for d in draws])
    assert set(np.unique(signs).tolist()) == {-1.0, 1.0}
    assert abs(signs.mean()) < 0.03
    assert sizes.min() > 0.0
    # Standardised by the ChannelSpec, size has mean 0 and sd 1.
    z = (sizes - v2.SIZE.location) / v2.SIZE.scale
    assert abs(z.mean()) < 0.03
    assert abs(z.std() - 1.0) < 0.03


def test_library_structures_are_valid() -> None:
    assert v2.NULL.features == ()
    assert Structure((Excite(KernelKind.EXP, One(), ALL),), Link.IDENTITY) == v2.HAWKES
    assert Structure((Periodic(),), Link.EXP) == v2.SEASONALITY
    for member in v2.LIBRARY:
        validate(member.structure, v2.CHANNELS)
    assert [m.name for m in v2.LIBRARY] == ["null", "hawkes", "seasonality"]
    assert [m.name for m in v2.OUT_OF_GRAMMAR_LIBRARY] == [
        "regime_switching",
        "poisson_mixture",
    ]


def test_truths_are_valid_stationary_and_calibrated_in_theory() -> None:
    assert list(v2.TRUTHS) == ["null", "hawkes", "seasonality", "size_excitation"]
    for truth in v2.TRUTHS.values():
        validate(truth.structure, v2.CHANNELS)
    assert (
        Structure((Excite(KernelKind.EXP, Mark("size"), ALL),), Link.IDENTITY)
        == v2.SIZE_EXCITED
    )
    for name in ("hawkes", "size_excitation"):
        truth = v2.TRUTHS[name]
        eta = truth.coef.per_feature[0][0]
        assert 0.0 < eta < 1.0  # E[size] = 1, so η is the branching ratio
        assert truth.coef.intercept / (1.0 - eta) == pytest.approx(1.0)
    seasonal = v2.TRUTHS["seasonality"]
    base = math.exp(seasonal.coef.intercept)
    amplitude = seasonal.coef.per_feature[0][1]
    assert seasonal.coef.per_feature[0][0] == 0.0
    assert base * float(i0(amplitude)) == pytest.approx(1.0, rel=1e-12)
    assert v2.TRUTHS["null"].coef.intercept == 1.0


def test_simulation_is_deterministic() -> None:
    truth = v2.TRUTHS["hawkes"]
    a = truth.simulate(200.0, np.random.default_rng(5))
    b = truth.simulate(200.0, np.random.default_rng(5))
    assert np.array_equal(a.times, b.times)
    assert all(np.array_equal(a.marks[k], b.marks[k]) for k in a.marks)
    assert sorted(a.marks) == ["sign", "size"]


@pytest.mark.slow
@pytest.mark.parametrize("name", ["null", "hawkes", "seasonality", "size_excitation"])
def test_mean_rate_is_one_by_simulation(name: str) -> None:
    truth = v2.TRUTHS[name]
    horizon = 5_000.0
    counts = [
        truth.simulate(horizon, np.random.default_rng(100 + seed)).n
        for seed in range(4)
    ]
    rate = sum(counts) / (4 * horizon)
    # Hawkes count variance ≈ T/(1-η)² ≈ 11 T: sd of this rate ≈ 0.024.
    assert rate == pytest.approx(1.0, abs=0.08)


@pytest.mark.slow
def test_size_excitation_is_visible_to_the_cross_mark_diagnostics() -> None:
    """Positive control linking the truths to the catalogue (v1's S11 lesson)."""
    rng = np.random.default_rng(11)
    s11 = v2.TRUTHS["size_excitation"].simulate(4_000.0, rng)
    hawkes = v2.TRUTHS["hawkes"].simulate(4_000.0, rng)
    args = {"channel": "size"}
    s11_corr = compute("mark_gap_correlation", s11, v2.CHANNELS, args)
    hawkes_corr = compute("mark_gap_correlation", hawkes, v2.CHANNELS, args)
    assert s11_corr < -0.05
    assert abs(hawkes_corr) < 0.05
    assert compute("next_gap_after_large_mark", s11, v2.CHANNELS, args) < 0.9
