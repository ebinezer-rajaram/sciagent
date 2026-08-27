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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from sciagent.core.edits import Defect
from sciagent.core.errors import (
    EngineTamperError,
    InvestigationError,
    MalformedClaimError,
    MalformedDesignError,
)
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    CLAIM_MODALITIES,
    CLAIM_STRENGTHS,
    Claim,
    ClaimId,
    ComponentId,
    Diagnosis,
    Direction,
    ExperimentId,
    FrozenDict,
    HypothesisId,
    Intervention,
    Probability,
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
from sciagent.verify import ClaimContext, Verdict, verify
from sciagent.verify.contradiction import zombie
from sciagent.verify.numerical import recompute
from sciagent.verify.relevance import EvidenceIndex
from sciagent.verify.verdict import CheckClass

__all__ = [
    "Adjudication",
    "Proposing",
    "ScenarioRun",
    "adjudicate",
    "claims_from_run",
    "run_scenario",
]


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
    probe. That is :attr:`probe`, and the two are reported side by side rather
    than reconciled: this one is arm-dependent even in its verdict -- on S11 it
    fires for B1, whose space holds only the null, and stays quiet for V1 at the
    same seed -- which is why a criterion read off it alone was comparing arms
    on an instrument that moves with the arm.
    """

    probe: PPCResult | None
    """The Stage A adequacy probe, evaluated by the harness for every arm.

    SPEC §12 criterion 4's observable, under the C1 reading taken cold on
    2026-08-21 (``docs/OPEN-DECISIONS.md`` §1, ``docs/DECISIONS.md``): power
    against size on the named Stage A probe, with the harness evaluating that
    probe for **every** arm regardless of whether the arm consults it. B1 and
    the conventional baselines never call
    :meth:`~sciagent.systems.base.Investigation.ppc`, and a criterion undefined
    for the floor it names is not a criterion.

    **Evaluated before ``investigate`` is called**, on the graph the harness
    handed the system and the Stage A reading :func:`_run_stage_a` has just
    taken. That is the whole of gate A29: the check reads the engine's live set
    and its posterior, both of which a system moves, so a value taken at any
    later point is a function of the arm. Taken here, no system code has run and
    "identical whichever system ran" is structural rather than an agreement
    between arms that happens to hold on one recorded matrix.

    A companion ``adequacy`` field was written on 2026-08-16 and withdrawn the
    same day for evaluating at the other end of the run, and that reasoning is
    what fixes the point here rather than merely arguing for it: the end-of-run
    posterior is the one the arm's *proposals* moved, so a system that
    successfully proposed a structure explaining the probe would be recorded as
    having failed to detect, inverting the criterion. The field also conflated
    two quantities -- whether the space is adequate, which is a property of the
    space and the scenario, and whether a *system* detected it, which B1 cannot
    answer because it never looks. This field is the first of those, and says so
    by being the same number for every arm.

    Not degenerate as an *instrument*: B1 holds only the null and proposes
    nothing, so its live set never changes and its posterior never moves, and
    this *is* B1's probe reading throughout its run -- the check measured on
    2026-08-16 to fire on S11 alone across the twelve slice scenarios. Read down
    a column it discriminates, which is why it is worth recording.

    **It does, however, cost both of C1's clauses, and the cost is larger than
    the first draft of this docstring admitted.** C1 words criterion 4 as two
    comparisons of V7 against B1 -- fires "at a rate at least matching B1's", at
    a false-positive rate "no higher than B1's" -- and
    :func:`~sciagent.eval.matrix.replicate_seeds` already pairs every arm on one
    seed sequence. Two arms reading one instrument at one seed agree exactly, so
    *neither* clause could fail, on S11 or on S1-S7 and S9. An earlier version of
    this paragraph claimed the false-positive term stayed falsifiable; it does
    not, and gate A29's own S1 assertions are what show it.

    **Resolved 2026-08-26, and not by restoring the comparison.** Criterion 4 was
    reworded absolutely -- fires on S11, does not fire on S1-S7 or S9 -- and moved
    to §12's Infrastructure block, because a reading identical across arms grades
    the instrument and cannot grade an agent whatever threshold it carries.
    **Revised once more on 2026-08-27 (gate A47):** the exactly-zero half of
    that wording failed a correctly calibrated probe in all but one campaign
    in ~1,583, so the size clause is now the *pooled* quiet-set rate at or
    below A9's measured 0.045. See
    :func:`~sciagent.eval.report.criterion_four`, which is the check (over
    counts, since rates cannot be pooled across unequal draw counts), gate
    A45, which demonstrates some probe vector fails it, and gate A47, which
    demonstrates a calibrated one passes. What the paragraph above costs is
    therefore permanent and was accepted rather than repaired: V7-versus-B1
    grading on this instrument is gone for good.

    ``None`` where the scenario declares no Stage A design, rather than the
    check over an empty record -- which would be a documented ``p_value = 1``
    and "not inadequate", and would summarise as a probe rate of 0.000
    indistinguishable from a probe evaluated and never fired. The distinction is
    :attr:`attempts`'s, for the same reason.
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
        # `brief()` and not `.designs`, and the two are the same tuple today.
        # `Scenario.brief` is documented as the one whitelist of what a system may
        # be told -- "adding a field here cannot accidentally widen what leaks" --
        # and it had **no call site anywhere in the repository**, so the mechanism
        # holding that line was a docstring. Found by the invariant auditor when
        # `held_out` became the first field added to `Scenario` since the claim
        # was written. Routing through it makes the stated property true.
        designs=scenario.brief(),
        truth=scenario.executed,
        executor=executor,
        engine=engine,
        graph=graph,
        seed=scenario.seed,
        stage_a=stage_a_id(scenario) if scenario.stage_a is not None else None,
    )
    _run_stage_a(scenario, executor=executor, engine=engine)
    # Read here and not after the run, which is gate A29 and not a preference
    # about where a line sits. The scoped check reads the engine's live set and
    # its posterior; `investigate` moves both. Taken on this side of the call
    # the value is a function of the scenario, the seed and the graph the
    # harness built -- so it is the same number for every arm by construction,
    # and no system can move it. See `ScenarioRun.probe`.
    probe = _stage_a_probe(scenario, engine, graph)
    reported = system.investigate(investigation)
    # Read before reconciling, not in the call below. ``Proposing.attempts`` and
    # ``ResearchSystem.name`` are both *properties*, so reading either runs
    # system code -- and every keyword argument after it in ``ScenarioRun`` is
    # evaluated later still, which would let a system that kept its
    # ``Investigation`` add an experiment to ``evidence`` or a node to ``graph``
    # after the check had already passed.
    #
    # ``name`` was missed when ``attempts`` was hoisted for this reason, and it
    # sits *earlier* in the same call -- so the window it opened covered
    # ``ppc``, ``experiments``, ``graph`` and ``evidence``, which is every field
    # a system could still move. Found by the invariant auditor on 2026-08-17.
    # No shipped system exploits it: all five baselines return a string literal.
    attempts = _attempts_of(system)
    name = system.name
    _reconcile(system, investigation, engine)

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
        system=name,
        diagnosis=reported,
        score=closed_world_score(reported, scenario.truth, edits),
        ppc=engine.ppc(),
        probe=probe,
        experiments=len(investigation.history),
        proposed=investigation.proposed,
        attempts=attempts,
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


