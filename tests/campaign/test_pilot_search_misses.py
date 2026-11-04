"""``scripts/pilot_search_misses.py``: the search-miss check (SPEC §6.3 instrument).

The rule is the pre-registration ``docs/v2/prereg/2026-11-04_pilot-search-misses.md``
(section 2.10 lists what these tests must cover). No test here touches a pilot
unit: every table, store and log is a toy, and the seed root the launch owns
(20261104) is never run; ``reference_rng`` takes its root as an argument, and
every test passes :data:`TEST_ROOT`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib
import importlib.util
import inspect
import json
import math
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Final

import numpy as np
import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.plan import baseline_key, baseline_units
from sciagent.campaign.store import ResultStore
from sciagent.glm.canonical import canonicalise
from sciagent.glm.data import Dataset
from sciagent.glm.fit import (
    FitConfig,
    _ascend,
    _Profile,
    _search_blocks,
    _Slots,
    fit,
)
from sciagent.glm.grammar import (
    ALL,
    Excite,
    Feature,
    Gate,
    KernelKind,
    Link,
    One,
    Periodic,
    PhaseWindow,
    Structure,
    n_columns,
    psi_slots,
)
from sciagent.glm.grids import grid
from sciagent.glm.simulate import Coefficients, simulate
from sciagent.glm.syntax import parse, render
from sciagent.scoring import gonogo
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.sparse import SparseConfig, _choose_link, split_dataset

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
psm: Any = importlib.import_module("pilot_search_misses")

#: Every R1 in this file runs under this root. It is not the launch's root.
TEST_ROOT: Final = 20261107
#: Every hunt log here is drawn under this root, not the launch's 20261106.
TEST_LOG_ROOT: Final = 20261108

EXP_K: Final = Excite(KernelKind.EXP, One(), ALL)
SMALL: Final = (EXP_K, Periodic())  # 6 · 5 = 30 points, sweep 11
MIDDLE: Final = (EXP_K, Gate(Periodic(), PhaseWindow()))  # 600 points, sweep 106


def test_the_test_roots_are_not_the_launchs_roots() -> None:
    assert psm.SEED_ROOT == 20261104
    assert psm.HUNT_LOG_ROOT == 20261106
    assert {TEST_ROOT, TEST_LOG_ROOT}.isdisjoint({psm.SEED_ROOT, psm.HUNT_LOG_ROOT})


# --------------------------------------------------------------------------
# Toy logs
# --------------------------------------------------------------------------


def structure_of(
    features: tuple[Feature, ...] = SMALL, link: Link = Link.SOFTPLUS
) -> Structure:
    return Structure(features, link)


@cache
def toy_data(
    features: tuple[Feature, ...] = SMALL, link: Link = Link.SOFTPLUS, seed: int = 3
) -> tuple[Dataset, ...]:
    structure = structure_of(features, link)
    rng = np.random.default_rng(seed)
    psi = tuple(
        {s: grid(s.name)[int(rng.integers(len(grid(s.name))))] for s in psi_slots(f)}
        for f in structure.features
    )
    coef = Coefficients(0.3, ((-1.0,), (0.4, -0.3)))
    log = simulate(structure, psi, coef, psm.T2_CHANNELS, psm.t2_marks, 60.0, rng)
    return (Dataset.observational(log),)


def toy_search(*, k: int = 2, cap: int = 10**6, r2_max: int = 0, **kw: Any) -> Any:
    return psm.reference_search(
        structure_of(),
        toy_data(),
        psm.T2_CHANNELS,
        psm.reference_rng("toy", TEST_ROOT),
        k=k,
        cap=cap,
        r2_max=r2_max,
        **kw,
    )


# --------------------------------------------------------------------------
# Constants fixed by the text
# --------------------------------------------------------------------------


def test_constants_are_the_texts() -> None:
    assert psm.SEED_ROOT == 20261104
    assert psm.K_STARTS == 48
    assert psm.S2_STARTS == 16
    assert psm.R1_POINT_CAP == 8_000
    assert psm.R1_POINT_CAP_IDENTITY == 3_000
    assert psm.S2_POINT_CAP == 2_000
    assert psm.JOB_CUT_HOURS == 2.5
    assert psm.R2_GRID_MAX == 14_400
    assert psm.TOL_FLOOR == 3e-7
    assert psm.H3_LIMIT == gonogo.BSYM_TOLERANCE == 0.01
    assert psm.VERDICT_SEED == 20261002
    assert psm.VERDICT_RESAMPLES == 10_000
    assert psm.CPU_STOP_HOURS == 78
    assert psm.JOB_CPU_HOURS == 3
    assert psm.CPU_CEILING_HOURS == 84
    assert psm.CPU_STOP_HOURS + 2 * psm.JOB_CPU_HOURS == psm.CPU_CEILING_HOURS
    assert psm.WALL_HOURS == 60
    assert psm.MEM_GATE_GIB == 8
    assert psm.BASE_COMMIT == "900e66a"
    assert psm.EXPECTED_ROWS == 670
    assert psm.SPLIT_SHA256 == (
        "31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577"
    )
    assert psm.S1_MIN_MISSES == 5
    assert psm.S1_RECALL_FLOOR == 0.8
    assert psm.HUNT_SIZE == 150
    assert psm.HUNT_R1_PER_STRATUM == 25
    assert psm.HUNT_CPU_HOURS == 6.0
    assert psm.HUNT_HORIZON == 300.0


@pytest.mark.parametrize(
    ("link", "cap"),
    [("exp", 8_000), ("softplus", 8_000), ("identity", 3_000)],
)
def test_r1s_cap_is_per_link(link: str, cap: int) -> None:
    assert psm.r1_cap(link) == cap


def test_group_a_is_the_texts_six_cases() -> None:
    assert psm.T2_CASES == (
        (0, "softplus", 103648458),
        (2, "exp", 1499614403),
        (0, "softplus", 1074),
        (0, "softplus", 3273957339),
        (0, "softplus", 358866363),
        (0, "softplus", 2006407976),
    )
    assert psm.GROUP_B == ("dev-015", 3)


def test_guarded_paths_are_the_texts_list() -> None:
    assert psm.GUARDED_PATHS == (
        "src/sciagent/glm",
        "src/sciagent/core",
        "src/sciagent/scoring",
        "src/sciagent/systems",
        "src/sciagent/scenarios",
        "src/sciagent/campaign/analysis.py",
        "src/sciagent/campaign/execute.py",
        "src/sciagent/campaign/plan.py",
        "src/environments",
        "scripts/pilot.py",
        "pyproject.toml",
        "uv.lock",
    )


# --------------------------------------------------------------------------
# Seeds (section 2.2)
# --------------------------------------------------------------------------


def test_case_strings_are_the_texts() -> None:
    assert psm.fit_case("dev-001", 2, "Base ψ") == "dev-001|2|Base ψ"
    assert psm.t2_case(0, "softplus", 1074) == "t2|0|softplus|1074"
    assert psm.hunt_case("identity", 3) == "hunt|identity|3"
    assert psm.hunt_index("id-24000", 3) == 3
    assert psm.hunt_index("large-exp", 3) == 2003


@pytest.mark.parametrize(
    "c",
    [
        "dev-001|2|Base ψ",
        "t2|0|softplus|1074",
        "hunt|identity|3",
    ],
)
def test_the_stream_is_the_first_16_hex_digits_of_sha256(c: str) -> None:
    h = int(hashlib.sha256(c.encode("utf-8")).hexdigest()[:16], 16)
    want = np.random.default_rng(np.random.SeedSequence([TEST_ROOT, h]))
    got = psm.reference_rng(c, TEST_ROOT)
    assert got.integers(2**32, size=16).tolist() == (
        want.integers(2**32, size=16).tolist()
    )


def test_the_root_is_always_stated_so_no_call_can_fall_into_the_frozen_one() -> None:
    root = inspect.signature(psm.reference_rng).parameters["root"]
    assert root.default is inspect.Parameter.empty


@pytest.mark.slow
def test_a_stream_does_not_depend_on_pythons_string_hash() -> None:
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(REPO / 'scripts')!r});"
        "import pilot_search_misses as p;"
        f"print(p.reference_rng('a|1|b', {TEST_ROOT}).integers(2**32))"
    )
    out = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert len(out) == 1


# --------------------------------------------------------------------------
# Tolerance and the miss rule (section 2.3)
# --------------------------------------------------------------------------


def valued(ll: float, gap: float = 0.0, certified: bool = True) -> Any:
    return psm.Valued(ll=ll, gap=gap, certified=certified)


def test_the_floor_decides_when_the_gaps_are_small() -> None:
    assert psm.tolerance(valued(0.0, 1e-11), valued(0.0, 1e-11)) == 3e-7


def test_the_tolerance_is_the_sum_of_the_gaps_above_the_floor() -> None:
    assert psm.tolerance(valued(0.0, 2e-5), valued(0.0, 1e-5)) == pytest.approx(3e-5)


def test_better_inside_the_tolerance_is_a_near_tie() -> None:
    call = psm.call_miss(valued(-100.0 + 2e-7), valued(-100.0))
    assert call.kind == "near_tie"
    assert call.gain > 0.0


def test_better_beyond_the_tolerance_is_a_miss() -> None:
    assert psm.call_miss(valued(-100.0 + 4e-7), valued(-100.0)).kind == "miss"


def test_the_gaps_widen_the_tolerance() -> None:
    chosen, stored = valued(-100.0 + 1e-5, 1e-5), valued(-100.0, 1e-5)
    assert psm.call_miss(chosen, stored).kind == "near_tie"


def test_equal_or_worse_is_neither() -> None:
    assert psm.call_miss(valued(-100.0), valued(-100.0)).kind == "none"
    assert psm.call_miss(valued(-101.0), valued(-100.0)).kind == "none"


def test_an_infinite_stored_gap_is_dropped_so_the_row_can_still_miss() -> None:
    call = psm.call_miss(valued(-99.0, 1e-9), valued(-100.0, math.inf, False))
    assert call.tol == 3e-7
    assert call.kind == "miss"
    assert call.stored_uncertified


def test_a_nan_stored_gap_is_dropped_too() -> None:
    call = psm.call_miss(valued(-99.0, 1e-9), valued(-100.0, math.nan, False))
    assert math.isfinite(call.tol)
    assert call.kind == "miss"


def test_a_finite_gap_on_a_certified_fit_is_not_called_uncertified() -> None:
    assert not psm.call_miss(valued(-99.0), valued(-100.0, 1e-9)).stored_uncertified


def test_an_uncertified_stored_fit_loses_its_gap_from_the_tolerance() -> None:
    chosen, stored = valued(-100.0 + 1e-5, 1e-9), valued(-100.0, 1.0, False)
    call = psm.call_miss(chosen, stored)
    assert call.tol == 3e-7
    assert call.kind == "miss"
    assert call.stored_uncertified


def test_an_infinite_gap_on_a_row_flagged_certified_is_still_dropped() -> None:
    call = psm.call_miss(valued(-99.0, 1e-9), valued(-100.0, math.inf, True))
    assert math.isfinite(call.tol)
    assert call.stored_uncertified


def test_a_stored_fit_at_minus_infinity_is_a_miss_not_nan() -> None:
    assert psm.call_miss(valued(-99.0), valued(-math.inf)).kind == "miss"


def test_a_gain_at_or_below_both_quadrature_errors_is_listed() -> None:
    assert psm.within_quadrature(1.0, 0.25, 0.75)
    assert psm.within_quadrature(0.5, 0.25, 0.75)
    assert not psm.within_quadrature(1.5, 0.25, 0.75)
    assert psm.within_quadrature(0.5, -0.25, 0.75)


def test_oracle_search_miss_rescued_by_truth_psi_is_not_a_reading_miss() -> None:
    """dev-015 s3's shape: the chosen point is the kept truth-ψ fit."""
    kept, search = valued(-100.0, 1e-9), valued(-104.53, 1e-9)
    chosen = kept
    assert psm.call_miss(chosen, kept).kind == "none"
    assert psm.call_miss(chosen, search).kind == "miss"


# --------------------------------------------------------------------------
# The chosen point (section 2.2)
# --------------------------------------------------------------------------


def pv(point: tuple[int, ...], score: float, certified: bool = True) -> Any:
    return psm.PointValue(point=point, score=score, gap=0.0, certified=certified)


def test_the_highest_certified_point_is_chosen() -> None:
    assert psm.choose_point(pv((0,), -5.0), pv((1,), -4.0)).point == (1,)
    assert psm.choose_point(pv((0,), -4.0), pv((1,), -5.0)).point == (0,)


def test_a_higher_uncertified_r0_is_not_chosen() -> None:
    assert psm.choose_point(pv((0,), -4.0, False), pv((1,), -5.0)).point == (1,)


def test_ties_go_to_the_lowest_index_tuple() -> None:
    assert psm.choose_point(pv((2,), -4.0), pv((1,), -4.0)).point == (1,)


def test_nothing_is_chosen_when_nothing_is_certified() -> None:
    assert psm.choose_point(pv((0,), -4.0, False), None) is None


def test_higher_uncertified_points_are_counted() -> None:
    values = [pv((0,), -3.0, False), pv((1,), -9.0, False), pv((2,), -4.0)]
    assert psm.count_uncertified_better(values, pv((2,), -4.0)) == 1


# --------------------------------------------------------------------------
# R1 and R2: blind, seeded, capped (section 2.2)
# --------------------------------------------------------------------------


def replayed_points(k: int, c: str = "toy") -> set[tuple[int, ...]]:
    """R1's points, rebuilt from the seeded starts alone, on a second profile."""
    data = toy_data()
    second = _Profile(structure_of(), data, psm.T2_CHANNELS, FitConfig(), None)
    rng = psm.reference_rng(c, TEST_ROOT)
    blocks = _search_blocks(second.slots, second.config.psi_block_max)
    for start in psm.draw_starts(second.slots, rng, k):
        _ascend(second, start, blocks, 1 << 62)
    return set(second.solutions)


def test_every_point_in_the_profile_was_reached_by_the_runs_own_starts() -> None:
    out = toy_search(k=1)
    assert 0 < len(out.profile.solutions) < 30  # not trivially the whole grid
    assert set(out.profile.solutions) == replayed_points(1)


def test_the_point_sets_are_equal_at_more_starts_too() -> None:
    assert set(toy_search(k=3).profile.solutions) == replayed_points(3)


def test_the_replay_would_see_a_point_supplied_from_outside() -> None:
    """The check itself can fail: the leak of round 1's script is visible."""
    out = toy_search(k=1)
    outside = next(
        p for p in product_points(out.profile) if p not in out.profile.solutions
    )
    out.profile.evaluate([outside])
    assert set(out.profile.solutions) != replayed_points(1)


def product_points(profile: Any) -> list[tuple[int, ...]]:
    a, b = (len(g) for g in profile.slots.grids)
    return [(i, j) for i in range(a) for j in range(b)]


def test_r1_refuses_a_profile_that_is_not_empty() -> None:
    profile = psm.fresh_profile(structure_of(), toy_data(), psm.T2_CHANNELS)
    profile.evaluate([(0, 0)])
    with pytest.raises(psm.ContaminatedProfile):
        psm.run_r1(profile, psm.reference_rng("toy", TEST_ROOT), k=1, cap=100)


def test_the_reference_takes_no_psi_and_no_coefficients() -> None:
    for function in (psm.reference_search, psm.run_r1, psm.run_r2):
        names = " ".join(inspect.signature(function).parameters)
        for word in ("psi", "truth", "generating", "coef", "known", "stored"):
            assert word not in names


def test_each_reference_search_starts_from_its_own_fresh_profile() -> None:
    assert toy_search().profile is not toy_search().profile


def test_r2_finishes_the_grid_on_the_same_profile() -> None:
    out = toy_search(k=1, r2_max=30)
    assert out.r2_complete
    assert len(out.profile.solutions) == 30
    assert out.r1.n_points < 30


def test_r2_does_not_run_above_its_limit() -> None:
    out = toy_search(k=1, r2_max=29)
    assert not out.r2_complete
    assert len(out.profile.solutions) == out.r1.n_points


def test_r1s_result_is_handed_over_before_r2_starts() -> None:
    seen: list[tuple[Any, int]] = []
    profiles: list[Any] = []
    real = psm.fresh_profile

    def capture(*args: Any) -> Any:
        profiles.append(real(*args))
        return profiles[-1]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(psm, "fresh_profile", capture)
        out = toy_search(
            k=1,
            r2_max=30,
            on_r1=lambda r1: seen.append((r1, len(profiles[0].solutions))),
        )
    assert seen[0][0] is out.r1
    assert seen[0][1] == out.r1.n_points < len(out.profile.solutions)


def test_r1s_own_best_does_not_see_r2() -> None:
    k1 = toy_search(k=1, r2_max=0)
    with_r2 = toy_search(k=1, r2_max=30)
    assert with_r2.r1 == k1.r1
    assert with_r2.best is not None
    assert with_r2.best.score >= with_r2.r1.best.score


def test_r1_is_deterministic() -> None:
    a, b = toy_search(k=3), toy_search(k=3)
    assert a.r1 == b.r1
    assert sorted(a.profile.solutions) == sorted(b.profile.solutions)


def test_the_cap_stops_r1_deterministically_and_flags_it() -> None:
    a, b = toy_search(k=8, cap=7), toy_search(k=8, cap=7)
    assert a.r1.capped
    assert a.r1.n_points <= 7
    assert a.r1 == b.r1


def test_a_start_cut_by_the_cap_is_not_a_completed_start() -> None:
    capped = toy_search(k=8, cap=7).r1
    assert capped.n_ascents < 8
    assert toy_search(k=3).r1.n_ascents == 3


def test_uncapped_r1_is_not_flagged() -> None:
    assert not toy_search(k=1).r1.capped


def test_a_capped_fit_still_goes_on_to_r2() -> None:
    out = toy_search(k=8, cap=7, r2_max=30)
    assert out.r1.capped
    assert out.r2_complete
    assert len(out.profile.solutions) == 30


def test_r1_stops_between_starts_at_the_job_cut() -> None:
    answers = iter([False, True])
    out = toy_search(k=5, should_stop=lambda: next(answers, True))
    assert out.r1.cpu_cut
    assert out.cpu_cut
    assert out.r1.n_ascents == 1


def test_a_cut_in_r1_leaves_r2_unstarted() -> None:
    out = toy_search(k=5, r2_max=30, should_stop=lambda: True)
    assert out.cpu_cut
    assert not out.r2_complete
    assert len(out.profile.solutions) == out.r1.n_points


def test_r2_stops_between_chunks_at_the_job_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(psm, "R2_CHUNK", 5)
    answers = iter([False, False, False])  # one start, then two chunks
    out = toy_search(k=1, r2_max=30, should_stop=lambda: next(answers, True))
    assert not out.r1.cpu_cut
    assert out.cpu_cut
    assert not out.r2_complete
    assert out.r1.n_points < len(out.profile.solutions) < 30


def test_uncut_searches_are_not_flagged() -> None:
    out = toy_search(k=1, r2_max=30)
    assert not out.cpu_cut
    assert not out.r1.cpu_cut


