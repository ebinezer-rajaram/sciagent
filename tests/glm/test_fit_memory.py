"""The fitter's feature-block cache is bounded and only memoises (fit/5).

fit/5's ψ search kept every feature block it built: on the dev-013 superset
(three long datasets) that passed 11 GB (LOG 2026-10-06). The blocks now live
in a byte-bounded LRU (``FitConfig.block_cache_bytes``); an evicted block is
rebuilt from the leaf cache, byte for byte. So:

- **Equality.** The fit (every reported number, and the sequence of ψ points
  the search evaluates) is byte-identical under a zero, a tiny and an
  unbounded cache, on both search paths, every link and several datasets;
  the exhaustive path still matches the pinned fit/4 digests.
- **Key.** The bound cannot change a result, so it is not in ``key()`` and
  not in the fit's content address.
- **Memory.** On a mid-size three-dataset coordinate fit, the traced peak
  stays under a threshold with a small bound, and (positive control) the
  same fit with an unbounded cache exceeds it.
"""

from __future__ import annotations

import tracemalloc
from collections.abc import Iterator, Sequence

import pytest
from test_fit_search import (
    CHANNELS,
    EXP_K,
    FIT4_DIGESTS,
    POWER_K,
    SIZE_K,
    WINDOW,
    fit_bytes,
    fit_digest,
    hawkes_log,
    obs,
    periodic_log,
    reference_cases,
)

from sciagent.glm import fit as fit_module
from sciagent.glm.canonical import canonicalise
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, FitConfigError, fit, fit_cache_key
from sciagent.glm.grammar import Link, Structure
from sciagent.glm.syntax import parse

#: "No bound" for the reference fits: larger than any fit here can fill.
UNBOUNDED = 1 << 62
#: A bound below one block of any case here, and one that holds a few.
BOUNDS = (0, 1 << 16, 1 << 20)

DEV013_BASE = (
    "link=exp; Gate(Gate(Periodic, PhaseWindow), PhaseWindow) + "
    "Gate(Product(Periodic, Periodic), PhaseWindow)"
)


def three_datasets(horizon: float) -> tuple[Dataset, ...]:
    return tuple(
        Dataset.observational(periodic_log(horizon, seed), f"d{seed}")
        for seed in (3, 4, 5)
    )


@pytest.fixture
def trajectory(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[object, ...]]]:
    """Every batch of ψ points the search asks to evaluate, in order."""
    calls: list[tuple[object, ...]] = []
    original = fit_module._Profile.evaluate

    def spy(self: fit_module._Profile, points: Sequence[tuple[int, ...]]) -> None:
        calls.append((self.index, tuple(points)))
        original(self, points)

    monkeypatch.setattr(fit_module._Profile, "evaluate", spy)
    yield calls


def assert_bound_free(
    structure: Structure,
    data: tuple[Dataset, ...],
    trajectory: list[tuple[object, ...]],
    bounds: Sequence[int] = BOUNDS,
) -> None:
    reference = fit(
        structure, data, CHANNELS, config=FitConfig(block_cache_bytes=UNBOUNDED)
    )
    path = list(trajectory)
    for bound in bounds:
        trajectory.clear()
        bounded = fit(
            structure, data, CHANNELS, config=FitConfig(block_cache_bytes=bound)
        )
        assert fit_bytes(bounded) == fit_bytes(reference), bound
        assert trajectory == path, bound


# --------------------------------------------------------------------------
# The bound is not part of a result
# --------------------------------------------------------------------------


def test_block_cache_bound_is_not_in_the_key() -> None:
    data = obs(periodic_log())
    structure = Structure((EXP_K,), Link.EXP)
    keys = {FitConfig(block_cache_bytes=b).key() for b in (0, 1, 1 << 30, UNBOUNDED)}
    assert keys == {FitConfig().key()}
    addresses = {
        fit_cache_key(structure, data, CHANNELS, FitConfig(block_cache_bytes=b))
        for b in (0, UNBOUNDED)
    }
    assert addresses == {fit_cache_key(structure, data, CHANNELS)}
    with pytest.raises(FitConfigError):
        FitConfig(block_cache_bytes=-1)


@pytest.mark.parametrize("name", sorted(reference_cases()))
def test_exhaustive_path_without_a_block_cache_is_fit4(name: str) -> None:
    structure, data = reference_cases()[name]
    result = fit(structure, data, CHANNELS, config=FitConfig(block_cache_bytes=0))
    assert result.psi_search == "exhaustive"
    assert fit_digest(result) == FIT4_DIGESTS[name]


def test_coordinate_fit_is_independent_of_the_bound(
    trajectory: list[tuple[object, ...]],
) -> None:
    structure = Structure((EXP_K, SIZE_K, WINDOW), Link.EXP)
    assert_bound_free(structure, obs(periodic_log(200.0, 4)), trajectory)


@pytest.mark.slow
@pytest.mark.parametrize("link", list(Link), ids=lambda link: link.value)
def test_coordinate_fit_is_independent_of_the_bound_per_link(
    link: Link, trajectory: list[tuple[object, ...]]
) -> None:
    structure = Structure((POWER_K, EXP_K, WINDOW), link)
    data = (
        Dataset.observational(hawkes_log(200.0, 1), "a"),
        Dataset.observational(hawkes_log(150.0, 5), "b"),
    )
    assert_bound_free(structure, data, trajectory, bounds=(0, 1 << 16))


@pytest.mark.slow
def test_dev013_like_fit_is_independent_of_the_bound(
    trajectory: list[tuple[object, ...]],
) -> None:
    """The dev-013 superset's structure on three (short) datasets."""
    structure = canonicalise(parse(DEV013_BASE + " + Excite(ExpK, One, all)", CHANNELS))
    assert_bound_free(structure, three_datasets(50.0), trajectory, bounds=(0, 1 << 20))


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------


def traced_peak(structure: Structure, data: tuple[Dataset, ...], bound: int) -> int:
    tracemalloc.start()
    try:
        fit(structure, data, CHANNELS, config=FitConfig(block_cache_bytes=bound))
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.slow
def test_block_cache_bound_caps_peak_memory() -> None:
    """Measured at the fix: unbounded blocks ~95 MiB on this case (they grow
    with the points evaluated); with a 4 MiB bound the peak is the bound plus
    one point's working set and the leaves."""
    structure = canonicalise(
        parse(
            "link=exp; Gate(Gate(Periodic, PhaseWindow), PhaseWindow) + "
            "Excite(ExpK, One, all)",
            CHANNELS,
        )
    )
    data = three_datasets(200.0)
    threshold = 40 << 20
    bounded = traced_peak(structure, data, 4 << 20)
    unbounded = traced_peak(structure, data, UNBOUNDED)
    assert bounded < threshold, bounded / 2**20
    # Positive control: the instrument separates a bounded from an unbounded
    # cache on this case.
    assert unbounded > 2 * threshold, unbounded / 2**20
