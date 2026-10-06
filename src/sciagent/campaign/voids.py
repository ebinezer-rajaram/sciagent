"""The void policy (P4-PROPOSAL Δ8), read from a stored session and its record.

The policy and its classifier live in :mod:`sciagent.harness.voids` (read its
docstring), because the harness applies it at run time and may not import from
the campaign. This module re-exports them and adds what is about the store:
:func:`session_void` reads a stored cell, and its record when a stored reason
needs it, and classifies it, whether the cell was written by the P3 harness
(``harness/1``: raw ``foreign tool:`` reasons, a denied bare name stored as
void) or under the policy. No stored cell is edited.

The record is read only when a stored foreign-tool reason needs it, and it is
fully hash-verified when read (:meth:`~sciagent.harness.record.SessionRecord.load`).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sciagent.harness.record import SessionRecord
from sciagent.harness.voids import (
    FOREIGN,
    INFRASTRUCTURE,
    OUT_OF_BOUNDS,
    SERVER,
    ForeignResult,
    Json,
    VoidStatus,
    classify,
    denial_text,
    foreign_results,
)

__all__ = [
    "FOREIGN",
    "INFRASTRUCTURE",
    "OUT_OF_BOUNDS",
    "SERVER",
    "ForeignResult",
    "VoidStatus",
    "classify",
    "decoded",
    "denial_text",
    "foreign_results",
    "session_void",
]


def session_void(
    root: Path, reading: Mapping[str, float], detail: Mapping[str, Json]
) -> VoidStatus:
    """:func:`classify` a stored session, reading its record under ``root``
    (the store's root; the detail's ``record_dir``/``record``) when needed."""
    needs_record = reading.get("status") == 1.0 and any(
        str(r).startswith(FOREIGN) and not str(r).startswith(FOREIGN + SERVER)
        for r in detail.get("void_reasons") or []
    )
    results: tuple[ForeignResult, ...] = ()
    if needs_record:
        path = root / str(detail["record_dir"]) / str(detail["record"])
        results = foreign_results(SessionRecord.load(path))
    return classify(reading, detail, results)


def decoded(reading: Mapping[str, str]) -> dict[str, float]:
    """A stored cell's reading (``float.hex`` strings) as floats."""
    return {k: float.fromhex(v) for k, v in reading.items()}
