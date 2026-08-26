"""B1: a posterior-predictive-check detector that proposes nothing (SPEC §5).

The Stage A floor. B1 spends its budget on a fixed rotation through the designs
it is offered, records everything, and stops. It never introduces a hypothesis,
so on any scenario whose truth is outside the graph it has been given, its
posterior stays where it started and its only informative output is the PPC
verdict: *the hypotheses in play do not explain what was seen*.

That verdict *was* what SPEC §12's criterion 4 measured an LLM against, and is
no longer. The criterion was re-specified on 2026-08-21 as C1 -- power against
size on the named Stage A probe -- and gate A29 made the harness evaluate that
probe for every arm alike, so what criterion 4 now reads is
:attr:`~sciagent.eval.campaign.ScenarioRun.probe` and not this. B1's whole-record
verdict remains :attr:`~sciagent.eval.campaign.ScenarioRun.ppc`, is still
reported, and is still the Stage A floor in the sense that matters to SPEC §5 --
what detection is worth without experiment design.

**"Beating B1 at detection" is no longer a thing an arm can do *on the probe***,
and that is a property of the re-specification rather than of B1: every arm now
reads one instrument, so every arm's probe rate is identical. Criterion 4 was
reworded absolutely on 2026-08-26 and moved to §12's Infrastructure block for
exactly that reason -- a reading shared by every arm grades the instrument, and
no threshold over it can grade an agent. See `docs/SPEC.md` §12 criterion 4 and
:func:`~sciagent.eval.report.criterion_four`.

The qualifier is load-bearing: the arms still differ on the *whole-record*
check, and B1 is not the floor there either. On S11 at one seed B1's
:attr:`~sciagent.eval.campaign.ScenarioRun.ppc` fires and V1's does not --
because B1 holds only the null, which makes its space inadequate on eleven of
twelve by construction rather than by detecting anything. That is the
multiplicity artefact `docs/OPEN-DECISIONS.md` §1 named as the reason the
original criterion 4 could not be read off this check.

Neither verdict is carried on the :class:`~sciagent.core.types.Diagnosis`: SPEC
§3.4 has no field for either, and inventing one would put a B1-shaped hole in a
type every system shares. Both are read off the run by
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
