"""The slice's investigation tasks, S1-S12 (SPEC §4.5).

S1-S10 arrived with backlog item 9, whose gate was to run the conventional
baselines on them. S11, S12 and the design space they need arrived with item 11,
whose gate is oracle policy lengths -- and a policy length over a design space
holding no discriminating experiment would have measured nothing.

*S5-S7's confounding is inherited, not separately tuned.* SPEC §4.2 calibrates
all four mechanisms to be mutually indistinguishable under dispersion
diagnostics, so a scenario does not have to make its alternative plausible --
it already is. What distinguishes S5-S7 from S1-S4 is the budget and how the
result is read, not a second calibration.

*S10's budget is derived, not asserted.* SPEC §4.5 defines S10 by a budget
"below the discriminating threshold"; where the threshold sits is what
:mod:`sciagent.eval.oracle` computes, and ``tests/test_oracle.py`` is where the
budget is held below it.

The two that carry the weight
-----------------------------

**S11** is out-of-library by the mechanical definition of SPEC §3.2: its truth is
licensed by ``edit_grammar`` and not by ``agent_grammar``, so no system whose
hypothesis graph carries the agent's grammar can propose it, whatever it
believes. Detecting the inadequacy and extending the space is the task.

**S12** is a garden path. Its *truth* is regime switching; its *nuisance* is an
observation process that censors part of every cycle, which makes the first
diagnostics read as seasonality -- see
:data:`~environments.pointproc.mechanisms.OBSERVATION_CENSORING` for the measured
signature, and for the two things it was calibrated not to destroy: seasonality
must stay refutable, and the intervention must stay readable. The nuisance is
executed and never scored (:attr:`~sciagent.eval.scenarios.Scenario.nuisance`).
"""

from __future__ import annotations

from functools import lru_cache

from environments.pointproc.mechanisms import (
    OBSERVATION_CENSORING,
    SEASONALITY,
    SIZE_EXCITATION,
    SIZE_MIXTURE,
    defect,
    mechanism_defect,
)
from environments.pointproc.outcomes import slice_designs
from sciagent.core.edits import Defect
from sciagent.core.types import ScenarioId, Seed
from sciagent.eval.scenarios import Scenario, ScenarioClass
from sciagent.registry.budget import Budget

__all__ = [
    "NON_IDENTIFIABLE_BUDGET",
    "STANDARD_BUDGET",
    "scenario",
    "slice_scenarios",
]

#: Experiments a scenario is normally allowed. Comfortably above the three
#: stages SPEC §4.2's minimum discriminating plan needs, so that a system has
#: room to spend an experiment badly and recover, and low enough that spending
#: several badly costs the investigation.
STANDARD_BUDGET = 8.0

#: Experiments S10 is allowed. SPEC §4.5 defines the scenario by a budget below
#: the discriminating threshold, and the threshold is
#: :func:`sciagent.eval.oracle.oracle_policy_length` -- what an optimal policy
#: needs on the same world with the same designs. ``tests/test_oracle.py`` holds
#: this number strictly below that one, so the scenario is non-identifiable by
#: measurement rather than by assertion.
NON_IDENTIFIABLE_BUDGET = 2.0

#: Seed of each scenario, fixed so a scenario is a reproducible artefact. Drawn
#: apart by scenario so that two scenarios sharing a truth -- S1 and S5, S2 and
#: S7 -- are not the same investigation twice.
_SEEDS: dict[str, int] = {
    "S1": 20260901,
    "S2": 20260902,
    "S3": 20260903,
    "S4": 20260904,
    "S5": 20260905,
    "S6": 20260906,
    "S7": 20260907,
    "S8": 20260908,
    "S9": 20260909,
    "S10": 20260910,
    "S11": 20260911,
    "S12": 20260912,
}

#: ``(class, truth, budget, rationale)`` per scenario, in SPEC §4.5's order.
_DEFINITIONS: tuple[tuple[str, ScenarioClass, str, float, str], ...] = (
    ("S1", "single", "hawkes", STANDARD_BUDGET, "basic competence"),
    ("S2", "single", "regime_switching", STANDARD_BUDGET, "basic competence"),
    ("S3", "single", "seasonality", STANDARD_BUDGET, "basic competence"),
    ("S4", "single", "poisson_mixture", STANDARD_BUDGET, "basic competence"),
    ("S5", "confounded", "hawkes", STANDARD_BUDGET, "intervention planning"),
    ("S6", "confounded", "seasonality", STANDARD_BUDGET, "conditional analysis"),
    ("S7", "confounded", "regime_switching", STANDARD_BUDGET, "multi-scale reasoning"),
    ("S8", "compound", "_compound", STANDARD_BUDGET, "decomposition"),
    ("S9", "null", "_null", STANDARD_BUDGET, "abstention"),
    (
        "S10",
        "non_identifiable",
        "hawkes",
        NON_IDENTIFIABLE_BUDGET,
        "calibrated insufficiency",
    ),
    (
        "S11",
        "out_of_library",
        "_size_excitation",
        STANDARD_BUDGET,
        "stage A detection, stage B extension",
    ),
    ("S12", "garden_path", "regime_switching", STANDARD_BUDGET, "plan revision"),
)

#: The nuisance each scenario carries, where it carries one. Only S12 does.
_NUISANCES: dict[str, Defect] = {"S12": defect(OBSERVATION_CENSORING)}


def _truth(name: str) -> Defect:
    """Return the defect a scenario's ground truth names."""
    if name == "_null":
        return frozenset()
    if name == "_compound":
        # SPEC §4.5 S8: seasonality plus a size-distribution mixture. Two edits
        # on two different components, which is what makes decomposition -- and
        # not discrimination -- the capability under test.
        return defect(SEASONALITY, SIZE_MIXTURE)
    if name == "_size_excitation":
        # SPEC §4.5 S11: rate excited by prior mark sizes. In edit_grammar and
        # not in agent_grammar, which is what makes it out-of-library.
        return defect(SIZE_EXCITATION)
    return mechanism_defect(name)


@lru_cache(maxsize=1)
def slice_scenarios() -> tuple[Scenario, ...]:
    """Return S1-S12, in specification order.

    Guarantees a fixed set of ids, truths, nuisances, budgets and seeds, so a
    scenario is a reproducible artefact and two runs of one system on one
    scenario perform byte-identical executions.
    """
    designs = slice_designs()
    stage_a = next(
        design for design in designs if str(design.id) == "query:size_gap_correlation"
    )
    return tuple(
        Scenario(
            id=ScenarioId(name),
            scenario_class=scenario_class,
            truth=_truth(truth),
            designs=designs,
            budget=Budget(total=budget),
            seed=Seed(_SEEDS[name]),
            nuisance=_NUISANCES.get(name, frozenset()),
            rationale=rationale,
            stage_a=stage_a,
        )
        for name, scenario_class, truth, budget, rationale in _DEFINITIONS
    )


def scenario(name: str) -> Scenario:
    """Return one scenario by id, or raise ``KeyError``."""
    for candidate in slice_scenarios():
        if str(candidate.id) == name:
            return candidate
    raise KeyError(f"no slice scenario named {name!r}")
