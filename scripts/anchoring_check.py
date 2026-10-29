"""P4 Δ6 anchoring check: AG-c-S named, v1 against link-neutral, dev split.

    # smoke: one truth, one session per variant
    uv run python scripts/anchoring_check.py --ids dev-000 --llm-limit 2

    # the check: 20 dev truths x seed 1 x 2 variants = 40 sessions
    uv run python scripts/anchoring_check.py --llm-concurrency 2

Only the two LLM arms ``AG-c-S/named`` and ``AG-c-S/named+link-neutral`` run,
plus ORACLE (seconds per truth) so that the report can state score gaps
against it. Environment binding is :mod:`scripts.pilot`'s. Resumable: rerun
the same command. Uses the logged-in Claude subscription (ANTHROPIC_API_KEY
must not be set).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Final

# Run as a script, scripts/ is on sys.path (as for pilot_report.py); pilot sets
# one BLAS thread per process before numpy loads.
import pilot

DEFAULT_ROOT: Final = pilot.ROOT / ".cache" / "anchoring-check"


def main() -> None:
    pilot.reconfigure_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--split", type=Path, default=pilot.DEFAULT_SPLIT)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--ids", default=None, help="comma-separated truth ids")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--llm-concurrency", type=int, default=1)
    p.add_argument("--llm-limit", type=int, default=None)
    args = p.parse_args()

    from sciagent.campaign.driver import Campaign, DriverSettings
    from sciagent.campaign.plan import ORACLE, CampaignConfig, LLMArm

    split = pilot.load_split(args.split)
    records = pilot.select(split, None, args.ids)
    config = CampaignConfig(
        seeds=(1,),
        control_seeds=(1,),
        baselines=(ORACLE,),
        llm_arms=(
            LLMArm("AG-c", "named"),
            LLMArm("AG-c", "named", prompt_variant="link-neutral"),
        ),
    )
    settings = DriverSettings(
        workers=args.workers,
        llm_concurrency=args.llm_concurrency,
        llm_limit=args.llm_limit,
        max_heavy=1,
    )
    campaign = Campaign(
        args.root,
        pilot.pointproc_environment(split),
        config,
        pilot.truth_entries(records),
        settings,
    )
    try:
        summary = campaign.run()
    finally:
        campaign.close()
    print(summary)


if __name__ == "__main__":
    main()
