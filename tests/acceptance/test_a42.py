"""Acceptance test A42: D4 names the set it improved on.

A42 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"D4 now rewards entertaining fewer alternatives"*,
and reads:

    ``test_a42_d4_names_the_set_it_improved_on`` -- the payload carries the size
    of the comparison set beside D4, and a candidate scoring 0.0 because the set
    was empty is distinguishable from one scoring 0.0 because it was
    outperformed.

Like A26, this grades the instrument rather than any system. What it establishes
is that a reported D4 can be read at all: the figure is a comparison, and a
comparison whose second term is unstated is not a measurement.

What is wrong
-------------

A26 excluded the candidate's own structure from the set D4 compares against,
which is what made the dimension carry information. It also made the figure a
function of *how many alternatives the system entertained*. With the candidate
excluded, ``best`` is a max over the other entertained structures, so
entertaining one more can only raise ``best`` and therefore only lower D4. A
system with a rich library is penalised against one entertaining a single weak
alternative, for a difference in breadth rather than in explanatory power.

The reading is not being changed here. ``docs/BACKLOG.md``'s entry offers the
fork -- the system's own entertained set, or a fixed per-scenario reference set
-- and the first was taken, so what D4 *means* is exactly what A26 implemented.
What is added is the second term, because a figure whose comparison set is
unknown cannot be compared across arms that entertained different numbers of
things.

The collision this closes
-------------------------

:func:`~sciagent.eval.scoring._explanatory_coverage` returns ``0.0`` down two
paths that mean opposite things:

* the exclusion emptied the set, so there was no alternative to improve on;
* the set was not empty and the candidate was outperformed or tied on every
  recorded experiment.

The first is the state A26's own gate pins in
``test_a26_d4_is_zero_when_the_candidate_is_the_only_hypothesis``, and the second
in ``test_a26_d4_is_zero_when_the_candidate_adds_nothing``. Both read ``0.0``,
and in the ledger both are indistinguishable from each other *and* from the
identically-zero bug A26 fixed -- which is what makes this a reporting defect
rather than a cosmetic one. ``n_comparison`` separates them: ``0`` is the empty
set, and any positive value with ``d4 == 0.0`` is a candidate that rescued
nothing from a set that existed.

Why the count is of the set and not of the scoring
--------------------------------------------------

The size is reported whether or not any observation was scored. It is a fact
about the entertained structures, and a run with no recorded experiments has an
unexamined comparison set rather than an absent one -- the same distinction
:attr:`~sciagent.eval.scoring.DimensionVector.n_held_out` draws for D2 and D3,
where zero means the battery was empty rather than that the question was never
put.

The cases are constructed exactly as A26's are, on the suite's gate table and
the slice's own library, so nothing is simulated. See ``test_a26.py`` for why
the design and the cell are named rather than searched for.
"""

from __future__ import annotations

from slice_tables import GRAMMAR, gate_table

from environments.pointproc.outcomes import closed_set, simulator, slice_designs
from sciagent.core.edits import Defect
from sciagent.core.types import ExperimentId, HypothesisId, Probability, ScenarioId
from sciagent.eval.agency import AgencyMetrics
from sciagent.eval.campaign import Adjudication
from sciagent.eval.matrix import CellReading, battery_key
from sciagent.eval.scoring import (
    DIMENSION_VERSION,
    ClosedWorldScore,
    DimensionVector,
    dimension_vector,
)
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.inference.empirical import EmpiricalTable
from sciagent.inference.interface import Observation

#: The design that separates the library, named rather than searched for --
#: ``test_a26.py`` gives the reason at length.
_DESIGN_ID = "query:phase_conditioned_dispersion"

#: The cell where ``poisson_mixture`` explains and ``hawkes`` does not.
_RESCUED_CELL = 12

#: Stands for a structure the table holds no row for, wherever ``_vector``'s
#: callers name library members. Resolved by :func:`_unheld_structure`.
_UNHELD = "unheld"

_TABLE: list[EmpiricalTable] = []


def _table() -> EmpiricalTable:
    if not _TABLE:
        _TABLE.append(gate_table())
    return _TABLE[0]


def _unheld_structure() -> Defect:
    """Return one single-edit structure the table holds no row for.

    Found by scanning the grammar's own enumeration and taking the first the
    table lacks -- rather than naming one, because which structures the gate
    table happens to hold is a property of that fixture and not of the grammar.
    :meth:`~sciagent.core.edits.EditGrammar.enumerate_edits` promises
    exhaustiveness rather than an order in so many words, but its order is fixed:
    it iterates ``EDIT_TYPES``, a module constant, then a
    :class:`~sciagent.core.types.FrozenDict` whose keys are sorted at
    construction, then literal tuples, so the scan returns the same structure in
    every process and under every ``PYTHONHASHSEED``.

    Raises rather than returning a held structure if the scan finds nothing, so a
    table that grew to cover the whole edit space fails loudly instead of quietly
    turning the case that needs this into another library case. That is the
    failure mode worth guarding: the case would still pass, and would have
    stopped testing anything.
    """
    table = _table()
    for edit in GRAMMAR.enumerate_edits():
        structure = frozenset({edit})
        if not table.holds(structure):
            return structure
    raise AssertionError("the gate table holds every single-edit structure")


