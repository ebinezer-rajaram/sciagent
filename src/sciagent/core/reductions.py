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

import numpy as np

from sciagent.core.errors import ExecutionError
from sciagent.core.types import Floats

__all__ = ["deviation", "dot", "mean", "total", "variance"]


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
