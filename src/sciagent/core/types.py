"""Core value types and identifier aliases.

``Prediction`` (SPEC §3.3) arrives here with backlog item 5 and ``Diagnosis``
(SPEC §3.4) with item 9. ``Claim``, ``Estimand`` and ``Scope`` arrive with their
own later items.

Everything here is immutable and hashable, so programmes and defects can be
content-addressed by the registry later without a separate serialisation path.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal, NewType, TypeVar

import numpy as np
import numpy.typing as npt

from sciagent.core.conditions import Condition
from sciagent.core.errors import DiagnosisError

if TYPE_CHECKING:
    # ``Defect`` is ``frozenset[Edit]`` and ``edits`` imports this module, so a
    # runtime import here would be circular. ``Diagnosis`` names the type only in
    # annotations, which ``from __future__ import annotations`` leaves unevaluated.
    from sciagent.core.edits import Defect

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

#: Identifiers for the investigation record (SPEC §3.3).
HypothesisId = NewType("HypothesisId", str)
PredictionId = NewType("PredictionId", str)
ExperimentId = NewType("ExperimentId", str)

#: Which of SPEC §4.5's twelve slice scenarios an investigation was run on.
ScenarioId = NewType("ScenarioId", str)

#: The identity of an experiment design (SPEC §4.4), which is
#: :attr:`sciagent.experiments.dsl.ExperimentDesign.id` -- a readable canonical
#: rendering of the act and the diagnostics it is read over.
#:
#: SPEC §3.3 writes ``Prediction.under: ExperimentTemplate``, i.e. the structure
#: itself. Backlog item 7 kept the id instead, deliberately. A ``Prediction`` is
#: a frozen value type that gets content-addressed, and embedding a whole design
#: in each one enlarges what is hashed while adding nothing: the id *is* the
#: design's canonical rendering, so naming it names the design uniquely. The
#: divergence from the specification's literal type is recorded in
#: ``docs/DECISIONS.md``.
ExperimentTemplateId = NewType("ExperimentTemplateId", str)

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


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricRef:
    """A metric named at a specific version (SPEC §3.3).

    Lives here rather than with :class:`~sciagent.registry.metrics.MetricRegistry`
    because :class:`Prediction` names a diagnostic and ``core`` may not import
    from ``registry``. ``sciagent.registry.metrics`` re-exports it, so either
    import path is valid.
    """

    name: MetricName
    version: str

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


# --------------------------------------------------------------------------
# Hypotheses and predictions
# --------------------------------------------------------------------------

HypothesisStatus = Literal["live", "suspended", "rejected", "confirmed"]

#: Every status, in the canonical order used for display.
HYPOTHESIS_STATUSES: tuple[HypothesisStatus, ...] = (
    "live",
    "suspended",
    "rejected",
    "confirmed",
)


class RejectionCode(Enum):
    """Why a hypothesis or one of its predictions was refused.

    Codes are recorded rather than raised alone, so that a refusal is a datum in
    the investigation record and not only a control-flow event. The first two
    lines are the criterion SPEC §6.4 A16 states; the rest are neighbouring
    incoherences the same interval arithmetic decides for free, kept separate so
    that A16's own gate measures exactly what A16 claims.
    """

    NO_PREDICTIONS = "no_predictions"
    """No prediction at all. SPEC §3.3 requires at least one."""

    UNSATISFIABLE_REFUTATION = "unsatisfiable_refutation"
    """No attainable diagnostic value could refute the hypothesis (A16)."""

    TAUTOLOGICAL_REFUTATION = "tautological_refutation"
    """Every attainable value refutes it, so the prediction carries nothing."""

    OVERLAPPING_REFUTATION = "overlapping_refutation"
    """Some value both confirms and refutes."""

    UNSATISFIABLE_CONDITION = "unsatisfiable_condition"
    """No attainable value could confirm the hypothesis."""

    UNKNOWN_METRIC = "unknown_metric"
    """The diagnostic is not in the metric registry, so it has no range."""

    DUPLICATE = "duplicate"
    """A structurally identical edit set is already in the graph (A18)."""

    MISMATCHED_HYPOTHESIS = "mismatched_hypothesis"
    """A prediction claims a different hypothesis than the one carrying it."""


@dataclass(frozen=True, slots=True)
class Prediction:
    """What a hypothesis says an experiment will show, and what would sink it.

    ``refutation`` must be satisfiable over ``diagnostic``'s declared range and
    must not overlap ``condition``; both are checked by
    :func:`sciagent.hypothesis.validator.validate_prediction`.

    SPEC §3.3 lists this without an ``id``, but
    :attr:`~sciagent.hypothesis.graph.HypothesisNode.predictions` holds
    ``PredictionId``s, so one is carried here.
    """

    id: PredictionId
    hypothesis_id: HypothesisId
    diagnostic: MetricRef
    condition: Condition
    under: ExperimentTemplateId
    refutation: Condition


# --------------------------------------------------------------------------
# Diagnosis
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """What one research system concluded about one scenario (SPEC §3.4).

    A value type and nothing more: every number in it is derived by
    :func:`sciagent.systems.base.diagnose` from a posterior engine, and no
    system constructs one directly. That is what keeps SPEC's second invariant
    true of the systems layer -- a baseline chooses *structure* (which
    hypotheses to propose, which designs to run) and the framework turns the
    consequences into numbers.

    Guarantees ``distribution`` is normalised over the hypotheses it names and
    that every mass lies in ``[0, 1]``; a diagnosis that does not describe a
    distribution cannot be built.
    """

    scenario_id: ScenarioId
    distribution: FrozenDict[HypothesisId, Probability]
    abstain_mass: Probability
    """Posterior mass not on the single leading hypothesis, ``1 - max_h p(h)``.

    SPEC §3.4 names the field without defining it; this reading is recorded in
    ``docs/DECISIONS.md``. It is how much the system declines to commit to its
    own best answer, so §12's criterion 9 -- null and abstain mass exceeding any
    single defect's mass on S9 and S10 -- discriminates a calibrated report of
    insufficiency from a confident wrong one.
    """

    null_mass: Probability
    """Posterior mass on the null hypothesis, the empty edit set."""

    proposed_edits: FrozenDict[HypothesisId, Defect]
    """Structures the system itself introduced. Empty for a closed-set system.

    The one field a system authors, and legitimately: structure is what agents
    write.
    """

    supporting: FrozenDict[HypothesisId, tuple[ExperimentId, ...]]
    """Per hypothesis, the recorded experiments whose evidence favoured it."""

    residual_candidates: tuple[HypothesisId, ...]
    """Hypotheses surfaced as worth further work but not committed to."""

    def __post_init__(self) -> None:
        for name, mass in (
            ("abstain_mass", self.abstain_mass),
            ("null_mass", self.null_mass),
        ):
            if not 0.0 <= mass <= 1.0:
                raise DiagnosisError(f"{name} must lie in [0, 1], got {mass!r}")
        for node_id in sorted(self.distribution):
            value = self.distribution[node_id]
            if not 0.0 <= value <= 1.0:
                raise DiagnosisError(
                    f"hypothesis {node_id!r} carries mass {value!r}, which is not a "
                    f"probability"
                )
        if self.distribution:
            total = math.fsum(
                self.distribution[node_id] for node_id in sorted(self.distribution)
            )
            if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
                raise DiagnosisError(
                    f"a diagnosis must carry a normalised distribution, got a total "
                    f"of {total!r} over {len(self.distribution)} hypotheses"
                )
