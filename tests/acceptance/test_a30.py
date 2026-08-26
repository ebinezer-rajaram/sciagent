"""Acceptance test A30: every campaign run is adjudicated.

A30 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The verifier has no production caller"*, and reads:

    ``test_a30_every_campaign_run_is_adjudicated`` -- a campaign over
    constructed runs yields an adjudication rate and a per-run contradiction
    count in its recorded output; an injected zombie claim is counted, not
    silently absent.

The same entry's **Idea** is what the gate is a check on: *"Wire claim
authorship and adjudication into campaign runs: ``claims_from_run`` (or
agent-authored claims when they exist) adjudicated by ``verify()`` per run,
verdicts aggregated per campaign, and ``verify/contradiction.py`` findings
accumulated so §12 criterion 8 is a measured zero rather than a vacuous one."*

What was wrong
--------------

:func:`sciagent.verify.verify` had **no caller anywhere in** ``src``. Seven
check modules, an orchestrator and a five-outcome verdict type, and the only
thing that ever ran them was ``tests/baseline_runs.py``. Two §12 criteria are
damaged by that, and differently.

**Criterion 10** -- *"at least 90% of claims adjudicated by the verifier without
human input"* -- was measured over a synthetic cross-product built in a test
(``docs/DECISIONS.md``). No recorded run produced the figure, so the criterion
described the test population rather than the campaign.

**Criterion 8** -- *"zero graph contradictions and zero zombie hypotheses across
all runs"* -- was **vacuous**, and structurally so rather than by accident.
:func:`~sciagent.eval.campaign.claims_from_run` filters to ``mass > 0.0``, and
:func:`~sciagent.systems.base.diagnose` gives a rejected hypothesis *exactly*
zero, so the generator cannot emit a claim about a rejected node and the zombie
rule cannot fire on the population it is measured over. Measured on this gate's
own fixture before it was written: S9 and S11 hold five nodes, none rejected, no
relations at all, and all eighty claims per run come back with zero contradiction
findings. A zero that no input could have moved is not a measurement.

That is why the gate line demands an **injected** claim, and why the injection
has to reach the recorded payload rather than a helper called in isolation: what
has to be shown is that the count is live, and the only way to show a count is
live is to move it.

Where the numbers come from
---------------------------

:func:`~sciagent.eval.campaign.adjudicate`, called by
:func:`~sciagent.eval.matrix.reading_of` from the
:class:`~sciagent.eval.campaign.ScenarioRun` it already receives -- which
``run_scenario`` has reconciled against the engine's own state before returning
it. A caller may say *which claims* are adjudicated and can say nothing about
how any one of them is decided -- every outcome comes from ``verify`` and every
count from ``adjudicate``.

An earlier draft of this paragraph put that as *"the claims are structure"*, and
review showed it is false: :attr:`~sciagent.core.types.Claim.effect` is a
**number**, and it selects which grading path runs. A claim carrying one is
graded by ``statistical._from_effect``, which almost never refers; a claim
without one by ``_from_predictions``, which refers whenever no cited experiment
bears on the subject. So a fabricated effect converts a referral into a refusal,
and a refusal *is* adjudicated. Fabricating a number raises the rate. That is a
property of criterion 10 rather than a hole in the seam -- refusing fabricated
effects here would take gate A19's work away from
:mod:`sciagent.verify.numerical`, which exists to catch them -- and it is
recorded in ``docs/DECISIONS.md`` rather than smoothed over.

The one thing here that is a choice rather than a consequence is that the pass
**accumulates within a run**: a claim whose verdict accepts it enters
:attr:`~sciagent.verify.ClaimContext.accepted` for the claims after it. Without
that, :mod:`sciagent.verify.contradiction`'s cross-contradiction and reversal
rules read an empty tuple and are as unreachable as the zombie rule was. It is
**per run** and deliberately not per campaign: accumulating across cells would
make a cell's reading a function of which cells ran before it, so a resumed
campaign would score differently at the same content address, which is the third
invariant.

What the tests establish
------------------------

``test_a30_every_campaign_run_is_adjudicated`` is the gate's two clauses, both
read off rows a real :func:`~sciagent.eval.matrix.run_matrix` pass recorded in a
ledger -- *"its recorded output"* -- rather than off a
:class:`~sciagent.eval.matrix.CellReading` in hand.

``test_a30_the_rate_is_bound_to_the_verdicts_it_summarises`` is A29's lesson
taken rather than restated. Every payload entry is compared against a figure
recomputed here from :func:`~sciagent.verify.verify` directly, never against a
sibling key: a test that pins two derived fields against each other pins neither
to its origin, and a transposition of exactly that kind passed the whole suite
once already.

``test_a30_an_accepted_claim_is_visible_to_the_claims_after_it`` pins the
accumulation. It is the only test in the repository that could notice its
removal, because on every real run the graph holds no ``CONTRADICTS`` edge and
both rules that read ``accepted`` are quiet whether it is populated or not. Its
claim pair is drawn from the run's **own accepted claims** rather than authored,
and a review of this module before it was implemented is why: a hand-written
``supports`` claim citing the run's whole evidence is refused on statistical
grounds before contradiction is reached, so a test built on one would have been
satisfiable only by an ``adjudicate`` that treated refused claims as admitted.

``test_a30_the_rate_falls_when_a_claim_cannot_be_decided`` is what stops the
rate being a constant. The same review measured that ``REFER`` occurs **zero**
times over this fixture and zero times over ``tests/baseline_runs.py``'s 2288
claims, so every row's true rate is 1.0 and ``1.0 if claims else nan``
reproduces the whole payload. Criterion 10 is then measured but not falsifiable
-- the same shape of vacuity the entry's Rationale complains about for criterion
8, one criterion over. Injecting a claim the verifier declines to grade is what
closes it.

``test_a30_a_zombie_is_a_contradiction_too`` stops the two criterion-8 fields
drifting apart. Every zombie claim is refused *by the contradiction check*, so
the zombie count is a subset of the contradiction count by construction, and a
payload where it is not has read one of them from the wrong place.

``test_a30_a_run_that_afforded_no_claim_reports_no_rate`` is the boundary the
optional rate exists for, and the reason it crosses the ledger as ``nan``.

``test_a30_a_row_from_the_previous_reading_is_not_pooled_beside_these`` is the
reading bump. The payload gained keys, so a ``spec8/4`` row cannot answer a
criterion-8 or criterion-10 question and a reader pooling the two generations
would average whichever rows happened to carry them.

``test_a30_the_report_shows_the_adjudication_beside_every_cell`` is the entry's
*"verdicts aggregated per campaign"*. Both criteria are read off the report, and
a field a reader must open the ledger to see has made neither decidable.

The last three came out of ``/preflight``'s review of the implementation, and
each closes a gap the tests above could not have caught.

``test_a30_the_report_carries_the_share_criterion_10_actually_names`` is the
difference between a mean of per-replicate rates and a share of claims. The
report pooled the first and criterion 10 names the second; they agree only where
a cell's replicates afford equal-sized populations, which is true of every
conventional arm and false of every arm holding a proposal layer. Two replicates
at 80/100 and 4/4 average to 0.9000 and clear the bar the 84/104 they represent
fails.

``test_a30_an_inconsistent_adjudication_cannot_be_built`` and
``test_a30_a_claim_about_another_run_is_refused`` are CLAUDE.md's second
invariant applied to this gate's own additions -- *"enforce with runtime
assertions, not comments"*. The first makes three relations the count docstrings
state as facts actually false-able, the way
:class:`~sciagent.verify.Verdict` already does one level down. The second bounds
the ``claims`` seam: a claim about a hypothesis the run never entertained is not
a claim the run can be graded on. Neither bounds *merit* -- a well-formed but
worthless claim still counts, and that limit is criterion 10's rather than this
module's.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

import pytest
from slice_tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    gate_table,
    save_gate_table,
)

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import MalformedClaimError, MalformedDesignError
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    Claim,
    ClaimId,
    ComponentId,
    DataVersion,
    EnvVersion,
    ExperimentId,
    FrozenDict,
    GrammarVersion,
    HypothesisId,
    MetricVersion,
    Probability,
    RejectionCode,
    ScenarioId,
    Seed,
)
from sciagent.eval.campaign import (
    Adjudication,
    ScenarioRun,
    adjudicate,
    claims_from_run,
    run_scenario,
)
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    cell_key,
    reading_of,
    run_matrix,
)
from sciagent.eval.report import render, summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import DIMENSION_VERSION
from sciagent.hypothesis.graph import Relation
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.inference.interface import Observation
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import null_seeded_graph
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly
from sciagent.verify import ClaimContext, Verdict, verify
from sciagent.verify.contradiction import SUPPORTING_STRENGTHS
from sciagent.verify.relevance import EvidenceIndex
from sciagent.verify.verdict import CheckClass

#: The two arms every scenario here is run with, both SPEC §5's. B1 entertains
#: nothing beyond the null and V1 opens on its whole library, so the two afford
#: claim populations of different sizes -- 16 claims against 80.
#:
#: That difference is **not** what stops a constant from passing, and an earlier
#: version of this comment claimed it was. It does not: measured over this
#: fixture, every one of the four cells adjudicates every claim it affords, so
#: ``16/16`` and ``80/80`` are both exactly 1.0 and
#: ``1.0 if claims else nan`` reproduces every row. The whole recorded claim
#: population reaches ``REFER`` zero times, here and across
#: ``tests/baseline_runs.py``'s 2288 claims. What actually stops the constant is
#: ``test_a30_the_rate_falls_when_a_claim_cannot_be_decided``, which injects a
#: claim the verifier declines to grade and requires the rate to move.
ARMS = ("B1", "V1")

#: S9 is the abstention scenario and S11 the one whose space is inadequate. Two
#: rather than one so that the campaign clause has a matrix rather than a cell,
#: and because a count read off a single run cannot be shown to vary at all.
SCENARIOS = ("S9", "S11")

ADDRESS = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

#: The arm name the constructed report rows below wear. **Not** a real one, and
#: that is the point: ``_synthetic_row`` keeps a genuine V1/S9 payload and
#: overwrites three counts, so a block labelled ``V1 / S9`` would render
#: fabricated arithmetic beside genuine V1 numbers and be indistinguishable from
#: a measurement once separated from the docstring explaining it. Review raised
#: exactly that. No arm in ``MATRIX_SYSTEMS`` or ``CRITERION5_SYSTEMS`` is
#: spelled this way, so the label cannot collide with a recorded cell either.
SYNTHETIC_ARM = "synthetic"

PLATFORM = "Windows-11-x86_64"
NUMPY = "2.5.1"
REPORT_GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")


@dataclass(frozen=True, slots=True)
class Recorded:
    """One arm's run on one scenario, with everything needed to re-score it.

    ``program`` is held because :class:`~sciagent.verify.ClaimContext` needs one
    and a :class:`~sciagent.eval.campaign.ScenarioRun` does not carry it: it is
    the *reference* programme off the executor, which
    :attr:`~sciagent.experiments.executor.Executor.reference` documents as
    carrying no scenario's ground truth. Hoisting that executor into a local is
    the whole of what ``MatrixRunner.execute`` had to change.

    ``observations`` is held for the same shape of reason:
    :func:`~sciagent.eval.matrix.reading_of` refuses a run scored against a
    different number of observations than it charged for, and the engine that
    produced them is gone by the time a test re-scores the run.
    """

    run: ScenarioRun
    reading: CellReading
    program: GenerativeProgram
    observations: tuple[Observation, ...]


@lru_cache(maxsize=1)
def _runs() -> dict[tuple[str, str], Recorded]:
    """Return every run this gate reads, keyed by ``(scenario, arm)``.

    Built once and cached with the empirical table threaded from run to run and
    saved at the end, exactly as ``tests/acceptance/test_a31.py`` does it: a
    structure entertained once is simulated once per machine rather than once
    per test.
    """
    table: EmpiricalTable = gate_table()
    simulate = simulator(GRAMMAR)
    built: dict[tuple[str, str], Recorded] = {}
    for name in SCENARIOS:
        target = scenario(name)
        for arm in ARMS:
            graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
            engine = EmpiricalTableEngine(graph, table, simulate=simulate)
            runner = executor(
                GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget
            )
            run = run_scenario(
                target, _system(arm), executor=runner, engine=engine, graph=graph
            )
            observations = tuple(engine.observations)
            reading, table = reading_of(
                run,
                grammar=GRAMMAR,
                table=engine.table,
                simulate=simulate,
                observations=observations,
                program=runner.reference,
            )
            built[(name, arm)] = Recorded(
                run=run,
                reading=reading,
                program=runner.reference,
                observations=observations,
            )
    save_gate_table(table)
    return built


def _system(arm: str) -> PPCOnly | BOEDOnly:
    """Return a fresh system for one arm."""
    return PPCOnly() if arm == "B1" else BOEDOnly(closed_set())


def _recorded(name: str, arm: str) -> Recorded:
    """Return one built run with everything this module judges it against."""
    return _runs()[(name, arm)]


def _payload(name: str, arm: str) -> dict[str, float]:
    """Return the ledger payload one recorded run was scored to."""
    return dict(_recorded(name, arm).reading.as_payload())


def _verdicts(
    run: ScenarioRun, program: GenerativeProgram, claims: Sequence[Claim]
) -> tuple[Verdict, ...]:
    """Return the verdicts on ``claims``, recomputed here from ``verify`` itself.

    An independent derivation of what the payload must hold, and deliberately
    independent: it calls :func:`~sciagent.verify.verify` rather than
    :func:`~sciagent.eval.campaign.adjudicate`, so a payload field is checked
    against the verifier and not against the function that summarises it.

    Accumulates, because the thing under test does. A version of this helper
    that did not would agree with the implementation on every real run -- both
    rules that read ``accepted`` are quiet where the graph holds no
    ``CONTRADICTS`` edge -- and disagree on exactly the constructed case
    ``test_a30_an_accepted_claim_is_visible_to_the_claims_after_it`` builds.
    """
    context = ClaimContext(
        graph=run.graph,
        evidence=run.evidence,
        program=program,
        posterior=FrozenDict[HypothesisId, Probability](run.diagnosis.distribution),
    )
    accepted: list[Claim] = []
    verdicts: list[Verdict] = []
    for claim in claims:
        verdict = verify(claim, replace(context, accepted=tuple(accepted)))
        verdicts.append(verdict)
        if verdict.accepted:
            accepted.append(claim)
    return tuple(verdicts)


def _contradictions(verdicts: Sequence[Verdict]) -> int:
    """Return how many contradiction findings ``verdicts`` carry between them.

    Counted over :attr:`~sciagent.verify.Finding.check`, which is typed, and
    never over :attr:`~sciagent.verify.Finding.message`, which
    :mod:`sciagent.verify.verdict` says in as many words is for a human reading
    the record and that nothing branches on.
    """
    return sum(
        1
        for verdict in verdicts
        for finding in verdict.findings
        if finding.check is CheckClass.CONTRADICTION
    )


def _supporting_claim(run: ScenarioRun, subject: HypothesisId, *, tag: str) -> Claim:
    """Return a claim asserting *for* ``subject``, citing the whole run.

    Every field but the subject is what :func:`claims_from_run` writes for a
    mechanistic supporting claim, so that what makes it a zombie is the graph's
    verdict on its subject and nothing about the claim's own construction. No
    estimand and no effect, so :mod:`sciagent.verify.contradiction`'s reversal
    rule cannot fire on it and the finding it draws is the one under test.
    """
    records = run.evidence.ordered()
    return Claim(
        id=ClaimId(f"{tag}/{run.system}/{run.scenario.id}/{subject}"),
        subject=subject,
        subject_kind="hypothesis",
        modality="mechanistic",
        estimand=None,
        strength="supports",
        scope=records[0].scope,
        evidence=tuple(record.experiment for record in records),
        partition="exploratory",
        effect=None,
        uniqueness="non_exclusive",
        prose=f"{tag}: supports {subject}",
        intervention=None,
    )


def _accepted_supporting(name: str, arm: str) -> tuple[Claim, ...]:
    """Return the run's own supporting claims that the verifier *accepts*.

    Drawn from :func:`claims_from_run`'s population rather than authored here,
    and that is the whole point of the helper. A claim built to look plausible
    is not necessarily one the verifier admits: measured on this fixture, a
    hand-written ``supports`` claim citing the run's whole evidence is refused
    on **statistical** grounds long before any contradiction rule is reached --
    ``prediction_evidence`` reports the subject refuted by 7 of the 8 bearing
    experiments, and :mod:`sciagent.verify.statistical` says an experiment that
    refuted a hypothesis is not evidence supporting it.

    That matters because :attr:`~sciagent.verify.ClaimContext.accepted` is
    documented as *"the claims already **admitted** in this investigation"*. A
    test built on refused claims could only be satisfied by an ``adjudicate``
    that carried refused claims forward as admitted ones, which would make every
    cross-contradiction a contradiction between claims the verifier threw out.

    Selected by asking :func:`~sciagent.verify.verify` itself, so the selection
    is independent of the function under test.
    """
    recorded = _recorded(name, arm)
    claims = claims_from_run(recorded.run)
    verdicts = _verdicts(recorded.run, recorded.program, claims)
    return tuple(
        claim
        for claim, verdict in zip(claims, verdicts, strict=True)
        if verdict.accepted
        and claim.subject_kind == "hypothesis"
        and claim.strength in SUPPORTING_STRENGTHS
    )


def _with_a_rejection(name: str, arm: str) -> tuple[ScenarioRun, HypothesisId]:
    """Return a run whose graph has rejected one hypothesis, and which one.

    The victim is a hypothesis the verifier *accepts* supporting claims about,
    so that after the rejection the only thing standing against such a claim is
    the zombie rule. Picking an arbitrary node would work for the count and
    would muddy the reading: a claim refused on four grounds at once still
    carries a contradiction finding, but it no longer demonstrates which rule
    put it there.

    It is rejected **after** the run rather than during one, and that is a
    fixture rather than a claim about any system: nothing in the framework
    rejects, which is precisely why criterion 8 has never had an input that
    could move it.
    """
    run = _recorded(name, arm).run
    supporting = _accepted_supporting(name, arm)
    assert supporting, (
        f"{arm} on {name} affords no supporting claim the verifier accepts, so "
        f"a rejection here could not isolate the zombie rule"
    )
    victim = HypothesisId(str(supporting[0].subject))
    rejected = run.graph.reject(victim, RejectionCode.UNSATISFIABLE_REFUTATION)
    return replace(run, graph=rejected), victim


def _undecidable_claim(run: ScenarioRun, program: GenerativeProgram) -> Claim:
    """Return a claim the verifier declines to grade, for the rate to notice.

    A claim about a *component* asserting exclusivity.
    :mod:`sciagent.verify.logical` refers it because exclusivity is a statement
    about rival hypotheses and a component is not one, and
    :mod:`sciagent.verify.statistical` refers it because a component makes no
    predictions for the cited experiments to bear on. Two referrals and no
    refusal, so :func:`~sciagent.verify.worst` leaves the verdict at ``REFER``
    and :attr:`~sciagent.verify.Verdict.adjudicated` is ``False``.

    Constructed rather than found because **nothing in the recorded population
    refers**: 2288 claims across ``tests/baseline_runs.py`` come back adjudicated,
    so an adjudication rate read off real runs alone is 1.0 by construction and
    could not tell a live measurement from the constant.
    """
    records = run.evidence.ordered()
    return Claim(
        id=ClaimId(f"undecidable/{run.system}/{run.scenario.id}"),
        subject=ComponentId(sorted(program.components)[0]),
        subject_kind="component",
        modality="mechanistic",
        estimand=None,
        strength="suggests",
        scope=records[0].scope,
        evidence=tuple(record.experiment for record in records),
        partition="exploratory",
        effect=None,
        uniqueness="exclusive",
        prose="undecidable: a component is not a rival hypothesis",
        intervention=None,
    )


def _rescored(
    name: str, arm: str, run: ScenarioRun, claims: Sequence[Claim] | None
) -> CellReading:
    """Return a reading for ``run``, scored on ``claims``, at this gate's table."""
    recorded = _recorded(name, arm)
    reading, _grown = reading_of(
        run,
        grammar=GRAMMAR,
        table=gate_table(),
        simulate=simulator(GRAMMAR),
        observations=recorded.observations,
        program=recorded.program,
        claims=claims,
    )
    return reading


