"""Δ11 item 2: EIG to relative precision (LOG 2026-10-27, adopted 2026-10-28).

On dev the BIC posterior is so concentrated that ``w = (1 - η, η)`` with η as
small as 1e-113. The mutual information is then ``η · KL(p_runner ‖ p_top)``
to leading order (the d11 draft's lemma), but the committed quadrature formed
``log p_w`` as ``top + log(Σ …)``, so the leader's term carried rounding of
order ε·c: 3.1e-18 against a true EIG of 8.1e-114 (red-team probe
``code_eig.1.txt``, cell B). These tests pin EIG against values computed
independently of its quadrature: the lemma's closed form, the binary mutual
information of the "undefined" atom, and a dense trapezoid written with
``log1p``; and the weights' logarithms against their closed form.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from sciagent.systems.v2.policies import (
    Predictive,
    bic_log_weights,
    bic_weights,
    eig,
    log_weight_entropy,
    tempered,
)
from sciagent.systems.v2.policies import (
    _kl_to_mixture as kl_to_mixture,
)

LEADER = Predictive(0.0, 0.0, 1.0)
#: The theorist's cells (``.cache/drafts/d11-scripts/code_eig.py``).
CELLS = {
    "A shift 1 sd": Predictive(0.0, 1.0, 1.0),
    "B sd x2": Predictive(0.0, 0.0, 4.0),
    "C shift 0.3 sd": Predictive(0.0, 0.3, 1.0),
}


def _kl_gauss(p: Predictive, q: Predictive) -> float:
    """``KL(N(p) ‖ N(q))`` in closed form."""
    r = p.var / q.var
    return 0.5 * ((r - 1.0) - math.log(r) + (p.mean - q.mean) ** 2 / q.var)


def _binary_entropy(u: float) -> float:
    if u in (0.0, 1.0):
        return 0.0
    return -u * math.log(u) - (1.0 - u) * math.log1p(-u)


def _dense_two(eta: float, p: Predictive, half_width: float = 40.0) -> float:
    """EIG of ``w = (1 - η, η)`` on ``(LEADER, p)`` by a dense trapezoid.

    Independent of the policy's panels and nodes; written with ``log1p`` where
    ``p_w / p_top`` is near 1, so the leader's term is not the difference of
    two numbers near 1, and with ``logaddexp`` elsewhere.
    """
    y = np.linspace(-half_width, half_width, 2_000_001)
    log_lead = -0.5 * y * y - 0.5 * math.log(2.0 * math.pi)
    log_run = -0.5 * (y - p.mean) ** 2 / p.var - 0.5 * math.log(2.0 * math.pi * p.var)
    c = log_run - log_lead
    # KL(lead ‖ p_w) = E_lead[-log g], g = 1 - η + η e^c
    small = eta * np.expm1(np.minimum(c, 700.0))
    near = np.abs(small) < 0.5
    log_g = np.where(
        near,
        np.log1p(np.where(near, small, 0.0)),
        np.logaddexp(math.log1p(-eta), math.log(eta) + c),
    )
    lead = float(np.trapezoid(np.exp(log_lead) * -log_g, y))
    # KL(p ‖ p_w) = E_p[-log(η + (1 - η) e^{-c})]
    log_mix = np.logaddexp(math.log(eta), math.log1p(-eta) - c)
    runner = float(np.trapezoid(np.exp(log_run) * -log_mix, y))
    return (1.0 - eta) * lead + eta * runner


@pytest.mark.parametrize(
    ("name", "eta"),
    [
        ("A shift 1 sd", 1e-60),
        ("A shift 1 sd", 1e-113),
        ("B sd x2", 1e-113),
        ("C shift 0.3 sd", 1e-60),
        ("C shift 0.3 sd", 1e-113),
    ],
)
def test_eig_reaches_the_small_weight_limit_to_relative_precision(
    name: str, eta: float
) -> None:
    """EIG = η·KL(p ‖ p_top) + o(η), the remainder below 1e-30 relative here
    (cell B's tail sets its remainder, so it is checked at 1e-113 only).

    Committed code at 1e-113, cell B: 3.149e-18 against 8.07e-114.
    """
    p = CELLS[name]
    want = eta * _kl_gauss(p, LEADER)
    got = eig((1.0 - eta, eta), (LEADER, p))
    assert got == pytest.approx(want, rel=1e-12, abs=0.0)


@pytest.mark.parametrize("name", sorted(CELLS))
@pytest.mark.parametrize("eta", [1e-3, 1e-6, 9e-15, 1e-113])
def test_eig_matches_a_dense_log1p_reference_at_concentrated_weights(
    name: str, eta: float
) -> None:
    p = CELLS[name]
    assert eig((1.0 - eta, eta), (LEADER, p)) == pytest.approx(
        _dense_two(eta, p), rel=1e-9, abs=0.0
    )


@pytest.mark.parametrize(
    ("name", "eta"),
    [
        ("A shift 1 sd", 9e-15),
        ("A shift 1 sd", 1e-113),
        ("B sd x2", 1e-113),
        ("C shift 0.3 sd", 9e-15),
        ("C shift 0.3 sd", 1e-113),
    ],
)
def test_the_leaders_term_is_not_rounding(name: str, eta: float) -> None:
    """``w_top · KL(p_top ‖ p_w)`` is O(η²) here (to first order it is
    η·(E_top[p / p_top] - 1) = 0); computed, it must stay far below the EIG.
    The committed code gave 3.1e-18 for cell B at η = 1e-113."""
    p = CELLS[name]
    w = (1.0 - eta, eta)
    leader = w[0] * kl_to_mixture(0, w, (LEADER, p))
    assert 0.0 <= leader <= 1e-12 * eta * _kl_gauss(p, LEADER)


@pytest.mark.parametrize("eta", [1e-3, 9e-15, 1e-113])
@pytest.mark.parametrize("u", [1.0 / 16.0, 0.5])
def test_a_stopping_runner_is_the_atoms_mutual_information(
    eta: float, u: float
) -> None:
    """Same Gaussian part, undefined shares 0 and u: the outcome's only
    information is whether it is undefined, so EIG = h(U) - Σ w_k h(u_k) with
    U = η·u and h the binary entropy. Red-team probe (η = 9e-15, u = 1/16):
    1.82027e-14."""
    w = (1.0 - eta, eta)
    want = _binary_entropy(eta * u) - eta * _binary_entropy(u)
    got = eig(w, (LEADER, Predictive(u, 0.0, 1.0)))
    assert got == pytest.approx(want, rel=1e-12, abs=0.0)


@pytest.mark.parametrize("var", [100.0, 1e4])
@pytest.mark.parametrize("eta", [9e-15, 1e-30, 1e-113])
def test_a_wide_runner_past_the_leaders_range_is_integrated(
    var: float, eta: float
) -> None:
    """Variance ratio 100: the runner's mass beyond 12 sd of the leader is 23 %,
    and at η = 1e-113 ``-log p_w`` is still linear in η out to 23 sd, so a
    range clipped to the leader's 12 sd misses about 0.2·η of the leader's
    term, 4e-3 of the EIG; and where η p meets p_top, ``log p_w`` turns within
    0.04 sd, which panels 5 sd wide step over (7.5e-5 of the EIG)."""
    p = Predictive(0.0, 0.0, var)
    want = _dense_two(eta, p, half_width=15.0 * math.sqrt(var))
    got = eig((1.0 - eta, eta), (LEADER, p))
    assert got == pytest.approx(want, rel=1e-8, abs=0.0)


# --------------------------------------------------------------------------
# Weights in log space
# --------------------------------------------------------------------------


def test_log_weights_are_the_closed_form() -> None:
    lw = bic_log_weights((0.0, 600.0))
    tail = math.exp(-300.0)
    assert lw[0] == -math.log1p(tail)
    assert lw[1] == pytest.approx(-300.0 - math.log1p(tail), rel=1e-15)
    assert lw[0] != 0.0  # the leader's -η is kept, not rounded to log 1
    three = bic_log_weights((4.0, 0.0, 10.0))
    z = (-2.0, 0.0, -5.0)
    norm = math.log(math.fsum(math.exp(x) for x in z))
    assert three == pytest.approx(tuple(x - norm for x in z), rel=1e-15, abs=1e-16)


def test_log_weights_survive_where_weights_underflow() -> None:
    assert bic_weights((0.0, 1600.0))[1] == 0.0
    assert bic_log_weights((0.0, 1600.0)) == (0.0, -800.0)


def test_log_weights_follow_the_temperature() -> None:
    bics = (3.0, 7.0, 11.0)
    t = 4.0
    assert tempered(t).log_weights(bics) == bic_log_weights(bics, temperature=t)
    assert tempered(1.0).log_weights(bics) == bic_log_weights(bics)
    pairs = zip(tempered(t).log_weights(bics), tempered(t).weights(bics), strict=True)
    for a, b in pairs:
        assert math.exp(a) == pytest.approx(b, rel=1e-15, abs=0.0)


@pytest.mark.parametrize("eta", [1e-3, 9e-15, 1e-113])
def test_the_entropy_from_log_weights_keeps_the_leaders_share(eta: float) -> None:
    """H(1 - η, η) = η(1 - log η) + O(η²); ``-Σ w log w`` on the floats drops
    the leader's η (w_top rounds to 1), 0.4 % of H at η = 1e-113."""
    lw = (math.log1p(-eta), math.log(eta))
    want = -(1.0 - eta) * math.log1p(-eta) - eta * math.log(eta)
    assert log_weight_entropy(lw) == pytest.approx(want, rel=1e-14, abs=0.0)
