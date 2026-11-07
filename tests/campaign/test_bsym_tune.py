"""``scripts/bsym_tune.py``: tuning B-sym on dev (SPEC §6.3 instrument).

The rule is ``docs/v2/prereg/2026-11-06_bsym-tuning.md``, whose section 11 lists
what these tests must cover. Section numbers below mean that file.

**Streams.** Root 20261113 and seeds 4, 5 and 6 are the launch's and run once, so
no test draws them: the script takes its root and seeds in a :class:`RunSpec`, every
test passes :data:`TEST_ROOT` and seeds 1 to 3, and :func:`no_undrawn_stream` fails
any test that reaches numpy with one of them, in a forked job too. That is a
tripwire of this file's own: no guard is removed or bypassed for a test, and the
tripwire's own test uses a stand-in id (INBOX 119eff3).

**Toys.** Most tests run on hand-built readings. :class:`StubJob` stands in for a
unit so the driver's tests take a fraction of a second; the slow tier runs real
units, on pointproc's named truths at seed 1 and a tiny budget.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import math
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest
from campaign_support import entry, environment

from sciagent.campaign.execute import BaselineJob, UnitResult, run_baseline_job
from sciagent.campaign.p4plan import P4_TRUTHS, p4_plan
from sciagent.campaign.plan import (
    B_SYM,
    CampaignConfig,
    TruthEntry,
    Unit,
    baseline_key,
    search_seed,
)
from sciagent.campaign.store import ResultStore
from sciagent.glm.grammar import Link, Structure
from sciagent.systems.v2.search import (
    DEFAULT_MUTATION_WEIGHTS,
    GPConfig,
    Mutation,
)

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
bt: Any = importlib.import_module("bsym_tune")

#: Every stream in this file is drawn under this root. It is not the launch's.
TEST_ROOT: Final = 20261107
#: The launch's root and the P4 check's frozen roots: never handed to numpy here.
FROZEN_STREAMS: Final = frozenset({20261110, 20261111, 20261112, 20261113})
#: Stage 1's seeds: undrawn until the launch.
UNDRAWN_SEEDS: Final = frozenset({4, 5, 6})
#: A stand-in the tripwire's own test watches (INBOX 119eff3).
WATCHED: Final[set[int]] = set()

#: ``scripts/pilot_search_misses.py`` at 4c5a21c: the file is frozen.
FROZEN_MISS_SCRIPT_SHA256: Final = (
    "94003dea9db544f8ff88d360b2906468db4dba77b06afa99531c34ddecc4713b"
)

TRUTHS: Final = ("t1", "t2", "t3", "t4")
SEEDS: Final = (1, 2, 3)
FITS: Final = 8
LOW: Final = 6

type Key = tuple[str, int, str]


def is_undrawn(value: Any) -> bool:
    """Whether a stream's entropy names a frozen root or an undrawn world seed."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value in FROZEN_STREAMS or value in UNDRAWN_SEEDS or value in WATCHED
    if isinstance(value, list | tuple) and value:
        return is_undrawn(value[0]) or any(
            isinstance(v, int) and (v in FROZEN_STREAMS or v in WATCHED) for v in value
        )
    return False


@pytest.fixture(autouse=True)
def no_undrawn_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A draw on an undrawn stream fails the test (module docstring)."""
    real_sequence = np.random.SeedSequence
    real_rng = np.random.default_rng

    def seed_sequence(entropy: Any = None, **kw: Any) -> Any:
        assert not is_undrawn(entropy), f"undrawn stream reached numpy: {entropy}"
        return real_sequence(entropy, **kw)

    def default_rng(seed: Any = None) -> np.random.Generator:
        assert not is_undrawn(seed), f"undrawn stream reached numpy: {seed}"
        return real_rng(seed)

    monkeypatch.setattr(np.random, "SeedSequence", seed_sequence)
    monkeypatch.setattr(np.random, "default_rng", default_rng)


def test_the_tripwire_holds_the_frozen_streams_by_membership_alone() -> None:
    """Set membership, with nothing handed to numpy."""
    assert all(is_undrawn(root) for root in sorted(FROZEN_STREAMS))
    assert all(is_undrawn([root, 0]) for root in sorted(FROZEN_STREAMS))
    assert all(is_undrawn([seed, 0, 0]) for seed in sorted(UNDRAWN_SEEDS))
    assert not is_undrawn([TEST_ROOT, 0])
    assert not is_undrawn([1, 0, 0])


def test_the_tripwire_fires_on_a_watched_id_and_on_nothing_else() -> None:
    """A non-control id stands in for the frozen streams."""
    stand_in = 11
    WATCHED.add(stand_in)
    try:
        with pytest.raises(AssertionError, match="undrawn stream"):
            bt.graded_rng(stand_in)
    finally:
        WATCHED.discard(stand_in)
    assert int(bt.graded_rng(TEST_ROOT).integers(1 << 30)) >= 0


def test_the_test_root_is_not_the_launchs_root() -> None:
    assert bt.SEED_ROOT == 20261113
    assert TEST_ROOT != bt.SEED_ROOT
    assert not set(SEEDS) & set(bt.STAGE1_SEEDS)


# --------------------------------------------------------------------------
# The settings are constants (section 2)
# --------------------------------------------------------------------------


def weights(**changed: float) -> tuple[tuple[Mutation, float], ...]:
    return tuple(
        (op, changed.get(op.name, weight)) for op, weight in DEFAULT_MUTATION_WEIGHTS
    )


#: The text's table, typed out here and not derived from the script.
EXPECTED_GP: Final[dict[str, GPConfig]] = {
    "c0": GPConfig(),
    "c1": GPConfig(population=4, offspring=4),
    "c2": GPConfig(population=16),
    "c3": GPConfig(tournament=2),
    "c4": GPConfig(tournament=5),
    "c5": GPConfig(crossover_prob=0.0),
    "c6": GPConfig(crossover_prob=0.5),
    "c7": GPConfig(mutation_weights=weights(CHANGE_LINK=3.0)),
    "c8": GPConfig(mutation_weights=weights(CHANGE_LINK=6.0)),
    "c9": GPConfig(offspring=2),
    "c10": GPConfig(
        mutation_weights=weights(ADD_FEATURE=4.0, MULTIPLY=2.0, WRAP_GATE=2.0)
    ),
    "c11": GPConfig(),
    "c0b": GPConfig(),
}


def test_the_settings_are_the_texts_list_in_the_texts_order() -> None:
    names = [s.name for s in bt.SETTINGS]
    assert names == [f"c{i}" for i in range(12)] + ["weak", "c0b"]
    assert tuple(f"c{i}" for i in range(12)) == bt.SELECTABLE


@pytest.mark.parametrize("name", sorted(EXPECTED_GP))
def test_each_settings_gp_is_the_texts(name: str) -> None:
    assert bt.setting(name).gp == EXPECTED_GP[name]


def test_c0_is_the_default_and_the_guards_never_change() -> None:
    assert bt.setting("c0").gp == GPConfig()
    assert GPConfig().population == 8 and GPConfig().offspring == 8
    assert GPConfig().tournament == 3 and GPConfig().crossover_prob == 0.2
    for s in bt.SETTINGS:
        assert s.gp.max_attempts == GPConfig().max_attempts
        assert s.gp.max_features == GPConfig().max_features


def test_weak_has_every_weight_0_but_remove_feature() -> None:
    gp = bt.setting("weak").gp
    assert dict(gp.mutation_weights) == {
        op: 1.0 if op is Mutation.REMOVE_FEATURE else 0.0
        for op, _ in DEFAULT_MUTATION_WEIGHTS
    }
    assert gp.crossover_prob == GPConfig().crossover_prob


def test_only_c11_adds_a_seed_and_it_is_a_softplus_null() -> None:
    assert bt.setting("c11").extra_seeds == (Structure((), Link.SOFTPLUS),)
    assert all(s.extra_seeds == () for s in bt.SETTINGS if s.name != "c11")


def test_only_c0b_shifts_the_search_seed() -> None:
    assert bt.setting("c0b").shifted
    assert not any(s.shifted for s in bt.SETTINGS if s.name != "c0b")


def test_the_shifted_seed_is_the_rule_named_in_the_constant() -> None:
    for truth, seed in (("t1", 1), ("t2", 3)):
        base = search_seed(truth, seed)
        assert bt.shifted_seed(truth, seed) == (base + bt.C0B_SEED_SHIFT) % 2**63
        assert bt.shifted_seed(truth, seed) != base


def test_weak_and_c0b_are_not_selectable() -> None:
    assert "weak" not in bt.SELECTABLE and "c0b" not in bt.SELECTABLE


# --------------------------------------------------------------------------
# Units: the framework's addresses, the order (sections 2, 8)
# --------------------------------------------------------------------------


def toy_entries(ids: Sequence[str] = ("hawkes", "size_excitation")) -> list[TruthEntry]:
    return [entry(i) for i in ids]


def toy_spec(
    truths: Sequence[str] = TRUTHS,
    seeds: Sequence[int] = SEEDS,
    *,
    min_complete: int | None = None,
    resamples: int = 400,
    fits: int = FITS,
    low: int = LOW,
) -> Any:
    worlds = len(truths) * len(seeds)
    return bt.RunSpec(
        truths=tuple(truths),
        seeds=tuple(seeds),
        fits=fits,
        graded_low=low,
        min_complete=worlds if min_complete is None else min_complete,
        root=TEST_ROOT,
        resamples=resamples,
        mode="rehearsal",
    )


def toy_plan(seeds: Sequence[int] = (1, 2)) -> tuple[Any, Any, tuple[Any, ...]]:
    entries = toy_entries()
    spec = toy_spec([e.id for e in entries], seeds)
    return spec, entries, bt.plan_stage1(spec, entries, environment())


def test_worlds_run_in_sha256_order_of_truth_bar_seed() -> None:
    worlds = bt.world_order(("a", "b", "c"), (1, 2))
    digest = {
        (t, s): hashlib.sha256(f"{t}|{s}".encode()).hexdigest()
        for t in "abc"
        for s in (1, 2)
    }
    assert worlds == tuple(sorted(digest, key=lambda k: digest[k]))


def test_world_order_does_not_depend_on_the_order_given() -> None:
    assert bt.world_order(("a", "b"), (1, 2)) == bt.world_order(("b", "a"), (2, 1))


def test_all_14_settings_run_on_a_world_before_the_next() -> None:
    spec, _, plan = toy_plan()
    names = [s.name for s in bt.SETTINGS]
    for i, world in enumerate(bt.world_order(spec.truths, spec.seeds)):
        block = plan[i * 14 : (i + 1) * 14]
        assert {(u.truth, u.seed) for u in block} == {world}
        assert [u.setting for u in block] == names


def test_every_unit_has_its_own_address() -> None:
    _, _, plan = toy_plan()
    assert len({u.job.id for u in plan}) == len(plan) == 14 * 4


def test_c0s_address_is_the_frameworks_for_the_default() -> None:
    spec, entries, plan = toy_plan()
    config = CampaignConfig(
        seeds=spec.seeds, control_seeds=spec.seeds[:1], fits=spec.fits, llm_arms=()
    )
    unit = next(u for u in plan if u.setting == "c0")
    truth = next(e for e in entries if e.id == unit.truth)
    want = baseline_key(Unit(truth.id, unit.seed, B_SYM), truth, environment(), config)
    assert unit.key == want


def test_c0b_is_c0_with_one_more_address_field() -> None:
    _, _, plan = toy_plan()
    c0 = next(u for u in plan if u.setting == "c0")
    c0b = next(
        u
        for u in plan
        if u.setting == "c0b" and (u.truth, u.seed) == (c0.truth, c0.seed)
    )
    extra = dict(c0b.key.config.items())
    assert extra.pop("search_seed_rule") == bt.C0B_SEED_RULE
    assert extra == dict(c0.key.config.items())


def test_c11s_seed_reaches_its_job_and_its_address_only() -> None:
    _, _, plan = toy_plan()
    c0 = next(u for u in plan if u.setting == "c0")
    c11 = next(
        u
        for u in plan
        if u.setting == "c11" and (u.truth, u.seed) == (c0.truth, c0.seed)
    )
    env = environment()
    assert c0.job.env.sym_seeds == env.sym_seeds
    assert c11.job.env.sym_seeds == (*env.sym_seeds, Structure((), Link.SOFTPLUS))
    assert c0.key != c11.key
    assert "softplus" in c11.text and "softplus" not in c0.text


def test_each_job_carries_its_settings_gp_at_the_specs_budget() -> None:
    _, _, plan = toy_plan()
    for u in plan:
        assert u.job.config.gp == bt.setting(u.setting).gp
        assert u.job.config.fits == FITS
        assert u.job.config.baselines == (B_SYM,)
        assert u.job.config.llm_arms == ()


def test_a_job_holds_no_investigation_spec_it_would_have_to_pickle() -> None:
    _, _, plan = toy_plan()
    assert all(u.job.env.investigation is None for u in plan)


# --------------------------------------------------------------------------
# Readings (section 3)
# --------------------------------------------------------------------------


def cell(
    reading: Mapping[str, float] | None = None,
    **result: Any,
) -> dict[str, Any]:
    body = dict(reading) if reading is not None else {"status": 0.0}
    return {
        "reading": {k: float(v).hex() for k, v in body.items()},
        "detail": {"result": result},
    }


OK: Final = {"status": 1.0, "has_structure": 1.0}


def test_a_finite_cell_is_read_with_its_curve() -> None:
    got = bt.read_cell("c0", "t1", 1, cell(OK | {"per_event": -0.5}, curve=CURVE))
    assert (got.why, got.per_event) == ("finite", -0.5)
    assert got.curve == ((1, -0.75), (6, -0.625), (8, -0.5))


CURVE: Final = [[1, "a", -0.75], [6, "b", -0.625], [8, "c", -0.5]]


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (cell(OK | {"per_event": -math.inf}), "minus_inf"),
        (cell(OK | {"per_event": -0.5, "has_structure": 0.0}), "no_submission"),
        (
            cell(
                error="SystemRunError",
                message="B-sym: no proposal produced a certified fit",
            ),
            "no_certified_fit",
        ),
        (cell(error="LinAlgError", message="singular"), "other_failure"),
        (cell(OK | {"per_event": math.nan}), "other_failure"),
    ],
)
def test_the_floor_kinds_are_told_apart(body: dict[str, Any], why: str) -> None:
    assert bt.read_cell("c0", "t1", 1, body).why == why


def reading(
    setting: str,
    truth: str,
    seed: int,
    value: float,
    why: str = "finite",
    curve: tuple[tuple[int, float], ...] | None = None,
) -> Any:
    if curve is None:
        curve = ((LOW, value - 0.01), (FITS, value))
    return bt.Reading(setting, truth, seed, why, value, curve)


def world_rows(
    truth: str,
    seed: int,
    values: Mapping[str, float] | None = None,
    *,
    gain: float = 0.01,
) -> list[Any]:
    """One world: every setting at -1, ``weak`` at -1.5, with ``values`` over that."""
    table = {s.name: -1.0 for s in bt.SETTINGS} | {"weak": -1.5} | dict(values or {})
    return [
        reading(
            name,
            truth,
            seed,
            value,
            curve=((LOW, value - gain), (FITS, value)),
        )
        for name, value in table.items()
    ]


def rows_for(
    truths: Sequence[str] = TRUTHS,
    seeds: Sequence[int] = SEEDS,
    values: Mapping[str, float] | None = None,
    *,
    gain: float = 0.01,
) -> list[Any]:
    return [
        r for t in truths for s in seeds for r in world_rows(t, s, values, gain=gain)
    ]


def swap(rows: Iterable[Any], new: Any) -> list[Any]:
    """``rows`` with ``new`` in the place of the row of its setting and world."""
    key = (new.setting, new.truth, new.seed)
    return [new if (r.setting, r.truth, r.seed) == key else r for r in rows]


def table_of(
    rows: Sequence[Any],
    truths: Sequence[str] = TRUTHS,
    seeds: Sequence[int] = SEEDS,
) -> Any:
    return bt.build_table(rows, tuple(truths), tuple(seeds))


@pytest.mark.parametrize(
    "why",
    ["minus_inf", "no_submission", "no_certified_fit", "cpu_limit", "other_failure"],
)
def test_a_floored_row_takes_the_lowest_finite_reading_on_its_world(why: str) -> None:
    rows = rows_for(("t1",), (1,), {"c3": -0.25, "c5": -2.0})
    rows = swap(rows, reading("c2", "t1", 1, -math.inf, why, ()))
    table = table_of(rows, ("t1",), (1,))
    assert table.values[("t1", 1, "c2")] == -2.0
    assert table.values[("t1", 1, "c3")] == -0.25
    assert table.floors["c2"][why] == 1


def test_the_floor_is_read_among_all_14_settings_weak_and_c0b_too() -> None:
    rows = rows_for(("t1",), (1,), {"weak": -3.0, "c0b": -4.0})
    rows = swap(rows, reading("c2", "t1", 1, -math.inf, "minus_inf", ()))
    assert table_of(rows, ("t1",), (1,)).values[("t1", 1, "c2")] == -4.0


def test_a_floor_is_per_world_and_never_crosses_to_another() -> None:
    rows = rows_for(("t1", "t2"), (1,), {"c5": -2.0})
    rows = swap(rows, reading("c5", "t2", 1, -9.0))
    rows = swap(rows, reading("c2", "t1", 1, -math.inf, "minus_inf", ()))
    assert table_of(rows, ("t1", "t2"), (1,)).values[("t1", 1, "c2")] == -2.0


def test_a_world_is_complete_with_a_row_or_a_floor_for_all_14() -> None:
    rows = rows_for(("t1",), (1,))
    rows = swap(rows, reading("c2", "t1", 1, -math.inf, "cpu_limit", ()))
    assert table_of(rows, ("t1",), (1,)).complete == (("t1", 1),)


def test_a_world_missing_one_setting_is_unfinished() -> None:
    rows = [r for r in rows_for(("t1",), (1,)) if r.setting != "c4"]
    table = table_of(rows, ("t1",), (1,))
    assert table.complete == ()
    assert table.unfinished == (("t1", 1),)


def test_a_failure_common_to_all_14_drops_the_world() -> None:
    rows = [
        reading(s.name, "t1", 1, math.nan, "other_failure", ()) for s in bt.SETTINGS
    ] + rows_for(("t2",), (1,))
    table = table_of(rows, ("t1", "t2"), (1,))
    assert table.dropped == (("t1", 1),)
    assert table.complete == (("t2", 1),)
    assert table.unfinished == ()


def test_a_failure_of_another_kind_takes_the_floor_by_the_named_constant() -> None:
    assert bt.OTHER_FAILURE_TAKES_FLOOR is True
    rows = rows_for(("t1",), (1,))
    rows = swap(rows, reading("c2", "t1", 1, math.nan, "other_failure", ()))
    assert table_of(rows, ("t1",), (1,)).values[("t1", 1, "c2")] == -1.5


def test_the_other_reading_of_that_constant_leaves_the_world_unfinished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the freeze goes the other way, no nan reaches a mean."""
    monkeypatch.setattr(bt, "OTHER_FAILURE_TAKES_FLOOR", False)
    rows = rows_for(("t1",), (1,))
    rows = swap(rows, reading("c2", "t1", 1, math.nan, "other_failure", ()))
    table = table_of(rows, ("t1",), (1,))
    assert table.complete == () and table.unfinished == (("t1", 1),)


