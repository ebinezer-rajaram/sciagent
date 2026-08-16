"""The V3/V4 memory ablation (SPEC §11 item 13), and what R2 can be asked of it.

SPEC §5 line 282 names the pair "LLM with raw history versus LLM with hypothesis
graph", and R2 (§2) asks whether the second beats the first **at equal
information**. In this architecture the only thing an LLM ever sees is
:func:`~sciagent.systems.llm.encoding.render_brief`, so the ablation is a
restriction of that one function and nothing else: same library, same
one-step-greedy selection, same framework-computed posterior.

Not named ``test_aN_``: item 13 carries no A-gate. ``scripts/status.py`` derives
no coverage from this module, which is correct -- what is asserted here is a
*contract between two arms*, not an acceptance criterion. The contract has three
parts and they are the reason each test below exists.

**V7's brief is frozen.** It is hashed into every transcript address, so a change
to the default rendering invalidates item 12's recorded corpus. ``Memory.BOTH``
is the default and must reproduce today's string exactly.

**The two arms swap representations rather than nest them.** V3 sees the
per-step readings and the entertained structures with no posterior; V4 sees the
entertained structures with their posterior and no readings. Neither brief's
content is a superset of the other's -- which is what makes a measured delta
attributable to representation, and is the literal reading of "at equal
information". ``docs/DECISIONS.md`` records why the additive alternative was
rejected.

**The delta is only defined where Stage A fires.** SPEC F6 makes extension
conditional on detection, so on a scenario whose posterior predictive check
passes, neither arm is ever asked for a proposal and the two are the same system.
:class:`TestWhereTheDeltaIsDefined` measures that rather than assuming it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import SystemConfigurationError
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.ablation import ABLATION_SYSTEM_PROMPT, memory_ablation
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.hybrid import Hybrid
from sciagent.systems.llm import (
    RECORD,
    Memory,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
    call_address,
    fixed_payload,
    render_brief,
    structural_menu,
    tool_schema,
)
from sciagent.systems.llm.provider import DEFAULT_SYSTEM_PROMPT

#: SPEC §9 line 399 scopes the ablation to these three and no others.
ABLATION_SCENARIOS = ("S8", "S11", "S12")

MENU = structural_menu(AGENT_GRAMMAR)
SCHEMA = tool_schema(MENU)

#: The same script ``tests/test_hybrid.py`` gives V7, so a difference between the
#: arms cannot come from a difference in what the provider says back.
SCRIPT: tuple[Mapping[str, Any], ...] = (
    fixed_payload(3, (20, 30, 40), name="size_mixture"),
    fixed_payload(1, (10, 20, 30, 40), name="latent_regime"),
)

#: Section headings ``render_brief`` emits. Named here so a test asserts on the
#: brief's structure rather than on a substring that could match prose.
MENU_HEAD = "## Structures you may propose"
DESIGNS_HEAD = "## Experiments available"
OBSERVED_HEAD = "## What has been observed"
GRAPH_HEAD = "## Hypotheses already entertained"
STRUCTURES_HEAD = "## Structures already entertained"
CHECK_HEAD = "## Posterior predictive check"
BUDGET_HEAD = "## Budget"

#: The exact annotation ``_hypotheses_section`` appends to a structure. Searching
#: for this rather than for "posterior" keeps the graph arm's mass distinct from
#: the ``## Posterior predictive check`` heading, which is in every arm.
POSTERIOR_MARK = " -- posterior "

_TABLE: list[EmpiricalTable] = []


def _table() -> EmpiricalTable:
    """Return the shared calibrated table, loading it once."""
    if not _TABLE:
        _TABLE.append(gate_table())
    return _TABLE[0]


def _investigation(
    scenario_id: str = "S12",
    *,
    steps: int = 2,
    entertained: Sequence[str] = ("hawkes", "regime_switching"),
) -> Investigation:
    """Return an investigation part-way through, for a brief to be rendered from.

    Two structures are entertained and two experiments run, so both memory
    sections have something in them: a brief rendered from an untouched
    investigation would be equal between the arms for the wrong reason.
    """
    the_scenario = scenario(scenario_id)
    table = _table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    investigation = Investigation(
        scenario_id=the_scenario.id,
        designs=the_scenario.designs,
        truth=the_scenario.executed,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=engine,
        graph=graph,
        seed=the_scenario.seed,
    )
    if entertained:
        library = closed_set()
        entertain(investigation, {name: library[name] for name in entertained})
    for design in investigation.designs[:steps]:
        investigation.run(design)
    return investigation


def _sections(brief: str) -> dict[str, str]:
    """Return a brief split into its ``## `` blocks, keyed by heading."""
    blocks = brief.split("\n\n## ")
    out = {}
    for position, block in enumerate(blocks):
        body = block if position == 0 else f"## {block}"
        heading, _, rest = body.partition("\n")
        out[heading] = rest
    return out


