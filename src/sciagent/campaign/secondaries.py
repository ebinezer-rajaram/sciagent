"""P4's labelled secondaries: exploratory, and no read of the miss check.

**Named minus anon, per model** (SPEC §6.2; control room ruling B2, 2026-11-06).
One read per tier and model, six in all: named minus anonymised on the held-out
gap. It mixes names and time (SPEC Q2, "bounds"), and it is the only like-for-like
figure across models, since `rescaled` runs on Sonnet alone. It is not one of
:data:`~sciagent.campaign.contrasts.READS`, so the miss check's tier 1, its labels
and its corrected table do not cover it, and any report must say so beside it. It
is read only where the contrasts may be read: after ``reads.json`` exists on P4.

**The rules are the contrasts'**, by the same code: truths from step 1 of
``contrasts``, seeds in common within a truth, ``contrasts._pairs``, and the same
gates. Controls 1, 2 and 3 gate every read, and control 4, the naming null, gates
all six, as it gates C2. A read needs at least ``MIN_TRUTHS`` truths.

**One rule is new, because the contrast rows hold no Haiku or Opus row.** The pair
rule's floor on a (truth, seed) comes from the contrast rows plus that read's own
two systems, and no other. So a Haiku or Opus row moves no read but its own, and
Sonnet's reads floor exactly as C2's do. The contrast rows are Sonnet's arms and
the baselines, though, so a Haiku or Opus read does depend on them: a low Sonnet
reading on a (truth, seed) lowers the floor a failed Haiku or Opus row takes there.
On complete rows Sonnet's read is C2's time read plus its names read.

**Rows left out** (:func:`read_named_minus_anon_floors`; SPEC §6.2). Beside every
read, the count of its rows that took a floor and the read with those rows absent,
as S6 does for the contrasts: the left-out read draws read ``i``'s own stream afresh
and has the same gates, asked with its own ``n``. :func:`why_not` gives the reasons a
read is not reportable, by the rule that sets ``reportable``.

**Scope** (:func:`read_scope`; control room ruling R2, 2026-11-07). If more than
:data:`MAX_LEFT_PERCENT` percent of a table's truths leave the reads, as dropped or
unchecked, the reads are reported as not confirmatory, and C4 likewise on its
out-of-dictionary truths alone. It is a limit on scope and not a test, set before any
P4 data, with its reasons in ``docs/v2/SPEC-TEXT-P4-C.md``. It moves no read and no
label: the reads are still printed. The total is the table's truths, which kept,
dropped and unchecked must partition, so the limit was set with
:data:`~sciagent.campaign.contrasts.READ_UNCHECKED_TRUTHS` off and refuses a table
read with it on. A table with no truth, or C4 with no out-of-dictionary truth, is not
confirmatory.

**Generators.** Read ``i`` draws from ``SeedSequence([seed, SECONDARY_STREAM + i])``,
clear of the contrasts' streams and of control 2's.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np

from sciagent.campaign import analysis, contrasts
from sciagent.campaign.analysis import Row, Table
from sciagent.campaign.contrasts import (
    CONTRAST_SYSTEMS,
    CONTROLS_FAILED,
    MIN_TRUTHS,
    NAMING_NULL_FAILED,
    TOO_FEW_TRUTHS,
    ContrastRead,
    ContrastSpec,
    ControlsRead,
    FloorRead,
    RowKey,
    ScriptedControls,
    floor_rows,
)
from sciagent.campaign.plan import P4_PROMPT_VARIANT, TruthEntry
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.stats import paired_bootstrap_ci

#: Read ``i`` draws stream ``SECONDARY_STREAM + i``.
SECONDARY_STREAM: Final = 2000
#: More than this share of truths leaving makes the reads not confirmatory.
MAX_LEFT_PERCENT: Final = 10


def _system(tier: str, letter: str, condition: str) -> str:
    """A P4 arm's system name, as ``LLMArm.system`` gives it."""
    return f"{tier}-{letter}/{condition}+{P4_PROMPT_VARIANT}"


#: The reads, in sorted name order: read ``i`` draws its stream ``i``.
NAMED_MINUS_ANON: Final = tuple(
    ContrastSpec(
        f"{tier}-{letter}",
        _system(tier, letter, "named"),
        _system(tier, letter, "anon"),
        naming=True,
    )
    for tier in ("AG-c", "AG-o")
    for letter in ("H", "O", "S")
)


def _own_rows(table: Table, spec: ContrastSpec) -> list[Row]:
    """The read's sides that are not contrast rows, refusing what no rule covers."""
    rows: list[Row] = []
    for r in table.rows:
        if r.system in CONTRAST_SYSTEMS or r.system not in (spec.first, spec.second):
            continue
        if r.per_event is None:
            if r.ok and r.submitted:
                raise ScoringError(f"row {r.truth, r.seed, r.system} has no reading")
        elif math.isnan(r.per_event) or r.per_event == math.inf:
            raise ScoringError(
                f"row {r.truth, r.seed, r.system} has per_event {r.per_event}"
            )
        rows.append(r)
    return rows


