"""The void policy (P4-PROPOSAL Δ8, audit §3), read from stored session records.

A session is void only if a foreign tool *executed* (a non-error result), on
server tool use, or on an out-of-bounds hit (and, kept from before, a sandbox
infrastructure fault). A denied call to a tool name that does not exist is an
``unknown_tool_attempt``, not a void. Void sessions are never scored, and the
analysis drops them from scoring while counting them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import analysis
from sciagent.campaign import driver as driver_module
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import ScoreJob, UnitResult
from sciagent.campaign.plan import LLMArm, Unit, score_key, session_key
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import (
    ForeignResult,
    classify,
    denial_text,
    session_void,
)
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.harness.voids import assess
from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

DONE = {"status": 1.0, "void": 1.0, "submitted": 1.0}
#: The denial of the 9 pilot sessions' bare ``python`` call, verbatim from
#: their records (the Claude Code CLI's text for a name it does not have).
PILOT_DENIAL = "<tool_use_error>Error: No such tool available: python</tool_use_error>"


def _detail(*reasons: str, **extra: Any) -> dict[str, Any]:
    return {"void_reasons": list(reasons), "usage": {}, **extra}


def test_a_denied_unknown_tool_name_is_counted_not_void() -> None:
    v = classify(
        DONE,
        _detail("foreign tool: python", "foreign tool: python"),
        (
            ForeignResult("python", True, PILOT_DENIAL),
            ForeignResult("python", True, PILOT_DENIAL),
        ),
    )
    assert not v.void and v.unknown_tool_attempts == 2
    assert v.stored_void and v.reclassified and v.reasons == ()


def test_an_executed_foreign_tool_is_void() -> None:
    v = classify(
        DONE,
        _detail("foreign tool: Bash", "foreign tool: python"),
        (
            ForeignResult("Bash", False, "a.txt"),
            ForeignResult("python", True, PILOT_DENIAL),
        ),
    )
    assert v.void and v.unknown_tool_attempts == 1
    assert v.reasons == ("foreign tool executed: Bash",)


def test_only_the_cli_denial_text_counts_as_a_denial() -> None:
    """An error result is a denial only with the CLI's "No such tool" text for
    that very name; any other error (a tool that ran and failed, a permission
    message, a denial naming another tool) is counted as executed: void."""
    assert denial_text("python") == PILOT_DENIAL
    for text in (
        "Traceback (most recent call last): ZeroDivisionError",
        "<tool_use_error>Error: No such tool available: Bash</tool_use_error>",
        "",
    ):
        v = classify(
            DONE,
            _detail("foreign tool: python"),
            (ForeignResult("python", True, text),),
        )
        assert v.void and v.reasons == ("foreign tool executed: python",), text
        assert v.unknown_tool_attempts == 0


def test_a_foreign_use_without_a_recorded_denial_is_void() -> None:
    v = classify(DONE, _detail("foreign tool: python"), ())
    assert v.void and v.reasons == ("foreign tool without a recorded denial: python",)


def test_server_tool_use_and_out_of_bounds_are_void() -> None:
    server = classify(DONE, _detail("foreign tool: server:web_search"), ())
    assert server.void and server.reasons == ("server tool use: web_search",)
    counted = classify(
        {**DONE, "void": 0.0},
        _detail(usage={"server_tool_use": {"web_search_requests": 1}}),
        (),
    )
    assert counted.void and not counted.stored_void
    oob = classify(DONE, _detail("out of bounds: call 3: /proc\\b"), ())
    assert oob.void and oob.reasons == ("out of bounds: call 3: /proc\\b",)
    infra = classify(DONE, _detail("infrastructure: sandbox died"), ())
    assert infra.void


def test_an_aborted_session_stays_void_and_a_clean_one_stays_clean() -> None:
    aborted = classify(
        {"status": 0.0, "void": 1.0},
        {"outcome": "hard_timeout", "error": "HardTimeout"},
        (),
    )
    assert aborted.void and aborted.reasons == ("not completed: hard_timeout",)
    clean = classify({**DONE, "void": 0.0}, _detail(), ())
    assert not clean.void and not clean.reclassified


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


BARE_PYTHON = (
    Step(calls=(("python", {"code": "print(1)"}),), raw_names=True),
    Step(
        calls=(("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]}),)
    ),
    Step(calls=(("submit", {"structure": "Excite(ExpK, One, all)", "report": "r"}),)),
    Step(text="done"),
)


def test_a_session_with_a_denied_bare_name_is_scored_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the real harness: the denied bare name is counted
    in the stored reading (``unknown_tool_attempts = 1``) and does not void it
    (``void = 0``; the pilot's ``harness/1`` stored 1), the driver scores it,
    and the analysis keeps its row and counts the attempt."""
    _clean(monkeypatch)
    scored: list[ScoreJob] = []

    def fake_score(job: ScoreJob) -> UnitResult:
        scored.append(job)
        return UnitResult(
            job.system, True, {"status": 1.0, "per_event": -0.7}, {"s": 1}, 0.0
        )

    monkeypatch.setattr(driver_module, "run_score_job", fake_score)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truth = entry("hawkes")
    c = Campaign(
        tmp_path,
        environment(),
        config,
        [truth],
        DriverSettings(
            workers=0,
            poll_s=0.05,
            run_baselines=False,
            driver_factory=lambda u: ScriptedDriver(BARE_PYTHON),
        ),
    )
    c.run()
    unit = Unit("hawkes", 1, "AG-c-H/named")
    cell = c.store.cell(session_key(unit, truth, environment(), config).digest)
    c.close()
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    assert reading["void"] == 0.0 and reading["unknown_tool_attempts"] == 1.0
    assert cell["detail"]["session"]["void_reasons"] == []
    status = session_void(tmp_path, reading, cell["detail"]["session"])
    assert not status.void and status.unknown_tool_attempts == 1
    assert len(scored) == 1
    with ResultStore(tmp_path) as store:
        tab = analysis.load_rows(store, environment(), config, [truth])
    (session,) = tab.sessions
    assert not session.void.void and session.void.unknown_tool_attempts == 1
    assert [r.system for r in tab.rows] == ["AG-c-H/named"]


