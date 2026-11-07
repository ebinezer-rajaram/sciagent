"""Tables of several configs merged, and the budget sweep's rows (an instrument: SPEC
§6.3, so these tests came first). Stand-in rows and truths only: no store opens.

The sweep config holds no ORACLE, so its gaps come from the Sonnet config's on the
same worlds; the Haiku and Opus configs hold none either, so named minus anon on
those models needs the same merge.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest
from campaign_support import entry
from test_campaign_analysis import row

from sciagent.campaign.analysis import Row, Session, Table, TellingCell, derive
from sciagent.campaign.p4plan import P4_TRUTHS, sweep_truths
from sciagent.campaign.plan import B_LIB, B_SYM, B_SYM_F80, ORACLE, TruthEntry
from sciagent.campaign.tables import (
    SWEEP_REFERENCES,
    TableMergeError,
    TableSource,
    merge_tables,
    sweep_rows,
)
from sciagent.campaign.voids import VoidStatus
from sciagent.core.errors import SciAgentError

STORE: Final = Path("/stand-in/p4")
OTHER_STORE: Final = Path("/stand-in/p4-sweep")
FINGERPRINT: Final = "commit=abc,tree=clean"
AGC: Final = "AG-c-S/named+link-neutral"
AGO: Final = "AG-o-S/named+link-neutral"
HAIKU: Final = "AG-c-H/named+link-neutral"
#: The sweep's ten arms, written out by hand (P4-PROPOSAL decision 15).
SWEEP_ARMS: Final = tuple(
    f"AG-{tier}-S/named+link-neutral+E={e},F={f}"
    for e, f in ((0, 40), (2, 40), (16, 40), (8, 20), (8, 80))
    for tier in ("c", "o")
)
F80_ARMS: Final = SWEEP_ARMS[-2:]


def standin(n: int = P4_TRUTHS) -> tuple[TruthEntry, ...]:
    """Distinct digests, half in the dictionary, interleaved as the sampler does."""
    return tuple(
        dataclasses.replace(
            entry("hawkes"),
            id=f"test-{k:03d}",
            digest=hashlib.sha256(f"test-{k:03d}".encode()).hexdigest(),
            in_dictionary=k % 2 == 0,
            split="test",
        )
        for k in range(n)
    )


TRUTHS: Final = standin()
SWEPT: Final = sweep_truths(TRUTHS)


def table(
    rows: Sequence[Row],
    *,
    truths: Sequence[TruthEntry] = TRUTHS,
    sessions: Sequence[Session] = (),
    planned: dict[str, int] | None = None,
    missing: dict[str, int] | None = None,
    telling: dict[tuple[str, int], TellingCell] | None = None,
) -> Table:
    """A table as ``load_rows`` gives it: each config's rows derived on their own."""
    return Table(
        {t.id: t for t in truths},
        tuple(derive(rows)),
        tuple(sessions),
        planned or {},
        missing or {},
        telling or {},
    )


def source(t: Table, root: Path = STORE, fingerprint: str = FINGERPRINT) -> TableSource:
    return TableSource(t, root, fingerprint)


def sonnet_rows(*, oracle: float = -0.70) -> list[Row]:
    """The Sonnet config on every world: references, B-sym and the (8, 40) point."""
    out: list[Row] = []
    for t in TRUTHS:
        for seed in (1, 2):
            out += [
                row(t.id, seed, ORACLE, oracle),
                row(t.id, seed, B_LIB, oracle - 0.05),
                row(t.id, seed, B_SYM, oracle - 0.02),
                row(t.id, seed, AGC, oracle - 0.01, llm=True),
                row(t.id, seed, AGO, oracle - 0.015, llm=True),
            ]
    return out


def sweep_config_rows(arms: Sequence[str], *, f80: bool = False) -> list[Row]:
    """A sweep config on its 20 truths at seed 1: its arms, and B-sym@F=80 at F = 80."""
    out: list[Row] = []
    for truth in SWEPT:
        out += [row(truth, 1, arm, -0.73, llm=True) for arm in arms]
        if f80:
            out.append(row(truth, 1, B_SYM_F80, -0.71))
    return out


def p4_sources() -> list[TableSource]:
    """Every config that holds a row of the sweep's view, as separate tables."""
    sweeps = [
        sweep_config_rows(SWEEP_ARMS[i : i + 2], f80=SWEEP_ARMS[i] in F80_ARMS)
        for i in range(0, len(SWEEP_ARMS), 2)
    ]
    haiku = [row(t.id, s, HAIKU, -0.8, llm=True) for t in TRUTHS for s in (1, 2)]
    return [source(table(rows)) for rows in (sonnet_rows(), haiku, *sweeps)]


