"""SPEC §7.1 pilot go/no-go read, and the §6.4 positive controls it rests on.

The input is a results table: one :class:`ScoreRow` per (truth, seed,
system), with the scores of ``scoring/heldout.py`` and
``scoring/structure.py``. System names are the SPEC §4.1 ids; the large-budget
B-sym run is its own system, :data:`B_SYM_10F`.

**Definitions** (preregistered here; LOG.md records why).

- A system **recovers** a truth on a seed (:func:`recovers`) when its
  structure is exactly the truth's (canonical hash). A gap clause
  (``gap ≥ -RECOVERY_TOLERANCE``) was preregistered first and withdrawn on
  measurement: wrong structures reach it on noise (LOG.md 2026-10-02).
  :data:`RECOVERY_TOLERANCE` is kept for reporting only.
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
  their paired standard error (needs ≥ 2 seeds with finite gaps). A truth
  that fails is not identifiable and is **dropped** by the preregistered rule
  before anything else is read; if every truth is dropped, the controls fail.
  **And** (P4-PROPOSAL §2.4) the ORACLE must carry zero ``oracle_underfit``
  flags (:func:`oracle_underfit_check`): an ORACLE fit whose training
  log-likelihood is below the true parameters' is not an upper bound, so
  nothing referenced to it can be read. A flag that was never recorded (a cell
  from before fit/5, or a failed ORACLE unit) is not "zero flags": the check
  fails and says so.
- :func:`planted_beats_rand` (Δ10, rank rule): per retained truth, the
  difference ``d_t`` of the planted-hint agent's and B-rand's per-truth mean
  gaps (a seed at ``-inf`` makes its truth's mean ``-inf``: it ranks lowest;
  both at ``-inf`` is a tie, ``d_t = 0``). It passes when **both** the
  one-sided exact Wilcoxon signed-rank test of ``d`` gives ``p <``
  :data:`CONTROL_2_ALPHA` **and** the percentile bootstrap CI of the median of
  ``d`` (``scoring/stats.py``) has its lower end above 0. The win count, the
  median and, beside them only, the mean over the finite ``d_t`` are
  reported. The mean is not the statistic: one explosive B-rand model
  carried it to 6.8 in the pilot (audit §2). The exact test has
  p >= 2**-n, so the control cannot pass on fewer than 5 truths.
- :func:`bsym_approaches_oracle`: the median
  (:func:`~sciagent.scoring.stats.order_median`, so a ``-inf`` truth ranks
  lowest rather than being dropped) over retained truths of B-sym at 10xF's
  per-truth mean gap is at least ``-BSYM_TOLERANCE`` = -0.01 nats/event: twice
  the recovery tolerance, since the median must say the space is searchable,
  not that B-sym recovers every truth, and a quarter of the S11 library gap.

**Non-finite gaps.** A row's gap may be ``-inf`` (a model that gives a
held-out event zero intensity); ``nan`` and ``+inf`` are refused. Control 1
reads finite gaps only (a ``-inf`` B-lib seed carries no SE); controls 2 and 3
read ``-inf`` by rank.

**Verdict** (SPEC §7.1, in precedence order).

1. **Controls fail** if any control fails: fix the instrument first.
2. **Too easy** if B-lib or B-rand recovers ≥ 70% of truths, or B-sparse
   closes the gap on ≥ 70% of the out-of-dictionary truths (not triggered
   when there are none; the verdict says so).
3. **Too hard** if the gap closes for < 10% of truths with *every* non-LLM
   system in the table (P4-PROPOSAL §2.4), the ORACLE excepted: it is the
   reference the gap is measured from, so it closes by definition. B-sym at
   10xF is required to be there.
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
    MedianCI,
    bootstrap_median_ci,
    order_median,
    paired_mean_se,
    signed_rank_greater,
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
#: One-sided level of control 2's signed-rank test (SPEC §6.2's 95%).
CONTROL_2_ALPHA: Final = 0.05


@dataclass(frozen=True)
class ScoreRow:
    """One system's scores on one (truth, seed).

    ``gap`` is ``LL_sub - LL_oracle`` per event (finite or ``-inf``);
    ``gap_closed`` is None when undefined; ``in_dictionary`` is the truth's
    B-sparse dictionary membership (the same on every row of a truth).
    ``oracle_underfit`` is the ORACLE row's flag (None: not recorded, or not
    an ORACLE row).
    """

    truth: str
    seed: int
    system: str
    gap: float
    gap_closed: float | None
    exact: bool
    in_dictionary: bool
    oracle_underfit: bool | None = None


class Outcome(Enum):
    CONTROLS_FAIL = "controls fail"
    TOO_EASY = "too easy"
    TOO_HARD = "too hard"
    GO = "go"


def recovers(row: ScoreRow) -> bool:
    """Exact canonical recovery (SPEC §7.1 "recovers").

    A held-out-gap tolerance was tried first (gap ≥ -RECOVERY_TOLERANCE) and
    dropped: B-rand reached it after 2-5 fits of *wrong* structures, because
    0.005 nats/event is within noise at ~2,000 held-out events, so the
    too-easy branch would fire on noise. The gap is reported separately.
    """
    return row.exact


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
        if math.isnan(r.gap) or r.gap == math.inf:
            raise ScoringError(f"gap {r.gap} in row {key}: only -inf is allowed")
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
    """Per truth: mean ``gap_oracle - gap_lib`` over seeds above its paired SE.

    Finite gaps only (module docstring).
    """
    index = _index([r for r in rows if math.isfinite(r.gap)])
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
class UnderfitCheck:
    """ORACLE ``oracle_underfit`` flags: (truth, seed) flagged and unrecorded."""

    flagged: tuple[tuple[str, int], ...]
    unrecorded: tuple[tuple[str, int], ...]
    checked: int

    @property
    def passed(self) -> bool:
        return not self.flagged and not self.unrecorded and self.checked > 0


def oracle_underfit_check(
    flags: Sequence[tuple[str, int, bool | None]],
) -> UnderfitCheck:
    """Control 1's second half: zero flags over ORACLE (truth, seed, flag)."""
    ordered = sorted(flags, key=lambda f: (f[0], f[1]))
    return UnderfitCheck(
        flagged=tuple((t, s) for t, s, f in ordered if f is True),
        unrecorded=tuple((t, s) for t, s, f in ordered if f is None),
        checked=len(ordered),
    )


