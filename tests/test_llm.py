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
from dataclasses import fields
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
from sciagent.core.edits import AddDependency, Defect, EditGrammar, canonical
from sciagent.core.errors import (
    EditNotInGrammarError,
    InvalidEditError,
    MalformedProposalError,
    OffGridParameterError,
    ProposalError,
    ProviderError,
    ProviderUnavailableError,
    TranscriptMissError,
    UnknownParameterError,
)
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.llm import (
    RECORD,
    REPLAY,
    Completion,
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
    settings: str = "",
    system: str = "s",
    brief: str = "b",
    index: int = 0,
) -> str:
    """Return an address for a fixed request, with one part optionally changed."""
    return call_address(
        provider=provider,
        model=model,
        settings=settings,
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
                lambda: Completion(fixed_payload(0, ())),
                provider="p",
                model="m",
                brief="b",
            )

    def test_record_fills_a_miss_and_counts_it(self) -> None:
        store = TranscriptStore(mode=RECORD)
        payload = fixed_payload(0, ())
        store.resolve(
            "call/x", lambda: Completion(payload), provider="p", model="m", brief="b"
        )
        assert store.misses == 1
        assert store.get("call/x").payload == payload

    def test_a_replayed_call_reports_no_miss(self) -> None:
        """What a reproducibility check asserts: it replayed, it did not re-derive."""
        store = TranscriptStore(mode=RECORD)
        store.resolve(
            "call/x",
            lambda: Completion(fixed_payload(0, ())),
            provider="p",
            model="m",
            brief="b",
        )
        replayed = TranscriptStore({t.address: t for t in store}, mode=REPLAY)
        replayed.resolve(
            "call/x",
            lambda: Completion(fixed_payload(1, ())),
            provider="p",
            model="m",
            brief="b",
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

    def test_provenance_survives_a_round_trip(self, tmp_path: Path) -> None:
        """What produced an answer is part of the artefact, not of its identity.

        A corpus that recorded which binary answered and then lost it on the
        first save would be worse than one that never claimed to: the field
        would read as evidence while being empty.
        """
        store = TranscriptStore(mode=RECORD)
        store.put(
            Transcript(
                "call/x",
                "p",
                "m",
                "b",
                fixed_payload(0, ()),
                {"claude_code_version": "2.1.233", "served_models": "claude-opus-5"},
            )
        )
        path = tmp_path / "transcripts.json"
        store.save(path)
        assert TranscriptStore.load(path).get("call/x").provenance == {
            "claude_code_version": "2.1.233",
            "served_models": "claude-opus-5",
        }

    def test_a_transcript_with_no_provenance_round_trips_too(
        self, tmp_path: Path
    ) -> None:
        """Nothing to declare stays nothing, rather than becoming a null."""
        store = TranscriptStore(mode=RECORD)
        store.put(Transcript("call/x", "p", "m", "b", fixed_payload(0, ())))
        path = tmp_path / "transcripts.json"
        store.save(path)
        assert TranscriptStore.load(path).get("call/x").provenance == {}

    def test_saving_over_a_corpus_keeps_the_earlier_provenance(
        self, tmp_path: Path
    ) -> None:
        """``put``'s first-recording rule has to hold at the file boundary too.

        A fresh RECORD store that re-derived the same answers on a newer binary
        and saved over the corpus would otherwise rewrite what produced each one
        -- and the payloads being byte-identical, the diff would show only the
        edit nobody was reviewing.
        """
        path = tmp_path / "transcripts.json"
        payload = fixed_payload(0, ())
        first = TranscriptStore(mode=RECORD)
        first.put(Transcript("call/x", "p", "m", "b", payload, {"v": "2.1.233"}))
        first.save(path)

        second = TranscriptStore(mode=RECORD)
        second.put(Transcript("call/x", "p", "m", "b", payload, {"v": "2.9.999"}))
        second.save(path)

        assert TranscriptStore.load(path).get("call/x").provenance == {"v": "2.1.233"}

    def test_saving_fills_in_provenance_a_corpus_never_had(
        self, tmp_path: Path
    ) -> None:
        """Restoring the earlier value must not mean pinning an empty one.

        A corpus recorded before provenance existed should gain it on the next
        save rather than being frozen without it forever.
        """
        path = tmp_path / "transcripts.json"
        payload = fixed_payload(0, ())
        old = TranscriptStore(mode=RECORD)
        old.put(Transcript("call/x", "p", "m", "b", payload))
        old.save(path)

        fresh = TranscriptStore(mode=RECORD)
        fresh.put(Transcript("call/x", "p", "m", "b", payload, {"v": "2.1.233"}))
        fresh.save(path)

        assert TranscriptStore.load(path).get("call/x").provenance == {"v": "2.1.233"}

    def test_the_settings_a_call_was_made_under_are_stored(self) -> None:
        """Address-determining, therefore recorded, for the same reason as model.

        Two calls recorded at different efforts differ in address and would
        otherwise be indistinguishable in the file -- which is the question a
        reader opens a transcript to answer.
        """
        store = TranscriptStore(mode=RECORD)
        store.resolve(
            "call/x",
            lambda: Completion(fixed_payload(0, ())),
            provider="p",
            model="m",
            brief="b",
            settings="effort=low",
        )
        assert store.get("call/x").settings == "effort=low"

    def test_a_rerecording_that_agrees_on_the_answer_agrees(self) -> None:
        """Payloads decide whether two recordings conflict; provenance does not.

        A store that compared provenance would refuse a re-recording which
        reproduced the answer exactly on a newer binary -- treating agreement as
        conflict, which is the opposite of what an append-only guarantee is for.
        The first recording's provenance is the one kept.
        """
        store = TranscriptStore(mode=RECORD)
        payload = fixed_payload(0, ())
        store.put(Transcript("call/x", "p", "m", "b", payload, {"v": "2.1.233"}))
        store.put(Transcript("call/x", "p", "m", "b", payload, {"v": "2.9.999"}))
        assert store.get("call/x").provenance == {"v": "2.1.233"}
        assert len(store) == 1

    def test_a_file_from_another_address_scheme_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The same refusal ``EmpiricalTable.load`` makes, for the same reason."""
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"version": "transcript/0", "calls": []}))
        with pytest.raises(ProposalError, match="address scheme"):
            TranscriptStore.load(path)

    def test_saving_over_a_corpus_may_not_drop_a_call(self, tmp_path: Path) -> None:
        """``save`` replaces the whole file, so it has to check what it replaces.

        Two recording sessions loading different snapshots of one path is the
        real shape of this: each holds calls the other never saw, and the second
        to save would silently delete the first's. ``put`` guards the in-process
        store and cannot see a file.
        """
        path = tmp_path / "corpus.json"
        first = TranscriptStore(mode=RECORD)
        first.put(Transcript("call/a", "p", "m", "brief a", fixed_payload(0, ())))
        first.save(path)

        second = TranscriptStore(mode=RECORD)
        second.put(Transcript("call/b", "p", "m", "brief b", fixed_payload(1, ())))
        with pytest.raises(ProposalError, match="would drop"):
            second.save(path)
        assert TranscriptStore.load(path).addresses() == ("call/a",)

    def test_saving_over_a_corpus_may_not_replace_an_answer(
        self, tmp_path: Path
    ) -> None:
        """Holding every address is not enough; the payloads must match too.

        Checking only the address set left the same hole one level in. ``put``
        refuses a changed payload at a known address in process, so a file
        boundary that did not would make the append-only guarantee depend on
        whether the two answers happened to arrive in one session.
        """
        path = tmp_path / "corpus.json"
        first = TranscriptStore(mode=RECORD)
        first.put(Transcript("call/a", "p", "m", "brief a", fixed_payload(0, ())))
        first.save(path)

        second = TranscriptStore(mode=RECORD)
        second.put(Transcript("call/a", "p", "m", "brief a", fixed_payload(1, ())))
        with pytest.raises(ProposalError, match="would replace the response"):
            second.save(path)
        kept = TranscriptStore.load(path)
        assert kept.get("call/a").payload == first.get("call/a").payload

    def test_a_malformed_corpus_is_not_treated_as_holding_nothing(
        self, tmp_path: Path
    ) -> None:
        """A present-but-unreadable file may hold calls nobody can reconstruct.

        Swallowing the read failure would have made ``save`` truncate exactly
        the corpus that cannot be rebuilt. The one readable-failure that *may*
        be replaced is an older address scheme, because every call in such a
        file misses anyway -- and that case is covered by the test above it.
        """
        path = tmp_path / "corpus.json"
        recorded = TranscriptStore(mode=RECORD)
        recorded.put(Transcript("call/a", "p", "m", "brief a", fixed_payload(0, ())))
        recorded.save(path)
        # Same file, one field removed: version-correct, so `load` gets past the
        # address-scheme check and fails on the entry itself.
        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["calls"][0]["brief"]
        path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")
        store = TranscriptStore(mode=RECORD)
        store.put(Transcript("call/new", "p", "m", "brief", fixed_payload(0, ())))
        with pytest.raises(KeyError):
            store.save(path)
        assert "call/a" in path.read_text(encoding="utf-8"), (
            "the malformed corpus was overwritten rather than left alone"
        )

    def test_saving_a_superset_is_how_a_corpus_grows(self, tmp_path: Path) -> None:
        """The check refuses loss, not addition; recording has to stay possible."""
        path = tmp_path / "corpus.json"
        store = TranscriptStore(mode=RECORD)
        store.put(Transcript("call/a", "p", "m", "brief a", fixed_payload(0, ())))
        store.save(path)

        grown = TranscriptStore.load(path, mode=RECORD)
        grown.put(Transcript("call/b", "p", "m", "brief b", fixed_payload(1, ())))
        grown.save(path)
        assert TranscriptStore.load(path).addresses() == ("call/a", "call/b")

    def test_a_corpus_is_written_with_unix_newlines(self, tmp_path: Path) -> None:
        """A corpus is the reproducible artefact, so its bytes are the point.

        Text mode translates every newline to ``os.linesep`` on write, so without
        pinning this the same corpus recorded here and on the Ubuntu half of this
        project would differ byte for byte while replaying identically. A
        round-trip assertion cannot see it: reading translates it back.
        """
        path = tmp_path / "corpus.json"
        store = TranscriptStore(mode=RECORD)
        store.put(Transcript("call/a", "p", "m", "brief a", fixed_payload(0, ())))
        store.save(path)
        assert b"\r\n" not in path.read_bytes()


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
        # `check=True` raised `CalledProcessError` with the child's stderr
        # captured and never shown, so a child that died reported only its exit
        # status. That happened once under `-n 4` on 2026-08-19 and left nothing
        # to diagnose from. The status is still asserted; what changed is that
        # the failure now carries the reason.
        completed = subprocess.run(
            [sys.executable, str(CHILD)], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, (
            f"{CHILD.name} exited {completed.returncode}\n"
            f"--- stderr ---\n{completed.stderr}\n"
            f"--- stdout ---\n{completed.stdout}"
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

    @pytest.mark.parametrize(
        "raised",
        [
            InvalidEditError("two edits on one target"),
            EditNotInGrammarError("this grammar does not license that type"),
            UnknownParameterError("parameter names disagree with the construct"),
            OffGridParameterError("value is not a grid point"),
        ],
    )
    def test_every_grammar_refusal_reaches_the_caller_as_malformed(
        self, raised: Exception
    ) -> None:
        """``_build``'s guard was one of the four errors it stands in front of.

        ``ProposalLayer.propose`` documents itself as raising
        ``MalformedProposalError`` "when the payload does not denote a structure
        the grammar licenses", and ``Hybrid._propose_once`` catches that and not
        ``GrammarError``. So any ``GrammarError`` the guard misses escapes
        ``investigate`` and stops a campaign -- which is exactly how
        ``InvalidEditError`` stopped item 15's V3/S11 cell on 2026-08-18, and the
        guard added then named that one error rather than the family behind it.

        The three additions are not reachable through the public path today: the
        menu is built from the same frozen ``EditGrammar`` the layer validates
        against, so a licensed decode cannot fail validation. That is a property
        of how the layer is wired and not a promise the guard makes, and the
        wiring is what a menu/grammar divergence would break -- ``decode`` itself
        carries an error for that case ("menu and grammar have come apart"). The
        grammar here therefore refuses at ``validate_defect`` directly, which is
        the seam the guard actually sits on.
        """

        class _RefusingGrammar(EditGrammar):
            def validate_defect(self, defect: Defect) -> None:
                raise raised

        grammar = _RefusingGrammar(
            **{
                field.name: getattr(AGENT_GRAMMAR, field.name)
                for field in fields(AGENT_GRAMMAR)
            }
        )
        layer = ProposalLayer(
            ScriptedProvider([fixed_payload(0, (10, 20, 30))]),
            grammar,
            TranscriptStore(mode=RECORD),
        )
        with pytest.raises(MalformedProposalError):
            layer.propose(_investigation("S1"))

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

            settings = ""

            def complete(self, system: str, brief: str, schema: object) -> Completion:
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


def _raising_client(failure: Exception) -> Any:
    """Return a stand-in Anthropic client whose ``messages.create`` raises.

    Injected through ``AnthropicProvider(client=...)``, so the transport failure
    arrives from exactly where a real one would -- the ``create`` call -- without
    a socket being opened or a credential being read.
    """

    class _Messages:
        def create(self, **_kwargs: Any) -> Any:
            raise failure

    class _Client:
        messages = _Messages()

    return _Client()


#: Every way the Messages API can fail to deliver a payload, by SDK class name.
#:
#: The last two are the point of the list. ``APIStatusError`` is the base of the
#: four status errors above it and ``AnthropicError`` is the SDK's root, so a
#: guard that converts *those* converts their subclasses by construction --
#: whereas a guard enumerating only the leaves converts nothing else. Naming the
#: leaves as well is not redundancy: ``OverloadedError`` is what the SDK actually
#: raises for the 529 a long campaign dies of, and it is a *sibling* of
#: ``InternalServerError`` rather than a subclass, so a list that reasoned from
#: the 5xx status alone would miss it.
ANTHROPIC_FAILURES: tuple[str, ...] = (
    "RateLimitError",
    "OverloadedError",
    "InternalServerError",
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "APIStatusError",
    "AnthropicError",
)


def _anthropic_failure(name: str) -> Exception:
    """Return a real instance of the named ``anthropic`` exception."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    statuses = {
        "RateLimitError": 429,
        "OverloadedError": 529,
        "InternalServerError": 500,
        "AuthenticationError": 401,
        "APIStatusError": 500,
    }
    build: Callable[..., Exception] = getattr(anthropic, name)
    if name in statuses:
        return build(
            name,
            response=httpx.Response(statuses[name], request=request),
            body=None,
        )
    if name in {"APIConnectionError", "APITimeoutError"}:
        return build(request=request)
    return anthropic.AnthropicError(name)


