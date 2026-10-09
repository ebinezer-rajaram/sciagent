"""Δ11: the evidence policies (SPEC §2.4, §4.2) are instruments (SPEC §6.3).

EIG and FSD choose what the B-sym and AG-c-S policy arms observe, and C5 reads
their outcomes, so each number they compute is tested against an independent
value: EIG against closed forms, quadrature by scipy and the evidence-wall
bound (``docs/v2/evidence-wall.md`` Proposition 1); FSD against
``battery.hellinger2`` on the same replicates and against its sampling-noise
null; the random policy against uniformity. The static tests keep the truth
out of ``policies.py`` (as ``test_investigation_truth_isolation`` does for
the tool layer) and every float fold on ``sciagent.core.reductions``.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy import integrate, stats

from environments.pointproc import v2
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import FitConfig, FitResult, fit
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.interventions import Experiment
from sciagent.scoring.battery import hellinger2, standard_battery
from sciagent.scoring.seeds import POLICY, generator, policy_seed
from sciagent.systems.v2 import policies
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.policies import (
    REFERENCE_INDEX,
    DecisionContext,
    EIGPolicy,
    EntertainedSet,
    FSDPolicy,
    Hypothesis,
    Menu,
    PolicyError,
    Predictive,
    RandomPolicy,
    bic_weights,
    choose,
    decide,
    design_menu,
    diagnostic_menu,
    eig,
    eig_table,
    fit_entertained,
    fsd_null,
    fsd_table,
    predictive,
    simulate_values,
)

ROOT = Path(__file__).resolve().parents[2]
SYSTEMS = ROOT / "src" / "sciagent" / "systems" / "v2"
SCORING = ROOT / "src" / "sciagent" / "scoring"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _gauss(mean: float, var: float, undefined: float = 0.0) -> Predictive:
    return Predictive(undefined, mean, var)


def _entropy(w: Sequence[float]) -> float:
    return -math.fsum(x * math.log(x) for x in w if x > 0.0)


def _log_npdf(y: float, mean: float, var: float) -> float:
    return -0.5 * math.log(2.0 * math.pi * var) - (y - mean) ** 2 / (2.0 * var)


def _eig_by_quad(w: Sequence[float], ps: Sequence[Predictive]) -> float:
    """An independent EIG: scipy adaptive quadrature of Σ w_k KL(p_k ‖ p_w)."""
    atom = math.fsum(wk * p.undefined for wk, p in zip(w, ps, strict=True))
    total = 0.0
    for wk, p in zip(w, ps, strict=True):
        kl = 0.0
        if p.undefined > 0.0:
            kl += p.undefined * math.log(p.undefined / atom)
        if p.undefined < 1.0:

            def integrand(y: float, p: Predictive = p) -> float:
                own = math.log(1.0 - p.undefined) + _log_npdf(y, p.mean, p.var)
                mix = math.fsum(
                    wj * (1.0 - q.undefined) * math.exp(_log_npdf(y, q.mean, q.var))
                    for wj, q in zip(w, ps, strict=True)
                    if q.undefined < 1.0
                )
                return math.exp(own) * (own - math.log(mix))

            lo = min(q.mean - 12.0 * math.sqrt(q.var) for q in ps)
            hi = max(q.mean + 12.0 * math.sqrt(q.var) for q in ps)
            value, _ = integrate.quad(integrand, lo, hi, limit=400, epsabs=1e-12)
            kl += value
        total += wk * kl
    return total


@dataclass(frozen=True)
class PoissonSim:
    """A fake simulator: homogeneous Poisson at ``rate``, marks from pointproc.

    It ignores the intervention, which the policy code never inspects; it only
    reads the returned data.
    """

    rate: float
    name: str = "poisson"

    def simulate_experiment(
        self, experiment: Experiment, rng: np.random.Generator, *, label: str = "x"
    ) -> Dataset:
        n = int(rng.poisson(self.rate * experiment.horizon))
        times = np.sort(rng.uniform(0.0, experiment.horizon, n))
        rows = [v2.mark_sampler(rng) for _ in range(n)]
        marks = {
            c.name: np.array([r[c.name] for r in rows], dtype=np.float64)
            for c in v2.CHANNELS
        }
        log = EventLog.create(times, marks, experiment.horizon)
        return Dataset.observational(log, label)


def _small_menu(log: EventLog, n_designs: int = 3, horizon: float = 60.0) -> Menu:
    designs = design_menu(v2.CHANNELS, horizon=horizon)[:n_designs]
    diagnostics = diagnostic_menu(log, v2.CHANNELS)
    return Menu(designs, diagnostics)


def _obs_log(rate: float = 1.0, horizon: float = 200.0, seed: int = 0) -> EventLog:
    sim = PoissonSim(rate)
    exp = design_menu(v2.CHANNELS, horizon=horizon)[0].experiment
    return sim.simulate_experiment(exp, np.random.default_rng(seed)).log


def _context(
    hypotheses: Sequence[tuple[str, float, float]],
    *,
    n_designs: int = 2,
    horizon: float = 60.0,
) -> DecisionContext:
    """``(name, rate, bic)`` hypotheses as Poisson fakes."""
    log = _obs_log()
    entertained = EntertainedSet.of(
        tuple(
            Hypothesis(name, PoissonSim(rate, name), bic)
            for name, rate, bic in hypotheses
        )
    )
    return DecisionContext(
        channels=v2.CHANNELS,
        observational=Dataset.observational(log),
        entertained=entertained,
        menu=_small_menu(log, n_designs, horizon),
    )


# --------------------------------------------------------------------------
# Menus
# --------------------------------------------------------------------------


def test_design_menu_is_the_battery_at_horizon_2000() -> None:
    menu = design_menu(v2.CHANNELS)
    battery = standard_battery(v2.CHANNELS, horizon=2000.0)
    assert [d.name for d in menu] == [b.name for b in battery]
    assert [d.experiment for d in menu] == [b.experiment for b in battery]
    assert len(menu) == 8
    assert all(d.experiment.horizon == 2000.0 for d in menu)
    assert policies.POLICY_HORIZON == 2000.0


def test_diagnostic_menu_is_the_whole_catalogue_in_a_fixed_order() -> None:
    from sciagent.diagnostics import catalogue

    log = _obs_log()
    a = diagnostic_menu(log, v2.CHANNELS)
    b = diagnostic_menu(log, v2.CHANNELS)
    assert a == b
    assert [d.label for d in a] == [d.label for d in b]
    # Every catalogue entry appears, in catalogue order (first appearance).
    names = list(dict.fromkeys(d.name for d in a))
    assert names == list(catalogue.names())
    # Channel arguments expand in channel order; cross pairs once, i < j.
    gaps = [d for d in a if d.name == "mark_gap_correlation"]
    assert [dict(d.args)["channel"] for d in gaps] == ["sign", "size"]
    (cross,) = [d for d in a if d.name == "mark_cross_correlation"]
    assert (dict(cross.args)["channel"], dict(cross.args)["other"]) == ("sign", "size")
    (dispersion,) = [d for d in a if d.name == "mark_dispersion"]
    assert dict(dispersion.args)["channel"] == "size"
    # Every argument is resolved (frozen) on the observational log.
    for d in a:
        spec = catalogue.get(d.name)
        assert [k for k, _ in d.args] == sorted(a.name for a in spec.args)
    assert len(a) == 35


def test_diagnostic_args_are_frozen_absolute_values() -> None:
    log = _obs_log(rate=1.0)
    slow = _obs_log(rate=0.5, seed=1)
    fano = next(d for d in diagnostic_menu(log, v2.CHANNELS) if d.name == "fano_factor")
    fano_slow = next(
        d for d in diagnostic_menu(slow, v2.CHANNELS) if d.name == "fano_factor"
    )
    # Time defaults are in units of the observational mean gap, then frozen.
    assert dict(fano.args)["window"] != dict(fano_slow.args)["window"]
    period = next(
        d
        for d in diagnostic_menu(log, v2.CHANNELS)
        if d.name == "spectral_power_at_period"
    )
    p = dict(period.args)["period"]
    assert isinstance(p, float) and 0.0 < p <= 0.5 * log.horizon


def test_choose_is_argmax_with_ties_to_menu_order() -> None:
    assert choose(((0.1, 0.5), (0.5, 0.2))) == (0, 1)
    assert choose(((0.0, 0.0), (0.0, 0.0))) == (0, 0)
    assert choose(((0.1, 0.2), (0.3, 0.3))) == (1, 0)
    with pytest.raises(PolicyError):
        choose(())
    with pytest.raises(PolicyError):
        choose(((0.1, math.nan),))


# --------------------------------------------------------------------------
# Entertained set
# --------------------------------------------------------------------------


def test_bic_weights_are_exp_minus_half_bic_normalised() -> None:
    w = bic_weights((10.0, 12.0, 20.0))
    raw = [math.exp(-b / 2.0) for b in (10.0, 12.0, 20.0)]
    expected = [r / math.fsum(raw) for r in raw]
    assert w == pytest.approx(expected, rel=1e-15)
    assert math.fsum(w) == pytest.approx(1.0, abs=1e-15)
    # Stable at realistic magnitudes (exp(-1e4) underflows unshifted).
    big = bic_weights((10_000.0, 10_002.0))
    assert big == pytest.approx(
        (1.0 / (1.0 + math.exp(-1.0)), math.exp(-1.0) / (1.0 + math.exp(-1.0)))
    )
    with pytest.raises(PolicyError):
        bic_weights(())
    with pytest.raises(PolicyError):
        bic_weights((1.0, math.inf))


def test_fit_entertained_refits_on_all_data_and_drops_uncertified() -> None:
    log = v2.TRUTHS["hawkes"].simulate(150.0, np.random.default_rng(3))
    data = (Dataset.observational(log),)
    seen: list[tuple[Structure, int]] = []

    def fitter(
        structure: Structure,
        datasets: Sequence[Dataset],
        channels: tuple[ChannelSpec, ...],
        config: FitConfig | None,
    ) -> FitResult:
        seen.append((structure, len(datasets)))
        result = fit(structure, datasets, channels, config=config)
        if structure == v2.NULL:
            import dataclasses

            return dataclasses.replace(result, certified=False)
        return result

    entertained = fit_entertained(
        (("null", v2.NULL), ("hawkes", v2.HAWKES), ("s11", v2.SIZE_EXCITED)),
        data,
        v2.CHANNELS,
        v2.mark_sampler,
        fitter=fitter,
    )
    assert [s for s, _ in seen] == [v2.NULL, v2.HAWKES, v2.SIZE_EXCITED]
    assert all(n == 1 for _, n in seen)
    assert [h.name for h in entertained.hypotheses] == ["hawkes", "s11"]
    assert all(isinstance(h.model, GLMModel) for h in entertained.hypotheses)
    assert entertained.weights == bic_weights(
        tuple(h.bic for h in entertained.hypotheses)
    )
    assert entertained.skipped == ("null",)
    with pytest.raises(PolicyError):
        fit_entertained((), data, v2.CHANNELS, v2.mark_sampler)
    with pytest.raises(PolicyError):
        fit_entertained(
            tuple((f"h{i}", v2.HAWKES) for i in range(5)),
            data,
            v2.CHANNELS,
            v2.mark_sampler,
        )


def test_top_weight_is_first_on_ties() -> None:
    e = EntertainedSet.of(
        (
            Hypothesis("a", PoissonSim(1.0), 5.0),
            Hypothesis("b", PoissonSim(1.0), 5.0),
        )
    )
    assert e.top == 0


# --------------------------------------------------------------------------
# EIG on predictives (closed forms, quadrature, the wall)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("eps", [0.0, 0.25, 0.5, 1.0])
@pytest.mark.parametrize("w1", [0.5, 0.8])
def test_eig_matches_the_erasure_closed_form(eps: float, w1: float) -> None:
    """Two Gaussians, far apart, each seen with probability ε: EIG = ε·H(w)."""
    w = (w1, 1.0 - w1)
    ps = (_gauss(-50.0, 1.0, 1.0 - eps), _gauss(50.0, 1.0, 1.0 - eps))
    assert eig(w, ps) == pytest.approx(eps * _entropy(w), abs=1e-12)


@pytest.mark.parametrize("delta", [1e-3, 1e-2, 0.05])
def test_eig_matches_the_small_shift_closed_form(delta: float) -> None:
    """Equal-variance Gaussians δσ apart: EIG = w1·w2·δ²/2 + O(δ⁴)."""
    w = (0.3, 0.7)
    ps = (_gauss(0.0, 4.0), _gauss(2.0 * delta, 4.0))
    expected = w[0] * w[1] * delta**2 / 2.0
    assert eig(w, ps) == pytest.approx(expected, rel=5.0 * delta**2 + 1e-6)


@pytest.mark.parametrize(
    ("w", "ps"),
    [
        ((0.5, 0.5), (_gauss(0.0, 1.0), _gauss(1.0, 1.0))),
        ((0.2, 0.8), (_gauss(0.0, 1.0), _gauss(0.5, 4.0))),
        ((0.6, 0.4), (_gauss(-1.0, 0.25, 0.3), _gauss(1.0, 2.0, 0.1))),
        ((0.5, 0.3, 0.2), (_gauss(0.0, 1.0), _gauss(2.0, 1.0), _gauss(1.0, 9.0, 0.5))),
    ],
)
def test_eig_matches_adaptive_quadrature(
    w: tuple[float, ...], ps: tuple[Predictive, ...]
) -> None:
    assert eig(w, ps) == pytest.approx(_eig_by_quad(w, ps), abs=1e-6)


def test_eig_is_zero_for_identical_predictives() -> None:
    for p in (_gauss(0.3, 2.0), _gauss(-1.0, 1e-20, 0.4), _gauss(0.0, 1.0, 1.0)):
        assert eig((0.25, 0.75), (p, p)) == 0.0
        assert eig((0.2, 0.3, 0.5), (p, p, p)) == 0.0


def test_eig_of_one_hypothesis_is_zero() -> None:
    assert eig((1.0,), (_gauss(0.0, 1.0),)) == 0.0


def _tv_equal_var(a: float, b: float, var: float) -> float:
    return float(2.0 * stats.norm.cdf(abs(a - b) / (2.0 * math.sqrt(var))) - 1.0)


@given(
    st.lists(st.floats(-3.0, 3.0), min_size=2, max_size=4),
    st.lists(st.floats(0.05, 1.0), min_size=4, max_size=4),
    st.floats(0.1, 3.0),
)
def test_eig_is_below_the_evidence_wall_gaussian(
    means: list[float], raw_w: list[float], var: float
) -> None:
    """Proposition 1(b): EIG ≤ ε·H(w), ε the largest pairwise TV."""
    k = len(means)
    w = tuple(x / math.fsum(raw_w[:k]) for x in raw_w[:k])
    ps = tuple(_gauss(m, var) for m in means)
    eps = max(_tv_equal_var(a, b, var) for a, b in itertools.combinations(means, 2))
    assert eig(w, ps) <= eps * _entropy(w) + 1e-9


@given(
    st.lists(st.floats(0.0, 1.0), min_size=2, max_size=4),
    st.lists(st.floats(0.05, 1.0), min_size=4, max_size=4),
)
def test_eig_is_below_the_evidence_wall_atoms(
    undefined: list[float], raw_w: list[float]
) -> None:
    """Same Gaussian part, different undefined shares: TV = |u_k - u_l|."""
    k = len(undefined)
    w = tuple(x / math.fsum(raw_w[:k]) for x in raw_w[:k])
    ps = tuple(_gauss(0.0, 1.0, u) for u in undefined)
    eps = max(abs(a - b) for a, b in itertools.combinations(undefined, 2))
    assert eig(w, ps) <= eps * _entropy(w) + 1e-9


def test_eig_rejects_bad_weights() -> None:
    with pytest.raises(PolicyError):
        eig((0.5, 0.6), (_gauss(0.0, 1.0), _gauss(1.0, 1.0)))
    with pytest.raises(PolicyError):
        eig((0.5,), (_gauss(0.0, 1.0), _gauss(1.0, 1.0)))


# --------------------------------------------------------------------------
# Predictive moments
# --------------------------------------------------------------------------


def test_predictive_is_the_atom_and_the_sample_moments() -> None:
    p = predictive((1.0, None, 3.0, None))
    assert p == Predictive(0.5, 2.0, 2.0)
    one = predictive((4.0,))
    assert one.undefined == 0.0 and one.mean == 4.0 and one.var > 0.0
    assert predictive((None, None)).undefined == 1.0
    with pytest.raises(PolicyError):
        predictive(())


# --------------------------------------------------------------------------
# Simulation sets and the EIG table
# --------------------------------------------------------------------------


def test_simulate_values_uses_the_policy_stream_once_per_replicate() -> None:
    log = _obs_log()
    menu = _small_menu(log, 1)
    sim = PoissonSim(1.0)
    values = simulate_values(
        sim, menu.designs[0], menu.diagnostics, v2.CHANNELS, 5, 2, 1, 3
    )
    assert len(values) == len(menu.diagnostics)
    assert all(len(v) == 3 for v in values)
    # Replicate r is exactly the simulator on policy_seed(seed, decision, k, r).
    from sciagent.diagnostics import catalogue

    for r in range(3):
        data = sim.simulate_experiment(
            menu.designs[0].experiment, generator(policy_seed(5, 2, 1, r))
        )
        d0 = menu.diagnostics[0]
        assert values[0][r] == catalogue.compute(
            d0.name, data.log, v2.CHANNELS, dict(d0.args), check_bounds=False
        )


def test_eig_table_is_near_zero_for_identical_hypotheses_and_large_for_distinct() -> (
    None
):
    same = eig_table(_context((("a", 1.0, 0.0), ("b", 1.0, 0.0))), 1, 0, replicates=16)
    apart = eig_table(_context((("a", 1.0, 0.0), ("b", 4.0, 0.0))), 1, 0, replicates=16)
    assert len(same) == 2 and all(len(row) == len(same[0]) for row in same)
    labels = [d.label for d in _context((("a", 1.0, 0.0),)).menu.diagnostics]
    rate = labels.index("mean_rate")
    # Rate 1 vs 4 on 60 time units: mean_rate separates them completely.
    assert apart[0][rate] == pytest.approx(math.log(2.0), abs=1e-6)
    # Identical hypotheses: only estimator noise (independent streams per k).
    assert same[0][rate] < 0.2
    assert all(0.0 <= x <= math.log(2.0) + 1e-9 for row in apart for x in row)


def test_eig_table_is_bounded_by_the_entropy_of_the_weights() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 2.0, 1.0), ("c", 3.0, 3.0)))
    table = eig_table(ctx, 3, 0, replicates=8)
    h = _entropy(ctx.entertained.weights)
    assert max(x for row in table for x in row) <= h + 1e-9


# --------------------------------------------------------------------------
# FSD
# --------------------------------------------------------------------------


def test_fsd_equals_hellinger2_on_the_replicates() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 2.0, 5.0)))
    reference = PoissonSim(1.5, "reference")
    table = fsd_table(ctx, 4, 2, replicates=6, reference=reference)
    top = ctx.entertained.hypotheses[ctx.entertained.top].model
    for e, design in enumerate(ctx.menu.designs):
        par = simulate_values(
            top, design, ctx.menu.diagnostics, v2.CHANNELS, 4, 2, ctx.entertained.top, 6
        )
        ref = simulate_values(
            reference,
            design,
            ctx.menu.diagnostics,
            v2.CHANNELS,
            4,
            2,
            REFERENCE_INDEX,
            6,
        )
        for d in range(len(ctx.menu.diagnostics)):
            assert table[e][d] == hellinger2(par[d], ref[d])


def test_fsd_null_on_gaussian_replicates_is_about_one_over_2r() -> None:
    """He² of two R-samples of one Gaussian: E ≈ 1/(4R) (means) + 1/(4R) (scales).

    The plan's "≈ 1/(4R)" counts only the mean term; the scale term is as large.
    """
    rng = np.random.default_rng(0)
    for r in (16, 32):
        values = [
            hellinger2(list(rng.normal(size=r)), list(rng.normal(size=r)))
            for _ in range(3000)
        ]
        mean = math.fsum(values) / len(values)
        assert mean == pytest.approx(1.0 / (2.0 * r), rel=0.15)


def test_fsd_of_a_model_against_itself_is_sampling_noise() -> None:
    """Model = reference: every cell is He² of two independent R-samples."""
    r = 16
    ctx = _context((("a", 1.0, 0.0),), n_designs=2)
    table = fsd_table(ctx, 9, 0, replicates=r, reference=PoissonSim(1.0, "same"))
    cells = [x for row in table for x in row]
    mean = math.fsum(cells) / len(cells)
    assert 1.0 / (4.0 * r) <= mean <= 3.0 / r


@pytest.mark.slow
def test_fsd_null_bootstraps_the_top_fit_against_wiener_hopf() -> None:
    log = v2.TRUTHS["hawkes"].simulate(400.0, np.random.default_rng(11))
    data = (Dataset.observational(log),)
    entertained = fit_entertained(
        (("hawkes", v2.HAWKES),), data, v2.CHANNELS, v2.mark_sampler
    )
    menu = Menu(
        design_menu(v2.CHANNELS, horizon=120.0)[:2],
        diagnostic_menu(log, v2.CHANNELS)[:6],
    )
    ctx = DecisionContext(v2.CHANNELS, data[0], entertained, menu)
    a = fsd_null(ctx, 2, 0, replicates=4, bootstraps=2)
    b = fsd_null(ctx, 2, 0, replicates=4, bootstraps=2)
    assert a == b
    assert len(a.tables) == 2
    assert all(len(t) == 2 and len(t[0]) == 6 for t in a.tables)
    assert all(0.0 <= x <= 1.0 for t in a.tables for row in t for x in row)
    for e in range(2):
        for d in range(6):
            cell = math.fsum(t[e][d] for t in a.tables) / 2.0
            assert a.mean[e][d] == cell


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------


def test_random_policy_is_uniform_and_seeded() -> None:
    ctx = _context((("a", 1.0, 0.0),), n_designs=3)
    n_cells = len(ctx.menu.designs) * len(ctx.menu.diagnostics)
    policy = RandomPolicy(seed=17)
    picks = [policy.choose(ctx, t) for t in range(40 * n_cells)]
    again = [RandomPolicy(seed=17).choose(ctx, t) for t in range(40 * n_cells)]
    other = [RandomPolicy(seed=18).choose(ctx, t) for t in range(40 * n_cells)]
    key = [(c.design_index, c.diagnostic_index) for c in picks]
    assert key == [(c.design_index, c.diagnostic_index) for c in again]
    assert key != [(c.design_index, c.diagnostic_index) for c in other]
    counts = Counter(key)
    observed = [
        counts.get(cell, 0)
        for cell in itertools.product(
            range(len(ctx.menu.designs)), range(len(ctx.menu.diagnostics))
        )
    ]
    _, p = stats.chisquare(observed)
    assert p > 1e-3
    # Draws are with replacement: repeats occur.
    assert len(counts) < len(key)
    assert all(c.scores is None for c in picks)
    # The draw is policy_seed(seed, decision, RANDOM_INDEX, 0): one integer.
    rng = generator(policy_seed(17, 5, policies.RANDOM_INDEX, 0))
    flat = int(rng.integers(n_cells))
    assert (picks[5].design_index, picks[5].diagnostic_index) == divmod(
        flat, len(ctx.menu.diagnostics)
    )


def test_eig_policy_choice_is_the_argmax_of_its_table() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 0.0)))
    choice = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    table = eig_table(ctx, 1, 0, replicates=8)
    assert choice.scores == table
    assert (choice.design_index, choice.diagnostic_index) == choose(table)
    assert choice.design == ctx.menu.designs[choice.design_index]
    assert choice.diagnostic == ctx.menu.diagnostics[choice.diagnostic_index]
    assert choice.policy == "EIG"


def test_policies_are_byte_identical_across_runs() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 1.5, 1.0)))
    for make in (
        lambda: EIGPolicy(seed=3, replicates=6),
        lambda: FSDPolicy(seed=3, replicates=6, reference=PoissonSim(1.2, "ref")),
        lambda: RandomPolicy(seed=3),
    ):
        a, b = make().choose(ctx, 4), make().choose(ctx, 4)
        assert a == b
        assert repr(a) == repr(b)
        if a.scores is not None and b.scores is not None:
            assert np.array(a.scores).tobytes() == np.array(b.scores).tobytes()


@pytest.mark.slow
def test_fsd_policy_defaults_to_wiener_hopf_on_the_observational_log() -> None:
    log = v2.TRUTHS["size_excitation"].simulate(400.0, np.random.default_rng(5))
    data = (Dataset.observational(log),)
    entertained = fit_entertained(
        (("hawkes", v2.HAWKES),), data, v2.CHANNELS, v2.mark_sampler
    )
    menu = Menu(
        design_menu(v2.CHANNELS, horizon=120.0)[:2],
        diagnostic_menu(log, v2.CHANNELS)[:4],
    )
    ctx = DecisionContext(v2.CHANNELS, data[0], entertained, menu)
    a = FSDPolicy(seed=2, replicates=4).choose(ctx, 0)
    b = FSDPolicy(seed=2, replicates=4).choose(ctx, 0)
    assert a == b and a.policy == "FSD"
    from sciagent.systems.v2.models import WienerHopfModel

    reference = WienerHopfModel.estimate(log, v2.CHANNELS)
    assert a.scores == fsd_table(ctx, 2, 0, replicates=4, reference=reference)


class _Runner:
    def __init__(self, result: Dataset | None) -> None:
        self.result = result
        self.calls: list[tuple[Experiment, str]] = []

    def run(self, experiment: Experiment, label: str) -> Dataset | None:
        self.calls.append((experiment, label))
        return self.result


def test_decide_runs_the_chosen_design_through_the_injected_runner() -> None:
    ctx = _context((("a", 1.0, 0.0),), n_designs=3)
    data = PoissonSim(1.0).simulate_experiment(
        ctx.menu.designs[0].experiment, np.random.default_rng(0)
    )
    runner = _Runner(data)
    choice, got = decide(RandomPolicy(seed=1), ctx, 2, runner)
    assert got is data
    assert runner.calls == [(choice.design.experiment, choice.design.name)]
    stopped = _Runner(None)
    _, none = decide(RandomPolicy(seed=1), ctx, 2, stopped)
    assert none is None and len(stopped.calls) == 1


# --------------------------------------------------------------------------
# Static guards
# --------------------------------------------------------------------------


FORBIDDEN_NAMES = {
    "truth",
    "_truth",
    "resolve",
    "coef",
    "TruthSpec",
    "Truth",
    "TruthModel",
    "simulate_truth",
    "World",
}
FORBIDDEN_MODULES = ("environments", "sciagent.scoring.truth", "sciagent.investigation")


def _names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name.rsplit(".", 1)[-1])
    return found


def _imported_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_policies_module_never_names_the_truth() -> None:
    tree = ast.parse((SYSTEMS / "policies.py").read_text(encoding="utf-8"))
    used = _names(tree)
    assert not used & FORBIDDEN_NAMES, sorted(used & FORBIDDEN_NAMES)
    modules = _imported_modules(tree)
    bad = sorted(m for m in modules if m.startswith(FORBIDDEN_MODULES))
    assert not bad, bad


def test_the_truth_guard_would_catch_a_truth() -> None:
    """Positive control for the static guard above."""
    tree = ast.parse("from sciagent.scoring.truth import TruthModel\nx = truth.coef\n")
    assert {"TruthModel", "truth", "coef"} <= _names(tree)
    assert "sciagent.scoring.truth" in _imported_modules(tree)


def _invariants() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sciagent_test_invariants", ROOT / "tests" / "test_invariants.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    [
        SYSTEMS / "policies.py",
        SYSTEMS / "policy_controls.py",
        SYSTEMS / "policy_search.py",
        SCORING / "telling.py",
    ],
    ids=lambda p: p.name,
)
def test_every_fold_uses_core_reductions(path: Path) -> None:
    inv = _invariants()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert inv._order_dependent_reductions(tree) == []
    assert all(
        node.attr in inv.SEEDED_CONSTRUCTORS for node in inv._numpy_random_uses(tree)
    )
    assert inv._numpy_random_imports(tree) == []


def test_policy_tag_is_the_policy_stream() -> None:
    assert policy_seed(1, 2, 3, 4).spawn_key[0] == POLICY


# --------------------------------------------------------------------------
# Control 5: the truth-aware FSD (policy_controls.py)
# --------------------------------------------------------------------------


def test_truth_fsd_is_fsd_with_the_truth_as_reference() -> None:
    from sciagent.systems.v2.policy_controls import TruthFSDPolicy

    ctx = _context((("a", 1.0, 0.0), ("b", 2.0, 3.0)))
    the_truth = PoissonSim(3.0, "planted")
    choice = TruthFSDPolicy(the_truth, seed=6, replicates=6).choose(ctx, 1)
    table = fsd_table(ctx, 6, 1, replicates=6, reference=the_truth)
    assert choice.scores == table
    assert (choice.design_index, choice.diagnostic_index) == choose(table)
    assert choice.policy == "truthFSD"
    # With a comparator (B-lib's pick) it replaces the entertained top fit.
    member = PoissonSim(1.0, "member")
    with_member = TruthFSDPolicy(the_truth, seed=6, comparator=member, replicates=6)
    assert with_member.choose(ctx, 1).scores == fsd_table(
        ctx, 6, 1, replicates=6, reference=the_truth, parametric=member
    )


def test_only_the_control_module_takes_a_truth() -> None:
    """policy_controls is imported by neither policies.py nor the tool layer."""
    for path in (
        SYSTEMS / "policies.py",
        ROOT / "src" / "sciagent" / "investigation" / "tools.py",
        ROOT / "src" / "sciagent" / "investigation" / "view.py",
        ROOT / "src" / "sciagent" / "investigation" / "prompts.py",
    ):
        modules = _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        assert not any("policy_controls" in m for m in modules), path
