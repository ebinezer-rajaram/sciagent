"""Instrument tests for the intervention language and its execution (SPEC §4.0, §5).

``run_experiment`` produces every interventional dataset the agent and the
scorer's held-out battery read (SPEC §4.3 item 3), so it is an instrument
(SPEC §6.3). These tests check each intervention's semantics on cases whose
answer is known by hand or by theory, the ``Dataset`` it returns, validation,
the JSON form, time rescaling for anonymisation, and, through the
time-rescaling theorem, that interventional runs are draws from the stated
intensity.
"""

from __future__ import annotations

import importlib
import json
import math
from collections.abc import Mapping
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from numpy.polynomial.legendre import leggauss
from scipy import stats

from sciagent.core.errors import SciAgentError
from sciagent.glm import interventions as iv
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    Feature,
    KernelKind,
    Link,
    Mark,
    One,
    PsiSlot,
    Structure,
    psi_slots,
)
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    EXPERIMENT_SCHEMA,
    INTERVENTION_SCHEMA,
    MAX_FORCED_EVENTS,
    MAX_PARTS,
    Censor,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
    InjectMarks,
    Intervention,
    InvalidInterventionError,
    experiment_from_json,
    experiment_to_json,
    from_json,
    rescale,
    rescale_dataset,
    rescale_experiment,
    run_experiment,
    to_json,
    validate_experiment,
)
from sciagent.glm.simulate import Coefficients, PsiAssignment, intensity, simulate

CHANNELS = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
    ChannelSpec("sign", ChannelKind.SIGN, location=0.0, scale=1.0),
)


def marks_exp(rng: np.random.Generator) -> Mapping[str, float]:
    """Sizes Exponential(mean 1), signs a fair coin."""
    size = float(rng.exponential(1.0))
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"size": size, "sign": sign}


def psi_for(feature: Feature, values: Mapping[str, float]) -> dict[PsiSlot, float]:
    return {slot: values[slot.name] for slot in psi_slots(feature)}


type Truth = tuple[Structure, PsiAssignment, Coefficients]
type Bools = npt.NDArray[np.bool_]


def truth(
    feature: Feature,
    psi: Mapping[str, float],
    mu: float,
    theta: float,
    link: Link = Link.IDENTITY,
) -> Truth:
    return (
        Structure((feature,), link),
        (psi_for(feature, psi),),
        Coefficients(mu, ((theta,),)),
    )


def hawkes(mu: float = 0.5, eta: float = 0.5, beta: float = 2.0) -> Truth:
    return truth(Excite(KernelKind.EXP, One(), ALL), {"exp_rate": beta}, mu, eta)


def size_excited(mu: float = 0.5, eta: float = 0.4, beta: float = 2.0) -> Truth:
    """S11's truth: excitation proportional to the parent's size (mean size 1)."""
    return truth(Excite(KernelKind.EXP, Mark("size"), ALL), {"exp_rate": beta}, mu, eta)


def run(t: Truth, experiment: Experiment, seed: int) -> Dataset:
    structure, psi, coef = t
    return run_experiment(
        structure,
        psi,
        coef,
        CHANNELS,
        marks_exp,
        experiment,
        np.random.default_rng(seed),
    )


def in_window(times: Floats, window: tuple[float, float]) -> Bools:
    return (times >= window[0]) & (times < window[1])


# --------------------------------------------------------------------------
# No intervention
# --------------------------------------------------------------------------


def test_empty_compose_is_the_plain_simulator() -> None:
    """Compose(()) is an unintervened run, byte-identical to ``simulate``."""
    structure, psi, coef = hawkes()
    ds = run((structure, psi, coef), Experiment(Compose(()), 300.0), seed=4)
    log = simulate(
        structure, psi, coef, CHANNELS, marks_exp, 300.0, np.random.default_rng(4)
    )
    assert ds.log.times.tobytes() == log.times.tobytes()
    for name in log.marks:
        assert ds.log.marks[name].tobytes() == log.marks[name].tobytes()
    assert ds.endogenous.all()
    assert ds.excluded == ()
    assert ds.log.horizon == 300.0


# --------------------------------------------------------------------------
# ClampRate
# --------------------------------------------------------------------------


