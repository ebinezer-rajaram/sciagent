"""Non-negotiable invariants (CLAUDE.md), checked statically.

These are not acceptance criteria for a subsystem; they are properties of the
repository that must hold at every commit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"
SCIAGENT = SOURCE / "sciagent"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    "path", sorted(SCIAGENT.rglob("*.py")), ids=lambda p: str(p.name)
)
def test_sciagent_never_imports_environments(path: Path) -> None:
    """SPEC §10, CLAUDE.md invariant 1. Do not weaken this test.

    The framework is domain-independent: family semantics reach it through an
    injected ``FamilyLibrary``, never through an import.
    """
    offenders = {
        module
        for module in _imported_modules(path)
        if module == "environments" or module.startswith("environments.")
    }
    assert not offenders, f"{path} imports {sorted(offenders)}"


def test_sciagent_core_performs_no_io() -> None:
    """CLAUDE.md: no I/O in ``core/``. Pure functions only."""
    forbidden = {"open", "pathlib", "os", "io", "sqlite3", "requests", "httpx"}
    for path in sorted((SCIAGENT / "core").rglob("*.py")):
        modules = _imported_modules(path)
        assert not (modules & forbidden), (
            f"{path} imports {sorted(modules & forbidden)}"
        )


def test_no_unseeded_randomness_in_source() -> None:
    """CLAUDE.md invariant 3: never ``random.`` or bare ``np.random.<dist>``.

    ``np.random.Generator``, ``np.random.PCG64`` and ``np.random.SeedSequence``
    are constructors for explicitly seeded generators, not global state, so they
    are the only permitted references.
    """
    allowed = {"Generator", "PCG64", "SeedSequence", "default_rng"}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "random"
                and isinstance(value.value, ast.Name)
                and value.value.id == "np"
            ):
                assert node.attr in allowed, (
                    f"{path}: np.random.{node.attr} is global mutable randomness"
                )
        assert "import random" not in path.read_text(encoding="utf-8"), path