def key(r: Row) -> tuple[str, int, str]:
    return (r.truth, r.seed, r.system)


def one(rows: Sequence[Row], truth: str, seed: int, system: str) -> Row:
    found = [r for r in rows if key(r) == (truth, seed, system)]
    assert len(found) == 1, found
    return found[0]


# --------------------------------------------------------------------------
# merge_tables
# --------------------------------------------------------------------------


def test_the_error_is_typed() -> None:
    assert issubclass(TableMergeError, SciAgentError)


def test_a_row_of_another_config_takes_its_gap_from_the_merged_oracle() -> None:
    alone = table(sweep_config_rows(SWEEP_ARMS[:2]))
    assert one(alone.rows, SWEPT[0], 1, SWEEP_ARMS[0]).gap is None
    merged = merge_tables([source(table(sonnet_rows())), source(alone)])
    got = one(merged.rows, SWEPT[0], 1, SWEEP_ARMS[0])
    assert got.gap == pytest.approx(-0.73 - -0.70, abs=1e-15)
    assert got.gap_closed == pytest.approx((-0.73 - -0.75) / 0.05, abs=1e-12)


def test_the_merge_is_one_derive_over_every_row() -> None:
    sources = p4_sources()
    merged = merge_tables(sources)
    every = [r for s in sources for r in s.table.rows]
    assert sorted(merged.rows, key=key) == sorted(derive(every), key=key)


def test_the_noise_flag_reads_the_references_on_every_seed() -> None:
    """ORACLE minus B-lib is 0.001 on seed 1 and 0.5 on seed 2: within the paired
    noise on seed 1. A merge that kept seed 1's references only would lose that."""
    refs = [
        row(SWEPT[0], 1, ORACLE, -0.700),
        row(SWEPT[0], 1, B_LIB, -0.701),
        row(SWEPT[0], 2, ORACLE, -0.200),
        row(SWEPT[0], 2, B_LIB, -0.700),
    ]
    arm = table([row(SWEPT[0], 1, SWEEP_ARMS[0], -0.7005, llm=True)])
    got = one(
        merge_tables([source(table(refs)), source(arm)]).rows,
        SWEPT[0],
        1,
        SWEEP_ARMS[0],
    )
    assert got.gap_closed == pytest.approx(0.5, abs=1e-9)
    assert got.closed_flagged
    narrowed = table([r for r in refs if r.seed == 1])
    lost = one(
        merge_tables([source(narrowed), source(arm)]).rows, SWEPT[0], 1, SWEEP_ARMS[0]
    )
    assert not lost.closed_flagged


def test_the_merge_does_not_depend_on_the_order_of_its_sources() -> None:
    sources = p4_sources()
    assert merge_tables(sources) == merge_tables(sources[::-1])
    assert merge_tables(sources) == merge_tables([*sources[1:], sources[0]])


def test_planned_and_missing_are_summed_per_system() -> None:
    a = table([], planned={ORACLE: 80, AGC: 80}, missing={ORACLE: 2})
    b = table([], planned={HAIKU: 80, AGC: 3}, missing={HAIKU: 5, ORACLE: 1})
    merged = merge_tables([source(a), source(b)])
    assert merged.planned == {AGC: 83, HAIKU: 80, ORACLE: 80}
    assert merged.missing == {HAIKU: 5, ORACLE: 3}
    assert list(merged.planned) == sorted(merged.planned)


def test_sessions_and_telling_cells_are_kept() -> None:
    status = VoidStatus(False, (), 0, False)
    s1 = Session(SWEPT[0], 1, AGC, {}, {}, status, "env", "d1")
    s2 = Session(SWEPT[0], 1, HAIKU, {}, {}, status, "env", "d2")
    cell = TellingCell("cross_kernel", ())
    merged = merge_tables(
        [
            source(table([], sessions=(s2,), telling={(SWEPT[0], 1): cell})),
            source(table([], sessions=(s1,))),
        ]
    )
    # Sorted by (truth, seed, system): "AG-c-H" before "AG-c-S".
    assert merged.sessions == (s2, s1)
    assert merged.telling == {(SWEPT[0], 1): cell}


def test_no_source_is_refused() -> None:
    with pytest.raises(TableMergeError):
        merge_tables([])


def test_an_oracle_from_another_code_fingerprint_is_refused() -> None:
    sweep = table(sweep_config_rows(SWEEP_ARMS[:2]))
    with pytest.raises(TableMergeError, match="fingerprint"):
        merge_tables(
            [
                source(table(sonnet_rows())),
                source(sweep, OTHER_STORE, "commit=def,tree=clean"),
            ]
        )


