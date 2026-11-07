"""WP5b and Δ11 item 3: EIG accuracy, the numerical tie floor, ties, weights.

The orchestrator's decision (2026-10-06): BIC posterior weights stay the
default; a concentrated posterior giving EIG ≈ 0 is the evidence wall itself
(``docs/v2/evidence-wall.md`` Proposition 1), but the pick must then not be
decided by numerical noise. So EIG values within :data:`EIG_TIE_TOL` of the
maximum are exact ties; the tolerance is derived from the quadrature's
measured error, which these tests pin against an independent dense-grid
reference, including extreme variance ratios.

Ties (LOG 2026-10-27 Δ11 item 3, adopted 2026-10-28, replacing the
2026-10-06 menu-order rule): a fully tied decision (every cell within the
floor of the maximum, which every degenerate decision is) takes the random
arm's draw for that decision, ``policy_seed(seed, decision, RANDOM_INDEX, 0)``,
so on it the EIG arm equals random; a strict-subset tie draws uniformly over
the tied cells, in menu order, from its own index :data:`TIE_INDEX`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from environments.pointproc import v2
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import Experiment
from sciagent.scoring.seeds import generator, policy_seed
from sciagent.systems.v2.policies import (
    BIC_WEIGHTS,
    EIG_TIE_TOL,
    ENTERTAINED_MAX,
    NULL_INDEX,
    PARAMETRIC_INDEX,
    RANDOM_INDEX,
    REFERENCE_INDEX,
    TIE_INDEX,
    DecisionContext,
    EIGPolicy,
    EntertainedSet,
    FSDPolicy,
    Hypothesis,
    Menu,
    PolicyError,
    Predictive,
    RandomPolicy,
    Weighting,
    bic_weights,
    choose,
    design_menu,
    diagnostic_menu,
    eig,
    eig_choice,
    eig_table,
    random_draw,
    tempered,
    tied_cells,
)

# --------------------------------------------------------------------------
# Helpers (Poisson fakes, as in test_policies.py)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PoissonSim:
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
        return Dataset.observational(
            EventLog.create(times, marks, experiment.horizon), label
        )


def _context(hypotheses: Sequence[tuple[str, float, float]]) -> DecisionContext:
    exp = design_menu(v2.CHANNELS, horizon=200.0)[0].experiment
    log = PoissonSim(1.0).simulate_experiment(exp, np.random.default_rng(0)).log
    entertained = EntertainedSet.of(
        tuple(Hypothesis(n, PoissonSim(rate, n), bic) for n, rate, bic in hypotheses)
    )
    menu = Menu(
        design_menu(v2.CHANNELS, horizon=60.0)[:2], diagnostic_menu(log, v2.CHANNELS)
    )
    return DecisionContext(v2.CHANNELS, Dataset.observational(log), entertained, menu)


def _dense_eig(w: Sequence[float], ps: Sequence[Predictive]) -> float:
    """An independent EIG: trapezoid on 2M points per component, no atoms."""
    total = 0.0
    for k, p in enumerate(ps):
        s = math.sqrt(p.var)
        y = np.linspace(p.mean - 15.0 * s, p.mean + 15.0 * s, 2_000_001)
        logs = np.stack(
            [
                math.log(wj)
                - 0.5 * math.log(2.0 * math.pi * q.var)
                - (y - q.mean) ** 2 / (2.0 * q.var)
                for wj, q in zip(w, ps, strict=True)
            ]
        )
        top = logs.max(axis=0)
        log_mix = top + np.log(np.exp(logs - top).sum(axis=0))
        own = logs[k] - math.log(w[k])
        total += w[k] * float(np.trapezoid(np.exp(own) * (own - log_mix), y))
    return total


# --------------------------------------------------------------------------
# Quadrature accuracy (the basis of the tie floor)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ratio", [1.0, 100.0, 1e4, 1e-4])
@pytest.mark.parametrize("shift", [0.0, 0.3, 3.0, 10.0])
def test_eig_matches_a_dense_reference_at_extreme_variance_ratios(
    ratio: float, shift: float
) -> None:
    """Gauss-Hermite on the wide component steps over a narrow one's spike.

    Measured before the fix (256 Gauss-Hermite nodes): ratio 1e4, shift 10
    gave 0.5009 against 0.6408.
    """
    w = (0.5, 0.5)
    ps = (Predictive(0.0, 0.0, 1.0), Predictive(0.0, shift, ratio))
    assert eig(w, ps) == pytest.approx(_dense_eig(w, ps), abs=EIG_TIE_TOL)


def test_eig_resolves_a_floored_constant_inside_a_wide_component() -> None:
    """A near-constant statistic (variance at the floor) next to a wide one."""
    w = (0.3, 0.7)
    ps = (Predictive(0.0, 1.0, 1e-18), Predictive(0.0, 1.0, 4.0))
    # The point mass is singular to the Gaussian: I(Y; H) = H(w).
    h = -math.fsum(x * math.log(x) for x in w)
    assert eig(w, ps) == pytest.approx(h, abs=1e-6)


def test_the_tie_tolerance_is_stated_and_small() -> None:
    assert 0.0 < EIG_TIE_TOL <= 1e-6


# --------------------------------------------------------------------------
# The tie floor
# --------------------------------------------------------------------------


def test_choose_treats_values_within_the_tolerance_as_ties() -> None:
    tol = 1e-6
    assert choose(((0.5, 0.5 + 0.5 * tol),), tolerance=tol) == (0, 0)
    assert choose(((0.5,), (0.5 + 0.9 * tol,)), tolerance=tol) == (0, 0)
    assert choose(((0.5, 0.5 + 3.0 * tol),), tolerance=tol) == (0, 1)
    # Without a tolerance the strict argmax is unchanged.
    assert choose(((0.5, 0.5 + 0.5 * tol),)) == (0, 1)
    with pytest.raises(PolicyError):
        choose(((0.1,),), tolerance=-1.0)


@given(st.lists(st.floats(0.0, EIG_TIE_TOL), min_size=2, max_size=12))
def test_values_all_below_the_floor_pick_the_first_cell(values: list[float]) -> None:
    assert choose((tuple(values),), tolerance=EIG_TIE_TOL) == (0, 0)


def test_degenerate_weights_take_the_random_arms_draw_deterministically() -> None:
    """BIC 600 apart: w = (1, e^-300). EIG ≤ ε·H(w) ≈ 0 on every cell, so the
    decision is fully tied and takes the random arm's draw for it (Δ11 item 3;
    until 2026-10-28 it took the menu-first cell, (0, 0))."""
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 600.0)))
    a = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    b = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    rnd = RandomPolicy(seed=1).choose(ctx, 0)
    assert (a.design_index, a.diagnostic_index) == (
        rnd.design_index,
        rnd.diagnostic_index,
    )
    assert a == b and repr(a) == repr(b)
    assert a.scores is not None and b.scores is not None
    assert np.array(a.scores).tobytes() == np.array(b.scores).tobytes()
    assert a.eig is not None
    assert a.eig.degenerate
    assert a.eig.full_tie and a.eig.tie == "full"
    assert (
        a.eig.tied == a.eig.cells == len(ctx.menu.designs) * len(ctx.menu.diagnostics)
    )
    assert a.eig.max_eig <= EIG_TIE_TOL
    assert a.eig.entropy < 1e-100


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_fully_tied_decision_is_the_random_arm_on_every_decision(seed: int) -> None:
    """On a degenerate context the EIG arm and the random arm pick the same cell
    at every decision, so their paired contrast there is exactly zero; and the
    pick is not the menu order's."""
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 600.0)))
    eig_arm = EIGPolicy(seed=seed, replicates=4)
    random_arm = RandomPolicy(seed=seed)
    picks = []
    for decision in range(6):
        mine = eig_arm.choose(ctx, decision)
        theirs = random_arm.choose(ctx, decision)
        assert mine.eig is not None and mine.eig.full_tie
        picks.append((mine.design_index, mine.diagnostic_index))
        assert picks[-1] == (theirs.design_index, theirs.diagnostic_index)
    assert any(p != (0, 0) for p in picks)


