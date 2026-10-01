"""Search the grammar's parameter grid for a common operating point.

v1 SPEC §4.2 asserts that the four mechanisms "all produce overdispersed counts and
clustered inter-arrivals, and are indistinguishable under basic dispersion
diagnostics". That is a claim about a *parameterisation*, not about the
mechanisms as such: any of them can be made obvious by choosing bad parameters.
This script is what makes the claim true, and ``scripts/confounding_check.py``
is what checks it afterwards.

Method
------

1. A coarse continuum scan (not reproduced here; its output is the seed points
   below) locates the region where all four mechanisms can meet.
2. Each seed point is snapped to the grammar's parameter grids.
3. Grid neighbours whose closed-form mean rate misses the reference rate are
   discarded before any simulation runs. The mean rate is a nuisance
   parameter, so mechanisms that disagree on it leak their identity through
   it; pruning analytically removes that leak and spends the whole
   simulation budget on the two statistics that have no closed form.
4. A local exhaustive search over the survivors minimises the worst relative
   deviation from the target across dispersion and the Fano factor.
5. The best few candidates are re-measured at long run length over several
   seeds, and the winner is printed for pasting into ``mechanisms.py``.

Everything is deterministic: the seed set is fixed, so re-running reproduces the
same choice exactly.

Usage::

    uv run python scripts/calibrate_mechanisms.py [--quick]
"""

from __future__ import annotations

import argparse
import itertools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from scipy.special import i0

from environments.pointproc.components import (
    ARRIVAL,
    MIXTURE_OF_POISSON_2,
    POISSON_PERIODIC,
    SIZE,
    TWO_STATE_MARKOV,
)
from environments.pointproc.diagnostics import (
    count_autocorrelation,
    fano_factor,
    inter_arrival_dispersion,
    mean_rate,
)
from environments.pointproc.grammar import (
    EXPONENTIAL_KERNEL,
    HAWKES_GRIDS,
    PERIODIC_GRIDS,
    POISSON_MIXTURE_GRIDS,
    SIZE_EXCITED_GRIDS,
    TWO_STATE_GRIDS,
    edit_grammar,
)
from environments.pointproc.program import (
    REFERENCE_MEAN_SIZE,
    REFERENCE_RATE,
    reference_program,
)
from sciagent.core.edits import (
    AddDependency,
    AddLatentVariable,
    ChangeDistributionFamily,
    Defect,
    Edit,
    ParameterGrid,
    ReparameteriseComponent,
)
from sciagent.core.errors import SciAgentError
from sciagent.core.types import FrozenDict, Seed

# --------------------------------------------------------------------------
# The target operating point
# --------------------------------------------------------------------------

#: Reference window for the count Fano factor, in time units. With the reference
#: rate of 1.0 this is two events per window on average. Chosen because it is
#: where all four mechanisms can be brought closest together: their Fano-vs-window
#: profiles genuinely differ, so they can be matched at one window, not at all of
#: them. That is the point -- discrimination has to come from the profile, from
#: autocorrelation, from phase-conditioning or from intervention, never from a
#: single dispersion number.
REFERENCE_WINDOW = 2.0

TARGET_RATE = 1.0
TARGET_DISPERSION = 3.5
TARGET_FANO = 3.22

#: Seed points from the coarse continuum scan, in grammar parameters.
SEEDS: dict[str, dict[str, float]] = {
    "hawkes": {"base_rate": 0.3065, "branching": 0.6935, "decay": 0.9283},
    "regime_switching": {
        "mult_low": 0.3022,
        "mult_high": 2.9188,
        "switch_rate": 0.2378,
        "p_high": 0.2667,
    },
    "seasonality": {"base_rate": 0.4372, "amplitude": 2.0048, "period": 13.3217},
    "poisson_mixture": {"rate_low": 0.455, "rate_high": 50.0, "weight_high": 0.55},
    "size_excitation": {"base_rate": 0.3065, "excitation": 0.6935, "decay": 0.9283},
}

GRIDS: dict[str, tuple[ParameterGrid, ...]] = {
    "hawkes": HAWKES_GRIDS,
    "regime_switching": TWO_STATE_GRIDS,
    "seasonality": PERIODIC_GRIDS,
    "poisson_mixture": POISSON_MIXTURE_GRIDS,
    "size_excitation": SIZE_EXCITED_GRIDS,
}

SEARCH_SEEDS = (Seed(101),)
VERIFY_SEEDS = (Seed(201), Seed(202), Seed(203), Seed(204), Seed(205))


