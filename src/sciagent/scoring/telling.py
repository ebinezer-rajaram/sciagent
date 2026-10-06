"""The telling diagnostic of a (truth, seed) (SPEC §4.2; P4 Δ11, plan choice 12).

SPEC §4.2 defines it per truth "by the oracle: the diagnostic with the largest
truth-vs-best-library discrepancy". Here, with the policy menus
(``systems/v2/policies.py``) frozen on the world's observational log:

    d* = argmax_d max_e He²(truth, member) on (e, d),

where ``member`` is B-lib's pick on the observational log and He² is
``battery.hellinger2`` between R = :data:`TELLING_REPLICATES` replicates per
side. Ties go to menu order (diagnostics first, then the first design that
attains the maximum).

**Stopped runs are dropped, not counted as undefined.** A replicate whose
simulation stops (past the event cap, or a negative identity-link intensity)
yields no value for *any* diagnostic, so it says the design is telling but
not which diagnostic is. Counting it as the "undefined" atom (as the battery
and the policies' predictives do) makes every diagnostic equally telling on
that design: for S11 against a fitted Hawkes model, the truth explodes on
every ``inject_size_high`` replicate, every diagnostic's He² there is
exactly 1, and d* fell to the first menu entry, ``mean_rate``. So each side
keeps only its non-stopped replicates; a design on which either side has
none scores 0 for every diagnostic, and the stopped shares are recorded
(:attr:`Telling.stopped`). An undefined value from a run that did not stop
(the diagnostic itself cannot be computed) is still the atom: that is a
property of the diagnostic. Whether a policy arm *observed* d* (C5's rate) is
:func:`was_observed`: d* was computed on some experiment dataset, matched by
name and channel arguments (time arguments are the observer's own choice).

**Randomness.** Replicate r of design e on side s (0 truth, 1 member) is
drawn from ``scorer_seed(TELLING, truth_id, seed, s, e, r)``: a scorer stream,
disjoint from the world's, the battery's and the policies'
(``scoring/seeds.py``). The same draws serve every system scored on a
(truth, seed), so the telling diagnostic is one per (truth, seed).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from sciagent.diagnostics.catalogue import ArgValue
from sciagent.glm.grammar import ChannelSpec
from sciagent.scoring.battery import Simulator, hellinger2
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.seeds import TELLING, generator, scorer_seed
from sciagent.systems.v2.policies import (
    POLICY_MAX_EVENTS,
    Design,
    DiagnosticChoice,
    Menu,
    Runs,
    Table,
    replicate_runs,
)

#: Replicates per (design, side).
TELLING_REPLICATES: Final = 32


@dataclass(frozen=True)
class Telling:
    """d*, the design where it peaks, its He² and the whole table ``[e][d]``."""

    diagnostic_index: int
    design_index: int
    diagnostic: DiagnosticChoice
    design: Design
    value: float
    table: Table
    stopped: tuple[tuple[float, float], ...] = ()

    @staticmethod
    def from_table(
        table: Table,
        designs: tuple[Design, ...],
        diagnostics: tuple[DiagnosticChoice, ...],
        stopped: tuple[tuple[float, float], ...] = (),
    ) -> Telling:
        """The argmax of the table (module docstring)."""
        if not table or len(table) != len(designs):
            raise ScoringError("one table row per design")
        if any(len(row) != len(diagnostics) for row in table):
            raise ScoringError("one table column per diagnostic")
        best_d, best_e, best = 0, 0, -1.0
        for d in range(len(diagnostics)):
            column = [row[d] for row in table]
            peak = max(column)
            if peak > best:
                best_d, best_e, best = d, column.index(peak), peak
        return Telling(
            best_d, best_e, diagnostics[best_d], designs[best_e], best, table, stopped
        )


@dataclass(frozen=True)
class TellingTable:
    """He² ``[e][d]`` and, per design, the (truth, member) stopped shares."""

    table: Table
    stopped: tuple[tuple[float, float], ...]


def _kept(
    values: tuple[float | None, ...], stopped: tuple[bool, ...]
) -> list[float | None]:
    return [v for v, s in zip(values, stopped, strict=True) if not s]


def telling_runs(
    truth: Simulator,
    member: Simulator,
    menu: Menu,
    channels: tuple[ChannelSpec, ...],
    truth_id: str,
    seed: int,
    *,
    replicates: int = TELLING_REPLICATES,
    max_events: int = POLICY_MAX_EVENTS,
) -> TellingTable:
    """He²(truth, member) on every (e, d), stopped runs dropped (module docstring)."""
    if replicates < 2:
        raise ScoringError(f"need at least 2 replicates, got {replicates}")
    rows: list[tuple[float, ...]] = []
    shares: list[tuple[float, float]] = []
    n_d = len(menu.diagnostics)
    for e, design in enumerate(menu.designs):
        sides: list[Runs] = []
        for side, simulator in enumerate((truth, member)):

            def rng_for(r: int, side: int = side, e: int = e) -> np.random.Generator:
                return generator(scorer_seed(TELLING, truth_id, seed, side, e, r))

            sides.append(
                replicate_runs(
                    simulator,
                    design,
                    menu.diagnostics,
                    channels,
                    rng_for,
                    replicates,
                    max_events,
                )
            )
        a, b = sides
        shares.append((sum(a.stopped) / replicates, sum(b.stopped) / replicates))
        if all(a.stopped) or all(b.stopped):
            rows.append((0.0,) * n_d)
            continue
        rows.append(
            tuple(
                hellinger2(_kept(a.values[d], a.stopped), _kept(b.values[d], b.stopped))
                for d in range(n_d)
            )
        )
    return TellingTable(tuple(rows), tuple(shares))


def telling_table(
    truth: Simulator,
    member: Simulator,
    menu: Menu,
    channels: tuple[ChannelSpec, ...],
    truth_id: str,
    seed: int,
    *,
    replicates: int = TELLING_REPLICATES,
    max_events: int = POLICY_MAX_EVENTS,
) -> Table:
    """The He² table of :func:`telling_runs`."""
    return telling_runs(
        truth,
        member,
        menu,
        channels,
        truth_id,
        seed,
        replicates=replicates,
        max_events=max_events,
    ).table


def telling_diagnostic(
    truth: Simulator,
    member: Simulator,
    menu: Menu,
    channels: tuple[ChannelSpec, ...],
    truth_id: str,
    seed: int,
    *,
    replicates: int = TELLING_REPLICATES,
    max_events: int = POLICY_MAX_EVENTS,
) -> Telling:
    """d* of a (truth, seed) (module docstring)."""
    runs = telling_runs(
        truth,
        member,
        menu,
        channels,
        truth_id,
        seed,
        replicates=replicates,
        max_events=max_events,
    )
    return Telling.from_table(runs.table, menu.designs, menu.diagnostics, runs.stopped)


def was_observed(
    telling: Telling, computed: Iterable[tuple[str, Mapping[str, ArgValue]]]
) -> bool:
    """True when some ``(name, args)`` computed on experiment data is d*."""
    target = telling.diagnostic
    channels = dict(target.channel_args)
    for name, args in computed:
        if name != target.name:
            continue
        if all(args.get(k) == v for k, v in channels.items()):
            return True
    return False
