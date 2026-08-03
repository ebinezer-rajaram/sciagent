"""Research systems: the things being evaluated (SPEC §5).

A system decides *structure* -- which hypotheses to entertain and which designs
to run. It never decides a number. Everything numeric in a
:class:`~sciagent.core.types.Diagnosis` is derived from the posterior engine by
:func:`sciagent.systems.base.diagnose`, and
:func:`sciagent.eval.campaign.run_scenario` re-derives it afterwards and refuses
a system whose report disagrees (SPEC's second invariant).

No LLM lives here until backlog item 12. The baselines under
:mod:`sciagent.systems.baselines` are conventional machinery, deliberately built
and validated before the agent exists.
"""

from __future__ import annotations
