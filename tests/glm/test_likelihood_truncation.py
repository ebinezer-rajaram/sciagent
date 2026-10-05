"""The likelihood of a log truncated at the event cap (P4 Δ2; SPEC §2.2, §6.3).

The N-th event's time τ is a stopping time, so the likelihood of a run
stopped there is ``Σ_{i≤N} log λ(tᵢ) - ∫₀^τ λ``, with λ the truth's
intensity under the *planned* horizon H (``Trend`` reads t/H, not t/τ).
:func:`run_experiment_truncating` encodes this through ``Dataset.excluded``,
so the fitter's likelihood needs no change. These tests check:

2. the design's log-likelihood equals that formula computed independently,
   through ``simulate.intensity`` and Gauss-Legendre quadrature between
   events, for a ``Trend`` truth (so H, not τ, is pinned), and that dropping
   the tail exclusion or using τ as the horizon both change it;
3. (slow) the fitter recovers the truth's parameters on truncated logs,
   where the same logs without the tail exclusion give a biased fit;
4. (slow) the score at the truth has mean zero over replicates, where
   without the tail exclusion it does not.
"""

from __future__ import annotations

import math
from typing import Final

import numpy as np
import pytest
from numpy.polynomial.legendre import leggauss
from test_simulate_truncation import (
    CHANNELS,
    Truth,
    marks_exp,
    psi_for,
    trend_hawkes,
)

from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.features import design
from sciagent.glm.fit import fit
from sciagent.glm.grammar import (
    ALL,
    Excite,
    KernelKind,
    Link,
    One,
    PsiSlot,
    Structure,
    Trend,
)
from sciagent.glm.interventions import Compose, Experiment, run_experiment_truncating
from sciagent.glm.likelihood import log_likelihood
from sciagent.glm.simulate import Coefficients, intensity

#: Gauss-Legendre nodes per inter-event interval; λ is smooth between events.
_NODES: Final = 30


def truncated(
    truth: Truth, horizon: float, cap: int, seed: int
) -> tuple[Dataset, float]:
    structure, psi, coef = truth
    ds, tau = run_experiment_truncating(
        structure,
        psi,
        coef,
        CHANNELS,
        marks_exp,
        Experiment(Compose(()), horizon),
        np.random.default_rng(seed),
        max_events=cap,
        label="e0",
    )
    assert tau is not None, "the run must reach its cap"
    return ds, tau


def theta_of(coef: Coefficients) -> Floats:
    return np.array([coef.intercept, *(v for f in coef.per_feature for v in f)])


def stopping_time_loglik(truth: Truth, log: EventLog, tau: float) -> float:
    """``Σ log λ(tᵢ) - ∫₀^τ λ``, with λ from the simulator, H = ``log.horizon``."""
    structure, psi, coef = truth
    lam_events = intensity(structure, psi, coef, CHANNELS, log, log.times)
    x, w = leggauss(_NODES)
    edges = np.concatenate([[0.0], log.times])
    assert edges[-1] == tau
    a, b = edges[:-1], edges[1:]
    half = 0.5 * (b - a)
    nodes = (a[:, None] + half[:, None] * (x[None, :] + 1.0)).ravel()
    weights = (half[:, None] * w[None, :]).ravel()
    lam_nodes = intensity(structure, psi, coef, CHANNELS, log, nodes)
    return math.fsum(np.log(lam_events).tolist()) - math.fsum(
        (weights * lam_nodes).tolist()
    )


def trend_hawkes_exp() -> Truth:
    excite = Excite(KernelKind.EXP, One(), ALL)
    return (
        Structure((Trend(), excite), Link.EXP),
        ({}, psi_for(excite, {"exp_rate": 1.0})),
        Coefficients(-0.5, ((0.8,), (0.3,))),
    )


