"""The relevance relation of SPEC §7.1, and the evidence it is computed over.

An experiment is relevant to a claim if any of six clauses holds. The clauses are
enumerated in :class:`RelevanceClause` in the specification's order, and
:func:`clauses` returns *every* one that fires rather than the first, because a
completeness report that named one reason when three applied would understate how
badly a citation was missed.

What is verified, and what is not
--------------------------------

SPEC §7.1 is explicit and this module inherits the wording: what is verified is
that **every registry experiment satisfying this query is cited**. What is *not*
verified is that no uncited experiment is scientifically relevant, which is not
formalisable. :class:`RelevanceSurvey` is named for a survey rather than for a
proof for that reason.

Versions
--------

Relevance is computed at the claim's declared metric and grammar versions.
Experiments registered under superseded ones are a third category --
:attr:`RelevanceSurvey.version_mismatched` -- reported rather than silently
resolved, because a table read under a discretisation it was not built under
gives confidently wrong numbers and quietly folding those rows into the relevant
set would hide exactly that.

What an experiment was aimed at
-------------------------------

Clause 1 asks about "E's target hypothesis", which no registry row records and
none should: what an experiment was aimed at does not determine its result, and
:class:`~sciagent.registry.store.ExperimentKey` covers what determines a result
and nothing else. Targets are therefore investigation-side data, carried on
:class:`EvidenceRecord` and supplied when the index is built.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from sciagent.core.errors import MalformedClaimError
from sciagent.core.types import (
    Claim,
    ComponentId,
    ExperimentId,
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    MetricName,
    Scope,
    estimand_endpoints,
)
from sciagent.experiments.executor import ObservedExecution
from sciagent.hypothesis.graph import HypothesisGraph, Relation

__all__ = [
    "PROXIMITY_HOPS",
    "EvidenceIndex",
    "EvidenceRecord",
    "RelevanceClause",
    "RelevanceSurvey",
    "claim_metrics",
    "claim_targets",
    "clauses",
    "scopes_overlap",
    "survey",
    "version_mismatched",
]

#: SPEC §7.1 clause 1's "within 2 edges".
PROXIMITY_HOPS: Final = 2

#: The relations clause 6 names.
RIVAL_RELATIONS: Final[frozenset[Relation]] = frozenset(
    {Relation.ALTERNATIVE_TO, Relation.CONTRADICTS}
)


class RelevanceClause(Enum):
    """SPEC §7.1's six clauses, in the specification's order."""

    GRAPH_PROXIMITY = "graph_proximity"
    """1. E's target hypothesis is within 2 edges of C's subject."""

    METRIC_OVERLAP = "metric_overlap"
    """2. E's metric set intersects C's evidence metric set."""

    SCOPE_OVERLAP = "scope_overlap"
    """3. E's scope overlaps C's in family, parameter range, or env version."""

    CAUSAL_TARGET = "causal_target"
    """4. E's intervention manipulates a component in C's causal target set."""

    SAME_TEMPLATE = "same_template"
    """5. E instantiates the same template as any cited experiment."""

    RIVAL_TARGET = "rival_target"
    """6. E's target is AlternativeTo or Contradicts C's subject."""


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One registered experiment, as the verifier needs to see it.

    A projection of :class:`~sciagent.experiments.executor.ObservedExecution`,
    and not a second source of truth: everything here is copied from it by
    :meth:`EvidenceIndex.from_history`, so a verdict cites the same bits the
    registry holds.

    That used to read *"of ``ExecutionResult`` and its registry row"*, and gate
    A41 narrowed what it is a projection of. The verifier never wanted the
    defect the experiment was run against, and ``sequence`` -- the one thing it
    read off the row -- is now a field of the record it is handed.
    """

    experiment: ExperimentId
    template: ExperimentTemplateId
    metrics: tuple[MetricName, ...]
    result: tuple[float, ...]
    manipulated: frozenset[ComponentId]
    collateral: frozenset[ComponentId]
    held_fixed: frozenset[ComponentId]
    targets: tuple[HypothesisId, ...]
    """Which hypotheses this experiment was aimed at. Empty when the system did
    not say, which costs it clause 1 and clause 6 and nothing else."""

    scope: Scope
    sequence: int
    """The registry's monotonic insertion order, and its only notion of time."""

    def value(self, metric: MetricName) -> float:
        """Return this experiment's reading on ``metric``.

        Raises :class:`~sciagent.core.errors.MalformedClaimError` if the
        experiment did not measure it, rather than returning a neighbouring axis:
        a figure read off the wrong axis is exactly the kind of wrong number
        acceptance test A19 exists to catch, and producing one here would put it
        on both sides of the comparison.
        """
        if metric not in self.metrics:
            raise MalformedClaimError(
                f"experiment {self.experiment!r} measured {list(self.metrics)!r}, "
                f"not {metric!r}"
            )
        return self.result[self.metrics.index(metric)]


