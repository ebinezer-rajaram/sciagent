"""The slice's scenarios S1-S12 (SPEC §4.5), and the scenario type itself.

These are not acceptance tests -- SPEC §11 gives items 9 and 11 integration gates
rather than A-gates, so nothing here is named ``test_aN_``. What they check is
that the scenarios say what SPEC §4.5's table says, since every later number is
computed against them. What an optimal policy *needs* on them is
``tests/test_oracle.py``; this module is only about what they are.
"""

from __future__ import annotations

import pytest

from environments.pointproc.mechanisms import (
    CONFOUNDED_MECHANISMS,
    OBSERVATION_CENSORING,
    REGIME_SWITCHING,
    SEASONALITY,
    SIZE_EXCITATION,
    SIZE_MIXTURE,
)
from environments.pointproc.outcomes import slice_designs
from environments.pointproc.scenarios import (
    NON_IDENTIFIABLE_BUDGET,
    scenario,
    slice_scenarios,
)
from sciagent.core.errors import InvestigationError, MalformedDesignError
from sciagent.core.types import ScenarioId, Seed
from sciagent.eval.scenarios import SCENARIO_CLASSES, Scenario
from sciagent.registry.budget import Budget


class TestTheSliceScenarios:
    """S1-S12 as SPEC §4.5's table defines them."""

    def test_there_are_twelve_of_them_in_specification_order(self) -> None:
        assert [str(s.id) for s in slice_scenarios()] == [f"S{i}" for i in range(1, 13)]

    def test_every_class_is_one_the_type_declares(self) -> None:
        for candidate in slice_scenarios():
            assert candidate.scenario_class in SCENARIO_CLASSES

    @pytest.mark.parametrize(
        ("scenario_id", "mechanism"),
        [
            ("S1", "hawkes"),
            ("S2", "regime_switching"),
            ("S3", "seasonality"),
            ("S4", "poisson_mixture"),
        ],
    )
    def test_s1_to_s4_are_each_mechanism_alone(
        self, scenario_id: str, mechanism: str
    ) -> None:
        """SPEC §4.5: "Single | Each mechanism alone"."""
        assert scenario(scenario_id).truth == frozenset(
            {CONFOUNDED_MECHANISMS[mechanism]}
        )
        assert scenario(scenario_id).scenario_class == "single"

    @pytest.mark.parametrize(
        ("scenario_id", "mechanism"),
        [("S5", "hawkes"), ("S6", "seasonality"), ("S7", "regime_switching")],
    )
    def test_s5_to_s7_carry_the_ground_truth_the_table_names(
        self, scenario_id: str, mechanism: str
    ) -> None:
        assert scenario(scenario_id).truth == frozenset(
            {CONFOUNDED_MECHANISMS[mechanism]}
        )
        assert scenario(scenario_id).scenario_class == "confounded"

    def test_s8_is_seasonality_plus_a_size_distribution_mixture(self) -> None:
        """SPEC §4.5: "Compound | Seasonality + size-distribution mixture"."""
        s8 = scenario("S8")
        assert s8.truth == frozenset({SEASONALITY, SIZE_MIXTURE})
        assert s8.scenario_class == "compound"
        targets = {type(edit).__name__ for edit in s8.truth}
        assert len(targets) == 2, "a compound defect must not be one edit twice"

    def test_s9_is_the_null(self) -> None:
        """SPEC §4.5: "Null | No edit"."""
        assert scenario("S9").truth == frozenset()
        assert scenario("S9").is_null

    def test_s10_is_budget_starved_relative_to_the_others(self) -> None:
        """SPEC §4.5 S10: "budget below discriminating threshold".

        That the budget is *below the threshold* rather than merely small is
        ``tests/test_oracle.py``'s to establish, since the threshold is what an
        optimal policy needs and that is a computation.
        """
        s10 = scenario("S10")
        assert s10.scenario_class == "non_identifiable"
        assert s10.budget.total == NON_IDENTIFIABLE_BUDGET
        assert all(
            s10.budget.total < other.budget.total
            for other in slice_scenarios()
            if str(other.id) != "S10"
        )

    def test_s11_is_the_out_of_library_mechanism(self) -> None:
        """SPEC §4.5 S11: ``AddDependency(size -> arrival)``, absent from the
        agent grammar."""
        s11 = scenario("S11")
        assert s11.truth == frozenset({SIZE_EXCITATION})
        assert s11.scenario_class == "out_of_library"
        assert not s11.nuisance

    def test_s12_is_regime_switching_under_a_censoring_nuisance(self) -> None:
        """SPEC §4.5 S12: the truth is the mechanism, the censoring is not."""
        s12 = scenario("S12")
        assert s12.truth == frozenset({REGIME_SWITCHING})
        assert s12.nuisance == frozenset({OBSERVATION_CENSORING})
        assert s12.scenario_class == "garden_path"

    def test_only_s12_carries_a_nuisance(self) -> None:
        """A nuisance is executed and never scored, so an unintended one would
        quietly change what a scenario measures."""
        carrying = [str(s.id) for s in slice_scenarios() if s.nuisance]
        assert carrying == ["S12"]

    def test_the_executed_defect_is_the_truth_plus_the_nuisance(self) -> None:
        for candidate in slice_scenarios():
            assert candidate.executed == candidate.truth | candidate.nuisance
            if not candidate.nuisance:
                assert candidate.executed == candidate.truth

    def test_no_two_scenarios_share_a_seed(self) -> None:
        """S1 and S5 hold the same truth; a shared seed would make them one run."""
        seeds = [int(s.seed) for s in slice_scenarios()]
        assert len(set(seeds)) == len(seeds)

    def test_every_scenario_offers_the_full_observational_design_set(self) -> None:
        for candidate in slice_scenarios():
            assert candidate.designs == slice_designs()

    def test_the_set_is_a_stable_artefact(self) -> None:
        """Same objects on every call, so a scenario cannot drift within a run."""
        assert slice_scenarios() == slice_scenarios()


class TestTheScenarioType:
    """What :class:`Scenario` refuses."""

    def test_a_scenario_with_no_design_is_refused(self) -> None:
        with pytest.raises(MalformedDesignError, match="offers no design"):
            Scenario(
                id=ScenarioId("empty"),
                scenario_class="single",
                truth=frozenset(),
                designs=(),
                budget=Budget(total=1.0),
                seed=Seed(1),
            )

    def test_a_repeated_template_is_refused(self) -> None:
        first = slice_designs()[0]
        with pytest.raises(MalformedDesignError, match="twice"):
            Scenario(
                id=ScenarioId("doubled"),
                scenario_class="single",
                truth=frozenset(),
                designs=(first, first),
                budget=Budget(total=1.0),
                seed=Seed(1),
            )

    def test_asking_for_an_unoffered_design_raises(self) -> None:
        with pytest.raises(InvestigationError, match="does not offer"):
            scenario("S1").design("query:nothing_of_the_sort")

    def test_asking_for_an_offered_design_returns_it(self) -> None:
        wanted = slice_designs()[0]
        assert scenario("S1").design(str(wanted.id)) == wanted
