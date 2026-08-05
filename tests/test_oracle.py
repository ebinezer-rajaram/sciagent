"""Backlog item 11's gate: the twelve scenarios, and what an optimal policy needs.

Item 11's gate is "oracle policy lengths -- exhaustive DP where tractable,
planning-baseline lower bound otherwise", an integration gate rather than a
lettered criterion, so nothing here is named ``test_aN_``: CLAUDE.md's naming
rule is what ``scripts/status.py`` derives A-gate coverage from, and a test named
for a gate it does not check would be counted as one.

Three things are established.

**The bracket is coherent.** A floor, an optimum and an achievable length have
to sit in that order. This is the test that caught the first version of
:func:`~sciagent.eval.oracle.evidence_bound`, which exceeded what greedy
achieved on S2 and was therefore not a bound at all.

**The scenarios are what SPEC §4.5 says they are**, in measurements rather than
in comments: S10's budget is below the threshold an optimal policy needs, S11's
truth is unreachable without extending the hypothesis space, and S12's censored
world reads as seasonality on the first diagnostics while still having regime
switching as its closest hypothesis -- a garden path, not a trap.

**The intervention earns its place in the design space.** Item 9 measured what
its absence cost; this measures what its presence buys, on the pair SPEC §4.2
says nothing else separates.
"""

from __future__ import annotations

import math
from itertools import permutations

import pytest
from oracle_runs import oracle_lengths, without_intervention
from slice_tables import CLOSED_SET, gate_table

from environments.pointproc.grammar import agent_grammar, edit_grammar
from environments.pointproc.mechanisms import (
    OBSERVATION_CENSORING,
    REGIME_SWITCHING,
    SIZE_EXCITATION,
)
from environments.pointproc.outcomes import forced_design, slice_templates
from environments.pointproc.scenarios import scenario, slice_scenarios
from sciagent.core.types import ExperimentTemplateId
from sciagent.eval.oracle import OraclePolicyLength, _choice_key

#: Scenarios by class, as SPEC §4.5's table groups them.
SINGLE = ("S1", "S2", "S3", "S4")
CONFOUNDED = ("S5", "S6", "S7")

#: The pairs that share a ground truth. An oracle length is a property of the
#: truth, the world, the designs and the prior -- and not of the seed -- so these
#: must agree exactly.
SAME_TRUTH = (("S1", "S5"), ("S2", "S7"), ("S3", "S6"))


def _length(scenario_id: str) -> OraclePolicyLength:
    return oracle_lengths()[scenario_id]


def _world_row(
    template: ExperimentTemplateId, scenario_id: str = "S12"
) -> tuple[float, ...]:
    """Return what a scenario's executed world returns on one design.

    Defaults to S12 because the censored world is what most of the callers here
    are asking about; the regime pair passes its own id.
    """
    return gate_table().probabilities(scenario(scenario_id).executed, template)


def _row(name: str, template: ExperimentTemplateId) -> tuple[float, ...]:
    return gate_table().probabilities(CLOSED_SET[name], template)


def _template(metric: str) -> ExperimentTemplateId:
    for candidate in slice_templates():
        if str(candidate.outcome.metrics[0].name) == metric:
            return candidate.id
    raise AssertionError(f"no slice template measures {metric!r}")


class TestTheBracketIsCoherent:
    """A floor, an optimum and an achievable length, in that order."""

    @pytest.mark.parametrize("scenario_id", [str(s.id) for s in slice_scenarios()])
    def test_the_floor_never_exceeds_what_greedy_achieves(
        self, scenario_id: str
    ) -> None:
        """``evidence_bound`` is a bound or it is nothing.

        One-step-greedy is a policy, so whatever it achieves is achievable, so
        no valid lower bound on the optimum can exceed it. Read only where greedy
        finishes every rollout, since a conditional mean is not an achievable
        expected length.
        """
        length = _length(scenario_id)
        if length.greedy_completion < 1.0 or length.greedy_expected_steps is None:
            pytest.skip("greedy does not finish every rollout; no achievable length")
        assert length.evidence_bound <= length.greedy_expected_steps + 1e-9

    @pytest.mark.parametrize("scenario_id", [str(s.id) for s in slice_scenarios()])
    def test_the_optimum_never_exceeds_what_greedy_achieves(
        self, scenario_id: str
    ) -> None:
        """The dynamic programme is a minimum over policies; greedy is one of them."""
        length = _length(scenario_id)
        if length.greedy_completion < 1.0 or length.greedy_expected_steps is None:
            pytest.skip("greedy does not finish every rollout; no achievable length")
        assert length.expected_steps <= length.greedy_expected_steps + 1e-9

    def test_an_unreachable_truth_reports_no_length_rather_than_a_large_one(
        self,
    ) -> None:
        """S11 and S8 hold structures the closed set cannot express."""
        for scenario_id in ("S8", "S11"):
            length = _length(scenario_id)
            assert length.truth_mass_prior == 0.0
            assert not length.identifiable
            assert math.isinf(length.evidence_bound)

    @pytest.mark.parametrize(("left", "right"), SAME_TRUTH)
    def test_scenarios_sharing_a_truth_share_a_length(
        self, left: str, right: str
    ) -> None:
        """The oracle depends on the world, not on the seed the world is drawn at.

        S1 and S5 are the same truth under the same designs; what distinguishes
        them is the seed and how the result is read (see
        ``environments/pointproc/scenarios.py``). If their lengths ever differed,
        the oracle would be reading something it should not.
        """
        assert _length(left).expected_steps == _length(right).expected_steps
        assert _length(left).reach_probability == _length(right).reach_probability


