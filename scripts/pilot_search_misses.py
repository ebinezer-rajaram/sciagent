"""Search misses on the pilot's own units (fit/5, P3): an instrument (SPEC §6.3).

The rule is ``docs/v2/prereg/2026-11-04_pilot-search-misses.md``; a section
number below means that file. This script implements it and decides nothing
of its own. It refuses to start until the text is frozen: ``PREREG_SHA256``
and ``F_COMMIT`` are set at ``R``.

    systemd-run --user --unit=sci-search-misses --nice=10 -p CPUQuota=200% \
        -p RuntimeMaxSec=<remaining> -p MemoryMax=5G -p OOMPolicy=continue \
        -E OPENBLAS_NUM_THREADS=1 -E OMP_NUM_THREADS=1 -E MKL_NUM_THREADS=1 \
        --working-directory=<worktree> uv run python \
        scripts/pilot_search_misses.py --workers 2 --split <abs> --store <abs>

    # for the LOG entry at R
    uv run python scripts/pilot_search_misses.py --print-hashes

    # after any kind of death, by hand: the label, from the records alone
    uv run python scripts/pilot_search_misses.py --split <abs> --store <abs> \
        --finalise

**The search.** R0 is the stored fit, rerun by the public call. R1 is K seeded
ascents with fit/5's own ``_ascend`` on a fresh ``_Profile``; R2 enumerates the
rest of the grid where it is small enough. Neither takes a ψ, a coefficient or
a stored value (:func:`reference_search`), and R1 refuses a profile that is not
empty, so the leak of round 1's draft cannot come back through shared state.
Known values for controls are computed in :func:`known_value`, on the public
fit's own profile, after the reference result is recorded.

**Processes.** A job is one fit, in one process that serves that job only
(:class:`Driver`, no shared pool), so ``RLIMIT_CPU`` is per job and a job's
death cannot break a sibling. The process's exit is read by :func:`exit_kind`:
SIGXCPU is the CPU limit (never retried), any other signal or an exit without
a result is a lost worker (retried once, after the stage), and an uncaught
exception is a bug, which stops the run.

**Records.** ``journal.jsonl`` is appended and fsynced per record by whichever
process owns the job, so the driver may die at any moment: ``--finalise`` needs
nothing else. ``units.jsonl`` is the journal's job records, sorted and without
timing, so it is byte-identical on a rerun. Wall and CPU times go to
``timing.jsonl`` only. Floats are ``float.hex`` strings everywhere.

**What this script does not write:** the note of section 10, and the
exploratory table of section 4. ``units.jsonl`` carries their inputs.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
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
from typing import Any, Final, Literal, Protocol

import numpy as np
import pilot

from sciagent.campaign import analysis, cpu
from sciagent.campaign.analysis import Row, Table, Verdict
from sciagent.campaign.execute import (
    UNIT_FAILURES,
    observational_data,
    score_submission,
)
from sciagent.campaign.plan import (
    B_LIB,
    B_NP,
    B_RAND,
    B_SPARSE,
    B_SYM_10F,
    ORACLE,
    PLANTED,
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
# Set at R (section 2.10): empty refuses every start
# --------------------------------------------------------------------------

#: sha256 of the frozen pre-registration, in code, not typed by the operator.
PREREG_SHA256: Final = (
    "ea71cdea282c9dac13586649b504286e11221958aa5764aba16326a37c972659"
)
#: The commit that freezes it.
F_COMMIT: Final = "3226f9b9521fe3a8c1d2445d1654cd1609b1eb2d"

# --------------------------------------------------------------------------
# The hunt, group C (section 2.9)
# --------------------------------------------------------------------------

#: Cases per sub-stratum.
HUNT_SIZE: Final = 150
#: R1 runs on this many found losses per sub-stratum, in index order.
HUNT_R1_PER_STRATUM: Final = 25
#: CPU hours of the whole of group C, R1 included.
HUNT_CPU_HOURS: Final = 6.0
HUNT_HORIZON: Final = 300.0
#: Root of the hunt's logs. Like ``SEED_ROOT`` it runs once, at the launch, so
#: :func:`hunt_log` takes it as an argument. R1 on a hunt case uses ``SEED_ROOT``.
HUNT_LOG_ROOT: Final = 20261106

# --------------------------------------------------------------------------
# Fixed by the text
# --------------------------------------------------------------------------

PREREG_PATH: Final = "docs/v2/prereg/2026-11-04_pilot-search-misses.md"
LOG_PATH: Final = "docs/v2/LOG.md"
SCRIPT_PATH: Final = "scripts/pilot_search_misses.py"
TEST_PATH: Final = "tests/campaign/test_pilot_search_misses.py"
BASE_COMMIT: Final = "900e66a"
GUARDED_PATHS: Final = (
    "src/sciagent/glm",
    "src/sciagent/core",
    "src/sciagent/scoring",
    "src/sciagent/systems",
    "src/sciagent/scenarios",
    "src/sciagent/campaign/analysis.py",
    "src/sciagent/campaign/execute.py",
    "src/sciagent/campaign/plan.py",
    "src/environments",
    "scripts/pilot.py",
    "pyproject.toml",
    "uv.lock",
)
BLAS_VARIABLES: Final = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
)
SPLIT_SHA256: Final = "31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577"
#: The split's path as the pinned hash names it, whatever checkout holds it.
SPLIT_RELATIVE: Final = "data/v2/truths/dev"
EXPECTED_ROWS: Final = 670

SEED_ROOT: Final = 20261104
K_STARTS: Final = 48
S2_STARTS: Final = 16
#: R1's new inner solves per fit, by link; a control takes its own link's. The
#: budget is points, not starts: K is at most 48, and a fit that reaches its cap
#: is ``capped``, with its completed starts on record.
R1_POINT_CAP: Final = 8_000
R1_POINT_CAP_IDENTITY: Final = 3_000
#: S2's reference: new solves per link.
S2_POINT_CAP: Final = 2_000
R2_GRID_MAX: Final = 14_400
#: Points per ``evaluate`` call in R2 (a memory bound; no number depends on it).
R2_CHUNK: Final = 2000
TOL_FLOOR: Final = 3e-7
H3_LIMIT: Final = gonogo.BSYM_TOLERANCE
VERDICT_SEED: Final = 20261002
VERDICT_RESAMPLES: Final = 10_000
S1_MIN_MISSES: Final = 5
S1_RECALL_FLOOR: Final = 0.8
#: ``audit.GAP_THRESHOLD``, the ``hunt_gap`` gate S4 counts.
GAP_GATE: Final = 0.01

CPU_STOP_HOURS: Final = 78
CPU_CEILING_HOURS: Final = 84
JOB_CPU_HOURS: Final = 3
#: A job stops searching here, flagged ``cpu_cut``, rather than reach the limit.
JOB_CUT_HOURS: Final = 2.5
WALL_HOURS: Final = 60
#: A SIGTERM this near the 60 hours is ``RuntimeMaxSec``; earlier it is a reboot.
#: The driver's clock starts after the store is read, so it runs a little behind.
WALL_MARGIN_S: Final = 600.0
#: How a run may have ended and still be resumed (section 5): no ``run_end`` at
#: all, or a SIGTERM with wall time left.
RESUMABLE_ENDS: Final = ("killed", "terminated")
MEM_GATE_GIB: Final = 8
#: Hard ``RLIMIT_CPU`` above the soft one: the soft limit's SIGXCPU kills the
#: job; only a SIGXCPU that was somehow ignored reaches this SIGKILL. Equal
#: limits would send SIGKILL alone, which reads as a lost worker.
CPU_HARD_MARGIN_S: Final = 60
START_METHOD: Final = "spawn"

#: The pilot's own configuration (``count4.py``, the PILOT.md command).
PILOT_TRUTHS: Final = 20
PILOT_CONTROL_TRUTHS: Final = 10
PILOT_MAX_TURNS: Final = 100

T2_CASES: Final = (
    (0, "softplus", 103648458),
    (2, "exp", 1499614403),
    (0, "softplus", 1074),
    (0, "softplus", 3273957339),
    (0, "softplus", 358866363),
    (0, "softplus", 2006407976),
)
GROUP_B: Final = ("dev-015", 3)
OUT_BASE: Final = Path("/home/ubuntu/research/records/sciagent")

EXIT_CODES: Final = {
    "BYTE_FAIL": 3,
    "INCOMPLETE": 5,
    "GO_CHANGES": 2,
    "CONTROL_FAIL": 4,
    "GO_SURVIVES_FOUND_MISSES": 0,
}
STATEMENT: Final = (
    "The computed GO survives substitution of the {n} misses found (N counts "
    "baseline rows, the ones the verdict reads). Of the {t} fits the verdict "
    "reads, {e} are exact over their certified grid points, {u} are searched and "
    "unvalidated, {x} are exhaustive but uncertified and were not checked, and "
    "{a} are outside the check (B-np, B-lib's out-of-grammar members). Nothing is "
    "said about misses on the {u} fits that the search did not find."
)
#: Section 5's table: what each label means for the go/no-go.
GO_NO_GO: Final = {
    "GO_SURVIVES_FOUND_MISSES": {
        "survived": "It survived the misses found, in section 2.7's wording",
        "next": "The go/no-go goes to the owner with that wording and the per-gate "
        "line. No longer held for this check",
    },
    "GO_CHANGES": {
        "survived": "No, on the misses found so far",
        "next": "Not put forward as GO. The consequence for the clause is stated. "
        "The owner decides, and keeping GO is a logged override. With partial: true "
        "the flip may yet reverse, so the consequence is stated and not applied "
        "until the owner has decided",
    },
    "CONTROL_FAIL": {
        "survived": "Open",
        "next": "The computed GO is not put forward as final. The exact class is "
        "reported. The way on is a frozen amendment with a stronger reference, or "
        "the owner's own decision to go on with the gap stated, logged as an "
        "override",
    },
    "INCOMPLETE": {
        "survived": "Open",
        "next": "As CONTROL_FAIL. A run that died by SIGKILL, a reboot or the "
        "out-of-memory killer, below the 78-hour stop and with wall time left, may "
        "be resumed. A run at the 78-hour stop, at the wall limit or ended by STOP "
        "may not; the script refuses, and going on takes an amendment",
    },
    "BYTE_FAIL": {
        "survived": "Open",
        "next": "As CONTROL_FAIL, and the failure is an instrument fault: root "
        "cause first, by amendment, before any reading",
    },
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
S1_BELOW_FLOOR: Final = (
    "R1 missed more than one in five of the misses on grids where the answer is known."
)
CONSEQUENCES: Final = {
    "C1": "a control failure means fix the instrument first",
    "C2": "a control failure means fix the instrument first",
    "C3": (
        "B-sym is under-tuned and the cure is the dev tuning the LOG already "
        "requires (2026-10-02)"
    ),
    "too_easy": "a difficulty flip means a re-pilot",
    "too_hard": "a difficulty flip means a re-pilot",
}
NOT_PUT_FORWARD: Final = (
    "Under GO_CHANGES the computed GO is not put forward to the owner as GO. "
    "The stored verdict stays in the record as what was computed. Keeping it "
    "anyway is the owner's decision, logged as an override."
)
NO_CONTROL: Final = "no known miss, no control"

#: Job records: these reach ``units.jsonl``, in this order within a job.
UNIT_TYPES: Final = ("r0", "r1", "r2", "done", "r2_lost", "reference_failed")
#: ``r2_lost``: the job died after R1 was on record, so R1's result stands.
TERMINAL_TYPES: Final = ("done", "r2_lost", "reference_failed")


class GuardFailure(RuntimeError):
    """A start-up guard refused the run (section 2.10): no unit was read."""


class ByteFailure(RuntimeError):
    """A byte check failed. Not a ``SciAgentError``: it must never be taken
    for a unit failure and turned into ``reference_failed``."""


class ResumeRefused(RuntimeError):
    """A resume over the CPU or wall limit, or a second driver on one output."""


class CpuAccountingError(RuntimeError):
    """The unit's ``cpu.stat`` cannot be read, so the ceiling cannot be kept."""


