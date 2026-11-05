"""SPEC §2.2's reporting in the pilot report: red team finding 3 of
``docs/v2/redteam/2026-11-04_pilot-md.md``.

The SPEC requires the certified share, the share of fits flagged "certified in
θ, coordinate-optimal in ψ" beside it, and uncertified fits "flagged, never
silently used". A score cell's ``certified`` reading has four states, and only
two of them are a certificate's answer: 1 and 0. NaN says the model is not a
GLM fit (B-np, or B-lib when an out-of-grammar member won), and a cell without
the field says nothing, so neither may enter a share, least of all as
certified.

No score cell stores ``psi_search``, but it is exact from what they do store:
``fit`` branches on the grid size alone, and only ORACLE's truth-ψ refit fixes
ψ. The tests below hold that recomputation to real ``fit`` calls.

The report is rendered through ``main()`` as in ``test_pilot_report_wording``:
only the store reads are replaced. The verdict and its numbers come from the
real ``analysis.verdict``, and none of them may move with the new lines.
"""

from __future__ import annotations

import importlib.util
import json
import math
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import numpy as np
import pytest
from campaign_support import entry, environment, tiny_config
from test_campaign_analysis import row
from test_pilot_report_counts import _counts, _put
from test_pilot_report_wording import SEEDS, TRUTHS, _render, _table
from test_pilot_search_misses import psm, structure_of, toy_data

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Session, Table, derive
from sciagent.campaign.plan import (
    CampaignError,
    baseline_key,
    baseline_units,
    llm_units,
    score_key,
    session_key,
)
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus
from sciagent.glm.fit import PSI_FULL_GRID_MAX, FitConfig, _Slots, fit
from sciagent.glm.syntax import render

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
ARM = "AG-c-S/named"
OPEN = "AG-o-S/named"
CHANNELS = environment().channels
#: A structure on 12 ψ points, and one on 6**4 = 1,296.
TEXT = "link=exp; Excite(GammaK, Mark(size), all)"
LARGE = "link=exp; " + " + ".join(["Excite(ExpK, Mark(size), all)"] * 4)

