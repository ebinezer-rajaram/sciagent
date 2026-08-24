"""Acceptance test A32: a foreign REPLACE is refused.

A32 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Append-only is breached by a foreign REPLACE, and
A14 has a module-scope blind spot"*, and reads:

    ``test_a32_a_foreign_replace_is_refused`` -- ``INSERT OR REPLACE`` from a
    fresh raw connection with default pragmas fails and the row is unchanged;
    the A14 analyser finds a planted module-level violation; no module under
    ``sciagent.systems`` references the store or ledger types.

Three closures, independent of one another. What unites them is that each is a
place where a stated guarantee was stronger than its enforcement.

What was wrong
--------------

**One: the REPLACE breach.** :mod:`sciagent.registry.store`'s docstring claims
three enforcement layers, the third being *"every table carries aborting BEFORE
UPDATE and BEFORE DELETE triggers, so a connection opened by any other tool is
still refused"*, and adds that ``PRAGMA recursive_triggers`` is on because
without it ``REPLACE`` deletes the conflicting row without firing delete
triggers. Both sentences are true, and together they are not enough: the pragma
is **per connection**. A tool this package never opened -- the sqlite CLI,
another process, a raw :func:`sqlite3.connect` -- gets sqlite's default, which
is ``recursive_triggers`` *off*, and its ``INSERT OR REPLACE`` therefore deletes
the registered row without ever reaching the delete trigger. Measured before
this gate was written: a raw connection took a registered result from ``[1.0]``
to ``[999.0]``, bumping its sequence number, against a schema carrying both
triggers. That is invariant 4 failing, not a hypothetical.

The closure is a third trigger, ``BEFORE INSERT``, aborting when the row it
would evict already exists. It fires before conflict resolution is consulted, so
it does not depend on the writer's pragmas -- which is the point, since the
writer's pragmas are exactly what this package does not control.

It has to name **both** unique constraints, and the first version of it named
only the digest. `/code-review` caught that, and the hole was real: conflicting
on the ``sequence`` rowid under a decoy digest evicts the row without ever
presenting a registered digest to a digest-only guard, and a second, ordinary
insert then forges the freed address. Measured through the store's own reader
against that version -- ``ExperimentStore.get(digest).result`` went ``(1.0,)``
to ``(999.0,)``. The gate went green over a registry that could still be forged,
which is why the tests below assert the eviction and the end-to-end address
separately.

**Two: the module-scope blind spot.** A14's analyser
(``tests/acceptance/callgraph.py``) built its call graph from ``FunctionDef``
nodes only. Module-level statements run at *import* time and are as capable of
reaching a sealed partition as anything in a function body, and they were not
analysed at all. Worse than silent: a module holding only module-level code
contributed no functions, so it did not appear in the analyser's module set
either, and its surface pattern was reported as *unmatched*. A planted violation
came back ``clean=True`` with ``entry_points=()``.

Closed for modules *on the surface*, which is the scope worth stating plainly.
The analyser models calls, not imports, so a module's ``<module>`` scope is an
entry point only when that module itself matches ``AGENT_TOOL_SURFACE``.
Import-time code in a non-surface module that a surface module imports still
runs, and is still not walked. That was true before this change and remains
true; what changed is that the surface's own import-time code is no longer
invisible.

**Three: the systems boundary.** That no system holds a store handle was true
and untested. A test costs nothing while it is true, and is the only thing that
will notice when it stops being.

Why the positive controls are here
----------------------------------

Each closure is paired with a check that it has not become a blanket ban, because
all three are the kind of guard that passes trivially by refusing everything: the
trigger must still admit an unregistered digest and must not break ``append``'s
documented idempotence, and the analyser must still clear
``fixtures/clean_tool.py``, whose module scope it now reads too.
"""

from __future__ import annotations

import ast
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from callgraph import MODULE_SCOPE, analyse

from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    MetricVersion,
    Seed,
)
from sciagent.registry.ledger import CampaignLedger
from sciagent.registry.partitions import (
    AGENT_TOOL_SURFACE,
    SEALED,
    SEALED_SYMBOLS,
    DataPartition,
)
from sciagent.registry.store import ExperimentKey, ExperimentStore

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = Path(__file__).resolve().parents[2] / "src"
SYSTEMS = SOURCE / "sciagent" / "systems"

