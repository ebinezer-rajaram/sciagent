"""Integration test for the experiment DSL and executor (SPEC §11 item 7).

Item 7's gate is an integration test rather than a lettered acceptance criterion,
so this module lives outside ``tests/acceptance/``: CLAUDE.md's ``test_aN_*``
naming is what ``scripts/status.py`` derives gate coverage from, and a test named
for a gate it does not check would be counted as one.

What the gate has to show is that a design travels the whole way -- compiled by
the environment, executed against a defected programme, measured, addressed,
registered, charged -- and that the operation SPEC §4.2 rests the slice on
actually discriminates. :class:`TestForcedArrivalDiscriminates` is the
substantive one: if a forced arrival did not separate Hawkes self-excitation from
latent regime switching, backlog item 8's BOED would have nothing to select and
A24 would be measured over a design space with no discriminating experiment in
it.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest

from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import ARRIVAL, OBS, SIGN, SIZE
from environments.pointproc.grammar import edit_grammar
from environments.pointproc.operations import arrival_burst
from environments.pointproc.outcomes import (
    BURST_COUNT,
    BURST_OBSERVE,
    BURST_SPACING,
    DATA_VERSION,
    ENV_VERSION,
    N_EVENTS,
    closed_set,
    discretisation,
    executor,
    forced_design,
    simulator,
    slice_designs,
    slice_templates,
)
from environments.pointproc.program import reference_program
from sciagent.core.errors import (
    ClampError,
    MalformedDesignError,
    RegistryConflictError,
    RegistryError,
    UnknownOperationError,
)
from sciagent.core.types import ComponentId, Floats, FrozenDict, MetricName, Seed
from sciagent.experiments.dsl import (
    AblateComponent,
    CompareCandidates,
    ConditionOn,
    ExperimentDesign,
    ForceArrival,
    Operation,
    PerturbParameter,
    QueryDiagnostic,
    render,
    targets,
)
from sciagent.inference.binning import Discretisation, OutcomeSpace
from sciagent.registry.budget import Budget
from sciagent.registry.store import ExperimentStore

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _space(name: str) -> OutcomeSpace:
    return OutcomeSpace(axes=(discretisation(name),))


def _rate_space() -> OutcomeSpace:
    """Return the frozen outcome space of the forced-arrival design.

    Local to this module until backlog item 11, which declared the scenarios
    that use the design and therefore froze its edges in ``outcomes.py``. Read
    off the design rather than restated, so a test cannot measure through a
    discretisation the posterior is not calibrated on.
    """
    return forced_design().outcome


def _forced_design(observe: int = BURST_OBSERVE) -> ExperimentDesign:
    """Return the slice's forced-arrival design, optionally at another window."""
    if observe == BURST_OBSERVE:
        return forced_design()
    return ExperimentDesign(
        operation=ForceArrival(
            component=ARRIVAL,
            at=arrival_burst(BURST_COUNT, BURST_SPACING),
            observe=observe,
        ),
        outcome=_rate_space(),
        n_events=N_EVENTS,
    )


@pytest.fixture
def store() -> Iterator[ExperimentStore]:
    with ExperimentStore.in_memory() as opened:
        yield opened


# --------------------------------------------------------------------------
# Designs
# --------------------------------------------------------------------------