def build_edit(name: str, parameters: FrozenDict[str, float]) -> Edit:
    """Return the edit for mechanism ``name`` at the given parameters."""
    match name:
        case "hawkes":
            return AddDependency(ARRIVAL, ARRIVAL, EXPONENTIAL_KERNEL, parameters)
        case "size_excitation":
            return AddDependency(SIZE, ARRIVAL, EXPONENTIAL_KERNEL, parameters)
        case "regime_switching":
            return AddLatentVariable(ARRIVAL, TWO_STATE_MARKOV, parameters)
        case "seasonality":
            return ReparameteriseComponent(ARRIVAL, POISSON_PERIODIC, parameters)
        case "poisson_mixture":
            return ChangeDistributionFamily(ARRIVAL, MIXTURE_OF_POISSON_2, parameters)
    raise SciAgentError(f"unknown mechanism {name!r}")


#: Tolerance on the closed-form mean rate, used to prune candidates before any
#: simulation. Tight, because the mean rate is a nuisance parameter: if the
#: mechanisms disagree on it, an investigator can partly identify the mechanism
#: from the marginal rate alone, which is a leak rather than a discriminator.
RATE_TOLERANCE = 0.006


def analytic_rate(name: str, p: FrozenDict[str, float]) -> float:
    """Return the stationary mean arrival rate in closed form.

    Every mechanism here has one, and it agrees with the simulated estimate to
    well within Monte Carlo error. Pruning on it rather than discovering it by
    simulation removes the mean rate from the search entirely: the simulation
    budget then goes wholly on matching dispersion and the Fano factor, which
    have no closed form.
    """
    match name:
        case "hawkes":
            # Kernel integrates to `branching`, so the cluster size is
            # geometric with mean 1/(1 - branching).
            return p["base_rate"] / (1.0 - p["branching"])
        case "size_excitation":
            # As Hawkes, with each mark's excitation scaled by its size; the
            # effective branching ratio is `excitation * E[size]`.
            return p["base_rate"] / (1.0 - p["excitation"] * REFERENCE_MEAN_SIZE)
        case "seasonality":
            # Time-average of `base_rate * exp(amplitude * cos(.))`.
            return p["base_rate"] * float(i0(p["amplitude"]))
        case "poisson_mixture":
            # Reciprocal of the mean hyperexponential gap.
            mean_gap = (
                p["weight_high"] / p["rate_high"]
                + (1.0 - p["weight_high"]) / p["rate_low"]
            )
            return 1.0 / mean_gap
        case "regime_switching":
            # The host component keeps the reference rate; the latent modulates
            # it, and `p_high` is the stationary probability of the high regime.
            occupancy = (
                p["mult_low"] * (1.0 - p["p_high"]) + p["mult_high"] * p["p_high"]
            )
            return REFERENCE_RATE * occupancy
    raise SciAgentError(f"no closed-form rate for mechanism {name!r}")


#: Mechanisms whose count autocorrelation must also be matched, and to what.
#: Only regime switching carries an entry, and its target is the Hawkes value.
#: v1 SPEC §4.2 assigns that pair to stage 3 of the minimum plan -- intervention --
#: so no dispersion diagnostic may separate them; without this term the search
#: leaves them separable at about 4 standard deviations on autocorrelation alone.
#: The mixture and seasonality are deliberately absent: they are *supposed* to be
#: separable by temporal structure, and constraining them here would destroy a
#: designed discriminator rather than close a leak.
TARGET_AUTOCORRELATION: dict[str, float] = {"regime_switching": 0.490}


@dataclass(frozen=True)
class Measurement:
    rate: float
    dispersion: float
    fano: float
    autocorrelation: float

    def loss(self, name: str) -> float:
        """Return the worst relative deviation from the target, on a log scale.

        The autocorrelation term applies only to mechanisms listed in
        :data:`TARGET_AUTOCORRELATION`, so a mechanism that is meant to be
        distinguishable by temporal structure is not penalised for being so.
        """
        deviations = [
            abs(math.log(self.rate / TARGET_RATE)),
            abs(math.log(self.dispersion / TARGET_DISPERSION)),
            abs(math.log(self.fano / TARGET_FANO)),
        ]
        target = TARGET_AUTOCORRELATION.get(name)
        if target is not None:
            deviations.append(abs(math.log(self.autocorrelation / target)))
        return max(deviations)


def measure(defect: Defect, n_events: int, seeds: Sequence[Seed]) -> Measurement:
    """Return the mean operating point of ``defect`` over ``seeds``."""
    grammar = edit_grammar()
    program = grammar.apply(reference_program(), defect)
    rates, dispersions, fanos, correlations = [], [], [], []
    for seed in seeds:
        log = program.execute(seed, n_events)
        rates.append(mean_rate(log))
        dispersions.append(inter_arrival_dispersion(log))
        fanos.append(fano_factor(log, REFERENCE_WINDOW))
        correlations.append(count_autocorrelation(log, REFERENCE_WINDOW))
    count = len(seeds)
    return Measurement(
        rate=math.fsum(rates) / count,
        dispersion=math.fsum(dispersions) / count,
        fano=math.fsum(fanos) / count,
        autocorrelation=math.fsum(correlations) / count,
    )


