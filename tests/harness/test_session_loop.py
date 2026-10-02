"""The live runner's message loop, driven by a scripted model.

The scripted driver yields the SDK's own message types and calls the very
``SdkMcpTool`` handlers the in-process MCP server would call, so everything
between the CLI and the tool layer -- conversion, matching, recording, budget
enforcement, outcome classification -- is the code a live run executes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import Mapping
from typing import Any

import pytest
from harness_toy import toy_layer

from sciagent.core.errors import SystemConfigurationError
from sciagent.harness.errors import HarnessFaultError, ServedModelError
from sciagent.harness.live import (
    MODELS,
    AgentConfig,
    arun_investigation,
    build_options,
    run_investigation,
)
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.harness.tools import ToolLayer, ToolOutput, ToolSpec

HAIKU = MODELS["haiku"]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _config(**overrides: Any) -> AgentConfig:
    base: dict[str, Any] = {
        "model": HAIKU,
        "system_prompt": "You are a scientist.",
        "prompt": "Investigate.",
        "seed": 11,
        "max_turns": 10,
        "wall_time_s": 30.0,
    }
    base.update(overrides)
    return AgentConfig(**base)


PREDICT = {"experiment": 0, "low": 0.0, "high": 9.0, "level": 0.9, "hypothesis": "h"}

SCRIPT = (
    Step(text="I will predict, then run.", calls=(("predict", PREDICT),)),
    Step(calls=(("run_experiment", {"rate": 3.0}),)),
    Step(
        thinking="fit three",
        calls=(
            ("fit", {"model": "a"}),
            ("fit", {"model": "b"}),
            ("fit", {"model": "c"}),
        ),
    ),
    Step(calls=(("submit", {"answer": "a", "report": "a fits"}),)),
    Step(text="Done."),
)


def test_a_scripted_run_records_every_turn_and_ends_on_submit() -> None:
    result = run_investigation(_config(), toy_layer(11), driver=ScriptedDriver(SCRIPT))
    assert result.outcome == "submitted"
    assert result.submission == {"answer": "a", "report": "a fits"}
    assert result.served_models == (HAIKU,)
    kinds = [e.kind for e in result.record.entries]
    assert kinds[0] == "config" and kinds[1] == "session"
    assert kinds[-2:] == ["result", "outcome"]
    assert kinds.count("assistant") == 5
    calls = [e.body for e in result.record.entries if e.kind == "tool_call"]
    assert [c["call"] for c in calls] == list(range(6))
    assert [c["name"] for c in calls] == [
        "predict",
        "run_experiment",
        "fit",
        "fit",
        "fit",
        "submit",
    ]
    assert [c["is_error"] for c in calls] == [False, False, False, False, True, False]
    assert calls[4]["charged"] is None and "budget" in calls[4]["text"]
    assert all(c["shown_matches"] for c in calls)


def test_each_tool_call_is_linked_to_the_tool_use_that_asked_for_it() -> None:
    result = run_investigation(_config(), toy_layer(11), driver=ScriptedDriver(SCRIPT))
    uses = {
        block["id"]: block
        for e in result.record.entries
        if e.kind == "assistant"
        for block in e.body["content"]
        if block["type"] == "tool_use"
    }
    for entry in result.record.entries:
        if entry.kind == "tool_call":
            use = uses[entry.body["tool_use_id"]]
            assert use["name"] == f"mcp__lab__{entry.body['name']}"
            assert use["input"] == entry.body["args"]


def test_the_config_entry_carries_what_replay_needs() -> None:
    result = run_investigation(_config(), toy_layer(11), driver=ScriptedDriver(SCRIPT))
    config = result.record.entries[0].body
    assert config["model"] == HAIKU
    assert config["seed"] == 11
    assert config["budgets"] == {"E": 3, "F": 2, "submit": 1}
    assert [t["name"] for t in config["tools"]] == toy_layer(11).names
    for key in ("system_prompt", "prompt", "harness_version", "sdk_version"):
        assert key in config


def test_the_options_are_hermetic() -> None:
    layer = toy_layer(1)
    options, tools = build_options(_config(), layer)
    assert options.tools == []
    assert options.setting_sources == []
    assert options.skills == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert sorted(options.allowed_tools) == sorted(tools)
    assert sorted(tools) == sorted(f"mcp__lab__{n}" for n in layer.names)
    assert isinstance(options.mcp_servers, dict)
    assert list(options.mcp_servers) == ["lab"]
    assert options.max_turns == 10
    assert options.model == HAIKU


def test_a_foreign_tool_use_voids_the_run() -> None:
    script = (
        Step(calls=(("Bash", {"command": "ls"}),), raw_names=True),
        Step(calls=(("submit", {"answer": "x", "report": ""}),)),
    )
    result = run_investigation(_config(), toy_layer(1), driver=ScriptedDriver(script))
    assert result.void
    assert result.outcome == "submitted"
    kinds = [e.kind for e in result.record.entries]
    assert "foreign_tool_result" in kinds


def test_running_out_of_turns_is_an_outcome_not_an_error() -> None:
    script = tuple(Step(calls=(("add", {"a": 1, "b": i}),)) for i in range(20))
    result = run_investigation(
        _config(max_turns=3), toy_layer(1), driver=ScriptedDriver(script)
    )
    assert result.outcome == "max_turns"
    assert result.submission is None


def test_the_wall_time_limit_stops_the_session() -> None:
    script = (Step(text="thinking", delay_s=5.0), Step(text="late"))
    result = run_investigation(
        _config(wall_time_s=0.2), toy_layer(1), driver=ScriptedDriver(script)
    )
    assert result.outcome == "wall_time"
    assert result.record.entries[-1].kind == "outcome"


def test_a_refusal_is_recorded_as_an_outcome() -> None:
    script = (Step(text="I won't."),)
    driver = ScriptedDriver(script, stop_reason="refusal")
    result = run_investigation(_config(), toy_layer(1), driver=driver)
    assert result.outcome == "refused"


def test_a_substitute_model_is_refused_with_the_record_attached() -> None:
    driver = ScriptedDriver(SCRIPT, served_model="claude-sonnet-5-5")
    with pytest.raises(ServedModelError) as caught:
        run_investigation(_config(), toy_layer(11), driver=driver)
    assert caught.value.record.entries[-1].kind == "outcome"


def test_a_contaminated_environment_is_refused_before_any_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    driver = ScriptedDriver(SCRIPT)
    with pytest.raises(SystemConfigurationError, match="ANTHROPIC_API_KEY"):
        run_investigation(_config(), toy_layer(11), driver=driver)
    assert driver.turns == 0


def test_a_handler_bug_aborts_the_run_rather_than_reaching_the_model() -> None:
    def broken(args: Mapping[str, Any]) -> ToolOutput:
        raise RuntimeError("bug in a tool")

    empty: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    layer = ToolLayer([ToolSpec("broken", "b", empty, broken, None)], {})
    script = (Step(calls=(("broken", {}),)), Step(text="never"))
    with pytest.raises(HarnessFaultError) as caught:
        run_investigation(_config(), layer, driver=ScriptedDriver(script))
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert caught.value.record.entries[-1].body["reason"] == "harness_fault"


def test_tool_handlers_run_off_the_event_loop_thread() -> None:
    seen: list[str] = []

    def where(args: Mapping[str, Any]) -> ToolOutput:
        seen.append(threading.current_thread().name)
        return ToolOutput("ok")

    empty: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    layer = ToolLayer([ToolSpec("where", "w", empty, where, None)], {})
    script = (Step(calls=(("where", {}),)),)
    run_investigation(_config(), layer, driver=ScriptedDriver(script))
    assert seen and seen[0] != threading.current_thread().name


def test_the_sync_entry_point_refuses_a_running_loop() -> None:
    async def inner() -> None:
        with pytest.raises(SystemConfigurationError):
            run_investigation(_config(), toy_layer(1), driver=ScriptedDriver(SCRIPT))
        result = await arun_investigation(
            _config(), toy_layer(11), driver=ScriptedDriver(SCRIPT)
        )
        assert result.outcome == "submitted"

    asyncio.run(inner())


def test_timing_is_recorded_outside_the_hash() -> None:
    a = run_investigation(_config(), toy_layer(11), driver=ScriptedDriver(SCRIPT))
    b = run_investigation(_config(), toy_layer(11), driver=ScriptedDriver(SCRIPT))
    assert a.record.head == b.record.head
    assert a.record.timing["wall_s"] >= 0.0
    assert len(a.record.timing["entries"]) == len(a.record.entries)


def test_the_undated_pricing_id_of_the_pinned_snapshot_is_accepted() -> None:
    """Measured live: Haiku 4.5 pinned by date reports ``claude-haiku-4-5``."""
    driver = ScriptedDriver(SCRIPT, canonical_model="claude-haiku-4-5")
    result = run_investigation(_config(), toy_layer(11), driver=driver)
    assert result.served_models == ("claude-haiku-4-5",)
    other = ScriptedDriver(SCRIPT, canonical_model="claude-sonnet-4-5")
    with pytest.raises(ServedModelError):
        run_investigation(_config(), toy_layer(11), driver=other)


def test_a_family_canonical_id_is_accepted_for_the_pinned_model() -> None:
    """Measured live: ``claude-sonnet-5-5`` reports ``canonicalModel``
    ``claude-sonnet-5``. Every assistant turn still carries the pinned id."""
    config = dataclasses.replace(_config(), model="claude-sonnet-5-5")
    driver = ScriptedDriver(
        SCRIPT, served_model="claude-sonnet-5-5", canonical_model="claude-sonnet-5"
    )
    result = run_investigation(config, toy_layer(11), driver=driver)
    assert result.served_models == ("claude-sonnet-5",)
    wrong_family = ScriptedDriver(
        SCRIPT, served_model="claude-sonnet-5-5", canonical_model="claude-opus-5"
    )
    with pytest.raises(ServedModelError):
        run_investigation(config, toy_layer(11), driver=wrong_family)
    wrong_turns = ScriptedDriver(
        SCRIPT, served_model="claude-sonnet-5", canonical_model="claude-sonnet-5"
    )
    with pytest.raises(ServedModelError):
        run_investigation(config, toy_layer(11), driver=wrong_turns)
