"""The pilot read (SPEC §4.3 derived scores, §6.4 controls, §7.1 verdict)."""

from __future__ import annotations

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


def _table(planted: float) -> Table:
    rows: list[Row] = []
    for t in ("hawkes", "size_excitation", "null"):
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
    truths = {t: entry("hawkes") for t in ("hawkes", "size_excitation", "null")}
    return Table(truths, tuple(derive(rows)), (), {})


def test_verdict_is_go_when_controls_pass_and_difficulty_is_in_band() -> None:
    v = verdict(_table(0.0), np.random.default_rng(0), resamples=500)
    assert v.outcome == "go", v.reasons
    assert v.controls_pass and len(v.kept) == 3 and not v.dropped
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
