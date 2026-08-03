"""Acceptance tests A19-A23 (SPEC §6.5): the verifier.

One test per criterion, named for it. These are the contract for backlog item 10.

Where each criterion is aimed
-----------------------------

A19, A20 and A23 are properties of the verifier as a whole and are tested through
:func:`sciagent.verify.verdict.verify`. A21 and A22 are properties of one check
class each, and are tested through that class directly: a causal licensing
decision reached correctly but reported under a verdict that some *other* check
had already rejected would satisfy an end-to-end test while telling us nothing
about licensing. Both also have end-to-end coverage in ``tests/test_verify.py``.

A23 before there is an agent
----------------------------

A23 measures "agent claims in slice runs", and SPEC §11 puts the verifier at item
10 and the agent at item 12, so no agent claim exists to measure. The gate is
discharged the way A14's was at item 4: against a declared surface, with the
controls that stop it passing vacuously. Claims are generated from the item 9
baseline runs by :func:`~sciagent.eval.campaign.claims_from_run`, which enumerates
the modality by strength cross-product for every hypothesis carrying mass. That
generator knows nothing about what the verifier can decide, so overreaching and
unsupportable claims are in the population by construction and referral is a real
possibility rather than a hypothetical one. The measured figure is recorded in
``docs/DECISIONS.md`` and is re-measured against real agent claims at item 12.
"""

from __future__ import annotations

import math
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
from sciagent.core.types import (
    AssumptionCode,
    Claim,
    ControlledDirectEffect,
    DirectEffect,
    Direction,
    EffectEstimate,
    Estimand,
    ExperimentId,
    HypothesisId,
    MetricName,
    PathSpecificEffect,
    TotalEffect,
)
from sciagent.hypothesis.graph import HypothesisGraph, Relation
from sciagent.verify import CheckClass, ClaimContext, Outcome, Verdict, verify
from sciagent.verify.causal import license
from sciagent.verify.logical import check as logical_check
from sciagent.verify.relevance import (
    EvidenceIndex,
    EvidenceRecord,
    RelevanceClause,
    survey,
)

# --------------------------------------------------------------------------
# A19 -- numerical
# --------------------------------------------------------------------------


def _float_deltas() -> tuple[float, ...]:
    """Return the additive corruptions applied to each float figure.

    Spanning twenty-three orders of magnitude in both directions, so the gate
    covers a corruption that moves a figure in its last few decimal places as
    well as one that replaces it outright. The small end is what a
    tolerance-based comparison would wave through, and waving it through is the
    failure A19 exists to prevent.
    """
    return tuple(
        sign * 10.0**exponent for exponent in range(-15, 9) for sign in (1.0, -1.0)
    )


def _corrupted_effects(
    effect: EffectEstimate,
) -> tuple[tuple[str, EffectEstimate], ...]:
    """Return every corrupted copy of ``effect``, labelled by what was corrupted.

    A delta too small to move a field leaves the copy equal to the original, and
    an unchanged figure is not a corruption; those are dropped rather than
    counted, so the tally A19 asserts against is a tally of real ones.
    """
    corruptions: list[tuple[str, EffectEstimate]] = []
    for delta in _float_deltas():
        for name, corrupted in (
            ("point", replace(effect, point=effect.point + delta)),
            (
                "standard_error",
                replace(effect, standard_error=effect.standard_error + delta),
            ),
            ("low", replace(effect, low=effect.low + delta)),
            ("high", replace(effect, high=effect.high + delta)),
        ):
            if corrupted != effect:
                corruptions.append((f"{name}{delta:+.0e}", corrupted))
    for level in (0.5, 0.8, 0.9, 0.99, 0.999):
        corruptions.append((f"level={level}", replace(effect, level=level)))
    for count in (-2, -1, 1, 2, 5):
        corruptions.append(
            (
                f"n_treated{count:+d}",
                replace(effect, n_treated=effect.n_treated + count),
            )
        )
        corruptions.append(
            (
                f"n_control{count:+d}",
                replace(effect, n_control=effect.n_control + count),
            )
        )
    for direction in Direction:
        if direction is not effect.direction:
            corruptions.append(
                (f"direction={direction.value}", replace(effect, direction=direction))
            )
    for metric in ("size_dispersion", "phase_conditioned_dispersion"):
        corruptions.append(
            (f"metric={metric}", replace(effect, metric=MetricName(metric)))
        )
    return tuple(corruptions)


