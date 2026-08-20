"""Acceptance test A28: the comparator SPEC §12 criterion 5 names, built at last.

A28 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The criterion-5 comparator that was never built"*,
and reads:

    ``test_a28_the_uniform_proposer_is_licensed_and_seeded`` -- every proposal
    is grammar-licensed, on-grid, and a pure function of (scenario seed,
    replicate index); two processes produce identical proposal sequences.

What was missing
----------------

SPEC §12 criterion 5 asks whether V7 proposes an S11 extension exceeding
"B6-equivalent random structured generation" on D3 with a non-overlapping 95%
interval. No B6 existed: SPEC §5 defers it to the full benchmark, and
``docs/DECISIONS.md`` (2026-08-04) records the criterion as unmeasured for
exactly that reason -- *"no B6 exists"*. The criterion therefore had a
comparator by name and nothing to compare against, and the headline contrast
lost its designated deflator.

What B6 is, and why it is ``Hybrid``
------------------------------------

B6 is :class:`~sciagent.systems.hybrid.Hybrid` -- the V7 class -- holding a
:class:`~sciagent.systems.baselines.uniform.UniformProposer` instead of an LLM
proposal layer. That is the whole design. The backlog entry asks for a proposer
"drawn at the same Stage A gate under the same budget split as V7", and building
B6 out of V7's own class makes both of those facts about *construction* rather
than claims some later reader has to audit. It is the same argument
:func:`~sciagent.systems.ablation.memory_ablation` makes for V3 and V4, and the
reason :meth:`~sciagent.systems.hybrid.Hybrid.name` was already parameterised.

What that leaves as the only difference between V7 and B6 is *how a point in the
action space is chosen* -- a model, or a uniform draw -- which is precisely what
criterion 5 asks about.

The draw, and why it is not the corner form
-------------------------------------------

The backlog entry offered two forms: uniform over ``enumerate_edits(1)``'s
48-corner stratification, or uniform over the full grid. This is the second,
decided with the user on 2026-08-19 and recorded in ``docs/DECISIONS.md``.

The corner form is cheaper -- 48 structures, whose rows amortise across a
campaign -- but a grid box's corners are where the degenerate parameterisations
live. ``BeamSearch``'s own ``_UNSCORABLE`` note says so in as many words: *"an
edit space's corners contain parameterisations that produce degenerate
programmes, and a search that enumerates corners will find them."* A comparator
restricted to corners would lose for a reason unrelated to random generation,
and a deflator that is too easy to beat flatters V7 -- the direction this
repository can least afford to be wrong in, since SPEC §12 says beating a
baseline is the research question rather than an exit criterion.

So B6 draws in exactly V7's action space: the structural menu cell uniformly,
then each of that cell's parameter grid indices uniformly. The menu is
:func:`~sciagent.systems.llm.encoding.structural_menu`'s, which is what a model
is offered, and the draw is what a model would be choosing between.

What this module learned from its own review
--------------------------------------------

The first version of this file was reviewed before any implementation existed --
``/test-review``, which is the one moment a test is looked at alone -- and came
back **too weak**, with three wrong implementations executed against it that all
passed 24 of 24. The three, and what now stops each, are worth recording because
they are the failure modes this gate is actually exposed to:

* **A proposer that ignores the seed it was built with and indexes its draw from
  a module-level counter.** Everything determinism-shaped was asserted against
  the free function :func:`uniform_draft`, and ``UniformProposer`` appeared only
  in an ``isinstance`` check -- ``propose`` was never called. Now
  :func:`_proposals` drives the class itself, and
  ``test_a28_the_call_counter_is_per_proposer`` interleaves two proposers so a
  shared counter shows up as the second one skipping ahead.

* **A proposer whose every draw is malformed**, so B6 admits nothing. ``Hybrid``
  records that as an attempt with outcome ``"malformed"`` and carries on, so
  ``run.attempts`` was still truthy and ``run.proposed`` still held the library
  -- every member of which is licensed and on-grid. The run went green in 5.6s
  against 136s, and a runtime that collapses when the comparator stops working
  is the tell. ``test_a28_no_draw_is_malformed_or_refused`` names the outcomes a
  uniform draw may legitimately have.

* **A draw confined to grid indices 0 and 1.** The menu's grids all hold 64
  points, so the two guards that were meant to pin the full-grid form -- "some
  index is interior" and "more distinct indices than there are grids" -- were
  satisfied by 32 > 16. ``TestA28TheDrawIsUniform`` replaces them with the
  distribution's own statistics, stated per grid against that grid's own size.

A second review, ``/code-review`` during ``/preflight``, found a **fourth** that
survived the rewrite, and it is the subtlest of the four:

* **A proposer whose ``_calls`` counter increments but never reaches the draw**
  -- ``uniform_draft(menu, seed, 0)`` every time. Nothing tied the class's output
  to the *function's indexed sequence*: every other test here reads either the
  free function directly or a fresh proposer's first call, and both agree with a
  constant index. In a run B6 would propose one structure twice, which
  ``Hybrid._admit`` records as ``"duplicate"`` -- inside ``DRAWABLE_OUTCOMES`` --
  so the arm would quietly take half V7's effective proposal budget with this
  module green. ``test_a28_the_proposer_draws_the_function_s_sequence`` is the
  assertion that makes the class and the function one thing.

The same review found the module had no golden pin at all: every test compared
one fresh computation against another, so changing the draw formula without
bumping ``_STREAM`` would leave everything green while silently invalidating
recorded B6 readings. ``test_a28_the_draw_stream_is_pinned_to_recorded_values``
is the one test here that compares against a value from outside this process,
and it exists to go red.

Why "pure function of (seed, index)" is checkable without an investigation
-------------------------------------------------------------------------

:func:`~sciagent.systems.baselines.uniform.uniform_draft` takes the menu, a seed
and a call index and returns the draft. It reads no investigation state, so the
gate's property is a fact about a function signature rather than a claim about a
run. ``UniformProposer.propose`` is that function plus a per-instance counter --
and both halves are exercised here, because the review showed that testing the
function alone leaves the class free to do anything at all.

The seed handed to B6 is ``CellTask.seed``, which
:func:`~sciagent.eval.matrix.replicate_seeds` already guarantees is a pure
function of ``(scenario_seed, replicate index)``. That composition has three
joints, and this module pins all three: that
:meth:`~environments.pointproc.runner.MatrixRunner.execute` gives the arm
``task.seed`` rather than the scenario's own; that
:func:`~environments.pointproc.runner.system_for` builds the proposer *at* that
seed rather than at one of its own; and that the proposer then draws at the
seed it was built with.

The middle one was missing until 2026-08-20, and its absence was not visible
from either side: ``system_for`` could have passed ``Seed(0)`` while every test
here stayed green and all twenty replicates drew one sequence -- the exact
failure the refusal in ``system_for`` exists to prevent, one line further in.
The call index is a fourth thing the sequence depends on, pinned separately
above; it is not a joint in this chain because no handoff carries it.
"""

