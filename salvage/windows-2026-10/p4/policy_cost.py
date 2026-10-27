"""Time one policy decision (Δ11) on dev truths: R = 16, M = 8, K = 4.

Run from the p4 worktree:  uv run python C:/dev/products/sciagent/.cache/p4/policy_cost.py
Re-measure after fit/5 lands (the fit API is unchanged).

Per truth: the world's observational log (seed 1); the entertained set is the
top K = 4 grammar library members by BIC on it, refitted (as at a decision);
then one choose() per policy, timed separately, plus one FSD-null bootstrap.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import numpy as np
from sciagent.systems.v2.policies import (
    DecisionContext,
    EIGPolicy,
    FSDPolicy,
    Menu,
    RandomPolicy,
    design_menu,
    diagnostic_menu,
    fit_entertained,
    fsd_null,
    reference_model,
)

from environments.pointproc import v2
from sciagent.glm.fit import FitError, fit
from sciagent.glm.interventions import Compose, Experiment, run_experiment
from sciagent.scenarios.records import read_split
from sciagent.systems.v2.systems import GrammarMember

SPLIT = Path("C:/dev/products/sciagent/data/v2/truths/dev")
TRUTHS = ("dev-008", "dev-013")
SEED = 1
R, K = 16, 4


def main() -> None:
    records, _ = read_split(SPLIT)
    by_id = {r.id: r for r in records}
    members = [m for m in v2.B_LIB_LIBRARY if isinstance(m, GrammarMember)]
    for truth_id in TRUTHS:
        if truth_id not in by_id:
            print(f"{truth_id}: not in the split", flush=True)
            continue
        rec = by_id[truth_id]
        # The world's observational draw (investigation/world.py), done here
        # without importing sciagent.investigation.
        obs = run_experiment(
            rec.structure(v2.CHANNELS),
            rec.psi_assignment(),
            rec.coefficients(),
            v2.CHANNELS,
            v2.mark_sampler,
            Experiment(Compose(()), 2000.0),
            np.random.default_rng(np.random.SeedSequence([SEED, 0, 0])),
            max_events=15_000,
            label="obs",
        )
        print(f"\n{truth_id}: {rec.dsl}  n_obs={obs.log.n}", flush=True)
        t0 = time.perf_counter()
        scored = []
        for m in members:
            try:
                res = fit(m.structure, (obs,), v2.CHANNELS)
            except FitError as error:
                print(f"  fit {m.name}: {type(error).__name__}")
                continue
            if res.certified:
                scored.append((res.bic, m.name, m.structure))
        scored.sort(key=lambda x: (x[0], x[1]))
        top = [(name, s) for _, name, s in scored[:K]]
        t_rank = time.perf_counter() - t0
        t0 = time.perf_counter()
        ent = fit_entertained(top, (obs,), v2.CHANNELS, v2.mark_sampler)
        t_fit = time.perf_counter() - t0
        menu = Menu(design_menu(v2.CHANNELS), diagnostic_menu(obs.log, v2.CHANNELS))
        ctx = DecisionContext(v2.CHANNELS, obs, ent, menu)
        print(
            f"  entertained {[h.name for h in ent.hypotheses]} "
            f"w={[round(w, 3) for w in ent.weights]}"
        )
        print(f"  M={len(menu.designs)} D={len(menu.diagnostics)}")
        print(f"  library ranking {t_rank:.1f}s; entertained refit {t_fit:.1f}s")
        for policy in (RandomPolicy(SEED), EIGPolicy(SEED, replicates=R)):
            t0 = time.perf_counter()
            c = policy.choose(ctx, 0)
            dt = time.perf_counter() - t0
            print(
                f"  {policy.name:7s} {dt:8.1f}s -> {c.design.name} / {c.diagnostic.label}",
                flush=True,
            )
        t0 = time.perf_counter()
        ref = reference_model(ctx)
        t_wh = time.perf_counter() - t0
        t0 = time.perf_counter()
        c = FSDPolicy(SEED, replicates=R, reference=ref).choose(ctx, 0)
        dt = time.perf_counter() - t0
        print(
            f"  FSD     {dt:8.1f}s (+ Wiener-Hopf estimate {t_wh:.1f}s) -> "
            f"{c.design.name} / {c.diagnostic.label}",
            flush=True,
        )
        t0 = time.perf_counter()
        null = fsd_null(ctx, SEED, 0, replicates=R, bootstraps=1)
        dt = time.perf_counter() - t0
        worst = max(x for row in null.mean for x in row)
        print(f"  FSD null, 1 bootstrap {dt:8.1f}s (max cell {worst:.3f})", flush=True)


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