class TestA19Numerical:
    """A19: 200 claims with deliberately corrupted figures are all rejected."""

    def test_a19_an_uncorrupted_claim_is_not_rejected_numerically(self) -> None:
        """The positive control. Without it "all rejected" is met by rejecting all."""
        world = registered_world()
        context = ClaimContext(
            graph=world.graph, evidence=world.evidence, program=world.program
        )
        verdict = verify(supported_claim(world), context)
        numerical = [
            finding
            for finding in verdict.findings
            if finding.check is CheckClass.NUMERICAL
        ]
        assert numerical == [], f"clean claim drew numerical findings: {numerical!r}"

    def test_a19_every_corrupted_figure_is_rejected(self) -> None:
        """At least 200 corrupted claims, every one rejected on numerical grounds."""
        world = registered_world()
        context = ClaimContext(
            graph=world.graph, evidence=world.evidence, program=world.program
        )
        honest = supported_claim(world)
        assert honest.effect is not None
        corruptions = _corrupted_effects(honest.effect)
        assert len(corruptions) >= 200, (
            f"A19 requires 200 corrupted claims; the enumeration produced "
            f"{len(corruptions)}"
        )

        survivors: list[str] = []
        for label, corrupted in corruptions:
            verdict = verify(replace(honest, effect=corrupted), context)
            rejected_numerically = any(
                finding.check is CheckClass.NUMERICAL
                and finding.outcome is Outcome.REJECT
                for finding in verdict.findings
            )
            if not (rejected_numerically and verdict.outcome is Outcome.REJECT):
                survivors.append(label)
        assert survivors == [], (
            f"{len(survivors)} of {len(corruptions)} corrupted claims were not "
            f"rejected: {survivors[:10]!r}"
        )

    def test_a19_the_effect_is_reproduced_from_the_registry(self) -> None:
        """The recomputation is a function of registered rows, not of the claim."""
        world = registered_world()
        context = ClaimContext(
            graph=world.graph, evidence=world.evidence, program=world.program
        )
        honest = supported_claim(world)
        assert honest.effect is not None
        verdict = verify(honest, context)
        assert verdict.recomputed == honest.effect
        # Bit-level, not approximate: the two sides run the same derivation over
        # the same rows, so any difference at all means one of them did not.
        assert verdict.recomputed is not None
        assert math.copysign(1.0, verdict.recomputed.point) == math.copysign(
            1.0, honest.effect.point
        )


# --------------------------------------------------------------------------
# A20 -- evidence completeness
# --------------------------------------------------------------------------

#: How many constructed cases each relevance clause contributes. Six clauses at
#: seventeen cases each is 102, which clears A20's hundred.
_CASES_PER_CLAUSE = 17