def test_clamp_rate_on_hawkes_is_poisson_in_the_window() -> None:
    """do(λ = c): the count in the window is Poisson(c·width), times uniform."""
    window = (500.0, 700.0)
    rate = 3.0
    ds = run(hawkes(), Experiment(ClampRate(window, rate), 1000.0), seed=1)
    inside = in_window(ds.log.times, window)
    n = int(inside.sum())
    # Poisson(600): sd ≈ 24.5; 4 sd.
    assert abs(n - 600) < 4 * math.sqrt(600)
    u = (ds.log.times[inside] - window[0]) / (window[1] - window[0])
    assert stats.kstest(u, "uniform").pvalue > 1e-3
    # Clamp events are not evidence about the model's λ; the window is excluded.
    assert not ds.endogenous[inside].any()
    assert ds.endogenous[~inside].all()
    assert ds.excluded == (window,)


def test_clamp_rate_zero_silences_the_window() -> None:
    window = (100.0, 160.0)
    ds = run(hawkes(), Experiment(ClampRate(window, 0.0), 300.0), seed=2)
    assert not in_window(ds.log.times, window).any()
    assert ds.excluded == (window,)
    assert ds.log.n > 0


def test_clamp_rate_events_excite_later_events() -> None:
    """Clamped events enter history: after a high clamp the rate stays raised."""
    window = (100.0, 110.0)
    after = (110.0, 112.0)
    high, ctrl = [], []
    for seed in range(40):
        ds = run(hawkes(), Experiment(ClampRate(window, 20.0), 112.0), seed)
        high.append(int(in_window(ds.log.times, after).sum()))
        ds = run(hawkes(), Experiment(Compose(()), 112.0), seed)
        ctrl.append(int(in_window(ds.log.times, after).sum()))
    # λ just after the clamp ≈ μ + η·20 = 10.5, decaying at β = 2: about 5 direct
    # children of clamp events in (110, 112], against a baseline of about 2.
    d = np.asarray(high, dtype=np.float64) - np.asarray(ctrl, dtype=np.float64)
    assert d.mean() > 3.0
    assert d.mean() > 4.0 * d.std(ddof=1) / math.sqrt(d.size)


# --------------------------------------------------------------------------
# ForceEvents
# --------------------------------------------------------------------------


def test_forced_events_are_in_the_log_exogenous_with_given_marks() -> None:
    times = (10.0, 10.5, 20.0)
    marks = {"size": (2.0, 3.0, 4.0)}
    ds = run(hawkes(), Experiment(ForceEvents(times, marks), 50.0), seed=3)
    forced = np.isin(ds.log.times, times)
    assert int(forced.sum()) == 3
    assert not ds.endogenous[forced].any()
    assert ds.endogenous[~forced].all()
    np.testing.assert_array_equal(ds.log.marks["size"][forced], [2.0, 3.0, 4.0])
    # Signs were not given, so they come from the sampler: valid ±1 values.
    assert set(ds.log.marks["sign"][forced].tolist()) <= {-1.0, 1.0}
    assert ds.excluded == ()


def test_forced_events_at_zero_and_at_the_horizon() -> None:
    ds = run(hawkes(), Experiment(ForceEvents((0.0, 50.0)), 50.0), seed=0)
    assert ds.log.times[0] == 0.0
    assert ds.log.times[-1] == 50.0
    assert not ds.endogenous[0]
    assert not ds.endogenous[-1]


def test_forced_burst_excess_matches_branching_ratio() -> None:
    """Each forced event on a Hawkes truth has η/(1-η) descendants on average.

    A burst of B events, so the excess over a control is B·η/(1-η) in
    expectation: B direct children times η, plus their descendants.
    """
    mu, eta, beta = 0.5, 0.5, 2.0
    burst = tuple(100.0 + 0.01 * k for k in range(50))
    window = (100.0, 130.0)
    excess = []
    for seed in range(60):
        forced = run(hawkes(mu, eta, beta), Experiment(ForceEvents(burst), 130.0), seed)
        endo = forced.log.times[forced.endogenous]
        n_forced = int(in_window(endo, window).sum())
        ctrl = run(hawkes(mu, eta, beta), Experiment(Compose(()), 130.0), 10_000 + seed)
        excess.append(n_forced - int(in_window(ctrl.log.times, window).sum()))
    expected = len(burst) * eta / (1.0 - eta)  # 50
    # Per-seed variance ≈ 2·120 (baselines) + 50·η/(1-η)^3 = 440: se ≈ 2.7.
    se = math.sqrt(440.0 / 60)
    assert abs(float(np.mean(excess)) - expected) < 4 * se


# --------------------------------------------------------------------------
# InjectMarks: the S11 discriminating experiment
# --------------------------------------------------------------------------


