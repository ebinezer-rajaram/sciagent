"""Defect hunt B, property 3 (determinism): a baseline job's cell bytes are
identical across processes with different PYTHONHASHSEED.

    uv run python .cache/hunt/B/baseline_determinism.py dev-004 1 ORACLE,B-lib,B-np
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

CHILD = r"""
import sys, hashlib
sys.path.insert(0, {scripts!r})
from pilot import load_split, pointproc_environment, truth_entries
from dataclasses import replace
from sciagent.campaign.plan import CampaignConfig, Unit, baseline_key, system_config_text
from sciagent.campaign.execute import BaselineJob, run_baseline_job
from sciagent.campaign.store import cell_bytes
from pathlib import Path
recs = load_split(Path({split!r}))
env = pointproc_environment(recs)
t = [x for x in truth_entries(recs) if x.id == {truth!r}][0]
config = CampaignConfig()
systems = tuple({systems!r}.split(","))
res = run_baseline_job(BaselineJob(t, {seed}, systems, replace(env, investigation=None), config))
for r in res:
    key = baseline_key(Unit(t.id, {seed}, r.system), t, env, config)
    detail = {{"unit": {{"truth": t.id, "seed": {seed}, "system": r.system, "kind": "baseline"}}, "result": r.detail,
              "system_config": system_config_text(r.system, env, config)}}
    b = cell_bytes(key, r.reading, detail)
    print(r.system, r.ok, hashlib.sha256(b).hexdigest(), r.detail.get("oracle_underfit"), r.reading.get("per_event"))
"""


def main() -> None:
    truth, seed, systems = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    code = CHILD.format(
        scripts=str(ROOT / "scripts"),
        split=str(ROOT / "data/v2/truths/dev"),
        truth=truth,
        seed=seed,
        systems=systems,
    )
    outs = []
    for hs in ("1", "999"):
        env = {
            **os.environ,
            "PYTHONHASHSEED": hs,
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
        )
        print(f"PYTHONHASHSEED={hs} exit {r.returncode}\n{r.stdout}{r.stderr[-1500:]}")
        outs.append(r.stdout)
    print("IDENTICAL" if outs[0] == outs[1] and outs[0] else "DIFFERENT_OR_EMPTY")


if __name__ == "__main__":
    main()
