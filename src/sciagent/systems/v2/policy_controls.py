"""The truth-aware FSD policy: positive control 5 (P4-PROPOSAL §4; Δ11).

Control 5 must show that the telling-diagnostic rate can separate a policy
that knows where the model is wrong from one that does not: a truth-aware FSD
must beat random on it before C3 or C5 is read. This policy is FSD
(``policies.fsd_table``) with the truth in place of the Wiener-Hopf reference:
``He²(q_par, q*)``, where ``q_par`` is the best library member when one is
given (``comparator``) and otherwise the top-weight entertained fit.

The truth enters only through the constructor, as it does for ORACLE
(``systems.py``), so it is visible at the call site. This module is never
imported by the tool layer or by ``policies.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sciagent.scoring.battery import Simulator
from sciagent.systems.v2.policies import (
    POLICY_MAX_EVENTS,
    REPLICATES,
    Choice,
    DecisionContext,
    Menu,
    choose,
    fsd_table,
)


@dataclass(frozen=True)
class TruthFSDPolicy:
    """FSD against the truth (module docstring). ``truth`` simulates the truth."""

    truth: Simulator
    seed: int
    comparator: Simulator | None = None
    replicates: int = REPLICATES
    max_events: int = POLICY_MAX_EVENTS
    name: str = "truthFSD"

    @property
    def config(self) -> str:
        """Canonical JSON of the configuration (not the truth, not the seed)."""
        return json.dumps(
            {
                "policy": self.name,
                "replicates": self.replicates,
                "max_events": self.max_events,
                "reference": "truth",
                "parametric": "top" if self.comparator is None else "comparator",
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        table = fsd_table(
            context,
            self.seed,
            decision,
            replicates=self.replicates,
            reference=self.truth,
            parametric=self.comparator,
            max_events=self.max_events,
        )
        e, d = choose(table)
        menu: Menu = context.menu
        return Choice(
            self.name, decision, e, d, menu.designs[e], menu.diagnostics[d], table
        )
