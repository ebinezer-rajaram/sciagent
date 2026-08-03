"""What a research system is handed, and what it must return.

The division of labour, which is SPEC's second invariant made structural:

* a system chooses **structure** -- :meth:`Investigation.run` picks an
  experiment, :meth:`Investigation.propose` introduces a hypothesis;
* the framework computes **numbers** -- the posterior comes off the engine, the
  prior off the grammar's code length, and a proposal's prediction thresholds
  off the table, via :func:`table_prediction`.

There is deliberately no argument anywhere in this module through which a system
could supply a probability, a plausibility or a score.

Ground truth is held by :class:`~sciagent.eval.scenarios.Scenario` and reaches an
:class:`Investigation` as a private attribute with no public accessor: a system
runs designs *against* the truth without being able to read it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from sciagent.core.conditions import Between, Condition, Not
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import InvestigationError
from sciagent.core.types import (
    Diagnosis,
    ExperimentId,
    FrozenDict,
    HypothesisId,
    MetricRef,
    Prediction,
    PredictionId,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.experiments.executor import ExecutionResult, Executor
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.hypothesis.validator import find_duplicate
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    replicate_seed,
)
from sciagent.inference.interface import ExpansionCost, PPCResult
from sciagent.registry.budget import Budget
from sciagent.registry.metrics import MetricRegistry

__all__ = [
    "NULL_DEFECT",
    "Investigation",
    "ResearchSystem",
    "diagnose",
    "entertain",
    "null_seeded_graph",
    "table_prediction",
]

#: The empty edit set: "nothing is wrong". A hypothesis and not an absence of
#: one, which is what makes :attr:`~sciagent.core.types.Diagnosis.null_mass`
#: something a posterior can carry.
NULL_DEFECT: Defect = frozenset()


class Investigation:
    """One scenario, as the system under evaluation sees it.

    Guarantees a system cannot reach the ground truth through any public
    attribute or method, cannot run a design the scenario does not offer, cannot
    exceed the budget, and cannot write a number: the only mutating operations
    are :meth:`run` and :meth:`propose`, and both take structure and return
    framework-computed consequences.

    Not immutable, unlike most of the framework. An investigation *is* the
    accumulating record of what a system did, and the engine it wraps is
    append-only for the same reason (see
    :class:`~sciagent.inference.empirical.EmpiricalTableEngine`).
    """

    __slots__ = (
        "_designs",
        "_engine",
        "_executor",
        "_graph",
        "_history",
        "_proposed",
        "_scenario_id",
        "_seed",
        "_targets",
        "_truth",
    )

    def __init__(
        self,
        *,
        scenario_id: ScenarioId,
        designs: Sequence[ExperimentDesign],
        truth: Defect,
        executor: Executor,
        engine: EmpiricalTableEngine,
        graph: HypothesisGraph,
        seed: Seed,
    ) -> None:
        if not designs:
            raise InvestigationError(
                f"investigation of {scenario_id!r} offers no design, so nothing can "
                f"be run"
            )
        self._scenario_id = scenario_id
        self._designs = tuple(designs)
        self._truth = truth
        self._executor = executor
        self._engine = engine
        self._graph = graph
        self._seed = seed
        self._history: list[ExecutionResult] = []
        self._proposed: dict[HypothesisId, Defect] = {}
        self._targets: dict[ExperimentId, tuple[HypothesisId, ...]] = {}

    # -- what a system may know --------------------------------------------

    @property
    def scenario_id(self) -> ScenarioId:
        """Return which scenario this is. Names the task, not the answer."""
        return self._scenario_id

    @property
    def designs(self) -> tuple[ExperimentDesign, ...]:
        """Return every design this scenario offers, in the scenario's order."""
        return self._designs

    @property
    def engine(self) -> EmpiricalTableEngine:
        """Return the posterior engine. Read-only in effect: it writes the numbers."""
        return self._engine

    @property
    def graph(self) -> HypothesisGraph:
        """Return the hypothesis graph as it now stands."""
        return self._graph

    @property
    def budget(self) -> Budget:
        """Return the allowance as it now stands."""
        return self._executor.budget

    @property
    def history(self) -> tuple[ExecutionResult, ...]:
        """Return every experiment run, in the order it was run."""
        return tuple(self._history)

    @property
    def proposed(self) -> FrozenDict[HypothesisId, Defect]:
        """Return the structures this system introduced, if any."""
        return FrozenDict[HypothesisId, Defect](self._proposed)

    @property
    def targets(self) -> FrozenDict[ExperimentId, tuple[HypothesisId, ...]]:
        """Return which hypotheses each experiment was aimed at, where stated."""
        return FrozenDict[ExperimentId, tuple[HypothesisId, ...]](self._targets)

    def affords(self, count: int = 1) -> bool:
        """Return whether ``count`` more experiments can be paid for."""
        return self.budget.affords(float(count))

    def posterior(self) -> Mapping[HypothesisId, Probability]:
        """Return the engine's current belief. Recomputed, never cached."""
        return self._engine.posterior()

    def ppc(self) -> PPCResult:
        """Return a posterior predictive check over what has been recorded."""
        return self._engine.ppc()

    # -- what a system may do ----------------------------------------------

    def run(
        self,
        design: ExperimentDesign,
        *,
        targets: Sequence[HypothesisId] = (),
    ) -> ExecutionResult:
        """Carry out one design against the hidden truth, and record it.

        Guarantees the seed is derived from the scenario seed, the design id and
        the step index, so a rerun of the same system on the same scenario
        performs byte-identical executions, and two different designs at the same
        step do not share a stream.

        ``targets`` names the hypotheses this experiment was aimed at. It is
        structure, so a system may state it and SPEC F7 is untouched; it is
        recorded here rather than in the registry because what an experiment was
        aimed at does not determine its result, and
        :class:`~sciagent.registry.store.ExperimentKey` covers what determines a
        result and nothing else. SPEC §7.1 clause 1 is the only thing that reads
        it, and a system that says nothing forfeits that clause and no other.

        Raises :class:`~sciagent.core.errors.InvestigationError` if the scenario
        does not offer ``design``, and
        :class:`~sciagent.core.errors.BudgetExhaustedError` if it cannot be paid
        for. Neither is caught here: a system that wants to stop before the
        budget runs out should check :meth:`affords`.
        """
        if design not in self._designs:
            raise InvestigationError(
                f"scenario {self._scenario_id!r} does not offer design "
                f"{design.id!r}; it offers "
                f"{sorted(str(d.id) for d in self._designs)!r}"
            )
        seed = replicate_seed(self._seed, str(design.id), len(self._history))
        result = self._executor.run(design, self._truth, seed)
        self._engine.record(result.experiment, design.template(), result.result)
        self._history.append(result)
        if targets:
            self._targets[result.experiment] = tuple(targets)
        return result

    def propose(
        self,
        node_id: HypothesisId,
        *,
        program_edit: Defect,
        predictions: Sequence[Prediction] | None = None,
        rationale: str = "",
    ) -> ExpansionCost:
        """Introduce a hypothesis and have the engine retro-evaluate it.

        The one thing a system authors. Structure is what agents write; the
        prior over it is derived from the grammar's code length and the
        likelihood from the table, so introducing a hypothesis cannot move a
        number in the proposer's favour.

        With ``predictions`` left at ``None`` -- which is what every baseline
        does -- the structure's table row is filled and
        :func:`table_prediction` derives the prediction from it. A system
        therefore says only *which* structure it wants to entertain, and the
        threshold that would refute it is the framework's. Supplying predictions
        explicitly is for a system that has a reason to claim something narrower;
        they are validated by the graph either way.

        ``proposed_at`` is set to the most recent experiment, so SPEC F9's rule
        -- lateness costs nothing in likelihood but forfeits a confirmatory claim
        without a prospectively registered experiment -- has the datum it needs.
        """
        if predictions is None:
            self._engine.ensure_structure(program_edit)
            predictions = [
                table_prediction(
                    self._engine.table, node_id, program_edit, self._designs[0]
                )
            ]
        proposed_at: ExperimentId | None = (
            self._history[-1].experiment if self._history else None
        )
        self._graph = self._graph.propose(
            node_id,
            program_edit=program_edit,
            predictions=predictions,
            rationale=rationale,
            proposed_at=proposed_at,
        )
        cost = self._engine.expand(self._graph.node(node_id))
        self._proposed[node_id] = program_edit
        return cost

    def conclude(
        self, *, residual_candidates: Sequence[HypothesisId] = ()
    ) -> Diagnosis:
        """Return the diagnosis this investigation's state implies.

        The way a system ends. It exists so that reporting what was proposed is
        not something a system can forget to do: the one argument is
        ``residual_candidates``, which is a statement about what to look at next
        and carries no number.

        :func:`~sciagent.eval.campaign.run_scenario` re-derives the same value
        and refuses a system whose return differs, so bypassing this is caught
        rather than merely discouraged.
        """
        return diagnose(
            self._scenario_id,
            self._engine,
            proposed_edits=self.proposed,
            residual_candidates=residual_candidates,
        )


