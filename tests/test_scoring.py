"""SPEC §8's six dimensions.

The dimension that carries the argument is **D3**. §8 says a structurally
different but interventionally equivalent explanation is a legitimate scientific
success and must not be scored as a failure -- that is research question R7 --
and D3 is the only dimension that can see it. The tests below measure that on
S11, where the truth is out of the agent's library by construction, and find
exactly the pattern §8 predicts: the library's Hawkes process is *no closer
structurally* than any other member, and interventionally almost
indistinguishable from the truth.

Nothing here asserts that a system scores well. These are properties of the
scoring functions, measured against structures whose relationship to the truth is
known independently.
"""

from __future__ import annotations

import math

import pytest
from slice_tables import GRAMMAR, gate_table

from environments.pointproc.outcomes import closed_set, simulator, slice_designs
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import DiagnosisError
from sciagent.core.types import Diagnosis, FrozenDict, HypothesisId, Probability
from sciagent.eval.scenarios import SCENARIO_CLASSES
from sciagent.eval.scoring import (
    DimensionVector,
    dimension_vector,
    jensen_shannon_bits,
    leading_structure,
    primary_dimension,
)
from sciagent.inference.empirical import EmpiricalTable

#: The designs held out of D2 and D3: the two mark diagnostics and the
#: forced-arrival intervention. The intervention is the one that matters -- §8
#: defines D3 over "a held-out intervention battery" -- and the mark diagnostics
#: are included because S11's mechanism is gated by marks, so a candidate that
#: got the arrival side right and the mark side wrong should not score a clean 1.
#:
#: **Named, not sliced.** This was ``slice_designs()[3:]`` until 2026-08-16, and
#: a positional slice silently absorbed ``size_gap_correlation`` when SPEC §4.3
#: gained it -- growing the battery from two designs to three, and moving a
#: reported number, with nothing in the diff to say so. Membership of a battery
#: that D2 and D3 are defined over has to be a stated choice.
#:
#: ``size_gap_correlation`` is kept rather than dropped, and that decision is the
#: one this comment exists for. It is the *most* direct instance of the rationale
#: above: it is precisely the statistic that catches an arrival side got right
#: and a mark coupling got wrong, which is Hawkes against S11's truth exactly.
#: Excluding it would have preserved an earlier finding by declining to measure
#: the thing that tests it. See ``docs/DECISIONS.md``.
_HELD_OUT_IDS = frozenset(
    {
        "query:size_dispersion",
        "query:size_gap_correlation",
    }
)
HELD_OUT = tuple(
    design
    for design in slice_designs()
    if str(design.id) in _HELD_OUT_IDS or str(design.id).startswith("force[")
)

_TABLE: list[EmpiricalTable] = []


def _table() -> EmpiricalTable:
    if not _TABLE:
        _TABLE.append(gate_table())
    return _TABLE[0]


def _vector(candidate_name: str) -> DimensionVector:
    """Return the dimension vector of a library structure against S11's truth."""
    truth = scenario("S11").truth
    library = closed_set()
    vector, grown = dimension_vector(
        library[candidate_name],
        truth,
        grammar=GRAMMAR,
        table=_table(),
        simulate=simulator(GRAMMAR),
        held_out=HELD_OUT,
    )
    _TABLE[0] = grown
    return vector