def _omission_case(
    clause: RelevanceClause, variant: int
) -> tuple[Claim, EvidenceIndex]:
    """Return a (claim, index) pair with one relevant experiment left uncited.

    Every case cites one experiment and omits a second that the clause under test
    makes relevant. The omitted row is built to be *irrelevant on every other
    axis* -- a metric, a template, a scope and a target none of the other clauses
    reach -- and then given back exactly the one property its clause reads. A
    case built for clause 4 therefore cannot be surfaced by clause 2 instead and
    quietly leave clause 4 untested.
    """
    subject = HypothesisId("hawkes")
    rival = HypothesisId("regime_switching")
    stranger = HypothesisId("seasonality")
    unrelated_scope = scope(
        families=frozenset(),
        parameters={"rate": (99.0 + variant, 100.0 + variant)},
        env_version=f"other/{variant}",
    )
    unrelated_metric = MetricName(f"unrelated_metric_{variant}")
    unrelated_template = f"query:unrelated_{variant}"
    cited = record(
        f"cited-{clause.value}-{variant}",
        sequence=1,
        value=1.0 + variant / 100.0,
        template="query:inter_arrival_dispersion",
        targets=(subject,),
    )

    name = f"omitted-{variant}"
    sequence = 2 + variant
    value = 5.0 + variant
    match clause:
        case RelevanceClause.GRAPH_PROXIMITY | RelevanceClause.RIVAL_TARGET:
            omitted = record(
                name,
                sequence=sequence,
                value=value,
                metric=unrelated_metric,
                template=unrelated_template,
                evidence_scope=unrelated_scope,
                targets=(rival,),
            )
        case RelevanceClause.METRIC_OVERLAP:
            omitted = record(
                name,
                sequence=sequence,
                value=value,
                metric=METRIC,
                template=unrelated_template,
                evidence_scope=unrelated_scope,
                targets=(stranger,),
            )
        case RelevanceClause.SCOPE_OVERLAP:
            omitted = record(
                name,
                sequence=sequence,
                value=value,
                metric=unrelated_metric,
                template=unrelated_template,
                evidence_scope=scope(),
                targets=(stranger,),
            )
        case RelevanceClause.CAUSAL_TARGET:
            omitted = record(
                name,
                sequence=sequence,
                value=value,
                metric=unrelated_metric,
                template=unrelated_template,
                evidence_scope=unrelated_scope,
                manipulated=frozenset({ARRIVAL}),
                targets=(stranger,),
            )
        case RelevanceClause.SAME_TEMPLATE:
            omitted = record(
                name,
                sequence=sequence,
                value=value,
                metric=unrelated_metric,
                template="query:inter_arrival_dispersion",
                evidence_scope=unrelated_scope,
                targets=(stranger,),
            )

    estimand = TotalEffect(ARRIVAL, SIZE)
    causal = clause is RelevanceClause.CAUSAL_TARGET
    subject_claim = claim(
        subject=str(subject),
        modality="causal" if causal else "predictive",
        estimand=estimand if causal else None,
        intervention=intervention(estimand) if causal else None,
        evidence=[str(cited.experiment)],
        claim_id=f"C-{clause.value}-{variant}",
    )
    return subject_claim, index(cited, omitted)


def _omission_graph(clause: RelevanceClause) -> HypothesisGraph:
    """Return the graph a given clause's cases are judged against.

    Only two clauses read the graph, and they need different shapes: clause 1
    wants ``regime_switching`` two relations away from ``hawkes``, clause 6 wants
    it standing in an explicit rivalry. Both are built here so a case cannot pass
    on the other clause's structure.
    """
    built = graph_of(closed_set())
    hawkes, regime, null = (
        HypothesisId("hawkes"),
        HypothesisId("regime_switching"),
        HypothesisId("null"),
    )
    if clause is RelevanceClause.GRAPH_PROXIMITY:
        # Two relations apart, which is the boundary of §7.1 clause 1's window.
        return built.relate(hawkes, null, Relation.ALTERNATIVE_TO).relate(
            null, regime, Relation.ALTERNATIVE_TO
        )
    if clause is RelevanceClause.RIVAL_TARGET:
        return built.relate(hawkes, regime, Relation.CONTRADICTS)
    return built


