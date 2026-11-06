"""Replay every stored LLM investigation record of a pilot store offline.

    uv run python scripts/replay_check.py --root .cache/smoke \
        [--split data/v2/truths/dev] [--limit N] [--arm AG-c] [--digests a,b]

Rebuilds hunt B's ``replay_check.py`` (the Windows salvage,
``salvage/windows-2026-10/hunt/B/replay_check.py``) as a store-level check of
SPEC §6.3 no. 9. Finds every ``<root>/llm/<digest16>/investigation-*.json``
(the campaign driver's layout; ``aborted-*.json`` partial records are not
replayable and are not looked for), in sorted path order, and replays each
through :func:`sciagent.investigation.runner.replay_investigation` against the
pointproc investigation environment built from ``--split`` as
``scripts/pilot.py`` builds it, with the current tree's code. Prints one line
per record: ``PASS`` with the steps, budgets used and seconds, or ``FAIL`` with
the divergence step and field (a
:class:`~sciagent.harness.errors.ReplayDivergenceError`), or the typed error
that stopped the record being replayed (it does not load, names no harness
version, names a truth the split does not hold, or the sandbox is
unavailable), or ``REFUSED``.

A record is ``REFUSED`` (:class:`~sciagent.harness.errors.HarnessVersionMismatchError`)
when the ``harness_version`` of its config entry is not this code's
:data:`~sciagent.harness.live.HARNESS_VERSION`: another harness's tools and
budgets are not this one's, so replaying it here would diverge by design and
say nothing about the harness. The line names both versions and where to
replay it: a ``harness/1`` record at the pilot's commit 900e66a (LOG
2026-10-29, :data:`REPLAY_AT`), any other at a checkout with that harness. The
version is checked before the truth, since nothing else this checkout says
about such a record means anything. The live loop has written
``harness_version`` into every record's config since P2 (558d600), so a record
without it (or with anything but a non-empty string) came from no harness
this project has run and no checkout is known to replay it: it is a ``FAIL``
(:class:`~sciagent.harness.errors.HarnessVersionMissingError`), not a refusal,
so it cannot leave the failure count by losing a field.

``--arm`` and ``--digests`` (prefixes of the ``llm/`` directory names) filter;
``--limit N`` replays the first N selected records, so a subset is
deterministic. A record that does not load is never filtered out by ``--arm``.
The summary counts the selected records, then passed, failed and refused.
Exit status: 0 (``REPLAY OK``) when every selected record passes; 1
(``REPLAY FAILED``) when one fails, whatever else was refused, or none is
found; 3 (``REPLAY REFUSED``) when none fails but at least one was refused, so
the store needs another checkout to be checked in full; 2 on a usage error.
Read-only: records are loaded, never saved; AG-o sandboxes run under a
temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from pilot import (
    DEFAULT_SPLIT,
    load_split,
    pointproc_environment,
    reconfigure_stdout,
)

from sciagent.campaign.plan import LEGACY_HARNESS_VERSION
from sciagent.core.errors import SciAgentError
from sciagent.harness.errors import (
    HarnessVersionMismatchError,
    HarnessVersionMissingError,
    ReplayDivergenceError,
)
from sciagent.harness.live import HARNESS_VERSION
from sciagent.harness.record import SessionRecord

if TYPE_CHECKING:
    from sciagent.investigation import EnvironmentSpec

#: The campaign driver's directory of LLM session records
#: (``sciagent.campaign.driver.LLM_DIR``).
LLM_DIR = "llm"
#: How much of a failure's message is printed.
SHOWN = 400
#: Where a record of an earlier harness is replayed: the commit whose code
#: wrote it (LOG 2026-10-29: the Linux pilot's ``harness/1`` sessions ran at
#: v2 900e66a, and the merge keeps no frozen ``harness/1`` tool layer).
REPLAY_AT: Final = {LEGACY_HARNESS_VERSION: "900e66a"}


def check_harness_version(record: SessionRecord) -> str:
    """The record's harness version, if it is this code's; raise otherwise.

    Raises :class:`HarnessVersionMismatchError` for another version and
    :class:`HarnessVersionMissingError` when the leading config entry names
    none (or the record has no config entry).
    """
    configs = record.of_kind("config")
    recorded = configs[0].body.get("harness_version") if configs else None
    if not isinstance(recorded, str) or not recorded:
        raise HarnessVersionMissingError(
            f"the record names no harness version (harness_version "
            f"{recorded!r}); this code runs {HARNESS_VERSION}, and no checkout "
            "is known to replay it",
            running=HARNESS_VERSION,
        )
    if recorded != HARNESS_VERSION:
        commit = REPLAY_AT.get(recorded)
        where = (
            f"replay it at {commit}, the commit that ran {recorded}"
            if commit is not None
            else f"replay it at a checkout whose HARNESS_VERSION is {recorded}"
        )
        raise HarnessVersionMismatchError(
            f"the record was written under {recorded}, this code runs "
            f"{HARNESS_VERSION}: its tools are not this harness's; {where}",
            recorded=recorded,
            running=HARNESS_VERSION,
            commit=commit,
        )
    return recorded


def exit_status(failed: int, refused: int) -> tuple[int, str]:
    """The exit status and last line: a failure outranks a refusal."""
    if failed:
        return 1, "REPLAY FAILED"
    if refused:
        return 3, "REPLAY REFUSED"
    return 0, "REPLAY OK"


@dataclass(frozen=True)
class Stored:
    """One record file, loaded or not."""

    rel: str
    record: SessionRecord | None
    error: SciAgentError | None

    @property
    def investigation(self) -> dict[str, Any]:
        if self.record is None:
            return {}
        configs = self.record.of_kind("config")
        body = configs[0].body.get("investigation") if configs else None
        return dict(body) if isinstance(body, dict) else {}

    @property
    def label(self) -> str:
        inv = self.investigation
        if not inv:
            return self.rel
        return (
            f"{self.rel} {inv.get('arm')} {inv.get('condition')} "
            f"{inv.get('truth_id')} s{inv.get('seed')}"
        )


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def discover(root: Path, digests: tuple[str, ...]) -> list[Path]:
    """Every investigation record under ``root/llm``, in sorted path order."""
    paths = sorted((root / LLM_DIR).glob("*/investigation-*.json"))
    if digests:
        paths = [p for p in paths if p.parent.name.startswith(digests)]
    return paths


def load(root: Path, path: Path) -> Stored:
    rel = path.relative_to(root).as_posix()
    try:
        return Stored(rel, SessionRecord.load(path), None)
    except SciAgentError as error:
        return Stored(rel, None, error)


def replay_one(stored: Stored, env: EnvironmentSpec, truths: frozenset[str]) -> str:
    """The record's ``PASS``, ``FAIL`` or ``REFUSED`` line."""
    from sciagent.investigation.runner import replay_investigation

    if stored.record is None:
        assert stored.error is not None
        return f"FAIL {stored.label}: {_named(stored.error)}"
    try:
        check_harness_version(stored.record)
    except HarnessVersionMismatchError as error:
        return f"REFUSED {stored.label}: {_named(error)}"
    except HarnessVersionMissingError as error:
        return f"FAIL {stored.label}: {_named(error)}"
    truth = stored.investigation.get("truth_id")
    if truth is not None and truth not in truths:
        return f"FAIL {stored.label}: truth {truth} is not in the split"
    t0 = time.perf_counter()
    try:
        report = replay_investigation(stored.record, env)
    except ReplayDivergenceError as error:
        return (
            f"FAIL {stored.label}: step {error.step} field {error.field}: "
            f"{str(error)[:SHOWN]}"
        )
    except SciAgentError as error:
        return f"FAIL {stored.label}: {_named(error)}"
    return (
        f"PASS {stored.label}: steps={report.steps} "
        f"budgets={dict(report.budgets_used)} "
        f"({time.perf_counter() - t0:.1f}s)"
    )


