"""One LLM investigation of a campaign, and what is read back from its record.

:func:`run_session` runs one (truth, seed, arm) through
:func:`sciagent.investigation.runner.run` (live Sonnet by default; tests pass a
scripted driver), then reads the saved, hash-chained record back for what the
campaign keeps: outcome, cost (turns, tokens), budgets used,
predictions (SPEC §4.4), tool-call counts, the submitted structure and every
charged fit's structure in fit order (native DSL, for the efficiency curve in
:func:`sciagent.campaign.execute.run_score_job`). Turns and tokens are the
SDK's own figures, from the record's ``result`` entry; a session with none (a
wall-time end) has no such reading fields. Old cells hold 0 there instead, so
whether a session has them is read from its record, not from a zero.

**What is recorded and what is retried.** A session that ran to an outcome
(submitted, max turns, wall time, ended without submit, refused, output
ceiling) is a result. So is a run the harness aborted for a reason that is
about the run: :class:`~sciagent.harness.errors.HermeticityError` (the session
offered a foreign tool) and :class:`~sciagent.harness.errors.HarnessFaultError`
(a tool handler raised; a framework bug, recorded so it is visible). A void run
under the void policy of :mod:`sciagent.harness.voids` (P4 Δ8: a foreign tool
that executed or has no recorded denial, server tool use, a sandbox
infrastructure fault, out-of-bounds code) is a result with ``void = 1``. A
denied call to an unknown tool name does not void it; the reading counts it
in ``unknown_tool_attempts``, and ``void_reasons`` in the detail are the
policy's.

Retryable (:attr:`SessionOutcome.retryable`), never recorded as a result: the
SDK session failing (:class:`~sciagent.harness.errors.SessionFailedError`), a
``session_error`` outcome (an API error inside the session: rate limit or
overload), a served-model mismatch
(:class:`~sciagent.harness.errors.ServedModelError`, e.g. a fallback model
under load), and the sandbox being unavailable at start. These are about
quota and infrastructure, not about the agent; the driver logs each attempt,
backs off, and gives up on a unit after a fixed number of attempts.

**Times are not in the cell.** The record's ``timing`` section is not hashed,
so two runs of the same record differ there. The session's wall time and the
API's duration and cost therefore travel beside the outcome
(:attr:`SessionOutcome.wall_s`, :attr:`SessionOutcome.timing`) into the cell's
``timing.jsonl`` line, never into its reading or detail (LOG 2026-10-27).
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import multiprocessing
import multiprocessing.process
import os
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.sharedctypes import SynchronizedArray
from pathlib import Path
from typing import Any, Final

from sciagent.campaign import cpu
from sciagent.campaign.plan import (
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    Unit,
    session_policy_settings,
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
from sciagent.harness.tools import FrameworkClock
from sciagent.investigation.runner import run
from sciagent.investigation.view import make_view
from sciagent.sandbox import SandboxError, image_tag, recover_run_dirs

type Json = Any

_log = logging.getLogger(__name__)

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
#: Seconds an isolated session's process has to exit once it has answered,
#: before it is killed (:func:`_run_isolated`).
EXIT_GRACE_S: Final = 10.0
#: Outcomes that are retried rather than recorded (module docstring).
RETRY_OUTCOMES: Final = ("session_error",)
#: What a session outcome carries beside its cell, for its ``timing.jsonl``
#: line (:attr:`SessionOutcome.timing`): the harness's own elapsed time and the
#: API's figures, then (P4b D5) each tool call's framework decision time, by
#: call index, and the agent's time (the harness's less all framework time),
#: all from the record's unhashed ``timing`` section; ``None`` where the
#: session has no such value (an abort, a hard timeout, a record from before
#: D5).
SESSION_TIMING_FIELDS: Final = (
    "harness_wall_s",
    "duration_api_ms",
    "total_cost_usd",
    "framework_s",
    "agent_s",
)

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
    #: The driver's clock over the attempt: the campaign's wall time of the
    #: unit. Written to the cell's ``timing.jsonl`` line, never to the cell.
    wall_s: float
    #: :data:`SESSION_TIMING_FIELDS`, each present (``None`` when absent), for
    #: the same line, then an isolated session's CPU time (:func:`_child`);
    #: empty for a retryable attempt, which is not recorded.
    timing: Mapping[str, Json]


def session_timing(record: SessionRecord | None) -> dict[str, Json]:
    """A session's :data:`SESSION_TIMING_FIELDS` from its record's unhashed
    ``timing`` section; each ``None`` where the record (or a record at all)
    lacks it, so an abort's missing harness time is stated, not taken as 0."""
    times: Mapping[str, Any] = {} if record is None else record.timing
    wall = times.get("wall_s")
    agent = times.get("agent_s")
    framework = times.get("framework_s")
    return {
        "harness_wall_s": None if wall is None else float(wall),
        "duration_api_ms": times.get("duration_api_ms"),
        "total_cost_usd": times.get("total_cost_usd"),
        "framework_s": None if framework is None else [float(f) for f in framework],
        "agent_s": None if agent is None else float(agent),
    }


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
    clock: FrameworkClock | None = None,
) -> SessionOutcome:
    """Run one LLM unit and read its record (module docstring). ``clock`` is
    the lab's framework clock (P4b D5), passed by an isolated session's
    process so its parent can read it."""
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
            prompt_variant=arm.prompt_variant,
            policy=arm.policy,
            policy_settings=None
            if arm.policy is None
            else session_policy_settings(config.policy),
            clock=clock,
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
            unit,
            False,
            reading,
            detail,
            None,
            (),
            time.perf_counter() - start,
            session_timing(error.record),
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
    }
    if outcome.outcome in RETRY_OUTCOMES:
        return SessionOutcome(
            unit, True, {}, detail, None, (), time.perf_counter() - start, {}
        )
    evaluated = [p for p in outcome.predictions if p.value is not None]
    # Turns and tokens exist only in the SDK's ``result`` message. A session
    # with none (a wall-time end) has no such reading, rather than a 0 that
    # would read as a session that cost nothing.
    reading = {
        "status": 1.0,
        "outcome": float(OUTCOMES.index(outcome.outcome))
        if outcome.outcome in OUTCOMES
        else -1.0,
        "submitted": 0.0 if outcome.submission is None else 1.0,
        "void": 1.0 if outcome.void else 0.0,
        "unknown_tool_attempts": float(outcome.unknown_tool_attempts),
        **({"turns": float(outcome.num_turns or 0)} if results else {}),
        "fits_used": float(outcome.fits_used),
        "experiments_used": float(outcome.experiments_used),
        "tool_calls": float(sum(counts.values())),
        "predictions": float(len(outcome.predictions)),
        "predictions_evaluated": float(len(evaluated)),
        "predictions_covered": float(sum(p.covered for p in evaluated)),
        **(_usage(outcome.usage) if results else {}),
    }
    return SessionOutcome(
        unit,
        False,
        reading,
        detail,
        detail["submission"],
        fitted,
        time.perf_counter() - start,
        session_timing(record),
    )