class TestA20EvidenceCompleteness:
    """A20: an omitted relevant experiment is surfaced in every constructed case."""

    def test_a20_omitted_relevant_experiments_are_all_surfaced(self) -> None:
        """102 cases spanning SPEC §7.1's six clauses; the query misses none."""
        missed: list[str] = []
        for clause in RelevanceClause:
            graph = _omission_graph(clause)
            for variant in range(_CASES_PER_CLAUSE):
                subject_claim, evidence = _omission_case(clause, variant)
                found = survey(subject_claim, evidence, graph)
                omitted = ExperimentId(f"omitted-{variant}")
                if omitted not in found.uncited_relevant:
                    missed.append(f"{clause.value}/{variant}")
        assert missed == [], (
            f"{len(missed)} of {len(RelevanceClause) * _CASES_PER_CLAUSE} omitted "
            f"experiments went unsurfaced: {missed[:10]!r}"
        )

    def test_a20_each_clause_surfaces_its_own_case(self) -> None:
        """Every clause of SPEC §7.1 is the reason its own case was surfaced.

        Without this, a suite could report six clauses' worth of cases while one
        clause carried all of them and another was never exercised at all.
        """
        for clause in RelevanceClause:
            graph = _omission_graph(clause)
            subject_claim, evidence = _omission_case(clause, 0)
            found = survey(subject_claim, evidence, graph)
            reasons = found.reasons[ExperimentId("omitted-0")]
            assert clause in reasons, (
                f"case built for {clause.value} was surfaced by {reasons!r} instead"
            )

    def test_a20_four_clauses_are_independently_sufficient(self) -> None:
        """Clauses 2 to 5 each surface a case no other clause reaches."""
        independent = (
            RelevanceClause.METRIC_OVERLAP,
            RelevanceClause.SCOPE_OVERLAP,
            RelevanceClause.CAUSAL_TARGET,
            RelevanceClause.SAME_TEMPLATE,
        )
        for clause in independent:
            graph = _omission_graph(clause)
            subject_claim, evidence = _omission_case(clause, 0)
            found = survey(subject_claim, evidence, graph)
            assert found.reasons[ExperimentId("omitted-0")] == frozenset({clause}), (
                f"case built for {clause.value} also fired "
                f"{found.reasons[ExperimentId('omitted-0')]!r}"
            )

    def test_a20_clause_six_is_subsumed_by_clause_one(self) -> None:
        """A finding about SPEC §7.1, pinned so it is recorded and not stumbled on.

        Clause 6 makes an experiment relevant when its target is ``AlternativeTo``
        or ``Contradicts`` the claim's subject. Both are relations, and a relation
        is one edge, so every clause 6 case is inside clause 1's two-edge window.
        Clause 6 is therefore never the *only* reason an experiment is relevant,
        and clause 1 is strictly wider -- the two-hop case below is surfaced by
        clause 1 alone. The redundancy is in the specification, not in this
        implementation, so it is asserted rather than removed. See
        ``docs/DECISIONS.md``.
        """
        rival_claim, rival_evidence = _omission_case(RelevanceClause.RIVAL_TARGET, 0)
        rival = survey(
            rival_claim,
            rival_evidence,
            _omission_graph(RelevanceClause.RIVAL_TARGET),
        )
        assert rival.reasons[ExperimentId("omitted-0")] == frozenset(
            {RelevanceClause.RIVAL_TARGET, RelevanceClause.GRAPH_PROXIMITY}
        )

        far_claim, far_evidence = _omission_case(RelevanceClause.GRAPH_PROXIMITY, 0)
        distant = survey(
            far_claim,
            far_evidence,
            _omission_graph(RelevanceClause.GRAPH_PROXIMITY),
        )
        assert distant.reasons[ExperimentId("omitted-0")] == frozenset(
            {RelevanceClause.GRAPH_PROXIMITY}
        )

    def test_a20_an_omission_is_rejected_end_to_end(self) -> None:
        """A verdict, not only a survey: the claim itself is refused."""
        graph = _omission_graph(RelevanceClause.METRIC_OVERLAP)
        subject_claim, evidence = _omission_case(RelevanceClause.METRIC_OVERLAP, 0)
        context = ClaimContext(
            graph=graph,
            evidence=evidence,
            program=reference_program(),
        )
        verdict = verify(subject_claim, context)
        assert verdict.outcome is Outcome.REJECT
        assert any(
            finding.check is CheckClass.COMPLETENESS for finding in verdict.findings
        )

    def test_a20_a_version_mismatch_is_reported_not_resolved(self) -> None:
        """SPEC §7.1's third category is surfaced separately, and does not reject."""
        cited = record("cited", sequence=1, targets=(HypothesisId("hawkes"),))
        superseded = record(
            "superseded",
            sequence=2,
            evidence_scope=scope(metric_version="superseded"),
            targets=(HypothesisId("hawkes"),),
        )
        subject_claim = claim(evidence=["cited"])
        found = survey(subject_claim, index(cited, superseded), graph_of(closed_set()))
        assert ExperimentId("superseded") in found.version_mismatched
        assert ExperimentId("superseded") not in found.uncited_relevant


