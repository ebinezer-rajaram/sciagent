"""Search misses on P4's fits (fit/5): an instrument (SPEC §6.3).

The rule is ``docs/v2/prereg/2026-11-05_p4-search-misses.md``, which amends
``docs/v2/prereg/2026-11-04_pilot-search-misses.md`` ("PR"); a section number below
means the amendment, and "PR x" the pilot rule. This script implements the two and
decides nothing of its own. It copies what it needs from
``scripts/pilot_search_misses.py``, whose bytes stay frozen.

**The ends.** Only ``killed`` and ``terminated`` can resume. ``complete``,
``cpu_stop``, ``wall_limit`` and ``stop`` end the run and reach the terminal step
with nothing chosen. ``--finalise`` on a run that could still resume refuses
unless given ``--end``, with the note that gives its authority. ``bug`` writes no
label, and a byte failure writes the label alone. Neither writes a read.

**The terminal step** (:func:`terminal_step`) is the only place the contrasts are
read, and :func:`compute_reads` is the only function that calls ``read_contrasts``,
``read_floors``, ``read_controls`` or ``analysis.verdict``. ``reads.json`` and
``decision.json`` are both written to temporary names first and then renamed, reads
first. A crash between the renames leaves the second temporary file whole, so a
later ``--finalise`` completes the step and computes nothing again. Once a label
or end is in ``decision.json``, or ``reads.json`` exists, nothing rewrites either.

**Before that step** ``decision.json`` holds no score, no read and no label, and
:func:`refuse` writes the refusal alone. A byte failure's reason can hold a
reading, so ``decision.json`` keeps only its kind and the reason stays in the
journal, which nobody reads before the run ends (PR 9, point 4).

**Processes and records** are as PR: a job is one fit in one process
(:class:`Driver`), ``journal.jsonl`` is appended and fsynced by whichever process
owns the job, ``units.jsonl`` is its job records sorted and without timing, and
floats are ``float.hex`` strings everywhere.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import multiprocessing
import os
import resource
import signal
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType
from typing import Any, ClassVar, Final, Literal, Protocol

import numpy as np

from sciagent.campaign import analysis, contrasts, cpu
from sciagent.campaign.analysis import Row, Table
from sciagent.campaign.contrasts import (
    ContrastRead,
    ControlsRead,
    FloorRead,
    ScriptedControls,
)
from sciagent.campaign.plan import B_SPARSE, ORACLE
from sciagent.glm.fit import PSI_FULL_GRID_MAX
from sciagent.glm.grammar import Link
from sciagent.scenarios.records import canonical_json
from sciagent.scoring import gonogo

type Json = Any
type RowKey = tuple[str, int, str]
type Record = dict[str, Json]

# --------------------------------------------------------------------------
# Fixed by the text
# --------------------------------------------------------------------------

#: sha256 of the frozen amendment, in code, not typed by the operator.
PREREG_SHA256: Final = (
    "4fc674dc4fbcd782dce8e9b96726ff5a2074b89848805b302d6e82f732025188"
)
#: The commit that freezes it.
F2_COMMIT: Final = "903194309fc3c8a1c0b76c5c56890980c162fcf1"
PREREG_PATH: Final = "docs/v2/prereg/2026-11-05_p4-search-misses.md"


@dataclass(frozen=True)
class Roots:
    """The three roots a run draws from. Every call names them, so nothing can
    fall into a frozen stream by default."""

    seed: int
    log: int
    reads: int


SEED_ROOT: Final = 20261111
HUNT_LOG_ROOT: Final = 20261112
#: Handed to the read functions and never drawn from here: read 0's stream is
#: this root bare (section 2.7).
READS_ROOT: Final = 20261110
FROZEN_ROOTS: Final = Roots(SEED_ROOT, HUNT_LOG_ROOT, READS_ROOT)
TEST_ROOT: Final = 20261107
TEST_LOG_ROOT: Final = 20261108
TEST_READS_ROOT: Final = 20261109
REHEARSAL_ROOTS: Final = Roots(TEST_ROOT, TEST_LOG_ROOT, TEST_READS_ROOT)
READS_RESAMPLES: Final = 10_000

H3_LIMIT: Final = gonogo.BSYM_TOLERANCE
TOL_FLOOR: Final = 3e-7
S1_MIN_MISSES: Final = 5
S1_RECALL_FLOOR: Final = 0.8
#: ``audit.GAP_THRESHOLD``, the ``hunt_gap`` gate S4 counts.
GAP_GATE: Final = 0.01

CPU_STOP_HOURS: Final = 172
CPU_CEILING_HOURS: Final = 178
JOB_CPU_HOURS: Final = 3
#: A job stops searching here, flagged ``cpu_cut``, rather than reach the limit.
JOB_CUT_HOURS: Final = 2.5
WALL_HOURS: Final = 120
#: A SIGTERM this near the 120 hours is ``RuntimeMaxSec``; earlier it is a reboot.
WALL_MARGIN_S: Final = 600.0
MEM_GATE_GIB: Final = 8
#: Hard ``RLIMIT_CPU`` above the soft one: the soft limit's SIGXCPU kills the job.
CPU_HARD_MARGIN_S: Final = 60
START_METHOD: Final = "spawn"

#: How a run may have ended and still be resumed (section 2.8).
RESUMABLE_ENDS: Final = ("killed", "terminated")
#: The ends that reach the terminal step with nothing chosen (section 2.8).
TERMINAL_ENDS: Final = ("complete", "cpu_stop", "wall_limit", "stop")
#: The ``run_end`` of an authorised ``--end``.
AUTHORISED_END: Final = "end"

T2_CASES: Final = (
    (0, "softplus", 103648458),
    (2, "exp", 1499614403),
    (0, "softplus", 1074),
    (0, "softplus", 3273957339),
    (0, "softplus", 358866363),
    (0, "softplus", 2006407976),
)
GROUP_B: Final = ("dev-015", 3)

EXIT_CODES: Final = {
    "BYTE_FAIL": 3,
    "INCOMPLETE": 5,
    "CONTRASTS_CHANGE": 2,
    "CONTROL_FAIL": 4,
    "CONTRASTS_SURVIVE_FOUND_MISSES": 0,
}
#: A refusal, a ``bug`` and a resumable end carry no label, so no label's code.
NO_LABEL_EXIT: Final = 1
STATEMENT: Final = (
    "The reportable contrasts survive substitution of the {n} misses found (N "
    "counts rows a read uses). Of the {t} fits the reads use, {e} are exact over "
    "their certified grid points, {u} are searched and unvalidated, {x} are "
    "exhaustive but uncertified and were not checked, and {a} are outside the "
    "check. Nothing is said about misses on the {u} fits that the search did not "
    "find, or about what any agent would have done under a better fit."
)
#: Section 5's table.
NEXT: Final = {
    "CONTRASTS_SURVIVE_FOUND_MISSES": "P4's report is generated from the stored "
    "table, and carries section 2.7's wording and the per-read line",
    "CONTRASTS_CHANGE": "Both reads are reported, the stored numbers stay the "
    "record, and the owner decides what P4 can claim. With partial: true nothing "
    "is applied until the owner has decided",
    "CONTROL_FAIL": "The stored contrasts are not put forward as checked. Exact "
    "class reported. A frozen amendment with a stronger reference, or the owner's "
    "own decision to go on with the gap stated",
    "INCOMPLETE": "Same as CONTROL_FAIL. It follows --end, a stop limit, or a "
    "complete end with a reference_failed tier 1 fit, so there is no resume",
    "BYTE_FAIL": "Same, and it is an instrument fault: root cause first, by "
    "amendment, before any reading",
}
#: Section 2.7's map from a searched fit to the control that speaks for it.
GROUPS_AB_GRID_MAX: Final = 24_000
CONTROLLED_GRID_MAX: Final = 3_456_000
STRATA: Final = (
    "groups_a_b",
    "large-exp",
    "large-softplus",
    "id-24000",
    "id-large",
    "no_control",
)
HUNT_STRATUM_NAMES: Final = ("id-24000", "id-large", "large-exp", "large-softplus")
S1_BELOW_FLOOR: Final = (
    "R1 missed more than one in five of the misses on grids where the answer is known."
)
NO_CONTROL: Final = "no known miss, no control"
#: Section 2.7's words for a read not reportable on both tables.
NO_STATEMENT: Final = "not reportable, no statement"
#: Section 2.6's third point, beside every read with a substituted LLM row.
LLM_CAVEAT: Final = (
    "Sessions cannot be replayed under a better fit: the agent saw fit/5's "
    "numbers. Substitution corrects a score, not what the agent did after seeing "
    "fit/5's number."
)

#: Job records: these reach ``units.jsonl``, in this order within a job.
UNIT_TYPES: Final = ("r0", "r1", "r2", "done", "r2_lost", "reference_failed")
#: ``r2_lost``: the job died after R1 was on record, so R1's result stands.
TERMINAL_TYPES: Final = ("done", "r2_lost", "reference_failed")


class GuardFailure(RuntimeError):
    """A start-up guard refused the run (section 2.10): no label."""


class ByteFailure(RuntimeError):
    """A byte check failed. Not a ``SciAgentError``: it must never be taken for a
    unit failure and turned into ``reference_failed``. ``kind`` is what reaches
    ``decision.json``: a message can hold a reading."""

    kind: ClassVar[str] = "byte_check"


class ResumeRefused(RuntimeError):
    """A resume, or a ``--finalise`` that needs a choice nobody made."""


class CpuAccountingError(RuntimeError):
    """The unit's ``cpu.stat`` cannot be read, so the ceiling cannot be kept."""


