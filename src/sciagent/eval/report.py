"""Rendering a campaign as SPEC §8's six-dimensional vector table.

SPEC §8 says the six dimensions are "reported separately, never collapsed into
one number". :class:`~sciagent.eval.scoring.DimensionVector` holds that at the
point of computation -- six fields, no ``total`` -- but the numbers are useless to
a reader in the form they are computed, and **the natural rendering is the
forbidden one**: a mean of the six, or a ranking column. So this module is where
the prohibition either holds or quietly fails, and building it deliberately is
how it holds.

Item 12's S11 table is the demonstration of why the rule is not fussiness. A
plain Hawkes proposal scores 1.50 on D1 -- *worse* than proposing nothing, which
scores 1.00 -- and 0.960 on D3 against a best rival of 0.698. A mean of the six
would report that as mediocre; a ranking column would report it as a loss. Both
would be wrong about the one scenario the slice is built around.

Nothing here carries a total, a mean of the six, a rank or a composite.
``tests/test_report.py`` checks that by inspecting the fields of every type in
:data:`__all__` -- derived from it, so a newly exported type is covered without
anybody remembering to add it -- rather than by trusting this paragraph.

**That check is a name heuristic and not a proof**, and saying so is the point: it
matches a list of substrings, so a collapsed figure called ``headline`` would pass
it. What actually holds the prohibition is structural -- :data:`DIMENSIONS` is a
six-tuple that a seventh column would have to be added to, and no arithmetic in
this module crosses two dimensions. The name check is a tripwire over those, not a
substitute for them.

What a report must say, and therefore cannot default
----------------------------------------------------

Two things have to appear wherever these numbers appear, and neither can be
recovered from the ledger:

**Which platform every cell ran on.** The project is pinned to one platform --
Windows -- precisely so that this answer is always the same one, and the
registry content-addresses over (env version, config, data version, metric
version, seed) with **no platform term**. The ledger therefore has no platform
column and cannot grow one without retiring every stored row. So :func:`summarise`
takes the platform and *refuses to build a report without it*.

A constant is exactly the kind of fact that stops being written down, which is
the argument for holding it by construction rather than by whoever writes the
caption remembering. The pin is a present choice, recorded in
``docs/DECISIONS.md`` alongside the measured Windows/Ubuntu divergence that
motivated it; a report that states its platform stays readable if that choice is
ever revisited, and one that assumed it does not.

**Which grammar produced D1 and D6.** Both are grammar-relative --
:attr:`~sciagent.eval.scoring.DimensionVector.d6_complexity` says so in as many
words -- and R5 is the open question of how much a ranking depends on the
grammar. A D1 quoted without its grammar version is not a number anybody can
interpret.

Selection is by address, not by cell
------------------------------------

The fourth invariant makes the registry append-only, so a cell re-run because its
inputs changed is an *append at a new address* and the stale row stays. Both
:mod:`sciagent.eval.matrix` and :mod:`sciagent.registry.ledger` warn about this
and hand the job here: :meth:`~sciagent.registry.ledger.CampaignLedger.entries`
returns every row whatever partition or version it came from, deliberately, since
filtering there would mean interpreting ``config`` and that is what keeps the
store domain-independent.

:func:`summarise` therefore keeps only the rows matching a whole
:class:`~sciagent.eval.matrix.CampaignAddress` -- matrix version, partition,
environment, data and metric versions -- **and the reading of SPEC §8's
dimensions the row was scored under**, which is the criterion most likely to
reject a caller's rows and the one no address column carries. It raises rather
than return an empty report, because a report over no rows is indistinguishable
from a matrix that ran and produced nothing.

Domain-independent, like the rest of ``sciagent``. This module names no system
and no scenario: it reads them as opaque text out of each row's ``config``, and
:class:`Preregistration` is the *type* of "which contrast was declared in
advance" while the instance lives beside the environment -- the same split
:class:`~sciagent.eval.matrix.Cell` and ``SPEC9_CELLS`` already use, and for the
same reason.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np

from sciagent.core.errors import MalformedDesignError
from sciagent.core.reductions import mean as exact_mean
from sciagent.core.reductions import variance as exact_variance
from sciagent.core.types import FrozenDict, GrammarVersion, ScenarioId
from sciagent.eval.matrix import MATRIX_VERSION, CampaignAddress, battery_key
from sciagent.eval.scenarios import SCENARIO_CLASSES, ScenarioClass
from sciagent.eval.scoring import DIMENSION_VERSION, primary_dimension
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.registry.ledger import LedgerEntry
from sciagent.verify.numerical import CONFIDENCE_LEVEL, Z_TWO_SIDED

__all__ = [
    "DIMENSIONS",
    "CellSummary",
    "Contrast",
    "DimensionSummary",
    "MatrixReport",
    "Preregistration",
    "contrast",
    "render",
    "summarise",
]

#: SPEC §8's six dimensions, in §8's order, under the names
#: :meth:`~sciagent.eval.matrix.CellReading.as_payload` stores them.
#:
#: A tuple, so the report's column order is fixed rather than a mapping's. Six
#: entries and no seventh: a "total" column would be a member of this tuple, so
#: keeping it exactly §8's list is one of the places the prohibition is held.
DIMENSIONS: Final[tuple[str, ...]] = (
    "d1_structural_distance",
    "d2_held_out_predictive",
    "d3_intervention_similarity",
    "d4_explanatory_coverage",
    "d5_enabled_experiment_value",
    "d6_complexity",
)

#: Payload key §9's primary contrast conditions on. Named here because
#: :func:`contrast` filters replicates by it, and a typo would silently produce
#: an unconditioned contrast reported as a conditioned one.
_INADEQUATE: Final = "inadequate"

#: What :func:`render` says about every figure it prints. SPEC §9: slice results
#: "are exploratory by construction ... they are not reportable as confirmatory
#: findings". Unconditional, because the sentence is only useful where somebody
#: reading a number will see it.
#: ASCII, deliberately, as is every other byte :func:`render` emits. Local
#: sessions read this on a Windows console, whose default code page is cp1252 --
#: a literal ``§`` comes out as a replacement character there, so the section
#: signs that are correct in a docstring are wrong in printed output.
_CAVEAT: Final = (
    "Slice results are exploratory by construction: they inform the frozen\n"
    "campaign and are not reportable as confirmatory findings. The six\n"
    "dimensions are reported separately and never collapsed into one number\n"
    "(SPEC sec. 8), so there is no total, no mean of the six and no rank\n"
    "column."
)


@dataclass(frozen=True, slots=True)
class DimensionSummary:
    """One dimension of one cell, over that cell's replicates.

    A point estimate and a normal 95% interval on the mean.
    :mod:`sciagent.verify.numerical` already argues why the framework computes
    exactly one estimator -- "an estimator chosen per claim is a degree of
    freedom" -- and the same argument applies to a report: a bootstrap would make
    a *reported* figure stochastic and would need a seeded generator threaded
    into a rendering path, which is a bad property for a number that gets quoted.
    So this reuses that module's :data:`~sciagent.verify.numerical.Z_TWO_SIDED`
    and confidence level.

    **The interval is not clipped to the dimension's support**, though at twenty
    replicates a D3 interval on a mean near the ceiling runs past 1.0. Clipping
    would *narrow* the interval, and a narrower interval makes SPEC §12
    criterion 5's "non-overlapping 95% interval" easier to satisfy -- so clipping
    would bias the preregistered contrast toward the claim it is testing. A bound
    overrun is also the honest signal that twenty replicates is thin.
    """

    point: float
    """Mean over the finite replicates, or ``nan`` if there were none."""

    low: float
    high: float
    """Ends of the interval. Both ``nan`` when fewer than two finite replicates
    leave nothing to estimate a spread from -- a zero-width interval there would
    claim a certainty that one reading does not support."""

    level: float
    """Nominal coverage, from :data:`~sciagent.verify.numerical.CONFIDENCE_LEVEL`."""

    n_finite: int
    """Replicates the point and interval were computed from."""

    n_non_finite: int
    """Replicates excluded for being ``inf`` or ``nan``, reported rather than
    hidden.

    These are ordinary here, not corruption:
    :attr:`~sciagent.eval.scoring.DimensionVector.d2_held_out_predictive` is
    ``-inf`` when the candidate ruled out something that happens and ``nan`` on
    an empty held-out battery, and
    :attr:`~sciagent.eval.scoring.ClosedWorldScore.log_score` is ``-inf`` whenever
    the truth got zero mass, which is B1's ordinary case. A mean that swallowed
    one would report the whole cell as ``-inf`` and its interval as ``[nan,
    nan]`` -- twenty replicates reported as no measurement because one of them
    was informative.
    """


@dataclass(frozen=True, slots=True)
class CellSummary:
    """One (system, scenario) cell of the matrix, over its replicates.

    Carries §8's six dimensions **and** the closed-world proper score, for every
    cell. §8 reads as though these were alternatives -- it says D1-D6 are "not
    applicable" to closed-world scenarios and, four lines later, "report D1
    through D6 as a vector in all cases".
    :func:`~sciagent.eval.scoring.primary_dimension` already resolves that: "not
    applicable" means *not the headline*, not *not computed*. So both are reported
    and :attr:`primary` names which one a headline figure may cite.
    """

    system: str
    scenario: ScenarioId
    scenario_class: ScenarioClass
    replicates: int
    """Rows selected for this cell at the report's address."""

    battery: str
    """The held-out battery term these rows were scored on, from their address.

    Rendered rather than merely carried. D2, D3 and D5 are defined over the
    battery, so a figure for any of the three means nothing without knowing
    which battery it is a figure *under*.

    Three checks stand behind the term by the time it is printed, and they ask
    three different questions: :func:`_at_address` that a row carries one at all,
    :func:`_refuse_mixed_batteries` that a scenario's rows agree with each other,
    and :func:`_refuse_superseded_battery` that what they agree on is what the
    scenario declares *now*. The third arrived a gate later than the other two,
    because it is the only one needing a declaration passed in; until it existed
    a report built entirely on rows scored under a replaced battery was accepted,
    and printing the term was what kept that from being silent. It is still
    printed, and the reason is no longer that: a reader comparing figures across
    two reports needs to see which question set each was scored on.
    """

    dimensions: FrozenDict[str, DimensionSummary]
    """§8's six, keyed by :data:`DIMENSIONS`."""

    primary: str | None
    """Which dimension a headline figure for this cell cites, or ``None`` for a
    closed-world scenario, which is read under the proper score instead."""

    truth_mass: DimensionSummary
    log_score: DimensionSummary
    """The closed-world proper score's two figures."""

    correct_rate: float
    identified_rate: float
    """Fraction of replicates whose leading hypothesis was the true structure,
    and the stricter fraction that also carried more than half the mass."""

    inadequate_rate: float
    """Fraction of replicates whose posterior predictive check judged the
    entertained space inadequate.

    SPEC §9's primary contrast is *conditional on inadequacy detection*, so this
    is the conditioning variable rather than one result among several.
    """

    experiments: DimensionSummary
    """Experiments the system spent, averaged over replicates."""


