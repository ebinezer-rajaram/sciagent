"""The hypothesis graph: what is under investigation, and how likely it was a priori.

The graph is an immutable value. Every transition returns a new graph, so an
investigation's history is a sequence of graphs rather than a mutable object with
a log bolted on, and two graphs are comparable by ``==``.

Where the numbers come from
---------------------------

:attr:`HypothesisNode.plausibility` is the structural-complexity prior of SPEC §0:
``p(D) proportional to 2 ** -code_length(D)``, normalised over the graph. It is
*derived*, never supplied. There is no parameter to pass it through, no setter,
and no keyword argument anywhere in this module's public surface -- which is the
strongest form acceptance test A17 can take, because a write path that does not
exist cannot be reached.

That leaves one way to plant a number: reach past the constructor with
``object.__setattr__`` or ``dataclasses.replace`` on a node. :meth:`__post_init__`
re-derives the whole vector and compares, so a forged value is refused the next
time a graph is built from those nodes. Exact equality is the right comparison
here rather than a tolerance: the derivation is a pure function of the grammar and
the edits, run the same way both times, so under the determinism invariant the two
floats are bit-identical or something is wrong.

Rejection does not disturb the prior. The prior is a statement about structure;
rejecting a hypothesis is a statement about evidence. Renormalising away from a
rejected hypothesis would silently move mass to its neighbours on evidential
grounds through a channel that is supposed to be evidence-free.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import (
    DuplicateHypothesisError,
    PlausibilityWriteError,
    UnfalsifiableHypothesisError,
    UnknownHypothesisError,
)
from sciagent.core.types import (
    ExperimentId,
    FrozenDict,
    HypothesisId,
    HypothesisStatus,
    Prediction,
    PredictionId,
    Probability,
    RejectionCode,
)
from sciagent.hypothesis.validator import find_duplicate, validate_prediction
from sciagent.registry.metrics import MetricRegistry

#: Names whose appearance in an agent-reachable call path constitutes a write to
#: the framework's prior. Read by acceptance test A17's call-graph analyser, so
#: this tuple and the code are one declaration rather than two.
#:
#: It over-approximates in two directions on purpose. ``plausibility`` is listed
#: even though *reading* it is harmless, because a static analyser cannot tell a
#: read from a write and the safe direction to be wrong in is the noisy one.
#: ``set_plausibility`` and ``with_plausibility`` name methods that do not exist:
#: listing them means the gate fails the moment anyone adds one.
PLAUSIBILITY_SYMBOLS: tuple[str, ...] = (
    "plausibility",
    "set_plausibility",
    "with_plausibility",
    "_derive_plausibility",
)

#: The functions licensed to touch those symbols: the framework's own derivation.
#: A17's analyser exempts these three references and nothing else.
#:
#: Why a boundary is needed at all. SPEC's second invariant forbids an
#: agent-reachable path from *setting* plausibility, and a research system must
#: still be able to introduce a hypothesis -- which necessarily runs the
#: derivation, because that is the framework writing the number. Every correct
#: systems layer therefore has a path into ``_derive_plausibility``, so a gate
#: that forbids the path outright cannot be satisfied by any working design. This
#: names the one place the write is meant to happen, so every *other* path stays
#: forbidden.
#:
#: Why these three, and no more. ``_rebuilt`` derives the whole vector and is the
#: only writer; ``propose`` sets the placeholder ``_rebuilt`` immediately
#: overwrites; ``__post_init__`` re-derives and refuses a value that disagrees,
#: which is the runtime half of A17 and the thing that actually stops a planted
#: number. ``_derive_plausibility`` itself is deliberately absent -- it reads the
#: grammar and the edits and nothing else, so it needs no exemption, and leaving
#: it out means a plausibility write appearing *inside* it would still fail A17.
#:
#: Exempting a function's own references does not stop the analyser walking
#: through it, so anything these three call is still checked.
PLAUSIBILITY_DERIVATION: tuple[str, ...] = (
    "sciagent.hypothesis.graph.HypothesisGraph.__post_init__",
    "sciagent.hypothesis.graph.HypothesisGraph._rebuilt",
    "sciagent.hypothesis.graph.HypothesisGraph.propose",
)


@dataclass(frozen=True, slots=True)
class HypothesisNode:
    """One candidate explanation (SPEC §3.3).

    ``program_edit`` is ``None`` only before compilation. :meth:`HypothesisGraph
    .propose` requires a compiled defect, so today no such node is constructible;
    the prose-first proposal path that produces one arrives with backlog item 12.
    An uncompiled node has no structure, and therefore no code length and no
    derived prior, which is why the field's ``None`` case is not yet reachable.
    """

    id: HypothesisId
    program_edit: Defect | None
    status: HypothesisStatus
    predictions: tuple[PredictionId, ...]
    plausibility: Probability
    rationale: str
    rejection_reason: RejectionCode | None
    proposed_at: ExperimentId | None
    version: int


def _derive_plausibility(
    grammar: EditGrammar, nodes: Mapping[HypothesisId, HypothesisNode]
) -> Mapping[HypothesisId, Probability]:
    """Return the normalised structural prior over ``nodes``.

    Guarantees the result depends only on the grammar and on each node's
    ``program_edit`` -- never on a stored plausibility, so re-deriving cannot
    launder a planted value -- and that it is bit-reproducible: keys are summed in
    sorted order, so the total is the same float on every run and process.
    """
    weights = {
        node_id: math.exp2(-grammar.code_length(node.program_edit))
        for node_id, node in nodes.items()
        if node.program_edit is not None
    }
    total = math.fsum(weights[key] for key in sorted(weights))
    if total == 0.0:
        return {node_id: Probability(0.0) for node_id in nodes}
    return {
        node_id: Probability(weights[node_id] / total if node_id in weights else 0.0)
        for node_id in nodes
    }


@dataclass(frozen=True, slots=True)
class HypothesisGraph:
    """An immutable set of hypotheses with a derived prior over them.

    Guarantees that every stored plausibility is the value
    :func:`_derive_plausibility` computes -- checked on construction, so it holds
    for a graph however it was built -- that no two nodes carry structurally
    identical edit sets, and that every node has at least one prediction some
    attainable diagnostic value could refute.
    """

    grammar: EditGrammar
    metrics: MetricRegistry
    nodes: FrozenDict[HypothesisId, HypothesisNode] = field(default_factory=FrozenDict)
    predictions: FrozenDict[PredictionId, Prediction] = field(
        default_factory=FrozenDict
    )

    def __post_init__(self) -> None:
        derived = _derive_plausibility(self.grammar, self.nodes)
        for node_id in sorted(self.nodes):
            stored = self.nodes[node_id].plausibility
            if stored != derived[node_id]:
                raise PlausibilityWriteError(
                    f"hypothesis {node_id!r} stores plausibility {stored!r} but the "
                    f"structural prior derives {derived[node_id]!r}; plausibility is "
                    f"framework-written and no caller may supply it (SPEC §6.4 A17)"
                )

    @classmethod
    def empty(cls, grammar: EditGrammar, metrics: MetricRegistry) -> HypothesisGraph:
        """Return a graph with no hypotheses."""
        return cls(grammar=grammar, metrics=metrics)

    # -- queries -----------------------------------------------------------

    def node(self, node_id: HypothesisId) -> HypothesisNode:
        """Return one node, or raise a typed error."""
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise UnknownHypothesisError(
                f"hypothesis {node_id!r} is not in the graph; it holds "
                f"{sorted(self.nodes)!r}"
            ) from exc

    def edits(self) -> Mapping[HypothesisId, Defect]:
        """Return the compiled edit set of every node that has one."""
        return {
            node_id: node.program_edit
            for node_id, node in self.nodes.items()
            if node.program_edit is not None
        }

    def with_status(self, status: HypothesisStatus) -> tuple[HypothesisId, ...]:
        """Return the ids of every node in ``status``, in a fixed order."""
        return tuple(
            node_id
            for node_id in sorted(self.nodes)
            if self.nodes[node_id].status == status
        )

    @property
    def live(self) -> tuple[HypothesisId, ...]:
        """Return the ids of every hypothesis still under investigation."""
        return self.with_status("live")

    # -- transitions -------------------------------------------------------

    def propose(
        self,
        node_id: HypothesisId,
        *,
        program_edit: Defect,
        predictions: Sequence[Prediction],
        rationale: str = "",
        proposed_at: ExperimentId | None = None,
    ) -> HypothesisGraph:
        """Return this graph with one more hypothesis in it.

        Guarantees the edit set is licensed by the grammar, is not a duplicate of
        any node already present including rejected ones, and carries at least
        one refutable prediction. Guarantees also that the caller cannot
        influence the prior: there is no parameter for it, and the whole vector
        is re-derived from the grammar.

        ``proposed_at`` is the experiment after which the hypothesis appeared, or
        ``None`` if it was present from the start. It drives SPEC F9, which says
        lateness costs nothing in likelihood but does cost the right to a
        confirmatory claim without a prospectively registered experiment.
        """
        if node_id in self.nodes:
            raise DuplicateHypothesisError(
                f"hypothesis {node_id!r} is already in the graph"
            )
        self.grammar.validate_defect(program_edit)

        clash = find_duplicate(self, program_edit)
        if clash is not None:
            raise DuplicateHypothesisError(
                f"hypothesis {node_id!r} proposes the same structure as {clash!r} "
                f"(status {self.nodes[clash].status!r}); the two edit sets are "
                f"identical up to ordering (SPEC §6.4 A18)"
            )

        if not predictions:
            raise UnfalsifiableHypothesisError(
                f"hypothesis {node_id!r} carries no predictions "
                f"({RejectionCode.NO_PREDICTIONS.value}); SPEC §3.3 requires at "
                f"least one, since a hypothesis with none cannot be refuted"
            )
        for prediction in predictions:
            if prediction.hypothesis_id != node_id:
                raise UnfalsifiableHypothesisError(
                    f"prediction {prediction.id!r} claims hypothesis "
                    f"{prediction.hypothesis_id!r} but was offered for {node_id!r} "
                    f"({RejectionCode.MISMATCHED_HYPOTHESIS.value})"
                )
            report = validate_prediction(prediction, self.metrics)
            if not report.ok:
                raise UnfalsifiableHypothesisError(
                    f"prediction {prediction.id!r} of hypothesis {node_id!r} is "
                    f"rejected: {'; '.join(report.messages)}"
                )

        fresh = HypothesisNode(
            id=node_id,
            program_edit=program_edit,
            status="live",
            predictions=tuple(prediction.id for prediction in predictions),
            plausibility=Probability(0.0),
            rationale=rationale,
            rejection_reason=None,
            proposed_at=proposed_at,
            version=1,
        )
        return self._rebuilt(
            nodes={**self.nodes, node_id: fresh},
            predictions={
                **self.predictions,
                **{prediction.id: prediction for prediction in predictions},
            },
        )

    def reject(self, node_id: HypothesisId, reason: RejectionCode) -> HypothesisGraph:
        """Return this graph with ``node_id`` rejected, for a recorded reason."""
        return self._transition(node_id, "rejected", reason)

    def suspend(self, node_id: HypothesisId, reason: RejectionCode) -> HypothesisGraph:
        """Return this graph with ``node_id`` suspended pending further evidence."""
        return self._transition(node_id, "suspended", reason)

    def confirm(self, node_id: HypothesisId) -> HypothesisGraph:
        """Return this graph with ``node_id`` confirmed."""
        return self._transition(node_id, "confirmed", None)

    def reinstate(self, node_id: HypothesisId) -> HypothesisGraph:
        """Return this graph with ``node_id`` live again and its reason cleared."""
        return self._transition(node_id, "live", None)

    # -- internals ---------------------------------------------------------

    def _transition(
        self,
        node_id: HypothesisId,
        status: HypothesisStatus,
        reason: RejectionCode | None,
    ) -> HypothesisGraph:
        """Return this graph with one node's status changed and its version bumped."""
        current = self.node(node_id)
        moved = replace(
            current,
            status=status,
            rejection_reason=reason,
            version=current.version + 1,
        )
        return self._rebuilt(
            nodes={**self.nodes, node_id: moved}, predictions=dict(self.predictions)
        )

    def _rebuilt(
        self,
        *,
        nodes: Mapping[HypothesisId, HypothesisNode],
        predictions: Mapping[PredictionId, Prediction],
    ) -> HypothesisGraph:
        """Return a graph over ``nodes`` with the prior re-derived from scratch.

        The single place a stored prior is ever written, and it writes only what
        :func:`_derive_plausibility` returns.
        """
        derived = _derive_plausibility(self.grammar, nodes)
        priced = {
            node_id: replace(node, plausibility=derived[node_id])
            for node_id, node in nodes.items()
        }
        return HypothesisGraph(
            grammar=self.grammar,
            metrics=self.metrics,
            nodes=FrozenDict[HypothesisId, HypothesisNode](priced),
            predictions=FrozenDict[PredictionId, Prediction](predictions),
        )