def _stage_a_probe(
    scenario: Scenario,
    engine: EmpiricalTableEngine,
    graph: HypothesisGraph,
) -> PPCResult | None:
    """Return the adequacy probe as the harness evaluates it, for any arm.

    Guarantees the answer is a function of the engine's state alone, so a caller
    that invokes it before handing the engine to a system gets a value no system
    can have influenced. That is the only guarantee here worth having, and it is
    why this is called where it is rather than beside the other fields.

    The same scoping :meth:`~sciagent.systems.base.Investigation.ppc` applies,
    and it has to be the same: the criterion compares what one arm *acted on*
    against what the others would have seen, so a harness computing a differently
    scoped check would be reporting a rate for a check no system consults. The
    scoping rule is stated once there and the fallback once here because the two
    are called at different points in a run and only agree on an engine holding
    the probe and nothing else.

    Returns ``None`` where the scenario declares no Stage A design. **Not a
    fallback to the check over the recorded record**, which at this point is
    empty and would yield
    :func:`~sciagent.inference.ppc.posterior_predictive_check`'s documented
    ``p_value = 1`` and "not inadequate" -- a real-looking number for a
    measurement never taken. ``None`` is the same distinction
    :attr:`ScenarioRun.attempts` draws, and for the same reason: "no probe
    declared" and "a probe that did not fire" are different facts about a run,
    and one value cannot carry both.

    Raises :class:`~sciagent.core.errors.MalformedDesignError` if the engine
    holds an experiment, or a hypothesis the caller's graph does not.
    **Both checks are the guarantee above, and without them the guarantee is a
    sentence.** Arm-independence is a property of the *caller*: it holds because
    ``run_scenario`` calls this on a fresh engine built from ``graph`` before any
    system has run, and nothing in the signature says so.

    The two conditions are not redundant, because the two degradations they
    catch are different and neither check sees the other's:

    - **An engine reused across scenarios** carries the previous run's
      experiments. ``record_probe`` 's duplicate-id guard does not fire, since
      the ids differ. Caught by ``observations`` -- and *not* by the hypothesis
      check, because an arm like B1 proposes nothing and leaves the set alone.
    - **A hypothesis entertained before the probe is read** moves ``posterior()``
      with ``observations`` still empty: ``expand`` records nothing, and V1, B4
      and V7 all call ``entertain`` ahead of their first experiment. Measured on
      S11, one added structure moves the probe by 7.19e-10 -- small because the
      null's code length dominates an observation-free posterior, which is a
      fact about this grammar and not an asserted bound. Caught by comparing the
      engine's hypotheses against the graph the caller passed, and *not* by
      ``observations``, which is still empty.

    An earlier version checked ``observations`` alone and said it was "exactly
    the quantity that must be empty". It is *a* quantity that must be empty; the
    entertained set is the other, and that half was left as a comment in a
    docstring claiming otherwise. Found by the invariant auditor, which
    demonstrated the drift rather than arguing it.

    See :attr:`ScenarioRun.probe` for why that point is before ``investigate``.
    """
    if engine.observations:
        raise MalformedDesignError(
            f"the Stage A probe was asked for on an engine already holding "
            f"{len(engine.observations)} experiment(s). It is read before a "
            f"system runs so that its verdict is the same whichever system ran "
            f"(gate A29); on a posterior some arm has already moved it is a "
            f"different quantity wearing the same name, and nothing downstream "
            f"could tell the two apart"
        )
    entertained = set(engine.hypotheses) - set(graph.nodes)
    if entertained:
        raise MalformedDesignError(
            f"the Stage A probe was asked for on an engine holding "
            f"{sorted(entertained)!r}, which the graph it was built from does "
            f"not. The check weights its mixture by the posterior over the live "
            f"set, so a structure entertained first moves it with no experiment "
            f"recorded -- and gate A29 is that the verdict is the same whichever "
            f"system ran"
        )
    if scenario.stage_a is None:
        return None
    return engine.ppc(experiments={stage_a_id(scenario)})


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


