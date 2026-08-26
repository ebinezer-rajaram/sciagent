"""Fetch the QTM catalogue snapshot, and verify it against its pinned digests.

    uv run python scripts/fetch_qtm.py            # fetch what is missing
    uv run python scripts/fetch_qtm.py --verify   # check what is there, fetch nothing
    uv run python scripts/fetch_qtm.py --force    # re-fetch even if present

The two files total 287MB and are **not** committed: SCEDC publishes no licence
text, so redistributing them is a question nobody here has an answer to. What is
committed is their SHA-256, in ``environments/qtm/snapshot.py``, and this script.
Anyone with SCEDC access therefore gets the same bytes or a loud failure, which
is the reproducibility guarantee that matters; ``data/`` is gitignored.

Downloads land in the **main worktree**, resolved through
:func:`~sciagent.paths.repo_root`, so concurrent sessions share one copy.

Citation, per SCEDC's terms: Ross, Trugman, Hauksson & Shearer (2019),
*Searching for hidden earthquakes in Southern California*, Science 364, 767-771.
Acknowledge doi:10.7914/SN/CI (SCSN) and doi:10.7909/C3WD3xH1 (SCEDC).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

from environments.qtm.snapshot import (
    SNAPSHOT_BYTES,
    SNAPSHOT_SHA256,
    SNAPSHOT_URL,
    snapshot_root,
)
from sciagent.core.errors import SnapshotMismatchError

_CHUNK = 1 << 20


def _digest(path: Path) -> tuple[str, int]:
    """Return ``(sha256, size)`` for ``path``, read in chunks."""
    digest = hashlib.sha256()
    seen = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            seen += len(chunk)
    return digest.hexdigest(), seen


def _download(name: str, target: Path) -> None:
    """Stream ``name`` from SCEDC to ``target``, reporting progress.

    Writes to a ``.partial`` sibling and renames only on success, so an
    interrupted transfer cannot leave a truncated file that later looks like a
    digest mismatch of unknown cause.
    """
    partial = target.with_suffix(target.suffix + ".partial")
    expected = SNAPSHOT_BYTES[name]
    seen = 0
    with httpx.stream(
        "GET", SNAPSHOT_URL + name, timeout=120.0, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(_CHUNK):
                handle.write(chunk)
                seen += len(chunk)
                sys.stdout.write(f"\r  {name}  {seen / expected:6.1%}")
                sys.stdout.flush()
    sys.stdout.write("\n")
    # Check the length before the rename, not only the digest afterwards. A
    # stream that ends early is the likeliest transfer failure, and without this
    # it surfaces as a digest mismatch -- which reads as "the catalogue changed",
    # the one thing this script exists to distinguish from "the download broke".
    if seen != expected:
        partial.unlink(missing_ok=True)
        raise SnapshotMismatchError(
            f"{name} ended after {seen} bytes, {expected} expected; the transfer "
            f"was truncated. The partial file has been removed -- re-run to retry."
        )
    partial.replace(target)


def main() -> int:
    """Fetch and verify. Returns 0 when every declared file matches its digest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify", action="store_true", help="check what is present; download nothing"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-download even when present"
    )
    arguments = parser.parse_args()

    root = snapshot_root()
    if not arguments.verify:
        root.mkdir(parents=True, exist_ok=True)
    print(f"snapshot root: {root}")

    failures = 0
    for name, expected in SNAPSHOT_SHA256.items():
        target = root / name
        # `--verify` reports on what is on disk and never downloads, so it is
        # `--force` that must give way when both are passed. The other order
        # reported a present, digest-matching file as MISSING.
        if not target.is_file():
            if arguments.verify:
                print(f"  {name}  MISSING")
                failures += 1
                continue
            _download(name, target)
        elif arguments.force and not arguments.verify:
            _download(name, target)

        actual, size = _digest(target)
        if actual == expected and size == SNAPSHOT_BYTES[name]:
            print(f"  {name}  ok  {size} bytes  {actual[:12]}...")
        else:
            print(
                f"  {name}  MISMATCH\n"
                f"    expected {expected} ({SNAPSHOT_BYTES[name]} bytes)\n"
                f"    actual   {actual} ({size} bytes)"
            )
            failures += 1

    if failures:
        print(
            f"\n{failures} file(s) missing or mismatched. A mismatch means the "
            f"catalogue on disk is not the one this repository's results are "
            f"addressed to -- do not ingest it. Re-run with --force to refetch.",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
