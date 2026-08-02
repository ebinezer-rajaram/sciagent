"""Data partitions, and the boundary an agent may not cross.

Two orthogonal partition axes exist in this project and they are easy to
conflate, so they are separated here explicitly.

**Data partition** -- :class:`DataPartition`, defined in this module. It labels
*registered rows*: which pool of scenarios a result came from. DEV is where
development happens and is the only pool an agent's tools may read; HOLDOUT
backs the calibration and coverage claims that would be worthless if development
had been able to see them; TEST is sealed for the three campaigns of SPEC F11.
This is the axis acceptance test A14 restricts.

**Claim partition** -- ``exploratory | confirmatory``, a property of a
:class:`Claim` under SPEC §3.3, saying what evidential weight a claim asserts for
itself. It has nothing to do with which pool the data came from, and lives with
the claim types rather than here.

SPEC §10's one-line comment on this module lists members of both axes, which is
what makes the conflation tempting.

Enforcement
-----------

The boundary is guarded twice, on the principle that a static check cannot
observe a dynamically constructed call and a runtime check cannot observe a path
that was not taken:

*Statically*, :data:`AGENT_TOOL_SURFACE` declares which modules an agent may
call into and :data:`SEALED_SYMBOLS` declares the names that constitute sealed
access. Acceptance test A14 builds the call graph and asserts no path runs from
the first to the second. Both lists live here, in the shipped package, so the
analyser reads the same declaration the code is written against.

*At runtime*, sealed rows are reachable only by presenting a :class:`SealedAccess`
token, and the ordinary read path raises :class:`PartitionAccessError` on a
sealed partition rather than returning an empty result -- silence would be
indistinguishable from an empty pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sciagent.core.errors import PartitionAccessError


class DataPartition(Enum):
    """The pool a registered experiment belongs to.

    Values are lowercase strings and are what the registry stores, so the
    database is readable without the enum.
    """

    DEV = "dev"
    """Development. The only partition an agent's tool surface may read."""

    HOLDOUT = "holdout"
    """Framework-only. Backs calibration and coverage claims (SPEC §6.2 A8)."""

    TEST = "test"
    """Sealed for the three preregistered campaigns of SPEC F11."""


#: Partitions an agent's tools may read.
AGENT_REACHABLE: frozenset[DataPartition] = frozenset({DataPartition.DEV})

#: Partitions no agent-reachable code path may touch (SPEC §6.3 A14).
SEALED: frozenset[DataPartition] = frozenset(
    {DataPartition.HOLDOUT, DataPartition.TEST}
)

#: Modules an agent may call into, as :mod:`fnmatch` patterns over dotted names.
#:
#: Empty of existing modules until SPEC §11 item 12, which is the first item
#: containing an agent. It is declared now, and non-empty, so that the declaration
#: cannot go missing between here and there: A14 asserts both that it exists and
#: that the analyser it feeds is capable of finding a violation.
AGENT_TOOL_SURFACE: tuple[str, ...] = (
    "sciagent.systems",
    "sciagent.systems.*",
    "sciagent.experiments.dsl",
)

#: Names whose appearance constitutes sealed-partition access. Read by A14's
#: analyser, so this tuple and the code are one declaration rather than two.
SEALED_SYMBOLS: tuple[str, ...] = (
    "HOLDOUT",
    "TEST",
    "SEALED",
    "SealedAccess",
    "sealed_records",
    "require_sealed",
)


@dataclass(frozen=True, slots=True)
class SealedAccess:
    """A capability token authorising one read of a sealed partition.

    Constructing one is deliberately trivial: the token is not a security
    measure against a determined caller, it is a *declaration*, and its purpose
    is to make sealed access a distinguishable syntactic event that A14's
    analyser can find. ``reason`` is recorded by callers so that a grep of the
    tree answers "who reads HOLDOUT, and why" without reading every function.
    """

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise PartitionAccessError("sealed access requires a stated reason")


def require_sealed(partition: DataPartition, access: SealedAccess | None) -> None:
    """Raise unless ``access`` authorises reading ``partition``.

    Guarantees that a sealed partition is never read without a token, and that an
    unsealed partition never needs one. Raises rather than returning an empty
    result: an unauthorised read must be distinguishable from an empty pool.
    """
    if partition in SEALED and access is None:
        raise PartitionAccessError(
            f"partition {partition.value!r} is sealed; reading it requires an "
            f"explicit SealedAccess token, and no agent-reachable code path may "
            f"construct one (SPEC §6.3 A14)"
        )
    if partition not in SEALED and access is not None:
        raise PartitionAccessError(
            f"partition {partition.value!r} is not sealed; presenting a "
            f"SealedAccess token for it is a category error"
        )