def test_one_root_given_two_fingerprints_is_refused() -> None:
    with pytest.raises(TableMergeError, match="fingerprint"):
        merge_tables(
            [
                source(table(sonnet_rows())),
                source(table([]), STORE, "commit=def,tree=clean"),
            ]
        )


def test_two_configs_of_one_store_pass_the_fingerprint_check_whatever_wrote_them() -> (
    None
):
    """The limit, pinned: the check compares the fingerprints it is given, one per
    root, so two configs of one store always agree. Cells a ``reuse`` line carried
    over from older code are under the same root and the same fingerprint, and the
    merge cannot see them."""
    sources = [source(table(sonnet_rows())), source(table(sweep_config_rows(F80_ARMS)))]
    assert {(s.root, s.fingerprint) for s in sources} == {(STORE, FINGERPRINT)}
    merged = merge_tables(sources)
    assert len(merged.rows) == sum(len(s.table.rows) for s in sources)


def test_another_truth_set_is_refused() -> None:
    other = (*TRUTHS[:-1], dataclasses.replace(TRUTHS[-1], id="test-999"))
    with pytest.raises(TableMergeError, match="truth"):
        merge_tables([source(table(sonnet_rows())), source(table([], truths=other))])


def test_the_same_ids_from_another_split_are_refused() -> None:
    """Two splits can share their ids: the digests tell them apart."""
    other = (*TRUTHS[:-1], dataclasses.replace(TRUTHS[-1], digest="0" * 64))
    with pytest.raises(TableMergeError, match="truth"):
        merge_tables([source(table(sonnet_rows())), source(table([], truths=other))])


def test_a_row_in_two_tables_is_refused() -> None:
    twice = table([row(SWEPT[0], 1, ORACLE, -0.7)])
    with pytest.raises(TableMergeError, match="twice"):
        merge_tables([source(table(sonnet_rows())), source(twice)])


def test_a_row_twice_in_one_table_is_refused() -> None:
    doubled = table([row(SWEPT[0], 1, ORACLE, -0.7), row(SWEPT[0], 1, ORACLE, -0.6)])
    with pytest.raises(TableMergeError, match="twice"):
        merge_tables([source(doubled)])


def test_a_session_in_two_tables_is_refused() -> None:
    status = VoidStatus(False, (), 0, False)
    s = Session(SWEPT[0], 1, AGC, {}, {}, status, "env", "d1")
    with pytest.raises(TableMergeError, match="twice"):
        merge_tables(
            [source(table([], sessions=(s,))), source(table([], sessions=(s,)))]
        )


def test_a_telling_cell_in_two_tables_is_refused() -> None:
    cell = {(SWEPT[0], 1): TellingCell("cross_kernel", ())}
    with pytest.raises(TableMergeError, match="twice"):
        merge_tables([source(table([], telling=cell)), source(table([], telling=cell))])


def test_a_row_of_a_truth_the_tables_do_not_hold_is_refused() -> None:
    stray = table([row("test-999", 1, ORACLE, -0.7)])
    with pytest.raises(TableMergeError, match="truth"):
        merge_tables([source(stray)])


# --------------------------------------------------------------------------
# sweep_rows: descriptive, exploratory (SPEC §6.2, "everything else")
# --------------------------------------------------------------------------


def test_the_view_holds_the_sweeps_systems_and_its_references() -> None:
    view = sweep_rows(merge_tables(p4_sources()))
    systems = {r.system for r in view.rows}
    assert systems == {*SWEEP_ARMS, AGC, AGO, B_SYM, B_SYM_F80, *SWEEP_REFERENCES}
    assert set(SWEEP_REFERENCES) == {ORACLE, B_LIB}
    assert HAIKU not in systems


def test_the_view_is_the_sweeps_worlds_only() -> None:
    view = sweep_rows(merge_tables(p4_sources()))
    assert {(r.truth, r.seed) for r in view.rows} == {(t, 1) for t in SWEPT}
    assert len(SWEPT) == 20 and tuple(f"test-{k:03d}" for k in range(20)) == SWEPT
    # Every system of the view has exactly one row on each world.
    assert len(view.rows) == 20 * (len(SWEEP_ARMS) + 4 + len(SWEEP_REFERENCES))
    assert view.absent == {}


def test_rows_outside_the_worlds_are_counted_not_silently_dropped() -> None:
    view = sweep_rows(merge_tables(p4_sources()))
    # Sonnet's systems: 80 worlds, of which the sweep's 20 are in the view.
    assert view.narrowed == {s: 60 for s in sorted((AGC, AGO, B_LIB, B_SYM, ORACLE))}


