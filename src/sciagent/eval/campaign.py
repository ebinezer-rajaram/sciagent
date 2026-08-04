"""Running a system on a scenario, and checking that it played fair.

:func:`run_scenario` is where SPEC's second invariant stops being a convention
and becomes a check. A system returns a :class:`~sciagent.core.types.Diagnosis`;
the harness re-derives one from the engine and refuses the run if the two
disagree. A system that fabricated a posterior, inflated its own leading
hypothesis, or nominated its own supporting evidence fails here rather than
producing a plausible number nobody audits.

The experiment matrix of SPEC §9 is item 15's and is not here. What is here is
the single run that matrix will be made of.

Claims from a run
-----------------

:func:`claims_from_run` renders a run as typed claims. It exists because
acceptance test A23 measures how much of a claim population the verifier decides
mechanically, and SPEC §11 puts the verifier six items before the agent that will
write the claims. So the claims it measures come from here for now, and from an
agent at item 12.

It is deliberately *not* selective. It enumerates the modality by strength
cross-product for every hypothesis carrying mass, which puts claims nobody would
make -- ``establishes`` on a hypothesis holding a twentieth of the posterior,
``causal`` where nothing was manipulated -- into the population alongside the
reasonable ones. A generator that emitted only what the verifier accepts would
turn A23 into a measurement of itself. Everything it emits is structure and
citation; the one number in a claim, its effect, comes off
:func:`sciagent.verify.numerical.recompute` and is never authored here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

from sciagent.core.edits import Defect
from sciagent.core.errors import InvestigationError
from sciagent.core.types import (
    CLAIM_MODALITIES,
    CLAIM_STRENGTHS,
    Claim,
    ClaimId,
    ComponentId,
    Diagnosis,
    Direction,
    HypothesisId,
    Intervention,
    TotalEffect,
)
from sciagent.eval.scenarios import Scenario
from sciagent.eval.scoring import ClosedWorldScore, closed_world_score
from sciagent.experiments.executor import Executor
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.inference.interface import PPCResult
from sciagent.systems.base import Investigation, ResearchSystem, diagnose
from sciagent.verify.numerical import recompute
from sciagent.verify.relevance import EvidenceIndex

__all__ = ["ScenarioRun", "claims_from_run", "run_scenario"]


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """One system's run on one scenario, and everything it is judged on."""

    scenario: Scenario
    system: str
    diagnosis: Diagnosis
    score: ClosedWorldScore
    ppc: PPCResult
    """The Stage A verdict. Carried here rather than on the diagnosis because
    SPEC §3.4 has no field for it, and B1's whole output is this flag."""

    experiments: int
    """How many experiments were actually run, which may be under the budget."""

    proposed: Mapping[HypothesisId, Defect]
    """Structures the system introduced. Empty for V1 and B1 by construction."""

    graph: HypothesisGraph
    """The hypothesis graph the run ended with, proposals included.

    Not the one passed in: a system that proposed a structure changed it, and a
    claim about that structure is judged against the graph that holds it."""

    evidence: EvidenceIndex
    """Every experiment this run registered, as the verifier reads them.

    Carried on the run rather than rebuilt later because it holds two things the
    registry does not: which hypotheses each experiment was aimed at, and the
    scope it was gathered in. Neither determines an experiment's result, so
    neither belongs in its content address."""

    structural_distance: float
    """Distance from the truth to the nearest structure entertained, in edits.

    Measured under the *environment's* grammar rather than the system's. It has
    to be: on SPEC §4.5's S11 the truth is out of the agent's library by
    construction, and a distance under a grammar that cannot express one of its
    endpoints is undefined. The two agree wherever both can express what is being
    compared, since the ground metric is over grid indices and the grids are the
    same objects in both.

    Reported alongside the closed-world score because the two can disagree
    sharply, and reporting only the first would misrepresent a system. B5
    searches a grammar whose parameters live on a 64-point grid and whose
    corners are what a bounded enumeration visits, so it routinely lands in the
    *right structural cell* with the wrong parameters: exact-match mass of zero,
    distance far below a whole edit. SPEC §5 warns against a baseline that loses
    because it was built carelessly, and scoring a near miss as a total failure
    would be exactly that.

    ``inf`` if nothing was entertained.
    """

    @property
    def spent(self) -> float:
        """Return what the run cost, in experiments."""
        return float(self.experiments)


def run_scenario(
    scenario: Scenario,
    system: ResearchSystem,
    *,
    executor: Executor,
    engine: EmpiricalTableEngine,
    graph: HypothesisGraph,
) -> ScenarioRun:
    """Run one system on one scenario and score what it concluded.

    The caller builds the executor, engine and graph, because all three are
    environment-shaped and ``sciagent`` may not import an environment. What this
    function owns is the part that must be identical for every system: the
    investigation it is handed, the audit of what it returned, and the score.

    Guarantees the returned diagnosis is the one the engine's own state implies.
    A system whose report differs -- in its distribution, its abstain or null
    mass, or its supporting sets -- raises
    :class:`~sciagent.core.errors.InvestigationError` rather than being scored,
    which is SPEC's second invariant enforced by assertion rather than by
    comment.

    Experiments are run against :attr:`~sciagent.eval.scenarios.Scenario.executed`
    and the result is scored against
    :attr:`~sciagent.eval.scenarios.Scenario.truth`. The two differ only where a
    scenario carries a nuisance, and where it does, that difference is the
    scenario: S12's censoring shapes every measurement and is not what anyone is
    asked to find.
    """
    investigation = Investigation(
        scenario_id=scenario.id,
        designs=scenario.designs,
        truth=scenario.executed,
        executor=executor,
        engine=engine,
        graph=graph,
        seed=scenario.seed,
    )
    reported = system.investigate(investigation)

    expected = diagnose(
        scenario.id,
        engine,
        proposed_edits=investigation.proposed,
        residual_candidates=reported.residual_candidates,
    )
    _audit(system, reported, expected)

    edits = engine_edits(engine)
    return ScenarioRun(
        scenario=scenario,
        system=system.name,
        diagnosis=reported,
        score=closed_world_score(reported, scenario.truth, edits),
        ppc=engine.ppc(),
        experiments=len(investigation.history),
        proposed=investigation.proposed,
        graph=investigation.graph,
        evidence=EvidenceIndex.from_history(
            investigation.history,
            scope=executor.scope(),
            targets=investigation.targets,
        ),
        structural_distance=min(
            (
                executor.grammar.distance(edits[node_id], scenario.truth)
                for node_id in sorted(edits)
            ),
            default=math.inf,
        ),
    )


