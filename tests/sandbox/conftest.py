"""Fixtures for the Docker-backed sandbox tests.

``sandbox_image`` **fails** the test when Docker is unavailable; it never
skips. The house rule is no skipped tests, and an isolation suite that quietly
skips on a machine without Docker would report SPEC §6.3 no. 8 as passing
when nothing was checked. The fast tier does not request it, so ``-m "not
slow"`` needs no Docker; every test that does is marked ``slow``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciagent.sandbox import (
    Sandbox,
    SandboxLimits,
    SandboxUnavailableError,
    ensure_image,
)


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    try:
        return ensure_image()
    except SandboxUnavailableError as exc:
        pytest.fail(
            "Docker is required for the sandbox isolation tests (SPEC §6.3 no. 8) "
            f"and is not available: {exc}",
            pytrace=False,
        )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


@pytest.fixture
def sandbox(sandbox_image: str, run_dir: Path) -> Sandbox:
    return Sandbox(
        run_dir,
        image=sandbox_image,
        limits=SandboxLimits(wall_seconds=60, memory_mb=512),
        seed=20261001,
    )