def _campaign(
    cells: Sequence[Cell], execute: Callable[[CellTask], CellReading]
) -> tuple[LedgerEntry, ...]:
    """Drive one real ``run_matrix`` pass and return the rows it recorded.

    The rows rather than the readings, which is the gate's *"recorded output"*:
    a payload asserted on a :class:`~sciagent.eval.matrix.CellReading` in hand
    has not been shown to survive :meth:`CellReading.as_payload` or the ledger's
    own round trip through sqlite.
    """
    with CampaignLedger.in_memory() as ledger:
        outcome = run_matrix(
            cells,
            address=ADDRESS,
            scenario_seed=lambda target: scenario(str(target)).seed,
            battery=lambda target: scenario(str(target)).held_out,
            execute=execute,
            ledger=ledger,
        )
        return outcome.entries


def _every_cell() -> tuple[Cell, ...]:
    """Return one replicate of every (arm, scenario) this module built."""
    return tuple(Cell(arm, ScenarioId(name), 1) for name in SCENARIOS for arm in ARMS)


def _built_reading(task: CellTask) -> CellReading:
    """Return the reading this module built for the task's cell."""
    return _recorded(str(task.cell.scenario), task.cell.system).reading


def _scenario_class(target: ScenarioId) -> ScenarioClass:
    """Classify off the environment's declaration, as the real caller does."""
    return scenario(str(target)).scenario_class


