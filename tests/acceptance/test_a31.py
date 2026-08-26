"""Acceptance test A31: the payload carries agency, both flags and the masses.

A31 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The ledger payload omits what three §12 criteria
read"*, and reads:

    ``test_a31_the_payload_carries_agency_and_masses`` -- a constructed run's
    payload carries autonomy fraction, both detection flags and both masses;
    render shows the autonomy fraction beside every dimension block.

The same entry's **Idea** is what the gate is a check on: *"Extend
``CellReading.as_payload`` with the fields §12 reads and the run already
computes or could: autonomy fraction (criterion 11 / F10), the Stage-A gate flag
beside the whole-record PPC (criterion 4), and ``null_mass``/``abstain_mass``
(criterion 9). Metric-version bump; old rows stay."*

What was wrong
--------------

Verified over the recorded campaign: its 1,120 rows carry sixteen payload fields
and not one of them is an agency figure or a mass decomposition. The live
payload holds eighteen -- gate A29 added ``probe_p_value`` and
``probe_inadequate`` after that campaign ran, which is why the two counts differ
and why no recorded row can answer a question about the probe either. Three §12
criteria read fields that reach the ledger under neither count, and each fails
differently.

**Criterion 11** -- *"Autonomy fraction reported for every investigation"* --
has complete machinery in :mod:`sciagent.eval.agency` and, before this gate,
**zero production callers**: every call to
:func:`~sciagent.eval.agency.agency_metrics` in the repository was in
``tests/test_agency.py``. F10 asks for the fraction *"alongside every
performance figure"*, and the performance figures live in the ledger payload.

**Criterion 9** -- *"Correct abstention on S9 and S10: null and abstain mass
exceeding any single defect's mass"* -- names three quantities and the payload
held none of them. ``leading_mass`` is not the third: it is the largest mass
over **all** hypotheses, the null included, so where the null leads it *is*
``null_mass`` and the largest defect's mass is unrecoverable. That is precisely
S9 and S10, the two scenarios the criterion is about, so the comparison was
undecidable in exactly the case it exists for. Hence ``max_defect_mass``, which
the gate line does not name and this module tests anyway: shipping the two
masses the **Idea** names without it would leave criterion 9 as undecidable as
before and cost a second reading bump to fix.

**Criterion 4** is the one already closed. Gate A29 put ``probe_inadequate``
beside ``inadequate`` and bound each to the attribute it is named for; the
clause here is a regression pin on that binding, not new work.

Where the numbers come from, and why that is the whole of it
-----------------------------------------------------------

Every field this gate adds is derived inside
:func:`~sciagent.eval.matrix.reading_of`, from the
:class:`~sciagent.eval.campaign.ScenarioRun` it already receives -- which
``run_scenario`` has reconciled against the engine's own state before it is
returned. No new seam is opened and nothing a system authored is read: the
agency figures come off :func:`~sciagent.eval.agency.agency_metrics`, which
reads the graph's ``proposed_at`` rather than any system's account of itself,
and the masses come off the :class:`~sciagent.core.types.Diagnosis` that
:func:`~sciagent.systems.base.diagnose` derived from the posterior. The run died
inside ``MatrixRunner.execute`` before this gate; what changes is that it is
read on the way past.

What the tests establish
------------------------

``test_a31_the_payload_carries_agency_and_masses`` is the gate's two clauses.
Its first half asserts each payload entry **equals the attribute it claims to
be**, and that is A29's lesson taken rather than restated: a test asserting only
that the keys are present, or that two of them differ from each other, is
invariant under swapping the labels, and a transposition of exactly that kind
passed the whole suite once already. Presence is not the check that matters; the
binding is.

``test_a31_the_masses_decide_criterion_9`` is what stops ``max_defect_mass``
being redundant with ``leading_mass``. On S9 the null leads, so the two part
company by construction, and the criterion's comparison becomes computable from
the payload alone. It asserts the comparison is *decidable*, never that it comes
out in the system's favour -- criterion 9 is a bar an arm may fail, and a gate
that asserted the outcome would be grading a system rather than the report.

``test_a31_the_autonomy_fraction_discriminates_between_arms`` is what stops the
first clause being satisfied by a constant. A payload writing ``1.0``
unconditionally passes every presence check and every equality against an
``agency_metrics`` that is itself never called; an arm that escalates has to
come out lower than one that does not.

``test_a31_a_run_that_decided_nothing_reports_no_fraction`` is the boundary the
optional return type exists for.
:attr:`~sciagent.eval.agency.AgencyMetrics.autonomy_fraction` is ``None`` for a
run that took no decision, because *"reporting 1.0 there would credit a system
that did nothing with full autonomy, and would pool into an aggregate as though
it were evidence"*. A ledger payload is a mapping of floats and has no ``None``,
so the whole of that reasoning is spent unless the crossing preserves it:
``nan``, which :func:`~sciagent.eval.report._summarise` excludes and counts,
exactly as it already does for a D2 on a scenario with no battery. ``0.0`` would
read as a system that decided nothing unilaterally and ``1.0`` as one that
decided everything, and both are claims about a run that decided nothing at all.

``test_a31_a_row_from_the_previous_reading_is_not_pooled_beside_these`` is the
**Idea**'s last four words. The payload gained fields, so a row recorded under
``spec8/3`` cannot answer a question about agency or masses, and a reader
folding the two generations together would average whichever rows happened to
carry the key. ``DIMENSION_VERSION`` is the term that moves -- not
``METRIC_VERSION``, which addresses every cached empirical table and would force
a rebuild for a change touching no estimator.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, replace
from functools import lru_cache

import pytest
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
from sciagent.core.errors import MalformedDesignError
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    DataVersion,
    Diagnosis,
    EnvVersion,
    FrozenDict,
    GrammarVersion,
    HypothesisId,
    MetricVersion,
    ScenarioId,
    Seed,
)
from sciagent.eval.agency import AgencyMetrics, agency_metrics
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    cell_key,
    reading_of,
)
from sciagent.eval.report import render, summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import DIMENSION_VERSION
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly

#: The three arms every scenario here is run with. B1 and V1 are SPEC §5's, so
#: the claims below are made about systems the matrix actually contains.
#: ``Escalating`` is here because neither of those ever introduces a structure
#: after evidence, and an autonomy fraction is 1.0 for every arm that does not.
ARMS = ("B1", "V1", "escalating")

#: The two scenarios. S9 is the abstention scenario, where the null leads and
#: ``max_defect_mass`` therefore parts company with ``leading_mass``; S11 is
#: where the space is inadequate, so the two detection flags are read somewhere
#: they have something to say.
SCENARIOS = ("S9", "S11")

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

PLATFORM = "Windows-11-x86_64"
NUMPY = "2.5.1"
REPORT_GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")


class Escalating:
    """A system that opens on part of its library and adds the rest late.

    Neither V1 nor B1, and here for one reason: both of those report an autonomy
    fraction of exactly 1.0 on every scenario, because neither ever introduces a
    structure after evidence is in hand. A gate whose every arm scores 1.0 is
    satisfied by a payload writing the constant, which is the shape this arm
    exists to refuse.

    The structure it holds back is a **library** one, so nothing here authors a
    defect the environment could not measure: what makes it tier 2 is *when* it
    is proposed, which is what ``proposed_at`` records and what
    :func:`~sciagent.eval.agency.agency_metrics` reads.
    """

    def __init__(self) -> None:
        library = closed_set()
        # Selected by *carrying an edit*, not by sort position. `closed_set()`
        # holds ``"null": frozenset()`` beside the four mechanisms, and the null
        # is already in every graph `null_seeded_graph` builds -- so a positional
        # pick is one rename away from proposing the null a second time, and
        # `propose` is reached here directly rather than through `entertain`,
        # whose `find_duplicate` guard is what would otherwise absorb that. It
        # would surface as a `DuplicateHypothesisError` (gate A18) thrown out of
        # a fixture, failing every test in this module on an error about
        # duplicates rather than about agency. The predicate is also what this
        # arm *means*: escalating is introducing a real structure late, and the
        # empty edit is not one.
        self._late = next(name for name in sorted(library) if library[name])
        self._opening = {
            name: library[name] for name in sorted(library) if name != self._late
        }
        self._library = library

    @property
    def name(self) -> str:
        """Return this arm's identifier."""
        return "escalating"

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Open on all but one structure, spend a design, then add the last."""
        entertain(investigation, self._opening)
        designs = investigation.designs
        if investigation.affords():
            investigation.run(designs[0])
        investigation.propose(
            HypothesisId(self._late),
            program_edit=self._library[self._late],
            rationale=f"library structure {self._late!r}, introduced late",
        )
        step = 1
        while investigation.affords():
            investigation.run(designs[step % len(designs)])
            step += 1
        return investigation.conclude()


@dataclass(frozen=True, slots=True)
class Recorded:
    """One arm's run on one scenario, and the reading it was scored to.

    ``program`` is the executor's reference programme, held because gate A30
    made it an argument of :func:`~sciagent.eval.matrix.reading_of`: the
    adjudication pass reads SPEC §7.2's causal licence against it, and a
    ``ScenarioRun`` does not carry one.
    """

    run: ScenarioRun
    reading: CellReading
    program: GenerativeProgram


@lru_cache(maxsize=1)
def _runs() -> dict[tuple[str, str], Recorded]:
    """Return every run this gate reads, keyed by ``(scenario, arm)``.

    Built once and cached, with the empirical table threaded from run to run and
    saved at the end, exactly as ``tests/acceptance/test_a29.py`` does it: a
    structure entertained once is simulated once per machine rather than once
    per test.
    """
    table: EmpiricalTable = gate_table()
    simulate = simulator(GRAMMAR)
    built: dict[tuple[str, str], Recorded] = {}
    for name in SCENARIOS:
        target = scenario(name)
        for arm in ARMS:
            graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
            engine = EmpiricalTableEngine(graph, table, simulate=simulate)
            runner = executor(
                GRAMMAR,
                store=ExperimentStore.in_memory(),
                budget=target.budget,
            )
            run = run_scenario(
                target,
                _system(arm),
                executor=runner,
                engine=engine,
                graph=graph,
            )
            reading, table = reading_of(
                run,
                grammar=GRAMMAR,
                table=engine.table,
                simulate=simulate,
                observations=engine.observations,
                program=runner.reference,
            )
            built[(name, arm)] = Recorded(
                run=run, reading=reading, program=runner.reference
            )
    save_gate_table(table)
    return built


def _system(arm: str) -> PPCOnly | BOEDOnly | Escalating:
    """Return a fresh system for one arm. Fresh because ``Escalating`` holds state."""
    if arm == "B1":
        return PPCOnly()
    if arm == "V1":
        return BOEDOnly(closed_set())
    return Escalating()


def _run(name: str, arm: str) -> ScenarioRun:
    """Return one recorded run."""
    return _runs()[(name, arm)].run


def _payload(name: str, arm: str) -> dict[str, float]:
    """Return the ledger payload one recorded run was scored to."""
    return dict(_runs()[(name, arm)].reading.as_payload())


def _agency(name: str, arm: str) -> AgencyMetrics:
    """Return the agency the framework derives for one recorded run.

    Called here from the *test*, and the gate's point is that
    :func:`~sciagent.eval.matrix.reading_of` now calls it too. Before this gate
    every caller in the repository was a test, which is what made criterion 11
    unmet for the recorded campaign.
    """
    return agency_metrics(_run(name, arm))


def _largest_defect_mass(run: ScenarioRun) -> float:
    """Return the largest posterior mass on a hypothesis holding a program edit.

    Criterion 9's third quantity, computed here from the graph and the posterior
    the way the payload field under test must be. Deliberately **not** read off
    the payload it is checking, and deliberately not ``leading_mass``: the two
    coincide only when the leader carries an edit, and S9 is where they do not.
    """
    masses = [
        float(run.diagnosis.distribution[node_id])
        for node_id in sorted(run.diagnosis.distribution)
        if run.graph.node(node_id).program_edit
    ]
    return max(masses) if masses else 0.0


def _scenario_class(target: ScenarioId) -> ScenarioClass:
    """Classify off the environment's declaration, as the real caller does."""
    return scenario(str(target)).scenario_class


