"""Driving SPEC §9's experiment matrix, cell by cell and resumably.

SPEC §11 item 15 is 56 cells at twenty seeds -- roughly 1,120 investigations and
days of compute, which is longer than any session and longer than any recording
run that has to fit inside a subscription's rate caps. A matrix that can only be
run in one sitting cannot be run, so resuming is part of the design rather than a
convenience bolted on afterwards.

Resuming means one thing here, and the fourth invariant is what decides which.
The registry is append-only: a re-run is an *append* at a new content address,
never a correction of the old row. So the driver asks *is this cell's address
already recorded* and skips it if so; it never overwrites what looks stale. A
cell that needs re-running because its inputs changed gets a new address, the old
row stays, and both are in the ledger -- which is why any report over a matrix
must select rows by address rather than assume one row per cell.

Domain-independent, like everything in ``sciagent``. This module names no
scenario and no system: :class:`Cell` carries a system identifier as text,
``scenario_seed`` and ``execute`` are callbacks, and SPEC §9's actual table lives
beside the environment it is a matrix over. The first invariant is what forces
that, and it is worth stating because the alternative reads so naturally --
hard-coding ``"V7"`` and ``"S11"`` here would put the slice inside the framework.

Seeds
-----

Twenty seeds per cell, and *which* twenty is load-bearing twice over.

**Paired across systems.** :func:`replicate_seeds` is a function of the
scenario's seed and the replicate index alone -- never of the system. SPEC §9's
preregistered contrast asks whether V7 exceeds B4 on S11, and with unpaired seeds
that comparison would be partly a comparison of worlds, at twenty draws an arm.

**Never derived from iteration order.** The third invariant routes all randomness
through explicitly passed seeded generators, and a seed taken from a loop counter
over the cell list is neither: reordering the list, or resuming a campaign that
stopped halfway, would silently change which world a cell ran in while its
address stayed the same.

The stream is also a *prefix* stream -- ``replicate_seeds(s, 5)`` is
``replicate_seeds(s, 20)[:5]`` -- so raising a campaign's replicate count reuses
the seeds already spent instead of re-drawing a disjoint set. Under the fourth
invariant a re-draw would strand every recorded cell at an address nothing asks
for again.

What is not here
----------------

Running the matrix. When it is run, it is run entirely on the project's reference
platform -- Windows -- and the report says so. ``docs/DECISIONS.md`` records a
measured Windows/Ubuntu divergence in a reported number, and the registry
content-addresses with no platform term, so a matrix built partly on each would
be internally incomparable with nothing in the registry to report it. Pinning is
the remedy the project took; it is settled, not pending.

Rendering the matrix. SPEC §8 forbids collapsing D1-D6 into one number, so the
report layer is where that prohibition either holds or quietly fails, which is
why it was built deliberately rather than improvised here. It is
:mod:`sciagent.eval.report`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import MalformedDesignError
from sciagent.core.program import stable_key
from sciagent.core.types import (
    DataVersion,
    Digest,
    EnvVersion,
    FrozenDict,
    HypothesisId,
    MetricVersion,
    ScenarioId,
    Seed,
)
from sciagent.eval.campaign import ScenarioRun
from sciagent.eval.scoring import (
    ClosedWorldScore,
    DimensionVector,
    dimension_vector,
    leading_structure,
)
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.experiments.executor import Executor
from sciagent.inference.empirical import EmpiricalTable
from sciagent.inference.interface import Observation, Simulator
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentKey

__all__ = [
    "MATRIX_VERSION",
    "CampaignAddress",
    "Cell",
    "CellReading",
    "CellTask",
    "MatrixOutcome",
    "cell_key",
    "held_out_battery",
    "reading_of",
    "replicate_seeds",
    "run_matrix",
    "tasks_of",
]

#: Which matrix a recorded cell belongs to. In every cell address, so that a
#: later matrix over the same environment cannot be mistaken for this one or
#: silently resume from its rows.
MATRIX_VERSION: Final = "spec9/1"

#: Upper bound on a derived seed, matching
#: :func:`sciagent.inference.empirical.replicate_seed`'s. Deliberately the same:
#: two seed streams with different ranges in one project is a difference nobody
#: would remember the reason for.
_SEED_MODULUS: Final = 1 << 63

#: Reading of one cell, as the ledger stores it. Named rather than positional;
#: see :mod:`sciagent.registry.ledger` for why.
Reading = Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Cell:
    """One (system, scenario) pair of the matrix, and how many seeds it gets."""

    system: str
    """SPEC §5's identifier, as text. Text and not a
    :class:`~sciagent.systems.base.ResearchSystem`, because a cell is a
    coordinate that survives in a ledger row long after the object is gone."""

    scenario: ScenarioId
    replicates: int

    def __post_init__(self) -> None:
        if self.replicates < 1:
            raise MalformedDesignError(
                f"cell {self.system}/{self.scenario} asks for "
                f"{self.replicates} replicates; a cell with no replicate is a "
                f"row in a table that nothing will ever fill"
            )
        if not self.system:
            raise MalformedDesignError(
                f"a cell on {self.scenario} names no system, so its address "
                f"would not distinguish it from any other arm"
            )


@dataclass(frozen=True, slots=True)
class CellTask:
    """One replicate of one cell: everything that identifies the work."""

    cell: Cell
    replicate: int
    seed: Seed

    @property
    def name(self) -> str:
        """Return the cell's human-readable coordinate, ``system/scenario/nn``."""
        return f"{self.cell.system}/{self.cell.scenario}/{self.replicate:02d}"


