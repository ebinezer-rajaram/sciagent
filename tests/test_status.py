"""The status report's second backlog: ``docs/BACKLOG.md``'s gated entries.

Nothing here is named ``test_aN_``. This is tooling, not a SPEC §6 criterion,
and crediting it to a gate would tell ``scripts/status.py`` that a criterion
covers work no criterion claims.

What these check: that ``docs/BACKLOG.md``'s gated entries -- the sixteen the
2026-08-18 review appended, and the ones added since -- are a *tracked, ordered*
backlog rather than prose. Three things have to hold
for that. The parser must survive the file's real shape -- a fenced example
entry, headers wrapped across two ``##`` lines, closed entries, ungated ideas.
The cursor must follow ``**Rank.**`` rather than file position or gate number,
because in the real file those three orders disagree. And a gate number the spec
does not declare must reach the report instead of vanishing: ``gate_of`` already
attributes ``test_a26_`` to gate 26, but before this change ``gate_statuses``
iterated only the §6 titles, so such a test was counted into no visible row and
excluded from the "not named for a gate" tally as well.

``scripts/`` is not on ``sys.path`` and is not a package. ``tests/test_report.py``
faced the same wall and chose a subprocess, because it wanted argparse and the
exit status. The surface here is pure functions, and a subprocess would run a
nested pytest collection for every case -- so this loads the module by path.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "status.py"
BACKLOG = ROOT / "docs" / "BACKLOG.md"

#: The heading the report gives its second backlog, and one rendered row of
#: it: marker, rank, title. The SPEC §11 rows share the shape, which is why
#: :func:`_section` slices before matching.
BACKLOG_SECTION = "docs/BACKLOG.md gated entries"
ROW = r"^  \[.\]\s+(\d+)\s"
GATES_SECTION = "Acceptance gates"


def _written(rendered: str) -> set[int]:
    """Return the gates the rendered report says have tests, by number."""
    return {
        int(n)
        for n in re.findall(r"^  A(\d+)\s+.*\d+ test\(s\) written", rendered, re.M)
    }


def _section(rendered: str) -> str:
    """Return the backlog block alone, from its heading to the gate block."""
    start = rendered.index(BACKLOG_SECTION)
    return rendered[start : rendered.index(GATES_SECTION, start)]


def _load() -> ModuleType:
    """Return ``scripts/status.py`` as a module, loaded by path."""
    spec = importlib.util.spec_from_file_location("sciagent_status", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


status = _load()


def _entry(
    title: str,
    *,
    rank: int | None,
    gate: int | None,
    held: str | None = None,
    closure: str | None = None,
    wrapped: bool = False,
) -> str:
    """Render one BACKLOG section in the file's own field format.

    ``closure`` is ``DONE`` or ``STRUCK``; ``wrapped`` splits the heading over
    two ``##`` lines, as the real file does when a title runs long. Both are
    rendered *above* the fields, so a parser that splits naively at every ``##``
    attributes this entry's gate and rank to the heading's second half.
    """
    heading = f"{closure} (2026-08-16) — {title}" if closure else title
    if wrapped:
        head, _, tail = heading.rpartition(" ")
        opening = [f"## {head}", f"## {tail}"]
    else:
        opening = [f"## {heading}"]
    lines = [*opening, "", "**Idea.** Something.", "**Touches.** None."]
    if gate is not None:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        lines.append(f"**Gate.** `test_a{gate}_{slug}` — it holds.")
    if rank is not None:
        lines.append(f"**Rank.** {rank}")
    if held is not None:
        lines.append(f"**Held.** {held}")
    lines.append("**Cost.** S.")
    return "\n".join(lines) + "\n"


#: Every shape the real file has, with file order, gate order and rank order
#: deliberately in conflict: the first gated section on the page carries the
#: lowest gate number and the *highest* rank. Both closed entries carry a gate
#: and a rank, because that is what a landed entry looks like -- the fields stay
#: and the heading gains a marker.
FIXTURE = (
    "# Backlog\n\n"
    "Entry format:\n\n"
    "```\n"
    "## Short title\n\n"
    "**Idea.** What it is.\n"
    "**Gate.** `test_a99_the_example_is_not_an_entry`\n"
    "**Rank.** 1\n"
    "```\n\n"
    "---\n\n"
    "## An idea with no gate, which is not tracked\n\n"
    "**Idea.** Just an idea.\n\n"
    + _entry("Third to build, first on the page", rank=30, gate=25)
    + "\n"
    + _entry(
        "A landed entry whose heading wraps onto a second line",
        rank=2,
        gate=38,
        closure="DONE",
        wrapped=True,
    )
    + "\n"
    + _entry("Second to build", rank=20, gate=41)
    + "\n"
    + _entry("Struck entries are closed too", rank=3, gate=37, closure="STRUCK")
    + "\n"
    + _entry(
        "Held on a decision that is the user's",
        rank=5,
        gate=42,
        held="OPEN-DECISIONS §1",
    )
    + "\n"
    + _entry("First to build", rank=10, gate=40)
)


def _gates(
    *, written: frozenset[int] = frozenset(), green: frozenset[int] = frozenset()
) -> dict[int, Any]:
    """Return a gate-number to :class:`GateStatus` map for the numbers named."""
    numbers = sorted(written | green)
    return {
        number: status.GateStatus(
            number=number,
            title=f"A{number}",
            total=1 if number in written | green else 0,
            passed=1 if number in green else 0,
        )
        for number in numbers
    }


class TestParsingTheGatedEntries:
    """``parse_backlog_entries`` against the shapes the real file contains."""

    def test_only_sections_carrying_a_gate_are_tracked(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        titles = [entry.title for entry in entries]
        assert "An idea with no gate, which is not tracked" not in titles
        assert len(entries) == 6

    def test_the_entries_come_back_in_rank_order(self) -> None:
        ranks = [entry.rank for entry in status.parse_backlog_entries(FIXTURE)]
        assert ranks == sorted(ranks)

    def test_a_fenced_example_entry_is_not_parsed_as_an_entry(self) -> None:
        """The file documents its own format in a fence, gate field included."""
        gates = {entry.gate for entry in status.parse_backlog_entries(FIXTURE)}
        assert 99 not in gates

    def test_a_header_wrapped_over_two_lines_is_one_header(self) -> None:
        """A naive ``##`` split takes the second line as a section of its own.

        The wrapped entry's gate and rank sit below *both* heading lines, so the
        phantom carries them and the real entry loses them -- which is why this
        entry has to be gated for the check to discriminate at all.
        """
        by_rank = {e.rank: e for e in status.parse_backlog_entries(FIXTURE)}
        assert "line" not in {entry.title for entry in by_rank.values()}
        assert by_rank[2].title == (
            "DONE (2026-08-16) — A landed entry whose heading wraps onto a second line"
        )

    def test_both_closure_markers_close_an_entry_that_keeps_its_gate(self) -> None:
        """An entry stays in the file when it lands; the heading gains a marker."""
        by_rank = {e.rank: e for e in status.parse_backlog_entries(FIXTURE)}
        assert {rank for rank, e in by_rank.items() if e.closed} == {2, 3}
        assert by_rank[2].gate == 38  # DONE
        assert by_rank[3].gate == 37  # STRUCK

    def test_the_gate_and_its_test_name_are_read_off_the_gate_line(self) -> None:
        by_rank = {e.rank: e for e in status.parse_backlog_entries(FIXTURE)}
        assert by_rank[10].gate == 40
        assert by_rank[10].test_name == "test_a40_first_to_build"

    def test_a_held_entry_names_what_holds_it(self) -> None:
        held = [e for e in status.parse_backlog_entries(FIXTURE) if e.held is not None]
        assert [entry.held for entry in held] == ["OPEN-DECISIONS §1"]

    def test_a_gated_entry_without_a_rank_is_an_error(self) -> None:
        """Rank is the build order; a gated entry without one has no position."""
        text = _entry("Gated but unranked", rank=None, gate=25)
        with pytest.raises(SystemExit, match="no \\*\\*Rank\\.\\*\\*"):
            status.parse_backlog_entries(text)

    def test_two_entries_at_one_rank_is_an_error(self) -> None:
        text = _entry("One", rank=3, gate=25) + "\n" + _entry("Two", rank=3, gate=26)
        with pytest.raises(SystemExit, match="rank 3"):
            status.parse_backlog_entries(text)


class TestTheCursorFollowsRank:
    """File position and gate number are both wrong answers here."""

    def test_the_cursor_is_the_lowest_rank_not_the_first_on_the_page(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        cursor = status.backlog_cursor(entries, _gates(), execute=False)
        assert cursor is not None
        assert cursor.title == "First to build"
        assert cursor.rank == 10

    def test_the_cursor_is_not_the_lowest_gate_number(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        cursor = status.backlog_cursor(entries, _gates(), execute=False)
        assert cursor is not None
        assert cursor.gate == 40

    def test_a_held_entry_is_skipped_even_at_the_lowest_rank(self) -> None:
        """Rank 5 is held on a decision only the user can take."""
        entries = status.parse_backlog_entries(FIXTURE)
        cursor = status.backlog_cursor(entries, _gates(), execute=False)
        assert cursor is not None
        assert cursor.held is None

    def test_an_entry_whose_gate_has_tests_is_done_without_run(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        cursor = status.backlog_cursor(
            entries, _gates(written=frozenset({40})), execute=False
        )
        assert cursor is not None
        assert cursor.rank == 20

    def test_written_is_not_green_under_run(self) -> None:
        """``--run`` demands passing tests; existence is not evidence."""
        entries = status.parse_backlog_entries(FIXTURE)
        cursor = status.backlog_cursor(
            entries, _gates(written=frozenset({40})), execute=True
        )
        assert cursor is not None
        assert cursor.rank == 10

    def test_the_cursor_is_none_when_every_unheld_entry_is_covered(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        gates = _gates(green=frozenset({25, 40, 41}))
        assert status.backlog_cursor(entries, gates, execute=True) is None


class TestTheGateNamespaceExtends:
    """A number SPEC does not declare must reach the report, not vanish."""

    def test_a_backlog_gate_gains_a_title(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        titles = status.merge_gate_titles({1: "Determinism"}, entries)
        assert set(titles) == {1, 25, 38, 40, 41, 42}

    def test_the_title_is_derived_from_the_test_not_the_heading(self) -> None:
        """Restating it in two places is how the two come to disagree.

        The heading is also the wrong source: a landed entry's heading opens
        with ``DONE (date, gate ANN) —``, so a report keyed on it would spend
        its width on provenance.
        """
        entries = status.parse_backlog_entries(FIXTURE)
        titles = status.merge_gate_titles({}, entries)
        assert titles[40] == "first to build"
        assert (
            titles[38]
            == (
                "a landed entry whose heading wraps onto a second line"[
                    : status.TITLE_WIDTH
                ]
            )
        )

    def test_a_withdrawn_criterion_is_not_in_the_namespace(self) -> None:
        """STRUCK keeps its gate line as a record of what was proposed.

        Nothing is ever going to satisfy it, so it is not a criterion: giving
        it a title would put a permanently empty row in the gate report and
        would reserve the number against an entry that could still use it.
        """
        entries = status.parse_backlog_entries(FIXTURE)
        struck = [entry for entry in entries if entry.struck]
        assert [entry.gate for entry in struck] == [37]
        assert 37 not in status.merge_gate_titles({}, entries)

    def test_a_withdrawn_entry_is_not_a_missing_gate(self) -> None:
        """It has no gate row, so it has no evidence to fall short of.

        The DONE branch renders "marked landed but no tests" as a discrepancy,
        which is right for work claimed to be done. Reaching that branch for
        STRUCK would demand tests for a decision to build nothing.
        """
        entry = next(e for e in status.parse_backlog_entries(FIXTURE) if e.struck)
        marker, detail = status.entry_row(entry, None, execute=False)
        assert marker == "-"
        assert "withdrawn" in detail and "no tests" not in detail

    def test_a_closed_entrys_row_drops_its_provenance_prefix(self) -> None:
        """``DONE (2026-08-16) — title`` renders as ``title``.

        The date carries two hyphens, so a separator rule that accepts one cuts
        the heading inside the parenthetical and renders the provenance instead
        of the name. Only the en and em dashes separate here.
        """
        entries = status.parse_backlog_entries(FIXTURE)
        landed = next(entry for entry in entries if entry.gate == 38)
        assert landed.title.startswith("DONE (2026-08-16) — ")
        assert landed.short_title == (
            "A landed entry whose heading wraps onto a second line"
        )

    def test_an_open_entrys_row_is_its_heading_unchanged(self) -> None:
        entries = status.parse_backlog_entries(FIXTURE)
        open_entry = next(entry for entry in entries if entry.gate == 40)
        assert open_entry.short_title == open_entry.title == "First to build"

    def test_a_gate_number_the_spec_already_defines_is_an_error(self) -> None:
        entries = status.parse_backlog_entries(_entry("Clash", rank=1, gate=7))
        with pytest.raises(SystemExit, match="A7"):
            status.merge_gate_titles({7: "Monte Carlo error"}, entries)

    def test_two_entries_claiming_one_gate_is_an_error(self) -> None:
        text = _entry("One", rank=1, gate=25) + "\n" + _entry("Two", rank=2, gate=25)
        entries = status.parse_backlog_entries(text)
        with pytest.raises(SystemExit, match="A25"):
            status.merge_gate_titles({}, entries)


class TestTheRealBacklogFile:
    """The fixture proves the parser; this proves the file it has to parse."""

    def test_the_review_entries_are_all_tracked(self) -> None:
        """The sixteen the 2026-08-18 review appended are a floor, not a total.

        Promoting an idea to a gated entry is the documented workflow of this
        file, so an exact count here would fail on correct work. What must hold
        is that none of the sixteen is lost and that the namespace stays below
        SPEC §6 nowhere.
        """
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        gates = {entry.gate for entry in entries}
        assert gates >= set(range(25, 41))
        assert min(gates) == 25

    def test_every_rank_is_distinct_and_the_cursor_is_well_formed(self) -> None:
        """Ranks are unique, and the cursor is either open work or nothing left.

        **This asserted ``cursor is not None`` until 2026-08-26**, when gate A46
        closed the last gated entry and the assumption expired. ``None`` is not a
        failure and never was: :func:`status.backlog_cursor` documents it as
        "``None`` if none is open", and :func:`status.cursor_line` renders it as
        *"every gate-tracked backlog item is satisfied"*. A test asserting the
        backlog is never finished would have to fail the moment it was.

        What is worth holding is that a resolved cursor is open and unheld, and
        that an unresolved one is not a parse failure wearing the same face.

        **The first replacement for the old line could not fail**, and
        ``/code-review`` said so: it re-derived
        :func:`status.backlog_cursor`'s own skip condition from the same
        ``entries`` and ``gates`` it had just passed in, so it was true by
        construction for any input on which that function returns ``None``.

        The second replacement was worse, and is worth recording because it
        looked stronger. It asserted that every entry marked ``DONE`` has a gate
        with tests -- a real property, and one nothing else checks over the real
        file -- but :func:`_gates` here returns an **empty** map by design, so it
        failed on all twenty-two closed entries at once. A check is only
        independent if the fixture can express what it is checking.

        What is actually independent, given an empty gate map, is the **ordering**
        guarantee: :func:`status.backlog_cursor` documents "lowest rank first",
        and every open entry satisfies its skip condition equally, so landing on
        the wrong one is a failure the condition cannot describe.
        """
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        assert len({entry.rank for entry in entries}) == len(entries)
        gates = _gates()
        cursor = status.backlog_cursor(entries, gates, execute=False)

        # A backlog that parsed to nothing also produces `None`, and that is a
        # parse failure wearing a finished backlog's face.
        assert entries, "the real BACKLOG parsed to no gated entries at all"

        buildable = [
            entry for entry in entries if not entry.closed and entry.held is None
        ]
        if not buildable:
            assert cursor is None
            return

        # The ordering guarantee, which is the part not already implied by the
        # skip condition: `backlog_cursor` promises "lowest rank first", so a
        # cursor landing on any other open entry is wrong even though every
        # open entry satisfies the same predicate. This is what fails if the
        # iteration stops trusting `**Rank.**`.
        assert cursor is not None
        assert not cursor.closed and cursor.held is None
        assert cursor.rank == min(entry.rank for entry in buildable), (
            f"cursor resolved to rank {cursor.rank} while rank "
            f"{min(entry.rank for entry in buildable)} is open and unheld"
        )

    def test_every_gate_line_names_a_test_for_its_own_gate(self) -> None:
        """``status.py`` attributes a test by its name, not by the entry it sits in."""
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        mismatched = [
            (entry.gate, entry.test_name)
            for entry in entries
            if status.gate_of(entry.test_name) != entry.gate
        ]
        assert mismatched == []

    def test_the_spec_and_the_backlog_do_not_claim_one_gate_twice(self) -> None:
        spec_titles = status.parse_gate_titles(status.SPEC.read_text(encoding="utf-8"))
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        merged = status.merge_gate_titles(spec_titles, entries)
        live = {entry.gate for entry in entries if not entry.struck}
        assert set(merged) == set(spec_titles) | live
        assert set(spec_titles) == set(range(1, 25))


@pytest.fixture(scope="module")
def rendered() -> str:
    """The default-mode report, run once for the whole class.

    ``report`` collects the suite in a child process, so this costs a few
    seconds; the assertions below are cheap and there is no reason to pay it
    per test.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = status.report(execute=False)
    assert code == 0
    return buffer.getvalue()