class TestD3SeesWhatD1Cannot:
    """R7, measured on the scenario it is about."""

    def test_hawkes_is_interventionally_closest_to_s11s_truth(self) -> None:
        """0.70 similarity, against 0.48-0.53 for every other library member.

        S11's mechanism is a Hawkes process whose marks gate the excitation, so
        a plain Hawkes reproduces its response to a forced arrival better than
        anything else in the library. Read against
        ``test_hawkes_is_structurally_no_closer_than_the_null``: D1 puts Hawkes
        *further* from the truth than proposing nothing, and that disagreement
        between the two dimensions is what R7 is and what §8 forbids collapsing.

        **Revised 2026-08-16, and the revision is the interesting part.** This
        read 0.960 against a best rival of 0.698 when the held-out battery was
        two designs, and asserted ``hawkes > 0.9`` -- near-indistinguishable from
        the truth's own 1.000. With ``size_gap_correlation`` in the battery it is
        0.697 against 0.527. Hawkes was never interventionally *equivalent* to
        S11's truth; the battery could not see the mark-arrival coupling, which
        is the one dimension the two genuinely differ on. It remains the closest
        available approximation, which is the weaker claim the numbers support
        and the one asserted here. See ``docs/DECISIONS.md``.
        """
        hawkes = _vector("hawkes").d3_intervention_similarity
        others = [
            _vector(name).d3_intervention_similarity
            for name in sorted(closed_set())
            if name != "hawkes"
        ]
        assert hawkes > max(others) + 0.15, (
            f"Hawkes scores {hawkes:.3f} against a best rival of {max(others):.3f}; "
            f"D3 is no longer separating the interventionally-closest "
            f"explanation from the rest"
        )
        assert hawkes < 0.8, (
            f"Hawkes scores {hawkes:.3f}, which is back in the range that reads as "
            f"interventional equivalence. That was an artefact of a battery blind "
            f"to the mark-arrival coupling; if it has returned, check whether "
            f"size_gap_correlation is still in HELD_OUT"
        )

    def test_structural_distance_does_not_see_it(self) -> None:
        """D1 ranks Hawkes no better than its rivals, and worse than the null.

        The whole point. Scored on D1 alone, the system that proposed Hawkes on
        S11 looks exactly as wrong as one that proposed seasonality, and *more*
        wrong than one that proposed nothing at all. §8 forbids collapsing the
        dimensions for precisely this reason.
        """
        hawkes = _vector("hawkes").d1_structural_distance
        assert hawkes == pytest.approx(_vector("seasonality").d1_structural_distance)
        assert _vector("null").d1_structural_distance < hawkes

    def test_the_truth_scores_one_against_itself(self) -> None:
        """The control: D3 is exactly 1 for a candidate that *is* the truth."""
        truth = scenario("S11").truth
        vector, grown = dimension_vector(
            truth,
            truth,
            grammar=GRAMMAR,
            table=_table(),
            simulate=simulator(GRAMMAR),
            held_out=HELD_OUT,
        )
        _TABLE[0] = grown
        assert vector.d3_intervention_similarity == pytest.approx(1.0)
        assert vector.d1_structural_distance == 0.0


class TestTheVectorIsNeverCollapsed:
    """§8: six numbers, reported separately."""

    def test_there_is_no_total(self) -> None:
        """A scalarisation would answer R7 by fiat, so there is none to read."""
        fields = set(_vector("hawkes").__slots__)
        assert not fields & {"total", "score", "combined", "overall"}

    def test_every_dimension_is_reported(self) -> None:
        vector = _vector("hawkes")
        for name in (
            "d1_structural_distance",
            "d2_held_out_predictive",
            "d3_intervention_similarity",
            "d4_explanatory_coverage",
            "d5_enabled_experiment_value",
            "d6_complexity",
        ):
            assert isinstance(getattr(vector, name), float)

    def test_d3_is_bounded(self) -> None:
        for name in sorted(closed_set()):
            similarity = _vector(name).d3_intervention_similarity
            assert 0.0 <= similarity <= 1.0

    def test_d4_is_never_negative(self) -> None:
        """§8 asks for *improvement*, so failing to improve costs nothing."""
        assert _vector("hawkes").d4_explanatory_coverage >= 0.0

    def test_an_empty_battery_reports_nan_rather_than_zero(self) -> None:
        """A question that was not asked must not look like a measurement of 0."""
        truth = scenario("S11").truth
        vector, grown = dimension_vector(
            closed_set()["hawkes"],
            truth,
            grammar=GRAMMAR,
            table=_table(),
            simulate=simulator(GRAMMAR),
            held_out=(),
        )
        _TABLE[0] = grown
        assert math.isnan(vector.d2_held_out_predictive)
        assert math.isnan(vector.d3_intervention_similarity)
        assert vector.n_held_out == 0
        assert vector.d5_enabled_experiment_value == 0.0


