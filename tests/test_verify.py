"""Backlog item 10's unit coverage: the verifier below the level A19-A23 reach.

The acceptance suite pins the five criteria SPEC §6.5 states. Four of the seven
check classes have no criterion of their own -- scope, statistical, logical
beyond A22, and contradiction -- and their rules are readings this repository
fixed rather than ones the specification handed down. Those readings are what is
tested here, so that changing one is a visible change and not a quiet one.

Also here: the hypothesis-graph relations item 10 added, and the verdict
arithmetic every check's result passes through.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from claim_world import (
    ARRIVAL,
    METRIC,
    OBS,
    SIGN,
    SIZE,
    claim,
    graph_of,
    index,
    intervention,
    record,
    registered_world,
    scope,
    supported_claim,
)

from environments.pointproc import reference_program
from environments.pointproc.outcomes import closed_set
from sciagent.core.errors import (
    EstimandError,
    MalformedClaimError,
    UnknownHypothesisError,
)
from sciagent.core.types import (
    AssumptionCode,
    Claim,
    ClaimStrength,
    ControlledDirectEffect,
    DirectEffect,
    Direction,
    EffectEstimate,
    ExperimentId,
    FamilyId,
    FrozenDict,
    HypothesisId,
    MetricName,
    PathSpecificEffect,
    Probability,
    RejectionCode,
    TotalEffect,
)
from sciagent.hypothesis.graph import HypothesisGraph, Relation
from sciagent.verify import CheckClass, ClaimContext, Outcome, verify
from sciagent.verify.causal import ladder, license
from sciagent.verify.contradiction import check as contradiction_check
from sciagent.verify.logical import check as logical_check
from sciagent.verify.numerical import CONFIDENCE_LEVEL, arms, recompute
from sciagent.verify.relevance import EvidenceRecord
from sciagent.verify.scope import covers
from sciagent.verify.statistical import check as statistical_check
from sciagent.verify.verdict import DECISIVENESS, Finding, Verdict, worst


def _context(
    *,
    graph: HypothesisGraph | None = None,
    records: tuple[EvidenceRecord, ...] = (),
    accepted: tuple[Claim, ...] = (),
    posterior: dict[str, float] | None = None,
) -> ClaimContext:
    """Return a context over constructed evidence."""
    return ClaimContext(
        graph=graph if graph is not None else graph_of(closed_set()),
        evidence=index(*records),
        program=reference_program(),
        accepted=accepted,
        posterior=FrozenDict[HypothesisId, Probability](
            {HypothesisId(k): Probability(v) for k, v in (posterior or {}).items()}
        ),
    )


# --------------------------------------------------------------------------
# Verdict arithmetic
# --------------------------------------------------------------------------


class TestVerdictArithmetic:
    """A verdict is the most decisive thing any check said, and nothing gentler."""

    def test_no_findings_is_acceptance(self) -> None:
        assert worst(()) is Outcome.ACCEPT

    @pytest.mark.parametrize("outcome", DECISIVENESS)
    def test_every_outcome_beats_acceptance_or_is_it(self, outcome: Outcome) -> None:
        assert worst((Outcome.ACCEPT, outcome)) is outcome

    def test_rejection_outranks_referral(self) -> None:
        """A claim refused mechanically has been decided, whatever else is open."""
        assert worst((Outcome.REFER, Outcome.REJECT)) is Outcome.REJECT

    def test_a_verdict_cannot_be_gentler_than_its_findings(self) -> None:
        with pytest.raises(AssertionError, match="findings imply"):
            Verdict(
                claim=claim().id,
                outcome=Outcome.ACCEPT,
                findings=(Finding(CheckClass.SCOPE, Outcome.REJECT, "out of scope"),),
                licensed=None,
                recomputed=None,
            )

    def test_a_downgraded_claim_still_stands(self) -> None:
        """SPEC §7.2 downgrades so the weaker claim survives, not so it dies."""
        verdict = Verdict(
            claim=claim().id,
            outcome=Outcome.DOWNGRADE,
            findings=(Finding(CheckClass.CAUSAL, Outcome.DOWNGRADE, "weaker"),),
            licensed=TotalEffect(ARRIVAL, OBS),
            recomputed=None,
        )
        assert verdict.accepted
        assert verdict.adjudicated

    def test_a_referred_claim_is_not_adjudicated(self) -> None:
        verdict = Verdict(
            claim=claim().id,
            outcome=Outcome.REFER,
            findings=(Finding(CheckClass.STATISTICAL, Outcome.REFER, "no channel"),),
            licensed=None,
            recomputed=None,
        )
        assert not verdict.adjudicated
        assert not verdict.accepted


# --------------------------------------------------------------------------
# Hypothesis graph relations
# --------------------------------------------------------------------------


class TestGraphRelations:
    """Item 10's addition to item 5's graph."""

    def test_a_relation_is_symmetric(self) -> None:
        graph = graph_of(closed_set()).relate(
            HypothesisId("hawkes"), HypothesisId("null"), Relation.CONTRADICTS
        )
        assert graph.relation(HypothesisId("hawkes"), HypothesisId("null")) is (
            Relation.CONTRADICTS
        )
        assert graph.relation(HypothesisId("null"), HypothesisId("hawkes")) is (
            Relation.CONTRADICTS
        )

    def test_relating_does_not_move_the_prior(self) -> None:
        """The prior is a statement about structure; a relation is not evidence."""
        before = graph_of(closed_set())
        after = before.relate(
            HypothesisId("hawkes"), HypothesisId("null"), Relation.ALTERNATIVE_TO
        )
        assert {
            node_id: after.nodes[node_id].plausibility for node_id in after.nodes
        } == {node_id: before.nodes[node_id].plausibility for node_id in before.nodes}

    def test_hops_counts_relations_and_is_zero_for_a_node_and_itself(self) -> None:
        graph = (
            graph_of(closed_set())
            .relate(
                HypothesisId("hawkes"), HypothesisId("null"), Relation.ALTERNATIVE_TO
            )
            .relate(
                HypothesisId("null"),
                HypothesisId("regime_switching"),
                Relation.ALTERNATIVE_TO,
            )
        )
        assert graph.hops(HypothesisId("hawkes"), HypothesisId("hawkes")) == 0
        assert graph.hops(HypothesisId("hawkes"), HypothesisId("null")) == 1
        assert graph.hops(HypothesisId("hawkes"), HypothesisId("regime_switching")) == 2
        assert graph.hops(HypothesisId("hawkes"), HypothesisId("seasonality")) is None

    def test_a_node_cannot_be_related_to_itself(self) -> None:
        with pytest.raises(UnknownHypothesisError, match="itself"):
            graph_of(closed_set()).relate(
                HypothesisId("hawkes"), HypothesisId("hawkes"), Relation.CONTRADICTS
            )

    def test_a_relation_must_name_nodes_the_graph_holds(self) -> None:
        with pytest.raises(UnknownHypothesisError):
            graph_of(closed_set()).relate(
                HypothesisId("hawkes"), HypothesisId("absent"), Relation.CONTRADICTS
            )

    def test_relations_survive_a_status_transition(self) -> None:
        """Every transition rebuilds the graph; none of them may drop an edge."""
        graph = graph_of(closed_set()).relate(
            HypothesisId("hawkes"), HypothesisId("null"), Relation.CONTRADICTS
        )
        rejected = graph.reject(HypothesisId("hawkes"), RejectionCode.DUPLICATE)
        assert (
            rejected.relation(HypothesisId("hawkes"), HypothesisId("null"))
            is Relation.CONTRADICTS
        )


# --------------------------------------------------------------------------
# Numerical, below A19
# --------------------------------------------------------------------------


class TestNumerical:
    """What A19 rests on: where the arms come from and what is derived."""

    def test_arms_split_by_what_was_manipulated(self) -> None:
        world = registered_world()
        subject = supported_claim(world)
        treated, control = arms(subject, world.evidence, METRIC)
        assert {r.experiment for r in treated} == set(world.treated)
        assert {r.experiment for r in control} == set(world.control)

    def test_an_effect_needs_both_arms(self) -> None:
        world = registered_world()
        observational = claim(
            evidence=[str(name) for name in world.control],
            modality="causal",
            estimand=TotalEffect(ARRIVAL, SIZE),
            intervention=intervention(TotalEffect(ARRIVAL, SIZE)),
        )
        assert recompute(observational, world.evidence, metric=METRIC) is None

    def test_the_derived_level_is_the_frameworks_and_not_the_claims(self) -> None:
        world = registered_world()
        derived = recompute(supported_claim(world), world.evidence, metric=METRIC)
        assert derived is not None
        assert derived.level == CONFIDENCE_LEVEL

    def test_recomputation_is_reproducible(self) -> None:
        world = registered_world()
        subject = supported_claim(world)
        first = recompute(subject, world.evidence, metric=METRIC)
        second = recompute(subject, world.evidence, metric=METRIC)
        assert first == second

    def test_citing_an_unregistered_experiment_is_an_error_not_a_verdict(self) -> None:
        """A claim that cites nothing real does not denote; it cannot be graded."""
        with pytest.raises(MalformedClaimError, match="not in the evidence index"):
            recompute(
                claim(evidence=["nowhere"], effect=None),
                index(record("somewhere", sequence=1)),
                metric=METRIC,
            )


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


class TestScope:
    """Containment, not overlap. A claim must not outrun where it was measured."""

    def test_a_claim_inside_its_evidence_is_covered(self) -> None:
        evidence = scope(parameters={"rate": (0.5, 2.0)})
        assert covers(evidence, scope(parameters={"rate": (1.0, 1.5)})) == ()

    def test_a_wider_parameter_range_is_not_covered(self) -> None:
        evidence = scope(parameters={"rate": (0.5, 2.0)})
        unsupported = covers(evidence, scope(parameters={"rate": (0.5, 9.0)}))
        assert len(unsupported) == 1
        assert "rate" in unsupported[0]

    def test_an_unmeasured_parameter_is_not_covered(self) -> None:
        evidence = scope(parameters={"rate": (0.5, 2.0)})
        unsupported = covers(evidence, scope(parameters={"decay": (0.0, 1.0)}))
        assert unsupported == ("parameter 'decay', unmeasured",)

    def test_a_family_the_evidence_never_ran_is_not_covered(self) -> None:
        evidence = scope(families=frozenset({FamilyId("poisson_homogeneous")}))
        unsupported = covers(
            evidence, scope(families=frozenset({FamilyId("hawkes_exponential")}))
        )
        assert len(unsupported) == 1
        assert "hawkes_exponential" in unsupported[0]

    def test_another_environment_version_is_not_covered(self) -> None:
        unsupported = covers(scope(env_version="a"), scope(env_version="b"))
        assert any("environment version" in reason for reason in unsupported)

    def test_evidence_from_two_environments_supports_neither(self) -> None:
        subject = claim(evidence=["one", "two"], claim_scope=scope(env_version="a"))
        context = _context(
            records=(
                record("one", sequence=1, evidence_scope=scope(env_version="a")),
                record("two", sequence=2, evidence_scope=scope(env_version="b")),
            )
        )
        verdict = verify(subject, context)
        assert any(
            finding.check is CheckClass.SCOPE and finding.outcome is Outcome.REJECT
            for finding in verdict.findings
        )


# --------------------------------------------------------------------------
# Statistical
# --------------------------------------------------------------------------


def _effect(
    point: float, standard_error: float, *, n: int = 3, level: float = 0.95
) -> EffectEstimate:
    """Return a well-formed effect with an explicit interval."""
    half = 1.959963984540054 * standard_error
    return EffectEstimate(
        metric=METRIC,
        point=point,
        standard_error=standard_error,
        low=point - half,
        high=point + half,
        level=level,
        n_treated=n,
        n_control=n,
        direction=(
            Direction.INCREASE
            if point > 0
            else Direction.DECREASE
            if point < 0
            else Direction.NO_CHANGE
        ),
    )


class TestStatisticalStrengthLadder:
    """The reading this repository fixed for SPEC §3.3's four strengths."""

    @pytest.mark.parametrize(
        ("strength", "effect", "accepted"),
        [
            ("suggests", _effect(0.5, 1.0), True),
            ("suggests", _effect(0.0, 1.0), False),
            ("supports", _effect(0.5, 1.0), False),
            ("supports", _effect(0.5, 0.1), True),
            ("establishes", _effect(0.5, 0.1), True),
            ("establishes", _effect(0.5, 0.1, n=1), False),
            ("establishes", _effect(0.5, 1.0), False),
        ],
    )
    def test_the_ladder(
        self, strength: ClaimStrength, effect: EffectEstimate, accepted: bool
    ) -> None:
        subject = replace(claim(strength=strength), effect=effect)
        findings = statistical_check(subject, _context())
        assert (findings == ()) is accepted, f"{strength}: {findings!r}"

    def test_a_zero_standard_error_reaches_no_further_than_suggests(self) -> None:
        """One run per arm has no sampling variability, so its interval is not one."""
        subject = replace(claim(strength="supports"), effect=_effect(0.5, 0.0, n=1))
        findings = statistical_check(subject, _context())
        assert findings and "standard error of zero" in findings[0].message

    def test_refutation_needs_a_preregistered_direction_to_contradict(self) -> None:
        subject = replace(claim(strength="refutes"), effect=_effect(0.5, 0.1))
        findings = statistical_check(subject, _context())
        assert findings and findings[0].outcome is Outcome.REFER

    def test_refutation_is_granted_against_the_preregistered_direction(self) -> None:
        estimand = TotalEffect(ARRIVAL, SIZE)
        subject = replace(
            claim(
                strength="refutes",
                modality="causal",
                estimand=estimand,
                intervention=intervention(estimand, expected=Direction.INCREASE),
            ),
            effect=_effect(-0.5, 0.1),
        )
        assert statistical_check(subject, _context()) == ()

    def test_refutation_is_refused_when_the_effect_went_as_predicted(self) -> None:
        estimand = TotalEffect(ARRIVAL, SIZE)
        subject = replace(
            claim(
                strength="refutes",
                modality="causal",
                estimand=estimand,
                intervention=intervention(estimand, expected=Direction.INCREASE),
            ),
            effect=_effect(0.5, 0.1),
        )
        findings = statistical_check(subject, _context())
        assert findings and findings[0].outcome is Outcome.REJECT

    def test_a_claim_with_no_channel_at_all_is_referred(self) -> None:
        """Neither a measured effect nor a prediction the evidence bears on."""
        subject = claim(evidence=["e"], effect=None)
        context = _context(
            records=(record("e", sequence=1, template="query:something_else"),)
        )
        findings = statistical_check(subject, context)
        assert findings and findings[0].outcome is Outcome.REFER

    def test_the_prediction_channel_grades_a_claim_with_no_effect(self) -> None:
        """A hypothesis's own prediction, evaluated on a bearing experiment."""
        confirming = record("e", sequence=1, value=1.0)
        refuting = record("f", sequence=2, value=9.0)
        context = _context(records=(confirming, refuting))
        assert statistical_check(claim(evidence=["e"]), context) == ()
        refused = statistical_check(claim(evidence=["f"]), context)
        assert refused and refused[0].outcome is Outcome.REJECT

    def test_an_experiment_that_refuted_the_subject_cannot_also_support_it(
        self,
    ) -> None:
        context = _context(
            records=(
                record("e", sequence=1, value=1.0),
                record("f", sequence=2, value=9.0),
            )
        )
        findings = statistical_check(
            claim(strength="supports", evidence=["e", "f"]), context
        )
        assert findings and "refuted the subject" in findings[0].message