# --------------------------------------------------------------------------
# A21 -- causal licensing
# --------------------------------------------------------------------------


def _aligned_pairs() -> tuple[tuple[str, Estimand, EvidenceRecord], ...]:
    """Return intervention/estimand pairs SPEC §7.2 licenses, with their evidence.

    Thirteen distinct shapes covering all four estimand rows, then repeats of the
    simplest one to reach A21's fifty. The repeats vary the measured value, so
    they are fifty separate licensing decisions rather than one decision counted
    fifty times, and the thirteen are what actually exercise the table.
    """
    return (
        (
            "total/adjacent",
            TotalEffect(ARRIVAL, SIZE),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
        ),
        (
            "total/downstream",
            TotalEffect(ARRIVAL, OBS),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
        ),
        (
            "total/broad-intervention",
            TotalEffect(SIZE, OBS),
            record("e", sequence=1, manipulated=frozenset({SIZE, SIGN})),
        ),
        (
            "total/self-contained",
            TotalEffect(SIGN, OBS),
            record("e", sequence=1, manipulated=frozenset({SIGN})),
        ),
        (
            "total/collateral-permitted",
            TotalEffect(ARRIVAL, SIGN),
            record(
                "e",
                sequence=1,
                manipulated=frozenset({ARRIVAL}),
                collateral=frozenset({SIZE, SIGN, OBS}),
            ),
        ),
        (
            "direct/no-mediator-exists",
            DirectEffect(SIZE, OBS, frozenset()),
            record("e", sequence=1, manipulated=frozenset({SIZE})),
        ),
        (
            "direct/no-mediator-on-the-sign-path",
            DirectEffect(SIGN, OBS, frozenset()),
            record("e", sequence=1, manipulated=frozenset({SIGN})),
        ),
        (
            "direct/all-mediators-blocked",
            DirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN})),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
        ),
        (
            "controlled/held-fixed-in-the-record",
            ControlledDirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN})),
            record(
                "e",
                sequence=1,
                manipulated=frozenset({ARRIVAL}),
                held_fixed=frozenset({SIZE, SIGN}),
            ),
        ),
        (
            "controlled/nothing-to-hold",
            ControlledDirectEffect(SIZE, OBS, frozenset()),
            record("e", sequence=1, manipulated=frozenset({SIZE})),
        ),
        (
            "path/off-path-untouched",
            PathSpecificEffect(ARRIVAL, OBS, (ARRIVAL, SIZE, OBS)),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
        ),
        (
            "path/off-path-held-fixed",
            PathSpecificEffect(ARRIVAL, OBS, (ARRIVAL, SIZE, OBS)),
            record(
                "e",
                sequence=1,
                manipulated=frozenset({ARRIVAL, SIGN}),
                held_fixed=frozenset({SIGN}),
            ),
        ),
        (
            "path/single-edge",
            PathSpecificEffect(SIZE, OBS, (SIZE, OBS)),
            record("e", sequence=1, manipulated=frozenset({SIZE})),
        ),
        *(
            (
                f"total/repeat-{k}",
                TotalEffect(ARRIVAL, OBS),
                record(
                    "e", sequence=1, manipulated=frozenset({ARRIVAL}), value=1.0 + k
                ),
            )
            for k in range(37)
        ),
    )


