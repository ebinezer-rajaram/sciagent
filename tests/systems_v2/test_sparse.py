"""B-sparse (SPEC §4.1): the group-lasso dictionary, the solver, the path.

Fast tests run on reduced alphabets (``TINY``: an ``ExpK`` atom, ``Periodic``
and the phase window; ``SMALL``: two ``ExpK`` atoms, ``Periodic``, ``Trend``
and both gate conditions). They keep every depth-2 construction (products of
two atoms, gated atoms, a two-column atom) at a size where every group can be
materialised and checked directly. The slow tests run the full pointproc
dictionary (about a million groups) on ~2,000 events. They are the recovery
checks: an in-dictionary depth-1 truth (S11's size excitation), an
in-dictionary depth-2 truth (an excitation gated on the last mark), and the
null.

A ``Product(Excite, Periodic)`` truth is deliberately not among them. On it
the lasso selects ``Gate(Excite, PhaseWindow)`` at the matching period and
phase. That half-cycle gate is a one-column alias of the two-column product,
which pays √2 in the group penalty, and the two have equal validation
likelihood. This is a property of B-sparse, reported, not a test to pass.
"""

from __future__ import annotations

import math
from typing import Final

import numpy as np
import pytest
from scipy import special

from environments.pointproc import v2
from sciagent.glm.canonical import canonical_feature
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.features import BlockCache
from sciagent.glm.grammar import (
    ALL,
    MAX_FEATURES,
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
    PsiSlot,
    Structure,
    Trend,
)
from sciagent.glm.simulate import Coefficients, simulate
from sciagent.glm.space import Alphabet
from sciagent.systems.v2.sparse import (
    B_SPARSE,
    BSparse,
    SparseConfig,
    SparsePath,
    lasso_path,
    rank_features,
    split_dataset,
)
from sciagent.systems.v2.sparse_dictionary import GroupDictionary, GroupKind
from sciagent.systems.v2.systems import InvestigationData

HAWKES_ATOM: Final = Excite(KernelKind.EXP, One(), ALL)
SIZE_ATOM: Final = Excite(KernelKind.EXP, Mark("size"), ALL)
SMALL: Final = Alphabet(
    atoms=(HAWKES_ATOM, SIZE_ATOM, Periodic(), Trend()),
    conds=(LastMarkAbove("size"), PhaseWindow()),
)
#: Units: 6 + 6 + 5 + 1 = 18; indicator columns: 4 + 20 = 24.
SMALL_UNITS: Final = 18
SMALL_INDICATORS: Final = 24
SMALL_CONFIG: Final = SparseConfig(alphabet=SMALL, links=(Link.EXP,), n_lambdas=12)
TINY: Final = Alphabet(atoms=(HAWKES_ATOM, Periodic()), conds=(PhaseWindow(),))
TINY_CONFIG: Final = SparseConfig(
    alphabet=TINY, links=(Link.EXP,), n_lambdas=6, lambda_min_ratio=0.05
)


def _size_excitation(horizon: float, seed: int) -> EventLog:
    return v2.TRUTHS["size_excitation"].simulate(horizon, np.random.default_rng(seed))


def _investigation(log: EventLog) -> InvestigationData:
    return InvestigationData(
        (Dataset.observational(log),), v2.CHANNELS, v2.mark_sampler
    )


@pytest.fixture(scope="module")
def small_dictionary() -> GroupDictionary:
    log = _size_excitation(300.0, 7)
    train, val = split_dataset(Dataset.observational(log), 0.75)
    return GroupDictionary.build((train,), (val,), v2.CHANNELS, alphabet=SMALL)


@pytest.fixture(scope="module")
def tiny_dictionary() -> GroupDictionary:
    log = _size_excitation(150.0, 4)
    train, val = split_dataset(Dataset.observational(log), 0.75)
    return GroupDictionary.build((train,), (val,), v2.CHANNELS, alphabet=TINY)


# --------------------------------------------------------------------------
# Split
# --------------------------------------------------------------------------


def test_split_dataset_is_a_time_split_with_full_history() -> None:
    log = _size_excitation(200.0, 3)
    data = Dataset.create(log, np.ones(log.n, dtype=np.bool_), ((10.0, 20.0),), "obs")
    train, val = split_dataset(data, 0.75)
    assert train.log is log and val.log is log
    assert train.excluded == ((10.0, 20.0), (150.0, 200.0))
    assert val.excluded == ((0.0, 150.0),)
    # A window that straddles the split is merged, not duplicated.
    straddle = Dataset.create(
        log, np.ones(log.n, dtype=np.bool_), ((140.0, 160.0),), "s"
    )
    tr, va = split_dataset(straddle, 0.75)
    assert tr.excluded == ((140.0, 200.0),)
    assert va.excluded == ((0.0, 160.0),)


