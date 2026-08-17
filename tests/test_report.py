"""Rendering the §9 matrix as a D1-D6 vector table, without collapsing it.

SPEC §11 item 15 carries **no acceptance criterion**, so nothing here is named
``test_aN_``; ``scripts/status.py`` derives gate coverage from that convention and
crediting these to a gate would report a contract that does not exist. This is
the convention ``tests/test_matrix.py`` states for the same reason.

What is checked instead, in the order the module's risks run:

- **Nothing collapses.** §8 says the six dimensions are "reported separately,
  never collapsed into one number", and a report layer is the one place that
  prohibition can quietly fail. No type here carries a total, a mean of the six or
  a rank, and the rendered table has no such column.
- **Rows are selected by address, never "one row per cell".** The ledger returns
  every row whatever partition or version it came from, and says in as many words
  that selecting is the report layer's job.
- **Non-finite readings are counted, not swallowed.** ``-inf`` and ``nan`` are
  ordinary here -- B1's ``log_score`` is ``-inf`` whenever the truth got zero
  mass -- and a mean that eats one reports the whole cell as ``-inf``.
- **Provenance is refused rather than defaulted.** The matrix skill requires the
  platform and the grammar wherever these numbers appear, and the ledger stores
  neither.
- **The preregistered contrast is the one §9 states**, conditional on inadequacy
  detection, and it names its comparator.

The ledger rows are synthesised. A test that ran real cells would be minutes of
simulation asserting nothing this file is about.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest

import sciagent.eval.report as report_module
from environments.pointproc.matrix import SPEC9_CONTRAST
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
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    cell_key,
)
from sciagent.eval.report import (
    DIMENSIONS,
    DimensionSummary,
    MatrixReport,
    Preregistration,
    contrast,
    render,
    summarise,
)
from sciagent.eval.scenarios import SCENARIO_CLASSES, ScenarioClass
from sciagent.eval.scoring import ClosedWorldScore, DimensionVector
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.verify.numerical import CONFIDENCE_LEVEL, Z_TWO_SIDED

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

PLATFORM = "Windows-11-x86_64"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")

#: The CLI in front of the report layer. Run as a child process; see
#: :meth:`TestTheScriptPrintsWhatTheLedgerHolds._run`.
SCRIPT = Path(__file__).parent.parent / "scripts" / "report_matrix.py"

#: Every scenario class §8 reads under the proper score rather than under the
#: vector. :func:`~sciagent.eval.scoring.primary_dimension` maps these to
#: ``None``; the two below it are the ones with a primary dimension.
CLOSED_WORLD: tuple[ScenarioClass, ...] = (
    "single",
    "confounded",
    "null",
    "non_identifiable",
    "garden_path",
)


def reading(**overrides: float) -> CellReading:
    """Return a ``CellReading`` carrying stated numbers.

    ``CellReading`` is constructible only through the whole vector -- the
    invariant made structural in ``eval/matrix.py`` -- so a test wanting a
    recognisable payload builds all of it. Same shape as
    ``tests/test_matrix.py``'s ``stub_reading``, kept separate because the fields
    this file varies are different ones.
    """
    values: dict[str, float] = {
        "d1": 1.0,
        "d2": -2.0,
        "d3": 0.5,
        "d4": 0.25,
        "d5": 0.75,
        "d6": 12.0,
        "log_score": -1.0,
        "truth_mass": 0.5,
        "inadequate": 0.0,
        "correct": 1.0,
        "identified": 0.0,
        "experiments": 8.0,
        **overrides,
    }
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=values["d1"],
            d2_held_out_predictive=values["d2"],
            d3_intervention_similarity=values["d3"],
            d4_explanatory_coverage=values["d4"],
            d5_enabled_experiment_value=values["d5"],
            d6_complexity=values["d6"],
            n_held_out=3,
        ),
        score=ClosedWorldScore(
            truth_mass=Probability(values["truth_mass"]),
            log_score=values["log_score"],
            leading_mass=Probability(0.5),
            correct=bool(values["correct"]),
            identified=bool(values["identified"]),
        ),
        ppc_p_value=0.2,
        inadequate=bool(values["inadequate"]),
        experiments=int(values["experiments"]),
        structural_distance=1.0,
    )


def rows(
    system: str,
    scenario: str,
    readings: Sequence[CellReading],
    *,
    address: CampaignAddress = ADDRESS,
) -> tuple[LedgerEntry, ...]:
    """Return one ledger entry per replicate, at ``address``, without a ledger.

    :class:`~sciagent.registry.ledger.LedgerEntry` is a value type, so a report
    can be summarised from entries that were never stored. That keeps these tests
    off sqlite for everything except the two that are actually about selection
    across addresses.
    """
    cell = Cell(system, ScenarioId(scenario), len(readings))
    return tuple(
        LedgerEntry(
            key=cell_key(
                CellTask(cell=cell, replicate=index, seed=Seed(index)), address
            ),
            reading=FrozenDict[str, float](dict(entry.as_payload())),
            sequence=index,
        )
        for index, entry in enumerate(readings)
    )


def report_of(
    entries: Iterable[LedgerEntry],
    *,
    classes: Mapping[str, ScenarioClass] | None = None,
    address: CampaignAddress = ADDRESS,
) -> MatrixReport:
    """Summarise ``entries`` with the provenance every report needs."""
    known: dict[str, ScenarioClass] = dict(classes or {})
    fallback: ScenarioClass = "single"

    def scenario_class(scenario: ScenarioId) -> ScenarioClass:
        return known.get(str(scenario), fallback)

    return summarise(
        tuple(entries),
        address=address,
        scenario_class=scenario_class,
        platform=PLATFORM,
        grammar=GRAMMAR,
    )


# --------------------------------------------------------------------------
# §8's prohibition, which is what this layer exists to hold
# --------------------------------------------------------------------------

#: Names a collapsed figure would plausibly be given. Checked as a *substring*
#: match against every field of every type the module exports, so ``total``
#: catches ``total_score`` and ``score_total`` alike.
#:
#: A heuristic, not a proof: a collapsed figure named ``headline`` would pass it.
#: The structural guards are :data:`~sciagent.eval.report.DIMENSIONS` being a
#: six-tuple and no arithmetic crossing two dimensions; this is a tripwire over
#: those.
FORBIDDEN = ("total", "overall", "rank", "combined", "aggregate", "composite")

#: Every dataclass :mod:`sciagent.eval.report` exports, **derived from**
#: ``__all__`` rather than listed by hand.
#:
#: Listing them by hand is what went wrong the first time: the list held three of
#: the five exported types, so `Contrast` and `Preregistration` were unchecked, and
#: a mutation test that gave `Contrast` a `total_score` field and an `overall_rank`
#: property passed the whole file. Deriving it means a type exported later is
#: covered without anybody remembering, which is what the module docstring claims.
EXPORTED_TYPES = tuple(
    sorted(
        (
            candidate
            for candidate in (
                getattr(report_module, name) for name in report_module.__all__
            )
            if isinstance(candidate, type) and dataclasses.is_dataclass(candidate)
        ),
        key=lambda kind: kind.__name__,
    )
)


class TestNothingCollapsesTheSixDimensions:
    def test_the_derived_type_list_covers_every_exported_dataclass(self) -> None:
        # Guards the guard. If __all__ gains a dataclass and this derivation
        # stops seeing it, the check below silently covers less than it claims.
        assert {kind.__name__ for kind in EXPORTED_TYPES} == {
            "CellSummary",
            "Contrast",
            "DimensionSummary",
            "MatrixReport",
            "Preregistration",
        }

    @pytest.mark.parametrize("kind", EXPORTED_TYPES, ids=lambda k: k.__name__)
    def test_no_type_carries_a_collapsed_figure(self, kind: type) -> None:
        # The same check tests/test_scoring.py makes of DimensionVector, extended
        # to the layer that renders it. §8 forbids one number; a field named for
        # one is how that would arrive. Properties are included as well as fields,
        # since a collapsed figure is as easily computed as stored.
        named = {field.name for field in dataclasses.fields(kind)} | {
            name for name in vars(kind) if not name.startswith("_")
        }
        offending = sorted(
            name for name in named for bad in FORBIDDEN if bad in name.lower()
        )
        assert offending == []

    def test_the_rendered_table_has_no_total_column(self) -> None:
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        header = next(
            line for line in text.splitlines() if "d1" in line.lower() and "d6" in line
        )
        assert [bad for bad in FORBIDDEN if bad in header.lower()] == []

    def test_the_rendered_table_carries_all_six_dimensions(self) -> None:
        # The other half of the same requirement: §8 says report D1 through D6 as
        # a vector *in all cases*, so a report that quietly drops one is as wrong
        # as one that sums them.
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        for index in range(1, 7):
            assert f"d{index}" in text.lower()


# --------------------------------------------------------------------------
# Selection is by address
# --------------------------------------------------------------------------


class TestRowsAreSelectedByAddress:
    def test_a_re_addressed_cell_leaves_both_rows_and_only_one_is_read(self) -> None:
        # The trap eval/matrix.py and registry/ledger.py both warn about: under
        # the fourth invariant a cell whose inputs changed is an *append* at a new
        # address, so the stale row is still there. A report that assumed one row
        # per cell would average the two.
        bumped = ADDRESS.at(metric_version=MetricVersion("1.3.0"))
        stale = rows("V7", "S11", [reading(d3=0.1)] * 4)
        fresh = rows("V7", "S11", [reading(d3=0.9)] * 4, address=bumped)

        report = report_of(stale + fresh, address=bumped)
        assert len(report.cells) == 1
        assert report.cells[0].dimensions["d3_intervention_similarity"].point == 0.9
        assert report.cells[0].dimensions["d3_intervention_similarity"].n_finite == 4

    def test_rows_from_another_partition_are_not_pooled(self) -> None:
        # registry/ledger.py records this as a stated limitation of the store:
        # entries() returns every row whatever pool it came from, and selecting
        # by partition is the report layer's job. This is that job being done.
        test_rows = rows(
            "V7",
            "S11",
            [reading(d3=0.1)] * 4,
            address=ADDRESS.at(partition=DataPartition.TEST),
        )
        dev_rows = rows("V7", "S11", [reading(d3=0.9)] * 4)

        report = report_of(test_rows + dev_rows)
        assert report.cells[0].dimensions["d3_intervention_similarity"].n_finite == 4
        assert report.cells[0].dimensions["d3_intervention_similarity"].point == 0.9

    def test_rows_of_another_matrix_are_not_read(self) -> None:
        entries = rows("V7", "S11", [reading()] * 4)
        foreign = tuple(
            dataclasses.replace(
                entry,
                key=dataclasses.replace(
                    entry.key,
                    config=type(entry.key.config)(
                        {**dict(entry.key.config), "matrix": "spec9/2"}
                    ),
                ),
            )
            for entry in entries
        )
        with pytest.raises(MalformedDesignError, match="no row"):
            report_of(foreign)

    def test_a_report_over_no_matching_row_raises(self) -> None:
        # An empty report is indistinguishable from a matrix that ran and
        # produced nothing, which is the one reading nobody should reach by
        # accident.
        with pytest.raises(MalformedDesignError, match="no row"):
            report_of(())

    def test_a_reseeded_campaign_is_refused_rather_than_pooled(self) -> None:
        # The hole an address-only filter leaves. A cell's content address covers
        # its seed; CampaignAddress does not. So a campaign re-run after its
        # scenario-seed table changed appends a second row per replicate whose
        # config is *identical*, and no version comparison can tell them apart.
        #
        # Measured before the guard existed: three replicates at 0.10 and three
        # re-seeded at 0.90 came back as one cell of six replicates with a point
        # estimate of 0.5000 and an interval spanning both, nothing raised. The
        # replicate count looked like a fuller campaign rather than a broken one,
        # which is what makes silence here worse than a wrong number.
        cell = Cell("V7", ScenarioId("S11"), 3)
        original = rows("V7", "S11", [reading(d3=0.10)] * 3)
        reseeded = tuple(
            LedgerEntry(
                key=cell_key(
                    CellTask(cell=cell, replicate=index, seed=Seed(900 + index)),
                    ADDRESS,
                ),
                reading=row.reading,
                sequence=row.sequence + 10,
            )
            for index, row in enumerate(rows("V7", "S11", [reading(d3=0.90)] * 3))
        )
        # Identical config, different digest: nothing but the seed separates them.
        assert dict(original[0].key.config) == dict(reseeded[0].key.config)
        assert original[0].digest != reseeded[0].digest

        with pytest.raises(MalformedDesignError, match="two rows at this address"):
            report_of(original + reseeded)

        # Each seed set alone is a perfectly good report.
        assert report_of(original).cells[0].replicates == 3
        assert report_of(reseeded).cells[0].replicates == 3

        # And the refusal's *text* does not depend on the order the rows arrived
        # in. `entries` is an Iterable, so a caller merging two sources could
        # otherwise get two different exception strings for one situation -- the
        # message names both seeds, so it has to name them canonically.
        messages = set()
        for order in (original + reseeded, reseeded + original):
            with pytest.raises(MalformedDesignError) as raised:
                report_of(order)
            messages.add(str(raised.value))
        assert len(messages) == 1
        assert "seeds 0 and 900" in messages.pop()

    def test_selection_survives_a_real_ledger(self) -> None:
        # Everything above builds LedgerEntry directly. This one goes through
        # sqlite, so the float.hex round trip and the config encoding are in the
        # path that a real report would use.
        bumped = ADDRESS.at(metric_version=MetricVersion("1.3.0"))
        with CampaignLedger.in_memory() as ledger:
            for entry in rows("V7", "S11", [reading(d3=0.1)] * 3):
                ledger.append(entry.key, reading=dict(entry.reading))
            for entry in rows("V7", "S11", [reading(d3=0.9)] * 3, address=bumped):
                ledger.append(entry.key, reading=dict(entry.reading))
            assert ledger.count() == 6
            report = report_of(ledger.entries(), address=bumped)
        assert report.cells[0].dimensions["d3_intervention_similarity"].point == 0.9


# --------------------------------------------------------------------------
# Intervals
# --------------------------------------------------------------------------


class TestIntervalsAreNormalAndUnclipped:
    def test_the_point_and_half_width_are_the_normal_ones(self) -> None:
        values = [0.1, 0.2, 0.3, 0.4]
        report = report_of(rows("V7", "S11", [reading(d3=v) for v in values]))
        summary = report.cells[0].dimensions["d3_intervention_similarity"]

        mean = math.fsum(values) / len(values)
        variance = math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1)
        half_width = Z_TWO_SIDED * math.sqrt(variance / len(values))

        assert summary.point == pytest.approx(mean, abs=1e-15)
        assert summary.low == pytest.approx(mean - half_width, abs=1e-15)
        assert summary.high == pytest.approx(mean + half_width, abs=1e-15)
        assert summary.level == CONFIDENCE_LEVEL

    def test_an_interval_is_reported_past_the_dimension_bound(self) -> None:
        # D3 is bounded in [0, 1], and at twenty replicates a normal interval on
        # a mean near the ceiling runs past it. Reported as computed: clipping
        # would *narrow* the interval, and a narrower interval makes §12
        # criterion 5's "non-overlapping 95% interval" easier to satisfy -- it
        # would bias the preregistered contrast toward the claim.
        #
        # Mean 0.99, standard error 0.01, so the upper bound is about 1.0096.
        values = [0.96, 1.0, 1.0, 1.0]
        report = report_of(rows("V7", "S11", [reading(d3=v) for v in values]))
        summary = report.cells[0].dimensions["d3_intervention_similarity"]
        assert summary.point == pytest.approx(0.99)
        assert summary.high > 1.0

    def test_a_single_replicate_has_a_point_but_no_interval(self) -> None:
        # A variance needs two observations. One replicate is a reading, not an
        # estimate, and nan bounds say so rather than a zero-width interval
        # claiming certainty.
        report = report_of(rows("V7", "S11", [reading(d3=0.4)]))
        summary = report.cells[0].dimensions["d3_intervention_similarity"]
        assert summary.point == 0.4
        assert summary.n_finite == 1
        assert math.isnan(summary.low) and math.isnan(summary.high)


# --------------------------------------------------------------------------
# Non-finite readings
# --------------------------------------------------------------------------


class TestNonFiniteReadingsAreCountedNotSwallowed:
    def test_one_infinite_replicate_does_not_take_the_cell_with_it(self) -> None:
        # D2 is -inf when the candidate ruled out something that happens. fsum
        # over that returns -inf for the whole cell and the variance returns nan,
        # so an interval would be [nan, nan] -- twenty replicates reported as no
        # measurement because one of them was informative.
        readings = [reading(d2=-1.0)] * 5 + [reading(d2=-math.inf)]
        report = report_of(rows("V7", "S11", readings))
        summary = report.cells[0].dimensions["d2_held_out_predictive"]
        assert (summary.n_finite, summary.n_non_finite) == (5, 1)
        assert summary.point == -1.0

    def test_a_cell_with_no_finite_replicate_is_nan_and_not_zero(self) -> None:
        readings = [reading(d2=math.nan)] * 4
        report = report_of(rows("V7", "S11", readings))
        summary = report.cells[0].dimensions["d2_held_out_predictive"]
        assert (summary.n_finite, summary.n_non_finite) == (0, 4)
        assert math.isnan(summary.point)
        assert math.isnan(summary.low) and math.isnan(summary.high)

    def test_the_closed_world_score_counts_its_own_infinities(self) -> None:
        # B1's ordinary case on anything but S9: it holds only the null, so the
        # truth gets zero mass and log_score is -inf. A report that swallowed
        # those would say B1 scored -inf everywhere.
        readings = [reading(log_score=-2.0)] * 3 + [reading(log_score=-math.inf)]
        report = report_of(rows("B1", "S11", readings))
        summary = report.cells[0].log_score
        assert (summary.n_finite, summary.n_non_finite) == (3, 1)
        assert summary.point == -2.0

    def test_a_non_finite_reading_is_named_in_the_rendered_table(self) -> None:
        # Named per dimension, not left as a column of counts to subtract: an
        # excluded -inf on D2 means something quite different from one on D4, so
        # which dimension lost replicates is the part a reader has to act on.
        readings = [reading(d2=-1.0)] * 5 + [reading(d2=-math.inf)]
        text = render(report_of(rows("V7", "S11", readings)))
        assert "non-finite: d2_held_out_predictive 1/6" in text

    def test_a_cell_with_every_reading_finite_says_nothing_about_it(self) -> None:
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        assert "non-finite: " not in text


# --------------------------------------------------------------------------
# Rates, which are means of booleans and not intervals on them
# --------------------------------------------------------------------------


class TestRatesAreReportedAsRates:
    def test_the_inadequacy_rate_is_the_fraction_that_detected(self) -> None:
        # §9's contrast is conditional on inadequacy detection, so this is the
        # conditioning variable and has to survive into the report.
        readings = [reading(inadequate=1.0)] * 3 + [reading(inadequate=0.0)]
        report = report_of(rows("B1", "S11", readings))
        assert report.cells[0].inadequate_rate == 0.75

    def test_correct_and_identified_are_separate_rates(self) -> None:
        readings = [reading(correct=1.0, identified=1.0)] * 2 + [
            reading(correct=1.0, identified=0.0)
        ] * 2
        report = report_of(rows("V1", "S1", readings))
        assert report.cells[0].correct_rate == 1.0
        assert report.cells[0].identified_rate == 0.5


# --------------------------------------------------------------------------
# Provenance: refused rather than defaulted
# --------------------------------------------------------------------------


class TestProvenanceIsRefusedRatherThanDefaulted:
    def test_a_report_without_a_platform_raises(self) -> None:
        # docs/DECISIONS.md records a measured Windows/Ubuntu divergence and that
        # the registry content-addresses with no platform term, so the ledger
        # cannot supply this and the report must be told. The matrix skill
        # requires it wherever these numbers are reported; refusing is how that
        # is held by construction rather than by remembering.
        with pytest.raises(MalformedDesignError, match="platform"):
            summarise(
                rows("V7", "S11", [reading()] * 4),
                address=ADDRESS,
                scenario_class=lambda _s: "out_of_library",
                platform="   ",
                grammar=GRAMMAR,
            )

    def test_a_report_without_a_grammar_raises(self) -> None:
        # D1 is grammar.distance and D6 is grammar.code_length, so both are
        # grammar-relative and meaningless unnamed. DimensionVector.d6_complexity
        # says so in as many words.
        with pytest.raises(MalformedDesignError, match="grammar"):
            summarise(
                rows("V7", "S11", [reading()] * 4),
                address=ADDRESS,
                scenario_class=lambda _s: "out_of_library",
                platform=PLATFORM,
                grammar=GrammarVersion(""),
            )

    def test_the_rendered_header_names_the_platform_and_the_grammar(self) -> None:
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        assert PLATFORM in text
        assert GRAMMAR in text

    def test_the_rendered_header_names_the_address(self) -> None:
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        assert ADDRESS.env_version in text
        assert ADDRESS.metric_version in text
        assert ADDRESS.partition.value in text

    def test_the_rendering_is_ascii(self) -> None:
        # Local sessions read this on a Windows console, whose default code page
        # is cp1252: a section sign comes out as a replacement character there.
        # The section signs that are correct in a docstring are wrong in printed
        # output, and this is the check that keeps the two apart.
        text = render(
            report_of(
                rows("V7", "S11", [reading(d2=-math.inf)] * 2),
                classes={"S11": "out_of_library"},
            )
        )
        assert text.isascii()
        assert text.encode("cp1252").decode("cp1252") == text

    def test_the_rendered_header_states_the_interval_level(self) -> None:
        # An interval printed without its coverage is not a quotable figure, and
        # DimensionSummary.level was stored and rendered by nothing until this was
        # asserted. Read off a summary rather than restated in the template, so the
        # printed level cannot drift from the computed one.
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        assert "95%" in text
        assert "not clipped" in text

    # Built with chr(), not written literally. ruff flags ambiguous Unicode in
    # source, and a \\u escape does not survive `ruff format`, which normalises it
    # back to the character. The point of these is the codepoint, not how it reads.
    @pytest.mark.parametrize(
        "bad",
        [f"Ubuntu {chr(0xA7)} caf{chr(0xE9)}", f"Linux {chr(0x2192)} x86"],
        ids=["latin1", "arrow"],
    )
    def test_a_non_ascii_platform_is_refused(self, bad: str) -> None:
        # The ASCII guarantee was prose, and false: render interpolates the
        # platform, so a --platform carrying an en dash or a section sign made the
        # rendered text non-ASCII while test_the_rendering_is_ascii -- which passes
        # an ASCII platform -- went on passing. On a cp1252 console those either
        # become replacement characters or raise out of print.
        with pytest.raises(MalformedDesignError, match="not ASCII"):
            summarise(
                rows("V7", "S11", [reading()] * 4),
                address=ADDRESS,
                scenario_class=lambda _s: "out_of_library",
                platform=bad,
                grammar=GRAMMAR,
            )

    def test_a_non_ascii_grammar_is_refused(self) -> None:
        with pytest.raises(MalformedDesignError, match="not ASCII"):
            summarise(
                rows("V7", "S11", [reading()] * 4),
                address=ADDRESS,
                scenario_class=lambda _s: "out_of_library",
                platform=PLATFORM,
                grammar=GrammarVersion(f"pointproc{chr(0x2013)}edits/1.0.0"),
            )

    def test_a_non_ascii_system_from_the_ledger_is_refused(self) -> None:
        # Not caller free text: this arrives in the ledger's config and reaches
        # rendered output the same way, so the guarantee needs it too.
        with pytest.raises(MalformedDesignError, match="not ASCII"):
            report_of(rows(f"V{chr(0x2087)}", "S11", [reading()] * 4))

    def test_the_rendered_header_says_the_results_are_exploratory(self) -> None:
        # SPEC §9: "Slice results are exploratory by construction ... they are
        # not reportable as confirmatory findings." Unconditional, because the
        # sentence is only useful where somebody reading a number will see it.
        text = render(report_of(rows("V7", "S11", [reading()] * 4)))
        assert "exploratory" in text.lower()


# --------------------------------------------------------------------------
# Which dimension a headline figure is
# --------------------------------------------------------------------------


class TestEachCellNamesItsPrimaryDimension:
    @pytest.mark.parametrize("scenario_class", CLOSED_WORLD)
    def test_a_closed_world_cell_has_no_primary_dimension(
        self, scenario_class: ScenarioClass
    ) -> None:
        # §8 reads S1-S10 under the proper score. The vector is still computed
        # and reported -- §8 asks for it "in all cases" -- but no dimension is
        # the headline, and None is how the report says so.
        report = report_of(
            rows("V1", "S1", [reading()] * 4), classes={"S1": scenario_class}
        )
        assert report.cells[0].primary is None

    def test_an_out_of_library_cell_is_read_on_d3(self) -> None:
        report = report_of(
            rows("V7", "S11", [reading()] * 4), classes={"S11": "out_of_library"}
        )
        assert report.cells[0].primary == "d3_intervention_similarity"

    def test_a_compound_cell_is_read_on_d1(self) -> None:
        report = report_of(
            rows("V7", "S8", [reading()] * 4), classes={"S8": "compound"}
        )
        assert report.cells[0].primary == "d1_structural_distance"

    def test_every_scenario_class_is_handled(self) -> None:
        # A Literal gaining a member should fail here rather than at the first
        # report over a scenario of the new class.
        for scenario_class in SCENARIO_CLASSES:
            report = report_of(
                rows("V7", "S1", [reading()] * 4), classes={"S1": scenario_class}
            )
            assert report.cells[0].scenario_class == scenario_class

    def test_the_rendered_table_names_the_primary_dimension(self) -> None:
        # Asserted against the label, not against "d3" appearing somewhere: "d3"
        # is in the column header and the dimension legend of every report, so a
        # substring check passes even with the primary-dimension line deleted
        # outright. Mutation-tested — removing that line from render() must fail
        # this.
        text = render(
            report_of(
                rows("V7", "S11", [reading()] * 4), classes={"S11": "out_of_library"}
            )
        )
        assert "primary dimension: d3_intervention_similarity" in text

    def test_a_closed_world_cell_says_it_has_no_primary_dimension(self) -> None:
        text = render(
            report_of(rows("V1", "S1", [reading()] * 4), classes={"S1": "null"})
        )
        assert "primary dimension: none" in text
        assert "closed-world score" in text


# --------------------------------------------------------------------------
# §9's preregistered contrast
# --------------------------------------------------------------------------


def contrast_report(
    treatment: Sequence[float], comparator: Sequence[float], **flags: float
) -> MatrixReport:
    """Return a report with a V7 arm and a B4 arm on S11, both on D3.

    Every replicate detects inadequacy unless ``flags`` says otherwise, because
    §9's contrast is conditional on detection and an arm of non-detectors has no
    contrast to compute. ``reading``'s own default is the opposite, which is right
    for a general cell and wrong for this fixture.
    """

    def arm(values: Sequence[float]) -> list[CellReading]:
        return [reading(**{"d3": v, "inadequate": 1.0, **flags}) for v in values]

    return report_of(
        rows("V7", "S11", arm(treatment)) + rows("B4", "S11", arm(comparator)),
        classes={"S11": "out_of_library"},
    )


class TestThePreregisteredContrast:
    def test_two_separated_arms_do_not_overlap(self) -> None:
        result = contrast(
            contrast_report([0.94, 0.96, 0.98, 0.96], [0.10, 0.12, 0.11, 0.13]),
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B4",
            dimension="d3_intervention_similarity",
        )
        assert result.overlaps is False
        assert result.treatment.point > result.comparator.point

    def test_two_arms_on_the_same_values_overlap(self) -> None:
        result = contrast(
            contrast_report([0.5, 0.6, 0.4, 0.5], [0.5, 0.6, 0.4, 0.5]),
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B4",
            dimension="d3_intervention_similarity",
        )
        assert result.overlaps is True

    def test_a_contrast_against_another_baseline_is_not_preregistered(self) -> None:
        # environments/pointproc/matrix.py: B4 is the comparator by prior
        # designation, and "reporting the contrast against a different baseline
        # is permitted only if the report says that is what happened". Deriving
        # the flag from SPEC9_CONTRAST is what makes that sayable rather than
        # something the caller asserts about itself.
        report = report_of(
            rows("V7", "S11", [reading(d3=0.9, inadequate=1.0)] * 4)
            + rows("B4", "S11", [reading(d3=0.1, inadequate=1.0)] * 4)
            + rows("B5", "S11", [reading(d3=0.3, inadequate=1.0)] * 4),
            classes={"S11": "out_of_library"},
        )
        result = contrast(
            report,
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B5",
            dimension="d3_intervention_similarity",
            preregistration=SPEC9_CONTRAST,
        )
        assert result.comparator_system == "B5"
        assert result.treatment_system == "V7"
        assert result.preregistered is False

    def test_the_spec_nine_contrast_is_marked_preregistered(self) -> None:
        result = contrast(
            contrast_report([0.9] * 4, [0.1] * 4),
            scenario=SPEC9_CONTRAST.scenario,
            treatment=SPEC9_CONTRAST.treatment,
            comparator=SPEC9_CONTRAST.comparator,
            dimension=SPEC9_CONTRAST.dimension,
            preregistration=SPEC9_CONTRAST,
        )
        assert result.preregistered is True
        assert result.exceeds is True

    def test_a_fabricated_declaration_is_believed_which_is_the_known_limit(
        self,
    ) -> None:
        # Pinned as a limitation, not as a feature. `contrast` compares its
        # arguments against whichever declaration it is handed, and both come from
        # the same caller -- so the flag catches an accidental mislabel (the
        # comparator and dimension are keyword arguments a later edit can change
        # while the caption stays put) and not a fabricated one.
        #
        # It cannot be closed from inside `sciagent`: a declaration names systems
        # and a scenario, so under invariant 1 it must arrive from outside, and
        # anything from outside is caller-supplied. Asserted so that anyone reading
        # `preregistered` knows exactly what it is worth, and so that a future
        # attempt to make it unforgeable fails here loudly rather than looking like
        # a no-op.
        fabricated = Preregistration(
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B5",
            dimension="d1_structural_distance",
        )
        report = report_of(
            rows("V7", "S11", [reading(d1=1.0, inadequate=1.0)] * 4)
            + rows("B5", "S11", [reading(d1=3.0, inadequate=1.0)] * 4),
            classes={"S11": "out_of_library"},
        )
        result = contrast(
            report,
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B5",
            dimension="d1_structural_distance",
            preregistration=fabricated,
        )
        assert result.preregistered is True
        assert result.comparator_system != SPEC9_CONTRAST.comparator

    def test_the_declared_contrast_is_the_one_spec_nine_states(self) -> None:
        # "On S11 Stage B, conditional on inadequacy detection, does V7 exceed
        # B4 on D3?" -- pinned here so a later edit to the declaration is a test
        # failure rather than a silently different preregistration.
        assert SPEC9_CONTRAST.scenario == ScenarioId("S11")
        assert SPEC9_CONTRAST.treatment == "V7"
        assert SPEC9_CONTRAST.comparator == "B4"
        assert SPEC9_CONTRAST.dimension == "d3_intervention_similarity"
        assert SPEC9_CONTRAST.conditional_on_inadequacy is True

    def test_dropping_the_conditioning_is_not_the_preregistered_contrast(self) -> None:
        # §9 states the contrast conditionally, so an unconditioned run of it is
        # a different question. Every field but the conditioning agrees here.
        result = contrast(
            contrast_report([0.9] * 4, [0.1] * 4),
            scenario=SPEC9_CONTRAST.scenario,
            treatment=SPEC9_CONTRAST.treatment,
            comparator=SPEC9_CONTRAST.comparator,
            dimension=SPEC9_CONTRAST.dimension,
            conditional_on_inadequacy=False,
            preregistration=SPEC9_CONTRAST,
        )
        assert result.preregistered is False

    def test_conditioning_drops_the_replicates_that_did_not_detect(self) -> None:
        # "Conditional on inadequacy detection" is a filter on replicates, not a
        # note in the caption. A contrast that ignored it would answer a
        # different question from the one §9 preregistered.
        entries = rows(
            "V7",
            "S11",
            [reading(d3=0.9, inadequate=1.0)] * 3 + [reading(d3=0.1, inadequate=0.0)],
        ) + rows("B4", "S11", [reading(d3=0.2, inadequate=1.0)] * 4)
        report = report_of(entries, classes={"S11": "out_of_library"})

        conditioned = contrast(
            report,
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B4",
            dimension="d3_intervention_similarity",
        )
        unconditioned = contrast(
            report,
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B4",
            dimension="d3_intervention_similarity",
            conditional_on_inadequacy=False,
        )
        assert conditioned.treatment.n_finite == 3
        assert conditioned.treatment.point == pytest.approx(0.9)
        assert unconditioned.treatment.n_finite == 4
        assert unconditioned.treatment.point == pytest.approx(0.7)

    def test_a_contrast_reports_whether_conditioning_kept_the_seeds_paired(
        self,
    ) -> None:
        # eval/matrix.py pairs seeds across arms so that §9's contrast is not
        # partly a comparison of worlds. Conditioning filters each arm by its own
        # inadequacy flag, which can undo that -- so the contrast reports whether
        # it did, rather than leaving a between-worlds component invisible.
        both = contrast_report([0.9] * 4, [0.1] * 4)
        result = contrast(
            both,
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B4",
            dimension="d3_intervention_similarity",
        )
        assert result.paired is True
        assert result.treatment_seeds == result.comparator_seeds

        # Now make the arms detect on different replicates: V7 on 0,1,2 and B4 on
        # 1,2,3. Both arms keep three replicates, so nothing about the counts
        # betrays it -- only the seeds do.
        entries = rows(
            "V7",
            "S11",
            [reading(d3=0.9, inadequate=1.0)] * 3 + [reading(d3=0.9, inadequate=0.0)],
        ) + rows(
            "B4",
            "S11",
            [reading(d3=0.1, inadequate=0.0)] + [reading(d3=0.1, inadequate=1.0)] * 3,
        )
        crossed = contrast(
            report_of(entries, classes={"S11": "out_of_library"}),
            scenario=ScenarioId("S11"),
            treatment="V7",
            comparator="B4",
            dimension="d3_intervention_similarity",
        )
        assert crossed.treatment.n_finite == crossed.comparator.n_finite == 3
        assert crossed.paired is False
        assert crossed.treatment_seeds != crossed.comparator_seeds

    def test_a_contrast_with_no_conditioned_replicate_raises(self) -> None:
        # Not a zero and not an empty interval: if no replicate detected
        # inadequacy then §9's question has no answer on this matrix, and that is
        # a finding to report rather than a number to compute.
        report = contrast_report([0.9] * 4, [0.1] * 4, inadequate=0.0)
        with pytest.raises(MalformedDesignError, match="inadequa"):
            contrast(
                report,
                scenario=ScenarioId("S11"),
                treatment="V7",
                comparator="B4",
                dimension="d3_intervention_similarity",
            )

    def test_a_contrast_naming_an_absent_arm_raises(self) -> None:
        with pytest.raises(MalformedDesignError, match="V3"):
            contrast(
                contrast_report([0.9] * 4, [0.1] * 4),
                scenario=ScenarioId("S11"),
                treatment="V3",
                comparator="B4",
                dimension="d3_intervention_similarity",
            )

    def test_a_contrast_on_an_unknown_dimension_raises(self) -> None:
        with pytest.raises(MalformedDesignError, match="d9"):
            contrast(
                contrast_report([0.9] * 4, [0.1] * 4),
                scenario=ScenarioId("S11"),
                treatment="V7",
                comparator="B4",
                dimension="d9_invented",
            )


# --------------------------------------------------------------------------
# Determinism (invariant 3)
# --------------------------------------------------------------------------


class TestTheScriptPrintsWhatTheLedgerHolds:
    """``scripts/report_matrix.py``, against a ledger written to ``tmp_path``.

    A script is the one part of this nobody runs under pytest by default, so the
    wiring gets a test even though every number it prints is checked above. Not
    an end-to-end run: no cell of the matrix has been run, for the reason
    ``docs/DECISIONS.md`` records.
    """

    def _ledger(self, path: Path) -> None:
        with CampaignLedger.open(path) as ledger:
            for entry in rows("V7", "S11", [reading(d3=0.9)] * 3):
                ledger.append(entry.key, reading=dict(entry.reading))

    def _argv(self, path: Path) -> list[str]:
        return [
            str(path),
            "--platform",
            PLATFORM,
            "--grammar",
            str(GRAMMAR),
            "--env-version",
            str(ADDRESS.env_version),
            "--data-version",
            str(ADDRESS.data_version),
            "--metric-version",
            str(ADDRESS.metric_version),
        ]

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Run the script as a child process, the way a person would.

        ``scripts/`` is not on ``sys.path`` and is not a package, so the choice is
        an ``importlib`` load by path or a subprocess. Subprocess, matching
        ``tests/acceptance/test_a01_a05.py``'s child-process pattern -- it also
        exercises argparse and the exit status, which an imported ``main`` would
        not.
        """
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            check=False,
        )

    def test_it_prints_the_table_for_a_ledger_on_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "campaign.sqlite"
        self._ledger(path)
        completed = self._run(self._argv(path))
        assert completed.returncode == 0, completed.stderr
        assert "V7 / S11" in completed.stdout
        assert PLATFORM in completed.stdout
        assert "exploratory" in completed.stdout.lower()

    def test_a_missing_ledger_is_an_error_and_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        absent = tmp_path / "absent.sqlite"
        completed = self._run(self._argv(absent))
        assert completed.returncode == 2
        assert "no ledger" in completed.stderr
        assert "Traceback" not in completed.stderr

    def test_an_unavailable_contrast_is_a_message_and_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        # --contrast's own help text calls "no replicate detected inadequacy" a
        # legitimate state for a partial campaign, and the ledger written here has
        # only a V7 arm anyway. The table has already printed by then, so this must
        # not become a traceback: exit 3, distinct from the missing-ledger 2.
        path = tmp_path / "campaign.sqlite"
        self._ledger(path)
        completed = self._run([*self._argv(path), "--contrast"])
        assert completed.returncode == 3
        assert "contrast unavailable" in completed.stderr
        assert "Traceback" not in completed.stderr
        # The table still reached stdout before the contrast failed.
        assert "V7 / S11" in completed.stdout

    def test_the_platform_and_the_grammar_are_required(self, tmp_path: Path) -> None:
        # argparse exits 2 on a missing required argument. Asserted because the
        # whole point of the report layer refusing an unlabelled report is undone
        # if the CLI in front of it supplies a default.
        path = tmp_path / "campaign.sqlite"
        self._ledger(path)
        for dropped in ("--platform", "--grammar"):
            argv = self._argv(path)
            index = argv.index(dropped)
            del argv[index : index + 2]
            completed = self._run(argv)
            assert completed.returncode == 2
            assert dropped in completed.stderr


