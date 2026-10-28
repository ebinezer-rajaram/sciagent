"""``scripts/replay_check.py``: every stored investigation record of a store,
replayed offline against the pilot's pointproc environment (SPEC §6.3 no. 9 at
the store level).

The script runs in-process through its ``main``, with ``scripts/`` importable
(it imports ``pilot``). The fast tests use records that never reach a tool
call; the slow one records a real scripted AG-c session on a dev truth through
the pilot's own environment, and is the positive control: the honest record
passes and a consistently re-chained copy with one tool result changed fails at
that call (SPEC §6.4).
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sciagent.harness.record import SessionRecord, digest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SPLIT = Path(__file__).resolve().parents[2] / "data" / "v2" / "truths" / "dev"
HONEST = "aaaaaaaaaaaaaaaa"
FORGED = "bbbbbbbbbbbbbbbb"
STRUCTURE = "Excite(ExpK, Mark(size), all)"


def _check(
    monkeypatch: pytest.MonkeyPatch, *args: str | Path
) -> subprocess.CompletedProcess[str]:
    """``replay_check.py``'s ``main`` in this process, as a completed process."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "replay_check", SCRIPTS / "replay_check.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    # Registered first, as importlib's recipe does: its dataclass resolves
    # its annotations through ``sys.modules``.
    monkeypatch.setitem(sys.modules, "replay_check", module)
    spec.loader.exec_module(module)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main([str(a) for a in args])
    return subprocess.CompletedProcess(
        "replay_check.py", code, out.getvalue(), err.getvalue()
    )


def _config_only(path: Path, truth_id: str) -> SessionRecord:
    """A record holding only a config entry naming ``truth_id``."""
    record = SessionRecord()
    record.append(
        "config",
        {
            "investigation": {
                "arm": "AG-c",
                "condition": "named",
                "environment": "pointproc-v2",
                "seed": 1,
                "truth_id": truth_id,
            }
        },
    )
    path.parent.mkdir(parents=True)
    record.save(path)
    return record


def test_replay_check_fails_when_no_record_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "s" / "llm" / HONEST).mkdir(parents=True)
    (tmp_path / "s" / "llm" / HONEST / "aborted-0123456789abcdef.json").write_text(
        "{}", encoding="utf-8"
    )
    done = _check(monkeypatch, "--root", tmp_path / "s", "--split", SPLIT)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "no investigation records" in done.stdout
    assert done.stdout.rstrip().endswith("REPLAY FAILED")


def test_replay_check_refuses_bad_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = _check(monkeypatch, "--root", tmp_path / "nope", "--split", SPLIT)
    assert missing.returncode == 2
    assert not (tmp_path / "nope").exists()
    (tmp_path / "s").mkdir()
    nosplit = _check(monkeypatch, "--root", tmp_path / "s", "--split", tmp_path)
    assert nosplit.returncode == 2
    assert f"{tmp_path}: no truths.json" in nosplit.stderr
    absent = _check(monkeypatch, "--root", tmp_path / "s", "--split", tmp_path / "dev")
    assert absent.returncode == 2
    assert f"{tmp_path / 'dev'}: the split is missing" in absent.stderr
    assert "git-ignored" in absent.stderr and "sample_truths.py" in absent.stderr
    with pytest.raises(SystemExit) as stopped:
        _check(monkeypatch, "--root", tmp_path / "s", "--limit", "0")
    assert stopped.value.code == 2


