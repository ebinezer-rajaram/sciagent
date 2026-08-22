"""The systems layer: what an investigation permits, and what it refuses.

The load-bearing tests here are the ones about SPEC's second invariant. A
research system is exactly the place that invariant is aimed at, so "no
agent-reachable path may set a posterior value" has to be checked against a
system that tries, not merely asserted about one that does not.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, slice_table

from environments.pointproc.operations import arrival_burst
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import (
    BudgetExhaustedError,
    DiagnosisError,
    DuplicateExperimentError,
    EngineTamperError,
    InvestigationError,
    MalformedDesignError,
)
from sciagent.core.types import (
    ComponentId,
    Diagnosis,
    ExperimentId,
    FrozenDict,
    HypothesisId,
    Probability,
    ScenarioId,
)
from sciagent.eval.campaign import ScenarioRun, run_scenario, stage_a_id
from sciagent.eval.scenarios import Scenario
from sciagent.eval.scoring import closed_world_score
from sciagent.experiments.dsl import ExperimentDesign, ForceArrival
from sciagent.experiments.executor import Executor
from sciagent.hypothesis.graph import HypothesisGraph, HypothesisNode
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import (
    Investigation,
    ResearchSystem,
    entertain,
    null_seeded_graph,
)
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly
from sciagent.systems.baselines.retrieval import Retrieval


class _Harness(NamedTuple):
    """Everything one run needs, built the way the gate builds it."""

    scenario: Scenario
    executor: Executor
    engine: EmpiricalTableEngine
    graph: HypothesisGraph


def _harness(scenario_id: str = "S1") -> _Harness:
    """Return a scenario and the three objects a run is performed against."""
    the_scenario = scenario(scenario_id)
    table = slice_table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    return _Harness(
        scenario=the_scenario,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR)),
        graph=graph,
    )


def _run(system: ResearchSystem, scenario_id: str = "S1") -> ScenarioRun:
    """Run one system on one scenario through the real harness."""
    harness = _harness(scenario_id)
    return run_scenario(
        harness.scenario,
        system,
        executor=harness.executor,
        engine=harness.engine,
        graph=harness.graph,
    )


class TestTheFrameworkWritesTheNumbers:
    """SPEC §1, second invariant, enforced rather than documented."""

    def test_a_system_that_fabricates_a_posterior_is_refused(self) -> None:
        """The whole point. A plausible number nobody audits is the failure mode."""

        class Liar:
            name = "liar"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                # Entertain the closed set first, so the honest posterior is
                # spread over five structures and a claim of certainty is a
                # genuine fabrication rather than a coincidence.
                entertain(investigation, closed_set())
                investigation.run(investigation.designs[0])
                honest = investigation.conclude()
                assert len(honest.distribution) > 1
                return Diagnosis(
                    scenario_id=honest.scenario_id,
                    distribution=FrozenDict[HypothesisId, Probability](
                        {HypothesisId("hawkes"): Probability(1.0)}
                    ),
                    abstain_mass=Probability(0.0),
                    null_mass=Probability(0.0),
                    proposed_edits=honest.proposed_edits,
                    supporting=honest.supporting,
                    residual_candidates=(),
                )

        with pytest.raises(InvestigationError, match="no system may author a number"):
            _run(Liar())

    def test_a_system_that_inflates_only_its_abstain_mass_is_refused(self) -> None:
        """Every field is compared, not just the distribution."""

        class Hedger:
            name = "hedger"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                investigation.run(investigation.designs[0])
                honest = investigation.conclude()
                return Diagnosis(
                    scenario_id=honest.scenario_id,
                    distribution=honest.distribution,
                    abstain_mass=Probability(0.99),
                    null_mass=honest.null_mass,
                    proposed_edits=honest.proposed_edits,
                    supporting=honest.supporting,
                    residual_candidates=(),
                )

        with pytest.raises(InvestigationError, match="abstain_mass"):
            _run(Hedger())

    def test_an_unnormalised_diagnosis_cannot_even_be_built(self) -> None:
        with pytest.raises(DiagnosisError, match="normalised distribution"):
            Diagnosis(
                scenario_id=ScenarioId("S1"),
                distribution=FrozenDict[HypothesisId, Probability](
                    {HypothesisId("a"): Probability(0.3)}
                ),
                abstain_mass=Probability(0.0),
                null_mass=Probability(0.0),
                proposed_edits=FrozenDict(),
                supporting=FrozenDict(),
                residual_candidates=(),
            )

    def test_an_honest_system_passes_the_same_audit(self) -> None:
        """The check must not be one no real system can satisfy."""
        assert _run(PPCOnly()).system == "B1"


class TestTheEngineIsSealedAgainstTheSystem:
    """Invariant 2 held by construction, not by a docstring.

    ``Investigation.engine`` used to return the live
    :class:`~sciagent.inference.empirical.EmpiricalTableEngine`, whose docstring
    claimed it was "read-only in effect". It was not: ``record`` folds straight
    into ``log_likelihood_total`` and therefore into ``posterior()``, so a system
    that called it wrote the belief it is scored on. ``_audit`` cannot catch
    that -- it re-derives the expected diagnosis from *the same engine object*
    the system was handed, so a system that poisons the state and then reports
    honestly is compared against its own poisoned source and both sides agree.

    Two defences, tested separately because either alone is weak: the view
    withholds the capability, and ``run_scenario`` reconciles what the engine
    holds against what the investigation charged for.
    """

    def _view(self) -> object:
        """Return the engine as a system sees it."""
        harness = _harness()
        return Investigation(
            scenario_id=harness.scenario.id,
            designs=harness.scenario.designs,
            truth=harness.scenario.executed,
            executor=harness.executor,
            engine=harness.engine,
            graph=harness.graph,
            seed=harness.scenario.seed,
        ).engine

    def test_a_system_cannot_record_an_observation(self) -> None:
        """The channel that writes the posterior. ``record`` is not reachable."""
        assert not hasattr(self._view(), "record")

    def test_a_system_cannot_record_a_probe(self) -> None:
        """``record_probe`` plus a scoped ``ppc`` is a self-authored verdict."""
        assert not hasattr(self._view(), "record_probe")

    def test_a_system_cannot_admit_a_hypothesis_to_the_engine(self) -> None:
        """``expand`` bypasses ``graph.propose`` and its A16/A18 validation."""
        assert not hasattr(self._view(), "expand")

    def test_a_system_cannot_scope_the_adequacy_check(self) -> None:
        """``Investigation.ppc`` is the sanctioned path, and it scopes to Stage A.

        ``engine.ppc(experiments={...})`` chooses its own evidence, which is the
        whole thing the scoping exists to prevent.
        """
        assert not hasattr(self._view(), "ppc")

    def test_the_engine_itself_has_no_public_accessor_on_the_view(self) -> None:
        """A view that hands back what it wraps is not a boundary.

        The same check ``test_the_ground_truth_has_no_public_accessor`` makes of
        an investigation, for the same reason: withholding the methods is worth
        nothing if the object carrying them is one attribute away.
        """
        harness = _harness()
        view = Investigation(
            scenario_id=harness.scenario.id,
            designs=harness.scenario.designs,
            truth=harness.scenario.executed,
            executor=harness.executor,
            engine=harness.engine,
            graph=harness.graph,
            seed=harness.scenario.seed,
        ).engine
        for name in (n for n in dir(view) if not n.startswith("_")):
            assert getattr(view, name, None) is not harness.engine, name

    def test_the_view_still_carries_what_the_baselines_read(self) -> None:
        """A boundary that withholds too much is a different bug, not a fix."""
        view = self._view()
        for name in ("table", "observations", "live", "hypotheses", "posterior"):
            assert hasattr(view, name), name

    def test_a_poisoned_engine_is_refused(self) -> None:
        """The reconciliation, against a system that reaches the engine anyway.

        It records a second copy of a real result under a fresh id -- valid in
        every way ``_validated`` checks, and double-counting the evidence
        sharpens the posterior -- then concludes honestly. Before the
        reconciliation this ran to completion and produced a score.
        """
        harness = _harness()

        class Poisoner:
            name = "poisoner"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                entertain(investigation, closed_set())
                result = investigation.run(investigation.designs[0])
                harness.engine.record(
                    ExperimentId("fabricated"),
                    investigation.designs[0].template(),
                    result.result,
                )
                return investigation.conclude()

        with pytest.raises(EngineTamperError, match="fabricated"):
            run_scenario(
                harness.scenario,
                Poisoner(),
                executor=harness.executor,
                engine=harness.engine,
                graph=harness.graph,
            )

    def test_a_system_cannot_act_from_the_property_the_harness_reads(self) -> None:
        """``ResearchSystem.name`` is a property, so reading it runs system code.

        The hazard the hoist of ``Proposing.attempts`` was for, at a site that
        was missed: ``system=system.name`` sat *inside* the ``ScenarioRun``
        call, and keyword arguments evaluate left to right, so everything after
        it -- ``ppc``, ``experiments``, ``graph``, ``evidence`` -- was read
        after the system had had one more turn. Every one of those becomes an
        append-only ledger row under SPEC §11 item 15, and ``inadequate`` is
        §9's conditioning variable, so the window wrote durable numbers.

        Found by the ``invariant-auditor`` on 2026-08-17, not by this suite, and
        latent rather than live: every shipped ``name`` returns a literal. This
        system instead spends an experiment when asked its name.

        Hoisting ``name`` above ``_reconcile`` puts that late experiment back
        inside the audited window, so the run is **refused**. Before the hoist
        it completed: ``_audit`` compared a diagnosis built from one experiment
        against an engine holding one, agreed, and only then did reading
        ``name`` charge the second -- leaving a ``ScenarioRun`` whose ``ppc``,
        ``experiments`` and ``evidence`` described two experiments while its
        diagnosis described one. Nothing raised, and under item 15 that
        inconsistency is what reaches the ledger.
        """
        harness = _harness()

        class LateActor:
            def __init__(self) -> None:
                self.investigation: Investigation | None = None
                self.asked = False

            @property
            def name(self) -> str:
                if self.investigation is not None and not self.asked:
                    self.asked = True
                    self.investigation.run(self.investigation.designs[1])
                return "late-actor"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                self.investigation = investigation
                entertain(investigation, closed_set())
                investigation.run(investigation.designs[0])
                return investigation.conclude()

        system = LateActor()
        with pytest.raises(InvestigationError, match="late-actor"):
            run_scenario(
                harness.scenario,
                system,
                executor=harness.executor,
                engine=harness.engine,
                graph=harness.graph,
            )
        assert system.asked, "the property must actually have been read"

    def test_a_smuggled_hypothesis_is_refused(self) -> None:
        """The half the first version of the reconciliation did not have.

        ``expand`` admits a structure to the engine without ``graph.propose``'s
        A16/A18 validation, and mass is distributed over what the *engine*
        holds. So a hypothesis the graph never saw still takes weight and still
        scores, while the evidence half of the check passes untouched -- the
        system ran and charged for exactly the experiments it recorded.

        Found by review rather than by this suite. Against the evidence-only
        version it raised nothing and moved the system's log score from ``-inf``
        to ``-12.2``.
        """
        harness = _harness()

        class Smuggler:
            name = "smuggler"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                investigation.run(investigation.designs[0])
                harness.engine.expand(
                    HypothesisNode(
                        id=HypothesisId("smuggled"),
                        program_edit=closed_set()["hawkes"],
                        status="live",
                        predictions=(),
                        plausibility=Probability(0.0),
                        rationale="",
                        rejection_reason=None,
                        proposed_at=None,
                        version=1,
                    )
                )
                return investigation.conclude()

        with pytest.raises(EngineTamperError, match="smuggled"):
            run_scenario(
                harness.scenario,
                Smuggler(),
                executor=harness.executor,
                engine=harness.engine,
                graph=harness.graph,
            )

    def test_a_rewritten_structure_is_refused(self) -> None:
        """The sharpest channel of the three, because it sets the score directly.

        ``engine_edits`` reads each hypothesis's edit set straight off the
        engine, and `campaign.run_scenario` passes that mapping to
        ``closed_world_score`` and to ``structural_distance``. So rewriting the
        leading hypothesis's structure to the scenario's truth awards an exact
        match without touching a single id -- the evidence and identity clauses
        both pass, because nothing was added or removed.
        """
        harness = _harness()
        truth = harness.scenario.truth

        class Forger:
            name = "forger"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                investigation.run(investigation.designs[0])
                held = harness.engine._hypotheses[HypothesisId("null")]
                held.program_edit = truth
                return investigation.conclude()

        with pytest.raises(EngineTamperError, match="disagree about a structure"):
            run_scenario(
                harness.scenario,
                Forger(),
                executor=harness.executor,
                engine=harness.engine,
                graph=harness.graph,
            )

    def test_an_honest_system_that_runs_experiments_still_passes(self) -> None:
        """The control. A reconciliation over two empty tuples proves nothing."""
        run = _run(BOEDOnly(closed_set()))
        assert run.experiments > 0


class TestWhatAnInvestigationRefuses:
    """The boundary a system is held to."""

    def test_the_ground_truth_has_no_public_accessor(self) -> None:
        """A system runs designs *against* the truth without being able to read it."""
        harness = _harness()
        truth = closed_set()["hawkes"]
        investigation = Investigation(
            scenario_id=ScenarioId("S1"),
            designs=slice_designs(),
            truth=truth,
            executor=harness.executor,
            engine=harness.engine,
            graph=harness.graph,
            seed=slice_table().seed,
        )
        public = [name for name in dir(investigation) if not name.startswith("_")]
        assert "truth" not in public
        for name in public:
            assert getattr(investigation, name, None) != truth

    def test_a_design_the_scenario_does_not_offer_is_refused(self) -> None:
        class Trespasser:
            name = "trespasser"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                intruder = ExperimentDesign(
                    operation=ForceArrival(
                        component=ComponentId("arrival"),
                        at=arrival_burst(count=20, spacing=0.05),
                        observe=20,
                    ),
                    outcome=investigation.designs[0].outcome,
                    n_events=512,
                )
                investigation.run(intruder)
                return investigation.conclude()

        with pytest.raises(InvestigationError, match="does not offer"):
            _run(Trespasser())

    def test_overspending_raises_rather_than_being_clamped(self) -> None:
        class Spendthrift:
            name = "spendthrift"

            def investigate(self, investigation: Investigation) -> Diagnosis:
                for _ in range(int(investigation.budget.total) + 1):
                    investigation.run(investigation.designs[0])
                return investigation.conclude()

        with pytest.raises(BudgetExhaustedError):
            _run(Spendthrift())


class TestTheBaselinesDoWhatSpecFiveSays:
    """Each baseline's defining property, as SPEC §5's table states it."""

    def test_b1_proposes_nothing(self) -> None:
        """SPEC §5: "PPC-only detector | Stage A floor; proposes nothing"."""
        run = _run(PPCOnly())
        assert run.proposed == {}
        assert run.diagnosis.proposed_edits == {}

    def test_v1_entertains_the_whole_closed_set_and_adds_nothing(self) -> None:
        """SPEC §5: "BOED-only over the closed set ... no representation change"."""
        run = _run(BOEDOnly(closed_set()))
        assert set(run.diagnosis.distribution) == set(closed_set())

    def test_b4_proposes_only_library_structures(self) -> None:
        run = _run(Retrieval(closed_set()))
        library = {frozenset(edit) for edit in closed_set().values()}
        for edit in run.proposed.values():
            assert frozenset(edit) in library

    def test_b4_shortlists_at_most_what_it_was_asked_for(self) -> None:
        run = _run(Retrieval(closed_set(), shortlist=1))
        # The null is seeded, so a one-entry shortlist adds at most one more.
        assert len(run.proposed) <= 1

    def test_a_shortlist_of_zero_is_refused(self) -> None:
        with pytest.raises(InvestigationError, match="at least one"):
            Retrieval(closed_set(), shortlist=0)

    def test_every_baseline_satisfies_the_protocol(self) -> None:
        for system in (PPCOnly(), BOEDOnly(closed_set()), Retrieval(closed_set())):
            assert isinstance(system, ResearchSystem)


class TestAProbeIsNotEvidence:
    """SPEC §4.6's Stage A reading reaches the check and nothing else.

    The guard on a defect that shipped for a day and was found by its
    consequences rather than by a test. ``_run_stage_a`` recorded the probe
    through ``engine.record``, which appends to the list
    ``log_likelihood_total`` folds over -- so the reading entered every system's
    posterior: an extra observation, charged to no budget and absent from the
    run's evidence index, silently reweighting the belief the run is scored on.
    What surfaced was B5's structural recovery falling from three scenarios of
    nine to one, because its beam ranks candidates by fit against
    ``engine.observations``.

    Nothing in the suite said so directly. These tests do, so that a future
    ``record`` where ``record_probe`` belongs fails by name instead of moving a
    baseline's score for reasons nobody can see.
    """

    def _probe(self) -> tuple[EmpiricalTableEngine, ExperimentId]:
        """Take S11's Stage A reading into a fresh engine, and hand both back."""
        harness = _harness("S11")
        design = harness.scenario.stage_a
        assert design is not None, "S11 declares a Stage A probe"
        result = harness.executor.measure(
            design, harness.scenario.executed, harness.scenario.seed
        )
        experiment = ExperimentId("probe")
        harness.engine.record_probe(experiment, design.template(), result)
        return harness.engine, experiment

    def test_a_reused_engine_cannot_supply_a_second_probe(self) -> None:
        """The probe's arm-symmetry is the caller's to preserve, and is checked.

        Gate A29 reads the Stage A probe before ``investigate`` so that its
        verdict is the same whichever system ran. That holds because
        ``run_scenario`` is handed a *fresh* engine, which nothing in its
        signature requires. A caller reusing one engine across two scenarios
        would compute the second probe over a posterior the first arm's run had
        already moved, and the recorded value would become arm-dependent with
        nothing complaining.

        The duplicate-id guard in ``record_probe`` does not cover this: it fires
        on reuse within one scenario, where ``stage_a/<id>`` collides, and these
        are two scenarios with two ids. Raised as a suspicion by the invariant
        auditor, which pointed out that the guarantee was stated in a docstring
        and enforced nowhere.
        """
        first = _harness("S11")
        run_scenario(
            first.scenario,
            PPCOnly(),
            executor=first.executor,
            engine=first.engine,
            graph=first.graph,
        )
        second = _harness("S1")
        with pytest.raises(MalformedDesignError, match="Stage A probe"):
            run_scenario(
                second.scenario,
                PPCOnly(),
                executor=second.executor,
                engine=first.engine,
                graph=first.graph,
            )

    def test_a_hypothesis_entertained_before_the_probe_is_refused(self) -> None:
        """The other half of the guard, and the half `observations` cannot see.

        ``expand`` records no observation, so a structure entertained before the
        probe is read moves ``posterior()`` with ``engine.observations`` still
        empty -- and V1, B4 and V7 all call ``entertain`` ahead of their first
        experiment. The drift is small (measured 7.19e-10 on S11 for one added
        structure, because the null's code length dominates an observation-free
        posterior) but the bound is a fact about this grammar rather than an
        asserted invariant, and gate A29's claim is that the verdict does not
        depend on the arm at all.

        Demonstrated by the invariant auditor against the first version of this
        guard, which checked ``observations`` alone and let this through.
        """
        harness = _harness("S11")
        entertain(
            Investigation(
                scenario_id=harness.scenario.id,
                designs=harness.scenario.designs,
                truth=harness.scenario.executed,
                executor=harness.executor,
                engine=harness.engine,
                graph=harness.graph,
                seed=harness.scenario.seed,
            ),
            closed_set(),
        )
        assert harness.engine.observations == ()
        with pytest.raises(MalformedDesignError, match="Stage A probe"):
            run_scenario(
                harness.scenario,
                PPCOnly(),
                executor=harness.executor,
                engine=harness.engine,
                graph=harness.graph,
            )

    def test_a_probe_moves_no_posterior_mass(self) -> None:
        """The one that matters. A reading about the space is not evidence in it.

        SPEC §4.6's Stage A asks whether the entertained set is adequate at all.
        A reading answering that must not also redistribute mass *within* the
        set, or the adequacy verdict is partly a consequence of the belief it
        exists to audit.
        """
        harness = _harness("S11")
        design = harness.scenario.stage_a
        assert design is not None
        before = dict(harness.engine.posterior())
        result = harness.executor.measure(
            design, harness.scenario.executed, harness.scenario.seed
        )
        harness.engine.record_probe(ExperimentId("probe"), design.template(), result)
        assert dict(harness.engine.posterior()) == before

    def test_a_probe_is_absent_from_the_record_a_system_reads(self) -> None:
        """``observations`` is what B5 ranks on and what ``_supporting`` walks."""
        engine, experiment = self._probe()
        assert experiment not in {o.experiment for o in engine.observations}

    def test_an_unscoped_check_does_not_fold_the_probe_in(self) -> None:
        """Else the reported p-value cites evidence the run's index cannot show."""
        engine, experiment = self._probe()
        assert experiment not in engine.ppc().per_experiment

    def test_naming_the_probe_reaches_it(self) -> None:
        """The whole reason it is recorded rather than discarded."""
        engine, experiment = self._probe()
        assert experiment in engine.ppc(experiments={experiment}).per_experiment

    def test_a_probe_cannot_shadow_an_experiment(self) -> None:
        """Ids are unique across both compartments, so a check is never ambiguous."""
        harness = _harness("S11")
        design = harness.scenario.stage_a
        assert design is not None
        result = harness.executor.measure(
            design, harness.scenario.executed, harness.scenario.seed
        )
        experiment = ExperimentId("probe")
        harness.engine.record_probe(experiment, design.template(), result)
        with pytest.raises(DuplicateExperimentError):
            harness.engine.record(experiment, design.template(), result)

    def test_the_stage_a_reading_is_not_in_a_runs_evidence(self) -> None:
        """End to end: a real run's index holds the experiments it can cite."""
        run = _run(PPCOnly(), "S11")
        cited = {record.experiment for record in run.evidence.ordered()}
        assert stage_a_id(run.scenario) not in cited