def _run(scenario_id: str, system: Hybrid) -> ScenarioRun:
    """Run one system on one scenario, growing the shared table.

    The table is always the module's shared one. An earlier version took an
    override; it was never passed a value, and passing one would have skipped
    the ``_table()`` call that populates ``_TABLE``, so the write-back on the
    last line raised ``IndexError``. A parameter with no caller and one failure
    mode is worse than no parameter.
    """
    the_scenario = scenario(scenario_id)
    table = _table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    run = run_scenario(
        the_scenario,
        system,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=engine,
        graph=graph,
    )
    _TABLE[0] = engine.table
    return run


def _arms() -> tuple[Hybrid, Hybrid]:
    """Return a fresh (V3, V4) pair over the shared script."""
    return memory_ablation(
        closed_set(),
        lambda: ScriptedProvider(list(SCRIPT)),
        AGENT_GRAMMAR,
        TranscriptStore(mode=RECORD),
    )


@dataclass(frozen=True)
class Arm:
    """One arm's run, and whether its memory representation was ever consulted.

    ``asked`` is the observable the ablation turns on, and it is deliberately not
    ``run.ppc.inadequate``. :attr:`~sciagent.eval.campaign.ScenarioRun.ppc` is the
    check taken *after* the whole budget is spent; the gate that opens a proposal
    is the check taken at the half-budget point (``Hybrid.investigate``). The two
    disagree on real scenarios -- item 12 measured S12 at a final p of 0.1011 with
    the final check not firing, and two proposal calls all the same -- so reading
    the final check would report "no delta" on a run where the arms genuinely
    diverged.
    """

    run: ScenarioRun
    asked: int
    """How many times the proposal layer was asked, i.e. how many times this
    arm's brief was rendered. Zero means the memory representation never
    reached a model and the two arms are the same system."""

    addresses: tuple[str, ...]
    """The transcript address of each call, in order. Disjoint between the arms,
    because the brief they differ in is hashed into it."""


def _addresses(system: Hybrid) -> tuple[str, ...]:
    """Return the transcript address of each call the system's last run made.

    An attempt that never produced an address is one the provider refused before
    it was recorded, which the scripted backend does not do here; it is dropped
    rather than rendered as ``None`` so a collision test compares only real ones.
    """
    return tuple(
        attempt.address for attempt in system.attempts if attempt.address is not None
    )


@lru_cache(maxsize=1)
def _ablation_runs() -> dict[str, tuple[Arm, Arm]]:
    """Run both arms on S8, S11 and S12 once. Cached; the measurement reads it."""
    out = {}
    for scenario_id in ABLATION_SCENARIOS:
        v3, v4 = _arms()
        out[scenario_id] = (
            Arm(_run(scenario_id, v3), len(v3.attempts), _addresses(v3)),
            Arm(_run(scenario_id, v4), len(v4.attempts), _addresses(v4)),
        )
    save_gate_table(_table())
    return out


