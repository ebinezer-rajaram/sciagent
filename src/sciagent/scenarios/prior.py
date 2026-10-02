"""The truth prior over structures and ψ (SPEC §3, §4.1).

A truth structure is drawn by a generative grammar over **canonical** features:

1. the link, from ``link_probs``;
2. the number of features K, from ``n_features_probs``;
3. each feature's *depth class*. An **in-dictionary** structure (SPEC §4.1:
   the B-sparse dictionary holds every feature of depth ≤ 2) draws every
   feature's depth from ``depth_probs`` (over 1 and 2). An
   **out-of-dictionary** structure has exactly one feature of depth 3 and the
   rest from ``depth_probs``. Which kind a candidate is, is decided by the
   caller (stratified sampling, ``sampler.py``), not drawn here, so the
   declared out-of-dictionary share holds exactly;
4. the feature's *shape* ``(n_atoms, n_gates)``, from ``shape_probs``
   restricted to the shapes whose depth-minimal tree has that depth
   (:func:`~sciagent.glm.space.feasible_shapes`; the canonical form is
   depth-minimal, so the shape fixes the canonical depth);
5. ``n_atoms`` atoms iid from :func:`atom_distribution` and ``n_gates``
   conditions iid from :func:`cond_distribution`, assembled and
   canonicalised.

A structure whose canonical form repeats a feature is redrawn feature by
feature (K is kept), so truths never carry two copies of one feature; two
copies differ only in ψ and make the truth weakly identifiable.

**Atoms.** An ``Excite`` atom's probability is the product of its kernel's,
its mark-function constructor's and its source kind's probabilities, split
evenly over the channels that make the combination valid; combinations that
:func:`~sciagent.glm.grammar.validate` rejects get none, and the result is
renormalised. ``Periodic`` and ``Trend`` take ``atom_kind_probs`` directly.
The pointproc prior gives ``Trend`` probability 0 (see ``truths_v2.py``).

**ψ** is drawn uniformly from each slot's *truth grid*, which must be a subset
of the fitting grid in :mod:`sciagent.glm.grids`: truths are on-grid, so the
ORACLE fit can reach the truth's exact ψ (``truths_v2.py`` says why).

This module draws only from the generator it is passed (invariant 3).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import canonical_feature, canonicalise, structure_hash
from sciagent.glm.grammar import (
    MAX_DEPTH,
    ChannelSpec,
    Cond,
    Excite,
    Feature,
    Gate,
    KernelKind,
    Link,
    MarkFn,
    Periodic,
    Product,
    PsiSlot,
    SourceKind,
    Structure,
    Trend,
    depth,
    psi_slots,
    validate,
)
from sciagent.glm.grids import PSI_GRIDS
from sciagent.glm.simulate import PsiAssignment
from sciagent.glm.space import alphabet, feasible_shapes

#: Depth of the B-sparse dictionary (SPEC §4.1).
DICTIONARY_DEPTH: Final = 2
#: Redraws allowed when a structure repeats a feature.
_MAX_REDRAWS: Final = 1000
_PROB_TOL: Final = 1e-9

type Atom = Excite | Periodic | Trend


class PriorError(SciAgentError):
    """A prior is ill-formed, or cannot produce a valid draw."""


def _mark_name(mark: MarkFn) -> str:
    return type(mark).__name__


def _check_probs[K](name: str, probs: Sequence[tuple[K, float]]) -> None:
    if not probs:
        raise PriorError(f"{name} is empty")
    keys = [k for k, _ in probs]
    if len(set(keys)) != len(keys):
        raise PriorError(f"{name} repeats a key")
    if any(not (math.isfinite(p) and p >= 0.0) for _, p in probs):
        raise PriorError(f"{name} has a negative or non-finite probability")
    if abs(math.fsum(p for _, p in probs) - 1.0) > _PROB_TOL:
        raise PriorError(f"{name} does not sum to 1")


@dataclass(frozen=True)
class StructurePrior:
    """Declared probabilities of the generative grammar (module docstring).

    Every field is a tuple of ``(key, probability)`` pairs summing to 1.
    ``mark_probs`` is keyed by constructor name (``One``, ``Mark``, ``Pow``,
    ``ExpOf``, ``Above``), ``source_probs`` by :class:`SourceKind`,
    ``cond_probs`` by condition class name (``PhaseWindow``,
    ``LastMarkAbove``), ``atom_kind_probs`` by ``Excite`` / ``Periodic`` /
    ``Trend``. ``shape_probs`` holds a weight for every feasible depth-≤3
    shape; it is renormalised within each depth class, so it need not sum to 1
    within one class, only overall. ``depth_probs`` is over depths 1 and 2.
    """

    link_probs: tuple[tuple[Link, float], ...]
    n_features_probs: tuple[tuple[int, float], ...]
    depth_probs: tuple[tuple[int, float], ...]
    shape_probs: tuple[tuple[tuple[int, int], float], ...]
    atom_kind_probs: tuple[tuple[str, float], ...]
    kernel_probs: tuple[tuple[KernelKind, float], ...]
    mark_probs: tuple[tuple[str, float], ...]
    source_probs: tuple[tuple[SourceKind, float], ...]
    cond_probs: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        for name in (
            "link_probs",
            "n_features_probs",
            "depth_probs",
            "shape_probs",
            "atom_kind_probs",
            "kernel_probs",
            "mark_probs",
            "source_probs",
            "cond_probs",
        ):
            _check_probs(name, getattr(self, name))
        if any(not 1 <= k <= 4 for k, _ in self.n_features_probs):
            raise PriorError("K must lie in 1..4")
        if {d for d, _ in self.depth_probs} - {1, 2}:
            raise PriorError("depth_probs is over in-dictionary depths 1 and 2")
        feasible = set(feasible_shapes(MAX_DEPTH))
        if {s for s, _ in self.shape_probs} != feasible:
            raise PriorError(f"shape_probs must cover exactly {sorted(feasible)}")
        if {k for k, _ in self.atom_kind_probs} != {"Excite", "Periodic", "Trend"}:
            raise PriorError("atom_kind_probs is over Excite, Periodic, Trend")
        if {k for k, _ in self.mark_probs} - {"One", "Mark", "Pow", "ExpOf", "Above"}:
            raise PriorError("unknown mark-function name in mark_probs")
        if {k for k, _ in self.cond_probs} - {"PhaseWindow", "LastMarkAbove"}:
            raise PriorError("unknown condition name in cond_probs")


# --------------------------------------------------------------------------
# Atom and condition distributions
# --------------------------------------------------------------------------


def atom_distribution(
    prior: StructurePrior, channels: tuple[ChannelSpec, ...]
) -> tuple[tuple[Atom, float], ...]:
    """Every atom of the channels' alphabet with its prior probability.

    In the alphabet's (``sort_key``) order. See the module docstring.
    """
    alpha = alphabet(channels)
    kind = dict(prior.atom_kind_probs)
    kernel = dict(prior.kernel_probs)
    mark = dict(prior.mark_probs)
    source = dict(prior.source_probs)
    # How many valid atoms share each (kernel, mark constructor, source kind):
    # the combination's weight is split evenly over its channel choices.
    combos: dict[tuple[KernelKind, str, SourceKind], int] = {}
    for atom in alpha.atoms:
        if isinstance(atom, Excite):
            key = (atom.kernel, _mark_name(atom.mark), atom.source.kind)
            combos[key] = combos.get(key, 0) + 1
    raw: list[tuple[Atom, float]] = []
    for atom in alpha.atoms:
        match atom:
            case Excite():
                key = (atom.kernel, _mark_name(atom.mark), atom.source.kind)
                w = (
                    kernel.get(atom.kernel, 0.0)
                    * mark.get(key[1], 0.0)
                    * source.get(atom.source.kind, 0.0)
                    / combos[key]
                )
                raw.append((atom, w))
            case Periodic():
                raw.append((atom, -1.0))
            case Trend():
                raw.append((atom, -2.0))
    excite_total = math.fsum(w for a, w in raw if isinstance(a, Excite))
    if excite_total <= 0.0 and kind["Excite"] > 0.0:
        raise PriorError("no valid Excite atom has positive probability")
    out: list[tuple[Atom, float]] = []
    for atom, w in raw:
        match atom:
            case Excite():
                out.append((atom, kind["Excite"] * w / excite_total))
            case Periodic():
                out.append((atom, kind["Periodic"]))
            case Trend():
                out.append((atom, kind["Trend"]))
    return tuple(out)


def cond_distribution(
    prior: StructurePrior, channels: tuple[ChannelSpec, ...]
) -> tuple[tuple[Cond, float], ...]:
    """Every gate condition of the alphabet with its prior probability."""
    conds = alphabet(channels).conds
    by_name = dict(prior.cond_probs)
    counts: dict[str, int] = {}
    for c in conds:
        counts[type(c).__name__] = counts.get(type(c).__name__, 0) + 1
    raw = [
        (c, by_name.get(type(c).__name__, 0.0) / counts[type(c).__name__])
        for c in conds
    ]
    total = math.fsum(w for _, w in raw)
    if total <= 0.0:
        raise PriorError("no gate condition has positive probability")
    return tuple((c, w / total) for c, w in raw)


def _choice[T](items: Sequence[tuple[T, float]], rng: np.random.Generator) -> T:
    """One draw by inverse CDF from a single uniform: a documented stream use."""
    u = rng.random()
    cumulative = 0.0
    last = None
    for item, p in items:
        if p <= 0.0:
            continue
        cumulative += p
        last = item
        if u < cumulative:
            return item
    if last is None:
        raise PriorError("no outcome has positive probability")
    return last


# --------------------------------------------------------------------------
# Features and structures
# --------------------------------------------------------------------------


def shape_depth(shape: tuple[int, int]) -> int:
    """The canonical (depth-minimal) depth of a feature of this shape."""
    for d in range(1, MAX_DEPTH + 1):
        if shape in feasible_shapes(d):
            return d
    raise PriorError(f"shape {shape} is not reachable at depth ≤ {MAX_DEPTH}")


def _feature(
    prior: StructurePrior,
    target_depth: int,
    atoms: Sequence[tuple[Atom, float]],
    conds: Sequence[tuple[Cond, float]],
    rng: np.random.Generator,
) -> Feature:
    shapes = [(s, p) for s, p in prior.shape_probs if shape_depth(s) == target_depth]
    total = math.fsum(p for _, p in shapes)
    if total <= 0.0:
        raise PriorError(f"no shape of depth {target_depth} has positive probability")
    n_atoms, n_gates = _choice([(s, p / total) for s, p in shapes], rng)
    drawn_atoms = [_choice(atoms, rng) for _ in range(n_atoms)]
    drawn_conds = [_choice(conds, rng) for _ in range(n_gates)]
    tree: Feature = drawn_atoms[0]
    for atom in drawn_atoms[1:]:
        tree = Product(tree, atom)
    for cond in drawn_conds:
        tree = Gate(tree, cond)
    feature = canonical_feature(tree)
    if depth(feature) != target_depth:
        raise PriorError(
            f"shape {(n_atoms, n_gates)} canonicalised to depth {depth(feature)}, "
            f"not {target_depth}"
        )
    return feature


def _key(feature: Feature) -> str:
    return structure_hash(Structure((feature,)))


def sample_structure(
    prior: StructurePrior,
    channels: tuple[ChannelSpec, ...],
    rng: np.random.Generator,
    *,
    out_of_dictionary: bool,
) -> Structure:
    """One canonical truth structure (module docstring, steps 1-5)."""
    atoms = atom_distribution(prior, channels)
    conds = cond_distribution(prior, channels)
    link = _choice(prior.link_probs, rng)
    k = _choice(prior.n_features_probs, rng)
    depths = [_choice(prior.depth_probs, rng) for _ in range(k)]
    if out_of_dictionary:
        depths[0] = MAX_DEPTH
    features: list[Feature] = []
    seen: set[str] = set()
    for d in depths:
        for _ in range(_MAX_REDRAWS):
            feature = _feature(prior, d, atoms, conds, rng)
            if _key(feature) not in seen:
                break
        else:
            raise PriorError(f"could not draw a distinct depth-{d} feature")
        seen.add(_key(feature))
        features.append(feature)
    structure = canonicalise(Structure(tuple(features), link))
    validate(structure, channels)
    return structure


def is_out_of_dictionary(
    structure: Structure, dictionary_depth: int = DICTIONARY_DEPTH
) -> bool:
    """True when some feature is deeper than the B-sparse dictionary reaches."""
    return any(
        depth(canonical_feature(f)) > dictionary_depth for f in structure.features
    )


# --------------------------------------------------------------------------
# ψ
# --------------------------------------------------------------------------


def truth_grid(name: str, grids: Mapping[str, tuple[float, ...]]) -> tuple[float, ...]:
    """The truth grid for ψ parameter ``name``: an override, else the fitting grid.

    Raises :class:`PriorError` unless it is a non-empty subset of the fitting grid.
    """
    if name not in PSI_GRIDS:
        raise PriorError(f"no fitting grid named {name!r}")
    values = grids.get(name, PSI_GRIDS[name])
    if not values or any(v not in PSI_GRIDS[name] for v in values):
        raise PriorError(
            f"truth grid for {name!r} must be a subset of {PSI_GRIDS[name]}"
        )
    return tuple(values)


def sample_psi(
    structure: Structure,
    grids: Mapping[str, tuple[float, ...]],
    rng: np.random.Generator,
) -> PsiAssignment:
    """ψ for every slot, uniform on its truth grid, in ``psi_slots`` order."""
    out: list[dict[PsiSlot, float]] = []
    for feature in structure.features:
        assignment: dict[PsiSlot, float] = {}
        for slot in psi_slots(feature):
            values = truth_grid(slot.name, grids)
            assignment[slot] = values[int(rng.integers(len(values)))]
        out.append(assignment)
    return tuple(out)
