"""P4's preregistered contrasts, C1 to C4 (SPEC §6.2; prereg 2026-11-05 §2.7).

An instrument (SPEC §6.3), and analysis code under SPEC §6.1. C5 and C3's
AG-S-boed half are P4b's (decision 12); C6 is exploratory and has no read.

**The reads** (:data:`READS`). Each is ``first`` minus ``second`` on the held-out
gap, paired by truth, with a percentile bootstrap 95% CI
(:func:`~sciagent.scoring.stats.paired_bootstrap_ci`):

==================  ================  ===========================
read                first             second
==================  ================  ===========================
``C1``              AG-c-S named      B-sym
``C2-AG-c-time``    AG-c-S named      AG-c-S rescaled
``C2-AG-c-names``   AG-c-S rescaled   AG-c-S anon
``C2-AG-o-time``    AG-o-S named      AG-o-S rescaled
``C2-AG-o-names``   AG-o-S rescaled   AG-o-S anon
``C3-free``         AG-o-S named      AG-c-S named
``C4``              AG-c-S named      B-sparse, out of dictionary
==================  ================  ===========================

The ``rescaled`` arms (Δ9) are :data:`~sciagent.campaign.plan.P4_RESCALED_ARMS`,
on Sonnet only, so only a plan whose Sonnet config runs
:data:`~sciagent.campaign.plan.P4_SONNET_ARMS` yields those rows. Without them
the four C2 reads have no rows, and a read with no rows is not reportable and is
named. A test holds the system names here to the arms'.

**Which rows** (:func:`contrast_rows`). Tier 1 of the miss check: every planned
unit of :data:`CONTRAST_SYSTEMS`, which is the arms above plus what §6.2 and §6.4
name (ORACLE, B-lib, B-sym, B-sparse, planted-hint, B-rand, B-sym@10F), over every
truth before any drop. A plan is the sequence of its
:class:`~sciagent.campaign.plan.CampaignConfig`\\ s, since one config holds one
model letter and one budget. A key has no budget field, so a key that two units
of the plan share is refused: a system at another budget must carry the budget
in its name. Reading no score and no cell, the function cannot depend on an
outcome.

**The fixed order** (:func:`read_contrasts`). Only contrast rows enter. A table
from :func:`~sciagent.campaign.analysis.load_rows` holds planned units only, so
the system rule is the same set as :func:`contrast_rows`, which needs the plan
that ``read_contrasts`` is not given.

1. Controls 1 to 3 and their drop rule read the table as given, unfloored
   (:func:`~sciagent.campaign.analysis.verdict`), with control 2's bootstrap on
   stream :data:`CONTROL_2_STREAM`. So an ORACLE at ``-inf`` still fails control 1,
   and control 2 and 3 still rank a ``-inf``. The truths kept are
   :attr:`Verdict.kept`: one the check could not reach (fewer than 2 paired seeds)
   is not kept, as it is not in any share of the verdict.
2. The pair rule floors a copy (:func:`floor_rows`).
3. :func:`~sciagent.campaign.analysis.derive` runs on the copy.
4. The reads.

**The pair rule.** A row that scored ``-inf``, has no submission, or failed with
:data:`FAILED_FIT` is a real outcome of its system, and takes the lowest finite
``per_event`` among the contrast rows of its (truth, seed). ORACLE and B-lib are
gap references and never take one. A void row, or a failure of any other cause,
is absent: load_rows already drops a void's row, and one that reaches the table
joins no pair and sets no floor. With no finite row on a (truth, seed), its rows
are absent. Within a truth both sides are averaged over the seeds where both have
a row, so a pair compares the same worlds, and a truth with no such seed leaves
the read and is counted (:attr:`FloorRead.no_common_seed`).

**Generators.** Read ``i`` of the sorted names draws from
``default_rng(SeedSequence([seed, i]))``, so a read's interval does not depend on
another read's rows. Truths enter sorted, and every fold is exactly rounded, so a
read does not depend on row order. A truth leaving a read makes a new resample
matrix, which moves an interval's end by itself.

**``rows``** is every row a read used, gap references included: both sides and the
ORACLE on each matched (truth, seed). B-lib is not read, so it is not in it.

**Reportable.** Controls 1, 2 and 3 gate every read and control 4 (the
:class:`ScriptedControls` value, a scripted run outside the table) gates the C2
reads. A read needs its gates passed and ``n``, its truths, at least 2. Otherwise
``point``, ``low`` and ``high`` are ``nan``, and ``n`` and ``rows`` still say what
the read had. Compare reads with :func:`same_read`, which goes by ``float.hex``, so
``nan`` equals ``nan``.

**S6** (:func:`read_floors`). For each read, how many of its rows took a floor,
and the read with those rows absent. It draws read ``i``'s own stream afresh, so
when ``n`` is equal the two intervals resample the same truths. It cannot change
a label.

A table the rules do not cover raises :class:`ScoringError` rather than guess: a
repeated key, an unknown truth, ``nan`` or ``+inf``, a scored row with no reading,
or rows that are not ``derive``'s output (their gaps would be stale). An exception
from here is an instrument fault, and gives ``BYTE_FAIL``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Table
from sciagent.campaign.plan import (
    B_LIB,
    B_RAND,
    B_SPARSE,
    B_SYM,
    B_SYM_10F,
    ORACLE,
    P4_PROMPT_VARIANT,
    PLANTED,
    CampaignConfig,
    CampaignError,
    TruthEntry,
    baseline_units,
    llm_units,
)
from sciagent.core import reductions
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.stats import paired_bootstrap_ci

#: (truth, seed, system). A budget other than the plan's main one is in the name.
type RowKey = tuple[str, int, str]

#: Control 2's bootstrap stream: ``SeedSequence([seed, 1000])``.
CONTROL_2_STREAM: Final = 1000
#: The failure that is an outcome of the system (``execute.run_score_job``).
FAILED_FIT: Final = "the submitted structure could not be fitted"
#: Gap references: never floored.
REFERENCES: Final = frozenset({ORACLE, B_LIB})
#: A read needs at least this many truths.
MIN_TRUTHS: Final = 2


def _sonnet(tier: str, condition: str) -> str:
    """A P4 Sonnet arm's system name, as ``LLMArm.system`` gives it."""
    return f"{tier}-S/{condition}+{P4_PROMPT_VARIANT}"


