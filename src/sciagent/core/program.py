"""Generative programmes: representation, deterministic execution, reachability.

A programme is a set of :class:`Component` nodes plus two edge sets:

``edges``
    every dependency, instantaneous or lagged. This is the relation
    :meth:`GenerativeProgram.descendants` walks, and therefore the relation from
    which causal collateral effects are derived (SPEC §3.3, §7.2).

``history_edges``
    the subset of ``edges`` that are *lagged*: the child at event ``i`` sees the
    parent's values at events ``< i`` only.

Acyclicity is required of ``edges - history_edges`` alone. A lagged edge
connects distinct event indices, so it cannot close a cycle in the time-unrolled
graph even when it opposes an instantaneous edge. This is what makes both the
Hawkes self-loop ``arrival -> arrival`` and scenario S11's ``size -> arrival``
expressible without contradicting SPEC §3.1's DAG requirement.

Family semantics live in a :class:`FamilyLibrary` supplied by the environment.
``sciagent`` therefore never imports ``environments`` and holds no mutable
global registry: a programme carries everything needed to execute it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field

import numpy as np

from sciagent.core.errors import (
    CyclicDependencyError,
    DeterminismError,
    ExecutionError,
    UnknownComponentError,
)
from sciagent.core.types import (
    ComponentId,
    ComponentKind,
    EventLog,
    FamilyId,
    Floats,
    FrozenDict,
    LatentSpec,
    LatentSpecId,
    Parameters,
    Seed,
)

# --------------------------------------------------------------------------
# Seed derivation
# --------------------------------------------------------------------------

_HASH_BYTES = 8


def stable_key(text: str) -> int:
    """Return a process-independent 64-bit integer key for ``text``.

    Guarantees the same value in every process, interpreter run and platform,
    unlike :func:`hash`, which is randomised per process by ``PYTHONHASHSEED``.
    Using :func:`hash` here would make execution non-reproducible across
    processes while looking perfectly deterministic within one -- the exact
    failure acceptance test A1's subprocess arm exists to catch.
    """
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=_HASH_BYTES).digest()
    return int.from_bytes(digest, "big")


def derive_generator(seed: Seed, key: str) -> np.random.Generator:
    """Return the generator for stream ``key`` under ``seed``.

    Seed derivation is *by name*, not by draw order::

        SeedSequence(entropy=seed, spawn_key=(stable_key(key),))

    Guarantees:

    * the stream for a component depends only on ``(seed, key)``, so adding,
      removing or reordering other components never perturbs it;
    * no reliance on ``SeedSequence.spawn()``, whose output depends on the order
      and count of previous spawns;
    * every stream is statistically independent (SeedSequence hashes the full
      ``spawn_key`` into 128 bits of entropy).

    Stream keys in use are the component id, and ``"{component}:{latent}"`` for
    a latent process (see :meth:`sciagent.core.types.LatentSpec.state_key`).
    """
    if seed < 0:
        raise DeterminismError(f"seed must be non-negative, got {seed}")
    sequence = np.random.SeedSequence(entropy=int(seed), spawn_key=(stable_key(key),))
    return np.random.Generator(np.random.PCG64(sequence))


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Component:
    """One node of a generative programme (SPEC §3.1).

    ``family`` is a *compiled* field: edits declare structure, and the grammar's
    resolution table determines the resulting family id (see
    ``sciagent.core.edits.EditGrammar.apply``). Structural payloads that will not
    fit in ``parameters``, which is float-valued by specification -- a mixture's
    component count, a Hawkes kernel's shape, a periodic rate's functional form
    -- are encoded in the family id itself.
    """

    id: ComponentId
    kind: ComponentKind
    family: FamilyId
    parameters: Parameters = field(default_factory=FrozenDict)
    latents: tuple[LatentSpec, ...] = ()


# --------------------------------------------------------------------------
# Execution context and family library
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrawContext:
    """Everything a family kernel may condition on when drawing one value.

    A kernel is a pure function of this context plus the generators' internal
    state. It may mutate ``latent_state`` (its own entry only) and must not
    mutate anything else.
    """

    index: int
    """Index of the event being generated, zero-based."""

    component: Component
    rng: np.random.Generator
    """The component's own stream, derived from its id."""

    latent_rngs: Mapping[LatentSpecId, np.random.Generator]
    """One stream per attached latent, derived from ``"{component}:{latent}"``."""

    latent_state: MutableMapping[str, float]
    """Live latent values, keyed by ``LatentSpec.state_key``. Persists across
    events; the kernel owns the entries for its own component's latents."""

    parents: Mapping[ComponentId, float]
    """Values of instantaneous parents at this event."""

    history: Mapping[ComponentId, Floats]
    """Values of lagged parents at events ``< index`` (read-only views)."""

    self_history: Floats
    """This component's own values at events ``< index`` (read-only view)."""

    @property
    def params(self) -> Parameters:
        return self.component.parameters

    def parameter(self, name: str) -> float:
        """Return parameter ``name``, raising a typed error if absent."""
        try:
            return self.component.parameters[name]
        except KeyError as exc:
            raise ExecutionError(
                f"component {self.component.id!r} (family {self.component.family!r}) "
                f"is missing required parameter {name!r}"
            ) from exc


