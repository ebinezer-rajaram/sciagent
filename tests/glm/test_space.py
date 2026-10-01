"""Instrument tests for the hypothesis-space size (SPEC §2.1, last paragraph).

The size of the space is a reported number that SPEC §2.1 makes a design
requirement ("far larger than any budget, and larger than the B-sparse
dictionary"), so :mod:`sciagent.glm.space` is an instrument. Its counts come
from a combinatorial argument over the canonical normal form; these tests
check that argument three ways:

* by hand, on channel sets small enough to count on paper;
* against brute force: raw trees of depth <= D built from the grammar,
  canonicalised and de-duplicated, with no use of the normal-form argument;
* the depth rule is checked against ``canonical_feature`` itself.
"""

from __future__ import annotations

import itertools
import math

import pytest
from strategies import MAGNITUDE_ONLY, SIZE_SIGN

from sciagent.glm.canonical import (
    canonical_feature,
    canonicalise,
    sort_key,
    structure_hash,
)
from sciagent.glm.grammar import (
    ALL,
    MAX_DEPTH,
    MAX_FEATURES,
    ChannelSpec,
    Excite,
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
    depth,
    n_columns,
    validate,
)
from sciagent.glm.space import (
    Alphabet,
    SpaceError,
    alphabet,
    count_features,
    count_features_by_depth,
    count_structure_points,
    count_structures,
    dictionary_size,
    enumerate_features,
    enumerate_raw,
    feasible_shapes,
    psi_distribution,
    psi_points,
    psi_summary,
)

NO_CHANNELS: tuple[ChannelSpec, ...] = ()


def _small_alphabets() -> list[Alphabet]:
    """Reduced atom / condition sets, for brute force at depth 3."""
    full = alphabet(SIZE_SIGN)
    hawkes = Excite(KernelKind.EXP, One(), ALL)
    return [
        Alphabet((Trend(),), (PhaseWindow(),)),
        Alphabet((hawkes, Periodic()), (PhaseWindow(),)),
        Alphabet((hawkes, Periodic(), Trend()), (PhaseWindow(), LastMarkAbove("size"))),
        Alphabet(
            (hawkes, Periodic(), Trend(), full.atoms[1]),
            (PhaseWindow(), LastMarkAbove("size")),
        ),
        Alphabet((hawkes, Periodic(), Trend()), (PhaseWindow(),)),
    ]


# --------------------------------------------------------------------------
# The alphabet: depth-1 atoms and conditions
# --------------------------------------------------------------------------


class TestAlphabet:
    def test_no_channels_gives_five_atoms(self) -> None:
        """Excite(K, One, all) for three kernels, Periodic, Trend."""
        alpha = alphabet(NO_CHANNELS)
        assert len(alpha.atoms) == 5
        for kernel in KernelKind:
            assert Excite(kernel, One(), ALL) in alpha.atoms
        assert Periodic() in alpha.atoms
        assert Trend() in alpha.atoms
        assert alpha.conds == (PhaseWindow(),)

    def test_pointproc_channels(self) -> None:
        """3 kernels x (6 marks on all + 5 on each of +, -) + 2 = 50.

        The six marks are One, Mark(size), Mark(sign), Pow, ExpOf, Above(size).
        ``Mark(sign)`` is forbidden on the signed sources, where it is a
        constant, so those carry five.
        """
        alpha = alphabet(SIZE_SIGN)
        assert len(alpha.atoms) == 3 * (6 + 5 + 5) + 2 == 50
        assert len(alpha.conds) == 2  # LastMarkAbove(size), PhaseWindow

    def test_no_constant_mark_on_a_signed_source(self) -> None:
        alpha = alphabet(SIZE_SIGN)
        for kernel in KernelKind:
            for kind in (SourceKind.POSITIVE, SourceKind.NEGATIVE):
                assert Excite(kernel, Mark("sign"), Source(kind, "sign")) not in (
                    alpha.atoms
                )
            assert Excite(kernel, Mark("sign"), ALL) in alpha.atoms

    def test_qtm_channels(self) -> None:
        """3 kernels x (One, Mark, ExpOf, Above) x all + 2; no Pow on a REAL."""
        alpha = alphabet(MAGNITUDE_ONLY)
        assert len(alpha.atoms) == 3 * 4 * 1 + 2 == 14
        assert len(alpha.conds) == 2

    @pytest.mark.parametrize("channels", [NO_CHANNELS, SIZE_SIGN, MAGNITUDE_ONLY])
    def test_atoms_are_valid_sorted_and_distinct(
        self, channels: tuple[ChannelSpec, ...]
    ) -> None:
        alpha = alphabet(channels)
        keys = [sort_key(a) for a in alpha.atoms]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)
        for atom in alpha.atoms:
            validate(Structure((atom,)), channels)
            assert canonical_feature(atom) == atom
        for cond in alpha.conds:
            validate(Structure((Gate(Trend(), cond),)), channels)


