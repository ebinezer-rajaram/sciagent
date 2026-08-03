"""Running a system on a scenario, and checking that it played fair.

:func:`run_scenario` is where SPEC's second invariant stops being a convention
and becomes a check. A system returns a :class:`~sciagent.core.types.Diagnosis`;
the harness re-derives one from the engine and refuses the run if the two
disagree. A system that fabricated a posterior, inflated its own leading
hypothesis, or nominated its own supporting evidence fails here rather than
producing a plausible number nobody audits.

The experiment matrix of SPEC §9 is item 15's and is not here. What is here is
the single run that matrix will be made of.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from sciagent.core.edits import Defect
from sciagent.core.errors import InvestigationError
from sciagent.core.types import Diagnosis, HypothesisId
from sciagent.eval.scenarios import Scenario
from sciagent.eval.scoring import ClosedWorldScore, closed_world_score
from sciagent.experiments.executor import Executor
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.inference.interface import PPCResult
from sciagent.systems.base import Investigation, ResearchSystem, diagnose

__all__ = ["ScenarioRun", "run_scenario"]


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """One system's run on one scenario, and everything it is judged on."""

    scenario: Scenario
    system: str
    diagnosis: Diagnosis
    score: ClosedWorldScore
    ppc: PPCResult
    """The Stage A verdict. Carried here rather than on the diagnosis because
    SPEC §3.4 has no field for it, and B1's whole output is this flag."""

    experiments: int
    """How many experiments were actually run, which may be under the budget."""

    proposed: Mapping[HypothesisId, Defect]
    """Structures the system introduced. Empty for V1 and B1 by construction."""

    structural_distance: float
    """Grammar distance from the truth to the nearest structure entertained.

    Reported alongside the closed-world score because the two can disagree
    sharply, and reporting only the first would misrepresent a system. B5
    searches a grammar whose parameters live on a 64-point grid and whose
    corners are what a bounded enumeration visits, so it routinely lands in the
    *right structural cell* with the wrong parameters: exact-match mass of zero,
    distance far below a whole edit. SPEC §5 warns against a baseline that loses
    because it was built carelessly, and scoring a near miss as a total failure
    would be exactly that.

    ``inf`` if nothing was entertained.
    """

    @property
    def spent(self) -> float:
        """Return what the run cost, in experiments."""
        return float(self.experiments)


def run_scenario(
    scenario: Scenario,
    system: ResearchSystem,
    *,
    executor: Executor,
    engine: EmpiricalTableEngine,
    graph: HypothesisGraph,
) -> ScenarioRun:
    """Run one system on one scenario and score what it concluded.

    The caller builds the executor, engine and graph, because all three are
    environment-shaped and ``sciagent`` may not import an environment. What this
    function owns is the part that must be identical for every system: the
    investigation it is handed, the audit of what it returned, and the score.

    Guarantees the returned diagnosis is the one the engine's own state implies.
    A system whose report differs -- in its distribution, its abstain or null
    mass, or its supporting sets -- raises
    :class:`~sciagent.core.errors.InvestigationError` rather than being scored,
    which is SPEC's second invariant enforced by assertion rather than by
    comment.
    """
    investigation = Investigation(
        scenario_id=scenario.id,
        designs=scenario.designs,
        truth=scenario.truth,
        executor=executor,
        engine=engine,
        graph=graph,
        seed=scenario.seed,
    )
    reported = system.investigate(investigation)

    expected = diagnose(
        scenario.id,
        engine,
        proposed_edits=investigation.proposed,
        residual_candidates=reported.residual_candidates,
    )
    _audit(system, reported, expected)

    edits = engine_edits(engine)
    return ScenarioRun(
        scenario=scenario,
        system=system.name,
        diagnosis=reported,
        score=closed_world_score(reported, scenario.truth, edits),
        ppc=engine.ppc(),
        experiments=len(investigation.history),
        proposed=investigation.proposed,
        structural_distance=min(
            (
                investigation.graph.grammar.distance(edits[node_id], scenario.truth)
                for node_id in sorted(edits)
            ),
            default=math.inf,
        ),
    )


def engine_edits(engine: EmpiricalTableEngine) -> Mapping[HypothesisId, Defect]:
    """Return the structure of every hypothesis an engine holds."""
    return {node_id: engine.program_edit(node_id) for node_id in engine.hypotheses}


def _audit(system: ResearchSystem, reported: Diagnosis, expected: Diagnosis) -> None:
    """Raise unless ``reported`` is the diagnosis the engine's state implies.

    ``residual_candidates`` is excluded from the comparison: it is the one field
    a system legitimately authors beyond ``proposed_edits``, being a statement
    about what it would look at next rather than a number. Everything else must
    match exactly -- not approximately, because both sides are computed by the
    same function from the same state, so any difference at all means the
    reported value did not come from there.
    """
    if reported.scenario_id != expected.scenario_id:
        raise InvestigationError(
            f"system {system.name!r} reported scenario {reported.scenario_id!r} but "
            f"was run on {expected.scenario_id!r}"
        )
    for field, mine, theirs in (
        ("distribution", reported.distribution, expected.distribution),
        ("abstain_mass", reported.abstain_mass, expected.abstain_mass),
        ("null_mass", reported.null_mass, expected.null_mass),
        ("proposed_edits", reported.proposed_edits, expected.proposed_edits),
        ("supporting", reported.supporting, expected.supporting),
    ):
        if mine != theirs:
            raise InvestigationError(
                f"system {system.name!r} reported {field} {mine!r}, but the engine's "
                f"state implies {theirs!r}. Build a diagnosis with "
                f"sciagent.systems.base.diagnose; no system may author a number "
                f"(SPEC §1, second invariant)"
            )
