"""Claims, evidence and a programme to judge them against, shared by two suites.

``tests/acceptance/test_a19_a23.py`` and ``tests/test_verify.py`` both need a
programme with a real DAG, an evidence index, a hypothesis graph and a claim that
verifies clean. Building that twice would let the two suites drift into testing
different things under the same names.

Two kinds of evidence live here, deliberately.

:func:`registered_world` executes and registers real experiments, so the figures
in :func:`supported_claim` are ones the registry actually holds. A19 corrupts
those figures, and a corruption is only meaningful against a number that came
from somewhere.

:func:`record` constructs an :class:`~sciagent.verify.relevance.EvidenceRecord`
directly, without executing anything. A20 and A21 are stated over *constructed*
cases -- "100 constructed cases", "100 constructed intervention/estimand pairs"
-- and constructing them is also the only way to reach estimands the slice's
one-operation-per-design DSL cannot produce in a single experiment. A controlled
direct effect needs a component manipulated and another held fixed at once, and
:class:`~sciagent.experiments.dsl.AblateComponent` holds exactly one component
fixed and manipulates nothing else. That limitation is real and is recorded in
``docs/DECISIONS.md``; it is a fact about the slice's operation set, not about
the licensing rule, so the licensing rule is tested over records that exhibit it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from environments.pointproc import edit_grammar, reference_program
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import ARRIVAL, OBS, SIGN, SIZE
from environments.pointproc.operations import arrival_burst
from environments.pointproc.outcomes import discretisation, executor
from sciagent.core.conditions import Between, Not
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    AssumptionCode,
    Claim,
    ClaimId,
    ClaimModality,
    ClaimPartition,
    ClaimStrength,
    ComponentId,
    Direction,
    EffectEstimate,
    EnvVersion,
    Estimand,
    ExperimentId,
    ExperimentTemplateId,
    FamilyId,
    FrozenDict,
    GrammarVersion,
    HypothesisId,
    Intervention,
    MetricName,
    MetricVersion,
    Prediction,
    PredictionId,
    Scope,
    Seed,
    SubjectKind,
    TotalEffect,
    Uniqueness,
)
from sciagent.experiments.dsl import (
    ExperimentDesign,
    ForceArrival,
    QueryDiagnostic,
)
from sciagent.experiments.executor import ObservedExecution
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.binning import OutcomeSpace
from sciagent.registry.store import ExperimentStore
from sciagent.verify.numerical import recompute
from sciagent.verify.relevance import EvidenceIndex, EvidenceRecord

#: The metric every constructed record is measured on unless told otherwise.
METRIC = MetricName("inter_arrival_dispersion")

#: Run length of the registered experiments. Short: A19 needs the figures to be
#: real, not to be precise, and 256 events keeps the whole gate under a second.
N_EVENTS = 256

#: How many seeds each arm of the registered effect is measured over. Two, so a
#: claim of strength ``establishes`` -- which SPEC §6.5 does not define but this
#: repository reads as needing more than a single experiment -- is constructible.
REPLICATES = 3


def scope(
    *,
    families: frozenset[FamilyId] | None = None,
    parameters: Mapping[str, tuple[float, float]] | None = None,
    env_version: str = "pointproc/1",
    metric_version: str | None = None,
    grammar_version: str | None = None,
) -> Scope:
    """Return a scope, defaulting to the slice's own versions."""
    return Scope(
        families=frozenset({FamilyId("poisson_homogeneous")})
        if families is None
        else families,
        parameters=FrozenDict[str, tuple[float, float]](
            {"rate": (0.5, 2.0)} if parameters is None else parameters
        ),
        env_version=EnvVersion(env_version),
        metric_version=MetricVersion(
            str(metric_registry().version) if metric_version is None else metric_version
        ),
        grammar_version=GrammarVersion(
            str(edit_grammar().version) if grammar_version is None else grammar_version
        ),
    )


