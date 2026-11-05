"""Audit every llm-session cell's lab-state readings against its record.

    uv run python scripts/p3_report/wall_time_audit.py --root .cache/pilot-merged

Fields read from lab state after the session ended (``runner.py``:
experiments_used, fits_used, predictions, submission, report, void) are
compared with what the hash-chained record shows. Prints every session with a
disagreement, every ``wall_time`` session in full, the tool_uses no layer call
answered (a tally by tool and arm, then each one), and the sessions whose
driver wall time runs past the record's own. Read-only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sciagent.campaign.store import ResultStore
from sciagent.harness.record import SessionRecord

#: A tool_use whose input the CLI could not parse carries this key instead.
UNPARSED: Final = "__unparsedToolInput"
RUN_EXPERIMENT: Final = "mcp__lab__run_experiment"
#: Reading fields shown for a session that ended without an SDK result.
SHOWN: Final = (
    "fits_used",
    "experiments_used",
    "tool_calls",
    "predictions",
    "submitted",
    "void",
    "turns",
    "output_tokens",
)


@dataclass(frozen=True)
class Audit:
    """One session's cell read against its record."""

    name: str
    arm: str
    digest: str
    outcome: str
    wall_record: float | None
    wall_driver: float | None
    has_result: bool
    assistant_entries: int
    calls: int
    flushed: int
    charged: dict[str, int]
    fitted: int
    void_reasons: list[str] | None
    submission: Any
    unanswered: list[tuple[str, Any]]
    disagreements: list[str]
    reading: dict[str, float]


def audit(cell: dict[str, Any], root: Path, wall_driver: float | None) -> Audit:
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    session = cell["detail"]["session"]
    config = cell["key"]["config"]
    record = SessionRecord.load(root / session["record_dir"] / session["record"])
    calls = [e.body for e in record.of_kind("tool_call")]
    outcome = record.of_kind("outcome")[-1].body
    uses = [
        block
        for e in record.of_kind("assistant")
        for block in e.body.get("content") or []
        if block.get("type") == "tool_use"
    ]
    answered = {b.get("tool_use_id") for b in calls}
    charged = Counter(b["charged"] for b in calls if b.get("charged"))
    predictions = sum(b["name"] == "predict" and not b.get("is_error") for b in calls)
    submits = [b for b in calls if b["name"] == "submit" and not b.get("is_error")]
    checks: dict[str, tuple[Any, Any]] = {
        "fits_used": (reading.get("fits_used"), float(charged["fits"])),
        "experiments_used": (
            reading.get("experiments_used"),
            float(charged["experiments"]),
        ),
        "tool_calls": (reading.get("tool_calls"), float(len(calls))),
        "predictions": (reading.get("predictions"), float(predictions)),
        "submitted": (reading.get("submitted"), float(bool(submits))),
        "detail_submission_vs_outcome": (
            session.get("submission") is None,
            outcome.get("submission") is None,
        ),
        "outcome_tool_calls": (float(outcome.get("tool_calls", -1)), float(len(calls))),
        "outcome_reason": (session.get("outcome"), outcome.get("reason")),
        "report_vs_submit": (
            session.get("report") is None,
            not submits or submits[-1]["args"].get("report") is None,
        ),
    }
    return Audit(
        name=f"{config['system']} {config['truth']} s{cell['key']['seed']}",
        arm=config["system"],
        digest=str(cell["digest"])[:16],
        outcome=session.get("outcome"),
        wall_record=record.timing.get("wall_s"),
        wall_driver=wall_driver,
        has_result=bool(record.of_kind("result")),
        assistant_entries=len(record.of_kind("assistant")),
        calls=len(calls),
        flushed=sum(b.get("tool_use_id") is None for b in calls),
        charged=dict(sorted(charged.items())),
        fitted=len(session.get("fitted") or []),
        void_reasons=session.get("void_reasons"),
        submission=session.get("submission"),
        unanswered=[
            (b["name"], b.get("input")) for b in uses if b["id"] not in answered
        ],
        disagreements=[
            f"{field}: cell {cell_value!r} record {record_value!r}"
            for field, (cell_value, record_value) in checks.items()
            if cell_value != record_value
        ],
        reading=reading,
    )


