"""The size of the v2 hypothesis space (SPEC §2.1, last paragraph).

SPEC §2.1 requires the space to be far larger than any budget in §4 and larger
than the B-sparse dictionary (§4.1), "otherwise proposal quality cannot matter,
which was v1's failure". That is a claim about a number, so the number is
computed here, exactly, in integer arithmetic.

**The counting argument.** By :mod:`sciagent.glm.canonical`, the equivalence
class of a feature tree is the pair ``(atoms, gates)``: the multiset of
``Excite`` / ``Periodic`` / ``Trend`` leaves under its products and the multiset
of conditions gating them. The normal form is depth-minimal, so the class is
reachable at depth <= D exactly when *some* tree of depth <= D has ``n`` atoms
and ``g`` gates. Whether a shape ``(n, g)`` is reachable does not depend on
which atoms or conditions fill it (gates may sit on any node, products may nest
any way), so :func:`feasible_shapes` derives the reachable shapes from the
grammar alone: a depth-1 tree is one atom; a depth-<=D tree is a depth-<=D-1
tree, a product of two of them, or a gate on one. The number of canonical
features of depth <= D over ``A`` atom types and ``C`` condition types is then

    Σ over feasible (n, g) of  multiset(A, n) · multiset(C, g),

with ``multiset(k, n) = C(k + n - 1, n)``. Nothing here enumerates trees.
:func:`enumerate_raw` is the independent definition used to check it: raw
trees, canonicalised, de-duplicated.

**Structures.** A structure is a link and a multiset of 0..K canonical
features (zero is the null, intercept-only model), with the de-duplication
rule of ``canonicalise``: a repeated feature is dropped only when it has no ψ
slots. With ``Q`` ψ-free and ``P`` ψ-bearing
features, the number of structures of size k is ``Σ_j C(Q, j) · multiset(P, k -
j)``. Weighting each feature by its number of ψ grid points gives the number of
*parameterised* structures (a structure with every ψ at a grid point), by the
same generating function with ``(1 - w z)^{-N_w}`` factors.

**The B-sparse dictionary** (SPEC §4.1) has one group per canonical feature of
depth <= 2 per point of that feature's ψ grid, and a group has
:func:`~sciagent.glm.grammar.n_columns` columns. Both are sums of products over
a class's atoms and gates, so they too need no enumeration.

A caveat on "distinct": the count is of *canonical* features, the equivalence
that ``canonicalise`` defines. It does not merge features that are equal as
functions for reasons outside that relation (``Mark(sign)`` on a ``+`` source
is the constant 1, so it duplicates ``One``). The counts are therefore an upper
bound on the number of distinct model families, and the grammar's looseness is
part of what is being measured.

This module is pure: no I/O, no randomness, exact integers throughout.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import canonical_feature, sort_key
from sciagent.glm.grammar import (
    ALL,
    MAX_DEPTH,
    Above,
    ChannelSpec,
    Cond,
    Excite,
    ExpOf,
    Feature,
    Gate,
    InvalidStructureError,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    MarkFn,
    One,
    Periodic,
    PhaseWindow,
    Pow,
    Product,
    Source,
    SourceKind,
    Structure,
    Trend,
    n_columns,
    psi_slots,
    validate,
)
from sciagent.glm.grids import grid

type Atom = Excite | Periodic | Trend


class SpaceError(SciAgentError):
    """A request about the hypothesis space is out of range."""


@dataclass(frozen=True)
class Alphabet:
    """The depth-1 vocabulary: atom types and gate-condition types.

    Both tuples hold distinct values. :func:`alphabet` builds the full one for
    a channel set; tests build reduced ones to brute-force depth 3.
    """

    atoms: tuple[Atom, ...]
    conds: tuple[Cond, ...]


type Space = tuple[ChannelSpec, ...] | Alphabet


@dataclass(frozen=True)
class PsiSummary:
    """Distribution of ψ grid-point counts over canonical features.

    ``median`` is the lower median (the element at index ``(n - 1) // 2`` of the
    sorted values), so it is always an attained integer.
    """

    minimum: int
    median: int
    maximum: int
    n_features: int


@dataclass(frozen=True)
class Dictionary:
    """Size of the B-sparse dictionary: features, ψ-point groups, columns."""

    n_features: int
    n_groups: int
    n_columns: int


# --------------------------------------------------------------------------
# The alphabet
# --------------------------------------------------------------------------


def _is_valid(feature: Feature, channels: tuple[ChannelSpec, ...]) -> bool:
    try:
        validate(Structure((feature,)), channels)
    except InvalidStructureError:
        return False
    return True


def alphabet(channels: tuple[ChannelSpec, ...]) -> Alphabet:
    """Every depth-1 atom and every gate condition valid for ``channels``.

    Candidates are generated without regard to channel kind (every kernel, every
    mark-function constructor on every channel, every source kind on every
    channel) and filtered by :func:`~sciagent.glm.grammar.validate`, so the
    alphabet is exactly what the grammar admits. Both tuples are in
    :func:`~sciagent.glm.canonical.sort_key` order.
    """
    marks: list[MarkFn] = [One()]
    for spec in channels:
        marks.extend(m(spec.name) for m in (Mark, Pow, ExpOf, Above))
    sources = [ALL]
    for spec in channels:
        sources.extend(
            Source(kind, spec.name)
            for kind in (SourceKind.POSITIVE, SourceKind.NEGATIVE)
        )
    candidates: list[Atom] = [
        Excite(kernel, mark, source)
        for kernel in KernelKind
        for mark in marks
        for source in sources
    ]
    candidates.extend((Periodic(), Trend()))
    atoms = tuple(
        sorted((a for a in candidates if _is_valid(a, channels)), key=sort_key)
    )
    cond_candidates: list[Cond] = [PhaseWindow()]
    cond_candidates.extend(LastMarkAbove(spec.name) for spec in channels)
    conds = tuple(
        sorted(
            (c for c in cond_candidates if _is_valid(Gate(Trend(), c), channels)),
            key=_cond_sort_key,
        )
    )
    return Alphabet(atoms, conds)


def _cond_sort_key(cond: Cond) -> tuple[int | str | tuple[object, ...], ...]:
    return sort_key(Gate(Trend(), cond))


def _resolve(space: Space) -> Alphabet:
    return space if isinstance(space, Alphabet) else alphabet(space)


def _check_depth(max_depth: int) -> None:
    if not 1 <= max_depth <= MAX_DEPTH:
        raise SpaceError(f"max_depth {max_depth} outside 1..{MAX_DEPTH}")


# --------------------------------------------------------------------------
# The depth rule
# --------------------------------------------------------------------------


def feasible_shapes(max_depth: int) -> tuple[tuple[int, int], ...]:
    """The ``(n_atoms, n_gates)`` classes reachable by a tree of depth <= D.

    Derived from the grammar alone: depth 1 is a single atom ``(1, 0)``; depth
    D adds a product of two depth-<=D-1 trees (shapes add) or a gate on one
    (``g + 1``). Because the canonical normal form is depth-minimal, this is
    also the set of classes whose canonical tree has depth <= D.
    """
    if max_depth < 1:
        raise SpaceError(f"max_depth {max_depth} < 1")
    level: set[tuple[int, int]] = {(1, 0)}
    for _ in range(max_depth - 1):
        ordered = sorted(level)
        grown = set(level)
        grown.update((n, g + 1) for n, g in ordered)
        grown.update((n1 + n2, g1 + g2) for n1, g1 in ordered for n2, g2 in ordered)
        level = grown
    return tuple(sorted(level))


# --------------------------------------------------------------------------
# Counting features
# --------------------------------------------------------------------------


def _multisets(kinds: int, size: int) -> int:
    """Number of multisets of ``size`` elements drawn from ``kinds`` types."""
    if kinds == 0:
        return 1 if size == 0 else 0
    return math.comb(kinds + size - 1, size)


def count_features(space: Space, max_depth: int) -> int:
    """Exact number of canonical features of depth <= ``max_depth``."""
    _check_depth(max_depth)
    alpha = _resolve(space)
    return sum(
        _multisets(len(alpha.atoms), n) * _multisets(len(alpha.conds), g)
        for n, g in feasible_shapes(max_depth)
    )


def count_features_by_depth(space: Space, max_depth: int) -> tuple[int, ...]:
    """Number of canonical features of each depth exactly, 1..``max_depth``."""
    _check_depth(max_depth)
    cumulative = [0, *(count_features(space, d) for d in range(1, max_depth + 1))]
    return tuple(b - a for a, b in itertools.pairwise(cumulative))


# --------------------------------------------------------------------------
# Enumerating features
# --------------------------------------------------------------------------


def _build(atoms: Sequence[Atom], conds: Sequence[Cond]) -> Feature:
    """Some tree of the class ``(atoms, conds)``, canonicalised."""
    tree: Feature = atoms[0]
    for atom in atoms[1:]:
        tree = Product(tree, atom)
    for cond in conds:
        tree = Gate(tree, cond)
    return canonical_feature(tree)


def enumerate_features(space: Space, max_depth: int) -> tuple[Feature, ...]:
    """Every canonical feature of depth <= ``max_depth``, in ``sort_key`` order.

    Built class by class (multiset of atoms x multiset of conditions, for every
    feasible shape), so it is as large as the count and no larger. Pointproc at
    depth 3 is about 5.6e5 trees; materialise it only when you need to.
    """
    _check_depth(max_depth)
    alpha = _resolve(space)
    atoms = sorted(alpha.atoms, key=sort_key)
    conds = sorted(alpha.conds, key=_cond_sort_key)
    out: list[Feature] = []
    for n, g in feasible_shapes(max_depth):
        for atom_combo in itertools.combinations_with_replacement(atoms, n):
            for cond_combo in itertools.combinations_with_replacement(conds, g):
                out.append(_build(atom_combo, cond_combo))
    return tuple(sorted(out, key=sort_key))


def enumerate_raw(space: Space, max_depth: int) -> tuple[Feature, ...]:
    """The definition of the space, by brute force; exponential, for checking.

    Every raw tree of depth <= ``max_depth`` built from the alphabet by the
    grammar's rules (atoms; ``Product`` of two trees; ``Gate`` of a tree), each
    canonicalised, de-duplicated by canonical form, in ``sort_key`` order. It
    does not use :func:`feasible_shapes` or any counting formula, and it does
    not canonicalise between levels, so it also checks that the normal form
    never exceeds the depth of the raw tree it came from.
    """
    _check_depth(max_depth)
    alpha = _resolve(space)
    level: list[Feature] = list(alpha.atoms)
    for _ in range(max_depth - 1):
        grown: list[Feature] = list(level)
        grown.extend(Product(a, b) for a in level for b in level)
        grown.extend(Gate(f, c) for f in level for c in alpha.conds)
        level = list(dict.fromkeys(grown))
    canonical = {canonical_feature(f) for f in level}
    return tuple(sorted(canonical, key=sort_key))


# --------------------------------------------------------------------------
# ψ grid points, columns and the B-sparse dictionary
# --------------------------------------------------------------------------


def psi_points(feature: Feature) -> int:
    """Number of ψ grid points of a feature: the product of its slots' grids."""
    return math.prod(len(grid(slot.name)) for slot in psi_slots(feature))