class TestTheDefaultPathIsUntouched:
    """The ablation's new parameter does not alter V7's rendering.

    Every transcript address is a hash over the brief, so a default that drifted
    would not fail loudly -- it would silently stop resolving item 12's recorded
    corpus and re-derive the twelve-scenario table against different text.

    **What this class does not establish**, and the distinction matters: that the
    default still matches the *pre-ablation* string. Every assertion here is
    within one process and one version, so if ``Memory.BOTH``'s own rendering
    were rewritten, all of them would still pass. Byte-identity against the
    previous commit was settled by rendering the same eight investigation states
    under a worktree at ``HEAD`` and diffing -- an independent check, recorded
    with its digest in ``docs/DECISIONS.md``, and not something a unit test in
    this repository can do for itself.

    What is guarded here is the part that *can* rot in-process: the default's
    section set and their order.
    """

    def test_the_default_is_both_memories(self) -> None:
        """A caller who passes nothing gets the same arm as one who asks for it."""
        investigation = _investigation()
        assert render_brief(investigation, MENU) == render_brief(
            investigation, MENU, memory=Memory.BOTH
        )

    def test_it_carries_exactly_the_six_sections_it_always_had(self) -> None:
        """No section added, none dropped, none reordered.

        The raw arm's ``Structures already entertained`` is the live hazard: it
        is new, and a refactor that let it reach the default would move every V7
        address at once.
        """
        assert list(_sections(render_brief(_investigation(), MENU))) == [
            MENU_HEAD,
            DESIGNS_HEAD,
            OBSERVED_HEAD,
            GRAPH_HEAD,
            CHECK_HEAD,
            BUDGET_HEAD,
        ]

    def test_the_raw_arms_section_is_unreachable_from_the_default(self) -> None:
        assert STRUCTURES_HEAD not in _sections(
            render_brief(_investigation(), MENU, memory=Memory.BOTH)
        )


class TestTheArmsSwapRepresentations:
    """Neither arm's brief is a superset of the other's."""

    def test_raw_carries_the_readings(self) -> None:
        sections = _sections(render_brief(_investigation(), MENU, memory=Memory.RAW))
        assert OBSERVED_HEAD in sections
        assert "step 0:" in sections[OBSERVED_HEAD]

    def test_raw_carries_no_posterior(self) -> None:
        """V3's memory is the record, not the belief derived from it.

        Asserted on :data:`POSTERIOR_MARK`, the exact annotation
        ``_hypotheses_section`` writes, rather than on the word "posterior". Both
        arms carry a ``## Posterior predictive check`` heading -- that is SPEC
        F5's conventional Stage A verdict and belongs in every arm -- so a bare
        word search would pass for the wrong reason and would start failing the
        day someone lower-cased the heading.
        """
        brief = render_brief(_investigation(), MENU, memory=Memory.RAW)
        assert GRAPH_HEAD not in _sections(brief)
        assert POSTERIOR_MARK not in brief

    def test_graph_carries_the_posterior(self) -> None:
        sections = _sections(render_brief(_investigation(), MENU, memory=Memory.GRAPH))
        assert GRAPH_HEAD in sections
        assert POSTERIOR_MARK in sections[GRAPH_HEAD]

    def test_the_check_section_is_in_both_arms(self) -> None:
        """Guards the reading above: "no posterior" never meant "no check"."""
        for memory in Memory:
            assert CHECK_HEAD in _sections(
                render_brief(_investigation(), MENU, memory=memory)
            )

    def test_graph_carries_no_readings(self) -> None:
        """V4's memory is the belief, not the record it was derived from."""
        assert OBSERVED_HEAD not in _sections(
            render_brief(_investigation(), MENU, memory=Memory.GRAPH)
        )

    def test_the_shared_sections_are_byte_identical(self) -> None:
        """Everything that is not memory is the same text in both arms.

        The menu and the designs are the action space, and the check is SPEC F5's
        conventional Stage A verdict, which both arms are entitled to. If any of
        them differed, a measured delta could be about that instead.
        """
        investigation = _investigation()
        raw = _sections(render_brief(investigation, MENU, memory=Memory.RAW))
        graph = _sections(render_brief(investigation, MENU, memory=Memory.GRAPH))
        for heading in (MENU_HEAD, DESIGNS_HEAD, CHECK_HEAD, BUDGET_HEAD):
            assert raw[heading] == graph[heading], heading

    def test_both_arms_see_the_same_hypotheses_in_the_same_order(self) -> None:
        """The equal-information claim, asserted rather than assumed.

        The two arms differ in how the entertained set is annotated -- with its
        posterior or without it -- and not in which structures it holds. A test
        is needed because the two sections are rendered by different code.
        """
        investigation = _investigation()
        raw = _sections(render_brief(investigation, MENU, memory=Memory.RAW))
        graph = _sections(render_brief(investigation, MENU, memory=Memory.GRAPH))
        named = [
            line.split(":", 1)[0].removeprefix("- ")
            for line in raw[STRUCTURES_HEAD].splitlines()
        ]
        annotated = [
            line.split(":", 1)[0].removeprefix("- ")
            for line in graph[GRAPH_HEAD].splitlines()
        ]
        assert named == annotated
        assert len(named) > 1, "the fixture entertained nothing to compare"

    def test_the_arms_render_the_same_structures(self) -> None:
        """Same structure text under each name, so only the annotation differs."""
        investigation = _investigation()
        raw = _sections(render_brief(investigation, MENU, memory=Memory.RAW))
        graph = _sections(render_brief(investigation, MENU, memory=Memory.GRAPH))
        for named, annotated in zip(
            raw[STRUCTURES_HEAD].splitlines(),
            graph[GRAPH_HEAD].splitlines(),
            strict=True,
        ):
            assert annotated.startswith(named), (named, annotated)

    def test_the_two_arms_address_differently(self) -> None:
        """A recorded corpus for one arm must never resolve a call from the other."""
        investigation = _investigation()
        addresses = {
            memory: call_address(
                settings="",
                provider="p",
                model="m",
                system="s",
                brief=render_brief(investigation, MENU, memory=memory),
                schema=SCHEMA,
                index=0,
            )
            for memory in Memory
        }
        assert len(set(addresses.values())) == len(Memory)