@dataclass(frozen=True, slots=True)
class Adjudication:
    """What the verifier made of one run's claims.

    Four counts and no verdict of its own. SPEC §12 reads two criteria off
    these, and both are bars a campaign may fail, so nothing here decides
    whether a run passed anything -- that is a comparison a reader makes against
    the criterion, from figures the report carries.
    """

    claims: int
    """How many claims the run afforded, and the denominator of :attr:`rate`."""

    adjudicated: int
    """How many the verifier decided without human input (§12 criterion 10).

    :attr:`~sciagent.verify.Verdict.adjudicated` is *"not referred"* rather than
    *"accepted"*: a claim refused on a mechanical ground has been decided, and
    the criterion is about how much of a population the verifier can settle, not
    about how much of it survives.
    """

    contradictions: int
    """Contradiction findings across every verdict (§12 criterion 8, first half).

    Findings and not claims: one claim can be incoherent with the record on more
    than one ground, and a criterion asking for *zero* contradictions is not
    served by collapsing three into one.
    """

    zombies: int
    """Supporting claims about a rejected hypothesis (criterion 8, second half).

    Beside :attr:`contradictions` because the criterion names two quantities --
    *"zero graph contradictions and zero zombie hypotheses"* -- and one pooled
    count leaves the second underivable: a nonzero figure could be a reversal.

    A subset of :attr:`contradictions` by construction, since every zombie claim
    draws a contradiction finding and this is
    :func:`sciagent.verify.contradiction.zombie`, the very predicate that check
    refuses on.
    """

    def __post_init__(self) -> None:
        """Raise unless the four counts can describe one population.

        :class:`~sciagent.verify.Verdict` asserts its own consistency in the
        same call path -- it refuses to report an outcome gentler than its
        findings imply -- and this is the same discipline one level up. Three
        relations the docstrings above state as facts, made false-able:
        every count is non-negative, no more claims were decided than were
        offered, and every zombie drew a contradiction finding.

        The third is the one worth asserting rather than trusting. ``zombies``
        and ``contradictions`` are counted from **different places** -- the
        first from :func:`sciagent.verify.contradiction.zombie` over the
        population, the second from the findings
        :func:`~sciagent.verify.verify` returned -- and the only thing making
        them agree is that ``check`` refuses on that same predicate. If that
        ever stops being true, the two SPEC §12 criterion 8 figures disagree
        about one run and the report averages both.

        CLAUDE.md's second invariant asks for runtime assertions rather than
        comments, and a frozen dataclass of four bare ints is exactly where a
        comment would otherwise have been the whole of it.
        """
        if min(self.claims, self.adjudicated, self.contradictions, self.zombies) < 0:
            raise MalformedClaimError(
                f"adjudication reports a negative count "
                f"(claims={self.claims}, adjudicated={self.adjudicated}, "
                f"contradictions={self.contradictions}, zombies={self.zombies}); "
                f"every field here is a tally over a claim population"
            )
        if self.adjudicated > self.claims:
            raise MalformedClaimError(
                f"adjudication decided {self.adjudicated} of {self.claims} "
                f"claim(s), which is more than it was offered; `rate` would "
                f"report above 1.0 and pool into a campaign aggregate as though "
                f"SPEC section 12 criterion 10 had been exceeded"
            )
        if self.zombies > self.contradictions:
            raise MalformedClaimError(
                f"adjudication counted {self.zombies} zombie(s) against "
                f"{self.contradictions} contradiction finding(s). Every zombie "
                f"claim is refused *by* the contradiction check, so the first is "
                f"a subset of the second; the two are counted from different "
                f"places and this is where they are made to agree"
            )

    @property
    def rate(self) -> float | None:
        """Return the share of claims adjudicated, or ``None`` for an empty run.

        ``None`` when the run afforded no claim at all, and for
        :attr:`~sciagent.eval.agency.AgencyMetrics.autonomy_fraction`'s reason:
        a rate over an empty population is not 1.0. Reporting it as 1.0 would
        credit a verifier that decided nothing with having decided everything,
        and would pool into a campaign aggregate as though it were evidence that
        criterion 10 was met.
        """
        if self.claims == 0:
            return None
        return self.adjudicated / self.claims


