"""Acceptance test A48: the contrast conditions on an arm-invariant event.

A48 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"The preregistered contrast conditions on an event
the arms it compares extinguish"*, and reads:

    ``test_a48_the_contrast_conditions_on_an_arm_invariant_event`` -- a
    contrast on a matrix where the treatment expands successfully still has a
    conditioning population; the filter admits replicates the *probe* flagged
    rather than those the arm's own post-hoc check flagged; and a contrast
    whose conditioning population is genuinely empty still refuses rather than
    reporting over everything.

What was wrong
--------------

§9's primary contrast reads "conditional on inadequacy detection", and
:func:`~sciagent.eval.report.contrast` implemented that as the replicate's own
``inadequate`` flag -- the whole-record posterior predictive check, taken
*after* ``investigate`` returns. An arm that expands successfully explains the
inadequacy away before that check is read, so the conditioning event is
extinguished by the very thing the contrast exists to measure: on the recorded
campaign B1 detects on 1.000 of S11 replicates and V7 and B4 on 0.000, and the
contrast refuses. Conditioning a treatment-versus-comparator comparison on a
post-treatment quantity selects against exactly the arms that succeeded, more
strongly the better they do.

The decision this gate encodes
------------------------------

Taken by the user on 2026-08-27 and recorded in ``docs/DECISIONS.md``:
"inadequacy detection" names the **Stage A probe** -- harness-evaluated before
``investigate`` for every arm alike (gate A29), and therefore arm-invariant --
not B1's whole-record check and not the arm's own. §9's two statements of the
claim are reconciled in wording as well as in code.

Disclosed on the BACKLOG entry and repeated here so no reader takes this gate
for a result: on the recorded campaign V7's and B4's D3 are bit-identical on
every S11 seed, so under the probe the preregistered contrast answers **tie**.
An arm-invariant event selects the same seed set for both arms by construction;
no choice of it can manufacture a directional advantage.

Why the fixture drives the two flags apart
------------------------------------------

On rows where ``inadequate`` and ``probe_inadequate`` agree, the old filter and
the new one are indistinguishable. Every case below that pins the filter's
identity makes them disagree per replicate and hangs a different D3 value on
each side of the disagreement, so the conditioned mean says which flag did the
filtering rather than the test inferring it from a refusal that could have
other causes.
"""

from __future__ import annotations

from collections.abc import Sequence

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
from sciagent.eval.report import Contrast, MatrixReport, contrast, summarise
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

#: The preregistered contrast's coordinates. Stated here from SPEC §9 rather
#: than imported from ``environments.pointproc.matrix``, so a drift in the
#: declared constant cannot silently re-aim this gate.
SCENARIO = ScenarioId("S11")
TREATMENT = "V7"
COMPARATOR = "B4"
DIMENSION = "d3_intervention_similarity"


def _scenario_class(target: ScenarioId) -> ScenarioClass:
    """Classify off the environment's declaration, as the real caller does."""
    return scenario(str(target)).scenario_class


def _battery(target: ScenarioId) -> Sequence[ExperimentDesign]:
    """Return the scenario's declared held-out battery, as the real caller does."""
    return scenario(str(target)).held_out


def _reading(*, inadequate: bool, probe_inadequate: bool, d3: float) -> CellReading:
    """Return a reading whose load-bearing fields are the two flags and D3.

    Every other number is stated rather than derived, exactly as
    ``test_a46.py`` builds its rows: the ledger stores a flat payload and
    ``summarise`` folds every field of one, so a row cannot be built without a
    whole vector, and none of the rest is read by this gate.
    """
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=0.0,
            d2_held_out_predictive=-1.0,
            d3_intervention_similarity=d3,
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
        inadequate=inadequate,
        probe_p_value=0.03,
        probe_inadequate=probe_inadequate,
        agency=AgencyMetrics(
            system="V7",
            scenario=SCENARIO,
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
    *,
    system: str,
    replicate: int,
    inadequate: bool,
    probe_inadequate: bool,
    d3: float,
) -> LedgerEntry:
    """Return one recorded S11 row, seeded by its replicate index.

    Seed equals replicate for both arms, which is the matrix's own pairing:
    seeds are a function of the scenario and the replicate index alone, never
    of the system.
    """
    task = CellTask(
        cell=Cell(system, SCENARIO, 1), replicate=replicate, seed=Seed(replicate)
    )
    return LedgerEntry(
        key=cell_key(task, ADDRESS, battery=tuple(_battery(SCENARIO))),
        reading=FrozenDict[str, float](
            dict(
                _reading(
                    inadequate=inadequate,
                    probe_inadequate=probe_inadequate,
                    d3=d3,
                ).as_payload()
            )
        ),
        sequence=replicate,
    )


def _report(
    *,
    inadequate: Sequence[bool],
    probe_inadequate: Sequence[bool],
    d3: Sequence[float],
) -> MatrixReport:
    """Summarise a two-arm S11 campaign with the stated per-replicate fields.

    Both arms get identical vectors, because the event the decision names is
    arm-invariant by A29 and a fixture that varied it by arm would build the
    breach :func:`~sciagent.eval.report._probe_counts` refuses.
    """
    rows = [
        _row(
            system=system,
            replicate=index,
            inadequate=inadequate[index],
            probe_inadequate=probe_inadequate[index],
            d3=d3[index],
        )
        for system in (TREATMENT, COMPARATOR)
        for index in range(len(d3))
    ]
    return summarise(
        tuple(rows),
        address=ADDRESS,
        scenario_class=_scenario_class,
        battery=_battery,
        platform=PLATFORM,
        numpy_version=NUMPY,
        grammar=GRAMMAR,
    )


