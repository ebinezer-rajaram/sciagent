"""Render ``scripts/pilot_report.py`` on one store under several hash seeds and
check that every rendering is byte-identical.

    uv run python scripts/report_determinism.py --root .cache/minipilot \
        --hash-seeds 0,1,2 -- --seeds 1 --ids dev-008,dev-013,dev-017

Rebuilds hunt B's ``report_determinism.py`` (STATUS-2026-10-05: "report
byte-identical across hash seeds"). Each rendering is a fresh process with its
own ``PYTHONHASHSEED`` and writes to a scratch directory, so no notes block is
carried over. Arguments after ``--`` go to ``pilot_report.py`` unchanged
(``--root`` and ``--out`` are set here). Exit status 0 when identical, 1 when
not, 2 when a rendering fails. Two *different* stores (two runs) are compared
by ``store_compare.py``, not here: the report prints wall times from
``timing.jsonl`` and the store path, which differ between runs by design.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sciagent.campaign.audit import same_bytes

REPORT = Path(__file__).resolve().parent / "pilot_report.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--hash-seeds", default="0,1,2")
    parser.add_argument("report_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not (args.root / "ledger.sqlite").is_file():
        print(f"{args.root}: not a campaign store", file=sys.stderr)
        return 2
    extra = [a for a in args.report_args if a != "--"]
    seeds = [s.strip() for s in args.hash_seeds.split(",") if s.strip()]
    if len(seeds) < 2:
        print("need at least two hash seeds", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="report-determinism-") as tmp:
        outs: list[Path] = []
        for seed in seeds:
            out = Path(tmp) / f"report-{seed}.md"
            env = {**os.environ, "PYTHONHASHSEED": seed}
            done = subprocess.run(
                [
                    sys.executable,
                    str(REPORT),
                    "--root",
                    str(args.root),
                    "--out",
                    str(out),
                    *extra,
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if done.returncode != 0 or not out.is_file():
                print(f"hash seed {seed}: pilot_report failed", file=sys.stderr)
                print(done.stderr[-2000:], file=sys.stderr)
                return 2
            outs.append(out)
            print(f"hash seed {seed}: {out.stat().st_size} bytes")
        differ = same_bytes(outs)
        for p in differ:
            print(f"differs from hash seed {seeds[0]}: {p.name}")
    print("REPORT IDENTICAL" if not differ else "REPORT DIFFERS")
    return 0 if not differ else 1


if __name__ == "__main__":
    sys.exit(main())
