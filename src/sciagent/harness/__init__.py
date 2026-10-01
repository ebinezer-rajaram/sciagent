"""The domain-independent agent harness (SPEC §4, §4.0, §4.4).

* :mod:`~sciagent.harness.tools` -- ``ToolSpec``/``ToolLayer``: the agent's only
  path to environment state, with budgets enforced in the layer.
* :mod:`~sciagent.harness.ledger` -- the prediction ledger (test 10).
* :mod:`~sciagent.harness.record` -- the append-only, hash-chained session record.
* :mod:`~sciagent.harness.replay` -- offline replay against a fresh layer (test 9).
* :mod:`~sciagent.harness.live` -- the Claude Agent SDK runner (imports the SDK).
* :mod:`~sciagent.harness.scripted` -- a scripted model for the same loop.

This module deliberately re-exports only the SDK-free half, so importing the
package -- and replaying a record -- never imports ``claude_agent_sdk``.
"""

from __future__ import annotations

from sciagent.harness.ledger import Prediction, PredictionLedger
from sciagent.harness.record import SessionRecord
from sciagent.harness.replay import ReplayReport, replay
from sciagent.harness.tools import (
    BudgetMeter,
    ToolCall,
    ToolLayer,
    ToolOutput,
    ToolSpec,
)

__all__ = [
    "BudgetMeter",
    "Prediction",
    "PredictionLedger",
    "ReplayReport",
    "SessionRecord",
    "ToolCall",
    "ToolLayer",
    "ToolOutput",
    "ToolSpec",
    "replay",
]
