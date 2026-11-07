"""Controls 1 to 3 after the control room's ruling of 2026-11-07 (INBOX, "controls 2
and 3 scope"; red team round 2, finding 1). An instrument (SPEC §6.3), so these
tests came first.

(1) Controls 2 and 3 read every truth control 1 does not drop, kept and unchecked
alike. (2) A scored ``-inf`` B-lib reading counts as ORACLE beating B-lib for
control 1. (3) A failed or NaN B-lib unit leaves the truth unchecked for the reads,
and it counts in R2's scope as before. Stand-in rows only, toy root 11.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest
from test_contrasts import _row, toy_truth

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.contrasts import ScriptedControls, read_controls
from sciagent.campaign.secondaries import read_scope
from sciagent.scoring.errors import ScoringError

ROOT = 11
RESAMPLES = 400
ORACLE = "ORACLE"
B_LIB = "B-lib"
B_RAND = "B-rand"
PLANTED = "planted-hint"
B_SYM_10F = "B-sym@10F"

type Change = Callable[[Row], Row]


def crashed(r: Row) -> Row:
    return replace(
        r,
        ok=False,
        submitted=False,
        per_event=None,
        error="CampaignError",
        message="boom",
    )


def at(value: float) -> Change:
    return lambda r: replace(r, per_event=value)


def unchanged(r: Row) -> Row:
    return r


def read_verdict(table: Table) -> analysis.Verdict:
    return analysis.verdict(
        table,
        np.random.default_rng(np.random.SeedSequence([ROOT, 1000])),
        resamples=RESAMPLES,
    )


# -- the red team's case (/tmp/rt-s38b/p1_blib_crash.py) -----------------------

IDS = tuple(f"rt-{k:03d}" for k in range(40))
HOSTILE = IDS[30:]


def hostile_table(changes: dict[tuple[str, int, str], Change]) -> Table:
    """Planted-hint 0.01 above B-rand on 30 truths and 0.05 below on 10."""
    rows: list[Row] = []
    for t in IDS:
        d = -0.05 if t in HOSTILE else 0.01
        for s in (1, 2):
            for system, v in (
                (ORACLE, -0.5),
                (B_LIB, -0.6875),
                (B_RAND, -0.75),
                (PLANTED, -0.75 + d),
            ):
                r = _row(t, s, system, v)
                rows.append(changes.get((t, s, system), unchanged)(r))
            if s == 1:
                rows.append(_row(t, 1, B_SYM_10F, -0.5))
    truths = {t: toy_truth(t, in_dictionary=k % 2 == 0) for k, t in enumerate(IDS)}
    return Table(truths, tuple(derive(rows)), (), {})


def on_blib(change: Change) -> dict[tuple[str, int, str], Change]:
    return {(t, 1, B_LIB): change for t in HOSTILE[:4]}


def test_the_hostile_table_fails_control_2_with_nothing_changed() -> None:
    v = read_verdict(hostile_table({}))
    assert not v.controls_pass
    assert v.planted is not None and v.planted.n == 40


@pytest.mark.parametrize("change", [crashed, at(math.nan)], ids=["crashed", "nan"])
def test_a_failed_or_nan_blib_leaves_the_truth_unchecked_and_read(
    change: Change,
) -> None:
    """Ruling parts (1) and (3): the truth leaves control 1's check, stays in
    controls 2 and 3, and counts in R2's scope; control 2 still fails."""
    table = hostile_table(on_blib(change))
    v = read_verdict(table)
    assert not v.controls_pass
    assert v.planted is not None and not v.planted.passed
    assert v.planted.n == 40
    assert set(HOSTILE[:4]) <= set(v.planted.truths)
    assert set(HOSTILE[:4]).isdisjoint(v.kept)
    assert set(HOSTILE[:4]).isdisjoint(v.dropped)
    # B-sym@10F is at ORACLE on them, which would only help control 3: left out.
    assert set(HOSTILE[:4]).isdisjoint(v.bsym_truths)
    assert not v.unchecked_out_2 and v.unchecked_out_3 == HOSTILE[:4]


def test_a_failed_blib_counts_in_the_scope_of_the_reads() -> None:
    table = hostile_table(on_blib(crashed))
    c = read_controls(table, ScriptedControls(naming_null=True), ROOT, RESAMPLES)
    assert not c.controls_pass
    assert set(c.unchecked) == set(HOSTILE[:4])
    scope = read_scope(c, table.truths)
    assert (scope.left, scope.total) == (4, 40)


def test_the_reads_refuse_a_nan_blib_as_before() -> None:
    """The contrasts refuse a NaN reading of any contrast row (an instrument fault,
    ``BYTE_FAIL``); the ruling does not move that."""
    table = hostile_table(on_blib(at(math.nan)))
    with pytest.raises(ScoringError, match="per_event nan"):
        read_controls(table, ScriptedControls(naming_null=True), ROOT, RESAMPLES)