def test_the_job_cut_reads_this_processs_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psm.time, "process_time", lambda: 2.5 * 3600 - 1)
    assert not psm.past_job_cut()
    monkeypatch.setattr(psm.time, "process_time", lambda: 2.5 * 3600)
    assert psm.past_job_cut()


def test_r1_lists_its_uncertified_points_for_the_count() -> None:
    assert toy_search(k=2).r1.uncertified == ()


def test_r1s_best_is_the_highest_certified_of_its_own_points() -> None:
    out = toy_search(k=2)
    certified = [
        (-out.profile.score(p), p)
        for p, s in out.profile.solutions.items()
        if s is not None and s.certified
    ]
    assert out.r1.best.point == min(certified)[1]


# --------------------------------------------------------------------------
# Exhaustive units cannot miss (section 2.1)
# --------------------------------------------------------------------------


def test_the_grid_class_boundary_is_512_513() -> None:
    assert psm.is_exhaustive(512)
    assert not psm.is_exhaustive(513)


def test_the_exhaustive_fit_is_the_brute_force_maximum() -> None:
    s, data = structure_of(), toy_data()
    assert _Slots.of(s).size == 30
    result = fit(s, data, psm.T2_CHANNELS, config=FitConfig())
    assert result.psi_search == "exhaustive"
    slots = _Slots.of(s)
    best = max(
        fit(
            s,
            data,
            psm.T2_CHANNELS,
            config=FitConfig(),
            psi=slots.assignment(p),
        ).log_likelihood
        for p in product_points(_Profile(s, data, psm.T2_CHANNELS, FitConfig(), None))
    )
    assert result.log_likelihood == best


def test_fit_takes_the_path_the_class_says_at_the_boundary() -> None:
    s, data = structure_of(), toy_data()
    at = fit(s, data, psm.T2_CHANNELS, config=FitConfig(psi_full_grid_max=30))
    above = fit(s, data, psm.T2_CHANNELS, config=FitConfig(psi_full_grid_max=29))
    assert at.psi_search == "exhaustive"
    assert above.psi_search == "coordinate"


# --------------------------------------------------------------------------
# The byte check (section 2.4)
# --------------------------------------------------------------------------


def oracle_expect(**over: Any) -> Any:
    base = {
        "search_ll": (-100.5).hex(),
        "truth_psi_ll": (-100.25).hex(),
        "psi_source": "truth",
        "underfit": False,
    }
    return psm.OracleExpect(**{**base, **over})


def expect(system: str = "B-sym", oracle: Any = None, value: float = -0.75) -> Any:
    return psm.RowExpect(system=system, llm=False, per_event=value.hex(), oracle=oracle)


def test_an_equal_reading_passes() -> None:
    assert psm.byte_check(expect(), (-0.75).hex(), None) == []


def test_one_ulp_off_fails() -> None:
    off = math.nextafter(-0.75, 0.0)
    assert psm.byte_check(expect(), off.hex(), None) != []


def test_minus_infinity_compares_as_text() -> None:
    assert psm.byte_check(expect(value=-math.inf), (-math.inf).hex(), None) == []


def test_the_oracle_fields_must_match_too() -> None:
    row = expect("ORACLE", oracle_expect())
    good = {
        "oracle_search_ll": -100.5,
        "oracle_truth_psi_ll": -100.25,
        "oracle_psi_source": "truth",
        "oracle_underfit": False,
    }
    assert psm.byte_check(row, (-0.75).hex(), good) == []
    for name, bad in (
        ("oracle_search_ll", math.nextafter(-100.5, 0.0)),
        ("oracle_truth_psi_ll", math.nextafter(-100.25, 0.0)),
        ("oracle_psi_source", "search"),
        ("oracle_underfit", True),
    ):
        assert psm.byte_check(row, (-0.75).hex(), {**good, name: bad}) != []


def test_an_oracle_field_the_cell_has_must_exist_at_r0() -> None:
    row = expect("ORACLE", oracle_expect())
    details = {
        "oracle_search_ll": -100.5,
        "oracle_psi_source": "truth",
        "oracle_underfit": False,
    }
    assert psm.byte_check(row, (-0.75).hex(), details) != []


# --------------------------------------------------------------------------
# Held-out size (section 2.4)
# --------------------------------------------------------------------------


def test_delta_is_signed() -> None:
    assert psm.held_out_delta(-0.70, -0.71) == (pytest.approx(-0.01), "finite")
    assert psm.held_out_delta(-0.71, -0.70) == (pytest.approx(0.01), "finite")


def test_minus_infinity_staying_there_is_delta_zero() -> None:
    assert psm.held_out_delta(-math.inf, -math.inf) == (0.0, "finite")


def test_a_move_to_or_from_minus_infinity_has_no_finite_delta() -> None:
    assert psm.held_out_delta(-math.inf, -0.7) == (None, "infinite_move")
    assert psm.held_out_delta(-0.7, -math.inf) == (None, "infinite_move")


def test_h3_counts_an_infinite_move_as_above_the_limit() -> None:
    assert psm.h3_falsified([(None, "infinite_move")])
    assert psm.h3_falsified([(0.02, "finite")])
    assert psm.h3_falsified([(-0.02, "finite")])
    assert not psm.h3_falsified([(0.01, "finite"), (-0.005, "finite")])
    assert not psm.h3_falsified([])


# --------------------------------------------------------------------------
# Toy tables: the verdict (section 2.7)
# --------------------------------------------------------------------------

TRUTHS: Final = ("t1", "t2", "t3", "t4", "t5")
SEEDS: Final = (1, 2, 3)


def table_row(
    truth: str,
    seed: int,
    system: str,
    per_event: float | None,
    *,
    llm: bool = False,
    exact: bool = False,
    structure: str | None = None,
    submitted: bool = True,
) -> Row:
    return Row(
        truth=truth,
        seed=seed,
        system=system,
        llm=llm,
        ok=True,
        submitted=submitted,
        per_event=per_event,
        exact=exact,
        distance=0.5,
        has_structure=structure is not None,
        similarity=0.9,
        fits_used=1,
        structure=structure,
        curve=(),
        error=None,
        oracle_underfit=False if system == "ORACLE" else None,
    )


def toy_table(*, ood: bool = True) -> Table:
    rows: list[Row] = []
    for t in TRUTHS:
        for seed in SEEDS:
            base = -0.7 - 0.001 * seed
            for system, gap, exact in (
                ("ORACLE", 0.0, True),
                ("B-lib", -0.05, False),
                ("B-rand", -0.04, False),
                ("B-sym", -0.01, False),
                ("B-sparse", -0.02, False),
                ("planted-hint", 0.0, True),
            ):
                rows.append(
                    table_row(
                        t, seed, system, base + gap, exact=exact, structure=system
                    )
                )
            rows.append(table_row(t, seed, "B-np", base - 0.03))
            if seed == 1:
                rows.append(
                    table_row(t, seed, "B-sym@10F", base - 0.002, structure="10F")
                )
            rows.append(
                table_row(
                    t, seed, "AG-c-S/named", base - 0.01, llm=True, structure="LLM"
                )
            )
    truths = {t: replace(entry("hawkes"), in_dictionary=not ood) for t in TRUTHS}
    return Table(truths, tuple(derive(rows)), (), {})


def test_the_toy_table_is_a_go() -> None:
    v = analysis.verdict(toy_table(), np.random.default_rng(0), resamples=200)
    assert v.outcome == "go", v.reasons


def test_empty_corrected_returns_an_equal_verdict() -> None:
    table = toy_table()
    got = psm.corrected_verdict(table, {}, {}, resamples=200)
    want = analysis.verdict(
        table, np.random.default_rng(psm.VERDICT_SEED), resamples=200
    )
    assert got == want


def test_the_baseline_check_passes_on_a_go() -> None:
    psm.check_baseline_verdict(toy_table(), resamples=200)


def test_the_baseline_check_stops_a_table_that_is_not_go() -> None:
    table = toy_table()
    rows = tuple(r for r in table.rows if r.system != "planted-hint")
    with pytest.raises(psm.ByteFailure):
        psm.check_baseline_verdict(replace(table, rows=rows), resamples=200)


def test_the_baseline_check_stops_when_the_rebuilt_verdict_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = toy_table()
    monkeypatch.setattr(
        psm,
        "corrected_verdict",
        lambda *a, **k: replace(
            analysis.verdict(table, np.random.default_rng(0), resamples=200),
            reasons=("different",),
        ),
    )
    with pytest.raises(psm.ByteFailure):
        psm.check_baseline_verdict(table, resamples=200)


def sparse_closes_everywhere(table: Table) -> dict[tuple[str, int, str], float]:
    return {
        (r.truth, r.seed, "B-sparse"): r.per_event
        for r in table.rows
        if r.system == "ORACLE" and r.per_event is not None
    }


def test_a_planted_shift_flips_the_outcome_where_it_should() -> None:
    table = toy_table()
    v = psm.corrected_verdict(table, sparse_closes_everywhere(table), {}, resamples=200)
    assert v.outcome == "too easy"


def test_a_planted_shift_on_one_row_leaves_a_go() -> None:
    table = toy_table()
    shift = {("t1", 1, "B-sparse"): -0.7205}
    v = psm.corrected_verdict(table, shift, {}, resamples=200)
    assert v.outcome == "go"


def test_substitution_reaches_the_references_through_derive() -> None:
    table = toy_table()
    corrected = {("t1", 1, "ORACLE"): -0.6}
    new = psm.corrected_table(table, corrected, {})
    sym = next(r for r in new.rows if (r.truth, r.seed, r.system) == ("t1", 1, "B-sym"))
    assert sym.gap == pytest.approx(sym.per_event + 0.6)


def test_llm_rows_are_substituted_but_do_not_move_the_verdict() -> None:
    table = toy_table()
    key = ("t1", 1, "AG-c-S/named")
    new = psm.corrected_table(table, {key: 0.0}, {})
    row = next(r for r in new.rows if (r.truth, r.seed, r.system) == key)
    assert row.per_event == 0.0
    assert psm.corrected_verdict(table, {key: 0.0}, {}, resamples=200) == (
        psm.corrected_verdict(table, {}, {}, resamples=200)
    )


def test_a_better_fit_can_clear_an_underfit_flag() -> None:
    table = toy_table()
    rows = tuple(
        replace(r, oracle_underfit=True)
        if (r.truth, r.seed, r.system) == ("t1", 1, "ORACLE")
        else r
        for r in table.rows
    )
    flagged = replace(table, rows=rows)
    assert (
        psm.corrected_verdict(flagged, {}, {}, resamples=200).outcome == "controls fail"
    )
    key = ("t1", 1, "ORACLE")
    cleared = psm.corrected_verdict(flagged, {}, {key: False}, resamples=200)
    assert cleared.outcome == "go"


def test_an_underfit_flag_that_appears_stops_the_script() -> None:
    table = toy_table()
    with pytest.raises(psm.ByteFailure):
        psm.corrected_table(table, {}, {("t1", 1, "ORACLE"): True})


def test_the_moved_clause_is_named_with_its_consequence() -> None:
    table = toy_table()
    stored = psm.corrected_verdict(table, {}, {}, resamples=200)
    moved = psm.corrected_verdict(
        table, sparse_closes_everywhere(table), {}, resamples=200
    )
    clauses = psm.clauses_moved(stored, moved)
    assert [c["clause"] for c in clauses] == ["too_easy"]
    assert "re-pilot" in clauses[0]["consequence"]


def test_c3_moving_names_the_dev_tuning() -> None:
    table = toy_table()
    higher = {
        (r.truth, r.seed, "ORACLE"): r.per_event + 0.05
        for r in table.rows
        if r.system == "ORACLE" and r.per_event is not None and r.seed == 1
    }
    stored = psm.corrected_verdict(table, {}, {}, resamples=200)
    moved = psm.corrected_verdict(table, higher, {}, resamples=200)
    clauses = {c["clause"]: c["consequence"] for c in psm.clauses_moved(stored, moved)}
    assert "dev tuning" in clauses["C3"]


# --------------------------------------------------------------------------
# Records: floats as hex, append-only, sorted, torn tails (sections 2.10, 8)
# --------------------------------------------------------------------------


def test_floats_are_written_as_hex() -> None:
    line = psm.dumps_record({"b": 1.5, "a": [math.inf, -math.inf], "c": 1})
    assert json.loads(line) == {
        "a": ["inf", "-inf"],
        "b": (1.5).hex(),
        "c": 1,
    }


