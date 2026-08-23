"""The environment-side half of the §9 matrix: which arm, at which seed, on which table.

SPEC §11 item 15 carries **no acceptance criterion**, so nothing here is named
``test_aN_``; ``scripts/status.py`` derives gate coverage from that convention and
crediting these to a gate would report a contract that does not exist. This is the
sibling of ``tests/test_matrix.py``, which owns the driver against a stand-in
``execute``. What is owned here is the ``execute`` the driver never had -- the
thing that turns a :class:`~sciagent.eval.matrix.CellTask` into a real
investigation.

Three of these tests were rewritten after an independent review of this module,
before any implementation existed. What the review measured is recorded here
because the replacements look more elaborate than the originals and the reason is
not visible from the code:

- **The seed test compared nothing.** It ran a replicate against a direct
  ``run_scenario`` on the *untouched* scenario, on the premise that
  ``replicate_seeds`` puts the scenario's own seed first. It does not --
  ``eval/matrix.py:293`` hashes ``matrix/{seed}/{index}``, so S9's 20260909
  becomes 3756393230493447090. The test passed only because its three asserted
  quantities are seed-invariant on B1/S9: measured over four replicate seeds, all
  four give ``(1.0, 8, 0.0)``, since ``ppc.py``'s ``1 + ln(n)`` scale saturates the
  null scenario under the null hypothesis at exactly 1.0. Its sibling --
  *two replicates differ* -- was therefore **red for a correct implementation**.
  A runner at ``task.seed + 7`` passed both.
- **The threading test accepted no threading.** It compared
  ``table.structures`` as a set under ``>=``, and a runner that rebuilt from
  ``gate_table()`` every replicate re-grew the same structure and passed.

Both are now measured rather than inferred. V1 on S1 is the seed instrument: at
``seed + 7`` seven payload fields move, and the same seed reproduces exactly.
B5 on S1 is the threading instrument: 12,000 simulator calls and 33.5s for the
first replicate, **0 calls and 0.6s** for the same task from the grown table.

Real investigations are run, not stand-ins, because the wiring is the subject.
B1 and V1 on S9 are the cheap pair -- the null scenario, nothing new simulated --
and are used wherever the arm does not matter, following ``tests/test_matrix.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import slice_tables
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

from environments.pointproc.matrix import ALL_CELLS, CRITERION5_CELLS, SPEC9_CELLS
from environments.pointproc.outcomes import executor, simulator, slice_designs
from environments.pointproc.runner import (
    ALL_SYSTEMS,
    CRITERION5_SYSTEMS,
    LLM_SYSTEMS,
    MATRIX_SYSTEMS,
    MatrixRunner,
    scenario_battery,
    scenario_seed,
    system_for,
)
from environments.pointproc.scenarios import scenario
from environments.pointproc.tables import cache_root
from sciagent.core.errors import SystemConfigurationError
from sciagent.core.types import GrammarVersion, ScenarioId, Seed
from sciagent.eval.campaign import run_scenario
from sciagent.eval.matrix import Cell, CellTask, reading_of, run_matrix, tasks_of
from sciagent.eval.report import summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.ledger import CampaignLedger
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import null_seeded_graph
from sciagent.systems.llm import (
    RECORD,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
)

#: The same script ``tests/test_hybrid.py`` and ``tests/test_ablation.py`` give
#: their arms, so an arm built here is the arm those modules built.
SCRIPT: tuple[dict[str, Any], ...] = (
    fixed_payload(3, (20, 30, 40), name="size_mixture"),
    fixed_payload(1, (10, 20, 30, 40), name="latent_regime"),
)


def provider_factory() -> ScriptedProvider:
    """Return a fresh offline provider. A factory, because a script is consumed."""
    return ScriptedProvider(list(SCRIPT))


def driver(**kwargs: Any) -> MatrixRunner:
    """Return a runner over the suite's shared table."""
    return MatrixRunner(gate_table(), **kwargs)


def task(system: str, scenario_id: str, replicate: int = 0) -> CellTask:
    """Return one replicate of one cell, seeded as the driver would seed it."""
    target = ScenarioId(scenario_id)
    return tasks_of(Cell(system, target, replicates=2), scenario_seed(target))[
        replicate
    ]


