"""The experiment registry: append-only storage, metric versions, partitions.

Domain-independent, like everything in ``sciagent``. Diagnostics reach the metric
registry as injected callables and experiment configuration reaches the store as
opaque text, so nothing here knows what an arrival process is.
"""

from __future__ import annotations

from sciagent.registry.budget import Budget
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.metrics import MetricRef, MetricRegistry, MetricSpec
from sciagent.registry.partitions import (
    AGENT_REACHABLE,
    AGENT_TOOL_SURFACE,
    SEALED,
    SEALED_SYMBOLS,
    DataPartition,
    SealedAccess,
    require_sealed,
)
from sciagent.registry.store import ExperimentKey, ExperimentRecord, ExperimentStore

__all__ = [
    "AGENT_REACHABLE",
    "AGENT_TOOL_SURFACE",
    "SEALED",
    "SEALED_SYMBOLS",
    "Budget",
    "CampaignLedger",
    "DataPartition",
    "ExperimentKey",
    "ExperimentRecord",
    "ExperimentStore",
    "LedgerEntry",
    "MetricRef",
    "MetricRegistry",
    "MetricSpec",
    "SealedAccess",
    "require_sealed",
]
