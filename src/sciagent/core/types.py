"""Core value types and identifier aliases.

``Prediction`` (v1 SPEC §3.3) arrives here with backlog item 5, ``Diagnosis``
(v1 SPEC §3.4) with item 9, and ``Claim``, ``Estimand``, ``Intervention`` and
``Scope`` with item 10, which is the item whose verifier is the only thing that
reads them.

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
from sciagent.core.errors import DiagnosisError, EstimandError, MalformedClaimError

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
#: the address itself (v1 SPEC §6.3 A13). Each is a distinct type rather than a bare
#: ``str`` so that transposing two of them in a five-field key is a type error and
#: not a silently different content address.
EnvVersion = NewType("EnvVersion", str)
DataVersion = NewType("DataVersion", str)
MetricVersion = NewType("MetricVersion", str)
MetricName = NewType("MetricName", str)
Digest = NewType("Digest", str)

#: Identifiers for the investigation record (v1 SPEC §3.3).
HypothesisId = NewType("HypothesisId", str)
PredictionId = NewType("PredictionId", str)
ExperimentId = NewType("ExperimentId", str)

#: v1 SPEC §3.3 lists ``Claim`` without an id. One is carried for the same reason
#: :class:`Prediction` acquired one at item 5: a verdict has to name the claim it
#: is about, and the v1 contradiction check compared a claim against
#: the ones already accepted, which is not expressible over anonymous values.
ClaimId = NewType("ClaimId", str)

#: Which of v1 SPEC §4.5's twelve slice scenarios an investigation was run on.
ScenarioId = NewType("ScenarioId", str)

#: The identity of an experiment design (v1 SPEC §4.4), which is
#: :attr:`sciagent.experiments.dsl.ExperimentDesign.id` -- a readable canonical
#: rendering of the act and the diagnostics it is read over.
#:
#: v1 SPEC §3.3 writes ``Prediction.under: ExperimentTemplate``, i.e. the structure
#: itself. Backlog item 7 kept the id instead, deliberately. A ``Prediction`` is
#: a frozen value type that gets content-addressed, and embedding a whole design
#: in each one enlarges what is hashed while adding nothing: the id *is* the
#: design's canonical rendering, so naming it names the design uniquely. The
#: divergence from the specification's literal type is recorded in
#: ``docs/v1/DECISIONS.md``.
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
    """A metric named at a specific version (v1 SPEC §3.3).

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
    lines are the criterion v1 SPEC §6.4 A16 states; the rest are neighbouring
    incoherences the same interval arithmetic decides for free, kept separate so
    that A16's own gate measures exactly what A16 claims.
    """

    NO_PREDICTIONS = "no_predictions"
    """No prediction at all. v1 SPEC §3.3 requires at least one."""

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

    v1 SPEC §3.3 lists this without an ``id``, but
    :attr:`~sciagent.hypothesis.graph.HypothesisNode.predictions` holds
    ``PredictionId``s, so one is carried here.
    """

    id: PredictionId
    hypothesis_id: HypothesisId
    diagnostic: MetricRef
    condition: Condition
    under: ExperimentTemplateId
    refutation: Condition

    authored: bool = False
    """Whether the system supplied this condition rather than the framework.

    ``False`` for every prediction the framework derives, which is every
    prediction the conventional baselines made and every prediction in the
    recorded v1 campaign. ``True`` only where a system supplied the predictions
    explicitly.

    **Not a number, and not trusted from the caller.** The flag is a fact about
    provenance, so the second invariant is untouched; and
    the framework stamps it on every explicitly supplied prediction rather than
    reading what arrived, since a
    system able to set it would otherwise simply clear it.

    It exists so that a threshold a system chose for itself is *referred* rather
    than accepted: the v1 statistical check handed such a claim
    to a human instead of grading it, which is strictly gentler than the refusal
    the A34 criterion also permits.

    **Be precise about what that costs, because the obvious gentler reading is
    false.** The framework stamps a node's
    predictions in one call and nothing adds more to a node afterwards, so every
    prediction of a node carries the same flag. A hypothesis proposed with any
    explicit prediction therefore cannot carry an *adjudicated* prediction-channel
    claim at all -- not a narrower one, not any.

    **And the trade is not only a cost, which is the part a first reading of this
    missed.** ``REFER`` is not merely "not adjudicated": it is also not
    accepted, so the claim never enters
    the accepted population the v1 contradiction check read.
    Authoring predictions therefore *lowers* the contradiction count v1 SPEC §12
    criterion 8 asks to be zero, by making the system's own earlier claims
    unadmittable — a criterion paid on a different criterion's account. It cannot
    fire today: no shipped system supplies predictions, and
    :meth:`~sciagent.hypothesis.graph.HypothesisGraph.relate` has no caller in
    ``src`` at all, so no ``CONTRADICTS`` edge exists in any recorded campaign.
    Both surfaces are agent-facing, so this is a design hole held shut by two
    absences rather than by anything structural; ``docs/v1/DECISIONS.md``
    (2026-08-23) records it.
    """