class TestDesignIdentity:
    """A design's id and config are stable, readable and injective."""

    def test_slice_design_ids_are_unchanged_by_the_dsl(self) -> None:
        """The four observational ids are the ones the table was always built on.

        ``EmpiricalTable.version`` hashes template ids, so a design that rendered
        differently from the hand-built template it replaced would invalidate a
        table that took minutes to build and, worse, would do it silently. The
        fifth id is item 11's forced arrival, which moves the version *on
        purpose*: it is a new design and the table has to be rebuilt to hold it.
        """
        assert [str(design.id) for design in slice_designs()] == [
            "query:count_autocorrelation_w2",
            "query:inter_arrival_dispersion",
            "query:phase_conditioned_dispersion",
            "query:size_dispersion",
            "force[arrival@0=0.01,1=0.02,2=0.03,3=0.04,4=0.05,5=0.06,6=0.07,"
            "7=0.08,8=0.09,9=0.1,10=0.11,11=0.12,12=0.13,13=0.14,14=0.15,"
            "15=0.16,16=0.17,17=0.18,18=0.19,19=0.2|20]:mean_rate",
        ]

    def test_templates_are_the_designs(self) -> None:
        designs = slice_designs()
        templates = slice_templates()
        assert [t.id for t in templates] == [d.id for d in designs]
        assert [t.outcome for t in templates] == [d.outcome for d in designs]
        assert [t.n_events for t in templates] == [d.n_events for d in designs]

    def test_distinct_operations_render_distinctly(self) -> None:
        operations: list[Operation] = [
            QueryDiagnostic(),
            PerturbParameter(component=ARRIVAL, parameter="rate", value=1.5),
            PerturbParameter(component=ARRIVAL, parameter="rate", value=2.5),
            PerturbParameter(component=SIZE, parameter="mean", value=1.5),
            ConditionOn(covariate=MetricName("phase"), low=0.0, high=0.5),
            ConditionOn(covariate=MetricName("phase"), low=0.0, high=0.6),
            ForceArrival(component=ARRIVAL, at=arrival_burst(3, 0.02), observe=10),
            ForceArrival(component=ARRIVAL, at=arrival_burst(3, 0.02), observe=20),
            ForceArrival(component=ARRIVAL, at=arrival_burst(4, 0.02), observe=10),
            AblateComponent(component=SIGN, value=1.0),
            AblateComponent(component=SIGN, value=0.0),
        ]
        rendered = [render(operation) for operation in operations]
        assert len(set(rendered)) == len(rendered), rendered

    def test_distinct_designs_have_distinct_configs(self) -> None:
        space = _space("inter_arrival_dispersion")
        one = ExperimentDesign(QueryDiagnostic(), space, N_EVENTS)
        two = ExperimentDesign(QueryDiagnostic(), space, N_EVENTS // 2)
        three = ExperimentDesign(
            PerturbParameter(component=ARRIVAL, parameter="rate", value=2.0),
            space,
            N_EVENTS,
        )
        configs = [dict(design.config()) for design in (one, two, three)]
        assert len({tuple(sorted(config.items())) for config in configs}) == 3

    @pytest.mark.parametrize("bad", ["arr:ival", "arr,ival", "arr[ival", "arr.ival"])
    def test_reserved_characters_are_refused(self, bad: str) -> None:
        """An id that could blur a delimiter is refused, never escaped."""
        with pytest.raises(MalformedDesignError, match="reserved character"):
            PerturbParameter(component=ComponentId(bad), parameter="rate", value=1.0)

    def test_a_design_must_run_for_a_positive_number_of_events(self) -> None:
        with pytest.raises(MalformedDesignError, match="positive number of events"):
            ExperimentDesign(QueryDiagnostic(), _space("size_dispersion"), 0)

    def test_targets_are_empty_for_observational_operations(self) -> None:
        assert targets(QueryDiagnostic()) == frozenset()
        assert targets(ConditionOn(MetricName("phase"), 0.0, 0.5)) == frozenset()
        assert targets(AblateComponent(component=SIGN, value=1.0)) == frozenset({SIGN})


# --------------------------------------------------------------------------
# Clamping (core/program.py)
# --------------------------------------------------------------------------


class TestClamp:
    """``execute(..., clamps=...)`` is ``do(X = x)`` and nothing more."""

    def test_no_clamps_is_the_unclamped_programme(self) -> None:
        program = reference_program()
        assert program.execute(Seed(7), 64) == program.execute(Seed(7), 64, clamps=None)
        assert program.execute(Seed(7), 64) == program.execute(Seed(7), 64, clamps={})

    def test_a_clamp_forces_exactly_the_named_indices(self) -> None:
        program = reference_program()
        held = program.execute(Seed(3), 32, clamps={SIGN: {0: 1.0, 5: 1.0, 9: 1.0}})
        assert [held.values[SIGN][i] for i in (0, 5, 9)] == [1.0, 1.0, 1.0]

    def test_a_clamp_does_not_perturb_another_component(self) -> None:
        """Streams are derived by name, so clamping one cannot reach another.

        This is the whole of why a clamp is determinism-safe. Note what is *not*
        claimed: the clamped component's own later values do shift, because a
        clamped index does not advance its stream. That is the recorded choice --
        skip the draw rather than draw and discard -- and it is why a
        forced-arrival effect is measured across replicates and never pairwise.
        """
        program = reference_program()
        free = program.execute(Seed(3), 32)
        held = program.execute(Seed(3), 32, clamps={SIGN: {0: 1.0, 5: 1.0, 9: 1.0}})
        assert np.array_equal(held.values[SIZE], free.values[SIZE])
        assert np.array_equal(held.values[ARRIVAL], free.values[ARRIVAL])
        # ``obs`` reads both, so it moves with the sign. That is the collateral
        # effect the DAG predicts, not a leak.
        assert not np.array_equal(held.values[OBS], free.values[OBS])

    def test_clamping_is_deterministic(self) -> None:
        program = reference_program()
        clamps = {SIGN: {0: 1.0, 1: 0.0}}
        first = program.execute(Seed(11), 48, clamps=clamps)
        second = program.execute(Seed(11), 48, clamps=clamps)
        assert first.to_bytes() == second.to_bytes()

    def test_a_clamp_propagates_to_descendants(self) -> None:
        """``obs = size * (2*sign - 1)``, so forcing the sign flips the sign."""
        program = reference_program()
        held = program.execute(Seed(5), 16, clamps={SIGN: {i: 0.0 for i in range(16)}})
        assert np.all(held.values[OBS] < 0.0)

    @pytest.mark.parametrize(
        ("clamps", "message"),
        [
            ({ComponentId("nope"): {0: 1.0}}, "unknown component"),
            ({SIGN: {}}, "forces no event"),
            ({SIGN: {99: 1.0}}, "outside the"),
            ({SIGN: {0: float("nan")}}, "non-finite"),
        ],
    )
    def test_a_malformed_clamp_is_refused_before_execution(
        self, clamps: dict[ComponentId, dict[int, float]], message: str
    ) -> None:
        with pytest.raises(ClampError, match=message):
            reference_program().execute(Seed(1), 16, clamps=clamps)


# --------------------------------------------------------------------------
# Execution and registration
# --------------------------------------------------------------------------


class TestExecutorRegisters:
    """``run`` turns an execution into a registered, addressed, charged experiment."""

    def test_a_run_registers_at_the_address_of_its_key(
        self, store: ExperimentStore
    ) -> None:
        runner = executor(store=store)
        design = ExperimentDesign(
            QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
        )
        result = runner.run(design, closed_set()["hawkes"], Seed(4))
        assert result.record.digest == result.record.key.digest
        assert str(result.experiment) == str(result.record.digest)
        assert store.get(result.record.digest) == result.record

    def test_a_faithful_rerun_is_the_same_row(self, store: ExperimentStore) -> None:
        """A15 in miniature, at the executor rather than at the store."""
        design = ExperimentDesign(
            QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
        )
        defect = closed_set()["seasonality"]
        first = executor(store=store).run(design, defect, Seed(9))
        second = executor(store=store).run(design, defect, Seed(9))
        assert first.record.digest == second.record.digest
        assert first.result == second.result
        assert first.record.sequence == second.record.sequence

    def test_the_defect_is_part_of_the_address(self, store: ExperimentStore) -> None:
        runner = executor(store=store)
        design = ExperimentDesign(
            QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
        )
        hawkes = runner.run(design, closed_set()["hawkes"], Seed(2))
        null = runner.run(design, closed_set()["null"], Seed(2))
        assert hawkes.record.digest != null.record.digest

    def test_collateral_is_derived_from_the_dag(self, store: ExperimentStore) -> None:
        """SPEC §3.3: collateral is derived, never declared.

        ``size`` reaches ``obs`` and nothing else in the reference programme, so
        ablating it licenses a total-effect claim over ``{obs}`` and no narrower
        one.
        """
        runner = executor(store=store)
        design = ExperimentDesign(
            AblateComponent(component=SIZE, value=1.0),
            _space("size_dispersion"),
            N_EVENTS,
        )
        result = runner.run(design, closed_set()["null"], Seed(1))
        assert result.manipulated == frozenset({SIZE})
        assert result.collateral == frozenset({OBS})
        assert dict(result.record.key.config)["collateral"] == "obs"

    def test_ablation_holds_the_component_fixed(self, store: ExperimentStore) -> None:
        """SPEC §7.2: held fixed in the executed experiment, not merely declared."""
        runner = executor(store=store)
        design = ExperimentDesign(
            AblateComponent(component=SIZE, value=1.0),
            _space("size_dispersion"),
            N_EVENTS,
        )
        result = runner.run(design, closed_set()["null"], Seed(1))
        assert result.result == (0.0,)  # a constant has zero dispersion

    def test_a_budget_is_charged_and_can_be_exhausted(
        self, store: ExperimentStore
    ) -> None:
        runner = executor(store=store, budget=Budget(total=2.0))
        design = ExperimentDesign(
            QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
        )
        defect = closed_set()["null"]
        assert runner.run(design, defect, Seed(1)).budget.spent == 1.0
        assert runner.run(design, defect, Seed(2)).budget.spent == 2.0
        with pytest.raises(Exception, match="exceeds the remaining budget"):
            runner.run(design, defect, Seed(3))

    def test_running_without_a_registry_is_refused(self) -> None:
        """An unregistered result is not an experiment and must not claim to be."""
        design = ExperimentDesign(
            QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
        )
        with pytest.raises(RegistryError, match="holds no registry"):
            executor().run(design, closed_set()["null"], Seed(1))

    def test_compare_candidates_is_refused_by_name(
        self, store: ExperimentStore
    ) -> None:
        """Refused permanently, not pending. Backlog item 8 settled it.

        ``CompareCandidates`` scores candidate defects against each other, which
        is answered by :func:`sciagent.experiments.boed.compare` as *selection*.
        It measures nothing, so there is no result to register and no budget to
        charge, and the executor path it lacks is one it will never acquire.
        """
        design = ExperimentDesign(
            CompareCandidates(
                candidates=(closed_set()["hawkes"], closed_set()["seasonality"])
            ),
            _space("inter_arrival_dispersion"),
            N_EVENTS,
        )
        with pytest.raises(UnknownOperationError, match="measures nothing"):
            executor(store=store).run(design, closed_set()["null"], Seed(1))

    def test_an_unperformable_operation_is_refused(self) -> None:
        """Refused loudly rather than approximated by something adjacent."""
        runner = executor()
        design = ExperimentDesign(
            AblateComponent(component=ARRIVAL, value=1.0),
            _space("inter_arrival_dispersion"),
            N_EVENTS,
        )
        with pytest.raises(UnknownOperationError, match="absolute times"):
            runner.measure(design, closed_set()["null"], Seed(1))

        unknown = ExperimentDesign(
            PerturbParameter(component=ARRIVAL, parameter="nonesuch", value=1.0),
            _space("inter_arrival_dispersion"),
            N_EVENTS,
        )
        with pytest.raises(UnknownOperationError, match="does not declare"):
            runner.measure(unknown, closed_set()["null"], Seed(1))

    def test_a_mid_run_arrival_clamp_is_refused(self) -> None:
        """Arrival times must ascend, and a mid-run clamp cannot promise it."""
        runner = executor()
        design = ExperimentDesign(
            ForceArrival(
                component=ARRIVAL,
                at=FrozenDict[int, float]({100: 0.5, 101: 0.6}),
                observe=10,
            ),
            _rate_space(),
            N_EVENTS,
        )
        with pytest.raises(MalformedDesignError, match="non-prefix"):
            runner.measure(design, closed_set()["null"], Seed(1))


class TestOperationsChangeWhatIsMeasured:
    """Each operation does something, and something different from the others."""

    def test_perturbing_the_rate_moves_the_rate(self) -> None:
        runner = executor()
        space = OutcomeSpace(
            axes=(
                Discretisation(
                    metric=metric_registry().spec("mean_rate").ref,
                    interior=(1.0, 3.0),
                    low=0.0,
                    high=float("inf"),
                ),
            )
        )
        null = closed_set()["null"]
        base = runner.measure(
            ExperimentDesign(QueryDiagnostic(), space, N_EVENTS), null, Seed(1)
        )
        doubled = runner.measure(
            ExperimentDesign(
                PerturbParameter(component=ARRIVAL, parameter="rate", value=2.0),
                space,
                N_EVENTS,
            ),
            null,
            Seed(1),
        )
        assert base[0] == pytest.approx(1.0, abs=0.15)
        assert doubled[0] == pytest.approx(2.0, abs=0.30)

    def test_conditioning_on_phase_selects_a_subset(self) -> None:
        """``ConditionOn`` restricts the log; it does not change the programme."""
        runner = executor()
        space = _space("size_dispersion")
        seasonality = closed_set()["seasonality"]
        whole = runner.measure(
            ExperimentDesign(QueryDiagnostic(), space, N_EVENTS), seasonality, Seed(6)
        )
        half = runner.measure(
            ExperimentDesign(
                ConditionOn(covariate=MetricName("phase"), low=0.0, high=0.5),
                space,
                N_EVENTS,
            ),
            seasonality,
            Seed(6),
        )
        # Same programme, fewer events: the mark distribution is unchanged in
        # expectation but the estimate is not the whole-run one.
        assert whole != half
        assert half[0] == pytest.approx(whole[0], abs=0.6)

    def test_an_unobservable_covariate_is_refused(self) -> None:
        runner = executor()
        design = ExperimentDesign(
            ConditionOn(covariate=MetricName("regime"), low=0.0, high=0.5),
            _space("size_dispersion"),
            N_EVENTS,
        )
        with pytest.raises(UnknownOperationError, match="not observable"):
            runner.measure(design, closed_set()["null"], Seed(1))


# --------------------------------------------------------------------------
# The posterior engine's path is the executor's path
# --------------------------------------------------------------------------


class TestSimulatorSharesTheExecutionPath:
    """The engine's likelihood and the observation it scores come from one route."""

    def test_the_simulator_agrees_with_a_direct_execution(self) -> None:
        """Observational designs only: a manipulated one has no direct twin.

        The forced arrival clamps a prefix of the run and reads the events after
        it, which is precisely what the compiler exists to do, so reproducing it
        here would mean restating the compiler and testing it against itself.
        :meth:`test_the_forced_design_is_not_a_plain_execution` covers that side.
        """
        grammar = edit_grammar()
        registry = metric_registry()
        simulate = simulator()
        observational = [
            design.template()
            for design in slice_designs()
            if isinstance(design.operation, QueryDiagnostic)
        ]
        assert len(observational) == len(slice_templates()) - 1
        for name, defect in sorted(closed_set().items()):
            program = grammar.apply(reference_program(), defect)
            for template in observational:
                log = program.execute(Seed(21), template.n_events)
                direct = tuple(
                    registry.spec(str(metric.name)).compute(log)
                    for metric in template.outcome.metrics
                )
                assert simulate(defect, template, Seed(21)) == direct, name

    def test_the_forced_design_is_not_a_plain_execution(self) -> None:
        """The manipulation reaches the measurement, on the engine's own path.

        Under Hawkes the post-burst rate is many times the run's average rate,
        which is the whole content of SPEC §4.2's stage 3. If the simulator ever
        returned the unmanipulated value here, every likelihood the engine
        computed for the intervention would be the likelihood of an experiment
        nobody performed.
        """
        template = forced_design().template()
        defect = closed_set()["hawkes"]
        program = edit_grammar().apply(reference_program(), defect)
        log = program.execute(Seed(21), template.n_events)
        unmanipulated = metric_registry().spec("mean_rate").compute(log)
        assert simulator()(defect, template, Seed(21))[0] > 4.0 * unmanipulated

    def test_the_simulator_registers_nothing(self, store: ExperimentStore) -> None:
        """Table-building executions are the engine's arithmetic, not experiments."""
        runner = executor(store=store)
        simulate = runner.simulator(slice_designs())
        for template in slice_templates():
            simulate(closed_set()["hawkes"], template, Seed(1))
        assert store.count() == 0

    def test_an_unknown_template_is_refused(self) -> None:
        simulate = executor().simulator(slice_designs()[:1])
        with pytest.raises(UnknownOperationError, match="no design for template"):
            simulate(closed_set()["null"], slice_templates()[-1], Seed(1))


# --------------------------------------------------------------------------
# The substantive gate
# --------------------------------------------------------------------------


class TestForcedArrivalDiscriminates:
    """SPEC §4.2: a forced arrival separates Hawkes from everything else.

    This is the operation the slice rests on. Hawkes self-excitation and latent
    regime switching are calibrated to be indistinguishable under *every*
    dispersion diagnostic -- ``docs/DECISIONS.md`` records the measurement -- so
    if forcing does not separate them, nothing does: stage 3 of SPEC §4.2's
    minimum discriminating plan has no experiment, scenario S10's
    non-identifiability is unconditional rather than budget-bound, and item 8's
    BOED would be choosing from a space with no discriminating design in it.
    """

    REPLICATES = 50

    def _samples(self, name: str, design: ExperimentDesign) -> Floats:
        runner = executor()
        defect = closed_set()[name]
        return np.array(
            [
                runner.measure(design, defect, Seed(seed))[0]
                for seed in range(self.REPLICATES)
            ]
        )

    def test_forcing_separates_hawkes_from_regime_switching(self) -> None:
        design = _forced_design()
        hawkes = self._samples("hawkes", design)
        regime = self._samples("regime_switching", design)
        auc = float((hawkes[:, None] > regime[None, :]).mean())
        assert auc > 0.90, f"AUC {auc:.3f}: forcing no longer discriminates"
        assert float(np.median(hawkes)) > 4.0 * float(np.median(regime))

    def test_forcing_leaves_the_unexcited_mechanisms_alone(self) -> None:
        """A response under a mechanism that has none would be an artefact."""
        design = _forced_design()
        for name in ("null", "seasonality", "poisson_mixture", "regime_switching"):
            samples = self._samples(name, design)
            assert float(np.median(samples)) < 2.0, name

    def test_no_dispersion_diagnostic_separates_the_same_pair(self) -> None:
        """The contrast that makes the forced arrival worth having.

        Under observation alone the two mechanisms sit on top of each other, and
        that is the calibrated design of SPEC §4.2 rather than a weakness of the
        diagnostic.
        """
        runner = executor()
        design = ExperimentDesign(
            QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
        )
        hawkes = np.array(
            [
                runner.measure(design, closed_set()["hawkes"], Seed(s))[0]
                for s in range(self.REPLICATES)
            ]
        )
        regime = np.array(
            [
                runner.measure(design, closed_set()["regime_switching"], Seed(s))[0]
                for s in range(self.REPLICATES)
            ]
        )
        auc = float((hawkes[:, None] > regime[None, :]).mean())
        assert 0.35 < auc < 0.65, f"AUC {auc:.3f}: the pair is no longer confounded"


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------


def test_env_and_data_versions_enter_the_address(store: ExperimentStore) -> None:
    """A13: any change to a versioned input changes the content address."""
    design = ExperimentDesign(
        QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
    )
    result = executor(store=store).run(design, closed_set()["null"], Seed(1))
    key = result.record.key
    assert key.env_version == ENV_VERSION
    assert key.data_version == DATA_VERSION
    assert key.metric_version == metric_registry().version


def test_a_disagreeing_result_at_one_address_is_a_conflict(
    store: ExperimentStore,
) -> None:
    """A framework bug, never a finding (SPEC §6.3 A15).

    The executor's address covers the design, the defect, the versions and the
    seed. If an execution at that address ever returned something else, the
    address would not cover everything that determined it -- so the store must
    refuse the second value rather than record two.
    """
    design = ExperimentDesign(
        QueryDiagnostic(), _space("inter_arrival_dispersion"), N_EVENTS
    )
    result = executor(store=store).run(design, closed_set()["null"], Seed(1))
    with pytest.raises(RegistryConflictError):
        store.append(
            result.record.key,
            partition=result.record.partition,
            result=(result.result[0] + 1.0,),
        )
