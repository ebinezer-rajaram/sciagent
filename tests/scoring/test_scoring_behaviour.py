"""SPEC §4.4 behaviour measures on hand-built investigation records."""

from __future__ import annotations

import pytest

from sciagent.harness.ledger import Evaluation, Prediction
from sciagent.scoring.behaviour import (
    Belief,
    ExperimentRun,
    StoppingRun,
    calibration,
    falsification_seeking,
    favoured_at,
    revision,
    stopping,
)
from sciagent.scoring.errors import ScoringError

type Record = tuple[Prediction, Evaluation | None]


def _p(
    exp: int,
    hyp: str,
    lo: float,
    hi: float,
    *,
    stat: str = "mean_rate",
    level: float = 0.9,
) -> Prediction:
    return Prediction(exp, stat, (lo, hi), level, hyp)


def _e(index: int, value: float, covered: bool) -> Evaluation:
    return Evaluation(index, value, covered)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def test_calibration_overall_and_per_hypothesis() -> None:
    records: list[Record] = [
        (_p(0, "A", 0, 1), _e(0, 0.5, True)),
        (_p(0, "B", 2, 3), _e(1, 0.5, False)),
        (_p(1, "A", 0, 1, level=0.5), _e(2, 2.0, False)),
        (_p(1, "A", 0, 1, level=0.5), _e(3, 0.2, True)),
        (_p(2, "A", 0, 1), None),
    ]
    c = calibration(records)
    assert c.n == 4 and c.unevaluated == 1 and c.covered == 2
    assert c.coverage == 0.5
    assert c.mean_level == pytest.approx(0.7)
    assert c.error == pytest.approx(-0.2)
    assert [h.hypothesis for h in c.per_hypothesis] == ["A", "B"]
    a = c.per_hypothesis[0]
    assert a.n == 3 and a.coverage == pytest.approx(2 / 3)


def test_calibration_of_nothing_is_undefined() -> None:
    c = calibration([])
    assert c.n == 0 and c.coverage is None and c.error is None


# --------------------------------------------------------------------------
# Falsification-seeking
# --------------------------------------------------------------------------

BELIEFS = (
    Belief(0, "A", "B"),
    Belief(5, "B", "A"),
)
RUNS = (ExperimentRun(0, 1), ExperimentRun(1, 3), ExperimentRun(2, 6))


def test_favoured_at_reads_the_latest_belief() -> None:
    assert favoured_at(BELIEFS, 0) == BELIEFS[0]
    assert favoured_at(BELIEFS, 4) == BELIEFS[0]
    assert favoured_at(BELIEFS, 5) == BELIEFS[1]
    assert favoured_at((Belief(2, "A"),), 1) is None


def test_falsification_seeking_share() -> None:
    records: list[Record] = [
        # exp 0: A and B disjoint on mean_rate -> could refute A.
        (_p(0, "A", 0, 1), None),
        (_p(0, "B", 2, 3), None),
        # exp 1: overlapping intervals only -> confirmation only.
        (_p(1, "A", 0, 1), None),
        (_p(1, "B", 0.5, 2), None),
        # disjoint, but on different statistics: not a refuting pair.
        (_p(1, "A", 0, 1, stat="fano_factor"), None),
        (_p(1, "B", 5, 6, stat="burstiness"), None),
        # exp 2: favoured is B now; no prediction by B -> unpredicted.
        (_p(2, "A", 0, 1), None),
    ]
    f = falsification_seeking(records, RUNS, BELIEFS)
    assert f.experiments == 3
    assert f.refuting == 1
    assert f.confirmation_only == 1
    assert f.unpredicted == 1
    assert f.share == pytest.approx(1 / 3)
    assert [x.can_refute for x in f.per_experiment] == [True, False, False]


def test_touching_intervals_are_not_disjoint() -> None:
    records: list[Record] = [(_p(0, "A", 0, 1), None), (_p(0, "B", 1, 2), None)]
    f = falsification_seeking(records, RUNS[:1], BELIEFS)
    assert f.refuting == 0


def test_falsification_needs_a_runner_up() -> None:
    records: list[Record] = [(_p(0, "A", 0, 1), None), (_p(0, "B", 2, 3), None)]
    f = falsification_seeking(records, RUNS[:1], (Belief(0, "A"),))
    assert f.refuting == 0 and f.confirmation_only == 1


# --------------------------------------------------------------------------
# Revision and zombies
# --------------------------------------------------------------------------


def test_revision_within_k_turns() -> None:
    records: list[Record] = [(_p(0, "A", 0, 1), _e(0, 3.0, False))]
    # Experiment 0 runs at turn 1; the favoured changes at turn 5.
    assert revision(records, RUNS, BELIEFS, k=4).revised == 1
    assert revision(records, RUNS, BELIEFS, k=3).revised == 0
    r = revision(records, RUNS, BELIEFS, k=4)
    assert r.falsified == 1 and r.rate == 1.0


def test_falsified_non_favoured_hypothesis_is_not_counted() -> None:
    records: list[Record] = [(_p(0, "B", 0, 1), _e(0, 3.0, False))]
    r = revision(records, RUNS, BELIEFS, k=10)
    assert r.falsified == 0 and r.rate is None and r.zombie_rate is None


def test_zombie_hypothesis_returns_after_displacement() -> None:
    beliefs = (Belief(0, "A", "B"), Belief(4, "B", "A"), Belief(9, "A", "B"))
    records: list[Record] = [
        (_p(0, "A", 0, 1), _e(0, 3.0, False)),
        (_p(0, "A", 0, 1, stat="burstiness"), _e(1, 3.0, False)),
    ]
    r = revision(records, RUNS, beliefs, k=5)
    # Two falsified statistics on one experiment are one falsification.
    assert r.falsified == 1
    assert r.revised == 1
    assert r.zombies == 1 and r.zombie_rate == 1.0


def test_revision_rejects_bad_k_and_unknown_experiment() -> None:
    with pytest.raises(ScoringError):
        revision([], RUNS, BELIEFS, k=0)
    records: list[Record] = [(_p(9, "A", 0, 1), _e(0, 3.0, False))]
    with pytest.raises(ScoringError):
        revision(records, RUNS, BELIEFS, k=2)


# --------------------------------------------------------------------------
# Stopping
# --------------------------------------------------------------------------


def test_stopping_early_and_burned() -> None:
    runs = (
        # converged after 2 experiments, ran 5: burned 3.
        StoppingRun(5, 8, (-0.2, -0.1, -0.001, -0.001, -0.001, -0.001)),
        # stopped after 1 of 8 with a large gap: early.
        StoppingRun(1, 8, (-0.3, -0.2)),
    )
    s = stopping(runs)
    assert [r.converged_at for r in s.runs] == [2, 1]
    assert [r.burned for r in s.runs] == [3, 0]
    assert [r.early for r in s.runs] == [False, True]
    assert s.early_share == 0.5
    assert s.mean_burned == 1.5
    assert s.mean_used_share == pytest.approx((5 / 8 + 1 / 8) / 2)


def test_stopping_validates_lengths() -> None:
    with pytest.raises(ScoringError):
        StoppingRun(2, 8, (-0.1, -0.1))
    with pytest.raises(ScoringError):
        StoppingRun(9, 8, tuple([-0.1] * 10))