class TestTheAblationPair:
    """``memory_ablation`` builds two systems that differ in memory and nothing else."""

    def test_it_names_them_for_spec_5(self) -> None:
        v3, v4 = _arms()
        assert (v3.name, v4.name) == ("V3", "V4")

    def test_the_arms_never_share_a_transcript_address(self) -> None:
        """A corpus recorded for one arm must not resolve a call from the other.

        Behavioural rather than an identity check on the layers, because this is
        the consequence that would actually corrupt a result: the arms differ in
        their brief, the brief is hashed into the address, and so an address
        collision would mean one arm replaying the other's answer.
        """
        v3, v4 = _ablation_runs()["S12"]
        assert v3.addresses and v4.addresses, "the layer was never asked"
        assert not set(v3.addresses) & set(v4.addresses)

    def test_they_entertain_the_same_library(self) -> None:
        """Measured on a real run, on a scenario where neither arm proposes.

        S8's gate stays shut (see :class:`TestWhereTheDeltaIsDefined`), so what
        each arm entertained is exactly the library it was configured with, and
        the two sets can be compared without a proposal confusing them.
        """
        v3, v4 = _ablation_runs()["S8"]
        assert sorted(v3.run.proposed) == sorted(v4.run.proposed)
        assert {
            "hawkes",
            "poisson_mixture",
            "regime_switching",
            "seasonality",
        } <= set(v3.run.proposed)

    def test_the_prompt_presupposes_neither_memory(self) -> None:
        """The arms share a prompt, and it asks for nothing only one arm can give.

        ``DEFAULT_SYSTEM_PROMPT`` names both of the things the arms differ in --
        "the posterior over the hypotheses entertained so far" and "the
        observation that motivated the choice" -- so using it would tell V3 to
        reason from a posterior it cannot see and ask V4 to cite an observation
        it cannot see. That is an asymmetric handicap on the exact axis R2
        measures, and `ScriptedProvider` cannot detect it because it never reads
        the prompt. Guarded here so a later simplification back to the default
        fails loudly rather than quietly confounding the result.
        """
        assert ABLATION_SYSTEM_PROMPT != DEFAULT_SYSTEM_PROMPT
        for presupposition in (
            "the posterior over the hypotheses",
            "the observation that motivated",
            "what has been observed",
        ):
            assert presupposition not in ABLATION_SYSTEM_PROMPT, presupposition

    def test_a_system_cannot_be_built_without_a_name(self) -> None:
        """An unnamed arm would be scored under an empty label.

        A :class:`SystemConfigurationError` rather than a
        ``MalformedProposalError``: nothing has been proposed at the point this
        raises, and there is no model in the picture at all.
        """
        with pytest.raises(SystemConfigurationError):
            Hybrid(
                closed_set(),
                ProposalLayer(
                    ScriptedProvider(list(SCRIPT)),
                    AGENT_GRAMMAR,
                    TranscriptStore(mode=RECORD),
                ),
                name="  ",
            )


