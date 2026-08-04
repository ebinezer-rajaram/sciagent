"""Child process for the transcript layer's cross-process determinism arm.

Prints one ``label address`` line per constructed call. Deliberately a separate
process, for the reason ``tests/acceptance/determinism_child.py`` gives: hash
randomisation, dict insertion order and set iteration order are all per-process,
so an in-process repeat cannot detect a dependence on them.

That matters more here than almost anywhere else. A transcript address is
computed from a rendered brief, and the brief renders a ``Defect`` -- which is a
``frozenset``, whose iteration order is genuinely process-dependent. If
``render_brief`` ever stops sorting, this is the arm that notices, and the
failure it prevents is a recorded corpus that replays on the machine that
recorded it and misses everywhere else.

Not named ``test_*`` so pytest does not collect it.
"""

from __future__ import annotations

import sys

from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.llm import (
    call_address,
    render_brief,
    structural_menu,
    tool_schema,
)

#: Scenarios whose briefs are addressed. S1 and S12 between them cover an
#: uncensored world and a censored one, and S9's truth is the null, so the three
#: differ in what the observation section can contain.
SCENARIOS = ("S1", "S9", "S12")


def addresses() -> dict[str, str]:
    """Return one address per scenario, after two experiments and two proposals.

    Two library structures are entertained before the brief is rendered, so the
    hypotheses section holds more than the null and its ordering is exercised.
    """
    menu = structural_menu(AGENT_GRAMMAR)
    schema = tool_schema(menu)
    table = gate_table()
    library = closed_set()
    out: dict[str, str] = {}
    for name in SCENARIOS:
        the_scenario = scenario(name)
        graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
        engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
        investigation = Investigation(
            scenario_id=the_scenario.id,
            designs=the_scenario.designs,
            truth=the_scenario.executed,
            executor=executor(
                GRAMMAR,
                store=ExperimentStore.in_memory(),
                budget=the_scenario.budget,
            ),
            engine=engine,
            graph=graph,
            seed=the_scenario.seed,
        )
        entertain(
            investigation,
            {key: library[key] for key in ("hawkes", "regime_switching")},
        )
        for design in the_scenario.designs[:2]:
            investigation.run(design)
        out[name] = call_address(
            provider="child",
            model="child/1",
            system="system",
            brief=render_brief(investigation, menu),
            schema=schema,
            index=0,
        )
    return out


def main() -> int:
    for name, address in addresses().items():
        sys.stdout.write(f"{name} {address}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