#: The two stores sharing :mod:`sciagent.registry.backing`, as
#: ``(fixture, table, payload column)``. Both closures of layer 3 have to hold
#: on both, since the point of the shared backing is that one implementation
#: covers them.
STORES: tuple[tuple[str, str, str], ...] = (
    ("registry", "experiments", "result"),
    ("ledger", "cells", "reading"),
)

#: The two modules that define a store or ledger type. Importing anything from
#: either into a system is what "holding a store handle" means.
REGISTRY_DATA_MODULES: tuple[str, ...] = (
    "sciagent.registry.store",
    "sciagent.registry.ledger",
)

#: Every dotted path through which one of those modules is reachable: itself, and
#: each of its parent packages. A parent counts because ``from sciagent.registry
#: import store`` and ``from sciagent import registry`` are language features
#: rather than re-exports -- neither names a forbidden module and both hand back
#: a live :class:`~sciagent.registry.store.ExperimentStore`. Checking the leaf
#: alone was the first draft of this test and it accepted four such spellings.
REGISTRY_REACHES: frozenset[str] = frozenset(
    {"sciagent", "sciagent.registry", *REGISTRY_DATA_MODULES}
)

#: The names those two modules export. Checked as well as the paths above,
#: because ``sciagent.registry`` re-exports every one of them, so
#: ``from sciagent.registry import ExperimentStore`` names no forbidden module.
REGISTRY_DATA_NAMES: frozenset[str] = frozenset(
    {
        "CampaignLedger",
        "ExperimentKey",
        "ExperimentRecord",
        "ExperimentStore",
        "LedgerEntry",
    }
)

#: Import spellings that reach a store or ledger type, as
#: ``(statement, importing package)``. Every one was verified to hand back a
#: live type before it was written down; the four that a leaf-only check misses
#: are marked. The importing package matters only for the relative forms, whose
#: target depends on where they are written.
REACHING_IMPORTS: tuple[tuple[str, str], ...] = (
    ("from sciagent.registry.store import ExperimentStore", "sciagent.systems"),
    ("from sciagent.registry.ledger import CampaignLedger", "sciagent.systems"),
    ("import sciagent.registry.store as st", "sciagent.systems"),
    ("from sciagent.registry import ExperimentStore as S", "sciagent.systems"),
    # The four a leaf-only check accepts.
    ("from sciagent.registry import store", "sciagent.systems"),
    ("from sciagent import registry", "sciagent.systems"),
    ("import sciagent.registry", "sciagent.systems"),
    ("from ..registry import ledger", "sciagent.systems"),
)

#: Import spellings that must stay clean. The first three are what
#: ``sciagent.systems`` actually imports from the registry today, and a check
#: that flagged them would be a ban on the registry rather than on its stores.
PERMITTED_IMPORTS: tuple[tuple[str, str], ...] = (
    ("from sciagent.registry.budget import Budget", "sciagent.systems"),
    ("from sciagent.registry.metrics import MetricRegistry", "sciagent.systems"),
    ("from sciagent.registry.partitions import DataPartition", "sciagent.systems"),
    ("from sciagent.registry import partitions", "sciagent.systems"),
    ("from ..registry.budget import Budget", "sciagent.systems"),
)


def _package_of(source: Path) -> str:
    """Return the package a module under ``src`` writes its relative imports in.

    ``a/b/c.py`` and ``a/b/__init__.py`` both anchor at ``a.b``: a module's
    relative imports resolve against its containing package, and a package's
    ``__init__`` *is* that package.
    """
    parts = list(source.relative_to(SOURCE).with_suffix("").parts)
    return ".".join(parts[:-1])


def _origin(module: str | None, level: int, package: str) -> str:
    """Return the absolute module an ``ImportFrom`` reads from.

    ``level`` is the leading-dot count: one dot anchors at ``package`` itself,
    each further dot strips one component. Resolving this is what makes
    ``from ..registry import ledger`` visible as ``sciagent.registry``.
    """
    if level == 0:
        return module or ""
    parts = package.split(".")
    anchor = ".".join(parts[: len(parts) - (level - 1)])
    return f"{anchor}.{module}" if module else anchor


