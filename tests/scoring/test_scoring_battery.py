"""SPEC §4.3 score 3: interventional similarity on the held-out battery.

Instrument tests (SPEC §6.3): the battery is fixed and valid; the bounded
discrepancy is a squared Hellinger distance with the stated limits; the
truth scored against itself is ≈ 1; S11's size excitation scored against
Hawkes is clearly < 1, with a mark-injection experiment driving the gap; the
whole computation is deterministic.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from scoring_support import investigation_data, truth

from environments.pointproc import v2
from sciagent.glm.interventions import (
    ClampRate,
    InjectMarks,
    atoms,
    canonical_json,
    validate_experiment,
)
from sciagent.glm.simulate import ExplosionError
from sciagent.scoring.battery import (
    BATTERY_HORIZON,
    INTERVENTION_WINDOW,
    hellinger2,
    interventional_similarity,
    simulate_capped,
    standard_battery,
)
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.truth import TruthModel
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.systems import Oracle

# --------------------------------------------------------------------------
# The battery
# --------------------------------------------------------------------------


def test_battery_is_fixed_valid_and_eight_long_for_pointproc() -> None:
    battery = standard_battery(v2.CHANNELS)
    assert len(battery) == 8
    names = [b.name for b in battery]
    assert len(set(names)) == len(names)
    for b in battery:
        validate_experiment(b.experiment, v2.CHANNELS)
        assert b.experiment.horizon == BATTERY_HORIZON
        assert b.readouts
    again = standard_battery(v2.CHANNELS)
    assert [canonical_json(b.experiment.intervention) for b in battery] == [
        canonical_json(b.experiment.intervention) for b in again
    ]


def test_battery_contains_each_primitive_kind() -> None:
    kinds = {
        type(a).__name__
        for b in standard_battery(v2.CHANNELS)
        for a in atoms(b.experiment.intervention)
    }
    assert kinds == {"ForceEvents", "InjectMarks", "Censor", "ClampRate"}


def test_battery_times_scale_with_the_horizon() -> None:
    small = standard_battery(v2.CHANNELS, horizon=40.0)
    large = standard_battery(v2.CHANNELS, horizon=160.0)
    for a, b in zip(small, large, strict=True):
        assert a.name == b.name
        for x, y in zip(
            atoms(a.experiment.intervention),
            atoms(b.experiment.intervention),
            strict=True,
        ):
            if isinstance(x, InjectMarks | ClampRate):
                assert isinstance(y, InjectMarks | ClampRate)
                assert y.window == (4.0 * x.window[0], 4.0 * x.window[1])
    lo, hi = INTERVENTION_WINDOW
    assert 0.0 < lo < hi < 1.0


def test_injection_levels_respect_channel_kinds() -> None:
    for b in standard_battery(v2.CHANNELS):
        for a in atoms(b.experiment.intervention):
            if isinstance(a, InjectMarks):
                if a.channel == "sign":
                    assert a.value in (-1.0, 1.0)
                else:
                    assert a.value > 0.0


# --------------------------------------------------------------------------
# The discrepancy
# --------------------------------------------------------------------------


def test_hellinger_limits() -> None:
    xs: list[float | None] = [1.0, 2.0, 3.0, 4.0]
    assert hellinger2(xs, xs) == 0.0
    assert hellinger2([0.0, 0.0], [5.0, 5.0]) == pytest.approx(1.0)
    assert hellinger2([None, None], [None, None]) == 0.0
    assert hellinger2([None, None], [1.0, 2.0]) == pytest.approx(1.0)
    assert hellinger2([1.0, 1.0], [1.0, 1.0]) == 0.0
    with pytest.raises(ScoringError):
        hellinger2([], [1.0])
    with pytest.raises(ScoringError):
        hellinger2([math.inf], [1.0])


def test_hellinger_mixture_with_undefined_atom() -> None:
    # Half undefined on one side, none on the other, identical defined parts:
    # H² = 1 - (0 + sqrt(1/2)·1).
    a: list[float | None] = [1.0, 2.0, None, None, 1.0, 2.0, None, None]
    b: list[float | None] = [1.0, 2.0, 1.0, 2.0]
    assert hellinger2(a, b) == pytest.approx(1.0 - math.sqrt(0.5))


values = st.lists(
    st.one_of(st.none(), st.floats(-1e3, 1e3, allow_nan=False)),
    min_size=1,
    max_size=12,
)


@given(values, values)
def test_hellinger_is_bounded_and_symmetric(
    a: list[float | None], b: list[float | None]
) -> None:
    d = hellinger2(a, b)
    assert 0.0 <= d <= 1.0
    assert d == hellinger2(b, a)


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_truth_against_itself_is_near_one_and_deterministic() -> None:
    t = TruthModel(truth("size_excitation"))
    a = interventional_similarity(t, t, v2.CHANNELS, "size_excitation", 1)
    b = interventional_similarity(t, t, v2.CHANNELS, "size_excitation", 1)
    assert a == b
    assert a.similarity >= 0.95, a
    assert 0.0 <= a.similarity <= 1.0


@pytest.mark.slow
def test_size_excitation_against_hawkes_is_clearly_below_one() -> None:
    truth_model = TruthModel(truth("size_excitation"))
    hawkes = TruthModel(truth("hawkes"))
    result = interventional_similarity(
        truth_model, hawkes, v2.CHANNELS, "size_excitation", 1
    )
    # Measured 0.852 (seed 1), against 0.974 for the truth against itself.
    assert result.similarity < 0.9, result
    worst = max(result.experiments, key=lambda e: e.discrepancy)
    assert worst.name.startswith("inject_size"), result
    assert worst.discrepancy > 0.5, result


def test_capped_run_of_a_grammar_fit_matches_its_own_simulation() -> None:
    data = investigation_data("size_excitation", 4, 300.0)
    model = Oracle(v2.SIZE_EXCITED).run(data).model
    assert isinstance(model, GLMModel)
    (battery_experiment, *_) = standard_battery(v2.CHANNELS)
    exp = battery_experiment.experiment
    a = simulate_capped(model, exp, np.random.default_rng(9), "x")
    b = model.simulate_experiment(exp, np.random.default_rng(9), label="x")
    assert a.log.times.tobytes() == b.log.times.tobytes()
    assert a.endogenous.tobytes() == b.endogenous.tobytes()


def test_capped_run_raises_past_the_cap() -> None:
    t = TruthModel(truth("hawkes"))
    exp = standard_battery(v2.CHANNELS)[0].experiment
    with pytest.raises(ExplosionError):
        simulate_capped(t, exp, np.random.default_rng(0), "x", max_events=5)


def test_similarity_rejects_too_few_replicates() -> None:
    t = TruthModel(truth("hawkes"))
    with pytest.raises(ScoringError):
        interventional_similarity(t, t, v2.CHANNELS, "hawkes", 1, replicates=1)
