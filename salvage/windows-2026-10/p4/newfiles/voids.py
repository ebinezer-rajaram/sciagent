"""The void policy (P4-PROPOSAL Δ8), read from a stored session and its record.

SPEC §9 voids a run for *out-of-bounds access*. The P3 harness (fit/4,
``harness/1``) flagged every ``tool_use`` whose name was not one of the lab's
tools, so a hallucinated bare ``python`` call that Claude Code refused ("No
such tool available") voided the run although nothing ran (audit §3: all 9
pilot voids). The policy, applied here at analysis time from what the store
already holds (no stored cell is edited):

A completed session (``status = 1``) is **void** if and only if one of:

- a foreign tool **executed**: the record holds a non-error
  ``foreign_tool_result`` for that name;
- a foreign tool use has **no recorded denial**: fewer error results for the
  name than uses of it (nothing shows it did not run, so it is not excused);
- **server tool use**: a ``server:`` reason, or a non-zero count in the
  result's ``usage.server_tool_use``;
- an **out-of-bounds** hit of the ``python`` scan;
- a sandbox **infrastructure** fault, or any reason this module does not know
  (kept void: the policy only excuses what it can show was harmless).

A foreign use whose every call was denied with an error result is counted in
``unknown_tool_attempts`` and does not void the run. A session that did not
complete (``status != 1``: aborted, hard timeout) keeps its stored void flag;
it has nothing to score either way.

The record is read only when a stored foreign-tool reason needs it, and it is
fully hash-verified when read (:meth:`~sciagent.harness.record.SessionRecord.load`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sciagent.harness.record import SessionRecord

type Json = Any

FOREIGN: Final = "foreign tool: "
SERVER: Final = "server:"
OUT_OF_BOUNDS: Final = "out of bounds: "
INFRASTRUCTURE: Final = "infrastructure: "


@dataclass(frozen=True)
class ForeignResult:
    """One ``foreign_tool_result`` entry of a record: the name and the flag."""

    name: str
    is_error: bool


@dataclass(frozen=True)
class VoidStatus:
    """A session under the policy: void or not, why, and denied attempts."""

    void: bool
    reasons: tuple[str, ...]
    unknown_tool_attempts: int
    stored_void: bool

    @property
    def reclassified(self) -> bool:
        """Stored as void by the harness, not void under the policy."""
        return self.stored_void and not self.void


def _server_requests(detail: Mapping[str, Json]) -> int:
    usage = detail.get("usage") or {}
    counts = usage.get("server_tool_use") or {}
    return sum(int(v or 0) for v in counts.values())


def classify(
    reading: Mapping[str, float],
    detail: Mapping[str, Json],
    foreign_results: Sequence[ForeignResult],
) -> VoidStatus:
    """The policy (module docstring) over a session's stored reading, detail
    and its record's foreign tool results."""
    stored = reading.get("void", 0.0) == 1.0
    if reading.get("status") != 1.0:
        outcome = str(detail.get("outcome") or detail.get("error"))
        return VoidStatus(stored, (f"not completed: {outcome}",), 0, stored)
    reasons: list[str] = []
    uses: dict[str, int] = {}
    for reason in detail.get("void_reasons") or []:
        text = str(reason)
        if text.startswith(FOREIGN + SERVER):
            reasons.append(f"server tool use: {text.removeprefix(FOREIGN + SERVER)}")
        elif text.startswith(FOREIGN):
            name = text.removeprefix(FOREIGN)
            uses[name] = uses.get(name, 0) + 1
        else:
            reasons.append(text)
    attempts = 0
    for name in sorted(uses):
        mine = [r for r in foreign_results if r.name == name]
        denied = sum(r.is_error for r in mine)
        if any(not r.is_error for r in mine):
            reasons.append(f"foreign tool executed: {name}")
        elif denied < uses[name]:
            reasons.append(f"foreign tool without a recorded denial: {name}")
        else:
            attempts += denied
    if _server_requests(detail) and not any(
        r.startswith("server tool use") for r in reasons
    ):
        reasons.append("server tool use: usage.server_tool_use")
    return VoidStatus(bool(reasons), tuple(reasons), attempts, stored)


def foreign_results(record: SessionRecord) -> tuple[ForeignResult, ...]:
    """Every ``foreign_tool_result`` entry of ``record``, in record order."""
    return tuple(
        ForeignResult(str(e.body.get("name")), bool(e.body.get("is_error")))
        for e in record.of_kind("foreign_tool_result")
    )


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