@dataclass(frozen=True, slots=True)
class EvidenceIndex:
    """The registered experiments an investigation may cite."""

    records: FrozenDict[ExperimentId, EvidenceRecord]

    @classmethod
    def of(cls, records: Iterable[EvidenceRecord]) -> EvidenceIndex:
        """Return an index over ``records``."""
        return cls(
            records=FrozenDict[ExperimentId, EvidenceRecord](
                {record.experiment: record for record in records}
            )
        )

    @classmethod
    def from_history(
        cls,
        history: Sequence[ObservedExecution],
        *,
        scope: Scope,
        targets: Mapping[ExperimentId, tuple[HypothesisId, ...]] | None = None,
    ) -> EvidenceIndex:
        """Return an index over an investigation's executed experiments.

        One ``scope`` covers all of them, and correctly: an investigation runs in
        one environment, at one version, against one grammar and one metric
        catalogue, so the axes SPEC §7.1 clause 3 compares do not vary within it.
        """
        aimed = dict(targets or {})
        return cls.of(
            EvidenceRecord(
                experiment=result.experiment,
                template=result.design.id,
                metrics=result.design.metrics,
                result=tuple(result.result),
                manipulated=result.manipulated,
                collateral=result.collateral,
                held_fixed=result.held_fixed,
                targets=aimed.get(result.experiment, ()),
                scope=scope,
                sequence=result.sequence,
            )
            for result in history
        )

    def record(self, experiment: ExperimentId) -> EvidenceRecord:
        """Return one record, or raise if the index does not hold it."""
        try:
            return self.records[experiment]
        except KeyError as exc:
            raise MalformedClaimError(
                f"experiment {experiment!r} is cited but is not in the evidence "
                f"index, which holds {sorted(self.records)!r}; a claim may cite "
                f"only registered experiments"
            ) from exc

    def ordered(self) -> tuple[EvidenceRecord, ...]:
        """Return every record in registry insertion order."""
        return tuple(sorted(self.records.values(), key=lambda record: record.sequence))


def scopes_overlap(left: Scope, right: Scope) -> bool:
    """Return whether two scopes overlap on any of SPEC §7.1 clause 3's axes."""
    if left.env_version == right.env_version:
        return True
    if left.families & right.families:
        return True
    for name in sorted(set(left.parameters) & set(right.parameters)):
        low, high = left.parameters[name]
        other_low, other_high = right.parameters[name]
        if low <= other_high and other_low <= high:
            return True
    return False


def claim_metrics(claim: Claim, index: EvidenceIndex) -> frozenset[MetricName]:
    """Return the diagnostics a claim rests on: its effect's, and its evidence's."""
    found: set[MetricName] = set()
    if claim.effect is not None:
        found.add(claim.effect.metric)
    for experiment in claim.evidence:
        found.update(index.record(experiment).metrics)
    return frozenset(found)


