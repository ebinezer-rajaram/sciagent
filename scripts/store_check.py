"""Check that a campaign store is internally consistent.

    uv run python scripts/store_check.py --store .cache/minipilot

Rebuilds hunt B's ``store_check.py`` (HANDOFF-2026-10-06 step 3): append-only
triggers, content addressing of ledger and detail files, canonical bytes,
derived address text, and resolving references (scores to sessions, sessions
to record dirs). Prints every issue and warning; exit status 0 when there is
no issue, 1 otherwise. The contract is :mod:`sciagent.campaign.audit`. Opens
the ledger read-only for the trigger and digest checks; never writes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sciagent.campaign.audit import check_store
from sciagent.campaign.plan import CampaignError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        check = check_store(args.store)
    except CampaignError as error:
        print(str(error), file=sys.stderr)
        return 2
    for f in check.issues:
        print(f"ISSUE {f.kind} {f.where}: {f.message}")
    for f in check.warnings:
        print(f"warning {f.kind} {f.where}: {f.message}")
    print(
        f"cells: {check.cells}; issues: {len(check.issues)}; "
        f"warnings: {len(check.warnings)}"
    )
    print("STORE OK" if check.passed else "STORE INCONSISTENT")
    return 0 if check.passed else 1


if __name__ == "__main__":
    sys.exit(main())
