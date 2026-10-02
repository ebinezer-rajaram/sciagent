"""SPEC §4.4: scientific behaviour, scored mechanically from the record.

Pure functions of plain data; no LLM judge, no model, no randomness. The
investigation module supplies three things:

- **predictions**: ``(Prediction, Evaluation | None)`` pairs from the
  ledger (``sciagent.harness.ledger``), None when the prediction was never
  evaluated (its experiment never ran, or the statistic failed on the data).
  ``Prediction.hypothesis`` is a label; the investigation should make it the
  canonical hash or canonical text of the structure, so that "the same
  hypothesis" means the same canonical structure (a zombie returns
  *unchanged*).
- **experiments**: :class:`ExperimentRun` per run experiment, its index and
  the agent turn at which it ran (its predictions are evaluated then).
- **beliefs**: the agent's favoured-hypothesis timeline, :class:`Belief`
  ``(turn, favoured, runner_up)`` at each turn where it changed (or was
  restated), in turn order. The state at turn t is the latest belief with
  ``turn ≤ t``. Where it comes from (an explicit tool argument, the
  hypotheses named by the latest predictions, the last fit) is the
  investigation's choice; this module only needs the timeline.

The measures:

- **Calibration** (:func:`calibration`): coverage of the evaluated intervals
  against the mean stated level, overall and per hypothesis. ``error =
  coverage - mean level`` (negative: over-confident).
- **Falsification-seeking** (:func:`falsification_seeking`): an experiment
  *could refute* the favoured hypothesis when, for some statistic, the
  favoured and the runner-up committed intervals on that experiment that are
  disjoint (closed intervals: touching endpoints overlap). It is
  *confirmation-only* when the favoured predicted on it but no such pair
  exists, and *unpredicted* when the favoured made no prediction on it.
  ``share`` = refuting / experiments run.
- **Revision** (:func:`revision`): a *falsification* is an experiment on
  which a prediction of the hypothesis favoured at that experiment's turn was
  evaluated and not covered (several falsified statistics on one experiment
  are one falsification). It is *revised* when the favoured hypothesis
  differs from it at some belief with turn in ``(t, t + k]``. It is a
  *zombie* when the hypothesis is displaced at some later belief and then
  favoured again after that.
- **Stopping** (:func:`stopping`): per run, the held-out gap of the model
  the agent favoured after each experiment (``gaps[i]`` after i experiments,
  ``gaps[-1]`` at submission). It converged at the first i from which every
  later gap is within :data:`CONVERGENCE_TOLERANCE` of the final one;
  ``burned`` = experiments used after that; ``early`` = it stopped with
  budget left and a final gap below ``-CONVERGENCE_TOLERANCE`` (worse than
  the ORACLE by more than the recovery tolerance of §7.1).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

import numpy as np

from sciagent.core import reductions
from sciagent.harness.ledger import Evaluation, Prediction
from sciagent.scoring.errors import ScoringError

#: Gap tolerance (nats/event) for convergence and "stopped early": the same
#: tolerance as recovery in the go/no-go read (``scoring/gonogo.py``).
CONVERGENCE_TOLERANCE: Final = 0.005

type PredictionRecord = tuple[Prediction, Evaluation | None]


@dataclass(frozen=True)
class Belief:
    """The agent's favoured hypothesis (and runner-up) from ``turn`` on."""

    turn: int
    favoured: str
    runner_up: str | None = None


@dataclass(frozen=True)
class ExperimentRun:
    """Experiment ``index`` ran (and its predictions were evaluated) at ``turn``."""

    index: int
    turn: int


def _mean(values: Sequence[float]) -> float:
    return reductions.mean(np.asarray(values, dtype=np.float64))


def _evaluated(record: PredictionRecord) -> Evaluation | None:
    evaluation = record[1]
    if evaluation is None or not math.isfinite(evaluation.value):
        return None
    return evaluation


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HypothesisCalibration:
    hypothesis: str
    n: int
    coverage: float
    mean_level: float


