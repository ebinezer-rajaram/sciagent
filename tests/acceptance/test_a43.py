"""Acceptance test A43: a superseded battery is refused, not rendered.

A43 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The report layer checks the battery's presence, not
its value"*, and reads:

    ``test_a43_a_superseded_battery_is_refused_rather_than_rendered`` -- a
    ledger holding only rows scored under a battery the scenario no longer
    declares raises rather than reporting, and the message names both terms; a
    ledger at the declared battery reports unchanged.

What was wrong
--------------

:func:`~sciagent.eval.report._at_address` compares every address term it can
against a known value -- ``matrix``, ``dimensions``, the partition and the three
version columns -- except ``battery``, which it checks only for *presence*. It
cannot do better on its own: a battery is declared per scenario on an
environment's :class:`~sciagent.eval.scenarios.Scenario`, and the first
invariant forbids :mod:`sciagent` from importing one, so there is no module
constant to compare against.

:func:`~sciagent.eval.report._refuse_mixed_batteries` does not close the hole. It
asks whether the surviving rows *agree with each other*, which is a different
question, and it fires only when two batteries coexist for one scenario. A
ledger whose rows agree unanimously on a battery the scenario has since replaced
passes both checks and is rendered as though it were the current campaign. The
case is not exotic: any change to a battery's membership makes every row
recorded before it exactly this, and no version column moves to say so.

Gate A27 took the two halves of this that needed no new parameter --
:attr:`~sciagent.eval.report.CellSummary.battery` is rendered per cell, and the
"no row matches" diagnostic names a row excluded for its battery rather than
listing the terms it matched. Both make the failure *visible* to a reader
holding the declaration. Neither makes it *refuse*.

The reading this gate encodes
-----------------------------

:func:`~sciagent.eval.report.summarise` takes the battery declaration as a
**required** callback, the sibling of the ``scenario_class`` callback it already
takes and of the ``battery`` callback
:func:`~sciagent.eval.matrix.run_matrix` takes. Required rather than defaulted
for the reason :func:`~sciagent.eval.matrix.cell_key` gives for its own: a
parameter defaulting to today's behaviour reproduces the defect for every caller
who forgets it, which is the failure mode the parameter exists to remove.

**Refusing rather than selecting**, which is the design choice worth stating.
Excluding a superseded row the way a stale ``dimensions`` row is excluded would
be the smaller change, and it is wrong here: a caller can *ask* for a metric
version -- ``scripts/report_matrix.py`` takes one on the command line -- but
cannot ask for a battery, because the callback returns whatever the scenario
currently declares. Silent selection would therefore drop rows the operator has
no way to ask for back, and would leave them with a report whose replicate
counts had quietly fallen. A27's reasoning binds unchanged: under the fourth
invariant the recorded rows are legitimate and this module cannot pick.

Ordering is load-bearing, and two tests here are about nothing else. The refusal
runs *after* :func:`~sciagent.eval.report._refuse_mixed_batteries`, so a mixed
ledger still raises on A27's message rather than on this one, and *before*
:func:`~sciagent.eval.report._refuse_reseeded`, for the reason already written
there: a stale campaign whose replicate indices collide trips the seed check
first and is reported as a re-seed that never happened.
"""

from __future__ import annotations

import pytest

from environments.pointproc.outcomes import slice_designs
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
from sciagent.eval.report import summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.experiments.dsl import ExperimentDesign, is_intervention
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.partitions import DataPartition

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

PLATFORM = "Windows-11-x86_64"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")


def _scenario_class(target: ScenarioId) -> ScenarioClass:
    """Classify off the environment's declaration, as the real caller does."""
    return scenario(str(target)).scenario_class


def _superseded(battery: tuple[ExperimentDesign, ...]) -> tuple[ExperimentDesign, ...]:
    """Return ``battery`` with one observational member exchanged for another.

    Same cardinality, same number of interventions, different membership --
    ``tests/acceptance/test_a27.py`` builds its second battery the same way and
    for the same reason. A variant differing in *size* would be the weaker
    fixture here: the check under test would pass it even if it compared
    ``n_held_out`` rather than the term, and the count is the natural slip
    because it is how a ledger row already summarises a battery.
    """
    inside = next(design for design in battery if not is_intervention(design))
    outside = next(
        design
        for design in slice_designs()
        if design not in battery and not is_intervention(design)
    )
    return tuple(outside if design == inside else design for design in battery)