def test_inject_marks_overrides_marks_of_generated_events_in_window() -> None:
    window = (50.0, 80.0)
    ds = run(
        hawkes(),
        Experiment(
            Compose((InjectMarks(window, "size", 7.0), ForceEvents((60.0,)))), 100.0
        ),
        seed=5,
    )
    inside = in_window(ds.log.times, window) & ds.endogenous
    outside = ~in_window(ds.log.times, window)
    assert inside.any()
    assert np.all(ds.log.marks["size"][inside] == 7.0)
    assert not np.any(ds.log.marks["size"][outside] == 7.0)
    # Forced events keep their own (sampled) marks.
    forced_at = int(np.flatnonzero(ds.log.times == 60.0)[0])
    assert ds.log.marks["size"][forced_at] != 7.0


def test_inject_marks_is_a_no_op_on_a_mark_blind_truth() -> None:
    """Pure Hawkes ignores sizes, so do(size) changes no time, byte for byte."""
    window = (100.0, 150.0)
    exp_inject = Experiment(InjectMarks(window, "size", 5.0), 300.0)
    exp_ctrl = Experiment(Compose(()), 300.0)
    a, b = run(hawkes(), exp_inject, seed=6), run(hawkes(), exp_ctrl, seed=6)
    assert a.log.times.tobytes() == b.log.times.tobytes()
    assert a.log.marks["sign"].tobytes() == b.log.marks["sign"].tobytes()
    assert a.log.marks["size"].tobytes() != b.log.marks["size"].tobytes()


def test_inject_marks_discriminates_size_excitation_from_hawkes() -> None:
    """S11: do(size = 2.5) on a window raises the later rate iff sizes excite."""
    window = (100.0, 110.0)
    count = (100.0, 116.0)
    design = Experiment(InjectMarks(window, "size", 2.5), 116.0)
    control = Experiment(Compose(()), 116.0)

    def effect(t: Truth) -> tuple[float, float]:
        diffs = []
        for seed in range(40):
            n_do = int(in_window(run(t, design, seed).log.times, count).sum())
            n_ctl = int(in_window(run(t, control, seed).log.times, count).sum())
            diffs.append(n_do - n_ctl)
        d = np.asarray(diffs, dtype=np.float64)
        return float(d.mean()), float(d.std(ddof=1) / math.sqrt(d.size))

    # Same branching ratio 0.3 under the sampler; only the mark dependence differs.
    sized_mean, sized_se = effect(size_excited(eta=0.3))
    hawkes_mean, hawkes_se = effect(hawkes(eta=0.3))
    assert sized_mean > 4 * sized_se
    assert sized_mean > 4.0
    # Mark-blind truth: identical runs, so exactly zero.
    assert hawkes_mean == 0.0
    assert hawkes_se == 0.0


# --------------------------------------------------------------------------
# Censor
# --------------------------------------------------------------------------


def test_censor_hides_events_but_they_still_excite() -> None:
    """Censoring is observation only: the run is the unintervened run, minus the
    window. Later events are therefore exactly those excited by censored ones."""
    window = (100.0, 150.0)
    censored = run(hawkes(), Experiment(Censor(window), 300.0), seed=7)
    full = run(hawkes(), Experiment(Compose(()), 300.0), seed=7)
    hidden = in_window(full.log.times, window)
    assert hidden.any()
    assert censored.log.times.tobytes() == full.log.times[~hidden].tobytes()
    for name in full.log.marks:
        assert (
            censored.log.marks[name].tobytes()
            == full.log.marks[name][~hidden].tobytes()
        )
    assert censored.excluded == (window,)
    assert censored.endogenous.all()
    # The hidden events mattered: λ just after the window is higher with them.
    structure, psi, coef = hawkes()
    t = np.array([150.0 + 1e-9])
    lam_full = intensity(structure, psi, coef, CHANNELS, full.log, t)[0]
    lam_obs = intensity(structure, psi, coef, CHANNELS, censored.log, t)[0]
    assert lam_full > lam_obs


def test_forced_events_stay_visible_inside_a_censor_window() -> None:
    window = (10.0, 30.0)
    ds = run(
        hawkes(),
        Experiment(Compose((Censor(window), ForceEvents((20.0,)))), 50.0),
        seed=8,
    )
    inside = in_window(ds.log.times, window)
    assert ds.log.times[inside].tolist() == [20.0]
    assert not ds.endogenous[inside].any()


