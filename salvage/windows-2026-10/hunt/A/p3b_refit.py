"""Property 3, cross-version: refit under the current fitter every stored
submission that was at or above ORACLE, and re-read its gap.

    uv run python .cache/hunt/A/p3b_refit.py --store .cache/pilot --env fit/4 \
        --oracle-jsonl .cache/hunt/A/out/p1.jsonl --seeds 1 --min-gap -0.002

A fit/4 store's systems were fitted by fit/4; under fit/5 their fits can only
move. This refits each distinct (truth, seed, structure) with stored gap >=
--min-gap (against the fresh ORACLE from p1_oracle.py) on the observational
data with the campaign FitConfig and prints its new held-out gap.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import common
from p3_gap import _scan


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--env", default="fit/4")
    p.add_argument("--oracle-jsonl", type=Path, required=True)
    p.add_argument("--seeds", default="1")
    p.add_argument("--min-gap", type=float, default=0.0)
    p.add_argument("--threshold", type=float, default=0.01)
    args = p.parse_args()

    from sciagent.glm.canonical import canonicalise
    from sciagent.glm.fit import fit
    from sciagent.glm.syntax import parse
    from sciagent.systems.v2.models import GLMModel

    oracle = {}
    for line in args.oracle_jsonl.read_text().splitlines():
        o = json.loads(line)
        oracle[(o["truth"], o["seed"])] = o["held_per_event_kept"]
    seeds = {int(s) for s in args.seeds.split(",")}
    todo: dict[tuple[str, int, str], list[str]] = {}
    for r in _scan(args.store, args.env):
        unit = (r["truth"], r["seed"])
        if r["seed"] not in seeds or unit not in oracle or r["system"] == "ORACLE":
            continue
        if r["per_event"] is None or r["structure"] is None:
            continue
        if r["per_event"] - oracle[unit] >= args.min_gap:
            todo.setdefault((*unit, r["structure"]), []).append(r["system"])
    env = common.env()
    fc = common.config().fit_config
    worst = -1.0
    for (truth, seed, text), systems in sorted(todo.items()):
        (entry,) = common.entries([truth])
        data = common.data_for(entry, seed)
        held = common.held_for(entry, seed)
        structure = canonicalise(parse(text, env.channels))
        res = fit(structure, data.observational, data.channels, config=fc)
        pe = GLMModel("x", res, data.channels, data.marks).held_out_per_event([held])
        gap = pe - oracle[(truth, seed)]
        worst = max(worst, gap)
        flag = " GAP>THR" if gap > args.threshold else ""
        print(
            f"{truth} s{seed} gap={gap:+.5f}{flag} {res.psi_search}/{res.n_psi_points} "
            f"{','.join(sorted(set(systems)))}: {text}",
            flush=True,
        )
    print(f"refitted {len(todo)}; max gap {worst:+.5f}")


if __name__ == "__main__":
    main()