type _Group = tuple[tuple[int, int], int]  # ((points, columns), n types)


def _groups(types: Sequence[Feature | Cond]) -> list[_Group]:
    """Types grouped by ``(ψ points, columns)``, in sorted order."""
    tally: dict[tuple[int, int], int] = {}
    for t in types:
        feature: Feature = (
            Gate(Trend(), t) if isinstance(t, LastMarkAbove | PhaseWindow) else t
        )
        key = (psi_points(feature), 1 if feature is not t else n_columns(feature))
        tally[key] = tally.get(key, 0) + 1
    return sorted(tally.items())


def _multiset_distribution(
    groups: list[_Group], max_size: int
) -> dict[tuple[int, int, int], int]:
    """``(size, Π points, Π columns) -> count`` over multisets of the types."""
    dist: dict[tuple[int, int, int], int] = {(0, 1, 1): 1}
    for (points, columns), kinds in groups:
        grown: dict[tuple[int, int, int], int] = {}
        for (size, p, c), count in sorted(dist.items()):
            for m in range(max_size - size + 1):
                key = (size + m, p * points**m, c * columns**m)
                grown[key] = grown.get(key, 0) + count * _multisets(kinds, m)
        dist = grown
    return dist


def _joint(space: Space, max_depth: int) -> dict[tuple[int, int], int]:
    """``(ψ points, columns) -> number of canonical features`` up to depth D."""
    _check_depth(max_depth)
    alpha = _resolve(space)
    shapes = feasible_shapes(max_depth)
    atom_dist = _multiset_distribution(_groups(alpha.atoms), max(n for n, _ in shapes))
    cond_dist = _multiset_distribution(_groups(alpha.conds), max(g for _, g in shapes))
    joint: dict[tuple[int, int], int] = {}
    for (n, p, c), n_atom in sorted(atom_dist.items()):
        for (g, q, _), n_cond in sorted(cond_dist.items()):
            if (n, g) in shapes:
                key = (p * q, c)
                joint[key] = joint.get(key, 0) + n_atom * n_cond
    return joint


