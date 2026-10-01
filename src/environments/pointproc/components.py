"""Executable semantics for the point-process families (v1 SPEC §4.1-4.2).

Every variate is derived from ``rng.random()`` by explicit inverse-CDF or
thinning. None of numpy's distribution methods is called. NumPy's versioning
policy guarantees stream stability for ``RandomState`` but *not* for
``Generator``'s distribution methods, so calling ``Generator.exponential`` would
make bit-exact reproducibility (v1 SPEC §6.1 A1) hostage to a numpy upgrade.
``Generator.random`` is a direct function of the raw 64-bit PCG64 output and is
stable, and the transforms here are visible and auditable rather than hidden in
a C extension.

Arrival components return an *absolute* time; the executor's ``self_history``
supplies the previous arrival times, so no mutable simulator state is needed.
"""

from __future__ import annotations

import math

import numpy as np

from sciagent.core import reductions
from sciagent.core.errors import ExecutionError
from sciagent.core.program import DrawContext, FamilyLibrary
from sciagent.core.types import (
    ComponentId,
    FamilyId,
    Floats,
    FrozenDict,
    LatentSpecId,
    Parameters,
)

# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------

ARRIVAL = ComponentId("arrival")
SIZE = ComponentId("size")
SIGN = ComponentId("sign")
OBS = ComponentId("obs")

POISSON_HOMOGENEOUS = FamilyId("poisson_homogeneous")
HAWKES_EXPONENTIAL = FamilyId("hawkes_exponential")
POISSON_PERIODIC = FamilyId("poisson_periodic")
MIXTURE_OF_POISSON_2 = FamilyId("mixture_of_poisson_2")
POISSON_MODULATED_2STATE = FamilyId("poisson_modulated_2state")
SIZE_EXCITED_EXPONENTIAL = FamilyId("size_excited_exponential")

EXPONENTIAL = FamilyId("exponential")
MIXTURE_OF_EXPONENTIAL_2 = FamilyId("mixture_of_exponential_2")

IID_BERNOULLI = FamilyId("iid_bernoulli")
IDENTITY = FamilyId("identity")
IDENTITY_PERIODIC_CENSORED = FamilyId("identity_periodic_censored")

TWO_STATE_MARKOV = LatentSpecId("two_state_markov")

#: Guard on thinning loops. A correct parameterisation accepts a candidate with
#: probability ``intensity/intensity_max``, bounded well away from zero, so this
#: is only ever reached if a family is misparameterised.
_MAX_THINNING_STEPS = 100_000

#: History older than ``_KERNEL_CUTOFF / decay`` is dropped from excitation sums.
#: The discarded terms are suppressed by ``exp(-_KERNEL_CUTOFF)``, about 4e-18,
#: which is below double precision's relative resolution of 1.1e-16, so the
#: truncated sum is the correctly rounded value of the full sum. This is a
#: deterministic rule, not a sampling shortcut: the cutoff index is a function of
#: the evaluation time alone. Without it every excitation evaluation would be
#: linear in the event count and simulation quadratic, which puts the run lengths
#: needed to measure confounding out of reach.
_KERNEL_CUTOFF = 40.0


def _recent(times: Floats, now: float, decay: float) -> int:
    """Return the first index of ``times`` whose kernel contribution survives."""
    return int(np.searchsorted(times, now - _KERNEL_CUTOFF / decay, side="left"))


# --------------------------------------------------------------------------
# Inverse-CDF primitives
# --------------------------------------------------------------------------


def exponential_variate(rng: np.random.Generator, rate: float) -> float:
    """Return an Exponential(``rate``) draw by inverse CDF.

    Uses ``-log1p(-u)`` rather than ``-log(1-u)``: ``u`` is in ``[0, 1)`` so the
    argument is in ``(0, 1]`` and the result is always finite, with full
    precision retained for small ``u``.
    """
    if rate <= 0.0 or not math.isfinite(rate):
        raise ExecutionError(
            f"exponential rate must be finite and positive, got {rate}"
        )
    return -math.log1p(-rng.random()) / rate


def bernoulli_variate(rng: np.random.Generator, probability: float) -> float:
    """Return 1.0 with probability ``probability``, else 0.0."""
    return 1.0 if rng.random() < probability else 0.0


def _previous_time(context: DrawContext) -> float:
    return float(context.self_history[-1]) if context.index else 0.0


