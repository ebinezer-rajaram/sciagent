"""Shared helpers for hunter A's property checks (fitting and scoring instruments).

Every script sets one BLAS thread before numpy loads, loads the dev split the
way scripts/pilot.py does, and writes JSON lines (one per check) so a run can
be resumed and re-read. Nothing here writes to src/, tests/ or any store.
"""

from __future__ import annotations

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_var] = "1"

import dataclasses
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))


SPLIT = ROOT / "data" / "v2" / "truths" / "dev"


def free_gb() -> float:
    out = subprocess.run(
        [
            "powershell",
            "-c",
            "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _pilot():
    """scripts/pilot.py: the campaign's own loaders."""
    import pilot

    return pilot


def records():
    return _pilot().load_split(SPLIT)


def entries(ids: list[str] | None = None):
    recs = records()
    if ids:
        recs = _pilot().select(recs, None, ",".join(ids))
    return _pilot().truth_entries(recs)


def env():
    return _pilot().pointproc_environment(records())


def config():
    from sciagent.campaign.plan import CampaignConfig

    return CampaignConfig()


def data_for(entry, seed: int):
    from sciagent.campaign.execute import observational_data

    return observational_data(entry, seed, env())


def held_for(entry, seed: int):
    from sciagent.scoring.heldout import held_out_dataset

    return held_out_dataset(entry.spec, entry.id, seed)


def _plain(value: Any) -> Any:
    from sciagent.glm.grammar import Structure
    from sciagent.glm.syntax import render

    if isinstance(value, float):
        return value.hex()
    if isinstance(value, Structure):
        return render(value)
    if isinstance(value, dict):
        return {
            str(k): _plain(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # enums
        return str(value.value)
    if hasattr(value, "name") and hasattr(value, "path"):  # PsiSlot
        return f"{value.name}@{value.path}"
    return value


def fit_record(result) -> dict[str, Any]:
    """Every compared field of a FitResult (all but wall_time), floats as hex."""
    out: dict[str, Any] = {}
    for f in dataclasses.fields(result):
        if f.name == "wall_time":
            continue
        out[f.name] = _plain(getattr(result, f.name))
    return out


def fit_digest(result) -> str:
    blob = json.dumps(fit_record(result), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()


def done_keys(path: Path, field: str = "key") -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)[field] for line in path.read_text().splitlines() if line}


def finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
