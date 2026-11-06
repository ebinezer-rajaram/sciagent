"""Control 4's record: where it lives, how it is written, and its one reader.

``scripts/p4_naming_null.py`` writes the file once;
``scripts/p4_search_misses.py`` reads it with :func:`read_naming_null` and builds
``ScriptedControls`` from the answer (prereg 2026-11-05 §2.7, "Control 4's
source"). No option sets the value, so the only way to True is this file.

The answer gates the four C2 reads, so a doubt about the file is False: control
4 was not shown to pass, and SPEC §6.4 allows no comparison without its control.
That covers a missing or unreadable file, a commit other than the expected one,
a split, seed or schema that is not the rule's, and anything but a pass on one
truth of each half.

Standard library only, so importing this file loads neither the runner nor
numpy.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

#: The record's path (§2.7). Outside the repository, as test-split readings are.
NAMING_NULL_RECORD: Final = Path(
    "/home/ubuntu/research/records/sciagent/p4-naming-null/naming-null.json"
)
#: The version of the file's format.
SCHEMA: Final = "sciagent.p4.naming-null/1"
#: The seed of §2.7's run.
NAMING_SEED: Final = 1
#: The halves of the split, one truth each: in and out of dictionary.
IN_HALF: Final = "in"
OUT_HALF: Final = "out"
#: The split §2.7 names; a rehearsal asks for ``dev`` by name.
TEST_SPLIT: Final = "test"


def record_bytes(body: Mapping[str, Any]) -> bytes:
    """The file's exact bytes: key order is the sorted one, and a float JSON
    cannot hold is refused, which is why the record's floats are hex text."""
    text = json.dumps(body, sort_keys=True, indent=1, allow_nan=False)
    return f"{text}\n".encode()


def write_record(path: Path, body: Mapping[str, Any]) -> None:
    """Write ``body`` to ``path`` once, whole.

    The bytes reach a temporary name first and are linked into place, so a crash
    leaves no half-written record, and a record already there raises
    :class:`FileExistsError` and keeps its bytes.
    """
    data = record_bytes(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink()


def _passed_on_both_halves(verdicts: object) -> bool:
    if not isinstance(verdicts, list) or len(verdicts) != 2:
        return False
    if not all(isinstance(v, dict) for v in verdicts):
        return False
    truths = [v.get("truth") for v in verdicts]
    return (
        all(isinstance(t, str) and t for t in truths)
        and truths[0] != truths[1]
        and sorted(str(v.get("half")) for v in verdicts) == [IN_HALF, OUT_HALF]
        and all(v.get("passed") is True for v in verdicts)
    )


def read_naming_null(
    commit: str, *, split: str = TEST_SPLIT, path: Path = NAMING_NULL_RECORD
) -> bool:
    """Control 4's pass state: ``True`` only for a pass on both truths of
    ``split`` at exactly ``commit`` (module docstring)."""
    try:
        body = json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        return False
    if not commit or not isinstance(body, dict):
        return False
    return (
        body.get("schema") == SCHEMA
        and body.get("commit") == commit
        and body.get("split") == split
        and body.get("seed") == NAMING_SEED
        and _passed_on_both_halves(body.get("verdicts"))
    )