class ContaminatedProfile(RuntimeError):
    """R1 was handed a profile that already held points (section 2.2)."""


# --------------------------------------------------------------------------
# Seeds (section 2.2)
# --------------------------------------------------------------------------


def fit_case(truth: str, seed: int, text: str) -> str:
    return f"{truth}|{seed}|{text}"


def t2_case(which: int, link: str, seed: int) -> str:
    return f"t2|{which}|{link}|{seed}"


def hunt_case(stratum: str, i: int) -> str:
    return f"hunt|{stratum}|{i}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reference_rng(c: str, root: int) -> np.random.Generator:
    """The case's stream. ``root`` has no default: :data:`SEED_ROOT` runs once,
    at the launch, so every call says which root it means."""
    h = int(_sha(c)[:16], 16)
    return np.random.default_rng(np.random.SeedSequence([root, h]))


def r1_cap(link: str) -> int:
    return R1_POINT_CAP_IDENTITY if link == Link.IDENTITY.value else R1_POINT_CAP


def fit_job_id(truth: str, seed: int, text: str) -> str:
    return _sha(fit_case(truth, seed, text))


# --------------------------------------------------------------------------
# Values, tolerance and the miss rule (section 2.3)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Valued:
    """A fit's training log L, its absolute gap and its certificate."""

    ll: float
    gap: float
    certified: bool