def test_a_separable_case_is_not_degenerate_and_picks_the_argmax() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 0.0)))
    choice = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    assert choice.eig is not None and not choice.eig.degenerate
    assert choice.scores is not None
    assert choice.eig.max_eig == max(x for row in choice.scores for x in row)
    best = choice.scores[choice.design_index][choice.diagnostic_index]
    assert choice.eig.max_eig - best <= EIG_TIE_TOL
    # A unique maximum is the pick; tied maxima are drawn from TIE_INDEX (Δ11
    # item 3; until 2026-10-28 the first tied cell in menu order).
    flat = [x for row in choice.scores for x in row]
    n_d = len(ctx.menu.diagnostics)
    tied = [
        divmod(i, n_d)
        for i, x in enumerate(flat)
        if choice.eig.max_eig - x <= EIG_TIE_TOL
    ]
    assert (choice.design_index, choice.diagnostic_index) in tied
    assert (choice.design_index, choice.diagnostic_index) == eig_choice(
        choice.scores, EIG_TIE_TOL, 1, 0
    )
    assert choice.eig.tied == len(tied)
    assert choice.eig.tie == ("none" if len(tied) == 1 else "partial")
    assert not choice.eig.full_tie


# --------------------------------------------------------------------------
# The tie rule (Δ11 item 3)
# --------------------------------------------------------------------------