#: Every way an Agent SDK session can die, by class name.
#:
#: ``ClaudeSDKError`` is the SDK's root and is here for the same reason
#: ``AnthropicError`` is above. Plain ``Exception`` is here for a different and
#: worse reason: ``claude_agent_sdk`` raises it bare from seven sites in
#: ``_internal/query.py``, and one of them is what stopped item 15's V4/S11 cell
#: (``Exception: Claude Code returned an error result: success``). A guard on the
#: SDK root converts none of those seven.
AGENT_SDK_FAILURES: tuple[str, ...] = (
    "CLINotFoundError",
    "ProcessError",
    "CLIJSONDecodeError",
    "CLIConnectionError",
    "ClaudeSDKError",
    "Exception",
)


def _agent_sdk_failure(name: str) -> Exception:
    """Return a real instance of the named ``claude_agent_sdk`` exception."""
    import claude_agent_sdk

    if name == "Exception":
        # Verbatim from item 15's third stoppage, recorded in docs/DECISIONS.md
        # (2026-08-18) as "transient on one observation".
        return Exception("Claude Code returned an error result: success")
    if name == "ProcessError":
        return claude_agent_sdk.ProcessError("claude exited", exit_code=1)
    if name == "CLIJSONDecodeError":
        return claude_agent_sdk.CLIJSONDecodeError("not json", ValueError("boom"))
    build: Callable[..., Exception] = getattr(claude_agent_sdk, name)
    return build(name)


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
            settings=provider.settings,
            system="s",
            brief="b",
            schema=SCHEMA,
            index=0,
        ).startswith("call/")

    def test_a_ceiling_that_raises_is_not_in_the_address(self) -> None:
        """One rule for both backends: a bound that aborts is not one that alters.

        ``max_tokens`` exhaustion raises rather than returning a shorter answer,
        so no two ceilings can produce different *recorded* payloads. Addressing
        it would make a headroom bump invalidate every replay and re-bill a
        recorded matrix for something no answer depended on -- the same reasoning
        that keeps the Agent SDK's turn ceiling out.
        """
        from sciagent.systems.llm.anthropic_provider import AnthropicProvider

        assert AnthropicProvider(max_tokens=16000).settings == "effort=high"
        assert AnthropicProvider(max_tokens=64000).settings == "effort=high"
        assert AnthropicProvider(effort="low").settings == "effort=low"

    def test_the_sdk_is_a_declared_dependency(self) -> None:
        assert _sdk_available(), "anthropic is declared in pyproject but not installed"

    @pytest.mark.parametrize("failure_name", ANTHROPIC_FAILURES)
    def test_a_transport_failure_becomes_a_provider_unavailable_error(
        self, failure_name: str
    ) -> None:
        """A backend that cannot be reached raises, and says so in its type.

        ``ProviderError`` *used* to be documented as covering "a refusal, a
        transport failure, or a response carrying no tool call". The middle
        clause was the defect rather than the contract, and it is gone: reaching
        the model and getting no proposal is an outcome to record, while never
        reaching it at all is not. So this asserts
        ``ProviderUnavailableError`` -- see that class, and
        ``tests/test_hybrid.py``'s
        ``test_an_unreachable_provider_stops_the_run_rather_than_scoring_it``
        for why the distinction is load-bearing rather than tidy.

        Before the guard existed the Messages API call was unguarded: a 429, a
        5xx or a dropped connection left the SDK's own exception to propagate.
        It is not a ``SciAgentError``, so it crossed ``run_matrix``'s
        ``except SciAgentError`` *and* its
        ``KeyboardInterrupt`` handler, and neither checkpoints -- taking the
        in-flight replicate's transcripts with it, which that runner's docstring
        names as the unrecoverable case.

        The list includes ``APIStatusError`` and ``AnthropicError`` themselves,
        not only their subclasses. That direction is the one that establishes
        anything: converting a base implies its leaves, while converting five
        leaves implies nothing about the base, and a guard enumerating exactly
        the leaves a test names is the wrong implementation such a test would
        wave through.

        ``__cause__`` is asserted here rather than in a test of its own, so that
        the chaining is checked on every failure in the list instead of on one
        chosen representative.
        """
        from sciagent.systems.llm.anthropic_provider import AnthropicProvider

        failure = _anthropic_failure(failure_name)
        provider = AnthropicProvider(client=_raising_client(failure))
        with pytest.raises(
            ProviderUnavailableError, match="could not be completed"
        ) as caught:
            provider.complete("s", "b", SCHEMA)
        # Chained, not swallowed. The tier this lands in says only that nothing
        # was proposed; *which* failure it was decides whether a stopped campaign
        # can be resumed at once or not at all -- a 429 waits, an
        # AuthenticationError does not -- so the original has to survive.
        assert caught.value.__cause__ is failure


