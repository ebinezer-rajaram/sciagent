"""A failed unit ranks against the control passing in controls 2 and 3 (control room
rulings R3 and "R3 direction", INBOX 2026-11-07; red team finding 3 on SPEC part C
and finding 1 on the analysis code): ``-inf`` for planted-hint and B-sym@10F, ``+inf``
for B-rand, control 2's comparator. An instrument, so these tests came first (SPEC
§6.3): a unit that crashed left the control, so a crash could pass a control that a
``-inf`` fails. Toy tables only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest
from test_campaign_analysis import NAMES, _table
from test_contrasts import (
    B_RAND,
    B_SYM_10F,
    FIT_FAILURE,
    PLANTED,
    SEEDS,
    TRUTHS,
    failed,
    read,
    score,
    score_all,
    tabled,
    world,
)

from sciagent.campaign.analysis import (
    Row,
    Table,
    Verdict,
    control_rows,
    derive,
    score_rows,
    verdict,
)
from sciagent.scoring.errors import ScoringError

type Change = Callable[[Row], Row]


def crashed(r: Row) -> Row:
    return replace(
        r,
        ok=False,
        submitted=False,
        per_event=None,
        error="CampaignError",
        message="boom",
    )


def no_submission(r: Row) -> Row:
    return replace(r, submitted=False, per_event=None, has_structure=False)


def at_minus_inf(r: Row) -> Row:
    return replace(r, per_event=-math.inf)


def changed(system: str, truths: tuple[str, ...], change: Change) -> Table:
    """The passing toy table with ``system``'s units on ``truths`` changed."""
    table = _table(0.0)
    rows = [
        change(r) if r.system == system and r.truth in truths else r for r in table.rows
    ]
    return replace(table, rows=tuple(derive(rows)))


def read_verdict(table: Table) -> Verdict:
    return verdict(table, np.random.default_rng(0), resamples=500)