def underfit_reason(check: UnderfitCheck) -> list[str]:
    """Why control 1's underfit half fails (empty when it passes)."""
    out: list[str] = []
    if check.flagged:
        units = [f"{t} s{s}" for t, s in check.flagged]
        out.append(
            f"ORACLE oracle_underfit flagged on {len(check.flagged)} unit(s): {units}"
        )
    if check.unrecorded:
        out.append(
            f"ORACLE oracle_underfit not recorded on {len(check.unrecorded)} of "
            f"{check.checked} unit(s) (cells from before fit/5, or failed ORACLE "
            f"units): zero flags cannot be shown"
        )
    if check.checked == 0:
        out.append("no ORACLE unit to check for oracle_underfit")
    return out


@dataclass(frozen=True)
class PlantedCheck:
    """Control 2 by rank (module docstring); ``differences`` per truth."""

    truths: tuple[str, ...]
    differences: tuple[float, ...]
    wins: int
    ties: int
    losses: int
    p_value: float
    median_ci: MedianCI
    mean_finite: float | None
    n_infinite: int
    passed: bool

    @property
    def median(self) -> float:
        return self.median_ci.median

    @property
    def n(self) -> int:
        return len(self.truths)


def _difference(planted: float, rand: float) -> float:
    if planted == rand:  # includes both at -inf: a tie, not nan
        return 0.0
    return planted - rand


def planted_beats_rand(
    rows: Sequence[ScoreRow],
    truths: Sequence[str],
    *,
    rng: np.random.Generator,
    resamples: int = DEFAULT_RESAMPLES,
) -> PlantedCheck:
    """Planted-hint minus B-rand per-truth mean gaps, read by rank (docstring)."""
    if not truths:
        raise ScoringError("no truths to read control 2 on")
    index = _index(rows)
    d = [
        _difference(_mean_gap(index, PLANTED, t), _mean_gap(index, B_RAND, t))
        for t in truths
    ]
    p = signed_rank_greater(d)
    ci = bootstrap_median_ci(d, rng, resamples=resamples)
    finite = [x for x in d if math.isfinite(x)]
    return PlantedCheck(
        truths=tuple(truths),
        differences=tuple(d),
        wins=sum(x > 0.0 for x in d),
        ties=sum(x == 0.0 for x in d),
        losses=sum(x < 0.0 for x in d),
        p_value=p,
        median_ci=ci,
        mean_finite=_mean(finite) if finite else None,
        n_infinite=len(d) - len(finite),
        passed=p < CONTROL_2_ALPHA and ci.low > 0.0,
    )


def planted_reason(check: PlantedCheck) -> str:
    """Why control 2 fails, in the rank rule's own terms."""
    ci = check.median_ci
    return (
        f"planted-hint does not beat B-rand by the rank rule: Wilcoxon one-sided "
        f"p = {check.p_value:.4g} (needs < {CONTROL_2_ALPHA}), median difference "
        f"{ci.median:.4g} with CI [{ci.low:.4g}, {ci.high:.4g}] (lower end must "
        f"be > 0), wins {check.wins}/{check.n}"
    )


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
    median = order_median([_mean_gap(index, B_SYM_10F, t) for t in truths])
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
    underfit: UnderfitCheck | None = None


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
    underfit = oracle_underfit_check(
        [(t, n, r.oracle_underfit) for (t, n, s), r in index.items() if s == ORACLE]
    )
    dropped = tuple(c.truth for c in checks if not c.passed)
    kept = tuple(c.truth for c in checks if c.passed)
    others = sorted({s for _, _, s in index if s != ORACLE})
    reasons: list[str] = []
    if dropped:
        reasons.append(f"dropped as not identifiable (ORACLE ≤ B-lib): {list(dropped)}")
    reasons += underfit_reason(underfit)
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
            underfit,
        )

    planted = planted_beats_rand(rows, kept, rng=rng, resamples=resamples)
    bsym = bsym_approaches_oracle(rows, kept)
    controls = planted.passed and bsym.passed and underfit.passed
    if not planted.passed:
        reasons.append(planted_reason(planted))
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
        underfit,
    )
