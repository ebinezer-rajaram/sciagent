"""SPEC §4.3 score 4: the efficiency curve from a system's trajectory.

Each distinct best-so-far model is evaluated on the held-out data exactly
once, and every point's gap is that model's per-event log-likelihood minus
the ORACLE's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pytest
from scoring_support import SMALL_LIBRARY, investigation_data, truth

from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import Experiment
from sciagent.scoring.efficiency import efficiency_curve, gap_at
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.heldout import held_out_dataset
from sciagent.systems.v2.models import FittedModel
from sciagent.systems.v2.systems import BLib, SystemResult, TrajectoryPoint


@dataclass
class Counting:
    """A stub model with a fixed per-event log-likelihood that counts calls."""

    name: str
    value: float
    calls: list[str] = field(default_factory=list)
    n_params: int = 1

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]:
        raise AssertionError("not used")

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float:
        raise AssertionError("not used")

    def held_out_per_event(self, datasets: Sequence[Dataset]) -> float:
        self.calls.append(self.name)
        return self.value

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset:
        raise AssertionError("not used")


def _held() -> list[Dataset]:
    log = EventLog.create([0.5, 1.0], {}, 2.0)
    return [Dataset.observational(log)]


def _result(points: list[tuple[int, Counting]]) -> SystemResult:
    traj = tuple(TrajectoryPoint(k, m.name, 0.0, m.name, 0.0, m) for k, m in points)
    model: FittedModel = points[-1][1]
    return SystemResult("S", points[-1][1].name, None, model, points[-1][0], traj)


def test_each_distinct_model_is_evaluated_once() -> None:
    a, b = Counting("a", -1.3), Counting("b", -1.1)
    result = _result([(1, a), (2, a), (3, b), (4, b), (5, b)])
    curve = efficiency_curve(result, -1.0, _held())
    assert [p.fits_used for p in curve] == [1, 2, 3, 4, 5]
    assert [p.best for p in curve] == ["a", "a", "b", "b", "b"]
    assert [p.gap for p in curve] == pytest.approx([-0.3, -0.3, -0.1, -0.1, -0.1])
    assert a.calls == ["a"] and b.calls == ["b"]


def test_gap_at_reads_the_best_so_far_within_a_budget() -> None:
    a, b = Counting("a", -1.3), Counting("b", -1.1)
    curve = efficiency_curve(_result([(1, a), (3, b)]), -1.0, _held())
    assert gap_at(curve, 0) is None
    assert gap_at(curve, 1) == pytest.approx(-0.3)
    assert gap_at(curve, 2) == pytest.approx(-0.3)
    assert gap_at(curve, 40) == pytest.approx(-0.1)


def test_empty_trajectory_gives_empty_curve() -> None:
    m = Counting("np", -1.0)
    result = SystemResult("B-np", "np", None, m, 0, ())
    assert efficiency_curve(result, -1.0, _held()) == ()


def test_unordered_trajectory_is_refused() -> None:
    a = Counting("a", -1.3)
    with pytest.raises(ScoringError):
        efficiency_curve(_result([(2, a), (1, a)]), -1.0, _held())


def test_blib_curve_ends_at_its_submission() -> None:
    data = investigation_data("hawkes", 1, 400.0)
    result = BLib(SMALL_LIBRARY).run(data)
    held = [held_out_dataset(truth("hawkes"), "hawkes", 1)]
    oracle = -1.0
    curve = efficiency_curve(result, oracle, held)
    assert len(curve) == len(result.trajectory)
    assert curve[-1].gap == result.model.held_out_per_event(held) - oracle