type Kernel = Callable[[DrawContext], float]
type LatentInit = Callable[[Parameters, np.random.Generator], float]


@dataclass(frozen=True, slots=True)
class FamilyLibrary:
    """The executable semantics of a set of family ids.

    Supplied by the environment and carried by the programme, so that
    ``sciagent`` never imports ``environments`` and no mutable global registry
    exists. Identity is ``(name, version)``: two libraries with the same name and
    version are interchangeable by contract.
    """

    name: str
    version: str
    kernels: FrozenDict[FamilyId, Kernel]
    latent_inits: FrozenDict[LatentSpecId, LatentInit] = field(
        default_factory=FrozenDict
    )

    def kernel(self, family: FamilyId) -> Kernel:
        """Return the kernel for ``family``, raising a typed error if unknown."""
        try:
            return self.kernels[family]
        except KeyError as exc:
            raise ExecutionError(
                f"family {family!r} is not implemented by library "
                f"{self.name!r} v{self.version}"
            ) from exc

    def latent_init(self, spec: LatentSpecId) -> LatentInit:
        """Return the initialiser for latent ``spec``, or raise a typed error."""
        try:
            return self.latent_inits[spec]
        except KeyError as exc:
            raise ExecutionError(
                f"latent spec {spec!r} has no initialiser in library "
                f"{self.name!r} v{self.version}"
            ) from exc

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FamilyLibrary):
            return (self.name, self.version) == (other.name, other.version)
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.name, self.version))


# --------------------------------------------------------------------------
# Programme
# --------------------------------------------------------------------------

type Edge = tuple[ComponentId, ComponentId]


