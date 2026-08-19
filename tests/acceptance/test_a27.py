"""Acceptance test A27: every arm answers the same held-out questions.

A27 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"A preregistered held-out battery, so arms are scored
on one question set"*, and reads:

    ``test_a27_the_battery_is_a_function_of_the_scenario_alone`` -- two arms
    with different run histories on one scenario receive identical batteries;
    every scenario's battery contains an intervention; battery membership
    appears in the recorded address.

What was wrong
--------------

:func:`~sciagent.eval.matrix.held_out_battery` derived the battery from the
run's evidence index: every design the scenario offered that the investigation
did not use. So the battery was a function of *what the arm did*, and two arms
on one scenario were graded on different question sets. Measured over the
recorded matrix, ``n_held_out`` was {3,2} for the V-arms, {2} for B4/B5 and
**{0}** for B1 -- whose D3 was therefore ``nan`` on all twenty S11 rows. No
cross-arm D2/D3 comparison was clean, including SPEC §9's preregistered
contrast, and D3 -- which §8 calls the "held-out intervention battery" -- could
contain no intervention at all.

The reading this gate encodes
-----------------------------

The battery is **declared on the scenario** and is a subset of the designs the
scenario offers. It is *not* withheld from the offer: the slice has exactly one
intervention (``forced_design``), and SPEC §4.2 makes it the only design that
separates Hawkes self-excitation from latent regime switching, so reserving it
would break S5's intervention planning, the oracle policy lengths behind gate
A24, and S10's derived budget. ``tests/test_scoring.py``'s ``HELD_OUT`` -- which
``docs/BACKLOG.md`` names as the precedent -- is a named subset of
:func:`~environments.pointproc.outcomes.slice_designs` for the same reason.

The cost of that reading is stated rather than hidden: an arm that ran a battery
design is scored on a question it asked. That is a weaker guarantee than the
derivation gave, and a better instrument, because the derivation bought it by
making the question set depend on the arm. D2 and D3 read simulated table rows
for candidate against truth rather than the run's own observations, so what
leaks is indirect.

``test_a27_an_arm_that_ran_every_design_is_still_scored`` is the case the entry
was written about: under the derivation B1 scored ``nan``, and the whole point
of the fix is that it now scores on the same three questions as everyone else.

Why the address gains a term
----------------------------

Membership is a choice, and a choice that changes what D2 and D3 mean. Nothing
in :class:`~sciagent.eval.matrix.CampaignAddress` moves when it changes -- the
version columns describe the environment, its data and its diagnostic catalogue
-- so without a battery term a re-scored cell would land on the address of the
reading it replaced, and ``run_matrix``'s ``skip_recorded`` default would report
the stale row as the new campaign's. That is the same failure
:data:`~sciagent.eval.scoring.DIMENSION_VERSION` was added to
:func:`~sciagent.eval.matrix.cell_key` for under A26, and it is why this gate
checks the *recorded* address out of the ledger and not only ``cell_key``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

import sciagent.systems
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    forced_design,
    held_out_designs,
    simulator,
    slice_designs,
)
from environments.pointproc.runner import scenario_battery
from environments.pointproc.scenarios import scenario, slice_scenarios
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
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    _battery_payload,
    battery_key,
    cell_key,
    held_out_battery,
    reading_of,
    run_matrix,
)
from sciagent.eval.report import render, summarise
from sciagent.eval.scenarios import Scenario
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.experiments.dsl import (
    CompareCandidates,
    ExperimentDesign,
    is_intervention,
)
from sciagent.experiments.executor import Executor
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.budget import Budget
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, ResearchSystem, null_seeded_graph
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)


def slice_run(
    name: str, system: ResearchSystem
) -> tuple[ScenarioRun, Executor, EmpiricalTableEngine]:
    """Return one real run on the slice, with the executor and engine it used."""
    table = gate_table()
    target = scenario(name)
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    runner = executor(GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget)
    run = run_scenario(target, system, executor=runner, engine=engine, graph=graph)
    return run, runner, engine


def _used(run: ScenarioRun) -> set[str]:
    """Return the template ids the investigation actually spent budget on."""
    return {str(record.template) for record in run.evidence.ordered()}


@pytest.fixture
def substitute_battery_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[set[str]], None]]:
    """Yield a way to swap the environment's battery id set for one test.

    ``held_out_designs`` reads a module-level declaration rather than an
    argument, so exercising its refusal means substituting the declaration.
    Through ``monkeypatch`` by dotted name, which is how ``tests/test_matrix.py``
    substitutes ``DIMENSION_VERSION`` -- and which is also the only spelling that
    type-checks, since the constant is ``Final``.

    A fixture rather than a plain helper because of the teardown:
    ``slice_scenarios`` is ``lru_cache``d, so a scenario built while the
    substitution was in force would outlive it and be handed to every later test
    in the process. The cache is cleared on the way in and on the way out, and
    the ``yield`` is what guarantees the second half runs even when the body
    raises.
    """
    slice_scenarios.cache_clear()

    def substitute(ids: set[str]) -> None:
        monkeypatch.setattr(
            "environments.pointproc.outcomes._HELD_OUT_IDS", frozenset(ids)
        )
        slice_scenarios.cache_clear()

    yield substitute
    slice_scenarios.cache_clear()


def _swap_one_observational(
    battery: tuple[ExperimentDesign, ...],
) -> tuple[ExperimentDesign, ...]:
    """Return ``battery`` with one observational member exchanged for another.

    Same cardinality, same number of interventions, different membership. This
    is what separates a term over the battery's *members* from one over its
    count -- and the count is the natural slip, because ``n_held_out`` is
    already how the ledger summarises a battery and how ``docs/BACKLOG.md``
    describes one. A test whose two batteries differ in size cannot tell the
    two apart.
    """
    inside = next(design for design in battery if not is_intervention(design))
    outside = next(
        design
        for design in slice_designs()
        if design not in battery and not is_intervention(design)
    )
    return tuple(outside if design == inside else design for design in battery)


def _task(system: str = "V7", name: str = "S11", replicate: int = 0) -> CellTask:
    return CellTask(
        cell=Cell(system, ScenarioId(name), 20), replicate=replicate, seed=Seed(7)
    )


def _stub_reading(task: CellTask) -> CellReading:
    """Return a reading carrying stated numbers and nothing derived.

    The driver takes a ``CellReading`` rather than a float mapping so that only
    :func:`~sciagent.eval.matrix.reading_of` can author one; the cost of that is
    that a test wanting a recorded row has to build the whole vector. Nothing
    here is read -- this gate is about the row's *address*.

    ``battery`` is the exception, and it is read: ``run_matrix`` refuses a
    reading whose battery disagrees with the address it would be recorded at, so
    a stub has to report the scenario's own. Taking it off ``task`` rather than
    hard-coding one is what keeps this honest under
    :func:`substitute_battery_ids`.
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
        experiments=8,
        structural_distance=1.0,
        battery=battery_key(scenario(str(task.cell.scenario)).held_out),
    )


