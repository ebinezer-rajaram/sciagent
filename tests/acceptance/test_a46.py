"""Acceptance test A46: the report layer evaluates SPEC §12 criterion 4.

A46 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Criterion 4's check has no production caller"*, and
reads:

    ``test_a46_the_report_evaluates_criterion_four`` -- a ``MatrixReport`` built
    from rows whose probe rates fail the criterion carries a failing verdict, and
    one built from rows that satisfy it carries a passing verdict; a report
    missing a scenario the criterion names refuses rather than reporting a
    verdict over what is present.

What was wrong
--------------

Gate A45 made criterion 4 falsifiable and left it uncalled. Outside its own
module and A45's gate, the only reference to
:func:`~sciagent.eval.report.criterion_four` was its ``__all__`` entry. SPEC §12
says the function *"is the check"*, so until something called it, §12 named a
check no report performed and the criterion was evaluated by a reader assembling
the rate vector by hand.

This repository has already indicted that exact shape once, as ``docs/BACKLOG.md``
rank 8's *"The verifier has no production caller"*, landed at gate A30. Shipping
it twice in one file's neighbourhood is what this gate closes.

Why a function beside ``summarise`` and not a field on the report
------------------------------------------------------------------

The entry left the fork open -- *"``summarise``'s return value or a function
beside it"* -- and the deciding constraint is that criterion 4 is defined over
**nine named scenarios** while a perfectly ordinary report covers one. A
single-cell report is what ``--scenarios S11`` produces, what B6's one opt-in
cell produces, and what ``tests/acceptance/test_a43.py`` already builds. A
mandatory field on :class:`~sciagent.eval.report.MatrixReport` would have to be
built by :func:`~sciagent.eval.report.summarise` for every one of those, so it
would either refuse them all -- breaking partial reports outright -- or carry
``None``, which is an absence dressed as a verdict and is precisely what the
gate's third clause forbids.

So the projection is a function over a finished report. It refuses loudly when
asked for a verdict it cannot form, and :func:`~sciagent.eval.report.render`
asks only when the report is complete enough to be asked.

What this test establishes
--------------------------

1. A campaign satisfying the criterion carries a **passing** verdict.
2. Each of the two ways it can fail -- silent on S11, and firing where it must
   stay quiet -- carries a **failing** verdict that says which way. The criterion
   is a conjunction, so exercising one clause leaves the other untested.
3. A report missing a scenario the criterion names **refuses**, naming it. This
   is the clause that stops the whole gate being satisfied by a projection that
   quietly drops what it cannot find.
4. S8, S10 and S12 firing changes **nothing**. Per SPEC §4.5 the criterion is
   silent about those three, and without this a stricter implementation that
   demanded quiet everywhere would satisfy clauses 1-3.
5. The verdict reaches :func:`~sciagent.eval.report.render`. An uncalled check is
   the defect being closed, so a check called only by a test would reproduce it.
6. Cells disagreeing on one scenario's rate **refuse**. This one is forced by the
   design rather than by the gate line: many cells project onto one rate per
   scenario, gate A29 says they are identical by construction, and an
   implementation free to pick one silently reports a verdict over a number
   nobody chose.
7. A partial report still **renders**. The regression guard on the design choice
   above: single-cell reports must not begin raising because criterion 4 arrived.

Why the loops and the fractional rates are here
------------------------------------------------

The first draft of this module instantiated clauses 2 and 3 at **one** scenario
each -- S3 for the false positive, S9 for the absence -- and ``/test-review``
refuted it by building three wrong implementations that passed it, each run
green against the whole file. They are recorded here because each names a real
shape a re-implementation falls into, and the guard against it is the specific
thing that would otherwise be quietly dropped as redundant:

- **The quiet set written as a contiguous range**, ``S1``-``S7``, losing ``S9``.
  A campaign whose probe fires on every S9 replicate then renders *"holds"*. This
  is why ``test_a46_the_report_evaluates_criterion_four`` loops over
  :data:`QUIET` rather than sampling it, mirroring ``test_a45.py``'s own loop
  and the non-contiguity note at ``report.py``'s ``_MUST_BE_QUIET``.
- **A rate threshold at 0.5**, in an implementation that *does* delegate to
  :func:`~sciagent.eval.report.criterion_four` and so satisfies the entry's
  "production caller" idea while still being wrong. When this gate was written
  the criterion read "does not fire" as exactly zero, so one firing replicate
  in twenty -- rate 0.05 -- was a failing campaign a rounding projection
  passed. Gate A47 then moved the size clause to the pooled quiet set against
  ``SIZE_TOLERANCE``, so the small-rate cases here now probe the pooled
  boundary through the report path instead; the S11 power clause keeps its
  ``> 0`` reading, and a module built only from 0.0 and 1.0 still cannot
  separate it from a threshold.
- **Presence required for the eight quiet scenarios only**, with an absent S11
  read as 0.0 -- the "treat a missing scenario as zero" default the criterion's
  own docstring names. That report is ``--scenarios S1,...,S9``, and clause 3
  requires a refusal rather than the *"FAILS -- silent on S11"* such a projection
  reports. This is why the absence loop covers all nine names and not just the
  quiet ones.

The review also noted, separately, that the gate line never says the projection
must *call* :func:`~sciagent.eval.report.criterion_four`, though wiring it in is
the entry's stated point -- so two of those three candidates satisfied the gate
line's letter while defeating its purpose. Asserting the call itself would mean
patching, which this suite does not do and which pins a mechanism rather than a
meaning. ``test_a46_the_projection_has_no_second_opinion``
encodes the property instead: over a spread of rate vectors the report's verdict
must equal the criterion's own on the same rates, so a re-implementation is free
to exist only while it agrees everywhere, which is the testable form of "there is
one check, not two".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from environments.pointproc.scenarios import scenario
from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    GrammarVersion,
    MetricVersion,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.eval.agency import AgencyMetrics
from sciagent.eval.campaign import Adjudication
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    battery_key,
    cell_key,
)
from sciagent.eval.report import (
    MatrixReport,
    ProbeCount,
    criterion_four,
    criterion_four_of,
    render,
    summarise,
)
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.partitions import DataPartition

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

PLATFORM = "Windows-11-x86_64"
NUMPY = "2.5.1"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")

#: The eight scenarios criterion 4 requires the probe to stay quiet on. **Not
#: contiguous** -- S8 sits inside the span and is not a member -- which is the
#: property the loops below exist to hold, and the one a ``range`` silently
#: loses.
QUIET = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S9")

#: The one it requires the probe to fire on.
FIRES = "S11"

#: The three SPEC §4.5 leaves unconstrained, named here so a test can say it
#: means them rather than "the rest".
UNCONSTRAINED = ("S8", "S10", "S12")

#: SPEC §9's replicate count, used where a rate has to be a fraction rather than
#: a flag. Twenty is what the campaign actually runs, so ``1/20`` is the smallest
#: positive rate a real cell can report and the natural probe of the ``> 0``
#: boundary.
REPLICATES = 20


def _scenario_class(target: ScenarioId) -> ScenarioClass:
    """Classify off the environment's declaration, as the real caller does."""
    return scenario(str(target)).scenario_class


