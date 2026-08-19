"""Report where the build stands, derived entirely from the repository.

Prints two backlogs in build order -- SPEC §11, then ``docs/BACKLOG.md``'s gated
entries -- with the cursor, per-gate acceptance coverage and working-tree state.
Every figure comes from those two documents, from pytest or from git; nothing in
the report is hand-maintained, so it cannot drift from the code it describes. A
malformed backlog file is an error rather than a quietly shorter report, since
every way of losing an entry looks like a clean one.

Guarantees: the script writes nothing inside the repository, and in the default
mode it never claims a gate passes, only that tests for it exist. Pass ``--run``
to execute the suite and report verified outcomes.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "SPEC.md"
#: Gates declared after the design freeze. SPEC §6 stops at A24 and SPEC is
#: frozen, so criteria added since are stated in the backlog entry that
#: proposes them -- see :func:`parse_backlog_entries`.
BACKLOG = ROOT / "docs" / "BACKLOG.md"

#: ``- **A1** Determinism: ...`` and ``- **A6 Likelihood estimation.** ...``
GATE_DEFINITION = re.compile(r"^-\s+\*\*A(\d+)\b(.*)$")
#: A gate range such as "A6 to A11". The spec writes these with an en dash, so
#: all three separators are accepted; the dashes are escaped rather than typed
#: literally because they are visually ambiguous with a hyphen.
DASHES = f"{chr(0x2013)}{chr(0x2014)}-"  # en dash, em dash, hyphen
#: The two that separate rather than join, for prose that also has hyphens.
LONG_DASHES = f"{chr(0x2013)}{chr(0x2014)}"
GATE_RANGE = re.compile(rf"A(\d+)\s*[{DASHES}]\s*A?(\d+)")
GATE_SINGLE = re.compile(r"A(\d+)")
#: Every acceptance test is named for its criterion; that naming is the tag.
TEST_GATE = re.compile(r"^test_a0*(\d+)_")
BACKLOG_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|\s*$")
#: ``PASSED tests/acceptance/test_a01_a05.py::TestA5Descendants::test_a5_x``
OUTCOME_LINE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+)")

#: ``docs/BACKLOG.md``'s three machine-read fields. All three are anchored at
#: the line start on purpose: the file's own prose names ``**Gate.**``
#: mid-sentence where it explains the convention, and a searching match would
#: read that paragraph as one more entry.
ENTRY_GATE = re.compile(r"^\*\*Gate\.\*\*\s+`(test_a0*(\d+)_\w+)`")
ENTRY_RANK = re.compile(r"^\*\*Rank\.\*\*\s+(\d+)")
ENTRY_HELD = re.compile(r"^\*\*Held\.\*\*\s+(.+?)\s*$")
#: A landed or withdrawn entry keeps its fields and marks its heading.
ENTRY_CLOSED = re.compile(r"^(DONE|STRUCK)\b")
#: ``DONE (2026-08-19, gate A26) — the title``. Everything up to and
#: including the dash is provenance; what follows is the entry's own name,
#: which is what a report has room for. A hyphen is deliberately not a
#: separator here: the date carries two, and accepting one cut the heading
#: mid-provenance instead of at it.
CLOSED_PREFIX = re.compile(rf"^(?:DONE|STRUCK)[^{LONG_DASHES}]*[{LONG_DASHES}]\s*(.+)$")

TITLE_WIDTH = 44


@dataclass(frozen=True)
class BacklogItem:
    """One row of the SPEC §11 ordered build backlog."""

    number: int
    title: str
    gates: tuple[int, ...]


@dataclass(frozen=True)
class BacklogEntry:
    """One gated entry of ``docs/BACKLOG.md``, the backlog after SPEC §11.

    SPEC is frozen and §13 sends new work here, so an entry that carries a
    ``**Gate.**`` is build work rather than an idea, and its ``**Rank.**`` is
    where it falls in the order. An entry ``held`` on an open decision is not
    the cursor's to take.
    """

    rank: int
    title: str
    gate: int
    test_name: str
    held: str | None
    closed: bool

    @property
    def struck(self) -> bool:
        """True when the entry was withdrawn rather than landed.

        Both mark a heading and both keep the entry out of the cursor, but a
        withdrawn criterion is not a criterion: it declares a gate as a record
        of what was proposed, and nothing is ever going to satisfy it.
        """
        return self.title.startswith("STRUCK")

    @property
    def gate_title(self) -> str:
        """The criterion's short name, opened out from the test it is named for.

        Derived rather than restated, so the entry and the report cannot drift.
        Taken from the test name rather than the heading because a closed
        entry's heading opens with ``DONE (date, gate ANN) —``, which is
        provenance and would crowd the name out of the width a row has.
        """
        return self.test_name.split("_", 2)[2].replace("_", " ")

    @property
    def short_title(self) -> str:
        """The heading with any ``DONE``/``STRUCK`` provenance prefix removed."""
        match = CLOSED_PREFIX.match(self.title)
        return match.group(1).strip() if match else self.title


@dataclass(frozen=True)
class GateStatus:
    """Test coverage for one acceptance criterion."""

    number: int
    title: str
    total: int
    passed: int | None
    post_freeze: bool = False
    """Declared in ``docs/BACKLOG.md`` rather than SPEC §6. Reported in its own
    block so that A1-A24 keep reading as the frozen contract they are."""

    @property
    def written(self) -> bool:
        return self.total > 0

    @property
    def green(self) -> bool:
        """True only when execution confirmed it; absent evidence is not green."""
        return self.passed is not None and self.total > 0 and self.passed == self.total


def section(spec_text: str, heading_prefix: str) -> str:
    """Return the lines of the section whose heading starts with the prefix.

    Scoping every parse to one section keeps tables elsewhere in the spec from
    being mistaken for backlog rows.
    """
    lines = spec_text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(heading_prefix)),
        None,
    )
    if start is None:
        raise SystemExit(f"{SPEC.name}: no section starting {heading_prefix!r}")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def parse_gate_titles(spec_text: str) -> dict[int, str]:
    """Return acceptance-criterion number to short title, read from SPEC §6."""
    titles: dict[int, str] = {}
    for line in section(spec_text, "## 6.").splitlines():
        match = GATE_DEFINITION.match(line.strip())
        if match is None:
            continue
        number = int(match.group(1))
        rest = match.group(2).replace("*", "").strip()
        title = re.split(r"[.:,]", rest, maxsplit=1)[0].strip()
        titles[number] = title[:TITLE_WIDTH] if title else f"A{number}"
    return titles


def parse_gate_references(text: str) -> tuple[int, ...]:
    """Return every acceptance criterion named in a backlog row's gate cell.

    Ranges expand; scenario and research identifiers (``S1-S10``, ``R2``) are
    not acceptance criteria and are ignored.
    """
    numbers: set[int] = set()
    remainder = text
    for match in GATE_RANGE.finditer(text):
        low, high = int(match.group(1)), int(match.group(2))
        numbers.update(range(min(low, high), max(low, high) + 1))
        remainder = remainder.replace(match.group(0), " ")
    numbers.update(int(match.group(1)) for match in GATE_SINGLE.finditer(remainder))
    return tuple(sorted(numbers))


def parse_backlog(spec_text: str) -> list[BacklogItem]:
    """Return the SPEC §11 backlog in build order."""
    items: list[BacklogItem] = []
    for line in section(spec_text, "## 11.").splitlines():
        match = BACKLOG_ROW.match(line.strip())
        if match is None:
            continue
        title = match.group(2).replace("`", "").strip()
        items.append(
            BacklogItem(
                number=int(match.group(1)),
                title=title,
                gates=parse_gate_references(match.group(3)),
            )
        )
    return sorted(items, key=lambda item: item.number)


def markdown_sections(text: str) -> list[tuple[str, list[str]]]:
    """Return ``(heading, body)`` per ``##`` section, fences excluded.

    Two shapes in ``docs/BACKLOG.md`` defeat a line-by-line split and are
    handled here rather than by every caller. A heading too long for one line is
    written as consecutive ``##`` lines and is one heading, not two -- and since
    the fields sit below both, a naive split hands the entry's gate and rank to
    the phantom. The file also documents its own entry format in a fenced block
    that contains a ``##`` line and a ``**Gate.**`` line, which is an example and
    not an entry.
    """
    sections: list[tuple[str, list[str]]] = []
    heading: list[str] = []
    body: list[str] = []
    fenced = False
    previous_was_heading = False
    for line in text.splitlines():
        if line.lstrip().startswith("```") or fenced:
            # A fence is not nothing: two ``##`` lines with one between them are
            # not consecutive and are two headings. Leaving the flag set merges
            # them, and the merged section then holds two gate lines of which
            # only the first is read -- losing an entry silently, which is the
            # failure this whole function exists to prevent.
            fenced = fenced != line.lstrip().startswith("```")
            previous_was_heading = False
            continue
        if line.startswith("## "):
            if previous_was_heading:
                heading.append(line[3:].strip())
            else:
                if heading:
                    sections.append((" ".join(heading), body))
                heading, body = [line[3:].strip()], []
            previous_was_heading = True
            continue
        previous_was_heading = False
        body.append(line)
    if fenced:
        raise SystemExit(
            f"{BACKLOG.name}: an unclosed ``` fence swallows every section after "
            "it, and entries would go missing from the report without a word"
        )
    if heading:
        sections.append((" ".join(heading), body))
    return sections


def held_value(body: list[str]) -> str | None:
    """Return a section's ``**Held.**`` value, continuation lines included.

    The blocker is free prose and the file's own format block wraps its fields,
    so reading only the first line would print a fragment and name the wrong
    blocker. Continuations run to the next blank line or the next bold field.
    """
    for index, line in enumerate(body):
        match = ENTRY_HELD.match(line)
        if match is None:
            continue
        parts = [match.group(1)]
        for continuation in body[index + 1 :]:
            if not continuation.strip() or continuation.startswith("**"):
                break
            parts.append(continuation.strip())
        return " ".join(parts)
    return None


def parse_backlog_entries(backlog_text: str) -> list[BacklogEntry]:
    """Return ``docs/BACKLOG.md``'s gated entries, in build order.

    A section is build work exactly when it carries a ``**Gate.**`` line;
    everything else in the file is an idea and is not tracked. Rank is required
    of a gated entry -- without one it has no position in the order -- and two
    entries may not share a rank.

    Every way of *losing* an entry raises rather than returning quietly. A
    section carrying a rank but no readable gate is a mistyped field, not an
    idea: demoting it silently is how a promoted entry would drop out of the
    backlog it was just added to. Two gate lines in one section mean two
    headings merged -- adjacent ``##`` lines are one wrapped heading by design --
    and only the first would ever be read.
    """
    entries: list[BacklogEntry] = []
    seen: dict[int, str] = {}
    for heading, body in markdown_sections(backlog_text):
        gates = [match for match in map(ENTRY_GATE.match, body) if match]
        ranks = [match for match in map(ENTRY_RANK.match, body) if match]
        if len(gates) > 1 or len(ranks) > 1:
            raise SystemExit(
                f"{BACKLOG.name}: {heading!r} carries two **Gate.**/**Rank.** "
                "fields. Two adjacent ## lines are read as one wrapped heading, "
                "so this is probably two entries merged -- separate them with a "
                "blank line, and only the first would have been read"
            )
        gate_field = gates[0] if gates else None
        rank_field = ranks[0] if ranks else None
        if gate_field is None:
            if rank_field is not None:
                raise SystemExit(
                    f"{BACKLOG.name}: {heading!r} has a **Rank.** but no readable "
                    "**Gate.**; check the field spelling, since without one this "
                    "would be silently demoted to an untracked idea"
                )
            continue
        if rank_field is None:
            raise SystemExit(
                f"{BACKLOG.name}: {heading!r} has a **Gate.** but no **Rank.**, "
                "so it has no place in the build order"
            )
        rank = int(rank_field.group(1))
        if rank in seen:
            raise SystemExit(
                f"{BACKLOG.name}: rank {rank} is claimed by both {seen[rank]!r} "
                f"and {heading!r}"
            )
        seen[rank] = heading
        entries.append(
            BacklogEntry(
                rank=rank,
                title=heading,
                gate=int(gate_field.group(2)),
                test_name=gate_field.group(1),
                held=held_value(body),
                closed=ENTRY_CLOSED.match(heading) is not None,
            )
        )
    return sorted(entries, key=lambda entry: entry.rank)


def merge_gate_titles(
    spec_titles: Mapping[int, str], entries: list[BacklogEntry]
) -> dict[int, str]:
    """Return the whole acceptance namespace: SPEC §6's gates, then BACKLOG's.

    This is what stops a gate outside §6 from disappearing. ``gate_of``
    attributes ``test_a26_...`` to gate 26 whatever declares it, so a criterion
    the report does not know about is counted into no row *and* excluded from
    the "not named for a gate" tally. Numbering is one namespace across two
    files, so a collision is an error rather than a silent overwrite.
    """
    titles = dict(spec_titles)
    claimed: dict[int, str] = {}
    for entry in entries:
        if entry.struck:
            # Withdrawn: the gate line survives as a record of what was
            # proposed, but nothing will ever satisfy it, so it is not part of
            # the namespace and must not reserve a number against a live entry.
            continue
        if entry.gate in titles:
            owner = claimed.get(entry.gate, f"{SPEC.name} §6")
            raise SystemExit(
                f"{BACKLOG.name}: {entry.title!r} claims A{entry.gate}, "
                f"which {owner} already declares"
            )
        titles[entry.gate] = entry.gate_title[:TITLE_WIDTH]
        claimed[entry.gate] = repr(entry.title)
    return titles


def entry_row(
    entry: BacklogEntry, gate: GateStatus | None, *, execute: bool
) -> tuple[str, str]:
    """Return the ``(marker, detail)`` one BACKLOG entry renders as.

    A ``DONE``/``STRUCK`` heading is the author's marker and is not evidence.
    It may keep the entry out of the cursor, since somebody decided the work is
    behind us -- but it may not speak for the gate, or this script would claim a
    criterion passes on the strength of a word in a heading. So a landed entry
    whose gate has no tests renders as a discrepancy rather than as a tick.
    """
    if gate is None or gate.total == 0:
        evidence = "no tests"
    elif not execute:
        evidence = "tests written"
    elif gate.green:
        evidence = "passing"
    else:
        evidence = f"FAILING {gate.passed}/{gate.total}"
    ready = gate is not None and (gate.green if execute else gate.written)

    if entry.struck:
        # Withdrawn work has no gate row and so no evidence to fall short of.
        # Rendering it as a discrepancy would demand tests for a decision to
        # build nothing.
        return "-", f"A{entry.gate}, withdrawn"
    if entry.held is not None and not entry.closed:
        return "-", f"A{entry.gate}, held: {entry.held}"
    if entry.closed:
        return (
            ("x", f"A{entry.gate}, landed")
            if ready
            else (
                "?",
                f"A{entry.gate}, marked landed but {evidence}",
            )
        )
    if ready:
        return ("x" if execute else "~"), f"A{entry.gate}, {evidence}"
    return " ", f"A{entry.gate}, {evidence}"


def no_open_summary(entries: list[BacklogEntry]) -> str:
    """Return the cursor line for when nothing is open.

    "Everything is satisfied" and "everything left is waiting on somebody" are
    different states, and the statusline compresses the first to
    ``all gates satisfied``. Reporting the second as the first would show a
    green cursor over work that is blocked.
    """
    held = [entry for entry in entries if entry.held is not None and not entry.closed]
    if not held:
        return "every gate-tracked backlog item is satisfied"
    noun = "entry" if len(held) == 1 else "entries"
    return f"nothing open; {len(held)} BACKLOG {noun} held on a decision"


def backlog_cursor(
    entries: list[BacklogEntry], gates: Mapping[int, GateStatus], *, execute: bool
) -> BacklogEntry | None:
    """Return the next BACKLOG entry to build, or ``None`` if none is open.

    Lowest rank first. A closed entry is behind us; a held one is waiting on a
    decision that is not the agent's to take, so neither is ever the cursor.
    """
    for entry in entries:
        if entry.closed or entry.held is not None:
            continue
        status = gates.get(entry.gate)
        if status is None:
            return entry
        if not (status.green if execute else status.written):
            return entry
    return None


def gate_of(node_id: str) -> int | None:
    """Return the acceptance criterion a test node is named for, if any.

    Only the final ``::`` component is considered. The module holding A1-A5 is
    itself named ``test_a01_a05.py``, so matching anywhere in the node id would
    attribute every test in that file to A1.
    """
    match = TEST_GATE.match(node_id.rsplit("::", 1)[-1])
    return int(match.group(1)) if match else None


def collect_tests(root: Path) -> tuple[dict[int, int], int]:
    """Return counts of collected tests per gate, and the count of other tests."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    per_gate: dict[int, int] = {}
    other = 0
    for line in completed.stdout.splitlines():
        if "::" not in line:
            continue
        gate = gate_of(line)
        if gate is None:
            other += 1
        else:
            per_gate[gate] = per_gate.get(gate, 0) + 1
    if not per_gate and not other:
        raise SystemExit(
            f"pytest collected nothing:\n{completed.stdout}\n{completed.stderr}"
        )
    return per_gate, other


