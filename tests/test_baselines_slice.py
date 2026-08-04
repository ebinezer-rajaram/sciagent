"""Backlog item 9's gate: the baselines run on the slice (SPEC §11).

Item 9's gate is an integration one -- "Run on S1-S10" -- not an A-gate, so
nothing here is named ``test_aN_``. What it establishes is that the four
conventional systems of SPEC §5 complete every closed-world scenario, stay
inside their budgets, and produce numbers the framework wrote.

It deliberately asserts *properties* rather than performance. SPEC §12 says
beating B4 or B5 is not an exit criterion but the research question, so a test
that pinned V1 above B4 would be encoding an answer nobody has yet. The one
place a number is asserted is where a baseline would otherwise be free to be
useless: a system that entertains the truth must beat one that cannot.

Cost
----

B5 dominates. Scoring a candidate structure means simulating it, and the beam
visits every single edit the agent grammar licenses at corner resolution --
about fifty. The whole suite shares **one** :class:`BeamSearch`, so those rows
are simulated once and reused across all ten scenarios; building them per
scenario would multiply the cost by ten for no extra information. The measured
figures are in ``docs/DECISIONS.md``.
"""

from __future__ import annotations

import math
from functools import lru_cache

import pytest
from slice_tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    gate_table,
    save_gate_table,
    search_table,
)

from environments.pointproc.grammar import agent_grammar
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario, slice_scenarios
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import ResearchSystem, null_seeded_graph
from sciagent.systems.baselines.beam_search import BeamSearch, table_fit
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly
from sciagent.systems.baselines.retrieval import Retrieval

#: How deep B5 searches. One level means single-edit structures only, which puts
#: S8's compound truth out of its reach -- a stated limitation of this
#: configuration, not of the algorithm, and a cost decision: a second level
#: multiplies the structures to simulate by the beam width.
SEARCH_LEVELS = 1

SYSTEM_NAMES = ("V1", "B1", "B4", "B5")


@lru_cache(maxsize=1)
def _beam_search() -> BeamSearch:
    """Return the one B5 every scenario is run with.

    Its search space is the cached
    :func:`~slice_tables.search_table`, so no scenario simulates a candidate.
    Shared across scenarios because B5 holds no per-scenario state -- the beam
    is rebuilt from the observations each time -- so sharing changes what it
    costs and not what it concludes.
    """
    return BeamSearch(
        agent_grammar(),
        table_fit(search_table(), simulator(agent_grammar())),
        width=3,
        levels=SEARCH_LEVELS,
    )


def _system(name: str) -> ResearchSystem:
    """Return the SPEC §5 system with this identifier."""
    if name == "V1":
        return BOEDOnly(closed_set())
    if name == "B1":
        return PPCOnly()
    if name == "B4":
        return Retrieval(closed_set())
    if name == "B5":
        return _beam_search()
    raise AssertionError(f"no system {name!r}")


@lru_cache(maxsize=1)
def _runs() -> dict[tuple[str, str], ScenarioRun]:
    """Run every system on every scenario once, keyed by ``(system, scenario)``.

    Cached because it is the expensive thing in this module and every test below
    reads it.

    The engine table is threaded from one run to the next and saved at the end.
    A system that proposes a structure outside the closed set makes the engine
    simulate a row for it at the slice's full 2000 replicates, which dominates
    everything else here; carrying the grown table forward means each distinct
    proposal is simulated once per machine rather than once per scenario per
    session. It changes what the gate costs and not what it concludes, since a
    row is a pure function of ``(defect, template, seed)``.
    """
    table = gate_table()
    results: dict[tuple[str, str], ScenarioRun] = {}
    for name in SYSTEM_NAMES:
        system = _system(name)
        for the_scenario in slice_scenarios():
            graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
            engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
            results[name, str(the_scenario.id)] = run_scenario(
                the_scenario,
                system,
                executor=executor(
                    GRAMMAR,
                    store=ExperimentStore.in_memory(),
                    budget=the_scenario.budget,
                ),
                engine=engine,
                graph=graph,
            )
            table = engine.table
    save_gate_table(table)
    return results


