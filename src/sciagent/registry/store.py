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
3. **Schema.** Every table carries aborting ``BEFORE UPDATE`` and ``BEFORE
   DELETE`` triggers, so a connection opened by any other tool is still refused.

``PRAGMA recursive_triggers`` is on. Without it SQLite's ``REPLACE`` conflict
resolution deletes the conflicting row *without* firing delete triggers, which
would leave a supported SQL statement able to overwrite a registered result.

The connection also disables sqlite3's prepared-statement cache; see
:func:`_connect` for why that is a correctness requirement and not a tuning
choice.

What is not stored
------------------

No wall-clock column. A timestamp would make two byte-identical runs produce
different rows, and reruns being identical is the whole content of A15. Ordering
comes from a monotonic sequence number instead.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
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
from sciagent.registry.partitions import (
    AGENT_REACHABLE,
    DataPartition,
    SealedAccess,
    require_sealed,
)

#: Bytes of digest. 256 bits: content addresses are compared across campaigns and
#: machines, and a collision would silently merge two different experiments.
DIGEST_BYTES: Final = 32

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

_ABORT_MESSAGE: Final = "the registry is append-only"

#: sqlite authorizer actions the store permits unconditionally. An allowlist
#: rather than a denylist: a future sqlite action that this code has never heard
#: of should be refused, not admitted by omission.
#:
#: ``SQLITE_INSERT`` is deliberately absent. It is the one action the store needs
#: but must not offer on every path: an authorizer is per *connection*, so
#: admitting it outright would let :meth:`ExperimentStore.query` -- which exists
#: to read -- write a row that never passed :meth:`ExperimentStore.append`'s
#: checks. It is granted only for the duration of an append, by
#: :attr:`ExperimentStore._appending`.
_PERMITTED_ACTIONS: Final[frozenset[int]] = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,
    }
)


def _append_only_triggers(table: str) -> tuple[str, ...]:
    """Return the aborting update and delete triggers for one table."""
    return tuple(
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} "
        f"BEFORE {event} ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{_ABORT_MESSAGE}'); END"
        for event in ("UPDATE", "DELETE")
    )


# --------------------------------------------------------------------------
# Canonical encoding
# --------------------------------------------------------------------------


def _field(name: str, value: str) -> bytes:
    """Return one length-prefixed field.

    Length-prefixing rather than delimiting is what makes the encoding
    injective: joined-with-a-separator encodings collide as soon as a value
    contains the separator, and config values are arbitrary strings.
    """
    payload = value.encode("utf-8")
    return f"{name}={len(payload)}:".encode("ascii") + payload


def _encode_mapping(name: str, mapping: Mapping[str, str]) -> bytes:
    """Return a length-prefixed encoding of a string mapping, order-independent."""
    items = sorted(mapping.items())
    chunks = [f"{name}#{len(items)}:".encode("ascii")]
    for key, value in items:
        chunks.append(_field(f"{name}.k", key))
        chunks.append(_field(f"{name}.v", value))
    return b"".join(chunks)


def _digest(payload: bytes) -> Digest:
    return Digest(hashlib.blake2b(payload, digest_size=DIGEST_BYTES).hexdigest())


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
                _field("env_version", str(self.env_version)),
                _encode_mapping("config", self.config),
                _field("data_version", str(self.data_version)),
                _field("metric_version", str(self.metric_version)),
                _field("seed", str(int(self.seed))),
            )
        )

    @property
    def digest(self) -> Digest:
        """Return the content address of this key."""
        return _digest(self.to_bytes())


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
        return _digest(b"".join(chunks))


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


def _authorizer(appending: Callable[[], bool]) -> Callable[..., int]:
    """Return the connection's authorizer. Allowlist; default deny.

    ``INSERT`` is admitted only while ``appending()`` is true, which is only
    inside :meth:`ExperimentStore.append`. Every other statement reaching the
    connection -- including one through :meth:`ExperimentStore.query` -- is
    refused, so the store's own validation cannot be routed around.
    """

    def authorize(action: int, *_: object) -> int:
        if action == sqlite3.SQLITE_INSERT:
            return sqlite3.SQLITE_OK if appending() else sqlite3.SQLITE_DENY
        return (
            sqlite3.SQLITE_OK if action in _PERMITTED_ACTIONS else sqlite3.SQLITE_DENY
        )

    return authorize


def _connect(target: Path | str) -> sqlite3.Connection:
    """Return a connection whose every statement reaches the authorizer.

    ``cached_statements=0`` is load-bearing, not a tuning knob. Python's sqlite3
    caches prepared statements by SQL text, and a cache hit **skips the
    authorizer**, which runs at prepare time. With the cache on, re-issuing the
    exact text of :meth:`ExperimentStore.append`'s own ``INSERT`` through
    :meth:`ExperimentStore.query` is authorised by the prepare that happened
    during an earlier append -- measured, not feared. Disabling the cache is what
    makes the write-scoped grant in :func:`_authorizer` actually hold.
    """
    return sqlite3.connect(target, cached_statements=0)


def _is_append_only_refusal(error: sqlite3.Error) -> bool:
    text = str(error).lower()
    return "not authorized" in text or _ABORT_MESSAGE in text


class ExperimentStore:
    """An append-only, content-addressed registry of experiment results.

    Guarantees: a registered row is never modified or removed through any path;
    a content address holds at most one result; and reads of a sealed partition
    require an explicit :class:`SealedAccess` token.
    """

    __slots__ = ("_appending", "_connection", "_path")

    def __init__(self, connection: sqlite3.Connection, path: Path | None) -> None:
        self._connection = connection
        self._path = path
        self._appending = False
        """True only inside :meth:`append`. The authorizer reads it to decide
        whether an ``INSERT`` is the store's own or somebody else's."""

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, path: Path) -> ExperimentStore:
        """Open (creating if absent) the registry stored at ``path``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls._prepare(_connect(path), path)

    @classmethod
    def in_memory(cls) -> ExperimentStore:
        """Open a registry that lives only for the lifetime of this object.

        For tests of registry behaviour itself. Anything whose results are to be
        cited must use :meth:`open`.
        """
        return cls._prepare(_connect(":memory:"), None)

    @classmethod
    def _prepare(
        cls, connection: sqlite3.Connection, path: Path | None
    ) -> ExperimentStore:
        # Schema first: the authorizer denies the statements that build it, and
        # is installed only once there is nothing left to create.
        connection.execute("PRAGMA recursive_triggers = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(_CREATE_TABLE)
        for statement in _append_only_triggers(_TABLE):
            connection.execute(statement)
        connection.commit()
        store = cls(connection, path)
        connection.set_authorizer(_authorizer(lambda: store._appending))
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
        self._appending = True
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
            self._appending = False
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
            if _is_append_only_refusal(error):
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
