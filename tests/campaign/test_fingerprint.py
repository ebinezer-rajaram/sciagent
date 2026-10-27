"""The store's code-fingerprint guard (LOG 2026-10-27, F5 point 1).

A store keeps ``fingerprint.jsonl`` beside its cells: an append-only sidecar
whose first line establishes the code fingerprint that writes the store and
whose later lines record each explicit permission to reuse its cells under
another fingerprint. A campaign whose fingerprint differs from the store's
refuses before any unit runs unless it names the store's fingerprint exactly
(``reuse_from``). The fingerprint is an opaque string; ``scripts/pilot.py``
computes it from git and ``uv.lock`` and refuses a dirty tree.

Fast: job functions are cheap fakes run in-process (``workers=0``), as in
``test_campaign.py``; the git checks run on a throwaway repository.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from campaign_support import TRUTH_IDS, entry, environment, tiny_config

from sciagent.campaign import driver as driver_module
from sciagent.campaign.audit import check_store, compare_stores
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, UnitResult
from sciagent.campaign.fingerprint import (
    FINGERPRINT_FILE,
    UNRECORDED,
    CodeFingerprintError,
    FingerprintEntry,
    guard_store,
    read_fingerprints,
)
from sciagent.campaign.plan import CampaignError, baseline_units
from sciagent.campaign.store import DETAILS_DIR, ResultStore, key_from_json

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
OLD = "commit=aaaa,tree=clean,uv.lock=1111"
NEW = "commit=bbbb,tree=clean,uv.lock=1111"

type Calls = list[tuple[str, int, tuple[str, ...]]]


def _fake_job(calls: Calls) -> Callable[[BaselineJob], tuple[UnitResult, ...]]:
    def fake(job: BaselineJob) -> tuple[UnitResult, ...]:
        calls.append((job.truth.id, job.seed, job.systems))
        return tuple(
            UnitResult(s, True, {"status": 1.0, "per_event": -0.7}, {"s": s}, 0.0)
            for s in job.systems
        )

    return fake


def _run(
    root: Path,
    fingerprint: str,
    reuse_from: str | None = None,
    **settings: Any,
) -> Any:
    campaign = Campaign(
        root,
        environment(),
        tiny_config(),
        [entry(t) for t in TRUTH_IDS],
        DriverSettings(workers=0, poll_s=0.05, run_llm=False, **settings),
        fingerprint=fingerprint,
        reuse_from=reuse_from,
    )
    try:
        return campaign.run()
    finally:
        campaign.close()


def _sidecar(root: Path) -> list[dict[str, Any]]:
    text = (root / FINGERPRINT_FILE).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in {"progress.log", "timing.jsonl"}
    }


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    made: Calls = []
    monkeypatch.setattr(driver_module, "run_baseline_job", _fake_job(made))
    return made


# --------------------------------------------------------------------------
# The sidecar
# --------------------------------------------------------------------------


def test_a_fresh_store_records_its_fingerprint_and_resumes_under_it(
    tmp_path: Path, calls: Calls
) -> None:
    root = tmp_path / "s"
    first = _run(root, OLD)
    n_units = len(baseline_units(TRUTH_IDS, tiny_config()))
    assert sum(first.recorded.values()) == n_units
    assert _sidecar(root) == [{"event": "establish", "fingerprint": OLD}]
    assert read_fingerprints(root) == (FingerprintEntry("establish", OLD, None),)
    n_jobs = len(calls)
    again = _run(root, OLD)
    assert again.skipped_done == n_units and len(calls) == n_jobs
    assert _sidecar(root) == [{"event": "establish", "fingerprint": OLD}]


@pytest.mark.parametrize("rescore", [False, True])
def test_a_different_fingerprint_refuses_and_launches_nothing(
    tmp_path: Path, calls: Calls, rescore: bool
) -> None:
    """Also in rescore mode, which writes score cells."""
    root = tmp_path / "s"
    _run(root, OLD)
    n_jobs = len(calls)
    before = _snapshot(root)
    with pytest.raises(CodeFingerprintError) as refused:
        _run(root, NEW, rescore_llm=rescore)
    assert isinstance(refused.value, CampaignError)
    assert OLD in str(refused.value) and NEW in str(refused.value)
    assert "--reuse-from" in str(refused.value)
    assert len(calls) == n_jobs
    assert _snapshot(root) == before


def test_reuse_from_the_stored_fingerprint_proceeds_and_is_recorded(
    tmp_path: Path, calls: Calls
) -> None:
    root = tmp_path / "s"
    _run(root, OLD)
    resumed = _run(root, NEW, reuse_from=OLD)
    assert resumed.skipped_done == len(baseline_units(TRUTH_IDS, tiny_config()))
    assert _sidecar(root) == [
        {"event": "establish", "fingerprint": OLD},
        {"event": "reuse", "fingerprint": NEW, "from": OLD},
    ]
    _run(root, NEW)  # the store is now NEW's: no permission needed, none added
    assert len(_sidecar(root)) == 2
    with pytest.raises(CodeFingerprintError):
        _run(root, OLD)  # and going back is another decision


@pytest.mark.parametrize(
    "wrong",
    ["commit=aaaa", OLD + " ", UNRECORDED, NEW],
    ids=["prefix", "near", "unrecorded", "itself"],
)
def test_reuse_from_a_wrong_value_refuses(
    tmp_path: Path, calls: Calls, wrong: str
) -> None:
    root = tmp_path / "s"
    _run(root, OLD)
    before = _snapshot(root)
    with pytest.raises(CodeFingerprintError) as refused:
        _run(root, NEW, reuse_from=wrong)
    assert OLD in str(refused.value)
    assert _snapshot(root) == before


def test_reuse_from_must_name_the_current_fingerprint_not_an_older_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s"
    guard_store(root, OLD)
    guard_store(root, NEW, reuse_from=OLD)
    third = "commit=cccc,tree=clean,uv.lock=1111"
    with pytest.raises(CodeFingerprintError):
        guard_store(root, third, reuse_from=OLD)
    assert guard_store(root, third, reuse_from=NEW) == FingerprintEntry(
        "reuse", third, NEW
    )


def test_a_wrong_reuse_from_refuses_even_when_the_fingerprint_matches(
    tmp_path: Path,
) -> None:
    """A permission that names something else means the operator's picture of
    the store is wrong; matching fingerprints do not excuse it."""
    root = tmp_path / "s"
    guard_store(root, OLD)
    with pytest.raises(CodeFingerprintError):
        guard_store(root, OLD, reuse_from=NEW)
    assert guard_store(root, OLD, reuse_from=OLD) is None
    assert len(_sidecar(root)) == 1


def test_a_fresh_store_refuses_a_reuse_permission(tmp_path: Path) -> None:
    root = tmp_path / "s"
    with pytest.raises(CodeFingerprintError):
        guard_store(root, NEW, reuse_from=OLD)
    assert not root.exists()


def test_an_empty_fingerprint_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CodeFingerprintError):
        guard_store(tmp_path / "s", "")
    with pytest.raises(CodeFingerprintError):
        guard_store(tmp_path / "s", "two\nlines")


# --------------------------------------------------------------------------
# Legacy stores: cells written before the guard existed
# --------------------------------------------------------------------------


def _legacy(root: Path) -> None:
    """A store with cells and no sidecar, as the smoke and the mini-pilot's
    (run with the ``calls`` fixture's fake jobs in place)."""
    _run(root, OLD)
    (root / FINGERPRINT_FILE).unlink()


def test_a_legacy_store_refuses_without_an_explicit_adoption(
    tmp_path: Path, calls: Calls
) -> None:
    root = tmp_path / "s"
    _legacy(root)
    n_jobs = len(calls)
    before = _snapshot(root)
    for reuse_from in (None, OLD, NEW):
        with pytest.raises(CodeFingerprintError) as refused:
            _run(root, NEW, reuse_from=reuse_from)
        assert f"--reuse-from {UNRECORDED}" in str(refused.value)
    assert len(calls) == n_jobs and _snapshot(root) == before


def test_a_legacy_store_is_adopted_with_reuse_from_unrecorded(
    tmp_path: Path, calls: Calls
) -> None:
    root = tmp_path / "s"
    _legacy(root)
    n_jobs = len(calls)
    resumed = _run(root, NEW, reuse_from=UNRECORDED)
    assert resumed.skipped_done == len(baseline_units(TRUTH_IDS, tiny_config()))
    assert len(calls) == n_jobs
    assert _sidecar(root) == [
        {"event": "reuse", "fingerprint": NEW, "from": UNRECORDED}
    ]
    _run(root, NEW)
    assert len(_sidecar(root)) == 1


@pytest.mark.parametrize("content", ["ledger", "detail", "llm"])
def test_any_stored_content_makes_a_store_legacy(tmp_path: Path, content: str) -> None:
    """A ledger (even an empty one), a detail file or a session record each
    count: ``recover`` would commit a stray detail, and a record is evidence of
    a session run by unrecorded code."""
    root = tmp_path / "s"
    if content == "ledger":
        ResultStore(root).close()
    elif content == "detail":
        (root / DETAILS_DIR).mkdir(parents=True)
        (root / DETAILS_DIR / "x.json").write_text("{}", encoding="utf-8")
    else:
        (root / "llm" / "abc").mkdir(parents=True)
    with pytest.raises(CodeFingerprintError):
        guard_store(root, NEW)
    assert not (root / FINGERPRINT_FILE).exists()


# --------------------------------------------------------------------------
# Append-only integrity of the sidecar
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lines",
    [
        ["not json"],
        [json.dumps({"event": "reuse", "fingerprint": NEW, "from": OLD})],
        [
            json.dumps({"event": "establish", "fingerprint": OLD}),
            json.dumps({"event": "reuse", "fingerprint": NEW, "from": NEW}),
        ],
        [
            json.dumps({"event": "establish", "fingerprint": OLD}),
            json.dumps({"event": "establish", "fingerprint": NEW}),
        ],
        [json.dumps({"event": "establish", "fingerprint": OLD, "x": 1})],
        [],
    ],
    ids=["json", "first-reuse", "broken-chain", "second-establish", "extra", "empty"],
)
def test_a_malformed_sidecar_refuses(tmp_path: Path, lines: list[str]) -> None:
    root = tmp_path / "s"
    root.mkdir()
    path = root / FINGERPRINT_FILE
    path.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CodeFingerprintError):
        read_fingerprints(root)
    with pytest.raises(CodeFingerprintError):
        guard_store(root, OLD)
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# Cell bytes do not depend on the sidecar
# --------------------------------------------------------------------------