def _retry(unit: Unit, error: BaseException, start: float) -> SessionOutcome:
    record = getattr(error, "record", None)
    detail: dict[str, Json] = {
        "outcome": "retry",
        "error": type(error).__name__,
        "message": str(error)[:2000],
        "head": None if record is None else record.head,
    }
    return SessionOutcome(
        unit, True, {}, detail, None, (), time.perf_counter() - start, {}
    )


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
    clock: FrameworkClock | None = None,
) -> None:
    """Entry point of an isolated session's process: run it, send the outcome.

    A recorded outcome's timing also carries the session's CPU time,
    :data:`~sciagent.campaign.cpu.CPU_FIELDS`, measured here, in the
    session's own process: its threads (tool calls, simulations, fits) in
    ``cpu_s``, and in ``cpu_children_s`` the children it reaped (the
    ``claude`` CLI, ``docker`` clients; not the containers' processes, see
    :mod:`sciagent.campaign.cpu`). A retryable attempt keeps its empty timing.
    ``clock`` becomes the lab's framework clock, which the parent reads
    (:func:`_session_process`).
    """
    mark = cpu.cpu_mark()
    outcome = run_session(
        unit, truth, env, config, out_dir, sandbox_root=sandbox_root, clock=clock
    )
    if not outcome.retryable:
        timing = {**outcome.timing, **cpu.cpu_since(mark)}
        outcome = dataclasses.replace(outcome, timing=timing)
    conn.send(outcome)
    conn.close()


