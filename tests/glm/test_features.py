"""Instrument tests for feature precomputation (SPEC §2.2, §6.3 test 1).

The design matrix is what every likelihood, fit and score reads, so each
feature is checked against an independent **brute-force** evaluation: a pure
Python double loop over events written straight from grammar.py's docstring,
sharing no code with ``features.py``. Column integrals are checked against
adaptive ``scipy.integrate.quad`` of that brute-force function over every
piece on which it is smooth (between events and window switches).

The brute-force helpers here are also imported by ``test_likelihood.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from itertools import pairwise

import numpy as np
import pytest
from scipy import integrate

from sciagent.core.errors import OffGridParameterError, SciAgentError
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.features import (
    DEFAULT_QUADRATURE,
    FeatureDataError,
    FeatureNumericsError,
    PsiAssignmentError,
    QuadratureSpec,
    assemble,
    design,
    evaluate_columns,
    feature_block,
    kernel_cdf,
    kernel_pdf,
    quadrature_rule,
)
from sciagent.glm.grammar import (
    ALL,
    KERNEL_PSI,
    Above,
    ChannelKind,
    ChannelSpec,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
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
    Trend,
    psi_slots,
)
from sciagent.glm.grids import grid

CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec("size", ChannelKind.POSITIVE, 1.0, 0.5),
    ChannelSpec("sign", ChannelKind.SIGN, 0.0, 1.0),
    ChannelSpec("mag", ChannelKind.REAL, 3.0, 1.0),
)
_SPEC = {c.name: c for c in CHANNELS}

#: One on-grid value per ψ name, used unless a test overrides it.
PICK: dict[str, float] = {
    "exp_rate": 2.0,
    "power_c": 0.2,
    "power_p": 1.5,
    "gamma_shape": 3.0,
    "gamma_mean": 1.0,
    "pow_exponent": 1.5,
    "exp_coef": 0.5,
    "above_z": 0.5,
    "period": 5.0,
    "phase": 0.5 * math.pi,
}
#: The sharpest kernels on the grid: the hardest case for quadrature.
SHARP: dict[str, float] = {
    **PICK,
    "exp_rate": 8.0,
    "power_c": 0.05,
    "gamma_mean": 0.5,
    "gamma_shape": 5.0,
}


def psi_for(feature: Feature, pick: Mapping[str, float] = PICK) -> dict[PsiSlot, float]:
    return {slot: pick[slot.name] for slot in psi_slots(feature)}


def small_log(seed: int = 7, n: int = 12, horizon: float = 15.0) -> EventLog:
    """A small marked log with clustered and isolated events."""
    rng = np.random.default_rng(seed)
    times = np.sort(rng.uniform(0.0, horizon, size=n))
    # Force a tight cluster so sharp kernels overlap.
    times[3] = times[2] + 0.01
    times = np.sort(times)
    return EventLog.create(
        times,
        {
            "size": rng.lognormal(0.0, 0.5, size=n),
            "sign": rng.choice([-1.0, 1.0], size=n),
            "mag": 3.0 + rng.normal(0.0, 1.0, size=n),
        },
        horizon,
    )


# --------------------------------------------------------------------------
# Brute force: pure Python, written from grammar.py's docstring alone
# --------------------------------------------------------------------------


def brute_kernel(kind: KernelKind, psi: Mapping[str, float], x: float) -> float:
    if x <= 0.0:
        return 0.0
    match kind:
        case KernelKind.EXP:
            b = psi["exp_rate"]
            return float(b * math.exp(-b * x))
        case KernelKind.POWER:
            c, p = psi["power_c"], psi["power_p"]
            return float((p - 1.0) / c * (1.0 + x / c) ** (-p))
        case KernelKind.GAMMA:
            k, mu = psi["gamma_shape"], psi["gamma_mean"]
            rate = k / mu
            return float(rate**k * x ** (k - 1.0) * math.exp(-rate * x) / math.gamma(k))


def brute_mark(mark: MarkFn, psi: Mapping[str, float], log: EventLog, j: int) -> float:
    match mark:
        case One():
            return 1.0
        case Mark(channel=c):
            return float(log.marks[c][j])
        case Pow(channel=c):
            m = float(log.marks[c][j])
            return float((m / _SPEC[c].location) ** psi["pow_exponent"])
        case ExpOf(channel=c):
            z = (float(log.marks[c][j]) - _SPEC[c].location) / _SPEC[c].scale
            return math.exp(psi["exp_coef"] * z)
        case Above(channel=c):
            z = (float(log.marks[c][j]) - _SPEC[c].location) / _SPEC[c].scale
            return 1.0 if z > psi["above_z"] else 0.0


def _local(slots: Mapping[PsiSlot, float], path: tuple[int, ...]) -> dict[str, float]:
    return {s.name: v for s, v in slots.items() if s.path == path}


def brute_columns(
    feature: Feature,
    slots: Mapping[PsiSlot, float],
    log: EventLog,
    t: float,
    path: tuple[int, ...] = (),
) -> list[float]:
    """Every column of ``feature`` at time ``t`` (left limit), by direct loops."""
    times = [float(x) for x in log.times]
    match feature:
        case Excite(kernel=kernel, mark=mark, source=source):
            kpsi = _local(slots, (*path, 0))
            mpsi = _local(slots, (*path, 1))
            total = 0.0
            for j, tj in enumerate(times):
                if not tj < t:
                    continue
                if source.kind is not SourceKind.ALL:
                    assert source.channel is not None
                    s = float(log.marks[source.channel][j])
                    want = 1.0 if source.kind is SourceKind.POSITIVE else -1.0
                    if s != want:
                        continue
                total += brute_mark(mark, mpsi, log, j) * brute_kernel(
                    kernel, kpsi, t - tj
                )
            return [total]
        case Periodic():
            p = slots[PsiSlot(path, "period")]
            return [math.sin(2 * math.pi * t / p), math.cos(2 * math.pi * t / p)]
        case Trend():
            return [t / log.horizon]
        case Product(left=left, right=right):
            a = brute_columns(left, slots, log, t, (*path, 0))
            b = brute_columns(right, slots, log, t, (*path, 1))
            return [x * y for x in a for y in b]
        case Gate(feature=inner, cond=cond):
            cols = brute_columns(inner, slots, log, t, (*path, 0))
            cpsi = _local(slots, (*path, 1))
            match cond:
                case LastMarkAbove(channel=c):
                    before = [j for j, tj in enumerate(times) if tj < t]
                    if before:
                        j = before[-1]
                        z = (float(log.marks[c][j]) - _SPEC[c].location) / _SPEC[
                            c
                        ].scale
                        on = z > cpsi["above_z"]
                    else:
                        on = False
                case PhaseWindow():
                    on = (
                        math.sin(2 * math.pi * t / cpsi["period"] - cpsi["phase"])
                        >= 0.0
                    )
            return cols if on else [0.0] * len(cols)


def phase_switches(
    feature: Feature, slots: Mapping[PsiSlot, float], horizon: float
) -> list[float]:
    """PhaseWindow switches and Periodic quarter points in (0, horizon).

    Used as extra ``quad`` breakpoints."""
    out: list[float] = []
    for slot, value in slots.items():
        if slot.name == "period":
            for k in range(int(4 * horizon / value) + 4):
                t = value * k / 4.0
                if 0.0 < t < horizon:
                    out.append(t)
                phase = slots.get(PsiSlot(slot.path, "phase"))
                if phase is not None:
                    t = value * (k / 2.0 + phase / (2 * math.pi))
                    if 0.0 < t < horizon:
                        out.append(t)
    return out


def pieces(log: EventLog, extra: list[float]) -> list[tuple[float, float]]:
    cuts = sorted({0.0, log.horizon, *map(float, log.times), *extra})
    return [(a, b) for a, b in pairwise(cuts) if b > a]


def quad_pieces(f: Callable[[float], float], parts: list[tuple[float, float]]) -> float:
    total = 0.0
    for a, b in parts:
        value, _ = integrate.quad(f, a, b, epsabs=1e-14, epsrel=1e-13, limit=400)
        total += value
    return total


# --------------------------------------------------------------------------
# The feature catalogue every comparison runs over
# --------------------------------------------------------------------------

POS = Source(SourceKind.POSITIVE, "sign")
NEG = Source(SourceKind.NEGATIVE, "sign")
MARKS: list[MarkFn] = [
    One(),
    Mark("size"),
    Pow("size"),
    ExpOf("mag"),
    Above("mag"),
    Mark("sign"),
]

CATALOGUE: list[Feature] = [
    *(Excite(k, m, ALL) for k in KernelKind for m in MARKS),
    Excite(KernelKind.EXP, One(), POS),
    Excite(KernelKind.POWER, Mark("size"), NEG),
    Excite(KernelKind.GAMMA, ExpOf("mag"), POS),
    Periodic(),
    Trend(),
    Product(Excite(KernelKind.EXP, One(), ALL), Periodic()),
    Product(Periodic(), Periodic()),
    Product(
        Excite(KernelKind.POWER, Mark("size"), ALL),
        Excite(KernelKind.GAMMA, One(), ALL),
    ),
    Product(Trend(), Excite(KernelKind.EXP, Above("mag"), ALL)),
    Gate(Excite(KernelKind.EXP, One(), ALL), LastMarkAbove("mag")),
    Gate(Excite(KernelKind.POWER, Mark("size"), ALL), LastMarkAbove("size")),
    Gate(Excite(KernelKind.GAMMA, One(), NEG), PhaseWindow()),
    Gate(Periodic(), PhaseWindow()),
    Gate(Trend(), LastMarkAbove("mag")),
    Gate(Gate(Excite(KernelKind.EXP, One(), ALL), LastMarkAbove("mag")), PhaseWindow()),
    Gate(Product(Excite(KernelKind.EXP, One(), ALL), Periodic()), LastMarkAbove("mag")),
    Product(Gate(Excite(KernelKind.POWER, One(), ALL), PhaseWindow()), Trend()),
]


def _closed_form(feature: Feature) -> bool:
    match feature:
        case Excite() | Periodic() | Trend():
            return True
        case Gate(feature=inner):
            return _closed_form(inner)
        case Product():
            return False


def _id(feature: Feature) -> str:
    return repr(feature)


# --------------------------------------------------------------------------
# Kernels
# --------------------------------------------------------------------------


def _kernel_grid(kind: KernelKind) -> list[dict[str, float]]:
    names = KERNEL_PSI[kind]
    combos: list[dict[str, float]] = [{}]
    for name in names:
        combos = [{**c, name: v} for c in combos for v in grid(name)]
    return combos


@pytest.mark.parametrize("kind", list(KernelKind))
def test_every_grid_kernel_is_a_normalised_density(kind: KernelKind) -> None:
    for psi in _kernel_grid(kind):

        def f(x: float, psi: dict[str, float] = psi) -> float:
            return float(kernel_pdf(kind, psi, np.array([x]))[0])

        total = 0.0
        for a, b in [(0.0, 1.0), (1.0, 100.0), (100.0, math.inf)]:
            total += integrate.quad(f, a, b, epsabs=1e-13, epsrel=1e-12, limit=400)[0]
        assert total == pytest.approx(1.0, abs=1e-8), psi


@pytest.mark.parametrize("kind", list(KernelKind))
def test_kernel_matches_brute_force_and_cdf_integrates_it(kind: KernelKind) -> None:
    xs = np.array([1e-6, 0.01, 0.3, 1.0, 2.5, 10.0, 80.0])
    for psi in _kernel_grid(kind):
        pdf = kernel_pdf(kind, psi, xs)
        cdf = kernel_cdf(kind, psi, xs)

        def ref_pdf(u: float, psi: dict[str, float] = psi) -> float:
            return brute_kernel(kind, psi, u)

        for i, x in enumerate(xs):
            assert pdf[i] == pytest.approx(brute_kernel(kind, psi, float(x)), rel=1e-12)
            ref = integrate.quad(
                ref_pdf,
                0.0,
                float(x),
                epsabs=1e-14,
                epsrel=1e-12,
                limit=400,
            )[0]
            assert cdf[i] == pytest.approx(ref, rel=1e-9, abs=1e-13)


# --------------------------------------------------------------------------
# Columns against brute force
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pick", [PICK, SHARP], ids=["typical", "sharp"])
@pytest.mark.parametrize("feature", CATALOGUE, ids=_id)
def test_columns_at_events_match_brute_force(
    feature: Feature, pick: dict[str, float]
) -> None:
    log = small_log()
    psi = psi_for(feature, pick)
    d = design(Structure((feature,)), (psi,), log, CHANNELS)
    cols = d.feature_columns[0]
    assert d.at_events.shape == (log.n, 1 + len(cols))
    assert np.all(d.at_events[:, 0] == 1.0)
    for i, t in enumerate(log.times):
        want = brute_columns(feature, psi, log, float(t))
        np.testing.assert_allclose(
            d.at_events[i, list(cols)], want, rtol=1e-11, atol=1e-13
        )


@pytest.mark.parametrize("pick", [PICK, SHARP], ids=["typical", "sharp"])
@pytest.mark.parametrize("feature", CATALOGUE, ids=_id)
def test_column_integrals_match_quad(feature: Feature, pick: dict[str, float]) -> None:
    log = small_log()
    psi = psi_for(feature, pick)
    d = design(Structure((feature,)), (psi,), log, CHANNELS)
    parts = pieces(log, phase_switches(feature, psi, log.horizon))
    assert d.integrals[0] == log.horizon
    exact = _closed_form(feature)
    for local, col in enumerate(d.feature_columns[0]):
        assert bool(d.integral_exact[col]) is exact

        def f(t: float, local: int = local) -> float:
            return brute_columns(feature, psi, log, t)[local]

        ref = quad_pieces(f, parts)
        tol = 1e-10 if exact else 1e-7
        assert d.integrals[col] == pytest.approx(ref, rel=tol, abs=tol), local


@pytest.mark.parametrize("feature", CATALOGUE, ids=_id)
def test_evaluate_columns_matches_brute_force_and_the_design(feature: Feature) -> None:
    log = small_log()
    psi = psi_for(feature)
    structure = Structure((feature,))
    d = design(structure, (psi,), log, CHANNELS)
    # Unsorted query times, including event times exactly and the horizon.
    t = np.array(
        [7.3, float(log.times[4]), 0.0, 1e-3, log.horizon, float(log.times[0]), 2.2]
    )
    got = evaluate_columns(structure, (psi,), log, CHANNELS, t)
    for i, ti in enumerate(t):
        want = [1.0, *brute_columns(feature, psi, log, float(ti))]
        np.testing.assert_allclose(got[i], want, rtol=1e-11, atol=1e-13)
    again = evaluate_columns(structure, (psi,), log, CHANNELS, d.nodes)
    np.testing.assert_allclose(again, d.at_nodes, rtol=1e-12, atol=1e-14)


def test_multi_feature_layout_and_labels() -> None:
    log = small_log()
    features: tuple[Feature, ...] = (
        Excite(KernelKind.EXP, One(), ALL),
        Periodic(),
        Product(Periodic(), Periodic()),
    )
    psi = tuple(psi_for(f) for f in features)
    d = design(Structure(features), psi, log, CHANNELS)
    assert d.feature_columns == ((1,), (2, 3), (4, 5, 6, 7))
    assert d.labels[0] == "θ0"
    assert len(d.labels) == 8
    assert len(set(d.labels)) == 8
    for k, f in enumerate(features):
        single = design(Structure((f,)), (psi[k],), log, CHANNELS)
        np.testing.assert_array_equal(
            d.at_events[:, list(d.feature_columns[k])],
            single.at_events[:, list(single.feature_columns[0])],
        )


# --------------------------------------------------------------------------
# Quadrature rule
# --------------------------------------------------------------------------


def test_quadrature_rule_is_a_partition_of_the_horizon() -> None:
    log = small_log()
    feature = Gate(Excite(KernelKind.POWER, One(), ALL), PhaseWindow())
    psi = psi_for(feature, SHARP)
    d = design(Structure((feature,)), (psi,), log, CHANNELS)
    assert np.all(np.diff(d.nodes) > 0)
    assert d.nodes[0] > 0.0
    assert d.nodes[-1] < log.horizon
    assert np.all(d.weights > 0)
    assert float(np.sum(d.weights)) == pytest.approx(log.horizon, rel=1e-13)
    # No event inside the open support of any panel: nodes avoid events.
    assert not np.any(np.isin(d.nodes, log.times))
    assert d.at_nodes.shape == (d.nodes.size, 2)


def test_quadrature_spec_validation() -> None:
    with pytest.raises(SciAgentError):
        QuadratureSpec(nodes_per_panel=0)
    with pytest.raises(SciAgentError):
        QuadratureSpec(growth=1.0)
    with pytest.raises(SciAgentError):
        QuadratureSpec(max_panel=0.0)
    assert DEFAULT_QUADRATURE.nodes_per_panel >= 2


def test_empty_log() -> None:
    log = EventLog.create([], {"size": [], "sign": [], "mag": []}, 10.0)
    feature = Product(Excite(KernelKind.POWER, One(), ALL), Periodic())
    psi = psi_for(feature)
    d = design(Structure((feature, Trend())), (psi, {}), log, CHANNELS)
    assert d.at_events.shape == (0, 4)
    assert float(np.sum(d.weights)) == pytest.approx(10.0)
    assert np.all(d.at_nodes[:, 1:3] == 0.0)
    assert d.integrals[3] == pytest.approx(5.0)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_design_is_byte_identical_across_calls() -> None:
    log = small_log(seed=11, n=30, horizon=25.0)
    features: tuple[Feature, ...] = (
        Excite(KernelKind.POWER, ExpOf("mag"), ALL),
        Gate(Excite(KernelKind.EXP, Mark("size"), POS), LastMarkAbove("mag")),
        Product(Excite(KernelKind.GAMMA, One(), ALL), Periodic()),
    )
    psi = tuple(psi_for(f) for f in features)
    a = design(Structure(features), psi, log, CHANNELS)
    b = design(Structure(features), psi, log, CHANNELS)
    for name in (
        "at_events",
        "integrals",
        "nodes",
        "weights",
        "at_nodes",
        "integral_exact",
    ):
        x, y = getattr(a, name), getattr(b, name)
        assert x.tobytes() == y.tobytes(), name
    assert a.labels == b.labels
    assert not a.at_events.flags.writeable


# --------------------------------------------------------------------------
# ψ validation and numerical guards
# --------------------------------------------------------------------------


def test_missing_slot_is_rejected() -> None:
    feature = Excite(KernelKind.POWER, One(), ALL)
    psi = psi_for(feature)
    del psi[PsiSlot((0,), "power_p")]
    with pytest.raises(PsiAssignmentError):
        design(Structure((feature,)), (psi,), small_log(), CHANNELS)


def test_extra_slot_is_rejected() -> None:
    feature = Excite(KernelKind.EXP, One(), ALL)
    psi = psi_for(feature)
    psi[PsiSlot((1,), "exp_coef")] = 1.0
    with pytest.raises(PsiAssignmentError):
        design(Structure((feature,)), (psi,), small_log(), CHANNELS)


def test_wrong_number_of_assignments_is_rejected() -> None:
    feature = Trend()
    with pytest.raises(PsiAssignmentError):
        design(Structure((feature,)), ({}, {}), small_log(), CHANNELS)


def test_off_grid_value_is_rejected_unless_allowed() -> None:
    feature = Excite(KernelKind.EXP, One(), ALL)
    psi = {PsiSlot((0,), "exp_rate"): 1.7}
    log = small_log()
    with pytest.raises(OffGridParameterError):
        design(Structure((feature,)), (psi,), log, CHANNELS)
    d = design(Structure((feature,)), (psi,), log, CHANNELS, allow_off_grid=True)
    want = brute_columns(feature, psi, log, float(log.times[5]))
    assert d.at_events[5, 1] == pytest.approx(want[0], rel=1e-12)
    with pytest.raises(OffGridParameterError):
        evaluate_columns(Structure((feature,)), (psi,), log, CHANNELS, np.array([1.0]))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("exp_rate", 0.0),
        ("power_p", 1.0),
        ("power_c", -1.0),
        ("period", 0.0),
        ("gamma_shape", math.nan),
    ],
)
def test_out_of_domain_value_is_rejected_even_off_grid(name: str, value: float) -> None:
    feature: Feature
    if name == "period":
        feature = Periodic()
    elif name.startswith("gamma"):
        feature = Excite(KernelKind.GAMMA, One(), ALL)
    elif name.startswith("power"):
        feature = Excite(KernelKind.POWER, One(), ALL)
    else:
        feature = Excite(KernelKind.EXP, One(), ALL)
    psi = psi_for(feature)
    key = next(s for s in psi if s.name == name)
    psi[key] = value
    with pytest.raises(PsiAssignmentError):
        design(
            Structure((feature,)), (psi,), small_log(), CHANNELS, allow_off_grid=True
        )


def test_exp_of_overflow_raises_a_typed_error() -> None:
    log = EventLog.create(
        [1.0, 2.0], {"size": [1.0, 1.0], "sign": [1.0, 1.0], "mag": [3.0, 400.0]}, 5.0
    )
    feature = Excite(KernelKind.EXP, ExpOf("mag"), ALL)
    psi = {PsiSlot((0,), "exp_rate"): 1.0, PsiSlot((1,), "exp_coef"): 2.5}
    with pytest.raises(FeatureNumericsError):
        design(Structure((feature,)), (psi,), log, CHANNELS)


def test_sign_channel_must_hold_plus_minus_one() -> None:
    log = EventLog.create(
        [1.0, 2.0], {"size": [1.0, 1.0], "sign": [1.0, 0.5], "mag": [3.0, 3.0]}, 5.0
    )
    feature = Excite(KernelKind.EXP, One(), POS)
    with pytest.raises(FeatureDataError):
        design(Structure((feature,)), (psi_for(feature),), log, CHANNELS)


def test_pow_needs_positive_marks() -> None:
    log = EventLog.create(
        [1.0, 2.0], {"size": [1.0, -0.5], "sign": [1.0, 1.0], "mag": [3.0, 3.0]}, 5.0
    )
    feature = Excite(KernelKind.EXP, Pow("size"), ALL)
    with pytest.raises(FeatureDataError):
        design(Structure((feature,)), (psi_for(feature),), log, CHANNELS)


def test_missing_mark_channel_is_rejected() -> None:
    log = EventLog.create([1.0, 2.0], {"size": [1.0, 1.0]}, 5.0)
    feature = Excite(KernelKind.EXP, ExpOf("mag"), ALL)
    with pytest.raises(FeatureDataError):
        design(Structure((feature,)), (psi_for(feature),), log, CHANNELS)


# --------------------------------------------------------------------------
# The rule is shared across ψ; blocks assemble into the same design
# --------------------------------------------------------------------------


def test_rule_depends_on_structure_not_on_grid_psi() -> None:
    log = small_log()
    features: tuple[Feature, ...] = (
        Excite(KernelKind.POWER, One(), ALL),
        Gate(Periodic(), PhaseWindow()),
    )
    a = design(
        Structure(features), tuple(psi_for(f, PICK) for f in features), log, CHANNELS
    )
    b = design(
        Structure(features), tuple(psi_for(f, SHARP) for f in features), log, CHANNELS
    )
    assert a.nodes.tobytes() == b.nodes.tobytes()
    assert a.weights.tobytes() == b.weights.tobytes()
    nodes, weights = quadrature_rule(Structure(features), log)
    assert nodes.tobytes() == a.nodes.tobytes()
    assert weights.tobytes() == a.weights.tobytes()


def test_blocks_assemble_into_the_design() -> None:
    log = small_log(seed=2, n=25, horizon=20.0)
    features: tuple[Feature, ...] = (
        Excite(KernelKind.GAMMA, Mark("size"), ALL),
        Product(Excite(KernelKind.EXP, One(), NEG), Periodic()),
    )
    psi = tuple(psi_for(f) for f in features)
    structure = Structure(features)
    whole = design(structure, psi, log, CHANNELS)
    nodes, weights = quadrature_rule(structure, log)
    blocks = tuple(
        feature_block(f, s, log, CHANNELS, nodes, weights)
        for f, s in zip(features, psi, strict=True)
    )
    built = assemble(structure, blocks, log, nodes, weights)
    for name in (
        "at_events",
        "integrals",
        "nodes",
        "weights",
        "at_nodes",
        "integral_exact",
    ):
        assert getattr(built, name).tobytes() == getattr(whole, name).tobytes(), name
    assert built.labels == whole.labels
    with pytest.raises(FeatureDataError):
        assemble(structure, blocks[:1], log, nodes, weights)


# --------------------------------------------------------------------------
# Recursions against direct sums on a longer log
# --------------------------------------------------------------------------


def _direct_reference(
    kind: KernelKind, psi: dict[str, float], log: EventLog, cdf: bool
) -> list[float]:
    """Pure-Python Σ_{j<i} f(tᵢ - tⱼ) (or F for the CDF at T) at every event."""
    times = [float(x) for x in log.times]
    if cdf:
        return [
            math.fsum(
                float(kernel_cdf(kind, psi, np.array([log.horizon - tj]))[0])
                for tj in times
            )
        ]
    return [
        math.fsum(brute_kernel(kind, psi, ti - tj) for tj in times[:i])
        for i, ti in enumerate(times)
    ]


@pytest.mark.parametrize(
    ("kind", "psi"),
    [
        (KernelKind.EXP, {"exp_rate": 8.0}),
        (KernelKind.GAMMA, {"gamma_shape": 5.0, "gamma_mean": 0.5}),
        (KernelKind.GAMMA, {"gamma_shape": 2.0, "gamma_mean": 4.0}),
        (KernelKind.GAMMA, {"gamma_shape": 2.5, "gamma_mean": 1.0}),  # direct path
        (KernelKind.POWER, {"power_c": 0.05, "power_p": 1.2}),
    ],
)
def test_history_sums_on_a_long_log(kind: KernelKind, psi: dict[str, float]) -> None:
    rng = np.random.default_rng(13)
    times = np.cumsum(rng.exponential(0.5, size=300))
    log = EventLog.create(times, {}, float(times[-1]) + 2.0)
    feature = Excite(kind, One(), ALL)
    slots = {PsiSlot((0,), name): value for name, value in psi.items()}
    d = design(Structure((feature,)), (slots,), log, (), allow_off_grid=True)
    np.testing.assert_allclose(
        d.at_events[:, 1],
        _direct_reference(kind, psi, log, False),
        rtol=1e-10,
        atol=1e-12,
    )
    assert d.integrals[1] == pytest.approx(
        _direct_reference(kind, psi, log, True)[0], rel=1e-12
    )


# --------------------------------------------------------------------------
# Interventional data: forced events and excluded windows
# --------------------------------------------------------------------------

EXCLUDED: tuple[tuple[float, float], ...] = ((2.0, 3.5), (9.0, 11.0))


def small_dataset() -> Dataset:
    log = small_log()
    endogenous = np.ones(log.n, dtype=np.bool_)
    endogenous[[1, 6]] = False  # forced: history only
    return Dataset.create(log, endogenous, EXCLUDED, "test")


def complement_pieces(log: EventLog, extra: list[float]) -> list[tuple[float, float]]:
    """Smooth pieces of [0, T] minus the excluded windows."""
    cuts = [a for w in EXCLUDED for a in w]
    return [
        (a, b)
        for a, b in pieces(log, [*extra, *cuts])
        if not any(lo <= a and b <= hi for lo, hi in EXCLUDED)
    ]


@pytest.mark.parametrize(
    "feature",
    [
        Excite(KernelKind.POWER, Mark("size"), ALL),
        Gate(Excite(KernelKind.EXP, One(), ALL), LastMarkAbove("mag")),
        Gate(Periodic(), PhaseWindow()),
        Product(Excite(KernelKind.GAMMA, One(), ALL), Trend()),
    ],
    ids=_id,
)
def test_dataset_design_matches_brute_force(feature: Feature) -> None:
    data = small_dataset()
    log = data.log
    psi = psi_for(feature)
    d = design(Structure((feature,)), (psi,), data, CHANNELS)
    inside = [any(lo <= float(t) <= hi for lo, hi in EXCLUDED) for t in log.times]
    want_rows = [i for i in range(log.n) if data.endogenous[i] and not inside[i]]
    assert d.event_index.tolist() == want_rows
    for row, i in enumerate(want_rows):
        # Forced events stay in the history of every later event.
        want = brute_columns(feature, psi, log, float(log.times[i]))
        np.testing.assert_allclose(d.at_events[row, 1:], want, rtol=1e-11, atol=1e-13)
    span = log.horizon - sum(hi - lo for lo, hi in EXCLUDED)
    assert d.integrals[0] == pytest.approx(span, rel=1e-15)
    assert float(np.sum(d.weights)) == pytest.approx(span, rel=1e-13)
    assert not np.any([any(lo <= x <= hi for lo, hi in EXCLUDED) for x in d.nodes])
    parts = complement_pieces(log, phase_switches(feature, psi, log.horizon))
    for local, col in enumerate(d.feature_columns[0]):

        def f(t: float, local: int = local) -> float:
            return brute_columns(feature, psi, log, t)[local]

        tol = 1e-10 if d.integral_exact[col] else 1e-7
        assert d.integrals[col] == pytest.approx(
            quad_pieces(f, parts), rel=tol, abs=tol
        )


def test_observational_dataset_equals_the_bare_log() -> None:
    log = small_log()
    feature = Gate(Excite(KernelKind.POWER, One(), ALL), PhaseWindow())
    psi = (psi_for(feature),)
    a = design(Structure((feature,)), psi, log, CHANNELS)
    b = design(Structure((feature,)), psi, Dataset.observational(log), CHANNELS)
    for name in (
        "at_events",
        "integrals",
        "nodes",
        "weights",
        "at_nodes",
        "event_index",
    ):
        assert getattr(a, name).tobytes() == getattr(b, name).tobytes(), name


@pytest.mark.parametrize("kind", [KernelKind.POWER, KernelKind.GAMMA])
def test_single_event_column_is_exactly_the_kernel(kind: KernelKind) -> None:
    """The fast paths reproduce ``kernel_pdf`` / ``kernel_cdf`` bit for bit."""
    log = EventLog.create([1.0], {}, 50.0)
    feature = Excite(kind, One(), ALL)
    psi = {PsiSlot((0,), name): grid(name)[0] for name in KERNEL_PSI[kind]}
    params = {s.name: v for s, v in psi.items()}
    t = np.array([0.5, 1.0, 1.001, 1.3, 4.0, 49.0])
    got = evaluate_columns(Structure((feature,)), (psi,), log, (), t)[:, 1]
    want = kernel_pdf(kind, params, t - 1.0)
    if kind is KernelKind.POWER:
        assert got.tobytes() == want.tobytes()
    else:  # the moment recursion is algebraically, not bitwise, equal
        np.testing.assert_allclose(got, want, rtol=1e-13, atol=1e-300)
    d = design(Structure((feature,)), (psi,), log, ())
    assert d.integrals[1] == pytest.approx(
        float(kernel_cdf(kind, params, np.array([49.0]))[0]), rel=1e-14
    )