@pytest.mark.parametrize("truth", [trend_hawkes(), trend_hawkes_exp()])
def test_loglik_is_the_stopping_time_likelihood(truth: Truth) -> None:
    structure, psi, coef = truth
    horizon = 2000.0
    ds, tau = truncated(truth, horizon, cap=300, seed=5)
    assert tau < 0.5 * horizon, "τ well before H, so t/τ and t/H differ"
    theta = theta_of(coef)
    got = log_likelihood(theta, design(structure, psi, ds, CHANNELS), structure.link)
    want = stopping_time_loglik(truth, ds.log, tau)
    assert got == pytest.approx(want, rel=1e-9)

    # Without the tail exclusion the compensator runs to H: a different value.
    untailed = Dataset.create(ds.log, ds.endogenous, (), "e0")
    wrong = log_likelihood(
        theta, design(structure, psi, untailed, CHANNELS), structure.link
    )
    assert want - wrong > 100.0
    # With τ as the horizon, Trend reads t/τ: a different λ, a different value.
    at_tau = EventLog.create(ds.log.times, ds.log.marks, tau)
    other = log_likelihood(
        theta,
        design(structure, psi, Dataset.observational(at_tau), CHANNELS),
        structure.link,
    )
    assert abs(other - want) > 1.0


# --------------------------------------------------------------------------
# 4. The score at the truth has mean zero (slow)
# --------------------------------------------------------------------------


def _score(truth: Truth, ds: Dataset) -> Floats:
    """∂ log L / ∂θ at the truth, identity link: Σ Xᵢ / λᵢ - ∫ X."""
    structure, psi, coef = truth
    d = design(structure, psi, ds, CHANNELS)
    theta = theta_of(coef)
    lam = d.at_events @ theta
    out: Floats = d.at_events.T @ (1.0 / lam) - d.integrals
    return out


@pytest.mark.slow
def test_mean_score_at_the_truth_is_zero() -> None:
    truth = trend_hawkes()
    replicates = 200
    good, naive = [], []
    for seed in range(replicates):
        ds, _ = truncated(truth, 2000.0, cap=300, seed=1000 + seed)
        good.append(_score(truth, ds))
        naive.append(_score(truth, Dataset.create(ds.log, ds.endogenous, (), "e0")))
    for scores, unbiased in ((np.array(good), True), (np.array(naive), False)):
        mean = scores.mean(axis=0)
        se = scores.std(axis=0, ddof=1) / math.sqrt(replicates)
        z = mean / se
        if unbiased:
            assert np.all(np.abs(z) < 4.0), z
        else:
            # Positive control: the instrument separates the right likelihood
            # from the one that integrates the unobserved tail.
            assert z[0] < -10.0, z


# --------------------------------------------------------------------------
# 3. The fitter recovers parameters on truncated logs (slow)
# --------------------------------------------------------------------------

HAWKES: Final = Structure((Excite(KernelKind.EXP, One(), ALL),), Link.IDENTITY)


@pytest.mark.slow
def test_fitter_recovers_parameters_on_truncated_logs() -> None:
    truth: Truth = (
        HAWKES,
        ({PsiSlot((0,), "exp_rate"): 1.0},),
        Coefficients(0.5, ((0.5,),)),
    )
    seeds = range(8)
    good, naive = [], []
    for seed in seeds:
        ds, tau = truncated(truth, 8000.0, cap=2000, seed=2000 + seed)
        assert tau < 0.6 * 8000.0
        result = fit(HAWKES, [ds], CHANNELS)
        assert result.certified
        assert result.psi[0][PsiSlot((0,), "exp_rate")] == 1.0
        good.append(result.theta)
        untailed = Dataset.create(ds.log, ds.endogenous, (), "e0")
        naive.append(fit(HAWKES, [untailed], CHANNELS).theta)
    est = np.array(good)
    mean = est.mean(axis=0)
    se = est.std(axis=0, ddof=1) / math.sqrt(len(seeds))
    assert np.all(np.abs(mean - [0.5, 0.5]) < np.maximum(4.0 * se, 0.03)), (mean, se)
    assert np.all(np.abs(est - [0.5, 0.5]) < 0.15), est
    # Positive control: integrating the unobserved tail biases μ down hard.
    assert np.array(naive)[:, 0].mean() < 0.35, naive
