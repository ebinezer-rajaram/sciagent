"""The v2 feature grammar (SPEC §2.1): the structure an agent proposes.

A :class:`Structure` is a link function and a tuple of :data:`Feature` trees.
The trees carry **no numbers**. Every continuous quantity a feature needs (a
kernel's decay, a period, a threshold) is a *shape parameter* ψ that the
framework profiles over a fixed grid (``grids.py``); the coefficients θ are
fitted. That is invariant 2 made structural: an agent cannot write a number.

The intensity is ``λ(t | H_t) = g(θ₀ + Σₖ θₖ · φₖ(t; H_t, ψₖ))``. A feature can
contribute more than one column to the design (``Periodic`` contributes a sine
and a cosine), so ``θₖ`` is a vector; :func:`n_columns` says how long.

Semantics, fixed here so every module agrees:

- ``Excite(kernel, mark, source)`` at time t is
  ``Σ_{t_j < t, j ∈ source} mark(m_j) · kernel(t - t_j; ψ)``. Kernels are
  probability densities on ``[0, ∞)`` (they integrate to 1), so under the
  identity link θ is a branching ratio.
- Kernels: ``EXP`` is ``β e^{-βt}`` (ψ: ``exp_rate``); ``POWER`` is the Lomax
  density ``((p-1)/c)(1 + t/c)^{-p}`` (ψ: ``power_c``, ``power_p``); ``GAMMA``
  is the gamma density with shape k and mean μ (ψ: ``gamma_shape``,
  ``gamma_mean``).
- Mark functions read channel ``c``, standardised by the environment's fixed
  :class:`ChannelSpec` as ``z = (m - location) / scale``:
  ``One`` is 1; ``Mark`` is m itself; ``Pow`` is ``(m / location)^a`` (ψ:
  ``pow_exponent``; positive channels only); ``ExpOf`` is ``exp(a z)`` (ψ:
  ``exp_coef``); ``Above`` is ``1[z > q]`` (ψ: ``above_z``).
- ``Source``: ``ALL`` takes every past event; ``POSITIVE`` / ``NEGATIVE``
  take the events whose sign channel is ``+1`` / ``-1``.
- ``Periodic`` is the pair ``(sin 2πt/P, cos 2πt/P)`` (ψ: ``period``).
- ``Trend`` is ``t / T`` with T the horizon of the data being fitted.
- ``Product(a, b)`` is the elementwise product of every column of ``a`` with
  every column of ``b`` (row-major: a's columns outer, b's inner). Still linear
  in θ.
- ``Gate(f, cond)`` is ``f``'s columns multiplied by the indicator of ``cond``.
  ``LastMarkAbove(c)`` holds while the most recent event before t has
  ``z > q`` (ψ: ``above_z``; false before the first event).
  ``PhaseWindow`` holds while ``sin(2πt/P - φ) ≥ 0`` (ψ: ``period``,
  ``phase``), i.e. a half-cycle window.

Depth counts feature nodes only: ``Excite``, ``Periodic`` and ``Trend`` have
depth 1; ``Product`` and ``Gate`` add one to their deepest child.

This module is pure: no I/O, no numerics, no randomness.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Final

from sciagent.core.errors import GrammarError

MAX_DEPTH: Final = 3
MAX_FEATURES: Final = 4


class InvalidStructureError(GrammarError):
    """A structure is ill-formed for the channels it is used with."""


class Link(Enum):
    IDENTITY = "identity"
    EXP = "exp"
    SOFTPLUS = "softplus"


class KernelKind(Enum):
    EXP = "ExpK"
    POWER = "PowerK"
    GAMMA = "GammaK"


class ChannelKind(Enum):
    POSITIVE = "positive"  # strictly positive reals (size, energy)
    REAL = "real"  # any real (magnitude)
    SIGN = "sign"  # values in {-1, +1}


@dataclass(frozen=True)
class ChannelSpec:
    """A mark channel, with a fixed standardisation chosen by the environment.

    ``location`` and ``scale`` are environment constants, not data statistics,
    so a fitted threshold means the same thing on every dataset.
    """

    name: str
    kind: ChannelKind
    location: float
    scale: float


# --------------------------------------------------------------------------
# Mark functions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class One:
    pass


@dataclass(frozen=True)
class Mark:
    channel: str


@dataclass(frozen=True)
class Pow:
    channel: str


@dataclass(frozen=True)
class ExpOf:
    channel: str


@dataclass(frozen=True)
class Above:
    channel: str


type MarkFn = One | Mark | Pow | ExpOf | Above


# --------------------------------------------------------------------------
# Sources and conditions
# --------------------------------------------------------------------------


class SourceKind(Enum):
    ALL = "all"
    POSITIVE = "+"
    NEGATIVE = "-"


@dataclass(frozen=True)
class Source:
    """Which past events excite. ``channel`` is the sign channel, or None for ALL."""

    kind: SourceKind
    channel: str | None = None


ALL: Final = Source(SourceKind.ALL)


@dataclass(frozen=True)
class LastMarkAbove:
    channel: str


@dataclass(frozen=True)
class PhaseWindow:
    pass


type Cond = LastMarkAbove | PhaseWindow


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Excite:
    kernel: KernelKind
    mark: MarkFn
    source: Source


@dataclass(frozen=True)
class Periodic:
    pass


@dataclass(frozen=True)
class Trend:
    pass


@dataclass(frozen=True)
class Product:
    left: Feature
    right: Feature


@dataclass(frozen=True)
class Gate:
    feature: Feature
    cond: Cond


type Feature = Excite | Periodic | Trend | Product | Gate


@dataclass(frozen=True)
class Structure:
    """What an agent submits: a link and a feature set. θ₀ is always present."""

    features: tuple[Feature, ...]
    link: Link = Link.IDENTITY


# --------------------------------------------------------------------------
# Shape parameters: where ψ lives in a tree
# --------------------------------------------------------------------------

#: ψ parameter names per node type, in a fixed order.
KERNEL_PSI: Final[dict[KernelKind, tuple[str, ...]]] = {
    KernelKind.EXP: ("exp_rate",),
    KernelKind.POWER: ("power_c", "power_p"),
    KernelKind.GAMMA: ("gamma_shape", "gamma_mean"),
}


@dataclass(frozen=True, order=True)
class PsiSlot:
    """One shape parameter inside one feature tree.

    ``path`` addresses the node from the feature root by child index:
    ``Product`` → 0 left, 1 right; ``Gate`` → 0 feature, 1 cond;
    ``Excite`` → 0 kernel, 1 mark. ``name`` is the parameter name.
    """

    path: tuple[int, ...]
    name: str


def _mark_psi(mark: MarkFn) -> tuple[str, ...]:
    match mark:
        case Pow():
            return ("pow_exponent",)
        case ExpOf():
            return ("exp_coef",)
        case Above():
            return ("above_z",)
        case One() | Mark():
            return ()


def _cond_psi(cond: Cond) -> tuple[str, ...]:
    match cond:
        case LastMarkAbove():
            return ("above_z",)
        case PhaseWindow():
            return ("period", "phase")


def psi_slots(feature: Feature) -> tuple[PsiSlot, ...]:
    """Every ψ slot of a feature tree, in a deterministic pre-order."""
    return tuple(_psi_slots(feature, ()))


def _psi_slots(feature: Feature, path: tuple[int, ...]) -> Iterator[PsiSlot]:
    match feature:
        case Excite(kernel=kernel, mark=mark):
            for name in KERNEL_PSI[kernel]:
                yield PsiSlot((*path, 0), name)
            for name in _mark_psi(mark):
                yield PsiSlot((*path, 1), name)
        case Periodic():
            yield PsiSlot(path, "period")
        case Trend():
            return
        case Product(left=left, right=right):
            yield from _psi_slots(left, (*path, 0))
            yield from _psi_slots(right, (*path, 1))
        case Gate(feature=inner, cond=cond):
            yield from _psi_slots(inner, (*path, 0))
            for name in _cond_psi(cond):
                yield PsiSlot((*path, 1), name)


# --------------------------------------------------------------------------
# Shape queries
# --------------------------------------------------------------------------


def depth(feature: Feature) -> int:
    match feature:
        case Excite() | Periodic() | Trend():
            return 1
        case Product(left=left, right=right):
            return 1 + max(depth(left), depth(right))
        case Gate(feature=inner):
            return 1 + depth(inner)


def n_columns(feature: Feature) -> int:
    """How many design columns (θ entries) the feature contributes."""
    match feature:
        case Excite() | Trend():
            return 1
        case Periodic():
            return 2
        case Product(left=left, right=right):
            return n_columns(left) * n_columns(right)
        case Gate(feature=inner):
            return n_columns(inner)


def channels_used(feature: Feature) -> frozenset[str]:
    match feature:
        case Excite(mark=mark, source=source):
            used: set[str] = set()
            if not isinstance(mark, One):
                used.add(mark.channel)
            if source.channel is not None:
                used.add(source.channel)
            return frozenset(used)
        case Periodic() | Trend():
            return frozenset()
        case Product(left=left, right=right):
            return channels_used(left) | channels_used(right)
        case Gate(feature=inner, cond=cond):
            extra = {cond.channel} if isinstance(cond, LastMarkAbove) else set()
            return channels_used(inner) | extra


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate(structure: Structure, channels: tuple[ChannelSpec, ...]) -> None:
    """Raise :class:`InvalidStructureError` unless ``structure`` is well-formed.

    Checks: 0..MAX_FEATURES features (zero is the null, intercept-only model),
    depth ≤ MAX_DEPTH, every channel exists,
    each mark function / source / condition is applied to a channel kind it is
    defined for.
    """
    by_name = {spec.name: spec for spec in channels}
    n = len(structure.features)
    if not 0 <= n <= MAX_FEATURES:
        raise InvalidStructureError(f"{n} features; must be 0..{MAX_FEATURES}")
    for feature in structure.features:
        d = depth(feature)
        if d > MAX_DEPTH:
            raise InvalidStructureError(f"depth {d} exceeds {MAX_DEPTH}: {feature}")
        _validate(feature, by_name)


def _kind(by_name: dict[str, ChannelSpec], channel: str) -> ChannelKind:
    spec = by_name.get(channel)
    if spec is None:
        raise InvalidStructureError(f"unknown channel {channel!r}")
    return spec.kind


def _validate(feature: Feature, by_name: dict[str, ChannelSpec]) -> None:
    match feature:
        case Excite(mark=mark, source=source):
            match mark:
                case One():
                    pass
                case Pow(channel=c):
                    if _kind(by_name, c) is not ChannelKind.POSITIVE:
                        raise InvalidStructureError(
                            f"Pow needs a positive channel: {c}"
                        )
                case Mark(channel=c):
                    _kind(by_name, c)
                case ExpOf(channel=c) | Above(channel=c):
                    if _kind(by_name, c) is ChannelKind.SIGN:
                        raise InvalidStructureError(f"{mark} on a sign channel")
            if source.kind is SourceKind.ALL:
                if source.channel is not None:
                    raise InvalidStructureError("Source ALL takes no channel")
            elif source.channel is None or (
                _kind(by_name, source.channel) is not ChannelKind.SIGN
            ):
                raise InvalidStructureError(
                    f"signed source needs a sign channel: {source}"
                )
            if (
                isinstance(mark, Mark)
                and source.kind is not SourceKind.ALL
                and mark.channel == source.channel
            ):
                raise InvalidStructureError(
                    f"Mark({mark.channel}) on a source filtered by {mark.channel} "
                    f"is the constant {source.kind.value}1; use One"
                )
        case Periodic() | Trend():
            pass
        case Product(left=left, right=right):
            _validate(left, by_name)
            _validate(right, by_name)
        case Gate(feature=inner, cond=cond):
            _validate(inner, by_name)
            if (
                isinstance(cond, LastMarkAbove)
                and _kind(by_name, cond.channel) is ChannelKind.SIGN
            ):
                raise InvalidStructureError(f"LastMarkAbove on a sign channel: {cond}")
