"""What SPEC §4.4's operations mean in the point-process slice.

``sciagent.experiments.dsl`` says an operation names a component and a value.
This module says what doing it amounts to here: that ``arrival``'s values are
absolute times and must ascend, that ablating an arrival process is incoherent
while ablating a sign is not, and that forcing an arrival is only informative
about what happens *after* it. None of that is domain-independent, which is why
the compiler is injected rather than imported.

Forcing an arrival, and what it is read over
--------------------------------------------

SPEC §4.2 makes the forced arrival the only thing separating Hawkes
self-excitation from latent regime switching. Realising it needs two decisions
the framework cannot make:

**A burst at the head of the run.** An arrival component's values are absolute
times and must ascend, so a clamp that sets event ``i`` to a time earlier than
event ``i-1`` produces a log no process could have generated. Whether it does is
not knowable at compile time -- it depends on the natural times, which have not
been drawn yet. Forcing a *prefix* of the events is the case where it is knowable:
indices ``0..k-1`` have no earlier event to contradict, so a strictly ascending
schedule over a prefix is monotone by construction. Mid-run forcing is refused
rather than checked-and-hoped-for.

**Measurement after the burst.** The response to a forced arrival is elevated
intensity in the interval that follows it, and a statistic pooled over the whole
run dilutes that to nothing: under a 512-event run a burst of five raises the
whole-run dispersion by far less than its replicate spread. So a ``ForceArrival``
is read over the events after the burst, with times rebased to the burst's end --
``mean_rate`` divides by elapsed time, and without the rebase it would divide by
absolute time and report the run's average instead of the response. The
restriction is part of what forcing means here; it is not a second operation.

"After the burst" is a statement about *time* and about what was *recorded*: the
first ``observe`` events the observer has, whose time exceeds the last forced
one. On an uncensored run that is exactly the events at indices ``k, k+1, ...``,
since the burst is a prefix and arrival times ascend. Under a censoring
observation process it is not, and the time-based reading is the one that stays
true to what the operation means -- an investigator reads the events that reach
them, and a window whose events were never recorded teaches less, which is a
property of the world and not an error in the design.

Censoring, and why it lives here
--------------------------------

Scenario S12's nuisance is an observation process that records events only during
part of each cycle -- the family
:data:`~environments.pointproc.components.IDENTITY_PERIODIC_CENSORED`.
An event that is censored still *happened*: the arrival was drawn, the mark was
drawn, the programme is untouched. What is lost is the record. The only place in
this architecture where a log becomes a record is the restriction a
:class:`~sciagent.experiments.executor.CompiledOperation` carries, so the family
declares the observation process and this module applies it, to every operation
alike -- an investigator does not get to switch the censoring off by choosing a
different experiment.

It is composed *before* the operation's own restriction, so an operation reads
the record and not the run. That is what makes "the twenty events after the
burst" mean the twenty an investigator has -- see below on why the window is
defined by time and count rather than by index, which is what lets the two
restrictions compose in this order at all.

Every diagnostic used is from SPEC §4.3's frozen catalogue. Nothing here adds one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from itertools import pairwise

import numpy as np
import numpy.typing as npt

from environments.pointproc.catalogue import CANDIDATE_PERIOD
from environments.pointproc.components import (
    ARRIVAL,
    IDENTITY_PERIODIC_CENSORED,
    SIZE,
)
from sciagent.core.errors import MalformedDesignError, UnknownOperationError
from sciagent.core.program import Component, GenerativeProgram
from sciagent.core.types import (
    ComponentId,
    EventLog,
    Floats,
    FrozenDict,
    MetricName,
    Parameters,
)
from sciagent.experiments.dsl import (
    AblateComponent,
    CompareCandidates,
    ConditionOn,
    ForceArrival,
    Operation,
    PerturbParameter,
    QueryDiagnostic,
    render,
    targets,
)
from sciagent.experiments.executor import CompiledOperation, OperationCompiler

__all__ = [
    "COVARIATES",
    "OPERATIONS_VERSION",
    "arrival_burst",
    "compiler",
    "observed",
]

#: Version of what an operation *means* here, carried into ``ENV_VERSION``.
#:
#: The compiler decides what a design measures every bit as much as the metric
#: registry does: the same ``ForceArrival`` read over a different window is a
#: different experiment. Neither the grammar version nor the family library's
#: covers that, and neither does
#: :attr:`~sciagent.inference.empirical.EmpiricalTable.version`, which addresses
#: templates and replicates. Without a version here, changing a restriction would
#: leave every cached row and every registered result silently describing an
#: experiment nobody would perform again.
#:
#: 1.1.0 at backlog item 11: the censoring observation process was added, and a
#: forced arrival's window became the first ``observe`` *recorded* events after
#: the burst rather than the next ``observe`` indices of the run.
OPERATIONS_VERSION = "1.1.0"

#: Components whose value is an absolute time rather than a quantity. Clamping
#: one is only coherent over a prefix of the run (see the module docstring), and
#: holding one fixed for the whole run is not coherent at all: every event would
#: occur at the same instant and no diagnostic of an arrival stream would be
#: defined.
_TIME_VALUED: frozenset[ComponentId] = frozenset({ARRIVAL})


# --------------------------------------------------------------------------
# Covariates for ConditionOn
# --------------------------------------------------------------------------


def _phase(log: EventLog) -> Floats:
    """Return each event's phase in ``[0, 1)`` against the candidate period.

    The period is the one a seasonality hypothesis proposes, read off the
    spectral peak, not privileged knowledge of the ground truth -- the same
    reading ``catalogue.CANDIDATE_PERIOD`` already documents for
    ``phase_conditioned_dispersion``.
    """
    times = log.values[ARRIVAL]
    return np.asarray(np.mod(times, CANDIDATE_PERIOD) / CANDIDATE_PERIOD)


def _mark_size(log: EventLog) -> Floats:
    return log.values[SIZE]


#: One value per event, in event order.
type Covariate = Callable[[EventLog], Floats]

#: What :attr:`~sciagent.experiments.executor.CompiledOperation.restrict` holds:
#: the map from an executed log to the part of it a measurement may read.
type Restriction = Callable[[EventLog], EventLog]

#: Per-event covariates a ``ConditionOn`` may select on. Deliberately small: a
#: covariate is a claim that the quantity is observable, and an investigator
#: conditioning on something unobservable would be reading the answer off the
#: ground truth. The latent regime of SPEC §4.2's regime-switching mechanism is
#: absent for exactly that reason -- conditioning on the *inferred* state is a
#: hypothesis-dependent analysis, not an observable.
COVARIATES: Mapping[MetricName, Covariate] = {
    MetricName("phase"): _phase,
    MetricName("size"): _mark_size,
}


def _covariate(name: MetricName) -> Covariate:
    try:
        return COVARIATES[name]
    except KeyError as exc:
        raise UnknownOperationError(
            f"covariate {name!r} is not observable in this environment; known "
            f"covariates are {sorted(COVARIATES)!r}"
        ) from exc


# --------------------------------------------------------------------------
# Log restrictions
# --------------------------------------------------------------------------


def _select(
    log: EventLog, keep: npt.NDArray[np.bool_], *, rebase: float = 0.0
) -> EventLog:
    """Return the sub-log of the events ``keep`` selects.

    ``rebase`` is subtracted from the time-valued components, so that a metric
    dividing by elapsed time divides by the elapsed time of the *selection* and
    not of the whole run.
    """
    indices = np.flatnonzero(keep)
    if indices.size < 2:
        raise MalformedDesignError(
            f"the restriction keeps {indices.size} event(s); no diagnostic in "
            f"the catalogue is defined on fewer than two"
        )
    values = {
        component_id: np.array(
            series[indices] - (rebase if component_id in _TIME_VALUED else 0.0),
            dtype=np.float64,
        )
        for component_id, series in log.values.items()
    }
    latents = {
        key: np.array(series[indices], dtype=np.float64)
        for key, series in log.latents.items()
    }
    return EventLog(
        n_events=int(indices.size),
        values=FrozenDict(values),
        latents=FrozenDict(latents),
    )


# --------------------------------------------------------------------------
# The observation process
# --------------------------------------------------------------------------


def observed(program: GenerativeProgram) -> Restriction | None:
    """Return the restriction this programme's observation process implies.

    ``None`` for the reference observation, which records every event -- so the
    whole censoring path costs an unedited programme one dictionary lookup and
    changes nothing about it.

    Under :data:`~environments.pointproc.components.IDENTITY_PERIODIC_CENSORED`
    an event is recorded when its arrival time falls in the first ``duty`` of a
    ``period``-long cycle. The window therefore *opens at the origin*, which is
    deliberate: the burst of a ``ForceArrival`` sits at the head of the run, and
    a censoring window that closed over it would make the one experiment that
    can recover S12's truth unreadable. A nuisance that also destroyed the
    recovery route would be a trap rather than a garden path.
    """
    censored = [
        component_id
        for component_id in sorted(program.components)
        if program.components[component_id].family == IDENTITY_PERIODIC_CENSORED
    ]
    if not censored:
        return None
    parameters = program.components[censored[0]].parameters
    period = parameters["period"]
    duty = parameters["duty"]
    if period <= 0.0 or not 0.0 < duty <= 1.0:
        raise MalformedDesignError(
            f"censoring window declares period={period!r} duty={duty!r}; a "
            f"positive period and a duty in (0, 1] are what make it a window"
        )
    open_for = period * duty

    def restrict(log: EventLog) -> EventLog:
        return _select(log, np.mod(log.values[ARRIVAL], period) < open_for)

    return restrict


def _compose(first: Restriction | None, then: Restriction | None) -> Restriction | None:
    """Return the restriction that applies ``first`` and then ``then``."""
    if first is None:
        return then
    if then is None:
        return first

    def restrict(log: EventLog) -> EventLog:
        return then(first(log))

    return restrict


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------


def arrival_burst(count: int, spacing: float) -> FrozenDict[int, float]:
    """Return a schedule forcing ``count`` arrivals at the head of the run.

    Times are ``spacing, 2*spacing, ...``, so the schedule is strictly ascending
    and starts after time zero. Guarantees a monotone arrival stream whatever the
    process does afterwards, which is the property that makes a prefix the only
    clamp on a time-valued component this environment accepts.

    A tight ``spacing`` is the informative choice: the excitation an arrival
    contributes decays, so ``count`` events crowded into a short interval raise
    the intensity at the burst's end far more than the same count spread out.
    """
    if count < 1:
        raise MalformedDesignError(f"a burst needs at least one arrival, got {count}")
    if not spacing > 0.0:
        raise MalformedDesignError(f"burst spacing must be positive, got {spacing}")
    return FrozenDict[int, float](
        {index: spacing * (index + 1) for index in range(count)}
    )


# --------------------------------------------------------------------------
# The compiler
# --------------------------------------------------------------------------


def _with_parameters(
    program: GenerativeProgram, component_id: ComponentId, parameters: Parameters
) -> GenerativeProgram:
    """Return ``program`` with one component's parameters replaced."""
    component = program.components[component_id]
    replaced = Component(
        id=component.id,
        kind=component.kind,
        family=component.family,
        parameters=parameters,
        latents=component.latents,
    )
    return GenerativeProgram(
        components=FrozenDict[ComponentId, Component](
            {**program.components, component_id: replaced}
        ),
        edges=program.edges,
        library=program.library,
        history_edges=program.history_edges,
    )