# --------------------------------------------------------------------------
# Seeds (section 2.2)
# --------------------------------------------------------------------------


def fit_case(truth: str, seed: int, text: str) -> str:
    return f"{truth}|{seed}|{text}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reference_rng(c: str, root: int) -> np.random.Generator:
    """The case's stream. ``root`` has no default: :data:`SEED_ROOT` runs once,
    at the launch, so every call says which root it means."""
    h = int(_sha(c)[:16], 16)
    return np.random.default_rng(np.random.SeedSequence([root, h]))


def fit_job_id(truth: str, seed: int, text: str) -> str:
    return _sha(fit_case(truth, seed, text))


# --------------------------------------------------------------------------
# Values, tolerance and the miss rule (PR 2.3)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Valued:
    """A fit's training log L, its absolute gap and its certificate."""

    ll: float
    gap: float
    certified: bool


@dataclass(frozen=True)
class MissCall:
    kind: Literal["miss", "near_tie", "none"]
    gain: float
    tol: float
    stored_uncertified: bool


def _gap_dropped(stored: Valued) -> bool:
    return not stored.certified or not math.isfinite(stored.gap)


def tolerance(chosen: Valued, stored: Valued) -> float:
    """``max(3e-7, gap_stored + gap_reference)``, with ``gap_stored`` dropped
    for a stored fit that is uncertified or has a non-finite gap."""
    reference = abs(chosen.gap)
    if _gap_dropped(stored):
        return max(TOL_FLOOR, reference)
    return max(TOL_FLOOR, abs(stored.gap) + reference)


def call_miss(chosen: Valued, stored: Valued) -> MissCall:
    """The miss rule: actual L against the row's stored fit."""
    gain = chosen.ll - stored.ll
    tol = tolerance(chosen, stored)
    kind: Literal["miss", "near_tie", "none"] = "none"
    if gain > tol:
        kind = "miss"
    elif gain > 0.0:
        kind = "near_tie"
    return MissCall(kind, gain, tol, _gap_dropped(stored))


def is_exhaustive(grid_size: int) -> bool:
    """Whether fit/5's ``_search`` takes the whole grid (PR 2.1)."""
    return grid_size <= PSI_FULL_GRID_MAX


def held_out_delta(stored: float, corrected: float) -> tuple[float | None, str]:
    """Δ and its kind (PR 2.4); ``-inf`` to ``-inf`` is 0 by definition."""
    if stored == corrected:
        return 0.0, "finite"
    if not (math.isfinite(stored) and math.isfinite(corrected)):
        return None, "infinite_move"
    return corrected - stored, "finite"


def h3_falsified(deltas: Iterable[tuple[float | None, str]]) -> bool:
    return any(d is None or abs(d) > H3_LIMIT for d, _ in deltas)


def control_caught(best: Valued | None, fit5: Valued, known: Valued | None) -> bool:
    """PR 2.9: the search's own best is at least the known value minus the
    tolerance, and exceeds fit/5's value by more than the tolerance."""
    if best is None or known is None:
        return False
    return best.ll >= known.ll - tolerance(
        best, known
    ) and best.ll > fit5.ll + tolerance(best, fit5)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


def _encode(value: Json) -> Json:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    return value


def dumps_record(value: Json) -> str:
    """Canonical JSON with every float as ``float.hex``."""
    return canonical_json(_encode(value))


def unhex(value: str | None) -> float | None:
    return None if value is None else float.fromhex(value)


def need_float(value: str) -> float:
    return float.fromhex(value)


def valued_of(data: Mapping[str, Json]) -> Valued:
    """A value as records hold it (R1's ``best`` is certified by definition)."""
    return Valued(
        need_float(data["ll"]),
        need_float(data["gap"]),
        bool(data.get("certified", True)),
    )


class Journal:
    """Append-only, one fsynced ``write`` per record: appends from the job
    processes and the driver do not interleave (``O_APPEND``, small lines)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: Mapping[str, Json]) -> None:
        append_line(self.path, dumps_record(dict(record)))


def append_line(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_records(path: Path) -> list[Record]:
    """The records on disk. A last line with no newline is a torn write, not a
    record; anything else that does not parse is a failure."""
    if not path.exists():
        return []
    data = path.read_bytes()
    lines = data.split(b"\n")
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


def write_temporary(path: Path, data: bytes) -> None:
    """``data`` on disk at ``path``, durable before any rename."""
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def publish(temporary: Path, path: Path) -> None:
    """The rename, durable with its directory."""
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def replace_file(path: Path, data: bytes) -> None:
    """Atomic and durable, rename included (never the journal, never a read)."""
    temporary = path.with_name(path.name + ".tmp")
    write_temporary(temporary, data)
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def units_bytes(records: Iterable[Mapping[str, Json]]) -> bytes:
    """``units.jsonl``: job records, sorted, one per (job, type). A retry writes
    identical bytes; different bytes are a determinism failure."""
    lines: dict[tuple[str, int], str] = {}
    for record in records:
        kind = record["type"]
        if kind not in UNIT_TYPES:
            continue
        key = (str(record["job"]), UNIT_TYPES.index(kind))
        line = dumps_record(dict(record))
        if lines.setdefault(key, line) != line:
            raise UnitsDiffer(f"two different {kind} records for job {record['job']}")
    return "".join(lines[k] + "\n" for k in sorted(lines)).encode("utf-8")


class UnitsDiffer(ByteFailure):
    kind: ClassVar[str] = "units"


class EmptyCorrectedDiffers(ByteFailure):
    kind: ClassVar[str] = "empty_corrected"


class RowsOutsidePlan(ByteFailure):
    kind: ClassVar[str] = "rows_outside_plan"


@dataclass(frozen=True)
class OutPaths:
    """Everything the run writes, all under one directory outside the repo."""

    root: Path

    @property
    def journal(self) -> Path:
        return self.root / "journal.jsonl"

    @property
    def units(self) -> Path:
        return self.root / "units.jsonl"

    @property
    def timing(self) -> Path:
        return self.root / "timing.jsonl"

    @property
    def decision(self) -> Path:
        return self.root / "decision.json"

    @property
    def decision_temporary(self) -> Path:
        return self.root / "decision.json.pending"

    @property
    def reads(self) -> Path:
        return self.root / "reads.json"

    @property
    def reads_temporary(self) -> Path:
        return self.root / "reads.json.pending"

    @property
    def controls(self) -> Path:
        return self.root / "controls.json"

    @property
    def cpu(self) -> Path:
        return self.root / "cpu.json"

    @property
    def stop(self) -> Path:
        return self.root / "STOP"

    @property
    def end_note(self) -> Path:
        return self.root / "END"

    @property
    def lock(self) -> Path:
        return self.root / "driver.lock"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"


class RecordSink(Protocol):
    def record(self, type_: str, payload: Mapping[str, Json]) -> None: ...


class Sink:
    """A job's writer: straight to the journal, so a record outlives its
    process, and so R1's result is on disk before R2 starts."""

    def __init__(self, journal: Path, job: str) -> None:
        self.journal = Journal(journal)
        self.job = job

    def record(self, type_: str, payload: Mapping[str, Json]) -> None:
        self.journal.append({"type": type_, "job": self.job, **payload})


# --------------------------------------------------------------------------
# The driver: one process per job (PR 6, 8, 9)
# --------------------------------------------------------------------------


class Job(Protocol):
    @property
    def id(self) -> str: ...

    def run(self, sink: RecordSink) -> None: ...


@dataclass(frozen=True)
class Stage:
    """Jobs in order; a stage ends before the next starts. ``cpu_cap_s`` stops
    dispatch once the stage's finished jobs have used that much CPU.
    ``follow_up`` names the jobs the records make due once every job of the
    stage has ended; they run last, under the same cap."""

    name: str
    jobs: tuple[Job, ...]
    cpu_cap_s: float | None = None
    follow_up: Callable[[Sequence[Record]], Sequence[Job]] | None = None


def exit_kind(exitcode: int | None) -> str:
    """How a job's process ended. An uncaught exception exits 1 through
    ``multiprocessing``; the kernel's limit is SIGXCPU."""
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