from __future__ import annotations

import importlib.util
import math
import os
import subprocess
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table, save_gate_table

from environments.pointproc.matrix import CRITERION5_CELLS, REPLICATES, SPEC9_CELLS
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.runner import (
    CRITERION5_SYSTEMS,
    MATRIX_SYSTEMS,
    MatrixRunner,
    scenario_seed,
    system_for,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import (
    DeterminismError,
    OffGridParameterError,
    SystemConfigurationError,
)
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    MetricVersion,
    ScenarioId,
    Seed,
)
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.eval.matrix import Cell, replicate_seeds, tasks_of
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.ledger import CampaignLedger
from sciagent.registry.store import ExperimentKey, ExperimentStore
from sciagent.systems.base import Investigation, null_seeded_graph
from sciagent.systems.baselines.uniform import UniformProposer, uniform_draft
from sciagent.systems.hybrid import Hybrid, ProposalSource
from sciagent.systems.llm import (
    RECORD,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
)
from sciagent.systems.llm.encoding import MenuEntry, decode, structural_menu
from sciagent.systems.llm.provider import slug_hypothesis_name

#: A seed with no significance beyond being fixed. Every draw checked here is
#: taken under it or under a stated neighbour, so a test that changed behaviour
#: with the seed would be testing the seed.
SEED = Seed(7919)

#: How many draws the per-draw properties are checked over. Larger than the two
#: a run can make, because a property that holds for two draws and fails for the
#: fiftieth is a property that fails.
DRAWS = 64

#: How many draws the *distribution* is checked over. Large enough that the
#: statistics in :class:`TestA28TheDrawIsUniform` sit tens of standard errors
#: from their thresholds, and cheap because a draw is a hash and no simulation.
SAMPLE = 4096

#: The first four draws under :data:`SEED`, recorded on 2026-08-19 against
#: ``_STREAM = "uniform-proposer/1"`` and the agent grammar of that date.
#:
#: A golden pin, and the only test in this module that compares against a value
#: from outside the current process. See
#: ``test_a28_the_draw_stream_is_pinned_to_recorded_values`` for what to do when
#: it fails -- the short version is *not* "update these".
PINNED_DRAWS: tuple[tuple[int, tuple[int, ...]], ...] = (
    (2, (47, 33, 12)),
    (2, (7, 58, 3)),
    (2, (35, 25, 15)),
    (4, (35, 28, 63)),
)

#: The scenario SPEC §12 criterion 5 names, and the one whose truth is outside
#: the library -- so the Stage A gate opens and B6 actually proposes.
OUT_OF_LIBRARY = "S11"

#: A scenario whose truth *is* in the library. The gate stays shut and B6 makes
#: no proposal at all -- which is V7's behaviour, and the point of building B6
#: out of V7's class.
IN_LIBRARY = "S1"

#: The outcomes a uniform draw may legitimately have.
#: :class:`~sciagent.systems.hybrid.ProposalAttempt` also admits ``"malformed"``
#: -- a draft that does not denote a licensed structure -- and ``"refused"``.
#: A draw taken from the grammar's own menu cannot be malformed, so that one
#: appearing means the proposer is not drawing.
#:
#: ``"refused"`` is the one that needs care, because it has **two** producers
#: and only one of them is a provider declining. ``Hybrid._propose_once``
#: returns it on a ``ProviderError``, which B6 cannot reach -- there is no
#: provider behind a draw. ``Hybrid._admit`` also returns it on a
#: ``BudgetExhaustedError``, which B6 owns outright: it inherits V7's budget
#: split by construction. That path says nothing about drawing, and
#: ``Hybrid._extend`` breaks out of the proposal loop at the first
#: ``"refused"`` -- so a budget-exhausted B6 stops proposing early, which is
#: the silent half-budget failure this module exists to catch, arriving
#: through the door the exclusion leaves open. Keep it excluded, and say which
#: one fired.
DRAWABLE_OUTCOMES = frozenset({"admitted", "duplicate", "unmeasurable"})


def _menu() -> tuple[MenuEntry, ...]:
    """Return the menu every draw in this module is taken against."""
    return structural_menu(AGENT_GRAMMAR)


@cache
def _drafts(seed: Seed, count: int = DRAWS) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """Return ``count`` draws under ``seed``, as (cell, grid indices) pairs.

    Reduced to integers deliberately. What the gate is about is the *choice*,
    and comparing decoded defects would compare floats the grid derived -- equal
    for the right reason, and hard to read when they are not.
    """
    menu = _menu()
    reduced: list[tuple[int, tuple[int, ...]]] = []
    for index in range(count):
        draft = uniform_draft(menu, seed, index)
        reduced.extend((edit.structure, tuple(edit.parameters)) for edit in draft.edits)
    return tuple(reduced)


def _decoded(seed: Seed, count: int = DRAWS) -> tuple[Defect, ...]:
    """Return the defects ``count`` draws under ``seed`` denote."""
    menu = _menu()
    return tuple(
        decode(AGENT_GRAMMAR, menu, uniform_draft(menu, seed, index))
        for index in range(count)
    )


def _is_on_grid(grammar: EditGrammar, defect: Defect) -> bool:
    """Return whether every parameter of ``defect`` is a grid point.

    Asked of :meth:`~sciagent.core.edits.EditGrammar.code_length` rather than
    re-derived here, because that method is where the framework's own answer
    lives: it raises :class:`~sciagent.core.errors.OffGridParameterError` rather
    than snapping, which is exactly the distinction ``ParameterGrid.snap`` exists
    to be the other side of.
    """
    try:
        return grammar.code_length(defect) > 0.0
    except OffGridParameterError:
        return False


@cache
def _investigation() -> Investigation:
    """Return an investigation for driving ``propose``, with nothing run on it.

    Shared across tests, and safe to share for the reason under test: a draw
    reads no investigation state, so ``propose`` cannot change what a later call
    on the same object returns. ``test_a28_the_proposer_leaves_the_investigation
    _alone`` is what turns that from an assumption into an assertion.
    """
    table = gate_table()
    target = scenario(IN_LIBRARY)
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    return Investigation(
        scenario_id=target.id,
        designs=slice_designs(),
        truth=frozenset(),
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget
        ),
        engine=EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR)),
        graph=graph,
        seed=target.seed,
    )


