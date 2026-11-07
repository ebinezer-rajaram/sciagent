"""Tuning B-sym on dev before the §6.1 freeze: an instrument (SPEC §6.3).

The rule is ``docs/v2/prereg/2026-11-06_bsym-tuning.md``; a section number below
means that file. This script implements it and decides nothing of its own. It
refuses to launch until the text is frozen: ``PREREG_SHA256`` and ``F_COMMIT`` are
set then, and a commit that sets them is the script's commit the LOG names.

    # stage 1, the launch: written here, run by the lane after the freeze
    systemd-run --user --collect --unit=sci-bsym-tune --nice=10 \
        -p CPUQuota=200% -p MemoryMax=5G -p OOMPolicy=continue \
        -E OPENBLAS_NUM_THREADS=1 -E OMP_NUM_THREADS=1 -E MKL_NUM_THREADS=1 \
        --working-directory=<checkout> uv run python scripts/bsym_tune.py \
        --workers 2 --split <abs>

    # the same command resumes after a crash, a reboot or an out-of-memory kill
    # the line, with this checkout's paths filled in:
    uv run python scripts/bsym_tune.py --print-launch --split <abs>

    # for the LOG entry
    uv run python scripts/bsym_tune.py --print-hashes

    # a rehearsal: seeds 1 to 3 and its own root only, never the launch's
    uv run python scripts/bsym_tune.py --rehearsal --split <abs> \
        --truths dev-000 --seeds 1 --fits 4

    # stage 2, once selection.json names a winner other than c0: the launch line
    # with --stage2 (--print-launch --stage2 prints it), at the same commit

    # section 2's table, once, read-only from a P3 store; its hash is P3_TABLE_SHA256
    uv run python scripts/bsym_tune.py --export-p3-table <store>

    # section 5: the run has its own detached worktree, whose HEAD never moves.
    # The lane copies each record to S3 and commits it under docs/v2/bsym-tuning/
    # from the main checkout, so that cannot move the commit stage 2 checks. The
    # script does neither, and touches no AWS.

**Stage 1.** All 20 dev truths at seeds 4, 5 and 6, 60 worlds, each of the 14
settings (:data:`SETTINGS`) on every world at F = 40. A unit runs by the framework's
normal path, :func:`~sciagent.campaign.execute.run_baseline_job`, under a
:class:`~sciagent.campaign.plan.CampaignConfig` holding the setting's ``gp`` and an
environment holding its ``sym_seeds``, so ``gp`` and the seeds are in its address,
and its cell is in ``_record``'s shape. ``c0b`` alone cannot go that way: the search
seed is no argument of that path. It runs :func:`run_shifted_unit`, which a test ties
to the framework's path byte for byte. An address holds no search seed, so ``c0b``
has ``c0``'s, and its cells go in the store :data:`C0B_STORE` under the run's own.

**Processes.** As ``pilot_search_misses.py``, whose mechanism is copied here and not
imported, since that file is frozen: a unit is one process that serves that unit
only, so ``RLIMIT_CPU`` is per unit and a unit's death cannot break a sibling. The
exit is read by :func:`exit_kind`: SIGXCPU is the CPU limit, journalled ``cpu_limit``
so that a resume does not rerun the unit, any other signal is a lost worker (retried
once, after the rest; lost twice is journalled ``worker_lost``), and both take the
floor. An uncaught exception is a bug, which stops the run with no label.

**Records.** ``journal.jsonl`` is appended and fsynced per record by whichever process
owns the unit, so the driver may die at any moment. The driver alone writes the
stores, from the journal, so each keeps its one writer. ``cpu.json`` carries the
kernel's count across restarts. ``selection.json`` is written last and is stage 1's
latch. ``cpu_report.json`` holds what is not repeatable, so that ``selection.json``'s
bytes are.

**Stage 2** (:func:`run_stage2`), on its own store and CPU count, at the selecting
commit. A pass (:func:`plan_stage2`) is B-sym@10F, then planted-hint, read by
:func:`read_pass` with gaps against P3's stored ORACLE: ``gonogo``'s control 2 on all
20 truths and control 3 on the ten. A pass not finished has failed. The winner takes a
pass, and on failure the next rank takes one. ``final.json`` is written at the end of
the passes, is their latch, and nothing later changes it. :func:`final_setting` is what
stands: a winner other than ``c0`` stands only by a ``final.json`` on the rule's own
passes. Then, as its own step on the same store and CPU count, B-sym
(:func:`plan_margin`, :func:`run_margin`) runs for what stands, if it is not ``c0``.
``margin.json`` is that step's latch, and no rule reads it: a stop or the CPU limit
writes it with ``finished`` false, and a kill resumes by the same command, so a kill
there costs a passed winner nothing. ``c0`` stands only from a pass that failed or
did not finish.

**A bug found after an outcome is read** stops every further step, and ``c0`` stands
(section 6). Nothing here reruns it. An amendment that meets section 6's conditions
moves :data:`STORE_DIR`, which every path of a run derives from, so the rerun is on the
same seeds in a new directory and the first result stays beside it.

**Choices the text leaves open** are the constants under "Open in the text" below.
They are not decided here: each is a named constant for the freeze to confirm.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
import resource
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from itertools import product
from pathlib import Path
from types import FrameType
from typing import Any, Final, Literal, Protocol

import numpy as np
import pilot

from sciagent.campaign import cpu
from sciagent.campaign.execute import (
    UNIT_FAILURES,
    BaselineJob,
    UnitResult,
    observational_data,
    run_baseline_job,
    score_submission,
)
from sciagent.campaign.freeze import DEV_SPLIT
from sciagent.campaign.plan import (
    B_LIB,
    B_RAND,
    B_SYM,
    B_SYM_10F,
    ORACLE,
    PLANTED,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    Unit,
    baseline_key,
    search_seed,
    system_config_text,
)
from sciagent.campaign.store import (
    DETAILS_DIR,
    LEDGER_FILE,
    ResultStore,
    json_safe,
)
from sciagent.core import reductions
from sciagent.glm.grammar import ALL, Excite, KernelKind, Link, One, Structure
from sciagent.registry.store import ExperimentKey
from sciagent.scenarios.records import canonical_json
from sciagent.scoring import gonogo
from sciagent.scoring.efficiency import efficiency_curve
from sciagent.scoring.heldout import held_out_dataset
from sciagent.scoring.stats import BootstrapCI, paired_bootstrap_ci
from sciagent.systems.v2.search import (
    DEFAULT_MUTATION_WEIGHTS,
    BSym,
    GPConfig,
    Mutation,
)

type Json = Any
type Record = dict[str, Json]
type World = tuple[str, int]
type Cell = tuple[str, int, str]

# --------------------------------------------------------------------------
# Set at the freeze: empty refuses every launch
# --------------------------------------------------------------------------

#: sha256 of the frozen pre-registration, in code, not typed by the operator.
PREREG_SHA256: Final = ""
#: The commit that freezes it.
F_COMMIT: Final = ""

# --------------------------------------------------------------------------
# Fixed by the text
# --------------------------------------------------------------------------

PREREG_PATH: Final = "docs/v2/prereg/2026-11-06_bsym-tuning.md"
LOG_PATH: Final = "docs/v2/LOG.md"
#: Section 11: the one line the LOG holds before launch. ``commit`` is the first seven
#: characters of the last commit to touch the script; ``worktree`` the run's absolute
#: path, resolved. Read as a substring of ``LOG_PATH`` in that worktree.
LAUNCH_LOG_LINE: Final = "bsym-tune launch: commit {commit}, worktree {worktree}"
SCRIPT_PATH: Final = "scripts/bsym_tune.py"
TEST_PATH: Final = "tests/campaign/test_bsym_tune.py"
#: Section 11: the launch's store. Every path of a run derives from this, and
#: section 6's amendment names a new one.
STORE_DIR: Final = ".cache/drafts/bsym-tune"
#: Section 2: ``c0b``'s store, under the run's own.
C0B_STORE: Final = "c0b"
#: Section 6: stage 2's store, under the run's own.
STAGE2_STORE: Final = "stage2"
#: Section 5: the records, at constant paths with no option, and where the §6.1
#: commit puts them. Until they exist, :func:`final_setting_at` reads ``c0``.
SELECTION_RECORD: Final = "docs/v2/bsym-tuning/selection.json"
FINAL_RECORD: Final = "docs/v2/bsym-tuning/final.json"
#: Section 2: the softplus Hawkes seed ``c11`` adds, by its DSL text.
C11_SEED_TEXT: Final = "link=softplus; Excite(ExpK, One, all)"
#: Section 2: ``c0b``'s search seed is ``(search_seed + 1) % 2**63``.
C0B_SEED_SHIFT: Final = 1
#: Section 11: new, and the launch's alone. Passed in, never defaulted.
SEED_ROOT: Final = 20261113
#: Section 6: exactly these in stage 1.
STAGE1_SEEDS: Final = (4, 5, 6)
#: Section 2: P4's budget.
TUNE_FITS: Final = 40
#: Section 3: c0 at 40 fits minus c0 at 30 fits.
GRADED_LOW_FITS: Final = 30
RESAMPLES: Final = 10_000
LEVEL: Final = 0.95
#: Section 5, rule 2.
MIN_COMPLETE_WORLDS: Final = 54
#: The dev split's size (section 7).
DEV_TRUTHS: Final = 20
#: Section 8: the mechanism's gate, checked before each dispatch.
MEM_GATE_GIB: Final = 8
WORKERS: Final = 2
UNIT_NAME: Final = "sci-bsym-tune"
#: Section 3's phrase for a search with nothing to submit (``search.py``).
NO_PROPOSAL: Final = "no proposal produced a certified fit"
#: The split's hash, as ``pilot_search_misses.py`` pins it.
SPLIT_SHA256: Final = "31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577"
#: The split's path as the pinned hash names it, whatever checkout holds it.
SPLIT_RELATIVE: Final = "data/v2/truths/dev"
BLAS_VARIABLES: Final = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
)
#: Hard ``RLIMIT_CPU`` above the soft one: the soft limit's SIGXCPU kills the unit;
#: only a SIGXCPU that was somehow ignored reaches this SIGKILL. Equal limits would
#: send SIGKILL alone, which reads as a lost worker.
CPU_HARD_MARGIN_S: Final = 60
START_METHOD: Final = "spawn"


@dataclass(frozen=True)
class StageLimits:
    """Section 8: per-unit ``RLIMIT_CPU`` and the kernel count that stops dispatch."""

    limit_s: int
    stop_s: int


STAGE1: Final = StageLimits(limit_s=2 * 3600, stop_s=110 * 3600)
STAGE2: Final = StageLimits(limit_s=14 * 3600, stop_s=120 * 3600)

#: Section 4: candidate ``i`` of the 12 draws ``SeedSequence([root, 1 + i])``.
SECONDARY_STREAM: Final = 1
#: Section 2: pass ``p``, from 1, draws ``SeedSequence([root, 12 + p])``.
STAGE2_STREAM: Final = 12
#: Section 2: the winner and, once, the next-ranked.
MAX_STAGE2_PASSES: Final = 2
#: Section 2: stage 2's seeds, and B-sym@10F's truths, which control 3 reads at the
#: first of those seeds: the first three near, the first three far and all four mid.
STAGE2_SEEDS: Final = (1, 2, 3)
STAGE2_CONTROL_TRUTHS: Final = (
    "dev-000",
    "dev-001",
    "dev-002",
    "dev-003",
    "dev-004",
    "dev-005",
    "dev-010",
    "dev-015",
    "dev-016",
    "dev-019",
)
#: Section 2: a unit killed, failed or without a reading.
STAGE2_UNSCORED_GAP: Final = -math.inf
#: Section 2's table of stored rows, exported once, and its pin. Empty refuses.
P3_TABLE_PATH: Final = "docs/v2/bsym-tuning/p3-rows.json"
P3_TABLE_SHA256: Final = (
    "1766167791b93ccbc5b7cbcaada6d2b406e6bf41bce9d89a7e9f18e438dda792"
)
P3_TABLE_COMMIT: Final = "9db1a46"
P3_TABLE_SEEDS: Final = (1, 2, 3)
P3_TABLE_CONTROL_TRUTHS: Final = 10
P3_SYSTEMS: Final = (ORACLE, B_LIB, B_RAND, B_SYM)

# --------------------------------------------------------------------------
# Open in the text: named here, for the freeze to confirm
# --------------------------------------------------------------------------

#: Rehearsal only: its roots, seeds and the share of worlds rule 2 asks for.
REHEARSAL_DIR: Final = ".cache/drafts/bsym-tune-rehearsal"
REHEARSAL_ROOTS: Final = (20261120,)
REHEARSAL_SEEDS: Final = (1, 2, 3)
#: 54 of 60, as a share, for a rehearsal's smaller grid.
REHEARSAL_COMPLETE_SHARE: Final = (9, 10)
#: ``final.json``'s and ``margin.json``'s place: beside ``selection.json``, not in
#: stage 2's store. The text fixes the names and that the paths are constant.
FINAL_FILE: Final = "final.json"
MARGIN_FILE: Final = "margin.json"

#: Exit codes. A label is not an exit code: any label writes ``selection.json``.
EXIT_DONE: Final = 0
EXIT_REFUSED: Final = 3
EXIT_NO_LABEL: Final = 5

#: How a run may have ended and still be resumed (section 6): no ``run_end`` at all,
#: or a SIGTERM.
RESUMABLE_ENDS: Final = ("killed", "terminated")
#: Ends section 5 applies to. A stop cannot be undone by removing its cause.
FINAL_ENDS: Final = ("complete", "stop", "cpu_stop")
LATCHED_ENDS: Final = ("stop", "cpu_stop")


class GuardFailure(RuntimeError):
    """A start-up guard refused the run: no unit was read or run."""


class ByteFailure(RuntimeError):
    """A record cannot be read. Never a unit's failure."""


