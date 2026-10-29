"""Fourth feasibility probe (design only; probe-only ids "probe-A2b", "probe-Bs").

(1) New A2 family (red team N1): theta+ = 0.5 fixed, theta_all = 0.49 (branching under
    inject_sign_high v = 0.99), theta0 = 0.75 - theta_all (observational rate ~1), member
    hawkes(theta_all + 0.25, same theta0). R = 48 per side, all designs. Crude a0 from
    overlapping subsamples of the 48 (32 for telling d-major, 16 for truthFSD e-major), e1 excluded.
(2) Size-gated B rung (N3): canonical form, and where it truncates (R = 8).
Run: nice -n 10 uv run --project $WT --no-sync python -I probe4.py > probe4-out.txt
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import probe
from probe import CH, gated, hawkes, run, spec

from sciagent.glm.canonical import canonicalise
from sciagent.glm.syntax import parse
from sciagent.investigation.world import OBSERVATIONAL, World
from sciagent.scoring.battery import hellinger2
from sciagent.scoring.seeds import TELLING, generator, scorer_seed
from sciagent.scoring.telling import Telling
from sciagent.systems.v2.policies import choose, design_menu, diagnostic_menu

R = 48


def a2() -> None:
    ta = 0.49
    truth, member = gated(0.5, ta, 0.75 - ta), hawkes(ta + 0.25, 0.75 - ta)
    menu = diagnostic_menu(
        World(truth, 0, truncate=True).datasets[OBSERVATIONAL].log, CH
    )
    labels = [c.label for c in menu]
    designs = design_menu(CH)
    runs = {}
    for e, des in enumerate(designs):
        for side, sim in enumerate((truth, member)):
            runs[(e, side)] = [
                run(
                    sim,
                    des,
                    generator(scorer_seed(TELLING, "probe-A2b", 0, side, e, r)),
                    menu,
                )
                for r in range(R)
            ]
            nt = sum(x["tau"] is not None for x in runs[(e, side)])
            ns = sum(x["stop"] is not None for x in runs[(e, side)])
            if nt or ns:
                print(
                    f"  {des.name} {'truth' if side == 0 else 'member'}: trunc {nt}/{R} stop {ns}/{R}"
                )
    keep = [e for e in range(len(designs)) if e != 1]

    def table(idx: list[int]) -> list[list[float]]:
        out = []
        for e in keep:
            a, b = runs[(e, 0)], runs[(e, 1)]
            out.append([
                hellinger2([None if a[r]["vals"] is None else a[r]["vals"][d] for r in idx],
                           [None if b[r]["vals"] is None else b[r]["vals"][d] for r in idx])
                for d in range(len(menu))
            ])  # fmt: skip
        return out

    full = table(list(range(R)))
    cells = sorted(((full[i][d], designs[e].name, labels[d]) for i, e in enumerate(keep)
                    for d in range(len(menu))), reverse=True)[:5]  # fmt: skip
    print(
        f"A2 new family theta_all={ta}: top cells off e1 at R={R}: "
        + "; ".join(f"{n}/{lab} {h:.3f}" for h, n, lab in cells)
    )
    rng = generator(scorer_seed(TELLING, "probe-A2b-boot", 0))
    p32: dict[str, float] = {}
    p16: dict[str, float] = {}
    B = 200
    for _ in range(B):
        i32 = sorted(rng.choice(R, 32, replace=False).tolist())
        t = Telling.from_table(
            tuple(map(tuple, table(i32))), tuple(keep), tuple(labels)
        )
        p32[labels[t.diagnostic_index]] = p32.get(labels[t.diagnostic_index], 0) + 1 / B
        i16 = sorted(rng.choice(R, 16, replace=False).tolist())
        _, d = choose(tuple(map(tuple, table(i16))))
        p16[labels[d]] = p16.get(labels[d], 0) + 1 / B
    a0 = sum(p * p16.get(lab, 0.0) for lab, p in p32.items())
    print(f"crude p32 {dict(sorted(p32.items(), key=lambda x: -x[1]))}")
    print(f"crude p16 {dict(sorted(p16.items(), key=lambda x: -x[1]))}")
    print(f"crude a0 = {a0:.3f} (overlapping subsamples of {R}; {B} draws)")


def size_rung() -> None:
    for dsl in ("link=identity; Excite(ExpK, Mark(size), all) + Excite(ExpK, One, all)",
                "link=identity; Excite(ExpK, One, all) + Excite(ExpK, Mark(size), all)"):  # fmt: skip
        s = parse(dsl, CH)
        print(f"{dsl}: canonical={canonicalise(s) == s}")
    dsl = next(d for d in ("link=identity; Excite(ExpK, Mark(size), all) + Excite(ExpK, One, all)",
                           "link=identity; Excite(ExpK, One, all) + Excite(ExpK, Mark(size), all)")
               if canonicalise(parse(d, CH)) == parse(d, CH))  # fmt: skip
    first_is_size = "Mark(size), all) +" in dsl
    thetas = (0.5, 0.35) if first_is_size else (0.35, 0.5)
    probe.pair("Bs", spec(dsl, 0.15, thetas), hawkes(0.85, 0.15), 8)


def main() -> None:
    t0 = time.process_time()
    a2()
    print(f"A2 part CPU {time.process_time() - t0:.1f}s")
    size_rung()
    print(f"total probe4 CPU {time.process_time() - t0:.1f}s")


if __name__ == "__main__":
    main()