def run_tests(root: Path) -> tuple[dict[int, int], dict[int, int], int]:
    """Execute the suite; return passed-per-gate, total-per-gate and other count.

    Outcomes are read from pytest's ``-rA`` short summary rather than a JUnit
    report, so no XML is parsed and the report cannot become an untrusted-input
    path. Only ``PASSED`` counts as green: a skipped acceptance test is not
    evidence that its criterion holds.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--no-header",
            "-rA",
            "--tb=no",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    passed: dict[int, int] = {}
    total: dict[int, int] = {}
    other = 0
    seen = False
    for line in completed.stdout.splitlines():
        match = OUTCOME_LINE.match(line.strip())
        if match is None:
            continue
        seen = True
        outcome, node_id = match.group(1), match.group(2)
        gate = gate_of(node_id)
        if gate is None:
            other += 1
            continue
        total[gate] = total.get(gate, 0) + 1
        passed[gate] = passed.get(gate, 0) + (1 if outcome == "PASSED" else 0)
    if not seen:
        raise SystemExit(
            f"pytest reported no outcomes:\n{completed.stdout}\n{completed.stderr}"
        )
    return passed, total, other


def refuse_undeclared_gates(
    total: Mapping[int, int], titles: Mapping[int, str]
) -> None:
    """Raise unless every gate the tests name is declared somewhere.

    This is the failure the module exists to prevent, arriving from the side the
    backlog parser cannot see. ``gate_of`` attributes a test by its name alone,
    and the report iterates the *declared* numbers -- so a test named for a
    number nothing declares is counted into no row and excluded from the "not
    named for a gate" tally as well. It disappears. A criterion struck after its
    tests were written reaches here, and so does a typo in a test name.
    """
    undeclared = sorted(number for number in total if number not in titles)
    if not undeclared:
        return
    numbers = ", ".join(f"A{number}" for number in undeclared)
    raise SystemExit(
        f"tests are named for {numbers}, which neither {SPEC.name} §6 nor "
        f"{BACKLOG.name} declares. Declare the criterion or rename the test -- "
        "as it stands the report would drop it silently."
    )


def gate_statuses(
    titles: Mapping[int, str], post_freeze: frozenset[int], *, execute: bool
) -> tuple[list[GateStatus], int]:
    """Return one status per acceptance criterion, frozen and post-freeze alike.

    Takes the merged namespace rather than the two documents, because a
    criterion declared in ``docs/BACKLOG.md`` is as real as one declared in
    SPEC §6 and a status list built from §6 alone drops it without saying so.
    ``post_freeze`` is which of them came from the backlog, so A1-A24 can keep
    reading as the frozen contract they are.
    """
    if execute:
        passed, total, other = run_tests(ROOT)
    else:
        total, other = collect_tests(ROOT)
        passed = {}
    refuse_undeclared_gates(total, titles)
    return [
        GateStatus(
            number=number,
            title=titles[number],
            total=total.get(number, 0),
            passed=passed.get(number, 0) if execute else None,
            post_freeze=number in post_freeze,
        )
        for number in sorted(titles)
    ], other


def git_state(root: Path) -> tuple[str, list[str]]:
    """Return a ``branch @ head`` description and the porcelain status lines."""

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            # git speaks UTF-8; the Windows locale encoding does not, and a
            # commit subject carrying a section sign came back as mojibake.
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return completed.stdout.strip()

    head = git("log", "-1", "--format=%h %s")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = [line for line in git("status", "--porcelain").splitlines() if line]
    return f"{branch} @ {head}", dirty


#: SPEC §11 items set aside on a recorded decision, by item number.
#:
#: Distinct from "untracked". An untracked item has no A-gate, so this script
#: cannot say whether it is done; a deferred one *is* known not to be done and
#: is not waiting on anybody. Without the distinction item 1 reads as pending
#: work somebody forgot, which is the opposite of what was decided about it.
#:
#: Adding a number here is not how an item gets deferred. The decision is made
#: and written to ``docs/DECISIONS.md`` first, and this constant follows it, so
#: the reason is always one file away and never only a number in a script.
DEFERRED: Final[Mapping[int, str]] = {
    1: "2026-08-15 — item 1: the recorder is deferred",
}


def item_state(
    item: BacklogItem, gates: dict[int, GateStatus], *, execute: bool
) -> str:
    """Return a one-word state for a backlog item."""
    if item.number in DEFERRED:
        return "deferred"
    if not item.gates:
        return "untracked"
    statuses = [gates[number] for number in item.gates if number in gates]
    if not statuses:
        return "untracked"
    if execute:
        return "done" if all(status.green for status in statuses) else "open"
    return "written" if all(status.written for status in statuses) else "open"


def compress_ranges(numbers: list[int]) -> str:
    """Return a compact rendering of criterion numbers, e.g. "A6-A11, A16"."""
    if not numbers:
        return ""
    ordered = sorted(numbers)
    spans: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append((start, previous))
        start = previous = number
    spans.append((start, previous))
    return ", ".join(
        f"A{low}" if low == high else f"A{low}-A{high}" for low, high in spans
    )


def _print_gates(
    statuses: list[GateStatus], *, blocking: list[int], execute: bool
) -> None:
    """Print one block of gates, compressing the ones nothing is waiting on.

    Listing twenty-odd "absent" lines every session costs context and says
    nothing, so a gate with no tests appears only when the cursor is blocked on
    it; the rest are compressed to a single range.
    """
    for status in statuses:
        if not status.total and status.number not in blocking:
            continue
        if execute and status.total:
            detail = f"{status.passed}/{status.total} pass"
            if not status.green:
                detail = f"FAIL {detail}"
        elif status.total:
            detail = f"{status.total} test(s) written"
        else:
            detail = "next up, not yet written"
        print(f"  A{status.number:<3} {status.title:<{TITLE_WIDTH}}  {detail}")
    later = [
        status.number
        for status in statuses
        if not status.total and status.number not in blocking
    ]
    if later:
        print(f"  later: {compress_ranges(later)} ({len(later)} gates, not written)")


def report(*, execute: bool) -> int:
    """Print the status report; return a process exit code."""
    spec_text = SPEC.read_text(encoding="utf-8")
    backlog_text = BACKLOG.read_text(encoding="utf-8")
    entries = parse_backlog_entries(backlog_text)
    spec_titles = parse_gate_titles(spec_text)
    titles = merge_gate_titles(spec_titles, entries)
    declared_late = frozenset(titles) - frozenset(spec_titles)
    statuses, other = gate_statuses(titles, declared_late, execute=execute)
    gates = {status.number: status for status in statuses}
    backlog = parse_backlog(spec_text)
    description, dirty = git_state(ROOT)

    mode = "verified by execution" if execute else "collected, NOT executed"
    print(f"sciagent status — gates {mode}")
    print(f"  {description}")
    print(f"  {len(dirty)} uncommitted path(s)" if dirty else "  working tree clean")
    if not execute:
        print("  run `uv run python scripts/status.py --run` to verify by execution")
    print()

    print("SPEC §11 backlog")
    cursor: BacklogItem | None = None
    for item in backlog:
        state = item_state(item, gates, execute=execute)
        if cursor is None and state == "open":
            cursor = item
        covered = sum(1 for number in item.gates if gates.get(number) is not None)
        ready = sum(
            1
            for number in item.gates
            if number in gates
            and (gates[number].green if execute else gates[number].written)
        )
        gate_text = f"{ready}/{covered} gates" if item.gates else "no A-gate"
        if state == "deferred":
            gate_text = "deferred, see DECISIONS"
        marker = {
            "done": "x",
            "written": "~",
            "open": " ",
            "untracked": "?",
            "deferred": "-",
        }[state]
        print(
            f"  [{marker}] {item.number:>2}  "
            f"{item.title[:TITLE_WIDTH]:<{TITLE_WIDTH}}  {gate_text}"
        )
    print()

    # SPEC §11 is the build backlog; this is the one that continues it, so it
    # prints second and only takes the cursor when §11 has nothing open.
    print(f"{BACKLOG.parent.name}/{BACKLOG.name} gated entries")
    entry_cursor = (
        None if cursor is not None else backlog_cursor(entries, gates, execute=execute)
    )
    for entry in entries:
        marker, detail = entry_row(entry, gates.get(entry.gate), execute=execute)
        print(
            f"  [{marker}] {entry.rank:>2}  "
            f"{entry.short_title[:TITLE_WIDTH]:<{TITLE_WIDTH}}  {detail}"
        )
    ideas = len(markdown_sections(backlog_text)) - len(entries)
    print(f"       {ideas} ungated idea(s) in the same file, not build work")
    print()

    # Gates the cursor item is waiting on are named in full; the rest of the
    # unwritten space is compressed. Listing twenty-odd "absent" lines every
    # session costs context and says nothing.
    blocking: list[int] = []
    if cursor is not None:
        blocking = [
            number
            for number in cursor.gates
            if number not in gates
            or not (gates[number].green if execute else gates[number].written)
        ]
    elif entry_cursor is not None:
        blocking = [entry_cursor.gate]

    print("Acceptance gates")
    _print_gates(
        [status for status in statuses if not status.post_freeze],
        blocking=blocking,
        execute=execute,
    )
    post_freeze = [status for status in statuses if status.post_freeze]
    if post_freeze:
        print()
        print("Post-freeze gates (docs/BACKLOG.md, not SPEC §6)")
        _print_gates(post_freeze, blocking=blocking, execute=execute)
    if other:
        print(f"  (+{other} test(s) not named for a gate)")
    print()

    blocked_by = ", ".join(f"A{number}" for number in blocking)
    if cursor is not None:
        print(f"cursor: item {cursor.number} — {cursor.title}")
        print(f"        blocked on {blocked_by}")
    elif entry_cursor is not None:
        print(f"cursor: BACKLOG rank {entry_cursor.rank} — {entry_cursor.title}")
        print(f"        blocked on {blocked_by}")
    else:
        print(f"cursor: {no_open_summary(entries)}")
    for entry in entries:
        if entry.held is not None and not entry.closed:
            print(
                f"        BACKLOG rank {entry.rank} waits on {entry.held}, not on code"
            )
    untracked = [
        item.number
        for item in backlog
        if not item.gates and item.number not in DEFERRED
    ]
    if untracked:
        numbers = ", ".join(str(number) for number in untracked)
        print(f"        items {numbers} have no A-gate and are not tracked here")
    deferred = [item.number for item in backlog if item.number in DEFERRED]
    if deferred:
        numbers = ", ".join(str(number) for number in deferred)
        noun = "item" if len(deferred) == 1 else "items"
        print(f"        {noun} {numbers} deferred on a recorded decision, not pending")

    failing = [status for status in statuses if status.total and not status.green]
    return 1 if execute and failing else 0


def main() -> int:
    # The spec's section signs and dashes are not encodable in the Windows
    # console default, and this report is consumed by tools that expect UTF-8.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="execute the suite and report verified pass/fail rather than coverage",
    )
    arguments = parser.parse_args()
    return report(execute=bool(arguments.run))


if __name__ == "__main__":
    raise SystemExit(main())
