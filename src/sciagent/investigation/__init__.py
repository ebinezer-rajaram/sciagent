"""One agent investigation: the science tools, the prompts and the runner (SPEC §4).

* :mod:`~sciagent.investigation.world` -- the truth and its seeded draws
  (framework-side; never agent-reachable).
* :mod:`~sciagent.investigation.view` -- names and units as the agent sees
  them, named or anonymised (SPEC §5).
* :mod:`~sciagent.investigation.tools` -- the tool layer with budgets (§4.0)
  and the prediction ledger's evaluation (§4.4).
* :mod:`~sciagent.investigation.prompts` -- the AG-c and AG-o prompts.
* :mod:`~sciagent.investigation.runner` -- run, record and replay.

Domain-independent: the environment arrives as an
:class:`~sciagent.investigation.runner.EnvironmentSpec`.
"""

from __future__ import annotations

from sciagent.investigation.runner import (
    EnvironmentSpec,
    InvestigationOutcome,
    InvestigationSpec,
    build_lab,
    layer_factory,
    replay_investigation,
    run,
)
from sciagent.investigation.tools import Lab, PredictionOutcome
from sciagent.investigation.view import AgentView, make_view
from sciagent.investigation.world import Truth, TruthSpec, World

__all__ = [
    "AgentView",
    "EnvironmentSpec",
    "InvestigationOutcome",
    "InvestigationSpec",
    "Lab",
    "PredictionOutcome",
    "Truth",
    "TruthSpec",
    "World",
    "build_lab",
    "layer_factory",
    "make_view",
    "replay_investigation",
    "run",
]
