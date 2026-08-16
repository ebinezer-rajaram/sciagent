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
from typing import Protocol, runtime_checkable

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
    ExperimentId,
    HypothesisId,
    Intervention,
    TotalEffect,
)
from sciagent.eval.scenarios import Scenario
from sciagent.eval.scoring import ClosedWorldScore, closed_world_score
from sciagent.experiments.executor import Executor
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.empirical import EmpiricalTableEngine, replicate_seed
from sciagent.inference.interface import PPCResult
from sciagent.systems.base import Investigation, ResearchSystem, diagnose
from sciagent.systems.hybrid import ProposalAttempt
from sciagent.verify.numerical import recompute
from sciagent.verify.relevance import EvidenceIndex

__all__ = ["Proposing", "ScenarioRun", "claims_from_run", "run_scenario"]


@runtime_checkable
class Proposing(Protocol):
    """A system that keeps a record of asking a proposal layer.

    Structural rather than nominal, deliberately. The harness has to capture the
    record at the moment a run ends -- a system object outlives its run, and item
    13's ablation reuses one -- but it must not thereby depend on which class
    holds it, so any system exposing ``attempts`` is read and no system is named.

    :class:`~sciagent.systems.hybrid.ProposalAttempt` is imported for its type
    and not for its class: it is the vocabulary the record is written in, which
    every system with a proposal layer shares.
    """

    @property
    def attempts(self) -> tuple[ProposalAttempt, ...]:
        """Return the proposals requested in the system's last run."""
        ...


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """One system's run on one scenario, and everything it is judged on."""

    scenario: Scenario
    system: str
    diagnosis: Diagnosis
    score: ClosedWorldScore
    ppc: PPCResult
    """How well the entertained set explains the whole recorded record.

    Carried here rather than on the diagnosis because SPEC §3.4 has no field for
    it, and B1's whole output is this flag. Over the run's *experiments*, and no
    probe -- so it is reconstructible from :attr:`evidence`, which a number
    folding in a reading absent from that index would not be.

    **Not** the verdict a system acted on where a scenario declares a Stage A
    probe, and deliberately no longer accompanied by one. A companion
    ``adequacy`` field was written on 2026-08-16 and withdrawn the same day: the
    only honest place to evaluate it is *after* ``investigate`` returns, which
    reads the final posterior rather than the one the gate saw, so a system that
    successfully proposed a structure explaining the probe would be recorded as
    having failed to detect. The field also conflated two different quantities --
    whether the space is adequate, which is a property of the space and the
    scenario, and whether a *system* detected that it was not, which B1 cannot
    have an answer to because it never consults the check. Both are open in
    ``docs/BACKLOG.md``. Until they are settled there is no consumer, and a field
    whose correct semantics depend on an unmade decision is worse than none.
    """

    experiments: int
    """How many experiments were actually run, which may be under the budget."""

    proposed: Mapping[HypothesisId, Defect]
    """Structures the system introduced, library included.

    Empty only for B1, which proposes nothing at all. Every other system routes
    the structures it entertains through
    :meth:`~sciagent.systems.base.Investigation.propose` -- that is what
    :func:`~sciagent.systems.base.entertain` does -- so V1's holds its whole
    library. Which of these were *extensions* rather than the space the
    investigation opened with is
    :func:`~sciagent.eval.agency.agency_metrics`'s question, and it reads the
    graph's ``proposed_at`` rather than this mapping to answer it.
    """

    attempts: tuple[ProposalAttempt, ...] | None
    """Every proposal the system requested during this run, in order.

    ``None`` for a system that holds no proposal layer, and an *empty tuple* for
    a system that holds one it never consulted -- a run whose posterior
    predictive check never opened SPEC F6's gate. The two are different facts
    about a run and an empty tuple cannot carry both, which is why this is
    optional rather than merely possibly-empty: B4 introduces structure without
    ever having a model to ask, and reporting that as "asked zero times" would
    put a layer in the report that the system does not have.

    Captured here rather than read off the system afterwards because a system
    object outlives its run: :attr:`~sciagent.systems.hybrid.Hybrid.attempts`
    holds whichever scenario ran last, and item 13's ablation reuses one arm
    across three scenarios.
    """

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
        stage_a=stage_a_id(scenario) if scenario.stage_a is not None else None,
    )
    _run_stage_a(scenario, executor=executor, engine=engine)
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
        attempts=_attempts_of(system),
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


