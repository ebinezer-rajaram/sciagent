"""Check that a campaign store is internally consistent.

    uv run python scripts/store_check.py --store .cache/minipilot

Rebuilds hunt B's ``store_check.py`` (HANDOFF-2026-10-06 step 3): append-only
triggers, content addressing of ledger and detail files, canonical bytes,
derived address text, and resolving references (scores to sessions, sessions
to record dirs), and hunt B's §3: the budget accounting of every completed
session's record (charges, ``budgets_after``, limits, the cell's counts).
Also prints the store's code-fingerprint history (``fingerprint.jsonl``,
:mod:`sciagent.campaign.fingerprint`), one line per entry, or that none is
recorded; a malformed sidecar is an issue.
Prints every issue and warning; exit status 0 when there is no issue, 1
otherwise. The contract is :mod:`sciagent.campaign.audit`
(:func:`~sciagent.campaign.audit.check_store`,
:func:`~sciagent.campaign.audit.check_budgets`). Opens the ledger read-only
for the trigger and digest checks; never writes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sciagent.campaign.audit import check_budgets, check_store
from sciagent.campaign.fingerprint import (
    FINGERPRINT_FILE,
    CodeFingerprintError,
    read_fingerprints,
)
from sciagent.campaign.plan import CampaignError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        check = check_store(args.store)
        budgets = check_budgets(args.store)
    except CampaignError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        history = read_fingerprints(args.store)
        sidecar = None
    except CodeFingerprintError as error:
        history, sidecar = (), str(error)
    for f in check.issues + budgets.issues:
        print(f"ISSUE {f.kind} {f.where}: {f.message}")
    if sidecar is not None:
        print(f"ISSUE fingerprint {FINGERPRINT_FILE}: {sidecar}")
    for f in check.warnings:
        print(f"warning {f.kind} {f.where}: {f.message}")
    print(
        f"cells: {check.cells}; issues: {len(check.issues)}; "
        f"warnings: {len(check.warnings)}"
    )
    print(f"sessions audited: {budgets.cells}; budget issues: {len(budgets.issues)}")
    if sidecar is None and not history:
        print("code fingerprint: none recorded")
    for n, e in enumerate(history, 1):
        source = "" if e.reuse_from is None else f" from {e.reuse_from}"
        print(f"code fingerprint {n}: {e.event} {e.fingerprint}{source}")
    passed = check.passed and budgets.passed and sidecar is None
    print("STORE OK" if passed else "STORE INCONSISTENT")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
