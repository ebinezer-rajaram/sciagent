"""Compare two campaign stores cell by cell for byte identity.

    uv run python scripts/store_compare.py .cache/minipilot .cache/minipilot-r2

The determinism half of the mini-pilot gate (HANDOFF-2026-10-06 step 1:
"compare its cell bytes with run 1"). Every committed digest must be in both
stores, and its detail file and ledger reading must be byte-identical.
``timing.jsonl`` (wall-clock) is not compared; ``--kinds baseline,llm-score``
leaves out LLM sessions, which are not reproducible across live runs. Exit
status 0 when identical, 1 otherwise. The contract is :mod:`sciagent.campaign.audit`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sciagent.campaign.audit import compare_stores
from sciagent.campaign.plan import CampaignError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument(
        "--kinds",
        default=None,
        help="comma-separated cell kinds to compare (default: every cell); "
        "llm-session cells are not reproducible across live runs",
    )
    args = parser.parse_args(argv)
    try:
        kinds = None if args.kinds is None else tuple(args.kinds.split(","))
        diff = compare_stores(args.a, args.b, kinds=kinds)
    except CampaignError as error:
        print(str(error), file=sys.stderr)
        return 2
    for d in diff.only_a:
        print(f"only in {args.a}: {d}")
    for d in diff.only_b:
        print(f"only in {args.b}: {d}")
    for d in diff.differing:
        print(f"bytes differ: {d}")
    print(
        f"identical cells: {diff.same}; only in a: {len(diff.only_a)}; "
        f"only in b: {len(diff.only_b)}; differing: {len(diff.differing)}"
    )
    print("IDENTICAL" if diff.identical else "DIFFERENT")
    return 0 if diff.identical else 1


if __name__ == "__main__":
    sys.exit(main())