def claim_targets(claim: Claim) -> frozenset[ComponentId]:
    """Return the components a claim is causally about.

    Its estimand's endpoints, whatever its intervention says it manipulated, and
    the subject itself when the subject is a component rather than a hypothesis.
    """
    found: set[ComponentId] = set()
    if claim.estimand is not None:
        found.update(estimand_endpoints(claim.estimand))
    if claim.intervention is not None:
        found.update(claim.intervention.manipulated)
        found.add(claim.intervention.target)
    if claim.subject_kind == "component":
        found.add(ComponentId(str(claim.subject)))
    return frozenset(found)


def clauses(
    claim: Claim,
    record: EvidenceRecord,
    index: EvidenceIndex,
    graph: HypothesisGraph,
) -> frozenset[RelevanceClause]:
    """Return every clause of SPEC §7.1 under which ``record`` is relevant.

    Guarantees the answer is a pure function of its arguments and does not depend
    on the order in which the index was built.
    """
    fired: set[RelevanceClause] = set()
    subject = HypothesisId(str(claim.subject))
    is_hypothesis = claim.subject_kind == "hypothesis"

    if is_hypothesis and subject in graph.nodes:
        for target in record.targets:
            if target not in graph.nodes:
                continue
            distance = graph.hops(subject, target)
            if distance is not None and distance <= PROXIMITY_HOPS:
                fired.add(RelevanceClause.GRAPH_PROXIMITY)
            if graph.relation(subject, target) in RIVAL_RELATIONS:
                fired.add(RelevanceClause.RIVAL_TARGET)

    if set(record.metrics) & claim_metrics(claim, index):
        fired.add(RelevanceClause.METRIC_OVERLAP)

    if scopes_overlap(record.scope, claim.scope):
        fired.add(RelevanceClause.SCOPE_OVERLAP)

    if record.manipulated & claim_targets(claim):
        fired.add(RelevanceClause.CAUSAL_TARGET)

    cited_templates = {
        index.record(experiment).template for experiment in claim.evidence
    }
    if record.template in cited_templates:
        fired.add(RelevanceClause.SAME_TEMPLATE)

    return frozenset(fired)


def version_mismatched(claim: Claim, record: EvidenceRecord) -> bool:
    """Return whether ``record`` was registered under a superseded version."""
    return (
        record.scope.metric_version != claim.scope.metric_version
        or record.scope.grammar_version != claim.scope.grammar_version
    )


@dataclass(frozen=True, slots=True)
class RelevanceSurvey:
    """What the relevance query found, in SPEC §7.1's three categories."""

    cited: tuple[ExperimentId, ...]
    uncited_relevant: tuple[ExperimentId, ...]
    """Relevant, at the claim's versions, and not cited. SPEC §7.1 says every one
    of these is a failure of evidence completeness."""

    version_mismatched: tuple[ExperimentId, ...]
    """Relevant but registered under a superseded metric or grammar version.
    Reported, never silently folded into the category above."""

    reasons: Mapping[ExperimentId, frozenset[RelevanceClause]]
    """Per experiment, every clause that made it relevant."""


def survey(
    claim: Claim, index: EvidenceIndex, graph: HypothesisGraph
) -> RelevanceSurvey:
    """Return the relevance query's three categories for one claim.

    Guarantees every returned tuple is in registry insertion order, so two runs
    of the same query report the same list rather than the same set.
    """
    cited = set(claim.evidence)
    uncited: list[ExperimentId] = []
    mismatched: list[ExperimentId] = []
    reasons: dict[ExperimentId, frozenset[RelevanceClause]] = {}
    for record in index.ordered():
        fired = clauses(claim, record, index, graph)
        reasons[record.experiment] = fired
        if not fired or record.experiment in cited:
            continue
        if version_mismatched(claim, record):
            mismatched.append(record.experiment)
        else:
            uncited.append(record.experiment)
    return RelevanceSurvey(
        cited=tuple(claim.evidence),
        uncited_relevant=tuple(uncited),
        version_mismatched=tuple(mismatched),
        reasons=reasons,
    )
