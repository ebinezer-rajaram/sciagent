"""The LLM proposal layer (SPEC §11 item 12), minus the LLM.

Everything reproducible about a model-backed system lives in
``sciagent/systems/llm/`` and none of it needs a model: the structural menu, the
brief, the schema, the decoder, and the record/replay store are all pure
functions of things this suite can construct. That is the point of the split, and
it is what lets the layer be gated offline while the measurement of what a model
*proposes* waits for a recorded corpus.

Not named ``test_aN_``: item 12's gate is SPEC §12's slice completion criteria,
which are measurements over V7 runs rather than an acceptance criterion of their
own. A17's arm over this package lives in ``tests/acceptance/test_a16_a18.py``
where the rest of A17 is.

The two properties worth stating plainly, because the rest are details:

* **No number can cross the boundary.** Asserted three ways -- the schema
  contains no ``number`` type, a payload carrying a float is refused, and a
  decoded parameter is always a grid point.
* **A replay is bit-exact, including across processes.** A ``Defect`` is a
  ``frozenset``, so the brief that feeds an address renders one in canonical
  order or the corpus stops replaying anywhere but the machine that recorded it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

from environments.pointproc.grammar import agent_grammar
from environments.pointproc.mechanisms import SIZE_EXCITATION
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.edits import AddDependency, canonical
from sciagent.core.errors import (
    MalformedProposalError,
    ProposalError,
    ProviderError,
    TranscriptMissError,
)
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.llm import (
    RECORD,
    REPLAY,
    EditDraft,
    ProposalDraft,
    ProposalLayer,
    Provider,
    ScriptedProvider,
    Transcript,
    TranscriptStore,
    call_address,
    decode,
    draft_from_payload,
    fixed_payload,
    render_brief,
    structural_menu,
    tool_schema,
)

CHILD = Path(__file__).parent / "transcript_child.py"

MENU = structural_menu(AGENT_GRAMMAR)
SCHEMA = tool_schema(MENU)


def _investigation(
    scenario_id: str = "S1",
    *,
    steps: int = 2,
    entertained: Sequence[str] = (),
) -> Investigation:
    """Return an investigation part-way through, for a brief to be rendered from."""
    the_scenario = scenario(scenario_id)
    table = gate_table()
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


def _address(
    *,
    provider: str = "p",
    model: str = "m",
    system: str = "s",
    brief: str = "b",
    index: int = 0,
) -> str:
    """Return an address for a fixed request, with one part optionally changed."""
    return call_address(
        provider=provider,
        model=model,
        system=system,
        brief=brief,
        schema=SCHEMA,
        index=index,
    )


def _layer(provider: Provider, store: TranscriptStore | None = None) -> ProposalLayer:
    """Return a layer over ``provider``, recording by default."""
    return ProposalLayer(
        provider,
        AGENT_GRAMMAR,
        store if store is not None else TranscriptStore(mode=RECORD),
    )


def smuggling_payload(
    field: str = "plausibility", value: float = 0.99
) -> dict[str, Any]:
    """Return a payload that tries to write a number, for A17's negative control.

    Deliberately here and not in ``sciagent/systems/llm/scripted.py``. That module
    is inside ``AGENT_TOOL_SURFACE``, so A17's analyser reads the string constant
    ``"plausibility"`` and reports the module as an agent-reachable path
    mentioning a sealed symbol -- correctly. A helper whose only purpose is to
    construct the thing an invariant forbids has no business being importable by
    the systems that invariant constrains.

    The realistic violation is not an agent assigning to a frozen attribute --
    ``HypothesisNode`` forbids that -- but one smuggling a value through the only
    channel it has, which is the payload.
    """
    payload = fixed_payload(0, (1, 2, 3), name="smuggled")
    payload[field] = value
    payload["edits"][0][field] = value
    return payload


class TestTheModelCannotWriteANumber:
    """SPEC F7 at this boundary, asserted three independent ways."""

    def test_a_smuggled_number_never_reaches_the_framework(self) -> None:
        """The negative control: a provider that *tries* is refused.

        A guarantee tested only against well-behaved input is a guarantee about
        the input. The refusal comes from
        :func:`~sciagent.systems.llm.encoding.draft_from_payload`, so the value
        never enters the framework's types at all and nothing downstream has to
        remember to ignore it.
        """
        with pytest.raises(MalformedProposalError):
            draft_from_payload(smuggling_payload())

    def test_the_layer_refuses_a_smuggling_provider_end_to_end(self) -> None:
        """The same control through the whole layer, not just the decoder."""
        layer = _layer(ScriptedProvider([smuggling_payload()]))
        with pytest.raises(MalformedProposalError):
            layer.propose(_investigation("S1"))

    def test_the_schema_declares_no_number_anywhere(self) -> None:
        """The wire format has no channel a real value could travel down.

        The strongest of the three, because it holds regardless of what any
        provider does: a response constrained by this schema cannot carry a
        float, so "the agent does not write numbers" is a property of the format
        rather than of the prompt or of the model's compliance.
        """
        serialised = json.dumps(SCHEMA)
        assert '"number"' not in serialised
        assert '"integer"' in serialised, (
            "the schema declares no integer either, so the parameter channel has "
            "gone missing rather than been constrained"
        )

    def test_the_schema_is_strict_at_every_level(self) -> None:
        """``additionalProperties`` is false wherever an object is declared."""
        objects = [SCHEMA, SCHEMA["properties"]["edits"]["items"]]
        for node in objects:
            assert node["additionalProperties"] is False, node

    def test_a_payload_carrying_a_float_is_refused(self) -> None:
        """The realistic violation: a value smuggled through the only open channel.

        A model cannot assign to a frozen ``HypothesisNode``, so the way a number
        would actually arrive is inside the payload. It is refused at decode,
        which means it never enters the framework's types and nothing downstream
        has to remember to ignore it.
        """
        payload = fixed_payload(0, (1, 2, 3))
        payload["edits"][0]["parameters"] = [1, 2.5, 3]
        with pytest.raises(MalformedProposalError, match="index and never a value"):
            draft_from_payload(payload)

    def test_a_conforming_payload_decodes_to_structure_and_prose_only(self) -> None:
        """The positive control: what a well-behaved provider sends, and all of it.

        Paired with the refusals above. A decoder that rejected everything would
        satisfy every negative test in this class and be useless, so this pins
        that a conforming payload still decodes, and decodes to exactly the three
        fields ``ProposalDraft`` declares.
        """
        payload = fixed_payload(0, (1, 2, 3))
        draft = draft_from_payload(payload)
        assert draft == ProposalDraft(
            edits=(EditDraft(structure=0, parameters=(1, 2, 3)),),
            name=payload["name"],
            rationale=payload["rationale"],
        )
        assert set(type(draft).__slots__) == {"edits", "name", "rationale"}

    def test_an_unknown_key_is_refused_rather_than_dropped(self) -> None:
        """The schema says ``additionalProperties: false``; the decoder agrees.

        A decoder more permissive than the contract it publishes is a decoder
        whose contract is not the real one. The value could never reach a score
        either way -- ``ProposalDraft`` has no field for it -- but "the number was
        refused" and "the number was silently dropped" are different things to be
        able to say afterwards, and only the first is evidence about the model.
        """
        with pytest.raises(MalformedProposalError, match="the schema does not declare"):
            draft_from_payload(smuggling_payload())

    def test_an_unknown_key_inside_an_edit_is_refused_too(self) -> None:
        payload = fixed_payload(0, (1, 2, 3))
        payload["edits"][0]["weight"] = 3
        with pytest.raises(
            MalformedProposalError, match="an edit is a structure index"
        ):
            draft_from_payload(payload)

    def test_every_decoded_parameter_is_a_grid_point(self) -> None:
        """On-grid by construction, so the prefix code is always defined.

        The parameter channel is an index into
        :attr:`~sciagent.core.edits.ParameterGrid.values`, so
        :meth:`~sciagent.core.edits.EditGrammar.code_length` -- which refuses an
        off-grid value rather than snapping it -- cannot raise on a decoded
        proposal. Checked over every menu cell at three index positions.
        """
        for entry in MENU:
            for position in (0, entry.grids[0].size // 2, entry.grids[0].size - 1):
                indices = tuple(min(position, grid.size - 1) for grid in entry.grids)
                defect = decode(
                    AGENT_GRAMMAR,
                    MENU,
                    ProposalDraft((EditDraft(entry.index, indices),)),
                )
                assert AGENT_GRAMMAR.code_length(defect) > 0.0


class TestTheMenuIsExactlyTheGrammar:
    """A structure outside the grammar has no index, so it cannot be named."""

    def test_every_menu_entry_decodes_to_a_licensed_edit(self) -> None:
        for entry in MENU:
            defect = decode(
                AGENT_GRAMMAR,
                MENU,
                ProposalDraft((EditDraft(entry.index, tuple(0 for _ in entry.grids)),)),
            )
            AGENT_GRAMMAR.validate_defect(defect)

    def test_the_menu_has_one_entry_per_grammar_structure(self) -> None:
        assert len(MENU) == len(list(AGENT_GRAMMAR.structures()))

    def test_s11s_mechanism_has_no_menu_entry(self) -> None:
        """The out-of-library property, made structural rather than validated.

        SPEC §4.5's S11 is a lagged dependency of arrivals on prior mark *sizes*.
        The agent grammar licenses a self-excitation and not that, so the menu
        built from it contains no way to say it -- the model is not refused when
        it proposes S11's mechanism, it has no vocabulary in which to propose it.
        That is a stronger guarantee than a validator, and it is what makes S11
        out-of-library by SPEC §3.2's mechanical definition.
        """
        truth = next(iter(canonical(frozenset({SIZE_EXCITATION}))))
        assert isinstance(truth, AddDependency)
        assert not any(
            entry.construct.startswith(f"{truth.source}|") for entry in MENU
        ), (
            f"the agent menu offers a dependency from {truth.source}, so S11 "
            f"is not out of library after all"
        )

    def test_the_environment_grammar_does_offer_it(self) -> None:
        """The control: S11 is unproposable because of the *agent's* grammar."""
        environment_menu = structural_menu(GRAMMAR)
        truth = next(iter(canonical(frozenset({SIZE_EXCITATION}))))
        assert isinstance(truth, AddDependency)
        assert any(
            entry.construct.startswith(f"{truth.source}|") for entry in environment_menu
        )

    def test_the_two_grammars_differ_only_where_spec_says(self) -> None:
        """``agent_grammar`` is a strict subset of the environment's menu."""
        agent = {
            (e.edit_type, e.target, e.construct)
            for e in structural_menu(agent_grammar())
        }
        environment = {
            (e.edit_type, e.target, e.construct) for e in structural_menu(GRAMMAR)
        }
        assert agent < environment