type Key = tuple[str, int, str]


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` loaded in-process, with ``scripts/`` importable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "pilot_report", SCRIPTS / "pilot_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cell(certified: float | None = 1.0, source: str | None = None) -> dict[str, Any]:
    """A score cell as ``store.cell`` gives it: the reading in hex."""
    reading = {"status": (1.0).hex()}
    if certified is not None:
        reading["certified"] = certified.hex()
    result = {} if source is None else {"oracle_psi_source": source}
    return {"reading": reading, "detail": {"result": result}}


def fitted(seed: int, system: str = "A", text: str = TEXT) -> Row:
    """A row with a stored structure, as every GLM fit's row has."""
    return replace(row("t", seed, system, -0.7), structure=text)


def key(r: Row) -> Key:
    return (r.truth, r.seed, r.system)


def body(lines: list[str]) -> dict[str, dict[str, str]]:
    """The section's table, by system, each row by column header."""
    cells = [
        [c.strip() for c in line.strip("|").split("|")]
        for line in lines
        if line.startswith("| ")
    ]
    header, *rest = cells
    return {r[0]: dict(zip(header, r, strict=True)) for r in rest}


def section(
    report: ModuleType,
    rows: list[Row],
    cells: dict[Key, dict[str, Any]],
    systems: tuple[str, ...] = ("A", "B"),
) -> list[str]:
    flags = report.fit_flags(rows, cells, CHANNELS, PSI_FULL_GRID_MAX)
    lines = report.certification_lines(systems, rows, flags, PSI_FULL_GRID_MAX)
    assert isinstance(lines, list)
    return lines


# --------------------------------------------------------------------------
# The states of one reading
# --------------------------------------------------------------------------


def test_a_reading_has_four_states_and_nothing_else(report: ModuleType) -> None:
    assert report.certified_state(cell(1.0)["reading"]) == report.CERTIFIED
    assert report.certified_state(cell(0.0)["reading"]) == report.UNCERTIFIED
    assert report.certified_state(cell(math.nan)["reading"]) == report.NOT_A_FIT
    assert report.certified_state(cell(None)["reading"]) == report.UNRECORDED
    with pytest.raises(CampaignError, match="certified"):
        report.certified_state(cell(0.5)["reading"])


def test_a_certificate_on_a_row_with_no_structure_is_refused(
    report: ModuleType,
) -> None:
    """Its search cannot be named, and no scorer writes such a cell."""
    r = row("t", 1, "A", -0.7)
    with pytest.raises(CampaignError, match="structure"):
        report.fit_flags([r], {key(r): cell(1.0)}, CHANNELS, PSI_FULL_GRID_MAX)


# --------------------------------------------------------------------------
# Shares per system
# --------------------------------------------------------------------------


def test_the_certified_share_is_per_system(report: ModuleType) -> None:
    rows = [fitted(s) for s in (1, 2, 3, 4)] + [fitted(s, "B") for s in (1, 2)]
    cells = {key(r): cell(1.0) for r in rows}
    cells[("t", 4, "A")] = cell(0.0)
    table = body(section(report, rows, cells))
    assert table["A"]["units"] == "4"
    assert table["A"]["certified"] == "3"
    assert table["A"]["uncertified"] == "1"
    assert table["A"]["certified share"] == "75.0% (3/4)"
    assert table["B"]["certified share"] == "100.0% (2/2)"
    assert table["all"]["certified share"] == "83.3% (5/6)"


def test_a_row_with_no_certified_field_is_unrecorded_never_certified(
    report: ModuleType,
) -> None:
    rows = [fitted(s) for s in (1, 2, 3)] + [fitted(s, "B") for s in (1, 2)]
    cells = {key(r): cell(1.0) for r in rows if r.system == "A"}
    cells |= {key(r): cell(None) for r in rows if r.system == "B"}
    cells[("t", 3, "A")] = cell(None)
    table = body(section(report, rows, cells))
    assert table["A"]["certified"] == "2"
    assert table["A"]["unrecorded"] == "1"
    assert table["A"]["certified share"] == "100.0% (2/2)"
    # Nothing recorded: the share is undefined, neither 100% nor 0%.
    assert table["B"]["certified"] == "0"
    assert table["B"]["unrecorded"] == "2"
    assert table["B"]["certified share"] == "-"


def test_a_row_without_a_cell_is_unrecorded(report: ModuleType) -> None:
    rows = [row("t", 1, "A", -0.7)]
    table = body(section(report, rows, {}))
    assert table["A"]["unrecorded"] == "1"
    assert table["A"]["certified share"] == "-"


def test_a_model_that_is_no_glm_fit_enters_no_share(report: ModuleType) -> None:
    rows = [fitted(s) for s in (1, 2, 3)]
    cells = {key(r): cell(1.0) for r in rows}
    cells[("t", 3, "A")] = cell(math.nan)
    table = body(section(report, rows, cells, ("A",)))
    assert table["A"]["no GLM fit"] == "1"
    assert table["A"]["unrecorded"] == "0"
    assert table["A"]["certified share"] == "100.0% (2/2)"


def test_rows_with_no_fit_at_all_say_so_among_the_unrecorded(
    report: ModuleType,
) -> None:
    """A no-submission or failed row has no ``certified`` field because no fit
    exists: unrecorded, as any row without the field, and named as that."""
    certified = fitted(1)
    silent = row("t", 2, "A", -0.7)
    failed = row("t", 3, "A", None, ok=False)
    nothing = replace(row("t", 4, "A", None), submitted=False)
    rows = [certified, silent, failed, nothing]
    cells = {key(r): cell(None) for r in rows}
    cells[key(certified)] = cell(1.0)
    table = body(section(report, rows, cells, ("A",)))
    assert table["A"]["unrecorded"] == "3 (2 with no fit)"
    assert table["A"]["certified share"] == "100.0% (1/1)"


def test_a_system_with_no_rows_is_left_out(report: ModuleType) -> None:
    rows = [fitted(1)]
    table = body(section(report, rows, {key(rows[0]): cell()}))
    assert sorted(table) == ["A", "all"]


# --------------------------------------------------------------------------
# The coordinate-optimal share
# --------------------------------------------------------------------------


def test_the_search_recomputed_is_the_search_fit_took(report: ModuleType) -> None:
    """Held to the fitter itself, on both sides of the limit, and for the one
    final fit that takes no search."""
    s, data, channels = structure_of(), toy_data(), psm.T2_CHANNELS
    r = replace(row("t", 1, "ORACLE", -0.7), structure=render(s))
    size = _Slots.of(s).size
    for limit, search in ((size, "exhaustive"), (size - 1, "coordinate")):
        result = fit(s, data, channels, config=FitConfig(psi_full_grid_max=limit))
        assert result.psi_search == search
        assert report.psi_search_of(r, {}, channels, limit) == search
        kept = {"oracle_psi_source": "search"}
        assert report.psi_search_of(r, kept, channels, limit) == search
    slots = _Slots.of(s)
    at_truth = fit(s, data, channels, psi=slots.assignment((0,) * len(slots.slots)))
    kept = {"oracle_psi_source": "truth"}
    assert report.psi_search_of(r, kept, channels, size) == at_truth.psi_search


def test_the_limit_is_the_fit_configurations(report: ModuleType) -> None:
    r = fitted(1)
    assert report.fit_flags([r], {key(r): cell()}, CHANNELS, 12)[key(r)].psi_search == (
        "exhaustive"
    )
    assert report.fit_flags([r], {key(r): cell()}, CHANNELS, 11)[key(r)].psi_search == (
        "coordinate"
    )


def test_the_coordinate_share_is_per_system(report: ModuleType) -> None:
    """The flag is "certified in θ, coordinate-optimal in ψ": an uncertified
    fit and a kept truth-ψ refit are in the denominator and not the numerator,
    however large their grid."""
    rows = [
        fitted(1, "A", LARGE),
        fitted(2, "A", TEXT),
        fitted(3, "A", LARGE),
        fitted(4, "A", LARGE),
        fitted(1, "B", LARGE),
        fitted(2, "B", LARGE),
    ]
    cells = {key(r): cell(1.0) for r in rows}
    cells[("t", 3, "A")] = cell(0.0)
    cells[("t", 4, "A")] = cell(1.0, "truth")
    table = body(section(report, rows, cells))
    assert table["A"]["coordinate-optimal share"] == "25.0% (1/4)"
    assert table["B"]["coordinate-optimal share"] == "100.0% (2/2)"
    assert table["all"]["coordinate-optimal share"] == "50.0% (3/6)"


def test_a_coordinate_share_of_nothing_is_a_true_zero_and_no_fit_is_undefined(
    report: ModuleType,
) -> None:
    rows = [fitted(1), row("t", 1, "B", -0.7)]
    cells = {key(rows[0]): cell(1.0), key(rows[1]): cell(math.nan)}
    table = body(section(report, rows, cells))
    assert table["A"]["coordinate-optimal share"] == "0.0% (0/1)"
    assert table["B"]["coordinate-optimal share"] == "-"
    assert table["all"]["coordinate-optimal share"] == "0.0% (0/1)"


def test_the_section_says_where_the_coordinate_share_comes_from(
    report: ModuleType,
) -> None:
    rows = [fitted(1, "A", LARGE)]
    text = " ".join(section(report, rows, {key(rows[0]): cell()}, ("A",)))
    assert "`psi_search`" in text
    assert "512 points" in text
    assert "truth-ψ refit" in text


# --------------------------------------------------------------------------
# The section's list of uncertified fits
# --------------------------------------------------------------------------


def _llm_rows() -> list[Row]:
    rows = [row("t", s, "ORACLE", -0.70 - 0.001 * s) for s in SEEDS] + [
        row("t", s, "B-lib", -0.80 - 0.001 * s) for s in SEEDS
    ]
    rows += [row("t", s, ARM, -0.75, llm=True) for s in SEEDS]
    rows += [row("t", s, OPEN, -0.75, llm=True) for s in (1, 2)]
    rows.append(row("t", 3, OPEN, -math.inf, llm=True))
    return derive([replace(r, structure=TEXT) for r in rows])


def test_each_uncertified_fit_is_listed_with_its_scores(report: ModuleType) -> None:
    rows = _llm_rows()
    cells = {key(r): cell(1.0) for r in rows}
    cells[("t", 1, ARM)] = cell(0.0)
    cells[("t", 3, OPEN)] = cell(0.0)
    lines = section(report, rows, cells, ("ORACLE", "B-lib", ARM, OPEN))
    listed = [line for line in lines if line.startswith("- ")]
    assert len(listed) == 2, lines
    assert listed[0].startswith(f"- {ARM} t seed 1: gap -0.0490, gap closed 0.51"), (
        listed
    )
    assert listed[1].startswith(f"- {OPEN} t seed 3: gap -inf, gap closed -"), listed
    assert any(line.startswith("Uncertified fits: 2,") for line in lines), lines
    assert any(
        "Non-LLM rows among them (the only rows the §7.1 verdict reads): 0." in line
        for line in lines
    ), lines


def test_the_list_does_not_depend_on_row_order(report: ModuleType) -> None:
    rows = _llm_rows()
    cells = {key(r): cell(0.0) for r in rows if r.llm}
    systems = ("ORACLE", "B-lib", ARM, OPEN)
    assert section(report, rows, cells, systems) == section(
        report, rows[::-1], cells, systems
    )


def test_the_section_says_what_a_certificate_says_of_each_search(
    report: ModuleType,
) -> None:
    """Every point of an exhaustive grid is evaluated, so a certified fit there
    has the best ψ of its grid; a coordinate search evaluates only some."""
    rows = [fitted(1, "A", LARGE)]
    text = " ".join(section(report, rows, {key(rows[0]): cell()}, ("A",)))
    assert "does not say ψ is the best point of its grid" not in text
    assert "searched exhaustively, that makes ψ the best point of the grid" in text
    assert "searched coordinate-wise, it does not" in text


def test_the_list_of_marked_places_leaves_out_the_baselines_only_shares(
    report: ModuleType,
) -> None:
    """The go/no-go shares table prints no LLM arm, and the verdict's baselines
    are the only rows it holds, so naming it promises a mark it cannot show."""
    rows = _llm_rows()
    cells = {key(r): cell(1.0) for r in rows}
    cells[("t", 1, ARM)] = cell(0.0)
    lines = section(report, rows, cells, ("ORACLE", "B-lib", ARM, OPEN))
    text = " ".join(lines)
    assert "the go/no-go shares" not in text
    assert "the per-system and group tables and the transcript picks" in text


def test_no_uncertified_fit_is_stated_too(report: ModuleType) -> None:
    rows = _llm_rows()
    lines = section(report, rows, {key(r): cell(1.0) for r in rows}, (ARM,))
    assert any("No uncertified fit" in line for line in lines), lines


# --------------------------------------------------------------------------
# The whole report: flags and the verdict
# --------------------------------------------------------------------------


def _session(
    store: ResultStore, system: str, truth: str, seed: int, *, stored_void: bool
) -> Session:
    made = _put(store, system, seed, "submitted", _counts(30, 1, 1, 1), result=True)
    return replace(
        made,
        truth=truth,
        void=VoidStatus(False, (), 1 if stored_void else 0, stored_void),
    )


def _arms(tmp_path: Path) -> Table:
    """The wording tests' complete baselines plus two LLM arms, with one AG-o
    session marked void by the harness, so it is in the per-unit lists."""
    base = _table()
    rows = list(base.rows)
    for t in TRUTHS:
        for seed in SEEDS:
            value = -0.72 - 0.001 * seed
            curve = ((1, value),)
            rows.append(row(t, seed, ARM, value, llm=True, curve=curve))
            rows.append(row(t, seed, OPEN, value, llm=True, curve=curve))
    with ResultStore(tmp_path / "store") as store:
        sessions = (
            _session(store, ARM, TRUTHS[1], 1, stored_void=False),
            _session(store, OPEN, TRUTHS[1], 2, stored_void=True),
        )
    lines = [
        {"kind": "llm-score", "system": system, "wall_s": 1.0} for system in (ARM, OPEN)
    ]
    (tmp_path / "store" / "timing.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    structured = [replace(r, structure=TEXT) for r in rows]
    return replace(base, rows=tuple(derive(structured)), sessions=sessions)


def _cells(table: Table, *uncertified: Key) -> dict[Key, dict[str, Any]]:
    cells = {key(r): cell(1.0) for r in table.rows}
    for unit in uncertified:
        cells[unit] = cell(0.0)
    return cells


def _text(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: Table,
    cells: dict[Key, dict[str, Any]],
) -> str:
    monkeypatch.setattr(report, "row_cells", lambda *_, **__: cells)
    return _render(report, tmp_path, monkeypatch, table)


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _arm_line(part: str, arm: str, prefix: str = "") -> str:
    found = [line for line in part.splitlines() if line.startswith(f"| {prefix}{arm}")]
    assert len(found) == 1, (arm, part)
    return found[0]


UNIT_LLM: Key = (TRUTHS[1], 2, OPEN)


def test_an_uncertified_llm_row_is_flagged_in_the_scores(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _arms(tmp_path)
    text = _text(report, tmp_path, monkeypatch, table, _cells(table, UNIT_LLM))
    part = _between(text, "## Per-system scores", "### By stratum")
    assert "(1 uncertified)" in _arm_line(part, OPEN)
    assert "uncertified" not in _arm_line(part, ARM)


def test_tables_that_read_no_final_fit_take_no_flag(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counts, curves, costs and wall times say nothing of a final fit, so a
    mark there would flag a number the fit is not behind."""
    table = _arms(tmp_path)
    text = _text(report, tmp_path, monkeypatch, table, _cells(table, UNIT_LLM))
    for start, end in (
        ("## Coverage", "## Notes"),
        ("## Positive controls", "## Per-system scores"),
        ("## Efficiency", "### AG-o transcripts"),
    ):
        assert "uncertified" not in _between(text, start, end), start
    assert "uncertified" not in text.split("## Wall and CPU", 1)[1]


def test_an_uncertified_row_is_flagged_in_the_group_it_falls_in(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _arms(tmp_path)
    text = _text(report, tmp_path, monkeypatch, table, _cells(table, UNIT_LLM))
    for start, end in (
        ("### By stratum", "### By dictionary"),
        ("### By dictionary membership", "## Fit certification"),
    ):
        part = _between(text, start, end)
        line = _arm_line(part, OPEN)
        assert line.count("(1 uncertified)") == 1, line
        assert "uncertified" not in _arm_line(part, ARM), part


def test_an_uncertified_unit_is_flagged_in_the_transcript_picks(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _arms(tmp_path)
    text = _text(report, tmp_path, monkeypatch, table, _cells(table, UNIT_LLM))
    named = f"{OPEN} {TRUTHS[1]} seed 2 (1 uncertified)"
    picks = _between(text, "### AG-o transcripts", "## Wall and CPU")
    assert f"{named}, gap closed" in picks, picks


def test_a_certified_store_gets_no_flag_outside_the_section(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _arms(tmp_path)
    text = _text(report, tmp_path, monkeypatch, table, _cells(table))
    rest = text.replace(_between(text, "## Fit certification", "## Efficiency"), "")
    assert "uncertified" not in rest


def _without_flags(text: str) -> str:
    """``text`` without the section and the marks: what the report said before."""
    section_text = _between(text, "## Fit certification", "## Efficiency")
    out = text.replace("## Fit certification" + section_text, "")
    return out.replace(" (1 uncertified)", "")


def test_the_flags_add_to_the_report_and_change_nothing_else(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _arms(tmp_path)
    plain = _text(report, tmp_path, monkeypatch, table, _cells(table))
    marked = _text(report, tmp_path, monkeypatch, table, _cells(table, UNIT_LLM))
    assert plain != marked
    assert _without_flags(plain) == _without_flags(marked)


def test_an_uncertified_llm_row_leaves_the_verdict_alone(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _arms(tmp_path)
    plain = _text(report, tmp_path, monkeypatch, table, _cells(table))
    marked = _text(report, tmp_path, monkeypatch, table, _cells(table, UNIT_LLM))
    start, end = "## Go/no-go (SPEC §7.1)", "## Per-system scores"
    assert _between(plain, start, end) == _between(marked, start, end)
    assert "**Verdict (computed): GO.**" in marked
    stated = _between(marked, "## Fit certification", "## Efficiency")
    assert (
        "Non-LLM rows among them (the only rows the §7.1 verdict reads): 0." in stated
    ), stated


def test_the_verdict_itself_does_not_read_the_flags(tmp_path: Path) -> None:
    """The reading never reaches a :class:`Row`, so the verdict cannot see it."""
    table = _arms(tmp_path)
    v = analysis.verdict(table, np.random.default_rng(0), resamples=200)
    assert v.outcome == "go"
    assert not hasattr(table.rows[0], "certified")


def test_an_uncertified_baseline_is_flagged_where_the_verdict_reads_it(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flagged, and still used: the verdict's rule does not move, so its text
    stays, and the report says the verdict reads the row."""
    table = _arms(tmp_path)
    plain = _text(report, tmp_path, monkeypatch, table, _cells(table))
    units = ((TRUTHS[0], 1, "ORACLE"), (TRUTHS[2], 3, "B-sym"))
    marked = _text(report, tmp_path, monkeypatch, table, _cells(table, *units))
    shares = _between(marked, "## Go/no-go", "## Positive controls")
    assert "| B-sym (1 uncertified) |" in shares, shares
    assert (
        "Non-LLM rows among them (the only rows the §7.1 verdict reads): 2." in marked
    )
    assert "**Verdict (computed): GO.**" in marked
    assert _without_flags(plain) == _without_flags(marked)


# --------------------------------------------------------------------------
# The cells are the rows' cells
# --------------------------------------------------------------------------


def _addressed_store(root: Path) -> tuple[Any, Any, Any]:
    """A baseline and an LLM unit with its session and score cell at the plan's
    addresses, each with its own ``certified`` reading."""
    env = environment()
    config = tiny_config(seeds=(1,), baselines=("B-rand",))
    config = replace(config, llm_arms=config.llm_arms[:1])
    truths = (entry("hawkes"),)
    with ResultStore(root) as store:
        for unit in baseline_units(["hawkes"], config):
            store.put(
                baseline_key(unit, truths[0], env, config),
                {"status": 1.0, "per_event": -0.75, "certified": 1.0},
                {"result": {"structure": TEXT}},
            )
        for unit in llm_units(["hawkes"], config):
            skey = session_key(unit, truths[0], env, config)
            store.put(
                skey,
                {"status": 1.0, "void": 0.0},
                {"session": {"outcome": "submitted", "tool_calls": {}}},
            )
            store.put(
                score_key(unit, skey, env, config),
                {"status": 1.0, "per_event": -0.76, "certified": 0.0},
                {"result": {"structure": TEXT}},
            )
    return env, config, truths


@pytest.mark.parametrize("sessions", ["current", "stored"])
def test_cells_are_read_at_the_rows_addresses(
    report: ModuleType, tmp_path: Path, sessions: Literal["current", "stored"]
) -> None:
    env, config, truths = _addressed_store(tmp_path)
    with ResultStore(tmp_path, read_only=True) as store:
        rows = analysis.load_rows(store, env, config, truths, sessions=sessions).rows
        cells = report.row_cells(store, env, config, truths, sessions=sessions)
    assert sorted(key(r) for r in rows) == sorted(cells)
    flags = report.fit_flags(rows, cells, env.channels, PSI_FULL_GRID_MAX)
    states = {r.system: flags[key(r)].state for r in rows}
    assert states["B-rand"] == report.CERTIFIED
    (arm,) = config.llm_systems()
    assert states[arm] == report.UNCERTIFIED


def test_a_cell_of_another_configuration_is_not_read(
    report: ModuleType, tmp_path: Path
) -> None:
    """It has another address, so a stray cell cannot give a row its flag."""
    env, config, truths = _addressed_store(tmp_path)
    other = replace(config, fits=config.fits + 1)
    with ResultStore(tmp_path, read_only=True) as store:
        assert report.row_cells(store, env, other, truths, sessions="current") == {}