class TestTheReportRendersIt:
    """The deliverable is the printed report, not the three functions alone.

    Everything above passes against an implementation that adds the parser and
    never calls it from ``report``. That implementation prints the same report
    it prints today: no second backlog, no backlog cursor, and A25-A40 still
    reaching no row -- which is the whole defect. These assertions are what
    distinguishes it.
    """

    def test_the_backlog_section_follows_the_spec_one(self, rendered: str) -> None:
        assert "SPEC §11 backlog" in rendered
        assert BACKLOG_SECTION in rendered
        assert rendered.index("SPEC §11 backlog") < rendered.index(BACKLOG_SECTION)

    def test_every_entry_renders_once_in_rank_order(self, rendered: str) -> None:
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        ranks = [int(rank) for rank in re.findall(ROW, _section(rendered), re.M)]
        assert ranks == sorted(entry.rank for entry in entries)

    def test_the_cursor_names_a_real_open_backlog_entry(self, rendered: str) -> None:
        """§11 is satisfied, so the cursor falls through to this backlog.

        Deliberately not pinned to rank 1: the whole point of the cursor is that
        it advances, and the next thing anyone does here is write ``test_a26_``,
        which moves it to rank 2. A test that has to be edited to let correct
        work go green is a test that will be edited without being read.

        The cursor has **three** legal forms and this covers all of them, which it
        did not when the backlog still had an open entry to land on. Once every
        entry is closed, held or gate-covered, :func:`status.backlog_cursor`
        returns ``None`` and :func:`status.no_open_summary` speaks instead -- and
        it distinguishes *satisfied* from *blocked on somebody*, which is the
        whole reason it exists. Asserting only the naming form would leave both
        exhausted branches unchecked, so a report announcing "nothing open" over
        a genuinely open entry would go green.
        """
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        cursor = re.search(r"^cursor: BACKLOG rank (\d+) — (.+)$", rendered, re.M)
        if cursor is None:
            held = [e for e in entries if e.held is not None and not e.closed]
            exhausted = re.search(
                r"^cursor: nothing open; (\d+) BACKLOG entr(?:y|ies) held on a "
                r"decision$",
                rendered,
                re.M,
            )
            if exhausted is None:
                # The other exhausted form: nothing is held either. Matching only
                # the held one would fail on a correct report the day ranks 20 and
                # 21 are decided, which is precisely the edit-to-go-green trap.
                assert held == [], (
                    f"the cursor claims everything is satisfied, but rank(s) "
                    f"{[e.rank for e in held]!r} are held on a decision"
                )
                assert re.search(
                    r"^cursor: every gate-tracked backlog item is satisfied$",
                    rendered,
                    re.M,
                ), rendered
            else:
                assert len(held) == int(exhausted.group(1))
            open_entries = [
                entry
                for entry in entries
                if not entry.closed
                and entry.held is None
                and entry.gate not in _written(rendered)
            ]
            assert not open_entries, (
                f"the cursor says nothing is open, but rank(s) "
                f"{[e.rank for e in open_entries]!r} are neither closed, held, "
                f"nor gate-covered"
            )
            return
        named = {entry.rank: entry for entry in entries}[int(cursor.group(1))]
        assert named.title == cursor.group(2)
        assert not named.closed and named.held is None
        assert re.search(rf"^ +blocked on A{named.gate}$", rendered, re.M) is not None
        earlier = [e for e in entries if e.rank < named.rank]
        assert all(
            e.closed or e.held is not None or e.gate in _written(rendered)
            for e in earlier
        )

    def test_every_backlog_gate_is_named_in_the_gate_section(
        self, rendered: str
    ) -> None:
        """Every gate this backlog declares reaches the gate section somehow.

        This is the namespace fix: before it, a gate outside SPEC §6 reached no
        row at all and was left out of the "not named for a gate" tally too.

        It used to ask this of the cursor's gate alone, which stopped being
        answerable once the cursor ran out of entries to name -- and was the
        weaker question anyway, since it left every *other* post-freeze gate
        unchecked. A gate with no test is compressed onto the ``later:`` line
        rather than given a row, so both placements count; what the namespace fix
        rules out is a gate appearing in neither.

        The ``later:`` line is **range-compressed** by
        :func:`status.compress_ranges`, so it is expanded here rather than
        searched. ``"A46" in "A45-A47"`` is false and ``"A4" in "A41"`` is true,
        and a substring test would therefore both miss a gate in the interior of
        a span and accept one that is only a numeric prefix of another.
        """
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        later = re.search(r"^  later: (.+?)(?: \(|$)", rendered, re.M)
        deferred: set[int] = set()
        for low, high in re.findall(
            r"A(\d+)(?:-A(\d+))?", later.group(1) if later else ""
        ):
            deferred.update(range(int(low), int(high or low) + 1))
        for entry in entries:
            row = re.search(rf"^  A{entry.gate}\s+\S", rendered, re.M)
            assert row is not None or entry.gate in deferred, (
                f"gate A{entry.gate} (rank {entry.rank}) reaches neither a row "
                f"nor the deferred line, so the report renders it nowhere"
            )

    def test_every_held_entry_names_what_it_waits_on(self, rendered: str) -> None:
        """A hold that does not say what it waits on is a row nobody can act on.

        Derived from the live file rather than naming one holder. Holds come
        off as the decisions behind them are taken -- A29's did, on 2026-08-21,
        when OPEN-DECISIONS §1 was settled -- and an assertion pinned to a
        particular holder string fails on the decision rather than on the
        behaviour it means to guard. Counting both ways keeps it biting when
        nothing is held: a row rendered as held that the file does not hold
        fails the second assertion.
        """
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        held = [e for e in entries if e.held is not None and not e.closed]
        section = _section(rendered)
        for entry in held:
            assert f"A{entry.gate}, held: {entry.held}" in section
        assert section.count(", held: ") == len(held)