def _named(error: SciAgentError) -> str:
    return f"{type(error).__name__}: {str(error)[:SHOWN]}"


def main(argv: list[str] | None = None) -> int:
    reconfigure_stdout()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True, help="the pilot store")
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument(
        "--limit",
        type=_positive,
        default=None,
        help="replay only the first N selected records, in sorted path order",
    )
    parser.add_argument("--arm", default=None, help="only this arm, e.g. AG-c")
    parser.add_argument(
        "--digests", default=None, help="comma-separated llm/ directory prefixes"
    )
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"{args.root}: not a directory", file=sys.stderr)
        return 2
    if not args.split.exists():
        print(
            f"{args.split}: the split is missing. The dev split is git-ignored, "
            "so a fresh clone or worktree has none: copy data/v2/truths/dev from "
            "the main checkout (truths.json sha256 604acec5...4420), or regenerate "
            "it on this machine with scripts/sample_truths.py --split dev --n 20 "
            "--seed 2026 and check that sha256",
            file=sys.stderr,
        )
        return 2
    if not (args.split / "truths.json").is_file():
        print(f"{args.split}: no truths.json", file=sys.stderr)
        return 2

    import sciagent
    from sciagent.glm.fit import FIT_VERSION

    split_sha = hashlib.sha256((args.split / "truths.json").read_bytes()).hexdigest()
    print(
        f"sciagent from {Path(sciagent.__file__).parent}; {FIT_VERSION}; "
        f"{HARNESS_VERSION}"
    )
    print(f"split: {args.split} truths.json sha256 {split_sha}")
    digests = tuple(args.digests.split(",")) if args.digests else ()
    stored = [load(args.root, p) for p in discover(args.root, digests)]
    if args.arm is not None:
        stored = [
            s
            for s in stored
            if s.record is None or s.investigation.get("arm") == args.arm
        ]
    if not stored:
        filtered = " selected by --arm/--digests" if args.arm or digests else ""
        print(f"no investigation records{filtered} under {args.root / LLM_DIR}")
        print("REPLAY FAILED")
        return 1
    chosen = stored if args.limit is None else stored[: args.limit]

    records = load_split(args.split)
    env = pointproc_environment(records).investigation
    assert env is not None
    truths = frozenset(r.id for r in records)
    passed = failed = refused = 0
    for s in chosen:
        line = replay_one(s, env, truths)
        passed += line.startswith("PASS ")
        failed += line.startswith("FAIL ")
        refused += line.startswith("REFUSED ")
        print(line, flush=True)
    assert passed + failed + refused == len(chosen)
    print(
        f"replayed: {len(chosen)} of {len(stored)}; "
        f"passed: {passed}; failed: {failed}; refused: {refused}"
    )
    code, verdict = exit_status(failed, refused)
    print(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
