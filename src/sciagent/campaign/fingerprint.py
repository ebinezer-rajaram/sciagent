"""The store's code fingerprint: no silent reuse of cells across code changes.

A store is resumable and ``ResultStore.done`` is ledger membership, so a
resume after a code change would reuse every stored cell whose hand-written
address did not move (LOG 2026-10-27, F5). The guard makes that reuse a
recorded decision.

The store keeps ``fingerprint.jsonl`` beside its cells, never in them, so cell
bytes and :func:`~sciagent.campaign.audit.compare_stores` are unaffected. It is
append-only (invariant 4), one canonical JSON object per ``\\n``-terminated
line:

- ``{"event": "establish", "fingerprint": F}``: the first line of a store
  created under the guard;
- ``{"event": "reuse", "fingerprint": G, "from": F}``: an explicit permission
  to keep the store's cells, written under ``F``, while writing under ``G``.
  ``F`` is the fingerprint the store held at that point (the previous line's),
  or :data:`UNRECORDED` as the first line of a store whose cells predate the
  guard.

The store's fingerprint is the last committed line's. :func:`guard_store` runs
before a campaign opens its store and before any unit is launched:

- no sidecar and no stored content: the fingerprint is established;
- the same fingerprint: nothing is written;
- a different one: refused, unless ``reuse_from`` names the store's
  fingerprint exactly; the permission is then appended;
- a store with content but no sidecar: refused, unless ``reuse_from`` is
  :data:`UNRECORDED`, which says, on the record, that the code that wrote the
  existing cells is unknown. A commit cannot be named for it: nothing in the
  store shows which commit wrote it.

A ``reuse_from`` that does not name the store's fingerprint is refused even
when the fingerprints match: it means the caller's picture of the store is
wrong. A fingerprint is printable ASCII without spaces, and never
:data:`UNRECORDED`.

**One driver per store.** :class:`StoreLock` is an exclusive, non-blocking
``flock`` on ``fingerprint.lock`` in the root. A campaign takes it before the
guard and holds it until it is closed, so no second driver can append a
competing permission or keep writing cells after another fingerprint is
recorded; a second driver is refused, not queued. (Pool workers and LLM
sessions return results to the campaign's main thread, the store's only
writer, so the lock covers every cell write.)

**Crash safety.** A line counts once its ``\\n`` is on disk. The first line is
written to a temporary file, synced and hard-linked into place (then the
directory is synced), so a sidecar is never seen half-created; appends are
synced. A final line without its ``\\n`` (a process killed mid-append) is
uncommitted: it is ignored, and cut off under the lock before the next append.
A sidecar with no committed line is treated as absent.

The fingerprint is opaque here: ``sciagent`` runs no git and reads no lock
file. The caller (``scripts/pilot.py``) computes it and refuses a dirty tree.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from types import TracebackType
from typing import Final, Literal

from sciagent.campaign.plan import CampaignError
from sciagent.campaign.store import DETAILS_DIR, LEDGER_FILE
from sciagent.scenarios.records import canonical_json

FINGERPRINT_FILE: Final = "fingerprint.jsonl"
LOCK_FILE: Final = "fingerprint.lock"
#: ``reuse_from`` for a store whose cells predate the guard.
UNRECORDED: Final = "unrecorded"
#: The driver's directory of LLM session records (``driver.LLM_DIR``, which
#: imports this module; a test keeps the two equal).
LLM_DIR: Final = "llm"

type Event = Literal["establish", "reuse"]


class CodeFingerprintError(CampaignError):
    """The store's code fingerprint forbids this run, or its sidecar is malformed."""


@dataclass(frozen=True)
class FingerprintEntry:
    """One sidecar line."""

    event: Event
    fingerprint: str
    reuse_from: str | None

    def as_json(self) -> dict[str, str]:
        body = {"event": self.event, "fingerprint": self.fingerprint}
        if self.reuse_from is not None:
            body["from"] = self.reuse_from
        return body


