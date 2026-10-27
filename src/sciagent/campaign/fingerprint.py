"""The store's code fingerprint: no silent reuse of cells across code changes.

A store is resumable and ``ResultStore.done`` is ledger membership, so a
resume after a code change would reuse every stored cell whose hand-written
address did not move (LOG 2026-10-27, F5). The guard makes that reuse a
recorded decision.

The store keeps ``fingerprint.jsonl`` beside its cells, never in them, so cell
bytes and :func:`~sciagent.campaign.audit.compare_stores` are unaffected. It is
append-only (invariant 4), one canonical JSON object per line:

- ``{"event": "establish", "fingerprint": F}``: the first line of a store
  created under the guard;
- ``{"event": "reuse", "fingerprint": G, "from": F}``: an explicit permission
  to keep the store's cells, written under ``F``, while writing under ``G``.
  ``F`` is the fingerprint the store held at that point (the previous line's),
  or :data:`UNRECORDED` as the first line of a store whose cells predate the
  guard.

The store's fingerprint is the last line's. :func:`guard_store` runs before a
campaign opens its store and before any unit is launched:

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
wrong.

The fingerprint is opaque here: ``sciagent`` runs no git and reads no lock
file. The caller (``scripts/pilot.py``) computes it and refuses a dirty tree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Final, Literal

from sciagent.campaign.plan import CampaignError
from sciagent.campaign.store import DETAILS_DIR, LEDGER_FILE
from sciagent.scenarios.records import canonical_json

FINGERPRINT_FILE: Final = "fingerprint.jsonl"
#: ``reuse_from`` for a store whose cells predate the guard.
UNRECORDED: Final = "unrecorded"
#: The driver's directory of LLM session records (``driver.LLM_DIR``; the
#: driver imports this module, so the name is repeated, not imported).
_LLM_DIR: Final = "llm"

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
        raise CodeFingerprintError(f"{where}: not a fingerprint line: {line}")
    values = [body[k] for k in sorted(expected - {"event"})]
    if not all(isinstance(v, str) and v for v in values):
        raise CodeFingerprintError(f"{where}: empty or non-string field: {line}")
    if event == "establish":
        return FingerprintEntry("establish", body["fingerprint"], None)
    return FingerprintEntry("reuse", body["fingerprint"], body["from"])


def read_fingerprints(root: Path) -> tuple[FingerprintEntry, ...]:
    """The sidecar's lines, checked as a chain; ``()`` when there is none.

    The first line establishes a fingerprint or adopts from
    :data:`UNRECORDED`; every later line is a reuse from the fingerprint the
    line before it left.
    """
    path = root / FINGERPRINT_FILE
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise CodeFingerprintError(f"{path} is empty")
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
        for sub in (DETAILS_DIR, _LLM_DIR)
    )


def _append(root: Path, entry: FingerprintEntry, *, first: bool) -> None:
    line = canonical_json(entry.as_json()) + "\n"
    path = root / FINGERPRINT_FILE
    if first:
        root.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
        except FileExistsError:
            # Another driver on this store got there first; agreeing is fine.
            if path.read_text(encoding="utf-8") != line:
                raise CodeFingerprintError(
                    f"{path} was created concurrently with different content"
                ) from None
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def guard_store(
    root: Path, fingerprint: str, *, reuse_from: str | None = None
) -> FingerprintEntry | None:
    """Check that code ``fingerprint`` may write the store at ``root``, and
    record it (module docstring). Returns the line appended, or None when the
    store already holds ``fingerprint``. Raises
    :class:`CodeFingerprintError` and writes nothing when refused."""
    if not fingerprint or "\n" in fingerprint or "\r" in fingerprint:
        raise CodeFingerprintError(f"not a code fingerprint: {fingerprint!r}")
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
        _append(root, entry, first=True)
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
    _append(root, entry, first=False)
    return entry
