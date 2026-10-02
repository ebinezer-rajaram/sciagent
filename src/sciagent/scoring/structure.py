"""SPEC §4.3 score 2: structural recovery.

Exact recovery is equality of canonical hashes
(:func:`~sciagent.glm.canonical.structure_hash`, link included), so
equivalent proposals (reordered features, commuted products, distributed
gates) count as recovered. The distance is the feature-set metric
:func:`~sciagent.glm.distance.structure_distance`, in ``[0, 1]``.

A submission with no grammar structure (None: an out-of-grammar library
member won B-lib, or B-np, which proposes none) is a non-answer: not exact,
distance 1.0, the metric's maximum. B-np is not scored on structure at all
(SPEC §2.3); ``submitted=False`` lets a report drop such rows rather than
average a 1.0 it never earned or lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sciagent.glm.canonical import structure_hash
from sciagent.glm.distance import structure_distance
from sciagent.glm.grammar import Structure

#: The distance a non-answer scores: the metric's upper bound.
MAX_DISTANCE: Final = 1.0


@dataclass(frozen=True)
class StructuralScore:
    """Exact recovery, distance to the truth, and whether a structure was given."""

    exact: bool
    distance: float
    submitted: bool


def structural_recovery(
    submitted: Structure | None, truth: Structure
) -> StructuralScore:
    """Score 2 for ``submitted`` against ``truth`` (module docstring)."""
    if submitted is None:
        return StructuralScore(exact=False, distance=MAX_DISTANCE, submitted=False)
    exact = structure_hash(submitted) == structure_hash(truth)
    distance = 0.0 if exact else structure_distance(submitted, truth)
    return StructuralScore(exact=exact, distance=distance, submitted=True)