def test_a_scored_minus_inf_blib_counts_as_oracle_beating_it() -> None:
    """Ruling part (2): the truth is checked and kept, so it is read and does not
    leave R2's scope; control 2 still fails on the hostile table."""
    table = hostile_table(on_blib(at(-math.inf)))
    v = read_verdict(table)
    assert not v.controls_pass
    assert v.planted is not None and v.planted.n == 40
    assert set(HOSTILE[:4]) <= set(v.kept)
    checks = {c.truth: c for c in v.oracle_checks}
    for t in HOSTILE[:4]:
        assert checks[t].passed and checks[t].margin == math.inf
    c = read_controls(table, ScriptedControls(naming_null=True), ROOT, RESAMPLES)
    assert not c.controls_pass and not c.unchecked
    assert read_scope(c, table.truths).left == 0


# -- small tables, one property each --------------------------------------------

SMALL = tuple(f"s{k}" for k in range(6))


def small_table(
    values: dict[str, dict[str, float]],
    changes: dict[tuple[str, int, str], Change] | None = None,
    *,
    shift: float = 0.001,
) -> Table:
    """``values[truth][system]`` is the seed-1 reading; seed 2 is ``shift`` lower,
    so control 1's paired SE is about 0 and its margin decides. B-sym@10F runs on
    seed 1."""
    changes = changes or {}
    rows: list[Row] = []
    for t, by_system in values.items():
        for s in (1, 2):
            for system, v in by_system.items():
                if system == B_SYM_10F and s != 1:
                    continue
                r = _row(t, s, system, v - shift * (s - 1))
                rows.append(changes.get((t, s, system), unchanged)(r))
    truths = {t: toy_truth(t) for t in values}
    return Table(truths, tuple(derive(rows)), (), {})


def passing(planted: float = 0.01, bsym: float = -0.001) -> dict[str, float]:
    """One truth's readings with every control passing on it."""
    return {
        ORACLE: -0.5,
        B_LIB: -0.6,
        B_RAND: -0.7,
        PLANTED: -0.7 + planted,
        B_SYM_10F: -0.5 + bsym,
    }


def test_a_minus_inf_blib_seed_keeps_a_truth_its_finite_seed_would_drop() -> None:
    """Ruling part (2): ORACLE is below B-lib on seed 2, and B-lib scored ``-inf``
    on seed 1. The truth is kept, at an infinite margin."""
    values = {t: passing() for t in SMALL}
    values["s0"] = {**passing(), B_LIB: -0.4}
    plain = read_verdict(small_table(values))
    assert "s0" in plain.dropped
    v = read_verdict(small_table(values, {("s0", 1, B_LIB): at(-math.inf)}))
    assert "s0" in v.kept
    check = next(c for c in v.oracle_checks if c.truth == "s0")
    assert check.passed and check.margin == math.inf


def test_controls_2_and_3_read_an_unchecked_truth_against_them_and_no_dropped_one() -> (
    None
):
    """Ruling part (1), as the lead applies the general rule: an unchecked truth on
    which planted-hint does not beat B-rand, and B-sym@10F is below the tolerance,
    enters both controls; a dropped truth enters neither."""
    values = {t: passing() for t in SMALL}
    values["s0"] = {**passing(), B_LIB: -0.4}  # dropped
    values["s1"] = passing(planted=-0.01, bsym=-0.05)  # against both controls
    v = read_verdict(small_table(values, {("s1", 1, B_LIB): crashed}))
    assert v.dropped == ("s0",)
    assert "s1" not in v.kept
    assert v.planted is not None
    assert v.planted.truths == v.bsym_truths == tuple(t for t in SMALL if t != "s0")
    assert v.unchecked_out_2 == v.unchecked_out_3 == ()


def test_control_3_reads_an_unchecked_truth() -> None:
    """Three of six B-sym@10F truths far from ORACLE fail control 3. A B-lib crash on
    one of them leaves it unchecked; it must still be read, so control 3 still fails
    (before the ruling the median moved up over five truths)."""
    values = {t: passing(bsym=-0.05 if t in SMALL[:3] else -0.001) for t in SMALL}
    base = read_verdict(small_table(values))
    assert base.bsym is not None and not base.bsym.passed
    v = read_verdict(small_table(values, {("s0", 1, B_LIB): crashed}))
    assert "s0" in v.bsym_truths
    assert v.bsym is not None and not v.bsym.passed
    assert v.bsym.median_gap == base.bsym.median_gap


# -- an unchecked truth enters only against the control (the lead's application of
# -- the general rule, INBOX 2026-11-07, last entry) -----------------------------