MOST = tuple(NAMES[: len(NAMES) // 2 + 1])


def test_the_toy_table_passes_with_nothing_changed() -> None:
    v = read_verdict(_table(0.0))
    assert v.controls_pass
    assert v.bsym is not None and v.bsym.median_gap > -0.01


@pytest.mark.parametrize("change", [crashed, no_submission], ids=lambda c: c.__name__)
def test_control_3_reads_a_failed_unit_as_it_reads_minus_inf(change: Change) -> None:
    gone = read_verdict(changed("B-sym@10F", MOST, change))
    inf = read_verdict(changed("B-sym@10F", MOST, at_minus_inf))
    assert not inf.controls_pass
    assert not gone.controls_pass
    assert gone.bsym is not None and inf.bsym is not None
    assert gone.bsym.median_gap == inf.bsym.median_gap == -math.inf


@pytest.mark.parametrize("change", [crashed, no_submission], ids=lambda c: c.__name__)
def test_a_failed_planted_unit_ranks_lowest_in_control_2(change: Change) -> None:
    gone = read_verdict(changed("planted-hint", NAMES, change))
    inf = read_verdict(changed("planted-hint", NAMES, at_minus_inf))
    assert gone.planted is not None and inf.planted is not None
    assert not gone.planted.passed
    assert gone.planted.differences == inf.planted.differences


def difference_on(v: Verdict, truth: str) -> float:
    assert v.planted is not None
    return dict(zip(v.planted.truths, v.planted.differences, strict=True))[truth]


def test_a_failed_b_rand_unit_ranks_against_control_2() -> None:
    """Moved by the R3 direction ruling (INBOX 2026-11-07): B-rand is control 2's
    comparator, so its failed unit ranks against the control passing, as ``+inf``,
    where a scored ``-inf`` still ranks lowest. The two now differ."""
    gone = read_verdict(changed("B-rand", NAMES[:1], crashed))
    inf = read_verdict(changed("B-rand", NAMES[:1], at_minus_inf))
    assert difference_on(gone, NAMES[0]) == -math.inf
    assert difference_on(inf, NAMES[0]) == math.inf


def test_every_b_rand_unit_crashed_fails_control_2() -> None:
    """The red team's case (``/tmp/rt-s38/d1_controls.py`` (a)): planted-hint is
    worse than B-rand everywhere and every B-rand unit crashed. Under R3's first
    words control 2 passed at a median of ``+inf`` and every read was reportable."""
    rows = world()
    score_all(rows, PLANTED, -0.8)
    assert not read_verdict(tabled(rows)).controls_pass
    for t in TRUTHS:
        for seed in SEEDS:
            failed(rows, (t, seed, B_RAND), "boom")
    v = read_verdict(tabled(rows))
    assert not v.controls_pass
    assert v.planted is not None and not v.planted.passed
    assert all(d == -math.inf for d in v.planted.differences)
    assert not any(r.reportable for r in read(tabled(rows)).values())


def test_control_rows_marks_each_failed_unit_it_ranks() -> None:
    """Red team round 2, finding 8: gonogo reads ``+inf`` only on a row marked as a
    failed unit, and ``control_rows`` is what marks it; a scored row is not marked."""
    for system, gap in (("B-rand", math.inf), ("planted-hint", -math.inf)):
        table = changed(system, NAMES[:1], crashed)
        added = [
            r
            for r in control_rows(table.rows, table.truths)
            if (r.truth, r.system) == (NAMES[0], system)
        ]
        assert added and all(r.failed and r.gap == gap for r in added)
    scored = changed("B-rand", NAMES[:1], at_minus_inf)
    assert not any(r.failed for r in control_rows(scored.rows, scored.truths))


def test_a_scored_minus_inf_b_rand_row_is_read_as_before() -> None:
    """A genuinely scored ``-inf`` stays as scored: no row is added for it, and its
    truth's difference is ``+inf``, as before the ruling."""
    table = changed("B-rand", NAMES[:1], at_minus_inf)
    rows = [r for r in table.rows if not r.llm]
    assert control_rows(rows, table.truths) == score_rows(rows, table.truths)
    assert difference_on(read_verdict(table), NAMES[0]) == math.inf


def test_one_scored_minus_inf_and_one_crashed_b_rand_seed_is_plus_inf() -> None:
    """Mixed: the crash counts against the control, never NaN."""
    table = _table(0.0)

    def mixed(r: Row) -> Row:
        if r.system != "B-rand" or r.truth != NAMES[0]:
            return r
        return at_minus_inf(r) if r.seed == 1 else crashed(r) if r.seed == 2 else r

    table = replace(table, rows=tuple(derive([mixed(r) for r in table.rows])))
    gaps = {
        r.seed: r.gap
        for r in control_rows(table.rows, table.truths)
        if (r.truth, r.system) == (NAMES[0], "B-rand")
    }
    assert gaps[1] == -math.inf and gaps[2] == math.inf
    v = read_verdict(table)
    assert difference_on(v, NAMES[0]) == -math.inf
    assert v.planted is not None and not any(map(math.isnan, v.planted.differences))


def test_a_failed_planted_unit_still_enters_at_minus_inf() -> None:
    table = changed("planted-hint", NAMES[:1], crashed)
    added = [
        r
        for r in control_rows(table.rows, table.truths)
        if (r.truth, r.system) == (NAMES[0], "planted-hint")
    ]
    assert added and all(r.gap == -math.inf for r in added)
    assert difference_on(read_verdict(table), NAMES[0]) == -math.inf


def test_a_row_with_a_reading_and_no_oracle_is_not_a_failed_unit() -> None:
    """Its gap is missing because ORACLE's is: the system did nothing wrong."""
    table = changed("ORACLE", NAMES[:1], crashed)
    rows = [r for r in table.rows if r.truth == NAMES[0] and r.system == "B-sym@10F"]
    assert rows and all(r.per_event is not None and r.gap is None for r in rows)
    v = read_verdict(table)
    assert v.bsym is not None
    assert v.bsym.median_gap > -0.01


def ranked_lowest(table: Table) -> set[tuple[str, int, str]]:
    return {
        (r.truth, r.seed, r.system)
        for r in control_rows(table.rows, table.truths)
        if r.gap == -math.inf
    }


def test_control_rows_adds_no_row_for_a_reading_with_no_gap() -> None:
    assert not ranked_lowest(changed("ORACLE", NAMES[:1], crashed))


def test_control_rows_adds_a_row_for_controls_2_and_3_and_no_other() -> None:
    assert ranked_lowest(changed("B-sym@10F", NAMES[:1], crashed)) == {
        (NAMES[0], 1, "B-sym@10F")
    }
    for system in ("B-sym", "B-lib", "B-sparse", "B-np"):
        assert not ranked_lowest(changed(system, NAMES[:1], crashed))


def test_a_failed_unit_of_another_system_enters_no_control() -> None:
    v = read_verdict(changed("B-sym", NAMES, crashed))
    assert v.controls_pass


def test_the_contrasts_read_it_through_their_controls() -> None:
    """A crash on most of control 3's units makes every read unreportable."""
    rows = world()
    for t in TRUTHS[:4]:
        failed(rows, (t, 1, B_SYM_10F), FIT_FAILURE)
    reads = read(tabled(rows))
    assert not any(r.reportable for r in reads.values())


RANKED = ("planted-hint", "B-rand", "B-sym@10F")


@pytest.mark.parametrize("value", [math.nan, math.inf], ids=["nan", "plus_inf"])
@pytest.mark.parametrize("system", RANKED)
def test_a_nan_or_plus_inf_reading_of_a_ranked_system_is_refused(
    system: str, value: float
) -> None:
    """Red team finding 6 (2026-11-07): such a reading left the control, so NaN or
    ``+inf`` on most of B-sym@10F's units passed control 3 where ``-inf`` fails it."""
    table = changed(system, MOST, lambda r: replace(r, per_event=value))
    with pytest.raises(ScoringError, match=system):
        control_rows(table.rows, table.truths)
    with pytest.raises(ScoringError, match=system):
        read_verdict(table)


@pytest.mark.parametrize("value", [math.nan, math.inf], ids=["nan", "plus_inf"])
def test_a_nan_or_plus_inf_reading_outside_the_controls_is_not_refused_here(
    value: float,
) -> None:
    table = changed("B-sym", NAMES[:1], lambda r: replace(r, per_event=value))
    assert read_verdict(table).controls_pass


def test_a_minus_inf_reading_of_a_ranked_system_is_still_read() -> None:
    table = changed("B-sym@10F", MOST, at_minus_inf)
    assert not read_verdict(table).controls_pass


def test_one_failed_unit_of_many_does_not_fail_control_3() -> None:
    rows = world()
    failed(rows, (TRUTHS[0], 1, B_SYM_10F), FIT_FAILURE)
    for t in TRUTHS:
        for seed in SEEDS:
            score(rows, t, seed, "B-sym", -0.625)
    assert all(r.reportable for r in read(tabled(rows)).values())
