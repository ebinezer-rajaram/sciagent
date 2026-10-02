"""Run one live AG-c or AG-o investigation on a pointproc truth, then replay it.

    uv run python scripts/investigate.py --truth size_excitation --arm AG-o \
        --condition named --model sonnet --experiments 3 --fits 8 --max-turns 40

    uv run python scripts/investigate.py --replay <record .json>

A live run costs one Claude session on the logged-in subscription (do not set
ANTHROPIC_API_KEY: the contamination guard refuses it). After the run the
record is reloaded from disk and replayed offline -- no model, every tool call
re-issued against a freshly built lab, sandbox code re-executed -- and the
script prints PASS or FAIL.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from environments.pointproc import v2
from sciagent.glm.syntax import render
from sciagent.harness.errors import ReplayDivergenceError
from sciagent.harness.live import MODELS
from sciagent.harness.record import SessionRecord
from sciagent.investigation import (
    EnvironmentSpec,
    TruthSpec,
    replay_investigation,
    run,
)

ROOT: Final = Path(__file__).resolve().parents[1]


def resolve(truth_id: str) -> TruthSpec:
    """pointproc's named truths, by id (the registry replay resolves from)."""
    truth = v2.TRUTHS[truth_id]
    return TruthSpec(
        truth.structure, truth.psi, truth.coef, v2.CHANNELS, v2.mark_sampler
    )


POINTPROC: Final = EnvironmentSpec(
    name="pointproc-v2",
    anon_channels={"size": "m1", "sign": "m2"},
    forbidden=("size", "sign", "arrival"),
    resolve=resolve,
)


def _replay(path: Path, fit_workers: int) -> bool:
    record = SessionRecord.load(path)
    try:
        report = replay_investigation(record, POINTPROC, fit_workers=fit_workers)
    except ReplayDivergenceError as error:
        print(f"replay         : FAIL at step {error.step} ({error.field}): {error}")
        return False
    print(f"replay         : PASS steps={report.steps} budgets={report.budgets_used}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", default="size_excitation", choices=sorted(v2.TRUTHS))
    parser.add_argument("--arm", default="AG-o", choices=["AG-c", "AG-o"])
    parser.add_argument("--condition", default="named", choices=["named", "anon"])
    parser.add_argument("--model", default="sonnet", choices=sorted(MODELS))
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--experiments", type=int, default=8)
    parser.add_argument("--fits", type=int, default=40)
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--wall-time", type=float, default=3600.0)
    parser.add_argument("--fit-workers", type=int, default=1)
    parser.add_argument("--out", type=Path, default=ROOT / ".cache" / "investigations")
    parser.add_argument("--replay", type=Path, default=None)
    args = parser.parse_args()

    if args.replay is not None:
        raise SystemExit(0 if _replay(args.replay, args.fit_workers) else 1)

    outcome = run(
        POINTPROC,
        args.truth,
        args.arm,
        args.condition,
        MODELS[args.model],
        args.seed,
        args.out,
        experiments=args.experiments,
        fits=args.fits,
        max_turns=args.max_turns,
        wall_time_s=args.wall_time,
        fit_workers=args.fit_workers,
    )
    usage = dict(outcome.usage or {})
    print(f"record         : {outcome.record_path}")
    print(f"head           : {outcome.head}")
    print(f"outcome        : {outcome.outcome}")
    print(f"turns          : {outcome.num_turns}")
    print(f"wall time (s)  : {outcome.wall_s}")
    print(f"tokens         : {json.dumps(usage, sort_keys=True)}")
    print(f"experiments    : {outcome.experiments_used} of {args.experiments}")
    print(f"fits           : {outcome.fits_used} of {args.fits}")
    print(
        "submission     : "
        + ("none" if outcome.submission is None else render(outcome.submission))
    )
    print(f"truth          : {render(resolve(args.truth).structure)}")
    print(f"report         : {outcome.report}")
    print(f"void           : {outcome.void} {list(outcome.void_reasons)}")
    for p in outcome.predictions:
        print(
            f"prediction {p.commitment}: e{p.experiment} {p.statistic} "
            f"{list(p.interval)} level {p.level} -> value {p.value} "
            f"covered={p.covered} hypothesis={p.hypothesis}"
        )
    ok = _replay(outcome.record_path, args.fit_workers)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
