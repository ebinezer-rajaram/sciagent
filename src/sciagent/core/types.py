"""Core value types and identifier aliases.

Only the types backlog items 2-3 need live here. ``Claim``, ``Diagnosis``,
``Prediction``, ``Estimand`` and ``Scope`` (SPEC §3.3-3.4) arrive with their own
backlog items.

Everything here is immutable and hashable, so programmes and defects can be
content-addressed by the registry later without a separate serialisation path.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal, NewType, TypeVar

import numpy as np
import numpy.typing as npt

# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------

ComponentId = NewType("ComponentId", str)
FamilyId = NewType("FamilyId", str)
LatentSpecId = NewType("LatentSpecId", str)
KernelId = NewType("KernelId", str)
GrammarVersion = NewType("GrammarVersion", str)
Seed = NewType("Seed", int)
Probability = NewType("Probability", float)

#: The four versioned quantities a registered experiment is addressed by, plus
#: the address itself (SPEC §6.3 A13). Each is a distinct type rather than a bare
#: ``str`` so that transposing two of them in a five-field key is a type error and
#: not a silently different content address.
EnvVersion = NewType("EnvVersion", str)
DataVersion = NewType("DataVersion", str)
MetricVersion = NewType("MetricVersion", str)
MetricName = NewType("MetricName", str)
Digest = NewType("Digest", str)

ComponentKind = Literal["arrival", "size", "sign", "observation"]

#: Every component kind, in the canonical order used for display and sorting.
COMPONENT_KINDS: tuple[ComponentKind, ...] = ("arrival", "size", "sign", "observation")

type Floats = npt.NDArray[np.float64]

# --------------------------------------------------------------------------
# Hashable mapping
# --------------------------------------------------------------------------

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class FrozenDict(Mapping[K, V]):
    """An immutable, hashable ``Mapping`` with a deterministic iteration order.

    Guarantees:

    * iteration order is ``sorted(keys, key=repr)`` and never depends on
      insertion order, on ``PYTHONHASHSEED``, or on the process;
    * ``__hash__`` is defined whenever the values are hashable, so it can sit in
      a ``frozen=True`` dataclass field typed ``Mapping[str, float]``;
    * no mutation path exists.

    Keys are ordered by ``repr`` rather than by ``<`` so that heterogeneous key
    types (plain ids and tuple keys) are both supported by one implementation.
    For a fixed key set the order is total and reproducible, which is all the
    determinism invariant requires.
    """

    __slots__ = ("_data", "_hash")

    _data: dict[K, V]
    _hash: int | None

    def __init__(self, data: Mapping[K, V] | Iterable[tuple[K, V]] = ()) -> None:
        items = dict(data)
        object.__setattr__(
            self, "_data", {key: items[key] for key in sorted(items, key=repr)}
        )
        object.__setattr__(self, "_hash", None)

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in self._data.items())
        return f"FrozenDict({{{inner}}})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self._data) == dict(other)
        return NotImplemented

    def __hash__(self) -> int:
        if self._hash is None:
            object.__setattr__(self, "_hash", hash(tuple(self._data.items())))
        cached = self._hash
        assert cached is not None
        return cached


Parameters = FrozenDict[str, float]


def parameters(**values: float) -> Parameters:
    """Build a :class:`Parameters` mapping from keyword arguments."""
    return FrozenDict[str, float](values)


# --------------------------------------------------------------------------
# Latent variables
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LatentSpec:
    """A latent process attached to a component.

    Guarantees the latent's identity and parameters are immutable and that the
    executor can derive a dedicated generator for it (see
    ``sciagent.core.program.derive_generator``).
    """

    id: LatentSpecId
    parameters: Parameters = field(default_factory=FrozenDict)

    def state_key(self, owner: ComponentId) -> str:
        """Return the key under which this latent's state is traced.

        The key is stable across processes and unique within a programme.
        """
        return f"{owner}:{self.id}"


# --------------------------------------------------------------------------
# Event log
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False, slots=True)
class EventLog:
    """The output of one programme execution.

    Guarantees: every array is read-only, of length ``n_events`` and dtype
    ``float64``; :meth:`to_bytes` is a total, order-independent serialisation, so
    two logs are byte-identical if and only if they are equal. Equality is
    defined on those bytes, not on numpy's elementwise comparison.

    ``values`` is keyed by component id; ``latents`` by
    :meth:`LatentSpec.state_key`.
    """

    n_events: int
    values: FrozenDict[ComponentId, Floats]
    latents: FrozenDict[str, Floats] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for array in (*self.values.values(), *self.latents.values()):
            array.flags.writeable = False

    def to_bytes(self) -> bytes:
        """Return a canonical byte serialisation of the whole log."""
        chunks: list[bytes] = [f"n_events={self.n_events}".encode()]
        for key in self.values:
            chunks.append(f"|value:{key}|".encode())
            chunks.append(self.values[key].tobytes())
        for state_key in self.latents:
            chunks.append(f"|latent:{state_key}|".encode())
            chunks.append(self.latents[state_key].tobytes())
        return b"".join(chunks)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EventLog):
            return self.to_bytes() == other.to_bytes()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.to_bytes())
