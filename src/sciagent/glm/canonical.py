"""Canonical forms and structure hashes (SPEC §2.2 "Canonicalisation").

Two proposals are *equivalent* when they span the same model family up to a
reparameterisation of θ and a relabelling of ψ. The rewrites that preserve this
are exactly:

- ``Product`` is commutative and associative (it permutes design columns);
- ``Gate`` distributes over ``Product``: ``Product(Gate(a, c), b)``,
  ``Product(a, Gate(b, c))`` and ``Gate(Product(a, b), c)`` have the same
  columns, and gates on one feature commute with each other;
- the order of the feature multiset is immaterial.

Every feature tree is therefore a pair ``(atoms, gates)``: the multiset of
``Excite`` / ``Periodic`` / ``Trend`` leaves under its products and the
multiset of conditions gating them. Each condition keeps its own ψ, so two
copies of one condition are two gates, never merged. The pair is the
equivalence class of a tree; :func:`canonical_feature` maps it to one tree.

**Normal form.** Hoisting every gate to the top would make some valid trees
deeper than ``MAX_DEPTH`` (``Product(Gate(a, c), Gate(b, d))`` has depth 3 but
its hoisted form has depth 4), as would a left-nested product of four atoms. So
the normal form is the *depth-minimal* representative of the class, built
deterministically: the gates, sorted, are dealt round-robin onto the sorted
atoms (innermost gate smallest), and the gated atoms are merged pairwise,
shallowest two first, with a product's children ordered by :func:`sort_key`.
Spreading the gates evenly and merging shallowest-first is depth-optimal, so
``depth(canonical_feature(f)) <= depth(f)`` and a valid structure stays valid.

De-duplication drops a repeated feature only when it has no ψ slots
(``Trend``, ``Product(Trend, Trend)``): those repeats are identical columns.
A repeated feature that has ψ slots is *not* redundant, since each copy gets
its own independently profiled ψ (two ``ExpK`` timescales), so multiplicity is
kept.

The link is part of the structure and of the hash. This module is pure: no I/O,
no randomness, and nothing depends on ``hash()`` or iteration order of a
mapping or set.
"""

from __future__ import annotations

import hashlib
import json

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
    Structure,
    Trend,
    psi_slots,
)

type SortKey = tuple[int | str | SortKey, ...]

type _Atom = Excite | Periodic | Trend


# --------------------------------------------------------------------------
# Total order
# --------------------------------------------------------------------------


def _mark_key(mark: MarkFn) -> SortKey:
    match mark:
        case One():
            return (0, "")
        case Mark(channel=c):
            return (1, c)
        case Pow(channel=c):
            return (2, c)
        case ExpOf(channel=c):
            return (3, c)
        case Above(channel=c):
            return (4, c)


def _source_key(source: Source) -> SortKey:
    return (source.kind.value, source.channel or "")


def _cond_key(cond: Cond) -> SortKey:
    match cond:
        case LastMarkAbove(channel=c):
            return (0, c)
        case PhaseWindow():
            return (1, "")


def sort_key(feature: Feature) -> SortKey:
    """A total, deterministic order on feature trees.

    Nested tuples of ints and strings, led by a node tag, so two trees compare
    equal exactly when they are equal, and the order never involves ``hash()``.
    """
    match feature:
        case Excite(kernel=kernel, mark=mark, source=source):
            return (0, kernel.value, _mark_key(mark), _source_key(source))
        case Periodic():
            return (1,)
        case Trend():
            return (2,)
        case Product(left=left, right=right):
            return (3, sort_key(left), sort_key(right))
        case Gate(feature=inner, cond=cond):
            return (4, sort_key(inner), _cond_key(cond))


# --------------------------------------------------------------------------
# Canonical form
# --------------------------------------------------------------------------


def _decompose(feature: Feature) -> tuple[list[_Atom], list[Cond]]:
    """The ``(atoms, gates)`` class of a tree, in traversal order."""
    match feature:
        case Excite() | Periodic() | Trend():
            return [feature], []
        case Product(left=left, right=right):
            la, lg = _decompose(left)
            ra, rg = _decompose(right)
            return la + ra, lg + rg
        case Gate(feature=inner, cond=cond):
            atoms, gates = _decompose(inner)
            return atoms, [*gates, cond]


def canonical_feature(feature: Feature) -> Feature:
    """The normal form of one feature tree (see the module docstring)."""
    atoms, gates = _decompose(feature)
    atoms.sort(key=sort_key)
    gates.sort(key=_cond_key)
    dealt: list[list[Cond]] = [[] for _ in atoms]
    for j, cond in enumerate(gates):
        dealt[j % len(atoms)].append(cond)  # sorted gates stay sorted per atom
    items: list[tuple[int, SortKey, Feature]] = []
    for atom, conds in zip(atoms, dealt, strict=True):
        gated: Feature = atom
        for cond in conds:
            gated = Gate(gated, cond)
        items.append((1 + len(conds), sort_key(gated), gated))
    while len(items) > 1:
        items.sort(key=lambda item: (item[0], item[1]))
        (d1, k1, f1), (d2, k2, f2) = items[0], items[1]
        lo, hi = (f1, f2) if k1 <= k2 else (f2, f1)
        merged = Product(lo, hi)
        items = [*items[2:], (1 + max(d1, d2), sort_key(merged), merged)]
    return items[0][2]


def canonicalise(structure: Structure) -> Structure:
    """The canonical form of a structure: same link, canonical sorted multiset.

    Repeated features with no ψ slots are collapsed to one; every other
    repeat is kept.
    """
    ordered = sorted((canonical_feature(f) for f in structure.features), key=sort_key)
    kept: list[Feature] = []
    for f in ordered:
        if kept and kept[-1] == f and not psi_slots(f):
            continue
        kept.append(f)
    return Structure(tuple(kept), structure.link)


# --------------------------------------------------------------------------
# Hash
# --------------------------------------------------------------------------

type _Json = int | str | list[_Json]


def _data(feature: Feature) -> _Json:
    match feature:
        case Excite(kernel=kernel, mark=mark, source=source):
            return [
                "Excite",
                kernel.value,
                _mark_data(mark),
                [source.kind.value, source.channel or ""],
            ]
        case Periodic():
            return ["Periodic"]
        case Trend():
            return ["Trend"]
        case Product(left=left, right=right):
            return ["Product", _data(left), _data(right)]
        case Gate(feature=inner, cond=cond):
            return ["Gate", _data(inner), _cond_data(cond)]


def _mark_data(mark: MarkFn) -> _Json:
    match mark:
        case One():
            return ["One"]
        case Mark(channel=c):
            return ["Mark", c]
        case Pow(channel=c):
            return ["Pow", c]
        case ExpOf(channel=c):
            return ["ExpOf", c]
        case Above(channel=c):
            return ["Above", c]


def _cond_data(cond: Cond) -> _Json:
    match cond:
        case LastMarkAbove(channel=c):
            return ["LastMarkAbove", c]
        case PhaseWindow():
            return ["PhaseWindow"]


def structure_hash(structure: Structure) -> str:
    """SHA-256 hex digest of the canonical form, link included.

    Equivalent proposals share one hash. The input is a fixed JSON
    serialisation of the canonical tree, so the digest is stable across
    processes, platforms and Python versions.
    """
    canonical = canonicalise(structure)
    payload: _Json = [
        canonical.link.value,
        [_data(f) for f in canonical.features],
    ]
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()
