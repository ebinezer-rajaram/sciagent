"""The append-only, content-addressed experiment registry (SPEC §10, §6.3).

An experiment is addressed by what determines it and by nothing else:
``(env_version, config, data_version, metric_version, seed)``. Two runs sharing
that tuple must produce the same result, so the tuple's digest is the row's
identity. A second, disagreeing result at the same address is not a new row, it
is :class:`RegistryConflictError` -- evidence that something outside the address
influenced the outcome, which is a framework bug and never a finding.

Append-only, three times over
-----------------------------

The invariant that no registered row is ever updated or deleted is load-bearing
for every claim the project makes, so it is enforced at three independent levels
and acceptance test A12 checks all three:

1. **API.** No update or delete method exists. :meth:`ExperimentStore.query` runs
   ad-hoc SQL for reads, and translates a refused mutation into a typed error
   rather than passing the sqlite exception through.
2. **Connection.** An authorizer allowlist admits ``SELECT``, ``READ``,
   ``FUNCTION`` and transaction control, and denies everything else, including
   ``PRAGMA`` -- ``PRAGMA writable_schema`` would otherwise be a route to the
   schema itself. ``INSERT`` is admitted only for the duration of
   :meth:`ExperimentStore.append`, since an authorizer is per connection and a
   blanket grant would make ``query`` a write path.
3. **Schema.** Every table carries aborting ``BEFORE UPDATE``, ``BEFORE DELETE``
   and ``BEFORE INSERT`` triggers, so a connection opened by any other tool is
   still refused.

``PRAGMA recursive_triggers`` is on, because without it SQLite's ``REPLACE``
conflict resolution deletes the conflicting row *without* firing delete
triggers. That is necessary and it is **not sufficient**, and the difference
mattered: the pragma is per *connection*, so setting it here says nothing about
the writer layer 3 exists for. Measured -- a foreign connection at sqlite's
defaults, where ``recursive_triggers`` is off, took a registered result from
``[1.0]`` to ``[999.0]`` against a table carrying both of the other triggers.
What actually closes it is the third trigger, guarded on the digest already
being present, which fires before conflict resolution is consulted at all. See
:func:`~sciagent.registry.backing.append_only_triggers`.

Layers 2 and 3 live in :mod:`sciagent.registry.backing`, which
:class:`~sciagent.registry.ledger.CampaignLedger` shares, so both stores are
covered by one implementation rather than by two that happen to agree. Layer 1 is
a property of this class's own surface and stays here.

The connection also disables sqlite3's prepared-statement cache; see
:func:`~sciagent.registry.backing.connect` for why that is a correctness
requirement and not a tuning choice.

What is not stored
------------------

No wall-clock column. A timestamp would make two byte-identical runs produce
different rows, and reruns being identical is the whole content of A15. Ordering
comes from a monotonic sequence number instead.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Final

from sciagent.core.errors import (
    AppendOnlyViolationError,
    RegistryConflictError,
    RegistryError,
)
from sciagent.core.types import (
    DataVersion,
    Digest,
    EnvVersion,
    FrozenDict,
    MetricVersion,
    Seed,
)
from sciagent.registry.backing import (
    AppendGrant,
    append_only_triggers,
    authorizer,
    connect,
    digest_of_bytes,
    encode_mapping,
    field,
    is_append_only_refusal,
)
from sciagent.registry.partitions import (
    AGENT_REACHABLE,
    DataPartition,
    SealedAccess,
    require_sealed,
)

#: Prefix tying a digest to this encoding. Changing the encoding without changing
#: this string would let two incompatible schemes produce the same address.
_KEY_ENCODING: Final = b"sciagent.registry.ExperimentKey/1\x00"
_RESULT_ENCODING: Final = b"sciagent.registry.result/1\x00"

_TABLE: Final = "experiments"

_CREATE_TABLE: Final = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
    digest         TEXT    NOT NULL UNIQUE,
    partition      TEXT    NOT NULL,
    env_version    TEXT    NOT NULL,
    config         TEXT    NOT NULL,
    data_version   TEXT    NOT NULL,
    metric_version TEXT    NOT NULL,
    seed           INTEGER NOT NULL,
    result         TEXT    NOT NULL,
    result_digest  TEXT    NOT NULL
)
"""

