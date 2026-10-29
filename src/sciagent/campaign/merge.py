"""Merge stores written in parallel by one checkout into one new store.

No unit reads another unit's cell: a baseline job and an LLM session see only
the truth, the seed and the configuration, and a score job only its own
session's detail (LOG 2026-10-29). So a pilot's non-LLM and LLM halves can run
as two drivers on two stores (one driver per store) from the same commit. Every
reader (``pilot_report.py``, ``hunt_gap.py``, the audit checks) takes one root;
:func:`merge_stores` writes a *new* store holding every committed cell of each
source, byte for byte, so the readers need no change and the sources are only
read (invariant 4).

Refused with :class:`~sciagent.campaign.plan.CampaignError`, before ``dest`` is
created, unless:

- there are two or more distinct sources and ``dest`` does not exist;
- each source's fingerprint sidecar is exactly one ``establish`` line, of the
  same fingerprint in every source: every cell was written by that code. A
  source with a reuse history is refused (its cells may come from other code);
- no cell is in two sources;
- each truth id carries one ``truth_digest`` across the sources (one split:
  two splits can share their ids);
- every source detail file is the canonical bytes of its key, reading and
  detail, so the copy is the same file.

Copied, source by source in the order given and so deterministically: the
committed cells in ledger order, the record dir of each committed session
cell (``detail.session.record_dir``, when the source has it; a missing one
stays missing, for ``store_check.py`` to report) and the complete lines of
``timing.jsonl``. Not copied: ``progress.log``, ``attempts.jsonl`` and record
dirs no committed cell names (a session still running), so merging a store a
driver is writing gives a snapshot of its committed cells. The new store's
sidecar establishes the shared fingerprint.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sciagent.campaign.fingerprint import (
    LLM_DIR,
    StoreLock,
    guard_store,
    read_fingerprints,
)
from sciagent.campaign.plan import CampaignError
from sciagent.campaign.store import (
    TIMING_FILE,
    Json,
    ResultStore,
    cell_bytes,
    key_from_json,
)
from sciagent.registry.store import ExperimentKey

_LLM_SESSION: Final = "llm-session"


@dataclass(frozen=True)
class MergeSummary:
    """What a merge wrote: the shared fingerprint and cells per source."""

    fingerprint: str
    cells: tuple[int, ...]
    record_dirs: int
    timing_lines: int


@dataclass(frozen=True)
class _Cell:
    key: ExperimentKey
    reading: dict[str, float]
    detail: Json


@dataclass(frozen=True)
class _Source:
    root: Path
    cells: tuple[_Cell, ...]
    record_dirs: tuple[str, ...]
    timing: str


def _fingerprint(root: Path) -> str:
    entries = read_fingerprints(root)
    if len(entries) != 1 or entries[0].event != "establish":
        raise CampaignError(
            f"{root}: its code fingerprint history is {len(entries)} lines, not "
            f"one 'establish': its cells may come from other code; not merged"
        )
    return entries[0].fingerprint


def _record_dir(root: Path, cell: _Cell) -> str | None:
    if cell.key.config.get("kind") != _LLM_SESSION:
        return None
    text = str(cell.detail["session"]["record_dir"])
    parts = Path(text).parts
    if len(parts) != 2 or parts[0] != LLM_DIR or parts[1] in ("", ".", ".."):
        raise CampaignError(f"{root}: record dir {text!r} is not under {LLM_DIR}/")
    return text if (root / text).is_dir() else None


def _read(root: Path) -> _Source:
    cells: list[_Cell] = []
    with ResultStore(root, read_only=True) as store:
        for entry in store.ledger.entries():
            raw = store.detail_path(entry.digest).read_bytes()
            stored = store.cell(entry.digest)
            key = key_from_json(stored["key"])
            reading = {k: float.fromhex(v) for k, v in stored["reading"].items()}
            if key.digest != entry.digest or (
                cell_bytes(key, reading, stored["detail"]) != raw
            ):
                raise CampaignError(
                    f"{root}: cell {entry.digest} is not the canonical bytes of "
                    f"its key, reading and detail; not merged"
                )
            cells.append(_Cell(key, reading, stored["detail"]))
    dirs = tuple(d for c in cells if (d := _record_dir(root, c)) is not None)
    path = root / TIMING_FILE
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    timing = text[: text.rfind("\n") + 1]
    return _Source(root, tuple(cells), dirs, timing)


def _check_disjoint(sources: Sequence[_Source]) -> None:
    owner: dict[str, Path] = {}
    truths: dict[str, str] = {}
    for source in sources:
        for cell in source.cells:
            digest = str(cell.key.digest)
            if digest in owner:
                raise CampaignError(
                    f"cell {digest} is in two sources ({owner[digest]} and "
                    f"{source.root}); not merged"
                )
            owner[digest] = source.root
            truth = cell.key.config.get("truth")
            record = cell.key.config.get("truth_digest")
            if truth is None or record is None:
                continue
            if truths.setdefault(truth, record) != record:
                raise CampaignError(
                    f"truth {truth} has two records ({truths[truth][:12]}… and "
                    f"{record[:12]}…, in {source.root}): two splits; not merged"
                )


def merge_stores(sources: Sequence[Path], dest: Path) -> MergeSummary:
    """Write a new store at ``dest`` from ``sources`` (module docstring)."""
    if len(sources) < 2 or len({s.resolve() for s in sources}) != len(sources):
        raise CampaignError("a merge takes two or more distinct source stores")
    if dest.exists():
        raise CampaignError(f"{dest} exists; a merge writes a new store only")
    fingerprints = {s: _fingerprint(s) for s in sources}
    if len(set(fingerprints.values())) != 1:
        shown = "; ".join(f"{s}: {f}" for s, f in fingerprints.items())
        raise CampaignError(f"the sources' code fingerprints differ ({shown})")
    fingerprint = fingerprints[sources[0]]
    read = tuple(_read(s) for s in sources)
    _check_disjoint(read)
    lock = StoreLock(dest)
    try:
        guard_store(dest, fingerprint, lock=lock)
        with ResultStore(dest) as out:
            for source in read:
                for d in source.record_dirs:
                    shutil.copytree(source.root / d, dest / d)
                for cell in source.cells:
                    out.put(cell.key, cell.reading, cell.detail)
                with (dest / TIMING_FILE).open("a", encoding="utf-8", newline="") as f:
                    f.write(source.timing)
    finally:
        lock.release()
    return MergeSummary(
        fingerprint,
        tuple(len(s.cells) for s in read),
        sum(len(s.record_dirs) for s in read),
        sum(s.timing.count("\n") for s in read),
    )