def test_a_truths_value_is_the_mean_over_its_seeds_then_over_truths() -> None:
    """t1 has seeds at -1 and -2, t2 one seed at -4: (-1.5 + -4) / 2, not -7 / 3."""
    rows = [
        *world_rows("t1", 1, {"c3": -1.0}),
        *world_rows("t1", 2, {"c3": -2.0}),
        *world_rows("t2", 1, {"c3": -4.0}),
    ]
    rows = [r for r in rows if not (r.truth == "t2" and r.seed == 2)]
    table = table_of(rows, ("t1", "t2"), (1, 2))
    assert table.complete == (("t1", 1), ("t1", 2), ("t2", 1))
    assert bt.setting_means(table)["c3"] == -2.75


def test_only_complete_worlds_enter_a_mean() -> None:
    rows = rows_for(("t1", "t2"), (1,), {"c3": -1.0})
    rows = swap(rows, reading("c3", "t2", 1, 100.0))
    rows = [r for r in rows if not (r.truth == "t2" and r.setting == "c9")]
    assert bt.setting_means(table_of(rows, ("t1", "t2"), (1,)))["c3"] == -1.0


# --------------------------------------------------------------------------
# c0 on its own curve, and the graded control (section 3)
# --------------------------------------------------------------------------


def test_curve_at_is_the_last_point_within_the_budget() -> None:
    curve = ((1, -0.75), (6, -0.625), (8, -0.5))
    assert bt.curve_at(curve, 8) == -0.5
    assert bt.curve_at(curve, 7) == -0.625
    assert bt.curve_at(curve, 6) == -0.625
    assert bt.curve_at(curve, 5) == -0.75
    assert bt.curve_at(curve, 0) is None
    assert bt.curve_at((), 8) is None


def graded_rows(gains: Mapping[str, float]) -> list[Any]:
    return [r for t in gains for r in world_rows(t, 1, gain=gains[t])]


def test_c0_at_low_reads_the_curve_and_floors_what_is_not_finite() -> None:
    rows = rows_for(("t1",), (1,), {"c5": -2.0})
    bad = ((1, -math.inf), (LOW, -math.inf), (FITS, -0.75))
    rows = swap(rows, reading("c0", "t1", 1, -0.75, curve=bad))
    table = table_of(rows, ("t1",), (1,))
    assert bt.c0_at(table, "t1", 1, LOW) == -2.0
    assert bt.c0_at(table, "t1", 1, FITS) == -0.75


def test_c0_with_no_curve_reads_the_floor_at_both_budgets() -> None:
    """A killed c0 has no curve: its world's difference is 0, by the floor."""
    rows = rows_for(("t1",), (1,), {"c5": -2.0})
    rows = swap(rows, reading("c0", "t1", 1, -math.inf, "cpu_limit", ()))
    table = table_of(rows, ("t1",), (1,))
    assert bt.c0_at(table, "t1", 1, LOW) == -2.0
    assert bt.c0_at(table, "t1", 1, FITS) == -2.0


def test_c0_before_its_first_point_reads_the_floor() -> None:
    rows = rows_for(("t1",), (1,), {"c5": -2.0})
    rows = swap(rows, reading("c0", "t1", 1, -0.75, curve=((FITS, -0.75),)))
    assert bt.c0_at(table_of(rows, ("t1",), (1,)), "t1", 1, LOW) == -2.0


@dataclass(frozen=True)
class FixedDraws:
    """Stands in for the generator: the resampled indices, by hand."""

    index: Sequence[Sequence[int]]

    def integers(self, low: int, high: int, size: tuple[int, int]) -> Any:
        assert (low, high) == (0, size[1]) and size[0] == len(self.index)
        return np.asarray(self.index)


def test_the_graded_control_on_a_hand_computed_toy() -> None:
    """Differences 0.25, 0.5, 1 by truth (mean 7/12). Five resamples of three:

    (0,0,0) 0.25 | (0,0,1) 1/3 | (0,1,2) 7/12 | (1,2,2) 5/6 | (2,2,2) 1.

    Sorted, at numpy's linear quantile with 5 points: position 0.025 * 4 = 0.1 gives
    0.25 + 0.1 * (1/3 - 1/4), and position 0.975 * 4 = 3.9 gives 5/6 + 0.9 * (1 - 5/6).
    """
    table = table_of(
        graded_rows({"t1": 0.25, "t2": 0.5, "t3": 1.0}), ("t1", "t2", "t3"), (1,)
    )
    draws = FixedDraws([[0, 0, 0], [0, 0, 1], [0, 1, 2], [1, 2, 2], [2, 2, 2]])
    got = bt.graded_control(table, FITS, LOW, draws, resamples=5)
    assert got.n == 3
    assert got.mean == pytest.approx(7 / 12, abs=1e-12)
    assert got.low == pytest.approx(0.25 + 0.1 * (1 / 3 - 1 / 4), abs=1e-12)
    assert got.high == pytest.approx(5 / 6 + 0.9 * (1 - 5 / 6), abs=1e-12)


def test_the_graded_control_pairs_by_truth_over_its_seeds() -> None:
    """t1's seeds gain 0.25 and 0.75 (mean 0.5), t2's 1.0: by truth, not by world."""
    rows = [
        *world_rows("t1", 1, gain=0.25),
        *world_rows("t1", 2, gain=0.75),
        *world_rows("t2", 1, gain=1.0),
    ]
    table = table_of(rows, ("t1", "t2"), (1, 2))
    draws = FixedDraws([[0, 1]])
    got = bt.graded_control(table, FITS, LOW, draws, resamples=1)
    assert got.n == 2
    assert got.mean == pytest.approx(0.75, abs=1e-12)


def test_the_graded_stream_is_the_texts_on_the_root_it_is_given() -> None:
    want = np.random.default_rng(np.random.SeedSequence([TEST_ROOT, 0])).integers(
        1 << 62, size=4
    )
    assert bt.graded_rng(TEST_ROOT).integers(1 << 62, size=4).tolist() == want.tolist()


def test_the_control_uses_10000_resamples_at_95_percent() -> None:
    assert bt.RESAMPLES == 10_000 and bt.LEVEL == 0.95


# --------------------------------------------------------------------------
# Decision rules (section 5)
# --------------------------------------------------------------------------


def decide(
    rows: Sequence[Any],
    truths: Sequence[str] = TRUTHS,
    seeds: Sequence[int] = SEEDS,
    *,
    min_complete: int | None = None,
) -> Any:
    spec = toy_spec(truths, seeds, min_complete=min_complete)
    return bt.decide(bt.build_table(rows, spec.truths, spec.seeds), spec)


def noisy_gains(rows: Sequence[Any], gains: Mapping[str, float]) -> list[Any]:
    """``rows`` with c0's curve gain set by truth, for an interval off zero."""
    out = []
    for r in rows:
        if r.setting == "c0":
            gain = gains[r.truth]
            out.append(
                replace(r, curve=((LOW, r.per_event - gain), (FITS, r.per_event)))
            )
        else:
            out.append(r)
    return out


def test_selected_takes_the_highest_mean() -> None:
    got = decide(rows_for(values={"c4": -0.75, "c7": -0.5}))
    assert (got.label, got.winner, got.stands) == ("SELECTED", "c7", "c7")