# --------------------------------------------------------------------------
# Dictionary
# --------------------------------------------------------------------------


def test_dictionary_enumerates_every_depth2_group(
    small_dictionary: GroupDictionary,
) -> None:
    d = small_dictionary
    assert d.n_units == SMALL_UNITS
    assert d.n_indicators == SMALL_INDICATORS
    kinds = [d.group_kind(g) for g in d.group_ids()]
    u = SMALL_UNITS
    # Every unordered pair of units (squares included), every unit under every
    # gate condition at every ψ, every unit alone; none excluded on this data.
    assert kinds.count(GroupKind.PRODUCT) == u * (u + 1) // 2
    assert kinds.count(GroupKind.GATE) == u * SMALL_INDICATORS
    assert kinds.count(GroupKind.ATOM) == u
    assert d.n_duplicate_units == 0


def _sample_groups(d: GroupDictionary) -> list[int]:
    ids = d.group_ids()
    picks: list[int] = []
    for kind in GroupKind:
        of_kind = [g for g in ids if d.group_kind(g) is kind]
        picks.extend(of_kind[:: max(1, len(of_kind) // 12)])
    # Include two-column groups: products and gates of Periodic.
    picks.extend(g for g in ids if d.group_width(g) == 4)
    return sorted(set(picks))


def test_group_columns_equal_the_feature_blocks(
    small_dictionary: GroupDictionary,
) -> None:
    """A group's columns are its canonical feature's design columns, byte for byte."""
    d = small_dictionary
    (train,) = d.train_datasets
    nodes, weights = d.train_rules[0]
    cache = BlockCache(train, v2.CHANNELS, nodes, weights)
    for g in _sample_groups(d):
        feature, psi = d.feature(g)
        assert canonical_feature(feature) == feature
        block = cache.block(feature, psi)
        events, at_nodes = d.train_columns(g, scaled=False)
        assert events.shape == block.at_events.shape
        # Same columns, possibly in a different order (left/right of a product).
        for got, want in ((events, block.at_events), (at_nodes, block.at_nodes)):
            got_cols = sorted(c.tobytes() for c in got.T)
            want_cols = sorted(c.tobytes() for c in want.T)
            assert got_cols == want_cols, d.feature(g)


def _weights(
    d: GroupDictionary, intercept: float, beta: dict[int, np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """Softplus-link gradient weights at nodes and events, computed directly:
    ``∂/∂η [w·sp(η)] = w·expit(η)`` and ``∂/∂η log sp(η) = expit(η)/sp(η)``."""
    eta_n = np.full(d.node_weights.size, intercept)
    eta_e = np.full(d.n_events, intercept)
    for g, b in sorted(beta.items()):
        ev, nd = d.train_columns(g, scaled=True)
        eta_n = eta_n + nd @ b
        eta_e = eta_e + ev @ b
    node_d = d.node_weights * special.expit(eta_n)
    event_c = special.expit(eta_e) / np.logaddexp(0.0, eta_e)
    return node_d, event_c


def _direct_gradient(
    d: GroupDictionary, g: int, node_d: np.ndarray, event_c: np.ndarray
) -> np.ndarray:
    events, nodes = d.train_columns(g, scaled=True)
    out: np.ndarray = nodes.T @ node_d - events.T @ event_c
    return out


def test_gram_group_norms_match_direct_columns(
    small_dictionary: GroupDictionary,
) -> None:
    d = small_dictionary
    ids = d.group_ids()
    beta = {ids[3]: np.array([0.05]), ids[-1]: np.full(d.group_width(ids[-1]), -0.02)}
    intercept = math.log(math.expm1(d.n_events / d.observed_time))
    node_d, event_c = _weights(d, intercept, beta)
    gram = d.group_norms(node_d, event_c)
    direct = np.zeros(d.n_slots_total)
    for g in ids:
        grad = _direct_gradient(d, g, node_d, event_c)
        direct[g] = math.sqrt(float(grad @ grad))
    valid = d.valid
    np.testing.assert_allclose(gram[valid], direct[valid], rtol=1e-8, atol=1e-8)
    assert np.all(gram[~valid] == 0.0)


# --------------------------------------------------------------------------
# Path and KKT
# --------------------------------------------------------------------------


def _check_kkt(d: GroupDictionary, path: SparsePath) -> None:
    """At every λ, from materialised columns (not the cross product): inactive
    groups have ‖∇_g‖ ≤ λω_g, active ones ∇_g = -λω_g β_g/‖β_g‖, and the
    intercept's derivative is zero."""
    assert path.points[0].groups == ()
    assert any(p.groups for p in path.points)
    for point in path.points:
        beta = dict(zip(point.groups, (np.array(b) for b in point.beta), strict=True))
        node_d, event_c = _weights(d, point.intercept, beta)
        assert abs(float(node_d.sum() - event_c.sum())) <= 1e-6 * d.n_events
        lam = point.lam
        for g in d.group_ids():
            grad = _direct_gradient(d, g, node_d, event_c)
            bound = lam * d.omega[g]
            if g in beta:
                b = beta[g]
                resid = grad + bound * b / math.sqrt(float(b @ b))
                assert math.sqrt(float(resid @ resid)) <= 1e-5 * bound, (lam, g)
            else:
                assert math.sqrt(float(grad @ grad)) <= bound * (1 + 1e-5), (lam, g)


def test_kkt_holds_on_every_group_tiny(tiny_dictionary: GroupDictionary) -> None:
    path = lasso_path(tiny_dictionary, TINY_CONFIG)
    assert len(path.points) == TINY_CONFIG.n_lambdas
    _check_kkt(tiny_dictionary, path)


@pytest.mark.slow
def test_kkt_holds_on_every_group_small(small_dictionary: GroupDictionary) -> None:
    path = lasso_path(small_dictionary, SMALL_CONFIG)
    assert len(path.points) >= 5
    _check_kkt(small_dictionary, path)


def test_path_is_deterministic_tiny() -> None:
    log = _size_excitation(150.0, 4)
    train, val = split_dataset(Dataset.observational(log), 0.75)
    paths = [
        lasso_path(
            GroupDictionary.build((train,), (val,), v2.CHANNELS, alphabet=TINY),
            TINY_CONFIG,
        )
        for _ in range(2)
    ]
    assert paths[0] == paths[1]


def test_rank_features_caps_at_max_features_by_summed_norm() -> None:
    feats: dict[int, Feature] = {
        1: HAWKES_ATOM,
        2: HAWKES_ATOM,  # same feature at another ψ: its norms add
        3: SIZE_ATOM,
        4: Periodic(),
        5: Trend(),
        6: Product(HAWKES_ATOM, Periodic()),
    }
    norms = {1: 0.3, 2: 0.25, 3: 0.5, 4: 0.1, 5: 0.05, 6: 0.2}
    top, before = rank_features(
        sorted(norms), [norms[g] for g in sorted(norms)], feats.__getitem__
    )
    assert before == 5
    assert len(top) == MAX_FEATURES
    assert [f for f, _ in top] == [
        HAWKES_ATOM,
        SIZE_ATOM,
        Product(HAWKES_ATOM, Periodic()),
        Periodic(),
    ]
    # The representative ψ of a feature is its largest-norm group.
    assert top[0][1] == 1


@pytest.mark.slow
def test_small_run_is_deterministic() -> None:
    data = _investigation(_size_excitation(300.0, 11))
    first = BSparse(SMALL_CONFIG).run_with_report(data)
    second = BSparse(SMALL_CONFIG).run_with_report(data)
    assert first == second
    result, report = first
    assert result.system == B_SPARSE
    assert result.fits_used == 1
    assert report.wall_time_total > 0
    assert report.selected_index < len(report.path)


# --------------------------------------------------------------------------
# Slow: the full pointproc dictionary
# --------------------------------------------------------------------------

FULL: Final = SparseConfig()


def _report_line(report: object) -> str:
    return repr(report)[:4000]


@pytest.mark.slow
def test_full_dictionary_recovers_size_excitation() -> None:
    log = _size_excitation(2000.0, 1)
    assert 1500 <= log.n <= 3000
    result, report = BSparse(FULL).run_with_report(_investigation(log))
    assert report.n_groups > 900_000
    assert result.structure is not None
    assert SIZE_ATOM in result.structure.features, _report_line(report)


GATED: Final = Gate(HAWKES_ATOM, LastMarkAbove("size"))


def _gate_truth(seed: int) -> EventLog:
    """Excitation that is on only while the last event's size exceeds its mean."""
    psi = ({PsiSlot((0, 0), "exp_rate"): 1.0, PsiSlot((1,), "above_z"): 0.0},)
    return simulate(
        Structure((GATED,), Link.IDENTITY),
        psi,
        Coefficients(0.6, ((0.9,),)),
        v2.CHANNELS,
        v2.mark_sampler,
        3000.0,
        np.random.default_rng(seed),
    )


@pytest.mark.slow
def test_full_dictionary_recovers_a_depth2_gate() -> None:
    log = _gate_truth(1)
    assert 1500 <= log.n <= 5000
    result, report = BSparse(FULL).run_with_report(_investigation(log))
    assert result.structure is not None
    assert canonical_feature(GATED) in result.structure.features, _report_line(report)


@pytest.mark.slow
def test_full_dictionary_selects_null_on_poisson() -> None:
    log = v2.TRUTHS["null"].simulate(2000.0, np.random.default_rng(5))
    result, report = BSparse(FULL).run_with_report(_investigation(log))
    assert result.structure == Structure((), Link.IDENTITY), _report_line(report)