# --------------------------------------------------------------------------
# The depth rule
# --------------------------------------------------------------------------


class TestShapes:
    """A class is ``(n atoms, g gates)``; which are reachable at depth <= D."""

    def test_depth_one_is_a_single_atom(self) -> None:
        assert feasible_shapes(1) == ((1, 0),)

    def test_depth_two(self) -> None:
        assert feasible_shapes(2) == ((1, 0), (1, 1), (2, 0))

    def test_depth_three_by_hand(self) -> None:
        """Products of depth-2 shapes plus one more gate; (4,1) and (3,2) fail."""
        assert feasible_shapes(3) == (
            (1, 0),
            (1, 1),
            (1, 2),
            (2, 0),
            (2, 1),
            (2, 2),
            (3, 0),
            (3, 1),
            (4, 0),
        )

    def test_canonical_depth_is_the_minimum_over_all_trees(self) -> None:
        """The normal form is depth-minimal, checked against the independent DP.

        ``feasible_shapes`` is derived from the grammar alone (a product of two
        depth <= D-1 trees, or a gate on one). ``canonical_feature`` is the
        deterministic round-robin / shallowest-first construction. They must
        agree on the depth of every class, or the normal form is not
        depth-minimal and a valid structure could canonicalise to an invalid
        one.
        """
        for n in range(1, 7):
            for g in range(0, 7):
                tree: Feature = Trend()
                for _ in range(n - 1):
                    tree = Product(tree, Periodic())
                for _ in range(g):
                    tree = Gate(tree, PhaseWindow())
                minimal = next(d for d in range(1, 20) if (n, g) in feasible_shapes(d))
                assert depth(canonical_feature(tree)) == minimal, (n, g)

    def test_shapes_nest(self) -> None:
        for d in range(1, 6):
            assert set(feasible_shapes(d)) <= set(feasible_shapes(d + 1))

    def test_depth_zero_is_an_error(self) -> None:
        with pytest.raises(SpaceError):
            feasible_shapes(0)


# --------------------------------------------------------------------------
# Counting and enumerating features
# --------------------------------------------------------------------------


class TestFeatureCounts:
    def test_no_channels_by_hand(self) -> None:
        """5 atoms, 1 condition: 5; 5 + 15 + 5 = 25; then 200.

        Depth 3 adds (3,0) C(7,3)=35, (2,1) 15, (4,0) C(8,4)=70, (3,1) 35,
        (2,2) 15, (1,2) 5.
        """
        assert [count_features(NO_CHANNELS, d) for d in (1, 2, 3)] == [5, 25, 200]
        assert count_features_by_depth(NO_CHANNELS, 3) == (5, 20, 175)

    def test_pointproc_depth_two_by_hand(self) -> None:
        """50 atoms, 2 conds: 50 + C(51,2) + 50*2 = 50 + 1275 + 100."""
        assert count_features(SIZE_SIGN, 2) == 1425
        assert count_features_by_depth(SIZE_SIGN, 2) == (50, 1375)

    def test_pointproc_depth_three_by_hand(self) -> None:
        a, c = 50, 2

        def ms(k: int, n: int) -> int:
            return math.comb(k + n - 1, n)

        shapes = [
            (1, 0),
            (1, 1),
            (1, 2),
            (2, 0),
            (2, 1),
            (2, 2),
            (3, 0),
            (3, 1),
            (4, 0),
        ]
        expected = sum(ms(a, n) * ms(c, g) for n, g in shapes)
        assert count_features(SIZE_SIGN, 3) == expected == 367_075
        # By hand: 50 + 100 + 150 (n=1) + 1275 + 2550 + 3825 (n=2)
        # + 22100 + 44200 (n=3) + 292825 (n=4, C(53,4)).
        assert count_features_by_depth(SIZE_SIGN, 3) == (50, 1375, 365_650)

    def test_by_depth_sums_to_total(self) -> None:
        for channels in (NO_CHANNELS, SIZE_SIGN, MAGNITUDE_ONLY):
            for d in (1, 2, 3):
                assert sum(count_features_by_depth(channels, d)) == count_features(
                    channels, d
                )

    def test_depth_out_of_range_is_an_error(self) -> None:
        for bad in (0, MAX_DEPTH + 1):
            with pytest.raises(SpaceError):
                count_features(SIZE_SIGN, bad)
            with pytest.raises(SpaceError):
                enumerate_features(SIZE_SIGN, bad)