def test_censor_and_clamp_compose_and_excluded_is_their_union() -> None:
    parts = (
        Censor((10.0, 30.0)),
        ClampRate((20.0, 40.0), 2.0),
        ClampRate((60.0, 70.0), 1.0),
        Censor((70.0, 80.0)),
        Censor((90.0, 95.0)),
    )
    ds = run(hawkes(), Experiment(Compose(parts), 100.0), seed=9)
    assert ds.excluded == ((10.0, 40.0), (60.0, 80.0), (90.0, 95.0))
    # Nothing generated in a censor window is observed; clamp-only parts are.
    assert not in_window(ds.log.times, (10.0, 30.0)).any()
    assert not in_window(ds.log.times, (70.0, 80.0)).any()
    clamp_only = in_window(ds.log.times, (30.0, 40.0)) | in_window(
        ds.log.times, (60.0, 70.0)
    )
    assert clamp_only.any()
    assert not ds.endogenous[clamp_only].any()


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_determinism() -> None:
    experiment = Experiment(
        Compose(
            (
                ForceEvents((5.0, 6.0), {"size": (2.0, 2.0)}),
                InjectMarks((10.0, 20.0), "sign", -1.0),
                Censor((30.0, 35.0)),
                ClampRate((40.0, 45.0), 4.0),
            )
        ),
        100.0,
    )
    a, b, c = (run(size_excited(), experiment, s) for s in (11, 11, 12))
    assert a.log.times.tobytes() == b.log.times.tobytes()
    for name in a.log.marks:
        assert a.log.marks[name].tobytes() == b.log.marks[name].tobytes()
    assert a.endogenous.tobytes() == b.endogenous.tobytes()
    assert a.excluded == b.excluded
    assert a.log.times.tobytes() != c.log.times.tobytes()


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: ForceEvents(()),
        lambda: ForceEvents((1.0, math.nan)),
        lambda: ForceEvents((2.0, 1.0)),
        lambda: ForceEvents((1.0, 1.0)),
        lambda: ForceEvents((-1.0,)),
        lambda: ForceEvents((1.0, math.inf)),
        lambda: ForceEvents((1.0, 2.0), {"size": (1.0,)}),
        lambda: ForceEvents((1.0,), {"size": (math.nan,)}),
        lambda: ForceEvents(tuple(float(k) for k in range(MAX_FORCED_EVENTS + 1))),
        lambda: InjectMarks((2.0, 1.0), "size", 1.0),
        lambda: InjectMarks((1.0, 1.0), "size", 1.0),
        lambda: InjectMarks((-1.0, 1.0), "size", 1.0),
        lambda: InjectMarks((0.0, 1.0), "size", math.nan),
        lambda: InjectMarks((0.0, 1.0), "", 1.0),
        lambda: Censor((0.0, math.inf)),
        lambda: ClampRate((0.0, 1.0), -0.5),
        lambda: ClampRate((0.0, 1.0), math.inf),
        lambda: ClampRate((0.0, 1.0), True),
        lambda: Compose((ClampRate((0.0, 2.0), 1.0), ClampRate((1.0, 3.0), 1.0))),
        lambda: Compose(
            (InjectMarks((0.0, 2.0), "size", 1.0), InjectMarks((1.0, 3.0), "size", 2.0))
        ),
        lambda: Compose((ForceEvents((1.0, 2.0)), ForceEvents((2.0, 3.0)))),
        lambda: Compose((Censor((0.0, 1.0)),)),
        lambda: Compose(tuple(Censor((k, k + 1.0)) for k in range(MAX_PARTS + 1))),
        lambda: Compose(
            (
                ForceEvents(tuple(float(k) for k in range(MAX_FORCED_EVENTS))),
                ForceEvents((0.5,)),
            )
        ),
        lambda: Experiment(Censor((0.0, 1.0)), 0.0),
        lambda: Experiment(Censor((0.0, 1.0)), math.nan),
    ],
)
def test_invalid_interventions_raise(build: Any) -> None:
    with pytest.raises(InvalidInterventionError):
        build()


def test_allowed_combinations() -> None:
    # Touching windows do not overlap; different channels never conflict.
    Compose((ClampRate((0.0, 1.0), 1.0), ClampRate((1.0, 2.0), 3.0)))
    Compose(
        (InjectMarks((0.0, 2.0), "size", 1.0), InjectMarks((1.0, 3.0), "sign", 1.0))
    )
    Compose((Censor((0.0, 2.0)), Censor((1.0, 3.0)), ClampRate((1.0, 4.0), 0.0)))
    Compose((ForceEvents((1.0, 3.0)), ForceEvents((2.0,))))


