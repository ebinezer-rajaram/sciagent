"""Acceptance test A49: criterion 5 reads a quantity a proposal can move.

A49 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Criterion 5 compares two extensions on a dimension
that cannot see either"*, and reads:

    ``test_a49_criterion_five_reads_a_quantity_a_proposal_can_move`` -- on a
    matrix where the treatment's proposal is strictly closer to the truth than
    the comparator's, the criterion separates the two arms where
    D3-over-the-leader reports them equal; a matrix where neither proposal
    leads still yields a verdict rather than a tie by construction; and the
    non-overlap rule still fails a genuine overlap.

What was wrong
--------------

§12 criterion 5 asks whether V7 "proposes an S11 extension exceeding
B6-equivalent random structured generation", and read the answer off D3 --
which ``scoring.dimension_vector`` computes over the **leading** structure. A
proposed extension reaches that figure only by winning the posterior, and on
the recorded campaign none did, from any arm: the comparison tied to the last
bit, and could only ever tie, because the instrument was pointed at a quantity
adjacent to the one the criterion names.

The decision this gate encodes
------------------------------

Taken by the user on 2026-08-27 and recorded in ``docs/DECISIONS.md``:
criterion 5 reads ``ScenarioRun.structural_distance`` -- the distance from the
truth to the **nearest entertained** structure, a genuinely proposal-sensitive
quantity already recorded in every row -- keeping the B6 comparator and the
non-overlap rule. Distance runs the other way from D3: **lower is closer**, so
the criterion's direction is explicit in :func:`criterion_five` rather than
borrowed from :attr:`Contrast.exceeds`, whose ``>`` reads the wrong way here.

Disclosed on the BACKLOG entry and repeated here so no reader takes this gate
for a result: on the recorded campaign ``structural_distance`` is 1.000000 for
all eight arm-cells on S11 -- no proposal from any arm ever landed closer to
the truth than the best library member. Re-instrumenting cannot turn that
campaign into a pass; what it buys is a criterion whose failure means something
on a campaign where some arm's proposal did better.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from environments.pointproc.scenarios import scenario
from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    GrammarVersion,
    MetricVersion,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.eval.agency import AgencyMetrics
from sciagent.eval.campaign import Adjudication
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    battery_key,
    cell_key,
)
from sciagent.eval.report import (
    Contrast,
    CriterionFive,
    MatrixReport,
    contrast,
    criterion_five,
    summarise,
)
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.partitions import DataPartition

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

PLATFORM = "Windows-11-x86_64"
NUMPY = "2.5.1"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")

#: Criterion 5's coordinates, stated from SPEC §12 rather than imported from
#: the environment's declaration, so a drift in the declared constant cannot
#: silently re-aim this gate. B6 is the criterion's own comparator and is not
#: part of §9's matrix.
SCENARIO = ScenarioId("S11")
TREATMENT = "V7"
COMPARATOR = "B6"

#: The D3 value every row carries, in both arms. Constant *and identical
#: across arms* on purpose: the gate's first clause is that the criterion
#: separates two arms that D3-over-the-leader reports as equal, so the fixture
#: holds D3 equal by construction and lets ``structural_distance`` differ.
_D3 = 0.7


def _scenario_class(target: ScenarioId) -> ScenarioClass:
    """Classify off the environment's declaration, as the real caller does."""
    return scenario(str(target)).scenario_class


def _battery(target: ScenarioId) -> Sequence[ExperimentDesign]:
    """Return the scenario's declared held-out battery, as the real caller does."""
    return scenario(str(target)).held_out


def _reading(*, structural_distance: float) -> CellReading:
    """Return a reading whose only load-bearing field is the entertained distance.

    Every other number is stated rather than derived, exactly as
    ``test_a46.py`` builds its rows: the ledger stores a flat payload and
    ``summarise`` folds every field of one, so a row cannot be built without a
    whole vector, and none of the rest is read by this gate.
    """
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=1.5,
            d2_held_out_predictive=-1.0,
            d3_intervention_similarity=_D3,
            d4_explanatory_coverage=0.0,
            d5_enabled_experiment_value=0.0,
            d6_complexity=12.0,
            n_held_out=3,
            n_comparison=1,
        ),
        score=ClosedWorldScore(
            truth_mass=Probability(0.5),
            log_score=0.0,
            leading_mass=Probability(0.5),
            correct=True,
            identified=False,
        ),
        ppc_p_value=0.0,
        inadequate=False,
        probe_p_value=0.03,
        probe_inadequate=True,
        agency=AgencyMetrics(
            system="V7",
            scenario=SCENARIO,
            experiments=8,
            entertained=4,
            escalated=1,
            proposals=None,
            causes=None,
        ),
        adjudication=Adjudication(
            claims=80, adjudicated=80, contradictions=0, zombies=0
        ),
        null_mass=Probability(0.25),
        abstain_mass=Probability(0.5),
        max_defect_mass=0.4,
        experiments=8,
        structural_distance=structural_distance,
        battery=battery_key(()),
    )


