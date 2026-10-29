"""Tests of controls.py and ctl_gate.py (criteria.md v8 §7 "Stub tests").

Run with the worktree's interpreter, from outside $R (no cache, no bytecode):

    cd /tmp && env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \\
      PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=$(mktemp -d) nice -n 10 \\
      uv run --project $WT --no-sync python -m pytest -p no:cacheprovider \\
      -c /dev/null -o markers=slow --rootdir $D $D/test_controls.py [-m "not slow"]

Every test calls internal functions; none uses the CLI's gate on the real
repository. The gate, D-push and end-to-end tests use scratch repositories with
a local bare origin under the pytest tmp directory, and an injected gh reader:
nothing here fetches from or pushes to GitHub, or calls gh.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import controls as c
import ctl_gate as g
import pytest

HERE = Path(__file__).resolve().parent
N_D = 3
LABELS = ["mean_rate", "x[sign]", "y[size]"]


# --------------------------------------------------------------------------
# Stub runs and menus
# --------------------------------------------------------------------------


def stub_diagnostics(n: int = N_D) -> tuple[Any, ...]:
    from sciagent.systems.v2.policies import DiagnosticChoice

    return tuple(DiagnosticChoice("mean_rate") for _ in range(n))


DESIGNS = c.designs()
DIAGS = stub_diagnostics()


def mk(
    outcome: str = "complete", vals: Sequence[float] = (1.0, 1.0, 1.0), r: int = 0
) -> dict[str, Any]:
    """A stub run; complete and truncated runs carry jittered values."""
    if outcome == "stopped":
        return c.outcome_record("stopped", None, None, None)
    if outcome == "cut0":
        return c.outcome_record("cut0", 0.0, 3, [None] * len(vals))
    jitter = 0.01 * ((r * 7) % 5)
    values: list[float | None] = [v + jitter * (i + 1) for i, v in enumerate(vals)]
    tau = None if outcome == "complete" else 5.0
    return c.outcome_record(outcome, tau, 100, values)


def side(
    n: int,
    vals: Sequence[float] = (1.0, 1.0, 1.0),
    outcomes: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    outcomes = outcomes or {}
    return [mk(outcomes.get(r, "complete"), vals, r) for r in range(n)]


def base_pairs(n: int = 4) -> list[tuple[list[Any], list[Any]]]:
    """8 designs, both sides identical (He² 0) except a planted cell (4, 1)."""
    out = []
    for e in range(c.N_DESIGNS):
        a = side(n, (1.0, 2.0, 1.0) if e == 4 else (1.0, 1.0, 1.0))
        out.append((a, side(n)))
    return out


# --------------------------------------------------------------------------
# Rules (a), (b), (c) and the K-/F-rules
# --------------------------------------------------------------------------


def test_column_rules_on_every_outcome() -> None:
    runs = [
        mk("complete", (1.0, 2.0, 3.0)),
        mk("truncated", (4.0, 5.0, 6.0)),
        mk("cut0"),
        mk("stopped"),
    ]
    assert c.column(runs, "a", 1) == [2.0, None, None, None]
    assert c.column(runs, "b", 1) == [2.0]
    assert c.column(runs, "c", 1) == [2.0, 5.0, None, None]
    assert [c.is_truncated(x) for x in runs] == [False, True, True, False]
    assert [c.is_stopped(x) for x in runs] == [False, False, False, True]


def test_rule_b_zero_row_and_k0_uses_b_for_telling_a_for_truthfsd() -> None:
    pairs = base_pairs()
    pairs[2] = (side(4, outcomes={r: "truncated" for r in range(4)}), side(4))
    tell = c.telling_scored("K0", pairs, N_D)
    assert tell.table[2] == (0.0, 0.0, 0.0)  # (b): truth keeps no run
    assert tell.excluded == frozenset()
    flipped = [(b, a) for a, b in pairs]
    tf = c.truthfsd_scored("K0", flipped, N_D, frozenset())
    assert tf.table[2] == (1.0, 1.0, 1.0)  # (a): every truth run is the atom
    k1 = c.telling_scored("K1", pairs, N_D)
    assert k1.table[2] == (0.0, 0.0, 0.0)  # (c): same values on both sides


def test_k2z_and_fz_zero_mean_rate_only_where_a_side_truncates() -> None:
    pairs = base_pairs()
    pairs[1] = (side(4, (9.0, 1.0, 1.0), {0: "truncated"}), side(4))
    pairs[3] = (side(4, (9.0, 1.0, 1.0), {0: "stopped"}), side(4))
    k1 = c.telling_scored("K1", pairs, N_D)
    k2z = c.telling_scored("K2z", pairs, N_D)
    fz = c.fsd_scored("Fz", pairs, N_D)
    assert k1.table[1][0] > 0.5 and k1.table[3][0] > 0.5
    assert k2z.table[1][0] == 0.0 and fz.table[1][0] == 0.0
    assert k2z.table[3][0] == k1.table[3][0]  # a stop is not a truncation
    for e in range(c.N_DESIGNS):
        for d in range(N_D):
            if (e, d) != (1, 0):
                assert k2z.table[e][d] == k1.table[e][d] == fz.table[e][d]


def test_k3a_k3h_thresholds() -> None:
    pairs = base_pairs()
    pairs[3] = (side(4, outcomes={0: "truncated"}), side(4))  # 1 of 4
    pairs[5] = (side(4, outcomes={0: "truncated", 1: "cut0"}), side(4))  # 2 of 4
    pairs[6] = (side(4), side(4, outcomes={r: "truncated" for r in range(3)}))
    k3a = c.telling_scored("K3a", pairs, N_D)
    k3h = c.telling_scored("K3h", pairs, N_D)
    assert k3a.excluded == frozenset({3, 5, 6})
    assert k3h.excluded == frozenset({6})  # more than half: 3 of 4 only
    assert k3a.table[3] == (0.0, 0.0, 0.0)  # excluded rows are not computed


def test_k3a_against_k4_on_an_all_stopped_design() -> None:
    pairs = base_pairs()
    pairs[0] = (side(4, outcomes={r: "stopped" for r in range(4)}), side(4))
    k3a = c.telling_scored("K3a", pairs, N_D)
    k4 = c.telling_scored("K4", pairs, N_D)
    assert k3a.excluded == frozenset()  # stops are not truncations
    assert k3a.table[0] == (1.0, 1.0, 1.0)  # the atom row
    assert c.telling_pick(k3a, DESIGNS, DIAGS) == (0, 0)  # mean_rate
    assert k4.excluded == frozenset({0})
    assert c.telling_pick(k4, DESIGNS, DIAGS) == (4, 1)  # the planted cell


def test_k5_shares_x_and_truthfsd_keeps_its_own_truncations_outside_x() -> None:
    tell = base_pairs(8)
    tell[3] = (side(8), side(8, outcomes={7: "stopped"}))  # X = {3}
    x = c.k5_exclusion(tell)
    assert x == frozenset({3})
    assert c.telling_scored("K5", tell, N_D).excluded == x
    tf = base_pairs(4)
    tf[4] = (side(4), side(4))  # no planted cell on truthFSD's stub
    # truthFSD's own truth runs truncate on design 5 (outside X), with values
    # far from the member's: K5 keeps them, K3a/K4 exclude design 5.
    tf[5] = (side(4, (1.0, 1.0, 1.0)), side(4, (1.0, 1.0, 7.0), {0: "truncated"}))
    k5 = c.truthfsd_scored("K5", tf, N_D, x)
    assert k5.excluded == x  # design 3 excluded although its own runs are clean
    assert 5 not in k5.excluded and k5.table[5][2] > 0.9
    assert c.policy_pick(k5) == (5, 2)
    assert c.truthfsd_scored("K3a", tf, N_D, x).excluded == frozenset({5})
    assert c.truthfsd_scored("K4", tf, N_D, x).excluded == frozenset({5})
    # x is ignored by every rule but K5
    for k in ("K0", "K1", "K2z", "K3a", "K3h", "K4"):
        assert 3 not in c.truthfsd_scored(k, tf, N_D, x).excluded


def test_f_rules() -> None:
    pairs = base_pairs(16)
    ref = side(16)
    ref[9] = mk("truncated", (5.0, 1.0, 1.0), 9)  # one truncated run in 16
    pairs[1] = (side(16), ref)
    pairs[2] = (side(16), side(16, outcomes={15: "stopped"}))
    fa = c.fsd_scored("Fa", pairs, N_D)
    fc = c.fsd_scored("Fc", pairs, N_D)
    fx = c.fsd_scored("Fx", pairs, N_D)
    assert fx.excluded == frozenset({1, 2})  # one truncation or stop in 16
    assert fa.excluded == fc.excluded == frozenset()
    assert fc.table[1][0] > fa.table[1][0] > 0.0  # (c) keeps values; (a) atom
    assert fa.table[1][1] == fa.table[1][2] == fa.table[1][0]  # atom on every d
    assert c.policy_pick(fc) == c.policy_pick(fx) == (4, 1)
    assert c.policy_pick(fa) == (4, 1)
    assert fa.table[2] == fc.table[2]  # a stop is the atom under (a) and (c)


def test_d_major_against_e_major() -> None:
    table = (
        (0.0, 0.0, 0.9),
        (0.0, 0.95, 0.0),
        *((0.0, 0.0, 0.0),) * 6,
    )
    scored = c.Scored(table, frozenset())
    assert c.telling_pick(scored, DESIGNS, DIAGS) == (1, 1)  # d-major: column 1
    assert c.policy_pick(scored) == (1, 1)
    table2 = ((0.0, 0.0, 0.9), (0.0, 0.9, 0.0), *((0.0, 0.0, 0.0),) * 6)
    s2 = c.Scored(table2, frozenset())
    assert c.telling_pick(s2, DESIGNS, DIAGS) == (1, 1)  # first column at the max
    assert c.policy_pick(s2) == (0, 2)  # e-major: first design at the max
    # exclusion removes a design (no zeroing): ties then go to the kept rows
    s3 = c.Scored(table2, frozenset({1}))
    assert c.telling_pick(s3, DESIGNS, DIAGS) == (0, 2)


def all_truncated_pairs(n: int) -> list[tuple[list[Any], list[Any]]]:
    return [(side(n, outcomes={0: "truncated"}), side(n)) for _ in range(8)]


def test_exclude_all_for_every_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    from sciagent.scoring.telling import Telling

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Telling.from_table called on an all-excluded table")

    monkeypatch.setattr(Telling, "from_table", staticmethod(boom))
    pairs = all_truncated_pairs(4)
    for k in ("K3a", "K4", "K5"):
        scored = c.telling_scored(k, pairs, N_D)
        assert scored.excluded == frozenset(range(8))
        assert c.telling_pick(scored, DESIGNS, DIAGS) is None
        tf = c.truthfsd_scored(k, pairs, N_D, c.k5_exclusion(pairs))
        assert c.policy_pick(tf) is None
    assert c.policy_pick(c.fsd_scored("Fx", pairs, N_D)) is None
    assert c.label_of(None, LABELS) is None


def stub_seed_runs(
    tell: list[Any], decision: list[Any], seeds: Sequence[int]
) -> dict[int, c.SeedRuns]:
    return {s: c.SeedRuns(tell, [decision] * c.DECISIONS) for s in seeds}


def stub_menus(seeds: Sequence[int]) -> c.Menus:
    return c.Menus(DESIGNS, {s: DIAGS for s in seeds}, {s: LABELS for s in seeds})


def test_exclude_all_scores_a_miss_and_keeps_the_unit() -> None:
    seeds = c.SEEDS["ctlA1"]
    runs = stub_seed_runs(all_truncated_pairs(4), all_truncated_pairs(4), seeds)
    out = c.score_a1("ctlA1", runs, stub_menus(seeds), ["x[sign]"])
    for k in ("K3a", "K4", "K5"):
        r = out[k]
        assert r["iii"]["units"] == 15 and r["iii"]["neither"] == 15
        assert r["i"]["count"] == 0 and r["ii"]["count"] == 0 and not r["pass"]
        assert all(v["telling"] is None for v in r["seeds"].values())
    seeds_b = c.SEEDS["ctlB"]
    runs_b = stub_seed_runs(all_truncated_pairs(4), all_truncated_pairs(4), seeds_b)
    out_b = c.score_b("ctlB", runs_b, stub_menus(seeds_b), ["x[sign]"])
    x = out_b["pairs"]["Fx|K4"]
    assert x["B1"]["off_e1"] == 0  # no pick is not "a design other than e1"
    assert x["B3"]["separation"]["units"] == 15 and x["B3"]["credited"] == 0


#: Random's picks never hold this label on 101-115 and only on 312 in 301-315
#: (random-picks.txt), so a planted cell on it separates from random.
PLANT = "mark_intensity_correlation[sign]"


def wide_pairs(
    labels: list[str], n: int, plant: bool = True
) -> list[tuple[list[Any], list[Any]]]:
    d = labels.index(PLANT)
    flat = [1.0] * len(labels)
    hot = [1.5 if i == d else 1.0 for i in range(len(labels))]
    return [
        (side(n, hot if (e == 4 and plant) else flat), side(n, flat))
        for e in range(c.N_DESIGNS)
    ]


def real_menus(labels: list[str], seeds: Sequence[int]) -> c.Menus:
    diags = stub_diagnostics(len(labels))
    return c.Menus(DESIGNS, {s: diags for s in seeds}, {s: labels for s in seeds})


@pytest.mark.slow
def test_a1_scoring_counts_on_a_planted_cell(menu_labels: list[str]) -> None:
    seeds = c.SEEDS["ctlA1"]
    tell = wide_pairs(menu_labels, 8)
    dec = [(b, a) for a, b in wide_pairs(menu_labels, 4)]
    menus = real_menus(menu_labels, seeds)
    out = c.score_a1("ctlA1", stub_seed_runs(tell, dec, seeds), menus, [PLANT])
    d = menu_labels.index(PLANT)
    for k in c.K_RULES:
        r = out[k]
        assert r["i"]["count"] == 15 and r["ii"]["count"] == 120
        assert r["seeds"]["101"]["telling"] == [4, d, PLANT]
        assert r["iii"]["arm_only"] == 15 and r["iii"]["random_only"] == 0
        assert r["iii"]["passed"] and r["pass"]
    # a miss on (ii): truthFSD's decisions see nothing (no planted cell)
    flat = [(b, a) for a, b in wide_pairs(menu_labels, 4, plant=False)]
    out2 = c.score_a1("ctlA1", stub_seed_runs(tell, flat, seeds), menus, [PLANT])
    assert out2["K1"]["ii"]["count"] == 0 and not out2["K1"]["pass"]
    assert out2["K1"]["i"]["pass"]


@pytest.mark.slow
def test_b_scoring_b1_b2_b3(menu_labels: list[str]) -> None:
    seeds = c.SEEDS["ctlB"]
    tell = wide_pairs(menu_labels, 8)
    dec = [(b, a) for a, b in wide_pairs(menu_labels, 16)]
    d = menu_labels.index(PLANT)
    # the reference truncates on e1 (the artefact) and differs most on the
    # planted label there: Fa and Fc pick e1, Fx excludes it
    hot = [9.0 if i == d else 1.0 for i in range(len(menu_labels))]
    dec[c.E1] = (
        side(16, [1.0] * len(menu_labels)),
        side(16, hot, {r: "truncated" for r in range(16)}),
    )
    menus = real_menus(menu_labels, seeds)
    out = c.score_b("ctlB", stub_seed_runs(tell, dec, seeds), menus, [PLANT])
    fc = out["pairs"]["Fc|K1"]
    assert fc["B1"]["off_e1"] == 0 and not fc["B1"]["pass"]
    assert fc["B2"]["credited_only_on_e1"] == list(seeds) and not fc["B2"]["pass"]
    fa = out["pairs"]["Fa|K1"]
    assert fa["B1"]["off_e1"] == 0  # the atom row on e1 is all 1.0: e1 first
    fx = out["pairs"]["Fx|K1"]
    assert fx["B1"]["off_e1"] == 120 and fx["B2"]["pass"]
    assert fx["B3"]["credited"] == 15 and fx["B3"]["separation"]["random_only"] == 0
    assert fx["pass"]
    assert out["telling"]["K1"]["in_d_star0"] == 15


def test_cut0_and_non_finite_values() -> None:
    from sciagent.diagnostics import catalogue

    outputs: list[float | str | None] = [1.5, math.nan, math.inf, None, "raise", -2.0]
    queue = iter(outputs)

    def compute(choice: Any) -> float | None:
        v = next(queue)
        if isinstance(v, str):
            raise catalogue.DiagnosticError("stub")
        return v

    values, other, nonfinite = c.read_values(stub_diagnostics(6), compute)
    assert values == [1.5, None, None, None, None, -2.0]
    assert (other, nonfinite) == (1, 2)
    cut = mk("cut0")
    assert cut["values"] == [None, None, None] and c.is_truncated(cut)
    pairs = base_pairs()
    pairs[6] = (side(4, outcomes={0: "cut0"}), side(4))
    assert c.telling_scored("K3a", pairs, N_D).excluded == frozenset({6})
    assert c.column(pairs[6][0], "c", 1)[0] is None
    with pytest.raises(c.ControlsError):
        c.outcome_record("exploded", None, None, None)


# --------------------------------------------------------------------------
# Calibration: D*0, P1, the rung reading and the exit statuses
# --------------------------------------------------------------------------


def test_d_star0_selection_and_p2_parts() -> None:
    labels = ["mean_rate", "a[sign]", "b[sign]", "c[size]"]
    sel, ok = c.d_star0({"a[sign]": 600, "b[sign]": 360, "c[size]": 40}, labels)
    assert (sel, ok) == (["a[sign]", "b[sign]"], True)
    sel, ok = c.d_star0({"b[sign]": 500, "a[sign]": 500}, labels)
    assert sel == ["a[sign]", "b[sign]"]  # ties in menu order
    sel, ok = c.d_star0({"a[sign]": 900}, labels)  # exclude-all draws
    assert (sel, ok) == (["a[sign]"], False)
    assert c.carries("mark_cross_correlation[sign,size]", "size")
    assert c.carries("mark_mean[sign]", "sign") and not c.carries("mean_rate", "sign")
    assert not c.carries("mark_mean[size]", "sign")


def cal_from(
    planned: int,
    broken: tuple[int, int, str] | None = None,
    n: int = c.R_CAL,
    plant: int = 4,
) -> c.CalRuns:
    def runs(kind: str) -> dict[tuple[int, int], list[Any]]:
        out = {}
        for e in range(c.N_DESIGNS):
            for s in (0, 1):
                vals = (1.0, 1.03, 1.0) if (e == plant and s == 0) else (1.0, 1.0, 1.0)
                outs = (
                    {r: "truncated" for r in range(n)}
                    if (s == 0 and e == planned)
                    else {}
                )
                if broken and broken[:2] == (e, s) and kind == "crn":
                    outs = {**outs, 0: broken[2]}
                out[(e, s)] = side(n, vals, outs)
        return out

    return c.CalRuns(runs("cal"), runs("crn"))


def test_p1_and_e_cal() -> None:
    rung = c.RUNGS["ctlA1"][0]
    assert c.p1_holds(rung, cal_from(c.E1))
    assert c.e_cal(cal_from(c.E1)) == frozenset({c.E1})
    assert not c.p1_holds(rung, cal_from(c.E1, (5, 1, "truncated")))  # member
    assert not c.p1_holds(rung, cal_from(c.E1, (5, 0, "stopped")))
    assert not c.p1_holds(rung, cal_from(c.E1, (c.E1, 0, "complete")))  # 255/256
    assert c.e_cal(cal_from(c.E1, (5, 0, "cut0"))) == frozenset({c.E1, 5})


def test_bootstrap_and_rung_values_on_stub_runs(token: g.GateToken) -> None:
    rung = c.RUNGS["ctlA1"][0]
    cal = cal_from(c.E1)
    boot = c.bootstrap_rung(
        rung, cal, LABELS, DESIGNS, DIAGS, c.NullLedger(), token=token, n_boot=20
    )
    assert boot.d_star0_counts == {"x[sign]": 20}
    assert boot.p32_counts == {"x[sign]": 20} and boot.p16_counts == {"x[sign]": 20}
    assert boot.clause_count == 20 and boot.pf_counts is None
    scaled = c.Bootstrap(
        {"x[sign]": 1000}, {"x[sign]": 1000}, {"x[sign]": 1000}, None, 1000
    )
    randoms = {s: ("y[size]",) * 8 for s in c.SEEDS["ctlA1"]}
    v = c.rung_values(rung, cal, scaled, LABELS, randoms)
    assert v["p1"] and v["p2"] and v["clause99"] and v["h"] == 1.0
    assert v["reading"] == "passes" and v["buildable"]
    assert v["pass_ii"] == pytest.approx(1.0) and v["pass_iii"] > 0.95
    rung_b = c.RUNGS["ctlB"][0]
    boot_b = c.bootstrap_rung(
        rung_b,
        cal_from(c.E2),
        LABELS,
        DESIGNS,
        DIAGS,
        c.NullLedger(),
        token=token,
        n_boot=10,
    )
    assert boot_b.pf_counts == {"x[sign]": 10}


def rung_row(reading: str, j: int = 0) -> dict[str, Any]:
    return {"rung": j, "reading": reading, "buildable": reading != "unbuildable"}


def test_rung_reading_every_branch() -> None:
    ok = {"p1": True, "p2": True, "clause99": True}
    a1 = {**ok, "h": 0.97, "pass_ii": 0.96, "pass_iii": 0.96}
    assert c.read_rung("ctlA1", a1) == "passes"
    for k in ("h", "pass_ii", "pass_iii"):
        assert c.read_rung("ctlA1", {**a1, k: 0.94}) == "R=16 shortfall"
    for k in ("p1", "p2", "clause99"):
        assert c.read_rung("ctlA1", {**a1, k: False}) == "unbuildable"
        assert c.read_rung("ctlB", {**ok, k: False, "h": 1, "h_f": 1}) == "unbuildable"
    b = {**ok, "h": 0.97, "h_f": 0.96, "pass_iii_f": 0.96}
    assert c.read_rung("ctlB", b) == "passes"
    # v8 order: h < 0.95 first, then h_F < 0.95, else a separation shortfall
    assert c.read_rung("ctlB", {**b, "h_f": 0.9}) == "cost of Fx's exclusion"
    assert c.read_rung("ctlB", {**b, "h": 0.9, "h_f": 0.9}) == "R=16 shortfall"
    assert c.read_rung("ctlB", {**b, "h": 0.9, "h_f": 0.99, "pass_iii_f": 0.1}) == (
        "R=16 shortfall"
    )
    assert c.read_rung("ctlB", {**b, "pass_iii_f": 0.9}) == "separation shortfall"


def test_calibration_exit_statuses() -> None:
    assert c.calibration_result([rung_row("unbuildable"), rung_row("passes", 1)]) == (
        0,
        1,
        "calibrated",
    )
    code, tested, _ = c.calibration_result(
        [rung_row("R=16 shortfall"), rung_row("unbuildable", 1)]
    )
    assert (code, tested) == (c.EXIT_NOT_TESTED, None) == (13, None)
    code, _, _ = c.calibration_result(
        [rung_row("unbuildable"), rung_row("unbuildable", 1)]
    )
    assert code == c.EXIT_VOID == 12
    code, _, name = c.calibration_result([rung_row("passes")], ref_ok=False)
    assert code == 12 and "reference" in name


def breach_records(bad: dict[int, str]) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Seeds 101-115, telling on e1/e2: the truth truncates on e1 only."""
    recs = {}
    for s in c.SEEDS["ctlA1"]:
        for e in (0, c.E1):
            for name in ("truth", "member"):
                want = "truncated" if (name == "truth" and e == c.E1) else "complete"
                got = bad.get(s, want) if (e, name) == (0, "truth") else want
                recs[(s, "telling", e, name, 0)] = mk(got)
    return recs


