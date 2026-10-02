"""The truth prior over structures and ψ (SPEC §3, §4.1; instrument test 6)."""

from __future__ import annotations

import math
from collections import Counter

import pytest

from environments.pointproc.truths_v2 import STRUCTURE_PRIOR, TRUTH_PSI_GRIDS
from environments.pointproc.v2 import CHANNELS
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.grammar import (
    MAX_DEPTH,
    Excite,
    Feature,
    Gate,
    Link,
    Periodic,
    Product,
    Structure,
    Trend,
    depth,
    psi_slots,
    validate,
)
from sciagent.glm.grids import PSI_GRIDS
from sciagent.scenarios.prior import (
    PriorError,
    StructurePrior,
    atom_distribution,
    is_out_of_dictionary,
    sample_psi,
    sample_structure,
)
from sciagent.scenarios.streams import stream as derive_generator


def _atoms(f: Feature) -> list[Feature]:
    match f:
        case Excite() | Periodic() | Trend():
            return [f]
        case Product(left=a, right=b):
            return _atoms(a) + _atoms(b)
        case Gate(feature=inner):
            return _atoms(inner)


def _draw(n: int, ood: bool, key: str = "prior") -> list[Structure]:
    rng = derive_generator(11, key)
    return [
        sample_structure(STRUCTURE_PRIOR, CHANNELS, rng, out_of_dictionary=ood)
        for _ in range(n)
    ]


@pytest.mark.parametrize("ood", [False, True])
def test_samples_are_valid_canonical_and_honour_the_dictionary_flag(ood: bool) -> None:
    for s in _draw(300, ood):
        validate(s, CHANNELS)
        assert canonicalise(s) == s
        assert 1 <= len(s.features) <= 4
        assert max(depth(f) for f in s.features) <= MAX_DEPTH
        assert is_out_of_dictionary(s) is ood
        if ood:
            assert sum(depth(f) == 3 for f in s.features) == 1
        # Distinct features: duplicates are resampled.
        assert len({structure_hash(Structure((f,), s.link)) for f in s.features}) == (
            len(s.features)
        )
        # Trend has horizon-dependent semantics, so truths never use it.
        assert not any(isinstance(a, Trend) for f in s.features for a in _atoms(f))


def test_sampling_is_deterministic() -> None:
    assert _draw(50, True) == _draw(50, True)
    assert _draw(50, True) != _draw(50, True, key="other")


@pytest.mark.slow
def test_link_and_size_frequencies_match_the_declared_prior() -> None:
    draws = _draw(700, False) + _draw(700, True)
    n = len(draws)
    links = Counter(s.link for s in draws)
    for link, p in STRUCTURE_PRIOR.link_probs:
        # Binomial sd at n = 1400 is at most 0.0134; 4.5 sd.
        assert abs(links[link] / n - p) < 0.06, (link, links[link] / n, p)
    sizes = Counter(len(s.features) for s in draws)
    for k, p in STRUCTURE_PRIOR.n_features_probs:
        # Duplicate resampling only touches structures, not K.
        assert abs(sizes[k] / n - p) < 0.06, (k, sizes[k] / n, p)


def test_atom_distribution_is_normalised_and_valid() -> None:
    atoms = atom_distribution(STRUCTURE_PRIOR, CHANNELS)
    assert math.isclose(math.fsum(p for _, p in atoms), 1.0, abs_tol=1e-12)
    assert all(p >= 0 for _, p in atoms)
    for atom, p in atoms:
        if p > 0:
            validate(Structure((atom,)), CHANNELS)
    assert all(p == 0 for a, p in atoms if isinstance(a, Trend))


def test_psi_is_on_the_fitting_grid_and_inside_the_truth_grid() -> None:
    rng = derive_generator(5, "psi")
    for s in _draw(200, True):
        psi = sample_psi(s, TRUTH_PSI_GRIDS, rng)
        assert len(psi) == len(s.features)
        for feature, assignment in zip(s.features, psi, strict=True):
            assert tuple(assignment) == psi_slots(feature)
            for slot, value in assignment.items():
                assert value in PSI_GRIDS[slot.name]
                assert value in TRUTH_PSI_GRIDS.get(slot.name, PSI_GRIDS[slot.name])


def test_a_truth_grid_off_the_fitting_grid_is_refused() -> None:
    s = Structure((Periodic(),), Link.EXP)
    with pytest.raises(PriorError):
        sample_psi(s, {"period": (7.0,)}, derive_generator(0, "x"))


def test_a_prior_with_bad_probabilities_is_refused() -> None:
    with pytest.raises(PriorError):
        StructurePrior(
            link_probs=((Link.IDENTITY, 0.5),),
            n_features_probs=STRUCTURE_PRIOR.n_features_probs,
            depth_probs=STRUCTURE_PRIOR.depth_probs,
            shape_probs=STRUCTURE_PRIOR.shape_probs,
            atom_kind_probs=STRUCTURE_PRIOR.atom_kind_probs,
            kernel_probs=STRUCTURE_PRIOR.kernel_probs,
            mark_probs=STRUCTURE_PRIOR.mark_probs,
            source_probs=STRUCTURE_PRIOR.source_probs,
            cond_probs=STRUCTURE_PRIOR.cond_probs,
        )
