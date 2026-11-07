"""A failed unit ranks as ``-inf`` in controls 2 and 3 (control room ruling R3,
INBOX 2026-11-07; red team finding 3 on SPEC part C). An instrument, so these tests
came first (SPEC §6.3): a unit that crashed left the control, so a crash could pass a
control that a ``-inf`` fails. Toy tables only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest
from test_campaign_analysis import NAMES, _table
from test_contrasts import (
    B_SYM_10F,
    FIT_FAILURE,
    SEEDS,
    TRUTHS,
    failed,
    read,
    score,
    tabled,
    world,
)

from sciagent.campaign.analysis import (
    Row,
    Table,
    Verdict,
    control_rows,
    derive,
    verdict,
)

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


def test_a_failed_b_rand_unit_ranks_lowest_too() -> None:
    gone = read_verdict(changed("B-rand", NAMES[:1], crashed))
    inf = read_verdict(changed("B-rand", NAMES[:1], at_minus_inf))
    assert gone.planted is not None and inf.planted is not None
    assert gone.planted.differences == inf.planted.differences


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


def test_one_failed_unit_of_many_does_not_fail_control_3() -> None:
    rows = world()
    failed(rows, (TRUTHS[0], 1, B_SYM_10F), FIT_FAILURE)
    for t in TRUTHS:
        for seed in SEEDS:
            score(rows, t, seed, "B-sym", -0.625)
    assert all(r.reportable for r in read(tabled(rows)).values())
