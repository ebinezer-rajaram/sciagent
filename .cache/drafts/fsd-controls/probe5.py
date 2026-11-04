"""Fifth probe (design only; probe-only id "probe-A2c"): e1's row in the v3 A2 family at the first
bisection point theta_all = 0.465 (theta+ = 0.5, theta0 = 0.285; member hawkes(0.715, 0.285)), R = 32.
Prints truncations, e1's top cells under (c) and with truncated runs dropped, and the top cells off e1.
Run: nice -n 10 uv run --project $WT --no-sync python -I probe5.py > probe5-out.txt"""

from __future__ import annotations
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import CH, gated, hawkes, run
from sciagent.investigation.world import OBSERVATIONAL, World
from sciagent.scoring.battery import hellinger2
from sciagent.scoring.seeds import TELLING, generator, scorer_seed
from sciagent.systems.v2.policies import design_menu, diagnostic_menu

R, TA = 32, 0.465
t0 = time.process_time()
truth, member = gated(0.5, TA, 0.75 - TA), hawkes(TA + 0.25, 0.75 - TA)
menu = diagnostic_menu(World(truth, 0, truncate=True).datasets[OBSERVATIONAL].log, CH)
labels = [c.label for c in menu]
designs = design_menu(CH)
runs = {
    (e, s): [
        run(sim, d, generator(scorer_seed(TELLING, "probe-A2c", 0, s, e, r)), menu)
        for r in range(R)
    ]
    for e, d in enumerate(designs)
    for s, sim in enumerate((truth, member))
}
for (e, s), rs in sorted(runs.items()):
    nt = sum(x["tau"] is not None for x in rs)
    ns = sum(x["stop"] is not None for x in rs)
    if nt or ns:
        print(
            f"{designs[e].name} {'truth' if s == 0 else 'member'}: trunc {nt}/{R}, stop {ns}/{R}"
        )


def row(e, drop):
    a = [x for x in runs[(e, 0)] if not (drop and x["tau"] is not None)]
    b = runs[(e, 1)]
    return [
        hellinger2(
            [None if x["vals"] is None else x["vals"][d] for x in a],
            [None if x["vals"] is None else x["vals"][d] for x in b],
        )
        for d in range(len(menu))
    ]


for drop in (False, True):
    r1 = row(1, drop)
    top = sorted(((h, labels[d]) for d, h in enumerate(r1)), reverse=True)[:4]
    print(
        f"e1 ({designs[1].name}) row, {'truncated runs dropped' if drop else 'rule (c)'}: "
        + "; ".join(f"{lab} {h:.4f}" for h, lab in top)
    )
off = sorted(
    (
        (h, designs[e].name, labels[d])
        for e in range(len(designs))
        if e != 1
        for d, h in enumerate(row(e, False))
    ),
    reverse=True,
)[:4]
print("top off e1, rule (c): " + "; ".join(f"{n}/{lab} {h:.4f}" for h, n, lab in off))
med = sorted(x.get("n", 0) for x in runs[(1, 0)] if x["tau"] is None)
print(
    f"e1 truth non-truncated event counts: median {med[len(med) // 2] if med else '-'}; "
    f"member median {sorted(x['n'] for x in runs[(1, 1)])[R // 2]}"
)
print(f"probe5 CPU {time.process_time() - t0:.1f}s")
