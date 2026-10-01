"""Acceptance tests A12-A15 (v1 SPEC §6.3): registry and partitions.

One test per criterion, named for it. These are the contract for backlog item 4;
nothing downstream may proceed while any of them fails.

A14 needs a note. It asks for static confirmation that no call path runs from the
agent tool surface to a sealed partition, but v1 SPEC §11 item 12 is the first item
that contains an agent, so today the surface matches no module and the analysis
over ``src`` is trivially clean. A gate that passes because it examined nothing is
worse than no gate, so the criterion is discharged by three assertions together:
the surface *declaration* exists, the analyser finds the planted path in
``fixtures/holdout_violator.py``, and it clears ``fixtures/clean_tool.py``. The
first stops the declaration going missing, the second stops the analyser being a
stub, the third stops it crying wolf.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest
from callgraph import MODULE_SCOPE, analyse
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from environments.pointproc import (
    CONFOUNDED_MECHANISMS,
    edit_grammar,
    mechanism_defect,
    reference_program,
)
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.outcomes import DATA_VERSION, ENV_VERSION
from sciagent.core.edits import Defect
from sciagent.core.errors import (
    AppendOnlyViolationError,
    PartitionAccessError,
    RegistryConflictError,
)
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    MetricVersion,
    Seed,
)
from sciagent.registry.partitions import (
    AGENT_REACHABLE,
    AGENT_TOOL_SURFACE,
    SEALED,
    SEALED_SYMBOLS,
    DataPartition,
    SealedAccess,
)
from sciagent.registry.store import ExperimentKey, ExperimentRecord, ExperimentStore

CHILD = Path(__file__).parent / "registry_child.py"
FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = Path(__file__).resolve().parents[2] / "src"

#: v1 SPEC §3.2 defines ``EnvVersion`` as a content hash of code plus reference
#: programme, which arrives with ``core/environment.py``. The registry needs only
#: that the field be a stable string, so the slice composes one from the versions
#: it already declares. Since backlog item 7 the environment declares both, and
#: they are imported rather than restated here: a second definition would let the
#: address this test checks drift from the one the executor actually writes.

N_EVENTS = 256
MECHANISMS: tuple[str, ...] = ("reference", *sorted(CONFOUNDED_MECHANISMS))

#: The four metrics A15 reruns. Restricted to the diagnostics that are defined
#: for every mechanism at every seed: the run-length and phase-conditioned
#: estimators raise on a run with too few above-average windows, and a criterion
#: about reproducibility must not fail for the unrelated reason that a 256-event
#: sample was too short for one estimator.
RERUN_METRICS: tuple[str, ...] = (
    "mean_rate",
    "inter_arrival_dispersion",
    "fano_factor_w2",
    "count_autocorrelation_w2",
)


def defect_named(name: str) -> Defect:
    """Return the defect for a mechanism name, or the null defect."""
    return frozenset() if name == "reference" else mechanism_defect(name)


def experiment_key(
    *,
    mechanism: str,
    metric: str,
    seed: int,
    n_events: int = N_EVENTS,
    env_version: EnvVersion = ENV_VERSION,
    data_version: DataVersion = DATA_VERSION,
) -> ExperimentKey:
    """Return the content-addressed key for one slice experiment."""
    return ExperimentKey(
        env_version=env_version,
        config=FrozenDict[str, str](
            {"mechanism": mechanism, "metric": metric, "n_events": str(n_events)}
        ),
        data_version=data_version,
        metric_version=metric_registry().version,
        seed=Seed(seed),
    )


def run(key: ExperimentKey) -> tuple[float, ...]:
    """Execute the experiment ``key` describes, from the key alone.

    This is the function A15 rests on: nothing outside the key may influence the
    result, so a rerun is a pure function of the content address.
    """
    program = edit_grammar().apply(
        reference_program(), defect_named(key.config["mechanism"])
    )
    log = program.execute(key.seed, int(key.config["n_events"]))
    return (metric_registry().spec(key.config["metric"]).compute(log),)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ExperimentStore]:
    """An empty on-disk store, closed after the test."""
    opened = ExperimentStore.open(tmp_path / "registry.sqlite")
    try:
        yield opened
    finally:
        opened.close()


# ==========================================================================
# A12  Append-only
# ==========================================================================


class TestAppendOnly:
    """No code path updates or deletes a registered row.

    Verified by schema inspection and a fuzz test, as v1 SPEC §6.3 requires.
    """

    def test_schema_forbids_update_and_delete(self, store: ExperimentStore) -> None:
        """Every table carries aborting BEFORE UPDATE and BEFORE DELETE triggers.

        Checked against ``sqlite_master`` rather than against the constant the
        store used to build it, so the assertion is about the database as it now
        stands, not about a string in the source.
        """
        tables = {
            row[0]
            for row in store.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables, "registry declared no tables"
        triggers = {
            str(row[0]): str(row[1])
            for row in store.query(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        for table in sorted(tables):
            for event in ("UPDATE", "DELETE"):
                wanted = f"BEFORE {event} ON {table}".upper()
                matching = [sql for sql in triggers.values() if wanted in sql.upper()]
                assert matching, f"{table} has no BEFORE {event} trigger"
                assert all("RAISE(ABORT" in sql.upper() for sql in matching), (
                    f"{table}'s BEFORE {event} trigger does not abort: {matching}"
                )

    def test_public_api_has_no_mutation_path(self) -> None:
        """The store's public surface is exactly the append-only allowlist.

        Pinned as an equality rather than a subset check: a later ``delete`` or
        ``update`` method must break this test rather than slip past it.
        """
        expected = {
            "append",
            "close",
            "contains",
            "count",
            "get",
            "in_memory",
            "open",
            "path",
            "query",
            "records",
            "sealed_records",
        }
        public = {name for name in dir(ExperimentStore) if not name.startswith("_")}
        assert public == expected, f"unexpected public surface: {public ^ expected}"

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(
        statement=st.sampled_from(
            (
                "UPDATE experiments SET result = '[0.0]'",
                "UPDATE experiments SET digest = 'forged' WHERE sequence = {n}",
                "UPDATE experiments SET partition = 'dev'",
                "DELETE FROM experiments",
                "DELETE FROM experiments WHERE sequence = {n}",
                "DROP TABLE experiments",
                "ALTER TABLE experiments RENAME TO experiments_old",
                "DROP TRIGGER experiments_no_delete",
                "INSERT OR REPLACE INTO experiments "
                "(sequence, digest, partition, env_version, config, data_version, "
                "metric_version, seed, result, result_digest) "
                "VALUES ({n}, 'forged', 'dev', 'v', '{{}}', 'v', 'v', 0, '[]', 'x')",
                # A bare INSERT adds a row rather than replacing one, so it is
                # not caught by the delete trigger that stops INSERT OR REPLACE.
                # It has to be refused by the authorizer instead, and it was not:
                # the allowlist admitted INSERT outright because append() needs
                # it, which made query() -- documented read-only -- able to
                # register a row that never passed append()'s checks, carrying a
                # digest unrelated to its own content.
                "INSERT INTO experiments "
                "(digest, partition, env_version, config, data_version, "
                "metric_version, seed, result, result_digest) "
                "VALUES ('forged', 'dev', 'v', '{{}}', 'v', 'v', {n}, '[9.0]', 'x')",
                # The same write hidden behind a leading SELECT, which is what a
                # prefix check on the statement text would wave through.
                "WITH source AS (SELECT 1) INSERT INTO experiments "
                "(digest, partition, env_version, config, data_version, "
                "metric_version, seed, result, result_digest) "
                "SELECT 'forged', 'dev', 'v', '{{}}', 'v', 'v', {n}, '[9.0]', 'x' "
                "FROM source",
            )
        ),
        n=st.integers(min_value=1, max_value=4),
    )
    def test_fuzzed_mutations_are_all_refused(
        self, tmp_path_factory: pytest.TempPathFactory, statement: str, n: int
    ) -> None:
        """Every mutation attempt raises, and the row count never falls."""
        directory = tmp_path_factory.mktemp("fuzz")
        opened = ExperimentStore.open(directory / "registry.sqlite")
        try:
            for seed in range(4):
                opened.append(
                    experiment_key(
                        mechanism="reference", metric="mean_rate", seed=seed
                    ),
                    partition=DataPartition.DEV,
                    result=(float(seed),),
                )
            before = opened.count()
            with pytest.raises(AppendOnlyViolationError):
                opened.query(statement.format(n=n))
            assert opened.count() >= before
            assert opened.count() == before
        finally:
            opened.close()

    def test_conflicting_reappend_is_refused(self, store: ExperimentStore) -> None:
        """One content address, one result. A second answer is a framework bug."""
        key = experiment_key(mechanism="hawkes", metric="mean_rate", seed=1)
        store.append(key, partition=DataPartition.DEV, result=(1.0,))
        with pytest.raises(RegistryConflictError):
            store.append(key, partition=DataPartition.DEV, result=(2.0,))
        assert store.count() == 1

    def test_identical_reappend_is_idempotent(self, store: ExperimentStore) -> None:
        """Re-registering an identical result is a no-op, not an error.

        A15 reruns registered experiments; if a faithful rerun could not be
        re-registered, reproducibility checking would need a mutation path.
        """
        key = experiment_key(mechanism="hawkes", metric="mean_rate", seed=1)
        first = store.append(key, partition=DataPartition.DEV, result=(1.0,))
        second = store.append(key, partition=DataPartition.DEV, result=(1.0,))
        assert first == second
        assert store.count() == 1


# ==========================================================================
# A13  Content addressing
# ==========================================================================


class TestContentAddressing:
    """Identical (env, config, data, metric, seed) yields an identical hash."""

    def test_identical_keys_share_a_digest(self) -> None:
        left = experiment_key(mechanism="hawkes", metric="mean_rate", seed=7)
        right = experiment_key(mechanism="hawkes", metric="mean_rate", seed=7)
        assert left == right
        assert left.digest == right.digest

    @pytest.mark.parametrize(
        "field",
        ["env_version", "config", "data_version", "metric_version", "seed"],
    )
    def test_each_field_changes_the_digest(self, field: str) -> None:
        """Every one of the five fields is load-bearing.

        Parametrised per field rather than asserted in bulk so that a field
        dropped from the encoding names itself in the failure.
        """
        base = experiment_key(mechanism="hawkes", metric="mean_rate", seed=7)
        perturbed: dict[str, ExperimentKey] = {
            "env_version": replace(base, env_version=EnvVersion("pointproc/9.9.9")),
            "config": replace(
                base,
                config=FrozenDict[str, str](
                    {
                        "mechanism": "seasonality",
                        "metric": "mean_rate",
                        "n_events": "256",
                    }
                ),
            ),
            "data_version": replace(
                base, data_version=DataVersion("pointproc-slice/9.9.9")
            ),
            "metric_version": replace(
                base, metric_version=MetricVersion("metrics/9.9.9")
            ),
            "seed": replace(base, seed=Seed(8)),
        }
        assert perturbed[field].digest != base.digest

    def test_digest_ignores_config_construction_order(self) -> None:
        """The address is of the mapping, not of how it was typed out."""
        forwards = ExperimentKey(
            env_version=ENV_VERSION,
            config=FrozenDict[str, str]({"a": "1", "b": "2"}),
            data_version=DATA_VERSION,
            metric_version=MetricVersion("m/1"),
            seed=Seed(3),
        )
        backwards = replace(forwards, config=FrozenDict[str, str]({"b": "2", "a": "1"}))
        assert forwards.digest == backwards.digest

    def test_digest_resists_delimiter_collision(self) -> None:
        """Two different configs cannot be encoded to the same bytes.

        The obvious encoding, joining keys and values with a separator, collides
        as soon as a value contains the separator. Length-prefixing is what
        prevents it, and this is the test that would notice its removal.
        """
        left = replace(
            experiment_key(mechanism="a", metric="b", seed=0),
            config=FrozenDict[str, str]({"x": "1|y", "z": "2"}),
        )
        right = replace(
            experiment_key(mechanism="a", metric="b", seed=0),
            config=FrozenDict[str, str]({"x": "1", "y": "2", "z": "2"}),
        )
        assert left.digest != right.digest

    def test_digest_is_stable_across_processes(self) -> None:
        """The arm that catches a content address built on :func:`hash`.

        Three child processes run under different ``PYTHONHASHSEED`` values. An
        address derived from the interpreter's randomised hash agrees with itself
        in-process and disagrees here.
        """
        outputs = []
        for hash_seed in ("0", "1", "random"):
            environment = dict(os.environ, PYTHONHASHSEED=hash_seed)
            completed = subprocess.run(
                [sys.executable, str(CHILD)],
                capture_output=True,
                text=True,
                check=True,
                env=environment,
            )
            outputs.append((hash_seed, completed.stdout))
        reference_output = outputs[0][1]
        assert reference_output.strip(), "child process produced no digests"
        for hash_seed, output in outputs[1:]:
            assert output == reference_output, (
                f"PYTHONHASHSEED={hash_seed} produced different digests:\n"
                f"{reference_output}\nvs\n{output}"
            )

    def test_store_addresses_rows_by_digest(self, store: ExperimentStore) -> None:
        """The digest is the row's identity in the database, not just in Python."""
        key = experiment_key(mechanism="hawkes", metric="mean_rate", seed=7)
        record = store.append(key, partition=DataPartition.DEV, result=(1.5,))
        assert store.contains(key.digest)
        assert store.get(key.digest) == record


# ==========================================================================
# A14  Partition isolation
# ==========================================================================


class TestPartitionIsolation:
    """No call path runs from the agent tool surface to HOLDOUT or TEST."""

    def test_surface_declaration_is_present(self) -> None:
        """The declaration exists and the partitions are disjoint and total.

        Without this, a later refactor could empty ``AGENT_TOOL_SURFACE`` and the
        reachability test below would pass by examining nothing.
        """
        assert AGENT_TOOL_SURFACE, "the agent tool surface declaration is empty"
        assert SEALED_SYMBOLS, "no symbols are declared sealed"
        assert AGENT_REACHABLE.isdisjoint(SEALED)
        unclassified = set(DataPartition) - AGENT_REACHABLE - SEALED
        assert not unclassified, (
            f"partition(s) {unclassified} are neither agent-reachable nor sealed; "
            f"a partition with no declared side defaults to readable, which is the "
            f"wrong default"
        )
        assert DataPartition.HOLDOUT in SEALED
        assert DataPartition.TEST in SEALED

    def test_no_agent_path_reaches_a_sealed_partition(self) -> None:
        """The criterion itself, over the shipped source tree."""
        analysis = analyse(
            SOURCE, surface=AGENT_TOOL_SURFACE, sealed_symbols=SEALED_SYMBOLS
        )
        assert analysis.clean, "\n".join(str(path) for path in analysis.paths)

    def test_analyser_detects_the_negative_control(self) -> None:
        """The planted violation is found, transitively, through one hop.

        This is what stops the assertion above being a statement about an
        analyser that returns nothing whatever it is given.
        """
        analysis = analyse(
            FIXTURES,
            surface=("holdout_violator",),
            sealed_symbols=SEALED_SYMBOLS,
        )
        assert not analysis.clean, "the analyser missed the planted violation"
        assert any(
            path.entry.endswith("agent_tool") and len(path.chain) > 1
            for path in analysis.paths
        ), f"violation found, but not transitively: {[str(p) for p in analysis.paths]}"

    def test_analyser_clears_the_positive_control(self) -> None:
        """A surface module that reads DEV only is reported clean."""
        analysis = analyse(
            FIXTURES, surface=("clean_tool",), sealed_symbols=SEALED_SYMBOLS
        )
        assert analysis.clean, "\n".join(str(path) for path in analysis.paths)
        functions = [
            entry for entry in analysis.entry_points if not entry.endswith(MODULE_SCOPE)
        ]
        assert functions, (
            "the positive control matched no function. Since A32 every module "
            "contributes a <module> pseudo-function, so a bare entry_points "
            "check here can no longer fail."
        )

    @pytest.mark.parametrize(
        "partition", [member for member in DataPartition if member in SEALED]
    )
    def test_sealed_partition_is_refused_at_runtime(
        self, store: ExperimentStore, partition: DataPartition
    ) -> None:
        """Static analysis is not the only guard: the ordinary read path refuses.

        CLAUDE.md invariant 2 asks for runtime assertions rather than comments.
        Reaching sealed rows requires the explicit :class:`SealedAccess` token.
        """
        with pytest.raises(PartitionAccessError):
            store.records(partition=partition)
        assert store.sealed_records(partition, SealedAccess("A14")) == ()


# ==========================================================================
# A15  Reproducibility
# ==========================================================================


class TestReproducibility:
    """100 registered experiments rerun bit-identically. Under 100% is a bug."""

    @staticmethod
    def keys() -> tuple[ExperimentKey, ...]:
        """Return exactly 100 distinct keys: 5 mechanisms x 4 metrics x 5 seeds.

        Spanning rather than sampling: every mechanism and every rerun metric
        appears, so a mechanism whose execution had become seed-dependent could
        not hide behind a truncated list.
        """
        return tuple(
            experiment_key(mechanism=mechanism, metric=metric, seed=seed)
            for mechanism, metric, seed in product(MECHANISMS, RERUN_METRICS, range(5))
        )

    def test_the_rerun_set_is_a_hundred_distinct_experiments(self) -> None:
        """Guards the criterion's own arithmetic before it is relied upon."""
        keys = self.keys()
        assert len(keys) == 100
        assert len({key.digest for key in keys}) == 100

    @pytest.mark.slow
    def test_hundred_registered_experiments_rerun_bit_identically(
        self, store: ExperimentStore
    ) -> None:
        """Rerun from the recorded key alone and compare digests, not floats.

        Comparing ``result_digest`` rather than the values with a tolerance is
        deliberate: v1 SPEC §12 asks for bit-identity, and an approximate comparison
        would pass while the seeded generators had quietly drifted.
        """
        registered: list[ExperimentRecord] = [
            store.append(key, partition=DataPartition.DEV, result=run(key))
            for key in self.keys()
        ]
        assert len(registered) == 100

        divergent = []
        for record in registered:
            replayed = run(record.key)
            if ExperimentRecord.digest_of(replayed) != record.result_digest:
                divergent.append(record.key.digest)
        assert not divergent, (
            f"{len(divergent)}/100 experiments did not reproduce: {divergent[:5]}"
        )

    def test_rerun_is_reregisterable_without_conflict(
        self, store: ExperimentStore
    ) -> None:
        """A faithful rerun re-registers idempotently; a drifted one collides.

        The store's conflict check is therefore a second, independent detector of
        irreproducibility, and this test confirms it is wired to the same digest.
        """
        keys = self.keys()[:10]
        for key in keys:
            store.append(key, partition=DataPartition.DEV, result=run(key))
        for key in keys:
            store.append(key, partition=DataPartition.DEV, result=run(key))
        assert store.count() == len(keys)

    def test_stored_results_survive_the_round_trip(
        self, store: ExperimentStore
    ) -> None:
        """Reading a row back gives the floats that were written, exactly."""
        for key in self.keys()[:10]:
            result = run(key)
            store.append(key, partition=DataPartition.DEV, result=result)
            loaded = store.get(key.digest)
            assert loaded is not None
            assert loaded.result == result
            assert json.loads(json.dumps(list(result))) == list(result)


def test_registry_database_is_a_real_file(tmp_path: Path) -> None:
    """The store persists across handles; the gates above are not testing a dict."""
    path = tmp_path / "registry.sqlite"
    first = ExperimentStore.open(path)
    key = experiment_key(mechanism="reference", metric="mean_rate", seed=0)
    first.append(key, partition=DataPartition.DEV, result=(1.0,))
    first.close()

    assert path.exists()
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
    raw.close()

    second = ExperimentStore.open(path)
    try:
        assert second.contains(key.digest)
    finally:
        second.close()