@dataclass(frozen=True, slots=True)
class GenerativeProgram:
    """A compositional executable programme (SPEC §3.1).

    Guarantees, checked at construction: every edge endpoint exists; every
    history edge is also in ``edges``; the instantaneous edge set is acyclic;
    every referenced family and latent spec is implemented by ``library``.
    """

    components: FrozenDict[ComponentId, Component]
    edges: frozenset[Edge]
    library: FamilyLibrary
    history_edges: frozenset[Edge] = frozenset()

    def __post_init__(self) -> None:
        for source, target in sorted(self.edges):
            for endpoint in (source, target):
                if endpoint not in self.components:
                    raise UnknownComponentError(
                        f"edge {(source, target)!r} references unknown component "
                        f"{endpoint!r}"
                    )
        orphaned = self.history_edges - self.edges
        if orphaned:
            raise UnknownComponentError(
                f"history edges must also appear in edges; missing: "
                f"{sorted(orphaned)!r}"
            )
        for component_id in self.order():  # raises on a cycle
            component = self.components[component_id]
            self.library.kernel(component.family)
            for latent in component.latents:
                self.library.latent_init(latent.id)

    # -- structure ---------------------------------------------------------

    @property
    def instantaneous_edges(self) -> frozenset[Edge]:
        """Edges whose parent value is read at the *same* event index."""
        return self.edges - self.history_edges

    def parents(
        self, component: ComponentId, *, lagged: bool
    ) -> tuple[ComponentId, ...]:
        """Return the sorted parents of ``component`` along lagged or instant edges."""
        self._require(component)
        chosen = self.history_edges if lagged else self.instantaneous_edges
        return tuple(sorted(source for source, target in chosen if target == component))

    def order(self) -> tuple[ComponentId, ...]:
        """Return the execution order: a topological sort of instantaneous edges.

        Guarantees a total, reproducible order. Ties are broken by sorted
        component id, never by set or dict iteration order, so the result does
        not depend on ``PYTHONHASHSEED`` or on construction order. Raises
        :class:`CyclicDependencyError` if the instantaneous edges contain a cycle.
        """
        edges = self.instantaneous_edges
        remaining = {
            component_id: sum(1 for _, target in edges if target == component_id)
            for component_id in self.components
        }
        ready = sorted(c for c, degree in remaining.items() if degree == 0)
        result: list[ComponentId] = []
        while ready:
            current = ready.pop(0)
            result.append(current)
            newly_ready: list[ComponentId] = []
            for source, target in sorted(edges):
                if source == current:
                    remaining[target] -= 1
                    if remaining[target] == 0:
                        newly_ready.append(target)
            ready = sorted(ready + newly_ready)
        if len(result) != len(self.components):
            unresolved = sorted(set(self.components) - set(result))
            raise CyclicDependencyError(
                f"instantaneous edges contain a cycle among {unresolved!r}; "
                f"only lagged (history) edges may oppose an instantaneous edge"
            )
        return tuple(result)

    def descendants(self, c: ComponentId) -> frozenset[ComponentId]:
        """Return every component reachable from ``c`` along one or more edges.

        Reachability is over the *full* edge set, lagged edges included: an
        intervention on ``c`` propagates through a lagged edge just as it does
        through an instantaneous one, only later. The relation is strict, i.e.
        it counts paths of length >= 1, so ``c`` appears in its own descendant
        set exactly when it lies on a cycle -- which, given the acyclicity
        invariant, means it has a history dependence on itself or on one of its
        own descendants. Collateral effects (SPEC §3.3) are therefore
        ``descendants(target) - {target}``.
        """
        self._require(c)
        adjacency: dict[ComponentId, list[ComponentId]] = {
            component_id: [] for component_id in self.components
        }
        for source, target in sorted(self.edges):
            adjacency[source].append(target)
        reached: set[ComponentId] = set()
        stack = list(adjacency[c])
        while stack:
            current = stack.pop()
            if current in reached:
                continue
            reached.add(current)
            stack.extend(adjacency[current])
        return frozenset(reached)

    def _require(self, component: ComponentId) -> None:
        if component not in self.components:
            raise UnknownComponentError(f"unknown component {component!r}")

    # -- execution ---------------------------------------------------------

    def execute(self, seed: Seed, n_events: int) -> EventLog:
        """Generate exactly ``n_events`` events and return the log.

        Guarantees bit-exact reproducibility: for a fixed
        ``(seed, n_events, programme, library version)`` the returned log is
        byte-identical in every process and every run (acceptance test A1).
        This rests on three properties and nothing else:

        1. every generator is derived by name via :func:`derive_generator`;
        2. components are visited in :meth:`order`, a total sorted-tie-break
           topological order, so no set or dict iteration order is observable;
        3. kernels take their generator explicitly and draw only through it.

        Events are generated one at a time. Within event ``i`` each component is
        drawn in topological order and may condition on its instantaneous
        parents at ``i``, on its lagged parents at ``< i``, and on its own past.
        """
        if n_events <= 0:
            raise ExecutionError(f"n_events must be positive, got {n_events}")

        order = self.order()
        rngs = {
            component_id: derive_generator(seed, str(component_id))
            for component_id in order
        }
        latent_rngs: dict[ComponentId, dict[LatentSpecId, np.random.Generator]] = {}
        latent_state: dict[str, float] = {}
        for component_id in order:
            component = self.components[component_id]
            streams: dict[LatentSpecId, np.random.Generator] = {}
            for latent in component.latents:
                key = latent.state_key(component_id)
                stream = derive_generator(seed, key)
                streams[latent.id] = stream
                latent_state[key] = self.library.latent_init(latent.id)(
                    latent.parameters, stream
                )
            latent_rngs[component_id] = streams

        values: dict[ComponentId, Floats] = {
            component_id: np.zeros(n_events, dtype=np.float64) for component_id in order
        }
        latent_keys = sorted(latent_state)
        traces: dict[str, Floats] = {
            key: np.zeros(n_events, dtype=np.float64) for key in latent_keys
        }

        lagged_parents = {c: self.parents(c, lagged=True) for c in order}
        instant_parents = {c: self.parents(c, lagged=False) for c in order}

        for index in range(n_events):
            current: dict[ComponentId, float] = {}
            for component_id in order:
                component = self.components[component_id]
                context = DrawContext(
                    index=index,
                    component=component,
                    rng=rngs[component_id],
                    latent_rngs=latent_rngs[component_id],
                    latent_state=latent_state,
                    parents={p: current[p] for p in instant_parents[component_id]},
                    history={
                        p: values[p][:index] for p in lagged_parents[component_id]
                    },
                    self_history=values[component_id][:index],
                )
                drawn = self.library.kernel(component.family)(context)
                if not np.isfinite(drawn):
                    raise ExecutionError(
                        f"component {component_id!r} (family {component.family!r}) "
                        f"drew a non-finite value {drawn!r} at event {index}"
                    )
                current[component_id] = drawn
                values[component_id][index] = drawn
            for key in latent_keys:
                traces[key][index] = latent_state[key]

        return EventLog(
            n_events=n_events,
            values=FrozenDict(values),
            latents=FrozenDict(traces),
        )


def component_of_kind(program: GenerativeProgram, kind: ComponentKind) -> ComponentId:
    """Return the unique component of ``kind``, or raise a typed error.

    Convenience for environments whose programmes have one component per kind.
    """
    matches = sorted(
        component_id
        for component_id, component in program.components.items()
        if component.kind == kind
    )
    if len(matches) != 1:
        raise UnknownComponentError(
            f"expected exactly one component of kind {kind!r}, found {matches!r}"
        )
    return matches[0]
