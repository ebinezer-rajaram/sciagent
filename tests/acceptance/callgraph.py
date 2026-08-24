"""Static call-graph reachability, for acceptance test A14.

A14 requires that no call path exists from the agent tool surface to a sealed
data partition. This module answers that by building an intra-package call graph
from the AST and searching it, rather than by executing anything: a runtime probe
could only show that the paths taken *on that run* were clean.

Test-side by design, following ``tests/test_invariants.py``, which likewise does
its static analysis outside the shipped package. Not named ``test_*``, so pytest
does not collect it.

Soundness over precision
------------------------

Calls are resolved by *simple name*: a call to ``store.sealed_records(...)``
creates an edge to every function named ``sealed_records`` anywhere in the tree.
That over-approximates the true call graph, so the analyser can report a path
that no execution would take, but it cannot miss one that exists. For a safety
gate that is the correct direction to be wrong in -- a false alarm costs an
argument about a declaration, a missed path costs the evaluation's integrity.

The restricted symbols are not listed here. They are read from
``sciagent.registry.partitions.SEALED_SYMBOLS``, so the shipped code declares its
own sealed surface and the two cannot drift apart.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class SealedReference:
    """One syntactic reference to a restricted symbol."""

    module: str
    function: str
    symbol: str
    lineno: int

    def __str__(self) -> str:
        return (
            f"{self.module}.{self.function} references "
            f"{self.symbol} at line {self.lineno}"
        )


@dataclass(frozen=True, slots=True)
class SealedPath:
    """A call chain from a declared entry point to a sealed reference."""

    entry: str
    chain: tuple[str, ...]
    reference: SealedReference

    def __str__(self) -> str:
        return f"{' -> '.join(self.chain)}  [{self.reference}]"


@dataclass(frozen=True, slots=True)
class Analysis:
    """The result of one analyser run."""

    paths: tuple[SealedPath, ...]
    entry_points: tuple[str, ...]
    matched_patterns: tuple[str, ...]
    unmatched_patterns: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.paths


@dataclass(slots=True)
class _Function:
    """One function definition and the simple names it calls."""

    module: str
    qualname: str
    calls: set[str] = field(default_factory=set)
    references: list[SealedReference] = field(default_factory=list)


#: Qualname given to a module's own body. Not a legal Python identifier, so no
#: call can resolve to it: ``_called_name`` returns identifiers, and module-level
#: code is reached by importing, never by calling. It is an entry point and never
#: a callee.
MODULE_SCOPE: Final = "<module>"


class _Visitor(ast.NodeVisitor):
    """Collect one ``_Function`` per definition, nested definitions included.

    Plus one for the module body itself, under :data:`MODULE_SCOPE`. Module-level
    statements run at *import* time and reach a sealed partition exactly as a
    function body does, and collecting only ``FunctionDef`` nodes missed them
    entirely -- a module that planted ``TOKEN = SealedAccess(...)`` and defined
    no function at all analysed clean (A32).

    It missed them twice over, which is the part worth stating. A module
    contributing no functions contributed nothing to the analyser's set of
    *modules* either, so its surface pattern was reported as unmatched -- and
    ``unmatched`` is the field that exists to make a surface declaration which
    has stopped describing the code visible rather than quietly clean. The blind
    spot disabled the instrument that was supposed to reveal it.
    """

    def __init__(self, module: str, sealed: frozenset[str]) -> None:
        self.module = module
        self.sealed = sealed
        self.functions: list[_Function] = []
        self._scope: list[str] = []

    def visit_Module(self, node: ast.Module) -> None:
        """Collect the module body, then recurse into the definitions in it.

        The body is everything *not* inside a function: top-level statements, and
        class bodies, which also execute on import and are not functions. The
        walk stops at each ``FunctionDef``, whose contents ``_enter`` owns --
        without that, every function's references would be attributed to module
        scope as well and the analyser would report the same violation twice
        under two names.
        """
        self._collect(node, MODULE_SCOPE, _import_time_nodes(node))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node)

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        qualname = ".".join(self._scope)
        self._collect(node, qualname, ast.walk(node))
        self.generic_visit(node)
        self._scope.pop()

    def _collect(
        self, node: ast.AST, qualname: str, children: Iterable[ast.AST]
    ) -> None:
        """Record one ``_Function`` for ``qualname`` over the nodes ``children``.

        Always appends, even when nothing was found: an empty ``_Function`` is
        what puts its module into the analyser's module set, which is what makes
        the surface pattern that matched it report as matched.
        """
        function = _Function(module=self.module, qualname=qualname)
        for child in children:
            if isinstance(child, ast.Call):
                name = _called_name(child.func)
                if name is not None:
                    function.calls.add(name)
            symbol = _sealed_symbol(child, self.sealed)
            if symbol is not None:
                function.references.append(
                    SealedReference(
                        module=self.module,
                        function=qualname,
                        symbol=symbol,
                        lineno=getattr(child, "lineno", getattr(node, "lineno", 0)),
                    )
                )
        self.functions.append(function)


def _import_time_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield every node under ``node`` that is not inside a function definition.

    Class bodies are descended into: a class body executes when the module is
    imported, exactly as the statements around it do, and it is not a function.

    A function definition is descended into only as far as its decorators and its
    argument defaults, which are evaluated at import while the body is not. The
    body belongs to ``_enter``, under the function's own qualname; descending
    into it here would report every violation twice, once under the function and
    once under module scope.
    """
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            stack.extend(current.decorator_list)
            stack.extend(
                default
                for default in (*current.args.defaults, *current.args.kw_defaults)
                if default is not None
            )
        else:
            stack.extend(ast.iter_child_nodes(current))


