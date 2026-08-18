"""Agency metrics (SPEC §11 item 14), and what the two approval tiers are.

Not named ``test_aN_``: item 14 carries no A-gate, so ``scripts/status.py``
derives no coverage from this module. Its gate is "computed over slice runs",
and :class:`TestOverSliceRuns` is what discharges it -- every metric asserted
below comes off a real run of a real system on a real scenario, not off a
constructed fixture, except where a test is about the metric's *guard* and says
so.

What the tiers are
------------------

SPEC F10 says "two approval tiers" and §12 criterion 11 says an autonomy
fraction is reported for every investigation. Neither says where the boundary
is, and ``docs/DECISIONS.md`` records the reading this module tests:

* **Tier 1**, unilateral: running one of the designs the scenario offers.
* **Tier 2**, escalated: introducing a hypothesis *after* evidence is in hand.

The boundary is F9's lateness datum -- ``proposed_at`` on the graph node -- and
not the count of structures a system introduced. Every system but B1 introduces
structure before it spends anything: that is the space the investigation was set
up with, and charging a system for assembling it would make the fraction depend
on library size. A *late* hypothesis is the one F9 already treats as needing a
prospectively registered experiment before it can support a confirmatory claim,
which is as close to "this needed sign-off" as the framework has.

``proposed_at`` is written by :meth:`~sciagent.systems.base.Investigation.propose`
from the investigation's own history. No system can set it, so the fraction is
not a number an agent can move in its own favour by writing a different
rationale -- which reading the ``rationale`` string would have allowed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from functools import lru_cache
from typing import Any

import pytest
from baseline_runs import runs as baseline_runs
from slice_tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    gate_table,
    save_gate_table,
)

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.edits import Defect
from sciagent.core.errors import InvestigationError
from sciagent.core.types import Diagnosis, FrozenDict, HypothesisId
from sciagent.eval.agency import (
    PROPOSAL_OUTCOMES,
    AgencyMetrics,
    ProposalRecord,
    agency_metrics,
    agency_report,
)
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.hybrid import Hybrid, ProposalAttempt
from sciagent.systems.llm import (
    RECORD,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
)

#: The same script ``tests/test_hybrid.py`` gives V7, so the run measured here is
#: the run that suite already characterises.
SCRIPT: tuple[Mapping[str, Any], ...] = (
    fixed_payload(3, (20, 30, 40), name="size_mixture"),
    fixed_payload(1, (10, 20, 30, 40), name="latent_regime"),
)

#: S11's mechanism is outside the agent grammar, so Stage A fires and V7 is asked
#: for a proposal. A scenario whose check passes is the other half of the pair --
#: see :func:`_quiet_run`, where the layer exists and is never consulted.
#:
#: S12 until 2026-08-16. Its truth is *in* the closed set and the old gate opened
#: there only because the censoring nuisance fooled a check with no reading
#: bearing on adequacy; once Stage A reads its own probe, S12 falls quiet and S11
#: fires. The constant exists so this file names the extending scenario once --
#: which is what made the move a one-line change. See ``docs/DECISIONS.md``.
EXTENDING_SCENARIO = "S11"


class _BrokenRecordSystem:
    """A system that advertises a proposal record and cannot produce one.

    Not a system anyone would write. It exists because :class:`Proposing` is a
    data protocol, so ``isinstance`` establishes that ``attempts`` exists and
    nothing about what it holds, and the harness has to refuse this rather than
    read it as "no proposal layer".
    """

    @property
    def name(self) -> str:
        return "broken"

    @property
    def attempts(self) -> tuple[ProposalAttempt, ...] | None:
        """Typed honestly, so the fault is in the value and not in a suppression.

        ``Proposing`` is ``runtime_checkable``, so ``isinstance`` passes on the
        attribute's presence however this is annotated -- which is exactly the
        hole being tested.
        """
        return None

    def investigate(self, investigation: Investigation) -> Diagnosis:
        entertain(investigation, closed_set())
        return investigation.conclude()


def _v7_run(
    script: Sequence[Mapping[str, Any]] = SCRIPT,
    *,
    scenario_id: str = EXTENDING_SCENARIO,
) -> ScenarioRun:
    """Return one real V7 run.

    Grows and saves the shared gate table exactly as item 12's and item 13's
    suites do: a structure V7 proposes has no row in the calibrated table, and
    filling one costs 2000 replicates, so it is simulated once per machine
    rather than once per module.
    """
    the_scenario = scenario(scenario_id)
    table = gate_table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    layer = ProposalLayer(
        ScriptedProvider(list(script)), AGENT_GRAMMAR, TranscriptStore(mode=RECORD)
    )
    run = run_scenario(
        the_scenario,
        Hybrid(closed_set(), layer),
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=engine,
        graph=graph,
    )
    save_gate_table(engine.table)
    return run


@lru_cache(maxsize=1)
def _extending_run() -> ScenarioRun:
    """Run V7 once on S11. Cached; every proposal-record test reads it."""
    return _v7_run()


@lru_cache(maxsize=1)
def _quiet_run() -> ScenarioRun:
    """Run V7 on a scenario whose check never fires, so its layer is never asked.

    S1's truth is in the library, so ``Hybrid.investigate`` never opens SPEC F6's
    gate and V7 *is* V1 -- see ``tests/test_hybrid.py``. The run exists here to
    hold the one distinction an empty attempt tuple cannot carry on its own.
    """
    return _v7_run(scenario_id="S1")


@lru_cache(maxsize=1)
def _slice_metrics() -> tuple[AgencyMetrics, ...]:
    """Return metrics for every conventional slice run, plus V7's."""
    conventional = tuple(agency_metrics(run) for run, _ in baseline_runs())
    return (*conventional, agency_metrics(_extending_run()))