def test_selected_c0_is_tuned_and_the_default_kept() -> None:
    got = decide(rows_for(values={"c0": -0.5}))
    assert (got.label, got.winner, got.stands) == ("SELECTED", "c0", "c0")
    assert "tuned and the default kept" in bt.label_line(got)


def test_an_exact_tie_goes_to_the_lower_number_so_c0_wins_ties() -> None:
    got = decide(rows_for())
    assert got.winner == "c0"
    got = decide(rows_for(values={"c10": -0.5, "c2": -0.5, "c9": -0.5}))
    assert got.winner == "c2"


def test_c10_ranks_by_its_number_not_by_its_text() -> None:
    assert decide(rows_for(values={"c10": -0.5, "c9": -0.5})).winner == "c9"


def test_weak_and_c0b_cannot_win_however_high_they_read() -> None:
    got = decide(rows_for(values={"c0b": 5.0, "c3": -0.5}))
    assert got.winner == "c3"
    assert "c0b" not in got.ranking and "weak" not in got.ranking


def test_bug_stop_when_weak_does_not_lose_to_c0() -> None:
    got = decide(rows_for(values={"weak": -1.0}))
    assert (got.label, got.stands) == ("BUG_STOP", "c0")
    assert got.winner is None


def test_bug_stop_when_weak_beats_c0() -> None:
    assert decide(rows_for(values={"weak": -0.5})).label == "BUG_STOP"


def test_not_separated_when_the_interval_touches_zero() -> None:
    got = decide(rows_for(values={"c3": -0.5}, gain=0.0))
    assert (got.label, got.stands) == ("NOT_SEPARATED", "c0")
    assert got.h1.low == 0.0


def test_not_separated_when_the_interval_straddles_zero() -> None:
    rows = noisy_gains(
        rows_for(values={"c3": -0.5}),
        {"t1": -0.5, "t2": 0.5, "t3": -0.25, "t4": 0.25},
    )
    got = decide(rows)
    assert got.h1.low < 0.0 < got.h1.high
    assert (got.label, got.stands) == ("NOT_SEPARATED", "c0")


def test_not_separated_when_the_interval_is_wholly_below_zero() -> None:
    got = decide(rows_for(values={"c3": -0.5}, gain=-0.01))
    assert got.h1.high < 0.0
    assert got.label == "NOT_SEPARATED"


def test_incomplete_below_54_of_60_and_the_boundary_is_inclusive() -> None:
    truths = tuple(f"t{i}" for i in range(20))
    all_rows = rows_for(truths, SEEDS, {"c3": -0.5})
    gone = list(bt.world_order(truths, SEEDS))

    def without(n: int) -> list[Any]:
        drop = set(gone[:n])
        return [
            r for r in all_rows if (r.truth, r.seed) not in drop or r.setting != "c1"
        ]

    assert bt.MIN_COMPLETE_WORLDS == 54
    assert (
        decide(without(6), truths, min_complete=bt.MIN_COMPLETE_WORLDS).label
        == "SELECTED"
    )
    got = decide(without(7), truths, min_complete=bt.MIN_COMPLETE_WORLDS)
    assert (got.label, got.stands) == ("INCOMPLETE", "c0")


def test_a_dropped_world_counts_against_the_54() -> None:
    truths = tuple(f"t{i}" for i in range(20))
    rows = rows_for(truths, SEEDS, {"c3": -0.5})
    lost = set(bt.world_order(truths, SEEDS)[:7])
    rows = [
        replace(r, why="other_failure", per_event=math.nan, curve=())
        if (r.truth, r.seed) in lost
        else r
        for r in rows
    ]
    got = decide(rows, truths, min_complete=54)
    assert got.label == "INCOMPLETE"
    assert len(got.dropped) == 7


def test_no_complete_world_is_incomplete_and_decides_nothing_else() -> None:
    got = decide([])
    assert (got.label, got.stands) == ("INCOMPLETE", "c0")
    assert got.h1 is None and got.means == {}


def test_the_rules_apply_in_order_bug_stop_first() -> None:
    """Each pair: both rules fit, and the earlier one wins."""
    truths = tuple(f"t{i}" for i in range(20))
    lost = set(bt.world_order(truths, SEEDS)[:7])

    def thin(rows: list[Any]) -> list[Any]:
        return [r for r in rows if (r.truth, r.seed) not in lost or r.setting != "c1"]

    bug = thin(rows_for(truths, SEEDS, {"weak": -0.5}, gain=0.0))
    assert decide(bug, truths, min_complete=54).label == "BUG_STOP"
    incomplete = thin(rows_for(truths, SEEDS, {"c3": -0.5}, gain=0.0))
    assert decide(incomplete, truths, min_complete=54).label == "INCOMPLETE"
    assert decide(rows_for(values={"weak": -0.5}, gain=0.0)).label == "BUG_STOP"


def test_h1_is_reported_whatever_the_label() -> None:
    assert decide(rows_for(values={"weak": -0.5})).h1 is not None
    assert decide(rows_for(gain=0.0)).h1 is not None


def test_c0bs_rank_among_the_12_is_reported() -> None:
    got = decide(rows_for(values={"c0b": -0.75, "c3": -0.5, "c4": -0.625}))
    assert got.c0b_rank == 3


def test_c0b_at_the_top_ranks_first() -> None:
    assert decide(rows_for(values={"c0b": 0.0})).c0b_rank == 1


def test_a_floored_candidate_loses_what_the_floor_says() -> None:
    rows = rows_for(values={"c3": -0.5})
    rows = swap(rows, reading("c3", "t1", 1, -math.inf, "minus_inf", ()))
    got = decide(rows)
    assert got.means["c3"] == pytest.approx(
        ((-1.5 - 0.5 - 0.5) / 3 + 3 * -0.5) / 4, abs=1e-12
    )


def test_the_decision_does_not_depend_on_the_order_of_the_rows() -> None:
    rows = rows_for(values={"c3": -0.5, "c0b": -0.75})
    one = bt.selection_bytes(decide(rows), IDENTITY, "complete")
    two = bt.selection_bytes(decide(list(reversed(rows))), IDENTITY, "complete")
    assert one == two


IDENTITY: Final = {"script_sha256": "s" * 64, "fingerprint": "toy"}


def test_the_decision_is_the_same_bytes_twice() -> None:
    rows = rows_for(values={"c3": -0.5})
    assert bt.selection_bytes(decide(rows), IDENTITY, "complete") == bt.selection_bytes(
        decide(rows), IDENTITY, "complete"
    )


def test_selection_json_holds_what_section_10_reports() -> None:
    rows = rows_for(values={"c3": -0.5})
    rows = swap(rows, reading("c2", "t1", 1, -math.inf, "cpu_limit", ()))
    body = json.loads(bt.selection_bytes(decide(rows), IDENTITY, "complete"))
    assert body["label"] == "SELECTED" and body["winner"] == "c3"
    assert body["stands"] == "c3"
    assert set(body["means"]) == {s.name for s in bt.SETTINGS}
    assert float.fromhex(body["means"]["c3"]) == -0.5
    assert body["c0b_rank"] >= 1
    assert body["floors"]["c2"] == {"cpu_limit": 1}
    assert body["worlds"] == {
        "planned": 12,
        "complete": 12,
        "dropped": [],
        "unfinished": [],
    }
    assert body["h1"]["low"] and body["h1"]["high"]
    assert body["end"] == "complete"
    assert body["identity"] == IDENTITY


def test_selection_json_never_holds_cpu_so_its_bytes_can_repeat() -> None:
    text = bt.selection_bytes(decide(rows_for()), IDENTITY, "complete").decode()
    assert "cpu" not in text.replace("cpu_limit", "")


def test_the_secondary_table_is_labelled_exploratory_and_covers_every_candidate() -> (
    None
):
    halves = {t: t in ("t1", "t2") for t in TRUTHS}
    table = table_of(rows_for(values={"c3": -0.5}))
    out = bt.secondary(table, toy_spec(), halves)
    assert out["label"] == "exploratory"
    assert set(out["difference_from_c0"]) == set(bt.SELECTABLE) - {"c0"}
    c3 = out["difference_from_c0"]["c3"]
    assert c3["mean"] == 0.5 and c3["low"] == 0.5 and c3["high"] == 0.5
    assert out["by_half"]["in_dictionary"]["c3"] == -0.5
    assert out["by_half"]["out_of_dictionary"]["c3"] == -0.5


# --------------------------------------------------------------------------
# Fails closed: any label but SELECTED leaves c0
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"label": "BUG_STOP", "winner": "c3"},
        {"label": "INCOMPLETE", "winner": "c3"},
        {"label": "NOT_SEPARATED", "winner": "c3"},
        {"label": "selected", "winner": "c3"},
        {"label": "SELECTED c3", "winner": "c3"},
        {"label": "SELECTED", "winner": "c0b"},
        {"label": "SELECTED", "winner": "weak"},
        {"label": "SELECTED", "winner": "c99"},
        {"label": "SELECTED", "winner": None},
        {"label": "SELECTED"},
        {"winner": "c3"},
        {},
    ],
)
def test_anything_but_a_well_formed_selected_leaves_c0(body: dict[str, Any]) -> None:
    assert bt.stands(body) == "c0"


def test_a_well_formed_selected_stands() -> None:
    assert bt.stands({"label": "SELECTED", "winner": "c3"}) == "c3"


def test_no_selection_file_leaves_c0(tmp_path: Path) -> None:
    assert bt.stands_at(tmp_path / "selection.json") == "c0"


def test_an_unreadable_selection_file_leaves_c0(tmp_path: Path) -> None:
    path = tmp_path / "selection.json"
    path.write_text('{"label": "SELECTED", "win', encoding="utf-8")
    assert bt.stands_at(path) == "c0"


def test_a_selection_file_that_is_no_object_leaves_c0(tmp_path: Path) -> None:
    path = tmp_path / "selection.json"
    path.write_text('["SELECTED", "c3"]', encoding="utf-8")
    assert bt.stands_at(path) == "c0"


# --------------------------------------------------------------------------
# The driver: CPU, limits, stops, restarts (sections 6, 8, 9)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StubJob:
    """A unit that runs no fit: it writes the record a unit would."""

    id: str
    setting: str
    truth: str
    seed: int
    value: float
    behaviour: str = "ok"
    marker: str = ""

    def run(self, sink: Any) -> None:
        if self.behaviour == "burn":
            while True:
                math.fsum(float(i) for i in range(1000))
        if self.behaviour == "raise":
            raise RuntimeError("a bug, not a unit failure")
        if self.behaviour == "die":
            os.kill(os.getpid(), signal.SIGKILL)
        if self.behaviour == "die_once" and not Path(self.marker).exists():
            Path(self.marker).write_text("x", encoding="utf-8")
            os.kill(os.getpid(), signal.SIGKILL)
        curve = [[LOW, "m", self.value - 0.01], [FITS, "m", self.value]]
        result = UnitResult(
            B_SYM,
            True,
            {"status": 1.0, "has_structure": 1.0, "per_event": self.value},
            {"curve": curve, "submitted": "m"},
            0.0,
        )
        sink.record(
            "unit", bt.unit_payload(self.setting, self.truth, self.seed, result)
        )


def stub_value(setting: str) -> float:
    return {"weak": -1.5, "c3": -0.5}.get(setting, -1.0)


def stubbed(
    plan: Sequence[Any],
    changes: Mapping[tuple[str, str, int], Mapping[str, Any]] | None = None,
) -> tuple[Any, ...]:
    out = []
    for unit in plan:
        extra = (changes or {}).get((unit.setting, unit.truth, unit.seed), {})
        job = StubJob(
            unit.job.id,
            unit.setting,
            unit.truth,
            unit.seed,
            stub_value(unit.setting),
            **extra,
        )
        out.append(replace(unit, job=job))
    return tuple(out)


def stub_run(seeds: Sequence[int] = (1,)) -> tuple[Any, tuple[Any, ...]]:
    spec, _, plan = toy_plan(seeds)
    return spec, stubbed(plan)


FAST: Final[dict[str, Any]] = {
    "workers": 2,
    "start_method": "fork",
    "poll_s": 0.005,
    "cpu_poll_s": 0.0,
    "read_usage_usec": lambda: 0,
    "read_mem_gib": lambda: 16.0,
}


def stage1(
    spec: Any,
    plan: Sequence[Any],
    out: Path,
    *,
    log: Callable[[str], None] | None = None,
    stop_s: float = 3600.0,
    cpu_limit_s: int = 60,
    **driver: Any,
) -> Any:
    kwargs = FAST | driver
    return bt.run_stage1(
        spec,
        plan,
        bt.OutPaths(out),
        IDENTITY,
        stop_s=stop_s,
        cpu_limit_s=cpu_limit_s,
        log=log or (lambda _line: None),
        **kwargs,
    )


def test_a_full_run_writes_the_store_and_selection(tmp_path: Path) -> None:
    spec, plan = stub_run()
    outcome = stage1(spec, plan, tmp_path)
    assert outcome.end == "complete"
    assert outcome.selection is not None
    body = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert (body["label"], body["winner"]) == ("SELECTED", "c3")
    with ResultStore(tmp_path, read_only=True) as store:
        assert len(store.cells()) == len(plan)