class StoreLock:
    """The exclusive driver lock on a store root (module docstring)."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self._fd: int | None = os.open(root / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise CodeFingerprintError(
                f"another driver holds {root / LOCK_FILE}: one campaign per store "
                f"at a time"
            ) from None

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> StoreLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _valid(fingerprint: str) -> bool:
    return (
        bool(fingerprint)
        and fingerprint != UNRECORDED
        and all("!" <= c <= "~" for c in fingerprint)
    )


def _parse(path: Path, n: int, line: str) -> FingerprintEntry:
    where = f"{path} line {n}"
    try:
        body = json.loads(line)
    except ValueError as error:
        raise CodeFingerprintError(f"{where}: not JSON ({error})") from error
    if not isinstance(body, dict):
        raise CodeFingerprintError(f"{where}: not a JSON object")
    event = body.get("event")
    expected = {"establish": {"event", "fingerprint"}}.get(
        str(event), {"event", "fingerprint", "from"}
    )
    if event not in ("establish", "reuse") or set(body) != expected:
        raise CodeFingerprintError(f"{where}: not a fingerprint line: {line!r}")
    values = [body[k] for k in sorted(expected - {"event"})]
    if not all(isinstance(v, str) and v for v in values):
        raise CodeFingerprintError(f"{where}: empty or non-string field: {line!r}")
    if event == "establish":
        return FingerprintEntry("establish", body["fingerprint"], None)
    return FingerprintEntry("reuse", body["fingerprint"], body["from"])


def _committed(path: Path) -> tuple[str, int]:
    """The committed text (whole ``\\n``-terminated lines) and its byte length."""
    data = path.read_bytes()
    end = data.rfind(b"\n") + 1
    try:
        return data[:end].decode("utf-8"), end
    except UnicodeDecodeError as error:
        raise CodeFingerprintError(f"{path} is not UTF-8 ({error})") from error


def read_fingerprints(root: Path) -> tuple[FingerprintEntry, ...]:
    """The sidecar's committed lines, checked as a chain; ``()`` when there is
    none (no file, or no ``\\n``-terminated line yet).

    The first line establishes a fingerprint or adopts from
    :data:`UNRECORDED`; every later line is a reuse from the fingerprint the
    line before it left.
    """
    path = root / FINGERPRINT_FILE
    if not path.exists():
        return ()
    text, _ = _committed(path)
    if not text:
        return ()
    lines = text.split("\n")[:-1]  # split on \n only: no U+2028 or U+0085 breaks
    entries = tuple(_parse(path, n, ln) for n, ln in enumerate(lines, 1))
    first = entries[0]
    if first.event == "reuse" and first.reuse_from != UNRECORDED:
        raise CodeFingerprintError(
            f"{path} line 1 reuses from {first.reuse_from!r}, which it never held"
        )
    for n, (prev, entry) in enumerate(pairwise(entries), 2):
        if entry.event != "reuse" or entry.reuse_from != prev.fingerprint:
            raise CodeFingerprintError(
                f"{path} line {n} does not follow from line {n - 1}: the store "
                f"held {prev.fingerprint!r}"
            )
    return entries


def has_content(root: Path) -> bool:
    """Whether ``root`` holds anything a run could reuse: a ledger (even an
    empty one), a detail file (``recover`` would commit it) or a session
    record."""
    if (root / LEDGER_FILE).exists():
        return True
    return any(
        (root / sub).is_dir() and any((root / sub).iterdir())
        for sub in (DETAILS_DIR, LLM_DIR)
    )


def _sync_dir(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _create(root: Path, line: str) -> None:
    """The first line, atomically: temp file, fsync, link into place, fsync dir.

    A sidecar with no committed line (an older, torn creation) is replaced.
    """
    path = root / FINGERPRINT_FILE
    tmp = root / f".{FINGERPRINT_FILE}.{os.getpid()}.tmp"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if path.exists():
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    _sync_dir(root)


def _append(root: Path, line: str) -> None:
    """One more line, synced; an uncommitted (torn) tail is cut off first."""
    path = root / FINGERPRINT_FILE
    _, end = _committed(path)
    with path.open("r+b") as handle:
        handle.truncate(end)
        handle.seek(end)
        handle.write(line.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def guard_store(
    root: Path,
    fingerprint: str,
    *,
    reuse_from: str | None = None,
    lock: StoreLock | None = None,
) -> FingerprintEntry | None:
    """Check that code ``fingerprint`` may write the store at ``root``, and
    record it (module docstring). Returns the line appended, or None when the
    store already holds ``fingerprint``. Raises
    :class:`CodeFingerprintError` and writes no line when refused.

    ``lock`` is the caller's :class:`StoreLock` on ``root``; without one the
    lock is taken for the duration of the call.
    """
    if not _valid(fingerprint):
        raise CodeFingerprintError(
            f"not a code fingerprint: {fingerprint!r} (printable ASCII without "
            f"spaces, not {UNRECORDED!r})"
        )
    if lock is None:
        with StoreLock(root) as held:
            return guard_store(root, fingerprint, reuse_from=reuse_from, lock=held)
    if lock.root != root:
        raise CodeFingerprintError(f"the lock held is on {lock.root}, not {root}")
    entries = read_fingerprints(root)
    if not entries:
        if not has_content(root):
            if reuse_from is not None:
                raise CodeFingerprintError(
                    f"{root} is a new store; --reuse-from {reuse_from} names a "
                    f"fingerprint it never held"
                )
            entry = FingerprintEntry("establish", fingerprint, None)
        elif reuse_from == UNRECORDED:
            entry = FingerprintEntry("reuse", fingerprint, UNRECORDED)
        else:
            raise CodeFingerprintError(
                f"{root} holds cells but no {FINGERPRINT_FILE}: the code that "
                f"wrote them is unrecorded. To keep them under {fingerprint}, "
                f"pass --reuse-from {UNRECORDED} (it is recorded); otherwise "
                f"use a new store"
            )
        _create(root, canonical_json(entry.as_json()) + "\n")
        return entry
    stored = entries[-1].fingerprint
    if reuse_from is not None and reuse_from != stored:
        raise CodeFingerprintError(
            f"{root} holds code fingerprint {stored}; --reuse-from {reuse_from} "
            f"does not name it"
        )
    if stored == fingerprint:
        return None
    if reuse_from is None:
        raise CodeFingerprintError(
            f"{root} was written by code fingerprint {stored}, not {fingerprint}. "
            f"To reuse its cells, pass --reuse-from {stored} (it is recorded); "
            f"otherwise use a new store"
        )
    entry = FingerprintEntry("reuse", fingerprint, stored)
    _append(root, canonical_json(entry.as_json()) + "\n")
    return entry
