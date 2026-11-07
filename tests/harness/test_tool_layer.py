"""Instrument test 7 (budget half): budgets are enforced by the tool layer.

The handler of a metered tool must never run once its budget is spent, the agent
must be told so in words, and the layer -- not the handler, not the model -- is
what keeps the count.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

import pytest
from harness_toy import toy_layer

from sciagent.harness.errors import ToolLayerError
from sciagent.harness.tools import (
    BudgetMeter,
    FrameworkClock,
    ToolLayer,
    ToolOutput,
    ToolSpec,
)

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
        "clock",
        "close",
        "finished",
        "idle",
        "meter",
        "names",
        "schemas",
        "submission",
    }


def test_a_footer_is_appended_to_every_output_after_charging() -> None:
    """P4 Δ12: the layer can append a line built from the budgets after the call.

    It goes on every output -- success, handler error, refusal by the layer --
    and the logged output is the one returned, so replay digests what the
    agent read.
    """
    seen: list[dict[str, int]] = []

    def footer(meters: Mapping[str, BudgetMeter]) -> str:
        seen.append({name: m.remaining for name, m in meters.items()})
        return f"left: {meters['E'].remaining}"

    handler = Counting()
    failing = Counting(error=True)
    layer = ToolLayer(
        [
            ToolSpec("probe", "p", EMPTY, handler, budget="E"),
            ToolSpec("bad", "b", EMPTY, failing, budget="E"),
        ],
        {"E": 1},
        footer=footer,
    )
    outputs = [
        layer.call("bad", {}),
        layer.call("probe", {}),
        layer.call("probe", {}),
        layer.call("nonexistent", {}),
    ]
    assert outputs[0].text == "call 1\nleft: 1"
    assert outputs[1].text == "call 1\nleft: 0"
    assert outputs[2].text.endswith("\nleft: 0")
    assert "budget exhausted" in outputs[2].text
    assert outputs[3].text.endswith("\nleft: 0")
    assert [o.is_error for o in outputs] == [True, False, True, True]
    assert [c.output for c in layer.calls] == outputs
    assert seen == [{"E": 1}, {"E": 0}, {"E": 0}, {"E": 0}]


def test_without_a_footer_outputs_are_unchanged() -> None:
    layer = ToolLayer([ToolSpec("probe", "p", EMPTY, Counting(), budget="E")], {"E": 1})
    assert layer.call("probe", {}).text == "call 1"


class Holding:
    """A handler that stays inside its call until the test lets it go."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, args: Mapping[str, Any]) -> ToolOutput:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(30.0)
        return ToolOutput(f"call {self.calls}")


def _closing_layer(
    handler: Holding, commits: list[int] | None = None
) -> tuple[ToolLayer, threading.Thread]:
    """A layer with ``handler`` running in a thread, the way a session runs it."""
    layer = ToolLayer(
        [ToolSpec("probe", "p", EMPTY, handler, budget="E")],
        {"E": 3},
        on_commit=None if commits is None else lambda c: commits.append(c.index),
    )
    thread = threading.Thread(target=layer.call, args=("probe", {}))
    thread.start()
    assert handler.entered.wait(30.0)
    return layer, thread


def test_close_does_not_wait_for_a_running_handler() -> None:
    handler = Holding()
    layer, thread = _closing_layer(handler)
    layer.close()  # the handler is still inside: this returning is the test
    assert thread.is_alive()
    handler.release.set()
    thread.join(30.0)


def test_a_handler_that_finishes_after_close_commits_nothing() -> None:
    handler = Holding()
    commits: list[int] = []
    layer, thread = _closing_layer(handler, commits)
    layer.close()
    handler.release.set()
    thread.join(30.0)
    assert not thread.is_alive()
    assert layer.calls == ()
    assert layer.meter("E").used == 0
    assert commits == []


def test_a_closed_layer_runs_no_handler_and_logs_nothing() -> None:
    handler = Counting()
    layer = ToolLayer([ToolSpec("probe", "p", EMPTY, handler, budget="E")], {"E": 2})
    assert not layer.call("probe", {}).is_error
    layer.close()
    out = layer.call("probe", {})
    assert out.is_error and "ended" in out.text
    assert handler.calls == 1
    assert len(layer.calls) == 1
    assert layer.meter("E").used == 1


