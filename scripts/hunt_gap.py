"""Per unit, the gap of each system to ORACLE on a store (the mini-pilot gate).

    uv run python scripts/hunt_gap.py --store .cache/minipilot --env fit/5 \
        --split data/v2/truths/dev

Rebuilds hunt A's ``p3_gap.py`` (HANDOFF-2026-10-06 step 3). Prints one line
per scored unit (truth, seed, system, held-out per-event LL, ORACLE's, gap,
ORACLE's ``oracle_underfit``), then the gate: 0 ``oracle_underfit`` flags and
none unrecorded, no gap above ``--threshold`` (+0.01), and every submitted unit
referenced to a finite ORACLE. From ``--split`` (the truths' split directory,
required: the gate is not run without it) it computes, per (truth, seed), the
held-out per-event log L of the truth's own parameters on the same held-out
log the cells were scored on, and fails any unit above it by more than the
threshold (p3_gap's "BEATS-TRUTH" leakage flag). The reference carries the
digest of the split's truth record, and any unit whose own ``truth_digest``
(an LLM score's, through its session) is not that digest, or cannot be
established, fails the gate by name: two splits can share their truth ids
(the Windows and Linux dev splits do). Exit status 0 when the gate passes,
1 when it fails, 2 on a usage error. The contract is
:mod:`sciagent.campaign.audit`.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from collections.abc import Iterable
from pathlib import Path

from sciagent.campaign.audit import GAP_THRESHOLD, GapUnit, TruthReference, gap_check
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


def truth_reference(
    units: Iterable[tuple[str, int]], split: Path
) -> dict[tuple[str, int], TruthReference]:
    """Per (truth id, seed), the held-out per-event log L of the truth's own
    parameters, and the digest of the split's record of that truth (computed
    as ``pilot.truth_entries`` computes the cells' ``truth_digest``): the
    held-out log and the quadrature rule are the ones a cell's ``per_event``
    uses (``campaign.execute``; ``GLMModel`` evaluates at the default rule)."""
    from pilot import load_split, pointproc_environment, truth_entries

    from sciagent.glm.fit import parameter_log_likelihood
    from sciagent.scoring.heldout import held_out_dataset
    from sciagent.systems.v2.models import per_event

    records = load_split(split)
    channels = pointproc_environment(records).channels
    by_id = {t.id: t for t in truth_entries(records)}
    out: dict[tuple[str, int], TruthReference] = {}
    for truth_id, seed in sorted(set(units)):
        if truth_id not in by_id:
            raise SystemExit(f"truth {truth_id} is not in the split {split}")
        entry = by_id[truth_id]
        spec = entry.spec
        held = held_out_dataset(spec, truth_id, seed)
        total = math.fsum(
            parameter_log_likelihood(
                spec.structure, spec.psi, spec.coef, [held], channels
            )
        )
        out[(truth_id, seed)] = TruthReference(entry.digest, per_event(total, [held]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--env", required=True, help="fitter, e.g. fit/5")
    parser.add_argument("--threshold", type=float, default=GAP_THRESHOLD)
    parser.add_argument(
        "--split",
        type=Path,
        required=True,
        help="the truths' split directory the store was run on: no unit may "
        "beat the truth's own parameters, and every unit's truth record must "
        "be this split's",
    )
    args = parser.parse_args(argv)
    if not (args.store / "ledger.sqlite").is_file():
        print(f"{args.store}: not a campaign store", file=sys.stderr)
        return 2
    if not (args.split / "truths.json").is_file():
        print(f"{args.split}: no truths.json", file=sys.stderr)
        return 2
    split_sha = hashlib.sha256((args.split / "truths.json").read_bytes()).hexdigest()
    with ResultStore(args.store, read_only=True) as store:
        units = gap_check(store, args.env, threshold=args.threshold).units
        truth = truth_reference(((u.truth, u.seed) for u in units), args.split)
        report = gap_check(store, args.env, threshold=args.threshold, truth=truth)
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
    print(f"split: {args.split} truths.json sha256 {split_sha}")
    print(f"not the split's truth record: {len(report.digest_issues)}")
    for u in report.digest_issues:
        print(f"  {_line(u)}: {u.digest_issue}")
    print(
        f"above the truth's own parameters by > {args.threshold:+g}: "
        f"{len(report.beats_truth)}"
    )
    for u in report.beats_truth:
        print(f"  {_line(u)} truth {_num(u.truth_ll, '.4f')} over {_num(u.over_truth)}")
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
