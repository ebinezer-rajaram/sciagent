"""Positive control 5's statistic (P4 WP11; Δ11 choice 17).

The one-sided exact McNemar test is checked against values worked out by hand
(``P(Binom(n, 1/2) >= b)`` over the n discordant units) and against scipy's
exact binomial test. The control itself must separate what it claims to: a
truth-aware policy that observes the telling diagnostic where random does not
passes; an equal split, a reversed split and too few units fail.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from scipy import stats as scipy_stats

from sciagent.scoring.controls_p4 import (
    CONTROL_5_ALPHA,
    PolicySeparationVerdict,
    mcnemar_exact_greater,
    policy_separation,
)
from sciagent.scoring.errors import ScoringError

# --------------------------------------------------------------------------
# The exact McNemar p-value
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("b", "c", "expected"),
    [
        # n = 5, all discordant pairs favour the first arm: 1/32.
        (5, 0, Fraction(1, 32)),
        # n = 4: the smallest p reachable is 1/16 > 0.05.
        (4, 0, Fraction(1, 16)),
        # n = 10, b = 8: (C(10,8) + C(10,9) + C(10,10)) / 2**10 = 56/1024.
        (8, 2, Fraction(56, 1024)),
        # n = 10, b = 9: (10 + 1) / 1024.
        (9, 1, Fraction(11, 1024)),
        # n = 6, b = 3: (20 + 15 + 6 + 1) / 64.
        (3, 3, Fraction(42, 64)),
        # b = 0: every outcome is >= 0.
        (0, 5, Fraction(1)),
        # no discordant pair: no evidence at all.
        (0, 0, Fraction(1)),
    ],
)
def test_mcnemar_matches_hand_computed_values(
    b: int, c: int, expected: Fraction
) -> None:
    assert mcnemar_exact_greater(b, c) == float(expected)


@pytest.mark.parametrize("n", [1, 2, 7, 20, 41])
def test_mcnemar_matches_scipy_binomial_test(n: int) -> None:
    for b in range(n + 1):
        ref = scipy_stats.binomtest(b, n, 0.5, alternative="greater").pvalue
        assert mcnemar_exact_greater(b, n - b) == pytest.approx(ref, rel=1e-12)


@pytest.mark.parametrize(("b", "c"), [(-1, 0), (0, -2)])
def test_mcnemar_refuses_negative_counts(b: int, c: int) -> None:
    with pytest.raises(ScoringError):
        mcnemar_exact_greater(b, c)


# --------------------------------------------------------------------------
# Control 5
# --------------------------------------------------------------------------


def _plan(units: list[tuple[str, int, bool]]) -> list[tuple[str, int]]:
    """The planned (truth, seed) units of an input that reads every one."""
    return [(truth, seed) for truth, seed, _ in units]


def _units(
    hits: list[bool], *, seed: int = 1, prefix: str = "t"
) -> list[tuple[str, int, bool]]:
    return [(f"{prefix}{i:02d}", seed, h) for i, h in enumerate(hits)]


def test_a_separating_policy_passes() -> None:
    truth_fsd = _units([True] * 6 + [True] * 10 + [False] * 4)
    random = _units([False] * 6 + [True] * 10 + [False] * 4)
    v = policy_separation(truth_fsd, random, planned=_plan(truth_fsd))
    assert isinstance(v, PolicySeparationVerdict)
    assert (v.units, v.truths, v.both, v.neither) == (20, 20, 10, 4)
    assert (v.truth_only, v.random_only) == (6, 0)
    assert v.p_value == 1 / 64
    assert v.passed and v.alpha == CONTROL_5_ALPHA == 0.05
    assert v.truth_rate == pytest.approx(16 / 20)
    assert v.random_rate == pytest.approx(10 / 20)


def test_concordant_units_do_not_move_the_p_value() -> None:
    a = policy_separation(
        _units([True] * 6), _units([False] * 6), planned=_plan(_units([True] * 6))
    )
    t = _units([True] * 6 + [True] * 30)
    b = policy_separation(t, _units([False] * 6 + [True] * 30), planned=_plan(t))
    assert a.p_value == b.p_value == 1 / 64


@pytest.mark.parametrize(
    ("truth_hits", "random_hits", "reason"),
    [
        ([True] * 5 + [False] * 5, [False] * 5 + [True] * 5, "an equal split"),
        ([False] * 6, [True] * 6, "random better"),
        ([True] * 4, [False] * 4, "four units cannot reach 0.05"),
        ([True] * 3, [True] * 3, "no discordant unit"),
        ([True] * 8 + [False] * 2, [False] * 8 + [True] * 2, "p = 56/1024 > 0.05"),
    ],
)
def test_no_separation_fails(
    truth_hits: list[bool], random_hits: list[bool], reason: str
) -> None:
    t = _units(truth_hits)
    v = policy_separation(t, _units(random_hits), planned=_plan(t))
    assert not v.passed, reason
    assert v.p_value >= CONTROL_5_ALPHA


def test_rates_are_truth_weighted_and_units_are_truth_seed_pairs() -> None:
    # truth a: two seeds, truthFSD hits both, random one; truth b: one seed.
    truth_fsd = [("a", 1, True), ("a", 2, True), ("b", 1, False)]
    random = [("a", 2, False), ("a", 1, True), ("b", 1, False)]
    v = policy_separation(truth_fsd, random, planned=_plan(truth_fsd))
    assert (v.units, v.truths, v.truth_only, v.random_only) == (3, 2, 1, 0)
    assert v.truth_rate == pytest.approx((1.0 + 0.0) / 2)
    assert v.random_rate == pytest.approx((0.5 + 0.0) / 2)
    assert v.p_value == 0.5


def test_the_verdict_does_not_depend_on_input_order() -> None:
    t = _units([True] * 7 + [False, True])
    r = _units([False] * 7 + [True, True])
    plan = _plan(t)
    assert policy_separation(t, r, planned=plan) == policy_separation(
        t[::-1], r[::-1], planned=plan[::-1]
    )


@pytest.mark.parametrize(
    ("truth_fsd", "random"),
    [
        ([("a", 1, True)], [("b", 1, True)]),  # unpaired units
        ([("a", 1, True), ("a", 1, False)], [("a", 1, True)]),  # duplicate
        ([], []),  # nothing to read
    ],
)
def test_unpaired_duplicate_or_empty_input_is_refused(
    truth_fsd: list[tuple[str, int, bool]], random: list[tuple[str, int, bool]]
) -> None:
    planned = sorted({(t, s) for t, s, _ in (*truth_fsd, *random)})
    with pytest.raises(ScoringError):
        policy_separation(truth_fsd, random, planned=planned)


# --------------------------------------------------------------------------
# Control 5 and the crash rule (red team 2026-11-07, findings 1, 2 and 4)
# --------------------------------------------------------------------------

#: Five units truthFSD alone observes, one random alone does: p = 7/64, a fail.
_FAILING_T = [True] * 5 + [False]
_FAILING_R = [False] * 5 + [True]
#: The same without random's unit: p = 1/32, a pass.
_PASSING_T = [True] * 5 + [False]
_PASSING_R = [False] * 6


def test_the_stand_ins_fail_and_pass_as_built() -> None:
    t, r = _units(_FAILING_T), _units(_FAILING_R)
    v = policy_separation(t, r, planned=_plan(t))
    assert (v.truth_only, v.random_only, v.p_value, v.passed) == (5, 1, 7 / 64, False)
    t, r = _units(_PASSING_T), _units(_PASSING_R)
    v = policy_separation(t, r, planned=_plan(t))
    assert (v.truth_only, v.random_only, v.p_value, v.passed) == (5, 0, 1 / 32, True)


def test_a_unit_with_no_reading_ranks_against_the_pass() -> None:
    """None (a failed unit): truthFSD did not observe, random did."""
    t, r = _units(_PASSING_T), _units(_PASSING_R)
    planned = _plan(t)
    r_failed = [(a, s, None if a == "t00" else h) for a, s, h in r]
    v = policy_separation(t, r_failed, planned=planned)
    assert (v.both, v.truth_only, v.random_only, v.passed) == (1, 4, 0, False)
    assert v.random_rate == pytest.approx(1 / 6)
    r_failed = [(a, s, None if a == "t05" else h) for a, s, h in r]
    v = policy_separation(t, r_failed, planned=planned)
    assert (v.truth_only, v.random_only, v.p_value, v.passed) == (5, 1, 7 / 64, False)
    t_failed = [(a, s, None if a == "t00" else h) for a, s, h in t]
    v = policy_separation(t_failed, r, planned=planned)
    assert (v.truth_only, v.neither, v.passed) == (4, 2, False)


def test_a_planned_unit_neither_arm_read_counts_random_only() -> None:
    """A unit cannot vanish: the plan holds it, so it ranks against the pass."""
    t, r = _units(_PASSING_T), _units(_PASSING_R)
    planned = [*_plan(t), ("t99", 1)]
    v = policy_separation(t, r, planned=planned)
    assert (v.units, v.truths, v.truth_only, v.random_only) == (7, 7, 5, 1)
    assert v.p_value == 7 / 64 and not v.passed
    assert v.truth_rate == pytest.approx(5 / 7)
    assert v.random_rate == pytest.approx(1 / 7)


@pytest.mark.parametrize(
    "planned",
    [
        pytest.param([("t00", 1)], id="a-read-unit-outside-the-plan"),
        pytest.param([*_plan(_units(_PASSING_T)), ("t00", 1)], id="planned-twice"),
        pytest.param([], id="nothing-planned"),
    ],
)
def test_a_plan_that_does_not_hold_the_reads_is_refused(
    planned: list[tuple[str, int]],
) -> None:
    with pytest.raises(ScoringError):
        policy_separation(
            _units(_PASSING_T), _units(_PASSING_R), planned=planned
        )


def test_no_unit_read_at_all_counts_every_planned_unit_random_only() -> None:
    v = policy_separation([], [], planned=[("a", 1), ("b", 1)])
    assert (v.units, v.random_only, v.passed) == (2, 2, False)