class TestDeterminism:
    """SPEC §1 invariant 3, over the surface this item adds."""

    @pytest.mark.parametrize("memory", list(Memory))
    def test_a_brief_renders_identically_on_repeat(self, memory: Memory) -> None:
        investigation = _investigation()
        assert render_brief(investigation, MENU, memory=memory) == render_brief(
            investigation, MENU, memory=memory
        )

    def test_both_arms_agree_across_two_runs(self) -> None:
        """S12, because it is the only scenario where the arms render a brief.

        A fresh pair per run, not one pair run twice: a layer's call counter and
        the scripted backend's script position both carry over, so the second run
        of a reused pair would be a different run and agreement would mean
        nothing. ``memory_ablation``'s docstring says the same thing.
        """
        v3_first, v4_first = _arms()
        v3_second, v4_second = _arms()
        first = _run("S12", v3_first), _run("S12", v4_first)
        second = _run("S12", v3_second), _run("S12", v4_second)
        for before, after in zip(first, second, strict=True):
            assert before.diagnosis.distribution == after.diagnosis.distribution
            assert before.experiments == after.experiments


class TestWhereTheDeltaIsDefined:
    """R2's delta exists only on runs where the proposal layer was asked.

    SPEC F6 makes extension conditional on detection, so on a scenario whose
    check passes at the gate, neither arm is asked for a proposal, the briefs are
    never rendered, and V3 and V4 are the same system running the same
    trajectory. Measured here rather than assumed, because which of S8, S11 and
    S12 that covers is an empirical fact about the diagnostic catalogue.

    The observable is the attempt count, not the run's final check -- :class:`Arm`
    says why the two differ and where they disagree.
    """

    @pytest.mark.parametrize("scenario_id", ABLATION_SCENARIOS)
    def test_both_arms_complete_the_scenario(self, scenario_id: str) -> None:
        v3, v4 = _ablation_runs()[scenario_id]
        assert v3.run.experiments > 0
        assert v4.run.experiments > 0

    @pytest.mark.parametrize("scenario_id", ABLATION_SCENARIOS)
    def test_both_arms_are_asked_the_same_number_of_times(
        self, scenario_id: str
    ) -> None:
        """The gate is upstream of memory, so it opens for both arms or neither.

        It is taken at the half-budget point, before either brief has been
        rendered, and both arms reach it having run the same trajectory. An arm
        asked more often than the other would mean the memory representation had
        reached back into Stage A, which SPEC F5 assigns to conventional methods.
        """
        v3, v4 = _ablation_runs()[scenario_id]
        assert v3.asked == v4.asked

    @pytest.mark.parametrize("scenario_id", ABLATION_SCENARIOS)
    def test_the_arms_are_identical_where_the_layer_is_never_asked(
        self, scenario_id: str
    ) -> None:
        """Where the gate stays shut, the memory representation is never consulted.

        Not a weakness of the ablation: it is the honest report that R2 has no
        delta to measure on that scenario. A pair that differed here would mean
        something other than memory had leaked into the arms.
        """
        v3, v4 = _ablation_runs()[scenario_id]
        if v3.asked:
            pytest.skip(f"{scenario_id}: the layer was asked, so the arms may differ")
        assert v3.run.diagnosis.distribution == v4.run.diagnosis.distribution
        assert v3.run.experiments == v4.run.experiments

    def test_the_layer_is_asked_somewhere_in_the_ablation_scope(self) -> None:
        """If it were asked nowhere, R2 would be unmeasurable on §9's whole cell.

        Recorded as an assertion so that a change to the catalogue which closed
        the last scenario where the delta is defined fails here rather than
        quietly reducing the ablation to a tautology.

        Read off the proposal attempts and **not** off
        :attr:`~sciagent.eval.campaign.ScenarioRun.ppc`. See :class:`Arm`: the
        final check and the gate are different observables, and on S12 they
        disagree.
        """
        asked = {
            scenario_id: _ablation_runs()[scenario_id][0].asked
            for scenario_id in ABLATION_SCENARIOS
        }
        assert any(asked.values()), (
            f"the proposal layer was asked on none of S8, S11, S12 ({asked}), so "
            f"neither arm's memory representation is ever rendered and R2's delta "
            f"is undefined across §9's whole cell"
        )
        assert {key for key, count in asked.items() if count} == {"S12"}, (
            f"the scenarios where R2 is measurable have moved: {asked}. "
            f"docs/DECISIONS.md records S12 as the only one of §9's three that "
            f"reaches the proposal layer, and that measurement is the item's "
            f"finding -- re-measure and record before changing this"
        )