@lru_cache(maxsize=1)
def _agent_sdk_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:  # pragma: no cover - dependency is declared
        return False
    return True


#: A payload that decodes against ``AGENT_GRAMMAR``: structure 0 there is an
#: ``AddDependency`` taking three parameters, so an empty grid tuple would be
#: refused by the decoder before any of this backend's behaviour was reached.
AGENT_SDK_PROPOSAL = fixed_payload(0, (1, 2, 3))


def _clear_contaminants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset every variable the provider refuses to run alongside.

    Without this the suite would pass or fail according to what the developer
    happened to have exported -- an ``ANTHROPIC_BASE_URL`` on one machine and not
    another is exactly the contamination the provider exists to refuse.
    """
    from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _model_usage(cost: float = 0.01, canonical: str | None = None) -> Any:
    """Return one per-model usage entry, in the SDK's camelCase wire shape."""
    entry: dict[str, Any] = {
        "inputTokens": 100,
        "outputTokens": 10,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "costUSD": cost,
        "contextWindow": 1_000_000,
        "maxOutputTokens": 128_000,
    }
    if canonical is not None:
        entry["canonicalModel"] = canonical
    return entry


def _result(**overrides: Any) -> Any:
    """Return a successful ``ResultMessage``, with fields optionally changed."""
    from claude_agent_sdk import ResultMessage

    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test-session",
        "structured_output": AGENT_SDK_PROPOSAL,
        "model_usage": {"claude-opus-5": _model_usage()},
    }
    fields.update(overrides)
    return ResultMessage(**fields)