def test_a_cell_carries_the_campaigns_fields_and_the_setting(tmp_path: Path) -> None:
    spec, plan = stub_run()
    stage1(spec, plan, tmp_path)
    with ResultStore(tmp_path, read_only=True) as store:
        got = store.cell(plan[0].job.id)
    assert got["detail"]["unit"] == {
        "truth": plan[0].truth,
        "seed": plan[0].seed,
        "system": B_SYM,
        "kind": "baseline",
    }
    assert got["detail"]["setting"] == plan[0].setting
    assert got["detail"]["system_config"] == plan[0].text
    assert got["detail"]["result"]["submitted"] == "m"


def test_a_unit_past_its_cpu_limit_is_killed_and_takes_the_floor(
    tmp_path: Path,
) -> None:
    spec, plan = stub_run()
    target = plan[1]
    key = (target.setting, target.truth, target.seed)
    plan = stubbed(plan, {key: {"behaviour": "burn"}})
    outcome = stage1(spec, plan, tmp_path, cpu_limit_s=1)
    assert outcome.end == "complete"
    records = bt.read_records(tmp_path / "journal.jsonl")
    assert [r["job"] for r in records if r["type"] == "unit_killed"] == [target.job.id]
    body = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert body["floors"][target.setting] == {"cpu_limit": 1}
    assert body["worlds"]["complete"] == 2


test_a_unit_past_its_cpu_limit_is_killed_and_takes_the_floor = pytest.mark.slow(
    test_a_unit_past_its_cpu_limit_is_killed_and_takes_the_floor
)


def test_dispatch_stops_at_the_cpu_stop_and_units_in_flight_finish(
    tmp_path: Path,
) -> None:
    """The kernel's count reads over the stop from the first tick."""
    spec, plan = stub_run()
    outcome = stage1(
        spec, plan, tmp_path, stop_s=10.0, read_usage_usec=lambda: 11 * 10**6
    )
    assert outcome.end == "cpu_stop"
    assert outcome.selection is not None
    assert outcome.selection.label == "INCOMPLETE"
    assert outcome.selection.stands == "c0"


def test_the_cpu_count_spans_a_restart(tmp_path: Path) -> None:
    """1 000 s in a killed first unit and 900 s in the second reach the 1,500 s stop,
    which neither alone does; a count that reset would run on."""
    spec, plan = stub_run()
    paths = bt.OutPaths(tmp_path)
    first = bt.CpuLedger.begin(paths.cpu)
    first.update(usage_usec=1000 * 10**6)
    assert json.loads(paths.cpu.read_text(encoding="utf-8"))["current_usec"] == 10**9
    outcome = stage1(
        spec,
        plan[:2],
        tmp_path,
        stop_s=1500.0,
        read_usage_usec=lambda: 900 * 10**6,
    )
    assert outcome.end == "cpu_stop"
    assert bt.CpuLedger.load(paths.cpu).earlier_usec == 1900 * 10**6