def _misaligned_pairs() -> tuple[
    tuple[str, Estimand, EvidenceRecord, tuple[AssumptionCode, ...]], ...
]:
    """Return pairs SPEC §7.2 refuses, with the evidence that fails them."""
    blocked = (AssumptionCode.MEDIATORS_BLOCKED,)
    held = (AssumptionCode.MEDIATORS_BLOCKED, AssumptionCode.HELD_FIXED)
    return (
        (
            "total/target-not-manipulated",
            TotalEffect(SIZE, OBS),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
            (),
        ),
        (
            "total/outcome-not-reachable",
            TotalEffect(OBS, ARRIVAL),
            record("e", sequence=1, manipulated=frozenset({OBS})),
            (),
        ),
        (
            "total/nothing-manipulated",
            TotalEffect(ARRIVAL, OBS),
            record("e", sequence=1),
            (),
        ),
        (
            "direct/one-path-unblocked",
            DirectEffect(ARRIVAL, OBS, frozenset({SIZE})),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
            blocked,
        ),
        (
            "direct/assumption-not-listed",
            DirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN})),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
            (),
        ),
        (
            "controlled/not-actually-held-fixed",
            ControlledDirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN})),
            record(
                "e",
                sequence=1,
                manipulated=frozenset({ARRIVAL}),
                held_fixed=frozenset({SIZE}),
            ),
            held,
        ),
        (
            "controlled/held-fixed-not-declared",
            ControlledDirectEffect(ARRIVAL, OBS, frozenset({SIZE, SIGN})),
            record(
                "e",
                sequence=1,
                manipulated=frozenset({ARRIVAL}),
                held_fixed=frozenset({SIZE, SIGN}),
            ),
            blocked,
        ),
        (
            "path/not-an-edge",
            PathSpecificEffect(ARRIVAL, OBS, (ARRIVAL, OBS)),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL})),
            (),
        ),
        (
            "path/off-path-manipulated",
            PathSpecificEffect(ARRIVAL, OBS, (ARRIVAL, SIGN, OBS)),
            record("e", sequence=1, manipulated=frozenset({ARRIVAL, SIZE})),
            (),
        ),
        (
            "path/reversed",
            PathSpecificEffect(OBS, ARRIVAL, (OBS, SIZE, ARRIVAL)),
            record("e", sequence=1, manipulated=frozenset({OBS})),
            (),
        ),
        *(
            (
                f"total/target-not-manipulated-{k}",
                TotalEffect(SIZE, OBS),
                record(
                    "e", sequence=1, manipulated=frozenset({ARRIVAL}), value=1.0 + k
                ),
                (),
            )
            for k in range(40)
        ),
    )


