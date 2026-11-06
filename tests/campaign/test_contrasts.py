"""P4's contrasts, C1 to C4 (prereg 2026-11-05 section 2.7): an instrument, so
these tests came first (SPEC §6.3). Toy tables only: nothing here opens a store,
and no test reads the reads' frozen root, so a toy draws from root 11.

The toy world has six truths on three seeds. Every per-event value on it is a
multiple of 1/16, so the arithmetic in a test's comment is the arithmetic the
code does. Unless a test changes them:

=====================  =========  =======================================
ORACLE                 -0.5       the gap's reference: gap 0
B-lib                  -0.6875    gap -3/16, so control 1 passes
B-rand                 -0.75      gap -1/4, the lowest contrast row
planted-hint           -0.5       beats B-rand by 1/4, so control 2 passes
B-sym@10F (seed 1)     -0.5       gap 0, so control 3 passes
B-sym, B-sparse, arms  -0.625     gap -1/8, so every read starts at 0
B-np                   -2.0       not a contrast row
AG-c-H/named           -3.0       not a contrast row
=====================  =========  =======================================

So a row that takes a floor takes -0.75, gap -1/4, unless the test says otherwise.
"""

from __future__ import annotations

import inspect
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, cast

import numpy as np
import pytest
from campaign_support import entry

from sciagent.campaign import analysis, contrasts, execute
from sciagent.campaign.analysis import Row, Session, Table, derive, row_from_cell
from sciagent.campaign.contrasts import (
    CONTROL_2_STREAM,
    FAILED_FIT,
    READS,
    ContrastRead,
    RowKey,
    ScriptedControls,
    contrast_rows,
    floor_rows,
    read_contrasts,
    read_floors,
    same_read,
)
from sciagent.campaign.plan import (
    BASELINES,
    P4_ARMS,
    P4_SONNET_ARMS,
    CampaignConfig,
    CampaignError,
    TruthEntry,
    baseline_units,
    llm_units,
)
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus
from sciagent.scoring.errors import ScoringError

TRUTHS = ("t1", "t2", "t3", "t4", "t5", "t6")
OUT_OF_DICTIONARY = ("t1", "t2")
SEEDS = (1, 2, 3)
ROOT = 11

AGC = "AG-c-S/named+link-neutral"
AGC_RESCALED = "AG-c-S/rescaled+link-neutral"
AGC_ANON = "AG-c-S/anon+link-neutral"
AGO = "AG-o-S/named+link-neutral"
AGO_RESCALED = "AG-o-S/rescaled+link-neutral"
AGO_ANON = "AG-o-S/anon+link-neutral"
ARMS = (AGC, AGC_RESCALED, AGC_ANON, AGO, AGO_RESCALED, AGO_ANON)

ORACLE = "ORACLE"
B_LIB = "B-lib"
B_NP = "B-np"
B_RAND = "B-rand"
B_SYM = "B-sym"
B_SPARSE = "B-sparse"
PLANTED = "planted-hint"
B_SYM_10F = "B-sym@10F"
HAIKU = "AG-c-H/named+link-neutral"

NAMES = (
    "C1",
    "C2-AG-c-names",
    "C2-AG-c-time",
    "C2-AG-o-names",
    "C2-AG-o-time",
    "C3-free",
    "C4",
)
C2_READS = NAMES[1:5]
OTHER_READS = ("C1", "C3-free", "C4")

PASS = ScriptedControls(naming_null=True)
FAIL_NAMING = ScriptedControls(naming_null=False)

FIT_FAILURE = "the submitted structure could not be fitted: abc123"

type World = dict[RowKey, Row]


def _row(truth: str, seed: int, system: str, per_event: float) -> Row:
    return Row(
        truth=truth,
        seed=seed,
        system=system,
        llm=system.startswith("AG-"),
        ok=True,
        submitted=True,
        per_event=per_event,
        exact=False,
        distance=0.5,
        has_structure=True,
        similarity=0.9,
        fits_used=1,
        structure=None,
        curve=(),
        error=None,
        oracle_underfit=False if system == ORACLE else None,
    )


def world() -> World:
    """The toy world's raw rows, by key (module docstring)."""
    values = {
        ORACLE: -0.5,
        B_LIB: -0.6875,
        B_RAND: -0.75,
        PLANTED: -0.5,
        B_SYM: -0.625,
        B_SPARSE: -0.625,
        B_NP: -2.0,
        HAIKU: -3.0,
        **dict.fromkeys(ARMS, -0.625),
    }
    rows: World = {}
    for t in TRUTHS:
        for seed in SEEDS:
            for system, value in values.items():
                rows[(t, seed, system)] = _row(t, seed, system, value)
        rows[(t, 1, B_SYM_10F)] = _row(t, 1, B_SYM_10F, -0.5)
    return rows


def toy_truth(truth_id: str, *, in_dictionary: bool = True) -> TruthEntry:
    return replace(entry("hawkes"), id=truth_id, in_dictionary=in_dictionary)


def truths() -> dict[str, TruthEntry]:
    return {t: toy_truth(t, in_dictionary=t not in OUT_OF_DICTIONARY) for t in TRUTHS}


def tabled(rows: World, sessions: Sequence[Session] = ()) -> Table:
    return Table(truths(), tuple(derive(list(rows.values()))), tuple(sessions), {})


def score(rows: World, truth: str, seed: int, system: str, value: float) -> None:
    rows[(truth, seed, system)] = replace(rows[(truth, seed, system)], per_event=value)


def score_all(rows: World, system: str, value: float) -> None:
    for (t, seed, s), row in list(rows.items()):
        if s == system:
            rows[(t, seed, s)] = replace(row, per_event=value)


def no_submission(rows: World, key: RowKey) -> None:
    rows[key] = replace(rows[key], submitted=False, per_event=None, has_structure=False)


def failed(rows: World, key: RowKey, message: str) -> None:
    rows[key] = replace(
        rows[key],
        ok=False,
        submitted=False,
        per_event=None,
        error="CampaignError",
        message=message,
    )


def read(
    table: Table,
    controls: ScriptedControls = PASS,
    *,
    seed: int = ROOT,
    resamples: int = 200,
) -> Mapping[str, ContrastRead]:
    return read_contrasts(table, controls, seed, resamples)


def substituted(table: Table, corrected: Mapping[RowKey, float]) -> Table:
    """Section 2.7's substitution, as the text writes it."""
    rows = [
        replace(
            r,
            per_event=corrected.get((r.truth, r.seed, r.system), r.per_event),
        )
        for r in table.rows
    ]
    return replace(table, rows=tuple(derive(rows)))


def not_reportable(r: ContrastRead) -> bool:
    return (
        not r.reportable
        and math.isnan(r.point)
        and math.isnan(r.low)
        and math.isnan(r.high)
    )


def all_same(a: Mapping[str, ContrastRead], b: Mapping[str, ContrastRead]) -> bool:
    return list(a) == list(b) and all(same_read(a[k], b[k]) for k in a)


# --------------------------------------------------------------------------
# The reads
# --------------------------------------------------------------------------


def test_the_reads_are_the_seven_of_section_2_7_and_no_other() -> None:
    assert [spec.name for spec in READS] == list(NAMES)
    got = read(tabled(world()))
    assert list(got) == list(NAMES)
    assert not any("C5" in name or "boed" in name for name in got)


def test_each_read_compares_the_systems_the_text_names() -> None:
    rows = world()
    score_all(rows, AGC, -0.5)
    score_all(rows, AGC_RESCALED, -0.5625)
    score_all(rows, AGC_ANON, -0.6875)
    score_all(rows, AGO, -0.25)
    score_all(rows, AGO_RESCALED, -0.375)
    score_all(rows, AGO_ANON, -0.4375)
    score_all(rows, B_SYM, -0.6875)
    score_all(rows, B_SPARSE, -0.5)  # in dictionary: C4 must not read these
    for t in OUT_OF_DICTIONARY:
        for seed in SEEDS:
            score(rows, t, seed, B_SPARSE, -0.625)
    got = read(tabled(rows))
    points = {name: r.point for name, r in got.items()}
    assert points == pytest.approx(
        {
            "C1": 0.1875,  # AG-c-S/named minus B-sym
            "C2-AG-c-time": 0.0625,  # named minus rescaled
            "C2-AG-c-names": 0.125,  # rescaled minus anon
            "C2-AG-o-time": 0.125,
            "C2-AG-o-names": 0.0625,
            "C3-free": 0.25,  # AG-o-S minus AG-c-S
            "C4": 0.125,  # AG-c-S minus B-sparse, out-of-dictionary truths
        }
    )
    assert {name: r.n for name, r in got.items()} == {
        **dict.fromkeys(NAMES, len(TRUTHS)),
        "C4": len(OUT_OF_DICTIONARY),
    }
    assert all(r.reportable for r in got.values())