def test_p1_breaches_and_score_exit_statuses() -> None:
    rung = c.RUNGS["ctlA1"][0]
    assert c.p1_breaches(breach_records({}), "ctlA1", rung) == {}
    two = c.p1_breaches(
        breach_records({101: "truncated", 102: "stopped"}), "ctlA1", rung
    )
    assert sorted(two) == [101, 102]
    assert two[102] == [["telling", 0, "truth", 0, "stopped"]]
    assert c.score_exit(True, len(two)) == (0, "scored")
    three = c.p1_breaches(
        breach_records({101: "truncated", 102: "cut0", 103: "stopped"}), "ctlA1", rung
    )
    assert c.score_exit(True, len(three))[0] == c.EXIT_BREACH == 14
    assert c.score_exit(False, 0)[0] == c.EXIT_NONDET == 15
    # a run that does not truncate where calibration had 256/256
    recs = breach_records({})
    recs[(101, "telling", c.E1, "truth", 0)] = mk("complete")
    assert c.p1_breaches(recs, "ctlA1", rung) == {
        101: [["telling", c.E1, "truth", 0, "complete"]]
    }
    rung_b = c.RUNGS["ctlB"][0]
    assert c.expected_truncation("ctlB", rung_b, "k0", "ref", c.E1)
    assert c.expected_truncation("ctlB", rung_b, "k0", "ref", c.E2)
    assert not c.expected_truncation("ctlB", rung_b, "k0", "member", c.E2)


