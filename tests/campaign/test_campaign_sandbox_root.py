"""An isolated AG-o session's sandbox is recovered by the parent, however it ends.

A session killed at its hard deadline (``run_session_isolated``) leaves its
sandbox behind: the agent's container running, ``work/`` owned by the
container's uid. The parent gives each AG-o session a sandbox root of its
own and, once the session has ended, recovers every run directory in it and
removes it. The process part is replaced here (the spawn and kill paths run
in ``test_campaign_slow.py``), and so is the recovery, which needs Docker
(``tests/sandbox`` runs it for real).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import llm
from sciagent.campaign.llm import SessionOutcome, run_session_isolated
from sciagent.campaign.plan import CampaignConfig, LLMArm, Unit
from sciagent.sandbox import SandboxError

OPEN = Unit("hawkes", 1, "AG-o-H/named")
CLOSED = Unit("hawkes", 1, "AG-c-H/named")


def _config() -> CampaignConfig:
    return tiny_config(
        seeds=(1,), llm_arms=(LLMArm("AG-c", "named"), LLMArm("AG-o", "named"))
    )


def _killed(unit: Unit) -> SessionOutcome:
    detail = {"outcome": "hard_timeout", "error": "HardTimeout"}
    reading = {"status": 0.0, "void": 1.0}
    return SessionOutcome(unit, False, reading, detail, None, (), 1.0)


class Recorder:
    """Stands in for ``recover_run_dirs`` and ``_run_isolated``."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.recovered: list[Path] = []
        self.roots: list[Path | None] = []

    def recover(self, root: Path, *, image: str) -> None:
        self.recovered.append(root)
        if self.fail:
            raise SandboxError(f"cannot recover {root}/run: still mounted")

    def session(
        self,
        unit: Unit,
        truth: object,
        env: object,
        config: object,
        out_dir: Path,
        sandbox_root: Path | None,
        grace_s: float,
    ) -> SessionOutcome:
        self.roots.append(sandbox_root)
        if sandbox_root is not None:
            # What a killed session leaves: a run directory with work/ in it.
            (sandbox_root / "tmpabc" / "work").mkdir(parents=True)
        return _killed(unit)


def _install(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> None:
    monkeypatch.setattr(llm, "_run_isolated", recorder.session)
    monkeypatch.setattr(llm, "recover_run_dirs", recorder.recover)


def test_an_open_session_gets_its_own_root_recovered_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    _install(monkeypatch, recorder)
    sandboxes = tmp_path / "sbx"
    out = run_session_isolated(
        OPEN,
        entry("hawkes"),
        environment(),
        _config(),
        tmp_path,
        sandbox_root=sandboxes,
    )
    (root,) = recorder.roots
    assert root is not None and root.parent == sandboxes
    assert recorder.recovered == [root]
    assert not root.exists()
    assert list(sandboxes.iterdir()) == []
    assert out.detail == _killed(OPEN).detail


def test_a_failed_recovery_is_noted_and_the_root_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(fail=True)
    _install(monkeypatch, recorder)
    out = run_session_isolated(
        OPEN,
        entry("hawkes"),
        environment(),
        _config(),
        tmp_path,
        sandbox_root=tmp_path / "sbx",
    )
    (root,) = recorder.roots
    assert root is not None and (root / "tmpabc" / "work").is_dir()
    # The type only: the message carries random paths, and details are stored.
    assert out.detail["sandbox_cleanup"] == "SandboxError"
    assert out.detail["outcome"] == "hard_timeout"


def test_a_closed_session_has_no_sandbox_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    _install(monkeypatch, recorder)
    run_session_isolated(
        CLOSED,
        entry("hawkes"),
        environment(),
        _config(),
        tmp_path,
        sandbox_root=tmp_path / "sbx",
    )
    assert recorder.roots == [None]
    assert recorder.recovered == []
    assert not (tmp_path / "sbx").exists()
