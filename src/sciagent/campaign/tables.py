"""Tables of several configs merged into one, and the budget sweep's rows.

An instrument (SPEC §6.3): the gaps these tables carry are what a report prints.

**Why a merge.** :func:`~sciagent.campaign.analysis.load_rows` reads one config, and
one config of :func:`~sciagent.campaign.p4plan.p4_plan` holds one model letter and
one budget. Only the Sonnet config runs the baselines, so a table of the Haiku, the
Opus or a sweep config has no ORACLE and no B-lib, and no gap. :func:`merge_tables`
puts the configs' tables back together and runs one
:func:`~sciagent.campaign.analysis.derive` over every row, so each row's gap is
taken against the ORACLE and B-lib of its own (truth, seed), and the noise flag of a
gap closed reads the references on every seed they have. A narrowed set of
references would lose it.

**What it refuses**, with :class:`TableMergeError` and nothing merged:

- no source;
- sources whose code fingerprints differ, which includes one root given twice with
  two fingerprints;
- sources from two roots under one fingerprint, unless the caller says they are
  one store (``one_store``): two stores that share a fingerprint are still two
  stores;
- tables of different truth sets: the ids, or any truth's digest (two splits can
  share their ids, and a digest hashes the record);
- a (truth, seed, system) in two rows, two sessions or two telling cells, in one
  table or across them;
- a row, a session or a telling cell of a truth the tables do not hold.

**The one-store limit.** The caller passes each table's store root and that
store's fingerprint, and the checks compare those: one root and one fingerprint.
Configs read from one store share both, so the checks always pass for them: they
cannot tell two configs of one store apart, and cells that a ``reuse`` line
carried over from older code sit under the same root and fingerprint as the rest.
Only a store whose sidecar is a single ``establish`` line says that every cell came
from that code (:mod:`~sciagent.campaign.fingerprint`); the merge does not read
the sidecar, and ``one_store`` is the caller's word, not checked.

**Determinism.** Rows and sessions come out sorted by (truth, seed, system), the
truths by id and ``planned`` and ``missing`` by system, so the merge does not depend
on the order of its sources or of their rows.

**The budget sweep** (:func:`sweep_rows`; P4-PROPOSAL decision 15). The sweep is
descriptive and exploratory, under SPEC §6.2's "everything else": it has no paired
read, no CI, no floor and no drop rule of its own. Its rows are, on the sweep's
worlds (:func:`~sciagent.campaign.p4plan.sweep_truths` at seed 1):

- each swept budget's arms, and B-sym@F=80;
- the (8, 40) point: the main grid's AG-c-S and AG-o-S named rows, so the E sweep
  has its E = 8 row and the F sweep its F = 40 row;
- B-sym at F = 40, beside B-sym@F=80;
- the references, ORACLE and B-lib (:data:`SWEEP_REFERENCES`).

The gaps are :func:`~sciagent.campaign.analysis.derive`'s on the whole given table,
recomputed here, so a stale table cannot pass. The noise flag reads the references
on every seed they have, so the view refuses a table in which ORACLE or B-lib lacks
a row on a seed of the Sonnet config (P4's seeds 1 and 2) on any sweep truth: a
narrowed set would lose the flag silently. A swept arm or B-sym@F=80 off those
worlds does not pair and is refused, and so is a sweep world with no ORACLE row.

**The controls gate.** The sweep has no read of its own to gate, but its gaps are
referenced to ORACLE, so SPEC §6.4 holds for it: :attr:`SweepRows.controls_pass` is
controls 1 to 3 on the whole given table, as
:func:`~sciagent.campaign.contrasts.read_controls` reads them for the reads' gate at
the caller's ``seed`` and ``resamples`` (the reads' own, for P4). The view carries
the verdict rather than refusing on it, so a caller prints it beside the rows (red
team round 2, finding 5); control 4 (the naming null) is not carried. A row of the
view's systems off
the worlds is not shown and is counted (:attr:`SweepRows.narrowed`), as is each world
with no row of a system (:attr:`SweepRows.absent`). A failed or unsubmitted row stays
with no gap, as in every other table. No address and no version constant moves: this
reads rows and writes none.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Session, Table, TellingCell
from sciagent.campaign.contrasts import (
    AG_C_NAMED,
    AG_O_NAMED,
    RowKey,
    ScriptedControls,
    read_controls,
)
from sciagent.campaign.p4plan import P4_BLOCKS, P4_SINGLE_SEED, p4_plan, sweep_truths
from sciagent.campaign.plan import B_LIB, B_SYM, ORACLE, CampaignError

#: The gap references the sweep's rows carry.
SWEEP_REFERENCES: Final = (ORACLE, B_LIB)
#: The (8, 40) point of the sweep: the main grid's named Sonnet arms.
SWEEP_CENTRE: Final = (AG_C_NAMED, AG_O_NAMED)
#: The P4 blocks that are swept budgets.
SWEEP_BLOCK_PREFIX: Final = "sweep-"
#: The P4 block whose config holds the references.
SONNET_BLOCK: Final = "sonnet"


class TableMergeError(CampaignError):
    """Tables that cannot be merged, or a sweep the rules do not cover."""


@dataclass(frozen=True)
class TableSource:
    """One config's table, the store root it was read from and that store's code
    fingerprint, as the caller read it (module docstring for the limit)."""

    table: Table
    root: Path
    fingerprint: str


def _row_key(r: Row) -> RowKey:
    return (r.truth, r.seed, r.system)


def _session_key(x: Session) -> RowKey:
    return (x.truth, x.seed, x.system)


def _unique[T](items: Iterable[T], key: Callable[[T], Hashable], what: str) -> list[T]:
    seen: set[Hashable] = set()
    out: list[T] = []
    for item in items:
        k = key(item)
        if k in seen:
            raise TableMergeError(f"{what} {k} is in the tables twice")
        seen.add(k)
        out.append(item)
    return out


def _truth_set(table: Table) -> dict[str, str]:
    return {t: entry.digest for t, entry in table.truths.items()}


def _summed(counts: Iterable[Mapping[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in counts:
        for name, n in c.items():
            out[name] = out.get(name, 0) + n
    return dict(sorted(out.items()))


def merge_tables(sources: Sequence[TableSource], *, one_store: bool = False) -> Table:
    """One table over ``sources``, each a table after ``derive``, with one ``derive``
    over every row (module docstring). ``one_store`` says that roots which differ
    are one store (a path and a link to it, say); without it they are refused."""
    if not sources:
        raise TableMergeError("no table to merge")
    pairs = sorted({(str(s.root), s.fingerprint) for s in sources})
    if len({fingerprint for _, fingerprint in pairs}) != 1:
        raise TableMergeError(
            f"the tables were read under different code fingerprints: {pairs}"
        )
    roots = sorted({root for root, _ in pairs})
    if len(roots) != 1 and not one_store:
        raise TableMergeError(
            f"the tables were read from {len(roots)} roots {roots}: two stores under "
            "one fingerprint are still two stores, unless the caller says they are one"
        )
    truths = _truth_set(sources[0].table)
    for s in sources[1:]:
        if _truth_set(s.table) != truths:
            raise TableMergeError(
                f"the table read from {s.root} holds another truth set: the ids or "
                f"a truth's digest differ"
            )
    rows = _unique((r for s in sources for r in s.table.rows), _row_key, "row")
    stray = sorted({r.truth for r in rows if r.truth not in truths})
    if stray:
        raise TableMergeError(f"rows of truths the tables do not hold: {stray}")
    sessions = _unique(
        (x for s in sources for x in s.table.sessions), _session_key, "session"
    )
    telling: dict[tuple[str, int], TellingCell] = {}
    for s in sources:
        for world, cell in s.table.telling.items():
            if world in telling:
                raise TableMergeError(f"telling cell {world} is in the tables twice")
            telling[world] = cell
    stray = sorted(
        {x.truth for x in sessions if x.truth not in truths}
        | {t for t, _ in telling if t not in truths}
    )
    if stray:
        raise TableMergeError(
            f"sessions or telling cells of truths the tables do not hold: {stray}"
        )
    return Table(
        dict(sorted(sources[0].table.truths.items())),
        tuple(sorted(analysis.derive(rows), key=_row_key)),
        tuple(sorted(sessions, key=_session_key)),
        _summed(s.table.planned for s in sources),
        _summed(s.table.missing for s in sources),
        dict(sorted(telling.items())),
    )


@dataclass(frozen=True)
class SweepRows:
    """The budget sweep's rows on its worlds (module docstring), sorted."""

    rows: tuple[Row, ...]
    #: Rows of the view's systems off the sweep's worlds, per system: not shown.
    narrowed: Mapping[str, int]
    #: Sweep worlds with no row of a system, per system.
    absent: Mapping[str, int]
    #: Controls 1 to 3 on the whole given table, as the reads' gate reads them
    #: (:func:`~sciagent.campaign.contrasts.read_controls`): a caller prints it
    #: beside the rows (module docstring).
    controls_pass: bool


