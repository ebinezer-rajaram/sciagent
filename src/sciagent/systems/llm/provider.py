"""The proposal layer: what a model is asked, and what the framework does with it.

:class:`Provider` is the one thing an LLM backend has to implement, and
:class:`ProposalLayer` is what a research system actually holds. The split
matters: everything reproducible -- addressing, replay, decoding, validation --
lives in the layer and is exercised by every backend, so a scripted provider and
a live one travel exactly the same code path. A backend that could take a
shortcut around the layer would be a backend whose results are not comparable.

Nothing here writes a number. A provider returns a payload of integers and
strings; the layer turns it into a :class:`~sciagent.core.edits.Defect`; the
caller hands that to :meth:`~sciagent.systems.base.Investigation.propose`, which
derives the prior from the grammar's code length and the predictions from the
table. The chain from model output to posterior contains no step at which a
value the model chose becomes a value the framework reports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import InvalidEditError, MalformedProposalError
from sciagent.systems.base import Investigation
from sciagent.systems.llm.encoding import (
    Memory,
    MenuEntry,
    ProposalDraft,
    decode,
    draft_from_payload,
    render_brief,
    structural_menu,
    tool_schema,
)
from sciagent.systems.llm.transcripts import (
    Completion,
    TranscriptStore,
    call_address,
)

__all__ = ["Proposal", "ProposalLayer", "Provider"]


@runtime_checkable
class Provider(Protocol):
    """A backend that turns a brief into a schema-conforming payload.

    One method, so a live model and a scripted stand-in are interchangeable and
    the comparison between a recorded run and a replayed one is not mediated by
    different plumbing.
    """

    @property
    def id(self) -> str:
        """Return a stable identifier for this backend, e.g. ``"anthropic"``.

        Part of every transcript address, so changing it invalidates recorded
        calls -- which is correct: a payload produced by a different backend is
        not the same artefact.
        """
        ...

    @property
    def model(self) -> str:
        """Return the model identifier. Also part of the address, for the same
        reason."""
        ...

    @property
    def settings(self) -> str:
        """Return everything provider-side that could change the answer.

        Rendered as one stable string, and part of every address. A backend run
        at a different reasoning effort, or with a lower output ceiling, is
        asking its model a different question; without this the two share an
        address and the second is either refused as a conflicting recording or
        silently served the first one's answer.

        Empty is a legitimate value, and means the backend has nothing that
        varies. It is not defaulted, so that a new backend has to decide.
        """
        ...

    def complete(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> Completion:
        """Return a :class:`~sciagent.systems.llm.transcripts.Completion`.

        The payload must conform to ``schema``; the provenance says what produced
        it and may be empty. The two are returned together rather than the
        provenance being read back off the provider afterwards, so that a
        backend needs no per-call state and a completion cannot be paired with
        the wrong run's metadata.

        Raises :class:`~sciagent.core.errors.ProviderError` if nothing could be
        produced at all -- a refusal, a transport failure, a response carrying no
        tool call. That is a legitimate outcome for a research system to have and
        is distinct from a payload that does not decode.
        """
        ...


@dataclass(frozen=True, slots=True)
class Proposal:
    """A decoded, grammar-checked proposal, ready to be entertained.

    Carries the structure and the prose, and no number. ``address`` is the
    transcript address the draft came from, which is what lets a claim about this
    proposal be traced back to the exact model call that produced it.
    """

    program_edit: Defect
    name: str
    rationale: str
    address: str


class ProposalLayer:
    """Turns an investigation into a proposal, reproducibly.

    Holds the grammar's structural menu, the transcript store and the provider.
    Guarantees that every proposal it returns is licensed by the grammar it was
    built with, that the same investigation state produces the same transcript
    address in any process, and that no live call happens unless the store is in
    record mode.

    The call counter is per layer instance and advances on every :meth:`propose`,
    so two identical briefs within one investigation address differently. A
    system that asks the same question twice is asking two questions; recording
    one answer for both would make the second call's result depend on the first.
    """

    __slots__ = (
        "_calls",
        "_grammar",
        "_memory",
        "_menu",
        "_provider",
        "_store",
        "_system",
    )

    def __init__(
        self,
        provider: Provider,
        grammar: EditGrammar,
        store: TranscriptStore,
        *,
        system_prompt: str = "",
        memory: Memory = Memory.BOTH,
    ) -> None:
        self._provider = provider
        self._grammar = grammar
        self._menu = structural_menu(grammar)
        self._store = store
        self._system = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._memory = memory
        self._calls = 0

    @property
    def menu(self) -> tuple[MenuEntry, ...]:
        """Return the structural menu proposals are drawn from."""
        return self._menu

    @property
    def memory(self) -> Memory:
        """Return how the run so far is represented in this layer's briefs.

        SPEC §11 item 13's ablation axis. It reaches the transcript address
        through the brief, so two layers that differ only in it address
        differently and cannot resolve each other's recorded calls -- which is
        what keeps a V3 corpus and a V4 corpus from contaminating one another.
        """
        return self._memory

    @property
    def store(self) -> TranscriptStore:
        """Return the transcript store, for saving after a recording run."""
        return self._store

    @property
    def calls(self) -> int:
        """Return how many proposals have been requested."""
        return self._calls

    def propose(self, investigation: Investigation) -> Proposal:
        """Return one proposal for the state ``investigation`` is in.

        Raises :class:`~sciagent.core.errors.TranscriptMissError` when replaying
        a call that was never recorded, and
        :class:`~sciagent.core.errors.MalformedProposalError` when the payload
        does not denote a structure the grammar licenses. Neither is caught here:
        a system that wants to carry on without a proposal should decide that
        itself rather than have the layer decide it silently.
        """
        brief = render_brief(investigation, self._menu, memory=self._memory)
        schema = tool_schema(self._menu)
        address = call_address(
            provider=self._provider.id,
            model=self._provider.model,
            settings=self._provider.settings,
            system=self._system,
            brief=brief,
            schema=schema,
            index=self._calls,
        )
        self._calls += 1
        transcript = self._store.resolve(
            address,
            lambda: self._provider.complete(self._system, brief, schema),
            provider=self._provider.id,
            model=self._provider.model,
            brief=brief,
            settings=self._provider.settings,
        )
        draft = draft_from_payload(transcript.payload)
        return self._build(draft, address)

    def _build(self, draft: ProposalDraft, address: str) -> Proposal:
        """Decode a draft and check it against the grammar.

        ``validate_defect`` raises :class:`~sciagent.core.errors.InvalidEditError`,
        a ``GrammarError``, for a defect whose edits are each licensed but which
        conflict -- two on one target, whose compiled family would be ambiguous.
        ``decode`` has already refused everything else it checks, so this is the
        one way a draft reaches here licensed edit by edit and invalid as a
        whole. It is re-raised as the error this method's caller documents,
        because "does not denote a structure the grammar licenses" is precisely
        what it is, and because ``Hybrid._propose_once`` catches that and not
        ``GrammarError``. Until 2026-08-18 it escaped and stopped item 15's
        V3/S11 mid-cell.
        """
        program_edit = decode(self._grammar, self._menu, draft)
        try:
            self._grammar.validate_defect(program_edit)
        except InvalidEditError as error:
            raise MalformedProposalError(str(error)) from error
        return Proposal(
            program_edit=program_edit,
            name=_slug(draft.name),
            rationale=draft.rationale,
            address=address,
        )


def _slug(name: str) -> str:
    """Return a hypothesis-id-safe rendering of a model-chosen name.

    A model writes prose, and a ``HypothesisId`` ends up in file paths, claim
    ids and sorted orderings, so the characters it may carry are the
    framework's decision and not the model's. Anything outside a conservative
    set becomes an underscore; an empty result becomes ``"proposal"`` rather
    than an id that sorts before everything and reads as absent.
    """
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in name.strip().lower()
    ).strip("_")
    return cleaned[:48] or "proposal"


#: What the model is told its job is. Deliberately short and deliberately about
#: the *division of labour*: it names the one thing the model decides (which
#: structure) and the things it does not (which experiment to run, what anything
#: is worth). SPEC F5 assigns experiment selection within a fixed space to
#: conventional methods, and a prompt that invited the model to opine on it would
#: be inviting an answer the framework then has to ignore.
DEFAULT_SYSTEM_PROMPT = """\
You are proposing structure for a scientific investigation.

An executable programme generates the data. Something has been changed in it,
and the change is one of the structures listed in the brief. Conventional
methods have already done the parts that are theirs: the posterior over the
hypotheses entertained so far, and the posterior predictive check that says
whether those hypotheses explain what was observed.

Your job is to choose which structure to entertain next, given what has been
observed and what the existing hypotheses fail to explain. Choose the structure
whose mechanism would produce the residual you can see -- not the one that is
most complex, and not the one that is most familiar.

You choose a structure and, for each of its parameters, an index into that
parameter's grid. You do not choose experiments, and you never state a
probability, a plausibility, a score or any other number: those are computed
from your proposal by the framework, and there is no field in which you could
write one.

Give a short rationale naming the observation that motivated the choice.\
"""
