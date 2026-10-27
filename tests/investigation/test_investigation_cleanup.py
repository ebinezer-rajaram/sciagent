"""Removing an AG-o run's sandbox directory never costs the run's record.

The sandbox directory is removed when the investigation ends. If an
ownership handoff was missed (a harness fault mid-call, a back handoff that
failed), ``work/`` still belongs to the container's uid and the host cannot
remove it. That used to raise out of :func:`~sciagent.investigation.run`
before the record was saved, so a finished run vanished. Now cleanup first
asks the sandbox to recover ``work/``; whatever still fails is recorded as an
infrastructure fault (the run is void) and the record is kept.

Docker is not needed: the image lookup and the recovery are replaced, and the
script never calls ``python``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from investigation_support import clean_environment, scripted_run

from sciagent.harness.scripted import Step
from sciagent.investigation import runner as runner_module
from sciagent.sandbox import Sandbox, SandboxError

SUBMIT = (
    Step(
        calls=(
            (
                "submit",
                {"structure": "Excite(ExpK, Mark(size), all)", "report": "done"},
            ),
        )
    ),
)


def _image_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image tag and the ID the record carries, without asking Docker."""
    monkeypatch.setattr(runner_module, "ensure_image", lambda: "sciagent-sandbox:test")
    monkeypatch.setattr(runner_module, "image_id", lambda *a, **k: "sha256:test")


def _open_lab_without_docker(
    monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    clean_environment(monkeypatch)
    _image_without_docker(monkeypatch)

    def recover(self: Sandbox) -> None:
        events.append("recover")

    monkeypatch.setattr(Sandbox, "recover", recover, raising=False)


def _run_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir())


def test_cleanup_recovers_work_then_removes_the_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _open_lab_without_docker(monkeypatch, events)
    real_rmtree = shutil.rmtree

    def rmtree(path: Path, ignore_errors: bool = False) -> None:
        events.append("rmtree")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", rmtree)
    outcome = scripted_run(
        SUBMIT, tmp_path / "out", arm="AG-o", sandbox_root=tmp_path / "sbx"
    )
    assert outcome.outcome == "submitted"
    assert not outcome.void, outcome.void_reasons
    assert events == ["recover", "rmtree"]
    assert _run_dirs(tmp_path / "sbx") == []


def test_an_unremovable_run_dir_keeps_the_record_and_voids_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The red-team finding: a missed handoff made ``shutil.rmtree`` raise
    ``PermissionError`` before the record was saved."""
    events: list[str] = []
    _open_lab_without_docker(monkeypatch, events)

    def unremovable(path: Path, ignore_errors: bool = False) -> None:
        raise PermissionError(13, "Permission denied", str(Path(path) / "work" / "x"))

    monkeypatch.setattr(shutil, "rmtree", unremovable)
    outcome = scripted_run(
        SUBMIT, tmp_path / "out", arm="AG-o", sandbox_root=tmp_path / "sbx"
    )
    assert outcome.record_path.is_file()
    assert outcome.outcome == "submitted"
    assert outcome.void
    assert outcome.void_reasons == ("infrastructure: sandbox cleanup: PermissionError",)
    # Left in place for the operator (logged), not silently dropped.
    assert len(_run_dirs(tmp_path / "sbx")) == 1


def test_a_failed_recovery_keeps_the_record_and_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    _image_without_docker(monkeypatch)

    def recover(self: Sandbox) -> None:
        raise SandboxError("docker inspect: the agent container is still there")

    monkeypatch.setattr(Sandbox, "recover", recover, raising=False)
    outcome = scripted_run(
        SUBMIT, tmp_path / "out", arm="AG-o", sandbox_root=tmp_path / "sbx"
    )
    assert outcome.record_path.is_file()
    assert outcome.void_reasons == ("infrastructure: sandbox cleanup: SandboxError",)
    assert len(_run_dirs(tmp_path / "sbx")) == 1
