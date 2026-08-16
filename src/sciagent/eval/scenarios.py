"""What a research system is asked to investigate (SPEC §4.5).

A :class:`Scenario` is *harness-side* data. It carries the ground truth, which is
exactly why no system ever receives one: a system is handed a
:class:`~sciagent.systems.base.Investigation`, built by :meth:`Scenario.brief`,
and that type has no path to :attr:`Scenario.truth`. The separation is structural
rather than a convention, so a baseline cannot read the answer by accident and a
future LLM system cannot read it on purpose.

SPEC §11 assigns the twelve slice scenarios to item 11. What lives here is the
*type*; the instances live under ``environments/pointproc``, since a scenario is
made of an environment's edits and this package may not import one.

Truth and nuisance
------------------

:attr:`Scenario.truth` is what the investigation is *about*. :attr:`nuisance` is
everything else the environment was built with: it is executed, so it shapes
every measurement, and it is never scored. Scenario S12 is why the distinction
exists -- SPEC §4.5 gives its ground truth as regime switching "plus an
observation-level censoring nuisance", and §12 criterion 7 asks a system to
"recover the correct diagnosis" there, which is the regime switching and not the
censoring. Folding the nuisance into the truth would have made S12 a
decomposition task, which is what S8 already is.
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

    ``truth`` is the structure the investigation is about: experiments run
    against it and the result is scored against it. A system never sees it; see
    :meth:`brief`.
    """

    id: ScenarioId
    scenario_class: ScenarioClass
    truth: Defect
    designs: tuple[ExperimentDesign, ...]
    budget: Budget
    seed: Seed
    nuisance: Defect = frozenset()
    """Structure the environment also carries, and that nothing is scored on.

    Executed with the truth -- see :attr:`executed` -- so it shapes every
    measurement a system takes, and absent from every score, so a system is
    neither credited for naming it nor penalised for not. SPEC §4.5's S12 is the
    case: a censoring observation process that makes the data look periodic, over
    a truth that is not.
    """

    rationale: str = ""
    """What the scenario is for, in SPEC §4.5's terms. Documentation, not data."""

    stage_a: ExperimentDesign | None = None
    """A design the *framework* runs once, before the system sees anything.

    SPEC F5 gives both experiment selection and inadequacy detection to
    conventional methods, which reads as though the two shared an interest. They
    do not, and the measurement is in ``docs/DECISIONS.md``: a design that
    detects inadequacy of a hypothesis space is by construction uninformative
    *within* that space, so one-step greedy BOED -- which maximises expected
    information gain about the entertained set -- ranks it last and never selects
    it. Measured over all twelve slice scenarios, the mark-arrival design was
    chosen zero times, and S11's posterior predictive p-value was identical to
    its value before that design existed.

    So Stage A gets an allocation the system does not control and cannot spend
    elsewhere. It is deliberately *not* an experiment: nothing is registered, no
    budget is charged, and it never enters the evidence index, so a system can
    neither cite it nor be credited for it. It is framework apparatus on the same
    footing as the posterior predictive check it feeds -- the executor already
    draws this line, where a table-building execution "is not an experiment".

    On the scenario rather than on :func:`~sciagent.eval.campaign.run_scenario`'s
    signature so that no caller can omit it for one system and supply it for
    another. An asymmetry there would bias every §9 comparison, and would do it
    invisibly.
    """

    def __post_init__(self) -> None:
        overlap = {edit.target for edit in self.truth} & {
            edit.target for edit in self.nuisance
        }
        if overlap:
            raise MalformedDesignError(
                f"scenario {self.id!r} has a truth and a nuisance on the same "
                f"component(s) {sorted(overlap)!r}; the two would compile to one "
                f"ambiguous edit and the scenario would not be executable"
            )
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

    @property
    def executed(self) -> Defect:
        """Return the defect the environment actually runs: truth and nuisance.

        What every experiment is performed against, and therefore what every
        measurement is a measurement of. Distinct from :attr:`truth`, which is
        what the result is scored against -- see this module's docstring.
        """
        return self.truth | self.nuisance

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
