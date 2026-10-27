"""Fault paths of the ownership handoff, with Docker faked (fast tier).

The sandbox boundary is an instrument (SPEC §6.3 no. 8), and the handoff is
part of it: it decides who owns ``work/`` between calls, and a handoff that
runs too early (agent code still alive), not at all (a fault before it) or
to the wrong owner (platform mis-detected) breaks either isolation or the
run's record. Real containers cannot be made to fail on cue, so these tests
replace ``subprocess`` with a scripted Docker CLI and check what the sandbox
does with each answer. The real-container counterparts are in
``test_sandbox_isolation.py``.
"""

from __future__ import annotations

import io
import itertools
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from sciagent.sandbox import (
    Sandbox,
    SandboxError,
    SandboxLimits,
    SandboxPlatformError,
    SandboxUnavailableError,
    runner,
)

IMAGE = "sciagent-sandbox:test"
ROOTLESS_INFO = {
    "OSType": "linux",
    "OperatingSystem": "Ubuntu 24.04.5 LTS",
    "SecurityOptions": ["name=seccomp,profile=builtin", "name=rootless"],
}


def _done(
    argv: Sequence[str], rc: int = 0, out: bytes = b"", err: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(list(argv), rc, out, err)


def _mount_source(argv: Sequence[str], target: str) -> Path | None:
    for a, b in itertools.pairwise(argv):
        if a == "--mount" and b.endswith(f",target={target}"):
            match = re.search(r"source=([^,]+)", b)
            assert match is not None
            return Path(match.group(1))
    return None


class FakeDocker:
    """A scripted Docker CLI: answers ``subprocess.run`` by subcommand.

    Agent containers (``sciagent-sbx-*``) are alive after their client exits
    when ``agent_lingers`` is set, as if ``docker rm -f`` had failed. Helper
    containers report ``helper_stdout`` and exit with ``helper_rc[owner]``.
    The platform probe (``--user 65534:65534`` with a ``/probe`` mount) writes
    its file as the current user, which is what Docker Desktop would do.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.info: dict[str, Any] = dict(ROOTLESS_INFO)
        self.agent_lingers = False
        self.rm_times_out = False
        self.helper_rc: dict[str, int] = {}
        self.helper_stdout: dict[str, bytes] = {}
        self.helper_times_out: set[str] = set()
        self.mounting: list[str] = []  # ids ``docker ps --filter volume=`` lists
        self.stuck: set[str] = set()  # ids ``docker rm -f`` cannot remove
        self.timeouts: list[float] = []

    @property
    def handoffs(self) -> list[str]:
        """The owner of every handoff helper run, in order."""
        return [o for o in (self._owner(c) for c in self.calls) if o is not None]

    @staticmethod
    def _owner(argv: Sequence[str]) -> str | None:
        if len(argv) < 2 or argv[1] != "run" or _mount_source(argv, "/work") is None:
            return None
        if argv[-1] == "/work":  # python helper: ... owner entries path /work
            return argv[-4]
        match = re.search(r"chown -hRP (\S+) /work", argv[-1])
        return None if match is None else match.group(1)

    def __call__(
        self, argv: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        argv = list(argv)
        self.calls.append(argv)
        sub = argv[1]
        if sub == "version":
            return _done(argv, out=b"27.0.0\n")
        if sub == "info":
            return _done(argv, out=json.dumps(self.info).encode())
        if sub == "inspect":
            name = argv[-1]
            lingers = self.agent_lingers and name.startswith("sciagent-sbx-")
            if lingers or name in self.mounting:
                return _done(argv, out=b"abc123\n")
            return _done(
                argv,
                1,
                err=f"Error response from daemon: No such container: {name}".encode(),
            )
        if sub == "rm":
            if self.rm_times_out:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 0.0)
            if argv[-1] not in self.stuck and argv[-1] in self.mounting:
                self.mounting.remove(argv[-1])
            return _done(argv)
        if sub == "ps":
            return _done(argv, out="".join(f"{c}\n" for c in self.mounting).encode())
        if sub == "run":
            probe = _mount_source(argv, "/probe")
            if probe is not None:
                (probe / "p").touch()
                return _done(argv)
            owner = self._owner(argv)
            assert owner is not None, argv
            self.timeouts.append(float(kwargs["timeout"]))
            if owner in self.helper_times_out:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            rc = self.helper_rc.get(owner, 0)
            out = self.helper_stdout.get(owner, b"ok\n")
            return _done(argv, rc, out if rc == 0 else b"", b"" if rc == 0 else b"boom")
        raise AssertionError(f"unexpected docker call {argv}")


class FakePopen:
    """The agent's ``docker run`` client: prints, exits 0, or hangs past the wall."""

    hang = False
    started: ClassVar[list[list[str]]] = []

    def __init__(self, argv: Sequence[str], **kwargs: Any) -> None:
        self.argv = list(argv)
        self.started.append(self.argv)
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"hello\n")
        self.stderr = io.BytesIO(b"")
        cid = Path(self.argv[self.argv.index("--cidfile") + 1])
        cid.write_text("abc123\n", encoding="utf-8")
        self._waited = False

    def wait(self, timeout: float | None = None) -> int:
        if self.hang and not self._waited:
            self._waited = True
            raise subprocess.TimeoutExpired(self.argv, timeout or 0.0)
        return 137 if self.hang else 0

    def kill(self) -> None:
        return None


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(FakePopen, "hang", False)
    monkeypatch.setattr(FakePopen, "started", [])
    # Polling for a container to go must not wait in a fast test.
    monkeypatch.setattr(runner, "_GONE_TIMEOUT_S", 0.0, raising=False)
    monkeypatch.setattr(runner, "_GONE_POLL_S", 0.0, raising=False)
    return fake