def test_cell_bytes_are_identical_with_and_without_the_sidecar(
    tmp_path: Path, calls: Calls
) -> None:
    a, b, bare = tmp_path / "a", tmp_path / "b", tmp_path / "bare"
    _run(a, OLD)
    _run(b, NEW)
    assert (a / FINGERPRINT_FILE).read_bytes() != (b / FINGERPRINT_FILE).read_bytes()
    with ResultStore(a) as source, ResultStore(bare) as copy:
        for cell in source.cells():
            reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
            copy.put(key_from_json(cell["key"]), reading, cell["detail"])
    assert not (bare / FINGERPRINT_FILE).exists()
    for other in (b, bare):
        diff = compare_stores(a, other)
        assert diff.identical and diff.same == len(
            baseline_units(TRUTH_IDS, tiny_config())
        )
        details = sorted((other / DETAILS_DIR).iterdir())
        assert [p.read_bytes() for p in details] == [
            (a / DETAILS_DIR / p.name).read_bytes() for p in details
        ]
    check = check_store(a)
    assert check.passed and not check.warnings, check.issues + check.warnings


# --------------------------------------------------------------------------
# scripts/pilot.py: the fingerprint of a git checkout
# --------------------------------------------------------------------------


def _pilot(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("pilot")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "uv.lock").write_text("lock v1\n", encoding="utf-8")
    (root / ".gitignore").write_text("/data/\n.venv\n", encoding="utf-8")
    _git(root, "add", "uv.lock", ".gitignore")
    _git(root, "commit", "-q", "-m", "c")
    return root


