"""Carrying out an experiment design, and registering what it returned.

One execution path, two consumers
---------------------------------

:meth:`Executor.measure` is the whole of "apply this defect, perform this act,
read these diagnostics". Everything else is a wrapper on it:

* :meth:`Executor.run` adds the parts that make an execution a *registered
  experiment* -- a content address, an append-only row, a budget charge. This is
  what an investigation calls.
* :meth:`Executor.simulator` adds nothing at all. It hands the posterior engine
  a :data:`~sciagent.inference.interface.Simulator`, whose ten thousand
  executions are the engine's internal arithmetic and emphatically not
  experiments: registering them would fill the record with rows nobody performed
  and make the reported cost of an investigation meaningless.

Keeping both on one path is the point. If the engine's likelihood came from a
different execution route than the observation it is scoring, a discrepancy
between them would be invisible and would look like evidence.

Semantics are injected
----------------------

The framework knows that an operation names a component and that a clamp forces
a value. It does not know what forcing an arrival *means*, which values a
component may legally take, or whether ablating one is coherent. An
:data:`OperationCompiler` supplied by the environment turns an operation into a
programme, a clamp schedule and an optional restriction of the log, in the same
shape as :class:`~sciagent.core.program.FamilyLibrary` and
:class:`~sciagent.registry.metrics.MetricSpec`.

Mutability
----------

An executor accumulates: a budget is spent, programmes and executions are
cached. Like :class:`~sciagent.inference.empirical.EmpiricalTableEngine` it is
therefore not a value type, and for the same reason -- an investigation is a
growing record. Everything it *returns* is frozen, and the caches are keyed on
exactly the inputs that determine an execution, so they change how long a call
takes and never what it returns.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import RegistryError, UnknownOperationError
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    ComponentId,
    DataVersion,
    EnvVersion,
    EventLog,
    ExperimentId,
    ExperimentTemplateId,
    FamilyId,
    FrozenDict,
    Scope,
    Seed,
)
from sciagent.experiments.dsl import (
    CompareCandidates,
    ExperimentDesign,
    Operation,
    defect_key,
    render,
)
from sciagent.inference.binning import DiagnosticVector
from sciagent.inference.interface import ExperimentTemplate, Simulator
from sciagent.registry.budget import Budget
from sciagent.registry.metrics import MetricRegistry
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentKey, ExperimentRecord, ExperimentStore

__all__ = [
    "CompiledOperation",
    "ExecutionResult",
    "Executor",
    "OperationCompiler",
]


@dataclass(frozen=True, slots=True)
class CompiledOperation:
    """What an environment says an operation amounts to, on one programme.

    Guarantees nothing about the operation's meaning -- that is the compiler's
    to guarantee -- but fixes the four things the executor is able to carry out:
    which programme runs, which values are forced, how the resulting log is
    restricted before measurement, and which components were manipulated.
    """

    program: GenerativeProgram
    clamps: FrozenDict[ComponentId, FrozenDict[int, float]] = field(
        default_factory=FrozenDict
    )
    restrict: Callable[[EventLog], EventLog] | None = field(default=None, compare=False)
    """Applied after execution, before measurement. This is how an operation
    that selects events -- ``ConditionOn``, or the post-intervention window a
    ``ForceArrival`` is read over -- reaches a metric, which is a function of a
    whole log and nothing else."""

    manipulated: frozenset[ComponentId] = frozenset()
    """Components this realisation actually intervenes on. Defaults to empty and
    is widened by the compiler, never narrowed by the executor: an environment
    whose realisation reaches further than the operation names must say so, and
    the collateral set is derived from this."""


#: Turns an operation into something executable, on a given programme, for a
#: given run length. The run length is passed because an operation may need it
#: to build its schedule -- ``AblateComponent`` holds a component fixed for the
#: *whole* run, which is not expressible without knowing how long that is.
type OperationCompiler = Callable[
    [Operation, GenerativeProgram, int], CompiledOperation
]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """One experiment that was carried out and registered."""

    design: ExperimentDesign
    defect: Defect
    seed: Seed
    result: DiagnosticVector
    record: ExperimentRecord
    manipulated: frozenset[ComponentId]
    collateral: frozenset[ComponentId]
    """Derived from the programme DAG (SPEC §3.3), never declared. These are the
    components an intervention reached without being aimed at, and SPEC §7.2
    licenses a total-effect claim over their union but no narrower one."""

    budget: Budget
    """The budget *after* this experiment was charged."""

    held_fixed: frozenset[ComponentId] = frozenset()
    """Components clamped at every event index of this run.

    SPEC §7.2's controlled-direct-effect row licenses a claim only if "the
    held-fixed components were actually held fixed in the executed experiment",
    which is a question about what ran and not about what was declared. Read off
    the compiled clamp schedule, so a compiler that clamped a prefix and a
    compiler that clamped the whole run are distinguishable here, and a claim
    resting on the first is refused by :mod:`sciagent.verify.causal`."""

    @property
    def experiment(self) -> ExperimentId:
        """Return this experiment's id, which is its registry content address.

        Naming an experiment by its address is what makes A13 and A15 statements
        about the investigation record and not only about the store: two runs
        that cite the same experiment id cited the same bits.
        """
        return ExperimentId(str(self.record.digest))


class Executor:
    """Runs experiment designs against an environment and registers the results.

    Guarantees that a design's result is a pure function of
    ``(design, defect, seed)`` and the injected environment, and that every
    experiment :meth:`run` performs is registered at the content address over
    everything that determined it.
    """

    __slots__ = (
        "_budget",
        "_compile",
        "_compiled",
        "_cost",
        "_data_version",
        "_env_version",
        "_grammar",
        "_log_cache",
        "_metrics",
        "_partition",
        "_reference",
        "_store",
    )

    def __init__(
        self,
        *,
        reference: GenerativeProgram,
        grammar: EditGrammar,
        compile: OperationCompiler,
        metrics: MetricRegistry,
        env_version: EnvVersion,
        data_version: DataVersion,
        store: ExperimentStore | None = None,
        partition: DataPartition = DataPartition.DEV,
        budget: Budget | None = None,
        cost: float = 1.0,
    ) -> None:
        self._reference = reference
        self._grammar = grammar
        self._compile = compile
        self._metrics = metrics
        self._store = store
        self._env_version = env_version
        self._data_version = data_version
        self._partition = partition
        self._budget = budget if budget is not None else Budget(total=math.inf)
        self._cost = cost
        self._compiled: dict[Defect, GenerativeProgram] = {}
        self._log_cache: dict[tuple[Defect, Operation, int, int], EventLog] = {}

    # -- accounting --------------------------------------------------------

    @property
    def budget(self) -> Budget:
        """Return the budget as it now stands."""
        return self._budget

    @property
    def partition(self) -> DataPartition:
        """Return the pool this executor registers into."""
        return self._partition

    @property
    def reference(self) -> GenerativeProgram:
        """Return the undefected programme every experiment is an edit of.

        Read-only, and safe to hand out: a
        :class:`~sciagent.core.program.GenerativeProgram` is frozen, and this is
        the *reference*, so it carries no scenario's ground truth. SPEC §7.2's
        licensing rules quantify over its DAG, which is why the verifier needs it.
        """
        return self._reference

    def scope(self) -> Scope:
        """Return where the experiments this executor runs are gathered.

        Everything SPEC §7.1 clause 3 compares, derived from what the executor
        already holds: the environment version, the reference programme's
        families, and each component's parameters as a degenerate range, since
        one programme was run at one parameterisation and claiming a wider one
        would be claiming an experiment nobody performed. Parameters are keyed
        ``component.parameter`` so that two components declaring ``rate`` do not
        collapse into one axis.

        Carried here rather than assembled by a caller because a scope built from
        somewhere other than the executor could describe a run that did not
        happen, and every claim's coverage is judged against it.
        """
        parameters: dict[str, tuple[float, float]] = {}
        families: set[FamilyId] = set()
        for component_id in sorted(self._reference.components):
            component = self._reference.components[component_id]
            families.add(component.family)
            for name in sorted(component.parameters):
                value = component.parameters[name]
                parameters[f"{component_id}.{name}"] = (value, value)
        return Scope(
            families=frozenset(families),
            parameters=FrozenDict[str, tuple[float, float]](parameters),
            env_version=self._env_version,
            metric_version=self._metrics.version,
            grammar_version=self._grammar.version,
        )

    # -- execution ---------------------------------------------------------

    def measure(
        self, design: ExperimentDesign, defect: Defect, seed: Seed
    ) -> DiagnosticVector:
        """Carry out ``design`` under ``defect`` and ``seed``, and measure.

        Guarantees a pure function of its arguments and the injected
        environment: nothing is registered, no budget is charged, and the caches
        consulted are keyed on exactly what determines an execution. Raises
        :class:`~sciagent.core.errors.UnknownOperationError` for an operation the
        executor has no path for.
        """
        compiled = self._compiled_operation(design, defect)
        key = (defect, design.operation, int(seed), design.n_events)
        log = self._log_cache.get(key)
        if log is None:
            log = compiled.program.execute(
                seed, design.n_events, clamps=compiled.clamps
            )
            # One slot. A design set asks every design for the same
            # (defect, seed) in turn, so a single entry is the whole win, and
            # holding more would grow without bound over a table build.
            self._log_cache.clear()
            self._log_cache[key] = log
        observed = compiled.restrict(log) if compiled.restrict is not None else log
        return tuple(
            self._metrics.spec(str(name)).compute(observed) for name in design.metrics
        )

    def run(
        self, design: ExperimentDesign, defect: Defect, seed: Seed
    ) -> ExecutionResult:
        """Carry out ``design``, register the result, and charge the budget.

        Guarantees the registered row's address covers everything that
        determined it -- the environment version, the design, the defect, the
        manipulated and collateral sets, the data version, the metric registry
        version and the seed -- so a rerun that disagrees raises
        :class:`~sciagent.core.errors.RegistryConflictError` rather than being
        recorded as a second finding.

        The budget is checked before the experiment runs and committed only
        after it is registered, so a failed execution neither spends nor is
        cited. Raises :class:`~sciagent.core.errors.BudgetExhaustedError` if the
        experiment is unaffordable, and
        :class:`~sciagent.core.errors.RegistryError` if this executor holds no
        store: an unregistered result is not an experiment, and returning one
        that claimed to be would put an uncitable number into an investigation.
        """
        if self._store is None:
            raise RegistryError(
                f"cannot run {design.id!r}: this executor holds no registry, so "
                f"the result could not be registered. Use measure() for an "
                f"execution that is not an experiment"
            )
        charged = self._budget.charge(self._cost)  # raises if unaffordable
        compiled = self._compiled_operation(design, defect)
        result = self.measure(design, defect, seed)
        manipulated = compiled.manipulated
        collateral = self._collateral(compiled.program, manipulated)
        held_fixed = self._held_fixed(compiled, design.n_events)
        key = ExperimentKey(
            env_version=self._env_version,
            config=self._config(design, defect, manipulated, collateral, held_fixed),
            data_version=self._data_version,
            metric_version=self._metrics.version,
            seed=seed,
        )
        record = self._store.append(key, partition=self._partition, result=result)
        self._budget = charged
        return ExecutionResult(
            design=design,
            defect=defect,
            seed=seed,
            result=result,
            record=record,
            manipulated=manipulated,
            collateral=collateral,
            budget=self._budget,
            held_fixed=held_fixed,
        )

    def simulator(self, designs: Sequence[ExperimentDesign]) -> Simulator:
        """Return a :data:`~sciagent.inference.interface.Simulator` over ``designs``.

        Guarantees the engine's likelihood simulations travel the same execution
        path as the observations it scores them against, and that none of them is
        registered or charged: they are the engine's arithmetic, not experiments.

        The engine holds :class:`ExperimentTemplate`\\ s, which say what is
        measured and not what is done, so the mapping from template id back to
        design is supplied here. A template the executor was not given is a
        framework fault and raises.
        """
        by_id: dict[ExperimentTemplateId, ExperimentDesign] = {
            design.id: design for design in designs
        }

        def simulate(
            defect: Defect, template: ExperimentTemplate, seed: Seed
        ) -> DiagnosticVector:
            design = by_id.get(template.id)
            if design is None:
                raise UnknownOperationError(
                    f"no design for template {template.id!r}; this executor was "
                    f"given {sorted(by_id)!r}"
                )
            return self.measure(design, defect, seed)

        return simulate

    # -- internals ---------------------------------------------------------

    def _compiled_operation(
        self, design: ExperimentDesign, defect: Defect
    ) -> CompiledOperation:
        if isinstance(design.operation, CompareCandidates):
            raise UnknownOperationError(
                f"{render(design.operation)} has no executor path and will not "
                f"acquire one: it measures nothing. Comparing candidates against "
                f"each other is answered by selection, not by execution -- see "
                f"sciagent.experiments.boed.compare, which ranks the designs that "
                f"would separate them without performing any of them"
            )
        program = self._compiled.get(defect)
        if program is None:
            program = self._grammar.apply(self._reference, defect)
            self._compiled[defect] = program
        return self._compile(design.operation, program, design.n_events)

    @staticmethod
    def _collateral(
        program: GenerativeProgram, manipulated: frozenset[ComponentId]
    ) -> frozenset[ComponentId]:
        """Return everything downstream of the manipulated set but not in it."""
        reached: frozenset[ComponentId] = frozenset()
        for component_id in sorted(manipulated):
            reached |= program.descendants(component_id)
        return reached - manipulated

    @staticmethod
    def _held_fixed(
        compiled: CompiledOperation, n_events: int
    ) -> frozenset[ComponentId]:
        """Return the components clamped at *every* event index of the run.

        A prefix clamp is an intervention on part of a realisation; a clamp over
        the whole run is a component held fixed. SPEC §7.2 licenses a controlled
        direct effect only on the second, so the distinction is drawn from the
        compiled schedule rather than from the operation's name -- an environment
        whose ``AblateComponent`` reached only a prefix would be caught here and
        not silently licensed.
        """
        wanted = frozenset(range(n_events))
        return frozenset(
            component_id
            for component_id in sorted(compiled.clamps)
            if wanted <= frozenset(compiled.clamps[component_id])
        )

    def _config(
        self,
        design: ExperimentDesign,
        defect: Defect,
        manipulated: frozenset[ComponentId],
        collateral: frozenset[ComponentId],
        held_fixed: frozenset[ComponentId],
    ) -> FrozenDict[str, str]:
        """Return the content-address config for one execution.

        The derived sets are recorded rather than recomputed on read: SPEC §7.2's
        causal licensing asks what an experiment *did* manipulate and what it
        *did* hold fixed, and an audit a year later must not depend on the DAG,
        or on the environment's compiler, still being what it was.
        """
        return FrozenDict[str, str](
            {
                **design.config(),
                "grammar": str(self._grammar.version),
                "defect": defect_key(defect),
                "manipulated": ",".join(sorted(manipulated)),
                "collateral": ",".join(sorted(collateral)),
                "held_fixed": ",".join(sorted(held_fixed)),
            }
        )
