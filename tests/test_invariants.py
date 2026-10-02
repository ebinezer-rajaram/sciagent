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
    """v1 SPEC §10, CLAUDE.md invariant 1. Do not weaken this test.

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
    ``core/edits.py`` computing v1 SPEC §8's D1 while the first version of this
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

#: Modules allowed to fold in a CPU-chosen order, each with its reason. Kept
#: to a minimum and reviewed by a human (docs/v2/LOG.md, 2026-10-02).
#: B-sparse's lasso path computes ~1M group gradients per KKT check from one
#: BLAS cross product; a fixed-order fold measured ~8 h per path against
#: ~1 min. The path only *steers a search*: every reported or stored number
#: (log-likelihoods, θ, scores) comes from the certified, fixed-order
#: ``glm.fit`` of the selected structure. Run-to-run byte identity on the
#: reference platform with BLAS pinned to one thread is tested in
#: ``tests/systems_v2/test_sparse.py``; across CPUs the *selected structure*
#: can flip at a near-tie, which is why this list exists rather than silence.
REDUCTION_EXEMPT: dict[str, str] = {
    "src/sciagent/systems/v2/sparse.py": "B-sparse path (search heuristic)",
    "src/sciagent/systems/v2/sparse_dictionary.py": "B-sparse path (search heuristic)",
    "src/sciagent/systems/v2/sparse_solver.py": "B-sparse path (search heuristic)",
}


def test_reduction_exemptions_name_existing_files() -> None:
    """An exemption for a deleted or renamed module must not linger."""
    missing = [rel for rel in REDUCTION_EXEMPT if not (ROOT / rel).is_file()]
    assert not missing, f"stale REDUCTION_EXEMPT entries: {missing}"


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
    ``EditGrammar.distance``, which is v1 SPEC §8's D1 and is reported and stored.
    Selections are deliberately absent from
    :data:`ORDER_DEPENDENT_REDUCTIONS`: ``np.median``, ``np.max`` and
    ``np.argmax`` pick from a multiset rather than accumulating over it, so no
    kernel can reorder them into a different answer.
    """
    if path.relative_to(ROOT).as_posix() in REDUCTION_EXEMPT:
        return
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


#: Builders whose result iterates in hash order rather than insertion order.
#:
#: Deliberately **not** ``.keys()``, ``.values()`` or ``.items()``. A dict has
#: preserved insertion order since 3.7, so flagging those would be noise on the
#: commonest loop in the codebase, and noise is how a guard gets suppressed
#: rather than obeyed. The hazard this clause names is the set: its iteration
#: order is a function of the hashes of its members, and for ``str`` that is a
#: function of ``PYTHONHASHSEED``, which differs per process.
SET_BUILDERS = frozenset({"set", "frozenset"})

#: Where a set's order becomes observable. ``sorted()`` is absent on purpose --
#: it is the fix, and a sorted set is exactly what this guard wants to see.
ORDER_OBSERVING_CALLS = frozenset({"list", "tuple"})


def _is_set_valued(node: ast.expr) -> bool:
    """Return whether ``node`` is a set this guard can recognise as one.

    Syntactic, and therefore incomplete by construction: a set arriving through
    a parameter or an attribute is invisible here. That is the same bargain the
    other three guards in this file strike -- they catch the spelling, not the
    value -- and it is why this one is a floor rather than a proof.
    """
    if isinstance(node, ast.Set | ast.SetComp):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in SET_BUILDERS
    )


