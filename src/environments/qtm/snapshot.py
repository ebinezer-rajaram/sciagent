"""The byte-frozen QTM snapshot, and the address a segment of it carries.

What is pinned, and what is not in this repository
--------------------------------------------------

SCEDC publishes the QTM catalogue as two files that have not changed since
2019-04-15. They are pinned here by SHA-256 and by size, and they are **not
committed**: they total 287MB, and SCEDC publishes no licence text, so
redistributing them is a question nobody here has an answer to. Committing the
digest and a fetch script settles reproducibility without redistributing
anything -- anyone with SCEDC access gets the same bytes or a loud failure.

``scripts/fetch_qtm.py`` obtains them; ``data/`` is gitignored.

The two files are two detection thresholds of one catalogue, not two datasets:
``9.5dev`` is the full 1.81M events at 9.5 times the median absolute deviation,
``12dev`` the ~900k highest-confidence events at 12.0. ``docs/v1/BACKLOG.md``
records the risk that makes the pair load-bearing rather than a convenience:
template matching produces false detections that cluster after large marks,
which is the same signature as the consensus edit. The threshold is therefore a
*parameter of the address* -- both files ingest through the identical pipeline --
so the sensitivity arm is a config change and not a rewrite.

What a data address has to contain
----------------------------------

Invariant 4 addresses a registered result by (env version, config, data version,
metric version, seed). For generated data ``DATA_VERSION`` is a declared
constant; ``environments/pointproc/outcomes.py`` says so in as many words --
"the slice generates its own data, so there is no external dataset to version".

Found data has the failure mode that sentence rules out by construction: the
input can move without anything saying so. So the address here is computed, not
declared, and it carries four things:

- the **snapshot digest**, so a different catalogue cannot share an address;
- the **pipeline version** and its parameters, including the magnitude cut;
- the **censoring version**, when an observation process is applied;
- the **preregistration digest**.

The last is what makes gate A25's third clause structural. "The consensus edit is
preregistered before any system runs on a segment" is a claim about time, and
nothing can assert it of a module constant. Mixing the digest into the address
converts it into a claim about dependency: a segment cannot be addressed, and so
cannot be recorded against, unless the preregistration already exists.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from environments.qtm.ingest import IngestionConfig
from environments.qtm.preregistration import PREREGISTRATION_DIGEST
from sciagent.core.errors import SnapshotMissingError
from sciagent.core.types import DataVersion
from sciagent.paths import data_root

__all__ = [
    "PRIMARY",
    "SENSITIVITY",
    "SNAPSHOT_BYTES",
    "SNAPSHOT_ENV_VAR",
    "SNAPSHOT_SHA256",
    "SNAPSHOT_URL",
    "data_version",
    "digest_of",
    "snapshot_path",
    "snapshot_root",
]

#: The full catalogue: 1.81M events, 9.5 x MAD detection threshold.
PRIMARY = "qtm_final_9.5dev.hypo"

#: The high-confidence subset: ~900k events, 12.0 x MAD. The sensitivity arm
#: against template-matching false detections.
SENSITIVITY = "qtm_final_12dev.hypo"

#: Verified 2026-08-26 against the files SCEDC serves, whose directory listing
#: reports both unmodified since 2019-04-15.
SNAPSHOT_SHA256: dict[str, str] = {
    PRIMARY: "0cd2010281a3b7c2c6c7162931d267cf65063ae29da61b606fa39f14d7539e31",
    SENSITIVITY: "b5438517ddb3ad7ab5777d5a5f1839a8b3aa41439ce0dffa66bdd394832d4dd6",
}

#: Sizes in bytes, as a cheap first check before a full digest.
SNAPSHOT_BYTES: dict[str, int] = {PRIMARY: 201061306, SENSITIVITY: 99744391}

SNAPSHOT_URL = "https://service.scedc.caltech.edu/ftp/QTMcatalog/"

#: Set to keep the snapshot outside the checkout entirely.
SNAPSHOT_ENV_VAR = "SCIAGENT_QTM_DATA"

_DIGEST_CHUNK = 1 << 20


def snapshot_root() -> Path:
    """Return the directory the snapshot lives in, shared across worktrees.

    Resolved through :func:`~sciagent.paths.data_root`, so every git worktree of
    this repository finds one copy of a 287MB download rather than one each --
    the same reasoning that puts the table cache in the main tree.
    """
    return data_root("qtm", SNAPSHOT_ENV_VAR)


def snapshot_path(name: str) -> Path:
    """Return where ``name`` is expected on disk. Does not check it is there."""
    return snapshot_root() / name


def digest_of(path: Path) -> str:
    """Return the SHA-256 of ``path``, read in chunks.

    Guarantees a value that depends on the file's bytes alone. Chunked because
    the primary catalogue is 201MB and reading it whole to hash it would cost
    more memory than the pipeline that consumes it.
    """
    if not path.is_file():
        raise SnapshotMissingError(
            f"{path} is not present. It is gitignored rather than redistributed; "
            f"run `uv run python scripts/fetch_qtm.py` to obtain it."
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DIGEST_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def data_version(path: Path, config: IngestionConfig) -> DataVersion:
    """Return the address every segment ingested from ``path`` is recorded under.

    Guarantees that the value moves when the catalogue's bytes move, when any
    pipeline parameter moves, when the observation process moves, and when the
    preregistered consensus edit moves -- and that it does not move otherwise.
    """
    return DataVersion(
        f"qtm/{path.name}@{digest_of(path)[:12]}"
        f"+{config.address()}"
        f"+prereg/{PREREGISTRATION_DIGEST[:12]}"
    )
