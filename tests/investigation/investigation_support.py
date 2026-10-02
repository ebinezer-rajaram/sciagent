"""Shared helpers for ``tests/investigation``: the pointproc environment as an
:class:`EnvironmentSpec`, and a scripted-agent runner.

Test-side, so it may import ``environments``; the framework never does.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from environments.pointproc import v2
from sciagent.harness.live import MODELS
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.investigation import (
    EnvironmentSpec,
    InvestigationOutcome,
    InvestigationSpec,
    Lab,
    TruthSpec,
    build_lab,
    run,
)
from sciagent.investigation.prompts import Arm
from sciagent.investigation.view import Condition

#: The anonymised channel names SPEC §5 fixes for pointproc.
ANON_CHANNELS: Final = {"size": "m1", "sign": "m2"}
#: pointproc's own leak terms beyond the default list.
POINTPROC_FORBIDDEN: Final = ("size", "sign", "arrival")
MODEL: Final = MODELS["haiku"]
SEED: Final = 20261002


def resolve(truth_id: str) -> TruthSpec:
    truth = v2.TRUTHS[truth_id]
    return TruthSpec(
        truth.structure, truth.psi, truth.coef, v2.CHANNELS, v2.mark_sampler
    )


POINTPROC: Final = EnvironmentSpec(
    name="pointproc-v2",
    anon_channels=ANON_CHANNELS,
    forbidden=POINTPROC_FORBIDDEN,
    resolve=resolve,
)


def lab(
    arm: Arm = "AG-c",
    condition: Condition = "named",
    *,
    truth: str = "size_excitation",
    experiments: int = 3,
    fits: int = 4,
    seed: int = SEED,
    sandbox_root: Path | None = None,
    image: str | None = None,
) -> Lab:
    spec = InvestigationSpec(
        POINTPROC.name, truth, arm, condition, seed, experiments, fits
    )
    return build_lab(spec, POINTPROC, sandbox_root=sandbox_root, image=image)


def scripted_run(
    script: Sequence[Step],
    out_dir: Path,
    *,
    arm: Arm = "AG-c",
    condition: Condition = "named",
    truth: str = "size_excitation",
    experiments: int = 3,
    fits: int = 4,
    sandbox_root: Path | None = None,
) -> InvestigationOutcome:
    return run(
        POINTPROC,
        truth,
        arm,
        condition,
        MODEL,
        SEED,
        out_dir,
        experiments=experiments,
        fits=fits,
        max_turns=40,
        wall_time_s=600.0,
        driver=ScriptedDriver(script),
        sandbox_root=sandbox_root,
    )


def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove variables the live runner's contamination guard refuses."""
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def plain_run(horizon: float) -> dict[str, Any]:
    """An unintervened experiment's arguments."""
    return {"intervention": {"type": "compose", "parts": []}, "horizon": horizon}
