"""Third feasibility probe (design only): how often A1's truth fails to truncate
on inject_sign_high (theta+ = 0.5 vs 0.6), and the A1 rung theta+ = 0.6.

Probe-only streams "probe-A1nc5", "probe-A1nc6", "probe-A16". Output probe3-out.txt.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe
from probe import CH, gated, hawkes, run
from sciagent.systems.v2.policies import design_menu, diagnostic_menu

from sciagent.investigation.world import OBSERVATIONAL, World
from sciagent.scoring.seeds import TELLING, generator, scorer_seed


def noncap(name: str, tp: float, reps: int) -> None:
    truth = gated(tp, 0.85 - tp / 2, 0.15)
    menu = diagnostic_menu(
        World(truth, 0, truncate=True).datasets[OBSERVATIONAL].log, CH
    )[:1]
    e = 1
    des = design_menu(CH)[e]
    rs = [run(truth, des, generator(scorer_seed(TELLING, f"probe-{name}", 0, 0, e, r)), menu)
          for r in range(reps)]  # fmt: skip
    nt = sum(x["tau"] is not None for x in rs)
    ns = sum(x["stop"] is not None for x in rs)
    taus = sorted(x["tau"] for x in rs if x["tau"] is not None)
    other = sorted(round(x["vals"][0], 2) for x in rs if x["tau"] is None and x["vals"])
    print(f"{name}: theta+={tp} high branching {0.85 + tp / 2:.2f} on {des.name}: "
          f"trunc {nt}/{reps}, stop {ns}/{reps}, tau min/max {taus[0]:.0f}/{taus[-1]:.0f}, "
          f"non-truncated mean_rate {other}")  # fmt: skip


def main() -> None:
    t0 = time.process_time()
    noncap("A1nc5", 0.5, 64)
    noncap("A1nc6", 0.6, 64)
    probe.pair("A16", gated(0.6, 0.55, 0.15), hawkes(0.85, 0.15), 8)
    print(f"\ntotal probe3 CPU {time.process_time() - t0:.1f}s")


if __name__ == "__main__":
    main()
