"""Acceptance test A37: the paired-seed design gets a paired analysis.

A37 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The paired-seed design deserves a paired analysis"*,
and reads:

    ``test_a37_paired_contrasts_report_the_paired_difference`` -- on constructed
    paired rows the report carries the within-seed difference and its interval,
    and refuses the paired reading when the seed sets differ.

The same entry's **Idea** is what the gate is a check on: *"When
``Contrast.paired`` is true, report the mean within-seed difference with its
interval alongside (not instead of) the two independent intervals ``contrast()``
reports today."*

What was wrong
--------------

:mod:`sciagent.eval.matrix` pairs seeds across arms by construction. A
replicate's seed is a function of the scenario and the replicate index **alone**,
never of the system, and ``docs/DECISIONS.md`` (2026-08-17, *"matrix seeds are
paired across arms, and never come from iteration order"*) records why: SPEC §9's
preregistered contrast asks whether V7 exceeds B4 on S11, and with per-system
seeds that comparison would be partly a comparison of *worlds*, at twenty draws
an arm.

That pairing was established at real cost and then discarded at analysis.
:func:`~sciagent.eval.report.contrast` summarised each arm independently and
compared two independent normal intervals, which throws away the fact that
replicate *i* of one arm ran the same world as replicate *i* of the other. The
within-seed difference is a strictly additional reading of the same ledger rows:
it records no new number, runs no cell, and collapses no dimension -- §8's
prohibition is on combining the six dimensions, and a paired difference on one
dimension is still one dimension.

Why the refusal is an absent reading and not an exception
---------------------------------------------------------

Conditioning on inadequacy detection filters each arm by its **own** flag, so two
arms can survive on overlapping-but-different seed sets;
:attr:`~sciagent.eval.report.Contrast.paired` exists to make that visible. The
entry asks for the paired difference *"alongside (not instead of)"* the two
independent intervals, so an unpaired matrix must still get the contrast it gets
today -- and ``tests/test_report.py``'s
``test_a_contrast_reports_whether_conditioning_kept_the_seeds_paired`` already
calls ``contrast()`` on deliberately crossed seed sets and reads ``paired`` off
the returned object. Raising would report *nothing* where two intervals are
reported now.

So ``paired_difference is None`` is the refusal, and no other field of the
contrast is disturbed by it.

What the tests establish
------------------------

``..._paired_contrasts_report_the_paired_difference`` is the gate's first clause
and carries its arithmetic: the point is the mean of the five within-seed
differences and the interval is the normal interval on that mean, both computed
here in plain Python rather than read back through the estimator under test. The
fixture is built so that the paired reading and the independent one **disagree in
their verdict** -- the two arms' intervals overlap while the paired difference
excludes zero. A fixture where they agreed would pass against an implementation
that merely differenced the two independent points, which is not a paired
analysis and would carry the wrong interval.

``..._the_independent_intervals_are_still_reported`` is the *"not instead of"*
half, on those same rows, and it asserts the arms' interval **bounds by value**.
An earlier draft asserted only that the four bounds were ``math.isfinite``, which
tests nothing: ``contrast()`` raises on a non-finite bound before it can return
one, so that draft was re-testing the function's own precondition and stayed
green against an implementation that narrowed both arms to the paired half-width.

``..._the_paired_difference_does_not_depend_on_row_order`` closes the other way
the reading can be wrong while looking right: pairing by row *position* rather
than by seed. The mean cannot tell the two apart -- positionally-paired
differences average to ``mean(T) - mean(C)`` under every permutation -- so the
interval on permuted rows is the only thing that discriminates.

``..._refuses_the_paired_reading_when_the_seed_sets_differ`` and
``..._refuses_the_paired_reading_when_conditioning_crossed_the_arms`` are the
gate's second clause. Both are needed and they are different cases: the first is
arms that never shared a world at all, the second is arms paired by construction
that conditioning pulled apart, and only the second can arise from a real
campaign.

``..._a_seed_carrying_two_readings_refuses_the_paired_reading`` guards the way
equal seed *sets* still fail to pair. :func:`~sciagent.eval.report._seeds_of`
deduplicates, so a seed appearing twice in one arm leaves ``paired`` true with no
fact of the matter about which of the two rows is that seed's reading -- and
picking one would be the report layer deciding by fiat, which is what
``_refuse_reseeded`` already declines to do one address over. It runs the
duplicate on **equal-length** arms as well as unequal ones, so that a guard on
the two arms' row counts -- a different check that happens to catch the unequal
case -- does not pass for it.

``..._a_non_finite_pair_is_dropped_rather_than_the_arm`` is D2's ordinary case:
``d2_held_out_predictive`` is ``-inf`` whenever the candidate ruled out something
that happens, and a paired difference that swallowed one would report five seeds
as ``nan``.

``..._the_contrast_block_prints_the_paired_difference`` is the wiring, through
``scripts/report_matrix.py --contrast`` as a person would run it. It is the first
test in the suite to take that flag down its success path, so it asserts both
renderings -- the reading and the refusal.
"""