def _row(
    *,
    target: ScenarioId,
    system: str,
    replicate: int,
    structural_distance: float,
) -> LedgerEntry:
    """Return one recorded row, seeded by its replicate index.

    Seed equals replicate for both arms, which is the pairing the criterion's
    comparator was recorded under: B6 ran on the same twenty seeds as V7's
    cell, the same battery and the same address.
    """
    task = CellTask(
        cell=Cell(system, target, 1), replicate=replicate, seed=Seed(replicate)
    )
    return LedgerEntry(
        key=cell_key(task, ADDRESS, battery=tuple(_battery(target))),
        reading=FrozenDict[str, float](
            dict(_reading(structural_distance=structural_distance).as_payload())
        ),
        sequence=replicate,
    )


def _report(
    *,
    treatment_distances: Sequence[float],
    comparator_distances: Sequence[float],
    target: ScenarioId = SCENARIO,
) -> MatrixReport:
    """Summarise a two-arm campaign with the stated per-replicate distances."""
    vectors = {TREATMENT: treatment_distances, COMPARATOR: comparator_distances}
    rows = [
        _row(
            target=target,
            system=system,
            replicate=index,
            structural_distance=distance,
        )
        for system, distances in vectors.items()
        for index, distance in enumerate(distances)
    ]
    return summarise(
        tuple(rows),
        address=ADDRESS,
        scenario_class=_scenario_class,
        battery=_battery,
        platform=PLATFORM,
        numpy_version=NUMPY,
        grammar=GRAMMAR,
    )


def _distance_contrast(
    report: MatrixReport, *, target: ScenarioId = SCENARIO
) -> Contrast:
    """Run criterion 5's contrast -- entertained distance, unconditioned."""
    return contrast(
        report,
        scenario=target,
        treatment=TREATMENT,
        comparator=COMPARATOR,
        dimension="structural_distance",
        conditional_on_inadequacy=False,
    )


