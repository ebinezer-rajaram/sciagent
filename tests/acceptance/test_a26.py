"""Acceptance test A26: D4 measures something, and D2 is a proper score.

A26 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"D4 is identically zero by construction, and D2 is not
a proper score"* (2026-08-18), and reads:

    ``test_a26_d4_rewards_a_rescuing_candidate`` -- on a constructed case where a
    candidate explains an observation the entertained set fits poorly, D4 > 0; on
    a case where it adds nothing, D4 = 0; and the truth maximises D2 among all
    candidate distributions on a constructed grid.

Both halves are defects in the instrument rather than in any system, which is
why this gate grades nothing that was built after it. What it establishes is that
two of §8's six reported dimensions carry information at all.

What was wrong with D4
----------------------

:func:`~sciagent.eval.matrix.reading_of` takes the run's *leading* structure as
the candidate and then passes the whole edit map as ``entertained``. The
candidate is therefore always a member of the set it is scored against, so the
best entertained log-likelihood is at least its own on every observation and
``max(0.0, mine - best)`` is zero for all of them. The 2026-08-18 review measured
``d4 == 0`` on 1,120 of 1,120 recorded ledger rows: a constant reported as a
comparison.

The fix is to exclude the candidate's own structure from the set it is compared
against, so D4 answers the question §8 asks -- *what does this structure rescue
that the alternatives do not* -- rather than comparing a hypothesis with itself.

What was wrong with D2
----------------------

D2 read ``log2 p(modal cell)``: the probability the candidate assigned to the one
outcome the truth produces most often. That is improper. A candidate that puts
*all* its mass on the truth's modal cell scores higher than the truth's own
distribution does, so the dimension rewarded overconfidence and would have graded
a system that hedged correctly below one that did not.

The proper reading is the expected log score under the truth's *whole*
distribution, which Gibbs' inequality maximises at the truth itself. The ``-inf``
semantics are unchanged: a candidate that rules out something that happens still
scores ``-inf``, which is the one thing the modal reading got right.

How the cases are constructed
-----------------------------

The D4 cases use the suite's gate table and the slice's own library, so nothing
is simulated: ``query:phase_conditioned_dispersion`` separates the library
sharply at cell 12, where ``poisson_mixture`` holds 0.3614 against ``hawkes``'s
0.0257, and again at cell 5, where the ordering reverses. That is a genuine
rescue -- one structure explains an observation another fits badly -- and it is
measured against the table rather than asserted, so the gate pins the arithmetic
and not merely the sign.

Those two figures are ``EmpiricalTable.probabilities``, which is the reading
:meth:`~sciagent.inference.empirical.EmpiricalTable.estimate` -- and therefore
D4 -- goes through. The same cell under ``resolved_probabilities`` is
0.3574 and 0.0255, because that reading floors an unreached cell at the rule of
three and renormalises. D2 goes through *that* one, so the two dimensions below
quote different numbers for the same cell on purpose.

The truth passed to :func:`~sciagent.eval.scoring.dimension_vector` is a library
member rather than S11's out-of-library truth, and the held-out battery is empty.
D4 does not read either one, and both choices keep the gate to a table load.

The propriety cases run on the per-design kernel directly, because "all candidate
distributions on a constructed grid" is a statement about distributions and the
library offers five. The grid is the twentieths simplex on three cells, which
contains the truth exactly.
"""

from __future__ import annotations

import math

import pytest
from slice_tables import GRAMMAR, gate_table

from environments.pointproc.outcomes import closed_set, simulator, slice_designs
from sciagent.core.types import ExperimentId, HypothesisId
from sciagent.eval.scoring import _predictive_log_score, dimension_vector
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.inference.empirical import EmpiricalTable
from sciagent.inference.interface import Observation

#: The design that separates the library. Named rather than found by search: a
#: gate that goes looking for whichever design happens to discriminate would keep
#: passing after the discrimination disappeared, by finding a different one.
_DESIGN_ID = "query:phase_conditioned_dispersion"

#: The cell where ``poisson_mixture`` explains and ``hawkes`` does not. Also
#: named: see above.
_RESCUED_CELL = 12

#: The cell where the ordering reverses -- ``hawkes`` holds 0.0944 against
#: ``poisson_mixture``'s 0.0002. Needed to tell per-observation clipping from
#: clipping the total, which one observation cannot distinguish.
_OUTGUNNED_CELL = 5

_TABLE: list[EmpiricalTable] = []


def _table() -> EmpiricalTable:
    if not _TABLE:
        _TABLE.append(gate_table())
    return _TABLE[0]


def _design() -> ExperimentDesign:
    return next(design for design in slice_designs() if str(design.id) == _DESIGN_ID)