def _report_of(rows: Sequence[LedgerEntry]) -> str:
    """Return the rendered report for ``rows``, at this module's address."""
    return render(
        summarise(
            tuple(rows),
            address=ADDRESS,
            scenario_class=_scenario_class,
            battery=lambda target: scenario(str(target)).held_out,
            platform=PLATFORM,
            numpy_version=NUMPY,
            grammar=REPORT_GRAMMAR,
        )
    )


def _synthetic_row(replicate: int, *, claims: float, adjudicated: float) -> LedgerEntry:
    """Return a recorded row carrying stated claim counts and a real reading.

    The payload is a genuine one -- ``V1``'s S9 reading, scored by
    :func:`~sciagent.eval.matrix.reading_of` -- with only the two counts and the
    rate they imply overridden, so every other field a summary reads is what the
    framework produced. Two replicates of one cell, differing in nothing else.

    Labelled :data:`SYNTHETIC_ARM` rather than ``V1``, because the payload it
    keeps *is* V1's and only three counts are fabricated: a rendered block
    reading ``V1 / S9`` would put invented arithmetic beside genuine V1 figures
    and read as a measurement to anyone who met it without this docstring.

    Constructed rather than run because **no conventional arm can produce the
    case**: measured over six replicates each of B1 and V1 on S9 and S11, every
    replicate affords exactly the same number of claims, so the per-replicate
    mean and the claim-weighted share agree and neither statistic can be told
    from the other. An arm with a proposal layer entertains a different number
    of structures per replicate and does produce it; running one here would cost
    a model call to establish arithmetic.
    """
    payload = dict(_recorded("S9", "V1").reading.as_payload())
    payload["claims"] = claims
    payload["adjudicated"] = adjudicated
    payload["adjudication_rate"] = adjudicated / claims
    task = CellTask(
        cell=Cell(SYNTHETIC_ARM, ScenarioId("S9"), 2),
        replicate=replicate,
        seed=Seed(replicate),
    )
    return LedgerEntry(
        key=cell_key(task, ADDRESS, battery=scenario("S9").held_out),
        reading=FrozenDict[str, float](payload),
        sequence=replicate,
    )