AG_C_NAMED: Final = _sonnet("AG-c", "named")
AG_C_RESCALED: Final = _sonnet("AG-c", "rescaled")
AG_C_ANON: Final = _sonnet("AG-c", "anon")
AG_O_NAMED: Final = _sonnet("AG-o", "named")
AG_O_RESCALED: Final = _sonnet("AG-o", "rescaled")
AG_O_ANON: Final = _sonnet("AG-o", "anon")

#: Every system whose rows are contrast rows (membership only: never iterated).
CONTRAST_SYSTEMS: Final = frozenset(
    {
        AG_C_NAMED,
        AG_C_RESCALED,
        AG_C_ANON,
        AG_O_NAMED,
        AG_O_RESCALED,
        AG_O_ANON,
        ORACLE,
        B_LIB,
        B_SYM,
        B_SPARSE,
        PLANTED,
        B_RAND,
        B_SYM_10F,
    }
)


@dataclass(frozen=True)
class ContrastSpec:
    """One read: ``first`` minus ``second`` on the held-out gap."""

    name: str
    first: str
    second: str
    #: Control 4, the naming null, gates it.
    naming: bool = False
    #: It reads the out-of-dictionary truths only.
    out_of_dictionary: bool = False


#: The reads, in sorted name order: read ``i`` draws stream ``i``.
READS: Final = tuple(
    sorted(
        (
            ContrastSpec("C1", AG_C_NAMED, B_SYM),
            ContrastSpec("C2-AG-c-time", AG_C_NAMED, AG_C_RESCALED, naming=True),
            ContrastSpec("C2-AG-c-names", AG_C_RESCALED, AG_C_ANON, naming=True),
            ContrastSpec("C2-AG-o-time", AG_O_NAMED, AG_O_RESCALED, naming=True),
            ContrastSpec("C2-AG-o-names", AG_O_RESCALED, AG_O_ANON, naming=True),
            ContrastSpec("C3-free", AG_O_NAMED, AG_C_NAMED),
            ContrastSpec("C4", AG_C_NAMED, B_SPARSE, out_of_dictionary=True),
        ),
        key=lambda spec: spec.name,
    )
)