def _sandbox(tmp_path: Path, **limits: Any) -> Sandbox:
    return Sandbox(tmp_path, image=IMAGE, limits=SandboxLimits(**limits), seed=1)


def _skip_platform_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_verify_platform", lambda *a, **k: None, raising=False)


# --------------------------------------------------------------------------
# The ordinary path, as a baseline for the fault paths below
# --------------------------------------------------------------------------


def test_a_call_is_bracketed_by_both_handoffs(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    result = _sandbox(tmp_path).run("print('hello')")
    assert result.stdout == "hello\n" and result.exit_code == 0
    assert docker.handoffs == ["65534:65534", "0:0"]


# --------------------------------------------------------------------------
# 1. The back handoff waits until the agent container is gone
# --------------------------------------------------------------------------


def test_back_handoff_refuses_while_the_agent_container_is_alive(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container that survives ``docker rm -f`` still runs agent code; a
    chown while it runs could hand the host files the code is still writing."""
    _skip_platform_probe(monkeypatch)
    docker.agent_lingers = True
    with pytest.raises(SandboxError, match="still"):
        _sandbox(tmp_path).run("print('hello')")
    assert docker.handoffs == ["65534:65534"], "handed back under a live container"
    assert any(c[1] == "inspect" for c in docker.calls)


def test_a_timed_out_docker_rm_is_not_an_untyped_error(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``docker rm -f`` timing out after a wall-time kill: the container is
    then checked by ``docker inspect``, not left to raise ``TimeoutExpired``."""
    _skip_platform_probe(monkeypatch)
    monkeypatch.setattr(FakePopen, "hang", True)
    docker.rm_times_out = True
    result = _sandbox(tmp_path).run("while True: pass")
    assert result.timed_out
    assert docker.handoffs == ["65534:65534", "0:0"]


def test_a_container_that_cannot_be_removed_is_a_typed_error(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    monkeypatch.setattr(FakePopen, "hang", True)
    docker.rm_times_out = True
    docker.agent_lingers = True
    with pytest.raises(SandboxError):
        _sandbox(tmp_path).run("while True: pass")
    assert docker.handoffs == ["65534:65534"]


# --------------------------------------------------------------------------
# 2. Both handoffs under one try/finally, and ownership checked afterwards
# --------------------------------------------------------------------------


def test_a_failed_forward_handoff_still_hands_work_back(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forward chown may have given part of ``work/`` away before it
    failed; without the back handoff the host could not remove it."""
    _skip_platform_probe(monkeypatch)
    docker.helper_rc["65534:65534"] = 1
    with pytest.raises(SandboxError, match="65534"):
        _sandbox(tmp_path).run("print('hello')")
    assert docker.handoffs == ["65534:65534", "0:0"]
    assert FakePopen.started == [], "the agent's code ran after a failed handoff"


def test_work_not_owned_by_the_host_after_the_handoff_is_an_error(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    monkeypatch.setattr(runner, "_host_uid", lambda: os.getuid() + 1, raising=False)
    with pytest.raises(SandboxError, match="owned"):
        _sandbox(tmp_path).run("print('hello')")


def test_a_sandbox_left_in_an_unknown_state_refuses_further_calls(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a back handoff failed, the next forward handoff must not run
    against a ``work/`` (or a container) in an unknown state."""
    _skip_platform_probe(monkeypatch)
    sb = _sandbox(tmp_path)
    docker.agent_lingers = True
    with pytest.raises(SandboxError):
        sb.run("print('hello')")
    docker.agent_lingers = False
    before = len(docker.calls)
    with pytest.raises(SandboxError, match="unusable"):
        sb.run("print('again')")
    assert docker.calls[before:] == [], "touched Docker after the sandbox broke"


# --------------------------------------------------------------------------
# 3. A work/ grown past its entry limit is emptied and reported, not voided
# --------------------------------------------------------------------------


def test_an_emptied_work_dir_is_reported_to_the_agent(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    docker.helper_stdout["0:0"] = b"emptied\n"
    result = _sandbox(tmp_path).run("print('hello')")
    assert result.stdout == "hello\n" and result.exit_code == 0
    assert "work/ was emptied" in result.stderr
    assert "100000 entries" in result.stderr


def test_a_helper_that_prints_garbage_is_a_typed_error(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    docker.helper_stdout["0:0"] = b"what\n"
    with pytest.raises(SandboxError, match="helper"):
        _sandbox(tmp_path).run("print('hello')")


# --------------------------------------------------------------------------
# 5. Platform detection: docker info parsing, and a check against reality
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (ROOTLESS_INFO, "0:0"),
        (
            {**ROOTLESS_INFO, "SecurityOptions": ["name=seccomp,profile=builtin"]},
            f"{os.getuid()}:{os.getgid()}",
        ),
        ({**ROOTLESS_INFO, "OperatingSystem": "Docker Desktop"}, None),
    ],
)
def test_probe_reads_the_daemon_kind_from_docker_info(
    docker: FakeDocker, info: dict[str, Any], expected: str | None
) -> None:
    docker.info = info
    assert runner._probe_handoff_owner("docker") == expected


@pytest.mark.parametrize(
    "stdout",
    [
        b"not json",
        b"[]",
        json.dumps({"OperatingSystem": "Ubuntu"}).encode(),
        json.dumps({**ROOTLESS_INFO, "SecurityOptions": "name=rootless"}).encode(),
        json.dumps({**ROOTLESS_INFO, "OperatingSystem": 7}).encode(),
    ],
)
def test_probe_refuses_unreadable_docker_info(
    monkeypatch: pytest.MonkeyPatch, stdout: bytes
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: _done(argv, out=stdout))
    with pytest.raises(SandboxError):
        runner._probe_handoff_owner("docker")


@pytest.mark.parametrize(
    "info",
    [
        # A non-string option was coerced with str() and matched quietly.
        {**ROOTLESS_INFO, "SecurityOptions": [{"name": "rootless"}]},
        # A Windows-container daemon cannot run the Linux image at all.
        {**ROOTLESS_INFO, "OSType": "windows"},
        {k: v for k, v in ROOTLESS_INFO.items() if k != "OSType"},
    ],
)
def test_probe_fails_loudly_on_an_unexpected_platform(
    docker: FakeDocker, info: dict[str, Any]
) -> None:
    docker.info = info
    with pytest.raises(SandboxPlatformError):
        runner._probe_handoff_owner("docker")


def test_probe_failures_are_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(argv: Sequence[str], **k: Any) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError(argv[0])

    def slow(argv: Sequence[str], **k: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 1.0)

    def failing(argv: Sequence[str], **k: Any) -> subprocess.CompletedProcess[bytes]:
        return _done(argv, 0 if argv[1] == "version" else 1, err=b"bad")

    def down(argv: Sequence[str], **k: Any) -> subprocess.CompletedProcess[bytes]:
        return _done(argv, 1, err=b"Cannot connect to the Docker daemon")

    for fake, error in (
        (missing, SandboxUnavailableError),
        (slow, SandboxUnavailableError),
        (down, SandboxUnavailableError),
        (failing, SandboxError),
    ):
        monkeypatch.setattr(subprocess, "run", fake)
        with pytest.raises(error):
            runner._probe_handoff_owner("docker")


def test_probe_asks_nothing_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(argv: Sequence[str], **k: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("asked Docker off Linux")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(sys, "platform", "win32")
    assert runner._probe_handoff_owner("docker") is None


@pytest.mark.parametrize(
    "owner",
    [
        "0:0",  # docker info said rootless ...
        f"{os.getuid()}:{os.getgid()}",  # ... or rootful ...
    ],
)
def test_detection_contradicted_by_the_daemon_fails_loudly(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    """... but a file written by container uid 65534 comes back owned by the
    host user, as under Docker Desktop. Handing ``work/`` around on that
    platform would be wrong, and nothing else would notice."""
    monkeypatch.setattr(runner, "_probe_handoff_owner", lambda docker: owner)
    with pytest.raises(SandboxPlatformError):
        _sandbox(tmp_path).run("print('hello')")
    assert docker.handoffs == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "work"]


def test_desktop_detection_confirmed_by_the_daemon_runs(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_probe_handoff_owner", lambda docker: None)
    result = _sandbox(tmp_path).run("print('hello')")
    assert result.stdout == "hello\n"
    assert docker.handoffs == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "work"]


@pytest.mark.parametrize(
    ("owner", "file_uid", "ok"),
    [
        (None, 1000, True),
        (None, 165533, False),
        (None, 65534, False),
        ("0:0", 165533, True),
        ("0:0", 1000, False),
        ("0:0", 65534, False),
        ("0:0", 0, False),
        ("1000:1000", 65534, True),
        ("1000:1000", 1000, False),
        ("1000:1000", 165533, False),
    ],
)
def test_platform_check_table(owner: str | None, file_uid: int, ok: bool) -> None:
    check = runner._check_platform
    if ok:
        check(owner, file_uid=file_uid, host_uid=1000)
    else:
        with pytest.raises(SandboxPlatformError):
            check(owner, file_uid=file_uid, host_uid=1000)


# --------------------------------------------------------------------------
# Timeouts and recovery
# --------------------------------------------------------------------------


def test_the_handoff_timeout_grows_with_the_wall_time() -> None:
    """20 s per second of wall time: a 60 s call nesting directories built a
    chain 910,000 deep, and emptying it took about 390 s (LOG 2026-10-27)."""
    assert runner._handoff_timeout(SandboxLimits()) == 1200.0
    assert runner._handoff_timeout(SandboxLimits(wall_seconds=5)) == 300.0
    assert runner._handoff_timeout(SandboxLimits(wall_seconds=120)) == 2400.0


def test_a_recovery_allows_at_least_a_call_s_handoff_time(
    docker: FakeDocker, tmp_path: Path
) -> None:
    (tmp_path / "work").mkdir()
    runner.recover(tmp_path, image=IMAGE, limits=SandboxLimits(wall_seconds=120))
    assert docker.timeouts == [2400.0]


def test_a_handoff_that_times_out_is_removed_and_typed(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    docker.helper_times_out.add("0:0")
    with pytest.raises(SandboxError, match="longer than 1200 s"):
        _sandbox(tmp_path).run("print('hello')")
    assert docker.timeouts == [1200.0, 1200.0]
    removed = [c[-1] for c in docker.calls if c[1] == "rm"]
    assert len(removed) == 1 and removed[0].startswith("sciagent-own-")


def test_recover_removes_containers_then_hands_work_back(
    docker: FakeDocker, tmp_path: Path
) -> None:
    (tmp_path / "work").mkdir()
    docker.mounting = ["agent1", "helper2"]
    runner.recover(tmp_path, image=IMAGE)
    assert docker.mounting == []
    subs = [c[1] for c in docker.calls]
    assert subs.index("rm") < subs.index("run"), "handed back before removing"
    assert docker.handoffs == ["0:0"]
    assert docker.timeouts == [1800.0]


def test_recover_refuses_while_a_container_cannot_be_removed(
    docker: FakeDocker, tmp_path: Path
) -> None:
    (tmp_path / "work").mkdir()
    docker.mounting = ["agent1"]
    docker.stuck = {"agent1"}
    with pytest.raises(SandboxError, match="still there"):
        runner.recover(tmp_path, image=IMAGE)
    assert docker.handoffs == []


def test_recover_run_dirs_tries_every_directory_before_raising(
    docker: FakeDocker, tmp_path: Path
) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name / "work").mkdir(parents=True)
    (tmp_path / "stray-file").touch()
    docker.helper_rc["0:0"] = 1
    with pytest.raises(SandboxError) as caught:
        runner.recover_run_dirs(tmp_path, image=IMAGE)
    assert docker.handoffs == ["0:0", "0:0", "0:0"]
    for name in ("a", "b", "c"):
        assert str(tmp_path / name) in str(caught.value)


def test_sandbox_recover_asks_docker_nothing_after_a_clean_call(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    sb = _sandbox(tmp_path)
    sb.recover()  # never ran
    sb.run("print('hello')")
    before = len(docker.calls)
    sb.recover()
    assert docker.calls[before:] == []


def test_sandbox_recover_after_a_failed_back_handoff_hands_work_back(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_platform_probe(monkeypatch)
    sb = _sandbox(tmp_path)
    docker.helper_rc["0:0"] = 1
    with pytest.raises(SandboxError):
        sb.run("print('hello')")
    docker.helper_rc["0:0"] = 0
    sb.recover()
    assert docker.handoffs == ["65534:65534", "0:0", "0:0"]
    assert docker.timeouts[-1] == 1800.0


# --------------------------------------------------------------------------
# Red-team follow-ups (JOURNAL 2026-10-27, lane session 3)
# --------------------------------------------------------------------------


def test_both_handoffs_failing_still_leave_work_to_recover(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``work/`` counts as handed away from before the forward handoff: one
    that fails part way may have given some of it to 65534 already, and if
    the back handoff fails too, only :meth:`Sandbox.recover` hands it back."""
    _skip_platform_probe(monkeypatch)
    sb = _sandbox(tmp_path)
    docker.helper_rc = {"65534:65534": 1, "0:0": 1}
    with pytest.raises(SandboxError):
        sb.run("print('hello')")
    docker.helper_rc = {}
    sb.recover()
    assert docker.handoffs == ["65534:65534", "0:0", "0:0"]


def test_work_gone_after_the_back_handoff_is_a_typed_error(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ownership check after the back handoff raises a sandbox error, not
    ``FileNotFoundError``, so the sandbox is marked unusable like any other
    failed back handoff."""
    _skip_platform_probe(monkeypatch)
    sb = _sandbox(tmp_path)
    handoff = runner.Sandbox._handoff

    def vanish(self: Sandbox, owner: str) -> bool:
        emptied = handoff(self, owner)
        if owner == "0:0":
            self.work_dir.rmdir()
        return emptied

    monkeypatch.setattr(runner.Sandbox, "_handoff", vanish)
    with pytest.raises(SandboxError, match="work/"):
        sb.run("print('hello')")
    with pytest.raises(SandboxError, match="unusable"):
        sb.run("print('again')")


def test_the_sweep_walks_a_chain_deeper_than_the_recursion_limit(
    tmp_path: Path,
) -> None:
    """``work/`` may be deep: up to 512 levels after a back handoff, any
    depth where there is no handoff (Docker Desktop). The host's sweep must
    walk it without recursing per level (Python's recursion limit is 1,000)."""
    work = tmp_path / "work"
    work.mkdir()
    rel = "/".join(["d"] * 1024)
    assert len(rel) == 2047
    bottom = work
    for _ in range(1024):  # not mkdir(parents=True): it recurses per level
        bottom = bottom / "d"
        bottom.mkdir()
    assert bottom == work / rel
    os.mkfifo(bottom / "p")
    os.symlink("/", work / "d" / "l")
    try:
        removed = runner._sweep_special_files(work, os.getuid())
        assert removed == [f"{rel}/p", "d/l"]
        assert bottom.is_dir() and not (bottom / "p").exists()
    finally:
        # pytest removes tmp_path with shutil.rmtree, which recurses per level.
        subprocess.run(["rm", "-rf", "--", str(work / "d")], check=True)


def test_the_sweep_checks_a_directory_before_removing_from_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an owner to check, nothing is removed from a directory holding an
    entry that is not the host user's."""
    work = tmp_path / "work"
    work.mkdir()
    os.mkfifo(work / "a")
    (work / "b").touch()
    real_lstat = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        st = real_lstat(path, *args, **kwargs)
        if Path(path).name == "b":
            fields = list(st)
            fields[4] = os.getuid() + 1  # st_uid
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(SandboxError, match="work/b"):
        runner._sweep_special_files(work, os.getuid())
    assert (work / "a").exists()


def test_recover_run_dirs_finds_containers_under_a_symlinked_root(
    docker: FakeDocker, tmp_path: Path
) -> None:
    """Containers are found by ``docker ps --filter volume=<work>``, an exact
    match on the mount source, and :class:`Sandbox` mounts the resolved path;
    recovery must ask for the resolved path too."""
    real = tmp_path / "real"
    (real / "run" / "work").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    runner.recover_run_dirs(link, image=IMAGE)
    asked = [c[-1] for c in docker.calls if c[1] == "ps"]
    assert asked and set(asked) == {f"volume={(real / 'run' / 'work').as_posix()}"}
    assert {str(_mount_source(c, "/work")) for c in docker.calls if c[1] == "run"} == {
        str(real / "run" / "work")
    }