@dataclass(frozen=True, slots=True)
class MatrixReport:
    """A campaign at one address, summarised, with the provenance it needs.

    Carries :attr:`rows` as well as :attr:`cells` because a question asked *of* a
    matrix may need to re-aggregate at the replicate level -- §9's contrast is
    conditional on inadequacy detection, which is a filter on replicates and not
    a note in a caption. Summarising it away here would make that question
    unanswerable without a second pass over the ledger.
    """

    cells: tuple[CellSummary, ...]
    """In ``(system, scenario)`` order, so the rendering never depends on the
    order rows arrived in."""

    rows: tuple[LedgerEntry, ...]
    """Every ledger row at this address, in the order given."""

    platform: str
    grammar: GrammarVersion
    address: CampaignAddress
    matrix_version: str


@dataclass(frozen=True, slots=True)
class Preregistration:
    """A contrast declared before the numbers existed.

    The *type* only. Which contrast a campaign preregistered names a system and a
    scenario, so the instance belongs beside the environment -- exactly as
    :class:`~sciagent.eval.matrix.Cell` is here and ``SPEC9_CELLS`` is not.

    **What this buys, stated narrowly.** :func:`contrast` compares its arguments
    against one of these and sets :attr:`Contrast.preregistered` from the result.
    Both come from the same caller, so this catches a contrast *accidentally*
    reported under the wrong label -- the realistic error, since the scenario,
    treatment, comparator, dimension and conditioning are five keyword arguments
    that a later edit can change while the caption stays put, and
    :meth:`describes` compares all five. It does **not** make the flag
    unforgeable: a caller who
    fabricates a :class:`Preregistration` at the call site gets
    ``preregistered=True`` for any pairing, and ``tests/test_report.py`` pins that
    as a known limitation rather than leaving it to be discovered.

    It cannot be made unforgeable here. The declaration names systems and a
    scenario, so under the first invariant it has to arrive from outside
    ``sciagent`` -- and anything arriving from outside is caller-supplied. The
    thing with actual authority is the single instance in the environment, which a
    test pins against drift.
    """

    scenario: ScenarioId
    treatment: str
    comparator: str
    dimension: str
    conditional_on_inadequacy: bool = True

    def describes(
        self,
        *,
        scenario: ScenarioId,
        treatment: str,
        comparator: str,
        dimension: str,
        conditional_on_inadequacy: bool,
    ) -> bool:
        """Return whether this is the contrast that was declared in advance.

        Every field must match, the conditioning included: a contrast run
        unconditionally is a different question from the one §9 preregistered,
        however much the rest of it agrees.
        """
        return (
            scenario == self.scenario
            and treatment == self.treatment
            and comparator == self.comparator
            and dimension == self.dimension
            and conditional_on_inadequacy == self.conditional_on_inadequacy
        )