def test_records_are_fsynced_per_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = os.fsync

    def counted(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(psm.os, "fsync", counted)
    psm.Journal(tmp_path / "j").append({"type": "x"})
    assert calls


def test_a_torn_last_line_is_not_a_record(tmp_path: Path) -> None:
    path = tmp_path / "j"
    psm.Journal(path).append({"type": "done", "job": "a"})
    with path.open("ab") as f:
        f.write(b'{"type":"done","jo')
    assert [r["job"] for r in psm.read_records(path)] == ["a"]


def test_a_torn_line_in_the_middle_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "j"
    path.write_bytes(b'{"type":"x"\n{"type":"y"}\n')
    with pytest.raises(psm.ByteFailure):
        psm.read_records(path)


def test_opening_for_a_resume_removes_only_the_torn_fragment(tmp_path: Path) -> None:
    path = tmp_path / "j"
    journal = psm.Journal(path)
    journal.append({"type": "done", "job": "a"})
    whole = path.read_bytes()
    with path.open("ab") as f:
        f.write(b'{"type":"done","jo')
    psm.repair_tail(path)
    assert path.read_bytes() == whole
    journal.append({"type": "done", "job": "b"})
    assert [r["job"] for r in psm.read_records(path)] == ["a", "b"]


def test_units_are_sorted_and_keep_no_duplicates(tmp_path: Path) -> None:
    records = [
        {"type": "done", "job": "b", "x": 1},
        {"type": "r1", "job": "a", "x": 2},
        {"type": "done", "job": "a", "x": 3},
        {"type": "r1", "job": "a", "x": 2},
        {"type": "job_lost", "job": "a"},
        {"type": "run_end", "reason": "complete"},
    ]
    one, two = tmp_path / "1", tmp_path / "2"
    psm.write_units(records, one)
    psm.write_units(list(reversed(records)), two)
    assert one.read_bytes() == two.read_bytes()
    jobs = [json.loads(x) for x in one.read_text().splitlines()]
    assert [(r["job"], r["type"]) for r in jobs] == [
        ("a", "r1"),
        ("a", "done"),
        ("b", "done"),
    ]


def test_a_rerun_with_different_bytes_is_a_failure(tmp_path: Path) -> None:
    records = [
        {"type": "r1", "job": "a", "x": 2},
        {"type": "r1", "job": "a", "x": 3},
    ]
    with pytest.raises(psm.ByteFailure):
        psm.write_units(records, tmp_path / "u")


# --------------------------------------------------------------------------
# The driver: one process per job, retries, limits, stops (sections 6, 8, 9)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToyJob:
    """A job for the driver's tests; ``mode`` says how its process ends."""

    name: str
    mode: str = "ok"
    marker: str = ""

    @property
    def id(self) -> str:
        return self.name

    def run(self, sink: Any) -> None:
        if self.marker:
            with Path(self.marker).open("a") as f:
                f.write(f"{self.name} {os.getpid()}\n")
        match self.mode:
            case "burn":
                while True:
                    pass
            case "kill":
                os.kill(os.getpid(), signal.SIGKILL)
            case "kill_once":
                if len(Path(self.marker).read_text().splitlines()) == 1:
                    os.kill(os.getpid(), signal.SIGKILL)
            case "bug":
                raise KeyError("a bug")
            case "exit_clean":
                return
            case "r1_then_kill" | "r1_then_burn":
                sink.record("r1", {"best": None, "result": {"kind": "fit"}})
                if self.mode == "r1_then_kill":
                    os.kill(os.getpid(), signal.SIGKILL)
                while True:
                    pass
            case "byte_fail":
                sink.record("byte_fail", {"reason": "planted"})
                return
            case "slow":
                time.sleep(0.5)
            case "hang":
                time.sleep(60)
        sink.record("done", {"kind": "toy"})


@dataclass(frozen=True)
class SearchJob:
    """A real R1 on a toy log, for the worker-count and order test."""

    name: str

    @property
    def id(self) -> str:
        return self.name

    def run(self, sink: Any) -> None:
        out = psm.reference_search(
            structure_of(),
            toy_data(),
            psm.T2_CHANNELS,
            psm.reference_rng(self.name, TEST_ROOT),
            k=2,
            cap=10**6,
            r2_max=0,
        )
        sink.record(
            "done",
            {"kind": "toy", "points": sorted(out.profile.solutions)},
        )


def driver(tmp: Path, stages: Sequence[Any], *, workers: int = 2, **kw: Any) -> Any:
    options: dict[str, Any] = {
        "read_usage_usec": lambda: 0,
        "read_mem_gib": lambda: 16.0,
        "cpu_limit_s": 3600,
        "poll_s": 0.005,
        "cpu_poll_s": 0.0,
        "start_method": "fork",
        **kw,
    }
    return psm.Driver(psm.OutPaths(tmp), tuple(stages), workers=workers, **options)


def plant(paths: Any, *records: dict[str, Any]) -> None:
    """Records left by an earlier unit, with that unit's ``cpu.json``."""
    paths.root.mkdir(parents=True, exist_ok=True)
    psm.CpuLedger.begin(paths.cpu)
    journal = psm.Journal(paths.journal)
    for record in records:
        journal.append(record)


def stage(*jobs: Any, name: str = "tier1", **kw: Any) -> Any:
    return psm.Stage(name, tuple(jobs), **kw)


def types_of(path: Path, job: str) -> list[str]:
    return [r["type"] for r in psm.read_records(path) if r.get("job") == job]


def test_every_job_runs_in_its_own_process(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    jobs = [ToyJob(f"j{i}", marker=marker) for i in range(5)]
    assert driver(tmp_path, [stage(*jobs)]).run() == "complete"
    pids = [x.split()[1] for x in Path(marker).read_text().splitlines()]
    assert len(pids) == 5
    assert len(set(pids)) == 5


def test_the_driver_uses_no_shared_pool() -> None:
    source = (REPO / "scripts" / "pilot_search_misses.py").read_text(encoding="utf-8")
    assert "ProcessPoolExecutor" not in source


def test_a_killed_job_does_not_touch_its_sibling(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    jobs = (ToyJob("dead", "kill", marker), ToyJob("alive", "slow"))
    assert driver(tmp_path, [stage(*jobs)]).run() == "complete"
    journal = psm.OutPaths(tmp_path).journal
    assert types_of(journal, "alive") == ["done"]
    assert types_of(journal, "dead")[-1] == "reference_failed"


def test_a_lost_worker_is_retried_once_after_the_stage(tmp_path: Path) -> None:
    marker = tmp_path / "pids"
    marker.write_text("")
    jobs = (ToyJob("lost", "kill_once", str(marker)), ToyJob("other"))
    assert driver(tmp_path, [stage(*jobs)], workers=1).run() == "complete"
    seen = [
        (r["type"], r.get("job"))
        for r in psm.read_records(psm.OutPaths(tmp_path).journal)
        if "job" in r
    ]
    assert seen == [("job_lost", "lost"), ("done", "other"), ("done", "lost")]


def test_a_second_loss_is_reference_failed_worker_lost(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    assert driver(tmp_path, [stage(ToyJob("x", "kill", marker))]).run() == "complete"
    records = psm.read_records(psm.OutPaths(tmp_path).journal)
    assert [r["type"] for r in records if r.get("job") == "x"] == [
        "job_lost",
        "reference_failed",
    ]
    assert next(r for r in records if r["type"] == "reference_failed")["reason"] == (
        "worker_lost"
    )
    assert len(Path(marker).read_text().splitlines()) == 2


def test_an_exit_without_a_result_counts_as_lost(tmp_path: Path) -> None:
    run = driver(tmp_path, [stage(ToyJob("x", "exit_clean"))])
    assert run.run() == "complete"
    assert types_of(psm.OutPaths(tmp_path).journal, "x") == [
        "job_lost",
        "reference_failed",
    ]


def test_the_retry_survives_a_resume(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "job_lost", "job": "x"})
    marker = str(tmp_path / "pids")
    assert driver(tmp_path, [stage(ToyJob("x", "kill", marker))]).run() == "complete"
    assert len(Path(marker).read_text().splitlines()) == 1
    assert types_of(paths.journal, "x") == ["job_lost", "reference_failed"]


@pytest.mark.slow
def test_the_cpu_limit_is_per_job_and_not_retried(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    jobs = (ToyJob("burn", "burn", marker), ToyJob("other", "slow"))
    assert driver(tmp_path, [stage(*jobs)], cpu_limit_s=1).run() == "complete"
    journal = psm.OutPaths(tmp_path).journal
    assert types_of(journal, "burn") == ["reference_failed"]
    assert types_of(journal, "other") == ["done"]
    failed = next(
        r for r in psm.read_records(journal) if r["type"] == "reference_failed"
    )
    assert failed["reason"] == "cpu_limit"
    assert len(Path(marker).read_text().splitlines()) == 1


def test_a_job_lost_after_r1_is_r2_lost_not_reference_failed(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    job = ToyJob("x", "r1_then_kill", marker)
    assert driver(tmp_path, [stage(job)]).run() == "complete"
    assert types_of(psm.OutPaths(tmp_path).journal, "x") == [
        "r1",
        "job_lost",
        "r1",
        "r2_lost",
    ]
    assert len(Path(marker).read_text().splitlines()) == 2


@pytest.mark.slow
def test_the_cpu_limit_after_r1_is_r2_lost_and_not_retried(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    job = ToyJob("x", "r1_then_burn", marker)
    assert driver(tmp_path, [stage(job)], cpu_limit_s=1).run() == "complete"
    records = psm.read_records(psm.OutPaths(tmp_path).journal)
    (lost,) = [r for r in records if r["type"] == "r2_lost"]
    assert lost["reason"] == "cpu_limit"
    assert len(Path(marker).read_text().splitlines()) == 1


def test_exit_codes_are_read_as_the_text_says() -> None:
    assert psm.exit_kind(0) == "ok"
    assert psm.exit_kind(-signal.SIGXCPU) == "cpu_limit"
    assert psm.exit_kind(-signal.SIGKILL) == "lost"
    assert psm.exit_kind(-signal.SIGTERM) == "terminated"
    assert psm.exit_kind(1) == "bug"


def test_a_bug_stops_the_run_and_is_not_retried(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    jobs = (ToyJob("bad", "bug", marker), ToyJob("later"))
    assert driver(tmp_path, [stage(*jobs)], workers=1).run() == "bug"
    journal = psm.OutPaths(tmp_path).journal
    assert types_of(journal, "later") == []
    assert len(Path(marker).read_text().splitlines()) == 1


def test_a_byte_failure_stops_at_once(tmp_path: Path) -> None:
    jobs = (ToyJob("bad", "byte_fail"), ToyJob("later"))
    assert driver(tmp_path, [stage(*jobs)], workers=1).run() == "byte_fail"
    assert types_of(psm.OutPaths(tmp_path).journal, "later") == []


def test_stages_run_in_order_with_a_barrier(tmp_path: Path) -> None:
    marker = str(tmp_path / "order")
    stages = [
        stage(ToyJob("a", "slow", marker), ToyJob("b", "ok", marker), name="one"),
        stage(ToyJob("c", "ok", marker), name="two"),
    ]
    assert driver(tmp_path, stages).run() == "complete"
    names = [x.split()[0] for x in Path(marker).read_text().splitlines()]
    assert names[-1] == "c"
    done = [
        r["job"]
        for r in psm.read_records(psm.OutPaths(tmp_path).journal)
        if r["type"] == "done"
    ]
    assert done[-1] == "c"


def test_memory_pauses_dispatch_and_stops_nothing(tmp_path: Path) -> None:
    readings = iter([7.9, 7.9, 7.9])

    def memory() -> float:
        return next(readings, 8.0)

    run = driver(tmp_path, [stage(ToyJob("a"))], read_mem_gib=memory)
    assert run.run() == "complete"
    assert types_of(psm.OutPaths(tmp_path).journal, "a") == ["done"]


def test_the_memory_gate_is_8_gib() -> None:
    assert psm.may_start(running=0, workers=2, mem_gib=8.0)
    assert psm.may_start(running=1, workers=2, mem_gib=8.0)
    assert not psm.may_start(running=1, workers=2, mem_gib=7.99)
    assert not psm.may_start(running=0, workers=2, mem_gib=7.99)
    assert not psm.may_start(running=2, workers=2, mem_gib=100.0)


def test_a_start_with_stop_present_is_refused(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    paths.stop.write_text("owner: read the journal entry\n")
    with pytest.raises(psm.ResumeRefused, match="STOP"):
        driver(tmp_path, [stage(ToyJob("a"))]).run()
    assert not paths.journal.exists()


def test_stop_lets_the_job_in_flight_finish(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    calls = iter(range(1000))

    def memory() -> float:
        if next(calls) == 1:
            paths.stop.write_text("control room: why\n")
        return 16.0

    jobs = (ToyJob("a", "slow"), ToyJob("b"))
    assert driver(tmp_path, [stage(*jobs)], workers=1, read_mem_gib=memory).run() == (
        "stop"
    )
    assert types_of(paths.journal, "a") == ["done"]
    assert types_of(paths.journal, "b") == []


def sigterm_a_driver(tmp_path: Path) -> list[Any]:
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=_run_driver, args=(tmp_path, [stage(ToyJob("a", "hang"))])
    )
    process.start()
    journal = psm.OutPaths(tmp_path).journal
    _wait_for(lambda: journal.exists() and "run_start" in journal.read_text())
    time.sleep(0.2)
    assert process.pid is not None
    os.kill(process.pid, signal.SIGTERM)
    process.join(20)
    assert process.exitcode == 0
    records: list[Any] = psm.read_records(journal)
    assert [r for r in records if r.get("job") == "a"] == []
    return records


def test_sigterm_at_sixty_hours_is_the_wall_limit(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    paths.root.mkdir(exist_ok=True)
    psm.CpuLedger.begin(paths.cpu).update(usage_usec=1, wall_s=60 * 3600.0 - 100.0)
    records = sigterm_a_driver(tmp_path)
    assert psm.end_state(records, paths.stop) == "wall_limit"


def test_sigterm_with_time_left_is_not_the_wall_limit(tmp_path: Path) -> None:
    """A clean reboot sends the same signal, and a reboot may resume."""
    records = sigterm_a_driver(tmp_path)
    assert psm.end_state(records, psm.OutPaths(tmp_path).stop) == "terminated"


def _run_driver(tmp: Path, stages: Sequence[Any]) -> None:
    os.setsid()
    driver(tmp, stages).run()


def _wait_for(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_a_resume_appends_and_skips_what_is_done(tmp_path: Path) -> None:
    marker = str(tmp_path / "ran")
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "run_start"}, {"type": "done", "job": "a"})
    before = paths.journal.read_bytes()
    jobs = (ToyJob("a", "ok", marker), ToyJob("b", "ok", marker))
    assert driver(tmp_path, [stage(*jobs)]).run() == "complete"
    assert paths.journal.read_bytes().startswith(before)
    names = [x.split()[0] for x in Path(marker).read_text().splitlines()]
    assert names == ["b"]


def test_a_stage_cap_stops_dispatch_in_order_and_counts_what_finished(
    tmp_path: Path,
) -> None:
    marker = str(tmp_path / "ran")
    jobs = [ToyJob(f"h{i}", "ok", marker) for i in range(4)]
    stages = [
        psm.Stage("hunt", tuple(jobs), cpu_cap_s=0.0),
        stage(ToyJob("after", "ok", marker)),
    ]
    assert driver(tmp_path, stages, workers=1).run() == "complete"
    names = [x.split()[0] for x in Path(marker).read_text().splitlines()]
    assert names == ["after"]


def test_follow_up_jobs_run_after_the_stages_jobs(tmp_path: Path) -> None:
    marker = str(tmp_path / "ran")

    def follow(records: Sequence[Any]) -> list[Any]:
        finished = {r.get("job") for r in records if r["type"] == "done"}
        assert finished >= {"a", "b"}
        return [ToyJob("r1", "ok", marker)]

    jobs = (ToyJob("a", "ok", marker), ToyJob("b", "ok", marker))
    stages = [psm.Stage("hunt", jobs, follow_up=follow)]
    assert driver(tmp_path, stages).run() == "complete"
    names = [x.split()[0] for x in Path(marker).read_text().splitlines()]
    assert names[-1] == "r1"
    assert len(names) == 3


def test_a_follow_up_due_at_a_resume_still_runs(tmp_path: Path) -> None:
    marker = str(tmp_path / "ran")
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "done", "job": "a"})

    def follow(records: Sequence[Any]) -> list[Any]:
        return [ToyJob("r1", "ok", marker)]

    stages = [psm.Stage("hunt", (ToyJob("a"),), follow_up=follow)]
    assert driver(tmp_path, stages).run() == "complete"
    assert [x.split()[0] for x in Path(marker).read_text().splitlines()] == ["r1"]


def test_the_stage_cap_also_stops_follow_ups(tmp_path: Path) -> None:
    marker = str(tmp_path / "ran")
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "done", "job": "a"})

    def follow(records: Sequence[Any]) -> list[Any]:
        return [ToyJob("r1", "ok", marker)]

    stages = [psm.Stage("hunt", (ToyJob("a"),), cpu_cap_s=0.0, follow_up=follow)]
    assert driver(tmp_path, stages).run() == "complete"
    assert not Path(marker).exists()


def test_the_cap_firing_is_named_in_the_records(tmp_path: Path) -> None:
    jobs = [ToyJob("h0"), ToyJob("h1")]
    stages = [psm.Stage("hunt", tuple(jobs), cpu_cap_s=0.0)]
    assert driver(tmp_path, stages).run() == "complete"
    records = psm.read_records(psm.OutPaths(tmp_path).journal)
    assert [r["stage"] for r in records if r["type"] == "stage_cap"] == ["hunt"]


def test_the_hook_runs_when_a_stage_ends_and_not_when_it_is_cut(
    tmp_path: Path,
) -> None:
    seen: list[str] = []
    stages = [stage(ToyJob("a"), name="tier1"), stage(ToyJob("b"), name="s2")]
    run = driver(tmp_path, stages, after_stage=seen.append)
    assert run.run() == "complete"
    assert seen == ["tier1", "s2"]
    out = tmp_path / "cut"
    out.mkdir()
    seen.clear()
    jobs = [stage(ToyJob("bad", "byte_fail"), name="tier1")]
    assert driver(out, jobs, after_stage=seen.append).run() == "byte_fail"
    assert seen == []


def test_a_run_ended_by_stop_refuses_a_resume(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)

    def memory() -> float:
        paths.stop.write_text("owner: why\n")
        return 16.0

    jobs = (ToyJob("a"), ToyJob("b"))
    assert driver(tmp_path, [stage(*jobs)], workers=1, read_mem_gib=memory).run() == (
        "stop"
    )
    paths.stop.unlink()
    with pytest.raises(psm.ResumeRefused, match="stop"):
        driver(tmp_path, [stage(ToyJob("a"))]).run()


@pytest.mark.parametrize(
    "end", ["cpu_stop", "wall_limit", "bug", "byte_fail", "complete"]
)
def test_a_resume_is_refused_after_any_end_the_text_does_not_allow(
    tmp_path: Path, end: str
) -> None:
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "run_start"}, {"type": "run_end", "reason": end})
    with pytest.raises(psm.ResumeRefused, match=end):
        driver(tmp_path, [stage(ToyJob("a"))]).run()


def test_a_resume_is_allowed_after_a_kill(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "run_start"})
    assert driver(tmp_path, [stage(ToyJob("a"))]).run() == "complete"


def test_a_resume_is_allowed_after_a_reboots_sigterm(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    plant(paths, {"type": "run_start"}, {"type": "run_end", "reason": "terminated"})
    assert driver(tmp_path, [stage(ToyJob("a"))]).run() == "complete"


def test_a_fresh_start_in_a_directory_with_records_is_refused(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    psm.Journal(paths.journal).append({"type": "done", "job": "a"})
    with pytest.raises(psm.ResumeRefused, match="records"):
        driver(tmp_path, [stage(ToyJob("b"))]).run()
    assert not paths.cpu.exists()


def test_a_fresh_start_in_an_empty_directory_is_allowed(tmp_path: Path) -> None:
    assert driver(tmp_path, [stage(ToyJob("a"))]).run() == "complete"


def test_timing_stays_out_of_the_records(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    assert driver(tmp_path, [stage(ToyJob("a"))]).run() == "complete"
    psm.write_units(psm.read_records(paths.journal), paths.units)
    timing = [json.loads(x) for x in paths.timing.read_text().splitlines()]
    assert timing[0]["job"] == "a"
    assert "wall_s" in timing[0]
    assert "cpu_s" in timing[0]
    for name in ("wall", "cpu_s", "elapsed"):
        assert name not in paths.units.read_text()
        assert name not in paths.journal.read_text()


@pytest.mark.slow
def test_r1_does_not_depend_on_worker_count_or_order(tmp_path: Path) -> None:
    names = ["s1", "s2", "s3"]
    outputs: list[bytes] = []
    for i, (workers, order) in enumerate(
        ((1, names), (2, names), (2, list(reversed(names))))
    ):
        out = tmp_path / str(i)
        out.mkdir()
        run = driver(out, [stage(*(SearchJob(n) for n in order))], workers=workers)
        assert run.run() == "complete"
        paths = psm.OutPaths(out)
        psm.write_units(psm.read_records(paths.journal), paths.units)
        outputs.append(paths.units.read_bytes())
    assert outputs[0] == outputs[1] == outputs[2]


@pytest.mark.slow
def test_the_default_start_method_gives_a_fresh_interpreter(tmp_path: Path) -> None:
    marker = str(tmp_path / "pids")
    run = driver(
        tmp_path,
        [stage(ToyJob("a", "ok", marker), ToyJob("b", "ok", marker))],
        start_method=psm.START_METHOD,
    )
    assert psm.START_METHOD == "spawn"
    assert run.run() == "complete"
    pids = [x.split()[1] for x in Path(marker).read_text().splitlines()]
    assert len(set(pids)) == 2


# --------------------------------------------------------------------------
# The CPU ceiling (section 8)
# --------------------------------------------------------------------------

HOUR_USEC: Final = 3600 * 10**6


def test_the_driver_reads_the_cgroup_usage(tmp_path: Path) -> None:
    (tmp_path / "proc").write_text("0::/user.slice/sci.service\n")
    unit = tmp_path / "cg" / "user.slice" / "sci.service"
    unit.mkdir(parents=True)
    (unit / "cpu.stat").write_text("usage_usec 1234\nuser_usec 1000\nsystem_usec 234\n")
    assert psm.read_usage_usec(tmp_path / "proc", tmp_path / "cg") == 1234


def test_a_missing_cgroup_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "proc").write_text("0::/gone\n")
    with pytest.raises(psm.CpuAccountingError):
        psm.read_usage_usec(tmp_path / "proc", tmp_path)


def test_the_ledger_adds_the_earlier_units(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    first = psm.CpuLedger.begin(path)
    first.update(usage_usec=10 * HOUR_USEC, wall_s=3600.0)
    second = psm.CpuLedger.begin(path)
    second.update(usage_usec=5 * HOUR_USEC, wall_s=1800.0)
    assert second.total_usec == 15 * HOUR_USEC
    assert second.wall_s == 5400.0


def test_a_ledger_begun_twice_does_not_count_a_unit_twice(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path).update(usage_usec=10 * HOUR_USEC, wall_s=1.0)
    psm.CpuLedger.begin(path)
    psm.CpuLedger.begin(path)
    assert psm.CpuLedger.begin(path).total_usec == 10 * HOUR_USEC


def test_a_resume_at_the_stop_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path).update(usage_usec=78 * HOUR_USEC, wall_s=1.0)
    with pytest.raises(psm.ResumeRefused):
        psm.CpuLedger.begin(path)


def test_a_resume_just_below_the_stop_is_allowed(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path).update(usage_usec=78 * HOUR_USEC - 1, wall_s=1.0)
    assert psm.CpuLedger.begin(path).total_usec == 78 * HOUR_USEC - 1


def test_a_resume_with_no_wall_time_left_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path).update(usage_usec=1, wall_s=60 * 3600.0)
    with pytest.raises(psm.ResumeRefused):
        psm.CpuLedger.begin(path)


def test_dispatch_stops_at_78_hours_counting_earlier_units(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    psm.CpuLedger.begin(paths.cpu).update(usage_usec=70 * HOUR_USEC, wall_s=1.0)
    marker = str(tmp_path / "ran")
    usage = iter([0, 0, 8 * HOUR_USEC])

    def read() -> int:
        return next(usage, 8 * HOUR_USEC)

    jobs = (ToyJob("a", "ok", marker), ToyJob("b", "ok", marker))
    run = driver(tmp_path, [stage(*jobs)], workers=1, read_usage_usec=read)
    assert run.run() == "cpu_stop"
    assert psm.end_state(psm.read_records(paths.journal), paths.stop) == "cpu_stop"
    assert len(Path(marker).read_text().splitlines()) == 1


def test_the_first_start_writes_the_scripts_own_hash(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path)
    source = REPO / "scripts" / "pilot_search_misses.py"
    want = hashlib.sha256(source.read_bytes()).hexdigest()
    assert psm.script_sha256() == want
    assert json.loads(path.read_text())["script_sha256"] == want


def test_a_start_under_another_script_hash_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path, "a" * 64)
    psm.CpuLedger.begin(path, "a" * 64)
    with pytest.raises(psm.ResumeRefused, match="hash"):
        psm.CpuLedger.begin(path, "b" * 64)


def test_finalise_under_another_script_hash_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path, "a" * 64)
    psm.check_script(path, "a" * 64)
    with pytest.raises(psm.ResumeRefused, match="hash"):
        psm.check_script(path, "b" * 64)


def test_finalise_at_the_cpu_stop_is_not_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path, "a" * 64).update(usage_usec=78 * HOUR_USEC, wall_s=1.0)
    psm.check_script(path, "a" * 64)