@dataclass(frozen=True, slots=True)
class CampaignAddress:
    """The versions every cell of one campaign is addressed under.

    Built once and passed down rather than assembled per cell, so a campaign
    cannot half-change version partway through and address two halves of one
    matrix differently. ``partition`` is here rather than as a ledger column
    because the pool a cell's evidence came from is part of *what determines the
    cell*: the same arm on the same scenario reads differently on DEV and on
    TEST, and two such readings must not share an address.
    """

    env_version: EnvVersion
    data_version: DataVersion
    metric_version: MetricVersion
    partition: DataPartition
    """Typed, not a bare string. A free label admits ``"dev"``/``"DEV"``/``"dev
    "`` as three addresses for one campaign, and 1,120 cells silently re-run
    into the second set."""

    @classmethod
    def of(cls, executor: Executor) -> CampaignAddress:
        """Return the address implied by the executor the cells will run under.

        **Prefer this to the constructor.** Every field is read from the object
        that will actually produce the evidence, so the address cannot describe
        a campaign other than the one being run.

        That matters most for ``metric_version``, and specifically because
        ``skip_recorded`` defaults to ``True``. A declared version that a caller
        forgets to bump after changing a metric does not raise: the cell is
        *skipped*, its stale reading is reported as the matrix's, and
        :meth:`~sciagent.registry.ledger.CampaignLedger.append`'s conflict check
        never fires because nothing was executed to disagree with it. Read from
        the registry, the version is a content hash over every registered
        ``(name, version)`` pair, so a metric change moves it whether anybody
        remembered or not.

        ``env_version`` and ``metric_version`` come off
        :meth:`~sciagent.experiments.executor.Executor.scope`, which is already
        the authoritative pair for a claim's scope.
        """
        scope = executor.scope()
        return cls(
            env_version=scope.env_version,
            data_version=executor.data_version,
            metric_version=scope.metric_version,
            partition=executor.partition,
        )

    def at(
        self,
        *,
        env_version: EnvVersion | None = None,
        data_version: DataVersion | None = None,
        metric_version: MetricVersion | None = None,
        partition: DataPartition | None = None,
    ) -> CampaignAddress:
        """Return this address with the named fields replaced.

        For stating deliberately that a version moved -- which under the fourth
        invariant is a *new* address rather than a correction of the old rows.
        Keyword-only and spelled out rather than ``**changes``, so a misspelt
        field is a type error here instead of a silently unchanged address and a
        campaign that resumes into rows it should not have.
        """
        return replace(
            self,
            env_version=self.env_version if env_version is None else env_version,
            data_version=self.data_version if data_version is None else data_version,
            metric_version=(
                self.metric_version if metric_version is None else metric_version
            ),
            partition=self.partition if partition is None else partition,
        )


