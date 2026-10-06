"""Reductions that no CPU gets to reorder.

No acceptance criterion covers these, so the names deliberately do not follow
the ``test_aN_`` convention that v1's ``scripts/status.py`` read. What they check is
the property the module exists for -- a value determined by the multiset of
addends alone -- rather than agreement with numpy, which is the thing it is
allowed to differ from.
"""

from __future__ import annotations

import math
from fractions import Fraction
from itertools import permutations

import numpy as np
import pytest

from sciagent.core.errors import ExecutionError
from sciagent.core.reductions import (
    deviation,
    dot,
    matvec,
    mean,
    row_totals,
    total,
    variance,
)

MAX = 1.7976931348623157e308
TINY = 2.0**-1074


def _exact(values: list[float]) -> float | None:
    """The correctly rounded sum by rational arithmetic, or ``None`` when it
    lies beyond the largest double. Not ``math.fsum``: that gives up when a
    partial sum overflows, which is the case under test."""
    exact = sum((Fraction(v) for v in values), Fraction(0))
    try:
        return float(exact)
    except OverflowError:
        return None


def _catastrophic() -> np.ndarray:
    """Return values whose sum depends on the order, in ordinary arithmetic.

    A large magnitude, its negation, and small terms that vanish if they are
    added to the large one before it is cancelled. This is the shape that makes
    summation order observable at all, and the shape a SIMD reduction reorders.
    """
    return np.array([1e16, 1.0, -1e16, 1.0, 1e16, -1e16, 1.0], dtype=np.float64)


class TestOrderCannotChangeTheAnswer:
    """The property the module is for."""

    def test_every_permutation_sums_alike(self) -> None:
        values = _catastrophic()
        sums = {
            total(np.array(order, dtype=np.float64)) for order in permutations(values)
        }
        assert len(sums) == 1, f"summation order changed the result: {sorted(sums)}"
        assert sums.pop() == 3.0

    def test_numpy_does_not_have_that_property(self) -> None:
        """The control. Without it the test above proves nothing about numpy.

        ``np.sum`` is pairwise, so a reordering of these values genuinely does
        change its answer -- which is the whole reason the helpers exist. If
        this ever stops holding, the case above has stopped being a case.
        """
        values = _catastrophic()
        sums = {
            float(np.sum(np.array(order, dtype=np.float64)))
            for order in permutations(values)
        }
        assert len(sums) > 1, "np.sum agreed with itself; pick a harder case"

    def test_the_exact_sum_is_what_is_returned(self) -> None:
        values = _catastrophic()
        assert total(values) == math.fsum(values.tolist())


class TestTheyComputeWhatTheyClaim:
    """Agreement with numpy where the arithmetic is not adversarial."""

    @pytest.mark.parametrize("size", [2, 17, 256, 512])
    def test_mean_matches_numpy_on_benign_input(self, size: int) -> None:
        values = np.linspace(0.5, 12.5, size)
        assert mean(values) == pytest.approx(float(np.mean(values)), rel=1e-12)

    @pytest.mark.parametrize("size", [3, 17, 256, 512])
    def test_variance_matches_numpy_on_benign_input(self, size: int) -> None:
        values = np.linspace(0.5, 12.5, size)
        assert variance(values) == pytest.approx(
            float(np.var(values, ddof=1)), rel=1e-12
        )

    @pytest.mark.parametrize("size", [1, 17, 256, 512])
    def test_dot_matches_numpy_on_benign_input(self, size: int) -> None:
        left = np.linspace(0.5, 12.5, size)
        right = np.linspace(-3.0, 4.0, size)
        assert dot(left, right) == pytest.approx(float(np.dot(left, right)), rel=1e-12)

    def test_variance_honours_ddof(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0])
        assert variance(values, ddof=0) == pytest.approx(float(np.var(values)))
        assert variance(values, ddof=1) == pytest.approx(float(np.var(values, ddof=1)))


class TestUndefinedInputRaises:
    """A ``nan`` here becomes a missing count several layers later."""

    def test_mean_of_nothing_raises(self) -> None:
        with pytest.raises(ExecutionError, match="empty array"):
            mean(np.array([], dtype=np.float64))

    def test_variance_needs_more_values_than_its_ddof(self) -> None:
        with pytest.raises(ExecutionError, match="ddof"):
            variance(np.array([1.0]), ddof=1)

    def test_dot_refuses_mismatched_shapes(self) -> None:
        with pytest.raises(ExecutionError, match="matching shapes"):
            dot(np.array([1.0, 2.0]), np.array([1.0]))

    def test_a_two_dimensional_array_is_refused_by_name(self) -> None:
        """``tolist()`` on a 2-D array nests, and ``fsum`` rejects nested lists.

        Left alone that surfaces as a bare ``TypeError`` from the standard
        library, naming neither this module nor the mistake. Ravelling silently
        would be worse: flattening should be something a caller chose.
        """
        square = np.arange(4.0).reshape(2, 2)
        with pytest.raises(ExecutionError, match="one-dimensional"):
            total(square)
        with pytest.raises(ExecutionError, match="one-dimensional"):
            mean(square)

    def test_deviation_is_the_root_of_the_variance(self) -> None:
        values = np.linspace(0.5, 12.5, 64)
        assert deviation(values) == pytest.approx(float(np.std(values, ddof=1)))
        assert deviation(values) == math.sqrt(variance(values))


