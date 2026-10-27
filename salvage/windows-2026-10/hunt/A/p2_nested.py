"""Property 2: nested monotonicity, fit(A+B) training log L >= fit(A) - tol.

    # list the pairs (and their ψ grid sizes) without fitting
    uv run python .cache/hunt/A/p2_nested.py --list
    # fit, cheapest A+B first, at most 512*... points; resumable
    uv run python .cache/hunt/A/p2_nested.py --seed 1 --max-points 1000000 \
        --out .cache/hunt/A/out/p2_fits.jsonl
    # evaluate the pairs from the fits already done
    uv run python .cache/hunt/A/p2_nested.py --report --out .cache/hunt/A/out/p2_fits.jsonl

Pairs, all on one dev truth's seed observational data, same link:
- drop-one: A = the truth without feature k, A+B = the truth (every k);
- library: A = a grammar library member (hawkes, seasonality), A+B = that
  member plus each truth feature (truths with the member's link);
- superset: A = the truth, A+B = the truth plus each library member's
  features (truths with the member's link).

Tolerance (per pair): the two fits' certificate gaps (an upper bound on each
θ-solve's distance from its optimum) plus the two fits' a-posteriori
quadrature errors |Λ_quad - Λ_exact| (A and A+B use different quadrature
rules, since the rule depends on the structure, so A's model evaluated
under A+B's rule differs from A's own value by up to that). Anything beyond
it is the ψ search's doing.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import common


def _pairs(seed: int):
    from sciagent.glm.fit import _Slots
    from sciagent.glm.grammar import Structure
    from sciagent.glm.syntax import render
    from sciagent.systems.v2.systems import GrammarMember

    env = common.env()
    lib = [
        m for m in env.library if isinstance(m, GrammarMember) and m.structure.features
    ]
    out = []
    for e in common.entries():
        s = e.spec.structure
        feats = s.features
        if len(feats) >= 2:
            for k in range(len(feats)):
                a = Structure(feats[:k] + feats[k + 1 :], s.link)
                out.append(("drop", e.id, a, s))
        for m in lib:
            if m.structure.link is not s.link:
                continue
            for f in feats:
                if f in m.structure.features:
                    continue
                out.append(
                    (
                        f"lib:{m.name}",
                        e.id,
                        m.structure,
                        Structure(m.structure.features + (f,), s.link),
                    )
                )
            extra = tuple(f for f in m.structure.features if f not in feats)
            if extra and len(feats) + len(extra) <= 4:
                out.append(
                    (f"super:{m.name}", e.id, s, Structure(feats + extra, s.link))
                )
    rows = []
    for kind, tid, a, ab in out:
        rows.append(
            {
                "kind": kind,
                "truth": tid,
                "a": render(a),
                "ab": render(ab),
                "a_points": _Slots.of(a).size,
                "ab_points": _Slots.of(ab).size,
                "a_struct": a,
                "ab_struct": ab,
            }
        )
    rows.sort(key=lambda r: (r["ab_points"], r["truth"], r["kind"], r["ab"], r["a"]))
    return rows


def _fit_key(truth: str, seed: int, text: str) -> str:
    return f"{truth}|{seed}|{text}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", type=Path, default=Path(".cache/hunt/A/out/p2_fits.jsonl"))
    p.add_argument("--max-points", type=int, default=10**12)
    p.add_argument("--list", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--min-free-gb", type=float, default=1.5)
    p.add_argument("--only", default=None, help="comma-separated truth ids")
    args = p.parse_args()
    pairs = _pairs(args.seed)
    if args.only:
        keep = set(args.only.split(","))
        pairs = [r for r in pairs if r["truth"] in keep]
    if args.list:
        for r in pairs:
            print(
                f"{r['ab_points']:>9} {r['a_points']:>7} {r['truth']} {r['kind']:<18} {r['ab']}"
            )
        print(len(pairs), "pairs")
        return
    if args.report:
        report(pairs, args)
        return

    from sciagent.glm.fit import FitError, fit
    from sciagent.glm.syntax import render

    fc = common.config().fit_config
    done = common.done_keys(args.out)
    data_cache: dict[str, object] = {}
    for r in pairs:
        if r["ab_points"] > args.max_points:
            continue
        for text, struct in ((r["a"], r["a_struct"]), (r["ab"], r["ab_struct"])):
            key = _fit_key(r["truth"], args.seed, text)
            if key in done:
                continue
            free = common.free_gb()
            if free < args.min_free_gb:
                raise SystemExit(f"free memory {free:.2f} GB < {args.min_free_gb}")
            if r["truth"] not in data_cache:
                data_cache.clear()
                (entry,) = common.entries([r["truth"]])
                data_cache[r["truth"]] = common.data_for(entry, args.seed)
            data = data_cache[r["truth"]]
            t0 = time.perf_counter()
            try:
                res = fit(struct, data.observational, data.channels, config=fc)
            except FitError as error:
                common.append(args.out, {"key": key, "error": repr(error)})
                done.add(key)
                continue
            row = {
                "key": key,
                "structure": render(struct),
                "ll": res.log_likelihood,
                "certified": res.certified,
                "gap": res.duality_gap,
                "quad_err": res.quadrature_error,
                "psi_search": res.psi_search,
                "n_psi_points": res.n_psi_points,
                "psi": common._plain(res.psi),
                "digest": common.fit_digest(res),
                "wall_s": time.perf_counter() - t0,
            }
            common.append(args.out, row)
            done.add(key)
            print(
                f"{key}: ll={res.log_likelihood:.4f} {res.psi_search}/"
                f"{res.n_psi_points} cert={res.certified} {row['wall_s']:.1f}s",
                flush=True,
            )


def report(pairs, args) -> None:
    fits = {}
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if line:
                row = json.loads(line)
                fits[row["key"]] = row
    n = bad = 0
    worst = []
    for r in pairs:
        a = fits.get(_fit_key(r["truth"], args.seed, r["a"]))
        ab = fits.get(_fit_key(r["truth"], args.seed, r["ab"]))
        if a is None or ab is None or "error" in a or "error" in ab:
            continue
        n += 1
        tol = abs(a["gap"]) + abs(ab["gap"]) + abs(a["quad_err"]) + abs(ab["quad_err"])
        diff = ab["ll"] - a["ll"]
        if diff < -tol:
            bad += 1
            worst.append((diff, tol, r, a, ab))
    print(f"pairs evaluated {n}, violations {bad}")
    for diff, tol, r, a, ab in sorted(worst, key=lambda w: w[0]):
        print(
            f"  {r['truth']} {r['kind']}: ll(A+B)-ll(A)={diff:.4f} tol={tol:.2e} "
            f"A[{a['psi_search']}/{a['n_psi_points']}] AB[{ab['psi_search']}/"
            f"{ab['n_psi_points']}]\n    A ={r['a']}\n    AB={r['ab']}"
        )
    if n:
        margins = [
            fits[_fit_key(r["truth"], args.seed, r["ab"])]["ll"]
            - fits[_fit_key(r["truth"], args.seed, r["a"])]["ll"]
            for r in pairs
            if _fit_key(r["truth"], args.seed, r["a"]) in fits
            and _fit_key(r["truth"], args.seed, r["ab"]) in fits
            and "error" not in fits[_fit_key(r["truth"], args.seed, r["a"])]
            and "error" not in fits[_fit_key(r["truth"], args.seed, r["ab"])]
        ]
        print(
            f"min margin {min(margins):.6f}; non-finite {sum(not math.isfinite(m) for m in margins)}"
        )


if __name__ == "__main__":
    main()
