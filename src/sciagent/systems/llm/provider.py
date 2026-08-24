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

One qualification, because the absolute form is not quite true and the gap is
worth knowing rather than papering over: the model chooses its proposal's
*name*, that name becomes a ``HypothesisId``, and three sites break an exact
tie on that id lexicographically -- ``closed_world_score`` and
``leading_structure`` in :mod:`sciagent.eval.scoring`, and ``leader`` in
:mod:`sciagent.eval.campaign`. Every mass in those comparisons is
framework-derived, so this is influence on which of two equal readings is
reported and never a value the model set. No recorded tie has turned on it: a
sweep of all 1,120 rows of the section 9 campaign found no row where the truth
tied the leader and lost. Recorded so the sentence above is read as the strong
claim it is and not a wider one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import (
    GrammarError,
    MalformedProposalError,
    ProviderError,
    SystemConfigurationError,
)
from sciagent.systems.base import Investigation
from sciagent.systems.llm.encoding import (
    Memory,
    MenuEntry,
    ProposalDraft,
    decode,
    draft_from_payload,
    render_brief,
    render_menu_prefix,
    structural_menu,
    tool_schema,
)
from sciagent.systems.llm.transcripts import (
    Completion,
    TranscriptStore,
    call_address,
)

__all__ = [
    "DrawOutcome",
    "Proposal",
    "ProposalLayer",
    "Provider",
    "RefusingProvider",
    "SampleRecord",
    "slug_hypothesis_name",
]


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

        Raises :class:`~sciagent.core.errors.ProviderError` if the model was
        reached and its answer holds no proposal -- a refusal, an output ceiling,
        a response carrying no tool call. That is a legitimate outcome for a
        research system to have, and is distinct from a payload that does not
        decode.

        Raises :class:`~sciagent.core.errors.ProviderUnavailableError` if the
        model was **not** reached: a rate limit, a 5xx, a dropped connection, a
        dead session. Every backend owes this distinction, because the two have
        opposite consequences downstream -- the first is recorded and scored, the
        second stops the run -- and a backend that folded them together would put
        a throttled account's silence in the corpus as a scientific result.
        """
        ...


@dataclass(frozen=True, slots=True)
class RefusingProvider:
    """A backend that answers nothing, under a recorded backend's identity.

    Guarantees that being *called* raises, and that its identity is whatever it
    was constructed with rather than a stand-in's.

    What it is for. A replay resolves every call out of a
    :class:`~sciagent.systems.llm.transcripts.TranscriptStore` in
    :data:`~sciagent.systems.llm.transcripts.REPLAY` mode, which returns a hit
    or raises :class:`~sciagent.core.errors.TranscriptMissError`; the ``call``
    thunk it is handed is never invoked either way. But a
    :class:`~sciagent.systems.base.ResearchSystem` on a proposal arm cannot be
    *built* without a provider, so something has to be passed. This is that
    something, and it turns "the provider is never called" from a property of
    the control flow into one the object enforces -- invariant 2's "runtime
    assertions, not comments" applied to the seam where a replay could otherwise
    quietly become a live run.

    **The identity is required and is not decorative.** A transcript address
    hashes :attr:`Provider.id`, :attr:`Provider.model` and
    :attr:`Provider.settings` along with the brief, so a replay presenting a
    stand-in's name computes a different address for every call and misses the
    whole corpus. The identity a replay carries has to be the *recording*
    backend's, read back off the corpus -- see
    ``scripts/run_matrix.py``'s ``_replay_provider``.

    Raises :class:`~sciagent.core.errors.SystemConfigurationError` from
    :meth:`complete`, and deliberately **not**
    :class:`~sciagent.core.errors.ProviderError`: that one is a legitimate
    research outcome which
    :meth:`~sciagent.systems.hybrid.Hybrid._propose_once` catches and records as
    a refusal, so raising it here would turn a harness fault into a scored datum
    and a replay into a matrix built on refusals nobody made. Nor
    :class:`~sciagent.core.errors.ProviderUnavailableError`, which says a real
    backend could not be reached. Reaching this method at all means the run was
    configured to replay and did not.
    """

    id: str
    model: str
    settings: str

    def complete(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> Completion:
        """Raise. A replay must resolve every call from the corpus."""
        raise SystemConfigurationError(
            f"a replay asked {self.id}/{self.model} for a completion. A replay "
            f"resolves every call from the transcript corpus and a miss raises "
            f"before reaching a provider, so this is a run that was configured "
            f"to replay and did not -- not a model declining to answer"
        )


@dataclass(frozen=True, slots=True)
class Proposal:
    """A decoded, grammar-checked proposal, ready to be entertained.

    Carries the structure and the prose, and no number.

    ``address`` says what produced this proposal. For a
    :class:`ProposalLayer` it is the transcript address of the model call, which
    is what lets a claim about the proposal be traced back to the exact call.
    It is **not** always a transcript address: a source that reaches no provider
    writes its own provenance instead --
    :class:`~sciagent.systems.baselines.uniform.UniformProposer` writes
    ``uniform:{seed}:{index}``, naming the draw rather than a call. So do not
    hand this to :meth:`~sciagent.systems.llm.transcripts.TranscriptStore.resolve`
    without knowing which source produced it; nothing in the framework does.
    """

    program_edit: Defect
    name: str
    rationale: str
    address: str


#: What became of one draw of a k-sample request.
#:
#: ``"admitted"`` is the draft that became the proposal -- exactly one per
#: successful :meth:`ProposalLayer.propose`. ``"valid"`` is a draft the grammar
#: licenses that a lower-numbered sample beat to it; it is *not* a failure, and
#: counting it as one would understate how often the model answers usably.
#: ``"malformed"`` is a payload that did not denote a licensed structure, and
#: ``"refused"`` is the backend declining or hitting its ceiling.
#:
#: The four are what make proposal diversity measurable, which is the whole point
#: of taking more than one draw.
type DrawOutcome = Literal["admitted", "valid", "malformed", "refused"]

#: What one draw can fail with and still be *one spent draw* rather than the end
#: of the run. Deliberately these two and no wider: their siblings under
#: :class:`~sciagent.core.errors.ProposalError` -- a replay miss, a scheme
#: mismatch, an unreachable backend -- are conditions about the machine or the
#: corpus rather than about this draw, and must stop the run. Both members carry
#: a ``cause`` tag, which is what lets a draw bin like a proposal attempt.
type _DrawFailure = ProviderError | MalformedProposalError

#: One draw, as the sampling loop hands it to the admission pass: the sample
#: index, the address it was recorded at, and exactly one of a parsed draft or
#: the failure that replaced it.
#:
#: The sample index is **carried rather than re-derived from position**. The two
#: agree today -- the loop appends exactly one entry per sample, in order -- and
#: that is precisely the coincidence a review flagged: a `SampleRecord.sample`
#: taken from `enumerate` would be a different quantity that happens to match,
#: and it names the value hashed into `.address`, so the day the two diverge the
#: record would point at a call it did not describe.
type _Draw = tuple[int, str, ProposalDraft | None, _DrawFailure | None]


@dataclass(frozen=True, slots=True)
class SampleRecord:
    """One draw of one proposal request, and what the framework made of it.

    Carries no number a model wrote. ``structure`` is the tuple of *menu
    indices* a draft named -- a choice of cell, not a magnitude -- and is
    present whenever the payload parsed, including when the structure it named
    turned out not to exist. That last case is deliberate: a draft indexing off
    the end of the menu is the most informative thing a coverage measurement can
    see, and dropping it would report perfect coverage of a menu the model
    cannot address.
    """

    proposal: int
    """Which request. :attr:`ProposalLayer.proposals` counts these."""

    sample: int
    """Which draw of that request, ``0`` to ``k-1``. Hashed into the address."""

    address: str
    outcome: DrawOutcome
    structure: tuple[int, ...] | None

    cause: str = ""
    """Why, for a draw that produced nothing -- a **tag**, never prose.

    The vocabulary is :attr:`~sciagent.core.errors.ProviderError.cause`'s, plus
    :attr:`~sciagent.core.errors.MalformedProposalError.cause`'s fixed
    ``"undecodable"``, so a draw bins exactly as a proposal attempt does.

    Split from :attr:`detail` for the reason
    :class:`~sciagent.systems.hybrid.ProposalAttempt` splits the same pair, and
    that reason is SPEC's second invariant: an error message from the decoder
    interpolates the model's own un-slugged ``name`` field, so putting it here
    would make a field documented as a bin key hold provider-authored text. A
    consumer that then binned by it would get a histogram whose *partition* the
    model chose -- one bin per proposal name. An audit found this class holding
    ``str(error)`` here and flagged it before any consumer existed to be misled
    by it."""

    detail: str = ""
    """The failure's message, for a human reading the log. Never a bin key."""


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
        "_draws",
        "_grammar",
        "_memory",
        "_menu",
        "_proposals",
        "_provider",
        "_samples",
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
        samples: int = 1,
    ) -> None:
        if samples < 1:
            raise SystemConfigurationError(
                f"a proposal layer was built to take {samples} sample(s) per "
                f"request, which is a layer that cannot propose at all. A "
                f"single-sample layer is 1, and k-sample elicitation is k > 1"
            )
        self._provider = provider
        self._grammar = grammar
        self._menu = structural_menu(grammar)
        self._store = store
        # The prefix joins **whatever** instruction this layer was given, not
        # only the default one. `memory_ablation` supplies its own prompt to both
        # arms, so composing onto the default alone would leave V3 and V4
        # indexing into a menu that is in neither the system block nor -- since
        # gate A36 -- the brief. A test review found that exact parenthesisation
        # before it was written; see `tests/acceptance/test_a36.py`.
        instruction = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._system = f"{instruction}\n\n{render_menu_prefix(self._menu)}"
        self._memory = memory
        self._samples = samples
        self._calls = 0
        self._proposals = 0
        self._draws: tuple[SampleRecord, ...] = ()

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
    def system(self) -> str:
        """Return the system block: this layer's instruction and the menu.

        Public because it is half of what determines an address, and a caller
        that can see only the brief can neither reproduce an address nor measure
        what the request costs. ``scripts/rate_limit_pilot.py`` needs the second.
        """
        return self._system

    @property
    def samples(self) -> int:
        """Return how many draws each :meth:`propose` takes. ``1`` unless asked.

        The default is one because k multiplies the live cost of a recording
        campaign, and a default above it would spend that without anyone
        choosing to.
        """
        return self._samples

    @property
    def calls(self) -> int:
        """Return how many model calls have been made -- ``samples`` per request.

        This is the count of *addresses consumed*, which is what it always was:
        at the single-sample default it is still one per :meth:`propose`. Use
        :attr:`proposals` for the number of times the layer was asked.
        """
        return self._calls

    @property
    def proposals(self) -> int:
        """Return how many times :meth:`propose` has been called."""
        return self._proposals

    @property
    def draws(self) -> tuple[SampleRecord, ...]:
        """Return every draw taken, in the order taken.

        The evidence behind :meth:`structure_counts`, kept so a caller can ask a
        question this class did not anticipate. Written by the framework from
        what the drafts named.
        """
        return self._draws

    def structure_counts(self) -> tuple[tuple[tuple[int, ...], int], ...]:
        """Return how often each structure was drawn, in sorted key order.

        Guarantees the ordering is by structure and not by insertion, so the
        result is comparable across runs and cannot make output depend on dict
        iteration order (SPEC's third invariant).

        Counting, not scoring. A structure index names a cell of the grammar's
        menu, so this is the framework tallying which cells a model reached for
        -- the proposal diversity and menu coverage that taking k draws exists to
        make measurable. Draws that produced no draft at all are not counted,
        because there is nothing to attribute them to; :attr:`draws` still holds
        them.
        """
        counts: dict[tuple[int, ...], int] = {}
        for draw in self._draws:
            if draw.structure is not None:
                counts[draw.structure] = counts.get(draw.structure, 0) + 1
        return tuple((key, counts[key]) for key in sorted(counts))

    def propose(self, investigation: Investigation) -> Proposal:
        """Return one proposal for the state ``investigation`` is in.

        Raises :class:`~sciagent.core.errors.TranscriptMissError` when replaying
        a call that was never recorded,
        :class:`~sciagent.core.errors.ProviderError` when the recorded call is a
        *refusal* -- rebuilt by
        :meth:`~sciagent.systems.llm.transcripts.TranscriptStore.resolve` with the
        cause it was recorded under, so a replayed refusal is indistinguishable
        from the live one (gate A35) -- and
        :class:`~sciagent.core.errors.MalformedProposalError` when the payload
        does not denote a structure the grammar licenses. **None of the three is
        swallowed**: a system that wants to carry on without a proposal should
        decide that itself rather than have the layer decide it silently.

        Since k-sampling, two of them *are* caught per draw and re-raised from
        the request — a refusal and an undecodable payload are each one spent
        draw of k, and the failure that propagates when no draw is admissible is
        sample 0's, unchanged and unwrapped. The externally visible behaviour is
        the same as before at the single-sample default; the sentence above is
        about the caller's contract, and this paragraph is about the mechanism,
        because a reader who assumed "caught nowhere" would misread the loop.
        A :class:`~sciagent.core.errors.TranscriptMissError` really is caught
        nowhere, and must not be: it says the corpus has no record of this draw.

        **Every one of :attr:`samples` draws is taken, whatever the first one
        says.** Stopping at the first usable draft would make the number of
        addresses a request consumes depend on the answers it got, so replaying
        the run would require reproducing the answers in order to know where to
        look for them. It also truncates exactly the distribution the mode
        exists to report. At the default of one this is a distinction without a
        difference; above it, it is the whole design.

        Admission is the **first draft, in sample order, that the grammar
        licenses** -- not the first answer, which may be malformed, and not the
        best of them, which would need a judgement this layer has no standing to
        make. If no draw is admissible, sample 0's exception propagates, so a
        single-sample layer behaves exactly as it did before k-sampling existed
        rather than approximately so.
        """
        brief = render_brief(investigation, memory=self._memory)
        schema = tool_schema(self._menu)
        index = self._proposals
        self._proposals += 1

        drawn: list[_Draw] = []
        for sample in range(self._samples):
            address = call_address(
                provider=self._provider.id,
                model=self._provider.model,
                settings=self._provider.settings,
                system=self._system,
                brief=brief,
                schema=schema,
                index=index,
                sample=sample,
            )
            self._calls += 1
            try:
                transcript = self._store.resolve(
                    address,
                    lambda: self._provider.complete(self._system, brief, schema),
                    provider=self._provider.id,
                    model=self._provider.model,
                    brief=brief,
                    settings=self._provider.settings,
                )
            except ProviderError as error:
                # Only a refusal. A `TranscriptMissError` is a replay that has no
                # record of this draw, and a `ProviderUnavailableError` is a
                # machine fault -- both are siblings rather than subclasses
                # precisely so that catching one here does not swallow them, and
                # both must stop the run rather than become one unusable draw
                # among k.
                drawn.append((sample, address, None, error))
                continue
            try:
                # **Outside the clause above, and this is not a tidiness move.**
                # `draft_from_payload` raises `MalformedProposalError`, which is a
                # *sibling* of `ProviderError` -- so while this call sat inside
                # that `try`, a payload that did not conform to the schema
                # escaped `propose` outright: the remaining draws were never
                # taken, an already-admissible draft from a lower sample was
                # discarded, and not one `SampleRecord` was written. A review
                # reproduced it at `samples=3` with a valid sample 0 and a
                # `plausibility`-carrying sample 1 -- three calls' worth of
                # intent, zero draws recorded. A non-conforming payload is one
                # unusable draw, exactly as an undecodable one is.
                drawn.append(
                    (sample, address, draft_from_payload(transcript.payload), None)
                )
            except MalformedProposalError as error:
                drawn.append((sample, address, None, error))
        return self._admit_first_valid(drawn, index)

    def _admit_first_valid(
        self,
        drawn: Sequence[_Draw],
        index: int,
    ) -> Proposal:
        """Return the first licensed draft, recording what every draw became.

        Two passes over the draws rather than one, and the split is the point:
        the calls are all made before any of them is judged, so what the
        framework decides can never change what the framework asked.
        """
        records: list[SampleRecord] = []
        admitted: Proposal | None = None
        for sample, address, draft, failure in drawn:
            if draft is None:
                assert failure is not None, "a draw with no draft carries its failure"
                records.append(
                    SampleRecord(
                        proposal=index,
                        sample=sample,
                        address=address,
                        # A refusal is the backend declining; a non-conforming
                        # payload is the model answering unusably. Both are one
                        # spent draw, and they bin differently because the first
                        # is a fact about the backend and the second is not.
                        outcome=(
                            "refused"
                            if isinstance(failure, ProviderError)
                            else "malformed"
                        ),
                        structure=None,
                        cause=failure.cause,
                        detail=str(failure),
                    )
                )
                continue
            structure = tuple(edit.structure for edit in draft.edits)
            try:
                proposal = self._build(draft, address)
            except MalformedProposalError as error:
                records.append(
                    SampleRecord(
                        proposal=index,
                        sample=sample,
                        address=address,
                        outcome="malformed",
                        structure=structure,
                        cause=error.cause,
                        detail=str(error),
                    )
                )
                continue
            if admitted is None:
                admitted = proposal
            records.append(
                SampleRecord(
                    proposal=index,
                    sample=sample,
                    address=address,
                    outcome="admitted" if proposal is admitted else "valid",
                    structure=structure,
                )
            )
        self._draws += tuple(records)
        if admitted is not None:
            return admitted
        # Nothing was admissible, so the request failed. Sample 0's failure is
        # what propagates: it is the one a single-sample layer would have raised,
        # which is what makes k=1 the old behaviour exactly.
        _sample, address, draft, failure = drawn[0]
        if failure is not None:
            raise failure
        assert draft is not None, "a draw with no failure carries its draft"
        return self._build(draft, address)

    def _build(self, draft: ProposalDraft, address: str) -> Proposal:
        """Decode a draft and check it against the grammar.

        Every ``GrammarError`` is re-raised as the error this method's caller
        documents, because "does not denote a structure the grammar licenses" is
        precisely what each of them says, and because ``Hybrid._propose_once``
        catches ``MalformedProposalError`` and not ``GrammarError`` -- so one
        that escapes here does not cost a proposal, it stops the campaign.

        The commonest is :class:`~sciagent.core.errors.InvalidEditError`, for a
        defect whose edits are each licensed but which conflict: two on one
        target, whose compiled family would be ambiguous. ``decode`` has already
        refused everything else it checks, so that is the one way a draft reaches
        here licensed edit by edit and invalid as a whole. Until 2026-08-18 it
        escaped and stopped item 15's V3/S11 mid-cell.

        The guard is written at ``GrammarError`` rather than at that one member
        because the fix for that stoppage named the error it had just seen, which
        is how a guard ends up narrower than the boundary it defends. The other
        three are unreachable while ``self._menu`` is built from ``self._grammar``
        in ``__init__`` -- a licensed decode cannot fail validation -- but that is
        a property of this class's wiring rather than a promise validation makes,
        and ``decode`` already carries an error for the wiring coming apart.
        """
        program_edit = decode(self._grammar, self._menu, draft)
        try:
            self._grammar.validate_defect(program_edit)
        except GrammarError as error:
            raise MalformedProposalError(f"{type(error).__name__}: {error}") from error
        return Proposal(
            program_edit=program_edit,
            name=slug_hypothesis_name(draft.name),
            rationale=draft.rationale,
            address=address,
        )