def test_a_paired_ci_by_hand_on_two_truths() -> None:
    """Two units: a resample's mean is d1, (d1 + d2) / 2 or d2, with chance 1/4,
    1/2 and 1/4. So at 10,000 resamples the 2.5% point is d1 and the 97.5% is d2,
    whatever the stream. C4 reads t1 and t2 only.

    t1: gap 0 minus gap -1/8 is 1/8. t2: gap 1/4 minus gap -1/8 is 3/8.
    """
    rows = world()
    for seed in SEEDS:
        score(rows, "t1", seed, AGC, -0.5)
        score(rows, "t2", seed, AGC, -0.25)
    got = read(tabled(rows), resamples=10_000)["C4"]
    assert (got.point, got.low, got.high, got.n) == (0.25, 0.125, 0.375, 2)
    assert got.reportable


def test_a_paired_ci_by_hand_with_its_resamples_written_out() -> None:
    """C1 on d = 0, 1/8, ..., 5/8 for t1 to t6, at five resamples.

    C1 is read 0, so its stream is ``SeedSequence([11, 0])``, and the indices are
    these, in truth positions; ``d`` is in eighths, so a resample's mean is its
    index sum over 48:

        0 0 4 2 3 3   12
        4 0 2 0 2 5   13
        3 0 3 0 4 5   15
        5 3 5 2 0 3   18
        2 3 5 1 5 0   16

    Sorted: 12, 13, 15, 16, 18. The 2.5% point sits at position 0.1 of 4 and the
    97.5% at 3.9, so low = 12.1 / 48 and high = 17.8 / 48. Point: 15 / 48.
    """
    index = (
        np.random.default_rng(np.random.SeedSequence([ROOT, 0]))
        .integers(0, 6, size=(5, 6))
        .tolist()
    )
    assert index == [
        [0, 0, 4, 2, 3, 3],
        [4, 0, 2, 0, 2, 5],
        [3, 0, 3, 0, 4, 5],
        [5, 3, 5, 2, 0, 3],
        [2, 3, 5, 1, 5, 0],
    ]
    rows = world()
    for k, t in enumerate(TRUTHS):
        for seed in SEEDS:
            score(rows, t, seed, AGC, -0.625 + k / 8)
    got = read(tabled(rows), resamples=5)["C1"]
    assert got.point == pytest.approx(15 / 48, abs=1e-12)
    assert got.low == pytest.approx(12.1 / 48, abs=1e-12)
    assert got.high == pytest.approx(17.8 / 48, abs=1e-12)
    assert got.n == 6


def test_a_planted_separation_is_seen_and_a_null_is_not() -> None:
    """The positive control of the read itself: a shift gives a CI above zero."""
    noise = np.random.default_rng(np.random.SeedSequence([ROOT, 77]))
    rows = world()
    for t in TRUTHS:
        value = -0.625 + 0.25 + float(noise.uniform(-1 / 32, 1 / 32))
        for seed in SEEDS:
            score(rows, t, seed, AGC, value)
    got = read(tabled(rows), resamples=2000)["C1"]
    assert got.low > 0.0
    null = read(tabled(world()), resamples=2000)["C1"]
    assert (null.point, null.low, null.high) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# The pair rule
# --------------------------------------------------------------------------


def _floored_by_inf(rows: World, key: RowKey) -> None:
    score(rows, key[0], key[1], key[2], -math.inf)


def _floored_by_no_submission(rows: World, key: RowKey) -> None:
    no_submission(rows, key)


def _floored_by_failed_fit(rows: World, key: RowKey) -> None:
    failed(rows, key, FIT_FAILURE)


@pytest.mark.parametrize(
    "outcome",
    [_floored_by_inf, _floored_by_no_submission, _floored_by_failed_fit],
    ids=["minus-inf", "no-submission", "could-not-be-fitted"],
)
def test_each_outcome_takes_the_floor_of_its_truth_and_seed(outcome: object) -> None:
    """The floor on (t1, 1) is B-rand's -0.75: B-np and AG-c-H lie below it and
    are not contrast rows. So t1's AG-c-S gaps are -1/4, -1/8, -1/8, mean -1/6,
    against B-sym's -1/8, so d = -1/24 on t1 and 0 elsewhere: point -1/144."""
    assert callable(outcome)
    rows = world()
    outcome(rows, ("t1", 1, AGC))
    got = read(tabled(rows))["C1"]
    assert got.reportable and got.n == 6
    assert got.point == pytest.approx(-1 / 144, abs=1e-12)
    assert ("t1", 1, AGC) in got.rows


def test_the_floor_is_recomputed_per_truth_and_seed() -> None:
    rows = world()
    score(rows, "t1", 1, B_RAND, -0.875)  # seed 1's floor only
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t1", 2, AGC, -math.inf)
    out, floored = floor_rows(tuple(rows[k] for k in sorted(rows) if _contrast(k)))
    by = {(r.truth, r.seed, r.system): r for r in out}
    assert by[("t1", 1, AGC)].per_event == -0.875
    assert by[("t1", 2, AGC)].per_event == -0.75
    assert set(floored) == {("t1", 1, AGC), ("t1", 2, AGC)}


def _contrast(key: RowKey) -> bool:
    return key[2] not in (B_NP, HAIKU)


def test_oracle_and_b_lib_are_never_floored() -> None:
    rows = world()
    score(rows, "t1", 1, ORACLE, -math.inf)
    score(rows, "t1", 1, B_LIB, -math.inf)
    failed(rows, ("t2", 1, B_LIB), FIT_FAILURE)
    no_submission(rows, ("t3", 1, ORACLE))
    out, floored = floor_rows(tuple(rows[k] for k in sorted(rows) if _contrast(k)))
    by = {(r.truth, r.seed, r.system): r for r in out}
    assert not floored
    assert by[("t1", 1, ORACLE)].per_event == -math.inf
    assert by[("t1", 1, B_LIB)].per_event == -math.inf
    assert by[("t2", 1, B_LIB)].per_event is None
    assert by[("t3", 1, ORACLE)].per_event is None


def test_a_baseline_side_takes_the_floor() -> None:
    """B-sym at -inf on (t1, 1) takes -0.75, so its t1 gaps are -1/4, -1/8, -1/8,
    mean -1/6, against AG-c-S's -1/8: d = +1/24 on t1, point +1/144. The pilot's
    floored rows on C1's sides were all of this kind (PILOT.md)."""
    rows = world()
    score(rows, "t1", 1, B_SYM, -math.inf)
    got = read(tabled(rows))["C1"]
    assert got.point == pytest.approx(1 / 144, abs=1e-12)
    assert ("t1", 1, B_SYM) in got.rows


def test_b_lib_sets_a_floor() -> None:
    """With B-rand raised, B-lib's -0.6875 is the lowest finite row on (t1, 1), as
    it was on 54 of the pilot's 60 floors. AG-c-S's t1 gaps are -3/16, -1/8, -1/8,
    mean -7/48, against -1/8: d = -1/48 on t1, point -1/288."""
    rows = world()
    score(rows, "t1", 1, B_RAND, -0.5)
    score(rows, "t1", 1, AGC, -math.inf)
    got = read(tabled(rows))["C1"]
    assert got.point == pytest.approx(-1 / 288, abs=1e-12)


def test_the_oracle_sets_a_floor() -> None:
    """A gap reference takes no floor, and it still sets one."""
    rows = world()
    for key in [
        k for k in rows if k[:2] == ("t1", 1) and _contrast(k) and k[2] != ORACLE
    ]:
        score(rows, *key, -0.25)
    score(rows, "t1", 1, AGC, -math.inf)
    out, _ = floor_rows(tuple(rows[k] for k in sorted(rows) if _contrast(k)))
    by = {(r.truth, r.seed, r.system): r for r in out}
    assert by[("t1", 1, AGC)].per_event == -0.5


