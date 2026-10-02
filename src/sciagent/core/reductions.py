"""Reductions that fold floats in an order no CPU gets to choose.

CLAUDE.md's third invariant asks for byte-identical output from the same seed,
config and version. Every function here exists because numpy's own reductions do
not provide that across machines, and because the registry content-addresses a
result over ``(env version, config, data version, metric version, seed)`` with
**no platform term** -- so two machines disagreeing in the last place produce two
different numbers under one address, and nothing is positioned to notice.

Why numpy's reductions are not enough
-------------------------------------

Floating-point addition is not associative, so a sum's value depends on the
order it is taken in. ``np.sum`` and ``np.mean`` fold with pairwise summation
implemented over SIMD lanes, and the lane count comes from the CPU's features;
``np.dot`` on float64 dispatches to BLAS, and the wheels this project installs
ship OpenBLAS built ``DYNAMIC_ARCH``, which selects a kernel at run time from the
same. Neither is a defect in numpy -- both are the right trade for numerical
work generally. They are the wrong trade here, where a metric value decides
which bin a replicate falls in, a bin decides a count, and a count decides a
likelihood. ``docs/v1/DECISIONS.md`` records the measurement: S12's posterior
predictive p-value came out 0.101100 on Windows and 0.1009 on Ubuntu from one
commit and one seed.

:func:`math.fsum` is exactly rounded. It returns *the* correctly-rounded sum of
its inputs, which is a single value determined by the multiset of addends alone
-- so it cannot depend on the order they are folded in, on the width of a vector
register, or on which kernel a dispatcher picked.

What is deliberately still numpy
--------------------------------

The elementwise half of every function below. A lane-wise subtract, square or
multiply is a set of *independent* correctly-rounded operations, one per
element, so vector width cannot change any of them -- there is no accumulator to
reorder. Keeping that work in numpy is what makes determinism cheap here rather
than a rewrite into Python loops: measured on slice-realistic arrays, the extra
cost of the whole catalogue is about a second on a full cold table build.

Not covered, and this is the important part
--------------------------------------------

**These functions remove one class of platform divergence, not all of them.**
Read the guarantee narrowly: the *fold* is exact, so the order addends are
summed in cannot matter. Nothing here makes the addends themselves portable.

Three things stay open, in descending order of how much they should worry you.

**Transcendental functions are not correctly rounded.** ``exp``, ``log`` and
friends are permitted a rounding error by every practical implementation, and
implementations differ -- measured here, ``math.exp`` disagrees with the
correctly-rounded double for **17694 of 20000** inputs across ``[-40, 0]``. The
Hawkes kernels in ``environments/pointproc/components.py`` sum ``np.exp(...)``
terms, so an exactly-rounded fold over inexactly-rounded addends is still only
as portable as the platform's ``exp``. Closing this would need a
correctly-rounded math library, which is a dependency this project does not
have and should not take on for it.

**Transforms that are not folds**, notably ``np.fft``. No exact-rounding
equivalent exists to substitute. numpy's FFT is pocketfft compiled in rather
than a dispatching library, so it is *probably* stable across x86-64 -- an
expectation, not a measurement.

So what is this worth? It closes the class that was *demonstrated*: two machines
computing different numbers because BLAS and numpy's pairwise summation choose
their kernels from CPU features. It also makes every remaining suspect a
narrower and more testable one.

Note what it is *not* for. The project is pinned to one platform -- Windows --
so portability across machines is not what these functions earn their keep on
day to day. What they still earn it on is the same machine. Invariant 3 asks
for byte-identical output across processes and across runs, and summation order
is one thing that can move underneath you without leaving the machine:
``np.sum`` picks its pairwise dispatch from CPU features, and a numpy upgrade
can change that pick while every version term in a content address stays put.

Read that as narrowly as the section above, because it is the same limit
restated on a second axis. What is nailed down is the *order*, never the
addends. The ``np.exp`` terms the Hawkes kernels hand to :func:`total` are as
free to move under a numpy upgrade as under a change of platform, for the
reason given three paragraphs up, and nothing in ``(env version, config, data
version, metric version, seed)`` names the numpy that computed them. So no
diff of the kind described in ``docs/v1/DECISIONS.md`` is dispensable here --
the pin retires the platform half of that question and leaves the rest
standing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np

from sciagent.core.errors import ExecutionError
from sciagent.core.types import Floats

__all__ = [
    "PAIRWISE_BLOCK",
    "ExactSum",
    "PairwiseAccumulator",
    "deviation",
    "dot",
    "matvec",
    "mean",
    "pairwise_depth",
    "pairwise_rows",
    "row_totals",
    "total",
    "variance",
]


def total(values: Floats) -> float:
    """Return the exactly-rounded sum of ``values``.

    Guarantees a value that depends only on the multiset of addends, and so is
    identical on every platform and for every summation order.

    One dimension only. ``tolist()`` on a 2-D array gives nested lists, which
    :func:`math.fsum` rejects with a bare ``TypeError`` from the standard
    library -- a fault reported by neither this module's name nor its error
    type. Ravel deliberately at the call site instead, so that flattening is a
    decision somebody made rather than one this function made quietly.
    """
    if values.ndim != 1:
        raise ExecutionError(
            f"reductions take a one-dimensional array, got {values.ndim} "
            f"dimensions with shape {values.shape}; ravel at the call site if "
            f"flattening is what you mean"
        )
    return math.fsum(values.tolist())


def mean(values: Floats) -> float:
    """Return the arithmetic mean, summed exactly.

    Raises :class:`~sciagent.core.errors.ExecutionError` on an empty array
    rather than returning ``nan``: a mean of nothing is a caller's mistake, and
    a silent ``nan`` would travel into a bin edge and be discovered as a missing
    count much later.
    """
    if values.size == 0:
        raise ExecutionError("cannot take the mean of an empty array")
    return total(values) / values.size


def variance(values: Floats, *, ddof: int = 1) -> float:
    """Return the variance about the exactly-summed mean.

    The deviations are squared elementwise by numpy and folded by
    :func:`total`, so the only ordered step is exact. ``ddof`` defaults to 1,
    matching every call site this replaced.

    Not algebraically identical to ``np.var``: this centres on a mean that is
    itself exactly summed, where numpy centres on its own. The difference is in
    the last places and is the point -- one of the two values is reproducible.
    """
    if values.size <= ddof:
        raise ExecutionError(
            f"variance with ddof={ddof} needs more than {ddof} value(s), "
            f"got {values.size}"
        )
    centred = values - mean(values)
    return total(np.square(centred)) / (values.size - ddof)


def deviation(values: Floats, *, ddof: int = 1) -> float:
    """Return the standard deviation about the exactly-summed mean.

    The square root of :func:`variance`, and separate from it only so that a
    caller wanting ``np.std`` has something to reach for rather than an excuse
    to reach for ``np.std``.
    """
    return math.sqrt(variance(values, ddof=ddof))


def dot(left: Floats, right: Floats) -> float:
    """Return the inner product, multiplied elementwise and summed exactly.

    The replacement for ``np.dot`` on float64 vectors, which is the call that
    reaches BLAS and therefore the CPU-dispatched kernel.
    """
    if left.shape != right.shape:
        raise ExecutionError(
            f"inner product needs matching shapes, got {left.shape} and {right.shape}"
        )
    return total(left * right)


def row_totals(matrix: Floats) -> Floats:
    """Return the exactly-rounded sum of each row of a 2-D array.

    The replacement for ``matrix.sum(axis=1)``: one :func:`math.fsum` per row,
    so each entry is the correctly rounded sum whatever numpy's dispatch.
    """
    if matrix.ndim != 2:
        raise ExecutionError(f"row_totals needs a 2-D array, got {matrix.ndim}-D")
    return np.fromiter(
        (math.fsum(row) for row in matrix), dtype=np.float64, count=matrix.shape[0]
    )


def matvec(matrix: Floats, vector: Floats) -> Floats:
    """Return ``matrix @ vector`` with every entry summed exactly.

    The replacement for a BLAS matrix-vector product whose result is reported
    or stored: elementwise products, then :func:`row_totals`.
    """
    if matrix.ndim != 2 or vector.shape != (matrix.shape[1],):
        raise ExecutionError(
            f"matvec needs (m, k) and (k,), got {matrix.shape} and {vector.shape}"
        )
    return row_totals(matrix * vector)


# --------------------------------------------------------------------------
# A fixed pairwise tree: fast, deterministic, not exact
# --------------------------------------------------------------------------

#: Width of the tree's blocks. Part of the tree's definition: changing it
#: changes every multi-block fold in the last places.
PAIRWISE_BLOCK: Final = 4096


def _halve_rows(buf: Floats, n: int) -> Floats:
    """Fold ``buf[:, :n]`` in place: the top ``n // 2`` elements are added
    onto the bottom ones, and the middle element of an odd count waits."""
    while n > 1:
        h = n // 2
        np.add(buf[:, :h], buf[:, n - h : n], out=buf[:, :h])
        n -= h
    out: Floats = buf[:, 0].copy()
    return out


def pairwise_rows(matrix: Floats) -> Floats:
    """Sum each row of a 2-D array by one fixed pairwise tree.

    Not exactly rounded, unlike :func:`row_totals`: this is for intermediate
    quantities (a Newton step, a residual judged against its own error bound)
    that must be *reproducible* but need not be exact, at elementwise-numpy
    speed. Every add is an elementwise, correctly rounded numpy operation on
    independent elements, so no CPU feature, SIMD width or BLAS kernel can
    change the order; a row's value depends only on that row's values and its
    length, never on how many rows are folded together.

    The tree, for a row of length n: if ``n ≤ PAIRWISE_BLOCK``, repeated
    halving (the top ``n // 2`` elements added onto the bottom ones, an odd
    middle element carried). Otherwise the row is cut into blocks of
    ``PAIRWISE_BLOCK``, the last zero-padded (adding zero is exact); blocks
    are added elementwise in adjacent pairs, level by level, an odd last
    block carried up; the surviving block is folded by halving.
    :class:`PairwiseAccumulator` builds the same tree from blocks streamed
    one at a time. The error is at most ``gamma_d · Σ|x|`` with ``d =``
    :func:`pairwise_depth` and ``gamma_d = d·u/(1 - d·u)``, ``u = 2⁻⁵³``.
    """
    if matrix.ndim != 2:
        raise ExecutionError(f"pairwise_rows needs a 2-D array, got {matrix.ndim}-D")
    k, n = matrix.shape
    if n == 0:
        return np.zeros(k)
    if n <= PAIRWISE_BLOCK:
        return _halve_rows(np.array(matrix, dtype=np.float64, copy=True), n)
    nb = -(-n // PAIRWISE_BLOCK)
    buf = np.zeros((k, nb, PAIRWISE_BLOCK))
    buf.reshape(k, nb * PAIRWISE_BLOCK)[:, :n] = matrix
    count, stride = nb, 1
    while count > 1:
        h = count // 2
        end = 2 * h * stride
        left = buf[:, 0 : end : 2 * stride]
        np.add(left, buf[:, stride : end : 2 * stride], out=left)
        count -= h
        stride *= 2
    return _halve_rows(buf[:, 0, :], PAIRWISE_BLOCK)


class PairwiseAccumulator:
    """:func:`pairwise_rows` over blocks pushed one at a time.

    Push consecutive ``(rows, PAIRWISE_BLOCK)`` blocks of the rows being
    folded, the last of them possibly narrower; :meth:`result` is then
    bit-identical to :func:`pairwise_rows` of the whole rows (a binary
    counter over blocks, merged right to left at the end, is the same tree as
    adjacent pairing with the odd block carried). Lets a caller compute the
    addends block by block in cache -- a Gram matrix's products, say --
    without ever holding all of them.
    """

    def __init__(self, rows: int) -> None:
        self._rows = rows
        self._stack: list[tuple[int, Floats]] = []
        self._count = 0
        self._first_width = 0
        self._closed = False

    def push(self, block: Floats) -> None:
        """Add the next block of columns; it is read, never kept."""
        if block.ndim != 2 or block.shape[0] != self._rows:
            raise ExecutionError(
                f"expected a ({self._rows}, w) block, got shape {block.shape}"
            )
        width = block.shape[1]
        if self._closed or width == 0 or width > PAIRWISE_BLOCK:
            raise ExecutionError(
                "blocks must be non-empty and PAIRWISE_BLOCK wide, except the last"
            )
        self._closed = width < PAIRWISE_BLOCK
        if self._count == 0:
            self._first_width = width
        self._count += 1
        if self._stack and self._stack[-1][0] == 0:
            # An odd block: add it straight onto its partner, no copy. A
            # narrow last block is zero-padded, and adding the zero is kept
            # (it turns a -0.0 into +0.0, as the padded tree does).
            _, buf = self._stack.pop()
            np.add(buf[:, :width], block, out=buf[:, :width])
            if width < PAIRWISE_BLOCK:
                np.add(buf[:, width:], 0.0, out=buf[:, width:])
            level = 1
        else:
            buf = np.empty((self._rows, PAIRWISE_BLOCK))
            buf[:, :width] = block
            buf[:, width:] = 0.0
            level = 0
        while self._stack and self._stack[-1][0] == level:
            _, prev = self._stack.pop()
            np.add(prev, buf, out=prev)
            buf = prev
            level += 1
        self._stack.append((level, buf))

    def result(self) -> Floats:
        """The row sums. The accumulator is spent afterwards."""
        self._closed = True
        if self._count == 0:
            return np.zeros(self._rows)
        if self._count == 1:
            return _halve_rows(self._stack[0][1], self._first_width)
        acc = self._stack[-1][1]
        for _, buf in reversed(self._stack[:-1]):
            np.add(buf, acc, out=buf)
            acc = buf
        self._stack = []
        return _halve_rows(acc, PAIRWISE_BLOCK)


def _ceil_log2(n: int) -> int:
    return max(0, n - 1).bit_length()


def pairwise_depth(n: int) -> int:
    """The most additions any addend passes through in :func:`pairwise_rows`
    of a row of length ``n``: the ``d`` of its error bound."""
    if n <= PAIRWISE_BLOCK:
        return _ceil_log2(n)
    return _ceil_log2(-(-n // PAIRWISE_BLOCK)) + _ceil_log2(PAIRWISE_BLOCK)


# --------------------------------------------------------------------------
# Exact sums at array speed
# --------------------------------------------------------------------------

#: One bin per biased binary exponent (the IEEE field, 0 for subnormals).
_N_BINS: Final = 2048
_EXP_FIELD: Final = np.int64(0x7FF << 52)
#: The bits of a double with biased exponent 1075 = 1023 + 52: OR-ed onto a
#: double's sign and fraction bits, they make the float ``±(2⁵² + fraction)``.
_UNIT_INTEGER: Final = np.int64(1075 << 52)
#: Addends at or above this magnitude (and non-finite ones) skip the bins.
_BIG: Final = 2.0**960
#: Bin totals stay below 2⁵³, so every add into them is exact, while fewer
#: than 2²⁶ addends are binned; compact well before that.
_MAX_COUNT: Final = 2**25
#: Addends binned per pass: small enough to stay in cache.
_CHUNK: Final = 2**14


@dataclass(frozen=True, eq=False)
class ExactSum:
    """An exact sum of float64 addends, held as integer-valued bin totals.

    Every finite double is ``±M·2^(b-1075)`` with ``M`` an integer below 2⁵³
    and ``b`` its biased exponent field (``b = 1`` read for subnormals, whose
    ``M`` lacks the implicit bit). ``M`` is read off the bits as a float
    (the sign and fraction bits under a fixed exponent), then split
    ``M = H·2²⁷ + L`` with ``|H| < 2²⁶`` and ``|L| < 2²⁷``, both exact.
    Summing ``H`` and ``L`` per exponent adds integers whose totals stay
    below 2⁵³, so *every* float addition there is exact and its order cannot
    matter: ``np.bincount`` may fold in any order it likes. The exact total
    is ``Σ_b H_b·2^(b-1048) + L_b·2^(b-1075)``, each term a representable
    double -- also for subnormal addends, which sit on the 2⁻¹⁰⁷⁴ grid the
    terms of bin 0 are scaled to -- and :meth:`value` hands those at most
    4096 terms to :func:`math.fsum`. So the value is the correctly rounded
    sum, equal to :func:`total` of the addends bit for bit, at a few
    elementwise passes instead of one Python float per addend.

    Sums combine exactly (``+``, ``-``, unary ``-``), so the exact total of a
    union of arrays never re-reads them. Addends of magnitude ≥ 2⁹⁶⁰ and
    non-finite ones are kept aside and reach :func:`math.fsum` as they are,
    so infinities and NaN behave as they do there; where ``fsum`` would raise
    ``OverflowError`` on an intermediate overflow, this may instead return
    the finite exact total.
    """

    hi: Floats
    lo: Floats
    count: int
    extra: tuple[float, ...]

    @classmethod
    def of(cls, values: Floats) -> ExactSum:
        """The exact sum of a one-dimensional array."""
        if values.ndim != 1:
            raise ExecutionError(
                f"ExactSum takes a one-dimensional array, got shape {values.shape}"
            )
        x = np.ascontiguousarray(values, dtype=np.float64)
        big = ~(np.abs(x) < _BIG)
        extra: tuple[float, ...] = ()
        if bool(big.any()):
            extra = tuple(x[big].tolist())
            x = np.ascontiguousarray(x[~big])
        out = cls(np.zeros(_N_BINS), np.zeros(_N_BINS), 0, extra)
        for a in range(0, x.size, _CHUNK):
            out = out._binned(x[a : a + _CHUNK])
        return out

    def _binned(self, x: Floats) -> ExactSum:
        base = self.compacted() if self.count + x.size > _MAX_COUNT else self
        bits = x.view(np.int64)
        field = (bits & _EXP_FIELD) >> 52
        m: Floats = ((bits & ~_EXP_FIELD) | _UNIT_INTEGER).view(np.float64)
        sub = field == 0
        if bool(sub.any()):  # no implicit bit: take the 2⁵² back off
            m = m.copy()
            m[sub] -= np.copysign(2.0**52, m[sub])
        h = np.trunc(m * 2.0**-27)
        lo = m - h * 2.0**27
        return ExactSum(
            base.hi + np.bincount(field, weights=h, minlength=_N_BINS),
            base.lo + np.bincount(field, weights=lo, minlength=_N_BINS),
            base.count + x.size,
            base.extra,
        )

    def _terms(self) -> Floats:
        scale = np.maximum(np.arange(_N_BINS, dtype=np.intp), 1) - 1075
        terms = np.concatenate(
            [np.ldexp(self.hi, scale + 27), np.ldexp(self.lo, scale)]
        )
        out: Floats = terms[terms != 0.0]
        return out

    def compacted(self) -> ExactSum:
        """The same exact sum, rebinned from its own at most 4096 terms."""
        fresh = ExactSum(np.zeros(_N_BINS), np.zeros(_N_BINS), 0, self.extra)
        terms = self._terms()
        return fresh._binned(terms) if terms.size else fresh

    def __add__(self, other: ExactSum) -> ExactSum:
        a, b = self, other
        if a.count + b.count > _MAX_COUNT:
            a, b = a.compacted(), b.compacted()
        return ExactSum(a.hi + b.hi, a.lo + b.lo, a.count + b.count, a.extra + b.extra)

    def __neg__(self) -> ExactSum:
        return ExactSum(-self.hi, -self.lo, self.count, tuple(-v for v in self.extra))

    def __sub__(self, other: ExactSum) -> ExactSum:
        return self + (-other)

    def value(self) -> float:
        """The correctly rounded total, with :func:`math.fsum` semantics."""
        return math.fsum([*self._terms().tolist(), *self.extra])