def test_the_tie_index_is_reserved_and_disjoint() -> None:
    reserved = (REFERENCE_INDEX, RANDOM_INDEX, NULL_INDEX, PARAMETRIC_INDEX)
    assert TIE_INDEX not in reserved
    assert ENTERTAINED_MAX <= TIE_INDEX < 2**32
    assert len({*reserved, TIE_INDEX}) == 5


def test_tied_cells_are_the_cells_within_the_floor_in_menu_order() -> None:
    tol = 1e-6
    table = ((0.5, 0.5 + 0.5 * tol, 0.1), (0.5 + 3.0 * tol, 0.5 + 2.5 * tol, 0.2))
    assert tied_cells(table, tol) == [(1, 0), (1, 1)]
    assert tied_cells(((0.0, 0.0), (0.0, 0.0)), tol) == [(0, 0), (0, 1), (1, 0), (1, 1)]
    with pytest.raises(PolicyError):
        tied_cells(((0.1, math.inf),), tol)
    with pytest.raises(PolicyError):
        tied_cells(((0.1,),), -1.0)


def test_a_unique_maximum_is_the_pick() -> None:
    table = ((0.1, 0.2, 0.3), (0.9, 0.4, 0.0))
    for decision in range(5):
        assert eig_choice(table, EIG_TIE_TOL, 7, decision) == (1, 0)


@pytest.mark.parametrize("seed", [0, 7, 2**62])
def test_a_full_tie_is_the_random_draw(seed: int) -> None:
    table = tuple(
        tuple(EIG_TIE_TOL * (d % 3) / 4.0 for d in range(11)) for _ in range(4)
    )
    for decision in range(20):
        rng = generator(policy_seed(seed, decision, RANDOM_INDEX, 0))
        want = divmod(int(rng.integers(4 * 11)), 11)
        assert random_draw(seed, decision, 4, 11) == want
        assert eig_choice(table, EIG_TIE_TOL, seed, decision) == want