def test_floor_rows_gives_the_row_or_rows_that_set_each_floor() -> None:
    """A tie at the lowest finite value is set by every row that holds it, and a
    system with no floored row sets nothing by being in the table."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t2", 1, AGC, -math.inf)
    score(rows, "t2", 1, B_SPARSE, -0.75)  # level with B-rand
    _, floored = floor_rows(tuple(rows[k] for k in sorted(rows) if _contrast(k)))
    assert floored == {
        ("t1", 1, AGC): (("t1", 1, B_RAND),),
        ("t2", 1, AGC): (("t2", 1, B_RAND), ("t2", 1, B_SPARSE)),
    }


def test_rows_hold_what_set_a_floor_on_the_reads_own_rows() -> None:
    """AG-c-S's floor on (t1, 1) is B-rand's, so C1, which reads AG-c-S there, holds
    B-rand's row, and only that (truth, seed)'s. A read of neither side does not."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    got = read(tabled(rows))
    sides = {(t, s, x) for t in TRUTHS for s in SEEDS for x in (AGC, B_SYM, ORACLE)}
    assert set(got["C1"].rows) == sides | {("t1", 1, B_RAND)}
    assert all(("t1", 1, B_RAND) in got[name].rows for name in ("C3-free", "C4"))
    assert all(
        ("t1", 1, B_RAND) not in got[name].rows
        for name in ("C2-AG-o-names", "C2-AG-o-time", "C2-AG-c-names")
    )


def test_b_lib_is_in_rows_when_it_sets_a_floor_and_only_then() -> None:
    rows = world()
    assert not any(k[2] == B_LIB for k in read(tabled(rows))["C1"].rows)
    score(rows, "t1", 1, B_RAND, -0.5)
    score(rows, "t1", 1, AGC, -math.inf)
    got = read(tabled(rows))["C1"]
    assert [k for k in got.rows if k[2] == B_LIB] == [("t1", 1, B_LIB)]


def test_substituting_only_the_row_that_sets_a_floor_shows_in_the_reads_rows() -> None:
    """Finding 3: this read moves, and neither side's row is substituted.

    B-rand falls to -1.75, so AG-c-S's gaps on t1 are -5/4, -1/8, -1/8, mean -1/2,
    against -1/8: d = -3/8 on t1, point -1/16 where it was -1/144."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    table = tabled(rows)
    key = ("t1", 1, B_RAND)
    stored = read(table)["C1"]
    fixed = read(substituted(table, {key: -1.75}))["C1"]
    assert stored.point == pytest.approx(-1 / 144, abs=1e-12)
    assert fixed.point == pytest.approx(-1 / 16, abs=1e-12)
    assert key in stored.rows and key in fixed.rows


def test_a_substitution_can_pass_a_floor_to_another_row() -> None:
    """Raised above B-lib, B-rand no longer sets the floor and B-lib does. So each
    table's ``rows`` name the row that set the floor on that table."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    table = tabled(rows)
    key = ("t1", 1, B_RAND)
    stored = read(table)["C1"]
    fixed = read(substituted(table, {key: -0.5}))["C1"]
    assert key in stored.rows and ("t1", 1, B_LIB) not in stored.rows
    assert key not in fixed.rows and ("t1", 1, B_LIB) in fixed.rows


def test_a_row_below_the_floor_after_substitution_enters_only_the_fixed_rows() -> None:
    """B-sparse is no side of C1, and at -1.75 it takes the floor from B-rand: it is
    in ``fixed``'s rows and not in ``stored``'s."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    table = tabled(rows)
    key = ("t1", 1, B_SPARSE)
    stored = read(table)["C1"]
    fixed = read(substituted(table, {key: -1.75}))["C1"]
    assert key not in stored.rows and key in fixed.rows


def test_rows_stay_sorted_and_hold_no_key_twice() -> None:
    """Both sides floored by one row, and a side that sets the other's floor: each
    key is still in ``rows`` once."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t1", 1, B_SYM, -math.inf)  # both floored by B-rand
    score(rows, "t2", 1, AGC, -1.0)  # below B-rand: it sets B-sym's floor
    score(rows, "t2", 1, B_SYM, -math.inf)
    got = read(tabled(rows))["C1"]
    assert list(got.rows) == sorted(set(got.rows))
    assert ("t1", 1, B_RAND) in got.rows
    assert ("t2", 1, B_RAND) not in got.rows


def test_a_floored_row_on_an_unmatched_seed_adds_nothing_to_rows() -> None:
    """AG-c-S takes a floor on (t1, 1), but B-sym has no row there, so the pair is
    not read: what set that floor did not act on the read."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    failed(rows, ("t1", 1, B_SYM), "the session timed out")
    got = read(tabled(rows))["C1"]
    assert ("t1", 1, AGC) not in got.rows
    assert ("t1", 1, B_RAND) not in got.rows


def test_a_failure_of_any_other_cause_is_absent_and_seeds_stay_matched() -> None:
    """B-sym's seed 1 is at gap 0, against -1/8 on its other seeds, and AG-c-S
    has no row on seed 1. Both sides are then averaged over seeds 2 and 3 only, so
    d on t1 is 0. With each side over its own seeds it would be +1/24."""
    rows = world()
    score(rows, "t1", 1, B_SYM, -0.5)
    failed(rows, ("t1", 1, AGC), "the session timed out")
    got = read(tabled(rows))["C1"]
    assert got.point == 0.0
    assert got.n == 6
    assert ("t1", 1, AGC) not in got.rows
    assert ("t1", 1, B_SYM) not in got.rows
    assert ("t1", 1, ORACLE) not in got.rows
    assert ("t1", 2, AGC) in got.rows


def _void() -> VoidStatus:
    return VoidStatus(True, ("foreign tool: x",), 0, True)


def test_a_void_row_is_absent_and_sets_no_floor() -> None:
    """load_rows drops a void's score row. Should one reach the table it is still
    no outcome of the system: it joins no pair and sets no floor."""
    rows = world()
    score(rows, "t1", 1, AGC, -0.5)
    score(rows, "t1", 1, AGO, -0.9375)
    score(rows, "t1", 1, AGC_ANON, -math.inf)
    session = Session("t1", 1, AGO, {}, {}, _void(), "env", "digest")
    plain = tabled(rows, (session,))
    got = read(plain)
    assert ("t1", 1, AGO) not in got["C3-free"].rows
    assert ("t1", 1, AGC) not in got["C3-free"].rows
    # AG-c-S/anon floors at B-rand's -0.75, not at the void's -0.9375.
    assert read_floors(plain, PASS, ROOT, 200)["C2-AG-c-names"].floored == 1
    assert got["C2-AG-c-names"].point == pytest.approx(1 / 144, abs=1e-12)
    # So the read's rows name B-rand as the floor's setter, and never the void.
    assert ("t1", 1, B_RAND) in got["C2-AG-c-names"].rows
    assert ("t1", 1, AGO) not in got["C2-AG-c-names"].rows


def test_a_live_sessions_row_stays_in() -> None:
    """A table holds a session per LLM unit, and only a void one takes its row."""
    live = VoidStatus(False, (), 0, False)
    session = Session("t1", 1, AGC, {}, {}, live, "env", "digest")
    got = read(tabled(world(), (session,)))["C1"]
    assert ("t1", 1, AGC) in got.rows


def test_rows_with_no_finite_row_on_their_truth_and_seed_are_absent() -> None:
    rows = world()
    for key in [k for k in rows if k[:2] == ("t3", 1)]:
        del rows[key]
    for system in (AGC, B_SYM):
        rows[("t3", 1, system)] = _row("t3", 1, system, -math.inf)
    got = read(tabled(rows))["C1"]
    assert got.reportable and got.n == 6
    assert ("t3", 1, AGC) not in got.rows
    assert ("t3", 2, AGC) in got.rows
    assert got.point == 0.0


def test_a_truth_with_no_seed_in_common_leaves_the_read_and_is_counted() -> None:
    rows = world()
    del rows[("t4", 1, AGC)]
    del rows[("t4", 2, AGC)]
    del rows[("t4", 3, B_SYM)]
    table = tabled(rows)
    got = read(table)["C1"]
    assert got.n == 5
    assert all(key[0] != "t4" for key in got.rows)
    floors = read_floors(table, PASS, ROOT, 200)
    assert floors["C1"].no_common_seed == 1
    assert floors["C3-free"].no_common_seed == 0


def test_no_common_seed_counts_the_read_and_not_its_rows_left_out() -> None:
    """Round 3, finding 9. Every seed of t3 floored: the read keeps t3 and S6's
    rows-left-out read loses it, and the count is of the read."""
    rows = world()
    for seed in SEEDS:
        score(rows, "t3", seed, AGC, -math.inf)
    table = tabled(rows)
    assert read(table)["C1"].n == 6
    got = read_floors(table, PASS, ROOT, 200)["C1"]
    assert got.floored == 3
    assert got.without.n == 5
    assert got.no_common_seed == 0


def test_c4_counts_its_own_truths_as_no_common_seed_and_no_other() -> None:
    """C4 reads the out-of-dictionary truths, t1 and t2. An in-dictionary truth was
    never C4's to lose, so it does not count, whole or with its B-sparse rows gone.
    An out-of-dictionary truth that loses them does."""
    assert read_floors(tabled(world()), PASS, ROOT, 200)["C4"].no_common_seed == 0
    rows = world()
    for seed in SEEDS:
        del rows[("t3", seed, B_SPARSE)]
    assert read_floors(tabled(rows), PASS, ROOT, 200)["C4"].no_common_seed == 0
    for seed in SEEDS:
        del rows[("t1", seed, B_SPARSE)]
    table = tabled(rows)
    got = read_floors(table, PASS, ROOT, 200)["C4"]
    assert got.no_common_seed == 1
    assert read(table)["C4"].n == 1


def test_the_floor_reads_only_contrast_rows_and_keeps_rows_it_does_not_touch() -> None:
    """Round 2, finding 4: a floor from B-np would be a different number."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    wide = tabled(rows)
    without = {k: v for k, v in rows.items() if _contrast(k)}
    assert all_same(read(wide), read(tabled(without)))