class TestWhatTheScenariosNeed:
    """SPEC §4.5's table, as measurements."""

    @pytest.mark.parametrize("scenario_id", SINGLE + CONFOUNDED)
    def test_every_in_library_scenario_is_identifiable(self, scenario_id: str) -> None:
        """A scenario whose truth is in the closed set must be reachable.

        Not a foregone conclusion: it is what makes the slice's floor a floor. A
        conventional system that entertains the whole closed set and plans
        optimally identifies these, so a system that does not has been beaten by
        something, and the something is measurable.
        """
        length = _length(scenario_id)
        assert length.truth_mass_prior > 0.0
        assert length.identifiable or length.greedy_completion > 0.0

    def test_the_null_scenario_needs_no_experiment(self) -> None:
        """S9's truth is the null, which the parsimony prior already believes.

        SPEC §0's structural prior gives the empty edit set one bit and every
        mechanism twenty-three or more, so the null holds essentially all the
        mass before anything is run. Abstention on S9 is therefore not a
        discovery, and the oracle says so with a length of zero -- which is worth
        knowing before crediting a system for it.
        """
        length = _length("S9")
        assert length.expected_steps == 0.0
        assert length.certain
        assert length.first_design is None

    def test_s10s_budget_is_below_the_discriminating_threshold(self) -> None:
        """SPEC §4.5 S10, derived rather than asserted.

        ``expected_steps`` is a lower bound when the horizon did not settle every
        path, so a budget below it is below the optimum under either reading.
        """
        length = _length("S10")
        assert scenario("S10").budget.total < length.expected_steps
        assert all(
            scenario("S10").budget.total < other.budget.total
            for other in slice_scenarios()
            if str(other.id) != "S10"
        )

    def test_s11_needs_the_hypothesis_space_extended(self) -> None:
        """SPEC §4.5 S11: out-of-library, by the grammar and by the arithmetic.

        The first two assertions are SPEC §3.2's mechanical definition; the third
        is its consequence, computed. No policy over the agent's closed set
        identifies S11 -- not with a larger budget, not with better planning --
        so Stage B is necessary and not merely helpful.
        """
        assert edit_grammar().contains(SIZE_EXCITATION)
        assert not agent_grammar().contains(SIZE_EXCITATION)
        assert scenario("S11").truth == frozenset({SIZE_EXCITATION})
        assert not _length("S11").identifiable


class TestTheInterventionEarnsItsPlace:
    """What the fifth design buys, on the pair nothing else separates."""

    def test_the_intervention_alone_separates_the_regime_pair(self) -> None:
        """SPEC §4.2 stage 3, measured on the pair it was added for.

        Hawkes and regime switching are calibrated to be indistinguishable under
        every dispersion diagnostic, so on a scenario whose truth is one of them
        the intervention is the only design that tells them apart. Stated as the
        margin it earns per experiment, which is what "separates" means for a
        belief: the four queries buy under a bit, the forced arrival buys
        several, and the gap is a multiple rather than a nose.

        This replaced an assertion that the optimal policy *opens* with the
        forced arrival, which does not follow and is not true -- see
        :class:`TestASaturatedSearchHasNoOpinion` and ``docs/DECISIONS.md``.
        Beating Hawkes is not the binding constraint at the prior: the truth has
        to outrun all four rivals to cross the identification threshold, and the
        phase-conditioned query does that better while separating this pair
        worse.
        """
        for scenario_id in ("S2", "S7"):
            margins = {
                str(template.id): _evidence(
                    "regime_switching", template.id, scenario_id
                )
                - _evidence("hawkes", template.id, scenario_id)
                for template in slice_templates()
            }
            forced = margins.pop(str(forced_design().id))
            sharpest_query = max(margins.values())
            assert forced > 1.0
            assert sharpest_query < 1.0
            assert forced > 4.0 * sharpest_query

    def test_the_observational_designs_alone_are_worse(self) -> None:
        """Item 9's ceiling, measured from the other side.

        With the four query designs an optimal policy is no better off than
        greedy is with five, and on the pair SPEC §4.2 reserves for the
        intervention it is strictly worse: a longer achievable length, or none at
        all. This is why the fifth design joined the calibrated set rather than
        waiting for the agent.
        """
        with_it = _length("S7")
        without = without_intervention("S7")
        assert without.evidence_bound > with_it.evidence_bound
        assert without.greedy_completion < with_it.greedy_completion