def _structure(name: str) -> Defect:
    """Return the library member ``name``, or the one structure the table lacks."""
    if name == _UNHELD:
        return _unheld_structure()
    return closed_set()[name]


def _design() -> ExperimentDesign:
    return next(design for design in slice_designs() if str(design.id) == _DESIGN_ID)


def _value_in_cell(cell: int) -> float:
    """Return a value landing in ``cell`` of the design's one-dimensional space."""
    axis = _design().outcome.axes[0]
    if cell == 0:
        return (axis.low + axis.interior[0]) / 2
    return axis.interior[cell - 1]


def _observation_at(cell: int) -> Observation:
    """Return one recorded experiment whose result falls in ``cell``."""
    return Observation(
        experiment=ExperimentId(f"e/a42/{cell}"),
        template=_design().template(),
        result=(_value_in_cell(cell),),
    )


def _vector(
    candidate: str, entertained: dict[str, str], cells: tuple[int, ...]
) -> DimensionVector:
    """Return the whole vector for a library candidate against a library set.

    A26's ``_d4`` helper returns the dimension alone, which is what that gate is
    about. This gate is about the term reported *beside* it, so both have to come
    back from one call: the pair is the claim, and reading them from separate
    calls would let an implementation report a size unrelated to the comparison
    the figure actually ran.
    """
    library = closed_set()
    vector, grown = dimension_vector(
        library[candidate],
        library["null"],
        grammar=GRAMMAR,
        table=_table(),
        simulate=simulator(GRAMMAR),
        held_out=(),
        observations=tuple(_observation_at(cell) for cell in cells),
        entertained={
            HypothesisId(node_id): _structure(name)
            for node_id, name in entertained.items()
        },
    )
    _TABLE[0] = grown
    return vector


def _reading(dimensions: DimensionVector) -> CellReading:
    """Return a reading carrying ``dimensions`` and stated numbers elsewhere.

    Nothing outside the vector is read by this gate. It exists because the ledger
    stores a flat payload and a row cannot be built without a whole reading --
    ``tests/acceptance/test_a43.py`` builds one the same way and says so.
    """
    return CellReading(
        dimensions=dimensions,
        score=ClosedWorldScore(
            truth_mass=Probability(0.5),
            log_score=0.0,
            leading_mass=Probability(0.5),
            correct=True,
            identified=False,
        ),
        ppc_p_value=0.0,
        inadequate=False,
        probe_p_value=0.03,
        probe_inadequate=False,
        agency=AgencyMetrics(
            system="V7",
            scenario=ScenarioId("S11"),
            experiments=8,
            entertained=4,
            escalated=0,
            proposals=None,
            causes=None,
        ),
        adjudication=Adjudication(
            claims=80, adjudicated=80, contradictions=0, zombies=0
        ),
        null_mass=Probability(0.25),
        abstain_mass=Probability(0.5),
        max_defect_mass=0.4,
        experiments=8,
        structural_distance=1.0,
        battery=battery_key(()),
    )