class TestGuardsAgainstAHandBuiltReport:
    """Refusals that only a report not built by ``summarise`` can reach.

    Harness-only, and none of it is agent-reachable -- no module under
    ``sciagent/systems/`` imports this one. They are here because every other
    failure in the module is a ``MalformedDesignError``, and each of these was an
    untyped ``KeyError``/``IndexError`` or, worse, a silent perturbation.
    """

    def test_a_non_boolean_inadequacy_flag_is_refused_not_dropped(self) -> None:
        # The defect an earlier fix here missed. _values checks a field is
        # *present*; the conditioning filter tests `flag == 1.0`. A present
        # non-boolean therefore satisfied the first and silently failed the second,
        # dropping the replicate: measured, one replicate at inadequate=2.0 moved an
        # arm from point 0.6333 / n=3 to 0.9000 / n=2 without raising.
        #
        # The 2.0 has to be written into the payload directly. `CellReading`
        # declares `inadequate` as a `bool`, so `reading(inadequate=2.0)` stores
        # 1.0 and the value can never arrive by that route -- which is the reason
        # this is a hand-built-report guard and not a reachable one.
        clean = rows("V7", "S11", [reading(d3=0.9, inadequate=1.0)] * 3)
        tampered = (
            *clean[:2],
            dataclasses.replace(
                clean[2],
                reading=FrozenDict[str, float](
                    {**dict(clean[2].reading), "inadequate": 2.0}
                ),
            ),
        )
        assert tampered[2].reading["inadequate"] == 2.0
        with pytest.raises(MalformedDesignError, match="read as a boolean"):
            report_of(tampered, classes={"S11": "out_of_library"})

    def test_a_report_with_no_cells_refuses_to_render(self) -> None:
        empty = MatrixReport(
            cells=(),
            rows=(),
            platform=PLATFORM,
            grammar=GRAMMAR,
            address=ADDRESS,
            matrix_version="spec9/1",
        )
        with pytest.raises(MalformedDesignError, match="no summarised cell"):
            render(empty)

    def test_intervals_at_two_levels_refuse_to_render(self) -> None:
        # A single header cannot label two levels, and printing either mislabels
        # the other -- which is the one thing DimensionSummary.level exists to
        # prevent. An earlier version read the level off cells[0] and would have
        # printed 50% over a table half of which was 95%.
        good = report_of(rows("V7", "S11", [reading()] * 4)).cells[0]
        other = dataclasses.replace(
            good,
            system="B4",
            dimensions=FrozenDict[str, DimensionSummary](
                {
                    name: dataclasses.replace(summary, level=0.5)
                    for name, summary in good.dimensions.items()
                }
            ),
        )
        mixed = MatrixReport(
            cells=(other, good),
            rows=(),
            platform=PLATFORM,
            grammar=GRAMMAR,
            address=ADDRESS,
            matrix_version="spec9/1",
        )
        with pytest.raises(MalformedDesignError, match="more than one confidence"):
            render(mixed)

    def test_an_unknown_scenario_class_is_refused(self) -> None:
        # The one semantic input this layer takes from a callback, and it decides
        # which figure is the headline. primary_dimension indexes a mapping with
        # it, so this used to be a bare KeyError out of scoring.py.
        with pytest.raises(MalformedDesignError, match="not one of"):
            summarise(
                rows("V7", "S11", [reading()] * 4),
                address=ADDRESS,
                # cast, not `type: ignore`: the invalid value is the point of the
                # test, and CLAUDE.md forbids suppressing a checker to pass.
                scenario_class=lambda _s: cast("ScenarioClass", "invented"),
                platform=PLATFORM,
                grammar=GRAMMAR,
            )

    def test_a_self_contrast_is_refused(self) -> None:
        with pytest.raises(MalformedDesignError, match="needs two arms"):
            contrast(
                contrast_report([0.9] * 4, [0.1] * 4),
                scenario=ScenarioId("S11"),
                treatment="V7",
                comparator="V7",
                dimension="d3_intervention_similarity",
            )


