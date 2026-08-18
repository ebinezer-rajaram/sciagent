"""Agency metrics over slice runs (SPEC §11 item 14).

SPEC F10 requires two approval tiers and an autonomy fraction reported alongside
every performance figure; §12 criterion 11 requires it for every investigation.
Neither says where the boundary between the tiers falls, and this module is
where that reading is written down. ``docs/DECISIONS.md`` records why it is this
one.

The two tiers
-------------

An :class:`~sciagent.systems.base.Investigation` offers a system exactly two
mutating operations, so there are exactly two kinds of act to tier.

**Tier 1, unilateral.** Running one of the designs the scenario offers. The
design set is fixed and licensed in advance, so a system choosing within it is
acting inside authority it already has.

**Tier 2, escalated.** Introducing a hypothesis *after* evidence is in hand.
This changes what is being estimated part-way through an investigation, which is
the act a deployment would put a human in front of.

The boundary is lateness, not proposal count
--------------------------------------------

Nearly every system introduces structure: :func:`~sciagent.systems.base.entertain`
routes each library structure through
:meth:`~sciagent.systems.base.Investigation.propose`, so V1's
:attr:`~sciagent.eval.campaign.ScenarioRun.proposed` holds its whole library and
only B1's is empty. Counting proposals would therefore report V1 -- the system
*defined* as never extending its hypothesis space -- as less autonomous than B1
for doing the one thing it does, and would make the fraction depend on how large
a library the harness handed out.

So the boundary is SPEC F9's lateness datum: ``proposed_at`` on the graph node,
``None`` for a hypothesis introduced before any experiment. F9 already treats a
late hypothesis as one that cannot support a confirmatory claim without a
prospectively registered discriminating experiment, which is the framework's
existing statement that such a hypothesis needs something more before it counts.
The opening set is reported as :attr:`AgencyMetrics.entertained` and kept out of
the fraction.

Nothing here reads a number an agent wrote
------------------------------------------

``proposed_at`` is set by ``Investigation.propose`` from the investigation's own
history and is unreachable from a system. The obvious alternative -- reading the
``rationale`` string, which :func:`~sciagent.systems.base.entertain` writes as
``library structure '...'`` -- would have let a system raise its own autonomy
fraction by writing a different rationale. Prose an agent authored may not decide
a number the framework reports (SPEC's second invariant).

The proposal record is a separate object
----------------------------------------

:class:`ProposalRecord` is what the model was asked and what became of it, read
off :attr:`~sciagent.eval.campaign.ScenarioRun.attempts`. It is ``None`` for a
system holding no proposal layer, which is *not* the same as a record of zero
requests: B4 and B5 introduce structure without ever consulting a model, so
reporting ``requested=0`` beside a positive escalation count would be a
contradiction on the face of the report. A system that holds a layer and never
consults it -- V7 on a scenario whose check never opens SPEC F6's gate -- does
get a record, reading zero. That is the honest report that the gate stayed shut,
and it is a different fact from having no gate.

Where a record exists, it is checked against the graph rather than believed. An
attempt the system recorded as admitted must appear as an escalation; a record
claiming more admissions than the graph received late structures means a system's
account of what it did disagrees with what the framework recorded, and that
raises rather than being reported.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields

from sciagent.core.errors import InvestigationError
from sciagent.core.types import ScenarioId
from sciagent.eval.campaign import ScenarioRun
from sciagent.systems.hybrid import ProposalAttempt

__all__ = [
    "PROPOSAL_OUTCOMES",
    "AgencyMetrics",
    "AgencyReport",
    "ProposalRecord",
    "agency_metrics",
    "agency_report",
    "proposal_record",
]


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """What a proposal layer was asked for, and what came back.

    Kept apart from the escalation count because the two answer different
    questions. The escalation count is what entered the hypothesis space; this is
    what was *requested*, and a system that asked five times and used one did
    something different from one that asked once.
    """

    admitted: int
    """Proposals that entered the hypothesis space."""

    duplicate: int
    """Proposals whose structure the graph already held.

    Not admitted -- SPEC §6.4's A18 makes re-proposing a structure an error -- and
    still worth counting: a model that keeps proposing what is already entertained
    is doing something a report should show.
    """

    refused: int
    """Requests that produced no structure and no draft to fault.

    Two causes, and the record does not separate them: a provider that declined,
    and :meth:`~sciagent.systems.base.Investigation.propose` refusing the
    expansion for want of budget. The second is unreachable while expansion is
    free, and it is named here rather than left to be discovered, because a
    reader counting refusals as evidence about a *model* would be wrong about
    every one of them the moment expansion becomes chargeable.
    """

    malformed: int
    """Drafts that did not decode to a structure the grammar licenses."""

    unmeasurable: int
    """Structures that decode and execute, but that a design cannot measure.

    Kept apart from ``malformed`` because the draft was faultless: it named a
    licensed structure at on-grid parameters, and the table refused it only
    because some design in its set yields no row on that structure
    (:class:`~sciagent.core.errors.StructureNotMeasurableError`). Counting it as
    malformed would blame the model for a limit of the measurement set, and
    counting it as refused would blame a provider that declined nothing.

    Reachable only by an arm that proposes structure outside the library, so it
    is always zero for V1, B1, B4 and B5.
    """

    @property
    def requested(self) -> int:
        """Return how many times the layer was asked, whatever came back."""
        return (
            self.admitted
            + self.duplicate
            + self.refused
            + self.malformed
            + self.unmeasurable
        )

    @property
    def yield_fraction(self) -> float | None:
        """Return admissions per request, or ``None`` if the layer was never asked.

        ``None`` rather than zero: a layer that was never consulted has no yield,
        and a zero would pool into an aggregate as though it were a layer that
        was asked and produced nothing.
        """
        if self.requested == 0:
            return None
        return self.admitted / self.requested


#: Every outcome :class:`~sciagent.systems.hybrid.ProposalAttempt` may carry.
#:
#: Derived from :class:`ProposalRecord`'s own fields rather than written out
#: beside them. The two must agree exactly -- :func:`proposal_record` validates
#: an attempt against this tuple and then builds a record field by field -- and
#: an outcome listed here with no field to hold it would be counted, accepted,
#: and dropped, understating what was requested and inflating the yield over a
#: denominator missing the cases it could not place. Deriving makes that
#: disagreement unrepresentable instead of merely unlikely.
PROPOSAL_OUTCOMES: tuple[str, ...] = tuple(
    field.name for field in fields(ProposalRecord)
)


@dataclass(frozen=True, slots=True)
class AgencyMetrics:
    """One investigation's agency, in SPEC F10's two tiers."""

    system: str
    scenario: ScenarioId

    experiments: int
    """Tier-1 decisions: designs run from the set the scenario licensed."""

    entertained: int
    """Structures introduced before any experiment.

    The space the investigation opened with. Reported because it says how large a
    hypothesis space a system assembled, and excluded from
    :attr:`autonomy_fraction` because including it would let a system raise its
    own autonomy by entertaining more of its library.
    """

    escalated: int
    """Tier-2 decisions: structures introduced after evidence was in hand."""

    proposals: ProposalRecord | None
    """The proposal layer's record, or ``None`` if the system holds no layer."""

    @property
    def decisions(self) -> int:
        """Return the decisions the fraction is taken over."""
        return self.experiments + self.escalated

    @property
    def autonomy_fraction(self) -> float | None:
        """Return the share of decisions taken unilaterally, or ``None``.

        ``None`` when the run took no decision at all. Reporting 1.0 there would
        credit a system that did nothing with full autonomy, and would pool into
        an aggregate as though it were evidence.
        """
        if self.decisions == 0:
            return None
        return self.experiments / self.decisions


