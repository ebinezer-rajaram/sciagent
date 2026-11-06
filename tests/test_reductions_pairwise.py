"""The fixed pairwise tree and the bucketed exact sum (``sciagent.core.reductions``).

Two promises are checked, and exactness is deliberately not one of them for the
tree. ``pairwise_rows`` promises a *fixed order*: every row is folded by one
tree that depends only on the row's length, so its value is the same for every
run, every batching of rows and every CPU, and its error is within the
pairwise bound. ``ExactSum`` promises the correctly rounded sum, so it must
agree with :func:`math.fsum` bit for bit wherever ``fsum`` answers, and with
exact rational arithmetic (:class:`fractions.Fraction`) everywhere: ``fsum``
gives up with ``OverflowError`` when a *partial* sum overflows, even when the
total is representable, so it cannot be the reference there.
"""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from sciagent.core.errors import ExecutionError
from sciagent.core.reductions import (
    PAIRWISE_BLOCK,
    ExactSum,
    PairwiseAccumulator,
    pairwise_depth,
    pairwise_rows,
)

U = 2.0**-53
MAX = 1.7976931348623157e308
TINY = 2.0**-1074


def _exact(values: list[float]) -> float | None:
    """The correctly rounded sum of finite ``values`` by rational arithmetic,
    or ``None`` when that sum lies beyond the largest double."""
    exact = sum((Fraction(v) for v in values), Fraction(0))
    try:
        return float(exact)
    except OverflowError:
        return None


def _halve(values: list[float]) -> float:
    a = list(values)
    n = len(a)
    if n == 0:
        return 0.0
    while n > 1:
        h = n // 2
        for i in range(h):
            a[i] = a[i] + a[n - h + i]
        n -= h
    return a[0]


def _reference(values: list[float]) -> float:
    """The tree spelled out in pure Python, one add at a time."""
    n = len(values)
    if n <= PAIRWISE_BLOCK:
        return _halve(values)
    nb = -(-n // PAIRWISE_BLOCK)
    padded = values + [0.0] * (nb * PAIRWISE_BLOCK - n)
    blocks = [padded[b * PAIRWISE_BLOCK : (b + 1) * PAIRWISE_BLOCK] for b in range(nb)]
    while len(blocks) > 1:
        nxt = [
            [x + y for x, y in zip(blocks[i], blocks[i + 1], strict=True)]
            for i in range(0, len(blocks) - 1, 2)
        ]
        if len(blocks) % 2:
            nxt.append(blocks[-1])
        blocks = nxt
    return _halve(blocks[0])


finite = st.floats(allow_nan=False, allow_infinity=False, width=64)
moderate = st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False, width=64)


class TestPairwiseRows:
    @pytest.mark.parametrize(
        "n",
        [
            0,
            1,
            2,
            3,
            7,
            8,
            9,
            PAIRWISE_BLOCK - 1,
            PAIRWISE_BLOCK,
            PAIRWISE_BLOCK + 1,
            3 * PAIRWISE_BLOCK + 17,
            7 * PAIRWISE_BLOCK,
        ],
    )
    def test_matches_the_tree_spelled_out(self, n: int) -> None:
        rng = np.random.default_rng(n)
        x = rng.standard_normal(n) * np.exp(rng.standard_normal(n) * 8.0)
        got = pairwise_rows(x[None, :])
        assert got.shape == (1,)
        assert got[0] == _reference(x.tolist())

    def test_each_row_is_folded_alone(self) -> None:
        """Batching rows together cannot change any row's value."""
        rng = np.random.default_rng(3)
        m = rng.standard_normal((5, 2 * PAIRWISE_BLOCK + 123)) * 1e3
        together = pairwise_rows(m)
        for i in range(5):
            assert together[i] == pairwise_rows(m[i : i + 1])[0]
            assert together[i] == pairwise_rows(np.ascontiguousarray(m[i][None]))[0]

    def test_input_is_not_modified(self) -> None:
        x = np.arange(10.0)[None, :]
        before = x.copy()
        pairwise_rows(x)
        assert np.array_equal(x, before)

    def test_streamed_blocks_agree_with_the_whole_row(self) -> None:
        rng = np.random.default_rng(4)
        n = 5 * PAIRWISE_BLOCK + 999
        m = rng.standard_normal((3, n)) * np.exp(rng.standard_normal((3, n)) * 5.0)
        acc = PairwiseAccumulator(3)
        for a in range(0, n, PAIRWISE_BLOCK):
            acc.push(m[:, a : a + PAIRWISE_BLOCK])
        assert np.array_equal(acc.result(), pairwise_rows(m))

    def test_streaming_a_single_short_block(self) -> None:
        m = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.5]])
        acc = PairwiseAccumulator(2)
        acc.push(m)
        assert np.array_equal(acc.result(), pairwise_rows(m))

    def test_accumulator_rejects_a_short_block_before_the_end(self) -> None:
        acc = PairwiseAccumulator(1)
        acc.push(np.ones((1, 5)))
        with pytest.raises(ExecutionError):
            acc.push(np.ones((1, 5)))

    def test_rejects_non_matrices(self) -> None:
        with pytest.raises(ExecutionError):
            pairwise_rows(np.ones(4))

    @settings(max_examples=200, deadline=None)
    @given(
        hnp.arrays(
            np.float64,
            st.integers(1, 3 * PAIRWISE_BLOCK),
            elements=moderate,
        )
    )
    def test_error_is_within_the_pairwise_bound(self, x: np.ndarray) -> None:
        got = pairwise_rows(x[None, :])[0]
        exact = math.fsum(x.tolist())
        depth = pairwise_depth(x.size)
        bound = depth * U / (1.0 - depth * U) * math.fsum(np.abs(x).tolist())
        assert abs(got - exact) <= bound * (1.0 + 1e-12) + 1e-300

    @pytest.mark.parametrize(
        ("n", "depth"),
        [
            (0, 0),
            (1, 0),
            (2, 1),
            (3, 2),
            (4, 2),
            (5, 3),
            (PAIRWISE_BLOCK, 12),
            (PAIRWISE_BLOCK + 1, 13),
            (2 * PAIRWISE_BLOCK, 13),
            (3 * PAIRWISE_BLOCK, 14),
        ],
    )
    def test_depth(self, n: int, depth: int) -> None:
        assert PAIRWISE_BLOCK == 4096
        assert pairwise_depth(n) == depth