def job_main(job: Job, stage: str, journal: str, timing: str, cpu_limit_s: int) -> None:
    """A job's process. It serves this job and exits."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    resource.setrlimit(
        resource.RLIMIT_CPU, (cpu_limit_s, cpu_limit_s + CPU_HARD_MARGIN_S)
    )
    start = time.perf_counter()
    mark = cpu.cpu_mark()
    job.run(Sink(Path(journal), job.id))
    line = {
        "job": job.id,
        "stage": stage,
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
    """The code that wrote ``cpu.json`` is the code running now (PR 2.10). A
    change to the script is an amendment, with a new output directory."""
    if not path.exists():
        return
    recorded = json.loads(path.read_text(encoding="utf-8")).get("script_sha256")
    current = script_sha or script_sha256()
    if recorded != current:
        raise ResumeRefused(
            f"script hash {current} is not the first start's {recorded}"
        )


def decision_is_final(paths: OutPaths) -> bool:
    """A label or an end is in ``decision.json``: a refusal is neither."""
    if not paths.decision.exists():
        return False
    body = json.loads(paths.decision.read_text(encoding="utf-8"))
    return "label" in body or "end" in body


def check_start(paths: OutPaths) -> None:
    """Section 2.8: once ``reads.json`` exists every resume is refused, so nobody
    reads the tables and then chooses whether to go on. A run that ended any way
    but ``killed`` or ``terminated`` does not resume, and a fresh start needs a
    fresh directory (PR 2.10). The CPU and wall limits are the ledger's."""
    if paths.reads.exists():
        raise ResumeRefused("reads.json exists: no run resumes after the reads")
    if decision_is_final(paths):
        raise ResumeRefused("decision.json holds an end: this run is over")
    if paths.stop.exists():
        raise ResumeRefused("STOP exists: going on takes an amendment")
    records = read_records(paths.journal)
    if records and paths.cpu.exists():
        end = journal_end(records)
        if end not in RESUMABLE_ENDS:
            raise ResumeRefused(f"a run that ended {end!r} is not resumed")
    if not paths.cpu.exists() and (records or paths.units.exists()):
        raise ResumeRefused(f"a fresh start in {paths.root}, which holds records")


@dataclass
class CpuLedger:
    """``cpu.json``: earlier units plus this one, kept current during a run,
    with the hash of the script the first start ran."""

    path: Path
    script_sha: str
    earlier_usec: int
    current_usec: int
    earlier_wall_s: float
    current_wall_s: float

    @classmethod
    def load(cls, path: Path, script_sha: str | None = None) -> CpuLedger:
        """The ledger as a new unit would start it, written nowhere. Refuses
        another script, a total at or above the stop, or no time left."""
        check_script(path, script_sha)
        data: Mapping[str, Json] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        ledger = cls(
            path,
            script_sha or script_sha256(),
            data.get("earlier_usec", 0) + data.get("current_usec", 0),
            0,
            data.get("earlier_wall_s", 0.0) + data.get("current_wall_s", 0.0),
            0.0,
        )
        if ledger.total_usec >= CPU_STOP_HOURS * 3600 * 10**6:
            raise ResumeRefused(
                f"{ledger.total_usec / 3.6e9:.2f} CPU hours used: at or above the "
                f"{CPU_STOP_HOURS}-hour stop"
            )
        if ledger.wall_s >= WALL_HOURS * 3600.0:
            raise ResumeRefused(f"no wall time left of {WALL_HOURS} hours")
        return ledger

    @classmethod
    def begin(cls, path: Path, script_sha: str | None = None) -> CpuLedger:
        """Start a unit."""
        ledger = cls.load(path, script_sha)
        ledger.save()
        return ledger

    @property
    def total_usec(self) -> int:
        return self.earlier_usec + self.current_usec

    @property
    def wall_s(self) -> float:
        return self.earlier_wall_s + self.current_wall_s

    def update(self, *, usage_usec: int, wall_s: float) -> None:
        """One reading: written and fsynced, so a SIGKILL loses at most the
        minute since the last one."""
        self.current_usec, self.current_wall_s = usage_usec, wall_s
        self.save()

    def save(self) -> None:
        body = {
            "script_sha256": self.script_sha,
            "earlier_usec": self.earlier_usec,
            "current_usec": self.current_usec,
            "earlier_wall_s": self.earlier_wall_s,
            "current_wall_s": self.current_wall_s,
        }
        replace_file(self.path, (json.dumps(body, sort_keys=True) + "\n").encode())


def ledger_totals(path: Path) -> tuple[int, float]:
    """CPU microseconds and wall seconds so far, with no check."""
    if not path.exists():
        return 0, 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    return (
        int(data["earlier_usec"] + data["current_usec"]),
        float(data["earlier_wall_s"] + data["current_wall_s"]),
    )


