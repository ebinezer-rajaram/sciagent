"""θ sampling, the operating point and stationarity (SPEC §3; instrument test 6)."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest

from environments.pointproc.truths_v2 import CALIBRATION, THETA_PRIOR, mark_mean
from environments.pointproc.v2 import CHANNELS, mark_sampler
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import (
    ALL,
    Above,
    Excite,
    ExpOf,
    Gate,
    KernelKind,
    Link,
    Mark,
    MarkFn,
    One,
    Periodic,
    PhaseWindow,
    Pow,
    Product,
    PsiSlot,
    Source,
    SourceKind,
    Structure,
)
from sciagent.glm.simulate import Coefficients
from sciagent.scenarios.calibrate import (
    CalibrationConfigError,
    CalibrationSettings,
    CandidateRejectedError,
    apply_knob,
    branching_bound,
    calibrate,
    column_scales,
    operating_point,
    sample_theta,
)
from sciagent.scenarios.streams import stream as derive_generator

PLUS = Source(SourceKind.POSITIVE, "sign")
HAWKES = Structure((Excite(KernelKind.EXP, One(), ALL),), Link.IDENTITY)
RATE = PsiSlot((0,), "exp_rate")


def _factory(seed: int, key: str) -> Callable[[str], np.random.Generator]:
    return lambda stage: derive_generator(seed, f"{key}:{stage}")


# --------------------------------------------------------------------------
# The pointproc mark moments
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mark", "psi", "source"),
    [
        (One(), None, ALL),
        (One(), None, PLUS),
        (Mark("size"), None, ALL),
        (Mark("sign"), None, ALL),
        (Pow("size"), 1.5, PLUS),
        (ExpOf("size"), 0.5, ALL),
        (Above("size"), 0.5, ALL),
    ],
)
def test_pointproc_mark_mean_matches_monte_carlo(
    mark: MarkFn, psi: float | None, source: Source
) -> None:
    rng = derive_generator(0, "mc")
    draws = [mark_sampler(rng) for _ in range(200_000)]
    size = np.array([d["size"] for d in draws])
    sign = np.array([d["sign"] for d in draws])
    keep = np.ones_like(size) if source.kind is SourceKind.ALL else (sign > 0)
    match mark:
        case One():
            f = np.ones_like(size)
        case Mark(channel="size"):
            f = size
        case Mark(channel="sign"):
            f = np.abs(sign)
        case Pow():
            f = size ** float(psi or 0)
        case ExpOf():
            f = np.exp(float(psi or 0) * (size - 1.0))
        case Above():
            f = (size - 1.0 > float(psi or 0)).astype(float)
        case _:
            raise AssertionError(mark)
    mc = float(np.mean(f * keep))
    assert mark_mean(mark, psi, source) == pytest.approx(mc, rel=0.02)


def test_exp_of_with_infinite_mean_is_infinite() -> None:
    assert math.isinf(mark_mean(ExpOf("size"), 1.0, ALL))


# --------------------------------------------------------------------------
# Branching bound
# --------------------------------------------------------------------------


def test_branching_bound_is_the_branching_ratio_for_linear_excitation() -> None:
    psi = ({RATE: 1.0},)
    assert branching_bound(HAWKES, psi, Coefficients(0.3, ((0.7,),)), mark_mean) == (
        pytest.approx(0.7)
    )
    s = Structure((Excite(KernelKind.EXP, Pow("size"), PLUS),), Link.IDENTITY)
    psi2 = ({RATE: 1.0, PsiSlot((1,), "pow_exponent"): 2.0},)
    # E[size²] = 2, and half the events are on the + source.
    assert branching_bound(s, psi2, Coefficients(0.3, ((0.4,),)), mark_mean) == (
        pytest.approx(0.4)
    )


def test_branching_bound_through_gates_and_periodic_products() -> None:
    excite = Excite(KernelKind.EXP, One(), ALL)
    s = Structure(
        (Gate(Product(excite, Periodic()), PhaseWindow()), Periodic()), Link.IDENTITY
    )
    psi = (
        {
            PsiSlot((0, 0, 0), "exp_rate"): 1.0,
            PsiSlot((0, 1), "period"): 10.0,
            PsiSlot((1,), "period"): 10.0,
            PsiSlot((1,), "phase"): 0.0,
        },
        {PsiSlot((), "period"): 10.0},
    )
    coef = Coefficients(1.0, ((0.2, -0.3), (0.5, 0.5)))
    # |0.2| + |-0.3| bounds the per-event offspring; Periodic is exogenous.
    assert branching_bound(s, psi, coef, mark_mean) == pytest.approx(0.5)


def test_branching_bound_is_none_when_nonlinear_or_not_identity() -> None:
    e = Excite(KernelKind.EXP, One(), ALL)
    s = Structure((Product(e, e),), Link.IDENTITY)
    psi = ({PsiSlot((0, 0), "exp_rate"): 1.0, PsiSlot((1, 0), "exp_rate"): 1.0},)
    assert branching_bound(s, psi, Coefficients(1.0, ((0.1,),)), mark_mean) is None
    exp_hawkes = Structure(HAWKES.features, Link.EXP)
    assert (
        branching_bound(exp_hawkes, ({RATE: 1.0},), Coefficients(0, ((1,),)), mark_mean)
        is None
    )


# --------------------------------------------------------------------------
# Operating point
# --------------------------------------------------------------------------


def test_operating_point_on_a_hand_built_log() -> None:
    # 100 events evenly spaced on [0, 100]: rate 1, Fano 0 at window 2, CV 0.
    t = np.arange(100) + 0.5
    log = EventLog.create(t, {}, 100.0)
    op = operating_point(log, burn_in=0.0, window=2.0)
    assert op.mean_rate == pytest.approx(1.0)
    assert op.fano == pytest.approx(0.0)
    assert op.cv == pytest.approx(0.0, abs=1e-12)
    assert op.drift == pytest.approx(1.0)
    # Burn-in removes the first 10% of the window.
    op2 = operating_point(log, burn_in=0.1, window=2.0)
    assert op2.n_events == 90


def test_operating_point_of_poisson_is_near_one() -> None:
    from sciagent.glm.simulate import simulate

    log = simulate(
        Structure(()),
        (),
        Coefficients(2.0, ()),
        CHANNELS,
        mark_sampler,
        5000.0,
        derive_generator(1, "poisson"),
    )
    op = operating_point(log, burn_in=0.1, window=2.0)
    assert op.mean_rate == pytest.approx(2.0, rel=0.05)
    assert op.fano == pytest.approx(1.0, abs=0.1)
    assert op.cv == pytest.approx(1.0, abs=0.05)


# --------------------------------------------------------------------------
# θ sampling and the knob
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_column_scales_of_hawkes_excitation() -> None:
    # Under a rate-1 Poisson reference, an ExpK(β) shot noise of unit marks has
    # mean 1 and variance β/2.
    scales = column_scales(
        HAWKES, ({RATE: 2.0},), CHANNELS, mark_sampler, derive_generator(0, "r"), 3000.0
    )
    (((mean_abs, sd, max_abs),),) = scales
    assert mean_abs == pytest.approx(1.0, rel=0.06)
    assert sd == pytest.approx(1.0, rel=0.1)
    assert max_abs > 2.0


def test_sample_theta_respects_link_conventions() -> None:
    rng = derive_generator(0, "theta")
    scales = (((1.0, 1.0, 3.0),),)
    for _ in range(20):
        c = sample_theta(HAWKES, scales, THETA_PRIOR, rng)
        lo, hi = THETA_PRIOR.identity_total
        assert lo <= c.per_feature[0][0] <= hi  # one feature takes the whole total
        assert c.intercept == 1.0
    exp_hawkes = Structure(HAWKES.features, Link.EXP)
    signs = {
        math.copysign(
            1.0, sample_theta(exp_hawkes, scales, THETA_PRIOR, rng).per_feature[0][0]
        )
        for _ in range(60)
    }
    assert signs == {1.0, -1.0}


def test_apply_knob() -> None:
    s = Structure((Excite(KernelKind.EXP, One(), ALL), Periodic()), Link.IDENTITY)
    c = Coefficients(1.0, ((0.5,), (0.2, 0.1)))
    k = apply_knob(s, c, math.log(2.0))
    # Identity: θ₀ and the exogenous feature scale; the excitation keeps its ratio.
    assert k == Coefficients(2.0, ((0.5,), (0.4, 0.2)))
    e = Structure(s.features, Link.EXP)
    assert apply_knob(e, c, 0.25) == Coefficients(1.25, c.per_feature)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

FAST = CalibrationSettings(
    pilot_horizons=(400.0, 2000.0),
    rate_tols=(0.1, 0.03),
    max_iter=8,
    event_cap_factor=3.0,
    burn_in=0.1,
    fano_window=2.0,
    fano_band=(1.0, 50.0),
    max_drift=1.5,
    max_branching=0.9,
)


def test_calibration_reaches_the_operating_point_for_identity_hawkes() -> None:
    psi = ({RATE: 1.0},)
    cal = calibrate(
        HAWKES,
        psi,
        Coefficients(1.0, ((0.6,),)),
        CHANNELS,
        mark_sampler,
        FAST,
        _factory(3, "cal"),
        mark_mean,
    )
    assert abs(cal.point.mean_rate - 1.0) <= FAST.rate_tols[-1]
    # Linear Hawkes: rate = θ₀ / (1 - n), so θ₀ lands near 0.4.
    assert cal.coef.intercept == pytest.approx(0.4, rel=0.15)
    assert cal.coef.per_feature == ((0.6,),)
    assert cal.branching == pytest.approx(0.6)


def test_calibration_shifts_the_intercept_under_the_exp_link() -> None:
    s = Structure((Periodic(),), Link.EXP)
    psi = ({PsiSlot((), "period"): 10.0},)
    cal = calibrate(
        s,
        psi,
        Coefficients(0.0, ((1.0, 0.5),)),
        CHANNELS,
        mark_sampler,
        FAST,
        _factory(4, "cal"),
        mark_mean,
    )
    assert abs(cal.point.mean_rate - 1.0) <= FAST.rate_tols[-1]
    # Mean of exp(A cos) over a cycle is I₀(A); A = |(1, 0.5)|.
    from scipy.special import i0

    assert cal.coef.intercept == pytest.approx(
        -math.log(i0(math.hypot(1.0, 0.5))), abs=0.05
    )
    assert cal.coef.per_feature == ((1.0, 0.5),)


def test_calibration_is_deterministic() -> None:
    psi = ({RATE: 2.0},)
    args = (HAWKES, psi, Coefficients(1.0, ((0.5,),)), CHANNELS, mark_sampler, FAST)
    a = calibrate(*args, _factory(5, "c"), mark_mean)
    b = calibrate(*args, _factory(5, "c"), mark_mean)
    assert a == b


def test_supercritical_identity_is_rejected_analytically() -> None:
    with pytest.raises(CandidateRejectedError, match="branching"):
        calibrate(
            HAWKES,
            ({RATE: 1.0},),
            Coefficients(1.0, ((0.95,),)),
            CHANNELS,
            mark_sampler,
            FAST,
            _factory(0, "x"),
            mark_mean,
        )


def test_exp_link_self_excitation_explodes_and_is_rejected_quickly() -> None:
    s = Structure(HAWKES.features, Link.EXP)
    with pytest.raises(CandidateRejectedError):
        calibrate(
            s,
            ({RATE: 1.0},),
            Coefficients(0.0, ((4.0,),)),
            CHANNELS,
            mark_sampler,
            FAST,
            _factory(0, "x"),
            mark_mean,
        )


def test_negative_identity_intensity_is_rejected() -> None:
    s = Structure((Excite(KernelKind.EXP, Mark("sign"), ALL),), Link.IDENTITY)
    with pytest.raises(CandidateRejectedError, match="negative"):
        calibrate(
            s,
            ({RATE: 8.0},),
            Coefficients(0.05, ((0.8,),)),
            CHANNELS,
            mark_sampler,
            FAST,
            _factory(0, "x"),
            mark_mean,
        )


def test_settings_are_validated() -> None:
    with pytest.raises(CalibrationConfigError):
        CalibrationSettings(
            pilot_horizons=(400.0,),
            rate_tols=(0.1, 0.02),
            max_iter=8,
            event_cap_factor=3.0,
            burn_in=0.1,
            fano_window=2.0,
            fano_band=(2.0, 5.0),
            max_drift=1.5,
            max_branching=0.9,
        )


def test_pointproc_calibration_settings_are_declared() -> None:
    assert CALIBRATION.rate_tols[-1] <= 0.03
    lo, hi = CALIBRATION.fano_band
    # The library's non-null truths have Fano(2) ≈ 3.1-3.6 (truths_v2.py).
    assert lo < 3.1 and hi > 3.6
