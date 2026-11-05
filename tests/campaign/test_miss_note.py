"""``scripts/p3_report/miss_note.py``: the section 10 note of the frozen miss check.

The rule is ``docs/v2/prereg/2026-11-04_pilot-search-misses.md``. Every record
here is a toy, written by the frozen script's own ``Journal`` and
``finalise_records`` from the planted records of its own test, so the shapes
are the script's and no test touches a pilot unit.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from test_pilot_search_misses import (
    IDENTITY,
    SEEDS,
    TRUTHS,
    control_records,
    done_records,
    info,
    psm,
    row_result,
    toy_table,
)

from sciagent.campaign.analysis import Table
from sciagent.glm.fit import _Slots
from sciagent.glm.grammar import Link, Structure
from sciagent.glm.syntax import parse, render

REPO: Final = Path(__file__).resolve().parents[2]
SCRIPT: Final = REPO / "scripts" / "p3_report" / "miss_note.py"
PREREG: Final = REPO / psm.PREREG_PATH
RESAMPLES: Final = 200

#: Section 10's bullets, in order.
HEADINGS: Final = (
    "## 1. Label and hypotheses",
    "## 2. Controls",
    "## 3. Classes",
    "## 4. Coverage",
    "## 5. Misses",
    "## 6. Verdict on corrected readings",
    "## 7. Secondary and exploratory analyses",
    "## 8. Not covered and unknown",
    "## 9. Cost, records and departures",
)


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("miss_note", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["miss_note"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return load()


# --------------------------------------------------------------------------
# Toy records, through the script's own writers
# --------------------------------------------------------------------------


def text_of(features: tuple[Any, ...], link: Link) -> str:
    return render(Structure(features, link))


#: Two gated kernels: two ``period`` slots, G = 14,400.
GATED: Final = text_of(psm.T2_BIG[0], Link.SOFTPLUS)
#: A gated power kernel and a gated ``Periodic``: three ``period`` slots.
WINDOWS: Final = text_of(psm.T2_BIG[2], Link.EXP)
#: One kernel and a bare ``Periodic``: one ``period`` slot.
ONE_PERIOD: Final = text_of((psm._EXP_K, psm.Periodic()), Link.EXP)
#: Two kernels, no ``period`` slot.
NO_PERIOD: Final = text_of((psm._EXP_K, psm._SIZE_K), Link.IDENTITY)
TEXTS: Final = {
    "ORACLE": GATED,
    "B-sym": WINDOWS,
    "B-sparse": WINDOWS,
    "B-sym@10F": WINDOWS,
    "B-rand": ONE_PERIOD,
    "planted-hint": ONE_PERIOD,
    "AG-c-S/named": NO_PERIOD,
}


def shape(text: str) -> dict[str, Any]:
    structure = parse(text, psm.T2_CHANNELS)
    slots = _Slots.of(structure)
    return {
        "grid_size": slots.size,
        "sweep": psm._sweep(slots),
        "link": structure.link.value,
        "n_features": len(structure.features),
        "n_events": 200,
    }


def census_of(table: Table) -> Any:
    rows = []
    for r in table.rows:
        if r.system == "B-np":
            rows.append(info(r, "no_search_np", None))
        elif r.system == "B-lib":
            rows.append(info(r, "exhaustive", f"{r.system} {r.truth} {r.seed}"))
        else:
            text = TEXTS[r.system]
            row = info(r, "coordinate", text)
            rows.append(
                replace(
                    row, grid_size=shape(text)["grid_size"], link=shape(text)["link"]
                )
            )
    return psm.Census(tuple(rows))


def with_shapes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The planted fits as the script writes them: an ``r0`` before R1, and the
    shape fields ``_fit_payload`` gives every fit."""
    out: list[dict[str, Any]] = []
    for record in records:
        if record["type"] == "done" and record.get("kind") == "fit":
            text = GATED if record["job"] == "B" else record["text"]
            record = {**record, "text": text, **shape(text)}
            zero = [0] * len(_Slots.of(parse(text, psm.T2_CHANNELS)).slots)
            point = {"point": zero, "score": -100.0, "point_certified": True}
            value = {"ll": -100.0, "gap": 1e-9, "certified": True}
            out.append(
                {
                    "type": "r0",
                    "job": record["job"],
                    "search": {**point, **value},
                    "kept": {**point, **value},
                }
            )
        out.append(record)
    return out


def miss(
    system: str,
    stored: float,
    corrected: float,
    *,
    llm: bool = False,
    point: tuple[int, ...] = (),
) -> dict[str, Any]:
    return {
        **row_result(
            system, kind="miss", llm=llm, stored=stored, corrected=corrected, gain=1.0
        ),
        "gain_per_event": 0.005,
        "chosen_point": list(point),
    }


@dataclass(frozen=True)
class Run:
    root: Path
    decision: dict[str, Any]

    def edit(self, change: Any) -> None:
        """Change ``decision.json`` by hand, as the toy of a damaged record."""
        path = self.root / "decision.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        change(body)
        path.write_text(json.dumps(body), encoding="utf-8")