def engine_edits(engine: EmpiricalTableEngine) -> Mapping[HypothesisId, Defect]:
    """Return the structure of every hypothesis an engine holds."""
    return {node_id: engine.program_edit(node_id) for node_id in engine.hypotheses}


def _causal_pair(run: ScenarioRun) -> tuple[ComponentId, ComponentId] | None:
    """Return a (manipulated, downstream) pair the run's experiments touched.

    ``None`` for a purely observational investigation, which is what every SPEC
    §5 baseline performs on the slice's ``query`` designs. A causal claim
    generated for such a run is one the verifier ought to refuse, and that it is
    generated anyway is the point: a claim population containing no overreach
    measures nothing.
    """
    for record in run.evidence.ordered():
        if not record.manipulated:
            continue
        target = sorted(record.manipulated)[0]
        downstream = sorted(record.collateral)
        if downstream:
            return target, downstream[0]
    return None


def claims_from_run(run: ScenarioRun) -> tuple[Claim, ...]:
    """Return the typed claims one run affords, for A23's coverage measurement.

    One claim per (hypothesis carrying mass) by (modality) by (strength). Every
    claim cites the whole run, which is what stops evidence completeness refusing
    all of them for a reason that has nothing to do with what they assert; every
    claim's scope is the scope the experiments were gathered in, for the same
    reason. What varies is what the claims *say*, which is what a verifier is for.

    Guarantees no number is authored here: an effect, where the citation can
    produce one, comes from :func:`sciagent.verify.numerical.recompute`.
    """
    cited = tuple(record.experiment for record in run.evidence.ordered())
    if not cited:
        return ()
    scope = run.evidence.ordered()[0].scope
    leader = max(
        sorted(run.diagnosis.distribution),
        key=lambda node_id: run.diagnosis.distribution[node_id],
        default=None,
    )
    pair = _causal_pair(run)
    built: list[Claim] = []
    for node_id in sorted(run.diagnosis.distribution):
        mass = float(run.diagnosis.distribution[node_id])
        if mass <= 0.0:
            continue
        for modality in CLAIM_MODALITIES:
            estimand = (
                TotalEffect(pair[0], pair[1])
                if modality == "causal" and pair is not None
                else None
            )
            declared = (
                Intervention(
                    target=estimand.target,
                    manipulated=frozenset({estimand.target}),
                    estimand=estimand,
                    collateral=frozenset({estimand.outcome}),
                    assumptions=(),
                    expected_direction=Direction.INCREASE,
                )
                if estimand is not None
                else None
            )
            for strength in CLAIM_STRENGTHS:
                draft = Claim(
                    id=ClaimId(
                        f"{run.system}/{run.scenario.id}/{node_id}/"
                        f"{modality}/{strength}"
                    ),
                    subject=node_id,
                    subject_kind="hypothesis",
                    modality=modality,
                    estimand=estimand,
                    strength=strength,
                    scope=scope,
                    evidence=cited,
                    partition="confirmatory" if node_id == leader else "exploratory",
                    effect=None,
                    uniqueness="exclusive" if mass > 0.5 else "non_exclusive",
                    prose=(
                        f"{run.system} on {run.scenario.id}: {strength} "
                        f"{node_id} ({modality})"
                    ),
                    intervention=declared,
                )
                built.append(
                    replace(draft, effect=recompute(draft, run.evidence))
                    if modality == "causal"
                    else draft
                )
    return tuple(built)


def _audit(system: ResearchSystem, reported: Diagnosis, expected: Diagnosis) -> None:
    """Raise unless ``reported`` is the diagnosis the engine's state implies.

    ``residual_candidates`` is excluded from the comparison: it is the one field
    a system legitimately authors beyond ``proposed_edits``, being a statement
    about what it would look at next rather than a number. Everything else must
    match exactly -- not approximately, because both sides are computed by the
    same function from the same state, so any difference at all means the
    reported value did not come from there.
    """
    if reported.scenario_id != expected.scenario_id:
        raise InvestigationError(
            f"system {system.name!r} reported scenario {reported.scenario_id!r} but "
            f"was run on {expected.scenario_id!r}"
        )
    for field, mine, theirs in (
        ("distribution", reported.distribution, expected.distribution),
        ("abstain_mass", reported.abstain_mass, expected.abstain_mass),
        ("null_mass", reported.null_mass, expected.null_mass),
        ("proposed_edits", reported.proposed_edits, expected.proposed_edits),
        ("supporting", reported.supporting, expected.supporting),
    ):
        if mine != theirs:
            raise InvestigationError(
                f"system {system.name!r} reported {field} {mine!r}, but the engine's "
                f"state implies {theirs!r}. Build a diagnosis with "
                f"sciagent.systems.base.diagnose; no system may author a number "
                f"(SPEC §1, second invariant)"
            )