@dataclass(frozen=True)
class PointValue:
    """One ψ point as a search saw it: ``score`` is ``-primal``."""

    point: _Point
    score: float
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


def choose_point(r0: PointValue, reference: PointValue | None) -> PointValue | None:
    """The highest-scoring certified point of R0 to R2, ties to the lowest
    index tuple. ``reference`` is the certified best of R1 and R2."""
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
    """Section 2.3: L is exact only up to ``quadrature_error``, which differs by
    ψ. Such a miss stays in M and is listed."""
    return gain <= abs(stored_error) + abs(chosen_error)


def is_exhaustive(grid_size: int) -> bool:
    """Whether fit/5's ``_search`` takes the whole grid (section 2.1)."""
    return grid_size <= PSI_FULL_GRID_MAX


def held_out_delta(stored: float, corrected: float) -> tuple[float | None, str]:
    """Δ and its kind (section 2.4); ``-inf`` to ``-inf`` is 0 by definition."""
    if stored == corrected:
        return 0.0, "finite"
    if not (math.isfinite(stored) and math.isfinite(corrected)):
        return None, "infinite_move"
    return corrected - stored, "finite"


def h3_falsified(deltas: Iterable[tuple[float | None, str]]) -> bool:
    return any(d is None or abs(d) > H3_LIMIT for d, _ in deltas)


def control_caught(best: Valued | None, fit5: Valued, known: Valued | None) -> bool:
    """Section 2.9: the search's own best is at least the known value minus
    the tolerance, and exceeds fit/5's value by more than the tolerance."""
    if best is None or known is None:
        return False
    return best.ll >= known.ll - tolerance(
        best, known
    ) and best.ll > fit5.ll + tolerance(best, fit5)


def found_loss(base: Valued, generating: Valued) -> bool:
    """Section 2.9: the fixed-ψ fit is certified and beats fit/5's L by more
    than the tolerance. fit/5's own flag is no condition: it is false when any
    visited point was uncertified."""
    return generating.certified and call_miss(generating, base).kind == "miss"


# --------------------------------------------------------------------------
# R1 and R2 (section 2.2)
# --------------------------------------------------------------------------


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
    """Every remaining point of the product grid, on the same profile. False
    if ``should_stop`` ended it between chunks."""
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
    """R1, then R2 where the grid allows. Blind by construction: the arguments
    are the structure, its data and a stream, and the profile is its own.
    ``on_r1`` runs before R2 starts, so R1's result is on record first."""
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
    """This process's CPU against :data:`JOB_CUT_HOURS`: a clock, so a fit it
    fires on is ``cpu_cut`` and the record says so."""
    return time.process_time() >= JOB_CUT_HOURS * 3600.0


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


def valued_json(value: Valued) -> Record:
    return {"ll": value.ll, "gap": value.gap, "certified": value.certified}


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


def write_units(records: Iterable[Mapping[str, Json]], path: Path) -> None:
    """``units.jsonl``: job records, sorted, one per (job, type). A retry
    rewrites identical bytes; different bytes are a determinism failure."""
    lines: dict[tuple[str, int], str] = {}
    for record in records:
        kind = record["type"]
        if kind not in UNIT_TYPES:
            continue
        key = (str(record["job"]), UNIT_TYPES.index(kind))
        line = dumps_record(dict(record))
        if lines.setdefault(key, line) != line:
            raise ByteFailure(f"two different {kind} records for job {record['job']}")
    replace_file(path, "".join(lines[k] + "\n" for k in sorted(lines)).encode("utf-8"))


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
    """One sub-stratum of section 2.9's table. Its position ``s`` in
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
    """Case ``j`` of a sub-stratum, drawn as section 2.9 fixes it: the point,
    the coefficients, then the log, from one stream."""
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


def _hex_or_none(value: Json) -> str | None:
    return None if value is None else float(value).hex()


def byte_check(
    row: RowExpect, per_event: str, details: Mapping[str, Json] | None
) -> list[str]:
    """Section 2.4: what differs between a cell and R0 (empty: it matches)."""
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
    """One fit: R0, R1, R2 where due, in one process (section 6)."""

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
    """One row against its own stored fit (section 2.3). The reference is
    shared; the row's R0 is not, so ORACLE's kept fit never reaches a sibling."""
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
    """Section 6's job. An R0 that does not reproduce its cell ends the job
    before any search; a unit failure in R1 or R2 is ``reference_failed``;
    anything else propagates, which is a bug and stops the run."""
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
        r0 = _r0_payload(structure, data, stored)
    except UNIT_FAILURES as error:
        # The cell holds a fit, so an R0 that cannot make one is not the
        # stored fit: section 6 covers R1 and R2 only.
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


@dataclass(frozen=True)
class T2Job:
    """A group A or D control. Group D is R2 alone on an empty profile: K = 0,
    no R1, and the one control that does not test R1."""

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
    """``i`` of the seeding rule: ``1000 * s + j`` (section 2.9)."""
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
    """R1 on a found loss. It holds no known value: whether R1 caught the loss
    is read later, from this job's record and the case's."""

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
    records: Sequence[Mapping[str, Json]], *, root: int, log_root: int
) -> list[HuntR1Job]:
    """The R1 jobs due: the first found losses of each sub-stratum in index
    order, counted only as far as every earlier case has finished. So the set
    is a function of the cases' results, never of which worker finished first.
    They run after the fit/5 jobs, inside the same cap."""
    done = {r["job"]: r for r in records if r["type"] == "done"}
    failed = {r["job"] for r in records if r["type"] == "reference_failed"}
    out: list[HuntR1Job] = []
    for spec in HUNT_STRATA:
        picked = 0
        for j in range(HUNT_SIZE):
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
    """``sparse._choose_link``'s rule with its fit made a parameter, so S2 can
    rerun it with R0 and with the reference. A ``blocked`` link stays ``-inf``:
    the rule reads the fit-level flag, which a single-point refit passes more
    easily, so no link may win for that reason alone."""
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
) -> FitResult:
    """R0 and R1 on the training window (section 4, S2). An exhaustive grid is
    settled by R0."""
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
        should_stop=past_job_cut,
    )
    chosen = choose_point(r0_value(structure, train, channels, base), outcome.r1.best)
    if chosen is None:
        return base
    return refit_at(structure, train, channels, chosen)


@dataclass(frozen=True)
class S2Job:
    """B-sparse's link choice on one row (section 4, S2)."""

    truth: TruthEntry
    seed: int
    text: str
    stored_link: str
    env: CampaignEnvironment
    root: int

    @property
    def id(self) -> str:
        return _sha(f"s2|{fit_case(self.truth.id, self.seed, self.text)}")

    def run(self, sink: RecordSink) -> None:
        run_s2_job(self, sink)


