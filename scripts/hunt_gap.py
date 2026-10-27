"""Per unit, the gap of each system to ORACLE on a store (the mini-pilot gate).

    uv run python scripts/hunt_gap.py --store .cache/minipilot --env fit/5

Rebuilds hunt A's ``p3_gap.py`` (HANDOFF-2026-10-06 step 3). Prints one line
per scored unit (truth, seed, system, held-out per-event LL, ORACLE's, gap,
ORACLE's ``oracle_underfit``), then the gate: 0 ``oracle_underfit`` flags and
none unrecorded, no gap above ``--threshold`` (+0.01), and every submitted unit
referenced to a finite ORACLE. Exit status 0 when the gate passes, 1 when it
fails. The contract is :mod:`sciagent.campaign.audit`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sciagent.campaign.audit import GAP_THRESHOLD, GapUnit, gap_check
from sciagent.campaign.store import ResultStore
from sciagent.scoring.gonogo import underfit_reason


def _num(x: float | None, spec: str = "+.4f") -> str:
    return "-" if x is None else format(x, spec)


def _line(u: GapUnit) -> str:
    flag = "" if u.system != "ORACLE" else f" underfit={u.oracle_underfit}"
    state = "" if u.ok else " FAILED"
    return (
        f"{u.truth} s{u.seed} {u.system}{' (llm)' if u.llm else ''}{state}: "
        f"LL {_num(u.per_event, '.4f')} ORACLE {_num(u.oracle, '.4f')} "
        f"gap {_num(u.gap)}{flag}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--env", required=True, help="fitter, e.g. fit/5")
    parser.add_argument("--threshold", type=float, default=GAP_THRESHOLD)
    args = parser.parse_args(argv)
    if not (args.store / "ledger.sqlite").is_file():
        print(f"{args.store}: not a campaign store", file=sys.stderr)
        return 2
    with ResultStore(args.store) as store:
        report = gap_check(store, args.env, threshold=args.threshold)
    for u in report.units:
        print(_line(u))
    print()
    print(f"units: {len(report.units)} under {args.env}")
    print(f"gap above {args.threshold:+g}: {len(report.above)}")
    for u in report.above:
        print(f"  {_line(u)}")
    print(f"submitted without a finite ORACLE: {len(report.unreferenced)}")
    for u in report.unreferenced:
        print(f"  {_line(u)}")
    check = report.underfit
    print(
        f"oracle_underfit: {len(check.flagged)} flagged, "
        f"{len(check.unrecorded)} unrecorded, of {check.checked} ORACLE units"
    )
    for reason in underfit_reason(check):
        print(f"  {reason}")
    print("GATE PASS" if report.passed else "GATE FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