#: Scenarios no policy resolves within :data:`DEFAULT_HORIZON`. Five of twelve,
#: which is why the opening design has to say so rather than name a design.
SATURATED = ("S2", "S7", "S8", "S11", "S12")


class TestASaturatedSearchHasNoOpinion:
    """What the dynamic programme reports when the horizon was not enough.

    Every branch of these five bottoms out at the horizon and is charged the
    ``horizon + 1`` floor, so every design scores exactly alike and the search
    has no preference to state. Reporting one anyway was a bug in two layers:
    the value comparison resolved a five-way tie on a difference of one unit in
    the last place, and :attr:`OraclePolicyLength.first_design` documented
    ``None`` for an unreachable truth without ever returning it.
    """

    @pytest.mark.parametrize("scenario_id", SATURATED)
    def test_a_horizon_that_never_resolves_names_no_opening(
        self, scenario_id: str
    ) -> None:
        """``first_design`` is a policy's choice or it is nothing.

        Its docstring promises ``None`` where the truth is unreachable, and
        :attr:`~OraclePolicyLength.identifiable` is what "unreachable" means
        here. A design named under a saturated search is not the optimum's
        opening -- there is no optimum -- and reading it as one is how the
        intervention came to look preferred on S2 and S7.
        """
        length = _length(scenario_id)
        assert length.reach_probability == 0.0
        assert not length.identifiable
        assert length.first_design is None

    @pytest.mark.parametrize(
        "scenario_id", [s for s in (str(x.id) for x in slice_scenarios())]
    )
    def test_an_opening_is_named_exactly_when_the_search_resolved(
        self, scenario_id: str
    ) -> None:
        """The converse, so the fix cannot silence a design that was earned.

        S9 is the third case the docstring names: identified before any
        experiment, so no opening either, and it is the one scenario where
        ``identifiable`` is true and ``first_design`` is still ``None``.
        """
        length = _length(scenario_id)
        if scenario_id == "S9":
            assert length.certain
            assert length.first_design is None
        elif length.identifiable:
            assert length.first_design is not None
        else:
            assert length.first_design is None


class TestTheChoiceOfDesignIsPlatformIndependent:
    """Invariant 3, at the one comparison that decides which design is named."""

    #: The two designs of the S2 tie, in the order their ids sort.
    EARLIER = ExperimentTemplateId("force[arrival@0=0.01|20]:mean_rate")
    LATER = ExperimentTemplateId("query:phase_conditioned_dispersion")

    def test_a_last_place_difference_is_a_tie_the_id_breaks(self) -> None:
        """The exact comparison that flipped between Windows and Linux.

        Five saturated designs all score ``horizon + 1``, but one summed a
        single unit in the last place low, which the raw float comparison read
        as a strict win. Rounding first makes it the tie it is, so the id
        decides and both platforms name the same design.
        """
        noisy = math.nextafter(4.0, 0.0)
        assert noisy < 4.0
        assert _choice_key(4.0, self.EARLIER) < _choice_key(noisy, self.LATER)
        assert _choice_key(noisy, self.EARLIER) < _choice_key(4.0, self.LATER)

    def test_a_real_margin_still_decides(self) -> None:
        """The rounding must not swallow a difference that means something.

        At the first horizon where the search resolves S2 the designs are about
        five hundredths apart, which is eight orders of magnitude above the
        tolerance, so nothing that follows from evidence is being tied here.
        """
        assert _choice_key(3.95, self.LATER) < _choice_key(4.0, self.EARLIER)

    def test_the_order_designs_are_offered_in_cannot_matter(self) -> None:
        """Rounding buys transitivity, which a tolerance would not.

        A near-equality test is not transitive, so with one the winner of three
        candidates could depend on which was compared first -- a dict ordering
        away from breaking the determinism invariant a second time.
        """
        candidates = [
            (4.0, self.EARLIER),
            (math.nextafter(4.0, 0.0), self.LATER),
            (4.0, ExperimentTemplateId("query:size_dispersion")),
        ]
        winners = {
            min(permuted, key=lambda pair: _choice_key(*pair))[1]
            for permuted in permutations(candidates)
        }
        assert winners == {self.EARLIER}


