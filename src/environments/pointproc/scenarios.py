"""The slice's investigation tasks, S1-S10 (SPEC §4.5).

What is here and what is not
----------------------------

SPEC §11 assigns the twelve scenarios to item 11, whose gate is *oracle policy
lengths* -- exhaustive dynamic programming where tractable, a planning-baseline
lower bound otherwise. Item 9's gate is to run B1, B4 and B5 on S1-S10, which
needs the scenarios to exist but needs nothing about the oracle. So S1-S10 are
defined here and **S11, S12 and every oracle policy length remain item 11's**.
Two consequences are worth stating rather than discovering later.

*S10's budget is asserted, not derived.* SPEC §4.5 defines S10 as
"budget below the discriminating threshold". Where that threshold sits is
exactly what item 11's dynamic programming computes. The budget here is set
below what the three-stage plan of SPEC §4.2 needs, on the argument given at
:data:`NON_IDENTIFIABLE_BUDGET`, and item 11 should confirm or move it.

*S5-S7's confounding is inherited, not separately tuned.* SPEC §4.2 calibrates
all four mechanisms to be mutually indistinguishable under dispersion
diagnostics, so a scenario does not have to make its alternative plausible --
it already is. What distinguishes S5-S7 from S1-S4 is the budget and how the
result is read, not a second calibration.

The intervention gap
--------------------

:func:`~environments.pointproc.outcomes.slice_designs` is observational: it
holds no ``ForceArrival``, because the empirical table is not calibrated on one.
SPEC §4.2 makes a forced arrival the *only* thing separating Hawkes from regime
switching, so on S5 and S10 no system offered these designs can do better than
split its belief between the two. That is a real ceiling and it is why S10 is
non-identifiable here for a reason stronger than its budget. Adding the
intervention template belongs with item 11, which needs the full design space
for its dynamic programming anyway.
"""

from __future__ import annotations

from functools import lru_cache

from environments.pointproc.mechanisms import (
    SEASONALITY,
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

#: Experiments a scenario is normally allowed. Twice the four observational
#: designs, so every design can be run and one repeat spent on whichever the
#: system thinks is worth repeating -- enough for a policy to express itself,
#: and little enough that spending it badly costs something.
STANDARD_BUDGET = 8.0

#: Experiments S10 is allowed. SPEC §4.2's minimum discriminating plan is three
#: stages, and the third is an intervention that these designs do not contain;
#: two experiments cannot complete even the first two stages against a pair
#: calibrated to be indistinguishable under dispersion. Item 11's dynamic
#: programming should confirm this is genuinely below the threshold rather than
#: merely small.
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
)


def _truth(name: str) -> Defect:
    """Return the defect a scenario's ground truth names."""
    if name == "_null":
        return frozenset()
    if name == "_compound":
        # SPEC §4.5 S8: seasonality plus a size-distribution mixture. Two edits
        # on two different components, which is what makes decomposition -- and
        # not discrimination -- the capability under test.
        return defect(SEASONALITY, SIZE_MIXTURE)
    return mechanism_defect(name)


@lru_cache(maxsize=1)
def slice_scenarios() -> tuple[Scenario, ...]:
    """Return S1-S10, in specification order.

    Guarantees a fixed set of ids, truths, budgets and seeds, so a scenario is a
    reproducible artefact and two runs of one system on one scenario perform
    byte-identical executions.
    """
    designs = slice_designs()
    return tuple(
        Scenario(
            id=ScenarioId(name),
            scenario_class=scenario_class,
            truth=_truth(truth),
            designs=designs,
            budget=Budget(total=budget),
            seed=Seed(_SEEDS[name]),
            rationale=rationale,
        )
        for name, scenario_class, truth, budget, rationale in _DEFINITIONS
    )


def scenario(name: str) -> Scenario:
    """Return one scenario by id, or raise ``KeyError``."""
    for candidate in slice_scenarios():
        if str(candidate.id) == name:
            return candidate
    raise KeyError(f"no slice scenario named {name!r}")
