"""The suite's empirical tables: the gate's own, and the shared ones re-exported.

The slice's empirical table lives here rather than in an acceptance module
because backlog item 9 needs it too: A6-A11 calibrate the engine on it, and the
baselines of SPEC §5 are run against the same table so that no difference
between a system and a gate can come from a difference in what was simulated.

Not a ``conftest``: it is imported by name rather than injected as a fixture,
so that ``tests/conftest.py`` can stay empty of anything expensive and a
module that does not want a table never builds one.

What moved, and what did not
----------------------------

Everything about *acquiring* a table -- the cache directory, the
content-addressed filename, the build-or-load helper, the slice and search
tables and their constants -- is now
:mod:`environments.pointproc.tables` and is re-exported here unchanged. SPEC §11
item 15 forced the move: the §9 matrix is run from ``scripts/run_matrix.py``,
and a script cannot import ``tests/`` -- that import resolves under pytest's
prepend mode only because ``tests/`` has no ``__init__.py``. The alternative was
a second implementation of the cache resolution, which is exactly the failure
``docs/DECISIONS.md`` records: two ways of finding the cache, one silently
wrong, and a **3m11s** cold rebuild of a table that already existed.
``tests/test_matrix_runner.py`` asserts the two agree, so the re-export is
checked rather than assumed.

**The gate table stays here**, because it is the suite's artefact and not the
framework's: it is grown by the systems the suite runs, and nothing in ``src/``
reads it. The §9 campaign keeps its own file for the same reason in reverse --
see :data:`environments.pointproc.tables.MATRIX_TABLE`.

It is a cached artefact. Building costs minutes, and it is a pure function of
its content address, so a rebuild on every run would buy nothing --
:meth:`EmpiricalTable.load` recomputes that address from the templates offered
and refuses a file that does not match, so a stale cache cannot be read as a
fresh one.
"""

from __future__ import annotations

from environments.pointproc.tables import (
    AGENT_GRAMMAR,
    CACHE,
    CLOSED_SET,
    GRAMMAR,
    METRICS,
    REPLICATES,
    SEARCH_REPLICATES,
    SEARCH_SEED,
    STRUCTURE_NAMES,
    TABLE_SEED,
    TEMPLATES,
    cache_key,
    cache_root,
    cached_table,
    publish,
    search_table,
    slice_table,
    table_probe,
)
from sciagent.inference.empirical import EmpiricalTable

__all__ = [
    "AGENT_GRAMMAR",
    "CACHE",
    "CLOSED_SET",
    "GATE_TABLE",
    "GRAMMAR",
    "METRICS",
    "REPLICATES",
    "SEARCH_REPLICATES",
    "SEARCH_SEED",
    "STRUCTURE_NAMES",
    "TABLE_SEED",
    "TEMPLATES",
    "cache_key",
    "cache_root",
    "cached_table",
    "gate_table",
    "publish",
    "save_gate_table",
    "search_table",
    "slice_table",
    "table_probe",
]

#: Where the gate's engine table is kept. Keyed on the table's content address
#: -- which covers the templates, their discretisations, the replicate count and
#: the seed -- but *not* on which structures are in it, unlike
#: :func:`~environments.pointproc.tables.cached_table`, because this one grows: a
#: system that proposes a structure outside the closed set makes the engine
#: simulate a row for it, at the slice's full 2000 replicates, and that is the
#: single most expensive thing in the suite.
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
    f"gate-{REPLICATES}-{TABLE_SEED}-"
    f"{cache_key(table_probe(REPLICATES, TABLE_SEED))}.json"
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
    publish(table, GATE_TABLE)