class TestA49CriterionFiveReadsAQuantityAProposalCanMove:
    """§12 criterion 5 is read off the entertained set, not the leader."""

    def test_a49_criterion_five_reads_a_quantity_a_proposal_can_move(self) -> None:
        """The gate: a strictly closer proposal separates arms D3 calls equal.

        The treatment's entertained set reaches 0.25 from the truth on every
        seed; the comparator's never leaves the library's 1.0. D3 is identical
        across arms by construction -- asserted, not assumed, via a second
        contrast on the same report -- so the old instrument reports this
        matrix as the recorded campaign's tie while the criterion now holds:
        closer, with non-overlapping intervals.
        """
        report = _report(
            treatment_distances=(0.25, 0.25, 0.25, 0.25),
            comparator_distances=(1.0, 1.0, 1.0, 1.0),
        )
        verdict = criterion_five(_distance_contrast(report))
        assert verdict.holds
        assert verdict.closer
        assert not verdict.overlaps
        # The verdict says which arms it compared -- `closer` is directional,
        # and a three-boolean verdict off a swapped contrast would state the
        # criterion's inverse with nothing downstream able to tell.
        assert verdict.treatment_system == TREATMENT
        assert verdict.comparator_system == COMPARATOR

        leader = contrast(
            report,
            scenario=SCENARIO,
            treatment=TREATMENT,
            comparator=COMPARATOR,
            dimension="d3_intervention_similarity",
            conditional_on_inadequacy=False,
        )
        assert leader.treatment.point == leader.comparator.point

    def test_a49_a_matrix_where_neither_proposal_leads_still_yields_a_verdict(
        self,
    ) -> None:
        """The gate's second clause: a genuine tie is a verdict, not an artefact.

        Both arms sit on the library's 1.0 -- the recorded campaign's own
        shape. The criterion fails, and fails *because the quantity tied*,
        which is information: under the old instrument the same figure was a
        tie by construction and its failure meant only that no extension led.
        ``closer`` is False and asserted, so the verdict carries its reason.
        """
        verdict = criterion_five(
            _distance_contrast(
                _report(
                    treatment_distances=(1.0, 1.0, 1.0, 1.0),
                    comparator_distances=(1.0, 1.0, 1.0, 1.0),
                )
            )
        )
        assert not verdict.holds
        assert not verdict.closer

    def test_a49_the_non_overlap_rule_still_fails_a_genuine_overlap(self) -> None:
        """The gate's third clause: direction alone is not the criterion.

        The treatment is closer on the mean -- 0.6 against 0.8 -- but both
        arms carry spread and two replicates, so the 95% intervals overlap
        broadly. The criterion demands both: strictly closer *and*
        non-overlapping, exactly as the wording it re-instruments did.
        """
        verdict = criterion_five(
            _distance_contrast(
                _report(
                    treatment_distances=(0.2, 1.0),
                    comparator_distances=(0.4, 1.2),
                )
            )
        )
        assert not verdict.holds
        assert verdict.closer
        assert verdict.overlaps

    def test_a49_a_strictly_farther_proposal_fails(self) -> None:
        """Direction, on the side no other case reaches: farther is a failure.

        The mirror of the gate's first clause -- the treatment sits on 1.0
        while the comparator's proposal reached 0.25, intervals disjoint. A
        direction-blind reading of "separates the two arms", ``closer =
        (points differ)``, passes every other case in this module and holds
        here; ``/test-review`` built it. Distance runs the other way from
        every dimension in the report, so the upper endpoint has to be pinned
        explicitly: strictly farther is ``closer=False`` and a failing
        verdict, however cleanly the intervals separate.
        """
        verdict = criterion_five(
            _distance_contrast(
                _report(
                    treatment_distances=(1.0, 1.0, 1.0, 1.0),
                    comparator_distances=(0.25, 0.25, 0.25, 0.25),
                )
            )
        )
        assert not verdict.holds
        assert not verdict.closer
        assert not verdict.overlaps

    def test_a49_the_leaders_dimension_is_refused(self) -> None:
        """A criterion-5 verdict over D3 raises rather than computing.

        The heart of the defect being closed: D3 is a function of the leading
        structure and cannot see a proposal that does not win the posterior.
        An implementation that accepted any dimension would let the old
        instrument back in under the new name, so the refusal names the
        quantity the criterion reads.
        """
        report = _report(
            treatment_distances=(0.25, 0.25),
            comparator_distances=(1.0, 1.0),
        )
        leader = contrast(
            report,
            scenario=SCENARIO,
            treatment=TREATMENT,
            comparator=COMPARATOR,
            dimension="d3_intervention_similarity",
            conditional_on_inadequacy=False,
        )
        with pytest.raises(MalformedDesignError, match="structural_distance"):
            criterion_five(leader)

    def test_a49_a_conditioned_contrast_is_refused(self) -> None:
        """Criterion 5 states no conditioning event, so a conditioned reading
        answers a different question under the same name and is refused.

        The rows here carry a firing probe on every replicate, so the
        conditioned contrast *exists* -- the refusal is about which question it
        answers, not about an empty population arriving first.

        This depends on gate A48, which lands in the same change: conditioning
        reads the Stage A probe (``probe_inadequate``, all True in this
        fixture), not the arm's own whole-record check (``inadequate``, all
        False here). Against the pre-A48 filter this ``contrast`` call itself
        refuses -- reviewed and accepted, because the tree this test goes
        green on carries both gates or neither.
        """
        conditioned = contrast(
            _report(
                treatment_distances=(0.25, 0.25),
                comparator_distances=(1.0, 1.0),
            ),
            scenario=SCENARIO,
            treatment=TREATMENT,
            comparator=COMPARATOR,
            dimension="structural_distance",
            conditional_on_inadequacy=True,
        )
        with pytest.raises(MalformedDesignError, match="condition"):
            criterion_five(conditioned)

    def test_a49_a_scenario_other_than_s11_is_refused(self) -> None:
        """Criterion 5 is an S11 claim, and the verdict will not travel.

        S11 is the out-of-library scenario -- the one whose truth a proposal
        can approach and a library cannot reach. A criterion-5 verdict on any
        other scenario grades nothing the criterion names, so it is refused
        with the scenario in the message.
        """
        s1 = ScenarioId("S1")
        off_target = _distance_contrast(
            _report(
                treatment_distances=(0.25, 0.25),
                comparator_distances=(1.0, 1.0),
                target=s1,
            ),
            target=s1,
        )
        with pytest.raises(MalformedDesignError, match="S11"):
            criterion_five(off_target)

    def test_a49_a_censored_arm_is_refused(self) -> None:
        """An arm whose summary excluded an infinite distance has no verdict.

        ``structural_distance`` is ``inf`` on a replicate that entertained
        nothing at all, and the interval machinery excludes non-finite values
        -- a design built for D2, where ``-inf`` is a legitimate score. On a
        distance that exclusion censors an arm's *worst* outcomes, in the
        direction that favours the arm that failed most: here the comparator
        entertained nothing on two of four seeds and reached 0.25 on the other
        two, so its censored summary reads 0.25 against the treatment's honest
        0.5 -- a "win" manufactured entirely by the censoring. Found by
        ``/code-review``; the refusal is the fix.
        """
        with pytest.raises(MalformedDesignError, match="censored"):
            criterion_five(
                _distance_contrast(
                    _report(
                        treatment_distances=(0.5, 0.5, 0.5, 0.5),
                        comparator_distances=(0.25, 0.25, math.inf, math.inf),
                    )
                )
            )

    def test_a49_a_verdict_cannot_contradict_itself(self) -> None:
        """``holds`` may not disagree with the two facts it is a conjunction of.

        The same ``__post_init__`` discipline as :class:`CriterionFour`: a
        frozen dataclass of three booleans and two arm names is exactly where
        a comment would otherwise have been the whole of the invariant. The
        third case: a direction between one arm and itself is not a
        comparison.
        """
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFive(
                holds=True,
                closer=True,
                overlaps=True,
                treatment_system="V7",
                comparator_system="B6",
            )
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFive(
                holds=False,
                closer=True,
                overlaps=False,
                treatment_system="V7",
                comparator_system="B6",
            )
        with pytest.raises(MalformedDesignError, match="itself"):
            CriterionFive(
                holds=False,
                closer=False,
                overlaps=True,
                treatment_system="V7",
                comparator_system="V7",
            )
