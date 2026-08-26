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

That sentence used to be the whole of the arrangement, and gate A41 found it
false in three places. The private attribute was never the leak; what leaked was
everything the framework handed *back*. :meth:`Investigation.run` returned the
executor's :class:`~sciagent.experiments.executor.ExecutionResult`, whose
``defect`` is the truth and whose ``record`` addresses the row by a content
config with ``defect_key(defect)`` in it; and :attr:`Investigation.engine` gave
out an :class:`~sciagent.inference.empirical.EmpiricalTable` whose structure keys
are readable renderings of every structure in it, the truth included. A system
is now handed :class:`~sciagent.experiments.executor.ObservedExecution` and a
table keyed by digest, both of which are projections with no field the truth
could sit in.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from sciagent.core.conditions import Between, Condition, Not
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import InvestigationError, NoLiveHypothesisError
from sciagent.core.types import (
    Diagnosis,
    ExperimentId,
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    MetricRef,
    Prediction,
    PredictionId,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.experiments import boed
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.experiments.executor import Executor, ObservedExecution
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.hypothesis.validator import find_duplicate
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    replicate_seed,
)
from sciagent.inference.interface import ExpansionCost, PPCResult
from sciagent.inference.view import EngineView
from sciagent.registry.budget import Budget
from sciagent.registry.metrics import MetricRegistry