def _wide(rng: np.random.Generator, n: int) -> np.ndarray:
    signs = rng.choice([-1.0, 1.0], n)
    exps = rng.integers(-1074, 960, n)
    mant = rng.uniform(1.0, 2.0, n)
    return signs * np.ldexp(mant, exps)


class TestExactSum:
    @settings(max_examples=300, deadline=None)
    @given(hnp.arrays(np.float64, st.integers(0, 300), elements=finite))
    def test_is_fsum_bit_for_bit(self, x: np.ndarray) -> None:
        try:
            expected = math.fsum(x.tolist())
        except OverflowError:
            return  # fsum gives up on an intermediate overflow; nothing to match
        got = ExactSum.of(x).value()
        assert got == expected or (got == 0.0 and expected == 0.0)
        assert math.copysign(1.0, got) == math.copysign(1.0, expected)

    @pytest.mark.parametrize("seed", range(6))
    def test_wide_exponents_and_subnormals(self, seed: int) -> None:
        rng = np.random.default_rng(seed)
        x = _wide(rng, 20000)
        x[:50] = rng.uniform(-1.0, 1.0, 50) * 2.0**-1060  # subnormals
        x[50:60] = 0.0
        assert ExactSum.of(x).value() == math.fsum(x.tolist())

    def test_cancellation(self) -> None:
        x = np.array([1e16, 1.0, -1e16, 1.0, 1e16, -1e16, 1.0, 2.0**-1074])
        assert ExactSum.of(x).value() == math.fsum(x.tolist())

    def test_compaction_keeps_the_sum_exact(self) -> None:
        rng = np.random.default_rng(9)
        parts = [_wide(rng, 4096) for _ in range(8)]
        acc = ExactSum.of(parts[0]).compacted()
        for p in parts[1:]:
            acc = (acc + ExactSum.of(p)).compacted()
        assert acc.value() == math.fsum(np.concatenate(parts).tolist())

    def test_many_blocks_stream_like_the_whole_row(self) -> None:
        rng = np.random.default_rng(5)
        for nb in (11, 13, 16, 17):
            n = nb * PAIRWISE_BLOCK - 5
            m = rng.standard_normal((1, n)) * 1e5
            acc = PairwiseAccumulator(1)
            for a in range(0, n, PAIRWISE_BLOCK):
                acc.push(m[:, a : a + PAIRWISE_BLOCK])
            assert acc.result()[0] == pairwise_rows(m)[0] == _reference(m[0].tolist())

    @settings(max_examples=100, deadline=None)
    @given(
        hnp.arrays(np.float64, st.integers(0, 100), elements=finite),
        hnp.arrays(np.float64, st.integers(0, 100), elements=finite),
        finite,
    )
    # The corpus counterexample, pinned in source: editing this test's body
    # changes its database key, which orphans the corpus entry it was saved
    # under, so the corpus alone cannot keep it.
    @example(
        np.array(
            [1.0484168695292626e307, 7.339271331761592e302, 8.988465674311582e307, 0.0]
        ),
        np.array([MAX]),
        0.0,
    )
    def test_merge_negate_and_scalar(
        self, a: np.ndarray, b: np.ndarray, s: float
    ) -> None:
        addends = [*a.tolist(), *(-b).tolist(), s]
        acc = ExactSum.of(a) - ExactSum.of(b) + ExactSum.of(np.array([s]))
        expected = _exact(addends)
        if expected is None:
            with pytest.raises(ExecutionError, match="overflow"):
                acc.value()
            return
        got = acc.value()
        assert got == expected
        try:
            via_fsum = math.fsum(addends)
        except OverflowError:
            return  # fsum's partial sums overflowed; the rational reference stands
        assert got == via_fsum

    @pytest.mark.parametrize(
        "x",
        [
            # The corpus counterexample: fsum overflows taking these in this
            # order, while the exact total is about -7.94e307.
            [
                1.0484168695292626e307,
                7.339271331761592e302,
                8.988465674311582e307,
                -MAX,
            ],
            [MAX, MAX, -MAX, -MAX, 1.0],
            [MAX, MAX, -MAX, -MAX],
            [MAX, MAX, -MAX, 2.0**969],  # under half an ulp of MAX: rounds down
            [MAX, MAX, -MAX, -(2.0**970), 3.0],
            [-MAX, -MAX, MAX, 1e300, -1e-300],
        ],
    )
    def test_partial_overflow_with_a_finite_total(self, x: list[float]) -> None:
        with pytest.raises(OverflowError):
            math.fsum(x)  # the premise: fsum itself cannot answer these
        expected = _exact(x)
        assert expected is not None
        got = ExactSum.of(np.array(x)).value()
        assert got == expected
        assert math.copysign(1.0, got) == math.copysign(1.0, expected)

    def test_partial_overflow_keeps_subnormal_bits(self) -> None:
        """Huge addends that cancel must not cost the subnormal ones a bit."""
        x = [MAX, MAX, TINY, -MAX, 3 * TINY, -MAX, -TINY, 2.0**-1060]
        expected = _exact(x)
        assert expected == 3 * TINY + 2.0**-1060
        assert ExactSum.of(np.array(x)).value() == expected

    def test_partial_overflow_across_merged_sums(self) -> None:
        small = np.array([2.0**-1074, 1.5, -(2.0**-1000), 1e-310])
        huge = ExactSum.of(np.array([MAX, MAX]))
        acc = huge + ExactSum.of(small) - huge + ExactSum.of(np.array([MAX]))
        assert acc.value() == _exact([MAX, *small.tolist()])

    @pytest.mark.parametrize(
        "x",
        [
            [MAX, MAX],
            [-MAX, -MAX, 1.0],
            [MAX, 2.0**970],  # half an ulp of MAX, ties to even: rounds up, out
            [MAX, MAX, -MAX, 2.0**971],
        ],
    )
    def test_an_overflowing_total_is_a_typed_error(self, x: list[float]) -> None:
        assert _exact(x) is None
        with pytest.raises(ExecutionError, match="overflow"):
            ExactSum.of(np.array(x)).value()

    def test_non_finite_wins_over_partial_overflow(self) -> None:
        """An infinite addend makes the sum infinite, as in fsum, even where
        fsum would first have tripped over the finite addends overflowing."""
        assert ExactSum.of(np.array([MAX, MAX, math.inf])).value() == math.inf
        assert ExactSum.of(np.array([-MAX, -MAX, -math.inf])).value() == -math.inf
        assert math.isnan(ExactSum.of(np.array([MAX, MAX, math.nan])).value())
        with pytest.raises(ValueError, match="inf"):
            ExactSum.of(np.array([MAX, MAX, math.inf, -math.inf])).value()

    def test_non_finite_follows_fsum(self) -> None:
        assert ExactSum.of(np.array([1.0, math.inf])).value() == math.inf
        assert math.isnan(ExactSum.of(np.array([1.0, math.nan])).value())
        with pytest.raises(ValueError, match="inf"):
            ExactSum.of(np.array([math.inf, -math.inf])).value()

    def test_order_cannot_matter(self) -> None:
        rng = np.random.default_rng(1)
        x = _wide(rng, 5000)
        ref = ExactSum.of(x).value()
        for _ in range(3):
            assert ExactSum.of(rng.permutation(x)).value() == ref

    def test_rejects_non_vectors(self) -> None:
        with pytest.raises(ExecutionError):
            ExactSum.of(np.ones((2, 2)))
