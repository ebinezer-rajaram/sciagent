"""The hypothesis graph and its schema validation (SPEC §3.3, §6.4).

Two things live here: the immutable graph of candidate explanations, and the
checks that stop an unfalsifiable or duplicate hypothesis entering it. The prior
over the graph is derived from the edit grammar's prefix code and cannot be
supplied by any caller -- see :mod:`sciagent.hypothesis.graph`.
"""

from __future__ import annotations

from sciagent.hypothesis.graph import (
    PLAUSIBILITY_SYMBOLS,
    HypothesisGraph,
    HypothesisNode,
    Relation,
)
from sciagent.hypothesis.validator import (
    Rejection,
    ValidationReport,
    defect_signature,
    find_duplicate,
    validate_prediction,
)

__all__ = [
    "PLAUSIBILITY_SYMBOLS",
    "HypothesisGraph",
    "HypothesisNode",
    "Rejection",
    "Relation",
    "ValidationReport",
    "defect_signature",
    "find_duplicate",
    "validate_prediction",
]
