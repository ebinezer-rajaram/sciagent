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
from sciagent.experiments.dsl import ExperimentDesign, is_intervention
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

    held_out: tuple[ExperimentDesign, ...] = ()
    """The designs SPEC §8's D2, D3 and D5 are scored on, fixed before any arm runs.

    **Three dimensions, not two.** D2 and D3 are the ones §8 names against the
    battery, and :func:`~sciagent.eval.scoring._enabled_value` reads it too --
    D5 is the value of the experiments a candidate leaves available, and the
    battery is the set it is drawn from. Raised by review against an earlier
    version of this docstring that named only D2 and D3, which under-states
    what a change here moves.

    A subset of :attr:`designs`, and **preregistered**: it is a property of the
    scenario, so two arms with different run histories are graded on one question
    set. That is the whole of gate A27, and what it replaces is a battery derived
    per run from whatever designs an investigation left unused -- under which
    ``n_held_out`` was {3,2} for the V-arms, {2} for B4/B5 and 0 for B1, whose D3
    was therefore ``nan`` on every recorded S11 row. No cross-arm D2/D3
    comparison was clean, including the §9 contrast that is the point of the
    matrix.

    **Not withheld from the offer, and that is a deliberate trade.** The slice
    holds exactly one intervention, and SPEC §4.2 makes it the only design that
    separates Hawkes self-excitation from latent regime switching, so reserving
    it would break S5's intervention planning, the oracle policy lengths behind
    A24, and S10's derived budget. The cost is that an arm which ran a battery
    design is scored on a question it asked. §8's "unused during the
    investigation" is honoured in intent rather than mechanically -- D2 and D3
    read simulated rows for candidate against truth rather than the run's own
    observations, so what leaks is indirect -- and the alternative bought a
    stronger guarantee by making the question set depend on the arm, which is
    worse.

    Empty means undeclared, and gives D2 and D3 as ``nan``: the honest reading of
    a question never asked. Every scenario the matrix scores declares one; the
    default exists so a scenario built for a test that scores nothing need not.
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
        if self.held_out:
            # By design equality, not by id. `ExperimentDesign.id` is documented
            # as **not injective** -- `n_events` is deliberately absent from it --
            # so an id check would admit a battery member that shares an offered
            # design's id and differs in run length, and it would then be scored
            # off the offered design's table row. A diagnostic's sampling
            # distribution depends on how much data it saw, so that is a wrong
            # number rather than a near-enough one.
            offered = set(self.designs)
            stray = sorted(
                str(design.id) for design in self.held_out if design not in offered
            )
            if stray:
                raise MalformedDesignError(
                    f"scenario {self.id!r} holds out template(s) {stray!r} that it "
                    f"does not offer; D2, D3 and D5 are read off the table rows "
                    f"the scenario's own designs build, so a battery member "
                    f"outside the offer has no row for either the candidate or "
                    f"the truth. A member sharing an offered id but differing in "
                    f"n_events is outside the offer for this purpose"
                )
            repeated = sorted(
                str(design.id)
                for index, design in enumerate(self.held_out)
                if design in self.held_out[:index]
            )
            if repeated:
                raise MalformedDesignError(
                    f"scenario {self.id!r} holds out template(s) {repeated!r} more "
                    f"than once. D2 and D3 are means over the battery and D5 is a "
                    f"maximum over it, so a repeated member does not merely "
                    f"duplicate a row -- it weights one question twice against the "
                    f"others, and the reader of a dimension has no way to see it. "
                    f"The offered designs are refused for the same reason a few "
                    f"lines above"
                )
            if not any(is_intervention(design) for design in self.held_out):
                raise MalformedDesignError(
                    f"scenario {self.id!r} holds out "
                    f"{sorted(str(design.id) for design in self.held_out)!r}, none "
                    f"of which is an intervention; SPEC section 8 defines D3 over "
                    f"a held-out *intervention* battery, and a battery of pure "
                    f"observation measures something else under that name"
                )

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

        Deliberately not a projection of this dataclass. The *design space* a
        system is allowed to know comes through here and nowhere else, so adding
        a field to :class:`Scenario` cannot accidentally widen it --
        :attr:`held_out` was the first field added since this was written, and it
        does not appear below.

        Not the whole of what reaches an
        :class:`~sciagent.systems.base.Investigation`, which an earlier version of
        this paragraph implied and review corrected.
        :func:`~sciagent.eval.campaign.run_scenario` also passes :attr:`id`,
        :attr:`executed`, :attr:`seed` and the Stage A template id. Three of those
        four are private on the investigation with no accessor, the fourth
        (``scenario_id``) is public and reaches no prompt, and none is a number or
        a battery -- but "this call and the engine" was not the full list.
        """
        return self.designs
