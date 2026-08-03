"""Negative control for acceptance test A17. **Never imported by ``src``.**

The plausibility counterpart of :mod:`holdout_violator`. This module pretends to
be part of the agent tool surface and writes a plausibility through one level of
indirection. A17 asserts that the call-graph analyser finds the path here, which
is what stops the analyser's silence over ``src`` from being vacuous: until
backlog item 12 there is no real agent surface to walk, so an analyser that
returned "no violations" unconditionally would pass the gate while checking
nothing.

The write is spelled the way a real one would be. ``HypothesisNode`` is a frozen
dataclass, so an agent-authored tool could not assign to the attribute; it would
have to reach past the constructor with ``dataclasses.replace``, where the symbol
appears as a keyword argument rather than as an attribute or a name. Catching
that is the case the analyser has to handle, and the reason it looks at keywords
and parameter names as well as at references.
"""

from __future__ import annotations

from dataclasses import replace

from sciagent.core.types import HypothesisId, Probability
from sciagent.hypothesis.graph import HypothesisGraph, HypothesisNode


def agent_scores(
    graph: HypothesisGraph, node_id: HypothesisId, belief: float
) -> HypothesisNode:
    """The declared entry point. Reaches the prior only via :func:`_forge`."""
    return _forge(graph.node(node_id), belief)


def _forge(node: HypothesisNode, belief: float) -> HypothesisNode:
    return replace(node, plausibility=Probability(belief))
