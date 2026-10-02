"""Instrument test 9, end to end: a recorded investigation replays offline, and
a perturbed tool or sandbox result is detected (SPEC §6.3, §4 "Determinism
with a sandbox").

The AG-o test runs real containers, so it is ``slow`` and fails (never skips)
without Docker, through the shared ``sandbox_image`` fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from investigation_support import (
    POINTPROC,
    clean_environment,
    plain_run,
    scripted_run,
)

from sciagent.harness.errors import ReplayDivergenceError
from sciagent.harness.record import SessionRecord, digest
from sciagent.harness.scripted import Step
from sciagent.investigation import replay_investigation
from sciagent.investigation.runner import submitted_structure

PREDICT_E1: dict[str, Any] = {
    "experiment": 1,
    "diagnostic": "mark_gap_correlation",
    "args": {"channel": "size"},
    "hypothesis": "Excite(ExpK, Mark(size), all)",
    "interval": [-0.5, 0.0],
    "level": 0.9,
}

CONSTRAINED = (
    Step(text="look", calls=(("diagnostic", {"name": "mean_rate", "data_id": "obs"}),)),
    Step(calls=(("run_experiment", plain_run(300.0)),)),
    Step(
        calls=(
            (
                "fit",
                {
                    "structure": "Excite(ExpK, Mark(size), all)",
                    "data_ids": ["obs", "e0"],
                },
            ),
            ("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]}),
        )
    ),
    Step(calls=(("predict", PREDICT_E1),)),
    Step(
        calls=(
            (
                "run_experiment",
                {
                    "intervention": {
                        "type": "inject_marks",
                        "window": [0.0, 300.0],
                        "channel": "size",
                        "value": 1.2,
                    },
                    "horizon": 300.0,
                },
            ),
        )
    ),
    Step(
        calls=(
            (
                "submit",
                {"structure": "Excite(ExpK, Mark(size), all)", "report": "done"},
            ),
        )
    ),
)

READ_DATA = """
import json
import pandas as pd
d = pd.read_csv("/data/e0.csv")
gaps = d["time"].diff().dropna()
state = {"n": int(len(d)), "mean_gap": float(gaps.mean())}
with open("state.json", "w") as f:
    json.dump(state, f)
print(state["n"], round(state["mean_gap"], 6), sorted(d.columns))
"""
READ_STATE = """
import json
print(json.load(open("/work/state.json")))
"""

OPEN = (
    Step(calls=(("run_experiment", plain_run(300.0)),)),
    Step(calls=(("python", {"code": READ_DATA}),)),
    Step(calls=(("python", {"code": READ_STATE}),)),
    Step(
        calls=(
            ("notebook", {"append": "e0 looks clustered"}),
            (
                "fit",
                {
                    "structure": "Excite(ExpK, Mark(size), all)",
                    "data_ids": ["obs", "e0"],
                },
            ),
        )
    ),
    Step(calls=(("predict", PREDICT_E1),)),
    Step(calls=(("run_experiment", plain_run(300.0)),)),
    Step(
        calls=(
            (
                "submit",
                {"structure": "Excite(ExpK, Mark(size), all)", "report": "done"},
            ),
        )
    ),
)


def _forged(record: SessionRecord, call: int, text: str) -> SessionRecord:
    """A consistently re-chained copy in which one tool result's text (and its
    digest) were changed: every hash verifies, so only replay can tell."""
    forged = SessionRecord()
    for entry in record.entries:
        body = json.loads(json.dumps(entry.body))
        if entry.kind == "tool_call" and body["call"] == call:
            body["text"] = text
            body["digest"] = digest(
                {"text": text, "is_error": body["is_error"], "record": body["record"]}
            )
        forged.append(entry.kind, body)
    return forged


def _call_index(record: SessionRecord, name: str) -> int:
    return int(
        next(
            e.body["call"]
            for e in record.of_kind("tool_call")
            if e.body["name"] == name
        )
    )


def test_constrained_run_replays_and_detects_a_perturbed_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    first = scripted_run(CONSTRAINED, tmp_path / "a")
    second = scripted_run(CONSTRAINED, tmp_path / "b")
    assert first.outcome == "submitted"
    assert first.head == second.head, "same seed and script must record the same run"
    assert first.experiments_used == 2 and first.fits_used == 2
    assert [p.covered for p in first.predictions] == [True]
    assert not first.void

    record = SessionRecord.load(first.record_path)
    report = replay_investigation(record, POINTPROC)
    assert report.steps == 7
    assert report.budgets_used == {"experiments": 2, "fits": 2, "submit": 1}
    native = submitted_structure(record, POINTPROC.resolve("hawkes").channels)
    assert native == first.submission

    fit_call = _call_index(record, "fit")
    original = next(
        e.body["text"]
        for e in record.of_kind("tool_call")
        if e.body["call"] == fit_call
    )
    perturbed = original.replace("certified: yes", "certified: NO")
    assert perturbed != original
    with pytest.raises(ReplayDivergenceError) as caught:
        replay_investigation(_forged(record, fit_call, perturbed), POINTPROC)
    assert caught.value.step == fit_call
    assert caught.value.field == "digest"


@pytest.mark.slow
def test_open_run_replays_and_detects_a_perturbed_sandbox_result(
    sandbox_image: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    outcome = scripted_run(
        OPEN, tmp_path / "out", arm="AG-o", sandbox_root=tmp_path / "sbx"
    )
    assert outcome.outcome == "submitted", outcome
    assert not outcome.void, outcome.void_reasons
    record = SessionRecord.load(outcome.record_path)
    calls = {e.body["call"]: e.body for e in record.of_kind("tool_call")}
    assert not any(b["is_error"] for b in calls.values()), [
        b["text"] for b in calls.values() if b["is_error"]
    ]
    python_calls = [b for b in calls.values() if b["name"] == "python"]
    assert len(python_calls) == 2
    first_stdout = python_calls[0]["text"]
    assert first_stdout.startswith("exit code 0")
    assert "endogenous" in first_stdout and "size" in first_stdout
    # State persisted through /work between two stateless calls.
    assert "'n':" in python_calls[1]["text"]
    assert python_calls[0]["record"]["image"] == sandbox_image

    report = replay_investigation(record, POINTPROC, sandbox_root=tmp_path / "re")
    assert report.steps == len(calls)

    step = python_calls[0]["call"]
    perturbed = first_stdout.replace("exit code 0", "exit code 0 ", 1)
    with pytest.raises(ReplayDivergenceError) as caught:
        replay_investigation(
            _forged(record, step, perturbed), POINTPROC, sandbox_root=tmp_path / "f"
        )
    assert caught.value.step == step
    assert caught.value.field == "digest"