@runtime_checkable
class ResearchSystem(Protocol):
    """What every evaluated system implements (SPEC §5).

    One method, so that a baseline and a hybrid LLM system are interchangeable
    at the harness and the comparison between them is not mediated by different
    plumbing.
    """

    @property
    def name(self) -> str:
        """Return the SPEC §5 identifier, e.g. ``"B4"``. Used in reports."""
        ...

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Investigate, and return what was concluded.

        Implementations build the return value with :func:`diagnose` rather than
        by hand; :func:`~sciagent.eval.campaign.run_scenario` re-derives it and
        refuses a diagnosis that disagrees with the engine.
        """
        ...


# --------------------------------------------------------------------------
# Turning an engine's state into a diagnosis
# --------------------------------------------------------------------------


def _supporting(
    engine: EmpiricalTableEngine,
    posterior: Mapping[HypothesisId, Probability],
) -> Mapping[HypothesisId, tuple[ExperimentId, ...]]:
    """Return, per hypothesis, the experiments whose evidence favoured it.

    An experiment supports a hypothesis when the hypothesis explains it better
    than the belief as a whole does -- its log-likelihood exceeds the
    posterior-weighted mean across live hypotheses. Derived, so a system cannot
    nominate its own supporting evidence, and computed over sorted keys with
    :func:`math.fsum`, so it is bit-reproducible.
    """
    live = engine.live
    if not live:
        return {}
    per_hypothesis: dict[HypothesisId, list[ExperimentId]] = {h: [] for h in live}
    for observation in engine.observations:
        experiment = observation.experiment
        scores = {h: engine.estimate(h, experiment).log_likelihood for h in live}
        mean = math.fsum(posterior.get(h, Probability(0.0)) * scores[h] for h in live)
        for h in live:
            if scores[h] > mean:
                per_hypothesis[h].append(experiment)
    return {h: tuple(per_hypothesis[h]) for h in live}


def diagnose(
    scenario_id: ScenarioId,
    engine: EmpiricalTableEngine,
    *,
    proposed_edits: Mapping[HypothesisId, Defect] | None = None,
    residual_candidates: Sequence[HypothesisId] = (),
) -> Diagnosis:
    """Build a :class:`~sciagent.core.types.Diagnosis` from an engine's state.

    The only supported way to produce one. Every number is read or derived
    here -- the distribution is :meth:`EmpiricalTableEngine.posterior`, the null
    mass is whatever that puts on the empty edit set, the abstain mass is
    ``1 - max_h p(h)``, and the supporting sets come from likelihood comparisons.
    The two arguments a caller does supply are both structure.

    Guarantees the result is a pure function of the engine's state and the two
    structural arguments, so two systems that did the same things report the
    same numbers.
    """
    posterior = engine.posterior()
    null_mass = math.fsum(
        posterior[h] for h in sorted(posterior) if not engine.program_edit(h)
    )
    leader = max(posterior[h] for h in sorted(posterior)) if posterior else 0.0
    return Diagnosis(
        scenario_id=scenario_id,
        distribution=FrozenDict[HypothesisId, Probability](posterior),
        abstain_mass=Probability(max(0.0, 1.0 - leader)),
        null_mass=Probability(min(1.0, null_mass)),
        proposed_edits=FrozenDict[HypothesisId, Defect](proposed_edits or {}),
        supporting=FrozenDict[HypothesisId, tuple[ExperimentId, ...]](
            _supporting(engine, posterior)
        ),
        residual_candidates=tuple(residual_candidates),
    )


# --------------------------------------------------------------------------
# Predictions a system may attach without authoring a number
# --------------------------------------------------------------------------


def _cell_condition(design: ExperimentDesign, cell: int) -> tuple[MetricRef, Condition]:
    """Return the metric and the interval a cell denotes, on a one-axis template.

    Restricted to single-diagnostic templates, which is what the slice declares
    (see :class:`~sciagent.inference.binning.OutcomeSpace`). A joint cell over
    several axes is a product of intervals and needs a conjunction over several
    diagnostics, which :class:`~sciagent.core.types.Prediction` -- one diagnostic
    per prediction -- cannot express, so this raises rather than quietly
    predicting one axis of several.
    """
    space = design.template().outcome
    if space.dimension != 1:
        raise InvestigationError(
            f"template {design.id!r} measures {space.dimension} diagnostics; a "
            f"table-derived prediction is defined for one-diagnostic templates "
            f"only, since a Prediction names a single diagnostic"
        )
    axis = space.axes[0]
    index = space.coordinates(cell)[0]
    edges = (axis.low, *axis.interior, axis.high)
    low, high = edges[index], edges[index + 1]
    return axis.metric, Between(
        low=low, high=high, low_closed=True, high_closed=index + 1 == axis.n_bins
    )


def entertain(investigation: Investigation, library: Mapping[str, Defect]) -> None:
    """Propose every library structure the graph does not already hold.

    Shared by the two systems that draw on a fixed library -- V1, which
    entertains all of it, and B4, which entertains what it retrieved. A structure
    already present is skipped rather than re-proposed: SPEC §6.4 A18 makes a
    duplicate an error, and the null is present from the start in every
    investigation.

    Each proposal carries the prediction :func:`table_prediction` derives for it
    under the scenario's first design, so a system introduces structure without
    authoring the threshold that would refute it.
    """
    for name in sorted(library):
        defect = library[name]
        if find_duplicate(investigation.graph, defect) is not None:
            continue
        node_id = HypothesisId(name)
        investigation.propose(
            node_id,
            program_edit=defect,
            rationale=f"library structure {name!r}",
        )


def null_seeded_graph(
    grammar: EditGrammar,
    metrics: MetricRegistry,
    table: EmpiricalTable,
    design: ExperimentDesign,
) -> HypothesisGraph:
    """Return a graph holding the null hypothesis and nothing else.

    Every investigation starts here, whatever system is being run. The null is
    seeded rather than left to a system to propose because B1 proposes nothing
    at all, and a posterior predictive check needs *some* hypothesis to check
    against: "the observations are not explained" is only a statement if there is
    something they are failing to be explained by.

    That it is the same starting point for all four systems is what makes their
    results comparable -- SPEC §5's comparison is between what systems *do*, not
    between what they were handed.
    """
    node_id = HypothesisId("null")
    return HypothesisGraph.empty(grammar, metrics).propose(
        node_id,
        program_edit=NULL_DEFECT,
        predictions=[table_prediction(table, node_id, NULL_DEFECT, design)],
        rationale="the null: nothing is wrong (SPEC §4.5 S9)",
    )


def table_prediction(
    table: EmpiricalTable,
    node_id: HypothesisId,
    program_edit: Defect,
    design: ExperimentDesign,
) -> Prediction:
    """Return the prediction a structure makes about a design, per the table.

    The system says *which* structure and *which* design; the framework says what
    that structure predicts, by reading the table's most probable cell for it.
    This is what lets a baseline propose a hypothesis without authoring a
    threshold, and it is why no baseline in this package contains a number.

    Guarantees the prediction is falsifiable -- the refutation is the complement
    of the condition over the diagnostic's declared range, so some attainable
    value refutes it and some confirms it, which is what SPEC §6.4 A16 requires.

    Raises :class:`~sciagent.core.errors.TableError` through the table if the
    structure has no row and no simulator is available to fill one.
    """
    probabilities = table.probabilities(program_edit, design.id)
    best = max(range(len(probabilities)), key=lambda c: (probabilities[c], -c))
    metric, condition = _cell_condition(design, best)
    return Prediction(
        id=PredictionId(f"{node_id}/{design.id}"),
        hypothesis_id=node_id,
        diagnostic=metric,
        condition=condition,
        under=design.id,
        refutation=Not(condition),
    )
