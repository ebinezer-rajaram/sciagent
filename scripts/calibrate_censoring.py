"""Search the censoring window that makes scenario S12 a garden path.

SPEC §4.5 defines S12 as regime switching "plus an observation-level censoring
nuisance that produces a strong spurious periodic signature in the first two
diagnostics", testing "plan revision, recovery". Both halves are claims about a
*parameterisation* and neither is free:

* **Garden path.** The censoring has to be strong enough that the first
  diagnostics read as a mechanism the truth is not. A window that drops a few
  events is a nuisance nobody is led astray by.
* **Recovery.** It must not also destroy the route back. If the censoring period
  coincided with the period a seasonality hypothesis proposes
  (``catalogue.CANDIDATE_PERIOD``), phase-conditioning would *remove* the
  spurious dispersion and confirm seasonality -- a trap with no exit, which is a
  different scenario from the one the specification asks for.

So the search reports, for each candidate window, the four slice diagnostics
under regime switching plus censoring, next to what each closed-set hypothesis
produces on its own. What to look for: the arrival diagnostics displaced well
away from regime switching's own values and towards the periodic mechanism's, and
the phase-conditioned dispersion *not* collapsing to 1.

Usage::

    uv run python scripts/calibrate_censoring.py [--quick]
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence

import numpy as np

from environments.pointproc.catalogue import CANDIDATE_PERIOD, metric_registry
from environments.pointproc.components import IDENTITY_PERIODIC_CENSORED, OBS
from environments.pointproc.grammar import CENSORING_GRIDS
from environments.pointproc.mechanisms import REGIME_SWITCHING, on_grid
from environments.pointproc.outcomes import (
    N_EVENTS,
    closed_set,
    executor,
    simulator,
    slice_designs,
    slice_templates,
)
from sciagent.core.edits import ChangeDistributionFamily, Defect
from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import Seed
from sciagent.experiments.dsl import ExperimentDesign, QueryDiagnostic
from sciagent.inference.binning import Discretisation, OutcomeSpace
from sciagent.inference.empirical import EmpiricalTable

#: Candidate windows: (period, observed fraction). Periods are kept away from
#: ``CANDIDATE_PERIOD`` and from its low harmonics, since a censoring cycle that
#: divides the candidate period is phase-locked to it in the phase-conditioned
#: diagnostic's eyes and would confirm seasonality rather than suggest it.
CANDIDATES: tuple[tuple[float, float], ...] = (
    (4.0, 0.7),
    (4.0, 0.5),
    (7.0, 0.7),
    (7.0, 0.6),
    (7.0, 0.5),
    (7.0, 0.4),
    (20.0, 0.6),
    (20.0, 0.5),
    (40.0, 0.6),
)


def censoring(period: float, duty: float) -> ChangeDistributionFamily:
    """Return the censoring edit at a grid point of the grammar."""
    return ChangeDistributionFamily(
        target=OBS,
        family=IDENTITY_PERIODIC_CENSORED,
        parameters=on_grid(CENSORING_GRIDS, period=period, duty=duty),
    )


def diagnostics(
    defect: Defect, seeds: Sequence[Seed]
) -> dict[str, tuple[float, float]]:
    """Return mean and standard deviation of each slice design under ``defect``."""
    runner = executor()
    summary: dict[str, tuple[float, float]] = {}
    for design in slice_designs():
        name = str(design.metrics[0])
        draws = np.array(
            [runner.measure(design, defect, seed)[0] for seed in seeds],
            dtype=np.float64,
        )
        summary[name] = (float(np.mean(draws)), float(np.std(draws, ddof=1)))
    return summary


#: SPEC §4.2 gives seasonality "a fixed spectral peak", and §4.5 asks S12's
#: nuisance for a *spurious* one. Neither metric is in a slice design -- the
#: posterior is not calibrated on them -- but both are in SPEC §4.3's catalogue,
#: and whether the signature is there at all is a question about the environment
#: rather than about the posterior.
SPECTRAL = ("spectral_peak_frequency", "spectral_peak_prominence")

#: Seed of the reduced-replicate tables the recovery report is read off. Distinct
#: from any table the posterior is calibrated on: this is a calibration
#: measurement, and reading it off the draws a later gate uses would tune the
#: scenario to its own test set.
RECOVERY_SEED = Seed(20260913)


def spectra(defect: Defect, seeds: Sequence[Seed]) -> dict[str, tuple[float, float]]:
    """Return mean and standard deviation of the two spectral diagnostics."""
    runner = executor()
    registry = metric_registry()
    summary: dict[str, tuple[float, float]] = {}
    for name in SPECTRAL:
        spec = registry.spec(name)
        design = ExperimentDesign(
            operation=QueryDiagnostic(),
            outcome=OutcomeSpace(
                axes=(
                    Discretisation(
                        metric=spec.ref,
                        interior=(max(spec.low, 0.0) + 1.0,),
                        low=spec.low,
                        high=spec.high,
                    ),
                )
            ),
            n_events=N_EVENTS,
        )
        draws = np.array(
            [runner.measure(design, defect, seed)[0] for seed in seeds],
            dtype=np.float64,
        )
        summary[name] = (float(np.mean(draws)), float(np.std(draws, ddof=1)))
    return summary


def show(
    label: str, summary: dict[str, tuple[float, float]], columns: Sequence[str]
) -> None:
    cells = "  ".join(f"{summary[name][0]:9.3f}" for name in columns)
    spread = "  ".join(f"{summary[name][1]:9.3f}" for name in columns)
    print(f"{label:<34s}  {cells}")
    print(f"{'  (sd)':<34s}  {spread}")


def recoverable(replicates: int) -> None:
    """Report which hypothesis the censored world is closest to, per candidate.

    The half of the calibration the diagnostics cannot see. A garden path has to
    be *left*, and a belief accumulating evidence from the slice's designs
    converges on whichever closed-set hypothesis minimises the Kullback-Leibler
    divergence from the world it is actually watching -- so if that hypothesis is
    not regime switching, no policy recovers S12's truth however long it looks,
    and the scenario is a trap rather than a garden path.

    Reported as the expected log-likelihood per experiment, summed over designs:
    KL up to a constant that is the same for every hypothesis, and readable
    directly as "bits per experiment against the leader".
    """
    templates = slice_templates()
    names = sorted(closed_set())
    worlds = {
        (period, duty): frozenset({REGIME_SWITCHING, censoring(period, duty)})
        for period, duty in CANDIDATES
    }
    print(f"\nWhich hypothesis the censored world converges on ({replicates} reps)")
    print(f"{'case':<34s}  " + "  ".join(f"{name[:11]:>11s}" for name in names))
    print("-" * (36 + 13 * len(names)))

    table, _ = EmpiricalTable.build(
        defects=[closed_set()[name] for name in names],
        templates=templates,
        simulate=simulator(),
        replicates=replicates,
        seed=RECOVERY_SEED,
    )
    for world in worlds.values():
        try:
            table, _ = table.with_structure(world, simulator())
        except MalformedDesignError:
            # A window that leaves an experiment with nothing to measure is a
            # candidate the environment cannot run, not a crash: it is reported
            # below and the search carries on, since the point is to find the
            # windows it can run.
            continue
    for (period, duty), world in worlds.items():
        if not table.holds(world):
            snapped = censoring(period, duty).parameters
            print(
                f"regime + censor p={snapped['period']:.2f} "
                f"d={snapped['duty']:.2f}   -- unreadable: some design keeps "
                f"fewer than two events"
            )
            continue
        scores = {
            name: math.fsum(
                weight * math.log2(probability)
                for template in templates
                for weight, probability in zip(
                    table.probabilities(world, template.id),
                    table.probabilities(closed_set()[name], template.id),
                    strict=True,
                )
            )
            for name in names
        }
        leader = max(names, key=lambda name: scores[name])
        cells = "  ".join(f"{scores[name]:11.3f}" for name in names)
        snapped = censoring(period, duty).parameters
        label = f"regime + censor p={snapped['period']:.2f} d={snapped['duty']:.2f}"
        print(f"{label:<34s}  {cells}   -> {leader}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--recovery-replicates",
        type=int,
        default=None,
        help="replicates behind the convergence report (default 200, 60 quick)",
    )
    arguments = parser.parse_args()
    seeds = [Seed(4000 + i) for i in range(15 if arguments.quick else 40)]
    recovery = arguments.recovery_replicates or (60 if arguments.quick else 200)

    columns = [str(design.metrics[0]) for design in slice_designs()]
    header = "  ".join(f"{name[:9]:>9s}" for name in columns)
    print(f"\nSlice diagnostics ({len(seeds)} seeds). Columns: {columns}")
    print(f"{'case':<34s}  {header}")
    print("-" * (36 + len(header)))

    for name in sorted(closed_set()):
        show(f"{name} (uncensored)", diagnostics(closed_set()[name], seeds), columns)

    print()
    for period, duty in CANDIDATES:
        edit = censoring(period, duty)
        snapped = (edit.parameters["period"], edit.parameters["duty"])
        label = f"regime + censor p={snapped[0]:.2f} d={snapped[1]:.2f}"
        show(label, diagnostics(frozenset({REGIME_SWITCHING, edit}), seeds), columns)

    print(f"\nSpectral signature ({len(seeds)} seeds). Columns: {list(SPECTRAL)}")
    print(f"{'case':<34s}  " + "  ".join(f"{name[:9]:>9s}" for name in SPECTRAL))
    print("-" * (36 + 22))
    for name in ("null", "regime_switching", "seasonality"):
        show(f"{name} (uncensored)", spectra(closed_set()[name], seeds), SPECTRAL)
    for period, duty in CANDIDATES:
        edit = censoring(period, duty)
        label = (
            f"regime + censor p={edit.parameters['period']:.2f} "
            f"d={edit.parameters['duty']:.2f}"
        )
        show(label, spectra(frozenset({REGIME_SWITCHING, edit}), seeds), SPECTRAL)

    recoverable(recovery)

    print(
        f"\nCandidate period of a seasonality hypothesis: {CANDIDATE_PERIOD:.3f}. "
        f"A censoring period near it, or near {CANDIDATE_PERIOD / 2:.3f} or "
        f"{CANDIDATE_PERIOD / 3:.3f}, is phase-locked to it."
    )
    print(
        "Look for: phase_conditioned_dispersion well above 1 (seasonality is "
        "refutable), inter_arrival_dispersion and count_autocorrelation far from "
        "regime switching's own values (the path is garden), and mean_rate under "
        f"the burst still elevated (the recovery route survives). Reference "
        f"burst response for regime switching: "
        f"{diagnostics(closed_set()['regime_switching'], seeds)['mean_rate'][0]:.3f}"
    )
    print(
        f"Metric registry version {metric_registry().version}, "
        f"log2 CANDIDATE_PERIOD ratio check {math.log2(CANDIDATE_PERIOD):.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
