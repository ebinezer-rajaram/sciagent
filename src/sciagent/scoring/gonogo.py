"""SPEC §7.1 pilot go/no-go read, and the §6.4 positive controls it rests on.

The input is a results table: one :class:`ScoreRow` per (truth, seed,
system), with the scores of ``scoring/heldout.py`` and
``scoring/structure.py``. System names are the SPEC §4.1 ids; the large-budget
B-sym run is its own system, :data:`B_SYM_10F`.

**Definitions** (preregistered here; LOG.md records why).

- A system **recovers** a truth on a seed (:func:`recovers`) when its
  structure is exactly the truth's (canonical hash) *or* its held-out gap is
  within :data:`RECOVERY_TOLERANCE` = 0.005 nats/event of the ORACLE's
  (``gap ≥ -0.005``). The second clause credits a predictively equivalent
  answer, which is what "recovers" must mean for out-of-grammar library
  members and interventionally equivalent structures. 0.005 is above the
  ORACLE's own finite-sample deficit: a correctly specified fit with d
  parameters loses about ``d / (2n)`` nats per event held out against the
  truth (the AIC argument), ≤ 0.0025 for d ≤ 10 at n = 2,000, so twice that
  separates "as good as the true structure" from noise. It is also an order
  of magnitude below the one library-vs-ORACLE gap measured so far (S11:
  0.040-0.046, LOG 2026-10-02).
- A system **closes the gap** (:func:`closes`) when its gap closed,
  ``(LL_sub - LL_lib) / (LL_oracle - LL_lib)``, is at least
  :data:`CLOSE_FRACTION` = 0.9. A relative criterion, so a truth far from the
  library is not "closed" by a fixed absolute margin it can never meet; 0.9
  leaves room for the finite-sample noise of both endpoints. Undefined gap
  closed (ORACLE not beating B-lib) never closes.
- **Shares** are over truths: a truth's value is the fraction of its seeds on
  which the property holds, and a share is the mean of that over truths (so
  every truth weighs the same, whatever its number of seeds).

**Positive controls** (SPEC §6.4).

- :func:`oracle_beats_lib`: per truth, the paired differences
  ``gap_oracle - gap_lib`` over seeds; it passes when their mean exceeds
  their paired standard error (needs ≥ 2 seeds). A truth that fails is not
  identifiable and is **dropped** by the preregistered rule before anything
  else is read; if every truth is dropped, the controls fail.
- :func:`planted_beats_rand`: the paired bootstrap CI (``scoring/stats.py``)
  over retained truths of the per-truth mean gap of the planted-hint agent
  minus B-rand's; passes when the CI's lower end is above 0.
- :func:`bsym_approaches_oracle`: the median over retained truths of B-sym at
  10xF's per-truth mean gap is at least ``-BSYM_TOLERANCE`` = -0.01
  nats/event: twice the recovery tolerance, since the median must say the
  space is searchable, not that B-sym recovers every truth, and a quarter of
  the S11 library gap.

**Verdict** (SPEC §7.1, in precedence order).

1. **Controls fail** if any control fails: fix the instrument first.
2. **Too easy** if B-lib or B-rand recovers ≥ 70% of truths, or B-sparse
   closes the gap on ≥ 70% of the out-of-dictionary truths (not triggered
   when there are none; the verdict says so).
3. **Too hard** if the gap closes for < 10% of truths with *every* system
   other than the ORACLE in the table (B-sym at 10xF is required to be there).
4. **Go** otherwise, regardless of who wins.

Too easy and too hard are both computed and reported even when a
higher-precedence outcome applies.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

import numpy as np

from sciagent.core import reductions
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.stats import (
    DEFAULT_RESAMPLES,
    BootstrapCI,
    paired_bootstrap_ci,
    paired_mean_se,
)

ORACLE: Final = "ORACLE"
B_LIB: Final = "B-lib"
B_RAND: Final = "B-rand"
B_SPARSE: Final = "B-sparse"
B_SYM_10F: Final = "B-sym@10F"
PLANTED: Final = "planted-hint"
#: Systems the go/no-go read needs on every (truth, seed).
REQUIRED: Final = (ORACLE, B_LIB, B_RAND, B_SPARSE, B_SYM_10F, PLANTED)

RECOVERY_TOLERANCE: Final = 0.005
CLOSE_FRACTION: Final = 0.9
BSYM_TOLERANCE: Final = 0.01
TOO_EASY_SHARE: Final = 0.7
TOO_HARD_SHARE: Final = 0.1


@dataclass(frozen=True)
class ScoreRow:
    """One system's scores on one (truth, seed).

    ``gap`` is ``LL_sub - LL_oracle`` per event; ``gap_closed`` is None when
    undefined; ``in_dictionary`` is the truth's B-sparse dictionary membership
    (the same on every row of a truth).
    """

    truth: str
    seed: int
    system: str
    gap: float
    gap_closed: float | None
    exact: bool
    in_dictionary: bool


class Outcome(Enum):
    CONTROLS_FAIL = "controls fail"
    TOO_EASY = "too easy"
    TOO_HARD = "too hard"
    GO = "go"


def recovers(row: ScoreRow) -> bool:
    """Exact canonical recovery, or a gap within the recovery tolerance."""
    return row.exact or row.gap >= -RECOVERY_TOLERANCE


def closes(row: ScoreRow) -> bool:
    """Gap closed of at least :data:`CLOSE_FRACTION` (False when undefined)."""
    return row.gap_closed is not None and row.gap_closed >= CLOSE_FRACTION


# --------------------------------------------------------------------------
# Table access
# --------------------------------------------------------------------------


def _index(rows: Sequence[ScoreRow]) -> dict[tuple[str, int, str], ScoreRow]:
    out: dict[tuple[str, int, str], ScoreRow] = {}
    membership: dict[str, bool] = {}
    for r in rows:
        key = (r.truth, r.seed, r.system)
        if key in out:
            raise ScoringError(f"duplicate row for {key}")
        if not math.isfinite(r.gap):
            raise ScoringError(f"non-finite gap in row {key}")
        if r.gap_closed is not None and not math.isfinite(r.gap_closed):
            raise ScoringError(f"non-finite gap closed in row {key}")
        if membership.setdefault(r.truth, r.in_dictionary) != r.in_dictionary:
            raise ScoringError(f"truth {r.truth!r} has inconsistent in_dictionary")
        out[key] = r
    return out


def _system_rows(
    index: Mapping[tuple[str, int, str], ScoreRow], system: str, truth: str
) -> list[ScoreRow]:
    return sorted(
        (r for (t, _, s), r in index.items() if t == truth and s == system),
        key=lambda r: r.seed,
    )


def _truths(rows: Sequence[ScoreRow]) -> tuple[str, ...]:
    return tuple(sorted({r.truth for r in rows}))


def _mean(values: Sequence[float]) -> float:
    return reductions.mean(np.asarray(values, dtype=np.float64))


def _mean_gap(index: Mapping[tuple[str, int, str], ScoreRow], s: str, t: str) -> float:
    mine = _system_rows(index, s, t)
    if not mine:
        raise ScoringError(f"no {s} rows for truth {t!r}")
    return _mean([r.gap for r in mine])


# --------------------------------------------------------------------------
# Positive controls
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OracleCheck:
    truth: str
    margin: float
    se: float
    passed: bool


def oracle_beats_lib(rows: Sequence[ScoreRow]) -> tuple[OracleCheck, ...]:
    """Per truth: mean ``gap_oracle - gap_lib`` over seeds above its paired SE."""
    index = _index(rows)
    checks: list[OracleCheck] = []
    for t in _truths(rows):
        oracle = {r.seed: r.gap for r in _system_rows(index, ORACLE, t)}
        lib = {r.seed: r.gap for r in _system_rows(index, B_LIB, t)}
        seeds = sorted(set(oracle) & set(lib))
        if len(seeds) < 2:
            raise ScoringError(
                f"truth {t!r}: ORACLE vs B-lib needs ≥ 2 paired seeds, has {len(seeds)}"
            )
        margin, se = paired_mean_se([oracle[s] for s in seeds], [lib[s] for s in seeds])
        checks.append(OracleCheck(t, margin, se, margin > se))
    return tuple(checks)


@dataclass(frozen=True)
class PlantedCheck:
    ci: BootstrapCI
    passed: bool


def planted_beats_rand(
    rows: Sequence[ScoreRow],
    truths: Sequence[str],
    *,
    rng: np.random.Generator,
    resamples: int = DEFAULT_RESAMPLES,
) -> PlantedCheck:
    """Paired bootstrap of planted-hint minus B-rand per-truth mean gaps."""
    index = _index(rows)
    planted = [_mean_gap(index, PLANTED, t) for t in truths]
    rand = [_mean_gap(index, B_RAND, t) for t in truths]
    ci = paired_bootstrap_ci(planted, rand, rng, resamples=resamples)
    return PlantedCheck(ci, ci.low > 0.0)


@dataclass(frozen=True)
class BsymCheck:
    median_gap: float
    tolerance: float
    passed: bool


def bsym_approaches_oracle(
    rows: Sequence[ScoreRow], truths: Sequence[str]
) -> BsymCheck:
    """Median per-truth mean gap of B-sym at 10xF is ≥ ``-BSYM_TOLERANCE``."""
    if not truths:
        raise ScoringError("no truths to read B-sym at 10xF on")
    index = _index(rows)
    gaps = np.array([_mean_gap(index, B_SYM_10F, t) for t in truths], dtype=np.float64)
    median = float(np.median(gaps))
    return BsymCheck(median, BSYM_TOLERANCE, median >= -BSYM_TOLERANCE)


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """The §7.1 read: outcome, the flags behind it, and every share it used."""

    outcome: Outcome
    controls_pass: bool
    too_easy: bool
    too_hard: bool
    truths: int
    dropped: tuple[str, ...]
    oracle_checks: tuple[OracleCheck, ...]
    planted: PlantedCheck | None
    bsym: BsymCheck | None
    recovery_share: Mapping[str, float]
    close_share: Mapping[str, float]
    sparse_out_of_dictionary_close_share: float | None
    reasons: tuple[str, ...]


def _truth_share(
    index: Mapping[tuple[str, int, str], ScoreRow],
    system: str,
    truths: Sequence[str],
    *,
    by_recovery: bool,
) -> float:
    per_truth: list[float] = []
    for t in truths:
        mine = _system_rows(index, system, t)
        if not mine:
            raise ScoringError(f"no {system} rows for truth {t!r}")
        hits = [recovers(r) if by_recovery else closes(r) for r in mine]
        per_truth.append(sum(hits) / len(hits))
    return _mean(per_truth)


def _check_complete(index: Mapping[tuple[str, int, str], ScoreRow]) -> None:
    systems = {s for _, _, s in index}
    missing = [s for s in REQUIRED if s not in systems]
    if missing:
        raise ScoringError(f"the go/no-go read needs systems {missing}")
    pairs = {(t, seed) for t, seed, s in index if s == ORACLE}
    for system in REQUIRED:
        mine = {(t, seed) for t, seed, s in index if s == system}
        if mine != pairs:
            raise ScoringError(
                f"{system} rows do not cover the same (truth, seed) pairs as ORACLE"
            )


def go_no_go(
    rows: Sequence[ScoreRow],
    *,
    rng: np.random.Generator,
    resamples: int = DEFAULT_RESAMPLES,
) -> Verdict:
    """The SPEC §7.1 read of a pilot results table (module docstring)."""
    index = _index(rows)
    _check_complete(index)
    checks = oracle_beats_lib(rows)
    dropped = tuple(c.truth for c in checks if not c.passed)
    kept = tuple(c.truth for c in checks if c.passed)
    others = sorted({s for _, _, s in index if s != ORACLE})
    reasons: list[str] = []
    if dropped:
        reasons.append(f"dropped as not identifiable (ORACLE ≤ B-lib): {list(dropped)}")
    if not kept:
        reasons.append("every truth was dropped: ORACLE never beats B-lib")
        return Verdict(
            Outcome.CONTROLS_FAIL,
            False,
            False,
            False,
            0,
            dropped,
            checks,
            None,
            None,
            {},
            {},
            None,
            tuple(reasons),
        )

    planted = planted_beats_rand(rows, kept, rng=rng, resamples=resamples)
    bsym = bsym_approaches_oracle(rows, kept)
    controls = planted.passed and bsym.passed
    if not planted.passed:
        reasons.append(
            f"planted-hint does not beat B-rand: CI [{planted.ci.low:.4g}, "
            f"{planted.ci.high:.4g}]"
        )
    if not bsym.passed:
        reasons.append(
            f"B-sym at 10xF median gap {bsym.median_gap:.4g} < -{BSYM_TOLERANCE}"
        )

    recovery = {s: _truth_share(index, s, kept, by_recovery=True) for s in others}
    close = {s: _truth_share(index, s, kept, by_recovery=False) for s in others}
    out_of_dict = [
        t for t in kept if not _system_rows(index, ORACLE, t)[0].in_dictionary
    ]
    sparse_ood = (
        _truth_share(index, B_SPARSE, out_of_dict, by_recovery=False)
        if out_of_dict
        else None
    )
    if sparse_ood is None:
        reasons.append("no out-of-dictionary truths: the B-sparse clause cannot fire")

    too_easy = False
    for s in (B_LIB, B_RAND):
        if recovery[s] >= TOO_EASY_SHARE:
            too_easy = True
            reasons.append(f"{s} recovers {recovery[s]:.0%} of truths")
    if sparse_ood is not None and sparse_ood >= TOO_EASY_SHARE:
        too_easy = True
        reasons.append(
            f"B-sparse closes the gap on {sparse_ood:.0%} of out-of-dictionary truths"
        )
    too_hard = all(close[s] < TOO_HARD_SHARE for s in others)
    if too_hard:
        reasons.append(f"no system closes the gap on ≥ {TOO_HARD_SHARE:.0%} of truths")

    if not controls:
        outcome = Outcome.CONTROLS_FAIL
    elif too_easy:
        outcome = Outcome.TOO_EASY
    elif too_hard:
        outcome = Outcome.TOO_HARD
    else:
        outcome = Outcome.GO
    return Verdict(
        outcome,
        controls,
        too_easy,
        too_hard,
        len(kept),
        dropped,
        checks,
        planted,
        bsym,
        recovery,
        close,
        sparse_ood,
        tuple(reasons),
    )
