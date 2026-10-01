"""The session record: an append-only, hash-chained log of one investigation.

What it is for
--------------
SPEC §4: "every model turn and every tool result is recorded, so a whole
investigation replays offline byte-for-byte." The record is that artefact. It
holds, in order, the run's configuration, the session manifest, every assistant
message as returned, every tool call with its result, the result message and the
outcome. :func:`~sciagent.harness.replay.replay` reads it back without the model.

Format
------
* Every entry is ``{"index", "kind", "body", "prev", "hash"}``. ``body`` is
  frozen to JSON-native values on append (tuples become lists), so what is
  hashed is exactly what is written.
* ``hash = sha256(prev + "\\n" + canonical_json({"index", "kind", "body"}))``,
  and the first entry's ``prev`` is :data:`GENESIS`, a hash of the format
  string -- so a record of a different format can never verify as this one.
  :attr:`SessionRecord.head`, the last hash, is the run's content address.
* Timing lives in a separate ``timing`` section that is **not** hashed: two runs
  that made the same calls and got the same answers have the same address
  however long they took.
* Files are written as JSON with sorted keys, ``indent=1``, UTF-8, ``\\n``
  newlines on every platform (the reason is the one recorded on
  :meth:`~sciagent.systems.llm.transcripts.TranscriptStore.save`).

Append-only
-----------
There is no way to remove or edit an entry in process. :meth:`SessionRecord.save`
refuses to overwrite a file holding a different record, but allows extending a
prefix of itself, so a long run can checkpoint. :meth:`SessionRecord.load`
re-verifies the whole chain, so an edit or a reorder made on disk is refused at
load rather than discovered at replay.

This module imports nothing from the Agent SDK; replay depends on that.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sciagent.harness.errors import (
    RecordFormatError,
    RecordIntegrityError,
    RecordOverwriteError,
)

__all__ = [
    "FORMAT",
    "GENESIS",
    "RecordEntry",
    "SessionRecord",
    "canonical_json",
    "digest",
]

#: The record format. Bound into :data:`GENESIS`, so a change of format changes
#: every address rather than silently reinterpreting old files.
FORMAT: Final = "sciagent-session/1"

#: ``prev`` of the first entry.
GENESIS: Final = hashlib.sha256(FORMAT.encode("utf-8")).hexdigest()


def _check_finite(value: Any) -> None:
    """Raise on NaN or infinity anywhere in a JSON-native value."""
    if isinstance(value, float) and not math.isfinite(value):
        raise RecordFormatError(f"non-finite float {value!r} cannot be recorded")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text of ``value``: sorted keys, compact, UTF-8.

    Raises :class:`RecordFormatError` for anything JSON cannot carry exactly:
    NaN and infinities (which ``json`` would write as non-standard tokens) and
    non-JSON objects.
    """
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise RecordFormatError(f"value cannot be recorded as JSON: {error}") from error
    return text


