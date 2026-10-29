"""Copy the parity fixtures of criteria.md v7 §7 into this directory.

Sources (read-only): ../../fsd-trunc/trunc-s1-d0.jsonl and its .truths.jsonl,
../../fsd-rule/candidates.json. Copies: the lines and truth records of
dev-002, dev-005, dev-006 and dev-015, and the candidates' picks for them under
K0, K1, K2z, K3a, K3h and K4. manifest.json holds the sha256 of every source
and every copy. Run: python3 -I make_parity.py (stdlib only).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRAFTS = HERE.parent.parent
TRUTHS = ("dev-002", "dev-005", "dev-006", "dev-015")
RULES = ("K0", "K1", "K2z", "K3a", "K3h", "K4")
SOURCES = {
    "lines": DRAFTS / "fsd-trunc" / "trunc-s1-d0.jsonl",
    "truths": DRAFTS / "fsd-trunc" / "trunc-s1-d0.jsonl.truths.jsonl",
    "candidates": DRAFTS / "fsd-rule" / "candidates.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    lines = [
        raw
        for raw in SOURCES["lines"].read_text("utf-8").splitlines(keepends=True)
        if json.loads(raw)["truth"] in TRUTHS
    ]
    truths = [
        raw
        for raw in SOURCES["truths"].read_text("utf-8").splitlines(keepends=True)
        if json.loads(raw)["truth"] in TRUTHS
    ]
    cand = json.loads(SOURCES["candidates"].read_text("utf-8"))
    picks = {
        rule: {
            t: {k: cand[rule][t][k] for k in ("fsd", "tfsd", "tell")} for t in TRUTHS
        }
        for rule in RULES
    }
    out = {
        "lines": HERE / "trunc-lines.jsonl",
        "truths": HERE / "trunc-truths.jsonl",
        "candidates": HERE / "candidates-picks.json",
    }
    out["lines"].write_text("".join(lines), "utf-8")
    out["truths"].write_text("".join(truths), "utf-8")
    out["candidates"].write_text(json.dumps(picks, sort_keys=True) + "\n", "utf-8")
    manifest = {
        "truths": list(TRUTHS),
        "rules": list(RULES),
        "sources": {k: {"path": str(v), "sha256": sha(v)} for k, v in SOURCES.items()},
        "copies": {k: {"path": v.name, "sha256": sha(v)} for k, v in out.items()},
        "n_lines": len(lines),
        "n_truths": len(truths),
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=1) + "\n", "utf-8"
    )
    print(json.dumps(manifest, sort_keys=True, indent=1))


if __name__ == "__main__":
    main()
