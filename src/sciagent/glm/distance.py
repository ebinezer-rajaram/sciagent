"""Structural distance between structures (SPEC §2.2 "Structural distance").

Used for structural recovery (§4.3 score 2), for the truth sampler's
non-membership test and the near/mid/far strata (§3). It is an instrument, and
§6.3 test 4 requires it to be a metric. It is built in three layers, each a
metric, so the composite is one.

**1. Tree edit distance.** Each feature becomes an ordered labelled tree
(:func:`feature_tree`): ``Excite`` has children kernel, mark function, source;
``Pow`` / ``ExpOf`` / ``Above`` wrap a ``Mark:c`` leaf, mirroring the grammar's
``Pow(Mark(c))``; ``Gate`` has children feature, condition. ``T(a, b)`` is the
Zhang-Shasha (1989) edit distance with unit insert, delete and relabel costs.
Unit costs form a metric on labels plus the empty label, so ``T`` is a metric on ordered
labelled trees (Tai 1979: edit scripts reverse and concatenate), and deleting a
whole tree costs its size: ``T(a, ∅) = |a|``.

**2. Normalised feature distance: the Steinhaus transform.** The usual
normalisations of edit distance are *not* metrics. ``T / max(|a|, |b|)`` fails
on ``x = a(b)``, ``y = a(b(a))``, ``z = b(a)`` (2/2 > 1/3 + 1/3), and
``T / (|a| + |b|)`` fails on ``a``, ``a(b)``, ``b(b)`` (2/3 > 1/3 + 1/4); both
are pinned in the tests. Instead, with the empty tree as a reference point,

    d(a, b) = 2 T(a, b) / (|a| + |b| + T(a, b))          ∈ [0, 1].

This is Li & Liu's (2007) normalised edit metric, and it is the Steinhaus
transform of ``T`` about ``∅``. *Lemma.* For any metric ``T`` and point ``o``,
``δ(x, y) = 2T(x,y) / (T(x,o) + T(y,o) + T(x,y))`` is a metric. *Proof of the
triangle.* Write ``p, q, r`` for the distances of ``x, y, z`` to ``o`` and
``u = T(x,y)``, ``v = T(y,z)``, ``w = T(x,z)``. Since ``t ↦ 2t / (s + t)`` is
increasing and ``w ≤ u + v``, ``δ(x,z) ≤ 2(u+v) / (p+r+u+v)``. By the triangle
through ``o``, ``q ≤ r + v`` and ``q ≤ p + u``, so ``p+q+u ≤ p+r+u+v`` and
``q+r+v ≤ p+r+u+v``; hence ``2u/(p+q+u) + 2v/(q+r+v) ≥ 2(u+v)/(p+r+u+v)``. ∎
The bound 1 follows from ``T ≤ |a| + |b|``. Every feature tree has at least one
node, so no denominator is zero, and ``d(a, ∅) = 1`` for every feature.

**3. Feature sets: OSPA with p = 1, cut-off c = 1.** The SPEC's "Hungarian
matching with a fixed cost for unmatched features" is the OSPA construction of
Schuhmacher, Vo & Vo (2008) with order 1. Pad the smaller set with null
features to ``N = max(m, n)``; a real-null pair costs ``c = UNMATCHED_COST = 1``
(which is ``d(a, ∅)`` above), null-null costs 0. With ``W`` the optimal
assignment cost (Hungarian, ``scipy.optimize.linear_sum_assignment``),

    D(X, Y) = W(X, Y) / N        ∈ [0, 1],   D(∅, ∅) = 0.

It is a metric on finite multisets because the per-pair cost never exceeds the
unmatched cost (``d ≤ 1 = c``). *Proof sketch of the triangle* (sizes x, y, z,
``M = max(x, z)``). ``W`` is a metric (compose bijections of padded sets; extra
null-null padding never changes ``W``). If ``y ≤ M`` both right-hand
normalisers are ≤ M, so ``D(X,Y) + D(Y,Z) ≥ (W(X,Y) + W(Y,Z)) / M ≥ D(X,Z)``.
If ``y > M``, pad everything to y and follow each point of Y through the two
optimal bijections to a path (x_k, y_k, z_k). Let ``r`` paths have both ends
real (their X-Z cost summing to ``E ≤ r``), ``a`` / ``b`` paths have only the X
/ only the Z end real (a ≥ b, say), and ``n₀`` have neither. Pairing the a and b
lone real ends with each other and the rest with nulls gives
``W(X,Z) ≤ E + a``, while every null end costs exactly 1 on its side, so
``W(X,Y) + W(Y,Z) ≥ 2n₀ + a + b + E``. With ``y = n₀+a+b+r`` and ``M = a+r``,
``y(E + a) ≤ M(2n₀ + a + b + E)`` reduces to ``(n₀+b)(E+a) ≤ (a+r)(2n₀+b)``,
true since ``E ≤ r``. ∎ Identity: ``D = 0`` iff a zero-cost bijection exists iff
the multisets are equal, since ``d`` is a metric and ``c > 0``.

**4. The link.** ``D_total = (1 - w) D(X, Y) + w · 1[link differs]`` with
``w = LINK_WEIGHT``. The discrete metric on links is a metric and a convex
combination of metrics is one, so ``D_total`` is a metric in ``[0, D_MAX]``,
``D_MAX = 1``. For scale: in a single-feature structure of tree size s, one
leaf relabel costs ``(1 - w) · 2 / (2s + 1)``: 0.178 for a plain ``Excite``
(s = 4), 0.145 for an ``Excite`` with a ``Pow`` mark (s = 5), and 0.533 for a
leaf feature such as ``Periodic`` (s = 1), against 0.2 for a changed link.

**Canonical forms.** :func:`feature_distance` and :func:`structure_distance`
canonicalise their arguments first (``canonical.py``), so they are
pseudometrics on raw trees and metrics on equivalence classes: distance 0 iff
the same :func:`~sciagent.glm.canonical.structure_hash`. Canonical structures
are multisets: a repeated ``Excite`` is two timescales and is kept, so the
set layer is defined on multisets, where OSPA is still a metric.

Determinism: each cost is one division of small integers, computed the same
way every time; the assignment's tie-breaking may vary but the optimal value
does not, and the sum is exactly rounded (``reductions.total``). This module
is pure: no I/O, no randomness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy.optimize import linear_sum_assignment

from sciagent.core.errors import SciAgentError
from sciagent.core.reductions import total as exact_total
from sciagent.glm.canonical import canonical_feature, canonicalise
from sciagent.glm.grammar import (
    Above,
    Cond,
    Excite,
    ExpOf,
    Feature,
    Gate,
    LastMarkAbove,
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
)

#: Cost of a feature left unmatched (OSPA cut-off c). Equals ``d(a, ∅)``.
UNMATCHED_COST: Final = 1.0
#: Weight of the link term; the feature-set term gets ``1 - LINK_WEIGHT``.
LINK_WEIGHT: Final = 0.2
#: Upper bound of :func:`structure_distance`.
D_MAX: Final = 1.0


class DistanceError(SciAgentError):
    """A structural distance query was malformed."""


class EmptyLibraryError(DistanceError):
    """:func:`nearest` was asked for the nearest member of an empty library."""


# --------------------------------------------------------------------------
# Trees
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tree:
    """An ordered labelled tree; children are left to right."""

    label: str
    children: tuple[Tree, ...] = ()


def _leaf(label: str) -> Tree:
    return Tree(label)


def _mark_tree(mark: MarkFn) -> Tree:
    match mark:
        case One():
            return _leaf("One")
        case Mark(channel=c):
            return _leaf(f"Mark:{c}")
        case Pow(channel=c):
            return Tree("Pow", (_leaf(f"Mark:{c}"),))
        case ExpOf(channel=c):
            return Tree("ExpOf", (_leaf(f"Mark:{c}"),))
        case Above(channel=c):
            return Tree("Above", (_leaf(f"Mark:{c}"),))


def _source_tree(source: Source) -> Tree:
    if source.kind is SourceKind.ALL:
        return _leaf("Source:all")
    return _leaf(f"Source:{source.kind.value}:{source.channel}")


def _cond_tree(cond: Cond) -> Tree:
    match cond:
        case LastMarkAbove(channel=c):
            return Tree("LastMarkAbove", (_leaf(f"Mark:{c}"),))
        case PhaseWindow():
            return _leaf("PhaseWindow")


def feature_tree(feature: Feature) -> Tree:
    """The ordered labelled tree of ``feature``, as given (not canonicalised)."""
    match feature:
        case Excite(kernel=kernel, mark=mark, source=source):
            return Tree(
                "Excite",
                (_leaf(kernel.value), _mark_tree(mark), _source_tree(source)),
            )
        case Periodic():
            return _leaf("Periodic")
        case Trend():
            return _leaf("Trend")
        case Product(left=left, right=right):
            return Tree("Product", (feature_tree(left), feature_tree(right)))
        case Gate(feature=inner, cond=cond):
            return Tree("Gate", (feature_tree(inner), _cond_tree(cond)))


def tree_size(tree: Tree) -> int:
    return 1 + sum(tree_size(child) for child in tree.children)


# --------------------------------------------------------------------------
# Zhang-Shasha
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Indexed:
    """Post-order labels, leftmost-leaf indices and keyroots of a tree."""

    labels: tuple[str, ...]
    leftmost: tuple[int, ...]
    keyroots: tuple[int, ...]


def _index(tree: Tree) -> _Indexed:
    labels: list[str] = []
    leftmost: list[int] = []

    def visit(node: Tree) -> int:
        first = -1
        for child in node.children:
            child_leftmost = visit(child)
            if first < 0:
                first = child_leftmost
        labels.append(node.label)
        here = len(labels) - 1
        leftmost.append(here if first < 0 else first)
        return leftmost[here]

    visit(tree)
    # A keyroot is the highest-numbered node for its leftmost leaf.
    last_for_leaf: dict[int, int] = {}
    for i, leaf in enumerate(leftmost):
        last_for_leaf[leaf] = i
    keyroots = tuple(sorted(last_for_leaf.values()))
    return _Indexed(tuple(labels), tuple(leftmost), keyroots)


def tree_edit_distance(a: Tree, b: Tree) -> int:
    """Zhang-Shasha ordered tree edit distance with unit costs."""
    ia, ib = _index(a), _index(b)
    la, lb = ia.leftmost, ib.leftmost
    n, m = len(ia.labels), len(ib.labels)
    td = [[0] * m for _ in range(n)]
    for i in ia.keyroots:
        for j in ib.keyroots:
            li, lj = la[i], lb[j]
            rows, cols = i - li + 2, j - lj + 2
            fd = [[0] * cols for _ in range(rows)]
            for x in range(1, rows):
                fd[x][0] = fd[x - 1][0] + 1
            for y in range(1, cols):
                fd[0][y] = fd[0][y - 1] + 1
            for x in range(1, rows):
                ix = li + x - 1
                for y in range(1, cols):
                    jy = lj + y - 1
                    delete = fd[x - 1][y] + 1
                    insert = fd[x][y - 1] + 1
                    if la[ix] == li and lb[jy] == lj:
                        relabel = int(ia.labels[ix] != ib.labels[jy])
                        fd[x][y] = min(delete, insert, fd[x - 1][y - 1] + relabel)
                        td[ix][jy] = fd[x][y]
                    else:
                        prior = fd[la[ix] - li][lb[jy] - lj]
                        fd[x][y] = min(delete, insert, prior + td[ix][jy])
    return td[n - 1][m - 1]


def normalised_ted(a: Tree, b: Tree) -> float:
    """``2T / (|a| + |b| + T)``: the Steinhaus transform of TED about ∅."""
    ted = tree_edit_distance(a, b)
    if ted == 0:
        return 0.0
    return 2 * ted / (tree_size(a) + tree_size(b) + ted)


# --------------------------------------------------------------------------
# Features, feature sets, structures
# --------------------------------------------------------------------------


def feature_distance(a: Feature, b: Feature) -> float:
    """Normalised tree edit distance between canonical forms, in [0, 1]."""
    return normalised_ted(
        feature_tree(canonical_feature(a)), feature_tree(canonical_feature(b))
    )


def feature_set_distance(xs: Sequence[Feature], ys: Sequence[Feature]) -> float:
    """OSPA (order 1, cut-off ``UNMATCHED_COST``) between feature multisets.

    In [0, 1]. Order within each sequence is irrelevant.
    """
    size = max(len(xs), len(ys))
    if size == 0:
        return 0.0
    cost = np.zeros((size, size), dtype=np.float64)
    for i in range(size):
        for j in range(size):
            if i < len(xs) and j < len(ys):
                cost[i, j] = feature_distance(xs[i], ys[j])
            elif i < len(xs) or j < len(ys):
                cost[i, j] = UNMATCHED_COST
    rows, cols = linear_sum_assignment(cost)
    return exact_total(cost[rows, cols]) / size


def structure_distance(a: Structure, b: Structure) -> float:
    """Metric distance between the canonical forms of two structures.

    ``(1 - LINK_WEIGHT) · OSPA(features) + LINK_WEIGHT · 1[links differ]``, in
    ``[0, D_MAX]``; 0 iff the canonical hashes agree.
    """
    ca, cb = canonicalise(a), canonicalise(b)
    features = feature_set_distance(ca.features, cb.features)
    link = 0.0 if ca.link is cb.link else 1.0
    return (1.0 - LINK_WEIGHT) * features + LINK_WEIGHT * link


def nearest(s: Structure, library: Sequence[Structure]) -> tuple[int, float]:
    """Index of and distance to the library member nearest ``s``.

    Ties go to the lowest index.
    """
    if not library:
        raise EmptyLibraryError("nearest() needs a non-empty library")
    best_index, best = 0, structure_distance(s, library[0])
    for index in range(1, len(library)):
        d = structure_distance(s, library[index])
        if d < best:
            best_index, best = index, d
    return best_index, best
