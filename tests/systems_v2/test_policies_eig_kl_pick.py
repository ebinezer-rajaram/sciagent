"""Δ11 item 5: the KL-ranked pick, logged on every EIG decision, never chosen.

The d11 draft proposed ranking cells by ``Σ_{k≠top} v_k KL(p_k ‖ p_top)``,
``v_k = w_k / (1 - w_top)``, the η → 0 limit of EIG. The red team rejected it
as the arm's rule (LOG 2026-10-27): it is not EIG at the actual weights, and KL
is infinite whenever a rival's run stops in one replicate and the leader's does
not. It is logged framework-side as an exploratory diagnostic, from the same
simulations, recorded as computed: an infinite value stays infinite.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pytest

from environments.pointproc import v2
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import Experiment
from sciagent.systems.v2.policies import (
    DecisionContext,
    EIGPolicy,
    EntertainedSet,
    Hypothesis,
    KLPick,
    Menu,
    Predictive,
    RandomPolicy,
    bic_log_weights,
    design_menu,
    diagnostic_menu,
    eig_predictives,
    eig_table,
    kl_divergence,
    kl_pick,
)

N = Predictive(0.0, 0.0, 1.0)


def _kl_gauss(p: Predictive, q: Predictive) -> float:
    r = p.var / q.var
    return 0.5 * ((r - 1.0) - math.log(r) + (p.mean - q.mean) ** 2 / q.var)


# --------------------------------------------------------------------------
# KL between atom-plus-Gaussian predictives
# --------------------------------------------------------------------------


def test_kl_of_gaussians_is_the_closed_form() -> None:
    for p in (Predictive(0.0, 1.0, 1.0), Predictive(0.0, 0.0, 4.0)):
        assert kl_divergence(p, N) == pytest.approx(_kl_gauss(p, N), rel=1e-14)
    assert kl_divergence(N, N) == 0.0


def test_kl_with_atoms_on_both_sides_is_finite() -> None:
    p, q = Predictive(0.25, 1.0, 1.0), Predictive(0.5, 0.0, 1.0)
    want = (
        0.25 * math.log(0.25 / 0.5)
        + 0.75 * math.log(0.75 / 0.5)
        + 0.75 * _kl_gauss(Predictive(0.0, 1.0, 1.0), N)
    )
    assert kl_divergence(p, q) == pytest.approx(want, rel=1e-14)


def test_kl_is_infinite_when_the_rival_stops_and_the_leader_never_does() -> None:
    """One stopped replicate in 16 against a leader that never stops."""
    assert kl_divergence(Predictive(1.0 / 16.0, 0.0, 1.0), N) == math.inf
    assert kl_divergence(N, Predictive(1.0, 0.0, 1.0)) == math.inf
    assert kl_divergence(Predictive(1.0, 0.0, 1.0), Predictive(0.5, 0.0, 1.0)) == (
        pytest.approx(math.log(2.0), rel=1e-15)
    )


# --------------------------------------------------------------------------
# The pick
# --------------------------------------------------------------------------


def _table(
    cells: Sequence[Sequence[Sequence[Predictive]]],
) -> tuple[tuple[tuple[Predictive, ...], ...], ...]:
    return tuple(tuple(tuple(ps) for ps in row) for row in cells)


def test_the_pick_is_the_argmax_of_the_weighted_kl() -> None:
    lw = bic_log_weights((0.0, 600.0, 610.0))
    table = _table(
        [
            [(N, Predictive(0.0, 1.0, 1.0), N), (N, Predictive(0.0, 0.3, 1.0), N)],
            [(N, Predictive(0.0, 0.0, 4.0), N), (N, N, Predictive(0.0, 5.0, 1.0))],
        ]
    )
    pick = kl_pick(lw, 0, table)
    assert isinstance(pick, KLPick)
    v1 = 1.0 / (1.0 + math.exp(-5.0))
    v2_ = 1.0 - v1
    scores = {
        (0, 0): v1 * 0.5,
        (0, 1): v1 * 0.045,
        (1, 0): v1 * _kl_gauss(Predictive(0.0, 0.0, 4.0), N),
        (1, 1): v2_ * 12.5,
    }
    best = max(scores, key=lambda c: scores[c])
    assert (pick.design_index, pick.diagnostic_index) == best
    assert pick.value == pytest.approx(scores[best], rel=1e-12)
    assert pick.top == 0 and pick.infinite == 0


def test_the_weights_are_taken_in_log_space() -> None:
    """ΔBIC 1600 and 1700: both rivals' float weights underflow to 0, but
    v = (1 - 1/(1+e^-50), …) is still defined."""
    lw = bic_log_weights((0.0, 1600.0, 1700.0))
    table = _table(
        [[(N, Predictive(0.0, 1.0, 1.0), N)], [(N, N, Predictive(0.0, 9.0, 1.0))]]
    )
    pick = kl_pick(lw, 0, table)
    assert pick is not None
    assert (pick.design_index, pick.diagnostic_index) == (0, 0)
    assert pick.value == pytest.approx(0.5, rel=1e-12)


def test_an_infinite_kl_is_recorded_as_computed() -> None:
    """A rival that stops in 1 of 16 replicates against a leader that never
    does: the score is +inf, the pick goes to it (the first such cell in menu
    order), and the infinity is kept, not replaced by a number."""
    lw = bic_log_weights((0.0, 30.0))
    stops = Predictive(1.0 / 16.0, 0.0, 1.0)
    table = _table(
        [[(N, Predictive(0.0, 50.0, 1.0)), (N, stops)], [(N, stops), (N, N)]]
    )
    pick = kl_pick(lw, 0, table)
    assert pick is not None
    assert (pick.design_index, pick.diagnostic_index) == (0, 1)
    assert pick.value == math.inf
    assert pick.infinite == 2
    assert pick.as_json()["value"] == "inf"
    json.dumps(pick.as_json(), allow_nan=False)


def test_one_hypothesis_has_no_pick() -> None:
    assert kl_pick((0.0,), 0, _table([[(N,)]])) is None


# --------------------------------------------------------------------------
# On an EIG decision
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


@pytest.mark.parametrize("dbic", [0.0, 600.0])
def test_every_eig_decision_logs_the_kl_pick_from_the_same_simulations(
    dbic: float,
) -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, dbic)))
    choice = EIGPolicy(seed=1, replicates=6).choose(ctx, 2)
    assert choice.eig is not None
    preds = eig_predictives(ctx, 1, 2, replicates=6)
    lw = bic_log_weights((0.0, dbic))
    assert choice.eig.kl == kl_pick(lw, ctx.entertained.top, preds)
    assert choice.eig.log_weights == lw
    assert choice.scores == eig_table(ctx, 1, 2, replicates=6)
    # The pick is logged, not chosen: on the degenerate context the arm takes
    # the random draw whatever the KL pick says.
    if dbic > 0.0:
        rnd = RandomPolicy(seed=1).choose(ctx, 2)
        assert (choice.design_index, choice.diagnostic_index) == (
            rnd.design_index,
            rnd.diagnostic_index,
        )


def test_the_summary_json_holds_the_degeneracy_and_the_pick() -> None:
    ctx = _context((("a", 1.0, 0.0), ("b", 4.0, 600.0)))
    choice = EIGPolicy(seed=1, replicates=4).choose(ctx, 0)
    assert choice.eig is not None
    record = choice.eig.as_json()
    json.dumps(record, allow_nan=False)
    assert record["degenerate"] is True
    assert record["full_tie"] is True and record["tie"] == "full"
    assert record["tied"] == record["cells"]
    assert record["log_weights"] == list(bic_log_weights((0.0, 600.0)))
    assert record["kl"] == (None if choice.eig.kl is None else choice.eig.kl.as_json())
    assert set(record) == {
        "weights",
        "log_weights",
        "entropy",
        "max_eig",
        "tolerance",
        "tied",
        "cells",
        "degenerate",
        "full_tie",
        "tie",
        "kl",
    }
