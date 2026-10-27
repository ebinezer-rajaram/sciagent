"""Compare two campaign stores' baseline cells byte for byte (read-only).

    python compare_runs.py STORE_A STORE_B

Every details/<digest>.json whose key kind is not llm-* must exist in both
stores with identical bytes; prints a per-file verdict and a summary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def baseline_cells(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted((root / "details").glob("*.json")):
        data = p.read_bytes()
        kind = json.loads(data)["key"]["config"].get("kind", "")
        if not kind.startswith("llm"):
            out[p.name] = data
    return out


def main() -> None:
    a, b = (baseline_cells(Path(x)) for x in sys.argv[1:3])
    bad = 0
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            print("MISSING", name, "in", "A" if name not in a else "B")
            bad += 1
        elif a[name] != b[name]:
            print("DIFFER ", name)
            bad += 1
        else:
            cfg = json.loads(a[name])["key"]["config"]
            print("same   ", name[:16], cfg.get("system"), cfg.get("truth"))
    print(f"cells A={len(a)} B={len(b)} mismatches={bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
