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

**The launch** runs in a clean detached worktree at ``T``, with the line
:func:`launch_line` gives. ``--split`` is the dev split, for group B, and
``--test-split`` the test split, outside the repository. The output is a constant.

    uv run python scripts/p4_search_misses.py --print-hashes   # for the LOG at R2
    uv run python scripts/p4_search_misses.py --split <abs> --test-split <abs> \
        --store <abs> --finalise [--end]

**The rehearsal** (``--rehearsal --rehearsal-plan <json>``) changes exactly section
2.10's list, runs only on the allowlist of :func:`check_rehearsal` and
:func:`rehearsal_records`, and prefixes its label. The plan is JSON with the keys
:data:`REHEARSAL_PLAN_KEYS`, and ``truths`` and ``seeds`` are required.

**Processes and records** are as PR: a job is one fit in one process
(:class:`Driver`), ``journal.jsonl`` is appended and fsynced by whichever process
owns the job, ``units.jsonl`` is its job records sorted and without timing, and
floats are ``float.hex`` strings everywhere.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
import re
import resource
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from itertools import batched, product
from pathlib import Path
from types import FrameType
from typing import Any, ClassVar, Final, Literal, Protocol

import numpy as np
import pilot
from naming_null_record import NAMING_NULL_RECORD, TEST_SPLIT, read_naming_null

from sciagent.campaign import analysis, contrasts, cpu
from sciagent.campaign.analysis import Row, Table
from sciagent.campaign.contrasts import (
    ContrastRead,
    ControlsRead,
    FloorRead,
    ScriptedControls,
    contrast_rows,
)
from sciagent.campaign.execute import (
    UNIT_FAILURES,
    observational_data,
    score_submission,
)
from sciagent.campaign.fingerprint import (
    FINGERPRINT_FILE,
    CodeFingerprintError,
    read_fingerprints,
)
from sciagent.campaign.p4plan import p4_plan
from sciagent.campaign.plan import (
    B_LIB,
    B_NP,
    B_SPARSE,
    ORACLE,
    P4_SONNET_ARMS,
    TELLING,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    baseline_key,
    baseline_units,
    llm_units,
    score_key,
)
from sciagent.campaign.rescore import stored_sessions
from sciagent.campaign.store import ResultStore
from sciagent.glm.canonical import canonicalise
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import (
    FIT_VERSION,
    PSI_FULL_GRID_MAX,
    FitConfig,
    FitError,
    FitResult,
    _ascend,
    _Budget,
    _Point,
    _Profile,
    _search_blocks,
    _Slots,
    evaluate_log_likelihood,
    fit,
)
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    Feature,
    Gate,
    KernelKind,
    Link,
    Mark,
    One,
    Periodic,
    PhaseWindow,
    Structure,
    n_columns,
    psi_slots,
)
from sciagent.glm.grids import grid
from sciagent.glm.simulate import (
    Coefficients,
    PsiAssignment,
    SimulationError,
    simulate,
)
from sciagent.glm.syntax import parse, render
from sciagent.harness.live import MODELS
from sciagent.scenarios.records import canonical_json
from sciagent.scoring import gonogo
from sciagent.scoring.heldout import held_out_dataset
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.sparse import SparseConfig, split_dataset
from sciagent.systems.v2.systems import InvestigationData, Oracle, oracle_underfit

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
LOG_PATH: Final = "docs/v2/LOG.md"
SCRIPT_PATH: Final = "scripts/p4_search_misses.py"
TEST_PATH: Final = "tests/campaign/test_p4_search_misses.py"
FREEZE_PATH: Final = "docs/v2/FREEZE-6.1.txt"
TEST_SPLIT_HASH_PATH: Final = "docs/v2/freeze/test-split.sha256"
#: origin/v2 as this checkout last saw it: a guard fetches nothing.
ORIGIN_V2: Final = "refs/remotes/origin/v2"
#: The output, outside the repository, and not an option (section 2.8).
OUT_DIR: Final = Path("/home/ubuntu/research/records/sciagent/p4-search-misses-F2")
#: Section 2.10's "launch copy", "beside the results". The text names no key:
#: this is the parent of the check's own S3 path, for the launcher to match.
LAUNCH_FINGERPRINT_URI: Final = (
    "s3://delaymargin-results-482913/sciagent/p4/fingerprint.jsonl"
)
#: How S's LOG names R2. The text fixes no form: this is the LOG's own for F2.
R2_NAMED: Final = re.compile(r"`R2` = ([0-9a-f]{7,40})\b")
HEX_COMMIT: Final = re.compile(r"[0-9a-f]{7,40}")
SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
FINGERPRINT_COMMIT: Final = re.compile(r"commit=([0-9a-f]{40})(?:,|$)")
BLAS_VARIABLES: Final = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
)

#: The fit guard (section 2.10).
BASE_COMMIT: Final = "900e66a"
FIT_GUARD_PATHS: Final = (
    "src/sciagent/glm/fit.py",
    "src/sciagent/glm/fit_solve.py",
    "src/sciagent/glm/fit_bounds.py",
    "src/sciagent/glm/likelihood.py",
    "src/sciagent/glm/features.py",
    "src/sciagent/glm/grids.py",
)
#: The one changed line, the refusal text at ``features.py:417``.
FIT_DIFF_SHA256: Final = (
    "5ab0f61de1ccb4d722a1337b317428847c8ba17ec8ef05a1650e8b4ca2a08156"
)
REDUCTIONS_PATH: Final = "src/sciagent/core/reductions.py"
REDUCTIONS_BLOB: Final = "466612a978068c74be2c30cde01c3574c1d9696b"
FIT_VERSION_EXPECTED: Final = "sciagent.glm.fit/5"

#: PR 2.10's dev split, for group B; the test split's hash is at ``S``.
SPLIT_SHA256: Final = "31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577"
DEV_RELATIVE: Final = "data/v2/truths/dev"
TEST_RELATIVE: Final = "data/v2/truths/test"
DEV_SPLIT: Final = "dev"

#: The rehearsal (section 2.10). Output and store sit under ``DRAFTS``, relative
#: to the repository, and a directory is named by the script and the plan, so a
#: repeat after a fix is not a fresh start over another run's records.
DRAFTS: Final = Path(".cache") / "drafts"
REHEARSAL_DIR: Final = "p4-search-misses-rehearsal"
REHEARSAL_HUNT_SIZE: Final = 5
#: "the first eight dev truths by sorted id ... two seeds".
REHEARSAL_TRUTHS: Final = 8
REHEARSAL_SEEDS: Final = 2
REHEARSAL_MIN_KEPT: Final = 6
REHEARSAL_MIN_OUT_OF_DICTIONARY: Final = 2
REHEARSAL_PLAN_KEYS: Final = (
    "truths",
    "seeds",
    "control_seeds",
    "fits",
    "experiments",
    "control_factor",
    "gp_population",
    "gp_offspring",
)
#: How far past zero the planted shift carries a read.
PLANTED_MARGIN: Final = 0.0625


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
#: From the pilot check's ``units.jsonl``: section 2.9's three hex constants.
GROUP_B_PER_EVENT: Final = "-0x1.4051a13cc7023p-1"
GROUP_B_SEARCH_LL: Final = "-0x1.86fd12dec695ap+9"
GROUP_B_KEPT_LL: Final = "-0x1.84b974f87ed40p+9"
GROUP_B_TEXT: Final = (
    "link=exp; Excite(ExpK, One, sign=-) + Product(Excite(ExpK, Pow(size), all), "
    "Product(Excite(ExpK, Mark(size), all), Excite(ExpK, Pow(size), all)))"
)

K_STARTS: Final = 48
S2_STARTS: Final = 16
#: R1's new inner solves per fit, by link (section 2.2: as PR 2.2).
R1_POINT_CAP: Final = 8_000
R1_POINT_CAP_IDENTITY: Final = 3_000
#: S2's reference: new solves per link.
S2_POINT_CAP: Final = 2_000
R2_GRID_MAX: Final = 14_400
#: Points per ``evaluate`` call in R2 (a memory bound; no number depends on it).
R2_CHUNK: Final = 2000

#: Cases per sub-stratum.
HUNT_SIZE: Final = 150
#: R1 runs on this many found losses per sub-stratum, in index order.
HUNT_R1_PER_STRATUM: Final = 25
#: CPU hours of the whole of group C, R1 included.
HUNT_CPU_HOURS: Final = 6.0
HUNT_HORIZON: Final = 300.0

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


def s2_case(truth: str, seed: int, text: str) -> str:
    """PR 2.2's case for S2, on the job and on both of its searches."""
    return f"s2|{fit_case(truth, seed, text)}"


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
    """Before a resume or the terminal step appends: drop a torn fragment, which
    was never a record. Appended to, it would merge with the new line into one that
    ends in a newline, so nothing could tell it was torn and no label could follow."""
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
    #: Section 2.10: the planted shift, and the ``REHEARSAL`` prefix on the label.
    rehearsal: bool = False

    def __post_init__(self) -> None:
        if self.rehearsal and self.roots != REHEARSAL_ROOTS:
            raise GuardFailure("a rehearsal runs on the test roots and no other root")


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
    rehearsal: bool = False
    #: What a rehearsal planted: None if no read was reportable.
    planted: Record | None = None


def planted_shift(
    stored: Mapping[str, ContrastRead],
    table: Table,
    corrected: Mapping[RowKey, float],
) -> tuple[dict[RowKey, float], Record | None]:
    """Section 2.10: a shift on one read's first side, to show a read flip. The
    first reportable read, by name, moves past zero by more than its interval's
    width. A rehearsal alone calls this, and the mode cannot force a gate: with no
    reportable read nothing is planted."""
    rows = {row_key(r): r for r in table.rows}
    for spec in contrasts.READS:
        read = stored[spec.name]
        if not read.reportable:
            continue
        size = 2.0 * abs(read.point) + (read.high - read.low) + PLANTED_MARGIN
        shift = -size if read.point >= 0.0 else size
        planted: dict[RowKey, float] = {}
        for key in read.rows:
            value = corrected.get(key, rows[key].per_event)
            if key[2] == spec.first and value is not None and math.isfinite(value):
                planted[key] = value + shift
        return planted, {"read": spec.name, "shift": shift, "rows": len(planted)}
    return {}, None


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
    shifted: dict[RowKey, float] = {}
    planted: Record | None = None
    if inputs.rehearsal:
        shifted, planted = planted_shift(stored, table, corrected)
    new = corrected_table(table, corrected | shifted, underfit)
    new2 = corrected_table(table, corrected | s2_extra | shifted, underfit)
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
        inputs.rehearsal,
        planted,
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
    planted = {"planted_shift": reads.planted} if reads.rehearsal else {}
    return {
        **planted,
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


def h1_json(h1: H1Status) -> Record:
    return {
        "falsified": not h1.stands,
        "group_a_caught": h1.a_caught,
        "group_a_of": h1.a_total,
        "group_b_caught": h1.b_caught,
        "group_d_caught": h1.d_caught,
        "cases": list(h1.cases),
    }


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
    return [views[job] for job in sorted(views) if not _is_group_b(views[job])]


def _is_group_b(view: Mapping[str, Json]) -> bool:
    """Group B is a dev unit, so its fit is in no count of P4's rows."""
    control = view.get("control")
    return control is not None and control["group"] == "B"


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
        "label": shown_label(label, inputs),
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
        "H1": h1_json(h1),
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
        "failed_units": [
            [r.truth, r.seed, r.system] for r in census.rows if r.klass == "failed"
        ],
        "void_units": sorted(
            [x.truth, x.seed, x.system] for x in table.sessions if x.void.void
        ),
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
    if inputs.rehearsal:
        decision["rehearsal"] = rehearsal_bar(reads, table, len(both))
    decision["label_line"] = label_line(decision)
    return decision


def shown_label(label: str, inputs: Inputs) -> str:
    """Section 2.10: a rehearsal's label is prefixed, so it cannot pass for P4's."""
    return f"REHEARSAL {label}" if inputs.rehearsal else label


def rehearsal_shown(kept: int, out_of_dictionary: int, reportable: int) -> bool:
    """Section 2.10's bar. Control 2's exact test cannot pass on fewer than 5
    truths, and the mode cannot force a gate."""
    return (
        kept >= REHEARSAL_MIN_KEPT
        and out_of_dictionary >= REHEARSAL_MIN_OUT_OF_DICTIONARY
        and reportable >= 1
    )


def rehearsal_bar(reads: Reads, table: Table, reportable: int) -> Record:
    kept = reads.controls["stored"].kept
    outside = sum(not table.truths[t].in_dictionary for t in kept)
    return {
        "kept": len(kept),
        "kept_out_of_dictionary": outside,
        "reportable_on_both": reportable,
        "shown": rehearsal_shown(len(kept), outside, reportable),
    }


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


def authority_notes(paths: OutPaths, end: str) -> Record:
    """Who stopped or ended the run, on a decision with no reads: section 2.8 and
    PR 9 copy the note to ``decision.json`` on every end it authorises. A note
    holds no score, so "the label or end alone" does not exclude it."""
    notes: Record = {}
    stop = read_stop_note(paths)
    if stop is not None:
        notes["stop_note"] = stop
    if end == AUTHORISED_END:
        notes["end_note"] = read_end_note(paths)
    return notes


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
            repair_tail(paths.journal)
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
                "label": shown_label(label, inputs),
                "exit_code": EXIT_CODES[label],
                "end": end,
                "byte_failures": faults,
                **authority_notes(paths, end),
            },
        )
    if reads is None:
        return seal(paths, {**identity, "end": end, **authority_notes(paths, end)})
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
        repair_tail(paths.journal)
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