@dataclass(frozen=True)
class Calibration:
    """Coverage against stated level; None fields when nothing was evaluated."""

    n: int
    unevaluated: int
    covered: int
    coverage: float | None
    mean_level: float | None
    error: float | None
    per_hypothesis: tuple[HypothesisCalibration, ...]


def calibration(records: Sequence[PredictionRecord]) -> Calibration:
    """Interval coverage at the stated level (module docstring)."""
    done = [(p, e) for p, e0 in records if (e := _evaluated((p, e0))) is not None]
    unevaluated = len(records) - len(done)
    if not done:
        return Calibration(0, unevaluated, 0, None, None, None, ())
    covered = sum(e.covered for _, e in done)
    coverage = covered / len(done)
    level = _mean([p.level for p, _ in done])
    per: list[HypothesisCalibration] = []
    for h in sorted({p.hypothesis for p, _ in done}):
        mine = [(p, e) for p, e in done if p.hypothesis == h]
        per.append(
            HypothesisCalibration(
                h,
                len(mine),
                sum(e.covered for _, e in mine) / len(mine),
                _mean([p.level for p, _ in mine]),
            )
        )
    return Calibration(
        len(done), unevaluated, covered, coverage, level, coverage - level, tuple(per)
    )


# --------------------------------------------------------------------------
# Beliefs
# --------------------------------------------------------------------------


def _check_beliefs(beliefs: Sequence[Belief]) -> None:
    for a, b in pairwise(beliefs):
        if b.turn < a.turn:
            raise ScoringError(f"beliefs must be in turn order; {a.turn} then {b.turn}")


def favoured_at(beliefs: Sequence[Belief], turn: int) -> Belief | None:
    """The latest belief with ``belief.turn ≤ turn`` (None before the first)."""
    _check_beliefs(beliefs)
    current: Belief | None = None
    for b in beliefs:
        if b.turn > turn:
            break
        current = b
    return current


def _turns(experiments: Sequence[ExperimentRun]) -> dict[int, int]:
    turns: dict[int, int] = {}
    for run in experiments:
        if run.index in turns:
            raise ScoringError(f"experiment {run.index} listed twice")
        turns[run.index] = run.turn
    return turns


# --------------------------------------------------------------------------
# Falsification-seeking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentFalsification:
    index: int
    favoured: str | None
    runner_up: str | None
    predicted: bool
    can_refute: bool


@dataclass(frozen=True)
class FalsificationSeeking:
    experiments: int
    refuting: int
    confirmation_only: int
    unpredicted: int
    share: float | None
    per_experiment: tuple[ExperimentFalsification, ...]