@dataclass(frozen=True, slots=True)
class Contrast:
    """Two arms of one scenario compared on one dimension.

    Reports both arms and whether their intervals overlap, and stops there. No
    p-value and no effect size: SPEC §9 states the question as a comparison of
    intervals, §12 criterion 5 asks for "a non-overlapping 95% interval", and
    slice results are exploratory by construction -- a test statistic here would
    dress an exploratory comparison as a confirmatory one.
    """

    scenario: ScenarioId
    dimension: str
    treatment_system: str
    comparator_system: str
    treatment: DimensionSummary
    comparator: DimensionSummary
    conditioned_on_inadequacy: bool

    treatment_seeds: tuple[int, ...]
    comparator_seeds: tuple[int, ...]
    """The seeds each arm's surviving replicates ran under, sorted and deduplicated.

    Deduplicated, so this is a seed *set* and not a per-replicate list: read
    :attr:`DimensionSummary.n_finite` for an arm's count, never ``len`` of this.

    Reported because **conditioning can break the pairing** that
    :mod:`sciagent.eval.matrix` goes to some length to establish. Seeds are a
    function of the scenario and the replicate index alone, never of the system,
    precisely so that §9's contrast is not partly a comparison of worlds at twenty
    draws an arm. But conditioning on inadequacy detection filters each arm by its
    *own* flag, so the two can end up on overlapping-but-different seed sets --
    and then some of the difference between the arms is the difference between the
    worlds they were left with.

    Carried rather than resolved: which reading §9's "conditional on inadequacy
    detection" intends -- each arm on its own detections, or both on the seeds
    where they agree -- is not settled by the text, and choosing here would decide
    it by fiat in the report layer. :attr:`paired` makes the answer visible in any
    given case, which is what a reader needs to interpret the contrast.
    """
    overlaps: bool
    """Whether the two intervals intersect. ``False`` is what §12 criterion 5
    asks for; it is not by itself evidence of anything, at twenty seeds."""

    preregistered: bool
    """Whether this contrast matches the :class:`Preregistration` it was given.

    ``False`` for any other pairing and ``False`` when no declaration was passed,
    so ``environments/pointproc/matrix.py``'s rule -- reporting the contrast
    against a baseline other than the designated one "is permitted only if the
    report says that is what happened" -- has something to say it *with*.

    Read it as a label check, not as provenance: see :class:`Preregistration` for
    why a caller who fabricates a declaration can set this to ``True``, and why
    that cannot be closed from inside ``sciagent``.
    """

    @property
    def paired(self) -> bool:
        """Return whether both arms survived on the same seeds.

        ``True`` is what §9's paired-seed design intends: the arms are compared on
        the same worlds, so the difference between them is not partly a difference
        between the draws they got. ``False`` does not invalidate the contrast, but
        it does mean the comparison carries a between-worlds component that twenty
        seeds cannot separate out -- and a report quoting the contrast should say
        which case it is in.
        """
        return self.treatment_seeds == self.comparator_seeds

    @property
    def exceeds(self) -> bool:
        """Return whether the treatment's point estimate is above the comparator's.

        Direction only, and deliberately separate from :attr:`overlaps`: §9 asks
        whether V7 *exceeds* B4, and an arm can lead on the point estimate while
        the intervals still overlap. Reading the two together is the caller's
        job, because collapsing them into one boolean would decide by fiat what
        counts as an answer.
        """
        return self.treatment.point > self.comparator.point


# --------------------------------------------------------------------------
# Aggregating replicates
# --------------------------------------------------------------------------


def _summarise(values: Sequence[float]) -> DimensionSummary:
    """Return the point and interval a dimension's replicates imply.

    Non-finite values are excluded and counted rather than folded in; see
    :attr:`DimensionSummary.n_non_finite` for why that is not a convenience.
    Folding is through :mod:`sciagent.core.reductions`, so the mean and variance
    are exactly rounded and do not depend on the order the replicates arrived in
    or on the CPU that summed them.
    """
    finite = [value for value in values if math.isfinite(value)]
    non_finite = len(values) - len(finite)
    if not finite:
        return DimensionSummary(
            point=math.nan,
            low=math.nan,
            high=math.nan,
            level=CONFIDENCE_LEVEL,
            n_finite=0,
            n_non_finite=non_finite,
        )
    array = np.array(finite, dtype=np.float64)
    point = exact_mean(array)
    if len(finite) < 2:
        return DimensionSummary(
            point=point,
            low=math.nan,
            high=math.nan,
            level=CONFIDENCE_LEVEL,
            n_finite=1,
            n_non_finite=non_finite,
        )
    half_width = Z_TWO_SIDED * math.sqrt(exact_variance(array) / len(finite))
    return DimensionSummary(
        point=point,
        low=point - half_width,
        high=point + half_width,
        level=CONFIDENCE_LEVEL,
        n_finite=len(finite),
        n_non_finite=non_finite,
    )


def _values(rows: Sequence[LedgerEntry], name: str) -> tuple[float, ...]:
    """Return one payload field across rows, raising if a row does not carry it.

    Raising rather than defaulting to ``0.0`` or skipping: a missing field means
    the rows were written by a different reading schema, and a report that
    averaged whichever rows happened to have the field would be a number nobody
    could interpret.
    """
    # `min` rather than `[0]`: the offending row is named in an exception, and
    # picking whichever arrived first makes that message depend on the order the
    # caller assembled `rows` in. `entries` is typed `Iterable`, so two logically
    # identical inputs merged from two sources could otherwise produce two
    # different exception strings.
    missing = [row for row in rows if name not in row.reading]
    if missing:
        culprit = min(missing, key=lambda row: str(row.digest))
        raise MalformedDesignError(
            f"row {culprit.digest} carries no {name!r}; it holds "
            f"{sorted(culprit.reading)!r}. A reading written under a different "
            f"schema cannot be summarised beside these"
        )
    return tuple(float(row.reading[name]) for row in rows)


def _flags(rows: Sequence[LedgerEntry], name: str) -> tuple[float, ...]:
    """Return a boolean payload field across rows, refusing a non-boolean value.

    Raises if a value is neither ``0.0`` nor ``1.0``.
    :meth:`~sciagent.eval.matrix.CellReading.as_payload` stores booleans as
    exactly those two, so anything else means this is reading a field that is not
    a boolean.

    **Separate from :func:`_values` because presence is not the check that
    matters here.** Both :func:`_rate` and :func:`_arm` read ``inadequate``, and
    an earlier version had `_arm` go through ``_values`` alone on the reasoning
    that it would then raise a typed error. It would -- for a *missing* field.
    For a **present but non-boolean** one it raised nothing, and the value then
    silently failed ``flag == 1.0`` and dropped the replicate from the arm:
    measured, one replicate with ``inadequate`` set to ``2.0`` moved a contrast
    arm from ``point=0.6333 n=3`` to ``point=0.9000 n=2`` without complaint. A
    guard that checks the wrong property is worse than none, because the comment
    beside it says the case is covered.
    """
    values = _values(rows, name)
    offending = sorted(value for value in values if value not in (0.0, 1.0))
    if offending:
        raise MalformedDesignError(
            f"{name!r} is read as a boolean but holds {offending[0]!r}; a rate is "
            f"a mean of booleans and a conditioning filter tests one, so a third "
            f"value has no meaning under either"
        )
    return values