def test_the_view_takes_its_gaps_from_derive_on_the_whole_table() -> None:
    """Including the references on seed 2, so the noise flag survives narrowing."""
    rows = sonnet_rows()
    rows = [
        r for r in rows if not (r.truth == SWEPT[0] and r.system in (ORACLE, B_LIB))
    ]
    rows += [
        row(SWEPT[0], 1, ORACLE, -0.700),
        row(SWEPT[0], 1, B_LIB, -0.701),
        row(SWEPT[0], 2, ORACLE, -0.200),
        row(SWEPT[0], 2, B_LIB, -0.700),
    ]
    sources = p4_sources()
    sources[0] = source(table(rows))
    got = one(sweep_rows(merge_tables(sources)).rows, SWEPT[0], 1, SWEEP_ARMS[0])
    assert got.closed_flagged
    assert got.gap == pytest.approx(-0.73 - -0.70, abs=1e-15)


def test_stale_gaps_in_the_given_table_do_not_reach_the_view() -> None:
    merged = merge_tables(p4_sources())
    stale = dataclasses.replace(
        merged,
        rows=tuple(
            dataclasses.replace(r, gap=None, gap_closed=None) for r in merged.rows
        ),
    )
    assert sweep_rows(stale) == sweep_rows(merged)


def test_the_view_has_no_drop_rule() -> None:
    """A truth on which ORACLE does not beat B-lib stays: control 1's drop is the
    contrasts' rule, and the sweep is descriptive."""
    rows = [
        dataclasses.replace(r, per_event=-0.60)
        if r.truth == SWEPT[0] and r.system == B_LIB
        else r
        for r in sonnet_rows()
    ]
    sources = p4_sources()
    sources[0] = source(table(rows))
    view = sweep_rows(merge_tables(sources))
    assert one(view.rows, SWEPT[0], 1, SWEEP_ARMS[0]).gap is not None


def test_a_missing_sweep_row_is_counted_as_absent() -> None:
    sources = p4_sources()
    first = sources[2].table
    kept = [r for r in first.rows if key(r) != (SWEPT[3], 1, SWEEP_ARMS[0])]
    sources[2] = source(table(kept))
    view = sweep_rows(merge_tables(sources))
    assert view.absent == {SWEEP_ARMS[0]: 1}


def test_a_failed_or_unsubmitted_row_stays_with_no_gap() -> None:
    sources = p4_sources()
    rows = [
        dataclasses.replace(r, ok=False, submitted=False, per_event=None)
        if key(r) == (SWEPT[1], 1, SWEEP_ARMS[2])
        else r
        for r in sources[3].table.rows
    ]
    sources[3] = source(table(rows))
    got = one(sweep_rows(merge_tables(sources)).rows, SWEPT[1], 1, SWEEP_ARMS[2])
    assert got.gap is None and not got.ok


def test_a_missing_oracle_on_a_sweep_world_is_refused() -> None:
    sources = p4_sources()
    rows = [r for r in sonnet_rows() if key(r) != (SWEPT[5], 1, ORACLE)]
    sources[0] = source(table(rows))
    with pytest.raises(TableMergeError, match="ORACLE"):
        sweep_rows(merge_tables(sources))


def test_an_oracle_at_minus_inf_is_a_row_and_leaves_the_gaps_undefined() -> None:
    sources = p4_sources()
    rows = [
        dataclasses.replace(r, per_event=-math.inf)
        if key(r) == (SWEPT[5], 1, ORACLE)
        else r
        for r in sonnet_rows()
    ]
    sources[0] = source(table(rows))
    view = sweep_rows(merge_tables(sources))
    assert one(view.rows, SWEPT[5], 1, SWEEP_ARMS[0]).gap is None


@pytest.mark.parametrize("system", [SWEEP_ARMS[0], F80_ARMS[1], B_SYM_F80])
@pytest.mark.parametrize("world", [(SWEPT[0], 2), ("test-020", 1)])
def test_a_sweep_row_on_a_world_that_does_not_pair_is_refused(
    system: str, world: tuple[str, int]
) -> None:
    sources = p4_sources()
    stray = row(world[0], world[1], system, -0.73, llm=system != B_SYM_F80)
    sources.append(source(table([stray])))
    with pytest.raises(TableMergeError, match="sweep"):
        sweep_rows(merge_tables(sources))


def test_the_view_does_not_depend_on_row_order() -> None:
    merged = merge_tables(p4_sources())
    reordered = dataclasses.replace(merged, rows=merged.rows[::-1])
    assert sweep_rows(reordered) == sweep_rows(merged)
    view = sweep_rows(merged)
    assert list(view.rows) == sorted(view.rows, key=key)


def test_a_row_twice_in_the_given_table_is_refused() -> None:
    merged = merge_tables(p4_sources())
    doubled = dataclasses.replace(merged, rows=(*merged.rows, merged.rows[0]))
    with pytest.raises(TableMergeError, match="twice"):
        sweep_rows(doubled)