def _for(system: str) -> tuple[AgencyMetrics, ...]:
    """Return every slice metric belonging to one SPEC §5 system."""
    return tuple(item for item in _slice_metrics() if item.system == system)


def _run_for(system: str) -> ScenarioRun:
    """Return one conventional slice run belonging to a SPEC §5 system."""
    for run, _ in baseline_runs():
        if run.system == system:
            return run
    raise AssertionError(f"no slice run for system {system!r}")


class TestTheTierBoundary:
    """Tier 1 is an experiment; tier 2 is a hypothesis introduced late."""

    def test_a_system_that_proposes_nothing_is_fully_autonomous(self) -> None:
        """B1 introduces no structure at all, so nothing of its run escalated."""
        metrics = _for("B1")
        assert metrics, "no B1 run reached the metric"
        for item in metrics:
            assert item.entertained == 0
            assert item.escalated == 0
            assert item.autonomy_fraction == 1.0

    def test_the_opening_library_is_not_an_escalation(self) -> None:
        """V1 entertains four structures and still escalates nothing.

        The case the metric has to get right. ``ScenarioRun.proposed`` is
        non-empty for V1 -- ``entertain`` routes every library structure through
        ``Investigation.propose`` -- so a metric counting proposals would report
        V1 as less autonomous than B1 for doing the one thing V1 is defined to
        do.
        """
        metrics = _for("V1")
        assert metrics, "no V1 run reached the metric"
        for item in metrics:
            assert item.entertained > 0
            assert item.escalated == 0
            assert item.autonomy_fraction == 1.0

    def test_structure_introduced_after_evidence_escalates(self) -> None:
        """B5 searches mid-run, so its late structures are tier 2."""
        metrics = _for("B5")
        assert metrics, "no B5 run reached the metric"
        assert any(item.escalated > 0 for item in metrics), (
            "no B5 run introduced a structure after evidence; the tier-2 side of "
            "the boundary is untested"
        )
        for item in metrics:
            if item.escalated > 0:
                assert item.autonomy_fraction is not None
                assert item.autonomy_fraction < 1.0

    def test_the_fraction_is_experiments_over_decisions(self) -> None:
        """Stated as arithmetic, so the definition is asserted and not implied."""
        for item in _slice_metrics():
            assert item.decisions == item.experiments + item.escalated
            if item.decisions:
                assert item.autonomy_fraction == item.experiments / item.decisions

    def test_a_run_that_decided_nothing_has_no_fraction(self) -> None:
        """``None``, not 1.0.

        Constructed rather than run: no slice scenario has a budget of zero. A
        run that took no decision escalated nothing, but reporting that as full
        autonomy would credit a system that did nothing, and a 1.0 would then
        pool into an aggregate as though it were evidence of autonomy.
        """
        idle = replace(_run_for("B1"), experiments=0)
        assert agency_metrics(idle).decisions == 0
        assert agency_metrics(idle).autonomy_fraction is None


