"""Instrument test 9: a recorded investigation replays byte-identically with the
provider disabled, and a perturbed tool result is detected (SPEC §6.3).

Recorded here with a scripted model over the toy layer; the AG-o version of this
test arrives with the sandbox. What is tested is the machinery: re-issuing every
recorded call through a fresh layer and comparing digests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from harness_toy import toy_factory, toy_layer

from sciagent.harness.errors import ReplayDivergenceError
from sciagent.harness.live import MODELS, AgentConfig, run_investigation
from sciagent.harness.record import SessionRecord, digest
from sciagent.harness.replay import replay
from sciagent.harness.scripted import ScriptedDriver, Step

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

PREDICT = {"experiment": 0, "low": 0.0, "high": 9.0, "level": 0.9, "hypothesis": "h"}
SCRIPT = (
    Step(calls=(("predict", PREDICT),)),
    Step(calls=(("run_experiment", {"rate": 3.0}),)),
    Step(calls=(("run_experiment", {"rate": 7.5}),)),
    Step(calls=(("fit", {"model": "a"}), ("fit", {"model": "b"}))),
    Step(calls=(("fit", {"model": "c"}),)),
    Step(calls=(("submit", {"answer": "a", "report": "r"}),)),
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _recorded() -> SessionRecord:
    config = AgentConfig(
        model=MODELS["haiku"],
        system_prompt="s",
        prompt="p",
        seed=5,
        max_turns=20,
        wall_time_s=30.0,
    )
    return run_investigation(config, toy_layer(5), driver=ScriptedDriver(SCRIPT)).record


def _rebuilt(record: SessionRecord, edit: Any) -> SessionRecord:
    """Return a consistently re-chained copy with ``edit`` applied to the bodies.

    This is the forger's record: every hash checks out, so only replay can tell.
    """
    bodies = [(e.kind, json.loads(json.dumps(e.body))) for e in record.entries]
    edit(bodies)
    forged = SessionRecord()
    for kind, body in bodies:
        forged.append(kind, body)
    return forged


def _tool_calls(bodies: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [body for kind, body in bodies if kind == "tool_call"]


def test_a_recorded_run_replays_cleanly(tmp_path: Path) -> None:
    record = _recorded()
    path = tmp_path / "run.json"
    record.save(path)
    report = replay(SessionRecord.load(path), toy_factory)
    assert report.head == record.head
    assert report.steps == 7
    assert report.submission == {"answer": "a", "report": "r"}
    assert report.budgets_used == {"E": 2, "F": 2, "submit": 1}


def test_one_byte_in_a_recorded_result_is_detected_at_its_step() -> None:
    record = _recorded()

    def flip(bodies: list[tuple[str, dict[str, Any]]]) -> None:
        call = _tool_calls(bodies)[2]  # the second experiment
        call["text"] = call["text"].replace("[", "(", 1)
        call["digest"] = digest(
            {
                "text": call["text"],
                "is_error": call["is_error"],
                "record": call["record"],
            }
        )

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(_rebuilt(record, flip), toy_factory)
    assert caught.value.step == 2
    assert caught.value.field == "digest"


def test_a_result_inconsistent_with_its_own_digest_is_detected() -> None:
    record = _recorded()

    def flip(bodies: list[tuple[str, dict[str, Any]]]) -> None:
        call = _tool_calls(bodies)[3]
        call["text"] = call["text"] + " "

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(_rebuilt(record, flip), toy_factory)
    assert caught.value.step == 3


def test_a_changed_tool_implementation_is_detected() -> None:
    record = _recorded()

    def other_seed(config: Any) -> Any:
        return toy_layer(int(config["seed"]) + 1)

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(record, other_seed)
    assert caught.value.step == 1  # the first seeded experiment; predict is unseeded


def test_a_changed_tool_schema_is_detected_before_any_call() -> None:
    record = _recorded()

    def fewer_fits(config: Any) -> Any:
        return toy_layer(int(config["seed"]), fits=3)

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(record, fewer_fits)
    assert caught.value.field == "budgets"


def test_forged_budget_accounting_is_detected() -> None:
    record = _recorded()

    def forge(bodies: list[tuple[str, dict[str, Any]]]) -> None:
        _tool_calls(bodies)[4]["budgets_after"]["F"] = 1

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(_rebuilt(record, forge), toy_factory)
    assert (caught.value.step, caught.value.field) == (4, "budgets_after")


def test_a_forged_submission_is_detected() -> None:
    record = _recorded()

    def forge(bodies: list[tuple[str, dict[str, Any]]]) -> None:
        for kind, body in bodies:
            if kind == "outcome":
                body["submission"] = {"answer": "the truth", "report": "r"}

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(_rebuilt(record, forge), toy_factory)
    assert caught.value.field == "submission"


def test_a_dropped_tool_call_is_detected() -> None:
    record = _recorded()

    def drop(bodies: list[tuple[str, dict[str, Any]]]) -> None:
        index = next(
            i for i, (k, b) in enumerate(bodies) if k == "tool_call" and b["call"] == 3
        )
        del bodies[index]

    with pytest.raises(ReplayDivergenceError) as caught:
        replay(_rebuilt(record, drop), toy_factory)
    assert caught.value.field == "call"


def test_replay_runs_with_the_agent_sdk_unimportable(tmp_path: Path) -> None:
    """Provider disabled: replay must not import ``claude_agent_sdk`` at all."""
    path = tmp_path / "run.json"
    _recorded().save(path)
    code = (
        "import sys\n"
        "sys.modules['claude_agent_sdk'] = None\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "from harness_toy import toy_factory\n"
        "from sciagent.harness.record import SessionRecord\n"
        "from sciagent.harness.replay import replay\n"
        "from pathlib import Path\n"
        f"r = replay(SessionRecord.load(Path({str(path)!r})), toy_factory)\n"
        "loaded = [m for m in sys.modules if sys.modules[m] is not None]\n"
        "assert 'claude_agent_sdk' not in loaded\n"
        "print(r.head, r.steps)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[1] == "7"
