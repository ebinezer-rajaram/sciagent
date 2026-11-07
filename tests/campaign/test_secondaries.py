"""Named minus anon, per model (SPEC §6.2, control room B2): an instrument, so these
tests came first (SPEC §6.3). Toy tables only, on test_contrasts' toy world and its
root 11: nothing here opens a store.
"""

from __future__ import annotations

import importlib.util
import inspect
import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from test_contrasts import (
    AGC,
    AGC_ANON,
    AGC_RESCALED,
    AGO,
    AGO_ANON,
    FAIL_NAMING,
    HAIKU,
    ORACLE,
    PASS,
    ROOT,
    SEEDS,
    TRUTHS,
    World,
    _row,
    _void,
    failed,
    no_submission,
    read,
    score,
    score_all,
    tabled,
    world,
)

from sciagent.campaign import contrasts, secondaries
from sciagent.campaign.analysis import Session, Table
from sciagent.campaign.contrasts import ContrastRead, ScriptedControls, read_contrasts
from sciagent.campaign.plan import P4_ARMS, P4_SONNET_ARMS
from sciagent.campaign.secondaries import (
    NAMED_MINUS_ANON,
    SECONDARY_STREAM,
    read_named_minus_anon,
)
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.stats import paired_bootstrap_ci

NAMES = (
    "AG-c-H",
    "AG-c-O",
    "AG-c-S",
    "AG-o-H",
    "AG-o-O",
    "AG-o-S",
)
HAIKU_ANON = "AG-c-H/anon+link-neutral"
OPUS = "AG-c-O/named+link-neutral"
OPUS_ANON = "AG-c-O/anon+link-neutral"
HAIKU_OPEN = "AG-o-H/named+link-neutral"
HAIKU_OPEN_ANON = "AG-o-H/anon+link-neutral"
OPUS_OPEN = "AG-o-O/named+link-neutral"
OPUS_OPEN_ANON = "AG-o-O/anon+link-neutral"


def full_world() -> World:
    """The toy world with every model's named and anon rows, at -0.625."""
    rows = world()
    for system in (
        HAIKU,
        HAIKU_ANON,
        OPUS,
        OPUS_ANON,
        HAIKU_OPEN,
        HAIKU_OPEN_ANON,
        OPUS_OPEN,
        OPUS_OPEN_ANON,
    ):
        for t in TRUTHS:
            for seed in SEEDS:
                rows[(t, seed, system)] = _row(t, seed, system, -0.625)
    return rows


def secondary(
    table: Table, controls: ScriptedControls = PASS
) -> Mapping[str, ContrastRead]:
    return read_named_minus_anon(table, controls, ROOT, 200)


def test_there_is_one_read_per_tier_and_model_in_sorted_order() -> None:
    assert tuple(spec.name for spec in NAMED_MINUS_ANON) == NAMES
    assert tuple(secondary(tabled(full_world()))) == NAMES


def test_each_read_is_named_minus_anon_of_one_arm_on_one_model() -> None:
    by_name = {spec.name: spec for spec in NAMED_MINUS_ANON}
    assert (by_name["AG-c-S"].first, by_name["AG-c-S"].second) == (AGC, AGC_ANON)
    assert (by_name["AG-o-S"].first, by_name["AG-o-S"].second) == (AGO, AGO_ANON)
    assert (by_name["AG-c-H"].first, by_name["AG-c-H"].second) == (HAIKU, HAIKU_ANON)
    assert (by_name["AG-c-O"].first, by_name["AG-c-O"].second) == (OPUS, OPUS_ANON)


def test_the_arms_read_are_the_plans_arms() -> None:
    """The names are the ones ``LLMArm.system`` gives, on the plan's arms."""
    for letter, arms in (("S", P4_SONNET_ARMS), ("H", P4_ARMS), ("O", P4_ARMS)):
        systems = {arm.system(letter) for arm in arms}
        for spec in NAMED_MINUS_ANON:
            if spec.name.endswith(f"-{letter}"):
                assert {spec.first, spec.second} <= systems


def test_a_planted_gap_is_read_with_its_sign_on_a_mixed_model() -> None:
    rows = full_world()
    score_all(rows, HAIKU, -0.5625)
    r = secondary(tabled(rows))["AG-c-H"]
    assert r.reportable
    assert r.point == 1 / 16
    assert r.low == r.high == 1 / 16
    assert r.n == len(TRUTHS)
    assert secondary(tabled(rows))["AG-c-O"].point == 0.0


