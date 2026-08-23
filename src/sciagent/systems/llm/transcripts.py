"""Record and replay of model calls, so an LLM run is reproducible.

SPEC §1's third invariant is bit-exact determinism: the same seed, config and
version must give byte-identical output. A model call cannot satisfy that on its
own, and the reason is not that determinism is hard to arrange but that the
knobs which would arrange it **do not exist**. The models this layer targets
reject ``temperature``, ``top_p`` and ``top_k`` outright -- a request carrying
any of them is refused -- so there is no setting, not even a degenerate one, that
makes two calls with one prompt return one answer.

A recorded response is therefore not a cache. It is *the* reproducible artefact,
and the model call is the process that produces it, exactly as
:class:`~sciagent.inference.empirical.EmpiricalTable` is the artefact and
simulation is the process that produces it. The parallel is deliberate and the
two behave the same way: content-addressed, refused when the address disagrees,
and never silently refreshed.

Two modes, and the asymmetry is the point
-----------------------------------------

:data:`REPLAY` raises :class:`~sciagent.core.errors.TranscriptMissError` on a
miss. :data:`RECORD` calls the provider and stores what comes back. Evaluation
runs in ``REPLAY``, because a run that would quietly call out on a miss is a run
whose result depends on when it happened and on who had a key. Recording is a
separate, deliberate act that produces an artefact to be committed, reviewed and
re-run against.

What the address covers
-----------------------

Everything that could change the answer: the provider's identity, the model id,
the provider's **settings**, the system prompt, the rendered brief, the tool
schema, and the index of the call within the investigation. The last one matters
because a system may ask twice with an identical brief -- after an experiment
that moved nothing, say -- and the two calls are different events that may
legitimately get different answers.

It deliberately does **not** cover the scenario id or the seed. Those reach the
address only through the brief, which is where they belong: two scenarios that
present a model with the identical brief are, as far as the model is concerned,
the same question, and giving them different addresses would record the same
answer twice and hide that fact.

Address, and provenance
-----------------------

The two are different questions and the split is deliberate.

The **address** is what determines the request. ``settings`` is in it because a
provider run at a different reasoning effort, or a lower output ceiling, asks a
different question and may get a different answer; without it two such runs share
an address, and the second either trips the append-only check or is silently
served the first one's answer. That was a real defect, not a tidiness argument.

**Provenance** is what produced the answer, and is recorded beside the payload
rather than hashed into its identity. The version of a spawned CLI belongs here:
it is worth knowing which binary answered, and it must not be part of the
address, because a tool that auto-updates would otherwise invalidate an entire
corpus on somebody else's release schedule.

One consequence worth stating plainly: :meth:`TranscriptStore.put` compares
the **answer**, not provenance. Two recordings of one address that agree on the
answer agree, whichever binary produced them, and the first one's provenance is
kept.

A refusal is an answer
----------------------

A model that declines, or hits an output ceiling, is not a call that failed to
happen: it is an event of the investigation, which ``Hybrid`` records as a
proposal outcome and the ledger scores. So it is recorded here too, as a
:class:`Transcript` whose :attr:`~Transcript.outcome` is ``"refused"``, and
:meth:`TranscriptStore.resolve` rebuilds the original exception from it on
replay. Before that (gate A35) a refusal stored nothing, so the *reading* counted
it while the *address* was absent from the corpus, and replaying that replicate
raised :class:`~sciagent.core.errors.TranscriptMissError` rather than reproducing
the refusal.

The record kind is new; the **addressing scheme is not**, and
:data:`ADDRESS_VERSION` deliberately did not move for it. Nothing that is hashed
changed, and a bump would have refused every corpus recorded before the change --
including the 112-call section 9 corpus the campaign's LLM numbers rest on.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from sciagent.core.errors import (
    ProposalError,
    ProviderError,
    TranscriptMissError,
    TranscriptSchemeError,
)
from sciagent.core.program import stable_key

__all__ = [
    "RECORD",
    "REPLAY",
    "Completion",
    "Transcript",
    "TranscriptMode",
    "TranscriptOutcome",
    "TranscriptStore",
    "call_address",
    "corpus_digest",
]

type TranscriptMode = Literal["replay", "record"]

#: What kind of record a transcript is.
#:
#: ``"answered"`` is a tool payload the model returned. ``"refused"`` is the
#: model declining, or hitting an output ceiling, or returning a body that is not
#: the object the schema demands -- every condition
#: :class:`~sciagent.core.errors.ProviderError` is raised for. Both are *events
#: of the investigation* and both are recorded, which is what lets a replicate
#: the ledger scored as refused replay.
#:
#: Deliberately narrow. A machine fault -- an unreachable backend, a replay that
#: reached a provider -- is not on this list and is not recordable: those raise
#: :class:`~sciagent.core.errors.ProviderUnavailableError` and
#: :class:`~sciagent.core.errors.SystemConfigurationError`, neither of which
#: :meth:`TranscriptStore.resolve` converts. Widening this type is how a harness
#: fault becomes a scored datum.
type TranscriptOutcome = Literal["answered", "refused"]

#: Refuse to call out; a missing address is an error. What evaluation runs under.
REPLAY: TranscriptMode = "replay"

#: Call out on a miss and store the answer. A deliberate, separate act.
RECORD: TranscriptMode = "record"

#: Version of the address scheme itself. Mixed into every address, so that a
#: change to *what* is hashed invalidates stored transcripts rather than silently
#: matching them against a differently-computed key -- the same promise
#: ``OPERATIONS_VERSION`` makes for the empirical table's cache.
ADDRESS_VERSION = "transcript/2"


def call_address(
    *,
    provider: str,
    model: str,
    settings: str,
    system: str,
    brief: str,
    schema: Mapping[str, Any],
    index: int,
) -> str:
    """Return the content address of one model call.

    Guarantees the address is a pure function of its arguments and is stable
    across processes: the schema is serialised with sorted keys and no
    whitespace, and every part is joined under a separator that cannot occur in
    a rendered brief. ``stable_key`` is the same hash the registry and the
    empirical table are addressed by, so one notion of content identity serves
    the whole framework.

    ``settings`` is required rather than defaulted deliberately. A default would
    let a new backend forget to declare what it varies and get a plausible
    address anyway, which is the failure this argument exists to prevent.
    """
    payload = "\x00".join(
        (
            ADDRESS_VERSION,
            provider,
            model,
            settings,
            system,
            brief,
            json.dumps(schema, sort_keys=True, separators=(",", ":")),
            str(index),
        )
    )
    return f"call/{stable_key(payload) % (1 << 64):016x}"


def _outcome_of(raw: object, path: Path) -> TranscriptOutcome:
    """Return ``raw`` as a record kind, refusing anything else.

    Guarantees a corpus cannot introduce a kind this process does not implement.
    An unknown value is not defaulted to ``"answered"``: that would read a record
    somebody wrote to mean something as though it were a model's answer, and hand
    an empty payload to the decoder. It raises, on the same reasoning
    :meth:`TranscriptStore.load` refuses a file addressed under another scheme.
    """
    if raw not in ("answered", "refused"):
        raise ProposalError(
            f"{path} holds a call whose record kind is {raw!r}, which this "
            f"process does not implement; a corpus recorded under a wider "
            f"vocabulary cannot be read by a narrower one"
        )
    # `raw` is narrowed by the membership test above, which mypy cannot see
    # through on an `object`, so the kind is rebuilt from the literal.
    return "refused" if raw == "refused" else "answered"


def corpus_digest(path: Path) -> str:
    """Return the SHA-256 of the corpus at ``path``, as lowercase hex.

    Guarantees the digest is a pure function of the file's bytes, so a third
    party can check it with ``sha256sum`` and no part of this framework. That is
    the point of the choice: the replay claim is the reproducibility story for
    every LLM number in the campaign, and a claim checkable only by the code it
    vouches for is not checkable.

    Bytes rather than a re-canonicalisation of the parsed content, because
    :meth:`TranscriptStore.save` already writes canonically -- sorted keys,
    ``indent=2``, and ``newline="\\n"`` regardless of platform -- so the file *is*
    the canonical form and hashing it twice would only add a way for the two to
    disagree.

    Read in binary and in chunks: a recorded corpus is hundreds of kilobytes
    today and grows with every campaign, and a digest is not a reason to hold one
    in memory.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Completion:
    """What a provider returns: the answer, and what produced it.

    The split is the point. ``payload`` is the model's answer and is what an
    address identifies. ``provenance`` is metadata about the run that produced
    it -- a CLI version, which model actually served the turn -- recorded for
    audit and deliberately not hashed, so that a corpus does not expire when a
    tool updates.

    Provenance values are strings so that a transcript file stays diffable and
    a reader does not have to know which fields happen to be numeric.
    """

    payload: Mapping[str, Any]
    provenance: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Transcript:
    """One recorded model call: what was asked, and what came back.

    The request fields are stored alongside the response even though the address
    already covers them. They are what makes a transcript file reviewable by a
    human -- a directory of opaque digests mapped to payloads would be
    reproducible and unauditable at the same time, and SPEC §8's insistence that
    prose is a rendering rather than the authority cuts both ways.
    """

    address: str
    provider: str
    model: str
    brief: str
    payload: Mapping[str, Any]
    """The tool payload the model returned, exactly as received."""

    provenance: Mapping[str, str] = field(default_factory=dict)
    """What produced the payload, for audit. Never part of the address.

    Defaulted so that a caller who has nothing to declare -- a scripted backend,
    a hand-built fixture -- says nothing rather than inventing a value.
    """

    settings: str = ""
    """The provider settings this call was made under. Part of the address.

    Stored for the same reason ``provider`` and ``model`` are: without it two
    entries recorded at different reasoning efforts differ in address and are
    otherwise indistinguishable in the file, which is precisely the question a
    reader opens a transcript to answer. Defaulted, so the five-argument
    constructions that predate it still read the same -- it was last in the field
    order until the refusal fields arrived after it, on the same reasoning.
    """

    outcome: TranscriptOutcome = "answered"
    """Whether this record holds an answer or a refusal. Part of the answer.

    Defaulted to ``"answered"`` for the reason ``settings`` is defaulted, and for
    one more: a corpus recorded before refusals were recordable carries no such
    key, and every record in it *is* an answer. The default is what lets the
    112-call section 9 corpus load unchanged under :data:`ADDRESS_VERSION`
    ``"transcript/2"``, which is the decision this field was added under -- a
    refusal is a new record **kind**, nothing that is hashed changed, and bumping
    the scheme would orphan that corpus for no gain.
    """

    cause: str = ""
    """For a refusal, :class:`~sciagent.core.errors.ProviderError`'s tag.

    Empty for an answer. Recorded rather than re-derived because
    ``Hybrid._propose_once`` reads the tag off the exception and
    :func:`~sciagent.eval.agency.proposal_causes` bins by it, so a replay that
    reconstructed a *generic* refusal would reproduce the ``refused`` count while
    moving the cause histogram -- reproducing the ledger row and not the reading.
    """

    detail: str = ""
    """For a refusal, the message the provider raised with. Empty otherwise.

    Stored for the reason the request fields are stored: it is what makes the
    record reviewable. It is also what ``Hybrid`` carries in
    :attr:`~sciagent.systems.hybrid.ProposalAttempt.detail`, so a replay that
    dropped it would answer "why did this refuse" with silence, in precisely the
    corpus somebody opened to find out.
    """

    @property
    def answer(self) -> tuple[str, str, Mapping[str, Any]]:
        """Return what this address identifies: the answer, and nothing else.

        Guarantees provenance is excluded. Two recordings of one address that
        agree on *this* agree, whichever binary produced them -- the rule
        :meth:`TranscriptStore.put` enforces, expressed here rather than restated
        at each place that compares.

        The payload alone was a complete description of an answer while every
        record was one. A refusal carries an empty payload, so a comparison
        reading payloads alone finds two refusals naming *different causes*
        identical, and an append-only store would keep whichever arrived first
        while the corpus reported the other's condition.

        ``detail`` is **not** in here, and the reason is the same one that keeps
        provenance out: it is recorded for audit, not for identity. It is still
        *stored*, because a replay that could not reproduce the message would not
        be the byte-identical replay gate A35 asks for -- but two recordings of
        one address that agree on the outcome, the cause and the payload agree,
        and the first one's wording is the one kept.

        A first version did include it, on the argument that every raise site
        varies its message only with what happened. A review falsified that, and
        the counterexample is not incidental. ``AnthropicProvider`` excludes
        ``max_tokens`` from the address deliberately -- its own docstring argues
        that a ceiling "either yields that answer or **raises**, so it can never
        produce a different recorded payload" -- and A35 turned that raise into a
        record whose message interpolates the ceiling. Two runs differing only in
        a value the addressing scheme is designed to ignore then collided, and
        the second ``save`` refused. Including ``detail`` would have made this
        module quietly break a neighbouring module's stated invariant.
        """
        return (self.outcome, self.cause, self.payload)

    def as_json(self) -> dict[str, Any]:
        """Return the JSON form written to disk.

        Guarantees a record that carries no refusal writes no refusal keys, so a
        corpus recorded before A35 round-trips through :meth:`TranscriptStore.load`
        and :meth:`~TranscriptStore.save` **byte-identically**.

        That is not tidiness, and a review caught it by reproducing the
        alternative. Emitting the three keys unconditionally rewrote every
        pre-A35 corpus on the first save that touched it -- ``spec9.json`` grew
        659,740 bytes to 667,356 and its digest moved -- which invalidates the
        hash ``docs/CORPUS.md`` publishes for it. ``run_matrix``'s ``checkpoint``
        calls ``save`` after *every replicate*, so a single resumed recording
        pass would have done it, silently, to the artefact the campaign's LLM
        numbers rest on.

        Still a pure function of the record, so determinism holds: an answer
        always writes the same keys and a refusal always writes its own. What is
        conditional is the record's kind, not anything about when it was written.
        """
        form: dict[str, Any] = {
            "address": self.address,
            "provider": self.provider,
            "model": self.model,
            "settings": self.settings,
            "brief": self.brief,
            "payload": dict(self.payload),
            "provenance": dict(self.provenance),
        }
        if self.outcome != "answered":
            form["outcome"] = self.outcome
        if self.cause:
            form["cause"] = self.cause
        if self.detail:
            form["detail"] = self.detail
        return form