def psi_distribution(space: Space, max_depth: int) -> tuple[tuple[int, int], ...]:
    """``(ψ grid points, number of canonical features)`` pairs, ascending."""
    by_points: dict[int, int] = {}
    for (points, _), count in sorted(_joint(space, max_depth).items()):
        by_points[points] = by_points.get(points, 0) + count
    return tuple(sorted(by_points.items()))


def psi_summary(space: Space, max_depth: int) -> PsiSummary:
    """Min, lower median and max of ψ grid points over canonical features."""
    dist = psi_distribution(space, max_depth)
    total = sum(count for _, count in dist)
    target = (total - 1) // 2  # zero-based rank of the lower median
    seen = 0
    median = dist[0][0]
    for points, count in dist:
        seen += count
        if seen > target:
            median = points
            break
    return PsiSummary(dist[0][0], median, dist[-1][0], total)


def dictionary_size(space: Space, max_depth: int = 2) -> Dictionary:
    """Size of the B-sparse dictionary (SPEC §4.1): features up to depth 2.

    ``n_groups`` counts a feature once per ψ grid point; ``n_columns`` is the
    design width, each group contributing ``n_columns(feature)`` columns.
    """
    joint = _joint(space, max_depth)
    return Dictionary(
        n_features=sum(joint.values()),
        n_groups=sum(p * count for (p, _), count in sorted(joint.items())),
        n_columns=sum(p * c * count for (p, c), count in sorted(joint.items())),
    )


