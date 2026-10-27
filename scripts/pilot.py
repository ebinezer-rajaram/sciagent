"""The P3 pilot driver (SPEC §7, P3): pointproc dev truths x seeds x systems.

    uv run python scripts/pilot.py --root .cache/pilot --truths 20 \
        --workers 4 --llm-concurrency 1

    # smoke: 2 truths, seed 1, every system, at most 4 live LLM sessions
    uv run python scripts/pilot.py --root .cache/pilot --truths 2 --seeds 1 \
        --llm-limit 4

    # P3.1 (P4-PROPOSAL §2.2): rerun the non-LLM units under the current
    # fitter and rescore the stored LLM sessions; no live session is launched
    uv run python scripts/pilot.py --root .cache/pilot --truths 20 \
        --b10f-truths 10 --workers 4 --rescore-llm

Binds pointproc to the domain-independent campaign driver
(``sciagent.campaign``): the dev split's truths (``records.read_split``,
hash-verified), channels and marks, B-lib's library, B-sym's seeds (the
library's grammar members), B-rand's prior (the truth sampler's structure
prior with its declared out-of-dictionary share) and the investigation
environment the LLM runner resolves truths from. Results go to ``--root``
(git-ignored) and are resumable: rerun the same command to continue; create
``<root>/STOP`` to stop launching new units (running ones finish).

Every run is guarded by the checkout's code fingerprint (:func:`code_fingerprint`:
the commit and a clean-tree flag; LOG 2026-10-27, F5), taken after ``--wait``
and checked again after the run (a change during the run exits non-zero). A
dirty tree, a copy that is not its own checkout, or packages imported from
another checkout refuse to run. A store written under another fingerprint
refuses before any unit is launched, unless ``--reuse-from`` names the store's
fingerprint exactly, which is recorded in ``<root>/fingerprint.jsonl``
(``--reuse-from unrecorded`` for a store with cells and no fingerprint). One
driver per store: a second ``pilot.py`` on the same ``--root`` is refused.

A live LLM run uses the logged-in Claude subscription (do not set
ANTHROPIC_API_KEY; the harness refuses it).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

# One BLAS thread per process, set before numpy loads (everything numpy-backed
# is imported inside functions below); the campaign's spawned workers inherit
# it, and the driver sets it again before its pool exists.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_var] = "1"

if TYPE_CHECKING:
    from sciagent.campaign.plan import CampaignConfig, CampaignEnvironment, TruthEntry
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


def _git(repo: Path, *args: str) -> str:
    from sciagent.campaign.plan import CampaignError

    try:
        done = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None) or error
        raise CampaignError(
            f"cannot read the code fingerprint: git {' '.join(args)} in {repo} "
            f"failed: {str(detail).strip()}"
        ) from error
    return done.stdout


def _running_packages() -> tuple[Path, Path]:
    """Where this process imported ``sciagent`` and ``environments`` from."""
    import environments
    import sciagent

    return (Path(sciagent.__file__), Path(environments.__file__))


def code_fingerprint(repo: Path = ROOT, packages: Sequence[Path] | None = None) -> str:
    """The checkout's code fingerprint, ``commit=<sha>,tree=clean``.

    Refuses (:class:`~sciagent.campaign.plan.CampaignError`) unless the commit
    describes the code that runs:

    - ``packages`` (default: where ``sciagent`` and ``environments`` were
      imported from) lie under ``repo/src``: ``uv run`` may have picked another
      checkout's environment;
    - ``repo`` is the top of its own git checkout, not a copy inside another
      (which would get the parent's HEAD);
    - the tree is clean: ``git status --porcelain --untracked-files=all`` lists
      nothing, whatever ``status.showUntrackedFiles`` says, and no file is
      flagged assume-unchanged or skip-worktree (``git ls-files -v``), which
      would hide an edit. Ignored files (the copied split under ``data/``,
      ``.venv``, ``.cache``) do not count.

    ``uv.lock`` is tracked, so the commit and the clean tree cover it.
    """
    from sciagent.campaign.plan import CampaignError

    top = repo.resolve()
    src = top / "src"
    for package in packages if packages is not None else _running_packages():
        if not package.resolve().is_relative_to(src):
            raise CampaignError(
                f"code fingerprint: {package} is imported from outside {src}; the "
                f"commit of {top} would not describe the code that runs (run "
                f"`uv run` from {top})"
            )
    toplevel = _git(repo, "rev-parse", "--show-toplevel").strip()
    if Path(toplevel).resolve() != top:
        raise CampaignError(
            f"{top} is not its own git checkout (its repository's top is "
            f"{toplevel}); the fingerprint would describe other code"
        )
    head = _git(repo, "rev-parse", "HEAD").strip()
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    hidden = [
        line
        for line in _git(repo, "ls-files", "-v").splitlines()
        if line[:1].islower() or line[:1] == "S"
    ]
    if dirty or hidden:
        paths = dirty + [f"flagged {line}" for line in hidden]
        shown = "; ".join(paths[:5]) + ("; ..." if len(paths) > 5 else "")
        raise CampaignError(
            f"the working tree at {top} is not clean ({len(paths)} paths: "
            f"{shown}); commit or remove the changes (and clear assume-unchanged "
            f"or skip-worktree flags) first, so the store records the code that "
            f"wrote it"
        )
    return f"commit={head},tree=clean"


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
    p.add_argument(
        "--reuse-from",
        default=None,
        help="the store's code fingerprint, exactly, to keep its cells under this "
        "checkout's (recorded in the store); 'unrecorded' for a store written "
        "before fingerprints were kept",
    )
    return p


def campaign_config(args: argparse.Namespace) -> CampaignConfig:
    """The run's :class:`CampaignConfig` from :func:`build_parser`'s arguments
    (shared with ``scripts/session_check.py``, which must rebuild exactly the
    configuration a run used)."""
    from sciagent.campaign.plan import BASELINES, PILOT_ARMS, CampaignConfig, LLMArm

    seeds = tuple(int(s) for s in args.seeds.split(","))
    control = tuple(int(s) for s in args.control_seeds.split(",") if int(s) in seeds)
    baselines = BASELINES if args.systems is None else tuple(args.systems.split(","))
    arms: tuple[LLMArm, ...] = PILOT_ARMS
    if args.arms is not None:
        wanted = args.arms.split(",")
        arms = tuple(a for a in PILOT_ARMS if f"{a.arm}/{a.condition}" in wanted)
    return CampaignConfig(
        seeds=seeds,
        control_seeds=control,
        baselines=baselines,
        llm_arms=arms,
        max_turns=args.max_turns,
        control_truths=args.b10f_truths,
    )


def main() -> None:
    reconfigure_stdout()
    parser = build_parser()
    parser.add_argument(
        "--rescore-llm",
        action="store_true",
        help="score the stored LLM sessions under the current versions; never "
        "launch a live session (P4-PROPOSAL §2.2)",
    )
    args = parser.parse_args()
    from sciagent.campaign.driver import Campaign, DriverSettings
    from sciagent.campaign.plan import CampaignError

    if args.wait:
        wait_for_split(args.split, args.wait)
    # After the wait: the code that runs is the code on disk from here on.
    try:
        fingerprint = code_fingerprint()
    except CampaignError as error:
        raise SystemExit(f"refused: {error}") from error
    print(f"code fingerprint: {fingerprint}", flush=True)
    records = select(load_split(args.split), args.truths, args.ids)
    env = pointproc_environment(load_split(args.split))
    config = campaign_config(args)
    settings = DriverSettings(
        workers=args.workers,
        llm_concurrency=args.llm_concurrency,
        llm_limit=args.llm_limit,
        run_baselines=not args.no_baselines,
        run_llm=not args.no_llm,
        max_heavy=args.max_heavy,
        rescore_llm=args.rescore_llm,
    )
    try:
        campaign = Campaign(
            args.root,
            env,
            config,
            truth_entries(records),
            settings,
            fingerprint=fingerprint,
            reuse_from=args.reuse_from,
        )
    except CampaignError as error:
        raise SystemExit(f"refused: {error}") from error
    try:
        summary = campaign.run()
    finally:
        campaign.close()
    print(summary)
    # Workers and LLM sessions import from disk during the run: the run is
    # attributable to ``fingerprint`` only if the checkout still is it.
    try:
        after = code_fingerprint()
    except CampaignError as error:
        after = f"unavailable ({error})"
    if after != fingerprint:
        raise SystemExit(
            f"the code changed during the run: started under {fingerprint}, "
            f"ended under {after}; cells written in this run may come from either"
        )


if __name__ == "__main__":
    main()
