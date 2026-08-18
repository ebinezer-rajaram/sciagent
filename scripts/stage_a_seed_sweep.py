"""Count how many model calls SPEC §9's matrix actually makes, across seeds.

The rate-limit pilot (``docs/DECISIONS.md`` 2026-08-16) priced one proposal and
found the open item's denominator wrong: 1,120 is an investigation count, only
:class:`~sciagent.systems.hybrid.Hybrid` holds a proposal layer, and the entry
"the gate is threaded" measures the scoped Stage A gate opening on S11 alone.
That gives ~120 calls rather than ~1,120 -- but it rests on **one seed per
scenario**, and S11's probe reads 0.0112 against an alpha of 0.05 while the
nearest in-library miss reads 0.0595. Either could move.

This settles it by running the model-bearing cells at twenty seeds each with a
**scripted** provider, and counting the times the layer is consulted. No quota
is spent, because no model is called: what is being measured is the gate, and
the gate is conventional (SPEC F5) and cannot see which backend would have
answered.

What the count means
--------------------

``Hybrid._extend`` loops ``max_proposals`` times and breaks only on a *refusal*
-- a provider error or an exhausted budget. A scripted provider never refuses, so
a cell whose gate opens asks exactly ``max_proposals`` times here. Against a live
model that is a **ceiling**: a real refusal on the first call would stop the loop
at one. So the totals below bound the recording run from above, which is the
direction a budget wants to be wrong in.

The seed grid, and the control built into it
---------------------------------------------

Seed ``k`` of a scenario is ``scenario.seed + STRIDE * k``. The stride is large
because the twelve scenario seeds are consecutive (20260901..20260912), so a
stride of one would have given S1's second seed the same value as S2's first --
not wrong, since the two execute different defects, but a grid nobody should
have to think that hard about.

``k = 0`` is therefore the scenario's own seed, so the first column must
reproduce the recorded table -- S11 asking twice, every other scenario zero. If
it does not, this harness is wrong and nothing else it prints should be read.

**This grid is not the matrix's, and no count here is the matrix's budget.**
Measured 2026-08-18: :func:`sciagent.eval.matrix.replicate_seeds` returns
``stable_key(f"matrix/{scenario_seed}/{index}") % _SEED_MODULUS`` -- hashed, not
arithmetic -- and the two seed sets overlap in **0 of 20** on every scenario
checked. The ``k = 0`` control above is sound for what it checks, item 12's
single-seed runs at ``scenario.seed``, and the matrix uses none of those either.
The firing table this prints therefore describes *these* seeds: it is evidence
about how seed-sensitive the gate is, and not a call count for a recording run.
The completed §9 matrix fired 17/20 on S11 where this grid gives 20/20. See
``docs/DECISIONS.md`` 2026-08-18.

Usage::

    uv run python scripts/stage_a_seed_sweep.py --seeds 1    # the control alone
    uv run python scripts/stage_a_seed_sweep.py --seeds 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import slice_scenarios
from sciagent.core.types import Seed
from sciagent.eval.campaign import run_scenario
from sciagent.eval.scenarios import Scenario
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.ablation import memory_ablation
from sciagent.systems.base import null_seeded_graph
from sciagent.systems.hybrid import Hybrid
from sciagent.systems.llm import (
    RECORD,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
    structural_menu,
)

# See scripts/rate_limit_pilot.py for why the table is reached by path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

#: Distance between consecutive seeds of one scenario. Larger than the spacing
#: of the twelve scenario seeds, so no two cells of the grid coincide.
STRIDE = 1_000_000

#: Where item 13's ablation runs (SPEC §11 item 13, and the /matrix table).
ABLATION_SCENARIOS = ("S8", "S11", "S12")

#: What ``Hybrid`` is configured with everywhere in the matrix.
MAX_PROPOSALS = 2


@dataclass(frozen=True, slots=True)
class CellResult:
    """One (system, scenario) cell, swept over seeds."""

    system: str
    scenario: str
    seeds: int
    fired: int
    """Seeds at which the Stage A gate opened and the layer was consulted."""

    asks: int
    """Total consultations across every seed. The call count this cell costs."""

    fired_at: tuple[int, ...]
    """Which seed indices fired, so a partial firing is legible rather than a
    bare count."""


def seeds_for(the_scenario: Scenario, count: int) -> tuple[Seed, ...]:
    """Return ``count`` seeds for a scenario, its own first."""
    return tuple(Seed(int(the_scenario.seed) + STRIDE * k) for k in range(count))


def _systems(
    store: TranscriptStore,
) -> tuple[Hybrid, Hybrid, Hybrid]:
    """Return ``(V7, V3, V4)``, all scripted, over one transcript store.

    The ablation pair is built through
    :func:`~sciagent.systems.ablation.memory_ablation` rather than by hand, so
    the two arms differ in exactly the one respect that function guarantees.
    """
    library = closed_set()
    first = structural_menu(AGENT_GRAMMAR)[0]
    # Mid-grid, not index zero. What is proposed cannot change the firing count
    # -- the gate is consulted before `_extend` runs -- but it does decide what
    # gets simulated afterwards, and the corner of a 64-point grid
    # (base_rate=0.01, branching=0.02, decay=0.02) is both unrepresentative and
    # expensive. The live pilot's model chose [23, 30, 18] on this same cell.
    payload = fixed_payload(
        first.index, tuple(len(grid.values) // 2 for grid in first.grids)
    )

    def provider() -> ScriptedProvider:
        return ScriptedProvider(lambda _brief: payload)

    v7 = Hybrid(
        library,
        ProposalLayer(provider(), AGENT_GRAMMAR, store),
        max_proposals=MAX_PROPOSALS,
        name="V7",
    )
    v3, v4 = memory_ablation(
        library, provider, AGENT_GRAMMAR, store, max_proposals=MAX_PROPOSALS
    )
    return v7, v3, v4


def _asks(the_scenario: Scenario, system: Hybrid, table: EmpiricalTable) -> int:
    """Return how many times ``system`` consulted its proposal layer.

    Runs the scenario through :func:`~sciagent.eval.campaign.run_scenario`, which
    is what threads the Stage A reading into the investigation. Reaching the gate
    any other way would be measuring a check this harness had wired itself.
    """
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    run = run_scenario(
        the_scenario,
        system,
        executor=executor(
            GRAMMAR,
            store=ExperimentStore.in_memory(),
            budget=the_scenario.budget,
        ),
        engine=engine,
        graph=graph,
    )
    return 0 if run.attempts is None else len(run.attempts)


def sweep(seeds: int, only: Sequence[str] = ()) -> list[CellResult]:
    """Run every model-bearing cell at ``seeds`` seeds and count consultations.

    ``only`` restricts the run to named ``SYSTEM/SCENARIO`` cells, which is what
    makes this resumable. It is not a convenience: a full sweep exhausted memory
    partway through V4/S11 on the first attempt, after roughly 300 investigations
    in one process, and cells are independent -- nothing carries between them but
    the shared read-only table -- so running them in separate processes is both
    the fix and the honest structure for the work.
    """
    table = gate_table()
    scenarios = {str(one.id): one for one in slice_scenarios()}
    cells: list[tuple[str, str]] = [("V7", name) for name in scenarios]
    cells += [(arm, name) for name in ABLATION_SCENARIOS for arm in ("V3", "V4")]
    if only:
        wanted = {entry.strip() for entry in only}
        unknown = wanted - {f"{system}/{name}" for system, name in cells}
        if unknown:
            raise ValueError(
                f"no such cell(s): {sorted(unknown)}; the model-bearing cells are "
                f"{sorted(f'{s}/{n}' for s, n in cells)}"
            )
        cells = [(s, n) for s, n in cells if f"{s}/{n}" in wanted]

    results: list[CellResult] = []
    for system_name, scenario_name in cells:
        base = scenarios[scenario_name]
        fired_at: list[int] = []
        asks = 0
        started = time.perf_counter()
        for k, seed in enumerate(seeds_for(base, seeds)):
            # A fresh store and fresh systems per seed: a layer's call counter
            # enters the transcript address, so a system carried across seeds
            # would address its second seed's calls as though they were its
            # first's.
            store = TranscriptStore(mode=RECORD)
            v7, v3, v4 = _systems(store)
            system = {"V7": v7, "V3": v3, "V4": v4}[system_name]
            count = _asks(replace(base, seed=seed), system, table)
            asks += count
            if count:
                fired_at.append(k)
        results.append(
            CellResult(
                system=system_name,
                scenario=scenario_name,
                seeds=seeds,
                fired=len(fired_at),
                asks=asks,
                fired_at=tuple(fired_at),
            )
        )
        latest = results[-1]
        print(
            f"  {system_name:<3} {scenario_name:<4} "
            f"fired {latest.fired:>2}/{seeds:<3} asks {latest.asks:>3}  "
            f"({time.perf_counter() - started:.1f}s)"
        )
    return results


def report(results: Sequence[CellResult], *, seeds: int, partial: bool = False) -> bool:
    """Print the firing table, the control check and the call total.

    Returns whether the run is trustworthy: ``False`` when the control did not
    reproduce the recorded table, so a caller can exit non-zero rather than
    printing "do not read the totals below" over a success code.
    """
    total_asks = sum(result.asks for result in results)
    firing = [result for result in results if result.fired]

    print()
    print("cells that ever fired:")
    for result in firing:
        print(
            f"  {result.system:<3} {result.scenario:<4} "
            f"{result.fired}/{result.seeds} seeds, {result.asks} asks"
        )
    if not firing:
        print("  none -- the matrix would make no model call at all")

    print()
    control = [
        result
        for result in results
        if 0 in result.fired_at  # the scenario's own seed
    ]
    expected = {("V7", "S11"), ("V3", "S11"), ("V4", "S11")}
    seen = {(result.system, result.scenario) for result in control}
    trustworthy = True
    print(f"control (k=0, the recorded seeds): fired on {sorted(seen)}")
    if partial:
        print(
            "  partial run -- the control is only meaningful over all 18 cells, "
            "so it is not checked here."
        )
    elif seen == expected:
        print("  matches the recorded table -- S11 alone. Harness agrees.")
    else:
        trustworthy = False
        print(
            f"  DOES NOT match the recorded table, which has S11 alone: "
            f"{sorted(expected)}.\n"
            f"  Something here is wrong; do not read the totals below."
        )

    print()
    print(f"total consultations over {seeds} seed(s): {total_asks}")
    print(
        "That is a CEILING against a live model: a scripted provider never\n"
        "refuses, and a real refusal on the first call would stop the loop at one."
    )
    return trustworthy


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds",
        type=int,
        default=20,
        help="seeds per cell (default 20, as SPEC §9 specifies)",
    )
    parser.add_argument(
        "--only",
        default="",
        help=(
            "comma-separated SYSTEM/SCENARIO cells to run, e.g. 'V4/S11,V3/S12'. "
            "Cells are independent; run them in separate processes to resume."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".cache/rate_limit_pilot/seed_sweep.json"),
        help="where the per-cell result is written (under .cache/)",
    )
    args = parser.parse_args(argv)

    only = tuple(part for part in args.only.split(",") if part.strip())
    if only and args.out == parser.get_default("out"):
        # A filtered run holds one cell's totals, not eighteen. Writing it to the
        # full sweep's default path would silently replace a complete result with
        # a partial one -- and the documented resume workflow (--only V4/S11) is
        # exactly the case that would do it.
        args.out = args.out.with_name(
            f"{args.out.stem}_{'_'.join(sorted(only)).replace('/', '-')}.json"
        )
    print(f"sweeping {len(only) or 'all'} cell(s) at {args.seeds} seed(s)")
    started = time.perf_counter()
    results = sweep(args.seeds, only)
    trustworthy = report(results, seeds=args.seeds, partial=bool(only))
    print(f"\nswept in {time.perf_counter() - started:.1f}s")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload: Mapping[str, object] = {
        "seeds": args.seeds,
        "stride": STRIDE,
        "max_proposals": MAX_PROPOSALS,
        "total_asks": sum(result.asks for result in results),
        "cells": [asdict(result) for result in results],
    }
    args.out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"written: {args.out}")
    return 0 if trustworthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
