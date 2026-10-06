"""Every tool result shows the remaining budget (P4 Δ12).

In the pilot no pre-submit text mentioned the budget. Now each result, a
success or an error, a handler's or the layer's own refusal, ends with one line
in one format: the experiments and fits left. Counts are unit-free, so the
line is the same in both naming conditions. The prompt says so, and also
describes design generators and truncation at the event cap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from investigation_support import BELIEF_ARGS, STOP, lab, plain_run

from sciagent.investigation.prompts import Arm, system_prompt
from sciagent.investigation.view import Condition
from sciagent.investigation.world import MAX_EVENTS


def budget(experiments: int, of_e: int, fits: int, of_f: int) -> str:
    return f"budget left: {experiments} of {of_e} experiments, {fits} of {of_f} fits"


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_every_result_ends_with_the_budget_line(condition: Condition) -> None:
    the_lab = lab("AG-c", condition, experiments=1, fits=1)
    view = the_lab.view
    c = view.time(1.0)
    rate = view.diagnostic("mean_rate")
    layer = the_lab.layer()
    calls: list[tuple[str, dict[str, Any], bool, int, int]] = [
        # (tool, args, is_error, experiments left, fits left)
        ("diagnostic", {"name": rate, "data_id": "obs"}, False, 1, 1),
        ("diagnostic", {"name": rate, "data_id": "e7"}, True, 1, 1),
        ("nonparam_kernels", {"data_id": "obs"}, False, 1, 1),
        ("run_experiment", plain_run(100.0 * c), True, 1, 1),  # no belief
        ("belief", BELIEF_ARGS, False, 1, 1),
        ("belief", {**BELIEF_ARGS, "confidence": 2.0}, True, 1, 1),
        ("run_experiment", {"intervention": {"type": "nope"}}, True, 1, 1),
        ("run_experiment", plain_run(100.0 * c), False, 0, 1),
        ("belief", BELIEF_ARGS, False, 0, 1),
        ("run_experiment", plain_run(100.0 * c), True, 0, 1),
        ("fit", {"structure": "null", "data_ids": ["obs"]}, False, 0, 0),
        ("fit", {"structure": "null", "data_ids": ["obs"]}, True, 0, 0),
        ("fit", {"structure": "Excite(", "data_ids": ["obs"]}, True, 0, 0),
        ("fit", {"structure": "null"}, True, 0, 0),
        ("no_such_tool", {}, True, 0, 0),
        ("submit", {"structure": "null", "report": "r"}, True, 0, 0),
        ("submit", {"structure": "null", "report": "r", **STOP}, False, 0, 0),
        ("diagnostic", {"name": rate, "data_id": "obs"}, True, 0, 0),
    ]
    for name, args, is_error, e_left, f_left in calls:
        out = layer.call(name, args)
        assert out.is_error is is_error, (name, out.text)
        lines = out.text.split("\n")
        assert lines[-1] == budget(e_left, 1, f_left, 1), (name, out.text)
        assert sum(line.startswith("budget left:") for line in lines) == 1


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
@pytest.mark.parametrize("condition", ["named", "anon"])
def test_the_prompt_describes_generators_truncation_and_the_budget_line(
    arm: Arm, condition: Condition, tmp_path: Path
) -> None:
    the_lab = lab(
        arm,
        condition,
        sandbox_root=tmp_path,
        image="sciagent-sandbox:unused",
    )
    text = system_prompt(the_lab.view, the_lab.arm, experiments=8, fits=40)
    assert '"times": {"start":' in text and '"step":' in text and '"n":' in text
    for pattern in ('"constant"', '"alternate"', '"repeat"'):
        assert pattern in text
    assert f"limit of {MAX_EVENTS} events" in text
    assert "truncated" in text
    assert "budget left:" in text
    assert ('"from": "/work/' in text) is (arm == "AG-o")
