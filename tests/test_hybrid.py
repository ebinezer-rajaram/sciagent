"""V7 (SPEC §5), driven by a scripted provider.

What is asserted here is the *architecture*: that V7 entertains the same library
as V1, selects the same way, proposes only when the conventional check says the
hypothesis space is inadequate, and reports what happened when a proposal could
not be made. What is deliberately **not** asserted is how well it does. SPEC §12
says beating B4 or B5 is the research question rather than an exit criterion, and
the answer here would in any case be about the scripted policy rather than about
a model.

The measurement of what a model proposes needs a recorded transcript corpus and
is item 12's open item; ``docs/DECISIONS.md`` records why it is not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

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
from environments.pointproc.scenarios import scenario, slice_scenarios
from sciagent.core.errors import TranscriptMissError
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import ResearchSystem, null_seeded_graph
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.hybrid import Hybrid
from sciagent.systems.llm import (
    RECORD,
    REPLAY,
    Completion,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
)

#: A payload per proposal V7 is allowed to make. Structure 3 is the mark-component
#: mixture and structure 1 the latent regime, so the scripted policy proposes
#: something real rather than repeating one cell.
SCRIPT: tuple[Mapping[str, Any], ...] = (
    fixed_payload(3, (20, 30, 40), name="size_mixture"),
    fixed_payload(1, (10, 20, 30, 40), name="latent_regime"),
)


def _layer(
    script: Sequence[Mapping[str, Any]] = SCRIPT,
    store: TranscriptStore | None = None,
) -> ProposalLayer:
    return ProposalLayer(
        ScriptedProvider(list(script)),
        AGENT_GRAMMAR,
        store if store is not None else TranscriptStore(mode=RECORD),
    )


#: The table every run in this module reads and grows. Held at module scope and
#: persisted, exactly as the item 9 gate does it: a structure V7 proposes has no
#: row in the calibrated table, and filling one costs 2000 replicates. Threading
#: the grown table through every run means each distinct proposal is simulated
#: once per machine rather than once per test, which changes what this module
#: costs and not what it concludes -- a row is a pure function of
#: ``(defect, template, seed)``.
_TABLE: list[EmpiricalTable] = []


def _table() -> EmpiricalTable:
    """Return the shared table, loading it once."""
    if not _TABLE:
        _TABLE.append(gate_table())
    return _TABLE[0]


def _run(
    scenario_id: str,
    layer: ProposalLayer | None = None,
    *,
    system: ResearchSystem | None = None,
) -> ScenarioRun:
    """Run one system on one scenario, growing the shared table."""
    the_scenario = scenario(scenario_id)
    table = _table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    run = run_scenario(
        the_scenario,
        system if system is not None else Hybrid(closed_set(), layer or _layer()),
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=engine,
        graph=graph,
    )
    _TABLE[0] = engine.table
    return run


@lru_cache(maxsize=1)
def _slice_runs() -> dict[str, ScenarioRun]:
    """Run V7 on every slice scenario once. Cached; every test below reads it.

    Saved at the end so a later session starts warm.
    """
    runs = {str(s.id): _run(str(s.id)) for s in slice_scenarios()}
    save_gate_table(_table())
    return runs


class TestTheArchitecture:
    """V7 is V1 plus a gated proposal step, and nothing else."""

    def test_it_completes_every_scenario(self) -> None:
        assert set(_slice_runs()) == {str(s.id) for s in slice_scenarios()}

    @pytest.mark.parametrize("scenario_id", [str(s.id) for s in slice_scenarios()])
    def test_it_never_exceeds_its_budget(self, scenario_id: str) -> None:
        run = _slice_runs()[scenario_id]
        assert run.experiments <= scenario(scenario_id).budget.total

    def test_it_entertains_the_whole_library(self) -> None:
        """The same starting set as V1, which is what makes the two comparable."""
        proposed = _slice_runs()["S1"].proposed
        assert {"hawkes", "poisson_mixture", "regime_switching", "seasonality"} <= set(
            proposed
        )

    def test_it_matches_v1_where_the_check_never_fires(self) -> None:
        """On a scenario whose truth is in the library, V7 *is* V1.

        Not an approximation: same library, same one-step-greedy selection, no
        proposal, so the two run identical trajectories and reach identical
        posteriors. This is the property that makes SPEC §9's contrast clean --
        where V7 and V1 differ, the proposal step is the only thing that can have
        caused it.
        """
        hybrid = _slice_runs()["S1"]
        conventional = _run("S1", system=BOEDOnly(closed_set()))
        assert not hybrid.ppc.inadequate
        assert hybrid.diagnosis.distribution == conventional.diagnosis.distribution
        assert hybrid.experiments == conventional.experiments

    def test_it_declares_experiment_targets(self) -> None:
        """What no SPEC §5 baseline does, and item 10 said V7 should.

        Without it, §7.1's clauses 1 and 6 never fire on a real slice run and
        evidence relevance rests on clauses 2 to 5 alone.
        """
        run = _slice_runs()["S1"]
        records = run.evidence.ordered()
        assert records, "the run registered no experiment"
        assert all(record.targets for record in records), (
            "V7 ran experiments without declaring what they were aimed at"
        )


class TestProposalIsGatedOnDetection:
    """SPEC F6: Stage B is conditional on Stage A, in behaviour as well as report."""

    def test_no_proposal_is_made_where_the_check_passes(self) -> None:
        layer = _layer()
        run = _run("S1", layer)
        assert not run.ppc.inadequate
        assert layer.calls == 0, (
            "V7 proposed on a scenario the conventional check found adequate, "
            "which would report Stage B on a run where Stage A never fired"
        )

    def test_the_proposal_layer_is_asked_where_the_check_fires(self) -> None:
        """S11 is out of library, so Stage A fires and extension is licensed.

        Asserted on the scenario rather than on a constructed case because what
        matters is that the gate opens on real evidence.

        This was S12 until 2026-08-16, and the move is the point rather than an
        adjustment. S12's truth is *in* the closed set -- regime switching, with
        censoring as a nuisance -- so the old gate opening there was the nuisance
        fooling a check that had no reading bearing on adequacy. Once Stage A
        reads its own probe, S12 correctly falls quiet at 0.5083 and S11, the one
        scenario whose mechanism is genuinely outside the library, fires at
        0.0112. See ``docs/DECISIONS.md``.
        """
        layer = _layer()
        _run("S11", layer)
        assert layer.calls > 0

    def test_a_refusing_provider_leaves_a_usable_investigation(self) -> None:
        """A refusal is an outcome, not a crash.

        The budget is still there and a diagnosis is still owed, so V7 records
        the refusal and carries on. The scripted provider is given an empty
        script, which is how it refuses.
        """
        run = _run("S11", _layer(script=()))
        assert run.experiments > 0
        total = sum(
            float(run.diagnosis.distribution[h]) for h in run.diagnosis.distribution
        )
        assert pytest.approx(total, abs=1e-9) == 1.0

    def test_a_malformed_draft_leaves_a_usable_investigation(self) -> None:
        """The same, for a payload that does not decode."""
        broken = dict(fixed_payload(0, ()))
        broken["edits"] = [{"structure": 999, "parameters": []}]
        run = _run("S11", _layer(script=(broken,)))
        assert run.experiments > 0

    def test_an_unmeasurable_proposal_leaves_a_usable_investigation(self) -> None:
        """A structure that executes but cannot be measured costs a proposal.

        The third way a proposal can fail, and the one V7 had no outcome for.
        ``StructureNotMeasurableError`` is raised when a candidate runs but some
        design in the table's set yields no row on it -- here a latent regime
        whose logs leave a phase bin of ``query:phase_conditioned_dispersion``
        holding one window. Only an arm that proposes structure outside the
        library can reach it, which is why 38 conventional cells never did.

        ``BeamSearch`` has held this exact guard since it existed
        (``_UNSCORABLE``, ``beam_search.py``): an unscorable candidate costs B5
        a rank, not the search. V7 instead let it escape ``investigate``, which
        stopped the first LLM cells of item 15's matrix at V7/S2 replicate 07.

        The parameters are the ones that stopped that run, recovered from the
        error by inverting the grids: structure 1 at indices
        ``(32, 55, 29, 22)`` is ``mult_low=0.1459``, ``mult_high=30.42``,
        ``switch_rate=0.0504``, ``p_high=0.3552``. See ``docs/DECISIONS.md``.
        """
        payload = fixed_payload(1, (32, 55, 29, 22), name="unmeasurable_regime")
        system = Hybrid(closed_set(), _layer(script=(payload,)))
        run = _run("S11", system=system)
        assert run.experiments > 0
        total = sum(
            float(run.diagnosis.distribution[h]) for h in run.diagnosis.distribution
        )
        assert pytest.approx(total, abs=1e-9) == 1.0

        # The tier, and the fact the loop did not stop on it. Asserting only
        # that the run survived would pass just as well if this were filed under
        # "refused", which breaks `_extend` at the first attempt -- so the
        # surviving-run assertion above cannot distinguish the fix from the bug
        # it replaces. The second attempt is "refused" because a one-payload
        # script is exhausted by then, which is what shows the loop continued.
        assert tuple(a.outcome for a in system.attempts) == (
            "unmeasurable",
            "refused",
        )

    def test_two_edits_on_one_target_leave_a_usable_investigation(self) -> None:
        """A draft can be licensed edit by edit and invalid as a defect.

        ``EditGrammar.validate_defect`` refuses two edits on one target -- the
        compiled family would be ambiguous -- and raises ``InvalidEditError``,
        which is a ``GrammarError``. ``ProposalLayer.propose`` documents itself
        as raising ``MalformedProposalError`` "when the payload does not denote
        a structure the grammar licenses", and this is that case, so the layer
        was not honouring its own contract: ``_propose_once`` catches
        ``MalformedProposalError`` and the ``InvalidEditError`` went past it.

        Structures 0 and 1 both target ``arrival`` (``AddDependency`` and
        ``AddLatentVariable``), which is the pair that stopped V3/S11 replicate
        09 of item 15's matrix. See ``docs/DECISIONS.md``.
        """
        clashing = dict(fixed_payload(0, (10, 20, 30)))
        clashing["edits"] = [
            {"structure": 0, "parameters": [10, 20, 30]},
            {"structure": 1, "parameters": [10, 20, 30, 40]},
        ]
        system = Hybrid(closed_set(), _layer(script=(clashing,)))
        run = _run("S11", system=system)
        assert run.experiments > 0
        # "malformed", not "refused": the draft is at fault, not the provider,
        # and the two tiers are what `ProposalRecord` reports separately.
        assert tuple(a.outcome for a in system.attempts) == ("malformed", "refused")

    def test_a_missing_transcript_is_not_swallowed(self) -> None:
        """A broken replay is a configuration fault, and must not degrade quietly.

        ``TranscriptMissError`` is a sibling of the two errors V7 does catch,
        precisely so that catching those cannot catch this one. A harness asked
        to replay a call nobody recorded should stop, not produce a slightly
        worse result nobody can account for.
        """
        empty = ProposalLayer(
            ScriptedProvider(list(SCRIPT)),
            AGENT_GRAMMAR,
            TranscriptStore(mode=REPLAY),
        )
        with pytest.raises(TranscriptMissError):
            _run("S11", empty)


class TestDeterminism:
    """Two runs of V7 on one scenario agree exactly (SPEC §1, invariant 3)."""

    def test_two_runs_agree_exactly(self) -> None:
        first, second = _run("S11"), _run("S11")
        assert first.diagnosis.distribution == second.diagnosis.distribution
        assert first.experiments == second.experiments
        assert first.ppc.p_value == second.ppc.p_value

    def test_a_recorded_run_replays_without_the_provider(self) -> None:
        """The reproducibility claim, end to end on a real scenario.

        The replay is handed a provider that raises if it is called, so a run
        that reached for the backend fails loudly rather than quietly agreeing
        for the wrong reason. ``misses == 0`` is the assertion that it replayed
        rather than re-derived.
        """
        recording = _layer(store=TranscriptStore(mode=RECORD))
        original = _run("S11", recording)
        assert len(recording.store) > 0

        class Refuses:
            id = "scripted"
            model = "scripted/1"
            settings = ""

            def complete(
                self, system: str, brief: str, schema: Mapping[str, Any]
            ) -> Completion:
                raise AssertionError("a replay must not call the provider")

        store = TranscriptStore({t.address: t for t in recording.store}, mode=REPLAY)
        replayed = _run("S11", ProposalLayer(Refuses(), AGENT_GRAMMAR, store))
        assert store.misses == 0
        assert replayed.diagnosis.distribution == original.diagnosis.distribution
        assert replayed.proposed == original.proposed