# --------------------------------------------------------------------------
# Diagnosis
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """What one research system concluded about one scenario (v1 SPEC §3.4).

    A value type and nothing more: every number in it is derived by
    the framework from a posterior engine, and no
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

    v1 SPEC §3.4 names the field without defining it; this reading is recorded in
    ``docs/v1/DECISIONS.md``. It is how much the system declines to commit to its
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


# --------------------------------------------------------------------------
# Estimands (v1 SPEC §3.3, §7.2)
# --------------------------------------------------------------------------


class Direction(Enum):
    """Which way an effect went, or was preregistered to go."""

    INCREASE = "increase"
    DECREASE = "decrease"
    NO_CHANGE = "no_change"


class AssumptionCode(Enum):
    """A declaration the claimant makes that no experiment can establish.

    Exactly the codes v1 SPEC §7.2's licensing table reads, and no others. An
    assumption vocabulary is a place where unread entries accumulate and start to
    look like guarantees, so a code enters this enum when the rule that reads it
    does, and not before.
    """

    MEDIATORS_BLOCKED = "mediators_blocked"
    """Every mediating path from target to outcome is blocked. Required by the
    direct-effect and controlled-direct-effect rows of §7.2."""

    HELD_FIXED = "held_fixed"
    """The components named by a controlled direct effect were held fixed. §7.2
    requires this to be true of the *executed* experiment, so the verifier checks
    it against the record rather than taking the declaration -- the code says what
    is being claimed, and the evidence says whether it happened."""

    OFF_PATH_CONTROLLED = "off_path_controlled"
    """Descendants off the claimed path were controlled rather than left free.
    The path-specific row's alternative to not manipulating them."""


@dataclass(frozen=True, slots=True)
class TotalEffect:
    """The whole effect of ``target`` on ``outcome``, collateral paths included."""

    target: ComponentId
    outcome: ComponentId

    def __post_init__(self) -> None:
        _check_endpoints(self.target, self.outcome)


@dataclass(frozen=True, slots=True)
class DirectEffect:
    """The effect not carried by any mediator, with the mediators declared."""

    target: ComponentId
    outcome: ComponentId
    mediators_blocked: frozenset[ComponentId]

    def __post_init__(self) -> None:
        _check_endpoints(self.target, self.outcome)


@dataclass(frozen=True, slots=True)
class ControlledDirectEffect:
    """A direct effect with the mediators held at declared values."""

    target: ComponentId
    outcome: ComponentId
    held_fixed: frozenset[ComponentId]

    def __post_init__(self) -> None:
        _check_endpoints(self.target, self.outcome)