def _proposals(seed: Seed, count: int) -> tuple[Defect, ...]:
    """Return the structures a *fresh* ``UniformProposer`` at ``seed`` proposes.

    The class, not the free function. The review that rewrote this module found
    every determinism assertion pointed at :func:`uniform_draft`, which left the
    proposer free to ignore its seed and to index from process-global state --
    and a proposer that does both is what a campaign actually runs.
    """
    proposer = UniformProposer(AGENT_GRAMMAR, seed)
    investigation = _investigation()
    return tuple(proposer.propose(investigation).program_edit for _ in range(count))


def _per_grid(seed: Seed, count: int) -> dict[tuple[int, int, int], tuple[float, ...]]:
    """Return the normalised indices drawn, keyed by which grid drew them.

    The key is ``(menu cell, position within the cell, that grid's size)``, so a
    test can hold one grid to its own size rather than to the smallest in the
    menu. Splitting rather than pooling is what makes the coverage claims say
    what their names say -- see the two tests that read this.
    """
    menu = _menu()
    split: dict[tuple[int, int, int], list[float]] = {}
    for structure, parameters in _drafts(seed, count):
        for position, (grid, index) in enumerate(
            zip(menu[structure].grids, parameters, strict=True)
        ):
            split.setdefault((structure, position, grid.size), []).append(
                index / (grid.size - 1)
            )
    return {key: tuple(values) for key, values in split.items()}


def _normalised(seed: Seed, count: int) -> tuple[float, ...]:
    """Return every drawn grid index, scaled to ``[0, 1]`` by its own grid.

    Normalised so the distribution tests state a property of the *draw* rather
    than of this environment's grid sizes, which happen all to be 64.
    """
    menu = _menu()
    scaled: list[float] = []
    for structure, parameters in _drafts(seed, count):
        for grid, index in zip(menu[structure].grids, parameters, strict=True):
            scaled.append(index / (grid.size - 1))
    return tuple(scaled)


#: The child program. Reaches the grammar through
#: :func:`~environments.pointproc.grammar.agent_grammar` and not through
#: ``environments.pointproc.tables``, whose ``AGENT_GRAMMAR`` is the same object
#: built by the same function but which drags in the table machinery, the
#: registry and scipy to get there. The child needs a menu and nothing else, and
#: a leaner child is not a style preference here -- see :func:`_child_output`.
_CHILD = """
import sys

from environments.pointproc.grammar import agent_grammar
from sciagent.core.types import Seed
from sciagent.systems.baselines.uniform import uniform_draft
from sciagent.systems.llm.encoding import structural_menu

menu = structural_menu(agent_grammar())
for index in range({draws}):
    draft = uniform_draft(menu, Seed({seed}), index)
    for edit in draft.edits:
        sys.stdout.write(f"{{index}} {{edit.structure}} {{list(edit.parameters)}}\\n")
"""


def _child_output(hash_seed: str) -> str:
    """Return a fresh process's draws, under a stated ``PYTHONHASHSEED``.

    The thread caps are not tuning and not a workaround for a flaky test. This
    parent holds the gate table and a grown engine table, and the first version
    of this helper spawned three children that each brought up an OpenBLAS
    thread pool sized to twelve logical processors; the first died with
    ``OpenBLAS error: Memory allocation still failed after 10 retries``. Memory
    binds before cores on this machine -- ``CLAUDE.md`` records ``-n auto``
    failing three tests on ``MemoryError`` for the same reason. The child
    multiplies no matrices, so a pool of one is what it actually needs, and
    capping it is declining an allocation rather than hiding a fault.
    """
    environment = dict(
        os.environ,
        PYTHONHASHSEED=hash_seed,
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
    )
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD.format(draws=DRAWS, seed=int(SEED))],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0, (
        f"the child exited {completed.returncode} under "
        f"PYTHONHASHSEED={hash_seed}\n"
        f"--- stderr ---\n{completed.stderr}\n"
        f"--- stdout ---\n{completed.stdout}"
    )
    return completed.stdout


def _in_process_output() -> str:
    """Return this process's draws, in the child's exact rendering."""
    menu = _menu()
    lines: list[str] = []
    for index in range(DRAWS):
        draft = uniform_draft(menu, SEED, index)
        lines.extend(
            f"{index} {edit.structure} {list(edit.parameters)}\n"
            for edit in draft.edits
        )
    return "".join(lines)


def _first_replicate_seed(scenario_id: str) -> Seed:
    """Return replicate 00's seed for a scenario, as a campaign would derive it.

    Used rather than the scenario's own seed, because the scenario seed is
    precisely the wrong thing to hand a proposer -- it is one value for all
    twenty replicates -- and a module whose only end-to-end run passed it would
    be demonstrating the mistake it warns about.
    """
    return replicate_seeds(scenario(scenario_id).seed, 1)[0]


def _b6_source(seed: Seed) -> UniformProposer:
    """Return the proposal source ``system_for`` builds B6 around.

    Reaches through ``Hybrid``'s slot because that is the seam under test: the
    question is what ``system_for`` put there, not what ``Hybrid`` does with it.
    """
    system = system_for("B6", seed=seed)
    assert isinstance(system, Hybrid)
    source = system._layer
    assert isinstance(source, UniformProposer)
    return source


@cache
def _b6_run(scenario_id: str) -> ScenarioRun:
    """Run B6 once on one scenario, growing and saving the shared gate table.

    Cached because a run that proposes a structure the table has never seen pays
    a full simulated row for it, and the two runs this module needs should be
    paid for once per session rather than once per test.
    """
    table = gate_table()
    target = scenario(scenario_id)
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    run = run_scenario(
        target,
        system_for("B6", seed=_first_replicate_seed(scenario_id)),
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget
        ),
        engine=engine,
        graph=graph,
    )
    save_gate_table(engine.table)
    return run