def test_the_ci_is_the_paired_bootstrap_on_the_reads_own_stream() -> None:
    rows = full_world()
    for i, t in enumerate(TRUTHS):
        score(rows, t, 1, OPUS, -0.625 + i / 16)
    index = [spec.name for spec in NAMED_MINUS_ANON].index("AG-c-O")
    r = secondary(tabled(rows))["AG-c-O"]
    first = [-0.125 + i / 48 for i in range(len(TRUTHS))]
    second = [-0.125] * len(TRUTHS)
    ci = paired_bootstrap_ci(
        first,
        second,
        np.random.default_rng(np.random.SeedSequence([ROOT, SECONDARY_STREAM + index])),
        resamples=200,
    )
    assert (r.point, r.low, r.high) == pytest.approx((ci.mean, ci.low, ci.high))
    assert r.low < r.high


def test_the_streams_are_clear_of_the_reads_and_control_2() -> None:
    streams = {SECONDARY_STREAM + i for i in range(len(NAMED_MINUS_ANON))}
    assert not streams & set(range(len(contrasts.READS)))
    assert contrasts.CONTROL_2_STREAM not in streams


def test_sonnets_read_is_the_sum_of_its_two_c2_reads() -> None:
    """Time plus names is named minus anon, on complete rows."""
    rows = full_world()
    score_all(rows, AGC, -0.5625)
    score_all(rows, AGC_RESCALED, -0.625)
    score_all(rows, AGC_ANON, -0.6875)
    c2 = read(tabled(rows))
    total = c2["C2-AG-c-time"].point + c2["C2-AG-c-names"].point
    assert secondary(tabled(rows))["AG-c-S"].point == total == 1 / 8


def test_the_primary_reads_do_not_move() -> None:
    """Rows only this module reads change no read of ``read_contrasts``."""
    plain = world()
    rows = full_world()
    score_all(rows, HAIKU, -9.0)
    score_all(rows, HAIKU_ANON, -9.0)
    assert read_contrasts(tabled(rows), PASS, ROOT, 200) == read_contrasts(
        tabled(plain), PASS, ROOT, 200
    )


def test_a_row_at_minus_inf_takes_the_floor_of_its_truth_and_seed() -> None:
    rows = full_world()
    score(rows, "t1", 1, HAIKU, -math.inf)
    r = secondary(tabled(rows))["AG-c-H"]
    # B-rand's -0.75 is the floor: the row's gap -1/4 against the anon side's -1/8.
    assert r.point == pytest.approx((-1 / 8 / 3) / len(TRUTHS))
    assert r.n == len(TRUTHS)


def test_no_submission_takes_the_floor_too() -> None:
    rows = full_world()
    no_submission(rows, ("t1", 1, OPUS))
    r = secondary(tabled(rows))["AG-c-O"]
    assert r.point == pytest.approx((-1 / 8 / 3) / len(TRUTHS))


def test_a_failed_fit_takes_the_floor_too() -> None:
    rows = full_world()
    failed(rows, ("t1", 1, OPUS), f"{contrasts.FAILED_FIT}: x")
    r = secondary(tabled(rows))["AG-c-O"]
    assert r.point == pytest.approx((-1 / 8 / 3) / len(TRUTHS))


def test_a_floor_comes_from_the_contrast_rows_and_the_reads_own_two() -> None:
    """Another model's rows set no floor: one model's failure cannot move another's
    read, and a read does not depend on which other models ran."""
    rows = full_world()
    score_all(rows, HAIKU, -2.0)
    score_all(rows, HAIKU_ANON, -2.0)
    score(rows, "t1", 1, OPUS, -math.inf)
    r = secondary(tabled(rows))["AG-c-O"]
    assert r.point == pytest.approx((-1 / 8 / 3) / len(TRUTHS))


def test_oracle_stays_unfloored_and_a_read_has_no_row_without_it() -> None:
    rows = full_world()
    no_submission(rows, ("t1", 1, ORACLE))
    r = secondary(tabled(rows))["AG-c-H"]
    assert ("t1", 1, HAIKU) not in r.rows
    assert ("t1", 2, HAIKU) in r.rows


