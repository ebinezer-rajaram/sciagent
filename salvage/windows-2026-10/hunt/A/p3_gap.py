"""Property 3: no system beats ORACLE on held-out data beyond noise.

    # a fit/5 campaign store (e.g. the mini-pilot)
    uv run python .cache/hunt/A/p3_gap.py --store .cache/minipilot --env fit/5
    # the fit/4 pilot store, with ORACLE replaced by fresh fit/5 ORACLE fits
    uv run python .cache/hunt/A/p3_gap.py --store .cache/pilot --env fit/4 \
        --oracle-jsonl .cache/hunt/A/out/p1.jsonl --seeds 1

Reads every scored cell of the store (baseline and llm-score cells, at the
given env version substring) by scanning ``details/`` (version-agnostic, so it
reads a fit/4 store too; read-only). Per (truth, seed) it reports each
system's gap = per_event - ORACLE per_event, flags gap > --threshold (the
mini-pilot gate's +0.01), and also compares every system with the truth's own
parameters on the same held-out log (computed here, cached in
--truth-cache): a fitted model beating the data-generating process by more
than the threshold is a leakage red flag whatever ORACLE does. ORACLE cells'
``oracle_underfit`` and ``oracle_psi_source`` are listed.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import common


def _scan(store: Path, env_sub: str):
    rows = []
    for f in sorted((store / "details").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        k = d["key"]
        c = k["config"]
        if env_sub not in k["env_version"] or c.get("kind") not in (
            "baseline",
            "llm-score",
        ):
            continue
        reading = {n: float.fromhex(v) for n, v in d["reading"].items()}
        result = d["detail"].get("result", {})
        rows.append(
            {
                "truth": c["truth"],
                "seed": int(k["seed"]),
                "system": c["system"],
                "status": reading.get("status"),
                "per_event": reading.get("per_event")
                if reading.get("status") == 1.0
                else None,
                "structure": result.get("structure"),
                "underfit": result.get("oracle_underfit"),
                "source": result.get("oracle_psi_source"),
                "metric": k["metric_version"],
                "file": f.name,
            }
        )
    return rows


def _true_params_per_event(truth: str, seed: int, cache: Path) -> float:
    key = f"{truth}|{seed}"
    if cache.exists():
        for line in cache.read_text().splitlines():
            row = json.loads(line)
            if row["key"] == key:
                return float.fromhex(row["per_event"])
    from sciagent.glm.fit import parameter_log_likelihood
    from sciagent.systems.v2.models import per_event

    (entry,) = common.entries([truth])
    held = common.held_for(entry, seed)
    fc = common.config().fit_config
    total = math.fsum(
        parameter_log_likelihood(
            entry.spec.structure,
            entry.spec.psi,
            entry.spec.coef,
            [held],
            entry.spec.channels
            if hasattr(entry.spec, "channels")
            else common.env().channels,
            quadrature=fc.quadrature,
        )
    )
    value = per_event(total, [held])
    common.append(cache, {"key": key, "per_event": value.hex()})
    return value


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--env", default="fit/5", help="env_version substring")
    p.add_argument("--oracle-jsonl", type=Path, default=None)
    p.add_argument("--seeds", default=None)
    p.add_argument("--truths", default=None)
    p.add_argument("--threshold", type=float, default=0.01)
    p.add_argument(
        "--truth-cache",
        type=Path,
        default=Path(".cache/hunt/A/out/truth_heldout.jsonl"),
    )
    p.add_argument("--no-truth", action="store_true")
    args = p.parse_args()

    rows = _scan(args.store, args.env)
    if args.seeds:
        seeds = {int(s) for s in args.seeds.split(",")}
        rows = [r for r in rows if r["seed"] in seeds]
    if args.truths:
        ts = set(args.truths.split(","))
        rows = [r for r in rows if r["truth"] in ts]
    oracle: dict[tuple[str, int], float] = {}
    for r in rows:
        if r["system"] == "ORACLE" and r["per_event"] is not None:
            oracle[(r["truth"], r["seed"])] = r["per_event"]
    if args.oracle_jsonl:
        override = {}
        for line in args.oracle_jsonl.read_text().splitlines():
            o = json.loads(line)
            override[(o["truth"], o["seed"])] = o["held_per_event_kept"]
        # Only units with a fresh ORACLE are compared.
        oracle = override
        rows = [r for r in rows if (r["truth"], r["seed"]) in oracle]
    by_unit = defaultdict(list)
    for r in rows:
        by_unit[(r["truth"], r["seed"])].append(r)

    print(
        f"store {args.store} env~{args.env}: {len(rows)} scored cells, {len(by_unit)} units"
    )
    flags = 0
    truth_flags = 0
    worst = []
    for unit in sorted(by_unit):
        o = oracle.get(unit)
        tp = None if args.no_truth else _true_params_per_event(*unit, args.truth_cache)
        line = f"{unit[0]} s{unit[1]}: ORACLE={o if o is None else f'{o:.5f}'}"
        if tp is not None:
            line += f" true-params={tp:.5f}"
            if o is not None:
                line += f" (truth-ORACLE {tp - o:+.5f})"
        print(line)
        for r in sorted(by_unit[unit], key=lambda r: r["system"]):
            if r["system"] == "ORACLE":
                if not args.oracle_jsonl:
                    print(f"    ORACLE underfit={r['underfit']} source={r['source']}")
                continue
            pe = r["per_event"]
            if pe is None or o is None:
                continue
            gap = pe - o
            mark = ""
            if math.isfinite(gap) and gap > args.threshold:
                flags += 1
                mark += " GAP>THR"
            if tp is not None and math.isfinite(pe) and pe - tp > args.threshold:
                truth_flags += 1
                mark += " BEATS-TRUTH"
            if math.isfinite(gap):
                worst.append((gap, unit, r["system"], r["structure"]))
            if mark or gap > 0:
                print(f"    {r['system']:<16} gap={gap:+.5f}{mark}  {r['structure']}")
    worst.sort(key=lambda w: -w[0])
    print(
        f"\nunits with gap > {args.threshold}: {flags}; beating truth params by > {args.threshold}: {truth_flags}"
    )
    print("largest gaps:")
    for gap, unit, system, struct in worst[:10]:
        print(f"  {gap:+.5f} {unit[0]} s{unit[1]} {system}: {struct}")
    underfit = [
        (r["truth"], r["seed"])
        for r in rows
        if r["system"] == "ORACLE" and r["underfit"]
    ]
    missing = [
        (r["truth"], r["seed"])
        for r in rows
        if r["system"] == "ORACLE" and r["underfit"] is None
    ]
    if not args.oracle_jsonl:
        print(f"ORACLE oracle_underfit True: {underfit}; flag missing: {len(missing)}")


if __name__ == "__main__":
    main()
