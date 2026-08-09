"""Non-negotiable invariants (CLAUDE.md), checked statically.

These are not acceptance criteria for a subsystem; they are properties of the
repository that must hold at every commit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
SCIAGENT = SOURCE / "sciagent"

#: Every directory the determinism invariant is checked over. ``scripts`` is
#: here because it is not incidental: ``calibrate_mechanisms.py`` and
#: ``calibrate_censoring.py`` produce the frozen literals in
#: ``environments/pointproc/mechanisms.py``, so unseeded randomness there would
#: make a calibration nobody could reproduce, silently. ``tests`` is here
#: because a test that draws from global state fails intermittently and blames
#: the code.
RANDOMNESS_ROOTS = (SOURCE, ROOT / "scripts", ROOT / "tests")


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.AST) -> set[str]:
    """Return every absolute module name a parsed module imports."""
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
        for module in _imported_modules(_parse(path))
        if module == "environments" or module.startswith("environments.")
    }
    assert not offenders, f"{path} imports {sorted(offenders)}"


def test_sciagent_core_performs_no_io() -> None:
    """CLAUDE.md: no I/O in ``core/``. Pure functions only."""
    forbidden = {"open", "pathlib", "os", "io", "sqlite3", "requests", "httpx"}
    for path in sorted((SCIAGENT / "core").rglob("*.py")):
        modules = _imported_modules(_parse(path))
        assert not (modules & forbidden), (
            f"{path} imports {sorted(modules & forbidden)}"
        )


#: The only names of ``numpy.random`` this repository may reach. Each constructs
#: a generator from an explicit seed; everything else on that module draws from
#: the global state the determinism invariant forbids.
SEEDED_CONSTRUCTORS = frozenset({"Generator", "PCG64", "SeedSequence", "default_rng"})


def _numpy_random_uses(tree: ast.AST) -> list[ast.Attribute]:
    """Return every ``np.random.<attr>`` reference in a parsed module."""
    found: list[ast.Attribute] = []
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
            found.append(node)
    return found


def _numpy_random_imports(tree: ast.AST) -> list[str]:
    """Return names imported from ``numpy.random`` that are not seeded constructors.

    The attribute check above sees ``np.random.normal`` and nothing else, so it
    is a check on one import style. ``from numpy.random import normal`` reaches
    the same global generator by a name the attribute walk never visits, and
    ``import numpy.random as r`` rebinds the module itself. Both are caught here,
    so the invariant is about what is reachable rather than about how it is
    spelled.
    """
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "numpy.random":
            offenders.extend(
                alias.name
                for alias in node.names
                if alias.name not in SEEDED_CONSTRUCTORS
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "numpy":
            offenders.extend(
                f"numpy.{alias.name}" for alias in node.names if alias.name == "random"
            )
        elif isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if alias.name == "numpy.random"
            )
    return offenders


def _called_name(node: ast.Call) -> str | None:
    """Return the final identifier of a call's target, however it was reached.

    ``np.random.default_rng(...)`` and a bare ``default_rng(...)`` under
    ``from numpy.random import default_rng`` are the same call and must be
    checked alike; matching only the dotted form would make the check a test of
    import style rather than of behaviour.
    """
    match node.func:
        case ast.Attribute(attr=name):
            return name
        case ast.Name(id=name):
            return name
    return None


def _unseeded_default_rng(tree: ast.AST) -> list[int]:
    """Return the lines calling ``default_rng`` with no seed.

    ``default_rng()`` with no argument draws entropy from the operating system,
    so it is global randomness wearing the name of a seeded constructor -- the
    one member of the allowlist below that can be either. The allowlist cannot
    decide it; only the call site can.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _called_name(node) == "default_rng"
        and not node.args
        and not node.keywords
    ]


@pytest.mark.parametrize(
    "path",
    sorted(p for root in RANDOMNESS_ROOTS for p in root.rglob("*.py")),
    ids=lambda p: str(p.name),
)
def test_no_unseeded_randomness(path: Path) -> None:
    """CLAUDE.md invariant 3: never ``random.`` or bare ``np.random.<dist>``.

    :data:`SEEDED_CONSTRUCTORS` are the only permitted references: each builds a
    generator from an explicit seed rather than reaching global state. Three
    things are checked, because the invariant is about what a module can reach
    and not about how it spells it -- the dotted ``np.random.<attr>`` form, the
    import forms that bypass it (:func:`_numpy_random_imports`), and whether a
    ``default_rng`` call actually supplies a seed
    (:func:`_unseeded_default_rng`), which is the one allowed name that can be
    either.

    Checked over ``scripts`` and ``tests`` as well as ``src``: see
    :data:`RANDOMNESS_ROOTS`.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in _numpy_random_uses(tree):
        assert node.attr in SEEDED_CONSTRUCTORS, (
            f"{path}:{node.lineno}: np.random.{node.attr} is global mutable randomness"
        )
    imported = _numpy_random_imports(tree)
    assert not imported, (
        f"{path} imports {sorted(imported)} from numpy.random; reach global "
        f"randomness by no spelling, not merely by no dotted one"
    )
    unseeded = _unseeded_default_rng(tree)
    assert not unseeded, (
        f"{path}: default_rng() with no seed at line(s) {unseeded}; it draws from "
        f"the operating system, so the run is not reproducible"
    )
    # By import rather than by substring. A substring search matches the module
    # name inside a comment or a string -- including the one that would have
    # appeared in this assertion -- and misses ``from random import randint``,
    # which is the form that actually reaches the global generator.
    stdlib_random = {
        module
        for module in _imported_modules(tree)
        if module == "random" or module.startswith("random.")
    }
    assert not stdlib_random, f"{path} imports the stdlib random module"
