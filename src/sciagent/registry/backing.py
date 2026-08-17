"""The sqlite backing an append-only, content-addressed store is built on.

Extracted from :mod:`sciagent.registry.store` when a second such store appeared
(:mod:`sciagent.registry.ledger`). Nothing here is new; what is new is that one
copy of it now serves both, so acceptance test A12's three enforcement layers
cover the ledger by construction rather than by a second implementation that
happens to agree.

The three layers, and which of them lives here
----------------------------------------------

SPEC §6.3 asks that no registered row is ever updated or deleted, and A12 checks
it at three independent levels. Two of the three are here:

- **Connection.** :func:`authorizer` is an allowlist over sqlite actions, default
  deny, with ``INSERT`` granted only for the duration of one append.
- **Schema.** :func:`append_only_triggers` returns the aborting ``BEFORE UPDATE``
  and ``BEFORE DELETE`` triggers, so a connection opened by any other tool is
  still refused.

The third -- that no update or delete method exists on the API -- cannot be
factored out, because it is a property of each store's own surface. Each store
owns it, and A12 checks it on each.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sciagent.core.types import Digest

__all__ = [
    "ABORT_MESSAGE",
    "DIGEST_BYTES",
    "PERMITTED_ACTIONS",
    "AppendGrant",
    "append_only_triggers",
    "authorizer",
    "connect",
    "digest_of_bytes",
    "encode_mapping",
    "field",
    "is_append_only_refusal",
]

#: Bytes of digest. 256 bits: content addresses are compared across campaigns and
#: machines, and a collision would silently merge two different rows.
DIGEST_BYTES: Final = 32

#: What a refused mutation says. Matched by :func:`is_append_only_refusal`, so a
#: store can tell an append-only refusal from an unrelated sqlite failure and
#: raise the typed error for one without swallowing the other.
ABORT_MESSAGE: Final = "the registry is append-only"

#: sqlite authorizer actions a store permits unconditionally. An allowlist rather
#: than a denylist: a future sqlite action that this code has never heard of
#: should be refused, not admitted by omission.
#:
#: ``SQLITE_INSERT`` is deliberately absent. It is the one action a store needs
#: but must not offer on every path: an authorizer is per *connection*, so
#: admitting it outright would let a read method -- which exists to read -- write
#: a row that never passed the store's own checks. It is granted only for the
#: duration of an append, by :class:`AppendGrant`.
PERMITTED_ACTIONS: Final[frozenset[int]] = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,
    }
)


def append_only_triggers(table: str) -> tuple[str, ...]:
    """Return the aborting update and delete triggers for one table.

    Guarantees the pair is refused at the schema level, so a connection this
    package never opened -- the sqlite CLI, another process -- is refused too.
    """
    return tuple(
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} "
        f"BEFORE {event} ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{ABORT_MESSAGE}'); END"
        for event in ("UPDATE", "DELETE")
    )


@dataclass(slots=True)
class AppendGrant:
    """Whether a store's own ``INSERT`` is authorised at this instant.

    A separate object rather than a flag on the store, because the authorizer
    closure has to read it and the connection holds the closure. Capturing the
    *store* would make ``connection -> closure -> store -> connection`` a
    reference cycle, so releasing the sqlite handle would wait on the cyclic
    collector; on Windows that is long enough for a temporary-directory teardown
    to fail on a file still open. This object references neither the store nor
    the connection, so refcounting alone still frees both.
    """

    open: bool = False


def authorizer(grant: AppendGrant) -> Callable[..., int]:
    """Return a connection's authorizer. Allowlist; default deny.

    ``INSERT`` is admitted only while ``grant.open`` is true, which is only
    inside a store's ``append``. Every other statement reaching the connection --
    including one through an ad-hoc read method -- is refused, so a store's own
    validation cannot be routed around.
    """

    def authorize(action: int, *_: object) -> int:
        if action == sqlite3.SQLITE_INSERT:
            return sqlite3.SQLITE_OK if grant.open else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in PERMITTED_ACTIONS else sqlite3.SQLITE_DENY

    return authorize


def connect(target: Path | str) -> sqlite3.Connection:
    """Return a connection whose every statement reaches the authorizer.

    ``cached_statements=0`` is load-bearing, not a tuning knob. Python's sqlite3
    caches prepared statements by SQL text, and a cache hit **skips the
    authorizer**, which runs at prepare time. With the cache on, re-issuing the
    exact text of a store's own ``INSERT`` through its read method is authorised
    by the prepare that happened during an earlier append -- measured, not
    feared. Disabling the cache is what makes the write-scoped grant in
    :func:`authorizer` actually hold.

    ``check_same_thread=True`` is passed explicitly although it is the default,
    because it is the whole of the argument that :class:`AppendGrant` may be a
    plain flag rather than a per-statement grant: a second thread reaching this
    connection is refused before the authorizer is consulted at all. Leaving it
    to the default meant that argument lived only in a docstring, and CLAUDE.md's
    second invariant asks for the assertion instead. Turning it off silently
    widens the append window to anything another thread can issue.
    """
    return sqlite3.connect(target, cached_statements=0, check_same_thread=True)


def is_append_only_refusal(error: sqlite3.Error) -> bool:
    """Return whether ``error`` is the store refusing a mutation.

    Distinguishes the refusal a caller should see as
    :class:`~sciagent.core.errors.AppendOnlyViolationError` from an unrelated
    sqlite failure, which must not be reported as an append-only violation.
    """
    text = str(error).lower()
    return "not authorized" in text or ABORT_MESSAGE in text


# --------------------------------------------------------------------------
# Canonical encoding
# --------------------------------------------------------------------------


def field(name: str, value: str) -> bytes:
    """Return one length-prefixed field.

    Length-prefixing rather than delimiting is what makes the encoding
    injective: joined-with-a-separator encodings collide as soon as a value
    contains the separator, and the values here are arbitrary strings.
    """
    payload = value.encode("utf-8")
    return f"{name}={len(payload)}:".encode("ascii") + payload


def encode_mapping(name: str, mapping: Mapping[str, str]) -> bytes:
    """Return a length-prefixed encoding of a string mapping, order-independent."""
    items = sorted(mapping.items())
    chunks = [f"{name}#{len(items)}:".encode("ascii")]
    for key, value in items:
        chunks.append(field(f"{name}.k", key))
        chunks.append(field(f"{name}.v", value))
    return b"".join(chunks)


def digest_of_bytes(payload: bytes) -> Digest:
    """Return the content address of a canonical encoding."""
    return Digest(hashlib.blake2b(payload, digest_size=DIGEST_BYTES).hexdigest())
