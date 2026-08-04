"""The systems layer: what an investigation permits, and what it refuses.

The load-bearing tests here are the ones about SPEC's second invariant. A
research system is exactly the place that invariant is aimed at, so "no
agent-reachable path may set a posterior value" has to be checked against a
system that tries, not merely asserted about one that does not.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, slice_table

from environments.pointproc.operations import arrival_burst
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import (
    BudgetExhaustedError,
    DiagnosisError,
    InvestigationError,
)
from sciagent.core.types import (
    ComponentId,
    Diagnosis,
    FrozenDict,
    HypothesisId,
    Probability,
    ScenarioId,
)
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.eval.scenarios import Scenario
from sciagent.eval.scoring import closed_world_score
from sciagent.experiments.dsl import ExperimentDesign, ForceArrival
from sciagent.experiments.executor import Executor
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import (
    Investigation,
    ResearchSystem,
    entertain,
    null_seeded_graph,
)
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly
from sciagent.systems.baselines.retrieval import Retrieval


class _Harness(NamedTuple):
    """Everything one run needs, built the way the gate builds it."""

    scenario: Scenario
    executor: Executor
    engine: EmpiricalTableEngine
    graph: HypothesisGraph


def _harness(scenario_id: str = "S1") -> _Harness:
    """Return a scenario and the three objects a run is performed against."""
    the_scenario = scenario(scenario_id)
    table = slice_table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs()[0])
    return _Harness(
        scenario=the_scenario,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR)),
        graph=graph,
    )


def _run(system: ResearchSystem, scenario_id: str = "S1") -> ScenarioRun:
    """Run one system on one scenario through the real harness."""
    harness = _harness(scenario_id)
    return run_scenario(
        harness.scenario,
        system,
        executor=harness.executor,
        engine=harness.engine,
        graph=harness.graph,
    )


class TestTheFrameworkWritesTheNumbers:
    """SPEC §1, second invariant, enforced rather than documented."""

    def test_a_system_that_fabricates_a_posterior_is_refused(self) -> None:
        """The whole point. A plausible number nobody audits is the failure mode."""

        class Liar:
            name = "liar"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                # Entertain the closed set first, so the honest posterior is
                # spread over five structures and a claim of certainty is a
                # genuine fabrication rather than a coincidence.
                entertain(investigation, closed_set())
                investigation.run(investigation.designs[0])
                honest = investigation.conclude()
                assert len(honest.distribution) > 1
                return Diagnosis(
                    scenario_id=honest.scenario_id,
                    distribution=FrozenDict[HypothesisId, Probability](
                        {HypothesisId("hawkes"): Probability(1.0)}
                    ),
                    abstain_mass=Probability(0.0),
                    null_mass=Probability(0.0),
                    proposed_edits=honest.proposed_edits,
                    supporting=honest.supporting,
                    residual_candidates=(),
                )

        with pytest.raises(InvestigationError, match="no system may author a number"):
            _run(Liar())

    def test_a_system_that_inflates_only_its_abstain_mass_is_refused(self) -> None:
        """Every field is compared, not just the distribution."""

        class Hedger:
            name = "hedger"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                investigation.run(investigation.designs[0])
                honest = investigation.conclude()
                return Diagnosis(
                    scenario_id=honest.scenario_id,
                    distribution=honest.distribution,
                    abstain_mass=Probability(0.99),
                    null_mass=honest.null_mass,
                    proposed_edits=honest.proposed_edits,
                    supporting=honest.supporting,
                    residual_candidates=(),
                )

        with pytest.raises(InvestigationError, match="abstain_mass"):
            _run(Hedger())

    def test_an_unnormalised_diagnosis_cannot_even_be_built(self) -> None:
        with pytest.raises(DiagnosisError, match="normalised distribution"):
            Diagnosis(
                scenario_id=ScenarioId("S1"),
                distribution=FrozenDict[HypothesisId, Probability](
                    {HypothesisId("a"): Probability(0.3)}
                ),
                abstain_mass=Probability(0.0),
                null_mass=Probability(0.0),
                proposed_edits=FrozenDict(),
                supporting=FrozenDict(),
                residual_candidates=(),
            )

    def test_an_honest_system_passes_the_same_audit(self) -> None:
        """The check must not be one no real system can satisfy."""
        assert _run(PPCOnly()).system == "B1"


class TestWhatAnInvestigationRefuses:
    """The boundary a system is held to."""

    def test_the_ground_truth_has_no_public_accessor(self) -> None:
        """A system runs designs *against* the truth without being able to read it."""
        harness = _harness()
        truth = closed_set()["hawkes"]
        investigation = Investigation(
            scenario_id=ScenarioId("S1"),
            designs=slice_designs(),
            truth=truth,
            executor=harness.executor,
            engine=harness.engine,
            graph=harness.graph,
            seed=slice_table().seed,
        )
        public = [name for name in dir(investigation) if not name.startswith("_")]
        assert "truth" not in public
        for name in public:
            assert getattr(investigation, name, None) != truth

    def test_a_design_the_scenario_does_not_offer_is_refused(self) -> None:
        class Trespasser:
            name = "trespasser"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                intruder = ExperimentDesign(
                    operation=ForceArrival(
                        component=ComponentId("arrival"),
                        at=arrival_burst(count=20, spacing=0.05),
                        observe=20,
                    ),
                    outcome=investigation.designs[0].outcome,
                    n_events=512,
                )
                investigation.run(intruder)
                return investigation.conclude()

        with pytest.raises(InvestigationError, match="does not offer"):
            _run(Trespasser())

    def test_overspending_raises_rather_than_being_clamped(self) -> None:
        class Spendthrift:
            name = "spendthrift"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                for _ in range(int(investigation.budget.total) + 1):
                    investigation.run(investigation.designs[0])
                return investigation.conclude()

        with pytest.raises(BudgetExhaustedError):
            _run(Spendthrift())


class TestTheBaselinesDoWhatSpecFiveSays:
    """Each baseline's defining property, as SPEC §5's table states it."""

    def test_b1_proposes_nothing(self) -> None:
        """SPEC §5: "PPC-only detector | Stage A floor; proposes nothing"."""
        run = _run(PPCOnly())
        assert run.proposed == {}
        assert run.diagnosis.proposed_edits == {}

    def test_v1_entertains_the_whole_closed_set_and_adds_nothing(self) -> None:
        """SPEC §5: "BOED-only over the closed set ... no representation change"."""
        run = _run(BOEDOnly(closed_set()))
        assert set(run.diagnosis.distribution) == set(closed_set())

    def test_b4_proposes_only_library_structures(self) -> None:
        run = _run(Retrieval(closed_set()))
        library = {frozenset(edit) for edit in closed_set().values()}
        for edit in run.proposed.values():
            assert frozenset(edit) in library

    def test_b4_shortlists_at_most_what_it_was_asked_for(self) -> None:
        run = _run(Retrieval(closed_set(), shortlist=1))
        # The null is seeded, so a one-entry shortlist adds at most one more.
        assert len(run.proposed) <= 1

    def test_a_shortlist_of_zero_is_refused(self) -> None:
        with pytest.raises(InvestigationError, match="at least one"):
            Retrieval(closed_set(), shortlist=0)

    def test_every_baseline_satisfies_the_protocol(self) -> None:
        for system in (PPCOnly(), BOEDOnly(closed_set()), Retrieval(closed_set())):
            assert isinstance(system, ResearchSystem)


class TestDeterminism:
    """SPEC's third invariant, at the level of a whole investigation."""

    def test_two_runs_of_one_system_agree_exactly(self) -> None:
        first, second = _run(PPCOnly()), _run(PPCOnly())
        assert first.diagnosis.distribution == second.diagnosis.distribution
        assert [e for e in first.diagnosis.supporting] == [
            e for e in second.diagnosis.supporting
        ]

    def test_two_scenarios_sharing_a_truth_are_not_the_same_run(self) -> None:
        """S1 and S5 are both Hawkes; distinct seeds must make them distinct."""
        s1, s5 = _run(BOEDOnly(closed_set()), "S1"), _run(BOEDOnly(closed_set()), "S5")
        assert s1.diagnosis.distribution != s5.diagnosis.distribution


