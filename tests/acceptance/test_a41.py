"""Acceptance test A41: the ground truth is off the investigation's surface.

A41 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The ground truth is on the ``Investigation`` public
surface, twice"*, and reads:

    ``test_a41_the_truth_is_not_on_the_investigation_surface`` -- no public
    attribute or method of ``Investigation`` returns, contains or renders the
    scenario's truth, checked by traversal rather than by name; and a structure
    key reachable from ``EngineView.table`` does not disclose a defect's
    parameters.

The same entry's **Idea** is what the gate is a check on: *"Close two read paths
by which a research system can reach the scenario's truth, and replace the
comments that currently claim it cannot with enforcement."*

What was wrong
--------------

Four docstrings state as a guarantee that a system "cannot reach the ground
truth through any public attribute or method" --
:class:`~sciagent.systems.base.Investigation`'s own, its module's,
and two in :mod:`sciagent.eval.scoring`. Three read paths made that false.

1. :attr:`~sciagent.experiments.executor.ExecutionResult.defect` **is** the
   truth. :meth:`~sciagent.systems.base.Investigation.run` returned that object
   and appended it to the history that
   :attr:`~sciagent.systems.base.Investigation.history` republishes, so
   ``investigation.history[-1].defect`` handed a system SPEC §8's D1 = 0, D2 and
   D3 maximal, and ``log_score`` = 0.
2. The registry row rendered it a second time. ``Executor._config`` writes
   ``"defect": defect_key(defect)`` into the content address, so
   ``history[i].record.key.config["defect"]`` was the same truth as a readable
   string. The backlog entry names the first path and not this one; a traversal
   finds it anyway, which is the reason the criterion says *by traversal rather
   than by name*.
3. :attr:`~sciagent.inference.view.EngineView.table` handed over the whole
   :class:`~sciagent.inference.empirical.EmpiricalTable`, whose ``structures``
   are readable renderings rather than hashes -- parameters included -- and the
   campaign threads one table from cell to cell, so every previous cell's truth
   was in the artefact each campaign loads.

Neither is read by any shipped system, so no recorded result is known to be
affected. What makes them worth a gate is invariant 2: enforce with runtime
structure, not comments.

Why the scenario is null-seeded, and why that is load-bearing
-------------------------------------------------------------

The truth must be *outside* what the investigation entertains, or the criterion
is unsatisfiable rather than merely unmet. On an in-library scenario the truth
is legitimately one of the structures a system is asked to choose between: it
sits in :attr:`~sciagent.systems.base.Investigation.graph`, in
``engine.hypotheses``, and in every posterior the engine reports, and it must,
because "which of these is it?" is the task. A traversal cannot tell that
disclosure apart from a leak.

:func:`~sciagent.systems.base.null_seeded_graph` gives every investigation in
this module a hypothesis space holding the null alone, and the one structure
proposed below is deliberately *not* the truth. So on this surface any
appearance of the truth is a leak, and the traversal's verdict is exact rather
than approximate.

The same reasoning is what makes the second clause the right shape. An opaque
structure key still lets a system confirm a structure it can *construct* -- it
addresses the row it wants by handing over the ``Defect`` -- and stops it
enumerating one it cannot. S11's out-of-library truth, which no agent-grammar
enumeration reaches, is exactly the case that separates the two.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario, slice_scenarios
from sciagent.core.edits import EDIT_TYPES, Defect, canonical, sort_key
from sciagent.core.errors import SciAgentError
from sciagent.core.types import HypothesisId
from sciagent.experiments.dsl import defect_key
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    structure_key,
)
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, null_seeded_graph

#: The scenario the gate is constructed on. S1 is in-library and cheap, so its
#: truth has a row in the gate table -- which is what makes the third read path
#: *present* here rather than hypothetical. The graph below is null-seeded, so
#: being in the table is not being entertained.
SCENARIO = "S1"

#: How deep the traversal follows the surface. Every known path is shallower:
#: ``history`` -> result -> ``record`` -> ``key`` -> ``config`` -> value is six,
#: and ``engine`` -> table -> ``counts`` -> key tuple -> string is five. The
#: margin is for a path nobody has thought of, which is the point of traversing.
DEPTH = 10

#: Objects with no interior worth following. ``str`` is here because a string is
#: checked rather than descended into.
_ATOMIC = (str, bytes, bytearray, int, float, complex, bool, type(None))

#: Whose objects are opened up by name. Containers are always descended into,
#: whoever built them, but only a type this repository defines has its public
#: accessors read -- and the reason is a measured escape rather than tidiness.
#:
#: ``EditGrammar.structures()`` returns a **generator**, and a generator carries
#: ``gi_frame.f_globals``: every global of the module it was defined in, which
#: is every import that module made. Traversing that reached ``numpy.test`` and
#: called it, and the gate ran numpy's own test suite instead of its criterion.
#:
#: Nothing is lost by stopping there. The truth is a ``Defect`` -- a frozenset
#: of this repository's edit dataclasses -- and it can only sit in a foreign
#: object's field if this repository put it there, which is a path through one
#: of our own types and is followed.
_PROJECT = ("sciagent", "environments")


def _investigation() -> tuple[Investigation, Defect, EmpiricalTable]:
    """Return a populated investigation, the truth it hides, and the real table.

    Built directly rather than through
    :func:`~sciagent.eval.campaign.run_scenario` because this gate reads the
    investigation itself afterwards, which a scored run does not hand back.

    Populated on purpose: an investigation that has run nothing has an empty
    history, and an empty history discloses nothing however it is typed. Every
    read path this gate closes needs something in it to leak.
    """
    target = scenario(SCENARIO)
    table = gate_table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    investigation = Investigation(
        scenario_id=target.id,
        designs=target.designs,
        truth=target.executed,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget
        ),
        engine=EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR)),
        graph=graph,
        seed=target.seed,
    )
    designs = investigation.designs
    for step in range(3):
        if not investigation.affords():
            break
        investigation.run(designs[step % len(designs)], targets=(HypothesisId("null"),))
    name, structure = _decoy(target.executed)
    investigation.propose(
        HypothesisId(name),
        program_edit=structure,
        rationale=f"library structure {name!r}, which is not the truth",
    )
    return investigation, target.executed, table


def _decoy(truth: Defect) -> tuple[str, Defect]:
    """Return one closed-set structure that is not ``truth`` and not the null.

    Proposed so the surface carries an entertained structure other than the
    seeded null. A gate that proposed the truth would be asserting that a
    system's own proposal is invisible to it, which is neither true nor wanted.
    """
    for name, structure in sorted(closed_set().items()):
        if structure and structure != truth:
            return name, structure
    raise AssertionError(
        f"the closed set holds no structure that is neither the null nor "
        f"{structure_key(truth)!r}; this gate needs one"
    )


def _renderings(truth: Defect) -> tuple[str, ...]:
    """Return every string that spells the truth out, in any known encoding.

    The two canonical ones address a table row and a registry field, and the
    per-edit ``repr`` covers a surface that renders an edit directly rather than
    through either. A leak that invented a fourth encoding would escape this and
    be caught by the object-identity half of the same check instead.
    """
    return (
        structure_key(truth),
        defect_key(truth),
        *(repr(edit) for edit in canonical(truth)),
    )


def _nameable_defects() -> tuple[Defect, ...]:
    """Return every defect this suite can put a name to.

    The second clause reads *"a structure key ... does not disclose **a**
    defect's parameters"*, and the generic article is the whole of it. Clause 1
    already catches this scenario's own truth on ``engine.table``, at level four
    of the traversal; scoped to that truth, clause 2 would be a restatement
    rather than a second conjunct. What it adds is every *other* defect in the
    artefact -- which is what the entry's rationale is about, since the campaign
    threads one table from cell to cell and S11's out-of-library truth is in the
    file every campaign loads.

    So the check runs over the closed set and over every slice scenario's truth,
    not over the scenario under investigation alone. A gate scoped to the latter
    passes an implementation that opaques five of the gate table's twenty keys
    and leaves S2, S3, S4, S6, S7, S8 and S12's truths legible -- and S11's is
    then caught only by the coincidence that one of its floats is one of S1's.
    """
    truths = [structure for structure in closed_set().values()]
    for target in slice_scenarios():
        truths.append(target.truth)
        truths.append(target.executed)
    return tuple(truths)


def _parameter_renderings(defect: Defect) -> tuple[str, ...]:
    """Return ``defect``'s parameters as a structure key would render them.

    The second clause is about *parameters* specifically -- an opaque key that
    still spelled ``base_rate=0.0155…`` would disclose the thing the criterion
    names -- so this is checked against structure keys and nowhere else. Applied
    to the whole surface it would be brittle rather than strict: a design's
    template id carries a forcing schedule of floats, and a defect sharing one
    value with it is a coincidence and not a disclosure.
    """
    return tuple(
        f"{name}={value!r}"
        for edit in canonical(defect)
        for _, _, _, parameters in (sort_key(edit),)
        for name, value in parameters
    )


def _takes_no_argument(value: object) -> bool:
    """Return whether ``value`` can be called with nothing supplied."""
    try:
        signature = inspect.signature(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return all(
        parameter.default is not inspect.Parameter.empty
        or parameter.kind
        in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    )


def _accessors(obj: object) -> Iterator[object]:
    """Yield what every public name on ``obj`` gives, calling the nullary ones.

    Public means no leading underscore, which is the criterion's own word: a
    private field is the arrangement :class:`Investigation` already uses for the
    truth and this gate is not a claim about it.

    An accessor that raises is skipped -- a name that will not answer discloses
    nothing. The exceptions caught are the framework's own plus the three Python
    raises a reflective read provokes; anything else is a real fault and is left
    to fail the test.
    """
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        try:
            attribute = getattr(obj, name)
        except (SciAgentError, AttributeError, TypeError, ValueError):
            continue
        if callable(attribute):
            if not _takes_no_argument(attribute):
                continue
            try:
                yield attribute()
            except (SciAgentError, AttributeError, TypeError, ValueError):
                continue
        else:
            yield attribute


def _followable(items: Iterator[object]) -> Iterator[object]:
    """Drop the children that are namespaces rather than values.

    A module or a class reached as an attribute or a dict value is a door into
    every import behind it, and behind none of them is this scenario's truth.
    """
    for item in items:
        if isinstance(item, type) or inspect.ismodule(item):
            continue
        yield item


def _children(obj: object) -> Iterator[object]:
    """Yield what ``obj`` holds: its elements if a container, else its surface."""
    if isinstance(obj, _ATOMIC):
        return
    if isinstance(obj, Mapping):
        yield from _followable(iter(obj.keys()))
        yield from _followable(iter(obj.values()))
        return
    if isinstance(obj, (tuple, list, set, frozenset)):
        yield from _followable(iter(obj))
        return
    if type(obj).__module__.split(".")[0] in _PROJECT:
        yield from _followable(_accessors(obj))


def _reachable(root: object) -> tuple[object, ...]:
    """Return every object reachable from ``root`` through public names.

    Breadth is bounded by :data:`DEPTH`, and every object seen is retained:
    without holding a reference, a freshly built value could be collected and its
    ``id`` reused by the next one, which would silently prune the traversal.

    ``seen`` records the *shallowest* level each object was reached at, not
    merely that it was reached. A plain identity set makes coverage depend on
    search order: an object first popped at the depth limit is marked seen and
    never expanded, so a later, shallower path to the same object -- with budget
    left to descend -- is discarded. What the traversal covered would then be a
    fact about the stack rather than about the surface.
    """
    seen: dict[int, int] = {}
    found: list[object] = []
    frontier: list[tuple[object, int]] = [(root, 0)]
    while frontier:
        obj, level = frontier.pop()
        if seen.get(id(obj), DEPTH + 1) <= level:
            continue
        if id(obj) not in seen:
            found.append(obj)
        seen[id(obj)] = level
        if level >= DEPTH:
            continue
        frontier.extend((child, level + 1) for child in _children(obj))
    return tuple(found)


@pytest.fixture(scope="module")
def populated() -> tuple[Investigation, Defect, EmpiricalTable]:
    """Return one investigation, its truth and the engine's real table.

    Module-scoped and built once: the three tests below only read it, and each
    build spends a scenario's budget against the executor.
    """
    return _investigation()


class TestA41TheTruthIsNotOnTheInvestigationSurface:
    """A41: what a system is handed does not contain the answer."""

    def test_a41_the_truth_is_not_on_the_investigation_surface(
        self, populated: tuple[Investigation, Defect, EmpiricalTable]
    ) -> None:
        """No object reachable through a public name is, or renders, the truth."""
        investigation, truth, _ = populated
        edits = frozenset(canonical(truth))
        renderings = _renderings(truth)

        surface = _reachable(investigation)
        assert len(surface) > 100, (
            f"the traversal reached only {len(surface)} objects, which is too few "
            f"to have covered the surface; the gate would pass vacuously"
        )

        for obj in surface:
            if isinstance(obj, str):
                for rendering in renderings:
                    assert rendering not in obj, (
                        f"a public path off Investigation renders the truth: "
                        f"{rendering!r} appears in {obj!r}"
                    )
                continue
            if isinstance(obj, frozenset):
                assert obj != truth, (
                    f"a public path off Investigation returns the truth itself: "
                    f"{structure_key(truth)!r}"
                )
                continue
            if isinstance(obj, EDIT_TYPES):
                assert obj not in edits, (
                    f"a public path off Investigation returns an edit of the "
                    f"truth: {obj!r}"
                )

    def test_a41_a_structure_key_does_not_disclose_a_defects_parameters(
        self, populated: tuple[Investigation, Defect, EmpiricalTable]
    ) -> None:
        """No key reachable from ``EngineView.table`` renders any defect.

        Every defect in the artefact, not this scenario's truth alone: see
        :func:`_nameable_defects` for why the criterion's *"a defect's"* has to
        be read generically, and for the implementation a truth-scoped version
        of this test lets through.
        """
        investigation, _, real = populated
        keys = investigation.engine.table.structures
        assert keys, "the table handed to a system holds no structure at all"

        republished = sorted(set(keys) & set(real.structures))
        assert not republished, (
            f"EngineView.table republishes {len(republished)} of the engine's "
            f"{len(real.structures)} readable structure key(s), among them "
            f"{republished[:2]!r}"
        )

        for defect in _nameable_defects():
            parameters = _parameter_renderings(defect)
            identifiers = tuple(
                (target, option)
                for _, target, option, _ in map(sort_key, canonical(defect))
            )
            for key in keys:
                for parameter in parameters:
                    assert parameter not in key, (
                        f"structure key {key!r} discloses a defect parameter: "
                        f"{parameter!r}"
                    )
                for target, option in identifiers:
                    assert target not in key and option not in key, (
                        f"structure key {key!r} discloses a defect's target or "
                        f"option ({target!r}, {option!r})"
                    )

    def test_a41_an_opaque_table_answers_the_lookups_a_system_makes(
        self, populated: tuple[Investigation, Defect, EmpiricalTable]
    ) -> None:
        """Opacity hides enumeration and nothing else: rows still resolve.

        The counterfeit fix this rules out is emptying the table, or handing over
        one a system cannot address. Every likelihood a system reads goes through
        :meth:`~sciagent.inference.empirical.EmpiricalTable.row` keyed by a
        ``Defect`` it already holds, so those must return exactly what the
        engine's own table returns.
        """
        investigation, _, real = populated
        seen = investigation.engine.table
        assert seen.replicates == real.replicates
        assert sorted(seen.templates) == sorted(real.templates)
        assert len(seen.structures) == len(real.structures)

        checked = 0
        for structure in closed_set().values():
            if not real.holds(structure):
                continue
            assert seen.holds(structure)
            for template_id in sorted(real.templates):
                assert seen.row(structure, template_id) == real.row(
                    structure, template_id
                )
                assert seen.probabilities(structure, template_id) == real.probabilities(
                    structure, template_id
                )
            checked += 1
        assert checked >= 2, (
            f"only {checked} structure(s) were addressable through the view's "
            f"table; the equivalence check is too weak to mean anything"
        )

    def test_a41_the_history_carries_no_defect_and_no_registry_row(
        self, populated: tuple[Investigation, Defect, EmpiricalTable]
    ) -> None:
        """Pin the two closed paths by name, beside the traversal that found them.

        Redundant with the traversal on purpose. The traversal states the
        criterion; this states what it caught, so a reader of a future failure
        sees which read path came back rather than only that one did.
        """
        investigation, _, _ = populated
        history = investigation.history
        assert history, "the fixture ran no experiment, so this pins nothing"
        for execution in history:
            assert not hasattr(execution, "defect"), (
                "Investigation.history republishes the executor's truth-bearing "
                "record; it must hand over a projection without `defect`"
            )
            assert not hasattr(execution, "record"), (
                "the registry row renders the truth in its content-address "
                "config, so the projection must not carry it"
            )

    def test_a41_run_returns_the_same_projection_as_history(self) -> None:
        """``run`` is a public method, so the criterion binds its return too.

        The backlog entry names ``history``. The gate says "attribute *or
        method*", and ``run`` hands back the identical object -- closing only the
        property would leave the shorter path open.

        On its own investigation rather than the class fixture, because it runs a
        design: sharing would make the tests above depend on whether this one had
        executed yet, which is an ordering dependency and not a criterion.
        """
        investigation, _, _ = _investigation()
        assert investigation.affords(), (
            "the investigation spent its whole budget, so this cannot run a design"
        )
        returned = investigation.run(investigation.designs[0])
        assert type(returned) is type(investigation.history[-1])
        assert not hasattr(returned, "defect")
        assert not hasattr(returned, "record")