class TestDecodingRefusesWhatDoesNotDenote:
    """Every refusal names what was offered; a bad draft is what gets debugged."""

    def test_an_empty_draft_is_refused(self) -> None:
        with pytest.raises(MalformedProposalError, match="names no edit"):
            decode(AGENT_GRAMMAR, MENU, ProposalDraft(()))

    def test_an_unknown_structure_index_is_refused(self) -> None:
        with pytest.raises(MalformedProposalError, match="the menu offers"):
            decode(AGENT_GRAMMAR, MENU, ProposalDraft((EditDraft(len(MENU), ()),)))

    def test_a_negative_structure_index_is_refused(self) -> None:
        with pytest.raises(MalformedProposalError, match="the menu offers"):
            decode(AGENT_GRAMMAR, MENU, ProposalDraft((EditDraft(-1, ()),)))

    def test_the_wrong_number_of_parameters_is_refused(self) -> None:
        entry = MENU[0]
        with pytest.raises(MalformedProposalError, match="which takes"):
            decode(
                AGENT_GRAMMAR,
                MENU,
                ProposalDraft((EditDraft(entry.index, (0,) * (entry.arity + 1)),)),
            )

    def test_a_grid_index_past_the_end_is_refused(self) -> None:
        entry = MENU[0]
        indices = tuple(grid.size for grid in entry.grids)
        with pytest.raises(MalformedProposalError, match="whose grid has"):
            decode(
                AGENT_GRAMMAR, MENU, ProposalDraft((EditDraft(entry.index, indices),))
            )

    def test_a_payload_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(MalformedProposalError, match="not an object"):
            draft_from_payload(
                {"name": "x", "rationale": "", "edits": ["not an object"]}
            )

    def test_a_boolean_is_not_accepted_as_an_index(self) -> None:
        """``True`` is an ``int`` in Python, and is refused anyway.

        Worth its own test because ``isinstance(True, int)`` is the trap that
        would let a payload of booleans decode to grid indices 0 and 1 and look
        entirely well-formed.
        """
        payload = fixed_payload(0, ())
        payload["edits"][0]["structure"] = True
        with pytest.raises(MalformedProposalError, match="not an integer"):
            draft_from_payload(payload)


