"""Reading a store never changes it: the report and the audit checks are pure
readers (CLAUDE.md invariant 4; hunt B's "report on a copy", STATUS-2026-10-05).

The instrument is :func:`snapshot`: every path under the store root, with the
sha256 of each file (``ledger.sqlite``, the details, ``timing.jsonl``, records,
lock and fingerprint files alike). A reader passes when the snapshot after it
equals the snapshot before it. The control shows the snapshot sees an appended
timing line and the schema repair a read-write open makes on a ledger that
lost a trigger; that repair is why the readers' test stores lack one: it is the
store an audit must report as found, not mend.

Found here (LOG 2026-10-28): every reader opened the store read-write
(``ResultStore(root)``), which runs the ledger's ``CREATE ... IF NOT EXISTS``
schema. On an intact store that leaves the bytes alone, but on a ledger that
lost a trigger the report, ``store_check`` and ``session_check`` were measured
putting it back (so a second ``store_check`` would pass), and a report on a
mistyped ``--root`` would create an empty store (read from the code: the
read-write open makes the directory and the ledger). Readers now open with
``read_only=True``.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from audit_support import clean_store, put_baseline
from test_audit_scripts import SCRIPTS, SPLIT, load

from sciagent.campaign.audit import check_budgets, check_store
from sciagent.campaign.store import LEDGER_FILE, ResultStore
from sciagent.core.errors import RegistryError
from sciagent.registry.ledger import CampaignLedger
from sciagent.registry.store import ExperimentKey

TRIGGER = "cells_no_update"


def snapshot(root: Path) -> dict[str, str]:
    """Every path under ``root``: ``dir`` for a directory, else its sha256."""
    return {
        p.relative_to(root).as_posix(): (
            "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
        )
        for p in sorted(root.rglob("*"))
    }


def without_trigger(root: Path) -> Path:
    """``root``'s ledger with one append-only trigger dropped by a foreign
    connection (as ``test_audit``'s dropped-trigger finding)."""
    con = sqlite3.connect(root / LEDGER_FILE)
    with con:
        con.execute(f"DROP TRIGGER {TRIGGER}")
    con.close()
    return root


def _triggers(root: Path) -> list[str]:
    con = sqlite3.connect(
        f"{(root / LEDGER_FILE).resolve().as_uri()}?mode=ro", uri=True
    )
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return [str(name) for (name,) in rows]


def test_the_snapshot_sees_a_new_timing_line_and_a_repaired_trigger(
    tmp_path: Path,
) -> None:
    root = without_trigger(clean_store(tmp_path / "s"))
    before = snapshot(root)
    assert "ledger.sqlite" in before and "timing.jsonl" in before
    assert TRIGGER not in _triggers(root)
    with ResultStore(root) as store:  # read-write: the writer's open
        pass
    assert TRIGGER in _triggers(root)
    repaired = snapshot(root)
    assert repaired["ledger.sqlite"] != before["ledger.sqlite"]
    with ResultStore(root) as store:
        store.log_timing({"kind": "control"})
    assert snapshot(root)["timing.jsonl"] != repaired["timing.jsonl"]


def test_a_read_only_store_reads_and_never_writes(tmp_path: Path) -> None:
    """Every read works; every write is refused before it touches a file (a
    refused ``put`` must not leave its detail file behind either)."""
    root = without_trigger(clean_store(tmp_path / "s"))
    before = snapshot(root)
    with ResultStore(root, read_only=True) as store:
        entries = store.ledger.entries()
        assert len(store.cells()) == len(entries) == 8
        assert store.done(entries[0].key)
        fresh = _absent_key(tmp_path / "other")
        for write in (
            lambda: store.put(fresh, {"status": 1.0}, {}),
            lambda: store.log_timing({"kind": "nope"}),
            store.recover,
        ):
            with pytest.raises(RegistryError, match="read-only"):
                write()
    assert snapshot(root) == before


def _absent_key(other: Path) -> ExperimentKey:
    """The key of a cell committed to another store, absent from the test's."""
    with ResultStore(other) as store:
        return put_baseline(store, "B-np", -0.9, truth="dev-013")


def test_a_read_only_ledger_refuses_an_append_at_the_sqlite_level(
    tmp_path: Path,
) -> None:
    """Under :class:`ResultStore`'s own refusal, the ledger's connection
    cannot write either (``mode=ro``)."""
    root = without_trigger(clean_store(tmp_path / "s"))
    before = snapshot(root)
    fresh = _absent_key(tmp_path / "other")
    with CampaignLedger.open_read_only(root / LEDGER_FILE) as ledger:
        assert ledger.count() == 8
        with pytest.raises(RegistryError, match="readonly"):
            ledger.append(fresh, reading={"status": 1.0})
    assert snapshot(root) == before
    with pytest.raises(RegistryError, match="no ledger"):
        CampaignLedger.open_read_only(tmp_path / "missing" / LEDGER_FILE)
    assert not (tmp_path / "missing").exists()


def test_a_read_only_store_is_never_created(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="not a campaign store"):
        ResultStore(tmp_path / "missing", read_only=True)
    assert not (tmp_path / "missing").exists()
    (tmp_path / "empty").mkdir()
    with pytest.raises(RegistryError, match="not a campaign store"):
        ResultStore(tmp_path / "empty", read_only=True)
    assert snapshot(tmp_path / "empty") == {}


def test_the_store_checks_leave_the_store_unchanged(tmp_path: Path) -> None:
    """``check_store`` reports a dropped trigger and leaves it dropped: a
    second check finds it again (a read-write open would have mended it)."""
    root = without_trigger(clean_store(tmp_path / "s"))
    before = snapshot(root)
    for _ in range(2):
        found = [(f.kind, f.where) for f in check_store(root).issues]
        assert found == [("missing_trigger", TRIGGER)]
        assert snapshot(root) == before
    assert check_budgets(root).passed
    assert snapshot(root) == before


def _render(root: Path, out: Path) -> subprocess.CompletedProcess[str]:
    """``pilot_report.py`` on ``root`` in a fresh process (as it is run)."""
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "pilot_report.py"),
            "--root",
            str(root),
            "--split",
            str(SPLIT),
            "--ids",
            "dev-008",
            "--seeds",
            "1,2",
            "--resamples",
            "50",
            "--out",
            str(out),
        ],
        env={**os.environ, "PYTHONHASHSEED": "0"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.slow
def test_rendering_the_pilot_report_leaves_every_store_file_unchanged(
    tmp_path: Path,
) -> None:
    whole = clean_store(tmp_path / "whole")
    damaged = without_trigger(clean_store(tmp_path / "damaged"))
    for root in (whole, damaged):
        before = snapshot(root)
        out = tmp_path / f"{root.name}.md"
        done = _render(root, out)
        assert done.returncode == 0, done.stdout + done.stderr[-4000:]
        assert out.read_text(encoding="utf-8").startswith("# P3 pilot report")
        assert snapshot(root) == before, root.name
    assert TRIGGER not in _triggers(damaged)
    missing = tmp_path / "missing"
    done = _render(missing, tmp_path / "missing.md")
    assert done.returncode == 2, done.stdout + done.stderr[-4000:]
    assert "not a campaign store" in done.stderr
    assert not missing.exists() and not (tmp_path / "missing.md").exists()


@pytest.mark.slow
def test_session_check_and_hunt_gap_leave_the_store_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    root = without_trigger(clean_store(tmp_path / "s"))
    before = snapshot(root)
    calls = (
        ("session_check.py", ["--root", root, "--split", SPLIT, "--ids", "dev-008"]),
        ("hunt_gap.py", ["--store", root, "--env", "fit/5", "--split", SPLIT]),
    )
    for script, args in calls:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = load(script).main([str(a) for a in args])
        assert code in (0, 1), (script, code, sink.getvalue())
        assert snapshot(root) == before, script
