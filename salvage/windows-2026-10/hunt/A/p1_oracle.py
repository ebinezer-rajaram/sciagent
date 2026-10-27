"""Property 1: ORACLE's training log L >= the true parameters' (no oracle_underfit).

    uv run python .cache/hunt/A/p1_oracle.py --ids dev-008,dev-013 --seed 1 \
        --out .cache/hunt/A/out/p1.jsonl

Runs the campaign's ORACLE (truth ψ check on, campaign FitConfig) on the
(truth, seed)'s observational data and records, per unit: the search's
training log L, the truth-ψ θ-refit's, the true parameters', the flag, the
source, and the held-out per-event log L of the kept fit and of the search fit.
It also records the search-only deficit (truth-ψ refit minus search), which
the ORACLE's fallback hides but every other system that fits the same
structure (planted-hint, an LLM submitting the truth) does not get.
Resumable: (truth, seed) already in --out are skipped.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import common


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ids", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--min-free-gb", type=float, default=1.5)
    args = p.parse_args()

    from sciagent.glm.fit import evaluate_log_likelihood, fit
    from sciagent.systems.v2.models import GLMModel, per_event
    from sciagent.systems.v2.systems import Oracle

    cfg = common.config()
    fc = cfg.fit_config
    done = common.done_keys(args.out)
    for entry in common.entries(args.ids.split(",")):
        key = f"{entry.id}|{args.seed}"
        if key in done:
            continue
        free = common.free_gb()
        if free < args.min_free_gb:
            raise SystemExit(f"free memory {free:.2f} GB < {args.min_free_gb}; stop")
        data = common.data_for(entry, args.seed)
        held = common.held_for(entry, args.seed)
        t0 = time.perf_counter()
        result = Oracle(
            entry.spec.structure,
            fit_config=fc,
            truth_psi=entry.spec.psi,
            truth_coefficients=entry.spec.coef,
        ).run(data)
        wall = time.perf_counter() - t0
        model = result.model
        assert isinstance(model, GLMModel)
        kept = model.fit
        d = dict(result.details)
        # The search fit's held-out value, independently of the fallback.
        search = (
            kept
            if d.get("oracle_psi_source") == "search"
            else fit(entry.spec.structure, data.observational, data.channels, config=fc)
        )
        n_held = held.log.n
        held_search = sum(
            evaluate_log_likelihood(
                search, [held], data.channels, quadrature=fc.quadrature
            )
        )
        row = {
            "key": key,
            "truth": entry.id,
            "seed": args.seed,
            "psi_search": search.psi_search,
            "n_psi_points": search.n_psi_points,
            "search_certified": search.certified,
            "kept_certified": kept.certified,
            "search_ll": d.get("oracle_search_ll"),
            "truth_psi_ll": d.get("oracle_truth_psi_ll"),
            "true_params_ll": d.get("oracle_true_params_ll"),
            "kept_ll": kept.log_likelihood,
            "source": d.get("oracle_psi_source"),
            "on_grid": d.get("oracle_truth_psi_on_grid"),
            "underfit": d.get("oracle_underfit"),
            "n_train_events": kept.n_events,
            "search_deficit_vs_truth_psi": (
                None
                if d.get("oracle_truth_psi_ll") is None
                else d["oracle_truth_psi_ll"] - d["oracle_search_ll"]
            ),
            "kept_minus_true_params": kept.log_likelihood - d["oracle_true_params_ll"],
            "held_per_event_kept": model.held_out_per_event([held]),
            "held_per_event_search": per_event(held_search, [held]),
            "held_events": n_held,
            "kept_digest": common.fit_digest(kept),
            "search_psi": common._plain(search.psi),
            "kept_psi": common._plain(kept.psi),
            "wall_s": wall,
        }
        common.append(args.out, row)
        print(
            f"{key}: src={row['source']} underfit={row['underfit']} "
            f"kept-true={row['kept_minus_true_params']:.4f} "
            f"search_deficit={row['search_deficit_vs_truth_psi']} "
            f"search={row['psi_search']}/{row['n_psi_points']} wall={wall:.1f}s",
            flush=True,
        )


if __name__ == "__main__":
    main()