def _evidence(
    name: str, template: ExperimentTemplateId, scenario_id: str = "S12"
) -> float:
    """Return the bits per experiment ``name`` earns under a scenario's world.

    The world-weighted mean log-likelihood: what a belief actually accumulates
    per repetition of that design, and therefore what it converges on. A constant
    that is the same for every hypothesis is dropped, so only differences between
    hypotheses are meaningful -- which is all that is ever asserted here.
    """
    return math.fsum(
        weight * math.log2(probability)
        for weight, probability in zip(
            _world_row(template, scenario_id), _row(name, template), strict=True
        )
    )


class TestS12IsAGardenPathAndNotATrap:
    """Both halves of SPEC §4.5 S12, measured on the calibrated table."""

    def test_the_spurious_signature_pays_for_the_wrong_answer(self) -> None:
        """The garden path, in bits.

        Repeating the count autocorrelation under the censored world moves the
        belief towards **seasonality** at some seven bits an experiment over the
        truth, which is last of the five. That is what "a strong spurious
        periodic signature" has to mean for a Bayesian: not that a number looks
        odd, but that the evidence points confidently somewhere wrong.
        """
        template = _template("count_autocorrelation_w2")
        scores = {name: _evidence(name, template) for name in CLOSED_SET}
        assert max(scores, key=lambda name: scores[name]) == "seasonality"
        assert min(scores, key=lambda name: scores[name]) == "regime_switching"
        assert scores["seasonality"] - scores["regime_switching"] > 5.0

    def test_the_path_can_be_left(self) -> None:
        """The exit, and the reason S12 is not a trap.

        Conditioning on the phase a seasonality hypothesis proposes refutes it
        outright -- it is the worst of the five there -- because the censoring
        period is deliberately not that period. A system that runs this
        diagnostic after the autocorrelation has told it "seasonality" learns
        that it was misled, which is the plan revision the scenario is for.
        """
        template = _template("phase_conditioned_dispersion")
        scores = {name: _evidence(name, template) for name in CLOSED_SET}
        assert min(scores, key=lambda name: scores[name]) in ("seasonality", "null")
        assert scores["regime_switching"] - scores["seasonality"] > 5.0

    def test_the_intervention_favours_the_truth(self) -> None:
        """The recovery, on the design SPEC §4.2 reserves for this pair."""
        scores = {name: _evidence(name, forced_design().id) for name in CLOSED_SET}
        assert max(scores, key=lambda name: scores[name]) == "regime_switching"

    def test_the_truth_is_the_closest_hypothesis_over_the_whole_design_set(
        self,
    ) -> None:
        """What makes recovery possible at all.

        A belief fed every design equally converges on whichever hypothesis is
        closest in Kullback-Leibler divergence to the world it is watching. If
        that were seasonality, no policy could ever recover the truth and SPEC
        §12 criterion 7 would be unreachable by construction. It is regime
        switching, by the margin ``docs/DECISIONS.md`` records.
        """
        scores = {
            name: math.fsum(
                _evidence(name, template.id) for template in slice_templates()
            )
            for name in CLOSED_SET
        }
        assert max(scores, key=lambda name: scores[name]) == "regime_switching"
        runner_up = max(
            (name for name in scores if name != "regime_switching"),
            key=lambda name: scores[name],
        )
        assert scores["regime_switching"] - scores[runner_up] > 0.5

    def test_the_nuisance_costs_one_step_greedy(self) -> None:
        """The scenario is harder for the reason SPEC §4.5 says it is.

        Greedy spends much of its budget on the highest-gain design, which under
        the censored world is the one carrying the spurious signal, so it is led
        away from the truth: it finishes strictly less often than on S2, the same
        truth without the nuisance, and takes longer when it does.

        Not a defect in greedy. Choosing what to *stop* running is exactly the
        plan revision S12 is for, and a scenario every conventional planner
        solved on the first try would not be testing it.
        """
        censored = _length("S12")
        clean = _length("S2")
        assert censored.greedy_completion < clean.greedy_completion
        if censored.greedy_expected_steps is not None:
            assert clean.greedy_expected_steps is not None
            assert censored.greedy_expected_steps > clean.greedy_expected_steps

    def test_the_nuisance_is_executed_and_never_scored(self) -> None:
        """SPEC §4.5 S12's truth is the regime switching, not the censoring."""
        s12 = scenario("S12")
        assert s12.truth == frozenset({REGIME_SWITCHING})
        assert s12.nuisance == frozenset({OBSERVATION_CENSORING})
        assert s12.executed == frozenset({REGIME_SWITCHING, OBSERVATION_CENSORING})
        assert not agent_grammar().contains(OBSERVATION_CENSORING)
        assert edit_grammar().contains(OBSERVATION_CENSORING)
