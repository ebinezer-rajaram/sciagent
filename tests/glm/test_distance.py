"""Instrument test 4 (SPEC §6.3): the structural distance is a metric.

Three layers, each tested on its own:

1. Zhang-Shasha tree edit distance, against published examples and an
   independent brute-force forest recursion.
2. The normalised feature distance (Steinhaus transform of TED), for the metric
   axioms on generated features and on adversarial shapes that break the
   naive ``TED / max(|a|, |b|)`` normalisation.
3. The structure distance (OSPA-style matching plus a link term), for the
   metric axioms on generated structures, including triples of different
   sizes, and identity of indiscernibles against the canonical hash.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from functools import cache

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from strategies import SIZE_SIGN, features, structures

from sciagent.glm.canonical import canonical_feature, canonicalise, structure_hash
from sciagent.glm.distance import (
    D_MAX,
    LINK_WEIGHT,
    UNMATCHED_COST,
    EmptyLibraryError,
    Tree,
    feature_distance,
    feature_set_distance,
    feature_tree,
    nearest,
    structure_distance,
    tree_edit_distance,
)
from sciagent.glm.grammar import (
    ALL,
    Above,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    One,
    Periodic,
    PhaseWindow,
    Product,
    Structure,
    Trend,
)

TOL = 1e-12


def t(label: str, *children: Tree) -> Tree:
    return Tree(label, children)


# --------------------------------------------------------------------------
# Independent brute force: the forest recursion of Tai / Zhang-Shasha, with no
# keyroot optimisation. Forests are tuples of trees; the rightmost root is
# removed at each step. Exponential-ish but memoised; fine on small trees.
# --------------------------------------------------------------------------


def _size(forest: tuple[Tree, ...]) -> int:
    return sum(1 + _size(tree.children) for tree in forest)


@cache
def _forest_distance(f: tuple[Tree, ...], g: tuple[Tree, ...]) -> int:
    if not f and not g:
        return 0
    if not f:
        return _size(g)
    if not g:
        return _size(f)
    v, w = f[-1], g[-1]
    delete_v = _forest_distance(f[:-1] + v.children, g) + 1
    insert_w = _forest_distance(f, g[:-1] + w.children) + 1
    match = (
        _forest_distance(f[:-1], g[:-1])
        + _forest_distance(v.children, w.children)
        + (0 if v.label == w.label else 1)
    )
    return min(delete_v, insert_w, match)


def brute_ted(a: Tree, b: Tree) -> int:
    return _forest_distance((a,), (b,))


def tree_size(a: Tree) -> int:
    return _size((a,))


# --------------------------------------------------------------------------
# 1. Zhang-Shasha
# --------------------------------------------------------------------------


def test_zhang_shasha_published_example() -> None:
    # Zhang & Shasha (1989), Fig. 1: f(d(a, c(b)), e) vs f(c(d(a, b)), e) = 2.
    a = t("f", t("d", t("a"), t("c", t("b"))), t("e"))
    b = t("f", t("c", t("d", t("a"), t("b"))), t("e"))
    assert tree_edit_distance(a, b) == 2
    assert brute_ted(a, b) == 2


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (t("a"), t("a"), 0),
        (t("a"), t("b"), 1),
        (t("a"), t("a", t("b")), 1),
        (t("a", t("b"), t("c")), t("a", t("c"), t("b")), 2),
        # Deleting an internal node promotes its children: one edit.
        (t("a", t("x", t("b"), t("c"))), t("a", t("b"), t("c")), 1),
        # Fully disjoint labels: relabel the min, insert/delete the rest.
        (t("a", t("b"), t("c")), t("x", t("y")), 3),
    ],
)
def test_zhang_shasha_small_cases(a: Tree, b: Tree, expected: int) -> None:
    assert tree_edit_distance(a, b) == expected
    assert brute_ted(a, b) == expected


_LABELS = st.sampled_from(["a", "b", "c"])


def _trees(max_leaves: int = 6) -> st.SearchStrategy[Tree]:
    return st.recursive(
        st.builds(Tree, _LABELS, st.just(())),
        lambda kids: st.builds(
            Tree, _LABELS, st.lists(kids, min_size=1, max_size=3).map(tuple)
        ),
        max_leaves=max_leaves,
    )


@settings(max_examples=150)
@given(_trees(), _trees())
def test_zhang_shasha_matches_brute_force(a: Tree, b: Tree) -> None:
    assert tree_edit_distance(a, b) == brute_ted(a, b)


@settings(max_examples=60)
@given(features(SIZE_SIGN), features(SIZE_SIGN))
def test_zhang_shasha_matches_brute_force_on_feature_trees(
    a: Feature, b: Feature
) -> None:
    ta, tb = feature_tree(a), feature_tree(b)
    assert tree_edit_distance(ta, tb) == brute_ted(ta, tb)


# --------------------------------------------------------------------------
# 2. Feature distance
# --------------------------------------------------------------------------

HAWKES = Excite(KernelKind.EXP, One(), ALL)
S11 = Excite(KernelKind.EXP, Mark("size"), ALL)
ETAS = Excite(KernelKind.POWER, ExpOf("magnitude"), ALL)


def test_feature_tree_shape() -> None:
    assert feature_tree(HAWKES) == t("Excite", t("ExpK"), t("One"), t("Source:all"))
    assert feature_tree(ETAS) == t(
        "Excite", t("PowerK"), t("ExpOf", t("Mark:magnitude")), t("Source:all")
    )
    gated = Gate(Product(Periodic(), Trend()), LastMarkAbove("size"))
    assert feature_tree(gated) == t(
        "Gate",
        t("Product", t("Periodic"), t("Trend")),
        t("LastMarkAbove", t("Mark:size")),
    )


def test_feature_distance_values() -> None:
    # Steinhaus: 2·TED / (|a| + |b| + TED).
    assert feature_distance(HAWKES, HAWKES) == 0.0
    assert feature_distance(HAWKES, S11) == pytest.approx(2 / 9)  # TED 1, 4+4
    assert feature_distance(HAWKES, Periodic()) == pytest.approx(8 / 9)  # TED 4
    assert feature_distance(HAWKES, ETAS) == pytest.approx(6 / 12)  # TED 3, 4+5


def _check_metric[T](
    dist: Callable[[T, T], float], xs: Sequence[T], bound: float
) -> None:
    """Non-negativity, bound, symmetry and every triangle among ``xs``."""
    n = len(xs)
    d = [[dist(xs[i], xs[j]) for j in range(n)] for i in range(n)]
    for i, j in itertools.product(range(n), repeat=2):
        assert 0.0 <= d[i][j] <= bound + TOL
        assert d[i][j] == pytest.approx(d[j][i], abs=TOL)
    for i, j, k in itertools.product(range(n), repeat=3):
        assert d[i][k] <= d[i][j] + d[j][k] + TOL, (xs[i], xs[j], xs[k])


@settings(max_examples=60)
@given(features(SIZE_SIGN), features(SIZE_SIGN), features(SIZE_SIGN))
def test_feature_distance_metric_axioms(a: Feature, b: Feature, c: Feature) -> None:
    _check_metric(feature_distance, [a, b, c], 1.0)
    same = canonical_feature(a) == canonical_feature(b)
    assert (feature_distance(a, b) == 0.0) == same


@settings(max_examples=150)
@given(_trees(8), _trees(8), _trees(8))
def test_normalised_ted_triangle_on_raw_trees(a: Tree, b: Tree, c: Tree) -> None:
    from sciagent.glm.distance import normalised_ted

    _check_metric(normalised_ted, [a, b, c], 1.0)


@pytest.mark.parametrize(
    ("x", "y", "z", "naive"),
    [
        # Found by exhaustive search over trees of <= 4 nodes on labels {a, b}.
        # TED / max(|a|, |b|): 2/2 > 1/3 + 1/3.
        (
            t("a", t("b")),
            t("a", t("b", t("a"))),
            t("b", t("a")),
            "max",
        ),
        # TED / (|a| + |b|): 2/3 > 1/3 + 1/4.
        (t("a"), t("a", t("b")), t("b", t("b")), "sum"),
    ],
)
def test_naive_normalisations_break_the_triangle_steinhaus_does_not(
    x: Tree, y: Tree, z: Tree, naive: str
) -> None:
    from sciagent.glm.distance import normalised_ted

    def norm(p: Tree, q: Tree) -> float:
        d = brute_ted(p, q)
        if naive == "max":
            return d / max(tree_size(p), tree_size(q))
        return d / (tree_size(p) + tree_size(q))

    assert norm(x, z) > norm(x, y) + norm(y, z)
    _check_metric(normalised_ted, [x, y, z], 1.0)


# --------------------------------------------------------------------------
# 3. Structure distance
# --------------------------------------------------------------------------


def s(*fs: Feature, link: Link = Link.IDENTITY) -> Structure:
    return Structure(tuple(fs), link)


def test_constants() -> None:
    assert D_MAX == 1.0
    assert UNMATCHED_COST == 1.0
    assert 0.0 < LINK_WEIGHT < 1.0


def test_feature_set_distance_ospa_values() -> None:
    # One-to-one sets reproduce the feature distance.
    assert feature_set_distance((HAWKES,), (S11,)) == pytest.approx(2 / 9)
    # An extra feature costs UNMATCHED_COST / max size.
    assert feature_set_distance((HAWKES,), (HAWKES, Periodic())) == pytest.approx(1 / 2)
    assert feature_set_distance((), ()) == 0.0
    assert feature_set_distance((), (HAWKES,)) == 1.0
    # Order inside a set is irrelevant.
    assert feature_set_distance(
        (HAWKES, Periodic()), (Periodic(), S11)
    ) == pytest.approx(feature_set_distance((Periodic(), HAWKES), (S11, Periodic())))


def test_v1_reference_points() -> None:
    hawkes, season, s11 = s(HAWKES), s(Periodic()), s(S11)
    d_hs11 = structure_distance(hawkes, s11)
    d_hp = structure_distance(hawkes, season)
    d_s11p = structure_distance(s11, season)
    assert d_hs11 == pytest.approx((1 - LINK_WEIGHT) * 2 / 9)
    assert d_hp == pytest.approx((1 - LINK_WEIGHT) * 8 / 9)
    # S11 is near Hawkes and far from seasonality.
    assert d_hs11 < 0.25 < 0.6 < d_hp
    assert d_s11p == pytest.approx(d_hp)


def test_link_term() -> None:
    a = s(HAWKES, link=Link.IDENTITY)
    b = s(HAWKES, link=Link.EXP)
    assert structure_distance(a, b) == pytest.approx(LINK_WEIGHT)
    c = s(Periodic(), Trend(), link=Link.SOFTPLUS)
    assert structure_distance(a, c) <= D_MAX


def test_distance_is_on_canonical_forms() -> None:
    # Product commutes and duplicates collapse: equivalent structures are at 0.
    p1 = Product(Periodic(), Trend())
    p2 = Product(Trend(), Periodic())
    assert structure_distance(s(p1), s(p2)) == 0.0
    # A repeated psi-free feature is one column; a repeated Excite is two
    # timescales, so it is a different structure.
    assert structure_distance(s(Trend(), Trend()), s(Trend())) == 0.0
    assert structure_distance(s(HAWKES, HAWKES), s(HAWKES)) == pytest.approx(
        (1 - LINK_WEIGHT) * UNMATCHED_COST / 2
    )


@settings(max_examples=40)
@given(structures(SIZE_SIGN), structures(SIZE_SIGN, max_depth=2), structures(SIZE_SIGN))
def test_structure_distance_metric_axioms(
    a: Structure, b: Structure, c: Structure
) -> None:
    _check_metric(structure_distance, [a, b, c], D_MAX)


@settings(max_examples=40)
@given(structures(SIZE_SIGN), structures(SIZE_SIGN))
def test_identity_of_indiscernibles(a: Structure, b: Structure) -> None:
    same = structure_hash(canonicalise(a)) == structure_hash(canonicalise(b))
    assert (structure_distance(a, b) == 0.0) == same
    assert structure_distance(a, a) == 0.0
    assert structure_distance(a, canonicalise(a)) == 0.0


def _sized_sets(min_size: int, max_size: int) -> st.SearchStrategy[list[Feature]]:
    return st.lists(
        features(SIZE_SIGN, max_depth=2),
        min_size=min_size,
        max_size=max_size,
        unique=True,
    )


@settings(max_examples=60)
@given(_sized_sets(0, 1), _sized_sets(3, 4), _sized_sets(0, 2))
def test_feature_set_triangle_with_large_middle(
    x: list[Feature], y: list[Feature], z: list[Feature]
) -> None:
    # The non-trivial case of the OSPA triangle proof: the middle set is the
    # largest, so it sets the normaliser on both right-hand terms.
    d = feature_set_distance
    sx, sy, sz = tuple(x), tuple(y), tuple(z)
    assert d(sx, sz) <= d(sx, sy) + d(sy, sz) + TOL


def test_feature_set_triangle_adversarial_shapes() -> None:
    # Shared core, disjoint extras, large middle: the shape that breaks
    # "matched cost / max size" when the unmatched cost exceeds the pair bound.
    core: Feature = HAWKES
    extras: list[Feature] = [Periodic(), Trend(), Product(Periodic(), Trend()), S11]
    sets: list[tuple[Feature, ...]] = [
        (core,),
        (core, extras[0]),
        (core, extras[1]),
        (core, *extras[:3]),
        tuple(extras),
        (extras[0],),
        (),
    ]
    _check_metric(feature_set_distance, sets, 1.0)


def test_nearest() -> None:
    library = [s(Periodic()), s(HAWKES), s(S11)]
    query = s(Excite(KernelKind.EXP, Above("size"), ALL))
    # Above(Mark(size)) is one insertion from Mark(size), two edits from One.
    idx, dist = nearest(query, library)
    assert idx == 2
    assert dist == min(structure_distance(query, x) for x in library)
    # Ties go to the lowest index.
    assert nearest(s(HAWKES), [s(HAWKES), s(HAWKES)]) == (0, 0.0)
    with pytest.raises(EmptyLibraryError):
        nearest(s(HAWKES), [])


def test_gate_and_phase_window_trees() -> None:
    g = Gate(HAWKES, PhaseWindow())
    assert feature_tree(g).children[1] == t("PhaseWindow")


@pytest.mark.slow
@settings(max_examples=600)
@given(structures(SIZE_SIGN), structures(SIZE_SIGN), structures(SIZE_SIGN))
def test_structure_distance_metric_axioms_many(
    a: Structure, b: Structure, c: Structure
) -> None:
    _check_metric(structure_distance, [a, b, c], D_MAX)
    same = structure_hash(canonicalise(a)) == structure_hash(canonicalise(b))
    assert (structure_distance(a, b) == 0.0) == same


@pytest.mark.slow
@settings(max_examples=400)
@given(_sized_sets(0, 4), _sized_sets(0, 4), _sized_sets(0, 4))
def test_feature_set_triangle_many(
    x: list[Feature], y: list[Feature], z: list[Feature]
) -> None:
    _check_metric(feature_set_distance, [tuple(x), tuple(y), tuple(z)], 1.0)


def test_null_structure_distances() -> None:
    """OSPA of two empty sets is 0; empty against k >= 1 is 1 (all unmatched)."""
    null = Structure(())
    assert structure_distance(null, null) == 0.0
    assert structure_distance(null, Structure((), Link.EXP)) == pytest.approx(
        LINK_WEIGHT
    )
    for other in (s(HAWKES), s(HAWKES, Periodic()), s(HAWKES, Periodic(), Trend())):
        assert structure_distance(null, other) == pytest.approx(1 - LINK_WEIGHT)
        assert structure_distance(other, null) == pytest.approx(1 - LINK_WEIGHT)
    assert structure_distance(null, s(HAWKES, link=Link.EXP)) == pytest.approx(1.0)
    assert structure_distance(Structure((), Link.SOFTPLUS), s(HAWKES)) <= D_MAX


def test_null_structure_is_a_point_of_the_metric_space() -> None:
    null = Structure(())
    triple = [null, s(HAWKES), s(HAWKES, Periodic(), link=Link.EXP)]
    for a, b, c in itertools.permutations(triple, 3):
        assert structure_distance(a, c) <= (
            structure_distance(a, b) + structure_distance(b, c) + TOL
        )
    assert structure_hash(null) != structure_hash(s(HAWKES))