class TestA21CausalLicensing:
    """A21: zero misaligned pairs licensed, zero aligned pairs rejected."""

    def test_a21_no_aligned_pair_is_rejected(self) -> None:
        """Every pair SPEC §7.2's table licenses is licensed at the claimed estimand."""
        program = reference_program()
        pairs = _aligned_pairs()
        assert len(pairs) >= 50, f"A21 wants 50 aligned pairs, got {len(pairs)}"
        refused: list[str] = []
        for label, estimand, evidence in pairs:
            declared = intervention(
                estimand,
                manipulated=evidence.manipulated,
                assumptions=(
                    AssumptionCode.MEDIATORS_BLOCKED,
                    AssumptionCode.HELD_FIXED,
                    AssumptionCode.OFF_PATH_CONTROLLED,
                ),
            )
            subject = claim(
                modality="causal",
                estimand=estimand,
                intervention=declared,
                evidence=["e"],
            )
            granted = license(subject, program, (evidence,))
            if granted.licensed != estimand:
                refused.append(f"{label} -> {granted.licensed!r}")
        assert refused == [], f"{len(refused)} aligned pairs refused: {refused[:8]!r}"

    def test_a21_no_misaligned_pair_is_licensed(self) -> None:
        """No pair the table refuses comes back licensed at the claimed estimand."""
        program = reference_program()
        pairs = _misaligned_pairs()
        assert len(pairs) >= 50, f"A21 wants 50 misaligned pairs, got {len(pairs)}"
        licensed: list[str] = []
        for label, estimand, evidence, assumptions in pairs:
            declared = intervention(
                estimand,
                manipulated=evidence.manipulated,
                assumptions=assumptions,
            )
            subject = claim(
                modality="causal",
                estimand=estimand,
                intervention=declared,
                evidence=["e"],
            )
            granted = license(subject, program, (evidence,))
            if granted.licensed == estimand:
                licensed.append(label)
        assert licensed == [], (
            f"{len(licensed)} misaligned pairs were licensed: {licensed[:8]!r}"
        )

    def test_a21_misalignment_downgrades_where_a_weaker_estimand_holds(self) -> None:
        """SPEC §7.2 downgrades rather than rejecting, when a weaker claim is true."""
        program = reference_program()
        estimand = DirectEffect(ARRIVAL, OBS, frozenset({SIZE}))
        evidence = record("e", sequence=1, manipulated=frozenset({ARRIVAL}))
        subject = claim(
            modality="causal",
            estimand=estimand,
            intervention=intervention(
                estimand, assumptions=(AssumptionCode.MEDIATORS_BLOCKED,)
            ),
            evidence=["e"],
        )
        granted = license(subject, program, (evidence,))
        assert granted.licensed == TotalEffect(ARRIVAL, OBS)
        assert any(finding.outcome is Outcome.DOWNGRADE for finding in granted.findings)

    def test_a21_an_unmanipulated_target_licenses_nothing(self) -> None:
        """There is no weaker estimand to fall back to when nothing was done."""
        program = reference_program()
        estimand = TotalEffect(ARRIVAL, OBS)
        subject = claim(
            modality="causal",
            estimand=estimand,
            intervention=intervention(estimand, manipulated=frozenset()),
            evidence=["e"],
        )
        granted = license(subject, program, (record("e", sequence=1),))
        assert granted.licensed is None
        assert any(finding.outcome is Outcome.REJECT for finding in granted.findings)


# --------------------------------------------------------------------------
# A22 -- prospective confirmation
# --------------------------------------------------------------------------


def _lateness_case(
    variant: int, *, prospective: bool
) -> tuple[Claim, HypothesisGraph, EvidenceIndex]:
    """Return a (claim, context-graph, index) triple for one lateness case.

    The hypothesis is proposed after experiment ``early``. When ``prospective``
    is false every cited experiment predates the proposal, which is what SPEC F9
    forbids a confirmatory claim from resting on.
    """
    early = record(
        f"early-{variant}",
        sequence=1,
        value=1.0 + variant / 100.0,
        targets=(HypothesisId("hawkes"),),
    )
    later = record(
        f"later-{variant}",
        sequence=2,
        value=1.5 + variant / 100.0,
        targets=(HypothesisId("hawkes"),),
    )
    graph = graph_of(
        closed_set(), late=("hawkes",), proposed_at=ExperimentId(f"early-{variant}")
    )
    cited = [str(early.experiment)] + ([str(later.experiment)] if prospective else [])
    subject = claim(
        subject="hawkes",
        partition="confirmatory",
        strength="supports",
        evidence=cited,
        claim_id=f"C-late-{variant}",
    )
    return subject, graph, index(early, later)