@dataclass(frozen=True, slots=True)
class AgencyReport:
    """One system's agency pooled over the runs it was measured on.

    Pooled rather than a mean of per-run fractions: a run that spent thirty
    experiments and one that spent two say different amounts about how much of an
    investigation a system drove, and averaging fractions would weigh them
    equally.
    """

    system: str
    runs: int
    experiments: int
    entertained: int
    escalated: int
    proposals: ProposalRecord | None
    """The summed record, or ``None`` if no run of this system held a layer."""

    @property
    def decisions(self) -> int:
        """Return the pooled decision count."""
        return self.experiments + self.escalated

    @property
    def autonomy_fraction(self) -> float | None:
        """Return the pooled share of decisions taken unilaterally."""
        if self.decisions == 0:
            return None
        return self.experiments / self.decisions


def proposal_record(attempts: Sequence[ProposalAttempt]) -> ProposalRecord:
    """Return the outcome counts of one run's proposal attempts.

    Guarantees every attempt is counted. Raises
    :class:`~sciagent.core.errors.InvestigationError` on an outcome outside
    :data:`PROPOSAL_OUTCOMES` rather than skipping it, since a skipped attempt
    would understate what the layer was asked for.
    """
    counts = dict.fromkeys(PROPOSAL_OUTCOMES, 0)
    for attempt in attempts:
        if attempt.outcome not in counts:
            raise InvestigationError(
                f"proposal attempt reports outcome {attempt.outcome!r}, which is "
                f"not one of {list(PROPOSAL_OUTCOMES)!r}; counting it would need "
                f"a tier it has not been given"
            )
        counts[attempt.outcome] += 1
    return ProposalRecord(
        admitted=counts["admitted"],
        duplicate=counts["duplicate"],
        refused=counts["refused"],
        malformed=counts["malformed"],
        unmeasurable=counts["unmeasurable"],
    )


