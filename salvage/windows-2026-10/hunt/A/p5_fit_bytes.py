"""Properties 5 and 6: a fit's bytes across processes, and across block-cache bounds.

One fit per process; run it twice and compare the digests:

    # 5: determinism (same config, two processes)
    uv run python .cache/hunt/A/p5_fit_bytes.py --truth dev-008 --seed 1 \
        --dsl truth --label det-a --out .cache/hunt/A/out/p5.jsonl
    uv run python .cache/hunt/A/p5_fit_bytes.py --truth dev-008 --seed 1 \
        --dsl truth --label det-b --out .cache/hunt/A/out/p5.jsonl
    # 6: block_cache_bytes invariance (bound 0 vs the default)
    uv run python .cache/hunt/A/p5_fit_bytes.py --truth dev-013 --seed 1 \
        --dsl "link=exp; Gate(Gate(Periodic, PhaseWindow), PhaseWindow) + Gate(Product(Periodic, Periodic), PhaseWindow) + Excite(ExpK, One, all)" \
        --cache-bytes 0 --label cache0 --out .cache/hunt/A/out/p5.jsonl
    # compare every pair of labels on the same (truth, seed, dsl, experiments)
    uv run python .cache/hunt/A/p5_fit_bytes.py --compare --out .cache/hunt/A/out/p5.jsonl

``--dsl truth`` fits the truth's own structure. ``--experiments N`` adds N
unintervened 8000-unit experiments drawn by the world (as the dev-013 pilot
pair in tests/glm/test_fit_search.py). Records the digest of every FitResult
field but wall time (floats as hex), wall time and peak commit.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from itertools import combinations
from pathlib import Path

import common


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_commit_gb() -> float:
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(pmc)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PMC),
        wintypes.DWORD,
    ]
    if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        raise OSError(ctypes.get_last_error(), "K32GetProcessMemoryInfo failed")
    return pmc.PeakPagefileUsage / 2**30


def _data(entry, seed: int, experiments: int):
    from sciagent.glm.interventions import Compose, Experiment
    from sciagent.investigation.world import World

    world = World(entry.spec, seed)
    for _ in range(experiments):
        world.run(Experiment(Compose(()), 8000.0))
    labels = sorted(world.datasets, key=lambda s: (s != "obs", s))
    return tuple(world.datasets[i] for i in labels)


def run(args) -> None:
    import dataclasses

    from sciagent.glm.canonical import canonicalise
    from sciagent.glm.fit import fit
    from sciagent.glm.syntax import parse, render

    (entry,) = common.entries([args.truth])
    channels = common.env().channels
    structure = (
        entry.spec.structure
        if args.dsl == "truth"
        else canonicalise(parse(args.dsl, channels))
    )
    cfg = common.config().fit_config
    if args.cache_bytes is not None:
        cfg = dataclasses.replace(cfg, block_cache_bytes=args.cache_bytes)
    data = _data(entry, args.seed, args.experiments)
    t0 = time.perf_counter()
    res = fit(structure, data, channels, config=cfg)
    wall = time.perf_counter() - t0
    row = {
        "label": args.label,
        "unit": f"{args.truth}|{args.seed}|{render(structure)}|exp={args.experiments}",
        "cache_bytes": cfg.block_cache_bytes,
        "digest": common.fit_digest(res),
        "record": common.fit_record(res),
        "ll": res.log_likelihood,
        "psi_search": res.psi_search,
        "n_psi_points": res.n_psi_points,
        "wall_s": wall,
        "peak_commit_gb": peak_commit_gb(),
        "datasets": [d.label for d in data],
    }
    common.append(args.out, row)
    print(
        f"{row['label']} {row['unit']}: digest={row['digest'][:16]} ll={res.log_likelihood!r} "
        f"{res.psi_search}/{res.n_psi_points} wall={wall:.1f}s peak={row['peak_commit_gb']:.2f}GB",
        flush=True,
    )


def compare(args) -> None:
    rows = [json.loads(x) for x in args.out.read_text().splitlines() if x]
    by_unit: dict[str, list[dict]] = {}
    for r in rows:
        by_unit.setdefault(r["unit"], []).append(r)
    for unit, rs in sorted(by_unit.items()):
        print(unit)
        for r in rs:
            print(
                f"  {r['label']:<10} cache={r['cache_bytes']:<11} {r['digest'][:16]} "
                f"wall={r['wall_s']:.1f}s peak={r['peak_commit_gb']:.2f}GB n={r['n_psi_points']}"
            )
        for a, b in combinations(rs, 2):
            if a["digest"] != b["digest"]:
                fields = [
                    k for k in a["record"] if a["record"][k] != b["record"].get(k)
                ]
                print(f"  MISMATCH {a['label']} vs {b['label']}: fields {fields}")
        if len({r["digest"] for r in rs}) == 1 and len(rs) > 1:
            print(f"  identical across {len(rs)} runs")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--truth", default="dev-008")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--dsl", default="truth")
    p.add_argument("--experiments", type=int, default=0)
    p.add_argument("--cache-bytes", type=int, default=None)
    p.add_argument("--label", default="a")
    p.add_argument("--out", type=Path, default=Path(".cache/hunt/A/out/p5.jsonl"))
    p.add_argument("--compare", action="store_true")
    args = p.parse_args()
    compare(args) if args.compare else run(args)


if __name__ == "__main__":
    main()