class TestA28UniformProposer:
    """SPEC §12 criterion 5's comparator: licensed, on-grid, seeded, portable."""

    def test_a28_the_uniform_proposer_is_licensed_and_seeded(self) -> None:
        """The gate, in one test: licensed, on-grid, and seed-determined.

        The clauses of the criterion that can be stated over draws alone, and
        asserted against the *proposer* rather than only the draw function --
        which is what the review of this module found missing. The remaining
        clause, two processes agreeing, needs a second process and is below.
        """
        for defect in _proposals(SEED, DRAWS):
            AGENT_GRAMMAR.validate_defect(defect)
            assert _is_on_grid(AGENT_GRAMMAR, defect), (
                f"{sorted(map(str, defect))} carries a parameter that is not a "
                f"grid point, so the proposal is off-grid"
            )
        assert _proposals(SEED, DRAWS) == _proposals(SEED, DRAWS), (
            "two fresh proposers at one seed disagreed, so the sequence is not "
            "a function of the seed the proposer was built with"
        )

    def test_a28_the_proposer_draws_at_the_seed_it_was_built_with(self) -> None:
        """A proposer that stored its seed and drew from another is caught here.

        The first of the three wrong implementations the review executed: it
        satisfied every clause stated over :func:`uniform_draft`, because it
        called that function correctly -- at a constant. All twenty replicates
        of a cell would then draw one sequence, and criterion 5's interval would
        be computed over twenty copies of one number.
        """
        assert _proposals(SEED, 4) != _proposals(Seed(int(SEED) + 1), 4)

    def test_a28_the_proposer_draws_the_function_s_sequence(self) -> None:
        """``propose`` returns draw ``i`` at call ``i``, not draw 0 every time.

        The gap `/code-review` found after the earlier rewrite, and a fourth
        wrong implementation distinct from the three before it: a proposer whose
        ``_calls`` counter increments but never reaches the draw. Every other
        test here goes through either the free function or a *fresh* proposer's
        first call, so a constant index survives all of them -- and in a real run
        B6 would then propose one structure twice, which ``Hybrid._admit``
        records as ``"duplicate"``, an outcome inside ``DRAWABLE_OUTCOMES``. The
        arm would quietly get half V7's effective proposal budget with the whole
        gate green.

        This is the assertion that makes the class and the function one thing.
        """
        menu = _menu()
        expected = tuple(
            decode(AGENT_GRAMMAR, menu, uniform_draft(menu, SEED, index))
            for index in range(DRAWS)
        )
        assert _proposals(SEED, DRAWS) == expected
        assert len(set(expected)) > 1, (
            "the draw function itself returned one structure throughout, so "
            "this test cannot tell a constant index from a moving one"
        )

    def test_a28_the_call_counter_is_per_proposer(self) -> None:
        """Two proposers interleaved each see their own call indices.

        A module-level counter is what the review's first variant used. Under
        one, ``second``'s first proposal is the sequence's *third* draw, so the
        two lists disagree while every single-proposer test still passes.
        """
        investigation = _investigation()
        first = UniformProposer(AGENT_GRAMMAR, SEED)
        second = UniformProposer(AGENT_GRAMMAR, SEED)
        interleaved = [
            (
                first.propose(investigation).program_edit,
                second.propose(investigation).program_edit,
            )
            for _ in range(3)
        ]
        for mine, theirs in interleaved:
            assert mine == theirs
        assert first.calls == second.calls == 3

    def test_a28_the_proposer_leaves_the_investigation_alone(self) -> None:
        """A draw reads no investigation state and writes none.

        The other half of "and by nothing else": the draw is independent of the
        run, so a proposer cannot be selecting a structure that fits the
        evidence -- which would make B6 something other than random structured
        generation, and criterion 5's comparator no longer a deflator.
        """
        investigation = _investigation()
        before = (
            investigation.history,
            dict(investigation.proposed),
            int(investigation.budget.remaining),
        )
        proposer = UniformProposer(AGENT_GRAMMAR, SEED)
        for _ in range(4):
            proposer.propose(investigation)
        assert (
            investigation.history,
            dict(investigation.proposed),
            int(investigation.budget.remaining),
        ) == before

    def test_a28_a_drawn_proposal_never_fails_to_decode(self) -> None:
        """The proposer itself refuses nothing and malforms nothing.

        The review's second variant malformed every draw, and ``Hybrid`` records
        that as an outcome and carries on -- so a comparator that never lands a
        structure looks, from the run, like one that did. A draw taken from the
        grammar's own menu has no way to be malformed, and this says so where a
        wrong implementation would have to disagree.
        """
        proposer = UniformProposer(AGENT_GRAMMAR, SEED)
        investigation = _investigation()
        names: list[str] = []
        rationales: list[str] = []
        for _ in range(DRAWS):
            proposal = proposer.propose(investigation)
            assert len(proposal.program_edit) == 1
            AGENT_GRAMMAR.validate_defect(proposal.program_edit)
            names.append(proposal.name)
            rationales.append(proposal.rationale)
        # `uniform_draft` builds both from unconditional f-strings, but this is
        # a `Proposal` and not that `ProposalDraft`: `UniformProposer.propose`
        # copies the two fields across, and a copy that dropped one would be
        # invisible here without a check. So the rationale keeps a truthiness
        # assertion, which is the whole of what can fail at that copy.
        assert all(rationales), "a draw carried no rationale across the copy"
        # The name gets a stronger one instead, because a stronger one exists.
        # B6 hands its names over unslugged, so these are the strings
        # `Hybrid._admit`'s slug has to survive, and two draws sharing a name
        # would be recorded as a duplicate rather than an admission.
        assert len(set(names)) == len(names), "two draws shared a name"
        assert all(slug_hypothesis_name(name) != "__candidate__" for name in names)

    def test_a28_the_draw_stream_is_pinned_to_recorded_values(self) -> None:
        """The first four draws, recorded. This test exists to go red.

        Everything else here compares one fresh computation against another, so
        the whole module stays green if the draw formula changes -- the
        statistical properties still hold, and cross-process identity still
        holds, while every previously recorded B6 reading silently becomes
        incomparable. ``_STREAM``'s embedded version is the only thing standing
        between that and a silent corruption, and nothing was checking that
        anybody remembered to bump it. Raised by the determinism lens during
        A28's preflight.

        **If this test fails, do not update the values.** It failing means the
        draw sequence moved, which means either ``_STREAM`` must go to ``/2`` or
        the agent grammar's own version moved -- and in both cases every recorded
        B6 reading was taken under a different instrument and must be re-derived,
        not re-labelled. Updating the constants below to match is the one
        response that destroys the evidence this test exists to preserve.
        """
        menu = _menu()
        drawn = [
            (edit.structure, tuple(edit.parameters))
            for index in range(len(PINNED_DRAWS))
            for edit in uniform_draft(menu, SEED, index).edits
        ]
        assert tuple(drawn) == PINNED_DRAWS

    def test_a28_two_processes_produce_identical_proposal_sequences(self) -> None:
        """The gate's last clause, against three hash seeds and this process.

        ``PYTHONHASHSEED`` is varied for the reason A1's determinism child varies
        it: a draw that reached a ``set`` or a bare ``hash`` would agree with
        itself in-process and disagree here.
        """
        outputs = [
            (hash_seed, _child_output(hash_seed)) for hash_seed in ("0", "1", "random")
        ]
        reference = outputs[0][1]
        assert reference.strip(), "the child produced no draws"
        for hash_seed, output in outputs[1:]:
            assert output == reference, (
                f"PYTHONHASHSEED={hash_seed} drew a different sequence:\n"
                f"{reference}\nvs\n{output}"
            )
        assert _in_process_output() == reference, (
            "the in-process draw disagrees with a fresh process's"
        )

    def test_a28_each_replicate_of_the_cell_draws_its_own_sequence(self) -> None:
        """Twenty replicate seeds give twenty proposal sequences, not one.

        Stated over the *proposer*, because the earlier version of this test
        stated it over ``uniform_draft`` and separately re-derived ``tasks_of``
        from ``replicate_seeds`` -- which is that function's own body, and holds
        whether or not B6 exists. Four replicates rather than twenty: each
        builds a proposer and takes two draws, and the property is about
        distinctness rather than about the count.
        """
        cell = CRITERION5_CELLS[0]
        tasks = tasks_of(cell, scenario_seed(cell.scenario))[:4]
        sequences = {_proposals(task.seed, 2) for task in tasks}
        assert len(sequences) == len(tasks), (
            "two replicates of one cell drew the same sequence, so the seed is "
            "not reaching the proposer"
        )

    def test_a28_the_runner_hands_the_arm_the_replicate_seed(self) -> None:
        """The second joint: a campaign gives B6 ``task.seed``, not the scenario's.

        Without this the composition the gate rests on has a hole exactly where
        it matters -- a runner passing the scenario seed is silent, reproducible,
        and identical across all twenty replicates. Checked at the seam rather
        than by running a cell: the substitute raises, so nothing is simulated.
        """
        cell = CRITERION5_CELLS[0]
        task = tasks_of(cell, scenario_seed(cell.scenario))[3]
        seen: list[object] = []

        def substitute(name: str, **kwargs: object) -> None:
            seen.append(kwargs.get("seed"))
            raise SystemConfigurationError("stop before anything is simulated")

        runner = MatrixRunner(gate_table())
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("environments.pointproc.runner.system_for", substitute)
            with pytest.raises(SystemConfigurationError):
                runner.execute(task)
        assert seen == [task.seed]
        assert task.seed != scenario(str(cell.scenario)).seed, (
            "the replicate seed equals the scenario seed, so this test cannot "
            "tell the two apart"
        )

    def test_a28_both_proposal_sources_satisfy_one_protocol(self) -> None:
        """B6's proposer and V7's layer satisfy the protocol ``Hybrid`` holds.

        Asserted on both, because a protocol only one implementation satisfies is
        a class with extra steps -- and because the whole reason ``Hybrid``'s
        parameter was widened is that it now has two callers of different types.

        Weak on purpose, and worth saying so rather than overselling it: a
        ``runtime_checkable`` Protocol's ``isinstance`` checks that a ``propose``
        *attribute* exists and nothing about its signature or return type, so any
        object with a ``propose`` passes. ``mypy`` is what actually enforces the
        shape, at every ``Hybrid(...)`` call site. What this test catches is the
        one thing ``mypy`` would not shout about: the protocol being narrowed
        later such that an existing source stops satisfying it at runtime.
        """
        layer = ProposalLayer(
            ScriptedProvider([]), AGENT_GRAMMAR, TranscriptStore(mode=RECORD)
        )
        assert isinstance(layer, ProposalSource)
        assert isinstance(UniformProposer(AGENT_GRAMMAR, SEED), ProposalSource)

    def test_a28_b6_is_v7s_class_and_not_a_subclass_of_it(self) -> None:
        """Same gate and same budget split, by construction rather than by claim.

        The backlog entry asks for a proposer drawn "at the same Stage A gate
        under the same budget split as V7". Both live in
        :meth:`~sciagent.systems.hybrid.Hybrid.investigate`, so sharing the class
        is the strongest available form of that guarantee.

        ``type(...) is`` and not ``isinstance``, which the review of this module
        found accepts a ``class B6(Hybrid)`` overriding ``investigate`` with a
        different split -- ``Hybrid`` is not ``@final`` and ``investigate`` is an
        ordinary method, so that subclass shares the name and nothing else.
        """
        b6 = system_for("B6", seed=SEED)
        assert type(b6) is Hybrid
        assert b6.name == "B6"

    def test_a28_b6_needs_no_provider(self) -> None:
        """The comparator costs no model call, which is why it is conventional.

        ``system_for`` refuses an arm in ``LLM_SYSTEMS`` offered no provider. B6
        must not be one of those: SPEC §12 criterion 5's comparator existing only
        when an API key does would make the criterion unmeasurable for exactly
        the population that most needs to check it.
        """
        assert "B6" in CRITERION5_SYSTEMS
        assert system_for("B6", seed=SEED).name == "B6"

    def test_a28_the_arm_is_built_at_the_seed_it_was_handed(self) -> None:
        """The third joint, which the two above do not close.

        ``test_a28_the_runner_hands_the_arm_the_replicate_seed`` stops at
        ``system_for``'s keyword and asserts nothing about what happens to it;
        ``test_a28_b6_without_a_seed_is_refused`` arrives one line later and
        only pins that *some* seed is required. Between them sits
        ``system_for``'s own construction --
        ``UniformProposer(AGENT_GRAMMAR, seed)`` -- and nothing reads
        ``UniformProposer.seed``, whose docstring says it is published so "a
        recorded B6 reading is only reproducible if the seed behind it can be
        read back off the arm that produced it".

        So ``system_for`` could pass ``Seed(0)`` and every test in this module
        stays green while all twenty replicates draw one sequence -- the exact
        failure the refusal one line below exists to prevent, arriving through
        the door the refusal leaves open.
        """
        seed = _first_replicate_seed(OUT_OF_LIBRARY)
        source = _b6_source(seed)
        assert source.seed == seed
        # Non-vacuous only if a different seed reaches a different arm: an
        # implementation ignoring the argument would satisfy the line above if
        # `seed` happened to equal whatever it substituted.
        assert _b6_source(Seed(int(seed) + 1)).seed != source.seed

    def test_a28_b6_without_a_seed_is_refused(self) -> None:
        """An unseeded B6 raises rather than picking a seed for itself.

        A default would make the arm's draws a function of whatever the default
        was, identically across all twenty replicates, and nothing downstream
        would say so.

        Matched on the message because ``SystemConfigurationError`` is the
        wrong-arm error, the no-provider error and ``uniform_draft``'s two
        argument errors as well. A bare ``pytest.raises`` here stays green if
        B6 falls out of ``CRITERION5_SYSTEMS`` entirely and reaches the
        unknown-arm branch -- the arm ceasing to exist, read as the arm
        refusing correctly.
        """
        with pytest.raises(SystemConfigurationError, match="was offered no seed"):
            system_for("B6")

    def test_a28_b6_is_built_fresh_for_each_replicate(self) -> None:
        """Two calls return two objects that agree, so a seed can be carried.

        B5 is deliberately shared across every cell -- it holds no per-run state.
        B6 does: the seed it draws from and the counter that indexes the draw. A
        cached B6 would answer replicate 07 with replicate 00's second proposal,
        so both halves matter: distinct objects, and identical behaviour at one
        seed.
        """
        assert system_for("B6", seed=SEED) is not system_for("B6", seed=SEED)
        assert _proposals(SEED, 2) == _proposals(SEED, 2)

    def test_a28_b6_does_not_join_spec_nine_s_matrix(self) -> None:
        """§9's preregistered table is unchanged by this item.

        ``environments/pointproc/matrix.py`` says "Do not add an arm" beside
        ``SPEC9_CELLS``, and means it: §9's 56 cells are what the recorded
        campaign was addressed under. Criterion 5's comparator is a separate
        preregistered cell set, so a report of §9 and a report of criterion 5
        cannot be confused for one another.
        """
        assert "B6" not in MATRIX_SYSTEMS
        assert "B6" not in {cell.system for cell in SPEC9_CELLS}
        assert len(SPEC9_CELLS) == 56
        assert {cell.system for cell in CRITERION5_CELLS} == set(CRITERION5_SYSTEMS)

    def test_a28_criterion_five_names_the_scenario_it_is_measured_on(self) -> None:
        """The comparator's cell is S11 at §9's replicate count.

        Criterion 5 is about "an S11 extension", and a non-overlapping 95%
        interval needs the same twenty seeds every other cell gets -- a
        comparator run at a different replicate count would not be comparable
        with the V7 cell it deflates.
        """
        recorded = [
            (cell.system, str(cell.scenario), cell.replicates)
            for cell in CRITERION5_CELLS
        ]
        assert recorded == [("B6", OUT_OF_LIBRARY, REPLICATES)]

    def test_a28_b6_proposes_only_when_the_gate_opens(self) -> None:
        """On an in-library scenario the check passes and B6 proposes nothing.

        V7's behaviour, inherited. SPEC F6 makes Stage B conditional on Stage A
        detection, and a comparator that proposed regardless would be scored on
        runs where the gate never fired -- which is the confound §9's contrast
        conditions on.
        """
        assert _b6_run(IN_LIBRARY).attempts == ()

    def test_a28_no_draw_is_malformed_or_refused(self) -> None:
        """On S11 the gate opens, B6 draws, and every outcome is a draw's.

        The run this item exists to make possible, and the assertion that
        separates a working comparator from the review's second variant: a
        uniform draw over the grammar's own menu cannot be malformed, and a
        refusal cannot be a provider declining. Either outcome appearing means
        nothing was drawn -- which ``Hybrid`` reports as an attempt and carries
        on from, so it is invisible unless something looks. The two are
        asserted separately because a refusal has a second cause B6 can reach
        and malformedness does not; see ``DRAWABLE_OUTCOMES``.

        What is *not* asserted is that a draw was admitted. Measurability is
        stochastic -- ``BeamSearch`` says so at ``_UNSCORABLE`` -- so an
        unmeasurable structure is a legitimate outcome for one seed, and a gate
        that forbade it would be pinning the environment rather than the arm.
        """
        run = _b6_run(OUT_OF_LIBRARY)
        assert run.attempts, "the Stage A gate did not open on S11, so B6 never drew"
        outcomes = [attempt.outcome for attempt in run.attempts]
        # Split from the set comparison below so the message names the cause.
        # "refused" is reachable for B6 through `BudgetExhaustedError`, which is
        # a budget fact and not a drawing fact; reporting it as "the proposer is
        # not drawing" would send the next reader to `uniform.py` for a bug that
        # is not there. See DRAWABLE_OUTCOMES.
        refused = [a for a in run.attempts if a.outcome == "refused"]
        assert not refused, (
            f"B6 was refused ({[a.detail for a in refused]}). With no provider "
            f"behind a draw this is `BudgetExhaustedError`, not a declining "
            f"provider -- a budget fact. `Hybrid._extend` stops proposing at "
            f"the first one, so the arm ran on less than its share"
        )
        assert set(outcomes) <= DRAWABLE_OUTCOMES, (
            f"{outcomes} contains an outcome no draw can have; the proposer is "
            f"not drawing"
        )
        for node_id, defect in run.proposed.items():
            AGENT_GRAMMAR.validate_defect(defect)
            assert _is_on_grid(AGENT_GRAMMAR, defect), f"{node_id} is off-grid"

    def test_a28_the_library_is_the_one_every_arm_entertains(self) -> None:
        """B6 opens with V1's closed set, like every other arm.

        SPEC §5's comparison is between what systems *do*, not between what they
        were handed, so a comparator entertaining a different library would be
        measuring the library. ``null`` is excluded because
        :func:`~sciagent.systems.base.null_seeded_graph` has already proposed it
        and :func:`~sciagent.systems.base.entertain` skips a duplicate.
        """
        entertained = {str(node) for node in _b6_run(IN_LIBRARY).proposed}
        assert set(closed_set()) - {"null"} <= entertained


