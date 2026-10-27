"""The pilot read (SPEC §4.3 derived scores, §6.4 controls, §7.1 verdict)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from campaign_support import entry

from sciagent.campaign.analysis import (
    Row,
    Table,
    derive,
    efficiency,
    gap_at,
    truth_share,
    verdict,
)


def row(
    truth: str,
    seed: int,
    system: str,
    per_event: float | None,
    *,
    exact: bool = False,
    ok: bool = True,
    curve: tuple[tuple[int, float], ...] = (),
    llm: bool = False,
) -> Row:
    return Row(
        truth=truth,
        seed=seed,
        system=system,
        llm=llm,
        ok=ok,
        submitted=ok,
        per_event=per_event if ok else None,
        exact=exact,
        distance=0.0 if exact else 0.5,
        has_structure=True,
        similarity=0.9,
        fits_used=len(curve),
        structure=None,
        curve=curve,
        error=None if ok else "SciAgentError",
        oracle_underfit=False if system == "ORACLE" else None,
    )


def test_gaps_are_against_the_oracle_and_blib_of_the_same_truth_and_seed() -> None:
    rows = derive(
        [
            row("a", 1, "ORACLE", -0.70),
            row("a", 1, "B-lib", -0.75),
            row("a", 2, "ORACLE", -0.60),
            row("a", 1, "B-sym", -0.72, curve=((1, -0.75), (3, -0.72))),
            row("a", 2, "B-sym", -0.62),
        ]
    )
    sym1 = next(r for r in rows if r.system == "B-sym" and r.seed == 1)
    sym2 = next(r for r in rows if r.system == "B-sym" and r.seed == 2)
    assert sym1.gap is not None and abs(sym1.gap - (-0.02)) < 1e-12
    assert sym1.gap_closed is not None and abs(sym1.gap_closed - 0.6) < 1e-12
    assert sym1.curve_gaps[1][0] == 3 and abs(sym1.curve_gaps[1][1] + 0.02) < 1e-12
    assert sym2.gap is not None and sym2.gap_closed is None  # no B-lib on seed 2
    assert gap_at(sym1.curve_gaps, 2) == sym1.curve_gaps[0][1]
    assert gap_at(sym1.curve_gaps, 0) is None


def test_shares_weight_truths_equally_and_failures_never_count() -> None:
    rows = [
        row("a", 1, "X", -0.7, exact=True),
        row("a", 2, "X", -0.7, exact=True),
        row("b", 1, "X", -0.7, exact=False),
        row("c", 1, "X", None, ok=False, exact=True),
    ]
    share, n = truth_share(rows, "recovers")
    assert n == 3 and share is not None and abs(share - 1 / 3) < 1e-12


#: Five truths: the smallest exact one-sided signed-rank p is 2**-n, so
#: control 2 cannot pass on fewer than 5 truths (1/32 < 0.05).
NAMES = ("hawkes", "size_excitation", "null", "t4", "t5")


def _table(planted: float) -> Table:
    rows: list[Row] = []
    for t in NAMES:
        for seed in (1, 2, 3):
            noise = 0.001 * seed
            base = -0.7 - noise
            rows += [
                row(t, seed, "ORACLE", base, exact=True, curve=((1, base),)),
                row(t, seed, "B-lib", base - 0.05),
                row(t, seed, "B-np", base - 0.03),
                row(t, seed, "B-rand", base - 0.04, curve=((1, base - 0.06),)),
                row(t, seed, "B-sym", base - 0.01),
                row(t, seed, "B-sparse", base - 0.02),
                row(t, seed, "planted-hint", base + planted, exact=planted == 0.0),
            ]
            if seed == 1:
                rows.append(row(t, seed, "B-sym@10F", base - 0.002))
            rows.append(row(t, seed, "AG-c-S/named", base - 0.01, llm=True))
    truths = {t: entry("hawkes") for t in NAMES}
    return Table(truths, tuple(derive(rows)), (), {})


def test_verdict_is_go_when_controls_pass_and_difficulty_is_in_band() -> None:
    v = verdict(_table(0.0), np.random.default_rng(0), resamples=500)
    assert v.outcome == "go", v.reasons
    assert v.controls_pass and len(v.kept) == 5 and not v.dropped
    assert v.bsym is not None and v.bsym.passed
    assert v.recovery["planted-hint"] == 1.0 and v.recovery["B-lib"] == 0.0
    assert v.close["planted-hint"] == 1.0
    assert "AG-c-S/named" not in v.recovery


def test_verdict_fails_the_controls_when_the_planted_hint_does_not_beat_rand() -> None:
    v = verdict(_table(-0.05), np.random.default_rng(0), resamples=500)
    assert v.outcome == "controls fail"
    assert v.planted is not None and not v.planted.passed


def test_efficiency_reads_the_median_best_so_far_gap() -> None:
    rows = derive(
        [
            row("a", 1, "ORACLE", -0.7),
            row("a", 1, "S", -0.7, curve=((1, -0.8), (5, -0.72))),
            row("a", 2, "ORACLE", -0.7),
            row("a", 2, "S", -0.7, curve=((2, -0.9),)),
        ]
    )
    table = dict((p, (m, n)) for p, m, n in efficiency(rows, "S", (1, 5)))
    m1, n1 = table[1]
    m5, n5 = table[5]
    assert n1 == 1 and m1 is not None and abs(m1 + 0.1) < 1e-12
    assert n5 == 2 and m5 is not None and abs(m5 - (-0.02 - 0.2) / 2) < 1e-12


def test_verdict_is_incomplete_before_any_truth_has_two_paired_seeds() -> None:
    rows = derive([row("a", 1, "ORACLE", -0.7), row("a", 1, "B-lib", -0.75)])
    table = Table({"a": entry("hawkes")}, tuple(rows), (), {})
    v = verdict(table, np.random.default_rng(0), resamples=100)
    assert v.outcome == "incomplete" and not v.controls_pass


def test_a_minus_infinite_score_never_closes_and_stays_out_of_means() -> None:
    rows = derive(
        [
            row("a", 1, "ORACLE", -0.7),
            row("a", 1, "B-lib", -0.75),
            row("a", 1, "X", float("-inf")),
            row("a", 2, "ORACLE", -0.7),
            row("a", 2, "X", -0.72),
        ]
    )
    x1 = next(r for r in rows if r.system == "X" and r.seed == 1)
    assert x1.gap == float("-inf") and x1.gap_closed is None
    from sciagent.campaign.analysis import summarise

    m = summarise(rows, "X")
    assert m.nonfinite == 1 and m.gap_mean is not None
    assert abs(m.gap_mean + 0.02) < 1e-12


def test_gap_closed_is_flagged_when_the_denominator_is_within_its_paired_noise() -> (
    None
):
    """T4: LL(ORACLE) - LL(B-lib) on a seed within the truth's paired SE of that
    difference over seeds is not a usable denominator (audit §1: 37.32 on a
    denominator of 0.0029). The ratio is still computed; it is flagged."""
    rows = derive(
        [
            row("a", 1, "ORACLE", -0.70),
            row("a", 1, "B-lib", -0.80),
            row("a", 2, "ORACLE", -0.70),
            row("a", 2, "B-lib", -0.76),
            row("a", 3, "ORACLE", -0.70),
            row("a", 3, "B-lib", -0.703),
            row("a", 1, "X", -0.72),
            row("a", 2, "X", -0.72),
            row("a", 3, "X", -0.60),
        ]
    )
    x = {r.seed: r for r in rows if r.system == "X"}
    # d = (0.1, 0.06, 0.003): mean 0.0543, paired SE 0.0281.
    assert not x[1].closed_flagged and not x[2].closed_flagged
    assert x[3].closed_flagged and x[3].gap_closed is not None
    from sciagent.campaign.analysis import summarise

    m = summarise(rows, "X")
    assert m.closed_flagged == 1
    assert m.closed_median is not None
    assert abs(m.closed_median - (0.8 + 2 / 3) / 2) < 1e-12  # seed 3 left out


def test_gap_closed_is_not_flagged_without_two_seeds_to_estimate_noise() -> None:
    rows = derive(
        [
            row("a", 1, "ORACLE", -0.70),
            row("a", 1, "B-lib", -0.7001),
            row("a", 1, "X", -0.70),
        ]
    )
    (x,) = [r for r in rows if r.system == "X"]
    assert not x.closed_flagged


def test_control_1_needs_zero_oracle_underfit_flags() -> None:
    from dataclasses import replace

    base = _table(0.0)
    flagged = tuple(
        replace(r, oracle_underfit=True)
        if (r.truth, r.seed, r.system) == ("null", 2, "ORACLE")
        else r
        for r in base.rows
    )
    v = verdict(replace(base, rows=flagged), np.random.default_rng(0), resamples=200)
    assert v.outcome == "controls fail" and not v.controls_pass
    assert v.underfit is not None and v.underfit.flagged == (("null", 2),)
    unrecorded = tuple(
        replace(r, oracle_underfit=None) if r.system == "ORACLE" else r
        for r in base.rows
    )
    v = verdict(replace(base, rows=unrecorded), np.random.default_rng(0), resamples=200)
    assert v.outcome == "controls fail"
    assert any("not recorded" in r for r in v.reasons)


def test_control_2_reads_a_minus_infinite_rand_unit_by_rank() -> None:
    from dataclasses import replace

    base = _table(0.0)
    rows = derive(
        [
            replace(r, per_event=float("-inf"))
            if (r.truth, r.seed, r.system) == ("hawkes", 1, "B-rand")
            else r
            for r in base.rows
        ]
    )
    v = verdict(
        replace(base, rows=tuple(rows)), np.random.default_rng(0), resamples=200
    )
    assert v.planted is not None and v.planted.n_infinite == 1
    assert v.planted.wins == 5 and v.planted.passed


def test_an_oracle_cell_without_the_underfit_key_reads_as_unrecorded() -> None:
    from sciagent.campaign.analysis import row_from_cell

    cell: dict[str, Any] = {
        "reading": {"status": (1.0).hex(), "per_event": (-0.7).hex()},
        "detail": {"result": {"structure": "s"}},
    }
    old = row_from_cell("a", 1, "ORACLE", False, cell)
    assert old.oracle_underfit is None and old.oracle_psi_source is None
    cell["detail"]["result"].update(
        {"oracle_underfit": False, "oracle_psi_source": "truth"}
    )
    new = row_from_cell("a", 1, "ORACLE", False, cell)
    assert new.oracle_underfit is False and new.oracle_psi_source == "truth"


# --------------------------------------------------------------------------
# A reference (ORACLE or B-lib) at -inf on a (truth, seed); defect hunt B F1
# --------------------------------------------------------------------------


def _with(base: Table, changes: dict[tuple[str, int, str], float]) -> Table:
    from dataclasses import replace

    rows = derive(
        [
            replace(r, per_event=changes[(r.truth, r.seed, r.system)])
            if (r.truth, r.seed, r.system) in changes
            else r
            for r in base.rows
        ]
    )
    return replace(base, rows=tuple(rows))


def test_a_minus_infinite_blib_seed_leaves_gap_closed_undefined() -> None:
    """``derive`` used to raise ScoringError (heldout.gap_closed refuses a
    non-finite reference). Gap closed against a -inf B-lib is undefined."""
    rows = derive(
        [
            row("a", s, sys_, v)
            for s, lib in ((1, -0.75), (2, -0.76), (3, float("-inf")))
            for sys_, v in (("ORACLE", -0.70), ("B-lib", lib), ("X", -0.72))
        ]
    )
    by = {(r.seed, r.system): r for r in rows}
    assert by[(3, "B-lib")].gap == float("-inf")
    assert by[(3, "X")].gap_closed is None
    assert by[(3, "X")].gap is not None and abs(by[(3, "X")].gap + 0.02) < 1e-12
    assert by[(1, "X")].gap_closed is not None and not by[(1, "X")].closed_flagged


def test_control_1_reads_a_minus_infinite_blib_seed_as_no_evidence() -> None:
    """Preregistered (gonogo docstring): control 1 reads finite gaps only. A
    truth keeps its check on its other seeds; with fewer than 2 finite pairs
    it is not checkable (inconclusive), never kept on the infinite seed."""
    one = _with(_table(0.0), {("hawkes", 3, "B-lib"): float("-inf")})
    v = verdict(one, np.random.default_rng(0), resamples=200)
    assert v.outcome == "go", v.reasons
    assert "hawkes" in v.kept
    two = _with(
        _table(0.0),
        {("hawkes", s, "B-lib"): float("-inf") for s in (2, 3)},
    )
    v = verdict(two, np.random.default_rng(0), resamples=200)
    assert "hawkes" not in v.kept and "hawkes" not in v.dropped
    assert any("hawkes" in m for m in v.inconclusive)


def test_a_minus_infinite_oracle_seed_leaves_every_gap_there_undefined() -> None:
    """Nothing is measured from a -inf reference: no gap (it was nan for the
    ORACLE and +inf for every other system), no gap closed, no curve gap."""
    rows = derive(
        [
            row("a", s, sys_, v, curve=((1, -0.8),))
            for s, ora in ((1, -0.70), (2, -0.71), (3, float("-inf")))
            for sys_, v in (("ORACLE", ora), ("B-lib", -0.75), ("X", -0.72))
        ]
    )
    for r in rows:
        if r.seed == 3:
            assert r.gap is None and r.gap_closed is None and r.curve_gaps == ()
        else:
            assert r.gap is not None and math.isfinite(r.gap)


def test_an_oracle_minus_inf_unit_fails_control_1() -> None:
    """LOG 2026-10-27 item (c), replacing the 2026-10-06 drop rule: an ORACLE
    at -inf on any (truth, seed) is not an upper bound, and fails control 1
    exactly as an ``oracle_underfit`` flag does, with a reason naming the
    unit. The red team's probe: on this table the drop shrank controls 2 and 3
    and turned GO into CONTROLS FAIL through the drop; now it is control 1."""
    t = _with(_table(0.0), {("hawkes", 3, "ORACLE"): float("-inf")})
    v = verdict(t, np.random.default_rng(0), resamples=200)
    assert v.outcome == "controls fail" and not v.controls_pass
    assert v.oracle_inf == (("hawkes", 3),)
    assert any("-inf" in m and "['hawkes s3']" in m for m in v.reasons), v.reasons
    # Controls 2 and 3 still read every kept truth: the -inf truth is not
    # dropped, so they pass on this table and C1 alone fails.
    assert v.planted is not None and v.planted.passed
    assert v.bsym is not None and v.bsym.passed


def test_every_oracle_minus_inf_unit_is_named() -> None:
    t = _with(
        _table(0.0),
        {
            ("hawkes", 3, "ORACLE"): float("-inf"),
            ("hawkes", 1, "ORACLE"): float("-inf"),
        },
    )
    v = verdict(t, np.random.default_rng(0), resamples=200)
    assert v.oracle_inf == (("hawkes", 1), ("hawkes", 3))
    assert any(
        "2 unit(s)" in m and "['hawkes s1', 'hawkes s3']" in m for m in v.reasons
    )
    assert not v.controls_pass


def test_an_oracle_minus_inf_truth_is_not_labelled_not_identifiable() -> None:
    """The old rule listed the -inf truth under "dropped as not identifiable
    (ORACLE ≤ B-lib)", which it was not shown to be. Now its ORACLE-vs-B-lib
    check reads its finite seeds only, as for a -inf B-lib seed; with fewer
    than 2 finite pairs it is not checkable (inconclusive)."""
    t = _with(_table(0.0), {("hawkes", 3, "ORACLE"): float("-inf")})
    v = verdict(t, np.random.default_rng(0), resamples=200)
    assert "hawkes" not in v.dropped and "hawkes" in v.kept
    assert not any("not identifiable" in m and "hawkes" in m for m in v.reasons)
    t = _with(_table(0.0), {("hawkes", s, "ORACLE"): float("-inf") for s in (2, 3)})
    v = verdict(t, np.random.default_rng(0), resamples=200)
    assert "hawkes" not in v.dropped and "hawkes" not in v.kept
    assert not any("not identifiable" in m and "hawkes" in m for m in v.reasons)
    assert any("hawkes" in m for m in v.inconclusive)
    assert not v.controls_pass


def test_no_oracle_minus_inf_leaves_the_verdict_unchanged() -> None:
    v = verdict(_table(0.0), np.random.default_rng(0), resamples=200)
    assert v.outcome == "go" and v.controls_pass, v.reasons
    assert v.oracle_inf == ()
    assert not any("-inf" in m for m in v.reasons)
    # A -inf elsewhere (B-lib) is not an ORACLE -inf and fails nothing.
    one = _with(_table(0.0), {("hawkes", 3, "B-lib"): float("-inf")})
    v = verdict(one, np.random.default_rng(0), resamples=200)
    assert v.outcome == "go" and v.oracle_inf == ()


def test_an_llm_oracle_row_is_not_read_for_minus_inf() -> None:
    from sciagent.campaign.analysis import oracle_inf_units

    rows = [
        row("a", 1, "ORACLE", float("-inf"), llm=True),
        row("a", 2, "ORACLE", float("-inf")),
        row("a", 3, "ORACLE", None),
        row("b", 1, "ORACLE", -0.7),
    ]
    assert oracle_inf_units(rows) == (("a", 2),)


# --------------------------------------------------------------------------
# Control 1's underfit half and failed ORACLE units; defect hunt B F2
# --------------------------------------------------------------------------


def test_a_failed_oracle_unit_counts_as_unrecorded() -> None:
    """A failed ORACLE unit has no flag: zero flags cannot be shown for it."""
    from sciagent.campaign.analysis import underfit_check

    u = underfit_check(
        [row("a", 1, "ORACLE", -0.7), row("a", 2, "ORACLE", None, ok=False)]
    )
    assert not u.passed
    assert u.checked == 2 and u.unrecorded == (("a", 2),) and u.flagged == ()