class TestPrimaryDimension:
    """§8's "primary interpretation by task", written down once."""

    def test_out_of_library_reads_d3(self) -> None:
        assert primary_dimension("out_of_library") == "d3_intervention_similarity"

    def test_compound_reads_d1(self) -> None:
        assert primary_dimension("compound") == "d1_structural_distance"

    def test_closed_world_classes_read_the_proper_score_instead(self) -> None:
        for name in ("single", "confounded", "null", "non_identifiable", "garden_path"):
            assert primary_dimension(name) is None

    def test_every_scenario_class_has_an_answer(self) -> None:
        """A class added later must not silently fall through to ``None``."""
        for name in SCENARIO_CLASSES:
            primary_dimension(name)


class TestJensenShannon:
    """The divergence D3 is built on."""

    def test_identical_distributions_diverge_by_zero(self) -> None:
        assert jensen_shannon_bits([0.2, 0.3, 0.5], [0.2, 0.3, 0.5]) == 0.0

    def test_disjoint_distributions_diverge_by_one_bit(self) -> None:
        """The bound is what makes averaging over a battery meaningful."""
        assert jensen_shannon_bits([1.0, 0.0], [0.0, 1.0]) == pytest.approx(1.0)

    def test_it_is_symmetric(self) -> None:
        """Neither ordering is privileged when comparing two explanations."""
        left, right = [0.7, 0.2, 0.1], [0.1, 0.3, 0.6]
        assert jensen_shannon_bits(left, right) == pytest.approx(
            jensen_shannon_bits(right, left)
        )

    def test_mismatched_lengths_raise(self) -> None:
        with pytest.raises(DiagnosisError, match="cannot compare"):
            jensen_shannon_bits([0.5, 0.5], [1.0])

    def test_no_cells_raise(self) -> None:
        with pytest.raises(DiagnosisError, match="no cells"):
            jensen_shannon_bits([], [])


class TestLeadingStructure:
    """What a system is scored on is what it concluded."""

    def _diagnosis(self, masses: dict[str, float]) -> Diagnosis:
        return Diagnosis(
            scenario_id=scenario("S11").id,
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId(k): Probability(v) for k, v in masses.items()}
            ),
            abstain_mass=Probability(1.0 - max(masses.values())),
            null_mass=Probability(masses.get("null", 0.0)),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )

    def test_it_returns_the_structure_carrying_the_most_mass(self) -> None:
        library = closed_set()
        diagnosis = self._diagnosis({"hawkes": 0.7, "null": 0.3})
        edits = {HypothesisId(k): library[k] for k in ("hawkes", "null")}
        assert leading_structure(diagnosis, edits) == library["hawkes"]

    def test_ties_break_to_the_first_id_not_to_iteration_order(self) -> None:
        """Two runs must agree, and a mapping's order is not a tiebreak.

        The rule is the lexicographically first hypothesis id. Which id wins is
        arbitrary; that it is the *same* id in every process is not, and it is
        what a dict's insertion order would not give.
        """
        library = closed_set()
        diagnosis = self._diagnosis({"hawkes": 0.5, "null": 0.5})
        forward = {HypothesisId(k): library[k] for k in ("hawkes", "null")}
        reversed_order = {HypothesisId(k): library[k] for k in ("null", "hawkes")}
        assert leading_structure(diagnosis, forward) == library["hawkes"]
        assert leading_structure(diagnosis, reversed_order) == library["hawkes"]

    def test_an_unknown_leader_raises(self) -> None:
        diagnosis = self._diagnosis({"mystery": 1.0})
        with pytest.raises(DiagnosisError, match="no known structure"):
            leading_structure(diagnosis, {})