@pytest.mark.parametrize("seed", [0, 7, 2**62])
def test_a_strict_subset_tie_draws_from_its_own_index(seed: int) -> None:
    """Cells (0, 2), (1, 0) and (2, 3) tie; (2, 1) is below the floor of them."""
    base = [[0.0] * 4 for _ in range(3)]
    for e, d in ((0, 2), (1, 0), (2, 3)):
        base[e][d] = 0.5
    base[1][0] += 0.5 * EIG_TIE_TOL
    base[2][1] = 0.5 - 2.0 * EIG_TIE_TOL
    table = tuple(tuple(row) for row in base)
    tied = [(0, 2), (1, 0), (2, 3)]
    seen = set()
    for decision in range(40):
        rng = generator(policy_seed(seed, decision, TIE_INDEX, 0))
        want = tied[int(rng.integers(len(tied)))]
        got = eig_choice(table, EIG_TIE_TOL, seed, decision)
        assert got == want
        seen.add(got)
    assert seen == set(tied)


def test_the_tie_draws_are_not_the_random_arms() -> None:
    """The strict-subset draw is its own stream: over decisions it does not
    follow the random arm's draw reduced to the tied cells."""
    seed = 11
    same = 0
    for decision in range(64):
        a = generator(policy_seed(seed, decision, TIE_INDEX, 0)).integers(3)
        b = generator(policy_seed(seed, decision, RANDOM_INDEX, 0)).integers(3)
        same += int(a == b)
    assert same < 40


def test_eig_summary_reports_the_weights_entropy() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 2.0, 1.0), ("c", 3.0, 2.0)))
    choice = EIGPolicy(seed=2, replicates=6).choose(ctx, 1)
    assert choice.eig is not None
    w = ctx.entertained.weights
    assert choice.eig.weights == w
    assert choice.eig.entropy == pytest.approx(
        -math.fsum(x * math.log(x) for x in w), rel=1e-15
    )
    assert choice.eig.tolerance == EIG_TIE_TOL
    # Random and FSD carry no EIG summary.
    assert RandomPolicy(seed=2).choose(ctx, 1).eig is None
    fsd = FSDPolicy(seed=2, replicates=4, reference=PoissonSim(1.5, "r"))
    assert fsd.choose(ctx, 1).eig is None


# --------------------------------------------------------------------------
# Pluggable weights
# --------------------------------------------------------------------------


@given(st.lists(st.floats(0.0, 5_000.0), min_size=1, max_size=6))
def test_tempered_at_one_is_bic_bit_for_bit(bics: list[float]) -> None:
    a = tempered(1.0).weights(bics)
    b = BIC_WEIGHTS.weights(bics)
    assert a == b == bic_weights(bics)
    assert np.array(a).tobytes() == np.array(b).tobytes()


def test_large_temperature_moves_weights_toward_uniform() -> None:
    bics = (0.0, 20.0, 100.0)
    spreads = [
        max(w) - min(w)
        for w in (tempered(t).weights(bics) for t in (1.0, 10.0, 100.0, 1e6))
    ]
    assert spreads == sorted(spreads, reverse=True)
    assert spreads[0] > 0.99
    assert spreads[-1] < 1e-4


def test_tempered_weights_are_exp_minus_delta_bic_over_2t() -> None:
    bics, t = (3.0, 7.0, 11.0), 4.0
    raw = [math.exp(-(b - 3.0) / (2.0 * t)) for b in bics]
    assert tempered(t).weights(bics) == pytest.approx(
        [r / math.fsum(raw) for r in raw], rel=1e-15
    )


def test_weighting_rejects_bad_temperatures() -> None:
    for bad in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(PolicyError):
            tempered(bad)
    with pytest.raises(PolicyError):
        Weighting("bic", 2.0)
    unknown: Any = "softmax"
    with pytest.raises(PolicyError):
        Weighting(unknown, 1.0)