def _value_in_cell(cell: int) -> float:
    """Return a value landing in ``cell`` of the design's one-dimensional space.

    Bins are half-open at the bottom, so an interior edge lies in the bin above
    it; the first bin has no interior edge below it and takes a midpoint.
    """
    axis = _design().outcome.axes[0]
    if cell == 0:
        return (axis.low + axis.interior[0]) / 2
    return axis.interior[cell - 1]


def _observation_at(cell: int) -> Observation:
    """Return one recorded experiment whose result falls in ``cell``."""
    return Observation(
        experiment=ExperimentId(f"e/a26/{cell}"),
        template=_design().template(),
        result=(_value_in_cell(cell),),
    )


def _d4(candidate: str, entertained: dict[str, str], cells: tuple[int, ...]) -> float:
    """Return D4 for a library candidate against a library set, over ``cells``.

    One recorded experiment per cell. ``entertained`` is keyed by hypothesis id
    so that the caller states whether the candidate is a member -- which is the
    whole subject of this gate, and which
    :func:`~sciagent.eval.matrix.reading_of` always makes true.
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
            HypothesisId(node_id): library[name]
            for node_id, name in entertained.items()
        },
    )
    _TABLE[0] = grown
    return vector.d4_explanatory_coverage


def _log_likelihood(structure: str, cell: int) -> float:
    """Return the table's log-likelihood of the observation under a structure."""
    observation = _observation_at(cell)
    return (
        _table()
        .estimate(closed_set()[structure], observation.template, observation.result)
        .log_likelihood
    )


class TestA26ExplanatoryCoverage:
    """D4 compares a candidate with the alternatives, not with itself."""

    def test_a26_d4_rewards_a_rescuing_candidate(self) -> None:
        """A structure that explains what the others do not scores its advantage.

        The candidate is a member of ``entertained``, which is the arrangement
        ``reading_of`` always produces. The expected figure is read off the same
        table the dimension reads, so this pins the quantity rather than its
        sign: D4 is the log2 improvement over the best *other* hypothesis.
        """
        expected = (
            _log_likelihood("poisson_mixture", _RESCUED_CELL)
            - _log_likelihood("hawkes", _RESCUED_CELL)
        ) / math.log(2.0)
        assert expected > 0.0, "the constructed case no longer separates"

        coverage = _d4(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": "hawkes"},
            cells=(_RESCUED_CELL,),
        )
        assert coverage > 0.0
        assert coverage == pytest.approx(expected)

    def test_a26_d4_is_zero_when_the_candidate_adds_nothing(self) -> None:
        """A candidate the set already outperforms rescues nothing, so D4 is 0.

        The gate's negative control, and it passes before the fix as well as
        after -- necessarily, since the unfixed D4 is zero for every input. Its
        value is on the other side: it holds the fix to §8's "improvement", so
        that excluding the candidate cannot turn a worse explanation into a
        positive score.
        """
        assert (
            _d4(
                candidate="hawkes",
                entertained={"h1": "hawkes", "h2": "poisson_mixture"},
                cells=(_RESCUED_CELL,),
            )
            == 0.0
        )

    def test_a26_d4_clips_each_observation_and_not_the_total(self) -> None:
        """An experiment the candidate loses cannot cancel one it wins.

        §8 asks for "likelihood improvement on previously poorly-explained
        registered results", and
        :attr:`~sciagent.eval.scoring.DimensionVector.d4_explanatory_coverage`
        states the same contract as "summed across recorded experiments *where
        it does better*". One observation cannot tell that from clipping the
        total, so this uses two whose orderings disagree: the candidate gains
        3.82 bits at cell 12 and loses 8.88 at cell 5. Clipping per observation
        reports the gain; clipping the total reports zero.
        """
        gain = (
            _log_likelihood("poisson_mixture", _RESCUED_CELL)
            - _log_likelihood("hawkes", _RESCUED_CELL)
        ) / math.log(2.0)
        loss = (
            _log_likelihood("poisson_mixture", _OUTGUNNED_CELL)
            - _log_likelihood("hawkes", _OUTGUNNED_CELL)
        ) / math.log(2.0)
        assert gain > 0.0 > loss, "the two cells must disagree to separate the two"
        assert gain + loss < 0.0, "clipping the total must give a different answer"

        assert _d4(
            candidate="poisson_mixture",
            entertained={"h1": "poisson_mixture", "h2": "hawkes"},
            cells=(_RESCUED_CELL, _OUTGUNNED_CELL),
        ) == pytest.approx(gain)

    def test_a26_d4_is_zero_when_the_candidate_is_the_only_hypothesis(self) -> None:
        """Excluding the candidate can empty the set, which is a 0 and not a crash.

        There is no alternative to improve on, so nothing was rescued. The
        unfixed code reached this answer by comparing the candidate with itself;
        the fixed code reaches it by having nothing to compare with.
        """
        assert (
            _d4(
                candidate="poisson_mixture",
                entertained={"h1": "poisson_mixture"},
                cells=(_RESCUED_CELL,),
            )
            == 0.0
        )


