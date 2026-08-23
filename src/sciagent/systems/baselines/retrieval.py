"""B4: retrieval from a fixed mechanism library, keyed on residual signature.

SPEC §5's **designated primary comparator**, and the system R1 is asked about:
does an LLM that *generates* structure beat a lookup table that *retrieves* it?
A weak B4 would answer that question by default, so this is built to be strong.

How it works
------------

Run experiments, then describe what was seen as a **residual signature**: per
diagnostic, how far the observation sits from what the null predicts, in units of
the null's own spread. Every library mechanism has a signature computed the same
way from the table. Retrieval is nearest neighbour in that space, and the
shortlist is proposed.

Why that is not just a likelihood
---------------------------------

Scoring candidates by ``p(observation | mechanism)`` would make B4 a posterior
engine wearing a different hat, and there would be almost nothing left for the
comparison against V1 to be about -- since A34 the two also select their
post-proposal experiments through the same call. A signature distance is a
genuinely different
object: it compares *summary statistics in diagnostic space* and is blind to how
peaked a mechanism's outcome distribution is. That is what retrieval means, and
it is why B4 can be beaten by evidence a likelihood would have weighed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from sciagent.core.edits import Defect
from sciagent.core.errors import InvestigationError
from sciagent.core.types import Diagnosis, HypothesisId
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.inference.binning import Discretisation
from sciagent.inference.empirical import EmpiricalTable
from sciagent.systems.base import (
    NULL_DEFECT,
    Investigation,
    entertain,
    select_experiments,
)

__all__ = ["Retrieval", "residual_signature"]


def _representatives(axis: Discretisation) -> tuple[float, ...]:
    """Return one finite representative value per bin of ``axis``.

    A bin with two finite edges is represented by its midpoint. An outer bin open
    to infinity -- three of the slice's four diagnostics declare ``high = inf``
    -- is represented by its finite edge displaced by half the width of the
    neighbouring bin, which is finite, order-preserving, and does not pretend to
    know where an unbounded tail's mass sits.
    """
    edges = (axis.low, *axis.interior, axis.high)
    interior = axis.interior
    values: list[float] = []
    for index in range(axis.n_bins):
        low, high = edges[index], edges[index + 1]
        if math.isfinite(low) and math.isfinite(high):
            values.append(0.5 * (low + high))
        elif math.isfinite(low):
            width = (
                interior[-1] - interior[-2]
                if len(interior) >= 2
                else max(1.0, abs(low))
            )
            values.append(low + 0.5 * width)
        elif math.isfinite(high):
            width = (
                interior[1] - interior[0] if len(interior) >= 2 else max(1.0, abs(high))
            )
            values.append(high - 0.5 * width)
        else:
            raise InvestigationError(
                f"axis {axis.metric} is unbounded at both ends, so no bin of it has "
                f"a finite representative and no signature can be built over it"
            )
    return tuple(values)


def _moments(
    table: EmpiricalTable, defect: Defect, design: ExperimentDesign
) -> tuple[tuple[float, float], ...]:
    """Return ``(mean, standard deviation)`` per axis of ``design`` under ``defect``.

    Computed from the table's cell probabilities against the bin representatives,
    marginalised onto each axis, so a template measuring several diagnostics
    yields one pair per diagnostic rather than a single number over joint cells.
    """
    space = design.template().outcome
    probabilities = table.probabilities(defect, design.id)
    out: list[tuple[float, float]] = []
    for position, axis in enumerate(space.axes):
        representatives = _representatives(axis)
        marginal = [0.0] * axis.n_bins
        for cell, mass in enumerate(probabilities):
            marginal[space.coordinates(cell)[position]] += mass
        mean = math.fsum(m * v for m, v in zip(marginal, representatives, strict=True))
        variance = math.fsum(
            m * (v - mean) ** 2 for m, v in zip(marginal, representatives, strict=True)
        )
        out.append((mean, math.sqrt(max(0.0, variance))))
    return tuple(out)


def residual_signature(
    table: EmpiricalTable,
    defect: Defect,
    designs: Sequence[ExperimentDesign],
    *,
    reference: Defect = NULL_DEFECT,
) -> tuple[float, ...]:
    """Return ``defect``'s expected signature over ``designs``, against ``reference``.

    Component ``(design, axis)`` is ``(mean_defect - mean_reference) /
    sd_reference``: how far this structure is expected to push the diagnostic
    away from the reference, in units of the reference's own spread. A reference
    with no spread on an axis contributes a unit scale rather than a division by
    zero, so a degenerate diagnostic is merely uninformative instead of fatal.

    Guarantees the ordering of components follows ``designs`` and, within a
    design, its template's axis order, so two signatures are comparable exactly
    when they were built from the same design sequence.
    """
    components: list[float] = []
    for design in designs:
        under = _moments(table, defect, design)
        base = _moments(table, reference, design)
        for (mean, _), (base_mean, base_sd) in zip(under, base, strict=True):
            scale = base_sd if base_sd > 0.0 else 1.0
            components.append((mean - base_mean) / scale)
    return tuple(components)


class Retrieval:
    """SPEC §5's B4.

    Guarantees the retrieved ranking is a pure function of what was observed and
    of the library, that ties break by library name so the order never depends on
    a dict, and that only library structures are ever proposed.
    """

    __slots__ = ("_library", "_shortlist")

    def __init__(self, library: Mapping[str, Defect], *, shortlist: int = 3) -> None:
        if shortlist < 1:
            raise InvestigationError(
                f"a shortlist of {shortlist} retrieves nothing; ask for at least one"
            )
        self._library = dict(library)
        self._shortlist = shortlist

    @property
    def name(self) -> str:
        """Return SPEC §5's identifier."""
        return "B4"

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Observe, retrieve, propose the shortlist, then select what is left.

        Half the budget is spent before retrieving and half after. Retrieval
        needs observations to key on, and the experiments that follow are what
        separate a shortlist the posterior then has to weigh -- spending
        everything up front would leave the proposals unexamined, and spending
        nothing would leave the signature undefined.

        The two halves are spent differently, and the asymmetry is the whole of
        A34. The second goes through
        :func:`~sciagent.systems.base.select_experiments`, which is the call V7
        and V1 make, so the designated comparator no longer differs from the
        treatment on selection policy in a contrast about proposal source. The
        first stays a rotation because there is nothing to select between yet;
        see :meth:`_rotate`.
        """
        total = int(investigation.budget.remaining)
        self._rotate(investigation, (total + 1) // 2)

        ranked = self._rank(investigation)
        proposed = ranked[: self._shortlist]
        entertain(investigation, {name: self._library[name] for name in proposed})

        remaining = int(investigation.budget.remaining)
        if len(investigation.engine.live) > 1:
            select_experiments(investigation, remaining)
        else:
            # Nothing was admitted, so the belief holds the null alone and every
            # design's expected information gain is exactly zero -- selection
            # would repeat one design for the whole half. The same degeneracy
            # :meth:`_rotate` documents for the half *before* the proposal, which
            # is reachable here too whenever the proposal step admits nothing.
            self._rotate(investigation, remaining)
        return investigation.conclude(
            residual_candidates=tuple(
                HypothesisId(name) for name in ranked[self._shortlist :]
            ),
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _rotate(investigation: Investigation, count: int) -> None:
        """Run ``count`` experiments in the scenario's design order, repeating.

        The pre-proposal half only, and that is not an oversight left over from
        A34. At this point the graph holds the null and nothing else, so every
        design's expected information gain is exactly zero and
        :func:`~sciagent.experiments.boed.rank` breaks the resulting all-way tie
        by ascending template id -- selection would repeat one design for the
        whole half. The rotation is what spreads the budget over the design space
        instead, which is what the step after it needs. The residual asymmetry
        against V7, which *has* entertained a library by the time it selects, is
        declared on ``SPEC9_CONTRAST`` rather than left for a reader to find.
        """
        designs = investigation.designs
        for step in range(max(0, count)):
            if not investigation.affords():
                return
            investigation.run(designs[step % len(designs)])

    def _rank(self, investigation: Investigation) -> tuple[str, ...]:
        """Return library names by ascending signature distance from what was seen.

        Repeat measurements of one design are averaged before the comparison, so
        a larger budget buys a less noisy key rather than a longer vector.
        """
        table = investigation.engine.table
        observed = _observed_signature(investigation, table)
        if observed is None:
            return tuple(sorted(self._library))
        key, designs = observed
        distances: list[tuple[float, str]] = []
        for name in sorted(self._library):
            expected = residual_signature(table, self._library[name], designs)
            distances.append(
                (
                    math.sqrt(
                        math.fsum(
                            (a - b) ** 2 for a, b in zip(key, expected, strict=True)
                        )
                    ),
                    name,
                )
            )
        return tuple(name for _, name in sorted(distances))


def _observed_signature(
    investigation: Investigation, table: EmpiricalTable
) -> tuple[tuple[float, ...], tuple[ExperimentDesign, ...]] | None:
    """Return what was seen, as a signature, with the designs it is over.

    ``None`` when nothing has been run, which is the one case where retrieval has
    no key and must fall back to library order.
    """
    by_design: dict[str, list[tuple[float, ...]]] = {}
    designs: dict[str, ExperimentDesign] = {}
    for execution in investigation.history:
        key = str(execution.design.id)
        by_design.setdefault(key, []).append(execution.result)
        designs[key] = execution.design
    if not by_design:
        return None
    ordered = tuple(designs[key] for key in sorted(designs))
    components: list[float] = []
    for design in ordered:
        results = by_design[str(design.id)]
        base = _moments(table, NULL_DEFECT, design)
        for position, (base_mean, base_sd) in enumerate(base):
            scale = base_sd if base_sd > 0.0 else 1.0
            mean = math.fsum(result[position] for result in results) / len(results)
            components.append((mean - base_mean) / scale)
    return tuple(components), ordered
