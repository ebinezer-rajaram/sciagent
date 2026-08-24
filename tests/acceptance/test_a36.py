"""Acceptance test A36: a neutral schema, a cacheable prefix, k samples.

A36 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Elicitation hygiene: a priming example, a cold cache,
one sample"*, and reads:

    ``test_a36_the_schema_is_neutral_and_the_address_versioned`` -- no menu
    mechanism name appears anywhere in the schema or its examples; the address
    version differs from the recorded campaign's; k-sample mode stores k indexed
    calls and admits deterministically.

The entry's **Idea.** is the rest of the standard these tests are written to:
*"Three changes at the elicitation surface, landed together at a
recording-campaign boundary since the schema is hashed into every address: (1)
replace the schema's name-field example ``'self_excitation'`` -- a menu mechanism
that is also S1's truth -- with a mechanism-neutral example; (2) restructure the
brief so the stable menu prefix sits in a cacheable block (the system block is
~300 tokens, below the 512-token cache minimum; the menu rides the user message,
so nothing caches today); (3) add a k-sample elicitation mode (record k
completions at indexed addresses, admit the first grammar-valid, report the
distribution) so proposal diversity and menu coverage become measurable."*

What is actually broken
-----------------------

**The schema primes S1's truth.**
:func:`~sciagent.systems.llm.encoding.tool_schema` describes the ``name`` field
as *"A short slug naming the mechanism, e.g. 'self_excitation'"*. Self-excitation
is menu entry 0 -- ``AddDependency(arrival -> arrival, exponential)`` -- and it
is S1's ground truth (``environments/pointproc/scenarios.py``, ``("S1",
"single", "hawkes", ...)``). Every call on every scenario carries that nudge and
its effect size is unmeasured, which is what makes it a hygiene defect rather
than a wording preference.

**Nothing caches.** ``DEFAULT_SYSTEM_PROMPT`` is ~273 tokens, under the
512-token minimum a cache breakpoint needs, and the menu -- the one part of the
request that is identical across every call of a scenario -- rides the *user*
message. The Agent SDK backend, which is what the recorded campaign used, passes
``system_prompt`` and ``prompt`` as bare strings and cannot place a breakpoint
inside the user turn, so moving the menu into the system block is the only route
to caching it.

**One sample per proposal.** R1's most direct evidence is what the model
*distribution* proposes, and a single completion cannot report a distribution.

Why the bump is deliberate here and was refused at A35
-----------------------------------------------------

``docs/DECISIONS.md`` (2026-08-23) declined to move
:data:`~sciagent.systems.llm.transcripts.ADDRESS_VERSION` for A35, because the
constant doubles as the corpus file's format version and a bump orphans the
recorded 112-call corpus that A40's re-derivation needed. A40 landed at
``docs/BACKLOG.md`` rank 12, so that reason is discharged, and this gate bumps on
purpose: all three changes above move what is hashed into an address, and a
scheme that did not move would leave a recorded corpus resolving against text no
process still produces.

The consequence is not hidden and is asserted below: a ``transcript/2`` corpus is
*refused*, not silently missed.

Cost
----

Nothing here simulates beyond the shared gate table, and no test makes a live
call: k-sampling is graded on :class:`~sciagent.systems.llm.ScriptedProvider`,
which is the whole reason the layer and the backend are separable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.errors import (
    MalformedProposalError,
    SystemConfigurationError,
    TranscriptSchemeError,
)
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.ablation import ABLATION_SYSTEM_PROMPT
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.llm import (
    RECORD,
    REPLAY,
    Memory,
    MenuEntry,
    Proposal,
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
from sciagent.systems.llm.transcripts import ADDRESS_VERSION

MENU = structural_menu(AGENT_GRAMMAR)

#: The address scheme the SPEC §9 campaign was recorded under. Named rather than
#: derived: ``docs/CORPUS.md`` records it for both corpora and
#: ``docs/DECISIONS.md`` (2026-08-23) is the decision that pinned it there, so
#: this is a literal with two independent sources in the repository. The gate's
#: second clause is that ``ADDRESS_VERSION`` no longer equals it.
CAMPAIGN_SCHEME = "transcript/2"

#: The literal this gate exists to remove. It is *not* one of the menu's own
#: construct tokens -- entry 0's construct is ``arrival|exponential`` -- which is
#: exactly why the derived check below is not sufficient on its own: the priming
#: string is the prose name of a mechanism, not the identifier the menu uses for
#: it, so a check built only from the menu would pass over the defect.
PRIMING_EXAMPLE = "self_excitation"

#: A cache breakpoint buys nothing below this many tokens.
CACHE_MINIMUM_TOKENS = 512

#: Characters per token, for turning a length into a token floor without a
#: tokeniser. Deliberately an *under*-estimate of the token count: this text is
#: dense in digits, underscores and punctuation, which tokenise at well under
#: four characters each, so a block that clears the minimum at this ratio clears
#: it in fact. The alternative -- depending on a tokeniser -- would make an
#: offline gate depend on a network or on a pinned vocabulary.
CHARS_PER_TOKEN = 4

#: The heading ``render_menu_prefix`` emits. Asserted on rather than on a
#: substring of the menu's body, so the test reads the block's identity and not
#: prose that could appear in a rationale.
MENU_HEAD = "## Structures you may propose"

#: Any quoted literal. The ``name`` field's description must contain none, and
#: the reasoning is in the gate test's own docstring: a blacklist cannot decide
#: neutrality, so what is checked is that the field describes a *format* and
#: illustrates it with no instance at all.
QUOTED_LITERAL = re.compile(r"""['"][^'"]+['"]""")

#: The other three ways a description introduces an instance without quoting it.
ILLUSTRATIONS = ("e.g.", "for example", "such as", "for instance")


def _mechanism_names(menu: Sequence[MenuEntry]) -> frozenset[str]:
    """Return every string by which the menu names a mechanism or its site.

    Derived from the menu rather than listed, so the check cannot go stale
    against a grammar that gains a structure. A construct is split on ``|``
    because a dependency's identity is a ``source|kernel`` pair
    (``encoding._construct_of``) and each half names something on its own.
    """
    names: set[str] = set()
    for entry in menu:
        names.add(str(entry.target))
        names.update(entry.construct.split("|"))
    return frozenset(names)


def _investigation(scenario_id: str = "S11", *, steps: int = 2) -> Investigation:
    """Return an investigation part-way through, for a brief to be rendered from.

    The same construction ``tests/test_llm.py`` uses, on the shared gate table.
    S11 rather than S1: the point of this gate is that the elicitation surface
    stops naming S1's truth, so a fixture built on S1 would be reading the
    defect's own scenario back to itself.
    """
    the_scenario = scenario(scenario_id)
    table = gate_table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    investigation = Investigation(
        scenario_id=the_scenario.id,
        designs=the_scenario.designs,
        truth=the_scenario.executed,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR)),
        graph=graph,
        seed=the_scenario.seed,
    )
    library = closed_set()
    entertain(investigation, {"hawkes": library["hawkes"]})
    for design in investigation.designs[:steps]:
        investigation.run(design)
    return investigation


def _legacy_corpus(path: Path, version: str) -> Path:
    """Write a one-call corpus by hand under ``version``, and return its path.

    Hand-written rather than produced by :meth:`TranscriptStore.save`, for the
    reason ``docs/DECISIONS.md`` (2026-08-23) records: ``save`` stamps whatever
    the constant currently says and ``load`` compares against that same
    constant, so a round trip through both is version-*agnostic* and would pass
    under any string at all. Only a literal in the file tests the refusal.
    """
    path.write_text(
        json.dumps(
            {
                "version": version,
                "calls": [
                    {
                        "address": "call/0",
                        "provider": "scripted",
                        "model": "scripted/1",
                        "settings": "",
                        "brief": "brief",
                        "payload": fixed_payload(3),
                        "provenance": {},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


class TestA36TheSchemaIsNeutralAndTheAddressVersioned:
    """The gate's two static clauses, and the consequence of the second."""

    def test_a36_the_schema_is_neutral_and_the_address_versioned(self) -> None:
        """The criterion named in ``docs/BACKLOG.md``, in one place.

        **Every clause is evaluated before anything is asserted**, and the
        failures are reported together. The first draft of this test asserted
        the clauses in sequence, so the neutrality assert fired and the address
        clause below it was never executed -- a criterion nobody had watched
        fail, which is the state this whole step exists to prevent. A review
        caught it.

        Neutrality is checked two ways, because a blacklist cannot decide it:

        * **No mechanism name anywhere in the schema.** The forbidden set is
          derived -- every menu construct token and target, so it tracks the
          grammar rather than a snapshot of it, plus every ``closed_set()`` key,
          which is what a scenario's truth is called and therefore the
          vocabulary a priming example is drawn from, plus the literal
          :data:`PRIMING_EXAMPLE`, which belongs to neither and is the actual
          defect. Matched as a case-folded substring: the claim is that the
          string does not *appear*, and ``Self_Excitation`` would be the same
          nudge.
        * **No instance in the name field at all.** The set above is finite and
          the vocabulary of mechanisms is not: a review measured that
          ``'excitation'`` and ``'self-exciting'`` both clear it while preserving
          the identical nudge toward S1's truth. So what is required of the
          ``name`` field is that it describes a *format* and illustrates it with
          nothing -- no quoted literal, no ``e.g.``. That is stronger than the
          **Idea.**'s "mechanism-neutral example", deliberately: the
          **Rationale.** objects to an *uncontrolled nudge of unmeasured effect
          size*, and every instance is one. Over-strong is the safe direction
          here -- it can refuse a harmless example, it cannot admit a priming
          one.

        The standing instruction is checked against the same forbidden set, so
        the fix cannot discharge the gate by moving the example from the schema
        into ``DEFAULT_SYSTEM_PROMPT`` -- which, once the menu prefix joins it,
        is the block the model reads first on every call. That clause is green
        today and is a guard rather than a criterion; it is here because the
        gate's own wording ("in the schema or its examples") does not reach it.
        """
        schema = tool_schema(MENU)
        forbidden = _mechanism_names(MENU) | frozenset(closed_set()) | {PRIMING_EXAMPLE}
        problems: list[str] = []

        rendered = json.dumps(schema, sort_keys=True).casefold()
        named = sorted(name for name in forbidden if name.casefold() in rendered)
        if named:
            problems.append(
                f"the schema names {named!r}, which primes that mechanism on "
                f"every call of every scenario"
            )

        description = str(schema["properties"]["name"]["description"])
        quoted = QUOTED_LITERAL.search(description)
        if quoted is not None:
            problems.append(
                f"the name field illustrates itself with {quoted.group()!r}; an "
                f"instance is a nudge whatever it names"
            )
        shown = [marker for marker in ILLUSTRATIONS if marker in description.casefold()]
        if shown:
            problems.append(f"the name field introduces an example with {shown!r}")

        instruction = DEFAULT_SYSTEM_PROMPT.casefold()
        relocated = sorted(name for name in forbidden if name.casefold() in instruction)
        if relocated:
            problems.append(
                f"the standing instruction names {relocated!r}, so the example "
                f"moved out of the schema rather than going away"
            )

        if ADDRESS_VERSION == CAMPAIGN_SCHEME:
            problems.append(
                f"the address scheme is still {CAMPAIGN_SCHEME!r}; all three A36 "
                f"changes move what is hashed into an address, so a corpus "
                f"recorded before them would resolve against text no process "
                f"still produces"
            )

        assert not problems, "; ".join(problems)

    def test_a36_a_corpus_recorded_under_the_campaign_scheme_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The bump's consequence, asserted rather than left to be discovered.

        A corpus recorded under the campaign's scheme must raise, not miss. The
        two are very different things to be handed: a refusal names the reason,
        while a store that loaded and then missed every address would look like
        a model that had changed its mind about everything at once.

        The current scheme's own file still loads, which is what makes this a
        test of the version check rather than of the reader being broken.
        """
        with pytest.raises(TranscriptSchemeError) as refused:
            TranscriptStore.load(
                _legacy_corpus(tmp_path / "campaign.json", CAMPAIGN_SCHEME)
            )
        assert CAMPAIGN_SCHEME in str(refused.value)

        current = TranscriptStore.load(
            _legacy_corpus(tmp_path / "current.json", ADDRESS_VERSION)
        )
        assert current.get("call/0").payload == fixed_payload(3)


class TestA36TheStablePrefixIsCacheable:
    """The **Idea.**'s second change: the menu moves where a cache can hold it."""

    def _layer(
        self, *, memory: Memory = Memory.BOTH, system_prompt: str = ""
    ) -> ProposalLayer:
        """Return a layer over a provider that is never called."""
        return ProposalLayer(
            ScriptedProvider(()),
            AGENT_GRAMMAR,
            TranscriptStore(mode=RECORD),
            memory=memory,
            system_prompt=system_prompt,
        )

    def test_a36_the_menu_leaves_the_brief_for_the_system_block(self) -> None:
        """The stable part is in the stable block, and only there.

        Both halves matter. In the system block alone it is cacheable; still in
        the brief as well it would be sent twice, which is worse than before.
        """
        from sciagent.systems.llm.encoding import render_menu_prefix

        prefix = render_menu_prefix(MENU)
        assert MENU_HEAD in prefix
        assert "[0] AddDependency" in prefix

        brief = render_brief(_investigation())
        assert MENU_HEAD not in brief, (
            "the menu is still in the user message, so every call pays for it "
            "again and the move bought nothing"
        )
        assert MENU_HEAD in self._layer().system

    @pytest.mark.parametrize(
        "system_prompt",
        ["", ABLATION_SYSTEM_PROMPT, "Propose a structure."],
        ids=["default", "ablation", "caller"],
    )
    def test_a36_the_system_block_clears_the_cache_minimum(
        self, system_prompt: str
    ) -> None:
        """Below 512 tokens a breakpoint buys nothing, which is today's state.

        The instruction alone is ~273 tokens. The claim is that the instruction
        *plus the menu* clears the floor, so this asserts on the composed block
        rather than on the prefix in isolation.

        **Parameterised over a caller-supplied prompt, and that is the point of
        the test rather than thoroughness.** A review found the first draft
        built every layer on the default prompt, which admits a one-parenthesis
        implementation -- ``system_prompt or (DEFAULT + prefix)`` instead of
        ``(system_prompt or DEFAULT) + prefix`` -- that passes the whole gate
        while giving V3 and V4 no prefix at all.
        :func:`~sciagent.systems.ablation.memory_ablation` supplies
        :data:`~sciagent.systems.ablation.ABLATION_SYSTEM_PROMPT` to both arms,
        so under that implementation they sit at 277 tokens against this floor
        *and* index into a menu that is now in neither the system block nor the
        brief. Measured: 273/277 alone, 899/904 composed.
        """
        system = self._layer(system_prompt=system_prompt).system
        assert MENU_HEAD in system, (
            "a caller who supplied their own instruction lost the menu, which "
            "is now in no block the model sees"
        )
        tokens = len(system) // CHARS_PER_TOKEN
        assert tokens >= CACHE_MINIMUM_TOKENS, (
            f"the system block is ~{tokens} tokens, under the "
            f"{CACHE_MINIMUM_TOKENS} a cache breakpoint needs, so nothing is "
            f"cached and the restructure has not paid for the address bump it "
            f"cost"
        )

    @pytest.mark.parametrize(
        "system_prompt",
        ["", ABLATION_SYSTEM_PROMPT],
        ids=["default", "ablation"],
    )
    def test_a36_the_instruction_points_the_right_way_at_the_menu(
        self, system_prompt: str
    ) -> None:
        """Where the instruction says the menu is, is where the menu is.

        A36 exists because the elicitation surface said something false to the
        model. Moving the menu made two instructions false in a new way -- they
        still said "listed in the brief" -- and the fix for *that* said "listed
        above", which is inverted: the block is composed instruction-then-menu,
        so the menu is ~900 characters below the sentence. A second review pass
        measured it at char 179 against char 1087.

        Pinned rather than trusted, because both wordings read fine and neither
        the type checker nor the suite could tell them apart.

        **Scoped to where the *menu* is, and not to the word "brief".** The
        first draft of this test forbade "in the brief" outright and failed on
        ``ABLATION_SYSTEM_PROMPT``'s closing line, which asks for a rationale
        naming what in the brief motivated the choice -- and the observations
        genuinely are in the brief. That sentence is correct; only the claim
        about the *structures* moved.
        """
        system = self._layer(system_prompt=system_prompt).system
        instruction, _, menu = system.partition(MENU_HEAD)

        assert menu, "the menu is not in the system block at all"
        assert "listed above" not in instruction, (
            "the instruction says the structures are listed above it, but the "
            "menu is composed below it -- the model is being pointed the wrong "
            "way, which is the class of defect this gate exists to remove"
        )
        assert "listed in the brief" not in instruction, (
            "the instruction still points at the brief for the structures, "
            "which the menu left"
        )

    def test_a36_the_prefix_is_identical_across_the_memory_arms(self) -> None:
        """The menu is the action space, not memory, so V3 and V4 share it.

        ``Memory`` ablates what the model is told *happened*. Moving the menu
        into the system block must not make it an arm difference: if the two
        arms' prefixes diverged, R2's delta would be measuring the action space
        as well as the representation.
        """
        prefixes = {memory: self._layer(memory=memory).system for memory in Memory}
        assert len(set(prefixes.values())) == 1, (
            "the arms' system blocks differ, so the memory ablation now varies "
            "the action space too"
        )