# --------------------------------------------------------------------------
# Logical
# --------------------------------------------------------------------------


class TestLogical:
    """Field coherence and the uniqueness rule. A22 is in the acceptance suite."""

    def test_a_causal_claim_needs_an_estimand(self) -> None:
        subject = claim(modality="causal", evidence=["e"], estimand=None)
        findings = logical_check(subject, _context(records=(record("e", sequence=1),)))
        assert any("names no estimand" in finding.message for finding in findings)

    def test_a_causal_claim_needs_an_intervention(self) -> None:
        subject = claim(
            modality="causal",
            evidence=["e"],
            estimand=TotalEffect(ARRIVAL, SIZE),
            intervention=None,
        )
        findings = logical_check(subject, _context(records=(record("e", sequence=1),)))
        assert any(
            "declares no intervention" in finding.message for finding in findings
        )

    def test_a_claim_citing_nothing_is_refused(self) -> None:
        findings = logical_check(claim(evidence=[]), _context())
        assert any("cites no experiment" in finding.message for finding in findings)

    def test_a_non_causal_claim_naming_an_estimand_is_noted_not_refused(self) -> None:
        subject = claim(
            modality="correlational",
            evidence=["e"],
            estimand=TotalEffect(ARRIVAL, SIZE),
        )
        findings = logical_check(subject, _context(records=(record("e", sequence=1),)))
        noted = [f for f in findings if f.outcome is Outcome.NOTE]
        assert noted and "carries no licence here" in noted[0].message

    def test_exclusivity_needs_more_than_half_the_posterior(self) -> None:
        context = _context(
            records=(record("e", sequence=1),), posterior={"hawkes": 0.4, "null": 0.6}
        )
        findings = logical_check(claim(evidence=["e"], uniqueness="exclusive"), context)
        assert any(finding.outcome is Outcome.REJECT for finding in findings)

    def test_exclusivity_is_granted_to_a_dominant_subject(self) -> None:
        context = _context(
            records=(record("e", sequence=1),), posterior={"hawkes": 0.9, "null": 0.1}
        )
        findings = logical_check(claim(evidence=["e"], uniqueness="exclusive"), context)
        assert [f for f in findings if f.outcome is Outcome.REJECT] == []

    def test_exclusivity_without_a_posterior_is_referred(self) -> None:
        findings = logical_check(
            claim(evidence=["e"], uniqueness="exclusive"),
            _context(records=(record("e", sequence=1),)),
        )
        assert any(finding.outcome is Outcome.REFER for finding in findings)