def _registry_handles(source: str, *, package: str) -> list[str]:
    """Return every import in ``source`` that puts a store or ledger type in reach.

    Guarantees that a binding is judged by what it *resolves to*, not by the
    text of the module named: an import is an offence when it binds a forbidden
    module, binds a package one is reachable through, or pulls one of their
    types out of the package that re-exports it.
    """
    offences: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in REGISTRY_REACHES:
                    offences.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            origin = _origin(node.module, node.level, package)
            if origin in REGISTRY_DATA_MODULES:
                offences.append(f"{node.lineno}: from {origin}")
                continue
            for alias in node.names:
                reaches = f"{origin}.{alias.name}" in REGISTRY_REACHES
                reexported = (
                    origin in REGISTRY_REACHES and alias.name in REGISTRY_DATA_NAMES
                )
                if reaches or reexported:
                    offences.append(f"{node.lineno}: from {origin} import {alias.name}")
    return offences


def _key(seed: int) -> ExperimentKey:
    """A content address. The registry never interprets ``config``, so any
    stable mapping addresses a row."""
    return ExperimentKey(
        env_version=EnvVersion("a32-env"),
        config=FrozenDict[str, str]({"probe": "a32"}),
        data_version=DataVersion("a32-data"),
        metric_version=MetricVersion("a32-metric"),
        seed=Seed(seed),
    )


def _rows(path: Path, table: str) -> list[dict[str, object]]:
    """Every row of ``table``, read through a connection this package did not
    prepare -- so a snapshot cannot be an artefact of the store's own reads."""
    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(f"SELECT * FROM {table}")
        names = [str(column[0]) for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor]
    finally:
        connection.close()


def _write_raw(
    path: Path, table: str, row: dict[str, object], *, clause: str, rowid: bool = False
) -> None:
    """Issue ``INSERT {clause} INTO table`` from a foreign connection.

    ``sequence`` is dropped unless ``rowid`` is set, so by default sqlite assigns
    one. Keeping it is what lets a caller aim the conflict at the **rowid**
    rather than at the digest, which is a second and independent way to evict a
    registered row -- see
    :meth:`TestA32AForeignReplaceIsRefused.test_a32_a_foreign_replace_cannot_evict_by_rowid`.

    The connection comes from :mod:`sqlite3` directly rather than from
    :func:`sciagent.registry.backing.connect`, and its pragmas are left at
    sqlite's defaults: the writer this has to refuse is the one whose pragmas
    this package does not set.
    """
    payload = {
        name: value for name, value in row.items() if rowid or name != "sequence"
    }
    columns = ", ".join(payload)
    placeholders = ", ".join("?" for _ in payload)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 0, (
            "the raw connection is not at sqlite's defaults, so this is not the "
            "foreign writer the test exists for"
        )
        connection.execute(
            f"INSERT {clause} INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[Path]:
    """An on-disk registry holding exactly one row, with the store closed."""
    path = tmp_path / "registry.sqlite"
    with ExperimentStore.open(path) as store:
        store.append(_key(1), partition=DataPartition.DEV, result=(1.0,))
    yield path


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Path]:
    """An on-disk ledger holding exactly one cell, with the ledger closed."""
    path = tmp_path / "ledger.sqlite"
    with CampaignLedger.open(path) as opened:
        opened.append(_key(1), reading={"d1": 1.0})
    yield path