def test_pilot_fingerprints_a_clean_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = _pilot(monkeypatch)
    head = _git(repo, "rev-parse", "HEAD")
    lock = hashlib.sha256(b"lock v1\n").hexdigest()
    expected = f"commit={head},tree=clean,uv.lock={lock}"
    assert pilot.code_fingerprint(repo) == expected
    (repo / "data" / "dev").mkdir(parents=True)  # ignored files do not count
    (repo / "data" / "dev" / "truths.json").write_text("{}", encoding="utf-8")
    (repo / ".venv").mkdir()
    assert pilot.code_fingerprint(repo) == expected
    (repo / "uv.lock").write_text("lock v2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "bump")
    bumped = pilot.code_fingerprint(repo)
    assert bumped != expected and hashlib.sha256(b"lock v2\n").hexdigest() in bumped


@pytest.mark.parametrize("dirt", ["modified", "untracked", "staged"])
def test_pilot_refuses_a_dirty_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch, dirt: str
) -> None:
    pilot = _pilot(monkeypatch)
    if dirt == "modified":
        (repo / "uv.lock").write_text("edited\n", encoding="utf-8")
    elif dirt == "untracked":
        (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    else:
        (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "new.py")
    with pytest.raises(CampaignError) as refused:
        pilot.code_fingerprint(repo)
    assert "not clean" in str(refused.value)


def test_pilot_refuses_a_directory_that_is_not_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = _pilot(monkeypatch)
    with pytest.raises(CampaignError):
        pilot.code_fingerprint(tmp_path)


def test_pilot_takes_reuse_from(monkeypatch: pytest.MonkeyPatch) -> None:
    pilot = _pilot(monkeypatch)
    args = pilot.build_parser().parse_args(["--reuse-from", UNRECORDED])
    assert args.reuse_from == UNRECORDED
    assert pilot.build_parser().parse_args([]).reuse_from is None