def test_each_reading_is_fsynced_with_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = psm.CpuLedger.begin(tmp_path / "cpu.json")
    calls: list[int] = []
    real = os.fsync

    def counted(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(psm.os, "fsync", counted)
    ledger.update(usage_usec=5, wall_s=1.0)
    assert len(calls) == 2  # the file, then the rename in its directory


def test_cpu_json_is_kept_current_during_the_run(tmp_path: Path) -> None:
    paths = psm.OutPaths(tmp_path)
    run = driver(tmp_path, [stage(ToyJob("a"))], read_usage_usec=lambda: 7)
    assert run.run() == "complete"
    assert json.loads(paths.cpu.read_text())["current_usec"] == 7


def test_the_launch_line_gives_the_remaining_wall_time(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    psm.CpuLedger.begin(path).update(usage_usec=1, wall_s=3600.0)
    line = psm.launch_line(
        psm.read_wall_s(path), Path("/w"), Path("/s"), Path("/st"), Path("/o")
    )
    assert f"RuntimeMaxSec={59 * 3600}" in line
    assert "--workers 2" in line
    assert "--split /s" in line
    assert "--store /st" in line
    assert "--out /o" in line
    assert "--working-directory=/w" in line
    assert "OOMPolicy=continue" in line
    assert "CPUQuota=200%" in line
    assert "MemoryMax=5G" in line
    assert "--nice=10" in line
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        assert f"-E {name}=1" in line


def test_no_launch_line_is_offered_when_a_resume_would_be_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cpu.json"
    ledger = psm.CpuLedger.begin(path)
    args = (path, Path("/w"), Path("/s"), Path("/st"), Path("/o"))
    assert psm.resume_line(*args) is not None
    ledger.update(usage_usec=78 * HOUR_USEC, wall_s=1.0)
    assert psm.resume_line(*args) is None
    ledger.update(usage_usec=1, wall_s=60 * 3600.0)
    assert psm.resume_line(*args) is None


@pytest.mark.parametrize(
    ("end", "offered"),
    [
        ("cpu_stop", False),
        ("wall_limit", False),
        ("killed", True),
        ("terminated", True),
        ("stop", False),
        ("byte_fail", False),
        ("bug", False),
        ("complete", False),
    ],
)
def test_a_resume_is_offered_only_where_section_5_allows_one(
    end: str, offered: bool
) -> None:
    assert psm.offers_resume(end) is offered


def test_more_than_two_workers_is_refused() -> None:
    with pytest.raises(SystemExit):
        psm.build_parser().parse_args(
            ["--split", "/s", "--store", "/st", "--out", "/o", "--workers", "3"]
        )


# --------------------------------------------------------------------------
# Start-up guards (section 2.10)
# --------------------------------------------------------------------------

GIT: Final = (
    "git",
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@example.org",
    "-c",
    "commit.gpgsign=false",
)
ONES: Final = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}
SCRIPT_PATH: Final = "scripts/pilot_search_misses.py"
TEST_PATH: Final = "tests/campaign/test_pilot_search_misses.py"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [*GIT, *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def write(repo: Path, relative: str, text: str = "x\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def commit_r(repo: Path, script: str = "x\n", test: str = "x\n") -> str:
    """R: the two files, and the LOG entry with their hashes."""
    write(repo, SCRIPT_PATH, script)
    write(repo, TEST_PATH, test)
    a = hashlib.sha256(script.encode()).hexdigest()
    b = hashlib.sha256(test.encode()).hexdigest()
    write(repo, "docs/v2/LOG.md", f"R: script {a}, test {b}\n")
    return commit(repo, "R")


@dataclass(frozen=True)
class Frozen:
    repo: Path
    base: str
    f: str
    sha: str


@pytest.fixture(scope="module")
def frozen_template(tmp_path_factory: pytest.TempPathFactory) -> Frozen:
    repo = tmp_path_factory.mktemp("frozen")
    git(repo, "init", "-q", "-b", "main")
    for path in psm.GUARDED_PATHS:
        write(repo, f"{path}/a.py" if "." not in Path(path).name else path)
    base = commit(repo, "base")
    write(repo, psm.PREREG_PATH, "the rule\n")
    write(repo, "docs/v2/LOG.md")
    f = commit(repo, "F")
    commit_r(repo)
    sha = hashlib.sha256(b"the rule\n").hexdigest()
    return Frozen(repo, base, f, sha)


@pytest.fixture
def frozen(frozen_template: Frozen, tmp_path: Path) -> Frozen:
    repo = tmp_path / "repo"
    shutil.copytree(frozen_template.repo, repo)
    return replace(frozen_template, repo=repo)


def guards(frozen: Frozen, **over: Any) -> Any:
    options: dict[str, Any] = {
        "prereg_sha": frozen.sha,
        "f_commit": frozen.f,
        "base_commit": frozen.base,
        "environ": ONES,
        "packages": (),
        **over,
    }
    return psm.run_guards(frozen.repo, **options)


def test_the_guards_pass_on_a_frozen_tree_and_name_f_r_and_the_hash(
    frozen: Frozen,
) -> None:
    identity = guards(frozen)
    assert identity == {
        "F": frozen.f,
        "R": git(frozen.repo, "rev-parse", "HEAD"),
        "prereg_sha256": frozen.sha,
    }


def test_a_docs_commit_after_r_is_allowed(frozen: Frozen) -> None:
    write(frozen.repo, "docs/v2/JOURNAL.md")
    commit(frozen.repo, "a note")
    guards(frozen)


def test_an_empty_hash_is_refused(frozen: Frozen) -> None:
    with pytest.raises(psm.GuardFailure):
        guards(frozen, prereg_sha="")


def test_a_wrong_hash_is_refused(frozen: Frozen) -> None:
    with pytest.raises(psm.GuardFailure):
        guards(frozen, prereg_sha="0" * 64)


def test_an_unset_f_is_refused(frozen: Frozen) -> None:
    with pytest.raises(psm.GuardFailure):
        guards(frozen, f_commit="")


def test_an_edit_to_the_prereg_after_f_is_refused(frozen: Frozen) -> None:
    write(frozen.repo, psm.PREREG_PATH, "the rule, changed\n")
    new = hashlib.sha256(b"the rule, changed\n").hexdigest()
    commit(frozen.repo, "edit")
    with pytest.raises(psm.GuardFailure, match=r"prereg|F"):
        guards(frozen, prereg_sha=new)


def test_a_third_file_outside_docs_is_refused(frozen: Frozen) -> None:
    write(frozen.repo, "scripts/other.py")
    commit(frozen.repo, "extra")
    with pytest.raises(psm.GuardFailure):
        guards(frozen)


def test_the_two_files_must_both_be_in_the_diff(frozen: Frozen) -> None:
    (frozen.repo / TEST_PATH).unlink()
    commit(frozen.repo, "drop the test")
    with pytest.raises(psm.GuardFailure):
        guards(frozen)


def test_a_head_that_does_not_descend_from_f_is_refused(frozen: Frozen) -> None:
    head = git(frozen.repo, "rev-parse", "HEAD")
    git(frozen.repo, "checkout", "-q", frozen.base)
    write(frozen.repo, psm.PREREG_PATH, "the rule\n")
    assert commit_r(frozen.repo) != head
    with pytest.raises(psm.GuardFailure):
        guards(frozen)


@pytest.mark.parametrize(
    "path",
    [
        "src/sciagent/glm/a.py",
        "src/sciagent/core/a.py",
        "src/sciagent/scoring/a.py",
        "src/sciagent/systems/a.py",
        "src/sciagent/scenarios/a.py",
        "src/sciagent/campaign/analysis.py",
        "src/sciagent/campaign/execute.py",
        "src/sciagent/campaign/plan.py",
        "src/environments/a.py",
        "scripts/pilot.py",
        "pyproject.toml",
        "uv.lock",
    ],
)
def test_a_change_under_a_guarded_path_is_refused(
    frozen_template: Frozen, tmp_path: Path, path: str
) -> None:
    """The change lands before F, so only the base diff can see it."""
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(frozen_template.repo), str(repo))
    git(repo, "checkout", "-q", frozen_template.base)
    write(repo, path, "changed\n")
    commit(repo, "a change since base")
    write(repo, psm.PREREG_PATH, "the rule\n")
    write(repo, "docs/v2/LOG.md")
    f = commit(repo, "F")
    commit_r(repo)
    frozen = Frozen(repo, frozen_template.base, f, frozen_template.sha)
    with pytest.raises(psm.GuardFailure, match=r"900e66a|base"):
        guards(frozen)


def test_a_change_outside_the_guarded_paths_before_f_is_allowed(
    frozen_template: Frozen, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(frozen_template.repo), str(repo))
    git(repo, "checkout", "-q", frozen_template.base)
    write(repo, "src/sciagent/campaign/audit.py")
    commit(repo, "allowed by the text")
    write(repo, psm.PREREG_PATH, "the rule\n")
    write(repo, "docs/v2/LOG.md")
    f = commit(repo, "F")
    commit_r(repo)
    guards(Frozen(repo, frozen_template.base, f, frozen_template.sha))


def test_a_script_changed_after_r_is_refused_though_the_diff_passes(
    frozen: Frozen,
) -> None:
    """Round 2, finding 4: R' touches the same two files, and the LOG entry
    at R does not name its hash."""
    write(frozen.repo, SCRIPT_PATH, "edited after R\n")
    commit(frozen.repo, "R prime")
    with pytest.raises(psm.GuardFailure, match="LOG"):
        guards(frozen)


def test_a_test_changed_after_r_is_refused(frozen: Frozen) -> None:
    write(frozen.repo, TEST_PATH, "edited after R\n")
    commit(frozen.repo, "R prime")
    with pytest.raises(psm.GuardFailure, match="LOG"):
        guards(frozen)


def test_a_modified_tracked_file_is_refused(frozen: Frozen) -> None:
    write(frozen.repo, SCRIPT_PATH, "edited\n")
    with pytest.raises(psm.GuardFailure):
        guards(frozen)


def test_an_untracked_file_is_refused(frozen: Frozen) -> None:
    write(frozen.repo, "stray.txt")
    with pytest.raises(psm.GuardFailure):
        guards(frozen)


@pytest.mark.parametrize("name", sorted(ONES))
@pytest.mark.parametrize("value", [None, "2", ""])
def test_blas_threads_must_be_one(frozen: Frozen, name: str, value: str | None) -> None:
    environ = {k: v for k, v in ONES.items() if k != name}
    if value is not None:
        environ[name] = value
    with pytest.raises(psm.GuardFailure):
        guards(frozen, environ=environ)


@pytest.mark.slow
def test_the_blas_guard_reads_the_environment_the_process_started_with() -> None:
    """``import pilot`` sets the three variables, so ``os.environ`` proves nothing."""
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(REPO / 'scripts')!r});"
        "import pilot_search_misses as p, os;"
        "print(os.environ['OMP_NUM_THREADS'], p.initial_environment().get("
        "'OMP_NUM_THREADS', 'unset'))"
    )
    env = {k: v for k, v in os.environ.items() if k not in ONES}
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.split() == ["1", "unset"]


# --------------------------------------------------------------------------
# The split (section 2.10)
# --------------------------------------------------------------------------


def toy_split(root: Path) -> Path:
    split = root / "data" / "v2" / "truths" / "dev"
    for name, text in (
        ("manifest.json", "{}"),
        ("dev-001.json", "a"),
        ("dev-010.json", "b"),
        ("Z.json", "c"),
        ("sub/dev-002.json", "d"),
        ("sub-x.json", "e"),
        ("sub.json", "f"),
    ):
        path = split / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return split


def test_the_split_hash_is_the_texts_shell_pipeline(tmp_path: Path) -> None:
    split = toy_split(tmp_path)
    done = subprocess.run(
        "find data/v2/truths/dev -type f -print0 | sort -z "
        "| xargs -0 sha256sum | sha256sum",
        shell=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )
    assert psm.split_digest(split) == done.stdout.split()[0]


def test_the_split_hash_does_not_depend_on_the_checkout_it_is_under(
    tmp_path: Path,
) -> None:
    a, b = tmp_path / "a", tmp_path / "somewhere" / "else"
    assert psm.split_digest(toy_split(a)) == psm.split_digest(toy_split(b))


def test_a_changed_file_changes_the_split_hash(tmp_path: Path) -> None:
    split = toy_split(tmp_path)
    before = psm.split_digest(split)
    (split / "dev-001.json").write_text("changed", encoding="utf-8")
    assert psm.split_digest(split) != before


def test_a_split_hash_mismatch_stops(tmp_path: Path) -> None:
    with pytest.raises(psm.GuardFailure):
        psm.check_split(toy_split(tmp_path), expected="0" * 64)


def test_the_matching_hash_passes(tmp_path: Path) -> None:
    split = toy_split(tmp_path)
    psm.check_split(split, expected=psm.split_digest(split))