class TestA22ProspectiveConfirmation:
    """A22: a confirmatory claim on retrospective evidence alone is rejected."""

    def test_a22_retrospective_only_confirmation_is_rejected(self) -> None:
        """50 constructed cases, every one refused."""
        survivors: list[int] = []
        for variant in range(50):
            subject, graph, evidence = _lateness_case(variant, prospective=False)
            context = ClaimContext(
                graph=graph,
                evidence=evidence,
                program=reference_program(),
            )
            findings = logical_check(subject, context)
            if not any(finding.outcome is Outcome.REJECT for finding in findings):
                survivors.append(variant)
        assert survivors == [], (
            f"{len(survivors)} retrospective confirmatory claims survived: "
            f"{survivors[:10]!r}"
        )

    def test_a22_a_prospective_experiment_rescues_the_same_claim(self) -> None:
        """The positive control: lateness alone is not what is being punished."""
        subject, graph, evidence = _lateness_case(0, prospective=True)
        context = ClaimContext(
            graph=graph,
            evidence=evidence,
            program=reference_program(),
        )
        findings = logical_check(subject, context)
        assert [f for f in findings if f.outcome is Outcome.REJECT] == []

    def test_a22_an_exploratory_claim_is_untouched_by_lateness(self) -> None:
        """SPEC F9 costs a *confirmatory* claim, and nothing else."""
        subject, graph, evidence = _lateness_case(0, prospective=False)
        exploratory = replace(subject, partition="exploratory")
        context = ClaimContext(
            graph=graph,
            evidence=evidence,
            program=reference_program(),
        )
        findings = logical_check(exploratory, context)
        assert [f for f in findings if f.outcome is Outcome.REJECT] == []

    def test_a22_a_hypothesis_present_from_the_start_is_untouched(self) -> None:
        """F9 is about lateness; a hypothesis that was never late pays nothing."""
        early = record("early", sequence=1, targets=(HypothesisId("hawkes"),))
        subject = claim(
            subject="hawkes",
            partition="confirmatory",
            strength="supports",
            evidence=["early"],
        )
        context = ClaimContext(
            graph=graph_of(closed_set()),
            evidence=index(early),
            program=reference_program(),
        )
        findings = logical_check(subject, context)
        assert [f for f in findings if f.outcome is Outcome.REJECT] == []


# --------------------------------------------------------------------------
# A23 -- coverage
# --------------------------------------------------------------------------

#: SPEC §6.5 A23's threshold.
COVERAGE_THRESHOLD = 0.90


class TestA23Coverage:
    """A23: at least 90% of claims from slice runs are adjudicated mechanically."""

    def test_a23_slice_run_claims_are_adjudicated_without_human_input(
        self, slice_claim_verdicts: tuple[Verdict, ...]
    ) -> None:
        """The measurement. This module's docstring says what stands in for an agent."""
        assert slice_claim_verdicts, "no claims were generated from the slice runs"
        adjudicated = [v for v in slice_claim_verdicts if v.adjudicated]
        coverage = len(adjudicated) / len(slice_claim_verdicts)
        assert coverage >= COVERAGE_THRESHOLD, (
            f"the verifier adjudicated {len(adjudicated)} of "
            f"{len(slice_claim_verdicts)} slice-run claims ({coverage:.1%}), under "
            f"A23's {COVERAGE_THRESHOLD:.0%}"
        )

    def test_a23_the_population_is_not_all_one_verdict(
        self, slice_claim_verdicts: tuple[Verdict, ...]
    ) -> None:
        """The control. A verifier that accepts or rejects everything covers 100%.

        A23 measures whether claims are *decided*, so a rubber stamp passes it
        outright. What makes the figure mean something is that the same
        population contains claims the verifier accepts and claims it refuses.
        """
        outcomes = {v.outcome for v in slice_claim_verdicts}
        assert len(outcomes) > 1, (
            f"every slice-run claim drew the same verdict ({outcomes!r}); a "
            f"verifier that decides everything the same way adjudicates 100% of "
            f"claims and establishes nothing"
        )
        assert any(v.outcome is Outcome.REJECT for v in slice_claim_verdicts), (
            "no slice-run claim was refused, so the generator produced no overreach"
        )


@pytest.fixture(scope="module")
def slice_claim_verdicts() -> tuple[Verdict, ...]:
    """Return a verdict for every claim generated from the item 9 baseline runs."""
    from baseline_runs import slice_run_claims

    return slice_run_claims()
