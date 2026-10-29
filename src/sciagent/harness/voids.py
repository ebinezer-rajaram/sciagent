"""The void policy (P4-PROPOSAL Δ8): which runs are void, and why.

SPEC §9 voids a run for *out-of-bounds access*. The P3 harness (``harness/1``)
flagged every ``tool_use`` whose name was not one of the lab's tools, so a
hallucinated bare ``python`` call that Claude Code refused ("No such tool
available") voided the run although nothing ran (audit §3: all 9 pilot voids).

The policy. A completed run is **void** if and only if one of:

- a foreign tool **executed**: the record holds a ``foreign_tool_result`` for
  that name that is not a denial. A denial is an error result whose text is
  exactly the CLI's refusal of a name it does not have
  (:func:`denial_text`); any other result, error or not, may have run
  something, so it counts as executed;
- a foreign tool use has **no recorded denial**: fewer denials for the
  name than uses of it (nothing shows it did not run, so it is not excused);
- **server tool use**: a ``server:`` use, or a non-zero count in the result's
  ``usage.server_tool_use``;
- an **out-of-bounds** hit of the ``python`` scan;
- a sandbox **infrastructure** fault, or any reason this module does not know
  (kept void: the policy only excuses what it can show was harmless).

A foreign use whose every call was denied (an error result with the denial
text) is counted in ``unknown_tool_attempts`` and does not void the run.

One classifier, two readers. :func:`judge` is the policy over *raw reasons*
(``foreign tool: <name>`` per use, ``foreign tool: server:<name>``, and the
investigation's ``infrastructure:`` and ``out of bounds:`` reasons), the
record's foreign tool results and the server request count. The harness applies
it at run time (:func:`assess`, from
:class:`~sciagent.harness.live.InvestigationResult` and
:func:`sciagent.investigation.runner.run`); the campaign applies it at analysis
time to a stored cell (:func:`classify`, from :mod:`sciagent.campaign.voids`),
whether the cell holds the pilot's raw reasons (``harness/1``) or the policy's
own. :func:`judge` is idempotent on its own output (every reason it emits
passes through unchanged), which is why a cell written under the policy reads
back the same; the attempt count, which is not a reason, is stored in the
reading and added back by :func:`classify`.

The harness may not import from the campaign or the investigation
(``tests/test_invariants.py``), so the classifier lives here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sciagent.harness.record import SessionRecord

__all__ = [
    "FOREIGN",
    "INFRASTRUCTURE",
    "OUT_OF_BOUNDS",
    "SERVER",
    "ForeignResult",
    "Verdict",
    "VoidStatus",
    "assess",
    "classify",
    "denial_text",
    "foreign_reasons",
    "foreign_results",
    "judge",
    "server_requests",
]

type Json = Any

FOREIGN: Final = "foreign tool: "
SERVER: Final = "server:"
OUT_OF_BOUNDS: Final = "out of bounds: "
INFRASTRUCTURE: Final = "infrastructure: "


@dataclass(frozen=True)
class ForeignResult:
    """One ``foreign_tool_result`` entry of a record: name, flag and text."""

    name: str
    is_error: bool
    text: str

    @property
    def denied(self) -> bool:
        """The CLI refused the name (module docstring); nothing ran."""
        return self.is_error and self.text == denial_text(self.name)


def denial_text(name: str) -> str:
    """The Claude Code CLI's result for a tool name it does not have.

    Verbatim from the pilot's records (all 9 bare ``python`` calls), and the
    text :class:`~sciagent.harness.scripted.ScriptedDriver` emulates.
    """
    return f"<tool_use_error>Error: No such tool available: {name}</tool_use_error>"


@dataclass(frozen=True)
class Verdict:
    """The policy over one run: why it is void (empty: it is not), and how
    many denied calls to unknown tool names it made."""

    reasons: tuple[str, ...]
    unknown_tool_attempts: int

    @property
    def void(self) -> bool:
        return bool(self.reasons)


@dataclass(frozen=True)
class VoidStatus:
    """A stored session under the policy: void or not, why, denied attempts,
    and the void flag the store holds."""

    void: bool
    reasons: tuple[str, ...]
    unknown_tool_attempts: int
    stored_void: bool

    @property
    def reclassified(self) -> bool:
        """Stored as void by the harness, not void under the policy."""
        return self.stored_void and not self.void


def server_requests(usage: Mapping[str, Json] | None) -> int:
    """The server tool requests a result's ``usage`` counts."""
    counts = (usage or {}).get("server_tool_use") or {}
    return sum(int(v or 0) for v in counts.values())


