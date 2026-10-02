"""SPEC §4.3 score 4: the efficiency curve, best-so-far held-out gap vs fits used.

A system's :class:`~sciagent.systems.v2.systems.SystemResult` carries its
trajectory: ``trajectory[i]`` is the model the system itself ranked best
(by its own criterion, BIC) after ``fits_used`` fits. The curve evaluates each
point's ``best_model`` on the held-out data, which no system saw, and reports
``gap = LL_best - LL_oracle`` per event (``scoring/heldout.py``). "Best so far"
is the system's own choice, not the held-out maximum: the curve shows what
the system would have submitted at that budget, so a held-out-best model the
system did not prefer earns nothing.

Each distinct model is evaluated once. Distinctness is object identity: a
trajectory repeats the same model object while the best does not change, and
two equal fits arriving as different objects are simply evaluated twice (same
value). The cache lives for one call, so identity is never reused across
objects.

Points must have strictly increasing ``fits_used``. A system with no fits
(B-np) has an empty curve.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sciagent.glm.data import Dataset
from sciagent.scoring.errors import ScoringError
from sciagent.systems.v2.systems import SystemResult


@dataclass(frozen=True)
class EfficiencyPoint:
    """The best-so-far model after ``fits_used`` fits, and its held-out gap."""

    fits_used: int
    best: str
    per_event: float
    gap: float


def efficiency_curve(
    result: SystemResult, oracle_per_event: float, held_out: Sequence[Dataset]
) -> tuple[EfficiencyPoint, ...]:
    """The curve of ``result`` against the ORACLE's held-out per-event value."""
    if not held_out:
        raise ScoringError("no held-out data")
    cache: dict[int, float] = {}
    points: list[EfficiencyPoint] = []
    previous = 0
    for point in result.trajectory:
        if point.fits_used <= previous:
            raise ScoringError(
                f"trajectory fits_used must increase strictly; {previous} then "
                f"{point.fits_used}"
            )
        previous = point.fits_used
        key = id(point.best_model)
        if key not in cache:
            cache[key] = point.best_model.held_out_per_event(held_out)
        value = cache[key]
        points.append(
            EfficiencyPoint(
                point.fits_used, point.best, value, value - oracle_per_event
            )
        )
    return tuple(points)


def gap_at(curve: Sequence[EfficiencyPoint], fits: int) -> float | None:
    """The best-so-far gap within a budget of ``fits`` (None before the first fit)."""
    value: float | None = None
    for point in curve:
        if point.fits_used > fits:
            break
        value = point.gap
    return value
