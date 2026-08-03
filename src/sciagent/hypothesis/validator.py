"""Schema validation for hypotheses: falsifiability and duplicate detection.

Two acceptance criteria live here.

**A16, falsifiability.** A prediction's ``refutation`` must be satisfiable over
its diagnostic's declared range. The check is exact, not sampled -- see
:mod:`sciagent.core.conditions` for why that matters and how it is done. Three
neighbouring incoherences the same interval arithmetic decides for free are
reported alongside it under their own codes: a refutation that covers the whole
range, a refutation overlapping the prediction it accompanies, and a condition
nothing attainable could satisfy. They are separate codes rather than one
"invalid", so A16's gate measures the criterion A16 states and the rest are
reported rather than folded in.

**A18, duplicates.** Two hypotheses are the same hypothesis when their edit sets
are structurally identical. A :class:`~sciagent.core.edits.Defect` is a
``frozenset``, so ordering is already gone; what :func:`defect_signature` adds is
a *stable* encoding, independent of process and of the order parameters were
inserted in, that can be compared, stored and cited. It is computed over
:func:`~sciagent.core.edits.canonical` order and over exact float reprs, so two
edits one grid point apart never collide -- the nearest pair in the log-spaced
grids differ in the fourth decimal, and a signature over rounded values would
merge them.

Detection covers rejected nodes as well as live ones. Re-proposing something
already refuted is precisely how a zombie hypothesis enters a graph, and SPEC §12
asks for zero of those.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sciagent.core.conditions import covers, overlap, satisfiable_over
from sciagent.core.edits import Defect, canonical, sort_key
from sciagent.core.errors import UnknownMetricError
from sciagent.core.types import Digest, HypothesisId, Prediction, RejectionCode
from sciagent.registry.metrics import MetricRegistry

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checking only
    from sciagent.hypothesis.graph import HypothesisGraph


@dataclass(frozen=True, slots=True)
class Rejection:
    """One reason a prediction was refused, with the prose to explain it."""

    code: RejectionCode
    message: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The outcome of validating one prediction.

    Guarantees :attr:`rejections` is in a fixed order, so two reports over the
    same fault set compare equal and a recorded report is reproducible.
    """

    rejections: tuple[Rejection, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the prediction passed every check."""
        return not self.rejections

    @property
    def codes(self) -> tuple[RejectionCode, ...]:
        """Return the codes raised, in the order the checks are declared."""
        return tuple(rejection.code for rejection in self.rejections)

    @property
    def messages(self) -> tuple[str, ...]:
        """Return the rendered reasons, for an error message or a record."""
        return tuple(str(rejection) for rejection in self.rejections)


def validate_prediction(
    prediction: Prediction, metrics: MetricRegistry
) -> ValidationReport:
    """Return every way ``prediction`` fails to be a falsifiable claim.

    Guarantees the verdict is exact over the diagnostic's declared range: it is
    decided by interval arithmetic on the endpoints as given, so no condition is
    accepted or refused because of where a sample happened to land.

    Returns a report rather than raising, because a refusal is a datum in the
    investigation record. :meth:`~sciagent.hypothesis.graph.HypothesisGraph
    .propose` is what turns a failing report into an error.
    """
    try:
        spec = metrics.spec(prediction.diagnostic.name)
    except UnknownMetricError as exc:
        return ValidationReport((Rejection(RejectionCode.UNKNOWN_METRIC, str(exc)),))

    low, high = spec.low, spec.high
    rejections: list[Rejection] = []

    if not satisfiable_over(prediction.refutation, low, high):
        rejections.append(
            Rejection(
                RejectionCode.UNSATISFIABLE_REFUTATION,
                f"no value of {spec.ref} in its declared range {low}..{high} "
                f"satisfies the refutation {prediction.refutation!r}, so no "
                f"outcome could refute the hypothesis",
            )
        )
    elif covers(prediction.refutation, low, high):
        rejections.append(
            Rejection(
                RejectionCode.TAUTOLOGICAL_REFUTATION,
                f"every value of {spec.ref} in its declared range {low}..{high} "
                f"satisfies the refutation {prediction.refutation!r}, so the "
                f"prediction cannot survive any outcome",
            )
        )

    if not satisfiable_over(prediction.condition, low, high):
        rejections.append(
            Rejection(
                RejectionCode.UNSATISFIABLE_CONDITION,
                f"no value of {spec.ref} in its declared range {low}..{high} "
                f"satisfies the condition {prediction.condition!r}, so no "
                f"outcome could confirm the hypothesis",
            )
        )

    if overlap(prediction.condition, prediction.refutation, low, high):
        rejections.append(
            Rejection(
                RejectionCode.OVERLAPPING_REFUTATION,
                f"some value of {spec.ref} satisfies both the condition "
                f"{prediction.condition!r} and the refutation "
                f"{prediction.refutation!r}; one observation may not both "
                f"confirm and refute",
            )
        )

    return ValidationReport(tuple(rejections))


def _encode(defect: Defect) -> str:
    """Return the canonical text encoding of an edit set.

    Built from :func:`~sciagent.core.edits.sort_key`, so the encoding covers edit
    type, target, construct and every parameter, and nothing else. Floats go in
    via ``repr``, which round-trips exactly.
    """
    return "\n".join(repr(sort_key(edit)) for edit in canonical(defect))


def defect_signature(defect: Defect) -> Digest:
    """Return a stable content address for an edit set.

    Guarantees two edit sets share a signature if and only if they are
    structurally identical: independent of construction order, of parameter
    insertion order, of process and of ``PYTHONHASHSEED``.
    """
    payload = _encode(defect).encode("utf-8")
    return Digest(hashlib.blake2b(payload, digest_size=16).hexdigest())


def find_duplicate(graph: HypothesisGraph, defect: Defect) -> HypothesisId | None:
    """Return the id of a hypothesis already holding ``defect``, if one exists.

    Searches every node whatever its status, rejected included. Ties are broken
    by sorted id, so the answer does not depend on insertion order.
    """
    signature = defect_signature(defect)
    edits = graph.edits()
    for node_id in sorted(edits):
        if defect_signature(edits[node_id]) == signature:
            return node_id
    return None
