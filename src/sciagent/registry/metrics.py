"""The versioned metric registry.

A diagnostic's *definition* is part of what determines a result, so it is part of
the content address (SPEC §6.3 A13). Recording "Fano factor = 3.1" without
recording which Fano factor was meant makes the row uncitable the moment the
estimator changes -- and estimators do change, which is why SPEC §7.1 has a
whole category for results that are relevant but version-mismatched.

The registry is immutable and append-only in the same sense as the store:
:meth:`MetricRegistry.with_metric` returns a new registry, and redefining a name
in place is an error rather than an update, because it would silently change the
meaning of every result already recorded under that name.

Computation is injected, never imported. A :class:`MetricSpec` carries the
callable that computes it, supplied by the environment, exactly as
:class:`~sciagent.core.program.FamilyLibrary` carries family semantics. That is
what lets ``sciagent`` hold a registry of point-process diagnostics without
importing ``environments``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sciagent.core.errors import DuplicateMetricError, UnknownMetricError
from sciagent.core.types import (
    EventLog,
    FrozenDict,
    MetricName,
    MetricRef,
    MetricVersion,
)

type MetricCompute = Callable[[EventLog], float]

#: ``MetricRef`` moved to ``sciagent.core.types`` with backlog item 5, so that
#: :class:`~sciagent.core.types.Prediction` could name a diagnostic without
#: ``core`` importing from ``registry``. Re-exported here because it is part of
#: this module's published surface and every call site still reads naturally.
__all__ = [
    "MetricCompute",
    "MetricRef",
    "MetricRegistry",
    "MetricSpec",
]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One diagnostic: its versioned identity, its range, and how to compute it.

    ``low`` and ``high`` bound the diagnostic's range. They are declared rather
    than inferred because acceptance test A16 rejects hypotheses whose refutation
    condition is unsatisfiable *over the diagnostic's range*, which is not a
    question that can be asked of a bare callable.
    """

    ref: MetricRef
    compute: MetricCompute = field(compare=False, repr=False)
    low: float = float("-inf")
    high: float = float("inf")

    def __post_init__(self) -> None:
        if not self.high > self.low:
            raise UnknownMetricError(
                f"metric {self.ref} declares an empty range {self.low}..{self.high}"
            )

    @property
    def name(self) -> MetricName:
        return self.ref.name


@dataclass(frozen=True, slots=True)
class MetricRegistry:
    """An immutable, versioned catalogue of diagnostics.

    Guarantees :attr:`version` is a content hash over every registered
    ``(name, version)`` pair, so adding, removing or revising any metric changes
    the registry version and therefore changes the content address of every
    experiment registered against it.
    """

    specs: FrozenDict[MetricName, MetricSpec] = field(default_factory=FrozenDict)

    @classmethod
    def of(cls, specs: Iterable[MetricSpec]) -> MetricRegistry:
        """Build a registry from ``specs``, rejecting duplicate names."""
        registry = cls()
        for spec in specs:
            registry = registry.with_metric(spec)
        return registry

    def with_metric(self, spec: MetricSpec) -> MetricRegistry:
        """Return a new registry with ``spec`` added.

        Re-adding an identical spec is a no-op. Adding a different spec under an
        existing name raises :class:`DuplicateMetricError`: a metric name is a
        promise about what recorded numbers mean, and it is kept by versioning
        the name, not by overwriting it.
        """
        existing = self.specs.get(spec.name)
        if existing is not None:
            if existing == spec:
                return self
            raise DuplicateMetricError(
                f"metric {spec.name!r} is already registered as {existing.ref}; "
                f"register {spec.ref} under a new name or a new version rather "
                f"than redefining it in place"
            )
        return MetricRegistry(
            specs=FrozenDict[MetricName, MetricSpec]({**self.specs, spec.name: spec})
        )

    def spec(self, name: str) -> MetricSpec:
        """Return the spec for ``name``, or raise a typed error."""
        try:
            return self.specs[MetricName(name)]
        except KeyError as exc:
            raise UnknownMetricError(
                f"metric {name!r} is not registered; known metrics are "
                f"{sorted(self.names)!r}"
            ) from exc

    @property
    def names(self) -> tuple[MetricName, ...]:
        """Return every registered metric name, in a fixed order."""
        return tuple(self.specs)

    @property
    def version(self) -> MetricVersion:
        """Return the content hash of the whole catalogue.

        This is the ``metric_version`` field of an
        :class:`~sciagent.registry.store.ExperimentKey`. It covers names and
        versions, not the callables: what a metric *is* is declared by its
        version, and a change of implementation that does not change the version
        is an assertion by the author that the definition has not changed.
        """
        payload = "\n".join(f"{ref.name}\x00{ref.version}" for ref in self.refs)
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
        return MetricVersion(f"metrics/{digest}")

    @property
    def refs(self) -> tuple[MetricRef, ...]:
        """Return every registered reference, ordered by name."""
        return tuple(self.specs[name].ref for name in self.names)
