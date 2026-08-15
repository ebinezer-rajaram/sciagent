"""SPEC §4.4's experiment operations, and the designs built from them.

An :class:`ExperimentDesign` is the answer to "what shall we do next": one act
performed on the environment, plus the finite outcome space the resulting
measurement falls in. It is what backlog item 8's one-step-greedy BOED chooses
between, and it is what
:class:`~sciagent.inference.interface.ExperimentTemplate` was a placeholder for
-- a template says *what is measured*, and a design adds *what is done*.

Structure here, semantics elsewhere
-----------------------------------

An operation is a typed value and nothing more. It names a component, a
parameter, a covariate; it does not know what an arrival is, how to remove one,
or what a legal ablation target would be. Carrying it out is the business of an
environment-supplied compiler (see
:data:`~sciagent.experiments.executor.OperationCompiler`), in exactly the way
that family semantics reach a programme through a
:class:`~sciagent.core.program.FamilyLibrary` and an estimator reaches a metric
through a :class:`~sciagent.registry.metrics.MetricSpec`. That is what lets the
operation set be domain-independent while ``ForceArrival`` still means something
specific in the point-process slice.

Two renderings, for two readers
-------------------------------

:attr:`ExperimentDesign.id` is a *readable* canonical rendering
(``query:inter_arrival_dispersion``, ``force[arrival@0=0.05]:mean_rate``). It
addresses a row of the empirical table and appears in every log, so it is built
to be recognised by eye. Injectivity is enforced rather than hoped for:
identifiers carrying a character the rendering reserves are refused at
construction, so two designs render alike only if they are alike.

:meth:`ExperimentDesign.config` is the rendering the *registry* addresses by. It
is a plain string mapping, which
:class:`~sciagent.registry.store.ExperimentKey` length-prefix-encodes, so the
content address is injective by the store's own construction and does not depend
on the reserved-character rule above.

``CompareCandidates``
---------------------

Present so that SPEC §4.4's operation set is complete, and permanently without
an executor path. Every other operation is one execution yielding one
:data:`~sciagent.inference.binning.DiagnosticVector`; this one scores candidate
defects against each other, which is precisely what one-step-greedy BOED does.
It is realised by :func:`sciagent.experiments.boed.compare`, as *selection*:
asked which of these candidates is right, BOED answers with the experiment that
would best tell them apart, and performs none of them.
:class:`~sciagent.experiments.executor.Executor` therefore refuses it by name,
and the refusal is settled rather than provisional -- an operation that measures
nothing has no result to register and no budget to charge.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

from sciagent.core.edits import Defect, canonical, sort_key
from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import (
    ComponentId,
    ExperimentTemplateId,
    FrozenDict,
    MetricName,
)
from sciagent.inference.binning import OutcomeSpace
from sciagent.inference.interface import ExperimentTemplate

__all__ = [
    "AblateComponent",
    "CompareCandidates",
    "ConditionOn",
    "ExperimentDesign",
    "ForceArrival",
    "Operation",
    "PerturbParameter",
    "QueryDiagnostic",
    "defect_key",
    "operation_config",
    "render",
    "targets",
]

#: Characters the readable rendering uses as structure. An identifier carrying
#: one of them could produce a rendering that another design also produces, so
#: they are refused at construction rather than escaped: every identifier in the
#: system is already a Python-style name, so the rule costs nothing and the
#: alternative is an escaping scheme nobody would read.
RESERVED: Final = frozenset("[]:,=@|.~ \t\n")


def _check_identifier(kind: str, value: str) -> str:
    """Return ``value``, refusing any character the rendering reserves."""
    if not value:
        raise MalformedDesignError(f"{kind} is empty")
    offending = sorted(RESERVED.intersection(value))
    if offending:
        raise MalformedDesignError(
            f"{kind} {value!r} carries the reserved character(s) {offending!r}; "
            f"an experiment design renders to a readable id and those characters "
            f"are its structure"
        )
    return value


def _check_finite(kind: str, value: float) -> float:
    """Return ``value``, refusing a non-finite one."""
    if not math.isfinite(value):
        raise MalformedDesignError(
            f"{kind} is {value!r}; an experiment must declare a value it could "
            f"actually set"
        )
    return value


# --------------------------------------------------------------------------
# The six operations (SPEC §4.4)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryDiagnostic:
    """Measure, under no manipulation.

    The observational operation, and the only one that licenses no causal claim
    on its own. Carries no fields: what is measured belongs to the design's
    outcome space, not to the act.
    """


@dataclass(frozen=True, slots=True)
class PerturbParameter:
    """Set one parameter of one component, then measure.

    A structural intervention on the programme rather than on a realisation: the
    component still draws, from a different parameterisation.
    """

    component: ComponentId
    parameter: str
    value: float

    def __post_init__(self) -> None:
        _check_identifier("component id", str(self.component))
        _check_identifier("parameter name", self.parameter)
        _check_finite(f"perturbation of {self.component}.{self.parameter}", self.value)


@dataclass(frozen=True, slots=True)
class ConditionOn:
    """Measure over the events whose covariate falls in ``[low, high]``.

    Not an intervention: nothing about the programme changes and no causal claim
    is licensed. It is the operation SPEC §4.2 assigns to stage 2 of the minimum
    discriminating plan -- conditioning on phase removes a deterministic seasonal
    rate's overdispersion and removes nothing from the other three mechanisms.
    """

    covariate: MetricName
    low: float
    high: float

    def __post_init__(self) -> None:
        _check_identifier("covariate name", str(self.covariate))
        _check_finite(f"lower bound on {self.covariate}", self.low)
        _check_finite(f"upper bound on {self.covariate}", self.high)
        if not self.high > self.low:
            raise MalformedDesignError(
                f"conditioning on {self.covariate} in {self.low}..{self.high} "
                f"selects no event"
            )


@dataclass(frozen=True, slots=True)
class ForceArrival:
    """Force a component's value at named event indices, then measure.

    ``do(X = x)`` at a set of event indices. SPEC §4.2 makes this the *only*
    thing that separates Hawkes self-excitation from latent regime switching: a
    forced arrival raises the subsequent rate under self-excitation and under
    nothing else. Everything downstream of it -- stage 3 of the minimum
    discriminating plan, scenario S10's non-identifiability, and item 8's claim
    to be a fair baseline -- rests on this operation existing.

    What ``at`` may contain is the environment's judgement, not this type's. An
    arrival component's values are times and must ascend; a sign component's are
    two-valued. The compiler refuses a schedule its environment cannot mean.

    ``observe`` is how many events after the last forced one the measurement is
    read over, and it is part of the design rather than a detail of it. A
    response to an intervention decays, so a window much longer than the decay
    dilutes the effect into the run's average and a window shorter than it
    measures noise; which is which is a property of the mechanism under test, so
    the length is something an experiment *chooses* and something backlog item
    8's BOED will choose between.
    """

    component: ComponentId
    at: FrozenDict[int, float]
    observe: int

    def __post_init__(self) -> None:
        _check_identifier("component id", str(self.component))
        if not self.at:
            raise MalformedDesignError(
                f"ForceArrival on {self.component!r} forces no event; an "
                f"intervention that changes nothing is not an experiment"
            )
        for index in sorted(self.at):
            if index < 0:
                raise MalformedDesignError(
                    f"ForceArrival on {self.component!r} names event index {index}"
                )
            _check_finite(f"forced value at event {index}", self.at[index])
        if self.observe < 2:
            raise MalformedDesignError(
                f"ForceArrival on {self.component!r} observes {self.observe} "
                f"event(s) after the intervention; no diagnostic is defined on "
                f"fewer than two"
            )


@dataclass(frozen=True, slots=True)
class AblateComponent:
    """Hold a component fixed for the whole run, then measure.

    The controlled-direct-effect operation of SPEC §7.2: a component that was
    actually held fixed in the executed experiment, rather than one merely
    declared to have been. Realised as a clamp over every event index, so
    "held fixed" is a property of the log and is checkable from it.
    """

    component: ComponentId
    value: float

    def __post_init__(self) -> None:
        _check_identifier("component id", str(self.component))
        _check_finite(f"ablation value for {self.component}", self.value)


@dataclass(frozen=True, slots=True)
class CompareCandidates:
    """Score candidate defects against each other.

    Typed here so SPEC §4.4's operation set is complete; realised by
    :func:`sciagent.experiments.boed.compare`. See this module's docstring for
    why it has no executor path.
    """

    candidates: tuple[Defect, ...]

    def __post_init__(self) -> None:
        if len(self.candidates) < 2:
            raise MalformedDesignError(
                f"CompareCandidates was given {len(self.candidates)} candidate(s); "
                f"a comparison needs at least two"
            )


type Operation = (
    QueryDiagnostic
    | PerturbParameter
    | ConditionOn
    | ForceArrival
    | AblateComponent
    | CompareCandidates
)

#: Every operation type, in a fixed order independent of import or hash order.
OPERATION_TYPES: Final[tuple[type, ...]] = (
    QueryDiagnostic,
    PerturbParameter,
    ConditionOn,
    ForceArrival,
    AblateComponent,
    CompareCandidates,
)


# --------------------------------------------------------------------------
# Canonical renderings
# --------------------------------------------------------------------------


@lru_cache(maxsize=4096)
def defect_key(defect: Defect) -> str:
    """Return a canonical string for a defect.

    Guarantees independence of the set's iteration order: edits are placed in
    :func:`~sciagent.core.edits.canonical` order and rendered by their total
    :func:`~sciagent.core.edits.sort_key`, which covers the edit type, its full
    target identity, the construct it selects and every parameter. The empty
    defect -- the null hypothesis, which is a hypothesis and not an absence of
    one -- renders as ``"null"`` rather than as an empty string, so a defect
    field is never blank in a content address.

    Memoised on the same argument as
    :func:`~sciagent.inference.empirical.structure_key`, for the same reason --
    it is called once per candidate per comparison on a path that revisits the
    same handful of edit sets all run -- and sound for the same reason: the cache
    identifies a ``Defect`` by ``__eq__`` while the key is rendered by ``repr``,
    and :func:`~sciagent.core.edits.sort_key` normalises the values it renders so
    that equal defects cannot render differently.
    """
    if not defect:
        return "null"
    return "|".join(repr(sort_key(edit)) for edit in canonical(defect))


def render(operation: Operation) -> str:
    """Return the readable canonical rendering of an operation.

    Guarantees the rendering is injective over well-formed operations: the
    leading keyword identifies the type, brackets delimit its fields, and every
    identifier that could otherwise blur a delimiter was refused at
    construction. Two operations render alike only if they are equal.
    """
    match operation:
        case QueryDiagnostic():
            return "query"
        case PerturbParameter():
            return (
                f"perturb[{operation.component}.{operation.parameter}"
                f"={operation.value!r}]"
            )
        case ConditionOn():
            return (
                f"condition[{operation.covariate}"
                f"={operation.low!r}..{operation.high!r}]"
            )
        case ForceArrival():
            schedule = ",".join(
                f"{index}={operation.at[index]!r}" for index in sorted(operation.at)
            )
            return f"force[{operation.component}@{schedule}|{operation.observe}]"
        case AblateComponent():
            return f"ablate[{operation.component}={operation.value!r}]"
        case CompareCandidates():
            # Digested rather than spelled out: a candidate set is unbounded in
            # size, and an id that grows with it would be unreadable in exactly
            # the runs where reading it matters.
            payload = "\x00".join(
                sorted(defect_key(defect) for defect in operation.candidates)
            )
            digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
            return f"compare[{len(operation.candidates)}#{digest}]"


def targets(operation: Operation) -> frozenset[ComponentId]:
    """Return the components an operation manipulates.

    Empty for the two observational operations. This is what the executor
    derives collateral effects from (SPEC §3.3: collateral is derived from the
    programme DAG, never declared), and a compiler may widen it if an
    environment's realisation reaches further than the operation names.
    """
    match operation:
        case QueryDiagnostic() | ConditionOn() | CompareCandidates():
            return frozenset()
        case PerturbParameter() | ForceArrival() | AblateComponent():
            return frozenset({operation.component})


def operation_config(operation: Operation) -> Mapping[str, str]:
    """Return an operation's fields as the string mapping the registry stores.

    Every field that determines the act appears, so that two experiments sharing
    a content address really did do the same thing. The registry never
    interprets these strings (see :class:`~sciagent.registry.store.ExperimentKey`).
    """
    match operation:
        case QueryDiagnostic():
            return {}
        case PerturbParameter():
            return {
                "op.component": str(operation.component),
                "op.parameter": operation.parameter,
                "op.value": repr(operation.value),
            }
        case ConditionOn():
            return {
                "op.covariate": str(operation.covariate),
                "op.low": repr(operation.low),
                "op.high": repr(operation.high),
            }
        case ForceArrival():
            return {
                "op.component": str(operation.component),
                "op.at": ",".join(
                    f"{index}={operation.at[index]!r}" for index in sorted(operation.at)
                ),
                "op.observe": str(operation.observe),
            }
        case AblateComponent():
            return {
                "op.component": str(operation.component),
                "op.value": repr(operation.value),
            }
        case CompareCandidates():
            return {
                "op.candidates": "\x00".join(
                    sorted(defect_key(defect) for defect in operation.candidates)
                )
            }


# --------------------------------------------------------------------------
# Designs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentDesign:
    """One repeatable experimental design: an act, an outcome space, a run length.

    Guarantees a stable :attr:`id` and a stable :meth:`config`, both pure
    functions of the fields they are built from, so a design carries the same
    identity in every process and every run.

    :meth:`config` covers all three fields and is injective. :attr:`id` covers
    the operation and the metrics only, and is therefore **not** -- see its own
    docstring for why ``n_events`` is deliberately absent, and what enforces the
    distinction where it matters.
    """

    operation: Operation
    outcome: OutcomeSpace
    n_events: int
    """Events per execution. Part of the design, and part of :meth:`config`: a
    diagnostic's sampling distribution depends on how much data it saw, so two
    run lengths are two designs and two registry rows.

    They are **not** two :attr:`id`\\ s. Offering two such designs to one
    investigation is refused rather than silently collapsed -- by
    :meth:`~sciagent.inference.empirical.EmpiricalTable.build`, by
    :func:`~sciagent.experiments.boed.rank`, and by
    :meth:`~sciagent.experiments.executor.Executor.simulator`."""

    _id: ExperimentTemplateId = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.n_events <= 0:
            raise MalformedDesignError(
                f"design {render(self.operation)} declares n_events="
                f"{self.n_events}; a design must run for a positive number of "
                f"events"
            )
        measures = ",".join(
            _check_identifier("metric name", str(axis.metric.name))
            for axis in self.outcome.axes
        )
        object.__setattr__(
            self,
            "_id",
            ExperimentTemplateId(f"{render(self.operation)}:{measures}"),
        )

    @property
    def id(self) -> ExperimentTemplateId:
        """Return the readable canonical id of this design.

        A ``QueryDiagnostic`` design over a single metric renders as
        ``query:<metric>``, which is the id the slice's templates have carried
        since backlog item 6 -- so introducing the DSL leaves
        :attr:`~sciagent.inference.empirical.EmpiricalTable.version` unchanged
        and a built table still loads.

        **Not injective over designs**, and deliberately so: ``n_events`` is
        absent, so two designs differing only in run length render alike. Putting
        it in would be the tidier rule and costs more than it is worth -- every
        id would change, hence every table version and every registry content
        address, retiring every stored table and every registered row to fix a
        collision no caller can reach by accident. What the id has to be is
        *unambiguous within one investigation*, and that is enforced where such a
        set is assembled: :meth:`~sciagent.inference.empirical.EmpiricalTable
        .build`, :func:`~sciagent.experiments.boed.rank` and
        :meth:`~sciagent.experiments.executor.Executor.simulator` each refuse a
        repeated id rather than keeping whichever design came last.
        """
        return self._id

    @property
    def metrics(self) -> tuple[MetricName, ...]:
        """Return the diagnostics this design measures, in vector order."""
        return tuple(axis.metric.name for axis in self.outcome.axes)

    def template(self) -> ExperimentTemplate:
        """Return the posterior engine's view of this design.

        The engine needs a stable identity and a finite outcome space and
        nothing else; what is *done* is the executor's business. This is the one
        conversion between the two, so the engine cannot come to depend on an
        operation.
        """
        return ExperimentTemplate(
            id=self.id, outcome=self.outcome, n_events=self.n_events
        )

    def config(self) -> FrozenDict[str, str]:
        """Return everything about this design that determines a result.

        Feeds :attr:`~sciagent.registry.store.ExperimentKey.config`. The outcome
        space enters by its content hash rather than by its edges: a table read
        under a discretisation it was not built under gives confidently wrong
        likelihoods, and the address is where that has to be caught.
        """
        return FrozenDict[str, str](
            {
                "design": str(self.id),
                "operation": type(self.operation).__name__,
                "outcome": self.outcome.version,
                "n_events": str(self.n_events),
                **operation_config(self.operation),
            }
        )