def test_eig_policy_applies_its_weighting_to_the_entertained_set() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 600.0)))
    hot = EIGPolicy(seed=1, replicates=8, weighting=tempered(1_000.0)).choose(ctx, 0)
    assert hot.eig is not None
    assert hot.eig.weights == tempered(1_000.0).weights((0.0, 600.0))
    assert not hot.eig.degenerate
    # The default leaves the fitted set's BIC weights untouched.
    cold = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    assert cold.eig is not None and cold.eig.weights == ctx.entertained.weights


def test_entertained_set_reweighs_by_strategy() -> None:
    hyps = (
        Hypothesis("a", PoissonSim(1.0), 0.0),
        Hypothesis("b", PoissonSim(2.0), 10.0),
    )
    e = EntertainedSet.of(hyps)
    assert e.weights == bic_weights((0.0, 10.0))
    assert e.reweighted(tempered(5.0)).weights == tempered(5.0).weights((0.0, 10.0))
    assert e.reweighted(BIC_WEIGHTS) == e
    assert EntertainedSet.of(hyps, weighting=tempered(5.0)).weights == (
        tempered(5.0).weights((0.0, 10.0))
    )


# --------------------------------------------------------------------------
# Config (for the run key)
# --------------------------------------------------------------------------


def test_policy_config_records_the_weighting_and_the_tie_floor() -> None:
    cfg = json.loads(EIGPolicy(seed=1).config)
    assert cfg["policy"] == "EIG"
    assert cfg["weights"] == {"kind": "bic", "temperature": 1.0}
    assert cfg["tie_tolerance"] == EIG_TIE_TOL
    assert cfg["replicates"] == 16
    hot = json.loads(EIGPolicy(seed=1, weighting=tempered(3.0)).config)
    assert hot["weights"] == {"kind": "tempered", "temperature": 3.0}
    # The seed is per run, not configuration.
    assert EIGPolicy(seed=1).config == EIGPolicy(seed=2).config
    assert json.loads(RandomPolicy(seed=1).config) == {"policy": "random"}
    fsd = json.loads(FSDPolicy(seed=1).config)
    assert fsd["policy"] == "FSD" and fsd["reference"] == "wiener_hopf"
    # Canonical text: sorted keys, so the same configuration is the same bytes.
    text = EIGPolicy(seed=1).config
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))


def test_policy_config_records_the_tie_rule_and_the_precision() -> None:
    """Δ11 items 2, 3 and 5 enter ``EIGPolicy.config`` and so the run key."""
    cfg = json.loads(EIGPolicy(seed=1).config)
    assert cfg["ties"] == {
        "full": {"draw": "random arm", "index": RANDOM_INDEX},
        "partial": {"draw": "uniform over tied cells, menu order", "index": TIE_INDEX},
    }
    assert cfg["precision"].startswith("relative")
    assert cfg["diagnostic"].startswith("kl_pick")
    # The random policy and FSD read none of it.
    assert json.loads(RandomPolicy(seed=1).config) == {"policy": "random"}
    assert "ties" not in json.loads(FSDPolicy(seed=1).config)


def test_the_tie_index_moves_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from sciagent.systems.v2 import policies

    before = EIGPolicy(seed=1).config
    monkeypatch.setattr(policies, "TIE_INDEX", TIE_INDEX - 100)
    assert EIGPolicy(seed=1).config != before


def test_eig_table_is_unchanged_by_the_policy_wrapper() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 1.5, 1.0)))
    choice = EIGPolicy(seed=3, replicates=6).choose(ctx, 4)
    assert choice.scores == eig_table(ctx, 3, 4, replicates=6)


def test_truth_fsd_config_names_the_reference_not_the_truth() -> None:
    from sciagent.systems.v2.policy_controls import TruthFSDPolicy

    cfg = json.loads(TruthFSDPolicy(PoissonSim(2.0), seed=1).config)
    assert cfg == {
        "policy": "truthFSD",
        "replicates": 16,
        "max_events": 15_000,
        "reference": "truth",
        "parametric": "top",
    }