def run_s2_job(job: S2Job, sink: RecordSink) -> None:
    channels, sparse = job.env.channels, SparseConfig()
    data = observational_data(job.truth, job.seed, job.env)
    (obs,) = data.observational
    train_set, validation_set = split_dataset(obs, sparse.train_fraction)
    train, validation = (train_set,), (validation_set,)
    features = parse(job.text, channels).features
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
            sink.record("done", {**payload, "status": "link_mismatch"})
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
                case=f"s2|{job.truth.id}|{job.seed}|{render(s)}",
                root=job.root,
            ),
            blocked=frozenset(Link(x[0]) for x in r0_scores if x[1] == -math.inf),
        )
        payload |= {"reference_link": ref_link.value, "reference_scores": ref_scores}
        if ref_link is r0_link:
            sink.record("done", {**payload, "status": "same"})
            return
        structure = canonicalise(Structure(features, ref_link))
        text = render(structure)
        outcome = reference_search(
            structure,
            data.observational,
            channels,
            reference_rng(fit_case(job.truth.id, job.seed, text), job.root),
            k=K_STARTS,
            cap=r1_cap(ref_link.value),
            r2_max=R2_GRID_MAX,
            should_stop=past_job_cut,
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
            "reference_text": text,
            "corrected_per_event": per_event,
        },
    )


# --------------------------------------------------------------------------
# The driver: one process per job (sections 6, 8, 9)
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
    """How a job's process ended (module docstring). An uncaught exception
    exits 1 through ``multiprocessing``; the kernel's limit is SIGXCPU."""
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
    """The code that wrote ``cpu.json`` is the code running now (section 2.10).
    A change to the script is an amendment, with a new output directory."""
    if not path.exists():
        return
    recorded = json.loads(path.read_text(encoding="utf-8")).get("script_sha256")
    current = script_sha or script_sha256()
    if recorded != current:
        raise ResumeRefused(
            f"script hash {current} is not the first start's {recorded}"
        )


def check_start(paths: OutPaths) -> None:
    """Section 5: a resume only after SIGKILL, a reboot or the out-of-memory
    killer, never with ``STOP`` present; and a fresh start needs a fresh
    directory (section 2.10). The CPU and wall limits are the ledger's."""
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


def read_wall_s(path: Path) -> float:
    if not path.exists():
        return 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    return float(data["earlier_wall_s"] + data["current_wall_s"])


def launch_line(
    used_wall_s: float, worktree: Path, split: Path, store: Path, out: Path
) -> str:
    """The unit's launch line, with the wall time left out of 60 hours."""
    remaining = max(int(WALL_HOURS * 3600.0 - used_wall_s), 0)
    env = " ".join(f"-E {name}=1" for name in BLAS_VARIABLES)
    return (
        "systemd-run --user --unit=sci-search-misses --nice=10 -p CPUQuota=200% "
        f"-p RuntimeMaxSec={remaining} -p MemoryMax=5G -p OOMPolicy=continue {env} "
        f"--working-directory={worktree} uv run python {SCRIPT_PATH} --workers 2 "
        f"--split {split} --store {store} --out {out}"
    )