class TestPartialSumsMayOverflow:
    """``math.fsum`` raises ``OverflowError`` when a *partial* sum overflows,
    depending on the order it meets its addends, even when the total is a
    finite double. A sum whose value depends on order is what the module
    exists to rule out, so these return the correctly rounded total, and a
    total that really is beyond the largest double is a typed error."""

    FINITE_TOTALS = (
        [MAX, MAX, -MAX],
        [MAX, MAX, -MAX, -MAX, 1.0],
        [MAX, MAX, -MAX, -MAX],
        [MAX, MAX, -MAX, 2.0**969],  # under half an ulp of MAX: rounds down
        [-MAX, -MAX, MAX, 1e300, -1e-300],
        [
            1.0484168695292626e307,
            7.339271331761592e302,
            8.988465674311582e307,
            -MAX,
        ],
    )
    OVERFLOWING_TOTALS = (
        [MAX, MAX],
        [-MAX, -MAX, 1.0],
        [MAX, 2.0**970],  # half an ulp of MAX, ties to even: rounds up, out
    )

    @pytest.mark.parametrize("x", FINITE_TOTALS)
    def test_total_is_the_exact_sum(self, x: list[float]) -> None:
        with pytest.raises(OverflowError):
            math.fsum(x)  # the premise: fsum itself cannot answer these
        expected = _exact(x)
        assert expected is not None
        got = total(np.array(x))
        assert got == expected
        assert math.copysign(1.0, got) == math.copysign(1.0, expected)

    @pytest.mark.parametrize("x", FINITE_TOTALS)
    def test_order_still_cannot_matter(self, x: list[float]) -> None:
        sums = {total(np.array(order)) for order in permutations(x)}
        assert sums == {_exact(x)}

    def test_subnormal_bits_survive_huge_cancelling_addends(self) -> None:
        x = [MAX, MAX, TINY, -MAX, 3 * TINY, -MAX, -TINY, 2.0**-1060]
        assert total(np.array(x)) == 3 * TINY + 2.0**-1060 == _exact(x)

    @pytest.mark.parametrize("x", OVERFLOWING_TOTALS)
    def test_an_overflowing_total_is_a_typed_error(self, x: list[float]) -> None:
        assert _exact(x) is None
        with pytest.raises(ExecutionError, match="overflow"):
            total(np.array(x))

    def test_non_finite_follows_fsum(self) -> None:
        assert total(np.array([MAX, MAX, math.inf])) == math.inf
        assert math.isnan(total(np.array([MAX, MAX, math.nan])))
        with pytest.raises(ValueError, match="inf"):
            total(np.array([MAX, MAX, math.inf, -math.inf]))

    def test_mean_inherits_it(self) -> None:
        assert mean(np.array([MAX, MAX, -MAX])) == MAX / 3

    def test_row_totals_is_exact_row_by_row(self) -> None:
        rows = [list(x) + [0.0] * (6 - len(x)) for x in self.FINITE_TOTALS]
        rows.append([1e16, 1.0, -1e16, 1.0, 2.0**-1074, -3.5])  # no overflow
        got = row_totals(np.array(rows))
        assert got.tolist() == [_exact(r) for r in rows]
        assert got[-1] == math.fsum(rows[-1])

    def test_row_totals_refuses_an_overflowing_row(self) -> None:
        m = np.array([[1.0, 2.0], [MAX, MAX]])
        with pytest.raises(ExecutionError, match="overflow"):
            row_totals(m)

    def test_matvec_inherits_it(self) -> None:
        m = np.array([[MAX, MAX, -MAX], [1.0, 2.0, 3.0]])
        got = matvec(m, np.ones(3))
        assert got.tolist() == [MAX, 6.0]


class TestUnchangedWhereFsumAnswers:
    """The fallback must not move one bit of any result fsum already gave."""

    @pytest.mark.parametrize("seed", range(4))
    def test_wide_values_match_fsum_bit_for_bit(self, seed: int) -> None:
        rng = np.random.default_rng(seed)
        n = 5000
        x: np.ndarray = rng.choice([-1.0, 1.0], n) * np.ldexp(
            rng.uniform(1.0, 2.0, n), rng.integers(-1074, 1020, n)
        )
        assert total(x) == math.fsum(x.tolist())
        m = x.reshape(50, 100)
        assert row_totals(m).tolist() == [math.fsum(r) for r in m.tolist()]
