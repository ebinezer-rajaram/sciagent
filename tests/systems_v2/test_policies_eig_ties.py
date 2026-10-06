"""WP5b: EIG quadrature accuracy, the numerical tie floor, pluggable weights.

The orchestrator's decision (2026-10-06): BIC posterior weights stay the
default; a concentrated posterior giving EIG ≈ 0 is the evidence wall itself
(``docs/v2/evidence-wall.md`` Proposition 1), but the pick must then not be
decided by numerical noise. So EIG values within :data:`EIG_TIE_TOL` of the
maximum are exact ties, broken by menu order; the tolerance is derived from the
quadrature's measured error, which these tests pin against an independent
dense-grid reference, including extreme variance ratios.
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
from sciagent.systems.v2.policies import (
    BIC_WEIGHTS,
    EIG_TIE_TOL,
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
    eig_table,
    tempered,
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


def test_degenerate_weights_pick_the_menu_first_cell_deterministically() -> None:
    """BIC 600 apart: w = (1, e^-300). EIG ≤ ε·H(w) ≈ 0 on every cell."""
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 600.0)))
    a = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    b = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    assert (a.design_index, a.diagnostic_index) == (0, 0)
    assert a == b and repr(a) == repr(b)
    assert a.scores is not None and b.scores is not None
    assert np.array(a.scores).tobytes() == np.array(b.scores).tobytes()
    assert a.eig is not None
    assert a.eig.degenerate
    assert a.eig.max_eig <= EIG_TIE_TOL
    assert a.eig.entropy < 1e-100


def test_a_separable_case_is_not_degenerate_and_picks_the_argmax() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 0.0)))
    choice = EIGPolicy(seed=1, replicates=8).choose(ctx, 0)
    assert choice.eig is not None and not choice.eig.degenerate
    assert choice.scores is not None
    assert choice.eig.max_eig == max(x for row in choice.scores for x in row)
    best = choice.scores[choice.design_index][choice.diagnostic_index]
    assert choice.eig.max_eig - best <= EIG_TIE_TOL
    # The first cell in menu order within the tolerance of the max.
    flat = [x for row in choice.scores for x in row]
    first = next(i for i, x in enumerate(flat) if choice.eig.max_eig - x <= EIG_TIE_TOL)
    n_d = len(ctx.menu.diagnostics)
    assert (choice.design_index, choice.diagnostic_index) == divmod(first, n_d)
    assert choice.eig.tied == sum(
        1 for x in flat if choice.eig.max_eig - x <= EIG_TIE_TOL
    )


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