# --------------------------------------------------------------------------
# Counting structures
# --------------------------------------------------------------------------


def _structure_polynomial(
    by_points: tuple[tuple[int, int], ...], max_features: int, *, weighted: bool
) -> list[int]:
    """Coefficients 0..K of the generating function of feature multisets.

    A ψ-free feature (one grid point) appears at most once; any other feature
    appears any number of times. With ``weighted`` each copy of a feature
    multiplies the coefficient by its ψ grid points.
    """
    poly = [1] + [0] * max_features
    for points, count in by_points:
        if count == 0:
            continue
        if points == 1:
            factor = [math.comb(count, j) for j in range(max_features + 1)]
        else:
            weight = points if weighted else 1
            factor = [
                math.comb(count + m - 1, m) * weight**m for m in range(max_features + 1)
            ]
        poly = [
            sum(poly[i] * factor[k - i] for i in range(k + 1))
            for k in range(max_features + 1)
        ]
    return poly


def count_structures(space: Space, max_depth: int, max_features: int) -> int:
    """Exact number of distinct canonical structures.

    Links x multisets of 0..``max_features`` canonical features of depth <=
    ``max_depth`` (the empty multiset is the null model), with repeats allowed
    only for features that have ψ slots (the de-duplication rule of
    ``canonicalise``).
    """
    _check_structure_args(max_depth, max_features)
    poly = _structure_polynomial(
        psi_distribution(space, max_depth), max_features, weighted=False
    )
    return len(Link) * sum(poly)


def count_structure_points(space: Space, max_depth: int, max_features: int) -> int:
    """Number of parameterised structures: :func:`count_structures`, with every
    feature's ψ slots set to a point of their grids.

    This is the number of distinct *models* the shape-parameter profile can
    reach, and is what a search over the space is choosing among.
    """
    _check_structure_args(max_depth, max_features)
    poly = _structure_polynomial(
        psi_distribution(space, max_depth), max_features, weighted=True
    )
    return len(Link) * sum(poly)


def _check_structure_args(max_depth: int, max_features: int) -> None:
    _check_depth(max_depth)
    if max_features < 0:
        raise SpaceError(f"max_features {max_features} < 0")