class TestA28TheDrawIsUniform:
    """The draw's distribution, which is the half a corner form would fail.

    The backlog entry's two forms differ only here, and the earlier version of
    this module pinned the difference with two guards that a draw confined to
    indices 0 and 1 satisfied -- 16 grids of 64 points, so "more distinct
    indices than grids" needed 17 and the two-point draw supplied 32. These are
    the distribution's own statistics instead, normalised by grid size so they
    state a property of the draw rather than of this environment.

    The draw is seeded, so none of this is flaky in the usual sense: each is a
    fixed computation whose value is checked, and it either passes or it does not.
    What the margins buy is that a *change* to ``SEED``, ``SAMPLE`` or the menu
    would not flip one by luck. Those margins are not uniform, and an earlier
    version of this docstring claimed "tens of standard errors" for all of them,
    which `/code-review` showed was false for two:

    * centred-on-grid: about 7-9 SE.
    * middle-half share: about 24 SE.
    * grid coverage: every one of 64 points expected ~200 times; missing any is
      beyond floating-point-rare.
    * every-cell-reachable: multinomial at n=4096, p=1/5 per cell, so the
      per-cell sd is about 28 and the 25%-of-expected threshold is about 7 SE.

    None is tight, but "tens" was an overstatement and the number is the whole
    justification for the threshold, so it is stated per test rather than
    asserted collectively.
    """

    def test_a28_the_draw_reaches_both_ends_of_every_grid(self) -> None:
        """The extremes are reachable **in each grid**, not merely somewhere.

        Per grid position, because the earlier version pooled every index into
        one list and asserted the global ``min`` and ``max`` -- which a proposer
        drawing only index 0 on one grid and only the top index on another
        satisfies, while the test's own name claims otherwise. `/code-review`
        found it.
        """
        for position, values in _per_grid(SEED, SAMPLE).items():
            assert min(values) < 0.02, f"{position} never drew near its low end"
            assert max(values) > 0.98, f"{position} never drew near its high end"

    def test_a28_the_draw_is_centred_on_its_grid(self) -> None:
        """The mean normalised index is a half.

        Uniform on ``{0..n-1}`` has mean ``0.5`` after normalisation, with a
        standard error here below 0.005, so 0.03 is a threshold no uniform draw
        fails and no low-corner draw passes -- the two-point draw the review
        executed sits at 0.008.
        """
        scaled = _normalised(SEED, SAMPLE)
        assert abs(math.fsum(scaled) / len(scaled) - 0.5) < 0.03

    def test_a28_half_the_draws_land_in_the_middle_half(self) -> None:
        """The mass is spread rather than piled at one end.

        A distributional statement the mean alone does not make: a draw
        alternating between the two extremes has mean 0.5 and lands nothing in
        the middle.
        """
        scaled = _normalised(SEED, SAMPLE)
        middle = sum(1 for value in scaled if 0.25 <= value < 0.75)
        assert 0.4 < middle / len(scaled) < 0.6

    def test_a28_most_of_every_grid_is_actually_drawn(self) -> None:
        """Nearly every point of **each** grid appears, not a handful.

        The count the earlier review's two-point variant failed by two orders of
        magnitude. Stated per grid position against that position's own size,
        because the pooled form `/code-review` found was load-bearing only by the
        accident that all sixteen grids here hold 64 points: pooling
        ``index / (size - 1)`` across heterogeneous sizes gains distinct values
        from every size present, so the threshold could be met with no single
        grid well covered -- the reverse of what normalising was said to buy.
        """
        for position, values in _per_grid(SEED, SAMPLE).items():
            distinct = len({round(value, 9) for value in values})
            expected = position[2]
            assert distinct >= 0.9 * expected, (
                f"{position} drew {distinct} distinct points of {expected} over "
                f"{len(values)} draws"
            )

    def test_a28_every_structural_cell_is_reachable(self) -> None:
        """The cell is drawn uniformly too, not only the parameters within it.

        A proposer that only ever named structure 0 would be licensed, on-grid
        and seeded, and would not be a uniform structured generator. Multinomial
        at ``n = SAMPLE``, ``p = 1/5``: per-cell sd about 28, and the threshold
        below is a quarter of the expected count, about 7 SE.
        """
        menu = _menu()
        counts = [0] * len(menu)
        for structure, _ in _drafts(SEED, SAMPLE):
            counts[structure] += 1
        assert all(counts), f"{counts} leaves a structural cell undrawn"
        expected = SAMPLE / len(menu)
        assert max(abs(count - expected) for count in counts) < 0.25 * expected