def test_execute_maps_stop_to_11_and_charges(tmp_path: Path) -> None:
    (tmp_path / "cpu-total.txt").write_text("0.0\n")
    ticks = iter([5.0, 7.0])
    ledger = c.Ledger(tmp_path, "test", "ctlA1", 100.0, clock=lambda: next(ticks))

    def stopped() -> int:
        raise c.StopRequested("stub")

    assert c.execute(stopped, ledger) == c.EXIT_STOP == 11
    assert float((tmp_path / "cpu-total.txt").read_text()) == 5.0
    assert c.execute(lambda: 13, ledger) == 13
    assert float((tmp_path / "cpu-total.txt").read_text()) == 7.0


def test_cli_refuses_with_10_before_touching_git(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--worktree", "/nonexistent", "--freeze-commit", "0" * 40]
    argv += ["--control", "A1", "--phase", "calibrate"]
    assert c.main(argv) == c.EXIT_GATE == 10
    assert "gate refused [worktree]" in capsys.readouterr().err


def test_every_exit_status_is_distinct_from_pythons() -> None:
    codes = [c.EXIT_DONE, c.EXIT_GATE, c.EXIT_STOP, c.EXIT_VOID]
    codes += [c.EXIT_NOT_TESTED, c.EXIT_BREACH, c.EXIT_NONDET]
    assert codes == [0, 10, 11, 12, 13, 14, 15]


# --------------------------------------------------------------------------
# Ledger (criteria §6)
# --------------------------------------------------------------------------

LEDGER_CHILD = """
import sys
sys.path.insert(0, {here!r})
import controls as c
from pathlib import Path
t = [0.0]
def clock():
    t[0] += 0.5
    return t[0]
ledger = c.Ledger(Path({dir!r}), "test", "ctlA1", 1e9, clock=clock)
ledger.start()
for i in range(200):
    ledger.charge(f"run-{{i}}")
"""


@pytest.mark.slow
def test_ledger_two_processes_lose_no_update(tmp_path: Path) -> None:
    (tmp_path / "cpu-total.txt").write_text("0.0\n")
    code = LEDGER_CHILD.format(here=str(HERE), dir=str(tmp_path))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(2)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    assert float((tmp_path / "cpu-total.txt").read_text()) == 200.0  # 2 x 200 x 0.5
    audit = (tmp_path / "cpu-ledger.csv").read_text().splitlines()
    assert len(audit) == 400
    assert {row.split(",")[0] for row in audit} == {"test"}


def test_ledger_loops_are_charged_every_30_cpu_seconds(tmp_path: Path) -> None:
    (tmp_path / "cpu-total.txt").write_text("0.0\n")
    now = [0.0]
    ledger = c.Ledger(tmp_path, "calibrate", "ctlA1", 1e6, clock=lambda: now[0])
    for _ in range(100):
        now[0] += 1.0
        ledger.tick("boot")
    assert float((tmp_path / "cpu-total.txt").read_text()) == 90.0
    assert len((tmp_path / "cpu-ledger.csv").read_text().splitlines()) == 3


def test_ledger_refuses_a_missing_or_unparseable_total(tmp_path: Path) -> None:
    ledger = c.Ledger(tmp_path, "test", "ctlA1", 100.0, clock=lambda: 1.0)
    with pytest.raises(c.LedgerError, match="missing"):
        ledger.start()
    with pytest.raises(c.LedgerError):
        ledger.charge("x")
    assert not (tmp_path / "cpu-total.txt").exists()  # no silent reset
    for bad in ("", "abc", "nan", "-1.0", "inf"):
        (tmp_path / "cpu-total.txt").write_text(bad)
        with pytest.raises(c.LedgerError):
            ledger.start()
    with pytest.raises(c.LedgerError):
        c.Ledger(tmp_path, "test", "ctlA1", math.nan)


def test_stop_fires_at_the_ceiling_and_blocks_every_process(tmp_path: Path) -> None:
    (tmp_path / "cpu-total.txt").write_text("0.0\n")
    now = [0.0]
    ledger = c.Ledger(tmp_path, "test", "ctlA1", 10.0, clock=lambda: now[0])
    ledger.check_stop()
    now[0] = 10.0
    ledger.charge("run")
    with pytest.raises(c.StopRequested, match="ceiling"):
        ledger.check_stop()
    assert (tmp_path / "STOP").read_text().startswith("total=10.0 time=")
    other = c.Ledger(tmp_path, "test", "ctlB", 1e9, clock=lambda: 0.0)
    with pytest.raises(c.StopRequested, match="exists"):
        other.check_stop()
    score = c.Ledger(tmp_path, "score", "ctlA1", 10.0, exempt=True, clock=lambda: 1.0)
    score.check_stop()  # score is exempt, and still charged
    score.charge("score")
    assert float((tmp_path / "cpu-total.txt").read_text()) == 11.0
    assert (tmp_path / "STOP").exists()  # no process removes STOP


# --------------------------------------------------------------------------
# Canonical JSON and the runs store
# --------------------------------------------------------------------------


def test_canonical_json_bytes() -> None:
    obj = {
        "b": [1.0, None, 0.1, 1e-300, 15000],
        "a": "\u00e9",
        "c": {"z": True, "y": 2},
    }
    assert c.canonical(obj) == (
        '{"a":"\\u00e9","b":[1.0,null,0.1,1e-300,15000],"c":{"y":2,"z":true}}\n'
    )
    x = 0.1 + 0.2
    assert json.loads(c.canonical([x]))[0] == x  # shortest round-trip repr
    for bad in (math.nan, math.inf):
        with pytest.raises(ValueError):
            c.canonical([bad])


HEADER = {"control": "ctlA1", "F": "f" * 40, "D": "d" * 40}
KEY0 = {"seed": 101, "arm": "telling", "design": 0, "side": "truth", "replicate": 0}


def test_run_store_cuts_a_torn_tail_and_refuses_corruption(tmp_path: Path) -> None:
    path = tmp_path / "runs-ctlA1.jsonl"
    store = c.RunStore(path, c.TEST_KEY, HEADER)
    store.load()
    keys = [
        {"seed": 101, "arm": "telling", "design": e, "side": "truth", "replicate": 0}
        for e in range(3)
    ]
    for k in keys:
        store.append(c.run_line(k, mk()))
    good = path.read_bytes()
    with pytest.raises(c.StoreError):
        store.append(c.run_line(keys[0], mk()))
    path.write_bytes(good + b'{"seed":101,"arm":"tel')
    again = c.RunStore(path, c.TEST_KEY, HEADER)
    again.load()
    assert len(again.records) == 3 and path.read_bytes() == good
    lines = good.splitlines(True)
    assert lines[0] == c.canonical(HEADER).encode()
    assert again.raw[(101, "telling", 1, "truth", 0)] == lines[2]
    path.write_bytes(lines[0] + b"not json\n" + b"".join(lines[1:]))
    with pytest.raises(c.StoreError):
        c.RunStore(path, c.TEST_KEY, HEADER).load()
    path.write_bytes(good + lines[1])
    with pytest.raises(c.StoreError, match="duplicate"):
        c.RunStore(path, c.TEST_KEY, HEADER).load()


def test_runs_header_refuses_another_freeze_or_d(tmp_path: Path) -> None:
    path = tmp_path / "runs-ctlA1.jsonl"
    store = c.RunStore(path, c.TEST_KEY, HEADER)
    store.load()
    store.append(c.run_line(KEY0, mk()))
    for other in ({**HEADER, "F": "1" * 40}, {**HEADER, "D": "2" * 40}):
        with pytest.raises(c.HeaderError):
            c.RunStore(path, c.TEST_KEY, other).load()
    torn = c.canonical(HEADER).encode()[:10]
    path.write_bytes(torn)  # a torn header starts afresh
    c.RunStore(path, c.TEST_KEY, HEADER).load()
    assert path.read_bytes() == c.canonical(HEADER).encode()
    with pytest.raises(c.StoreError):
        c.RunStore(path, c.TEST_KEY)  # a file always has a header
    mem = c.RunStore(None, c.CAL_KEY)  # calibrate's memory store writes nothing
    mem.load()
    mem.append(
        c.run_line(
            {"set": "cal", "rung": 0, "design": 0, "side": 0, "replicate": 0}, mk()
        )
    )
    assert list(tmp_path.iterdir()) == [path]


# --------------------------------------------------------------------------
# Invariant 2: X never reaches FSDPolicy, the tool layer or an agent record
# --------------------------------------------------------------------------


def test_x_never_reaches_fsdpolicy_tools_or_agent_records() -> None:
    from sciagent.systems.v2 import policies

    fields = {f.name for f in dataclasses.fields(policies.FSDPolicy)}
    assert fields == {"seed", "replicates", "reference", "max_events", "name"}
    assert list(inspect.signature(policies.choose).parameters) == ["table", "tolerance"]
    assert list(inspect.signature(policies.fsd_table).parameters) == [
        "context",
        "seed",
        "decision",
        "replicates",
        "reference",
        "parametric",
        "max_events",
    ]
    forbidden = (
        "sciagent.harness",
        "sciagent.investigation.tools",
        "sciagent.investigation.runner",
        "sciagent.investigation.prompts",
        "sciagent.investigation.policy",
        "sciagent.investigation.timeline",
        "sciagent.sandbox",
    )
    callers: set[str] = set()
    for name in ("controls.py", "ctl_gate.py"):
        tree = ast.parse((HERE / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden), node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), alias.name
            if isinstance(node, ast.Name | ast.Attribute):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert ident not in ("FSDPolicy", "EIGPolicy", "decide"), ident
            if isinstance(node, ast.FunctionDef):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "k5_exclusion"
                    ):
                        callers.add(node.name)
    # X is computed and consumed only inside scoring functions
    assert callers == {"telling_scored", "score_a1", "determinism_unit"}
    line = c.run_line(
        {"seed": 1, "arm": "k0", "design": 0, "side": "member", "replicate": 0}, mk()
    )
    assert sorted(line) == sorted([*c.TEST_KEY, *c.RUN_FIELDS])


# --------------------------------------------------------------------------
# Power formulas and the random arm, against the preregistration's numbers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def menu_labels() -> list[str]:
    truth = c.RUNGS["ctlA1"][0].truth.build()
    return [x.label for x in c.menu_for(truth, 0)]