def _called_name(node: ast.expr) -> str | None:
    """Return the simple name a call expression invokes, if it has one."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _sealed_symbol(node: ast.AST, sealed: frozenset[str]) -> str | None:
    """Return the restricted symbol a node names, if any.

    Four syntactic forms count, because a restricted name can be reached through
    any of them: an attribute (``node.plausibility``), a bare name
    (``sealed_records``), a string constant (``object.__setattr__(n, "x", v)``),
    and a keyword or parameter name. The last matters most for a write to a
    frozen dataclass, which cannot be an assignment and so appears as
    ``replace(node, plausibility=...)`` -- a keyword, not a reference.
    """
    if isinstance(node, ast.Attribute) and node.attr in sealed:
        return node.attr
    if isinstance(node, ast.Name) and node.id in sealed:
        return node.id
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in sealed
    ):
        return node.value
    if isinstance(node, ast.keyword) and node.arg in sealed:
        return node.arg
    if isinstance(node, ast.arg) and node.arg in sealed:
        return node.arg
    return None


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return ".".join(parts)


def _python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def analyse(
    root: Path,
    *,
    surface: Sequence[str],
    sealed_symbols: Sequence[str],
    licensed: Sequence[str] = (),
) -> Analysis:
    """Return every call path from ``surface`` to a sealed symbol under ``root``.

    ``surface`` holds :mod:`fnmatch` patterns over dotted module names; every
    function defined in a matching module is an entry point. ``sealed_symbols``
    holds the restricted names, normally
    ``sciagent.registry.partitions.SEALED_SYMBOLS``.

    ``licensed`` holds fully qualified function names whose *own* references to a
    sealed symbol are not reported -- the framework's sanctioned writer, normally
    ``sciagent.hypothesis.graph.PLAUSIBILITY_DERIVATION``. It is deliberately a
    list of exact functions rather than a module or a pattern, so widening it is
    a visible edit and not a wildcard that quietly grows.

    A licensed function is still **traversed**: only the references it makes
    itself are exempt, and everything it calls is checked as usual. So the
    exemption cannot be used to hide a write one hop further down, which is the
    difference between naming a boundary and switching the gate off.

    Guarantees the search is exhaustive over the definitions found under ``root``
    and terminates on recursive call cycles. Reports which surface patterns
    matched nothing, so a surface declaration that has silently stopped
    describing the code is visible rather than quietly clean.
    """
    permitted = frozenset(licensed)
    sealed = frozenset(sealed_symbols)
    functions: list[_Function] = []
    for path in _python_files(root):
        module = _module_name(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _Visitor(module, sealed)
        visitor.visit(tree)
        functions.extend(visitor.functions)

    # Resolution is by simple name, preferring definitions in the calling module.
    # Python resolves a bare ``helper()`` through module globals, so a module that
    # defines ``helper`` itself never reaches another module's; without the
    # preference, two modules that happen to share a private helper name appear to
    # call each other. Attribute calls (``obj.method()``) are never local
    # definitions, so they still fall back to the whole tree, which is where the
    # over-approximation that keeps the analysis sound actually lives.
    by_simple_name: dict[str, list[_Function]] = {}
    by_module: dict[tuple[str, str], list[_Function]] = {}
    for function in functions:
        simple = function.qualname.rsplit(".", 1)[-1]
        by_simple_name.setdefault(simple, []).append(function)
        by_module.setdefault((function.module, simple), []).append(function)

    def resolve(caller: _Function, called: str) -> list[_Function]:
        local = by_module.get((caller.module, called))
        return local if local else by_simple_name.get(called, [])

    modules = {function.module for function in functions}
    matched = tuple(
        pattern
        for pattern in surface
        if any(fnmatch(module, pattern) for module in modules)
    )
    unmatched = tuple(pattern for pattern in surface if pattern not in matched)

    entries = [
        function
        for function in functions
        if any(fnmatch(function.module, pattern) for pattern in surface)
    ]

    paths: list[SealedPath] = []
    for entry in entries:
        entry_name = f"{entry.module}.{entry.qualname}"
        stack: list[tuple[_Function, tuple[str, ...]]] = [(entry, (entry_name,))]
        seen: set[str] = set()
        while stack:
            current, chain = stack.pop()
            identity = f"{current.module}.{current.qualname}"
            if identity in seen:
                continue
            seen.add(identity)
            if identity not in permitted:
                for reference in current.references:
                    paths.append(
                        SealedPath(entry=entry_name, chain=chain, reference=reference)
                    )
            for called in sorted(current.calls):
                for callee in resolve(current, called):
                    target = f"{callee.module}.{callee.qualname}"
                    if target not in seen:
                        stack.append((callee, (*chain, target)))

    return Analysis(
        paths=tuple(paths),
        entry_points=tuple(sorted(f"{f.module}.{f.qualname}" for f in entries)),
        matched_patterns=matched,
        unmatched_patterns=unmatched,
    )