@pytest.mark.parametrize(
    "intervention",
    [
        Censor((10.0, 101.0)),
        ForceEvents((101.0,)),
        InjectMarks((0.0, 1.0), "colour", 1.0),
        InjectMarks((0.0, 1.0), "size", 0.0),  # positive channel
        InjectMarks((0.0, 1.0), "sign", 0.5),  # sign channel
        ForceEvents((1.0,), {"sign": (2.0,)}),
        ForceEvents((1.0,), {"colour": (2.0,)}),
        ClampRate((0.0, 10.0), iv.MAX_CLAMP_RATE * 2.0),
    ],
)
def test_validate_experiment_rejects_out_of_context(
    intervention: Intervention,
) -> None:
    experiment = Experiment(intervention, 100.0)
    with pytest.raises(InvalidInterventionError):
        validate_experiment(experiment, CHANNELS)
    with pytest.raises(InvalidInterventionError):  # run_experiment validates too
        run(hawkes(), experiment, seed=0)


def test_validate_experiment_caps() -> None:
    validate_experiment(Experiment(Censor((0.0, 1.0)), iv.MAX_HORIZON), CHANNELS)
    with pytest.raises(InvalidInterventionError):
        validate_experiment(
            Experiment(Censor((0.0, 1.0)), 2.0 * iv.MAX_HORIZON), CHANNELS
        )
    # The expected number of clamp events is capped (scale-free).
    width = iv.MAX_CLAMP_EVENTS / iv.MAX_CLAMP_RATE * 1.5
    many = Experiment(ClampRate((0.0, width), iv.MAX_CLAMP_RATE), iv.MAX_HORIZON)
    with pytest.raises(InvalidInterventionError):
        validate_experiment(many, CHANNELS)


def test_validate_experiment_scales_caps_with_the_time_factor() -> None:
    """In agent units (times * f) the horizon cap is * f and the rate cap ÷ f."""
    f = 4.0
    ok = Experiment(ClampRate((0.0, 1.0), iv.MAX_CLAMP_RATE / f), iv.MAX_HORIZON * f)
    validate_experiment(ok, CHANNELS, time_factor=f)
    with pytest.raises(InvalidInterventionError):
        validate_experiment(ok, CHANNELS)
    fast = Experiment(ClampRate((0.0, 1.0), iv.MAX_CLAMP_RATE), 100.0)
    with pytest.raises(InvalidInterventionError):
        validate_experiment(fast, CHANNELS, time_factor=f)


def test_errors_are_typed_and_readable() -> None:
    with pytest.raises(InvalidInterventionError) as info:
        Compose((ClampRate((0.0, 2.0), 1.0), ClampRate((1.0, 3.0), 1.0)))
    assert issubclass(InvalidInterventionError, SciAgentError)
    assert "overlap" in str(info.value)


# --------------------------------------------------------------------------
# JSON form
# --------------------------------------------------------------------------

# 0 or ≥ 1e-6: rescaling by 2**±12 then stays in the normal range, where it is exact.
_times = st.one_of(st.just(0.0), st.floats(min_value=1e-6, max_value=1e4))


@st.composite
def windows(draw: st.DrawFn) -> tuple[float, float]:
    a = draw(_times)
    b = draw(_times)
    assume(a != b)
    return (min(a, b), max(a, b))


def _force(draw: st.DrawFn) -> ForceEvents:
    times = sorted(set(draw(st.lists(_times, min_size=1, max_size=6))))
    marks = None
    if draw(st.booleans()):
        sizes = st.floats(min_value=1e-3, max_value=1e3, allow_subnormal=False)
        marks = {"size": tuple(draw(sizes) for _ in times)}
        if draw(st.booleans()):
            signs = st.sampled_from([-1.0, 1.0])
            marks["sign"] = tuple(draw(signs) for _ in times)
    return ForceEvents(tuple(times), marks)


@st.composite
def atoms(draw: st.DrawFn) -> Intervention:
    kind = draw(st.sampled_from(["force", "inject", "censor", "clamp"]))
    match kind:
        case "force":
            return _force(draw)
        case "inject":
            channel = draw(st.sampled_from(["size", "sign"]))
            value = 2.5 if channel == "size" else -1.0
            return InjectMarks(draw(windows()), channel, value)
        case "censor":
            return Censor(draw(windows()))
        case _:
            rate = draw(
                st.one_of(st.just(0.0), st.floats(min_value=1e-6, max_value=10.0))
            )
            return ClampRate(draw(windows()), rate)


