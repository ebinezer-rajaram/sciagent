"""An AG-o record pins its sandbox: every limit and the handoff helper's code.

Replay re-executes every ``python`` call (SPEC §6.3 no. 9). The image tag is
recorded beside each result, but the entry and path limits of ``work/`` and
the helper that enforces them live in ``src/``, outside the image; a change
to either would change what a replayed call leaves in ``work/`` without
changing anything the record shows. So the run's record carries them, and
replay refuses a record made under others.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sciagent.investigation import InvestigationSpec
from sciagent.investigation.tools import LabError
from sciagent.sandbox import SandboxLimits, _handoff_helper


def _spec(arm: str) -> InvestigationSpec:
    return InvestigationSpec(
        environment="pointproc",
        truth_id="t",
        arm="AG-o" if arm == "AG-o" else "AG-c",
        condition="named",
        seed=7,
    )


def _recorded(spec: InvestigationSpec) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(json.dumps(spec.as_json()))
    return data


def test_an_ag_o_record_carries_every_limit_and_the_helper_digest() -> None:
    spec = _spec("AG-o")
    sandbox = _recorded(spec)["sandbox"]
    assert sandbox["limits"] == dataclasses.asdict(
        SandboxLimits(wall_seconds=60.0, memory_mb=2048)
    )
    assert sandbox["limits"]["max_work_entries"] == 100_000
    assert sandbox["limits"]["max_work_path_bytes"] == 1024
    source = Path(_handoff_helper.__file__).read_text(encoding="utf-8")
    assert (
        sandbox["handoff_helper_sha256"]
        == hashlib.sha256(source.encode("utf-8")).hexdigest()
    )
    assert InvestigationSpec.from_json(_recorded(spec)) == spec


@pytest.mark.parametrize(
    "tamper",
    [
        lambda s: s["limits"].update(max_work_entries=200_000),
        lambda s: s["limits"].update(max_work_path_bytes=512),
        lambda s: s["limits"].update(output_bytes=1),
        lambda s: s.update(handoff_helper_sha256="0" * 64),
    ],
)
def test_replay_refuses_a_record_made_under_another_sandbox(tamper: Any) -> None:
    data = _recorded(_spec("AG-o"))
    tamper(data["sandbox"])
    with pytest.raises(LabError, match="sandbox"):
        InvestigationSpec.from_json(data)
    del data["sandbox"]
    with pytest.raises(LabError, match="sandbox"):
        InvestigationSpec.from_json(data)


def test_an_ag_c_record_has_no_sandbox_entry() -> None:
    """AG-c has no sandbox; its records keep their bytes (and addresses)."""
    data = _recorded(_spec("AG-c"))
    assert "sandbox" not in data
    assert InvestigationSpec.from_json(data) == _spec("AG-c")
