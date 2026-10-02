"""The P3 pilot driver (SPEC §7, P3): pointproc dev truths x seeds x systems.

    uv run python scripts/pilot.py --root .cache/pilot --truths 20 \
        --workers 4 --llm-concurrency 1

    # smoke: 2 truths, seed 1, every system, at most 4 live LLM sessions
    uv run python scripts/pilot.py --root .cache/pilot --truths 2 --seeds 1 \
        --llm-limit 4

Binds pointproc to the domain-independent campaign driver
(``sciagent.campaign``): the dev split's truths (``records.read_split``,
hash-verified), channels and marks, B-lib's library, B-sym's seeds (the
library's grammar members), B-rand's prior (the truth sampler's structure
prior with its declared out-of-dictionary share) and the investigation
environment the LLM runner resolves truths from. Results go to ``--root``
(git-ignored) and are resumable: rerun the same command to continue; create
``<root>/STOP`` to stop launching new units (running ones finish).

A live LLM run uses the logged-in Claude subscription (do not set
ANTHROPIC_API_KEY; the harness refuses it).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

# One BLAS thread per process, set before numpy loads (everything numpy-backed
# is imported inside functions below); the campaign's spawned workers inherit
# it, and the driver sets it again before its pool exists.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_var] = "1"

if TYPE_CHECKING:
    from sciagent.campaign.plan import CampaignEnvironment, TruthEntry
    from sciagent.investigation.world import Truth, TruthSpec
    from sciagent.scenarios.records import TruthRecord

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_SPLIT: Final = ROOT / "data" / "v2" / "truths" / "dev"
DEFAULT_ROOT: Final = ROOT / ".cache" / "pilot"
#: The anonymised channel names SPEC §5 fixes for pointproc.
ANON_CHANNELS: Final = {"size": "m1", "sign": "m2"}
#: pointproc's own leak terms beyond the default list.
FORBIDDEN: Final = ("size", "sign", "arrival")
#: The investigation environment's name (as scripts/investigate.py).
ENV_NAME: Final = "pointproc-v2"


def reconfigure_stdout() -> None:
    """UTF-8 console output (a Windows console is cp1252; DSL text carries ψ)."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def wait_for_split(directory: Path, timeout_s: float, poll_s: float = 60.0) -> None:
    """Block until ``manifest.json`` exists in ``directory`` (or time out)."""
    deadline = time.monotonic() + timeout_s
    while not (directory / "manifest.json").exists():
        if time.monotonic() > deadline:
            raise SystemExit(f"no manifest.json in {directory} after {timeout_s}s")
        print(f"waiting for {directory / 'manifest.json'} ...", flush=True)
        time.sleep(poll_s)


def truth_spec(record: TruthRecord) -> TruthSpec:
    from environments.pointproc import v2
    from sciagent.investigation.world import TruthSpec

    return TruthSpec(
        record.structure(v2.CHANNELS),
        record.psi_assignment(),
        record.coefficients(),
        v2.CHANNELS,
        v2.mark_sampler,
    )


def truth_entries(records: tuple[TruthRecord, ...]) -> tuple[TruthEntry, ...]:
    from sciagent.campaign.plan import TruthEntry
    from sciagent.scenarios.records import canonical_json

    return tuple(
        TruthEntry(
            id=r.id,
            digest=hashlib.sha256(
                canonical_json(r.to_json()).encode("utf-8")
            ).hexdigest(),
            spec=truth_spec(r),
            in_dictionary=not r.out_of_dictionary,
            stratum=r.stratum,
            nearest_distance=r.nearest_distance,
            dsl=r.dsl,
        )
        for r in records
    )


def load_split(directory: Path) -> tuple[TruthRecord, ...]:
    from sciagent.scenarios.records import read_split

    records, _ = read_split(directory)
    return records


@dataclass(frozen=True)
class RecordResolver:
    """Truth id -> truth, from split records (picklable, so an isolated LLM
    session's process can rebuild its world)."""

    records: tuple[TruthRecord, ...]

    def __call__(self, truth_id: str) -> Truth:
        for r in self.records:
            if r.id == truth_id:
                return truth_spec(r)
        raise KeyError(truth_id)