def record(
    name: str,
    *,
    sequence: int,
    value: float = 1.0,
    metric: MetricName = METRIC,
    template: str = "query:inter_arrival_dispersion",
    manipulated: frozenset[ComponentId] = frozenset(),
    collateral: frozenset[ComponentId] = frozenset(),
    held_fixed: frozenset[ComponentId] = frozenset(),
    targets: Sequence[HypothesisId] = (),
    evidence_scope: Scope | None = None,
) -> EvidenceRecord:
    """Return one constructed evidence record. Nothing is executed."""
    return EvidenceRecord(
        experiment=ExperimentId(name),
        template=ExperimentTemplateId(template),
        metrics=(metric,),
        result=(value,),
        manipulated=manipulated,
        collateral=collateral,
        held_fixed=held_fixed,
        targets=tuple(targets),
        scope=scope() if evidence_scope is None else evidence_scope,
        sequence=sequence,
    )


def index(*records: EvidenceRecord) -> EvidenceIndex:
    """Return an evidence index over constructed records."""
    return EvidenceIndex.of(records)


def claim(
    *,
    subject: str = "hawkes",
    subject_kind: SubjectKind = "hypothesis",
    modality: ClaimModality = "correlational",
    estimand: Estimand | None = None,
    strength: ClaimStrength = "suggests",
    evidence: Sequence[str] = (),
    partition: ClaimPartition = "exploratory",
    effect: EffectEstimate | None = None,
    uniqueness: Uniqueness = "non_exclusive",
    intervention: Intervention | None = None,
    claim_scope: Scope | None = None,
    claim_id: str = "C1",
) -> Claim:
    """Return a claim assembled from keyword parts, with workable defaults."""
    return Claim(
        id=ClaimId(claim_id),
        subject=HypothesisId(subject),
        subject_kind=subject_kind,
        modality=modality,
        estimand=estimand,
        strength=strength,
        scope=scope() if claim_scope is None else claim_scope,
        evidence=tuple(ExperimentId(name) for name in evidence),
        partition=partition,
        effect=effect,
        uniqueness=uniqueness,
        prose="",
        intervention=intervention,
    )


def intervention(
    estimand: Estimand,
    *,
    manipulated: frozenset[ComponentId] | None = None,
    collateral: frozenset[ComponentId] = frozenset(),
    assumptions: Sequence[AssumptionCode] = (),
    expected: Direction = Direction.INCREASE,
) -> Intervention:
    """Return an intervention declaration for ``estimand``."""
    return Intervention(
        target=estimand.target,
        manipulated=frozenset({estimand.target})
        if manipulated is None
        else manipulated,
        estimand=estimand,
        collateral=collateral,
        assumptions=tuple(assumptions),
        expected_direction=expected,
    )


# --------------------------------------------------------------------------
# A hypothesis graph with relations and predictions
# --------------------------------------------------------------------------


def graph_of(
    structures: Mapping[str, Defect],
    *,
    grammar: EditGrammar | None = None,
    late: Sequence[str] = (),
    proposed_at: ExperimentId | None = None,
) -> HypothesisGraph:
    """Return a graph over ``structures``, with a prediction on each.

    ``late`` names the hypotheses introduced mid-investigation, which is what
    SPEC F9 and acceptance test A22 turn on. Their ``proposed_at`` is
    ``proposed_at``; everything else was present from the start.
    """
    metrics = metric_registry()
    built = HypothesisGraph.empty(
        grammar if grammar is not None else edit_grammar(), metrics
    )
    spec = metrics.spec(str(METRIC))
    for name in sorted(structures):
        node_id = HypothesisId(name)
        condition = Between(low=spec.low, high=2.0, low_closed=True, high_closed=False)
        built = built.propose(
            node_id,
            program_edit=structures[name],
            predictions=[
                Prediction(
                    id=PredictionId(f"{name}/{METRIC}"),
                    hypothesis_id=node_id,
                    diagnostic=spec.ref,
                    condition=condition,
                    under=ExperimentTemplateId("query:inter_arrival_dispersion"),
                    refutation=Not(condition),
                )
            ],
            rationale=f"structure {name!r}",
            proposed_at=proposed_at if name in late else None,
        )
    return built