def scenario_class_of(target: ScenarioId) -> ScenarioClass:
    return scenario(str(target)).scenario_class


def direct_payload(scenario_id: str, system: str, seed: Seed) -> dict[str, float]:
    """Return the reading a plain ``run_scenario`` at ``seed`` produces.

    Deliberately not routed through :class:`MatrixRunner`: this is the
    independent construction the runner is checked against, built the way
    ``tests/baseline_runs.py`` builds one.
    """
    table = gate_table()
    target = replace(scenario(scenario_id), seed=seed)
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    runner = executor(GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget)
    run = run_scenario(
        target,
        system_for(system),
        executor=runner,
        engine=engine,
        graph=graph,
    )
    reading, _grown = reading_of(
        run,
        grammar=GRAMMAR,
        table=engine.table,
        simulate=simulator(GRAMMAR),
        observations=engine.observations,
        program=runner.reference,
    )
    return dict(reading.as_payload())


class TestEveryArmOfTheMatrixIsBuildable:
    """§9 names seven arms. A factory covering six is a matrix with a hole."""

    def test_the_factory_covers_exactly_the_arms_the_cell_table_names(self) -> None:
        assert set(MATRIX_SYSTEMS) == {cell.system for cell in SPEC9_CELLS}

    def test_the_criterion_five_arm_is_covered_and_kept_separate(self) -> None:
        """B6's factory and cell set agree, and neither leaks into §9's.

        The same pin as above for the arm SPEC §12 criterion 5 names, plus the
        separation that keeps it out of §9's recorded matrix -- see
        ``CRITERION5_CELLS`` in ``environments/pointproc/matrix.py``. Both
        directions matter: a B6 with no cell is unrunnable, and a B6 inside
        ``SPEC9_CELLS`` would silently make the preregistered matrix 57 cells.
        """
        assert set(CRITERION5_SYSTEMS) == {cell.system for cell in CRITERION5_CELLS}
        assert not set(CRITERION5_SYSTEMS) & set(MATRIX_SYSTEMS)
        assert set(ALL_CELLS) == set(SPEC9_CELLS) | set(CRITERION5_CELLS)
        assert set(ALL_SYSTEMS) == set(MATRIX_SYSTEMS) | set(CRITERION5_SYSTEMS)

    @pytest.mark.parametrize("name", ("V1", "B1", "B4", "B5"))
    def test_a_conventional_arm_reports_its_own_identifier(self, name: str) -> None:
        assert system_for(name).name == name

    @pytest.mark.parametrize("name", ("V7", "V3", "V4"))
    def test_an_llm_arm_reports_its_own_identifier(self, name: str) -> None:
        # V3 and V4 come out of `memory_ablation` as a *pair*, differing only in
        # their memory representation. Selecting the wrong element of that pair
        # is invisible except here: both are Hybrid, both run, and the numbers
        # land at the other arm's address.
        built = system_for(
            name, provider=provider_factory, store=TranscriptStore(mode=RECORD)
        )
        assert built.name == name

    def test_an_unknown_arm_raises_rather_than_defaulting(self) -> None:
        with pytest.raises(SystemConfigurationError, match="B9"):
            system_for("B9")


class TestAnArmNeedingAProviderRefusesWithoutOne:
    """The blocked arms fail loudly at construction, never quietly at run time."""

    def test_llm_systems_is_exactly_the_three_proposal_arms(self) -> None:
        assert frozenset({"V7", "V3", "V4"}) == LLM_SYSTEMS

    @pytest.mark.parametrize("name", ("V7", "V3", "V4"))
    def test_an_llm_arm_without_a_provider_raises(self, name: str) -> None:
        with pytest.raises(SystemConfigurationError, match="provider"):
            system_for(name)

    @pytest.mark.parametrize("name", ("V1", "B1", "B4", "B5"))
    def test_a_conventional_arm_needs_no_provider(self, name: str) -> None:
        assert system_for(name).name == name

    def test_a_runner_with_no_provider_refuses_an_llm_cell(self) -> None:
        with pytest.raises(SystemConfigurationError, match="provider"):
            driver().execute(task("V7", "S9"))