class ResumeRefused(RuntimeError):
    """A resume that section 6 does not allow, or a second driver on one store."""


class CpuAccountingError(RuntimeError):
    """The unit's ``cpu.stat`` cannot be read, so the ceiling cannot be kept."""


class PlanMismatch(RuntimeError):
    """A plan does not hold the setting that stands."""


# --------------------------------------------------------------------------
# The settings (section 2)
# --------------------------------------------------------------------------


def _weights(**changed: float) -> tuple[tuple[Mutation, float], ...]:
    return tuple(
        (op, changed.get(op.name, weight)) for op, weight in DEFAULT_MUTATION_WEIGHTS
    )


@dataclass(frozen=True)
class Setting:
    """One row of section 2's tables. ``extra_seeds`` follow the library's."""

    name: str
    gp: GPConfig = field(default_factory=GPConfig)
    extra_seeds: tuple[Structure, ...] = ()
    shifted: bool = False


#: In the order they run on a world (section 8).
SETTINGS: Final = (
    Setting("c0"),
    Setting("c1", GPConfig(population=4, offspring=4)),
    Setting("c2", GPConfig(population=16)),
    Setting("c3", GPConfig(tournament=2)),
    Setting("c4", GPConfig(tournament=5)),
    Setting("c5", GPConfig(crossover_prob=0.0)),
    Setting("c6", GPConfig(crossover_prob=0.5)),
    Setting("c7", GPConfig(mutation_weights=_weights(CHANGE_LINK=3.0))),
    Setting("c8", GPConfig(mutation_weights=_weights(CHANGE_LINK=6.0))),
    Setting("c9", GPConfig(offspring=2)),
    Setting(
        "c10",
        GPConfig(
            mutation_weights=_weights(ADD_FEATURE=4.0, MULTIPLY=2.0, WRAP_GATE=2.0)
        ),
    ),
    Setting(
        "c11",
        extra_seeds=(Structure((Excite(KernelKind.EXP, One(), ALL),), Link.SOFTPLUS),),
    ),
    Setting(
        "weak",
        GPConfig(
            mutation_weights=tuple(
                (op, 1.0 if op is Mutation.REMOVE_FEATURE else 0.0)
                for op, _ in DEFAULT_MUTATION_WEIGHTS
            )
        ),
    ),
    Setting("c0b", shifted=True),
)
SETTING_NAMES: Final = tuple(s.name for s in SETTINGS)
#: Lower number first, so the tie rule is the order of this tuple.
SELECTABLE: Final = tuple(f"c{i}" for i in range(12))
DEFAULT: Final = "c0"
WEAK: Final = "weak"
NULL: Final = "c0b"


def setting(name: str) -> Setting:
    return next(s for s in SETTINGS if s.name == name)


def shifted_seed(truth: str, seed: int) -> int:
    """``c0b``'s search seed (section 2)."""
    return (search_seed(truth, seed) + C0B_SEED_SHIFT) % 2**63


# --------------------------------------------------------------------------
# What a run is, and who may run it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    """Everything a stage 1 run reads. The root has no default: every call says
    which it means, so nothing reaches :data:`SEED_ROOT` by leaving it out."""

    truths: tuple[str, ...]
    seeds: tuple[int, ...]
    fits: int
    graded_low: int
    min_complete: int
    root: int
    resamples: int
    mode: Literal["launch", "rehearsal"]


def launch_spec(truths: Sequence[str]) -> RunSpec:
    """The text's design on the dev split's ``truths``."""
    if len(truths) != DEV_TRUTHS:
        raise GuardFailure(f"stage 1 runs all {DEV_TRUTHS} truths, not {len(truths)}")
    return RunSpec(
        truths=tuple(truths),
        seeds=STAGE1_SEEDS,
        fits=TUNE_FITS,
        graded_low=GRADED_LOW_FITS,
        min_complete=MIN_COMPLETE_WORLDS,
        root=SEED_ROOT,
        resamples=RESAMPLES,
        mode="launch",
    )


def rehearsal_spec(truths: Sequence[str], seeds: Sequence[int], fits: int) -> RunSpec:
    """A small run on seeds the launch does not use."""
    worlds = len(truths) * len(seeds)
    top, bottom = REHEARSAL_COMPLETE_SHARE
    return RunSpec(
        truths=tuple(truths),
        seeds=tuple(seeds),
        fits=fits,
        graded_low=fits * GRADED_LOW_FITS // TUNE_FITS,
        min_complete=-(-worlds * top // bottom),
        root=REHEARSAL_ROOTS[0],
        resamples=RESAMPLES,
        mode="rehearsal",
    )


def check_stage1_seeds(seeds: Sequence[int]) -> None:
    if tuple(seeds) != STAGE1_SEEDS:
        raise GuardFailure(f"stage 1 allows seeds {STAGE1_SEEDS} only, not {seeds}")


def check_spec(spec: RunSpec) -> None:
    """A launch is the text's design and nothing else; a rehearsal has the seeds and
    roots of its own lists, so nothing else reaches a stream (section 6)."""
    if spec.mode == "launch":
        check_stage1_seeds(spec.seeds)
        want = launch_spec(spec.truths)
        if spec != want:
            raise GuardFailure(f"the launch is {want}, not {spec}")
        return
    if not spec.seeds or not set(spec.seeds) <= set(REHEARSAL_SEEDS):
        raise GuardFailure(
            f"a rehearsal holds seeds {REHEARSAL_SEEDS}, not {spec.seeds}"
        )
    if spec.root not in REHEARSAL_ROOTS:
        raise GuardFailure(f"a rehearsal runs on root {REHEARSAL_ROOTS} only")
    if not 1 <= spec.graded_low < spec.fits <= TUNE_FITS:
        raise GuardFailure(f"a rehearsal's budget is off: {spec}")


def check_truths(entries: Iterable[TruthEntry]) -> None:
    """Section 11: dev truths only, an unknown split counting as outside."""
    outside = sorted(e.id for e in entries if e.split != DEV_SPLIT)
    if outside:
        raise GuardFailure(f"truths outside the {DEV_SPLIT} split: {outside}")


def world_order(truths: Iterable[str], seeds: Iterable[int]) -> tuple[World, ...]:
    """Section 8's order: by ``sha256(f"{truth}|{seed}")``, so it reads no outcome."""

    def digest(world: World) -> str:
        return hashlib.sha256(f"{world[0]}|{world[1]}".encode()).hexdigest()

    return tuple(sorted(product(truths, seeds), key=digest))


def base_config(spec: RunSpec) -> CampaignConfig:
    return CampaignConfig(
        seeds=spec.seeds,
        control_seeds=spec.seeds[:1],
        fits=spec.fits,
        baselines=(B_SYM,),
        llm_arms=(),
    )


def setting_env(env: CampaignEnvironment, s: Setting) -> CampaignEnvironment:
    return replace(env, sym_seeds=(*env.sym_seeds, *s.extra_seeds))


# --------------------------------------------------------------------------
# A unit
# --------------------------------------------------------------------------


def run_shifted_unit(job: BaselineJob, seed: int) -> UnitResult:
    """``run_baseline_job`` for one B-sym with the search seed given: the reading and
    the detail that path gives, which a test holds it to."""
    (system,) = job.systems
    start = time.perf_counter()
    mark = cpu.cpu_mark()
    config = job.config
    try:
        data = observational_data(job.truth, job.seed, job.env)
        held = held_out_dataset(job.truth.spec, job.truth.id, job.seed)
        result = BSym(
            job.env.sym_seeds,
            config.gp,
            budget=config.fits,
            seed=seed,
            fit_config=config.fit_config,
            name=system,
        ).run(data)
        run_s = time.perf_counter() - start
        reading, detail = score_submission(
            job.truth, job.seed, result.structure, result.model, held, job.env
        )
        curve = [
            [p.fits_used, p.best, p.gap] for p in efficiency_curve(result, 0.0, [held])
        ]
    except UNIT_FAILURES as error:
        return UnitResult(
            system,
            False,
            {"status": 0.0},
            {"error": type(error).__name__, "message": str(error)[:2000]},
            time.perf_counter() - start,
            cpu.cpu_since(mark),
        )
    reading["fits_used"] = float(result.fits_used)
    detail.update(result.details)
    detail.update(
        {
            "submitted": result.submitted,
            "skipped": list(result.skipped),
            "curve": curve,
            "observational_events": data.observational[0].log.n,
            "held_out_events": held.log.n,
        }
    )
    timing = {"run_s": run_s, **cpu.cpu_since(mark)}
    return UnitResult(
        system, True, reading, detail, time.perf_counter() - start, timing
    )


def unit_payload(
    setting_name: str, truth: str, seed: int, result: UnitResult
) -> Record:
    """A unit's journal record: its reading as ``float.hex`` and its detail as the
    store will hold it, so the driver can write the cell from the journal alone."""
    return {
        "setting": setting_name,
        "truth": truth,
        "seed": seed,
        "system": result.system,
        "ok": result.ok,
        "reading": {k: float(v).hex() for k, v in sorted(result.reading.items())},
        "detail": json_safe(result.detail),
    }


class RecordSink(Protocol):
    def record(self, type_: str, payload: Mapping[str, Json]) -> None: ...