TIMING: Final = (
    {"job": "a", "stage": "tier1", "wall_s": 7200.0, "cpu_s": 3600.0},
    {"job": "b", "stage": "tier1", "wall_s": 3600.0, "cpu_s": 1800.0},
    {"job": "c", "stage": "hunt", "wall_s": 360.0, "cpu_s": 180.0},
)


def run_of(
    root: Path,
    table: Table,
    records: list[dict[str, Any]],
    *,
    end: str = "complete",
    provisional: bool = False,
) -> Run:
    """The run's directory, by the frozen script's own finalise."""
    root.mkdir()
    paths = psm.OutPaths(root)
    journal = psm.Journal(paths.journal)
    for record in [
        *with_shapes(records),
        {"type": "run_start"},
        {"type": "run_end", "reason": end},
    ]:
        journal.append(record)
    for line in TIMING:
        body = {**line, "cpu_children_s": 0.0}
        psm.append_line(paths.timing, json.dumps(body, sort_keys=True))
    decision = psm.finalise_records(
        paths,
        census_of(table),
        table,
        identity=IDENTITY,
        manifest_failures=(),
        resamples=RESAMPLES,
        provisional=provisional,
    )
    return Run(root, decision)


@pytest.fixture(scope="module")
def go_table() -> Table:
    return toy_table()


def clean_records(table: Table, **rows: Any) -> list[dict[str, Any]]:
    return [*control_records(), *done_records(census_of(table), **rows)]


@pytest.fixture(scope="module")
def clean_run(go_table: Table, tmp_path_factory: pytest.TempPathFactory) -> Run:
    root = tmp_path_factory.mktemp("clean") / "records"
    return run_of(root, go_table, clean_records(go_table))


#: Two systems on one fit, and one LLM row on another.
WINDOW_POINT: Final = (0, 0, 1, 0, 2, 3, 0)


def misses_of() -> dict[tuple[str, int, str], dict[str, Any]]:
    return {
        ("t1", 1, "B-sparse"): miss("B-sparse", -0.721, -0.7205, point=WINDOW_POINT),
        ("t1", 1, "B-sym"): miss("B-sym", -0.711, -0.7105, point=WINDOW_POINT),
        ("t2", 2, "AG-c-S/named"): miss(
            "AG-c-S/named", -0.712, -0.60, llm=True, point=(0, 0)
        ),
    }


@pytest.fixture(scope="module")
def miss_run(go_table: Table, tmp_path_factory: pytest.TempPathFactory) -> Run:
    root = tmp_path_factory.mktemp("miss") / "records"
    return run_of(root, go_table, clean_records(go_table, rows=misses_of()))


@pytest.fixture(scope="module")
def flip_run(go_table: Table, tmp_path_factory: pytest.TempPathFactory) -> Run:
    rows = {
        (t, s, "B-sparse"): miss(
            "B-sparse", -0.72 - 0.001 * s, -0.7 - 0.001 * s, point=WINDOW_POINT
        )
        for t in TRUTHS
        for s in SEEDS
    }
    root = tmp_path_factory.mktemp("flip") / "records"
    return run_of(root, go_table, clean_records(go_table, rows=rows))


@pytest.fixture(scope="module")
def provisional_run(go_table: Table, tmp_path_factory: pytest.TempPathFactory) -> Run:
    records = done_records(census_of(go_table), skip=lambda f: f.truth == "t1")
    root = tmp_path_factory.mktemp("provisional") / "records"
    return run_of(
        root, go_table, [*control_records(), *records], end="stop", provisional=True
    )


def section(note: str, heading: str) -> str:
    """One numbered section of the note, heading included."""
    start = note.index(heading)
    ends = [note.find(h, start + 1) for h in (*HEADINGS, "## Fields not recorded")]
    return note[start : min(e for e in ends if e != -1)]


def copy_of(run: Run, target: Path) -> Path:
    return Path(shutil.copytree(run.root, target))


# --------------------------------------------------------------------------
# Every bullet of section 10, in order
# --------------------------------------------------------------------------


