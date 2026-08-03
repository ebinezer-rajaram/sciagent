"""Discretisation of diagnostic outcomes into a finite space.

Why the likelihood is binned
----------------------------

The empirical table engine estimates ``p(result | hypothesis)`` by simulating the
hypothesis and counting how often the result lands where the observation landed.
That requires "where the observation landed" to be a set of positive probability,
which is what a bin is.

The alternative -- a kernel density estimate over the simulated replicates --
fails acceptance test A6 by construction. A6 asks that the estimate sit within
two Monte Carlo standard errors of the exact value; a KDE at its optimal
bandwidth has a smoothing bias of the *same order* as its standard error, so the
discrepancy would never shrink into the tolerance no matter how many replicates
were spent. A binned frequency has no such term: its estimand is exactly the cell
probability, and the only error is Monte Carlo error, which is what A6 and A7 are
written to measure. A7's Miller-Madow correction points the same way -- it is an
estimator for the entropy of a *discrete* distribution from counts, and it has no
meaning unless the outcome space is finite.

Edges are declared, never fitted
--------------------------------

Bin edges are chosen once, from the reference operating point, and frozen. They
are not derived from the scenario under investigation, and not from the observed
value: a discretisation that moved with the data would make the likelihood a
function of the observation twice over. :attr:`Discretisation.version` is a
content hash, so a table built under one set of edges cannot be silently read
under another.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass

from sciagent.core.errors import DiscretisationError, OutOfRangeError
from sciagent.core.types import MetricRef

#: A vector of diagnostic values, one per metric of the template that produced
#: it. Matches the shape of :attr:`sciagent.registry.store.ExperimentRecord
#: .result`, so a registered row can be handed to the engine unchanged.
type DiagnosticVector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Discretisation:
    """A finite partition of one metric's declared range.

    Bin ``i`` is the half-open interval ``[e[i-1], e[i])``, with ``e[-1]`` the
    metric's declared ``low`` and ``e[n]`` its declared ``high``; the final bin is
    closed at the top so that the partition is total. Guarantees every value in
    ``[low, high]`` lands in exactly one bin, and that a value outside raises
    rather than being clamped.
    """

    metric: MetricRef
    interior: tuple[float, ...]
    """Interior edges, strictly ascending, strictly inside ``(low, high)``."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if not self.interior:
            raise DiscretisationError(
                f"discretisation of {self.metric} declares no interior edge, so it "
                f"has a single bin and carries no information"
            )
        if not self.high > self.low:
            raise DiscretisationError(
                f"discretisation of {self.metric} declares an empty range "
                f"{self.low}..{self.high}"
            )
        for previous, edge in zip(self.interior, self.interior[1:], strict=False):
            if not edge > previous:
                raise DiscretisationError(
                    f"interior edges of {self.metric} must ascend strictly, got "
                    f"{self.interior!r}"
                )
        for edge in self.interior:
            if not math.isfinite(edge):
                raise DiscretisationError(
                    f"interior edge {edge!r} of {self.metric} is not finite; an "
                    f"infinite edge would bound a bin that no value can enter"
                )
            if not self.low < edge < self.high:
                raise DiscretisationError(
                    f"interior edge {edge!r} of {self.metric} lies outside the "
                    f"declared range {self.low}..{self.high}"
                )

    @property
    def n_bins(self) -> int:
        """Return the number of bins."""
        return len(self.interior) + 1

    def bin_of(self, value: float) -> int:
        """Return the index of the bin containing ``value``.

        Raises :class:`OutOfRangeError` for a value outside the declared range or
        for a non-finite value. Clamping would be the tempting alternative and is
        wrong: it moves probability mass into a bin whose simulated count was
        never drawn from the same set.
        """
        if not math.isfinite(value):
            raise OutOfRangeError(
                f"{self.metric} returned the non-finite value {value!r}; a "
                f"diagnostic that cannot produce a number must raise"
            )
        if not self.low <= value <= self.high:
            raise OutOfRangeError(
                f"{self.metric} returned {value!r}, outside its declared range "
                f"{self.low}..{self.high}"
            )
        index = 0
        for edge in self.interior:
            if value < edge:
                return index
            index += 1
        return index

    @property
    def version(self) -> str:
        """Return a content hash over the metric, the edges and the range."""
        payload = "\x00".join(
            (
                str(self.metric),
                repr(self.low),
                repr(self.high),
                *(repr(edge) for edge in self.interior),
            )
        )
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
        return f"bins/{digest}"


@dataclass(frozen=True, slots=True)
class OutcomeSpace:
    """The finite outcome space of one experiment template.

    A template measuring ``d`` diagnostics has a ``d``-dimensional space, and a
    cell is the joint bin -- not a product of marginals. Diagnostics read off one
    execution are correlated, and factorising them would make the likelihood
    overconfident and break the calibration A8 measures. The price is that the
    number of cells grows as the product of the axes' bin counts, so a template
    with many diagnostics needs a proportionately larger table; the slice's
    templates declare one diagnostic each.
    """

    axes: tuple[Discretisation, ...]

    def __post_init__(self) -> None:
        if not self.axes:
            raise DiscretisationError("an outcome space needs at least one axis")
        names = [axis.metric.name for axis in self.axes]
        if len(set(names)) != len(names):
            raise DiscretisationError(
                f"an outcome space names a metric twice: {names!r}"
            )

    @property
    def dimension(self) -> int:
        """Return the number of diagnostics this space is over."""
        return len(self.axes)

    @property
    def n_cells(self) -> int:
        """Return the number of joint cells."""
        total = 1
        for axis in self.axes:
            total *= axis.n_bins
        return total

    @property
    def metrics(self) -> tuple[MetricRef, ...]:
        """Return the metrics this space is over, in vector order."""
        return tuple(axis.metric for axis in self.axes)

    def cell_of(self, vector: Sequence[float]) -> int:
        """Return the index of the joint cell containing ``vector``.

        Cells are numbered in mixed radix with the first axis most significant,
        so the numbering is a pure function of the axes and does not depend on
        iteration order anywhere.
        """
        if len(vector) != self.dimension:
            raise DiscretisationError(
                f"outcome space is over {self.dimension} diagnostic(s) "
                f"{[str(m) for m in self.metrics]!r} but was given a vector of "
                f"length {len(vector)}"
            )
        cell = 0
        for axis, value in zip(self.axes, vector, strict=True):
            cell = cell * axis.n_bins + axis.bin_of(value)
        return cell

    def coordinates(self, cell: int) -> tuple[int, ...]:
        """Return the per-axis bin indices of ``cell``."""
        if not 0 <= cell < self.n_cells:
            raise DiscretisationError(
                f"cell {cell} is outside the {self.n_cells} cells of this space"
            )
        indices: list[int] = []
        remaining = cell
        for axis in reversed(self.axes):
            indices.append(remaining % axis.n_bins)
            remaining //= axis.n_bins
        return tuple(reversed(indices))

    @property
    def version(self) -> str:
        """Return a content hash over every axis."""
        payload = "\x00".join(axis.version for axis in self.axes)
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
        return f"outcomes/{digest}"
