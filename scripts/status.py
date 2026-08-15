"""Report where the build stands, derived entirely from the repository.

Prints the SPEC §11 cursor, per-gate acceptance coverage and working-tree state.
Every figure comes from the spec text, from pytest or from git; nothing in the
report is hand-maintained, so it cannot drift from the code it describes.

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

#: ``- **A1** Determinism: ...`` and ``- **A6 Likelihood estimation.** ...``
GATE_DEFINITION = re.compile(r"^-\s+\*\*A(\d+)\b(.*)$")
#: A gate range such as "A6 to A11". The spec writes these with an en dash, so
#: all three separators are accepted; the dashes are escaped rather than typed
#: literally because they are visually ambiguous with a hyphen.
DASHES = f"{chr(0x2013)}{chr(0x2014)}-"  # en dash, em dash, hyphen
GATE_RANGE = re.compile(rf"A(\d+)\s*[{DASHES}]\s*A?(\d+)")
GATE_SINGLE = re.compile(r"A(\d+)")
#: Every acceptance test is named for its criterion; that naming is the tag.
TEST_GATE = re.compile(r"^test_a0*(\d+)_")
BACKLOG_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|\s*$")
#: ``PASSED tests/acceptance/test_a01_a05.py::TestA5Descendants::test_a5_x``
OUTCOME_LINE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+)")

TITLE_WIDTH = 44


@dataclass(frozen=True)
class BacklogItem:
    """One row of the SPEC §11 ordered build backlog."""

    number: int
    title: str
    gates: tuple[int, ...]


@dataclass(frozen=True)
class GateStatus:
    """Test coverage for one acceptance criterion."""

    number: int
    title: str
    total: int
    passed: int | None

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


def gate_statuses(spec_text: str, *, execute: bool) -> tuple[list[GateStatus], int]:
    """Return one status per acceptance criterion named in the spec."""
    titles = parse_gate_titles(spec_text)
    if execute:
        passed, total, other = run_tests(ROOT)
    else:
        total, other = collect_tests(ROOT)
        passed = {}
    return [
        GateStatus(
            number=number,
            title=titles[number],
            total=total.get(number, 0),
            passed=passed.get(number, 0) if execute else None,
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


def report(*, execute: bool) -> int:
    """Print the status report; return a process exit code."""
    spec_text = SPEC.read_text(encoding="utf-8")
    statuses, other = gate_statuses(spec_text, execute=execute)
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

    print("Acceptance gates")
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
    if other:
        print(f"  (+{other} test(s) not named for a gate)")
    print()

    if cursor is None:
        print("cursor: every gate-tracked backlog item is satisfied")
    else:
        blocked_by = ", ".join(f"A{number}" for number in blocking)
        print(f"cursor: item {cursor.number} — {cursor.title}")
        print(f"        blocked on {blocked_by}")
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