def stage_a_id(scenario: Scenario) -> ExperimentId:
    """Return the id the scenario's Stage A reading is recorded under.

    One definition, because two would be a latent bug of the worst kind: the
    reading is written under this name and the adequacy check looks it up by it,
    so a mismatch would not raise -- ``ppc(experiments=...)`` would, but only
    after the two had already diverged -- and the two sites are in different
    modules. Public so that a probe or a test can name the reading without
    rebuilding the string.
    """
    return ExperimentId(f"stage_a/{scenario.id}")


def _run_stage_a(
    scenario: Scenario,
    *,
    executor: Executor,
    engine: EmpiricalTableEngine,
) -> None:
    """Take the scenario's Stage A reading, if it declares one.

    Guarantees the reading reaches the posterior predictive check and nothing
    else. It is measured rather than run, so no registry row is written, no
    budget is charged and ``investigation.history`` does not hold it; and it goes
    in through :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.record_probe`,
    so it is absent from ``engine.observations``, contributes no likelihood, and
    moves no posterior mass. A system can neither see that it happened nor cite
    it.

    That routing is load-bearing and was wrong for a day. ``engine.record``
    appends to the same list ``log_likelihood_total`` folds over, so a probe
    recorded through it entered *every* system's posterior -- an extra
    observation, free of budget and missing from the evidence index, silently
    reweighting the belief the run is scored on. B5 was where it showed:
    structural recovery fell from 3 of 9 scenarios to 1, because its beam ranks
    candidates by fit against ``engine.observations`` and was ranking them partly
    on a reading it was never meant to see. See ``docs/DECISIONS.md``.

    Identical for every system by construction, since it is taken from the
    scenario before ``investigate`` is called and no system is consulted about
    it. That is the property SPEC §9's comparison rests on: a Stage A allocation
    given to one arm and not another would make every detection figure a
    statement about the harness.

    The seed is derived from the scenario seed and the design id under the same
    :func:`replicate_seed` every experiment uses, at a step index that no
    experiment can occupy -- Stage A precedes them all, so it takes step
    ``-1``'s place by name rather than by number and cannot collide with the
    first experiment's stream.
    """
    if scenario.stage_a is None:
        return
    design = scenario.stage_a
    seed = replicate_seed(scenario.seed, f"stage_a:{design.id}", 0)
    result = executor.measure(design, scenario.executed, seed)
    engine.record_probe(stage_a_id(scenario), design.template(), result)


def _attempts_of(system: ResearchSystem) -> tuple[ProposalAttempt, ...] | None:
    """Return a system's proposal record, or ``None`` if it holds no layer.

    Guarantees the captured record is readable: :class:`Proposing` is a
    ``runtime_checkable`` protocol, so ``isinstance`` establishes that
    ``attempts`` *exists* and nothing about what it holds. A system whose
    ``attempts`` is ``None`` or is not a tuple of
    :class:`~sciagent.systems.hybrid.ProposalAttempt` would otherwise be filed as
    holding no proposal layer, or would fail later inside
    :func:`~sciagent.eval.agency.proposal_record` as a bare ``AttributeError``
    with the run already scored.

    Raises :class:`~sciagent.core.errors.InvestigationError` in that case, since
    a system that advertises a record it cannot produce is a fault in the system
    and not a run to report an agency figure for.
    """
    if not isinstance(system, Proposing):
        return None
    attempts = system.attempts
    if not isinstance(attempts, tuple) or not all(
        isinstance(attempt, ProposalAttempt) for attempt in attempts
    ):
        raise InvestigationError(
            f"system {system.name!r} exposes 'attempts' but it holds "
            f"{attempts!r}, not a tuple of ProposalAttempt; a system that "
            f"advertises a proposal record must produce one, since an "
            f"unreadable record is not the same as holding no proposal layer"
        )
    return attempts


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
