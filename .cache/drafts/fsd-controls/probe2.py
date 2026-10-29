"""Second feasibility probe (design only): Control B's sign=- truth, and the
labels that are exactly 1.0 on each capped row (the K2z / Fz prediction).

Reuses probe.py's builders; probe-only streams "probe-B2", "probe-A1s" (no
control stream uses these truth ids). Run as probe.py, output probe2-out.txt.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe
from probe import CH, gated, hawkes, run, spec
from sciagent.systems.v2.policies import design_menu, diagnostic_menu

from sciagent.investigation.world import OBSERVATIONAL, World
from sciagent.scoring.battery import hellinger2
from sciagent.scoring.seeds import TELLING, generator, scorer_seed

NEG = "link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)"


def saturated(name: str, truth: object, member: object, e: int, reps: int) -> None:
    world = World(truth, 0, truncate=True)
    menu = diagnostic_menu(world.datasets[OBSERVATIONAL].log, CH)
    des = design_menu(CH)[e]
    sides = [
        [run(sim, des, generator(scorer_seed(TELLING, f"probe-{name}", 0, s, e, r)), menu)
         for r in range(reps)]
        for s, sim in enumerate((truth, member))
    ]  # fmt: skip
    lab = []
    for d, c in enumerate(menu):
        va = [None if x["vals"] is None else x["vals"][d] for x in sides[0]]
        vb = [None if x["vals"] is None else x["vals"][d] for x in sides[1]]
        if hellinger2(va, vb) == 1.0:
            lab.append(c.label)
    nt = [sum(x["tau"] is not None for x in s) for s in sides]
    print(f"{name} {des.name}: trunc truth {nt[0]}/{reps} member {nt[1]}/{reps}; "
          f"He2 == 1.0 on {len(lab)}/35, menu order: {lab}")  # fmt: skip


def main() -> None:
    t0 = time.process_time()
    s = spec(NEG, 0.15, (0.5, 0.6))
    print(f"sign=- structure canonical; features {s.structure.features}")
    probe.pair("B2", s, hawkes(0.85, 0.15), 8)
    saturated("A1s", gated(0.5, 0.6, 0.15), hawkes(0.85, 0.15), 1, 16)
    print(f"\ntotal probe2 CPU {time.process_time() - t0:.1f}s")


if __name__ == "__main__":
    main()
