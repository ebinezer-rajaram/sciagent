"""Registers the Hypothesis profile every property test runs under.

It is registered here, in the module body, because ``@given`` captures
``settings.default`` at decoration time, during collection; a profile loaded
later (an autouse fixture, say) would silently govern nothing.

Sampling stays random, and failures are kept in a committed corpus under
``tests/regressions/``. Hypothesis forbids ``derandomize=True`` together with a
database, and random sampling keeps widening coverage across runs, while the
committed corpus still carries any counterexample between machines. A passing
run writes nothing there. ``deadline`` is off because these properties simulate.

It also pins BLAS to one thread, as the campaign driver does, before anything
imports numpy (OpenBLAS reads the variables when it loads): at default threads
each test process commits about 2.2 GB, and the campaign's reference numbers
are computed single-threaded (LOG 2026-10-06). Nothing numpy-backed may be
imported above that point; the sandbox fixtures import their module lazily.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

if TYPE_CHECKING:
    from sciagent.sandbox import Sandbox

#: The variables ``sciagent.campaign.driver.THREAD_VARIABLES`` sets for workers.
BLAS_THREAD_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
for _var in BLAS_THREAD_VARIABLES:
    os.environ[_var] = "1"
if "numpy" in sys.modules:
    raise pytest.UsageError(
        "numpy was imported before tests/conftest.py pinned BLAS to one thread"
    )

#: The committed corpus. Tracked, unlike Hypothesis's default ``.hypothesis/``,
#: which writes a ``.gitignore`` holding ``*`` into itself -- so a counterexample
#: found there can never be committed and dies with the working tree.
REGRESSIONS = Path(__file__).resolve().parent / "regressions"

settings.register_profile(
    "sciagent",
    derandomize=False,
    deadline=None,
    print_blob=True,
    database=DirectoryBasedExampleDatabase(REGRESSIONS),
)
settings.load_profile("sciagent")


# --------------------------------------------------------------------------
# Sandbox fixtures (tests/sandbox). They live here because a second
# ``conftest.py`` collides with this one as a top-level module under mypy.
# ``sandbox_image`` **fails** the test when Docker is unavailable; it never
# skips: an isolation suite that quietly skips would report SPEC §6.3 no. 8 as
# passing when nothing was checked. Only ``slow`` tests request it.
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    from sciagent.sandbox import SandboxUnavailableError, ensure_image

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
    from sciagent.sandbox import Sandbox, SandboxLimits

    return Sandbox(
        run_dir,
        image=sandbox_image,
        limits=SandboxLimits(wall_seconds=60, memory_mb=512),
        seed=20261001,
    )