class TestEnumeration:
    @pytest.mark.parametrize("channels", [NO_CHANNELS, SIZE_SIGN, MAGNITUDE_ONLY])
    @pytest.mark.parametrize("max_depth", [1, 2])
    def test_formula_matches_brute_force(
        self, channels: tuple[ChannelSpec, ...], max_depth: int
    ) -> None:
        """Raw trees -> canonicalise -> de-dup is the definition of the space."""
        alpha = alphabet(channels)
        brute = enumerate_raw(alpha, max_depth)
        assert enumerate_features(channels, max_depth) == brute
        assert count_features(channels, max_depth) == len(brute)

    @pytest.mark.parametrize("alpha", _small_alphabets())
    def test_formula_matches_brute_force_at_depth_three_reduced(
        self, alpha: Alphabet
    ) -> None:
        brute = enumerate_raw(alpha, 3)
        assert enumerate_features(alpha, 3) == brute
        assert count_features(alpha, 3) == len(brute)
        by_depth = count_features_by_depth(alpha, 3)
        assert by_depth == tuple(
            sum(1 for f in brute if depth(f) == d) for d in (1, 2, 3)
        )

    @pytest.mark.slow
    def test_formula_matches_brute_force_qtm_depth_three(self) -> None:
        alpha = alphabet(MAGNITUDE_ONLY)
        brute = enumerate_raw(alpha, 3)
        assert len(brute) == count_features(alpha, 3) == 4774
        assert enumerate_features(alpha, 3) == brute

    @pytest.mark.parametrize("channels", [NO_CHANNELS, SIZE_SIGN, MAGNITUDE_ONLY])
    def test_every_feature_is_canonical_valid_distinct_and_sorted(
        self, channels: tuple[ChannelSpec, ...]
    ) -> None:
        feats = enumerate_features(channels, 3 if len(channels) < 2 else 2)
        keys = [sort_key(f) for f in feats]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)
        for f in feats:
            assert canonical_feature(f) == f
            validate(Structure((f,)), channels)
            assert depth(f) <= MAX_DEPTH

    @pytest.mark.slow
    def test_pointproc_depth_three_is_materialisable_and_matches(self) -> None:
        feats = enumerate_features(SIZE_SIGN, 3)
        assert len(feats) == count_features(SIZE_SIGN, 3)
        assert len({sort_key(f) for f in feats}) == len(feats)
        histogram = [sum(1 for f in feats if depth(f) == d) for d in (1, 2, 3)]
        assert tuple(histogram) == count_features_by_depth(SIZE_SIGN, 3)


# --------------------------------------------------------------------------
# Shape-parameter grid points and the B-sparse dictionary
# --------------------------------------------------------------------------