#: What an isolated session's process runs: :func:`_child`'s signature.
SessionTarget = Callable[
    [
        Connection,
        Unit,
        TruthEntry,
        CampaignEnvironment,
        CampaignConfig,
        Path,
        Path | None,
        FrameworkClock,
    ],
    None,
]

#: The child's framework clock as the parent reads it: ``[total, since]``,
#: seconds, ``since`` the running decision's start on ``time.monotonic`` (one
#: system-wide clock on the platforms the campaign runs on), or
#: :data:`_NOT_MEASURING`.
type SharedClock = SynchronizedArray[float]
_NOT_MEASURING: Final = -1.0


def _mirror(shared: SharedClock) -> Callable[[float, float | None], None]:
    """A :class:`FrameworkClock` publisher writing to ``shared``."""

    def publish(total: float, since: float | None) -> None:
        with shared.get_lock():
            shared[0] = total
            shared[1] = _NOT_MEASURING if since is None else since

    return publish


def _framework_s(shared: SharedClock, now: float) -> tuple[float, bool]:
    """The child's framework time at ``now`` (a running decision included),
    and whether a decision is running."""
    with shared.get_lock():
        total, since = shared[0], shared[1]
    if since == _NOT_MEASURING:
        return total, False
    return total + max(0.0, now - since), True


def _session_process(
    target: SessionTarget,
    conn: Connection,
    shared: SharedClock,
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    sandbox_root: Path | None,
) -> None:
    """Entry point of an isolated session's process.

    It first leads a process group of its own, so the parent can kill it
    together with every process it started (:func:`_kill_session`). The
    session's framework clock mirrors itself to ``shared``, which the parent
    reads to place its hard deadline (:func:`_run_isolated`).
    """
    if sys.platform != "win32":
        os.setpgid(0, 0)
    clock = FrameworkClock(publish=_mirror(shared))
    target(conn, unit, truth, env, config, out_dir, sandbox_root, clock)