def _reading() -> CellReading:
    """Return a reading carrying stated numbers and nothing derived.

    Nothing in it is read by this gate, which is about a row's *address*. It
    exists because the ledger stores a flat payload and ``summarise`` folds
    every field of one, so a row cannot be built without a whole vector.
    """
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=0.0,
            d2_held_out_predictive=-1.0,
            d3_intervention_similarity=0.0,
            d4_explanatory_coverage=0.0,
            d5_enabled_experiment_value=0.0,
            d6_complexity=12.0,
            n_held_out=3,
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
        probe_inadequate=False,
        agency=AgencyMetrics(
            system="V7",
            scenario=ScenarioId("S11"),
            experiments=8,
            entertained=4,
            escalated=0,
            proposals=None,
            causes=None,
        ),
        # A30's counts. A fully adjudicated run with a clean record, which is
        # what every real one on the slice is: `verify` never refers a claim in
        # the recorded population, and no run rejects a hypothesis, so nothing
        # here can be a zombie. Fixed rather than derived because this builder
        # constructs a reading rather than scoring a run.
        adjudication=Adjudication(
            claims=80, adjudicated=80, contradictions=0, zombies=0
        ),
        null_mass=Probability(0.25),
        abstain_mass=Probability(0.5),
        max_defect_mass=0.4,
        experiments=8,
        structural_distance=1.0,
        battery=battery_key(()),
    )


def _row(
    name: str,
    battery: tuple[ExperimentDesign, ...],
    *,
    system: str = "V7",
    replicate: int = 0,
    seed: int = 0,
) -> LedgerEntry:
    """Return one recorded row of ``name``, addressed at ``battery``."""
    task = CellTask(
        cell=Cell(system, ScenarioId(name), 1), replicate=replicate, seed=Seed(seed)
    )
    return LedgerEntry(
        key=cell_key(task, ADDRESS, battery=battery),
        reading=FrozenDict[str, float](dict(_reading().as_payload())),
        sequence=replicate,
    )