class TestPsiPoints:
    def test_atom_grid_sizes(self) -> None:
        assert psi_points(Excite(KernelKind.EXP, One(), ALL)) == 6
        assert psi_points(Excite(KernelKind.POWER, One(), ALL)) == 3 * 4
        assert psi_points(Excite(KernelKind.GAMMA, One(), ALL)) == 3 * 4
        assert psi_points(Periodic()) == 5
        assert psi_points(Trend()) == 1

    def test_gate_and_product_multiply(self) -> None:
        phase = Gate(Trend(), PhaseWindow())
        assert psi_points(phase) == 5 * 4
        assert psi_points(Product(Periodic(), phase)) == 5 * 5 * 4

    def test_no_channels_depth_one_distribution(self) -> None:
        """ψ counts {6, 12, 12, 5, 1}: min 1, lower median 6, max 12."""
        summary = psi_summary(NO_CHANNELS, 1)
        assert (summary.minimum, summary.median, summary.maximum) == (1, 6, 12)
        assert summary.n_features == 5
        assert psi_distribution(NO_CHANNELS, 1) == ((1, 1), (5, 1), (6, 1), (12, 2))

    @pytest.mark.parametrize(
        ("channels", "max_depth"),
        [(NO_CHANNELS, 3), (MAGNITUDE_ONLY, 3), (SIZE_SIGN, 2), (SIZE_SIGN, 1)],
    )
    def test_distribution_matches_enumeration(
        self, channels: tuple[ChannelSpec, ...], max_depth: int
    ) -> None:
        feats = enumerate_features(channels, max_depth)
        tally: dict[int, int] = {}
        for f in feats:
            tally[psi_points(f)] = tally.get(psi_points(f), 0) + 1
        assert psi_distribution(channels, max_depth) == tuple(sorted(tally.items()))
        values = sorted(psi_points(f) for f in feats)
        summary = psi_summary(channels, max_depth)
        assert summary.minimum == values[0]
        assert summary.maximum == values[-1]
        assert summary.median == values[(len(values) - 1) // 2]
        assert summary.n_features == len(feats)


class TestDictionary:
    def test_no_channels_by_hand(self) -> None:
        """Σ w·cols over depth <= 1 is 6+12+12+5*2+1 = 41.

        Depth 2 adds the unordered pairs, h_2 of the weights (6,12,12,10,1) =
        (41² + 425)/2 = 1053, and each atom gated by PhaseWindow (20 points):
        41 * 20 = 820. Total 41 + 1053 + 820 = 1914.
        """
        assert dictionary_size(NO_CHANNELS, 1).n_columns == 41
        assert dictionary_size(NO_CHANNELS, 2).n_columns == 1914
        assert dictionary_size(NO_CHANNELS, 2).n_features == 25

    def test_pointproc_by_hand(self) -> None:
        """Weights w = kernel ψ x mark ψ, summed over the 50 atoms.

        Kernel ψ points 6 + 12 + 12 (Exp, Power, Gamma). Marks: One 1, Mark 1,
        Pow 4, ExpOf 5, Above 4, and Mark(sign) 1 on ``all`` only, so 16 on
        ``all`` and 15 on each signed source: 46. Excite: 30 x 46 = 1380;
        Periodic 5 (x2 columns), Trend 1. Depth 1: 1386 groups, 1391 columns.
        Depth 2 adds the pairs, h_2 = (p1² + p2)/2, and each atom gated by one
        of two conditions (LastMarkAbove 4 points, PhaseWindow 20: 24 total).
        Columns: p2 = 324 x 178 + 100 + 1 = 57773, h_2 = (1391² + 57773)/2 =
        996327, gated 1391 x 24 = 33384. Groups: p2 = 57698, h_2 = 989347,
        gated 1386 x 24 = 33264.
        """
        size = dictionary_size(SIZE_SIGN, 2)
        assert size.n_features == 1425
        assert size.n_groups == 1386 + 989_347 + 33_264 == 1_023_997
        assert size.n_columns == 1391 + 996_327 + 33_384 == 1_031_102
        assert dictionary_size(SIZE_SIGN, 1).n_columns == 1391

    @pytest.mark.parametrize("channels", [NO_CHANNELS, SIZE_SIGN, MAGNITUDE_ONLY])
    def test_matches_enumeration(self, channels: tuple[ChannelSpec, ...]) -> None:
        feats = enumerate_features(channels, 2)
        size = dictionary_size(channels)
        assert size.n_features == len(feats)
        assert size.n_groups == sum(psi_points(f) for f in feats)
        assert size.n_columns == sum(psi_points(f) * n_columns(f) for f in feats)

    def test_columns_at_least_groups(self) -> None:
        size = dictionary_size(SIZE_SIGN)
        assert size.n_columns >= size.n_groups >= size.n_features


# --------------------------------------------------------------------------
# Counting structures
# --------------------------------------------------------------------------


def _brute_structures(
    alpha: Alphabet, max_depth: int, max_features: int
) -> tuple[int, int]:
    """(distinct canonical structure hashes, Σ ψ points), by enumeration."""
    feats = enumerate_features(alpha, max_depth)
    seen: dict[str, int] = {}
    for k in range(0, max_features + 1):
        for combo in itertools.combinations_with_replacement(feats, k):
            for link in Link:
                s = canonicalise(Structure(combo, link))
                points = math.prod(psi_points(f) for f in s.features)
                seen[structure_hash(Structure(combo, link))] = points
    return len(seen), sum(seen.values())


class TestStructures:
    def test_no_channels_depth_one_by_hand(self) -> None:
        """5 features, 3 links. K=1: 18. K=2: 60.

        Per link: the null structure (K=0) is 1; K=1 adds 5, K=2 adds 15 - 1,
        because the 15 two-multisets include {Trend, Trend}, which has no ψ
        slots and collapses to {Trend}.
        """
        assert count_structures(NO_CHANNELS, 1, 0) == 3  # just the nulls
        assert count_structures(NO_CHANNELS, 1, 1) == 3 * (1 + 5) == 18
        assert count_structures(NO_CHANNELS, 1, 2) == 3 * (1 + 5 + 14) == 60

    def test_pointproc_by_hand(self) -> None:
        """50 features: per link, K=0 is 1 (the null), K=1 adds 50, K=2 adds
        C(51,2) - 1 pairs (the ψ-free {Trend, Trend} collapses). Points: the
        null has one, and Σ w = 1386 over the 50 features for K=1."""
        assert count_structures(SIZE_SIGN, 1, 0) == 3
        assert count_structures(SIZE_SIGN, 1, 1) == 3 * (1 + 50) == 153
        assert count_structures(SIZE_SIGN, 1, 2) == 3 * (1 + 50 + 1274) == 3975
        assert count_structures(SIZE_SIGN, 3, 1) == 3 * (1 + 367_075)
        assert count_structure_points(SIZE_SIGN, 1, 1) == 3 * (1 + 1386) == 4161

    def test_no_channels_depth_one_points_by_hand(self) -> None:
        """Per link: the null is 1 point; Σ ψ over K=1 is 36; K=2 is 787 (ψ-ful
        pairs) + 35 (with Trend)."""
        assert count_structure_points(NO_CHANNELS, 1, 0) == 3
        assert count_structure_points(NO_CHANNELS, 1, 1) == 3 * (1 + 36) == 111
        assert count_structure_points(NO_CHANNELS, 1, 2) == 3 * (1 + 36 + 787 + 35)

    @pytest.mark.parametrize(("max_depth", "max_features"), [(1, 4), (2, 2), (2, 3)])
    def test_matches_brute_force_no_channels(
        self, max_depth: int, max_features: int
    ) -> None:
        alpha = alphabet(NO_CHANNELS)
        n_brute, points_brute = _brute_structures(alpha, max_depth, max_features)
        assert count_structures(alpha, max_depth, max_features) == n_brute
        assert count_structure_points(alpha, max_depth, max_features) == points_brute

    @pytest.mark.parametrize(
        "alpha",
        [
            a if i in (0, 1, 4) else pytest.param(a, marks=pytest.mark.slow)
            for i, a in enumerate(_small_alphabets())
        ],
    )
    def test_matches_brute_force_reduced_alphabets(self, alpha: Alphabet) -> None:
        n_brute, points_brute = _brute_structures(alpha, 3, 2)
        assert count_structures(alpha, 3, 2) == n_brute
        assert count_structure_points(alpha, 3, 2) == points_brute

    def test_psi_free_repeats_collapse_but_psi_repeats_do_not(self) -> None:
        alpha = Alphabet((Trend(), Periodic()), ())
        # K=2 per link: null, {T}, {P}, {T,P}, {P,P}; {T,T} collapses into {T}.
        assert count_structures(alpha, 1, 2) == 3 * 5

    def test_monotone_in_depth_and_features(self) -> None:
        for channels in (SIZE_SIGN, MAGNITUDE_ONLY):
            counts = [count_structures(channels, d, MAX_FEATURES) for d in (1, 2, 3)]
            assert counts == sorted(counts)
            assert len(set(counts)) == 3
            by_k = [count_structures(channels, 3, k) for k in range(1, 5)]
            assert by_k == sorted(by_k)

    def test_bad_arguments_are_errors(self) -> None:
        with pytest.raises(SpaceError):
            count_structures(SIZE_SIGN, 3, -1)
        with pytest.raises(SpaceError):
            count_structures(SIZE_SIGN, 0, 4)

    def test_spec_requirement_space_dwarfs_budgets_and_dictionary(self) -> None:
        """SPEC §2.1: far larger than F = 40 and 10 F, and than the dictionary."""
        for channels in (SIZE_SIGN, MAGNITUDE_ONLY):
            space = count_structures(channels, MAX_DEPTH, MAX_FEATURES)
            points = count_structure_points(channels, MAX_DEPTH, MAX_FEATURES)
            columns = dictionary_size(channels).n_columns
            assert space > 400 * 1000
            assert space > columns
            assert points > space