class TestA26PredictivePropriety:
    """D2 is maximised by the truth, so it cannot reward overconfidence."""

    #: A truth spread across three cells, whose modal cell is the first. Any
    #: distribution more peaked there beats it under the modal reading.
    TRUTH = (0.5, 0.3, 0.2)

    def test_a26_the_truth_maximises_the_predictive_score(self) -> None:
        """Over the twentieths simplex, the score's argmax is the truth itself."""
        grid = [
            (first / 20, second / 20, (20 - first - second) / 20)
            for first in range(21)
            for second in range(21 - first)
        ]
        assert self.TRUTH in grid, "the grid must contain the truth to be a test of it"

        best = max(grid, key=lambda mine: _predictive_log_score(mine, self.TRUTH))
        assert best == pytest.approx(self.TRUTH)

    def test_a26_the_score_refuses_to_reward_overconfidence(self) -> None:
        """A candidate more peaked at the modal cell scores below the truth.

        This is the case the modal reading got backwards: ``log2 0.8`` exceeds
        ``log2 0.5``, so the old D2 preferred the overconfident candidate. Both
        the strict point mass and the merely peaked candidate are checked,
        because the point mass alone would pass on the ``-inf`` rule rather than
        on propriety.
        """
        honest = _predictive_log_score(self.TRUTH, self.TRUTH)
        assert _predictive_log_score((0.8, 0.1, 0.1), self.TRUTH) < honest
        assert _predictive_log_score((1.0, 0.0, 0.0), self.TRUTH) == -math.inf

    def test_a26_the_score_keeps_the_minus_infinity_rule(self) -> None:
        """Ruling out something that happens is still ``-inf``; ruling out
        something that does not is free."""
        assert _predictive_log_score((0.0, 0.5, 0.5), (0.2, 0.4, 0.4)) == -math.inf
        assert _predictive_log_score((0.5, 0.5, 0.0), (0.5, 0.5, 0.0)) > -math.inf

    def test_a26_d2_reads_the_whole_distribution(self) -> None:
        """The dimension is wired to the proper score, not merely accompanied by it.

        A proper kernel that nothing calls would leave D2 exactly as it was, and
        every test above would still pass. This pins the wiring at the one place
        it is checkable without constructing a table: scored against itself, a
        structure's D2 is the negative entropy of its own row, whereas the modal
        reading returns ``log2`` of its largest cell alone.
        """
        design = _design()
        structure = closed_set()["hawkes"]
        row = _table().resolved_probabilities(structure, design.id)
        entropic = math.fsum(cell * math.log2(cell) for cell in row)
        modal = math.log2(max(row))
        assert entropic != pytest.approx(modal), "the two readings must differ here"

        vector, grown = dimension_vector(
            structure,
            structure,
            grammar=GRAMMAR,
            table=_table(),
            simulate=simulator(GRAMMAR),
            held_out=(design,),
        )
        _TABLE[0] = grown
        assert vector.d2_held_out_predictive == pytest.approx(entropic)

    def test_a26_d2_is_the_proper_score_off_the_diagonal(self) -> None:
        """Scored against a *different* truth, D2 separates every candidate wiring.

        The test above cannot do this. At ``candidate == truth`` the proper
        score and its transpose are numerically identical -- both reduce to the
        row's negative entropy -- so a call site that swapped its two arguments
        would pass it while computing ``sum(mine[c] * log2 theirs[c])``, which
        is linear in ``mine`` and therefore maximised by a point mass on the
        truth's best cell. That is the very defect A26 exists to remove,
        reinstated with the gate still green.

        Off the diagonal the three candidate readings take three distinct
        values, so one assertion rejects all but the right one. The test asserts
        they are distinct before relying on it, rather than assuming a table it
        does not control keeps them so.
        """
        design = _design()
        library = closed_set()
        candidate, truth = library["poisson_mixture"], library["hawkes"]
        mine = _table().resolved_probabilities(candidate, design.id)
        theirs = _table().resolved_probabilities(truth, design.id)

        proper = math.fsum(
            weight * math.log2(probability)
            for probability, weight in zip(mine, theirs, strict=True)
        )
        transposed = math.fsum(
            probability * math.log2(weight)
            for probability, weight in zip(mine, theirs, strict=True)
        )
        modal = math.log2(
            mine[max(range(len(theirs)), key=lambda cell: (theirs[cell], -cell))]
        )
        assert len({round(value, 9) for value in (proper, transposed, modal)}) == 3

        vector, grown = dimension_vector(
            candidate,
            truth,
            grammar=GRAMMAR,
            table=_table(),
            simulate=simulator(GRAMMAR),
            held_out=(design,),
        )
        _TABLE[0] = grown
        assert vector.d2_held_out_predictive == pytest.approx(proper)
