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
OLD = "commit=aaaa,tree=clean"
NEW = "commit=bbbb,tree=clean"
#: U+2028 LINE SEPARATOR, which ``str.splitlines`` breaks on.
LS = chr(0x2028)

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
    assert not (root / FINGERPRINT_FILE).exists()


@pytest.mark.parametrize(
    "bad",
    ["", "two\nlines", "a b", "tab\there", "café", "nel\x85", "ls" + LS, UNRECORDED],
)
def test_a_malformed_fingerprint_is_refused(tmp_path: Path, bad: str) -> None:
    """Printable ASCII without spaces only, and never the legacy marker."""
    with pytest.raises(CodeFingerprintError):
        guard_store(tmp_path / "s", bad)
    assert not (tmp_path / "s" / FINGERPRINT_FILE).exists()


# --------------------------------------------------------------------------
# One driver per store, for the life of the campaign
# --------------------------------------------------------------------------


def _campaign(root: Path, fingerprint: str, reuse_from: str | None = None) -> Campaign:
    return Campaign(
        root,
        environment(),
        tiny_config(),
        [entry(t) for t in TRUTH_IDS],
        DriverSettings(workers=0, poll_s=0.05, run_llm=False),
        fingerprint=fingerprint,
        reuse_from=reuse_from,
    )


def test_a_second_driver_on_a_store_refuses_while_the_first_lives(
    tmp_path: Path, calls: Calls
) -> None:
    """Two drivers cannot both append a reuse from the same fingerprint, and an
    old driver cannot keep writing after a new fingerprint is recorded: the
    first campaign holds the store's lock until it is closed."""
    root = tmp_path / "s"
    first = _campaign(root, OLD)
    for fingerprint, reuse_from in ((OLD, None), (NEW, OLD)):
        with pytest.raises(CodeFingerprintError) as refused:
            _campaign(root, fingerprint, reuse_from)
        assert "another driver" in str(refused.value)
    with pytest.raises(CodeFingerprintError):
        guard_store(root, NEW, reuse_from=OLD)
    first.run()
    first.close()
    assert _sidecar(root) == [{"event": "establish", "fingerprint": OLD}]
    second = _campaign(root, NEW, reuse_from=OLD)
    second.close()
    assert len(_sidecar(root)) == 2


# --------------------------------------------------------------------------
# Crash safety: torn lines, atomic creation
# --------------------------------------------------------------------------


