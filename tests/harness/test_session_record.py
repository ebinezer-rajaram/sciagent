"""The session record: append-only, canonical JSON, hash-chained."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciagent.harness.errors import (
    RecordFormatError,
    RecordIntegrityError,
    RecordOverwriteError,
)
from sciagent.harness.record import GENESIS, SessionRecord, canonical_json, digest


def _record() -> SessionRecord:
    record = SessionRecord()
    record.append("config", {"model": "m", "seed": 7}, elapsed_s=0.0)
    record.append("assistant", {"content": [{"type": "text", "text": "héllo"}]})
    record.append("tool_call", {"call": 0, "text": "1", "values": (1.5, 2)})
    return record


def test_canonical_json_is_sorted_compact_utf8() -> None:
    assert canonical_json({"b": 1, "a": [1.0, "é"]}) == '{"a":[1.0,"é"],"b":1}'
    with pytest.raises(RecordFormatError):
        canonical_json({"x": float("nan")})
    with pytest.raises(RecordFormatError):
        canonical_json({"x": object()})


def test_the_chain_links_every_entry_to_its_predecessor() -> None:
    record = _record()
    entries = record.entries
    assert [e.index for e in entries] == [0, 1, 2]
    assert entries[0].prev == GENESIS
    assert entries[1].prev == entries[0].hash
    assert record.head == entries[-1].hash
    assert entries[2].body["values"] == [1.5, 2]  # frozen to JSON-native form


def test_the_head_is_independent_of_timing() -> None:
    a, b = _record(), SessionRecord()
    b.append("config", {"seed": 7, "model": "m"}, elapsed_s=99.0)
    b.append("assistant", {"content": [{"text": "héllo", "type": "text"}]})
    b.append("tool_call", {"call": 0, "text": "1", "values": [1.5, 2]})
    b.set_timing("wall_s", 12.0)
    assert a.head == b.head


def test_save_load_round_trip_is_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    record = _record()
    record.save(path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    loaded = SessionRecord.load(path)
    assert loaded.head == record.head
    assert loaded.entries == record.entries
    loaded.save(tmp_path / "again.json")
    assert (tmp_path / "again.json").read_bytes() == raw


def test_save_refuses_to_overwrite_a_different_record(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    _record().save(path)
    other = SessionRecord()
    other.append("config", {"model": "other"})
    with pytest.raises(RecordOverwriteError):
        other.save(path)


def test_save_allows_extending_a_prefix(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    record = SessionRecord()
    record.append("config", {"model": "m"})
    record.save(path)
    record.append("outcome", {"reason": "submitted"})
    record.save(path)
    assert SessionRecord.load(path).head == record.head


def test_tampering_with_a_saved_record_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    _record().save(path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("héllo", "hello"), encoding="utf-8", newline="\n")
    with pytest.raises(RecordIntegrityError, match="entry 1"):
        SessionRecord.load(path)


def test_reordering_entries_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    _record().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"][1], data["entries"][2] = data["entries"][2], data["entries"][1]
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    with pytest.raises(RecordIntegrityError):
        SessionRecord.load(path)


def test_digest_is_sha256_of_canonical_json() -> None:
    import hashlib

    value = {"text": "x", "is_error": False}
    assert digest(value) == hashlib.sha256(canonical_json(value).encode()).hexdigest()
