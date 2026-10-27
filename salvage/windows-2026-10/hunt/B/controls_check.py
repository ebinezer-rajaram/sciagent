"""Defect hunt B, property 8: the §6.4 control code on hand-built inputs.

uv run python .cache/hunt/B/controls_check.py
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from fractions import Fraction

import numpy as np

from sciagent.campaign import analysis
from sciagent.scoring import gonogo, stats
from sciagent.scoring.errors import ScoringError

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    RESULTS.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""))


def brute_signed_rank(d: list[float]) -> float:
    """Exact one-sided p by enumerating all sign flips, average ranks for ties."""
    d = [x for x in d if x != 0.0]
    if not d:
        return 1.0
    mags = [abs(x) for x in d]
    order = sorted(range(len(d)), key=lambda i: mags[i])
    ranks = [0.0] * len(d)
    i = 0
    while i < len(d):
        j = i
        while j + 1 < len(d) and mags[order[j + 1]] == mags[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + 1 + j + 1) / 2
        i = j + 1
    obs = sum(r for r, x in zip(ranks, d) if x > 0)
    tail = sum(
        1
        for signs in itertools.product((0, 1), repeat=len(d))
        if sum(r for r, s in zip(ranks, signs) if s) >= obs - 1e-12
    )
    return float(Fraction(tail, 2 ** len(d)))


def row(truth, seed, system, per_event, ok=True, underfit=None, llm=False):
    return analysis.Row(
        truth=truth,
        seed=seed,
        system=system,
        llm=llm,
        ok=ok,
        submitted=ok,
        per_event=per_event if ok else None,
        exact=False,
        distance=None,
        has_structure=True,
        similarity=None,
        fits_used=1,
        structure=None,
        curve=(),
        error=None if ok else "X",
        oracle_underfit=underfit,
    )


def main() -> None:
    # ---- control 1: underfit -----------------------------------------
    u = gonogo.oracle_underfit_check([("a", 1, False), ("a", 2, False)])
    check("c1 all False -> pass", u.passed)
    u = gonogo.oracle_underfit_check([("a", 1, False), ("a", 2, True)])
    check("c1 one True -> fail", not u.passed and u.flagged == (("a", 2),))
    u = gonogo.oracle_underfit_check([("a", 1, False), ("a", 2, None)])
    check("c1 one missing (None) -> fail", not u.passed and u.unrecorded == (("a", 2),))
    u = gonogo.oracle_underfit_check([])
    check("c1 no ORACLE units -> fail", not u.passed)
    # analysis path: missing key in a cell detail -> None -> fail
    cell = {
        "reading": {"status": (1.0).hex(), "per_event": (-1.0).hex()},
        "detail": {"result": {"structure": "x"}},
    }
    r = analysis.row_from_cell("a", 1, "ORACLE", False, cell)
    check("c1 cell without oracle_underfit reads as None", r.oracle_underfit is None)
    uc = analysis.underfit_check([r])
    check("c1 analysis: missing flag fails", not uc.passed)
    # a FAILED ORACLE unit (ok=False) next to clean ones
    rows = [
        row("a", 1, "ORACLE", -1.0, underfit=False),
        row("a", 2, "ORACLE", None, ok=False),
    ]
    uc = analysis.underfit_check(rows)
    check(
        "c1 analysis: failed ORACLE unit counted as unrecorded (not silently skipped)",
        not uc.passed,
        f"passed={uc.passed} checked={uc.checked} unrecorded={uc.unrecorded}",
    )

    # ---- control 2: Wilcoxon + median CI ---------------------------------
    cases = [
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [1.0, 2.0, 3.0, 4.0],
        [1.0, -2.0, 3.0, 4.0, 5.0, 6.0],
        [1.0, 1.0, -1.0, 2.0, 2.0, 0.0, 3.0],
        [math.inf, -1.0, 2.0, 3.0, -math.inf, 4.0, 5.0],
        [-math.inf, -math.inf, 1.0],
        [0.0, 0.0],
        [0.5, -0.5, 0.5, -0.5, 1.0, 2.0, 2.0, -3.0],
    ]
    for d in cases:
        p = stats.signed_rank_greater(d)
        b = brute_signed_rank(d)
        check(f"c2 wilcoxon {d}", p == b, f"code {p} brute {b}")
    try:
        from scipy.stats import wilcoxon

        d = [0.3, -0.1, 0.8, 1.2, 0.05, 0.6, -0.4, 0.9]
        sp = wilcoxon(d, alternative="greater", method="exact").pvalue
        check(
            "c2 wilcoxon vs scipy exact (no ties)",
            abs(sp - stats.signed_rank_greater(d)) < 1e-12,
            f"scipy {sp} code {stats.signed_rank_greater(d)}",
        )
    except ImportError:
        pass
    # -inf ranks lowest in the median and CI
    rng = np.random.default_rng(1)
    ci = stats.bootstrap_median_ci([-math.inf, 1.0, 2.0, 3.0, 4.0], rng, resamples=2000)
    check("c2 order median with -inf", ci.median == 2.0, f"{ci}")
    check("c2 CI never nan", not (math.isnan(ci.low) or math.isnan(ci.high)))
    ci = stats.bootstrap_median_ci(
        [-math.inf, -math.inf, -math.inf, 1.0], np.random.default_rng(1), resamples=500
    )
    check(
        "c2 mostly -inf: median -inf, low -inf",
        ci.median == -math.inf and ci.low == -math.inf,
        f"{ci}",
    )
    check(
        "c2 order_median(-inf,+inf) = 0",
        stats.order_median([-math.inf, math.inf]) == 0.0,
    )

    def sr(t, s, sys_, gap):
        return gonogo.ScoreRow(t, s, sys_, gap, None, False, True)

    def planted_rows(diffs_by_truth):
        out = []
        for t, (pg, rg) in diffs_by_truth.items():
            for s, (a, b) in enumerate(zip(pg, rg), 1):
                out += [sr(t, s, gonogo.PLANTED, a), sr(t, s, gonogo.B_RAND, b)]
        return out

    five = {
        f"t{i}": ([-0.01, -0.01], [-0.1 * (i + 1), -0.1 * (i + 1)]) for i in range(5)
    }
    pc = gonogo.planted_beats_rand(
        planted_rows(five), sorted(five), rng=np.random.default_rng(0), resamples=2000
    )
    check(
        "c2 5/5 wins -> pass (p=1/32)",
        pc.passed and pc.p_value == 1 / 32,
        f"p={pc.p_value} ci={pc.median_ci}",
    )
    four = dict(list(five.items())[:4])
    pc = gonogo.planted_beats_rand(
        planted_rows(four), sorted(four), rng=np.random.default_rng(0), resamples=2000
    )
    check("c2 4/4 wins -> fail (p=1/16 >= .05)", not pc.passed and pc.p_value == 1 / 16)
    # B-rand -inf on one seed: rand mean -inf -> planted wins that truth with +inf
    inf_rand = {**five, "t0": ([-0.01, -0.01], [-0.2, -math.inf])}
    pc = gonogo.planted_beats_rand(
        planted_rows(inf_rand),
        sorted(inf_rand),
        rng=np.random.default_rng(0),
        resamples=2000,
    )
    check(
        "c2 rand -inf seed -> +inf win, ranks highest",
        pc.differences[0] == math.inf and pc.passed,
        f"d={pc.differences} n_inf={pc.n_infinite} mean_finite={pc.mean_finite}",
    )
    # planted -inf -> -inf loss
    inf_pl = {**five, "t0": ([-math.inf, -0.01], [-0.2, -0.2])}
    pc = gonogo.planted_beats_rand(
        planted_rows(inf_pl),
        sorted(inf_pl),
        rng=np.random.default_rng(0),
        resamples=2000,
    )
    check(
        "c2 planted -inf -> -inf loss",
        pc.differences[0] == -math.inf and pc.losses == 1,
        f"p={pc.p_value} passed={pc.passed}",
    )
    both = {**five, "t0": ([-math.inf, -0.01], [-math.inf, -0.2])}
    pc = gonogo.planted_beats_rand(
        planted_rows(both), sorted(both), rng=np.random.default_rng(0), resamples=2000
    )
    check("c2 both -inf -> tie 0", pc.differences[0] == 0.0 and pc.ties == 1)
    # Wilcoxon passes but CI lower end <= 0 -> fail
    mixed = {
        f"t{i}": ([v], [0.0])
        for i, v in enumerate([5, 4, 3, 2, 1, 0.5, -0.01, -0.02, 6, 7, 8, 9])
    }
    mixed = {k: ([a[0], a[0]], [b[0], b[0]]) for k, (a, b) in mixed.items()}
    pc = gonogo.planted_beats_rand(
        planted_rows(mixed), sorted(mixed), rng=np.random.default_rng(0), resamples=2000
    )
    check(
        "c2 result requires both tests (consistency)",
        pc.passed == (pc.p_value < 0.05 and pc.median_ci.low > 0),
        f"p={pc.p_value:.4g} ci_low={pc.median_ci.low}",
    )
    # rng determinism
    a = gonogo.planted_beats_rand(
        planted_rows(five), sorted(five), rng=np.random.default_rng(7), resamples=999
    )
    b = gonogo.planted_beats_rand(
        planted_rows(five), sorted(five), rng=np.random.default_rng(7), resamples=999
    )
    check("c2 deterministic under same rng", a == b)

    # ---- gap closed noise flag (T4) ---------------------------------------
    # truth T: ORACLE - B-lib per seed d = [0.001, 0.1, 0.1] -> noise = sd/sqrt(3)
    lib = [-1.0, -1.0, -1.0]
    ora = [-0.999, -0.9, -0.9]
    rows = []
    for s in (1, 2, 3):
        rows += [
            row("T", s, "ORACLE", ora[s - 1], underfit=False),
            row("T", s, "B-lib", lib[s - 1]),
            row("T", s, "B-sym", -0.95),
        ]
    d = np.array([o - l for o, l in zip(ora, lib)])
    noise = float(np.std(d, ddof=1) / math.sqrt(3))
    out = {(r.seed, r.system): r for r in analysis.derive(rows)}
    check(
        "T4 seed1 |d|<=noise -> flagged",
        out[(1, "B-sym")].closed_flagged,
        f"d1={d[0]:.4f} noise={noise:.4f} closed={out[(1, 'B-sym')].gap_closed}",
    )
    check("T4 seed2 |d|>noise -> not flagged", not out[(2, "B-sym")].closed_flagged)
    m = analysis.summarise(list(out.values()), "B-sym")
    check(
        "T4 flagged ratio excluded from median closed",
        m.closed_median == out[(2, "B-sym")].gap_closed and m.closed_flagged == 1,
        f"median={m.closed_median} flagged={m.closed_flagged}",
    )
    # one seed only: noise not estimable -> no flag
    one = [r for r in rows if r.seed == 1]
    check(
        "T4 single seed -> never flagged",
        not any(r.closed_flagged for r in analysis.derive(one)),
    )
    # B-lib -inf on one seed (gonogo docstring anticipates it)
    rows_inf = [
        replace(r, per_event=-math.inf) if (r.seed == 3 and r.system == "B-lib") else r
        for r in rows
    ]
    try:
        analysis.derive(rows_inf)
        check("derive survives a -inf B-lib seed", True)
    except ScoringError as e:
        check("derive survives a -inf B-lib seed", False, f"ScoringError: {e}")
    rows_inf = [
        replace(r, per_event=-math.inf) if (r.seed == 3 and r.system == "ORACLE") else r
        for r in rows
    ]
    try:
        out2 = analysis.derive(rows_inf)
        g = [r.gap for r in out2 if r.seed == 3 and r.system == "B-sym"][0]
        check("derive survives a -inf ORACLE seed", True, f"B-sym gap there = {g}")
    except ScoringError as e:
        check("derive survives a -inf ORACLE seed", False, f"ScoringError: {e}")

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} pass; failing: {bad}")


if __name__ == "__main__":
    main()
