"""The campaign scheduler: a process pool for non-LLM work, threads for LLM sessions.

**What runs where.**

- *Non-LLM jobs* (:func:`~sciagent.campaign.execute.run_baseline_job`) and
  *LLM scoring jobs* (:func:`~sciagent.campaign.execute.run_score_job`) run in
  a ``spawn`` process pool of ``workers`` processes. Every worker process is
  started with ``OPENBLAS_NUM_THREADS = OMP_NUM_THREADS = MKL_NUM_THREADS = 1``
  in its environment (set here before the pool exists, so it holds before the
  child imports numpy) and fits with ``workers = 1``. Workers are reused
  across jobs: ``max_tasks_per_child = 1`` was tried and hung the pool on
  Python 3.12 (replacement workers sat idle while submitted jobs never ran;
  CPython gh-115634), so feature caches live for a worker's lifetime (budget
  ~2 GB per busy worker, measured in P2).
- *LLM sessions* (:func:`~sciagent.campaign.llm.run_session`) run in up to
  ``llm_concurrency`` threads of this process (one asyncio loop each), while
  the pool keeps going. Their own ``fit`` calls run in-process.
- The **store** is written only by the main thread (the ledger's connection
  is bound to it).

**Jobs.** On each (truth, seed) ORACLE, B-lib and B-np form one job (each takes
seconds); every other system is its own job, so a long unit (B-sym@10F, a
heavy B-rand) occupies one worker and blocks nothing else. The queue is
seed-major, then truth; scoring jobs for finished LLM sessions jump the
queue.

**Resumable.** A unit whose address is in the store is skipped; nothing is
ever overwritten. An LLM unit whose session is stored but whose score is not
is scored from the session's stored detail, without calling the model.

**Failures.** A unit that fails with a framework or numerical error is
recorded as a failed result by the worker (``execute``). A job that raises
anything else is a bug: it is logged with its traceback to the progress log
and nothing is recorded, so it runs again on the next start. A worker process
that dies (out of memory) breaks the pool: the pool is rebuilt and the jobs in
flight are resubmitted; a job in flight at two breaks is skipped for this run
and logged (an environment-dependent failure is not a result).

**Rate limits.** A retryable LLM attempt (:mod:`sciagent.campaign.llm`) drops
the LLM concurrency to 1 for the rest of the run, is logged to
``attempts.jsonl``, and the unit goes to the back of the LLM queue
(at most ``max_attempts`` attempts). After ``backoff_after`` consecutive
retryable attempts, launching pauses for ``backoff_s`` (doubling each time);
after ``max_backoffs`` pauses without a success in between, no new LLM unit
is launched for the rest of the run, and that is logged. Creating the file
``STOP`` in the root stops launching new units of either kind; ``STOP_LLM``
stops launching LLM sessions only.

**Several processes on one store.** Before a job or a session is launched its
units are checked against the store again, so two drivers on one root (say a
second, LLM-only one) never run a unit twice; at worst both run it at the same
moment and the second write is refused and logged.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import time
import traceback
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from sciagent.campaign.execute import (
    BaselineJob,
    ScoreJob,
    UnitResult,
    run_baseline_job,
    run_score_job,
)
from sciagent.campaign.llm import SessionOutcome, run_session, run_session_isolated
from sciagent.campaign.plan import (
    B_LIB,
    B_NP,
    B_SPARSE,
    ORACLE,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    Unit,
    baseline_key,
    baseline_units,
    llm_units,
    score_key,
    session_key,
    session_text,
    system_config_text,
)
from sciagent.campaign.store import ResultStore
from sciagent.core.errors import SciAgentError
from sciagent.harness.live import SessionDriver
from sciagent.registry.store import ExperimentKey

type Json = Any

#: Systems run together as one job per (truth, seed): each takes seconds.
FAST_GROUP: Final = (ORACLE, B_LIB, B_NP)
THREAD_VARIABLES: Final = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
LOG_FILE: Final = "progress.log"
ATTEMPTS_FILE: Final = "attempts.jsonl"
STOP_FILE: Final = "STOP"
STOP_LLM_FILE: Final = "STOP_LLM"
LLM_DIR: Final = "llm"

#: Builds the session driver for one LLM unit; None means the live SDK.
type DriverFactory = Callable[[Unit], SessionDriver | None]


@dataclass(frozen=True)
class DriverSettings:
    """How a run is executed (none of it changes a result)."""

    #: Worker processes; 0 runs jobs in one thread of this process (tests and
    #: debugging: the scheduler is unchanged and job functions can be patched).
    workers: int = 4
    llm_concurrency: int = 1
    llm_limit: int | None = None
    run_baselines: bool = True
    run_llm: bool = True
    max_attempts: int = 3
    backoff_after: int = 3
    backoff_s: float = 900.0
    max_backoffs: int = 4
    poll_s: float = 30.0
    sandbox_root: Path | None = None
    #: Run each live session in its own process with a hard deadline
    #: (:func:`~sciagent.campaign.llm.run_session_isolated`); a scripted
    #: driver (``driver_factory``) always runs in-thread.
    isolate_llm: bool = True
    #: Memory-heavy jobs (B-sparse) allowed in flight at once.
    max_heavy: int = 2
    driver_factory: DriverFactory | None = field(default=None, compare=False)


@dataclass
class _PoolJob:
    job: BaselineJob | ScoreJob
    units: tuple[Unit, ...]
    keys: tuple[ExperimentKey, ...]
    breaks: int = 0


@dataclass
class _LLMTask:
    unit: Unit
    key: ExperimentKey
    attempts: int = 0


@dataclass(frozen=True)
class RunSummary:
    """Counts for one :meth:`Campaign.run` (logged, and returned for tests)."""

    recorded: Mapping[str, int]
    failed: Mapping[str, int]
    skipped_done: int
    bugs: int
    retries: int
    llm_stopped: bool


def _logger(root: Path) -> logging.Logger:
    log = logging.getLogger(f"sciagent.campaign.{root.resolve()}")
    if not log.handlers:
        handler = logging.FileHandler(root / LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(handler)
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(stream)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


class Campaign:
    """One campaign over ``truths`` (see the module docstring)."""

    def __init__(
        self,
        root: Path,
        env: CampaignEnvironment,
        config: CampaignConfig,
        truths: Sequence[TruthEntry],
        settings: DriverSettings,
    ) -> None:
        ids = [t.id for t in truths]
        if len(set(ids)) != len(ids):
            raise CampaignError("truth ids must be distinct")
        self.root = root
        self.env = env
        self.worker_env = replace(env, investigation=None)
        self.config = config
        self.truths = {t.id: t for t in truths}
        self.order = tuple(ids)
        self.settings = settings
        self.store = ResultStore(root)
        self.log = _logger(root)
        self.pool_queue: deque[_PoolJob] = deque()
        self.llm_queue: deque[_LLMTask] = deque()
        self.recorded: dict[str, int] = {}
        self.failed: dict[str, int] = {}
        self.skipped_done = 0
        self.bugs = 0
        self.retries = 0
        self.llm_launched = 0
        self.llm_stopped = False
        self.consecutive_retry = 0
        self.backoffs = 0
        self.paused_until = 0.0
        self._llm_tasks: dict[Future[SessionOutcome], _LLMTask] = {}
        self.llm_concurrency = settings.llm_concurrency

    # -- planning ------------------------------------------------------------

    def _plan_baselines(self) -> None:
        units = baseline_units(self.order, self.config)
        pending: dict[tuple[str, int], list[Unit]] = {}
        for u in units:
            key = baseline_key(u, self.truths[u.truth_id], self.env, self.config)
            if self.store.done(key):
                self.skipped_done += 1
                continue
            pending.setdefault((u.truth_id, u.seed), []).append(u)
        for seed in self.config.seeds:
            for t in self.order:
                todo = pending.get((t, seed), [])
                fast = tuple(u for u in todo if u.system in FAST_GROUP)
                if fast:
                    self._queue_baselines(fast)
                for u in todo:
                    if u.system not in FAST_GROUP:
                        self._queue_baselines((u,))

    def _queue_baselines(self, units: tuple[Unit, ...]) -> None:
        truth = self.truths[units[0].truth_id]
        job = BaselineJob(
            truth,
            units[0].seed,
            tuple(u.system for u in units),
            self.worker_env,
            self.config,
        )
        keys = tuple(baseline_key(u, truth, self.env, self.config) for u in units)
        self.pool_queue.append(_PoolJob(job, units, keys))

    def _plan_llm(self) -> None:
        for u in llm_units(self.order, self.config):
            truth = self.truths[u.truth_id]
            skey = session_key(u, truth, self.env, self.config)
            if self.store.done(skey):
                ckey = score_key(u, skey, self.env, self.config)
                if self.store.done(ckey):
                    self.skipped_done += 1
                else:
                    self._queue_score(u, skey, self.store.cell(skey.digest))
                continue
            self.llm_queue.append(_LLMTask(u, skey))

    def _queue_score(
        self, unit: Unit, skey: ExperimentKey, cell: Mapping[str, Json]
    ) -> None:
        detail = cell["detail"]["session"]
        if cell["reading"].get("status") != (1.0).hex():
            return  # an aborted session has nothing to score
        job = ScoreJob(
            self.truths[unit.truth_id],
            unit.seed,
            unit.system,
            detail["submission"],
            tuple(detail["fitted"]),
            self.worker_env,
            self.config,
        )
        ckey = score_key(unit, skey, self.env, self.config)
        self.pool_queue.appendleft(_PoolJob(job, (unit,), (ckey,)))

    # -- recording -----------------------------------------------------------

    def _unit_json(self, unit: Unit, kind: str) -> dict[str, Json]:
        return {
            "truth": unit.truth_id,
            "seed": unit.seed,
            "system": unit.system,
            "kind": kind,
        }

    def _record(self, pj: _PoolJob, results: Sequence[UnitResult]) -> None:
        by_system = {r.system: r for r in results}
        for unit, key in zip(pj.units, pj.keys, strict=True):
            r = by_system.get(unit.system)
            if r is None:
                raise CampaignError(f"job returned no result for {unit}")
            kind = "llm-score" if isinstance(pj.job, ScoreJob) else "baseline"
            detail: dict[str, Json] = {
                "unit": self._unit_json(unit, kind),
                "result": r.detail,
            }
            if kind == "baseline":
                detail["system_config"] = system_config_text(
                    unit.system, self.env, self.config
                )
            self.store.put(key, r.reading, detail)
            bucket = self.recorded if r.ok else self.failed
            bucket[unit.system] = bucket.get(unit.system, 0) + 1
            self.store.log_timing(
                {
                    **self._unit_json(unit, kind),
                    "digest": str(key.digest),
                    "ok": r.ok,
                    "wall_s": round(r.wall_s, 3),
                    **(r.timing or {}),
                }
            )
            status = "ok" if r.ok else f"FAILED {r.detail.get('error')}"
            self.log.info(
                f"{kind} {unit.system} {unit.truth_id} s{unit.seed} {status} "
                f"{r.wall_s:.1f}s"
            )

    def _record_session(self, task: _LLMTask, out: SessionOutcome) -> None:
        unit = task.unit
        detail: dict[str, Json] = {
            "unit": self._unit_json(unit, "llm-session"),
            "session_config": session_text(
                self.config.arm_of(unit.system), self.config
            ),
            "session": {
                **out.detail,
                "record_dir": f"{LLM_DIR}/{str(task.key.digest)[:16]}",
            },
        }
        self.store.put(task.key, out.reading, detail)
        self.store.log_timing(
            {
                **self._unit_json(unit, "llm-session"),
                "digest": str(task.key.digest),
                "wall_s": round(out.wall_s, 3),
                "attempt": task.attempts,
            }
        )
        r = out.reading
        self.log.info(
            f"llm-session {unit.system} {unit.truth_id} s{unit.seed} "
            f"{out.detail.get('outcome')} turns={r.get('turns')} "
            f"out_tokens={r.get('output_tokens')} fits={r.get('fits_used')} "
            f"exp={r.get('experiments_used')} {out.wall_s:.0f}s"
        )
        self._queue_score(unit, task.key, self.store.cell(task.key.digest))

    def _attempt(self, task: _LLMTask, info: Mapping[str, Json]) -> None:
        line = json.dumps(
            {
                **self._unit_json(task.unit, "llm-session"),
                "attempt": task.attempts,
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **info,
            },
            sort_keys=True,
            default=str,
        )
        with (self.root / ATTEMPTS_FILE).open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")

    # -- the loop ------------------------------------------------------------

    def _stop_requested(self) -> bool:
        return (self.root / STOP_FILE).exists()

    def _new_pool(self) -> Executor:
        if self.settings.workers == 0:
            return ThreadPoolExecutor(max_workers=1)
        for var in THREAD_VARIABLES:
            os.environ[var] = "1"
        return ProcessPoolExecutor(
            max_workers=self.settings.workers,
            mp_context=multiprocessing.get_context("spawn"),
        )

    def _llm_may_launch(self) -> bool:
        s = self.settings
        # Another process may have run a queued unit since this one planned.
        while self.llm_queue and self.store.done(self.llm_queue[0].key):
            self.llm_queue.popleft()
            self.skipped_done += 1
        if (self.root / STOP_LLM_FILE).exists():
            return False
        if not s.run_llm or self.llm_stopped or not self.llm_queue:
            return False
        if s.llm_limit is not None and self.llm_launched >= s.llm_limit:
            return False
        return time.monotonic() >= self.paused_until

    def _launch_llm(self, threads: ThreadPoolExecutor) -> Future[SessionOutcome]:
        task = self.llm_queue.popleft()
        task.attempts += 1
        self.llm_launched += 1
        truth = self.truths[task.unit.truth_id]
        out_dir = self.root / LLM_DIR / str(task.key.digest)[:16]
        factory = self.settings.driver_factory
        driver = None if factory is None else factory(task.unit)
        self.log.info(
            f"llm-launch {task.unit.system} {task.unit.truth_id} s{task.unit.seed} "
            f"attempt {task.attempts}"
        )
        future: Future[SessionOutcome]
        if driver is None and self.settings.isolate_llm:
            future = threads.submit(
                run_session_isolated,
                task.unit,
                truth,
                self.env,
                self.config,
                out_dir,
                sandbox_root=self.settings.sandbox_root,
            )
        else:
            future = threads.submit(
                run_session,
                task.unit,
                truth,
                self.env,
                self.config,
                out_dir,
                driver=driver,
                sandbox_root=self.settings.sandbox_root,
            )
        self._llm_tasks[future] = task
        return future

    def _on_session(self, future: Future[SessionOutcome]) -> None:
        task = self._llm_tasks.pop(future)
        error = future.exception()
        if error is not None:
            self.bugs += 1
            self.log.error(
                f"BUG in llm-session {task.unit}: "
                + "".join(traceback.format_exception(error))
            )
            self._attempt(task, {"bug": type(error).__name__, "message": str(error)})
            return
        out = future.result()
        if not out.retryable:
            self.consecutive_retry = 0
            self.backoffs = 0
            self._guarded((task.unit,), self._record_session, task, out)
            return
        self.retries += 1
        self.consecutive_retry += 1
        if self.llm_concurrency > 1:
            self.llm_concurrency = 1
            self.log.warning("llm: retryable failure; concurrency reduced to 1")
        self._attempt(task, {"retryable": True, **out.detail})
        self.log.warning(
            f"llm retryable {task.unit.system} {task.unit.truth_id} "
            f"s{task.unit.seed}: {out.detail.get('error') or out.detail.get('outcome')}"
            f" {out.detail.get('message', '')[:200]}"
        )
        if task.attempts < self.settings.max_attempts:
            self.llm_queue.append(task)
        else:
            self.log.warning(f"llm unit given up after {task.attempts} attempts")
        if self.consecutive_retry >= self.settings.backoff_after:
            self.consecutive_retry = 0
            if self.backoffs >= self.settings.max_backoffs:
                self.llm_stopped = True
                self.log.warning(
                    "llm: repeated retryable failures (rate limits?); no new LLM "
                    "unit will be launched in this run"
                )
                return
            pause = self.settings.backoff_s * 2**self.backoffs
            self.backoffs += 1
            self.paused_until = time.monotonic() + pause
            self.log.warning(f"llm: pausing launches for {pause:.0f}s")

    def run(self) -> RunSummary:
        """Run every pending unit; return the counts."""
        recovered = self.store.recover()
        if recovered:
            self.log.info(f"recovered {recovered} cells from detail files")
        if self.settings.run_baselines:
            self._plan_baselines()
        if self.settings.run_llm:
            self._plan_llm()
        self.log.info(
            f"plan: {len(self.pool_queue)} pool jobs, {len(self.llm_queue)} LLM "
            f"units, {self.skipped_done} units already done"
        )
        pool_inflight: dict[Future[tuple[UnitResult, ...]], _PoolJob] = {}
        pool = self._new_pool()
        threads = ThreadPoolExecutor(max_workers=max(1, self.settings.llm_concurrency))
        try:
            while True:
                stop = self._stop_requested()
                while (
                    not stop
                    and self.pool_queue
                    and len(pool_inflight) < max(1, self.settings.workers)
                ):
                    pending = self._pending(self._next_job(pool_inflight))
                    if pending is not None:
                        pool_inflight[pool.submit(_run_job, pending.job)] = pending
                while (
                    not stop
                    and len(self._llm_tasks) < self.llm_concurrency
                    and self._llm_may_launch()
                ):
                    self._launch_llm(threads)
                active: list[Future[Any]] = [*pool_inflight, *self._llm_tasks]
                if not active:
                    waiting = (
                        not stop
                        and not (self.root / STOP_LLM_FILE).exists()
                        and self.llm_queue
                        and self.settings.run_llm
                        and not self.llm_stopped
                        and (
                            self.settings.llm_limit is None
                            or self.llm_launched < self.settings.llm_limit
                        )
                    )
                    if waiting:
                        time.sleep(
                            max(1.0, min(60.0, self.paused_until - time.monotonic()))
                        )
                        continue
                    break
                done, _ = wait(
                    active, timeout=self.settings.poll_s, return_when=FIRST_COMPLETED
                )
                broken = False
                for future in sorted(done, key=id):
                    if future in self._llm_tasks:
                        self._on_session(future)
                        continue
                    pj = pool_inflight.pop(future)
                    error = future.exception()
                    if isinstance(error, BrokenProcessPool):
                        broken = True
                        pj.breaks += 1
                        self._requeue_broken(pj)
                    elif error is not None:
                        self.bugs += 1
                        self.log.error(
                            f"BUG in job {pj.units}: "
                            + "".join(traceback.format_exception(error))
                        )
                    else:
                        self._guarded(pj.units, self._record, pj, future.result())
                if broken:
                    for pj in pool_inflight.values():
                        pj.breaks += 1
                        self._requeue_broken(pj)
                    pool_inflight.clear()
                    pool.shutdown(wait=True, cancel_futures=True)
                    self.log.warning("worker process died; pool rebuilt")
                    pool = self._new_pool()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            threads.shutdown(wait=True)
        summary = RunSummary(
            dict(sorted(self.recorded.items())),
            dict(sorted(self.failed.items())),
            self.skipped_done,
            self.bugs,
            self.retries,
            self.llm_stopped,
        )
        self.log.info(f"run finished: {summary}")
        return summary

    def _next_job(self, inflight: Mapping[Any, _PoolJob]) -> _PoolJob:
        """The first queued job that keeps heavy jobs within their cap.

        B-sparse peaks at ~1.7 GB per process (the dictionary), so at most
        ``settings.max_heavy`` run at once; a deferred one keeps its place.
        """
        heavy = sum(_is_heavy(pj) for pj in inflight.values())
        if heavy >= self.settings.max_heavy:
            for i, pj in enumerate(self.pool_queue):
                if not _is_heavy(pj):
                    del self.pool_queue[i]
                    return pj
        return self.pool_queue.popleft()

    def _pending(self, pj: _PoolJob) -> _PoolJob | None:
        """``pj`` without the units already in the store (None if all are).

        Another process sharing the store may have run them since this one
        planned; a unit is never run twice.
        """
        todo = [
            (u, k)
            for u, k in zip(pj.units, pj.keys, strict=True)
            if not self.store.done(k)
        ]
        self.skipped_done += len(pj.units) - len(todo)
        if not todo:
            return None
        if len(todo) == len(pj.units):
            return pj
        job = pj.job
        if isinstance(job, BaselineJob):
            job = replace(job, systems=tuple(u.system for u, _ in todo))
        return _PoolJob(
            job, tuple(u for u, _ in todo), tuple(k for _, k in todo), pj.breaks
        )

    def _guarded(
        self, units: Sequence[Unit], fn: Callable[..., None], *args: Any
    ) -> None:
        """Record a result; a failure to record it is a bug, logged, not fatal.

        The units stay unrecorded, so they run again on the next start.
        """
        try:
            fn(*args)
        except (SciAgentError, ValueError, TypeError, OSError) as error:
            self.bugs += 1
            self.log.error(
                f"BUG recording {list(units)}: "
                + "".join(traceback.format_exception(error))
            )

    def _requeue_broken(self, pj: _PoolJob) -> None:
        if pj.breaks >= 2:
            self.log.error(
                f"job {pj.units} was in flight at {pj.breaks} pool breaks; skipped"
            )
            return
        self.pool_queue.appendleft(pj)

    def close(self) -> None:
        self.store.close()


def _is_heavy(pj: _PoolJob) -> bool:
    return isinstance(pj.job, BaselineJob) and B_SPARSE in pj.job.systems


def _run_job(job: BaselineJob | ScoreJob) -> tuple[UnitResult, ...]:
    """The pool's entry point (module-level, so spawned workers can import it)."""
    if isinstance(job, ScoreJob):
        return (run_score_job(job),)
    return run_baseline_job(job)
