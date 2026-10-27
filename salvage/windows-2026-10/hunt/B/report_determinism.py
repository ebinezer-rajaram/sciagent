"""Defect hunt B, property 7: pilot_report.py is byte-deterministic.

    uv run python .cache/hunt/B/report_determinism.py --root <store> [--legacy-fit4] \
        [-- <extra pilot_report args, e.g. --truths 20 --b10f-truths 10>]

Runs scripts/pilot_report.py twice, each in a fresh process with a different
PYTHONHASHSEED (exposes set/dict-order dependence), each on its own fresh COPY
of the store, and compares the outputs byte for byte.

--legacy-fit4 reads the store at the fit/4 + score/1 addresses (patches
FIT_VERSION, METRIC_VERSION and FitConfig.key in-process), so the P3 pilot
store yields a full report. It changes which cells are read, not how the
report computes from them.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD_FIT_KEY = (
    "q=8,0x1.0000000000000p-2,0x1.8000000000000p+1,0x1.0000000000000p+0,8|solver=newton|"
    "max_iter=100|gap=0x1.5798ee2308c3ap-27|full=512|cycles=50"
)
OLD_METRIC = "sciagent.campaign.score/1|battery:120.0x32cap1000"
OLD_FIT = "sciagent.glm.fit/4"

SHIM = """
import sys, runpy
sys.path.insert(0, {scripts!r})
if {legacy!r}:
    import sciagent.glm.fit as F, sciagent.campaign.plan as P
    F.FitConfig.key = lambda self: {key!r}
    P.FIT_VERSION = {fit!r}
    F.FIT_VERSION = {fit!r}
    P.METRIC_VERSION = {metric!r}
sys.argv = ["pilot_report.py"] + {argv!r}
runpy.run_path({script!r}, run_name="__main__")
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--legacy-fit4", action="store_true")
    p.add_argument("--resamples", default="2000")
    p.add_argument("rest", nargs="*")
    args = p.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="report-", dir=HERE))
    outs = []
    for i, hashseed in enumerate(("0", "4242")):
        store = tmp / f"store{i}"
        shutil.copytree(args.root.resolve(), store)
        out = tmp / f"PILOT{i}.md"
        argv = [
            "--root",
            str(store),
            "--out",
            str(out),
            "--resamples",
            args.resamples,
            *args.rest,
        ]
        code = SHIM.format(
            scripts=str(ROOT / "scripts"),
            legacy=args.legacy_fit4,
            key=OLD_FIT_KEY,
            fit=OLD_FIT,
            metric=OLD_METRIC,
            argv=argv,
            script=str(ROOT / "scripts" / "pilot_report.py"),
        )
        env = {
            **os.environ,
            "PYTHONHASHSEED": hashseed,
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        r = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        print(
            f"run {i} (PYTHONHASHSEED={hashseed}) exit {r.returncode}: {r.stdout.strip()[-200:]} {r.stderr.strip()[-1500:]}"
        )
        outs.append(out)
    texts = []
    for out in outs:
        # the store path differs between the two copies; normalise it
        t = out.read_bytes().replace(str(out.parent.as_posix()).encode(), b"<TMP>")
        t = t.replace(b"store0", b"storeN").replace(b"store1", b"storeN")
        texts.append(t)
        print(out, len(t), hashlib.sha256(t).hexdigest()[:16])
    print("IDENTICAL" if texts[0] == texts[1] else "DIFFERENT")
    if texts[0] != texts[1]:
        a, b = texts[0].decode().splitlines(), texts[1].decode().splitlines()
        import difflib

        for line in list(difflib.unified_diff(a, b, lineterm=""))[:60]:
            print(line)
    print(f"(outputs in {tmp})")


if __name__ == "__main__":
    main()