def sweep_rows(table: Table, *, seed: int, resamples: int) -> SweepRows:
    """The sweep's rows of ``table``, a merge of P4's configs, and controls 1 to 3
    read on it at ``seed`` and ``resamples``: the reads' own root and resamples for
    the gate's verdict (module docstring)."""
    truths = tuple(table.truths.values())
    plan = p4_plan(truths)
    swept: set[str] = set()
    for name, config in zip(P4_BLOCKS, plan, strict=True):
        if name.startswith(SWEEP_BLOCK_PREFIX):
            swept |= {arm.system(config.llm_letter) for arm in config.llm_arms}
            swept |= set(config.baselines)
    shown = frozenset({*swept, *SWEEP_CENTRE, B_SYM, *SWEEP_REFERENCES})
    worlds = {(t, seed) for t in sweep_truths(truths) for seed in P4_SINGLE_SEED}
    derived = analysis.derive(_unique(table.rows, _row_key, "row"))
    unpaired = sorted(
        _row_key(r)
        for r in derived
        if r.system in swept and (r.truth, r.seed) not in worlds
    )
    if unpaired:
        raise TableMergeError(
            f"sweep rows on worlds that do not pair with the sweep's: {unpaired}"
        )
    oracles = {(r.truth, r.seed) for r in derived if r.system == ORACLE}
    no_oracle = sorted(worlds - oracles)
    if no_oracle:
        raise TableMergeError(f"no ORACLE row on the sweep's worlds {no_oracle}")
    # The noise flag reads the references on every seed they have: on the sweep's
    # truths they must have a row on every seed of the Sonnet config (finding 5).
    sonnet = plan[P4_BLOCKS.index(SONNET_BLOCK)]
    held = {_row_key(r) for r in derived}
    narrowed_refs = sorted(
        (t, s, ref)
        for t in sweep_truths(truths)
        for s in sonnet.seeds
        for ref in SWEEP_REFERENCES
        if (t, s, ref) not in held
    )
    if narrowed_refs:
        raise TableMergeError(
            f"the references do not cover the Sonnet config's seeds {sonnet.seeds} on "
            f"the sweep's truths: no row of {narrowed_refs}"
        )
    rows: list[Row] = []
    narrowed: dict[str, int] = {}
    for r in derived:
        if r.system not in shown:
            continue
        if (r.truth, r.seed) in worlds:
            rows.append(r)
        else:
            narrowed[r.system] = narrowed.get(r.system, 0) + 1
    present: dict[str, int] = {}
    for r in rows:
        present[r.system] = present.get(r.system, 0) + 1
    absent = {
        s: len(worlds) - present.get(s, 0)
        for s in sorted(shown)
        if present.get(s, 0) != len(worlds)
    }
    # Controls 1 to 3 read no naming null; control 4 is not carried.
    controls = read_controls(
        replace(table, rows=tuple(derived)),
        ScriptedControls(naming_null=False),
        seed,
        resamples,
    )
    return SweepRows(
        tuple(sorted(rows, key=_row_key)),
        dict(sorted(narrowed.items())),
        absent,
        controls.controls_pass,
    )
