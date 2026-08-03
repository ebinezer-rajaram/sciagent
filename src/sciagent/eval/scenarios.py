"""What a research system is asked to investigate (SPEC §4.5).

A :class:`Scenario` is *harness-side* data. It carries the ground truth, which is
exactly why no system ever receives one: a system is handed a
:class:`~sciagent.systems.base.Investigation`, built by :meth:`Scenario.brief`,
and that type has no path to :attr:`Scenario.truth`. The separation is structural
rather than a convention, so a baseline cannot read the answer by accident and a
future LLM system cannot read it on purpose.

SPEC §11 assigns the twelve slice scenarios to item 11. What lives here is the
*type*; item 9 adds S1-S10 as instances under ``environments/pointproc`` because
item 9's gate is to run baselines on them. Oracle policy lengths, S11 and S12
remain item 11's, which is what that item's gate actually names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sciagent.core.errors import InvestigationError, MalformedDesignError
from sciagent.core.types import ScenarioId, Seed
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.registry.budget import Budget

if TYPE_CHECKING:
    from sciagent.core.edits import Defect

__all__ = ["SCENARIO_CLASSES", "Scenario", "ScenarioClass"]


#: SPEC §4.5's classification of the twelve slice scenarios. Carried on the
#: scenario because §8 makes the primary scoring dimension task-dependent, so
#: which class a scenario is in decides how its result is read.
ScenarioClass = Literal[
    "single",
    "confounded",
    "compound",
    "null",
    "non_identifiable",
    "out_of_library",
    "garden_path",
]

SCENARIO_CLASSES: tuple[ScenarioClass, ...] = (
    "single",
    "confounded",
    "compound",
    "null",
    "non_identifiable",
    "out_of_library",
    "garden_path",
)


@dataclass(frozen=True, slots=True)
class Scenario:
    """One investigation task: a hidden truth, a design space, and an allowance.

    Guarantees the design set is non-empty and free of duplicate template ids, so
    a system offered this scenario has something to run and cannot be handed two
    designs that address the same template under different objects.

    ``truth`` is what the environment was actually built with. It is used to
    execute experiments -- every design runs *against* the defect -- and to score
    the result afterwards. A system never sees it; see :meth:`brief`.
    """

    id: ScenarioId
    scenario_class: ScenarioClass
    truth: Defect
    designs: tuple[ExperimentDesign, ...]
    budget: Budget
    seed: Seed
    rationale: str = ""
    """What the scenario is for, in SPEC §4.5's terms. Documentation, not data."""

    def __post_init__(self) -> None:
        if not self.designs:
            raise MalformedDesignError(
                f"scenario {self.id!r} offers no design, so nothing can be "
                f"investigated in it"
            )
        seen: dict[str, ExperimentDesign] = {}
        for design in self.designs:
            if str(design.id) in seen:
                raise MalformedDesignError(
                    f"scenario {self.id!r} offers template {design.id!r} twice; a "
                    f"template id names a design uniquely"
                )
            seen[str(design.id)] = design

    @property
    def is_null(self) -> bool:
        """Return whether the truth is the empty edit set (SPEC §4.5 S9)."""
        return not self.truth

    def design(self, template_id: str) -> ExperimentDesign:
        """Return the offered design with this template id, or raise.

        Raises :class:`~sciagent.core.errors.InvestigationError` for a design the
        scenario does not offer, which is how a system asking for an experiment
        outside its allowance is stopped rather than quietly served.
        """
        for candidate in self.designs:
            if str(candidate.id) == template_id:
                return candidate
        raise InvestigationError(
            f"scenario {self.id!r} does not offer template {template_id!r}; it "
            f"offers {sorted(str(d.id) for d in self.designs)!r}"
        )

    def brief(self) -> Sequence[ExperimentDesign]:
        """Return what a system may be told: the design space, and nothing else.

        Deliberately not a projection of this dataclass. Anything a system is
        allowed to know is assembled by
        :class:`~sciagent.systems.base.Investigation` from this call and from the
        engine, so adding a field here cannot accidentally widen what leaks.
        """
        return self.designs