def resume_line(
    cpu_json: Path, worktree: Path, split: Path, store: Path, out: Path
) -> str | None:
    """The line a resume is launched with; None if it would be refused."""
    try:
        data = json.loads(cpu_json.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    used = data.get("earlier_usec", 0) + data.get("current_usec", 0)
    wall = read_wall_s(cpu_json)
    if used >= CPU_STOP_HOURS * 3600 * 10**6 or wall >= WALL_HOURS * 3600.0:
        return None
    return launch_line(wall, worktree, split, store, out)


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
    """Runs stages of jobs, each in its own process (sections 6, 8, 9).

    The driver owns no result: it reads a job's exit and the journal, and adds
    ``job_lost``, ``reference_failed`` for what a dead job could not write, and
    ``run_start`` / ``run_end``.
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
        """SIGTERM is ``RuntimeMaxSec`` only when the 60 hours are spent: each
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
                self._kill(running)
                return reason
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
        """Let the jobs in flight finish; a loss now waits for a resume."""
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
# Start-up guards (section 2.10)
# --------------------------------------------------------------------------


def initial_environment() -> dict[str, str]:
    """The environment this process was started with. ``import pilot`` sets
    the BLAS variables in ``os.environ``, so that cannot show they were set
    before numpy loaded."""
    data = Path("/proc/self/environ").read_bytes()
    pairs = (item.partition(b"=") for item in data.split(b"\0") if item)
    return {k.decode(): v.decode() for k, _, v in pairs}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def run_guards(
    repo: Path,
    *,
    prereg_sha: str,
    f_commit: str,
    base_commit: str,
    environ: Mapping[str, str],
    packages: Sequence[Path] | None = None,
) -> dict[str, str]:
    """Every start-up guard of section 2.10; raises :class:`GuardFailure`.
    Returns F, R and the hash, as ``decision.json`` records them."""
    if not prereg_sha or not f_commit:
        raise GuardFailure(
            "PREREG_SHA256 and F_COMMIT are not set: the text is not frozen"
        )
    prereg = repo / PREREG_PATH
    if not prereg.is_file():
        raise GuardFailure(f"no pre-registration at {prereg}")
    digest = hashlib.sha256(prereg.read_bytes()).hexdigest()
    if digest != prereg_sha:
        raise GuardFailure(f"prereg hash {digest} is not PREREG_SHA256 {prereg_sha}")
    try:
        pilot.code_fingerprint(repo, packages)
    except CampaignError as error:
        raise GuardFailure(str(error)) from error
    if _git(repo, "merge-base", "--is-ancestor", f_commit, "HEAD").returncode != 0:
        raise GuardFailure(f"HEAD does not descend from F {f_commit}")
    changed = _git(repo, "diff", "--name-only", f_commit, "HEAD")
    if changed.returncode != 0:
        raise GuardFailure(f"git diff F HEAD failed: {changed.stderr.strip()}")
    paths = changed.stdout.split()
    if PREREG_PATH in paths:
        raise GuardFailure(f"the prereg changed after F {f_commit}")
    outside = sorted(p for p in paths if not p.startswith("docs/"))
    if outside != sorted((SCRIPT_PATH, TEST_PATH)):
        raise GuardFailure(f"outside docs/, F..HEAD lists {outside}")
    quiet = _git(repo, "diff", "--quiet", base_commit, "HEAD", "--", *GUARDED_PATHS)
    if quiet.returncode != 0:
        raise GuardFailure(f"a guarded path differs from base {base_commit}")
    log_path = repo / LOG_PATH
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    for path in (SCRIPT_PATH, TEST_PATH):
        file_sha = hashlib.sha256((repo / path).read_bytes()).hexdigest()
        if file_sha not in log:
            raise GuardFailure(f"{path} hash {file_sha} is not in the LOG entry at R")
    for name in BLAS_VARIABLES:
        if environ.get(name) != "1":
            raise GuardFailure(f"{name} must be 1 at start, is {environ.get(name)!r}")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return {"F": f_commit, "R": head, "prereg_sha256": prereg_sha}


def check_paths(*, split: Path, store: Path, out: Path, repo: Path) -> None:
    for name, path in (("--split", split), ("--store", store), ("--out", out)):
        if not path.is_absolute():
            raise GuardFailure(f"{name} must be an absolute path: {path}")
    for parent in (repo, store):
        if out.resolve().is_relative_to(parent.resolve()):
            raise GuardFailure(f"--out {out} must be outside {parent}")


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


# --------------------------------------------------------------------------
# The pilot's inputs: one place opens the store, read only
# --------------------------------------------------------------------------


def pilot_inputs(
    split: Path,
) -> tuple[CampaignEnvironment, CampaignConfig, tuple[TruthEntry, ...]]:
    """The arguments the census gave ``load_rows`` (``count4.py``), from
    ``split`` only: ``pilot.DEFAULT_SPLIT`` is under the worktree, which has
    none (QUEUE.md:88)."""
    records = pilot.load_split(split)
    env = pilot.pointproc_environment(records)
    config = CampaignConfig(
        seeds=(1, 2, 3),
        control_seeds=(1,),
        max_turns=PILOT_MAX_TURNS,
        control_truths=PILOT_CONTROL_TRUTHS,
    )
    return env, config, pilot.truth_entries(pilot.select(records, PILOT_TRUTHS, None))


def stored_cells(
    store: ResultStore,
    env: CampaignEnvironment,
    config: CampaignConfig,
    truths: Sequence[TruthEntry],
) -> dict[RowKey, Mapping[str, Json]]:
    """Each row's raw cell, by the addresses ``load_rows`` reads: a
    :class:`Row` keeps neither ``certified`` nor the reading's hex."""
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


def load_store(
    root: Path,
    env: CampaignEnvironment,
    config: CampaignConfig,
    truths: Sequence[TruthEntry],
) -> tuple[Table, dict[RowKey, Mapping[str, Json]]]:
    with ResultStore(root, read_only=True) as store:
        table = analysis.load_rows(store, env, config, truths, sessions="stored")
        cells = stored_cells(store, env, config, truths)
    return table, cells


def row_key(row: Row) -> RowKey:
    return (row.truth, row.seed, row.system)


def check_table(
    table: Table,
    cells: Mapping[RowKey, Mapping[str, Json]],
    *,
    expected_rows: int = EXPECTED_ROWS,
) -> None:
    """Every cell of the census is there, at the grid cap the text relies on.
    The count is needed too: a session without a score cell is not ``missing``."""
    if any(table.missing.values()):
        raise GuardFailure(f"missing cells: {dict(sorted(table.missing.items()))}")
    if len(table.rows) != expected_rows:
        raise GuardFailure(f"{len(table.rows)} rows, expected {expected_rows}")
    for row in table.rows:
        cell = cells.get(row_key(row))
        if cell is None:
            raise GuardFailure(f"missing cell for {row_key(row)}")
        fields = cell["key"]["config"]["fit_config"].split("|")
        if "full=512" not in fields or "sweeps=0" not in fields:
            raise GuardFailure(f"{row_key(row)}: not full=512 and sweeps=0")


# --------------------------------------------------------------------------
# The census (section 2.1)
# --------------------------------------------------------------------------


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


@dataclass(frozen=True)
class Census:
    rows: tuple[RowInfo, ...]

    def fits(self) -> list[FitGroup]:
        """The distinct coordinate fits, by ``sha256`` of their case."""
        groups: dict[tuple[str, int, str], list[RowInfo]] = {}
        for r in self.rows:
            if r.klass == "coordinate" and r.text is not None:
                groups.setdefault((r.truth, r.seed, r.text), []).append(r)
        out: list[FitGroup] = []
        for (truth, seed, text), rows in groups.items():
            members = sorted(rows, key=lambda r: r.system)
            out.append(
                FitGroup(
                    truth,
                    seed,
                    text,
                    tuple(r.expect for r in members if r.expect is not None),
                    2 if all(r.llm for r in members) else 1,
                    members[0].grid_size or 0,
                    members[0].link or "",
                )
            )
        return sorted(out, key=lambda f: fit_job_id(f.truth, f.seed, f.text))


def classify_row(row: Row, *, grid_size: int | None, certified: bool | None) -> str:
    """Section 2.1's table: exactly one class per row."""
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
) -> Census:
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
        expect = None
        if klass == "coordinate":
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
            )
        )
    return Census(tuple(out))


def hunt_jobs(*, log_root: int) -> tuple[HuntJob, ...]:
    """Round-robin, ``j`` outer and ``s`` inner, so a cap cuts the sub-strata
    evenly."""
    return tuple(
        HuntJob(s.name, j, log_root) for j in range(HUNT_SIZE) for s in HUNT_STRATA
    )