# --------------------------------------------------------------------------
# Start-up guards (section 2.10)
# --------------------------------------------------------------------------


def initial_environment() -> dict[str, str]:
    """The environment this process was started with. ``import pilot`` sets the
    BLAS variables in ``os.environ``, so that cannot show they were set before
    numpy loaded."""
    data = Path("/proc/self/environ").read_bytes()
    pairs = (item.partition(b"=") for item in data.split(b"\0") if item)
    return {k.decode(): v.decode() for k, _, v in pairs}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _git_out(repo: Path, *args: str) -> str | None:
    """Stripped output, or None if git refused."""
    done = _git(repo, *args)
    return done.stdout.strip() if done.returncode == 0 else None


def _blob_sha(repo: Path, commit: str, path: str) -> str | None:
    """sha256 of a file at a commit, by its bytes; None if it is not there."""
    done = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=repo, capture_output=True, check=False
    )
    return hashlib.sha256(done.stdout).hexdigest() if done.returncode == 0 else None


def check_f2(repo: Path, prereg_sha: str, f2_commit: str) -> None:
    """``PREREG_SHA256`` and ``F2_COMMIT`` match, in the tree and at ``F2``."""
    prereg = repo / PREREG_PATH
    if not prereg.is_file():
        raise GuardFailure(f"no prereg at {prereg}")
    digest = hashlib.sha256(prereg.read_bytes()).hexdigest()
    if digest != prereg_sha:
        raise GuardFailure(f"prereg hash {digest} is not PREREG_SHA256 {prereg_sha}")
    if _git(repo, "merge-base", "--is-ancestor", f2_commit, "HEAD").returncode != 0:
        raise GuardFailure(f"HEAD does not descend from F2 {f2_commit}")
    if _blob_sha(repo, f2_commit, PREREG_PATH) != prereg_sha:
        raise GuardFailure(f"the frozen text is not the prereg at F2 {f2_commit}")


def check_chain(repo: Path) -> tuple[str, str]:
    """HEAD is ``T``, and ``S`` is the commit before it (section 2.10). Returns
    ``(S, T)``."""
    log = _git_out(repo, "log", "--format=%H", "--", FREEZE_PATH)
    commits = (log or "").split()
    if len(commits) != 1:
        raise GuardFailure(
            f"{FREEZE_PATH} must show exactly one commit, shows {len(commits)}"
        )
    t = commits[0]
    head = _git_out(repo, "rev-parse", "HEAD")
    if head != t:
        raise GuardFailure(f"HEAD {head} is not T {t}")
    parents = (_git_out(repo, "rev-list", "--parents", "-n", "1", t) or "").split()[1:]
    if len(parents) != 1:
        raise GuardFailure(f"T {t} must have one parent, has {len(parents)}")
    s = parents[0]
    text = _git_out(repo, "show", f"{t}:{FREEZE_PATH}") or ""
    lines = text.splitlines()
    if len(lines) != 1 or not HEX_COMMIT.fullmatch(lines[0].strip()):
        raise GuardFailure(f"{FREEZE_PATH} must be one line naming S")
    named = _git_out(
        repo, "rev-parse", "--verify", "-q", f"{lines[0].strip()}^{{commit}}"
    )
    if named != s:
        raise GuardFailure(f"{FREEZE_PATH} names {lines[0]}, T's parent is {s}")
    changed = (_git_out(repo, "diff", "--name-only", s, t) or "").split()
    if changed != [FREEZE_PATH]:
        raise GuardFailure(f"T's diff must be {FREEZE_PATH} alone, lists {changed}")
    chain = _git_out(repo, "rev-list", "--first-parent", ORIGIN_V2)
    if chain is None or t not in chain.split():
        raise GuardFailure(f"T {t} is not on origin/v2's first-parent chain")
    return s, t