class TestStatefulArmsAreRebuiltPerReplicate:
    """A ProposalLayer counts its calls; a scripted backend consumes its script."""

    @pytest.mark.parametrize("name", ("V7", "V3", "V4"))
    def test_an_llm_arm_is_a_fresh_object_every_time(self, name: str) -> None:
        store = TranscriptStore(mode=RECORD)
        first = system_for(name, provider=provider_factory, store=store)
        second = system_for(name, provider=provider_factory, store=store)
        assert first is not second

    def test_the_runner_builds_a_new_arm_for_every_replicate(self) -> None:
        # The factory test above checks `system_for`; this checks the *runner*,
        # which is where the claim actually bites. An arm reused across
        # replicates carries its ProposalLayer's call counter into the next one,
        # so every call after the first is written to another replicate's
        # transcript address. S9 because the arm is constructed either way and
        # the null scenario costs nothing.
        built: list[int] = []

        def counted() -> ScriptedProvider:
            built.append(1)
            return provider_factory()

        running = driver(provider=counted, store=TranscriptStore(mode=RECORD))
        running.execute(task("V7", "S9", replicate=0))
        after_first = len(built)
        running.execute(task("V7", "S9", replicate=1))
        assert after_first == 1
        assert len(built) == 2

    def test_the_beam_is_reused_because_its_fitted_table_is_expensive(self) -> None:
        # B5 holds a fit over the search table and no per-run state. Rebuilding
        # it per replicate would re-fit 1,120 times for no difference in what it
        # does, which is the opposite error to the one above.
        assert system_for("B5") is system_for("B5")


class TestAReplicateRunsAtTheSeedItIsAddressedUnder:
    """The load-bearing claim, with its own power guard.

    ``cell_key`` puts ``task.seed`` in the ledger's content address
    (``eval/matrix.py:322``), so a replicate that ran at any other seed makes the
    address a lie: two campaigns agreeing on an address while holding different
    numbers, which is the failure ``docs/DECISIONS.md`` records for the platform
    divergence and which nothing downstream can detect.

    The first assertion pins the runner to an independently built run at exactly
    ``task.seed``. The second is the guard that the first has power: if the
    payload at ``task.seed`` and at ``task.seed + 7`` were equal, the first
    assertion would hold for every seeding scheme and prove nothing. V1 on S1 is
    the cell where they are not equal -- seven of sixteen fields move.
    """

    def test_a_replicate_reproduces_an_independent_run_at_its_own_seed(self) -> None:
        subject = task("V1", "S1", replicate=1)
        assert driver().execute(subject).as_payload() == direct_payload(
            "S1", "V1", subject.seed
        )

    def test_the_comparison_above_can_tell_two_seeds_apart(self) -> None:
        subject = task("V1", "S1", replicate=1)
        wrong = Seed(int(subject.seed) + 7)
        assert direct_payload("S1", "V1", subject.seed) != direct_payload(
            "S1", "V1", wrong
        )

    def test_the_scenario_seed_is_the_scenarios_own_and_not_invented(self) -> None:
        # `scenario_seed` feeds `replicate_seeds`, which hashes it. What this
        # pins is the input: a runner deriving the campaign's seeds from
        # anywhere but the scenario table would pair arms differently.
        for target in ("S1", "S9", "S11"):
            assert scenario_seed(ScenarioId(target)) == scenario(target).seed


class TestOneTaskIsOneReading:
    """Invariant 3 at the granularity the ledger records."""

    def test_the_same_task_twice_yields_a_byte_identical_payload(self) -> None:
        subject = task("B1", "S9", replicate=1)
        first = driver().execute(subject).as_payload()
        second = driver().execute(subject).as_payload()
        assert set(first) == set(second)
        for name, value in first.items():
            assert value.hex() == second[name].hex(), name