def test_random_picks_match_random_picks_txt(menu_labels: list[str]) -> None:
    lines = (HERE / "random-picks.txt").read_text().splitlines()
    expected = {}
    for line in lines:
        head, _, rest = line.partition(" ")
        if head.isdigit():
            expected[int(head)] = [lab for _, lab in ast.literal_eval(rest)]
    assert sorted(expected) == [*c.SEEDS["ctlA1"], *c.SEEDS["ctlB"]]
    for s, labs in expected.items():
        assert list(c.random_labels(s, menu_labels)) == labs


def test_pass_ii_matches_power2_out() -> None:
    cases = [
        (10, {"x": 0.95, "y": 0.05}, {"x": 0.84, "y": 0.12}, 0.942),
        (10, {"x": 0.8, "y": 0.2}, {"x": 1.0, "y": 0.0}, 0.879),
        (10, {"x": 1.0}, {"x": 0.8}, 0.989),
        (20, {"x": 0.95, "y": 0.05}, {"x": 0.9, "y": 0.1}, 0.998),
    ]
    for n, p32, p16, want in cases:
        assert round(c.pass_ii(n, p32, p16), 3) == want


def test_pass_iii_matches_power2_out(menu_labels: list[str]) -> None:
    # power2-out.txt: "(iii)/B3 power ... worst over [sign]", A1 N=15, h=0.95
    out = (HERE / "power2-out.txt").read_text()
    line = next(x for x in out.splitlines() if x.strip().startswith("A1 N=15:"))
    want = float(line.split("h=0.95: ")[1].split()[0])
    seeds = list(range(101, 116))
    randoms = {s: c.random_labels(s, menu_labels) for s in seeds}
    sign = [lab for lab in menu_labels if "sign" in lab]
    worst = min(c.pass_iii(seeds, {lab: 1.0}, {lab: 0.95}, randoms) for lab in sign)
    assert round(worst, 3) == want


# --------------------------------------------------------------------------
# Parity with the fsd-trunc measurement (criteria §7): K0-K4, 0 mismatches
# --------------------------------------------------------------------------

F_OF_K = {"K0": "Fa", "K1": "Fc", "K2z": "Fz", "K3a": "Fc", "K3h": "Fc", "K4": "Fc"}


def parity_record(d: dict[str, Any]) -> dict[str, Any]:
    if d["stopped"]:
        outcome = "stopped"
    elif d["cut_at_zero"]:
        outcome = "cut0"
    elif d["truncated_at"] is not None:
        outcome = "truncated"
    else:
        outcome = "complete"
    return c.outcome_record(outcome, d["truncated_at"], d["n_events"], d["values"])


def test_parity_manifest_and_k0_k4_picks() -> None:
    p = HERE / "parity"
    manifest = json.loads((p / "manifest.json").read_text())
    for meta in manifest["copies"].values():
        assert g.sha256_file(p / meta["path"]) == meta["sha256"]
    for meta in manifest["sources"].values():
        assert g.sha256_file(Path(meta["path"])) == meta["sha256"]
    runs: dict[tuple[str, int, str], dict[int, Any]] = {}
    for raw in (p / "trunc-lines.jsonl").read_text().splitlines():
        d = json.loads(raw)
        runs.setdefault((d["truth"], d["design_index"], d["side"]), {})[
            d["replicate"]
        ] = parity_record(d)
    infos = {
        json.loads(x)["truth"]: json.loads(x)
        for x in (p / "trunc-truths.jsonl").read_text().splitlines()
    }
    want = json.loads((p / "candidates-picks.json").read_text())

    def pairs(tid: str, a: str, b: str) -> list[Any]:
        return [
            (
                [runs[(tid, e, a)][r] for r in sorted(runs[(tid, e, a)])],
                [runs[(tid, e, b)][r] for r in sorted(runs[(tid, e, b)])],
            )
            for e in range(c.N_DESIGNS)
        ]

    mismatches = []
    checked = 0
    for k in manifest["rules"]:
        for tid in manifest["truths"]:
            n_d = len(infos[tid]["labels"])
            diags = stub_diagnostics(n_d)
            got = {
                "tell": c.telling_pick(
                    c.telling_scored(k, pairs(tid, "truth", "glm"), n_d), DESIGNS, diags
                ),
                "tfsd": c.policy_pick(
                    c.truthfsd_scored(k, pairs(tid, "glm", "truth"), n_d, frozenset())
                ),
                "fsd": c.policy_pick(
                    c.fsd_scored(F_OF_K[k], pairs(tid, "glm", "wh"), n_d)
                ),
            }
            for arm, pick in got.items():
                checked += 1
                if pick is None or list(pick) != want[k][tid][arm]:
                    mismatches.append((k, tid, arm, pick, want[k][tid][arm]))
    print(f"parity: {checked} picks checked, {len(mismatches)} mismatches")
    assert checked == 6 * 4 * 3 and mismatches == []


# --------------------------------------------------------------------------
# Rules that v8 asks a stub test for: removal, not zeroing (E7)
# --------------------------------------------------------------------------


def test_exclusion_removes_a_design_it_does_not_zero_it() -> None:
    hot = (0.9, 0.8, 0.7)
    table = (hot, *((0.0, 0.0, 0.0),) * 7)
    scored = c.Scored(table, frozenset({0}))
    # every remaining cell is 0: zeroing row 0 would give (0, 0) by menu order
    assert c.telling_pick(scored, DESIGNS, DIAGS) == (1, 0)
    assert c.policy_pick(scored) == (1, 0)
    zeroed = c.Scored(((0.0, 0.0, 0.0), *table[1:]), frozenset())
    assert c.telling_pick(zeroed, DESIGNS, DIAGS) == (0, 0)
    assert c.policy_pick(zeroed) == (0, 0)


# --------------------------------------------------------------------------
# The token: internal draw functions refuse the real ids without it (§7)
# --------------------------------------------------------------------------


def test_internal_functions_refuse_the_real_ids_without_the_token(
    token: g.GateToken,
) -> None:
    calls: list[Callable[[object], Any]] = [
        lambda t: c.telling_seed("ctlA1", 101, 0, 1, 0, token=t),
        lambda t: c.cal_seed("ctlB", 3, 0, 1, 0, token=t),
        lambda t: c.crn_seed("ctlA1", 0, 0, 0, token=t),
        lambda t: c.boot_seed("ctlB", 0, token=t),
        lambda t: c.ref_seed(1, 0, token=t),
    ]
    for call in calls:
        with pytest.raises(c.ControlsError, match="token"):
            call(None)
        with pytest.raises(c.ControlsError, match="token"):
            call(object())
        call(token)  # a passed gate's token draws
    rung = c.RUNGS["ctlA1"][0]
    truth, member = rung.truth.build(), c.MEMBER.build()
    with pytest.raises(c.ControlsError, match="token"):
        c.draw_todo("ctlA1", 101, truth, member, None, DESIGNS, token=None)
    with pytest.raises(c.ControlsError, match="token"):
        c.cal_todo(rung, truth, member, DESIGNS, token=None)
    with pytest.raises(c.ControlsError, match="token"):
        c.calibration_d0("ctlB", c.NullLedger(), token=None)
    c.telling_seed("ctl-det", 0, 0, 0, 0, token=None)  # probe ids need none
    with pytest.raises(g.GateError):
        g.GateToken("0" * 40, object())  # only the gate builds one


def test_ledger_calibrate_updates_are_coarse(tmp_path: Path) -> None:
    (tmp_path / "cpu-total.txt").write_text("0.0\n")
    now = [0.0]
    ledger = c.Ledger(
        tmp_path, "calibrate", "ctlA1", 1e6, coarse=True, clock=lambda: now[0]
    )
    for _ in range(14):
        now[0] += 5.0
        ledger.after_run("cal")
    ledger.at_exit()
    rows = (tmp_path / "cpu-ledger.csv").read_text().splitlines()
    assert [r.split(",")[2] for r in rows] == ["charge-1", "charge-2", "charge-3-exit"]
    assert float((tmp_path / "cpu-total.txt").read_text()) == 70.0
    fine = c.Ledger(tmp_path, "test", "ctlA1", 1e6, clock=lambda: now[0] + 1.0)
    fine.after_run("seed-101")  # test charges after every run
    assert (tmp_path / "cpu-ledger.csv").read_text().splitlines()[-1].split(",")[2] == (
        "seed-101"
    )


# --------------------------------------------------------------------------
# Gate, D build and push, in a scratch repository with a local bare origin
# (criteria §5, v8). gh is injected; GitHub is never called.
# --------------------------------------------------------------------------

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
REL = ".cache/drafts/fsd-controls"


