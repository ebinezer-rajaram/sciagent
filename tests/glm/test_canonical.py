"""Instrument tests for canonicalisation and structure hashing (SPEC §2.2, §6.3.3).

Canonicalisation decides which proposals count as the same model, so it is an
instrument: a false merge hides a structure from the evaluation, a missed merge
lets one structure be scored twice. The properties here are the §6.3 test 3
gate: idempotent, and equivalence-preserving under every rewrite the
equivalence relation licenses.
"""

from __future__ import annotations

from collections import Counter

from hypothesis import given
from hypothesis import strategies as st
from strategies import SIZE_SIGN, features, structures

from sciagent.glm.canonical import (
    canonical_feature,
    canonicalise,
    sort_key,
    structure_hash,
)
from sciagent.glm.grammar import (
    ALL,
    MAX_DEPTH,
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
    Source,
    SourceKind,
    Structure,
    Trend,
    channels_used,
    depth,
    n_columns,
    psi_slots,
    validate,
)

CHANNELS = SIZE_SIGN
SIGN_POS = Source(SourceKind.POSITIVE, "sign")


# --------------------------------------------------------------------------
# Equivalence-preserving rewrites (the oracle for the property tests)
# --------------------------------------------------------------------------


def _root_rewrites(f: Feature) -> list[Feature]:
    """Every equivalent form of ``f`` reachable by one rewrite at its root."""
    out: list[Feature] = [f]
    match f:
        case Product(left=a, right=b):
            out.append(Product(b, a))
            if isinstance(a, Product):
                out.append(Product(a.left, Product(a.right, b)))
            if isinstance(b, Product):
                out.append(Product(Product(a, b.left), b.right))
            if isinstance(a, Gate):
                out.append(Gate(Product(a.feature, b), a.cond))
            if isinstance(b, Gate):
                out.append(Gate(Product(a, b.feature), b.cond))
        case Gate(feature=Product(left=a, right=b), cond=c):
            out.append(Product(Gate(a, c), b))
            out.append(Product(a, Gate(b, c)))
        case _:
            pass
    if isinstance(f, Gate) and isinstance(f.feature, Gate):
        out.append(Gate(Gate(f.feature.feature, f.cond), f.feature.cond))
    return out


@st.composite
def equivalent(draw: st.DrawFn, f: Feature) -> Feature:
    """A feature equivalent to ``f``: rewrite the children, then the root."""
    g: Feature
    match f:
        case Product(left=a, right=b):
            g = Product(draw(equivalent(a)), draw(equivalent(b)))
        case Gate(feature=inner, cond=c):
            g = Gate(draw(equivalent(inner)), c)
        case _:
            g = f
    return draw(st.sampled_from(_root_rewrites(g)))


@st.composite
def equivalent_structures(draw: st.DrawFn, s: Structure) -> Structure:
    """A structure equivalent to ``s``: rewritten trees, permuted multiset."""
    rewritten = []
    for f in s.features:
        g = f
        for _ in range(draw(st.integers(min_value=0, max_value=3))):
            g = draw(equivalent(g))
        rewritten.append(g)
    return Structure(tuple(draw(st.permutations(rewritten))), s.link)


# --------------------------------------------------------------------------
# Property tests (§6.3 test 3)
# --------------------------------------------------------------------------


@given(structures(CHANNELS))
def test_canonicalise_is_idempotent(s: Structure) -> None:
    once = canonicalise(s)
    assert canonicalise(once) == once


@given(features(CHANNELS))
def test_canonical_feature_is_idempotent(f: Feature) -> None:
    once = canonical_feature(f)
    assert canonical_feature(once) == once


@given(st.data())
def test_equivalent_proposals_share_canonical_form_and_hash(
    data: st.DataObject,
) -> None:
    s = data.draw(structures(CHANNELS))
    t = data.draw(equivalent_structures(s))
    assert canonicalise(t) == canonicalise(s)
    assert structure_hash(t) == structure_hash(s)


@given(st.data())
def test_equivalent_features_share_canonical_form(data: st.DataObject) -> None:
    f = data.draw(features(CHANNELS))
    g = data.draw(equivalent(f))
    assert canonical_feature(g) == canonical_feature(f)


@given(structures(CHANNELS))
def test_canonical_output_is_valid(s: Structure) -> None:
    validate(canonicalise(s), CHANNELS)


