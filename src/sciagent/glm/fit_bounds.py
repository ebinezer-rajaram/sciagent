"""A history-free lower bound on the fitted intensity: is the model simulable?

The fitter enforces λ ≥ 0 on the observed data only (quadrature nodes and
event right limits, ``fit_solve``). Under the identity link a fitted model
with a negative coefficient on an unbounded column, e.g. an inhibitory
``Excite``, is non-negative on the data it was fitted to but goes negative on
a history with enough events in a burst, and the simulator then raises
``NegativeIntensityError``. That model is still the maximum-likelihood
structure on the data, and inhibition is a real mechanism, so it is not
forbidden. Instead :func:`intensity_floor` bounds ``θ₀ + Σ θⱼ φⱼ`` below
over **every** history and mark sequence, and a fit is ``simulable`` iff that
bound is ≥ 0. Exp and softplus are positive for any predictor, so always
simulable (positivity only; an exp-link model can still explode).

The bound is interval arithmetic over each feature's column ranges:

- ``Excite``: kernels are non-negative and unbounded above (events can pile
  up), so ``[0, ∞)`` for a non-negative mark function (``One``, ``Pow``,
  ``ExpOf``, ``Above``, ``Mark`` of a positive channel) and ``(-∞, ∞)`` for
  ``Mark`` of a real or sign channel;
- ``Periodic``: each column ``[-1, 1]``; a bare (or gated) ``Periodic`` is
  bounded jointly, ``a sin + b cos ≥ -√(a² + b²)``;
- ``Trend``: ``[0, 1]``; ``Product``: interval products, row-major;
  ``Gate``: the inner range joined with 0.

It is sufficient, not necessary (columns are treated as independent), and
exact for the common cases: non-negative coefficients on ``Excite`` columns,
and ``Periodic`` on its own.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from sciagent.glm.grammar import (
    Above,
    ChannelKind,
    ChannelSpec,
    Excite,
    ExpOf,
    Feature,
    Gate,
    Link,
    Mark,
    One,
    Periodic,
    Pow,
    Product,
    Structure,
    Trend,
    n_columns,
)

type _Range = tuple[float, float]


def _mul(x: float, y: float) -> float:
    """Product with ``0 · ∞ = 0`` (a column that is 0 contributes nothing)."""
    if x == 0.0 or y == 0.0:
        return 0.0
    return x * y


def _range_mul(a: _Range, b: _Range) -> _Range:
    products = [_mul(x, y) for x in a for y in b]
    return min(products), max(products)


def _ranges(feature: Feature, kinds: dict[str, ChannelKind]) -> list[_Range]:
    """Per-column ranges over every history (row-major for products)."""
    match feature:
        case Excite(mark=mark):
            match mark:
                case One() | Pow() | ExpOf() | Above():
                    return [(0.0, math.inf)]
                case Mark(channel=c):
                    if kinds[c] is ChannelKind.POSITIVE:
                        return [(0.0, math.inf)]
                    return [(-math.inf, math.inf)]
        case Periodic():
            return [(-1.0, 1.0), (-1.0, 1.0)]
        case Trend():
            return [(0.0, 1.0)]
        case Product(left=left, right=right):
            return [
                _range_mul(a, b)
                for a in _ranges(left, kinds)
                for b in _ranges(right, kinds)
            ]
        case Gate(feature=inner):
            return [(min(0.0, lo), max(0.0, hi)) for lo, hi in _ranges(inner, kinds)]


def _bare_periodic(feature: Feature) -> bool:
    while isinstance(feature, Gate):
        feature = feature.feature
    return isinstance(feature, Periodic)


def _feature_floor(
    feature: Feature, theta: Sequence[float], kinds: dict[str, ChannelKind]
) -> float:
    if _bare_periodic(feature):
        a, b = theta
        return -math.hypot(a, b)  # a gate only adds the value 0
    floor = 0.0
    for coef, (lo, hi) in zip(theta, _ranges(feature, kinds), strict=True):
        floor += min(_mul(coef, lo), _mul(coef, hi))
    return floor


def intensity_floor(
    structure: Structure, theta: Sequence[float], channels: tuple[ChannelSpec, ...]
) -> float:
    """A lower bound on the linear predictor ``θ₀ + Σ θⱼ φⱼ`` over every
    history (``-inf`` when it is unbounded below)."""
    kinds = {c.name: c.kind for c in channels}
    floor = float(theta[0])
    start = 1
    for feature in structure.features:
        width = n_columns(feature)
        floor += _feature_floor(feature, theta[start : start + width], kinds)
        start += width
    return floor


def simulable(
    structure: Structure, theta: Sequence[float], channels: tuple[ChannelSpec, ...]
) -> bool:
    """λ ≥ 0 under every history: always for exp and softplus; for the
    identity link iff :func:`intensity_floor` is ≥ 0."""
    if structure.link is not Link.IDENTITY:
        return True
    return intensity_floor(structure, theta, channels) >= 0.0