class TestA36KSampleMode:
    """The gate's third clause: k indexed calls, and a deterministic admission."""

    #: Sample 0 names a structure the five-entry menu does not offer, so it
    #: decodes to a draft and then fails at ``decode``. That is the interesting
    #: failure: the payload conformed to the schema, so a provider-side check
    #: would not have caught it, and only the grammar can refuse it.
    SCRIPT = (
        fixed_payload(99, (), name="off_menu"),
        fixed_payload(1, (10, 20, 30, 40), name="second"),
        fixed_payload(2, (5, 6, 7), name="third"),
    )

    def _run(
        self, store: TranscriptStore, *, samples: int
    ) -> tuple[Proposal, ProposalLayer]:
        """Propose once at ``samples`` draws, returning the proposal and layer."""
        layer = ProposalLayer(
            ScriptedProvider(self.SCRIPT),
            AGENT_GRAMMAR,
            store,
            samples=samples,
        )
        return layer.propose(_investigation()), layer

    def test_a36_k_sample_mode_stores_k_indexed_calls(self) -> None:
        """Three draws, three distinct addresses, all of them recorded.

        Distinct is the load-bearing half. Three calls sharing one address would
        put three answers at one key, which :meth:`TranscriptStore.put` refuses
        -- so a scheme that did not separate the draws would not merely
        mis-record, it would raise on the second one.
        """
        store = TranscriptStore(mode=RECORD)
        _proposal, layer = self._run(store, samples=3)

        assert layer.samples == 3
        assert layer.proposals == 1
        assert layer.calls == 3, "the layer did not make one call per draw"
        assert len(store.addresses()) == 3
        assert len(set(store.addresses())) == 3

        schema = tool_schema(MENU)
        brief = render_brief(_investigation())
        expected = tuple(
            call_address(
                provider="scripted",
                model="scripted/1",
                settings="",
                system=layer.system,
                brief=brief,
                schema=schema,
                index=0,
                sample=draw,
            )
            for draw in range(3)
        )
        assert store.addresses() == tuple(sorted(expected)), (
            "the recorded addresses are not the ones a draw of proposal 0 "
            "addresses to, so the corpus cannot be replayed by this layer"
        )

        # Each record's `sample` must be the index actually hashed into *its own*
        # address, not merely its position in the list. The two coincide in every
        # fixture here -- one record per draw, in order -- which is exactly why a
        # review flagged the coincidence: nothing tied the field to the address
        # it names, so a record could have pointed at a call it did not describe.
        for record in layer.draws:
            assert record.proposal == 0
            assert record.address == expected[record.sample], (
                f"draw {record.sample} names an address that belongs to a "
                f"different sample"
            )
        assert tuple(record.sample for record in layer.draws) == (0, 1, 2)

    def test_a36_admission_is_the_first_grammar_valid_sample(self) -> None:
        """Not the first *answer*: the first one the grammar licenses.

        Sample 0 conforms to the schema and names structure 99, which the menu
        does not offer. A layer that admitted by position would raise; one that
        admitted the last valid draft would take sample 2. The criterion is
        *first grammar-valid*, and only sample 1 satisfies it.
        """
        proposal, layer = self._run(TranscriptStore(mode=RECORD), samples=3)

        assert proposal.name == "second"
        outcomes = tuple(draw.outcome for draw in layer.draws)
        assert outcomes == ("malformed", "admitted", "valid"), outcomes
        assert proposal.address == layer.draws[1].address

    def test_a36_all_k_draws_are_taken_even_once_one_is_admitted(self) -> None:
        """Stopping early would make the addresses depend on the answers.

        Sample 2 is valid and unused, and it is still called and still recorded.
        Two reasons, and the second is the one that would bite: the distribution
        is the point of the mode, and a run that stopped at the first valid
        draft would consume a number of addresses determined by what the model
        said -- so replaying it would depend on reproducing the answers it was
        replaying in order to find them.
        """
        store = TranscriptStore(mode=RECORD)
        _proposal, layer = self._run(store, samples=3)
        assert layer.calls == 3
        assert len(store.addresses()) == 3

    def test_a36_two_identical_runs_admit_the_same_sample(self) -> None:
        """Deterministic, which is the half of the clause that is not a count."""
        first_store = TranscriptStore(mode=RECORD)
        second_store = TranscriptStore(mode=RECORD)
        first, first_layer = self._run(first_store, samples=3)
        second, second_layer = self._run(second_store, samples=3)

        assert first.address == second.address
        assert first.program_edit == second.program_edit
        assert first.name == second.name
        assert first_store.addresses() == second_store.addresses()
        assert first_layer.draws == second_layer.draws

    def test_a36_a_recorded_k_sample_run_replays_with_no_misses(self) -> None:
        """The mode is only worth having if the corpus it writes replays.

        A recording pass that stored three draws and a replay that read one
        would be a mode that made a matrix unreproducible in exchange for a
        distribution.
        """
        recorded = TranscriptStore(mode=RECORD)
        live, _layer = self._run(recorded, samples=3)

        replayed_store = TranscriptStore(
            {address: recorded.get(address) for address in recorded.addresses()},
            mode=REPLAY,
        )
        replayed, replayed_layer = self._run(replayed_store, samples=3)

        assert replayed.address == live.address
        assert replayed.program_edit == live.program_edit
        assert replayed_store.misses == 0
        assert replayed_layer.calls == 3

    def test_a36_the_default_is_a_single_sample(self) -> None:
        """One draw unless asked, so no existing arm changes behaviour or cost.

        The failure mode this guards is silent and expensive: a default above
        one multiplies the live cost of every future recording campaign by k
        without anybody choosing it.
        """
        store = TranscriptStore(mode=RECORD)
        layer = ProposalLayer(ScriptedProvider(self.SCRIPT[1:]), AGENT_GRAMMAR, store)
        proposal = layer.propose(_investigation())

        assert layer.samples == 1
        assert layer.calls == 1
        assert layer.proposals == 1
        assert len(store.addresses()) == 1
        assert proposal.name == "second"

    def test_a36_the_draws_report_what_was_proposed(self) -> None:
        """Diversity and menu coverage, which is what the mode is *for*.

        A count over the draws, computed by the framework from what the drafts
        named. Nothing here is a number a model wrote: a structure index is a
        choice of cell, and the count of them is arithmetic this side of the
        boundary.
        """
        _proposal, layer = self._run(TranscriptStore(mode=RECORD), samples=3)

        assert tuple(draw.structure for draw in layer.draws) == ((99,), (1,), (2,))
        assert layer.structure_counts() == (((1,), 1), ((2,), 1), ((99,), 1))

    def test_a36_a_non_conforming_payload_is_one_spent_draw(self) -> None:
        """A payload that does not conform is one bad draw, not the end of the run.

        The regression test for a defect `/preflight`'s review found and
        reproduced. `draft_from_payload` raises `MalformedProposalError`, which
        is a **sibling** of `ProviderError` under `ProposalError` — so while that
        call sat inside the sampling loop's `except ProviderError`, a
        non-conforming payload escaped `propose` outright: the later draws were
        never taken, an already-admissible draft from a *lower* sample was thrown
        away, and not one `SampleRecord` was written.

        The other malformed tests here do not reach it. `fixed_payload(99)`
        conforms to the schema — 99 is an integer — so it parses and fails later,
        at `decode`. Only a payload the *schema* rejects takes the escaping path,
        and the sharpest one is a smuggled number: a model that writes
        `plausibility` has tried to author a value, which
        `draft_from_payload` refuses by design.

        Sample 0 is valid here on purpose. The bug discarded a good draft it had
        already paid for, so a script whose first draw was also bad would have
        hidden half of what went wrong.
        """
        smuggled = fixed_payload(1, (10, 20, 30, 40), name="smuggled")
        smuggled["plausibility"] = 0.99
        store = TranscriptStore(mode=RECORD)
        layer = ProposalLayer(
            ScriptedProvider(
                (
                    fixed_payload(2, (5, 6, 7), name="first"),
                    smuggled,
                    fixed_payload(3, (20, 30, 40), name="third"),
                )
            ),
            AGENT_GRAMMAR,
            store,
            samples=3,
        )

        proposal = layer.propose(_investigation())

        assert proposal.name == "first", (
            "the draw that was already admissible was discarded by a later "
            "draw's failure"
        )
        assert layer.calls == 3, "the run stopped early on a non-conforming payload"
        assert len(store.addresses()) == 3
        outcomes = tuple(draw.outcome for draw in layer.draws)
        assert outcomes == ("admitted", "malformed", "valid"), outcomes
        assert layer.draws[1].structure is None, (
            "a payload that never parsed cannot report a structure"
        )
        assert layer.draws[1].cause == "undecodable", (
            "the cause field is a bin key, so it carries the tag and not prose"
        )
        assert "plausibility" in layer.draws[1].detail

    def test_a36_a_run_with_no_valid_draw_raises_the_first_samples_error(self) -> None:
        """k samples do not turn an unusable answer into a silent success.

        Every draw off-menu, so nothing is admissible. The first draw's error is
        what propagates, which is what makes k=1 behaviour exactly the old
        behaviour rather than approximately it.
        """
        layer = ProposalLayer(
            ScriptedProvider((fixed_payload(97), fixed_payload(98))),
            AGENT_GRAMMAR,
            TranscriptStore(mode=RECORD),
            samples=2,
        )
        with pytest.raises(MalformedProposalError) as refused:
            layer.propose(_investigation())
        assert "97" in str(refused.value)
        assert tuple(draw.outcome for draw in layer.draws) == (
            "malformed",
            "malformed",
        )

    def test_a36_a_non_positive_sample_count_is_refused(self) -> None:
        """Zero draws is a layer that cannot propose, and says so at construction.

        Caught where it is written rather than at the first ``propose``, so the
        configuration error is reported by the thing that was misconfigured.
        """
        with pytest.raises(SystemConfigurationError):
            ProposalLayer(
                ScriptedProvider(()),
                AGENT_GRAMMAR,
                TranscriptStore(mode=RECORD),
                samples=0,
            )
