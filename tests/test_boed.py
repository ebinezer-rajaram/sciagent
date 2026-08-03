"""Backlog item 8: BOED's wiring, and the identity its cheap form rests on.

A24 lives in ``tests/acceptance/test_a24.py`` and measures whether the selector
chooses *well*. This module checks the things A24 cannot see because a ratio of
realised gains would survive them being wrong: that the formula computed is the
one intended, that the planning belief and the engine's posterior are the same
distribution, and that ``CompareCandidates`` is answered without anything being
executed.

The table is written by hand rather than simulated
--------------------------------------------------

Every count in :func:`synthetic_table` is a literal. Nothing here needs the
slice's real outcome distributions -- what is under test is the arithmetic on
top of them -- and writing the counts down buys two things a simulated table
cannot. The expected answers become exact rather than approximate: a design
whose rows are *identical* across hypotheses has an expected information gain of
zero by construction, so the assertion is a statement and not a threshold. And
the module costs no simulation at all, against the several minutes the slice's
2000-replicate table takes to build.

Everything else is real: the slice's templates and their outcome spaces, the
closed set of SPEC §4.2, the edit grammar, the hypothesis graph and its
falsifiability check, and :class:`EmpiricalTableEngine` itself.
"""

from __future__ import annotations

import math

import pytest

from environments.pointproc.catalogue import metric_registry
from environments.pointproc.grammar import edit_grammar
from environments.pointproc.mechanisms import SIZE_EXCITATION
from environments.pointproc.outcomes import closed_set, slice_templates
from sciagent.core.conditions import Compare
from sciagent.core.edits import Defect
from sciagent.core.errors import (
    InferenceError,
    MalformedDesignError,
    UnknownHypothesisError,
)
from sciagent.core.types import (
    ExperimentId,
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    Prediction,
    PredictionId,
    Probability,
    Seed,
)
from sciagent.experiments.boed import (
    Predictive,
    candidate_hypotheses,
    compare,
    expected_information_gain,
    greedy,
    plan,
    rank,
    restrict,
    select,
    table_predictive,
    update,
)
from sciagent.experiments.dsl import CompareCandidates
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    ExperimentTemplate,
    structure_key,
)
from sciagent.inference.entropy import entropy_bits

GRAMMAR = edit_grammar()
METRICS = metric_registry()
TEMPLATES = slice_templates()
CLOSED_SET = closed_set()
STRUCTURE_NAMES = tuple(sorted(CLOSED_SET))

#: Replicates the hand-written rows declare. No simulation is performed, so this
#: is only the denominator the Krichevsky-Trofimov estimator and the reported
#: standard errors are computed against.
REPLICATES = 1000

#: Rows identical across every hypothesis. Learns nothing, exactly.
FLAT = ExperimentTemplateId("query:size_dispersion")

#: Rows concentrated on a different cell per hypothesis. Resolves the whole
#: belief in one experiment, exactly.
SEPARATING = ExperimentTemplateId("query:count_autocorrelation_w2")


def row(cells: int, peak: int, sharpness: float) -> tuple[int, ...]:
    """Return counts with ``sharpness`` of the mass on ``peak``, the rest level.

    Guarantees the row sums to :data:`REPLICATES` and that the remainder is
    spread from the lowest cell upwards, so the row is a function of its
    arguments alone and carries no iteration-order dependence.
    """
    focused = round(sharpness * REPLICATES)
    rest = REPLICATES - focused
    base, remainder = divmod(rest, cells - 1)
    counts = [base] * cells
    counts[peak] = focused
    for index in range(cells):
        if remainder == 0:
            break
        if index != peak:
            counts[index] += 1
            remainder -= 1
    return tuple(counts)


def synthetic_table() -> EmpiricalTable:
    """Return a table whose informativeness per design is known by construction."""
    counts: dict[tuple[str, ExperimentTemplateId], tuple[int, ...]] = {}
    for index, name in enumerate(STRUCTURE_NAMES):
        key = structure_key(CLOSED_SET[name])
        for template in TEMPLATES:
            cells = template.outcome.n_cells
            if template.id == FLAT:
                counts[(key, template.id)] = row(cells, 0, 0.2)
            elif template.id == SEPARATING:
                counts[(key, template.id)] = row(cells, index, 1.0)
            else:
                counts[(key, template.id)] = row(cells, index, 0.5)
    return EmpiricalTable(
        templates=FrozenDict[ExperimentTemplateId, ExperimentTemplate](
            {template.id: template for template in TEMPLATES}
        ),
        counts=FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]](counts),
        replicates=REPLICATES,
        seed=Seed(0),
    )


