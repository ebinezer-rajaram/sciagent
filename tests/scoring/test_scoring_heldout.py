"""SPEC §4.3 scores 1 and 2: the held-out predictive gap and structural recovery.

Instrument tests (SPEC §6.3): the held-out stream is deterministic and
disjoint from the investigation's; the ORACLE's gap is exactly 0; the gap
closed is 0 for B-lib and 1 for ORACLE; structural recovery is exact for the
ORACLE, canonical (order-free), and maximal for a non-answer.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scoring_support import SMALL_LIBRARY, investigation_data, truth

from environments.pointproc import v2
from sciagent.glm.distance import structure_distance
from sciagent.glm.grammar import ALL, Excite, KernelKind, Link, One, Periodic, Structure
from sciagent.investigation.world import World
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.heldout import (
    HELDOUT_HORIZON,
    gap_closed,
    held_out_dataset,
    heldout_seed,
    predictive_gap,
)
from sciagent.scoring.structure import structural_recovery
from sciagent.systems.v2.systems import BLib, Oracle

# --------------------------------------------------------------------------
# Held-out data
# --------------------------------------------------------------------------


def _uniforms(seq: np.random.SeedSequence, n: int = 8) -> list[float]:
    return [float(x) for x in np.random.default_rng(seq).random(n)]


def test_heldout_seed_is_deterministic_and_keyed() -> None:
    a = _uniforms(heldout_seed("size_excitation", 7))
    assert a == _uniforms(heldout_seed("size_excitation", 7))
    assert a != _uniforms(heldout_seed("size_excitation", 8))
    assert a != _uniforms(heldout_seed("hawkes", 7))


@pytest.mark.parametrize("seed", [0, 1, 7, 2**40])
def test_heldout_stream_is_disjoint_from_the_world_streams(seed: int) -> None:
    held = _uniforms(heldout_seed("size_excitation", seed))
    for stream in (0, 1):
        for index in range(16):
            world = _uniforms(np.random.SeedSequence([seed, stream, index]))
            assert held != world


def test_heldout_seed_rejects_bad_seeds() -> None:
    with pytest.raises(ScoringError):
        heldout_seed("x", -1)
    with pytest.raises(ScoringError):
        heldout_seed("", 1)


def test_held_out_dataset_is_deterministic_and_observational() -> None:
    t = truth("hawkes")
    a = held_out_dataset(t, "hawkes", 3)
    b = held_out_dataset(t, "hawkes", 3)
    assert a.log.times.tobytes() == b.log.times.tobytes()
    for name in a.log.marks:
        assert a.log.marks[name].tobytes() == b.log.marks[name].tobytes()
    assert a.log.horizon == HELDOUT_HORIZON
    assert a.excluded == ()
    assert bool(a.endogenous.all())
    assert 1500 < a.log.n < 2500


def test_held_out_differs_from_the_investigations_observational_log() -> None:
    t = truth("hawkes")
    held = held_out_dataset(t, "hawkes", 5)
    obs = World(t, 5).datasets["obs"]
    assert held.log.times.tobytes() != obs.log.times.tobytes()


# --------------------------------------------------------------------------
# Predictive gap
# --------------------------------------------------------------------------


def test_oracle_gap_is_exactly_zero() -> None:
    data = investigation_data("size_excitation", 4, 400.0)
    oracle = Oracle(v2.SIZE_EXCITED).run(data)
    held = [held_out_dataset(truth("size_excitation"), "size_excitation", 4)]
    score = predictive_gap(oracle.model, oracle.model, held)
    assert score.gap == 0.0
    assert score.per_event == score.oracle_per_event
    assert math.isfinite(score.per_event)


def test_gap_is_submitted_minus_oracle_per_event() -> None:
    data = investigation_data("hawkes", 2, 400.0)
    oracle = Oracle(v2.HAWKES).run(data)
    blib = BLib(SMALL_LIBRARY).run(data)
    held = [held_out_dataset(truth("hawkes"), "hawkes", 2)]
    score = predictive_gap(blib.model, oracle.model, held)
    assert score.per_event == blib.model.held_out_per_event(held)
    assert score.oracle_per_event == oracle.model.held_out_per_event(held)
    assert score.gap == score.per_event - score.oracle_per_event


def test_gap_closed_endpoints_and_undefined_case() -> None:
    assert gap_closed(-1.2, -1.2, -1.0) == 0.0
    assert gap_closed(-1.0, -1.2, -1.0) == 1.0
    assert gap_closed(-1.1, -1.2, -1.0) == pytest.approx(0.5)
    # ORACLE does not beat B-lib: the truth is not identifiable (SPEC §6.4).
    assert gap_closed(-1.1, -1.0, -1.0) is None
    assert gap_closed(-1.1, -0.9, -1.0) is None
    with pytest.raises(ScoringError):
        gap_closed(math.nan, -1.0, -0.9)


def test_null_loses_to_oracle_on_hawkes_held_out() -> None:
    from sciagent.systems.v2.systems import GrammarMember

    data = investigation_data("hawkes", 12, 2000.0)
    oracle = Oracle(v2.HAWKES).run(data)
    null = BLib((GrammarMember("null", v2.NULL),)).run(data)
    held = [held_out_dataset(truth("hawkes"), "hawkes", 12)]
    score = predictive_gap(null.model, oracle.model, held)
    assert score.gap < -0.05, score


# --------------------------------------------------------------------------
# Structural recovery
# --------------------------------------------------------------------------


def test_oracle_structure_is_exact_recovery() -> None:
    s = structural_recovery(v2.SIZE_EXCITED, v2.SIZE_EXCITED)
    assert s.exact and s.distance == 0.0 and s.submitted


def test_recovery_is_canonical() -> None:
    a = Structure((Excite(KernelKind.EXP, One(), ALL), Periodic()), Link.EXP)
    b = Structure((Periodic(), Excite(KernelKind.EXP, One(), ALL)), Link.EXP)
    s = structural_recovery(b, a)
    assert s.exact and s.distance == 0.0


def test_none_submission_is_maximal_distance() -> None:
    s = structural_recovery(None, v2.SIZE_EXCITED)
    assert not s.exact and s.distance == 1.0 and not s.submitted


def test_wrong_structure_distance_matches_the_metric() -> None:
    s = structural_recovery(v2.HAWKES, v2.SIZE_EXCITED)
    assert not s.exact
    assert s.distance == structure_distance(v2.HAWKES, v2.SIZE_EXCITED)
    assert 0.0 < s.distance < 1.0
