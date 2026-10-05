"""Instrument tests for truncation at the event cap (P4 Δ2; SPEC §4.0, §6.3).

When a run reaches its event cap N, :func:`simulate_planned` with
``on_cap="truncate"`` stops at the N-th event and keeps it, and
:func:`run_experiment_truncating` returns the log observed up to that time τ:
the planned horizon H is kept (``Trend`` reads t/H), and the tail
``[nextafter(τ), H]`` joins ``Dataset.excluded``, so the likelihood is the
stopping-time likelihood ``Σ_{i≤N} log λ(tᵢ) - ∫₀^τ λ`` (checked in
``test_likelihood_truncation.py``).

These tests check that a truncated run is exactly the prefix of the same
seeded run under a larger cap, the dataset it returns, that runs below the cap
are byte-identical to the pre-Δ2 behaviour (a pinned digest), determinism, and
that the candidate cap and runaway guards still stop a run.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Final, cast

import numpy as np
import pytest

from sciagent.glm import simulate as sim
from sciagent.glm.data import Dataset
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
    Periodic,
    PsiSlot,
    Structure,
    Trend,
    psi_slots,
)
from sciagent.glm.interventions import (
    Censor,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
    InjectMarks,
    run_experiment,
    run_experiment_truncating,
    to_plan,
)
from sciagent.glm.simulate import (
    Coefficients,
    ExplosionError,
    InvalidSimulationInputError,
    PsiAssignment,
)

CHANNELS: Final = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
    ChannelSpec("sign", ChannelKind.SIGN, location=0.0, scale=1.0),
)

type Truth = tuple[Structure, PsiAssignment, Coefficients]


def marks_exp(rng: np.random.Generator) -> Mapping[str, float]:
    """Sizes Exponential(mean 1), signs a fair coin."""
    size = float(rng.exponential(1.0))
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"size": size, "sign": sign}


def psi_for(feature: Feature, values: Mapping[str, float]) -> dict[PsiSlot, float]:
    return {slot: values[slot.name] for slot in psi_slots(feature)}


def size_excited(mu: float = 0.5, eta: float = 0.4, beta: float = 2.0) -> Truth:
    feature = Excite(KernelKind.EXP, Mark("size"), ALL)
    return (
        Structure((feature,), Link.IDENTITY),
        (psi_for(feature, {"exp_rate": beta}),),
        Coefficients(mu, ((eta,),)),
    )


def trend_hawkes(
    mu: float = 0.4, trend: float = 0.4, eta: float = 0.4, beta: float = 1.0
) -> Truth:
    """λ = μ + a·t/H + η Σ β e^{-β(t - tⱼ)}: a truth whose λ reads H."""
    excite = Excite(KernelKind.EXP, One(), ALL)
    return (
        Structure((Trend(), excite), Link.IDENTITY),
        ({}, psi_for(excite, {"exp_rate": beta})),
        Coefficients(mu, ((trend,), (eta,))),
    )


#: A design touching every flag: forced events (some after a typical τ),
#: a mark injection, a censor and a clamp.
PLAN: Final = sim.Plan(
    forced=(
        sim.ForcedEvent(5.0, {"size": 2.0}),
        sim.ForcedEvent(30.0),
        sim.ForcedEvent(90.0),
        sim.ForcedEvent(250.0),
    ),
    clamps=(sim.RateClamp(40.0, 50.0, 3.0),),
    overrides=(sim.MarkOverride(60.0, 80.0, "sign", -1.0),),
)

EXPERIMENT: Final = Experiment(
    Compose(
        (
            ForceEvents((5.0, 30.0, 90.0, 250.0), {"size": (2.0, 2.0, 2.0, 2.0)}),
            InjectMarks((60.0, 80.0), "sign", -1.0),
            Censor((100.0, 120.0)),
            ClampRate((40.0, 50.0), 3.0),
        )
    ),
    300.0,
)


def planned(
    truth: Truth,
    plan: sim.Plan,
    horizon: float,
    seed: int,
    max_events: int,
    on_cap: sim.OnCap = "raise",
) -> sim.PlannedRun:
    structure, psi, coef = truth
    return sim.simulate_planned(
        structure,
        psi,
        coef,
        CHANNELS,
        marks_exp,
        horizon,
        np.random.default_rng(seed),
        plan,
        max_events=max_events,
        on_cap=on_cap,
    )


def experiment(
    truth: Truth, design: Experiment, seed: int, max_events: int
) -> tuple[Dataset, float | None]:
    structure, psi, coef = truth
    return run_experiment_truncating(
        structure,
        psi,
        coef,
        CHANNELS,
        marks_exp,
        design,
        np.random.default_rng(seed),
        max_events=max_events,
        label="e0",
    )


def digest(ds: Dataset) -> str:
    """Every byte of a dataset that an instrument reads."""
    h = hashlib.sha256()
    h.update(ds.log.times.tobytes())
    for name in sorted(ds.log.marks):
        h.update(name.encode())
        h.update(ds.log.marks[name].tobytes())
    h.update(ds.endogenous.tobytes())
    h.update(repr([(a.hex(), b.hex()) for a, b in ds.excluded]).encode())
    h.update(ds.log.horizon.hex().encode())
    h.update(ds.label.encode())
    return h.hexdigest()


# --------------------------------------------------------------------------
# 1. A truncated run is the prefix of the same run under a larger cap
# --------------------------------------------------------------------------


def _assert_prefix(cut: sim.PlannedRun, full: sim.PlannedRun, n: int) -> None:
    assert cut.log.n == n
    assert cut.log.times.tobytes() == full.log.times[:n].tobytes()
    assert sorted(cut.log.marks) == sorted(full.log.marks)
    for name in full.log.marks:
        assert cut.log.marks[name].tobytes() == full.log.marks[name][:n].tobytes()
    assert cut.forced.tobytes() == full.forced[:n].tobytes()
    assert cut.clamped.tobytes() == full.clamped[:n].tobytes()
    assert cut.log.horizon == full.log.horizon
    assert cut.truncated_at == float(full.log.times[n - 1])


@pytest.mark.parametrize("n", [1, 37, 150, 260])
def test_truncated_run_is_the_prefix_of_the_uncapped_run(n: int) -> None:
    full = planned(size_excited(), PLAN, 300.0, seed=4, max_events=100_000)
    assert full.log.n > 260 and full.truncated_at is None
    cut = planned(size_excited(), PLAN, 300.0, 4, max_events=n, on_cap="truncate")
    _assert_prefix(cut, full, n)


def test_truncation_at_a_forced_event() -> None:
    """The N-th event may be forced: the run stops there too."""
    full = planned(size_excited(), PLAN, 300.0, seed=4, max_events=100_000)
    k = int(np.flatnonzero(full.forced)[2])  # the forced event at t = 90
    assert full.log.times[k] == 90.0
    cut = planned(size_excited(), PLAN, 300.0, 4, max_events=k + 1, on_cap="truncate")
    _assert_prefix(cut, full, k + 1)
    assert cut.forced[-1] and cut.truncated_at == 90.0


def test_a_run_with_exactly_n_events_is_truncated_at_the_nth() -> None:
    """The stop is at the N-th event whether or not an (N+1)-th would follow:
    that is what makes τ a stopping time (the likelihood depends on it)."""
    full = planned(size_excited(), PLAN, 300.0, seed=4, max_events=100_000)
    n = full.log.n
    cut = planned(size_excited(), PLAN, 300.0, 4, max_events=n, on_cap="truncate")
    _assert_prefix(cut, full, n)
    # Under "raise" the same cap is not exceeded, so nothing changes.
    same = planned(size_excited(), PLAN, 300.0, seed=4, max_events=n)
    assert same.truncated_at is None
    assert same.log.times.tobytes() == full.log.times.tobytes()


def test_below_the_cap_truncate_changes_nothing() -> None:
    full = planned(size_excited(), PLAN, 300.0, seed=4, max_events=100_000)
    also = planned(
        size_excited(), PLAN, 300.0, 4, max_events=full.log.n + 1, on_cap="truncate"
    )
    assert also.truncated_at is None
    assert also.log.times.tobytes() == full.log.times.tobytes()
    assert also.forced.tobytes() == full.forced.tobytes()
    assert also.clamped.tobytes() == full.clamped.tobytes()


def test_raise_is_still_the_default_at_the_cap() -> None:
    with pytest.raises(ExplosionError, match="more than 50 events"):
        planned(size_excited(), PLAN, 300.0, seed=4, max_events=50)


def test_on_cap_is_validated() -> None:
    with pytest.raises(InvalidSimulationInputError, match="on_cap"):
        planned(size_excited(), PLAN, 300.0, 4, 50, on_cap=cast(sim.OnCap, "stop"))
    with pytest.raises(InvalidSimulationInputError, match="max_events"):
        planned(size_excited(), PLAN, 300.0, 4, max_events=0, on_cap="truncate")


# --------------------------------------------------------------------------
# The dataset of a truncated experiment
# --------------------------------------------------------------------------


def test_truncated_experiment_dataset() -> None:
    n = 150
    full = planned(size_excited(), to_plan(EXPERIMENT), 300.0, 4, max_events=100_000)
    tau = float(full.log.times[n - 1])
    assert 120.0 < tau < 250.0, (
        "the design's censor lies before τ, a forced event after"
    )
    ds, at = experiment(size_excited(), EXPERIMENT, seed=4, max_events=n)
    assert at == tau
    # H is kept, and the tail after τ is excluded, merged with the design's
    # own windows; τ itself stays outside every excluded window.
    assert ds.log.horizon == 300.0
    tail = (math.nextafter(tau, math.inf), 300.0)
    assert ds.excluded == ((40.0, 50.0), (100.0, 120.0), tail)
    assert ds.log.times[-1] == tau
    # The observed log is the first n events minus the censored ones.
    first = full.log.times[:n]
    seen = ~((first >= 100.0) & (first < 120.0)) | full.forced[:n]
    assert ds.log.times.tobytes() == first[seen].tobytes()
    assert ds.endogenous.tobytes() == (~full.forced & ~full.clamped)[:n][seen].tobytes()
    # The forced event at 250 lies after τ, so it never happened.
    assert 250.0 not in ds.log.times


def test_tail_merges_with_a_window_that_straddles_tau() -> None:
    n = 150
    # Censoring is observation only, so the run is the unplanned one.
    full = planned(size_excited(), sim.Plan(), 300.0, seed=4, max_events=100_000)
    tau = float(full.log.times[n - 1])
    design = Experiment(Censor((tau - 1.0, tau + 1.0)), 300.0)
    ds, at = experiment(size_excited(), design, seed=4, max_events=n)
    assert at is not None
    assert ds.excluded == ((tau - 1.0, 300.0),)


# --------------------------------------------------------------------------
# 5. Below the cap: byte-identical to the pre-Δ2 behaviour
# --------------------------------------------------------------------------

#: :func:`digest` of ``run_experiment`` on the design below, computed on the
#: code before Δ2 (git 183da42). A change here means below-cap data changed.
BELOW_CAP_DIGEST: Final = (
    "b10272e98d668b1d0373c1de0e238a5e320fc397f5f6573f2edc671b4949ab0c"
)
BELOW_CAP_EVENTS: Final = 255


def test_below_the_cap_is_byte_identical_to_today() -> None:
    design = Experiment(
        Compose(
            (
                ForceEvents((5.0, 6.0), {"size": (2.0, 2.0)}),
                InjectMarks((10.0, 20.0), "sign", -1.0),
                Censor((30.0, 35.0)),
                ClampRate((40.0, 45.0), 4.0),
            )
        ),
        300.0,
    )
    structure, psi, coef = size_excited()
    old = run_experiment(
        structure,
        psi,
        coef,
        CHANNELS,
        marks_exp,
        design,
        np.random.default_rng(20261006),
        max_events=100_000,
        label="e0",
    )
    assert old.log.n == BELOW_CAP_EVENTS
    assert digest(old) == BELOW_CAP_DIGEST
    # The cap counts every event of the run, censored ones too: the smallest
    # cap the run stays below is one more than its full history.
    history = planned(size_excited(), to_plan(design), 300.0, 20261006, 100_000)
    assert history.log.n > BELOW_CAP_EVENTS
    for cap in (100_000, history.log.n + 1):
        new, at = experiment(size_excited(), design, seed=20261006, max_events=cap)
        assert at is None
        assert digest(new) == BELOW_CAP_DIGEST


# --------------------------------------------------------------------------
# 6. Determinism
# --------------------------------------------------------------------------


def test_truncated_runs_are_deterministic() -> None:
    a, at_a = experiment(trend_hawkes(), EXPERIMENT, seed=9, max_events=120)
    b, at_b = experiment(trend_hawkes(), EXPERIMENT, seed=9, max_events=120)
    c, at_c = experiment(trend_hawkes(), EXPERIMENT, seed=10, max_events=120)
    assert at_a is not None and at_b is not None and at_c is not None
    assert digest(a) == digest(b)
    assert at_a.hex() == at_b.hex()
    assert digest(a) != digest(c)


# --------------------------------------------------------------------------
# The candidate cap and the runaway guards still stop a run
# --------------------------------------------------------------------------


def test_runaway_exp_bound_still_raises_under_truncate() -> None:
    feature = Excite(KernelKind.EXP, One(), ALL)
    truth = (
        Structure((feature,), Link.EXP),
        (psi_for(feature, {"exp_rate": 1.0}),),
        Coefficients(701.0, ((0.0,),)),
    )
    with pytest.raises(ExplosionError, match="exp-link bound"):
        planned(truth, sim.Plan(), 10.0, 0, max_events=1_000, on_cap="truncate")


def test_runaway_window_still_raises_under_truncate() -> None:
    """A bound above _MAX_EXPECTED / _MIN_WINDOW: the runaway guard."""
    feature = Excite(KernelKind.EXP, One(), ALL)
    truth = (
        Structure((feature,), Link.IDENTITY),
        (psi_for(feature, {"exp_rate": 1.0}),),
        Coefficients(1e8, ((0.0,),)),
    )
    with pytest.raises(ExplosionError, match="runaway"):
        planned(truth, sim.Plan(), 10.0, 0, max_events=1_000, on_cap="truncate")


def test_candidate_cap_still_raises_under_truncate() -> None:
    """A loose bound: η = θ₀ + 20 sin + 20 cos is bounded by θ₀ + 40 on any
    window longer than a period, while its maximum is θ₀ + 28.3, so almost
    every candidate is rejected and the candidate cap (50 N + 10,000) is
    reached long before the N-th event."""
    truth = (
        Structure((Periodic(),), Link.EXP),
        ({PsiSlot((), "period"): 1.0},),
        Coefficients(-44.6, ((20.0, 20.0),)),
    )
    with pytest.raises(ExplosionError, match="thinning candidates"):
        planned(truth, sim.Plan(), 1e7, 0, max_events=5, on_cap="truncate")
