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
    toy_truth,
    world,
)

from sciagent.campaign import contrasts, secondaries
from sciagent.campaign.analysis import Session, Table
from sciagent.campaign.contrasts import (
    CONTROLS_FAILED,
    NAMING_NULL_FAILED,
    TOO_FEW_TRUTHS,
    ContrastRead,
    ControlsRead,
    FloorRead,
    ScriptedControls,
    read_contrasts,
    read_controls,
    same_read,
)
from sciagent.campaign.plan import P4_ARMS, P4_SONNET_ARMS
from sciagent.campaign.secondaries import (
    NAMED_MINUS_ANON,
    SECONDARY_STREAM,
    read_named_minus_anon,
    read_named_minus_anon_floors,
    read_scope,
    why_not,
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
    """A Haiku or Opus row sets no floor in another read: Haiku's failure cannot move
    Opus's read."""
    rows = full_world()
    score_all(rows, HAIKU, -2.0)
    score_all(rows, HAIKU_ANON, -2.0)
    score(rows, "t1", 1, OPUS, -math.inf)
    r = secondary(tabled(rows))["AG-c-O"]
    assert r.point == pytest.approx((-1 / 8 / 3) / len(TRUTHS))


def test_a_sonnet_row_can_set_another_models_floor() -> None:
    """Sonnet's arms are contrast rows, so a low Sonnet reading floors a Haiku row on
    its (truth, seed): a Haiku or Opus read depends on the Sonnet rows (red team
    2026-11-07 finding 11; the module docstring says so)."""
    rows = full_world()
    score(rows, "t1", 1, HAIKU, -math.inf)
    before = secondary(tabled(rows))["AG-c-H"].point
    score(rows, "t1", 1, AGC, -3.0)
    after = secondary(tabled(rows))["AG-c-H"].point
    assert after < before
    assert secondaries.__doc__ is not None
    assert "does not depend on which other models ran" not in secondaries.__doc__


def floors(table: Table, controls: ScriptedControls = PASS) -> Mapping[str, FloorRead]:
    return read_named_minus_anon_floors(table, controls, ROOT, 200)


def test_every_read_has_its_floored_count_and_the_read_without_them() -> None:
    """SPEC §6.2: the read with the floored rows left out, beside every read."""
    assert tuple(floors(tabled(full_world()))) == NAMES
    rows = full_world()
    for i, t in enumerate(TRUTHS):
        for seed in SEEDS:
            score(rows, t, seed, HAIKU, -0.625 + i / 16)
    score(rows, "t1", 1, HAIKU, -math.inf)
    f = floors(tabled(rows))["AG-c-H"]
    assert f.floored == 1
    assert f.no_common_seed == 0
    # Left out is the pair absent: the same as a table without the pair's two rows.
    without = dict(rows)
    del without[("t1", 1, HAIKU)]
    del without[("t1", 1, HAIKU_ANON)]
    assert same_read(f.without, secondary(tabled(without))["AG-c-H"])
    assert f.without.reportable
    assert not same_read(f.without, secondary(tabled(rows))["AG-c-H"])


def test_a_read_with_no_floored_row_is_its_own_left_out_read() -> None:
    rows = full_world()
    score(rows, "t1", 1, HAIKU, -math.inf)
    reads = secondary(tabled(rows))
    for name, f in floors(tabled(rows)).items():
        if name == "AG-c-H":
            continue
        assert f.floored == 0
        assert same_read(f.without, reads[name])


def test_the_left_out_read_has_the_same_gates() -> None:
    rows = full_world()
    score(rows, "t1", 1, HAIKU, -math.inf)
    for f in floors(tabled(rows), FAIL_NAMING).values():
        assert not f.without.reportable
        assert math.isnan(f.without.point)


def test_why_not_gives_every_reason_in_order() -> None:
    passed = ControlsRead(("t1", "t2"), (), (), True, True)
    assert why_not(passed, 2) == ()
    assert why_not(passed, 1) == (TOO_FEW_TRUTHS,)
    failed_all = ControlsRead((), (), (), False, False)
    assert why_not(failed_all, 0) == (
        CONTROLS_FAILED,
        NAMING_NULL_FAILED,
        TOO_FEW_TRUTHS,
    )


def test_why_not_is_the_rule_that_sets_reportable() -> None:
    """The reason the report prints and the gate come from one rule."""
    rows = full_world()
    for t in TRUTHS[1:]:
        for seed in SEEDS:
            del rows[(t, seed, OPUS)]
    table = tabled(rows)
    for controls in (PASS, FAIL_NAMING):
        got = read_controls(table, controls, ROOT, 200)
        for r in secondary(table, controls).values():
            assert r.reportable == (why_not(got, r.n) == ())


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


def controls_read(
    left: tuple[str, ...], unchecked: tuple[str, ...] = ()
) -> ControlsRead:
    kept = tuple(t for t in FORTY if t not in {*left, *unchecked})
    return ControlsRead(kept, left, unchecked, True, True)


#: A split as P4's: 40 truths, the even ones in dictionary.
FORTY = tuple(f"t{k:02d}" for k in range(40))
OUT = frozenset(FORTY[1::2])
IN = tuple(FORTY[0::2])
SPLIT = {t: toy_truth(t, in_dictionary=t not in OUT) for t in FORTY}


def test_four_of_forty_leaving_is_inside_the_limit() -> None:
    scope = read_scope(controls_read(IN[:4]), SPLIT)
    assert (scope.left, scope.total) == (4, 40)
    assert scope.confirmatory


def test_five_of_forty_leaving_is_past_it() -> None:
    assert not read_scope(controls_read(IN[:5]), SPLIT).confirmatory


def test_unchecked_truths_count_as_leaving() -> None:
    scope = read_scope(controls_read(IN[:3], IN[3:5]), SPLIT)
    assert scope.left == 5
    assert not scope.confirmatory


def test_c4_has_its_own_limit_on_out_of_dictionary_truths() -> None:
    out = tuple(sorted(OUT))
    two = read_scope(controls_read(out[:2]), SPLIT)
    assert (two.left_out_of_dictionary, two.out_of_dictionary) == (2, 20)
    assert two.c4_confirmatory
    three = read_scope(controls_read(out[:3]), SPLIT)
    assert three.confirmatory
    assert not three.c4_confirmatory


def test_the_out_of_dictionary_truths_are_the_tables_own() -> None:
    """Three in-dictionary truths leaving do not touch C4's limit."""
    scope = read_scope(controls_read(IN[:3]), SPLIT)
    assert (scope.left_out_of_dictionary, scope.out_of_dictionary) == (0, 20)
    assert scope.c4_confirmatory


def test_the_reads_past_the_limit_take_c4_with_them() -> None:
    scope = read_scope(controls_read(IN[:5]), SPLIT)
    assert not scope.confirmatory
    assert not scope.c4_confirmatory


def test_an_empty_table_is_not_confirmatory() -> None:
    """A share of nothing speaks for no split (red team 2026-11-07 finding 12)."""
    scope = read_scope(ControlsRead((), (), (), False, False), {})
    assert scope.total == 0
    assert not scope.confirmatory
    assert not scope.c4_confirmatory


def test_c4_with_no_out_of_dictionary_truth_is_not_confirmatory() -> None:
    inside = {t: SPLIT[t] for t in IN}
    kept = ControlsRead(IN, (), (), True, True)
    scope = read_scope(kept, inside)
    assert scope.confirmatory
    assert not scope.c4_confirmatory


def test_the_total_is_the_tables_truths_and_not_a_sum() -> None:
    """A truth in kept and unchecked, as ``READ_UNCHECKED_TRUTHS`` would make it, is
    not a partition: refused rather than counted twice."""
    twice = ControlsRead(FORTY, (), IN[:4], True, True)
    with pytest.raises(ScoringError, match="partition"):
        read_scope(twice, SPLIT)


@pytest.mark.parametrize(
    "controls",
    [
        ControlsRead(FORTY, IN[:1], (), True, True),
        ControlsRead(FORTY[1:], (), (), True, True),
        ControlsRead((*FORTY, "t99"), (), (), True, True),
        ControlsRead(FORTY[1:], IN[:1], IN[:1], True, True),
    ],
    ids=["kept-and-dropped", "a-truth-in-none", "a-truth-outside", "dropped-unchecked"],
)
def test_kept_dropped_and_unchecked_must_partition_the_tables_truths(
    controls: ControlsRead,
) -> None:
    with pytest.raises(ScoringError, match="partition"):
        read_scope(controls, SPLIT)


def test_the_report_says_when_the_reads_are_not_confirmatory(
    report: ModuleType,
) -> None:
    lines = report.scope_lines([("stored", read_scope(controls_read(IN[:5]), SPLIT))])
    text = "\n".join(lines)
    assert "not confirmatory" in text
    assert "5 of 40" in text
    assert "the miss check's label does not change" in text


def test_the_report_says_when_only_c4_is_past_its_limit(report: ModuleType) -> None:
    out = tuple(sorted(OUT))
    scope = read_scope(controls_read(out[:3]), SPLIT)
    text = "\n".join(report.scope_lines([("stored", scope)]))
    assert "C4 is not confirmatory" in text
    assert "reads are not confirmatory" not in text


def test_the_report_gives_the_counts_when_inside_both_limits(
    report: ModuleType,
) -> None:
    scope = read_scope(controls_read(()), SPLIT)
    text = "\n".join(report.scope_lines([("stored", scope)]))
    assert "0 of 40" in text
    assert "0 of 20" in text
    assert "not confirmatory" not in text


def test_the_report_gives_each_tables_scope(report: ModuleType) -> None:
    """Finding 3(c): the stored and the corrected table each get the limit."""
    stored = read_scope(controls_read(()), SPLIT)
    fixed = read_scope(controls_read(IN[:5]), SPLIT)
    text = "\n".join(report.scope_lines([("stored", stored), ("fixed", fixed)]))
    assert "stored table: 0 of 40" in text
    assert "fixed table: 5 of 40" in text
    assert "On the fixed table the reads are not confirmatory" in text
    assert "On the stored table the reads are not confirmatory" not in text


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
    text = "\n".join(report.named_minus_anon_lines(tabled(rows), True))
    assert "exploratory" in text
    assert "not covered by the miss check" in text
    assert "mixes names and time" in text
    for name in NAMES:
        assert f"| {name} |" in text
    no_floor = "| 0 | 6 | 0.0625 | 0.0625 | 0.0625 |"
    assert f"| AG-c-H | 6 | 0.0625 | 0.0625 | 0.0625 {no_floor}" in text


def test_the_report_takes_no_root_or_resamples(report: ModuleType) -> None:
    """Finding 2: the secondary draws from the miss script's frozen root and
    resamples, which no caller of the report can type."""
    params = inspect.signature(report.named_minus_anon_lines).parameters
    assert list(params) == ["tab", "naming_null"]


def test_the_report_prints_the_floored_count_and_the_read_without_them(
    report: ModuleType,
) -> None:
    rows = full_world()
    score(rows, "t1", 1, HAIKU, -math.inf)
    text = "\n".join(report.named_minus_anon_lines(tabled(rows), True))
    assert "with the floored rows left out" in text
    line = next(x for x in text.splitlines() if x.startswith("| AG-c-H |"))
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert cells[5] == "1"
    assert cells[6:] == ["6", "0.0000", "0.0000", "0.0000"]


def test_the_report_says_why_a_read_is_not_reportable(report: ModuleType) -> None:
    lines = report.named_minus_anon_lines(tabled(full_world()), False)
    why = f"not reportable: {NAMING_NULL_FAILED}"
    assert f"| AG-c-H | 6 | {why} | - | - | 0 | 6 | {why} | - | - |" in lines


def test_the_report_gives_every_reason(report: ModuleType) -> None:
    rows = full_world()
    score_all(rows, "planted-hint", -0.75)
    for t in TRUTHS[1:]:
        for seed in SEEDS:
            del rows[(t, seed, OPUS)]
    lines = report.named_minus_anon_lines(tabled(rows), False)
    line = next(x for x in lines if x.startswith("| AG-c-O |"))
    assert f"{CONTROLS_FAILED}; {NAMING_NULL_FAILED}; {TOO_FEW_TRUTHS}" in line