def test_the_cpu_count_keeps_earlier_usage_when_the_new_unit_reads_low(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cpu.json"
    one = bt.CpuLedger.begin(path)
    one.update(usage_usec=5)
    two = bt.CpuLedger.begin(path)
    assert (two.earlier_usec, two.current_usec, two.total_usec) == (5, 0, 5)
    two.update(usage_usec=3)
    three = bt.CpuLedger.begin(path)
    assert three.total_usec == 8


def test_a_resume_skips_units_on_record_and_runs_the_rest(tmp_path: Path) -> None:
    """A driver SIGKILLed between units leaves no run_end; the same command goes on."""
    spec, plan = stub_run()
    done = plan[:5]
    paths = bt.OutPaths(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    bt.CpuLedger.begin(paths.cpu)
    journal = bt.Journal(paths.journal)
    journal.append({"type": "run_start"})
    for unit in done:
        unit.job.run(bt.Sink(paths.journal, unit.job.id))
    ran = plan[5:]
    outcome = stage1(spec, [*done, *ran], tmp_path)
    assert outcome.end == "complete"
    records = bt.read_records(paths.journal)
    units = [r["job"] for r in records if r["type"] == "unit"]
    assert sorted(units) == sorted(u.job.id for u in plan)
    with ResultStore(tmp_path, read_only=True) as store:
        assert len(store.cells()) == len(plan)


def test_a_resume_ingests_a_record_the_dead_driver_never_stored(
    tmp_path: Path,
) -> None:
    spec, plan = stub_run()
    paths = bt.OutPaths(tmp_path)
    bt.CpuLedger.begin(paths.cpu)
    bt.Journal(paths.journal).append({"type": "run_start"})
    plan[0].job.run(bt.Sink(paths.journal, plan[0].job.id))
    assert stage1(spec, plan, tmp_path).end == "complete"
    with ResultStore(tmp_path, read_only=True) as store:
        assert store.ledger.contains(plan[0].job.id)


def test_a_killed_unit_stays_killed_across_a_resume(tmp_path: Path) -> None:
    """Rerunning it would spend its limit again and could hand it a row."""
    spec, plan = stub_run()
    target = plan[0]
    paths = bt.OutPaths(tmp_path)
    bt.CpuLedger.begin(paths.cpu)
    journal = bt.Journal(paths.journal)
    journal.append({"type": "run_start"})
    journal.append(
        {
            "type": "unit_killed",
            "job": target.job.id,
            "setting": target.setting,
            "reason": "cpu_limit",
        }
    )
    outcome = stage1(spec, plan, tmp_path)
    assert outcome.end == "complete"
    records = bt.read_records(paths.journal)
    assert target.job.id not in [r["job"] for r in records if r["type"] == "unit"]
    body = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert body["floors"][target.setting] == {"cpu_limit": 1}


def test_a_unit_lost_twice_runs_again_on_a_resume(tmp_path: Path) -> None:
    """An environment's failure is no result, so it gets no floor and no latch."""
    _, plan = stub_run()
    paths = bt.OutPaths(tmp_path)
    jobs = tuple(u.job for u in plan)
    bt.CpuLedger.begin(paths.cpu)
    journal = bt.Journal(paths.journal)
    journal.append({"type": "run_start"})
    journal.append({"type": "job_lost", "job": jobs[0].id})
    journal.append({"type": "unit_lost", "job": jobs[0].id})
    driver = bt.Driver(
        paths, jobs, stop_s=3600.0, cpu_limit_s=60, log=lambda _line: None, **FAST
    )
    assert driver.run() == "complete"
    records = bt.read_records(paths.journal)
    assert jobs[0].id in [r["job"] for r in records if r["type"] == "unit"]


def test_a_torn_last_line_is_dropped_on_resume(tmp_path: Path) -> None:
    spec, plan = stub_run()
    paths = bt.OutPaths(tmp_path)
    bt.CpuLedger.begin(paths.cpu)
    bt.Journal(paths.journal).append({"type": "run_start"})
    with paths.journal.open("ab") as handle:
        handle.write(b'{"type": "un')
    assert stage1(spec, plan, tmp_path).end == "complete"


def test_stop_ends_dispatch_and_section_5_applies(tmp_path: Path) -> None:
    spec, plan = stub_run()
    (tmp_path / "STOP").write_text("", encoding="utf-8")
    outcome = stage1(spec, plan, tmp_path)
    assert outcome.end == "stop"
    assert outcome.selection is not None and outcome.selection.stands == "c0"
    assert (tmp_path / "selection.json").exists()


def test_a_stop_cannot_be_undone_by_removing_the_file(tmp_path: Path) -> None:
    """The driver died before writing the label; STOP is gone. Nothing dispatches."""
    spec, plan = stub_run()
    paths = bt.OutPaths(tmp_path)
    bt.CpuLedger.begin(paths.cpu)
    journal = bt.Journal(paths.journal)
    journal.append({"type": "run_start"})
    journal.append({"type": "run_end", "reason": "stop"})
    outcome = stage1(spec, plan, tmp_path)
    assert outcome.end == "stop"
    assert not [r for r in bt.read_records(paths.journal) if r["type"] == "unit"]


def test_a_bug_ends_the_run_with_no_selection_and_refuses_a_resume(
    tmp_path: Path,
) -> None:
    spec, plan = stub_run()
    target = plan[0]
    key = (target.setting, target.truth, target.seed)
    bad = stubbed(plan, {key: {"behaviour": "raise"}})
    outcome = stage1(spec, bad, tmp_path)
    assert outcome.end == "bug"
    assert outcome.selection is None
    assert not (tmp_path / "selection.json").exists()
    with pytest.raises(bt.ResumeRefused, match="bug"):
        stage1(spec, bad, tmp_path)


def test_a_lost_worker_is_retried_once(tmp_path: Path) -> None:
    spec, plan = stub_run()
    target = plan[1]
    key = (target.setting, target.truth, target.seed)
    marker = str(tmp_path / "marker")
    plan = stubbed(plan, {key: {"behaviour": "die_once", "marker": marker}})
    assert stage1(spec, plan, tmp_path / "run").end == "complete"
    records = bt.read_records(tmp_path / "run" / "journal.jsonl")
    assert [r["job"] for r in records if r["type"] == "job_lost"] == [target.job.id]
    assert target.job.id in [r["job"] for r in records if r["type"] == "unit"]


def test_a_unit_lost_twice_leaves_its_world_unfinished(tmp_path: Path) -> None:
    spec, plan = stub_run()
    target = plan[1]
    key = (target.setting, target.truth, target.seed)
    plan = stubbed(plan, {key: {"behaviour": "die"}})
    outcome = stage1(spec, plan, tmp_path)
    assert outcome.end == "complete"
    assert (target.truth, target.seed) in outcome.selection.unfinished


def test_sigterm_kills_units_in_flight_writes_no_label_and_resumes(
    tmp_path: Path,
) -> None:
    spec, plan = stub_run()
    paths = bt.OutPaths(tmp_path)
    driver = bt.Driver(
        paths,
        tuple(u.job for u in plan),
        stop_s=3600.0,
        cpu_limit_s=60,
        log=lambda _line: None,
        **FAST,
    )
    driver.terminating = True
    assert driver.run() == "terminated"
    assert bt.journal_end(bt.read_records(paths.journal)) == "terminated"
    assert stage1(spec, plan, tmp_path).end == "complete"


def test_no_dispatch_while_memory_is_below_the_gate() -> None:
    assert bt.MEM_GATE_GIB == 6
    assert bt.may_start(running=0, workers=2, mem_gib=6.0)
    assert not bt.may_start(running=0, workers=2, mem_gib=5.99)
    assert not bt.may_start(running=2, workers=2, mem_gib=16.0)


def test_exit_kinds() -> None:
    assert bt.exit_kind(0) == "ok"
    assert bt.exit_kind(-signal.SIGXCPU) == "cpu_limit"
    assert bt.exit_kind(-signal.SIGTERM) == "terminated"
    assert bt.exit_kind(-signal.SIGKILL) == "lost"
    assert bt.exit_kind(1) == "bug"


def test_the_stage_limits_are_the_texts() -> None:
    assert bt.STAGE1.limit_s == 2 * 3600 and bt.STAGE1.stop_s == 90 * 3600
    assert bt.STAGE2.limit_s == 8 * 3600 and bt.STAGE2.stop_s == 84 * 3600
    for stage in (bt.STAGE1, bt.STAGE2):
        assert stage.stop_s + 2 * stage.limit_s <= 100 * 3600


def test_the_cpu_limit_is_set_soft_below_hard_so_it_arrives_as_sigxcpu() -> None:
    assert bt.CPU_HARD_MARGIN_S > 0


def test_cpu_read_from_the_units_cgroup(tmp_path: Path) -> None:
    proc = tmp_path / "cgroup"
    proc.write_text("0::/user.slice/toy.service\n", encoding="utf-8")
    stat = tmp_path / "root" / "user.slice" / "toy.service"
    stat.mkdir(parents=True)
    (stat / "cpu.stat").write_text("usage_usec 1234\nuser_usec 1\n", encoding="utf-8")
    assert bt.read_usage_usec(proc, tmp_path / "root") == 1234


def test_a_missing_cpu_stat_stops_the_run_rather_than_counting_zero(
    tmp_path: Path,
) -> None:
    with pytest.raises(bt.CpuAccountingError):
        bt.read_usage_usec(tmp_path / "absent", tmp_path)


def test_the_cpu_report_counts_a_killed_unit_at_its_limit(tmp_path: Path) -> None:
    timing = tmp_path / "timing.jsonl"
    lines = [
        {"job": "a", "setting": "c0", "cpu_s": 10.0},
        {"job": "b", "setting": "c0", "cpu_s": 5.0},
        {"job": "c", "setting": "c3", "cpu_s": 7.0},
    ]
    timing.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    killed = [{"type": "unit_killed", "job": "d", "setting": "c3"}]
    assert bt.cpu_by_setting(timing, killed, 100) == {"c0": 15.0, "c3": 107.0}


# --------------------------------------------------------------------------
# No outcome before the end (section 9)
# --------------------------------------------------------------------------

ALLOWED_LINE: Final = re.compile(
    r"(units \d+/\d+ worlds \d+/\d+ cpu_hours \d+\.\d\d|"
    r"run ended: (complete|stop|cpu_stop|terminated|bug))"
)


def test_nothing_but_counts_and_cpu_is_printed_before_selection_json(
    tmp_path: Path,
) -> None:
    spec, plan = stub_run()
    lines: list[tuple[bool, str]] = []
    selection = tmp_path / "selection.json"

    def log(line: str) -> None:
        lines.append((selection.exists(), line))

    stage1(spec, plan, tmp_path, log=log, print_s=0.0)
    before = [line for exists, line in lines if not exists]
    after = [line for exists, line in lines if exists]
    assert any(line.startswith("units ") for line in before)
    assert all(ALLOWED_LINE.fullmatch(line) for line in before), before
    assert len(after) == 1 and after[0].startswith("SELECTED c3")


def test_a_progress_line_names_no_setting_and_no_reading(tmp_path: Path) -> None:
    spec, plan = stub_run()
    lines: list[str] = []
    stage1(spec, plan, tmp_path, log=lines.append, print_s=0.0)
    progress = [line for line in lines if line.startswith("units ")]
    assert progress
    for line in progress:
        assert not re.search(r"\bc\d+|weak|-\d|per_event|floor|inf", line)


def test_a_killed_unit_prints_nothing_of_itself_before_the_end(tmp_path: Path) -> None:
    spec, plan = stub_run()
    target = plan[1]
    key = (target.setting, target.truth, target.seed)
    plan = stubbed(plan, {key: {"behaviour": "die"}})
    lines: list[str] = []
    stage1(spec, plan, tmp_path, log=lines.append, print_s=0.0)
    assert all(ALLOWED_LINE.fullmatch(line) for line in lines[:-1]), (
        "a loss printed more than counts"
    )


# --------------------------------------------------------------------------
# Refusals (sections 6, 11)
# --------------------------------------------------------------------------


def test_a_store_under_the_drafts_directory() -> None:
    assert bt.STORE_DIR == ".cache/drafts/bsym-tune"
    assert bt.launch_paths(Path("/repo")).root == Path("/repo/.cache/drafts/bsym-tune")


def test_the_launch_refuses_a_store_anywhere_else(tmp_path: Path) -> None:
    good = tmp_path / ".cache/drafts/bsym-tune"
    bt.check_out(good, tmp_path)
    with pytest.raises(bt.GuardFailure, match="bsym-tune"):
        bt.check_out(tmp_path / ".cache/pilot-merged", tmp_path)
    with pytest.raises(bt.GuardFailure, match="bsym-tune"):
        bt.check_out(tmp_path / "elsewhere", tmp_path)


def test_a_rehearsal_never_writes_where_the_launch_writes() -> None:
    assert bt.rehearsal_paths(Path("/repo"), "x").root.parent == Path(
        "/repo/.cache/drafts/bsym-tune-rehearsal"
    )


def test_a_truth_outside_the_dev_split_is_refused() -> None:
    bad = dataclasses.replace(entry("hawkes"), split="test")
    with pytest.raises(bt.GuardFailure, match="dev"):
        bt.check_truths([entry("size_excitation"), bad])


def test_an_unknown_split_is_refused() -> None:
    bad = dataclasses.replace(entry("hawkes"), split="")
    with pytest.raises(bt.GuardFailure, match="dev"):
        bt.check_truths([bad])


def test_the_dev_split_passes() -> None:
    bt.check_truths(toy_entries())


def test_a_truth_the_plan_was_not_given_is_refused() -> None:
    entries = toy_entries()
    spec = toy_spec(["hawkes", "no-such-truth"], (1,))
    with pytest.raises(bt.GuardFailure, match="no-such-truth"):
        bt.plan_stage1(spec, entries, environment())


@pytest.mark.parametrize(
    "seeds",
    [(1, 2, 3), (4, 5), (4, 5, 7), (4, 5, 6, 7), (6, 5, 4), (7, 8, 9), ()],
)
def test_stage_1_allows_exactly_seeds_4_5_and_6(seeds: tuple[int, ...]) -> None:
    with pytest.raises(bt.GuardFailure, match="seeds"):
        bt.check_stage1_seeds(seeds)


def test_stage_1s_own_seeds_pass_the_check_and_draw_nothing() -> None:
    bt.check_stage1_seeds((4, 5, 6))


def test_the_launch_spec_is_the_texts_design() -> None:
    ids = tuple(f"dev-{i:03}" for i in range(20))
    spec = bt.launch_spec(ids)
    assert spec.seeds == (4, 5, 6) and spec.fits == 40 and spec.graded_low == 30
    assert spec.min_complete == 54 and spec.resamples == 10_000
    assert spec.root == 20261113 and spec.mode == "launch"


def test_the_launch_spec_refuses_anything_but_all_20_truths() -> None:
    with pytest.raises(bt.GuardFailure, match="20"):
        bt.launch_spec(tuple(f"dev-{i:03}" for i in range(19)))


def test_a_launch_spec_with_any_other_seeds_or_budget_is_refused() -> None:
    ids = tuple(f"dev-{i:03}" for i in range(20))
    spec = bt.launch_spec(ids)
    for change in (
        {"seeds": (1, 2, 3)},
        {"fits": 8},
        {"graded_low": 20},
        {"min_complete": 1},
        {"root": TEST_ROOT},
        {"resamples": 100},
    ):
        with pytest.raises(bt.GuardFailure):
            bt.check_spec(replace(spec, **change))


def test_a_rehearsal_holds_seeds_1_to_3_and_its_own_root_only() -> None:
    root = bt.REHEARSAL_ROOTS[0]
    good = replace(toy_spec(("hawkes",), (1,)), root=root)
    bt.check_spec(good)
    for seeds in ((7,), (0,), (1, 8)):
        with pytest.raises(bt.GuardFailure, match="seeds"):
            bt.check_spec(replace(good, seeds=seeds))
    with pytest.raises(bt.GuardFailure, match="root"):
        bt.check_spec(replace(good, root=TEST_ROOT))


def test_no_rehearsal_root_is_a_launch_root() -> None:
    assert bt.SEED_ROOT not in bt.REHEARSAL_ROOTS
    assert not set(bt.REHEARSAL_ROOTS) & FROZEN_STREAMS


def test_a_store_holding_cells_of_another_run_is_refused(tmp_path: Path) -> None:
    spec, plan = stub_run()
    assert stage1(spec, plan[:3], tmp_path).end == "complete"
    (tmp_path / "selection.json").unlink()
    (tmp_path / "journal.jsonl").unlink()
    (tmp_path / "cpu.json").unlink()
    with pytest.raises(bt.GuardFailure, match="other run"):
        stage1(spec, plan[3:], tmp_path)


def test_a_store_holding_only_this_runs_cells_is_accepted(tmp_path: Path) -> None:
    _, plan = stub_run()
    with ResultStore(tmp_path) as store:
        store.put(plan[0].key, {"status": 0.0}, {"result": {}})
    bt.check_store(tmp_path, {u.job.id for u in plan})


def test_a_detail_file_with_no_ledger_row_counts_as_a_cell(tmp_path: Path) -> None:
    _, plan = stub_run()
    (tmp_path / "details").mkdir()
    (tmp_path / "details" / ("0" * 64 + ".json")).write_text("{}", encoding="utf-8")
    with pytest.raises(bt.GuardFailure, match="other run"):
        bt.check_store(tmp_path, {u.job.id for u in plan})


def test_a_start_is_refused_once_selection_json_exists(tmp_path: Path) -> None:
    spec, plan = stub_run()
    assert stage1(spec, plan, tmp_path).end == "complete"
    before = (tmp_path / "selection.json").read_bytes()
    with pytest.raises(bt.GuardFailure, match=r"selection\.json"):
        stage1(spec, plan, tmp_path)
    assert (tmp_path / "selection.json").read_bytes() == before


def test_selection_json_is_checked_before_anything_else_in_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No split, no guard, no directory is touched: the file alone refuses."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "selection.json").write_text("{}", encoding="utf-8")
    code = bt.main(["--out", str(out), "--split", str(tmp_path / "none")])
    assert code == bt.EXIT_REFUSED
    assert "selection.json" in capsys.readouterr().out


def test_a_changed_script_cannot_resume_a_run(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    bt.CpuLedger.begin(path, "a" * 64)
    with pytest.raises(bt.ResumeRefused, match="script"):
        bt.CpuLedger.begin(path, "b" * 64)


def test_another_identity_cannot_resume_a_run(tmp_path: Path) -> None:
    paths = bt.OutPaths(tmp_path)
    bt.check_manifest(paths, {"spec": "one"})
    bt.check_manifest(paths, {"spec": "one"})
    with pytest.raises(bt.ResumeRefused, match="manifest"):
        bt.check_manifest(paths, {"spec": "two"})


def test_a_second_driver_on_one_store_is_refused(tmp_path: Path) -> None:
    paths = bt.OutPaths(tmp_path)
    with bt.driver_lock(paths), pytest.raises(bt.ResumeRefused), bt.driver_lock(paths):
        pass


def test_the_launch_refuses_until_the_text_is_frozen(tmp_path: Path) -> None:
    assert bt.PREREG_SHA256 == "" and bt.F_COMMIT == ""
    with pytest.raises(bt.GuardFailure, match="not frozen"):
        bt.launch_guards(tmp_path, prereg_sha="", f_commit="", environ={})


def test_the_launch_refuses_a_changed_text(tmp_path: Path) -> None:
    prereg = tmp_path / bt.PREREG_PATH
    prereg.parent.mkdir(parents=True)
    prereg.write_text("text", encoding="utf-8")
    with pytest.raises(bt.GuardFailure, match="prereg hash"):
        bt.launch_guards(tmp_path, prereg_sha="0" * 64, f_commit="f", environ={})


def test_blas_threads_must_be_1_at_start() -> None:
    good = dict.fromkeys(bt.BLAS_VARIABLES, "1")
    bt.check_blas(good)
    with pytest.raises(bt.GuardFailure, match="OMP_NUM_THREADS"):
        bt.check_blas(good | {"OMP_NUM_THREADS": "4"})


def test_the_launch_runs_in_its_own_unit_so_the_kernels_count_is_its_own() -> None:
    bt.check_unit(f"0::/user.slice/app.slice/{bt.UNIT_NAME}.service\n")
    with pytest.raises(bt.GuardFailure, match=bt.UNIT_NAME):
        bt.check_unit("0::/user.slice/session-3.scope\n")


def test_the_split_digest_is_pinned(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    with pytest.raises(bt.GuardFailure, match="split hash"):
        bt.check_split(tmp_path)


# --------------------------------------------------------------------------
# The launch form, written and not run
# --------------------------------------------------------------------------


def test_the_launch_line_is_the_texts_machine_section() -> None:
    line = bt.launch_line(Path("/repo"), Path("/split"))
    assert line.startswith("systemd-run --user ")
    assert f"--unit={bt.UNIT_NAME}" in line
    assert "--nice=10" in line and "--workers 2" in line
    assert "--working-directory=/repo" in line
    for name in bt.BLAS_VARIABLES:
        assert f"-E {name}=1" in line


def test_the_same_command_can_run_again_after_a_failed_unit() -> None:
    """A failed transient unit keeps its name until collected, which would refuse
    section 6's resume by the same command."""
    assert "--collect" in bt.launch_line(Path("/repo"), Path("/split"))
    assert "--collect" in bt.__doc__


def test_a_rehearsal_cannot_write_into_the_launchs_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Its selection.json there would latch the launch out for good."""
    monkeypatch.setattr(bt.pilot, "ROOT", tmp_path)
    out = tmp_path / bt.STORE_DIR
    args = ["--rehearsal", "--truths", "dev-000", "--seeds", "1", "--out", str(out)]
    assert bt.main(args) == bt.EXIT_REFUSED
    assert "launch" in capsys.readouterr().out
    assert not (out / "selection.json").exists()


@pytest.mark.parametrize(
    "extra",
    [["--seeds", "7"], ["--truths", "dev-000"], ["--fits", "8"]],
)
def test_a_launch_refuses_a_rehearsals_arguments_rather_than_ignore_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    """Non-control values: the rule refuses every value, so none need be a launch's."""
    args = ["--out", str(tmp_path / "out"), "--split", str(tmp_path), *extra]
    assert bt.main(args) == bt.EXIT_REFUSED
    assert "rehearsal" in capsys.readouterr().out


def test_records_with_no_cpu_count_are_not_resumed(tmp_path: Path) -> None:
    """A count that restarted at zero would hand the run its ceiling again."""
    spec, plan = stub_run()
    bt.Journal(tmp_path / "journal.jsonl").append({"type": "run_start"})
    with pytest.raises(bt.ResumeRefused, match="CPU count"):
        stage1(spec, plan, tmp_path)


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def toy_checkout(repo: Path) -> tuple[str, str]:
    """A checkout with a frozen text and a script; returns its hash and commit."""
    prereg = repo / bt.PREREG_PATH
    prereg.parent.mkdir(parents=True)
    prereg.write_text("the text\n", encoding="utf-8")
    log = repo / bt.LOG_PATH
    log.write_text("log\n", encoding="utf-8")
    script = repo / bt.SCRIPT_PATH
    script.parent.mkdir(parents=True)
    script.write_text("script\n", encoding="utf-8")
    (repo / "src").mkdir()
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "text and script")
    return hashlib.sha256(prereg.read_bytes()).hexdigest(), git(
        repo, "rev-parse", "HEAD"
    )


def guards(repo: Path, sha: str, commit: str, **environ: str) -> dict[str, str]:
    out: dict[str, str] = bt.launch_guards(
        repo,
        prereg_sha=sha,
        f_commit=commit,
        environ=dict.fromkeys(bt.BLAS_VARIABLES, "1") | environ,
        packages=[repo / "src" / "sciagent"],
    )
    return out


def test_the_launch_needs_the_scripts_commit_in_the_log(tmp_path: Path) -> None:
    sha, commit = toy_checkout(tmp_path)
    with pytest.raises(bt.GuardFailure, match="LOG"):
        guards(tmp_path, sha, commit)
    log = tmp_path / bt.LOG_PATH
    log.write_text(f"log\nscript at {commit[:7]}\n", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "name it")
    identity = guards(tmp_path, sha, commit)
    assert identity["F"] == commit and identity["prereg_sha256"] == sha
    assert identity["fingerprint"].startswith("commit=")


def test_the_launch_refuses_a_dirty_tree(tmp_path: Path) -> None:
    sha, commit = toy_checkout(tmp_path)
    (tmp_path / "scripts" / "new.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(bt.GuardFailure, match="not clean"):
        guards(tmp_path, sha, commit)


def test_the_launch_refuses_a_head_not_descended_from_the_freeze(
    tmp_path: Path,
) -> None:
    sha, _ = toy_checkout(tmp_path)
    with pytest.raises(bt.GuardFailure, match="descend"):
        guards(tmp_path, sha, "0" * 40)


def test_the_launch_guards_read_blas_last_and_refuse_it(tmp_path: Path) -> None:
    sha, commit = toy_checkout(tmp_path)
    log = tmp_path / bt.LOG_PATH
    log.write_text(f"log\nscript at {commit[:7]}\n", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "name it")
    with pytest.raises(bt.GuardFailure, match="OMP_NUM_THREADS"):
        guards(tmp_path, sha, commit, OMP_NUM_THREADS="4")


def test_stage_2s_launch_line_differs_by_its_flag_alone() -> None:
    one = bt.launch_line(Path("/repo"), Path("/split"))
    two = bt.launch_line(Path("/repo"), Path("/split"), stage2=True)
    assert two == one + " --stage2"


def test_print_launch_prints_the_line_and_runs_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bt.main(["--print-launch", "--split", str(tmp_path)]) == bt.EXIT_DONE
    assert capsys.readouterr().out.startswith("systemd-run --user ")


def test_the_command_refuses_a_launch_before_the_freeze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bt.main(["--out", str(tmp_path / "out"), "--split", str(tmp_path)])
    assert code == bt.EXIT_REFUSED
    assert "not frozen" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_the_command_refuses_stage_2_before_the_freeze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bt.main(
        ["--stage2", "--out", str(tmp_path / "out"), "--split", str(tmp_path)]
    )
    assert code == bt.EXIT_REFUSED
    assert "not frozen" in capsys.readouterr().out


def test_stage_2_has_no_rehearsal_on_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bt.main(["--stage2", "--rehearsal", "--out", str(tmp_path)])
    assert code == bt.EXIT_REFUSED
    assert "rehearsal" in capsys.readouterr().out


def test_a_rehearsal_names_its_truths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bt.main(["--rehearsal", "--out", str(tmp_path)]) == bt.EXIT_REFUSED
    assert "--truths" in capsys.readouterr().out


@pytest.mark.parametrize("seed", ["7", "9"])
def test_the_command_refuses_a_rehearsal_on_other_seeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], seed: str
) -> None:
    """Non-control seeds: a rehearsal's list refuses everything else, so the launch's
    seeds need not be handed to the command to show it."""
    args = [
        "--rehearsal",
        "--truths",
        "dev-000",
        "--seeds",
        seed,
        "--out",
        str(tmp_path),
    ]
    assert bt.main(args) == bt.EXIT_REFUSED
    assert "seeds" in capsys.readouterr().out


def test_stage_2_runs_under_the_script_that_selected(tmp_path: Path) -> None:
    body = {"identity": {"script_sha256": bt.script_sha256()}}
    bt.check_selected_by_this_script(body)
    with pytest.raises(bt.GuardFailure, match="script"):
        bt.check_selected_by_this_script({"identity": {"script_sha256": "0" * 64}})
    with pytest.raises(bt.GuardFailure, match="script"):
        bt.check_selected_by_this_script({})


def test_the_export_command_writes_the_table_and_prints_its_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, plan = toy_plan((1,))
    store_root = tmp_path / "store"
    key = plan[0].key
    fields = dict(key.config.items()) | {"system": bt.ORACLE, "truth": "dev-000"}
    with ResultStore(store_root) as store:
        store.put(
            replace(key, config=bt.FrozenDict[str, str](fields)),
            {"status": 1.0, "per_event": -1.0},
            {"result": {}},
        )
    target = tmp_path / "table.json"
    args = ["--export-p3-table", str(store_root), "--table-out", str(target)]
    assert bt.main(args) == bt.EXIT_DONE
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert digest in capsys.readouterr().out
    assert (
        bt.load_p3_table(target, digest).rows[("dev-000", int(key.seed), bt.ORACLE)]
        == -1.0
    )


def test_the_table_is_exported_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "table.json"
    target.write_text("{}", encoding="utf-8")
    args = ["--export-p3-table", str(tmp_path), "--table-out", str(target)]
    assert bt.main(args) == bt.EXIT_REFUSED
    assert "exported once" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "{}"


def test_the_docstring_shows_the_launch_form() -> None:
    doc = bt.__doc__
    assert "systemd-run --user" in doc and "--nice=10" in doc and "--workers 2" in doc


def test_workers_are_at_most_2() -> None:
    with pytest.raises(SystemExit):
        bt.build_parser().parse_args(["--workers", "3"])


# --------------------------------------------------------------------------
# p4_plan's tie to the result (section 2, "Where it lands"; section 11)
# --------------------------------------------------------------------------


def standin_truths() -> list[TruthEntry]:
    base = entry("hawkes")
    return [
        dataclasses.replace(
            base, id=f"test-{i:03}", in_dictionary=i % 2 == 0, split="test"
        )
        for i in range(P4_TRUTHS)
    ]


def test_every_config_of_p4_plan_must_hold_the_standing_gp() -> None:
    plan = p4_plan(standin_truths())
    bt.check_plan(plan, environment(), "c0", environment().sym_seeds)
    with pytest.raises(bt.PlanMismatch, match="c3"):
        bt.check_plan(plan, environment(), "c3", environment().sym_seeds)


def test_one_config_off_the_standing_gp_is_enough_to_fail() -> None:
    plan = list(p4_plan(standin_truths()))
    plan[-1] = replace(plan[-1], gp=bt.setting("c3").gp)
    with pytest.raises(bt.PlanMismatch):
        bt.check_plan(plan, environment(), "c0", environment().sym_seeds)


def test_c11_ties_the_environments_seeds_and_not_only_the_gp() -> None:
    """``GPConfig`` is the default under c11, so the gp alone would pass."""
    plan = p4_plan(standin_truths())
    env = environment()
    with pytest.raises(bt.PlanMismatch, match="sym_seeds"):
        bt.check_plan(plan, env, "c11", env.sym_seeds)
    wider = replace(env, sym_seeds=(*env.sym_seeds, Structure((), Link.SOFTPLUS)))
    bt.check_plan(plan, wider, "c11", env.sym_seeds)


def test_p4_plans_gp_is_what_the_committed_record_says() -> None:
    """The tie. The record is committed in the §6.1 commit; until then, c0 stands."""
    record = REPO / bt.SELECTION_RECORD
    stage2 = REPO / bt.STAGE2_RECORD
    name = bt.final_setting_at(record, stage2)
    env = environment()
    bt.check_plan(p4_plan(standin_truths()), env, name, env.sym_seeds)


# --------------------------------------------------------------------------
# The fallback to the next rank, and what stands after stage 2 (sections 2, 5)
# --------------------------------------------------------------------------

RANKED: Final = {
    "label": "SELECTED",
    "winner": "c3",
    "ranking": ["c3", "c7", "c0", "c1", "c2"],
}


def passes(*results: tuple[str, bool]) -> dict[str, Any]:
    return {"passes": [{"setting": s, "passed": p} for s, p in results]}


def test_the_next_rank_is_the_next_candidate_above_c0() -> None:
    assert bt.next_candidate(RANKED, ("c3",)) == "c7"


def test_there_is_no_next_rank_at_or_below_c0() -> None:
    assert bt.next_candidate(RANKED, ("c3", "c7")) is None
    only = RANKED | {"ranking": ["c3", "c0", "c1"]}
    assert bt.next_candidate(only, ("c3",)) is None


def test_a_malformed_ranking_has_no_next_rank() -> None:
    assert bt.next_candidate({"ranking": "c3"}, ("c3",)) is None
    assert bt.next_candidate({}, ("c3",)) is None


def test_a_winner_that_passes_stands() -> None:
    assert bt.final_setting(RANKED, passes(("c3", True))) == "c3"


def test_the_next_rank_stands_when_it_passes_after_the_winner_fails() -> None:
    assert bt.final_setting(RANKED, passes(("c3", False), ("c7", True))) == "c7"


def test_c0_stands_when_both_fail() -> None:
    assert bt.final_setting(RANKED, passes(("c3", False), ("c7", False))) == "c0"


def test_c0_stands_when_no_candidate_ranks_above_it() -> None:
    only = RANKED | {"ranking": ["c3", "c0", "c1"]}
    assert bt.final_setting(only, passes(("c3", False))) == "c0"


def test_a_winner_with_no_stage_2_result_does_not_stand() -> None:
    assert bt.final_setting(RANKED, None) == "c0"


def test_selected_c0_needs_no_stage_2() -> None:
    assert bt.final_setting({"label": "SELECTED", "winner": "c0"}, None) == "c0"


@pytest.mark.parametrize(
    "stage2",
    [
        {},
        {"passes": "c3"},
        {"passes": []},
        passes(("c7", True)),
        passes(("c3", False), ("c1", True)),
        passes(("c3", False), ("c7", False), ("c3", True)),
        {"passes": [{"setting": "c3", "passed": "yes"}]},
        {"passes": ["c3"]},
    ],
)
def test_a_stage_2_record_off_the_rule_leaves_c0(stage2: dict[str, Any]) -> None:
    assert bt.final_setting(RANKED, stage2) == "c0"


def test_a_failed_label_ignores_any_stage_2_result() -> None:
    body = RANKED | {"label": "NOT_SEPARATED"}
    assert bt.final_setting(body, passes(("c3", True))) == "c0"


def test_only_one_fallback_pass_exists() -> None:
    assert bt.MAX_STAGE2_PASSES == 2


# --------------------------------------------------------------------------
# Stage 2: units (section 2)
# --------------------------------------------------------------------------

DEV_IDS: Final = tuple(f"dev-{i:03}" for i in range(20))
TEN_IDS: Final = DEV_IDS[:10]


def stage2_spec(
    truths: Sequence[str] = TEN_IDS,
    ten: Sequence[str] = TEN_IDS,
    *,
    fits: int = FITS,
    resamples: int = 400,
) -> Any:
    return bt.Stage2Spec(
        truths=tuple(truths),
        seeds=SEEDS,
        control_truths=tuple(ten),
        fits=fits,
        root=TEST_ROOT,
        resamples=resamples,
    )


def dev_entries(ids: Sequence[str] = DEV_IDS) -> list[TruthEntry]:
    base = entry("hawkes")
    return [dataclasses.replace(base, id=i) for i in ids]


def test_the_launch_stage_2_spec_is_the_texts() -> None:
    spec = bt.stage2_launch_spec(DEV_IDS)
    assert spec.seeds == (1, 2, 3) and spec.fits == 40
    assert spec.control_truths == TEN_IDS
    assert spec.root == 20261113 and spec.resamples == 10_000


def test_stage_2_runs_the_three_blocks_of_the_text() -> None:
    plan = bt.plan_stage2(
        "c3", bt.stage2_launch_spec(DEV_IDS), dev_entries(), environment()
    )
    bsym = [u for u in plan if u.system == bt.B_SYM]
    planted = [u for u in plan if u.system == bt.PLANTED]
    tenfold = [u for u in plan if u.system == bt.B_SYM_10F]
    assert (len(bsym), len(planted), len(tenfold)) == (60, 60, 10)
    assert {u.seed for u in bsym} == {u.seed for u in planted} == {1, 2, 3}
    assert {(u.truth, u.seed) for u in tenfold} == {(t, 1) for t in TEN_IDS}


def test_stage_2_units_carry_the_candidates_gp_and_seeds() -> None:
    plan = bt.plan_stage2("c3", stage2_spec(), dev_entries(TEN_IDS), environment())
    assert {u.job.config.gp for u in plan} == {bt.setting("c3").gp}
    assert all(u.setting == "c3" for u in plan)
    plan = bt.plan_stage2("c11", stage2_spec(), dev_entries(TEN_IDS), environment())
    softplus = Structure((), Link.SOFTPLUS)
    assert all(u.job.env.sym_seeds[-1] == softplus for u in plan)


def test_a_stage_2_unit_runs_the_system_it_names_at_its_budget() -> None:
    plan = bt.plan_stage2("c3", stage2_spec(), dev_entries(TEN_IDS), environment())
    for u in plan:
        assert u.job.system == u.system
        assert u.job.config.control_factor == 10
        assert u.job.config.fits == FITS


def test_two_candidates_never_share_an_address() -> None:
    args = (stage2_spec(), dev_entries(TEN_IDS), environment())
    one = {u.job.id for u in bt.plan_stage2("c3", *args)}
    two = {u.job.id for u in bt.plan_stage2("c7", *args)}
    assert len(one) == 70 and not one & two


def test_stage_2_refuses_a_truth_outside_dev() -> None:
    bad = dataclasses.replace(dev_entries(TEN_IDS[:1])[0], split="test")
    with pytest.raises(bt.GuardFailure, match="dev"):
        bt.plan_stage2(
            "c3", stage2_spec(TEN_IDS[:1], TEN_IDS[:1]), [bad], environment()
        )


def test_stage_2_refuses_a_control_truth_outside_its_truths() -> None:
    with pytest.raises(bt.GuardFailure, match="dev-001"):
        bt.plan_stage2(
            "c3",
            stage2_spec(TEN_IDS[:1], TEN_IDS[:2]),
            dev_entries(TEN_IDS),
            environment(),
        )


def test_stage_2_refuses_c0_weak_and_c0b() -> None:
    for name in ("c0", "weak", "c0b"):
        with pytest.raises(bt.GuardFailure, match=name):
            bt.plan_stage2(name, stage2_spec(), dev_entries(TEN_IDS), environment())


def test_stage_2s_seeds_are_those_selection_did_not_use() -> None:
    assert bt.STAGE2_SEEDS == (1, 2, 3)
    assert not set(bt.STAGE2_SEEDS) & set(bt.STAGE1_SEEDS)


# --------------------------------------------------------------------------
# Stage 2: the read, by gonogo's rules
# --------------------------------------------------------------------------


def p3_rows(
    truths: Sequence[str] = TEN_IDS,
    *,
    rand: float = -1.25,
    lib: Callable[[str, int], float] | None = None,
) -> Any:
    """ORACLE -1, B-lib below it by a varied margin, B-rand and default B-sym."""
    rows: dict[tuple[str, int, str], float | None] = {}
    for t in truths:
        for s in SEEDS:
            rows[(t, s, bt.ORACLE)] = -1.0
            rows[(t, s, bt.B_LIB)] = -1.5 - 0.0625 * s if lib is None else lib(t, s)
            rows[(t, s, bt.B_RAND)] = rand
            rows[(t, s, B_SYM)] = -1.125
    return bt.P3Table(rows)


def stage2_readings(
    candidate: str = "c3",
    *,
    planted: float = -1.0,
    tenfold: float = -1.0,
    bsym: float = -1.0625,
    spec: Any = None,
) -> list[Any]:
    spec = spec or stage2_spec()
    rows: list[Any] = []
    for t in spec.truths:
        for s in spec.seeds:
            rows.append(reading(B_SYM, t, s, bsym, curve=()))
            rows.append(reading(bt.PLANTED, t, s, planted, curve=()))
    rows += [
        reading(bt.B_SYM_10F, t, 1, tenfold, curve=()) for t in spec.control_truths
    ]
    return rows


def read_pass(
    rows: Sequence[Any],
    table: Any = None,
    spec: Any = None,
    candidate: str = "c3",
) -> dict[str, Any]:
    spec = spec or stage2_spec()
    halves = dict.fromkeys(spec.truths, True)
    out: dict[str, Any] = bt.read_pass(
        candidate, rows, table or p3_rows(), spec, halves, 0
    )
    return out


def test_a_pass_with_both_controls_passing_passes() -> None:
    got = read_pass(stage2_readings())
    assert got["setting"] == "c3" and got["complete"]
    assert got["control_2"]["passed"] and got["control_3"]["passed"]
    assert got["passed"] is True


def test_control_2_is_gonogos_rank_rule_against_the_stored_b_rand() -> None:
    """planted gap 0, stored B-rand gap -0.25: every difference is 0.25."""
    got = read_pass(stage2_readings())["control_2"]
    assert got["wins"] == 10 and got["losses"] == 0
    assert got["median"] == 0.25


def test_control_2_fails_when_planted_does_not_beat_the_stored_b_rand() -> None:
    got = read_pass(stage2_readings(planted=-1.25))
    assert not got["control_2"]["passed"] and got["control_3"]["passed"]
    assert got["passed"] is False


def test_control_3_reads_the_median_against_the_stored_oracle() -> None:
    got = read_pass(stage2_readings(tenfold=-1.0078125))
    assert got["control_3"]["median_gap"] == -0.0078125
    assert got["control_3"]["passed"]


def test_control_3_fails_a_candidate_past_gonogos_tolerance() -> None:
    """The tolerance is gonogo's, and its own tests hold the boundary."""
    got = read_pass(stage2_readings(tenfold=-1.0625))
    assert got["control_3"]["median_gap"] == -0.0625
    assert got["control_3"]["tolerance"] == bt.gonogo.BSYM_TOLERANCE
    assert not got["control_3"]["passed"]
    assert got["passed"] is False


def test_control_3_reads_only_the_control_truths_at_seed_1() -> None:
    spec = stage2_spec(TEN_IDS, TEN_IDS[:5])
    rows = stage2_readings(spec=spec)
    assert read_pass(rows, spec=spec)["control_3"]["truths"] == 5


def test_a_truth_control_1_drops_is_read_by_neither_control() -> None:
    """dev-000's B-lib beats ORACLE: gonogo's rule drops it before anything is read."""

    def lib(truth: str, seed: int) -> float:
        return -0.75 if truth == "dev-000" else -1.5 - 0.0625 * seed

    got = read_pass(stage2_readings(), p3_rows(lib=lib))
    assert got["retained"] == 9
    assert got["control_2"]["wins"] == 9
    assert got["control_3"]["truths"] == 9


def test_a_minus_inf_planted_unit_ranks_lowest() -> None:
    rows = swap(
        stage2_readings(),
        reading(bt.PLANTED, "dev-000", 1, -math.inf, "minus_inf", ()),
    )
    got = read_pass(rows)["control_2"]
    assert got["losses"] == 1 and got["wins"] == 9


@pytest.mark.parametrize("why", ["cpu_limit", "other_failure", "no_certified_fit"])
def test_a_unit_with_no_score_reads_as_minus_inf_and_never_helps(why: str) -> None:
    rows = swap(stage2_readings(), reading(bt.PLANTED, "dev-000", 1, math.nan, why, ()))
    got = read_pass(rows)
    assert got["complete"] and got["control_2"]["losses"] == 1


def test_a_pass_missing_a_unit_cannot_pass() -> None:
    rows = stage2_readings()[1:]
    got = read_pass(rows)
    assert got["complete"] is False and got["passed"] is False


def test_a_pass_with_no_rows_cannot_pass_and_does_not_raise() -> None:
    got = read_pass([])
    assert got["complete"] is False and got["passed"] is False


def test_the_margin_is_the_candidates_b_sym_over_the_stored_default() -> None:
    assert read_pass(stage2_readings())["margin"]["mean"] == -1.0625 - -1.125


def test_the_margin_takes_the_floor_on_a_world_with_no_finite_candidate_row() -> None:
    rows = swap(
        stage2_readings(),
        reading(B_SYM, "dev-000", 1, -math.inf, "minus_inf", ()),
    )
    margin = read_pass(rows)["margin"]
    assert margin["floored"] == 1
    assert math.isfinite(margin["mean"])


def test_the_read_repeats_and_does_not_depend_on_the_order_of_rows() -> None:
    rows = stage2_readings()
    assert read_pass(rows) == read_pass(list(reversed(rows)))


def test_each_pass_draws_its_own_stream() -> None:
    spec = stage2_spec()
    one = bt.stage2_rng(spec.root, 0).integers(1 << 62, size=4).tolist()
    two = bt.stage2_rng(spec.root, 1).integers(1 << 62, size=4).tolist()
    want = np.random.default_rng(
        np.random.SeedSequence([TEST_ROOT, bt.STAGE2_STREAM])
    ).integers(1 << 62, size=4)
    assert one == want.tolist() and one != two
    assert bt.SECONDARY_STREAM + len(bt.SELECTABLE) <= bt.STAGE2_STREAM


# --------------------------------------------------------------------------
# Stage 2: the table exported once, and its pin
# --------------------------------------------------------------------------


def toy_table_json(path: Path) -> str:
    body = bt.p3_table_bytes(
        [
            {"truth": "dev-000", "seed": 1, "system": bt.ORACLE, "per_event": -1.0},
            {
                "truth": "dev-000",
                "seed": 1,
                "system": bt.B_RAND,
                "per_event": -math.inf,
            },
            {"truth": "dev-000", "seed": 2, "system": bt.B_RAND, "per_event": None},
        ]
    )
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_the_table_loads_under_its_pin_with_minus_inf_and_failures(
    tmp_path: Path,
) -> None:
    path = tmp_path / "p3.json"
    table = bt.load_p3_table(path, toy_table_json(path))
    assert table.rows[("dev-000", 1, bt.ORACLE)] == -1.0
    assert table.rows[("dev-000", 1, bt.B_RAND)] == -math.inf
    assert table.rows[("dev-000", 2, bt.B_RAND)] is None


def test_the_table_is_refused_off_its_pin(tmp_path: Path) -> None:
    path = tmp_path / "p3.json"
    toy_table_json(path)
    with pytest.raises(bt.GuardFailure, match="table"):
        bt.load_p3_table(path, "0" * 64)


def test_an_empty_pin_refuses_the_table(tmp_path: Path) -> None:
    path = tmp_path / "p3.json"
    toy_table_json(path)
    assert bt.P3_TABLE_SHA256 == ""
    with pytest.raises(bt.GuardFailure, match="not pinned"):
        bt.load_p3_table(path, bt.P3_TABLE_SHA256)


def test_a_missing_table_is_refused(tmp_path: Path) -> None:
    with pytest.raises(bt.GuardFailure, match="table"):
        bt.load_p3_table(tmp_path / "none.json", "0" * 64)


def test_the_export_reads_a_store_by_enumeration_and_holds_four_systems(
    tmp_path: Path,
) -> None:
    """Addresses moved with F5, so nothing here computes one."""
    _, _, plan = toy_plan((1,))
    with ResultStore(tmp_path) as store:
        for system, key, value in zip(
            (bt.ORACLE, bt.B_RAND, "B-np"),
            [u.key for u in plan[:3]],
            (-1.0, -math.inf, -2.0),
            strict=True,
        ):
            fields = dict(key.config.items()) | {"system": system, "truth": "dev-000"}
            store.put(
                replace(key, config=bt.FrozenDict[str, str](fields)),
                {"status": 1.0, "per_event": value},
                {"result": {}},
            )
    rows = bt.export_p3_rows(tmp_path)
    assert [(r["system"], r["per_event"]) for r in rows] == [
        (bt.B_RAND, -math.inf),
        (bt.ORACLE, -1.0),
    ]
    assert {(r["truth"], r["seed"]) for r in rows} == {("dev-000", plan[0].key.seed)}


def test_the_export_refuses_two_cells_for_one_row(tmp_path: Path) -> None:
    _, _, plan = toy_plan((1,))
    with ResultStore(tmp_path) as store:
        for u in plan[:2]:
            fields = dict(u.key.config.items()) | {
                "system": bt.ORACLE,
                "truth": "dev-000",
            }
            store.put(
                replace(
                    u.key,
                    config=bt.FrozenDict[str, str](fields | {"extra": u.setting}),
                ),
                {"status": 1.0, "per_event": -1.0},
                {"result": {}},
            )
    with pytest.raises(bt.GuardFailure, match="two cells"):
        bt.export_p3_rows(tmp_path)


def test_the_export_gives_a_failed_unit_no_reading(tmp_path: Path) -> None:
    _, _, plan = toy_plan((1,))
    key = plan[0].key
    fields = dict(key.config.items()) | {"system": bt.B_RAND, "truth": "dev-000"}
    with ResultStore(tmp_path) as store:
        store.put(
            replace(key, config=bt.FrozenDict[str, str](fields)),
            {"status": 0.0},
            {"result": {"error": "ValueError"}},
        )
    assert bt.export_p3_rows(tmp_path)[0]["per_event"] is None


# --------------------------------------------------------------------------
# Stage 2: the run
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemStub:
    """A stage 2 unit: its reading by system, the candidate read by ``behaviour``."""

    id: str
    setting: str
    system: str
    truth: str
    seed: int
    value: float

    def run(self, sink: Any) -> None:
        result = UnitResult(
            self.system,
            True,
            {"status": 1.0, "has_structure": 1.0, "per_event": self.value},
            {"curve": [], "submitted": "m"},
            0.0,
        )
        sink.record(
            "unit", bt.unit_payload(self.setting, self.truth, self.seed, result)
        )


def plan_for(values: Mapping[str, float]) -> Callable[[str], tuple[Any, ...]]:
    """``values``: each candidate's planted reading; -1 passes, -1.25 fails."""
    spec = stage2_spec()
    entries = dev_entries(TEN_IDS)

    def plan(name: str) -> tuple[Any, ...]:
        out = []
        for u in bt.plan_stage2(name, spec, entries, environment()):
            value = values[name] if u.system == bt.PLANTED else -1.0
            job = SystemStub(u.job.id, name, u.system, u.truth, u.seed, value)
            out.append(replace(u, job=job))
        return tuple(out)

    return plan


def run2(
    tmp_path: Path,
    values: Mapping[str, float],
    selection: Mapping[str, Any] = RANKED,
    **driver: Any,
) -> Any:
    kwargs = FAST | driver
    spec = stage2_spec()
    log = kwargs.pop("log", lambda _line: None)
    return bt.run_stage2(
        spec,
        selection,
        plan_for(values),
        p3_rows(),
        dict.fromkeys(spec.truths, True),
        bt.Stage2Paths(tmp_path),
        IDENTITY,
        log=log,
        **kwargs,
    )


def result_of(tmp_path: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (tmp_path / "stage2.json").read_text(encoding="utf-8")
    )
    return body


def test_a_passing_winner_ends_stage_2_and_stands(tmp_path: Path) -> None:
    assert run2(tmp_path, {"c3": -1.0}) == "c3"
    body = result_of(tmp_path)
    assert [p["setting"] for p in body["passes"]] == ["c3"]
    assert body["final"] == "c3"
    assert bt.final_setting(RANKED, body) == "c3"


def test_a_failed_winner_hands_on_to_the_next_rank_once(tmp_path: Path) -> None:
    assert run2(tmp_path, {"c3": -1.25, "c7": -1.0}) == "c7"
    body = result_of(tmp_path)
    assert [(p["setting"], p["passed"]) for p in body["passes"]] == [
        ("c3", False),
        ("c7", True),
    ]
    assert bt.final_setting(RANKED, body) == "c7"


def test_both_failing_leaves_c0(tmp_path: Path) -> None:
    assert run2(tmp_path, {"c3": -1.25, "c7": -1.25}) == "c0"
    assert result_of(tmp_path)["final"] == "c0"


def test_no_candidate_above_c0_leaves_c0_after_one_pass(tmp_path: Path) -> None:
    only = RANKED | {"ranking": ["c3", "c0", "c1"]}
    assert run2(tmp_path, {"c3": -1.25}, only) == "c0"
    assert len(result_of(tmp_path)["passes"]) == 1


def test_the_two_passes_keep_their_own_stores(tmp_path: Path) -> None:
    run2(tmp_path, {"c3": -1.25, "c7": -1.0})
    for name in ("pass1-c3", "pass2-c7"):
        with ResultStore(tmp_path / name, read_only=True) as store:
            assert len(store.cells()) == 70


@pytest.mark.parametrize(
    "selection",
    [
        {"label": "NOT_SEPARATED", "winner": None},
        {"label": "SELECTED", "winner": "c0"},
        {"label": "SELECTED", "winner": "c0b"},
        {},
    ],
)
def test_stage_2_runs_only_on_a_selected_winner_that_is_not_c0(
    tmp_path: Path, selection: dict[str, Any]
) -> None:
    with pytest.raises(bt.GuardFailure, match="no stage 2"):
        run2(tmp_path, {"c3": -1.0}, selection)


def test_stage_2_is_refused_once_its_result_exists(tmp_path: Path) -> None:
    run2(tmp_path, {"c3": -1.0})
    with pytest.raises(bt.GuardFailure, match=r"stage2\.json"):
        run2(tmp_path, {"c3": -1.0})


def test_a_stage_2_stop_leaves_c0_and_starts_no_second_pass(tmp_path: Path) -> None:
    (tmp_path / "STOP").write_text("", encoding="utf-8")
    assert run2(tmp_path, {"c3": -1.0, "c7": -1.0}) == "c0"
    body = result_of(tmp_path)
    assert len(body["passes"]) == 1
    assert body["passes"][0]["complete"] is False
    assert body["end"] == "stop"


def test_the_cpu_stop_spans_both_passes_and_counts_each_unit_once(
    tmp_path: Path,
) -> None:
    """The kernel's count for one unit only grows, so pass 2 reads it from where
    pass 1 ended, and the shared ledger holds the unit's count once."""
    clock = {"usec": 0}

    def usage() -> int:
        clock["usec"] += 1000
        return clock["usec"]

    assert run2(tmp_path, {"c3": -1.25, "c7": -1.0}, read_usage_usec=usage) == "c7"
    total = bt.CpuLedger.load(tmp_path / "cpu.json").total_usec
    assert total <= clock["usec"]


def test_a_stage_2_resume_reads_the_finished_pass_again_and_goes_on(
    tmp_path: Path,
) -> None:
    """A kill between passes: the first pass re-reads from its store; nothing reruns."""
    paths = bt.Stage2Paths(tmp_path)
    spec = stage2_spec()
    halves = dict.fromkeys(spec.truths, True)
    values = {"c3": -1.25, "c7": -1.0}

    def stop_before_pass_2(name: str) -> tuple[Any, ...]:
        if name == "c7":
            raise KeyboardInterrupt
        return plan_for(values)(name)

    with pytest.raises(KeyboardInterrupt):
        bt.run_stage2(
            spec,
            RANKED,
            stop_before_pass_2,
            p3_rows(),
            halves,
            paths,
            IDENTITY,
            log=lambda _line: None,
            **FAST,
        )
    assert run2(tmp_path, values) == "c7"
    records = bt.read_records(tmp_path / "pass1-c3" / "journal.jsonl")
    assert sum(r["type"] == "unit" for r in records) == 70


def test_stage_2_prints_counts_and_cpu_until_its_result_exists(tmp_path: Path) -> None:
    lines: list[tuple[bool, str]] = []
    path = tmp_path / "stage2.json"

    def log(line: str) -> None:
        lines.append((path.exists(), line))

    run2(tmp_path, {"c3": -1.0}, log=log, print_s=0.0)
    before = [line for exists, line in lines if not exists]
    assert before and all(ALLOWED_LINE.fullmatch(line) for line in before)
    assert [line for exists, line in lines if exists] == ["stage 2: c3 stands"]


def test_a_stage_2_killed_run_writes_no_result(tmp_path: Path) -> None:
    """A SIGTERM leaves nothing to read: the same command resumes."""
    spec = stage2_spec()
    outcome = None
    paths = bt.Stage2Paths(tmp_path)

    def terminated(*args: Any, **kwargs: Any) -> str:
        return "terminated"

    patched = pytest.MonkeyPatch()
    patched.setattr(bt, "_run_pass", terminated)
    try:
        outcome = bt.run_stage2(
            spec,
            RANKED,
            plan_for({"c3": -1.0}),
            p3_rows(),
            dict.fromkeys(spec.truths, True),
            paths,
            IDENTITY,
            log=lambda _line: None,
            **FAST,
        )
    finally:
        patched.undo()
    assert outcome is None
    assert not (tmp_path / "stage2.json").exists()


# --------------------------------------------------------------------------
# Real units, seed 1 and a tiny budget
# --------------------------------------------------------------------------

REAL_FITS: Final = 3


def real_job(setting: str, fits: int = REAL_FITS) -> Any:
    truth = entry("hawkes")
    spec = toy_spec(("hawkes",), (1,), fits=fits, low=2)
    plan = bt.plan_stage1(spec, [truth], environment())
    return next(u for u in plan if u.setting == setting).job


@pytest.mark.slow
def test_a_real_unit_runs_by_the_frameworks_path_and_its_record_repeats(
    tmp_path: Path,
) -> None:
    job = real_job("c0")
    records: list[dict[str, Any]] = []

    class Capture:
        def record(self, type_: str, payload: Mapping[str, Any]) -> None:
            records.append({"type": type_, **payload})

    job.run(Capture())
    job.run(Capture())
    one, two = records
    assert one["type"] == "unit" and one["ok"] is True
    assert bt.dumps_unit(one) == bt.dumps_unit(two)
    direct = run_baseline_job(
        BaselineJob(entry("hawkes"), 1, (B_SYM,), job.env, job.config)
    )[0]
    assert one["reading"] == {k: v.hex() for k, v in direct.reading.items()}


@pytest.mark.slow
def test_the_shifted_path_equals_the_frameworks_at_the_frameworks_seed() -> None:
    """c0b cannot go through ``run_baseline_job`` (no seed argument), so its path is
    tied to it: at the framework's own seed the two give the same reading and detail."""
    job = real_job("c0")
    base = BaselineJob(entry("hawkes"), 1, (B_SYM,), job.env, job.config)
    want = run_baseline_job(base)[0]
    got = bt.run_shifted_unit(base, search_seed("hawkes", 1))
    assert got.ok and want.ok
    assert got.reading == want.reading
    assert got.detail == want.detail


def test_c0bs_job_holds_the_shifted_seed_and_no_other_job_holds_one() -> None:
    _, _, plan = toy_plan()
    for u in plan:
        want = bt.shifted_seed(u.truth, u.seed) if u.setting == "c0b" else None
        assert u.job.shifted == want


def test_the_shifted_path_searches_on_the_seed_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outcome cannot show this: two searches may well reach one best model."""
    seen: dict[str, Any] = {}

    class Spy:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

        def run(self, data: Any) -> Any:
            raise ValueError("stop here")

    monkeypatch.setattr(bt, "BSym", Spy)
    monkeypatch.setattr(bt, "observational_data", lambda *a: None)
    monkeypatch.setattr(bt, "held_out_dataset", lambda *a: None)
    job = real_job("c0b")
    base = BaselineJob(entry("hawkes"), 1, (B_SYM,), job.env, job.config)
    got = bt.run_shifted_unit(base, 12345)
    assert seen["seed"] == 12345 and seen["budget"] == REAL_FITS
    assert (got.ok, got.detail["error"]) == (False, "ValueError")


@pytest.mark.slow
def test_c0b_sees_c0s_data() -> None:
    c0 = real_job("c0", 5)
    records: dict[str, dict[str, Any]] = {}

    class Capture:
        def __init__(self, name: str) -> None:
            self.name = name

        def record(self, type_: str, payload: Mapping[str, Any]) -> None:
            records[self.name] = dict(payload)

    c0.run(Capture("c0"))
    real_job("c0b", 5).run(Capture("c0b"))
    assert records["c0"]["ok"] and records["c0b"]["ok"]
    one, two = records["c0"]["detail"], records["c0b"]["detail"]
    assert one["observational_events"] == two["observational_events"]
    assert one["held_out_events"] == two["held_out_events"]
    assert one["curve"][0] == two["curve"][0]  # the same library seed, the same data


@pytest.mark.slow
@pytest.mark.parametrize("system", ["planted-hint", "B-sym@10F"])
def test_a_real_stage_2_unit_runs_by_the_frameworks_path(system: str) -> None:
    spec = bt.Stage2Spec(
        truths=("hawkes",),
        seeds=(1,),
        control_truths=("hawkes",),
        fits=REAL_FITS,
        root=TEST_ROOT,
        resamples=10,
    )
    plan = bt.plan_stage2("c3", spec, [entry("hawkes")], environment())
    unit = next(u for u in plan if u.system == system)
    records: list[dict[str, Any]] = []

    class Capture:
        def record(self, type_: str, payload: Mapping[str, Any]) -> None:
            records.append(dict(payload))

    unit.job.run(Capture())
    assert records[0]["ok"] and records[0]["system"] == system
    got = bt.read_cell(
        system,
        "hawkes",
        1,
        {"reading": records[0]["reading"], "detail": {"result": records[0]["detail"]}},
    )
    assert got.why == "finite"


@pytest.mark.slow
def test_c11_fits_its_softplus_seed_inside_the_budget() -> None:
    job = real_job("c11", 4)
    records: list[dict[str, Any]] = []

    class Capture:
        def record(self, type_: str, payload: Mapping[str, Any]) -> None:
            records.append(dict(payload))

    job.run(Capture())
    assert records[0]["ok"]
    assert float.fromhex(records[0]["reading"]["fits_used"]) == 4.0


@pytest.mark.slow
def test_a_real_curve_ends_on_the_units_reading() -> None:
    """c0 at the full budget, read from its curve, is the unit's own reading."""
    job = real_job("c0")
    (result,) = run_baseline_job(
        BaselineJob(entry("hawkes"), 1, (B_SYM,), job.env, job.config)
    )
    curve = tuple((int(p[0]), float(p[2])) for p in result.detail["curve"])
    assert bt.curve_at(curve, REAL_FITS) == result.reading["per_event"]


@pytest.mark.slow
def test_a_spawned_unit_under_the_real_driver_reaches_the_store(tmp_path: Path) -> None:
    spec = toy_spec(("hawkes",), (1,), fits=2, low=1)
    plan = bt.plan_stage1(spec, [entry("hawkes")], environment())
    unit = next(u for u in plan if u.setting == "c0")
    driver = bt.Driver(
        bt.OutPaths(tmp_path),
        (unit.job,),
        stop_s=3600.0,
        cpu_limit_s=600,
        log=lambda _line: None,
        workers=1,
        poll_s=0.05,
        cpu_poll_s=0.0,
        read_usage_usec=lambda: 0,
        read_mem_gib=lambda: 16.0,
    )
    assert driver.run() == "complete"
    records = bt.read_records(tmp_path / "journal.jsonl")
    assert [r["job"] for r in records if r["type"] == "unit"] == [unit.job.id]


def test_the_frozen_miss_script_is_untouched() -> None:
    """The mechanism is copied, not imported, and not edited in place."""
    frozen = (REPO / "scripts/pilot_search_misses.py").read_bytes()
    assert hashlib.sha256(frozen).hexdigest() == FROZEN_MISS_SCRIPT_SHA256
    source = (REPO / "scripts/bsym_tune.py").read_text(encoding="utf-8")
    assert "import pilot_search_misses" not in source