def test_a_terminal_call_after_close_does_not_finish_the_run() -> None:
    layer = toy_layer(1)
    layer.close()
    assert layer.call("submit", {"answer": "a", "report": "r"}).is_error
    assert not layer.finished and layer.submission is None


def test_close_is_idempotent() -> None:
    layer = toy_layer(1)
    layer.close()
    layer.close()
    assert layer.calls == ()


def test_every_committed_call_is_announced_in_order_refusals_too() -> None:
    commits: list[tuple[int, str, int]] = []
    handler = Counting()
    layer = ToolLayer(
        [ToolSpec("probe", "p", EMPTY, handler, budget="E")],
        {"E": 1},
        on_commit=lambda c: commits.append(
            (c.index, c.name, int(c.budgets_after["E"]))
        ),
    )
    layer.call("probe", {})
    layer.call("probe", {})  # refused: budget spent
    layer.call("nothing", {})  # refused: unknown tool
    assert commits == [(0, "probe", 1), (1, "probe", 1), (2, "nothing", 1)]


def test_idle_says_whether_a_handler_is_still_running() -> None:
    handler = Holding()
    layer, thread = _closing_layer(handler)
    layer.close()
    assert not layer.idle(0.0)
    handler.release.set()
    thread.join(30.0)
    assert layer.idle(0.0)


# --------------------------------------------------------------------------
# Framework time (P4b D5)
# --------------------------------------------------------------------------


class Ticking:
    """A fake monotonic clock that advances only when told to."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_each_call_carries_the_framework_time_measured_during_it() -> None:
    ticks = Ticking()
    clock = FrameworkClock(ticks)

    def decide(_args: Mapping[str, Any]) -> ToolOutput:
        ticks.now += 1.0  # the handler's own time: not framework time
        with clock.measure():
            ticks.now += 7.5
        return ToolOutput("chosen")

    layer = ToolLayer(
        [
            ToolSpec("decide", "d", EMPTY, decide, budget="E"),
            ToolSpec("plain", "p", EMPTY, Counting(), budget="E"),
        ],
        {"E": 5},
        clock=clock,
    )
    plain = layer.call("plain", {})
    decided = layer.call("decide", {})
    assert [c.framework_s for c in layer.calls] == [0.0, 7.5]
    assert clock.total_s() == 7.5
    # a measurement, not an output: the digest is the output's alone
    assert decided.digest == ToolOutput("chosen").digest
    assert plain.digest == ToolOutput("call 1").digest


def test_a_layer_without_a_clock_has_one_that_measures_nothing() -> None:
    layer = ToolLayer([ToolSpec("probe", "p", EMPTY, Counting())], {})
    layer.call("probe", {})
    assert layer.clock.total_s() == 0.0
    assert layer.calls[0].framework_s == 0.0


def test_framework_time_is_paused_time_while_it_runs() -> None:
    ticks = Ticking()
    clock = FrameworkClock(ticks)
    ended: list[float] = []
    clock.listen(lambda: ended.append(ticks.now))
    with clock.measure():
        ticks.now += 2.0
        assert clock.measuring
        assert clock.paused_s() == 2.0
        assert clock.total_s() == 0.0
    assert not clock.measuring
    assert clock.total_s() == clock.paused_s() == 2.0
    assert ended == [102.0]
    clock.listen(None)
    with clock.measure():
        ticks.now += 1.0
    assert ended == [102.0]
    assert clock.total_s() == 3.0


def test_a_clock_publishes_each_start_and_end() -> None:
    ticks = Ticking()
    published: list[tuple[float, float | None]] = []
    clock = FrameworkClock(ticks, publish=lambda t, s: published.append((t, s)))
    with clock.measure():
        ticks.now += 2.0
    ticks.now += 5.0
    with clock.measure():
        ticks.now += 1.0
    assert published == [(0.0, 100.0), (2.0, None), (2.0, 107.0), (3.0, None)]


def test_framework_time_counts_when_the_measured_work_raises() -> None:
    ticks = Ticking()
    clock = FrameworkClock(ticks)
    with pytest.raises(ValueError, match="fault"), clock.measure():
        ticks.now += 4.0
        raise ValueError("fault")
    assert clock.total_s() == 4.0 and not clock.measuring


def test_framework_time_measurements_do_not_nest() -> None:
    clock = FrameworkClock()
    with (
        clock.measure(),
        pytest.raises(ToolLayerError, match="nest"),
        clock.measure(),
    ):
        pass
