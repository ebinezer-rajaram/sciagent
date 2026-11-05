"""First-turn context and ``predict`` calls across every AG record.

    uv run python scripts/p3_report/first_turn_and_predict.py --root .cache/pilot-merged

Red team findings 4 and 5 (``docs/v2/redteam/2026-11-05_ag-o-transcript-read.md``):
the first assistant entry's ``usage`` per arm, as its input, cache-creation and
cache-read tokens and their total, then ``predict`` calls and sessions per arm,
accepted and refused. Read-only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _kind(entry: dict[str, Any]) -> str:
    return str(entry.get("kind", entry.get("type")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    root = parser.parse_args().root / "llm"
    usage: Counter[tuple[str, int, int, int]] = Counter()
    calls: Counter[tuple[str, bool]] = Counter()
    tried: Counter[str] = Counter()
    accepted: Counter[str] = Counter()
    records = sorted(root.glob("*/investigation-*.json"))
    for path in records:
        entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
        config = next(e for e in entries if _kind(e) == "config")
        investigation = config["body"]["investigation"]
        arm = f"{investigation['arm']}/{investigation['condition']}"
        first = next(e for e in entries if _kind(e) == "assistant")["body"]
        tokens = first.get("usage") or first["message"]["usage"]
        usage[
            (
                arm,
                tokens["input_tokens"],
                tokens["cache_creation_input_tokens"],
                tokens["cache_read_input_tokens"],
            )
        ] += 1
        refused = [
            bool(e["body"]["is_error"])
            for e in entries
            if _kind(e) == "tool_call" and str(e["body"]["name"]).endswith("predict")
        ]
        for error in refused:
            calls[(arm, error)] += 1
        tried[arm] += bool(refused)
        accepted[arm] += not all(refused) if refused else False
    print(f"records {len(records)}")
    print("arm, input, cache creation, cache read, total: records")
    for (arm, fresh, created, read), count in sorted(usage.items()):
        print(f"{arm}, {fresh}, {created}, {read}, {fresh + created + read}: {count}")
    print("arm: predict accepted, refused; sessions tried, with one accepted")
    for arm in sorted({a for a, *_ in usage}):
        print(
            f"{arm}: {calls[(arm, False)]}, {calls[(arm, True)]}; "
            f"{tried[arm]}, {accepted[arm]}"
        )


if __name__ == "__main__":
    main()
