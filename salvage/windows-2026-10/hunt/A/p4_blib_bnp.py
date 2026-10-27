"""Property 4: B-lib and B-np are byte-identical run to run, and B-lib under
fit/5 takes the exhaustive path (so equals fit/4's B-lib bit for bit).

    # one run (call twice with different --label, in separate processes)
    uv run python .cache/hunt/A/p4_blib_bnp.py run --label r1 --seed 1 \
        --out .cache/hunt/A/out/p4.jsonl
    uv run python .cache/hunt/A/p4_blib_bnp.py run --label r2 --seed 1 \
        --out .cache/hunt/A/out/p4.jsonl
    # compare the runs, and against a store's B-lib / B-np per_event
    uv run python .cache/hunt/A/p4_blib_bnp.py compare --out .cache/hunt/A/out/p4.jsonl \
        --store .cache/pilot --env fit/4

Per (truth, seed): every library member's fit digest (all FitResult fields
but wall time, floats as hex) and ψ search path, B-lib's submission and
held-out per_event, and B-np's held-out per_event (hex).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import common


def run(args) -> None:
    from sciagent.glm.fit import FitError, fit
    from sciagent.systems.v2.systems import BLib, BNp, GrammarMember

    env = common.env()
    fc = common.config().fit_config
    done = common.done_keys(args.out)
    ids = args.ids.split(",") if args.ids else None
    for entry in common.entries(ids):
        key = f"{args.label}|{entry.id}|{args.seed}"
        if key in done:
            continue
        data = common.data_for(entry, args.seed)
        held = common.held_for(entry, args.seed)
        members = {}
        for m in env.library:
            if isinstance(m, GrammarMember):
                try:
                    res = fit(m.structure, data.observational, data.channels, config=fc)
                except FitError as error:
                    members[m.name] = {"error": repr(error)}
                    continue
                members[m.name] = {
                    "digest": common.fit_digest(res),
                    "psi_search": res.psi_search,
                    "n_psi_points": res.n_psi_points,
                }
        lib = BLib(env.library, fit_config=fc).run(data)
        np_ = BNp().run(data)
        row = {
            "key": key,
            "label": args.label,
            "truth": entry.id,
            "seed": args.seed,
            "members": members,
            "blib_submitted": lib.submitted,
            "blib_per_event": lib.model.held_out_per_event([held]).hex(),
            "blib_skipped": list(lib.skipped),
            "bnp_per_event": np_.model.held_out_per_event([held]).hex(),
        }
        common.append(args.out, row)
        print(
            key,
            lib.submitted,
            float.fromhex(row["blib_per_event"]),
            float.fromhex(row["bnp_per_event"]),
            {k: v.get("psi_search") for k, v in members.items()},
            flush=True,
        )


def compare(args) -> None:
    runs: dict[str, dict[tuple[str, int], dict]] = {}
    for line in args.out.read_text().splitlines():
        r = json.loads(line)
        runs.setdefault(r["label"], {})[(r["truth"], r["seed"])] = r
    labels = sorted(runs)
    print("labels:", labels, {lab: len(v) for lab, v in runs.items()})
    fields = (
        "members",
        "blib_submitted",
        "blib_per_event",
        "blib_skipped",
        "bnp_per_event",
    )
    if len(labels) >= 2:
        a, b = runs[labels[0]], runs[labels[1]]
        common_units = sorted(set(a) & set(b))
        diffs = [(u, f) for u in common_units for f in fields if a[u][f] != b[u][f]]
        print(
            f"{labels[0]} vs {labels[1]}: {len(common_units)} units, {len(diffs)} differing fields"
        )
        for u, f in diffs:
            print("  DIFF", u, f, a[u][f], b[u][f])
    paths = {
        (r["truth"], r["seed"], m, v.get("psi_search"))
        for runs_ in runs.values()
        for r in runs_.values()
        for m, v in r["members"].items()
    }
    nonexh = [p for p in paths if p[3] != "exhaustive"]
    print(f"member fits not on the exhaustive path: {len(nonexh)} {nonexh[:5]}")
    if args.store:
        stored: dict[tuple[str, int, str], float] = {}
        for f in sorted((args.store / "details").glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            k, c = d["key"], d["key"]["config"]
            if args.env not in k["env_version"] or c.get("kind") != "baseline":
                continue
            if c["system"] in ("B-lib", "B-np") and "per_event" in d["reading"]:
                stored[(c["truth"], int(k["seed"]), c["system"])] = d["reading"][
                    "per_event"
                ]
        first = runs[labels[0]]
        n = same = 0
        for (t, s), r in sorted(first.items()):
            for system, field in (
                ("B-lib", "blib_per_event"),
                ("B-np", "bnp_per_event"),
            ):
                got = stored.get((t, s, system))
                if got is None:
                    continue
                n += 1
                if got == r[field]:
                    same += 1
                else:
                    print(
                        f"  STORE DIFF {t} s{s} {system}: store {float.fromhex(got)!r} "
                        f"now {float.fromhex(r[field])!r}"
                    )
        print(f"vs store {args.store} ({args.env}): {same}/{n} per_event bit-identical")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("run", "compare"))
    p.add_argument("--label", default="r1")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--ids", default=None)
    p.add_argument("--out", type=Path, default=Path(".cache/hunt/A/out/p4.jsonl"))
    p.add_argument("--store", type=Path, default=None)
    p.add_argument("--env", default="fit/4")
    args = p.parse_args()
    run(args) if args.mode == "run" else compare(args)


if __name__ == "__main__":
    main()
