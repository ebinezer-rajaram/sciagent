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

import hashlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.eval.agency import AgencyMetrics, agency_metrics
from sciagent.eval.campaign import ScenarioRun
from sciagent.eval.scoring import (
    DIMENSION_VERSION,
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
    "battery_key",
    "cell_key",
    "held_out_battery",
    "largest_defect_mass",
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


def battery_key(battery: Sequence[ExperimentDesign]) -> str:
    """Return the address term naming a held-out battery's membership.

    Guarantees the term is a pure function of the *set* of designs: two orderings
    of one battery give one term, and two batteries of one size and different
    membership give two. The size is carried in the clear beside the digest
    because it is the figure a reader of a ledger row wants -- ``n_held_out`` is
    in the payload and this is what it should agree with -- and the digest is
    what makes the term cover membership rather than the count.

    Over each design's :meth:`~sciagent.experiments.dsl.ExperimentDesign.config`
    rather than its ``id``, and the difference is not cosmetic: that module
    documents the id as **not injective over designs**, because ``n_events`` is
    deliberately absent from it. Digesting ids would give two batteries differing
    only in run length one address, while their D2, D3 and D5 differ -- they read
    different table rows, since a diagnostic's sampling distribution depends on
    how much data it saw. That is the same stale-row failure this term exists to
    prevent, one level down. ``config`` is injective and is what the registry
    already addresses an experiment by.

    That distinction is the whole reason this is not ``str(len(battery))``. D2
    and D3 mean something different under a battery of three other designs, so a
    count-only term would let a re-scored cell land on the address of the reading
    it replaced, and :func:`run_matrix`'s ``skip_recorded`` default would report
    the stale row as the new campaign's, having executed nothing to disagree with
    it. Digested rather than spelled out for the reason
    :func:`~sciagent.experiments.dsl.render` digests a candidate set: a battery
    is unbounded in size and an address that grew with it would be unreadable in
    exactly the campaigns where reading it matters.

    The encoding is **length-framed**, not delimited, and that is the second
    thing here that is not cosmetic. This joined on ``\\x00`` between designs and
    ``\\x01`` between a design's config entries until review, on the premise that
    no config value could contain either -- and that premise is false by
    construction:
    :func:`~sciagent.experiments.dsl.operation_config` renders a
    ``CompareCandidates`` operation's candidate set as its keys joined on
    ``\\x00``, so one design's value carries the byte that separates designs.
    Demonstrated rather than argued: a two-candidate design's ``op.candidates``
    holds exactly one ``\\x00``. Under a delimited encoding a battery of designs
    ``{"P\\x00Q", "R"}`` and one of ``{"P", "Q\\x00R"}`` build one payload, so two
    memberships share an address -- the failure the paragraph above is about,
    arriving through the encoding instead of through the count. Framing each part
    with its length makes the payload decodable, and a decodable payload cannot
    be ambiguous whatever the parts contain. ``tests/acceptance/test_a27.py``
    decodes one to check that, rather than trusting this paragraph.
    """
    payload = _battery_payload(battery)
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
    return f"{len(battery)}#{digest}"


def _battery_payload(battery: Sequence[ExperimentDesign]) -> str:
    """Return the string :func:`battery_key` digests.

    Separated from the digest so a test can decode it. What makes the term
    trustworthy is that this is unambiguous, and a digest cannot be inspected
    for that -- ``tests/acceptance/test_a27.py`` decodes one with a parser it
    writes itself.
    """
    return _framed(
        sorted(
            _framed(
                part
                for name, value in sorted(design.config().items())
                for part in (name, value)
            )
            for design in battery
        )
    )


def _framed(parts: Iterable[str]) -> str:
    """Return ``parts`` concatenated so the original sequence can be recovered.

    Each part is prefixed with its length in characters and a colon, so no part's
    content can be mistaken for a boundary. The alternative -- picking a
    separator believed not to occur in the parts -- is what
    :func:`battery_key` did until a config value was found carrying it.
    """
    return "".join(f"{len(part)}:{part}" for part in parts)


def cell_key(
    task: CellTask, address: CampaignAddress, *, battery: Sequence[ExperimentDesign]
) -> ExperimentKey:
    """Return the content address one replicate of one cell is recorded at.

    Guarantees the address covers every coordinate that should determine the
    cell -- the matrix, the system, the scenario, the replicate, the partition,
    the environment, data and metric versions, the reading of §8's dimensions the
    cell was scored under, the held-out battery it was scored on, and the seed --
    and nothing that should not. Two cells share an address only if they are the
    same work.

    ``battery`` is keyword-only and **has no default**, which is the point of it.
    A default would reproduce exactly the failure the paragraph below describes:
    a caller who forgot it would not raise, the cell would be *skipped*, and its
    stale reading reported as the new campaign's. See :func:`battery_key`.

    ``dimensions`` is here because none of the version *columns* moves when a
    dimension's definition changes: they describe the environment, its data and
    its diagnostic catalogue, while D1-D6 are computed in
    :mod:`sciagent.eval.scoring` from the truth and the table. Without it,
    ``skip_recorded`` would report a stale reading as a new campaign's. See
    :data:`~sciagent.eval.scoring.DIMENSION_VERSION` for why it is not a
    ``METRIC_VERSION`` bump.

    The replicate is zero-padded so that a ledger sorted as text reads in
    replicate order, which is the order anybody inspecting one expects.
    """
    return ExperimentKey(
        env_version=address.env_version,
        config=FrozenDict[str, str](
            {
                "matrix": MATRIX_VERSION,
                "dimensions": DIMENSION_VERSION,
                "battery": battery_key(battery),
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
    battery: Callable[[ScenarioId], Sequence[ExperimentDesign]],
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

    ``battery`` returns the scenario's preregistered held-out battery, and is a
    callback for the same reason ``scenario_seed`` is: the declaration lives on
    the environment's :class:`~sciagent.eval.scenarios.Scenario` instances and
    this module may not reach them. It goes into the address rather than only
    into the reading, because D2 and D3 mean something different under a
    different battery and no version *column* moves when membership changes --
    see :func:`battery_key`. Required rather than defaulted for the reason spelt
    out at :meth:`CampaignAddress.of`: an omitted address term does not raise, it
    makes the cell *skip*.

    It is checked against what ``execute`` reports having scored on
    (:attr:`CellReading.battery`) before the row is recorded, and a disagreement
    raises. The check is not ceremony: this callback and
    :func:`reading_of`'s scenario lookup are two independent resolutions of one
    fact, and until review found it, nothing but a docstring said they agreed.

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
        # Resolved once per cell rather than once per replicate, so that every
        # replicate of a cell is addressed on one battery even if the callback
        # is not a pure function.
        declared = tuple(battery(cell.scenario))
        addressed = battery_key(declared)
        for task in tasks_of(cell, scenario_seed(cell.scenario)):
            key = cell_key(task, address, battery=declared)
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
            reading = execute(task)
            if reading.battery != addressed:
                raise MalformedDesignError(
                    f"replicate {task.name} was addressed on battery "
                    f"{addressed!r} and scored on {reading.battery!r}. The "
                    f"address is built from this call's ``battery`` callback and "
                    f"the reading from the scenario ``execute`` actually ran, so "
                    f"the two disagreeing means the callback and the scenario "
                    f"are not the same declaration. Recording it would put a "
                    f"reading of one question set at the address of another, "
                    f"which is what the battery term exists to prevent"
                )
            entries.append(ledger.append(key, reading=reading.as_payload()))
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

    **Arm-dependent, and not only in its p-value.** On S11 this fires for B1 and
    stays quiet for V1 at the same seed, because B1 holds only the null and its
    space is inadequate on eleven of twelve by construction. That is why it is
    now recorded beside :attr:`probe_inadequate` rather than as the only
    detection flag: a rate read off this alone compares two arms on an
    instrument that moves with the arm.
    """

    probe_p_value: float
    """The Stage A adequacy probe's p-value, from
    :attr:`~sciagent.eval.campaign.ScenarioRun.probe`."""

    probe_inadequate: bool
    """Whether the harness-evaluated Stage A probe judged the space inadequate.

    SPEC §12 criterion 4's observable under C1, and the same value for every arm
    on a given (scenario, seed) -- see
    :attr:`~sciagent.eval.campaign.ScenarioRun.probe`, which owns the guarantee
    and the reason the evaluation point is where it is.

    **Recorded under its own name, which is the point rather than a detail.**
    ``inadequate`` already means the whole-record check in 1,120 recorded rows
    and in :mod:`sciagent.eval.report`, which conditions its contrast on that
    literal key; the entry this gate comes from asks for both flags *"clearly
    labelled"* because the two were conflated under one name and a preregistered
    contrast was left unanswerable by it. Writing the probe into ``inadequate``
    would discharge the letter of that and reinstate the defect.
    """

    agency: AgencyMetrics
    """SPEC F10's two tiers and the fraction §12 criterion 11 asks for.

    The whole object rather than the one float, for the reason this class holds
    a :class:`~sciagent.eval.scoring.DimensionVector` rather than six floats:
    :func:`~sciagent.eval.agency.agency_metrics` is the only thing that builds
    one, it reads the graph's ``proposed_at`` rather than any account a system
    gives of itself, and a caller assembling a cell cannot supply a fraction
    without the counts it is a fraction *of*.

    Criterion 11 is *"autonomy fraction reported for every investigation"*, and
    F10 puts it "alongside every performance figure". Before this field the
    machinery was complete and had **no production caller at all** -- every call
    in the repository was in ``tests/test_agency.py`` -- so the criterion was
    unmet for the recorded campaign in the most literal way available: the number
    was never computed outside a test.
    """

    null_mass: Probability
    abstain_mass: Probability
    """§12 criterion 9's first two quantities, from the run's own
    :class:`~sciagent.core.types.Diagnosis`.

    Carried because the criterion -- *"null and abstain mass exceeding any
    single defect's mass"* -- is read off the report, and the ledger held
    neither. The :class:`~sciagent.eval.campaign.ScenarioRun` that computes them
    dies inside the environment's matrix runner, so a campaign that has finished
    cannot be asked.
    """

    max_defect_mass: float
    """The largest posterior mass on any single hypothesis holding an edit.

    Criterion 9's third quantity, and **not** :attr:`ClosedWorldScore.leading_mass`
    beside it. That one is the maximum over *every* hypothesis, the null
    included, so wherever the null leads it is :attr:`null_mass` restated and
    says nothing about the largest defect. Which is exactly S9 and S10 -- the two
    scenarios criterion 9 names -- so a payload carrying only the leader leaves
    the comparison undecidable in the one case it exists for. Measured on this
    gate's own fixture: all three arms on S9 report ``leading_mass = 1.0`` and a
    largest defect of ``0.0``.

    :func:`largest_defect_mass` owns the derivation and the partition it uses.
    """

    experiments: int
    structural_distance: float
    """Distance to the nearest structure entertained, from
    :attr:`~sciagent.eval.campaign.ScenarioRun.structural_distance`. Distinct
    from D1, which is the distance to the *leading* structure: a system can hold
    the truth and conclude something else, and reporting only one of the two
    would hide that."""

    battery: str
    """:func:`battery_key` of the battery D2, D3 and D5 were actually scored on.

    Carried so :func:`run_matrix` can check the reading against the address it
    recorded it at. The two are resolved separately and have to be: the address
    is computed *before* the cell runs, from the caller's ``battery`` callback,
    while the reading is scored afterwards from
    :attr:`~sciagent.eval.scenarios.Scenario.held_out` on the run's own scenario.
    Nothing made them agree until review pointed out that only a docstring
    claimed they did -- and a cell recorded at one battery's address while scored
    on another is the stale-reading failure :func:`battery_key` exists to
    prevent, arriving from the other side.

    Not in :meth:`as_payload`, which is a mapping of floats. Membership already
    reaches the ledger through the address; this is the check, not a second
    record of it.
    """

    def as_payload(self) -> Reading:
        """Return the reading as the ledger stores it.

        Flat and named. Booleans reach the ledger as ``1.0``/``0.0``, which is
        lossless for a boolean and keeps one payload type rather than two.

        **An absent autonomy fraction reaches it as ``nan``**, and that is the
        one crossing here that could be got wrong quietly.
        :attr:`~sciagent.eval.agency.AgencyMetrics.autonomy_fraction` is ``None``
        for a run that took no decision, deliberately: *"reporting 1.0 there
        would credit a system that did nothing with full autonomy, and would pool
        into an aggregate as though it were evidence"*. A payload holds floats
        and has no ``None``, so writing either ``0.0`` or ``1.0`` here would
        spend that whole reasoning at the ledger boundary -- one reads as a
        system that decided nothing unilaterally, the other as one that decided
        everything, and the run decided nothing at all. ``nan`` is what
        :func:`~sciagent.eval.report._summarise` already excludes from a mean and
        counts separately, exactly as it does for a D2 on a scenario declaring no
        battery, so the distinction survives into the rendered line.
        """
        fraction = self.agency.autonomy_fraction
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
            "probe_p_value": self.probe_p_value,
            "probe_inadequate": float(self.probe_inadequate),
            "experiments": float(self.experiments),
            "structural_distance": self.structural_distance,
            "autonomy_fraction": math.nan if fraction is None else fraction,
            # F10's two tiers beside the fraction taken over them. Tier 1 is
            # `experiments` above, so the denominator is recoverable from the
            # payload alone and a reader need not take the fraction on trust.
            "entertained": float(self.agency.entertained),
            "escalated": float(self.agency.escalated),
            "null_mass": float(self.null_mass),
            "abstain_mass": float(self.abstain_mass),
            "max_defect_mass": self.max_defect_mass,
        }


def largest_defect_mass(run: ScenarioRun) -> float:
    """Return the largest posterior mass on a single hypothesis holding an edit.

    Guarantees the set of hypotheses maximised over is the exact complement of
    the set :attr:`~sciagent.core.types.Diagnosis.null_mass` sums over, so §12
    criterion 9's comparison is between quantities that mean what their names
    say.

    **The two sets partition the posterior; the two numbers do not add up to
    it**, and the difference is worth stating because the first sentence invites
    the second reading. This is a ``max`` and ``null_mass`` is a ``sum``: on a
    posterior of ``{null: 0.2, hawkes: 0.4, seasonality: 0.4}`` they are ``0.2``
    and ``0.4``, totalling ``0.6``. That is the correct answer to criterion 9 --
    which asks about *any single* defect's mass and not about the defects
    collectively -- and it is the wrong answer to "how is the mass split", which
    nothing here claims to report.

    **The partition is by truthiness of the program edit, not by ``is not
    None``**, and the two are different sets here. The null carries an *empty*
    edit rather than a missing one, so ``is not None`` -- which is how
    :func:`reading_of` selects the edits it hands to
    :func:`~sciagent.eval.scoring.dimension_vector`, correctly, for a different
    question -- would count the null as a defect and make this the leader on
    every abstention scenario. :func:`~sciagent.systems.base.diagnose` computes
    ``null_mass`` as the mass where ``not engine.program_edit(h)``; this is that
    predicate negated, and nothing else.

    ``0.0`` where the run holds no defect-carrying hypothesis at all, which is
    B1's case and is the honest reading rather than a placeholder: criterion 9
    asks whether null and abstain mass exceed *any single defect's*, and over an
    empty set that is vacuously true. ``0.0`` reproduces the verdict, and a
    reader who needs to tell "no defect entertained" from "defects all at zero"
    has ``entertained`` and ``escalated`` in the same payload, whose sum is zero
    in exactly the first case.
    """
    masses = [
        float(run.diagnosis.distribution[node_id])
        for node_id in sorted(run.diagnosis.distribution)
        if run.graph.node(node_id).program_edit
    ]
    return max(masses) if masses else 0.0


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

    The held-out battery is *read off the scenario* rather than passed or
    derived. SPEC §8 measures D2 and D3 on diagnostics unused during the
    investigation, and :func:`~sciagent.eval.scoring.dimension_vector` says in as
    many words that it cannot check this because it cannot see what was run. This
    could -- the run carries its evidence index -- and until gate A27 it did,
    which made the battery a function of what the arm chose to run and graded two
    arms on one scenario against different question sets. It is now
    :func:`held_out_battery`, which is
    :attr:`~sciagent.eval.scenarios.Scenario.held_out` and nothing else. A caller
    still cannot get it wrong by supplying the wrong list, because a caller still
    does not supply one; what changed is which right answer it gets.

    ``observations`` cannot be derived the same way -- an
    :class:`~sciagent.inference.interface.Observation` carries the engine's
    template rather than the evidence index's -- so it is checked instead.
    ``_reconcile`` makes ``len(engine.observations) == run.experiments`` exact
    on any run that was scored at all, so a mismatch means the caller passed
    somebody else's engine or an empty tuple. Raising matters more here than it
    looks: D4 sums over recorded experiments, so an empty list yields a
    confident ``0.0`` rather than an error, and 1,120 cells of zero
    explanatory coverage is a plausible-looking result.

    **Raises :class:`~sciagent.core.errors.InvestigationError` as well as
    :class:`~sciagent.core.errors.MalformedDesignError`, and the second family
    arrived with the agency fields rather than with this function.**
    :func:`~sciagent.eval.agency.agency_metrics` refuses a run whose own
    proposal record claims more admissions than the graph received late
    structures -- two accounts of one run disagreeing -- and that check now runs
    at scoring time because this function calls it. The condition is a graph
    inconsistency and not a scoring fault, so it is deliberately *not* rewrapped
    as a design error: a campaign should stop on it rather than record a cell
    whose agency figures describe a run nobody can reconstruct. Worth naming
    because the cost changed even though the check did not -- what used to
    surface inside a system now aborts a matrix pass, with every cell completed
    so far already in the ledger.
    """
    if run.probe is None:
        raise MalformedDesignError(
            f"scenario {run.scenario.id!r} declares no Stage A probe, so a cell "
            f"reading for it cannot carry one. A ledger row holds floats, so "
            f"there is no way to record 'never evaluated' in the payload: it "
            f"would go in as 0.0 and summarise as a probe rate of 0.000, "
            f"indistinguishable from a probe that was evaluated and never "
            f"fired, and SPEC section 12 criterion 4 is read off exactly that "
            f"number. This refuses rather than narrowing what the matrix can "
            f"score by accident -- an environment that declares no Stage A "
            f"design cannot use it, which is a real restriction and the "
            f"deliberate one. Declare a Stage A design on the scenario, or "
            f"score this run outside the matrix"
        )
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
            probe_p_value=run.probe.p_value,
            probe_inadequate=run.probe.inadequate,
            # Criterion 11's observable, derived here for the same reason every
            # other figure is: the run has been through `run_scenario`'s
            # reconciliation, so its counts are the ones the graph's own state
            # implies rather than a system's account of itself.
            agency=agency_metrics(run),
            null_mass=run.diagnosis.null_mass,
            abstain_mass=run.diagnosis.abstain_mass,
            max_defect_mass=largest_defect_mass(run),
            experiments=run.experiments,
            structural_distance=run.structural_distance,
            battery=battery_key(held_out_battery(run)),
        ),
        grown,
    )


def held_out_battery(run: ScenarioRun) -> tuple[ExperimentDesign, ...]:
    """Return the battery this run's D2 and D3 are scored on: the scenario's own.

    Guarantees the answer is a function of
    :attr:`~sciagent.eval.scenarios.Scenario.held_out` alone, so two arms with
    different run histories on one scenario are graded on one question set --
    on D2, D3 **and D5**, all three of which read the battery. That
    is gate A27, and it is the only guarantee here worth having -- the reading
    itself is one attribute access.

    **This replaces a derivation, and the reversal is deliberate.** Until A27
    this returned every offered design the investigation did *not* run, which
    read SPEC §8's "diagnostics unused during the investigation" literally and
    made the battery a function of what the arm chose. ``docs/DECISIONS.md``
    (2026-08-17) recorded that as a closure -- ``dimension_vector`` cannot check
    the battery excludes what was run, and this function could -- and the closure
    was real. What it missed is that the property it bought is worth less than
    the one it spent: measured over the recorded matrix, ``n_held_out`` came out
    {3,2} for the V-arms, {2} for B4/B5 and 0 for B1, whose D3 was therefore
    ``nan`` on all twenty S11 rows. An instrument whose question set moves with
    the answer is not an instrument, and no cross-arm D2/D3 comparison was clean,
    including the §9 contrast the matrix exists for.

    The cost of the reversal is stated at
    :attr:`~sciagent.eval.scenarios.Scenario.held_out`: an arm that ran a battery
    design is now scored on a question it asked. Empty means the scenario
    declares no battery, and D2 and D3 are ``nan`` -- the honest reading of a
    question never asked, and the reason
    :attr:`~sciagent.eval.scoring.DimensionVector.n_held_out` is reported beside
    them rather than left implicit.

    Kept as a function rather than inlined because it is the one place the
    battery is resolved **for scoring**. There is a second resolution for
    *addressing* -- :func:`run_matrix`'s ``battery`` callback, which has to run
    before the cell does and so cannot read a scenario off a run that does not
    exist yet. The two are reconciled by
    :attr:`CellReading.battery` rather than by this sentence: an earlier version
    of it claimed there was one resolution, which review corrected, and a
    docstring is not what CLAUDE.md's second invariant asks for.
    """
    return run.scenario.held_out