class Job(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def setting(self) -> str: ...

    def run(self, sink: RecordSink) -> None: ...


@dataclass(frozen=True)
class UnitJob:
    """One unit, as its process runs it. ``shifted`` is ``c0b``'s seed."""

    id: str
    setting: str
    truth: TruthEntry
    seed: int
    env: CampaignEnvironment
    config: CampaignConfig
    shifted: int | None
    system: str = B_SYM

    def run(self, sink: RecordSink) -> None:
        job = BaselineJob(self.truth, self.seed, (self.system,), self.env, self.config)
        if self.shifted is None:
            (result,) = run_baseline_job(job)
        else:
            result = run_shifted_unit(job, self.shifted)
        payload = unit_payload(self.setting, self.truth.id, self.seed, result)
        sink.record("unit", payload)


@dataclass(frozen=True)
class PlannedUnit:
    """A unit of the plan: its address and text, and the job that fills them.
    ``store`` is the directory under the run's own, empty for the run's own store.
    ``job.id`` names the unit in the journal: it is the address, but for ``c0b``."""

    setting: str
    truth: str
    seed: int
    in_dictionary: bool
    key: ExperimentKey
    text: str
    job: Job
    system: str = B_SYM
    store: str = ""


def plan_stage1(
    spec: RunSpec, entries: Sequence[TruthEntry], env: CampaignEnvironment
) -> tuple[PlannedUnit, ...]:
    """Every unit in section 8's order: all 14 settings on a world before the next."""
    by_id = {e.id: e for e in entries}
    missing = sorted(set(spec.truths) - set(by_id))
    if missing:
        raise GuardFailure(f"no record for truths {missing}")
    check_truths(by_id[t] for t in spec.truths)
    base = base_config(spec)
    units: list[PlannedUnit] = []
    for truth_id, seed in world_order(spec.truths, spec.seeds):
        truth = by_id[truth_id]
        unit = Unit(truth_id, seed, B_SYM)
        for s in SETTINGS:
            config = replace(base, gp=s.gp)
            unit_env = setting_env(env, s)
            key = baseline_key(unit, truth, unit_env, config)
            store = C0B_STORE if s.shifted else ""
            job = UnitJob(
                f"{store}|{key.digest}" if store else str(key.digest),
                s.name,
                truth,
                seed,
                replace(unit_env, investigation=None),
                config,
                shifted_seed(truth_id, seed) if s.shifted else None,
            )
            text = system_config_text(B_SYM, unit_env, config)
            units.append(
                PlannedUnit(
                    s.name,
                    truth_id,
                    seed,
                    truth.in_dictionary,
                    key,
                    text,
                    job,
                    store=store,
                )
            )
    return tuple(units)


# --------------------------------------------------------------------------
# Readings and the table (section 3)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """What one unit gives the analysis. ``why`` is ``finite``, or why it has no finite
    reading: ``minus_inf``, ``no_certified_fit``, ``cpu_limit``, ``worker_lost`` or
    ``other_failure``. ``curve`` is (fits, held-out per event)."""

    setting: str
    truth: str
    seed: int
    why: str
    per_event: float
    curve: tuple[tuple[int, float], ...]


def read_cell(
    setting_name: str, truth: str, seed: int, cell: Mapping[str, Json]
) -> Reading:
    """A store cell as a :class:`Reading`."""
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    result = cell["detail"]["result"]
    curve = tuple((int(p[0]), float(p[2])) for p in result.get("curve", ()))
    per_event = reading.get("per_event", math.nan)
    why = "finite"
    if reading.get("status") != 1.0:
        message = str(result.get("message", ""))
        why = "no_certified_fit" if NO_PROPOSAL in message else "other_failure"
    elif per_event == -math.inf:
        why = "minus_inf"
    elif not math.isfinite(per_event):
        why = "other_failure"
    return Reading(setting_name, truth, seed, why, per_event, curve)


@dataclass(frozen=True)
class Table:
    """Section 3's values on the complete worlds, floors applied."""

    truths: tuple[str, ...]
    seeds: tuple[int, ...]
    #: (truth, seed, setting) to the floored reading, complete worlds only.
    values: Mapping[Cell, float]
    #: The world's lowest finite reading among the 14, complete worlds only.
    lowest: Mapping[World, float]
    #: ``c0``'s curve by world, complete worlds only.
    curves: Mapping[World, tuple[tuple[int, float], ...]]
    complete: tuple[World, ...]
    dropped: tuple[World, ...]
    unfinished: tuple[World, ...]
    #: setting to {kind: rows}, over every row read.
    floors: Mapping[str, Mapping[str, int]]


def build_table(
    rows: Iterable[Reading], truths: tuple[str, ...], seeds: tuple[int, ...]
) -> Table:
    """Floors (section 3: any row with no finite reading, whatever the cause, unless
    all 14 failed) and worlds (section 5, rule 2). A unit that never ran has no row,
    so its world is unfinished. Not P4's pair rule, which floors one named failure."""
    index = {(r.truth, r.seed, r.setting): r for r in rows}
    floors: dict[str, dict[str, int]] = {name: {} for name in SETTING_NAMES}
    values: dict[Cell, float] = {}
    lowest: dict[World, float] = {}
    curves: dict[World, tuple[tuple[int, float], ...]] = {}
    complete: list[World] = []
    dropped: list[World] = []
    unfinished: list[World] = []
    for world in sorted(product(truths, seeds)):
        truth, seed = world
        found = [index.get((truth, seed, name)) for name in SETTING_NAMES]
        present = [r for r in found if r is not None]
        for r in present:
            if r.why != "finite":
                kinds = floors[r.setting]
                kinds[r.why] = kinds.get(r.why, 0) + 1
        if len(present) != len(found):
            unfinished.append(world)
            continue
        finite = [r.per_event for r in present if r.why == "finite"]
        if not finite:
            dropped.append(world)
            continue
        floor = min(finite)
        complete.append(world)
        lowest[world] = floor
        for r in present:
            value = r.per_event if r.why == "finite" else floor
            values[(truth, seed, r.setting)] = value
            if r.setting == DEFAULT:
                curves[world] = r.curve
    return Table(
        truths,
        seeds,
        values,
        lowest,
        curves,
        tuple(complete),
        tuple(dropped),
        tuple(unfinished),
        floors,
    )


def _mean(values: Sequence[float]) -> float:
    return reductions.mean(np.asarray(values, dtype=np.float64))


def _truth_groups(worlds: Iterable[World]) -> dict[str, list[int]]:
    """Each truth with its seeds, sorted."""
    out: dict[str, list[int]] = {}
    for truth, seed in sorted(worlds):
        out.setdefault(truth, []).append(seed)
    return out


def truth_means(
    table: Table,
    read: Callable[[str, int], float],
    worlds: Iterable[World] | None = None,
) -> dict[str, float]:
    """A truth's value: the mean over its complete seeds (section 3)."""
    groups = _truth_groups(table.complete if worlds is None else worlds)
    return {t: _mean([read(t, s) for s in seeds]) for t, seeds in groups.items()}


def setting_means(
    table: Table, worlds: Iterable[World] | None = None
) -> dict[str, float]:
    """The selection statistic: each setting's mean over truths, on complete worlds."""
    chosen = table.complete if worlds is None else tuple(worlds)
    if not chosen:
        return {}
    out: dict[str, float] = {}
    for name in SETTING_NAMES:

        def read(truth: str, seed: int, name: str = name) -> float:
            return table.values[(truth, seed, name)]

        per_truth = truth_means(table, read, chosen)
        out[name] = _mean([per_truth[t] for t in sorted(per_truth)])
    return out


def curve_at(curve: Sequence[tuple[int, float]], fits: int) -> float | None:
    """The best-so-far reading within a budget of ``fits`` (``scoring.gap_at``'s
    rule): the last point at or before it, None before the first."""
    value: float | None = None
    for used, per_event in curve:
        if used > fits:
            break
        value = per_event
    return value


def c0_at(table: Table, truth: str, seed: int, fits: int) -> float:
    """``c0`` at ``fits`` from its own curve, and the world's floor where that reads
    ``-inf``: section 3, the same number at both ends. A curve with no point within
    ``fits`` reads the floor too, as nothing within that budget was selectable."""
    value = curve_at(table.curves[(truth, seed)], fits)
    if value is None or not math.isfinite(value):
        return table.lowest[(truth, seed)]
    return value


def h1_left(table: Table) -> tuple[World, ...]:
    """Section 3: the complete worlds where ``c0`` has no curve, which leave H1."""
    return tuple(w for w in table.complete if not table.curves[w])


def graded_rng(root: int) -> np.random.Generator:
    """Section 3's stream, on the root the caller names."""
    return np.random.default_rng(np.random.SeedSequence([root, 0]))


def graded_control(
    table: Table, fits: int, low: int, rng: Any, *, resamples: int
) -> BootstrapCI | None:
    """H1: ``c0`` at ``fits`` minus at ``low``, paired by truth, truths sorted
    (section 3). None if no world is left to read, which falsifies it."""

    def top(truth: str, seed: int) -> float:
        return c0_at(table, truth, seed, fits)

    def bottom(truth: str, seed: int) -> float:
        return c0_at(table, truth, seed, low)

    gone = set(h1_left(table))
    worlds = [w for w in table.complete if w not in gone]
    if not worlds:
        return None
    high_by_truth = truth_means(table, top, worlds)
    low_by_truth = truth_means(table, bottom, worlds)
    names = sorted(high_by_truth)
    return paired_bootstrap_ci(
        [high_by_truth[t] for t in names],
        [low_by_truth[t] for t in names],
        rng,
        resamples=resamples,
        level=LEVEL,
    )


# --------------------------------------------------------------------------
# Decision rules (section 5)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """Section 5's outcome and everything section 10 reports from it."""

    label: str
    winner: str | None
    stands: str
    means: Mapping[str, float]
    ranking: tuple[str, ...]
    c0b_rank: int | None
    h1: BootstrapCI | None
    planned: int
    complete: tuple[World, ...]
    dropped: tuple[World, ...]
    unfinished: tuple[World, ...]
    floors: Mapping[str, Mapping[str, int]]
    h1_left: tuple[World, ...]


def decide(table: Table, spec: RunSpec, end: str) -> Selection:
    """Section 5's rules, in order, the first that fits. With no complete world no
    statistic exists, so rule 1 has nothing to read and rule 2 is the one that fits.
    A run ended by a ``STOP`` file with any world short is ``INCOMPLETE`` whatever
    rule 2's count says, so nobody keeps a partial result by stopping; the CPU stop is
    the budget's, and keeps the count. ``stands`` holds ``c0`` until stage 2 decides:
    a winner is in ``winner``, and only ``final.json`` makes it stand."""
    planned = len(table.truths) * len(table.seeds)

    def outcome(
        label: str,
        winner: str | None,
        means: Mapping[str, float],
        ranking: tuple[str, ...],
        rank: int | None,
        h1: BootstrapCI | None,
    ) -> Selection:
        return Selection(
            label,
            winner,
            DEFAULT,
            means,
            ranking,
            rank,
            h1,
            planned,
            table.complete,
            table.dropped,
            table.unfinished,
            table.floors,
            h1_left(table),
        )

    if not table.complete:
        return outcome("INCOMPLETE", None, {}, (), None, None)
    means = setting_means(table)
    order = {name: i for i, name in enumerate(SELECTABLE)}
    ranking = tuple(sorted(SELECTABLE, key=lambda n: (-means[n], order[n])))
    rank = 1 + sum(means[n] > means[NULL] for n in SELECTABLE)
    h1 = graded_control(
        table,
        spec.fits,
        spec.graded_low,
        graded_rng(spec.root),
        resamples=spec.resamples,
    )
    if not means[WEAK] < means[DEFAULT]:
        return outcome("BUG_STOP", None, means, ranking, rank, h1)
    stopped_short = end == "stop" and len(table.complete) < planned
    if len(table.complete) < spec.min_complete or stopped_short:
        return outcome("INCOMPLETE", None, means, ranking, rank, h1)
    if h1 is None or not h1.low > 0.0:
        return outcome("NOT_SEPARATED", None, means, ranking, rank, h1)
    return outcome("SELECTED", ranking[0], means, ranking, rank, h1)


def label_line(selection: Selection) -> str:
    if selection.label != "SELECTED":
        return f"{selection.label}: {DEFAULT} stands"
    if selection.winner == DEFAULT:
        return f"SELECTED {DEFAULT}: tuned and the default kept"
    return f"SELECTED {selection.winner}"


def secondary(table: Table, spec: RunSpec, in_dictionary: Mapping[str, bool]) -> Record:
    """Section 4, labelled exploratory: nothing here decides anything."""
    differences: Record = {}
    for i, name in enumerate(SELECTABLE):
        if name == DEFAULT:
            continue

        def mine(truth: str, seed: int, name: str = name) -> float:
            return table.values[(truth, seed, name)]

        def base(truth: str, seed: int) -> float:
            return table.values[(truth, seed, DEFAULT)]

        x, y = truth_means(table, mine), truth_means(table, base)
        names = sorted(x)
        rng = np.random.default_rng(
            np.random.SeedSequence([spec.root, SECONDARY_STREAM + i])
        )
        ci = paired_bootstrap_ci(
            [x[t] for t in names],
            [y[t] for t in names],
            rng,
            resamples=spec.resamples,
            level=LEVEL,
        )
        differences[name] = {"mean": ci.mean, "low": ci.low, "high": ci.high}
    halves: Record = {}
    for half, wanted in (("in_dictionary", True), ("out_of_dictionary", False)):
        chosen = [w for w in table.complete if in_dictionary[w[0]] is wanted]
        halves[half] = setting_means(table, chosen)
    return {
        "label": "exploratory",
        "difference_from_c0": differences,
        "by_half": halves,
    }


def _encode(value: Json) -> Json:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, Mapping):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    return value


