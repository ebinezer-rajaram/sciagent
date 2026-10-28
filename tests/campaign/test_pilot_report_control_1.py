"""The control-1 headline of the pilot report says what the verdict's rule says.

Control 1 (P4-PROPOSAL §2.4, SPEC §6.4) has three parts: the paired margin
gap(ORACLE) - gap(B-lib) > SE per truth (a failing truth is dropped and the
drop reported; none kept fails the control), zero ``oracle_underfit`` flags,
and no ORACLE at -inf. The red team's finding on the mini-b report: one seed
per truth, so no margin could be computed, yet the headline printed PASS
because only the underfit and -inf parts were read. The label is built from
the verdict's own checks (``analysis.verdict``); nothing is recomputed.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from campaign_support import entry
from test_campaign_analysis import _table, _with, row

from sciagent.campaign.analysis import Table, derive, verdict

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SYSTEMS = ("ORACLE", "B-lib", "B-np", "B-rand", "B-sym", "B-sparse", "planted-hint")


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


def _headline(report: ModuleType, table: Table) -> str:
    v = verdict(table, np.random.default_rng(0), resamples=200)
    assert v.underfit is not None
    text = report.control_1_headline(v, sorted(table.truths), v.underfit, v.oracle_inf)
    assert isinstance(text, str)
    return text


def _one_seed(truths: tuple[str, ...]) -> Table:
    """The mini-b shape: every system on seed 1 only, ORACLE ahead of B-lib."""
    rows = [
        row(t, 1, s, -0.70 - 0.01 * i) for t in truths for i, s in enumerate(SYSTEMS)
    ]
    return Table({t: entry("hawkes") for t in truths}, tuple(derive(rows)), (), {})


def test_one_seed_per_truth_is_not_checkable_never_pass(report: ModuleType) -> None:
    table = _one_seed(("a", "b", "c"))
    text = _headline(report, table)
    assert not text.startswith("**PASS**"), text
    assert text.startswith("**NOT CHECKABLE**"), text
    assert "< 2 paired" in text and "['a', 'b', 'c']" in text, text


def test_one_seed_with_an_underfit_flag_fails_and_names_both(
    report: ModuleType,
) -> None:
    base = _one_seed(("a", "b"))
    flagged = tuple(
        replace(r, oracle_underfit=True)
        if (r.truth, r.system) == ("a", "ORACLE")
        else r
        for r in base.rows
    )
    text = _headline(report, replace(base, rows=flagged))
    assert text.startswith("**FAIL**"), text
    assert "oracle_underfit flagged" in text and "a s1" in text, text
    assert "not checkable" in text and "['a', 'b']" in text, text


def test_the_full_table_passes_every_part(report: ModuleType) -> None:
    text = _headline(report, _table(0.0))
    assert text.startswith("**PASS**"), text
    assert "5 of 5 truths" in text and "15 units" in text, text
    assert "dropped" not in text and "not checkable" not in text, text


def test_a_dropped_truth_is_reported_beside_the_pass(report: ModuleType) -> None:
    """The preregistered rule drops it and reports the drop: not a C1 failure
    while another truth is kept (the verdict's own reading)."""
    lib_ahead = {("null", s, "B-lib"): -0.60 for s in (1, 2, 3)}
    text = _headline(report, _with(_table(0.0), lib_ahead))
    assert text.startswith("**PASS**"), text
    assert "4 of 5 truths" in text and "['null']" in text, text


def test_no_truth_kept_fails(report: ModuleType) -> None:
    lib_ahead = {
        (t, s, "B-lib"): -0.60
        for t in ("hawkes", "size_excitation", "null", "t4", "t5")
        for s in (1, 2, 3)
    }
    text = _headline(report, _with(_table(0.0), lib_ahead))
    assert text.startswith("**FAIL**"), text
    assert "no truth kept" in text, text


def test_a_truth_without_two_finite_pairs_is_named_inconclusive(
    report: ModuleType,
) -> None:
    two = _with(_table(0.0), {("hawkes", s, "B-lib"): float("-inf") for s in (2, 3)})
    text = _headline(report, two)
    assert text.startswith("**PASS**"), text
    assert "4 of 5 truths" in text, text
    assert "not checkable" in text and "['hawkes']" in text, text


def test_an_oracle_minus_inf_unit_still_fails(report: ModuleType) -> None:
    t = _with(_table(0.0), {("hawkes", 3, "ORACLE"): float("-inf")})
    text = _headline(report, t)
    assert text.startswith("**FAIL**"), text
    assert "-inf" in text and "hawkes s3" in text, text
