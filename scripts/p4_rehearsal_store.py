"""The store section 2.10's rehearsal runs on (prereg 2026-11-05 section 2.10).

    uv run python scripts/p4_rehearsal_store.py --split <abs dev split> \
        --store <abs path under .cache/drafts> --rehearsal-plan <json>

"The first eight dev truths by sorted id, four of them out of dictionary, two seeds,
baseline systems at reduced budget and scripted sessions, no LLM call." The plan is
the file ``scripts/p4_search_misses.py --rehearsal`` takes, and this script builds
everything from :func:`p4_search_misses.rehearsal_world`, so the two cannot address
a unit differently. It refuses what that script's allowlist refuses
(:func:`p4_search_misses.check_rehearsal`), and any seeds but 1 and 2.

**What runs.** The real :class:`~sciagent.campaign.driver.Campaign`, so cells, session
records and scores are the campaign's own. Each session plays
``planted_hint_script`` on a :class:`~sciagent.harness.scripted.ScriptedDriver`
through the real tool layer: it plants a hint, fits, runs one experiment, and submits
the truth. :func:`scripted_factory` never answers ``None``, which is what would
launch a live session. An AG-o session needs the sandbox image, as a live one does.

**``--only``** fills part of the plan. It selects units and addresses none, so those
cells are the plan's own, and the script refuses the store as missing the rest. The
campaign skips what is stored, so this also fills a store in parts.

The numbers in a rehearsal store are dev readings at a reduced budget. They are
exploratory, and may not be cited as a P4 result.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import p4_search_misses as misses
import pilot

from sciagent.campaign.driver import Campaign, DriverSettings, RunSummary
from sciagent.campaign.plan import (
    CampaignConfig,
    CampaignError,
    TruthEntry,
    Unit,
)
from sciagent.harness.scripted import ScriptedDriver
from sciagent.investigation.view import make_view
from sciagent.scoring.controls_p4 import PLANTED_FITS, planted_hint_script

#: Seeds 4 to 6 are not this script's to draw.
REHEARSAL_SEEDS: Final = (1, 2)
#: The experiments the planted script runs.
PLANTED_EXPERIMENTS: Final = 1
#: Beside the store and not in it, so the manifest does not see a sandbox.
SANDBOX_DIR: Final = "sandbox"
REFUSED_EXIT: Final = 2


@dataclass(frozen=True)
class Made:
    """What was built and how the run went."""

    plan: tuple[CampaignConfig, ...]
    truths: tuple[TruthEntry, ...]
    run: RunSummary


def scripted_factory(
    config: CampaignConfig, truths: Sequence[TruthEntry]
) -> Callable[[Unit], ScriptedDriver]:
    """Each unit's scripted agent, in its arm's names and units."""
    by_id = {t.id: t for t in truths}

    def factory(unit: Unit) -> ScriptedDriver:
        truth = by_id[unit.truth_id]
        condition = config.arm_of(unit.system).condition
        view = make_view(condition, truth.spec.channels, pilot.ANON_CHANNELS)
        return ScriptedDriver(planted_hint_script(truth.structure, view))

    return factory


def check_plan(
    config: CampaignConfig, truths: Sequence[TruthEntry], only: Sequence[str]
) -> None:
    """What this maker adds to the script's allowlist."""
    if config.seeds != REHEARSAL_SEEDS:
        raise misses.GuardFailure(
            f"a rehearsal store is built on seeds 1 and 2, not {config.seeds}"
        )
    if config.fits < PLANTED_FITS or config.experiments < PLANTED_EXPERIMENTS:
        raise misses.GuardFailure(
            f"the planted script needs {PLANTED_FITS} fits and "
            f"{PLANTED_EXPERIMENTS} experiment; the plan gives {config.fits} and "
            f"{config.experiments}"
        )
    outside = sorted(set(only) - {t.id for t in truths})
    if outside:
        raise misses.GuardFailure(f"--only names {outside}, not truths of the plan")


def make_store(
    repo: Path,
    *,
    split: Path,
    store: Path,
    plan: Path,
    only: tuple[str, ...],
    workers: int,
    poll_s: float = 5.0,
) -> Made:
    """The plan's units into ``store``, or those of ``only``. Every refusal comes
    before the campaign exists, so nothing is opened."""
    misses.check_rehearsal(repo, split=split, store=store, test_split=None)
    misses.check_blas(misses.initial_environment())
    env, configs, truths = misses.rehearsal_world(plan, split)
    (config,) = configs
    check_plan(config, truths, only)
    fingerprint = pilot.code_fingerprint(repo)
    settings = DriverSettings(
        workers=workers,
        max_attempts=1,
        poll_s=poll_s,
        sandbox_root=store.parent / SANDBOX_DIR,
        driver_factory=scripted_factory(config, truths),
    )
    campaign = Campaign(
        store,
        env,
        replace(config, only_truths=only) if only else config,
        truths,
        settings,
        fingerprint=fingerprint,
    )
    try:
        summary = campaign.run()
    finally:
        campaign.close()
    return Made(configs, truths, summary)


def build_parser() -> argparse.ArgumentParser:
    """No option sets a seed, a root or a budget: those are the plan's."""
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--split", type=Path, required=True, help="the dev split, absolute")
    p.add_argument(
        "--store", type=Path, required=True, help="absolute, under .cache/drafts"
    )
    p.add_argument(
        "--rehearsal-plan",
        type=Path,
        required=True,
        help="the file the search-miss script's --rehearsal takes",
    )
    p.add_argument(
        "--only", default="", help="comma-separated truths of the plan to fill"
    )
    p.add_argument("--workers", type=int, choices=(1, 2), default=2)
    return p


def main(argv: Sequence[str] | None = None, *, repo: Path = pilot.ROOT) -> int:
    pilot.reconfigure_stdout()
    args = build_parser().parse_args(argv)
    only = tuple(x for x in args.only.split(",") if x)
    try:
        made = make_store(
            repo,
            split=args.split,
            store=args.store,
            plan=args.rehearsal_plan,
            only=only,
            workers=args.workers,
        )
    except (misses.GuardFailure, CampaignError) as error:
        print(f"refused: {error}", flush=True)
        return REFUSED_EXIT
    print(made.run, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