class TestTheBriefTellsTheTruthWithoutTellingTheAnswer:
    """The brief is built from an ``Investigation``, which cannot reach the truth."""

    def test_the_truths_parameters_do_not_appear_when_it_is_not_entertained(
        self,
    ) -> None:
        """S1's truth is a Hawkes process; its calibrated parameters must not leak.

        The menu necessarily lists the Hawkes *structure* -- that is the space
        being searched. What must never appear is the particular parameter
        assignment the environment was built with, and it does not, because the
        only path to it is ``Scenario.truth`` and an ``Investigation`` has none.
        """
        investigation = _investigation("S1", entertained=())
        brief = render_brief(investigation, MENU)
        truth = scenario("S1").truth
        for edit in canonical(truth):
            for key in sorted(edit.parameters):
                assert f"{edit.parameters[key]:g}" not in brief, (
                    f"the brief contains the truth's {key}={edit.parameters[key]:g}"
                )

    def test_the_brief_reports_the_check_verdict(self) -> None:
        """Stage A is conventional (SPEC F5/F6), so the model is told the answer."""
        investigation = _investigation("S1", steps=4)
        brief = render_brief(investigation, MENU)
        assert "Posterior predictive check" in brief
        expected = "do NOT explain" if investigation.ppc().inadequate else "adequately"
        assert expected in brief

    def test_the_brief_reports_every_experiment_run(self) -> None:
        investigation = _investigation("S1", steps=3)
        brief = render_brief(investigation, MENU)
        for step in range(3):
            assert f"step {step}:" in brief

    def test_the_brief_is_stable_across_repeated_rendering(self) -> None:
        investigation = _investigation("S1", entertained=("hawkes", "seasonality"))
        first = render_brief(investigation, MENU)
        assert all(render_brief(investigation, MENU) == first for _ in range(5))


