"""The §9 matrix driver: its cells, its seeds, and what resuming means.

SPEC §11 item 15 carries **no acceptance criterion**, so nothing here is named
``test_aN_``; ``scripts/status.py`` derives gate coverage from that convention
and crediting these to a gate would report a contract that does not exist. What
is checked instead:

- The §9 cell table is §9's, and has not quietly grown an arm.
- Seeds are a function of the scenario and the replicate index alone -- paired
  across systems, and independent of the order the driver happens to visit
  cells in, which is what SPEC's third invariant forbids.
- Resuming skips what is addressed rather than overwriting what looks stale,
  which is the only meaning the fourth invariant leaves available.
- A cell whose inputs changed is a *new* address with the old row still
  present.

The driver is exercised against a stand-in ``execute``. A test that ran real
cells would be minutes of simulation asserting nothing this file is about.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

from environments.pointproc.matrix import REPLICATES, SPEC9_CELLS
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    held_out_designs,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import MalformedDesignError, RegistryConflictError
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    MetricVersion,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.eval.agency import AgencyMetrics
from sciagent.eval.campaign import Adjudication, ScenarioRun, run_scenario
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    MatrixOutcome,
    battery_key,
    cell_key,
    held_out_battery,
    reading_of,
    replicate_seeds,
    run_matrix,
)
from sciagent.eval.scoring import (
    DIMENSION_VERSION,
    ClosedWorldScore,
    DimensionVector,
)
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.experiments.executor import Executor
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.ledger import CampaignLedger
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentKey, ExperimentStore
from sciagent.systems.base import ResearchSystem, null_seeded_graph
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

#: Stand-in for the environment's own seed table, in its shape: one fixed seed
#: per scenario, drawn apart so two scenarios are not the same investigation
#: twice. The real one is ``environments.pointproc.scenarios``.
SEEDS = {ScenarioId(f"S{index}"): Seed(20260900 + index) for index in range(1, 13)}


def seed_of(scenario: ScenarioId) -> Seed:
    return SEEDS[scenario]


#: The battery this module's addresses are built over: the slice's own, since
#: `slice_run` runs real slice scenarios and an address has to match the one
#: `reading_of` scored. Bound to a name so the assertions read, not to decouple
#: anything -- an earlier comment here claimed it was "named rather than
#: imported" to keep these addresses independent of the slice's battery, which
#: was simply false of `held_out_designs()` and was caught by review.
BATTERY = held_out_designs()


def battery_of(scenario: ScenarioId) -> tuple[ExperimentDesign, ...]:
    return BATTERY


def stub_reading(**overrides: float) -> CellReading:
    """Return a ``CellReading`` carrying stated numbers and nothing derived.

    ``run_matrix`` takes a ``CellReading`` rather than a float mapping so that
    only :func:`~sciagent.eval.matrix.reading_of` can author one, which is the
    invariant made structural. The cost lands here: a test that wants a
    recognisable payload has to build the whole vector. That cost is the
    feature -- it is exactly the friction a caller hand-rolling a payload
    would have met.
    """
    # ``probe`` defaults apart from ``ppc`` so that a payload rendered from this
    # builder distinguishes the two checks: equal defaults would let a
    # transposition of the two keys go unnoticed everywhere this is used.
    values = {
        "d1": 0.0,
        "d3": 0.0,
        "log_score": 0.0,
        "ppc": 0.0,
        "probe": 0.03,
        **overrides,
    }
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=values["d1"],
            d2_held_out_predictive=-1.0,
            d3_intervention_similarity=values["d3"],
            d4_explanatory_coverage=0.5,
            d5_enabled_experiment_value=0.25,
            d6_complexity=12.0,
            n_held_out=3,
        ),
        score=ClosedWorldScore(
            truth_mass=Probability(0.5),
            log_score=values["log_score"],
            leading_mass=Probability(0.5),
            correct=True,
            identified=False,
        ),
        ppc_p_value=values["ppc"],
        inadequate=False,
        probe_p_value=values["probe"],
        probe_inadequate=False,
        agency=AgencyMetrics(
            system="V1",
            scenario=ScenarioId("S1"),
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
        # The battery this module addresses on, since `run_matrix` refuses a
        # reading scored on one battery at another's address.
        battery=battery_key(BATTERY),
    )


def reading_of_task(task: CellTask) -> CellReading:
    """Return a stand-in reading that depends on the cell and on nothing else."""
    return stub_reading(
        d1=float(len(task.cell.system)), d3=float(task.replicate) / 100.0
    )


class TestTheCellTableIsSpecNine:
    def test_the_matrix_is_exactly_fifty_six_cells(self) -> None:
        # §9: V1/V7/B4/B5 on all twelve (48), B1 on S9 and S11 (2), the V3/V4
        # ablation on S8, S11 and S12 (6). "Small and interpretable" -- a new
        # arm belongs in docs/BACKLOG.md per §13, not here.
        assert len(SPEC9_CELLS) == 56

    def test_the_matrix_is_eleven_hundred_and_twenty_investigations(self) -> None:
        assert REPLICATES == 20
        assert sum(cell.replicates for cell in SPEC9_CELLS) == 1120

    def test_every_system_covers_the_scenarios_spec_nine_gives_it(self) -> None:
        covered: dict[str, set[str]] = {}
        for cell in SPEC9_CELLS:
            covered.setdefault(cell.system, set()).add(str(cell.scenario))
        twelve = {f"S{index}" for index in range(1, 13)}
        assert covered["V1"] == twelve
        assert covered["V7"] == twelve
        assert covered["B4"] == twelve
        assert covered["B5"] == twelve
        assert covered["B1"] == {"S9", "S11"}
        assert covered["V3"] == {"S8", "S11", "S12"}
        assert covered["V4"] == {"S8", "S11", "S12"}

    def test_no_cell_is_listed_twice(self) -> None:
        pairs = [(cell.system, str(cell.scenario)) for cell in SPEC9_CELLS]
        assert len(set(pairs)) == len(pairs)

    def test_the_driver_refuses_a_matrix_that_repeats_a_cell(self) -> None:
        # The check above is of the *table*; this is of the driver, and they are
        # not the same guarantee. A duplicated (system, scenario) would execute
        # twice under skip_recorded=False and count the second as skipped under
        # skip_recorded=True -- so both the tally and run_matrix's documented
        # "executed at most once per pass" would be false. Raised by review.
        doubled = (Cell("V7", ScenarioId("S11"), 2), Cell("V7", ScenarioId("S11"), 2))
        with (
            CampaignLedger.in_memory() as ledger,
            pytest.raises(MalformedDesignError, match="same address"),
        ):
            _drive(doubled, ledger)


class TestSeedsArePairedAndOrderIndependent:
    """SPEC §9 compares arms on one scenario, so the arms must see one world."""

    def test_two_systems_on_one_scenario_draw_the_same_seeds(self) -> None:
        # The preregistered contrast is V7 against B4 on S11. Paired seeds make
        # that a comparison of systems; unpaired seeds would make it partly a
        # comparison of worlds, at twenty draws per arm.
        v7 = replicate_seeds(SEEDS[ScenarioId("S11")], REPLICATES)
        b4 = replicate_seeds(SEEDS[ScenarioId("S11")], REPLICATES)
        assert v7 == b4

    def test_a_seed_does_not_depend_on_the_order_cells_are_visited(self) -> None:
        # Invariant 3: the driver may not derive seeds from iteration order.
        forward = _tasks_of(SPEC9_CELLS)
        backward = _tasks_of(tuple(reversed(SPEC9_CELLS)))
        assert forward == backward

    def test_two_scenarios_do_not_share_a_seed_stream(self) -> None:
        first = set(replicate_seeds(SEEDS[ScenarioId("S1")], REPLICATES))
        second = set(replicate_seeds(SEEDS[ScenarioId("S11")], REPLICATES))
        assert not first & second

    def test_a_scenario_draws_distinct_seeds(self) -> None:
        seeds = replicate_seeds(SEEDS[ScenarioId("S11")], REPLICATES)
        assert len(set(seeds)) == REPLICATES

    def test_a_seed_stream_is_a_prefix_of_a_longer_one(self) -> None:
        # So extending a campaign's replicate count re-uses the seeds already
        # spent rather than re-drawing a disjoint set, which under invariant 4
        # would strand every cell already recorded at a dead address.
        short = replicate_seeds(SEEDS[ScenarioId("S11")], 5)
        long = replicate_seeds(SEEDS[ScenarioId("S11")], REPLICATES)
        assert long[:5] == short

    def test_the_scenario_seed_itself_is_not_reused(self) -> None:
        # The scenario's own seed drives its executions; a replicate reusing it
        # would make replicate 0 the run the rest are meant to vary from.
        seeds = replicate_seeds(SEEDS[ScenarioId("S11")], REPLICATES)
        assert SEEDS[ScenarioId("S11")] not in seeds


def _tasks_of(cells: Sequence[Cell]) -> dict[tuple[str, str, int], int]:
    """Return every (system, scenario, replicate) -> seed the driver would use."""
    return {
        (cell.system, str(cell.scenario), replicate): int(seed)
        for cell in cells
        for replicate, seed in enumerate(
            replicate_seeds(seed_of(cell.scenario), cell.replicates)
        )
    }


class TestResumingSkipsWhatIsAddressed:
    def test_a_completed_matrix_runs_nothing_on_a_second_pass(self) -> None:
        cells = (Cell("V7", ScenarioId("S11"), 3), Cell("B4", ScenarioId("S11"), 3))
        with CampaignLedger.in_memory() as ledger:
            first = _drive(cells, ledger)
            second = _drive(cells, ledger)
            assert ledger.count() == 6
        assert first.ran == 6 and first.skipped == 0
        assert second.ran == 0 and second.skipped == 6

    def test_a_partial_campaign_resumes_and_runs_each_cell_once(self) -> None:
        cells = (Cell("V7", ScenarioId("S11"), 4),)
        executed: list[str] = []

        def stop_after_two(task: CellTask) -> CellReading:
            if len(executed) == 2:
                raise KeyboardInterrupt("the session ended badly")
            executed.append(task.name)
            return reading_of_task(task)

        with CampaignLedger.in_memory() as ledger:
            with pytest.raises(KeyboardInterrupt):
                run_matrix(
                    cells,
                    address=ADDRESS,
                    scenario_seed=seed_of,
                    battery=battery_of,
                    execute=stop_after_two,
                    ledger=ledger,
                )
            assert ledger.count() == 2
            resumed = _drive(cells, ledger, executed=executed)
            assert ledger.count() == 4
        assert resumed.ran == 2 and resumed.skipped == 2
        assert len(executed) == 4
        assert len(set(executed)) == 4, "no cell may be executed twice"

    def test_the_reading_a_skipped_cell_reports_is_the_recorded_one(self) -> None:
        cells = (Cell("V7", ScenarioId("S11"), 2),)
        with CampaignLedger.in_memory() as ledger:
            first = _drive(cells, ledger)
            second = _drive(cells, ledger)
        assert [dict(entry.reading) for entry in first.entries] == [
            dict(entry.reading) for entry in second.entries
        ]

    def test_a_cell_that_disagrees_with_its_recorded_reading_raises(self) -> None:
        # Invariant 3 at matrix scale, and the check the platform divergence
        # would trip: same address, different number, so something outside the
        # address moved.
        cells = (Cell("V7", ScenarioId("S11"), 1),)
        with CampaignLedger.in_memory() as ledger:
            _drive(cells, ledger)
            with pytest.raises(RegistryConflictError):
                run_matrix(
                    cells,
                    address=ADDRESS,
                    scenario_seed=seed_of,
                    battery=battery_of,
                    execute=lambda _task: stub_reading(d1=99.0),
                    ledger=ledger,
                    skip_recorded=False,
                )


class TestAnAddressCoversWhatDeterminesACell:
    def test_a_metric_version_bump_is_a_new_address(self) -> None:
        # The trap docs/BACKLOG.md asks to be written down: a cell re-run
        # because its inputs changed is a new address, so the old row stays and
        # both are in the ledger. A report selects by address, never "one row
        # per cell".
        task = CellTask(
            cell=Cell("V7", ScenarioId("S11"), 1), replicate=0, seed=Seed(7)
        )
        before = cell_key(task, ADDRESS, battery=BATTERY)
        after = cell_key(
            task, ADDRESS.at(metric_version=MetricVersion("1.3.0")), battery=BATTERY
        )
        assert before.digest != after.digest

        with CampaignLedger.in_memory() as ledger:
            ledger.append(before, reading={"d3": 0.96})
            ledger.append(after, reading={"d3": 0.42})
            assert ledger.count() == 2
            assert ledger.contains(before.digest)

    def test_a_dimension_reading_bump_is_a_new_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The sibling of the metric-version case, and the one no version
        # *column* can express: D1-D6 are computed in sciagent.eval.scoring, so
        # nothing in CampaignAddress moves when a dimension's definition
        # changes. Without this term a re-scored cell would land on the address
        # of the reading it replaced, and run_matrix's skip_recorded default
        # would report the stale row as the new campaign's -- having executed
        # nothing to disagree with it.
        task = CellTask(
            cell=Cell("V7", ScenarioId("S11"), 1), replicate=0, seed=Seed(7)
        )
        after = cell_key(task, ADDRESS, battery=BATTERY)
        assert after.config["dimensions"] == DIMENSION_VERSION

        monkeypatch.setattr("sciagent.eval.matrix.DIMENSION_VERSION", "spec8/1")
        before = cell_key(task, ADDRESS, battery=BATTERY)
        assert before.config["dimensions"] == "spec8/1"
        assert before.digest != after.digest

    def test_a_partition_change_is_a_new_address(self) -> None:
        # A cell run on DEV and the same cell run on TEST are two readings and
        # must not collide.
        task = CellTask(
            cell=Cell("V7", ScenarioId("S11"), 1), replicate=0, seed=Seed(7)
        )
        dev = cell_key(task, ADDRESS, battery=BATTERY)
        test = cell_key(task, ADDRESS.at(partition=DataPartition.TEST), battery=BATTERY)
        assert dev.digest != test.digest

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            (("V7", "S11", 0), ("B4", "S11", 0)),
            (("V7", "S11", 0), ("V7", "S1", 0)),
            (("V7", "S11", 0), ("V7", "S11", 1)),
        ],
    )
    def test_every_coordinate_of_a_cell_is_in_its_address(
        self, left: tuple[str, str, int], right: tuple[str, str, int]
    ) -> None:
        assert _key_of(left).digest != _key_of(right).digest

    def test_the_address_is_the_same_in_every_process(self) -> None:
        # ExperimentKey.to_bytes is injective and PYTHONHASHSEED-independent;
        # this pins that the driver does not undo that by putting an unordered
        # rendering into config.
        task = CellTask(
            cell=Cell("V7", ScenarioId("S11"), 1), replicate=7, seed=Seed(7)
        )
        key = cell_key(task, ADDRESS, battery=BATTERY)
        assert key.digest == cell_key(task, ADDRESS, battery=BATTERY).digest
        assert dict(key.config) == {
            "matrix": "spec9/1",
            # Literal rather than `DIMENSION_VERSION`, deliberately: this is
            # what makes a bump something a session has to notice and account
            # for. `spec8/5` is A30 -- the payload gained `claims`,
            # `adjudicated`, `adjudication_rate`, `contradictions` and
            # `zombie_claims`, so a `spec8/4` row cannot answer a question about
            # SPEC 12 criterion 8 or 10. `spec8/4` was A31, which added the
            # autonomy fraction, F10's two tiers and criterion 9's three masses;
            # `spec8/3` was A29, which added `probe_p_value` and
            # `probe_inadequate`.
            #
            # This is the assertion the mechanism above is *for*, and A30 is
            # where it earned its keep: mypy caught the two sibling pins because
            # they compare against `DIMENSION_VERSION` and narrow to a
            # `Literal`, and could not catch this one because it is a dict
            # value. The suite did.
            "dimensions": "spec8/5",
            "battery": battery_key(BATTERY),
            "partition": "dev",
            "replicate": "07",
            "scenario": "S11",
            "system": "V7",
        }
        # Spelled through battery_key rather than as a literal, and that is not
        # laziness: the literal would pin a digest of the *slice's* battery into
        # a module that is otherwise free of the slice's choices, so changing the
        # battery would fail here with a hex mismatch rather than in
        # tests/acceptance/test_a27.py where the choice lives. What this line is
        # for is that the key is present and named, which a literal and a call
        # establish equally.
        assert key.config["battery"].startswith(f"{len(BATTERY)}#")


def _key_of(coordinate: tuple[str, str, int]) -> ExperimentKey:
    system, scenario, replicate = coordinate
    task = CellTask(
        cell=Cell(system, ScenarioId(scenario), 20),
        replicate=replicate,
        seed=Seed(1),
    )
    return cell_key(task, ADDRESS, battery=BATTERY)


class TestTheDriverReportsWhatItDid:
    def test_a_reading_reaches_the_ledger_unrounded(self) -> None:
        cells = (Cell("B1", ScenarioId("S9"), 1),)
        with CampaignLedger.in_memory() as ledger:
            outcome = run_matrix(
                cells,
                address=ADDRESS,
                scenario_seed=seed_of,
                battery=battery_of,
                execute=lambda _task: stub_reading(log_score=-math.inf, d3=math.nan),
                ledger=ledger,
            )
        reading = outcome.entries[0].reading
        assert reading["log_score"] == -math.inf
        assert math.isnan(reading["d3_intervention_similarity"])

    def test_the_outcome_names_every_cell_in_visit_order(self) -> None:
        cells = (Cell("V7", ScenarioId("S11"), 2), Cell("B4", ScenarioId("S1"), 1))
        with CampaignLedger.in_memory() as ledger:
            outcome = _drive(cells, ledger)
        assert [entry.key.config["system"] for entry in outcome.entries] == [
            "V7",
            "V7",
            "B4",
        ]
        assert outcome.ran == 3


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


class TestAReadingComesFromARealRun:
    """The framework-side half of ``execute``, against an actual investigation.

    B1 on S9: the null scenario run by the system that holds only the null, so
    both the candidate and the truth are already in the shared table and the
    cell costs no new simulation. What is under test is the wiring -- that a
    reading is derivable from a :class:`~sciagent.eval.campaign.ScenarioRun`
    alone, and that every field survives the ledger -- not any particular
    number. Asserting a value here would encode an answer nobody has yet, which
    is the rule ``tests/test_baselines_slice.py`` states for the same reason.
    """

    def test_every_field_of_a_reading_survives_the_ledger(self) -> None:
        run, runner, engine = slice_run("S9", PPCOnly())
        reading, _grown = reading_of(
            run,
            grammar=runner.grammar,
            table=engine.table,
            simulate=simulator(GRAMMAR),
            observations=engine.observations,
            program=runner.reference,
        )
        payload = reading.as_payload()
        with CampaignLedger.in_memory() as ledger:
            entry = ledger.append(_key_of(("B1", "S9", 0)), reading=payload)
            stored = ledger.get(entry.digest)
        assert stored is not None
        assert set(stored.reading) == set(payload)
        for name, value in payload.items():
            if math.isnan(value):
                assert math.isnan(stored.reading[name]), name
            else:
                assert stored.reading[name].hex() == value.hex(), name

    def test_a_reading_reports_what_the_run_reported(self) -> None:
        # Every field either comes off the run unchanged or off
        # sciagent.eval.scoring. None is authored here, and none by the system:
        # run_scenario has already reconciled the diagnosis against the engine.
        run, runner, engine = slice_run("S9", PPCOnly())
        reading, _grown = reading_of(
            run,
            grammar=runner.grammar,
            table=engine.table,
            simulate=simulator(GRAMMAR),
            observations=engine.observations,
            program=runner.reference,
        )
        assert reading.experiments == run.experiments
        assert reading.inadequate == run.ppc.inadequate
        assert reading.ppc_p_value == run.ppc.p_value
        # `reading_of` assigns two checks to four fields, so the probe's half is
        # pinned as well as the check's. Note what this scenario cannot show: S9
        # has both verdicts False, so a transposition confined to the *booleans*
        # is invisible here however many assertions are added. What catches it is
        # `tests/acceptance/test_a29.py`, on S11, where the two disagree.
        assert run.probe is not None, "S9 declares a Stage A design"
        assert reading.probe_p_value == run.probe.p_value
        assert reading.probe_inadequate == run.probe.inadequate
        assert reading.score == run.score
        assert reading.structural_distance == run.structural_distance
        assert reading.dimensions.n_held_out == len(held_out_battery(run))

    def test_scoring_against_the_wrong_observations_raises(self) -> None:
        # D4 sums over recorded experiments, so an empty list does not fail --
        # it reports explanatory coverage of 0.0, which reads as a measurement.
        # held_out is derived precisely so a caller cannot get it wrong;
        # observations cannot be derived, so it is checked instead. Raised by
        # the invariant auditor and by review, independently.
        run, runner, engine = slice_run("S9", PPCOnly())
        with pytest.raises(MalformedDesignError, match="observation"):
            reading_of(
                run,
                grammar=runner.grammar,
                table=engine.table,
                simulate=simulator(GRAMMAR),
                observations=(),
                program=runner.reference,
            )

    def test_scoring_a_scenario_with_no_stage_a_probe_raises(self) -> None:
        # Same failure shape as the observations check above, and found the same
        # way: without a Stage A design, `ScenarioRun.probe` falls back to the
        # check over an empty record -- a documented p_value of 1.0 and "not
        # inadequate". Written to a ledger row that summarises as a probe rate
        # of 0.000, which reads as "evaluated, never fired" rather than as "never
        # evaluated", and SPEC section 12 criterion 4 is read off that number.
        run, runner, engine = slice_run("S9", PPCOnly())
        # Both fields, because that is the pair `run_scenario` actually
        # produces: `_stage_a_probe` returns None precisely when the scenario
        # declares no design. Replacing only the scenario would build a run no
        # harness can emit, and `reading_of` reads the probe rather than the
        # declaration -- so such a fixture would pass while testing nothing.
        probeless = replace(
            run, scenario=replace(run.scenario, stage_a=None), probe=None
        )
        with pytest.raises(MalformedDesignError, match="no Stage A probe"):
            reading_of(
                probeless,
                grammar=runner.grammar,
                table=engine.table,
                simulate=simulator(GRAMMAR),
                observations=engine.observations,
                program=runner.reference,
            )

    def test_an_address_derived_from_the_executor_matches_it(self) -> None:
        # CampaignAddress.of reads the versions off the object that will
        # produce the evidence. A declared metric_version that a caller forgot
        # to bump does not raise under the default skip_recorded=True -- the
        # cell is skipped and its stale reading reported as the matrix's.
        _run, runner, _engine = slice_run("S9", PPCOnly())
        derived = CampaignAddress.of(runner)
        assert derived.metric_version == runner.scope().metric_version
        assert derived.env_version == runner.scope().env_version
        assert derived.data_version == runner.data_version
        assert derived.partition == runner.partition

    def test_the_held_out_battery_is_the_scenarios_and_not_the_runs(self) -> None:
        # Gate A27 reversed what this test used to assert. It read the battery as
        # the complement of the evidence index -- SPEC §8's "diagnostics unused
        # during the investigation", taken literally -- which made the question
        # set a function of what the arm chose to run, and graded B1 on nothing
        # at all. The battery is now declared on the scenario. The whole
        # criterion lives in tests/acceptance/test_a27.py; what is checked here
        # is that this module's driver reads the same thing, since it is what
        # puts the battery in the address.
        run, _runner, _engine = slice_run("S1", BOEDOnly(closed_set()))
        used = {record.template for record in run.evidence.ordered()}
        assert used, "the run should have executed something"
        assert held_out_battery(run) == run.scenario.held_out
        assert {design.id for design in held_out_battery(run)} & used, (
            "the declared battery no longer overlaps what a BOED arm runs, so "
            "this test no longer shows that the battery survives being run"
        )


def _drive(
    cells: Sequence[Cell],
    ledger: CampaignLedger,
    executed: list[str] | None = None,
) -> MatrixOutcome:
    """Run ``cells`` through the driver with a stand-in for the real work."""

    def execute(task: CellTask) -> CellReading:
        if executed is not None:
            executed.append(task.name)
        return reading_of_task(task)

    return run_matrix(
        cells,
        address=ADDRESS,
        scenario_seed=seed_of,
        battery=battery_of,
        execute=execute,
        ledger=ledger,
    )