@contextmanager
def driver_lock(paths: OutPaths) -> Iterator[None]:
    """One driver per output, also against ``--finalise``. ``flock`` goes with
    the process, so a SIGKILL leaves nothing to clear."""
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
    """Runs stages of jobs, each in its own process (PR 6, 8, 9).

    The driver owns no result: it reads a job's exit and the journal, and adds
    ``job_lost``, ``reference_failed`` for what a dead job could not write, and
    ``run_start`` / ``run_end``. It computes no read.
    """

    def __init__(
        self,
        paths: OutPaths,
        stages: tuple[Stage, ...],
        *,
        workers: int,
        read_usage_usec: Callable[[], int] = read_usage_usec,
        read_mem_gib: Callable[[], float] = read_mem_available_gib,
        cpu_limit_s: int = JOB_CPU_HOURS * 3600,
        poll_s: float = 1.0,
        cpu_poll_s: float = 60.0,
        start_method: str = START_METHOD,
        after_stage: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.after_stage = after_stage
        self.stages = stages
        self.workers = workers
        self.read_usage_usec = read_usage_usec
        self.read_mem_gib = read_mem_gib
        self.cpu_limit_s = cpu_limit_s
        self.poll_s = poll_s
        self.cpu_poll_s = cpu_poll_s
        self.context: Any = multiprocessing.get_context(start_method)
        self.journal = Journal(paths.journal)
        self.finished: set[str] = set()
        self.lost: dict[str, int] = {}
        self.byte_failed = False
        #: Jobs whose R1 result is on record: their death is ``r2_lost``.
        self.r1_on_record: set[str] = set()
        self.terminating = False
        self.ledger: CpuLedger | None = None
        self.started = 0.0
        self.last_cpu = -math.inf

    def run(self) -> str:
        """The stages, in order; returns how the run ended. A start over the
        ceiling raises :class:`ResumeRefused` before anything is appended."""
        self.paths.root.mkdir(parents=True, exist_ok=True)
        repair_tail(self.paths.journal)
        check_start(self.paths)
        self.ledger = CpuLedger.begin(self.paths.cpu)
        self.started = time.monotonic()
        self.journal.append({"type": "run_start"})
        self._ingest()
        previous = None
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, self._on_term)
        try:
            self._tick_cpu(force=True)
            reason = self._run_stages()
            self._tick_cpu(force=True)
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        self.journal.append({"type": "run_end", "reason": reason})
        return reason

    def _on_term(self, signum: int, frame: FrameType | None) -> None:
        self.terminating = True

    def _term_reason(self) -> str:
        """SIGTERM is ``RuntimeMaxSec`` only when the 120 hours are spent: each
        unit is launched with what is left, so the total reaches them then.
        Earlier it is a reboot, or an operator's ``systemctl stop``."""
        assert self.ledger is not None
        self._tick_cpu(force=True)
        spent = self.ledger.wall_s >= WALL_HOURS * 3600.0 - WALL_MARGIN_S
        return "wall_limit" if spent else "terminated"

    def _ingest(self) -> None:
        self.finished, self.lost, self.byte_failed = set(), {}, False
        for r in read_records(self.paths.journal):
            if r["type"] in TERMINAL_TYPES:
                self.finished.add(r["job"])
            elif r["type"] == "job_lost":
                self.lost[r["job"]] = self.lost.get(r["job"], 0) + 1
            elif r["type"] == "byte_fail":
                self.byte_failed = True
            elif r["type"] == "r1" and "result" in r:
                self.r1_on_record.add(r["job"])

    def _tick_cpu(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_cpu < self.cpu_poll_s:
            return
        self.last_cpu = now
        assert self.ledger is not None
        self.ledger.update(usage_usec=self.read_usage_usec(), wall_s=now - self.started)

    def _stop_reason(self) -> str | None:
        assert self.ledger is not None
        if self.terminating:
            return self._term_reason()
        if self.paths.stop.exists():
            return "stop"
        if self.ledger.total_usec >= CPU_STOP_HOURS * 3600 * 10**6:
            return "cpu_stop"
        return None

    def _run_stages(self) -> str:
        for stage in self.stages:
            reason = self._run_stage(stage)
            if reason is not None:
                return reason
        return "complete"

    def _stage_cpu_s(self, stage: Stage) -> float:
        if not self.paths.timing.exists():
            return 0.0
        rows = self.paths.timing.read_text(encoding="utf-8").splitlines()
        lines = [json.loads(x) for x in rows]
        return math.fsum(
            x.get("cpu_s") or 0.0 for x in lines if x.get("stage") == stage.name
        )

    def _run_stage(self, stage: Stage) -> str | None:
        """None when the stage is finished, else why the run ends."""
        queue = deque(j for j in stage.jobs if j.id not in self.finished)
        retry: deque[Job] = deque()
        running: dict[str, _Running] = {}
        follow = stage.follow_up
        noted = False
        while True:
            failure = self._reap(running, retry)
            if failure is not None:
                self._kill(running)
                return failure
            self._tick_cpu()
            reason = self._stop_reason()
            if self.terminating:
                # A SIGTERM can land after that read, which is then None and
                # would end the stage as finished (LOG 2026-11-05): ask again.
                self._kill(running)
                return self._term_reason()
            if reason is not None:
                return self._drain(running, reason)
            capped = stage.cpu_cap_s is not None and (
                self._stage_cpu_s(stage) >= stage.cpu_cap_s
            )
            if capped:
                if not noted and (queue or retry or follow is not None):
                    self.journal.append({"type": "stage_cap", "stage": stage.name})
                    noted = True
                queue.clear()
                retry.clear()
                follow = None
            if not queue and not running:
                queue, retry = retry, deque()  # after the stage's other jobs
            if not queue and not running and follow is not None:
                due = follow(read_records(self.paths.journal))
                queue = deque(j for j in due if j.id not in self.finished)
                follow = None
            if not queue and not running:
                break
            if queue:
                mem = self.read_mem_gib()
                if may_start(running=len(running), workers=self.workers, mem_gib=mem):
                    job = queue.popleft()
                    running[job.id] = self._start(job, stage)
            time.sleep(self.poll_s)
        if self.after_stage is not None:
            self.after_stage(stage.name)
        return None

    def _start(self, job: Job, stage: Stage) -> _Running:
        process = self.context.Process(
            target=job_main,
            args=(
                job,
                stage.name,
                str(self.paths.journal),
                str(self.paths.timing),
                self.cpu_limit_s,
            ),
        )
        process.start()
        return _Running(job, process)

    def _reap(self, running: dict[str, _Running], retry: deque[Job]) -> str | None:
        """Read the exits. Returns "bug" or "byte_fail" if the run must stop."""
        for job_id, handle in sorted(running.items()):
            process = handle.process
            if process.is_alive():
                continue
            process.join()
            del running[job_id]
            self._ingest()
            if self.byte_failed:
                return "byte_fail"
            if job_id in self.finished:
                continue
            kind = exit_kind(process.exitcode)
            if kind == "bug":
                return "bug"
            if kind == "cpu_limit":
                self._fail(job_id, "cpu_limit")
            elif kind == "terminated" and self.terminating:
                continue  # the unit is stopping: a resume runs this job again
            elif self.lost.get(job_id, 0) >= 1:
                self._fail(job_id, "worker_lost")
            else:
                self.journal.append({"type": "job_lost", "job": job_id})
                self.lost[job_id] = self.lost.get(job_id, 0) + 1
                retry.append(handle.job)
        return None

    def _fail(self, job_id: str, reason: str) -> None:
        kind = "r2_lost" if job_id in self.r1_on_record else "reference_failed"
        self.journal.append({"type": kind, "job": job_id, "reason": reason})
        self.finished.add(job_id)

    def _drain(self, running: dict[str, _Running], reason: str) -> str:
        """Let the jobs in flight finish; a loss now is not retried."""
        ignored: deque[Job] = deque()
        while running:
            failure = self._reap(running, ignored)
            if failure is not None:
                self._kill(running)
                return failure
            if self.terminating:
                self._kill(running)
                return self._term_reason()
            self._tick_cpu()
            time.sleep(self.poll_s)
        return reason

    def _kill(self, running: dict[str, _Running]) -> None:
        for handle in running.values():
            handle.process.terminate()
        for handle in running.values():
            handle.process.join()
        running.clear()


# --------------------------------------------------------------------------
# The census (PR 2.1, section 2.1)
# --------------------------------------------------------------------------


def row_key(row: Row) -> RowKey:
    return (row.truth, row.seed, row.system)


@dataclass(frozen=True)
class OracleExpect:
    """The fields of an ORACLE cell the byte check reads, floats as hex."""

    search_ll: str | None
    truth_psi_ll: str | None
    psi_source: str | None
    underfit: bool | None


@dataclass(frozen=True)
class RowExpect:
    """What one row's cell says, as the worker must reproduce it."""

    system: str
    llm: bool
    per_event: str
    oracle: OracleExpect | None


@dataclass(frozen=True)
class RowInfo:
    """One row of the census: its class and what its fit needs."""

    truth: str
    seed: int
    system: str
    llm: bool
    klass: str
    text: str | None
    grid_size: int | None
    link: str | None
    certified: bool | None
    expect: RowExpect | None
    sparse_link: str | None = None
    #: Section 2.9's group E: an ORACLE unit whose search lost to truth ψ.
    group_e: bool = False


@dataclass(frozen=True)
class FitGroup:
    """Rows that share one search: same data, same stored text."""

    truth: str
    seed: int
    text: str
    rows: tuple[RowExpect, ...]
    tier: int
    grid_size: int
    link: str
    group_e: bool = False


@dataclass(frozen=True)
class Census:
    """``tier1`` is ``contrast_rows(plan, truths)``, which reads no cell: a fit
    with a row in it is tier 1, whatever its outcome (section 2.1)."""

    rows: tuple[RowInfo, ...]
    tier1: frozenset[RowKey]

    def fits(self) -> list[FitGroup]:
        """The distinct coordinate fits, by ``sha256`` of their case."""
        groups: dict[tuple[str, int, str], list[RowInfo]] = {}
        for r in self.rows:
            if r.klass == "coordinate" and r.text is not None:
                groups.setdefault((r.truth, r.seed, r.text), []).append(r)
        out: list[FitGroup] = []
        for (truth, seed, text), rows in groups.items():
            members = sorted(rows, key=lambda r: r.system)
            tier1 = any((r.truth, r.seed, r.system) in self.tier1 for r in members)
            out.append(
                FitGroup(
                    truth,
                    seed,
                    text,
                    tuple(r.expect for r in members if r.expect is not None),
                    1 if tier1 else 2,
                    members[0].grid_size or 0,
                    members[0].link or "",
                    any(r.group_e for r in members),
                )
            )
        return sorted(out, key=lambda f: fit_job_id(f.truth, f.seed, f.text))


# --------------------------------------------------------------------------
# The substitution and the reads (section 2.7)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """What the terminal step reads besides the journal. ``plan_rows`` is
    ``contrast_rows(plan, truths)``."""

    census: Census
    table: Table
    plan_rows: tuple[RowKey, ...]
    controls: ScriptedControls
    roots: Roots
    resamples: int = READS_RESAMPLES


def corrected_table(
    table: Table,
    corrected: Mapping[RowKey, float],
    underfit: Mapping[RowKey, bool],
) -> Table:
    """Section 2.7's substitution, with ``derive`` over every row at once.
    A better fit can only clear an underfit flag."""
    rows = []
    for r in table.rows:
        key = row_key(r)
        row = replace(r, per_event=corrected.get(key, r.per_event))
        if key in underfit:
            if underfit[key] and r.oracle_underfit is False:
                raise ByteFailure(f"an underfit flag appeared on {key}")
            row = replace(row, oracle_underfit=underfit[key])
        rows.append(row)
    return replace(table, rows=tuple(analysis.derive(rows)))


def _substitutions(
    fits: Sequence[Mapping[str, Json]],
) -> tuple[dict[RowKey, float], dict[RowKey, bool], list[tuple[RowKey, Record]]]:
    corrected: dict[RowKey, float] = {}
    underfit: dict[RowKey, bool] = {}
    misses: list[tuple[RowKey, Record]] = []
    for record in fits:
        for row in record["rows"]:
            if row["call"] != "miss":
                continue
            key = (record["truth"], record["seed"], row["system"])
            value = unhex(row["corrected_per_event"])
            if value is None or math.isnan(value):
                raise ByteFailure(f"{key}: no corrected reading on a miss row")
            corrected[key] = value
            if row.get("corrected_underfit") is not None:
                underfit[key] = bool(row["corrected_underfit"])
            misses.append((key, row))
    return corrected, underfit, sorted(misses, key=lambda m: m[0])


@dataclass(frozen=True)
class Reads:
    """Everything the reads give, at the terminal step only. ``floors`` and
    ``controls`` are keyed ``stored`` and ``fixed``."""

    stored: Mapping[str, ContrastRead]
    fixed: Mapping[str, ContrastRead]
    #: S2's second table (section 4).
    fixed2: Mapping[str, ContrastRead]
    floors: Mapping[str, Mapping[str, FloorRead]]
    controls: Mapping[str, ControlsRead]
    hard_timeouts: Mapping[str, int]
    new: Table
    misses: tuple[tuple[RowKey, Record], ...]


def same_reads(a: Mapping[str, ContrastRead], b: Mapping[str, ContrastRead]) -> bool:
    """Equal in every read, every float by ``float.hex``, so ``nan`` equals ``nan``."""
    return list(a) == list(b) and all(contrasts.same_read(a[k], b[k]) for k in a)


def check_plan_rows(
    plan_rows: Iterable[RowKey],
    tables: Iterable[Table],
    reads: Iterable[Mapping[str, ContrastRead]],
) -> None:
    """Section 2.7: each table's rows of the contrast systems, and every read's
    ``rows``, are rows of ``contrast_rows``."""
    allowed = frozenset(plan_rows)
    for table in tables:
        for r in table.rows:
            if r.system in contrasts.CONTRAST_SYSTEMS and row_key(r) not in allowed:
                raise RowsOutsidePlan(f"table row {row_key(r)} is not a planned row")
    for one in reads:
        for name, read in one.items():
            stray = [key for key in read.rows if key not in allowed]
            if stray:
                raise RowsOutsidePlan(f"read {name} holds {stray[0]}, not planned")


def hard_timeouts(table: Table) -> dict[str, int]:
    """S6: sessions stored void as ``hard_timeout``, per arm. They are absent,
    while a ``wall_time`` end with no submission takes the floor, so one event can
    land either way, and this keeps it visible (section 2.7)."""
    return _count(
        x.system
        for x in table.sessions
        if x.void.void and x.detail.get("outcome") == "hard_timeout"
    )


def compute_reads(records: Sequence[Mapping[str, Json]], inputs: Inputs) -> Reads:
    """The only caller of the read functions. Only the terminal step calls it, and
    it raises rather than return part of an answer, so no read is written unless
    every one was computed."""
    corrected, underfit, misses = _substitutions(fit_views(records))
    _, s2_extra = _s2_rows(records)
    table = inputs.table
    args = (inputs.controls, inputs.roots.reads, inputs.resamples)
    stored = contrasts.read_contrasts(table, *args)
    rebuilt = corrected_table(table, {}, {})
    if not same_reads(contrasts.read_contrasts(rebuilt, *args), stored):
        raise EmptyCorrectedDiffers("the empty substitution does not equal stored")
    new = corrected_table(table, corrected, underfit)
    new2 = corrected_table(table, corrected | s2_extra, underfit)
    fixed = contrasts.read_contrasts(new, *args)
    fixed2 = contrasts.read_contrasts(new2, *args)
    check_plan_rows(inputs.plan_rows, (table, new, new2), (stored, fixed, fixed2))
    return Reads(
        stored,
        fixed,
        fixed2,
        {
            "stored": contrasts.read_floors(table, *args),
            "fixed": contrasts.read_floors(new, *args),
        },
        {
            "stored": contrasts.read_controls(table, *args),
            "fixed": contrasts.read_controls(new, *args),
        },
        hard_timeouts(table),
        new,
        tuple(misses),
    )


def _sign(value: float) -> int | None:
    """-1, 0 or 1: exactly zero has its own sign. None for ``nan``."""
    if math.isnan(value):
        return None
    return (value > 0.0) - (value < 0.0)


def _excludes_zero(read: ContrastRead) -> bool:
    return read.low > 0.0 or read.high < 0.0


def read_changed(a: ContrastRead, b: ContrastRead) -> bool:
    """Section 2.7: the sign of ``point``, whether ``[low, high]`` excludes zero,
    or ``reportable``, differs between the tables."""
    return (
        _sign(a.point) != _sign(b.point)
        or _excludes_zero(a) != _excludes_zero(b)
        or a.reportable != b.reportable
    )


def read_json(read: ContrastRead) -> Record:
    return {
        "point": read.point,
        "low": read.low,
        "high": read.high,
        "reportable": read.reportable,
        "n": read.n,
        "rows": [list(key) for key in read.rows],
    }


def floor_json(floor: FloorRead) -> Record:
    return {
        "floored": floor.floored,
        "no_common_seed": floor.no_common_seed,
        "without": read_json(floor.without),
    }


def controls_json(controls: ControlsRead, reads: Mapping[str, ContrastRead]) -> Record:
    return {
        "kept": list(controls.kept),
        "dropped": list(controls.dropped),
        "unchecked": list(controls.unchecked),
        "controls_pass": controls.controls_pass,
        "naming_null": controls.naming_null,
        "why_not": {k: list(controls.why_not(k, v.n)) for k, v in reads.items()},
    }


def matched_pairs(read: ContrastRead) -> int:
    """S6: the read's matched (truth, seed) pairs. ``rows`` holds the ORACLE of
    each one and no other ORACLE row."""
    return sum(key[2] == ORACLE for key in read.rows)


def reads_body(reads: Reads) -> Record:
    """``reads.json``: every part section 2.8 lists."""
    tables = {"stored": reads.stored, "fixed": reads.fixed}
    return {
        "stored": {k: read_json(v) for k, v in reads.stored.items()},
        "fixed": {k: read_json(v) for k, v in reads.fixed.items()},
        "fixed2": {k: read_json(v) for k, v in reads.fixed2.items()},
        "floors": {
            name: {k: floor_json(v) for k, v in floors.items()}
            for name, floors in reads.floors.items()
        },
        "controls": {
            name: controls_json(value, tables[name])
            for name, value in reads.controls.items()
        },
        "matched": {
            name: {k: matched_pairs(v) for k, v in table.items()}
            for name, table in tables.items()
        },
        "hard_timeouts": dict(reads.hard_timeouts),
    }


def read_lines(
    reads: Reads, census: Census, done: Mapping[str, Mapping[str, Json]]
) -> list[Record]:
    """The per-read line and S5, each over the union of the read's rows on the two
    tables, since ``rows`` is per table (section 2.7)."""
    llm = {(r.truth, r.seed, r.system) for r in census.rows if r.llm}
    substituted = {key for key, _ in reads.misses}
    out: list[Record] = []
    for name, stored in reads.stored.items():
        fixed = reads.fixed[name]
        used = frozenset((*stored.rows, *fixed.rows))
        hit = used & substituted
        status = "changed" if read_changed(stored, fixed) else "unchanged"
        if not stored.reportable and not fixed.reportable:
            status = NO_STATEMENT
        out.append(
            {
                "read": name,
                "status": status,
                **statement_counts(census, done, used),
                "n": len(hit),
                "llm_substituted": len(hit & llm),
                "rows": len(used),
            }
        )
    return out


# --------------------------------------------------------------------------
# Controls and secondaries from records (PR 2.9, section 4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class H1Status:
    a_caught: int
    a_total: int
    b_caught: bool
    d_caught: bool
    cases: tuple[Record, ...]

    @property
    def stands(self) -> bool:
        return self.a_caught >= 5 and self.b_caught and self.d_caught


def _control_of(record: Mapping[str, Json]) -> Mapping[str, Json] | None:
    if record.get("kind") == "control":
        return record
    control: Mapping[str, Json] | None = record.get("control")
    return control


def h1_status(records: Sequence[Mapping[str, Json]]) -> H1Status:
    """H1 from R1's own record and, for group D, R2's. A control never reads a
    chosen point: R0 plays no part (PR 2.9). Group E has no pass threshold, so it
    is not read here."""
    r1 = {r["job"]: r for r in records if r["type"] == "r1"}
    caught: dict[str, list[bool]] = {"A": [], "B": [], "D": []}
    cases: list[Record] = []
    for record in records:
        if record["type"] != "done":
            continue
        control = _control_of(record)
        if control is None or control["group"] not in caught:
            continue
        group = control["group"]
        best_data = (
            control.get("r2_best")
            if group == "D"
            else r1.get(record["job"], {}).get("best")
        )
        best = None if best_data is None else valued_of(best_data)
        known = None if control["known"] is None else valued_of(control["known"])
        fit5 = valued_of(control["fit5"])
        hit = control_caught(best, fit5, known)
        caught[group].append(hit)
        cases.append(
            {
                "group": group,
                "case": control["case"],
                "caught": hit,
                "fit5": fit5.ll,
                "known": None if known is None else known.ll,
                "own_best": None if best is None else best.ll,
            }
        )
    return H1Status(
        sum(caught["A"]),
        len(T2_CASES),
        any(caught["B"]),
        any(caught["D"]),
        tuple(sorted(cases, key=lambda c: (c["group"], str(c["case"])))),
    )


def group_e_summary(records: Sequence[Mapping[str, Json]], census: Census) -> Record:
    """Section 2.9's group E: the caught count, with every selected unit in the
    denominator, so a unit the run never reached counts against it."""
    r1 = {r["job"]: r for r in records if r["type"] == "r1"}
    caught = 0
    for record in records:
        if record["type"] != "done":
            continue
        control = _control_of(record)
        if control is None or control["group"] != "E":
            continue
        best = r1.get(record["job"], {}).get("best")
        known = None if control["known"] is None else valued_of(control["known"])
        hit = control_caught(
            None if best is None else valued_of(best),
            valued_of(control["fit5"]),
            known,
        )
        caught += hit
    return {"caught": caught, "of": sum(r.group_e for r in census.rows)}


def s1_status(*, found: int, total: int) -> str:
    if total < S1_MIN_MISSES:
        return "not_estimable"
    return "below_floor" if found / total < S1_RECALL_FLOOR else "at_floor"


def _r1_found(row: Mapping[str, Json], r1: Mapping[str, Json]) -> bool:
    best = r1.get("best")
    if best is None:
        return False
    stored = Valued(
        need_float(row["stored_ll"]),
        need_float(row["stored_gap"]),
        bool(row["stored_certified"]),
    )
    return call_miss(valued_of(best), stored).kind == "miss"


def s1_counts(
    fits: Iterable[Mapping[str, Json]], records: Sequence[Mapping[str, Json]]
) -> tuple[int, int]:
    """Over fits where R2 ran and found a miss: how many R1's record had too.
    Read from the ``r1`` record, which was written before R2 started."""
    r1 = {r["job"]: r for r in records if r["type"] == "r1"}
    found = total = 0
    for record in fits:
        rows = [row for row in record["rows"] if row["call"] == "miss"]
        if not rows or not record["r2_complete"]:
            continue
        total += 1
        found += all(_r1_found(row, r1.get(record["job"], {})) for row in rows)
    return found, total


def hunt_summary(records: Sequence[Mapping[str, Json]]) -> dict[str, Record]:
    """Group C per sub-stratum. A catch is read from R1's own record against
    the case's fit/5 and known values, and nothing else."""
    out: dict[str, Record] = {
        name: {
            "completed": 0,
            "found_losses": 0,
            "r1_run": 0,
            "caught": 0,
            "not_run": 0,
            "uncertified": 0,
            "sim_failed": 0,
        }
        for name in HUNT_STRATUM_NAMES
    }
    r1 = {r["job"]: r for r in records if r["type"] == "r1"}
    cases: dict[tuple[str, int], Mapping[str, Json]] = {}
    for r in records:
        if r["type"] == "done" and r.get("kind") == "hunt":
            cases[(r["stratum"], r["j"])] = r
            row = out[r["stratum"]]
            if r["sim_failed"]:
                row["sim_failed"] += 1
                continue
            row["completed"] += 1
            row["found_losses"] += bool(r["found_loss"])
            row["uncertified"] += bool(r["uncertified_loss"])
    for r in records:
        if r["type"] == "done" and r.get("kind") == "hunt_r1":
            case = cases[(r["stratum"], r["j"])]
            best = r1.get(r["job"], {}).get("best")
            row = out[r["stratum"]]
            row["r1_run"] += 1
            row["caught"] += control_caught(
                None if best is None else valued_of(best),
                valued_of(case["fit5"]),
                valued_of(case["known"]),
            )
    for row in out.values():
        row["not_run"] = row["found_losses"] - row["r1_run"]
    return out


def stratum_of(link: str, grid_size: int) -> str:
    """Section 2.7's stratum of a searched fit, fixed by link and G."""
    if grid_size > CONTROLLED_GRID_MAX:
        return "no_control"
    if link == Link.IDENTITY.value:
        return "id-24000" if grid_size <= GROUPS_AB_GRID_MAX else "id-large"
    return "groups_a_b" if grid_size <= GROUPS_AB_GRID_MAX else f"large-{link}"


def _strata(
    fits: Sequence[FitGroup],
    done: Mapping[str, Mapping[str, Json]],
    state: Mapping[str, str],
    h1: H1Status,
) -> dict[str, Record]:
    """Each stratum's searched tier 1 fits and the control that speaks for them."""
    b = "caught" if h1.b_caught else "not caught"
    controls = {
        **state,
        "groups_a_b": f"group A {h1.a_caught} of {h1.a_total}, group B {b}",
        "no_control": "no control",
    }
    counts = dict.fromkeys(STRATA, 0)
    for f in fits:
        record = done.get(fit_job_id(f.truth, f.seed, f.text))
        if f.tier == 1 and record is not None and not record["r2_complete"]:
            counts[stratum_of(f.link, f.grid_size)] += 1
    return {
        name: {"searched_fits": counts[name], "control": controls[name]}
        for name in STRATA
    }


def _control_state(hunt: Mapping[str, Record]) -> dict[str, str]:
    return {
        name: NO_CONTROL
        if row["found_losses"] == 0
        else f"{row['caught']} of {row['r1_run']} caught"
        for name, row in hunt.items()
    }


def _spread(values: Sequence[float]) -> Record:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


# --------------------------------------------------------------------------
# The label (section 5)
# --------------------------------------------------------------------------


def journal_end(records: Sequence[Mapping[str, Json]]) -> str:
    """How the last driver ended: "killed" if it never said. A byte failure and
    a STOP stay what they were through anything later, so neither can be
    laundered."""
    if any(r["type"] == "byte_fail" for r in records):
        return "byte_fail"
    if any(r["type"] == "run_end" and r["reason"] == "stop" for r in records):
        return "stop"
    last = "killed"
    for r in records:
        if r["type"] == "run_start":
            last = "killed"
        elif r["type"] == "run_end":
            last = r["reason"]
    return last


def end_state(records: Sequence[Mapping[str, Json]], paths: OutPaths) -> str:
    """The run's end as ``--finalise`` sees it. A kill with STOP present is that
    stop, and a kill at or past a limit cannot resume, so it is that limit."""
    end = journal_end(records)
    if end not in RESUMABLE_ENDS:
        return end
    if paths.stop.exists():
        return "stop"
    used, wall = ledger_totals(paths.cpu)
    if used >= CPU_STOP_HOURS * 3600 * 10**6:
        return "cpu_stop"
    if wall >= WALL_HOURS * 3600.0:
        return "wall_limit"
    return end


def decide_label(
    *,
    byte_fail: bool,
    changed: bool,
    tier1_complete: bool,
    h1: bool,
    any_reportable: bool,
) -> str:
    """Section 5: the first rule that applies."""
    if byte_fail:
        return "BYTE_FAIL"
    if changed:
        return "CONTRASTS_CHANGE"
    if not tier1_complete:
        return "INCOMPLETE"
    if not h1 or not any_reportable:
        return "CONTROL_FAIL"
    return "CONTRASTS_SURVIVE_FOUND_MISSES"


def row_state(row: RowInfo, done: Mapping[str, Mapping[str, Json]]) -> str:
    match row.klass:
        case "exhaustive":
            return "exact"
        case "exhaustive_uncertified":
            return "exhaustive_uncertified"
        case "coordinate":
            record = done.get(fit_job_id(row.truth, row.seed, row.text or ""))
            if record is None:
                return "unchecked"
            return "exact" if record["r2_complete"] else "searched"
    return "not_applicable"


def coverage_of(census: Census, done: Mapping[str, Mapping[str, Json]]) -> Record:
    states = [(r, row_state(r, done)) for r in census.rows]

    def rows(*names: str) -> int:
        return sum(s in names for _, s in states)

    def fits(*names: str) -> int:
        return len({(r.truth, r.seed, r.text) for r, s in states if s in names})

    return {
        "exact_rows": rows("exact"),
        "exact_fits": fits("exact"),
        "searched_rows": rows("searched"),
        "searched_fits": fits("searched"),
        "unchecked_rows": rows("unchecked", "exhaustive_uncertified"),
        "unchecked_fits": fits("unchecked", "exhaustive_uncertified"),
        "capped_fits": sum(bool(r.get("capped")) for r in done.values()),
        "not_applicable_rows": rows("not_applicable"),
    }


def statement_counts(
    census: Census,
    done: Mapping[str, Mapping[str, Json]],
    keys: frozenset[RowKey],
) -> dict[str, int]:
    """T, E, U, X and A of section 2.7's wording, over the rows ``keys``. A fit
    with one uncertified row counts as uncertified."""
    rank = {"exhaustive_uncertified": 0, "unchecked": 1, "searched": 2, "exact": 3}
    fits: dict[tuple[str, int, str | None], str] = {}
    outside = 0
    for r in census.rows:
        if (r.truth, r.seed, r.system) not in keys:
            continue
        state = row_state(r, done)
        if r.klass in ("no_search_np", "no_search_lib"):
            outside += 1
        if state == "not_applicable":
            continue
        key = (r.truth, r.seed, r.text)
        if key not in fits or rank[state] < rank[fits[key]]:
            fits[key] = state
    states = list(fits.values())
    return {
        "t": len(states) + outside,
        "e": states.count("exact"),
        "u": states.count("searched"),
        "x": states.count("exhaustive_uncertified"),
        "a": outside,
    }


def _s3(fits: Sequence[Mapping[str, Json]]) -> Record:
    rows = [(f, row) for f in fits for row in f["rows"]]
    misses = [row for _, row in rows if row["call"] == "miss"]
    oracle = [row["oracle"] for _, row in rows if row.get("oracle")]
    deltas = [need_float(x["delta"]) for x in misses if x["delta"] is not None]
    per_event = [
        need_float(x["gain_per_event"]) for x in misses if x.get("gain_per_event")
    ]
    return {
        "gain_nats": _spread([need_float(x["gain"]) for x in misses]),
        "gain_per_event": _spread(per_event),
        "delta": _spread(deltas),
        "delta_positive": sum(d > 0.0 for d in deltas),
        "delta_negative": sum(d < 0.0 for d in deltas),
        "delta_infinite_move": sum(x["delta_kind"] == "infinite_move" for x in misses),
        "near_ties": sum(row["call"] == "near_tie" for _, row in rows),
        "uncertified_better": sum(row["uncertified_better"] for _, row in rows),
        "uncertified_better_identity": sum(
            row["uncertified_better"] for f, row in rows if f.get("link") == "identity"
        ),
        "capped": sum(bool(f.get("capped")) for f in fits),
        "starts_completed": _spread(
            [float(f["starts_completed"]) for f in fits if "starts_completed" in f]
        ),
        "stored_uncertified": sum(bool(row["stored_uncertified"]) for _, row in rows),
        "oracle_search_misses": sum(o["search_call"] == "miss" for o in oracle),
        "oracle_search_below_truth_psi": sum(
            bool(o["search_below_truth_psi"]) for o in oracle
        ),
    }


def _s4(
    table: Table,
    new: Table,
    misses: Sequence[tuple[RowKey, Mapping[str, Json]]],
) -> Record:
    before = {row_key(r): r for r in table.rows}
    after = {row_key(r): r for r in new.rows}
    rows: list[Record] = []
    for key, row in misses:
        if key not in before:
            continue
        a, b = before[key], after[key]
        rows.append(
            {
                "key": list(key),
                "gap_stored": a.gap,
                "gap_corrected": b.gap,
                "gap_closed_stored": a.gap_closed,
                "gap_closed_corrected": b.gap_closed,
                "t4_flag_stored": a.closed_flagged,
                "t4_flag_corrected": b.closed_flagged,
                "similarity_stored": a.similarity,
                "similarity_corrected": unhex(row.get("corrected_similarity")),
            }
        )
    above = [
        r
        for r in new.rows
        if not r.llm and r.system != ORACLE and r.gap is not None and r.gap > GAP_GATE
    ]
    return {"rows": rows, "gaps_above_gate": len(above)}


def _s2_rows(
    records: Sequence[Mapping[str, Json]],
) -> tuple[Record, dict[RowKey, float]]:
    rows = [r for r in records if r["type"] == "done" and r.get("kind") == "s2"]
    extra: dict[RowKey, float] = {}
    for r in rows:
        value = unhex(r.get("corrected_per_event"))
        if r["status"] == "differs" and value is not None:
            extra[(r["truth"], r["seed"], B_SPARSE)] = value
    statuses = [r["status"] for r in rows]
    return (
        {
            "done": len(rows),
            "same": statuses.count("same"),
            "differs": statuses.count("differs"),
            "link_mismatch": statuses.count("link_mismatch"),
        },
        extra,
    )


def _count(names: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in names:
        out[name] = out.get(name, 0) + 1
    return dict(sorted(out.items()))


def _miss_rows(
    misses: Sequence[tuple[RowKey, Mapping[str, Json]]],
    census: Census,
    done: Mapping[str, Mapping[str, Json]],
) -> list[Record]:
    """Section 10's table: each miss row with its gain, Δ, readings and class."""
    states = {(r.truth, r.seed, r.system): row_state(r, done) for r in census.rows}
    return [
        {
            "key": list(key),
            "gain": row["gain"],
            "delta": row["delta"],
            "delta_kind": row["delta_kind"],
            "stored_per_event": row["stored_per_event"],
            "corrected_per_event": row["corrected_per_event"],
            "evidence": states.get(key),
            "stored_uncertified": row["stored_uncertified"],
            "stored_quadrature_error": row.get("stored_quadrature_error"),
            "chosen_quadrature_error": row.get("chosen_quadrature_error"),
            "within_quadrature": bool(row.get("within_quadrature")),
        }
        for key, row in misses
    ]


def _max_delta(misses: Sequence[tuple[RowKey, Mapping[str, Json]]]) -> Json:
    if any(row["delta_kind"] == "infinite_move" for _, row in misses):
        return "infinite_move"
    values = [abs(need_float(row["delta"])) for _, row in misses]
    return max(values) if values else None


def fit_views(records: Sequence[Mapping[str, Json]]) -> list[Mapping[str, Json]]:
    """Each fit's last stage on record: its ``done``, or else R1's result, so a
    job that died in R2 stays searched with any miss R1 found (PR 6)."""
    views = {
        r["job"]: r for r in records if r["type"] == "done" and r.get("kind") == "fit"
    }
    for r in records:
        if r["type"] == "r1" and "result" in r and r["job"] not in views:
            views[r["job"]] = {**r["result"], "job": r["job"]}
    return [views[job] for job in sorted(views)]


def _clock_stops(
    records: Sequence[Mapping[str, Json]],
    fits: Sequence[Mapping[str, Json]],
    end: str,
) -> Record:
    """The stops that read a clock, so a record that differs on a rerun says why
    (PR 2.10)."""
    others = [r for r in records if r["type"] == "done" and r.get("kind") != "fit"]
    failed = [r for r in records if r["type"] in ("r2_lost", "reference_failed")]
    return {
        "cpu_cut": sum(bool(r.get("cpu_cut")) for r in (*fits, *others)),
        "cpu_limit": sum(r.get("reason") == "cpu_limit" for r in failed),
        "r2_lost": sum(r["type"] == "r2_lost" for r in records),
        "group_c_cap": any(r["type"] == "stage_cap" for r in records),
        "cpu_stop": end == "cpu_stop",
    }


def decide(
    records: Sequence[Mapping[str, Json]],
    inputs: Inputs,
    reads: Reads,
    *,
    identity: Mapping[str, str],
    end: str,
    stop_note: str | None,
    end_note: str | None,
) -> Record:
    """The label and everything beside it, at the terminal step."""
    census, table = inputs.census, inputs.table
    fit_records = fit_views(records)
    done_fits = {r["job"]: r for r in fit_records}
    misses = reads.misses
    fits = census.fits()

    def complete(f: FitGroup) -> bool:
        return fit_job_id(f.truth, f.seed, f.text) in done_fits

    tier1 = [f for f in fits if f.tier == 1]
    tier2 = [f for f in fits if f.tier == 2]
    tier1_complete = all(complete(f) for f in tier1)
    h1 = h1_status(records)
    changed = [k for k in reads.stored if read_changed(reads.stored[k], reads.fixed[k])]
    both = [
        k
        for k in reads.stored
        if reads.stored[k].reportable and reads.fixed[k].reportable
    ]
    label = decide_label(
        byte_fail=False,
        changed=bool(changed),
        tier1_complete=tier1_complete,
        h1=h1.stands,
        any_reportable=bool(both),
    )
    used = frozenset(
        key
        for name in reads.stored
        for key in (*reads.stored[name].rows, *reads.fixed[name].rows)
    )
    counts = statement_counts(census, done_fits, used)
    n_used = sum(key in used for key, _ in misses)
    statement = None
    if label == "CONTRASTS_SURVIVE_FOUND_MISSES":
        statement = STATEMENT.format(n=n_used, **counts)
    found, total = s1_counts(fit_records, records)
    s1 = s1_status(found=found, total=total)
    hunt = hunt_summary(records)
    s2, _ = _s2_rows(records)
    lines = read_lines(reads, census, done_fits)
    reason = None
    if label == "CONTROL_FAIL":
        reason = "no read is reportable on both tables" if h1.stands else None
        reason = reason or "H1 is falsified"
    coverage = coverage_of(census, done_fits)
    deltas = [(unhex(row["delta"]), str(row["delta_kind"])) for _, row in misses]
    quadrature = [list(key) for key, row in misses if row.get("within_quadrature")]
    decision: Record = {
        **identity,
        "label": label,
        "exit_code": EXIT_CODES[label],
        "end": end,
        "partial": not tier1_complete,
        "next": NEXT[label],
        "reason": reason,
        "statement": statement,
        "reads": lines,
        "no_statement": [x["read"] for x in lines if x["status"] == NO_STATEMENT],
        "fixed2_differs": [
            k
            for k in reads.fixed
            if not contrasts.same_read(reads.fixed[k], reads.fixed2[k])
        ],
        "llm_caveat": {
            "text": LLM_CAVEAT,
            "reads": sorted(x["read"] for x in lines if x["llm_substituted"]),
        },
        "stop_note": stop_note,
        "end_note": end_note,
        "H1": {
            "falsified": not h1.stands,
            "group_a_caught": h1.a_caught,
            "group_a_of": h1.a_total,
            "group_b_caught": h1.b_caught,
            "group_d_caught": h1.d_caught,
            "cases": list(h1.cases),
        },
        "H2": {
            "falsified": len(misses) >= 1,
            "rests_on_within_quadrature": bool(quadrature)
            and len(quadrature) == len(misses),
        },
        "H3": {"falsified": h3_falsified(deltas), "vacuous": not misses},
        "H4": {
            "falsified": bool(changed),
            "changed": changed,
            "vacuous": not misses,
        },
        "M_rows": len(misses),
        "M_fits": sum(any(x["call"] == "miss" for x in r["rows"]) for r in fit_records),
        "M_by_system": _count(key[2] for key, _ in misses),
        "miss_rows": _miss_rows(misses, census, done_fits),
        "classes": _count(r.klass for r in census.rows),
        "within_quadrature_rows": quadrature,
        "clock_stops": _clock_stops(records, fit_records, end),
        "max_abs_delta": _max_delta(misses),
        "coverage": coverage,
        "control_state": _control_state(hunt),
        "strata": _strata(fits, done_fits, _control_state(hunt), h1),
        "hunt": hunt,
        "group_e": group_e_summary(records, census),
        "s1": {"found_by_r1": found, "misses": total},
        "s1_status": s1,
        "s2": s2,
        "s3": _s3(fit_records),
        "s4": _s4(table, reads.new, misses),
        "shortfall": {
            "tier1_missing": sum(not complete(f) for f in tier1),
            "tier2_missing": sum(not complete(f) for f in tier2),
            "s2_missing": sum(
                r.system == B_SPARSE and r.klass == "coordinate" for r in census.rows
            )
            - s2["done"],
        },
    }
    decision["label_line"] = label_line(decision)
    return decision


def label_line(d: Mapping[str, Json]) -> str:
    """Section 5's line: the label and what stands beside it."""
    coverage = d["coverage"]
    parts = [
        d["label"],
        f"H2 falsified={d['H2']['falsified']}",
        f"H3 falsified={d['H3']['falsified']}",
        f"M={d['M_rows']} rows, {d['M_fits']} fits",
        f"uncertified_better on identity {d['s3']['uncertified_better_identity']}",
        f"group A {d['H1']['group_a_caught']} of {d['H1']['group_a_of']}, group B "
        f"{d['H1']['group_b_caught']}, group D {d['H1']['group_d_caught']}",
        f"group E {d['group_e']['caught']} of {d['group_e']['of']}",
        f"max|delta|={d['max_abs_delta']}",
        f"exact {coverage['exact_fits']}, searched {coverage['searched_fits']}, "
        f"unchecked {coverage['unchecked_fits']}, capped {coverage['capped_fits']}",
        f"s1_status={d['s1_status']}",
    ]
    if d["s1_status"] == "below_floor":
        parts.append(S1_BELOW_FLOOR)
    for name, row in d["hunt"].items():
        parts.append(
            f"group C {name}: {row['completed']} cases completed, "
            f"{row['found_losses']} found losses, R1 run on {row['r1_run']}; "
            f"{d['control_state'][name]}"
        )
    for name, row in d["strata"].items():
        parts.append(f"{name}: {row['searched_fits']} searched fits, {row['control']}")
    for x in d["reads"]:
        if x["status"] == NO_STATEMENT:
            parts.append(f"{x['read']}: {NO_STATEMENT}")
            continue
        parts.append(
            f"{x['read']}: {x['status']}, T {x['t']}, E {x['e']}, U {x['u']}, X "
            f"{x['x']}, A {x['a']}, N {x['n']}, LLM substituted "
            f"{x['llm_substituted']} of {x['rows']} rows"
        )
    if d["fixed2_differs"]:
        names = ", ".join(d["fixed2_differs"])
        parts.append(f"fixed2 differs from fixed: {names}")
    parts.append(
        f"coverage: rows exact {coverage['exact_rows']}, searched "
        f"{coverage['searched_rows']}, unchecked {coverage['unchecked_rows']}, not "
        f"applicable {coverage['not_applicable_rows']}"
    )
    if d["statement"] is not None:
        parts.append(d["statement"])
    return " | ".join(parts)


# --------------------------------------------------------------------------
# The terminal step (sections 2.8, 5)
# --------------------------------------------------------------------------


def finished_decision(paths: OutPaths) -> Record | None:
    """What the terminal step already wrote, or None. A crash between the two
    renames leaves ``decision.json.pending`` whole, so this finishes the rename
    and computes nothing. Reads with neither file are refused: nothing may write
    a label beside reads it did not make."""
    if decision_is_final(paths):
        body: Record = json.loads(paths.decision.read_text(encoding="utf-8"))
        return body
    if not paths.reads.exists():
        return None
    if not paths.decision_temporary.exists():
        raise ResumeRefused("reads.json exists with no label: nothing rewrites it")
    publish(paths.decision_temporary, paths.decision)
    done: Record = json.loads(paths.decision.read_text(encoding="utf-8"))
    return done


def read_stop_note(paths: OutPaths) -> str | None:
    if not paths.stop.exists():
        return None
    return paths.stop.read_text(encoding="utf-8").strip() or "no reason was given"


def read_end_note(paths: OutPaths) -> str:
    """``--end``'s authority (section 2.8): a note saying who and why."""
    text = ""
    if paths.end_note.exists():
        text = paths.end_note.read_text(encoding="utf-8").strip()
    if not text:
        raise ResumeRefused(
            f"--end needs a note in {paths.end_note} saying who and why: only the "
            "owner or the control room may end a run that could still resume"
        )
    return text


def byte_failure_kinds(
    records: Sequence[Mapping[str, Json]],
    manifest_failures: Sequence[str],
    end: str,
) -> list[str]:
    """Each kind of failure on record. Kinds only: a reason can hold a reading,
    and it is in the journal."""
    kinds: list[str] = []
    if end == "byte_fail" or any(r["type"] == "byte_fail" for r in records):
        kinds.append("byte_check")
    if manifest_failures:
        kinds.append("store_manifest")
    return kinds


def seal(paths: OutPaths, body: Mapping[str, Json]) -> Record:
    """A decision with no reads beside it."""
    replace_file(paths.decision, (dumps_record(dict(body)) + "\n").encode("utf-8"))
    return dict(body)


def terminal_step(
    paths: OutPaths,
    inputs: Inputs,
    *,
    identity: Mapping[str, str],
    end: str,
    manifest_failures: Sequence[str],
) -> Record:
    """The one step that reads the contrasts, and it runs once (module docstring).
    Rule 1 comes first; then ``bug``, which evaluates no other rule."""
    finished = finished_decision(paths)
    if finished is not None:
        return finished
    records = read_records(paths.journal)
    faults = byte_failure_kinds(records, manifest_failures, end)
    units = b""
    reads: Reads | None = None
    if not faults and end != "bug":
        try:
            units = units_bytes(records)
            reads = compute_reads(records, inputs)
        except ByteFailure as error:
            faults.append(error.kind)
            fault = str(error)
        # Section 2.7: *an* exception from the read functions is an instrument
        # fault, whatever its type, and it ends in BYTE_FAIL. Nothing passes here.
        except Exception as error:
            faults.append("read_exception")
            fault = f"{type(error).__name__}: {error}"
        if faults:
            Journal(paths.journal).append(
                {"type": "terminal_fault", "kind": faults[-1], "reason": fault[:2000]}
            )
    if faults:
        label = decide_label(
            byte_fail=True,
            changed=False,
            tier1_complete=False,
            h1=False,
            any_reportable=False,
        )
        return seal(
            paths,
            {
                **identity,
                "label": label,
                "exit_code": EXIT_CODES[label],
                "end": end,
                "byte_failures": faults,
            },
        )
    if reads is None:
        return seal(paths, {**identity, "end": end})
    decision = decide(
        records,
        inputs,
        reads,
        identity=identity,
        end=end,
        stop_note=read_stop_note(paths),
        end_note=read_end_note(paths) if end == AUTHORISED_END else None,
    )
    replace_file(paths.units, units)
    write_temporary(
        paths.reads_temporary, (dumps_record(reads_body(reads)) + "\n").encode("utf-8")
    )
    write_temporary(
        paths.decision_temporary, (dumps_record(decision) + "\n").encode("utf-8")
    )
    publish(paths.reads_temporary, paths.reads)
    publish(paths.decision_temporary, paths.decision)
    return decision


def conclude(
    paths: OutPaths,
    inputs: Inputs,
    reason: str,
    *,
    identity: Mapping[str, str],
    manifest_failures: Sequence[str],
) -> Record | None:
    """What follows the driver: nothing on a resumable end, and otherwise the
    terminal step, with nothing chosen."""
    if reason in RESUMABLE_ENDS:
        return None
    return terminal_step(
        paths,
        inputs,
        identity=identity,
        end=reason,
        manifest_failures=manifest_failures,
    )


def finalise(
    paths: OutPaths,
    inputs: Inputs,
    *,
    identity: Mapping[str, str],
    manifest_failures: Sequence[str],
    end_flag: bool,
) -> Record:
    """``--finalise``. A run that could still resume is refused unless ``--end``
    comes with its note, and the choice goes to the journal before anything is
    read, so it is made once."""
    finished = finished_decision(paths)
    if finished is not None:
        return finished
    end = end_state(read_records(paths.journal), paths)
    if end in RESUMABLE_ENDS:
        if not end_flag:
            raise ResumeRefused(
                f"this run ended {end!r} and could still resume: --finalise needs "
                "--end, on the owner's or the control room's authority"
            )
        read_end_note(paths)
        Journal(paths.journal).append({"type": "run_end", "reason": AUTHORISED_END})
        end = AUTHORISED_END
    elif end_flag:
        raise ResumeRefused(f"this run ended {end!r}: --end has nothing to choose")
    return terminal_step(
        paths,
        inputs,
        identity=identity,
        end=end,
        manifest_failures=manifest_failures,
    )


def refuse(paths: OutPaths, reason: str) -> int:
    """A guard failed: the refusal alone, no label, and never over an end."""
    print(f"refused: {reason}", flush=True)
    paths.root.mkdir(parents=True, exist_ok=True)
    if not decision_is_final(paths) and not paths.reads.exists():
        body = {"refusal": reason}
        replace_file(paths.decision, (dumps_record(body) + "\n").encode("utf-8"))
    return NO_LABEL_EXIT