def _battery(target: ScenarioId) -> Sequence[ExperimentDesign]:
    """Return the scenario's declared held-out battery, as the real caller does."""
    return scenario(str(target)).held_out


def _reading(*, probe_inadequate: bool) -> CellReading:
    """Return a reading whose only load-bearing field is the Stage A probe.

    Every other number is stated rather than derived: the ledger stores a flat
    payload and ``summarise`` folds every field of one, so a row cannot be built
    without a whole vector, and none of the rest is read by this gate.
    """
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=0.0,
            d2_held_out_predictive=-1.0,
            d3_intervention_similarity=0.0,
            d4_explanatory_coverage=0.0,
            d5_enabled_experiment_value=0.0,
            d6_complexity=12.0,
            n_held_out=3,
            n_comparison=1,
        ),
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
        probe_inadequate=probe_inadequate,
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


def _row(
    name: str, *, probe_inadequate: bool, system: str, replicate: int
) -> LedgerEntry:
    """Return one recorded row of ``name`` carrying a stated probe verdict."""
    target = ScenarioId(name)
    task = CellTask(
        cell=Cell(system, target, 1), replicate=replicate, seed=Seed(replicate)
    )
    return LedgerEntry(
        key=cell_key(task, ADDRESS, battery=tuple(_battery(target))),
        reading=FrozenDict[str, float](
            dict(_reading(probe_inadequate=probe_inadequate).as_payload())
        ),
        sequence=replicate,
    )