@st.composite
def interventions(draw: st.DrawFn) -> Intervention:
    parts = draw(st.lists(atoms(), max_size=4))
    if len(parts) == 1:
        return parts[0]
    try:
        return Compose(tuple(parts))
    except InvalidInterventionError:
        assume(False)
        raise


_validator = importlib.import_module("jsonschema")


@settings(max_examples=100)
@given(interventions())
def test_json_round_trip(intervention: Intervention) -> None:
    obj = to_json(intervention)
    text = json.dumps(obj, allow_nan=False)
    back = from_json(json.loads(text))
    assert back == intervention
    assert to_json(back) == obj
    _validator.validate(obj, INTERVENTION_SCHEMA)


@settings(max_examples=50)
@given(interventions(), st.floats(min_value=1.0, max_value=1e4))
def test_experiment_json_round_trip(intervention: Intervention, horizon: float) -> None:
    experiment = Experiment(intervention, horizon)
    obj = experiment_to_json(experiment)
    back = experiment_from_json(json.loads(json.dumps(obj, allow_nan=False)))
    assert back == experiment
    _validator.validate(obj, EXPERIMENT_SCHEMA)


def test_json_canonical_forms() -> None:
    a, b = Censor((0.0, 1.0)), ClampRate((2.0, 3.0), 1.0)
    assert Compose((a, b)) == Compose((b, a))
    assert to_json(Compose((a, b))) == to_json(Compose((b, a)))
    # Nested composes flatten; a one-part compose is its part.
    nested = {
        "type": "compose",
        "parts": [to_json(a), {"type": "compose", "parts": [to_json(b)]}],
    }
    assert from_json(nested) == Compose((a, b))
    assert from_json({"type": "compose", "parts": [to_json(a)]}) == a
    # Integers are accepted where numbers are expected.
    assert from_json({"type": "censor", "window": [0, 1]}) == a
    assert to_json(ForceEvents((1.0,), {})) == {"type": "force_events", "times": [1.0]}
    assert experiment_from_json({"intervention": to_json(a)}) == Experiment(
        a, DEFAULT_HORIZON
    )


def test_json_examples() -> None:
    """The exact wire form the tool layer documents to the agent."""
    exp = Experiment(
        Compose(
            (
                ForceEvents((1.0, 2.0), {"size": (3.0, 4.0)}),
                InjectMarks((5.0, 6.0), "size", 2.0),
                Censor((7.0, 8.0)),
                ClampRate((9.0, 10.0), 0.5),
            )
        ),
        100.0,
    )
    assert experiment_to_json(exp) == {
        "intervention": {
            "type": "compose",
            "parts": [
                {"type": "censor", "window": [7.0, 8.0]},
                {"type": "clamp_rate", "window": [9.0, 10.0], "rate": 0.5},
                {
                    "type": "force_events",
                    "times": [1.0, 2.0],
                    "marks": {"size": [3.0, 4.0]},
                },
                {
                    "type": "inject_marks",
                    "window": [5.0, 6.0],
                    "channel": "size",
                    "value": 2.0,
                },
            ],
        },
        "horizon": 100.0,
    }


@pytest.mark.parametrize(
    "obj",
    [
        None,
        [],
        "censor",
        {},
        {"type": "teleport"},
        {"type": "censor"},
        {"type": "censor", "window": [0.0]},
        {"type": "censor", "window": [0.0, 1.0, 2.0]},
        {"type": "censor", "window": ["0", 1.0]},
        {"type": "censor", "window": [True, 1.0]},
        {"type": "censor", "window": [0.0, 1.0], "extra": 1},
        {"type": "clamp_rate", "window": [0.0, 1.0]},
        {"type": "clamp_rate", "window": [0.0, 1.0], "rate": "fast"},
        {"type": "force_events", "times": 1.0},
        {"type": "force_events", "times": [1.0], "marks": [1.0]},
        {"type": "force_events", "times": [1.0], "marks": {"size": 1.0}},
        {"type": "inject_marks", "window": [0.0, 1.0], "channel": 3, "value": 1.0},
        {"type": "compose", "parts": {}},
        {"type": "compose", "parts": [{"type": "censor", "window": [1.0, 0.0]}]},
    ],
)
def test_from_json_rejects_malformed(obj: object) -> None:
    with pytest.raises(InvalidInterventionError):
        from_json(obj)