def digest(value: Any) -> str:
    """Return the SHA-256 (lowercase hex) of ``canonical_json(value)``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    """Return ``value`` as JSON-native data (what a load would give back)."""
    frozen = json.loads(canonical_json(value))
    _check_finite(frozen)
    return frozen


def _link(prev: str, index: int, kind: str, body: Any) -> str:
    payload = canonical_json({"index": index, "kind": kind, "body": body})
    return hashlib.sha256(f"{prev}\n{payload}".encode()).hexdigest()


@dataclass(frozen=True)
class RecordEntry:
    """One link of the chain. ``body`` is JSON-native and must not be mutated."""

    index: int
    kind: str
    body: Any
    prev: str
    hash: str

    def as_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "body": self.body,
            "prev": self.prev,
            "hash": self.hash,
        }


class SessionRecord:
    """An append-only, hash-chained log of one investigation."""

    __slots__ = ("_elapsed", "_entries", "_timing")

    def __init__(self) -> None:
        self._entries: list[RecordEntry] = []
        self._elapsed: list[float | None] = []
        self._timing: dict[str, Any] = {}

    # -- writing ------------------------------------------------------------

    def append(
        self, kind: str, body: Mapping[str, Any], *, elapsed_s: float | None = None
    ) -> str:
        """Append one entry and return its hash.

        ``elapsed_s`` (seconds since the run started) goes to the unhashed timing
        section; it never affects the address.
        """
        frozen = _freeze(dict(body))
        index = len(self._entries)
        prev = self.head
        entry = RecordEntry(index, kind, frozen, prev, _link(prev, index, kind, frozen))
        self._entries.append(entry)
        self._elapsed.append(elapsed_s)
        return entry.hash

    def set_timing(self, key: str, value: Any) -> None:
        """Set an unhashed timing/cost field (wall time, API time, cost)."""
        if key == "entries":
            raise RecordFormatError("'entries' is reserved for per-entry timing")
        self._timing[key] = _freeze({"v": value})["v"]

    # -- reading ------------------------------------------------------------

    @property
    def head(self) -> str:
        """The content address of the run so far: the last entry's hash."""
        return self._entries[-1].hash if self._entries else GENESIS

    @property
    def entries(self) -> tuple[RecordEntry, ...]:
        return tuple(self._entries)

    @property
    def timing(self) -> dict[str, Any]:
        """The unhashed section: per-entry elapsed seconds plus summary fields."""
        return {**self._timing, "entries": list(self._elapsed)}

    def of_kind(self, kind: str) -> tuple[RecordEntry, ...]:
        return tuple(e for e in self._entries if e.kind == kind)

    # -- persistence --------------------------------------------------------

    def as_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "head": self.head,
            "entries": [e.as_json() for e in self._entries],
            "timing": self.timing,
        }

    def dumps(self) -> str:
        """Return the file text: sorted keys, ``indent=1``, trailing newline."""
        return (
            json.dumps(
                self.as_json(),
                sort_keys=True,
                indent=1,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )

    def save(self, path: Path) -> None:
        """Write the record to ``path``; refuse to replace a different record.

        A file whose entries are a prefix of this record's (same hashes, in
        order) may be extended -- that is a checkpoint growing. Anything else
        raises :class:`RecordOverwriteError`, including a file that does not
        parse, since its content cannot be shown to be a prefix.
        """
        if path.exists():
            try:
                existing = SessionRecord.load(path)
            except (RecordIntegrityError, RecordFormatError) as error:
                raise RecordOverwriteError(
                    f"{path} holds something that is not a valid record ({error}); "
                    f"refusing to overwrite it"
                ) from error
            ours = [e.hash for e in self._entries]
            theirs = [e.hash for e in existing.entries]
            if ours[: len(theirs)] != theirs:
                raise RecordOverwriteError(
                    f"{path} holds a different record (head {existing.head[:12]}); "
                    f"records are append-only, so it is not overwritten"
                )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.dumps(), encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path: Path) -> SessionRecord:
        """Read and fully verify a record. Raises on any broken link."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RecordFormatError(
                f"cannot read a record from {path}: {error}"
            ) from error
        return cls.from_json(data)

    @classmethod
    def from_json(cls, data: Any) -> SessionRecord:
        """Rebuild a record from its JSON form, re-verifying every hash."""
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise RecordFormatError(f"not a {FORMAT} record")
        record = cls()
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise RecordFormatError("a record's 'entries' must be a list")
        for position, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                raise RecordFormatError(f"entry {position} is not an object")
            if raw.get("index") != position:
                raise RecordIntegrityError(
                    f"entry {position} claims index {raw.get('index')!r}: the "
                    f"record was reordered or truncated"
                )
            record.append(str(raw.get("kind")), raw.get("body") or {})
            rebuilt = record._entries[-1]
            if rebuilt.prev != raw.get("prev") or rebuilt.hash != raw.get("hash"):
                raise RecordIntegrityError(
                    f"entry {position} ({rebuilt.kind}) does not verify: its "
                    f"content or its predecessor was changed after recording"
                )
        if data.get("head") != record.head:
            raise RecordIntegrityError(
                "the record's head does not match its last entry"
            )
        timing = data.get("timing") or {}
        elapsed = timing.get("entries") or []
        if len(elapsed) == len(record._entries):
            record._elapsed = [None if v is None else float(v) for v in elapsed]
        for key in sorted(timing):
            if key != "entries":
                record._timing[key] = timing[key]
        return record
