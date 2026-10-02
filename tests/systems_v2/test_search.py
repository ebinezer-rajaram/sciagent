"""B-rand, B-sym and the planted-hint control (SPEC §4.1, §6.4, §9).

Fast tests run the searches against a *fake* fitter whose BIC is a known
function of the structure (its distance to a target), so the budget
accounting, deduplication, determinism and search quality are checked without
fitting anything. Slow tests use the certified fitter on pointproc's named
truths at the operating point (≈2,000 events).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from functools import cache

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from environments.pointproc import v2
from environments.pointproc.truths_v2 import STRUCTURE_PRIOR
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.distance import structure_distance
from sciagent.glm.fit import FitConfig, FitError, FitResult, fit
from sciagent.glm.grammar import (
    ALL,
    MAX_DEPTH,
    MAX_FEATURES,
    ChannelSpec,
    Excite,
    ExpOf,
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
    Source,
    SourceKind,
    Structure,
    depth,
    validate,
)
from sciagent.glm.simulate import Coefficients
from sciagent.glm.space import alphabet
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.search import (
    BRand,
    BSym,
    GPConfig,
    Mutation,
    PlantedHint,
    ScenarioPrior,
    SearchError,
    UniformShapePrior,
    crossover,
    hint,
    mutate,
)
from sciagent.systems.v2.systems import BLib, InvestigationData, Oracle, SystemResult

CHANNELS = v2.CHANNELS
ALPHABET = alphabet(CHANNELS)
SEEDS: tuple[Structure, ...] = tuple(m.structure for m in v2.LIBRARY)

#: The fake landscape's optimum: two features, one of them depth 2.
TARGET = canonicalise(
    Structure(
        (
            Excite(KernelKind.EXP, Mark("size"), ALL),
            Gate(Excite(KernelKind.EXP, One(), ALL), LastMarkAbove("size")),
        ),
        Link.IDENTITY,
    )
)


@cache
def _small_data() -> InvestigationData:
    log = v2.TRUTHS["hawkes"].simulate(60.0, np.random.default_rng(0))
    return InvestigationData((Dataset.observational(log),), CHANNELS, v2.mark_sampler)


@cache
def _template() -> FitResult:
    return fit(v2.NULL, _small_data().observational, CHANNELS)


def fake_fit(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    config: FitConfig | None,
) -> FitResult:
    """BIC = 1000 · distance to TARGET + number of features; no fitting.

    A ``GammaK`` anywhere makes the fit uncertified, and a ``PowerK`` with an
    ``ExpOf`` mark raises, so both failure paths are exercised.
    """
    text = repr(structure)
    if "POWER" in text and "ExpOf" in text:
        raise FitError("fake failure")
    bic = 1000.0 * structure_distance(structure, TARGET) + len(structure.features)
    return dataclasses.replace(
        _template(),
        structure=structure,
        link=structure.link,
        bic=bic,
        certified="GAMMA" not in text,
    )


def _hashes(result: SystemResult) -> list[str]:
    return [p.candidate for p in result.trajectory]


def _assert_budget(result: SystemResult, budget: int) -> None:
    assert result.fits_used <= budget
    names = [p.candidate for p in result.trajectory]
    assert len(names) == len(set(names)), "a structure was charged twice"
    assert [p.fits_used for p in result.trajectory] == list(
        range(result.fits_used - len(names) + 1, result.fits_used + 1)
    )
    bests = [p.best_criterion for p in result.trajectory]
    assert bests == sorted(bests, reverse=True)
    assert result.trajectory[-1].best_model is result.model


# --------------------------------------------------------------------------
# Priors
# --------------------------------------------------------------------------


@given(st.integers(0, 2**32 - 1))
def test_uniform_prior_samples_valid_canonical_structures(seed: int) -> None:
    prior = UniformShapePrior(CHANNELS)
    s = prior.sample(np.random.default_rng(seed))
    validate(s, CHANNELS)
    assert canonicalise(s) == s
    assert 1 <= len(s.features) <= prior.max_features


def test_priors_are_deterministic() -> None:
    for prior in (
        UniformShapePrior(CHANNELS),
        ScenarioPrior(STRUCTURE_PRIOR, CHANNELS, out_of_dictionary_share=0.3),
    ):
        a = [prior.sample(np.random.default_rng(7)) for _ in range(3)]
        b = [prior.sample(np.random.default_rng(7)) for _ in range(3)]
        assert a == b


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


@given(st.integers(0, 2**32 - 1), st.sampled_from(list(Mutation)))
def test_mutation_yields_valid_canonical_novel_structures(
    seed: int, op: Mutation
) -> None:
    rng = np.random.default_rng(seed)
    parent = canonicalise(
        Structure(
            (
                Excite(KernelKind.EXP, Mark("size"), ALL),
                Gate(Periodic(), PhaseWindow()),
            ),
            Link.EXP,
        )
    )
    child = mutate(parent, op, ALPHABET, rng, MAX_FEATURES)
    if child is None:
        return
    validate(child, CHANNELS)
    assert canonicalise(child) == child
    assert structure_hash(child) != structure_hash(parent)
    assert all(depth(f) <= MAX_DEPTH for f in child.features)


@given(st.integers(0, 2**32 - 1))
def test_crossover_draws_features_from_the_parents(seed: int) -> None:
    rng = np.random.default_rng(seed)
    a = canonicalise(Structure((Excite(KernelKind.EXP, One(), ALL), Periodic())))
    b = canonicalise(
        Structure((Excite(KernelKind.POWER, Mark("size"), ALL),), Link.SOFTPLUS)
    )
    child = crossover(a, b, rng, MAX_FEATURES)
    if child is None:
        return
    assert child.link in (a.link, b.link)
    pool = set(a.features) | set(b.features)
    assert set(child.features) <= pool
    assert 1 <= len(child.features) <= MAX_FEATURES


def test_hint_is_the_top_level_production_with_default_children() -> None:
    hawkes = Excite(KernelKind.EXP, One(), ALL)
    assert hint(v2.SIZE_EXCITED) == v2.HAWKES
    assert hint(v2.SEASONALITY) == v2.SEASONALITY
    gated = Structure(
        (
            Gate(
                Excite(
                    KernelKind.POWER, ExpOf("size"), Source(SourceKind.POSITIVE, "sign")
                ),
                LastMarkAbove("size"),
            ),
        ),
        Link.SOFTPLUS,
    )
    assert hint(gated) == Structure(
        (Gate(hawkes, LastMarkAbove("size")),), Link.SOFTPLUS
    )
    product = Structure(
        (Product(Excite(KernelKind.GAMMA, Mark("size"), ALL), Periodic()),), Link.EXP
    )
    assert hint(product) == canonicalise(
        Structure((Product(hawkes, Periodic()),), Link.EXP)
    )


# --------------------------------------------------------------------------
# Budget, deduplication, determinism (fake fitter)
# --------------------------------------------------------------------------


class _Cycle:
    """A 'prior' that cycles through three structures, two of them equivalent."""

    def __init__(self) -> None:
        self.items = (
            v2.HAWKES,
            v2.SIZE_EXCITED,
            Structure((Excite(KernelKind.EXP, One(), ALL),), Link.IDENTITY),
        )

    def sample(self, rng: np.random.Generator) -> Structure:
        return self.items[int(rng.integers(len(self.items)))]


def test_brand_dedupes_and_never_charges_a_duplicate() -> None:
    result = BRand(_Cycle(), budget=10, seed=1, fitter=fake_fit).run(_small_data())
    # Only two distinct canonical structures exist; the rest are free repeats.
    assert result.fits_used == 2
    assert len(result.trajectory) == 2
    _assert_budget(result, 10)


def test_brand_spends_the_budget_on_distinct_structures() -> None:
    result = BRand(UniformShapePrior(CHANNELS), budget=25, seed=3, fitter=fake_fit).run(
        _small_data()
    )
    assert result.fits_used == 25
    _assert_budget(result, 25)


@pytest.mark.parametrize("budget", [1, 5, 17, 40])
def test_bsym_never_exceeds_its_budget(budget: int) -> None:
    result = BSym(SEEDS, budget=budget, seed=2, fitter=fake_fit).run(_small_data())
    assert result.fits_used == budget
    _assert_budget(result, budget)


def test_bsym_counts_failed_and_uncertified_fits_but_never_selects_them() -> None:
    seeds = (
        Structure((Excite(KernelKind.GAMMA, One(), ALL),)),
        Structure((Excite(KernelKind.POWER, ExpOf("size"), ALL),)),
        v2.NULL,
    )
    result = BSym(seeds, budget=12, seed=0, fitter=fake_fit).run(_small_data())
    assert result.fits_used == 12
    assert result.trajectory[0].fits_used == 3  # no best until the null
    assert len(result.skipped) >= 2
    assert "GammaK" not in result.submitted
    _assert_budget(result, 12)


def test_searches_are_deterministic() -> None:
    data = _small_data()
    for make in (
        lambda s: BSym(SEEDS, budget=30, seed=s, fitter=fake_fit),
        lambda s: PlantedHint(TARGET, SEEDS, budget=30, seed=s, fitter=fake_fit),
        lambda s: BRand(
            UniformShapePrior(CHANNELS), budget=30, seed=s, fitter=fake_fit
        ),
    ):
        a, b, c = make(5).run(data), make(5).run(data), make(6).run(data)
        assert _hashes(a) == _hashes(b)
        assert [p.candidate_criterion for p in a.trajectory] == [
            p.candidate_criterion for p in b.trajectory
        ]
        assert a.submitted == b.submitted
        assert _hashes(a) != _hashes(c)


def test_memo_reuses_fits_without_changing_the_result_or_the_charge() -> None:
    calls: list[str] = []

    def counting(
        structure: Structure,
        datasets: Sequence[Dataset],
        channels: tuple[ChannelSpec, ...],
        config: FitConfig | None,
    ) -> FitResult:
        calls.append(structure_hash(structure))
        return fake_fit(structure, datasets, channels, config)

    memo: dict[str, FitResult] = {}
    data = _small_data()
    first = BSym(SEEDS, budget=15, seed=4, fitter=counting, memo=memo).run(data)
    n_calls = len(calls)
    second = BSym(SEEDS, budget=15, seed=4, fitter=counting, memo=memo).run(data)
    assert len(calls) == n_calls == 15
    assert second.fits_used == first.fits_used == 15
    assert _hashes(first) == _hashes(second)


def test_planted_hint_proposes_the_hint_first() -> None:
    result = PlantedHint(v2.SIZE_EXCITED, (v2.NULL,), budget=4, fitter=fake_fit).run(
        _small_data()
    )
    assert result.trajectory[0].candidate == "link=identity; Excite(ExpK, One, all)"
    assert result.system == "planted-hint"


def test_rejects_a_nonpositive_budget() -> None:
    with pytest.raises(SearchError):
        BSym(SEEDS, budget=0)
    with pytest.raises(SearchError):
        GPConfig(population=0)


def test_bsym_beats_brand_on_the_fake_landscape() -> None:
    """Search quality without fitting: on a landscape whose BIC is 1000 x the
    distance to TARGET, B-sym gets much closer than B-rand at F = 40.

    Measured (seeds 0-4): B-sym 82-198, B-rand 463-501.
    """
    data = _small_data()
    sym_best, rnd_best = [], []
    for seed in range(5):
        sym = BSym(SEEDS, budget=40, seed=seed, fitter=fake_fit).run(data)
        rnd = BRand(
            UniformShapePrior(CHANNELS), budget=40, seed=seed, fitter=fake_fit
        ).run(data)
        sym_best.append(sym.trajectory[-1].best_criterion)
        rnd_best.append(rnd.trajectory[-1].best_criterion)
    assert all(a < b for a, b in zip(sym_best, rnd_best, strict=True))
    assert sum(sym_best) < 0.5 * sum(rnd_best), (sym_best, rnd_best)


# --------------------------------------------------------------------------
# Slow: real fits at the operating point
# --------------------------------------------------------------------------

HORIZON = 2000.0


def _data(truth: str, seed: int) -> InvestigationData:
    log = v2.TRUTHS[truth].simulate(HORIZON, np.random.default_rng(seed))
    return InvestigationData((Dataset.observational(log),), CHANNELS, v2.mark_sampler)


def _held_out(truth: str, seed: int) -> list[Dataset]:
    rng = np.random.default_rng(seed)
    return [
        Dataset.observational(v2.TRUTHS[truth].simulate(HORIZON, rng), f"held{i}")
        for i in range(2)
    ]


@pytest.mark.slow
def test_parallel_fitting_is_identical_to_serial() -> None:
    log = v2.TRUTHS["size_excitation"].simulate(300.0, np.random.default_rng(3))
    data = InvestigationData((Dataset.observational(log),), CHANNELS, v2.mark_sampler)
    serial = BSym(SEEDS, budget=10, seed=1).run(data)
    parallel = BSym(SEEDS, budget=10, seed=1, workers=3).run(data)
    assert _hashes(serial) == _hashes(parallel)
    assert [p.candidate_criterion for p in serial.trajectory] == [
        p.candidate_criterion for p in parallel.trajectory
    ]


@pytest.mark.slow
def test_bsym_at_least_matches_blib_on_size_excitation() -> None:
    wins = []
    for seed in (101, 202, 303):
        data = _data("size_excitation", seed)
        held = _held_out("size_excitation", seed + 1)
        sym = BSym(SEEDS, budget=40, seed=seed, workers=4).run(data)
        lib = BLib(v2.B_LIB_LIBRARY).run(data)
        gap = sym.model.held_out_per_event(held) - lib.model.held_out_per_event(held)
        wins.append((gap >= 0.0, round(gap, 4), sym.submitted))
    assert sum(w for w, _, _ in wins) >= 2, wins


#: A truth where the hint is informative: B-lib's Hawkes is not its top-level
#: production. On-grid ψ; about 2,100 events at the operating point.
GATED = v2.Truth(
    "gated_size",
    Structure(
        (Gate(Excite(KernelKind.EXP, Mark("size"), ALL), LastMarkAbove("size")),),
        Link.IDENTITY,
    ),
    ({PsiSlot((0, 0), "exp_rate"): 1.0, PsiSlot((1,), "above_z"): 0.5},),
    Coefficients(0.9, ((1.0,),)),
)


def _first_reach(
    result: SystemResult,
    truth: Structure,
    held: list[Dataset],
    oracle: float,
    tol: float,
) -> int | None:
    """Fits used when the best-so-far is first the truth (canonical match) or
    within ``tol`` of the oracle held out per event; None if never."""
    target = structure_hash(truth)
    for point in result.trajectory:
        model = point.best_model
        assert isinstance(model, GLMModel)
        if structure_hash(model.fit.structure) == target:
            return point.fits_used
        if oracle - model.held_out_per_event(held) < tol:
            return point.fits_used
    return None


@pytest.mark.slow
def test_planted_hint_recovers_the_truth_before_brand() -> None:
    """A mini SPEC §6.4 control on GATED: the hinted search recovers the truth
    exactly, in fewer fits than random proposals from the truth prior.

    The criterion is exact canonical recovery. The looser "held-out gap <
    0.005" does not separate them: measured on seeds 11 and 12, B-rand got
    within 0.005 after 5 and 2 fits with wrong structures, a planted hint after
    1 and 11. At ~2,000 held-out events 0.005 nats/event is within noise.
    """
    seed = 11
    data = InvestigationData(
        (Dataset.observational(GATED.simulate(HORIZON, np.random.default_rng(seed))),),
        CHANNELS,
        v2.mark_sampler,
    )
    rng = np.random.default_rng(seed + 100)
    held = [
        Dataset.observational(GATED.simulate(HORIZON, rng), f"held{i}")
        for i in range(2)
    ]
    oracle = Oracle(GATED.structure).run(data).model.held_out_per_event(held)
    prior = ScenarioPrior(STRUCTURE_PRIOR, CHANNELS, out_of_dictionary_share=0.3)
    planted = PlantedHint(GATED.structure, SEEDS, budget=40, seed=seed, workers=4)
    rand = BRand(prior, budget=40, seed=seed, workers=4)
    p_run, r_run = planted.run(data), rand.run(data)
    p = _first_reach(p_run, GATED.structure, held, oracle, tol=-math.inf)
    r = _first_reach(r_run, GATED.structure, held, oracle, tol=-math.inf)
    assert p is not None, (p_run.submitted, r_run.submitted)
    assert r is None or p < r, (p, r)