def _unsorted_set_iterations(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(line, shape)`` for every place a set's order can be observed.

    Four shapes, for the same reason the reduction guard has four: the invariant
    is about what a module does, not how it spells it. A ``for`` over a set, a
    comprehension over one, ``list()``/``tuple()`` of one, and ``join()`` of one
    all turn an unordered collection into an ordered artefact. Wrapping any of
    them in ``sorted()`` makes the order a property of the values instead of the
    process, which is the whole of the fix.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and _is_set_valued(node.iter):
            found.append((node.lineno, "for over a set"))
        if isinstance(
            node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
        ):
            found.extend(
                (node.lineno, "comprehension over a set")
                for generator in node.generators
                if _is_set_valued(generator.iter)
            )
        if not isinstance(node, ast.Call) or not node.args:
            continue
        target = node.func
        if not _is_set_valued(node.args[0]):
            continue
        if isinstance(target, ast.Name) and target.id in ORDER_OBSERVING_CALLS:
            found.append((node.lineno, f"{target.id}() of a set"))
        elif isinstance(target, ast.Attribute) and target.attr == "join":
            found.append((node.lineno, "join() of a set"))
    return found


@pytest.mark.parametrize(
    "path",
    sorted(p for root in REDUCTION_ROOTS for p in root.rglob("*.py")),
    ids=lambda p: str(p.name),
)
def test_no_set_iteration_order_reaches_an_artefact(path: Path) -> None:
    """CLAUDE.md invariant 3's fourth clause, which had no static guard.

    The invariant names four things and this file guarded three of them: the
    random source, the fold order, and the line ending. *"No dict/set iteration
    order dependence in anything affecting output"* was enforced by convention
    only, and the convention did hold -- this guard was written against a tree it
    reported clean, over both roots -- but a clause with no guard is a clause
    whose next violation is found by a reader rather than by the suite.

    Added when ``pytest-xdist`` made the exposure worse rather than merely
    theoretical. A serial run holds one ``PYTHONHASHSEED`` for its whole
    duration, so a set-ordering dependence is at least *consistent* within a run
    and shows up as a cross-run diff. Four workers hold four seeds at once, so
    the same dependence can make two tests in one run disagree.

    Scoped like the reduction guard, and out of the same reasoning: ``src`` plus
    ``scripts``, because a calibration script's frozen literals are an artefact
    nobody can reproduce if a set chose their order, and not ``tests``, where a
    set is usually being asserted about rather than written out.
    """
    offenders = _unsorted_set_iterations(_parse(path))
    assert not offenders, (
        f"{path}: {[f'{shape} at line {line}' for line, shape in offenders]} "
        f"observes a set's order, which follows PYTHONHASHSEED; wrap it in "
        f"sorted() so the order comes from the values"
    )


class TestTheSetOrderGuardItself:
    """Controls for :func:`test_no_set_iteration_order_reaches_an_artefact`.

    The guard passes over both roots, so without these every case below is a
    property nobody has watched hold. The two that carry their weight are the
    ``sorted()`` exemption -- a guard that flagged the fix would be worse than no
    guard -- and the dict exemption, because flagging ``.items()`` would bury the
    real finding under every loop in the codebase.
    """

    def test_a_for_over_a_set_call_is_caught(self) -> None:
        tree = ast.parse("for name in set(names):\n    emit(name)\n")
        assert _unsorted_set_iterations(tree) == [(1, "for over a set")]

    def test_a_set_literal_is_caught(self) -> None:
        tree = ast.parse("for x in {'a', 'b'}:\n    emit(x)\n")
        assert _unsorted_set_iterations(tree) == [(1, "for over a set")]

    def test_a_comprehension_over_a_set_comprehension_is_caught(self) -> None:
        tree = ast.parse("rows = [f(x) for x in {g(y) for y in ys}]\n")
        assert _unsorted_set_iterations(tree) == [(1, "comprehension over a set")]

    def test_listing_a_set_is_caught(self) -> None:
        tree = ast.parse("order = list(set(names))\n")
        assert _unsorted_set_iterations(tree) == [(1, "list() of a set")]

    def test_joining_a_set_is_caught(self) -> None:
        tree = ast.parse("key = ','.join(frozenset(parts))\n")
        assert _unsorted_set_iterations(tree) == [(1, "join() of a set")]

    def test_sorting_the_set_first_is_permitted(self) -> None:
        """The fix must not be flagged, or the guard teaches the wrong lesson."""
        tree = ast.parse(
            "for name in sorted(set(names)):\n    emit(name)\n"
            "order = list(sorted({'a', 'b'}))\n"
        )
        assert _unsorted_set_iterations(tree) == []

    def test_a_dict_is_not_a_set(self) -> None:
        """Insertion order has been guaranteed since 3.7; these are not offences."""
        tree = ast.parse(
            "for k, v in mapping.items():\n    emit(k, v)\n"
            "order = list(mapping.keys())\n"
            "rows = [f(v) for v in mapping.values()]\n"
        )
        assert _unsorted_set_iterations(tree) == []

    def test_membership_against_a_set_is_not_an_iteration(self) -> None:
        """Sets are the right tool for membership; only observing order is not."""
        tree = ast.parse(
            "if name in set(names):\n    emit(name)\nshared = {'a', 'b'} & other\n"
        )
        assert _unsorted_set_iterations(tree) == []


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
