"""V1: one-step-greedy BOED over a closed hypothesis set (SPEC §5).

Optimal selection with no representation change. V1 entertains exactly the
structures its library names, chooses each experiment by expected information
gain, and never proposes anything. It is the ceiling for a system that cannot
extend its own hypothesis space, and therefore the thing an out-of-library
scenario (S11) is supposed to defeat.

The selection itself is :func:`sciagent.experiments.boed.plan`, gated by A24.
Nothing here re-implements it.
"""

from __future__ import annotations

from collections.abc import Mapping

from sciagent.core.edits import Defect
from sciagent.core.types import Diagnosis, ExperimentTemplateId
from sciagent.experiments import boed
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.systems.base import Investigation, entertain

__all__ = ["BOEDOnly"]


class BOEDOnly:
    """SPEC §5's V1.

    Guarantees the library is entertained in full before any experiment is
    chosen, so selection is never made against a hypothesis space that is still
    being assembled, and that no structure outside the library is ever proposed.
    """

    __slots__ = ("_library",)

    def __init__(self, library: Mapping[str, Defect]) -> None:
        self._library = dict(library)

    @property
    def name(self) -> str:
        """Return SPEC §5's identifier."""
        return "V1"

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Entertain the library, then spend the budget on greedy selection.

        Guarantees every experiment is chosen by expected information gain and
        that the budget is spent exactly, one experiment at a time. The plan is
        run as a single trajectory rather than step by step so that the belief
        BOED selects against is the same belief it updates -- see
        :func:`sciagent.experiments.boed.greedy` on why designs are chosen with
        replacement.
        """
        entertain(investigation, self._library)
        by_id: dict[ExperimentTemplateId, ExperimentDesign] = {
            design.id: design for design in investigation.designs
        }

        def observe(_index: int, template: ExperimentTemplateId) -> int:
            # The step index is BOED's; the seed comes from the investigation,
            # which counts steps itself.
            design = by_id[template]
            result = investigation.run(design)
            return design.template().outcome.cell_of(result.result)

        steps = int(investigation.budget.remaining)
        if steps >= 1:
            boed.plan(
                investigation.engine,
                tuple(by_id),
                observe,
                steps=steps,
            )
        return investigation.conclude()