def _dimension_hexes(report: MatrixReport) -> tuple[tuple[str, ...], ...]:
    """Return every summarised float in a report, as ``float.hex()``.

    Compared at ``hex()`` rather than at rendered precision so a last-place
    difference from an order-dependent fold is visible. ``render`` formats through
    ``.4f``, which would hide exactly the difference invariant 3 is about.
    """
    return tuple(
        (
            cell.system,
            str(cell.scenario),
            *(
                value.hex()
                for name in DIMENSIONS
                for value in (
                    cell.dimensions[name].point,
                    cell.dimensions[name].low,
                    cell.dimensions[name].high,
                )
            ),
            cell.truth_mass.point.hex(),
            cell.log_score.point.hex(),
            cell.correct_rate.hex(),
            cell.identified_rate.hex(),
            cell.inadequate_rate.hex(),
        )
        for cell in report.cells
    )


class TestTheReportIsAPureFunctionOfItsRows:
    def test_the_fold_does_not_depend_on_replicate_order(self) -> None:
        # Every permutation, not a reversal and a rotation. These five values are
        # chosen because a naive left-to-right fold gives *two* distinct means
        # across their 120 orderings, so the mutation this test exists to catch is
        # reachable -- but forward, reversed and rotated all happen to land on the
        # same one of the two. Picking distinct values is not enough; the ordering
        # has to be exhaustive, or the test passes by luck.
        #
        # Compared at float.hex(), because render's .4f rounds away exactly the
        # last-place difference an order-dependent fold produces.
        values = (0.91, 0.87, 0.94, 0.89, 0.92)
        entries = rows("V7", "S11", [reading(d3=v) for v in values])
        expected = _dimension_hexes(report_of(entries))
        for order in itertools.permutations(entries):
            assert _dimension_hexes(report_of(order)) == expected

    def test_the_cell_order_does_not_change_the_rendering(self) -> None:
        # The other half, and a different claim: rows arrive interleaved from a
        # resumed campaign's ledger, and `summarise` groups them through a plain
        # dict. This is about `sorted(grouped)`, not about the fold.
        entries = (
            rows("V7", "S11", [reading(d3=0.91)] * 3)
            + rows("B4", "S11", [reading(d3=0.21)] * 2)
            + rows("B2", "S1", [reading(d3=0.55)] * 2)
        )
        forward = render(report_of(entries))
        assert forward == render(report_of(tuple(reversed(entries))))
        assert forward == render(report_of(entries[3:] + entries[:3]))
        assert forward == render(report_of(entries[::2] + entries[1::2]))

    def test_the_same_rows_render_identically_twice(self) -> None:
        entries = rows("V7", "S11", [reading()] * 4)
        assert render(report_of(entries)) == render(report_of(entries))

    def test_cells_are_ordered_by_system_then_scenario(self) -> None:
        entries = (
            rows("V7", "S2", [reading()] * 2)
            + rows("B4", "S11", [reading()] * 2)
            + rows("V7", "S11", [reading()] * 2)
        )
        report = report_of(tuple(reversed(entries)))
        assert [(cell.system, str(cell.scenario)) for cell in report.cells] == [
            ("B4", "S11"),
            ("V7", "S11"),
            ("V7", "S2"),
        ]