def _rate(rows: Sequence[LedgerEntry], name: str) -> float:
    """Return the fraction of rows whose boolean field is set.

    A rate above one would otherwise be reported and read as a percentage; see
    :func:`_flags` for the check that prevents it.
    """
    return exact_mean(np.array(_flags(rows, name), dtype=np.float64))


# --------------------------------------------------------------------------
# Selecting rows and building the report
# --------------------------------------------------------------------------


def _at_address(row: LedgerEntry, address: CampaignAddress) -> bool:
    """Return whether a row belongs to the campaign ``address`` names.

    ``dimensions`` is checked for the same reason ``matrix`` is: D1-D6 are what
    this report renders, and two rows scored under different readings of them are
    not two measurements of one quantity. A row recorded before
    :data:`~sciagent.eval.scoring.DIMENSION_VERSION` existed carries no such key
    and is therefore excluded, which is correct -- it was scored under the modal
    D2 and the identically-zero D4 that A26 replaced. Re-deriving those rows is
    ``docs/BACKLOG.md``'s own next entry; pooling them would be the error that
    entry exists to avoid.

    ``battery`` is required to be *present* for the same reason and excluded for
    the same reason, but it cannot be compared against a constant the way
    ``dimensions`` is: a battery is declared per scenario on the environment's
    :class:`~sciagent.eval.scenarios.Scenario`, and this module may not import an
    environment. So presence is all that is decided here -- which drops every row
    recorded before gate A27, since those were scored on a battery derived from
    whatever the arm happened not to run -- and the term's *value* is decided
    downstream, by :func:`_refuse_mixed_batteries` for agreement within a
    scenario and by :func:`_refuse_superseded_battery` against the declaration
    :func:`summarise` is handed.

    Downstream rather than here, and deliberately: those two **refuse** where
    this function **excludes**. A row this filter drops is not this campaign and
    the caller asked for the campaign; a row carrying a replaced battery is a
    campaign nobody can ask for, because there is no argument that names a
    battery. Folding the comparison in here would have made it disappear
    quietly.
    """
    key = row.key
    return (
        key.config.get("matrix") == MATRIX_VERSION
        and key.config.get("dimensions") == DIMENSION_VERSION
        and key.config.get("battery") is not None
        and key.config.get("partition") == address.partition.value
        and key.env_version == address.env_version
        and key.data_version == address.data_version
        and key.metric_version == address.metric_version
    )


def _refuse_non_ascii(what: str, value: str) -> None:
    """Raise if a string destined for rendered output is not ASCII.

    :func:`render` claims every byte it emits is ASCII, and the reason is
    concrete: a local session reads this on a Windows console whose default code
    page is cp1252, where a non-ASCII character either comes out as a replacement
    character or raises ``UnicodeEncodeError`` on the way out of ``print``.

    That claim was prose. ``render`` interpolates the platform, the grammar and
    each cell's system and scenario, so a ``--platform`` carrying an en dash or a
    section sign made the claim false while
    ``test_the_rendering_is_ascii`` -- which passes an ASCII platform -- went on
    passing. Checking the inputs is what makes the guarantee hold, since the
    numbers and the fixed template were never the risk.
    """
    if not value.isascii():
        offending = sorted(
            {character for character in value if not character.isascii()}
        )
        raise MalformedDesignError(
            f"{what} {value!r} is not ASCII ({offending!r}); a report is read on a "
            f"cp1252 console, where these either become replacement characters or "
            f"raise on the way out of print, so a report cannot carry them"
        )


def _refuse_reseeded(rows: Sequence[LedgerEntry]) -> None:
    """Raise if one replicate of one cell has more than one row at this address.

    **The hole an address-only filter leaves, and why it needs its own check.**
    :class:`~sciagent.eval.matrix.CampaignAddress` carries the matrix, partition
    and three versions -- but a cell's content address also covers its **seed**,
    and the seed is not on the address. So a campaign re-run after its
    scenario-seed table changed appends a second row per replicate at a new
    digest whose ``config`` is *identical*, and :func:`_at_address` cannot tell
    the two apart.

    Left unchecked, that pools two disjoint campaigns into one cell: measured on
    a three-replicate cell, two seed sets reporting 0.10 and 0.90 came back as
    six replicates with a point estimate of 0.5000 and an interval spanning both,
    with nothing raising. That is worse than a wrong number, because the row
    count looks like a fuller campaign rather than a broken one.

    At one address a replicate index identifies one run, so two rows sharing one
    means two campaigns. Raising is the only honest option: under the fourth
    invariant both rows are legitimate and neither supersedes the other, so this
    module cannot pick. The caller has to say which seed set it means.
    """
    seen: dict[tuple[str, str, str], LedgerEntry] = {}
    for row in rows:
        system, scenario = _coordinate(row)
        replicate = row.key.config.get("replicate")
        if replicate is None:
            raise MalformedDesignError(
                f"row {row.digest} is addressed to this matrix but names no "
                f"replicate, so it cannot be told apart from a re-seeded run of "
                f"the same cell; its config is {dict(row.key.config)!r}"
            )
        coordinate = (system, scenario, replicate)
        earlier = seen.get(coordinate)
        if earlier is not None:
            # Sorted, not (earlier, row): which of the two arrived first depends
            # on the order the caller assembled `entries` in, and `summarise`
            # promises the rendering does not. An exception string is output too.
            first, second = sorted((int(earlier.key.seed), int(row.key.seed)))
            raise MalformedDesignError(
                f"{system}/{scenario} replicate {replicate} has two rows at this "
                f"address, under seeds {first} and "
                f"{second}. A seed is part of a cell's content address "
                f"but not of the campaign address, so a re-seeded campaign appends "
                f"rather than replaces and both rows match this filter. Pooling "
                f"them would average two campaigns and report the total as one "
                f"cell's replicate count; the fourth invariant means neither row "
                f"supersedes the other, so select the seed set you mean"
            )
        seen[coordinate] = row


def _refuse_mixed_batteries(rows: Sequence[LedgerEntry]) -> None:
    """Raise if one scenario's rows were scored on more than one held-out battery.

    The sibling of :func:`_refuse_reseeded`, and it exists for a defect found by
    review rather than by reasoning. Gate A27 put the battery in every cell's
    content address, and :func:`_at_address` compares the terms it can compare
    against a constant -- but the battery has no module-level constant to compare
    against, because it is declared per scenario on the environment. So two rows
    differing *only* in battery both matched the filter and were pooled: measured
    on a constructed pair, D2 of -1.0 and -9.0 came back as one cell at -5.0 with
    nothing raising, and at a colliding replicate the seed check fired instead
    and blamed "seeds 7 and 7" -- a re-seed that had not happened.

    D2 and D3 mean something different under a different battery, and D5 reads it
    too, so pooling is averaging answers to different questions. Raising is the
    only honest option for the reason :func:`_refuse_reseeded` gives: under the
    fourth invariant both rows are legitimate and neither supersedes the other,
    so this module cannot pick which battery the caller meant.

    Per **scenario**, not across the report. Two scenarios with different design
    spaces have different batteries by construction, and refusing that would
    refuse every well-formed multi-scenario report.
    """
    seen: dict[str, str] = {}
    for row in rows:
        _system, scenario = _coordinate(row)
        battery = row.key.config["battery"]
        earlier = seen.setdefault(scenario, battery)
        if earlier != battery:
            first, second = sorted((earlier, battery))
            raise MalformedDesignError(
                f"scenario {scenario} has rows scored on two held-out batteries, "
                f"{first} and {second}. D2, D3 and D5 are defined over the "
                f"battery, so rows under two of them answer different questions "
                f"and pooling them would report the average as one cell. The "
                f"battery is part of the cell address, so both rows are "
                f"legitimate under the fourth invariant and neither supersedes "
                f"the other; select the battery you mean"
            )


