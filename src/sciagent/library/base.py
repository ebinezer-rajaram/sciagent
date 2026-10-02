"""Shared machinery for the out-of-grammar library members (SPEC §2.1, §3).

Regime switching and the Poisson mixture are not functions of observable
history, so they stay outside the feature grammar as fitted special cases. They
are generic point-process models of the **arrival times** only; marks are not
modelled by them (v1's library members drew marks iid, independently of
arrivals). This module holds what both need:

- the :class:`LibraryModel` / :class:`FittedLibraryModel` protocols B-lib and
  scoring read;
- the counting rule shared with the GLM likelihood: an event is *counted*
  (contributes evidence about λ) iff it is endogenous and lies outside every
  excluded window, the windows taken as **closed** intervals ``[a, b]``
  exactly as ``glm/features.py`` does;
- :class:`EmpiricalMarks`, a mark sampler that resamples the training data's
  counted events' marks (used when a simulation is asked for without an
  environment mark sampler);
- the execution of an :class:`~sciagent.glm.interventions.Experiment` on an
  arrival-only model (:func:`run_arrival_experiment`), with the same observable
  semantics as :func:`~sciagent.glm.interventions.run_experiment`;
- a deterministic multi-start quasi-Newton minimiser (:func:`multistart`).

**Draw order** for an arrival-only experiment: the model first draws every
generated arrival time on ``[0, horizon]`` (its own documented order), then the
mark sampler is called once per event, forced ones included, in time order.
Arrivals of these models never depend on marks, so that order is legitimate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np
import numpy.typing as npt
from scipy import optimize

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.interventions import Censor, Experiment, atoms, to_plan
from sciagent.glm.simulate import ForcedEvent, MarkOverride, MarkSampler, RateClamp

type Bools = npt.NDArray[np.bool_]
type Window = tuple[float, float]
#: ``generate(horizon, clamps, rng) -> (times, clamped)``: an arrival model's
#: generated events on ``[0, horizon]`` under the clamps (sorted, disjoint).
type ArrivalGenerator = Callable[
    [float, tuple[RateClamp, ...], np.random.Generator], tuple[Floats, Bools]
]

#: Guard on simulation loops; more generated events than this is an error.
MAX_EVENTS: Final = 200_000


class LibraryError(SciAgentError):
    """Base for faults raised by the out-of-grammar library."""


class LibraryInputError(LibraryError):
    """Invalid parameters, datasets or simulation inputs."""


class LibraryFitError(LibraryError):
    """A library model could not be fitted to the data."""


class LibrarySimulationError(LibraryError):
    """A simulation exceeded its event budget."""


# --------------------------------------------------------------------------
# Protocols
# --------------------------------------------------------------------------


class FittedLibraryModel(Protocol):
    """A fitted out-of-grammar member, as B-lib and scoring read it.

    ``log_likelihood`` is the maximised total over the training datasets,
    ``n_params`` the free-parameter count, ``n_events`` the counted events
    (the N of BIC, as for a GLM fit), ``bic = n_params log N - 2 log L``.
    """

    @property
    def name(self) -> str: ...

    @property
    def log_likelihood(self) -> float: ...

    @property
    def n_params(self) -> int: ...

    @property
    def n_events(self) -> int: ...

    @property
    def bic(self) -> float: ...

    def log_likelihoods(self, datasets: Sequence[Dataset]) -> tuple[float, ...]: ...

    def held_out_log_likelihood(self, datasets: Sequence[Dataset]) -> float: ...

    def simulate(
        self,
        horizon: float,
        rng: np.random.Generator,
        *,
        marks: MarkSampler | None = None,
    ) -> EventLog: ...

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        marks: MarkSampler | None = None,
        label: str = "experiment",
    ) -> Dataset: ...


class LibraryModel(Protocol):
    """An out-of-grammar member: a named model the framework fits."""

    @property
    def name(self) -> str: ...

    def fit(self, datasets: Sequence[Dataset]) -> FittedLibraryModel: ...


# --------------------------------------------------------------------------
# Counting rule
# --------------------------------------------------------------------------


def counted_mask(data: Dataset) -> Bools:
    """Endogenous events outside every closed excluded window ``[a, b]``."""
    times = data.log.times
    inside = np.zeros(times.size, dtype=np.bool_)
    for a, b in data.excluded:
        inside |= (times >= a) & (times <= b)
    out: Bools = data.endogenous & ~inside
    return out


def n_counted(datasets: Sequence[Dataset]) -> int:
    """Counted events over all datasets (the N of BIC and of per-event scores)."""
    return sum(int(np.count_nonzero(counted_mask(d))) for d in datasets)


def observed_time(data: Dataset) -> float:
    """The horizon minus the excluded windows."""
    return data.log.horizon - math.fsum(b - a for a, b in data.excluded)


def check_datasets(datasets: Sequence[Dataset]) -> tuple[Dataset, ...]:
    data = tuple(datasets)
    if not data:
        raise LibraryInputError("need at least one dataset")
    if not all(isinstance(d, Dataset) for d in data):
        raise LibraryInputError("datasets must be Dataset instances")
    return data


# --------------------------------------------------------------------------
# Marks
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class EmpiricalMarks:
    """Resample the marks of observed events, iid with replacement.

    ``rows[i, c]`` is the i-th event's value on ``channels[c]``. One call draws
    one uniform u and returns row ``min(⌊u n⌋, n - 1)``, so the stream is a
    documented function of ``rng.random()``.
    """

    channels: tuple[str, ...]
    rows: Floats

    @staticmethod
    def from_datasets(datasets: Sequence[Dataset]) -> EmpiricalMarks:
        """The counted events' marks of ``datasets``, pooled in dataset order.

        Channels are those every dataset carries, sorted by name.
        """
        data = check_datasets(datasets)
        names = sorted(set.intersection(*(set(d.log.marks) for d in data)))
        blocks = []
        for d in data:
            keep = counted_mask(d)
            blocks.append(
                np.stack([d.log.marks[c][keep] for c in names], axis=1)
                if names
                else np.empty((int(np.count_nonzero(keep)), 0))
            )
        rows = np.concatenate(blocks, axis=0)
        if names and rows.shape[0] == 0:
            raise LibraryInputError("no counted events to resample marks from")
        rows.setflags(write=False)
        return EmpiricalMarks(tuple(names), rows)

    def __call__(self, rng: np.random.Generator, /) -> Mapping[str, float]:
        u = rng.random()
        if not self.channels:
            return {}
        n = self.rows.shape[0]
        i = min(int(u * n), n - 1)
        return {c: float(self.rows[i, k]) for k, c in enumerate(self.channels)}


def no_marks(rng: np.random.Generator, /) -> Mapping[str, float]:
    """A sampler for logs with no mark channels; draws nothing."""
    return {}


# --------------------------------------------------------------------------
# Experiments on arrival-only models
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Schedule:
    """An experiment, decomposed: forced events, clamps, mark overrides, censors."""

    horizon: float
    forced: tuple[ForcedEvent, ...]
    clamps: tuple[RateClamp, ...]
    overrides: tuple[MarkOverride, ...]
    censors: tuple[Window, ...]

    @staticmethod
    def of(experiment: Experiment) -> Schedule:
        plan = to_plan(experiment)
        censors = tuple(
            sorted(
                p.window
                for p in atoms(experiment.intervention)
                if isinstance(p, Censor)
            )
        )
        return Schedule(
            experiment.horizon, plan.forced, plan.clamps, plan.overrides, censors
        )

    @staticmethod
    def empty(horizon: float) -> Schedule:
        return Schedule(horizon, (), (), (), ())

    def excluded(self) -> tuple[Window, ...]:
        """Censor and clamp windows, sorted and merged (as ``run_experiment``)."""
        merged: list[Window] = []
        windows = sorted([*self.censors, *((c.start, c.end) for c in self.clamps)])
        for start, end in windows:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return tuple(merged)


def event_marks(
    sampler: MarkSampler,
    rng: np.random.Generator,
    time: float,
    schedule: Schedule,
    given: Mapping[str, float] | None,
) -> dict[str, float]:
    """One event's marks: the sampler's draw, then forced values or overrides.

    ``given`` is None for a generated event (overrides in force at ``time``
    apply, windows half-open) and the forced event's marks otherwise.
    """
    marks = dict(sampler(rng))
    if given is not None:
        marks.update(given)
    else:
        for o in schedule.overrides:
            if o.start <= time < o.end:
                marks[o.channel] = o.value
    return marks


def finish(
    schedule: Schedule,
    times: Sequence[float],
    marks: Sequence[Mapping[str, float]],
    forced: Sequence[bool],
    clamped: Sequence[bool],
    label: str,
) -> Dataset:
    """The observed :class:`Dataset` of a run, as ``run_experiment`` builds it.

    Generated events in a censor window ``[a, b)`` are hidden; forced events
    stay visible; ``endogenous`` is neither forced nor clamped; ``excluded`` is
    :meth:`Schedule.excluded`.
    """
    t = np.array(times, dtype=np.float64)
    f = np.array(forced, dtype=np.bool_)
    c = np.array(clamped, dtype=np.bool_)
    hidden = np.zeros(t.size, dtype=np.bool_)
    for a, b in schedule.censors:
        hidden |= (t >= a) & (t < b)
    seen = ~hidden | f
    names = sorted(set().union(*(set(m) for m in marks))) if marks else []
    columns = {n: np.array([m[n] for m in marks], dtype=np.float64) for n in names}
    log = EventLog.create(
        t[seen], {n: v[seen] for n, v in columns.items()}, schedule.horizon
    )
    return Dataset.create(log, (~f & ~c)[seen], schedule.excluded(), label)


def run_arrival_experiment(
    generate: ArrivalGenerator,
    schedule: Schedule,
    rng: np.random.Generator,
    sampler: MarkSampler,
    label: str,
) -> Dataset:
    """Run an arrival-only model under ``schedule`` (draw order: module docstring).

    Forced events do not change an arrival-only model's dynamics, so generated
    arrivals are drawn first and forced events merged in. A forced time equal
    to a generated one is refused by :class:`EventLog` (probability zero).
    """
    gen_times, gen_clamped = generate(schedule.horizon, schedule.clamps, rng)
    rows: list[tuple[float, int, bool, Mapping[str, float] | None]] = [
        (float(t), 1, bool(c), None)
        for t, c in zip(gen_times.tolist(), gen_clamped.tolist(), strict=True)
    ]
    rows.extend((e.time, 0, False, dict(e.marks)) for e in schedule.forced)
    rows.sort(key=lambda r: (r[0], r[1]))
    marks = [event_marks(sampler, rng, t, schedule, given) for t, _, _, given in rows]
    return finish(
        schedule,
        [r[0] for r in rows],
        marks,
        [r[1] == 0 for r in rows],
        [r[2] for r in rows],
        label,
    )


def clamp_at(clamps: Sequence[RateClamp], t: float) -> RateClamp | None:
    """The clamp whose half-open window holds ``t``, if any."""
    for c in clamps:
        if c.start <= t < c.end:
            return c
    return None


def exponential(rng: np.random.Generator, rate: float) -> float:
    """An Exp(rate) draw by inverse CDF from one uniform; inf when rate is 0."""
    u = rng.random()
    if rate <= 0.0:
        return math.inf
    return -math.log1p(-u) / rate


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MultistartResult:
    """The best local optimum over a fixed start grid."""

    x: tuple[float, ...]
    value: float
    start_index: int
    n_starts: int
    n_converged: int


def multistart(
    objective: Callable[[Floats], float],
    starts: Sequence[Sequence[float]],
    bounds: Sequence[tuple[float, float]],
    *,
    max_iter: int = 500,
) -> MultistartResult:
    """Minimise ``objective`` by L-BFGS-B (finite-difference gradient) from each start.

    Deterministic: the starts are fixed, scipy's L-BFGS-B is deterministic
    given the same function values, and the best start is the lowest value
    with ties to the lowest index. Non-finite objective values are returned to
    the optimiser as a large finite penalty.
    """
    best: tuple[float, int, Floats] | None = None
    converged = 0

    def safe(x: Floats) -> float:
        v = objective(x)
        return v if math.isfinite(v) else 1e300

    for i, x0 in enumerate(starts):
        res = optimize.minimize(
            safe,
            np.array(x0, dtype=np.float64),
            method="L-BFGS-B",
            bounds=list(bounds),
            options={"maxiter": max_iter, "ftol": 1e-13, "gtol": 1e-9},
        )
        value = objective(np.asarray(res.x, dtype=np.float64))
        if res.success:
            converged += 1
        if math.isfinite(value) and (best is None or value < best[0]):
            best = (value, i, np.asarray(res.x, dtype=np.float64))
    if best is None:
        raise LibraryFitError("no start reached a finite likelihood")
    return MultistartResult(
        tuple(float(v) for v in best[2]), best[0], best[1], len(starts), converged
    )