# --------------------------------------------------------------------------
# Arrival families
# --------------------------------------------------------------------------


def arrival_poisson_homogeneous(context: DrawContext) -> float:
    """Homogeneous Poisson process: inter-arrival times are iid Exponential."""
    return _previous_time(context) + exponential_variate(
        context.rng, context.parameter("rate")
    )


def _hawkes_intensity(
    time: float, history: Floats, base_rate: float, branching: float, decay: float
) -> float:
    if history.size == 0:
        return base_rate
    recent = history[_recent(history, time, decay) :]
    if recent.size == 0:
        return base_rate
    # `reductions.total`, not `np.sum`: this fold is inside the *simulation*, so
    # a summation order chosen by the CPU changes the intensity, which changes
    # the next arrival time, which changes the event log itself. Every metric is
    # computed downstream of that log, so a wobble here is the one that reaches
    # furthest -- which is why it is worth an exact fold even on one machine,
    # where the order can still move under a numpy upgrade.
    return base_rate + branching * decay * reductions.total(
        np.exp(-decay * (time - recent))
    )


def arrival_hawkes_exponential(context: DrawContext) -> float:
    """Hawkes process with an exponential kernel.

    Intensity ``lambda(t) = base_rate + sum over past events of
    branching*decay*exp(-decay*(t - t_j))``. ``branching`` is the branching
    ratio: the kernel integrates to exactly ``branching``, so the process is
    stationary for ``branching < 1`` and its mean rate is
    ``base_rate/(1 - branching)``.

    Simulated by Ogata thinning against the intensity at the current time, which
    dominates the intensity over the whole waiting interval because the kernel
    decays monotonically between events.
    """
    base_rate = context.parameter("base_rate")
    branching = context.parameter("branching")
    decay = context.parameter("decay")
    history = context.self_history
    time = _previous_time(context)
    for _ in range(_MAX_THINNING_STEPS):
        ceiling = _hawkes_intensity(time, history, base_rate, branching, decay)
        time += exponential_variate(context.rng, ceiling)
        intensity = _hawkes_intensity(time, history, base_rate, branching, decay)
        if context.rng.random() * ceiling <= intensity:
            return time
    raise ExecutionError("Hawkes thinning failed to accept; check parameterisation")


def arrival_poisson_periodic(context: DrawContext) -> float:
    """Inhomogeneous Poisson process with a deterministic periodic rate.

    ``lambda(t) = base_rate * exp(amplitude * cos(2*pi*t/period))``.

    An exponentiated cosine rather than ``base_rate*(1 + A*sin(.))``: the linear
    form is bounded by ``A < 1`` and cannot reach the inter-arrival dispersion
    the other three mechanisms produce, which would make seasonality trivially
    separable and defeat the point of v1 SPEC §4.2. The exponentiated form has the
    same two free parameters and unbounded dispersion.

    ``base_rate`` is the *geometric*-mean rate; the arithmetic mean rate is
    ``base_rate * I0(amplitude)``. The Bessel factor is deliberately not applied
    here, keeping ``scipy.special`` out of the bit-exact generation path.
    """
    base_rate = context.parameter("base_rate")
    amplitude = context.parameter("amplitude")
    period = context.parameter("period")
    if period <= 0.0:
        raise ExecutionError(f"period must be positive, got {period}")
    ceiling = base_rate * math.exp(amplitude)
    time = _previous_time(context)
    for _ in range(_MAX_THINNING_STEPS):
        time += exponential_variate(context.rng, ceiling)
        intensity = base_rate * math.exp(
            amplitude * math.cos(2.0 * math.pi * time / period)
        )
        if context.rng.random() * ceiling <= intensity:
            return time
    raise ExecutionError("periodic thinning failed to accept; check parameterisation")


def arrival_mixture_of_poisson_2(context: DrawContext) -> float:
    """Two-component Poisson mixture: the rate is redrawn iid at every event.

    Inter-arrival times are hyperexponential, so they are overdispersed while
    remaining independent: the counting process is a renewal process with no
    temporal correlation and no response to a forced arrival, which is what
    separates this mechanism from the other three (v1 SPEC §4.2).
    """
    weight_high = context.parameter("weight_high")
    rate = (
        context.parameter("rate_high")
        if context.rng.random() < weight_high
        else context.parameter("rate_low")
    )
    return _previous_time(context) + exponential_variate(context.rng, rate)


