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


def _numpy_aliases(tree: ast.AST) -> set[str]:
    """Return every local name an ``import numpy`` statement binds the package to.

    ``import numpy`` binds ``numpy``; ``import numpy as np`` binds ``np``; and
    ``import numpy as onp`` binds ``onp``. All three reach the same package, so
    all three have to be resolved before an attribute walk can say anything
    about what a module touches.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "numpy"
            )
    return aliases


def _numpy_random_uses(tree: ast.AST) -> list[ast.Attribute]:
    """Return every ``<numpy>.random.<attr>`` reference in a parsed module.

    The base is resolved through :func:`_numpy_aliases` rather than matched
    against the identifier ``np``. Pinning it to ``np`` made this a check on the
    house style: ``import numpy as onp`` followed by ``onp.random.normal(...)``
    reaches the global generator and was invisible, and so was the plain
    ``import numpy`` / ``numpy.random.normal(...)`` -- neither is caught by
    :func:`_numpy_random_imports` either, which looks for ``numpy.random`` and
    ``from numpy import random`` and never sees a bare ``import numpy``.

    Resolving the alias is also what keeps this from degenerating into a
    substring check: an unrelated ``mypkg.random.normal`` is not numpy's and is
    not returned.
    """
    aliases = _numpy_aliases(tree)
    found: list[ast.Attribute] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "random"
            and isinstance(value.value, ast.Name)
            and value.value.id in aliases
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


class TestTheRandomnessGuardItself:
    """Controls for :func:`test_no_unseeded_randomness`, over source strings.

    The guard scans every file under :data:`RANDOMNESS_ROOTS`, so a fixture
    written to disk to prove it fires would be a file the guard then fails on --
    the suite would go red to demonstrate that it can. The offending code
    therefore lives in string literals here: ``ast.parse`` of *this* module sees
    a ``Constant`` where the guard's own scan looks for an ``Attribute``, so
    these cases are invisible to the check they exercise.

    A guard nobody has watched fail is a guard nobody knows the reach of. The
    repository has been clean at every commit, which means every one of these
    passes for free and none of them was ever earned by a real defect.
    """

    def test_the_dotted_form_is_caught(self) -> None:
        """The form the guard was written for."""
        tree = ast.parse("import numpy as np\nnp.random.normal(3)\n")
        assert [node.attr for node in _numpy_random_uses(tree)] == ["normal"]

    def test_an_aliased_numpy_import_is_caught(self) -> None:
        """``import numpy as onp`` reaches the same global generator.

        The guard resolved a use by the base identifier being literally ``np``,
        so any other alias was invisible: not to the attribute walk, which did
        not match, and not to :func:`_numpy_random_imports`, which looks for
        ``numpy.random`` and ``from numpy import random`` and never sees a plain
        ``import numpy``. The invariant is about what a module can reach, so the
        alias a module happens to choose cannot be what decides it.
        """
        tree = ast.parse("import numpy as onp\nonp.random.normal(3)\n")
        assert [node.attr for node in _numpy_random_uses(tree)] == ["normal"]

    def test_the_unaliased_package_is_caught(self) -> None:
        """``import numpy`` then ``numpy.random.normal`` is the same reach."""
        tree = ast.parse("import numpy\nnumpy.random.normal(3)\n")
        assert [node.attr for node in _numpy_random_uses(tree)] == ["normal"]

    def test_a_seeded_constructor_under_an_alias_is_permitted(self) -> None:
        """Widening the guard must not refuse the form the codebase uses.

        Every offender the walk returns is checked against
        :data:`SEEDED_CONSTRUCTORS` rather than rejected on sight, so the
        constructors stay legal under any alias.
        """
        tree = ast.parse("import numpy as onp\nonp.random.default_rng(7)\n")
        found = [node.attr for node in _numpy_random_uses(tree)]
        assert found == ["default_rng"]
        assert all(attr in SEEDED_CONSTRUCTORS for attr in found)

    def test_an_unrelated_module_named_random_is_not_caught(self) -> None:
        """The alias must be bound to numpy, not merely spelled like it.

        A local ``mypkg.random`` is not numpy's, and a guard that fired on the
        attribute name alone would be a substring check with extra steps -- the
        exact failure the stdlib-``random`` clause below was rewritten to avoid.
        """
        tree = ast.parse("import mypkg\nmypkg.random.normal(3)\n")
        assert _numpy_random_uses(tree) == []

    def test_a_bare_numpy_import_is_not_itself_an_offence(self) -> None:
        """Importing numpy is not reaching its global generator."""
        tree = ast.parse("import numpy as onp\nonp.array([1, 2])\n")
        assert _numpy_random_uses(tree) == []
        assert _numpy_random_imports(tree) == []


def _text_writes_without_newline(tree: ast.AST) -> list[int]:
    r"""Return the lines calling ``write_text`` without pinning ``newline``.

    ``Path.write_text`` opens in text mode with ``newline=None``, which
    translates every ``\n`` to ``os.linesep`` on write. On Windows that is
    ``\r\n``, so the same artefact written here and on the Ubuntu half of this
    project differs byte for byte while parsing identically -- which is the
    shape of failure the third invariant exists to forbid, and one no round-trip
    test can see, because reading translates it back.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_text"
        and not any(keyword.arg == "newline" for keyword in node.keywords)
    ]


@pytest.mark.parametrize(
    "path", sorted(SOURCE.rglob("*.py")), ids=lambda p: str(p.name)
)
def test_artefacts_are_written_with_pinned_newlines(path: Path) -> None:
    """CLAUDE.md invariant 3, at the point where output leaves the process.

    Every artefact this project persists -- the empirical table, the transcript
    corpus -- is a file another machine is meant to reproduce or to diff. A
    writer that lets the platform pick its line ending makes those files differ
    between the Windows and Ubuntu halves of this project for a reason that has
    nothing to do with the numbers inside them.

    Checked over ``src`` only. A test writing a scratch file is not producing an
    artefact anybody compares.
    """
    offenders = _text_writes_without_newline(_parse(path))
    assert not offenders, (
        f"{path}: write_text at line(s) {offenders} does not pin newline=; on "
        f"Windows it emits CRLF and the artefact stops being byte-comparable"
    )
