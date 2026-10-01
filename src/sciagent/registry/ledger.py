"""What a campaign has already run, and what it read (v1 SPEC §11 item 15).

An experiment store answers *what did this measurement produce*. This answers
*has this cell of the matrix been run, and what were its numbers* -- the question
a driver has to ask before spending an hour re-deriving an answer it already
has. v1 SPEC §9's matrix is 56 cells at twenty seeds, longer than any session, so
resuming is not a convenience: a matrix that can only be run in one sitting
cannot be run.

Addressed by :class:`~sciagent.registry.store.ExperimentKey`, the same content
address the registry uses, so "resume" means *skip what is addressed* and never
*overwrite what looks stale* -- which is what SPEC's fourth invariant leaves as
the only available meaning. A cell whose inputs changed is a new address, the old
row stays, and both are in the ledger; any report over a matrix therefore selects
rows by address rather than assuming one row per cell.

Why this is not a second :class:`~sciagent.registry.store.ExperimentStore`
--------------------------------------------------------------------------

``ExperimentStore.append`` refuses a non-finite result, on the reasoning that a
diagnostic which cannot produce a number must fail rather than register one.
That is right for an experiment and wrong for a score:

- v1's ``DimensionVector.d2_held_out_predictive`` is
  ``-inf`` when the candidate ruled out something that happens.
- D2 and D3 are ``nan`` on an empty held-out battery, which is the honest
  reading of "the question was never asked".
- v1's ``ClosedWorldScore.log_score`` is ``-inf`` whenever
  the truth got zero mass -- B1's ordinary case, since it holds only the null.

So the two stores differ in what a valid payload *is*, and sharing one class
would mean relaxing a guard that is load-bearing on the other side of it.
Everything they do agree on -- the address, the canonical encoding, and the
three append-only enforcement layers -- lives in
:mod:`sciagent.registry.backing` and is shared rather than reimplemented.

The payload is **named**, not a positional vector. A fixed-width vector that
gains a field silently re-reads every stored row against the wrong names, and a
matrix is exactly the artefact where that would go unnoticed: the numbers are
expensive, so nobody recomputes one to check it.

Digested over ``float.hex()`` rather than over the packed double. Two ``nan``s
are then the same reading, which is what a resumed campaign needs -- ``nan !=
nan``, so a value comparison would make an honest rerun raise -- and it keeps a
platform's choice of nan payload bits out of the address. That last part is
belt-and-braces under the Windows pin rather than a live concern, but it costs
nothing and the resumed-campaign argument above carries it on its own.

What is not stored
------------------

No wall-clock column, for the reason
:mod:`sciagent.registry.store` gives: a timestamp would make two byte-identical
campaigns produce different rows. Ordering is a monotonic sequence number.

No partition column either. Which pool a cell's evidence came from belongs in
the *address*, since a cell run on DEV and the same cell run on TEST are two
different readings and must not collide; the driver puts it in ``config``. A
column would instead ask this store to reimplement A14's sealed-read boundary,
and it has no sealed-read path to protect.

**A stated limitation of that choice**, since it is the reverse of
:meth:`~sciagent.registry.store.ExperimentStore.records`, whose ``None`` means
*every agent-reachable partition* rather than *every partition*. :meth:`entries`
and :meth:`count` here return every row whatever pool it came from, so a report
that pools DEV and TEST cells is a mistake this store will not catch. That is
deliberate: filtering would mean interpreting ``config``, which is the one thing
that keeps the store domain-independent -- it holds opaque text and never asks
what a cell is. Selecting rows by partition belongs to the report layer, which
has to select by address anyway, since a re-addressed cell leaves both rows in
place. That was v1's ``eval.report.summarise``, which matched a whole
``CampaignAddress`` -- partition included -- and
refused to report over no matching row.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
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
    is_append_only_refusal,
)
from sciagent.registry.store import ExperimentKey

__all__ = ["CampaignLedger", "LedgerEntry"]

#: Prefix tying a digest to this encoding. Changing how a reading is encoded
#: without changing this string would let two incompatible schemes agree on an
#: address while disagreeing about what it holds.
_READING_ENCODING: Final = b"sciagent.registry.reading/1\x00"

_TABLE: Final = "cells"

_CREATE_TABLE: Final = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
    digest         TEXT    NOT NULL UNIQUE,
    env_version    TEXT    NOT NULL,
    config         TEXT    NOT NULL,
    data_version   TEXT    NOT NULL,
    metric_version TEXT    NOT NULL,
    seed           INTEGER NOT NULL,
    reading        TEXT    NOT NULL,
    reading_digest TEXT    NOT NULL
)
"""