def test_the_toy_runs_carry_the_labels_they_are_named_for(
    clean_run: Run, miss_run: Run, flip_run: Run, provisional_run: Run
) -> None:
    assert clean_run.decision["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert clean_run.decision["M_rows"] == 0
    assert miss_run.decision["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert miss_run.decision["M_rows"] == 3
    assert miss_run.decision["M_fits"] == 2
    assert flip_run.decision["label"] == "GO_CHANGES"
    assert flip_run.decision["clauses_moved"]
    assert provisional_run.decision["label"] == "INCOMPLETE"
    assert provisional_run.decision["provisional"] is True


def test_every_bullet_has_its_section_in_order(
    tool: ModuleType, clean_run: Run
) -> None:
    note = tool.note_of(clean_run.root)
    assert [line for line in note.splitlines() if line.startswith("## ")][:9] == list(
        HEADINGS
    )


def test_the_note_is_labelled_unreviewed_and_gives_its_command(
    tool: ModuleType, clean_run: Run
) -> None:
    head = tool.note_of(clean_run.root).split("## 1.")[0]
    assert "unreviewed" in head
    assert "scripts/p3_report/miss_note.py --records" in head
    assert "A corrected reading is never presented as the pilot's reading." in head


def test_the_note_names_each_file_it_read_by_its_digest(
    tool: ModuleType, clean_run: Run
) -> None:
    note = tool.note_of(clean_run.root)
    for name in ("decision.json", "units.jsonl", "timing.jsonl"):
        digest = hashlib.sha256((clean_run.root / name).read_bytes()).hexdigest()
        assert digest in note


# --------------------------------------------------------------------------
# GO_SURVIVES_FOUND_MISSES at M = 0
# --------------------------------------------------------------------------


def test_m_zero_gives_the_none_table(tool: ModuleType, clean_run: Run) -> None:
    part = section(tool.note_of(clean_run.root), "## 5. Misses")
    assert "Miss rows: none." in part
    assert "| none |" not in part.split("Miss rows: none.")[1].split("###")[0]
    assert "M = 0 rows, 0 fits" in part


def test_the_survival_wording_is_copied_whole(tool: ModuleType, clean_run: Run) -> None:
    part = section(tool.note_of(clean_run.root), "## 1. Label and hypotheses")
    assert clean_run.decision["statement"] in part
    assert clean_run.decision["label_line"] in part
    assert clean_run.decision["go_no_go"]["next"] in part
    assert clean_run.decision["label"] in part


def test_h3_and_h4_say_they_are_vacuous_at_m_zero(
    tool: ModuleType, clean_run: Run
) -> None:
    part = section(tool.note_of(clean_run.root), "## 1. Label and hypotheses")
    assert "| H1 | not falsified |" in part
    assert "| H2 | not falsified |" in part
    assert "| H3 | not falsified, vacuously (M = 0) |" in part
    assert "| H4 | not falsified, vacuously (M = 0) |" in part


def test_the_exploratory_table_at_m_zero_says_none(
    tool: ModuleType, clean_run: Run
) -> None:
    part = section(
        tool.note_of(clean_run.root), "## 7. Secondary and exploratory analyses"
    )
    exploratory = part.split("### Exploratory")[1]
    assert "No miss rows in `units.jsonl`." in exploratory
    assert "| exp | 30 | 0 | 0 | none |" in exploratory


# --------------------------------------------------------------------------
# GO_SURVIVES_FOUND_MISSES with misses
# --------------------------------------------------------------------------


def test_each_miss_row_is_listed_with_its_copied_values(
    tool: ModuleType, miss_run: Run
) -> None:
    part = section(tool.note_of(miss_run.root), "## 5. Misses")
    assert "M = 3 rows, 2 fits" in part
    assert "| AG-c-S/named | 1 |" in part
    assert "| B-sparse | 1 |" in part
    row = next(r for r in miss_run.decision["miss_rows"] if r["key"][2] == "B-sym")
    delta = repr(float.fromhex(row["delta"]))
    assert (
        f"| t1 | 1 | B-sym | 1.0 | {delta} | -0.711 | -0.7105 | searched "
        "| false | false | 1e-06 | 1e-06 |"
    ) in part


def test_every_float_is_shown_as_its_exact_decimal_never_as_hex(
    tool: ModuleType, miss_run: Run
) -> None:
    assert "0x1." not in tool.note_of(miss_run.root)


def test_an_infinite_move_is_listed_and_not_read_as_a_number(
    tool: ModuleType, go_table: Table, tmp_path: Path
) -> None:
    rows = {("t1", 1, "B-sym"): miss("B-sym", -math.inf, -0.7105, point=WINDOW_POINT)}
    run = run_of(tmp_path / "r", go_table, clean_records(go_table, rows=rows))
    assert run.decision["max_abs_delta"] == "infinite_move"
    note = tool.note_of(run.root)
    assert "| t1 | 1 | B-sym | 1.0 | infinite_move | -inf | -0.7105 |" in note
    assert "largest |Δ| among the misses found: infinite_move" in note


def test_oracle_search_misses_are_apart(tool: ModuleType, miss_run: Run) -> None:
    part = section(tool.note_of(miss_run.root), "## 5. Misses")
    assert "### ORACLE search misses" in part
    assert "Search misses on ORACLE: 0" in part
    assert "stored search below truth ψ: 0" in part


def test_uncertified_better_is_beside_m(tool: ModuleType, miss_run: Run) -> None:
    part = section(tool.note_of(miss_run.root), "## 5. Misses")
    assert "`uncertified_better` on the identity link: 0" in part


def test_h2_says_when_it_rests_on_quadrature_alone(
    tool: ModuleType, go_table: Table, tmp_path: Path
) -> None:
    row = {
        **miss("B-sym", -0.711, -0.7105, point=WINDOW_POINT),
        "within_quadrature": True,
    }
    records = clean_records(go_table, rows={("t1", 1, "B-sym"): row})
    run = run_of(tmp_path / "r", go_table, records)
    assert run.decision["H2"]["rests_on_within_quadrature"] is True
    part = section(tool.note_of(run.root), "## 1. Label and hypotheses")
    assert "H2 rests on `within_quadrature` rows only." in part


def test_the_per_gate_counts_and_strata_are_copied(
    tool: ModuleType, miss_run: Run
) -> None:
    part = section(tool.note_of(miss_run.root), "## 4. Coverage")
    assert "| Exact (R2 or exhaustive) |" in part
    assert "| Not applicable |" in part
    for name, row in miss_run.decision["strata"].items():
        assert f"| {name} | {row['searched_fits']} | {row['control']} |" in part


def test_the_control_table_is_case_by_case(tool: ModuleType, miss_run: Run) -> None:
    part = section(tool.note_of(miss_run.root), "## 2. Controls")
    assert "| A | 0, softplus, 103648458 | true | -105.0 | -100.0 | -100.0 |" in part
    assert "| B | dev-015, 3 | true | -105.0 | -100.0 | -100.0 |" in part
    assert "| D | 0, softplus, 103648458 | true | -105.0 | -100.0 | -100.0 |" in part
    for name, row in miss_run.decision["hunt"].items():
        assert f"| {name} | {row['completed']} |" in part
    assert psm.NO_CONTROL in part


def test_the_class_table_gives_exhaustive_uncertified_rows(
    tool: ModuleType, miss_run: Run
) -> None:
    part = section(tool.note_of(miss_run.root), "## 3. Classes")
    assert "| Exhaustive, `certified` = 0 | 0 |" in part
    assert f"| Coordinate path | {miss_run.decision['classes']['coordinate']} |" in part


def test_each_statistic_is_given_stored_and_corrected(
    tool: ModuleType, miss_run: Run
) -> None:
    part = section(tool.note_of(miss_run.root), "## 6. Verdict on corrected readings")
    assert "| Outcome | go | go |" in part
    assert "| C1 kept |" in part
    assert "| C1 dropped |" in part
    assert "| Reasons |" in part
    assert "| Inconclusive |" in part
    stored = miss_run.decision["stored_gates"]["C2"]
    corrected = miss_run.decision["corrected_gates"]["C2"]
    assert (f"| C2 wins | {stored['wins']} | {corrected['wins']} |") in part


def test_s1_to_s4_and_v2_are_copied(tool: ModuleType, miss_run: Run) -> None:
    part = section(
        tool.note_of(miss_run.root), "## 7. Secondary and exploratory analyses"
    )
    assert "### S1" in part
    assert "`s1_status`: not_estimable" in part
    assert "### S2" in part
    assert "### S3" in part
    assert "### S4" in part
    assert "`v2` outcome: go" in part
    assert "| t1 | 1 | B-sym |" in part


def test_s4_says_the_limits_are_not_recorded(tool: ModuleType, miss_run: Run) -> None:
    part = section(
        tool.note_of(miss_run.root), "## 7. Secondary and exploratory analyses"
    )
    assert "Each statistic's limit: not recorded" in part


# --------------------------------------------------------------------------
# The exploratory table: the one new computation
# --------------------------------------------------------------------------


def exploratory_of(tool: ModuleType, run: Run) -> str:
    part = section(tool.note_of(run.root), "## 7. Secondary and exploratory analyses")
    return part.split("### Exploratory")[1]


def test_the_exploratory_table_is_marked(tool: ModuleType, miss_run: Run) -> None:
    part = exploratory_of(tool, miss_run)
    assert "exploratory" in part.lower()
    assert (
        "No difference between systems is claimed, and no control supports one." in part
    )


def test_misses_are_counted_by_each_shape(tool: ModuleType, miss_run: Run) -> None:
    part = exploratory_of(tool, miss_run)
    assert "| exp | 30 | 1 | 2 | B-sparse 1, B-sym 1 |" in part
    assert "| identity | 15 | 1 | 1 | AG-c-S/named 1 |" in part
    assert "| softplus | 16 | 0 | 0 | none |" in part
    assert "| 2 | 61 | 2 | 3 | AG-c-S/named 1, B-sparse 1, B-sym 1 |" in part
    assert "| 0 | 15 | 1 | 1 | AG-c-S/named 1 |" in part
    assert "| 1 | 15 | 0 | 0 | none |" in part
    assert "| 2 or more | 31 | 1 | 2 | B-sparse 1, B-sym 1 |" in part


def test_shared_periods_are_read_at_the_stored_and_the_chosen_point(
    tool: ModuleType, miss_run: Run
) -> None:
    """Toy stored points are all zero, so their period slots agree; the planted
    chosen point gives each of the three its own index."""
    part = exploratory_of(tool, miss_run)
    assert "| yes | no | 2 | B-sparse 1, B-sym 1 |" in part
    assert "| yes | yes | 0 | none |" in part


def test_a_point_that_is_not_on_record_is_said_not_guessed(
    tool: ModuleType, go_table: Table, tmp_path: Path
) -> None:
    rows = {("t1", 1, "B-sym"): miss("B-sym", -0.711, -0.7105)}
    run = run_of(tmp_path / "r", go_table, clean_records(go_table, rows=rows))
    assert "| yes | not recorded | 1 | B-sym 1 |" in exploratory_of(tool, run)


def test_a_unit_line_without_its_keys_is_left_out_and_said(
    tool: ModuleType, clean_run: Run, tmp_path: Path
) -> None:
    root = copy_of(clean_run, tmp_path / "r")
    with (root / "units.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "fit"}\n')
    assert "`units.jsonl`: 1 unreadable line left out" in tool.note_of(root)


def test_misses_on_one_fit_across_systems_are_shown(
    tool: ModuleType, miss_run: Run
) -> None:
    part = exploratory_of(tool, miss_run)
    assert "Fits with a miss on one system: 1. On two or more systems: 1." in part
    assert f"| t1 | 1 | `{WINDOWS}` | exp |" in part
    assert "| B-sparse, B-sym | B-sym@10F none |" in part


def test_no_rate_and_no_ranking_is_shown(tool: ModuleType, miss_run: Run) -> None:
    part = exploratory_of(tool, miss_run)
    assert "%" not in part
    assert not re.search(r"\b0\.\d", part)


def test_the_tally_is_sorted_by_name_not_by_count(tool: ModuleType) -> None:
    assert tool.tally({"B-sym": 1, "AG-c-S/anon": 5, "ORACLE": 3}) == (
        "AG-c-S/anon 5, B-sym 1, ORACLE 3"
    )


@pytest.mark.parametrize(
    ("size", "band"),
    [
        (513, 0),
        (psm.R2_GRID_MAX, 0),
        (psm.R2_GRID_MAX + 1, 1),
        (psm.GROUPS_AB_GRID_MAX, 1),
        (psm.GROUPS_AB_GRID_MAX + 1, 2),
        (psm.CONTROLLED_GRID_MAX, 2),
        (psm.CONTROLLED_GRID_MAX + 1, 3),
    ],
)
def test_the_bands_of_g_are_the_texts_own_edges(
    tool: ModuleType, size: int, band: int
) -> None:
    assert tool.g_band(size) == tool.G_BANDS[band][1]


@pytest.mark.parametrize(
    "features",
    [
        psm.T2_BIG[0],
        psm.T2_BIG[1],
        psm.T2_BIG[2],
        psm.T2_BIG[3],
        psm._LARGE,
        (psm._EXP_K, psm.Periodic()),
        (psm._EXP_K, psm._SIZE_K),
    ],
)
def test_period_positions_are_the_fits_own_slot_order(
    tool: ModuleType, features: tuple[Any, ...]
) -> None:
    structure = Structure(features, Link.EXP)
    wanted = tuple(
        i
        for i, (_, slot) in enumerate(_Slots.of(structure).slots)
        if slot.name == "period"
    )
    assert tool.period_positions(render(structure)) == wanted


def test_a_structure_that_does_not_parse_is_said_so(tool: ModuleType) -> None:
    assert tool.period_positions("not dsl text") is None


def test_a_fit_with_unreadable_text_lands_in_not_recorded(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    root = copy_of(miss_run, tmp_path / "r")
    path = root / "units.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        record = json.loads(line)
        if record["job"] == "B" and record["type"] == "done":
            record["text"] = "not dsl text"
        out.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    part = tool.note_of(root).split("### Exploratory")[1]
    assert "| not recorded | 1 | 0 | 0 | none |" in part


def test_the_fit_view_is_the_scripts(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    """A job that died in R2 keeps R1's result, in both readers."""
    records = psm.read_records(miss_run.root / "units.jsonl")
    done = next(r for r in records if r["type"] == "done" and r.get("kind") == "fit")
    records.remove(done)
    r1 = next(r for r in records if r["type"] == "r1" and r["job"] == done["job"])
    r1["result"] = {k: v for k, v in done.items() if k not in ("type", "job")}
    assert tool.fit_views(records) == psm.fit_views(records)


def test_a_job_that_died_in_r2_keeps_its_miss_in_the_table(
    tool: ModuleType, go_table: Table, tmp_path: Path
) -> None:
    rows = {("t1", 1, "B-sym"): miss("B-sym", -0.711, -0.7105, point=WINDOW_POINT)}
    records = with_shapes(clean_records(go_table, rows=rows))
    job = psm.fit_job_id("t1", 1, WINDOWS)
    done = next(r for r in records if r["type"] == "done" and r["job"] == job)
    records.remove(done)
    r1 = next(r for r in records if r["type"] == "r1" and r["job"] == job)
    r1["result"] = {k: v for k, v in done.items() if k not in ("type", "job")}
    records.append({"type": "r2_lost", "job": job, "reason": "cpu_limit"})
    run = run_of(tmp_path / "r", go_table, records)
    assert run.decision["M_rows"] == 1
    note = tool.note_of(run.root)
    part = note.split("### Exploratory")[1]
    assert "| 2 or more | 31 | 1 | 1 | B-sym 1 |" in part
    assert "disagree" not in note


def test_a_table_that_disagrees_with_the_decision_says_so(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    root = copy_of(miss_run, tmp_path / "r")
    run = Run(root, miss_run.decision)
    run.edit(lambda d: d.__setitem__("M_rows", 7))
    note = tool.note_of(root)
    assert "`units.jsonl` and `decision.json` disagree" in note
    assert "M = 7 rows" in note


# --------------------------------------------------------------------------
# GO_CHANGES
# --------------------------------------------------------------------------


def test_go_changes_names_each_clause_with_its_consequence(
    tool: ModuleType, flip_run: Run
) -> None:
    note = tool.note_of(flip_run.root)
    part = section(note, "## 6. Verdict on corrected readings")
    for row in flip_run.decision["clauses_moved"]:
        assert f"| {row['clause']} | {row['consequence']} | not recorded |" in part
    assert flip_run.decision["not_put_forward"] in part


def test_go_changes_gives_each_gate_stored_and_corrected(
    tool: ModuleType, flip_run: Run
) -> None:
    part = section(tool.note_of(flip_run.root), "## 6. Verdict on corrected readings")
    stored = flip_run.decision["stored_gates"]["outcome"]
    corrected = flip_run.decision["corrected_gates"]["outcome"]
    assert stored != corrected
    assert f"| Outcome | {stored} | {corrected} |" in part
    assert "| too_easy flag | false | true |" in part


def test_go_changes_has_no_survival_statement(tool: ModuleType, flip_run: Run) -> None:
    part = section(tool.note_of(flip_run.root), "## 1. Label and hypotheses")
    assert "Statement: none in `decision.json`." in part
    assert "survives substitution" not in part
    assert "| H4 | falsified |" in part


def test_a_flip_on_a_partial_run_says_partial(
    tool: ModuleType, flip_run: Run, tmp_path: Path
) -> None:
    root = copy_of(flip_run, tmp_path / "r")
    Run(root, flip_run.decision).edit(lambda d: d.__setitem__("partial", True))
    part = section(tool.note_of(root), "## 1. Label and hypotheses")
    assert "`partial`: true" in part


# --------------------------------------------------------------------------
# INCOMPLETE, provisional
# --------------------------------------------------------------------------


def test_a_provisional_decision_says_so_at_the_top(
    tool: ModuleType, provisional_run: Run
) -> None:
    note = tool.note_of(provisional_run.root)
    head = note.split("## 1.")[0]
    assert "PROVISIONAL" in head
    assert "(provisional)" in section(note, "## 1. Label and hypotheses")


def test_a_final_decision_does_not_say_provisional(
    tool: ModuleType, clean_run: Run
) -> None:
    assert "PROVISIONAL" not in tool.note_of(clean_run.root)


def test_provisional_records_have_no_units_and_the_note_says_so(
    tool: ModuleType, provisional_run: Run
) -> None:
    assert not (provisional_run.root / "units.jsonl").exists()
    note = tool.note_of(provisional_run.root)
    assert "`units.jsonl`: absent" in note
    part = note.split("### Exploratory")[1]
    assert "not recorded: `units.jsonl` is absent" in part
    assert "| exp |" not in part


def test_the_shortfall_is_copied(tool: ModuleType, provisional_run: Run) -> None:
    part = section(tool.note_of(provisional_run.root), "## 1. Label and hypotheses")
    missing = provisional_run.decision["shortfall"]["tier1_missing"]
    assert missing > 0
    assert f"tier 1 fits missing: {missing}" in part
    assert "`end`: stop" in part


# --------------------------------------------------------------------------
# What the records do not hold
# --------------------------------------------------------------------------


def test_a_missing_field_is_said_not_invented(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    root = copy_of(miss_run, tmp_path / "r")
    Run(root, miss_run.decision).edit(lambda d: d.pop("coverage"))
    note = tool.note_of(root)
    part = section(note, "## 4. Coverage")
    assert "not recorded" in part
    assert "`coverage`" in note.split("## Fields not recorded")[1]


def test_a_nested_missing_field_is_named_by_its_path(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    root = copy_of(miss_run, tmp_path / "r")
    Run(root, miss_run.decision).edit(lambda d: d["H3"].pop("falsified"))
    note = tool.note_of(root)
    assert "| H3 | not recorded |" in note
    assert "`H3.falsified`" in note.split("## Fields not recorded")[1]


def test_a_refused_starts_short_decision_gives_a_whole_note(
    tool: ModuleType, tmp_path: Path
) -> None:
    """``refuse`` writes three fields only."""
    paths = psm.OutPaths(tmp_path)
    assert psm.refuse(paths, "the split hash differs") == 3
    note = tool.note_of(tmp_path)
    assert [line for line in note.splitlines() if line.startswith("## ")][:9] == list(
        HEADINGS
    )
    assert "BYTE_FAIL" in section(note, "## 1. Label and hypotheses")
    assert "the split hash differs" in section(note, "## 1. Label and hypotheses")
    assert "`M_rows`" in note.split("## Fields not recorded")[1]


def test_no_decision_at_all_is_said_at_the_top(
    tool: ModuleType, tmp_path: Path
) -> None:
    note = tool.note_of(tmp_path)
    assert "`decision.json`: absent" in note.split("## 1.")[0]
    assert "| Label | not recorded |" in note


def test_an_unreadable_decision_is_said_at_the_top(
    tool: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "decision.json").write_text("{broken", encoding="utf-8")
    assert "`decision.json`: unreadable" in tool.note_of(tmp_path).split("## 1.")[0]


def test_the_fixed_gaps_are_not_recorded_in_every_note(
    tool: ModuleType, clean_run: Run
) -> None:
    """What section 10 asks for and no record carries (the report's list)."""
    note = tool.note_of(clean_run.root)
    coverage = section(note, "## 4. Coverage")
    assert "| Capped | not recorded |" in coverage
    assert "| Not applicable |" in coverage
    last = section(note, "## 9. Cost, records and departures")
    for item in (
        "LEDGER row: not recorded",
        "Departures from the pre-registration: not recorded",
        "Kernel CPU count and run wall time: not recorded",
        "Whether anyone read `journal.jsonl` before the run ended: not recorded",
    ):
        assert item in last


def test_f_r_and_the_path_come_from_the_decision(
    tool: ModuleType, clean_run: Run
) -> None:
    part = section(tool.note_of(clean_run.root), "## 9. Cost, records and departures")
    assert f"`F`: {IDENTITY['F']}" in part
    assert f"`R`: {IDENTITY['R']}" in part
    assert IDENTITY["prereg_sha256"] in part
    assert (
        "s3://delaymargin-results-482913/sciagent/p3-linux/"
        f"search-misses-{IDENTITY['F']}/"
    ) in part


def test_a_stop_note_is_copied(
    tool: ModuleType, provisional_run: Run, tmp_path: Path
) -> None:
    root = copy_of(provisional_run, tmp_path / "r")
    Run(root, provisional_run.decision).edit(
        lambda d: d.__setitem__("stop_note", "control room: disk")
    )
    part = section(tool.note_of(root), "## 1. Label and hypotheses")
    assert "control room: disk" in part


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


def test_timing_sums_the_jobs_by_stage_from_plain_decimals(
    tool: ModuleType, clean_run: Run
) -> None:
    part = section(tool.note_of(clean_run.root), "## 9. Cost, records and departures")
    assert "| hunt | 1 | 0.050 | 0.000 | 0.100 |" in part
    assert "| tier1 | 2 | 1.500 | 0.000 | 3.000 |" in part
    assert "| All | 3 | 1.550 | 0.000 | 3.100 |" in part


def test_a_torn_timing_line_is_left_out_and_said(
    tool: ModuleType, clean_run: Run, tmp_path: Path
) -> None:
    root = copy_of(clean_run, tmp_path / "r")
    with (root / "timing.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"job": "d", "sta')
    note = tool.note_of(root)
    assert "| All | 3 | 1.550 | 0.000 | 3.100 |" in note
    assert "`timing.jsonl`: 1 torn last line left out" in note


def test_a_broken_middle_line_is_said(
    tool: ModuleType, clean_run: Run, tmp_path: Path
) -> None:
    root = copy_of(clean_run, tmp_path / "r")
    path = root / "timing.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], "{broken", *lines[1:]]) + "\n", "utf-8")
    note = tool.note_of(root)
    assert "`timing.jsonl`: 1 unreadable line left out" in note


def test_a_job_without_cpu_figures_is_counted_not_zeroed(
    tool: ModuleType, clean_run: Run, tmp_path: Path
) -> None:
    root = copy_of(clean_run, tmp_path / "r")
    with (root / "timing.jsonl").open("a", encoding="utf-8") as handle:
        body = {"job": "e", "stage": "tier1", "wall_s": 3.6, "cpu_s": None}
        handle.write(json.dumps({**body, "cpu_children_s": None}) + "\n")
    part = section(tool.note_of(root), "## 9. Cost, records and departures")
    assert "Lines with no CPU figure: 1" in part


def test_no_timing_file_is_not_recorded(
    tool: ModuleType, clean_run: Run, tmp_path: Path
) -> None:
    root = copy_of(clean_run, tmp_path / "r")
    (root / "timing.jsonl").unlink()
    part = section(tool.note_of(root), "## 9. Cost, records and departures")
    assert "CPU hours: not recorded: `timing.jsonl` is absent" in part


# --------------------------------------------------------------------------
# Fixed words
# --------------------------------------------------------------------------


def prereg_section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def test_the_quoted_words_are_the_frozen_files(tool: ModuleType) -> None:
    text = PREREG.read_text(encoding="utf-8")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == psm.PREREG_SHA256
    for line in tool.NOT_COVERED_ROWS:
        assert f"\n{line}\n" in text
    assert f"\n{tool.NOT_COVERED_HEADER}\n" in text
    assert f"\n{tool.NOT_COVERED_RULE}\n" in text
    assert f"\n{tool.STATED_PLAINLY}\n" in text
    assert f"\n{tool.UNKNOWNS}\n" in text
    assert tool.CURVES in text
    assert tool.NO_DIFFERENCE in text
    assert tool.STORED_STAY in text
    assert tool.PROVISIONAL in text


def test_every_not_covered_row_of_the_text_is_quoted(tool: ModuleType) -> None:
    text = PREREG.read_text(encoding="utf-8")
    part = prereg_section(text, "### 2.5 Selection effects", "### 2.6 LLM units")
    rows = [x for x in part.splitlines() if x.startswith("|") and "**Not covered" in x]
    assert len(rows) == 3
    assert list(tool.NOT_COVERED_ROWS) == rows


def test_the_note_carries_the_quotes_whatever_m_is(
    tool: ModuleType, clean_run: Run, miss_run: Run
) -> None:
    for run in (clean_run, miss_run):
        part = section(tool.note_of(run.root), "## 8. Not covered and unknown")
        assert tool.STATED_PLAINLY in part
        assert tool.UNKNOWNS in part
        assert tool.CURVES in part
        for row in tool.NOT_COVERED_ROWS:
            assert row in part


# --------------------------------------------------------------------------
# It decides nothing, reads three files and writes none
# --------------------------------------------------------------------------


def test_the_label_is_copied_and_never_derived(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    root = copy_of(miss_run, tmp_path / "r")

    def plant(d: dict[str, Any]) -> None:
        d["label"] = "PLANTED_LABEL"
        d["label_line"] = "planted line"
        d["statement"] = "planted statement"

    Run(root, miss_run.decision).edit(plant)
    part = section(tool.note_of(root), "## 1. Label and hypotheses")
    assert "| Label | PLANTED_LABEL |" in part
    assert "planted line" in part
    assert "planted statement" in part
    assert "GO_SURVIVES_FOUND_MISSES" not in part


def test_the_source_names_no_label(tool: ModuleType) -> None:
    """The one exception is section 5's own sentence, quoted for a provisional run."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "BYTE_FAIL" in tool.PROVISIONAL
    assert source.count("BYTE_FAIL") == 1
    for label in psm.EXIT_CODES:
        if label != "BYTE_FAIL":
            assert label not in source


def test_the_tool_imports_neither_the_frozen_script_nor_the_analysis() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert "pilot_search_misses" not in names
    assert not {n for n in names if n.endswith(("analysis", "gonogo", "store"))}


def test_the_journal_is_never_read(
    tool: ModuleType, clean_run: Run, tmp_path: Path
) -> None:
    root = copy_of(clean_run, tmp_path / "r")
    before = tool.note_of(root)
    (root / "journal.jsonl").write_text("poison\n", encoding="utf-8")
    poisoned = tool.note_of(root)
    (root / "journal.jsonl").unlink()
    assert before == poisoned == tool.note_of(root)


def test_only_the_three_files_are_opened(
    tool: ModuleType, clean_run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    read_bytes = Path.read_bytes

    def spy(self: Path) -> bytes:
        seen.append(self.name)
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    tool.note_of(clean_run.root)
    assert sorted(set(seen)) == ["decision.json", "timing.jsonl", "units.jsonl"]


def test_records_has_no_default(tool: ModuleType) -> None:
    with pytest.raises(SystemExit):
        tool.main([])


def test_a_directory_that_does_not_exist_is_refused(
    tool: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tool.main(["--records", str(tmp_path / "nowhere")]) == 2
    assert capsys.readouterr().out == ""


def test_the_tool_writes_nothing_in_the_records(
    tool: ModuleType, clean_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    def listing() -> list[tuple[str, int, int]]:
        return [
            (p.name, p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(clean_run.root.iterdir())
        ]

    before = listing()
    assert tool.main(["--records", str(clean_run.root)]) == 0
    assert listing() == before
    assert capsys.readouterr().out == tool.note_of(clean_run.root)


# --------------------------------------------------------------------------
# Same records, same bytes
# --------------------------------------------------------------------------


def printed(root: Path, hash_seed: str) -> bytes:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(root)],
        env=env,
        capture_output=True,
        check=True,
    )
    return done.stdout


def test_a_rerun_is_byte_identical_across_processes_and_directories(
    miss_run: Run, tmp_path: Path
) -> None:
    other = copy_of(miss_run, tmp_path / "elsewhere")
    first = printed(miss_run.root, "1")
    assert first == printed(miss_run.root, "2")
    assert first == printed(other, "3")


def test_the_table_does_not_depend_on_the_order_of_the_lines(
    tool: ModuleType, miss_run: Run, tmp_path: Path
) -> None:
    root = copy_of(miss_run, tmp_path / "r")
    for name in ("units.jsonl", "timing.jsonl"):
        path = root / name
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    def without_digests(note: str) -> str:
        return re.sub(r"[0-9a-f]{64}", "", note)

    assert without_digests(tool.note_of(root)) == without_digests(
        tool.note_of(miss_run.root)
    )


def test_the_source_reads_no_clock() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "datetime" not in source
    assert "import time" not in source
