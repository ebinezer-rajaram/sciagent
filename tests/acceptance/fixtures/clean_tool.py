"""Positive control for acceptance test A14. **Never imported by ``src``.**

The mirror of :mod:`holdout_violator`: an agent-surface module that reads the
registry through the agent-reachable partition only. A14 asserts the analyser
reports *nothing* here, so that a checker which flagged every module -- and would
therefore also "detect" the negative control -- fails the gate.
"""

from __future__ import annotations

from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentRecord, ExperimentStore


def agent_tool(store: ExperimentStore) -> tuple[ExperimentRecord, ...]:
    """The declared entry point. Reads DEV, which agents may do."""
    return _lookup(store)


def _lookup(store: ExperimentStore) -> tuple[ExperimentRecord, ...]:
    return store.records(partition=DataPartition.DEV)