@dataclass(frozen=True, slots=True)
class MatrixOutcome:
    """What one pass over the matrix did.

    Counted in **replicates**, not in cells, and the distinction is not
    pedantry: §9's matrix is 56 cells at twenty seeds, so the two differ by a
    factor of twenty and a figure labelled with the wrong one is off by that
    much. ``len(SPEC9_CELLS)`` is 56; a full pass reports 1,120 here.
    """

    ran: int
    """Replicates executed on this pass."""

    skipped: int
    """Replicates already recorded, and therefore not executed."""

    entries: tuple[LedgerEntry, ...]
    """Every replicate of the requested matrix, recorded or skipped, in visit
    order.

    Includes the skipped ones deliberately: a caller wants the matrix, not the
    increment, and reading a resumed campaign's results should not depend on
    which pass happened to run each replicate.
    """

    @property
    def replicates(self) -> int:
        """Return how many replicates the pass covered."""
        return self.ran + self.skipped


def replicate_seeds(scenario_seed: Seed, replicates: int) -> tuple[Seed, ...]:
    """Return the seeds one scenario's replicates are run under.

    Guarantees the tuple is a pure function of ``(scenario_seed, index)``: the
    same in every process and on every platform, identical for every system run
    on that scenario, and a prefix of the tuple any larger ``replicates`` would
    return. See this module's docstring for why each of the three matters.

    The namespace prefix keeps these disjoint from
    :func:`sciagent.inference.empirical.replicate_seed`'s table stream, so a
    campaign seed can never collide with one of a table's own draws.
    """
    if replicates < 0:
        raise MalformedDesignError(
            f"asked for {replicates} replicate seeds; a negative count is not a "
            f"shorter campaign, it is a mistake"
        )
    return tuple(
        Seed(stable_key(f"matrix/{int(scenario_seed)}/{index}") % _SEED_MODULUS)
        for index in range(replicates)
    )


def cell_key(task: CellTask, address: CampaignAddress) -> ExperimentKey:
    """Return the content address one replicate of one cell is recorded at.

    Guarantees the address covers every coordinate that should determine the
    cell -- the matrix, the system, the scenario, the replicate, the partition,
    the environment, data and metric versions, and the seed -- and nothing that
    should not. Two cells share an address only if they are the same work.

    The replicate is zero-padded so that a ledger sorted as text reads in
    replicate order, which is the order anybody inspecting one expects.
    """
    return ExperimentKey(
        env_version=address.env_version,
        config=FrozenDict[str, str](
            {
                "matrix": MATRIX_VERSION,
                "system": task.cell.system,
                "scenario": str(task.cell.scenario),
                "replicate": f"{task.replicate:02d}",
                "partition": address.partition.value,
            }
        ),
        data_version=address.data_version,
        metric_version=address.metric_version,
        seed=task.seed,
    )


def tasks_of(cell: Cell, scenario_seed: Seed) -> tuple[CellTask, ...]:
    """Return every replicate of one cell, in replicate order."""
    return tuple(
        CellTask(cell=cell, replicate=index, seed=seed)
        for index, seed in enumerate(replicate_seeds(scenario_seed, cell.replicates))
    )