@given(features(CHANNELS))
def test_canonical_feature_never_deeper(f: Feature) -> None:
    """The normal form is depth-minimal in its class, so it never leaves the
    space: validity (depth <= MAX_DEPTH) is closed under canonicalisation."""
    assert depth(canonical_feature(f)) <= depth(f)


@given(features(CHANNELS))
def test_canonical_feature_keeps_columns_psi_and_channels(f: Feature) -> None:
    c = canonical_feature(f)
    assert n_columns(c) == n_columns(f)
    assert len(psi_slots(c)) == len(psi_slots(f))
    assert channels_used(c) == channels_used(f)


def _total_columns(s: Structure) -> int:
    return sum(n_columns(f) for f in s.features)


def _psi_free_duplicates(s: Structure) -> bool:
    free = [canonical_feature(f) for f in s.features if not psi_slots(f)]
    return len(free) != len(set(free))


@given(structures(CHANNELS))
def test_canonical_structure_keeps_psi_and_channels(s: Structure) -> None:
    """ψ is relabelled, never lost; columns shrink only by dropping ψ-free
    repeats (which are collinear copies)."""
    c = canonicalise(s)
    assert sum(len(psi_slots(f)) for f in c.features) == sum(
        len(psi_slots(f)) for f in s.features
    )
    assert Counter(channels_used(f) for f in c.features if psi_slots(f)) == Counter(
        channels_used(f) for f in s.features if psi_slots(f)
    )
    if _psi_free_duplicates(s):
        assert _total_columns(c) < _total_columns(s)
    else:
        assert _total_columns(c) == _total_columns(s)
    assert c.link == s.link


@given(features(CHANNELS), features(CHANNELS))
def test_sort_key_is_a_total_order_consistent_with_equality(
    a: Feature, b: Feature
) -> None:
    assert (sort_key(a) == sort_key(b)) == (a == b)
    assert (sort_key(a) < sort_key(b)) or (sort_key(a) > sort_key(b)) or a == b


@given(structures(CHANNELS))
def test_canonical_features_are_sorted(s: Structure) -> None:
    keys = [sort_key(f) for f in canonicalise(s).features]
    assert keys == sorted(keys)


@given(structures(CHANNELS))
def test_hash_is_a_sha256_hex_digest(s: Structure) -> None:
    h = structure_hash(s)
    assert len(h) == 64
    assert int(h, 16) >= 0
    assert structure_hash(canonicalise(s)) == h


# --------------------------------------------------------------------------
# Hand-written cases
# --------------------------------------------------------------------------

EXP_ONE = Excite(KernelKind.EXP, One(), ALL)
EXP_SIZE = Excite(KernelKind.EXP, Mark("size"), ALL)
POW_SIZE = Excite(KernelKind.POWER, Mark("size"), ALL)
GAMMA_ONE = Excite(KernelKind.GAMMA, One(), ALL)
ABOVE = LastMarkAbove("size")
WINDOW = PhaseWindow()


def _s(*fs: Feature, link: Link = Link.IDENTITY) -> Structure:
    return Structure(tuple(fs), link)


def test_product_is_commutative_and_associative() -> None:
    a, b, c = EXP_ONE, Periodic(), Trend()
    forms: list[Feature] = [
        Product(Product(a, b), c),
        Product(a, Product(b, c)),
        Product(Product(c, a), b),
        Product(b, Product(c, a)),
    ]
    assert len({structure_hash(_s(f)) for f in forms}) == 1


def test_gate_distributes_over_product() -> None:
    a, b = EXP_ONE, Periodic()
    forms: list[Feature] = [
        Gate(Product(a, b), ABOVE),
        Product(Gate(a, ABOVE), b),
        Product(b, Gate(a, ABOVE)),
        Product(a, Gate(b, ABOVE)),
    ]
    assert len({structure_hash(_s(f)) for f in forms}) == 1


def test_stacked_gates_commute() -> None:
    f1 = Gate(Gate(EXP_ONE, ABOVE), WINDOW)
    f2 = Gate(Gate(EXP_ONE, WINDOW), ABOVE)
    assert structure_hash(_s(f1)) == structure_hash(_s(f2))


def test_gates_with_distinct_psi_are_not_merged() -> None:
    """Each gate has its own ψ, so two copies of one condition are two gates."""
    one = Gate(EXP_ONE, ABOVE)
    two = Gate(Gate(EXP_ONE, ABOVE), ABOVE)
    assert structure_hash(_s(one)) != structure_hash(_s(two))