class TestTheGate:
    """ "Run on S1-S10" (SPEC §11, item 9), and on S11 and S12 since item 11.

    Item 9's gate names the ten closed-world scenarios. The two that arrived with
    item 11 are run here as well rather than in a second place: they are the same
    four systems on the same table, and SPEC §12 criterion 4 needs B1's detection
    rate on S11 to be a measured floor rather than an assumption.
    """

    def test_every_system_completes_every_scenario(self) -> None:
        runs = _runs()
        assert set(runs) == {
            (name, str(s.id)) for name in SYSTEM_NAMES for s in slice_scenarios()
        }

    @pytest.mark.parametrize("name", SYSTEM_NAMES)
    def test_no_system_exceeds_its_budget(self, name: str) -> None:
        for the_scenario in slice_scenarios():
            run = _runs()[name, str(the_scenario.id)]
            assert run.experiments <= the_scenario.budget.total

    @pytest.mark.parametrize("name", SYSTEM_NAMES)
    def test_every_diagnosis_is_a_distribution(self, name: str) -> None:
        """Guaranteed by construction; asserted because it is what is scored."""
        for the_scenario in slice_scenarios():
            distribution = _runs()[name, str(the_scenario.id)].diagnosis.distribution
            total = math.fsum(distribution[h] for h in sorted(distribution))
            assert math.isclose(total, 1.0, rel_tol=1e-9)

    def test_s10_starves_every_system_of_experiments(self) -> None:
        """S10's point is that the budget, not the method, is the binding limit."""
        for name in SYSTEM_NAMES:
            assert _runs()[name, "S10"].experiments <= scenario("S10").budget.total


class TestTheBaselinesAreNotStrawMen:
    """SPEC §5 makes B4 the designated comparator.

    A baseline that lost because it was built carelessly would answer R1 by
    default, so each one's defining capability is asserted here.
    """

    def test_b4_identifies_more_single_mechanism_scenarios_than_b1(self) -> None:
        """B1 proposes nothing, so it can only ever be right about the null."""
        singles = ("S1", "S2", "S3", "S4")
        b4 = sum(_runs()["B4", s].score.correct for s in singles)
        b1 = sum(_runs()["B1", s].score.correct for s in singles)
        assert b4 > b1, f"B4 got {b4}/4 single mechanisms, B1 got {b1}/4"

    def test_b4_recovers_most_single_mechanism_scenarios(self) -> None:
        """A retrieval baseline that could not do this would be a straw man."""
        singles = ("S1", "S2", "S3", "S4")
        correct = sum(_runs()["B4", s].score.correct for s in singles)
        assert correct >= 3, f"B4 recovered only {correct}/4"

    def test_v1_recovers_most_single_mechanism_scenarios(self) -> None:
        singles = ("S1", "S2", "S3", "S4")
        correct = sum(_runs()["V1", s].score.correct for s in singles)
        assert correct >= 3, f"V1 recovered only {correct}/4"

    def test_b5_searching_beats_not_searching(self) -> None:
        """B5's exact-match score is zero everywhere, and that is not a failure.

        Its candidates are grid *corners* and the slice's truths are interior
        points of the same grids, so an exact-match score is zero almost by
        construction and says nothing about the search. The question that does
        have content is whether searching gets closer to the truth than not
        searching: B1 never proposes, so its nearest structure is always the
        seeded null, and that is the thing to beat.

        Measured at item 11, over the ten closed-world scenarios item 9's gate
        names: B5 averages 0.775 edits from the truth against B1's 1.111. The
        assertion is the comparison, not those figures. S11 and S12 are excluded
        because a distance to a truth outside the search space measures the
        grammar rather than the search.
        """
        non_null = [f"S{i}" for i in range(1, 11) if i != 9]
        b5 = [_runs()["B5", s].structural_distance for s in non_null]
        b1 = [_runs()["B1", s].structural_distance for s in non_null]
        mean_b5 = math.fsum(b5) / len(b5)
        mean_b1 = math.fsum(b1) / len(b1)
        assert mean_b5 < mean_b1, (
            f"B5 averaged {mean_b5:.3f} edits from the truth, no better than "
            f"proposing nothing at {mean_b1:.3f}"
        )
        inside = [s for s, d in zip(non_null, b5, strict=True) if d < 1.0]
        assert len(inside) >= 3, (
            f"B5 found the right structural cell on only {len(inside)} of "
            f"{len(non_null)} scenarios ({inside})"
        )


