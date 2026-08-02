"""Negative control for acceptance test A14. **Never imported by ``src``.**

This module pretends to be part of the agent tool surface and reaches a sealed
partition through one level of indirection. A14 asserts that the call-graph
analyser finds the path here, which is what stops the analyser's silence over
``src`` from being vacuous: until backlog item 12 there is no real agent surface
to walk, so an analyser that returned "no violations" unconditionally would pass
the gate while checking nothing.

The indirection is deliberate. A direct reference would be caught by a plain
textual scan, and the property A14 actually claims is *reachability*.
"""

from __future__ import annotations

from sciagent.registry.partitions import DataPartition, SealedAccess
from sciagent.registry.store import ExperimentRecord, ExperimentStore


def agent_tool(store: ExperimentStore) -> tuple[ExperimentRecord, ...]:
    """The declared entry point. Reaches HOLDOUT only via :func:`_lookup`."""
    return _lookup(store)


def _lookup(store: ExperimentStore) -> tuple[ExperimentRecord, ...]:
    return store.sealed_records(
        DataPartition.HOLDOUT, SealedAccess("A14 negative control")
    )
