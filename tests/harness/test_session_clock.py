"""The session clock excludes framework decision time (P4b D5; SPEC §6.3).

A policy decision inside ``run_experiment`` (fits, simulations, diagnostics of
the entertained set) is the framework's compute, not the agent's. It runs
inside a tool handler, so under a plain ``asyncio.timeout(wall_time_s)`` it
would count against the agent's wall limit. Here a stand-in policy burns a
known time inside :meth:`FrameworkClock.measure`, with a wall limit shorter
than that time: the session must not end on ``wall_time``, the agent's own time
must still be bounded, and the record must carry each call's framework time in
its unhashed timing section, so the content address and replay do not move.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest
from harness_toy import toy_layer

from sciagent.harness.live import MODELS, AgentConfig, run_investigation
from sciagent.harness.replay import replay
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.harness.tools import FrameworkClock, ToolLayer, ToolOutput, ToolSpec

EMPTY: dict[str, Any] = {"type": "object", "properties": {}}
SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _config(wall_time_s: float) -> AgentConfig:
    return AgentConfig(
        model=MODELS["haiku"],
        system_prompt="s",
        prompt="p",
        seed=3,
        max_turns=20,
        wall_time_s=wall_time_s,
    )


def _layer(decision_s: float, agent_s: float = 0.0) -> ToolLayer:
    """``decide`` burns ``decision_s`` inside the clock (a stand-in policy) and
    then ``agent_s`` outside it; ``submit`` ends the run."""
    clock = FrameworkClock()

    def decide(_args: Mapping[str, Any]) -> ToolOutput:
        with clock.measure():
            time.sleep(decision_s)
        time.sleep(agent_s)
        return ToolOutput("chosen", record={"cell": 0})

    def submit(args: Mapping[str, Any]) -> ToolOutput:
        return ToolOutput(f"submitted {args['answer']}")

    specs = [
        ToolSpec("decide", "d", EMPTY, decide, budget="E"),
        ToolSpec("submit", "s", SUBMIT_SCHEMA, submit, budget="S", terminal=True),
    ]
    return ToolLayer(specs, {"E": 3, "S": 1}, clock=clock)


SCRIPT = (
    Step(calls=(("decide", {}),)),
    Step(calls=(("submit", {"answer": "a"}),)),
    Step(text="done"),
)


def test_a_decision_longer_than_the_wall_limit_does_not_end_the_session() -> None:
    result = run_investigation(_config(0.3), _layer(0.8), driver=ScriptedDriver(SCRIPT))
    assert result.outcome == "submitted"
    timing = result.record.timing
    framework = timing["framework_s"]
    assert len(framework) == 2
    assert framework[0] >= 0.8
    assert framework[1] == 0.0
    assert timing["wall_s"] >= 0.8
    assert timing["agent_s"] == pytest.approx(timing["wall_s"] - framework[0], abs=1e-5)
    assert timing["agent_s"] < 0.3


def test_the_agents_own_time_is_still_bounded_after_a_decision() -> None:
    """A decision of 0.5 s, then 0.2 s of the handler's own (agent-side) time
    and a turn that thinks for 5 s: the limit of 0.4 s of agent time ends it."""
    script = (
        Step(calls=(("decide", {}),)),
        Step(delay_s=5.0, calls=(("submit", {"answer": "a"}),)),
    )
    result = run_investigation(
        _config(0.4), _layer(0.5, agent_s=0.2), driver=ScriptedDriver(script)
    )
    assert result.outcome == "wall_time"
    timing = result.record.timing
    assert timing["framework_s"][0] >= 0.5
    assert timing["agent_s"] >= 0.4
    assert timing["agent_s"] < 2.0
    assert timing["wall_s"] >= 0.9


def test_a_decision_that_starts_late_runs_past_the_old_deadline() -> None:
    """0.25 s of the 0.4 s limit used by thinking, then a 0.6 s decision: the
    plain deadline would fall inside the decision; the paused one does not."""
    script = (Step(delay_s=0.25, calls=(("decide", {}),)), *SCRIPT[1:])
    result = run_investigation(_config(0.4), _layer(0.6), driver=ScriptedDriver(script))
    assert result.outcome == "submitted"
    assert result.record.timing["framework_s"][0] >= 0.6
    assert result.record.timing["agent_s"] < 0.4


def test_framework_time_moves_no_hashed_byte_and_replays() -> None:
    """Same calls, different decision times: the same head, and the record
    with framework time replays through a fresh layer (instrument test 9)."""
    slow = run_investigation(_config(5.0), _layer(0.3), driver=ScriptedDriver(SCRIPT))
    fast = run_investigation(_config(5.0), _layer(0.0), driver=ScriptedDriver(SCRIPT))
    assert slow.record.head == fast.record.head
    report = replay(slow.record, lambda _config: _layer(0.0))
    assert report.head == slow.record.head
    assert report.submission == {"answer": "a"}


def test_a_session_without_framework_decisions_records_zeros() -> None:
    """AG-c and AG-o without a policy: the clock is the plain wall clock, and
    every call records a framework time of zero."""
    script = (
        Step(calls=(("run_experiment", {"rate": 3.0}),)),
        Step(calls=(("fit", {"model": "a"}),)),
        Step(calls=(("submit", {"answer": "a", "report": "r"}),)),
    )
    result = run_investigation(
        _config(30.0), toy_layer(3), driver=ScriptedDriver(script)
    )
    assert result.outcome == "submitted"
    timing = result.record.timing
    assert timing["framework_s"] == [0.0, 0.0, 0.0]
    assert timing["agent_s"] == timing["wall_s"]
