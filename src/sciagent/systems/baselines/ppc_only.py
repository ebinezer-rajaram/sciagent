"""B1: a posterior-predictive-check detector that proposes nothing (SPEC §5).

The Stage A floor. B1 spends its budget on a fixed rotation through the designs
it is offered, records everything, and stops. It never introduces a hypothesis,
so on any scenario whose truth is outside the graph it has been given, its
posterior stays where it started and its only informative output is the PPC
verdict: *the hypotheses in play do not explain what was seen*.

That verdict is the number SPEC §12's criterion 4 measures an LLM against --
"detects inadequacy on S11 at a rate at least matching B1" -- which is why it is
a floor and not a competitor. Beating B1 at detection is the minimum, not the
result.

The verdict is not carried on the :class:`~sciagent.core.types.Diagnosis`: SPEC
§3.4 has no field for it, and inventing one would put a B1-shaped hole in a type
every system shares. It is read off the run by
:class:`~sciagent.eval.campaign.ScenarioRun`.
"""

from __future__ import annotations

from sciagent.core.types import Diagnosis
from sciagent.systems.base import Investigation

__all__ = ["PPCOnly"]


class PPCOnly:
    """SPEC §5's B1.

    Guarantees nothing is ever proposed: the returned diagnosis carries an empty
    ``proposed_edits``, and this class holds no path to
    :meth:`~sciagent.systems.base.Investigation.propose`.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """Return SPEC §5's identifier."""
        return "B1"

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Rotate through the offered designs until the budget is gone.

        A rotation rather than a selection, deliberately: B1 exists to measure
        what detection is worth *without* experiment design, so giving it a
        policy would confound the floor it is supposed to establish. The rotation
        is in the scenario's design order and repeats, so a budget larger than
        the design set buys repeat measurements under fresh seeds.
        """
        designs = investigation.designs
        step = 0
        while investigation.affords():
            investigation.run(designs[step % len(designs)])
            step += 1
        return investigation.conclude()