def test_a_void_session_is_not_scored_and_the_analysis_drops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """driver.py's old rule scored every status-1 session, voids included."""
    scored: list[ScoreJob] = []

    def record(job: ScoreJob) -> UnitResult:
        scored.append(job)
        raise AssertionError("a void session must not be scored")

    monkeypatch.setattr(driver_module, "run_score_job", record)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-o", "named"),))
    truth = entry("hawkes")
    env = environment()
    unit = Unit("hawkes", 1, "AG-o-H/named")
    skey = session_key(unit, truth, env, config)
    detail = {
        "unit": {"truth": "hawkes", "seed": 1, "system": unit.system},
        "session": {
            "outcome": "submitted",
            "submission": "link=identity; Excite(ExpK, One, all)",
            "fitted": [],
            "void_reasons": ["out of bounds: call 1: /proc\\b"],
            "usage": {},
            "record_dir": "llm/x",
            "record": "investigation-x.json",
        },
    }
    with ResultStore(tmp_path) as store:
        store.put(skey, DONE, detail)

    def refuse(u: Unit) -> ScriptedDriver:
        raise AssertionError("no session may be launched")

    c = Campaign(
        tmp_path,
        env,
        config,
        [truth],
        DriverSettings(
            workers=0, poll_s=0.05, run_baselines=False, driver_factory=refuse
        ),
    )
    c.run()
    c.close()
    assert scored == []
    assert "void" in (tmp_path / "progress.log").read_text(encoding="utf-8")
    with ResultStore(tmp_path) as store:
        # A score written by the old rule (the pilot's) is not read either.
        store.put(
            score_key(unit, skey, env, config),
            {"status": 1.0, "per_event": -0.7},
            {"unit": {}, "result": {}},
        )
        tab = analysis.load_rows(store, env, config, [truth])
    (session,) = tab.sessions
    assert session.void.void
    assert tab.rows == ()
    assert analysis.void_counts(tab) == {unit.system: 1}


def test_an_executed_foreign_tool_is_stored_void_and_not_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean(monkeypatch)

    def refuse(job: ScoreJob) -> UnitResult:
        raise AssertionError("a void session must not be scored")

    monkeypatch.setattr(driver_module, "run_score_job", refuse)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truth = entry("hawkes")
    executed = (
        Step(calls=(("Bash", {"command": "ls"}),), raw_names=True, foreign="executed"),
        *BARE_PYTHON[1:],
    )
    c = Campaign(
        tmp_path,
        environment(),
        config,
        [truth],
        DriverSettings(
            workers=0,
            poll_s=0.05,
            run_baselines=False,
            driver_factory=lambda u: ScriptedDriver(executed),
        ),
    )
    c.run()
    unit = Unit("hawkes", 1, "AG-c-H/named")
    cell = c.store.cell(session_key(unit, truth, environment(), config).digest)
    c.close()
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    assert reading["void"] == 1.0 and reading["unknown_tool_attempts"] == 0.0
    session = cell["detail"]["session"]
    assert session["void_reasons"] == ["foreign tool executed: Bash"]
    status = session_void(tmp_path, reading, session)
    assert status.void and not status.reclassified