def test_a_torn_final_line_is_uncommitted_and_dropped_on_the_next_append(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s"
    guard_store(root, OLD)
    path = root / FINGERPRINT_FILE
    committed = path.read_bytes()
    with path.open("ab") as f:
        f.write(b'{"event":"reuse","fing')  # killed mid-append
    assert read_fingerprints(root) == (FingerprintEntry("establish", OLD, None),)
    assert guard_store(root, OLD) is None
    guard_store(root, NEW, reuse_from=OLD)
    assert path.read_bytes().startswith(committed)
    assert _sidecar(root) == [
        {"event": "establish", "fingerprint": OLD},
        {"event": "reuse", "fingerprint": NEW, "from": OLD},
    ]


@pytest.mark.parametrize("torn", [b"", b'{"event":"establ'])
def test_a_sidecar_with_no_committed_line_is_absent(
    tmp_path: Path, torn: bytes
) -> None:
    """An empty or torn first line (a crash before the first commit) does not
    lock the store out: there is no committed fingerprint, so the store is new
    (or legacy, by its content)."""
    root = tmp_path / "s"
    root.mkdir()
    (root / FINGERPRINT_FILE).write_bytes(torn)
    assert read_fingerprints(root) == ()
    assert guard_store(root, OLD) == FingerprintEntry("establish", OLD, None)
    assert _sidecar(root) == [{"event": "establish", "fingerprint": OLD}]


def test_a_line_separator_inside_a_line_is_not_a_line_break(tmp_path: Path) -> None:
    """Lines split on ``\\n`` only: U+2028 or U+0085 inside a JSON string must
    not split a committed line (``str.splitlines`` would)."""
    root = tmp_path / "s"
    root.mkdir()
    body = {"event": "establish", "fingerprint": "x" + LS + "y\x85z"}
    (root / FINGERPRINT_FILE).write_text(
        json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    assert read_fingerprints(root) == (
        FingerprintEntry("establish", "x" + LS + "y\x85z", None),
    )


def test_the_first_line_is_created_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first line is written to a temporary file, synced and linked into
    place, so a crash leaves either no sidecar or a whole line."""
    import os

    linked: list[tuple[str, bytes]] = []
    real_link = os.link

    def spy(src: str | Path, dst: str | Path) -> None:
        linked.append((Path(dst).name, Path(src).read_bytes()))
        real_link(src, dst)

    monkeypatch.setattr(os, "link", spy)
    root = tmp_path / "s"
    guard_store(root, OLD)
    line = (root / FINGERPRINT_FILE).read_bytes()
    assert linked == [(FINGERPRINT_FILE, line)] and line.endswith(b"\n")
    assert sorted(p.name for p in root.iterdir() if p.name.startswith(".")) == []


def test_the_driver_and_the_guard_agree_on_the_session_directory() -> None:
    from sciagent.campaign import fingerprint as fp_module

    assert fp_module.LLM_DIR == driver_module.LLM_DIR


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
        [json.dumps({"event": "establish", "fingerprint": OLD}), ""],
    ],
    ids=["json", "first-reuse", "broken-chain", "second-establish", "extra", "blank"],
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


def _inside(repo: Path) -> tuple[Path, Path]:
    """Where ``sciagent`` and ``environments`` would be imported from if
    ``repo``'s own ``src`` were the one running."""
    src = repo / "src"
    return (src / "sciagent" / "__init__.py", src / "environments" / "__init__.py")


def test_pilot_fingerprints_a_clean_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``commit=<sha>,tree=clean``: ``uv.lock`` is tracked, so the commit and
    the clean tree already cover it."""
    pilot = _pilot(monkeypatch)
    head = _git(repo, "rev-parse", "HEAD")
    expected = f"commit={head},tree=clean"
    assert pilot.code_fingerprint(repo, _inside(repo)) == expected
    (repo / "data" / "dev").mkdir(parents=True)  # ignored files do not count
    (repo / "data" / "dev" / "truths.json").write_text("{}", encoding="utf-8")
    (repo / ".venv").mkdir()
    assert pilot.code_fingerprint(repo, _inside(repo)) == expected
    (repo / "uv.lock").write_text("lock v2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "bump")
    assert pilot.code_fingerprint(repo, _inside(repo)) != expected


@pytest.mark.parametrize(
    "dirt",
    ["modified", "untracked", "staged", "untracked-hidden", "assume-unchanged", "skip"],
)
def test_pilot_refuses_a_dirty_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch, dirt: str
) -> None:
    """Also when git's configuration would hide it: untracked files hidden by
    ``status.showUntrackedFiles=no``, and an edit under an assume-unchanged or
    skip-worktree flag."""
    pilot = _pilot(monkeypatch)
    if dirt == "modified":
        (repo / "uv.lock").write_text("edited\n", encoding="utf-8")
    elif dirt == "untracked":
        (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    elif dirt == "staged":
        (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "new.py")
    elif dirt == "untracked-hidden":
        _git(repo, "config", "status.showUntrackedFiles", "no")
        (repo / "pkg").mkdir()
        (repo / "pkg" / "new.py").write_text("x = 1\n", encoding="utf-8")
    else:
        flag = "--assume-unchanged" if dirt == "assume-unchanged" else "--skip-worktree"
        _git(repo, "update-index", flag, "uv.lock")
        (repo / "uv.lock").write_text("edited\n", encoding="utf-8")
    with pytest.raises(CampaignError) as refused:
        pilot.code_fingerprint(repo, _inside(repo))
    assert "not clean" in str(refused.value)


def test_pilot_refuses_a_directory_that_is_not_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = _pilot(monkeypatch)
    with pytest.raises(CampaignError):
        pilot.code_fingerprint(tmp_path, _inside(tmp_path))


def test_pilot_refuses_a_copy_inside_another_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uncommitted copy under an ignored path of a parent repository would
    otherwise get the parent's HEAD and ``tree=clean``."""
    pilot = _pilot(monkeypatch)
    copy = repo / "data" / "copy"
    (copy / "scripts").mkdir(parents=True)
    (copy / "scripts" / "pilot.py").write_text("# edited copy\n", encoding="utf-8")
    with pytest.raises(CampaignError) as refused:
        pilot.code_fingerprint(copy, _inside(copy))
    assert "not its own git checkout" in str(refused.value)


def test_pilot_refuses_code_imported_from_another_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fingerprint names ``repo``'s commit, so the running packages must be
    ``repo``'s: ``uv run`` may have picked another checkout's venv."""
    pilot = _pilot(monkeypatch)
    elsewhere = _inside(repo.parent / "other")
    for packages in (elsewhere, (_inside(repo)[0], elsewhere[1])):
        with pytest.raises(CampaignError) as refused:
            pilot.code_fingerprint(repo, packages)
        assert "imported from" in str(refused.value)
    # The default reads the running packages, and this checkout runs its own
    # (the tree may be dirty while tests run, so the git half is not called).
    running = pilot._running_packages()
    assert [p.name for p in running] == ["__init__.py", "__init__.py"]
    assert all(p.resolve().is_relative_to(pilot.ROOT / "src") for p in running)


def _fake_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fingerprints: list[object]
) -> tuple[ModuleType, list[object]]:
    """``pilot.main`` with its campaign, its wait and its fingerprints faked;
    returns the module and the event log."""
    import sys

    pilot = _pilot(monkeypatch)
    events: list[object] = []
    queue = list(fingerprints)

    def fingerprint(*args: object) -> str:
        events.append("fingerprint")
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return str(value)

    class FakeCampaign:
        def __init__(
            self,
            root: Path,
            env: object,
            config: object,
            truths: object,
            settings: DriverSettings,
            *,
            fingerprint: str,
            reuse_from: str | None = None,
        ) -> None:
            events.append(("campaign", fingerprint, reuse_from, settings.rescore_llm))

        def run(self) -> str:
            events.append("run")
            return "summary"

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(pilot, "code_fingerprint", fingerprint)
    monkeypatch.setattr(pilot, "wait_for_split", lambda *a: events.append("wait"))
    monkeypatch.setattr(driver_module, "Campaign", FakeCampaign)
    argv = ["pilot.py", "--root", str(tmp_path / "s"), "--ids", "dev-006"]
    argv += ["--seeds", "1", "--wait", "1", "--reuse-from", "fp-0", "--rescore-llm"]
    monkeypatch.setattr(sys, "argv", argv)
    return pilot, events


def test_pilot_main_passes_the_fingerprint_taken_after_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot, events = _fake_main(monkeypatch, tmp_path, ["fp-1", "fp-1"])
    pilot.main()
    assert events == [
        "wait",
        "fingerprint",
        ("campaign", "fp-1", "fp-0", True),
        "run",
        "close",
        "fingerprint",
    ]


@pytest.mark.parametrize(
    "after", ["fp-2", CampaignError("the working tree is not clean")]
)
def test_pilot_main_fails_when_the_code_changed_during_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: object
) -> None:
    """Workers and LLM sessions import from disk during the run, so a change
    under a running campaign is an error, not a pass."""
    pilot, events = _fake_main(monkeypatch, tmp_path, ["fp-1", after])
    with pytest.raises(SystemExit) as stopped:
        pilot.main()
    assert stopped.value.code not in (0, None)
    assert "changed during the run" in str(stopped.value.code)
    assert events[-2:] == ["close", "fingerprint"]


def test_pilot_takes_reuse_from(monkeypatch: pytest.MonkeyPatch) -> None:
    pilot = _pilot(monkeypatch)
    args = pilot.build_parser().parse_args(["--reuse-from", UNRECORDED])
    assert args.reuse_from == UNRECORDED
    assert pilot.build_parser().parse_args([]).reuse_from is None
