"""The telling diagnostic per (truth, seed) (SPEC §4.2; plan choice 12).

d* is the diagnostic with the largest truth-vs-best-library discrepancy:
``argmax_d max_e He²(truth, member)`` over the policy menus, with R = 32
replicates per side on the TELLING stream. It is an instrument (C5 reads
whether d* was observed), so: the table is ``battery.hellinger2`` on the
documented streams, the argmax goes to menu order on ties, it is
deterministic, and on the planted Hawkes-vs-S11 case it is a cross-mark
diagnostic (v1's 0/112 statistic family).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest
from scoring_support import truth

from environments.pointproc import v2
from sciagent.diagnostics import catalogue
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import Experiment
from sciagent.glm.simulate import ExplosionError
from sciagent.scoring.battery import hellinger2
from sciagent.scoring.seeds import TELLING, generator, scorer_seed
from sciagent.scoring.telling import (
    TELLING_REPLICATES,
    Telling,
    telling_diagnostic,
    telling_runs,
    telling_table,
    was_observed,
)
from sciagent.scoring.truth import TruthModel
from sciagent.systems.v2.policies import (
    DiagnosticChoice,
    Menu,
    design_menu,
    diagnostic_menu,
)
from sciagent.systems.v2.systems import BLib, GrammarMember, InvestigationData

#: Diagnostics that read a mark against the timing of events: the family of
#: v1's ``size_gap_correlation`` (LOG 2026-10-01, the 0/112 statistic).
CROSS_MARK = frozenset(
    {
        "mark_gap_correlation",
        "next_gap_after_large_mark",
        "rate_after_mark_slope",
        "mark_intensity_correlation",
    }
)


@dataclass(frozen=True)
class PoissonSim:
    rate: float

    def simulate_experiment(
        self, experiment: Experiment, rng: np.random.Generator, *, label: str = "x"
    ) -> Dataset:
        n = int(rng.poisson(self.rate * experiment.horizon))
        times = np.sort(rng.uniform(0.0, experiment.horizon, n))
        rows = [v2.mark_sampler(rng) for _ in range(n)]
        marks = {
            c.name: np.array([r[c.name] for r in rows], dtype=np.float64)
            for c in v2.CHANNELS
        }
        return Dataset.observational(
            EventLog.create(times, marks, experiment.horizon), label
        )


def _menu(n_designs: int = 2, horizon: float = 60.0) -> Menu:
    sim = PoissonSim(1.0)
    exp = design_menu(v2.CHANNELS, horizon=200.0)[0].experiment
    log = sim.simulate_experiment(exp, np.random.default_rng(0)).log
    return Menu(
        design_menu(v2.CHANNELS, horizon=horizon)[:n_designs],
        diagnostic_menu(log, v2.CHANNELS),
    )


def test_replicates_default_is_32() -> None:
    assert TELLING_REPLICATES == 32


def test_table_is_hellinger2_on_the_telling_stream() -> None:
    menu = _menu()
    a, b = PoissonSim(1.0), PoissonSim(2.0)
    table = telling_table(a, b, menu, v2.CHANNELS, "t-1", 3, replicates=4)
    for e, design in enumerate(menu.designs):
        sides: list[list[list[float | None]]] = []
        for side, sim in enumerate((a, b)):
            per_d: list[list[float | None]] = [[] for _ in menu.diagnostics]
            for r in range(4):
                rng = generator(scorer_seed(TELLING, "t-1", 3, side, e, r))
                data = sim.simulate_experiment(design.experiment, rng)
                for d, choice in enumerate(menu.diagnostics):
                    try:
                        value: float | None = catalogue.compute(
                            choice.name,
                            data.log,
                            v2.CHANNELS,
                            dict(choice.args),
                            check_bounds=False,
                        )
                    except catalogue.InsufficientDataError:
                        value = None
                    per_d[d].append(value)
            sides.append(per_d)
        for d in range(len(menu.diagnostics)):
            assert table[e][d] == hellinger2(sides[0][d], sides[1][d])


def test_telling_is_the_argmax_and_deterministic() -> None:
    menu = _menu()
    a, b = PoissonSim(1.0), PoissonSim(3.0)
    t1 = telling_diagnostic(a, b, menu, v2.CHANNELS, "t-2", 1, replicates=6)
    t2 = telling_diagnostic(a, b, menu, v2.CHANNELS, "t-2", 1, replicates=6)
    assert t1 == t2
    best = max(max(row[d] for row in t1.table) for d in range(len(menu.diagnostics)))
    assert t1.value == best
    column = [row[t1.diagnostic_index] for row in t1.table]
    assert t1.design_index == column.index(max(column))
    assert t1.diagnostic == menu.diagnostics[t1.diagnostic_index]
    # Rates 1 vs 3 over 60 time units: the best diagnostic separates them.
    rate = next(i for i, d in enumerate(menu.diagnostics) if d.name == "mean_rate")
    assert t1.value >= max(row[rate] for row in t1.table) > 0.99


def test_ties_go_to_menu_order() -> None:
    menu = _menu()
    t = Telling.from_table(
        ((0.5, 0.5), (0.5, 0.2)), menu.designs[:2], menu.diagnostics[:2]
    )
    assert (t.design_index, t.diagnostic_index) == (0, 0)
    later = Telling.from_table(
        ((0.1, 0.2), (0.6, 0.6)), menu.designs[:2], menu.diagnostics[:2]
    )
    assert (later.design_index, later.diagnostic_index) == (1, 0)


def test_observed_matches_name_and_channel_arguments() -> None:
    menu = _menu()
    gap = next(
        i
        for i, d in enumerate(menu.diagnostics)
        if d.name == "mark_gap_correlation" and dict(d.args)["channel"] == "size"
    )
    table = tuple(
        tuple(1.0 if (e, d) == (1, gap) else 0.0 for d in range(len(menu.diagnostics)))
        for e in range(len(menu.designs))
    )
    t = Telling.from_table(table, menu.designs, menu.diagnostics)
    assert t.diagnostic.name == "mark_gap_correlation"
    assert was_observed(t, [("mark_gap_correlation", {"channel": "size"})])
    assert not was_observed(t, [("mark_gap_correlation", {"channel": "sign"})])
    assert not was_observed(t, [("mean_rate", {})])
    assert not was_observed(t, [])
    # Time arguments are the observer's choice; only channels identify d*.
    rate = Telling.from_table(
        tuple(
            tuple(1.0 if d == 0 else 0.0 for d in range(len(menu.diagnostics)))
            for _ in menu.designs
        ),
        menu.designs,
        menu.diagnostics,
    )
    assert was_observed(rate, [("mean_rate", {})])
    fano = DiagnosticChoice("fano_factor", (("window", 2.0),))
    assert fano.label == "fano_factor"


@dataclass(frozen=True)
class StoppingSim:
    """Stops (explodes) with probability ``p``; otherwise Poisson at ``rate``."""

    p: float
    rate: float = 1.0

    def simulate_experiment(
        self, experiment: Experiment, rng: np.random.Generator, *, label: str = "x"
    ) -> Dataset:
        if rng.random() < self.p:
            raise ExplosionError("planted stop")
        return PoissonSim(self.rate).simulate_experiment(experiment, rng, label=label)


def test_a_design_where_a_side_always_stops_tells_no_diagnostic() -> None:
    menu = _menu()
    runs = telling_runs(
        StoppingSim(1.0), PoissonSim(1.0), menu, v2.CHANNELS, "t", 1, replicates=4
    )
    assert runs.stopped == ((1.0, 0.0), (1.0, 0.0))
    assert all(x == 0.0 for row in runs.table for x in row)


def test_stopped_replicates_are_dropped_not_counted_undefined() -> None:
    menu = _menu(n_designs=1)
    truth_sim, member = StoppingSim(0.5, 2.0), PoissonSim(1.0)
    runs = telling_runs(truth_sim, member, menu, v2.CHANNELS, "t", 2, replicates=8)
    ((share_truth, share_member),) = runs.stopped
    assert 0.0 < share_truth < 1.0 and share_member == 0.0
    # Rebuild the truth side by hand: only the replicates that did not stop.
    kept: list[float | None] = []
    for r in range(8):
        rng = generator(scorer_seed(TELLING, "t", 2, 0, 0, r))
        try:
            data = truth_sim.simulate_experiment(menu.designs[0].experiment, rng)
        except ExplosionError:
            continue
        kept.append(
            catalogue.compute(
                "mean_rate", data.log, v2.CHANNELS, {}, check_bounds=False
            )
        )
    other: list[float | None] = []
    for r in range(8):
        rng = generator(scorer_seed(TELLING, "t", 2, 1, 0, r))
        data = member.simulate_experiment(menu.designs[0].experiment, rng)
        other.append(
            catalogue.compute(
                "mean_rate", data.log, v2.CHANNELS, {}, check_bounds=False
            )
        )
    rate = next(i for i, d in enumerate(menu.diagnostics) if d.name == "mean_rate")
    assert len(kept) == round(8 * (1.0 - share_truth))
    assert runs.table[0][rate] == hellinger2(kept, other)


@pytest.mark.slow
def test_planted_hawkes_vs_s11_telling_diagnostic_is_cross_mark() -> None:
    """S11 truth, Hawkes as B-lib's pick: d* reads size against timing."""
    log = v2.TRUTHS["size_excitation"].simulate(2000.0, np.random.default_rng(1))
    data = InvestigationData(
        (Dataset.observational(log),), v2.CHANNELS, v2.mark_sampler
    )
    lib = BLib((GrammarMember("null", v2.NULL), GrammarMember("hawkes", v2.HAWKES)))
    member = lib.run(data)
    assert member.submitted == "hawkes"
    menu = Menu(design_menu(v2.CHANNELS), diagnostic_menu(log, v2.CHANNELS))
    told = telling_diagnostic(
        TruthModel(truth("size_excitation")),
        member.model,
        menu,
        v2.CHANNELS,
        "size_excitation",
        1,
    )
    assert told.diagnostic.name in CROSS_MARK, told.diagnostic
    assert dict(told.diagnostic.args).get("channel") == "size", told.diagnostic