def _require_component(
    program: GenerativeProgram, component_id: ComponentId, operation: Operation
) -> None:
    if component_id not in program.components:
        raise UnknownOperationError(
            f"{render(operation)} names component {component_id!r}, which this "
            f"programme does not hold; it has "
            f"{sorted(program.components)!r}"
        )


def compiler() -> OperationCompiler:
    """Return the slice's :data:`~sciagent.experiments.executor.OperationCompiler`.

    Guarantees that every operation it accepts is realised as an act this
    environment can actually perform, that every one it cannot is refused by
    name rather than approximated, and that the programme's observation process
    is applied to all of them alike. ``QueryDiagnostic`` is the identity;
    ``CompareCandidates`` belongs to backlog item 8 and is refused by the
    executor before reaching here.
    """

    def compile_operation(
        operation: Operation, program: GenerativeProgram, n_events: int
    ) -> CompiledOperation:
        compiled = _compile_act(operation, program, n_events)
        censoring = observed(program)
        if censoring is None:
            return compiled
        return replace(compiled, restrict=_compose(censoring, compiled.restrict))

    def _compile_act(
        operation: Operation, program: GenerativeProgram, n_events: int
    ) -> CompiledOperation:
        match operation:
            case QueryDiagnostic():
                return CompiledOperation(program=program)

            case PerturbParameter():
                _require_component(program, operation.component, operation)
                component = program.components[operation.component]
                if operation.parameter not in component.parameters:
                    raise UnknownOperationError(
                        f"{render(operation)} sets parameter "
                        f"{operation.parameter!r}, which component "
                        f"{operation.component!r} (family {component.family!r}) "
                        f"does not declare; it has "
                        f"{sorted(component.parameters)!r}"
                    )
                perturbed = FrozenDict[str, float](
                    {**component.parameters, operation.parameter: operation.value}
                )
                return CompiledOperation(
                    program=_with_parameters(program, operation.component, perturbed),
                    manipulated=targets(operation),
                )

            case ConditionOn():
                covariate = _covariate(operation.covariate)
                low, high = operation.low, operation.high

                def restrict(log: EventLog) -> EventLog:
                    values = covariate(log)
                    return _select(log, (values >= low) & (values <= high))

                return CompiledOperation(program=program, restrict=restrict)

            case ForceArrival():
                _require_component(program, operation.component, operation)
                schedule = _validated_schedule(operation, n_events)
                boundary = schedule[max(schedule)]
                window = operation.observe

                def restrict_after(log: EventLog) -> EventLog:
                    after = np.flatnonzero(log.values[ARRIVAL] > boundary)[:window]
                    keep = np.zeros(log.n_events, dtype=np.bool_)
                    keep[after] = True
                    return _select(log, keep, rebase=boundary)

                return CompiledOperation(
                    program=program,
                    clamps=FrozenDict[ComponentId, FrozenDict[int, float]](
                        {operation.component: schedule}
                    ),
                    restrict=restrict_after,
                    manipulated=targets(operation),
                )

            case AblateComponent():
                _require_component(program, operation.component, operation)
                if operation.component in _TIME_VALUED:
                    raise UnknownOperationError(
                        f"{render(operation)} would hold {operation.component!r} "
                        f"fixed for the whole run, but its values are absolute "
                        f"times: every event would occur at the same instant and "
                        f"no arrival diagnostic would be defined. Ablate a "
                        f"downstream component, or perturb this one"
                    )
                return CompiledOperation(
                    program=program,
                    clamps=FrozenDict[ComponentId, FrozenDict[int, float]](
                        {
                            operation.component: FrozenDict[int, float](
                                {index: operation.value for index in range(n_events)}
                            )
                        }
                    ),
                    manipulated=targets(operation),
                )

            case CompareCandidates():  # pragma: no cover - executor refuses first
                raise UnknownOperationError(
                    f"{render(operation)} is realised by backlog item 8, not by "
                    f"an environment compiler"
                )

    return compile_operation


