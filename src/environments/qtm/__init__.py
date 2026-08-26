"""The QTM found-data environment: real Southern California seismicity.

A sibling of ``pointproc``, not a replacement for it. ``pointproc`` generates its
own data from a reference programme; this package ingests a byte-frozen external
catalogue and produces the same :class:`~sciagent.core.types.EventLog` segments,
so both feed one framework.

Gate A25 covers this package's three guarantees -- ingestion is deterministic
across processes, the observation process is declared and applied, and the
consensus edit is preregistered. The two preregistered tracks
``docs/BACKLOG.md`` describes, the recalibration to QTM's operating point, and
D1-D6 on found data are downstream of it and are not built here.

May import ``sciagent``; never the reverse.
"""

from __future__ import annotations

from environments.qtm.censoring import CENSORING_VERSION, DECLARED_CENSORING
from environments.qtm.ingest import (
    N_EVENTS,
    PIPELINE_VERSION,
    REFERENCE_CONFIG,
    Catalogue,
    IngestionConfig,
    ingest_segments,
    parse_catalogue,
)
from environments.qtm.preregistration import CONSENSUS_EDIT, PREREGISTRATION_DIGEST
from environments.qtm.snapshot import (
    PRIMARY,
    SENSITIVITY,
    SNAPSHOT_SHA256,
    data_version,
    digest_of,
    snapshot_path,
)

__all__ = [
    "CENSORING_VERSION",
    "CONSENSUS_EDIT",
    "DECLARED_CENSORING",
    "N_EVENTS",
    "PIPELINE_VERSION",
    "PREREGISTRATION_DIGEST",
    "PRIMARY",
    "REFERENCE_CONFIG",
    "SENSITIVITY",
    "SNAPSHOT_SHA256",
    "Catalogue",
    "IngestionConfig",
    "data_version",
    "digest_of",
    "ingest_segments",
    "parse_catalogue",
    "snapshot_path",
]