@dataclass(frozen=True, slots=True)
class PathSpecificEffect:
    """The effect carried by one named path through the programme DAG."""

    target: ComponentId
    outcome: ComponentId
    path: tuple[ComponentId, ...]

    def __post_init__(self) -> None:
        _check_endpoints(self.target, self.outcome)
        if len(self.path) < 2:
            raise EstimandError(
                f"path-specific effect of {self.target!r} on {self.outcome!r} "
                f"declares the path {self.path!r}, which names fewer than two "
                f"components; a path is a sequence of edges"
            )
        if self.path[0] != self.target or self.path[-1] != self.outcome:
            raise EstimandError(
                f"path {self.path!r} does not run from {self.target!r} to "
                f"{self.outcome!r}; a path-specific effect is an effect along the "
                f"path it names"
            )
        if len(set(self.path)) != len(self.path):
            raise EstimandError(
                f"path {self.path!r} repeats a component; a path through a DAG "
                f"visits each node at most once"
            )


def _check_endpoints(target: ComponentId, outcome: ComponentId) -> None:
    """Raise unless an estimand's two endpoints are distinct components.

    Whether they are *connected* is a question about a programme and belongs to
    the v1 causal verifier. Whether they are the same component is a
    question about the estimand alone, and an effect of a thing on itself is not
    one a licensing rule could either grant or refuse.
    """
    if target == outcome:
        raise EstimandError(
            f"an estimand names {target!r} as both its target and its outcome; an "
            f"effect of a component on itself is not an effect"
        )


type Estimand = TotalEffect | DirectEffect | ControlledDirectEffect | PathSpecificEffect

#: Every estimand type, in a fixed order independent of import or hash order.
ESTIMAND_TYPES: tuple[type, ...] = (
    TotalEffect,
    DirectEffect,
    ControlledDirectEffect,
    PathSpecificEffect,
)


def estimand_endpoints(estimand: Estimand) -> tuple[ComponentId, ComponentId]:
    """Return an estimand's ``(target, outcome)`` whatever its type."""
    return estimand.target, estimand.outcome


@dataclass(frozen=True, slots=True)
class Intervention:
    """What was done, what it reached, and what is being claimed from it.

    ``collateral`` is derived from the programme DAG (v1 SPEC §3.3), never declared:
    :attr:`~sciagent.experiments.executor.ExecutionResult.collateral` is where it
    comes from, and the v1 causal verifier compared the two rather than
    trusting this field.
    """

    target: ComponentId
    manipulated: frozenset[ComponentId]
    estimand: Estimand
    collateral: frozenset[ComponentId]
    assumptions: tuple[AssumptionCode, ...]
    expected_direction: Direction
    """Preregistered. Compared against the measured direction, so an intervention
    whose result went the other way cannot be reported as confirming it."""