_COLUMNS: Final = (
    "sequence, digest, env_version, config, data_version, metric_version, "
    "seed, reading, reading_digest"
)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One cell of a campaign: its address, and every number it contributed."""

    key: ExperimentKey
    reading: FrozenDict[str, float]
    sequence: int
    """Monotonic insertion order. The ledger's only notion of time."""

    @property
    def digest(self) -> Digest:
        """Return the cell's content address."""
        return self.key.digest

    @property
    def reading_digest(self) -> Digest:
        """Return the digest of the reading this entry holds."""
        return self.digest_of(self.reading)

    @staticmethod
    def digest_of(reading: Mapping[str, float]) -> Digest:
        """Return the digest of a named reading, over each value's ``hex()``.

        Guarantees the digest is independent of the order the mapping was built
        in, exact for every finite double, and equal for two ``nan``s. The last
        is deliberate: see this module's docstring.
        """
        return digest_of_bytes(
            _READING_ENCODING
            + encode_mapping(
                "reading", {name: float(value).hex() for name, value in reading.items()}
            )
        )


class CampaignLedger:
    """An append-only, content-addressed record of completed campaign cells.

    Guarantees: a registered cell is never modified or removed through any path;
    a content address holds at most one reading; and a reading is recovered bit
    for bit, non-finite values included.
    """

    __slots__ = ("__weakref__", "_connection", "_grant", "_path")
    """``__weakref__`` for the same reason
    :class:`~sciagent.registry.store.ExperimentStore` carries it: the property
    that the authorizer closure does not keep the store alive is testable rather
    than merely argued."""

    def __init__(self, connection: sqlite3.Connection, path: Path | None) -> None:
        """Wrap a prepared connection. Use :meth:`open` or :meth:`in_memory`.

        **Not the way to build a ledger.** This installs no schema, no triggers
        and no authorizer. Both classmethods below go through :meth:`_prepare`,
        and nothing in the repository calls this constructor -- it is public
        only because the classmethods have to reach it.
        """
        self._connection = connection
        self._path = path
        self._grant = AppendGrant()
        """Open only inside :meth:`append`. See
        :class:`~sciagent.registry.backing.AppendGrant`."""

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, path: Path) -> CampaignLedger:
        """Open (creating if absent) the ledger stored at ``path``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls._prepare(connect(path), path)

    @classmethod
    def in_memory(cls) -> CampaignLedger:
        """Open a ledger that lives only for the lifetime of this object.

        For tests of ledger behaviour itself. A campaign that is to be resumed
        must use :meth:`open`: the whole point is surviving the session.
        """
        return cls._prepare(connect(":memory:"), None)

    @classmethod
    def _prepare(
        cls, connection: sqlite3.Connection, path: Path | None
    ) -> CampaignLedger:
        # Schema first: the authorizer denies the statements that build it, and
        # is installed only once there is nothing left to create.
        connection.execute("PRAGMA recursive_triggers = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(_CREATE_TABLE)
        for statement in append_only_triggers(_TABLE):
            connection.execute(statement)
        connection.commit()
        ledger = cls(connection, path)
        connection.set_authorizer(authorizer(ledger._grant))
        return ledger

    def __enter__(self) -> CampaignLedger:
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
        """Return the ledger's path, or ``None`` for an in-memory ledger."""
        return self._path

    # -- writing -----------------------------------------------------------

    def append(
        self, key: ExperimentKey, *, reading: Mapping[str, float]
    ) -> LedgerEntry:
        """Record one cell's reading and return the stored entry.

        Guarantees: idempotent for an identical reading at an identical address,
        so a faithful rerun of a cell is re-recorded rather than refused; raises
        :class:`~sciagent.core.errors.RegistryConflictError` if the address
        already holds a different reading. Nothing is ever overwritten.

        A conflict here is invariant 3 failing at matrix scale. The address
        covers everything that should determine the cell, so a second pass
        producing different numbers means something outside the address moved.
        That is a framework bug and never a finding.
        """
        values = FrozenDict[str, float](
            {str(name): float(value) for name, value in reading.items()}
        )
        digest = key.digest
        reading_digest = LedgerEntry.digest_of(values)
        existing = self.get(digest)
        if existing is not None:
            if existing.reading_digest != reading_digest:
                raise RegistryConflictError(
                    f"cell {digest} is already recorded with a different reading "
                    f"({dict(existing.reading)!r} vs {dict(values)!r}); the content "
                    f"address covers everything that should determine the cell, so "
                    f"the campaign is not reproducible"
                )
            return existing

        # The only window in which the authorizer admits an INSERT. Reset in a
        # `finally` so a failed insert does not leave the connection writable.
        self._grant.open = True
        try:
            cursor = self._execute(
                f"INSERT INTO {_TABLE} (digest, env_version, config, data_version, "
                f"metric_version, seed, reading, reading_digest) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(digest),
                    str(key.env_version),
                    _dump_mapping(key.config),
                    str(key.data_version),
                    str(key.metric_version),
                    int(key.seed),
                    _dump_reading(values),
                    str(reading_digest),
                ),
            )
        finally:
            self._grant.open = False
        self._connection.commit()
        sequence = cursor.lastrowid
        if sequence is None:  # pragma: no cover - sqlite always assigns one
            raise RegistryError(f"sqlite assigned no sequence number to {digest}")
        return LedgerEntry(key=key, reading=values, sequence=sequence)

    # -- reading -----------------------------------------------------------

    def get(self, digest: Digest) -> LedgerEntry | None:
        """Return the entry at ``digest``, or ``None`` if it is not recorded."""
        rows = self._execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE digest = ?", (str(digest),)
        ).fetchall()
        return _to_entry(rows[0]) if rows else None

    def contains(self, digest: Digest) -> bool:
        """Return whether ``digest`` is recorded.

        The question a resuming driver asks before spending a cell.
        """
        return self.get(digest) is not None

    def count(self) -> int:
        """Return the number of recorded cells."""
        row = self._execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
        return int(row[0])

    def entries(self) -> tuple[LedgerEntry, ...]:
        """Return every recorded cell, in insertion order."""
        rows = self._execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} ORDER BY sequence"
        ).fetchall()
        return tuple(_to_entry(row) for row in rows)

    def query(
        self, sql: str, parameters: Sequence[object] = ()
    ) -> tuple[tuple[Any, ...], ...]:
        """Run a read-only SQL statement and return its rows.

        Read-only is enforced, not requested: the connection's authorizer admits
        ``INSERT`` only while :meth:`append` is running, and refuses every other
        write action outright. Anything that would add to or modify a recorded
        cell raises :class:`~sciagent.core.errors.AppendOnlyViolationError`.
        """
        return tuple(self._execute(sql, parameters).fetchall())

    # -- internals ---------------------------------------------------------

    def _execute(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, tuple(parameters))
        except sqlite3.Error as error:
            if is_append_only_refusal(error):
                raise AppendOnlyViolationError(
                    f"refused: {sql.strip().splitlines()[0]} -- the campaign ledger "
                    f"is append-only (SPEC §6.3 A12)"
                ) from error
            raise RegistryError(f"ledger query failed: {error}") from error


def _dump_mapping(mapping: Mapping[str, str]) -> str:
    return json.dumps(dict(mapping), sort_keys=True, separators=(",", ":"))


def _dump_reading(reading: Mapping[str, float]) -> str:
    """Return the stored rendering of a named reading.

    ``float.hex()`` rather than a decimal or a JSON number: it round-trips every
    finite double exactly, it renders ``inf`` and ``nan`` -- which JSON does not,
    outside a non-standard extension -- and it is ASCII, so the bytes do not
    depend on the platform that wrote them.
    """
    return json.dumps(
        {name: float(value).hex() for name, value in reading.items()},
        sort_keys=True,
        separators=(",", ":"),
    )


def _to_entry(row: tuple[Any, ...]) -> LedgerEntry:
    (
        sequence,
        _digest_text,
        env_version,
        config,
        data_version,
        metric_version,
        seed,
        reading,
        _reading_digest,
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
    return LedgerEntry(
        key=key,
        reading=FrozenDict[str, float](
            {str(k): float.fromhex(str(v)) for k, v in json.loads(reading).items()}
        ),
        sequence=int(sequence),
    )