def test_a_b_sym_at_10f_row_can_set_the_floor() -> None:
    """Round 3, finding 9. The floor's domain is the contrast rows, and B-sym@10F is
    one. Lowest on (t1, 1), it sets the floor and is in the read's rows."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t1", 1, B_SYM_10F, -0.875)
    got = read(tabled(rows))["C1"]
    assert got.reportable
    # Floor -0.875: gap -3/8, mean (-3/8 - 1/8 - 1/8) / 3 = -5/24, d = -1/12.
    assert got.point == pytest.approx(-1 / 72, abs=1e-12)
    assert ("t1", 1, B_SYM_10F) in got.rows


# --------------------------------------------------------------------------
# A row crossing the -inf boundary under substitution
# --------------------------------------------------------------------------


def test_a_row_that_falls_to_minus_inf_is_floored_on_the_corrected_table() -> None:
    table = tabled(world())
    key = ("t1", 1, AGC)
    fixed = substituted(table, {key: -math.inf})
    direct = world()
    score(direct, *key, -math.inf)
    assert all_same(read(fixed), read(tabled(direct)))
    assert read(fixed)["C1"].point == pytest.approx(-1 / 144, abs=1e-12)
    assert read(fixed)["C1"].n == 6
    assert read_floors(table, PASS, ROOT, 200)["C1"].floored == 0
    assert read_floors(fixed, PASS, ROOT, 200)["C1"].floored == 1


def test_a_row_that_rises_from_minus_inf_is_read_as_scored() -> None:
    rows = world()
    key = ("t1", 1, AGC)
    score(rows, *key, -math.inf)
    table = tabled(rows)
    fixed = substituted(table, {key: -0.625})
    assert all_same(read(fixed), read(tabled(world())))
    assert read(fixed)["C1"].point == 0.0
    assert read_floors(table, PASS, ROOT, 200)["C1"].floored == 1
    assert read_floors(fixed, PASS, ROOT, 200)["C1"].floored == 0


def test_a_substitution_below_the_floor_moves_the_floor() -> None:
    """A corrected row can lower the floor of a (truth, seed)."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    table = tabled(rows)
    fixed = substituted(table, {("t1", 1, B_RAND): -0.875})
    # Floor -0.875: gap -3/8, mean (-3/8 - 1/8 - 1/8) / 3 = -5/24, d = -1/12.
    assert read(fixed)["C1"].point == pytest.approx(-1 / 72, abs=1e-12)


def test_a_substitution_can_change_which_truths_are_kept() -> None:
    table = tabled(world())
    corrected: dict[RowKey, float] = {
        ("t2", seed, ORACLE): -0.75 for seed in SEEDS
    }  # below B-lib: control 1 drops t2 on the corrected table only
    got = read(substituted(table, corrected))["C1"]
    assert read(table)["C1"].n == 6 and got.n == 5
    assert all(key[0] != "t2" for key in got.rows)


def test_an_empty_substitution_rebuilds_the_same_reads_by_hex() -> None:
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    failed(rows, ("t2", 2, AGC), FIT_FAILURE)
    no_submission(rows, ("t3", 3, B_SYM))
    table = tabled(rows)
    assert all_same(read(table), read(substituted(table, {})))


# --------------------------------------------------------------------------
# The controls gate, in their fixed order
# --------------------------------------------------------------------------


def test_an_oracle_at_minus_inf_fails_control_1_on_every_read() -> None:
    rows = world()
    score(rows, "t1", 1, ORACLE, -math.inf)
    got = read(tabled(rows))
    assert all(not_reportable(r) for r in got.values())


def test_an_underfit_flag_fails_control_1_on_every_read() -> None:
    rows = world()
    key = ("t1", 1, ORACLE)
    rows[key] = replace(rows[key], oracle_underfit=True)
    assert all(not_reportable(r) for r in read(tabled(rows)).values())


def test_control_2_reads_the_unfloored_table() -> None:
    """Planted-hint at -inf on t1 ranks lowest, so the rank rule fails (p = 17/64).

    Floored first it would be at -0.75, level with B-rand: d = 0 on t1 and five
    wins, p = 1/32, a pass. So every read being unreportable shows the controls
    came first."""
    rows = world()
    for seed in SEEDS:
        score(rows, "t1", seed, PLANTED, -math.inf)
    assert all(not_reportable(r) for r in read(tabled(rows)).values())


def test_control_2_failing_gates_every_read() -> None:
    rows = world()
    score_all(rows, PLANTED, -0.75)
    assert all(not_reportable(r) for r in read(tabled(rows)).values())


def test_control_3_failing_gates_every_read() -> None:
    rows = world()
    score_all(rows, B_SYM_10F, -0.75)
    assert all(not_reportable(r) for r in read(tabled(rows)).values())


def test_control_3_reads_the_unfloored_table() -> None:
    """Four of six truths have B-sym@10F at -inf, so the median ranks lowest and
    control 3 fails. Every other contrast row on those (truth, seed)s sits at the
    ORACLE's -0.5, so a floor would lift them to gap 0 and control 3 would pass."""
    rows = world()
    for t in TRUTHS[:4]:
        for key in [k for k in rows if k[:2] == (t, 1) and _contrast(k)]:
            score(rows, *key, -0.5)
        score(rows, t, 1, B_SYM_10F, -math.inf)
    assert all(not_reportable(r) for r in read(tabled(rows)).values())


def test_control_4_gates_the_c2_reads_and_no_other() -> None:
    got = read(tabled(world()), FAIL_NAMING)
    assert all(not_reportable(got[name]) for name in C2_READS)
    assert all(got[name].reportable for name in OTHER_READS)


def test_a_truth_control_1_drops_leaves_every_read() -> None:
    rows = world()
    for seed in SEEDS:
        score(rows, "t3", seed, B_LIB, -0.25)  # ORACLE does not beat B-lib here
    got = read(tabled(rows))
    assert all(r.reportable for r in got.values())
    assert all(all(key[0] != "t3" for key in r.rows) for r in got.values())
    assert got["C1"].n == 5
    assert got["C4"].n == 2


def test_a_drop_can_leave_a_read_too_few_truths() -> None:
    rows = world()
    for seed in SEEDS:
        score(rows, "t2", seed, B_LIB, -0.25)
    got = read(tabled(rows))
    assert got["C1"].reportable and got["C1"].n == 5
    assert got["C4"].n == 1 and not_reportable(got["C4"])


def test_every_truth_dropped_leaves_seven_empty_reads_and_no_exception() -> None:
    rows = world()
    score_all(rows, B_LIB, -0.25)
    got = read(tabled(rows))
    assert list(got) == list(NAMES)
    assert all(not_reportable(r) and r.n == 0 and r.rows == () for r in got.values())


def test_a_truth_control_1_cannot_check_is_not_kept() -> None:
    """Fewer than two paired seeds: not shown identifiable, so not ``kept``."""
    rows = world()
    for seed in (1, 2):
        del rows[("t3", seed, B_LIB)]
    table = tabled(rows)
    got = read(table)["C1"]
    assert got.n == 5
    assert all(key[0] != "t3" for key in got.rows)
    assert controlled(table).unchecked == ("t3",)


def controlled(
    table: Table, controls: ScriptedControls = PASS
) -> contrasts.ControlsRead:
    return contrasts.read_controls(table, controls, ROOT, 200)


