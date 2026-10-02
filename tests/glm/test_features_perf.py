"""Throughput guards for feature precomputation (slow tier).

The campaign fits thousands of structures, and Lomax history sums were the
measured bottleneck of the certified fitter (LOG.md 2026-10-02: 144 s of a
226 s fit). These tests pin the two structural speed-ups, so a regression
shows up as a failure rather than as a slower campaign: the Lomax sum of
exponentials against the direct O(n·m) sums, and leaf caching against per-ψ
recomputation. Ratios, not wall-clock limits, so machine load does not
decide the outcome.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from sciagent.glm.data import EventLog
from sciagent.glm.features import BlockCache, design, quadrature_rule
from sciagent.glm.grammar import (
    ALL,
    Excite,
    KernelKind,
    One,
    Periodic,
    Product,
    PsiSlot,
    Structure,
)
from sciagent.glm.grids import grid


def _log(n: int) -> EventLog:
    rng = np.random.default_rng(5)
    times = np.cumsum(rng.exponential(1.0, size=n))
    return EventLog.create(times, {}, float(times[-1]) + 1.0)


@pytest.mark.slow
def test_fast_lomax_design_is_much_faster_than_direct_sums() -> None:
    log = _log(2000)
    structure = Structure((Excite(KernelKind.POWER, One(), ALL),))
    psi = ({PsiSlot((0,), "power_c"): 0.05, PsiSlot((0,), "power_p"): 1.2},)
    start = time.perf_counter()
    fast = design(structure, psi, log, ())
    t_fast = time.perf_counter() - start
    start = time.perf_counter()
    exact = design(structure, psi, log, (), exact=True)
    t_exact = time.perf_counter() - start
    np.testing.assert_allclose(fast.at_nodes, exact.at_nodes, rtol=1e-9, atol=0.0)
    # Measured ≈ 28x at n = 5000; the gap grows with n.
    assert t_exact > 4.0 * t_fast, (t_fast, t_exact)


@pytest.mark.slow
def test_product_profile_costs_a_sum_over_leaves() -> None:
    """A full grid of ``Product(PowerK, Periodic)``: 60 blocks from 12 + 5
    leaves, much faster than 60 independent block computations would be."""
    log = _log(1500)
    feature = Product(Excite(KernelKind.POWER, One(), ALL), Periodic())
    nodes, weights = quadrature_rule(Structure((feature,)), log)
    cache = BlockCache(log, (), nodes, weights)
    points = [
        {
            PsiSlot((0, 0), "power_c"): c,
            PsiSlot((0, 0), "power_p"): p,
            PsiSlot((1,), "period"): period,
        }
        for c in grid("power_c")
        for p in grid("power_p")
        for period in grid("period")
    ]
    start = time.perf_counter()
    for psi in points:
        cache.block(feature, psi)
    t_all = time.perf_counter() - start
    assert cache.n_leaf_evaluations == 12 + 5
    start = time.perf_counter()
    BlockCache(log, (), nodes, weights).block(feature, points[0])
    t_one = time.perf_counter() - start
    # Without leaf caching, 60 points would cost ≈ 60 single blocks.
    assert t_all < 30.0 * t_one, (t_all, t_one)