def test_a_truth_with_no_common_seed_leaves_the_read() -> None:
    rows = full_world()
    for seed in SEEDS:
        del rows[("t1", seed, HAIKU_ANON)]
    assert secondary(tabled(rows))["AG-c-H"].n == len(TRUTHS) - 1


def test_each_side_averages_only_the_seeds_both_have() -> None:
    rows = full_world()
    del rows[("t1", 1, HAIKU_ANON)]
    score(rows, "t1", 1, HAIKU, 0.0)
    assert secondary(tabled(rows))["AG-c-H"].point == 0.0


def test_a_model_with_no_rows_reads_nothing_and_says_so() -> None:
    r = secondary(tabled(world()))["AG-o-O"]
    assert r.n == 0
    assert not r.reportable
    assert math.isnan(r.point)


def test_a_failed_control_makes_every_read_unreportable() -> None:
    rows = full_world()
    score_all(rows, "planted-hint", -0.75)
    for r in secondary(tabled(rows)).values():
        assert not r.reportable
        assert math.isnan(r.point) and math.isnan(r.low) and math.isnan(r.high)


def test_control_4_gates_every_read() -> None:
    for r in secondary(tabled(full_world()), FAIL_NAMING).values():
        assert not r.reportable
        assert math.isnan(r.point)


def test_too_few_truths_is_not_reportable() -> None:
    rows = full_world()
    for t in TRUTHS[1:]:
        for seed in SEEDS:
            del rows[(t, seed, OPUS)]
    r = secondary(tabled(rows))["AG-c-O"]
    assert r.n == 1
    assert not r.reportable


def test_a_dropped_truth_leaves_the_read() -> None:
    rows = full_world()
    score(rows, "t1", 1, "B-lib", -0.5)
    score(rows, "t1", 2, "B-lib", -0.5)
    score(rows, "t1", 3, "B-lib", -0.5)
    assert secondary(tabled(rows))["AG-c-H"].n == len(TRUTHS) - 1


def test_a_void_row_joins_no_pair() -> None:
    session = Session("t1", 1, HAIKU, {}, {}, _void(), "env", "digest")
    r = secondary(tabled(full_world(), (session,)))["AG-c-H"]
    assert ("t1", 1, HAIKU) not in r.rows
    assert ("t1", 2, HAIKU) in r.rows


def test_nan_on_a_side_is_refused() -> None:
    rows = full_world()
    score(rows, "t1", 1, HAIKU, math.nan)
    with pytest.raises(ScoringError):
        secondary(tabled(rows))


def test_plus_inf_on_a_side_is_refused() -> None:
    rows = full_world()
    score(rows, "t1", 1, OPUS_ANON, math.inf)
    with pytest.raises(ScoringError):
        secondary(tabled(rows))


def test_a_scored_row_with_no_reading_is_refused() -> None:
    rows = full_world()
    rows[("t1", 1, HAIKU)] = replace(rows[("t1", 1, HAIKU)], per_event=None)
    with pytest.raises(ScoringError):
        secondary(tabled(rows))


def test_the_module_writes_no_read_of_the_frozen_set() -> None:
    """A secondary: nothing here is in ``READS``, so the miss check's reads, its
    tier 1 and its labels do not see it."""
    assert not {spec.name for spec in NAMED_MINUS_ANON} & {
        spec.name for spec in contrasts.READS
    }
    assert "secondaries" not in inspect.getsource(contrasts)
    assert secondaries.__doc__ is not None
    assert "exploratory" in secondaries.__doc__


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` loaded in-process, with ``scripts/`` importable."""
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "pilot_report", scripts / "pilot_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_labels_it_and_prints_every_read(report: ModuleType) -> None:
    rows = full_world()
    score_all(rows, HAIKU, -0.5625)
    text = "\n".join(report.named_minus_anon_lines(tabled(rows), True, ROOT, 200))
    assert "exploratory" in text
    assert "not covered by the miss check" in text
    assert "mixes names and time" in text
    for name in NAMES:
        assert f"| {name} |" in text
    assert "| AG-c-H | 6 | 0.0625 | 0.0625 | 0.0625 |" in text


def test_the_report_says_why_a_read_is_not_reportable(report: ModuleType) -> None:
    lines = report.named_minus_anon_lines(tabled(full_world()), False, ROOT, 200)
    assert "| AG-c-H | 6 | not reportable | - | - |" in lines
