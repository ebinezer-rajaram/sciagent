"""The slice's empirical tables, shared across the suite.

The slice's empirical table lives here rather than in an acceptance module
because backlog item 9 needs it too: A6-A11 calibrate the engine on it, and the
baselines of SPEC §5 are run against the same table so that no difference
between a system and a gate can come from a difference in what was simulated.

Not a ``conftest``: it is imported by name rather than injected as a fixture,
so that ``tests/conftest.py`` can stay empty of anything expensive and a
module that does not want a table never builds one.

It is a cached artefact. Building costs minutes, and it is a pure function of
its content address, so a rebuild on every run would buy nothing --
:meth:`EmpiricalTable.load` recomputes that address from the templates offered
and refuses a file that does not match, so a stale cache cannot be read as a
fresh one.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from environments.pointproc import edit_grammar
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.grammar import agent_grammar
from environments.pointproc.outcomes import (
    ENV_VERSION,
    closed_set,
    simulator,
    slice_templates,
)
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import ExecutionError, OutOfRangeError
from sciagent.core.program import stable_key
from sciagent.core.types import ExperimentTemplateId, FrozenDict, Seed
from sciagent.inference.empirical import (
    EmpiricalTable,
    ExperimentTemplate,
    structure_key,
)
from sciagent.registry.metrics import MetricRegistry

#: The environment's grammar. Everything that *executes* uses it -- an executor
#: applies a scenario's defect, and S11's and S12's are licensed here and
#: nowhere else.
GRAMMAR = edit_grammar()

#: The grammar a system reasons in. Every hypothesis graph is built on it, so a
#: structure outside it cannot be proposed however much a system would like to:
#: :meth:`~sciagent.hypothesis.graph.HypothesisGraph.propose` validates against
#: the graph's grammar. That is what makes S11 out-of-library in fact and not
#: merely on paper, and what keeps S12's censoring nuisance unproposable. It also
#: means every prior in the suite is an *agent-grammar* code length, which is the
#: honest one: a system is charged for the structures it can express, not for the
#: ones the environment can.
AGENT_GRAMMAR = agent_grammar()

METRICS: MetricRegistry = metric_registry()
TEMPLATES = slice_templates()
CLOSED_SET = closed_set()
STRUCTURE_NAMES = tuple(sorted(CLOSED_SET))

#: Replicates behind every cell probability the slice's engine reports. At this
#: count the Krichevsky-Trofimov shrinkage is about 2% of the Monte Carlo noise
#: it is measured against, so it cannot disturb A6, and a cell of probability
#: 0.05 is resolved to about 10% relative error.
REPLICATES = 2000

#: Seed of the slice table. Fixed, so the table is a reproducible artefact.
TABLE_SEED = Seed(20260803)

#: Where built tables are kept between runs. Gitignored: derived, not authored.
CACHE = Path(__file__).resolve().parents[1] / ".cache" / "tables"


def _cache_key(probe: EmpiricalTable, *parts: str) -> str:
    """Return the file stem a cached table is stored under.

    The table's own content address covers the templates, their discretisations,
    the replicate count and the seed. It cannot cover what a design *does* --
    that is the environment's compiler, and a row simulated under one reading of
    a forced arrival's window is not a row under another. ``ENV_VERSION`` is
    therefore mixed in here, where the environment is in scope, so a change to
    the environment's semantics is a cache miss and never a stale read.
    """
    payload = "/".join((probe.version, str(ENV_VERSION), *parts))
    return f"{stable_key(payload) % (1 << 48):012x}"


def _probe(replicates: int, seed: Seed) -> EmpiricalTable:
    """Return an empty table over the slice's templates, for its content address.

    The address covers the templates, their discretisations, the replicate count
    and the seed -- everything about the *table* a cached file must agree with
    before it may be read as this one. See :func:`_cache_key` for what it does
    not cover.
    """
    return EmpiricalTable(
        templates=FrozenDict[ExperimentTemplateId, ExperimentTemplate](
            {template.id: template for template in TEMPLATES}
        ),
        counts=FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]]({}),
        replicates=replicates,
        seed=seed,
    )


def cached_table(
    defects: Sequence[Defect],
    *,
    replicates: int,
    seed: Seed,
    label: str,
    grammar: EditGrammar | None = None,
) -> EmpiricalTable:
    """Return a table over ``defects``, building and caching it if absent.

    The cache key covers the table's own content address *and* a fingerprint of
    which structures are in it, so adding a structure produces a different file
    rather than a table that silently lacks a row.
    """
    probe = _probe(replicates, seed)
    fingerprint = "-".join(sorted(structure_key(d) for d in defects))
    path = CACHE / f"{label}-{_cache_key(probe, fingerprint)}.json"
    if path.exists():
        return EmpiricalTable.load(path, TEMPLATES)
    table, _ = EmpiricalTable.build(
        defects=defects,
        templates=TEMPLATES,
        simulate=simulator(grammar if grammar is not None else GRAMMAR),
        replicates=replicates,
        seed=seed,
    )
    table.save(path)
    return table


@lru_cache(maxsize=1)
def slice_table() -> EmpiricalTable:
    """Return the slice's empirical table over the closed set."""
    return cached_table(
        [CLOSED_SET[name] for name in STRUCTURE_NAMES],
        replicates=REPLICATES,
        seed=TABLE_SEED,
        label="slice",
    )


