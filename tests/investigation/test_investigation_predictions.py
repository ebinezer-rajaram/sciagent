"""Instrument test 10: predictions are evaluated by the framework only, and a
prediction stated after its experiment ran is refused (SPEC §6.3, §4.4).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from investigation_support import lab, plain_run

from sciagent.diagnostics import catalogue
from sciagent.harness.ledger import Prediction
from sciagent.investigation.view import Condition


def _predict(index: int, low: float, high: float, **extra: Any) -> dict[str, Any]:
    return {
        "experiment": index,
        "diagnostic": "mean_rate",
        "hypothesis": "Excite(ExpK, One, all)",
        "interval": [low, high],
        "level": 0.9,
        **extra,
    }


def test_the_agent_cannot_supply_an_observed_value() -> None:
    """Neither the ledger's record nor the tool's schema has a field for one."""
    fields = set(Prediction.__dataclass_fields__)
    assert fields == {"experiment", "statistic", "interval", "level", "hypothesis"}
    layer = lab("AG-c").layer()
    schema = next(s for s in layer.schemas() if s["name"] == "predict")
    props = set(schema["input_schema"]["properties"])
    assert props == {
        "experiment",
        "diagnostic",
        "args",
        "hypothesis",
        "interval",
        "level",
    }
    assert schema["input_schema"]["additionalProperties"] is False
    smuggled = layer.call("predict", _predict(0, 0.0, 2.0, value=1.0))
    assert smuggled.is_error
    assert "unknown argument" in smuggled.text


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_framework_evaluates_on_the_experiments_data(condition: Condition) -> None:
    the_lab = lab("AG-c", condition)
    layer = the_lab.layer()
    view = the_lab.view
    rate = view.diagnostic("mean_rate")
    wide = layer.call("predict", {**_predict(0, 0.0, 100.0), "diagnostic": rate})
    narrow = layer.call("predict", {**_predict(0, 50.0, 60.0), "diagnostic": rate})
    assert not wide.is_error and not narrow.is_error
    out = layer.call("run_experiment", plain_run(view.time(300.0)))
    assert not out.is_error, out.text
    evaluated = out.record["predictions"] if out.record else []
    assert [p["covered"] for p in evaluated] == [True, False]
    # The value is the catalogue's, computed by the framework on the agent-view
    # dataset the agent will also hold.
    agent_log = view.dataset(the_lab.world.datasets["e0"]).log
    expected = catalogue.compute("mean_rate", agent_log, view.channels, {})
    assert [p["value"] for p in evaluated] == [expected, expected]
    assert "prediction 0" in out.text and "inside" in out.text
    assert "prediction 1" in out.text and "OUTSIDE" in out.text
    outcomes = the_lab.prediction_outcomes
    assert [o.covered for o in outcomes] == [True, False]
    assert json.loads(outcomes[0].statistic)["diagnostic"] == "mean_rate"


def test_a_prediction_after_its_experiment_ran_is_refused() -> None:
    layer = lab("AG-c").layer()
    assert not layer.call("run_experiment", plain_run(200.0)).is_error
    late = layer.call("predict", _predict(0, 0.0, 2.0))
    assert late.is_error
    assert "refused" in late.text and "already been run" in late.text
    # The next index is still open.
    assert not layer.call("predict", _predict(1, 0.0, 2.0)).is_error


def test_an_index_beyond_the_budget_is_refused() -> None:
    layer = lab("AG-c", experiments=2).layer()
    out = layer.call("predict", _predict(2, 0.0, 2.0))
    assert out.is_error
    assert "can never run" in out.text


def test_a_bad_prediction_is_refused_with_a_reason() -> None:
    layer = lab("AG-c").layer()
    assert layer.call("predict", _predict(0, 2.0, 1.0)).is_error
    assert layer.call("predict", {**_predict(0, 0, 1), "level": 1.5}).is_error
    assert layer.call("predict", {**_predict(0, 0, 1), "hypothesis": "Nope"}).is_error
    assert layer.call("predict", {**_predict(0, 0, 1), "diagnostic": "d99"}).is_error
    bad_args = {**_predict(0, 0, 1), "args": {"window": 1.0}}
    assert layer.call("predict", bad_args).is_error


def test_unevaluable_prediction_is_recorded_not_raised() -> None:
    """A statistic undefined on the experiment's data is recorded as such."""
    the_lab = lab("AG-c")
    layer = the_lab.layer()
    huge_window = {
        **_predict(0, 0.0, 10.0),
        "diagnostic": "fano_factor",
        "args": {"window": 150.0},
    }
    assert not layer.call("predict", huge_window).is_error
    out = layer.call("run_experiment", plain_run(200.0))
    assert not out.is_error, out.text
    (outcome,) = the_lab.prediction_outcomes
    assert outcome.value is None
    assert not outcome.covered
    assert outcome.error is not None
    assert "could not be evaluated" in out.text