# --------------------------------------------------------------------------
# A world with real registered experiments
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisteredWorld:
    """Executed, registered experiments and everything needed to judge them."""

    program: GenerativeProgram
    store: ExperimentStore
    history: tuple[ObservedExecution, ...]
    evidence: EvidenceIndex
    graph: HypothesisGraph

    @property
    def treated(self) -> tuple[ExperimentId, ...]:
        """Return the ids of the experiments that intervened."""
        return tuple(result.experiment for result in self.history if result.manipulated)

    @property
    def control(self) -> tuple[ExperimentId, ...]:
        """Return the ids of the experiments that only observed."""
        return tuple(
            result.experiment for result in self.history if not result.manipulated
        )

    @property
    def cited(self) -> tuple[ExperimentId, ...]:
        """Return every registered experiment, which is what a claim must cite."""
        return tuple(result.experiment for result in self.history)


def registered_world(structures: Mapping[str, Defect] | None = None) -> RegisteredWorld:
    """Return a world whose experiments were really executed and registered.

    One observational arm and one interventional arm, each over
    :data:`REPLICATES` seeds, both read on :data:`METRIC`. That is the smallest
    shape a difference-of-means effect can be recomputed from, and A19's whole
    subject is whether the verifier recomputes it.
    """
    store = ExperimentStore.in_memory()
    runner = executor(store=store)
    space = OutcomeSpace(axes=(discretisation(str(METRIC)),))
    designs = (
        ExperimentDesign(operation=QueryDiagnostic(), outcome=space, n_events=N_EVENTS),
        ExperimentDesign(
            operation=ForceArrival(ARRIVAL, arrival_burst(4, 0.01), observe=64),
            outcome=space,
            n_events=N_EVENTS,
        ),
    )
    # Projected, as an Investigation projects what it hands a system (gate A41):
    # the verifier is given what a system could have cited, not the executor's
    # own truth-bearing record.
    history = [
        runner.run(design, frozenset(), Seed(1000 + replicate)).observed()
        for design in designs
        for replicate in range(REPLICATES)
    ]
    return RegisteredWorld(
        program=reference_program(),
        store=store,
        history=tuple(history),
        evidence=EvidenceIndex.from_history(history, scope=scope()),
        graph=graph_of(structures if structures is not None else {"null": frozenset()}),
    )


def supported_claim(world: RegisteredWorld) -> Claim:
    """Return a claim whose every figure the registry can reproduce.

    The effect is not authored here: :func:`~sciagent.verify.numerical.recompute`
    derives it from the cited rows, which is the only way an
    :class:`~sciagent.core.types.EffectEstimate` is ever produced (SPEC F7).
    """
    estimand = TotalEffect(ARRIVAL, SIZE)
    draft = claim(
        subject="null",
        modality="causal",
        estimand=estimand,
        # ``suggests`` and not ``supports``, because the measurement is what it
        # is: forcing four arrivals into a 256-event run moves the size
        # component's dispersion by well under its own standard error, so the
        # recomputed interval spans zero and the stronger strength is refused.
        # A claim built to verify clean has to be a claim the evidence actually
        # reaches, and picking the strength to fit the measurement is the
        # direction of fitting that is allowed.
        strength="suggests",
        evidence=[str(name) for name in world.cited],
        effect=None,
        intervention=intervention(estimand, collateral=frozenset({SIZE, SIGN, OBS})),
    )
    derived = recompute(draft, world.evidence, metric=METRIC)
    assert derived is not None, "the registered world must yield a recomputable effect"
    return replace(draft, effect=derived)


__all__ = [
    "ARRIVAL",
    "METRIC",
    "N_EVENTS",
    "OBS",
    "REPLICATES",
    "SIGN",
    "SIZE",
    "RegisteredWorld",
    "claim",
    "graph_of",
    "index",
    "intervention",
    "record",
    "registered_world",
    "scope",
    "supported_claim",
]