def _refuse_superseded_battery(
    rows: Sequence[LedgerEntry],
    battery: Callable[[ScenarioId], Sequence[ExperimentDesign]],
) -> None:
    """Raise if a scenario's rows carry a battery it no longer declares.

    The third of this module's refusals and the one that needed a parameter.
    :func:`_at_address` compares every address term it can against a known value
    but can only require ``battery`` to be *present*, because a battery is
    declared per scenario on an environment's
    :class:`~sciagent.eval.scenarios.Scenario` and the first invariant forbids
    this package from importing one. :func:`_refuse_mixed_batteries` asks a
    different question -- whether the surviving rows agree with *each other* --
    and so fires only when two batteries coexist for one scenario. A ledger
    whose rows agree unanimously on a battery that has since been replaced
    passes both, and was rendered as the current campaign. Not an exotic case:
    any change to a battery's membership makes every earlier row exactly this,
    and no version *column* moves to say so.

    So the declaration arrives as a callback, the sibling of ``scenario_class``
    and of the one :func:`~sciagent.eval.matrix.run_matrix` takes, and it is
    **required** for the reason :func:`~sciagent.eval.matrix.cell_key` gives for
    its own: a default reproduces the defect for every caller who forgets it.

    **Refusing rather than selecting**, and the difference is not stylistic. A
    stale ``dimensions`` row is excluded silently because the caller *chose* the
    reading they asked for; a caller cannot choose a battery, since the callback
    returns whatever the scenario declares now. Excluding would therefore drop
    rows the operator has no way to ask for back and hand them a report whose
    replicate counts had quietly fallen. A27's reasoning binds unchanged: under
    the fourth invariant the recorded rows are legitimate and this module cannot
    pick which campaign was meant.

    Per **scenario**, for the reason :func:`_refuse_mixed_batteries` is. A ledger
    holding one scenario at its declared battery beside another entirely at a
    superseded one is refused on the second: the first being current says
    nothing about the second, and a cell built wholly on a replaced battery is
    the defect whether or not a sibling scenario is up to date. That ledger is
    what a battery change leaves behind when the cells are re-scored scenario by
    scenario -- the state between the first and the last.

    Not what a *re-derivation* leaves behind, and the difference is worth
    stating because the backlog entry this gate came from conflates them. A40's
    two generations are separated by ``METRIC_VERSION``, which is on
    :class:`~sciagent.eval.matrix.CampaignAddress` and which :func:`_at_address`
    already selects on, so its old rows never reach this check. The generations
    this function exists for are the ones that arise with **no version column
    moving at all**, which is exactly why they have to be refused rather than
    sorted.

    The offending scenarios are sorted before one is named. :func:`summarise`
    promises its rendering does not depend on the order rows arrived in, and an
    exception string is output too -- the same care :func:`_refuse_reseeded`
    takes with the two seeds it names.
    """
    recorded: dict[str, str] = {}
    declared: dict[str, str] = {}
    for row in rows:
        _system, scenario = _coordinate(row)
        if scenario in recorded:
            # One term per scenario: `_refuse_mixed_batteries` has already
            # raised if this scenario's rows disagree, so reading the first is
            # reading all of them -- and the callback is asked once per
            # scenario rather than once per row.
            continue
        recorded[scenario] = row.key.config["battery"]
        declared[scenario] = battery_key(battery(ScenarioId(scenario)))

    offending = sorted(name for name in recorded if recorded[name] != declared[name])
    if not offending:
        return
    name = offending[0]
    # Named one at a time rather than all at once, but the count is not
    # incidental: every scenario of a slice may share one declaration, so a
    # single membership change makes all of them offending together and a
    # message naming one would read as an isolated fault.
    others = (
        " "
        + "; ".join(f"{other} carries {recorded[other]}" for other in offending[1:])
        + f" -- {len(offending)} scenarios in all carry a battery they do not "
        f"declare."
        if len(offending) > 1
        else ""
    )
    raise MalformedDesignError(
        f"scenario {name} has rows scored on held-out battery {recorded[name]}, "
        f"which it does not declare -- it declares {declared[name]}. D2, D3 and "
        f"D5 are defined over the battery, so these rows answer a question set "
        f"that has since been replaced, and reporting them would render a "
        f"superseded campaign as the current one. No version column moves when a "
        f"battery's membership changes, which is why nothing else here excludes "
        f"them. The battery is part of the cell address, so the rows are "
        f"legitimate under the fourth invariant and are not stale rows to be "
        f"dropped; re-deriving them is a campaign re-run and not a report "
        f"option.{others}"
    )


def _coordinate(row: LedgerEntry) -> tuple[str, str]:
    """Return a row's ``(system, scenario)``, raising if its config lacks either."""
    config = row.key.config
    system, scenario = config.get("system"), config.get("scenario")
    if not system or not scenario:
        raise MalformedDesignError(
            f"row {row.digest} is addressed to this matrix but names no "
            f"system/scenario pair; its config is {dict(config)!r}"
        )
    # These reach rendered output too, out of the ledger's config rather than
    # from a caller's argument -- so the ASCII guarantee needs them as much as it
    # needs the platform.
    _refuse_non_ascii("system", system)
    _refuse_non_ascii("scenario", scenario)
    return system, scenario