def would_drop(by_system: dict[str, float]) -> dict[str, float]:
    """ORACLE below B-lib: control 1 drops the truth unless its B-lib is lost."""
    return {**by_system, B_LIB: -0.4}


def test_control_2_an_unchecked_win_cannot_pass_it() -> None:
    """Four kept wins of four is p = 1/16: control 2 fails. A fifth truth, which
    control 1 would drop, is a win; its B-lib crashed, so it is unchecked. Read, it
    would make five of five, p = 1/32, and pass the control."""
    values = {t: passing() for t in SMALL[:4]}
    values["s4"] = would_drop(passing())
    plain = read_verdict(small_table(values))
    assert plain.dropped == ("s4",)
    assert plain.planted is not None and not plain.planted.passed
    v = read_verdict(small_table(values, {("s4", 1, B_LIB): crashed}))
    assert "s4" not in v.kept and "s4" not in v.dropped
    assert v.planted is not None and not v.planted.passed
    assert "s4" not in v.planted.truths and v.unchecked_out_2 == ("s4",)
    assert not v.controls_pass


def test_control_3_an_unchecked_truth_near_oracle_cannot_pass_it() -> None:
    """Six kept truths, three far from ORACLE: the median is between the third and
    fourth, below the tolerance. A seventh truth, which control 1 would drop, is
    near ORACLE; its B-lib crashed. Read, it would lift the median to the fourth."""
    values = {t: passing(bsym=-0.05 if t in SMALL[:3] else -0.001) for t in SMALL}
    values["s6"] = would_drop(passing())
    plain = read_verdict(small_table(values))
    assert plain.dropped == ("s6",)
    assert plain.bsym is not None and not plain.bsym.passed
    v = read_verdict(small_table(values, {("s6", 1, B_LIB): crashed}))
    assert v.bsym is not None and not v.bsym.passed
    assert "s6" not in v.bsym_truths and v.unchecked_out_3 == ("s6",)
    assert not v.controls_pass


@pytest.mark.parametrize(
    ("system", "control"), [(PLANTED, "2"), (B_RAND, "2"), (B_SYM_10F, "3")]
)
def test_a_planned_unit_not_run_leaves_its_control_unread(
    system: str, control: str
) -> None:
    """The general rule for a unit not run (``Table.missing``): whichever truth it
    belonged to, its control cannot pass, even with every row present passing."""
    table = replace(small_table({t: passing() for t in SMALL}), missing={system: 1})
    v = read_verdict(table)
    assert not v.controls_pass
    assert (v.planted if control == "2" else v.bsym) is None
    assert any("not run" in m for m in v.inconclusive)


def test_known_limit_a_small_unchecked_loss_can_pass_control_2() -> None:
    """The exact signed-rank p is not monotone in adding a loss: a loss of the
    smallest size takes rank 1 and moves every win up a rank. Nine kept truths, one
    loss of 9 units and eight wins of 1 unit: p = 0.0742, control 2 fails. A tenth
    truth, which control 1 would drop, is a loss of half a unit; its B-lib crashed,
    so it is unchecked, and it enters as a loss. Then p = 0.0469 and the median's CI
    is above 0: control 2 passes. Stated in ``analysis``'s docstring and the report;
    this test shows the case exists, so a change to the rule is seen here."""
    unit = 2.0**-10
    names = tuple(f"m{k}" for k in range(10))
    values = {
        t: passing(planted=-9 * unit if k == 0 else unit) for k, t in enumerate(names)
    }
    values["m9"] = would_drop(passing(planted=-unit / 2))
    plain = read_verdict(small_table(values, shift=0.0))
    assert plain.dropped == ("m9",)
    assert plain.planted is not None and not plain.planted.passed
    assert plain.planted.p_value == pytest.approx(0.0742, abs=1e-4)
    v = read_verdict(small_table(values, {("m9", 1, B_LIB): crashed}, shift=0.0))
    assert v.planted is not None and "m9" in v.planted.truths
    assert v.planted.p_value == pytest.approx(0.0469, abs=1e-4)
    assert v.planted.passed and v.controls_pass


# -- the general rule's sweep: a crash or a missing reading never passes a control
# -- that fails (INBOX 2026-11-07, last entry) ------------------------------------

#: The systems controls 1 to 3 read: control 1 ORACLE and B-lib, control 2
#: planted-hint and B-rand, control 3 B-sym@10F, each against ORACLE's gap.
SWEPT = (ORACLE, B_LIB, PLANTED, B_RAND, B_SYM_10F)


def unsubmitted(r: Row) -> Row:
    return replace(r, submitted=False, per_event=None, has_structure=False)


def absent(r: Row) -> None:
    return None