class TestTheProposalRecord:
    """What the model was asked, and what became of it."""

    def test_a_system_with_no_proposal_layer_has_no_record(self) -> None:
        """``None`` means no layer, which is not the same as asked zero times.

        B4 and B5 introduce structure without ever consulting a model, so a
        record reading ``requested=0`` beside a positive escalation count would
        be a contradiction on the face of the report.
        """
        for name in ("V1", "B1", "B4", "B5"):
            for item in _for(name):
                assert item.proposals is None

    def test_a_layer_that_was_never_asked_still_has_a_record(self) -> None:
        """Zero requests, which is not the same fact as holding no layer.

        The distinction an empty attempt tuple cannot carry on its own: V7 on S1
        never opens SPEC F6's gate, so it asks nothing, and a metric reporting
        that as ``None`` would make it indistinguishable from B4 -- a system with
        no model to ask at all. Run end to end rather than constructed, because
        the part that has to get this right is the harness's capture, not the
        metric's arithmetic.
        """
        item = agency_metrics(_quiet_run())
        assert item.proposals is not None, (
            "V7's run reported no proposal layer; a layer that was never "
            "consulted is not the same as a system that holds none"
        )
        assert item.proposals.requested == 0
        assert item.proposals.yield_fraction is None
        assert item.escalated == 0
        assert item.autonomy_fraction == 1.0

    def test_v7_reports_what_it_asked_and_what_became_of_it(self) -> None:
        item = agency_metrics(_extending_run())
        record = item.proposals
        assert record is not None, "V7's run carried no proposal record"
        assert record.requested > 0
        assert record.requested == sum(
            getattr(record, outcome) for outcome in PROPOSAL_OUTCOMES
        )

    def test_an_admitted_proposal_is_an_escalation(self) -> None:
        """The record and the graph agree on what entered the hypothesis space."""
        item = agency_metrics(_extending_run())
        assert item.proposals is not None
        assert item.proposals.admitted > 0, (
            "V7 admitted nothing on the scenario whose check fires; the "
            "record-versus-graph agreement is untested"
        )
        assert item.escalated >= item.proposals.admitted

    def test_a_refusal_is_counted_and_admits_nothing(self) -> None:
        """Constructed: this is about the record's arithmetic, not about V7.

        A refused attempt names no hypothesis, so it must not reach the
        escalation count while still being visible as a request that was made.
        """
        run = _extending_run()
        refused = replace(
            run,
            attempts=(ProposalAttempt(None, None, "refused", "provider declined"),),
            proposed=FrozenDict[HypothesisId, Defect]({}),
        )
        record = agency_metrics(refused).proposals
        assert record is not None
        assert record.requested == 1
        assert record.refused == 1
        assert record.admitted == 0
        assert agency_metrics(refused).escalated == 0

    def test_a_record_claiming_more_than_the_graph_received_is_refused(self) -> None:
        """A self-reported admission that never entered the graph is a fault.

        The one place the metric can catch a system whose own account of what it
        did disagrees with what the framework recorded, so it raises rather than
        reporting the system's number.
        """
        run = _extending_run()
        assert run.attempts is not None, "V7's run carried no proposal record"
        overclaiming = replace(
            run,
            attempts=(
                *run.attempts,
                ProposalAttempt("addr", None, "admitted", "never proposed"),
            ),
        )
        with pytest.raises(InvestigationError, match="admitted"):
            agency_metrics(overclaiming)

    def test_the_outcome_list_cannot_drift_from_the_record(self) -> None:
        """Every listed outcome has a field to hold it.

        ``proposal_record`` accepts an attempt whose outcome is in
        ``PROPOSAL_OUTCOMES`` and then builds a record field by field, so an
        outcome on the list with no field behind it would be counted, accepted
        and dropped -- understating ``requested`` and inflating the yield. The
        constant is derived from the record's fields to make that
        unrepresentable; this asserts the derivation still holds.
        """
        record = ProposalRecord(
            admitted=1, duplicate=2, refused=3, malformed=4, unmeasurable=5
        )
        assert set(PROPOSAL_OUTCOMES) == {
            field.name for field in fields(ProposalRecord)
        }
        assert record.requested == sum(
            getattr(record, outcome) for outcome in PROPOSAL_OUTCOMES
        )

    def test_a_system_advertising_an_unreadable_record_is_refused(self) -> None:
        """``isinstance`` against a data protocol proves presence, not payload.

        A system exposing ``attempts = None`` satisfies :class:`Proposing` and
        would otherwise be filed as holding no proposal layer -- the exact
        distinction the optional type exists to preserve, lost silently.
        """
        the_scenario = scenario("S1")
        table = gate_table()
        graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
        with pytest.raises(InvestigationError, match="attempts"):
            run_scenario(
                the_scenario,
                _BrokenRecordSystem(),
                executor=executor(
                    GRAMMAR,
                    store=ExperimentStore.in_memory(),
                    budget=the_scenario.budget,
                ),
                engine=EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR)),
                graph=graph,
            )

    def test_an_unknown_outcome_is_refused(self) -> None:
        """Dropping it would silently understate what was requested."""
        run = _extending_run()
        bogus = replace(
            run, attempts=(ProposalAttempt(None, None, "considered", "not an outcome"),)
        )
        with pytest.raises(InvestigationError, match="considered"):
            agency_metrics(bogus)


