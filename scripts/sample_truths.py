"""Sample a split of pointproc truths (SPEC §3) and write it with its manifest.

Usage::

    uv run python scripts/sample_truths.py --split dev --n 20 --seed 2026 \\
        --out data/v2/truths/dev --workers 10

Writes ``truths.json`` (canonical JSON; its SHA-256 is the split's hash),
``manifest.json`` (deterministic: config digest, hash, counts, rejection
tallies) and ``timing.json`` (wall clock, never hashed), then prints a summary.

The **test** split's records must never be committed: SPEC §3 commits only its
hash, before any agent touches it. This script refuses to write a test split
inside the repository; write it elsewhere and commit ``records_sha256`` from
its manifest (a P4 preregistration step, SPEC §6.1).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

# One BLAS thread per process, set before numpy loads (spawned workers inherit
# the environment). With a thread per core, OpenBLAS commits ~0.7 GB at import
# (33 MB pinned) and a running worker ~2.2 GB on the reference machine; eight
# such workers exhausted memory. The parallelism is over candidates instead.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

if TYPE_CHECKING:
    from sciagent.scenarios.sampler import SplitResult

REPO = Path(__file__).resolve().parents[1]


def _inside_repo(path: Path) -> bool:
    return path.resolve().is_relative_to(REPO)


def summarise(result: SplitResult) -> str:
    m, t, recs = result.manifest, result.timing, result.records
    lines = [
        f"split {m['split']}: {len(recs)} truths, seed {m['seed']}",
        f"records sha256 {m['records_sha256']}",
        f"config digest  {m['config_digest']}",
        f"strata {m['strata']['counts']}  links {m['links']}",
        f"out-of-dictionary {m['out_of_dictionary']['count']}/{m['n']}",
    ]
    for kind, c in m["candidates"].items():
        rate = c["accepted"] / c["evaluated"]
        lines.append(
            f"{kind}: {c['accepted']}/{c['evaluated']} accepted ({rate:.0%}); "
            f"{c['outcomes']}"
        )
    rates = [r.calibration.mean_rate for r in recs]
    fanos = [r.calibration.fano for r in recs]
    held = [r.identifiability.held_out_mean_rate for r in recs]
    margins = [r.identifiability.min_margin for r in recs]
    lines += [
        f"calibration mean rate {min(rates):.3f}..{max(rates):.3f}; "
        f"held-out rate {min(held):.3f}..{max(held):.3f} "
        f"(median {statistics.median(held):.3f})",
        f"Fano(2) {min(fanos):.2f}..{max(fanos):.2f} "
        f"(median {statistics.median(fanos):.2f})",
        f"min identifiability margin {min(margins):.4f}..{max(margins):.4f} nats/event",
        f"truth fit recovered the truth's ψ: "
        f"{sum(r.identifiability.truth_psi_recovered for r in recs)}/{len(recs)}",
        f"wall {t['wall_time_s']:.0f} s on {t['workers']} worker(s); "
        f"{t['wall_time_per_accepted_s']:.1f} s wall per accepted truth; "
        f"candidate CPU {t['candidate_cpu_s']:.0f} s",
    ]
    for r in recs:
        lines.append(
            f"  {r.id} {r.stratum:4} {'ood' if r.out_of_dictionary else 'ind'} "
            f"d={r.nearest_distance:.3f} rate={r.calibration.mean_rate:.3f} "
            f"fano={r.calibration.fano:.2f} margin={r.identifiability.min_margin:.3f} "
            f"{r.dsl}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", required=True, choices=("dev", "test"))
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--candidate-timeout",
        type=float,
        default=3600.0,
        help="wall-clock cap per candidate in seconds; exceeding it aborts the run",
    )
    args = parser.parse_args(argv)
    if args.split == "test" and _inside_repo(args.out):
        parser.error(
            "the test split's records must not be written inside the repository "
            "(SPEC §3: only its hash is committed)"
        )
    # Imported here, after the thread pins above are in the environment.
    from environments.pointproc.truths_v2 import SAMPLER_CONFIG, environment
    from sciagent.scenarios.records import write_split
    from sciagent.scenarios.sampler import sample_split

    result = sample_split(
        SAMPLER_CONFIG,
        environment(),
        split=args.split,
        n=args.n,
        seed=args.seed,
        workers=args.workers,
        candidate_timeout_s=args.candidate_timeout,
    )
    write_split(args.out, result)
    print(summarise(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