def summarise(
    entries: Iterable[LedgerEntry],
    *,
    address: CampaignAddress,
    scenario_class: Callable[[ScenarioId], ScenarioClass],
    battery: Callable[[ScenarioId], Sequence[ExperimentDesign]],
    platform: str,
    grammar: GrammarVersion,
) -> MatrixReport:
    """Summarise every row of a campaign at one address.

    Guarantees :attr:`MatrixReport.cells` -- and therefore everything
    :func:`render` prints -- is independent of the order the rows arrive in: cells
    come back in ``(system, scenario)`` order and every fold runs through
    :mod:`sciagent.core.reductions`.

    The :class:`MatrixReport` *value* is not order-independent, and the difference
    is worth stating rather than rounding off: :attr:`MatrixReport.rows` retains
    the order given, so two reports over one campaign whose rows arrived
    differently render identically and compare unequal. The rendering is the
    reported artefact; ``rows`` is retained for :func:`contrast` to re-aggregate
    from.

    Only rows matching the whole ``address`` are read -- matrix version,
    partition, environment, data and metric versions, and
    :data:`~sciagent.eval.scoring.DIMENSION_VERSION` -- so stale rows left by a
    re-addressed cell, rows from another partition, and rows scored under an
    earlier reading of SPEC §8's dimensions are all excluded by construction
    rather than by the caller filtering first. See this module's docstring for
    why that job lands here.

    ``battery`` returns the scenario's preregistered held-out battery and is
    **required**, the sibling of ``scenario_class`` and of the callback
    :func:`~sciagent.eval.matrix.run_matrix` takes. It is a callback for the
    reason that one is -- the declaration lives on the environment's
    :class:`~sciagent.eval.scenarios.Scenario` and this package may not reach it
    -- and required rather than defaulted because a default would reproduce the
    defect it closes for every caller who forgot it. Rows carrying a battery
    their scenario no longer declares are **refused**, not excluded; see
    :func:`_refuse_superseded_battery` for why that asymmetry with ``dimensions``
    is the right way round.

    ``platform`` and ``grammar`` are **required and validated**, not defaulted.
    Neither is recoverable from the ledger and both must appear wherever these
    numbers are reported; refusing an unlabelled report is how that holds without
    depending on anybody remembering.

    Raises :class:`~sciagent.core.errors.MalformedDesignError` if no row matches
    the address. An empty report is indistinguishable from a matrix that ran and
    produced nothing, which is the one reading nobody should reach by accident.
    """
    if not platform.strip():
        raise MalformedDesignError(
            "a report needs the platform its cells ran on. docs/DECISIONS.md "
            "records a measured Windows/Ubuntu divergence in a reported number, "
            "and the registry content-addresses with no platform term -- so the "
            "ledger cannot supply this and an unlabelled report cannot be "
            "compared with any other"
        )
    if not str(grammar).strip():
        raise MalformedDesignError(
            "a report needs the grammar version its cells were scored under. D1 "
            "is grammar.distance and D6 is grammar.code_length, so both are "
            "grammar-relative and neither can be interpreted unnamed"
        )
    _refuse_non_ascii("platform", platform)
    _refuse_non_ascii("grammar", str(grammar))

    # Materialised, because the diagnostic below reads them a second time and
    # ``entries`` is an Iterable: a generator caller would have found it empty
    # and reported "the ledger holds nothing" about a ledger that holds rows.
    recorded = tuple(entries)
    rows = tuple(row for row in recorded if _at_address(row, address))
    if not rows:
        others = sorted(
            {
                row.key.config.get("dimensions", "spec8/1 (unlabelled)")
                for row in recorded
                if row.key.config.get("dimensions") != DIMENSION_VERSION
            }
        )
        # Only when rows under another reading actually exist. Volunteering it
        # unconditionally told the operator of an empty ledger that their rows
        # needed re-deriving, which is a diagnosis of a problem they do not have.
        elsewhere = (
            f" The ledger holds rows under {others!r} instead: rows scored under "
            f"another reading of SPEC §8 are excluded rather than pooled, and "
            f"re-deriving them is a campaign re-run and not a report option."
            if others
            else ""
        )
        # The battery term excludes rows whose *every other* term matches, so a
        # message naming only the terms above describes a row that does match it
        # and leaves the reader hunting a version that moved. This is the case
        # anyone reaching for the recorded 1,120-row matrix meets first: those
        # rows were scored before gate A27, on a battery derived from whatever
        # each arm happened not to run. Found by review, on the message rather
        # than on the filter.
        pre_battery = sum(
            1
            for row in recorded
            if row.key.config.get("battery") is None
            and row.key.config.get("dimensions") == DIMENSION_VERSION
        )
        unbatteried = (
            f" {pre_battery} row(s) match every other term but carry no battery "
            f"in their address, so they were recorded before gate A27 and were "
            f"scored on a battery derived per run rather than declared: D2, D3 "
            f"and D5 on them answer a question that varied by arm. They are "
            f"excluded rather than pooled, and re-deriving them is a campaign "
            f"re-run and not a report option."
            if pre_battery
            else ""
        )
        # The metric version gets the same treatment as the dimension reading,
        # and needs it more. A caller *chooses* this one -- it is
        # `--metric-version` on `scripts/report_matrix.py`'s command line -- so
        # naming the versions actually present turns "no row matches" into an
        # argument they can retype. Without it the message lists the version
        # asked for and stays silent about the generation the ledger is full of,
        # which is precisely the state a re-derivation leaves behind: two
        # generations in one ledger, one of them the answer.
        # Only rows the metric version is the *sole* obstacle for. A row failing
        # on the battery as well is not a `--metric-version` away from being
        # reported, and saying so would send an operator to retype an argument
        # that changes nothing -- worse, on the recorded 1,120-row ledger it
        # would contradict the `unbatteried` clause standing beside it in the
        # same message. Found by review; the first version filtered on the
        # metric version alone.
        recorded_versions = sorted(
            {
                str(row.key.metric_version)
                for row in recorded
                if row.key.metric_version != address.metric_version
                and _at_address(
                    row, replace(address, metric_version=row.key.metric_version)
                )
            }
        )
        generations = (
            f" The ledger holds rows differing only in their metric version, at "
            f"{recorded_versions!r}: rows scored under another metric version "
            f"are excluded rather than pooled, and asking for one of those is a "
            f"--metric-version away."
            if recorded_versions
            else ""
        )
        raise MalformedDesignError(
            f"no row matches campaign {MATRIX_VERSION} under dimension reading "
            f"{DIMENSION_VERSION} at {address.env_version}/"
            f"{address.data_version}/{address.metric_version} on "
            f"{address.partition.value}. An empty report reads as a matrix that "
            f"ran and produced nothing.{elsewhere}{generations}{unbatteried}"
        )

    grouped: dict[tuple[str, str], list[LedgerEntry]] = {}
    for row in rows:
        grouped.setdefault(_coordinate(row), []).append(row)
    # Batteries before seeds: a mixed-battery pair also trips the seed check
    # when its replicate indices collide, and the message it gives there blames
    # a re-seed that did not happen. A superseded campaign does the same, so it
    # goes on the same side of that line.
    #
    # Agreement before currency, and this order is load-bearing too. A ledger
    # holding both generations for one scenario is A27's case and keeps A27's
    # diagnosis -- two batteries coexist and the module cannot pick. Checked the
    # other way round it would be reported as a superseded campaign, saying
    # nothing about the current rows sitting beside it.
    _refuse_mixed_batteries(rows)
    _refuse_superseded_battery(rows, battery)
    _refuse_reseeded(rows)

    cells = tuple(
        _cell(system, ScenarioId(scenario), grouped[(system, scenario)], scenario_class)
        for system, scenario in sorted(grouped)
    )
    return MatrixReport(
        cells=cells,
        rows=rows,
        platform=platform.strip(),
        grammar=grammar,
        address=address,
        matrix_version=MATRIX_VERSION,
    )