# --------------------------------------------------------------------------
# Key and record
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentKey:
    """Everything that determines an experiment's result (SPEC §6.3 A13).

    ``config`` is a plain string mapping and the registry never interprets it.
    That is what keeps the store domain-independent: a defect, an intervention
    and a window width all reach it as text, and the registry can be reasoned
    about without knowing what an arrival process is.
    """

    env_version: EnvVersion
    config: FrozenDict[str, str]
    data_version: DataVersion
    metric_version: MetricVersion
    seed: Seed

    def to_bytes(self) -> bytes:
        """Return the canonical encoding of the key.

        Guarantees the encoding is injective -- two keys share bytes only if they
        are equal -- and independent of the order in which ``config`` was built,
        of the process, and of ``PYTHONHASHSEED``.
        """
        return b"".join(
            (
                _KEY_ENCODING,
                field("env_version", str(self.env_version)),
                encode_mapping("config", self.config),
                field("data_version", str(self.data_version)),
                field("metric_version", str(self.metric_version)),
                field("seed", str(int(self.seed))),
            )
        )

    @property
    def digest(self) -> Digest:
        """Return the content address of this key."""
        return digest_of_bytes(self.to_bytes())


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """One registered experiment: its address, its pool and its result."""

    key: ExperimentKey
    partition: DataPartition
    result: tuple[float, ...]
    sequence: int
    """Monotonic insertion order. The registry's only notion of time."""

    @property
    def digest(self) -> Digest:
        """Return the experiment's content address."""
        return self.key.digest

    @property
    def result_digest(self) -> Digest:
        """Return the bit-level digest of the result vector."""
        return self.digest_of(self.result)

    @staticmethod
    def digest_of(result: Sequence[float]) -> Digest:
        """Return the digest of a result vector, over its IEEE-754 bytes.

        Digesting the packed doubles rather than a decimal rendering is what
        makes A15's claim a claim about bits: two results that differ in the last
        place produce different digests, where a rounded comparison would not.
        """
        chunks = [_RESULT_ENCODING, struct.pack("<Q", len(result))]
        chunks.extend(struct.pack("<d", float(value)) for value in result)
        return digest_of_bytes(b"".join(chunks))


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class ExperimentStore:
    """An append-only, content-addressed registry of experiment results.

    Guarantees: a registered row is never modified or removed through any path;
    a content address holds at most one result; and reads of a sealed partition
    require an explicit :class:`SealedAccess` token.
    """

    __slots__ = ("__weakref__", "_connection", "_grant", "_path")
    """``__weakref__`` is here so the release property can be *tested* rather
    than argued: see ``test_the_authorizer_does_not_keep_the_store_alive``,
    which disables the cyclic collector and checks a dropped store is freed by
    refcounting alone. Without the slot that test cannot be written."""

    def __init__(self, connection: sqlite3.Connection, path: Path | None) -> None:
        """Wrap a prepared connection. Use :meth:`open` or :meth:`in_memory`.

        **Not the way to build a store.** This installs no schema, no triggers
        and no authorizer; those are :meth:`_prepare`'s, and a store built
        directly from a raw :func:`sqlite3.connect` would carry none of the three
        layers A12 checks. Both classmethods below go through ``_prepare``, and
        nothing in the repository calls this constructor -- it is public only
        because the classmethods have to reach it.
        """
        self._connection = connection
        self._path = path
        self._grant = AppendGrant()
        """Open only inside :meth:`append`. The authorizer reads it to decide
        whether an ``INSERT`` is the store's own or somebody else's.

        A plain flag, so the grant is open for the whole of an append rather
        than for one statement. Nothing can use it: the connection is opened
        with ``check_same_thread=True``, so a second thread reaching it is
        refused before the authorizer is consulted at all, and within one thread
        an append is synchronous and yields to no other caller. A store that
        ever wanted cross-thread use would have to make this per-statement
        first."""

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, path: Path) -> ExperimentStore:
        """Open (creating if absent) the registry stored at ``path``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls._prepare(connect(path), path)

    @classmethod
    def in_memory(cls) -> ExperimentStore:
        """Open a registry that lives only for the lifetime of this object.

        For tests of registry behaviour itself. Anything whose results are to be
        cited must use :meth:`open`.
        """
        return cls._prepare(connect(":memory:"), None)

    @classmethod
    def _prepare(
        cls, connection: sqlite3.Connection, path: Path | None
    ) -> ExperimentStore:
        # Schema first: the authorizer denies the statements that build it, and
        # is installed only once there is nothing left to create.
        connection.execute("PRAGMA recursive_triggers = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(_CREATE_TABLE)
        for statement in append_only_triggers(_TABLE):
            connection.execute(statement)
        connection.commit()
        store = cls(connection, path)
        connection.set_authorizer(authorizer(store._grant))
        return store

    def __enter__(self) -> ExperimentStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection."""
        self._connection.close()

    @property
    def path(self) -> Path | None:
        """Return the database's path, or ``None`` for an in-memory store."""
        return self._path

    # -- writing -----------------------------------------------------------

    def append(
        self,
        key: ExperimentKey,
        *,
        partition: DataPartition,
        result: Sequence[float],
    ) -> ExperimentRecord:
        """Register one experiment result and return the stored record.

        Guarantees: idempotent for an identical result at an identical address,
        so a faithful rerun can be re-registered; raises
        :class:`RegistryConflictError` if the address already holds a different
        result or a different partition. Nothing is ever overwritten.
        """
        values = tuple(float(value) for value in result)
        for value in values:
            if value != value or value in (float("inf"), float("-inf")):
                raise RegistryError(
                    f"result {values!r} contains a non-finite value; a diagnostic "
                    f"that cannot produce a number must fail, not register one"
                )

        digest = key.digest
        result_digest = ExperimentRecord.digest_of(values)
        existing = self.get(digest)
        if existing is not None:
            if existing.result_digest != result_digest:
                raise RegistryConflictError(
                    f"experiment {digest} is already registered with a different "
                    f"result ({existing.result!r} vs {values!r}); the content "
                    f"address covers everything that should determine it, so the "
                    f"experiment is not reproducible"
                )
            if existing.partition is not partition:
                raise RegistryConflictError(
                    f"experiment {digest} is already registered in partition "
                    f"{existing.partition.value!r}, not {partition.value!r}"
                )
            return existing

        # The only window in which the authorizer admits an INSERT. Reset in a
        # `finally` so a failed insert does not leave the connection writable.
        self._grant.open = True
        try:
            cursor = self._execute(
                f"INSERT INTO {_TABLE} (digest, partition, env_version, config, "
                f"data_version, metric_version, seed, result, result_digest) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(digest),
                    partition.value,
                    str(key.env_version),
                    _dump_config(key.config),
                    str(key.data_version),
                    str(key.metric_version),
                    int(key.seed),
                    _dump_result(values),
                    str(result_digest),
                ),
            )
        finally:
            self._grant.open = False
        self._connection.commit()
        sequence = cursor.lastrowid
        if sequence is None:  # pragma: no cover - sqlite always assigns one
            raise RegistryError(f"sqlite assigned no sequence number to {digest}")
        return ExperimentRecord(
            key=key, partition=partition, result=values, sequence=sequence
        )

    # -- reading -----------------------------------------------------------

    def get(self, digest: Digest) -> ExperimentRecord | None:
        """Return the record at ``digest``, or ``None`` if it is not registered.

        Reads every partition: a content address is unique across the registry,
        so refusing to answer for a sealed row would leak which sealed rows exist
        while making the conflict check in :meth:`append` unsound.
        """
        rows = self._execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE digest = ?", (str(digest),)
        ).fetchall()
        return _to_record(rows[0]) if rows else None

    def contains(self, digest: Digest) -> bool:
        """Return whether ``digest`` is registered."""
        return self.get(digest) is not None

    def count(self) -> int:
        """Return the number of registered rows."""
        row = self._execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
        return int(row[0])

    def records(
        self, *, partition: DataPartition | None = None
    ) -> tuple[ExperimentRecord, ...]:
        """Return registered records in insertion order.

        Guarantees no sealed row is ever returned. ``partition=None`` means every
        agent-reachable partition, not every partition -- an unfiltered read that
        quietly included HOLDOUT would be the exact failure A14 exists to
        prevent. Naming a sealed partition raises rather than returning nothing.
        """
        if partition is None:
            wanted = AGENT_REACHABLE
        else:
            require_sealed(partition, None)
            wanted = frozenset({partition})
        return self._select(wanted)

    def sealed_records(
        self, partition: DataPartition, access: SealedAccess
    ) -> tuple[ExperimentRecord, ...]:
        """Return records from a sealed partition, given an explicit token.

        The framework-only path to HOLDOUT and TEST. Acceptance test A14 asserts
        statically that no agent-reachable call path reaches this method.
        """
        require_sealed(partition, access)
        return self._select(frozenset({partition}))

    def query(
        self, sql: str, parameters: Sequence[object] = ()
    ) -> tuple[tuple[Any, ...], ...]:
        """Run a read-only SQL statement and return its rows.

        For inspection and for the relevance queries of SPEC §7.1. Read-only is
        enforced, not requested: the connection's authorizer admits ``INSERT``
        only while :meth:`append` is running, and refuses every other write
        action outright, so a statement reaching here can read and nothing else.
        Anything that would add to or modify registered data raises
        :class:`AppendOnlyViolationError`; this is the supported way to discover
        that, rather than a raw sqlite exception escaping the abstraction.
        """
        return tuple(self._execute(sql, parameters).fetchall())

    # -- internals ---------------------------------------------------------

    def _select(
        self, partitions: Iterable[DataPartition]
    ) -> tuple[ExperimentRecord, ...]:
        wanted = sorted(partition.value for partition in partitions)
        if not wanted:
            return ()
        placeholders = ", ".join("?" for _ in wanted)
        rows = self._execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE partition IN ({placeholders}) "
            f"ORDER BY sequence",
            wanted,
        ).fetchall()
        return tuple(_to_record(row) for row in rows)

    def _execute(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, tuple(parameters))
        except sqlite3.Error as error:
            if is_append_only_refusal(error):
                raise AppendOnlyViolationError(
                    f"refused: {sql.strip().splitlines()[0]} -- the registry is "
                    f"append-only (SPEC §6.3 A12)"
                ) from error
            raise RegistryError(f"registry query failed: {error}") from error


