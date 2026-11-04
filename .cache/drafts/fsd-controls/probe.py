"""Feasibility probe for the at-stop-rule controls (design only, NOT the controls).

Throwaway, 2026-10-29. Builds the planted truths/members of criteria.md and
checks, at small R on probe-only streams, (1) where each side truncates or
stops, (2) per-run CPU, (3) whether the top non-capping cell is unsaturated
and not mean_rate. Streams: scorer_seed(TELLING, "probe-<name>", 0, side, e, r):
the truth ids "probe-*" are used by no control stream, so the controls' draws
are not seen here. Imports the read-only worktree (bff61ec); src is not edited.

Run:
  WT=/home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist
  nice -n 10 uv run --project $WT --no-sync python -I probe.py > probe-out.txt
"""

from __future__ import annotations

import math
import sys
import time
from typing import Any

import numpy as np
from sciagent.systems.v2.policies import (
    POLICY_MAX_EVENTS,
    STOPPED,
    design_menu,
    diagnostic_menu,
    diagnostic_value,
)

import sciagent
from environments.pointproc import v2
from sciagent.diagnostics import catalogue
from sciagent.glm.canonical import canonicalise
from sciagent.glm.grammar import PsiSlot
from sciagent.glm.interventions import run_experiment_truncating
from sciagent.glm.simulate import Coefficients
from sciagent.glm.syntax import parse
from sciagent.investigation.view import ViewError, clip_to_analysis
from sciagent.investigation.world import OBSERVATIONAL, TruthSpec, World
from sciagent.scoring.battery import _FLOOR, _moments, hellinger2
from sciagent.scoring.seeds import TELLING, generator, scorer_seed

CH = v2.CHANNELS
GATED = "link=identity; Excite(ExpK, One, sign=+) + Excite(ExpK, One, all)"
HAWKES = "link=identity; Excite(ExpK, One, all)"
RATE = {(): 1.0}


def spec(dsl: str, mu: float, thetas: tuple[float, ...]) -> TruthSpec:
    s = parse(dsl, CH)
    if canonicalise(s) != s:
        raise SystemExit(f"not canonical: {dsl}")
    psi = tuple({PsiSlot((0,), "exp_rate"): 1.0} for _ in s.features)
    return TruthSpec(
        s, psi, Coefficients(mu, tuple((t,) for t in thetas)), CH, v2.mark_sampler
    )


def gated(tp: float, ta: float, mu: float) -> TruthSpec:
    return spec(GATED, mu, (tp, ta))


def hawkes(n: float, mu: float) -> TruthSpec:
    return spec(HAWKES, mu, (n,))


def run(
    t: TruthSpec, design: Any, rng: np.random.Generator, menu: Any
) -> dict[str, Any]:
    t0 = time.process_time()
    try:
        ds, tau = run_experiment_truncating(
            t.structure, t.psi, t.coef, t.channels, t.mark_sampler,
            design.experiment, rng, max_events=POLICY_MAX_EVENTS, label=design.name,
        )  # fmt: skip
    except STOPPED as error:
        return {"stop": type(error).__name__, "tau": None, "vals": None,
                "cpu": time.process_time() - t0}  # fmt: skip
    log = ds.log
    if tau is not None:
        try:
            log = clip_to_analysis(ds, tau).log
        except ViewError:
            return {"stop": "cut0", "tau": tau, "vals": None,
                    "cpu": time.process_time() - t0}  # fmt: skip
    vals: list[float | None] = []
    for c in menu:
        try:
            vals.append(diagnostic_value(c, log, CH))
        except catalogue.DiagnosticError:
            vals.append(None)
    return {"stop": None, "tau": tau, "vals": vals, "n": ds.log.n,
            "cpu": time.process_time() - t0}  # fmt: skip


def bhatt(a: list[float | None], b: list[float | None]) -> float:
    """D = -ln rho of hellinger2's atom+Gaussian fits, in log space (inf if rho = 0)."""
    da = [v for v in a if v is not None]
    db = [v for v in b if v is not None]
    p = (len(a) - len(da)) / len(a)
    q = (len(b) - len(db)) / len(b)
    terms = []
    if p > 0 and q > 0:
        terms.append(0.5 * (math.log(p) + math.log(q)))
    if da and db:
        m1, v1 = _moments(da)
        m2, v2 = _moments(db)
        fl = (_FLOOR * (1.0 + abs(m1) + abs(m2))) ** 2
        v1, v2 = max(v1, fl), max(v2, fl)
        tot = v1 + v2
        lbc = 0.5 * math.log(2.0 * math.sqrt(v1 * v2) / tot) - (m1 - m2) ** 2 / (
            4 * tot
        )
        terms.append(0.5 * (math.log1p(-p) + math.log1p(-q)) + lbc)
    if not terms:
        return math.inf
    mx = max(terms)
    return -(mx + math.log(sum(math.exp(x - mx) for x in terms)))