def _report(
    rates: Mapping[str, float],
    *,
    systems: Sequence[str] = ("V7",),
    replicates: int = 1,
) -> MatrixReport:
    """Summarise a campaign whose probe fired at the rate ``rates`` names.

    ``replicates`` defaults to one, where a rate can only be 0.0 or 1.0 and the
    cells are cheap. Pass :data:`REPLICATES` where the point is a rate strictly
    between the two -- the ``> 0`` boundary the criterion is written on, which a
    module built only from flags cannot reach.

    Refuses a rate that rounds to a different verdict than it names, so a test
    asking for a small positive rate cannot silently get a quiet cell.
    """
    rows: list[LedgerEntry] = []
    for system in systems:
        rows.extend(_rows(rates, system=system, replicates=replicates))
    return _summarise(tuple(rows))


def _rows(
    rates: Mapping[str, float], *, system: str, replicates: int
) -> tuple[LedgerEntry, ...]:
    """Return one arm's rows, firing at the rate each scenario names.

    Separate from :func:`_report` so a test can give two arms **different**
    replicate counts, which is what a ``--replicates N`` smoke run beside a full
    pass actually leaves in a ledger.
    """
    rows: list[LedgerEntry] = []
    for name, rate in rates.items():
        firing = round(rate * replicates)
        if (rate > 0.0) is not (firing > 0):
            raise AssertionError(
                f"{name} at rate {rate} over {replicates} replicate(s) rounds "
                f"to {firing} firing, which is the other verdict; this "
                f"fixture would test the opposite of what it says"
            )
        rows.extend(
            _row(
                name,
                probe_inadequate=index < firing,
                system=system,
                replicate=index,
            )
            for index in range(replicates)
        )
    return tuple(rows)


def _summarise(rows: tuple[LedgerEntry, ...]) -> MatrixReport:
    """Summarise ``rows`` with the callbacks the real caller supplies."""
    return summarise(
        rows,
        address=ADDRESS,
        scenario_class=_scenario_class,
        battery=_battery,
        platform=PLATFORM,
        numpy_version=NUMPY,
        grammar=GRAMMAR,
    )


def _passing() -> dict[str, float]:
    """Return the probe pattern criterion 4 is satisfied by."""
    return {**{name: 0.0 for name in QUIET}, FIRES: 1.0}


def _criterion_line(text: str) -> str:
    """Return the one rendered line reporting criterion 4.

    Raises if there is not exactly one: a report carrying the verdict twice, or
    not at all, is what this helper exists to make visible rather than paper over
    with a ``next()``.
    """
    matched = [line for line in text.splitlines() if "criterion 4" in line]
    assert len(matched) == 1, f"expected exactly one criterion 4 line, got {matched!r}"
    return matched[0]


