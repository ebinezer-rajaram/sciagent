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

Three things have to appear wherever these numbers appear, and none can be
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

**Which numpy every cell was computed under.** SPEC's third invariant is
bit-exact determinism, and that guarantee rides numpy's generator bit-stream
stability: the same seed reaches the same draws only for as long as numpy's
streams do not move. ``pyproject`` declares ``numpy>=2.1``, a lower bound, so a
``uv lock --upgrade`` resolves a different one without any file the registry
addresses over changing. The address is (env version, config, data version,
metric version, seed) and has no dependency term -- so two campaigns run under
different numpy versions are indistinguishable by their addresses, and the
report is the only place the difference can surface. Gate A33 is why it is here.

This does not *pin* numpy, and the difference is worth being clear about: the
lockfile can still move. What changes is that the move stops being silent, since
two reports built under different resolutions no longer render identically.

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
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    "CriterionFour",
    "DimensionSummary",
    "MatrixReport",
    "Preregistration",
    "contrast",
    "criterion_four",
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

#: Payload key holding the harness-evaluated Stage A probe, which SPEC §12
#: criterion 4 is read off. Named beside :data:`_INADEQUATE` and never
#: confused with it: that one is the whole-record check, is arm-dependent even
#: in its verdict, and is what :func:`contrast` conditions on.
_PROBE_INADEQUATE: Final = "probe_inadequate"

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

    probe_inadequate_rate: float
    """Fraction of replicates whose Stage A probe fired: §12 criterion 4's rate.

    Beside :attr:`inadequate_rate` rather than replacing it, because the two
    answer different questions and the criterion names this one. The
    probe is evaluated by the harness for every arm, so on a given scenario this
    figure is the same across arms by construction -- which is what makes
    reading it *down* a scenario meaningful: the rate on S11 is the instrument's
    power and the rate on S1-S7 and S9 is its size.

    This is the figure :func:`criterion_four` is evaluated over. Under C1 the
    criterion compared V7's rate against B1's and was therefore unfailable, since
    the two are identical by the paragraph above; it was reworded absolutely on
    2026-08-26 and moved to §12's Infrastructure block, so what it now asks of
    this figure is that it be positive on S11 and zero on S1-S7 and S9. Reported
    per cell regardless of that verdict, because the rate is what a reader needs
    and the criterion is one reading of it rather than a replacement for it.
    """

    experiments: DimensionSummary
    """Experiments the system spent, averaged over replicates."""

    autonomy_fraction: DimensionSummary
    """§12 criterion 11's figure, pooled over this cell's replicates.

    SPEC F10 asks for it *"alongside every performance figure"*, and this is
    where the performance figures live -- hence a field on every cell rather
    than a table of its own after them. Criterion 11 is *"reported for every
    investigation"*, which is a claim about the report and not about any arm's
    conduct, so unlike the rates beside it this one cannot be failed by a system.
    It can only be absent, which is what it was.

    A replicate that took no decision contributes ``nan`` and is therefore
    excluded from the mean and counted in
    :attr:`DimensionSummary.n_non_finite` -- see
    :meth:`~sciagent.eval.matrix.CellReading.as_payload` for why it crosses the
    ledger that way rather than as a number.
    """

    claims: DimensionSummary
    adjudicated: DimensionSummary
    """The two counts :attr:`adjudicated_share` is a ratio of, pooled per cell.

    Carried because criterion 10 is a share of **claims** and this class pools
    per **replicate**, and the two are not the same number wherever a cell's
    replicates afford different-sized claim populations. Without them the
    criterion's own statistic is unrecoverable from the report -- exactly the
    gap :attr:`max_defect_mass` exists to close for criterion 9.
    """

    adjudication_rate: DimensionSummary
    """§12 criterion 10's figure, pooled over this cell's replicates.

    *"At least 90% of claims adjudicated by the verifier without human input"*
    is read off a report, and until gate A30 the report carried nothing to read
    it from: :func:`sciagent.verify.verify` had no caller in ``src`` at all, so
    the only measurement of it anywhere was over a claim population a test built.

    A replicate that afforded no claim contributes ``nan`` and is excluded from
    the mean and counted in :attr:`DimensionSummary.n_non_finite`, exactly as an
    absent autonomy fraction is -- see
    :meth:`~sciagent.eval.matrix.CellReading.as_payload`.

    **This is the mean of per-replicate rates, and criterion 10 is not that
    number.** *"At least 90% of claims adjudicated"* is a share of claims, so
    replicates weight by how many claims each afforded; this weights them
    equally. Two replicates at 80/100 and 4/4 average to 0.9000 and clear the
    bar, while the share they actually represent is 84/104 = 0.8077 and does
    not. :attr:`adjudicated_share` is the criterion's statistic; this one is
    kept beside it because it is the only figure here carrying an interval and
    a non-finite count, which is what shows a replicate that afforded nothing.
    """

    @property
    def adjudicated_share(self) -> float:
        """Return §12 criterion 10's statistic: adjudicated claims over claims.

        Guarantees the figure is weighted by claim count rather than by
        replicate, so a cell whose replicates afford unequal populations is
        reported on the quantity the criterion names.

        :attr:`claims` and :attr:`adjudicated` are means over the *same*
        replicate set and both are always finite -- they come off non-negative
        integer counts -- so the replicate count cancels and this is the
        claim-weighted share rather than the replicate-weighted one. That is the
        whole distinction the field exists for.

        **It is not exact, and an earlier version of this paragraph said it
        was.** Two separately-rounded means divided are not
        ``sum(adjudicated) / sum(claims)`` in IEEE-754: on claims ``[33, 33,
        34]`` against adjudicated ``[26, 27, 27]`` this returns
        ``0.7999999999999999`` where the true share is ``0.8``. The error is at
        the last unit in the last place and cannot move the statistic's meaning;
        it can in principle decide a comparison against criterion 10's ``0.90``
        that was already on a knife edge, which is a case where the bar is not
        answering anything either way. Carrying the raw sums instead would need
        :func:`_summarise` to report one, and a report layer plumbing sums to
        chase an ulp is a worse trade than saying so here.

        ``nan`` where the cell afforded no claim at all, for the reason
        :attr:`~sciagent.eval.campaign.Adjudication.rate` is ``None`` there.

        **Currently equal to** :attr:`adjudication_rate`'s point on every
        conventional arm, and that is a fact about the arms rather than about
        the two statistics: measured over six replicates of B1 and V1 on S9 and
        S11, every replicate afforded exactly 16 and 80 claims respectively, so
        the weights are uniform and the two agree. An arm holding a proposal
        layer entertains a different number of structures per replicate, so they
        part company on V7, V3 and V4 -- which is to say on every arm the
        recorded campaign exists to compare.
        """
        if not math.isfinite(self.claims.point) or self.claims.point <= 0.0:
            return math.nan
        return self.adjudicated.point / self.claims.point

    contradictions: DimensionSummary
    zombie_claims: DimensionSummary
    """§12 criterion 8's two quantities, pooled the same way.

    Both, because the criterion is *"zero graph contradictions **and** zero
    zombie hypotheses"* and one count cannot answer it: a nonzero pooled figure
    could be a reversal, which is neither of the two things named. See
    :func:`sciagent.verify.contradiction.zombie`, which owns the second
    predicate and is what the payload counts.

    Means rather than totals, like everything else on this class. A campaign
    meeting the criterion reports 0.000 in both columns, and the mean is what
    makes a single offending replicate visible as a small nonzero rather than
    hidden by however many quiet ones surround it.
    """

    null_mass: DimensionSummary
    abstain_mass: DimensionSummary
    max_defect_mass: DimensionSummary
    """§12 criterion 9's three quantities, pooled the same way.

    All three, because the criterion compares the first two against the third
    and the report carried none of them. :attr:`max_defect_mass` is not
    ``leading_mass`` restated: that one is the maximum over every hypothesis
    including the null, so on S9 and S10 -- the two scenarios criterion 9 names,
    and the two where the null is *supposed* to lead -- it says nothing about the
    largest defect. See :func:`~sciagent.eval.matrix.largest_defect_mass`.

    Rendered rather than merely carried, for the reason the criterion exists:
    *"not decidable from the report"* was the defect, and a field a reader has to
    open the ledger to see has not fixed it.
    """


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

    numpy_version: str
    """The numpy the cells were computed under. Beside :attr:`platform` and for
    the same reason: bit-exact determinism rides numpy's generator bit-stream
    stability, ``pyproject`` declares a lower bound only, and the registry
    content-addresses with no dependency term -- so a ``uv lock --upgrade`` could
    move every figure below while every content address stayed fixed."""

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

    residual_asymmetries: tuple[str, ...] = ()
    """Protocol deltas between the two arms that this contrast does **not**
    control for, stated in advance.

    A contrast is only as clean as what the arms share, and the arms do not share
    everything. Recording the remainder is what turns a confound into a stated
    limitation: a reader can price a difference that is named, and cannot price
    one that is not. ``docs/BACKLOG.md``'s A34 entry is where the requirement
    comes from, and the instance that carries it is in
    ``environments/pointproc/matrix.py`` -- the framework may not know which
    systems a campaign compares.

    Deliberately **not** part of :meth:`describes`. That compares the five fields
    that say *which* contrast was declared, and a note about what the comparison
    does not control for is not one of them. Folding it in would make every
    contrast report ``preregistered=False`` the moment a residual was written
    down, which is the opposite of what recording one is for.
    """

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
    paired_difference: DimensionSummary | None
    """The mean within-seed difference, ``treatment - comparator``, or ``None``.

    Reported **alongside** the two independent intervals above and never instead
    of them. :mod:`sciagent.eval.matrix` pairs seeds across arms by construction
    -- a seed is a function of the scenario and the replicate index alone -- and
    until this existed that pairing was established at real cost and then thrown
    away here, where two independent normal intervals were compared. This is an
    additional *reading* of the same ledger rows: it records nothing, runs no
    cell, and collapses no dimension, since §8's prohibition is on combining the
    six and a paired difference on one dimension is still one dimension.

    Computed by the same :func:`_summarise` the two arms go through, so the
    estimator, the confidence level and the exactly-summed folding are not merely
    equivalent to theirs but literally the same code. A pair whose either half is
    non-finite yields a non-finite difference, which that function already
    excludes and counts in :attr:`DimensionSummary.n_non_finite` -- so
    ``n_finite`` here is the number of *seeds* that contributed, and is not
    generally either arm's count.

    **It can be zero while both arms are usable, and this does not raise.** The
    two arm summaries are guaranteed finite, because :func:`contrast` refuses to
    return an unusable interval; that guarantee does **not** extend here. Each
    arm needs two finite readings, while a *pair* needs one seed finite in both,
    and on D2 -- ``-inf`` whenever a candidate ruled out something that happens
    -- the two can come apart entirely. Reported as ``point=nan`` with
    ``n_finite=0`` rather than refused, because the arms did pair: the reading
    is empty, which is a different fact from the arms not pairing, and
    collapsing the two into ``None`` would lose it. ``scripts/report_matrix.py``
    renders it as its own line rather than as a ``nan`` in the shape of a
    figure.

    ``None`` is a **refusal**, not an absence of interest, and it does not stop
    the contrast: an unpaired matrix still gets everything above. Two cases reach
    it. The seed sets differ, which is :attr:`paired` being ``False`` -- ordinary,
    since conditioning filters each arm by its own flag. Or the sets agree while
    one arm carries two rows for a seed: :func:`_seeds_of` deduplicates, so equal
    sets do not imply one reading per seed, and there is then no fact of the
    matter about which row that seed contributes. Picking one would be this module
    deciding by fiat what :func:`_refuse_reseeded` refuses to decide one address
    over.
    """

    overlaps: bool
    """Whether the two intervals intersect. ``False`` is what §12 criterion 5
    asks for; it is not by itself evidence of anything, at twenty seeds."""

    residual_asymmetries: tuple[str, ...]
    """The declaration's stated protocol deltas, carried onto the result.

    Copied from the :class:`Preregistration` rather than re-derived, and empty
    when none was given. It is here because a limitation nobody reads is not a
    limitation stated: ``docs/BACKLOG.md``'s A34 entry asks for the bounding
    comparison to be preregistered *in the contrast analysis*, and the analysis is
    what a reader sees. ``scripts/report_matrix.py`` prints it under the contrast
    block.
    """

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