def plan_stages(
    census: Census,
    truths: Mapping[str, TruthEntry],
    env: CampaignEnvironment,
    *,
    root: int,
    log_root: int,
) -> tuple[Stage, ...]:
    """Section 2.9's order A, D, C, B, so group B is the first pilot unit
    read; then tier 1, S2, tier 2 (section 8)."""
    fits = census.fits()

    def t2(case: tuple[int, str, int], group: str, with_r2: bool, k: int) -> T2Job:
        return T2Job(*case, group, with_r2, root, k, r1_cap(case[1]))

    def fit_job(f: FitGroup, group: str | None = None) -> FitJob:
        return FitJob(
            truth=truths[f.truth],
            seed=f.seed,
            text=f.text,
            rows=f.rows,
            env=env,
            root=root,
            k=K_STARTS,
            cap=r1_cap(f.link),
            r2_max=R2_GRID_MAX,
            group=group,
        )

    group_b = [
        f
        for f in fits
        if (f.truth, f.seed) == GROUP_B and any(r.system == ORACLE for r in f.rows)
    ]
    if len(group_b) != 1:
        raise GuardFailure(f"group B {GROUP_B} is not one coordinate ORACLE fit")
    sparse = sorted(
        (r for r in census.rows if r.system == B_SPARSE and r.klass == "coordinate"),
        key=lambda r: (
            truths[r.truth].in_dictionary,
            fit_job_id(r.truth, r.seed, r.text or ""),
        ),
    )
    return (
        Stage("group_a", tuple(t2(c, "A", False, K_STARTS) for c in T2_CASES)),
        Stage("group_d", (t2(T2_CASES[0], "D", True, 0),)),
        Stage(
            "hunt",
            hunt_jobs(log_root=log_root),
            cpu_cap_s=HUNT_CPU_HOURS * 3600.0,
            follow_up=partial(hunt_r1_jobs, root=root, log_root=log_root),
        ),
        Stage("group_b", (fit_job(group_b[0], "B"),)),
        Stage("tier1", tuple(fit_job(f) for f in fits if f.tier == 1)),
        Stage(
            "s2",
            tuple(
                S2Job(
                    truths[r.truth],
                    r.seed,
                    r.text or "",
                    r.sparse_link or "",
                    env,
                    root,
                )
                for r in sparse
            ),
        ),
        Stage("tier2", tuple(fit_job(f) for f in fits if f.tier == 2)),
    )


# --------------------------------------------------------------------------
# The corrected verdict (section 2.7)
# --------------------------------------------------------------------------


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


def corrected_verdict(
    table: Table,
    corrected: Mapping[RowKey, float],
    underfit: Mapping[RowKey, bool],
    *,
    resamples: int = VERDICT_RESAMPLES,
) -> Verdict:
    new = corrected_table(table, corrected, underfit)
    return analysis.verdict(
        new, np.random.default_rng(VERDICT_SEED), resamples=resamples
    )


def check_baseline_verdict(table: Table, *, resamples: int = VERDICT_RESAMPLES) -> None:
    """The same code with nothing substituted is the stored verdict, a ``go``."""
    stored = analysis.verdict(
        table, np.random.default_rng(VERDICT_SEED), resamples=resamples
    )
    rebuilt = corrected_verdict(table, {}, {}, resamples=resamples)
    if rebuilt != stored:
        raise ByteFailure("the empty substitution does not return the stored verdict")
    if stored.outcome != "go":
        raise ByteFailure(f"the stored verdict is {stored.outcome!r}, not go")


def gate_values(v: Verdict) -> Record:
    """Each §7.1 statistic of a verdict."""
    planted = v.planted
    return {
        "outcome": v.outcome,
        "too_easy": {
            "flag": v.too_easy,
            "recovery_b_lib": v.recovery.get(B_LIB),
            "recovery_b_rand": v.recovery.get(B_RAND),
            "sparse_ood_close": v.sparse_ood_close,
        },
        "too_hard": {
            "flag": v.too_hard,
            "close": {k: v.close[k] for k in sorted(v.close)},
        },
        "C1": {
            "kept": list(v.kept),
            "dropped": list(v.dropped),
            "underfit_passed": None if v.underfit is None else v.underfit.passed,
            "oracle_inf": [list(u) for u in v.oracle_inf],
        },
        "C2": None
        if planted is None
        else {
            "passed": planted.passed,
            "wins": planted.wins,
            "p_value": planted.p_value,
            "median": planted.median,
        },
        "C3": None
        if v.bsym is None
        else {"passed": v.bsym.passed, "median_gap": v.bsym.median_gap},
        "reasons": list(v.reasons),
        "inconclusive": list(v.inconclusive),
    }


def _clause_states(v: Verdict) -> dict[str, Json]:
    return {
        "too_easy": v.too_easy,
        "too_hard": v.too_hard,
        "C1": (
            v.kept,
            v.dropped,
            None if v.underfit is None else v.underfit.passed,
            v.oracle_inf,
        ),
        "C2": None if v.planted is None else v.planted.passed,
        "C3": None if v.bsym is None else v.bsym.passed,
    }


def clauses_moved(stored: Verdict, corrected: Verdict) -> list[Record]:
    """Which §7.1 clauses moved, each with the consequence P4-PROPOSAL fixes."""
    a, b = _clause_states(stored), _clause_states(corrected)
    return [
        {"clause": name, "consequence": CONSEQUENCES[name]}
        for name in ("C1", "C2", "C3", "too_easy", "too_hard")
        if a[name] != b[name]
    ]


# --------------------------------------------------------------------------
# Controls and secondaries from records (sections 2.9, 4)
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
    chosen point: R0 plays no part (section 2.9)."""
    r1 = {r["job"]: r for r in records if r["type"] == "r1"}
    caught: dict[str, list[bool]] = {"A": [], "B": [], "D": []}
    cases: list[Record] = []
    for record in records:
        if record["type"] != "done":
            continue
        control = _control_of(record)
        if control is None:
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
        s.name: {
            "completed": 0,
            "found_losses": 0,
            "r1_run": 0,
            "caught": 0,
            "not_run": 0,
            "uncertified": 0,
            "sim_failed": 0,
        }
        for s in HUNT_STRATA
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
# The label (section 5) and finalise (section 8)
# --------------------------------------------------------------------------


def journal_end(records: Sequence[Mapping[str, Json]]) -> str:
    """How the last driver ended: "killed" if it never said. A STOP stays
    INCOMPLETE through a resume, so that a stop cannot be laundered."""
    if any(r["type"] == "run_end" and r["reason"] == "stop" for r in records):
        return "stop"
    last = "killed"
    for r in records:
        if r["type"] == "run_start":
            last = "killed"
        elif r["type"] == "run_end":
            last = r["reason"]
    return last


def end_state(records: Sequence[Mapping[str, Json]], stop: Path) -> str:
    return "stop" if stop.exists() else journal_end(records)


def decide_label(
    *,
    byte_fail: bool,
    tier1_complete: bool,
    outcome: str,
    h1: bool,
) -> str:
    """Section 5: the first rule that applies. A flip outranks every shortfall,
    so no stop can bury it; a stop after tier 1 is reported, and moves nothing."""
    if byte_fail:
        return "BYTE_FAIL"
    if outcome != "go":
        return "GO_CHANGES"
    if not tier1_complete:
        return "INCOMPLETE"
    return "GO_SURVIVES_FOUND_MISSES" if h1 else "CONTROL_FAIL"


GATES: Final = ("too_easy", "too_hard", "C1", "C2", "C3")


def gate_reads(gate: str, row: RowInfo, truths: Mapping[str, TruthEntry]) -> bool:
    """Whether a §7.1 gate reads a baseline row. ORACLE is every gate's gap
    reference, and B-lib the gap closed's."""
    if row.llm:
        return False
    match gate:
        case "too_easy":
            ood = not truths[row.truth].in_dictionary
            return row.system in (ORACLE, B_LIB, B_RAND) or (
                row.system == B_SPARSE and ood
            )
        case "too_hard":
            return True
        case "C1":
            return row.system in (ORACLE, B_LIB)
        case "C2":
            return row.system in (ORACLE, PLANTED, B_RAND)
        case "C3":
            return row.system in (ORACLE, B_SYM_10F)
    raise ValueError(f"unknown gate {gate!r}")


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