def selection_bytes(
    selection: Selection,
    identity: Mapping[str, Json],
    end: str,
    extra: Mapping[str, Json] | None = None,
) -> bytes:
    """``selection.json``: canonical, floats as ``float.hex``, and no clock."""
    h1 = selection.h1
    body: Record = {
        "schema": "sciagent.bsym-tune.selection/1",
        "label": selection.label,
        "winner": selection.winner,
        "stands": selection.stands,
        "means": dict(selection.means),
        "ranking": list(selection.ranking),
        "c0b_rank": selection.c0b_rank,
        "h1": None if h1 is None else dataclasses.asdict(h1),
        "worlds": {
            "planned": selection.planned,
            "complete": len(selection.complete),
            "dropped": [list(w) for w in selection.dropped],
            "unfinished": [list(w) for w in selection.unfinished],
        },
        "h1_left": [list(w) for w in selection.h1_left],
        "floors": {k: dict(v) for k, v in selection.floors.items()},
        "end": end,
        "identity": dict(identity),
        "secondary": None if extra is None else dict(extra),
    }
    return (canonical_json(_encode(body)) + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# What stands: fails closed to c0 (section 5)
# --------------------------------------------------------------------------


def selected_winner(body: Mapping[str, Json]) -> str:
    """Stage 1's winner, read from ``label`` and ``winner`` and never from ``stands``.
    Any label but ``SELECTED``, or a winner that is no candidate, leaves ``c0``. A
    winner is not what stands: that is :func:`final_setting`."""
    winner = body.get("winner")
    if body.get("label") == "SELECTED" and winner in SELECTABLE:
        return str(winner)
    return DEFAULT


def _read_body(path: Path) -> Mapping[str, Json] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return body if isinstance(body, Mapping) else None


def selected_winner_at(path: Path) -> str:
    body = _read_body(path)
    return DEFAULT if body is None else selected_winner(body)


def next_candidate(selection: Mapping[str, Json], failed: Sequence[str]) -> str | None:
    """Section 2's fallback: the next-ranked of ``c1`` to ``c11`` above ``c0``."""
    ranking = selection.get("ranking")
    if not isinstance(ranking, list):
        return None
    for name in ranking:
        if name == DEFAULT:
            return None
        if name in SELECTABLE and name not in failed:
            return str(name)
    return None


def final_setting(
    selection: Mapping[str, Json], final: Mapping[str, Json] | None
) -> str:
    """What the §6.1 commit lands (section 2). A winner other than ``c0`` stands only
    by ``final.json``, so with none ``c0`` does: a pass not finished at that commit has
    failed. A record whose passes are not the rule's, or that names anything but what
    they give, is read as no record."""
    candidate: str | None = selected_winner(selection)
    if candidate == DEFAULT or final is None:
        return DEFAULT
    passes = final.get("passes")
    if not isinstance(passes, list) or len(passes) > MAX_STAGE2_PASSES:
        return DEFAULT
    failed: list[str] = []
    for one in passes:
        if not isinstance(one, Mapping) or one.get("setting") != candidate:
            return DEFAULT
        if one.get("passed") is True:
            return str(candidate) if final.get("stands") == candidate else DEFAULT
        failed.append(str(candidate))
        candidate = next_candidate(selection, failed)
    return DEFAULT


def final_setting_at(selection: Path, final: Path) -> str:
    body = _read_body(selection)
    return DEFAULT if body is None else final_setting(body, _read_body(final))


def check_plan(
    plan: Sequence[CampaignConfig],
    env: CampaignEnvironment,
    name: str,
    library_seeds: Sequence[Structure],
) -> None:
    """Section 2: every config holds the setting's ``gp``, and the environment holds
    the library's seeds and the setting's own (``c11``)."""
    s = setting(name)
    for config in plan:
        if config.gp != s.gp:
            raise PlanMismatch(f"a config's gp is not {name}'s: {config.gp!r}")
    if tuple(env.sym_seeds) != (*library_seeds, *s.extra_seeds):
        raise PlanMismatch(f"the environment's sym_seeds are not {name}'s")


# --------------------------------------------------------------------------
# Records (copied from pilot_search_misses.py)
# --------------------------------------------------------------------------


def dumps_unit(record: Mapping[str, Json]) -> str:
    return canonical_json(json_safe(dict(record)))


def append_line(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    """Append-only, one fsynced ``write`` per record: appends from the units'
    processes and the driver do not interleave (``O_APPEND``)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: Mapping[str, Json]) -> None:
        append_line(self.path, dumps_unit(record))


def read_records(path: Path) -> list[Record]:
    """The records on disk. A last line with no newline is a torn write, not a
    record; anything else that does not parse is a failure."""
    if not path.exists():
        return []
    lines = path.read_bytes().split(b"\n")
    out: list[Record] = []
    for line in lines[:-1]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ByteFailure(f"{path}: unreadable record: {error}") from error
    return out


def repair_tail(path: Path) -> None:
    """Before a resume appends: drop a torn fragment, which was never a record."""
    if not path.exists():
        return
    data = path.read_bytes()
    keep = data.rfind(b"\n") + 1
    if keep != len(data):
        with path.open("r+b") as handle:
            handle.truncate(keep)
            os.fsync(handle.fileno())


def replace_file(path: Path, data: bytes) -> None:
    """Atomic and durable, rename included (never the journal)."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclass(frozen=True)
class OutPaths:
    """Everything a stage writes, store included, under one directory."""

    root: Path

    @property
    def journal(self) -> Path:
        return self.root / "journal.jsonl"

    @property
    def timing(self) -> Path:
        return self.root / "timing.jsonl"

    @property
    def selection(self) -> Path:
        return self.root / "selection.json"

    @property
    def cpu_report(self) -> Path:
        return self.root / "cpu_report.json"

    @property
    def cpu(self) -> Path:
        return self.root / "cpu.json"

    @property
    def stop(self) -> Path:
        return self.root / "STOP"

    @property
    def lock(self) -> Path:
        return self.root / "driver.lock"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"


def launch_paths(repo: Path) -> OutPaths:
    return OutPaths(repo / STORE_DIR)


def rehearsal_paths(repo: Path, name: str) -> OutPaths:
    return OutPaths(repo / REHEARSAL_DIR / name)


@dataclass(frozen=True)
class Stage2Paths:
    """Stage 2's store and CPU count, under the run's directory (section 6), and
    ``final.json`` beside ``selection.json``."""

    root: Path

    @property
    def store(self) -> OutPaths:
        return OutPaths(self.root / STAGE2_STORE)

    @property
    def final(self) -> Path:
        return self.root / FINAL_FILE

    @property
    def margin(self) -> Path:
        return self.root / MARGIN_FILE


def stage2_paths(repo: Path) -> Stage2Paths:
    return Stage2Paths(repo / STORE_DIR)


class Sink:
    """A unit's writer: straight to the journal, so a record outlives its process."""

    def __init__(self, journal: Path, job: str) -> None:
        self.journal = Journal(journal)
        self.job = job

    def record(self, type_: str, payload: Mapping[str, Json]) -> None:
        self.journal.append({"type": type_, "job": self.job, **payload})


def journal_end(records: Sequence[Mapping[str, Json]]) -> str:
    """How the last driver ended: "killed" if it never said. A stop stays a stop from
    the moment it is seen, through a kill and through removing ``STOP``, so that it
    cannot be undone. A bug outranks it: a bug is never resumed, stop or no stop."""
    if any(r["type"] == "run_end" and r["reason"] == "bug" for r in records):
        return "bug"
    for r in records:
        if r["type"] == "stop_seen":
            return str(r["reason"])
    last = "killed"
    for r in records:
        if r["type"] == "run_start":
            last = "killed"
        elif r["type"] == "run_end":
            last = r["reason"]
    return last


# --------------------------------------------------------------------------
# The driver: one process per unit (sections 6, 8, 9)
# --------------------------------------------------------------------------


def exit_kind(exitcode: int | None) -> str:
    """How a unit's process ended (module docstring). An uncaught exception exits 1
    through ``multiprocessing``; the kernel's limit is SIGXCPU."""
    if exitcode == 0:
        return "ok"
    if exitcode == -signal.SIGXCPU:
        return "cpu_limit"
    if exitcode == -signal.SIGTERM:
        return "terminated"
    if exitcode is not None and exitcode < 0:
        return "lost"
    return "bug"


def may_start(*, running: int, workers: int, mem_gib: float) -> bool:
    return running < workers and mem_gib >= MEM_GATE_GIB


def job_main(job: Job, journal: str, timing: str, cpu_limit_s: int) -> None:
    """A unit's process. It serves this unit and exits."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    resource.setrlimit(
        resource.RLIMIT_CPU, (cpu_limit_s, cpu_limit_s + CPU_HARD_MARGIN_S)
    )
    start = time.perf_counter()
    mark = cpu.cpu_mark()
    job.run(Sink(Path(journal), job.id))
    line = {
        "job": job.id,
        "setting": job.setting,
        "wall_s": time.perf_counter() - start,
        **cpu.cpu_since(mark),
    }
    append_line(Path(timing), json.dumps(line, sort_keys=True))


def read_usage_usec(
    proc_cgroup: Path = Path("/proc/self/cgroup"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> int:
    """The kernel's count for this process's unit (cgroup v2 ``cpu.stat``)."""
    try:
        line = proc_cgroup.read_text(encoding="utf-8").splitlines()[0]
        stat = cgroup_root / line.split("::", 1)[1].lstrip("/") / "cpu.stat"
        for row in stat.read_text(encoding="utf-8").splitlines():
            name, value = row.split()
            if name == "usage_usec":
                return int(value)
    except (OSError, IndexError, ValueError) as error:
        raise CpuAccountingError(f"cannot read the unit's cpu.stat: {error}") from error
    raise CpuAccountingError(f"no usage_usec in {stat}")


def read_mem_available_gib(path: Path = Path("/proc/meminfo")) -> float:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 2**20
    raise CpuAccountingError(f"no MemAvailable in {path}")


def script_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def check_script(path: Path, script_sha: str | None = None) -> None:
    """The code that wrote ``cpu.json`` is the code running now. A change to the
    script is an amendment (section 6), with a new store."""
    if not path.exists():
        return
    recorded = json.loads(path.read_text(encoding="utf-8")).get("script_sha256")
    current = script_sha or script_sha256()
    if recorded != current:
        raise ResumeRefused(
            f"script hash {current} is not the first start's {recorded}"
        )


@dataclass
class CpuLedger:
    """``cpu.json``: earlier units plus this one, kept current during a run, with the
    hash of the script the first start ran. A total at or over the stop is no
    refusal here: it stops dispatch, and section 5 then applies."""

    path: Path
    script_sha: str
    earlier_usec: int
    current_usec: int

    @classmethod
    def load(cls, path: Path, script_sha: str | None = None) -> CpuLedger:
        """The ledger as a new unit would start it, written nowhere."""
        check_script(path, script_sha)
        data: Mapping[str, Json] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path,
            script_sha or script_sha256(),
            data.get("earlier_usec", 0) + data.get("current_usec", 0),
            0,
        )

    @classmethod
    def begin(cls, path: Path, script_sha: str | None = None) -> CpuLedger:
        """Start a unit."""
        ledger = cls.load(path, script_sha)
        ledger.save()
        return ledger

    @property
    def total_usec(self) -> int:
        return self.earlier_usec + self.current_usec

    def update(self, *, usage_usec: int) -> None:
        """One reading: written and fsynced, so a SIGKILL loses at most the minute
        since the last one."""
        self.current_usec = usage_usec
        self.save()

    def save(self) -> None:
        body = {
            "script_sha256": self.script_sha,
            "earlier_usec": self.earlier_usec,
            "current_usec": self.current_usec,
        }
        replace_file(self.path, (json.dumps(body, sort_keys=True) + "\n").encode())


def check_start(paths: OutPaths) -> None:
    """Section 6: the same command resumes, except after a bug, which is fixed by
    amendment; and a run's records are not resumed without its CPU count."""
    records = read_records(paths.journal)
    if records and journal_end(records) == "bug":
        raise ResumeRefused("a run that ended 'bug' is not resumed: amend it")
    if records and not paths.cpu.exists():
        raise ResumeRefused(f"{paths.root} holds records and no CPU count")


@contextmanager
def driver_lock(paths: OutPaths) -> Iterator[None]:
    """One driver per store. ``flock`` goes with the process, so a SIGKILL leaves
    nothing to clear."""
    paths.root.mkdir(parents=True, exist_ok=True)
    with paths.lock.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ResumeRefused(f"a driver holds {paths.lock}") from error
        yield


@dataclass
class _Running:
    job: Job
    process: Any


class Driver:
    """Runs units, each in its own process (sections 6, 8, 9).

    The driver owns no result: it reads a unit's exit and the journal, and adds
    ``job_lost``, ``unit_failed``, ``stop_seen``, ``run_start`` and ``run_end``.
    ``unit_failed`` ends a unit: its ``reason`` is ``cpu_limit`` or ``worker_lost``, and
    a resume does not rerun it. ``stop_seen`` is written the first time a stop is seen,
    before the units in flight drain, and :func:`journal_end` latches on it. ``log``
    receives counts and CPU and nothing else (section 9). ``on_unit``
    gets each unit record of ``jobs`` once, on the driver's own thread: a journal may
    hold other drivers' units, as stage 2's does across its blocks.
    """

    def __init__(
        self,
        paths: OutPaths,
        jobs: tuple[Job, ...],
        *,
        stop_s: float,
        cpu_limit_s: int,
        log: Callable[[str], None],
        workers: int,
        read_usage_usec: Callable[[], int] = read_usage_usec,
        read_mem_gib: Callable[[], float] = read_mem_available_gib,
        poll_s: float = 1.0,
        cpu_poll_s: float = 60.0,
        print_s: float = 600.0,
        start_method: str = START_METHOD,
        on_unit: Callable[[Record], None] | None = None,
        world_of: Mapping[str, World] | None = None,
    ) -> None:
        self.paths = paths
        self.jobs = jobs
        self.stop_s = stop_s
        self.cpu_limit_s = cpu_limit_s
        self.log = log
        self.workers = workers
        self.read_usage_usec = read_usage_usec
        self.read_mem_gib = read_mem_gib
        self.poll_s = poll_s
        self.cpu_poll_s = cpu_poll_s
        self.print_s = print_s
        self.on_unit = on_unit
        self.world_of = dict(world_of or {})
        self.context: Any = multiprocessing.get_context(start_method)
        self.journal = Journal(paths.journal)
        self.finished: set[str] = set()
        self.stored: set[str] = set()
        self.lost: dict[str, int] = {}
        self.terminating = False
        self.latched: str | None = None
        self.ledger: CpuLedger | None = None
        self.last_cpu = -math.inf
        self.last_print = -math.inf

    def run(self) -> str:
        """The units, in order; returns how the run ended."""
        self.paths.root.mkdir(parents=True, exist_ok=True)
        repair_tail(self.paths.journal)
        check_start(self.paths)
        ended = journal_end(read_records(self.paths.journal))
        self.latched = ended if ended in LATCHED_ENDS else None
        self.ledger = CpuLedger.begin(self.paths.cpu)
        self.journal.append({"type": "run_start"})
        self._ingest()
        previous = None
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, self._on_term)
        try:
            self._tick_cpu(force=True)
            reason = self._run_units()
            self._tick_cpu(force=True)
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        self._print(force=True)
        self.journal.append({"type": "run_end", "reason": reason})
        return reason

    def _on_term(self, signum: int, frame: FrameType | None) -> None:
        self.terminating = True

    def _ingest(self) -> None:
        self.finished, self.lost = set(), {}
        mine = {j.id for j in self.jobs}
        for r in read_records(self.paths.journal):
            kind = r["type"]
            if kind == "unit":
                self.finished.add(r["job"])
                if r["job"] in mine and r["job"] not in self.stored:
                    self.stored.add(r["job"])
                    if self.on_unit is not None:
                        self.on_unit(r)
            elif kind == "unit_failed":
                self.finished.add(r["job"])
            elif kind == "job_lost":
                self.lost[r["job"]] = self.lost.get(r["job"], 0) + 1

    def _tick_cpu(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_cpu < self.cpu_poll_s:
            return
        self.last_cpu = now
        assert self.ledger is not None
        self.ledger.update(usage_usec=self.read_usage_usec())

    def _print(self, *, force: bool = False) -> None:
        """Counts and CPU only (section 9)."""
        now = time.monotonic()
        if not force and now - self.last_print < self.print_s:
            return
        self.last_print = now
        assert self.ledger is not None
        done = sum(j.id in self.finished for j in self.jobs)
        worlds: dict[World, bool] = {}
        for j in self.jobs:
            world = self.world_of.get(j.id)
            if world is not None:
                worlds[world] = worlds.get(world, True) and j.id in self.finished
        hours = self.ledger.total_usec / 3.6e9
        self.log(
            f"units {done}/{len(self.jobs)} "
            f"worlds {sum(worlds.values())}/{len(worlds)} cpu_hours {hours:.2f}"
        )

    def _stop_reason(self) -> str | None:
        assert self.ledger is not None
        if self.terminating:
            return "terminated"
        if self.latched is not None:
            return self.latched
        reason = None
        if self.paths.stop.exists():
            reason = "stop"
        elif self.ledger.total_usec >= self.stop_s * 10**6:
            reason = "cpu_stop"
        if reason is not None:
            self.journal.append({"type": "stop_seen", "reason": reason})
            self.latched = reason
        return reason

    def _run_units(self) -> str:
        queue = deque(j for j in self.jobs if j.id not in self.finished)
        retry: deque[Job] = deque()
        running: dict[str, _Running] = {}
        while True:
            failure = self._reap(running, retry)
            if failure is not None:
                self._kill(running)
                return failure
            self._tick_cpu()
            self._print()
            reason = self._stop_reason()
            if self.terminating:
                self._kill(running)
                return "terminated"
            if reason is not None:
                return self._drain(running, reason)
            if not queue and not running:
                queue, retry = retry, deque()  # after the run's other units
            if not queue and not running:
                return "complete"
            if queue and may_start(
                running=len(running), workers=self.workers, mem_gib=self.read_mem_gib()
            ):
                job = queue.popleft()
                running[job.id] = self._start(job)
            time.sleep(self.poll_s)

    def _start(self, job: Job) -> _Running:
        process = self.context.Process(
            target=job_main,
            args=(
                job,
                str(self.paths.journal),
                str(self.paths.timing),
                self.cpu_limit_s,
            ),
        )
        process.start()
        return _Running(job, process)

    def _reap(self, running: dict[str, _Running], retry: deque[Job]) -> str | None:
        """Read the exits. Returns "bug" if the run must stop."""
        for job_id, handle in sorted(running.items()):
            process = handle.process
            if process.is_alive():
                continue
            process.join()
            del running[job_id]
            self._ingest()
            if job_id in self.finished:
                continue
            kind = exit_kind(process.exitcode)
            if kind == "bug":
                return "bug"
            if kind == "cpu_limit":
                self._fail(handle.job, "cpu_limit")
            elif kind == "terminated" and self.terminating:
                continue  # the unit is stopping: a resume runs this one again
            elif self.lost.get(job_id, 0) >= 1:
                self._fail(handle.job, "worker_lost")
            else:
                self.journal.append({"type": "job_lost", "job": job_id})
                self.lost[job_id] = self.lost.get(job_id, 0) + 1
                retry.append(handle.job)
        return None

    def _fail(self, job: Job, reason: str) -> None:
        """Section 6: journalled, so a resume does not rerun it."""
        record = {
            "type": "unit_failed",
            "job": job.id,
            "setting": job.setting,
            "reason": reason,
        }
        self.journal.append(record)
        self.finished.add(job.id)

    def _drain(self, running: dict[str, _Running], reason: str) -> str:
        """Let the units in flight finish. A first loss now has no row: nothing
        dispatches after a stop, so its world is unfinished."""
        ignored: deque[Job] = deque()
        while running:
            failure = self._reap(running, ignored)
            if failure is not None:
                self._kill(running)
                return failure
            if self.terminating:
                self._kill(running)
                return "terminated"
            self._tick_cpu()
            self._print()
            time.sleep(self.poll_s)
        return reason

    def _kill(self, running: dict[str, _Running]) -> None:
        for handle in running.values():
            handle.process.terminate()
        for handle in running.values():
            handle.process.join()
        running.clear()


# --------------------------------------------------------------------------
# Stage 1
# --------------------------------------------------------------------------


def check_no_selection(paths: OutPaths) -> None:
    """Section 6: there is one run of stage 1."""
    if paths.selection.exists():
        raise GuardFailure(f"{paths.selection} exists: there is one run")


def _store_root(paths: OutPaths, store: str) -> Path:
    return paths.root / store if store else paths.root


def check_stores(paths: OutPaths, plan: Iterable[PlannedUnit]) -> None:
    """:func:`check_store` on every store the plan writes, each on its own cells."""
    held: dict[str, set[str]] = {}
    for u in plan:
        held.setdefault(u.store, set()).add(str(u.key.digest))
    for store in sorted(held):
        check_store(_store_root(paths, store), held[store])


def check_store(root: Path, planned: Iterable[str]) -> None:
    """Section 6: a store holding any cell the plan does not name is another run's.
    The detail files count too, so a cell its ledger never committed is seen."""
    held: set[str] = set()
    details = root / DETAILS_DIR
    if details.is_dir():
        held |= {p.stem for p in details.glob("*.json")}
    if (root / LEDGER_FILE).is_file():
        with ResultStore(root, read_only=True) as store:
            held |= {str(e.digest) for e in store.ledger.entries()}
    other = sorted(held - set(planned))
    if other:
        raise GuardFailure(
            f"{root} holds {len(other)} cells of another run, such as {other[0]}: "
            "the store is this other run's"
        )


def check_manifest(paths: OutPaths, body: Mapping[str, Json]) -> None:
    """The run a store belongs to: fixed at the first start, the same at a resume."""
    text = canonical_json(_encode(dict(body))) + "\n"
    if not paths.manifest.exists():
        paths.root.mkdir(parents=True, exist_ok=True)
        replace_file(paths.manifest, text.encode("utf-8"))
        return
    if paths.manifest.read_text(encoding="utf-8") != text:
        raise ResumeRefused(f"{paths.manifest} names another run")


def load_readings(
    paths: OutPaths, plan: Sequence[PlannedUnit], *, by_system: bool = False
) -> list[Reading]:
    """The planned units with a row or a journalled failure; the rest have neither.
    A reading is named by its setting, or in stage 2 by its system."""
    failed = {
        r["job"]: r["reason"]
        for r in read_records(paths.journal)
        if r["type"] == "unit_failed"
    }
    out: list[Reading] = []
    with ExitStack() as stack:
        stores = {
            name: stack.enter_context(
                ResultStore(_store_root(paths, name), read_only=True)
            )
            for name in sorted({u.store for u in plan})
        }
        for u in plan:
            name = u.system if by_system else u.setting
            store = stores[u.store]
            if store.done(u.key):
                out.append(read_cell(name, u.truth, u.seed, store.cell(u.key.digest)))
            elif u.job.id in failed:
                why = failed[u.job.id]
                out.append(Reading(name, u.truth, u.seed, why, math.nan, ()))
    return out


def cpu_by_setting(
    timing: Path, failed: Sequence[Mapping[str, Json]], limit_s: int
) -> dict[str, float]:
    """Section 4: a unit's own CPU, and a killed unit's limit, which it spent."""
    parts: dict[str, list[float]] = {}
    if timing.exists():
        for row in timing.read_text(encoding="utf-8").splitlines():
            line = json.loads(row)
            parts.setdefault(line["setting"], []).append(line.get("cpu_s") or 0.0)
    for record in failed:
        if record["reason"] == "cpu_limit":
            parts.setdefault(record["setting"], []).append(float(limit_s))
    return {name: math.fsum(parts[name]) for name in sorted(parts)}


@dataclass(frozen=True)
class Outcome:
    end: str
    selection: Selection | None


def finalise(
    spec: RunSpec,
    plan: Sequence[PlannedUnit],
    paths: OutPaths,
    identity: Mapping[str, Json],
    end: str,
    cpu_limit_s: int,
) -> Selection:
    """Section 5, from the store and the journal. ``selection.json`` comes last."""
    table = build_table(load_readings(paths, plan), spec.truths, spec.seeds)
    selection = decide(table, spec, end)
    halves = {u.truth: u.in_dictionary for u in plan}
    extra = secondary(table, spec, halves) if table.complete else None
    records = read_records(paths.journal)
    failed = [r for r in records if r["type"] == "unit_failed"]
    report = {
        "cpu_hours": CpuLedger.load(paths.cpu).total_usec / 3.6e9,
        "cpu_s_by_setting": cpu_by_setting(paths.timing, failed, cpu_limit_s),
        "units_planned": len(plan),
        "units_killed": sum(r["reason"] == "cpu_limit" for r in failed),
        "units_lost": sum(r["reason"] == "worker_lost" for r in failed),
    }
    replace_file(paths.cpu_report, (json.dumps(report, sort_keys=True) + "\n").encode())
    data = selection_bytes(selection, identity, end, extra)
    replace_file(paths.selection, data)
    return selection


def _run_pass(
    plan: Sequence[PlannedUnit],
    paths: OutPaths,
    *,
    stop_s: float,
    cpu_limit_s: int,
    log: Callable[[str], None],
    **driver: Any,
) -> str:
    """The units of a plan, under the caller's lock and guards; returns how it ended.
    Cells are in ``Campaign._record``'s shape (section 2)."""
    by_id = {u.job.id: u for u in plan}
    with ExitStack() as stack:
        stores: dict[str, ResultStore] = {}
        for name in sorted({u.store for u in plan}):
            stores[name] = stack.enter_context(ResultStore(_store_root(paths, name)))
            stores[name].recover()

        def put(record: Record) -> None:
            unit = by_id[record["job"]]
            store = stores[unit.store]
            if store.done(unit.key):
                return
            reading = {k: float.fromhex(v) for k, v in record["reading"].items()}
            store.put(
                unit.key,
                reading,
                {
                    "unit": {
                        "truth": unit.truth,
                        "seed": unit.seed,
                        "system": unit.system,
                        "kind": "baseline",
                    },
                    "result": record["detail"],
                    "system_config": unit.text,
                },
            )

        run = Driver(
            paths,
            tuple(u.job for u in plan),
            stop_s=stop_s,
            cpu_limit_s=cpu_limit_s,
            log=log,
            on_unit=put,
            world_of={u.job.id: (u.truth, u.seed) for u in plan},
            **driver,
        )
        end = run.run()
    log(f"run ended: {end}")
    return end


def run_stage1(
    spec: RunSpec,
    plan: Sequence[PlannedUnit],
    paths: OutPaths,
    identity: Mapping[str, Json],
    *,
    stop_s: float,
    cpu_limit_s: int,
    log: Callable[[str], None],
    check_code: Callable[[], None] | None = None,
    **driver: Any,
) -> Outcome:
    """The run, then section 5. A stop writes the label; a kill or a bug does not."""
    check_no_selection(paths)
    manifest = {"identity": identity, "spec": dataclasses.asdict(spec)}
    with driver_lock(paths):
        check_stores(paths, plan)
        check_manifest(paths, manifest)
        end = _run_pass(
            plan,
            paths,
            stop_s=stop_s,
            cpu_limit_s=cpu_limit_s,
            log=log,
            **driver,
        )
        if end not in FINAL_ENDS:
            return Outcome(end, None)
        if check_code is not None:
            check_code()
        selection = finalise(spec, plan, paths, identity, end, cpu_limit_s)
    log(label_line(selection))
    return Outcome(end, selection)


# --------------------------------------------------------------------------
# Stage 2 (section 2): the candidate where it gates everything
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage2Spec:
    """What stage 2 runs on. As :class:`RunSpec`, the root has no default."""

    truths: tuple[str, ...]
    seeds: tuple[int, ...]
    control_truths: tuple[str, ...]
    fits: int
    root: int
    resamples: int


def stage2_launch_spec(truths: Sequence[str]) -> Stage2Spec:
    if len(truths) != DEV_TRUTHS:
        raise GuardFailure(f"stage 2 runs all {DEV_TRUTHS} truths, not {len(truths)}")
    return Stage2Spec(
        truths=tuple(truths),
        seeds=STAGE2_SEEDS,
        control_truths=STAGE2_CONTROL_TRUTHS,
        fits=TUNE_FITS,
        root=SEED_ROOT,
        resamples=RESAMPLES,
    )


def _stage2_planner(
    name: str,
    spec: Stage2Spec,
    entries: Sequence[TruthEntry],
    env: CampaignEnvironment,
) -> Callable[[str, int, str], PlannedUnit]:
    """Builds a unit under candidate ``name``. The units go by the framework's path,
    so a candidate's ``gp`` and seeds are in every address."""
    if name not in SELECTABLE or name == DEFAULT:
        raise GuardFailure(f"no stage 2 for {name}: it is for a candidate above c0")
    by_id = {e.id: e for e in entries}
    missing = sorted((set(spec.truths) | set(spec.control_truths)) - set(by_id))
    outside = sorted(set(spec.control_truths) - set(spec.truths))
    if missing or outside:
        raise GuardFailure(f"no record for {missing}; outside the truths: {outside}")
    check_truths(by_id[t] for t in spec.truths)
    s = setting(name)
    control_seed = spec.seeds[0]
    config = CampaignConfig(
        seeds=spec.seeds,
        control_seeds=(control_seed,),
        fits=spec.fits,
        baselines=(B_SYM, PLANTED, B_SYM_10F),
        llm_arms=(),
        gp=s.gp,
    )
    unit_env = setting_env(env, s)
    job_env = replace(unit_env, investigation=None)

    def planned(truth_id: str, seed: int, system: str) -> PlannedUnit:
        truth = by_id[truth_id]
        key = baseline_key(Unit(truth_id, seed, system), truth, unit_env, config)
        job = UnitJob(str(key.digest), name, truth, seed, job_env, config, None, system)
        text = system_config_text(system, unit_env, config)
        return PlannedUnit(
            name, truth_id, seed, truth.in_dictionary, key, text, job, system
        )

    return planned


def plan_stage2(
    name: str,
    spec: Stage2Spec,
    entries: Sequence[TruthEntry],
    env: CampaignEnvironment,
) -> tuple[PlannedUnit, ...]:
    """A pass under candidate ``name``, in section 2's order, by sorted truth and seed:
    B-sym@10F at the first seed on the ten truths, then planted-hint on all."""
    planned = _stage2_planner(name, spec, entries, env)
    tenfold = [
        planned(t, spec.seeds[0], B_SYM_10F) for t in sorted(spec.control_truths)
    ]
    worlds = sorted(product(spec.truths, spec.seeds))
    return (*tenfold, *(planned(t, s, PLANTED) for t, s in worlds))


def plan_margin(
    name: str,
    spec: Stage2Spec,
    entries: Sequence[TruthEntry],
    env: CampaignEnvironment,
) -> tuple[PlannedUnit, ...]:
    """B-sym under the setting that stands, by sorted truth and seed: no rule reads
    it (section 2)."""
    planned = _stage2_planner(name, spec, entries, env)
    return tuple(
        planned(t, s, B_SYM) for t, s in sorted(product(spec.truths, spec.seeds))
    )


@dataclass(frozen=True)
class P3Table:
    """P3's stored rows: (truth, seed, system) to ``per_event``, None for a failed
    unit, which ``analysis.row_from_cell`` gives no reading either."""

    rows: Mapping[tuple[str, int, str], float | None]


def p3_table_bytes(rows: Iterable[Mapping[str, Json]]) -> bytes:
    """The table's file: canonical, floats as ``float.hex``."""
    ordered = sorted(rows, key=lambda r: (r["truth"], r["seed"], r["system"]))
    body = {
        "schema": "sciagent.bsym-tune.p3-table/1",
        "commit": P3_TABLE_COMMIT,
        "seeds": list(P3_TABLE_SEEDS),
        "control_truths": P3_TABLE_CONTROL_TRUTHS,
        "rows": ordered,
    }
    return (canonical_json(_encode(body)) + "\n").encode("utf-8")


def load_p3_table(path: Path, sha256: str) -> P3Table:
    """The table under its pin: section 2 has its sha256 in the script."""
    if not sha256:
        raise GuardFailure("P3_TABLE_SHA256 is empty: the table is not pinned")
    if not path.is_file():
        raise GuardFailure(f"no table at {path}")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise GuardFailure(f"table hash {digest} is not the pin {sha256}")
    rows: dict[tuple[str, int, str], float | None] = {}
    for r in json.loads(data)["rows"]:
        value = r["per_event"]
        key = (r["truth"], r["seed"], r["system"])
        rows[key] = None if value is None else float.fromhex(value)
    return P3Table(rows)


def export_p3_rows(store_root: Path) -> list[Record]:
    """The rows of :data:`P3_SYSTEMS` in a store, read-only and by enumeration: F5
    moved these cells' addresses, so none is computed. Two cells for one row refuse."""
    rows: dict[tuple[str, int, str], Record] = {}
    with ResultStore(store_root, read_only=True) as store:
        for cell in store.cells():
            fields = cell["key"]["config"]
            system = fields.get("system")
            if fields.get("kind") != "baseline" or system not in P3_SYSTEMS:
                continue
            key = (fields["truth"], int(cell["key"]["seed"]), system)
            if key in rows:
                raise GuardFailure(f"two cells for {key}: the table would pick one")
            reading = cell["reading"]
            scored = float.fromhex(reading["status"]) == 1.0
            value = float.fromhex(reading["per_event"]) if scored else None
            rows[key] = {
                "truth": key[0],
                "seed": key[1],
                "system": system,
                "per_event": value,
            }
    return [rows[k] for k in sorted(rows)]


def stage2_rng(root: int, p: int) -> np.random.Generator:
    """Control 2's stream in pass ``p``, from 1 (:data:`STAGE2_STREAM`)."""
    return np.random.default_rng(np.random.SeedSequence([root, STAGE2_STREAM + p]))


def margin_of(rows: Iterable[Reading], table: P3Table, spec: Stage2Spec) -> Record:
    """Section 2's margin: the standing setting's B-sym over ``c0``'s stored rows, per
    truth then over truths. No rule reads it. A row with no finite reading takes the
    lowest finite reading its world has among these rows and stored B-rand's."""
    readings = {(r.truth, r.seed): r for r in rows}
    worlds = sorted(product(spec.truths, spec.seeds))
    floored = 0
    by_truth: dict[str, list[float]] = {}
    for truth, seed in worlds:
        mine = readings.get((truth, seed))
        pair = [
            None if mine is None else mine.per_event,
            table.rows.get((truth, seed, B_SYM)),
        ]
        rand = table.rows.get((truth, seed, B_RAND))
        finite = [v for v in (*pair, rand) if v is not None and math.isfinite(v)]
        if not finite:
            continue
        lowest = min(finite)
        one, two = (v if v is not None and math.isfinite(v) else lowest for v in pair)
        floored += sum(v is None or not math.isfinite(v) for v in pair)
        by_truth.setdefault(truth, []).append(one - two)
    means = [_mean(by_truth[t]) for t in sorted(by_truth)]
    finished = all(w in readings for w in worlds)
    return {
        "mean": _mean(means) if means and finished else None,
        "floored": floored,
        "finished": finished,
    }


def _gap(table: P3Table, truth: str, seed: int, value: float) -> float:
    """Section 2: ``per_event`` minus the stored ORACLE's, unfloored."""
    oracle = table.rows.get((truth, seed, ORACLE))
    if oracle is None or not math.isfinite(oracle):
        raise ByteFailure(f"the table has no finite ORACLE on {truth} s{seed}")
    return value - oracle


def _stored_rows(
    table: P3Table, in_dictionary: Mapping[str, bool]
) -> list[gonogo.ScoreRow]:
    """P3's ORACLE, B-lib and B-rand as ``ScoreRow``s on these truths."""
    out: list[gonogo.ScoreRow] = []
    for (truth, seed, system), value in sorted(table.rows.items()):
        if value is None or truth not in in_dictionary or system == B_SYM:
            continue
        gap = _gap(table, truth, seed, value)
        out.append(
            gonogo.ScoreRow(truth, seed, system, gap, None, False, in_dictionary[truth])
        )
    return out


def check_retained(
    table: P3Table, spec: Stage2Spec, in_dictionary: Mapping[str, bool]
) -> None:
    """Section 2: P3's control 1 kept every truth, and any other count is a bug."""
    checks = gonogo.oracle_beats_lib(_stored_rows(table, in_dictionary))
    kept = sorted(c.truth for c in checks if c.passed)
    if kept != sorted(spec.truths):
        raise ByteFailure(
            f"control 1 on the table keeps {len(kept)} of {len(spec.truths)} truths: "
            "any other count than all of them is a bug"
        )


def read_pass(
    name: str,
    rows: Iterable[Reading],
    table: P3Table,
    spec: Stage2Spec,
    in_dictionary: Mapping[str, bool],
    p: int,
) -> Record:
    """Controls 2 and 3 as ``scoring/gonogo.py`` has them, on this pass's rows beside
    P3's. A unit killed, failed or without a reading has gap ``-inf``, so it stays in
    the rank and the median. A unit never run means the pass did not finish, and a
    pass not finished has failed (sections 2, 5)."""
    readings = {(r.truth, r.seed, r.setting): r for r in rows}
    wanted = [(t, spec.seeds[0], B_SYM_10F) for t in sorted(spec.control_truths)]
    wanted += [(t, s, PLANTED) for t, s in sorted(product(spec.truths, spec.seeds))]
    finished = all(key in readings for key in wanted)
    both = _stored_rows(table, in_dictionary)
    for truth, seed, system in wanted:
        one = readings.get((truth, seed, system))
        gap = STAGE2_UNSCORED_GAP
        if one is not None and one.why in ("finite", "minus_inf"):
            gap = _gap(table, truth, seed, one.per_event)
        both.append(
            gonogo.ScoreRow(truth, seed, system, gap, None, False, in_dictionary[truth])
        )
    planted = gonogo.planted_beats_rand(
        both,
        sorted(spec.truths),
        rng=stage2_rng(spec.root, p),
        resamples=spec.resamples,
    )
    check = gonogo.bsym_approaches_oracle(both, sorted(spec.control_truths))
    return {
        "setting": name,
        "finished": finished,
        "control_2": {
            "passed": planted.passed,
            "truths": planted.n,
            "wins": planted.wins,
            "ties": planted.ties,
            "losses": planted.losses,
            "p_value": planted.p_value,
            "median": planted.median,
            "low": planted.median_ci.low,
            "high": planted.median_ci.high,
        },
        "control_3": {
            "passed": check.passed,
            "truths": len(spec.control_truths),
            "median_gap": check.median_gap,
            "tolerance": check.tolerance,
        },
        "passed": finished and planted.passed and check.passed,
    }


def check_same_commit(
    selection: Mapping[str, Json], identity: Mapping[str, Json]
) -> None:
    """Section 6: stage 2 runs at the commit that selected."""
    recorded = selection.get("identity")
    fingerprint = recorded.get("fingerprint") if isinstance(recorded, Mapping) else None
    if not fingerprint or fingerprint != identity.get("fingerprint"):
        raise GuardFailure(
            f"selection.json is from commit {fingerprint}, "
            f"not this one {identity.get('fingerprint')}"
        )


def _candidates(selection: Mapping[str, Json]) -> tuple[str, ...]:
    """The winner, and the next rank if one stands above ``c0`` (section 2)."""
    winner = selected_winner(selection)
    second = next_candidate(selection, (winner,))
    return (winner,) if second is None else (winner, second)


def check_stage2_store(
    spec: Stage2Spec,
    selection: Mapping[str, Json],
    plan_of: Callable[[str], Sequence[PlannedUnit]],
    margin_plan_of: Callable[[str], Sequence[PlannedUnit]],
    store: OutPaths,
    identity: Mapping[str, Json],
) -> None:
    """Section 6: stage 2's store holds this run's cells and no other's, whichever of
    its steps is starting."""
    units = [
        u
        for name in _candidates(selection)
        for u in (*plan_of(name), *margin_plan_of(name))
    ]
    check_stores(store, units)
    check_manifest(store, {"identity": identity, "spec": dataclasses.asdict(spec)})


def run_stage2(
    spec: Stage2Spec,
    selection: Mapping[str, Json],
    plan_of: Callable[[str], Sequence[PlannedUnit]],
    margin_plan_of: Callable[[str], Sequence[PlannedUnit]],
    table: P3Table,
    in_dictionary: Mapping[str, bool],
    paths: Stage2Paths,
    identity: Mapping[str, Json],
    *,
    log: Callable[[str], None],
    read_usage_usec: Callable[[], int] = read_usage_usec,
    check_code: Callable[[], None] | None = None,
    **driver: Any,
) -> str | None:
    """The winner's pass and the next rank's if that fails (section 2), ending in
    ``final.json``, which nothing later changes. Returns what stands, or None if a
    kill or a bug left no result. The same command resumes: a finished pass is read
    again from the store and runs nothing. After a stop the passes still go by in
    order and dispatch nothing, so a pass unfinished at it has failed, and so has the
    one after it."""
    if paths.final.exists():
        raise GuardFailure(f"{paths.final} exists: there is one run")
    winner = selected_winner(selection)
    if winner == DEFAULT:
        raise GuardFailure("no stage 2: only a SELECTED winner other than c0 takes it")
    check_same_commit(selection, identity)
    check_retained(table, spec, in_dictionary)
    candidates = _candidates(selection)
    plans = {name: tuple(plan_of(name)) for name in candidates}
    store = paths.store
    # The kernel's count for a systemd unit only grows, and each block starts its own
    # entry in the ledger: each reads from where the last ended, so none counts twice.
    base = 0

    def block(units: Sequence[PlannedUnit]) -> str:
        nonlocal base
        start = base

        def usage() -> int:
            return read_usage_usec() - start

        end = _run_pass(
            units,
            store,
            stop_s=STAGE2.stop_s,
            cpu_limit_s=STAGE2.limit_s,
            log=log,
            read_usage_usec=usage,
            **driver,
        )
        base = read_usage_usec()
        return end

    results: list[Record] = []
    final = DEFAULT
    with driver_lock(store):
        check_stage2_store(spec, selection, plan_of, margin_plan_of, store, identity)
        end = "complete"
        for p, candidate in enumerate(candidates, start=1):
            end = block(plans[candidate])
            if end not in FINAL_ENDS:
                return None
            rows = load_readings(store, plans[candidate], by_system=True)
            one = read_pass(candidate, rows, table, spec, in_dictionary, p)
            results.append(one)
            if one["passed"]:
                final = candidate
                break
    if check_code is not None:
        check_code()
    body = {
        "schema": "sciagent.bsym-tune.final/1",
        "winner": winner,
        "passes": results,
        "stands": final,
        "end": end,
        "identity": dict(identity),
    }
    replace_file(paths.final, (canonical_json(_encode(body)) + "\n").encode("utf-8"))
    log(f"stage 2: {final} stands")
    return final


def _standing(selection: Mapping[str, Json], paths: Stage2Paths) -> str:
    """What ``final.json`` makes stand; ``c0`` if it is absent or off the rule."""
    body = _read_body(paths.final)
    return DEFAULT if body is None else final_setting(selection, body)


def run_margin(
    spec: Stage2Spec,
    selection: Mapping[str, Json],
    plan_of: Callable[[str], Sequence[PlannedUnit]],
    margin_plan_of: Callable[[str], Sequence[PlannedUnit]],
    table: P3Table,
    in_dictionary: Mapping[str, bool],
    paths: Stage2Paths,
    identity: Mapping[str, Json],
    *,
    log: Callable[[str], None],
    read_usage_usec: Callable[[], int] = read_usage_usec,
    check_code: Callable[[], None] | None = None,
    base: int = 0,
    **driver: Any,
) -> Record | None:
    """B-sym for the setting that stands, on stage 2's store and CPU count, as its own
    step. It reads ``final.json`` and never writes it, and no rule reads what it
    writes. ``margin.json`` is its latch: a stop or the CPU limit writes it with
    ``finished`` false, and a kill or a bug writes nothing, so the same command
    resumes. ``base`` is the kernel's count where the passes left it, in the same
    systemd unit. Returns the record, or None if nothing was written."""
    if paths.margin.exists():
        raise GuardFailure(f"{paths.margin} exists: there is one run")
    name = _standing(selection, paths)
    if name == DEFAULT:
        raise GuardFailure("no margin: only a setting that stands over c0 takes it")
    check_same_commit(selection, identity)
    units = tuple(margin_plan_of(name))
    store = paths.store

    def usage() -> int:
        return read_usage_usec() - base

    with driver_lock(store):
        check_stage2_store(spec, selection, plan_of, margin_plan_of, store, identity)
        end = _run_pass(
            units,
            store,
            stop_s=STAGE2.stop_s,
            cpu_limit_s=STAGE2.limit_s,
            log=log,
            read_usage_usec=usage,
            **driver,
        )
        if end not in FINAL_ENDS:
            return None
        rows = load_readings(store, units, by_system=True)
    if check_code is not None:
        check_code()
    body = {
        "schema": "sciagent.bsym-tune.margin/1",
        "setting": name,
        **margin_of(rows, table, spec),
        "end": end,
        "identity": dict(identity),
    }
    replace_file(paths.margin, (canonical_json(_encode(body)) + "\n").encode("utf-8"))
    log(f"{MARGIN_FILE} written")
    return body


def run_stage2_steps(
    spec: Stage2Spec,
    selection: Mapping[str, Json],
    plan_of: Callable[[str], Sequence[PlannedUnit]],
    margin_plan_of: Callable[[str], Sequence[PlannedUnit]],
    table: P3Table,
    in_dictionary: Mapping[str, bool],
    paths: Stage2Paths,
    identity: Mapping[str, Json],
    *,
    log: Callable[[str], None],
    read_usage_usec: Callable[[], int] = read_usage_usec,
    check_code: Callable[[], None] | None = None,
    **driver: Any,
) -> str | None:
    """What the ``--stage2`` command does: the passes unless ``final.json`` exists, then
    the margin block if a setting other than ``c0`` stands. So the same command resumes
    either step. Returns what stands, or None if the passes left no result."""
    if paths.margin.exists():
        raise GuardFailure(f"{paths.margin} exists: there is one run")
    args = (spec, selection, plan_of, margin_plan_of, table, in_dictionary, paths)
    base = 0
    if paths.final.exists():
        if _standing(selection, paths) == DEFAULT:
            raise GuardFailure(f"{paths.final} exists, and c0 stands: nothing is left")
    else:
        passed = run_stage2(
            *args,
            identity,
            log=log,
            read_usage_usec=read_usage_usec,
            check_code=check_code,
            **driver,
        )
        if passed is None:
            return None
        base = read_usage_usec()
    name = _standing(selection, paths)
    if name != DEFAULT:
        run_margin(
            *args,
            identity,
            log=log,
            read_usage_usec=read_usage_usec,
            check_code=check_code,
            base=base,
            **driver,
        )
    return name


# --------------------------------------------------------------------------
# Start-up guards
# --------------------------------------------------------------------------


def initial_environment() -> dict[str, str]:
    """The environment this process was started with. ``import pilot`` sets the BLAS
    variables in ``os.environ``, so that cannot show they were set before numpy
    loaded."""
    data = Path("/proc/self/environ").read_bytes()
    pairs = (item.partition(b"=") for item in data.split(b"\0") if item)
    return {k.decode(): v.decode() for k, _, v in pairs}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def check_blas(environ: Mapping[str, str]) -> None:
    for name in BLAS_VARIABLES:
        if environ.get(name) != "1":
            raise GuardFailure(f"{name} must be 1 at start, is {environ.get(name)!r}")


def launch_guards(
    repo: Path,
    *,
    prereg_sha: str,
    f_commit: str,
    environ: Mapping[str, str],
    packages: Sequence[Path] | None = None,
) -> dict[str, str]:
    """The text frozen and unchanged, the tree clean, the script's commit and this
    worktree in one LOG line (section 11, :data:`LAUNCH_LOG_LINE`), BLAS at one thread.
    Returns the run's identity."""
    if not prereg_sha or not f_commit:
        raise GuardFailure("PREREG_SHA256 and F_COMMIT are empty: not frozen")
    prereg = repo / PREREG_PATH
    if not prereg.is_file():
        raise GuardFailure(f"no pre-registration at {prereg}")
    digest = hashlib.sha256(prereg.read_bytes()).hexdigest()
    if digest != prereg_sha:
        raise GuardFailure(f"prereg hash {digest} is not PREREG_SHA256 {prereg_sha}")
    try:
        fingerprint = pilot.code_fingerprint(repo, packages)
    except CampaignError as error:
        raise GuardFailure(str(error)) from error
    if _git(repo, "merge-base", "--is-ancestor", f_commit, "HEAD").returncode != 0:
        raise GuardFailure(f"HEAD does not descend from F {f_commit}")
    commit = _git(repo, "log", "-1", "--format=%H", "--", SCRIPT_PATH).stdout.strip()
    log_path = repo / LOG_PATH
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    entry = LAUNCH_LOG_LINE.format(commit=commit[:7], worktree=repo.resolve())
    if not commit or entry not in log:
        raise GuardFailure(f"the LOG does not hold the line {entry!r}")
    check_blas(environ)
    return {
        "F": f_commit,
        "fingerprint": fingerprint,
        "prereg_sha256": prereg_sha,
        "script_sha256": script_sha256(),
    }


def check_unit(cgroup: str) -> None:
    """Section 8: the run has its own unit, so the kernel's count is its own."""
    name = cgroup.splitlines()[0].rsplit("/", 1)[-1]
    if name != f"{UNIT_NAME}.service":
        raise GuardFailure(f"launch under the unit {UNIT_NAME}, not {name}")


def split_digest(split: Path) -> str:
    """``find data/v2/truths/dev -type f -print0 | sort -z | xargs -0 sha256sum
    | sha256sum``, from the root of whichever checkout holds the split."""
    named: list[tuple[bytes, Path]] = []
    for path in split.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        text = f"{SPLIT_RELATIVE}/{path.relative_to(split).as_posix()}"
        if "\\" in text or "\n" in text:
            raise GuardFailure(f"sha256sum would escape the name {text!r}")
        named.append((text.encode("utf-8"), path))
    lines = b"".join(
        hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"  " + name + b"\n"
        for name, path in sorted(named)
    )
    return hashlib.sha256(lines).hexdigest()


def check_split(split: Path, expected: str = SPLIT_SHA256) -> None:
    digest = split_digest(split)
    if digest != expected:
        raise GuardFailure(f"split hash {digest} is not {expected}")


def launch_line(repo: Path, split: Path, *, stage2: bool = False) -> str:
    """Section 8's form. A stage's CPU count is its own unit's cgroup, and a resume
    is a new unit, whose count :class:`CpuLedger` adds to the last."""
    env = " ".join(f"-E {name}=1" for name in BLAS_VARIABLES)
    line = (
        f"systemd-run --user --collect --unit={UNIT_NAME} --nice=10 "
        f"-p CPUQuota=200% -p MemoryMax=5G -p OOMPolicy=continue {env} "
        f"--working-directory={repo} uv run python {SCRIPT_PATH} "
        f"--workers {WORKERS} --split {split}"
    )
    return f"{line} --stage2" if stage2 else line


def relative_usage() -> Callable[[], int]:
    """A rehearsal's count: its shell's cgroup, from now. A launch has its own unit,
    which starts at zero, so it reads the count as it is."""
    start = read_usage_usec()

    def read() -> int:
        return read_usage_usec() - start

    return read


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--split", type=Path, default=pilot.DEFAULT_SPLIT)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--workers", type=int, choices=(1, WORKERS), default=WORKERS)
    p.add_argument("--print-hashes", action="store_true")
    p.add_argument("--print-launch", action="store_true")
    p.add_argument(
        "--rehearsal",
        action="store_true",
        help="a small run on seeds 1 to 3 and the rehearsal root, under its own "
        "directory: the launch's streams are out of its reach",
    )
    p.add_argument("--truths", default=None, help="a rehearsal's truth ids")
    p.add_argument("--seeds", default=None, help="a rehearsal's seeds (default 1)")
    p.add_argument(
        "--fits", type=int, default=None, help="a rehearsal's budget (default 4)"
    )
    p.add_argument(
        "--stage2",
        action="store_true",
        help="section 2's check of the winner, after selection.json",
    )
    p.add_argument(
        "--export-p3-table",
        type=Path,
        default=None,
        help="the P3 store to read, read-only, for section 2's table",
    )
    p.add_argument(
        "--table-out",
        type=Path,
        default=None,
        help="where the table goes (default P3_TABLE_PATH); exported once",
    )
    return p


def _same_code(fingerprint: str) -> Callable[[], None]:
    """Run before a record is written: the code that ran is the code on disk."""

    def check() -> None:
        after = pilot.code_fingerprint(pilot.ROOT)
        if after != fingerprint:
            raise GuardFailure(f"code moved under the run: {after}")

    return check


def _export(args: argparse.Namespace) -> int:
    target = args.table_out or pilot.ROOT / P3_TABLE_PATH
    if target.exists():
        raise GuardFailure(f"{target} exists: the table is exported once")
    data = p3_table_bytes(export_p3_rows(args.export_p3_table))
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_file(target, data)
    print(f"{hashlib.sha256(data).hexdigest()}  {target}", flush=True)
    return EXIT_DONE


def _stage2(args: argparse.Namespace) -> int:
    if args.rehearsal:
        raise GuardFailure("stage 2 has no rehearsal on the command line")
    identity = launch_guards(
        pilot.ROOT,
        prereg_sha=PREREG_SHA256,
        f_commit=F_COMMIT,
        environ=initial_environment(),
    )
    check_unit(Path("/proc/self/cgroup").read_text(encoding="utf-8"))
    check_split(args.split)
    selection = _read_body(launch_paths(pilot.ROOT).selection) or {}
    table = load_p3_table(pilot.ROOT / P3_TABLE_PATH, P3_TABLE_SHA256)
    records = pilot.load_split(args.split)
    entries = pilot.truth_entries(records)
    env = pilot.pointproc_environment(records)
    spec = stage2_launch_spec(tuple(sorted(e.id for e in entries)))

    def plan_of(name: str) -> tuple[PlannedUnit, ...]:
        return plan_stage2(name, spec, entries, env)

    def margin_plan_of(name: str) -> tuple[PlannedUnit, ...]:
        return plan_margin(name, spec, entries, env)

    paths = stage2_paths(pilot.ROOT)
    final = run_stage2_steps(
        spec,
        selection,
        plan_of,
        margin_plan_of,
        table,
        {e.id: e.in_dictionary for e in entries},
        paths,
        identity,
        log=_print_line,
        check_code=_same_code(identity["fingerprint"]),
        workers=args.workers,
    )
    if final is None or (final != DEFAULT and not paths.margin.exists()):
        return EXIT_NO_LABEL
    return EXIT_DONE


def _print_line(line: str) -> None:
    print(line, flush=True)


def check_launch_arguments(args: argparse.Namespace) -> None:
    """Sections 5 and 6: the launch's truths, seeds, budget and paths are the text's,
    with no option. Ignoring a value given here would run something else."""
    given = (args.truths, args.seeds, args.fits, args.out)
    if any(value is not None for value in given):
        raise GuardFailure(
            "--truths, --seeds, --fits and --out are a rehearsal's alone"
        )


def _rehearsal(
    args: argparse.Namespace, paths: OutPaths
) -> tuple[RunSpec, dict[str, str]]:
    if paths.root.resolve() == (pilot.ROOT / STORE_DIR).resolve():
        raise GuardFailure(f"a rehearsal never writes the launch's store {STORE_DIR}")
    if not args.truths:
        raise GuardFailure("a rehearsal names its --truths")
    truths = tuple(args.truths.split(","))
    seeds = tuple(int(s) for s in (args.seeds or "1").split(","))
    spec = rehearsal_spec(truths, seeds, 4 if args.fits is None else args.fits)
    check_spec(spec)
    check_blas(initial_environment())
    return spec, {"mode": "rehearsal", "script_sha256": script_sha256()}


def _launch(args: argparse.Namespace) -> tuple[RunSpec, dict[str, str]]:
    identity = launch_guards(
        pilot.ROOT,
        prereg_sha=PREREG_SHA256,
        f_commit=F_COMMIT,
        environ=initial_environment(),
    )
    check_unit(Path("/proc/self/cgroup").read_text(encoding="utf-8"))
    check_split(args.split)
    ids = tuple(sorted(r.id for r in pilot.load_split(args.split)))
    spec = launch_spec(ids)
    check_spec(spec)
    return spec, identity


def main(argv: Sequence[str] | None = None) -> int:
    pilot.reconfigure_stdout()
    args = build_parser().parse_args(argv)
    if args.print_hashes:
        for path in (SCRIPT_PATH, TEST_PATH):
            digest = hashlib.sha256((pilot.ROOT / path).read_bytes()).hexdigest()
            print(f"{digest}  {path}")
        return EXIT_DONE
    if args.print_launch:
        line = launch_line(pilot.ROOT, args.split.resolve(), stage2=args.stage2)
        print(line)
        return EXIT_DONE
    usage: Callable[[], int] = read_usage_usec
    paths = launch_paths(pilot.ROOT)
    if args.out:
        paths = OutPaths(args.out)
    elif args.rehearsal:
        paths = rehearsal_paths(pilot.ROOT, "run")
    try:
        if args.export_p3_table is not None:
            return _export(args)
        if not args.rehearsal:
            check_launch_arguments(args)
        if args.stage2:
            return _stage2(args)
        check_no_selection(paths)
        check_code: Callable[[], None] | None = None
        if args.rehearsal:
            spec, identity = _rehearsal(args, paths)
            usage = relative_usage()
        else:
            spec, identity = _launch(args)
            check_code = _same_code(identity["fingerprint"])
        records = pilot.load_split(args.split)
        plan = plan_stage1(
            spec, pilot.truth_entries(records), pilot.pointproc_environment(records)
        )
        outcome = run_stage1(
            spec,
            plan,
            paths,
            identity,
            stop_s=STAGE1.stop_s,
            cpu_limit_s=STAGE1.limit_s,
            log=_print_line,
            check_code=check_code,
            workers=args.workers,
            read_usage_usec=usage,
        )
    except (GuardFailure, ResumeRefused, ByteFailure, CampaignError) as error:
        print(f"refused: {error}", flush=True)
        return EXIT_REFUSED
    return EXIT_DONE if outcome.selection is not None else EXIT_NO_LABEL


if __name__ == "__main__":
    sys.exit(main())