#: The scenario §12 criterion 4 requires the Stage A probe to fire on. S11 is
#: the out-of-library case: its truth is in ``edit_grammar`` and absent from
#: ``agent_grammar``, so the entertained space really is inadequate there.
_MUST_FIRE: Final = ScenarioId("S11")

#: The scenarios it requires the probe to stay quiet on -- the ones where the
#: space is both adequate and identifiable, so a firing probe is a false alarm.
#:
#: **Not contiguous, and written out for that reason.** S8 sits inside the span
#: and is not a member: per SPEC §4.5 it is compound, two edits against a space
#: that holds them singly, so a strained probe there is not evidence of a bad
#: instrument. S10 is non-identifiable by construction with a budget below the
#: discriminating threshold, and S12 carries a censoring nuisance producing a
#: strong spurious periodic signature. The criterion says nothing about those
#: three in either direction, and a ``range`` here would quietly say something.
_MUST_BE_QUIET: Final[tuple[ScenarioId, ...]] = (
    ScenarioId("S1"),
    ScenarioId("S2"),
    ScenarioId("S3"),
    ScenarioId("S4"),
    ScenarioId("S5"),
    ScenarioId("S6"),
    ScenarioId("S7"),
    ScenarioId("S9"),
)


@dataclass(frozen=True, slots=True)
class CriterionFour:
    """SPEC §12 criterion 4's verdict on one campaign's Stage A probe rates.

    A verdict about the **instrument**, not about any arm, and the type says so
    by carrying no system field. Gate A29 made the probe arm-symmetric -- it is
    evaluated by the harness before ``investigate`` is called, so its value is a
    function of the scenario and the seed alone -- which means this verdict is
    identical for B1, V1 and V7 by construction and always will be. That is why
    §12 files criterion 4 under Infrastructure beside "A1-A24 passing" and "100%
    reproducibility" rather than under Capability, whose heading reads "V7 versus
    baselines" and whose other members really are comparisons.
    """

    holds: bool
    """Whether the criterion is met: the probe fired on S11 and nowhere it must
    not."""

    fired_on_s11: bool
    """Whether the probe fired on the out-of-library scenario at all -- the power
    clause. ``False`` is an instrument that cannot see the one inadequacy the
    slice is built around."""

    false_positives: tuple[ScenarioId, ...]
    """The scenarios the probe fired on where the space was adequate, in
    :data:`_MUST_BE_QUIET` order -- the size clause, whose absence from the
    original wording meant an instrument firing on all twelve would have passed.

    Empty when the probe stayed quiet everywhere it had to, which includes the
    case where it fired nowhere at all: a blind probe fails on
    :attr:`fired_on_s11` and has no false positive to report.
    """

    def __post_init__(self) -> None:
        """Refuse a verdict whose ``holds`` disagrees with its own two facts.

        The second invariant asks for runtime assertions rather than comments,
        and :class:`~sciagent.eval.campaign.Adjudication` takes the same guard
        for the same reason -- a frozen dataclass of a few bare fields is exactly
        where a comment would otherwise have been the whole of it. Without this,
        the relation the field docstrings describe holds only inside
        :func:`criterion_four`, and any other construction can state a passing
        criterion over a failing instrument.
        """
        implied = self.fired_on_s11 and not self.false_positives
        if self.holds is not implied:
            raise MalformedDesignError(
                f"a criterion 4 verdict states holds={self.holds} while its own "
                f"facts imply {implied}: fired_on_s11={self.fired_on_s11} with "
                f"{len(self.false_positives)} false positive(s). The criterion is "
                f"the conjunction of those two, so a verdict is not free to "
                f"disagree with them"
            )