@dataclass(frozen=True)
class ScriptedControls:
    """The pass states the table cannot give: control 4, a scripted run. It is
    the same value in the stored and the corrected call."""

    naming_null: bool


@dataclass(frozen=True)
class ContrastRead:
    """One read (module docstring). ``n`` counts truths."""

    point: float
    low: float
    high: float
    reportable: bool
    n: int
    rows: tuple[RowKey, ...]


@dataclass(frozen=True)
class FloorRead:
    """S6 for one read: its floored rows, its truths with no seed in common, and
    the read with its floored rows absent."""

    floored: int
    no_common_seed: int
    without: ContrastRead


def same_read(a: ContrastRead, b: ContrastRead) -> bool:
    """Equal in every field, every float by ``float.hex``."""
    return (
        a.point.hex() == b.point.hex()
        and a.low.hex() == b.low.hex()
        and a.high.hex() == b.high.hex()
        and a.reportable == b.reportable
        and a.n == b.n
        and a.rows == b.rows
    )


def contrast_rows(
    plan: Sequence[CampaignConfig], truths: Sequence[TruthEntry]
) -> tuple[RowKey, ...]:
    """Tier 1: every contrast row of ``plan``, sorted (module docstring)."""
    ids = [t.id for t in truths]
    seen: set[RowKey] = set()
    keys: list[RowKey] = []
    for config in plan:
        for unit in (*baseline_units(ids, config), *llm_units(ids, config)):
            key = (unit.truth_id, unit.seed, unit.system)
            if key in seen:
                raise CampaignError(
                    f"row key {key} appears twice in the plan: a key has no budget "
                    f"field, so a system at another budget must carry it in its name"
                )
            seen.add(key)
            if unit.system in CONTRAST_SYSTEMS:
                keys.append(key)
    return tuple(sorted(keys))


def _key(row: Row) -> RowKey:
    return (row.truth, row.seed, row.system)


def _takes_floor(row: Row) -> bool:
    """Whether a row is one of the pair rule's real outcomes (module docstring)."""
    if row.system in REFERENCES:
        return False
    if not row.ok:
        return (row.message or "").startswith(FAILED_FIT)
    return not row.submitted or row.per_event == -math.inf


def floor_rows(rows: Sequence[Row]) -> tuple[tuple[Row, ...], frozenset[RowKey]]:
    """The pair rule's copy of ``rows``, which must be contrast rows and no void,
    and the keys it floored. A row with no finite row on its (truth, seed) stays
    as it is: it has no gap, so it is absent."""
    lowest: dict[tuple[str, int], float] = {}
    for r in rows:
        if r.per_event is not None and math.isfinite(r.per_event):
            unit = (r.truth, r.seed)
            lowest[unit] = min(lowest.get(unit, math.inf), r.per_event)
    out: list[Row] = []
    floored: set[RowKey] = set()
    for r in rows:
        floor = lowest.get((r.truth, r.seed))
        if floor is not None and _takes_floor(r):
            out.append(replace(r, per_event=floor))
            floored.add(_key(r))
        else:
            out.append(r)
    return tuple(out), frozenset(floored)


def _checked_rows(table: Table) -> list[Row]:
    """The table's contrast rows, refusing what no rule covers (module docstring)."""
    seen: set[RowKey] = set()
    rows: list[Row] = []
    for r in table.rows:
        key = _key(r)
        if key in seen:
            raise ScoringError(f"row key {key} is in the table twice")
        seen.add(key)
        if r.truth not in table.truths:
            raise ScoringError(f"row {key} is of a truth the table does not hold")
        if r.system not in CONTRAST_SYSTEMS:
            continue
        if r.per_event is None:
            if r.ok and r.submitted:
                raise ScoringError(f"row {key} is scored and has no reading")
        elif math.isnan(r.per_event) or r.per_event == math.inf:
            raise ScoringError(f"row {key} has per_event {r.per_event}")
        rows.append(r)
    if analysis.derive(rows) != rows:
        raise ScoringError("the table's rows are not derive's output: stale gaps")
    return rows