class TestA46ReportEvaluatesCriterionFour:
    """The report layer performs the check SPEC §12 says is the check."""

    def test_a46_the_report_evaluates_criterion_four(self) -> None:
        """The gate: a passing campaign passes, a failing one fails, a partial
        one refuses.

        The three clauses of the gate line. Two of them are quantified over the
        nine scenarios the criterion names, so both are looped rather than
        sampled -- see the module docstring for the three implementations that
        passed the sampled version.
        """
        holds = criterion_four_of(_report(_passing()))
        assert holds.holds is True
        assert holds.fired_on_s11 is True
        assert holds.false_positives == ()

        # Fails: the probe never fired on the one scenario built around an
        # inadequacy. An instrument that cannot see it is the power clause.
        blind = criterion_four_of(_report({**_passing(), FIRES: 0.0}))
        assert blind.holds is False
        assert blind.fired_on_s11 is False
        assert blind.false_positives == ()

        # Fails the other way, on every member of the quiet set in turn. The
        # size clause, absent from the original wording. Looped because the set
        # is not contiguous: a projection written as `range(1, 8)` loses S9
        # alone, and sampling any other scenario never sees it.
        for name in QUIET:
            noisy = criterion_four_of(_report({**_passing(), name: 1.0}))
            assert noisy.holds is False, f"{name} firing must fail the criterion"
            assert noisy.fired_on_s11 is True
            assert noisy.false_positives == (ScenarioId(name),)

        # Refuses rather than judging what is present, for every scenario the
        # criterion names -- including S11, whose absence a projection that
        # defaults a missing rate to 0.0 reports as a failing verdict instead.
        # In each case the scenarios that remain would satisfy the criterion, so
        # a projection that skipped the absent one returns a pass here.
        for dropped in (*QUIET, FIRES):
            partial = {
                name: rate for name, rate in _passing().items() if name != dropped
            }
            with pytest.raises(MalformedDesignError, match=rf"\b{dropped}\b") as raised:
                criterion_four_of(_report(partial))
            assert "criterion 4" in str(raised.value)

    def test_a46_the_pooled_size_clause_reaches_the_report(self) -> None:
        """Both sides of gate A47's boundary, read through the report path.

        Until A47 this test pinned the opposite: one firing replicate in
        twenty *failed*, because the clause was exactly-zero. The revision is
        the recorded 2026-08-27 decision, and what this now establishes is
        that the report layer reads the pooled form rather than a per-scenario
        one -- one firing in 160 quiet draws is 0.00625 and holds, still
        naming the scenario as a false positive; eight firings on one scenario
        is 8/160 = 0.05, above the 0.045 tolerance, and fails. Every quiet
        scenario in turn, for the same reason the clause above is looped: a
        projection deriving the quiet set as a contiguous range loses S9.
        """
        for name in QUIET:
            within = criterion_four_of(
                _report({**_passing(), name: 1.0 / REPLICATES}, replicates=REPLICATES)
            )
            assert within.holds is True, (
                f"{name} at one firing in 160 quiet draws is within the "
                f"instrument's measured size"
            )
            assert within.false_positives == (ScenarioId(name),)

            beyond = criterion_four_of(
                _report({**_passing(), name: 8.0 / REPLICATES}, replicates=REPLICATES)
            )
            assert beyond.holds is False, (
                f"{name} at eight firings in 160 quiet draws is 0.05, beyond "
                f"the tolerance"
            )
            assert beyond.false_positives == (ScenarioId(name),)

    def test_a46_a_single_firing_replicate_on_s11_is_firing(self) -> None:
        """The other side: no power threshold is imposed on S11 either.

        :func:`~sciagent.eval.report.criterion_four` guarantees that it imposes
        no numeric power level, because choosing one is a decision nobody has
        taken. Without this, the test above is satisfied by a projection that
        demands a *high* rate everywhere, which fails a campaign the criterion
        passes.
        """
        rates = {**_passing(), FIRES: 1.0 / REPLICATES}
        verdict = criterion_four_of(_report(rates, replicates=REPLICATES))
        assert verdict.holds is True
        assert verdict.fired_on_s11 is True

    def test_a46_the_projection_has_no_second_opinion(self) -> None:
        """The report's verdict equals the criterion's own, on the same rates.

        The entry's point is that :func:`~sciagent.eval.report.criterion_four` is
        *the* check and the report layer should call it -- not that the report
        layer should hold a second, agreeing-looking opinion about criterion 4.
        Asserting the call would mean patching; this asserts the property that
        makes the call worth having, over a spread that includes both failure
        modes, a fractional rate and the unconstrained scenarios.
        """
        vectors: tuple[dict[str, float], ...] = (
            _passing(),
            {**_passing(), FIRES: 0.0},
            {**_passing(), "S9": 1.0},
            {**_passing(), "S1": 1.0 / REPLICATES},
            {**_passing(), "S4": 0.5, "S7": 0.25},
            {**_passing(), **{name: 1.0 for name in UNCONSTRAINED}},
            {**_passing(), FIRES: 0.0, "S2": 1.0},
        )
        for rates in vectors:
            report = _report(rates, replicates=REPLICATES)
            # The cell rate recovers the count exactly because the fixture runs
            # every cell at REPLICATES replicates; `round` undoes the division
            # `_rows` performed, not a lossy estimate of it.
            expected = criterion_four(
                {
                    ScenarioId(str(cell.scenario)): ProbeCount(
                        fired=round(cell.probe_inadequate_rate * cell.replicates),
                        draws=cell.replicates,
                    )
                    for cell in report.cells
                }
            )
            assert criterion_four_of(report) == expected, rates

    def test_a46_the_unconstrained_scenarios_do_not_move_the_verdict(self) -> None:
        """S8, S10 and S12 fire freely and the criterion still holds.

        Clause 4. Per SPEC §4.5 those three are compound, non-identifiable and
        garden-path respectively, so a firing probe there is not evidence of a bad
        instrument and the criterion says nothing about them in either direction.
        Without this, an implementation demanding quiet everywhere satisfies every
        other clause here.
        """
        loud = {**_passing(), **{name: 1.0 for name in UNCONSTRAINED}}
        verdict = criterion_four_of(_report(loud))
        assert verdict.holds is True
        assert verdict.false_positives == ()

    def test_a46_render_carries_the_verdict(self) -> None:
        """Clause 5: the rendered report states the criterion's verdict.

        The defect being closed is a check nothing calls, so a check called only
        from a test would reproduce it exactly. ``render`` is the reported
        artefact, and the verdict has to be in it.
        """
        passing = render(_report(_passing()))
        line = _criterion_line(passing)
        assert "holds" in line
        # ASCII only, as every other byte `render` emits is: a local session reads
        # this on a Windows console under cp1252.
        assert passing.isascii()

        # Both failure modes reach the rendering, and each says which it was.
        noisy = _criterion_line(render(_report({**_passing(), "S3": 1.0})))
        assert "holds" not in noisy
        assert "S3" in noisy

        blind = _criterion_line(render(_report({**_passing(), FIRES: 0.0})))
        assert "holds" not in blind
        assert FIRES in blind

    def test_a46_a_partial_report_renders_and_says_it_was_not_evaluated(self) -> None:
        """Clause 7: a one-cell report renders, and does not claim a verdict.

        ``--scenarios S11`` and B6's single opt-in cell both produce exactly this,
        and ``tests/acceptance/test_a43.py`` already builds one. Criterion 4
        arriving must not make them raise -- but the line must also not read as a
        pass or a fail, which is the failure mode a ``None`` field would have had.
        """
        text = render(_report({FIRES: 1.0}))
        line = _criterion_line(text)
        assert "not evaluated" in line
        assert "holds" not in line
        # And emphatically *not* the breach marker: this report is fine, it is
        # merely small. The two outcomes shared three opening words until review
        # pointed out that nothing skimming the text could separate them.
        assert "A29 BREACH" not in line
        # Names something absent, so a reader knows what would have to be run.
        assert any(name in line for name in QUIET)

    def test_a46_cells_disagreeing_on_one_rate_are_refused(self) -> None:
        """Clause 6: two arms reporting different rates for one scenario refuse.

        Gate A29 evaluates the probe in the harness before ``investigate`` is
        called, so its rate is a function of the scenario and the seed alone and
        every arm's is bit-identical. A projection from many cells onto one rate
        per scenario has to say what it does when that fails, and the only honest
        answer is to refuse: picking one silently reports a verdict over a number
        nobody chose, and A29's guarantee is exactly what would have broken.
        """
        report = _summarise(
            (
                *_rows(_passing(), system="V7", replicates=1),
                *_rows({**_passing(), FIRES: 0.0}, system="B1", replicates=1),
            )
        )
        with pytest.raises(MalformedDesignError, match=r"\bS11\b") as raised:
            criterion_four_of(report)
        message = str(raised.value)
        assert "B1" in message and "V7" in message

    def test_a46_a_three_arm_disagreement_reads_the_same_in_any_row_order(
        self,
    ) -> None:
        """The conflict is a function of the rows, not of the order they arrive.

        Found by the invariant-3 lens, which confirmed it by execution rather
        than by reading. The first version compared each incoming row against
        whichever arm currently held the coordinate, so with three arms it named
        whichever *pair* happened to be adjacent in ``rows``: the same three rows
        gave ``B1 ... and V7 ...`` under one order and ``B4 ... and V7 ...``
        under another.

        This is the normal case rather than a corner of one. SPEC §9 runs V1, V7,
        B4 and B5 over all twelve scenarios and B1 over two, so every scenario
        carries at least four arms and S11 carries five. ``render`` guarantees
        that "a report built from the same rows in a different order renders
        identically", and the conflict reaches a rendered line.

        **Both assertions are kept, and the naming one is the load-bearing
        half.** A pairwise design that compares each row against whichever arm
        currently holds the coordinate can never name more than *two* systems,
        whatever its tie-breaking does -- so ``V7`` is absent from its message
        under every order, and that failure is structural rather than a property
        of this fixture. The equality check stays because byte-identical
        rendering is the property actually being claimed; it was measured to fail
        against the old shape here too, though only the naming check fails for a
        reason that holds of every implementation of that shape.
        """
        agreeing = _passing()
        odd = {**_passing(), FIRES: 0.0}
        arms = (
            _rows(agreeing, system="V7", replicates=1),
            _rows(agreeing, system="B4", replicates=1),
            _rows(odd, system="B1", replicates=1),
        )
        forwards = _summarise(tuple(row for arm in arms for row in arm))
        backwards = _summarise(tuple(row for arm in reversed(arms) for row in arm))

        with pytest.raises(MalformedDesignError) as first:
            criterion_four_of(forwards)
        with pytest.raises(MalformedDesignError) as second:
            criterion_four_of(backwards)
        assert str(first.value) == str(second.value)

        # And all three arms are named, not the two that happened to collide.
        message = str(first.value)
        for system in ("V7", "B4", "B1"):
            assert system in message, f"{system} is missing from {message!r}"

        # The rendering says the same thing both ways round, byte for byte.
        assert _criterion_line(render(forwards)) == _criterion_line(render(backwards))

    def test_a46_arms_from_different_seed_tables_are_refused(self) -> None:
        """Two arms sharing no seed are refused, not pooled.

        Found by ``/code-review`` on the union-by-seed projection, and it is the
        hole that projection *opened*: the conflict scan fires when two arms
        disagree at a shared ``(scenario, seed)``, and two arms drawn from
        different seed tables share none, so nothing fired and their replicates
        were unioned into one rate. The per-arm rate comparison this replaced
        happened to catch it. ``_refuse_reseeded`` does not: it keys on
        ``(system, scenario, replicate)``, so it sees a re-seeded *arm* and is
        blind to two arms carrying different tables.

        Measured before the guard: V7 on S11 at seeds 0 and 1 with the probe
        firing on both, beside B1 at seeds 500 and 501 with it firing on neither
        -- cells reporting 1.0 and 0.0 -- pooled to 0.5 and rendered ``holds``.
        Two cells in flat contradiction of A29, reported as a passing criterion.
        """
        firing = _rows({**_passing(), FIRES: 1.0}, system="V7", replicates=2)
        quiet = tuple(
            _row(FIRES, probe_inadequate=False, system="B1", replicate=seed)
            for seed in (500, 501)
        )
        report = _summarise((*firing, *quiet))

        by_arm = {
            (cell.system, str(cell.scenario)): cell.probe_inadequate_rate
            for cell in report.cells
        }
        # The premise: the two cells really are in flat contradiction.
        assert by_arm[("V7", FIRES)] == 1.0
        assert by_arm[("B1", FIRES)] == 0.0

        with pytest.raises(MalformedDesignError, match=r"\bS11\b") as raised:
            criterion_four_of(report)
        message = str(raised.value)
        assert "B1" in message and "V7" in message
        # Names the seeds, so a reader can see the two tables rather than infer
        # them, and does not blame a per-seed disagreement that never happened.
        assert "500" in message

        line = _criterion_line(render(report))
        assert "A29 BREACH" in line
        assert "holds" not in line

    def test_a46_unconstrained_scenarios_still_carry_a_breach(self) -> None:
        """A disagreement on S8, S10 or S12 suppresses the verdict.

        Found by ``/code-review``, and kept as the behaviour rather than fixed.
        ``test_a46_the_unconstrained_scenarios_do_not_move_the_verdict`` builds a
        **one-arm** report, so it never reached the multi-arm case and its claim
        that those three move the verdict "in neither direction" is true only
        while the arms agree.

        The scan deliberately covers every scenario, not the criterion's nine:
        A29 is one mechanism, so a probe disagreeing by arm anywhere has broken
        the guarantee the nine clean rates rest on too. It fails safe -- it can
        turn ``holds`` into a breach notice, never the reverse.
        """
        agreeing = _passing()
        loud = {**_passing(), **{name: 1.0 for name in UNCONSTRAINED}}
        report = _summarise(
            (
                *_rows(
                    {**agreeing, **{n: 0.0 for n in UNCONSTRAINED}},
                    system="V7",
                    replicates=1,
                ),
                *_rows(loud, system="B4", replicates=1),
            )
        )
        with pytest.raises(MalformedDesignError) as raised:
            criterion_four_of(report)
        # The named nine all agree; the breach is on an unconstrained scenario.
        message = str(raised.value)
        assert any(name in message for name in UNCONSTRAINED)

        # And the identical single-arm report still reads `holds`, which is what
        # makes this a statement about disagreement rather than about S8/S10/S12.
        assert criterion_four_of(_report(loud)).holds is True

    def test_a46_unequal_replicate_counts_are_not_a_breach(self) -> None:
        """Two arms at different replicate counts agree, and are not refused.

        Found by ``/code-review`` after the first implementation, which compared
        :attr:`~sciagent.eval.report.CellSummary.probe_inadequate_rate` across
        arms. **A29 guarantees the per-replicate verdict is a function of
        ``(scenario, seed)``; it says nothing about how many replicates an arm
        ran.** ``scripts/run_matrix.py --replicates N`` exists so one can run
        fewer, so a smoke pass of one arm beside a full pass of another leaves a
        perfectly sound ledger where the two cells report different rates -- and
        the first implementation called that an A29 breach and refused it.

        Here every seed both arms share agrees, and the short arm is a strict
        prefix of the long one, which is exactly what pairing produces.

        **The two arms' rates must actually differ for this test to
        discriminate**, and that is what the fixture is built around: the probe
        fires on seeds 0 and 1 and not on 2 or 3, so the four-replicate arm
        reports 0.5 where the two-replicate arm reports 1.0, while the two agree
        on every seed they share. Built from :func:`_passing` alone both arms
        report exactly 1.0 and 0.0, which the old implementation compared equal
        -- such a test passes before and after the fix and establishes nothing.
        """
        report = _summarise(
            (
                *_rows({**_passing(), FIRES: 0.5}, system="V7", replicates=4),
                *_rows({**_passing(), FIRES: 1.0}, system="B1", replicates=2),
            )
        )
        by_arm = {
            (cell.system, str(cell.scenario)): cell.probe_inadequate_rate
            for cell in report.cells
        }
        # The premise: the per-cell rates really do disagree.
        assert by_arm[("V7", FIRES)] == 0.5
        assert by_arm[("B1", FIRES)] == 1.0

        verdict = criterion_four_of(report)
        assert verdict.holds is True, (
            f"unequal replicate counts are not a breach, but the cells report {by_arm}"
        )
        assert verdict.fired_on_s11 is True
        # And the rendering says so rather than reporting a fault.
        assert "holds" in _criterion_line(render(report))

    def test_a46_render_reports_both_failed_clauses(self) -> None:
        """An instrument failing both clauses says so twice, not once.

        Found by ``/code-review``: the first rendering returned on the power
        clause and never reached the size clause, so a probe that was blind on
        S11 *and* fired on scenarios where the space is adequate rendered as
        blind alone. The criterion is a conjunction and the rendered line is the
        only human-readable surface it has, so losing a clause there loses it
        entirely.
        """
        both = {**_passing(), FIRES: 0.0, "S3": 1.0, "S5": 1.0}
        verdict = criterion_four_of(_report(both))
        assert verdict.fired_on_s11 is False
        assert verdict.false_positives == (ScenarioId("S3"), ScenarioId("S5"))

        line = _criterion_line(render(_report(both)))
        assert FIRES in line
        assert "S3" in line and "S5" in line

    def test_a46_render_survives_an_arm_disagreement(self) -> None:
        """A broken A29 guarantee does not suppress the D1-D6 table.

        Found by ``/code-review``: the first rendering called a function that
        raises, so one bad line took the whole report with it.
        ``scripts/report_matrix.py`` already wraps its
        :func:`~sciagent.eval.report.contrast` call for exactly this reason --
        its comment says a failure there "should not stop the table printing" --
        and criterion 4 had arrived without the same care.

        The fault is still reported. It is reported *as a line*.
        """
        report = _summarise(
            (
                *_rows(_passing(), system="V7", replicates=1),
                *_rows({**_passing(), FIRES: 0.0}, system="B1", replicates=1),
            )
        )
        text = render(report)

        line = _criterion_line(text)
        # A distinct marker, not a second "not evaluated": a partial report and a
        # broken harness are opposite findings and a CI grep must tell them
        # apart. `test_a46_a_partial_report_renders_and_says_it_was_not_evaluated`
        # asserts the other branch does *not* carry this.
        assert "A29 BREACH" in line
        assert "holds" not in line
        assert FIRES in line

        # The numbers the report exists for are still there.
        assert "d1" in text and "d6" in text
        for system in ("V7", "B1"):
            assert f"{system} / {FIRES}" in text

    def test_a46_agreeing_arms_project_to_one_rate(self) -> None:
        """The other side of clause 6: many arms agreeing is the ordinary case.

        Without this the refusal above is satisfied by an implementation that
        refuses every multi-arm report, which is every real one.
        """
        verdict = criterion_four_of(_report(_passing(), systems=("V1", "V7", "B1")))
        assert verdict.holds is True
        assert verdict.fired_on_s11 is True