def criterion_four(probe_rates: Mapping[ScenarioId, float]) -> CriterionFour:
    """Return §12 criterion 4's verdict on a probe rate vector.

    Guarantees a verdict that some input fails and some input passes, which is
    the whole of gate A45. The wording it implements is absolute -- *fires on
    S11; does not fire on S1-S7 or S9* -- and replaces the two V7-versus-B1
    comparisons C1 gave it, which A29 made unfailable by making the probe
    arm-symmetric: paired seeds put both arms on bit-identical rates, so neither
    comparison could ever come out either way.

    "Fires" is any positive rate and "does not fire" is exactly zero, which is
    the criterion's own words. No numeric power threshold is imposed, because
    choosing one is a further decision nobody has taken and defaulting to one
    here would take it silently.

    Raises :class:`~sciagent.core.errors.MalformedDesignError` naming the first
    scenario missing from ``probe_rates``. A criterion evaluated on a partial
    vector is the defect this wording exists to remove, arriving by a second
    door: treating an absent scenario as zero fails an instrument nobody
    measured, and treating it as inapplicable passes the criterion by leaving
    nothing to check.
    """
    for scenario in (*_MUST_BE_QUIET, _MUST_FIRE):
        if scenario not in probe_rates:
            raise MalformedDesignError(
                f"SPEC section 12 criterion 4 is defined over "
                f"{len(_MUST_BE_QUIET) + 1} scenarios and this vector is missing "
                f"{scenario}. A criterion evaluated on the scenarios that happen "
                f"to be present is one no input can fail, which is what this "
                f"criterion was re-worded to stop being."
            )
        rate = probe_rates[scenario]
        if not 0.0 <= rate <= 1.0:
            raise MalformedDesignError(
                f"criterion 4 was handed {rate!r} as the probe rate on "
                f"{scenario}, which is not a fraction of replicates. A "
                f"non-finite rate is the missing-scenario case wearing a float: "
                f"`nan > 0.0` is False, so it would read as a quiet scenario and "
                f"pass the criterion on an instrument nobody measured"
            )
    fired = probe_rates[_MUST_FIRE] > 0.0
    false_positives = tuple(
        scenario for scenario in _MUST_BE_QUIET if probe_rates[scenario] > 0.0
    )
    return CriterionFour(
        holds=fired and not false_positives,
        fired_on_s11=fired,
        false_positives=false_positives,
    )


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

    **ASCII is not sufficient on its own, and a control character is the case it
    misses.** ``"\\n"`` is ASCII, ``strip`` does not remove it from the interior
    of a string, and ``render`` interpolates these values into a header of
    aligned ``label  value`` lines -- so a value carrying a newline adds lines to
    that header which a reader cannot distinguish from ones the framework
    computed. Provenance a caller can forge is worse than provenance a caller
    merely has to supply, and every checked field is caller- or ledger-supplied.
    Found by two independent reviews of gate A33, which added one more such
    field; the gap was pre-existing for ``platform`` and ``grammar``, so closing
    it here hardens those, the three address versions, and the per-cell
    ``system`` and ``scenario`` at the same time.
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
    control = sorted({character for character in value if not character.isprintable()})
    if control:
        raise MalformedDesignError(
            f"{what} {value!r} carries a non-printing character ({control!r}); "
            f"render interpolates it into the report, so a value holding a "
            f"newline or a tab forges lines that read as framework output"
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
    numpy_version: str,
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

    ``platform``, ``numpy_version`` and ``grammar`` are **required and
    validated**, not defaulted. None is recoverable from the ledger and all three
    must appear wherever these numbers are reported; refusing an unlabelled
    report is how that holds without depending on anybody remembering.

    ``numpy_version`` is **caller-supplied rather than read from**
    ``numpy.__version__`` **here**, which is the one thing about it worth
    stating. It describes the process that filled the ledger, and this function
    may well be running on another machine -- ``scripts/report_matrix.py`` opens
    a ledger file, not a campaign. Reading the local interpreter would print a
    confident fact about the wrong process, which is worse than refusing.

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
    if not numpy_version.strip():
        raise MalformedDesignError(
            "a report needs the numpy version its cells were computed under. "
            "Bit-exact determinism rides numpy's generator bit-stream stability, "
            "pyproject declares a lower bound only, and the registry "
            "content-addresses with no dependency term -- so a lockfile upgrade "
            "moves the numbers while every address stays fixed, and only the "
            "report can say which resolution produced these"
        )
    if not str(grammar).strip():
        raise MalformedDesignError(
            "a report needs the grammar version its cells were scored under. D1 "
            "is grammar.distance and D6 is grammar.code_length, so both are "
            "grammar-relative and neither can be interpreted unnamed"
        )
    # Checked on the *stored* value, because that is what `render` interpolates:
    # `platform` and `numpy_version` are stripped on their way into the report, so
    # guarding the raw argument refuses a trailing newline that would never have
    # reached the output. `grammar` is stored unstripped and is checked as given.
    _refuse_non_ascii("platform", platform.strip())
    _refuse_non_ascii("numpy", numpy_version.strip())
    _refuse_non_ascii("grammar", str(grammar))
    # The address renders into the same header and was reaching it unchecked, so
    # the guarantee held for three of the six strings `render` interpolates and
    # not the other three -- which reads as a decision rather than an oversight.
    # `CampaignAddress` carries bare `NewType` strings with no validation of
    # their own, and `scripts/report_matrix.py` takes all three from the command
    # line beside `--platform`.
    _refuse_non_ascii("env version", str(address.env_version))
    _refuse_non_ascii("data version", str(address.data_version))
    _refuse_non_ascii("metric version", str(address.metric_version))

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
        numpy_version=numpy_version.strip(),
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
        probe_inadequate_rate=_rate(rows, _PROBE_INADEQUATE),
        experiments=_summarise(_values(rows, "experiments")),
        autonomy_fraction=_summarise(_values(rows, "autonomy_fraction")),
        claims=_summarise(_values(rows, "claims")),
        adjudicated=_summarise(_values(rows, "adjudicated")),
        adjudication_rate=_summarise(_values(rows, "adjudication_rate")),
        contradictions=_summarise(_values(rows, "contradictions")),
        zombie_claims=_summarise(_values(rows, "zombie_claims")),
        null_mass=_summarise(_values(rows, "null_mass")),
        abstain_mass=_summarise(_values(rows, "abstain_mass")),
        max_defect_mass=_summarise(_values(rows, "max_defect_mass")),
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
        paired_difference=_paired_difference(
            arms[treatment], arms[comparator], dimension
        ),
        overlaps=left.low <= right.high and right.low <= left.high,
        residual_asymmetries=(
            () if preregistration is None else preregistration.residual_asymmetries
        ),
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


def _readings_by_seed(
    rows: Sequence[LedgerEntry], dimension: str
) -> dict[int, float] | None:
    """Return one arm's readings keyed by seed, or ``None`` if a seed repeats.

    Guarantees the mapping is a *bijection* on seeds when it returns one, which is
    what makes a within-seed difference well defined. A repeat is not resolved by
    taking either row: :func:`_refuse_reseeded` records why one address cannot
    choose between two rows of the same cell, and the same reasoning holds a
    dimension later.
    """
    values = _values(rows, dimension)
    readings: dict[int, float] = {}
    for row, value in zip(rows, values, strict=True):
        seed = int(row.key.seed)
        if seed in readings:
            return None
        readings[seed] = value
    return readings


def _paired_difference(
    treatment: Sequence[LedgerEntry],
    comparator: Sequence[LedgerEntry],
    dimension: str,
) -> DimensionSummary | None:
    """Return the mean within-seed difference, or ``None`` if the arms do not pair.

    Guarantees the result does not depend on the order rows arrived in.
    :attr:`MatrixReport.rows` retains the order it was given, so the pairing is by
    **seed** and never by position -- the two coincide on rows that happen to
    arrive seed-ascending, and the mean coincides under any permutation, so only
    the interval would show the difference. See :attr:`Contrast.paired_difference`
    for what ``None`` means and why it is not an exception.
    """
    left = _readings_by_seed(treatment, dimension)
    right = _readings_by_seed(comparator, dimension)
    if left is None or right is None or left.keys() != right.keys():
        return None
    # `sorted`, not the dicts' insertion order, which is the order the rows
    # arrived in. `_summarise` folds through `core.reductions` and is
    # order-independent anyway, so this is belt and braces -- and invariant 3
    # forbids the dependence rather than the dependence that happens to matter.
    return _summarise([left[seed] - right[seed] for seed in sorted(left)])


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
        f"  {'numpy':<16s}{report.numpy_version}",
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
            # On its own line and labelled for the check it is, rather than as a
            # fourth number under "inadequate": the two are different checks and
            # this is the one SPEC 12 criterion 4 is read off. ASCII, as is every
            # other byte `render` emits -- see `_CAVEAT`.
            f"  {'rate stage A probe fired':<{_LABEL_WIDTH}s}"
            f"{cell.probe_inadequate_rate:>10.3f}",
            # Inside the cell block and not in a section after all of them, which
            # is the whole of what SPEC 12 criterion 11 and F10 ask for: the
            # fraction is reported *for every investigation*, alongside that
            # investigation's figures, so a reader of one cell has it without
            # cross-referencing a footer. Its interval carries the non-finite
            # count, which is how a replicate that decided nothing shows as
            # excluded rather than as a zero folded in.
            f"  {'autonomy fraction':<{_LABEL_WIDTH}s}"
            f"{_interval(cell.autonomy_fraction)}",
            # Criterion 9's comparison, as three means on one line in the shape
            # the rates line above already uses. The third is not the leader:
            # see `CellSummary.max_defect_mass`.
            f"  {'masses null/abstain/max defect':<{_LABEL_WIDTH}s}"
            f"{cell.null_mass.point:>10.3f}  {cell.abstain_mass.point:>10.3f}  "
            f"{cell.max_defect_mass.point:>10.3f}",
            # Criterion 10, inside the cell block for the reason the autonomy
            # fraction is: a criterion read off the report is not answerable
            # from a field a reader has to open the ledger to find. Its interval
            # carries the non-finite count, so a replicate that afforded no
            # claim shows as excluded rather than as a zero folded in.
            f"  {'adjudicated fraction':<{_LABEL_WIDTH}s}"
            f"{_interval(cell.adjudication_rate)}",
            # Criterion 10's own statistic, which the line above is not: that
            # one weights replicates equally and the criterion weights claims.
            # Printed with both counts in the clear so a reader can check the
            # quotient rather than take it, the way criterion 9's three masses
            # are printed rather than their verdict.
            f"  {'claims afforded/adjudicated/share':<{_LABEL_WIDTH}s}"
            f"{cell.claims.point:>10.3f}  {cell.adjudicated.point:>10.3f}  "
            f"{cell.adjudicated_share:>10.4f}",
            # Criterion 8's two quantities, on one line and labelled apart. A
            # campaign meeting the criterion prints 0.000 twice; a pooled single
            # figure could not say which of the two a nonzero was.
            f"  {'per run contradictions/zombies':<{_LABEL_WIDTH}s}"
            f"{cell.contradictions.point:>10.3f}  "
            f"{cell.zombie_claims.point:>10.3f}",
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
                cell.autonomy_fraction,
                cell.claims,
                cell.adjudicated,
                cell.adjudication_rate,
                cell.contradictions,
                cell.zombie_claims,
                cell.null_mass,
                cell.abstain_mass,
                cell.max_defect_mass,
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
