"""The report counts the sessions shown the softplus note, per arm, from the records.

LOG 2026-11-05 (session 34): the note reached 60 of 120 anonymised sessions and no
named one. It stays, because softplus θ cannot be converted to the agent's units,
so C2 compares conditions that include it and the report says how many read it.

Only a ``fit`` result counts. Those are what the framework showed, and an AG-o
agent can put the sentence into a ``notebook`` or a ``python`` output, where the
record's text would hold it though the framework never said it. A refused fit
holds no note; a free repeat repeats the fit's text, so it was shown.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from campaign_support import environment

from sciagent.campaign.analysis import Session, Table
from sciagent.campaign.plan import CampaignError
from sciagent.campaign.voids import VoidStatus
from sciagent.harness.record import SessionRecord
from sciagent.investigation import InvestigationSpec, build_lab

SCRIPTS: Final = Path(__file__).resolve().parents[2] / "scripts"
ANON: Final = "AG-c-S/anon"
NAMED: Final = "AG-c-S/named"
OPEN: Final = "AG-o-S/anon"
NOTE: Final = (
    "note: softplus-link coefficients are reported as fitted, not converted to "
    "your time units"
)
FITTED: Final = f"fit 1 (1 of 40 fits used)\ntheta0 (intercept) = 0.1\n{NOTE}\nbudget"
PLAIN: Final = "fit 1 (1 of 40 fits used)\ntheta0 (intercept) = 0.1\nbudget"


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


def _call(name: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"name": name, "text": text, "is_error": is_error}


def _session(
    root: Path,
    system: str,
    seed: int,
    calls: Sequence[dict[str, Any]],
    *,
    void: bool = False,
) -> Session:
    """A session and its toy record: only ``tool_call`` entries are read."""
    directory = f"llm/{system.replace('/', '-')}-{seed}"
    record = SessionRecord()
    record.append("config", {"budgets": {}})
    for call in calls:
        record.append("tool_call", call)
    (root / directory).mkdir(parents=True)
    record.save(root / directory / "record.json")
    return Session(
        "dev-008",
        seed,
        system,
        {},
        {"record_dir": directory, "record": "record.json"},
        VoidStatus(void, ("x",) if void else (), 0, void),
        "env",
        f"{seed:064x}",
    )


def _table(sessions: Sequence[Session]) -> Table:
    return Table({}, (), tuple(sessions), {})


def _rows(lines: list[str]) -> dict[str, dict[str, str]]:
    cells = [
        [c.strip() for c in line.strip("|").split("|")]
        for line in lines
        if line.startswith("| ")
    ]
    header, *body = cells
    return {r[0]: dict(zip(header, r, strict=True)) for r in body}


def test_a_session_is_counted_once_however_often_it_read_the_note(
    tmp_path: Path, report: ModuleType
) -> None:
    sessions = [
        _session(tmp_path, ANON, 1, [_call("fit", FITTED), _call("fit", FITTED)]),
        _session(tmp_path, ANON, 2, [_call("fit", PLAIN)]),
        _session(tmp_path, ANON, 3, []),
    ]
    rows = _rows(report.softplus_note_table(tmp_path, [ANON], _table(sessions)))
    assert rows[ANON]["sessions"] == "3"
    assert rows[ANON]["shown the note"] == "1"


def test_the_count_is_per_arm_and_an_arm_at_zero_has_its_row(
    tmp_path: Path, report: ModuleType
) -> None:
    sessions = [
        _session(tmp_path, ANON, 1, [_call("fit", FITTED)]),
        _session(tmp_path, ANON, 2, [_call("fit", FITTED)]),
        _session(tmp_path, OPEN, 1, [_call("fit", FITTED)]),
        _session(tmp_path, OPEN, 2, [_call("fit", PLAIN)]),
        _session(tmp_path, NAMED, 1, [_call("fit", PLAIN)]),
    ]
    rows = _rows(
        report.softplus_note_table(tmp_path, [NAMED, ANON, OPEN], _table(sessions))
    )
    assert rows[ANON]["shown the note"] == "2"
    assert rows[OPEN]["shown the note"] == "1"
    assert rows[NAMED]["shown the note"] == "0"
    assert rows[NAMED]["sessions"] == "1"


def test_an_arm_with_no_sessions_has_no_row(tmp_path: Path, report: ModuleType) -> None:
    sessions = [_session(tmp_path, ANON, 1, [_call("fit", FITTED)])]
    rows = _rows(report.softplus_note_table(tmp_path, [NAMED, ANON], _table(sessions)))
    assert set(rows) == {ANON}


@pytest.mark.parametrize("tool", ["notebook", "python", "belief", "submit"])
def test_the_sentence_outside_a_fit_is_not_the_framework_showing_it(
    tmp_path: Path, report: ModuleType, tool: str
) -> None:
    sessions = [_session(tmp_path, OPEN, 1, [_call(tool, f"[1] {NOTE}")])]
    rows = _rows(report.softplus_note_table(tmp_path, [OPEN], _table(sessions)))
    assert rows[OPEN]["shown the note"] == "0"


def test_a_free_repeat_shows_the_note_and_a_refusal_does_not(
    tmp_path: Path, report: ModuleType
) -> None:
    repeat = f"not charged: this structure was already fitted\n{FITTED}"
    refusal = "fit: LastMarkAbove on a binary channel"
    sessions = [
        _session(tmp_path, ANON, 1, [_call("fit", repeat, is_error=True)]),
        _session(tmp_path, ANON, 2, [_call("fit", refusal, is_error=True)]),
    ]
    rows = _rows(report.softplus_note_table(tmp_path, [ANON], _table(sessions)))
    assert rows[ANON]["shown the note"] == "1"


def test_void_sessions_are_counted_and_said_apart_from_the_scored_ones(
    tmp_path: Path, report: ModuleType
) -> None:
    """C2 reads the scored sessions, so the table gives both."""
    sessions = [
        _session(tmp_path, ANON, 1, [_call("fit", FITTED)]),
        _session(tmp_path, ANON, 2, [_call("fit", FITTED)], void=True),
        _session(tmp_path, ANON, 3, [_call("fit", PLAIN)], void=True),
    ]
    row = _rows(report.softplus_note_table(tmp_path, [ANON], _table(sessions)))[ANON]
    assert row["sessions"] == "3"
    assert row["shown the note"] == "2"
    assert row["scored sessions"] == "1"
    assert row["scored, shown the note"] == "1"


def test_a_session_with_no_record_is_an_error_not_a_session_without_the_note(
    tmp_path: Path, report: ModuleType
) -> None:
    session = Session(
        "dev-008",
        1,
        ANON,
        {},
        {},
        VoidStatus(False, (), 0, False),
        "env",
        "0" * 64,
    )
    with pytest.raises(CampaignError, match="names no record"):
        report.softplus_note_table(tmp_path, [ANON], _table([session]))


def test_the_table_says_what_it_counts(tmp_path: Path, report: ModuleType) -> None:
    sessions = [_session(tmp_path, ANON, 1, [_call("fit", FITTED)])]
    text = "\n".join(report.softplus_note_table(tmp_path, [ANON], _table(sessions)))
    assert "softplus-link coefficients are reported as fitted" in text
    assert "C2" in text


def test_the_table_says_which_c2_reads_hold_the_note(
    tmp_path: Path, report: ModuleType
) -> None:
    """Rescaled shows the note too, so C2's time read holds it and its names read
    does not (LOG 2026-11-06)."""
    sessions = [_session(tmp_path, ANON, 1, [_call("fit", FITTED)])]
    text = "\n".join(report.softplus_note_table(tmp_path, [ANON], _table(sessions)))
    assert "the rescaled and anonymised conditions are told" in text
    assert "C2's time reads include this note and its names reads do not" in text


@pytest.mark.parametrize(
    ("condition", "shown"), [("anon", True), ("rescaled", True), ("named", False)]
)
def test_the_needle_is_what_a_real_softplus_fit_prints(
    condition: Any, shown: bool, report: ModuleType
) -> None:
    """Positive control: the report's literal against the framework's own text, so
    a change of wording in ``tools.py`` cannot leave the count silently at zero."""
    env = environment()
    assert env.investigation is not None
    spec = InvestigationSpec(env.name, "hawkes", "AG-c", condition, 1, 3, 4)
    layer = build_lab(spec, env.investigation).layer()
    out = layer.call("fit", {"structure": "link=softplus; Trend", "data_ids": ["obs"]})
    assert not out.is_error, out.text
    assert (report.SOFTPLUS_NOTE in out.text) is shown
