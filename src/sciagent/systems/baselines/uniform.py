"""B6: uniform structured generation, the comparator SPEC §12 criterion 5 names.

Criterion 5 asks whether V7 proposes an S11 extension exceeding "B6-equivalent
random structured generation" on D3. It had no comparator: SPEC §5 defers B6 to
the full benchmark, and ``docs/DECISIONS.md`` (2026-08-04) records the criterion
as unmeasured because *"no B6 exists"*. This is that arm's proposal source.

The whole design is one sentence
--------------------------------

B6 is :class:`~sciagent.systems.hybrid.Hybrid` -- V7's class -- holding this
instead of an LLM proposal layer. Nothing here reimplements the loop, the Stage
A gate or the budget split, so "drawn at the same gate under the same budget
split as V7" is a fact about construction rather than a claim to audit. It is
the argument :func:`~sciagent.systems.ablation.memory_ablation` already makes
for V3 and V4.

What that leaves as the difference between the two arms is *how a point in the
action space is chosen* -- a model, or a uniform draw -- which is what criterion
5 asks about, and is the only thing this module decides.

Why the action space is the model's menu
----------------------------------------

The draw is over :func:`~sciagent.systems.llm.encoding.structural_menu`'s cells
and their parameter grids -- the same menu a model is offered, and the same
:func:`~sciagent.systems.llm.encoding.decode` that turns its answer into a
structure. That import direction looks wrong for a conventional baseline and is
deliberate: the menu is the *action space*, not an LLM artefact, and a
comparator drawing from anywhere else would differ from V7 in two ways rather
than one. Nothing imported here reaches a provider, a transcript or a network.

Uniform over the grid, not over the grid's corners
--------------------------------------------------

``docs/BACKLOG.md`` offered two forms, and this is the second: the cell
uniformly, then each of that cell's grid indices uniformly. The corner form --
uniform over ``enumerate_edits(1)`` -- is cheaper, because its 48 structures'
table rows amortise across a campaign. It was rejected because a grid box's
corners are where the degenerate parameterisations live;
:class:`~sciagent.systems.baselines.beam_search.BeamSearch` says so at
``_UNSCORABLE``, having found them by enumerating exactly those corners. A
comparator confined to them would lose for a reason unrelated to random
generation, and a deflator that is too easy to beat flatters the arm it is
supposed to deflate.

The cost that buys is bounded and stated: a structure the engine table has
never seen costs one full simulated row, and B6's cell is twenty replicates of
at most two proposals, so at most forty.

Determinism
-----------

:func:`uniform_draft` is a pure function of ``(menu, seed, index)``. It holds no
state, reads no clock and touches no investigation, and its stream comes from
:func:`~sciagent.core.program.derive_generator` -- seeded by name, so the draw
at index 7 does not depend on whether indices 0 to 6 were ever taken.
:class:`UniformProposer` is that function plus a per-instance call counter.

The counter is per instance and not per module for the reason
:class:`~sciagent.systems.llm.provider.ProposalLayer`'s is: a campaign builds
one arm per replicate, and a shared counter would make replicate 07's first
proposal depend on how many replicates had already run in the process.

Only ``rng.random()`` is used, never a distribution method. NumPy guarantees
stream stability for ``Generator.random`` and not for
``Generator.integers``, so an index drawn through the latter would make SPEC
§6.1 A1 hostage to a numpy upgrade -- the same reason
``environments.pointproc.components`` derives every variate by inverse CDF.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from sciagent.core.edits import EditGrammar
from sciagent.core.errors import (
    GrammarError,
    MalformedProposalError,
    SystemConfigurationError,
)
from sciagent.core.program import derive_generator
from sciagent.core.types import Seed
from sciagent.systems.base import Investigation
from sciagent.systems.llm.encoding import (
    EditDraft,
    MenuEntry,
    ProposalDraft,
    decode,
    structural_menu,
)
from sciagent.systems.llm.provider import Proposal

__all__ = ["UniformProposer", "uniform_draft"]

#: The stream name every draw is derived under. Part of
#: :func:`~sciagent.core.program.derive_generator`'s ``spawn_key``, so changing
#: it changes every draw B6 has ever made -- which is why it is a constant with
#: a version in it rather than a literal spelled at the call site.
_STREAM = "uniform-proposer/1"


def uniform_draft(menu: Sequence[MenuEntry], seed: Seed, index: int) -> ProposalDraft:
    """Return the draft drawn at call ``index`` under ``seed``.

    Guarantees the result is a pure function of its three arguments: the same
    triple yields the same draft in any process, on any platform, under any
    ``PYTHONHASHSEED``, and independently of which other indices have been
    drawn. That is what makes B6's proposal sequence a pure function of
    ``(scenario seed, replicate index)`` once the seed is
    :attr:`~sciagent.eval.matrix.CellTask.seed`, which
    :func:`~sciagent.eval.matrix.replicate_seeds` already guarantees is one.

    Guarantees also that the draft names exactly one edit, that its structure is
    a cell of ``menu``, and that every parameter is a valid index into that
    cell's grid -- so :func:`~sciagent.systems.llm.encoding.decode` cannot refuse
    it and a draw is never reported as a malformed proposal.

    One edit, not several. The matrix runs B5 at ``levels=1``, so a comparator
    drawing compound defects would search a space no conventional arm searches;
    and a uniform draw over pairs is mostly *invalid* rather than mostly
    informative, since two edits on one target do not compose.

    Raises :class:`~sciagent.core.errors.SystemConfigurationError` on an empty
    menu or a negative index, and
    :class:`~sciagent.core.errors.DeterminismError` through
    :func:`~sciagent.core.program.derive_generator` on a negative seed.
    """
    if not menu:
        raise SystemConfigurationError(
            "a uniform proposer was offered a menu with no structural cell, so "
            "there is nothing to draw from; a grammar licensing no structure "
            "cannot supply a comparator"
        )
    if index < 0:
        raise SystemConfigurationError(
            f"a uniform proposer was asked for draw {index}; a call index is a "
            f"position in a sequence and there is no stream before the first"
        )
    generator = derive_generator(seed, f"{_STREAM}/{index}")
    entry = menu[_uniform_index(generator, len(menu))]
    parameters = tuple(_uniform_index(generator, grid.size) for grid in entry.grids)
    return ProposalDraft(
        edits=(EditDraft(structure=entry.index, parameters=parameters),),
        name=f"uniform/{entry.index}/{index}",
        rationale=(
            f"uniform draw {index} over the {len(menu)}-cell structural menu; "
            f"no observation was consulted"
        ),
    )


def _uniform_index(generator: np.random.Generator, size: int) -> int:
    """Return a uniform draw from ``range(size)``, through ``random()`` alone.

    ``Generator.random`` returns a double in ``[0, 1)``, so ``u * size`` lies in
    ``[0, size)`` and truncation lands in ``range(size)``.

    **The ``min`` never fires, and saying why is the point of it.** An earlier
    version of this docstring claimed the multiplication could round ``u`` just
    below one up to exactly ``size``. A28's preflight checked that and it is
    false: the largest double below one is ``1 - 2**-53``, and
    ``int((1 - 2**-53) * size) < size`` for every ``size`` from 2 to 300000 --
    at ``size = 3`` the product is ``2.9999999999999996``, not ``3.0``. The
    clamp is unreachable for any input this function can be given. It stays
    because it costs one comparison on a determinism-critical primitive and
    turns any future floating-point surprise into a clamped index rather than a
    ``MalformedProposalError`` raised two frames away inside ``decode``, which
    would name the draft rather than the arithmetic. A guard that provably
    cannot fire earns its place only by admitting so.

    Drawn this way rather than through ``Generator.integers`` because numpy's
    versioning policy pins the bit stream of ``random`` and not of the
    distribution methods, and a comparator whose draws moved under a numpy
    upgrade would silently invalidate every recorded B6 reading.

    The truncation is not perfectly uniform in general -- a double carries 53
    bits and a grid at most a few hundred points, so the departure is of order
    ``2**-45`` and is *deterministic* rather than a source of drift. On this
    environment it is not even that: every grid holds 64 points and the menu 5
    cells, and for a power of two the multiplication is exact, so the draw is
    perfectly uniform. Stated rather than hidden, because "uniform" is this
    arm's whole definition.

    Raises :class:`~sciagent.core.errors.SystemConfigurationError` for a range
    below one. Unreachable through the menu --
    :class:`~sciagent.core.edits.ParameterGrid` refuses fewer than two points
    and :func:`uniform_draft` refuses an empty menu -- but this is the
    primitive every B6 draw goes through, and without it ``size == 0`` returns
    ``-1``, which is not a draw from ``range(0)`` and would surface far away as
    a malformed draft.
    """
    if size < 1:
        raise SystemConfigurationError(
            f"a uniform draw was asked for a range of {size}; there is no "
            f"value to draw, and returning one anyway is how an impossible "
            f"state becomes a plausible-looking index"
        )
    return min(int(generator.random() * size), size - 1)


class UniformProposer:
    """A proposal source that draws structure at random, and reads nothing.

    Guarantees every proposal it returns is licensed by the grammar it was built
    with and on that grammar's parameter grids, that the sequence of proposals is
    a pure function of ``(seed, call index)``, and that no investigation state is
    read or written -- so B6 cannot be selecting a structure that fits the
    evidence, which is what would stop it being random structured generation.

    Satisfies :class:`~sciagent.systems.hybrid.ProposalSource`, which is what
    lets :class:`~sciagent.systems.hybrid.Hybrid` hold it in place of an LLM
    proposal layer.
    """

    __slots__ = ("_calls", "_grammar", "_menu", "_seed")

    def __init__(self, grammar: EditGrammar, seed: Seed) -> None:
        self._grammar = grammar
        self._menu = structural_menu(grammar)
        self._seed = seed
        self._calls = 0

    @property
    def menu(self) -> tuple[MenuEntry, ...]:
        """Return the structural menu proposals are drawn from."""
        return self._menu

    @property
    def seed(self) -> Seed:
        """Return the seed every draw is derived under.

        The replicate seed, where a campaign built this. Published because a
        recorded B6 reading is only reproducible if the seed behind it can be
        read back off the arm that produced it.
        """
        return self._seed

    @property
    def calls(self) -> int:
        """Return how many proposals have been drawn.

        The index of the *next* draw, and per instance. See this module's
        docstring on why it is not per module.
        """
        return self._calls

    def propose(self, investigation: Investigation) -> Proposal:
        """Return one proposal, drawn uniformly and independently of the run.

        ``investigation`` is accepted and deliberately unread. The signature is
        :class:`~sciagent.systems.hybrid.ProposalSource`'s, so that ``Hybrid``
        calls this and an LLM layer identically; ignoring the argument is what
        makes the draw random rather than responsive, and
        ``test_a28_the_proposer_leaves_the_investigation_alone`` asserts that the
        object comes back untouched.

        Does not raise :class:`~sciagent.core.errors.MalformedProposalError` in
        practice: the draft is drawn from the grammar's own menu, so it decodes,
        and one edit cannot trip the two-edits-on-one-target check. That matters
        because ``Hybrid`` records a malformed draft as an outcome and carries
        on, which would make an arm that never lands a structure
        indistinguishable, from the run, from one that does.

        The validation is written anyway, converting a
        :class:`~sciagent.core.errors.GrammarError` into a
        ``MalformedProposalError`` exactly as
        :meth:`~sciagent.systems.llm.provider.ProposalLayer._build` does. Not
        defensive clutter: ``Hybrid._propose_once`` catches
        ``MalformedProposalError`` and **not** ``GrammarError``, so one that
        escapes stops the campaign instead of costing a proposal -- a stoppage
        this repository has already paid for once, recorded at that method.
        Today neither error is reachable here; the day ``uniform_draft`` emits
        two edits, B6 fails the way V7 does rather than by traceback.
        """
        draft = uniform_draft(self._menu, self._seed, self._calls)
        self._calls += 1
        program_edit = decode(self._grammar, self._menu, draft)
        try:
            self._grammar.validate_defect(program_edit)
        except GrammarError as error:
            raise MalformedProposalError(f"{type(error).__name__}: {error}") from error
        return Proposal(
            program_edit=program_edit,
            name=draft.name,
            rationale=draft.rationale,
            # Not a transcript address, and shaped so it cannot be mistaken for
            # one: `call_address` returns a bare `stable_key` digest. A uniform
            # draw has no recorded call behind it, and `None` would lose the one
            # thing worth tracing -- which seed and which index produced this.
            address=f"uniform:{int(self._seed)}:{self._calls - 1}",
        )