#: The pilot's record shapes (the tests above), as (stored reasons, foreign
#: tool results in record order, the result's usage).
PILOT_SHAPES: dict[str, tuple[tuple[str, ...], tuple[ForeignResult, ...], Any]] = {
    "denied twice": (
        ("foreign tool: python", "foreign tool: python"),
        (
            ForeignResult("python", True, PILOT_DENIAL),
            ForeignResult("python", True, PILOT_DENIAL),
        ),
        {},
    ),
    "executed and denied": (
        ("foreign tool: Bash", "foreign tool: python"),
        (
            ForeignResult("Bash", False, "ran"),
            ForeignResult("python", True, PILOT_DENIAL),
        ),
        {},
    ),
    "no recorded denial": (("foreign tool: python",), (), {}),
    "server block": (("foreign tool: server:web_search",), (), {}),
    "server usage": ((), (), {"server_tool_use": {"web_search_requests": 1}}),
    "out of bounds": (("out of bounds: call 3: /proc\b",), (), {}),
    "infrastructure": (("infrastructure: sandbox died",), (), {}),
    "clean": ((), (), {}),
    "everything": (
        (
            "foreign tool: Bash",
            "infrastructure: sandbox died",
            "out of bounds: call 3: /proc\b",
            "foreign tool: server:web_search",
            "foreign tool: python",
        ),
        (
            ForeignResult("python", True, PILOT_DENIAL),
            ForeignResult("Bash", True, denial_text("Bash")),
        ),
        {"server_tool_use": {"web_fetch_requests": 2}},
    ),
}


def _pilot_record(results: tuple[ForeignResult, ...], usage: Any) -> SessionRecord:
    record = SessionRecord()
    for k, r in enumerate(results):
        record.append(
            "foreign_tool_result",
            {
                "tool_use_id": f"toolu_{k:03d}",
                "name": r.name,
                "text": r.text,
                "is_error": r.is_error,
            },
        )
    record.append("result", {"subtype": "success", "usage": usage})
    return record


@pytest.mark.parametrize("shape", sorted(PILOT_SHAPES))
def test_harness_and_analysis_classify_the_pilot_shapes_alike(
    shape: str, tmp_path: Path
) -> None:
    """The harness classifies a record at run time; the analysis classifies
    the stored cell, reading the saved record. On one record they agree."""
    reasons, results, usage = PILOT_SHAPES[shape]
    record = _pilot_record(results, usage)
    (tmp_path / "llm").mkdir()
    record.save(tmp_path / "llm" / "investigation-x.json")
    at_harness = assess(reasons, record)
    detail = {
        "void_reasons": list(reasons),
        "usage": usage,
        "record_dir": "llm",
        "record": "investigation-x.json",
    }
    at_analysis = session_void(
        tmp_path, {"status": 1.0, "void": 1.0 if reasons else 0.0}, detail
    )
    assert at_analysis.void == at_harness.void
    assert at_analysis.reasons == at_harness.reasons
    assert at_analysis.unknown_tool_attempts == at_harness.unknown_tool_attempts
    # A cell written under the policy stores the harness's verdict; reading it
    # back gives the same verdict without the record.
    stored = classify(
        {
            "status": 1.0,
            "void": 1.0 if at_harness.void else 0.0,
            "unknown_tool_attempts": float(at_harness.unknown_tool_attempts),
        },
        {"void_reasons": list(at_harness.reasons), "usage": usage},
        (),
    )
    assert (stored.void, stored.reasons, stored.unknown_tool_attempts) == (
        at_harness.void,
        at_harness.reasons,
        at_harness.unknown_tool_attempts,
    )


def test_the_campaign_module_re_exports_the_harness_classifier() -> None:
    from sciagent.harness import voids as harness_voids

    assert classify is harness_voids.classify
    assert ForeignResult is harness_voids.ForeignResult
