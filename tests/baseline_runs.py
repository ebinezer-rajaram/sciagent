"""The item 9 baseline runs, and the claims A23 measures the verifier against.

``tests/test_baselines_slice.py`` owns the runs as item 9's gate. This module
owns them as *material*: acceptance test A23 needs a population of claims from
real slice runs, and SPEC §11 puts the verifier six items before the agent that
will write them. See ``tests/acceptance/test_a19_a23.py`` for why that is
discharged this way rather than deferred.

Kept apart from ``test_baselines_slice`` so that neither suite's cache is the
other's, and so an A23 failure cannot be mistaken for an item 9 regression. The
empirical table is shared through ``slice_tables``, which is where the expense
actually is.
"""

from __future__ import annotations

from functools import lru_cache

from slice_tables import GRAMMAR, METRICS, gate_table, save_gate_table, search_table

from environments.pointproc.grammar import agent_grammar
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import slice_scenarios
from sciagent.core.types import Claim, FrozenDict, HypothesisId, Probability
from sciagent.eval.campaign import ScenarioRun, claims_from_run, run_scenario
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import ResearchSystem, null_seeded_graph
from sciagent.systems.baselines.beam_search import BeamSearch, table_fit
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly
from sciagent.systems.baselines.retrieval import Retrieval
from sciagent.verify import ClaimContext, Verdict, verify

#: The four conventional systems of SPEC §5 that item 9 runs on the slice.
SYSTEM_NAMES = ("V1", "B1", "B4", "B5")


@lru_cache(maxsize=1)
def _beam_search() -> BeamSearch:
    """Return the one B5 every scenario is run with. See item 9's suite for why."""
    return BeamSearch(
        agent_grammar(),
        table_fit(search_table(), simulator(GRAMMAR)),
        width=3,
        levels=1,
    )


def _system(name: str) -> ResearchSystem:
    """Return the SPEC §5 system with this identifier."""
    if name == "V1":
        return BOEDOnly(closed_set())
    if name == "B1":
        return PPCOnly()
    if name == "B4":
        return Retrieval(closed_set())
    if name == "B5":
        return _beam_search()
    raise AssertionError(f"no system {name!r}")


@lru_cache(maxsize=1)
def runs() -> tuple[tuple[ScenarioRun, ClaimContext], ...]:
    """Return every baseline run on the slice, with the context to judge it in.

    The table is threaded from run to run and saved at the end, exactly as item
    9's suite does it: a structure a system proposes is simulated once per
    machine rather than once per scenario.
    """
    table = gate_table()
    built: list[tuple[ScenarioRun, ClaimContext]] = []
    for name in SYSTEM_NAMES:
        system = _system(name)
        for scenario in slice_scenarios():
            graph = null_seeded_graph(GRAMMAR, METRICS, table, slice_designs()[0])
            engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
            store = ExperimentStore.in_memory()
            runner = executor(GRAMMAR, store=store, budget=scenario.budget)
            run = run_scenario(
                scenario, system, executor=runner, engine=engine, graph=graph
            )
            built.append(
                (
                    run,
                    ClaimContext(
                        graph=run.graph,
                        evidence=run.evidence,
                        program=runner.reference,
                        posterior=FrozenDict[HypothesisId, Probability](
                            run.diagnosis.distribution
                        ),
                    ),
                )
            )
            table = engine.table
    save_gate_table(table)
    return tuple(built)


@lru_cache(maxsize=1)
def slice_run_claims() -> tuple[Verdict, ...]:
    """Return a verdict for every claim the baseline runs afford."""
    verdicts: list[Verdict] = []
    for run, context in runs():
        for claim in claims_from_run(run):
            verdicts.append(verify(claim, context))
    return tuple(verdicts)


def claims() -> tuple[Claim, ...]:
    """Return the claim population itself, for inspection and for reporting."""
    return tuple(claim for run, _ in runs() for claim in claims_from_run(run))