def neighbourhood(
    grids: tuple[ParameterGrid, ...], seed: dict[str, float], radius: int
) -> Iterator[FrozenDict[str, float]]:
    """Yield every on-grid point within ``radius`` steps of the snapped seed."""
    axes = []
    for grid in grids:
        centre = grid.values.index(grid.snap(seed[grid.name]))
        low = max(0, centre - radius)
        high = min(grid.size - 1, centre + radius)
        axes.append([grid.values[i] for i in range(low, high + 1)])
    names = [grid.name for grid in grids]
    for combination in itertools.product(*axes):
        yield FrozenDict[str, float](dict(zip(names, combination, strict=True)))


def calibrate(
    name: str, radius: int, search_events: int, verify_events: int, shortlist: int
) -> None:
    grids = GRIDS[name]
    candidates = list(neighbourhood(grids, SEEDS[name], radius))
    feasible = [
        parameters
        for parameters in candidates
        if abs(math.log(analytic_rate(name, parameters) / TARGET_RATE))
        <= RATE_TOLERANCE
    ]
    print(
        f"\n=== {name}: {len(candidates)} on-grid candidates, "
        f"{len(feasible)} within {100 * RATE_TOLERANCE:.1f}% of the target rate ===",
        flush=True,
    )
    if not feasible:
        print("  no candidate meets the rate constraint; widen --radius")
        return

    scored: list[tuple[float, FrozenDict[str, float]]] = []
    for parameters in feasible:
        defect = frozenset({build_edit(name, parameters)})
        try:
            measurement = measure(defect, search_events, SEARCH_SEEDS)
        except SciAgentError:
            continue
        scored.append((measurement.loss(name), parameters))
    scored.sort(key=lambda pair: pair[0])

    print(f"  re-measuring the top {shortlist} at {verify_events} events")
    best: tuple[float, FrozenDict[str, float], Measurement] | None = None
    for _, parameters in scored[:shortlist]:
        defect = frozenset({build_edit(name, parameters)})
        measurement = measure(defect, verify_events, VERIFY_SEEDS)
        if best is None or measurement.loss(name) < best[0]:
            best = (measurement.loss(name), parameters, measurement)

    if best is None:
        print("  no feasible candidate")
        return
    loss, parameters, measurement = best
    print(
        f"  best: rate={measurement.rate:.4f} cv2={measurement.dispersion:.4f} "
        f"F{REFERENCE_WINDOW:g}={measurement.fano:.4f} "
        f"ac={measurement.autocorrelation:.4f} "
        f"worst deviation {100 * (math.exp(loss) - 1):.1f}%"
    )
    body = ", ".join(f"{k}={v!r}" for k, v in sorted(parameters.items()))
    print(f"  parameters: {body}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="smaller search")
    parser.add_argument("--only", default=None, help="calibrate one mechanism")
    parser.add_argument(
        "--radius",
        type=int,
        default=None,
        help="grid steps searched per parameter; widen when the seed point's "
        "neighbourhood is clipped by a grid edge",
    )
    arguments = parser.parse_args()

    # The search phase dominates: it visits every grid neighbour, while the
    # verification phase only re-measures a shortlist. So the search runs short
    # and noisy and the shortlist is generous, and precision comes from
    # re-measuring the survivors at long run length over several seeds.
    #
    # The radius is in grid steps, so it must scale with GRID_SIZE to cover the
    # same span of parameter values: 5 steps on the 64-point grids reach about as
    # far as 3 did on 32-point grids, but sample the interval twice as finely.
    # Analytic pruning discards most of the neighbourhood, so the radius can be
    # generous: a wide net costs almost nothing once the rate constraint has
    # thinned it.
    radius = 4 if arguments.quick else 9
    search_events = 4_000 if arguments.quick else 10_000
    verify_events = 30_000 if arguments.quick else 80_000
    shortlist = 5 if arguments.quick else 15

    print(
        f"target: rate={TARGET_RATE} cv2={TARGET_DISPERSION} "
        f"F{REFERENCE_WINDOW:g}={TARGET_FANO}"
    )
    names = [arguments.only] if arguments.only else list(SEEDS)
    for name in names:
        if arguments.radius is not None:
            effective_radius = arguments.radius
        else:
            effective_radius = radius if len(GRIDS[name]) == 3 else max(1, radius - 1)
        calibrate(name, effective_radius, search_events, verify_events, shortlist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