def check_hashes(repo: Path, s: str, f2_commit: str) -> str:
    """The script's and test's sha256 are in the LOG at ``S``, not the working
    LOG, and that LOG names ``R2`` as ``` `R2` = <commit> ```. ``R2`` is a commit
    between ``F2`` and ``S`` that holds these same bytes. Returns ``R2``."""
    log = _git_out(repo, "show", f"{s}:{LOG_PATH}") or ""
    digests: dict[str, str] = {}
    for path in (SCRIPT_PATH, TEST_PATH):
        digests[path] = hashlib.sha256((repo / path).read_bytes()).hexdigest()
        if digests[path] not in log:
            raise GuardFailure(f"{path} hash {digests[path]} is not in S's LOG")
    named = sorted(set(R2_NAMED.findall(log)))
    if len(named) != 1:
        raise GuardFailure(f"S's LOG must name one R2, names {named}")
    r2 = _git_out(repo, "rev-parse", "--verify", "-q", f"{named[0]}^{{commit}}")
    if r2 is None:
        raise GuardFailure(f"R2 {named[0]} is no commit here")
    for ancestor, descendant in ((f2_commit, r2), (r2, s)):
        if _git(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode:
            raise GuardFailure(f"R2 {r2} does not lie between F2 and S")
    for path, digest in digests.items():
        if _blob_sha(repo, r2, path) != digest:
            raise GuardFailure(f"R2 {r2} does not hold this {path}")
    return r2


def read_launch_copy() -> bytes:
    """The copy of ``fingerprint.jsonl`` that went to S3 at the launch."""
    try:
        done = subprocess.run(
            ["aws", "s3", "cp", LAUNCH_FINGERPRINT_URI, "-"],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise GuardFailure(f"no launch copy: {error}") from error
    if done.returncode != 0:
        raise GuardFailure(f"no launch copy at {LAUNCH_FINGERPRINT_URI}")
    return done.stdout


def check_store_witness(store: Path, t: str, launch_copy: Callable[[], bytes]) -> None:
    """The store was begun at ``T`` and never reused, so ``S`` is tied to the
    campaign: repointing the freeze file gives another commit and fails here."""
    try:
        entries = read_fingerprints(store)
    except CodeFingerprintError as error:
        raise GuardFailure(f"unreadable fingerprint sidecar: {error}") from error
    if len(entries) != 1 or entries[0].event != "establish":
        raise GuardFailure(
            f"{FINGERPRINT_FILE} must be one establish line and no reuse line"
        )
    found = FINGERPRINT_COMMIT.match(entries[0].fingerprint)
    if found is None or found.group(1) != t:
        raise GuardFailure(f"the store witness {entries[0].fingerprint} is not T {t}")
    if (store / FINGERPRINT_FILE).read_bytes() != launch_copy():
        raise GuardFailure(f"{FINGERPRINT_FILE} is not the launch copy")


def fit_diff_digest(repo: Path, base: str) -> str:
    """sha256 of section 2.10's ``git diff --full-index``, by its bytes, with the
    options that local git configuration could otherwise change."""
    done = subprocess.run(
        [
            "git",
            "diff",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            base,
            "HEAD",
            "--",
            *FIT_GUARD_PATHS,
        ],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise GuardFailure(f"the fit guard's git diff failed: {done.stderr!r}")
    return hashlib.sha256(done.stdout).hexdigest()


def check_fit(
    repo: Path, base_commit: str, fit_diff_sha: str, reductions_blob: str
) -> None:
    """Section 2.10's fit guard: the one-line diff and nothing else, ``fit/5``, and
    ``reductions.py`` by its blob."""
    if FIT_VERSION != FIT_VERSION_EXPECTED:
        raise GuardFailure(f"FIT_VERSION is {FIT_VERSION}, not fit/5")
    digest = fit_diff_digest(repo, base_commit)
    if digest != fit_diff_sha:
        raise GuardFailure(f"the fit code's diff {digest} is not {fit_diff_sha}")
    blob = _git_out(repo, "rev-parse", f"HEAD:{REDUCTIONS_PATH}")
    if blob != reductions_blob:
        raise GuardFailure(f"reductions.py blob {blob} is not {reductions_blob}")


def check_tree(repo: Path, packages: Sequence[Path] | None) -> None:
    try:
        pilot.code_fingerprint(repo, packages)
    except CampaignError as error:
        raise GuardFailure(str(error)) from error


def check_blas(environ: Mapping[str, str]) -> None:
    for name in BLAS_VARIABLES:
        if environ.get(name) != "1":
            raise GuardFailure(f"{name} must be 1 at start, is {environ.get(name)!r}")


def run_guards(
    repo: Path,
    *,
    prereg_sha: str,
    f2_commit: str,
    base_commit: str,
    fit_diff_sha: str,
    reductions_blob: str,
    environ: Mapping[str, str],
    store: Path,
    launch_copy: Callable[[], bytes],
    packages: Sequence[Path] | None = None,
) -> dict[str, str]:
    """Every start-up guard of section 2.10; raises :class:`GuardFailure`. Returns
    what ``decision.json`` records."""
    check_f2(repo, prereg_sha, f2_commit)
    check_tree(repo, packages)
    s, t = check_chain(repo)
    r2 = check_hashes(repo, s, f2_commit)
    check_store_witness(store, t, launch_copy)
    check_fit(repo, base_commit, fit_diff_sha, reductions_blob)
    check_blas(environ)
    return {"F2": f2_commit, "R2": r2, "S": s, "T": t, "prereg_sha256": prereg_sha}


def main_checkout(repo: Path) -> Path:
    """The directory that holds the repository's ``.git``. The script runs in
    ``T``'s worktree, so "outside the repository" means outside this one too."""
    common = _git_out(repo, "rev-parse", "--git-common-dir")
    if common is None:
        raise GuardFailure(f"git knows no repository at {repo}")
    return (repo / common).resolve().parent


def check_paths(
    *, split: Path, test_split: Path, store: Path, out: Path, repo: Path
) -> None:
    named = (("--split", split), ("--test-split", test_split), ("--store", store))
    for name, path in named:
        if not path.is_absolute():
            raise GuardFailure(f"{name} must be an absolute path: {path}")
    for checkout in (repo, main_checkout(repo)):
        if test_split.resolve().is_relative_to(checkout.resolve()):
            raise GuardFailure(f"--test-split {test_split} must be outside {checkout}")
    for parent in (repo, store):
        if out.resolve().is_relative_to(parent.resolve()):
            raise GuardFailure(f"the output {out} must be outside {parent}")


def split_digest(split: Path, relative: str) -> str:
    """``find <relative> -type f -print0 | sort -z | xargs -0 sha256sum |
    sha256sum``, from the root of whichever checkout holds the split."""
    named: list[tuple[bytes, Path]] = []
    for path in split.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        text = f"{relative}/{path.relative_to(split).as_posix()}"
        if "\\" in text or "\n" in text:
            raise GuardFailure(f"sha256sum would escape the name {text!r}")
        named.append((text.encode("utf-8"), path))
    lines = b"".join(
        hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"  " + name + b"\n"
        for name, path in sorted(named)
    )
    return hashlib.sha256(lines).hexdigest()


def check_split(split: Path, expected: str, relative: str) -> None:
    digest = split_digest(split, relative)
    if digest != expected:
        raise GuardFailure(f"split hash {digest} is not {expected}")


def test_split_expected(repo: Path, s: str) -> str:
    """The test split's hash, as ``S`` holds it."""
    text = _git_out(repo, "show", f"{s}:{TEST_SPLIT_HASH_PATH}") or ""
    fields = text.split()
    if not fields or not SHA256_HEX.fullmatch(fields[0]):
        raise GuardFailure(f"no test split hash at {s}:{TEST_SPLIT_HASH_PATH}")
    return fields[0]


def store_manifest(root: Path) -> dict[str, str]:
    """sha256 of every file under the store, by relative path."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def manifest_diff(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    out = [f"added {p}" for p in sorted(set(after) - set(before))]
    out += [
        f"changed {p}"
        for p in sorted(set(before) & set(after))
        if before[p] != after[p]
    ]
    out += [f"removed {p}" for p in sorted(set(before) - set(after))]
    return out


def scripted_controls(
    t_commit: str, *, split: str = TEST_SPLIT, path: Path = NAMING_NULL_RECORD
) -> ScriptedControls:
    """Control 4's pass state, from its record alone (section 2.7). ``t_commit`` is
    ``T`` and not HEAD, because a ``T2`` run differs from it. No option reaches
    this: only ``main`` calls it, with constants."""
    return ScriptedControls(read_naming_null(t_commit, split=split, path=path))


def build_parser() -> argparse.ArgumentParser:
    """No option sets a number the evaluation reads, a root, or the output."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--split", type=Path, help="the dev split, absolute, for group B")
    p.add_argument(
        "--test-split", type=Path, help="the test split, absolute, outside the repo"
    )
    p.add_argument("--store", type=Path, help="absolute path")
    p.add_argument("--workers", type=int, choices=(1, 2), default=2)
    p.add_argument(
        "--finalise",
        action="store_true",
        help="the terminal step, from the records, after any end",
    )
    p.add_argument(
        "--end",
        action="store_true",
        help="with --finalise: end a run that could still resume. Needs a note in "
        "END saying who and why: the owner's or the control room's authority",
    )
    p.add_argument(
        "--print-hashes",
        action="store_true",
        help="print the script's and the test's sha256, for the LOG entry at R2",
    )
    p.add_argument(
        "--rehearsal",
        action="store_true",
        help="section 2.10's rehearsal, on a dev store under .cache/drafts",
    )
    p.add_argument(
        "--rehearsal-plan",
        type=Path,
        help="the rehearsal's truths and budget, as JSON: REHEARSAL_PLAN_KEYS",
    )
    return p


# --------------------------------------------------------------------------
# R1 and R2 (PR 2.2), as the pilot's
# --------------------------------------------------------------------------


class ContaminatedProfile(RuntimeError):
    """R1 was handed a profile that already held points (PR 2.2)."""


def t2_case(which: int, link: str, seed: int) -> str:
    return f"t2|{which}|{link}|{seed}"


def hunt_case(stratum: str, i: int) -> str:
    return f"hunt|{stratum}|{i}"


def r1_cap(link: str) -> int:
    return R1_POINT_CAP_IDENTITY if link == Link.IDENTITY.value else R1_POINT_CAP


@dataclass(frozen=True)
class PointValue:
    """One ψ point as a search saw it: ``score`` is ``-primal``."""

    point: _Point
    score: float
    gap: float
    certified: bool


def choose_point(r0: PointValue, reference: PointValue | None) -> PointValue | None:
    """The highest-scoring certified point of R0 to R2, ties to the lowest index
    tuple. ``reference`` is the certified best of R1 and R2."""
    candidates = [v for v in (r0, reference) if v is not None and v.certified]
    if not candidates:
        return None
    return min(candidates, key=lambda v: (-v.score, v.point))


def count_uncertified_better(
    values: Iterable[PointValue], chosen: PointValue | None
) -> int:
    """Uncertified points above the chosen one: seen, counted, never used."""
    floor = -math.inf if chosen is None else chosen.score
    return sum(not v.certified and v.score > floor for v in values)


def within_quadrature(gain: float, stored_error: float, chosen_error: float) -> bool:
    """PR 2.3: L is exact only up to ``quadrature_error``, which differs by ψ.
    Such a miss stays in M and is listed."""
    return gain <= abs(stored_error) + abs(chosen_error)


def found_loss(base: Valued, generating: Valued) -> bool:
    """PR 2.9: the fixed-ψ fit is certified and beats fit/5's L by more than the
    tolerance. fit/5's own flag is no condition: it is false when any visited
    point was uncertified."""
    return generating.certified and call_miss(generating, base).kind == "miss"


@dataclass(frozen=True)
class R1Outcome:
    starts: tuple[_Point, ...]
    n_ascents: int
    capped: bool
    n_points: int
    best: PointValue | None
    #: The uncertified points R1 reached, for ``uncertified_better``.
    uncertified: tuple[PointValue, ...] = ()
    n_infeasible: int = 0
    cpu_cut: bool = False


@dataclass(frozen=True)
class ReferenceOutcome:
    r1: R1Outcome
    r2_complete: bool
    #: Certified best of everything the profile holds.
    best: PointValue | None
    profile: _Profile
    cpu_cut: bool = False

    @property
    def uncertified(self) -> tuple[PointValue, ...]:
        return tuple(v for v in point_values(self.profile) if not v.certified)


def fresh_profile(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
) -> _Profile:
    return _Profile(structure, tuple(datasets), channels, FitConfig(), None)


def draw_starts(slots: _Slots, rng: np.random.Generator, k: int) -> list[_Point]:
    return [tuple(int(rng.integers(len(g))) for g in slots.grids) for _ in range(k)]


def point_values(profile: _Profile) -> list[PointValue]:
    """Every solved point of the profile, in index order."""
    out: list[PointValue] = []
    for point in sorted(profile.solutions):
        solution = profile.solutions[point]
        if solution is None:
            continue
        out.append(
            PointValue(point, profile.score(point), solution.gap, solution.certified)
        )
    return out


def best_certified(profile: _Profile) -> PointValue | None:
    usable = [
        v for v in point_values(profile) if v.certified and math.isfinite(v.score)
    ]
    return min(usable, key=lambda v: (-v.score, v.point)) if usable else None


def run_r1(
    profile: _Profile,
    rng: np.random.Generator,
    *,
    k: int,
    cap: int,
    should_stop: Callable[[], bool] | None = None,
) -> R1Outcome:
    """K seeded ascents with fit/5's own ``_ascend`` and blocks. The cap is a
    solve count, so where it stops is deterministic; ``should_stop`` reads a
    clock, between starts, and is flagged ``cpu_cut`` when it fires."""
    if profile.solutions or profile.solves[0]:
        raise ContaminatedProfile(
            f"R1 needs an empty profile; this one holds {len(profile.solutions)} points"
        )
    starts = draw_starts(profile.slots, rng, k)
    blocks = _search_blocks(profile.slots, profile.config.psi_block_max)
    finished, capped, cut = 0, False, False
    try:
        for start in starts:
            if should_stop is not None and should_stop():
                cut = True
                break
            _ascend(profile, start, blocks, cap)
            finished += 1
    except _Budget:
        capped = True
    values = point_values(profile)
    return R1Outcome(
        tuple(starts),
        finished,
        capped,
        len(profile.solutions),
        best_certified(profile),
        tuple(v for v in values if not v.certified),
        len(profile.solutions) - len(values),
        cut,
    )


def run_r2(profile: _Profile, should_stop: Callable[[], bool] | None = None) -> bool:
    """Every remaining point of the product grid, on the same profile. False if
    ``should_stop`` ended it between chunks."""
    ranges = (range(len(g)) for g in profile.slots.grids)
    for chunk in batched(product(*ranges), R2_CHUNK):
        if should_stop is not None and should_stop():
            return False
        profile.evaluate(list(chunk))
    return True


def reference_search(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    rng: np.random.Generator,
    *,
    k: int,
    cap: int,
    r2_max: int,
    on_r1: Callable[[R1Outcome], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ReferenceOutcome:
    """R1, then R2 where the grid allows. Blind by construction: the arguments are
    the structure, its data and a stream, and the profile is its own. ``on_r1``
    runs before R2 starts, so R1's result is on record first."""
    profile = fresh_profile(structure, datasets, channels)
    r1 = run_r1(profile, rng, k=k, cap=cap, should_stop=should_stop)
    if on_r1 is not None:
        on_r1(r1)
    complete, cut = False, r1.cpu_cut
    if profile.slots.size <= r2_max and not cut:
        complete = run_r2(profile, should_stop)
        cut = not complete
    return ReferenceOutcome(r1, complete, best_certified(profile), profile, cut)


def past_job_cut() -> bool:
    """This process's CPU against :data:`JOB_CUT_HOURS`: a clock, so a fit it fires
    on is ``cpu_cut`` and the record says so."""
    return time.process_time() >= JOB_CUT_HOURS * 3600.0


class JobCut:
    """:func:`past_job_cut`, remembering that it fired. An S2 job runs up to four
    searches, and a job's record has to name the cut (PR 2.10, PR 6) whichever of
    them it landed in. The clock only goes forward, so a cut stays a cut."""

    def __init__(self) -> None:
        self.fired = False

    def __call__(self) -> bool:
        self.fired = self.fired or past_job_cut()
        return self.fired


def refit_at(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    value: PointValue,
) -> FitResult:
    """The public refit of a searched point; it must be that point's solve."""
    psi = _Slots.of(structure).assignment(value.point)
    result = fit(structure, datasets, channels, config=FitConfig(), psi=psi)
    if -result.primal_objective != value.score:
        raise ByteFailure(
            f"refit at {value.point} gives {(-result.primal_objective).hex()}, the "
            f"search gave {value.score.hex()}"
        )
    return result


def valued_fit(result: FitResult) -> Valued:
    return Valued(result.log_likelihood, result.duality_gap, result.certified)


def known_value(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    psi: PsiAssignment,
) -> Valued:
    """A control's known better value, on the public fit's own profile. Only a
    job's control half calls this, after the reference result is recorded."""
    return valued_fit(fit(structure, datasets, channels, config=FitConfig(), psi=psi))


def valued_json(value: Valued) -> Record:
    return {"ll": value.ll, "gap": value.gap, "certified": value.certified}


# --------------------------------------------------------------------------
# T2's cases, as tests/glm/test_fit_search.py draws them
# --------------------------------------------------------------------------

T2_CHANNELS: Final = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
)
T2_HORIZON: Final = 300.0
_EXP_K: Final = Excite(KernelKind.EXP, One(), ALL)
_SIZE_K: Final = Excite(KernelKind.EXP, Mark("size"), ALL)
_POWER_K: Final = Excite(KernelKind.POWER, One(), ALL)
_WINDOW: Final = Gate(Periodic(), PhaseWindow())
T2_BIG: Final[tuple[tuple[Feature, ...], ...]] = (
    (Gate(_EXP_K, PhaseWindow()), Gate(_SIZE_K, PhaseWindow())),
    (_EXP_K, _SIZE_K, _WINDOW),
    (Gate(_POWER_K, PhaseWindow()), _WINDOW),
    (_POWER_K, _WINDOW),
)
#: Above every T2 grid (spike, session 28): three gated kernels.
_LARGE: Final[tuple[Feature, ...]] = (
    Gate(_EXP_K, PhaseWindow()),
    Gate(_SIZE_K, PhaseWindow()),
    Gate(_POWER_K, PhaseWindow()),
)


def t2_marks(rng: np.random.Generator) -> Mapping[str, float]:
    return {"size": float(rng.exponential(1.0))}


def t2_random_point(structure: Structure, rng: np.random.Generator) -> PsiAssignment:
    return tuple(
        {s: grid(s.name)[int(rng.integers(len(grid(s.name))))] for s in psi_slots(f)}
        for f in structure.features
    )


def t2_random_coefficients(
    structure: Structure, rng: np.random.Generator
) -> Coefficients:
    return Coefficients(
        0.3,
        tuple(
            tuple(float(rng.uniform(-1.5, -0.5)) for _ in range(n_columns(f)))
            if not isinstance(f, Gate) or not isinstance(f.feature, Periodic)
            else (0.4, -0.3)
            for f in structure.features
        ),
    )


def _bare_periodic(feature: Feature) -> bool:
    while isinstance(feature, Gate):
        feature = feature.feature
    return isinstance(feature, Periodic)


def identity_coefficients(
    structure: Structure, rng: np.random.Generator
) -> Coefficients:
    """Non-negative excitation, so an identity-link truth stays above zero."""
    return Coefficients(
        0.5,
        tuple(
            (0.2, -0.15)
            if _bare_periodic(f)
            else tuple(float(rng.uniform(0.1, 0.4)) for _ in range(n_columns(f)))
            for f in structure.features
        ),
    )


@dataclass(frozen=True)
class SyntheticCase:
    structure: Structure
    generating: PsiAssignment
    coefficients: Coefficients
    log: EventLog


def t2_log(which: int, link: str, seed: int) -> SyntheticCase:
    structure = Structure(T2_BIG[which], Link(link))
    rng = np.random.default_rng(seed)
    generating = t2_random_point(structure, rng)
    coefficients = t2_random_coefficients(structure, rng)
    log = simulate(
        structure, generating, coefficients, T2_CHANNELS, t2_marks, T2_HORIZON, rng
    )
    return SyntheticCase(structure, generating, coefficients, log)


@dataclass(frozen=True)
class HuntStratum:
    """One sub-stratum of PR 2.9's table. Its position ``s`` in
    :data:`HUNT_STRATA` is part of the seed."""

    name: str
    features: tuple[Feature, ...]
    link: Link
    coefficients: Callable[[Structure, np.random.Generator], Coefficients]


HUNT_STRATA: Final = (
    HuntStratum("id-24000", T2_BIG[2], Link.IDENTITY, identity_coefficients),
    HuntStratum("id-large", _LARGE, Link.IDENTITY, identity_coefficients),
    HuntStratum("large-exp", _LARGE, Link.EXP, t2_random_coefficients),
    HuntStratum("large-softplus", _LARGE, Link.SOFTPLUS, t2_random_coefficients),
)


def hunt_log(
    stratum: str, j: int, log_root: int, *, horizon: float = HUNT_HORIZON
) -> SyntheticCase:
    """Case ``j`` of a sub-stratum, drawn as PR 2.9 fixes it: the point, the
    coefficients, then the log, from one stream."""
    s, spec = next((s, x) for s, x in enumerate(HUNT_STRATA) if x.name == stratum)
    structure = Structure(spec.features, spec.link)
    seq = np.random.SeedSequence([log_root, 1000 * s + j])
    rng = np.random.default_rng(seq)
    generating = t2_random_point(structure, rng)
    coefficients = spec.coefficients(structure, rng)
    log = simulate(
        structure, generating, coefficients, T2_CHANNELS, t2_marks, horizon, rng
    )
    return SyntheticCase(structure, generating, coefficients, log)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def best_json(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    value: PointValue | None,
) -> Record | None:
    """A search's best with the public L, the value every comparison uses."""
    if value is None:
        return None
    result = refit_at(structure, datasets, channels, value)
    return {
        "point": list(value.point),
        "score": value.score,
        "gap": value.gap,
        "ll": result.log_likelihood,
    }


def r1_payload(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    r1: R1Outcome,
) -> Record:
    return {
        "n_starts": len(r1.starts),
        "starts_completed": r1.n_ascents,
        "capped": r1.capped,
        "n_points": r1.n_points,
        "best": best_json(structure, datasets, channels, r1.best),
    }


def _sweep(slots: _Slots) -> int:
    blocks = _search_blocks(slots, FitConfig().psi_block_max)
    return sum(math.prod(len(slots.grids[i]) for i in b) for b in blocks)


def _search_counts(outcome: ReferenceOutcome) -> Record:
    values = point_values(outcome.profile)
    return {
        "n_points": len(outcome.profile.solutions),
        "n_uncertified": sum(not v.certified for v in values),
        "n_infeasible": len(outcome.profile.solutions) - len(values),
    }


def _hex_or_none(value: Json) -> str | None:
    return None if value is None else float(value).hex()


def byte_check(
    row: RowExpect, per_event: str, details: Mapping[str, Json] | None
) -> list[str]:
    """PR 2.4: what differs between a cell and R0 (empty: it matches)."""
    out: list[str] = []
    if per_event != row.per_event:
        out.append(f"{row.system} per_event {per_event} != {row.per_event}")
    if row.oracle is None:
        return out
    got = details or {}
    pairs = (
        ("oracle_search_ll", row.oracle.search_ll, _hex_or_none),
        ("oracle_truth_psi_ll", row.oracle.truth_psi_ll, _hex_or_none),
        ("oracle_psi_source", row.oracle.psi_source, lambda x: x),
        ("oracle_underfit", row.oracle.underfit, lambda x: x),
    )
    for name, want, read in pairs:
        value = read(got.get(name))
        if value != want:
            out.append(f"{row.system} {name} {value!r} != {want!r}")
    return out


@dataclass(frozen=True)
class StoredFits:
    """R0. ``kept`` is the fit an ORACLE row reads, ``search`` every other's."""

    search: FitResult
    kept: FitResult
    details: Mapping[str, Json] | None


def bit_check_failures(expected: tuple[str, str], stored: StoredFits) -> list[str]:
    """Group B's ``r0.search.ll`` and ``r0.kept.ll``, bit for bit (section 2.9)."""
    search, kept = expected
    out: list[str] = []
    if stored.search.log_likelihood.hex() != search:
        out.append(f"r0.search.ll {stored.search.log_likelihood.hex()} != {search}")
    if stored.kept.log_likelihood.hex() != kept:
        out.append(f"r0.kept.ll {stored.kept.log_likelihood.hex()} != {kept}")
    return out


def stored_fits(
    job: FitJob, structure: Structure, data: InvestigationData
) -> StoredFits:
    obs, channels = data.observational, data.channels
    if not any(r.system == ORACLE for r in job.rows):
        result = fit(structure, obs, channels, config=FitConfig())
        return StoredFits(result, result, None)
    ran = Oracle(
        job.truth.structure,
        fit_config=FitConfig(),
        truth_psi=job.truth.spec.psi,
        truth_coefficients=job.truth.spec.coef,
    ).run(data)
    model = ran.model
    if not isinstance(model, GLMModel):
        raise ByteFailure(f"ORACLE returned a {type(model).__name__}")
    kept = model.fit
    if ran.details.get("oracle_psi_source") == "search":
        return StoredFits(kept, kept, ran.details)
    search = fit(structure, obs, channels, config=FitConfig())
    return StoredFits(search, kept, ran.details)


def r0_value(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    result: FitResult,
) -> PointValue:
    """R0's point. ``FitResult.certified`` covers every evaluated point, so a
    search fit that is flagged needs its own point's solve looked at."""
    certified = result.certified
    if not certified:
        at = fit(structure, datasets, channels, config=FitConfig(), psi=result.psi)
        certified = at.certified
    primal = result.primal_objective
    return PointValue(
        _Slots.of(structure).point(result.psi),
        -primal if math.isfinite(primal) else -math.inf,
        result.duality_gap,
        certified,
    )


@dataclass(frozen=True)
class FitJob:
    """One fit: R0, R1, R2 where due, in one process (PR 6). ``group`` is ``B`` or
    ``E`` on a control's fit; ``bit_check`` is group B's two committed values."""

    truth: TruthEntry
    seed: int
    text: str
    rows: tuple[RowExpect, ...]
    env: CampaignEnvironment
    root: int
    k: int
    cap: int
    r2_max: int
    group: str | None = None
    bit_check: tuple[str, str] | None = None

    @property
    def id(self) -> str:
        return fit_job_id(self.truth.id, self.seed, self.text)

    def run(self, sink: RecordSink) -> None:
        run_fit_job(self, sink)


def _r0_payload(
    structure: Structure, data: InvestigationData, stored: StoredFits
) -> Record:
    def one(result: FitResult) -> Record:
        value = r0_value(structure, data.observational, data.channels, result)
        return {
            "point": list(value.point),
            "score": value.score,
            "point_certified": value.certified,
            **valued_json(valued_fit(result)),
        }

    return {"search": one(stored.search), "kept": one(stored.kept)}


def _compare_row(
    job: FitJob,
    row: RowExpect,
    structure: Structure,
    data: InvestigationData,
    held: Dataset,
    stored: StoredFits,
    best: PointValue | None,
    uncertified: Sequence[PointValue],
) -> Record:
    """One row against its own stored fit (PR 2.3). The reference is shared; the
    row's R0 is not, so ORACLE's kept fit never reaches a sibling."""
    env = job.env
    fitted = stored.kept if row.system == ORACLE else stored.search
    r0 = r0_value(structure, data.observational, data.channels, fitted)
    chosen = choose_point(r0, best)
    outside = [] if r0.point in {v.point for v in uncertified} else [r0]
    stored_value = valued_fit(fitted)
    base: Record = {
        "system": row.system,
        "llm": row.llm,
        "stored_ll": stored_value.ll,
        "stored_gap": stored_value.gap,
        "stored_certified": stored_value.certified,
        "stored_per_event": need_float(row.per_event),
        "stored_uncertified": _gap_dropped(stored_value),
        "uncertified_better": count_uncertified_better(
            [*uncertified, *outside], chosen
        ),
        "corrected_per_event": None,
        "corrected_similarity": None,
        "corrected_underfit": None,
        "delta": None,
        "delta_kind": None,
        "stored_quadrature_error": None,
        "chosen_quadrature_error": None,
        "within_quadrature": False,
        "oracle": None,
    }
    if chosen is None:
        return {**base, "call": "none", "gain": 0.0, "tol": TOL_FLOOR}
    if chosen.point == r0.point:
        chosen_fit = fitted
    else:
        chosen_fit = refit_at(structure, data.observational, data.channels, chosen)
    chosen_value = Valued(chosen_fit.log_likelihood, chosen_fit.duality_gap, True)
    call = call_miss(chosen_value, stored_value)
    out: Record = {
        **base,
        "call": call.kind,
        "gain": call.gain,
        "gain_per_event": call.gain / max(chosen_fit.n_events, 1),
        "tol": call.tol,
        "chosen_ll": chosen_value.ll,
        "chosen_gap": chosen_value.gap,
        "chosen_point": list(chosen.point),
    }
    details = stored.details or {}
    if row.system == ORACLE:
        truth_psi = details.get("oracle_truth_psi_ll")
        search = call_miss(chosen_value, valued_fit(stored.search))
        out["oracle"] = {
            "search_call": search.kind,
            "search_gain": search.gain,
            "search_below_truth_psi": truth_psi is not None
            and stored.search.log_likelihood < truth_psi,
        }
    if call.kind != "miss":
        return out
    model = GLMModel(row.system, chosen_fit, env.channels, env.marks)
    reading, _ = score_submission(job.truth, job.seed, structure, model, held, env)
    corrected = reading["per_event"]
    if math.isnan(corrected):
        raise ArithmeticError("nan_reading")
    delta, kind = held_out_delta(need_float(row.per_event), corrected)
    out |= {
        "corrected_per_event": corrected,
        "corrected_similarity": reading["similarity"],
        "delta": delta,
        "delta_kind": kind,
        "stored_quadrature_error": fitted.quadrature_error,
        "chosen_quadrature_error": chosen_fit.quadrature_error,
        "within_quadrature": within_quadrature(
            call.gain, fitted.quadrature_error, chosen_fit.quadrature_error
        ),
    }
    if row.system == ORACLE:
        flagged = bool(details.get("oracle_truth_psi_on_grid")) and oracle_underfit(
            chosen_value.ll, details["oracle_true_params_ll"]
        )
        if flagged and details.get("oracle_underfit") is False:
            raise ByteFailure(f"an underfit flag appeared on {job.truth.id}")
        out["corrected_underfit"] = flagged
    return out


def run_fit_job(job: FitJob, sink: RecordSink) -> None:
    """PR 6's job. An R0 that does not reproduce its cell ends the job before any
    search; a unit failure in R1 or R2 is ``reference_failed``; anything else
    propagates, which is a bug and stops the run."""
    channels = job.env.channels
    data = observational_data(job.truth, job.seed, job.env)
    held = held_out_dataset(job.truth.spec, job.truth.id, job.seed)
    structure = parse(job.text, channels)
    failures: list[str] = []
    if canonicalise(structure) != structure:
        failures.append(f"stored text is not canonical: {job.text}")
    try:
        stored = stored_fits(job, structure, data)
        for row in job.rows:
            fitted = stored.kept if row.system == ORACLE else stored.search
            model = GLMModel(row.system, fitted, channels, job.env.marks)
            failures += byte_check(
                row, model.held_out_per_event([held]).hex(), stored.details
            )
        if job.bit_check is not None:
            failures += bit_check_failures(job.bit_check, stored)
        r0 = _r0_payload(structure, data, stored)
    except UNIT_FAILURES as error:
        # The cell holds a fit, so an R0 that cannot make one is not the stored
        # fit: PR 6 covers R1 and R2 only.
        failures.append(f"R0 failed: {type(error).__name__}: {error}")
    if failures:
        sink.record("byte_fail", {"reason": "; ".join(failures)})
        return
    sink.record("r0", r0)
    obs = data.observational
    r1_rows: list[list[Record]] = []

    def compare(
        best: PointValue | None, uncertified: Sequence[PointValue]
    ) -> list[Record]:
        return [
            _compare_row(job, row, structure, data, held, stored, best, uncertified)
            for row in job.rows
        ]

    def write_r1(r1: R1Outcome) -> None:
        rows = compare(r1.best, r1.uncertified)
        r1_rows.append(rows)
        counts = {
            "n_points": r1.n_points,
            "n_uncertified": len(r1.uncertified),
            "n_infeasible": r1.n_infeasible,
        }
        result = _fit_payload(
            job, structure, stored, rows, False, r1, r1.cpu_cut, counts
        )
        sink.record(
            "r1", {**r1_payload(structure, obs, channels, r1), "result": result}
        )

    rng = reference_rng(fit_case(job.truth.id, job.seed, job.text), job.root)
    try:
        outcome = reference_search(
            structure,
            obs,
            channels,
            rng,
            k=job.k,
            cap=job.cap,
            r2_max=job.r2_max,
            on_r1=write_r1,
            should_stop=past_job_cut,
        )
        grew = len(outcome.profile.solutions) != outcome.r1.n_points
        rows = compare(outcome.best, outcome.uncertified) if grew else r1_rows[0]
    except ByteFailure as error:
        sink.record("byte_fail", {"reason": str(error)})
        return
    except UNIT_FAILURES as error:
        reason = str(error) if str(error) == "nan_reading" else type(error).__name__
        kind = "r2_lost" if r1_rows else "reference_failed"
        sink.record(kind, {"reason": reason, "message": str(error)[:500]})
        return
    sink.record(
        "done",
        _fit_payload(
            job,
            structure,
            stored,
            rows,
            outcome.r2_complete,
            outcome.r1,
            outcome.cpu_cut,
            _search_counts(outcome),
        ),
    )


def _fit_payload(
    job: FitJob,
    structure: Structure,
    stored: StoredFits,
    rows: list[Record],
    r2_complete: bool,
    r1: R1Outcome,
    cpu_cut: bool,
    counts: Mapping[str, int],
) -> Record:
    """A fit's result, the same whether R1 or R2 is the last stage it reached."""
    slots = _Slots.of(structure)
    payload: Record = {
        "kind": "fit",
        "truth": job.truth.id,
        "seed": job.seed,
        "text": job.text,
        "grid_size": slots.size,
        "sweep": _sweep(slots),
        "link": structure.link.value,
        "n_features": len(structure.features),
        "n_events": stored.search.n_events,
        "evidence": "exact" if r2_complete else "searched",
        "capped": r1.capped,
        "starts_completed": r1.n_ascents,
        "cpu_cut": cpu_cut,
        "r2_complete": r2_complete,
        "rows": rows,
        **counts,
    }
    if job.group is not None:
        details = stored.details or {}
        known = details.get("oracle_truth_psi_ll")
        payload["control"] = {
            "group": job.group,
            "case": [job.truth.id, job.seed],
            "fit5": valued_json(valued_fit(stored.search)),
            "known": None
            if known is None
            else valued_json(
                Valued(known, stored.kept.duality_gap, stored.kept.certified)
            ),
        }
    return payload


def group_b_job(truth: TruthEntry, env: CampaignEnvironment, root: int) -> FitJob:
    """Group B, fresh on the dev split (section 2.9). The three values are the
    pilot check's; ``oracle_psi_source`` and ``oracle_underfit`` are asserted."""
    oracle = OracleExpect(GROUP_B_SEARCH_LL, GROUP_B_KEPT_LL, "truth", False)
    row = RowExpect(ORACLE, False, GROUP_B_PER_EVENT, oracle)
    return FitJob(
        truth=truth,
        seed=GROUP_B[1],
        text=GROUP_B_TEXT,
        rows=(row,),
        env=env,
        root=root,
        k=K_STARTS,
        cap=r1_cap(Link.EXP.value),
        r2_max=R2_GRID_MAX,
        group="B",
        bit_check=(GROUP_B_SEARCH_LL, GROUP_B_KEPT_LL),
    )


@dataclass(frozen=True)
class T2Job:
    """A group A or D control. Group D is R2 alone on an empty profile: K = 0, no
    R1, and the one control that does not test R1."""

    which: int
    link: str
    seed: int
    group: str
    with_r2: bool
    root: int
    k: int
    cap: int

    @property
    def id(self) -> str:
        return _sha(f"{self.group}|{t2_case(self.which, self.link, self.seed)}")

    def run(self, sink: RecordSink) -> None:
        run_t2_job(self, sink)


def run_t2_job(job: T2Job, sink: RecordSink) -> None:
    case = t2_log(job.which, job.link, job.seed)
    structure = case.structure
    data = (Dataset.observational(case.log),)
    base = fit(structure, data, T2_CHANNELS, config=FitConfig())
    sink.record("r0", {"search": valued_json(valued_fit(base))})

    def write_r1(r1: R1Outcome) -> None:
        sink.record("r1", r1_payload(structure, data, T2_CHANNELS, r1))

    rng = reference_rng(t2_case(job.which, job.link, job.seed), job.root)
    try:
        outcome = reference_search(
            structure,
            data,
            T2_CHANNELS,
            rng,
            k=job.k,
            cap=job.cap,
            r2_max=R2_GRID_MAX if job.with_r2 else 0,
            on_r1=write_r1 if job.k else None,
            should_stop=past_job_cut,
        )
        best = best_json(structure, data, T2_CHANNELS, outcome.best)
        if job.with_r2:
            sink.record("r2", {"best": best, "complete": outcome.r2_complete})
        known = known_value(structure, data, T2_CHANNELS, case.generating)
    except ByteFailure as error:
        sink.record("byte_fail", {"reason": str(error)})
        return
    except UNIT_FAILURES as error:
        sink.record("reference_failed", {"reason": type(error).__name__})
        return
    payload: Record = {
        "kind": "control",
        "group": job.group,
        "case": [job.which, job.link, job.seed],
        "fit5": valued_json(valued_fit(base)),
        "known": valued_json(known),
        "grid_size": outcome.profile.slots.size,
        "capped": outcome.r1.capped,
        "starts_completed": outcome.r1.n_ascents,
        "cpu_cut": outcome.cpu_cut,
        "r2_complete": outcome.r2_complete,
        **_search_counts(outcome),
    }
    if job.with_r2:
        payload["r2_best"] = best
    sink.record("done", payload)


def hunt_index(stratum: str, j: int) -> int:
    """``i`` of the seeding rule: ``1000 * s + j`` (PR 2.9)."""
    return 1000 * [x.name for x in HUNT_STRATA].index(stratum) + j


def hunt_job_id(stratum: str, j: int) -> str:
    return _sha(hunt_case(stratum, hunt_index(stratum, j)))


def hunt_r1_job_id(stratum: str, j: int) -> str:
    return _sha(f"r1|{hunt_case(stratum, hunt_index(stratum, j))}")


@dataclass(frozen=True)
class HuntJob:
    """One hunt case: fit/5, then the fit at the generating point."""

    stratum: str
    j: int
    log_root: int

    @property
    def id(self) -> str:
        return hunt_job_id(self.stratum, self.j)

    def run(self, sink: RecordSink) -> None:
        run_hunt_job(self, sink)


def run_hunt_job(job: HuntJob, sink: RecordSink) -> None:
    try:
        case = hunt_log(job.stratum, job.j, job.log_root)
    except SimulationError:
        sink.record(
            "done",
            {
                "kind": "hunt",
                "stratum": job.stratum,
                "j": job.j,
                "sim_failed": True,
                "found_loss": False,
                "uncertified_loss": False,
            },
        )
        return
    structure = case.structure
    data = (Dataset.observational(case.log),)
    try:
        base = valued_fit(fit(structure, data, T2_CHANNELS, config=FitConfig()))
        known = known_value(structure, data, T2_CHANNELS, case.generating)
    except UNIT_FAILURES as error:
        sink.record("reference_failed", {"reason": type(error).__name__})
        return
    sink.record(
        "done",
        {
            "kind": "hunt",
            "stratum": job.stratum,
            "j": job.j,
            "sim_failed": False,
            "link": structure.link.value,
            "grid_size": _Slots.of(structure).size,
            "n_events": case.log.n,
            "fit5": valued_json(base),
            "known": valued_json(known),
            "found_loss": found_loss(base, known),
            "uncertified_loss": not known.certified
            and call_miss(known, base).kind == "miss",
        },
    )


@dataclass(frozen=True)
class HuntR1Job:
    """R1 on a found loss. It holds no known value: whether R1 caught the loss is
    read later, from this job's record and the case's."""

    stratum: str
    j: int
    root: int
    log_root: int
    k: int
    cap: int

    @property
    def id(self) -> str:
        return hunt_r1_job_id(self.stratum, self.j)

    def run(self, sink: RecordSink) -> None:
        run_hunt_r1_job(self, sink)


def run_hunt_r1_job(job: HuntR1Job, sink: RecordSink) -> None:
    case = hunt_log(job.stratum, job.j, job.log_root)
    structure = case.structure
    data = (Dataset.observational(case.log),)

    def write_r1(r1: R1Outcome) -> None:
        sink.record("r1", r1_payload(structure, data, T2_CHANNELS, r1))

    i = hunt_index(job.stratum, job.j)
    rng = reference_rng(hunt_case(job.stratum, i), job.root)
    try:
        outcome = reference_search(
            structure,
            data,
            T2_CHANNELS,
            rng,
            k=job.k,
            cap=job.cap,
            r2_max=0,
            on_r1=write_r1,
            should_stop=past_job_cut,
        )
    except ByteFailure as error:
        sink.record("byte_fail", {"reason": str(error)})
        return
    except UNIT_FAILURES as error:
        sink.record("reference_failed", {"reason": type(error).__name__})
        return
    sink.record(
        "done",
        {
            "kind": "hunt_r1",
            "stratum": job.stratum,
            "j": job.j,
            "capped": outcome.r1.capped,
            "starts_completed": outcome.r1.n_ascents,
            "cpu_cut": outcome.cpu_cut,
            **_search_counts(outcome),
        },
    )


def hunt_r1_jobs(
    records: Sequence[Mapping[str, Json]],
    *,
    root: int,
    log_root: int,
    size: int = HUNT_SIZE,
) -> list[HuntR1Job]:
    """The R1 jobs due: the first found losses of each sub-stratum in index order,
    counted only as far as every earlier case has finished. So the set is a
    function of the cases' results, never of which worker finished first. They run
    after the fit/5 jobs, inside the same cap."""
    done = {r["job"]: r for r in records if r["type"] == "done"}
    failed = {r["job"] for r in records if r["type"] == "reference_failed"}
    out: list[HuntR1Job] = []
    for spec in HUNT_STRATA:
        picked = 0
        for j in range(size):
            job_id = hunt_job_id(spec.name, j)
            if job_id in failed:
                continue
            record = done.get(job_id)
            if record is None or picked == HUNT_R1_PER_STRATUM:
                break
            if record["found_loss"]:
                picked += 1
                cap = r1_cap(spec.link.value)
                out.append(HuntR1Job(spec.name, j, root, log_root, K_STARTS, cap))
    position = {spec.name: s for s, spec in enumerate(HUNT_STRATA)}
    return sorted(out, key=lambda x: (x.j, position[x.stratum]))


def link_choice(
    features: tuple[Feature, ...],
    train: tuple[Dataset, ...],
    validation: tuple[Dataset, ...],
    channels: tuple[ChannelSpec, ...],
    links: Sequence[Link],
    fitter: Callable[[Structure], FitResult],
    *,
    blocked: frozenset[Link] = frozenset(),
) -> tuple[Link, list[tuple[str, float, bool]]]:
    """``sparse._choose_link``'s rule with its fit made a parameter, so S2 can rerun
    it with R0 and with the reference. A ``blocked`` link stays ``-inf``: the rule
    reads the fit-level flag, which a single-point refit passes more easily, so no
    link may win for that reason alone."""
    scores: list[tuple[Link, float, bool]] = []
    for link in links:
        if link in blocked:
            scores.append((link, -math.inf, False))
            continue
        structure = canonicalise(Structure(features, link))
        try:
            result = fitter(structure)
        except FitError:
            scores.append((link, -math.inf, False))
            continue
        if not result.certified:
            scores.append((link, -math.inf, False))
            continue
        ll = math.fsum(
            evaluate_log_likelihood(
                result, validation, channels, quadrature=FitConfig().quadrature
            )
        )
        scores.append((link, ll, True))
    best = 0
    for i, score in enumerate(scores):
        if score[1] > scores[best][1]:
            best = i
    return scores[best][0], [(s[0].value, s[1], s[2]) for s in scores]


def reference_link_fit(
    structure: Structure,
    train: tuple[Dataset, ...],
    channels: tuple[ChannelSpec, ...],
    *,
    k: int,
    case: str,
    root: int,
    should_stop: Callable[[], bool],
) -> FitResult:
    """R0 and R1 on the training window (PR 4, S2). An exhaustive grid is settled
    by R0."""
    base = fit(structure, train, channels, config=FitConfig())
    if is_exhaustive(_Slots.of(structure).size):
        return base
    outcome = reference_search(
        structure,
        train,
        channels,
        reference_rng(case, root),
        k=k,
        cap=S2_POINT_CAP,
        r2_max=0,
        should_stop=should_stop,
    )
    chosen = choose_point(r0_value(structure, train, channels, base), outcome.r1.best)
    if chosen is None:
        return base
    return refit_at(structure, train, channels, chosen)


@dataclass(frozen=True)
class S2Job:
    """B-sparse's link choice on one row (PR 4, S2)."""

    truth: TruthEntry
    seed: int
    text: str
    stored_link: str
    env: CampaignEnvironment
    root: int

    @property
    def id(self) -> str:
        return _sha(s2_case(self.truth.id, self.seed, self.text))

    def run(self, sink: RecordSink) -> None:
        run_s2_job(self, sink)


def run_s2_job(job: S2Job, sink: RecordSink) -> None:
    channels, sparse = job.env.channels, SparseConfig()
    data = observational_data(job.truth, job.seed, job.env)
    (obs,) = data.observational
    train_set, validation_set = split_dataset(obs, sparse.train_fraction)
    train, validation = (train_set,), (validation_set,)
    features = parse(job.text, channels).features
    cut = JobCut()
    payload: Record = {
        "kind": "s2",
        "truth": job.truth.id,
        "seed": job.seed,
        "text": job.text,
        "stored_link": job.stored_link,
        "corrected_per_event": None,
    }
    try:
        r0_link, r0_scores = link_choice(
            features,
            train,
            validation,
            channels,
            sparse.links,
            lambda s: fit(s, train, channels, config=FitConfig()),
        )
        payload |= {"r0_link": r0_link.value, "r0_scores": r0_scores}
        if r0_link.value != job.stored_link:
            status = {"status": "link_mismatch", "cpu_cut": cut.fired}
            sink.record("done", {**payload, **status})
            return
        ref_link, ref_scores = link_choice(
            features,
            train,
            validation,
            channels,
            sparse.links,
            lambda s: reference_link_fit(
                s,
                train,
                channels,
                k=S2_STARTS,
                case=s2_case(job.truth.id, job.seed, render(s)),
                root=job.root,
                should_stop=cut,
            ),
            blocked=frozenset(Link(x[0]) for x in r0_scores if x[1] == -math.inf),
        )
        payload |= {"reference_link": ref_link.value, "reference_scores": ref_scores}
        if ref_link is r0_link:
            sink.record("done", {**payload, "status": "same", "cpu_cut": cut.fired})
            return
        structure = canonicalise(Structure(features, ref_link))
        text = render(structure)
        outcome = reference_search(
            structure,
            data.observational,
            channels,
            reference_rng(s2_case(job.truth.id, job.seed, text), job.root),
            k=K_STARTS,
            cap=r1_cap(ref_link.value),
            r2_max=R2_GRID_MAX,
            should_stop=cut,
        )
        base = fit(structure, data.observational, channels, config=FitConfig())
        chosen = choose_point(
            r0_value(structure, data.observational, channels, base), outcome.best
        )
        result = (
            base
            if chosen is None
            else refit_at(structure, data.observational, channels, chosen)
        )
        held = held_out_dataset(job.truth.spec, job.truth.id, job.seed)
        model = GLMModel(B_SPARSE, result, channels, job.env.marks)
        per_event = model.held_out_per_event([held])
    except ByteFailure as error:
        sink.record("byte_fail", {"reason": str(error)})
        return
    except UNIT_FAILURES as error:
        sink.record("reference_failed", {"reason": type(error).__name__})
        return
    sink.record(
        "done",
        {
            **payload,
            "status": "differs",
            "cpu_cut": cut.fired,
            "reference_text": text,
            "corrected_per_event": per_event,
        },
    )


# --------------------------------------------------------------------------
# The population: one place opens the store, read only (sections 2.1, 2.8)
# --------------------------------------------------------------------------


def p4_inputs(
    test_split: Path,
) -> tuple[CampaignEnvironment, tuple[CampaignConfig, ...], tuple[TruthEntry, ...]]:
    """The test split's environment, the frozen plan and the truths. The plan is a
    pure function of the truths, not an operator's input (section 2.7's list)."""
    records = pilot.load_split(test_split)
    truths = pilot.truth_entries(records)
    return pilot.pointproc_environment(records), p4_plan(truths), truths


def group_b_inputs(split: Path) -> tuple[TruthEntry, CampaignEnvironment]:
    """Group B's truth and environment, from the dev split alone."""
    records = pilot.load_split(split)
    by_id = {t.id: t for t in pilot.truth_entries(records)}
    if GROUP_B[0] not in by_id:
        raise GuardFailure(f"group B's {GROUP_B[0]} is not in {split}")
    return by_id[GROUP_B[0]], pilot.pointproc_environment(records)


def stored_cells(
    store: ResultStore,
    env: CampaignEnvironment,
    config: CampaignConfig,
    truths: Sequence[TruthEntry],
) -> dict[RowKey, Mapping[str, Json]]:
    """Each row's raw cell, by the addresses ``load_rows`` reads: a :class:`Row`
    keeps neither ``certified`` nor the reading's hex."""
    by_id = {t.id: t for t in truths}
    order = [t.id for t in truths]
    found = stored_sessions(store, env, config, truths)
    out: dict[RowKey, Mapping[str, Json]] = {}
    for u in baseline_units(order, config):
        key = baseline_key(u, by_id[u.truth_id], env, config)
        if store.done(key):
            out[(u.truth_id, u.seed, u.system)] = store.cell(key.digest)
    for u in llm_units(order, config):
        hit = found.get(u)
        if hit is None:
            continue
        ckey = score_key(u, hit[0], env, config)
        if store.done(ckey):
            out[(u.truth_id, u.seed, u.system)] = store.cell(ckey.digest)
    return out


def _summed(tables: Sequence[Mapping[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in tables:
        for name, count in table.items():
            out[name] = out.get(name, 0) + count
    return out


def merged_table(tables: Sequence[Table]) -> Table:
    """One table over the plan's configs, so that one ``derive`` covers every row
    and ``read_contrasts`` refuses a key two configs share."""
    rows = [r for t in tables for r in t.rows]
    telling = {key: cell for t in tables for key, cell in t.telling.items()}
    return Table(
        tables[0].truths,
        tuple(analysis.derive(rows)),
        tuple(x for t in tables for x in t.sessions),
        _summed([t.planned for t in tables]),
        _summed([t.missing for t in tables]),
        telling,
    )


def load_store(
    root: Path,
    env: CampaignEnvironment,
    plan: Sequence[CampaignConfig],
    truths: Sequence[TruthEntry],
) -> tuple[Table, dict[RowKey, Mapping[str, Json]]]:
    tables: list[Table] = []
    cells: dict[RowKey, Mapping[str, Json]] = {}
    with ResultStore(root, read_only=True) as store:
        for config in plan:
            tables.append(
                analysis.load_rows(store, env, config, truths, sessions="stored")
            )
            cells |= stored_cells(store, env, config, truths)
    return merged_table(tables), cells


def check_table(table: Table, cells: Mapping[RowKey, Mapping[str, Json]]) -> None:
    """Section 2.1: nothing is missing, or the campaign is not finished. A planned
    unit with neither a reading nor a counted void or failure is missing, and that
    includes a non-void session with no score cell."""
    missing = {k: v for k, v in sorted(table.missing.items()) if v}
    if missing:
        raise GuardFailure(f"missing units: {missing}")
    keys = {row_key(r) for r in table.rows}
    voids = sum(x.void.void for x in table.sessions)
    silent = [
        x
        for x in table.sessions
        if not x.void.void and (x.truth, x.seed, x.system) not in keys
    ]
    if silent:
        raise GuardFailure(f"{len(silent)} non-void sessions have no score cell")
    planned = sum(v for k, v in table.planned.items() if k != TELLING)
    if len(table.rows) + voids != planned:
        raise GuardFailure(
            f"{len(table.rows)} rows and {voids} voids, {planned} units planned"
        )
    for row in table.rows:
        cell = cells.get(row_key(row))
        if cell is None:
            raise GuardFailure(f"missing cell for {row_key(row)}")
        fields = cell["key"]["config"]["fit_config"].split("|")
        if "full=512" not in fields or "sweeps=0" not in fields:
            raise GuardFailure(f"{row_key(row)}: not full=512 and sweeps=0")


def classify_row(row: Row, *, grid_size: int | None, certified: bool | None) -> str:
    """PR 2.1's table: exactly one class per row."""
    if not row.ok:
        return "failed"
    if not row.submitted:
        return "no_submission"
    if row.system == B_NP:
        return "no_search_np"
    if grid_size is None:
        return "no_search_lib" if row.system == B_LIB else "failed"
    if not is_exhaustive(grid_size):
        return "coordinate"
    return "exhaustive" if certified else "exhaustive_uncertified"


def _oracle_expect(detail: Mapping[str, Json]) -> OracleExpect:
    return OracleExpect(
        _hex_or_none(detail.get("oracle_search_ll")),
        _hex_or_none(detail.get("oracle_truth_psi_ll")),
        detail.get("oracle_psi_source"),
        detail.get("oracle_underfit"),
    )


def build_census(
    table: Table,
    cells: Mapping[RowKey, Mapping[str, Json]],
    channels: tuple[ChannelSpec, ...],
    tier1: frozenset[RowKey],
) -> Census:
    """Section 2.1's classes, and section 2.9's group E: an ORACLE unit with stored
    ``oracle_psi_source == "truth"``. It reads a P4 row, in memory only: nothing
    here is printed."""
    out: list[RowInfo] = []
    for r in table.rows:
        cell = cells[row_key(r)]
        reading = cell["reading"]
        detail = cell["detail"]["result"]
        size: int | None = None
        link: str | None = None
        if r.structure is not None:
            structure = parse(r.structure, channels)
            size, link = _Slots.of(structure).size, structure.link.value
        raw = unhex(reading.get("certified"))
        certified = None if raw is None or math.isnan(raw) else raw == 1.0
        klass = classify_row(r, grid_size=size, certified=certified)
        group_e = r.system == ORACLE and r.oracle_psi_source == "truth"
        expect = None
        if klass == "coordinate" or group_e:
            oracle = _oracle_expect(detail) if r.system == ORACLE else None
            expect = RowExpect(r.system, r.llm, reading["per_event"], oracle)
        sparse = detail.get("sparse")
        out.append(
            RowInfo(
                truth=r.truth,
                seed=r.seed,
                system=r.system,
                llm=r.llm,
                klass=klass,
                text=r.structure,
                grid_size=size,
                link=link,
                certified=certified,
                expect=expect,
                sparse_link=None if sparse is None else sparse["link"],
                group_e=group_e,
            )
        )
    return Census(tuple(out), tier1)


def print_counts(census: Census, table: Table) -> None:
    """Section 2.1: counts only. No score, ``oracle_*`` or ``certified`` value, and
    nothing of group E, whose selection reads a P4 row."""
    print(f"classes: {_count(r.klass for r in census.rows)}", flush=True)
    print(f"void sessions: {analysis.void_counts(table)}", flush=True)


def hunt_jobs(*, log_root: int, size: int = HUNT_SIZE) -> tuple[HuntJob, ...]:
    """Round-robin, ``j`` outer and ``s`` inner, so a cap cuts the sub-strata
    evenly."""
    return tuple(HuntJob(s.name, j, log_root) for j in range(size) for s in HUNT_STRATA)


def plan_stages(
    census: Census,
    truths: Mapping[str, TruthEntry],
    env: CampaignEnvironment,
    group_b: FitJob,
    *,
    roots: Roots,
    hunt_size: int = HUNT_SIZE,
) -> tuple[Stage, ...]:
    """Section 8's order: controls A, D, C, B; tier 1 with group E; S2; tier 2.
    Inside a stage by ``sha256`` of the case. Group B reads no P4 row, so the
    first P4 unit read is tier 1's."""
    fits = census.fits()

    def t2(case: tuple[int, str, int], group: str, with_r2: bool, k: int) -> T2Job:
        return T2Job(*case, group, with_r2, roots.seed, k, r1_cap(case[1]))

    def fit_job(f: FitGroup) -> FitJob:
        return FitJob(
            truth=truths[f.truth],
            seed=f.seed,
            text=f.text,
            rows=f.rows,
            env=env,
            root=roots.seed,
            k=K_STARTS,
            cap=r1_cap(f.link),
            r2_max=R2_GRID_MAX,
            group="E" if f.group_e else None,
        )

    # Group E's unit on an exhaustive grid has no coordinate fit of its own.
    exhaustive_e = [
        FitGroup(
            r.truth,
            r.seed,
            r.text,
            (r.expect,),
            1,
            r.grid_size or 0,
            r.link or "",
            True,
        )
        for r in census.rows
        if r.group_e
        and r.klass != "coordinate"
        and r.text is not None
        and r.expect is not None
    ]
    sparse = sorted(
        (r for r in census.rows if r.system == B_SPARSE and r.klass == "coordinate"),
        key=lambda r: (
            truths[r.truth].in_dictionary,
            fit_job_id(r.truth, r.seed, r.text or ""),
        ),
    )
    tier1 = sorted(
        (fit_job(f) for f in (*fits, *exhaustive_e) if f.tier == 1),
        key=lambda j: j.id,
    )
    return (
        Stage("group_a", tuple(t2(c, "A", False, K_STARTS) for c in T2_CASES)),
        Stage("group_d", (t2(T2_CASES[0], "D", True, 0),)),
        Stage(
            "hunt",
            hunt_jobs(log_root=roots.log, size=hunt_size),
            cpu_cap_s=HUNT_CPU_HOURS * 3600.0,
            follow_up=partial(
                hunt_r1_jobs, root=roots.seed, log_root=roots.log, size=hunt_size
            ),
        ),
        Stage("group_b", (group_b,)),
        Stage("tier1", tuple(tier1)),
        Stage(
            "s2",
            tuple(
                S2Job(
                    truths[r.truth],
                    r.seed,
                    r.text or "",
                    r.sparse_link or "",
                    env,
                    roots.seed,
                )
                for r in sparse
            ),
        ),
        Stage("tier2", tuple(fit_job(f) for f in fits if f.tier == 2)),
    )


def write_controls(paths: OutPaths, stage: str) -> None:
    """``controls.json``, when the controls end (section 2.8): H1 is known before
    tier 1 without reading the journal. Group B is a dev unit, so it holds no P4
    row."""
    if stage != "group_b":
        return
    records = read_records(paths.journal)
    hunt = hunt_summary(records)
    body = {
        "H1": h1_json(h1_status(records)),
        "hunt": hunt,
        "control_state": _control_state(hunt),
    }
    replace_file(paths.controls, (dumps_record(body) + "\n").encode("utf-8"))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def launch_line(
    used_wall_s: float, worktree: Path, split: Path, test_split: Path, store: Path
) -> str:
    """The unit's launch line, with the wall time left out of 120 hours."""
    remaining = max(int(WALL_HOURS * 3600.0 - used_wall_s), 0)
    env = " ".join(f"-E {name}=1" for name in BLAS_VARIABLES)
    return (
        "systemd-run --user --unit=sci-p4-search-misses --nice=10 -p CPUQuota=200% "
        f"-p RuntimeMaxSec={remaining} -p MemoryMax=5G -p OOMPolicy=continue {env} "
        f"--working-directory={worktree} uv run python {SCRIPT_PATH} --workers 2 "
        f"--split {split} --test-split {test_split} --store {store}"
    )


def resume_line(
    cpu_json: Path, worktree: Path, split: Path, test_split: Path, store: Path
) -> str | None:
    """The line a resume is launched with; None if it would be refused."""
    used, wall = ledger_totals(cpu_json)
    if used >= CPU_STOP_HOURS * 3600 * 10**6 or wall >= WALL_HOURS * 3600.0:
        return None
    return launch_line(wall, worktree, split, test_split, store)


@dataclass(frozen=True)
class Setup:
    """What ``main`` found once every guard passed."""

    identity: Mapping[str, str]
    paths: OutPaths
    roots: Roots
    hunt_size: int
    env: CampaignEnvironment
    plan: Sequence[CampaignConfig]
    truths: Sequence[TruthEntry]
    controls: ScriptedControls
    group_b: FitJob
    store: Path
    resamples: int = READS_RESAMPLES
    rehearsal: bool = False


def head_commit(repo: Path) -> str:
    head = _git_out(repo, "rev-parse", "HEAD")
    if head is None:
        raise GuardFailure(f"no HEAD at {repo}")
    return head


def rehearsal_paths(repo: Path) -> OutPaths:
    """Where a refused rehearsal says so, before its plan names its directory."""
    return OutPaths(repo / DRAFTS / REHEARSAL_DIR)


def check_rehearsal(
    repo: Path, *, split: Path, store: Path, test_split: Path | None
) -> None:
    """Section 2.10's positive allowlist, but for the truths, which need the plan
    (:func:`rehearsal_records`). So the mode cannot run at ``T``, on a copy of the
    P4 store, or on the test split."""
    if (repo / FREEZE_PATH).exists():
        raise GuardFailure(f"{FREEZE_PATH} exists: a rehearsal cannot run at T")
    if test_split is not None:
        raise GuardFailure("a rehearsal takes no test split")
    for name, path in (("--split", split), ("--store", store)):
        if not path.is_absolute():
            raise GuardFailure(f"{name} must be an absolute path: {path}")
    drafts = (repo / DRAFTS).resolve()
    target = store.resolve()
    if target == drafts or not target.is_relative_to(drafts):
        raise GuardFailure(f"a rehearsal's store must be under {drafts}: {target}")
    check_split(split, SPLIT_SHA256, DEV_RELATIVE)


def rehearsal_records(
    path: Path, records: Sequence[Any]
) -> tuple[list[Any], Mapping[str, Json]]:
    """The plan's truths, each a record of ``--split`` with ``split`` equal to
    ``dev``, so a truth file cannot be brought in by the plan alone. They are the
    first eight dev truths by sorted id, on two seeds: section 2.10 lets a short
    rehearsal raise its budget, and not swap its truths."""
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GuardFailure(f"unreadable rehearsal plan {path}: {error}") from error
    if not isinstance(spec, dict) or not spec.get("truths"):
        raise GuardFailure(f"the rehearsal plan {path} names no truths")
    unknown = sorted(set(spec) - set(REHEARSAL_PLAN_KEYS))
    if unknown:
        raise GuardFailure(f"the rehearsal plan has unknown keys {unknown}")
    by_id = {r.id: r for r in records}
    chosen: list[Any] = []
    for truth in spec["truths"]:
        if truth not in by_id:
            raise GuardFailure(f"{truth} is no record of --split")
        if by_id[truth].split != DEV_SPLIT:
            raise GuardFailure(f"{truth} is of split {by_id[truth].split}, not dev")
        chosen.append(by_id[truth])
    dev = sorted(r.id for r in records if r.split == DEV_SPLIT)
    first = dev[:REHEARSAL_TRUTHS]
    if spec["truths"] != first:
        raise GuardFailure(
            f"a rehearsal runs on the first eight dev truths by sorted id, {first}, "
            f"and the plan names {spec['truths']}"
        )
    seeds = spec.get("seeds")
    distinct = (
        isinstance(seeds, list)
        and len(seeds) == REHEARSAL_SEEDS
        and all(type(seed) is int for seed in seeds)
        and len(set(seeds)) == REHEARSAL_SEEDS
    )
    if not distinct:
        raise GuardFailure(f"a rehearsal runs on two seeds, and the plan has {seeds!r}")
    return chosen, spec


def rehearsal_configs(spec: Mapping[str, Json]) -> tuple[CampaignConfig, ...]:
    """The plan as one Sonnet config at the stated budget: P4's contrast systems
    and baselines, at reduced budget (section 2.10). ``seeds`` is required, and any
    other key left out keeps its default. ``scripts/p4_rehearsal_store.py`` calls
    this too, so the store's maker and this script build the same addresses."""
    config = CampaignConfig(
        llm_arms=P4_SONNET_ARMS,
        llm_model=MODELS["sonnet"],
        llm_letter="S",
    )
    plain = {k: spec[k] for k in ("fits", "experiments", "control_factor") if k in spec}
    gp = {
        name: spec[f"gp_{name}"]
        for name in ("population", "offspring")
        if f"gp_{name}" in spec
    }
    return (
        replace(
            config,
            seeds=tuple(spec["seeds"]),
            control_seeds=tuple(spec.get("control_seeds", config.control_seeds)),
            gp=replace(config.gp, **gp),
            **plain,
        ),
    )


def rehearsal_setup(args: argparse.Namespace, repo: Path) -> Setup:
    """The mode changes exactly section 2.10's list. The commit-chain guards do not
    run; the fit, tree and BLAS guards do. Control 4 has its own dev record, at
    HEAD, since there is no ``T`` here."""
    check_rehearsal(
        repo, split=args.split, store=args.store, test_split=args.test_split
    )
    check_tree(repo, None)
    check_fit(repo, BASE_COMMIT, FIT_DIFF_SHA256, REDUCTIONS_BLOB)
    check_blas(initial_environment())
    records = pilot.load_split(args.split)
    chosen, spec = rehearsal_records(args.rehearsal_plan, records)
    truth, dev_env = group_b_inputs(args.split)
    head = head_commit(repo)
    plan_sha = hashlib.sha256(args.rehearsal_plan.read_bytes()).hexdigest()
    name = f"{REHEARSAL_DIR}-{script_sha256()[:12]}-{plan_sha[:12]}"
    return Setup(
        identity={"mode": "rehearsal", "HEAD": head, "prereg_sha256": PREREG_SHA256},
        paths=OutPaths(repo / DRAFTS / name),
        roots=REHEARSAL_ROOTS,
        hunt_size=REHEARSAL_HUNT_SIZE,
        env=pilot.pointproc_environment(records),
        plan=rehearsal_configs(spec),
        truths=pilot.truth_entries(tuple(chosen)),
        # Section 2.10's list does not change control 4, and a rehearsal has no
        # ``T``, so section 2.7's rule gives False and the C2 reads stay unreportable.
        controls=ScriptedControls(naming_null=False),
        group_b=group_b_job(truth, dev_env, REHEARSAL_ROOTS.seed),
        store=args.store,
        rehearsal=True,
    )


def frozen_setup(args: argparse.Namespace) -> Setup:
    """The guards of section 2.10, then the launch's inputs. Control 4 is read at
    ``T``, which the guards name."""
    identity = run_guards(
        pilot.ROOT,
        prereg_sha=PREREG_SHA256,
        f2_commit=F2_COMMIT,
        base_commit=BASE_COMMIT,
        fit_diff_sha=FIT_DIFF_SHA256,
        reductions_blob=REDUCTIONS_BLOB,
        environ=initial_environment(),
        store=args.store,
        launch_copy=read_launch_copy,
    )
    check_paths(
        split=args.split,
        test_split=args.test_split,
        store=args.store,
        out=OUT_DIR,
        repo=pilot.ROOT,
    )
    check_split(args.split, SPLIT_SHA256, DEV_RELATIVE)
    expected = test_split_expected(pilot.ROOT, identity["S"])
    check_split(args.test_split, expected, TEST_RELATIVE)
    env, plan, truths = p4_inputs(args.test_split)
    truth, dev_env = group_b_inputs(args.split)
    return Setup(
        identity=identity,
        paths=OutPaths(OUT_DIR),
        roots=FROZEN_ROOTS,
        hunt_size=HUNT_SIZE,
        env=env,
        plan=plan,
        truths=truths,
        controls=scripted_controls(identity["T"]),
        group_b=group_b_job(truth, dev_env, FROZEN_ROOTS.seed),
        store=args.store,
    )


def take_manifest(paths: OutPaths, store: Path) -> None:
    """PR 2.8's "before", which is the first start's. It is written before
    ``cpu.json``, so a ledger with no manifest means a record was deleted, and a
    new baseline would hide what changed the store before this resume."""
    if paths.manifest.exists():
        return
    if paths.cpu.exists():
        raise ResumeRefused(
            f"{paths.cpu} exists and {paths.manifest} does not: a resume takes no "
            "new manifest, since the store's first one is what it is checked against"
        )
    body = canonical_json(store_manifest(store))
    replace_file(paths.manifest, (body + "\n").encode("utf-8"))


def _manifest_failures(paths: OutPaths, store: Path) -> list[str]:
    if not paths.manifest.exists():
        return ["no manifest was taken before the run"]
    before = json.loads(paths.manifest.read_text(encoding="utf-8"))
    return manifest_diff(before, store_manifest(store))


def report(decision: Mapping[str, Json]) -> int:
    """The line and the exit code. ``bug`` has no label and so no label's code."""
    if "label_line" in decision:
        print(decision["label_line"], flush=True)
    elif "label" in decision:
        print(f"{decision['label']}: {decision['byte_failures']}", flush=True)
    else:
        print(f"no label: the run ended {decision['end']!r}", flush=True)
    return int(decision.get("exit_code", NO_LABEL_EXIT))


def execute(setup: Setup, args: argparse.Namespace) -> int:
    """A run or ``--finalise``, once the guards have passed. A refused resume stops
    before the store is opened."""
    paths = setup.paths
    paths.root.mkdir(parents=True, exist_ok=True)
    print(f"script sha256 {script_sha256()}", flush=True)
    decision: Record | None
    try:
        check_script(paths.cpu)
        if args.finalise:
            finished = finished_decision(paths)
            if finished is not None:
                return report(finished)
        else:
            check_start(paths)
            CpuLedger.load(paths.cpu)
            take_manifest(paths, setup.store)
        table, cells = load_store(setup.store, setup.env, setup.plan, setup.truths)
        check_table(table, cells)
        plan_rows = contrast_rows(setup.plan, setup.truths)
        channels = setup.env.channels
        census = build_census(table, cells, channels, frozenset(plan_rows))
        print_counts(census, table)
        inputs = Inputs(
            census,
            table,
            plan_rows,
            setup.controls,
            setup.roots,
            setup.resamples,
            setup.rehearsal,
        )
        with driver_lock(paths):
            if args.finalise:
                decision = finalise(
                    paths,
                    inputs,
                    identity=setup.identity,
                    manifest_failures=_manifest_failures(paths, setup.store),
                    end_flag=args.end,
                )
            else:
                stages = plan_stages(
                    census,
                    {t.id: t for t in setup.truths},
                    setup.env,
                    setup.group_b,
                    roots=setup.roots,
                    hunt_size=setup.hunt_size,
                )
                run = Driver(
                    paths,
                    stages,
                    workers=args.workers,
                    after_stage=partial(write_controls, paths),
                )
                reason = run.run()
                print(f"run ended: {reason}", flush=True)
                decision = None
                if reason not in RESUMABLE_ENDS:
                    decision = conclude(
                        paths,
                        inputs,
                        reason,
                        identity=setup.identity,
                        manifest_failures=_manifest_failures(paths, setup.store),
                    )
    except (GuardFailure, ResumeRefused, ByteFailure, CampaignError) as error:
        return refuse(paths, str(error))
    if decision is None:
        if setup.rehearsal:
            print("a rehearsal resumes by its own command line", flush=True)
            return NO_LABEL_EXIT
        line = resume_line(
            paths.cpu, pilot.ROOT, args.split, args.test_split, setup.store
        )
        print(line or "no resume: the CPU stop or the wall limit is reached")
        return NO_LABEL_EXIT
    return report(decision)


def main(argv: Sequence[str] | None = None) -> int:
    pilot.reconfigure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.print_hashes:
        for path in (SCRIPT_PATH, TEST_PATH):
            digest = hashlib.sha256((pilot.ROOT / path).read_bytes()).hexdigest()
            print(f"{digest}  {path}")
        return 0
    if args.end and not args.finalise:
        parser.error("--end goes with --finalise")
    if args.rehearsal != (args.rehearsal_plan is not None):
        parser.error("--rehearsal and --rehearsal-plan come together")
    if args.split is None or args.store is None:
        parser.error("--split and --store are required")
    if args.rehearsal:
        try:
            setup = rehearsal_setup(args, pilot.ROOT)
        except (GuardFailure, CampaignError) as error:
            return refuse(rehearsal_paths(pilot.ROOT), str(error))
        return execute(setup, args)
    if args.test_split is None:
        parser.error("--test-split is required")
    try:
        setup = frozen_setup(args)
    except (GuardFailure, CampaignError) as error:
        return refuse(OutPaths(OUT_DIR), str(error))
    return execute(setup, args)


if __name__ == "__main__":
    sys.exit(main())