# --------------------------------------------------------------------------
# Contradiction
# --------------------------------------------------------------------------


class TestContradiction:
    """SPEC §4.6 requirement 5, made checkable."""

    def test_supporting_a_rejected_hypothesis_is_a_zombie(self) -> None:
        graph = graph_of(closed_set()).reject(
            HypothesisId("hawkes"), RejectionCode.UNSATISFIABLE_REFUTATION
        )
        findings = contradiction_check(
            claim(evidence=["e"], strength="supports"),
            _context(graph=graph, records=(record("e", sequence=1),)),
        )
        assert findings and "zombie" in findings[0].message

    def test_refuting_a_rejected_hypothesis_is_fine(self) -> None:
        graph = graph_of(closed_set()).reject(
            HypothesisId("hawkes"), RejectionCode.UNSATISFIABLE_REFUTATION
        )
        findings = contradiction_check(
            claim(evidence=["e"], strength="refutes"),
            _context(graph=graph, records=(record("e", sequence=1),)),
        )
        assert findings == ()

    def test_a_suspended_hypothesis_may_be_argued_for_again(self) -> None:
        """Suspension is 'pending further evidence'; this is further evidence."""
        graph = graph_of(closed_set()).suspend(
            HypothesisId("hawkes"), RejectionCode.UNSATISFIABLE_CONDITION
        )
        findings = contradiction_check(
            claim(evidence=["e"], strength="supports"),
            _context(graph=graph, records=(record("e", sequence=1),)),
        )
        assert findings == ()

    def test_supporting_both_sides_of_a_contradiction_is_refused(self) -> None:
        graph = graph_of(closed_set()).relate(
            HypothesisId("hawkes"),
            HypothesisId("regime_switching"),
            Relation.CONTRADICTS,
        )
        already = claim(
            subject="regime_switching",
            strength="supports",
            evidence=["e"],
            claim_id="C0",
        )
        findings = contradiction_check(
            claim(subject="hawkes", strength="supports", evidence=["e"]),
            _context(
                graph=graph, records=(record("e", sequence=1),), accepted=(already,)
            ),
        )
        assert findings and "contradict" in findings[0].message

    def test_mere_alternatives_may_both_be_supported(self) -> None:
        """``AlternativeTo`` is rivalry, not incompatibility."""
        graph = graph_of(closed_set()).relate(
            HypothesisId("hawkes"),
            HypothesisId("regime_switching"),
            Relation.ALTERNATIVE_TO,
        )
        already = claim(
            subject="regime_switching",
            strength="supports",
            evidence=["e"],
            claim_id="C0",
        )
        findings = contradiction_check(
            claim(subject="hawkes", strength="supports", evidence=["e"]),
            _context(
                graph=graph, records=(record("e", sequence=1),), accepted=(already,)
            ),
        )
        assert findings == ()

    def test_the_same_estimand_may_not_go_two_ways(self) -> None:
        estimand = TotalEffect(ARRIVAL, SIZE)
        already = replace(
            claim(
                modality="causal",
                estimand=estimand,
                intervention=intervention(estimand),
                evidence=["e"],
                claim_id="C0",
            ),
            effect=_effect(1.0, 0.1),
        )
        reversed_claim = replace(
            claim(
                modality="causal",
                estimand=estimand,
                intervention=intervention(estimand),
                evidence=["e"],
                claim_id="C1",
            ),
            effect=_effect(-1.0, 0.1),
        )
        findings = contradiction_check(
            reversed_claim,
            _context(records=(record("e", sequence=1),), accepted=(already,)),
        )
        assert findings and "same estimand" in findings[0].message


