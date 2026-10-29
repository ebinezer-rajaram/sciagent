"""The void policy at harness time (P4-PROPOSAL Δ8, audit §3).

A run is void only if a foreign tool *executed* (a non-error result), on a
server tool use, or on an out-of-bounds hit (the runner's scan; see
``tests/investigation/test_investigation_voids.py``). A call to a name the
session does not offer, denied with an error result, is counted in
``unknown_tool_attempts`` and does not void the run. A foreign ``tool_use``
with no recorded denial stays void: nothing shows it did not run.

The classifier is the one :mod:`sciagent.campaign.voids` applies at analysis
time, so the two readings of one record agree (tested here on live-loop
records and in ``tests/campaign/test_campaign_voids.py`` on the pilot shapes).
"""

from __future__ import annotations

import pytest
from harness_toy import toy_layer

from sciagent.harness.live import (
    MODELS,
    AgentConfig,
    InvestigationResult,
    run_investigation,
)
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.harness.voids import (
    FOREIGN,
    ForeignResult,
    assess,
    classify,
    denial_text,
    foreign_results,
    judge,
    server_requests,
)

HAIKU = MODELS["haiku"]
SUBMIT = Step(calls=(("submit", {"answer": "x", "report": ""}),))


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _config() -> AgentConfig:
    return AgentConfig(
        model=HAIKU,
        system_prompt="You are a scientist.",
        prompt="Investigate.",
        seed=11,
        max_turns=10,
        wall_time_s=30.0,
    )


def _run(*steps: Step) -> InvestigationResult:
    return run_investigation(
        _config(), toy_layer(1), driver=ScriptedDriver((*steps, SUBMIT))
    )


def test_a_denied_bare_name_is_counted_not_void() -> None:
    result = _run(Step(calls=(("python", {"code": "print(1)"}),), raw_names=True))
    assert result.outcome == "submitted"
    assert result.foreign_tool_uses == ("python",)
    assert not result.void
    assert result.void_reasons == ()
    assert result.unknown_tool_attempts == 1


def test_two_denied_names_are_two_attempts() -> None:
    result = _run(
        Step(
            calls=(("python", {"code": "1"}), ("Bash", {"command": "ls"})),
            raw_names=True,
        )
    )
    assert not result.void and result.unknown_tool_attempts == 2


def test_an_executed_foreign_tool_is_void() -> None:
    result = _run(
        Step(calls=(("Bash", {"command": "ls"}),), raw_names=True, foreign="executed")
    )
    assert result.void
    assert result.void_reasons == ("foreign tool executed: Bash",)
    assert result.unknown_tool_attempts == 0
    (entry,) = result.record.of_kind("foreign_tool_result")
    assert entry.body["is_error"] is False


def test_a_foreign_use_without_a_recorded_denial_is_void() -> None:
    result = _run(
        Step(calls=(("python", {"code": "1"}),), raw_names=True, foreign="unanswered")
    )
    assert result.record.of_kind("foreign_tool_result") == ()
    assert result.void
    assert result.void_reasons == ("foreign tool without a recorded denial: python",)


def test_a_server_tool_use_is_void() -> None:
    result = _run(
        Step(
            calls=(("add", {"a": 1, "b": 2}),),
            server_tools=("web_search",),
        )
    )
    assert result.foreign_tool_uses == ("server:web_search",)
    assert result.void
    assert result.void_reasons == ("server tool use: web_search",)


def test_a_clean_run_is_not_void() -> None:
    result = _run(Step(calls=(("add", {"a": 1, "b": 2}),)))
    assert not result.void
    assert result.void_reasons == () and result.unknown_tool_attempts == 0


AGREEMENT_SCRIPTS = {
    "clean": Step(calls=(("add", {"a": 1, "b": 2}),)),
    "denied": Step(calls=(("python", {"code": "1"}),), raw_names=True),
    "denied-twice": Step(
        calls=(("python", {"code": "1"}), ("python", {"code": "2"})), raw_names=True
    ),
    "executed": Step(
        calls=(("Bash", {"command": "ls"}),), raw_names=True, foreign="executed"
    ),
    "mixed": Step(
        calls=(("Bash", {"command": "ls"}), ("python", {"code": "1"})),
        raw_names=True,
        foreign="executed",
    ),
    "unanswered": Step(
        calls=(("python", {"code": "1"}),), raw_names=True, foreign="unanswered"
    ),
    "server": Step(calls=(("add", {"a": 1, "b": 2}),), server_tools=("web_fetch",)),
}


@pytest.mark.parametrize("name", sorted(AGREEMENT_SCRIPTS))
def test_the_harness_and_analysis_classifications_agree(name: str) -> None:
    """Analysis time reads a stored cell: the pilot form (``harness/1``: raw
    ``foreign tool:`` reasons, void flag 1) and the new form (policy reasons
    and the attempt count in the reading). Both give the harness's verdict."""
    result = _run(AGREEMENT_SCRIPTS[name])
    results = result.record.of_kind("result")
    usage = dict(results[-1].body.get("usage") or {})
    raw = [FOREIGN + n for n in result.foreign_tool_uses]
    pilot = classify(
        {"status": 1.0, "void": 1.0 if raw else 0.0},
        {"void_reasons": raw, "usage": usage},
        foreign_results(result.record),
    )
    stored = classify(
        {
            "status": 1.0,
            "void": 1.0 if result.void else 0.0,
            "unknown_tool_attempts": float(result.unknown_tool_attempts),
        },
        {"void_reasons": list(result.void_reasons), "usage": usage},
        (),
    )
    for analysed in (pilot, stored):
        assert analysed.void == result.void
        assert analysed.reasons == result.void_reasons
        assert analysed.unknown_tool_attempts == result.unknown_tool_attempts
    assert not stored.reclassified


def test_judge_is_idempotent_on_its_own_reasons() -> None:
    """A new cell stores policy reasons; reading them again changes nothing."""
    first = judge(
        [
            FOREIGN + "Bash",
            FOREIGN + "python",
            FOREIGN + "server:web_search",
            "infrastructure: sandbox died",
            "out of bounds: call 3: /proc\\b",
        ],
        (
            ForeignResult("Bash", False, "ran"),
            ForeignResult("python", True, denial_text("python")),
        ),
        1,
    )
    again = judge(first.reasons, (), 1)
    assert again.reasons == first.reasons
    assert first.unknown_tool_attempts == 1 and again.unknown_tool_attempts == 0


def test_server_requests_are_read_from_the_usage() -> None:
    assert server_requests(None) == 0
    assert server_requests({}) == 0
    assert server_requests({"server_tool_use": {"web_search_requests": 2}}) == 2
    result = _run(Step(calls=(("add", {"a": 1, "b": 2}),)))
    assert assess([], result.record) == judge([], (), 0)