def _stub_runner(
    result: Any,
    captured: dict[str, Any] | None = None,
    *,
    manifest: dict[str, Any] | None = None,
) -> Any:
    """Return a stand-in for ``claude_agent_sdk.query`` that yields ``result``.

    Records what it was called with, so a test can assert on the request that
    *would* have been sent without a process being spawned to send it. When
    ``manifest`` is given, an ``init`` system event carrying it is yielded first,
    in the order a real session emits them.
    """
    from claude_agent_sdk import SystemMessage

    async def runner(*, prompt: str, options: Any) -> Any:
        if captured is not None:
            captured["prompt"] = prompt
            captured["options"] = options
        if manifest is not None:
            yield SystemMessage(subtype="init", data=manifest)
        yield result

    return runner


class TestTheAgentSdkProvider:
    """The subscription-authenticated backend, exercised without a subscription.

    Everything here injects a stand-in for ``query``, so no process is spawned,
    no credential is read and no quota is spent. What cannot be asserted offline
    -- that a real session accepts this option set -- is a live check, not a test.
    """

    def test_it_can_be_constructed_and_addressed_without_credentials(self) -> None:
        """Same lazy-construction property the Messages API backend has.

        The SDK is imported on the first call rather than at ``__init__``, so an
        address can be computed, and a transcript replayed, where the SDK and the
        credential are both absent.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        provider = AgentSdkProvider()
        assert provider.id == "claude-agent-sdk"
        assert provider.model == "claude-opus-5"
        assert _address(provider=provider.id, model=provider.model).startswith("call/")

    def test_the_two_backends_address_one_request_differently(self) -> None:
        """The corpus-separation property, asserted rather than assumed.

        Same model, same brief, same schema: the only difference is which door
        the call went through, and that is enough to make the addresses disagree.
        A corpus recorded through one backend therefore cannot resolve a call
        recorded through the other, which is what stops the two being mixed in
        a single recorded run without anyone noticing.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider
        from sciagent.systems.llm.anthropic_provider import AnthropicProvider

        messages_api = AnthropicProvider()
        agent_sdk = AgentSdkProvider()
        assert messages_api.model == agent_sdk.model
        assert _address(provider=messages_api.id, model=messages_api.model) != _address(
            provider=agent_sdk.id, model=agent_sdk.model
        )

    def test_two_efforts_do_not_share_one_address(self) -> None:
        """The defect this backend's `settings` exists to prevent.

        Before `settings` entered the address, two providers differing only in
        reasoning effort hashed identically. The consequence was not cosmetic:
        `TranscriptStore.put` compares payloads, so recording both either raised
        a conflicting-answer error that named nothing about effort, or -- worse,
        on replay -- served the high-effort answer to the low-effort run without
        anything noticing. The two ask their model a different question and must
        address differently.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        high = AgentSdkProvider(effort="high")
        low = AgentSdkProvider(effort="low")
        assert high.id == low.id and high.model == low.model
        assert _address(
            provider=high.id, model=high.model, settings=high.settings
        ) != _address(provider=low.id, model=low.model, settings=low.settings)

    def test_bypassing_the_environment_guard_addresses_differently(self) -> None:
        """An unguarded run must not be able to share a guarded run's address.

        ``require_subscription=False`` disables the contamination refusal, so an
        inherited variable the module itself calls out as changing the artefact
        can reach the model. That makes the answer depend on an input nothing
        records -- so the bypass goes in the address, and only when it is on, so
        ordinary runs address as they always did.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        guarded = AgentSdkProvider()
        unguarded = AgentSdkProvider(require_subscription=False)
        assert guarded.settings == "effort=high"
        assert unguarded.settings == "effort=high;unguarded"

    def test_it_records_which_binary_answered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provenance rides beside the payload, never inside the address.

        The version is worth keeping -- a corpus should say which binary produced
        it -- and must not be hashed, or a Claude Code auto-update would expire
        every recorded call on somebody else's release schedule.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(
            runner=_stub_runner(_result(), manifest={"claude_code_version": "2.1.233"})
        )

        completion = provider.complete("s", "b", SCHEMA)

        assert completion.payload == AGENT_SDK_PROPOSAL
        assert completion.provenance == {
            "claude_code_version": "2.1.233",
            "served_models": "claude-opus-5",
        }
        # And the address is indifferent to it.
        assert _address(
            provider=provider.id, model=provider.model, settings=provider.settings
        ) == _address(
            provider="claude-agent-sdk", model="claude-opus-5", settings="effort=high"
        )

    def test_a_session_that_reports_no_version_records_an_empty_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An older CLI that omits the field is not a reason to refuse the run.

        Unlike the model check, where absence of evidence is refused, a missing
        version costs only audit detail -- the answer is still the pinned model's.
        Recording an empty string says so honestly.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(runner=_stub_runner(_result(), manifest={}))
        assert provider.complete("s", "b", SCHEMA).provenance == {
            "claude_code_version": "",
            "served_models": "claude-opus-5",
        }

    def test_the_request_it_builds_carries_no_ambient_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hermeticity guarantee, which is this backend's whole difficulty.

        Claude Code reads ``~/.claude``, the project's ``.claude/``, ``CLAUDE.md``
        and its skills by default. Any of them would be an input to the model
        that the brief does not mention and the address therefore does not cover,
        which would make a recorded corpus replay only on the machine that
        recorded it. ``--bare`` cannot be used, because bare mode never reads the
        OAuth credential this backend authenticates with, so the guarantee is
        assembled field by field -- and this is the test that says so.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        captured: dict[str, Any] = {}
        provider = AgentSdkProvider(runner=_stub_runner(_result(), captured))

        provider.complete("SYSTEM", "BRIEF", SCHEMA)

        options = captured["options"]
        assert captured["prompt"] == "BRIEF"
        # A bare string replaces Claude Code's prompt; a preset dict would
        # prepend it and the brief would stop being the whole input.
        assert options.system_prompt == "SYSTEM"
        assert options.setting_sources == []
        assert options.tools == []
        # What the SDK documents for "no skills". Inert in practice -- no
        # --skills flag is emitted for either value -- so this pins the stated
        # intent, not the guarantee. The guarantee is tools=[] plus the replaced
        # system prompt, measured live at a 593-token total prompt.
        assert options.skills == []
        # Guards a dead end rather than a feature. CLAUDE_CODE_SIMPLE=1 is what
        # --bare sets, and setting it drops memory_paths from the session
        # manifest while apiKeySource stays "none" -- so it looks like bare
        # mode's context-skipping without bare mode's API-key requirement. It is
        # not: every turn then fails with no model call at all. Re-adding it
        # would break every live recording while the offline tests stayed green.
        assert options.env == {}
        assert options.mcp_servers == {}
        assert options.strict_mcp_config is True
        assert options.max_turns == 4  # measured: a real brief takes two
        assert options.model == "claude-opus-5"
        assert options.output_format == {"type": "json_schema", "schema": dict(SCHEMA)}

    @pytest.mark.parametrize(
        "variable",
        [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_SIMPLE",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        ],
    )
    def test_an_inherited_variable_that_would_change_the_run_refuses(
        self, monkeypatch: pytest.MonkeyPatch, variable: str
    ) -> None:
        """The option set covers what is sent; it cannot cover what is inherited.

        The SDK builds the child's environment as ``{**os.environ,
        **options.env}``, and a merge adds but never removes, so none of these
        can be unset from here -- only noticed. Each either redirects the account
        (billing the wrong one while appearing to succeed, the failure this
        backend was chosen to avoid) or changes the artefact itself under an
        address that does not cover it.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        monkeypatch.setenv(variable, "1")
        provider = AgentSdkProvider(runner=_stub_runner(_result()))
        with pytest.raises(ProviderError, match=variable):
            provider.complete("s", "b", SCHEMA)

    def test_a_turn_served_by_another_provider_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bedrock or Vertex serving the turn is a different account entirely.

        Belt and braces with the environment check: that one runs before the
        call and this one reads the evidence the response itself carries.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        usage = _model_usage()
        usage["provider"] = "bedrock"
        provider = AgentSdkProvider(
            runner=_stub_runner(_result(model_usage={"claude-opus-5": usage}))
        )
        with pytest.raises(ProviderError, match="bedrock"):
            provider.complete("s", "b", SCHEMA)

    def test_an_alias_whose_canonical_id_differs_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The raw usage key only echoes what the caller asked for.

        A provider pinned to ``"opus"`` must not be satisfied by usage keyed
        ``"opus"`` that priced as a different model: the key is the string the
        CLI was invoked with, and the canonical id is what actually served the
        turn. Accepting either would let the echo vouch for the substitution.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(
            model="opus",
            runner=_stub_runner(
                _result(model_usage={"opus": _model_usage(canonical="claude-sonnet-5")})
            ),
        )
        with pytest.raises(ProviderError, match="claude-sonnet-5"):
            provider.complete("s", "b", SCHEMA)

    def test_the_api_key_refusal_can_be_overridden_deliberately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal is a guard against an accident, not a prohibition."""
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
        provider = AgentSdkProvider(
            require_subscription=False, runner=_stub_runner(_result())
        )
        assert provider.complete("s", "b", SCHEMA).payload == AGENT_SDK_PROPOSAL

    def test_a_response_served_by_another_model_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An address names a model, so a substitute's answer is not recordable.

        The same reasoning as the Messages API backend's refusal to fall back to
        another model, applied to a backend that has more ways to serve one.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(
            runner=_stub_runner(
                _result(model_usage={"claude-sonnet-5": _model_usage()})
            )
        )
        with pytest.raises(ProviderError, match="not by 'claude-opus-5'"):
            provider.complete("s", "b", SCHEMA)

    def test_an_alias_that_resolved_to_the_pinned_model_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The canonical id counts as evidence, not only the raw key.

        The CLI keys usage by the model string it was invoked with and reports
        what pricing resolved that to. Demanding the raw key alone would refuse a
        run that in fact reached the right model.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(
            runner=_stub_runner(
                _result(model_usage={"opus": _model_usage(canonical="claude-opus-5")})
            )
        )
        assert provider.complete("s", "b", SCHEMA).payload == AGENT_SDK_PROPOSAL

    def test_a_response_with_no_per_model_usage_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absence of evidence is refused too, deliberately.

        A result carrying no per-model usage does not say the right model
        answered. Recording it would put an unverified claim in the provenance
        chain, which is worse than a run that stops.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(runner=_stub_runner(_result(model_usage=None)))
        with pytest.raises(ProviderError, match="no per-model usage"):
            provider.complete("s", "b", SCHEMA)

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"stop_reason": "refusal"}, "declined to answer"),
            ({"stop_reason": "max_tokens"}, "output ceiling"),
            (
                {"subtype": "error_during_execution", "is_error": True},
                "ended as 'error_during_execution'",
            ),
            ({"structured_output": None}, "no structured output"),
            ({"structured_output": [1, 2]}, "list where the schema"),
        ],
    )
    def test_an_unusable_session_raises_rather_than_returning_a_payload(
        self,
        monkeypatch: pytest.MonkeyPatch,
        overrides: dict[str, Any],
        expected: str,
    ) -> None:
        """Every way a session can fail to carry a proposal is an error here.

        A provider that returned something plausible on a failed session would
        put it in the corpus, where nothing downstream could tell it apart from
        an answer.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(runner=_stub_runner(_result(**overrides)))
        with pytest.raises(ProviderError, match=expected):
            provider.complete("s", "b", SCHEMA)

    def test_a_session_that_yields_no_result_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stream that ends without a result message records nothing."""
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)

        async def empty(*, prompt: str, options: Any) -> Any:
            return
            yield  # pragma: no cover - makes this an async generator

        provider = AgentSdkProvider(runner=empty)
        with pytest.raises(ProviderError, match="without a result message"):
            provider.complete("s", "b", SCHEMA)

    @pytest.mark.parametrize("failure_name", AGENT_SDK_FAILURES)
    def test_a_session_that_dies_becomes_a_provider_unavailable_error(
        self, monkeypatch: pytest.MonkeyPatch, failure_name: str
    ) -> None:
        """The same contract clause, for the backend item 15 actually recorded on.

        ``asyncio.run(drain())`` was unguarded, so every way the SDK reports a
        dead session -- CLI missing, child process gone, a stream that will not
        decode -- escaped as a non-``SciAgentError`` and stopped a campaign
        without checkpointing.

        The ``"Exception"`` case is the one that decides the shape of the guard,
        and it is not hypothetical. Item 15's third stoppage, on V4/S11 replicate
        13, was ``Exception: Claude Code returned an error result: success``,
        printed as a traceback rather than as the runner's designed "stopped
        after N replicate(s)"; ``docs/DECISIONS.md`` (2026-08-18) recorded it as
        transient on one observation. ``claude_agent_sdk`` raises bare
        ``Exception`` from seven sites in ``_internal/query.py``, so a guard on
        ``ClaudeSDKError`` -- the tidy answer, and the one the Messages backend
        can use -- converts none of them and leaves this exact stoppage escaping.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        failure = _agent_sdk_failure(failure_name)

        def runner(*, prompt: str, options: Any) -> Any:
            async def stream() -> Any:
                raise failure
                yield  # pragma: no cover - makes this an async generator

            return stream()

        provider = AgentSdkProvider(runner=runner)
        with pytest.raises(
            ProviderUnavailableError, match="could not be completed"
        ) as caught:
            provider.complete("s", "b", SCHEMA)
        assert caught.value.__cause__ is failure

    def test_a_framework_error_is_not_disguised_as_a_provider_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ceiling on that guard, which matters more than the floor.

        Widening far enough to catch a bare ``Exception`` risks converting *our*
        faults into a provider outcome, and a ``ProviderError`` is caught by
        ``Hybrid._propose_once`` and recorded as ``"refused"`` -- so a bug here
        would not stop a campaign, it would let one carry on and write a
        scientific outcome for it. That is strictly worse than the crash this
        change exists to prevent.

        A ``SciAgentError`` raised anywhere under the session must therefore come
        out unchanged. ``TranscriptMissError`` is the one to check with: it is
        the fault ``Hybrid`` deliberately does not catch, precisely so a broken
        replay cannot masquerade as a quietly worse result.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        failure = TranscriptMissError("no recording at this address")

        def runner(*, prompt: str, options: Any) -> Any:
            async def stream() -> Any:
                raise failure
                yield  # pragma: no cover - makes this an async generator

            return stream()

        provider = AgentSdkProvider(runner=runner)
        with pytest.raises(TranscriptMissError):
            provider.complete("s", "b", SCHEMA)

    def test_the_layer_records_a_proposal_through_this_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end: the payload decodes and lands under this backend's address.

        The layer is the thing that has to be indifferent to which backend it
        holds, so this asserts the same path the scripted and Messages API
        backends travel, with the transcript attributed to this one.
        """
        from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

        _clear_contaminants(monkeypatch)
        provider = AgentSdkProvider(runner=_stub_runner(_result()))
        store = TranscriptStore(mode=RECORD)
        layer = ProposalLayer(provider, AGENT_GRAMMAR, store)

        proposal = layer.propose(_investigation("S1"))

        assert proposal.program_edit == decode(
            AGENT_GRAMMAR, MENU, draft_from_payload(AGENT_SDK_PROPOSAL)
        )
        assert store.misses == 1
        (transcript,) = tuple(store)
        assert transcript.provider == "claude-agent-sdk"
        assert transcript.model == "claude-opus-5"
        assert transcript.address == proposal.address

    def test_the_sdk_is_a_declared_dependency(self) -> None:
        assert _agent_sdk_available(), (
            "claude-agent-sdk is declared in pyproject's dev group but not installed"
        )
