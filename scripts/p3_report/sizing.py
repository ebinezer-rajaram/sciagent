"""P4 sizing inputs: per-arm session wall by outcome, turns against model calls.

    uv run python scripts/p3_report/sizing.py --root .cache/pilot-merged \
        --run-log .cache/pilot-llm-run.log

Reads ``timing.jsonl``, the session cells and their records, and the LLM half's
run log (the only place with each session's finish time). The matched check
compares the sessions that finished while the baseline half ran with the same
arm and truth at other seeds. Distinct assistant message ids are taken as one
model response each. Read-only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics as st
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NamedTuple

from sciagent.campaign.store import ResultStore
from sciagent.harness.record import SessionRecord

#: When the baseline half stopped (``.cache/pilot-run.log``): UTC, as the log's
#: leading timestamp, so strings compare in time order.
BASELINE_STOPPED: Final = "2026-10-29 21:22:54"
LOG_LINE: Final = re.compile(
    r"(\S+ \S+),\d+ llm-session (\S+) (dev-\d+) s(\d) (\S+) .* (\d+)s$"
)
SEEDS: Final = (1, 2, 3)


class Finished(NamedTuple):
    """One session line of the run log."""

    at: str
    wall_s: int
    outcome: str


@dataclass(frozen=True)
class Session:
    name: str
    outcome: str
    wall: float
    record_wall: float
    messages: int
    tool_uses: int
    has_result: bool
    turns: float
    output_tokens: float


def run_log_sessions(lines: Iterable[str]) -> dict[tuple[str, str, int], Finished]:
    """The session lines of a run log, by (arm, truth, seed)."""
    found: dict[tuple[str, str, int], Finished] = {}
    for line in lines:
        m = LOG_LINE.match(line)
        if m:
            found[(m[2], m[3], int(m[4]))] = Finished(m[1], int(m[6]), m[5])
    return found


def session(cell: Mapping[str, Any], root: Path, wall: float) -> Session:
    detail = cell["detail"]["session"]
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    record = SessionRecord.load(root / detail["record_dir"] / detail["record"])
    assistant = record.of_kind("assistant")
    config = cell["key"]["config"]
    return Session(
        name=f"{config['truth']} s{cell['key']['seed']}",
        outcome=detail["outcome"],
        wall=wall,
        record_wall=record.timing["wall_s"],
        messages=len({e.body.get("message_id") for e in assistant}),
        tool_uses=sum(
            block.get("type") == "tool_use"
            for e in assistant
            for block in e.body.get("content") or []
        ),
        has_result=bool(record.of_kind("result")),
        turns=reading["turns"],
        output_tokens=reading["output_tokens"],
    )


def turns_against_messages(arms: Mapping[str, list[Session]]) -> None:
    """``turns`` read against tool_uses and against distinct message ids."""
    counted = [x for v in arms.values() for x in v if x.has_result]
    same = sum(x.turns == x.tool_uses + 1 for x in counted)
    print(f"turns == tool_uses + 1: {same} of {len(counted)} sessions with a count")
    submitted = [x for v in arms.values() for x in v if x.outcome == "submitted"]
    per_session = st.median(x.turns / x.messages for x in submitted)
    pooled = math.fsum(x.turns for x in submitted) / sum(x.messages for x in submitted)
    print(
        f"turns / distinct message ids over {len(submitted)} submitted sessions: "
        f"median per session {per_session:.2f}, pooled {pooled:.2f}"
    )


def matched_check(when: Mapping[tuple[str, str, int], Finished]) -> None:
    """Sessions that finished before the baseline half stopped, against later
    seeds of the same arm and truth, then the early and later medians by arm."""
    early = sorted(k for k, v in when.items() if v.at < BASELINE_STOPPED)
    pairs: list[tuple[int, float]] = []
    for arm, truth, seed in early:
        mine = when[(arm, truth, seed)]
        if mine.outcome == "wall_time":
            continue
        later = [
            when[(arm, truth, n)].wall_s
            for n in SEEDS
            if n != seed and when[(arm, truth, n)].at >= BASELINE_STOPPED
        ]
        if later:
            pairs.append((mine.wall_s, st.median(later)))
    print(
        f"early sessions {len(early)}; submitted with later seeds of same arm and "
        f"truth: {len(pairs)}; median early wall "
        f"{st.median(p[0] for p in pairs):.0f} s; median same-cell later wall "
        f"{st.median(p[1] for p in pairs):.0f} s; median ratio "
        f"{st.median(p[0] / p[1] for p in pairs):.2f}; early slower in "
        f"{sum(p[0] > p[1] for p in pairs)}"
    )
    for arm in sorted({k[0] for k in when}):
        of_arm = [v for k, v in when.items() if k[0] == arm]
        before = [v.wall_s for v in of_arm if v.at < BASELINE_STOPPED]
        after = [v.wall_s for v in of_arm if v.at >= BASELINE_STOPPED]
        print(
            arm,
            "early n",
            len(before),
            "median",
            st.median(before) if before else None,
            "later n",
            len(after),
            "median",
            st.median(after),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-log", type=Path, required=True)
    args = parser.parse_args(argv)
    lines = (args.root / "timing.jsonl").read_text(encoding="utf-8").splitlines()
    timing = [json.loads(line) for line in lines]
    wall = {r["digest"]: r["wall_s"] for r in timing if r["kind"] == "llm-session"}
    with ResultStore(args.root, read_only=True) as store:
        cells = sorted(
            (
                c
                for c in store.cells()
                if c["key"]["config"].get("kind") == "llm-session"
            ),
            key=lambda c: str(c["digest"]),
        )
    arms: dict[str, list[Session]] = {}
    for c in cells:
        arms.setdefault(c["key"]["config"]["system"], []).append(
            session(c, args.root, wall[c["digest"]])
        )
    for arm, sessions in sorted(arms.items()):
        sub = [x for x in sessions if x.outcome == "submitted"]
        w = [x.wall for x in sub]
        print(
            f"{arm}: submitted n={len(sub)} wall median {st.median(w):.0f} mean "
            f"{st.fmean(w):.0f} p90 {sorted(w)[int(0.9 * len(w))]:.0f} max "
            f"{max(w):.0f} sum_h {math.fsum(w) / 3600:.2f}; distinct message ids "
            f"median {st.median(x.messages for x in sub):.0f}; turns median "
            f"{st.median(x.turns for x in sub):.0f} mean "
            f"{st.fmean(x.turns for x in sub):.1f}; out tokens mean "
            f"{st.fmean(x.output_tokens for x in sub):.0f}"
        )
        for x in sessions:
            if x.outcome != "submitted":
                print(
                    f"   {x.outcome}: {x.name} driver {x.wall:.0f} s record "
                    f"{x.record_wall:.0f} s distinct message ids {x.messages}"
                )
    everything = [x.wall for v in arms.values() for x in v]
    print(f"all sessions driver wall sum {math.fsum(everything) / 3600:.2f} h")
    for kind in sorted({r["kind"] for r in timing}):
        walls = [r["wall_s"] for r in timing if r["kind"] == kind]
        print(kind, len(walls), f"wall sum {math.fsum(walls) / 3600:.2f} h")
    turns_against_messages(arms)
    log = args.run_log.read_text(encoding="utf-8").splitlines()
    matched_check(run_log_sessions(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