def _conditioned(report: MatrixReport) -> Contrast:
    """Run the preregistered contrast, conditioned, on ``report``."""
    return contrast(
        report,
        scenario=SCENARIO,
        treatment=TREATMENT,
        comparator=COMPARATOR,
        dimension=DIMENSION,
        conditional_on_inadequacy=True,
    )


class TestA48TheContrastConditionsOnAnArmInvariantEvent:
    """§9's conditioning event is the Stage A probe, not the arm's own check."""

    def test_a48_the_contrast_conditions_on_an_arm_invariant_event(
        self,
    ) -> None:
        """The gate's first clause, on the recorded campaign's own shape.

        Named for the gate, as ``docs/BACKLOG.md``'s **Gate.** line declares
        it, so anything resolving the declared node id -- a targeted run, a
        reader following the entry -- finds a real test; the siblings below
        pin the second and third clauses under their own names.

        Probe fired on every replicate; whole-record check quiet on every one,
        because both arms expanded and explained the inadequacy away. Under the
        old filter this is exactly the matrix on which ``contrast`` refused
        with *"no replicate of V7 on S11 detected inadequacy"*; under the
        probe it has a full conditioning population and answers.
        """
        result = _conditioned(
            _report(
                inadequate=(False, False, False, False),
                probe_inadequate=(True, True, True, True),
                d3=(0.6, 0.7, 0.8, 0.9),
            )
        )
        assert result.conditioned_on_inadequacy
        assert result.treatment.n_finite == 4
        assert result.comparator.n_finite == 4

    def test_a48_the_filter_reads_the_probe_not_the_arms_own_check(self) -> None:
        """The gate's second clause: which flag filtered is read off the mean.

        The two flags disagree on every replicate -- the probe names the first
        two, the whole-record check names the last two -- and D3 is 0.9 where
        the probe fired and 0.1 where it did not. A conditioned mean of 0.9
        can only have been filtered by the probe; the old filter reports 0.1
        on the same rows. Asserted on both arms, exactly, because the values
        are stated constants and a tolerance would blur the two filters this
        exists to tell apart.
        """
        result = _conditioned(
            _report(
                inadequate=(False, False, True, True),
                probe_inadequate=(True, True, False, False),
                d3=(0.9, 0.9, 0.1, 0.1),
            )
        )
        assert result.treatment.point == 0.9
        assert result.comparator.point == 0.9
        assert result.treatment_seeds == (0, 1)
        assert result.comparator_seeds == (0, 1)

    def test_a48_an_arm_invariant_event_selects_the_same_seeds_for_both_arms(
        self,
    ) -> None:
        """Conditioning no longer breaks the pairing the matrix establishes.

        Under the old filter each arm was filtered by its *own* flag, so the
        arms could survive on different seed sets and the paired difference
        was refused. The probe's verdict is a function of the scenario and the
        seed alone, so both arms keep the same seeds by construction and the
        paired difference exists. Its value here is exactly zero -- the two
        arms carry identical D3 vectors -- which is the BACKLOG entry's
        disclosed tie, pinned so nobody can later mistake this change for the
        thing that produced a win.
        """
        result = _conditioned(
            _report(
                inadequate=(False, False, False, False),
                probe_inadequate=(True, True, True, False),
                d3=(0.6, 0.7, 0.8, 0.9),
            )
        )
        assert result.paired
        assert result.paired_difference is not None
        assert result.paired_difference.point == 0.0

    def test_a48_an_empty_conditioning_population_still_refuses(self) -> None:
        """The gate's third clause: a quiet probe is a refusal, not a caption.

        The probe fired nowhere, and the arm's own check fired everywhere --
        the inverse of the recorded campaign, and the trap for a filter that
        silently fell back to the old flag or to the unconditioned rows: either
        of those *returns* here, over a population the probe never selected.
        The refusal must name the event it conditioned on, so a reader of the
        message can tell which question had no answer.
        """
        with pytest.raises(MalformedDesignError, match="Stage A probe"):
            _conditioned(
                _report(
                    inadequate=(True, True, True, True),
                    probe_inadequate=(False, False, False, False),
                    d3=(0.6, 0.7, 0.8, 0.9),
                )
            )

    def test_a48_an_unconditioned_contrast_ignores_both_flags(self) -> None:
        """Relaxing the conditioning still means *no* filter, old or new.

        ``conditional_on_inadequacy=False`` must read every replicate -- the
        probe-quiet ones included -- so the mean here spans all four values.
        Pinned because a re-implementation that filtered by the probe
        unconditionally would pass every conditioned case above while quietly
        answering the conditioned question under the unconditioned name.
        """
        result = contrast(
            _report(
                inadequate=(True, False, True, False),
                probe_inadequate=(True, True, False, False),
                d3=(0.2, 0.4, 0.6, 0.8),
            ),
            scenario=SCENARIO,
            treatment=TREATMENT,
            comparator=COMPARATOR,
            dimension=DIMENSION,
            conditional_on_inadequacy=False,
        )
        assert result.treatment.point == pytest.approx(0.5)
        assert result.treatment.n_finite == 4
