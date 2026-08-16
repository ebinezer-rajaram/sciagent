"""How an empirical table reaches disk, and what a concurrent reader can see.

These are not acceptance criteria. They cover the persistence guarantees
:meth:`EmpiricalTable.save` makes, which became load-bearing when worktrees
started sharing one ``.cache/tables``: two trees can then reach the same
content-addressed path at once, so a write that truncates first, or that leaves
a temporary file behind, is a defect other trees observe.

Deliberately deterministic rather than a real race. Driving two processes at the
same path does reproduce the failure -- measured at 2000 reads with 0 partial
before the fix, and one writer killed by ``PermissionError`` (WinError 5) after
it -- but as a suite test that is slow and intermittent, and it would assert the
absence of a symptom rather than the presence of the mechanism. Patching
``os.replace`` tests the mechanism itself, in milliseconds, with no flake.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from slice_tables import slice_table

from sciagent.core.errors import TableError
from sciagent.inference import empirical

if TYPE_CHECKING:
    from sciagent.inference.empirical import EmpiricalTable

#: What ``os.replace`` accepts, spelled out rather than silenced. The stdlib's
#: own alias for this lives in ``_typeshed`` and does not exist at runtime, and
#: CLAUDE.md rules out reaching for ``# type: ignore`` to paper over the gap.
StrPath = str | os.PathLike[str]


@pytest.fixture(scope="module")
def table() -> EmpiricalTable:
    """The slice table, which the rest of the suite has usually already built."""
    return slice_table()


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the give-up path fast; the wait is not what is under test."""
    monkeypatch.setattr(empirical, "_REPLACE_BACKOFF_S", 0.0)


def _temps(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.tmp"))


def test_a_saved_table_leaves_no_temporary_file(
    table: EmpiricalTable, tmp_path: Path
) -> None:
    """The temporary file is an implementation detail, not an artefact.

    It is written into the destination directory, because os.replace is only
    atomic within a filesystem. That directory is now shared between worktrees,
    so anything left there is left in everybody's cache.
    """
    dest = tmp_path / "table.json"
    table.save(dest)

    assert dest.exists()
    assert _temps(tmp_path) == [], "save left a temporary file in the cache directory"


def test_a_transient_replace_failure_is_retried(
    table: EmpiricalTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses MoveFileEx while a reader holds the destination open.

    That is transient -- the reader closes -- so giving up on the first refusal
    would make a shared cache flaky rather than merely contended.
    """
    dest = tmp_path / "table.json"
    real_replace = os.replace
    calls = 0

    def flaky(src: StrPath, dst: StrPath) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    table.save(dest)

    assert calls == 3, "save did not retry a transient PermissionError"
    assert json.loads(dest.read_text(encoding="utf-8"))["counts"]
    assert _temps(tmp_path) == []


def test_giving_up_raises_a_typed_error_and_removes_the_temporary(
    table: EmpiricalTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanent holder must fail loudly, not hang and not leak."""
    dest = tmp_path / "table.json"

    def always_denied(src: object, dst: object) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", always_denied)

    with pytest.raises(TableError, match="holding it open"):
        table.save(dest)

    assert _temps(tmp_path) == [], "the temporary file survived a failed save"
    assert not dest.exists()


def test_a_failed_save_leaves_an_existing_table_intact(
    table: EmpiricalTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atomicity property, without needing a second process to observe it.

    This is what a plain ``write_text`` could not offer: it truncates the
    destination before writing, so a failure part-way through leaves a reader
    with a prefix. Replacing means the previous table stays readable until the
    new one is complete, and a failed save is invisible rather than destructive.
    """
    dest = tmp_path / "table.json"
    table.save(dest)
    before = dest.read_bytes()

    def always_denied(src: object, dst: object) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", always_denied)
    with pytest.raises(TableError):
        table.save(dest)

    assert dest.read_bytes() == before, (
        "a failed save damaged the table already on disk"
    )
    assert json.loads(dest.read_text(encoding="utf-8"))["counts"]


def test_a_transient_read_failure_is_retried(
    table: EmpiricalTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader's half of the same race, which was once assumed not to exist.

    ``save``'s docstring concluded from 2000 clean reads that readers are never
    wrong. That held for two readers; it does not hold for the process count a
    ``-n 4`` suite plus four agents reaches, where a reader died with errno 13 on
    the gate table. Windows refuses an ``open`` for the window in which
    ``os.replace`` holds the destination, so the reader has to wait exactly as
    the writer does.
    """
    dest = tmp_path / "table.json"
    table.save(dest)
    real_read_text = Path.read_text
    calls = 0

    def flaky(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", flaky)
    recovered = empirical._read_text_contended(dest)

    assert calls == 3, "the read did not retry a transient PermissionError"
    assert json.loads(recovered)["counts"]


def test_a_read_that_never_succeeds_raises_a_typed_error(
    table: EmpiricalTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanent holder must surface as a TableError naming the cause.

    Not as the bare ``PermissionError`` the filesystem raises: that reaches the
    caller as an unhandled OS error indistinguishable from a missing file or a
    permissions misconfiguration, and sends the reader looking in the wrong place.
    """
    dest = tmp_path / "table.json"
    table.save(dest)

    def always_denied(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", always_denied)

    with pytest.raises(TableError, match="holding it open"):
        empirical._read_text_contended(dest)


def test_load_goes_through_the_guarded_read(
    table: EmpiricalTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is worth nothing if the one caller that matters bypasses it.

    ``load`` is the only reader of a cached table, and it read with a bare
    ``read_text`` until a contended suite run caught it. This is the test that
    fails if that bypass ever returns.
    """
    dest = tmp_path / "table.json"
    table.save(dest)
    calls = 0
    real_read_text = Path.read_text

    def flaky(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", flaky)
    reloaded = empirical.EmpiricalTable.load(dest, list(table.templates.values()))

    assert calls == 2, "load did not retry, so it is not using the guarded read"
    assert reloaded.version == table.version