def _cell(
    system: str,
    scenario: ScenarioId,
    rows: Sequence[LedgerEntry],
    scenario_class: Callable[[ScenarioId], ScenarioClass],
) -> CellSummary:
    """Return one cell's summary over the rows selected for it.

    The classification is checked, not trusted. It is one of the two semantic
    inputs this layer takes from a callback, and it decides which figure is the
    headline -- :func:`~sciagent.eval.scoring.primary_dimension` indexes a
    mapping with it, so an unknown class raised a bare ``KeyError`` out of
    ``scoring.py`` rather than a typed error from here.

    The other is ``summarise``'s ``battery``, and it is checked differently
    rather than not at all: a wrong return there produces a term matching no row
    and :func:`_refuse_superseded_battery` refuses, so it fails closed without
    needing a constant to validate against. The one case neither catches is a
    caller who reads the ledger and hands back what it already holds, which
    makes the check vacuous -- the same shape as the known
    :class:`Preregistration` limit this module documents, and pinned nowhere for
    the same reason: it cannot be closed from inside ``sciagent``, which may not
    hold the declaration.
    """
    kind = scenario_class(scenario)
    if kind not in SCENARIO_CLASSES:
        raise MalformedDesignError(
            f"scenario {scenario!r} was classified {kind!r}, which is not one of "
            f"{list(SCENARIO_CLASSES)!r}. The class decides which dimension a "
            f"headline figure for this cell may cite, so it cannot be guessed"
        )
    return CellSummary(
        system=system,
        scenario=scenario,
        scenario_class=kind,
        replicates=len(rows),
        # One term for the whole cell: `_refuse_mixed_batteries` has already
        # raised if this scenario's rows disagree, so reading the first is
        # reading all of them.
        battery=rows[0].key.config["battery"],
        dimensions=FrozenDict[str, DimensionSummary](
            {name: _summarise(_values(rows, name)) for name in DIMENSIONS}
        ),
        primary=primary_dimension(kind),
        truth_mass=_summarise(_values(rows, "truth_mass")),
        log_score=_summarise(_values(rows, "log_score")),
        correct_rate=_rate(rows, "correct"),
        identified_rate=_rate(rows, "identified"),
        inadequate_rate=_rate(rows, _INADEQUATE),
        experiments=_summarise(_values(rows, "experiments")),
    )


# --------------------------------------------------------------------------
# SPEC §9's contrast
# --------------------------------------------------------------------------


def contrast(
    report: MatrixReport,
    *,
    scenario: ScenarioId,
    treatment: str,
    comparator: str,
    dimension: str,
    conditional_on_inadequacy: bool = True,
    preregistration: Preregistration | None = None,
) -> Contrast:
    """Compare two arms of one scenario on one dimension.

    ``conditional_on_inadequacy`` defaults to ``True`` because SPEC §9 states its
    primary contrast that way -- "conditional on inadequacy detection" -- and
    conditioning is a **filter on replicates**, not a caption. A contrast that
    ignored it would answer a different question under the same name, so the
    stricter reading is the default and relaxing it is explicit.

    Guarantees the arms are re-aggregated from the report's own rows, so the
    conditioned figures are means over the replicates that actually detected
    rather than a correction applied to a summary.

    Raises :class:`~sciagent.core.errors.MalformedDesignError` if either arm has
    no rows, if ``dimension`` is not one this report holds, if conditioning
    leaves an arm empty, or if either interval is unavailable. Each of those is a
    fact about the matrix worth reporting, and none of them is a number.
    """
    known = (*DIMENSIONS, "truth_mass", "log_score", "experiments")
    if dimension not in known:
        raise MalformedDesignError(
            f"{dimension!r} is not a dimension this report holds; it holds "
            f"{list(known)!r}"
        )
    if treatment == comparator:
        # Otherwise the two arms collapse into one dict key and the contrast
        # reports overlaps=True, exceeds=False, paired=True -- an answer, to a
        # question that was not asked.
        raise MalformedDesignError(
            f"a contrast needs two arms; {treatment!r} was given as both the "
            f"treatment and the comparator"
        )

    arms = {
        system: _arm(report, scenario, system, conditional_on_inadequacy)
        for system in (treatment, comparator)
    }
    summaries = {
        system: _summarise(_values(rows, dimension)) for system, rows in arms.items()
    }
    left, right = summaries[treatment], summaries[comparator]
    bounds = (left.low, left.high, right.low, right.high)
    if not all(math.isfinite(bound) for bound in bounds):
        raise MalformedDesignError(
            f"contrast on {dimension!r} has no usable interval: "
            f"{treatment} is {left!r} and {comparator} is {right!r}. Two finite "
            f"replicates an arm are needed before intervals can be compared"
        )
    return Contrast(
        scenario=scenario,
        dimension=dimension,
        treatment_system=treatment,
        comparator_system=comparator,
        treatment=left,
        comparator=right,
        conditioned_on_inadequacy=conditional_on_inadequacy,
        treatment_seeds=_seeds_of(arms[treatment]),
        comparator_seeds=_seeds_of(arms[comparator]),
        overlaps=left.low <= right.high and right.low <= left.high,
        preregistered=preregistration is not None
        and preregistration.describes(
            scenario=scenario,
            treatment=treatment,
            comparator=comparator,
            dimension=dimension,
            conditional_on_inadequacy=conditional_on_inadequacy,
        ),
    )


def _seeds_of(rows: Sequence[LedgerEntry]) -> tuple[int, ...]:
    """Return the seeds a set of rows ran under, sorted and deduplicated.

    Sorted so that two arms are comparable as sets rather than in the order rows
    happened to arrive, which is what :attr:`Contrast.paired` needs.
    """
    return tuple(sorted({int(row.key.seed) for row in rows}))


def _arm(
    report: MatrixReport,
    scenario: ScenarioId,
    system: str,
    conditional_on_inadequacy: bool,
) -> tuple[LedgerEntry, ...]:
    """Return one arm's rows, conditioned on inadequacy detection if asked."""
    rows = tuple(
        row for row in report.rows if _coordinate(row) == (system, str(scenario))
    )
    if not rows:
        raise MalformedDesignError(
            f"the report holds no cell for {system} on {scenario}; it holds "
            f"{sorted({cell.system for cell in report.cells})!r} on "
            f"{sorted({str(cell.scenario) for cell in report.cells})!r}"
        )
    if not conditional_on_inadequacy:
        return rows
    # Through _flags, not _values and not a bare subscript. A MatrixReport built
    # by hand has not been past _rate, so it is here that both a missing field and
    # a present-but-non-boolean one have to be refused -- the second is the one an
    # earlier version of this line missed, and it perturbed the arm silently
    # rather than raising. See _flags.
    flags = _flags(rows, _INADEQUATE)
    detected = tuple(row for row, flag in zip(rows, flags, strict=True) if flag == 1.0)
    if not detected:
        raise MalformedDesignError(
            f"no replicate of {system} on {scenario} detected inadequacy, so a "
            f"contrast conditional on inadequacy detection has no answer on this "
            f"matrix. That is a finding to report, not a number to compute"
        )
    return detected


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

