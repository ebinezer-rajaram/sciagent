"""Instrument tests for the v2 diagnostic catalogue (SPEC §4.0 ``diagnostic``).

Diagnostics produce numbers the evaluation reads (predictive p-values, the
telling diagnostic of §4.2, committed predictions of §4.4), so they are
instruments (§6.3). Checked here: hand-computed values on tiny logs, typed
errors on degenerate input, the argument schema, known-process behaviour, the
time-rescaling covariance each diagnostic declares, and determinism.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sciagent.diagnostics.catalogue import (
    CATALOGUE,
    ArgKind,
    Covariance,
    DiagnosticArgumentError,
    DiagnosticError,
    InsufficientDataError,
    UnknownDiagnosticError,
    args_schema,
    compute,
    mean_gap,
    names,
    rescale_args,
    resolve_args,
    transform_value,
)
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    KernelKind,
    Link,
    Mark,
    One,
    Periodic,
    PsiSlot,
    Structure,
)
from sciagent.glm.simulate import Coefficients, simulate

CHANNELS = (
    ChannelSpec("a", ChannelKind.SIGN, 0.0, 1.0),
    ChannelSpec("b", ChannelKind.POSITIVE, 1.0, 1.0),
)

FORBIDDEN = ("earthquake", "seismic", "hawkes", "etas", "aftershock", "omori")


def tiny() -> EventLog:
    return EventLog.create(
        [1.0, 2.0, 4.0, 7.0], {"a": [1, -1, 1, -1], "b": [1, 2, 3, 4]}, 8.0
    )


def clumped() -> EventLog:
    return EventLog.create(
        [0.5, 1.0, 1.5, 5.0], {"a": [1, 1, -1, -1], "b": [1, 1, 1, 1]}, 8.0
    )


def value(name: str, log: EventLog, **args: float | int | str) -> float:
    return compute(name, log, CHANNELS, args)


# --------------------------------------------------------------------------
# Hand-computed values
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("mean_rate", {}, 0.5),
        ("inter_arrival_dispersion", {}, 0.25),
        ("burstiness", {}, -1.0 / 3.0),
        ("mark_mean", {"channel": "b"}, 2.5),
        ("mark_dispersion", {"channel": "b"}, (5.0 / 3.0) / 6.25),
        ("mark_skewness", {"channel": "b"}, 0.0),
        ("mark_autocorrelation", {"channel": "a", "lag": 1}, -0.75),
        ("mark_gap_correlation", {"channel": "b"}, 1.0),
        ("next_gap_after_large_mark", {"channel": "b", "quantile": 0.5}, 2.0),
        ("post_event_rate_ratio", {"window": 2.0}, 2.0 / 3.0),
        (
            "pair_clustering_ratio",
            {"scale": 1.5},
            2.0 / (12.0 * (2 * 1.5 / 8 - (1.5 / 8) ** 2)),
        ),
        ("uniformity_ks", {}, 0.25),
        ("gap_autocorrelation", {"lag": 1}, 0.0),
        ("mark_cross_correlation", {"channel": "a", "other": "b", "lag": 0}, None),
    ],
)
def test_hand_computed_values_on_a_tiny_log(
    name: str, args: Mapping[str, float | int | str], expected: float | None
) -> None:
    got = compute(name, tiny(), CHANNELS, args)
    if expected is None:
        a = np.array([1.0, -1.0, 1.0, -1.0])
        b = np.array([1.0, 2.0, 3.0, 4.0])
        expected = float(np.corrcoef(a, b)[0, 1])
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_log_gap_cv_by_hand() -> None:
    gaps = np.array([1.0, 2.0, 3.0]) / 2.0
    logs = np.log(gaps)
    expected = float(np.std(logs, ddof=1) / abs(np.mean(logs)))
    assert value("log_gap_cv", tiny()) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("fano_factor", {"window": 2.0}, 2.0),
        ("count_autocorrelation", {"window": 2.0, "lag": 1}, -1.0 / 3.0),
        ("count_trend_slope", {"window": 2.0}, -3.2),
        ("mean_high_run_length", {"window": 2.0}, 1.0),
    ],
)
def test_windowed_counts_by_hand(
    name: str, args: Mapping[str, float | int | str], expected: float
) -> None:
    # Windows of width 2 on [0, 8): counts [3, 0, 1, 0].
    assert compute(name, clumped(), CHANNELS, args) == pytest.approx(expected)


def test_windows_tile_the_horizon_not_the_last_event() -> None:
    # Last event at 1.5 but horizon 8: four windows of width 2, not zero.
    log = EventLog.create([0.5, 1.0, 1.5], {"a": [1, 1, 1], "b": [1, 1, 1]}, 8.0)
    # counts [3, 0, 0, 0]: mean 0.75, var 2.25 → Fano 3.
    assert value("fano_factor", log, window=2.0) == pytest.approx(3.0)


def test_mean_rate_uses_the_horizon() -> None:
    log = EventLog.create([0.1, 0.2], {"a": [1, 1], "b": [1, 1]}, 10.0)
    assert value("mean_rate", log) == pytest.approx(0.2)


def test_mean_gap_is_horizon_over_count() -> None:
    assert mean_gap(tiny()) == 2.0


def test_spectral_power_at_period_matches_a_direct_dft() -> None:
    log = clumped()
    counts = np.array([3.0, 0.0, 1.0, 0.0])  # bin width 2 on [0, 8)
    centred = counts - counts.mean()
    k = np.arange(4)
    period = 4.0
    angle = 2 * math.pi * k * 2.0 / period
    power = (
        np.dot(centred, np.cos(angle)) ** 2 + np.dot(centred, np.sin(angle)) ** 2
    ) / 4
    expected = power / np.var(counts, ddof=1)
    got = value("spectral_power_at_period", log, period=period, bin_width=2.0)
    assert got == pytest.approx(expected)


# --------------------------------------------------------------------------
# Degenerate input
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", names())
def test_every_diagnostic_raises_typed_on_an_empty_log(name: str) -> None:
    log = EventLog.create([], {"a": [], "b": []}, 5.0)
    args = minimal_args(name, log)
    with pytest.raises(InsufficientDataError):
        compute(name, log, CHANNELS, args)


@pytest.mark.parametrize("name", names())
def test_every_diagnostic_is_finite_or_typed_on_constant_input(name: str) -> None:
    """Evenly spaced events with constant marks: zero variance everywhere."""
    n = 64
    log = EventLog.create(
        np.arange(n) + 0.5, {"a": np.ones(n), "b": np.ones(n)}, float(n)
    )
    args = minimal_args(name, log)
    try:
        got = compute(name, log, CHANNELS, args)
    except InsufficientDataError:
        return
    assert math.isfinite(got)


def test_constant_marks_give_zero_correlation_not_nan() -> None:
    assert value("mark_gap_correlation", clumped(), channel="b") == 0.0
    assert value("mark_skewness", clumped(), channel="b") == 0.0


def test_dispersion_needs_three_events() -> None:
    log = EventLog.create([1.0, 2.0], {"a": [1, 1], "b": [1, 1]}, 3.0)
    with pytest.raises(InsufficientDataError):
        value("inter_arrival_dispersion", log)


def test_errors_are_typed() -> None:
    assert issubclass(InsufficientDataError, DiagnosticError)
    with pytest.raises(UnknownDiagnosticError):
        value("no_such_diagnostic", tiny())
    with pytest.raises(DiagnosticArgumentError):
        value("fano_factor", tiny(), window=-1.0)
    with pytest.raises(DiagnosticArgumentError):
        value("fano_factor", tiny(), widow=2.0)
    with pytest.raises(DiagnosticArgumentError):
        value("mark_mean", tiny(), channel="nope")
    with pytest.raises(DiagnosticArgumentError):
        value("mark_mean", tiny())  # channel is required
    with pytest.raises(DiagnosticArgumentError):
        value("mark_dispersion", tiny(), channel="a")  # positive channels only
    with pytest.raises(DiagnosticArgumentError):
        value("mark_autocorrelation", tiny(), channel="a", lag=1.5)
    with pytest.raises(DiagnosticArgumentError):
        value("fano_factor", tiny(), window=True)
    with pytest.raises(DiagnosticArgumentError):
        value("phase_conditioned_dispersion", tiny())  # period is required


def test_a_channel_missing_from_the_log_is_an_argument_error() -> None:
    log = EventLog.create([1.0, 2.0, 3.0], {"a": [1, 1, 1]}, 4.0)
    with pytest.raises(DiagnosticArgumentError):
        value("mark_mean", log, channel="b")


# --------------------------------------------------------------------------
# Catalogue shape and schema
# --------------------------------------------------------------------------


def test_catalogue_size_and_order() -> None:
    assert 25 <= len(names()) <= 32
    assert names() == tuple(CATALOGUE)
    assert len(set(names())) == len(names())


V1_PORTS = (
    "mean_rate",
    "inter_arrival_dispersion",
    "fano_factor",
    "count_autocorrelation",
    "spectral_peak_frequency",
    "spectral_peak_prominence",
    "phase_conditioned_dispersion",
    "mean_high_run_length",
    "run_length_geometric_deviation",
    "mark_mean",
    "mark_dispersion",
    "mark_skewness",
    "mark_autocorrelation",
    "mark_gap_correlation",
)


def test_every_v1_metric_is_ported() -> None:
    for name in V1_PORTS:
        assert name in CATALOGUE


@pytest.mark.parametrize("name", names())
def test_descriptions_are_domain_neutral(name: str) -> None:
    spec = CATALOGUE[name]
    text = (spec.description + " ".join(a.description for a in spec.args)).lower()
    assert spec.description and "\n" not in spec.description
    for term in FORBIDDEN:
        assert term not in text, (name, term)
    for channel in ("size", "sign", "magnitude"):
        assert channel not in text.split(), (name, channel)


@pytest.mark.parametrize("name", names())
def test_schema_is_closed_bounded_and_serialisable(name: str) -> None:
    schema = args_schema(name, CHANNELS)
    json.dumps(schema, sort_keys=True)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert isinstance(props, dict)
    for arg in CATALOGUE[name].args:
        prop = props[arg.name]
        match arg.kind:
            case ArgKind.CHANNEL:
                assert prop["enum"]
            case ArgKind.INTEGER | ArgKind.FRACTION:
                assert "minimum" in prop and "maximum" in prop
            case ArgKind.TIME:
                assert "exclusiveMinimum" in prop
    required = [a.name for a in CATALOGUE[name].args if a.default is None]
    assert schema["required"] == required


def prop(schema: Mapping[str, object], name: str) -> Mapping[str, object]:
    props = schema["properties"]
    assert isinstance(props, dict)
    found = props[name]
    assert isinstance(found, dict)
    return found


def test_schema_with_a_log_states_absolute_time_bounds() -> None:
    window = prop(args_schema("fano_factor", CHANNELS, tiny()), "window")
    assert window["minimum"] == pytest.approx(0.05 * 2.0)
    assert window["maximum"] == pytest.approx(0.5 * 8.0)


def test_channel_enums_respect_kinds() -> None:
    schema = args_schema("mark_dispersion", CHANNELS)
    assert prop(schema, "channel")["enum"] == ["b"]
    schema = args_schema("mark_mean", CHANNELS)
    assert prop(schema, "channel")["enum"] == ["a", "b"]


def test_defaults_are_in_mean_gap_units() -> None:
    resolved = resolve_args("fano_factor", {}, tiny(), CHANNELS)
    assert resolved["window"] == 2.0 * mean_gap(tiny())


def test_rescale_args_scales_time_args_only() -> None:
    args = {"window": 2.0, "lag": 3}
    assert rescale_args("count_autocorrelation", args, 0.5) == {
        "window": 1.0,
        "lag": 3,
    }
    assert rescale_args("mark_mean", {"channel": "b"}, 4.0) == {"channel": "b"}


def test_transform_value() -> None:
    assert CATALOGUE["mean_rate"].covariance is Covariance.INVERSE_TIME
    assert transform_value("mean_rate", 2.0, 4.0) == 0.5
    assert transform_value("fano_factor", 2.0, 4.0) == 2.0


# --------------------------------------------------------------------------
# Time-rescaling covariance
# --------------------------------------------------------------------------


def minimal_args(name: str, log: EventLog) -> dict[str, float | int | str]:
    """Defaults, plus a value for every required arg (period 10 mean gaps)."""
    tau = mean_gap(log) if log.n else 1.0
    args: dict[str, float | int | str] = {}
    for arg in CATALOGUE[name].args:
        if arg.default is not None:
            continue
        match arg.kind:
            case ArgKind.CHANNEL:
                args[arg.name] = "b"
            case ArgKind.TIME:
                args[arg.name] = 10.0 * tau
            case ArgKind.INTEGER | ArgKind.FRACTION:
                raise AssertionError(f"{name}.{arg.name} has no default")
    return args


def rescaled(log: EventLog, factor: float) -> EventLog:
    return EventLog.create(log.times * factor, log.marks, log.horizon * factor)


def outcome(
    name: str, log: EventLog, args: Mapping[str, float | int | str]
) -> float | type[DiagnosticError]:
    try:
        return compute(name, log, CHANNELS, args)
    except DiagnosticError as exc:
        return type(exc)


def assert_covariant(
    name: str, log: EventLog, args: Mapping[str, float | int | str], factor: float
) -> None:
    before = outcome(name, log, args)
    after = outcome(name, rescaled(log, factor), rescale_args(name, args, factor))
    if isinstance(before, type):
        assert after is before, (name, args, before, after)
        return
    assert isinstance(after, float), (name, args, after)
    expected = transform_value(name, before, factor)
    assert after == pytest.approx(expected, rel=1e-9, abs=1e-12), (name, args)


def poisson_log(n: int, rng: np.random.Generator) -> EventLog:
    times = np.cumsum(rng.exponential(1.0, n))
    horizon = float(times[-1] + rng.exponential(1.0))
    marks = {
        "a": np.where(rng.random(n) < 0.5, 1.0, -1.0),
        "b": rng.exponential(1.0, n),
    }
    return EventLog.create(times, marks, horizon)


@pytest.mark.parametrize("name", names())
@pytest.mark.parametrize("factor", [0.125, 4.0])
def test_every_diagnostic_is_covariant_at_defaults(name: str, factor: float) -> None:
    log = poisson_log(400, np.random.default_rng(11))
    args = minimal_args(name, log)
    assert isinstance(outcome(name, log, args), float), name
    assert_covariant(name, log, args, factor)


@st.composite
def logs_and_args(
    draw: st.DrawFn,
) -> tuple[str, EventLog, dict[str, float | int | str], float]:
    n = draw(st.integers(3, 40))
    gaps = draw(st.lists(st.floats(0.01, 10.0), min_size=n, max_size=n).map(np.array))
    times = np.cumsum(gaps)
    if not np.all(np.diff(times) > 0):
        times = np.arange(1, n + 1, dtype=np.float64)
    horizon = float(times[-1]) + draw(st.floats(0.0, 5.0))
    signs = draw(st.lists(st.sampled_from([-1.0, 1.0]), min_size=n, max_size=n))
    sizes = draw(st.lists(st.floats(0.01, 20.0), min_size=n, max_size=n))
    log = EventLog.create(times, {"a": signs, "b": sizes}, horizon)
    name = draw(st.sampled_from(names()))
    tau = mean_gap(log)
    args: dict[str, float | int | str] = {}
    for arg in CATALOGUE[name].args:
        if arg.default is not None and draw(st.booleans()):
            continue
        match arg.kind:
            case ArgKind.TIME:
                args[arg.name] = draw(st.floats(0.05, 8.0)) * tau
            case ArgKind.INTEGER:
                args[arg.name] = draw(st.integers(int(arg.low), int(arg.high)))
            case ArgKind.FRACTION:
                args[arg.name] = draw(st.floats(arg.low, arg.high))
            case ArgKind.CHANNEL:
                allowed = [c.name for c in CHANNELS if c.kind in arg.channel_kinds]
                args[arg.name] = draw(st.sampled_from(allowed))
    factor = 2.0 ** draw(st.integers(-6, 6))
    return name, log, args, factor


@given(logs_and_args())
def test_time_rescaling_covariance(
    case: tuple[str, EventLog, dict[str, float | int | str], float],
) -> None:
    name, log, args, factor = case
    assert_covariant(name, log, args, factor)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", names())
def test_determinism(name: str) -> None:
    log = poisson_log(300, np.random.default_rng(5))
    copy = EventLog.create(
        log.times.tolist(), {k: v.tolist() for k, v in log.marks.items()}, log.horizon
    )
    args = minimal_args(name, log)
    first = compute(name, log, CHANNELS, args)
    assert compute(name, log, CHANNELS, args) == first
    assert compute(name, copy, CHANNELS, args) == first


# --------------------------------------------------------------------------
# Known-process behaviour
# --------------------------------------------------------------------------


def marks(rng: np.random.Generator) -> Mapping[str, float]:
    size = -math.log1p(-rng.random())
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"a": sign, "b": size}


def excite_log(mark_fn: One | Mark, eta: float, beta: float, seed: int) -> EventLog:
    structure = Structure((Excite(KernelKind.EXP, mark_fn, ALL),), Link.IDENTITY)
    psi = ({PsiSlot((0,), "exp_rate"): beta},)
    coef = Coefficients(0.3, ((eta,),))
    return simulate(
        structure, psi, coef, CHANNELS, marks, 2000.0, np.random.default_rng(seed)
    )


def test_poisson_reads_as_poisson() -> None:
    log = poisson_log(3000, np.random.default_rng(1))
    assert value("inter_arrival_dispersion", log) == pytest.approx(1.0, abs=0.15)
    assert value("fano_factor", log) == pytest.approx(1.0, abs=0.15)
    assert value("mark_gap_correlation", log, channel="b") == pytest.approx(
        0.0, abs=0.06
    )
    assert value("burstiness", log) == pytest.approx(0.0, abs=0.05)
    assert value("post_event_rate_ratio", log) == pytest.approx(1.0, abs=0.1)
    assert value("pair_clustering_ratio", log) == pytest.approx(1.0, abs=0.1)
    assert value("next_gap_after_large_mark", log, channel="b") == pytest.approx(
        1.0, abs=0.15
    )
    assert value("count_variance_time_slope", log) == pytest.approx(0.0, abs=0.1)
    assert value("gap_exponential_ks", log) < 0.05


def test_self_exciting_log_is_overdispersed() -> None:
    log = excite_log(One(), 0.7, 0.93, seed=2)
    assert value("fano_factor", log) > 1.5
    assert value("inter_arrival_dispersion", log) > 1.3
    assert value("post_event_rate_ratio", log) > 1.3
    assert value("count_variance_time_slope", log) > 0.2
    assert value("burstiness", log) > 0.05
    # Marks do not drive arrivals here.
    assert value("mark_gap_correlation", log, channel="b") == pytest.approx(
        0.0, abs=0.06
    )


def test_mark_excited_log_couples_marks_to_gaps() -> None:
    log = excite_log(Mark("b"), 0.7, 0.6, seed=3)
    assert value("mark_gap_correlation", log, channel="b") < -0.05
    assert value("next_gap_after_large_mark", log, channel="b") < 0.9
    assert value("rate_after_mark_slope", log, channel="b") > 0.2


def test_periodic_log_has_its_spectral_peak() -> None:
    period = 11.559385768306628
    structure = Structure((Periodic(),), Link.EXP)
    psi = ({PsiSlot((), "period"): period},)
    coef = Coefficients(math.log(0.416), ((0.0, 2.08),))
    horizon = 2000.0
    log = simulate(
        structure, psi, coef, CHANNELS, marks, horizon, np.random.default_rng(4)
    )
    freq = value("spectral_peak_frequency", log)
    assert freq == pytest.approx(1.0 / period, abs=2.0 / horizon)
    assert value("spectral_peak_prominence", log) > 20.0
    on = value("spectral_power_at_period", log, period=period)
    off = value("spectral_power_at_period", log, period=period * 0.71)
    assert on > 20.0 * off
    pcd = value("phase_conditioned_dispersion", log, period=period)
    assert pcd < 1.3 < value("fano_factor", log, window=4.0)