class TestA42ComparisonSet:
    """A reported D4 states what it was compared against."""

    def test_a42_d4_names_the_set_it_improved_on(self) -> None:
        """The gate: two zeros that mean opposite things read differently.

        Both clauses are checked at the **payload**, which is where the criterion
        puts them and the only surface a reader of the ledger has. Checking them
        on the vector alone would pass an implementation that computed the size
        and never carried it, which is precisely the state A26 left the ledger
        in: the number existed inside the function and the row did not say it.

        Three cases, not two. The set can be emptied **two** ways -- by the
        exclusion, when the candidate was the only thing entertained, and by the
        table, when every alternative is a structure it holds no row for. Both
        are the standard's "empty", and both have to separate from the
        outperformed case. The first two are A26's own zero cases, unchanged; the
        third is the one a count taken over ``entertained`` rather than over what
        was actually compared against gets wrong, reporting a comparison that
        never ran and collapsing this row onto the outperformed one.
        """
        emptied = _vector(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture"},
            cells=(_RESCUED_CELL,),
        )
        filtered = _vector(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": _UNHELD},
            cells=(_RESCUED_CELL,),
        )
        outgunned = _vector(
            candidate="hawkes",
            entertained={"h1": "hawkes", "h2": "poisson_mixture"},
            cells=(_RESCUED_CELL,),
        )
        assert emptied.d4_explanatory_coverage == 0.0
        assert filtered.d4_explanatory_coverage == 0.0
        assert outgunned.d4_explanatory_coverage == 0.0, (
            "the constructed case no longer outguns the candidate"
        )

        empty_payload = _reading(emptied).as_payload()
        filtered_payload = _reading(filtered).as_payload()
        outgunned_payload = _reading(outgunned).as_payload()

        assert empty_payload["n_comparison"] == 0.0
        assert filtered_payload["n_comparison"] == 0.0
        assert outgunned_payload["n_comparison"] == 1.0
        assert (
            empty_payload["d4_explanatory_coverage"]
            == filtered_payload["d4_explanatory_coverage"]
            == outgunned_payload["d4_explanatory_coverage"]
        ), "the three cases must agree on D4, or they do not test the collision"
        # Deliberately *not* `empty_payload != outgunned_payload`. That assertion
        # was here and was vacuous: the outgunned case has to use a different
        # candidate to be outgunned at all, so the two payloads already differ on
        # `d1_structural_distance` and `d6_complexity` and the comparison passes
        # with `n_comparison` deleted from `as_payload` altogether. The three
        # assertions above are what pin the criterion.

    def test_a42_a_rescuing_candidate_names_its_set_too(self) -> None:
        """The size accompanies a positive D4, not only a zero one.

        The criterion's clause about the two zeros is what makes the term
        necessary; it is not the whole of what the term is for. A26's rescue case
        improves on one alternative, and a reader comparing that figure against an
        arm that entertained six needs the denominator on both rows. An
        implementation reporting the size only where D4 is zero would satisfy the
        clause and leave every informative row unreadable.
        """
        rescuing = _vector(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": "hawkes"},
            cells=(_RESCUED_CELL,),
        )
        assert rescuing.d4_explanatory_coverage > 0.0
        assert _reading(rescuing).as_payload()["n_comparison"] == 1.0

    def test_a42_the_count_is_the_set_and_not_the_scoring(self) -> None:
        """With nothing recorded, D4 is 0.0 and the set is still named.

        The two terms answer different questions. ``_explanatory_coverage``
        returns early on empty observations, and a size derived from that path
        rather than from the set would report ``0`` here -- claiming the
        candidate stood alone when in fact it was never asked to rescue anything.
        That is the same conflation
        :attr:`~sciagent.eval.scoring.DimensionVector.n_held_out` avoids by
        reporting the battery's size rather than the number of designs scored.
        """
        unexamined = _vector(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": "hawkes"},
            cells=(),
        )
        assert unexamined.d4_explanatory_coverage == 0.0
        assert unexamined.n_comparison == 1

    def test_a42_the_set_is_counted_by_structure_and_not_by_id(self) -> None:
        """Two ids carrying one structure are one alternative.

        A26 excludes by structure rather than by hypothesis id, because a
        :data:`~sciagent.core.types.Defect` is a ``frozenset`` and two ids holding
        one structure are one structure. The size has to be counted the same way
        or it contradicts the figure beside it, reporting two alternatives for a
        comparison that ran against one.
        """
        duplicated = _vector(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": "hawkes", "h3": "hawkes"},
            cells=(_RESCUED_CELL,),
        )
        assert duplicated.n_comparison == 1

    def test_a42_a_candidate_entertained_twice_has_still_rescued_nothing(self) -> None:
        """One structure under two ids excludes to nothing, not to one.

        The other half of counting by structure, and a separate test rather than
        a second block of the one above because a failure there short-circuits
        this and leaves it unwatched -- which is how an assertion that never runs
        red gets taken for one that did.

        A26 states the case in ``_explanatory_coverage``'s own docstring: "a
        candidate entertained twice has still rescued nothing". Counting by id
        would report a comparison set of one against a D4 of ``0.0``, which reads
        as *outperformed by an alternative* when there was no alternative.
        """
        twice = _vector(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": "poisson_mixture"},
            cells=(_RESCUED_CELL,),
        )
        assert twice.d4_explanatory_coverage == 0.0
        assert twice.n_comparison == 0

    def test_a42_the_reading_bumps_for_the_new_payload_key(self) -> None:
        """``DIMENSION_VERSION`` moves, and ``METRIC_VERSION`` does not.

        The fourth time this has been the shape of the answer, after three prior
        bumps -- A29, A31 and A30 -- which each moved the term because
        :meth:`~sciagent.eval.matrix.CellReading.as_payload` gained keys, and a
        row from the previous generation cannot answer a question about them. A
        ``spec8/5`` row carries no comparison-set size, so a reader pooling the
        two generations would average D4 over rows where the denominator is
        unknown -- which is the defect this gate exists to remove, reintroduced by
        aggregation.

        ``METRIC_VERSION`` does not move, for the reason
        :data:`~sciagent.eval.scoring.DIMENSION_VERSION` states at length: it
        addresses every cached empirical table, D1-D6 are unchanged here, and no
        estimator is touched. Bumping it would force a table rebuild for a change
        that computes nothing new.

        The exclusion *mechanism* is generic over the term and is gated by
        ``test_a31_a_row_from_the_previous_reading_is_not_pooled_beside_these``
        and its A30 sibling, both of which carry this literal too. What this
        assertion adds is that the bump arrives as a decision rather than as
        silence.
        """
        assert DIMENSION_VERSION == "spec8/6"
