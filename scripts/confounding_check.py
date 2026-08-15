"""Measure the confounding of the four mechanisms. Do not assume it.

SPEC §4.2 claims the four mechanisms are "indistinguishable under basic
dispersion diagnostics". If that is false the whole vertical slice is worthless,
so this script reports the numbers rather than asserting the claim:

* **Population moments** at long run length, which pin down what each mechanism
  actually does.
* **Single-investigation moments** at a realistic run length, over many seeds,
  which is what an investigator would actually see.
* **Separability**, as the standardised difference between each pair of
  mechanisms on each statistic. Below about 1.0 a single run cannot tell the
  pair apart; a value of 3 or more means one number settles it.

The verdict to look for: separability near zero for mean rate, inter-arrival
dispersion and the Fano factor at the reference window, and large somewhere in
the Fano-versus-window profile or the count autocorrelation. That is the
designed structure -- no single diagnostic resolves the mechanisms, but a
multi-stage plan does (SPEC §4.2).

Usage::

    uv run python scripts/confounding_check.py [--quick]
"""

from __future__ import annotations

import argparse
import itertools
import math
from collections.abc import Sequence

import numpy as np

from environments.pointproc import (
    CONFOUNDED_MECHANISMS,
    SIZE_EXCITATION,
    edit_grammar,
    mechanism_defect,
    reference_program,
)
from environments.pointproc.diagnostics import (
    count_autocorrelation,
    fano_factor,
    inter_arrival_dispersion,
    mean_rate,
)
from sciagent.core import reductions
from sciagent.core.edits import Defect
from sciagent.core.types import Seed

REFERENCE_WINDOW = 2.0
WINDOWS = (0.5, 1.0, 2.0, 5.0, 10.0, 25.0)

#: The four mechanisms of SPEC §4.2, plus the undefective reference and the
#: out-of-library mechanism of scenario S11.
CASES: dict[str, Defect] = {
    "reference (null)": frozenset(),
    **{name: mechanism_defect(name) for name in CONFOUNDED_MECHANISMS},
    "size_excitation (S11)": frozenset({SIZE_EXCITATION}),
}

#: Only these four are required to be mutually confounded. The null is expected
#: to sit at 1.0 everywhere, and S11 is a fifth mechanism the agent grammar
#: cannot express.
CONFOUNDED = tuple(CONFOUNDED_MECHANISMS)


def statistics(defect: Defect, seed: Seed, n_events: int) -> dict[str, float]:
    """Return every dispersion statistic for one run."""
    program = edit_grammar().apply(reference_program(), defect)
    log = program.execute(seed, n_events)
    values = {"rate": mean_rate(log), "cv2": inter_arrival_dispersion(log)}
    for window in WINDOWS:
        values[f"F{window:g}"] = fano_factor(log, window)
    values["ac"] = count_autocorrelation(log, REFERENCE_WINDOW)
    return values


def table(
    label: str, n_events: int, seeds: Sequence[Seed]
) -> dict[str, dict[str, tuple[float, float]]]:
    """Print and return mean and standard deviation per case per statistic."""
    columns = ["rate", "cv2", *[f"F{w:g}" for w in WINDOWS], "ac"]
    print(f"\n{label}  (n_events={n_events}, {len(seeds)} seeds)")
    header = "  ".join(f"{name:>9s}" for name in columns)
    print(f"{'mechanism':<24s}  {header}")
    print("-" * (26 + len(header)))

    results: dict[str, dict[str, tuple[float, float]]] = {}
    for case, defect in CASES.items():
        samples = [statistics(defect, seed, n_events) for seed in seeds]
        summary = {}
        for column in columns:
            draws = np.array([sample[column] for sample in samples], dtype=np.float64)
            spread = reductions.deviation(draws) if len(seeds) > 1 else 0.0
            summary[column] = (reductions.mean(draws), spread)
        results[case] = summary
        cells = "  ".join(f"{summary[column][0]:9.3f}" for column in columns)
        print(f"{case:<24s}  {cells}")

    if len(seeds) > 1:
        print(f"{'(standard deviation)':<24s}  " + "  ".join(" " * 9 for _ in columns))
        for case in CASES:
            cells = "  ".join(f"{results[case][column][1]:9.3f}" for column in columns)
            print(f"{'  ' + case:<24s}  {cells}")
    return results


def separability(results: dict[str, dict[str, tuple[float, float]]]) -> None:
    """Print the standardised difference between each confounded pair."""
    columns = ["rate", "cv2", *[f"F{w:g}" for w in WINDOWS], "ac"]
    print("\nSeparability: |mean difference| / pooled standard deviation")
    print("A single run separates a pair on a statistic when this exceeds ~3.")
    header = "  ".join(f"{name:>9s}" for name in columns)
    print(f"{'pair':<40s}  {header}")
    print("-" * (42 + len(header)))

    worst_at_reference = 0.0
    for left, right in itertools.combinations(CONFOUNDED, 2):
        cells = []
        for column in columns:
            left_mean, left_sd = results[left][column]
            right_mean, right_sd = results[right][column]
            pooled = math.sqrt((left_sd**2 + right_sd**2) / 2.0)
            score = abs(left_mean - right_mean) / pooled if pooled > 0 else math.inf
            cells.append(score)
            if column in ("rate", "cv2", f"F{REFERENCE_WINDOW:g}"):
                worst_at_reference = max(worst_at_reference, score)
        rendered = "  ".join(f"{score:9.2f}" for score in cells)
        print(f"{left + ' vs ' + right:<40s}  {rendered}")

    print(
        f"\nWorst separability at the reference operating point "
        f"(rate, cv2, F{REFERENCE_WINDOW:g}): {worst_at_reference:.2f}"
    )
    if worst_at_reference < 3.0:
        print("=> confounded: no single reference-point moment separates any pair.")
    else:
        print("=> NOT confounded: a single reference-point moment separates a pair.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    arguments = parser.parse_args()

    long_events = 20_000 if arguments.quick else 150_000
    run_events = 2_000
    n_seeds = 12 if arguments.quick else 60

    table(
        "POPULATION MOMENTS -- what each mechanism actually does",
        long_events,
        [Seed(900 + i) for i in range(3)],
    )
    results = table(
        "SINGLE INVESTIGATION -- what an investigator actually sees",
        run_events,
        [Seed(1000 + i) for i in range(n_seeds)],
    )
    separability(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