@pytest.mark.parametrize(
    "obj",
    [
        None,
        {"intervention": {"type": "censor", "window": [0, 1]}, "horizon": "long"},
        {"intervention": {"type": "censor", "window": [0, 1]}, "extra": 1},
        {"horizon": 10.0},
    ],
)
def test_experiment_from_json_rejects_malformed(obj: object) -> None:
    with pytest.raises(InvalidInterventionError):
        experiment_from_json(obj)


def test_schema_rejects_what_from_json_rejects() -> None:
    bad = {"type": "censor", "window": [0.0, 1.0], "extra": 1}
    with pytest.raises(_validator.ValidationError):
        _validator.validate(bad, INTERVENTION_SCHEMA)
    with pytest.raises(_validator.ValidationError):
        _validator.validate({"type": "teleport"}, INTERVENTION_SCHEMA)
    _validator.Draft202012Validator.check_schema(INTERVENTION_SCHEMA)
    _validator.Draft202012Validator.check_schema(EXPERIMENT_SCHEMA)


# --------------------------------------------------------------------------
# Time rescaling for anonymisation (SPEC §5)
# --------------------------------------------------------------------------

_factors = st.integers(min_value=-12, max_value=12).map(lambda k: math.ldexp(1.0, k))


@given(interventions(), _factors)
def test_rescale_round_trip_is_exact(intervention: Intervention, f: float) -> None:
    there = rescale(intervention, f)
    assert rescale(there, 1.0 / f) == intervention
    assert to_json(rescale(there, 1.0 / f)) == to_json(intervention)


def test_rescale_semantics() -> None:
    f = 8.0
    assert rescale(ClampRate((1.0, 2.0), 4.0), f) == ClampRate((8.0, 16.0), 0.5)
    assert rescale(Censor((1.0, 2.0)), f) == Censor((8.0, 16.0))
    assert rescale(InjectMarks((1.0, 2.0), "size", 3.0), f) == InjectMarks(
        (8.0, 16.0), "size", 3.0
    )
    forced = ForceEvents((1.0, 2.0), {"size": (5.0, 6.0)})
    assert rescale(forced, f) == ForceEvents((8.0, 16.0), {"size": (5.0, 6.0)})
    exp = Experiment(Censor((1.0, 2.0)), 10.0)
    assert rescale_experiment(exp, f) == Experiment(Censor((8.0, 16.0)), 80.0)


@pytest.mark.parametrize("factor", [3.0, 0.0, -2.0, math.nan, math.inf, 0.1])
def test_rescale_rejects_inexact_factors(factor: float) -> None:
    with pytest.raises(InvalidInterventionError):
        rescale(Censor((1.0, 2.0)), factor)
    ds = Dataset.observational(EventLog.create([1.0], {"size": [1.0]}, 2.0))
    with pytest.raises(InvalidInterventionError):
        rescale_dataset(ds, factor)


def test_rescale_dataset_round_trip_is_exact() -> None:
    experiment = Experiment(
        Compose(
            (ForceEvents((3.3,)), Censor((10.1, 20.7)), ClampRate((30.0, 40.3), 2.0))
        ),
        100.0,
    )
    ds = run(hawkes(), experiment, seed=13)
    f = 2.0**-5
    there = rescale_dataset(ds, f)
    np.testing.assert_array_equal(there.log.times, ds.log.times * f)
    assert there.log.horizon == 100.0 * f
    assert there.excluded == ((10.1 * f, 20.7 * f), (30.0 * f, 40.3 * f))
    assert there.endogenous.tobytes() == ds.endogenous.tobytes()
    back = rescale_dataset(there, 1.0 / f)
    assert back.log.times.tobytes() == ds.log.times.tobytes()
    for name in ds.log.marks:
        assert back.log.marks[name].tobytes() == ds.log.marks[name].tobytes()
    assert back.excluded == ds.excluded
    assert back.log.horizon == ds.log.horizon
    assert back.label == ds.label


def test_rescaled_experiment_runs_the_same_as_native() -> None:
    """The agent's rescaled design, mapped back, is the native design exactly;
    its dataset, rescaled for display, is the native dataset * f."""
    f = 16.0
    native = Experiment(
        Compose((ForceEvents((5.0,)), ClampRate((10.0, 20.0), 1.5))), 50.0
    )
    agent_view = rescale_experiment(native, f)
    validate_experiment(agent_view, CHANNELS, time_factor=f)
    back = rescale_experiment(agent_view, 1.0 / f)
    assert back == native
    ds = run(hawkes(), back, seed=14)
    shown = rescale_dataset(ds, f)
    np.testing.assert_array_equal(shown.log.times, ds.log.times * f)


