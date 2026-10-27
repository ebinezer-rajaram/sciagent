"""Defect hunt B, property 4: offline replay of stored session records.

Pilot records were made under fit/4; replay them with the p3-fit4 source:

    git archive p3-fit4 src scripts pyproject.toml | tar -x -C .cache/hunt/B/fit4src
    PYTHONPATH=.cache/hunt/B/fit4src/src uv run python .cache/hunt/B/replay_check.py \
        --root <store> --split data/v2/truths/dev --n 3 [--arm AG-c] [--digests a,b,c]

Under the current tree (no PYTHONPATH) it replays a fit/5 store (mini-pilot)
or shows where a fit/4 record diverges. Read-only: records are loaded, never
saved.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument(
        "--split", type=Path, default=ROOT / "data" / "v2" / "truths" / "dev"
    )
    p.add_argument("--n", type=int, default=3)
    p.add_argument("--arm", default=None, help="AG-c or AG-o filter")
    p.add_argument("--digests", default=None, help="session digest prefixes")
    args = p.parse_args()

    import sciagent
    from sciagent.glm.fit import FIT_VERSION

    scripts = Path(sciagent.__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    from pilot import load_split, pointproc_environment, reconfigure_stdout

    reconfigure_stdout()
    from sciagent.harness.errors import ReplayDivergenceError
    from sciagent.harness.record import SessionRecord
    from sciagent.investigation.runner import replay_investigation

    print(
        f"sciagent from {Path(sciagent.__file__).parent}; {FIT_VERSION}; pilot.py from {scripts}"
    )
    env = pointproc_environment(load_split(args.split)).investigation
    assert env is not None
    cells = []
    for path in sorted((args.root / "details").glob("*.json")):
        c = json.loads(path.read_text(encoding="utf-8"))
        if c["key"]["config"].get("kind") != "llm-session":
            continue
        s = c["detail"]["session"]
        if float.fromhex(c["reading"]["status"]) != 1.0:
            continue
        system = c["key"]["config"]["system"]
        if args.arm and not system.startswith(args.arm + "-"):
            continue
        if args.digests and not any(
            path.stem.startswith(d) for d in args.digests.split(",")
        ):
            continue
        cells.append(
            (
                len(s.get("fitted", [])),
                path.stem,
                system,
                c["key"]["config"]["truth"],
                c["key"]["seed"],
                s,
            )
        )
    cells.sort()
    ok = 0
    for nfit, dg, system, truth, seed, s in cells[: args.n]:
        rec_path = args.root / s["record_dir"] / s["record"]
        record = SessionRecord.load(rec_path)
        t0 = time.perf_counter()
        try:
            rep = replay_investigation(record, env)
            ok += 1
            print(
                f"PASS {dg[:12]} {system} {truth} s{seed}: steps={rep.steps} budgets={dict(rep.budgets_used)} "
                f"head={rep.head[:16]} ({time.perf_counter() - t0:.0f}s)"
            )
        except ReplayDivergenceError as e:
            print(
                f"DIVERGE {dg[:12]} {system} {truth} s{seed}: step {e.step} field {e.field}: {str(e)[:400]}"
            )
    print(f"{ok}/{min(args.n, len(cells))} replayed byte-identically")


if __name__ == "__main__":
    main()