def arrival_poisson_modulated_2state(context: DrawContext) -> float:
    """Markov-modulated Poisson process driven by a two-state latent regime.

    The latent alternates between a low and a high regime in continuous time.
    Rates are ``rate * mult_low`` and ``rate * mult_high``; the regime's holding
    rates are ``switch_rate*p_high`` out of low and ``switch_rate*(1 - p_high)``
    out of high, so ``p_high`` is exactly the stationary probability of the high
    regime and ``switch_rate`` sets the timescale independently of it.

    Exact simulation by competing exponentials: the next event and the next
    regime change are raced, and regime changes are absorbed until an event
    wins. Event draws come from the component's stream and regime draws from the
    latent's own stream, so the two are independent and each is reproducible on
    its own.
    """
    latent = context.component.latents
    if not latent or latent[0].id != TWO_STATE_MARKOV:
        raise ExecutionError(
            f"{POISSON_MODULATED_2STATE} requires a {TWO_STATE_MARKOV} latent"
        )
    spec = latent[0]
    key = spec.state_key(context.component.id)
    regime_rng = context.latent_rngs[TWO_STATE_MARKOV]

    base_rate = context.parameter("rate")
    mult_low = spec.parameters["mult_low"]
    mult_high = spec.parameters["mult_high"]
    switch_rate = spec.parameters["switch_rate"]
    p_high = spec.parameters["p_high"]

    state = context.latent_state[key]
    time = _previous_time(context)
    for _ in range(_MAX_THINNING_STEPS):
        is_high = state >= 0.5
        rate = base_rate * (mult_high if is_high else mult_low)
        leaving = switch_rate * (1.0 - p_high) if is_high else switch_rate * p_high
        wait_event = exponential_variate(context.rng, rate)
        wait_switch = exponential_variate(regime_rng, leaving)
        if wait_event <= wait_switch:
            context.latent_state[key] = state
            return time + wait_event
        time += wait_switch
        state = 0.0 if is_high else 1.0
        context.latent_state[key] = state
    raise ExecutionError("regime simulation exceeded its switch budget")


def arrival_size_excited_exponential(context: DrawContext) -> float:
    """Arrival rate excited by the *sizes* of preceding events (scenario S11).

    ``lambda(t) = base_rate + sum over past events of
    excitation*size_j*decay*exp(-decay*(t - t_j))``.

    Structurally a Hawkes process whose marks gate the excitation. It reaches
    ``arrival`` through a lagged edge from ``size``, which is why it is absent
    from the agent grammar and therefore out-of-library: the agent grammar
    licenses self-loops only.
    """
    base_rate = context.parameter("base_rate")
    excitation = context.parameter("excitation")
    decay = context.parameter("decay")
    times = context.self_history
    sizes = context.history[SIZE]
    if sizes.size != times.size:
        raise ExecutionError(
            f"size history has {sizes.size} entries but arrival history has "
            f"{times.size}"
        )

    def intensity_at(time: float) -> float:
        if times.size == 0:
            return base_rate
        start = _recent(times, time, decay)
        if start >= times.size:
            return base_rate
        # See `_hawkes_intensity` on why this fold may not be `np.sum`. This is
        # S11's out-of-library mechanism, so its event log is the one the whole
        # out-of-library evaluation rests on.
        return base_rate + excitation * decay * reductions.total(
            sizes[start:] * np.exp(-decay * (time - times[start:]))
        )

    time = _previous_time(context)
    for _ in range(_MAX_THINNING_STEPS):
        ceiling = intensity_at(time)
        time += exponential_variate(context.rng, ceiling)
        if context.rng.random() * ceiling <= intensity_at(time):
            return time
    raise ExecutionError("size-excited thinning failed to accept")


# --------------------------------------------------------------------------
# Size, sign and observation families
# --------------------------------------------------------------------------


def size_exponential(context: DrawContext) -> float:
    """Exponential mark sizes with mean ``mean``."""
    return exponential_variate(context.rng, 1.0 / context.parameter("mean"))


def size_mixture_of_exponential_2(context: DrawContext) -> float:
    """Two-component exponential mixture of mark sizes (scenario S8)."""
    weight_high = context.parameter("weight_high")
    mean = (
        context.parameter("mean_high")
        if context.rng.random() < weight_high
        else context.parameter("mean_low")
    )
    return exponential_variate(context.rng, 1.0 / mean)


