"""The oracle policy length of every slice scenario, computed once.

Backlog item 11's gate reads these numbers and ``scripts/status.py`` reports
them; both want the same computation, and it is expensive enough -- three
structures that no closed-set row covers must be simulated at the slice's full
replicate count -- that computing it twice would be the dominant cost of the
suite.

Kept out of ``tests/test_oracle.py`` for the reason ``tests/baseline_runs.py``
is kept out of ``tests/test_baselines_slice.py``: the runs are *material*, and a
module that both produces and judges them makes a failure ambiguous between the
two.

What the oracle is given
------------------------

Exactly what a conventional system starts from, so that its length is a bar
those systems could in principle meet:

* the **closed set** of SPEC §4.2 as its hypotheses, entertained through
  :func:`~sciagent.systems.base.entertain` -- the same call V1 and B4 make;
* the **agent grammar**'s prior, since the graph carries that grammar;
* the slice's six designs;
* and, as the world, the empirical row of the defect the scenario *executes* --
  truth and nuisance together -- which for S11 and S12 is not any hypothesis's
  row.
"""

from __future__ import annotations

from functools import lru_cache

from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table, save_gate_table

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    forced_design,
    simulator,
    slice_designs,
    slice_templates,
)
from environments.pointproc.scenarios import slice_scenarios
from sciagent.core.types import HypothesisId
from sciagent.eval.oracle import OraclePolicyLength, World, oracle_policy_length
from sciagent.eval.scenarios import Scenario
from sciagent.experiments.boed import table_predictive
from sciagent.experiments.dsl import defect_key
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph


def _entertained(
    scenario: Scenario, table: EmpiricalTable
) -> tuple[EmpiricalTableEngine, EmpiricalTable]:
    """Return an engine holding the closed set, and the table it grew into.

    Built through :class:`~sciagent.systems.base.Investigation` rather than by
    hand so that the hypotheses, their priors and their table rows arrive by the
    same path a system's would. No experiment is run: the investigation exists
    to be proposed into, and its executor never leaves the store empty-handed
    because it is never asked to.
    """
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    investigation = Investigation(
        scenario_id=scenario.id,
        designs=scenario.designs,
        truth=scenario.executed,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=scenario.budget
        ),
        engine=engine,
        graph=graph,
        seed=scenario.seed,
    )
    entertain(investigation, closed_set())
    return engine, engine.table


def _world(engine: EmpiricalTableEngine, scenario: Scenario) -> World:
    """Return the outcome distribution of the defect the scenario executes.

    :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.ensure_structure`
    fills the row without admitting a hypothesis, which is exactly right here:
    S11's mechanism and S12's censored world are things the environment does,
    not things anyone believes.
    """
    engine.ensure_structure(scenario.executed)
    table = engine.table
    return {
        template.id: table.probabilities(scenario.executed, template.id)
        for template in slice_templates()
    }


def _truth_hypotheses(
    engine: EmpiricalTableEngine, scenario: Scenario
) -> tuple[HypothesisId, ...]:
    """Return the hypotheses holding the scenario's true structure.

    Matched by :func:`~sciagent.experiments.dsl.defect_key`, as
    :func:`~sciagent.eval.scoring.closed_world_score` matches. Empty for S11,
    whose truth no hypothesis in the agent's grammar can express.
    """
    wanted = defect_key(scenario.truth)
    return tuple(
        node_id
        for node_id in engine.hypotheses
        if defect_key(engine.program_edit(node_id)) == wanted
    )


def _length(
    scenario: Scenario, table: EmpiricalTable, *, observational_only: bool = False
) -> tuple[OraclePolicyLength, EmpiricalTable]:
    """Return one scenario's oracle length, and the table it grew into."""
    engine, table = _entertained(scenario, table)
    world = _world(engine, scenario)
    templates = [
        template.id
        for template in slice_templates()
        if not (observational_only and template.id == forced_design().id)
    ]
    return (
        oracle_policy_length(
            scenario.id,
            templates,
            engine.posterior(),
            table_predictive(engine),
            truth=_truth_hypotheses(engine, scenario),
            world={key: world[key] for key in templates},
            seed=scenario.seed,
        ),
        engine.table,
    )


@lru_cache(maxsize=8)
def without_intervention(scenario_id: str) -> OraclePolicyLength:
    """Return one scenario's oracle length over the observational designs alone.

    What item 9 measured the cost of, from the other side: SPEC §4.2 makes the
    forced arrival the only discriminator of Hawkes from regime switching, so on
    a scenario turning on that pair the four query designs should leave an
    optimal policy worse off than five do. Item 11 added the fifth design on
    that argument, and this is the argument as a number.
    """
    scenario = next(s for s in slice_scenarios() if str(s.id) == scenario_id)
    length, table = _length(scenario, gate_table(), observational_only=True)
    save_gate_table(table)
    return length


@lru_cache(maxsize=1)
def oracle_lengths() -> dict[str, OraclePolicyLength]:
    """Return the oracle policy length of every slice scenario, by id.

    The table is threaded from scenario to scenario and saved at the end, as
    item 9's gate threads it: a row is a pure function of ``(defect, template,
    seed)``, so sharing changes what the computation costs and not what it
    concludes.
    """
    table = gate_table()
    lengths: dict[str, OraclePolicyLength] = {}
    for scenario in slice_scenarios():
        lengths[str(scenario.id)], table = _length(scenario, table)
    save_gate_table(table)
    return lengths