def _row(name: str, arm: str, *, replicate: int = 0) -> LedgerEntry:
    """Return one recorded row holding a real run's payload."""
    task = CellTask(
        cell=Cell(arm, ScenarioId(name), 1), replicate=replicate, seed=Seed(replicate)
    )
    return LedgerEntry(
        key=cell_key(task, ADDRESS, battery=scenario(name).held_out),
        reading=FrozenDict[str, float](_payload(name, arm)),
        sequence=replicate,
    )


def _report_of(rows: tuple[LedgerEntry, ...]) -> str:
    """Return the rendered report for ``rows``, at this module's address."""
    return render(
        summarise(
            rows,
            address=ADDRESS,
            scenario_class=_scenario_class,
            battery=lambda target: scenario(str(target)).held_out,
            platform=PLATFORM,
            numpy_version=NUMPY,
            grammar=REPORT_GRAMMAR,
        )
    )


def _block(text: str, name: str, arm: str) -> list[str]:
    """Return the lines of one cell's dimension block, heading to blank line.

    The gate says the fraction is shown *beside every dimension block*, which is
    a claim about **where** the line is and not about how many such lines the
    report holds. Locating the block is the whole of the difference: a render
    appending a trailing agency section after all six blocks emits exactly as
    many lines beginning ``autonomy`` as there are cells, carrying exactly the
    right values, and satisfies none of the clause. That implementation is the
    cheaper one -- a footer needs no :class:`~sciagent.eval.report.CellSummary`
    field -- so it is the one a cardinality check would have licensed.

    A block runs from its heading, which
    :func:`~sciagent.eval.report._cell_block` writes as ``system / scenario``,
    to the blank line that same function ends it with.
    """
    lines = text.splitlines()
    heading = f"{arm} / {name}  ("
    start = next(index for index, line in enumerate(lines) if line.startswith(heading))
    end = next(
        index for index in range(start + 1, len(lines)) if not lines[index].strip()
    )
    return lines[start:end]


