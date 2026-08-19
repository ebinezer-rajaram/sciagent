"""Turning one cell of SPEC §9's matrix into one real investigation.

:func:`~sciagent.eval.matrix.run_matrix` takes an ``execute`` callback and says
in as many words that the callback is not the framework's to write: it "does the
environment-shaped work -- build the executor, engine and graph, run the system".
Until now nothing supplied one outside a test, so the driver, the ledger and the
D1-D6 report layer all existed with no path from a
:class:`~sciagent.eval.matrix.CellTask` to a number. This is that path.

Here rather than in ``sciagent`` for the reason
:data:`environments.pointproc.matrix.SPEC9_CELLS` is: it names ``V7`` and
``S11``, and the framework's first invariant is that ``sciagent`` never imports
an environment. What is domain-independent is the driver; what is domain-specific
is which arm runs on which world, and how.

Three things here are less obvious than they look
-------------------------------------------------

**A replicate runs at its own seed.** ``CellTask.seed`` is in the ledger's
content address (:func:`~sciagent.eval.matrix.cell_key`), so a replicate that ran
at any other seed makes the address a claim about work that was never done. The
re-seed goes through :func:`dataclasses.replace` on the scenario because that is
what both consumers read: ``run_scenario`` hands ``scenario.seed`` to the
``Investigation``, and ``_run_stage_a`` derives Stage A's draw from it.

**The table is threaded from replicate to replicate.** A structure a system
proposes costs a full 2000-replicate simulation the first time it is seen and
nothing after -- measured at 12,000 simulator calls and 33.5s for B5's first
replicate on S1, against 0 calls and 0.6s for the same task once the table holds
what it produced. Over 1,120 investigations that is the difference between a
campaign that finishes and one that does not.

**The stateful arms are rebuilt every replicate; the expensive one is not.**
``ProposalLayer`` counts its calls and a scripted backend consumes its script, so
V7, V3 and V4 reused across replicates would address every call after the first
to another replicate's transcript. B5 is the opposite case: it holds a fit over
the search table and no per-run state, so it is built once and shared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import lru_cache
from typing import Final

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from environments.pointproc.tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    search_table,
)
from sciagent.core.edits import Defect
from sciagent.core.errors import SystemConfigurationError
from sciagent.core.types import ScenarioId, Seed
from sciagent.eval.campaign import run_scenario
from sciagent.eval.matrix import CampaignAddress, CellReading, CellTask, reading_of
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.inference.interface import DiagnosticVector, ExperimentTemplate
from sciagent.registry.store import ExperimentStore
from sciagent.systems.ablation import memory_ablation
from sciagent.systems.base import ResearchSystem, null_seeded_graph
from sciagent.systems.baselines.beam_search import BeamSearch, table_fit
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly
from sciagent.systems.baselines.retrieval import Retrieval
from sciagent.systems.hybrid import Hybrid
from sciagent.systems.llm import ProposalLayer, TranscriptStore
from sciagent.systems.llm.provider import Provider

__all__ = [
    "LLM_SYSTEMS",
    "MATRIX_SYSTEMS",
    "MatrixRunner",
    "ProviderFactory",
    "scenario_battery",
    "scenario_seed",
    "system_for",
]

#: A source of fresh providers, not a provider. ``memory_ablation`` documents
#: why: a :class:`~sciagent.systems.llm.ScriptedProvider` is consumed by use, so
#: two arms sharing one instance get different payloads and the ablation measures
#: the script position rather than the memory representation.
ProviderFactory = Callable[[], Provider]

#: The arms needing the LLM proposal layer, and therefore a provider. Everything
#: else in the matrix is conventional and runs with nothing configured.
LLM_SYSTEMS: Final[frozenset[str]] = frozenset({"V7", "V3", "V4"})

#: Every arm §9 names. Pinned against
#: :data:`environments.pointproc.matrix.SPEC9_CELLS` by a test, because a factory
#: that covers six of seven is a matrix with a hole in it that reports complete.
MATRIX_SYSTEMS: Final[tuple[str, ...]] = ("V1", "V7", "B4", "B5", "B1", "V3", "V4")


@lru_cache(maxsize=1)
def _beam() -> BeamSearch:
    """Return the one B5 every cell is run with.

    Cached because the fit is over :func:`~environments.pointproc.tables.search_table`
    and holds no per-run state. Rebuilding it per replicate would re-fit it 1,120
    times for no difference in what it does.
    """
    return BeamSearch(
        AGENT_GRAMMAR,
        table_fit(search_table(), simulator(GRAMMAR)),
        width=3,
        levels=1,
    )


def system_for(
    name: str,
    *,
    provider: ProviderFactory | None = None,
    store: TranscriptStore | None = None,
) -> ResearchSystem:
    """Return the SPEC §5 system with this identifier, built to run one replicate.

    Guarantees the returned system reports ``name`` as its own identifier, so a
    reading cannot be filed at another arm's address. That matters most for V3
    and V4: :func:`~sciagent.systems.ablation.memory_ablation` returns them as a
    *pair* differing only in memory representation, both are
    :class:`~sciagent.systems.hybrid.Hybrid`, and selecting the wrong element is
    invisible in every later stage.

    Raises :class:`~sciagent.core.errors.SystemConfigurationError` for an arm in
    :data:`LLM_SYSTEMS` offered no ``provider`` or no ``store``. Refusing is the
    point: substituting an offline provider would report a scripted run as a
    model's, and skipping the cell would leave the matrix short by twenty
    replicates while reporting that it finished.

    A fresh object is returned on every call for the LLM arms, which carry
    per-call state. B5 is shared -- see :func:`_beam`.
    """
    if name == "V1":
        return BOEDOnly(closed_set())
    if name == "B1":
        return PPCOnly()
    if name == "B4":
        return Retrieval(closed_set())
    if name == "B5":
        return _beam()
    if name in LLM_SYSTEMS:
        if provider is None:
            raise SystemConfigurationError(
                f"{name!r} holds the LLM proposal layer and was offered no "
                f"provider. It is not run without one: an offline substitute "
                f"would file a scripted run under this arm's address, and "
                f"skipping the cell would leave the matrix short while "
                f"reporting that it finished"
            )
        if store is None:
            raise SystemConfigurationError(
                f"{name!r} was offered no transcript store. Every model call is "
                f"recorded, and a store invented here would be discarded when "
                f"the replicate ended, so a campaign could not be replayed"
            )
        if name == "V7":
            return Hybrid(closed_set(), ProposalLayer(provider(), AGENT_GRAMMAR, store))
        # Both arms are built and one is returned. `memory_ablation` calls the
        # factory once per arm, so the discarded one costs a provider object and
        # never a model call -- the live backends construct their client lazily.
        v3, v4 = memory_ablation(closed_set(), provider, AGENT_GRAMMAR, store)
        return v3 if name == "V3" else v4
    raise SystemConfigurationError(
        # ASCII: this reaches a Windows console through scripts/run_matrix.py,
        # where a literal section sign comes back as a replacement character.
        f"no system {name!r}; the arms of SPEC section 9 are "
        f"{', '.join(MATRIX_SYSTEMS)}"
    )


def scenario_battery(target: ScenarioId) -> tuple[ExperimentDesign, ...]:
    """Return the held-out battery a scenario's cells are addressed and scored on.

    The ``battery`` callback :func:`~sciagent.eval.matrix.run_matrix` needs, and a
    sibling of :func:`scenario_seed` for the same reason: ``sciagent`` may not
    import an environment, and the declaration lives on the
    :class:`~sciagent.eval.scenarios.Scenario`. Reading it off the scenario rather
    than deriving it here is what makes the battery a function of the scenario
    alone -- gate A27 -- so every arm on one scenario is graded on one question
    set whatever it chose to run.
    """
    return scenario(str(target)).held_out


def scenario_seed(target: ScenarioId) -> Seed:
    """Return the seed a scenario's replicates are drawn from.

    The input to :func:`~sciagent.eval.matrix.replicate_seeds`, which hashes it
    with the replicate index. Reading it off the scenario table rather than
    deriving it is what pairs the arms: every system run on a scenario sees the
    same twenty seeds, so a difference between arms is never a difference in
    which data they were shown.
    """
    return scenario(str(target)).seed


class MatrixRunner:
    """The ``execute`` callback :func:`~sciagent.eval.matrix.run_matrix` needs.

    Holds the empirical table across replicates and nothing else. One instance
    per campaign; :attr:`table` is the grown table, which a caller persists at
    the end through
    :func:`~environments.pointproc.tables.save_matrix_table`.

    Authors no number. Every figure in the returned reading comes from
    :func:`~sciagent.eval.matrix.reading_of`, which derives it from a
    :class:`~sciagent.eval.campaign.ScenarioRun` that ``run_scenario`` has
    already reconciled against the engine's own state. What this class chooses is
    which scenario, which seed and which arm -- never what any of them scored.
    """

    def __init__(
        self,
        table: EmpiricalTable,
        *,
        provider: ProviderFactory | None = None,
        store: TranscriptStore | None = None,
    ) -> None:
        self._table = table
        self._provider = provider
        self._store = store
        self._simulations = 0
        # The simulator is built here and never accepted from a caller. An
        # earlier version took one, so that a test could count calls, and the
        # invariant audit was right that the seam was wider than its purpose: a
        # doctored simulator changes what gets *scored*, not merely what gets
        # counted, and `execute` persists the grown table into a shared
        # content-addressed cache whose key covers the templates, the replicate
        # count and the seed -- but not the simulator. Poisoned rows would be
        # byte-indistinguishable from faithful ones and would be read by every
        # later campaign. Counting is what was actually wanted, so the runner
        # counts, and nothing needs to be trusted not to lie.
        self._environment_simulator = simulator(GRAMMAR)
        # No store: `CampaignAddress.of` reads the scope, the data version and
        # the partition, none of which need one. Built once and held, so every
        # cell of a campaign is addressed identically and a resumed pass finds
        # what an earlier one recorded.
        self._address = CampaignAddress.of(executor(GRAMMAR))

    def _simulate(
        self, defect: Defect, template: ExperimentTemplate, seed: Seed
    ) -> DiagnosticVector:
        """Simulate one replicate of one structure, keeping a tally."""
        self._simulations += 1
        return self._environment_simulator(defect, template, seed)

    @property
    def table(self) -> EmpiricalTable:
        """The table as the last replicate left it."""
        return self._table

    @property
    def simulations(self) -> int:
        """Rows simulated since this runner was built.

        The campaign's real cost, and the thing threading the table exists to
        keep down: a structure already in the table costs nothing to reuse and a
        new one costs a full replicate count. Counts what the engine and
        :func:`~sciagent.eval.matrix.reading_of` draw through this runner --
        which is exactly what the threaded table holds. It does **not** count
        B5's internal beam fit, which keeps its own process-lived cache and
        never touches the table a cell is scored against.
        """
        return self._simulations

    @property
    def address(self) -> CampaignAddress:
        """The address every cell of this campaign is recorded under."""
        return self._address

    def execute(self, task: CellTask) -> CellReading:
        """Run one replicate of one cell and return what it scored.

        Guarantees the investigation runs at ``task.seed`` -- the seed the ledger
        addresses the row under -- and that a structure simulated for an earlier
        replicate is not simulated again.
        """
        target = replace(scenario(str(task.cell.scenario)), seed=task.seed)
        system = system_for(
            task.cell.system, provider=self._provider, store=self._store
        )
        graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, self._table, slice_designs())
        engine = EmpiricalTableEngine(graph, self._table, simulate=self._simulate)
        run = run_scenario(
            target,
            system,
            executor=executor(
                GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget
            ),
            engine=engine,
            graph=graph,
        )
        reading, grown = reading_of(
            run,
            grammar=GRAMMAR,
            table=engine.table,
            simulate=self._simulate,
            observations=engine.observations,
        )
        self._table = grown
        return reading