def test_replay_check_fails_records_it_cannot_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record that does not parse, one whose hash chain is broken, and one
    naming a truth the split does not hold each fail by name, before any
    replay; the run fails and counts them."""
    root = tmp_path / "s"
    garbage = root / "llm" / "1111111111111111" / "investigation-0000000000000001.json"
    garbage.parent.mkdir(parents=True)
    garbage.write_text("not json", encoding="utf-8")
    edited = root / "llm" / "2222222222222222" / "investigation-0000000000000002.json"
    _config_only(edited, "dev-006")
    edited.write_text(
        edited.read_text(encoding="utf-8").replace("dev-006", "dev-008"),
        encoding="utf-8",
    )
    foreign = root / "llm" / "3333333333333333" / "investigation-0000000000000003.json"
    _config_only(foreign, "dev-999")
    done = _check(monkeypatch, "--root", root, "--split", SPLIT)
    assert done.returncode == 1, done.stdout + done.stderr
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith(("PASS", "FAIL"))]
    assert [ln.split(" ")[1].rstrip(":") for ln in lines] == [
        str(p.relative_to(root)) for p in (garbage, edited, foreign)
    ]
    assert "RecordFormatError" in lines[0]
    assert "RecordIntegrityError" in lines[1]
    assert "AG-c named dev-999 s1" in lines[2]
    assert "truth dev-999 is not in the split" in lines[2]
    assert "replayed: 3 of 3; passed: 0; failed: 3" in done.stdout
    assert done.stdout.rstrip().endswith("REPLAY FAILED")


def _forge(record: SessionRecord, call: int) -> SessionRecord:
    """A consistently re-chained copy in which tool call ``call``'s text (and
    its digest) were changed: every hash verifies, so only replay can tell."""
    forged = SessionRecord()
    for entry in record.entries:
        body: dict[str, Any] = json.loads(json.dumps(entry.body))
        if entry.kind == "tool_call" and body["call"] == call:
            body["text"] = body["text"] + " "
            body["digest"] = digest(
                {
                    "text": body["text"],
                    "is_error": body["is_error"],
                    "record": body["record"],
                }
            )
        forged.append(entry.kind, body)
    return forged


@pytest.mark.slow
def test_replay_check_passes_a_recorded_session_and_names_a_forged_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sciagent.harness.live import MODELS
    from sciagent.harness.scripted import ScriptedDriver, Step
    from sciagent.investigation import run
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    pilot = importlib.import_module("pilot")
    env = pilot.pointproc_environment(pilot.load_split(SPLIT)).investigation
    assert env is not None
    root = tmp_path / "s"
    script = (
        Step(calls=(("fit", {"structure": STRUCTURE, "data_ids": ["obs"]}),)),
        Step(calls=(("submit", {"structure": STRUCTURE, "report": "done"}),)),
    )
    outcome = run(
        env,
        "dev-006",
        "AG-c",
        "named",
        MODELS["haiku"],
        1,
        root / "llm" / HONEST,
        experiments=2,
        fits=2,
        max_turns=10,
        wall_time_s=600.0,
        driver=ScriptedDriver(script),
    )
    assert outcome.outcome == "submitted", outcome
    record = SessionRecord.load(outcome.record_path)
    forged = _forge(record, 0)
    forged_path = root / "llm" / FORGED / f"investigation-{forged.head[:16]}.json"
    forged_path.parent.mkdir(parents=True)
    forged.save(forged_path)

    done = _check(monkeypatch, "--root", root, "--split", SPLIT)
    assert done.returncode == 1, done.stdout + done.stderr
    honest = f"PASS {outcome.record_path.relative_to(root)} AG-c named dev-006 s1: "
    assert honest + "steps=2 budgets={'experiments': 0, 'fits': 1, 'submit': 1}" in (
        done.stdout
    )
    assert f"FAIL {forged_path.relative_to(root)} AG-c named dev-006 s1: " in (
        done.stdout
    )
    assert "step 0 field digest:" in done.stdout
    assert "replayed: 2 of 2; passed: 1; failed: 1" in done.stdout
    assert done.stdout.rstrip().endswith("REPLAY FAILED")

    first = _check(monkeypatch, "--root", root, "--split", SPLIT, "--limit", "1")
    assert first.returncode == 0, first.stdout + first.stderr
    assert honest in first.stdout and "FAIL" not in first.stdout
    assert "replayed: 1 of 2; passed: 1; failed: 0" in first.stdout
    assert first.stdout.rstrip().endswith("REPLAY OK")

    other = _check(monkeypatch, "--root", root, "--split", SPLIT, "--arm", "AG-o")
    assert other.returncode == 1, other.stdout + other.stderr
    assert "no investigation records" in other.stdout