class TestOverSliceRuns:
    """Item 14's gate: the metrics are computed over real slice runs."""

    def test_every_investigation_reports_a_fraction(self) -> None:
        """SPEC §12 criterion 11, over every run the slice affords."""
        metrics = _slice_metrics()
        assert len(metrics) > 12, "the slice runs did not reach the metric"
        for item in metrics:
            assert item.decisions > 0, f"{item.system} on {item.scenario} idled"
            assert item.autonomy_fraction is not None
            assert 0.0 <= item.autonomy_fraction <= 1.0

    def test_the_report_pools_by_system(self) -> None:
        """One row per system, totals summed, fraction over the pooled decisions.

        Pooled rather than a mean of per-run fractions: a run that spent thirty
        experiments and one that spent two say different amounts about how much
        of an investigation a system drove, and averaging fractions would weigh
        them equally.
        """
        report = agency_report(_slice_metrics())
        assert {row.system for row in report} == {"V1", "B1", "B4", "B5", "V7"}
        for row in report:
            own = _for(row.system)
            assert row.runs == len(own)
            assert row.experiments == sum(item.experiments for item in own)
            assert row.escalated == sum(item.escalated for item in own)
            assert row.autonomy_fraction == row.experiments / row.decisions

    def test_the_report_is_ordered_stably(self) -> None:
        """Invariant 3: a report built from a different run order is the same."""
        forward = agency_report(_slice_metrics())
        backward = agency_report(tuple(reversed(_slice_metrics())))
        assert forward == backward
        assert [row.system for row in forward] == sorted(row.system for row in forward)

    def test_only_v7_carries_a_proposal_record(self) -> None:
        """The slice's one system with a proposal layer is the one that has one."""
        with_record = {
            item.system for item in _slice_metrics() if item.proposals is not None
        }
        assert with_record == {"V7"}