def _gaps(
    rows: Sequence[Row], void: frozenset[RowKey]
) -> tuple[
    Mapping[tuple[str, str], Mapping[int, float]], Mapping[RowKey, tuple[RowKey, ...]]
]:
    """Each (truth, system)'s finite gaps by seed, after the pair rule on ``rows``,
    and each floored key with what set its floor."""
    copy, floored = floor_rows(
        [r for r in rows if (r.truth, r.seed, r.system) not in void]
    )
    gaps: dict[tuple[str, str], dict[int, float]] = {}
    for r in analysis.derive(copy):
        if r.gap is not None and math.isfinite(r.gap):
            gaps.setdefault((r.truth, r.system), {})[r.seed] = r.gap
    return gaps, floored


def why_not(controls: ControlsRead, n: int) -> tuple[str, ...]:
    """Every reason a read of this module with ``n`` truths is not reportable on the
    table ``controls`` was read from, in the contrasts' order. Every read here is
    gated by control 4, as C2 is. Empty means reportable."""
    return (
        *((CONTROLS_FAILED,) if not controls.controls_pass else ()),
        *((NAMING_NULL_FAILED,) if not controls.naming_null else ()),
        *((TOO_FEW_TRUTHS,) if n < MIN_TRUTHS else ()),
    )


def _read(index: int, frame: contrasts._Frame, pairs: contrasts._Pairs) -> ContrastRead:
    """Read ``index`` of ``pairs``, on its own stream."""
    n = len(pairs.first)
    if why_not(frame.controls, n):
        return ContrastRead(math.nan, math.nan, math.nan, False, n, pairs.rows)
    ci = paired_bootstrap_ci(
        pairs.first,
        pairs.second,
        np.random.default_rng(
            np.random.SeedSequence([frame.seed, SECONDARY_STREAM + index])
        ),
        resamples=frame.resamples,
    )
    return ContrastRead(ci.mean, ci.low, ci.high, True, n, pairs.rows)


def _frames(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> Iterator[tuple[int, ContrastSpec, contrasts._Frame]]:
    """Each read's index, spec and frame: the contrasts' frame with the read's own
    floors (module docstring)."""
    base = contrasts._frame(table, controls, seed, resamples)
    given = contrasts._checked_rows(table)
    void = frozenset((x.truth, x.seed, x.system) for x in table.sessions if x.void.void)
    for i, spec in enumerate(NAMED_MINUS_ANON):
        gaps, floored = _gaps([*given, *_own_rows(table, spec)], void)
        yield i, spec, replace(base, gaps=gaps, floored=floored)


def read_named_minus_anon(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> Mapping[str, ContrastRead]:
    """Every read of ``table`` (a table after ``derive``), in sorted name order."""
    return {
        spec.name: _read(i, frame, contrasts._pairs(spec, frame, frozenset()))
        for i, spec, frame in _frames(table, controls, seed, resamples)
    }


def read_named_minus_anon_floors(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> Mapping[str, FloorRead]:
    """Each read's floored rows and the read without them, in sorted name order
    (module docstring)."""
    out: dict[str, FloorRead] = {}
    for i, spec, frame in _frames(table, controls, seed, resamples):
        pairs = contrasts._pairs(spec, frame, frozenset())
        out[spec.name] = FloorRead(
            floored=sum(key in frame.floored for key in pairs.sides),
            no_common_seed=pairs.no_common_seed,
            without=_read(
                i, frame, contrasts._pairs(spec, frame, frozenset(frame.floored))
            ),
        )
    return out


@dataclass(frozen=True)
class Scope:
    """How many truths left the reads of one table (module docstring)."""

    left: int
    total: int
    left_out_of_dictionary: int
    out_of_dictionary: int

    @property
    def confirmatory(self) -> bool:
        return self.total > 0 and self.left * 100 <= self.total * MAX_LEFT_PERCENT

    @property
    def c4_confirmatory(self) -> bool:
        within = (
            self.left_out_of_dictionary * 100
            <= self.out_of_dictionary * MAX_LEFT_PERCENT
        )
        return self.confirmatory and self.out_of_dictionary > 0 and within


def read_scope(controls: ControlsRead, truths: Mapping[str, TruthEntry]) -> Scope:
    """The scope of the reads on the table whose truths are ``truths``, read by
    ``controls``. Kept, dropped and unchecked must partition ``truths``."""
    parts = (set(controls.kept), set(controls.dropped), set(controls.unchecked))
    whole = set(truths)
    if sum(map(len, parts)) != len(whole) or set().union(*parts) != whole:
        raise ScoringError(
            "kept, dropped and unchecked do not partition the table's truths: "
            f"{len(controls.kept)} + {len(controls.dropped)} + "
            f"{len(controls.unchecked)} for {len(whole)}"
        )
    left = parts[1] | parts[2]
    outside = {t for t, entry in truths.items() if not entry.in_dictionary}
    return Scope(
        left=len(left),
        total=len(whole),
        left_out_of_dictionary=len(left & outside),
        out_of_dictionary=len(outside),
    )