# --------------------------------------------------------------------------
# Causal, below A21
# --------------------------------------------------------------------------


class TestCausalLadder:
    """How a misaligned claim is downgraded, and to what."""

    def test_a_total_effect_has_nothing_weaker_to_fall_back_to(self) -> None:
        assert ladder(TotalEffect(ARRIVAL, OBS)) == (TotalEffect(ARRIVAL, OBS),)

    def test_a_direct_effect_falls_back_to_the_total_effect(self) -> None:
        estimand = DirectEffect(ARRIVAL, OBS, frozenset({SIZE}))
        assert ladder(estimand) == (estimand, TotalEffect(ARRIVAL, OBS))

    def test_a_controlled_direct_effect_falls_back_through_the_direct_one(self) -> None:
        estimand = ControlledDirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN}))
        assert ladder(estimand) == (
            estimand,
            DirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN})),
            TotalEffect(ARRIVAL, OBS),
        )

    def test_the_ladder_invents_no_rung_the_claim_did_not_describe(self) -> None:
        """A path-specific claim has no mediator set to fall back through."""
        estimand = PathSpecificEffect(ARRIVAL, OBS, (ARRIVAL, SIZE, OBS))
        assert ladder(estimand) == (estimand, TotalEffect(ARRIVAL, OBS))

    def test_a_non_causal_claim_is_licensed_and_refused_nothing(self) -> None:
        granted = license(
            claim(modality="correlational", evidence=["e"]),
            reference_program(),
            (record("e", sequence=1),),
        )
        assert granted.licensed is None
        assert granted.findings == ()

    def test_an_estimand_may_not_name_one_component_twice(self) -> None:
        with pytest.raises(EstimandError, match="both its target and its outcome"):
            TotalEffect(ARRIVAL, ARRIVAL)

    def test_a_path_must_run_between_the_endpoints_it_declares(self) -> None:
        with pytest.raises(EstimandError, match="does not run from"):
            PathSpecificEffect(ARRIVAL, OBS, (ARRIVAL, SIZE, SIGN))


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