class TestTheTableIsThreadedAcrossReplicates:
    """Measured, not inferred: a threaded second replicate simulates nothing."""

    def test_a_structure_simulated_once_is_not_simulated_again(self) -> None:
        # B5 searches outside the closed set, so the first replicate simulates
        # rows the starting table does not hold: measured at 12,000 rows and
        # 33.5s. Repeating that exact task on a threading runner costs 0 rows
        # and 0.6s; on a runner that rebuilt from `gate_table()` it costs 12,000
        # again, which is the whole of the claim and what a set comparison on
        # `table.structures` could not see.
        #
        # The count is the runner's own, not an injected simulator's. An earlier
        # version passed one in, and the invariant audit was right that the seam
        # was wider than the test needed: a caller-supplied simulator changes
        # what gets scored and persists into the shared table cache, whose key
        # does not cover it. `MatrixRunner.simulations` counts what the engine
        # and `reading_of` draw -- which is precisely the table being threaded,
        # and excludes B5's internal beam fit, which keeps its own cache and
        # never reaches the table a cell is scored against.
        shared = set(gate_table().structures)
        running = driver()
        subject = task("B5", "S1", replicate=0)

        running.execute(subject)
        first = running.simulations
        running.execute(subject)
        second = running.simulations - first

        assert first > 0, "this cell simulates nothing; the test cannot discriminate"
        assert second == 0

        # The other half of the same claim, asserted here rather than in its own
        # test because the second B5 replicate is 34s and this needs no more of
        # them. Threading is state on the *runner*: a campaign that grew
        # `gate_table()` in place would make every later suite run start from a
        # different table than the one its numbers were calibrated on.
        assert set(running.table.structures) > shared
        assert set(gate_table().structures) == shared


class TestTheRunnerDrivesTheRealMatrix:
    """End to end: cells in, ledger rows out, and the report layer consumes them."""

    def test_a_small_matrix_records_rows_the_report_layer_can_summarise(
        self, tmp_path: Path
    ) -> None:
        running = driver()
        cells = (Cell("B1", ScenarioId("S9"), 2), Cell("V1", ScenarioId("S9"), 2))
        address = running.address
        with CampaignLedger.open(tmp_path / "campaign.db") as ledger:
            outcome = run_matrix(
                cells,
                address=address,
                scenario_seed=scenario_seed,
                battery=scenario_battery,
                execute=running.execute,
                ledger=ledger,
            )
            assert (outcome.ran, outcome.skipped) == (4, 0)

            resumed = run_matrix(
                cells,
                address=address,
                scenario_seed=scenario_seed,
                battery=scenario_battery,
                execute=running.execute,
                ledger=ledger,
            )
            assert (resumed.ran, resumed.skipped) == (0, 4)

            entries = ledger.entries()

        report = summarise(
            entries,
            address=address,
            scenario_class=scenario_class_of,
            battery=scenario_battery,
            platform="Windows",
            grammar=GrammarVersion(str(AGENT_GRAMMAR.version)),
        )
        assert {(row.system, str(row.scenario)) for row in report.cells} == {
            ("B1", "S9"),
            ("V1", "S9"),
        }
        assert all(row.replicates == 2 for row in report.cells)


class TestTheTableCacheIsTheOneEveryTreeShares:
    """A worktree that resolves its own cache pays 3m11s to rebuild what exists."""

    def test_the_cache_root_is_the_main_trees_and_not_this_worktrees(self) -> None:
        # The anchor moved when this logic left tests/ for src/: `parents[1]` was
        # the repository root from `tests/slice_tables.py` and is `src/` from
        # `src/environments/pointproc/`. Wrong is silent -- every tree simply
        # goes cold -- which is why it is asserted rather than trusted.
        root = cache_root()
        assert root.name == "tables"
        assert root.parent.name == ".cache"
        assert (root.parent.parent / "pyproject.toml").is_file()
        assert ".claude" not in root.parts, "resolved to a worktree, not the main tree"

    def test_the_environment_variable_still_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCIAGENT_TABLE_CACHE", str(tmp_path))
        assert cache_root() == tmp_path.resolve()

    def test_the_tests_and_the_runner_resolve_the_same_directory(self) -> None:
        # One implementation, not two that agree today. `slice_tables.CACHE` is
        # what sixteen test modules read; `cache_root()` is what the matrix
        # runner reads. Two definitions that drifted would send the suite and the
        # campaign to different caches, and neither would say so.
        assert cache_root() == slice_tables.CACHE