from __future__ import annotations

import importlib.util
import math
import statistics
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

from environments.pointproc.outcomes import held_out_designs
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
from sciagent.eval.report import Contrast, MatrixReport, contrast, summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.verify.numerical import CONFIDENCE_LEVEL, Z_TWO_SIDED

# --------------------------------------------------------------------------
# Fixtures
#
# Self-contained, as every module under ``tests/acceptance/`` is: no test module
# in this suite imports another, and shared fixtures live in non-``test_``
# modules (``slice_tables.py``, ``baseline_runs.py``). ``tests/test_report.py``
# builds a ``CellReading`` the same way and says why it does not share
# ``tests/test_matrix.py``'s -- the fields each module varies are different ones,
# and here only D2, D3 and the inadequacy flag ever move.
# --------------------------------------------------------------------------

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

#: S11's real held-out battery. Not a stand-in here: the script test runs
#: ``report_matrix.py``, which derives the battery from the scenario itself, and
#: gate A43 refuses rows scored on any other.
BATTERY = held_out_designs()

PLATFORM = "Windows-11-x86_64"
NUMPY = "2.5.1"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "report_matrix.py"

#: The two arms of the worked fixture, on D3.
#:
#: Chosen so the two readings disagree: each arm spans a wide range, so the
#: independent intervals overlap, while every within-seed difference is small and
#: positive, so the paired difference excludes zero. That is the entry's power
#: claim made concrete, and it is what tells a paired analysis apart from a
#: subtraction of two independent points.
TREATMENT: tuple[float, ...] = (0.50, 0.70, 0.60, 0.80, 0.55)
COMPARATOR: tuple[float, ...] = (0.45, 0.55, 0.55, 0.65, 0.50)


def reading(
    *,
    d3: float = 0.5,
    d2: float = -2.0,
    inadequate: bool = True,
    probe_inadequate: bool = True,
) -> CellReading:
    """Return a ``CellReading`` varying only what this module varies.

    ``CellReading`` is constructible only through the whole vector -- the
    invariant made structural in ``eval/matrix.py`` -- so everything else is
    fixed, recognisable filler. ``probe_inadequate`` is the flag conditioning
    reads since gate A48; it defaults to firing so every fixture that does not
    vary it keeps its full conditioning population.
    """
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=1.0,
            d2_held_out_predictive=d2,
            d3_intervention_similarity=d3,
            d4_explanatory_coverage=0.25,
            d5_enabled_experiment_value=0.75,
            d6_complexity=12.0,
            n_held_out=len(BATTERY),
            n_comparison=1,
        ),
        score=ClosedWorldScore(
            truth_mass=Probability(0.5),
            log_score=-1.0,
            leading_mass=Probability(0.5),
            correct=True,
            identified=False,
        ),
        ppc_p_value=0.2,
        inadequate=inadequate,
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
        battery=battery_key(BATTERY),
    )