def _statement_counts(
    census: Census, done: Mapping[str, Mapping[str, Json]]
) -> dict[str, int]:
    """T, E, U, X and A of section 2.7's wording. A fit with one uncertified
    row counts as uncertified."""
    rank = {"exhaustive_uncertified": 0, "unchecked": 1, "searched": 2, "exact": 3}
    fits: dict[tuple[str, int, str | None], str] = {}
    outside = 0
    for r in census.rows:
        state = row_state(r, done)
        if r.llm:
            continue
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


def _gate_counts(
    census: Census,
    table: Table,
    done: Mapping[str, Mapping[str, Json]],
) -> dict[str, Record]:
    out: dict[str, Record] = {}
    for gate in GATES:
        counts: Record = {
            "exact": 0,
            "searched": 0,
            "exhaustive_uncertified": 0,
            "not_applicable": 0,
        }
        for r in census.rows:
            if gate_reads(gate, r, table.truths):
                state = row_state(r, done)
                counts[state] = counts.get(state, 0) + 1
        out[gate] = counts
    return out


def _s3(
    fits: Sequence[Mapping[str, Json]],
) -> Record:
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
    job that died in R2 stays searched with any miss R1 found (section 6)."""
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
    (section 2.10)."""
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
    census: Census,
    table: Table,
    *,
    identity: Mapping[str, str],
    stop_note: str | None,
    manifest_failures: Sequence[str],
    resamples: int = VERDICT_RESAMPLES,
    provisional: bool = False,
) -> Record:
    """The label and everything beside it, from records and the stored table."""
    byte_failures = [str(r.get("reason")) for r in records if r["type"] == "byte_fail"]
    byte_failures += [f"store manifest: {x}" for x in manifest_failures]
    byte_failures.sort()
    try:
        check_baseline_verdict(table, resamples=resamples)
    except ByteFailure as error:
        byte_failures = sorted([*byte_failures, str(error)])
    fit_records = fit_views(records)
    done_fits = {r["job"]: r for r in fit_records}
    end = "stop" if stop_note is not None else journal_end(records)
    corrected, underfit, misses = _substitutions(fit_records)
    stored = corrected_verdict(table, {}, {}, resamples=resamples)
    v = corrected_verdict(table, corrected, underfit, resamples=resamples)
    new = corrected_table(table, corrected, underfit)
    fits = census.fits()

    def complete(f: FitGroup) -> bool:
        return fit_job_id(f.truth, f.seed, f.text) in done_fits

    tier1 = [f for f in fits if f.tier == 1]
    tier2 = [f for f in fits if f.tier == 2]
    tier1_complete = all(complete(f) for f in tier1)
    h1 = h1_status(records)
    label = decide_label(
        byte_fail=bool(byte_failures),
        tier1_complete=tier1_complete,
        outcome=v.outcome,
        h1=h1.stands,
    )
    n_baseline = sum(not row["llm"] for _, row in misses)
    counts = _statement_counts(census, done_fits)
    statement = None
    if label == "GO_SURVIVES_FOUND_MISSES":
        statement = STATEMENT.format(n=n_baseline, **counts)
    found, total = s1_counts(fit_records, records)
    s1 = s1_status(found=found, total=total)
    hunt = hunt_summary(records)
    s2, s2_extra = _s2_rows(records)
    v2 = corrected_verdict(table, corrected | s2_extra, underfit, resamples=resamples)
    moved = clauses_moved(stored, v) if label == "GO_CHANGES" else []
    coverage = coverage_of(census, done_fits)
    deltas = [(unhex(row["delta"]), str(row["delta_kind"])) for _, row in misses]
    max_delta = _max_delta(misses)
    quadrature = [list(key) for key, row in misses if row.get("within_quadrature")]
    decision: Record = {
        **identity,
        "label": label,
        "exit_code": EXIT_CODES[label],
        "end": end,
        "provisional": provisional,
        "partial": not tier1_complete,
        "go_no_go": GO_NO_GO[label],
        "statement": statement,
        "stop_note": stop_note,
        "byte_failures": byte_failures,
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
            "falsified": v.outcome != "go",
            "outcome": v.outcome,
            "vacuous": not misses,
        },
        "M_rows": len(misses),
        "M_fits": sum(any(x["call"] == "miss" for x in r["rows"]) for r in fit_records),
        "M_by_system": _count(key[2] for key, _ in misses),
        "miss_rows": _miss_rows(misses, census, done_fits),
        "classes": _count(r.klass for r in census.rows),
        "within_quadrature_rows": quadrature,
        "clock_stops": _clock_stops(records, fit_records, end),
        "max_abs_delta": max_delta,
        "coverage": coverage,
        "gates": _gate_counts(census, table, done_fits),
        "control_state": _control_state(hunt),
        "strata": _strata(fits, done_fits, _control_state(hunt), h1),
        "hunt": hunt,
        "s1": {"found_by_r1": found, "misses": total},
        "s1_status": s1,
        "s2": s2,
        "s3": _s3(fit_records),
        "s4": _s4(table, new, misses),
        "stored_gates": gate_values(stored),
        "corrected_gates": gate_values(v),
        "v2_outcome": v2.outcome,
        "clauses_moved": moved,
        "not_put_forward": NOT_PUT_FORWARD if label == "GO_CHANGES" else None,
        "shortfall": {
            "tier1_missing": sum(not complete(f) for f in tier1),
            "tier2_missing": sum(not complete(f) for f in tier2),
            "s2_missing": sum(
                r.system == B_SPARSE and r.klass == "coordinate" for r in census.rows
            )
            - s2["done"],
        },
    }
    decision["label_line"] = label_line(decision, v.outcome)
    return decision