def _two_seeds() -> World:
    """P4's seeds: one lost B-lib row leaves a truth one paired seed."""
    return {k: v for k, v in world().items() if k[1] != 3}


def test_read_controls_holds_the_truths_of_a_table() -> None:
    """Four truths kept: four wins of four is p = 1/16, so control 2 fails here."""
    rows = world()
    for seed in SEEDS:
        score(rows, "t2", seed, B_LIB, -0.25)  # ORACLE does not beat B-lib here
    for seed in (1, 2):
        del rows[("t5", seed, B_LIB)]
    got = controlled(tabled(rows))
    assert got.kept == ("t1", "t3", "t4", "t6")
    assert got.dropped == ("t2",)
    assert got.unchecked == ("t5",)
    assert got.controls_pass is False
    assert got.naming_null is True


@pytest.mark.parametrize(
    "outcome", [_floored_by_inf, _floored_by_failed_fit], ids=["minus-inf", "failed"]
)
def test_on_two_seeds_one_lost_b_lib_row_leaves_a_truth_unchecked(
    outcome: object,
) -> None:
    """Finding 1: at ``-inf`` or failed, B-lib leaves one paired seed. Such a truth
    is neither checked nor dropped, and it leaves all seven reads, with the controls
    passing and nothing else on the table to show it."""
    assert callable(outcome)
    rows = _two_seeds()
    outcome(rows, ("t3", 2, B_LIB))
    table = tabled(rows)
    got = controlled(table)
    assert got.unchecked == ("t3",)
    assert got.kept == ("t1", "t2", "t4", "t5", "t6")
    assert got.dropped == ()
    assert got.controls_pass
    reads = read(table)
    assert reads["C1"].n == 5 and reads["C1"].reportable
    assert not any(k[0] == "t3" for r in reads.values() for k in r.rows)


def test_with_no_b_lib_row_every_truth_is_unchecked_and_nothing_raises() -> None:
    table = tabled({k: v for k, v in world().items() if k[2] != B_LIB})
    got = controlled(table)
    assert got.unchecked == TRUTHS
    assert got.kept == () and got.dropped == ()
    assert not got.controls_pass
    assert got.why_not("C1", read(table)["C1"].n) == (
        contrasts.CONTROLS_FAILED,
        contrasts.TOO_FEW_TRUTHS,
    )


def test_read_controls_is_verdicts_answer_on_control_2s_stream() -> None:
    rows = world()
    for seed in SEEDS:
        score(rows, "t2", seed, B_LIB, -0.25)
    table = tabled(rows)
    given = replace(
        table, rows=tuple(r for r in table.rows if r.system not in (B_NP, HAIKU))
    )
    rng = np.random.default_rng(np.random.SeedSequence([ROOT, CONTROL_2_STREAM]))
    direct = analysis.verdict(given, rng, resamples=200)
    got = controlled(table)
    assert got.kept == tuple(sorted(direct.kept))
    assert got.dropped == tuple(sorted(direct.dropped))
    assert got.controls_pass == direct.controls_pass


def test_read_controls_draws_no_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("read_controls drew a read's bootstrap")

    monkeypatch.setattr(contrasts, "paired_bootstrap_ci", refuse)
    assert controlled(tabled(world())).controls_pass


def test_the_unchecked_rule_is_one_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control room has the rule open (finding 1): a truth with fewer than 2
    paired seeds is not kept. It stays so here, and the constant is all that says
    so: turned over, the truth is read and still listed as unchecked."""
    assert contrasts.READ_UNCHECKED_TRUTHS is False
    rows = _two_seeds()
    score(rows, "t3", 2, B_LIB, -math.inf)
    table = tabled(rows)
    assert read(table)["C1"].n == 5
    monkeypatch.setattr(contrasts, "READ_UNCHECKED_TRUTHS", True)
    got = controlled(table)
    assert got.unchecked == ("t3",)
    assert got.kept == TRUTHS
    assert read(table)["C1"].n == 6


def test_why_not_is_empty_for_a_reportable_read() -> None:
    table = tabled(world())
    got = controlled(table)
    reads = read(table)
    assert all(got.why_not(name, reads[name].n) == () for name in NAMES)


def test_why_not_tells_a_failed_gate_from_too_few_truths() -> None:
    """The same ``nan`` read, for the three different reasons a report must give."""
    few = world()
    for key in [k for k in few if k[2] == B_SPARSE and k[0] == "t2"]:
        del few[key]
    table = tabled(few)
    assert not_reportable(read(table)["C4"])
    assert controlled(table).why_not("C4", 1) == (contrasts.TOO_FEW_TRUTHS,)

    bad = world()
    score_all(bad, PLANTED, -0.75)  # control 2 fails
    failing = tabled(bad)
    reads = read(failing)
    assert not_reportable(reads["C1"]) and reads["C1"].n == 6
    assert controlled(failing).why_not("C1", 6) == (contrasts.CONTROLS_FAILED,)

    table = tabled(world())
    naming = controlled(table, FAIL_NAMING)
    assert naming.naming_null is False and naming.controls_pass
    for name in C2_READS:
        assert naming.why_not(name, 6) == (contrasts.NAMING_NULL_FAILED,)
    for name in OTHER_READS:
        assert naming.why_not(name, 6) == ()


def test_why_not_gives_every_reason_that_holds() -> None:
    rows = world()
    score_all(rows, PLANTED, -0.75)
    got = controlled(tabled(rows), FAIL_NAMING)
    assert got.why_not("C2-AG-c-time", 0) == (
        contrasts.CONTROLS_FAILED,
        contrasts.NAMING_NULL_FAILED,
        contrasts.TOO_FEW_TRUTHS,
    )


def test_why_not_takes_only_a_read_that_exists() -> None:
    with pytest.raises(ScoringError, match="C5"):
        controlled(tabled(world())).why_not("C5", 6)


def _gate_cases() -> list[tuple[Table, ScriptedControls]]:
    """Tables a read's state turns on: each gate, a drop, a truth lost to S6."""
    bad = world()
    score_all(bad, PLANTED, -0.75)
    drop = world()
    for seed in SEEDS:
        score(drop, "t2", seed, B_LIB, -0.25)
    floored = world()
    for seed in SEEDS:
        score(floored, "t1", seed, AGC, -math.inf)
    return [
        (tabled(world()), PASS),
        (tabled(world()), FAIL_NAMING),
        (tabled(bad), PASS),
        (tabled(drop), PASS),
        (tabled(drop), FAIL_NAMING),
        (tabled(floored), PASS),
    ]


def test_why_not_and_reportable_never_disagree() -> None:
    """Over every read and every left-out read: one rule gives both. On the floored
    table C4's left-out read has one truth, and its gates pass."""
    tested = 0
    for table, controls in _gate_cases():
        got = controlled(table, controls)
        floors = read_floors(table, controls, ROOT, 200)
        reads = read(table, controls)
        for name in NAMES:
            for r in (reads[name], floors[name].without):
                assert r.reportable == (not got.why_not(name, r.n))
                tested += 1
    assert tested == 6 * len(NAMES) * 2
    last = _gate_cases()[-1][0]
    left_out = read_floors(last, PASS, ROOT, 200)["C4"].without
    assert left_out.n == 1
    assert controlled(last).why_not("C4", left_out.n) == (contrasts.TOO_FEW_TRUTHS,)


@pytest.mark.parametrize(
    "entry_point", ["read_contrasts", "read_floors", "read_controls"]
)
def test_the_controls_read_the_table_as_given_with_stream_1000(
    entry_point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Table, Mapping[str, object], int]] = []
    real = analysis.verdict

    def spy(
        table: Table, rng: np.random.Generator, *, resamples: int
    ) -> analysis.Verdict:
        state = rng.bit_generator.state
        seen.append((table, state, resamples))
        return real(table, rng, resamples=resamples)

    monkeypatch.setattr(analysis, "verdict", spy)
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    no_submission(rows, ("t1", 1, PLANTED))
    table = tabled(rows)
    getattr(contrasts, entry_point)(table, PASS, ROOT, 150)
    assert len(seen) == 1
    given, state, resamples = seen[0]
    assert resamples == 150
    assert CONTROL_2_STREAM == 1000
    fresh = np.random.default_rng(np.random.SeedSequence([ROOT, 1000]))
    assert state == fresh.bit_generator.state
    by = {(r.truth, r.seed, r.system): r for r in given.rows}
    assert by[("t1", 1, AGC)].per_event == -math.inf  # unfloored
    assert by[("t1", 1, PLANTED)].per_event is None
    assert not any(r.system in (B_NP, HAIKU) for r in given.rows)