class TestTheTranscriptStore:
    """Record and replay, which is what makes an LLM run reproducible at all."""

    def test_replay_raises_on_a_miss_rather_than_calling_out(self) -> None:
        """The guarantee: an evaluation run cannot become a live one by accident."""
        store = TranscriptStore(mode=REPLAY)
        with pytest.raises(TranscriptMissError, match="will not call out"):
            store.resolve(
                "call/absent",
                lambda: fixed_payload(0, ()),
                provider="p",
                model="m",
                brief="b",
            )

    def test_record_fills_a_miss_and_counts_it(self) -> None:
        store = TranscriptStore(mode=RECORD)
        payload = fixed_payload(0, ())
        store.resolve("call/x", lambda: payload, provider="p", model="m", brief="b")
        assert store.misses == 1
        assert store.get("call/x").payload == payload

    def test_a_replayed_call_reports_no_miss(self) -> None:
        """What a reproducibility check asserts: it replayed, it did not re-derive."""
        store = TranscriptStore(mode=RECORD)
        store.resolve(
            "call/x", lambda: fixed_payload(0, ()), provider="p", model="m", brief="b"
        )
        replayed = TranscriptStore({t.address: t for t in store}, mode=REPLAY)
        replayed.resolve(
            "call/x", lambda: fixed_payload(1, ()), provider="p", model="m", brief="b"
        )
        assert replayed.misses == 0

    def test_a_different_payload_at_one_address_is_refused(self) -> None:
        """Append-only, for the reason the registry is (SPEC §6.3 A12)."""
        store = TranscriptStore(mode=RECORD)
        base = Transcript("call/x", "p", "m", "b", fixed_payload(0, ()))
        store.put(base)
        with pytest.raises(ProposalError, match="append-only"):
            store.put(Transcript("call/x", "p", "m", "b", fixed_payload(1, ())))

    def test_storing_an_identical_payload_twice_is_not_an_error(self) -> None:
        store = TranscriptStore(mode=RECORD)
        transcript = Transcript("call/x", "p", "m", "b", fixed_payload(0, ()))
        store.put(transcript)
        store.put(transcript)
        assert len(store) == 1

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        store = TranscriptStore(mode=RECORD)
        for index in range(3):
            store.put(
                Transcript(
                    f"call/{index}",
                    "p",
                    "m",
                    f"brief {index}",
                    fixed_payload(index, ()),
                )
            )
        path = tmp_path / "transcripts.json"
        store.save(path)
        loaded = TranscriptStore.load(path)
        assert loaded.addresses() == store.addresses()
        assert all(
            loaded.get(a).payload == store.get(a).payload for a in store.addresses()
        )

    def test_a_file_from_another_address_scheme_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The same refusal ``EmpiricalTable.load`` makes, for the same reason."""
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"version": "transcript/0", "calls": []}))
        with pytest.raises(ProposalError, match="address scheme"):
            TranscriptStore.load(path)


class TestAddressingIsDeterministic:
    """An address is a pure function of the request, in any process."""

    def test_the_same_request_addresses_identically(self) -> None:
        assert len({_address() for _ in range(20)}) == 1

    @pytest.mark.parametrize(
        "part, changed",
        [
            ("provider", lambda: _address(provider="other")),
            ("model", lambda: _address(model="other")),
            ("system", lambda: _address(system="other")),
            ("brief", lambda: _address(brief="other")),
            ("index", lambda: _address(index=1)),
        ],
    )
    def test_changing_any_part_changes_the_address(
        self, part: str, changed: Callable[[], str]
    ) -> None:
        """Every component of the request is load-bearing.

        A part that could change without moving the address would be a part a
        recorded response is silently reused across, which is the one way a
        replay can be *wrong* rather than merely absent.
        """
        assert changed() != _address(), f"{part} does not reach the address"

    def test_a_repeated_brief_addresses_differently_by_call_index(self) -> None:
        """Asking the same question twice is asking two questions.

        A system may legitimately re-propose after an experiment that moved
        nothing. Recording one answer for both calls would make the second
        depend on the first, which is the sort of coupling that is invisible
        until a run is replayed in a different order.
        """
        first = _layer(ScriptedProvider([fixed_payload(0, (1, 2, 3))] * 2))
        investigation = _investigation("S1")
        first.propose(investigation)
        first.propose(investigation)
        assert len(first.store) == 2

    def test_in_process_addresses_match_a_subprocess(self) -> None:
        """A ``Defect`` is a ``frozenset``; this is the arm that notices.

        Set iteration order is per-process, so if ``render_brief`` ever stops
        rendering a defect in canonical order, the corpus replays only on the
        machine that recorded it. An in-process loop cannot see that.
        """
        sys.path.insert(0, str(CHILD.parent))
        try:
            import transcript_child
        finally:
            sys.path.pop(0)
        completed = subprocess.run(
            [sys.executable, str(CHILD)], capture_output=True, text=True, check=True
        )
        expected = "".join(
            f"{name} {address}\n"
            for name, address in transcript_child.addresses().items()
        )
        assert completed.stdout == expected


class TestTheProposalLayer:
    """What a research system actually holds."""

    def test_a_proposal_decodes_to_a_licensed_structure(self) -> None:
        layer = _layer(ScriptedProvider([fixed_payload(0, (10, 20, 30))]))
        proposal = layer.propose(_investigation("S1"))
        AGENT_GRAMMAR.validate_defect(proposal.program_edit)
        assert proposal.address.startswith("call/")

    def test_the_layer_carries_no_number_of_its_own(self) -> None:
        """A ``Proposal`` is structure, prose and provenance. Nothing else."""
        layer = _layer(ScriptedProvider([fixed_payload(0, (1, 2, 3))]))
        proposal = layer.propose(_investigation("S1"))
        assert set(type(proposal).__slots__) == {
            "program_edit",
            "name",
            "rationale",
            "address",
        }

    def test_a_model_chosen_name_is_slugged(self) -> None:
        """A ``HypothesisId`` ends up in paths and orderings, so the framework
        decides which characters it may carry."""
        payload = fixed_payload(0, (1, 2, 3), name="Self  Excitation!! / v2")
        layer = _layer(ScriptedProvider([payload]))
        assert layer.propose(_investigation("S1")).name == "self__excitation_____v2"

    def test_an_empty_name_becomes_a_readable_default(self) -> None:
        payload = fixed_payload(0, (1, 2, 3), name="   ")
        layer = _layer(ScriptedProvider([payload]))
        assert layer.propose(_investigation("S1")).name == "proposal"

    def test_the_rationale_survives_unchanged(self) -> None:
        """Prose is recorded and never scored (SPEC F8), so it is not touched."""
        text = "The autocorrelation is high; excitation would produce that."
        payload = fixed_payload(0, (1, 2, 3), rationale=text)
        layer = _layer(ScriptedProvider([payload]))
        assert layer.propose(_investigation("S1")).rationale == text

    def test_two_layers_over_one_investigation_agree_exactly(self) -> None:
        investigation = _investigation("S1", entertained=("hawkes",))
        payload = fixed_payload(2, (5, 6, 7))
        first = _layer(ScriptedProvider([payload])).propose(investigation)
        second = _layer(ScriptedProvider([payload])).propose(investigation)
        assert first == second

    def test_a_provider_that_refuses_propagates_rather_than_proposing_nothing(
        self,
    ) -> None:
        """A refusal is an outcome, and the caller decides what to do about it."""
        layer = _layer(ScriptedProvider([]))
        with pytest.raises(ProviderError, match="only 0 were scripted"):
            layer.propose(_investigation("S1"))

    def test_replaying_a_recorded_run_needs_no_provider_at_all(self) -> None:
        """The point of the split: a replay does not touch a backend.

        The second layer is handed a provider that raises if called, so a
        replayed run that reached for it would fail loudly rather than quietly
        producing the same answer for a different reason.
        """
        investigation = _investigation("S1")
        recorded = _layer(ScriptedProvider([fixed_payload(0, (1, 2, 3))]))
        original = recorded.propose(investigation)

        class Refuses:
            id = "scripted"
            model = "scripted/1"

            def complete(
                self, system: str, brief: str, schema: object
            ) -> dict[str, object]:
                raise AssertionError("a replay must not call the provider")

        replayed = ProposalLayer(
            Refuses(),
            AGENT_GRAMMAR,
            TranscriptStore({t.address: t for t in recorded.store}, mode=REPLAY),
        )
        assert replayed.propose(investigation) == original


@lru_cache(maxsize=1)
def _sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:  # pragma: no cover - dependency is declared
        return False
    return True


class TestTheAnthropicProvider:
    """What can be asserted about the live backend without credentials."""

    def test_it_can_be_constructed_and_addressed_without_credentials(self) -> None:
        """A replay must be possible where no key exists, so identity is lazy.

        The client is built on the first call, not in ``__init__``, which is what
        lets an address be computed -- and a recorded transcript replayed -- in an
        environment that could not make the call in the first place.
        """
        from sciagent.systems.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider()
        assert provider.id == "anthropic"
        assert provider.model == "claude-opus-5"
        assert call_address(
            provider=provider.id,
            model=provider.model,
            system="s",
            brief="b",
            schema=SCHEMA,
            index=0,
        ).startswith("call/")

    def test_the_sdk_is_a_declared_dependency(self) -> None:
        assert _sdk_available(), "anthropic is declared in pyproject but not installed"