_COLUMNS: Final = (
    "sequence, digest, partition, env_version, config, "
    "data_version, metric_version, seed, result, result_digest"
)


def _dump_config(config: Mapping[str, str]) -> str:
    return json.dumps(dict(config), sort_keys=True, separators=(",", ":"))


def _dump_result(result: Sequence[float]) -> str:
    """Return the stored rendering of a result vector.

    Python's float repr is the shortest string that round-trips, so
    ``json.loads(json.dumps(x)) is exactly x`` for every finite double and the
    stored text is both exact and readable. The authoritative comparison is
    still :meth:`ExperimentRecord.digest_of`, over the raw bytes.
    """
    return json.dumps(list(result), separators=(",", ":"))


def _to_record(row: tuple[Any, ...]) -> ExperimentRecord:
    (
        sequence,
        _digest_text,
        partition,
        env_version,
        config,
        data_version,
        metric_version,
        seed,
        result,
        _result_digest,
    ) = row
    key = ExperimentKey(
        env_version=EnvVersion(str(env_version)),
        config=FrozenDict[str, str](
            {str(k): str(v) for k, v in json.loads(config).items()}
        ),
        data_version=DataVersion(str(data_version)),
        metric_version=MetricVersion(str(metric_version)),
        seed=Seed(int(seed)),
    )
    return ExperimentRecord(
        key=key,
        partition=DataPartition(str(partition)),
        result=tuple(float(value) for value in json.loads(result)),
        sequence=int(sequence),
    )