def test_a_read_with_one_truth_is_not_reportable_but_says_what_it_had() -> None:
    rows = world()
    for key in [k for k in rows if k[2] == B_SPARSE and k[0] == "t2"]:
        del rows[key]
    got = read(tabled(rows))["C4"]
    assert not_reportable(got)
    assert got.n == 1
    assert {key[0] for key in got.rows} == {"t1"}


def test_a_read_with_no_rows_is_not_reportable_and_is_named() -> None:
    rows = {k: v for k, v in world().items() if k[2] not in (AGO, AGO_ANON)}
    got = read(tabled(rows))
    assert not_reportable(got["C3-free"])
    assert got["C3-free"].n == 0 and got["C3-free"].rows == ()
    assert got["C1"].reportable


def test_with_no_rescaled_rows_the_four_c2_reads_are_named_and_not_reportable() -> None:
    """A plan without the ``rescaled`` arms: its reads have no rows, so they are
    not reportable; the others do not notice."""
    rows = {
        k: v for k, v in world().items() if k[2] not in (AGC_RESCALED, AGO_RESCALED)
    }
    got = read(tabled(rows))
    assert list(got) == list(NAMES)
    for name in C2_READS:
        assert not_reportable(got[name]) and got[name].n == 0
    assert all(got[name].reportable for name in OTHER_READS)


# --------------------------------------------------------------------------
# Determinism and the generators
# --------------------------------------------------------------------------


def _spread_world() -> World:
    noise = np.random.default_rng(np.random.SeedSequence([ROOT, 88]))
    rows = world()
    for system in (*ARMS, B_SYM, B_SPARSE):
        for t in TRUTHS:
            for seed in SEEDS:
                score(rows, t, seed, system, -0.625 + float(noise.uniform(-0.1, 0.1)))
    return rows


def _expected_ci(
    rows: World, first: str, second: str, truth_ids: Sequence[str], stream: int
) -> tuple[float, float]:
    """A read's interval from its stream, by plain numpy (no ``stats``)."""
    d = np.array(
        [
            sum(
                _per_event(rows, t, seed, first) - _per_event(rows, t, seed, second)
                for seed in SEEDS
            )
            / len(SEEDS)
            for t in truth_ids
        ]
    )
    rng = np.random.default_rng(np.random.SeedSequence([ROOT, stream]))
    means = d[rng.integers(0, d.size, size=(300, d.size))].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def _per_event(rows: World, truth: str, seed: int, system: str) -> float:
    value = rows[(truth, seed, system)].per_event
    assert value is not None
    return value


def test_read_i_of_the_sorted_names_draws_from_stream_i() -> None:
    rows = _spread_world()
    got = read(tabled(rows), resamples=300)
    plan = {
        "C1": (AGC, B_SYM, TRUTHS),
        "C2-AG-c-names": (AGC_RESCALED, AGC_ANON, TRUTHS),
        "C2-AG-c-time": (AGC, AGC_RESCALED, TRUTHS),
        "C2-AG-o-names": (AGO_RESCALED, AGO_ANON, TRUTHS),
        "C2-AG-o-time": (AGO, AGO_RESCALED, TRUTHS),
        "C3-free": (AGO, AGC, TRUTHS),
        "C4": (AGC, B_SPARSE, OUT_OF_DICTIONARY),
    }
    for i, name in enumerate(NAMES):
        first, second, ids = plan[name]
        low, high = _expected_ci(rows, first, second, ids, i)
        assert got[name].low == pytest.approx(low, abs=1e-12), name
        assert got[name].high == pytest.approx(high, abs=1e-12), name


def test_one_reads_rows_do_not_move_anothers_interval() -> None:
    """Round 1, finding 10: a truth leaving one read must not shift another."""
    rows = _spread_world()
    before = read(tabled(rows))
    for seed in SEEDS:
        del rows[("t1", seed, B_SPARSE)]
    after = read(tabled(rows))
    assert after["C4"].n == 1
    assert all(same_read(before[n], after[n]) for n in NAMES if n != "C4")


def test_two_calls_are_byte_identical_and_row_order_does_not_matter() -> None:
    rows = _spread_world()
    score(rows, "t1", 1, AGC, -math.inf)
    table = tabled(rows)
    first = read(table)
    assert all_same(first, read(table))
    order = np.random.default_rng(np.random.SeedSequence([ROOT, 99])).permutation(
        len(table.rows)
    )
    shuffled = replace(table, rows=tuple(table.rows[int(i)] for i in order))
    assert all_same(first, read(shuffled))
    assert all(r.rows == tuple(sorted(r.rows)) for r in first.values())


def test_two_tables_with_equal_n_resample_the_same_truths() -> None:
    """Section 2.7: so a shift of every d by 1/8 shifts both ends by exactly 1/8."""
    rows = _spread_world()
    before = read(tabled(rows))["C1"]
    for key in [k for k in rows if k[2] == AGC]:
        value = rows[key].per_event
        assert value is not None
        score(rows, *key, value + 0.125)
    after = read(tabled(rows))["C1"]
    assert before.n == after.n
    assert after.low - before.low == pytest.approx(0.125, abs=1e-12)
    assert after.high - before.high == pytest.approx(0.125, abs=1e-12)


def test_a_different_root_gives_a_different_interval() -> None:
    table = tabled(_spread_world())
    assert read(table, seed=11)["C1"].low != read(table, seed=12)["C1"].low


def _hash_seed_table() -> Table:
    """A table with every path a set or a dict could reach: floors, a tie at a floor,
    both kinds of failure, a truth unchecked, and a void and a live session."""
    rows = _spread_world()
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t1", 1, B_SYM, -math.inf)
    score(rows, "t2", 1, B_SPARSE, -0.75)  # level with B-rand
    score(rows, "t2", 1, AGO, -math.inf)
    failed(rows, ("t3", 2, AGC), FIT_FAILURE)
    failed(rows, ("t4", 2, AGO), "the session timed out")
    no_submission(rows, ("t6", 3, B_SYM))
    for seed in (1, 2):
        del rows[("t5", seed, B_LIB)]
    void = Session("t3", 1, AGO, {}, {}, _void(), "env", "digest")
    live = Session("t4", 1, AGC, {}, {}, VoidStatus(False, (), 0, False), "e", "d")
    return tabled(rows, (void, live))


def _no_b_lib_table() -> Table:
    return tabled({k: v for k, v in world().items() if k[2] != B_LIB})


def _text(r: ContrastRead) -> str:
    floats = (r.point.hex(), r.low.hex(), r.high.hex())
    return f"{floats} {r.reportable} {r.n} {r.rows}"


def reads_text() -> str:
    """All three entry points on the table above, every float by ``float.hex``, and
    step 1 on a table with every truth unchecked: one unchecked truth has no order
    to lose, and the table above can spare only one and still pass control 2."""
    table = _hash_seed_table()
    gate = contrasts.read_controls(table, PASS, ROOT, 200)
    lines = [f"{gate}", f"{controlled(_no_b_lib_table())}"]
    lines += [f"{k} {_text(r)}" for k, r in read(table).items()]
    lines += [
        f"{k} {f.floored} {f.no_common_seed} {_text(f.without)}"
        for k, f in read_floors(table, PASS, ROOT, 200).items()
    ]
    return "\n".join(lines)


_HASH_SEED_CHILD: Final = """
import sys
from test_contrasts import reads_text
print(hash("sciagent"))
sys.stdout.write(reads_text())
"""


@pytest.mark.slow
def test_the_reads_are_identical_bytes_across_hash_seeds() -> None:
    """Hash randomisation is per process, so a repeat in this one cannot see a
    dependence on set order. Each child's first line is its ``hash``, the control
    that the seeds really differed."""
    here = str(Path(__file__).parent)
    path = os.pathsep.join(filter(None, (here, os.environ.get("PYTHONPATH"))))
    hashes: list[bytes] = []
    texts: list[bytes] = []
    for hash_seed in ("0", "1", "4242"):
        done = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_CHILD],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": path},
            capture_output=True,
            check=True,
            timeout=300,
        )
        first, text = done.stdout.split(b"\n", 1)
        hashes.append(first)
        texts.append(text)
    assert len(set(hashes)) == 3
    assert len(set(texts)) == 1
    assert texts[0] == reads_text().encode()


