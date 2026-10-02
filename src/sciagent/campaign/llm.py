"""One LLM investigation of a campaign, and what is read back from its record.

:func:`run_session` runs one (truth, seed, arm) through
:func:`sciagent.investigation.runner.run` (live Sonnet by default; tests pass a
scripted driver), then reads the saved, hash-chained record back for what the
campaign keeps: outcome, cost (turns, tokens, wall time), budgets used,
predictions (SPEC §4.4), tool-call counts, the submitted structure and every
charged fit's structure in fit order (native DSL, for the efficiency curve in
:func:`sciagent.campaign.execute.run_score_job`).

**What is recorded and what is retried.** A session that ran to an outcome
(submitted, max turns, wall time, ended without submit, refused, output
ceiling) is a result. So is a run the harness aborted for a reason that is
about the run: :class:`~sciagent.harness.errors.HermeticityError` (the session
offered a foreign tool) and :class:`~sciagent.harness.errors.HarnessFaultError`
(a tool handler raised; a framework bug, recorded so it is visible). A void run
(foreign tool use, sandbox infrastructure fault, out-of-bounds code) is a
result with ``void = 1``.

Retryable (:attr:`SessionOutcome.retryable`), never recorded as a result: the
SDK session failing (:class:`~sciagent.harness.errors.SessionFailedError`), a
``session_error`` outcome (an API error inside the session: rate limit or
overload), a served-model mismatch
(:class:`~sciagent.harness.errors.ServedModelError`, e.g. a fallback model
under load), and the sandbox being unavailable at start. These are about
quota and infrastructure, not about the agent; the driver logs each attempt,
backs off, and gives up on a unit after a fixed number of attempts.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Final

from sciagent.campaign.plan import (
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    Unit,
)
from sciagent.glm.canonical import canonicalise
from sciagent.glm.syntax import render
from sciagent.harness.errors import (
    HarnessFaultError,
    HermeticityError,
    ServedModelError,
    SessionFailedError,
)
from sciagent.harness.live import SessionDriver
from sciagent.harness.record import SessionRecord
from sciagent.investigation.runner import run
from sciagent.investigation.view import make_view
from sciagent.sandbox import SandboxError

type Json = Any

#: Outcomes of a completed session; the reading stores the index.
OUTCOMES: Final = (
    "submitted",
    "max_turns",
    "wall_time",
    "ended_without_submit",
    "refused",
    "output_ceiling",
    "session_error",
    "aborted",
    "hard_timeout",
)
#: Seconds past the session's wall time before an isolated session's process
#: is killed (:func:`run_session_isolated`).
HARD_TIMEOUT_GRACE_S: Final = 600.0
#: Outcomes that are retried rather than recorded (module docstring).
RETRY_OUTCOMES: Final = ("session_error",)

_USAGE_FIELDS: Final = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


@dataclass(frozen=True)
class SessionOutcome:
    """A finished session attempt: a result to record, or a retryable failure."""

    unit: Unit
    retryable: bool
    reading: dict[str, float]
    detail: dict[str, Json]
    submission: str | None
    fitted: tuple[str, ...]
    wall_s: float


def _usage(usage: Mapping[str, Any] | None) -> dict[str, float]:
    usage = usage or {}
    return {f: float(usage.get(f) or 0) for f in _USAGE_FIELDS}


def read_record(
    record: SessionRecord,
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
) -> tuple[tuple[str, ...], dict[str, int]]:
    """Charged fits' structures (native DSL, fit order) and tool-call counts."""
    spec = env.investigation
    if spec is None:
        raise CampaignError("the environment has no investigation spec")
    arm = config.arm_of(unit.system)
    view = make_view(arm.condition, truth.spec.channels, spec.anon_channels)
    fits: list[tuple[int, str]] = []
    counts: dict[str, int] = {}
    for entry in record.of_kind("tool_call"):
        body = entry.body
        name = str(body.get("name"))
        counts[name] = counts.get(name, 0) + 1
        rec = body.get("record") or {}
        if name == "fit" and not body.get("is_error") and "fit" in rec:
            text = str(body["args"]["structure"])
            native = canonicalise(view.parse(text))
            fits.append((int(rec["fit"]), render(native)))
    fits.sort()
    return tuple(t for _, t in fits), dict(sorted(counts.items()))