class TestA28TheDrawItself:
    """Properties of ``uniform_draft`` alone, with no system around it."""

    def test_a28_a_draw_names_exactly_one_edit(self) -> None:
        """One edit per proposal, matching B5's configured search depth.

        The matrix runs B5 at ``levels=1``, so a comparator drawing compound
        defects would be searching a space no conventional arm searches, and a
        uniform draw over pairs is mostly invalid rather than mostly informative.
        """
        menu = _menu()
        for index in range(DRAWS):
            assert len(uniform_draft(menu, SEED, index).edits) == 1

    def test_a28_the_parameter_count_matches_the_cell(self) -> None:
        """Every draw supplies exactly the grid indices its cell takes.

        ``decode`` refuses anything else, so this is what stands between a draw
        and a ``MalformedProposalError`` reported as a scientific outcome.
        """
        menu = _menu()
        for structure, parameters in _drafts(SEED, SAMPLE):
            assert len(parameters) == menu[structure].arity

    def test_a28_every_grid_index_is_inside_its_grid(self) -> None:
        """No index falls outside the grid it names.

        The failure mode an ``int(u * size)`` draw has: ``u`` lies in ``[0, 1)``
        so the product lies in ``[0, size)``, but a float rounding at the top of
        the range would name ``size`` and ``decode`` would refuse it.
        """
        menu = _menu()
        for structure, parameters in _drafts(SEED, SAMPLE):
            for grid, index in zip(menu[structure].grids, parameters, strict=True):
                assert 0 <= index < grid.size

    def test_a28_the_draft_decodes_against_the_menu_it_was_drawn_from(self) -> None:
        """Every draft denotes a defect, which is the licensing clause's floor."""
        for defect in _decoded(SEED):
            AGENT_GRAMMAR.validate_defect(defect)
            assert _is_on_grid(AGENT_GRAMMAR, defect)

    def test_a28_a_negative_seed_is_refused(self) -> None:
        """The draw goes through the framework's own seed derivation.

        Named here so the refusal is a property of B6 rather than an accident of
        which helper it happened to call.
        """
        with pytest.raises(DeterminismError):
            uniform_draft(_menu(), Seed(-1), 0)

    def test_a28_a_negative_index_is_refused(self) -> None:
        """A call index below zero is a caller fault, not a stream to derive."""
        with pytest.raises(SystemConfigurationError):
            uniform_draft(_menu(), SEED, -1)

    def test_a28_an_empty_menu_is_refused(self) -> None:
        """A menu with no cell has nothing to draw from, and says so.

        Reachable only through a grammar licensing no structure, which is a
        configuration fault. A proposer that returned an empty draft instead
        would have it refused by ``decode`` two frames later, naming the draft
        rather than the menu that could not supply one.
        """
        with pytest.raises(SystemConfigurationError):
            uniform_draft((), SEED, 0)