def _block(text: str, name: str, arm: str) -> list[str]:
    """Return the lines of one cell's dimension block, heading to blank line.

    A block runs from its heading, which
    :func:`~sciagent.eval.report._cell_block` writes as ``system / scenario``, to
    the blank line that same function ends it with.
    """
    lines = text.splitlines()
    heading = f"{arm} / {name}  ("
    start = next(index for index, line in enumerate(lines) if line.startswith(heading))
    end = next(
        index for index in range(start + 1, len(lines)) if not lines[index].strip()
    )
    return lines[start:end]


class TestA30EveryCampaignRunIsAdjudicated:
    """A verifier with no production caller measures its tests, not the campaign."""

    def test_a30_every_campaign_run_is_adjudicated(self) -> None:
        """The gate's two clauses, both read off rows a campaign recorded."""
        cells = _every_cell()
        rows = _campaign(cells, _built_reading)
        assert len(rows) == len(cells)

        for row in rows:
            reading = row.reading
            # The first clause: both figures the gate names, in the recorded
            # payload, for every replicate of the campaign.
            assert "adjudication_rate" in reading
            assert "contradictions" in reading
            assert reading["claims"] > 0.0, (
                "a run affording no claim at all cannot show that the campaign "
                "was adjudicated, and this fixture is meant to afford some"
            )
            assert math.isfinite(reading["adjudication_rate"])
            assert 0.0 <= reading["adjudication_rate"] <= 1.0
            assert reading["adjudicated"] <= reading["claims"]
            # The vacuity the entry complains about, pinned so the injection
            # below is measured against a real zero rather than an assumed one.
            # No run rejects anything, and `claims_from_run` filters to mass
            # above zero while a rejected hypothesis is given exactly zero, so
            # nothing in this population *can* be a zombie.
            assert reading["zombie_claims"] == 0.0

        # The second clause. One run, scored twice: once on the claims it
        # affords and once on those claims plus an injected zombie, recorded at
        # two replicates of one honestly-labelled cell. The rejection is in
        # *both* graphs, so the only difference between the two rows is the
        # claim -- and the assertion is a **difference** rather than a literal.
        # "Counted, not silently absent" is a statement about what the count
        # does when an input arrives, and a field returning 1.0 unconditionally
        # would satisfy any assertion against a constant.
        run, victim = _with_a_rejection("S9", "V1")
        afforded = claims_from_run(run)
        injected = (*afforded, _supporting_claim(run, victim, tag="injected"))

        before = _rescored("S9", "V1", run, afforded)
        after = _rescored("S9", "V1", run, injected)
        recorded = _campaign(
            (Cell("V1", ScenarioId("S9"), 2),),
            lambda task: before if task.replicate == 0 else after,
        )
        quiet, loud = recorded[0].reading, recorded[1].reading

        assert loud["claims"] == quiet["claims"] + 1.0
        assert loud["zombie_claims"] == quiet["zombie_claims"] + 1.0, (
            f"the injected zombie claim about {victim!r} did not reach the "
            f"recorded zombie count: {quiet['zombie_claims']} -> "
            f"{loud['zombie_claims']}"
        )
        assert loud["contradictions"] >= quiet["contradictions"] + 1.0, (
            f"the injected zombie claim about {victim!r} drew no contradiction "
            f"finding: {quiet['contradictions']} -> {loud['contradictions']}"
        )

    def test_a30_the_rate_is_bound_to_the_verdicts_it_summarises(self) -> None:
        """Each payload entry equals the figure ``verify`` itself produces.

        Compared against a recomputation rather than against a sibling key. Gate
        A29 records why: a transposition of two payload labels passed all four of
        its tests and the whole suite, because those tests compared the two keys
        to each other and never to the source each is named for.
        """
        for name in SCENARIOS:
            for arm in ARMS:
                recorded = _recorded(name, arm)
                claims = claims_from_run(recorded.run)
                verdicts = _verdicts(recorded.run, recorded.program, claims)
                payload = _payload(name, arm)

                assert payload["claims"] == float(len(claims))
                assert payload["adjudicated"] == float(
                    sum(1 for verdict in verdicts if verdict.adjudicated)
                )
                assert payload["contradictions"] == float(_contradictions(verdicts))
                # Criterion 10's own figure, and not merely the two counts it is
                # a ratio of: a payload carrying the pair and dividing them
                # wrongly would pass every assertion above.
                assert payload["adjudication_rate"] == payload["adjudicated"] / float(
                    len(claims)
                )

    def test_a30_an_accepted_claim_is_visible_to_the_claims_after_it(self) -> None:
        """Accumulation, which is the only thing making two of three rules live.

        Two supporting claims about hypotheses the graph holds to contradict.
        Adjudicated with accumulation the second is refused, because the first
        was accepted and is in ``accepted`` by the time the second is judged;
        adjudicated against an empty ``accepted`` -- which is what every caller
        did before this gate -- neither is, and
        :mod:`sciagent.verify.contradiction`'s cross-contradiction rule is
        unreachable code.

        Nothing else in the suite would notice its removal: on every real run
        the graph holds no ``CONTRADICTS`` edge at all, so the rule is quiet
        whether ``accepted`` is populated or not.
        """
        recorded = _recorded("S9", "V1")
        run = recorded.run
        supporting = _accepted_supporting("S9", "V1")
        first = supporting[0]
        second = next(claim for claim in supporting if claim.subject != first.subject)
        left = HypothesisId(str(first.subject))
        right = HypothesisId(str(second.subject))
        related = replace(
            run, graph=run.graph.relate(left, right, Relation.CONTRADICTS)
        )
        pair = (first, second)

        # The precondition, asserted rather than assumed: if the verifier stopped
        # accepting the first claim, the second would draw no cross-contradiction
        # for a reason that has nothing to do with accumulation, and this test
        # would report the implementation broken when it is the fixture that
        # moved.
        alone = _verdicts(related, recorded.program, (first,))
        assert alone[0].accepted, (
            f"claim {first.id!r} is no longer accepted, so it cannot enter "
            f"`accepted` and this test cannot exercise the rule it exists for"
        )

        adjudged: Adjudication = adjudicate(
            related, program=recorded.program, claims=pair
        )
        assert adjudged.claims == 2
        assert adjudged.contradictions == 1, (
            "a supporting claim about a hypothesis contradicting one already "
            "accepted drew no finding, so accepted claims are not carried "
            "forward and the cross-contradiction rule is dead code"
        )
        assert adjudged.zombies == 0, (
            "neither hypothesis is rejected, so a zombie counted here means the "
            "two criterion 8 figures are reading one set of findings"
        )

    def test_a30_the_rate_falls_when_a_claim_cannot_be_decided(self) -> None:
        """A rate that never moves is a constant wearing a measurement's name.

        Criterion 10 is *"at least 90% of claims adjudicated without human
        input"*, and every claim this repository can currently record is
        adjudicated: ``REFER`` occurs zero times over the four cells here and
        zero times over ``tests/baseline_runs.py``'s whole population. So the
        true rate is 1.0 on every recorded row, and ``1.0 if claims else nan``
        -- which computes nothing and reads no verdict -- reproduces the payload
        of every test above this one exactly.

        This is the assertion that separates them. A claim the verifier declines
        to grade is injected through the same seam the zombie uses, and the rate
        has to notice: strictly below 1.0, and the numerator strictly below the
        denominator. Both, because a payload writing ``adjudicated = claims - 1``
        while leaving the rate at 1.0 would satisfy either alone.
        """
        recorded = _recorded("S9", "V1")
        run = recorded.run
        afforded = claims_from_run(run)
        undecided = _undecidable_claim(run, recorded.program)

        # The precondition. If this claim ever became decidable the test below
        # would pass a broken implementation for the same reason the rest of the
        # module does, and silently.
        alone = _verdicts(run, recorded.program, (undecided,))
        assert not alone[0].adjudicated, (
            f"the injected claim came back {alone[0].outcome.value!r} rather "
            f"than referred, so it cannot move an adjudication rate"
        )

        quiet = dict(_rescored("S9", "V1", run, afforded).as_payload())
        loud = dict(_rescored("S9", "V1", run, (*afforded, undecided)).as_payload())

        assert quiet["adjudication_rate"] == 1.0
        assert loud["claims"] == quiet["claims"] + 1.0
        assert loud["adjudicated"] == quiet["adjudicated"], (
            "the injected claim was referred, so it must not be counted as adjudicated"
        )
        assert loud["adjudicated"] < loud["claims"]
        assert loud["adjudication_rate"] < 1.0, (
            f"a claim the verifier refused to grade left the rate at "
            f"{loud['adjudication_rate']!r}; the field is not reading the "
            f"verdicts"
        )

    def test_a30_an_inconsistent_adjudication_cannot_be_built(self) -> None:
        """The three relations the counts' docstrings state, made false-able.

        :class:`~sciagent.verify.Verdict` refuses to report an outcome gentler
        than its own findings imply, in this same call path; this is that
        discipline one level up, and CLAUDE.md's second invariant is what asks
        for it -- *"enforce with runtime assertions, not comments"*. A frozen
        dataclass of four bare ints is precisely where a docstring would
        otherwise have been the whole of the guarantee.

        The zombie clause is the one worth having. ``zombies`` and
        ``contradictions`` are counted from **different places** -- the first
        from :func:`~sciagent.verify.contradiction.zombie` over the population,
        the second from the findings the verifier returned -- and only the fact
        that ``check`` refuses on that same predicate makes them agree. Nothing
        else in the suite would notice them drifting apart.
        """
        assert Adjudication(claims=2, adjudicated=2, contradictions=1, zombies=1)

        with pytest.raises(MalformedClaimError, match="more than it was offered"):
            Adjudication(claims=1, adjudicated=2, contradictions=0, zombies=0)
        with pytest.raises(MalformedClaimError, match="subset"):
            Adjudication(claims=2, adjudicated=2, contradictions=0, zombies=1)
        with pytest.raises(MalformedClaimError, match="negative"):
            Adjudication(claims=2, adjudicated=-1, contradictions=0, zombies=0)

    def test_a30_a_claim_about_another_run_is_refused(self) -> None:
        """The `claims` seam is bounded by an assertion, not by its docstring.

        Item 12 feeds this parameter from an agent, and a claim about a
        hypothesis the run never entertained is not a claim the run can be
        graded on -- counting it moves criterion 10's denominator with something
        the investigation never held. Same for evidence the run never
        registered, which
        :meth:`~sciagent.verify.relevance.EvidenceIndex.record` already refuses
        several frames deeper without naming which claim.

        What this does **not** bound is merit, and the test says so rather than
        leaving a reader to infer a stronger guarantee than exists: a
        well-formed but worthless claim passes here, the verifier refuses it,
        and a refusal *is* adjudicated. That is a property of criterion 10 and
        is recorded against the backlog rather than papered over.
        """
        recorded = _recorded("S9", "V1")
        run = recorded.run
        stranger = HypothesisId("no_such_hypothesis")
        assert stranger not in run.graph.nodes

        with pytest.raises(MalformedClaimError, match=r"never.*entertained"):
            adjudicate(
                run,
                program=recorded.program,
                claims=(_supporting_claim(run, stranger, tag="stranger"),),
            )

        real = _accepted_supporting("S9", "V1")[0]
        with pytest.raises(MalformedClaimError, match=r"never.*registered"):
            adjudicate(
                run,
                program=recorded.program,
                claims=(replace(real, evidence=(ExperimentId("no-such-experiment"),)),),
            )

        # A **component** subject, which the first version of this check skipped
        # entirely -- and skipping it mattered rather than being untidy, because
        # a component-subject claim is the one demonstrated lever on criterion 10
        # in this repository (see the referral test above). A guarantee false of
        # half of `SubjectKind` is not the guarantee its docstring stated.
        undecidable = _undecidable_claim(run, recorded.program)
        assert undecidable.subject_kind == "component"
        adjudicate(run, program=recorded.program, claims=(undecidable,))
        with pytest.raises(MalformedClaimError, match="does not hold"):
            adjudicate(
                run,
                program=recorded.program,
                claims=(replace(undecidable, subject=ComponentId("no_such_thing")),),
            )

    def test_a30_a_zombie_is_a_contradiction_too(self) -> None:
        """The zombie count is a subset of the contradiction count, always.

        Every zombie claim is refused *by the contradiction check*, so a payload
        where the zombie count exceeds the contradiction count has read one of
        them from somewhere the other does not. Asserted on the injected case as
        well as the quiet ones, since a subset relation that holds only where
        both are zero says nothing.
        """
        for name in SCENARIOS:
            for arm in ARMS:
                payload = _payload(name, arm)
                assert payload["zombie_claims"] <= payload["contradictions"]

        run, victim = _with_a_rejection("S11", "V1")
        loud = dict(
            _rescored(
                "S11",
                "V1",
                run,
                (*claims_from_run(run), _supporting_claim(run, victim, tag="z")),
            ).as_payload()
        )
        assert loud["zombie_claims"] > 0.0
        assert loud["zombie_claims"] <= loud["contradictions"]

    def test_a30_a_run_that_afforded_no_claim_reports_no_rate(self) -> None:
        """``nan`` at the ledger boundary, not 0.0 and not 1.0.

        A run citing nothing affords no claim -- ``claims_from_run`` returns the
        empty tuple -- and a rate over an empty population is not a number. The
        two values a reader might expect are both false statements: ``0.0``
        reads as a verifier that decided none of them and ``1.0`` as one that
        decided all, and neither happened. ``nan`` is what
        :func:`~sciagent.eval.report._summarise` already excludes from a mean and
        counts separately, exactly as it does for the autonomy fraction.
        """
        recorded = _recorded("S9", "B1")
        idle = replace(recorded.run, experiments=0, evidence=EvidenceIndex.of(()))
        assert claims_from_run(idle) == ()
        assert adjudicate(idle, program=recorded.program).rate is None

        reading, _grown = reading_of(
            idle,
            grammar=GRAMMAR,
            table=gate_table(),
            simulate=simulator(GRAMMAR),
            observations=(),
            program=recorded.program,
        )
        payload = dict(reading.as_payload())
        assert payload["claims"] == 0.0
        assert math.isnan(payload["adjudication_rate"])

    def test_a30_a_row_from_the_previous_reading_is_not_pooled_beside_these(
        self,
    ) -> None:
        """The reading bumps, and a row scored under the old one is excluded.

        ``spec8/4`` rows carry no adjudication figure and no contradiction
        count, so summarising them beside these would average a field over
        whichever rows happened to hold it. ``DIMENSION_VERSION`` is the term
        that moves and ``METRIC_VERSION`` is not: the metric version addresses
        every cached empirical table, and this change touches no estimator.
        """
        # The literal moved to `spec8/6` at gate A42, which is the next scoring
        # change of the kind this assertion exists to notice -- the payload
        # gained D4's comparison-set size. What it pins is unchanged: the term
        # moves when a reading changes and only then, and the stale row below is
        # still `spec8/4`, so the exclusion is exercised across two generations
        # rather than one.
        assert DIMENSION_VERSION == "spec8/6"

        current = _campaign((Cell("V1", ScenarioId("S9"), 1),), _built_reading)[0]
        stale = dataclasses.replace(
            current,
            key=dataclasses.replace(
                current.key,
                config=type(current.key.config)(
                    {**dict(current.key.config), "dimensions": "spec8/4"}
                ),
            ),
        )
        with pytest.raises(MalformedDesignError, match="spec8/4"):
            _report_of((stale,))

    def test_a30_the_report_shows_the_adjudication_beside_every_cell(self) -> None:
        """The entry's *"verdicts aggregated per campaign"*, where a reader is.

        Criteria 8 and 10 are read off the report, and a field a reader has to
        open the ledger to see has made neither decidable. The line is asserted
        **inside each cell's block** rather than anywhere in the rendered text,
        for the reason ``test_a31``'s equivalent is: a trailing section after all
        the blocks emits exactly as many correct lines and satisfies none of the
        clause.
        """
        rows = _campaign(_every_cell(), _built_reading)
        text = _report_of(rows)
        for name in SCENARIOS:
            for arm in ARMS:
                block = _block(text, name, arm)
                printed = [
                    line for line in block if line.strip().startswith("adjudicated")
                ]
                assert len(printed) == 1, (
                    f"{arm} on {name} carries {len(printed)} adjudication "
                    f"line(s) in its dimension block; the rate is reported per "
                    f"cell"
                )
                rate = _payload(name, arm)["adjudication_rate"]
                assert f"{rate:>10.4f}" in printed[0], (
                    f"the adjudication rate in {arm}/{name}'s block is not "
                    f"{rate!r}: {printed[0]!r}"
                )

        everywhere = [
            line for line in text.splitlines() if line.strip().startswith("adjudicated")
        ]
        assert len(everywhere) == len(rows)

    def test_a30_the_report_carries_the_share_criterion_10_actually_names(
        self,
    ) -> None:
        """Criterion 10 weights *claims*; the per-replicate mean weights runs.

        The two agree only where a cell's replicates afford equal-sized claim
        populations, which is true of every conventional arm -- measured, six
        replicates each of B1 and V1 on S9 and S11, all afford exactly 16 and 80
        -- and false of any arm holding a proposal layer, since how many
        structures it entertains varies by replicate. Those are V7, V3 and V4:
        every arm the recorded campaign exists to compare.

        Constructed rather than run, because no conventional arm can produce the
        disagreement and running an LLM arm here would cost a model call. Two
        replicates at 80/100 and 4/4 average to 0.9000 and clear the bar; the
        share they represent is 84/104 = 0.8077 and does not. A report printing
        only the mean says a cell passed criterion 10 when it failed.
        """
        summary = summarise(
            tuple(
                _synthetic_row(replicate, claims=claims, adjudicated=adjudicated)
                for replicate, (claims, adjudicated) in enumerate(
                    ((100.0, 80.0), (4.0, 4.0))
                )
            ),
            address=ADDRESS,
            scenario_class=_scenario_class,
            battery=lambda target: scenario(str(target)).held_out,
            platform=PLATFORM,
            numpy_version=NUMPY,
            grammar=REPORT_GRAMMAR,
        )
        cell = summary.cells[0]

        assert cell.adjudication_rate.point == pytest.approx(0.9)
        assert cell.adjudicated_share == pytest.approx(84.0 / 104.0)
        assert cell.adjudicated_share < 0.9 <= cell.adjudication_rate.point, (
            "this fixture is meant to straddle criterion 10's bar; if it no "
            "longer does, the two statistics are not being told apart here"
        )

        printed = [
            line
            for line in render(summary).splitlines()
            if line.strip().startswith("claims afforded")
        ]
        assert len(printed) == 1
        assert f"{cell.adjudicated_share:>10.4f}" in printed[0]
        # Both counts in the clear, so the quotient is checkable rather than
        # taken on trust -- criterion 9's three masses are printed the same way.
        assert f"{cell.claims.point:>10.3f}" in printed[0]
        assert f"{cell.adjudicated.point:>10.3f}" in printed[0]