class TestDeterminism:
    """SPEC's third invariant, at the level of a whole investigation."""

    def test_two_runs_of_one_system_agree_exactly(self) -> None:
        first, second = _run(PPCOnly()), _run(PPCOnly())
        assert first.diagnosis.distribution == second.diagnosis.distribution
        assert [e for e in first.diagnosis.supporting] == [
            e for e in second.diagnosis.supporting
        ]
        # The check too, not just the posterior. Since 2026-08-16 a Stage A probe
        # is recorded before every run under a seed derived from the scenario's,
        # and the p-value is the only place a difference in that derivation would
        # show -- the probe moves no posterior mass by construction, so the two
        # assertions above would agree whatever it did.
        assert first.ppc == second.ppc

    def test_two_scenarios_sharing_a_truth_are_not_the_same_run(self) -> None:
        """S1 and S5 are both Hawkes; distinct seeds must make them distinct."""
        s1, s5 = _run(BOEDOnly(closed_set()), "S1"), _run(BOEDOnly(closed_set()), "S5")
        assert s1.diagnosis.distribution != s5.diagnosis.distribution


class TestClosedWorldScoring:
    """SPEC §8's closed-world case."""

    def test_certainty_in_the_truth_scores_zero_bits(self) -> None:
        edits = {HypothesisId("h"): closed_set()["hawkes"]}
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S1"),
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId("h"): Probability(1.0)}
            ),
            abstain_mass=Probability(0.0),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        score = closed_world_score(diagnosis, closed_set()["hawkes"], edits)
        assert score.log_score == 0.0
        assert score.identified

    def test_zero_mass_on_the_truth_scores_minus_infinity(self) -> None:
        edits = {HypothesisId("h"): closed_set()["seasonality"]}
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S1"),
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId("h"): Probability(1.0)}
            ),
            abstain_mass=Probability(0.0),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        score = closed_world_score(diagnosis, closed_set()["hawkes"], edits)
        assert score.log_score == -math.inf
        assert not score.correct

    def test_a_bare_lead_is_correct_but_not_identified(self) -> None:
        """ "Recovered the diagnosis" must not mean "led without a majority"."""
        edits = {
            HypothesisId("a"): closed_set()["hawkes"],
            HypothesisId("b"): closed_set()["regime_switching"],
            HypothesisId("c"): closed_set()["seasonality"],
        }
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S10"),
            distribution=FrozenDict[HypothesisId, Probability](
                {
                    HypothesisId("a"): Probability(0.4),
                    HypothesisId("b"): Probability(0.35),
                    HypothesisId("c"): Probability(0.25),
                }
            ),
            abstain_mass=Probability(0.6),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        score = closed_world_score(diagnosis, closed_set()["hawkes"], edits)
        assert score.correct
        assert not score.identified

    def test_mass_on_an_unknown_structure_raises(self) -> None:
        diagnosis = Diagnosis(
            scenario_id=ScenarioId("S1"),
            distribution=FrozenDict[HypothesisId, Probability](
                {HypothesisId("ghost"): Probability(1.0)}
            ),
            abstain_mass=Probability(0.0),
            null_mass=Probability(0.0),
            proposed_edits=FrozenDict(),
            supporting=FrozenDict(),
            residual_candidates=(),
        )
        with pytest.raises(DiagnosisError, match="unknown to the scorer"):
            closed_world_score(diagnosis, closed_set()["hawkes"], {})