def agency_metrics(run: ScenarioRun) -> AgencyMetrics:
    """Return SPEC F10's two tiers for one investigation.

    Guarantees every count comes off what the framework recorded: the number of
    experiments, and each introduced hypothesis's ``proposed_at`` as the graph
    holds it. No field a system authored is read.

    Raises :class:`~sciagent.core.errors.InvestigationError` if the system's own
    proposal record claims more admissions than the graph received late
    structures, which means the two accounts of the run disagree.
    """
    entertained = 0
    escalated = 0
    for node_id in sorted(run.proposed):
        if run.graph.node(node_id).proposed_at is None:
            entertained += 1
        else:
            escalated += 1

    record = proposal_record(run.attempts) if run.attempts is not None else None
    if record is not None and record.admitted > escalated:
        raise InvestigationError(
            f"system {run.system!r} on {run.scenario.id!r} recorded "
            f"{record.admitted} admitted proposal(s) but the graph holds "
            f"{escalated} hypothesis introduced after evidence; a proposal the "
            f"framework did not record entering the hypothesis space cannot be "
            f"reported as one"
        )
    return AgencyMetrics(
        system=run.system,
        scenario=run.scenario.id,
        experiments=run.experiments,
        entertained=entertained,
        escalated=escalated,
        proposals=record,
    )


def agency_report(metrics: Iterable[AgencyMetrics]) -> tuple[AgencyReport, ...]:
    """Return one pooled row per system, ordered by name.

    Guarantees the result does not depend on the order the runs arrived in:
    systems are keyed by name and the rows are sorted, so a report built from a
    reordered campaign is byte-identical (SPEC's third invariant).
    """
    pooled: dict[str, list[AgencyMetrics]] = {}
    for item in metrics:
        pooled.setdefault(item.system, []).append(item)
    return tuple(_pool(system, pooled[system]) for system in sorted(pooled))


def _pool(system: str, items: Sequence[AgencyMetrics]) -> AgencyReport:
    """Return one system's rows summed into a single report row."""
    records = [item.proposals for item in items if item.proposals is not None]
    summed = (
        ProposalRecord(
            admitted=sum(record.admitted for record in records),
            duplicate=sum(record.duplicate for record in records),
            refused=sum(record.refused for record in records),
            malformed=sum(record.malformed for record in records),
            unmeasurable=sum(record.unmeasurable for record in records),
        )
        if records
        else None
    )
    return AgencyReport(
        system=system,
        runs=len(items),
        experiments=sum(item.experiments for item in items),
        entertained=sum(item.entertained for item in items),
        escalated=sum(item.escalated for item in items),
        proposals=summed,
    )
