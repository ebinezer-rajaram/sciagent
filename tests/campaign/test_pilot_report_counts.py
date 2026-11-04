"""The report's LLM cost table never reads an unrecorded count as zero.

The SDK's ``result`` message carries a session's turn count and token usage.
A session that ends without one (a ``wall_time`` end, LOG 2026-10-29) has no
``result`` entry in its record, and its session cell reads turns and tokens as
0 (``campaign.llm.run_session``: ``num_turns or 0``, ``_usage(None)``). The
cost table takes those medians over the sessions whose stored record has a
``result`` entry and says how many it has, as ``timed_text`` does for wall
times. Whether a count was recorded is read from the record, never from the
outcome's name or from the value being zero. A store whose every session has a
``result`` entry renders as before.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from audit_support import put_session
from campaign_support import entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign.analysis import Session, Table
from sciagent.campaign.llm import run_session
from sciagent.campaign.plan import Unit
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import ScriptedDriver, Step

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
ARM = "AG-o-S/named"
OTHER = "AG-o-S/anon"


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` loaded in-process, with ``scripts/`` importable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "pilot_report", SCRIPTS / "pilot_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _put(
    store: ResultStore,
    system: str,
    seed: int,
    outcome: str,
    counts: dict[str, float],
    *,
    result: bool,
) -> Session:
    """A session cell reading ``counts`` and its record: assistant turns, then
    a ``result`` entry only if ``result``."""
    key = put_session(
        store,
        system,
        seed=seed,
        make_record=False,
        calls=(),
        reading=counts,
        session={"outcome": outcome},
    )
    cell = store.cell(str(key.digest))
    detail = cell["detail"]["session"]
    record = SessionRecord()
    record.append("config", {"budgets": {}})
    for i in range(3):
        record.append("assistant", {"turn": i})
    if result:
        record.append(
            "result",
            {
                "num_turns": int(counts["turns"]),
                "usage": {"output_tokens": int(counts["output_tokens"])},
            },
        )
    record.save(store.root / detail["record_dir"] / detail["record"])
    return Session(
        "dev-008",
        seed,
        system,
        {k: float.fromhex(v) for k, v in cell["reading"].items()},
        detail,
        VoidStatus(False, (), 0, False),
        str(cell["key"]["env_version"]),
        str(key.digest),
    )


def table_of(sessions: Sequence[Session]) -> Table:
    """A report table holding ``sessions`` and nothing else."""
    return Table({}, (), tuple(sessions), {})


def _counts(turns: float, out: float, inp: float, cache: float) -> dict[str, float]:
    return {
        "turns": turns,
        "output_tokens": out,
        "input_tokens": inp,
        "cache_read_input_tokens": cache,
    }


def _rows(lines: list[str]) -> dict[str, dict[str, str]]:
    """The cost table's rows by arm, each by column header."""
    cells = [
        [c.strip() for c in line.strip("|").split("|")]
        for line in lines
        if line.startswith("| ")
    ]
    header, *body = cells
    return {r[0]: dict(zip(header, r, strict=True)) for r in body}


def test_a_session_without_a_result_entry_is_not_counted_as_zero(
    tmp_path: Path, report: ModuleType
) -> None:
    with ResultStore(tmp_path) as store:
        sessions = [
            _put(store, ARM, 1, "submitted", _counts(31, 5000, 120, 3000), result=True),
            # What run_session writes for a wall-time end: zeros, no result.
            _put(store, ARM, 2, "wall_time", _counts(0, 0, 0, 0), result=False),
            _put(store, OTHER, 1, "wall_time", _counts(0, 0, 0, 0), result=False),
        ]
    lines = report.cost_table(tmp_path, [ARM, OTHER], table_of(sessions))
    rows = _rows(lines)
    arm = rows[ARM]
    assert arm["sessions"] == "2"
    assert arm["turns"] == "31 (1/2 recorded)", arm
    assert arm["out tokens"] == "5000 (1/2 recorded)", arm
    assert arm["in tokens"] == "120 (1/2 recorded)", arm
    assert arm["cache read"] == "3000 (1/2 recorded)", arm
    # Counts read from the record's tool calls are not the SDK's: unchanged.
    assert arm["tool calls"] == "0", arm
    other = rows[OTHER]
    for column in ("turns", "out tokens", "in tokens", "cache read"):
        assert other[column] == "- (0/1 recorded)", other
    assert any("no `result` message" in line for line in lines), lines


def test_recorded_is_read_from_the_record_not_the_outcome_or_a_zero(
    tmp_path: Path, report: ModuleType
) -> None:
    """A wall-time session whose record has a ``result`` entry reading zero
    turns is counted, and the rendering is the one from before this change."""
    with ResultStore(tmp_path) as store:
        sessions = [
            _put(store, ARM, 1, "submitted", _counts(30, 4000, 100, 2000), result=True),
            _put(store, ARM, 2, "wall_time", _counts(0, 0, 0, 0), result=True),
        ]
    lines: list[Any] = report.cost_table(tmp_path, [ARM], table_of(sessions))
    assert lines[2] == (
        "| AG-o-S/named | 2 | submitted 1, wall_time 1 | 15 | 0 | 2000 | 50 | 1000 "
        "| - (0/2 timed) | - (0/2 timed) | - (0/2 timed) | 0 | 0 | 100% | 0% "
        "| 0 | 0 | 0 | 0/0 | 0 (0) |"
    ), lines
    assert not any("recorded" in line for line in lines), lines