class TestAbstention:
    """SPEC §12 criterion 9, on the two scenarios it names."""

    @pytest.mark.parametrize("name", SYSTEM_NAMES)
    def test_s9_puts_its_mass_on_the_null(self, name: str) -> None:
        """The null is a hypothesis, so getting S9 right is a positive result."""
        run = _runs()[name, "S9"]
        assert run.diagnosis.null_mass > 0.5, (
            f"{name} put only {run.diagnosis.null_mass:.3f} on the null in S9"
        )

    def test_s10_leaves_every_system_uncertain(self) -> None:
        """SPEC §4.5 S10: Hawkes against regime switching, below the threshold.

        The reason moved at item 11 and the assertion did not. It used to hold
        because no design offered was a forced arrival; the intervention is in
        the design set now, and what makes S10 non-identifiable is its budget of
        two against an optimal policy's 3.008 (``tests/test_oracle.py``). A system
        identifying it would mean the budget is not below the threshold after
        all, and the scenario would have stopped being the one §4.5 describes.
        """
        for name in SYSTEM_NAMES:
            run = _runs()[name, "S10"]
            assert not run.score.identified, (
                f"{name} identified S10 with mass {run.score.truth_mass:.3f}; "
                f"the discriminating experiment is not in its design set"
            )


class TestDetectionIsMeasuredNotAssumed:
    """B1's Stage A rate, which SPEC §12 criterion 4 compares an LLM against."""

    def test_b1_detects_arrival_mechanisms_on_a_full_budget(self) -> None:
        """Evidence spread across experiments now accumulates instead of cancelling.

        On S1-S7 the truth is one of SPEC §4.2's four arrival mechanisms and B1
        holds only the null, so the hypothesis space is inadequate by
        construction and the check ought to say so. Under the Sidak correction on
        the smallest p-value it did not: eight experiments agreeing were treated
        as eight chances to be wrong, 0.013 was inflated to about 0.102, and
        detection got *worse* as the budget grew. ``docs/DECISIONS.md`` records
        the replacement and the measurements that chose it.

        What is asserted here is the property, not the roster: at least one
        eight-experiment arrival-mechanism scenario is detected. The roster
        itself moves with the calibration and is reported rather than pinned.
        """
        fed = [
            _runs()["B1", f"S{i}"]
            for i in range(1, 8)
            if _runs()["B1", f"S{i}"].experiments > 2
        ]
        assert fed, "no arrival-mechanism scenario ran on a full budget"
        detected = [run for run in fed if run.ppc.inadequate]
        assert detected, (
            "B1 detects no arrival mechanism on a full budget, so evidence "
            "across experiments is being discarded again; see the harmonic-mean "
            "entry in docs/DECISIONS.md"
        )

    def test_detection_no_longer_requires_a_starved_budget(self) -> None:
        """The pathology inverted: a fed scenario is no longer strictly worse off.

        S10 carries the *least* evidence of any scenario -- two experiments
        against everything else's eight -- and under the old correction it was
        the only arrival-mechanism scenario B1 flagged, purely because two tests
        carried a smaller penalty than eight. Its per-experiment evidence is the
        same 0.013 as the scenarios that were missed.

        Both are detected now, which is the finding. The starved scenario still
        reports the smaller combined value, and that is correct rather than a
        residue: two tests genuinely offer fewer chances to be wrong than eight.
        What had to go was a penalty steep enough to clear alpha by the fourth
        experiment.
        """
        starved, fed = _runs()["B1", "S10"], _runs()["B1", "S1"]
        assert starved.experiments < fed.experiments
        assert starved.ppc.inadequate, "the starved scenario stopped being detected"
        assert fed.ppc.inadequate, (
            f"only the starved scenario is detected ({starved.ppc.p_value:.4f} "
            f"against {fed.ppc.p_value:.4f}); the multiplicity penalty is once "
            f"again deciding rather than the data"
        )

    def test_the_adequate_hypothesis_space_is_not_flagged(self) -> None:
        """S9's truth is the null, which B1 holds, so nothing is inadequate.

        The true-negative control for the two tests above. A combiner that
        accumulates evidence more eagerly buys detection at the price of firing
        on a space that explains the data perfectly well, and this is what would
        catch that -- B1 on S9 is the one run in the slice where the hypothesis
        space provably contains the truth.
        """
        run = _runs()["B1", "S9"]
        assert not run.ppc.inadequate, (
            f"B1 flagged S9 as inadequate at p={run.ppc.p_value:.4f}, but it "
            f"holds the null and the null is the truth"
        )

    def test_b1_detects_the_compound_scenario(self) -> None:
        """S8 is detected on strength of signal, and always was.

        Its size-distribution mixture moves a diagnostic far enough that the
        per-experiment tail probability is about 0.003, which survived even the
        Sidak correction that 0.013 did not. It is the one scenario whose
        detection does not depend on how experiments are combined, which is why
        it is kept as a control on the two tests above.
        """
        run = _runs()["B1", "S8"]
        assert run.ppc.inadequate
        per_experiment = run.ppc.per_experiment
        assert min(per_experiment[k] for k in sorted(per_experiment)) < 0.01