def sign_iid_bernoulli(context: DrawContext) -> float:
    """Independent signs: 1.0 with probability ``p``, else 0.0."""
    return bernoulli_variate(context.rng, context.parameter("p"))


def observation_identity(context: DrawContext) -> float:
    """Signed mark size: ``size * (2*sign - 1)``. Deterministic given its parents."""
    try:
        size = context.parents[SIZE]
        sign = context.parents[SIGN]
    except KeyError as exc:
        raise ExecutionError(
            f"identity observation requires instantaneous parents {SIZE!r} and {SIGN!r}"
        ) from exc
    return size * (2.0 * sign - 1.0)


def observation_periodic_censored(context: DrawContext) -> float:
    """Signed mark size, under an observation window that opens and closes.

    Scenario S12's nuisance. The *value* is the identity family's: an event whose
    record is lost still happened, and its mark is still drawn, so nothing about
    the generative process changes. What changes is which events reach a
    measurement, and that is not a property of a draw at all -- it is a property
    of the record. This environment's one place for that is the restriction an
    :class:`~sciagent.experiments.executor.OperationCompiler` returns, so this
    family *declares* the observation process, in ``period`` and ``duty``, and
    :func:`environments.pointproc.operations.compiler` realises it.

    Writing a sentinel here instead was rejected: it would put a number into the
    log that every metric would have to know not to read, and a metric is a pure
    function of a log by specification (v1 SPEC §3.2).
    """
    for name in ("period", "duty"):
        if name not in context.component.parameters:
            raise ExecutionError(
                f"{IDENTITY_PERIODIC_CENSORED} requires parameter {name!r}; a "
                f"censoring window that is not declared cannot be applied"
            )
    return observation_identity(context)


# --------------------------------------------------------------------------
# Latent initialisers
# --------------------------------------------------------------------------


def init_two_state_markov(parameters: Parameters, rng: np.random.Generator) -> float:
    """Draw the initial regime from its stationary distribution.

    Starting stationary rather than always-low removes a transient that would
    otherwise make early events diagnostic of the mechanism for reasons that
    have nothing to do with the mechanism.
    """
    return bernoulli_variate(rng, parameters["p_high"])


# --------------------------------------------------------------------------
# The library
# --------------------------------------------------------------------------

#: Bumped to 1.1.0 at backlog item 11, which added
#: :data:`IDENTITY_PERIODIC_CENSORED` for scenario S12. The version enters
#: ``ENV_VERSION`` and therefore every registered experiment's content address:
#: a library that can execute a programme the previous one could not is a
#: different library, whether or not any existing programme's behaviour moved.
#:
#: Bumped to 1.2.0 on 2026-08-15, when both Hawkes intensity kernels moved from
#: ``np.sum`` to :func:`sciagent.core.reductions.total`. Unlike the 1.1.0 bump
#: this one *does* move existing behaviour: the intensity is what the thinning
#: loop compares against, so an event log drawn under 1.2.0 differs from one
#: drawn under 1.1.0 in the last places, and every stored result computed from
#: one is retired. That is the intended effect -- see ``docs/v1/DECISIONS.md`` on
#: the Windows/Ubuntu measurement that made these folds a defect.
LIBRARY_VERSION = "1.2.0"

LIBRARY = FamilyLibrary(
    name="pointproc",
    version=LIBRARY_VERSION,
    kernels=FrozenDict(
        {
            POISSON_HOMOGENEOUS: arrival_poisson_homogeneous,
            HAWKES_EXPONENTIAL: arrival_hawkes_exponential,
            POISSON_PERIODIC: arrival_poisson_periodic,
            MIXTURE_OF_POISSON_2: arrival_mixture_of_poisson_2,
            POISSON_MODULATED_2STATE: arrival_poisson_modulated_2state,
            SIZE_EXCITED_EXPONENTIAL: arrival_size_excited_exponential,
            EXPONENTIAL: size_exponential,
            MIXTURE_OF_EXPONENTIAL_2: size_mixture_of_exponential_2,
            IID_BERNOULLI: sign_iid_bernoulli,
            IDENTITY: observation_identity,
            IDENTITY_PERIODIC_CENSORED: observation_periodic_censored,
        }
    ),
    latent_inits=FrozenDict({TWO_STATE_MARKOV: init_two_state_markov}),
)
