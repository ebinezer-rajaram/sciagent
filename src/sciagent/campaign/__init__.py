"""Running a campaign: many (truth, seed, system) units, scored and stored (SPEC §7).

Built for the P3 pilot and meant to carry into P4.

* :mod:`~sciagent.campaign.plan` -- the units, their content addresses, and the
  order they run in.
* :mod:`~sciagent.campaign.store` -- the append-only, content-addressed results
  store: a :class:`~sciagent.registry.ledger.CampaignLedger` of numbers plus
  one exclusively-created JSON detail file per cell.
* :mod:`~sciagent.campaign.execute` -- one job in a worker process: build the
  investigation's world, run the non-LLM systems, score them (and score LLM
  sessions after the fact).
* :mod:`~sciagent.campaign.llm` -- one live (or scripted) LLM investigation,
  and what is read back from its record.
* :mod:`~sciagent.campaign.driver` -- the scheduler: a process pool for the
  non-LLM jobs, a few threads for LLM sessions, resumable, robust to a unit
  failing.
* :mod:`~sciagent.campaign.analysis` -- tables, controls and the go/no-go read
  from the store.

Domain-independent: the environment arrives as a
:class:`~sciagent.campaign.plan.CampaignEnvironment`.
"""

from __future__ import annotations