def pair(name: str, truth: TruthSpec, member: TruthSpec, reps: int,
         only: tuple[int, ...] | None = None) -> None:  # fmt: skip
    world = World(truth, 0, truncate=True)
    obs = world.datasets[OBSERVATIONAL]
    menu = diagnostic_menu(obs.log, CH)
    labels = [c.label for c in menu]
    designs = design_menu(CH)
    print(f"\n=== {name}: obs n={obs.log.n}, R={reps} per side, probe streams ===")
    tab: dict[int, list[tuple[float, float]]] = {}
    capped: set[int] = set()
    for e, des in enumerate(designs):
        if only is not None and e not in only:
            continue
        sides = []
        for side, sim in enumerate((truth, member)):
            rs = [
                run(sim, des, generator(scorer_seed(TELLING, f"probe-{name}", 0, side, e, r)), menu)
                for r in range(reps)
            ]  # fmt: skip
            sides.append(rs)
            ntr = sum(x["tau"] is not None for x in rs)
            nst = sum(x["stop"] is not None for x in rs)
            taus = sorted(round(x["tau"]) for x in rs if x["tau"] is not None)
            ns = sorted(x.get("n", 0) for x in rs if x["vals"] is not None)
            cpu = sorted(x["cpu"] for x in rs)
            if ntr or nst:
                capped.add(e)
            print(
                f"  {des.name:20s} {'truth' if side == 0 else 'member':6s} trunc {ntr}/{reps} "
                f"stop {nst}/{reps} tau {taus[:3]} n_med {ns[len(ns) // 2] if ns else '-'} "
                f"cpu med {cpu[len(cpu) // 2]:.2f} max {cpu[-1]:.2f}"
            )
        a, b = sides
        row = []
        for d in range(len(menu)):
            va = [None if x["vals"] is None else x["vals"][d] for x in a]
            vb = [None if x["vals"] is None else x["vals"][d] for x in b]
            row.append((hellinger2(va, vb), bhatt(va, vb)))
        tab[e] = row
    cells = sorted(
        ((dv, h, e, d) for e, row in tab.items() if e not in capped
         for d, (h, dv) in enumerate(row)),
        reverse=True,
    )  # fmt: skip
    print("  top cells on designs where no run truncated or stopped (rank by D):")
    for dv, h, e, d in cells[:6]:
        print(f"    {designs[e].name:20s} {labels[d]:34s} He2={h:.6f} D={dv:.2f}")
    for e in sorted(capped):
        row = tab[e]
        sat = sum(h == 1.0 for h, _ in row)
        print(
            f"  capped design {designs[e].name}: He2(mean_rate)={row[0][0]!r}, He2==1 on {sat}/35"
        )


def cap_sweep(reps: int) -> None:
    designs = design_menu(CH)
    e = [i for i, d in enumerate(designs) if d.name == "inject_sign_high"][0]
    world = World(gated(0.3, 0.7, 0.15), 0, truncate=True)
    menu = diagnostic_menu(world.datasets[OBSERVATIONAL].log, CH)[:1]  # mean_rate only
    print(
        f"\n=== partial-cap sweep: truth on inject_sign_high, obs branching 0.85, R={reps} ==="
    )
    for tp in (0.26, 0.30, 0.34, 0.38):
        t = gated(tp, 0.85 - tp / 2, 0.15)
        rs = [run(t, designs[e], generator(scorer_seed(TELLING, "probe-sweep", 0, int(tp * 100), e, r)), menu)
              for r in range(reps)]  # fmt: skip
        ntr = sum(x["tau"] is not None for x in rs)
        mr = sorted(
            round(x["vals"][0], 2)
            for x in rs
            if x["vals"] is not None and x["tau"] is None
        )
        cpu = sum(x["cpu"] for x in rs)
        print(f"  theta+={tp:.2f} high branching {0.85 + tp / 2:.2f}: trunc {ntr}/{reps}; "
              f"uncapped mean_rate {mr[:3]}..{mr[-3:]}; cpu {cpu:.1f}s")  # fmt: skip


def main() -> None:
    print(f"sciagent from {sciagent.__file__}; cap {POLICY_MAX_EVENTS}")
    t0 = time.process_time()
    # Control A1: caps on inject_sign_high only; member = population-matched Hawkes.
    pair("A1", gated(0.5, 0.6, 0.15), hawkes(0.85, 0.15), 8)
    # Control B: truth subcritical everywhere (high 0.85); stub reference on inject_sign_high.
    pair("B", gated(0.3, 0.55, 0.3), hawkes(0.7, 0.3), 8)
    pair("Bx", gated(0.7, 0.35, 0.3), hawkes(0.7, 0.3), 8, only=(1,))
    cap_sweep(12)
    print(f"\ntotal probe CPU {time.process_time() - t0:.1f}s", file=sys.stderr)
    print(f"\ntotal probe CPU {time.process_time() - t0:.1f}s")


if __name__ == "__main__":
    main()