#: One unit's crash or missing reading; None removes the row (not run).
MODES: dict[str, Callable[[Row], Row | None]] = {
    "crashed": crashed,
    "unsubmitted": unsubmitted,
    "nan": at(math.nan),
    "not_run": absent,
}


def c1_fails(v: analysis.Verdict) -> bool:
    return (
        v.underfit is None or not v.underfit.passed or bool(v.oracle_inf) or not v.kept
    )


def c2_fails(v: analysis.Verdict) -> bool:
    return v.planted is None or not v.planted.passed


def c3_fails(v: analysis.Verdict) -> bool:
    return v.bsym is None or not v.bsym.passed


FAILS: dict[str, Callable[[analysis.Verdict], bool]] = {
    "1": c1_fails,
    "2": c2_fails,
    "3": c3_fails,
}


def flag(r: Row) -> Row:
    return replace(r, oracle_underfit=True)


EIGHT = tuple(f"h{k}" for k in range(8))

#: Stand-in tables on which one control fails, by (control, name).
BASES: dict[tuple[str, str], Callable[[], Table]] = {
    ("1", "every truth dropped"): lambda: small_table(
        {t: would_drop(passing()) for t in SMALL}
    ),
    ("1", "an underfit flag"): lambda: small_table(
        {t: passing() for t in SMALL}, {("s0", 1, ORACLE): flag}
    ),
    ("1", "an ORACLE at -inf"): lambda: small_table(
        {t: passing() for t in SMALL}, {("s0", 1, ORACLE): at(-math.inf)}
    ),
    ("2", "four wins and a dropped win"): lambda: small_table(
        {**{t: passing() for t in SMALL[:4]}, "s4": would_drop(passing())}
    ),
    ("2", "two large losses"): lambda: small_table(
        {t: passing(planted=-0.05 if t in EIGHT[:2] else 0.01) for t in EIGHT}
    ),
    ("3", "three far and a dropped near"): lambda: small_table(
        {
            **{t: passing(bsym=-0.05 if t in SMALL[:3] else -0.001) for t in SMALL},
            "s6": would_drop(passing()),
        }
    ),
    ("3", "four far of six"): lambda: small_table(
        {t: passing(bsym=-0.05 if t in SMALL[:4] else -0.001) for t in SMALL}
    ),
}


def changed_unit(
    table: Table, key: tuple[str, int, str], mode: Callable[[Row], Row | None]
) -> Table:
    """``table`` with the unit ``key`` changed by ``mode``. A removed row is a
    planned unit not run, which ``load_rows`` counts in ``Table.missing``."""
    rows: list[Row] = []
    missing = dict(table.missing)
    for r in table.rows:
        new = mode(r) if (r.truth, r.seed, r.system) == key else r
        if new is None:
            missing[r.system] = missing.get(r.system, 0) + 1
        else:
            rows.append(new)
    return replace(table, rows=tuple(derive(rows)), missing=missing)


def test_each_base_table_fails_its_control_and_no_other() -> None:
    for (control, name), build in BASES.items():
        v = read_verdict(build())
        assert FAILS[control](v), name
        assert not v.controls_pass, name
        if control != "1":
            others = [c for c in FAILS if c != control]
            assert not any(FAILS[c](v) for c in others), name


@pytest.mark.parametrize("base", sorted(BASES), ids=lambda b: f"c{b[0]}-{b[1]}")
def test_no_crash_or_missing_reading_passes_a_failing_control(
    base: tuple[str, str],
) -> None:
    """For each system the controls read, one unit at a time: crashed, unsubmitted,
    NaN or not run. In ``analysis.verdict`` the gate stays closed and the failing
    control still fails, and ``contrasts.read_controls`` does not pass, or either
    refuses the table (an instrument fault fails closed).

    One stated limit: an ORACLE unit with no reading fails control 1, which closes
    the gate, and controls 2 and 3 leave out the rows it leaves without a gap, so
    one of them alone may then pass. For an ORACLE unit the test asserts control 1
    fails, not the base table's own control."""
    control, _ = base
    table = BASES[base]()
    keys = sorted((r.truth, r.seed, r.system) for r in table.rows if r.system in SWEPT)
    passed: list[tuple[tuple[str, int, str], str, str]] = []
    for key in keys:
        for mode_name, mode in MODES.items():
            t = changed_unit(table, key, mode)
            fails = c1_fails if key[2] == ORACLE else FAILS[control]
            try:
                v = read_verdict(t)
                if v.controls_pass or not fails(v):
                    passed.append((key, mode_name, "verdict"))
            except ScoringError:
                pass
            try:
                got = read_controls(
                    t, ScriptedControls(naming_null=True), ROOT, RESAMPLES
                )
                if got.controls_pass:
                    passed.append((key, mode_name, "read_controls"))
            except ScoringError:
                pass
    assert not passed, passed