def _about_this_run(
    run: ScenarioRun, population: Sequence[Claim], program: GenerativeProgram
) -> None:
    """Raise unless every claim in ``population`` is a claim about ``run``.

    Guarantees each claim's subject is one this run could be about -- a
    hypothesis it entertained, or a component the reference programme holds --
    and each cited experiment one it registered, so the counts
    :func:`adjudicate` returns are a tally over *this* run rather than over a
    population that happens to have been handed to it.

    **Both halves of** :data:`~sciagent.core.types.SubjectKind`, and the second
    is not decoration. An earlier version checked only ``"hypothesis"`` and its
    guarantee was therefore false of exactly the claims
    ``test_a30_the_rate_falls_when_a_claim_cannot_be_decided`` injects: a
    component-subject claim is the one demonstrated lever on criterion 10 in
    this repository, so the branch the check skipped was the branch that
    mattered.

    The check the ``claims`` seam needs, and the reason it is an assertion
    rather than the sentence in :func:`adjudicate`'s docstring saying claims are
    structure. Item 12 will feed that parameter from an agent, and a claim about
    a hypothesis this run never held is not a claim this run can be graded on --
    counting it moves SPEC §12 criterion 10's denominator with something the
    investigation never entertained. CLAUDE.md's second invariant asks for the
    boundary to be enforced rather than described.

    **It bounds well-formedness, not merit, and the difference is the honest
    limit of what any check here can do.** A claim can pass this and still be
    worthless: the verifier refuses it, a refusal *is* adjudicated (a claim
    decided on a mechanical ground has been decided, which is what criterion 10
    measures), and so a population padded with junk raises the rate toward 1.0.
    That is a property of the criterion rather than of this function -- the same
    one :func:`claims_from_run` names when it says a generator emitting only
    what the verifier accepts would turn A23 into a measurement of itself -- and
    it is recorded in ``docs/DECISIONS.md`` (2026-08-23, A30 after review)
    rather than papered over here. Changing what criterion 10 measures is a SPEC
    §12 question and is nobody's to take inside a gate.

    Raising rather than referring, and deliberately. The verifier's posture is
    that an undecidable claim is *referred* rather than guessed at, but a claim
    citing evidence that does not exist is malformed rather than undecidable:
    :meth:`~sciagent.verify.relevance.EvidenceIndex.record` already raises
    :class:`~sciagent.core.errors.MalformedClaimError` for it, several frames
    deeper and without naming which claim. This is that failure moved to the
    boundary where it can say.
    """
    for claim in population:
        if claim.subject_kind == "hypothesis":
            subject = HypothesisId(str(claim.subject))
            if subject not in run.graph.nodes:
                raise MalformedClaimError(
                    f"claim {claim.id!r} is about hypothesis {subject!r}, which "
                    f"{run.system!r}'s run on {run.scenario.id!r} never "
                    f"entertained; the graph holds "
                    f"{sorted(run.graph.nodes)!r}. Adjudicating it would count a "
                    f"claim about another investigation toward this one's"
                )
        else:
            component = ComponentId(str(claim.subject))
            if component not in program.components:
                raise MalformedClaimError(
                    f"claim {claim.id!r} is about component {component!r}, which "
                    f"the reference programme does not hold; it names "
                    f"{sorted(program.components)!r}. A claim about a component "
                    f"no programme has is not a claim about this run"
                )
        for experiment in claim.evidence:
            if experiment not in run.evidence.records:
                raise MalformedClaimError(
                    f"claim {claim.id!r} cites experiment {experiment!r}, which "
                    f"{run.system!r}'s run on {run.scenario.id!r} never "
                    f"registered. A claim resting on evidence the run does not "
                    f"hold cannot be adjudicated against it"
                )