class TestEndToEnd:
    """The whole pipeline over a claim the registry really supports."""

    def test_a_well_founded_claim_is_accepted(self) -> None:
        world = registered_world()
        subject = supported_claim(world)
        verdict = verify(
            subject,
            ClaimContext(
                graph=world.graph, evidence=world.evidence, program=world.program
            ),
        )
        assert verdict.accepted, [f.message for f in verdict.findings]
        assert verdict.licensed == subject.estimand

    def test_every_check_runs_whatever_the_others_found(self) -> None:
        """A verdict names every ground a claim failed on, not the first."""
        world = registered_world()
        honest = supported_claim(world)
        assert honest.effect is not None
        broken = replace(
            honest,
            effect=replace(honest.effect, point=99.0),
            scope=scope(env_version="elsewhere"),
        )
        verdict = verify(
            broken,
            ClaimContext(
                graph=world.graph, evidence=world.evidence, program=world.program
            ),
        )
        checks = {finding.check for finding in verdict.findings}
        assert CheckClass.NUMERICAL in checks
        assert CheckClass.SCOPE in checks

    def test_an_uncited_relevant_experiment_and_a_bad_figure_both_appear(self) -> None:
        world = registered_world()
        partial = replace(
            supported_claim(world), evidence=(world.cited[0],), id=claim().id
        )
        verdict = verify(
            partial,
            ClaimContext(
                graph=world.graph, evidence=world.evidence, program=world.program
            ),
        )
        assert verdict.outcome is Outcome.REJECT
        assert CheckClass.COMPLETENESS in {f.check for f in verdict.findings}


def test_experiment_ids_are_what_a_claim_cites() -> None:
    """A guard on the shape of the record, since every check indexes by it."""
    world = registered_world()
    assert set(world.cited) == set(world.evidence.records)
    assert all(isinstance(name, str) for name in world.cited)
    assert ExperimentId(str(world.cited[0])) in world.evidence.records


def test_a_metric_the_experiment_did_not_measure_is_an_error() -> None:
    """Reading a neighbouring axis is the wrong number A19 exists to catch."""
    with pytest.raises(MalformedClaimError, match="not 'elsewhere'"):
        record("e", sequence=1).value(MetricName("elsewhere"))


def test_assumption_codes_are_only_those_the_licensing_table_reads() -> None:
    """An unread assumption accumulates and starts to look like a guarantee."""
    assert {code.value for code in AssumptionCode} == {
        "mediators_blocked",
        "held_fixed",
        "off_path_controlled",
    }