#: Row labels of the per-cell block, and how each is read out of a
#: :class:`DimensionSummary`. One row per statistic and one column per dimension,
#: rather than the reverse: it keeps all six dimensions visible side by side on
#: one screen, which is what makes a collapsed column conspicuous by its absence.
_STATS: Final[tuple[tuple[str, Callable[[DimensionSummary], float]], ...]] = (
    ("point", lambda summary: summary.point),
    ("low", lambda summary: summary.low),
    ("high", lambda summary: summary.high),
)

_LABEL_WIDTH: Final = 34
_COLUMN_WIDTH: Final = 11


def render(report: MatrixReport) -> str:
    """Return the report as SPEC §8's vector table, as text.

    Guarantees the same report renders byte-identically every time, and that a
    report built from the same rows in a different order renders identically too.

    **There is no total column and no rank column**, and this is the function
    where that matters most: the numbers are only useful to a reader once
    rendered, and the natural rendering is the forbidden one. Six dimension
    columns, each read separately, with the primary one marked per cell.
    """
    lines = [
        f"SPEC sec. 9 experiment matrix -- {report.matrix_version}",
        "",
        f"  {'platform':<16s}{report.platform}",
        f"  {'grammar':<16s}{report.grammar}",
        f"  {'env / data':<16s}"
        f"{report.address.env_version} / {report.address.data_version}",
        f"  {'metric':<16s}{report.address.metric_version}",
        # The dimension reading is on the header for the same reason every other
        # generation term is, and it earns its line by being the one that
        # *moves*. D1-D6 are computed in `sciagent.eval.scoring` from the truth
        # and the table, so no version column above changes when their
        # definition does -- which is why `DIMENSION_VERSION` exists at all. A
        # re-derivation under fixed instruments therefore differs from the
        # campaign it re-derives in this term and in the battery, and in nothing
        # printed here; without it the two generations render identically and a
        # reader comparing two reports has no way to tell which is which.
        f"  {'dimensions':<16s}{DIMENSION_VERSION}",
        f"  {'partition':<16s}{report.address.partition.value}",
        f"  {'cells':<16s}{len(report.cells)} ({len(report.rows)} replicates)",
        "",
    ]
    level = _level_of(report)
    lines.append(
        f"  {'intervals':<16s}{level:.0%} normal, on the mean over replicates; "
        f"not clipped to each dimension's support"
    )
    lines.append("")
    lines.extend(f"  {line}" for line in _CAVEAT.splitlines())
    lines.append("")
    lines.append("  dimensions, in SPEC sec. 8's order:")
    for index, name in enumerate(DIMENSIONS, start=1):
        lines.append(f"    d{index}  {name}")
    lines.append("")

    header = "".join(
        f"{f'd{index}':>{_COLUMN_WIDTH}s}" for index in range(1, len(DIMENSIONS) + 1)
    )
    for cell in report.cells:
        lines.extend(_cell_block(cell, header))
    return "\n".join(lines) + "\n"


def _cell_block(cell: CellSummary, header: str) -> list[str]:
    """Return the lines for one cell: its heading, its six columns, its rates."""
    primary = cell.primary or "none -- read under the closed-world score"
    lines = [
        f"{cell.system} / {cell.scenario}  ({cell.scenario_class}, "
        f"{cell.replicates} replicates)",
        f"  primary dimension: {primary}",
        f"  held-out battery:  {cell.battery}",
        f"  {'':<{_LABEL_WIDTH}s}{header}",
    ]
    summaries = [cell.dimensions[name] for name in DIMENSIONS]
    for label, read in _STATS:
        cells = "".join(f"{read(summary):>{_COLUMN_WIDTH}.4f}" for summary in summaries)
        lines.append(f"  {label:<{_LABEL_WIDTH}s}{cells}")
    counts = "".join(f"{summary.n_finite:>{_COLUMN_WIDTH}d}" for summary in summaries)
    lines.append(f"  {'n (finite)':<{_LABEL_WIDTH}s}{counts}")

    # Named per dimension rather than left as a column of counts to subtract:
    # which dimension lost replicates is the part a reader has to act on, and an
    # excluded -inf on D2 means something quite different from one on D4.
    for name in DIMENSIONS:
        summary = cell.dimensions[name]
        if summary.n_non_finite:
            lines.append(
                f"  non-finite: {name} "
                f"{summary.n_non_finite}/{summary.n_finite + summary.n_non_finite}"
            )
    lines.extend(
        [
            f"  {'closed-world truth_mass':<{_LABEL_WIDTH}s}"
            f"{_interval(cell.truth_mass)}",
            f"  {'closed-world log_score':<{_LABEL_WIDTH}s}{_interval(cell.log_score)}",
            f"  {'experiments':<{_LABEL_WIDTH}s}{_interval(cell.experiments)}",
            f"  {'rates correct/ident/inadequate':<{_LABEL_WIDTH}s}"
            f"{cell.correct_rate:>10.3f}  {cell.identified_rate:>10.3f}  "
            f"{cell.inadequate_rate:>10.3f}",
            "",
        ]
    )
    return lines


def _level_of(report: MatrixReport) -> float:
    """Return the one confidence level every summary in ``report`` was built at.

    The level is read off the data rather than restated in :func:`render`'s
    template, so a printed coverage cannot drift from the computed one -- an
    interval printed without its level is not a quotable figure, and
    :attr:`DimensionSummary.level` was stored and rendered by nothing until this
    existed.

    **Reads every summary and refuses a disagreement**, rather than taking the
    first cell's. Today :func:`_summarise` writes the module constant on every
    branch so no disagreement is reachable, and an earlier version relied on
    exactly that -- it indexed ``cells[0]``, which raised ``IndexError`` on a
    report with no cells and ``KeyError`` on one whose first cell lacked D1, both
    untyped and both out of a module whose every other failure is a
    :class:`~sciagent.core.errors.MalformedDesignError`. Worse, with two cells
    disagreeing it printed the first one's level as though it governed the table,
    which is the single thing this field exists to prevent.
    """
    levels = sorted(
        {
            summary.level
            for cell in report.cells
            for summary in (
                *(
                    cell.dimensions[name]
                    for name in DIMENSIONS
                    if name in cell.dimensions
                ),
                cell.truth_mass,
                cell.log_score,
                cell.experiments,
            )
        }
    )
    if not levels:
        raise MalformedDesignError(
            "a report with no summarised cell has no interval level to state; "
            "summarise refuses an empty row set, so this is a hand-built report"
        )
    if len(levels) > 1:
        raise MalformedDesignError(
            f"this report holds intervals at more than one confidence level "
            f"({levels!r}); one header cannot label them, and printing either "
            f"would mislabel the other"
        )
    return levels[0]


def _interval(summary: DimensionSummary) -> str:
    """Return ``point [low, high]  n`` for a figure reported on its own line."""
    return (
        f"{summary.point:>10.4f}  [{summary.low:>10.4f}, {summary.high:>10.4f}]"
        f"  n={summary.n_finite}"
        + (f"  ({summary.n_non_finite} non-finite)" if summary.n_non_finite else "")
    )