def run_session(
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    *,
    driver: SessionDriver | None = None,
    sandbox_root: Path | None = None,
) -> SessionOutcome:
    """Run one LLM unit and read its record (module docstring)."""
    spec = env.investigation
    if spec is None:
        raise CampaignError("the environment has no investigation spec")
    arm = config.arm_of(unit.system)
    start = time.perf_counter()
    try:
        outcome = run(
            spec,
            truth.id,
            arm.arm,
            arm.condition,
            config.llm_model,
            unit.seed,
            out_dir,
            experiments=config.experiments,
            fits=config.fits,
            max_turns=config.max_turns,
            wall_time_s=config.wall_time_s,
            driver=driver,
            sandbox_root=sandbox_root,
        )
    except (SessionFailedError, ServedModelError, SandboxError) as error:
        return _retry(unit, error, start)
    except (HermeticityError, HarnessFaultError) as error:
        detail: dict[str, Json] = {
            "outcome": "aborted",
            "error": type(error).__name__,
            "message": str(error)[:2000],
            "record": f"aborted-{error.record.head[:16]}.json",
            "head": error.record.head,
        }
        reading = {
            "status": 0.0,
            "void": 1.0,
            "outcome": float(OUTCOMES.index("aborted")),
        }
        return SessionOutcome(
            unit, False, reading, detail, None, (), time.perf_counter() - start
        )
    record = SessionRecord.load(outcome.record_path)
    fitted, counts = read_record(record, unit, truth, env, config)
    results = record.of_kind("result")
    result_body = results[-1].body if results else {}
    detail = {
        "outcome": outcome.outcome,
        "record": outcome.record_path.name,
        "head": outcome.head,
        "submission": None
        if outcome.submission is None
        else render(canonicalise(outcome.submission)),
        "report": outcome.report,
        "void_reasons": list(outcome.void_reasons),
        "predictions": [p.as_json() for p in outcome.predictions],
        "usage": dict(outcome.usage or {}),
        "tool_calls": counts,
        "fitted": list(fitted),
        "api_error_status": result_body.get("api_error_status"),
        "errors": result_body.get("errors"),
        "served_models": result_body.get("served_models"),
        "total_cost_usd": record.timing.get("total_cost_usd"),
        "duration_api_ms": record.timing.get("duration_api_ms"),
    }
    if outcome.outcome in RETRY_OUTCOMES:
        return SessionOutcome(
            unit, True, {}, detail, None, (), time.perf_counter() - start
        )
    evaluated = [p for p in outcome.predictions if p.value is not None]
    reading = {
        "status": 1.0,
        "outcome": float(OUTCOMES.index(outcome.outcome))
        if outcome.outcome in OUTCOMES
        else -1.0,
        "submitted": 0.0 if outcome.submission is None else 1.0,
        "void": 1.0 if outcome.void else 0.0,
        "turns": float(outcome.num_turns or 0),
        "wall_s": float(outcome.wall_s or 0.0),
        "fits_used": float(outcome.fits_used),
        "experiments_used": float(outcome.experiments_used),
        "tool_calls": float(sum(counts.values())),
        "predictions": float(len(outcome.predictions)),
        "predictions_evaluated": float(len(evaluated)),
        "predictions_covered": float(sum(p.covered for p in evaluated)),
        **_usage(outcome.usage),
    }
    return SessionOutcome(
        unit,
        False,
        reading,
        detail,
        detail["submission"],
        fitted,
        time.perf_counter() - start,
    )


def _retry(unit: Unit, error: BaseException, start: float) -> SessionOutcome:
    record = getattr(error, "record", None)
    detail: dict[str, Json] = {
        "outcome": "retry",
        "error": type(error).__name__,
        "message": str(error)[:2000],
        "head": None if record is None else record.head,
    }
    return SessionOutcome(unit, True, {}, detail, None, (), time.perf_counter() - start)


# --------------------------------------------------------------------------
# Isolation: one process per session, killed past a hard deadline
# --------------------------------------------------------------------------


def _child(
    conn: Connection,
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    sandbox_root: Path | None,
) -> None:
    """Entry point of an isolated session's process: run it, send the outcome."""
    conn.send(run_session(unit, truth, env, config, out_dir, sandbox_root=sandbox_root))
    conn.close()


def run_session_isolated(
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    *,
    sandbox_root: Path | None = None,
    grace_s: float = HARD_TIMEOUT_GRACE_S,
) -> SessionOutcome:
    """:func:`run_session` in its own ``spawn`` process, killed if it overruns.

    The harness enforces the session's wall time by cancelling the model
    stream, but a tool call already running in a worker thread (an experiment
    whose simulation takes hours) cannot be cancelled, and ``asyncio.run``
    waits for it before returning. Measured in the pilot: an AG-o session
    stuck past its 3,600 s wall time inside ``run_experiment``. Here the
    session gets ``wall_time_s + grace_s``; past that its process is killed and
    the unit is recorded as ``hard_timeout`` (void, nothing scored, no record
    saved), so one runaway experiment cannot block the campaign. A process
    that dies without an answer is retryable.
    """
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child,
        args=(send, unit, truth, env, config, out_dir, sandbox_root),
        daemon=True,
    )
    start = time.perf_counter()
    process.start()
    send.close()
    deadline = config.wall_time_s + grace_s
    try:
        if receive.poll(deadline):
            outcome: SessionOutcome = receive.recv()
            process.join()
            return outcome
    except EOFError as error:
        process.join()
        return _retry(unit, error, start)
    finally:
        receive.close()
    process.kill()
    process.join()
    detail: dict[str, Json] = {
        "outcome": "hard_timeout",
        "error": "HardTimeout",
        "message": f"the session did not end within {deadline:.0f} s (wall time "
        f"{config.wall_time_s:.0f} s plus {grace_s:.0f} s): a tool call kept "
        f"running past the wall time; the process was killed",
    }
    reading = {
        "status": 0.0,
        "void": 1.0,
        "outcome": float(OUTCOMES.index("hard_timeout")),
        "wall_s": time.perf_counter() - start,
    }
    return SessionOutcome(
        unit, False, reading, detail, None, (), time.perf_counter() - start
    )
