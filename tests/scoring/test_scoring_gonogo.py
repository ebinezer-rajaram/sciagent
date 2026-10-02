"""SPEC §7.1 go/no-go, §6.4 positive controls and paired bootstrap CIs.

Synthetic results tables built to hit each branch exactly.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sciagent.scoring.errors import ScoringError
from sciagent.scoring.gonogo import (
    B_LIB,
    B_RAND,
    B_SPARSE,
    B_SYM_10F,
    CLOSE_FRACTION,
    ORACLE,
    PLANTED,
    RECOVERY_TOLERANCE,
    Outcome,
    ScoreRow,
    bsym_approaches_oracle,
    closes,
    go_no_go,
    oracle_beats_lib,
    planted_beats_rand,
    recovers,
)
from sciagent.scoring.stats import paired_bootstrap_ci, paired_mean_se

TRUTHS = tuple(f"t{i:02d}" for i in range(10))
SEEDS = (0, 1, 2)


def _row(
    truth: str,
    seed: int,
    system: str,
    gap: float,
    lib_gap: float,
    *,
    exact: bool = False,
    in_dictionary: bool = False,
) -> ScoreRow:
    closed = None if lib_gap >= 0.0 else (gap - lib_gap) / (0.0 - lib_gap)
    return ScoreRow(truth, seed, system, gap, closed, exact, in_dictionary)


def _table(
    gaps: dict[str, float],
    *,
    lib_gap: float = -0.05,
    exact: frozenset[str] = frozenset(),
    in_dictionary: bool = False,
    jitter: float = 0.002,
) -> list[ScoreRow]:
    """Every truth x seed; ``gaps`` per system (B-lib's is ``lib_gap``)."""
    rows: list[ScoreRow] = []
    for i, t in enumerate(TRUTHS):
        for s in SEEDS:
            j = jitter * ((i + 2 * s) % 3 - 1)
            lib = lib_gap + j
            rows.append(
                _row(t, s, ORACLE, 0.0, lib, exact=True, in_dictionary=in_dictionary)
            )
            rows.append(
                _row(
                    t,
                    s,
                    B_LIB,
                    lib,
                    lib,
                    exact=B_LIB in exact,
                    in_dictionary=in_dictionary,
                )
            )
            for system, g in gaps.items():
                rows.append(
                    _row(
                        t,
                        s,
                        system,
                        g + j,
                        lib,
                        exact=system in exact,
                        in_dictionary=in_dictionary,
                    )
                )
    return rows


GOOD = {B_RAND: -0.04, B_SPARSE: -0.03, B_SYM_10F: -0.002, PLANTED: -0.004}


# --------------------------------------------------------------------------
# Definitions
# --------------------------------------------------------------------------


def test_recovers_and_closes_definitions() -> None:
    assert recovers(_row("t", 0, B_RAND, -0.2, -0.3, exact=True))
    assert recovers(_row("t", 0, B_RAND, -RECOVERY_TOLERANCE, -0.3))
    assert not recovers(_row("t", 0, B_RAND, -0.0051, -0.3))
    assert closes(_row("t", 0, B_RAND, -0.1 * (1 - CLOSE_FRACTION), -0.1))
    assert not closes(_row("t", 0, B_RAND, -0.02, -0.1))
    assert not closes(_row("t", 0, B_RAND, 0.0, 0.0))  # undefined gap closed


# --------------------------------------------------------------------------
# Verdict branches
# --------------------------------------------------------------------------


def test_go() -> None:
    v = go_no_go(_table(GOOD), rng=np.random.default_rng(0))
    assert v.outcome is Outcome.GO, v.reasons
    assert v.controls_pass and not v.too_easy and not v.too_hard
    assert v.dropped == ()


def test_too_easy_by_b_lib_recovery() -> None:
    v = go_no_go(_table(GOOD, exact=frozenset({B_LIB})), rng=np.random.default_rng(0))
    assert v.outcome is Outcome.TOO_EASY
    assert v.recovery_share[B_LIB] == 1.0


def test_too_easy_by_b_rand_recovery() -> None:
    gaps = {**GOOD, B_RAND: -0.002, PLANTED: 0.0}
    v = go_no_go(_table(gaps), rng=np.random.default_rng(0))
    assert v.outcome is Outcome.TOO_EASY
    assert v.recovery_share[B_RAND] == 1.0


def test_too_easy_by_b_sparse_on_out_of_dictionary_truths() -> None:
    gaps = {**GOOD, B_SPARSE: -0.001}
    v = go_no_go(_table(gaps), rng=np.random.default_rng(0))
    assert v.outcome is Outcome.TOO_EASY
    assert v.sparse_out_of_dictionary_close_share == 1.0


def test_b_sparse_closing_in_dictionary_truths_is_not_too_easy() -> None:
    gaps = {**GOOD, B_SPARSE: -0.001}
    v = go_no_go(_table(gaps, in_dictionary=True), rng=np.random.default_rng(0))
    assert v.sparse_out_of_dictionary_close_share is None
    assert not v.too_easy


def test_too_hard_when_no_system_closes_the_gap() -> None:
    # B-sym@10F must still approach the ORACLE for the controls; make
    # "approach" pass on the median while closing on < 10% of truths is
    # impossible, so instead fail closing everywhere and check precedence:
    gaps = {B_RAND: -0.045, B_SPARSE: -0.04, B_SYM_10F: -0.009, PLANTED: -0.02}
    v = go_no_go(_table(gaps), rng=np.random.default_rng(0))
    assert v.too_hard, v.close_share
    assert v.outcome is Outcome.TOO_HARD, v.reasons
    assert v.controls_pass


def test_controls_fail_take_precedence() -> None:
    gaps = {**GOOD, PLANTED: -0.04}  # planted-hint no better than B-rand
    v = go_no_go(_table(gaps, exact=frozenset({B_LIB})), rng=np.random.default_rng(0))
    assert v.outcome is Outcome.CONTROLS_FAIL
    assert v.too_easy  # still reported


def test_unidentifiable_truths_are_dropped() -> None:
    rows = _table(GOOD)
    # t00: B-lib matches the ORACLE on every seed.
    rows = [
        ScoreRow(r.truth, r.seed, r.system, 0.0, None, r.exact, r.in_dictionary)
        if r.truth == "t00" and r.system == B_LIB
        else r
        for r in rows
    ]
    v = go_no_go(rows, rng=np.random.default_rng(0))
    assert v.dropped == ("t00",)
    assert v.outcome is Outcome.GO
    assert v.truths == 9


def test_all_truths_dropped_fails_the_controls() -> None:
    rows = [
        ScoreRow(r.truth, r.seed, r.system, 0.0, None, r.exact, r.in_dictionary)
        if r.system == B_LIB
        else r
        for r in _table(GOOD)
    ]
    v = go_no_go(rows, rng=np.random.default_rng(0))
    assert v.outcome is Outcome.CONTROLS_FAIL


def test_missing_required_system_is_refused() -> None:
    rows = [r for r in _table(GOOD) if r.system != B_SYM_10F]
    with pytest.raises(ScoringError):
        go_no_go(rows, rng=np.random.default_rng(0))


def test_duplicate_rows_are_refused() -> None:
    rows = _table(GOOD)
    with pytest.raises(ScoringError):
        go_no_go([*rows, rows[0]], rng=np.random.default_rng(0))


def test_verdict_is_deterministic_given_the_seed() -> None:
    a = go_no_go(_table(GOOD), rng=np.random.default_rng(3))
    b = go_no_go(_table(GOOD), rng=np.random.default_rng(3))
    assert a == b


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------


def test_oracle_beats_lib_needs_margin_over_paired_se() -> None:
    rows = _table(GOOD)
    checks = oracle_beats_lib(rows)
    assert all(c.passed for c in checks)
    assert [c.truth for c in checks] == list(TRUTHS)
    # A B-lib that ties the ORACLE on average fails.
    noisy = [
        ScoreRow(
            r.truth, r.seed, r.system, (-0.01, 0.01, 0.0)[r.seed], None, False, False
        )
        if r.system == B_LIB
        else r
        for r in rows
        if r.truth == "t00"
    ]
    (check,) = oracle_beats_lib(noisy)
    assert not check.passed


def test_oracle_beats_lib_needs_two_seeds() -> None:
    rows = [r for r in _table(GOOD) if r.seed == 0]
    with pytest.raises(ScoringError):
        oracle_beats_lib(rows)


def test_planted_and_bsym_controls() -> None:
    rows = _table(GOOD)
    planted = planted_beats_rand(rows, TRUTHS, rng=np.random.default_rng(0))
    assert planted.passed and planted.ci.low > 0.0
    bsym = bsym_approaches_oracle(rows, TRUTHS)
    assert bsym.passed and bsym.median_gap == pytest.approx(-0.002)
    far = [
        ScoreRow(r.truth, r.seed, r.system, -0.05, r.gap_closed, r.exact, False)
        if r.system == B_SYM_10F
        else r
        for r in rows
    ]
    assert not bsym_approaches_oracle(far, TRUTHS).passed


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def test_paired_bootstrap_is_deterministic_and_covers_the_mean() -> None:
    x = [0.1, 0.3, 0.2, 0.5, 0.4]
    y = [0.0, 0.1, 0.0, 0.2, 0.1]
    a = paired_bootstrap_ci(x, y, np.random.default_rng(11))
    b = paired_bootstrap_ci(x, y, np.random.default_rng(11))
    assert a == b
    assert a.low <= a.mean <= a.high
    assert a.mean == pytest.approx(0.22)
    assert a.low > 0.0
    c = paired_bootstrap_ci(x, y, np.random.default_rng(12))
    assert c.mean == a.mean


def test_paired_bootstrap_rejects_bad_input() -> None:
    rng = np.random.default_rng(0)
    with pytest.raises(ScoringError):
        paired_bootstrap_ci([1.0], [1.0, 2.0], rng)
    with pytest.raises(ScoringError):
        paired_bootstrap_ci([], [], rng)
    with pytest.raises(ScoringError):
        paired_bootstrap_ci([1.0], [0.0], rng, level=1.0)


@given(st.lists(st.floats(-10, 10), min_size=2, max_size=20))
def test_paired_mean_se_matches_numpy(d: list[float]) -> None:
    mean, se = paired_mean_se(d, [0.0] * len(d))
    assert mean == pytest.approx(float(np.mean(d)), abs=1e-12)
    assert se == pytest.approx(float(np.std(d, ddof=1)) / len(d) ** 0.5, abs=1e-9)