# --------------------------------------------------------------------------
# Claims (v1 SPEC §3.3, §8)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a claim asserts it holds (v1 SPEC §7.1 clause 3).

    The three axes §7.1 names -- family, parameter range, environment version --
    plus the metric and grammar versions §7.1 says relevance is computed at.
    Those two live here rather than on :class:`Claim` because they are part of
    *where* a claim holds in exactly the way the other three are: a claim made
    under one diagnostic catalogue is not a claim about another.
    """

    families: frozenset[FamilyId]
    parameters: FrozenDict[str, tuple[float, float]]
    env_version: EnvVersion
    metric_version: MetricVersion
    grammar_version: GrammarVersion

    def __post_init__(self) -> None:
        for name in sorted(self.parameters):
            low, high = self.parameters[name]
            if math.isnan(low) or math.isnan(high) or not high >= low:
                raise MalformedClaimError(
                    f"scope declares parameter {name!r} over {low!r}..{high!r}, "
                    f"which is not a range; a claim must say where it holds"
                )


@dataclass(frozen=True, slots=True)
class EffectEstimate:
    """A measured effect. **Framework-written** (SPEC F7).

    Produced only by the framework (v1's ``verify.numerical.recompute``), from
    registered experiment results. No system supplies one: there is no argument
    anywhere in the framework's public surface that accepts an effect, and
    v1's numerical verifier re-derived every field of whatever a claim
    carries and refuses a claim whose figures are not bit-identical to it. That
    check is acceptance test A19.

    Well-formedness is checked here; *correctness* deliberately is not. A
    reversed interval, a negative standard error and an impossible replicate
    count are all corruptions A19 requires the **verifier** to catch, so refusing
    them at construction would move the gate off the subsystem it is a gate on
    and leave a corrupted claim unable to exist rather than caught. Only NaN and
    infinity are refused, because those do not denote a measurement at all.
    """

    metric: MetricName
    point: float
    standard_error: float
    low: float
    high: float
    level: float
    """Nominal coverage of ``low..high``, e.g. ``0.95``."""

    n_treated: int
    n_control: int
    direction: Direction

    def __post_init__(self) -> None:
        for name, value in (
            ("point", self.point),
            ("standard_error", self.standard_error),
            ("low", self.low),
            ("high", self.high),
            ("level", self.level),
        ):
            if not math.isfinite(value):
                raise MalformedClaimError(
                    f"effect on {self.metric} carries {name}={value!r}; a "
                    f"non-finite figure does not denote a measured effect"
                )
        if not 0.0 < self.level < 1.0:
            raise MalformedClaimError(
                f"effect on {self.metric} declares coverage {self.level!r}; a "
                f"nominal level lies strictly between 0 and 1"
            )


ClaimModality = Literal["correlational", "mechanistic", "causal", "predictive"]

#: Every modality, in the canonical order used for display and enumeration.
CLAIM_MODALITIES: tuple[ClaimModality, ...] = (
    "correlational",
    "mechanistic",
    "causal",
    "predictive",
)

ClaimStrength = Literal["suggests", "supports", "establishes", "refutes"]

#: Every strength, weakest assertion first. The order is load-bearing:
#: v1's statistical verifier read it to decide whether the evidence
#: reaches the strength claimed.
CLAIM_STRENGTHS: tuple[ClaimStrength, ...] = (
    "suggests",
    "supports",
    "establishes",
    "refutes",
)

ClaimPartition = Literal["exploratory", "confirmatory"]

#: v1 SPEC §3.3's evidential axis, which is *not* the registry's data partition.
#: See ``docs/v1/DECISIONS.md``, item 4: a confirmatory claim can rest on DEV data.
CLAIM_PARTITIONS: tuple[ClaimPartition, ...] = ("exploratory", "confirmatory")

SubjectKind = Literal["hypothesis", "component"]

Uniqueness = Literal["exclusive", "non_exclusive"]


@dataclass(frozen=True, slots=True)
class Claim:
    """One typed assertion, and everything the verifier judges it on (v1 SPEC §3.3).

    ``prose`` is a rendering and is never scored (SPEC F8); whether it is
    faithful to the rest is R6's open question and not mechanical.

    Two fields v1 SPEC §3.3 does not list. ``subject_kind`` is needed because
    :class:`HypothesisId` and :class:`ComponentId` are both ``NewType``\\ s over
    ``str`` and therefore indistinguishable at runtime: a verifier that must look
    the subject up in either the hypothesis graph or the programme cannot tell
    from the value which one to ask. ``intervention`` is where §7.2's "declared
    blocked" mediators and "listed" assumptions live -- they are declarations the
    claimant makes, so no experiment record could supply them, and without them
    the causal licensing table has nothing to read.
    """

    id: ClaimId
    subject: HypothesisId | ComponentId
    subject_kind: SubjectKind
    modality: ClaimModality
    estimand: Estimand | None
    strength: ClaimStrength
    scope: Scope
    evidence: tuple[ExperimentId, ...]
    partition: ClaimPartition
    effect: EffectEstimate | None
    uniqueness: Uniqueness
    prose: str
    intervention: Intervention | None = None
