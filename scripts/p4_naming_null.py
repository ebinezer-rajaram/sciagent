"""Control 4's runner: the naming null at seed 1 (prereg 2026-11-05 §2.7).

    uv run python scripts/p4_naming_null.py --split <the test split>

Runs ``planted_hint_script`` through the campaign's own session path
(``campaign.llm.run_session``, so the real tool layer) in the named, rescaled and
anon conditions, with a :class:`ScriptedDriver` and no LLM, on two truths fixed
by :func:`rule_truths`: the first by sorted id in each half of the split, in and
out of dictionary. Each session is then scored by ``run_score_job``, the score
cell a real session gets, and ORACLE's unit by ``run_baseline_job``; the gap is
``analysis.derive``'s, the cell's ``per_event`` less ORACLE's on the same (truth,
seed). ``naming_null`` judges each truth, and both verdicts go into one record
with the commit they ran at (``naming_null_record``). The miss script reads that
record; nothing here decides what it means.

The record is written once. An existing one refuses the run before anything
starts, and a dirty tree refuses too, since the commit must describe the code
that ran (:func:`pilot.code_fingerprint`). A crash leaves no record, so the
answer stays False and the run can be made again. Sessions are kept in
``<record stem>-sessions/`` beside the record, for replay.

Where a record may go is fixed by its split (:func:`check_destination`): a test
split writes the constant and nothing else, and a dev run writes anywhere but the
constant, since a dev record there would stop the real run for good. So ``--split``
has no default, and on dev ``--record`` must be given.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from naming_null_record import (
    IN_HALF,
    NAMING_NULL_RECORD,
    NAMING_SEED,
    OUT_HALF,
    SCHEMA,
    write_record,
)

# pilot pins BLAS to one thread on import; everything numpy-backed is imported
# inside the functions below, after it.
from pilot import (
    ANON_CHANNELS,
    code_fingerprint,
    pointproc_environment,
    reconfigure_stdout,
    truth_entries,
)

if TYPE_CHECKING:
    from sciagent.campaign.execute import UnitResult
    from sciagent.campaign.llm import SessionOutcome
    from sciagent.campaign.plan import (
        CampaignConfig,
        CampaignEnvironment,
        LLMArm,
        TruthEntry,
    )
    from sciagent.harness.record import SessionRecord
    from sciagent.scenarios.records import TruthRecord
    from sciagent.scoring.controls_p4 import NamingNullVerdict

#: ORACLE's system name, the one baseline a gap needs.
ORACLE: Final = "ORACLE"
#: The experiments the planted script runs.
PLANTED_EXPERIMENTS: Final = 1


def rule_truths(
    records: Sequence[TruthRecord],
) -> tuple[TruthRecord, TruthRecord]:
    """The first by sorted id in each half: (in dictionary, out of dictionary)."""
    from sciagent.scoring.errors import ScoringError

    ordered = sorted(records, key=lambda r: r.id)
    first_in = next((r for r in ordered if not r.out_of_dictionary), None)
    first_out = next((r for r in ordered if r.out_of_dictionary), None)
    if first_in is None or first_out is None:
        raise ScoringError(
            "control 4 needs a truth in each half of the split, in and out of "
            f"dictionary; found in={first_in is not None}, out={first_out is not None}"
        )
    return first_in, first_out


def check_destination(records: Sequence[TruthRecord], record: Path) -> str:
    """The records' one split, if ``record`` is where it may go (module docstring)."""
    from sciagent.campaign.freeze import DEV_SPLIT
    from sciagent.scoring.errors import ScoringError

    labels = sorted({r.split for r in records})
    if len(labels) != 1:
        raise ScoringError(f"control 4 needs one split, got {labels}")
    split = labels[0]
    constant = record.resolve() == NAMING_NULL_RECORD.resolve()
    if split == DEV_SPLIT and constant:
        raise ScoringError(
            f"the record at {NAMING_NULL_RECORD} belongs to the test split; give a "
            f"{split} run its own --record"
        )
    if split != DEV_SPLIT and not constant:
        raise ScoringError(
            f"a {split} run's record goes to {NAMING_NULL_RECORD} and nowhere else, "
            f"not {record}"
        )
    return split


def cell_gap(cell: UnitResult, oracle: UnitResult) -> float:
    """``per_event - oracle``, as ``analysis.derive`` gives it; ``nan`` where a
    unit failed or holds no reading, which ``naming_null`` cannot pass."""
    if not (cell.ok and oracle.ok):
        return math.nan
    return cell.reading.get("per_event", math.nan) - oracle.reading.get(
        "per_event", math.nan
    )


def planted_arms() -> tuple[LLMArm, ...]:
    """AG-c in each naming condition, on P4's frozen prompt variant."""
    from sciagent.campaign.plan import P4_PROMPT_VARIANT, LLMArm
    from sciagent.scoring.controls_p4 import NAMING_CONDITIONS

    return tuple(LLMArm("AG-c", c, P4_PROMPT_VARIANT) for c in NAMING_CONDITIONS)


def planted_config() -> CampaignConfig:
    """The sessions' budgets, and otherwise a default campaign: its fit
    configuration is the one every agent fit and score cell uses."""
    from sciagent.campaign.plan import CampaignConfig
    from sciagent.scoring.controls_p4 import PLANTED_FITS

    return CampaignConfig(
        seeds=(NAMING_SEED,),
        control_seeds=(NAMING_SEED,),
        baselines=(ORACLE,),
        llm_arms=planted_arms(),
        fits=PLANTED_FITS,
        experiments=PLANTED_EXPERIMENTS,
    )