class TestClosedWorldScoring:
    """SPEC §8's closed-world case."""

    def test_certainty_in_the_truth_scores_zero_bits(self) -> None:
        edits = {HypothesisId("h"): closed_set()["hawkes"]}
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S1"),
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId("h"): Probability(1.0)}
            ),
            abstain_mass=Probability(0.0),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        score = closed_world_score(diagnosis, closed_set()["hawkes"], edits)
        assert score.log_score == 0.0
        assert score.identified

    def test_zero_mass_on_the_truth_scores_minus_infinity(self) -> None:
        edits = {HypothesisId("h"): closed_set()["seasonality"]}
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S1"),
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId("h"): Probability(1.0)}
            ),
            abstain_mass=Probability(0.0),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        score = closed_world_score(diagnosis, closed_set()["hawkes"], edits)
        assert score.log_score == -math.inf
        assert not score.correct

    def test_a_bare_lead_is_correct_but_not_identified(self) -> None:
        """ "Recovered the diagnosis" must not mean "led without a majority"."""
        edits = {
            HypothesisId("a"): closed_set()["hawkes"],
            HypothesisId("b"): closed_set()["regime_switching"],
            HypothesisId("c"): closed_set()["seasonality"],
        }
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S10"),
            distribution=FrozenDict[HypothesisId, Probability](
                {
                    HypothesisId("a"): Probability(0.4),
                    HypothesisId("b"): Probability(0.35),
                    HypothesisId("c"): Probability(0.25),
                }
            ),
            abstain_mass=Probability(0.6),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        score = closed_world_score(diagnosis, closed_set()["hawkes"], edits)
        assert score.correct
        assert not score.identified

    def test_mass_on_an_unknown_structure_raises(self) -> None:
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S1"),
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId("ghost"): Probability(1.0)}
            ),
            abstain_mass=Probability(0.0),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        with pytest.raises(DiagnosisError, match="unknown to the scorer"):
            closed_world_score(diagnosis, closed_set()["hawkes"], {})