def pointproc_environment(records: tuple[TruthRecord, ...]) -> CampaignEnvironment:
    """pointproc as a :class:`CampaignEnvironment`, truths resolved from ``records``."""
    from environments.pointproc import v2
    from environments.pointproc.truths_v2 import (
        SAMPLER_CONFIG,
        STRUCTURE_PRIOR,
        environment,
    )
    from sciagent.campaign.plan import CampaignEnvironment
    from sciagent.investigation import EnvironmentSpec
    from sciagent.scenarios.sampler import config_digest
    from sciagent.systems.v2.search import ScenarioPrior
    from sciagent.systems.v2.systems import GrammarMember

    share = SAMPLER_CONFIG.out_of_dictionary_share
    return CampaignEnvironment(
        name=ENV_NAME,
        channels=v2.CHANNELS,
        marks=v2.mark_sampler,
        library=v2.B_LIB_LIBRARY,
        sym_seeds=tuple(
            m.structure for m in v2.B_LIB_LIBRARY if isinstance(m, GrammarMember)
        ),
        prior=ScenarioPrior(STRUCTURE_PRIOR, v2.CHANNELS, share),
        prior_id=f"{config_digest(SAMPLER_CONFIG, environment())}|ood={share!r}",
        investigation=EnvironmentSpec(
            name=ENV_NAME,
            anon_channels=ANON_CHANNELS,
            forbidden=FORBIDDEN,
            resolve=RecordResolver(records),
        ),
    )


def select(
    records: tuple[TruthRecord, ...], n: int | None, ids: str | None
) -> tuple[TruthRecord, ...]:
    if ids:
        wanted = ids.split(",")
        by_id = {r.id: r for r in records}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise SystemExit(f"unknown truth ids {missing}")
        return tuple(by_id[i] for i in wanted)
    return records if n is None else records[:n]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--truths", type=int, default=None, help="first N truths")
    p.add_argument("--ids", default=None, help="comma-separated truth ids")
    p.add_argument("--seeds", default="1,2,3")
    p.add_argument("--control-seeds", default="1")
    p.add_argument(
        "--b10f-truths",
        type=int,
        default=None,
        help="run B-sym@10F on the first K truths in split order only",
    )
    p.add_argument("--max-heavy", type=int, default=2, help="B-sparse jobs at once")
    p.add_argument("--systems", default=None, help="comma-separated non-LLM systems")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--llm-concurrency", type=int, default=1)
    p.add_argument("--llm-limit", type=int, default=None)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-baselines", action="store_true")
    p.add_argument("--max-turns", type=int, default=100)
    p.add_argument(
        "--arms", default=None, help="comma-separated LLM arms, e.g. AG-o/anon"
    )
    p.add_argument("--wait", type=float, default=0.0, help="wait for the split (s)")
    return p


def main() -> None:
    reconfigure_stdout()
    args = build_parser().parse_args()
    from sciagent.campaign.driver import Campaign, DriverSettings
    from sciagent.campaign.plan import (
        BASELINES,
        PILOT_ARMS,
        CampaignConfig,
        LLMArm,
    )

    if args.wait:
        wait_for_split(args.split, args.wait)
    records = select(load_split(args.split), args.truths, args.ids)
    env = pointproc_environment(load_split(args.split))
    seeds = tuple(int(s) for s in args.seeds.split(","))
    control = tuple(int(s) for s in args.control_seeds.split(",") if int(s) in seeds)
    baselines = BASELINES if args.systems is None else tuple(args.systems.split(","))
    arms: tuple[LLMArm, ...] = PILOT_ARMS
    if args.arms is not None:
        wanted = args.arms.split(",")
        arms = tuple(a for a in PILOT_ARMS if f"{a.arm}/{a.condition}" in wanted)
    config = CampaignConfig(
        seeds=seeds,
        control_seeds=control,
        baselines=baselines,
        llm_arms=arms,
        max_turns=args.max_turns,
        control_truths=args.b10f_truths,
    )
    settings = DriverSettings(
        workers=args.workers,
        llm_concurrency=args.llm_concurrency,
        llm_limit=args.llm_limit,
        run_baselines=not args.no_baselines,
        run_llm=not args.no_llm,
        max_heavy=args.max_heavy,
    )
    campaign = Campaign(args.root, env, config, truth_entries(records), settings)
    try:
        summary = campaign.run()
    finally:
        campaign.close()
    print(summary)


if __name__ == "__main__":
    main()
