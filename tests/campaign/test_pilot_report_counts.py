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
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from audit_support import put_session

from sciagent.campaign.analysis import Session
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus
from sciagent.harness.record import SessionRecord

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
    lines = report.cost_table(tmp_path, [ARM, OTHER], sessions)
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
    lines: list[Any] = report.cost_table(tmp_path, [ARM], sessions)
    assert lines[2] == (
        "| AG-o-S/named | 2 | submitted 1, wall_time 1 | 15 | 0 | 2000 | 50 | 1000 "
        "| - (0/2 timed) | - (0/2 timed) | - (0/2 timed) | 0 | 0 | 100% | 0% "
        "| 0 | 0 | 0 | 0/0 |"
    ), lines
    assert not any("recorded" in line for line in lines), lines