def adjudicate(
    run: ScenarioRun,
    *,
    program: GenerativeProgram,
    claims: Sequence[Claim] | None = None,
) -> Adjudication:
    """Adjudicate one run's claims and return what the verifier found.

    Guarantees every claim is put through :func:`sciagent.verify.verify` in the
    order given, that the counts returned are of what that function decided, and
    that nothing here reads a number a system reported: the context is built
    from the graph the run ended with, the evidence it registered and the
    posterior the engine's own state implies.

    This is :func:`~sciagent.verify.verify`'s **production caller**. Before it,
    every call in the repository was in a test, so §12 criterion 10's figure
    described a synthetic population rather than a campaign, and criterion 8
    counted objects no recorded run ever created.

    ``claims`` defaults to :func:`claims_from_run`. It is a parameter because
    the claims will come from an agent once one exists, and because a count no
    input can move is not a measurement -- a rejected hypothesis is given
    exactly zero mass and ``claims_from_run`` filters to mass above zero, so the
    generated population *cannot* contain a zombie and criterion 8's zero is
    unfalsifiable without a way in.

    **A caller says which claims are adjudicated and cannot say how any one of
    them is decided** -- every outcome comes from :func:`~sciagent.verify.verify`
    and every count from this function. An earlier version of this paragraph put
    that as *"claims are structure"*, which is **false** and was corrected after
    review rather than quietly softened.
    :attr:`~sciagent.core.types.Claim.effect` is a number, and it selects which
    grading path runs: :mod:`sciagent.verify.statistical` takes
    ``_from_effect`` when a claim carries one and ``_from_predictions`` when it
    does not, and only the second can return ``REFER``. So attaching a
    *fabricated* effect to an otherwise-referrable claim converts it to a
    refusal -- and a refusal **is** adjudicated, which is criterion 10's own
    definition of decided. Fabricating a number therefore raises the rate.

    That is not closed here, and cannot be. Refusing a fabricated effect at this
    boundary would take the work away from :mod:`sciagent.verify.numerical`,
    whose whole purpose -- gate A19 -- is to catch exactly that; the verifier is
    *supposed* to receive fabricated effects and refuse them. What the mechanism
    actually shows is a property of criterion 10, recorded in
    ``docs/DECISIONS.md`` (2026-08-23, A30 after review) alongside the volume
    case: a bar counting refusals as decisions rewards a caller for supplying
    more claims, whatever they say. Changing that is SPEC §12 and frozen.

    **Accumulating, and within this run only.** A claim the verifier accepts
    enters :attr:`~sciagent.verify.ClaimContext.accepted` for the claims after
    it, which is what
    :mod:`sciagent.verify.contradiction`'s cross-contradiction and reversal
    rules read; against the empty tuple every earlier caller passed, two of that
    module's three rules are unreachable code. Only claims the verdict
    *accepts* are carried -- ``accepted`` is documented as the claims already
    **admitted**, and putting refused ones there would let the record contradict
    itself with claims the verifier threw out.

    Accumulating across a *campaign* would be a bug rather than a stronger
    version of this: a cell's reading would become a function of which cells ran
    before it, so a resumed pass would score differently at the same content
    address. That is the third invariant, and it is why the accumulator is a
    local here rather than anything the caller holds.
    """
    population = tuple(claims_from_run(run) if claims is None else claims)
    _about_this_run(run, population, program)
    context = ClaimContext(
        graph=run.graph,
        evidence=run.evidence,
        program=program,
        posterior=FrozenDict[HypothesisId, Probability](run.diagnosis.distribution),
    )
    admitted: list[Claim] = []
    verdicts: list[Verdict] = []
    for claim in population:
        verdict = verify(claim, replace(context, accepted=tuple(admitted)))
        verdicts.append(verdict)
        if verdict.accepted:
            admitted.append(claim)
    return Adjudication(
        claims=len(population),
        adjudicated=sum(1 for verdict in verdicts if verdict.adjudicated),
        contradictions=sum(
            1
            for verdict in verdicts
            for finding in verdict.findings
            if finding.check is CheckClass.CONTRADICTION
        ),
        zombies=sum(1 for claim in population if zombie(claim, run.graph)),
    )


