"""The void policy in :func:`sciagent.investigation.runner.run` (P4 Δ8).

``run`` adds the lab's own reasons (sandbox infrastructure faults, the
out-of-bounds scan of ``python`` code) to the harness's foreign tool uses and
classifies them all with the shared policy of :mod:`sciagent.harness.voids`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from investigation_support import BELIEF, STOP, clean_environment, scripted_run

from sciagent.harness.scripted import Step

FIT = ("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]})
SUBMIT = (
    Step(calls=(FIT,)),
    Step(
        calls=(
            BELIEF,
            ("submit", {"structure": "Excite(ExpK, One, all)", "report": "r", **STOP}),
        )
    ),
    Step(text="done"),
)


def test_a_denied_bare_python_is_counted_not_void(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    outcome = scripted_run(
        (Step(calls=(("python", {"code": "print(1)"}),), raw_names=True), *SUBMIT),
        tmp_path,
    )
    assert outcome.outcome == "submitted"
    assert not outcome.void and outcome.void_reasons == ()
    assert outcome.unknown_tool_attempts == 1


def test_an_executed_foreign_tool_voids_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    outcome = scripted_run(
        (
            Step(
                calls=(("Bash", {"command": "ls"}),),
                raw_names=True,
                foreign="executed",
            ),
            *SUBMIT,
        ),
        tmp_path,
    )
    assert outcome.void
    assert outcome.void_reasons == ("foreign tool executed: Bash",)
    assert outcome.unknown_tool_attempts == 0


def test_a_server_tool_use_voids_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    outcome = scripted_run(
        (Step(calls=(FIT,), server_tools=("web_search",)), *SUBMIT[1:]), tmp_path
    )
    assert outcome.void
    assert outcome.void_reasons == ("server tool use: web_search",)


@pytest.mark.slow
def test_an_out_of_bounds_hit_voids_the_run(
    sandbox_image: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    outcome = scripted_run(
        (Step(calls=(("python", {"code": 'print("/proc")'}),)), *SUBMIT),
        tmp_path / "out",
        arm="AG-o",
        sandbox_root=tmp_path / "sbx",
    )
    assert outcome.outcome == "submitted", outcome
    assert outcome.void
    (reason,) = outcome.void_reasons
    assert reason.startswith("out of bounds: call ") and reason.endswith("/proc\\b")
    assert outcome.unknown_tool_attempts == 0
