"""Merge stores written in parallel by one checkout into one new store.

    uv run python scripts/store_merge.py .cache/pilot .cache/pilot-llm \
        --out .cache/pilot-merged

For a pilot run as two drivers (``pilot.py --no-llm`` on one root,
``--no-baselines`` on another, from the same commit): the readers
(``pilot_report.py``, ``hunt_gap.py``, ``store_check.py``,
``session_check.py``, ``replay_check.py``) then run unchanged on ``--out``.
The sources are only read. Refused, with nothing written, unless every source
has one established code fingerprint and it is the same, no cell is in two
sources, each truth id has one record, and ``--out`` does not exist. A source
still being written is merged as a snapshot of its committed cells; merge
again into a new ``--out`` for a later read. The contract is
:mod:`sciagent.campaign.merge`. Exit status 0 when merged, 2 when refused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sciagent.campaign.merge import merge_stores
from sciagent.campaign.plan import CampaignError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = merge_stores(args.sources, args.out)
    except CampaignError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(
        f"merged {len(args.sources)} stores "
        f"({' + '.join(str(n) for n in summary.cells)} cells) under "
        f"{summary.fingerprint} into {args.out}: {summary.record_dirs} record "
        f"dirs, {summary.timing_lines} timing lines"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
