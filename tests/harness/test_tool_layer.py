"""Instrument test 7 (budget half): budgets are enforced by the tool layer.

The handler of a metered tool must never run once its budget is spent, the agent
must be told so in words, and the layer -- not the handler, not the model -- is
what keeps the count.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from harness_toy import toy_layer

from sciagent.harness.errors import ToolLayerError
from sciagent.harness.tools import BudgetMeter, ToolLayer, ToolOutput, ToolSpec

EMPTY: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


class Counting:
    """A handler that counts how often it was really invoked."""

    def __init__(self, *, error: bool = False) -> None:
        self.calls = 0
        self.error = error

    def __call__(self, args: Mapping[str, Any]) -> ToolOutput:
        self.calls += 1
        return ToolOutput(f"call {self.calls}", is_error=self.error)


def test_handler_is_never_called_once_the_budget_is_spent() -> None:
    handler = Counting()
    layer = ToolLayer([ToolSpec("probe", "p", EMPTY, handler, budget="E")], {"E": 2})
    outputs = [layer.call("probe", {}) for _ in range(4)]
    assert handler.calls == 2
    assert [o.is_error for o in outputs] == [False, False, True, True]
    assert "budget" in outputs[2].text and "'E'" in outputs[2].text
    assert layer.meter("E") == BudgetMeter("E", limit=2, used=2)


def test_errors_returned_by_the_handler_do_not_consume_budget() -> None:
    handler = Counting(error=True)
    layer = ToolLayer([ToolSpec("probe", "p", EMPTY, handler, budget="E")], {"E": 1})
    for _ in range(3):
        assert layer.call("probe", {}).is_error
    assert handler.calls == 3
    assert layer.meter("E").used == 0


def test_every_call_is_logged_including_refusals() -> None:
    layer = toy_layer(seed=1, experiments=1)
    layer.call("run_experiment", {"rate": 2.0})
    layer.call("run_experiment", {"rate": 2.0})
    layer.call("nonexistent", {})
    log = layer.calls
    assert [c.index for c in log] == [0, 1, 2]
    assert [c.name for c in log] == ["run_experiment", "run_experiment", "nonexistent"]
    assert [c.charged for c in log] == ["E", None, None]
    assert [c.output.is_error for c in log] == [False, True, True]
    assert log[0].budgets_after == {"E": 1, "F": 0, "submit": 0}


def test_argument_shape_is_checked_before_the_handler_runs() -> None:
    handler = Counting()
    schema = {
        "type": "object",
        "properties": {"x": {"type": "number"}},
        "required": ["x"],
        "additionalProperties": False,
    }
    layer = ToolLayer([ToolSpec("probe", "p", schema, handler, budget="E")], {"E": 5})
    assert layer.call("probe", {}).is_error
    assert layer.call("probe", {"x": 1, "y": 2}).is_error
    assert handler.calls == 0
    assert not layer.call("probe", {"x": 1}).is_error


def test_submit_ends_the_run_and_nothing_runs_after_it() -> None:
    layer = toy_layer(seed=1)
    assert not layer.finished
    out = layer.call("submit", {"answer": "a", "report": "r"})
    assert not out.is_error
    assert layer.finished
    assert layer.submission is not None
    assert layer.submission.args == {"answer": "a", "report": "r"}
    after = layer.call("add", {"a": 1, "b": 2})
    assert after.is_error and "ended" in after.text


def test_misconfigured_layers_are_refused() -> None:
    spec = ToolSpec("probe", "p", EMPTY, Counting(), budget="missing")
    with pytest.raises(ToolLayerError):
        ToolLayer([spec], {"E": 1})
    dup = ToolSpec("probe", "p", EMPTY, Counting(), budget=None)
    with pytest.raises(ToolLayerError):
        ToolLayer([dup, dup], {})
    with pytest.raises(ToolLayerError):
        ToolLayer([ToolSpec("bad name!", "p", EMPTY, Counting(), None)], {})
    with pytest.raises(ToolLayerError):
        ToolLayer([dup], {"E": -1})


def test_schemas_are_what_the_record_and_the_model_see() -> None:
    layer = toy_layer(seed=1)
    schemas = layer.schemas()
    assert [s["name"] for s in schemas] == [
        "run_experiment",
        "fit",
        "predict",
        "add",
        "submit",
    ]
    assert schemas[0]["budget"] == "E"
    assert schemas[-1]["terminal"] is True


def test_handlers_reach_state_only_through_the_layer() -> None:
    """The layer exposes no handler or environment object to its caller."""
    layer = toy_layer(seed=1)
    public = {name for name in dir(layer) if not name.startswith("_")}
    assert public == {
        "budgets",
        "call",
        "calls",
        "finished",
        "meter",
        "names",
        "schemas",
        "submission",
    }