def label_line(d: Mapping[str, Json], outcome: str) -> str:
    """Section 5's line: the label and what stands beside it."""
    coverage = d["coverage"]
    parts = [
        f"{d['label']} (provisional)" if d["provisional"] else d["label"],
        f"H2 falsified={d['H2']['falsified']}",
        f"H3 falsified={d['H3']['falsified']}",
        f"M={d['M_rows']} rows, {d['M_fits']} fits",
        f"uncertified_better on identity {d['s3']['uncertified_better_identity']}",
        f"group A {d['H1']['group_a_caught']} of {d['H1']['group_a_of']}, group B "
        f"{d['H1']['group_b_caught']}, group D {d['H1']['group_d_caught']}",
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
    if d["v2_outcome"] != outcome:
        parts.append(
            f"v2.outcome differs from v.outcome: {d['v2_outcome']} against {outcome}"
        )
    gates = "; ".join(
        f"{name}: " + ", ".join(f"{k} {v}" for k, v in d["gates"][name].items())
        for name in GATES
    )
    parts.append(f"rows per gate: {gates}")
    parts.append(
        f"coverage: rows exact {coverage['exact_rows']}, searched "
        f"{coverage['searched_rows']}, unchecked {coverage['unchecked_rows']}, not "
        f"applicable {coverage['not_applicable_rows']}"
    )
    if d["statement"] is not None:
        parts.append(d["statement"])
    return " | ".join(parts)


def finalise_records(
    paths: OutPaths,
    census: Census,
    table: Table,
    *,
    identity: Mapping[str, str],
    manifest_failures: Sequence[str],
    resamples: int = VERDICT_RESAMPLES,
    provisional: bool = False,
) -> Record:
    """``units.jsonl`` and ``decision.json`` from the records alone. At the end
    of tier 1 the label is written ``provisional``, and ``units.jsonl`` waits for
    the end of the run, because a byte failure later still gives ``BYTE_FAIL``."""
    records = read_records(paths.journal)
    stop_note = None
    if paths.stop.exists():
        text = paths.stop.read_text(encoding="utf-8").strip()
        stop_note = text or "no reason was given"
    decision = decide(
        records,
        census,
        table,
        identity=identity,
        stop_note=stop_note,
        manifest_failures=manifest_failures,
        resamples=resamples,
        provisional=provisional,
    )
    if not provisional:
        write_units(records, paths.units)
    replace_file(paths.decision, (dumps_record(decision) + "\n").encode("utf-8"))
    return decision


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--split", type=Path, help="absolute path")
    p.add_argument("--store", type=Path, help="absolute path")
    p.add_argument(
        "--out", type=Path, default=OUT_BASE / f"search-misses-{F_COMMIT or 'F'}"
    )
    p.add_argument("--workers", type=int, choices=(1, 2), default=2)
    p.add_argument(
        "--finalise",
        action="store_true",
        help="write the label from the records, after any kind of death",
    )
    p.add_argument(
        "--print-hashes",
        action="store_true",
        help="print the script's and the test's sha256, for the LOG entry at R",
    )
    return p


def refuse(paths: OutPaths, reason: str) -> int:
    """A guard failed: BYTE_FAIL, over a provisional decision but never over a
    finished one."""
    print(f"BYTE_FAIL: {reason}", flush=True)
    paths.root.mkdir(parents=True, exist_ok=True)
    finished = paths.decision.exists() and not json.loads(
        paths.decision.read_text(encoding="utf-8")
    ).get("provisional", False)
    if not finished:
        body = {
            "label": "BYTE_FAIL",
            "exit_code": EXIT_CODES["BYTE_FAIL"],
            "byte_failures": [reason],
        }
        replace_file(paths.decision, (dumps_record(body) + "\n").encode("utf-8"))
    return EXIT_CODES["BYTE_FAIL"]


def offers_resume(end: str) -> bool:
    """Section 5: after SIGKILL, a reboot or the out-of-memory killer only."""
    return end in RESUMABLE_ENDS


def _manifest_failures(paths: OutPaths, store: Path) -> list[str]:
    if not paths.manifest.exists():
        return ["no manifest was taken before the run"]
    before = json.loads(paths.manifest.read_text(encoding="utf-8"))
    return manifest_diff(before, store_manifest(store))


def main(argv: Sequence[str] | None = None) -> int:
    pilot.reconfigure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.print_hashes:
        for path in (SCRIPT_PATH, TEST_PATH):
            digest = hashlib.sha256((pilot.ROOT / path).read_bytes()).hexdigest()
            print(f"{digest}  {path}")
        return 0
    if args.split is None or args.store is None:
        parser.error("--split and --store are required")
    paths = OutPaths(args.out)
    try:
        identity = run_guards(
            pilot.ROOT,
            prereg_sha=PREREG_SHA256,
            f_commit=F_COMMIT,
            base_commit=BASE_COMMIT,
            environ=initial_environment(),
        )
        check_paths(split=args.split, store=args.store, out=args.out, repo=pilot.ROOT)
        check_split(args.split)
    except GuardFailure as error:
        return refuse(paths, str(error))
    paths.root.mkdir(parents=True, exist_ok=True)
    print(f"script sha256 {script_sha256()}", flush=True)
    try:
        check_script(paths.cpu)
        if not args.finalise:
            check_start(paths)
            CpuLedger.load(paths.cpu)
        if not args.finalise and not paths.manifest.exists():
            body = canonical_json(store_manifest(args.store))
            replace_file(paths.manifest, (body + "\n").encode("utf-8"))
        env, config, truths = pilot_inputs(args.split)
        table, cells = load_store(args.store, env, config, truths)
        check_table(table, cells)
        check_baseline_verdict(table)
        census = build_census(table, cells, env.channels)
        by_id = {t.id: t for t in truths}

        def write_label(*, provisional: bool) -> Record:
            return finalise_records(
                paths,
                census,
                table,
                identity=identity,
                manifest_failures=_manifest_failures(paths, args.store),
                provisional=provisional,
            )

        def after_stage(name: str) -> None:
            if name == "tier1":
                write_label(provisional=True)

        with driver_lock(paths):
            if not args.finalise:
                run = Driver(
                    paths,
                    plan_stages(
                        census, by_id, env, root=SEED_ROOT, log_root=HUNT_LOG_ROOT
                    ),
                    workers=args.workers,
                    after_stage=after_stage,
                )
                print(f"run ended: {run.run()}", flush=True)
            decision = write_label(provisional=False)
    except (GuardFailure, ByteFailure) as error:
        return refuse(paths, str(error))
    except ResumeRefused as error:
        print(f"refused: {error}", flush=True)
        return EXIT_CODES["INCOMPLETE"]
    print(decision["label_line"], flush=True)
    if offers_resume(decision["end"]):
        line = resume_line(paths.cpu, pilot.ROOT, args.split, args.store, args.out)
        print(line or "no resume: the CPU stop or the wall limit is reached")
    return int(decision["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