def test_the_absolute_split_is_what_reaches_load_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pilot.py`` defaults to the worktree's own ``data/``: nothing may fall
    back to it (QUEUE.md:88)."""
    seen: list[Path] = []
    records: tuple[Any, ...] = ()

    def load(directory: Path) -> tuple[Any, ...]:
        seen.append(directory)
        return records

    monkeypatch.setattr(psm.pilot, "load_split", load)
    monkeypatch.setattr(psm.pilot, "pointproc_environment", lambda r: "env")
    monkeypatch.setattr(psm.pilot, "truth_entries", lambda r: ())
    split = Path("/elsewhere/data/v2/truths/dev")
    assert split != psm.pilot.DEFAULT_SPLIT
    psm.pilot_inputs(split)
    assert seen
    assert set(seen) == {split}


def test_a_relative_split_or_store_or_out_is_refused(tmp_path: Path) -> None:
    good = {
        "split": tmp_path / "s",
        "store": tmp_path / "st",
        "out": tmp_path / "o",
        "repo": tmp_path / "r",
    }
    psm.check_paths(**good)
    for name in ("split", "store", "out"):
        with pytest.raises(psm.GuardFailure):
            psm.check_paths(**{**good, name: Path("relative/path")})


def test_the_output_must_be_outside_the_repository_and_the_store(
    tmp_path: Path,
) -> None:
    repo, store = tmp_path / "repo", tmp_path / "store"
    good = {"split": tmp_path / "s", "store": store, "repo": repo}
    with pytest.raises(psm.GuardFailure):
        psm.check_paths(out=repo / "records", **good)
    with pytest.raises(psm.GuardFailure):
        psm.check_paths(out=store / "records", **good)


# --------------------------------------------------------------------------
# The store: read only, manifest, missing cells (sections 2.8, 2.10)
# --------------------------------------------------------------------------


def toy_store(root: Path) -> tuple[Any, Any, Any]:
    env = environment()
    config = tiny_config(llm_arms=(), seeds=(1,), baselines=("B-rand",))
    truths = (entry("hawkes"),)
    with ResultStore(root) as store:
        for unit in baseline_units(["hawkes"], config):
            store.put(
                baseline_key(unit, truths[0], env, config),
                {"status": 1.0, "per_event": -0.75, "certified": 1.0},
                {"result": {"structure": "x"}},
            )
    return env, config, truths


def test_opening_the_store_read_only_leaves_the_manifest_unchanged(
    tmp_path: Path,
) -> None:
    env, config, truths = toy_store(tmp_path)
    before = psm.store_manifest(tmp_path)
    with ResultStore(tmp_path, read_only=True) as store:
        analysis.load_rows(store, env, config, truths, sessions="stored")
    assert psm.manifest_diff(before, psm.store_manifest(tmp_path)) == []


def test_the_manifest_sees_a_changed_an_added_and_a_removed_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "details").mkdir()
    a, b = tmp_path / "details" / "a.json", tmp_path / "b"
    a.write_text("1")
    b.write_text("2")
    before = psm.store_manifest(tmp_path)
    assert sorted(before) == ["b", "details/a.json"]
    a.write_text("3")
    b.unlink()
    (tmp_path / "c").write_text("4")
    assert psm.manifest_diff(before, psm.store_manifest(tmp_path)) == [
        "added c",
        "changed details/a.json",
        "removed b",
    ]


def test_cells_are_read_at_the_rows_address(tmp_path: Path) -> None:
    env, config, truths = toy_store(tmp_path)
    with ResultStore(tmp_path, read_only=True) as store:
        cells = psm.stored_cells(store, env, config, truths)
    assert sorted(cells) == [("hawkes", 1, "B-rand")]
    assert cells[("hawkes", 1, "B-rand")]["reading"]["per_event"] == (-0.75).hex()


def test_a_missing_cell_stops(tmp_path: Path) -> None:
    env, config, truths = toy_store(tmp_path)
    wider = replace(config, seeds=(1, 2))
    with ResultStore(tmp_path, read_only=True) as store:
        table = analysis.load_rows(store, env, wider, truths, sessions="stored")
        cells = psm.stored_cells(store, env, wider, truths)
    with pytest.raises(psm.GuardFailure, match="missing"):
        psm.check_table(table, cells, expected_rows=1)


def test_a_wrong_row_count_stops() -> None:
    """A session with no score cell is not in ``table.missing``; only the
    count sees it."""
    table = replace(toy_table(), missing={})
    cells = {
        (r.truth, r.seed, r.system): {
            "key": {"config": {"fit_config": "full=512|sweeps=0"}}
        }
        for r in table.rows
    }
    with pytest.raises(psm.GuardFailure):
        psm.check_table(table, cells, expected_rows=len(table.rows) + 1)
    psm.check_table(table, cells, expected_rows=len(table.rows))


def test_a_cell_with_another_grid_cap_stops() -> None:
    table = toy_table()
    keys = [(r.truth, r.seed, r.system) for r in table.rows]
    cells = {
        k: {"key": {"config": {"fit_config": "x|full=512|sweeps=0|y"}}} for k in keys
    }
    psm.check_table(table, cells, expected_rows=len(keys))
    cells[keys[0]] = {"key": {"config": {"fit_config": "x|full=513|sweeps=0"}}}
    with pytest.raises(psm.GuardFailure, match="full=512"):
        psm.check_table(table, cells, expected_rows=len(keys))


def test_the_store_is_only_ever_opened_read_only() -> None:
    import ast

    tree = ast.parse(
        (REPO / "scripts" / "pilot_search_misses.py").read_text(encoding="utf-8")
    )
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "ResultStore"
    ]
    assert calls
    for call in calls:
        flags = {k.arg: k.value for k in call.keywords}
        value = flags["read_only"]
        assert isinstance(value, ast.Constant)
        assert value.value is True


@pytest.mark.parametrize("sha", ["", "0" * 64])
@pytest.mark.parametrize("extra", [[], ["--finalise"]])
def test_no_pilot_store_is_touched_without_the_frozen_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sha: str,
    extra: list[str],
) -> None:
    store = tmp_path / "store"
    toy_store(store)
    before = psm.store_manifest(store)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("touched before the guards passed")

    monkeypatch.setattr(psm, "PREREG_SHA256", sha)
    monkeypatch.setattr(psm, "ResultStore", forbidden)
    monkeypatch.setattr(psm.pilot, "load_split", forbidden)
    monkeypatch.setattr(psm, "split_digest", forbidden)
    monkeypatch.setattr(psm, "store_manifest", forbidden)
    out = tmp_path / "out"
    code = psm.main(
        [
            "--split",
            str(tmp_path / "split"),
            "--store",
            str(store),
            "--out",
            str(out),
            *extra,
        ]
    )
    monkeypatch.undo()
    assert code == 3
    assert psm.store_manifest(store) == before
    decision = json.loads((out / "decision.json").read_text())
    assert decision["label"] == "BYTE_FAIL"


def test_a_refused_start_replaces_a_provisional_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "decision.json").write_text('{"label":"x","provisional":true}')
    monkeypatch.setattr(psm, "PREREG_SHA256", "0" * 64)
    assert psm.main(["--split", "/s", "--store", "/st", "--out", str(out)]) == 3
    assert json.loads((out / "decision.json").read_text())["label"] == "BYTE_FAIL"


def test_the_hashes_for_the_log_entry_are_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert psm.main(["--print-hashes"]) == 0
    out = capsys.readouterr().out
    for path in (SCRIPT_PATH, TEST_PATH):
        digest = hashlib.sha256((REPO / path).read_bytes()).hexdigest()
        assert f"{digest}  {path}" in out


def test_split_and_store_are_needed_for_a_run() -> None:
    with pytest.raises(SystemExit):
        psm.main(["--out", "/o"])


def test_a_refused_start_does_not_overwrite_a_finished_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "decision.json").write_text('{"label":"GO_SURVIVES_FOUND_MISSES"}')
    monkeypatch.setattr(psm, "PREREG_SHA256", "0" * 64)
    args = ["--split", "/s", "--store", "/st", "--out", str(out)]
    assert psm.main(args) == 3
    assert json.loads((out / "decision.json").read_text())["label"] == (
        "GO_SURVIVES_FOUND_MISSES"
    )


# --------------------------------------------------------------------------
# The census (section 2.1)
# --------------------------------------------------------------------------


def text_of(features: tuple[Feature, ...], link: Link = Link.EXP) -> str:
    return render(canonicalise(Structure(features, link)))


def cell(certified: float, per_event: float = -0.75, **detail: Any) -> dict[str, Any]:
    return {
        "reading": {"certified": certified.hex(), "per_event": per_event.hex()},
        "detail": {"result": detail},
        "key": {"config": {"fit_config": "full=512|sweeps=0"}},
    }


def census_of(rows: Sequence[Row], cells: dict[tuple[str, int, str], Any]) -> Any:
    table = Table({}, tuple(rows), (), {})
    return psm.build_census(table, cells, psm.T2_CHANNELS)


def test_every_row_lands_in_exactly_one_class() -> None:
    big, small = text_of(MIDDLE), text_of(SMALL)
    rows = [
        table_row("a", 1, "B-sym", -0.7, structure=big),
        table_row("a", 1, "B-rand", -0.7, structure=small),
        table_row("a", 1, "B-sparse", -0.7, structure=small),
        table_row("a", 1, "B-np", -0.7),
        table_row("a", 1, "B-lib", -0.7),
        table_row("a", 1, "AG-o-S/named", None, llm=True, submitted=False),
    ]
    cells = {
        ("a", 1, "B-sym"): cell(1.0),
        ("a", 1, "B-rand"): cell(1.0),
        ("a", 1, "B-sparse"): cell(0.0),
        ("a", 1, "B-np"): cell(math.nan),
        ("a", 1, "B-lib"): cell(math.nan),
        ("a", 1, "AG-o-S/named"): {"reading": {}, "detail": {"result": {}}},
    }
    census = census_of(rows, cells)
    assert {r.system: r.klass for r in census.rows} == {
        "B-sym": "coordinate",
        "B-rand": "exhaustive",
        "B-sparse": "exhaustive_uncertified",
        "B-np": "no_search_np",
        "B-lib": "no_search_lib",
        "AG-o-S/named": "no_submission",
    }


def test_a_structureless_row_of_another_system_is_not_called_b_lib() -> None:
    row = table_row("a", 1, "B-sym", -0.7)
    assert psm.classify_row(row, grid_size=None, certified=None) == "failed"


def test_rows_with_the_same_text_on_the_same_data_share_one_fit() -> None:
    big = text_of(MIDDLE)
    rows = [
        table_row("a", 1, "B-sym", -0.7, structure=big),
        table_row("a", 1, "B-rand", -0.6, structure=big),
        table_row("a", 2, "B-rand", -0.6, structure=big),
    ]
    cells = {(r.truth, r.seed, r.system): cell(1.0, r.per_event or 0.0) for r in rows}
    fits = census_of(rows, cells).fits()
    assert sorted((f.truth, f.seed) for f in fits) == [("a", 1), ("a", 2)]
    shared = next(f for f in fits if f.seed == 1)
    assert [e.system for e in shared.rows] == ["B-rand", "B-sym"]
    assert [e.per_event for e in shared.rows] == [(-0.6).hex(), (-0.7).hex()]


def test_a_fit_only_on_llm_rows_is_tier_2() -> None:
    a, b = text_of(MIDDLE), text_of(MIDDLE, Link.SOFTPLUS)
    rows = [
        table_row("a", 1, "B-sym", -0.7, structure=a),
        table_row("a", 1, "AG-c-S/anon", -0.7, llm=True, structure=a),
        table_row("a", 1, "AG-o-S/anon", -0.7, llm=True, structure=b),
    ]
    cells = {(r.truth, r.seed, r.system): cell(1.0) for r in rows}
    tiers = {f.text: f.tier for f in census_of(rows, cells).fits()}
    assert tiers == {a: 1, b: 2}


def test_the_oracle_cell_fields_reach_the_expectation() -> None:
    big = text_of(MIDDLE)
    row = table_row("a", 1, "ORACLE", -0.7, structure=big)
    fields = {
        "oracle_search_ll": -100.5,
        "oracle_truth_psi_ll": -100.25,
        "oracle_psi_source": "truth",
        "oracle_underfit": False,
    }
    census = census_of([row], {("a", 1, "ORACLE"): cell(1.0, -0.7, **fields)})
    oracle = census.fits()[0].rows[0].oracle
    assert oracle.search_ll == (-100.5).hex()
    assert oracle.truth_psi_ll == (-100.25).hex()
    assert oracle.psi_source == "truth"
    assert oracle.underfit is False


def test_stages_are_in_the_texts_order_and_sorted_by_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(psm, "GROUP_B", ("a", 1))
    monkeypatch.setattr(psm, "HUNT_SIZE", 2)
    texts = [text_of(MIDDLE, link) for link in (Link.EXP, Link.SOFTPLUS)]
    rows = [
        table_row("a", 1, "ORACLE", -0.7, structure=texts[0]),
        table_row("b", 1, "B-sym", -0.7, structure=texts[0]),
        table_row("c", 1, "B-sym", -0.7, structure=texts[1]),
        table_row("d", 1, "B-sparse", -0.7, structure=texts[0]),
        table_row("e", 1, "B-sparse", -0.7, structure=texts[0]),
        table_row("f", 1, "AG-c-S/anon", -0.7, llm=True, structure=texts[1]),
    ]
    cells: dict[tuple[str, int, str], Any] = {
        (r.truth, r.seed, r.system): cell(
            1.0,
            -0.7,
            sparse={"link": "exp"},
            oracle_search_ll=-1.0,
            oracle_psi_source="search",
            oracle_underfit=False,
        )
        for r in rows
    }
    census = census_of(rows, cells)
    truths = {
        t: replace(entry("hawkes"), id=t, in_dictionary=t != "e") for t in "abcdef"
    }
    stages = psm.plan_stages(
        census, truths, environment(), root=TEST_ROOT, log_root=TEST_LOG_ROOT
    )
    assert [s.name for s in stages] == [
        "group_a",
        "group_d",
        "hunt",
        "group_b",
        "tier1",
        "s2",
        "tier2",
    ]
    group_a, group_d, hunt, group_b, tier1, s2, tier2 = (x.jobs for x in stages)
    assert [j.group for j in group_a] == ["A"] * 6
    assert [j.with_r2 for j in group_a] == [False] * 6
    assert [j.cap for j in group_a] == [8_000] * 6
    assert [j.k for j in group_a] == [48] * 6
    (d,) = group_d
    assert (d.group, d.with_r2, d.k) == ("D", True, 0)
    (b,) = group_b
    assert b.truth.id == "a"  # the first pilot unit read
    assert b.group == "B"
    assert b.cap == 8_000
    assert all(j.root == TEST_ROOT for x in stages if x.name != "hunt" for j in x.jobs)
    assert all(j.log_root == TEST_LOG_ROOT for j in hunt)
    assert stages[2].follow_up is not None
    order = [
        hashlib.sha256(psm.fit_case(j.truth.id, j.seed, j.text).encode()).hexdigest()
        for j in tier1
    ]
    assert order == sorted(order)
    assert {j.truth.id for j in tier1} == {"a", "b", "c", "d", "e"}
    assert [j.truth.id for j in tier2] == ["f"]
    assert [j.truth.id for j in s2] == ["e", "d"]  # out-of-dictionary first
    assert stages[2].cpu_cap_s == psm.HUNT_CPU_HOURS * 3600.0


def test_a_fits_cap_follows_its_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psm, "GROUP_B", ("a", 1))
    text = text_of(MIDDLE, Link.IDENTITY)
    rows = [
        table_row("a", 1, "ORACLE", -0.7, structure=text_of(MIDDLE, Link.EXP)),
        table_row("b", 1, "B-sym", -0.7, structure=text),
    ]
    cells: dict[tuple[str, int, str], Any] = {
        (r.truth, r.seed, r.system): cell(1.0, -0.7) for r in rows
    }
    truths = {t: replace(entry("hawkes"), id=t) for t in "ab"}
    stages = psm.plan_stages(
        census_of(rows, cells),
        truths,
        environment(),
        root=TEST_ROOT,
        log_root=TEST_LOG_ROOT,
    )
    caps = {j.truth.id: j.cap for j in stages[4].jobs}
    assert caps == {"a": 8_000, "b": 3_000}


def test_the_hunt_strata_are_the_texts() -> None:
    t2 = t2_module()
    large = (
        Gate(t2.EXP_K, PhaseWindow()),
        Gate(t2.SIZE_K, PhaseWindow()),
        Gate(t2.POWER_K, PhaseWindow()),
    )
    got = [(s.name, s.features, s.link) for s in psm.HUNT_STRATA]
    assert got == [
        ("id-24000", t2.BIG[2], Link.IDENTITY),
        ("id-large", large, Link.IDENTITY),
        ("large-exp", large, Link.EXP),
        ("large-softplus", large, Link.SOFTPLUS),
    ]
    sizes = [_Slots.of(Structure(s.features, s.link)).size for s in psm.HUNT_STRATA]
    assert sizes == [24_000, 3_456_000, 3_456_000, 3_456_000]


def texts_identity_coefficients(
    structure: Structure, rng: np.random.Generator
) -> Coefficients:
    """Section 2.9 in its own words, apart from the script's code."""
    per_feature: list[tuple[float, ...]] = []
    for f in structure.features:
        inner = f.feature if isinstance(f, Gate) else f
        if isinstance(inner, Periodic):
            per_feature.append((0.2, -0.15))
        else:
            per_feature.append(
                tuple(float(rng.uniform(0.1, 0.4)) for _ in range(n_columns(f)))
            )
    return Coefficients(0.5, tuple(per_feature))


@pytest.mark.parametrize("s", [0, 1, 2, 3])
def test_a_hunt_case_is_the_texts_generator(s: int) -> None:
    """On a test root and a short horizon: the draws, in the text's order."""
    t2 = t2_module()
    spec = psm.HUNT_STRATA[s]
    j = 7
    structure = Structure(spec.features, spec.link)
    rng = np.random.default_rng(np.random.SeedSequence([TEST_LOG_ROOT, 1000 * s + j]))
    generating = t2._random_point(structure, rng)
    if spec.link is Link.IDENTITY:
        coefficients = texts_identity_coefficients(structure, rng)
    else:
        coefficients = t2._random_coefficients(structure, rng)
    log = simulate(
        structure, generating, coefficients, t2.CHANNELS, t2.marks, 30.0, rng
    )
    case = psm.hunt_log(spec.name, j, TEST_LOG_ROOT, horizon=30.0)
    assert case.structure == structure
    assert case.generating == generating
    assert case.coefficients == coefficients
    assert case.log.times.tobytes() == log.times.tobytes()


def test_the_hunt_log_root_is_always_stated() -> None:
    root = inspect.signature(psm.hunt_log).parameters["log_root"]
    assert root.default is inspect.Parameter.empty


def test_hunt_cases_run_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psm, "HUNT_SIZE", 2)
    jobs = psm.hunt_jobs(log_root=TEST_LOG_ROOT)
    assert [(j.stratum, j.j) for j in jobs] == [
        (s.name, j) for j in range(2) for s in psm.HUNT_STRATA
    ]
    assert len(psm.HUNT_STRATA) == 4


FIT5_JSON: Final = {"ll": -105.0, "gap": 1e-9, "certified": False}
KNOWN_JSON: Final = {"ll": -100.0, "gap": 1e-9, "certified": True}


def hunt_done(stratum: str, j: int, loss: bool = True) -> dict[str, Any]:
    return {
        "type": "done",
        "job": psm.hunt_job_id(stratum, j),
        "kind": "hunt",
        "stratum": stratum,
        "j": j,
        "found_loss": loss,
        "uncertified_loss": False,
        "sim_failed": False,
        "fit5": FIT5_JSON,
        "known": KNOWN_JSON,
    }


def hunt_r1(stratum: str, j: int, ll: float) -> list[dict[str, Any]]:
    job = psm.hunt_r1_job_id(stratum, j)
    best = {"ll": ll, "gap": 1e-9, "point": [0], "score": ll}
    return [
        {"type": "r1", "job": job, "best": best, "capped": False, "n_points": 10},
        {"type": "done", "job": job, "kind": "hunt_r1", "stratum": stratum, "j": j},
    ]