def test_the_hash_seed_table_reaches_what_it_says() -> None:
    """So that the test above compares real reads, not seven ``nan``s."""
    table = _hash_seed_table()
    gate = contrasts.read_controls(table, PASS, ROOT, 200)
    assert gate.unchecked == ("t5",) and gate.controls_pass
    reads = read(table)
    assert all(r.reportable for r in reads.values())
    floors = read_floors(table, PASS, ROOT, 200)
    # (t1, 1) on both sides, the failed fit on (t3, 2), no submission on (t6, 3).
    assert floors["C1"].floored == 4
    assert ("t2", 1, B_RAND) in reads["C3-free"].rows
    assert ("t2", 1, B_SPARSE) in reads["C3-free"].rows
    assert controlled(_no_b_lib_table()).unchecked == TRUTHS


def test_nan_compares_equal_by_hex_and_any_other_difference_does_not() -> None:
    nan = ContrastRead(math.nan, math.nan, math.nan, False, 0, ())
    assert same_read(nan, ContrastRead(math.nan, math.nan, math.nan, False, 0, ()))
    # Plain == is no use: it holds only while both share one nan object.
    computed = ContrastRead(float("nan"), float("nan"), float("nan"), False, 0, ())
    assert nan != computed and same_read(nan, computed)
    plain = ContrastRead(0.25, 0.125, 0.375, True, 2, (("t1", 1, AGC),))
    assert same_read(plain, replace(plain))
    for changed in (
        replace(plain, point=0.25000000000000006),
        replace(plain, low=0.0),
        replace(plain, high=math.nan),
        replace(plain, reportable=False),
        replace(plain, n=3),
        replace(plain, rows=()),
    ):
        assert not same_read(plain, changed)
    assert not same_read(replace(plain, point=0.0), replace(plain, point=-0.0))


# --------------------------------------------------------------------------
# What read_contrasts refuses (an exception is BYTE_FAIL, section 2.7)
# --------------------------------------------------------------------------


def test_a_duplicate_row_key_in_the_table_is_refused() -> None:
    """A sweep row under a main row's name would otherwise enter a read."""
    table = tabled(world())
    twin = next(r for r in table.rows if r.system == AGC)
    with pytest.raises(ScoringError, match="twice"):
        read(replace(table, rows=(*table.rows, twin)))


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_a_score_no_rule_covers_is_refused(value: float) -> None:
    rows = world()
    score(rows, "t1", 1, AGC, value)
    with pytest.raises(ScoringError, match="t1"):
        read(tabled(rows))


def test_a_scored_row_with_no_reading_is_refused() -> None:
    rows = world()
    key = ("t1", 1, AGC)
    rows[key] = replace(rows[key], per_event=None)
    with pytest.raises(ScoringError, match="t1"):
        read(tabled(rows))


def test_a_table_not_derived_from_its_own_scores_is_refused() -> None:
    table = tabled(world())
    stale = tuple(
        replace(r, per_event=-0.25) if r.system == AGC and r.truth == "t1" else r
        for r in table.rows
    )
    with pytest.raises(ScoringError, match="derive"):
        read(replace(table, rows=stale))


def test_a_stale_gap_or_gap_closed_alone_is_refused() -> None:
    table = tabled(world())
    row = next(r for r in table.rows if r.system == AGC)
    for changed in (replace(row, gap=-0.0625), replace(row, gap_closed=0.0625)):
        stale = tuple(changed if r is row else r for r in table.rows)
        with pytest.raises(ScoringError, match="derive"):
            read(replace(table, rows=stale))


def test_a_nan_in_a_curve_no_read_uses_is_not_a_stale_gap() -> None:
    """Finding 12: this table is derive's output, and ``nan`` != ``nan`` made the
    whole-row comparison call it stale, so a field no read uses gave BYTE_FAIL."""
    rows = world()
    key = ("t2", 1, AGC)
    rows[key] = replace(rows[key], curve=((1, math.nan),))
    assert all_same(read(tabled(rows)), read(tabled(world())))


def test_the_stale_check_reads_only_what_the_reads_read() -> None:
    table = tabled(world())
    other = tuple(
        replace(r, curve_gaps=((1, 0.5),), closed_flagged=True)
        if r.system == AGC
        else r
        for r in table.rows
    )
    assert all_same(read(replace(table, rows=other)), read(table))


def test_a_row_of_an_unknown_truth_is_refused() -> None:
    table = tabled(world())
    stranger = replace(table.rows[0], truth="t9")
    with pytest.raises(ScoringError, match="t9"):
        read(replace(table, rows=(*table.rows, stranger)))


# --------------------------------------------------------------------------
# S6: the rows left out
# --------------------------------------------------------------------------


def test_s6_counts_a_reads_floored_rows_and_reads_it_with_them_absent() -> None:
    """AG-c-S is 1/8 above B-sym on t1, and at -inf on seed 1.

    Floored: gaps -1/4, 0, 0, mean -1/12, against -1/8: d = 1/24, point 1/144.
    Absent: seeds 2 and 3 only: d = 1/8, point 1/48. The other truths are 0."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t1", 2, AGC, -0.5)
    score(rows, "t1", 3, AGC, -0.5)
    score(rows, "t5", 1, AGO, -math.inf)  # not on C1's sides
    table = tabled(rows)
    got = read_floors(table, PASS, ROOT, 200)
    assert list(got) == list(NAMES)
    assert got["C1"].floored == 1
    assert read(table)["C1"].point == pytest.approx(1 / 144, abs=1e-12)
    without = got["C1"].without
    assert without.point == pytest.approx(1 / 48, abs=1e-12)
    assert without.n == 6
    assert ("t1", 1, AGC) not in without.rows
    assert ("t1", 1, B_SYM) not in without.rows
    assert got["C3-free"].floored == 2


def test_s6_leaves_out_a_floored_second_side() -> None:
    """B-sym, C1's second side, at -inf on (t1, 1). With that pair left out every
    remaining d is 0."""
    rows = world()
    score(rows, "t1", 1, B_SYM, -math.inf)
    got = read_floors(tabled(rows), PASS, ROOT, 200)["C1"]
    assert got.floored == 1
    assert ("t1", 1, B_SYM) not in got.without.rows
    assert ("t1", 1, AGC) not in got.without.rows
    assert got.without.point == 0.0


def test_s6_counts_floored_sides_and_not_the_rows_that_set_floors() -> None:
    """Both sides floored, set by one row: 2, and the left-out read has no setter.

    The setter here is itself a row that takes a floor: submitted is False and a
    reading is there, which the rule leaves finite for the floor's search. On C1 it
    is no side, so it is not C1's floored row, though it is in C1's ``rows``."""
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    score(rows, "t1", 1, B_SYM, -math.inf)
    key = ("t1", 1, B_RAND)
    rows[key] = replace(rows[key], submitted=False)
    table = tabled(rows)
    got = read_floors(table, PASS, ROOT, 200)["C1"]
    assert key in read(table)["C1"].rows
    assert got.floored == 2
    assert key not in got.without.rows


def test_s6_with_nothing_floored_is_the_read_itself() -> None:
    table = tabled(_spread_world())
    got = read_floors(table, PASS, ROOT, 200)
    reads = read(table)
    for name in NAMES:
        assert got[name].floored == 0
        assert same_read(got[name].without, reads[name])


def test_s6_a_truth_only_its_floored_rows_held_leaves_the_left_out_read() -> None:
    rows = world()
    for seed in SEEDS:
        score(rows, "t1", seed, AGC, -math.inf)
    table = tabled(rows)
    got = read_floors(table, PASS, ROOT, 200)["C1"]
    assert got.floored == 3
    assert got.without.n == 5
    assert read(table)["C1"].n == 6


def test_s6_is_gated_as_its_read_is() -> None:
    got = read_floors(tabled(world()), FAIL_NAMING, ROOT, 200)
    assert all(not_reportable(got[name].without) for name in C2_READS)
    assert all(got[name].without.reportable for name in OTHER_READS)


# --------------------------------------------------------------------------
# contrast_rows
# --------------------------------------------------------------------------


def _entries(n: int) -> list[TruthEntry]:
    return [toy_truth(f"test-{k:03d}") for k in range(n)]


def _sonnet(*, rescaled: bool) -> CampaignConfig:
    return CampaignConfig(
        seeds=(1, 2),
        control_seeds=(1,),
        baselines=BASELINES,
        llm_arms=P4_SONNET_ARMS if rescaled else P4_ARMS,
    )


def _p4_shaped(*, rescaled: bool) -> tuple[CampaignConfig, ...]:
    """A stand-in with P4-PROPOSAL §4's blocks, the sweep's budget in the name
    through the letter and on every truth. The real plan is ``p4plan.p4_plan``,
    tested in ``test_p4_plan.py``."""
    named = tuple(a for a in P4_ARMS if a.condition == "named")
    models = tuple(
        CampaignConfig(seeds=(1, 2), baselines=(), llm_arms=P4_ARMS, llm_letter=letter)
        for letter in ("H", "O")
    )
    sweep = tuple(
        CampaignConfig(
            seeds=(1,),
            control_seeds=(1,),
            baselines=(),
            llm_arms=named,
            llm_letter=f"S@E={experiments},F={fits}",
            experiments=experiments,
            fits=fits,
        )
        for experiments, fits in ((0, 40), (2, 40), (16, 40), (8, 20), (8, 80))
    )
    return (_sonnet(rescaled=rescaled), *models, *sweep)


def test_contrast_rows_on_a_p4_shaped_plan_are_section_7s_thousand() -> None:
    """40 truths, 2 seeds: six Sonnet arms 480, six baselines 480, control 3 on
    seed 1 40, so 1,000. B-np, Haiku, Opus and the sweep are not contrast rows."""
    keys = contrast_rows(_p4_shaped(rescaled=True), _entries(40))
    assert len(keys) == 1000
    assert len(set(keys)) == 1000
    assert keys == tuple(sorted(keys))
    systems = sorted({k[2] for k in keys})
    assert systems == sorted(
        (*ARMS, ORACLE, B_LIB, B_RAND, B_SYM, B_SPARSE, PLANTED, B_SYM_10F)
    )
    assert sum(k[2] == B_SYM_10F for k in keys) == 40
    assert {k[1] for k in keys if k[2] == B_SYM_10F} == {1}


def test_a_plan_without_the_rescaled_arms_is_160_rows_short() -> None:
    """Section 7's table needs them: the Sonnet config must carry six arms."""
    keys = contrast_rows(_p4_shaped(rescaled=False), _entries(40))
    assert len(keys) == 840
    assert not any("rescaled" in k[2] for k in keys)