def prediction(name: str) -> Prediction:
    """Return a refutable prediction, so the graph will admit the hypothesis."""
    return Prediction(
        id=PredictionId(f"{name}/dispersion"),
        hypothesis_id=HypothesisId(name),
        diagnostic=METRICS.spec("inter_arrival_dispersion").ref,
        condition=Compare(">", 1.3) if name != "null" else Compare("<=", 1.3),
        under=ExperimentTemplateId("query:inter_arrival_dispersion"),
        refutation=Compare("<=", 1.3) if name != "null" else Compare(">", 1.3),
    )


def closed_graph() -> HypothesisGraph:
    """Return a hypothesis graph over the slice's closed set."""
    graph = HypothesisGraph.empty(GRAMMAR, METRICS)
    for name in STRUCTURE_NAMES:
        graph = graph.propose(
            HypothesisId(name),
            program_edit=CLOSED_SET[name],
            predictions=(prediction(name),),
            rationale=f"{name} as specified in SPEC §4.2",
        )
    return graph


def engine() -> EmpiricalTableEngine:
    """Return an engine over the hand-written table."""
    return EmpiricalTableEngine(closed_graph(), synthetic_table())


def flat_belief() -> FrozenDict[HypothesisId, Probability]:
    """Return a uniform belief over the closed set.

    Used wherever what is under test is the *ranking*. The engine's own prior is
    the structural one of SPEC §0, which is concentrated almost entirely on the
    null -- see :class:`TestTheStructuralPriorLeavesLittleToGain`, which measures
    that rather than working around it.
    """
    share = Probability(1.0 / len(STRUCTURE_NAMES))
    return FrozenDict[HypothesisId, Probability](
        {HypothesisId(name): share for name in STRUCTURE_NAMES}
    )


def program_edits() -> dict[HypothesisId, Defect]:
    """Return the structure of every hypothesis, as :func:`compare` wants it."""
    return {HypothesisId(name): CLOSED_SET[name] for name in STRUCTURE_NAMES}


TEMPLATE_IDS = tuple(template.id for template in TEMPLATES)


class TestExpectedInformationGain:
    """The number BOED maximises is the one it is documented to maximise."""

    def test_the_two_readings_of_mutual_information_agree(self) -> None:
        """``H(Y) - E_h H(Y|h)`` equals ``H(P) - E_y H(P|y)``, cell by cell.

        BOED computes the first because it is cheap. The second is the one the
        name describes. They are the same number, and this recomputes the
        expensive side in plain arithmetic to show it.
        """
        belief = flat_belief()
        predict = table_predictive(engine())
        names = sorted(belief)

        for template_id in TEMPLATE_IDS:
            cheap = expected_information_gain(template_id, belief, predict).bits

            cells = [predict(name, template_id).cells for name in names]
            before = entropy_bits([belief[name] for name in names])
            expensive = before
            for cell in range(len(cells[0])):
                joint = [belief[name] * cells[i][cell] for i, name in enumerate(names)]
                marginal = math.fsum(joint)
                posterior = [value / marginal for value in joint]
                expensive -= marginal * entropy_bits(posterior)

            assert cheap == pytest.approx(expensive, abs=1e-12), (
                f"{template_id}: outcome-space form gave {cheap!r} and "
                f"hypothesis-space form gave {expensive!r}"
            )

    def test_a_design_with_identical_rows_gains_nothing(self) -> None:
        """Rows that do not vary with the hypothesis carry exactly zero gain."""
        gain = expected_information_gain(
            FLAT, flat_belief(), table_predictive(engine())
        )

        assert gain.bits == pytest.approx(0.0, abs=1e-12)
        assert gain.marginal == pytest.approx(gain.conditional, abs=1e-12)

    def test_a_perfectly_separating_design_gains_the_whole_belief(self) -> None:
        """A design that puts each hypothesis on its own cell resolves the belief.

        Not exactly ``H(P)``: Krichevsky-Trofimov leaves every other cell a small
        positive probability, so a little uncertainty survives. The shortfall is
        bounded here rather than ignored.
        """
        belief = flat_belief()
        gain = expected_information_gain(SEPARATING, belief, table_predictive(engine()))
        prior_bits = entropy_bits([belief[name] for name in sorted(belief)])

        assert gain.bits < prior_bits
        assert gain.bits > prior_bits - 0.05, (
            f"a perfectly separating design left {prior_bits - gain.bits:.4f} bits "
            f"unresolved; only the estimator's smoothing should survive"
        )

    def test_select_prefers_the_separating_design(self) -> None:
        """The whole point, stated at its smallest."""
        best = select(TEMPLATE_IDS, flat_belief(), table_predictive(engine()))

        assert best.template == SEPARATING
        assert (
            rank(TEMPLATE_IDS, flat_belief(), table_predictive(engine()))[-1].template
            == FLAT
        )

    def test_an_exact_predictive_reports_no_monte_carlo_error(self) -> None:
        """``samples = inf`` is how a closed-form distribution is spelled."""

        def predict(
            hypothesis: HypothesisId, template: ExperimentTemplateId
        ) -> Predictive:
            index = STRUCTURE_NAMES.index(str(hypothesis))
            cells = [0.1] * 5
            cells[index] = 0.6
            return Predictive(cells=tuple(cells), samples=math.inf)

        gain = expected_information_gain(SEPARATING, flat_belief(), predict)

        assert gain.standard_error == 0.0
        assert gain.bits > 0.0

    def test_a_finite_predictive_reports_a_positive_error(self) -> None:
        gain = expected_information_gain(
            SEPARATING, flat_belief(), table_predictive(engine())
        )

        assert gain.standard_error > 0.0

    def test_a_belief_that_is_not_normalised_is_refused(self) -> None:
        belief = FrozenDict[HypothesisId, Probability](
            {HypothesisId(name): Probability(0.5) for name in STRUCTURE_NAMES}
        )

        with pytest.raises(InferenceError, match="normalised"):
            expected_information_gain(FLAT, belief, table_predictive(engine()))

    def test_an_empty_or_repeated_design_set_is_refused(self) -> None:
        predict = table_predictive(engine())

        with pytest.raises(MalformedDesignError, match="empty design set"):
            rank((), flat_belief(), predict)
        with pytest.raises(MalformedDesignError, match="repeats a template"):
            rank((FLAT, FLAT), flat_belief(), predict)