@dataclass(frozen=True)
class _Frame:
    """What every read of one table reads, after steps 1 to 3."""

    #: Truths kept by control 1, sorted.
    kept: tuple[str, ...]
    out_of_dictionary: frozenset[str]
    #: Controls 1 to 3, and control 4.
    controls_pass: bool
    naming_null: bool
    #: Each (truth, system)'s finite gaps by seed.
    gaps: Mapping[tuple[str, str], Mapping[int, float]]
    floored: frozenset[RowKey]
    seed: int
    resamples: int


def _frame(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> _Frame:
    given = _checked_rows(table)
    verdict = analysis.verdict(
        replace(table, rows=tuple(given)),
        np.random.default_rng(np.random.SeedSequence([seed, CONTROL_2_STREAM])),
        resamples=resamples,
    )
    void = {(x.truth, x.seed, x.system) for x in table.sessions if x.void.void}
    copy, floored = floor_rows([r for r in given if _key(r) not in void])
    gaps: dict[tuple[str, str], dict[int, float]] = {}
    for r in analysis.derive(copy):
        if r.gap is not None and math.isfinite(r.gap):
            gaps.setdefault((r.truth, r.system), {})[r.seed] = r.gap
    return _Frame(
        kept=tuple(sorted(verdict.kept)),
        out_of_dictionary=frozenset(
            t for t, entry in table.truths.items() if not entry.in_dictionary
        ),
        controls_pass=verdict.controls_pass,
        naming_null=controls.naming_null,
        gaps=gaps,
        floored=floored,
        seed=seed,
        resamples=resamples,
    )


def _mean(values: Sequence[float]) -> float:
    return reductions.mean(np.asarray(values, dtype=np.float64))


def _read(
    index: int, spec: ContrastSpec, frame: _Frame, absent: frozenset[RowKey]
) -> tuple[ContrastRead, int]:
    """Read ``index`` with the ``absent`` rows left out, and the truths it lost to
    having no seed in common."""
    first: list[float] = []
    second: list[float] = []
    used: list[RowKey] = []
    no_common_seed = 0
    for truth in frame.kept:
        if spec.out_of_dictionary and truth not in frame.out_of_dictionary:
            continue
        a = frame.gaps.get((truth, spec.first), {})
        b = frame.gaps.get((truth, spec.second), {})
        seeds = sorted(
            s
            for s in a.keys() & b.keys()
            if (truth, s, spec.first) not in absent
            and (truth, s, spec.second) not in absent
        )
        if not seeds:
            no_common_seed += 1
            continue
        first.append(_mean([a[s] for s in seeds]))
        second.append(_mean([b[s] for s in seeds]))
        for s in seeds:
            used += [
                (truth, s, spec.first),
                (truth, s, spec.second),
                (truth, s, ORACLE),
            ]
    rows = tuple(sorted(used))
    n = len(first)
    gated = frame.controls_pass and (frame.naming_null or not spec.naming)
    if not gated or n < MIN_TRUTHS:
        return ContrastRead(
            math.nan, math.nan, math.nan, False, n, rows
        ), no_common_seed
    ci = paired_bootstrap_ci(
        first,
        second,
        np.random.default_rng(np.random.SeedSequence([frame.seed, index])),
        resamples=frame.resamples,
    )
    return ContrastRead(ci.mean, ci.low, ci.high, True, n, rows), no_common_seed


def read_contrasts(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> Mapping[str, ContrastRead]:
    """Every read of ``table`` (a table after ``derive``), in sorted name order."""
    frame = _frame(table, controls, seed, resamples)
    return {
        spec.name: _read(i, spec, frame, frozenset())[0] for i, spec in enumerate(READS)
    }


def read_floors(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> Mapping[str, FloorRead]:
    """S6 for every read of ``table``, in sorted name order (module docstring)."""
    frame = _frame(table, controls, seed, resamples)
    out: dict[str, FloorRead] = {}
    for i, spec in enumerate(READS):
        read, no_common_seed = _read(i, spec, frame, frozenset())
        without, _ = _read(i, spec, frame, frame.floored)
        out[spec.name] = FloorRead(
            floored=sum(key in frame.floored for key in read.rows),
            no_common_seed=no_common_seed,
            without=without,
        )
    return out