def _reconcile(
    system: ResearchSystem,
    investigation: Investigation,
    engine: EmpiricalTableEngine,
) -> None:
    """Raise unless the engine's evidence is exactly what the run charged for.

    The check :func:`_audit` cannot be. ``_audit`` re-derives the expected
    diagnosis from *the same engine object* the system was handed, so it sees a
    system whose reported numbers disagree with the engine and is blind to one
    that changed the engine and then reported honestly -- there, both sides are
    computed from the poisoned state and agree.

    So this compares things that come from different places. **Evidence:** what
    the engine holds against what
    :class:`~sciagent.systems.base.Investigation` recorded as it charged each
    experiment to the budget. **Hypotheses:** what the engine entertains against
    what the graph admitted. The posterior is a pure function of the first pair
    and is distributed over the second, so either disagreeing means the belief
    rests on something the run cannot cite.

    **What is compared, and why each clause is here.**

    1. *Evidence* -- engine observations against ``investigation.history``, by id
       and in order. Catches a fabricated ``record``, and a substitution as well
       as an addition, for the same cost as counting.
    2. *Structure* -- each entertained hypothesis's edit set against the graph's.
       Catches ``expand``, which admits a structure without ``graph.propose``'s
       A16/A18 validation; and catches a rewrite of an existing hypothesis's edit
       set, which ``engine_edits`` feeds straight into ``closed_world_score`` and
       ``structural_distance``.
    3. *Status* -- the engine's live set against the graph's non-rejected nodes.
       A rejected hypothesis is given exactly zero and the rest renormalise over
       themselves, so a status flip is a redistribution of mass. Nothing in the
       framework rejects, so this clause is quiet on every honest run by
       construction rather than by luck.

    Clauses 2 and 3 were both absent from earlier versions and both were found by
    review rather than by this reasoning -- clause 2 against a system that
    smuggled a hypothesis in and moved its own log score from ``-inf`` to
    ``-12.2`` while clause 1 passed. That is the argument for stating the scope
    below rather than implying completeness.

    **Not covered.** Replacement of the engine's table. It changes legitimately
    under ``ensure_structure``, so there is no fixed expectation to compare it
    against, and a caller able to reach the private engine to swap it could
    equally reach anything else. This function is a check with a scope, not a
    containment proof, and the scope is these three clauses.

    ``ensure_structure`` needs no clause here. It fills a table row and admits
    nothing, and
    :meth:`~sciagent.inference.empirical.EmpiricalTable.with_structure` leaves
    every existing row untouched, so it moves no mass. Probes are not compared
    either: they live in a compartment with no public accessor, and adding one
    to reconcile them would widen the surface this exists to narrow.

    Exact equality is right, not merely convenient. ``_run_stage_a`` takes its
    reading through
    :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.record_probe`,
    which ``observations`` does not expose, so the framework itself adds nothing
    here beyond what :meth:`~sciagent.systems.base.Investigation.run` appended.
    """
    held = tuple(observation.experiment for observation in engine.observations)
    charged = tuple(result.experiment for result in investigation.history)
    if held != charged:
        raise EngineTamperError(
            f"system {system.name!r} left the engine holding experiments {held!r}, "
            f"but the investigation ran and charged for {charged!r}. The posterior "
            f"is a function of what the engine holds, so a system that records "
            f"writes its own score; no system may author a number (SPEC §1, second "
            f"invariant)"
        )
    entertained = {h: engine.program_edit(h) for h in engine.hypotheses}
    admitted = {
        node_id: node.program_edit
        for node_id, node in investigation.graph.nodes.items()
        if node.program_edit is not None
    }
    if entertained != admitted:
        raise EngineTamperError(
            f"system {system.name!r} left the engine entertaining "
            f"{sorted(entertained)!r}, but the graph admitted {sorted(admitted)!r}, "
            f"or the two disagree about a structure. Mass is distributed over what "
            f"the engine holds and the score is read off the structures it holds, "
            f"so either divergence lets a system author its own result; no system "
            f"may author a number (SPEC §1, second invariant)"
        )
    live_held = frozenset(engine.live)
    live_admitted = frozenset(
        node_id
        for node_id, node in investigation.graph.nodes.items()
        if node.program_edit is not None and node.status != "rejected"
    )
    if live_held != live_admitted:
        raise EngineTamperError(
            f"system {system.name!r} left the engine treating "
            f"{sorted(live_held)!r} as live, but the graph makes "
            f"{sorted(live_admitted)!r} live. A rejected hypothesis is given "
            f"exactly zero and the rest renormalise over themselves, so a status "
            f"flip is a redistribution of mass; no system may author a number "
            f"(SPEC §1, second invariant)"
        )


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