def test_both_forms_of_a_session_without_a_result_entry_read_the_same(
    tmp_path: Path, report: ModuleType
) -> None:
    """The pilot's cells hold 0 where there is no ``result``; ``run_session``
    now leaves the fields out. Neither counts as a value, neither is left out
    twice, and a store holding both forms says so once per session."""

    def table_with(root: Path, *cuts: dict[str, float]) -> list[str]:
        with ResultStore(root) as store:
            sessions = [
                _put(
                    store,
                    ARM,
                    1,
                    "submitted",
                    _counts(31, 5000, 120, 3000),
                    result=True,
                ),
                *(
                    _put(store, ARM, 2 + i, "wall_time", cut, result=False)
                    for i, cut in enumerate(cuts)
                ),
            ]
        return list(report.cost_table(root, [ARM], table_of(sessions)))

    zeros = _counts(0, 0, 0, 0)
    stored = table_with(tmp_path / "stored", zeros)
    left_out = table_with(tmp_path / "left-out", {})
    assert stored == left_out
    both = table_with(tmp_path / "both", zeros, {})
    arm = _rows(both)[ARM]
    assert arm["sessions"] == "3"
    for column, value in (
        ("turns", "31"),
        ("out tokens", "5000"),
        ("in tokens", "120"),
        ("cache read", "3000"),
    ):
        assert arm[column] == f"{value} (1/3 recorded)", arm
    notes = [line for line in both if "no `result` message" in line]
    assert len(notes) == 1 and notes[0].startswith("Turns and tokens: 2 session(s)")


def test_what_run_session_writes_is_counted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: ModuleType
) -> None:
    """The reading and the table are two halves of one rule: the reading of a
    real cut session has no counts, and its real record says none were
    recorded; a real finished session has both."""
    clean(monkeypatch)
    unit = Unit("size_excitation", 1, "AG-c-H/named")

    def run(name: str, driver: ScriptedDriver, wall_time_s: float) -> Session:
        out = run_session(
            unit,
            entry("size_excitation"),
            environment(),
            tiny_config(seeds=(1,), wall_time_s=wall_time_s),
            tmp_path / "llm" / name,
            driver=driver,
        )
        detail = {**out.detail, "record_dir": f"llm/{name}"}
        void = VoidStatus(False, (), 0, False)
        return Session(
            unit.truth_id, 1, unit.system, out.reading, detail, void, "env", name
        )

    done = run("done", ScriptedDriver(SCRIPT), 60.0)
    cut = run("cut", ScriptedDriver((Step(text="thinking", delay_s=30.0),)), 0.2)
    assert done.detail["outcome"] == "submitted"
    assert cut.detail["outcome"] == "wall_time"
    assert report.counts_recorded(tmp_path, done)
    assert not report.counts_recorded(tmp_path, cut)
    lines = report.cost_table(tmp_path, [unit.system], table_of([done, cut]))
    arm = _rows(lines)[unit.system]
    assert arm["turns"] == f"{done.reading['turns']:.0f} (1/2 recorded)", arm
    assert arm["out tokens"] == (
        f"{done.reading['output_tokens']:.0f} (1/2 recorded)"
    ), arm
    assert sum("no `result` message" in line for line in lines) == 1


def test_a_policy_arm_row_leaves_its_predictions_out(
    tmp_path: Path, report: ModuleType
) -> None:
    """P4 Δ11: the prediction columns read only sessions whose agent chose the
    experiment; the last column counts what was left out."""
    with ResultStore(tmp_path) as store:
        counts = {
            **_counts(10, 100, 10, 10),
            "predictions": 3.0,
            "predictions_evaluated": 2.0,
            "predictions_covered": 1.0,
        }
        plain = _put(store, ARM, 1, "submitted", counts, result=True)
        policy = replace(
            _put(store, "AG-c-S+EIG/named", 1, "submitted", counts, result=True),
            policy="eig",
        )
    rows = _rows(
        report.cost_table(tmp_path, [ARM, policy.system], table_of([plain, policy]))
    )
    last = "policy (excl.): sessions (predictions)"
    assert rows[ARM]["any prediction"] == "100%"
    assert rows[ARM]["predictions covered/evaluated"] == "1/2"
    assert rows[ARM][last] == "0 (0)"
    assert rows[policy.system]["any prediction"] == "excl."
    assert rows[policy.system]["predictions covered/evaluated"] == "excl."
    assert rows[policy.system][last] == "1 (3)"
    assert rows[policy.system]["turns"] == "10"
