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


#: numpy reductions whose summation order is chosen at runtime.
#:
#: Every one of these folds many floats into one, and the order it folds them in
#: depends on the kernel numpy or its BLAS picks from the CPU's features --
#: OpenBLAS ships ``DYNAMIC_ARCH`` and numpy's own pairwise sum is SIMD-width
#: dependent. Floating-point addition is not associative, so two machines
#: disagree in the last places. That is not a rounding curiosity here: a metric
#: value decides which bin a replicate falls in, a bin decides a count, and a
#: count decides a likelihood.
#:
#: Elementwise operations are deliberately absent. A lane-wise multiply or
#: subtract is a set of independent correctly-rounded operations, so vector
#: width cannot change the result, which is why the deterministic helpers keep
#: numpy for that part and replace only the fold.
ORDER_DEPENDENT_REDUCTIONS = frozenset(
    {
        "sum",
        "nansum",
        "mean",
        "nanmean",
        "var",
        "nanvar",
        "std",
        "nanstd",
        "dot",
        "vdot",
        "inner",
        "matmul",
        "tensordot",
        "einsum",
        "prod",
        "nanprod",
        "average",
        "cumsum",
        "cumprod",
        "trace",
    }
)

#: Bases whose reduction-shaped attributes are the deterministic ones. Without
#: this, the module built to fix the problem is the loudest offender in the
#: report -- ``reductions.total`` is an attribute call whose name is on the list.
DETERMINISTIC_BASES = frozenset({"reductions", "math"})


def _reduction_imports(tree: ast.AST) -> set[str]:
    """Return names bound directly to a numpy reduction by a ``from`` import.

    ``from numpy import mean`` puts ``mean`` in the module namespace as a bare
    identifier, so the call is an ``ast.Name`` and every check below keyed on an
    attribute walks straight past it -- the same shape of gap that
    :func:`_numpy_random_imports` exists to close for the randomness guard.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"numpy", "numpy.ma"}:
            bound.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in ORDER_DEPENDENT_REDUCTIONS
            )
    return bound


def _order_dependent_reductions(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(line, name)`` for every runtime-ordered float fold.

    Four spellings, because the invariant is about what a module reaches and not
    how it spells it. ``np.sum(x)`` is an attribute call on the numpy alias.
    ``x.sum()`` is an attribute call on an *arbitrary expression* -- and when
    that expression is a subscript, as in ``cost[rows, columns].sum()``, no check
    keyed on the base being a plain name can see it; that exact line sat in
    ``core/edits.py`` computing SPEC §8's D1 while the first version of this
    guard reported the tree clean. ``from numpy import mean`` makes the call a
    bare name that no attribute walk visits at all. And ``a @ b`` is a
    ``BinOp``, not a call, while reaching the same BLAS ``dot`` this bans by
    name.
    """
    aliases = _numpy_aliases(tree)
    imported = _reduction_imports(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.MatMult):
            found.append((node.lineno, "@"))
            continue
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id in imported:
            found.append((node.lineno, f"{target.id}()"))
            continue
        if not isinstance(target, ast.Attribute):
            continue
        if target.attr not in ORDER_DEPENDENT_REDUCTIONS:
            continue
        base = target.value
        if isinstance(base, ast.Name) and base.id in DETERMINISTIC_BASES:
            continue
        if isinstance(base, ast.Name) and base.id in aliases:
            found.append((node.lineno, f"np.{target.attr}"))
        elif not isinstance(base, ast.Name):
            found.append((node.lineno, f".{target.attr}()"))
        elif base.id not in aliases:
            found.append((node.lineno, f"{base.id}.{target.attr}()"))
    return found


#: Everywhere a fold can reach a number somebody later relies on.
#:
#: The same roots as :data:`RANDOMNESS_ROOTS` minus ``tests``, and ``scripts`` is
#: here for the reason recorded there: ``calibrate_mechanisms.py`` and
#: ``calibrate_censoring.py`` produce the frozen literals in ``mechanisms.py``
#: and ``pilot_forced_edges.py`` the frozen bin edges in ``outcomes.py``. A fold
#: whose value depends on the machine makes a calibration nobody can reproduce,
#: and unlike a registry row there is no address to notice it.
#:
#: ``tests`` is deliberately out. A fold in a test is checking something, not
#: producing an artefact, and several exist precisely to demonstrate that numpy
#: disagrees with itself.
REDUCTION_ROOTS = (SOURCE, ROOT / "scripts")


