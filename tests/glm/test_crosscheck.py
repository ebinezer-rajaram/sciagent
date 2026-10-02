"""Cross-check of two independent implementations (SPEC §6.3 instrument test 1).

``simulate.py`` (the thinning simulator and its pointwise ``intensity``) and
``features.py`` + ``likelihood.py`` (design columns, exact compensator, log
likelihood) were written independently from the semantics in ``grammar.py``'s
docstring. A bug shared by the simulator and the likelihood would make every
fit "recover" a wrong truth, so this file checks that they agree:

1. **Pointwise.** ``g(θ · evaluate_columns)`` equals ``simulate.intensity`` at
   random times, at event times and just after them. History is a left limit in
   both implementations: at ``t = tᵢ`` event i does not count, at ``t = tᵢ + ε``
   it does, and the tests pin both sides of every jump.
2. **Time rescaling.** The compensator increments between events of a log
   simulated under the truth are iid Exp(1); a KS test must not reject. A
   positive control (the same log through a deliberately wrong, intercept-only
   compensator) must reject, otherwise the test could not have failed.
3. **Log-likelihood.** ``log_likelihood`` equals ``Σ log λ(tᵢ) - Λ(T)`` with
   ``λ`` from ``simulate.intensity`` and ``Λ`` by adaptive ``scipy`` quadrature
   between events.

Every kernel x every link, every mark function, signed sources, ``Periodic``,
``Trend``, ``Product`` and ``Gate`` are covered. Channels: ``size`` (POSITIVE,
location 1, scale 1, drawn Exp(1)) and ``sign`` (SIGN, fair coin).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from itertools import pairwise

import numpy as np
import pytest
from _pytest.mark import ParameterSet
from scipy import integrate, stats

from sciagent.glm.data import EventLog, Floats
from sciagent.glm.features import design, evaluate_columns
from sciagent.glm.grammar import (
    ALL,
    Above,
    ChannelKind,
    ChannelSpec,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    One,
    Periodic,
    PhaseWindow,
    Pow,
    Product,
    PsiSlot,
    Source,
    SourceKind,
    Structure,
    Trend,
    psi_slots,
)
from sciagent.glm.grids import grid
from sciagent.glm.likelihood import compensator, log_likelihood
from sciagent.glm.simulate import Coefficients, PsiAssignment, intensity, simulate

CHANNELS = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
    ChannelSpec("sign", ChannelKind.SIGN, location=0.0, scale=1.0),
)

POS = Source(SourceKind.POSITIVE, "sign")
NEG = Source(SourceKind.NEGATIVE, "sign")

LINKS = (Link.IDENTITY, Link.EXP, Link.SOFTPLUS)

#: The "tiny" offset after an event time (event i is history from here on).
EPS = 1e-9
#: Pointwise tolerance.
RTOL_POINT = 1e-10
#: Log-likelihood tolerance against quadrature of the simulator's intensity.
RTOL_LOGLIK = 1e-8
#: A KS test at this level must not reject a correct compensator.
KS_LEVEL = 1e-3


def draw_marks(rng: np.random.Generator) -> Mapping[str, float]:
    """Sizes Exp(1) (location 1, scale 1), signs a fair coin."""
    size = float(rng.exponential(1.0))
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"size": size, "sign": sign}


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

#: One truth's numbers under one link: θ₀ and per-feature coefficients.
type Truth = tuple[float, tuple[tuple[float, ...], ...]]


@dataclass(frozen=True)
class Case:
    """A truth: features, on-grid ψ, and θ under each link, and how to run it.

    ``truth[link]`` holds θ₀ and the per-feature coefficients, chosen per link
    for stability and for a signal strong enough to tell from a constant rate:
    the identity link needs λ > 0 (small or nonnegative coefficients); the exp
    link explodes under positive feedback, so excitation is *inhibitory* there;
    softplus is close to linear for large η and tolerates strong excitation.
    ``phase_windows`` lists the ``(period, phase)`` of every ``PhaseWindow``,
    whose switch times the quadrature must be told about. ``fast`` cases run
    in the fast tier at small sizes. ``weak`` lists the links under which the
    truth is too close to a constant rate for the positive control to apply.
    """

    name: str
    features: tuple[Feature, ...]
    psi_values: tuple[Mapping[str, float], ...]
    truth: Mapping[Link, Truth]
    phase_windows: tuple[tuple[float, float], ...] = ()
    fast: bool = False
    weak: tuple[Link, ...] = ()


def truths(identity: Truth, exp: Truth, softplus: Truth) -> dict[Link, Truth]:
    return {Link.IDENTITY: identity, Link.EXP: exp, Link.SOFTPLUS: softplus}


def psi_for(feature: Feature, values: Mapping[str, float]) -> dict[PsiSlot, float]:
    """Assign ψ by parameter name; ``"name@path"`` (path joined by dots, e.g.
    ``"period@1"``) overrides one slot, so two slots of one name can differ."""
    out: dict[PsiSlot, float] = {}
    for slot in psi_slots(feature):
        key = f"{slot.name}@{'.'.join(map(str, slot.path))}"
        out[slot] = values[key] if key in values else values[slot.name]
    return out


type Vec = np.ndarray[tuple[int], np.dtype[np.float64]]


@dataclass(frozen=True)
class Model:
    structure: Structure
    psi: PsiAssignment
    coef: Coefficients
    theta: Vec


def build(case: Case, link: Link) -> Model:
    """The model for ``case`` under ``link``, with θ in design-column order.

    Column order (``features.Design``): the intercept, then each feature's
    columns in feature order.
    """
    structure = Structure(case.features, link)
    psi = tuple(
        psi_for(f, v) for f, v in zip(case.features, case.psi_values, strict=True)
    )
    intercept, per_feature = case.truth[link]
    coef = Coefficients(intercept, per_feature)
    theta = np.array(
        [intercept, *(c for row in per_feature for c in row)], dtype=np.float64
    )
    return Model(structure, psi, coef, theta)


EXP2 = {"exp_rate": 2.0}
POWER = {"power_c": 0.2, "power_p": 2.0}
GAMMA3 = {"gamma_shape": 3.0, "gamma_mean": 1.0}

CASES: tuple[Case, ...] = (
    # --- every kernel (One mark, all sources) ------------------------------
    Case(
        "kernel_exp",
        (Excite(KernelKind.EXP, One(), ALL),),
        (EXP2,),
        truths((0.5, ((0.5,),)), (0.5, ((-0.75,),)), (-1.0, ((0.8,),))),
        fast=True,
    ),
    Case(
        "kernel_power",
        (Excite(KernelKind.POWER, One(), ALL),),
        (POWER,),
        truths((0.5, ((0.5,),)), (0.5, ((-0.3,),)), (0.2, ((0.6,),))),
    ),
    Case(
        "kernel_gamma",
        (Excite(KernelKind.GAMMA, One(), ALL),),
        (GAMMA3,),
        truths((0.5, ((0.5,),)), (0.5, ((-1.5,),)), (-1.0, ((0.8,),))),
    ),
    # --- every mark function -----------------------------------------------
    Case(
        "mark_mark",
        (Excite(KernelKind.EXP, Mark("size"), ALL),),
        ({"exp_rate": 1.0},),
        truths((0.5, ((0.4,),)), (0.5, ((-0.6,),)), (0.2, ((0.6,),))),
    ),
    Case(
        "mark_pow",
        (Excite(KernelKind.EXP, Pow("size"), ALL),),
        ({"exp_rate": 1.0, "pow_exponent": 0.5},),
        truths((0.5, ((0.5,),)), (0.5, ((-0.6,),)), (0.2, ((0.6,),))),
    ),
    Case(
        "mark_expof",
        (Excite(KernelKind.GAMMA, ExpOf("size"), ALL),),
        ({"gamma_shape": 2.0, "gamma_mean": 2.0, "exp_coef": 0.5},),
        truths((0.5, ((0.3,),)), (0.5, ((-3.0,),)), (0.2, ((0.5,),))),
    ),
    Case(
        "mark_above",
        (Excite(KernelKind.EXP, Above("size"), ALL),),
        ({"exp_rate": 2.0, "above_z": 0.5},),
        truths((0.5, ((1.0,),)), (0.5, ((-2.0,),)), (0.2, ((1.2,),))),
    ),
    # --- signed sources, two features --------------------------------------
    Case(
        "signed",
        (
            Excite(KernelKind.EXP, One(), POS),
            Excite(KernelKind.GAMMA, Mark("size"), NEG),
        ),
        (EXP2, GAMMA3),
        truths(
            (0.5, ((0.5,), (0.4,))),
            (0.5, ((-0.75,), (-0.8,))),
            (0.2, ((0.8,), (0.6,))),
        ),
        fast=True,
    ),
    # --- other leaves -------------------------------------------------------
    Case(
        "periodic",
        (Periodic(),),
        ({"period": 10.0},),
        truths((1.0, ((0.5, 0.3),)), (0.0, ((0.8, 0.5),)), (0.5, ((1.2, 0.8),))),
    ),
    Case(
        "trend",
        (Trend(),),
        ({},),
        truths((0.1, ((1.8,),)), (0.5, ((-1.5,),)), (-1.5, ((4.0,),))),
    ),
    # --- products ------------------------------------------------------------
    Case(
        "product_excite_periodic",
        (Product(Excite(KernelKind.EXP, One(), ALL), Periodic()),),
        ({"exp_rate": 2.0, "period": 10.0},),
        truths((1.0, ((0.08, 0.08),)), (0.0, ((0.1, 0.1),)), (0.3, ((0.8, 0.8),))),
        fast=True,
        weak=(Link.IDENTITY,),
    ),
    Case(
        "product_excite_excite",
        (
            Product(
                Excite(KernelKind.EXP, One(), ALL),
                Excite(KernelKind.GAMMA, Mark("size"), POS),
            ),
        ),
        ({"exp_rate": 1.0, "gamma_shape": 2.0, "gamma_mean": 1.0},),
        truths((0.5, ((0.1,),)), (0.5, ((-0.3,),)), (0.8, ((-0.4,),))),
    ),
    # --- gates ---------------------------------------------------------------
    Case(
        "gate_lastmark",
        (Gate(Excite(KernelKind.EXP, Mark("size"), ALL), LastMarkAbove("size")),),
        ({"exp_rate": 1.0, "above_z": 0.5},),
        truths((0.5, ((0.6,),)), (0.5, ((-0.6,),)), (-1.0, ((0.8,),))),
        fast=True,
    ),
    Case(
        "gate_phase",
        (Gate(Excite(KernelKind.EXP, One(), ALL), PhaseWindow()),),
        ({"exp_rate": 1.0, "period": 10.0, "phase": 0.5 * math.pi},),
        truths((0.5, ((0.8,),)), (0.5, ((-0.8,),)), (0.2, ((1.0,),))),
        phase_windows=((10.0, 0.5 * math.pi),),
        fast=True,
    ),
    Case(
        "gate_phase_periodic",
        (Gate(Periodic(), PhaseWindow()),),
        ({"period@0": 25.0, "period@1": 5.0, "phase": math.pi},),
        truths((0.8, ((0.6, 0.4),)), (0.0, ((0.8, 0.5),)), (0.5, ((1.2, 0.8),))),
        phase_windows=((5.0, math.pi),),
    ),
    # --- depth 3 --------------------------------------------------------------
    Case(
        "deep_gate_product",
        (
            Gate(
                Product(Excite(KernelKind.GAMMA, Pow("size"), POS), Periodic()),
                LastMarkAbove("size"),
            ),
        ),
        (
            {
                "gamma_shape": 2.0,
                "gamma_mean": 1.0,
                "pow_exponent": 0.5,
                "period": 10.0,
                "above_z": 0.0,
            },
        ),
        truths((1.0, ((0.1, 0.1),)), (0.0, ((1.2, 1.2),)), (-0.5, ((2.0, 2.0),))),
        weak=(Link.IDENTITY,),
    ),
)
CASE_BY_NAME = {c.name: c for c in CASES}


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

SLOW = pytest.mark.slow


def link_fn(link: Link, eta: Floats) -> Floats:
    """The inverse link g, written here from SPEC §2.1, not imported."""
    match link:
        case Link.IDENTITY:
            return eta
        case Link.EXP:
            out: Floats = np.exp(eta)
            return out
        case Link.SOFTPLUS:
            out = np.logaddexp(0.0, eta)
            return out


@cache
def sample(name: str, link: Link, n_target: int, seed: int) -> EventLog:
    """A log of about ``n_target`` events drawn from the truth ``name`` x ``link``.

    The horizon comes from a pilot run at horizon 150 (rates differ a lot
    between truths); the pilot's draws are not reused.
    """
    case = CASE_BY_NAME[name]
    m = build(case, link)
    stream = [seed, n_target, list(CASE_BY_NAME).index(name)]
    pilot = simulate(
        m.structure,
        m.psi,
        m.coef,
        CHANNELS,
        draw_marks,
        150.0,
        np.random.default_rng([*stream, 0]),
        max_events=3000,
    )
    rate = max(pilot.n, 15) / 150.0
    return simulate(
        m.structure,
        m.psi,
        m.coef,
        CHANNELS,
        draw_marks,
        n_target / rate,
        np.random.default_rng([*stream, 1]),
        max_events=3 * n_target,
    )


def ids(cases: list[tuple[str, Link]]) -> list[str]:
    return [f"{n}-{link.value}" for n, link in cases]


def grid_params(*, fast_cases: bool) -> list[ParameterSet]:
    """Every (case, link) of the fast cases (``fast_cases``) or of the rest,
    which run in the slow tier."""
    return [
        pytest.param(
            c.name,
            link,
            id=f"{c.name}-{link.value}",
            marks=() if fast_cases else (SLOW,),
        )
        for c in CASES
        if c.fast == fast_cases
        for link in LINKS
    ]


# --------------------------------------------------------------------------
# The cases themselves
# --------------------------------------------------------------------------


def test_every_psi_is_on_grid() -> None:
    """The cases use on-grid ψ, so ``features`` accepts them without a flag."""
    for c in CASES:
        for feature, values in zip(c.features, c.psi_values, strict=True):
            for slot, value in psi_for(feature, values).items():
                assert value in grid(slot.name), (c.name, slot, value)


@pytest.mark.parametrize("link", LINKS)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_theta_layout_matches_the_design(case: Case, link: Link) -> None:
    """θ = (intercept, feature 0's columns, feature 1's columns, ...) is
    ``Design``'s column order, which is what the mapping from ``Coefficients``
    to θ in every other test relies on."""
    m = build(case, link)
    log = sample(case.name, link, 40, 0) if case.fast else _tiny_log(case, link)
    d = design(m.structure, m.psi, log, CHANNELS)
    assert d.n_columns == m.theta.size
    assert d.labels[0] == "θ0"
    start = 1
    for cols, row in zip(d.feature_columns, m.coef.per_feature, strict=True):
        assert cols == tuple(range(start, start + len(row)))
        start += len(row)
    assert start == m.theta.size


def _tiny_log(case: Case, link: Link) -> EventLog:
    return sample(case.name, link, 30, 0)


# --------------------------------------------------------------------------
# 1. Pointwise agreement
# --------------------------------------------------------------------------


def lambda_from_design(m: Model, log: EventLog, t: Floats) -> Floats:
    """``g(θ · X(t))`` with ``X`` from ``features.evaluate_columns``."""
    x = evaluate_columns(m.structure, m.psi, log, CHANNELS, t)
    return link_fn(m.structure.link, x @ m.theta)


def lambda_from_simulator(m: Model, log: EventLog, t: Floats) -> Floats:
    return intensity(m.structure, m.psi, m.coef, CHANNELS, log, t)


def query_times(log: EventLog, seed: int) -> dict[str, Floats]:
    """~500 uniform times, every event time, and every event time + ``EPS``."""
    rng = np.random.default_rng([seed, log.n])
    return {
        "random": rng.uniform(0.0, log.horizon, 500),
        "at_event": np.array(log.times),
        "after_event": np.array(log.times) + EPS,
    }


def check_pointwise(case_name: str, link: Link, n_target: int) -> None:
    case = CASE_BY_NAME[case_name]
    m = build(case, link)
    log = sample(case_name, link, n_target, 1)
    assert log.n > 0.3 * n_target, f"only {log.n} events"
    for where, t in query_times(log, 11).items():
        got = lambda_from_design(m, log, t)
        want = lambda_from_simulator(m, log, t)
        assert np.all(want > 0.0), where
        worst = float(np.max(np.abs(got - want) / want))
        assert worst <= RTOL_POINT, (
            f"{case_name} {link.value} {where}: rel err {worst:.3e}"
        )


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=True))
def test_pointwise_agreement(name: str, link: Link) -> None:
    check_pointwise(name, link, 300)


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=False))
def test_pointwise_agreement_long(name: str, link: Link) -> None:
    check_pointwise(name, link, 1000)


@pytest.mark.parametrize(
    ("name", "expected_jump"),
    [("kernel_exp", 0.5 * 2.0), ("kernel_power", 0.5 * (2.0 - 1.0) / 0.2)],
)
def test_event_time_is_a_left_limit(name: str, expected_jump: float) -> None:
    """At ``t = tᵢ`` event i is not history; at ``tᵢ + ε`` it is.

    Under the identity link the jump across an event is ``θ · kernel(0)``,
    known in closed form (``β`` for ExpK, ``(p - 1)/c`` for PowerK). Both
    implementations must show it, so neither counts an event at its own time.
    """
    case = CASE_BY_NAME[name]
    m = build(case, Link.IDENTITY)
    log = sample(name, Link.IDENTITY, 200, 4)
    at, after = np.array(log.times), np.array(log.times) + EPS
    for fn in (lambda_from_design, lambda_from_simulator):
        jump = fn(m, log, after) - fn(m, log, at)
        np.testing.assert_allclose(jump, expected_jump, rtol=1e-6)


def test_gate_switches_just_after_the_event() -> None:
    """``LastMarkAbove`` reads the last event strictly before t: the gate
    changes between ``tᵢ`` and ``tᵢ + ε`` in both implementations."""
    m = build(CASE_BY_NAME["gate_lastmark"], Link.IDENTITY)
    log = sample("gate_lastmark", Link.IDENTITY, 200, 4)
    at, after = np.array(log.times), np.array(log.times) + EPS
    for fn in (lambda_from_design, lambda_from_simulator):
        assert np.max(np.abs(fn(m, log, after) - fn(m, log, at))) > 0.1


# --------------------------------------------------------------------------
# 2. Time rescaling
# --------------------------------------------------------------------------


def rescaled_gaps(
    theta: Vec, structure: Structure, psi: PsiAssignment, log: EventLog
) -> Floats:
    """Compensator increments between consecutive events (and from 0)."""
    t = np.concatenate([[0.0], log.times])
    lam = compensator(theta, structure, psi, log, CHANNELS, structure.link, t)
    return np.diff(lam)


def constant_rate_model(log: EventLog, link: Link) -> tuple[Vec, Structure]:
    """A wrong model: no features, intercept at the MLE constant rate."""
    rate = log.n / log.horizon
    match link:
        case Link.IDENTITY:
            eta = rate
        case Link.EXP:
            eta = math.log(rate)
        case Link.SOFTPLUS:
            eta = math.log(math.expm1(rate))
    return np.array([eta]), Structure((), link)


def ks_pvalue(gaps: Floats) -> float:
    return float(stats.kstest(gaps, "expon").pvalue)


def check_rescaling(name: str, link: Link, n_target: int, *, control: bool) -> None:
    case = CASE_BY_NAME[name]
    m = build(case, link)
    log = sample(name, link, n_target, 2)
    gaps = rescaled_gaps(m.theta, m.structure, m.psi, log)
    assert gaps.size == log.n
    assert np.all(gaps > 0.0)
    p_true = ks_pvalue(gaps)
    assert p_true > KS_LEVEL, f"{name} {link.value}: KS p = {p_true:.2e}, n = {log.n}"
    if control:
        theta0, constant = constant_rate_model(log, link)
        p_constant = ks_pvalue(rescaled_gaps(theta0, constant, (), log))
        negated = np.array([m.theta[0], *(-m.theta[1:])])
        p_negated = ks_pvalue(rescaled_gaps(negated, m.structure, m.psi, log))
        assert min(p_constant, p_negated) < KS_LEVEL, (
            f"{name} {link.value}: no wrong compensator rejected "
            f"(constant rate p = {p_constant:.2e}, negated features p = "
            f"{p_negated:.2e}; true p = {p_true:.2e}, n = {log.n})"
        )


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=True))
def test_time_rescaling(name: str, link: Link) -> None:
    check_rescaling(name, link, 500, control=False)


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=False))
def test_time_rescaling_long(name: str, link: Link) -> None:
    check_rescaling(name, link, 3000, control=link not in CASE_BY_NAME[name].weak)


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=True))
@SLOW
def test_time_rescaling_long_fast_cases(name: str, link: Link) -> None:
    """The long run, with the positive control: a log with structure,
    rescaled by an intercept-only compensator, fails the KS test that the true
    compensator passes."""
    check_rescaling(name, link, 3000, control=link not in CASE_BY_NAME[name].weak)


# --------------------------------------------------------------------------
# 3. Log-likelihood against quadrature of the simulator's intensity
# --------------------------------------------------------------------------


def switch_times(
    windows: tuple[tuple[float, float], ...], horizon: float
) -> list[float]:
    """Times in ``(0, T)`` where ``sin(2πt/P - φ)`` changes sign: ``P(φ + kπ)/2π``."""
    out: list[float] = []
    for period, phase in windows:
        k = math.floor(-phase / math.pi)
        while (x := period * (phase + k * math.pi) / (2.0 * math.pi)) < horizon:
            if x > 0.0:
                out.append(x)
            k += 1
    return out


def quadrature_loglik(case: Case, m: Model, log: EventLog) -> tuple[float, float]:
    """``(Σ log λ(tᵢ), Λ(T))`` from ``simulate.intensity`` alone.

    ``λ`` is smooth between events (history is constant there) except where a
    ``PhaseWindow`` switches, so each piece between consecutive events and
    switches goes to adaptive ``quad``; an integration warning is an error.
    """

    def lam(x: float) -> float:
        return float(
            intensity(m.structure, m.psi, m.coef, CHANNELS, log, np.array([x]))[0]
        )

    at_events = intensity(m.structure, m.psi, m.coef, CHANNELS, log, log.times)
    edges = sorted(
        {0.0, log.horizon, *log.times, *switch_times(case.phase_windows, log.horizon)}
    )
    comp = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        parts = [
            integrate.quad(lam, a, b, epsabs=0.0, epsrel=1e-12, limit=200)[0]
            for a, b in pairwise(edges)
        ]
    comp = math.fsum(parts)
    return math.fsum(np.log(at_events)), comp


def check_loglik(name: str, link: Link, n_target: int) -> None:
    case = CASE_BY_NAME[name]
    m = build(case, link)
    log = sample(name, link, n_target, 3)
    assert log.n >= 10
    events, comp = quadrature_loglik(case, m, log)
    got = log_likelihood(m.theta, design(m.structure, m.psi, log, CHANNELS), link)
    assert got == pytest.approx(events - comp, rel=RTOL_LOGLIK), (
        f"{name} {link.value}: log L {got!r} vs {events - comp!r} "
        f"(Σ log λ {events!r}, Λ(T) {comp!r}, n = {log.n})"
    )
    top = compensator(m.theta, m.structure, m.psi, log, CHANNELS, link, [log.horizon])
    assert float(top[0]) == pytest.approx(comp, rel=RTOL_LOGLIK)


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=True))
def test_log_likelihood_matches_quadrature(name: str, link: Link) -> None:
    check_loglik(name, link, 30)


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=False))
def test_log_likelihood_matches_quadrature_long(name: str, link: Link) -> None:
    check_loglik(name, link, 80)


# --------------------------------------------------------------------------
# Positive controls for 1 and 3: the checks must be able to fail
# --------------------------------------------------------------------------


def next_on_grid(name: str, value: float) -> float:
    g = grid(name)
    return g[(g.index(value) + 1) % len(g)]


@pytest.mark.parametrize(
    "case", [c for c in CASES if any(c.psi_values)], ids=lambda c: c.name
)
def test_pointwise_check_detects_a_wrong_psi(case: Case) -> None:
    """Design columns evaluated at a neighbouring grid ψ disagree with the
    simulator's intensity by far more than ``RTOL_POINT`` (``Trend`` has no ψ
    to shift, so it is not a case here)."""
    link = Link.SOFTPLUS
    m = build(case, link)
    log = sample(case.name, link, 100, 1)
    shifted = tuple(
        {slot: next_on_grid(slot.name, v) for slot, v in mapping.items()}
        for mapping in m.psi
    )
    t = query_times(log, 11)["random"]
    want = lambda_from_simulator(m, log, t)
    wrong = lambda_from_design(Model(m.structure, shifted, m.coef, m.theta), log, t)
    assert float(np.max(np.abs(wrong - want) / want)) > 1e-3


@pytest.mark.parametrize(("name", "link"), grid_params(fast_cases=True))
def test_log_likelihood_check_detects_wrong_coefficients(name: str, link: Link) -> None:
    case = CASE_BY_NAME[name]
    m = build(case, link)
    log = sample(name, link, 30, 3)
    events, comp = quadrature_loglik(case, m, log)
    theta = np.array([m.theta[0], *(1.1 * m.theta[1:])])
    got = log_likelihood(theta, design(m.structure, m.psi, log, CHANNELS), link)
    assert abs(got - (events - comp)) > 1e-3