# --------------------------------------------------------------------------
# Time-rescaling self-consistency of interventional runs (slow)
# --------------------------------------------------------------------------

_GL_X, _GL_W = leggauss(12)


def interventional_increments(t: Truth, ds: Dataset) -> Floats:
    """Λ between consecutive endogenous events outside the excluded windows.

    λ is the simulator's own ``intensity`` on the full log (forced events in
    history), integrated by Gauss-Legendre on pieces split at every event and
    every excluded-window boundary, with the excluded windows removed.
    """
    structure, psi, coef = t
    log = ds.log
    bounds = np.asarray([x for w in ds.excluded for x in w], dtype=np.float64)
    knots = np.unique(np.concatenate([[0.0], log.times, bounds, [log.horizon]]))
    fine: list[float] = []
    for a, b in pairwise(knots):
        k = max(1, math.ceil((b - a) / 0.1))
        fine.extend(np.linspace(a, b, k + 1)[:-1].tolist())
    fine.append(float(knots[-1]))
    edges = np.array(fine)
    lo, hi = edges[:-1], edges[1:]
    mid = 0.5 * (lo + hi)
    keep = np.ones(mid.size, dtype=bool)
    for a, b in ds.excluded:
        keep &= ~((mid > a) & (mid < b))
    nodes = 0.5 * (hi - lo)[:, None] * _GL_X[None, :] + mid[:, None]
    lam = intensity(structure, psi, coef, CHANNELS, log, nodes.ravel())
    piece = np.where(keep, 0.5 * (hi - lo) * (lam.reshape(nodes.shape) @ _GL_W), 0.0)
    cum = np.concatenate([[0.0], np.cumsum(piece)])
    counted = ds.endogenous.copy()
    for a, b in ds.excluded:
        counted &= ~((log.times >= a) & (log.times < b))
    at = cum[np.searchsorted(edges, log.times[counted])]
    return np.diff(np.concatenate([[0.0], at]))


_SELF_CONSISTENCY = Experiment(
    Compose(
        (
            ForceEvents(
                tuple(50.0 + 0.2 * k for k in range(10)), {"size": (4.0,) * 10}
            ),
            ForceEvents(tuple(200.0 + 0.5 * k for k in range(5))),
            InjectMarks((100.0, 140.0), "size", 2.0),
            ClampRate((250.0, 270.0), 4.0),
            ClampRate((320.0, 330.0), 0.0),
        )
    ),
    400.0,
)


@pytest.mark.slow
@pytest.mark.parametrize(
    "t",
    [
        size_excited(),
        hawkes(),
        truth(
            Excite(KernelKind.POWER, Mark("size"), ALL),
            {"power_c": 0.2, "power_p": 2.0},
            0.3,
            0.4,
            Link.SOFTPLUS,
        ),
        truth(
            Excite(KernelKind.GAMMA, One(), ALL),
            {"gamma_shape": 2.0, "gamma_mean": 1.0},
            math.log(0.8),
            -0.5,
            Link.EXP,
        ),
    ],
    ids=["size-excited", "hawkes", "powerk-softplus", "gammak-exp-inhibition"],
)
def test_interventional_runs_rescale_to_unit_exponentials(t: Truth) -> None:
    ds = run(t, _SELF_CONSISTENCY, seed=21)
    tau = interventional_increments(t, ds)
    assert tau.size > 150
    assert stats.kstest(tau, "expon").pvalue > 1e-3


@pytest.mark.slow
def test_interventional_rescaling_has_power() -> None:
    """Positive control: dropping the exogenous (forced, clamped) events from
    history is detected, so the check above can fail."""
    t = size_excited()
    every = tuple(2.0 + 4.0 * k for k in range(100))
    design = Compose(
        (ForceEvents(every, {"size": (4.0,) * 100}), ClampRate((250.0, 270.0), 4.0))
    )
    ds = run(t, Experiment(design, 400.0), seed=21)
    keep = ds.endogenous.copy()
    stripped = Dataset.create(
        EventLog.create(
            ds.log.times[keep],
            {k: v[keep] for k, v in ds.log.marks.items()},
            ds.log.horizon,
        ),
        ds.endogenous[keep],
        ds.excluded,
        ds.label,
    )
    assert stats.kstest(interventional_increments(t, ds), "expon").pvalue > 1e-3
    assert stats.kstest(interventional_increments(t, stripped), "expon").pvalue < 1e-3