def arm(
    system: str,
    readings: Sequence[CellReading],
    *,
    seeds: Sequence[int] | None = None,
) -> tuple[LedgerEntry, ...]:
    """Return one ledger row per replicate of ``system`` on S11.

    ``seeds`` defaults to ``range(len(readings))``, which is the shape
    ``eval/matrix.py`` produces -- a seed is a function of the scenario and the
    replicate index alone, so both arms of a scenario run the same worlds.
    Overridable because what happens when they do not is the whole of this gate.
    """
    chosen = list(range(len(readings))) if seeds is None else list(seeds)
    cell = Cell(system, ScenarioId("S11"), len(readings))
    return tuple(
        LedgerEntry(
            key=cell_key(
                CellTask(cell=cell, replicate=index, seed=Seed(seed)),
                ADDRESS,
                battery=BATTERY,
            ),
            reading=FrozenDict[str, float](dict(entry.as_payload())),
            sequence=index,
        )
        for index, (seed, entry) in enumerate(zip(chosen, readings, strict=True))
    )


def _scenario_class(_name: ScenarioId) -> ScenarioClass:
    return "out_of_library"


def _battery(_name: ScenarioId) -> Sequence[ExperimentDesign]:
    return BATTERY


def report_of(*arms: Sequence[LedgerEntry]) -> MatrixReport:
    """Summarise the given arms with the provenance every report needs."""
    entries = tuple(row for one in arms for row in one)
    return summarise(
        entries,
        address=ADDRESS,
        scenario_class=_scenario_class,
        battery=_battery,
        platform=PLATFORM,
        numpy_version=NUMPY,
        grammar=GRAMMAR,
    )


def contrast_of(
    report: MatrixReport, *, dimension: str = "d3_intervention_similarity"
) -> Contrast:
    """SPEC §9's contrast -- V7 against B4 on S11 -- over ``report``."""
    return contrast(
        report,
        scenario=ScenarioId("S11"),
        treatment="V7",
        comparator="B4",
        dimension=dimension,
    )