class TestAMalformedFileIsAnError:
    """The parser skips fenced blocks, so an unclosed fence hides everything."""

    def test_an_unclosed_fence_is_refused_rather_than_swallowing_entries(
        self,
    ) -> None:
        text = FIXTURE + "\n```\nan example nobody closed\n"
        with pytest.raises(SystemExit, match="unclosed"):
            status.parse_backlog_entries(text)

    def test_a_fence_between_two_headings_does_not_merge_them(self) -> None:
        """Skipping a fence must not make the headings either side adjacent.

        Consecutive ``##`` lines are one wrapped heading, and a fence in between
        is not consecutive. Merging them costs the second entry entirely: one
        section holds two gate lines and only the first is read.
        """
        text = (
            "## Alpha\n"
            "```\nan example straight under the heading\n```\n"
            "## Beta\n\n"
            "**Gate.** `test_a26_beta`\n**Rank.** 2\n"
        )
        entries = status.parse_backlog_entries(text)
        assert [(entry.rank, entry.title) for entry in entries] == [(2, "Beta")]
        assert [heading for heading, _ in status.markdown_sections(text)] == [
            "Alpha",
            "Beta",
        ]


class TestAClosedEntryDoesNotClaimEvidence:
    """``DONE`` is an author's marker; it is not a test result.

    The module guarantees it "never claims a gate passes, only that tests for
    it exist". A heading marker is not evidence of either, so it may order the
    backlog without being allowed to speak for the gate.
    """

    def test_a_landed_entry_with_no_tests_is_not_rendered_as_a_pass(self) -> None:
        entry = status.BacklogEntry(
            rank=1, title="T", gate=25, test_name="test_a25_x", held=None, closed=True
        )
        marker, detail = status.entry_row(entry, None, execute=False)
        assert marker != "x"
        assert "no tests" in detail

    def test_a_landed_entry_with_tests_is_rendered_as_landed(self) -> None:
        entry = status.BacklogEntry(
            rank=1, title="T", gate=25, test_name="test_a25_x", held=None, closed=True
        )
        gate = status.GateStatus(number=25, title="T", total=2, passed=None)
        marker, detail = status.entry_row(entry, gate, execute=False)
        assert marker == "x"
        assert "landed" in detail

    def test_a_failing_gate_is_never_rendered_as_passing(self) -> None:
        entry = status.BacklogEntry(
            rank=1, title="T", gate=25, test_name="test_a25_x", held=None, closed=False
        )
        gate = status.GateStatus(number=25, title="T", total=3, passed=1)
        marker, detail = status.entry_row(entry, gate, execute=True)
        assert marker != "x"
        assert "1/3" in detail

    def test_a_landed_entry_that_kept_its_held_field_still_reads_as_landed(
        self,
    ) -> None:
        """``**Held.**`` is stale the moment the decision is taken and it lands.

        The file's own convention is that an entry keeps its fields when it
        lands and gains only a heading marker, so this is the documented path
        rather than an abuse of one. Every other reader of ``held`` guards it
        with ``and not closed``; this one did not, so a held entry that landed
        green would have reported as blocked while the cursor summary below it
        said nothing was held.
        """
        entry = status.BacklogEntry(
            rank=3,
            title="DONE (2026-08-20, gate A29) — the decision was taken",
            gate=29,
            test_name="test_a29_x",
            held="OPEN-DECISIONS §1",
            closed=True,
        )
        gate = status.GateStatus(number=29, title="T", total=4, passed=4)
        marker, detail = status.entry_row(entry, gate, execute=True)
        assert marker == "x"
        assert "landed" in detail and "held" not in detail