def _validated_schedule(
    operation: ForceArrival, n_events: int
) -> FrozenDict[int, float]:
    """Return the forcing schedule, checked against this environment's meaning.

    A time-valued component may only be forced over a prefix of the run, with
    strictly ascending positive times. Everything else is refused: see the module
    docstring for why monotonicity is not checkable at compile time off a prefix.
    """
    schedule = operation.at
    indices = sorted(schedule)
    if max(indices) + operation.observe >= n_events:
        raise MalformedDesignError(
            f"{render(operation)} forces up to event {max(indices)} of a "
            f"{n_events}-event run and then observes {operation.observe} more, "
            f"which the run does not contain"
        )
    if operation.component not in _TIME_VALUED:
        return schedule
    if indices != list(range(len(indices))):
        raise MalformedDesignError(
            f"{render(operation)} forces a non-prefix of the run "
            f"({indices!r}); an arrival time forced mid-run may fall before its "
            f"predecessor, and whether it does is not knowable before the run"
        )
    times = [schedule[index] for index in indices]
    if times[0] <= 0.0:
        raise MalformedDesignError(
            f"{render(operation)} forces the first arrival to {times[0]!r}; "
            f"arrival times are measured from zero and must be positive"
        )
    for earlier, later in pairwise(times):
        if not later > earlier:
            raise MalformedDesignError(
                f"{render(operation)} forces a non-ascending arrival schedule {times!r}"
            )
    return schedule