def foreign_reasons(foreign_tool_uses: Iterable[str]) -> tuple[str, ...]:
    """The raw reason of each foreign tool use, in use order."""
    return tuple(FOREIGN + name for name in foreign_tool_uses)


def judge(
    reasons: Iterable[str],
    foreign_results: Sequence[ForeignResult],
    server_requests: int,
) -> Verdict:
    """The policy (module docstring) over raw reasons, the record's foreign
    tool results and the server request count.

    The returned reasons are, in order: every reason that is not a foreign use
    (kept in its given order, ``server:`` uses rewritten), then one per foreign
    name that is not excused (by name), then the usage count's server reason
    if no server use was already a reason."""
    kept: list[str] = []
    uses: dict[str, int] = {}
    for reason in reasons:
        text = str(reason)
        if text.startswith(FOREIGN + SERVER):
            kept.append(f"server tool use: {text.removeprefix(FOREIGN + SERVER)}")
        elif text.startswith(FOREIGN):
            name = text.removeprefix(FOREIGN)
            uses[name] = uses.get(name, 0) + 1
        else:
            kept.append(text)
    attempts = 0
    for name in sorted(uses):
        mine = [r for r in foreign_results if r.name == name]
        denied = sum(r.denied for r in mine)
        if any(not r.denied for r in mine):
            kept.append(f"foreign tool executed: {name}")
        elif denied < uses[name]:
            kept.append(f"foreign tool without a recorded denial: {name}")
        else:
            attempts += denied
    if server_requests and not any(r.startswith("server tool use") for r in kept):
        kept.append("server tool use: usage.server_tool_use")
    return Verdict(tuple(kept), attempts)


def foreign_results(record: SessionRecord) -> tuple[ForeignResult, ...]:
    """Every ``foreign_tool_result`` entry of ``record``, in record order."""
    return tuple(
        ForeignResult(
            str(e.body.get("name")),
            bool(e.body.get("is_error")),
            str(e.body.get("text") or ""),
        )
        for e in record.of_kind("foreign_tool_result")
    )


def assess(reasons: Iterable[str], record: SessionRecord) -> Verdict:
    """:func:`judge` at run time: the foreign tool results and the server
    request count are read from ``record`` (its last ``result`` entry)."""
    results = record.of_kind("result")
    usage = results[-1].body.get("usage") if results else None
    return judge(reasons, foreign_results(record), server_requests(usage))


def classify(
    reading: Mapping[str, float],
    detail: Mapping[str, Json],
    foreign_results: Sequence[ForeignResult],
) -> VoidStatus:
    """:func:`judge` at analysis time, over a stored session's reading and
    detail and its record's foreign tool results.

    A session that did not complete (``status != 1``: aborted, hard timeout)
    keeps its stored void flag; it has nothing to score either way. A reading
    written under the policy carries ``unknown_tool_attempts``; it is added to
    what the stored reasons show (none, since its reasons are the policy's)."""
    stored = reading.get("void", 0.0) == 1.0
    if reading.get("status") != 1.0:
        outcome = str(detail.get("outcome") or detail.get("error"))
        return VoidStatus(stored, (f"not completed: {outcome}",), 0, stored)
    verdict = judge(
        detail.get("void_reasons") or [],
        foreign_results,
        server_requests(detail.get("usage")),
    )
    attempts = verdict.unknown_tool_attempts + int(
        reading.get("unknown_tool_attempts", 0.0)
    )
    return VoidStatus(verdict.void, verdict.reasons, attempts, stored)
