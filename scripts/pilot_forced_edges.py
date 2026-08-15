"""Pilot the bin edges of the forced-arrival design. Do not guess them.

``environments/pointproc/outcomes.py`` freezes one interior edge tuple per
diagnostic, "chosen once from a pilot of 300 runs per structure at the reference
operating point". The four observational designs were piloted before their edges
were written down; backlog item 11 adds a fifth design -- a forced arrival read
over ``mean_rate`` -- and this script is that design's pilot.

What to read off it. The edges have to do two things and no more:

* **resolve where the closed set differs.** SPEC §4.2 makes the forced arrival
  the only discriminator of Hawkes self-excitation from latent regime switching,
  so the region between the unexcited structures and the excited one is where
  every bit of resolution is worth having.
* **lump where it does not.** An extra edge in a region no hypothesis occupies
  costs replicates and, worse, costs posterior-predictive power: the check's tail
  sums over every cell no more likely than the observed one, and each unreached
  cell contributes the rule-of-three floor (``docs/DECISIONS.md``, item 6).

Usage::

    uv run python scripts/pilot_forced_edges.py [--quick] [--replicates N]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import ARRIVAL
from environments.pointproc.mechanisms import SIZE_EXCITATION
from environments.pointproc.operations import arrival_burst
from environments.pointproc.outcomes import (
    BURST_COUNT,
    BURST_OBSERVE,
    BURST_SPACING,
    N_EVENTS,
    closed_set,
    executor,
)
from sciagent.core import reductions
from sciagent.core.edits import Defect
from sciagent.core.types import Floats, Seed
from sciagent.experiments.dsl import ExperimentDesign, ForceArrival
from sciagent.inference.binning import Discretisation, OutcomeSpace

#: Quantiles reported per structure. The tails matter more than the centre here:
#: an edge is worth placing where one structure's tail meets another's body.
QUANTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)

#: Every structure whose row the table will hold, so that no occupied region of
#: the axis is discovered after the edges are frozen.
CASES: dict[str, Defect] = {
    **closed_set(),
    "size_excitation": frozenset({SIZE_EXCITATION}),
}


def unbinned_design() -> ExperimentDesign:
    """Return the forced-arrival design over an arbitrary two-bin axis.

    A design needs an outcome space and the outcome space is what this script is
    for, so the pilot measures through a placeholder one. Nothing about it
    reaches the sample: :meth:`~sciagent.experiments.executor.Executor.measure`
    returns the metric's raw value and bins nothing, so the edges cannot
    influence the draws they are chosen from.
    """
    spec = metric_registry().spec("mean_rate")
    return ExperimentDesign(
        operation=ForceArrival(
            component=ARRIVAL,
            at=arrival_burst(BURST_COUNT, BURST_SPACING),
            observe=BURST_OBSERVE,
        ),
        outcome=OutcomeSpace(
            axes=(
                Discretisation(
                    metric=spec.ref, interior=(1.0,), low=spec.low, high=spec.high
                ),
            )
        ),
        n_events=N_EVENTS,
    )


def samples(name: str, replicates: int) -> Floats:
    """Return the post-burst mean rate over ``replicates`` seeds of one structure."""
    runner = executor()
    design = unbinned_design()
    defect = CASES[name]
    return np.array(
        [runner.measure(design, defect, Seed(seed))[0] for seed in range(replicates)],
        dtype=np.float64,
    )


def report(replicates: int) -> dict[str, Floats]:
    """Print the quantile table and return the raw draws per structure."""
    print(
        f"\nPost-burst mean_rate: burst of {BURST_COUNT} at spacing "
        f"{BURST_SPACING}, read over {BURST_OBSERVE} events, "
        f"{replicates} replicates per structure"
    )
    header = "  ".join(f"{q:>7.0%}" for q in QUANTILES)
    print(f"{'structure':<18s}  {header}")
    print("-" * (20 + len(header)))
    drawn: dict[str, Floats] = {}
    for name in sorted(CASES):
        values = samples(name, replicates)
        drawn[name] = values
        cells = "  ".join(f"{float(np.quantile(values, q)):7.3f}" for q in QUANTILES)
        print(f"{name:<18s}  {cells}")
    return drawn


def overlap(drawn: dict[str, Floats]) -> None:
    """Print how far apart Hawkes sits from everything else, in this statistic.

    The number the design exists for. If Hawkes stops separating here, stage 3
    of SPEC §4.2's minimum discriminating plan has no experiment.
    """
    print("\nAUC of hawkes against each other structure (1.0 = fully separated)")
    hawkes = drawn["hawkes"]
    for name in sorted(drawn):
        if name == "hawkes":
            continue
        other = drawn[name]
        # Booleans and counts fold exactly whatever the order, so these three
        # were never at risk. They go through `reductions` anyway: an exempt
        # call is one a later reader has to re-derive the exemption for, and
        # these are printed into the frozen edges in `outcomes.py`.
        wins = (hawkes[:, None] > other[None, :]).ravel().astype(np.float64)
        auc = reductions.mean(wins)
        above = (hawkes > float(np.quantile(other, 0.99))).astype(np.float64)
        clears = reductions.mean(above)
        print(
            f"  vs {name:<18s} AUC {auc:5.3f}   "
            f"hawkes above its 99th percentile: {clears:5.1%}"
        )


def suggest(drawn: dict[str, Floats], edges: Sequence[float]) -> None:
    """Print the occupancy each structure would have under ``edges``.

    A cell no structure reaches is a cell the posterior predictive check pays
    for and learns nothing from; a cell every structure reaches equally resolves
    nothing. Both are visible here before the edges are frozen.
    """
    print(f"\nOccupancy under interior edges {tuple(edges)!r}")
    labels = [f"<{edges[0]:g}"]
    labels += [f"{low:g}-{high:g}" for low, high in pairwise(edges)]
    labels += [f">{edges[-1]:g}"]
    print(f"{'structure':<18s}  " + "  ".join(f"{label:>9s}" for label in labels))
    print("-" * (20 + 11 * len(labels)))
    for name in sorted(drawn):
        counts = np.histogram(drawn[name], bins=[-np.inf, *edges, np.inf])[0]
        share = counts / reductions.total(counts.astype(np.float64))
        print(f"{name:<18s}  " + "  ".join(f"{value:9.3f}" for value in share))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--replicates", type=int, default=None)
    parser.add_argument(
        "--edges",
        type=float,
        nargs="*",
        default=(0.6, 0.9, 1.2, 1.6, 2.2, 3.2, 5.0, 8.0, 12.0, 18.0),
        help="candidate interior edges to report occupancy for",
    )
    arguments = parser.parse_args()
    replicates = arguments.replicates or (60 if arguments.quick else 300)

    drawn = report(replicates)
    overlap(drawn)
    suggest(drawn, arguments.edges)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