def tally(audits: list[Audit]) -> None:
    """The unanswered tool_uses, by tool and arm, and how many failed to parse."""
    by_tool = Counter((name, a.arm) for a in audits for name, _ in a.unanswered)
    for (name, arm), n in sorted(by_tool.items()):
        print(f"{name} {arm}: {n}")
    tools = Counter(name for a in audits for name, _ in a.unanswered)
    print("by tool:", {k: tools[k] for k in sorted(tools)})
    experiments = [
        i for a in audits for name, i in a.unanswered if name == RUN_EXPERIMENT
    ]
    unparsed = sum(UNPARSED in (i or {}) for i in experiments)
    print(
        f"{RUN_EXPERIMENT}: {unparsed} with {UNPARSED}, "
        f"{len(experiments) - unparsed} with parsed input"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    with ResultStore(args.root, read_only=True) as store:
        cells = sorted(
            (
                c
                for c in store.cells()
                if c["key"]["config"].get("kind") == "llm-session"
            ),
            key=lambda c: str(c["digest"]),
        )
    timing: dict[str, float] = {}
    lines = (args.root / "timing.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        rec = json.loads(line)
        if rec.get("kind") == "llm-session":
            timing[rec["digest"]] = rec["wall_s"]
    audits = [audit(c, args.root, timing.get(str(c["digest"]))) for c in cells]

    print(f"sessions: {len(audits)}")
    by_outcome = Counter(f"{a.arm}|{a.outcome}" for a in audits)
    print(
        "by arm and outcome:",
        json.dumps({k: by_outcome[k] for k in sorted(by_outcome)}),
    )
    print("\n== sessions with any cell/record disagreement")
    disagreeing = [a for a in audits if a.disagreements]
    print(f"{len(disagreeing)} of {len(audits)}")
    for a in disagreeing:
        print(a.name, a.digest, a.outcome, a.disagreements)
    print("\n== wall_time sessions, or no SDK result, or calls flushed at the end")
    for a in audits:
        if a.outcome == "wall_time" or not a.has_result or a.flushed:
            print(
                a.name,
                a.digest,
                a.outcome,
                f"wall record {a.wall_record} driver {a.wall_driver}",
                f"has_result {a.has_result}",
                f"assistant entries {a.assistant_entries}",
                f"calls {a.calls} flushed {a.flushed}",
                f"charged {a.charged} fitted {a.fitted}",
                f"void_reasons {a.void_reasons}",
                f"submission {a.submission}",
            )
            print("   unanswered:", [name for name, _ in a.unanswered])
            print("   reading:", {k: a.reading.get(k) for k in SHOWN})
    print("\n== unanswered tool_uses, tally over all sessions")
    tally(audits)
    print("\n== sessions with unanswered tool_uses, not wall_time")
    for a in audits:
        if a.unanswered and a.outcome != "wall_time":
            print(a.name, a.outcome, [(n, str(i)[:80]) for n, i in a.unanswered])
    print("\n== driver wall minus record wall, top 12")
    past = sorted(
        (
            (a.wall_driver - a.wall_record, a)
            for a in audits
            if a.wall_driver and a.wall_record
        ),
        key=lambda x: -x[0],
    )
    for delta, a in past[:12]:
        print(
            f"{delta:9.1f} {a.name} {a.outcome} record {a.wall_record:.1f} "
            f"driver {a.wall_driver:.1f}"
        )
    print("\n== sessions with record wall > 3000 s or driver wall > 3000 s")
    for a in audits:
        if (a.wall_record or 0) > 3000 or (a.wall_driver or 0) > 3000:
            print(a.name, a.outcome, a.wall_record, a.wall_driver)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
