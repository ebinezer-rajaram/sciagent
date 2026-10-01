"""Instrument test 10: predictions are evaluated by the framework only, and an
interval stated after its experiment ran is refused (SPEC §4.4, §6.3).
"""

from __future__ import annotations

import math

import pytest
from harness_toy import toy_layer
from hypothesis import given
from hypothesis import strategies as st

from sciagent.harness.errors import PredictionRefusedError
from sciagent.harness.ledger import Prediction, PredictionLedger


def _p(experiment: int, low: float = 0.0, high: float = 10.0) -> Prediction:
    return Prediction(
        experiment=experiment,
        statistic="mean_count",
        interval=(low, high),
        level=0.9,
        hypothesis="h1",
    )


def test_a_prediction_for_a_sealed_experiment_is_refused() -> None:
    ledger = PredictionLedger()
    ledger.commit(_p(0))
    ledger.seal(0)
    with pytest.raises(PredictionRefusedError, match="already"):
        ledger.commit(_p(0))
    ledger.commit(_p(1))
    assert [c.prediction.experiment for c in ledger.committed] == [0, 1]


def test_seal_is_strictly_sequential() -> None:
    ledger = PredictionLedger()
    with pytest.raises(PredictionRefusedError):
        ledger.seal(1)
    ledger.seal(0)
    with pytest.raises(PredictionRefusedError):
        ledger.seal(0)
    assert ledger.next_experiment == 1


def test_evaluation_uses_the_framework_callable_and_only_after_seal() -> None:
    ledger = PredictionLedger()
    ledger.commit(_p(0, 1.0, 2.0))
    ledger.commit(_p(0, 5.0, 6.0))
    with pytest.raises(PredictionRefusedError):
        ledger.evaluate(0, lambda p: 1.5)
    ledger.seal(0)
    evaluations = ledger.evaluate(0, lambda p: 1.5)
    assert [e.value for e in evaluations] == [1.5, 1.5]
    assert [e.covered for e in evaluations] == [True, False]
    with pytest.raises(PredictionRefusedError):
        ledger.evaluate(0, lambda p: 1.5)
    assert ledger.evaluations == evaluations


def test_a_prediction_carries_no_evaluated_value() -> None:
    """The agent-facing type has nowhere to put the number the framework reads."""
    fields = set(Prediction.__dataclass_fields__)
    assert fields == {"experiment", "statistic", "interval", "level", "hypothesis"}


@pytest.mark.parametrize(
    ("interval", "level"),
    [((2.0, 1.0), 0.9), ((0.0, math.inf), 0.9), ((0.0, 1.0), 1.0), ((0.0, 1.0), 0.0)],
)
def test_malformed_predictions_are_refused(
    interval: tuple[float, float], level: float
) -> None:
    with pytest.raises(PredictionRefusedError):
        Prediction(
            experiment=0,
            statistic="s",
            interval=interval,
            level=level,
            hypothesis="h",
        )


def test_through_the_tool_layer_late_predictions_reach_the_agent_as_errors() -> None:
    layer = toy_layer(seed=3)
    args = {"experiment": 0, "low": 0.0, "high": 9.0, "level": 0.9, "hypothesis": "h"}
    assert not layer.call("predict", args).is_error
    layer.call("run_experiment", {"rate": 3.0})
    late = layer.call("predict", args)
    assert late.is_error and "already" in late.text
    ok = layer.call("predict", {**args, "experiment": 1})
    assert not ok.is_error


@given(
    st.lists(
        st.tuples(st.sampled_from(["commit", "seal"]), st.integers(0, 4)), max_size=30
    )
)
def test_no_commit_ever_targets_an_experiment_sealed_before_it(
    ops: list[tuple[str, int]],
) -> None:
    ledger = PredictionLedger()
    for op, k in ops:
        try:
            if op == "commit":
                ledger.commit(_p(k))
            else:
                ledger.seal(k)
        except PredictionRefusedError:
            continue
    for entry in ledger.committed:
        assert entry.prediction.experiment >= entry.sealed_before