class TestA27PreregisteredBattery:
    """The battery is the scenario's, not the run's."""

    def test_a27_the_battery_is_a_function_of_the_scenario_alone(self) -> None:
        """Two arms, two genuinely different histories, one battery.

        The gate's first clause, on real runs rather than constructed ones: what
        the derivation got wrong was a *consequence of running experiments*, so a
        constructed run with an empty evidence index could not have caught it.
        S9 is the null scenario, so both arms' candidates and the truth are
        already in the gate table and neither cell costs a new simulation.
        """
        boed, _, _ = slice_run("S9", BOEDOnly(closed_set()))
        ppc, _, _ = slice_run("S9", PPCOnly())
        assert _used(boed) != _used(ppc), (
            "the two arms ran the same designs, so this test cannot show that "
            "the battery is independent of the history -- pick two arms whose "
            "policies differ"
        )
        assert held_out_battery(boed) == held_out_battery(ppc)
        assert held_out_battery(boed) == scenario("S9").held_out

    def test_a27_every_scenario_declares_a_battery_with_an_intervention(self) -> None:
        """The gate's second clause, over the scenarios that are actually scored.

        §8 names D3 the "held-out intervention battery". A battery of pure
        observation makes that phrase false and D3 a measure of something else.
        """
        for target in slice_scenarios():
            assert target.held_out, f"{target.id} declares no held-out battery"
            assert any(is_intervention(design) for design in target.held_out), (
                f"{target.id}'s battery holds no intervention, so D3 is not the "
                f"held-out intervention battery §8 defines"
            )

    def test_a27_battery_membership_appears_in_the_recorded_address(self) -> None:
        """The gate's third clause, read back out of the ledger.

        Out of the ledger and not off ``cell_key``, because what the criterion
        protects is the *recorded* address: ``run_matrix`` is what writes it, and
        a driver that computed the term and then dropped it would satisfy a
        ``cell_key``-only assertion.
        """
        cells = (Cell("B1", ScenarioId("S9"), 1),)
        with CampaignLedger.in_memory() as ledger:
            outcome = run_matrix(
                cells,
                address=ADDRESS,
                scenario_seed=lambda target: scenario(str(target)).seed,
                battery=scenario_battery,
                execute=_stub_reading,
                ledger=ledger,
            )
            recorded = outcome.entries[0].key
        assert recorded.config["battery"] == battery_key(scenario("S9").held_out)

    def test_a27_two_batteries_are_two_addresses(self) -> None:
        """A battery change is a new address, not a correction of the old rows.

        Invariant 4's consequence, and the reason the term is in the address at
        all: without it, re-scoring a cell under a different battery would land
        on the address of the reading it replaced and ``skip_recorded`` would
        report the stale row as the new campaign's, having executed nothing to
        disagree with it.
        """
        full = scenario("S9").held_out
        swapped = _swap_one_observational(full)
        assert len(swapped) == len(full) and set(swapped) != set(full), (
            "the two batteries must differ in membership at the same size, or "
            "this test cannot tell an address term that covers membership from "
            "one that covers only the count"
        )
        assert (
            cell_key(_task(), ADDRESS, battery=full).digest
            != cell_key(_task(), ADDRESS, battery=swapped).digest
        )

        narrower = tuple(design for design in full if is_intervention(design))
        assert narrower and narrower != full
        assert (
            cell_key(_task(), ADDRESS, battery=full).digest
            != cell_key(_task(), ADDRESS, battery=narrower).digest
        )

    def test_a27_the_battery_term_does_not_depend_on_declaration_order(self) -> None:
        """Two orderings of one battery are one address.

        Invariant 3 at the address: a battery is a set of questions, so a term
        that moved with the order they were listed in would make an address
        depend on something that determines nothing.
        """
        battery = scenario("S9").held_out
        assert battery_key(battery) == battery_key(tuple(reversed(battery)))

    def test_a27_an_arm_that_ran_every_design_is_still_scored(self) -> None:
        """The case the entry was written about: B1's ``nan`` D3 on 20/20 rows.

        Under the derivation an arm that spent its budget on every offered
        design had an empty battery, so D2 and D3 were ``nan`` -- the arm was
        not scored on the dimensions the contrast is read on. Declared, the
        battery is the same three questions whatever the arm did.
        """
        run, runner, engine = slice_run("S9", PPCOnly())
        assert _used(run) == {str(design.id) for design in slice_designs()}, (
            "PPCOnly no longer runs every offered design, so this test is no "
            "longer the exhausted-budget case the entry measured"
        )
        reading, _grown = reading_of(
            run,
            grammar=runner.grammar,
            table=engine.table,
            simulate=simulator(GRAMMAR),
            observations=engine.observations,
        )
        assert reading.dimensions.n_held_out == len(scenario("S9").held_out)

    def test_a27_scoring_reads_the_members_and_not_merely_their_count(self) -> None:
        """D2 and D3 are computed over the battery the scenario *names*.

        ``n_held_out`` is a length
        (:attr:`~sciagent.eval.scoring.DimensionVector.n_held_out`), so an
        implementation that handed :func:`~sciagent.eval.matrix.reading_of` any
        three offered designs would satisfy a count assertion while scoring D3
        on a battery holding no intervention -- which is the defect
        ``docs/BACKLOG.md`` was written about, arrived at by a different route.

        S11 is where the difference is largest and already measured:
        ``docs/DECISIONS.md`` records a Hawkes candidate against S11's truth
        reading D3 0.70 with ``size_gap_correlation`` in the battery and 0.960
        without, because that design is the only one that sees the mark-arrival
        coupling. Two batteries of one size and different membership therefore
        have to disagree, and the assertion is that they do.
        """
        run, runner, engine = slice_run("S11", BOEDOnly(closed_set()))
        swapped = _swap_one_observational(run.scenario.held_out)
        as_declared, grown = reading_of(
            run,
            grammar=runner.grammar,
            table=engine.table,
            simulate=simulator(GRAMMAR),
            observations=engine.observations,
        )
        as_swapped, _ = reading_of(
            replace(run, scenario=replace(run.scenario, held_out=swapped)),
            grammar=runner.grammar,
            table=grown,
            simulate=simulator(GRAMMAR),
            observations=engine.observations,
        )
        assert as_declared.dimensions.n_held_out == as_swapped.dimensions.n_held_out
        assert (
            as_declared.dimensions.d3_intervention_similarity
            != as_swapped.dimensions.d3_intervention_similarity
        ), (
            "two batteries of the same size and different membership scored "
            "identically, so nothing here shows that the declared designs are "
            "the ones D3 was computed over"
        )

    def test_a27_a_battery_member_the_scenario_does_not_offer_is_refused(self) -> None:
        """A battery is scored off the scenario's own table rows.

        A design outside the offer has no row for the candidate or the truth, so
        it would either be silently simulated at the slice's full replicate cost
        inside the scoring path or read as an absent cell. Refused at
        construction instead.
        """
        offered = tuple(
            design for design in slice_designs() if design != forced_design()
        )
        with pytest.raises(MalformedDesignError, match="does not offer"):
            Scenario(
                id=ScenarioId("stray"),
                scenario_class="single",
                truth=frozenset(),
                designs=offered,
                budget=Budget(total=1.0),
                seed=Seed(1),
                held_out=(forced_design(),),
            )

    def test_a27_a_battery_with_no_intervention_is_refused(self) -> None:
        """The second clause, enforced where it is declared and not only tested.

        The gate above checks the twelve slice scenarios. This checks that a
        thirteenth cannot be written without one, which is what makes the
        property a guarantee of the type rather than a fact about today's data.
        """
        observational = tuple(
            design for design in slice_designs() if not is_intervention(design)
        )
        with pytest.raises(MalformedDesignError, match="intervention"):
            Scenario(
                id=ScenarioId("no_intervention"),
                scenario_class="single",
                truth=frozenset(),
                designs=slice_designs(),
                budget=Budget(total=1.0),
                seed=Seed(1),
                held_out=observational,
            )

    def test_a27_the_declared_battery_is_the_one_scoring_reads(self) -> None:
        """The environment's declaration and the slice's battery are one object.

        ``tests/test_scoring.py`` named its own ``HELD_OUT`` and
        ``docs/DECISIONS.md`` records a reported D3 moving silently when that
        list and the design set drifted apart. One declaration, named once.
        """
        assert held_out_designs() == scenario("S11").held_out
        assert set(held_out_designs()) <= set(slice_designs())

    def test_a27_a_battery_id_that_names_nothing_is_refused(
        self, substitute_battery_ids: Callable[[set[str]], None]
    ) -> None:
        """A typo must not silently shrink the battery.

        Found by review. ``held_out_designs`` selects by membership in a frozenset
        of ids, and an id matching no offered design simply contributes nothing --
        the battery drops from three members to two, D2, D3 and D5 all move, and
        no length- or subset-relative assertion goes red. That is the positional
        slice's failure in a slower form, and ``docs/DECISIONS.md`` records what
        the positional slice cost.
        """
        substitute_battery_ids({"query:size_dispersion", "query:no_such_diagnostic"})
        with pytest.raises(MalformedDesignError, match="does not offer"):
            held_out_designs()

    def test_a27_a_battery_member_differing_only_in_run_length_is_refused(
        self,
    ) -> None:
        """Membership is by design, not by template id.

        ``ExperimentDesign.id`` is documented as *not injective*: ``n_events`` is
        deliberately absent from it. So a battery member sharing an offered
        design's id and differing in run length would pass an id-based check and
        then be scored off the offered design's table row — and a diagnostic's
        sampling distribution depends on how much data it saw, so that is a wrong
        number rather than a near-enough one.
        """
        offered = slice_designs()
        longer = replace(offered[0], n_events=offered[0].n_events * 2)
        assert longer.id == offered[0].id and longer != offered[0]
        with pytest.raises(MalformedDesignError, match="does not offer"):
            Scenario(
                id=ScenarioId("longer"),
                scenario_class="single",
                truth=frozenset(),
                designs=offered,
                budget=Budget(total=1.0),
                seed=Seed(1),
                held_out=(longer, forced_design()),
            )

    def test_a27_the_address_separates_two_run_lengths(self) -> None:
        """The same non-injectivity, at the address.

        ``battery_key`` digests each design's ``config`` rather than its ``id``
        for this reason: two batteries differing only in run length read different
        table rows and therefore differ on D2, D3 and D5, so an id-only digest
        would give them one address — the stale-row failure the term exists to
        prevent, one level down.
        """
        battery = scenario("S9").held_out
        longer = (replace(battery[0], n_events=battery[0].n_events * 2), *battery[1:])
        assert [design.id for design in longer] == [design.id for design in battery]
        assert battery_key(longer) != battery_key(battery)

    def test_a27_two_batteries_are_not_pooled_into_one_cell(self) -> None:
        """The address term is worth nothing if the report ignores it.

        Found by review, and it was a real hole: gate A26 added
        ``DIMENSION_VERSION`` to both the address *and* ``report._at_address``,
        and A27 first added the battery to only the address. Two rows differing in
        nothing but their battery therefore both matched the filter and were
        averaged into one cell. Refusing is the only honest option — under the
        fourth invariant both rows are legitimate and neither supersedes the
        other.
        """
        cell = Cell("V7", ScenarioId("S11"), 2)
        battery = scenario("S11").held_out
        rows = tuple(
            LedgerEntry(
                key=cell_key(
                    CellTask(cell=cell, replicate=index, seed=Seed(index)),
                    ADDRESS,
                    battery=which,
                ),
                reading=FrozenDict[str, float](
                    dict(_stub_reading(CellTask(cell, index, Seed(index))).as_payload())
                ),
                sequence=index,
            )
            for index, which in enumerate((battery, _swap_one_observational(battery)))
        )
        with pytest.raises(MalformedDesignError, match="two held-out batteries"):
            summarise(
                rows,
                address=ADDRESS,
                platform="Windows-11-x86_64",
                grammar=GrammarVersion("pointproc-edits/1.0.0"),
                scenario_class=lambda target: scenario(str(target)).scenario_class,
            )

    def test_a27_only_the_manipulating_operations_are_interventions(self) -> None:
        """What the second clause is quantified over.

        ``QueryDiagnostic`` and ``ConditionOn`` license no causal claim -- the
        DSL says so at both types -- so a battery of them is observational
        however many designs it holds.
        """
        interventions = [
            design for design in slice_designs() if is_intervention(design)
        ]
        assert interventions == [forced_design()]

    def test_a27_a_repeated_battery_member_is_refused(self) -> None:
        """A member named twice weights one question twice, invisibly.

        D2 and D3 are means over the battery and D5 is a maximum over it, so a
        repeated design is not a harmless duplicate -- it moves all three, and
        ``n_held_out`` reports the inflated count as though it were membership.
        The *offered* designs have been refused for repeating a template id
        since the type was written; the battery was not, until review.
        """
        target = scenario("S11")
        with pytest.raises(MalformedDesignError, match="more than once"):
            replace(target, held_out=(*target.held_out, target.held_out[0]))

    def test_a27_the_battery_term_survives_a_config_carrying_its_separator(
        self,
    ) -> None:
        r"""The address term must be unambiguous for *any* design, not the usual ones.

        ``battery_key`` joined designs on ``\x00`` and a design's config entries
        on ``\x01`` until review, on the premise that no config value holds
        either. The premise is false by construction:
        :func:`~sciagent.experiments.dsl.operation_config` renders a
        ``CompareCandidates`` candidate set as its keys joined on ``\x00``, so
        one value carries the byte that separated designs. Under a delimited
        encoding a battery of ``{"P\x00Q", "R"}`` and one of ``{"P", "Q\x00R"}``
        build one payload -- equal cardinality, different membership, one
        address, which is the failure the term exists to prevent arriving
        through the encoding instead of through the count.

        Asserted by *decoding* rather than by hunting a colliding pair of real
        designs: a length-framed payload is decodable, and a decodable payload
        cannot be ambiguous whatever the parts contain. The parser below is
        written independently of the encoder, so it checks the encoding rather
        than restating it.
        """
        library = closed_set()
        carrier = ExperimentDesign(
            operation=CompareCandidates(
                candidates=(library["hawkes"], library["seasonality"])
            ),
            outcome=forced_design().outcome,
            n_events=64,
        )
        assert "\x00" in dict(carrier.config())["op.candidates"], (
            "this test's premise is that a config value carries the byte the "
            "earlier battery_key used as a separator. If operation_config no "
            "longer joins candidates on NUL, re-point the test rather than "
            "deleting it: the guarantee is about any value, not this one"
        )

        def unframe(payload: str) -> list[str]:
            """Return the parts a length-framed payload was built from."""
            parts: list[str] = []
            cursor = 0
            while cursor < len(payload):
                colon = payload.index(":", cursor)
                size = int(payload[cursor:colon])
                parts.append(payload[colon + 1 : colon + 1 + size])
                cursor = colon + 1 + size
            return parts

        def entries(design: ExperimentDesign) -> list[str]:
            return [
                part
                for name, value in sorted(design.config().items())
                for part in (name, value)
            ]

        battery = (carrier, forced_design())
        recovered = [unframe(design) for design in unframe(_battery_payload(battery))]
        # As a multiset. *Which* order the payload puts the designs in is the
        # encoder's business -- it sorts, so that two orderings of one battery
        # give one term -- and asserting the same sort here would restate the
        # implementation instead of checking it. What the guarantee needs is
        # that exactly these designs come back out, and no others.
        assert sorted(recovered) == sorted(entries(design) for design in battery)

    def test_a27_a_reading_scored_on_another_battery_is_not_recorded(self) -> None:
        """The address and the reading are two resolutions of one fact.

        ``run_matrix`` addresses a cell *before* it runs, from the caller's
        ``battery`` callback; ``reading_of`` scores it afterwards, from the
        scenario the run actually carried. Nothing but a docstring said the two
        agreed until review. A row recorded at one battery's address while
        scored on another is the stale-reading failure the term exists to stop,
        arriving from the other side -- and it would be invisible in the ledger,
        because the address is the only place membership is written down.
        """
        cells = (Cell("B1", ScenarioId("S9"), 1),)

        def foreign(task: CellTask) -> CellReading:
            return replace(
                _stub_reading(task),
                battery=battery_key(_swap_one_observational(scenario("S9").held_out)),
            )

        with CampaignLedger.in_memory() as ledger:
            with pytest.raises(MalformedDesignError, match="addressed on battery"):
                run_matrix(
                    cells,
                    address=ADDRESS,
                    scenario_seed=lambda target: scenario(str(target)).seed,
                    battery=scenario_battery,
                    execute=foreign,
                    ledger=ledger,
                )
            assert not ledger.entries(), "the row must not reach the ledger"

    def test_a27_the_report_names_the_battery_it_read(self) -> None:
        """D2, D3 and D5 mean nothing without the battery they are figures under.

        ``summarise`` cannot check the term against a current one -- a battery is
        declared per scenario on an environment, and :mod:`sciagent` may not
        import one -- so a report built entirely on rows under a *superseded*
        battery is accepted. Rendering the term is what keeps that from being
        silent: a reader holding the declaration can compare, which is strictly
        more than they could do before.
        """
        cell = Cell("B1", ScenarioId("S9"), 1)
        task = CellTask(cell=cell, replicate=0, seed=Seed(3))
        rows = (
            LedgerEntry(
                key=cell_key(task, ADDRESS, battery=scenario("S9").held_out),
                reading=FrozenDict[str, float](dict(_stub_reading(task).as_payload())),
                sequence=0,
            ),
        )
        report = summarise(
            rows,
            address=ADDRESS,
            platform="test",
            grammar=GrammarVersion("pointproc-edits/1.0.0"),
            scenario_class=lambda target: scenario(str(target)).scenario_class,
        )
        term = battery_key(scenario("S9").held_out)
        assert report.cells[0].battery == term
        assert term in render(report)

    def test_a27_the_diagnostic_names_the_term_that_excluded_the_row(self) -> None:
        """The message must name what dropped the row, not what it matched.

        A pre-A27 row matches the campaign version, the dimension reading, every
        version column and the partition, and is dropped for the one term the
        message did not mention. Anyone pointing ``scripts/report_matrix.py`` at
        the recorded 1,120-row matrix meets exactly this, and was sent looking
        for a version that had not moved.
        """
        cell = Cell("B1", ScenarioId("S9"), 1)
        task = CellTask(cell=cell, replicate=0, seed=Seed(3))
        addressed = cell_key(task, ADDRESS, battery=scenario("S9").held_out)
        before_a27 = replace(
            addressed,
            config=FrozenDict[str, str](
                {
                    name: value
                    for name, value in addressed.config.items()
                    if name != "battery"
                }
            ),
        )
        rows = (
            LedgerEntry(
                key=before_a27,
                reading=FrozenDict[str, float](dict(_stub_reading(task).as_payload())),
                sequence=0,
            ),
        )
        with pytest.raises(MalformedDesignError, match="carry no battery") as raised:
            summarise(
                rows,
                address=ADDRESS,
                platform="test",
                grammar=GrammarVersion("pointproc-edits/1.0.0"),
                scenario_class=lambda target: scenario(str(target)).scenario_class,
            )
        assert "before gate A27" in str(raised.value)

    def test_a27_no_research_system_can_reach_the_battery(self) -> None:
        """The battery is framework apparatus, and nothing asserted that but this.

        ``Scenario.truth`` is unreachable from a system by construction, and the
        battery has to be too: a system that could read it could shape a
        proposal to the questions it will be scored on. Until this test, that
        rested on unasserted structural facts -- ``Investigation`` carries no
        scenario, and ``systems/`` imports no ``sciagent.eval``. Adding
        ``held_out`` to ``Investigation`` tomorrow would have failed nothing.
        Asked for by the invariant auditor, which could verify the facts and
        observed that no test held them.
        """
        systems = Path(str(sciagent.systems.__file__)).parent
        offenders = sorted(
            f"{path.relative_to(systems)}:{number}"
            for path in systems.rglob("*.py")
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if line.startswith(("import sciagent.eval", "from sciagent.eval"))
        )
        assert not offenders, (
            f"{offenders!r} import the evaluation layer into the systems "
            f"package. Scenario.held_out lives there, and an import is the first "
            f"step of a path from an agent to the questions it is scored on"
        )
        assert not [
            slot
            for slot in Investigation.__slots__
            if "scenario" in slot and slot != "_scenario_id"
        ], (
            "Investigation gained a scenario-shaped slot; both Scenario.truth "
            "and Scenario.held_out are reachable from one"
        )