def run_matrix(
    cells: Sequence[Cell],
    *,
    address: CampaignAddress,
    scenario_seed: Callable[[ScenarioId], Seed],
    execute: Callable[[CellTask], CellReading],
    ledger: CampaignLedger,
    skip_recorded: bool = True,
) -> MatrixOutcome:
    """Run every cell of ``cells`` that the ledger does not already hold.

    Guarantees each cell is executed at most once per pass, that a cell already
    recorded at its address is not executed at all, and that what a skipped cell
    reports is the reading the ledger holds rather than a fresh one. Resuming a
    campaign that stopped partway therefore completes it, and re-running a
    completed campaign is free.

    ``execute`` does the environment-shaped work -- build the executor, engine
    and graph, run the system -- and returns a :class:`CellReading`. It is a
    callback rather than something this module does because ``sciagent`` may not
    import an environment.

    It returns a ``CellReading`` and not a float mapping **so that a partial or
    invented payload cannot be authored**. An earlier version took
    ``Mapping[str, float]`` and relied on a docstring saying the caller should not
    write one, which is the shape CLAUDE.md's second invariant explicitly rejects
    -- "enforce with runtime assertions, not comments". Rendering to the ledger's
    payload is :meth:`CellReading.as_payload`, which this function calls itself,
    so no caller chooses the key names either.

    **The narrowing is "only through the whole vector", not "only through**
    :func:`reading_of` **".** An earlier version of this paragraph claimed the
    latter and it is false: ``CellReading`` is a frozen dataclass with a public
    ``__init__``, and both test modules construct one directly. What is true is
    that constructing one costs a complete
    :class:`~sciagent.eval.scoring.DimensionVector` and
    :class:`~sciagent.eval.scoring.ClosedWorldScore` -- there is no way to supply
    three fields and let the rest default -- so the friction a caller hand-rolling
    a payload would meet is real, while a capability boundary is not something
    Python offers. ``execute`` is harness code in any case, never
    agent-reachable.

    ``skip_recorded=False`` executes every cell even when it is already
    recorded, which is the *verification* pass: the ledger then compares each
    fresh reading against the stored one and raises
    :class:`~sciagent.core.errors.RegistryConflictError` on a disagreement. That
    is invariant 3 audited at matrix scale, and it is the instrument that would
    catch the recorded Windows/Ubuntu divergence reaching a cell.

    An exception from ``execute`` propagates with every cell completed so far
    already in the ledger. That is the whole of the checkpointing: there is no
    separate progress file to fall out of step with what was actually recorded.
    """
    ran = 0
    skipped = 0
    entries: list[LedgerEntry] = []
    seen: dict[Digest, str] = {}
    for cell in cells:
        for task in tasks_of(cell, scenario_seed(cell.scenario)):
            key = cell_key(task, address)
            if key.digest in seen:
                raise MalformedDesignError(
                    f"replicate {task.name} has the same address as "
                    f"{seen[key.digest]}; a matrix that lists one "
                    f"(system, scenario) twice would execute it twice under "
                    f"skip_recorded=False and count the second as skipped "
                    f"under skip_recorded=True, so neither the tally nor the "
                    f"'executed at most once' guarantee would hold"
                )
            seen[key.digest] = task.name
            recorded = ledger.get(key.digest) if skip_recorded else None
            if recorded is not None:
                skipped += 1
                entries.append(recorded)
                continue
            entries.append(ledger.append(key, reading=execute(task).as_payload()))
            ran += 1
    return MatrixOutcome(ran=ran, skipped=skipped, entries=tuple(entries))


# --------------------------------------------------------------------------
# Scoring one cell
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CellReading:
    """Every number one replicate of one cell contributes to SPEC §9's report.

    Deliberately not a score. SPEC §8 forbids collapsing D1-D6 into one number,
    so this carries the six separately and offers no total, no mean and no rank
    -- exactly as :class:`~sciagent.eval.scoring.DimensionVector` does, and for
    the same reason.
    """

    dimensions: DimensionVector
    score: ClosedWorldScore
    ppc_p_value: float
    """The whole-record posterior predictive probability of the run."""

    inadequate: bool
    """Whether that check judged the entertained space inadequate.

    Stored because SPEC §9's primary contrast is *conditional on inadequacy
    detection*, so the conditioning variable has to survive in the ledger. A
    matrix that recorded only the outcome would leave the contrast unanswerable
    without re-running every cell.
    """

    experiments: int
    structural_distance: float
    """Distance to the nearest structure entertained, from
    :attr:`~sciagent.eval.campaign.ScenarioRun.structural_distance`. Distinct
    from D1, which is the distance to the *leading* structure: a system can hold
    the truth and conclude something else, and reporting only one of the two
    would hide that."""

    def as_payload(self) -> Reading:
        """Return the reading as the ledger stores it.

        Flat and named. Booleans reach the ledger as ``1.0``/``0.0``, which is
        lossless for a boolean and keeps one payload type rather than two.
        """
        return {
            "d1_structural_distance": self.dimensions.d1_structural_distance,
            "d2_held_out_predictive": self.dimensions.d2_held_out_predictive,
            "d3_intervention_similarity": self.dimensions.d3_intervention_similarity,
            "d4_explanatory_coverage": self.dimensions.d4_explanatory_coverage,
            "d5_enabled_experiment_value": self.dimensions.d5_enabled_experiment_value,
            "d6_complexity": self.dimensions.d6_complexity,
            "n_held_out": float(self.dimensions.n_held_out),
            "truth_mass": float(self.score.truth_mass),
            "log_score": self.score.log_score,
            "leading_mass": float(self.score.leading_mass),
            "correct": float(self.score.correct),
            "identified": float(self.score.identified),
            "ppc_p_value": self.ppc_p_value,
            "inadequate": float(self.inadequate),
            "experiments": float(self.experiments),
            "structural_distance": self.structural_distance,
        }