class TestSelectionIsDeterministic:
    """Ties are resolved by a rule, not by the sort's stability."""

    def test_an_exact_tie_resolves_by_ascending_template_id(self) -> None:
        """Every design here is uninformative, so every gain is exactly equal."""

        def predict(
            hypothesis: HypothesisId, template: ExperimentTemplateId
        ) -> Predictive:
            return Predictive(cells=(0.25, 0.25, 0.25, 0.25), samples=100.0)

        ordered = rank(TEMPLATE_IDS, flat_belief(), predict)

        assert len({gain.bits for gain in ordered}) == 1, "the tie is not exact"
        assert [gain.template for gain in ordered] == sorted(TEMPLATE_IDS)
        assert (
            select(tuple(reversed(TEMPLATE_IDS)), flat_belief(), predict).template
            == (sorted(TEMPLATE_IDS)[0])
        ), "selection depended on the order the designs were offered in"


class TestPlanningBeliefMatchesTheEngine:
    """One Bayes step is what recording the experiment does to the posterior."""

    def cell_two_of(self, template: ExperimentTemplate) -> tuple[float, ...]:
        """Return a diagnostic vector landing in cell 2 of a single-axis design."""
        axis = template.outcome.axes[0]
        value = 0.5 * (axis.interior[1] + axis.interior[2])
        assert template.outcome.cell_of((value,)) == 2
        return (value,)

    def test_update_agrees_with_recording_the_experiment(self) -> None:
        """The two beliefs of this subsystem are one distribution, measured.

        If these ever diverged, a plan would be optimising a belief the
        investigation does not hold, and no acceptance test would see it: A24
        scores realised gain, which a self-consistent but wrong belief still
        reports happily.
        """
        live = engine()
        template = next(t for t in TEMPLATES if t.id == SEPARATING)
        result = self.cell_two_of(template)

        before = live.posterior()
        planned = update(before, template.id, 2, table_predictive(live))

        live.record(ExperimentId("e1"), template, result)
        recorded = live.posterior()

        for name in sorted(recorded):
            assert planned[name] == pytest.approx(
                recorded[name], rel=1e-12, abs=1e-15
            ), (
                f"{name}: planning belief {planned[name]!r} against the engine's "
                f"{recorded[name]!r}"
            )

    def test_a_zero_mass_hypothesis_is_not_argued_back_in(self) -> None:
        """Rejection is evidential and an outcome cannot undo it."""
        belief = restrict(
            flat_belief(), [HypothesisId("hawkes"), HypothesisId("regime_switching")]
        )
        after = update(belief, SEPARATING, 2, table_predictive(engine()))

        assert after[HypothesisId("null")] == 0.0
        assert math.isclose(math.fsum(after[name] for name in sorted(after)), 1.0)

    def test_an_outcome_outside_the_design_is_refused(self) -> None:
        with pytest.raises(InferenceError, match="outside the"):
            update(flat_belief(), SEPARATING, 999, table_predictive(engine()))