def _disjoint(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[1] < b[0] or b[1] < a[0]


def falsification_seeking(
    records: Sequence[PredictionRecord],
    experiments: Sequence[ExperimentRun],
    beliefs: Sequence[Belief],
) -> FalsificationSeeking:
    """Share of experiments whose predictions could refute the favoured."""
    _turns(experiments)
    rows: list[ExperimentFalsification] = []
    for run in sorted(experiments, key=lambda r: r.index):
        belief = favoured_at(beliefs, run.turn)
        fav = belief.favoured if belief else None
        alt = belief.runner_up if belief else None
        mine = [p for p, _ in records if p.experiment == run.index]
        by_fav = [p for p in mine if p.hypothesis == fav]
        by_alt = [p for p in mine if alt is not None and p.hypothesis == alt]
        refute = any(
            f.statistic == r.statistic and _disjoint(f.interval, r.interval)
            for f in by_fav
            for r in by_alt
        )
        rows.append(ExperimentFalsification(run.index, fav, alt, bool(by_fav), refute))
    refuting = sum(r.can_refute for r in rows)
    confirming = sum(r.predicted and not r.can_refute for r in rows)
    unpredicted = sum(not r.predicted for r in rows)
    share = refuting / len(rows) if rows else None
    return FalsificationSeeking(
        len(rows), refuting, confirming, unpredicted, share, tuple(rows)
    )


# --------------------------------------------------------------------------
# Revision and zombies
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Falsification:
    experiment: int
    turn: int
    hypothesis: str
    revised: bool
    zombie: bool


@dataclass(frozen=True)
class Revision:
    k: int
    falsified: int
    revised: int
    zombies: int
    rate: float | None
    zombie_rate: float | None
    events: tuple[Falsification, ...]


def revision(
    records: Sequence[PredictionRecord],
    experiments: Sequence[ExperimentRun],
    beliefs: Sequence[Belief],
    *,
    k: int,
) -> Revision:
    """Revision within ``k`` turns of a falsification, and zombie returns."""
    if k < 1:
        raise ScoringError(f"k must be at least 1 turn, got {k}")
    turns = _turns(experiments)
    _check_beliefs(beliefs)
    seen: set[tuple[int, str]] = set()
    events: list[Falsification] = []
    for p, e0 in records:
        e = _evaluated((p, e0))
        if e is None or e.covered:
            continue
        if p.experiment not in turns:
            raise ScoringError(f"prediction on experiment {p.experiment}, never run")
        turn = turns[p.experiment]
        belief = favoured_at(beliefs, turn)
        if belief is None or belief.favoured != p.hypothesis:
            continue
        key = (p.experiment, p.hypothesis)
        if key in seen:
            continue
        seen.add(key)
        h = p.hypothesis
        later = [b for b in beliefs if b.turn > turn]
        revised = any(b.favoured != h for b in later if b.turn <= turn + k)
        displaced = next((i for i, b in enumerate(later) if b.favoured != h), None)
        zombie = displaced is not None and any(
            b.favoured == h for b in later[displaced + 1 :]
        )
        events.append(Falsification(p.experiment, turn, h, revised, zombie))
    events.sort(key=lambda f: (f.experiment, f.hypothesis))
    n = len(events)
    revised_n = sum(f.revised for f in events)
    zombies = sum(f.zombie for f in events)
    return Revision(
        k,
        n,
        revised_n,
        zombies,
        revised_n / n if n else None,
        zombies / n if n else None,
        tuple(events),
    )


# --------------------------------------------------------------------------
# Stopping
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoppingRun:
    """One investigation: experiments used of ``budget``, and the gap path.

    ``gaps[i]`` is the held-out gap of the model favoured after i experiments
    (``len(gaps) == experiments_used + 1``); ``gaps[-1]`` is at submission.
    """

    experiments_used: int
    budget: int
    gaps: tuple[float, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.experiments_used <= self.budget:
            raise ScoringError(
                f"experiments used {self.experiments_used} outside [0, {self.budget}]"
            )
        if len(self.gaps) != self.experiments_used + 1:
            raise ScoringError(
                f"need {self.experiments_used + 1} gaps (after 0..used experiments), "
                f"got {len(self.gaps)}"
            )
        if not all(math.isfinite(g) for g in self.gaps):
            raise ScoringError("gaps must be finite")


@dataclass(frozen=True)
class StoppingOutcome:
    converged_at: int
    burned: int
    early: bool
    used_share: float
    final_gap: float


@dataclass(frozen=True)
class Stopping:
    runs: tuple[StoppingOutcome, ...]
    early_share: float | None
    mean_burned: float | None
    mean_used_share: float | None


def _stopping_one(run: StoppingRun) -> StoppingOutcome:
    final = run.gaps[-1]
    converged = run.experiments_used
    for i in range(run.experiments_used, -1, -1):
        if abs(run.gaps[i] - final) > CONVERGENCE_TOLERANCE:
            break
        converged = i
    used_share = run.experiments_used / run.budget if run.budget else 1.0
    early = run.experiments_used < run.budget and final < -CONVERGENCE_TOLERANCE
    return StoppingOutcome(
        converged, run.experiments_used - converged, early, used_share, final
    )


def stopping(runs: Sequence[StoppingRun]) -> Stopping:
    """Early stops and budget burned after convergence (module docstring)."""
    out = tuple(_stopping_one(r) for r in runs)
    if not out:
        return Stopping((), None, None, None)
    return Stopping(
        out,
        sum(o.early for o in out) / len(out),
        _mean([float(o.burned) for o in out]),
        _mean([o.used_share for o in out]),
    )
