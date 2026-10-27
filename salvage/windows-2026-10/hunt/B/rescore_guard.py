"""Defect hunt B, property 1 (+5): --rescore-llm launches nothing.

    uv run python .cache/hunt/B/rescore_guard.py --root <store> --truths 20 \
        --run-ids dev-004 --run-seeds 3 --run-arms AG-c/named

Works on a COPY of --root. Every path to a live session is replaced by a
recorder that raises, the CLI is removed from PATH and ANTHROPIC_API_KEY is
set (the provider refuses it). Part A plans the full rescore and inspects the
queues; part B runs a real (small) rescore end to end.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot import (
    build_parser,
    load_split,
    pointproc_environment,
    reconfigure_stdout,
    select,
    truth_entries,
)

CALLS: list[str] = []


class LaunchAttempt(BaseException):
    """Raised by every patched launch path (BaseException: nothing catches it)."""


def recorder(name: str):
    def f(*a: object, **k: object) -> None:
        CALLS.append(name)
        raise LaunchAttempt(name)

    return f


def main() -> None:
    reconfigure_stdout()
    p = build_parser()
    p.add_argument("--run-ids", default="dev-004")
    p.add_argument("--run-seeds", default="3")
    p.add_argument("--run-arms", default="AG-c/named")
    p.add_argument("--skip-run", action="store_true")
    args = p.parse_args()

    # provider unavailable
    os.environ["ANTHROPIC_API_KEY"] = "sk-hunt-invalid"
    os.environ["PATH"] = os.pathsep.join(
        d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not any((Path(d) / n).exists() for n in ("claude", "claude.exe", "claude.cmd", "claude.EXE"))
    )
    print("claude on PATH:", shutil.which("claude"))

    import claude_agent_sdk

    import sciagent.campaign.driver as driver
    import sciagent.campaign.llm as llm
    import sciagent.investigation.runner as runner
    import sciagent.harness.live as live

    for mod, names in (
        (driver, ("run_session", "run_session_isolated")),
        (llm, ("run_session", "run_session_isolated", "run")),
        (runner, ("run", "run_investigation")),
        (live, ("run_investigation",)),
        (claude_agent_sdk, ("query", "ClaudeSDKClient")),
    ):
        for n in names:
            if hasattr(mod, n):
                setattr(mod, n, recorder(f"{mod.__name__}.{n}"))

    from sciagent.campaign.driver import Campaign, DriverSettings
    from sciagent.campaign.plan import PILOT_ARMS, CampaignConfig, CampaignError, score_key
    from sciagent.campaign.execute import ScoreJob

    tmp = Path(tempfile.mkdtemp(prefix="rescore-", dir=HERE))
    root = tmp / "store"
    shutil.copytree(args.root.resolve(), root)
    env = pointproc_environment(load_split(args.split))

    # ---- part A: plan the whole rescore -------------------------------
    records = select(load_split(args.split), args.truths, args.ids)
    seeds = tuple(int(s) for s in args.seeds.split(","))
    config = CampaignConfig(seeds=seeds, control_seeds=(seeds[0],), max_turns=args.max_turns)
    settings = DriverSettings(workers=0, rescore_llm=True, run_baselines=False)  # run_llm stays True
    c = Campaign(root, env, config, truth_entries(records), settings)
    c._plan_rescore()
    jobs = [pj for pj in c.pool_queue if isinstance(pj.job, ScoreJob)]
    print(f"[A] llm_queue={len(c.llm_queue)} score jobs queued={len(jobs)} skipped_done={c.skipped_done}")
    print(f"[A] _llm_may_launch() -> {c._llm_may_launch()}")
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as tp:
        try:
            c._launch_llm(tp)
            print("[A] FAIL: _launch_llm did not refuse")
        except CampaignError as e:
            print(f"[A] _launch_llm refused: {e}")
    reclass = {("dev-000", 2, "AG-o-S/anon"), ("dev-000", 2, "AG-o-S/named"), ("dev-005", 3, "AG-o-S/anon"),
               ("dev-005", 3, "AG-o-S/named"), ("dev-011", 2, "AG-o-S/anon"), ("dev-014", 3, "AG-o-S/named"),
               ("dev-015", 3, "AG-o-S/named"), ("dev-016", 2, "AG-o-S/anon"), ("dev-017", 3, "AG-o-S/anon")}
    queued = {(pj.units[0].truth_id, pj.units[0].seed, pj.units[0].system) for pj in jobs}
    print(f"[A] the 9 earlier voids queued for scoring: {len(reclass & queued)}/9")
    c.close()

    if args.skip_run:
        print("calls to launch paths:", CALLS)
        return
    # ---- part B: a real small rescore run -----------------------------
    wanted = args.run_arms.split(",")
    arms = tuple(a for a in PILOT_ARMS if f"{a.arm}/{a.condition}" in wanted)
    rseeds = tuple(int(s) for s in args.run_seeds.split(","))
    rconfig = CampaignConfig(seeds=rseeds, control_seeds=(rseeds[0],), llm_arms=arms, max_turns=args.max_turns)
    rrecords = select(load_split(args.split), None, args.run_ids)
    c = Campaign(root, env, rconfig, truth_entries(rrecords), settings)
    before = set(p.name for p in (root / "details").glob("*.json"))
    llm_before = sorted(str(p.relative_to(root)) for p in (root / "llm").rglob("*"))
    summary = c.run()
    c.close()
    after = set(p.name for p in (root / "details").glob("*.json"))
    llm_after = sorted(str(p.relative_to(root)) for p in (root / "llm").rglob("*"))
    print(f"[B] summary {summary}")
    print(f"[B] new cells: {len(after - before)}; llm dir unchanged: {llm_before == llm_after}; "
          f"attempts.jsonl exists: {(root / 'attempts.jsonl').exists()}")
    for name in sorted(after - before):
        cell = json.loads((root / "details" / name).read_text(encoding="utf-8"))
        k = cell["key"]
        print(f"    {name[:16]} kind={k['config']['kind']} env={k['env_version']} metric={k['metric_version'][:28]} "
              f"session={k['config'].get('session', '')[:16]} reading={ {kk: float.fromhex(v) for kk, v in cell['reading'].items()} }")
    print("calls to launch paths:", CALLS)
    print(f"(temp store {root})")


if __name__ == "__main__":
    main()