def slug_hypothesis_name(name: str) -> str:
    """Return a hypothesis-id-safe rendering of a source-chosen name.

    A model writes prose, and a ``HypothesisId`` ends up in claim ids and in
    sorted orderings that decide an exact tie, so the characters it may carry
    are the framework's decision and not the model's.

    Public, and applied by :meth:`~sciagent.systems.hybrid.Hybrid._admit`
    rather than only here. While ``Hybrid`` held a ``ProposalLayer``
    concretely, calling this in :meth:`ProposalLayer._build` was enough --
    every name reaching the graph had been through it. Once ``Hybrid`` took
    any :class:`~sciagent.systems.hybrid.ProposalSource`, that stopped being
    a property of the framework and became one each source had to remember,
    which is the shape of guarantee SPEC's second invariant says to enforce
    with an assertion rather than a convention. Idempotent, so a source that
    slugs its own names -- as ``ProposalLayer`` still does -- is unchanged by
    the second application. The trim runs *after* the cut for that reason and
    not for tidiness: cutting last leaves a name whose forty-eighth character
    is strippable longer on its first application than on its second -- by one
    character, or by as many as the run of underscores the cut lands in the
    middle of. That difference is the whole of what the second call site relies
    on not existing.
    ``TestSlugHypothesisName`` pins it at both boundary characters.

    Anything outside a conservative set becomes an underscore; an empty result
    becomes ``"proposal"`` rather than an id that sorts before everything and
    reads as absent.
    """
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in name.strip().lower()
    ).strip("_")
    return cleaned[:48].strip("_") or "proposal"


#: What the model is told its job is. Deliberately short and deliberately about
#: the *division of labour*: it names the one thing the model decides (which
#: structure) and the things it does not (which experiment to run, what anything
#: is worth). SPEC F5 assigns experiment selection within a fixed space to
#: conventional methods, and a prompt that invited the model to opine on it would
#: be inviting an answer the framework then has to ignore.
DEFAULT_SYSTEM_PROMPT = """\
You are proposing structure for a scientific investigation.

An executable programme generates the data. Something has been changed in it,
and the change is one of the structures listed below. Conventional
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