@pytest.mark.parametrize(
    "path",
    sorted(p for root in REDUCTION_ROOTS for p in root.rglob("*.py")),
    ids=lambda p: str(p.name),
)
def test_metric_values_use_deterministic_reductions(path: Path) -> None:
    """CLAUDE.md invariant 3, wherever a float fold reaches a stored number.

    A result is content-addressed with no platform term, so a fold taken in a
    CPU-chosen order makes two machines produce different numbers under one
    address. That is not hypothetical here -- ``DECISIONS.md`` records S12's
    posterior predictive p measured at 0.101100 on Windows and 0.1009 on Ubuntu
    from the same commit and seed.

    Use :mod:`sciagent.core.reductions`, which keeps numpy for the elementwise
    work and folds with :func:`math.fsum`. ``fsum`` is exactly rounded, so its
    result is *the* correctly-rounded sum and cannot depend on the order or the
    kernel.

    Scoped to the whole of ``src``, not to the environments. The first version
    of this checked only ``src/environments``, on the reasoning that a fold
    outside a diagnostic is summarising for a human -- and missed
    ``EditGrammar.distance``, which is SPEC §8's D1 and is reported and stored.
    Selections are deliberately absent from
    :data:`ORDER_DEPENDENT_REDUCTIONS`: ``np.median``, ``np.max`` and
    ``np.argmax`` pick from a multiset rather than accumulating over it, so no
    kernel can reorder them into a different answer.
    """
    offenders = _order_dependent_reductions(_parse(path))
    assert not offenders, (
        f"{path}: {[f'{name} at line {line}' for line, name in offenders]} fold "
        f"in a CPU-chosen order; use sciagent.core.reductions instead"
    )


class TestTheReductionGuardItself:
    """Controls for :func:`test_metric_values_use_deterministic_reductions`.

    The guard passes over the whole tree, so every case here would otherwise be
    a property nobody has seen it hold. The one that matters is the subscript
    base: the first version of the guard reported the tree clean while
    ``cost[rows, columns].sum()`` sat in ``core/edits.py`` computing D1.
    """

    def test_the_numpy_attribute_form_is_caught(self) -> None:
        tree = ast.parse("import numpy as np\nnp.sum(x)\n")
        assert [name for _, name in _order_dependent_reductions(tree)] == ["np.sum"]

    def test_the_method_form_is_caught(self) -> None:
        """``x.sum()`` names no module, so an alias-keyed check cannot see it."""
        tree = ast.parse("y = x.sum()\n")
        assert [name for _, name in _order_dependent_reductions(tree)] == ["x.sum()"]

    def test_a_method_call_on_a_subscript_is_caught(self) -> None:
        """The exact line the first version of the guard could not reach.

        Its base is an ``ast.Subscript``, not an ``ast.Name``, so a check that
        required a plain identifier could never match it however it was scoped.
        """
        tree = ast.parse("total = cost[rows, columns].sum()\n")
        assert [name for _, name in _order_dependent_reductions(tree)] == [".sum()"]

    def test_a_from_import_is_caught(self) -> None:
        """``from numpy import mean`` makes the call a bare name.

        No attribute walk visits it, however many base shapes that walk handles
        -- the same gap :func:`_numpy_random_imports` closes for the randomness
        guard, and it was open here until the review found it.
        """
        tree = ast.parse("from numpy import mean\nmean(x)\n")
        assert [name for _, name in _order_dependent_reductions(tree)] == ["mean()"]

    def test_the_matmul_operator_is_caught(self) -> None:
        """``a @ b`` reaches the same BLAS ``dot`` this bans by name.

        It is a ``BinOp`` rather than a ``Call``, so nothing keyed on call
        syntax sees it at all.
        """
        tree = ast.parse("value = centred @ centred\n")
        assert [name for _, name in _order_dependent_reductions(tree)] == ["@"]

    def test_the_deterministic_helpers_are_not_flagged(self) -> None:
        """Otherwise the module that fixes the problem is the loudest offender."""
        tree = ast.parse(
            "from sciagent.core import reductions\n"
            "reductions.total(x)\n"
            "reductions.mean(x)\n"
            "math.fsum(values)\n"
        )
        assert _order_dependent_reductions(tree) == []

    def test_a_selection_is_not_a_fold(self) -> None:
        """``median``/``max``/``argmax`` pick from a multiset, never accumulate.

        No kernel can reorder a selection into a different answer, so flagging
        them would be noise -- and noise in a guard is what gets a guard
        weakened later.
        """
        tree = ast.parse("import numpy as np\nnp.median(p)\nnp.max(p)\nnp.argmax(p)\n")
        assert _order_dependent_reductions(tree) == []


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
