"""Scoring a diagnosis (SPEC §8).

Item 9 implements the closed-world case only, which is the one S1-S10 are
scored under: a proper score over the closed set. D1-D6 are for the open-world
scenarios (S8, S11) and arrive with the items those scenarios belong to; SPEC §8
says in as many words that D1-D6 are not applicable to the closed-world task.

Never collapsed into one number, per §8. Even here, where there *is* only one
number, it is returned alongside the quantities it was computed from, so a
headline figure can say which dimension it is.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from sciagent.core.edits import Defect
from sciagent.core.errors import DiagnosisError
from sciagent.core.types import Diagnosis, HypothesisId, Probability
from sciagent.experiments.dsl import defect_key

__all__ = ["ClosedWorldScore", "closed_world_score"]


@dataclass(frozen=True, slots=True)
class ClosedWorldScore:
    """A proper score over a closed hypothesis set, and its parts.

    ``log_score`` is ``log2 p(truth)``: zero when the truth is held with
    certainty and increasingly negative as mass moves away from it. Proper, so a
    system cannot improve it by reporting anything other than its actual belief,
    which is the property that makes it safe to compare systems on.
    """

    truth_mass: Probability
    """Posterior mass the system put on the true structure."""

    log_score: float
    """``log2`` of :attr:`truth_mass`, in bits. ``-inf`` if the truth got zero."""

    leading_mass: Probability
    """Mass on whichever hypothesis led, true or not."""

    correct: bool
    """Whether the leading hypothesis is the true one."""

    identified: bool
    """Whether the truth led *and* carried more than half the mass.

    Stricter than :attr:`correct`, and the honest reading of "recovered the
    diagnosis" when the alternative is a three-way split whose winner leads by a
    rounding error.
    """


def closed_world_score(
    diagnosis: Diagnosis,
    truth: Defect,
    program_edit: Mapping[HypothesisId, Defect],
) -> ClosedWorldScore:
    """Score a diagnosis against the structure the environment actually had.

    Matching is by :func:`~sciagent.experiments.dsl.defect_key`, so a system that
    proposed the true structure under its own name is credited for it -- which is
    the whole point of allowing systems to propose. Mass on several hypotheses
    holding the same structure is summed.

    Guarantees the score is a pure function of its arguments. Raises
    :class:`~sciagent.core.errors.DiagnosisError` if the distribution names a
    hypothesis ``program_edit`` does not, since a mass on an unidentifiable
    structure could otherwise be silently scored as zero.
    """
    wanted = defect_key(truth)
    truth_mass = 0.0
    leading_mass = 0.0
    leader: HypothesisId | None = None
    for node_id in sorted(diagnosis.distribution):
        mass = float(diagnosis.distribution[node_id])
        if node_id not in program_edit:
            raise DiagnosisError(
                f"diagnosis puts mass {mass!r} on hypothesis {node_id!r}, whose "
                f"structure is unknown to the scorer; it knows "
                f"{sorted(program_edit)!r}"
            )
        if defect_key(program_edit[node_id]) == wanted:
            truth_mass += mass
        if mass > leading_mass:
            leading_mass, leader = mass, node_id
    correct = leader is not None and defect_key(program_edit[leader]) == wanted
    return ClosedWorldScore(
        truth_mass=Probability(truth_mass),
        log_score=math.log2(truth_mass) if truth_mass > 0.0 else -math.inf,
        leading_mass=Probability(leading_mass),
        correct=correct,
        identified=correct and truth_mass > 0.5,
    )