def report_matrix() -> ModuleType:
    """Load ``scripts/report_matrix.py`` as a module.

    ``scripts/`` is not a package and is not on ``sys.path``, so reaching into it
    is an ``importlib`` load by path or a subprocess. ``tests/test_report.py``
    chose the subprocess and says why, and the end-to-end test below keeps that
    for the wiring. This is for ``_paired_line`` alone, which is a pure function
    of a ``Contrast``: calling it pins each of its three branches directly, where
    matching stdout can only reach the ones a ``--contrast`` run can produce --
    and that run's dimension is fixed to D3 by ``SPEC9_CONTRAST``, so the branch
    that needs a non-finite reading is not among them.
    """
    spec = importlib.util.spec_from_file_location("report_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worked_contrast() -> Contrast:
    """The contrast over :data:`TREATMENT` and :data:`COMPARATOR`, paired."""
    return contrast_of(
        report_of(
            arm("V7", [reading(d3=value) for value in TREATMENT]),
            arm("B4", [reading(d3=value) for value in COMPARATOR]),
        )
    )


class TestA37PairedContrastsReportThePairedDifference:
    """``docs/BACKLOG.md``: *the paired-seed design deserves a paired analysis*."""

    def test_a37_paired_contrasts_report_the_paired_difference(self) -> None:
        result = worked_contrast()
        assert result.paired is True

        paired = result.paired_difference
        assert paired is not None

        # Computed here rather than read back through the estimator under test.
        # ``statistics.variance`` is ddof=1, matching ``core.reductions.variance``;
        # it is a different implementation of the same quantity, which is the
        # point of asserting against it.
        differences = [
            treatment - comparator
            for treatment, comparator in zip(TREATMENT, COMPARATOR, strict=True)
        ]
        expected = sum(differences) / len(differences)
        half_width = Z_TWO_SIDED * math.sqrt(
            statistics.variance(differences) / len(differences)
        )

        assert paired.point == pytest.approx(expected)
        assert paired.low == pytest.approx(expected - half_width)
        assert paired.high == pytest.approx(expected + half_width)
        assert paired.level == CONFIDENCE_LEVEL
        assert paired.n_finite == len(TREATMENT)
        assert paired.n_non_finite == 0

        # The two readings disagree on this fixture, which is what makes it a
        # test of a paired analysis rather than of a subtraction: the independent
        # intervals overlap, and the paired difference excludes zero.
        assert result.overlaps is True
        assert paired.low > 0.0

    def test_a37_the_independent_intervals_are_still_reported(self) -> None:
        # The entry asks for the paired reading "alongside (not instead of)" the
        # two independent intervals. Every field ``contrast()`` reported before
        # this gate still reports, and reports the same numbers.
        #
        # The **bounds** are asserted by value, not by `math.isfinite`. Finiteness
        # is not a property this could lose: `contrast()` raises at report.py's
        # "has no usable interval" check before it can return a non-finite one, so
        # a test asserting only that re-tests the function's own precondition and
        # goes green against an implementation that narrowed both arms to the
        # paired half-width -- which is "instead of", exactly what this guards.
        # That is not hypothetical: it puts the treatment arm at
        # [0.581991, 0.678009] against the [0.524453, 0.735547] below, and it is
        # the *biased* direction, since §12 criterion 5 is read off arm
        # non-overlap and report.py refuses to clip intervals for that reason.
        result = worked_contrast()

        for summary, values in (
            (result.treatment, TREATMENT),
            (result.comparator, COMPARATOR),
        ):
            point = sum(values) / len(values)
            half_width = Z_TWO_SIDED * math.sqrt(
                statistics.variance(values) / len(values)
            )
            assert summary.point == pytest.approx(point)
            assert summary.low == pytest.approx(point - half_width)
            assert summary.high == pytest.approx(point + half_width)
            assert summary.n_finite == len(values)
            assert summary.n_non_finite == 0

        assert result.exceeds is True
        assert result.overlaps is True

    def test_a37_the_paired_difference_does_not_depend_on_row_order(self) -> None:
        # Pairing is **by seed**, and the cheapest wrong implementation --
        # ``zip(treatment_rows, comparator_rows)`` -- is by position. The mean
        # cannot tell them apart: positionally-paired differences average to
        # ``mean(T) - mean(C)`` under every permutation, so only the interval
        # discriminates, and only on rows that did not arrive in seed order.
        #
        # ``summarise`` accepts any ``Iterable`` and report.py states that
        # ``MatrixReport.rows`` retains the order given while everything rendered
        # is order-independent. A paired difference is a rendered number, so the
        # permutation below must not move it -- under positional pairing it runs
        # to [-0.053359, 0.233359] and stops excluding zero, which is a flipped
        # verdict from a re-ordered tuple.
        comparator = arm("B4", [reading(d3=value) for value in COMPARATOR])
        shuffled = contrast_of(
            report_of(
                arm("V7", [reading(d3=value) for value in TREATMENT]),
                tuple(comparator[index] for index in (3, 1, 2, 4, 0)),
            )
        )

        expected = worked_contrast().paired_difference
        assert expected is not None
        paired = shuffled.paired_difference
        assert paired is not None
        assert paired.point == pytest.approx(expected.point)
        assert paired.low == pytest.approx(expected.low)
        assert paired.high == pytest.approx(expected.high)

    def test_a37_refuses_the_paired_reading_when_the_seed_sets_differ(self) -> None:
        # Arms that never shared a world at all. The contrast still reports --
        # refusing the paired reading is an absent reading, not an exception.
        result = contrast_of(
            report_of(
                arm("V7", [reading(d3=value) for value in TREATMENT], seeds=range(5)),
                arm(
                    "B4",
                    [reading(d3=value) for value in COMPARATOR],
                    seeds=range(10, 15),
                ),
            )
        )
        assert result.paired is False
        assert result.paired_difference is None
        assert result.treatment.point == pytest.approx(sum(TREATMENT) / len(TREATMENT))
        assert result.comparator.point == pytest.approx(
            sum(COMPARATOR) / len(COMPARATOR)
        )
        assert math.isfinite(result.treatment.low)
        assert math.isfinite(result.comparator.high)

    def test_a37_refuses_the_paired_reading_when_conditioning_crossed_the_arms(
        self,
    ) -> None:
        # Until gate A48 this was a case a real campaign could reach:
        # conditioning read each arm's own whole-record flag, which could pull
        # the paired seeds apart. The filter now reads the Stage A probe,
        # arm-invariant by A29, so on any real ledger the sets agree by
        # construction -- and what this pins is that a hand-built report whose
        # probe flags *do* differ by arm (the A29-breach shape, refused on the
        # criterion-4 path by `_probe_counts`) is reported rather than
        # silently repaired: the probe flags V7 on seeds 0,1,2 and B4 on
        # 1,2,3, so both arms keep three replicates and nothing in the counts
        # betrays it -- only the seeds do, and the paired reading is refused.
        result = contrast_of(
            report_of(
                arm(
                    "V7",
                    [reading(d3=0.9, probe_inadequate=True)] * 3
                    + [reading(d3=0.9, probe_inadequate=False)],
                ),
                arm(
                    "B4",
                    [reading(d3=0.1, probe_inadequate=False)]
                    + [reading(d3=0.1, probe_inadequate=True)] * 3,
                ),
            )
        )
        assert result.treatment.n_finite == result.comparator.n_finite == 3
        assert result.paired is False
        assert result.paired_difference is None

    def test_a37_a_seed_carrying_two_readings_refuses_the_paired_reading(
        self,
    ) -> None:
        # Equal seed *sets* do not imply one reading per seed: ``_seeds_of``
        # deduplicates. An arm carrying two rows at seed 2 leaves ``paired`` true
        # with no fact of the matter about which row seed 2 contributes to the
        # difference. The report layer will not pick.
        #
        # **Both arms are the same length**, and that is the whole point of the
        # first case. An implementation guarding on ``len(treatment) !=
        # len(comparator)`` -- which is the guard that suggests itself, and which
        # is not the same check -- passes the second case and fails here: on two
        # arms both at seeds (0,1,2,2) it would report four "pairs" drawn from
        # three seeds, with an interval to match.
        for treatment_seeds, comparator_seeds, comparator_values in (
            ((0, 1, 2, 2), (0, 1, 2, 2), (0.4, 0.5, 0.6, 0.7)),
            ((0, 1, 2, 2), (0, 1, 2), (0.4, 0.5, 0.6)),
        ):
            result = contrast_of(
                report_of(
                    arm(
                        "V7",
                        [reading(d3=value) for value in (0.5, 0.6, 0.7, 0.8)],
                        seeds=treatment_seeds,
                    ),
                    arm(
                        "B4",
                        [reading(d3=value) for value in comparator_values],
                        seeds=comparator_seeds,
                    ),
                )
            )
            assert result.treatment_seeds == result.comparator_seeds
            assert result.paired is True
            assert result.paired_difference is None

    def test_a37_a_non_finite_pair_is_dropped_rather_than_the_arm(self) -> None:
        # ``d2_held_out_predictive`` is ``-inf`` whenever the candidate ruled out
        # something that happens -- ordinary, not corruption. The pair it belongs
        # to cannot yield a difference, so it is excluded and counted, exactly as
        # ``_summarise`` already does for an arm.
        #
        # **One non-finite in each arm, at different seeds**, which is what makes
        # this a test of a seed count rather than of an arm's. With the only
        # ``-inf`` in the treatment arm the paired count and that arm's count are
        # both 4, and the assertion passes against an implementation that reported
        # either -- so the field docstring's claim that ``n_finite`` "is not
        # generally either arm's count" would go unpinned. Here the two arms are 4
        # apiece and the paired reading is 3.
        treatment = (-math.inf, -1.0, -3.0, -2.5, -2.0)
        comparator = (-2.5, -1.8, -3.4, -2.7, -math.inf)
        result = contrast_of(
            report_of(
                arm("V7", [reading(d2=value) for value in treatment]),
                arm("B4", [reading(d2=value) for value in comparator]),
            ),
            dimension="d2_held_out_predictive",
        )

        paired = result.paired_difference
        assert paired is not None
        assert paired.n_finite == 3
        assert paired.n_non_finite == 2

        # Seeds 1, 2 and 3 -- the only ones finite in both arms.
        differences = [
            left - right
            for left, right in zip(treatment[1:4], comparator[1:4], strict=True)
        ]
        assert paired.point == pytest.approx(sum(differences) / len(differences))

        # Both arms are 4, and the paired reading is 3. No implementation
        # reporting an arm's count can satisfy all three.
        assert result.treatment.n_finite == 4
        assert result.comparator.n_finite == 4
        assert result.treatment.n_non_finite == 1
        assert result.comparator.n_non_finite == 1

    def test_a37_a_paired_reading_with_no_finite_pair_is_empty_not_refused(
        self,
    ) -> None:
        # Each arm has two finite readings, so `contrast()` does not raise. But no
        # single seed is finite in *both*, so there is no pair to difference. The
        # arms did pair; the reading is empty. `None` would collapse those two
        # into one and the block would print "refused -- the arms ran on
        # different seeds", which the row above it contradicts.
        # Negative, because D2 is a mean log2 probability and a positive value is
        # not a state a real candidate can be in. `DimensionVector` does not
        # validate it, so a positive would pass -- and a fixture nothing can
        # produce is a worse test of an ordinary case.
        treatment = (-1.0, -2.0, -math.inf, -math.inf)
        comparator = (-math.inf, -math.inf, -3.0, -4.0)
        result = contrast_of(
            report_of(
                arm("V7", [reading(d2=value) for value in treatment]),
                arm("B4", [reading(d2=value) for value in comparator]),
            ),
            dimension="d2_held_out_predictive",
        )

        assert result.paired is True
        assert result.treatment.n_finite == result.comparator.n_finite == 2
        paired = result.paired_difference
        assert paired is not None
        assert paired.n_finite == 0
        assert paired.n_non_finite == 4
        assert math.isnan(paired.point)
        assert math.isnan(paired.low) and math.isnan(paired.high)

    def test_a37_the_block_renders_each_state_of_the_row(self) -> None:
        # Four states, three renderings, none of them a bare `nan` in the shape of
        # a figure. The two arm rows above this one are guaranteed finite --
        # `contrast()` raises rather than return an unusable interval -- so a
        # reader learns to read that column as always-numbers, and this row is the
        # one that can break the habit.
        line = report_matrix()._paired_line

        reading_row: str = line(worked_contrast())
        assert "0.0900" in reading_row
        assert "0.0420" in reading_row and "0.1380" in reading_row
        assert "n=5" in reading_row

        unpaired: str = line(
            contrast_of(
                report_of(
                    arm(
                        "V7",
                        [reading(d3=value) for value in TREATMENT],
                        seeds=range(5),
                    ),
                    arm(
                        "B4",
                        [reading(d3=value) for value in COMPARATOR],
                        seeds=range(10, 15),
                    ),
                )
            )
        )
        assert "refused" in unpaired and "different seeds" in unpaired

        # Seeds match, so blaming differing seeds here would print a claim the
        # `same seeds (paired) True` row directly above it contradicts.
        duplicated: str = line(
            contrast_of(
                report_of(
                    arm(
                        "V7",
                        [reading(d3=value) for value in (0.5, 0.6, 0.7, 0.8)],
                        seeds=(0, 1, 2, 2),
                    ),
                    arm(
                        "B4",
                        [reading(d3=value) for value in (0.4, 0.5, 0.6)],
                        seeds=(0, 1, 2),
                    ),
                )
            )
        )
        assert "refused" in duplicated and "two readings" in duplicated
        assert "different seeds" not in duplicated

        def rendered_pairs(
            treatment: tuple[float, ...], comparator: tuple[float, ...]
        ) -> str:
            # Bound through a declared `str` because `line` comes off a module
            # loaded by path, so it is typed `Any` and returning it directly
            # trips mypy's `warn_return_any`.
            rendered: str = line(
                contrast_of(
                    report_of(
                        arm("V7", [reading(d2=value) for value in treatment]),
                        arm("B4", [reading(d2=value) for value in comparator]),
                    ),
                    dimension="d2_held_out_predictive",
                )
            )
            return rendered

        empty = rendered_pairs(
            (-1.0, -2.0, -math.inf, -math.inf), (-math.inf, -math.inf, -3.0, -4.0)
        )
        assert "no interval" in empty and "0 of 4" in empty
        # Not a refusal -- the arms paired -- and not a `nan` dressed as a figure.
        assert "refused" not in empty
        assert "nan" not in empty

        # **One finite pair, not zero**, and this case is why it is here. The row
        # reports `n_finite of n_finite + n_non_finite`, and at zero those two
        # readings coincide -- a version printing `n_non_finite` alone renders "0
        # of 4" for the fixture above and passes. Only a state with a finite pair
        # in it separates them: here seed 0 is finite in both arms and nothing
        # else is, so the count is 1 of 4 and the wrong version says 1 of 3.
        single = rendered_pairs(
            (-2.0, -3.0, -math.inf, -math.inf), (-2.5, -math.inf, -3.5, -math.inf)
        )
        assert "no interval" in single and "1 of 4" in single
        assert "nan" not in single

        # Width: the block's data rows run to 65 characters and its prose wraps at
        # 68. An earlier refusal string reached 90 and wrapped in a reader's
        # terminal, splitting one row across two lines in a block read by column.
        for rendered in (reading_row, unpaired, duplicated, empty, single):
            assert len(rendered) <= 68, (len(rendered), rendered)

    def test_a37_the_contrast_block_prints_the_paired_difference(
        self, tmp_path: Path
    ) -> None:
        # The wiring, run as a person would run it. Both renderings, because the
        # refusal is a branch of its own and a block that silently dropped the row
        # would look identical to one that never carried it.
        #
        # The gate asks for the difference "and its interval", and this is the
        # form a person actually reads it in -- so the interval is asserted on the
        # block too, not only on the object. Every other figure in that block
        # prints as `point [low, high]`, and a row printing the point alone would
        # be the one number there a reader could not weigh. All three are matched
        # on the *same line*, so a coincidental four-decimal match elsewhere in
        # the table cannot satisfy this.
        differences = [
            treatment - comparator
            for treatment, comparator in zip(TREATMENT, COMPARATOR, strict=True)
        ]
        point = sum(differences) / len(differences)
        half_width = Z_TWO_SIDED * math.sqrt(
            statistics.variance(differences) / len(differences)
        )

        paired = self._run(self._ledger(tmp_path / "paired.sqlite", seeds=range(5)))
        assert paired.returncode == 0, paired.stderr
        printed = self._row(paired.stdout)
        for figure in (point, point - half_width, point + half_width):
            assert f"{figure:.4f}" in printed, printed

        crossed = self._run(
            self._ledger(tmp_path / "crossed.sqlite", seeds=range(10, 15))
        )
        assert crossed.returncode == 0, crossed.stderr
        assert "refused" in self._row(crossed.stdout)

    @staticmethod
    def _row(stdout: str) -> str:
        """Return the block's single ``paired difference`` row.

        Selected on the label rather than on the phrase: the block's closing prose
        explains what the row means and so contains the phrase too, and matching
        that would let the data row go missing without failing.
        """
        rows = [
            line
            for line in stdout.splitlines()
            if line.strip().startswith("paired difference")
        ]
        assert len(rows) == 1, stdout
        return rows[0]

    # ----------------------------------------------------------------------
    # Script harness. ``scripts/`` is not a package and is not on ``sys.path``,
    # so this is a subprocess rather than an ``importlib`` load by path --
    # matching ``tests/test_report.py``, and exercising argparse and the exit
    # status besides.
    # ----------------------------------------------------------------------

    def _ledger(self, path: Path, *, seeds: Sequence[int]) -> Path:
        """Write a V7 and a B4 arm on S11 to a ledger at ``path``."""
        entries = arm(
            "V7", [reading(d3=value) for value in TREATMENT], seeds=range(5)
        ) + arm("B4", [reading(d3=value) for value in COMPARATOR], seeds=seeds)
        with CampaignLedger.open(path) as ledger:
            for entry in entries:
                ledger.append(entry.key, reading=dict(entry.reading))
        return path

    def _run(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(path),
                "--platform",
                PLATFORM,
                "--numpy",
                NUMPY,
                "--grammar",
                str(GRAMMAR),
                "--env-version",
                str(ADDRESS.env_version),
                "--data-version",
                str(ADDRESS.data_version),
                "--metric-version",
                str(ADDRESS.metric_version),
                "--contrast",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
            check=False,
        )
