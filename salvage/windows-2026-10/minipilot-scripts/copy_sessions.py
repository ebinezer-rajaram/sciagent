"""Copy the pilot's llm-session cells for given truths/seed into another store.

    uv run python copy_sessions.py SRC DST --ids dev-008,dev-013,dev-017 --seed 1

Copies details/<digest>.json and llm/<digest16>/ verbatim, then commits the
ledger rows through ResultStore.recover() (append-only; no existing cell is
touched). Refuses if a destination file exists with different bytes.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
from pathlib import Path

from sciagent.campaign.store import ResultStore


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("src", type=Path)
    p.add_argument("dst", type=Path)
    p.add_argument("--ids", required=True)
    p.add_argument("--seed", type=int, required=True)
    a = p.parse_args()
    ids = set(a.ids.split(","))
    with ResultStore(a.src) as src:
        cells = [
            c
            for c in src.cells()
            if c["key"]["config"].get("kind") == "llm-session"
            and c["key"]["config"].get("truth") in ids
            and int(c["key"]["seed"]) == a.seed
        ]
    for c in cells:
        d = c["digest"]
        for rel in (Path("details") / f"{d}.json", Path("llm") / d[:16]):
            s, t = a.src / rel, a.dst / rel
            if not s.exists():
                raise FileNotFoundError(s)
            if s.is_dir():
                if t.exists():
                    cmp = filecmp.dircmp(s, t)
                    if cmp.left_only or cmp.right_only or cmp.diff_files:
                        raise FileExistsError(f"{t} differs from {s}")
                else:
                    shutil.copytree(s, t)
            elif t.exists():
                if t.read_bytes() != s.read_bytes():
                    raise FileExistsError(f"{t} differs from {s}")
            else:
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(s, t)
        cfg = c["key"]["config"]
        print(d[:16], cfg["truth"], cfg["system"])
    with ResultStore(a.dst) as dst:
        print("recovered rows:", dst.recover())
    print(json.dumps({"copied": len(cells)}))


if __name__ == "__main__":
    main()
