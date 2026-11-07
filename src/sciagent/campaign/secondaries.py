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
two systems, and no other. So one model's failure moves no other model's read, a
read does not depend on which other models ran, and Sonnet's reads floor exactly
as C2's do. On complete rows Sonnet's read is C2's time read plus its names read.

**Scope** (:func:`read_scope`; control room ruling R2, 2026-11-07). If more than
:data:`MAX_LEFT_PERCENT` percent of a table's truths leave the reads, as dropped or
unchecked, the reads are reported as not confirmatory, and C4 likewise on its
out-of-dictionary truths alone. It is a limit on scope and not a test, set before any
P4 data, with its reasons in ``docs/v2/SPEC-TEXT-P4-C.md``. It moves no read and no
label: the reads are still printed.

**Generators.** Read ``i`` draws from ``SeedSequence([seed, SECONDARY_STREAM + i])``,
clear of the contrasts' streams and of control 2's.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np

from sciagent.campaign import analysis, contrasts
from sciagent.campaign.analysis import Row, Table
from sciagent.campaign.contrasts import (
    CONTRAST_SYSTEMS,
    MIN_TRUTHS,
    ContrastRead,
    ContrastSpec,
    ControlsRead,
    RowKey,
    ScriptedControls,
    floor_rows,
)
from sciagent.campaign.plan import P4_PROMPT_VARIANT
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


def read_named_minus_anon(
    table: Table, controls: ScriptedControls, seed: int, resamples: int
) -> Mapping[str, ContrastRead]:
    """Every read of ``table`` (a table after ``derive``), in sorted name order."""
    base = contrasts._frame(table, controls, seed, resamples)
    given = contrasts._checked_rows(table)
    void = frozenset((x.truth, x.seed, x.system) for x in table.sessions if x.void.void)
    gated = base.controls.controls_pass and base.controls.naming_null
    out: dict[str, ContrastRead] = {}
    for i, spec in enumerate(NAMED_MINUS_ANON):
        gaps, floored = _gaps([*given, *_own_rows(table, spec)], void)
        pairs = contrasts._pairs(
            spec, replace(base, gaps=gaps, floored=floored), frozenset()
        )
        n = len(pairs.first)
        if not gated or n < MIN_TRUTHS:
            out[spec.name] = ContrastRead(
                math.nan, math.nan, math.nan, False, n, pairs.rows
            )
            continue
        ci = paired_bootstrap_ci(
            pairs.first,
            pairs.second,
            np.random.default_rng(np.random.SeedSequence([seed, SECONDARY_STREAM + i])),
            resamples=resamples,
        )
        out[spec.name] = ContrastRead(ci.mean, ci.low, ci.high, True, n, pairs.rows)
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
        return self.left * 100 <= self.total * MAX_LEFT_PERCENT

    @property
    def c4_confirmatory(self) -> bool:
        within = (
            self.left_out_of_dictionary * 100
            <= self.out_of_dictionary * MAX_LEFT_PERCENT
        )
        return self.confirmatory and within


def read_scope(controls: ControlsRead, out_of_dictionary: Collection[str]) -> Scope:
    """The scope of the reads on ``controls``' table; ``out_of_dictionary`` is that
    table's truths outside the dictionary."""
    left = {*controls.dropped, *controls.unchecked}
    return Scope(
        left=len(left),
        total=len(left) + len(controls.kept),
        left_out_of_dictionary=len(left & set(out_of_dictionary)),
        out_of_dictionary=len(out_of_dictionary),
    )