class TestA43SupersededBattery:
    """A report may not render rows scored on a battery that has been replaced."""

    def test_a43_a_superseded_battery_is_refused_rather_than_rendered(self) -> None:
        """The gate: refuse, name both terms, and leave the current case alone.

        Three clauses, and the third is what stops the first being satisfied by
        a check that refuses everything. The *same rows* are accepted when the
        declaration matches them and refused when it does not, so what the test
        separates is the declaration and not the fixture.
        """
        declared = scenario("S11").held_out
        superseded = _superseded(declared)
        assert battery_key(declared) != battery_key(superseded)

        stale = (_row("S11", superseded),)
        with pytest.raises(MalformedDesignError, match="does not declare") as raised:
            summarise(
                stale,
                address=ADDRESS,
                scenario_class=_scenario_class,
                battery=lambda _target: declared,
                platform=PLATFORM,
                grammar=GRAMMAR,
            )
        # Both terms, so the reader can tell which generation they are holding.
        # Naming only one leaves them unable to say whether the ledger or the
        # declaration is the thing that moved.
        message = str(raised.value)
        assert battery_key(superseded) in message
        assert battery_key(declared) in message
        assert "S11" in message

        current = (_row("S11", declared),)
        report = summarise(
            current,
            address=ADDRESS,
            scenario_class=_scenario_class,
            battery=lambda _target: declared,
            platform=PLATFORM,
            grammar=GRAMMAR,
        )
        assert len(report.cells) == 1
        assert report.cells[0].battery == battery_key(declared)
        assert report.cells[0].replicates == 1

    def test_a43_a_mixed_ledger_still_raises_on_a27s_message(self) -> None:
        """The new check runs after ``_refuse_mixed_batteries``, not before it.

        A ledger holding both generations for one scenario is A27's case and
        keeps A27's diagnosis: two batteries coexist and the module cannot pick.
        Were this check to run first it would report the older rows as
        superseded and say nothing about the newer ones sitting beside them,
        which describes a ledger the operator does not have.

        Nothing else asserts the order.
        ``test_a27_two_batteries_are_not_pooled_into_one_cell`` passes either way
        as long as *something* raises with its message, and would go on passing
        if this check preempted it.
        """
        declared = scenario("S11").held_out
        rows = (
            _row("S11", declared, replicate=0, seed=0),
            _row("S11", _superseded(declared), replicate=1, seed=1),
        )
        with pytest.raises(MalformedDesignError, match="two held-out batteries"):
            summarise(
                rows,
                address=ADDRESS,
                scenario_class=_scenario_class,
                battery=lambda _target: declared,
                platform=PLATFORM,
                grammar=GRAMMAR,
            )

    def test_a43_the_refusal_precedes_the_reseed_check(self) -> None:
        """A stale campaign is not reported as a re-seed of the current one.

        The sibling of the ordering ``_refuse_mixed_batteries`` already has, and
        the same failure: two rows of one superseded campaign sharing a
        replicate index also match ``_refuse_reseeded``, whose message blames a
        seed set that was never changed and sends the reader to select one.
        """
        declared = scenario("S11").held_out
        superseded = _superseded(declared)
        rows = (
            _row("S11", superseded, replicate=0, seed=3),
            _row("S11", superseded, replicate=0, seed=4),
        )
        with pytest.raises(MalformedDesignError, match="does not declare") as raised:
            summarise(
                rows,
                address=ADDRESS,
                scenario_class=_scenario_class,
                battery=lambda _target: declared,
                platform=PLATFORM,
                grammar=GRAMMAR,
            )
        assert "two rows at this address" not in str(raised.value)

    def test_a43_each_scenario_is_checked_against_its_own_declaration(self) -> None:
        """One current scenario beside one superseded one still refuses.

        The unit is the **scenario**, not the ledger.
        ``_refuse_mixed_batteries`` is already per scenario -- two scenarios
        with different design spaces have different batteries by construction,
        and refusing that would refuse every well-formed multi-scenario report
        -- and this check composes with it only by using the same unit.

        Two implementations that satisfy every other test in this class fail
        here: one that refuses only when *every* row at the address is
        superseded, and one that checks a single scenario and stops. Both
        render a cell built entirely on a battery its scenario no longer
        declares, which is the defect verbatim, one scenario over. It is the
        ledger a battery change leaves behind when the cells are re-scored
        scenario by scenario: the state between the first and the last.

        Not the state A40's re-derivation leaves behind -- those two generations
        are separated by ``METRIC_VERSION``, which ``_at_address`` already
        selects on, so they never reach this check at all.

        Both orientations, because "check the first scenario and stop" is
        otherwise caught only by whichever order the fixture happens to use.
        """
        for current, stale in (("S9", "S11"), ("S11", "S9")):
            seen: list[str] = []

            def declaration(
                target: ScenarioId, *, log: list[str] = seen
            ) -> tuple[ExperimentDesign, ...]:
                log.append(str(target))
                return scenario(str(target)).held_out

            rows = (
                _row(current, scenario(current).held_out),
                _row(stale, _superseded(scenario(stale).held_out)),
            )
            with pytest.raises(
                MalformedDesignError, match="does not declare"
            ) as raised:
                summarise(
                    rows,
                    address=ADDRESS,
                    scenario_class=_scenario_class,
                    battery=declaration,
                    platform=PLATFORM,
                    grammar=GRAMMAR,
                )
            message = str(raised.value)
            assert stale in message
            assert battery_key(_superseded(scenario(stale).held_out)) in message
            # The scenario whose rows *are* current is not blamed for the one
            # that is not. Neither name can collide with a term: `battery_key`
            # renders a count and a hex digest, and "S" is not a hex digit.
            assert current not in message

            # Asked for *both* scenarios in this ledger, and asked within this
            # orientation -- accumulating across the two would be satisfied by a
            # callback consulted once per ledger, which is the same defect
            # wearing a callback. Exactly two calls, one per scenario: the loop
            # skips a scenario already recorded, so a callback asked per row
            # would show four here on a two-row ledger.
            assert sorted(seen) == sorted([current, stale])

    def test_a43_the_message_does_not_depend_on_the_order_rows_arrive_in(self) -> None:
        """``summarise`` promises order-independence, and this is output too.

        ``_refuse_reseeded`` sorts the two seeds it names for exactly this
        reason. With two scenarios both superseded, reporting whichever was
        encountered first would make the message a function of how the caller
        assembled the ledger.
        """
        rows = (
            _row("S9", _superseded(scenario("S9").held_out)),
            _row("S11", _superseded(scenario("S11").held_out)),
        )

        def raised_on(ledger: tuple[LedgerEntry, ...]) -> str:
            with pytest.raises(MalformedDesignError) as raised:
                summarise(
                    ledger,
                    address=ADDRESS,
                    scenario_class=_scenario_class,
                    battery=lambda target: scenario(str(target)).held_out,
                    platform=PLATFORM,
                    grammar=GRAMMAR,
                )
            return str(raised.value)

        assert raised_on(rows) == raised_on(tuple(reversed(rows)))