def _kill_session(process: multiprocessing.process.BaseProcess) -> None:
    """Kill an isolated session's process and every process it started.

    Killing only the process would orphan the Docker clients of its
    ``python`` calls and handoffs; their containers are removed by the
    recovery that follows, but the clients themselves would run on. On
    POSIX the whole process group goes (:func:`_session_process`).
    """
    if sys.platform != "win32" and process.pid is not None:
        # No such group yet means the process has not reached setpgid, so it
        # has started nothing, and the kill below is enough.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.kill()
    process.join()


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
    session gets ``wall_time_s + grace_s`` of its own time; past that its
    process is killed and the unit is recorded as ``hard_timeout`` (void,
    nothing scored, no record saved), so one runaway experiment cannot block
    the campaign. Its own time excludes framework decision time (P4b D5), as
    the harness's wall limit does: the child's framework clock is mirrored to
    a shared array (:func:`_session_process`), and the parent's deadline
    pauses while a decision runs (:func:`_answered`). A shared array rather
    than the pipe: the pipe carries one message, the outcome, and the parent
    can read the array at any moment without the child's help. A decision
    that never ends is therefore never killed here; bounding it is left to the
    policy arms' driver and its CPU meter (P4b D6). A process
    that dies without an answer is retryable. A process that has answered gets
    :data:`EXIT_GRACE_S` to exit and is then killed: its outcome and record are
    in hand, and all that can keep it alive is a tool call that outlived the
    session, which interpreter exit would join however long it runs. Measured
    in the pilot: two wall-time sessions held their slot 1,034 s and 1,455 s
    past the end of their records, both past the hard deadline.

    An AG-o session gets a sandbox root of its own (an opaque temporary
    directory under ``sandbox_root``). A killed process leaves its sandbox
    behind: the agent's container still running and ``work/`` owned by the
    container's uid. So once the session has ended, however it ended, this
    process recovers every run directory left in that root
    (:func:`~sciagent.sandbox.recover_run_dirs`) and removes the root. If
    that fails, the root is left in place and logged, and the outcome's
    detail notes ``sandbox_cleanup``.
    """
    session_root = _session_root(config, unit, sandbox_root)
    try:
        outcome = _run_isolated(
            unit, truth, env, config, out_dir, session_root, grace_s
        )
    finally:
        fault = None if session_root is None else _release_session_root(session_root)
    if fault is None:
        return outcome
    return dataclasses.replace(
        outcome, detail={**outcome.detail, "sandbox_cleanup": fault}
    )


def _session_root(
    config: CampaignConfig, unit: Unit, sandbox_root: Path | None
) -> Path | None:
    """A fresh, opaque sandbox root for an AG-o session; ``None`` for AG-c."""
    if config.arm_of(unit.system).arm != "AG-o":
        return None
    if sandbox_root is not None:
        sandbox_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=sandbox_root))


def _release_session_root(root: Path) -> str | None:
    """Recover what a session left under ``root``, then remove ``root``.

    Never raises (it runs in a ``finally``); returns the failing error's type
    (its message holds random paths, and the detail is stored), or ``None``.
    """
    try:
        recover_run_dirs(root, image=image_tag())
        shutil.rmtree(root)
    except (SandboxError, OSError) as error:
        _log.warning(
            "sandbox root %s not recovered, left in place: %s: %s",
            root,
            type(error).__name__,
            error,
        )
        return type(error).__name__
    return None


#: How often the parent looks again while the child is inside a decision, in
#: seconds: a kill can come at most this late after a decision ends.
DECISION_POLL_S: Final = 1.0


def _answered(
    receive: Connection, shared: SharedClock, began: float, deadline: float
) -> bool:
    """Wait for the child's answer until its own time passes ``deadline``.

    The child's own time is the time since ``began`` less its framework
    decision time (P4b D5), read from ``shared``; it stands still while a
    decision runs, so a decision is never cut by the hard deadline, and the
    grace bounds only what the session spends outside decisions. Raises
    ``EOFError`` as :meth:`Connection.poll` does.
    """
    while True:
        now = time.monotonic()
        framework, deciding = _framework_s(shared, now)
        left = deadline - (now - began - framework)
        if left <= 0:
            return False
        if receive.poll(max(left, DECISION_POLL_S) if deciding else left):
            return True


def _run_isolated(
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    sandbox_root: Path | None,
    grace_s: float,
    *,
    target: SessionTarget = _child,
    exit_grace_s: float = EXIT_GRACE_S,
) -> SessionOutcome:
    """The process part of :func:`run_session_isolated`.

    ``target`` is what the process runs (:func:`_child`; tests replace it).
    """
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    shared: SharedClock = ctx.Array("d", [0.0, _NOT_MEASURING])
    process = ctx.Process(
        target=_session_process,
        args=(target, send, shared, unit, truth, env, config, out_dir, sandbox_root),
        daemon=True,
    )
    start = time.perf_counter()
    began = time.monotonic()
    process.start()
    send.close()
    deadline = config.wall_time_s + grace_s
    try:
        if _answered(receive, shared, began, deadline):
            outcome: SessionOutcome = receive.recv()
            process.join(exit_grace_s)
            if process.is_alive():
                _kill_session(process)
            # The driver's clock, spawn included, as for a hard timeout below,
            # so the unit's wall time is one measurement whatever the outcome.
            return dataclasses.replace(outcome, wall_s=time.perf_counter() - start)
    except EOFError as error:
        process.join()
        return _retry(unit, error, start)
    finally:
        receive.close()
    _kill_session(process)
    detail: dict[str, Json] = {
        "outcome": "hard_timeout",
        "error": "HardTimeout",
        "message": f"the session did not end within {deadline:.0f} s of its own "
        f"time (wall time {config.wall_time_s:.0f} s plus {grace_s:.0f} s; "
        f"framework decision time not counted): a tool call kept running past "
        f"the wall time; the process was killed",
    }
    reading = {
        "status": 0.0,
        "void": 1.0,
        "outcome": float(OUTCOMES.index("hard_timeout")),
    }
    return SessionOutcome(
        unit,
        False,
        reading,
        detail,
        None,
        (),
        time.perf_counter() - start,
        session_timing(None),
    )