class TestATestForAnUndeclaredGateIsRefused:
    """The vanishing bug, arriving from the side the parser cannot see.

    ``gate_of`` attributes a test by its name alone, and ``gate_statuses``
    iterates the declared numbers. So a test named for a number *nothing*
    declares is counted into no row and excluded from the "not named for a
    gate" tally as well -- which is the exact failure this module was changed
    to close, reached by writing the test rather than by omitting the entry.
    """

    def test_a_gate_with_tests_that_nothing_declares_is_an_error(self) -> None:
        with pytest.raises(SystemExit, match="A99"):
            status.refuse_undeclared_gates({1: 3, 99: 2}, {1: "Determinism"})

    def test_a_declared_gate_with_no_tests_is_not_an_error(self) -> None:
        """Unwritten is the normal state of a gate; only unattributable is not."""
        status.refuse_undeclared_gates({1: 3}, {1: "Determinism", 99: "Later"})

    def test_the_real_tree_declares_every_gate_it_names_a_test_for(self) -> None:
        """The guard above is worth nothing if the repository already trips it."""
        total, _ = status.collect_tests(status.ROOT)
        entries = status.parse_backlog_entries(BACKLOG.read_text(encoding="utf-8"))
        spec = status.parse_gate_titles(status.SPEC.read_text(encoding="utf-8"))
        declared = status.merge_gate_titles(spec, entries)
        assert sorted(set(total) - set(declared)) == []