__all__ = [
    "NULL_DEFECT",
    "Investigation",
    "ResearchSystem",
    "diagnose",
    "entertain",
    "null_seeded_graph",
    "select_experiments",
    "table_prediction",
    "table_predictions",
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

    The first clause is checked rather than asserted. Gate A41 traverses this
    class's public surface -- every name without a leading underscore, following
    what each returns -- and refuses any object that is, or renders, the
    scenario's truth. It found three routes when it was written, and what closed
    them is structural: :meth:`run` and :attr:`history` hand over an
    :class:`~sciagent.experiments.executor.ObservedExecution`, which has no
    ``defect`` field and no registry row, and :attr:`engine`'s table is keyed by
    digest. Nothing here relies on a caller declining to look.

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
        "_stage_a",
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
        stage_a: ExperimentId | None = None,
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
        self._stage_a = stage_a
        self._history: list[ObservedExecution] = []
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
    def engine(self) -> EngineView:
        """Return the posterior engine, as much of it as a system may reach.

        A view and not the engine, and the difference is the whole of SPEC's
        second invariant: the engine's ``record``, ``record_probe``, ``expand``
        and ``ensure_structure`` do not exist on what comes back, so a system
        cannot write the belief it is scored on. ``ppc`` is withheld too --
        :meth:`ppc` is the sanctioned path and scopes the check to the
        framework's Stage A reading, which ``engine.ppc(experiments={...})``
        would let a system choose for itself.

        This used to return ``self._engine`` under a docstring saying it was
        "read-only in effect". It was not, and a system that recorded a
        fabricated result scored on it with nothing raising.
        """
        return EngineView(self._engine)

    @property
    def graph(self) -> HypothesisGraph:
        """Return the hypothesis graph as it now stands."""
        return self._graph

    @property
    def budget(self) -> Budget:
        """Return the allowance as it now stands."""
        return self._executor.budget

    @property
    def history(self) -> tuple[ObservedExecution, ...]:
        """Return every experiment run, in the order it was run.

        A projection, not the executor's own record: see
        :meth:`~sciagent.experiments.executor.ExecutionResult.observed` for the
        two fields it drops and gate A41 for why.
        """
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
        """Return the adequacy check: is the entertained space the right one?

        This is SPEC §4.6's Stage A as a system sees it, and it is deliberately
        **not** a check over everything recorded. Where the scenario supplies a
        Stage A reading, the check is scoped to it alone.

        The reason is measured and is in ``docs/DECISIONS.md``. The combination
        rule scales the harmonic mean by ``1 + ln(n)``, so a check over the whole
        record dilutes the readings that bear on adequacy with the readings that
        do not -- and on a budgeted run almost all of them do not, because BOED
        selects designs to separate hypotheses *inside* the entertained set. An
        experiment chosen for that says close to nothing about whether the set is
        the right one. Scoped, S11 reads 0.0112 and every in-library scenario
        stays quiet; unscoped, S11 reads 0.2090 and nothing fires anywhere.

        The full-record check is still computed and reported -- ``ScenarioRun.ppc``
        keeps it, since it is the honest summary of how well the entertained set
        explains everything seen. What is scoped is the check a system *acts on*,
        which is the one SPEC F6 makes extension conditional upon.

        Falls back to the full record when the scenario declares no Stage A
        reading, so an environment that supplies none behaves exactly as before.
        """
        if self._stage_a is None:
            return self._engine.ppc()
        return self._engine.ppc(experiments={self._stage_a})

    # -- what a system may do ----------------------------------------------

    def run(
        self,
        design: ExperimentDesign,
        *,
        targets: Sequence[HypothesisId] = (),
    ) -> ObservedExecution:
        """Carry out one design against the hidden truth, and record it.

        Guarantees the seed is derived from the scenario seed, the design id and
        the step index, so a rerun of the same system on the same scenario
        performs byte-identical executions, and two different designs at the same
        step do not share a stream; and that what comes back says what was done
        and what was measured, but not what it was done *to*.

        The second half is gate A41 and is why this returns an
        :class:`~sciagent.experiments.executor.ObservedExecution` rather than the
        executor's own record. Closing only :attr:`history` would have left the
        shorter path open -- the object a system leaks the truth through is the
        one this method hands it.

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
        result = self._executor.run(design, self._truth, seed).observed()
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
        likelihood from the table, so introducing a hypothesis cannot move the
        **posterior** in the proposer's favour.

        That used to read *"cannot move a number"*, without qualification, and
        gate A30 made the unqualified form false. ``predictions`` below is now
        read by :func:`sciagent.verify.statistical.prediction_evidence`, which
        :func:`~sciagent.eval.campaign.adjudicate` reaches on every scored cell,
        so a system supplying its own predictions moves SPEC §12 criterion 10's
        adjudication rate. Before A30 the field reached no reported figure,
        because :func:`sciagent.verify.verify` had no caller in ``src`` at all.
        The claim about the prior and the likelihood is unchanged and still
        holds; what changed is that it is no longer the whole story, and
        ``docs/DECISIONS.md`` (2026-08-23) carries the rest.

        With ``predictions`` left at ``None`` -- which is what every baseline
        does -- the structure's table row is filled and
        :func:`table_predictions` derives one prediction per design the scenario
        offers. A system therefore says only *which* structure it wants to
        entertain, and the thresholds that would refute it are the framework's.
        Supplying predictions explicitly is for a system that has a reason to
        claim something narrower; they are validated by the graph either way, and
        the framework stamps :attr:`~sciagent.core.types.Prediction.authored` on
        them so that what the system chose its own threshold for is recoverable
        downstream. :func:`~sciagent.verify.statistical.check` is what acts on
        that stamp.

        ``proposed_at`` is set to the most recent experiment, so SPEC F9's rule
        -- lateness costs nothing in likelihood but forfeits a confirmatory claim
        without a prospectively registered experiment -- has the datum it needs.
        """
        # Unconditionally, and before the graph moves. A structure that cannot be
        # measured raises here, leaving the graph and the engine both untouched,
        # which is what lets B5 try a candidate and carry on. Inside the
        # ``predictions is None`` branch -- where it used to be -- the explicit
        # path reached the same call through ``expand`` *after* the graph had
        # already been reassigned, so a caught failure left the graph holding a
        # node the engine did not and tripped ``_reconcile`` on an honest system.
        self._engine.ensure_structure(program_edit)
        if predictions is None:
            predictions = list(
                table_predictions(
                    self._engine.table, node_id, program_edit, self._designs
                )
            )
        else:
            # Stamped rather than read. A caller able to set this flag is able to
            # clear it, so the framework overwrites it on everything it did not
            # derive itself -- which is what makes this a seal and not a
            # convention a system is trusted to keep.
            #
            # Unconditional, and deliberately not narrowed to conditions that
            # *contradict* the structure's table row. Contradicting the modal
            # cell makes a claim harder to confirm; the move that actually pays
            # is widening the condition to cover most of the diagnostic's range,
            # which strictly contains the row and contradicts nothing. A stamp
            # that fired only on contradiction would miss it.
            predictions = [
                replace(prediction, authored=True) for prediction in predictions
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

    Raises :class:`~sciagent.core.errors.NoLiveHypothesisError` if every
    hypothesis has been rejected. The engine reports that as a posterior of all
    zeros, which is the truthful answer to "how is the mass distributed" and not
    a distribution; without this the condition would surface two frames later as
    a :class:`~sciagent.core.types.Diagnosis` that fails to normalise, which
    names the symptom rather than the cause.
    """
    if engine.hypotheses and not engine.live:
        raise NoLiveHypothesisError(
            f"every one of the {len(engine.hypotheses)} hypotheses on scenario "
            f"{scenario_id!r} has been rejected, so there is no distribution to "
            f"report; a system that has ruled out its whole hypothesis space has "
            f"detected inadequacy, which is what the posterior predictive check "
            f"is for"
        )
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


def select_experiments(
    investigation: Investigation,
    steps: int,
    *,
    targets: Callable[[Investigation], Sequence[HypothesisId]] | None = None,
) -> None:
    """Spend ``steps`` experiments by expected information gain.

    The single selection call site. Every arm that selects at all comes through
    here, so *"the comparator chooses its evidence the way the treatment does"*
    is a property of one function rather than a resemblance between four that a
    later edit could quietly break in one of them.

    It exists because that resemblance did not hold. V1 and V7 planned while B4
    and B5 spent their budget on a fixed rotation through the design order, so
    SPEC §9's contrast -- designated to test *proposal source* -- carried a
    selection difference too, and "equal budget" held for the count and not for
    the informativeness. See ``docs/BACKLOG.md``'s A34 entry.

    Guarantees no number reaches the plan from the caller. The belief and the
    predictive are both taken off the engine inside
    :func:`~sciagent.experiments.boed.plan`; what a system supplies is which
    designs exist and how many steps to spend, neither of which is a number about
    the world.

    ``steps`` below one spends nothing rather than raising. A caller handing over
    "whatever is left" may legitimately have nothing left, and
    :func:`~sciagent.experiments.boed.greedy` refuses a plan of zero steps --
    which is right for a plan and wrong for a budget.

    The plan is run as one trajectory rather than step by step so that the belief
    BOED selects against is the belief it updates; see
    :func:`~sciagent.experiments.boed.greedy` on why designs are chosen with
    replacement.

    ``targets`` names, per step, the hypotheses the chosen experiment is aimed at,
    which is SPEC §7.1 clause 1's input. Optional, and its absence does not break
    the parity above: nothing in :mod:`sciagent.experiments.boed` reads it, so an
    arm that declares no target selects identically to an arm that does. What
    differs is what the relevance check can later say about the claims each makes,
    and that difference is declared in ``SPEC9_CONTRAST`` rather than hidden.
    """
    if steps < 1:
        return
    by_id: dict[ExperimentTemplateId, ExperimentDesign] = {
        design.id: design for design in investigation.designs
    }

    def observe(_index: int, template: ExperimentTemplateId) -> int:
        # The step index is BOED's; the seed comes from the investigation, which
        # counts steps itself.
        design = by_id[template]
        result = investigation.run(
            design, targets=() if targets is None else targets(investigation)
        )
        return design.template().outcome.cell_of(result.result)

    boed.plan(investigation.engine, tuple(by_id), observe, steps=steps)


def entertain(investigation: Investigation, library: Mapping[str, Defect]) -> None:
    """Propose every library structure the graph does not already hold.

    Shared by the two systems that draw on a fixed library -- V1, which
    entertains all of it, and B4, which entertains what it retrieved. A structure
    already present is skipped rather than re-proposed: SPEC §6.4 A18 makes a
    duplicate an error, and the null is present from the start in every
    investigation.

    Each proposal carries the predictions :func:`table_predictions` derives for
    it, one per design the scenario offers, so a system introduces structure
    without authoring any threshold that would refute it.
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
    designs: Sequence[ExperimentDesign],
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

    ``designs`` is the whole design space rather than one member of it, for the
    reason :func:`table_predictions` gives.
    """
    node_id = HypothesisId("null")
    return HypothesisGraph.empty(grammar, metrics).propose(
        node_id,
        program_edit=NULL_DEFECT,
        predictions=list(table_predictions(table, node_id, NULL_DEFECT, designs)),
        rationale="the null: nothing is wrong (SPEC §4.5 S9)",
    )


def table_predictions(
    table: EmpiricalTable,
    node_id: HypothesisId,
    program_edit: Defect,
    designs: Sequence[ExperimentDesign],
) -> tuple[Prediction, ...]:
    """Return what a structure predicts about every design that can carry one.

    A hypothesis is a statement about the whole design space, so attaching its
    falsifiability to one arbitrary member of that space was the actual error.
    Deriving one prediction per design is what makes SPEC §6.4 A16's guarantee
    hold in practice as well as on paper: a hypothesis was refutable before this,
    but only by an experiment nobody might run.

    Measured consequence, under A23. The verifier grades a claim carrying no
    effect by evaluating its subject's predictions against the cited experiments,
    and a prediction made under a design the system never ran bears on nothing,
    so the claim is *referred* -- correctly, since the verifier cannot decide it
    mechanically. All 180 of A23's referrals were that, all of them V1 on S5, S6
    and S10, which are the three scenarios where BOED does not select the first
    design. See ``docs/DECISIONS.md`` for the re-measured figure.

    A design whose template measures several diagnostics at once is **skipped**,
    not fatal: a :class:`~sciagent.core.types.Prediction` names one diagnostic
    by specification, so a joint cell has no representation as one, and
    :func:`_cell_condition` raises rather than quietly predicting a single axis
    of several. Skipping keeps such a design usable as an *experiment* while
    declining to invent a prediction for it.

    Raises :class:`~sciagent.core.errors.InvestigationError` if no design can
    carry a prediction at all, since a hypothesis with none is unfalsifiable and
    the graph would refuse it anyway -- better to say why here than to fail two
    frames later on an empty sequence.
    """
    derived = [
        table_prediction(table, node_id, program_edit, design)
        for design in designs
        if design.template().outcome.dimension == 1
    ]
    if not derived:
        raise InvestigationError(
            f"no design offered to hypothesis {node_id!r} measures a single "
            f"diagnostic, so no prediction can be derived and the hypothesis "
            f"would be unfalsifiable; designs were "
            f"{sorted(str(d.id) for d in designs)!r}"
        )
    return tuple(derived)


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
