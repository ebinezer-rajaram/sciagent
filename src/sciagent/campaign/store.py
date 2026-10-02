"""The campaign's results store: append-only and content-addressed (invariant 4).

Two layers, both keyed by a cell's :class:`~sciagent.registry.store.ExperimentKey`
digest:

- the **ledger** (:class:`~sciagent.registry.ledger.CampaignLedger`, SQLite):
  the cell's named numbers, append-only by triggers and an authorizer;
- one **detail file** per cell, ``details/<digest>.json``: the key's fields,
  the reading (each value as ``float.hex()``, so non-finite values survive) and
  a JSON detail (structures, curves, error messages, record paths; non-finite
  floats as strings, :func:`json_safe`). It is
  written with exclusive creation, never replaced: an existing file is accepted
  only if its bytes are identical (a faithful rerun), otherwise the write is
  refused with :class:`~sciagent.core.errors.RegistryConflictError`.

The detail file is written first and the ledger row second, so the ledger row
is the commit: a cell is *done* when the ledger holds it. A crash between the
two leaves a detail without a row; :meth:`ResultStore.recover` appends the row
from the detail's own reading on the next start, which makes a resumed
campaign skip it rather than run it again (an LLM session must not be rerun to
fill a missing row: its rerun would differ).

Wall-clock times are never in a cell. They go to ``timing.jsonl``, an
append-only log that is not addressed (CLAUDE.md invariant 3: a timestamp in a
cell would make two byte-identical campaigns differ).

Single-writer: the ledger connection is bound to the thread that opened it, so
the driver's main thread is the only writer. Workers return results; they never
touch the store.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Final

from sciagent.core.errors import RegistryConflictError
from sciagent.core.types import (
    DataVersion,
    Digest,
    EnvVersion,
    FrozenDict,
    MetricVersion,
    Seed,
)
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.store import ExperimentKey
from sciagent.scenarios.records import canonical_json

LEDGER_FILE: Final = "ledger.sqlite"
DETAILS_DIR: Final = "details"
TIMING_FILE: Final = "timing.jsonl"
DETAIL_SCHEMA: Final = "sciagent.campaign.cell/1"

type Json = Any


def key_json(key: ExperimentKey) -> dict[str, Json]:
    return {
        "env_version": str(key.env_version),
        "config": dict(sorted(key.config.items())),
        "data_version": str(key.data_version),
        "metric_version": str(key.metric_version),
        "seed": int(key.seed),
    }


def key_from_json(data: Mapping[str, Json]) -> ExperimentKey:
    return ExperimentKey(
        env_version=EnvVersion(str(data["env_version"])),
        config=FrozenDict[str, str](
            {str(k): str(v) for k, v in data["config"].items()}
        ),
        data_version=DataVersion(str(data["data_version"])),
        metric_version=MetricVersion(str(data["metric_version"])),
        seed=Seed(int(data["seed"])),
    )


def cell_bytes(key: ExperimentKey, reading: Mapping[str, float], detail: Json) -> bytes:
    """The exact bytes of a cell's detail file (canonical JSON plus newline)."""
    body = {
        "schema": DETAIL_SCHEMA,
        "digest": str(key.digest),
        "key": key_json(key),
        "reading": {k: float(v).hex() for k, v in sorted(reading.items())},
        "detail": json_safe(detail),
    }
    return (canonical_json(body) + "\n").encode("utf-8")


def json_safe(value: Json) -> Json:
    """``value`` with every non-finite float as the string ``"inf"``,
    ``"-inf"`` or ``"nan"`` (canonical JSON refuses them; ``float()`` reads
    them back). A model that gives a held-out event zero intensity scores
    ``-inf``, which is a result, not an error."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


class ResultStore:
    """The ledger plus the detail files under one root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / DETAILS_DIR).mkdir(exist_ok=True)
        self.ledger = CampaignLedger.open(root / LEDGER_FILE)

    def __enter__(self) -> ResultStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.ledger.close()

    def detail_path(self, digest: Digest | str) -> Path:
        return self.root / DETAILS_DIR / f"{digest}.json"

    # -- writing ----------------------------------------------------------

    def put(
        self, key: ExperimentKey, reading: Mapping[str, float], detail: Json
    ) -> LedgerEntry:
        """Write the cell's detail (exclusively), then commit its ledger row."""
        data = cell_bytes(key, reading, detail)
        path = self.detail_path(key.digest)
        try:
            with path.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise RegistryConflictError(
                    f"cell {key.digest} already has a different detail file at "
                    f"{path}; the store is append-only"
                ) from None
        return self.ledger.append(key, reading=reading)

    def log_timing(self, record: Mapping[str, Json]) -> None:
        """Append one line to the (unaddressed) timing log."""
        line = json.dumps(dict(record), sort_keys=True, ensure_ascii=False)
        with (self.root / TIMING_FILE).open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")

    def recover(self) -> int:
        """Commit ledger rows for detail files whose row is missing; count them."""
        added = 0
        for path in sorted((self.root / DETAILS_DIR).glob("*.json")):
            cell = json.loads(path.read_text(encoding="utf-8"))
            key = key_from_json(cell["key"])
            if str(key.digest) != path.stem or cell["digest"] != path.stem:
                raise RegistryConflictError(f"{path}: name does not match its key")
            if self.ledger.contains(key.digest):
                continue
            reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
            self.ledger.append(key, reading=reading)
            added += 1
        return added

    # -- reading ----------------------------------------------------------

    def done(self, key: ExperimentKey) -> bool:
        return self.ledger.contains(key.digest)

    def cell(self, digest: Digest | str) -> dict[str, Json]:
        """The detail file of a committed cell, parsed."""
        data: dict[str, Json] = json.loads(
            self.detail_path(digest).read_text(encoding="utf-8")
        )
        return data

    def cells(self) -> tuple[dict[str, Json], ...]:
        """Every committed cell's detail, in ledger order."""
        return tuple(self.cell(e.digest) for e in self.ledger.entries())