def test_row_keys_are_unique_over_a_p4_shaped_plan_or_refused() -> None:
    """The key has no budget field, so the name must carry it. A sweep arm under
    the main arm's name is the collision the text warns of: refused."""
    plan = _p4_shaped(rescaled=False)
    ids = [t.id for t in _entries(20)]
    every = [
        (unit.truth_id, unit.seed, unit.system)
        for config in plan
        for unit in (*baseline_units(ids, config), *llm_units(ids, config))
    ]
    assert len(every) > 420 and len(set(every)) == len(every)
    assert len(contrast_rows(plan, _entries(20))) == 420  # 840 on 40 truths
    named = tuple(a for a in P4_ARMS if a.condition == "named")
    collide = CampaignConfig(
        seeds=(1,), baselines=(), llm_arms=named, experiments=0
    )  # same letter, so the same names, at another budget
    with pytest.raises(CampaignError, match="budget"):
        contrast_rows((*plan, collide), _entries(20))
    twice = CampaignConfig(seeds=(1,), baselines=(B_SYM,), llm_arms=(), fits=80)
    with pytest.raises(CampaignError, match="budget"):
        contrast_rows((*plan, twice), _entries(20))


def test_a_key_twice_outside_the_contrast_systems_is_refused() -> None:
    """Section 2.7 says unique over the plan, so a collision is refused even where
    no row of it enters a read: an LLM arm of another model, and a baseline."""
    haiku = CampaignConfig(seeds=(1,), baselines=(), llm_arms=P4_ARMS, llm_letter="H")
    assert HAIKU not in contrasts.CONTRAST_SYSTEMS
    assert len(contrast_rows((haiku,), _entries(2))) == 0
    with pytest.raises(CampaignError, match=f"{re.escape(HAIKU)}.*budget"):
        contrast_rows((haiku, replace(haiku, experiments=0)), _entries(2))
    lone = CampaignConfig(seeds=(1,), baselines=(B_NP,), llm_arms=())
    assert B_NP not in contrasts.CONTRAST_SYSTEMS
    assert len(contrast_rows((lone,), _entries(2))) == 0
    with pytest.raises(CampaignError, match=f"{B_NP}.*budget"):
        contrast_rows((lone, replace(lone, fits=80)), _entries(2))


def test_a_duplicated_truth_is_refused() -> None:
    plan = (_sonnet(rescaled=False),)
    with pytest.raises(CampaignError, match="budget"):
        contrast_rows(plan, [*_entries(2), toy_truth("test-000")])


class _OnlyAnId:
    """A truth that fails on any read but its id: a score, a split, a record."""

    def __init__(self, truth_id: str) -> None:
        self.id = truth_id

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"contrast_rows read {name!r} of a truth")


def test_contrast_rows_reads_no_cell_and_no_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("contrast_rows opened the store")

    for name in ("cell", "cells", "done", "__init__"):
        monkeypatch.setattr(ResultStore, name, refuse)
    assert list(inspect.signature(contrast_rows).parameters) == ["plan", "truths"]
    ids = cast("list[TruthEntry]", [_OnlyAnId(f"test-{k:03d}") for k in range(4)])
    keys = contrast_rows(_p4_shaped(rescaled=True), ids)
    assert len(keys) == 100
    names = set(contrast_rows.__code__.co_names)
    assert not names & {"per_event", "gap", "reading", "cell", "cells", "rows", "done"}


def test_contrast_rows_take_every_truth_before_any_drop() -> None:
    """Tier 1 reads no score, so no drop can reach it: every truth is in it."""
    plan = (
        CampaignConfig(
            seeds=SEEDS,
            control_seeds=(1,),
            llm_arms=P4_SONNET_ARMS,
        ),
    )
    keys = set(contrast_rows(plan, list(truths().values())))
    assert {k[0] for k in keys} == set(TRUTHS)
    assert ("t2", 1, AGC) in keys


def test_every_reads_rows_are_contrast_rows_on_both_tables() -> None:
    plan = (
        CampaignConfig(
            seeds=SEEDS,
            control_seeds=(1,),
            llm_arms=P4_SONNET_ARMS,
        ),
    )
    allowed = set(contrast_rows(plan, list(truths().values())))
    rows = world()
    score(rows, "t1", 1, AGC, -math.inf)
    table = tabled(rows)
    fixed = substituted(table, {("t1", 1, AGC): -0.625, ("t2", 1, B_SYM): -math.inf})
    for reads in (read(table), read(fixed)):
        assert all(set(r.rows) <= allowed for r in reads.values())
        assert all(r.rows for r in reads.values())


def test_a_reads_rows_are_its_sides_and_its_gap_reference() -> None:
    rows = world()
    got = read(tabled(rows))["C1"]
    expected = sorted(
        (t, seed, system)
        for t in TRUTHS
        for seed in SEEDS
        for system in (AGC, B_SYM, ORACLE)
    )
    assert list(got.rows) == expected


def test_the_system_names_are_the_arms_own() -> None:
    """A renamed arm would otherwise leave its read empty without a word."""
    names = {arm.system("S") for arm in P4_SONNET_ARMS}
    assert names == {AGC, AGC_RESCALED, AGC_ANON, AGO, AGO_RESCALED, AGO_ANON}
    assert names <= contrasts.CONTRAST_SYSTEMS


# --------------------------------------------------------------------------
# A failed cell keeps its cause (the pair rule splits ``failed`` by cause)
# --------------------------------------------------------------------------


def _cell(error: Exception) -> dict[str, object]:
    out = execute._failure(AGC, error, 0.0, None)
    return {
        "reading": {k: float(v).hex() for k, v in out.reading.items()},
        "detail": {"result": out.detail},
    }


def test_the_floors_failure_text_is_what_the_score_job_raises() -> None:
    source = inspect.getsource(execute)
    assert source.count(f'f"{FAILED_FIT}: {{key}}"') == 2


def test_a_stored_failed_fit_row_keeps_its_cause_and_is_floored() -> None:
    row = row_from_cell("t1", 1, AGC, True, _cell(CampaignError(FIT_FAILURE)))
    assert not row.ok and row.error == "CampaignError"
    assert row.message == FIT_FAILURE
    rows = world()
    rows[("t1", 1, AGC)] = row
    assert read(tabled(rows))["C1"].point == pytest.approx(-1 / 144, abs=1e-12)


def test_a_stored_failure_of_another_cause_is_absent() -> None:
    cause = CampaignError("the session's record does not parse")
    row = row_from_cell("t1", 1, AGC, True, _cell(cause))
    assert row.error == "CampaignError" and row.message == str(cause)
    rows = world()
    rows[("t1", 1, AGC)] = row
    got = read(tabled(rows))["C1"]
    assert got.point == 0.0
    assert ("t1", 1, AGC) not in got.rows