class TestA32AForeignReplaceIsRefused:
    """The three closures of ``docs/BACKLOG.md`` rank 13."""

    # -- one: the REPLACE breach -------------------------------------------

    @pytest.mark.parametrize("fixture,table,payload", STORES)
    def test_a32_a_foreign_replace_is_refused(
        self,
        fixture: str,
        table: str,
        payload: str,
        request: pytest.FixtureRequest,
    ) -> None:
        """The criterion itself, on both stores that share the backing."""
        path: Path = request.getfixturevalue(fixture)
        before = _rows(path, table)
        assert len(before) == 1, f"the fixture registered {len(before)} rows, not one"

        forged = dict(before[0])
        forged[payload] = "[999.0]"
        with pytest.raises(sqlite3.Error) as raised:
            _write_raw(path, table, forged, clause="OR REPLACE")

        assert "append-only" in str(raised.value).lower(), (
            f"the statement failed, but not as an append-only refusal: {raised.value}"
        )
        assert _rows(path, table) == before, "the registered row was modified"

    @pytest.mark.parametrize("fixture,table,payload", STORES)
    def test_a32_a_foreign_replace_cannot_evict_by_rowid(
        self,
        fixture: str,
        table: str,
        payload: str,
        request: pytest.FixtureRequest,
    ) -> None:
        """The other unique constraint. Guarding the digest alone was not enough.

        Every one of these tables has two unique constraints, and ``INSERT OR
        REPLACE`` evicts on either. A forger who conflicts on the ``sequence``
        rowid under a *decoy* digest never trips a digest guard, and the eviction
        frees the content address; an ordinary second insert then puts a forged
        result at it, with no statement in the pair ever presenting a registered
        digest to a digest-only ``WHEN`` clause.

        Measured against exactly that form of the trigger, through the store's
        own reader rather than through sqlite: ``ExperimentStore.get(digest)``
        went from ``(1.0,)`` to ``(999.0,)`` at an unchanged content address. The
        two statements are asserted separately below because only the first is
        the eviction -- if it is refused, the address was never freed and the
        second could not forge anything.
        """
        path: Path = request.getfixturevalue(fixture)
        before = _rows(path, table)
        decoy = dict(before[0])
        decoy["digest"] = "a32-decoy"
        decoy[payload] = "[0.0]"

        with pytest.raises(sqlite3.Error) as raised:
            _write_raw(path, table, decoy, clause="OR REPLACE", rowid=True)

        assert "append-only" in str(raised.value).lower(), (
            f"the eviction failed, but not as an append-only refusal: {raised.value}"
        )
        assert _rows(path, table) == before, "the registered row was evicted by rowid"

    def test_a32_the_content_address_survives_the_whole_forgery(
        self, registry: Path
    ) -> None:
        """The claim as a reader of the registry would experience it.

        The two tests above assert about rows and about sqlite errors. What the
        project actually guarantees is that a content address keeps returning the
        result that was registered at it, so this runs the full two-statement
        forgery -- evict by rowid, then re-insert at the freed address -- with no
        expectation that either statement raises, and asks
        :meth:`~sciagent.registry.store.ExperimentStore.get` what is there
        afterwards. Against the digest-only trigger this returned ``(999.0,)``.
        """
        digest = _key(1).digest
        row = dict(_rows(registry, "experiments")[0])

        decoy = dict(row, digest="a32-decoy", result="[0.0]")
        forged = dict(row, result="[999.0]")
        for statement, uses_rowid in ((decoy, True), (forged, False)):
            try:
                _write_raw(
                    registry,
                    "experiments",
                    statement,
                    clause="OR REPLACE",
                    rowid=uses_rowid,
                )
            except sqlite3.Error:
                continue

        with ExperimentStore.open(registry) as store:
            record = store.get(digest)
        assert record is not None, "the registered row is gone from its address"
        assert record.result == (1.0,), (
            f"the content address now returns {record.result!r}; a registered "
            f"result was overwritten by a connection this package never opened"
        )

    @pytest.mark.parametrize("fixture,table,payload", STORES)
    def test_a32_an_unregistered_digest_is_still_admitted(
        self,
        fixture: str,
        table: str,
        payload: str,
        request: pytest.FixtureRequest,
    ) -> None:
        """The trigger refuses an overwrite, not every insert.

        Without this, a ``BEFORE INSERT ... RAISE(ABORT)`` with no ``WHEN``
        clause would satisfy the criterion above while making the store
        unwritable.
        """
        path: Path = request.getfixturevalue(fixture)
        fresh = dict(_rows(path, table)[0])
        fresh["digest"] = "a32-unregistered"
        _write_raw(path, table, fresh, clause="")
        assert len(_rows(path, table)) == 2

    def test_a32_the_store_stays_idempotent(self, registry: Path) -> None:
        """``append`` still returns the existing record for an identical rerun.

        The store's documented idempotence runs through the table the new trigger
        guards, so it is the behaviour a wrong ``WHEN`` clause would break in
        production rather than in a probe.
        """
        with ExperimentStore.open(registry) as store:
            first = store.get(_key(1).digest)
            assert first is not None
            again = store.append(_key(1), partition=DataPartition.DEV, result=(1.0,))
            assert again == first
            assert store.count() == 1

    def test_a32_the_ledger_stays_idempotent(self, ledger: Path) -> None:
        """The same, for the second store sharing the backing."""
        with CampaignLedger.open(ledger) as opened:
            first = opened.get(_key(1).digest)
            assert first is not None
            assert opened.append(_key(1), reading={"d1": 1.0}) == first

    @pytest.mark.parametrize("fixture,table,payload", STORES)
    def test_a32_the_insert_trigger_is_declared_in_the_schema(
        self,
        fixture: str,
        table: str,
        payload: str,
        request: pytest.FixtureRequest,
    ) -> None:
        """Read from ``sqlite_master``, not from the constant that built it.

        A12 checks the update and delete triggers this way for the same reason:
        the assertion is about the database as it now stands, not about a string
        in the source.
        """
        path: Path = request.getfixturevalue(fixture)
        connection = sqlite3.connect(path)
        try:
            triggers = [
                str(row[0])
                for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger'"
                )
            ]
        finally:
            connection.close()
        matching = [
            sql
            for sql in triggers
            if f"BEFORE INSERT ON {table}".upper() in sql.upper()
        ]
        assert matching, f"{table} has no BEFORE INSERT trigger: {triggers}"
        assert all("RAISE(ABORT" in sql.upper() for sql in matching)
        assert all("WHEN" in sql.upper() for sql in matching), (
            f"{table}'s insert trigger is unconditional, so it bans every "
            f"append: {matching}"
        )

    # -- two: the module-scope blind spot -----------------------------------

    def test_a32_the_analyser_finds_a_module_level_violation(self) -> None:
        """Import-time sealed access is reachable, so it must be analysable."""
        analysis = analyse(
            FIXTURES,
            surface=("module_violator",),
            sealed_symbols=SEALED_SYMBOLS,
        )
        assert not analysis.clean, (
            "the analyser missed a sealed reference at module scope"
        )
        assert any(
            MODULE_SCOPE in path.reference.function for path in analysis.paths
        ), (
            f"found, but not attributed to module scope: "
            f"{[str(p) for p in analysis.paths]}"
        )
        found = {path.reference.symbol for path in analysis.paths}
        assert "TEST" in found, (
            f"the class body was not analysed; a class body executes at import "
            f"exactly as a module body does, and is not a function: {sorted(found)}"
        )

    def test_a32_a_sealed_partitions_own_value_is_a_sealed_symbol(self) -> None:
        """A partition is sealed by the string it is stored as, not only by name.

        ``SEALED_SYMBOLS`` declared ``HOLDOUT`` and ``TEST`` and neither
        ``holdout`` nor ``test``, so a comparison against the value the database
        actually holds was not a sealed reference at all. Asserted against
        :data:`~sciagent.registry.partitions.SEALED` rather than against two
        literals, so a fourth sealed partition cannot arrive half-declared.
        """
        missing = {partition.value for partition in SEALED} - set(SEALED_SYMBOLS)
        assert not missing, (
            f"partition value(s) {sorted(missing)} are sealed as enum members and "
            f"not as the strings the registry stores"
        )

    def test_a32_the_analyser_finds_a_partition_value(self) -> None:
        """And the declaration is live: the analyser reports the literal.

        Without this, the tuple above could carry the values while nothing read
        them -- the assertion would be about a constant rather than about what
        the gate can detect.
        """
        analysis = analyse(
            FIXTURES,
            surface=("module_violator",),
            sealed_symbols=SEALED_SYMBOLS,
        )
        found = {path.reference.symbol for path in analysis.paths}
        assert "holdout" in found, (
            f"the planted partition value was not reported: {sorted(found)}"
        )

    def test_a32_a_module_without_functions_matches_its_surface_pattern(self) -> None:
        """A file holding only module-level code is not invisible.

        The blind spot had two halves. A module with no ``FunctionDef``
        contributed nothing to the analyser's module set, so its surface pattern
        came back *unmatched* -- which is how a planted violation reported
        ``clean=True`` with no entry points at all.
        """
        analysis = analyse(
            FIXTURES,
            surface=("module_violator",),
            sealed_symbols=SEALED_SYMBOLS,
        )
        assert analysis.matched_patterns == ("module_violator",)
        assert not analysis.unmatched_patterns
        assert analysis.entry_points, "a module-only surface declared no entry point"

    def test_a32_module_scope_analysis_clears_the_positive_control(self) -> None:
        """Analysing module scope must not make every module a violation.

        ``clean_tool`` imports from :mod:`sciagent.registry.partitions` at module
        level, as most modules in the tree do. If that counted, the analyser would
        report the whole tree and A14 would be a checker that flags everything.
        """
        analysis = analyse(
            FIXTURES, surface=("clean_tool",), sealed_symbols=SEALED_SYMBOLS
        )
        assert analysis.clean, "\n".join(str(path) for path in analysis.paths)
        assert analysis.entry_points, (
            "the positive control matched no entry point, so it is clean by "
            "having been examined rather than by being clean"
        )

    def test_a32_the_shipped_tree_is_still_clean_at_module_scope(self) -> None:
        """A14's own assertion, under the widened analyser.

        A14 asserts this over ``src`` already; repeated here because widening the
        analyser is exactly the change that could turn that gate red, and a
        closure that breaks the gate it widens is not a closure.
        """
        analysis = analyse(
            SOURCE, surface=AGENT_TOOL_SURFACE, sealed_symbols=SEALED_SYMBOLS
        )
        assert analysis.clean, "\n".join(str(path) for path in analysis.paths)

    # -- three: the systems boundary ----------------------------------------

    def test_a32_no_system_imports_a_store_or_ledger_type(self) -> None:
        """No module under ``sciagent.systems`` holds a store handle.

        Checked over imports rather than over text, so the cross-reference in a
        docstring -- the only mention in the tree today -- is not a false
        positive, and an import inside a function body is not a false negative.

        Pinned as a whole-module ban rather than as a list of forbidden symbols.
        A system that later has a real reason to build an ``ExperimentKey`` moves
        this line with the argument attached -- the same posture the analyser's
        ``licensed`` list takes.
        """
        offences: list[str] = []
        for source in sorted(SYSTEMS.rglob("*.py")):
            if "__pycache__" in source.parts:
                continue
            for offence in _registry_handles(
                source.read_text(encoding="utf-8"), package=_package_of(source)
            ):
                offences.append(f"{source.name}:{offence}")
        assert not offences, (
            "a system holds a store or ledger handle; a result reaches a system "
            f"as a value, never as a database: {offences}"
        )

    @pytest.mark.parametrize("statement,package", REACHING_IMPORTS)
    def test_a32_every_route_to_a_store_handle_is_caught(
        self, statement: str, package: str
    ) -> None:
        """The check above sees every spelling that reaches a store, not one.

        Four of these name no forbidden module and were accepted by the first
        draft of this test, which compared against the two leaf modules only.
        ``from sciagent.registry import store`` and ``from sciagent import
        registry`` are submodule and package bindings rather than re-exports;
        ``import sciagent.registry`` runs the package ``__init__`` that re-exports
        all five types; ``from ..registry import ledger`` names neither. Each was
        confirmed to hand back a live class before it was written down.
        """
        assert _registry_handles(statement, package=package), (
            f"{statement!r} reaches a store or ledger type and was not caught"
        )

    @pytest.mark.parametrize("statement,package", PERMITTED_IMPORTS)
    def test_a32_the_boundary_check_admits_the_rest_of_the_registry(
        self, statement: str, package: str
    ) -> None:
        """The ban is on the two stores, not on ``sciagent.registry``.

        ``systems.base`` imports :class:`~sciagent.registry.budget.Budget` and
        :class:`~sciagent.registry.metrics.MetricRegistry` today, and the whole
        agent surface reads :mod:`sciagent.registry.partitions`. A check that
        flagged those would pass the criterion by banning the registry outright.
        """
        assert not _registry_handles(statement, package=package), (
            f"{statement!r} reaches no store or ledger type and was flagged"
        )

    def test_a32_the_boundary_check_runs_over_a_real_population(self) -> None:
        """The clean result above is not an empty glob.

        Without this, deleting ``sciagent/systems`` would make the criterion
        pass.
        """
        modules = [
            source
            for source in SYSTEMS.rglob("*.py")
            if "__pycache__" not in source.parts
        ]
        assert len(modules) > 1, f"the systems package matched {modules}"
        assert any(source.name == "base.py" for source in modules)