def sh(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return done.stdout.strip()


def gh_ok(args: Sequence[str]) -> str:
    return g.H1_EXPECTED if "protection" in args[1] else "0"


class Scratch:
    """$R with a bare origin, $D, and a $WT repo; the URL is the bare path."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.repo = root / "repo"
        self.wt = root / "wt"
        self.pyc = root / "pyc"
        self.pyc.mkdir(parents=True)
        self.env = {v: "1" for v in g.BLAS_VARS} | {
            "PYTHONPYCACHEPREFIX": str(self.pyc)
        }
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "v2", str(self.origin)], check=True
        )
        self.repo.mkdir()
        sh(self.repo, "init", "-q", "-b", "v2")
        # the gate strips GIT_* (s18b 4): commit-tree reads the repo's config
        sh(self.repo, "config", "user.name", "t")
        sh(self.repo, "config", "user.email", "t@example.invalid")
        sh(self.repo, "remote", "add", "origin", str(self.origin))
        (self.repo / ".gitignore").write_text(".cache/\n")
        (self.repo / "docs/v2").mkdir(parents=True)
        (self.repo / "docs/v2/LEDGER.csv").write_text(
            "date,item,provider,hours,usd,stage\n"
        )
        self.d = self.repo / REL
        self.d.mkdir(parents=True)
        sh(self.repo, "add", ".")
        sh(self.repo, "commit", "-q", "-m", "base")
        self.base = sh(self.repo, "rev-parse", "HEAD")
        sh(self.repo, "push", "-q", "origin", "v2")
        self.wt.mkdir()
        sh(self.wt, "init", "-q", "-b", "main")
        (self.wt / "src").mkdir()
        (self.wt / "src" / "m.py").write_text("x = 1\n")
        (self.wt / "uv.lock").write_text("lock\n")
        (self.wt / ".gitignore").write_text("__pycache__/\n")
        sh(self.wt, "add", ".")
        sh(self.wt, "commit", "-q", "-m", "wt")
        self.chain: list[str] = []

    def freeze(
        self,
        controls_text: str = "# stub\n",
        *,
        extra: dict[str, str] | None = None,
        row: str | None = "fsd-controls freeze {f} ceiling 7.6 CPU h",
    ) -> str:
        (self.d / "criteria.md").write_text("criteria\n")
        (self.d / "controls.py").write_text(controls_text)
        (self.d / "notes.md").write_text("notes\n")
        names = ["criteria.md", "controls.py", "notes.md"]
        for name, text in (extra or {}).items():
            (self.d / name).write_text(text)
            names.append(name)
        h1 = g.sha256_file(self.d / "criteria.md")
        h2 = g.sha256_file(self.d / "controls.py")
        sh(self.repo, "add", "-f", *(f"{REL}/{n}" for n in names))
        msg = f"freeze\n\ncriteria.md sha256: {h1}\ncontrols.py sha256: {h2}\n"
        sh(self.repo, "commit", "-q", "-m", msg)
        sh(self.repo, "pull", "-q", "--rebase", "origin", "v2")
        f = sh(self.repo, "rev-parse", "HEAD")
        sh(self.repo, "push", "-q", "origin", "v2")
        if row is not None:
            self.row(row.format(f=f))
        self.chain.append(f)
        return f

    def row(self, item: str) -> None:
        with (self.repo / "docs/v2/LEDGER.csv").open("a") as fh:
            fh.write(f"2026-10-29,{item},dm-devbox,0,0,p4\n")
        sh(self.repo, "add", "docs/v2/LEDGER.csv")
        sh(self.repo, "commit", "-q", "-m", "ledger")
        sh(self.repo, "pull", "-q", "--rebase", "origin", "v2")
        sh(self.repo, "push", "-q", "origin", "v2")

    @property
    def paths(self) -> g.Paths:
        head = sh(self.wt, "rev-parse", "HEAD")
        return g.Paths(self.repo, REL, self.wt, head, url=str(self.origin))

    @property
    def crit(self) -> str:
        return g.sha256_file(self.d / "criteria.md")

    def gate(
        self,
        phase: str = "calibrate",
        d: str | None = None,
        *,
        f: str | None = None,
        env: dict[str, str] | None = None,
        gh: g.GhReader = gh_ok,
        control: str = "ctlA1",
    ) -> g.GateResult:
        return g.gate(
            self.paths,
            phase,
            control,
            f or self.chain[-1],
            d,
            env or self.env,
            gh=gh,
            criteria_constant=self.crit,
        )

    def push_d(self, control: str = "ctlA1", **kwargs: Any) -> str:
        def make(r: g.GateResult) -> bytes:
            return c.canonical({"records": r.record(score=False)}).encode()

        d, data, _ = g.push_d0(
            self.paths,
            control,
            self.chain[-1],
            self.env,
            make,
            gh=gh_ok,
            criteria_constant=self.crit,
            sleep=kwargs.pop("sleep", lambda s: None),
            **kwargs,
        )
        (self.d / f"D0-{control}.json").write_bytes(data)
        return d

    def manual_d(self, files: dict[str, bytes], message: str) -> str:
        for name, data in files.items():
            (self.repo / name).write_bytes(data)
        sh(self.repo, "add", "-f", *files)
        sh(self.repo, "commit", "-q", "-m", message)
        d = sh(self.repo, "rev-parse", "HEAD")
        sh(self.repo, "push", "-q", "origin", "v2")
        return d


@pytest.fixture
def scratch(tmp_path: Path) -> Scratch:
    s = Scratch(tmp_path)
    s.freeze()
    return s


@pytest.fixture(scope="module")
def token(tmp_path_factory: pytest.TempPathFactory) -> g.GateToken:
    s = Scratch(tmp_path_factory.mktemp("token"))
    s.freeze()
    return s.gate().token


def refused(fn: Callable[[], Any], check: str) -> None:
    with pytest.raises(g.GateError) as info:
        fn()
    assert info.value.check == check, str(info.value)


def test_gate_passes_on_a_clean_scratch_and_records(scratch: Scratch) -> None:
    for name in (
        "cpu-total.txt",
        "cpu-total.txt.tmp",
        "STOP",
        "runs-ctlA1.jsonl",
        "x-err.txt",
    ):
        (scratch.d / name).write_text("")
    for d in ("__pycache__", ".ruff_cache"):
        (scratch.d / d).mkdir()
        (scratch.d / d / "x").write_text("")
    r = scratch.gate()
    assert r.freeze == scratch.chain[0] and r.chain == tuple(scratch.chain)
    assert r.status == "preregistered" and r.ceiling_h == 7.6
    assert r.h1 == g.H1_EXPECTED and r.h2 == ("0", "0")
    assert isinstance(r.token, g.GateToken) and r.token.o == r.o
    assert (scratch.d / "git.lock").exists()  # the gate's own lock is a run output
    rec = r.record(score=False)
    assert "o" not in rec and "d0_commit" not in rec
    assert rec["freeze_chain"] == [
        {
            "freeze": r.freeze,
            "rows": [f"fsd-controls freeze {r.freeze} ceiling 7.6 CPU h"],
        }
    ]
    d = scratch.push_d()
    r2 = scratch.gate("test", d)
    assert r2.d0_commit == d and r2.record(score=True)["o"] == r2.o
    assert r2.record(score=True)["status_commits"] == [
        {"commit": d, "descends_from_F": True}
    ]


@pytest.mark.parametrize(
    "var,value",
    [
        ("OPENBLAS_NUM_THREADS", "2"),
        ("OMP_NUM_THREADS", None),
        ("MKL_NUM_THREADS", "4"),
        ("PYTHONPYCACHEPREFIX", None),
        ("PYTHONPYCACHEPREFIX", "relative/dir"),
        ("PYTHONPYCACHEPREFIX", "INSIDE"),
        ("PYTHONPYCACHEPREFIX", "MISSING"),
    ],
)
def test_gate_refuses_the_environment(
    scratch: Scratch, var: str, value: str | None
) -> None:
    env = dict(scratch.env)
    if value is None:
        del env[var]
    elif value == "INSIDE":
        (scratch.repo / "pyc").mkdir()
        env[var] = str(scratch.repo / "pyc")
    elif value == "MISSING":
        env[var] = str(scratch.root / "nope")
    else:
        env[var] = value
    refused(lambda: scratch.gate(env=env), "env")


def test_gate_refuses_when_fetch_fails(scratch: Scratch) -> None:
    paths = dataclasses.replace(scratch.paths, url=str(scratch.root / "missing.git"))
    refused(
        lambda: g.gate(
            paths, "calibrate", "ctlA1", scratch.chain[0], None, scratch.env,
            gh=gh_ok, criteria_constant=scratch.crit,
        ),
        "fetch",
    )  # fmt: skip


def test_gate_reads_o_from_the_url_not_origin_v2(scratch: Scratch) -> None:
    # a hand-moved origin/v2 and a re-pointed remote change nothing
    sh(scratch.repo, "update-ref", "refs/remotes/origin/v2", scratch.base)
    sh(scratch.repo, "remote", "set-url", "origin", str(scratch.root / "elsewhere"))
    (scratch.repo / ".git" / "FETCH_HEAD").unlink(missing_ok=True)  # the helpers' pulls
    r = scratch.gate()
    assert r.o == sh(scratch.origin, "rev-parse", "v2")
    assert not (scratch.repo / ".git" / "FETCH_HEAD").exists()


def test_gate_refuses_h1_and_h2(scratch: Scratch) -> None:
    def h1_off(args: Sequence[str]) -> str:
        return "[true,false,true]" if "protection" in args[1] else "0"

    def h2_force(args: Sequence[str]) -> str:
        if "protection" in args[1]:
            return g.H1_EXPECTED
        return "1" if "force_push" in args[1] else "0"

    def h2_delete(args: Sequence[str]) -> str:
        if "protection" in args[1]:
            return g.H1_EXPECTED
        return "2" if "branch_deletion" in args[1] else "0"

    def gh_fails(args: Sequence[str]) -> str:
        raise g.GateError("gh", "stub: gh exited 1")

    refused(lambda: scratch.gate(gh=h1_off), "H1")
    refused(lambda: scratch.gate(gh=h2_force), "H2")
    refused(lambda: scratch.gate(gh=h2_delete), "H2")
    refused(lambda: scratch.gate(gh=gh_fails), "gh")
    seen: list[tuple[str, ...]] = []

    def record(args: Sequence[str]) -> str:
        seen.append(tuple(args))
        return gh_ok(args)

    scratch.gate(gh=record)
    assert seen[0] == g.H1_ARGS
    assert [a[1].split("activity_type=")[1].split("&")[0] for a in seen[1:]] == [
        "force_push",
        "branch_deletion",
    ]


def test_gate_c1_no_freeze(tmp_path: Path) -> None:
    s = Scratch(tmp_path)
    s.row("fsd-controls ceiling 7.6 CPU h")
    (s.d / "criteria.md").write_text("criteria\n")
    refused(lambda: s.gate(f="0" * 40), "C1")


def test_gate_c2_freeze_branched_from_before_f0(scratch: Scratch) -> None:
    """s18 S3: F-prime branched from before F0 and merged."""
    sh(scratch.repo, "checkout", "-q", "-b", "side", scratch.base)
    scratch.d.mkdir(parents=True, exist_ok=True)
    (scratch.d / "criteria.md").write_text("criteria\n")
    (scratch.d / "controls.py").write_text("# side\n")
    (scratch.d / "notes.md").write_text("notes\n")
    h1, h2 = scratch.crit, g.sha256_file(scratch.d / "controls.py")
    sh(scratch.repo, "add", "-f", REL)
    sh(
        scratch.repo,
        "commit",
        "-q",
        "-m",
        f"F'\n\ncriteria.md sha256: {h1}\ncontrols.py sha256: {h2}\n",
    )
    side = sh(scratch.repo, "rev-parse", "HEAD")
    sh(scratch.repo, "checkout", "-q", "v2")
    sh(scratch.repo, "merge", "-q", "--no-edit", "-X", "theirs", "side")
    sh(scratch.repo, "push", "-q", "origin", "v2")
    scratch.row(f"fsd-controls freeze {side} ceiling 7.6 CPU h")
    scratch.chain.append(side)
    refused(scratch.gate, "C2")


def test_gate_c3_a_controls_commit_without_a_row(scratch: Scratch) -> None:
    scratch.freeze("# unrecorded\n", row=None)
    refused(scratch.gate, "C3")
    # a later row naming it, even twice, repairs the chain (E3: duplicates fine)
    f = scratch.chain[-1]
    scratch.row(f"fsd-controls freeze {f} ceiling 7.6 CPU h")
    scratch.row(f"fsd-controls freeze {f} ceiling 7.6 CPU h")
    scratch.row(f"fsd-controls freeze {'e' * 40} ceiling 7.6 CPU h")  # another sha
    r = scratch.gate()
    assert len(dict(r.chain_rows)[f]) == 2
    assert r.other_rows == (f"fsd-controls freeze {'e' * 40} ceiling 7.6 CPU h",)


def test_gate_c4_f_must_be_the_newest(scratch: Scratch) -> None:
    f0 = scratch.chain[0]
    scratch.freeze("# F1\n")
    refused(lambda: scratch.gate(f=f0), "C4")
    scratch.gate()


def test_gate_c5_hashes(scratch: Scratch) -> None:
    (scratch.d / "controls.py").write_text("# edited\n")
    refused(scratch.gate, "C5")
    (scratch.d / "controls.py").write_text("# stub\n")
    refused(
        lambda: g.gate(
            scratch.paths, "calibrate", "ctlA1", scratch.chain[0], None, scratch.env,
            gh=gh_ok, criteria_constant="0" * 64,
        ),
        "C5",
    )  # fmt: skip


def test_gate_c6_run_output_in_f(tmp_path: Path) -> None:
    s = Scratch(tmp_path)
    s.freeze(extra={"STOP": "x"})
    refused(s.gate, "C6")


def test_gate_c7_content_in_f_o_head_and_the_working_tree(scratch: Scratch) -> None:
    (scratch.d / "notes.md").write_text("changed\n")
    refused(scratch.gate, "C7")  # the working tree
    sh(scratch.repo, "add", "-f", f"{REL}/notes.md")
    sh(scratch.repo, "commit", "-q", "-m", "local only")
    refused(scratch.gate, "C7")  # HEAD (O is still F's content)
    sh(scratch.repo, "push", "-q", "origin", "v2")
    sh(scratch.repo, "revert", "--no-edit", "HEAD")
    refused(scratch.gate, "C7")  # O
    sh(scratch.repo, "push", "-q", "origin", "v2")
    scratch.gate()  # changed and reverted: content equal again, accepted (§5)


@pytest.mark.parametrize("name", ["stray.txt", "D0-x.json.bak", "runs.jsonl"])
def test_gate_c8_refuses_a_non_run_output(scratch: Scratch, name: str) -> None:
    (scratch.d / name).write_text("x")
    refused(scratch.gate, "C8")


def test_gate_c8_refuses_an_untracked_directory(scratch: Scratch) -> None:
    (scratch.d / "sub").mkdir()
    (scratch.d / "sub" / "STOP").write_text("x")
    refused(scratch.gate, "C8")


def test_gate_c9_the_worktree(scratch: Scratch) -> None:
    paths = dataclasses.replace(scratch.paths, worktree_head="0" * 40)
    refused(
        lambda: g.gate(
            paths, "calibrate", "ctlA1", scratch.chain[0], None, scratch.env,
            gh=gh_ok, criteria_constant=scratch.crit,
        ),
        "C9",
    )  # fmt: skip
    (scratch.wt / "src" / "m.py").write_text("x = 2\n")
    refused(scratch.gate, "C9")
    (scratch.wt / "src" / "m.py").write_text("x = 1\n")
    (scratch.wt / "src" / "__pycache__").mkdir()
    (scratch.wt / "src" / "__pycache__" / "m.pyc").write_text("")
    scratch.gate()
    (scratch.wt / "src" / "new.py").write_text("")
    refused(scratch.gate, "C9")


def test_ceiling_is_read_from_rows(scratch: Scratch) -> None:
    assert scratch.gate().ceiling_h == 7.6
    scratch.row("fsd-controls ceiling 9.25 CPU h")
    scratch.row("fsd-controls ceiling 8 CPU h")
    assert scratch.gate().ceiling_h == 9.25  # the largest over rows ever added


def test_gate_refuses_without_a_ceiling_row(tmp_path: Path) -> None:
    s = Scratch(tmp_path)
    s.freeze(row="fsd-controls freeze {f}")
    refused(s.gate, "ceiling")


def test_args(scratch: Scratch) -> None:
    refused(lambda: scratch.gate(f=scratch.chain[0][:12]), "args")
    refused(lambda: scratch.gate("test", None), "args")
    refused(lambda: scratch.gate("calibrate", "1" * 40), "args")
    refused(lambda: scratch.gate(control="ctlX"), "args")


# --- D --------------------------------------------------------------------


def test_d_build_and_push_against_a_concurrent_pusher(scratch: Scratch) -> None:
    other = scratch.root / "other"
    subprocess.run(
        ["git", "clone", "-q", "-b", "v2", str(scratch.origin), str(other)],
        check=True,
        env={**os.environ, **GIT_ENV},
    )
    waits: list[float] = []

    def race(attempt: int) -> None:
        if attempt == 0:  # the lane pushes between our fetch and our push
            (other / "lane.txt").write_text("lane\n")
            sh(other, "add", "lane.txt")
            sh(other, "commit", "-q", "-m", "lane")
            sh(other, "push", "-q", "origin", "v2")

    d = scratch.push_d(before_push=race, sleep=waits.append)
    assert waits == [g.PUSH_WAIT_S]  # rejected once, rebuilt, pushed
    tip = sh(scratch.origin, "rev-parse", "v2")
    assert d == tip
    parents = sh(scratch.repo, "rev-list", "--parents", "-n", "1", d).split()
    assert len(parents) == 2 and parents[1] == sh(other, "rev-parse", "HEAD")
    assert (
        sh(scratch.repo, "show", "--name-only", "--format=", d)
        == f"{REL}/D0-ctlA1.json"
    )
    assert sh(scratch.repo, "log", "-1", "--format=%s", d) == g.d0_first_line(
        "ctlA1", scratch.chain[0]
    )
    assert sh(scratch.repo, "rev-parse", "HEAD") != d  # HEAD, index, tree untouched
    assert sh(scratch.repo, "status", "--porcelain") == ""
    scratch.gate("test", d)


def test_d_push_whose_reply_is_lost_still_counts(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = g.try_push

    def lost(paths: g.Paths, d: str, extra: dict[str, str]) -> bool:
        real(paths, d, extra)
        return False  # the server took it; the reply never came

    monkeypatch.setattr(g, "try_push", lost)
    waits: list[float] = []
    d = scratch.push_d(sleep=waits.append)
    assert waits == [] and d == sh(scratch.origin, "rev-parse", "v2")


@pytest.mark.slow
def test_d_push_gives_up_after_20_attempts(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(g, "try_push", lambda paths, d, extra: False)
    waits: list[float] = []
    with pytest.raises(g.GateError) as info:
        scratch.push_d(sleep=waits.append)
    assert info.value.check == "push" and len(waits) == 20
    assert not (scratch.d / "D0-ctlA1.json").exists()
    # s18b 1: 20 built Ds left no D0 blob in $R/.git/objects
    data = c.canonical({"records": scratch.gate().record(score=False)})
    blob = sh_in(scratch.repo, data, "hash-object", "--stdin")
    assert not has_object(scratch.repo, blob)


def test_calibrate_refuses_when_a_d_exists_and_names_it(scratch: Scratch) -> None:
    d = scratch.push_d()
    (scratch.d / "D0-ctlA1.json").unlink()  # pushed, but D0 never written
    with pytest.raises(g.GateError) as info:
        scratch.gate()
    assert info.value.check == "D-exists" and d in str(info.value)
    scratch.gate(control="ctlB")  # the other control is unaffected


def good_d0(scratch: Scratch) -> bytes:
    return c.canonical({"records": scratch.gate().record(score=False)}).encode()


def test_d_checks(scratch: Scratch) -> None:
    data = good_d0(scratch)
    rel = f"{REL}/D0-ctlA1.json"
    first = g.d0_first_line("ctlA1", scratch.chain[0])
    # a wrong first line
    bad_msg = scratch.manual_d({rel: data}, "D0 by hand")
    refused(lambda: scratch.gate("test", bad_msg), "D")
    # two candidates under F
    d2 = scratch.manual_d({rel: data + b" "}, first)
    refused(lambda: scratch.gate("test", d2), "D")


def test_d_checks_extra_file_and_bytes(tmp_path: Path) -> None:
    s = Scratch(tmp_path)
    s.freeze()
    rel = f"{REL}/D0-ctlA1.json"
    first = g.d0_first_line("ctlA1", s.chain[0])
    d = s.manual_d({rel: good_d0(s), "other.txt": b"x"}, first)
    refused(lambda: s.gate("test", d), "D")
    s2 = Scratch(tmp_path / "two")
    s2.freeze()
    d = s2.push_d()
    (s2.d / "D0-ctlA1.json").write_bytes(b"{}\n")
    refused(lambda: s2.gate("test", d), "D")  # disk bytes differ from D's blob
    (s2.d / "D0-ctlA1.json").unlink()
    refused(lambda: s2.gate("test", d), "D")  # missing on disk
    refused(lambda: s2.gate("test", s2.chain[0]), "D")  # F is not a candidate


def test_d_checks_environment(tmp_path: Path) -> None:
    s = Scratch(tmp_path)
    s.freeze()
    rec = s.gate().record(score=False) | {"numpy": "0.0"}
    data = c.canonical({"records": rec}).encode()
    d = s.manual_d({f"{REL}/D0-ctlA1.json": data}, g.d0_first_line("ctlA1", s.chain[0]))
    refused(lambda: s.gate("test", d), "environment")


# --- status: s18's S1-S3 and the S4-S5 restarts --------------------------


def test_status_s1_linear_refreeze_after_a_pushed_d(scratch: Scratch) -> None:
    d = scratch.push_d()
    (scratch.d / "D0-ctlA1.json").unlink()
    # a re-freeze that forgets to git rm the tracked D0 holds a run output (C6)
    scratch.freeze("# F1\n")
    refused(scratch.gate, "C6")
    # §5 Deviations: git rm the tracked D0 in a commit before F'
    sh(scratch.repo, "rm", "-q", "--cached", f"{REL}/D0-ctlA1.json")
    sh(scratch.repo, "commit", "-q", "-m", "drop D0 before F'")
    scratch.freeze("# F2\n")
    r = scratch.gate()
    assert r.status == "exploratory deviation"
    assert (d, False) in r.d0_history
    assert scratch.gate(control="ctlB").status == "preregistered"


def test_status_s2_refreeze_on_a_branch_and_merged(scratch: Scratch) -> None:
    f0 = scratch.chain[0]
    d = scratch.push_d()
    (scratch.d / "D0-ctlA1.json").unlink()
    sh(scratch.repo, "pull", "-q", "--rebase", "origin", "v2")
    sh(scratch.repo, "checkout", "-q", "-b", "fprime", f0)
    (scratch.d / "controls.py").write_text("# F'\n")
    h1, h2 = scratch.crit, g.sha256_file(scratch.d / "controls.py")
    sh(scratch.repo, "add", "-f", f"{REL}/controls.py")
    sh(
        scratch.repo,
        "commit",
        "-q",
        "-m",
        f"F'\n\ncriteria.md sha256: {h1}\ncontrols.py sha256: {h2}\n",
    )
    fp = sh(scratch.repo, "rev-parse", "HEAD")
    sh(scratch.repo, "checkout", "-q", "v2")
    sh(scratch.repo, "merge", "-q", "--no-ff", "--no-edit", "fprime")
    sh(scratch.repo, "push", "-q", "origin", "v2")
    scratch.row(f"fsd-controls freeze {fp} ceiling 7.6 CPU h")
    scratch.chain.append(fp)
    r = scratch.gate()  # the honest merge is accepted (content-based C7)
    assert r.chain == (f0, fp) and r.status == "exploratory deviation"
    assert (d, False) in r.d0_history


def test_status_s4_s5_killed_before_the_push(scratch: Scratch) -> None:
    # S4: nothing was pushed, so a rerun under the same F is preregistered
    assert scratch.gate().status == "preregistered"
    # S5: then a re-freeze: still preregistered
    scratch.freeze("# F1\n")
    assert scratch.gate().status == "preregistered"


def test_check_runtime_refuses_outside_python_i(scratch: Scratch) -> None:
    paths = g.Paths(c.REPO, c.DRAFTS_REL, c.WORKTREE, c.WORKTREE_HEAD)
    refused(
        lambda: g.check_runtime(scratch.env, HERE / "controls.py", paths), "runtime"
    )
    g.check_imports(c.WORKTREE)
    refused(lambda: g.check_imports(Path("/nonexistent")), "imports")
    assert g.sha256_file(HERE / "criteria.md") == c.CRITERIA_SHA256
    assert (c.VERSION, len(c.CRITERIA_SHA256)) == ("8.1", 64)


# --------------------------------------------------------------------------
# Keys: what test and calibrate draw is what score and the bootstrap read
# --------------------------------------------------------------------------


def test_draw_keys_match_the_score_side(token: g.GateToken) -> None:
    from sciagent.systems.v2.policies import PARAMETRIC_INDEX, REFERENCE_INDEX

    from sciagent.scoring.seeds import TELLING, policy_seed, scorer_seed

    for control in ("ctlA1", "ctlB"):
        rung = c.RUNGS[control][0]
        truth, member = rung.truth.build(), c.MEMBER.build()
        ref = c.REF_MODELS[0].build() if control == "ctlB" else None
        seed = c.SEEDS[control][0]
        todo = c.draw_todo(control, seed, truth, member, ref, DESIGNS, token=token)
        assert len(todo) == 8 * 2 * 32 + 8 * 8 * 2 * 16
        records = {}
        for key, model, design, seq in todo:
            k = (key["seed"], key["arm"], key["design"], key["side"], key["replicate"])
            records[k] = mk()
            # the streams of criteria §0, written out literally (s18b 2)
            if key["arm"] == "telling":
                side_i = {"truth": 0, "member": 1}[key["side"]]
                want = scorer_seed(
                    TELLING, control, seed, side_i, key["design"], key["replicate"]
                )
            else:
                index = 2**32 - 4 if key["side"] == "member" else 2**32 - 1
                assert (index == PARAMETRIC_INDEX) == (key["side"] == "member")
                assert (index == REFERENCE_INDEX) == (key["side"] != "member")
                want = policy_seed(seed, int(key["arm"][1:]), index, key["replicate"])
                if control == "ctlB" and key["side"] == "ref":
                    assert model is (ref if key["design"] == c.E1 else truth)
            got = seq()
            assert (got.entropy, got.spawn_key) == (want.entropy, want.spawn_key)
            assert design is DESIGNS[key["design"]]
        assert len(records) == len(todo)
        runs = c.seed_runs(records, control, seed)
        assert all(len(a) == 32 and len(b) == 32 for a, b in runs.telling)
        assert all(len(a) == 16 and len(b) == 16 for p in runs.decisions for a, b in p)


def test_cal_keys_match_the_bootstrap_side(token: g.GateToken) -> None:
    from sciagent.scoring.seeds import TELLING, scorer_seed

    assert TELLING == 5
    for j in (0, 3):
        got = c.boot_seed("ctlA1", j, token=token)
        want = scorer_seed(TELLING, f"ctlA1-boot-r{j}", 0)
        assert (got.entropy, got.spawn_key) == (want.entropy, want.spawn_key)
    for j, r in ((0, 0), (1, 255)):
        got = c.ref_seed(j, r, token=token)
        want = scorer_seed(TELLING, f"ctlB-ref-r{j}", 0, 0, 1, r)
        assert (got.entropy, got.spawn_key) == (want.entropy, want.spawn_key)
    rung = c.RUNGS["ctlB"][3]
    truth, member = rung.truth.build(), c.MEMBER.build()
    todo = c.cal_todo(rung, truth, member, DESIGNS, token=token)
    assert len(todo) == 2 * 8 * 2 * 256
    store = c.RunStore(None, c.CAL_KEY)
    for key, _, _, seq in todo:
        store.records[store.key(key)] = mk()
        want = (  # literal ids (s18b 2)
            scorer_seed(
                TELLING, "ctlB-cal-r3", 0, key["side"], key["design"], key["replicate"]
            )
            if key["set"] == "cal"
            else scorer_seed(TELLING, "ctlB-crn-r3", 0, key["side"], key["replicate"])
        )
        got = seq()
        assert (got.entropy, got.spawn_key) == (want.entropy, want.spawn_key)
    cal = c.cal_runs(store, 3)
    assert len(cal.per_design) == len(cal.crn) == 16
    assert all(len(v) == 256 for v in cal.crn.values())


# --------------------------------------------------------------------------
# Miniature end-to-end runs of the phase functions in a scratch repository
# (real simulator, tiny sizes, push to the bare origin; never the real $D)
# --------------------------------------------------------------------------


def miniature(monkeypatch: pytest.MonkeyPatch, forced: int | None = None) -> None:
    sizes = {"R_CAL": 4, "R_HALF": 2, "R_TELLING": 2, "R_POLICY": 1}
    sizes |= {"N_BOOT": 6, "DECISIONS": 2}
    for name, value in sizes.items():
        monkeypatch.setattr(c, name, value)
    monkeypatch.setattr(c, "SEEDS", {"ctlA1": (101, 102), "ctlB": (301, 302)})
    monkeypatch.setattr(c, "RUNGS", {k: v[:1] for k, v in c.RUNGS.items()})
    if forced is not None:  # force the result whatever the tiny sizes gave

        def result(rungs: Any, ref_ok: bool = True) -> tuple[int, int | None, str]:
            return forced, (0 if forced == 0 else None), f"forced {forced} by the test"

        monkeypatch.setattr(c, "calibration_result", result)


def pusher(s: Scratch, control: str) -> c.Pusher:
    def push(make: Callable[[g.GateResult], bytes]) -> tuple[str, bytes, g.GateResult]:
        return g.push_d0(
            s.paths, control, s.chain[-1], s.env, make,
            gh=gh_ok, criteria_constant=s.crit, sleep=lambda t: None,
        )  # fmt: skip

    return push


@pytest.mark.slow
def test_miniature_a1_killed_then_calibrated_tested_and_scored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    miniature(monkeypatch, forced=0)
    s = Scratch(tmp_path)
    s.freeze()
    (s.d / "cpu-total.txt").write_text("0.0\n")
    r = s.gate()
    ledger = c.Ledger(s.d, "calibrate", "ctlA1", r.ceiling_h * 3600, coarse=True)

    # 1. killed before its push: nothing in a file, stdout or stderr
    def killed(
        make: Callable[[g.GateResult], bytes],
    ) -> tuple[str, bytes, g.GateResult]:
        raise KeyboardInterrupt

    before = sorted(p.name for p in s.d.iterdir())
    with pytest.raises(KeyboardInterrupt):
        c.calibrate(s.paths, "ctlA1", killed, ledger, token=r.token)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    after = sorted(p.name for p in s.d.iterdir())
    assert set(after) - set(before) <= {"cpu-ledger.csv", "cpu-total.lock"}
    if (s.d / "cpu-ledger.csv").exists():
        units = [
            x.split(",")[2] for x in (s.d / "cpu-ledger.csv").read_text().splitlines()
        ]
        assert all(u.startswith("charge-") for u in units)
    assert sh(s.origin, "rev-parse", "v2") == r.o  # nothing pushed
    # 2. rerun under the same F (S4): calibrates, pushes D, then writes D0
    r = s.gate()
    assert r.status == "preregistered"
    code = c.calibrate(s.paths, "ctlA1", pusher(s, "ctlA1"), ledger, token=r.token)
    out = capsys.readouterr()
    d = sh(s.origin, "rev-parse", "v2")
    assert code == 0 and out.out == f"D {d}\n" and out.err == ""
    d0_path = s.d / "D0-ctlA1.json"
    data = d0_path.read_bytes()
    assert data == subprocess.run(
        ["git", "-C", str(s.repo), "cat-file", "blob", f"{d}:{REL}/D0-ctlA1.json"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    d0 = json.loads(data)
    assert d0["status"] == "preregistered" and d0["result"]["code"] == 0
    assert d0["version"] == "8.1" and "o" not in d0["records"]
    rung = d0["rungs"][0]
    assert rung["e_cal"] == [c.E1] and rung["p1"]  # T_A truncates on e1 only
    # 3. test, resumable; a header from another D refuses
    rt = s.gate("test", d)
    tledger = c.Ledger(s.d, "test", "ctlA1", rt.ceiling_h * 3600)
    assert c.draw_test(s.paths, "ctlA1", rt, tledger) == 0
    runs_path = s.d / "runs-ctlA1.jsonl"
    lines = runs_path.read_text().splitlines(keepends=True)
    assert json.loads(lines[0]) == {"control": "ctlA1", "F": s.chain[0], "D": d}
    assert len(lines) == 1 + 2 * (8 * 2 * 2 + 2 * 8 * 2 * 1)
    assert c.draw_test(s.paths, "ctlA1", rt, tledger) == 0
    assert runs_path.read_text().splitlines(keepends=True) == lines
    fake = dataclasses.replace(rt, d0_commit="1" * 40)
    with pytest.raises(c.HeaderError):
        c.draw_test(s.paths, "ctlA1", fake, tledger)
    # 4. score: 0 with every rule
    rs = s.gate("score", d)
    sledger = c.Ledger(s.d, "score", "ctlA1", 1.0, exempt=True)
    assert c.score_phase(s.paths, "ctlA1", rs, sledger) == 0
    score = json.loads((s.d / "score-ctlA1.json").read_text())
    assert score["result"]["code"] == 0 and set(score["rules"]) == set(c.K_RULES)
    assert score["determinism"]["ok"] and score["breaches"] == {}
    assert score["records"]["o"] == rs.o and score["records"]["d0_commit"] == d
    assert score["records"]["runs_sha256"] == g.sha256_file(runs_path)

    def edit(pred: Callable[[dict[str, Any]], bool], change: dict[str, Any]) -> None:
        out_lines = list(lines)
        for i, line in enumerate(out_lines[1:], start=1):
            rec = json.loads(line)
            if pred(rec):
                out_lines[i] = c.canonical(rec | change)
                break
        runs_path.write_text("".join(out_lines))

    # 5. a breach on seed 102 (more than BREACH_MAX_SEEDS = 0 here): 14, evidence only
    monkeypatch.setattr(c, "BREACH_MAX_SEEDS", 0)
    stop = {"outcome": "stopped", "values": None, "tau": None, "n_events": None}
    edit(lambda x: (x["seed"], x["design"], x["side"]) == (102, 0, "truth"), stop)
    assert c.score_phase(s.paths, "ctlA1", rs, sledger) == c.EXIT_BREACH
    score = json.loads((s.d / "score-ctlA1.json").read_text())
    assert "rules" not in score and list(score["breaches"]) == ["102"]
    # 6. a corrupted seed-101 e1 line and the breach: 15 beats 14, evidence only
    text = runs_path.read_text().splitlines(keepends=True)
    for i, line in enumerate(text[1:], start=1):
        rec = json.loads(line)
        if (rec["seed"], rec["design"], rec["side"]) == (101, c.E1, "member"):
            text[i] = c.canonical(rec | {"n_events": rec["n_events"] + 1})
            break
    runs_path.write_text("".join(text))
    assert c.score_phase(s.paths, "ctlA1", rs, sledger) == c.EXIT_NONDET
    score = json.loads((s.d / "score-ctlA1.json").read_text())
    assert "rules" not in score and "breaches" not in score
    assert len(score["determinism"]["mismatches"]) == 1
    assert "nondeterministic" in (s.d / "score-ctlA1.txt").read_text()
    # 7. an unfinished test, or none at all: 11
    runs_path.write_text("".join(lines[:-1]))
    assert c.score_phase(s.paths, "ctlA1", rs, sledger) == c.EXIT_STOP
    runs_path.unlink()
    assert c.score_phase(s.paths, "ctlA1", rs, sledger) == c.EXIT_STOP
    assert "INCOMPLETE" in (s.d / "score-ctlA1.txt").read_text()
    # 8. calibrate again under F refuses: D exists
    refused(s.gate, "D-exists")


@pytest.mark.slow
def test_miniature_b_not_tested_exits_13_everywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    miniature(monkeypatch, forced=13)
    s = Scratch(tmp_path)
    s.freeze()
    r = s.gate(control="ctlB")
    code = c.calibrate(
        s.paths, "ctlB", pusher(s, "ctlB"), c.NullLedger(), token=r.token
    )
    assert code == 13
    d = sh(s.origin, "rev-parse", "v2")
    d0 = json.loads((s.d / "D0-ctlB.json").read_text())
    assert d0["ref_check"] == {"used": 0, "truncated_counts": [4], "of": 4}
    rung = d0["rungs"][0]
    assert rung["e_cal"] == [c.E2] and rung["p1"]
    assert {"h_f", "pass_iii_f", "p_f_counts", "p3_b"} <= set(rung)
    rt = s.gate("test", d, control="ctlB")
    assert c.draw_test(s.paths, "ctlB", rt, c.NullLedger()) == 13
    assert not (s.d / "runs-ctlB.jsonl").exists()  # drew nothing
    assert c.score_phase(s.paths, "ctlB", rt, c.NullLedger()) == 13
    score = json.loads((s.d / "score-ctlB.json").read_text())
    assert score["result"]["code"] == 13 and score["rungs"] == d0["rungs"]


# --------------------------------------------------------------------------
# s18b: boundary tests, the plant on e1, and the gate mutants it listed
# --------------------------------------------------------------------------


def sh_in(repo: Path, stdin: str, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ENV},
    )
    return done.stdout.strip()


def has_object(repo: Path, sha: str) -> bool:
    done = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", sha],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


SIGN4 = ["mean_rate", "a[sign]", "b[sign]", "c[sign]"]


def boundary_values(
    d0_counts: dict[str, int], p32_counts: dict[str, int], clause: int
) -> dict[str, Any]:
    rung = c.RUNGS["ctlA1"][0]
    boot = c.Bootstrap(d0_counts, p32_counts, {"a[sign]": 1000}, None, clause)
    randoms = {s: ("mean_rate",) * 8 for s in c.SEEDS["ctlA1"]}
    return c.rung_values(rung, cal_from(c.E1, n=4), boot, SIGN4, randoms)


def test_boundaries_coverage_clause_and_d_star0_size() -> None:
    assert c.d_star0({"a[sign]": 949}, SIGN4) == (["a[sign]"], False)
    assert c.d_star0({"a[sign]": 950}, SIGN4) == (["a[sign]"], True)
    ok = boundary_values({"a[sign]": 1000}, {"a[sign]": 950}, 990)
    assert ok["p2"] and ok["clause99"] and ok["reading"] != "unbuildable"
    held = boundary_values({"a[sign]": 1000}, {"a[sign]": 949, "b[sign]": 51}, 990)
    assert not held["p2"] and held["reading"] == "unbuildable"  # coverage 949
    clause = boundary_values({"a[sign]": 1000}, {"a[sign]": 1000}, 989)
    assert not clause["clause99"] and clause["reading"] == "unbuildable"
    two = boundary_values(
        {"a[sign]": 500, "b[sign]": 450, "c[sign]": 50}, {"a[sign]": 1000}, 990
    )
    assert two["d_star0"] == ["a[sign]", "b[sign]"] and two["p2"]
    three = boundary_values(
        {"a[sign]": 400, "b[sign]": 300, "c[sign]": 300}, {"a[sign]": 1000}, 990
    )
    assert len(three["d_star0"]) == 3 and not three["p2_parts"]["size"]
    assert three["reading"] == "unbuildable"


def decision_mix(
    labels: list[str], n_match: int, seeds: Sequence[int], tell_planted: int = 15
) -> dict[int, c.SeedRuns]:
    """Telling planted on the first ``tell_planted`` seeds; the first
    ``n_match`` (seed, decision) pairs see the plant, the rest see nothing."""
    hot = wide_pairs(labels, 8)
    flat_tell = wide_pairs(labels, 8, plant=False)
    match = [(b, a) for a, b in wide_pairs(labels, 4)]
    miss = [(b, a) for a, b in wide_pairs(labels, 4, plant=False)]
    out: dict[int, c.SeedRuns] = {}
    i = 0
    for n, s in enumerate(seeds):
        decisions = []
        for _ in range(c.DECISIONS):
            decisions.append(match if i < n_match else miss)
            i += 1
        out[s] = c.SeedRuns(hot if n < tell_planted else flat_tell, decisions)
    return out


@pytest.mark.slow
def test_boundaries_a1_i_and_ii(menu_labels: list[str]) -> None:
    seeds = c.SEEDS["ctlA1"]
    menus = real_menus(menu_labels, seeds)
    for n_match, ok in ((84, True), (83, False)):
        out = c.score_a1(
            "ctlA1", decision_mix(menu_labels, n_match, seeds), menus, [PLANT]
        )
        assert out["K1"]["ii"] == {"count": n_match, "pass": ok}
    for n_tell, ok in ((12, True), (11, False)):
        runs = decision_mix(menu_labels, 120, seeds, tell_planted=n_tell)
        out = c.score_a1("ctlA1", runs, menus, [PLANT])
        assert out["K1"]["i"] == {"count": n_tell, "pass": ok}


@pytest.mark.slow
def test_boundaries_b1(menu_labels: list[str]) -> None:
    seeds = c.SEEDS["ctlB"]
    d = menu_labels.index(PLANT)
    base = [(b, a) for a, b in wide_pairs(menu_labels, 4)]
    artefact = list(base)
    hot = [9.0 if i == d else 1.0 for i in range(len(menu_labels))]
    artefact[c.E1] = (side(4, [1.0] * len(menu_labels)), side(4, hot, {0: "truncated"}))
    menus = real_menus(menu_labels, seeds)
    for n_off, ok in ((108, True), (107, False)):
        runs: dict[int, c.SeedRuns] = {}
        i = 0
        for s in seeds:
            decisions = []
            for _ in range(c.DECISIONS):
                decisions.append(base if i < n_off else artefact)
                i += 1
            runs[s] = c.SeedRuns(wide_pairs(menu_labels, 8), decisions)
        out = c.score_b("ctlB", runs, menus, [PLANT])
        assert out["pairs"]["Fc|K1"]["B1"] == {"off_e1": n_off, "pass": ok}
        assert out["pairs"]["Fx|K1"]["B1"]["off_e1"] == 120  # Fx excludes e1


def test_p_f_excludes_e1_with_the_plant_on_e1(token: g.GateToken) -> None:
    rung = c.RUNGS["ctlB"][0]
    cal = cal_from(c.E2, plant=c.E1)
    boot = c.bootstrap_rung(
        rung, cal, LABELS, DESIGNS, DIAGS, c.NullLedger(), token=token, n_boot=10
    )
    assert boot.p16_counts == {"x[sign]": 10}  # truthFSD sees the plant on e1
    assert boot.pf_counts == {"mean_rate": 10}  # Fx never sees e1


def test_check_runtime_requires_the_isolated_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = g.Paths(c.REPO, c.DRAFTS_REL, c.WORKTREE, c.WORKTREE_HEAD)
    prefix = sys.pycache_prefix
    assert prefix is not None  # the tests run with PYTHONPYCACHEPREFIX set
    env = {v: "1" for v in g.BLAS_VARS} | {"PYTHONPYCACHEPREFIX": prefix}

    class Flags:
        def __init__(self, isolated: int) -> None:
            self.isolated = isolated

    monkeypatch.setattr(sys, "flags", Flags(0))
    refused(lambda: g.check_runtime(env, HERE / "controls.py", paths), "runtime")
    monkeypatch.setattr(sys, "flags", Flags(1))
    g.check_runtime(env, HERE / "controls.py", paths)  # everything else holds
    refused(lambda: g.check_runtime(env, HERE / "ctl_det.py", paths), "runtime")


def test_d_with_two_parents_is_refused(scratch: Scratch) -> None:
    """A merge carrying D0 is never a D-candidate (--no-merges), so the D
    check refuses it. (The single-parent check is then redundant: a mutant
    loosening it alone is equivalent.)"""
    f = scratch.chain[0]
    sh(scratch.repo, "checkout", "-q", "-b", "side")
    (scratch.repo / "side.txt").write_text("x")
    sh(scratch.repo, "add", "side.txt")
    sh(scratch.repo, "commit", "-q", "-m", "side")
    sh(scratch.repo, "checkout", "-q", "v2")
    (scratch.repo / "main.txt").write_text("y")
    sh(scratch.repo, "add", "main.txt")
    sh(scratch.repo, "commit", "-q", "-m", "main")
    sh(scratch.repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    (scratch.d / "D0-ctlA1.json").write_bytes(good_d0(scratch))
    sh(scratch.repo, "add", "-f", f"{REL}/D0-ctlA1.json")
    sh(scratch.repo, "commit", "-q", "-m", g.d0_first_line("ctlA1", f))
    merge = sh(scratch.repo, "rev-parse", "HEAD")
    sh(scratch.repo, "push", "-q", "origin", "v2")
    assert len(sh(scratch.repo, "rev-list", "--parents", "-n", "1", merge).split()) == 3
    refused(lambda: scratch.gate("test", merge), "D")
    assert not g.d_candidates(scratch.repo, merge, f, f"{REL}/D0-ctlA1.json")


def test_a_commit_deleting_d0_is_not_a_d_candidate(scratch: Scratch) -> None:
    """The "tree holds D0" condition: the git rm before a re-freeze touches
    D0-<C>.json under F, but is no D-candidate."""
    d = scratch.push_d()
    sh(scratch.repo, "pull", "-q", "--rebase", "origin", "v2")
    sh(scratch.repo, "rm", "-q", "--cached", f"{REL}/D0-ctlA1.json")
    sh(scratch.repo, "commit", "-q", "-m", "drop D0")
    sh(scratch.repo, "push", "-q", "origin", "v2")
    r = scratch.gate("test", d)  # candidates are exactly {D}
    assert r.d0_commit == d and len(r.d0_history) == 2


def test_status_walk_uses_full_history(scratch: Scratch) -> None:
    """A D0 commit on a side branch merged with -s ours (the merge keeps
    v2's tree) is hidden from rev-list without --full-history; it counts."""
    f0 = scratch.chain[0]
    sh(scratch.repo, "checkout", "-q", "-b", "hidden", f0)
    (scratch.d / "D0-ctlA1.json").write_bytes(good_d0(scratch))
    sh(scratch.repo, "add", "-f", f"{REL}/D0-ctlA1.json")
    sh(scratch.repo, "commit", "-q", "-m", g.d0_first_line("ctlA1", f0))
    x = sh(scratch.repo, "rev-parse", "HEAD")
    sh(scratch.repo, "checkout", "-q", "v2")
    sh(scratch.repo, "merge", "-q", "-s", "ours", "--no-edit", "hidden")
    sh(scratch.repo, "push", "-q", "origin", "v2")
    (scratch.d / "D0-ctlA1.json").unlink(missing_ok=True)
    scratch.freeze("# F1\n")
    o = sh(scratch.origin, "rev-parse", "v2")
    simplified = sh(scratch.repo, "rev-list", o, "--", f"{REL}/D0-ctlA1.json")
    assert x not in simplified.split()  # what a walk without --full-history sees
    r = scratch.gate()
    assert (x, False) in r.d0_history and r.status == "exploratory deviation"


def test_c8_refuses_an_untracked_unignored_entry(scratch: Scratch) -> None:
    """`??` is never accepted, even for a name matching a run-output pattern."""
    (scratch.repo / ".gitignore").write_text(
        ".cache/*\n!.cache/drafts/\n.cache/drafts/*\n!.cache/drafts/fsd-controls/\n"
        ".cache/drafts/fsd-controls/*\n!.cache/drafts/fsd-controls/D0-x.json\n"
    )
    scratch.gate()  # the tracked files are unaffected
    (scratch.d / "D0-x.json").write_text("{}")
    status = sh(scratch.repo, "status", "--porcelain", "--ignored", "--", REL)
    assert f"?? {REL}/D0-x.json" in status.splitlines()
    refused(scratch.gate, "C8")


def test_gate_ignores_git_environment_and_replace_refs(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = scratch.chain[0]
    # a replacement of F whose message lacks the hashes: C5 would refuse it
    tree = sh(scratch.repo, "rev-parse", f"{f}^{{tree}}")
    parent = sh(scratch.repo, "rev-parse", f"{f}^")
    fake = sh(scratch.repo, "commit-tree", tree, "-p", parent, "-m", "no hashes")
    sh(scratch.repo, "replace", f, fake)
    assert "sha256" not in sh(scratch.repo, "log", "-1", "--format=%B", f)
    paths = scratch.paths
    for var, value in (
        ("GIT_DIR", str(scratch.root / "nowhere")),
        ("GIT_INDEX_FILE", str(scratch.root / "bogus-index")),
        ("GIT_OBJECT_DIRECTORY", str(scratch.root / "bogus-objects")),
        ("GIT_NO_REPLACE_OBJECTS", "0"),
    ):
        monkeypatch.setenv(var, value)
    r = g.gate(
        paths, "calibrate", "ctlA1", f, None, scratch.env,
        gh=gh_ok, criteria_constant=scratch.crit,
    )  # fmt: skip
    assert r.freeze == f
    env = g.git_env({"X": "1"})
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1" and "GIT_DIR" not in env


def test_os_errors_and_missing_files_refuse_with_10(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scratch.wt / "uv.lock").unlink()
    refused(scratch.gate, "C9")
    (scratch.wt / "uv.lock").write_text("lock\n")
    paths, crit = scratch.paths, scratch.crit
    (scratch.d / "criteria.md").unlink()
    refused(
        lambda: g.gate(
            paths, "calibrate", "ctlA1", scratch.chain[0], None, scratch.env,
            gh=gh_ok, criteria_constant=crit,
        ),
        "C5",
    )  # fmt: skip
    (scratch.d / "criteria.md").write_text("criteria\n")

    def no_git(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    refused(lambda: g.git(paths.repo, "status"), "git")
    refused(lambda: g.blob_bytes(paths.repo, "HEAD:x"), "git")
    refused(lambda: g.try_push(paths, "0" * 40, {}), "push")


def test_d_push_leaves_no_d0_object_in_r_before_it_lands(scratch: Scratch) -> None:
    seen: list[bool] = []
    data = c.canonical({"records": scratch.gate().record(score=False)})
    blob = sh_in(scratch.repo, data, "hash-object", "--stdin")

    def check(attempt: int) -> None:  # just before the push
        seen.append(has_object(scratch.repo, blob))

    scratch.push_d(before_push=check)
    assert seen == [False]  # D0's bytes were not in $R/.git/objects
    assert has_object(scratch.repo, blob)  # the step-4 fetch brought D back