class TranscriptStore:
    """An append-only store of recorded model calls.

    Append-only for the reason the registry is (SPEC §6.3 A12): a transcript
    that could be overwritten is a record of what the model says *now*, and the
    whole point is a record of what it said when the result was measured. Storing
    a different :attr:`Transcript.answer` at an existing address raises rather
    than replacing -- the answer rather than the payload, because a refusal
    carries an empty payload and two refusals naming different causes would
    otherwise compare equal.

    Not a frozen value type, unlike most of this framework. A store accumulates
    during a recording run, exactly as
    :class:`~sciagent.inference.empirical.EmpiricalTableEngine` accumulates
    during an investigation and for the same reason.
    """

    __slots__ = ("_entries", "_misses", "_mode")

    def __init__(
        self,
        entries: Mapping[str, Transcript] | None = None,
        *,
        mode: TranscriptMode = REPLAY,
    ) -> None:
        self._entries: dict[str, Transcript] = dict(entries or {})
        self._mode: TranscriptMode = mode
        self._misses = 0

    @property
    def mode(self) -> TranscriptMode:
        """Return whether a miss raises or is filled by calling out."""
        return self._mode

    @property
    def misses(self) -> int:
        """Return how many addresses were filled by a live call.

        Zero for a pure replay, which is what a reproducibility check asserts:
        a run that reports misses did not replay, it re-derived.
        """
        return self._misses

    def __len__(self) -> int:
        """Return how many calls are stored."""
        return len(self._entries)

    def __contains__(self, address: str) -> bool:
        """Return whether this address has a recorded response."""
        return address in self._entries

    def addresses(self) -> tuple[str, ...]:
        """Return every stored address, sorted, so iteration is reproducible."""
        return tuple(sorted(self._entries))

    def get(self, address: str) -> Transcript:
        """Return the recorded call at ``address``.

        Raises :class:`~sciagent.core.errors.TranscriptMissError` if absent,
        whatever the mode. Filling a miss is :meth:`resolve`'s business, and
        keeping the two apart is what stops a read path acquiring a write path
        by accident.
        """
        try:
            return self._entries[address]
        except KeyError:
            raise TranscriptMissError(
                f"no recorded response at {address}; the store holds "
                f"{len(self._entries)} call(s)"
            ) from None

    def put(self, transcript: Transcript) -> None:
        """Store a call. Raises if the address already holds a different answer.

        **The answer decides; provenance does not.** Two recordings of one
        address that agree on :attr:`Transcript.answer` agree, whichever binary
        or SDK version produced them, and the first one's provenance is the one
        kept. Comparing provenance too would make an append-only store reject a
        re-recording that reproduced the answer exactly, which is the opposite of
        what the guarantee is for.

        The comparison reads :attr:`Transcript.answer` rather than the payload
        alone, and the difference is only visible once refusals are recordable: a
        refusal carries an *empty* payload, so two refusals naming different
        causes -- a model declining, and an output ceiling -- compared equal, and
        the store kept whichever arrived first while reporting the other's
        condition. See that property for the full argument.
        """
        existing = self._entries.get(transcript.address)
        if existing is not None:
            if existing.answer != transcript.answer:
                raise ProposalError(
                    f"address {transcript.address} already holds a different "
                    f"response; a transcript store is append-only, so a model "
                    f"that answered differently to an identical prompt must be "
                    f"recorded under a new address rather than overwriting one"
                )
            return
        self._entries[transcript.address] = transcript

    def resolve(
        self,
        address: str,
        call: Callable[[], Completion],
        *,
        provider: str,
        model: str,
        brief: str,
        settings: str = "",
    ) -> Transcript:
        """Return the answer at ``address``, or re-raise the refusal recorded there.

        ``call`` is invoked exactly once, and only on a miss in :data:`RECORD`
        mode. In :data:`REPLAY` a miss raises, which is the whole guarantee: an
        evaluation run cannot quietly become a live one because someone had
        credentials in their environment.

        **A refusal is recorded and then re-raised, on both legs.** In
        :data:`RECORD` a :class:`~sciagent.core.errors.ProviderError` from
        ``call`` is stored as a refusal record and raised onward; in
        :data:`REPLAY` that record is a *hit*, and this method rebuilds the same
        exception from it. The two legs therefore leave the caller in states that
        differ in nothing it can observe -- which is what makes a replicate the
        ledger scored as refused replay at all, and it is the whole of gate A35.

        Raising here rather than returning the record for a caller to interpret
        is deliberate. It is the only arrangement in which the reconstruction
        cannot be *forgotten*: a caller handed a refusal record would otherwise
        pass its empty payload to the decoder and get
        :class:`~sciagent.core.errors.MalformedProposalError`, which
        ``Hybrid._extend`` continues past where a refusal breaks -- so the replay
        would make a call the recording never made, at an address the corpus
        cannot hold, and miss. A review of A35's test found exactly that
        implementation passing every other assertion.

        **Only ``ProviderError`` is converted, and the two exclusions are the
        point.** :class:`~sciagent.core.errors.ProviderUnavailableError` is a
        *sibling*, not a subclass, so a 429 propagates and records nothing: a
        transport failure says nothing about the model's ability to propose, and
        recording one would write a refusal nobody made into an append-only
        corpus. :class:`~sciagent.core.errors.SystemConfigurationError` sits
        outside :class:`~sciagent.core.errors.ProposalError` entirely, so a
        replay that reached a provider stays the configuration fault it is.
        Widening this clause by one class -- to ``ProposalError``, which
        :class:`~sciagent.core.errors.TranscriptMissError` also sits under --
        would make a replay against an incomplete corpus report a full matrix
        scored on refusals the harness invented, and contradicts gate A40.
        """
        recorded = self._entries.get(address)
        if recorded is not None:
            self._raise_for_refusal(recorded)
            return recorded
        if self._mode != RECORD:
            raise TranscriptMissError(
                f"no recorded response at {address} and the store is in "
                f"{self._mode!r} mode, so it will not call out. Record the "
                f"transcript deliberately, or run against a store that holds it"
            )
        try:
            completion = call()
        except ProviderError as error:
            refusal = Transcript(
                address=address,
                provider=provider,
                model=model,
                brief=brief,
                payload={},
                settings=settings,
                outcome="refused",
                cause=error.cause,
                detail=str(error),
            )
            self.put(refusal)
            # Counted, because `misses` says how many addresses were filled by a
            # live call and this one was. A recording run that refused did reach
            # the network; a replay of it will not.
            self._misses += 1
            raise
        if not isinstance(completion, Completion):
            raise ProposalError(
                f"a provider must return a Completion, got {type(completion).__name__}"
            )
        if not isinstance(completion.payload, Mapping):
            raise ProposalError(
                f"a provider must return a mapping payload, got "
                f"{type(completion.payload).__name__}"
            )
        transcript = Transcript(
            address=address,
            provider=provider,
            model=model,
            brief=brief,
            payload=dict(completion.payload),
            provenance=dict(completion.provenance),
            settings=settings,
        )
        self.put(transcript)
        self._misses += 1
        return transcript

    @staticmethod
    def _raise_for_refusal(transcript: Transcript) -> None:
        """Re-raise the refusal ``transcript`` records; return if it holds an answer.

        Named for the raise rather than the return, on ``raise_for_status``'s
        precedent. A helper that re-raises is the wrong place for a name a reader
        can mistake for a predicate, and this one stands at the seam deciding
        whether a harness fault becomes a scored datum.

        Guarantees the exception a replay raises is the one that was recorded,
        tag and message alike, and not a fresh refusal wearing the same outcome.
        ``Hybrid._propose_once`` reads
        :attr:`~sciagent.core.errors.ProviderError.cause` off the exception and
        :func:`~sciagent.eval.agency.proposal_causes` bins by it, so a generic
        reconstruction would reproduce the ledger row -- which carries no
        proposal tier and no cause histogram -- while silently moving the agency
        reading beside it. That is not a hypothetical: it is the wrong
        implementation A35's test review named first.
        """
        if transcript.outcome != "refused":
            return
        raise ProviderError(transcript.detail, cause=transcript.cause)

    # -- persistence -------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write the store to ``path`` as JSON, sorted by address.

        Sorted and indented so that a recorded transcript is a reviewable diff
        rather than one long line whose changes cannot be read.

        Guarantees the file never loses a call. A write that would drop an
        address already on disk raises
        :class:`~sciagent.core.errors.ProposalError` instead.

        The check exists because this method is the one place the store's
        append-only promise did not hold. :meth:`put` enforces it *in process* --
        an address holding a different answer raises -- but ``save`` replaces the
        whole file, so two recording sessions that loaded different snapshots of
        one path would each write their own view and the second would silently
        delete the first's calls. Recording is meant to be a deliberate act
        producing an artefact to review; an artefact quietly missing entries is
        the one outcome review would not catch, because there is nothing in the
        diff to look at.

        The same reasoning covers provenance one level down. :meth:`put` keeps
        the *first* recording's provenance in process; without
        :meth:`_keep_earlier_provenance`, a fresh ``RECORD`` store that
        re-recorded the same addresses on a newer binary and saved over the file
        would rewrite what the corpus says produced each answer -- a silent edit
        to the audit trail, in a file whose payloads are byte-identical and whose
        diff would therefore show only the thing nobody was looking at.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._refuse_to_drop(path)
        entries = self._keep_earlier_provenance(path)
        payload = {
            "version": ADDRESS_VERSION,
            "calls": [entries[key].as_json() for key in sorted(entries)],
        }
        # newline="\n" rather than the default: a corpus is the reproducible
        # artefact, and text mode would translate every newline to os.linesep on
        # write, so the same corpus recorded on Windows and on Linux would differ
        # byte for byte while replaying identically.
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _keep_earlier_provenance(self, path: Path) -> dict[str, Transcript]:
        """Return this store's calls, with any earlier provenance on disk kept.

        Extends :meth:`put`'s rule -- first recording wins -- across the file
        boundary. An address already on disk keeps the provenance it was recorded
        with, even when this store re-derived the same answer on a newer binary,
        because the corpus is a record of what produced each answer *when it was
        measured*.

        Only ever restores; never invents. An address absent from disk, or one
        whose stored provenance is empty, takes this store's value, so a corpus
        recorded before provenance existed gains it on the next save rather than
        being pinned to nothing. ``_refuse_to_drop`` has already established that
        the answers agree, so this cannot pair one run's provenance with
        another's answer.
        """
        entries = dict(self._entries)
        if not path.exists():
            return entries
        try:
            on_disk = self.load(path, mode=self._mode)
        except TranscriptSchemeError:
            return entries
        for address, stored in on_disk._entries.items():
            current = entries.get(address)
            if current is not None and stored.provenance:
                entries[address] = replace(current, provenance=stored.provenance)
        return entries

    def _refuse_to_drop(self, path: Path) -> None:
        """Raise unless writing this store to ``path`` preserves every call there.

        Two ways a write loses a recorded call, and both are refused: an address
        on disk that this store does not hold at all, and one it holds under a
        *different answer*. Checking only the address set would have left the
        second open, which is the same hole one level in -- :meth:`put` raises on
        a changed answer in process, so a file boundary that did not would make
        the guarantee depend on whether the two answers happened to arrive in one
        session.

        A file that does not exist drops nothing. A file recorded under an older
        address scheme is the one readable-failure that may be replaced: every
        call in it would miss anyway, which is what
        :class:`~sciagent.core.errors.TranscriptSchemeError` says, and refusing
        here as well would strand the corpus forever. **Every other failure to
        read propagates**, deliberately -- a corpus that is present but malformed
        may still hold recoverable calls, and quietly treating it as "drops
        nothing" would truncate exactly the file nobody can reconstruct.

        The clause is written at that narrow class and **not** at
        :class:`~sciagent.core.errors.ProposalError`, which is what it caught
        while a scheme mismatch was the only thing :meth:`load` raised. Gate A35
        added a second failure -- a record kind this process does not implement
        -- and under the broad clause a corpus holding one lost *every* call in
        the file, well-formed ones included. That was reproduced rather than
        reasoned about, and it is the whole reason the narrow class exists.
        """
        if not path.exists():
            return
        try:
            on_disk = self.load(path, mode=self._mode)
        except TranscriptSchemeError:
            return
        dropped = sorted(set(on_disk._entries) - set(self._entries))
        if dropped:
            raise ProposalError(
                f"writing this store to {path} would drop {len(dropped)} "
                f"recorded call(s) it does not hold, beginning {dropped[:3]}. A "
                f"transcript store is append-only: load the file, add to it, and "
                f"save that, rather than saving a view that never saw them"
            )
        changed = sorted(
            address
            for address, transcript in on_disk._entries.items()
            if self._entries[address].answer != transcript.answer
        )
        if changed:
            raise ProposalError(
                f"writing this store to {path} would replace the response at "
                f"{len(changed)} address(es), beginning {changed[:3]}. A "
                f"transcript store is append-only: a model that answered "
                f"differently to an identical prompt is recorded under a new "
                f"address, never over an old one"
            )

    @classmethod
    def load(cls, path: Path, *, mode: TranscriptMode = REPLAY) -> TranscriptStore:
        """Return the store recorded at ``path``.

        Raises :class:`~sciagent.core.errors.TranscriptSchemeError` if the file
        was written under a different address scheme, and the broader
        :class:`~sciagent.core.errors.ProposalError` for anything else it cannot
        read -- a record kind this process does not implement. The split is the
        safety property: ``save``'s guards may overwrite a corpus for the first
        reason and must refuse for the second. Same reasoning as
        :meth:`~sciagent.inference.empirical.EmpiricalTable.load` refusing a
        table whose content address disagrees: a file that cannot be addressed
        the way this process addresses things is not this store, and reading it
        anyway would produce misses that look like the model having changed.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("version")
        if version != ADDRESS_VERSION:
            raise TranscriptSchemeError(
                f"{path} was recorded under address scheme {version!r}, but this "
                f"process addresses calls as {ADDRESS_VERSION!r}; every stored "
                f"call would miss"
            )
        entries = {
            str(call["address"]): Transcript(
                address=str(call["address"]),
                provider=str(call["provider"]),
                model=str(call["model"]),
                brief=str(call["brief"]),
                settings=str(call.get("settings", "")),
                # Defaulted reads, not required keys. A file recorded before
                # refusals were recordable carries none of the three and every
                # record in it is an answer, which is exactly what these
                # defaults say. See :attr:`Transcript.outcome`.
                outcome=_outcome_of(call.get("outcome", "answered"), path),
                cause=str(call.get("cause", "")),
                detail=str(call.get("detail", "")),
                payload=dict(call["payload"]),
                provenance={
                    str(key): str(value)
                    for key, value in dict(call.get("provenance", {})).items()
                },
            )
            for call in raw.get("calls", [])
        }
        return cls(entries, mode=mode)

    def __iter__(self) -> Iterator[Transcript]:
        """Yield stored calls in address order."""
        for address in sorted(self._entries):
            yield self._entries[address]