def reading_of(
    run: ScenarioRun,
    *,
    grammar: EditGrammar,
    table: EmpiricalTable,
    simulate: Simulator,
    observations: Sequence[Observation],
) -> tuple[CellReading, EmpiricalTable]:
    """Return SPEC §8's dimensions and §9's conditioning for one run.

    Every figure is derived here, from the run and the truth, by
    :mod:`sciagent.eval.scoring`. No number reaches a cell from a system: a
    :class:`~sciagent.eval.campaign.ScenarioRun` has already been through
    ``run_scenario``'s reconciliation and audit, so its diagnosis is the one the
    engine's own state implies.

    Returns the grown table alongside the reading, because D2 and D3 need rows
    for the candidate and for the truth on every held-out design. Threading it
    from cell to cell is the difference between a campaign that simulates a
    structure once and one that simulates it 1,120 times.

    ``grammar`` must be the **environment's**, not the agent's: S11's truth is
    out of the agent's library by construction, and a distance under a grammar
    that cannot express one endpoint is undefined.

    The held-out battery is *derived* rather than passed. SPEC §8 measures D2 and
    D3 on diagnostics unused during the investigation, and
    :func:`~sciagent.eval.scoring.dimension_vector` says in as many words that it
    cannot check this because it cannot see what was run. This can: the run
    carries its evidence index, so the battery is every design the scenario
    offered that no experiment used. A caller cannot get it wrong by supplying
    the wrong list.

    ``observations`` cannot be derived the same way -- an
    :class:`~sciagent.inference.interface.Observation` carries the engine's
    template rather than the evidence index's -- so it is checked instead.
    ``_reconcile`` makes ``len(engine.observations) == run.experiments`` exact
    on any run that was scored at all, so a mismatch means the caller passed
    somebody else's engine or an empty tuple. Raising matters more here than it
    looks: D4 sums over recorded experiments, so an empty list yields a
    confident ``0.0`` rather than an error, and 1,120 cells of zero
    explanatory coverage is a plausible-looking result.
    """
    if len(observations) != run.experiments:
        raise MalformedDesignError(
            f"run of {run.system!r} on {run.scenario.id!r} charged for "
            f"{run.experiments} experiment(s) but was scored against "
            f"{len(observations)} observation(s). D4 is a sum over recorded "
            f"experiments, so a short list does not fail -- it reports "
            f"explanatory coverage of 0.0, which is a number and not a "
            f"complaint. Pass the engine's own observations"
        )
    edits: dict[HypothesisId, Defect] = {
        node_id: node.program_edit
        for node_id, node in run.graph.nodes.items()
        if node.program_edit is not None
    }
    candidate = leading_structure(run.diagnosis, edits)
    vector, grown = dimension_vector(
        candidate,
        run.scenario.truth,
        grammar=grammar,
        table=table,
        simulate=simulate,
        held_out=held_out_battery(run),
        observations=observations,
        entertained=edits,
        posterior=run.diagnosis.distribution,
    )
    return (
        CellReading(
            dimensions=vector,
            score=run.score,
            ppc_p_value=run.ppc.p_value,
            inadequate=run.ppc.inadequate,
            experiments=run.experiments,
            structural_distance=run.structural_distance,
        ),
        grown,
    )


def held_out_battery(run: ScenarioRun) -> tuple[ExperimentDesign, ...]:
    """Return the designs the scenario offered and the investigation never ran.

    What SPEC §8 means by "diagnostics unused during the investigation". Empty
    when a system ran every design the scenario offered, in which case D2 and D3
    are ``nan`` -- the honest reading of a question never asked, and the reason
    :attr:`~sciagent.eval.scoring.DimensionVector.n_held_out` is reported beside
    them rather than left implicit.

    A scenario's Stage A design is *not* excluded. It is framework apparatus the
    system neither chose nor could cite, so it was never used *by the
    investigation*, and holding it out is what keeps the battery identical
    across arms.
    """
    used = {record.template for record in run.evidence.ordered()}
    return tuple(design for design in run.scenario.designs if design.id not in used)