def due(records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    planted = json.loads(psm.dumps_record(records))
    jobs = psm.hunt_r1_jobs(planted, root=TEST_ROOT, log_root=TEST_LOG_ROOT)
    return [(j.stratum, j.j) for j in jobs]


def test_r1_runs_on_the_first_25_found_losses_of_a_stratum() -> None:
    name = psm.HUNT_STRATA[0].name
    records = [hunt_done(name, j, loss=j % 2 == 0) for j in range(psm.HUNT_SIZE)]
    assert due(records) == [(name, 2 * i) for i in range(25)]


def test_an_unfinished_earlier_case_holds_the_later_ones_back() -> None:
    name = psm.HUNT_STRATA[1].name
    assert due([hunt_done(name, 0), hunt_done(name, 2)]) == [(name, 0)]


def test_what_is_due_does_not_depend_on_arrival_order() -> None:
    a, b = psm.HUNT_STRATA[0].name, psm.HUNT_STRATA[2].name
    records = [hunt_done(n, j) for j in range(40) for n in (a, b)]
    assert due(records) == due(records[::-1])
    assert len(due(records)) == 50


def test_a_failed_case_does_not_hold_the_stratum_back() -> None:
    name = psm.HUNT_STRATA[0].name
    failed = {
        "type": "reference_failed",
        "job": psm.hunt_job_id(name, 1),
        "reason": "ValueError",
    }
    assert due([hunt_done(name, 0), failed, hunt_done(name, 2)]) == [
        (name, 0),
        (name, 2),
    ]


def test_a_failed_simulation_is_counted_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise psm.SimulationError("exploded")

    monkeypatch.setattr(psm, "hunt_log", fail)
    sink = ListSink()
    name = psm.HUNT_STRATA[0].name
    psm.HuntJob(name, 4, TEST_LOG_ROOT).run(sink)
    done = sink.only("done")
    assert done["sim_failed"] is True
    assert done["found_loss"] is False
    done["job"] = psm.hunt_job_id(name, 4)
    summary = psm.hunt_summary([done])
    assert summary[name]["sim_failed"] == 1
    assert summary[name]["completed"] == 0
    earlier = [hunt_done(name, j, loss=j == 3) for j in range(4)]
    assert due([*earlier, done, hunt_done(name, 5)]) == [(name, 3), (name, 5)]


def test_a_hunt_r1_job_takes_its_links_cap() -> None:
    names = {s.name: s for s in psm.HUNT_STRATA}
    records = [hunt_done(n, 0) for n in names]
    planted = json.loads(psm.dumps_record(records))
    jobs = psm.hunt_r1_jobs(planted, root=TEST_ROOT, log_root=TEST_LOG_ROOT)
    assert {j.stratum: j.cap for j in jobs} == {
        "id-24000": 3_000,
        "id-large": 3_000,
        "large-exp": 8_000,
        "large-softplus": 8_000,
    }


def test_the_r1_job_carries_no_known_value() -> None:
    fields = {f.name for f in dataclasses.fields(psm.HuntR1Job)}
    assert fields == {"stratum", "j", "root", "log_root", "k", "cap"}


def test_group_c_is_summarised_from_r1s_own_record() -> None:
    a, b = psm.HUNT_STRATA[0].name, psm.HUNT_STRATA[1].name
    records = [
        hunt_done(a, 0),
        hunt_done(a, 1),
        hunt_done(a, 2),
        hunt_done(a, 3, loss=False),
        hunt_done(b, 0, loss=False),
        *hunt_r1(a, 0, -100.0),
        *hunt_r1(a, 1, -103.0),
    ]
    summary = psm.hunt_summary(json.loads(psm.dumps_record(records)))
    assert summary[a] == {
        "completed": 4,
        "found_losses": 3,
        "r1_run": 2,
        "caught": 1,
        "not_run": 1,
        "uncertified": 0,
        "sim_failed": 0,
    }
    assert summary[b]["completed"] == 1
    state = psm._control_state(summary)
    assert state[a] == "1 of 2 caught"
    assert state[b] == "no known miss, no control"


def test_a_hunt_case_runs_fit5_then_the_fixed_fit_and_no_r1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structure = structure_of()
    generating = _Slots.of(structure).assignment((0, 0))
    case = SimpleNamespace(
        structure=structure, generating=generating, log=toy_data()[0].log
    )
    monkeypatch.setattr(psm, "hunt_log", lambda *a, **k: case)
    sink = ListSink()
    psm.HuntJob("large-exp", 0, TEST_LOG_ROOT).run(sink)
    assert sink.types() == ["done"]
    done = sink.only("done")
    assert done["found_loss"] is False  # fit/5 is exhaustive here
    assert psm.valued_of(done["fit5"]).ll >= psm.valued_of(done["known"]).ll


def test_the_hunts_r1_stream_is_seeded_by_i_not_j(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 2.9: ``i = 1000 * s + j`` in ``c``. A point-set replay cannot see
    this on a small grid, where two streams can reach the same points."""
    case = SimpleNamespace(
        structure=structure_of(), generating=None, log=toy_data()[0].log
    )
    monkeypatch.setattr(psm, "hunt_log", lambda *a, **k: case)
    seen: list[tuple[str, int]] = []
    real = psm.reference_rng

    def capture(c: str, root: int) -> Any:
        seen.append((c, root))
        return real(c, root)

    monkeypatch.setattr(psm, "reference_rng", capture)
    psm.HuntR1Job("large-exp", 3, TEST_ROOT, TEST_LOG_ROOT, 1, 10**6).run(ListSink())
    assert seen == [("hunt|large-exp|2003", TEST_ROOT)]


def test_the_hunts_r1_job_is_the_reference_search_on_the_cases_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structure = structure_of()
    case = SimpleNamespace(structure=structure, generating=None, log=toy_data()[0].log)
    monkeypatch.setattr(psm, "hunt_log", lambda *a, **k: case)
    captured: list[Any] = []
    real = psm.fresh_profile

    def capture(*args: Any) -> Any:
        captured.append(real(*args))
        return captured[-1]

    monkeypatch.setattr(psm, "fresh_profile", capture)
    sink = ListSink()
    psm.HuntR1Job("large-exp", 3, TEST_ROOT, TEST_LOG_ROOT, 1, 10**6).run(sink)
    assert sink.types() == ["r1", "done"]
    assert sink.only("done")["starts_completed"] == 1
    assert set(captured[0].solutions) == replayed_points(
        1, psm.hunt_case("large-exp", 2003)
    )


def test_a_found_loss_does_not_gate_on_fit5s_own_certified_flag() -> None:
    assert psm.found_loss(valued(-100.0, 1e-9, False), valued(-99.0, 1e-9))
    assert psm.found_loss(valued(-100.0, math.inf, False), valued(-99.0, 1e-9))


def test_a_found_loss_needs_the_tolerance_and_a_certified_generating_point() -> None:
    assert psm.found_loss(valued(-100.0), valued(-99.0))
    assert not psm.found_loss(valued(-100.0), valued(-100.0 + 1e-7))
    assert not psm.found_loss(valued(-100.0), valued(-99.0, certified=False))


# --------------------------------------------------------------------------
# S2's link rerun (section 4)
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_the_link_choice_is_choose_links_rule() -> None:
    (obs,) = toy_data()
    train, validation = split_dataset(obs, 0.75)
    config = SparseConfig()
    d: Any = SimpleNamespace(train_datasets=(train,), validation_datasets=(validation,))
    want, _ = _choose_link(SMALL, d, psm.T2_CHANNELS, config, FitConfig())
    got, scores = psm.link_choice(
        SMALL,
        (train,),
        (validation,),
        psm.T2_CHANNELS,
        config.links,
        lambda s: fit(s, (train,), psm.T2_CHANNELS, config=FitConfig()),
    )
    assert got is want
    assert [s[0] for s in scores] == [link.value for link in config.links]


def test_a_link_r0_scored_minus_infinity_stays_there() -> None:
    (obs,) = toy_data()
    train, validation = split_dataset(obs, 0.75)
    fitted: list[Link] = []

    def fitter(s: Structure) -> Any:
        fitted.append(s.link)
        return fit(s, (train,), psm.T2_CHANNELS, config=FitConfig())

    links = (Link.EXP, Link.SOFTPLUS)
    _, scores = psm.link_choice(
        SMALL,
        (train,),
        (validation,),
        psm.T2_CHANNELS,
        links,
        fitter,
        blocked=frozenset({Link.EXP}),
    )
    assert fitted == [Link.SOFTPLUS]
    assert scores[0] == ("exp", -math.inf, False)


def test_s2s_reference_stops_at_its_own_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    (obs,) = toy_data()
    train, _ = split_dataset(obs, 0.75)
    seen: list[Any] = []
    real = psm.reference_search

    def capture(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(psm, "reference_search", capture)
    monkeypatch.setattr(psm, "is_exhaustive", lambda size: False)
    psm.reference_link_fit(
        canonicalise(structure_of()),
        (train,),
        psm.T2_CHANNELS,
        k=2,
        case="s2|toy",
        root=TEST_ROOT,
    )
    assert seen[0]["cap"] == psm.S2_POINT_CAP
    assert seen[0]["r2_max"] == 0


def test_the_reference_link_fit_is_never_worse_than_r0_on_training_data() -> None:
    (obs,) = toy_data()
    train, _ = split_dataset(obs, 0.75)
    s = canonicalise(structure_of())
    base = fit(s, (train,), psm.T2_CHANNELS, config=FitConfig())
    better = psm.reference_link_fit(
        s, (train,), psm.T2_CHANNELS, k=2, case="s2|toy", root=TEST_ROOT
    )
    assert better.log_likelihood >= base.log_likelihood
    assert better.certified


# --------------------------------------------------------------------------
# Labels (section 5) on planted records
# --------------------------------------------------------------------------


def info(r: Row, klass: str, text: str | None) -> Any:
    size = {"coordinate": 1000, "exhaustive": 100}.get(klass)
    return psm.RowInfo(
        truth=r.truth,
        seed=r.seed,
        system=r.system,
        llm=r.llm,
        klass=klass,
        text=text,
        grid_size=size,
        link="exp" if text else None,
        certified=klass != "exhaustive_uncertified",
        expect=psm.RowExpect(r.system, r.llm, "", None),
    )


def planted_census(table: Table, *, uncertified: bool = False) -> Any:
    rows = []
    for r in table.rows:
        if r.system == "B-np":
            rows.append(info(r, "no_search_np", None))
        elif r.system == "B-lib":
            klass = "exhaustive_uncertified" if uncertified else "exhaustive"
            rows.append(info(r, klass, f"{r.system} {r.truth} {r.seed}"))
        else:
            rows.append(info(r, "coordinate", f"{r.system} {r.truth} {r.seed}"))
    return psm.Census(tuple(rows))


def row_result(
    system: str,
    *,
    kind: str = "none",
    llm: bool = False,
    corrected: float | None = None,
    stored: float = -0.7,
    underfit: bool | None = None,
    gain: float = 0.0,
) -> dict[str, Any]:
    delta, delta_kind = (None, None)
    if corrected is not None:
        delta, delta_kind = psm.held_out_delta(stored, corrected)
    return {
        "system": system,
        "llm": llm,
        "call": kind,
        "gain": gain,
        "tol": 3e-7,
        "stored_uncertified": False,
        "uncertified_better": 0,
        "stored_ll": -100.0,
        "stored_gap": 1e-9,
        "stored_certified": True,
        "chosen_ll": -100.0 + gain,
        "stored_per_event": stored,
        "corrected_per_event": corrected,
        "delta": delta,
        "delta_kind": delta_kind,
        "corrected_underfit": underfit,
        "corrected_similarity": None,
        "oracle": None,
        "stored_quadrature_error": 1e-6,
        "chosen_quadrature_error": 1e-6,
        "within_quadrature": False,
    }


def done_records(
    census: Any,
    *,
    evidence: str = "searched",
    rows: dict[tuple[str, int, str], dict[str, Any]] | None = None,
    skip: Callable[[Any], bool] = lambda f: False,
) -> list[dict[str, Any]]:
    rows = rows or {}
    out: list[dict[str, Any]] = []
    for f in census.fits():
        if skip(f):
            continue
        job = psm.fit_job_id(f.truth, f.seed, f.text)
        results = [
            rows.get((f.truth, f.seed, r.system), row_result(r.system, llm=r.llm))
            for r in f.rows
        ]
        out.append(
            {
                "type": "r1",
                "job": job,
                "best": {"ll": -100.0, "gap": 1e-9, "point": [0], "score": -100.0},
                "capped": False,
                "n_points": 10,
            }
        )
        out.append(
            {
                "type": "done",
                "job": job,
                "kind": "fit",
                "truth": f.truth,
                "seed": f.seed,
                "text": f.text,
                "evidence": evidence,
                "capped": False,
                "cpu_cut": False,
                "starts_completed": 48,
                "r2_complete": evidence == "exact",
                "grid_size": 1000 if evidence == "exact" else 20_000,
                "link": "exp",
                "n_uncertified": 0,
                "rows": results,
            }
        )
    return out


def control_records(
    *, a: int = 6, b: bool = True, d: bool = True
) -> list[dict[str, Any]]:
    """Group A with ``a`` caught, then B and D. The chosen value is always
    the known one, so a control that read it would always pass."""
    known = {"ll": -100.0, "gap": 1e-9, "certified": True}
    fit5 = {"ll": -105.0, "gap": 1e-9, "certified": True}
    out: list[dict[str, Any]] = []

    def r1(job: str, caught: bool) -> None:
        ll = -100.0 if caught else -103.0
        out.append(
            {
                "type": "r1",
                "job": job,
                "best": {"ll": ll, "gap": 1e-9, "point": [0], "score": ll},
                "capped": False,
                "n_points": 10,
            }
        )

    for i, case in enumerate(psm.T2_CASES):
        job = f"A{i}"
        r1(job, i < a)
        out.append(
            {
                "type": "done",
                "job": job,
                "kind": "control",
                "group": "A",
                "case": list(case),
                "fit5": fit5,
                "known": known,
                "chosen": known,
            }
        )
    r1("B", b)
    out.append(
        {
            "type": "done",
            "job": "B",
            "kind": "fit",
            "truth": psm.GROUP_B[0],
            "seed": psm.GROUP_B[1],
            "text": "group B",
            "r2_complete": False,
            "grid_size": 20_736,
            "capped": False,
            "rows": [],
            "control": {
                "group": "B",
                "case": list(psm.GROUP_B),
                "fit5": fit5,
                "known": known,
            },
        }
    )
    best = known if d else {"ll": -103.0, "gap": 1e-9, "certified": True}
    out.append(
        {
            "type": "done",
            "job": "D",
            "kind": "control",
            "group": "D",
            "case": list(psm.T2_CASES[0]),
            "fit5": fit5,
            "known": known,
            "r2_best": best,
            "chosen": known,
        }
    )
    return out


IDENTITY: Final = {"F": "f" * 40, "R": "a" * 40, "prereg_sha256": "b" * 64}


def decide(
    table: Table,
    records: list[dict[str, Any]],
    *,
    census: Any = None,
    end: str = "complete",
    stop_note: str | None = None,
    manifest_failures: Sequence[str] = (),
    **over: Any,
) -> Any:
    tail = [{"type": "run_start"}, {"type": "run_end", "reason": end}]
    planted = json.loads(psm.dumps_record(records + tail))
    return psm.decide(
        planted,
        census or planted_census(table),
        table,
        identity=IDENTITY,
        stop_note=stop_note,
        manifest_failures=tuple(manifest_failures),
        resamples=200,
        **over,
    )


@pytest.fixture(scope="module")
def go_table() -> Table:
    return toy_table()


def clean(table: Table, **kw: Any) -> list[dict[str, Any]]:
    return [*control_records(**kw), *done_records(planted_census(table))]


def test_a_clean_run_survives_with_the_fixed_wording(go_table: Table) -> None:
    d = decide(go_table, clean(go_table))
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["exit_code"] == 0
    assert d["M_rows"] == 0
    census = planted_census(go_table)
    verdict_fits = {
        (r.truth, r.seed, r.text)
        for r in census.rows
        if not r.llm and r.klass in ("coordinate", "exhaustive")
    }
    n = len(verdict_fits)
    exact = sum(r.klass == "exhaustive" and not r.llm for r in census.rows)
    outside = sum(
        not r.llm and r.klass in ("no_search_np", "no_search_lib") for r in census.rows
    )
    assert d["statement"] == (
        "The computed GO survives substitution of the 0 misses found (N counts "
        f"baseline rows, the ones the verdict reads). Of the {n + outside} fits the "
        f"verdict reads, {exact} are exact over their certified grid points, "
        f"{n - exact} are searched and unvalidated, 0 are exhaustive but uncertified "
        f"and were not checked, and {outside} are outside the check (B-np, B-lib's "
        "out-of-grammar members). Nothing is said about misses on the "
        f"{n - exact} fits that the search did not find."
    )
    assert d["provisional"] is False
    assert d["H1"]["falsified"] is False
    assert d["H2"]["falsified"] is False
    assert d["H3"]["falsified"] is False
    assert d["H4"]["falsified"] is False


def test_the_statement_counts_exhaustive_uncertified_apart(go_table: Table) -> None:
    census = planted_census(go_table, uncertified=True)
    d = decide(
        go_table,
        [*control_records(), *done_records(census)],
        census=census,
    )
    n_lib = sum(r.klass == "exhaustive_uncertified" for r in census.rows)
    assert (
        f", {n_lib} are exhaustive but uncertified and were not checked,"
        in (d["statement"])
    )


def test_r2_complete_fits_are_exact_in_the_statement(go_table: Table) -> None:
    census = planted_census(go_table)
    d = decide(
        go_table,
        [*control_records(), *done_records(census, evidence="exact")],
    )
    assert ", 0 are searched and unvalidated," in d["statement"]


def test_a_found_miss_that_leaves_a_go_is_counted(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "B-sparse")
    misses = {key: row_result("B-sparse", kind="miss", corrected=-0.7205, gain=1.0)}
    d = decide(go_table, [*control_records(), *done_records(census, rows=misses)])
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["M_rows"] == 1
    assert d["M_fits"] == 1
    assert d["H2"]["falsified"] is True
    assert "substitution of the 1 misses found (N counts" in d["statement"]


def test_a_large_delta_falsifies_h3_and_nothing_else(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "AG-c-S/named")
    misses = {
        key: row_result(
            "AG-c-S/named", kind="miss", llm=True, corrected=-0.60, gain=1.0
        )
    }
    d = decide(go_table, [*control_records(), *done_records(census, rows=misses)])
    assert d["H3"]["falsified"] is True
    assert d["H4"]["falsified"] is False
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"


def test_go_changes_stands_whatever_the_control_did(go_table: Table) -> None:
    census = planted_census(go_table)
    misses = {
        (t, s, "B-sparse"): row_result(
            "B-sparse", kind="miss", corrected=-0.7 - 0.001 * s, gain=1.0
        )
        for t in TRUTHS
        for s in SEEDS
    }
    records = done_records(census, rows=misses)
    for controls in (control_records(), control_records(a=0, b=False, d=False)):
        d = decide(go_table, [*controls, *records])
        assert d["label"] == "GO_CHANGES"
        assert d["exit_code"] == 2
        assert d["statement"] is None
        assert d["clauses_moved"][0]["clause"] == "too_easy"
        assert d["not_put_forward"]
        assert "corrected_gates" in d
        assert "stored_gates" in d


def test_control_fail_makes_no_survival_statement(go_table: Table) -> None:
    d = decide(go_table, clean(go_table, a=4))
    assert d["label"] == "CONTROL_FAIL"
    assert d["exit_code"] == 4
    assert d["statement"] is None


def test_h1_passes_at_five_of_six(go_table: Table) -> None:
    assert decide(go_table, clean(go_table, a=5))["label"] == (
        "GO_SURVIVES_FOUND_MISSES"
    )


@pytest.mark.parametrize("failed", ["b", "d"])
def test_group_b_and_d_must_both_be_caught(go_table: Table, failed: str) -> None:
    d = decide(go_table, clean(go_table, **{failed: False}))
    assert d["label"] == "CONTROL_FAIL"


def test_a_control_cannot_pass_on_the_chosen_point() -> None:
    """Every planted record's ``chosen`` is the known value; R1's own best
    below it must fail regardless."""
    status = psm.h1_status(json.loads(psm.dumps_record(control_records(a=0, b=False))))
    assert status.a_caught == 0
    assert status.b_caught is False
    assert status.stands is False


def test_control_catch_needs_the_known_value_and_a_gain_over_fit5() -> None:
    fit5, known = valued(-105.0, 1e-9), valued(-100.0, 1e-9)
    assert psm.control_caught(valued(-100.0, 1e-9), fit5, known)
    assert psm.control_caught(valued(-99.0, 1e-9), fit5, known)
    assert psm.control_caught(valued(-100.0 - 1e-7, 1e-9), fit5, known)
    assert not psm.control_caught(valued(-101.0, 1e-9), fit5, known)
    assert not psm.control_caught(None, fit5, known)
    same = valued(-105.0, 1e-9)
    assert not psm.control_caught(same, fit5, same)


def test_a_missing_tier_1_fit_is_incomplete_and_partial(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.truth == "t1" and f.tier == 1)
    d = decide(go_table, [*control_records(), *records])
    assert d["label"] == "INCOMPLETE"
    assert d["exit_code"] == 5
    assert d["partial"] is True
    assert d["statement"] is None
    assert "outcome" in d["H4"]


def test_a_reference_failed_tier_1_fit_is_incomplete(go_table: Table) -> None:
    census = planted_census(go_table)
    first = next(f for f in census.fits() if f.tier == 1)
    job = psm.fit_job_id(first.truth, first.seed, first.text)
    records = done_records(census, skip=lambda f: f == first)
    failed = {"type": "reference_failed", "job": job, "reason": "cpu_limit"}
    d = decide(go_table, [*control_records(), *records, failed])
    assert d["label"] == "INCOMPLETE"
    assert d["coverage"]["unchecked_fits"] == 1


def test_a_fit_cut_before_r2_is_searched_and_not_a_shortfall(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census)
    for r in records:
        if r["type"] == "done":
            r["grid_size"] = 14_400
            r["cpu_cut"] = True
    d = decide(go_table, [*control_records(), *records])
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["clock_stops"]["cpu_cut"] > 0


def test_a_stop_inside_tier_1_is_incomplete_and_keeps_its_note(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.truth == "t1" and f.tier == 1)
    d = decide(
        go_table,
        [*control_records(), *records],
        end="stop",
        stop_note="owner: because",
    )
    assert d["label"] == "INCOMPLETE"
    assert d["partial"] is True
    assert d["stop_note"] == "owner: because"
    assert d["statement"] is None


def test_a_stop_after_tier_1_is_a_shortfall(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.tier == 2)
    d = decide(
        go_table,
        [*control_records(), *records],
        end="stop",
        stop_note="owner: because",
    )
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["stop_note"] == "owner: because"
    assert d["end"] == "stop"
    assert d["shortfall"]["tier2_missing"] > 0


def test_no_stop_can_bury_a_flip(go_table: Table) -> None:
    """Round 2, finding 1: a flipped verdict outranks every shortfall."""
    census = planted_census(go_table)
    misses = {
        (t, s, "B-sparse"): row_result(
            "B-sparse", kind="miss", corrected=-0.7 - 0.001 * s, gain=1.0
        )
        for t in TRUTHS
        for s in SEEDS
    }
    records = done_records(
        census, rows=misses, skip=lambda f: f.truth == "t5" and f.tier == 1
    )
    for end in ("stop", "cpu_stop", "wall_limit", "bug", "killed"):
        d = decide(go_table, [*control_records(), *records], end=end)
        assert d["label"] == "GO_CHANGES"
        assert d["partial"] is True
        assert d["exit_code"] == 2


def test_byte_fail_outranks_a_flip(go_table: Table) -> None:
    census = planted_census(go_table)
    misses = {
        (t, s, "B-sparse"): row_result(
            "B-sparse", kind="miss", corrected=-0.7 - 0.001 * s, gain=1.0
        )
        for t in TRUTHS
        for s in SEEDS
    }
    records = done_records(census, rows=misses)
    bad = {"type": "byte_fail", "job": "x", "reason": "planted"}
    assert decide(go_table, [*control_records(), *records, bad])["label"] == "BYTE_FAIL"


def test_an_empty_stop_file_says_no_reason_was_given(
    tmp_path: Path, go_table: Table
) -> None:
    paths = psm.OutPaths(tmp_path)
    plant(paths, *clean(go_table))
    paths.stop.write_text("")
    d = psm.finalise_records(
        paths,
        planted_census(go_table),
        go_table,
        identity=IDENTITY,
        manifest_failures=(),
        resamples=200,
    )
    assert d["stop_note"] == "no reason was given"
    assert d["end"] == "stop"


def test_a_stop_file_alone_counts_as_a_stop_after_a_kill(go_table: Table) -> None:
    d = decide(go_table, clean(go_table), stop_note="owner: because")
    assert d["end"] == "stop"
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"


def test_a_cpu_stop_inside_tier_1_is_incomplete(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.truth == "t1" and f.tier == 1)
    d = decide(go_table, [*control_records(), *records], end="cpu_stop")
    assert d["label"] == "INCOMPLETE"


@pytest.mark.parametrize("end", ["cpu_stop", "wall_limit", "killed"])
def test_a_clock_stop_after_tier_1_is_a_shortfall(go_table: Table, end: str) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.tier == 2)
    d = decide(go_table, [*control_records(), *records], end=end)
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["partial"] is False
    assert d["shortfall"]["tier2_missing"] > 0


def test_a_tier_2_failure_does_not_alter_the_label(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.tier == 2)
    tier2 = next(f for f in census.fits() if f.tier == 2)
    failed = {
        "type": "reference_failed",
        "job": psm.fit_job_id(tier2.truth, tier2.seed, tier2.text),
        "reason": "worker_lost",
    }
    d = decide(go_table, [*control_records(), *records, failed])
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"


def test_a_bug_inside_tier_1_is_incomplete(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.truth == "t1" and f.tier == 1)
    d = decide(go_table, [*control_records(), *records], end="bug")
    assert d["label"] == "INCOMPLETE"


def test_a_bug_after_tier_1_is_a_shortfall(go_table: Table) -> None:
    assert decide(go_table, clean(go_table), end="bug")["label"] == (
        "GO_SURVIVES_FOUND_MISSES"
    )


def test_incomplete_outranks_control_fail(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census, skip=lambda f: f.truth == "t1" and f.tier == 1)
    d = decide(go_table, [*control_records(a=0), *records])
    assert d["label"] == "INCOMPLETE"


def test_r1s_result_stands_when_the_job_dies_in_r2(go_table: Table) -> None:
    """Round 2, finding 7: searched, the miss counts, and no shortfall."""
    census = planted_census(go_table)
    key = ("t1", 1, "B-sym")
    misses = {key: row_result("B-sym", kind="miss", corrected=-0.705, gain=1.0)}
    records = done_records(census, rows=misses)
    target = psm.fit_job_id("t1", 1, "B-sym t1 1")
    done = next(r for r in records if r["type"] == "done" and r["job"] == target)
    records.remove(done)
    r1 = next(r for r in records if r["type"] == "r1" and r["job"] == target)
    r1["result"] = {**done, "type": "r1_result"}
    tail = {"type": "r2_lost", "job": target, "reason": "cpu_limit"}
    d = decide(go_table, [*control_records(), *records, tail])
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["M_rows"] == 1
    assert d["coverage"]["unchecked_fits"] == 0
    assert d["clock_stops"]["r2_lost"] == 1


def test_a_job_killed_in_r2_with_no_terminal_record_keeps_r1_too(
    go_table: Table,
) -> None:
    census = planted_census(go_table)
    records = done_records(census)
    target = psm.fit_job_id("t1", 1, "B-sym t1 1")
    done = next(r for r in records if r["type"] == "done" and r["job"] == target)
    records.remove(done)
    r1 = next(r for r in records if r["type"] == "r1" and r["job"] == target)
    r1["result"] = done
    d = decide(go_table, [*control_records(), *records], end="killed")
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"


def test_byte_fail_comes_first(go_table: Table) -> None:
    records = clean(go_table)
    records.append({"type": "byte_fail", "job": "x", "reason": "planted"})
    d = decide(go_table, records, end="byte_fail")
    assert d["label"] == "BYTE_FAIL"
    assert d["exit_code"] == 3
    assert d["byte_failures"]


def test_a_manifest_difference_is_byte_fail(go_table: Table) -> None:
    d = decide(go_table, clean(go_table), manifest_failures=["changed ledger.sqlite"])
    assert d["label"] == "BYTE_FAIL"


def test_a_miss_inside_quadrature_stays_in_m_and_is_listed(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "B-sparse")
    row = row_result("B-sparse", kind="miss", corrected=-0.7205, gain=1e-7)
    row["within_quadrature"] = True
    d = decide(go_table, [*control_records(), *done_records(census, rows={key: row})])
    assert d["M_rows"] == 1
    assert d["within_quadrature_rows"] == [["t1", 1, "B-sparse"]]
    assert d["H2"]["rests_on_within_quadrature"] is True
    assert d["H2"]["falsified"] is True


def test_every_label_says_what_it_means_for_the_go() -> None:
    assert sorted(psm.GO_NO_GO) == sorted(psm.EXIT_CODES)
    assert psm.GO_NO_GO["GO_CHANGES"]["survived"] == "No, on the misses found so far"
    assert "may yet reverse" in psm.GO_NO_GO["GO_CHANGES"]["next"]
    assert "may not" in psm.GO_NO_GO["INCOMPLETE"]["next"]
    assert psm.GO_NO_GO["CONTROL_FAIL"]["survived"] == "Open"


def test_the_decision_says_what_the_label_means(go_table: Table) -> None:
    d = decide(go_table, clean(go_table, a=0))
    assert d["go_no_go"] == psm.GO_NO_GO["CONTROL_FAIL"]


def test_the_decision_names_f_r_and_the_hash(go_table: Table) -> None:
    d = decide(go_table, clean(go_table))
    for name, value in IDENTITY.items():
        assert d[name] == value


def test_the_decision_is_identical_on_a_rerun(go_table: Table) -> None:
    a = psm.dumps_record(decide(go_table, clean(go_table)))
    b = psm.dumps_record(decide(go_table, clean(go_table)))
    assert a == b


def test_the_gate_line_counts_the_rows_each_gate_reads(go_table: Table) -> None:
    d = decide(go_table, clean(go_table))
    gates = d["gates"]
    assert sorted(gates) == ["C1", "C2", "C3", "too_easy", "too_hard"]
    c3 = gates["C3"]
    assert c3 == {
        "exact": 0,
        "searched": 20,
        "exhaustive_uncertified": 0,
        "not_applicable": 0,
    }
    assert gates["C1"]["exact"] == 15
    assert gates["C1"]["searched"] == 15


def test_the_label_line_gives_the_control_state_of_each_stratum(
    go_table: Table,
) -> None:
    d = decide(go_table, clean(go_table))
    assert sorted(d["control_state"]) == sorted(s.name for s in psm.HUNT_STRATA)
    for state in d["control_state"].values():
        assert state == "no known miss, no control"


@pytest.mark.parametrize(
    ("link", "size", "stratum"),
    [
        ("exp", 20_736, "groups_a_b"),
        ("softplus", 24_000, "groups_a_b"),
        ("exp", 24_001, "large-exp"),
        ("softplus", 3_456_000, "large-softplus"),
        ("identity", 24_000, "id-24000"),
        ("identity", 24_001, "id-large"),
        ("identity", 3_456_000, "id-large"),
        ("exp", 3_456_001, "no_control"),
        ("identity", 3_456_001, "no_control"),
    ],
)
def test_a_searched_fits_stratum_is_fixed_by_link_and_grid(
    link: str, size: int, stratum: str
) -> None:
    assert psm.stratum_of(link, size) == stratum


def test_the_label_line_gives_the_stratum_map(go_table: Table) -> None:
    census = planted_census(go_table)
    big = psm.Census(
        tuple(
            replace(r, grid_size=4_000_000)
            if r.system == "B-sym" and r.klass == "coordinate"
            else r
            for r in census.rows
        )
    )
    d = decide(go_table, [*control_records(), *done_records(big)], census=big)
    strata = d["strata"]
    assert strata["no_control"]["searched_fits"] == 15
    assert strata["no_control"]["control"] == "no control"
    assert strata["groups_a_b"]["control"] == "group A 6 of 6, group B caught"
    assert strata["large-exp"]["control"] == "no known miss, no control"
    assert "no_control: 15 searched fits, no control" in d["label_line"]


def test_a_clock_stop_is_named_beside_the_records(go_table: Table) -> None:
    records = [*clean(go_table), {"type": "stage_cap", "stage": "hunt"}]
    d = decide(go_table, records, end="cpu_stop")
    assert d["clock_stops"]["group_c_cap"] is True
    assert d["clock_stops"]["cpu_stop"] is True


def test_s1_status_floors() -> None:
    assert psm.s1_status(found=0, total=0) == "not_estimable"
    assert psm.s1_status(found=4, total=4) == "not_estimable"
    assert psm.s1_status(found=4, total=5) == "at_floor"
    assert psm.s1_status(found=3, total=5) == "below_floor"
    assert psm.s1_status(found=7, total=10) == "below_floor"
    assert psm.s1_status(found=8, total=10) == "at_floor"


def test_s1_reads_r1s_record_not_r2s_result(go_table: Table) -> None:
    census = planted_census(go_table)
    keys = [("t1", 1, "B-sym"), ("t2", 1, "B-sym"), ("t3", 1, "B-sym")]
    keys += [("t4", 1, "B-sym"), ("t5", 1, "B-sym")]
    misses = {
        k: row_result("B-sym", kind="miss", corrected=-0.705, gain=1.0) for k in keys
    }
    records = done_records(census, evidence="exact", rows=misses)
    d = decide(go_table, [*control_records(), *records])
    assert d["s1_status"] == "below_floor"
    assert d["s1"] == {"found_by_r1": 0, "misses": 5}
    assert (
        "R1 missed more than one in five of the misses on grids where the "
        in (d["label_line"])
    )
    for r in records:
        if r["type"] == "r1":
            r["best"]["ll"] = -99.0
    assert decide(go_table, [*control_records(), *records])["s1_status"] == "at_floor"


def test_the_label_line_says_when_v2_differs(go_table: Table) -> None:
    census = planted_census(go_table)
    s2 = [
        {
            "type": "done",
            "job": f"s2-{t}-{s}",
            "kind": "s2",
            "truth": t,
            "seed": s,
            "status": "differs",
            "corrected_per_event": -0.7 - 0.001 * s,
        }
        for t in TRUTHS
        for s in SEEDS
    ]
    d = decide(go_table, [*control_records(), *done_records(census), *s2])
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["v2_outcome"] == "too easy"
    assert "v2.outcome differs from v.outcome" in d["label_line"]


def test_an_s2_link_mismatch_is_reported_and_cannot_move_the_label(
    go_table: Table,
) -> None:
    census = planted_census(go_table)
    s2 = [
        {
            "type": "done",
            "job": "s2-x",
            "kind": "s2",
            "truth": "t1",
            "seed": 1,
            "status": "link_mismatch",
            "corrected_per_event": None,
        }
    ]
    d = decide(go_table, [*control_records(), *done_records(census), *s2])
    assert d["label"] == "GO_SURVIVES_FOUND_MISSES"
    assert d["s2"]["link_mismatch"] == 1


def test_a_miss_row_with_a_nan_reading_cannot_enter_corrected(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "B-sparse")
    bad = row_result("B-sparse", kind="miss", gain=1.0)
    bad["corrected_per_event"] = math.nan
    with pytest.raises(psm.ByteFailure):
        decide(go_table, [*control_records(), *done_records(census, rows={key: bad})])


def test_the_coverage_line_has_every_class(go_table: Table) -> None:
    d = decide(go_table, clean(go_table))
    assert sorted(d["coverage"]) == sorted(
        [
            "exact_rows",
            "exact_fits",
            "searched_rows",
            "searched_fits",
            "unchecked_rows",
            "unchecked_fits",
            "capped_fits",
            "not_applicable_rows",
        ]
    )
    assert d["coverage"]["not_applicable_rows"] == 15


def test_oracle_search_misses_are_counted_apart(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "ORACLE")
    result = row_result("ORACLE")
    result["oracle"] = {
        "search_call": "miss",
        "search_gain": 4.53,
        "search_below_truth_psi": True,
    }
    d = decide(
        go_table, [*control_records(), *done_records(census, rows={key: result})]
    )
    assert d["M_rows"] == 0
    assert d["s3"]["oracle_search_misses"] == 1
    assert d["s3"]["oracle_search_below_truth_psi"] == 1


def test_completed_starts_are_summarised_over_the_searched_fits(
    go_table: Table,
) -> None:
    census = planted_census(go_table)
    records = done_records(census)
    done = [r for r in records if r["type"] == "done"]
    done[0]["starts_completed"] = 12
    done[0]["capped"] = True
    d = decide(go_table, [*control_records(), *records])
    spread = d["s3"]["starts_completed"]
    assert spread["n"] == len(done)
    assert float.fromhex(psm.dumps_record(spread["min"]).strip('"')) == 12
    assert float.fromhex(psm.dumps_record(spread["max"]).strip('"')) == 48


def test_s4_reads_each_gate_stored_and_corrected(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "B-sparse")
    misses = {key: row_result("B-sparse", kind="miss", corrected=-0.7205, gain=1.0)}
    d = decide(go_table, [*control_records(), *done_records(census, rows=misses)])
    (row,) = d["s4"]["rows"]
    assert row["key"] == ["t1", 1, "B-sparse"]
    assert row["gap_stored"] != row["gap_corrected"]
    assert d["s4"]["gaps_above_gate"] == 0
    assert d["corrected_gates"]["C3"]["median_gap"] is not None


def test_the_decision_does_not_depend_on_the_order_records_arrived(
    go_table: Table,
) -> None:
    records = clean(go_table)
    records.append({"type": "byte_fail", "job": "x", "reason": "planted a"})
    records.append({"type": "byte_fail", "job": "y", "reason": "planted b"})
    a = psm.dumps_record(decide(go_table, records, end="byte_fail"))
    b = psm.dumps_record(decide(go_table, records[::-1], end="byte_fail"))
    assert a == b


def test_m_fits_counts_fits_not_rows(go_table: Table) -> None:
    census = planted_census(go_table)
    records = done_records(census)
    shared = next(r for r in records if r["type"] == "done")
    shared["rows"] = [
        row_result("B-sym", kind="miss", corrected=-0.705, gain=1.0),
        row_result("B-rand", kind="miss", corrected=-0.705, gain=1.0),
    ]
    shared["truth"], shared["seed"] = "t1", 1
    d = decide(go_table, [*control_records(), *records])
    assert d["M_rows"] == 2
    assert d["M_fits"] == 1


def test_the_decision_gives_the_class_table_and_each_miss_row(go_table: Table) -> None:
    census = planted_census(go_table)
    key = ("t1", 1, "B-sparse")
    misses = {key: row_result("B-sparse", kind="miss", corrected=-0.7205, gain=1.0)}
    d = decide(go_table, [*control_records(), *done_records(census, rows=misses)])
    assert d["classes"]["no_search_np"] == 15
    assert d["classes"]["exhaustive"] == 15
    assert sum(d["classes"].values()) == len(census.rows)
    (row,) = d["miss_rows"]
    assert row["key"] == ["t1", 1, "B-sparse"]
    assert row["evidence"] == "searched"
    assert row["delta_kind"] == "finite"
    assert d["M_by_system"] == {"B-sparse": 1}


def test_h3_and_h4_say_when_they_are_vacuous(go_table: Table) -> None:
    d = decide(go_table, clean(go_table))
    assert d["H3"]["vacuous"] is True
    assert d["H4"]["vacuous"] is True


def test_the_label_line_gives_the_controls_and_identity_uncertified(
    go_table: Table,
) -> None:
    line = decide(go_table, clean(go_table, a=5))["label_line"]
    assert "group A 5 of 6" in line
    assert "uncertified_better on identity 0" in line


# --------------------------------------------------------------------------
# Finalise (sections 2.10, 8): from records alone, after any kind of death
# --------------------------------------------------------------------------


def test_finalise_works_from_records_left_by_a_real_kill(
    tmp_path: Path, go_table: Table
) -> None:
    paths = psm.OutPaths(tmp_path)
    context = multiprocessing.get_context("fork")
    jobs = [stage(ToyJob("first"), ToyJob("second", "hang"))]
    process = context.Process(target=_run_driver, args=(tmp_path, jobs))
    process.start()
    _wait_for(lambda: paths.journal.exists() and "first" in paths.journal.read_text())
    assert process.pid is not None
    os.killpg(process.pid, signal.SIGKILL)
    process.join(20)
    assert process.exitcode == -signal.SIGKILL
    with paths.journal.open("ab") as f:
        f.write(b'{"type":"done","job":"sec')  # a torn write, as a crash leaves
    records = psm.read_records(paths.journal)
    assert psm.end_state(records, paths.stop) == "killed"
    census = planted_census(go_table)
    d = psm.finalise_records(
        paths,
        census,
        go_table,
        identity=IDENTITY,
        manifest_failures=(),
        resamples=200,
    )
    assert d["label"] == "INCOMPLETE"
    assert d["end"] == "killed"
    assert d["partial"] is True
    assert json.loads(paths.decision.read_text())["label"] == "INCOMPLETE"
    assert paths.units.exists()


def test_finalise_is_repeatable_and_refuses_a_live_driver(
    tmp_path: Path, go_table: Table
) -> None:
    paths = psm.OutPaths(tmp_path)
    journal = psm.Journal(paths.journal)
    for r in clean(go_table):
        journal.append(r)
    journal.append({"type": "run_start"})
    journal.append({"type": "run_end", "reason": "complete"})
    census = planted_census(go_table)
    kwargs: dict[str, Any] = {
        "identity": IDENTITY,
        "manifest_failures": (),
        "resamples": 200,
    }
    d = psm.finalise_records(paths, census, go_table, provisional=True, **kwargs)
    assert d["provisional"] is True
    assert json.loads(paths.decision.read_text())["provisional"] is True
    assert not paths.units.exists()
    psm.finalise_records(paths, census, go_table, **kwargs)
    assert json.loads(paths.decision.read_text())["provisional"] is False
    first = (paths.decision.read_bytes(), paths.units.read_bytes())
    psm.finalise_records(paths, census, go_table, **kwargs)
    assert first == (paths.decision.read_bytes(), paths.units.read_bytes())
    with psm.driver_lock(paths), pytest.raises(psm.ResumeRefused):
        psm.driver_lock(paths).__enter__()


# --------------------------------------------------------------------------
# T2 helpers are T2's own (tests/glm/test_fit_search.py)
# --------------------------------------------------------------------------


def t2_module() -> ModuleType:
    path = REPO / "tests" / "glm" / "test_fit_search.py"
    spec = importlib.util.spec_from_file_location("t2_source", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_t2_structures_are_t2s() -> None:
    t2 = t2_module()
    assert psm.T2_BIG == t2.BIG
    assert psm.T2_CHANNELS == t2.CHANNELS


def test_a_t2_log_is_t2s_log() -> None:
    """A seed outside group A: the log only, no search."""
    t2 = t2_module()
    which, link, seed = 3, "exp", 5
    structure = Structure(t2.BIG[which], Link(link))
    rng = np.random.default_rng(seed)
    generating = t2._random_point(structure, rng)
    coef = t2._random_coefficients(structure, rng)
    log = simulate(structure, generating, coef, t2.CHANNELS, t2.marks, 300.0, rng)
    case = psm.t2_log(which, link, seed)
    assert case.structure == structure
    assert case.generating == generating
    assert case.log.times.tobytes() == log.times.tobytes()


# --------------------------------------------------------------------------
# Whole jobs, on a toy truth (slow tier)
# --------------------------------------------------------------------------


class ListSink:
    """Collects a job's records in this process."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, type_: str, payload: dict[str, Any]) -> None:
        self.records.append(
            json.loads(psm.dumps_record({"type": type_, "job": "j", **payload}))
        )

    def types(self) -> list[str]:
        return [r["type"] for r in self.records]

    def only(self, type_: str) -> dict[str, Any]:
        (found,) = [r for r in self.records if r["type"] == type_]
        return found


def toy_job(
    per_event: str | None = None, *, k: int = 1, cap: int = 150, r2_max: int = 0
) -> Any:
    truth = entry("hawkes")
    env = environment()
    structure = canonicalise(Structure(MIDDLE, Link.EXP))
    text = render(structure)
    if per_event is None:
        data = psm.observational_data(truth, 1, env)
        stored = fit(structure, data.observational, env.channels, config=FitConfig())
        held = psm.held_out_dataset(truth.spec, truth.id, 1)
        model = GLMModel("B-sym", stored, env.channels, env.marks)
        per_event = model.held_out_per_event([held]).hex()
    row = psm.RowExpect(system="B-sym", llm=False, per_event=per_event, oracle=None)
    return psm.FitJob(
        truth=truth,
        seed=1,
        text=text,
        rows=(row,),
        env=env,
        root=TEST_ROOT,
        k=k,
        cap=cap,
        r2_max=r2_max,
    )


@pytest.mark.slow
def test_a_whole_job_writes_r0_then_r1_then_done_and_stays_blind() -> None:
    profiles: list[Any] = []
    real = psm.fresh_profile

    def capture(*args: Any) -> Any:
        profiles.append(real(*args))
        return profiles[-1]

    job = toy_job()
    sink = ListSink()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(psm, "fresh_profile", capture)
        psm.run_fit_job(job, sink)
    assert sink.types() == ["r0", "r1", "done"]
    done = sink.only("done")
    assert done["evidence"] == "searched"
    assert done["rows"][0]["call"] in ("none", "near_tie", "miss")
    (profile,) = profiles
    data = psm.observational_data(job.truth, job.seed, job.env)
    second = _Profile(
        parse(job.text, job.env.channels),
        tuple(data.observational),
        job.env.channels,
        FitConfig(),
        None,
    )
    rng = psm.reference_rng(psm.fit_case("hawkes", 1, job.text), TEST_ROOT)
    blocks = _search_blocks(second.slots, second.config.psi_block_max)
    with contextlib.suppress(psm._Budget):
        for start in psm.draw_starts(second.slots, rng, job.k):
            _ascend(second, start, blocks, job.cap)
    assert set(profile.solutions) == set(second.solutions)


@pytest.mark.slow
def test_a_byte_mismatch_ends_the_job_before_r1() -> None:
    job = toy_job()
    value = float.fromhex(job.rows[0].per_event)
    job = replace(
        job,
        rows=(replace(job.rows[0], per_event=math.nextafter(value, 0.0).hex()),),
    )
    sink = ListSink()
    psm.run_fit_job(job, sink)
    assert sink.types() == ["byte_fail"]


@pytest.mark.slow
def test_an_r0_that_fails_is_a_byte_failure_not_reference_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = toy_job((-0.75).hex())

    def fail(*args: Any, **kwargs: Any) -> None:
        raise psm.FitError("no point")

    monkeypatch.setattr(psm, "fit", fail)
    sink = ListSink()
    psm.run_fit_job(job, sink)
    assert sink.types() == ["byte_fail"]


@pytest.mark.slow
def test_a_failing_r1_is_reference_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    job = toy_job()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("numerics")

    monkeypatch.setattr(psm, "reference_search", fail)
    sink = ListSink()
    psm.run_fit_job(job, sink)
    assert sink.types() == ["r0", "reference_failed"]
    assert sink.only("reference_failed")["reason"] == "ValueError"


@pytest.mark.slow
def test_a_refit_that_is_not_the_searched_point_is_a_byte_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = toy_job()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise psm.ByteFailure("refit differs")

    monkeypatch.setattr(psm, "refit_at", fail)
    sink = ListSink()
    psm.run_fit_job(job, sink)
    assert sink.types()[-1] == "byte_fail"


@pytest.mark.slow
def test_a_fit_job_survives_the_trip_to_a_fresh_interpreter(tmp_path: Path) -> None:
    job = toy_job()
    run = driver(tmp_path, [stage(job)], start_method=psm.START_METHOD)
    assert run.run() == "complete"
    journal = psm.OutPaths(tmp_path).journal
    assert types_of(journal, job.id) == ["r0", "r1", "done"]


@pytest.mark.slow
def test_any_other_exception_in_r1_is_a_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    job = toy_job()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise KeyError("a bug")

    monkeypatch.setattr(psm, "reference_search", fail)
    with pytest.raises(KeyError):
        psm.run_fit_job(job, ListSink())


def hawkes_setup() -> tuple[Any, Any, Any, Dataset, str]:
    """A truth with a 6-point grid: whole jobs, at the cost of a few fits."""
    truth = entry("hawkes")
    env = environment()
    data = psm.observational_data(truth, 1, env)
    held = psm.held_out_dataset(truth.spec, truth.id, 1)
    return truth, env, data, held, render(truth.structure)


def hawkes_job(rows: tuple[Any, ...], group: str | None = None) -> Any:
    truth, env, *_, text = hawkes_setup()
    return psm.FitJob(
        truth=truth,
        seed=1,
        text=text,
        rows=rows,
        env=env,
        root=TEST_ROOT,
        k=2,
        cap=10**4,
        r2_max=10**4,
        group=group,
    )


def score(fitted: Any, env: Any, held: Dataset) -> str:
    return (
        GLMModel("x", fitted, env.channels, env.marks).held_out_per_event([held]).hex()
    )


@pytest.mark.slow
def test_an_oracle_row_and_its_sibling_each_read_their_own_stored_fit() -> None:
    truth, env, data, held, _ = hawkes_setup()
    ran = psm.Oracle(
        truth.structure,
        fit_config=FitConfig(),
        truth_psi=truth.spec.psi,
        truth_coefficients=truth.spec.coef,
    ).run(data)
    details = ran.details
    oracle = psm.OracleExpect(
        psm._hex_or_none(details.get("oracle_search_ll")),
        psm._hex_or_none(details.get("oracle_truth_psi_ll")),
        details.get("oracle_psi_source"),
        details.get("oracle_underfit"),
    )
    plain = fit(truth.structure, data.observational, env.channels, config=FitConfig())
    rows = (
        psm.RowExpect("ORACLE", False, score(ran.model.fit, env, held), oracle),
        psm.RowExpect("B-sym", False, score(plain, env, held), None),
    )
    sink = ListSink()
    psm.run_fit_job(hawkes_job(rows, "B"), sink)
    assert sink.types() == ["r0", "r1", "done"]
    done = sink.only("done")
    assert done["evidence"] == "exact"
    oracle_row, sibling = done["rows"]
    assert oracle_row["oracle"]["search_call"] in ("none", "near_tie", "miss")
    assert sibling["oracle"] is None
    assert done["control"]["group"] == "B"
    assert psm.valued_of(done["control"]["fit5"]).ll == plain.log_likelihood


@pytest.mark.slow
def test_a_real_miss_is_refitted_scored_and_sized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored fit is planted at the grid's worst point, so the search
    really beats it; everything after R0 is the real path."""
    truth, env, data, held, _ = hawkes_setup()
    obs, channels = data.observational, env.channels
    slots = _Slots.of(truth.structure)
    fits = [
        fit(
            truth.structure,
            obs,
            channels,
            config=FitConfig(),
            psi=slots.assignment((i,)),
        )
        for i in range(slots.size)
    ]
    poor = min(fits, key=lambda f: f.log_likelihood)
    best = max(fits, key=lambda f: f.log_likelihood)
    monkeypatch.setattr(psm, "stored_fits", lambda *a: psm.StoredFits(poor, poor, None))
    stored = score(poor, env, held)
    sink = ListSink()
    row = psm.RowExpect("B-sym", False, stored, None)
    psm.run_fit_job(hawkes_job((row,)), sink)
    assert sink.only("done")["starts_completed"] == 2
    assert sink.only("r1")["result"]["starts_completed"] == 2
    (result,) = sink.only("done")["rows"]
    assert result["call"] == "miss"
    assert float.fromhex(result["chosen_ll"]) == best.log_likelihood
    assert float.fromhex(result["gain"]) > 0.0
    corrected = float.fromhex(result["corrected_per_event"])
    assert corrected == float.fromhex(score(best, env, held))
    assert float.fromhex(result["delta"]) == corrected - float.fromhex(stored)
    assert result["delta_kind"] == "finite"
    assert float.fromhex(result["corrected_similarity"]) >= 0.0
    assert float.fromhex(result["stored_quadrature_error"]) == poor.quadrature_error
    assert float.fromhex(result["chosen_quadrature_error"]) == best.quadrature_error
    assert result["within_quadrature"] is False


@pytest.mark.slow
def test_oracles_kept_fit_does_not_reach_a_sibling_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 1, finding 2. ORACLE keeps the best point and the sibling's stored
    fit is the worst: the sibling carries a miss, ORACLE does not."""
    truth, env, data, held, _ = hawkes_setup()
    slots = _Slots.of(truth.structure)
    fits = [
        fit(
            truth.structure,
            data.observational,
            env.channels,
            config=FitConfig(),
            psi=slots.assignment((i,)),
        )
        for i in range(slots.size)
    ]
    poor = min(fits, key=lambda f: f.log_likelihood)
    kept = max(fits, key=lambda f: f.log_likelihood)
    monkeypatch.setattr(psm, "stored_fits", lambda *a: psm.StoredFits(poor, kept, None))
    none = psm.OracleExpect(None, None, None, None)
    rows = (
        psm.RowExpect("ORACLE", False, score(kept, env, held), none),
        psm.RowExpect("B-sym", False, score(poor, env, held), None),
    )
    sink = ListSink()
    psm.run_fit_job(hawkes_job(rows), sink)
    oracle_row, sibling = sink.only("done")["rows"]
    assert oracle_row["call"] == "none"
    assert oracle_row["oracle"]["search_call"] == "miss"
    assert sibling["call"] == "miss"


def planted_poor(monkeypatch: pytest.MonkeyPatch) -> Any:
    """R0 planted at the grid's worst point; the row a job then checks."""
    truth, env, data, held, _ = hawkes_setup()
    slots = _Slots.of(truth.structure)
    fits = [
        fit(
            truth.structure,
            data.observational,
            env.channels,
            config=FitConfig(),
            psi=slots.assignment((i,)),
        )
        for i in range(slots.size)
    ]
    poor = min(fits, key=lambda f: f.log_likelihood)
    monkeypatch.setattr(psm, "stored_fits", lambda *a: psm.StoredFits(poor, poor, None))
    return psm.RowExpect("B-sym", False, score(poor, env, held), None)


@pytest.mark.slow
def test_a_job_dying_in_r2_leaves_r1s_miss_on_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = planted_poor(monkeypatch)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("in R2")

    monkeypatch.setattr(psm, "run_r2", fail)
    sink = ListSink()
    psm.run_fit_job(hawkes_job((row,)), sink)
    assert sink.types() == ["r0", "r1", "r2_lost"]
    result = sink.only("r1")["result"]
    assert result["evidence"] == "searched"
    assert result["r2_complete"] is False
    assert result["rows"][0]["call"] == "miss"
    assert sink.only("r2_lost")["reason"] == "ValueError"


@pytest.mark.slow
def test_a_job_past_the_cut_is_flagged_and_searched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = planted_poor(monkeypatch)
    answers = iter([False])
    monkeypatch.setattr(psm, "past_job_cut", lambda: next(answers, True))
    sink = ListSink()
    psm.run_fit_job(hawkes_job((row,)), sink)
    assert sink.types() == ["r0", "r1", "done"]
    done = sink.only("done")
    assert done["cpu_cut"] is True
    assert done["evidence"] == "searched"
    assert done["r2_complete"] is False


@pytest.mark.slow
def test_an_s2_job_reports_a_stored_link_it_cannot_reproduce() -> None:
    truth, env, *_, text = hawkes_setup()
    sink = ListSink()
    psm.S2Job(truth, 1, text, "no-such-link", env, TEST_ROOT).run(sink)
    done = sink.only("done")
    assert done["status"] == "link_mismatch"
    assert done["corrected_per_event"] is None


@pytest.mark.slow
def test_an_s2_job_with_the_same_choice_changes_nothing() -> None:
    truth, env, *_, text = hawkes_setup()
    first = ListSink()
    psm.S2Job(truth, 1, text, "no-such-link", env, TEST_ROOT).run(first)
    link = first.only("done")["r0_link"]
    sink = ListSink()
    psm.S2Job(truth, 1, text, link, env, TEST_ROOT).run(sink)
    done = sink.only("done")
    assert done["status"] == "same"
    assert done["reference_link"] == link


@pytest.mark.slow
def test_an_s2_job_whose_reference_differs_fits_and_scores_that_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth, env, *_, text = hawkes_setup()
    real = psm.link_choice
    calls: list[Any] = []

    def second_differs(*args: Any, **kwargs: Any) -> Any:
        link, scores = real(*args, **kwargs)
        calls.append(link)
        if len(calls) == 2:
            other = next(x for x in SparseConfig().links if x is not link)
            return other, scores
        return link, scores

    monkeypatch.setattr(psm, "link_choice", second_differs)
    first = ListSink()
    psm.S2Job(truth, 1, text, "no-such-link", env, TEST_ROOT).run(first)
    calls.clear()
    sink = ListSink()
    link = first.only("done")["r0_link"]
    psm.S2Job(truth, 1, text, link, env, TEST_ROOT).run(sink)
    done = sink.only("done")
    assert done["status"] == "differs"
    assert done["reference_link"] != link
    assert math.isfinite(float.fromhex(done["corrected_per_event"]))


# --------------------------------------------------------------------------
# T2 controls 1 and 2 (section 2.10): slow tier, never the frozen root
# --------------------------------------------------------------------------


def t2_job(case: tuple[int, str, int], group: str, with_r2: bool) -> Any:
    return psm.T2Job(
        which=case[0],
        link=case[1],
        seed=case[2],
        group=group,
        with_r2=with_r2,
        root=TEST_ROOT,
        k=0 if group == "D" else psm.K_STARTS,
        cap=psm.r1_cap(case[1]),
    )


def test_known_values_are_computed_after_the_result_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order of section 2.2, on a toy case standing in for the T2 log."""
    data = toy_data()
    case = SimpleNamespace(structure=structure_of(), generating=None, log=data[0].log)
    monkeypatch.setattr(psm, "t2_log", lambda *a: case)
    sink = ListSink()
    seen: list[list[str]] = []

    def known(*args: Any) -> Any:
        seen.append(sink.types())
        return valued(0.0)

    monkeypatch.setattr(psm, "known_value", known)
    job = psm.T2Job(0, "softplus", 1, "A", False, TEST_ROOT, 1, 10**6)
    psm.run_t2_job(job, sink)
    assert seen == [["r0", "r1"]]
    assert sink.types() == ["r0", "r1", "done"]


def test_a_control_records_its_completed_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SimpleNamespace(
        structure=structure_of(), generating=None, log=toy_data()[0].log
    )
    monkeypatch.setattr(psm, "t2_log", lambda *a: case)
    monkeypatch.setattr(psm, "known_value", lambda *a: valued(0.0))
    sink = ListSink()
    psm.run_t2_job(psm.T2Job(0, "softplus", 1, "A", False, TEST_ROOT, 2, 10**6), sink)
    assert sink.only("done")["starts_completed"] == 2
    assert sink.only("r1")["starts_completed"] == 2


def test_group_d_is_r2_alone_on_an_empty_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = toy_data()
    case = SimpleNamespace(structure=structure_of(), generating=None, log=data[0].log)
    monkeypatch.setattr(psm, "t2_log", lambda *a: case)
    monkeypatch.setattr(psm, "known_value", lambda *a: valued(0.0))
    captured: list[Any] = []
    real = psm.fresh_profile

    def capture(*args: Any) -> Any:
        captured.append(real(*args))
        return captured[-1]

    monkeypatch.setattr(psm, "fresh_profile", capture)
    sink = ListSink()
    psm.run_t2_job(psm.T2Job(0, "softplus", 1, "D", True, TEST_ROOT, 0, 10**6), sink)
    assert sink.types() == ["r0", "r2", "done"]
    assert len(captured[0].solutions) == 30
    assert sink.only("done")["r2_complete"] is True


@pytest.mark.slow
def test_t2_control_1_with_r2_reaches_the_known_value_by_enumeration() -> None:
    sink = ListSink()
    psm.run_t2_job(t2_job(psm.T2_CASES[0], "D", True), sink)
    assert sink.types() == ["r0", "r2", "done"]
    records = sink.records
    done = sink.only("done")
    known = psm.valued_of(done["known"])
    fit5 = psm.valued_of(done["fit5"])
    assert known.ll > fit5.ll
    assert psm.control_caught(psm.valued_of(done["r2_best"]), fit5, known), records


@pytest.mark.slow
def test_t2_control_2_is_caught_by_r1_alone() -> None:
    sink = ListSink()
    psm.run_t2_job(t2_job(psm.T2_CASES[1], "A", False), sink)
    done = sink.only("done")
    best = psm.valued_of(sink.only("r1")["best"])
    assert psm.control_caught(
        best, psm.valued_of(done["fit5"]), psm.valued_of(done["known"])
    )