class TestA31ThePayloadCarriesAgencyAndMasses:
    """A criterion read off the report needs its observable in the payload."""

    def test_a31_the_payload_carries_agency_and_masses(self) -> None:
        """The gate: every named field present, bound to its source, and rendered.

        Each assertion compares a payload entry against the attribute it claims
        to be, rather than against another payload entry. Gate A29 records why:
        a transposition of two payload labels passed all four of its tests and
        the whole suite, because the tests compared the two keys to each other
        and never to the field each is named for.
        """
        for name in SCENARIOS:
            for arm in ARMS:
                payload = _payload(name, arm)
                run = _run(name, arm)
                agency = _agency(name, arm)

                # Criterion 11 / F10. `None` is the run that decided nothing and
                # has its own test; here every arm decides something.
                assert agency.autonomy_fraction is not None
                assert payload["autonomy_fraction"] == agency.autonomy_fraction
                # F10's two tiers, beside the fraction they are taken over.
                # `experiments` is tier 1 and was already recorded, so the
                # denominator is recoverable from the payload alone.
                assert payload["escalated"] == float(agency.escalated)
                assert payload["entertained"] == float(agency.entertained)
                assert payload["experiments"] == float(agency.experiments)

                # Criterion 4's two flags, each bound to its own source. A29's
                # clause, pinned again because this gate's entry names it.
                assert run.probe is not None
                assert payload["inadequate"] == float(run.ppc.inadequate)
                assert payload["probe_inadequate"] == float(run.probe.inadequate)

                # Criterion 9's three masses.
                assert payload["null_mass"] == float(run.diagnosis.null_mass)
                assert payload["abstain_mass"] == float(run.diagnosis.abstain_mass)
                assert payload["max_defect_mass"] == _largest_defect_mass(run)

        # The second clause, asserted **inside each block** rather than over the
        # whole rendered string. One row per (scenario, arm), so the report holds
        # six cells, and each has to carry its own figure where a reader of that
        # cell will meet it.
        rows = tuple(_row(name, arm) for name in SCENARIOS for arm in ARMS)
        text = _report_of(rows)
        for name in SCENARIOS:
            for arm in ARMS:
                fraction = _agency(name, arm).autonomy_fraction
                assert fraction is not None
                block = _block(text, name, arm)
                printed = [
                    line for line in block if line.strip().startswith("autonomy")
                ]
                assert len(printed) == 1, (
                    f"{arm} on {name} carries {len(printed)} autonomy line(s) in "
                    f"its dimension block; the fraction is reported per cell"
                )
                assert f"{fraction:>10.4f}" in printed[0], (
                    f"the autonomy fraction in {arm}/{name}'s block is not "
                    f"{fraction!r}: {printed[0]!r}"
                )

        # Nowhere else, which is what makes the assertions above about *location*
        # rather than about a count. Six blocks each holding one line, and six
        # such lines in the whole report, together say the figure is not also
        # accumulated into a trailing section -- and it is that section, needing
        # no `CellSummary` field, that a cardinality check on its own licenses.
        everywhere = [
            line for line in text.splitlines() if line.strip().startswith("autonomy")
        ]
        assert len(everywhere) == len(rows)

    def test_a31_the_masses_decide_criterion_9(self) -> None:
        """S9's comparison is computable from the payload, and needs all three.

        Criterion 9 compares null and abstain mass against *any single defect's*
        mass. ``leading_mass`` is the largest over every hypothesis including the
        null, so on an abstention scenario -- where the null leads, which is the
        whole of what S9 tests -- it says nothing about the largest defect. The
        assertion below is that the two part company on a real run: without
        that, ``max_defect_mass`` is a redundant key and criterion 9 would be
        decidable without this gate.

        What is asserted is that the comparison is **decidable**, never how it
        comes out. Criterion 9 is a bar an arm may fail, and a gate asserting
        the verdict would be grading a system.
        """
        led_by_null = 0
        for arm in ARMS:
            payload = _payload("S9", arm)

            # Every term of the criterion is in the payload, so the comparison
            # it asks for is arithmetic over three floats from one row. Not
            # asserted as a *verdict*: criterion 9 is a bar an arm may fail, and
            # a gate asserting the outcome would be grading a system. What is
            # asserted is that the three terms are real numbers, so the
            # comparison has an answer at all -- a `nan` reaching any of them
            # would make it silently False whichever way the masses lay.
            for term in ("null_mass", "abstain_mass", "max_defect_mass"):
                assert math.isfinite(payload[term]), (
                    f"{arm} on S9 reports a non-finite {term}, so criterion 9's "
                    f"comparison has no answer rather than a negative one"
                )

            assert payload["max_defect_mass"] <= payload["leading_mass"]
            if math.isclose(payload["null_mass"], payload["leading_mass"]):
                led_by_null += 1
                assert payload["max_defect_mass"] < payload["leading_mass"], (
                    f"{arm} on S9 is led by the null, so the largest defect's "
                    f"mass is strictly below the leader's and a payload "
                    f"reporting them equal has read the wrong hypotheses"
                )

        assert led_by_null, (
            "no arm on S9 was led by the null, so this test never reached the "
            "case where leading_mass and max_defect_mass differ -- the field "
            "under test is unexercised and criterion 9 is untested where it is "
            "read"
        )

    def test_a31_the_autonomy_fraction_discriminates_between_arms(self) -> None:
        """An arm that escalated reports below 1.0; the two that did not report 1.0.

        What stops the gate's first clause being satisfied by a payload writing
        a constant. B1 introduces no structure and V1 introduces its whole
        library *before* any evidence, so both are fully autonomous by F10's
        reading -- and a fraction that were merely always 1.0 would be no
        measurement at all.
        """
        for name in SCENARIOS:
            for quiet in ("B1", "V1"):
                assert _agency(name, quiet).escalated == 0
                assert _payload(name, quiet)["autonomy_fraction"] == 1.0

            assert _agency(name, "escalating").escalated > 0
            assert _payload(name, "escalating")["autonomy_fraction"] < 1.0
            assert _payload(name, "escalating")["escalated"] > 0.0

    def test_a31_a_run_that_decided_nothing_reports_no_fraction(self) -> None:
        """``nan`` at the ledger boundary, not 0.0 and not 1.0.

        Constructed rather than run, exactly as ``tests/test_agency.py`` does it:
        no slice scenario has a budget of zero.
        :attr:`~sciagent.eval.agency.AgencyMetrics.autonomy_fraction` is ``None``
        here and a payload holds floats, so the crossing is where that reasoning
        is either preserved or silently spent.

        The second half is why ``nan`` rather than either number a reader might
        expect: the report already excludes non-finite values from a mean and
        counts them, so a run that decided nothing stays out of the aggregate
        and says so on the rendered line.
        """
        idle = replace(_run("S9", "B1"), experiments=0)
        assert agency_metrics(idle).decisions == 0
        assert agency_metrics(idle).autonomy_fraction is None

        reading, _ = reading_of(
            idle,
            grammar=GRAMMAR,
            table=gate_table(),
            simulate=simulator(GRAMMAR),
            observations=(),
            program=_runs()[("S9", "B1")].program,
        )
        payload = dict(reading.as_payload())
        assert math.isnan(payload["autonomy_fraction"])

        row = LedgerEntry(
            key=cell_key(
                CellTask(
                    cell=Cell("B1", ScenarioId("S9"), 1), replicate=0, seed=Seed(0)
                ),
                ADDRESS,
                battery=scenario("S9").held_out,
            ),
            reading=FrozenDict[str, float](payload),
            sequence=0,
        )
        block = _block(_report_of((row,)), "S9", "B1")
        line = next(line for line in block if line.strip().startswith("autonomy"))
        assert "n=0" in line
        assert "1 non-finite" in line

    def test_a31_a_row_from_the_previous_reading_is_not_pooled_beside_these(
        self,
    ) -> None:
        """The reading bumps, and a row scored under the old one is excluded.

        The **Idea**'s *"Metric-version bump; old rows stay"*. ``spec8/3`` rows
        carry no agency figure and no mass decomposition, so summarising them
        beside these would average a field over whichever rows happened to hold
        it. ``DIMENSION_VERSION`` is the term that moves and ``METRIC_VERSION``
        is not: the metric version addresses every cached empirical table, and
        this change touches no estimator.
        """
        # The literal has moved twice since: to `spec8/5` at gate A30, and to
        # `spec8/6` at gate A42, each the next scoring change of exactly the kind
        # this assertion was written to notice -- the payload gained the
        # adjudication and contradiction fields SPEC 12 criteria 8 and 10 read,
        # and then D4's comparison-set size. Updating it keeps the assertion's
        # purpose: what it pins is that the term moves when a reading changes and
        # only then, and a literal is what makes each bump arrive here as a
        # decision rather than as silence.
        assert DIMENSION_VERSION == "spec8/6"

        current = _row("S9", "B1")
        stale = dataclasses.replace(
            current,
            key=dataclasses.replace(
                current.key,
                config=type(current.key.config)(
                    {**dict(current.key.config), "dimensions": "spec8/3"}
                ),
            ),
        )
        with pytest.raises(MalformedDesignError, match="spec8/3"):
            _report_of((stale,))
