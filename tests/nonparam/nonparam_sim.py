"""An exact, independent simulator for the Wiener-Hopf tests.

It deliberately does not use ``sciagent.glm.simulate``: the estimator's tests
must not share code with the thing they will later be compared against.

The cluster (branching / Poisson-cluster) representation of a linear marked
Hawkes process: immigrants arrive as a Poisson(μ) process; every event draws an
iid mark ``m ~ Exp(1)`` (so ``z = m - 1`` under the size channel's
standardisation ``location = 1, scale = 1``, and ``z ≥ -1``), and has
``Poisson(η (1 + a z))`` children at lags drawn iid from the kernel density h.
For ``0 ≤ a ≤ 1`` the offspring mean is non-negative. The ground intensity is
then exactly

``λ(t) = μ + Σ_{t_j < t} η (1 + a z_j) h(t - t_j)``,

i.e. ``φ_arrival = η h`` and ``φ_size = η a h`` on the standardised mark, with
branching ratio ``η`` (because ``E z = 0``) and mean rate ``μ / (1 - η)``.

The process is simulated from ``-burn_in`` and cut to ``[0, horizon]`` so the
retained window is close to stationary. An optional sign channel carries iid
±1 marks that have no effect.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from sciagent.glm.data import EventLog
from sciagent.glm.grammar import ChannelKind, ChannelSpec

type Floats = npt.NDArray[np.float64]
type LagSampler = Callable[[np.random.Generator, int], Floats]
type Cdf = Callable[[Floats], Floats]

SIZE = ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0)
SIGN = ChannelSpec("sign", ChannelKind.SIGN, location=0.0, scale=1.0)


def exp_sampler(beta: float) -> LagSampler:
    def sample(rng: np.random.Generator, k: int) -> Floats:
        return rng.exponential(1.0 / beta, size=k)

    return sample


def exp_cdf(beta: float) -> Cdf:
    def cdf(t: Floats) -> Floats:
        return -np.expm1(-beta * t)

    return cdf


def lomax_sampler(c: float, p: float) -> LagSampler:
    """Inverse-CDF draws from ``((p-1)/c)(1 + t/c)^{-p}``."""

    def sample(rng: np.random.Generator, k: int) -> Floats:
        u = rng.random(size=k)
        return c * ((1.0 - u) ** (-1.0 / (p - 1.0)) - 1.0)

    return sample


def lomax_cdf(c: float, p: float) -> Cdf:
    def cdf(t: Floats) -> Floats:
        return 1.0 - (1.0 + t / c) ** (-(p - 1.0))

    return cdf


def simulate(
    rng: np.random.Generator,
    *,
    mu: float,
    eta: float,
    lags: LagSampler,
    horizon: float,
    mark_coef: float = 0.0,
    burn_in: float = 100.0,
    with_sign: bool = False,
) -> EventLog:
    """One realisation on ``[0, horizon]`` of the process described above."""
    if not 0.0 <= mark_coef <= 1.0:
        raise ValueError("mark_coef must be in [0, 1] for non-negative offspring")
    start = -burn_in
    n_imm = int(rng.poisson(mu * (horizon - start)))
    gen_times = rng.uniform(start, horizon, size=n_imm)
    all_times: list[Floats] = []
    all_sizes: list[Floats] = []
    while gen_times.size:
        sizes = rng.exponential(1.0, size=gen_times.size)
        all_times.append(gen_times)
        all_sizes.append(sizes)
        z = sizes - SIZE.location
        counts = rng.poisson(eta * (1.0 + mark_coef * z))
        parents = np.repeat(gen_times, counts)
        children = parents + lags(rng, int(counts.sum()))
        gen_times = children[children <= horizon]
    times = np.concatenate(all_times) if all_times else np.zeros(0)
    size = np.concatenate(all_sizes) if all_sizes else np.zeros(0)
    order = np.argsort(times, kind="stable")
    times, size = times[order], size[order]
    keep = times >= 0.0
    times, size = times[keep], size[keep]
    marks: dict[str, Floats] = {"size": size}
    if with_sign:
        marks["sign"] = rng.choice(np.array([-1.0, 1.0]), size=times.size)
    return EventLog.create(times, marks, horizon)