def check_session(done: SessionOutcome) -> None:
    """Refuse a session control 4 cannot read: retryable, void (or with no void
    reading), or ended other than by a submit. A refusal stops the run before
    any record is written, so the answer stays False (red team 2026-11-07,
    finding 3)."""
    from sciagent.scoring.errors import ScoringError

    if done.retryable:
        raise ScoringError(f"{done.unit}: no session to read: {done.detail}")
    if done.reading.get("void") != 0.0:
        raise ScoringError(f"{done.unit}: the session is void: {done.detail}")
    if done.detail.get("outcome") != "submitted":
        raise ScoringError(
            f"{done.unit}: the session ended {done.detail.get('outcome')!r}, "
            "not by the script's submit"
        )


def run_condition(
    arm: LLMArm,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
) -> tuple[SessionRecord, UnitResult]:
    """One planted session, its record, and its score cell."""
    from sciagent.campaign.execute import ScoreJob, run_score_job
    from sciagent.campaign.llm import run_session
    from sciagent.campaign.plan import Unit
    from sciagent.harness.record import SessionRecord
    from sciagent.harness.scripted import ScriptedDriver
    from sciagent.investigation.view import make_view
    from sciagent.scoring.controls_p4 import planted_hint_script

    view = make_view(arm.condition, truth.spec.channels, ANON_CHANNELS)
    script = planted_hint_script(truth.structure, view)
    system = arm.system(config.llm_letter)
    unit = Unit(truth.id, NAMING_SEED, system)
    done = run_session(unit, truth, env, config, out_dir, driver=ScriptedDriver(script))
    check_session(done)
    record = SessionRecord.load(out_dir / str(done.detail["record"]))
    job = ScoreJob(
        truth, NAMING_SEED, system, done.submission, done.fitted, env, config
    )
    return record, run_score_job(job)


def run_truth(
    truth: TruthEntry, env: CampaignEnvironment, sessions: Path
) -> NamingNullVerdict:
    """Control 4 on one truth, at :data:`NAMING_SEED` (module docstring)."""
    from sciagent.campaign.execute import BaselineJob, run_baseline_job
    from sciagent.scoring.controls_p4 import naming_null

    config = planted_config()
    (oracle,) = run_baseline_job(
        BaselineJob(truth, NAMING_SEED, (ORACLE,), env, config)
    )
    records: dict[str, SessionRecord] = {}
    gaps: dict[str, float] = {}
    for arm in config.llm_arms:
        out_dir = sessions / truth.id / arm.condition
        record, cell = run_condition(arm, truth, env, config, out_dir)
        records[arm.condition] = record
        gaps[arm.condition] = cell_gap(cell, oracle)
    return naming_null(records, gaps)


def verdict_json(truth: str, half: str, verdict: NamingNullVerdict) -> dict[str, Any]:
    """One truth's verdict for the record; every float as ``float.hex``, so it is
    exact and a ``nan`` can be written."""
    return {
        "truth": truth,
        "half": half,
        "passed": verdict.passed,
        "conditions": list(verdict.conditions),
        "gaps": {c: gap.hex() for c, gap in verdict.gaps},
        "facts": {
            c: {
                "submission": facts.submission,
                "submission_hash": facts.submission_hash,
                "fits": [
                    {
                        "structure_hash": f.structure_hash,
                        "data_ids": list(f.data_ids),
                        "log_likelihood": f.log_likelihood.hex(),
                    }
                    for f in facts.fits
                ],
            }
            for c, facts in verdict.facts
        },
        "mismatches": list(verdict.mismatches),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--record", type=Path, default=NAMING_NULL_RECORD)
    return parser


def main(argv: list[str] | None = None) -> int:
    reconfigure_stdout()
    args = build_parser().parse_args(argv)
    record: Path = args.record
    if record.exists(follow_symlinks=False):
        print(f"refused: {record} exists; control 4 runs once", file=sys.stderr)
        return 2

    from sciagent.campaign import cpu
    from sciagent.campaign.plan import CampaignError
    from sciagent.scenarios.records import read_split
    from sciagent.scoring.errors import ScoringError

    records, manifest = read_split(args.split)
    try:
        split = check_destination(records, record)
        first_in, first_out = rule_truths(records)
        commit = code_fingerprint().removeprefix("commit=").split(",")[0]
    except (CampaignError, ScoringError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2

    env = pointproc_environment(records)
    sessions = record.parent / f"{record.stem}-sessions"
    verdicts: list[dict[str, Any]] = []
    for half, truth in zip(
        (IN_HALF, OUT_HALF), truth_entries((first_in, first_out)), strict=True
    ):
        mark = cpu.cpu_mark()
        verdict = run_truth(truth, env, sessions)
        spent = cpu.cpu_since(mark)
        verdicts.append(verdict_json(truth.id, half, verdict))
        print(
            f"{truth.id} ({half}): {'pass' if verdict.passed else 'FAIL'}, "
            f"gaps {dict(verdict.gaps)}, CPU {spent['cpu_s']} s, "
            f"mismatches {list(verdict.mismatches)}"
        )
    write_record(
        record,
        {
            "schema": SCHEMA,
            "commit": commit,
            "split": split,
            "split_sha256": manifest["records_sha256"],
            "seed": NAMING_SEED,
            "verdicts": verdicts,
        },
    )
    print(f"wrote {record} at {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