class TestFieldValidationIsSymmetric:
    """A field that fails to parse must say so, not reclassify the entry."""

    def test_a_rank_without_a_parseable_gate_is_an_error(self) -> None:
        """``**Gate:**`` for ``**Gate.**`` would otherwise demote it to an idea."""
        text = "## Typo\n\n**Gate:** `test_a25_x` — mistyped.\n**Rank.** 1\n"
        with pytest.raises(SystemExit, match="Rank"):
            status.parse_backlog_entries(text)

    def test_two_gate_lines_in_one_section_is_an_error(self) -> None:
        """Two adjacent headings merge, and the second entry would vanish."""
        text = (
            "## Alpha\n## Beta\n\n"
            "**Gate.** `test_a25_alpha`\n**Rank.** 1\n"
            "**Gate.** `test_a26_beta`\n**Rank.** 2\n"
        )
        with pytest.raises(SystemExit, match=r"two \*\*Gate\.\*\*"):
            status.parse_backlog_entries(text)

    def test_a_held_value_wrapped_over_two_lines_is_read_whole(self) -> None:
        text = (
            "## Wrapped\n\n**Gate.** `test_a25_x`\n**Rank.** 1\n"
            "**Held.** OPEN-DECISIONS §1, which has to be taken\n"
            "cold and is the user's alone\n"
        )
        (entry,) = status.parse_backlog_entries(text)
        assert entry.held is not None
        assert entry.held.endswith("the user's alone")


class TestHeldWorkIsNotSatisfiedWork:
    """ "Everything is satisfied" and "everything left is blocked" differ."""

    def test_only_held_entries_left_does_not_read_as_satisfied(self) -> None:
        entries = [
            status.BacklogEntry(
                rank=1,
                title="T",
                gate=25,
                test_name="test_a25_x",
                held="OPEN-DECISIONS §1",
                closed=False,
            )
        ]
        assert status.backlog_cursor(entries, {}, execute=False) is None
        message = status.no_open_summary(entries)
        assert "satisfied" not in message
        assert "held" in message

    def test_nothing_held_and_nothing_open_reads_as_satisfied(self) -> None:
        assert "satisfied" in status.no_open_summary([])
