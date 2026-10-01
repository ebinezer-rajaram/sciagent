"""Reductions that no CPU gets to reorder.

No acceptance criterion covers these, so the names deliberately do not follow
the ``test_aN_`` convention that v1's ``scripts/status.py`` read. What they check is
the property the module exists for -- a value determined by the multiset of
addends alone -- rather than agreement with numpy, which is the thing it is
allowed to differ from.
"""

from __future__ import annotations

import math
from itertools import permutations

import numpy as np
import pytest

from sciagent.core.errors import ExecutionError
from sciagent.core.reductions import deviation, dot, mean, total, variance


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
