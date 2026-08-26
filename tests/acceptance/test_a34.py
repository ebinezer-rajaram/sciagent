"""Acceptance test A34: comparator parity, and the predictions channel sealed.

A34 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Comparator parity: selection policy is confounded
with proposal source"*, and reads:

    ``test_a34_selection_parity_or_bounded`` -- either B4/B5 route selection
    through ``boed.plan`` identically to V7 on a constructed scenario, or the
    contrast declaration records the bounding comparison; and a self-serving
    explicit prediction that contradicts the structure's table row is refused or
    marked agent-authored.

The same entry's **Idea** is what the gate is a check on: *"Give B4 and B5 the
same BOED selection V7 uses (or, if rotation is kept deliberately, preregister
that V1-vs-B4 bounds the selection effect in the contrast analysis and state the
three protocol deltas where the comparison is defined). Close the open sealing
channel while in the file: ``Investigation.propose(predictions=...)`` currently
accepts predictions validated for falsifiability only -- require table-fidelity
or record authorship so the verifier can discount agent-authored conditions."*

What was wrong
--------------

SPEC §9's preregistered primary contrast asks whether V7 exceeds B4 on S11's D3.
V7 spends its whole budget through :func:`sciagent.experiments.boed.plan`, and so
does V1. B4 and B5 each carried a private ``_rotate`` -- the scenario's design
order, repeating, with no selection at all. "Equal budget" therefore held for the
experiment *count* and not for its informativeness, so a V7 win partly credited
conventional BOED to the LLM arm, in a contrast designated to test **proposal
source**.

B1's rotation is a different thing and stays. It is documented as deliberate:
a selection policy would confound the floor B1 exists to establish.

Separately, :meth:`~sciagent.systems.base.Investigation.propose` accepted
caller-supplied :class:`~sciagent.core.types.Prediction` objects, had the graph
validate them for falsifiability alone, and stored them verbatim. Since A30 gave
the verifier a production caller those conditions are read by
:func:`~sciagent.verify.statistical.prediction_evidence` on every adjudicated
cell -- so a system supplying its own predictions set the threshold that graded
its own claim, and moved SPEC §12 criterion 10's adjudication rate.

Why the first clause is answered on *both* branches of its "or"
---------------------------------------------------------------

Full parity is not reachable, and the obstruction is structural rather than a
policy choice worth arguing about.

V7 entertains its whole library **before** its first selection, so its first half
plans against a belief with something to discriminate between. B4 must observe
before it can retrieve -- its key is a residual signature over what was seen --
and B5 before it can score predictive fit. When their first half runs the graph
holds only the null, and with a one-hypothesis belief every design's expected
information gain is exactly zero, with :func:`sciagent.experiments.boed.rank`
breaking the resulting all-way tie by ascending template id. Routing that half
through ``boed.plan`` would repeat one design for every step, collapsing B4's
retrieval key from a vector over every design run to a single design's residual
and scoring B5's beam on one design. It would gut the comparator this project
deliberately built strong, which is the opposite of what the entry asks for.

So the half that *can* be selected is selected, and the half that cannot is
declared: ``test_a34_the_pre_proposal_asymmetry_is_declared`` holds the
declaration to naming what remains, so the residual is bounded and stated rather
than silent.

What the tests establish
------------------------

``..._b4_selects_its_post_proposal_half_by_information_gain`` and its B5 sibling
are the first clause's parity branch, asserted against *behaviour*: the design
sequence each arm actually ran is compared against a replay that spends the same
budget through ``boed.plan``, and separately against the rotation it used to run.
Both comparisons are needed. Agreement with the replay alone would also hold if
``boed.plan`` had somehow reproduced the rotation, and disagreement with the
rotation alone would hold for any change at all.

``..._the_pre_proposal_asymmetry_is_declared`` is the first clause's declaration
branch, and ``..._a_declaration_note_is_not_an_identity`` guards the way that
branch could be got wrong -- a note folded into
:meth:`~sciagent.eval.report.Preregistration.describes` would make every contrast
recorded before it was written report ``preregistered=False``.

``..._a_self_serving_prediction_is_marked_agent_authored`` and
``..._an_agent_authored_confirmation_cannot_carry_a_claim`` are the second
clause: the framework stamps authorship rather than believing it, and the stamp
changes what the verifier will let a claim rest on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from functools import lru_cache

import pytest
from claim_world import METRIC, claim, index, record
from slice_tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    gate_table,
    search_table,
)

from environments.pointproc import edit_grammar, reference_program
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.grammar import agent_grammar
from environments.pointproc.matrix import SPEC9_CONTRAST
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.conditions import Between, Not
from sciagent.core.edits import Defect
from sciagent.core.errors import MalformedClaimError
from sciagent.core.types import (
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    Prediction,
    PredictionId,
    Probability,
)
from sciagent.experiments import boed
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import (
    Investigation,
    null_seeded_graph,
    select_experiments,
    table_prediction,
)
from sciagent.systems.baselines.beam_search import BeamSearch, table_fit
from sciagent.systems.baselines.retrieval import Retrieval
from sciagent.verify import ClaimContext, Outcome
from sciagent.verify.statistical import PredictionEvidence
from sciagent.verify.statistical import check as statistical_check

#: The scenario the parity clause is constructed on. S1 is in-library and cheap,
#: and it is one of the nine where BOED does not spread its selection across the
#: design space -- so a selected half and a rotated half are far apart, which is
#: what makes the non-vacuity assertion below mean something.
SCENARIO = "S1"

#: The two arms the first clause is about. B1 is deliberately absent: its
#: rotation is documented as the thing that keeps its floor unconfounded.
ARMS = ("B4", "B5")


def _built(table: EmpiricalTable) -> tuple[Investigation, EmpiricalTableEngine]:
    """Return a fresh investigation on ``table``, and the engine behind it.

    Constructed directly rather than through
    :func:`~sciagent.eval.campaign.run_scenario` because this gate reads the
    investigation's own history and graph afterwards, which a scored run does not
    hand back.

    The engine comes back too because ``_replay`` needs the table an arm *grew*,
    and since gate A41 ``investigation.engine.table`` is the opaque projection a
    system sees -- correct for a lookup, and not something a second engine can be
    built over. The harness holds the engine it created; a system never does.
    """
    target = scenario(SCENARIO)
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    investigation = Investigation(
        scenario_id=target.id,
        designs=target.designs,
        truth=target.executed,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=target.budget
        ),
        engine=engine,
        graph=graph,
        seed=target.seed,
    )
    return investigation, engine


def _investigation(table: EmpiricalTable) -> Investigation:
    """Return a fresh investigation on ``table``, discarding the engine."""
    investigation, _ = _built(table)
    return investigation


def _system(arm: str) -> Retrieval | BeamSearch:
    """Return an instance of one arm. Each is built once and used once."""
    if arm == "B4":
        return Retrieval(closed_set())
    return BeamSearch(
        agent_grammar(),
        table_fit(search_table(), simulator(GRAMMAR)),
        width=3,
        levels=1,
    )


def _ran(investigation: Investigation) -> tuple[str, ...]:
    """Return the design ids an investigation ran, in order."""
    return tuple(str(result.design.id) for result in investigation.history)


def _rotation(total: int, half: int) -> tuple[str, ...]:
    """Return the design ids the arms' two rotation blocks would have run.

    Two restarting blocks, not one continuous cycle. Each arm called ``_rotate``
    twice and the step counter began at zero both times, so the second block
    started again at ``designs[0]`` rather than continuing from where the first
    left off. Modelling it as one cycle compares the selected half against a
    rotation nobody ever ran, and the non-vacuity guard below then passes against
    the very code this gate exists to reject.
    """
    designs = scenario(SCENARIO).designs

    def block(count: int) -> list[str]:
        return [str(designs[step % len(designs)].id) for step in range(count)]

    return tuple(block(half) + block(total - half))


def _proposed(investigation: Investigation) -> tuple[tuple[HypothesisId, Defect], ...]:
    """Return the structures an arm's run ended up holding.

    In ``HypothesisId``-repr order, **not** proposal order: ``graph.nodes`` is a
    :class:`~sciagent.core.types.FrozenDict`, which re-sorts by ``repr`` in its
    constructor so that iteration never depends on insertion. Which order this is
    does not matter to the replay, and that is the point -- a node's plausibility
    is derived from its own ``program_edit`` and normalised over sorted keys, so
    the belief the replay reaches is the belief the arm reached whatever order the
    proposals are replayed in.
    """
    held: list[tuple[HypothesisId, Defect]] = []
    for node_id in investigation.graph.nodes:
        if str(node_id) == "null":
            continue
        # ``program_edit`` is ``Defect | None`` because a node exists before it is
        # compiled. Anything an arm proposed is compiled, so this holds -- and if
        # it ever did not, the replay would be spending its budget against a
        # different belief than the arm did, which is worth failing on.
        program_edit = investigation.graph.node(node_id).program_edit
        assert program_edit is not None, node_id
        held.append((node_id, program_edit))
    return tuple(held)


def _replay(
    table: EmpiricalTable, proposed: Sequence[tuple[HypothesisId, Defect]]
) -> tuple[str, ...]:
    """Return what the same budget spends when its second half is ``boed.plan``.

    The first half is the rotation, unchanged, because that is the half the arms
    keep. ``proposed`` is what the arm's own run ended up holding, so the replay
    neither reimplements retrieval and beam search nor re-runs them -- it only has
    to reach the same belief the arm reached, and the arm has already said what
    that is.

    Not re-running them is what keeps this gate off every future suite run's
    critical path. Measured: three investigations an arm rather than one cost the
    whole suite 272.73s against a 151.30s baseline, and B5's beam re-attempts
    every unmeasurable corner of the candidate set per instance, because
    :func:`~sciagent.systems.baselines.beam_search.table_fit` scores those
    ``-inf`` without caching the failure.
    """
    investigation = _investigation(table)
    total = int(investigation.budget.remaining)
    designs = investigation.designs
    for step in range((total + 1) // 2):
        investigation.run(designs[step % len(designs)])
    for node_id, program_edit in proposed:
        investigation.propose(node_id, program_edit=program_edit, rationale="replay")

    by_id = {design.id: design for design in investigation.designs}

    def observe(_index: int, template: ExperimentTemplateId) -> int:
        design = by_id[template]
        result = investigation.run(design)
        return design.template().outcome.cell_of(result.result)

    boed.plan(
        investigation.engine,
        tuple(by_id),
        observe,
        steps=int(investigation.budget.remaining),
    )
    return _ran(investigation)


@lru_cache(maxsize=1)
def _sequences() -> dict[str, tuple[tuple[str, ...], tuple[str, ...], int]]:
    """Return ``(what the arm ran, what the replay ran, the first half's length)``.

    Each arm runs **once**, and the table is threaded from run to run so that a
    structure an arm proposes is simulated once here rather than once per
    comparison.

    **It is deliberately not saved back.** ``save_gate_table`` writes the shared
    on-disk table, and `tests/test_matrix_runner.py`'s
    ``test_a_structure_simulated_once_is_not_simulated_again`` asserts at its last
    line that that file did *not* grow while it ran -- an assertion any concurrent
    writer breaks under ``-n 4``, since modules land on different workers. This
    gate needs no write: `tests/baseline_runs.py` and
    `tests/test_baselines_slice.py` both run B5 on every slice scenario including
    S1 and save, so every row this would persist is already persisted by them,
    and the only thing writing here buys is one more racer.

    The replay starts from the table the arm's run *ended* with rather than the
    one it began with, and that is deliberate rather than sloppy. A row is a pure
    function of ``(defect, template, seed)``, so a row filled mid-run and the same
    row present from the start carry identical numbers; and every structure a plan
    reads has a row by the time it plans, because
    :meth:`~sciagent.systems.base.Investigation.propose` calls
    ``ensure_structure`` before the graph moves. The two ``boed.plan`` calls
    therefore see the same table, which is the only property this comparison
    needs -- and buying it with a third run an arm was measured costing the whole
    suite two minutes.
    """
    table: EmpiricalTable = gate_table()
    built: dict[str, tuple[tuple[str, ...], tuple[str, ...], int]] = {}
    for arm in ARMS:
        investigation, engine = _built(table)
        total = int(investigation.budget.remaining)
        _system(arm).investigate(investigation)
        table = engine.table
        built[arm] = (
            _ran(investigation),
            _replay(table, _proposed(investigation)),
            (total + 1) // 2,
        )
    return built


def _graph_with(*, authored: bool) -> HypothesisGraph:
    """Return a graph over the closed set whose predictions carry ``authored``.

    Identical to ``claim_world.graph_of`` in every other respect, so the pair of
    contexts the discount test compares differ in the flag and in nothing else.
    """
    metrics = metric_registry()
    spec = metrics.spec(str(METRIC))
    condition = Between(low=spec.low, high=2.0, low_closed=True, high_closed=False)
    built = HypothesisGraph.empty(edit_grammar(), metrics)
    structures = closed_set()
    for name in sorted(structures):
        node_id = HypothesisId(name)
        built = built.propose(
            node_id,
            program_edit=structures[name],
            predictions=[
                Prediction(
                    id=PredictionId(f"{name}/{METRIC}"),
                    hypothesis_id=node_id,
                    diagnostic=spec.ref,
                    condition=condition,
                    under=ExperimentTemplateId("query:inter_arrival_dispersion"),
                    refutation=Not(condition),
                    authored=authored,
                )
            ],
            rationale=f"structure {name!r}",
        )
    return built


def _context(graph: HypothesisGraph, *, value: float = 1.0) -> ClaimContext:
    """Return a context over one experiment and ``graph``.

    ``value`` decides which way that experiment fell: the default lands inside
    the condition `_graph_with` states, and a value outside it lands in the
    complement, which is the refutation.
    """
    return ClaimContext(
        graph=graph,
        evidence=index(
            record("e", sequence=1, value=value), record("f", sequence=2, value=value)
        ),
        program=reference_program(),
        accepted=(),
        posterior=FrozenDict[HypothesisId, Probability]({}),
    )


class TestA34SelectionParityOrBounded:
    """The comparator's evidence is selected the way the treatment's is."""

    def test_a34_b4_selects_its_post_proposal_half_by_information_gain(self) -> None:
        """B4's spend after retrieval is ``boed.plan``'s, not the design order."""
        ran, replayed, half = _sequences()["B4"]
        assert ran == replayed
        assert ran[half:] != _rotation(len(ran), half)[half:]

    def test_a34_b5_selects_its_post_proposal_half_by_information_gain(self) -> None:
        """B5's spend after its proposal is ``boed.plan``'s, not the design order."""
        ran, replayed, half = _sequences()["B5"]
        assert ran == replayed
        assert ran[half:] != _rotation(len(ran), half)[half:]

    def test_a34_the_pre_proposal_half_is_still_the_rotation(self) -> None:
        """The half that cannot be selected is unchanged, in both arms.

        Not incidental. It is the reason the declaration below has to exist, and
        a change that quietly BOED-planned this half as well would collapse B4's
        retrieval key onto one design while this gate went on passing.
        """
        for arm in ARMS:
            ran, _, half = _sequences()[arm]
            assert ran[:half] == _rotation(len(ran), half)[:half], arm

    def test_a34_selection_on_a_null_only_belief_repeats_one_design(self) -> None:
        """The degeneracy the whole "both branches" argument rests on.

        With one hypothesis in the belief every design's expected information
        gain is exactly zero, so :func:`~sciagent.experiments.boed.rank` decides
        the whole plan by its tiebreak -- ascending template id -- and returns
        the same design every step. Pinned rather than asserted in prose, because
        it is the reason B4's and B5's pre-proposal half stays a rotation and the
        reason each falls back to one when its proposal step admits nothing. If
        this ever stopped being true, both of those would be dead weight and this
        gate should say so.
        """
        investigation = _investigation(gate_table())
        assert len(investigation.engine.live) == 1
        select_experiments(investigation, 4)
        chosen = _ran(investigation)
        assert len(chosen) == 4
        assert len(set(chosen)) == 1

    def test_a34_the_pre_proposal_asymmetry_is_declared(self) -> None:
        """The declaration names the bounding comparison and every delta left.

        The **Idea** asks for the bounding comparison *and* "the three protocol
        deltas where the comparison is defined", so a declaration is held to
        naming all three rather than to being non-empty. Three survive the parity
        change, and each is checked by a word only that delta would use:

        * the pre-proposal half, which cannot be selected because **retrieval**
          has not run yet and the belief holds only the null;
        * the relevance declaration -- V7 names the live hypotheses an experiment
          **targets** and B4/B5 name none, which is SPEC §7.1's input and not
          selection, so parity does not reach it;
        * B4's **library**, which excludes S11's truth by construction, so the
          comparator cannot retrieve the answer on the one scenario the
          designation exists for.
        """
        stated = " ".join(SPEC9_CONTRAST.residual_asymmetries).lower()
        assert len(SPEC9_CONTRAST.residual_asymmetries) >= 3
        assert "v1" in stated and "b4" in stated
        for delta in ("retriev", "target", "librar"):
            assert delta in stated, delta

    def test_a34_a_declaration_note_is_not_an_identity(self) -> None:
        """Recording a residual may not change which contrast was declared.

        ``describes`` compares the five identifying fields. Folding the note into
        it would report every contrast as un-preregistered the moment a residual
        was written down, which is the opposite of what recording it is for.
        """
        for declaration in (
            SPEC9_CONTRAST,
            replace(SPEC9_CONTRAST, residual_asymmetries=()),
        ):
            assert declaration.describes(
                scenario=SPEC9_CONTRAST.scenario,
                treatment=SPEC9_CONTRAST.treatment,
                comparator=SPEC9_CONTRAST.comparator,
                dimension=SPEC9_CONTRAST.dimension,
                conditional_on_inadequacy=SPEC9_CONTRAST.conditional_on_inadequacy,
            )

    def test_a34_a_self_serving_prediction_is_marked_agent_authored(self) -> None:
        """An explicit prediction is stamped by the framework, not believed.

        The condition is the complement of the one the structure's own table row
        implies, which is the gate's *"contradicts the structure's table row"*,
        and it arrives claiming ``authored=False`` -- the forgery the stamp has
        to survive, since a caller who could set the flag could clear it.
        """
        investigation = _investigation(gate_table())
        program_edit = closed_set()["hawkes"]
        design = next(
            design
            for design in investigation.designs
            if design.template().outcome.dimension == 1
        )
        node_id = HypothesisId("self-serving")
        honest = table_prediction(
            investigation.engine.table, node_id, program_edit, design
        )
        investigation.propose(
            node_id,
            program_edit=program_edit,
            predictions=[
                replace(
                    honest,
                    condition=Not(honest.condition),
                    refutation=honest.condition,
                    authored=False,
                )
            ],
            rationale="a threshold of the system's own choosing",
        )
        assert investigation.graph.predictions[honest.id].authored is True

    def test_a34_a_widened_prediction_is_marked_agent_authored_too(self) -> None:
        """Widening is the self-serving move, and it is stamped as well.

        The **Gate** sentence names a condition that *contradicts* the table row,
        which is the weaker attack: contradicting the modal cell makes a claim
        harder to confirm, while a condition strictly *containing* it is
        confirmed by everything the honest one is and by much else besides. A
        stamp that fired only on contradiction -- a literal reading of the Gate,
        and therefore a realistic thing to write -- would leave this one
        unmarked, and it is exactly the channel the **Idea** says to close.

        So authorship is recorded for every explicitly supplied prediction,
        rather than for the ones the framework can see are wrong.
        """
        investigation = _investigation(gate_table())
        program_edit = closed_set()["hawkes"]
        design = next(
            design
            for design in investigation.designs
            if design.template().outcome.dimension == 1
        )
        node_id = HypothesisId("widened")
        honest = table_prediction(
            investigation.engine.table, node_id, program_edit, design
        )
        row = honest.condition
        assert isinstance(row, Between)
        spec = METRICS.spec(str(honest.diagnostic.name))
        widened = Between(
            low=spec.low,
            high=(row.high + spec.high) / 2.0,
            low_closed=True,
            high_closed=False,
        )
        # Strictly containing, so nothing here is a contradiction the framework
        # could have caught by comparing against the row.
        assert widened.low <= row.low and widened.high > row.high

        investigation.propose(
            node_id,
            program_edit=program_edit,
            predictions=[
                replace(
                    honest,
                    condition=widened,
                    refutation=Not(widened),
                    authored=False,
                )
            ],
            rationale="a bar wide enough to clear",
        )
        assert investigation.graph.predictions[honest.id].authored is True

    def test_a34_a_framework_derived_prediction_is_not_marked(self) -> None:
        """The stamp distinguishes; it does not condemn every prediction.

        A stamp that fired on the ``predictions=None`` path would mark every
        prediction in the recorded campaign agent-authored and make the discount
        below universal, which is a different bug wearing this gate's clothes.
        """
        investigation = _investigation(gate_table())
        node_id = HypothesisId("framework-derived")
        investigation.propose(
            node_id,
            program_edit=closed_set()["hawkes"],
            rationale="the framework's own thresholds",
        )
        derived = investigation.graph.node(node_id).predictions
        assert derived
        assert all(
            investigation.graph.predictions[one].authored is False for one in derived
        )

    def test_a34_an_agent_authored_refutation_cannot_carry_a_claim(self) -> None:
        """The symmetric case, and the one a first cut of this gate missed.

        A ``refutes`` claim asserts *against* its subject, so clearing a
        self-authored refutation bar is the favourable outcome, not self-harm: a
        system can propose a rival structure with a wide authored refutation and
        have "refutes" accepted on a threshold it set. ``claims_from_run`` emits
        a claim per hypothesis carrying mass, so the path is live on every scored
        cell the moment a system authors anything.
        """
        refutes = claim(strength="refutes", evidence=["f"])
        honest = _context(_graph_with(authored=False), value=9.0)
        assert statistical_check(refutes, honest) == ()

        findings = statistical_check(
            refutes, _context(_graph_with(authored=True), value=9.0)
        )
        assert findings
        assert findings[0].outcome is Outcome.REFER

    def test_a34_a_tally_claiming_more_authored_than_outcomes_is_refused(self) -> None:
        """The subset relation is asserted, not trusted to a docstring.

        `_from_predictions` decides the discount by `confirmed ==
        authored_confirmed`. If `authored_confirmed` could exceed `confirmed`
        that equality would silently stop holding and the discount would vanish
        with nothing raising — so the tally refuses to be built that way.
        """
        with pytest.raises(MalformedClaimError, match="more agent-authored"):
            PredictionEvidence(
                evaluated=1, confirmed=1, refuted=0, authored_confirmed=2
            )
        with pytest.raises(MalformedClaimError, match="more outcomes than"):
            PredictionEvidence(evaluated=1, confirmed=2, refuted=0)
        with pytest.raises(MalformedClaimError, match="negative count"):
            PredictionEvidence(evaluated=-1, confirmed=0, refuted=0)

    def test_a34_an_agent_authored_confirmation_cannot_carry_a_claim(self) -> None:
        """The stamp is what lets the verifier discount a self-set threshold.

        The two contexts differ in the flag and in nothing else -- same
        structures, same condition, same confirming experiment -- so the change
        in verdict is attributable to authorship and to nothing about the
        evidence.
        """
        supports = claim(strength="supports", evidence=["e"])
        assert statistical_check(supports, _context(_graph_with(authored=False))) == ()

        findings = statistical_check(supports, _context(_graph_with(authored=True)))
        assert findings
        assert findings[0].outcome is Outcome.REFER