class TestCompareCandidates:
    """SPEC §4.4's sixth operation, answered by selection (backlog item 8)."""

    def test_compare_ranks_designs_against_the_named_candidates_only(self) -> None:
        """A design separating only the excluded hypotheses is worth nothing here.

        The separating design puts each of the five structures on its own cell.
        Restricted to two of them it still separates those two, so it must still
        win; the flat design must still lose.
        """
        operation = CompareCandidates(
            candidates=(CLOSED_SET["hawkes"], CLOSED_SET["regime_switching"])
        )
        ordered = compare(
            operation,
            TEMPLATE_IDS,
            flat_belief(),
            table_predictive(engine()),
            program_edit=program_edits(),
        )

        assert ordered[0].template == SEPARATING
        assert ordered[-1].template == FLAT
        # Two candidates, so at most one bit is available -- against the 2.32
        # bits the same design is worth over all five.
        assert ordered[0].bits < 1.0

    def test_candidates_are_matched_by_structure_not_by_name(self) -> None:
        chosen = candidate_hypotheses(
            CompareCandidates(
                candidates=(CLOSED_SET["seasonality"], CLOSED_SET["null"])
            ),
            program_edits(),
        )

        assert chosen == (HypothesisId("null"), HypothesisId("seasonality"))

    def test_a_candidate_no_hypothesis_holds_is_refused(self) -> None:
        """Silently dropping it would compare against fewer candidates than asked."""
        operation = CompareCandidates(
            candidates=(CLOSED_SET["hawkes"], frozenset({SIZE_EXCITATION}))
        )

        with pytest.raises(UnknownHypothesisError, match="no hypothesis holds"):
            candidate_hypotheses(operation, program_edits())

    def test_restrict_zeroes_everything_outside_the_question(self) -> None:
        belief = restrict(flat_belief(), [HypothesisId("hawkes")])

        assert belief[HypothesisId("hawkes")] == 1.0
        assert all(belief[name] == 0.0 for name in sorted(belief) if name != "hawkes")

    def test_restrict_refuses_a_hypothesis_the_belief_does_not_hold(self) -> None:
        with pytest.raises(UnknownHypothesisError, match="cannot restrict"):
            restrict(flat_belief(), [HypothesisId("nonexistent")])


class TestPlanning:
    """The policy, and the entry point that keeps a caller away from the numbers."""

    def test_greedy_returns_one_step_per_experiment_and_a_falling_entropy(self) -> None:
        """The separating design resolves the belief, so the plan ends certain."""
        predict = table_predictive(engine())
        steps = greedy(
            TEMPLATE_IDS,
            flat_belief(),
            predict,
            lambda index, template: 0,
            steps=3,
        )

        assert len(steps) == 3
        assert all(step.template == SEPARATING for step in steps)
        final = steps[-1].posterior
        assert entropy_bits([final[name] for name in sorted(final)]) < 0.01
        assert steps[0].realised > 1.0, (
            "the first experiment should have resolved most of a 2.32-bit belief"
        )

    def test_a_plan_of_no_steps_is_refused(self) -> None:
        with pytest.raises(MalformedDesignError, match="performs no experiment"):
            greedy(
                TEMPLATE_IDS,
                flat_belief(),
                table_predictive(engine()),
                lambda index, template: 0,
                steps=0,
            )

    def test_plan_takes_both_beliefs_off_the_engine(self) -> None:
        """The entry point a system uses has no argument that could carry a score."""
        live = engine()
        steps = plan(live, TEMPLATE_IDS, lambda index, template: 0, steps=2)

        assert len(steps) == 2
        assert all(
            math.isclose(
                math.fsum(step.posterior[name] for name in sorted(step.posterior)), 1.0
            )
            for step in steps
        )


class TestTheStructuralPriorLeavesLittleToGain:
    """A property of SPEC §0's prior that backlog item 9's V1 has to plan around."""

    def test_the_untouched_prior_puts_almost_everything_on_the_null(self) -> None:
        """The null costs a bit and every mechanism costs twenty-three or more.

        Consequence: with nothing recorded, every design's expected information
        gain is near zero and BOED's first choice is settled by the tiebreak
        rather than by the belief. Recorded here because it is not a defect to be
        fixed -- SPEC §0 fixes the prior deliberately -- but it is a fact a
        BOED-only baseline has to be read in the light of.
        """
        live = engine()
        posterior = live.posterior()
        entropy = entropy_bits([posterior[name] for name in sorted(posterior)])
        best = select(TEMPLATE_IDS, posterior, table_predictive(live))

        assert posterior[HypothesisId("null")] > 0.99
        assert entropy < 0.1
        assert best.bits < entropy