#: Where the gate's engine table is kept. Keyed on the table's content address
#: -- which covers the templates, their discretisations, the replicate count and
#: the seed -- but *not* on which structures are in it, unlike
#: :func:`cached_table`, because this one grows: a system that proposes a
#: structure outside the closed set makes the engine simulate a row for it, at
#: the slice's full 2000 replicates, and that is the single most expensive thing
#: in the suite.
#:
#: Storing a superset is safe. A row is a pure function of ``(defect, template,
#: seed)``, so a row that is present is correct whatever else the file holds,
#: and one that is absent is filled on demand. Sharing the file across sessions
#: turns a repeated multi-minute simulation into a read.
#:
#: The address is in the *name* rather than only in the file, so that adding a
#: design -- as backlog item 11 did -- is a cache miss and not a load failure.
#: :meth:`EmpiricalTable.load` refuses a file whose address disagrees with the
#: templates it is handed, which is the right behaviour for a file that claims
#: to be this table and the wrong one for a file that is simply the previous
#: design set's.
GATE_TABLE = CACHE / (
    f"gate-{REPLICATES}-{TABLE_SEED}-{_cache_key(_probe(REPLICATES, TABLE_SEED))}.json"
)


def gate_table() -> EmpiricalTable:
    """Return the engine table the item 9 gate runs on, grown by past sessions.

    Identical to :func:`slice_table` on the closed set -- same templates, same
    replicate count, same seed, therefore the same counts -- so the gate's
    numbers are the calibrated ones and not a cheaper approximation.
    """
    if GATE_TABLE.exists():
        return EmpiricalTable.load(GATE_TABLE, TEMPLATES)
    return slice_table()


def save_gate_table(table: EmpiricalTable) -> None:
    """Persist the gate table, including any rows a system's proposals added."""
    GATE_TABLE.parent.mkdir(parents=True, exist_ok=True)
    table.save(GATE_TABLE)


#: Replicates behind B5's search estimates, and the seed they are drawn under.
#: Far below the slice table's, deliberately: a beam ranks candidates it will
#: mostly discard, and whatever survives is re-scored by the engine at full
#: precision. The seed is distinct so a search estimate is never read off the
#: draws the posterior is calibrated on.
SEARCH_REPLICATES = 25
SEARCH_SEED = Seed(20260911)


@lru_cache(maxsize=1)
def search_table() -> EmpiricalTable:
    """Return a table covering every single edit the agent grammar licenses.

    B5's search space, precomputed and cached. Built here rather than filled
    lazily inside :func:`~sciagent.systems.baselines.beam_search.table_fit`
    because the fit function's rows live only as long as the process: without
    this, every test session would re-simulate the whole candidate set, which is
    minutes, and it is a pure function of the grammar and the seed.

    Candidates the environment cannot measure are **skipped**, not fatal. An
    edit space's corners hold parameterisations that produce degenerate
    programmes -- a rate so high the run spans less than one measurement window
    -- and a search that enumerates corners finds them. They stay absent from the
    table, where ``table_fit`` scores them ``-inf`` on the same reasoning.
    """
    grammar = agent_grammar()
    candidates = [frozenset({edit}) for edit in grammar.enumerate_edits(1)]
    probe = _probe(SEARCH_REPLICATES, SEARCH_SEED)
    key = _cache_key(probe, str(grammar.version), str(len(candidates)))
    path = CACHE / f"search-{key}.json"
    if path.exists():
        return EmpiricalTable.load(path, TEMPLATES)

    table, _ = EmpiricalTable.build(
        defects=[CLOSED_SET["null"]],
        templates=TEMPLATES,
        simulate=simulator(grammar),
        replicates=SEARCH_REPLICATES,
        seed=SEARCH_SEED,
    )
    simulate = simulator(grammar)
    for defect in candidates:
        if table.holds(defect):
            continue
        try:
            table, _ = table.with_structure(defect, simulate)
        except (ExecutionError, OutOfRangeError):
            continue
    table.save(path)
    return table