@cache
def _run_matrix_cli() -> ModuleType:
    """Load ``scripts/run_matrix.py`` as a module, for its guards.

    By path, because ``scripts/`` is not a package and pytest's prepend mode puts
    only ``tests/`` on the path. ``tests/test_report.py`` reaches its script
    through a subprocess instead; that is right for asserting an *exit status*,
    and wrong here, where what is under test is one function's refusal and
    driving it through the CLI would need a populated campaign.
    """
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "run_matrix_cli", root / "scripts" / "run_matrix.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ledger_holding(path: Path, system: str) -> None:
    """Write one ledger row recorded under ``system``, and nothing else.

    The key is built directly rather than through
    :func:`~sciagent.eval.matrix.cell_key`, because the guard reads
    ``config["system"]`` and no other term, and a real cell key would drag a
    battery and a scenario in to say the same thing.
    """
    with CampaignLedger.open(path) as ledger:
        ledger.append(
            ExperimentKey(
                env_version=EnvVersion("pointproc/1.0.0"),
                config=FrozenDict[str, str]({"system": system}),
                data_version=DataVersion("slice/1"),
                metric_version=MetricVersion("1.2.0"),
                seed=Seed(1),
            ),
            reading={"d1": 0.0},
        )


class TestA28TheTwoCellSetsStayInTwoLedgers:
    """The separation §9 and criterion 5 need, downstream of cell selection.

    Declaring two cell *sets* keeps them apart when cells are chosen, and stops
    there. Every cell of either set is addressed under the same
    ``CampaignAddress``, and :func:`~sciagent.eval.report.summarise` groups
    whatever rows an address matches -- so a B6 row written into §9's ledger
    comes back out of ``report_matrix.py`` as a §9 cell, indistinguishable from a
    preregistered arm. That is the ambiguity the separate declaration exists to
    prevent, arriving one layer further down than it was prevented. Found by
    ``/code-review`` during A28's preflight, not by a test.
    """

    def test_a28_criterion_five_is_refused_into_a_section_nine_ledger(
        self, tmp_path: Path
    ) -> None:
        """B6 will not be recorded where §9's arms already are."""
        ledger = tmp_path / "spec9.db"
        _ledger_holding(ledger, "V1")
        with pytest.raises(ValueError, match="record criterion 5's comparator"):
            _run_matrix_cli()._refuse_mixed_ledger(
                [Cell("B6", ScenarioId(OUT_OF_LIBRARY), 1)], ledger
            )

    def test_a28_section_nine_is_refused_into_a_criterion_five_ledger(
        self, tmp_path: Path
    ) -> None:
        """And the converse, so the guard is not one-directional."""
        ledger = tmp_path / "criterion5.db"
        _ledger_holding(ledger, "B6")
        with pytest.raises(ValueError, match="record section 9's matrix"):
            _run_matrix_cli()._refuse_mixed_ledger(
                [Cell("V1", ScenarioId(OUT_OF_LIBRARY), 1)], ledger
            )

    def test_a28_each_cell_set_is_admitted_into_its_own_ledger(
        self, tmp_path: Path
    ) -> None:
        """Neither direction refuses the case it exists to allow.

        The half a guard is most likely to get wrong, and the one that would make
        the campaign unrunnable rather than merely ambiguous.
        """
        spec9 = tmp_path / "spec9.db"
        _ledger_holding(spec9, "V1")
        _run_matrix_cli()._refuse_mixed_ledger(
            [Cell("B4", ScenarioId(OUT_OF_LIBRARY), 1)], spec9
        )
        criterion5 = tmp_path / "criterion5.db"
        _ledger_holding(criterion5, "B6")
        _run_matrix_cli()._refuse_mixed_ledger(
            [Cell("B6", ScenarioId(OUT_OF_LIBRARY), 1)], criterion5
        )

    @pytest.mark.parametrize("spec9_arm", ["V1", "B4"])
    def test_a28_one_invocation_may_not_ask_for_both_cell_sets(
        self, tmp_path: Path, spec9_arm: str
    ) -> None:
        """Two invocations against one path are not the only way to mix them.

        The guard reads what the ledger *already holds*, and a single
        invocation asking for both sets never becomes something it already
        holds -- so ``--systems V1,B6`` against a fresh path selects both cell
        sets and is admitted, writing §9 and criterion-5 rows under one
        ``CampaignAddress``. That is the state the function exists to prevent,
        reached by the one route it does not read.

        Every other test in this class passes a single-element ``cells`` list,
        and ``test_a28_a_ledger_that_does_not_exist_yet_is_admitted`` pins the
        empty-``recorded`` fall-through that hides it.

        Parametrised over the §9 arm because every other test in this class
        names V1, and a guard spelled ``"V1" in wanted and wanted &
        CRITERION5_SYSTEMS`` passes all of them while ``--systems B4,B6`` still
        mixes.
        """
        with pytest.raises(ValueError, match="one invocation"):
            _run_matrix_cli()._refuse_mixed_ledger(
                [
                    Cell(spec9_arm, ScenarioId(OUT_OF_LIBRARY), 1),
                    Cell("B6", ScenarioId(OUT_OF_LIBRARY), 1),
                ],
                tmp_path / "absent.db",
            )

    def test_a28_a_multi_arm_section_nine_invocation_is_still_admitted(
        self, tmp_path: Path
    ) -> None:
        """The refusal is about the two *sets*, not about breadth.

        A guard spelled ``len(wanted) > 1`` refuses the mixed case and passes
        every other test in this class -- while making the §9 campaign
        unrunnable, since ``--systems`` defaults to all seven arms. Nothing
        here exercised a multi-element list until this test, so nothing
        distinguished the two.
        """
        _run_matrix_cli()._refuse_mixed_ledger(
            [
                Cell("V1", ScenarioId(OUT_OF_LIBRARY), 1),
                Cell("B4", ScenarioId(OUT_OF_LIBRARY), 1),
                Cell("B5", ScenarioId(OUT_OF_LIBRARY), 1),
            ],
            tmp_path / "absent.db",
        )

    def test_a28_the_cli_consults_the_guard_before_it_records(
        self, tmp_path: Path
    ) -> None:
        """A correct helper is worth nothing if ``main`` stops calling it.

        ``scripts/run_matrix.py`` holds the only call site, and no test in this
        repository drives ``main`` -- so deleting that one line leaves this
        class green while ``--systems V1,B6`` records both sets under one
        ``CampaignAddress``. The logic and the wiring are separate failures and
        need separate tests.

        The guard is replaced with one that always refuses, so this pins the
        wiring alone and nothing is simulated whatever the real guard does.
        """
        cli = _run_matrix_cli()
        ledger = tmp_path / "mixed.db"
        seen: list[tuple[str, ...]] = []

        def always_refuse(cells: list[Cell], path: Path) -> None:
            seen.append(tuple(sorted(cell.system for cell in cells)))
            raise ValueError("refusing in one invocation, for this test")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cli, "_refuse_mixed_ledger", always_refuse)
            code = cli.main(
                [
                    str(ledger),
                    "--systems",
                    "V1,B6",
                    "--scenarios",
                    OUT_OF_LIBRARY,
                    "--replicates",
                    "1",
                ]
            )
        assert seen == [("B6", "V1")], "main did not hand the guard both arms"
        assert code == 2
        assert not ledger.exists(), "the ledger was opened before the guard ran"

    def test_a28_a_ledger_that_does_not_exist_yet_is_admitted(
        self, tmp_path: Path
    ) -> None:
        """A first run has nothing to conflict with, and must not be refused."""
        _run_matrix_cli()._refuse_mixed_ledger(
            [Cell("B6", ScenarioId(OUT_OF_LIBRARY), 1)], tmp_path / "absent.db"
        )
