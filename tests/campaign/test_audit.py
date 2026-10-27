"""The defect-hunt checks (``sciagent.campaign.audit``) on small synthetic stores.

Cells are written through :meth:`ResultStore.put` with the driver's layout
(``unit``, ``result``, ``system_config`` / ``session_config``,
``session.record_dir``), so the checks read exactly what a campaign writes.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from audit_support import (
    FIT4,
    FIT5,
    clean_store,
    make_key,
    put_baseline,
    put_score,
    put_session,
    sha,
    unit_json,
)

from sciagent.campaign.audit import (
    GAP_THRESHOLD,
    check_store,
    compare_stores,
    gap_check,
    matches_env,
    same_bytes,
)
from sciagent.campaign.plan import CampaignError
from sciagent.campaign.store import DETAILS_DIR, LEDGER_FILE, ResultStore


def _gap(root: Path, env: str = "fit/5", **kw: float) -> Any:
    with ResultStore(root) as store:
        return gap_check(store, env, **kw)


# --------------------------------------------------------------------------
# Gap check
# --------------------------------------------------------------------------


def test_gap_is_per_event_minus_the_oracle_of_the_same_truth_and_seed(
    tmp_path: Path,
) -> None:
    report = _gap(clean_store(tmp_path / "s"))
    gaps = {(u.system, u.seed): u.gap for u in report.units}
    assert gaps[("B-lib", 1)] == -0.80 - -0.70
    assert gaps[("B-rand", 2)] == -0.75 - -0.70
    assert gaps[("ORACLE", 1)] == 0.0
    assert gaps[("AG-o-S/named", 1)] == -0.72 - -0.70
    assert [u.llm for u in report.units if u.system == "AG-o-S/named"] == [True]
    assert report.passed
    assert report.underfit.checked == 2


def test_a_gap_above_the_threshold_fails_and_one_at_it_passes(tmp_path: Path) -> None:
    step = 2.0**-7  # exact in binary, so the boundary is exact
    with ResultStore(tmp_path / "at") as store:
        put_baseline(store, "ORACLE", -1.0)
        put_baseline(store, "B-sparse", -1.0 + step)
    with ResultStore(tmp_path / "above") as store:
        put_baseline(store, "ORACLE", -1.0)
        put_baseline(store, "B-sparse", -1.0 + 2 * step)
    assert _gap(tmp_path / "at", threshold=step).passed
    above = _gap(tmp_path / "above", threshold=step)
    assert not above.passed
    assert [(u.system, u.gap) for u in above.above] == [("B-sparse", 2 * step)]
    assert GAP_THRESHOLD == 0.01


def test_the_default_threshold_is_the_gates_plus_one_hundredth(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70)
        put_baseline(store, "B-sparse", -0.68)
    report = _gap(tmp_path / "s")
    assert [u.system for u in report.above] == ["B-sparse"]


def test_an_oracle_underfit_flag_fails_the_gate(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70, underfit=True)
        put_baseline(store, "B-lib", -0.80)
    report = _gap(tmp_path / "s")
    assert report.underfit.flagged == (("dev-008", 1),)
    assert not report.passed


@pytest.mark.parametrize("failed", [False, True])
def test_an_unrecorded_flag_or_a_failed_oracle_fails_the_gate(
    tmp_path: Path, failed: bool
) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", None if failed else -0.70, underfit=None)
        put_baseline(store, "B-lib", -0.80)
    report = _gap(tmp_path / "s")
    assert report.underfit.unrecorded == (("dev-008", 1),)
    assert not report.passed
    if failed:  # B-lib then has no reference either
        assert [u.system for u in report.unreferenced] == ["B-lib"]


@pytest.mark.parametrize("oracle", [None, -math.inf])
def test_a_unit_without_a_finite_oracle_has_no_gap_and_fails(
    tmp_path: Path, oracle: float | None
) -> None:
    with ResultStore(tmp_path / "s") as store:
        if oracle is not None:
            put_baseline(store, "ORACLE", oracle)
        put_baseline(store, "ORACLE", -0.70, seed=2)
        put_baseline(store, "B-lib", -0.80)
        put_baseline(store, "B-lib", -0.80, seed=2)
    report = _gap(tmp_path / "s")
    assert ("B-lib", 1) in {(u.system, u.seed) for u in report.unreferenced}
    assert not report.passed


def test_a_failed_non_oracle_unit_has_no_gap_but_does_not_fail(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70)
        put_baseline(store, "B-rand", None)
    report = _gap(tmp_path / "s")
    assert [u.gap for u in report.units if u.system == "B-rand"] == [None]
    assert report.passed


def test_only_cells_of_the_named_fitter_are_read(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70)
        put_baseline(store, "B-lib", -0.80)
        put_baseline(store, "ORACLE", -0.90, env=FIT4, underfit=None)
        put_baseline(store, "B-sparse", -0.10, env=FIT4)
    five = _gap(tmp_path / "s")
    assert sorted(u.system for u in five.units) == ["B-lib", "ORACLE"]
    assert five.passed
    four = _gap(tmp_path / "s", env="fit/4")
    assert not four.passed  # B-sparse +0.80 above, and no underfit flag


def test_cells_under_two_fit_configurations_are_never_paired(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70, fit_config="a")
        put_baseline(store, "B-lib", -0.80, fit_config="b")
    report = _gap(tmp_path / "s")
    assert [u.system for u in report.unreferenced] == ["B-lib"]
    assert [u.oracle for u in report.units if u.system == "B-lib"] == [None]


def test_two_cells_of_one_system_in_one_group_are_refused(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70)
        put_baseline(store, "B-lib", -0.80)
        put_baseline(store, "B-lib", -0.81, variant=" (rerun)")
    with pytest.raises(CampaignError, match="two B-lib cells"):
        _gap(tmp_path / "s")


def test_an_empty_selection_does_not_pass(tmp_path: Path) -> None:
    clean_store(tmp_path / "s")
    report = _gap(tmp_path / "s", env="fit/9")
    assert report.units == ()
    assert not report.passed


def test_matches_env_reads_the_fitter_part_exactly() -> None:
    assert matches_env(FIT5, "fit/5")
    assert matches_env(FIT5, "sciagent.glm.fit/5")
    assert matches_env(FIT5, FIT5)
    assert not matches_env("pointproc|sciagent.glm.fit/15", "fit/5")
    assert not matches_env(FIT4, "fit/5")


# --------------------------------------------------------------------------
# Store consistency
# --------------------------------------------------------------------------


def _kinds(check: Any) -> list[str]:
    return sorted({f.kind for f in check.issues})


def test_a_store_written_by_the_store_passes(tmp_path: Path) -> None:
    check = check_store(clean_store(tmp_path / "s"))
    assert check.issues == ()
    assert check.warnings == ()
    assert check.cells == 8
    assert check.passed


def test_checking_a_missing_store_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(CampaignError, match="not a campaign store"):
        check_store(tmp_path / "nope")
    assert not (tmp_path / "nope").exists()


def _a_detail(root: Path, system: str = "B-rand") -> Path:
    for path in sorted((root / DETAILS_DIR).glob("*.json")):
        cell = json.loads(path.read_text(encoding="utf-8"))
        if cell["key"]["config"]["system"] == system:
            return path
    raise AssertionError(system)


def test_rewritten_detail_bytes_are_noncanonical(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    path = _a_detail(root)
    cell = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(cell, indent=1), encoding="utf-8")
    assert _kinds(check_store(root)) == ["noncanonical"]


def test_a_detail_reading_that_differs_from_the_ledger_is_found(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    path = _a_detail(root)
    data = path.read_text(encoding="utf-8")
    old = (-0.75).hex()
    assert old in data
    path.write_text(data.replace(old, (-0.5).hex()), encoding="utf-8")
    assert _kinds(check_store(root)) == ["reading_mismatch"]


def test_a_missing_detail_file_is_found(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    _a_detail(root).unlink()
    assert _kinds(check_store(root)) == ["missing_detail"]


def test_a_detail_under_another_name_is_found(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    a, b = _a_detail(root, "B-rand"), _a_detail(root, "B-lib")
    data_a = a.read_bytes()
    a.write_bytes(b.read_bytes())
    b.write_bytes(data_a)
    assert "detail_address" in _kinds(check_store(root))


def test_an_uncommitted_detail_and_a_stray_file_are_warnings(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    orphan = root / DETAILS_DIR / ("0" * 64 + ".json")
    orphan.write_text("{}", encoding="utf-8")
    (root / DETAILS_DIR / "notes.txt").write_text("x", encoding="utf-8")
    check = check_store(root)
    assert check.passed
    assert sorted(w.kind for w in check.warnings) == [
        "stray_file",
        "uncommitted_detail",
    ]


def _raw(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(root / LEDGER_FILE)


def test_a_dropped_append_only_trigger_is_found(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    with _raw(root) as con:
        con.execute("DROP TRIGGER cells_no_update")
    con.close()
    check = check_store(root)
    assert [(f.kind, f.where) for f in check.issues] == [
        ("missing_trigger", "cells_no_update")
    ]


def test_a_ledger_row_edited_behind_the_triggers_is_found(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    with _raw(root) as con:
        con.execute("DROP TRIGGER cells_no_update")
        con.execute("UPDATE cells SET digest = ? WHERE sequence = 2", ("f" * 64,))
        con.execute(
            "UPDATE cells SET reading_digest = ? WHERE sequence = 3", ("e" * 64,)
        )
    con.close()
    kinds = _kinds(check_store(root))
    assert {"missing_trigger", "ledger_address", "ledger_reading"} <= set(kinds)


def test_address_text_that_does_not_hash_to_the_key_is_found(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_baseline(store, "ORACLE", -0.70, text="hand-edited text")
    assert _kinds(check_store(tmp_path / "s")) == ["address_text"]


def test_a_unit_block_that_disagrees_with_the_key_is_found(tmp_path: Path) -> None:
    root = tmp_path / "s"
    with ResultStore(root) as store:
        key = make_key("baseline", "B-lib", "dev-008", 1, {"system_config": sha("t")})
        store.put(
            key,
            {"status": 0.0},
            {
                "unit": unit_json("baseline", "B-lib", "dev-009", 1),
                "result": {"error": "x"},
                "system_config": "t",
            },
        )
    check = check_store(root)
    assert [(f.kind, f.message) for f in check.issues] == [
        ("unit_mismatch", "unit truth 'dev-009', key 'dev-008'")
    ]


def test_a_score_whose_session_is_not_in_the_store_is_found(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_score(store, "AG-o-S/named", "a" * 64, -0.7)
    assert _kinds(check_store(tmp_path / "s")) == ["dangling_session"]


def test_a_session_without_its_record_dir_is_found(tmp_path: Path) -> None:
    with ResultStore(tmp_path / "s") as store:
        put_session(store, "AG-o-S/named", make_record=False)
    assert _kinds(check_store(tmp_path / "s")) == ["missing_record"]


def test_a_session_whose_record_file_is_gone_is_found(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    for record in root.glob("llm/*/record.json"):
        record.unlink()
    assert _kinds(check_store(root)) == ["missing_record"]


def test_a_timing_line_that_is_not_json_is_an_issue(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    with (root / "timing.jsonl").open("a", encoding="utf-8") as f:
        f.write("{truncated\n")
        f.write(json.dumps({"digest": "b" * 64}) + "\n")
    check = check_store(root)
    assert _kinds(check) == ["timing_line"]
    assert [w.kind for w in check.warnings] == ["timing_digest"]


# --------------------------------------------------------------------------
# Byte comparison
# --------------------------------------------------------------------------


def test_two_stores_written_alike_are_identical(tmp_path: Path) -> None:
    diff = compare_stores(clean_store(tmp_path / "a"), clean_store(tmp_path / "b"))
    assert diff.identical
    assert diff.same == 8


def test_a_cell_whose_numbers_differ_is_found_at_the_same_address(
    tmp_path: Path,
) -> None:
    a = clean_store(tmp_path / "a")
    b = clean_store(tmp_path / "b", blib=-0.81)
    diff = compare_stores(a, b)
    assert not diff.identical
    assert len(diff.differing) == 2  # B-lib on seeds 1 and 2
    assert diff.only_a == diff.only_b == ()


def test_cells_in_one_store_only_are_listed(tmp_path: Path) -> None:
    a = clean_store(tmp_path / "a")
    b = clean_store(tmp_path / "b")
    with ResultStore(b) as store:
        extra = put_baseline(store, "B-sym", -0.9)
    diff = compare_stores(a, b)
    assert diff.only_b == (str(extra.digest),)
    assert diff.only_a == ()
    assert not diff.identical
    assert compare_stores(b, a).only_a == (str(extra.digest),)


def test_a_missing_detail_file_in_either_store_differs(tmp_path: Path) -> None:
    a = clean_store(tmp_path / "a")
    b = clean_store(tmp_path / "b")
    _a_detail(a).unlink()
    assert len(compare_stores(a, b).differing) == 1
    assert len(compare_stores(b, a).differing) == 1


def test_detail_bytes_that_differ_under_an_equal_reading_are_found(
    tmp_path: Path,
) -> None:
    a = clean_store(tmp_path / "a")
    b = clean_store(tmp_path / "b")
    path = _a_detail(b)
    path.write_bytes(path.read_bytes().replace(b"Base()", b"Base( )"))
    assert compare_stores(a, b).differing == (path.stem,)


def test_a_comparison_can_be_restricted_to_some_kinds_of_cell(tmp_path: Path) -> None:
    a = clean_store(tmp_path / "a")
    b = clean_store(tmp_path / "b")
    with ResultStore(b) as store:
        extra = put_session(store, "AG-c-S/named")
    assert compare_stores(a, b).only_b == (str(extra.digest),)
    some = compare_stores(a, b, kinds=("baseline", "llm-score"))
    assert some.identical
    assert some.same == 7
    with pytest.raises(CampaignError, match="unknown cell kind"):
        compare_stores(a, b, kinds=("baselines",))


def test_same_bytes_names_the_files_that_differ(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i}.md" for i in range(3)]
    for p, text in zip(paths, ("x", "x", "y"), strict=True):
        p.write_text(text, encoding="utf-8")
    assert same_bytes(paths) == (paths[2],)
    assert same_bytes(paths[:2]) == ()