def test_feature_multiset_order_is_irrelevant() -> None:
    assert structure_hash(_s(EXP_ONE, Periodic(), Trend())) == structure_hash(
        _s(Trend(), EXP_ONE, Periodic())
    )


def test_canonical_form_never_exceeds_the_depth_of_a_valid_input() -> None:
    """Hoisting every gate would take these past MAX_DEPTH; the normal form
    must not."""
    a, b, c, d = EXP_ONE, Periodic(), Trend(), EXP_SIZE
    gated_pair = Product(Gate(a, ABOVE), Gate(b, WINDOW))  # depth 3
    balanced = Product(Product(a, b), Product(c, d))  # depth 3
    for f in (gated_pair, balanced):
        assert depth(f) == MAX_DEPTH
        out = canonicalise(_s(f))
        validate(out, CHANNELS)
        assert depth(out.features[0]) == MAX_DEPTH


def test_psi_free_repeats_are_deduplicated() -> None:
    assert canonicalise(_s(Trend(), Trend())) == _s(Trend())
    tt = Product(Trend(), Trend())
    assert canonicalise(_s(tt, tt, EXP_ONE)).features.count(tt) == 1


def test_trend_and_trend_squared_are_different_features() -> None:
    tt = Product(Trend(), Trend())
    assert len(canonicalise(_s(Trend(), tt)).features) == 2


def test_psi_bearing_repeats_are_kept() -> None:
    """Two ExpK features each get their own profiled timescale."""
    out = canonicalise(_s(EXP_ONE, EXP_ONE))
    assert out.features == (EXP_ONE, EXP_ONE)
    assert structure_hash(_s(EXP_ONE, EXP_ONE)) != structure_hash(_s(EXP_ONE))
    assert structure_hash(_s(Periodic(), Periodic())) != structure_hash(_s(Periodic()))


def test_hash_golden_value() -> None:
    """Freezes the serialisation: a change here changes every stored hash."""
    s = _s(
        Excite(KernelKind.EXP, Mark("size"), ALL),
        Gate(Product(Periodic(), Excite(KernelKind.POWER, One(), SIGN_POS)), ABOVE),
        link=Link.EXP,
    )
    assert structure_hash(s) == GOLDEN


GOLDEN = "8f806d85d97488c83de7f171c4b0bfd1bac8a9fb0a6a53d7a1bc1ced8dc0e190"


# --------------------------------------------------------------------------
# Discrimination sanity: non-equivalent structures must not collide
# --------------------------------------------------------------------------


def _distinct(*structs: Structure) -> None:
    hashes = [structure_hash(s) for s in structs]
    assert len(set(hashes)) == len(hashes)


def test_different_kernels_differ() -> None:
    _distinct(_s(EXP_ONE), _s(Excite(KernelKind.POWER, One(), ALL)), _s(GAMMA_ONE))


def test_different_marks_differ() -> None:
    _distinct(
        _s(EXP_ONE),
        _s(EXP_SIZE),
        _s(Excite(KernelKind.EXP, ExpOf("size"), ALL)),
        _s(Excite(KernelKind.EXP, Above("size"), ALL)),
        _s(Excite(KernelKind.EXP, Mark("sign"), ALL)),
    )


def test_different_sources_differ() -> None:
    neg = Source(SourceKind.NEGATIVE, "sign")
    _distinct(
        _s(EXP_ONE),
        _s(Excite(KernelKind.EXP, One(), SIGN_POS)),
        _s(Excite(KernelKind.EXP, One(), neg)),
    )


def test_different_links_differ() -> None:
    _distinct(*(_s(EXP_ONE, link=link) for link in Link))


def test_different_structure_shapes_differ() -> None:
    a, b = EXP_ONE, Periodic()
    _distinct(
        _s(a, b),
        _s(Product(a, b)),
        _s(a),
        _s(Gate(a, ABOVE)),
        _s(Gate(a, WINDOW)),
        _s(Gate(a, LastMarkAbove("sign"))),
        _s(Product(a, a)),
        _s(a, a),
    )


def test_gate_on_different_factor_of_nonequivalent_products_differs() -> None:
    a, b = EXP_ONE, Periodic()
    _distinct(
        _s(Product(Gate(a, ABOVE), b)),
        _s(Product(Gate(a, ABOVE), Gate(b, ABOVE))),
    )


def test_channel_names_are_part_of_the_hash() -> None:
    _distinct(_s(EXP_SIZE), _s(Excite(KernelKind.EXP, Mark("energy"), ALL)))
