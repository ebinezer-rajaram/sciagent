"""Acceptance test A35: a refused replicate replays, and the corpus is hashed.

A35 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Refusals break replay, and the corpus has no in-repo
hash"*, and reads:

    ``test_a35_a_refused_replicate_replays`` -- a recorded run containing a
    refusal replays byte-identically with zero misses; the corpus hash is
    resolvable from the repository.

The entry's **Idea.** is the standard these tests are written to: *"Record model
refusals as first-class transcripts so a scored replicate containing one replays
(``REPLAY`` currently raises ``TranscriptMissError`` at that address -- a sibling
of ``ProviderError`` that ``Hybrid`` cannot catch); register the transcript
corpus's content hash in the repository (or registry) so 'an artefact to be
committed, reviewed and re-run against' is checkable by a third party; run and
record one full ``store.misses == 0`` replay across all 18 LLM cells."*

What is actually broken
-----------------------

A refusal records *nothing*.
:meth:`~sciagent.systems.llm.anthropic_provider.AnthropicProvider.complete`
raises :class:`~sciagent.core.errors.ProviderError`,
:meth:`~sciagent.systems.llm.transcripts.TranscriptStore.resolve` never sees it
because the exception escapes the ``call()`` thunk, and
``Hybrid._propose_once`` catches it and writes a scored ``"refused"`` row. So the
*address* is absent from the corpus while the *reading* counts the refusal, and
replaying that replicate raises
:class:`~sciagent.core.errors.TranscriptMissError` at the first refused call.
Every replicate the recorded campaign scored as refused is therefore unreplayable,
which is what makes A35 a blocker on A40's re-derivation.

The boundary this gate must not cross
-------------------------------------

``docs/DECISIONS.md`` (2026-08-21) pins it, from the other side:
``test_a40_a_replay_stops_on_a_miss_rather_than_recording_a_refusal`` requires
that a REPLAY **miss** raises and is never converted into a recorded refusal. The
fix is to *fill the corpus*, never to widen ``Hybrid``'s ``except ProviderError``
to :class:`~sciagent.core.errors.ProposalError`, nor to make
``TranscriptMissError`` a subclass of ``ProviderError``. Both would land a
harness fault in the ledger as a scored datum. The hierarchy those tests rest on
is ``TranscriptMissError`` and ``ProviderError`` as *siblings* under
``ProposalError``, with ``ProviderUnavailableError`` a third sibling and
``SystemConfigurationError`` outside the subtree entirely.

Three of the tests below exist only to hold that boundary from A35's side: a
miss, a transport failure and a misconfigured replay must each keep propagating
once refusals are recordable. A change that recorded any of them would satisfy
the gate's headline and destroy what the gate is for.

Why the address scheme was deliberately **not** versioned here
--------------------------------------------------------------

The entry's **Touches.** suggests versioning the scheme, and the decision taken
with this gate (2026-08-23) was not to.
:data:`~sciagent.systems.llm.transcripts.ADDRESS_VERSION` doubles as the corpus
file's format version, and :meth:`TranscriptStore.load` *refuses* a file whose
version disagrees -- so a bump orphaned the recorded 112-call corpus outright and
made A40's re-derivation unreachable, which is the opposite of what this entry
exists to achieve. Nothing that A35 changes is *hashed*: a refusal is a new
record **kind**, not a new addressing scheme, and the new fields are defaulted
exactly as ``settings`` was.

**That reason has since expired, and the scheme has moved.** A40 landed
(``docs/BACKLOG.md`` rank 12), and gate A36 then bumped to ``transcript/3`` for
three changes that *are* hashed -- a schema that no longer names a mechanism, a
menu that moved from the brief into the system block, and an address that
carries a sample index. The corpora are orphaned as a consequence, deliberately
and with the cost written down.

What survives here is the half of the decision that was never about A40: the
scheme is not moved by a passing edit, and a record written before refusals were
recordable still loads through *defaulted* reads rather than required keys.
``test_a35_a_corpus_recorded_before_refusals_still_loads`` holds both, and its
docstring carries the history.

Cost
----

The campaign helper runs one replicate of one cell on the *gate* table and
persists no table, exactly as ``tests/acceptance/test_a40.py`` argues for the
same helper. The remaining tests build stores and transcripts directly and
simulate nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from slice_tables import gate_table

from environments.pointproc.runner import MatrixRunner, scenario_battery, scenario_seed
from sciagent.core.errors import (
    ProposalError,
    ProviderError,
    ProviderUnavailableError,
    SystemConfigurationError,
    TranscriptMissError,
)
from sciagent.core.types import FrozenDict, ScenarioId
from sciagent.eval.matrix import Cell, run_matrix
from sciagent.registry.ledger import CampaignLedger
from sciagent.systems.llm import (
    RECORD,
    REPLAY,
    Completion,
    Provider,
    RefusingProvider,
    ScriptedProvider,
    Transcript,
    TranscriptStore,
    fixed_payload,
)
from sciagent.systems.llm.transcripts import ADDRESS_VERSION, corpus_digest

#: The cheapest cell that actually calls a provider, and why it is this one:
#: a ``Hybrid`` proposes only where Stage A finds the closed set inadequate, so
#: on S8 the arm completes without a single model call and there is nothing for a
#: corpus to hold. ``tests/acceptance/test_a40.py`` records that it found this by
#: failing -- a green replay test over no replay -- and the same trap is open
#: here.
ABLATION_ARM = "V3"
ABLATION_SCENARIO = "S11"

#: How many calls one replicate of a proposing arm makes: ``Hybrid``'s
#: ``max_proposals`` default, which ``memory_ablation`` passes through to V3.
MAX_PROPOSALS = 2

#: A payload per proposal a full script would serve. What they propose does not
#: matter; that a call is made, and therefore addressed, does.
SCRIPT = (
    fixed_payload(3, (20, 30, 40), name="size_mixture"),
    fixed_payload(1, (10, 20, 30, 40), name="latent_regime"),
)

#: A script one payload short of what the arm will ask for. The second call
#: exhausts it and :class:`ScriptedProvider` raises
#: :class:`~sciagent.core.errors.ProviderError` -- which is how this repository
#: simulates a refusal, and is the condition the whole gate is about.
#:
#: Its cause is ``"exhausted"`` rather than ``"declined"`` deliberately
#: (``scripted.py``, gate A44): ``declined`` is the one bin called unambiguously
#: a fact about a model, and a fixture one entry short of its script must not
#: inflate it. What matters here is that the cause *round-trips*, not which one
#: it is.
SHORT_SCRIPT = SCRIPT[:1]

#: The manifest the gate's second clause requires, relative to the repo root.
MANIFEST = Path(__file__).resolve().parents[2] / "docs" / "CORPUS.md"

#: A digest line in the manifest's fenced block, in ``sha256sum`` format -- two
#: spaces between the digest and the path, so a third party can run
#: ``sha256sum -c`` on the extracted block with no sciagent involved.
DIGEST_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$", re.MULTILINE)


class _UnavailableProvider:
    """A backend that cannot be reached, under a recorded backend's identity.

    Distinct from :class:`ScriptedProvider` running out of script, which is a
    *refusal*. This is a 429, a 529 or a dropped connection: the model was never
    asked, so there is no answer to record and nothing about the investigation to
    learn. ``ProviderUnavailableError`` is a sibling of ``ProviderError`` under
    ``ProposalError`` precisely so that recording the one does not record the
    other.
    """

    id = "scripted"
    model = "scripted/1"
    settings = ""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> Completion:
        """Raise as a rate-limited backend does."""
        self.calls += 1
        raise ProviderUnavailableError("429 from a stand-in backend")


def _campaign(ledger_path: Path, store: TranscriptStore, provider: Provider) -> None:
    """Run one replicate of one ablation cell into ``ledger_path``.

    The real :class:`~environments.pointproc.runner.MatrixRunner` and the real
    :func:`~sciagent.eval.matrix.run_matrix`, on the *gate* table: a campaign
    rather than a stand-in for one. Nothing here persists a table.
    """
    runner = MatrixRunner(gate_table(), provider=lambda: provider, store=store)
    with CampaignLedger.open(ledger_path) as ledger:
        run_matrix(
            (Cell(ABLATION_ARM, ScenarioId(ABLATION_SCENARIO), 1),),
            address=runner.address,
            scenario_seed=scenario_seed,
            battery=scenario_battery,
            execute=runner.execute,
            ledger=ledger,
        )


def _shared_checkout() -> Path:
    """Return the root of the checkout that holds ``.cache/``.

    A worktree has its own tree and the *shared* checkout's ``.git``. Recorded
    corpora live under the latter's ``.cache/``, which is why
    ``git rev-parse --git-common-dir`` is the door CLAUDE.md names for anything
    reading a cached artefact -- ``EmpiricalTable``'s cache resolution already
    goes through it, and every worktree finds the main tree's tables with nothing
    configured.

    Falls back to the repository root two levels up when git cannot answer, which
    is the tarball case rather than a failure: there is no worktree to resolve
    away from, so the two are the same directory.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parents[2]
    return (Path(__file__).resolve().parent / common).resolve().parent


def _readings(ledger_path: Path) -> tuple[FrozenDict[str, float], ...]:
    """Return every reading a ledger holds, in sequence order."""
    with CampaignLedger.open(ledger_path) as ledger:
        rows = sorted(ledger.entries(), key=lambda entry: entry.sequence)
    return tuple(entry.reading for entry in rows)


def _answer(address: str = "call/0", payload: int = 3) -> Transcript:
    """Return a transcript holding an answer, for the append-only tests."""
    return Transcript(
        address=address,
        provider="scripted",
        model="scripted/1",
        brief="brief",
        payload=fixed_payload(payload),
    )


def _refusal(
    address: str = "call/0",
    *,
    cause: str = "declined",
    detail: str = "declined to answer",
) -> Transcript:
    """Return a transcript holding a refusal, for the append-only tests."""
    return Transcript(
        address=address,
        provider="scripted",
        model="scripted/1",
        brief="brief",
        payload={},
        outcome="refused",
        cause=cause,
        detail=detail,
    )


class TestA35RefusalsReplay:
    """The gate: a refusal is a record, and the corpus's hash is in the repo.

    The criterion's two clauses -- byte-identical replay of a run containing a
    refusal at zero misses, and a hash resolvable from the repository -- plus the
    three boundary tests the A40 decision requires and the gate line abbreviates
    away.
    """

    def test_a35_a_refused_replicate_replays(self, tmp_path: Path) -> None:
        """The headline: record a run that refuses, replay it, get it back.

        The arm asks twice; the script serves one payload, so the second call is
        a refusal. Today the corpus keeps one call of the two and the replay
        raises :class:`TranscriptMissError` at the second address.

        Three assertions carry this, and the first is the one that fails today.
        ``misses == 0`` alone asserts nothing -- ``_misses`` is incremented only
        on the branch that calls out, which is unreachable in REPLAY, so it holds
        of every REPLAY store that ever existed including one that replayed
        nothing. What makes it mean something is that the corpus is *complete*
        (both calls, not just the answered one), that the campaign **completed**
        under a REPLAY store whose provider is a
        :class:`~sciagent.systems.llm.provider.RefusingProvider` that raises if
        it is reached at all, and that the readings are the recorded ones rather
        than fresh work.
        """
        recorded = TranscriptStore(mode=RECORD)
        recording_provider = ScriptedProvider(list(SHORT_SCRIPT))
        _campaign(tmp_path / "record.db", recorded, recording_provider)

        assert recording_provider.calls == MAX_PROPOSALS, "the arm did not ask twice"
        assert len(recorded) == MAX_PROPOSALS, (
            "the refusal was not recorded, so the corpus is one call short of "
            "the calls the arm made"
        )
        assert [t.outcome for t in recorded].count("refused") == 1

        # Through the file, not through the object: the gate says a *recorded*
        # run replays, and a refusal that lives only in memory would pass an
        # in-process round trip while being absent from the artefact.
        corpus = tmp_path / "corpus.json"
        recorded.save(corpus)
        store = TranscriptStore.load(corpus, mode=REPLAY)

        _campaign(
            tmp_path / "replay.db",
            store,
            RefusingProvider(id="scripted", model="scripted/1", settings=""),
        )

        assert store.misses == 0
        assert _readings(tmp_path / "replay.db") == _readings(tmp_path / "record.db")

    def test_a35_a_replayed_refusal_carries_the_recorded_cause(
        self, tmp_path: Path
    ) -> None:
        """The replayed error is the recorded one, tag and message alike.

        ``Hybrid._propose_once`` reads ``error.cause`` off the exception and
        ``str(error)`` into the attempt's detail, and
        :func:`~sciagent.eval.agency.proposal_causes` refuses a cause outside
        :data:`~sciagent.eval.agency.PROPOSAL_CAUSES`. So a replay that raised a
        *generic* refusal would replay the reading's ``refused`` count while
        moving its cause histogram -- the byte-identity the gate asks for is not
        the ledger row alone.

        **Asserted by actually replaying**, at the ``resolve`` seam, and a review
        is why. An earlier version of this test asserted against the *recording*
        store -- so it read back the fields it had just written and never
        resolved anything out of a REPLAY store at all. Two wrong
        implementations passed it: one that re-raised a hardcoded generic cause,
        which is verbatim the failure :attr:`Transcript.cause` exists to forbid;
        and one that reconstructed no error at all and handed the empty payload
        to the decoder, which ``Hybrid`` then files as ``"malformed"``. The
        campaign leg cannot see either, because ``CellReading.as_payload`` emits
        no proposal tier and no cause histogram -- the ledger reading is blind to
        both.
        """
        recorded = TranscriptStore(mode=RECORD)
        provider = ScriptedProvider(list(SHORT_SCRIPT))
        _campaign(tmp_path / "record.db", recorded, provider)

        refusals = [t for t in recorded if t.outcome == "refused"]
        assert len(refusals) == 1
        assert refusals[0].cause == "exhausted", (
            "the cause did not survive the recording; a replay would re-tag it"
        )
        assert "scripted provider was asked for completion" in refusals[0].detail
        assert refusals[0].payload == {}, "a refusal has no tool payload to hold"

        # The replay leg. `resolve` is the read path a campaign takes; `get` is
        # not, which is what let the earlier version of this test miss the gap.
        replay = TranscriptStore({t.address: t for t in recorded}, mode=REPLAY)
        with pytest.raises(ProviderError) as raised:
            replay.resolve(
                refusals[0].address,
                lambda: pytest.fail("a replay called out"),
                provider=refusals[0].provider,
                model=refusals[0].model,
                brief=refusals[0].brief,
            )

        assert raised.value.cause == "exhausted", (
            "the replay re-tagged the refusal, which moves the cause histogram "
            "while leaving the ledger row identical"
        )
        assert str(raised.value) == refusals[0].detail
        assert replay.misses == 0

    def test_a35_a_refusal_on_the_first_call_replays_at_the_same_length(
        self, tmp_path: Path
    ) -> None:
        """A refusal that *stops* the proposal loop replays as one that does.

        ``_extend`` breaks on ``"refused"`` and continues on the other four
        outcomes, so what a refusal is reconstructed *as* decides how many calls
        the arm goes on to make. A replay that raised anything but a refusal here
        would ask a second time, at an address the corpus does not hold, and miss
        -- the gate's own headline, failing on exactly the replicates it exists
        to unblock.

        This needs the refusal at proposal index **0**. The gate test above puts
        it on the last of two calls, which is the one position where break and
        continue cost the same and the divergence is invisible; a review found
        that hole. An empty script refuses immediately.
        """
        recorded = TranscriptStore(mode=RECORD)
        recording_provider = ScriptedProvider([])
        _campaign(tmp_path / "record.db", recorded, recording_provider)

        assert recording_provider.calls == 1, (
            "the arm did not stop at the refusal, so this tests nothing"
        )
        assert len(recorded) == 1
        assert [t.outcome for t in recorded] == ["refused"]

        store = TranscriptStore({t.address: t for t in recorded}, mode=REPLAY)
        _campaign(
            tmp_path / "replay.db",
            store,
            RefusingProvider(id="scripted", model="scripted/1", settings=""),
        )

        assert store.misses == 0
        assert _readings(tmp_path / "replay.db") == _readings(tmp_path / "record.db")

    def test_a35_a_miss_still_raises_rather_than_becoming_a_refusal(
        self, tmp_path: Path
    ) -> None:
        """A40's boundary, held from this side: an empty corpus stops the run.

        The failure this forbids is the one A35 could most easily introduce.
        Recording refusals means there is now a path that *converts a provider
        exception into a stored record*; widening the clause that reads it by one
        class -- to ``ProposalError``, the parent both ``ProviderError`` and
        ``TranscriptMissError`` sit under -- would make a replay against an
        incomplete corpus report a complete matrix scored on refusals nobody
        made.

        **What this pins is ``Hybrid``'s narrow catch, not ``resolve``'s.** A
        review corrected an earlier docstring here that located the hazard in
        ``resolve``: a REPLAY miss raises at the ``mode != RECORD`` guard, before
        the ``call()`` thunk is ever invoked, so no widening *there* could reach
        this. ``test_a35_a_transport_failure_is_not_recorded`` catches the same
        widening independently, which is why the claim that it "would also pass
        every other test in this class" was struck rather than softened.
        """
        with pytest.raises(TranscriptMissError):
            _campaign(
                tmp_path / "replay.db",
                TranscriptStore(mode=REPLAY),
                ScriptedProvider(list(SCRIPT)),
            )

    def test_a35_a_transport_failure_is_not_recorded(self, tmp_path: Path) -> None:
        """A 429 propagates and stores nothing, once refusals store something.

        ``ProviderUnavailableError`` is a *sibling* of ``ProviderError``, not a
        subclass, exactly so that this holds. Recording it would write a refusal
        nobody made into an append-only corpus and score a replicate a rate limit
        degraded -- ``anthropic_provider.py`` records that this was a real defect
        once, fixed the other way round.
        """
        store = TranscriptStore(mode=RECORD)
        provider = _UnavailableProvider()

        with pytest.raises(ProviderUnavailableError):
            _campaign(tmp_path / "record.db", store, provider)

        assert provider.calls >= 1, (
            "the backend was never reached, so this tests nothing"
        )
        assert len(store) == 0, "a transport failure was recorded as an answer"

    def test_a35_a_replay_that_reaches_a_provider_is_not_recorded(self) -> None:
        """``RefusingProvider``'s fault stays a fault, not a recorded refusal.

        It raises ``SystemConfigurationError``, which sits outside
        ``ProposalError`` entirely and so cannot be caught by a clause reaching
        for ``ProviderError``. Reaching it means a run configured to replay did
        not -- recording that would turn a misconfigured replay into a corpus of
        refusals and a matrix built on them.

        Driven through ``resolve`` in RECORD mode, which is the only mode whose
        ``call()`` thunk is ever invoked, and therefore the only place the
        conversion this gate adds could swallow it.
        """
        store = TranscriptStore(mode=RECORD)
        provider = RefusingProvider(id="scripted", model="scripted/1", settings="")

        with pytest.raises(SystemConfigurationError):
            store.resolve(
                "call/0",
                lambda: provider.complete("system", "brief", {}),
                provider=provider.id,
                model=provider.model,
                brief="brief",
            )

        assert len(store) == 0

    def test_a35_an_answer_and_a_refusal_do_not_overwrite_each_other(self) -> None:
        """Append-only holds across record kinds, in both directions.

        :meth:`TranscriptStore.put` compared *payloads*, which was a complete
        description of "the answer" while every record was an answer. A refusal
        carries an empty payload, so under the old comparison a refusal would
        silently replace another refusal that named a different cause -- and the
        corpus would record a decline where an output ceiling happened.
        """
        store = TranscriptStore(mode=RECORD)
        store.put(_answer())

        with pytest.raises(ProposalError):
            store.put(_refusal())

        other = TranscriptStore(mode=RECORD)
        other.put(_refusal())
        with pytest.raises(ProposalError):
            other.put(_answer())
        with pytest.raises(ProposalError):
            other.put(_refusal(cause="output_ceiling", detail="hit the ceiling"))

        # And an identical re-recording still agrees, which is what makes this
        # append-only rather than write-once.
        other.put(_refusal())
        assert len(other) == 1

        # The message is **not** part of identity, and this is what pins that.
        # A review reproduced the alternative: with `detail` in the comparison,
        # two runs differing only in `max_tokens` -- a value the addressing
        # scheme excludes *deliberately*, because a ceiling "either yields that
        # answer or raises" -- collided at one address, because the ceiling
        # refusal interpolates the ceiling into its message. Including the
        # message made this module break `anthropic_provider`'s stated
        # invariant. The first wording wins, exactly as provenance does.
        other.put(_refusal(detail="declined to answer, worded differently"))
        assert len(other) == 1
        assert other.get("call/0").detail == "declined to answer"

    def test_a35_a_saved_refusal_survives_the_file(self, tmp_path: Path) -> None:
        """The record kind round-trips through JSON, and a save cannot drop it.

        ``save``'s ``_refuse_to_drop`` compares what is on disk against what is
        being written, on the same terms ``put`` compares. A comparison still
        reading payloads alone would let a second recording session overwrite a
        recorded refusal's cause with another's, silently, in a file whose diff
        would show nothing but the thing nobody was looking at.
        """
        corpus = tmp_path / "corpus.json"
        store = TranscriptStore(mode=RECORD)
        store.put(_answer("call/0"))
        store.put(_refusal("call/1", cause="declined", detail="declined to answer"))
        store.save(corpus)

        reloaded = TranscriptStore.load(corpus, mode=REPLAY)
        assert reloaded.get("call/1").outcome == "refused"
        assert reloaded.get("call/1").cause == "declined"
        assert reloaded.get("call/1").detail == "declined to answer"
        assert reloaded.get("call/0").outcome == "answered"

        clashing = TranscriptStore(mode=RECORD)
        clashing.put(_answer("call/0"))
        clashing.put(_refusal("call/1", cause="output_ceiling", detail="ceiling"))
        with pytest.raises(ProposalError):
            clashing.save(corpus)

    def test_a35_an_unreadable_record_kind_does_not_let_save_truncate(
        self, tmp_path: Path
    ) -> None:
        """A corpus this process cannot fully read is never silently replaced.

        ``_refuse_to_drop`` treats exactly one read failure as safe to overwrite:
        a corpus addressed under an older *scheme*, every call in which would
        miss anyway, so refusing would strand it forever. Its docstring says
        every other failure propagates -- and adding a record kind gave the
        module a second failure that clause had never seen.

        This is a defect the A35 review found and it was **reproduced, not
        argued**: with the check written at ``ProposalError``, a corpus holding
        one unimplemented kind was read as "drops nothing", and ``save`` replaced
        the file -- losing the unreadable record *and every well-formed call
        beside it*. :class:`~sciagent.core.errors.TranscriptSchemeError` is what
        separates the two, and this test is what holds them apart.

        The well-formed neighbour is the load-bearing half. A test asserting only
        that ``save`` raises would pass against an implementation that raised and
        truncated anyway, and the call nobody can reconstruct is the one that had
        nothing wrong with it.
        """
        corpus = tmp_path / "corpus.json"
        corpus.write_text(
            json.dumps(
                {
                    "version": ADDRESS_VERSION,
                    "calls": [
                        {
                            "address": "call/unreadable",
                            "provider": "scripted",
                            "model": "scripted/1",
                            "settings": "",
                            "brief": "brief",
                            "outcome": "throttled",
                            "payload": {},
                            "provenance": {},
                        },
                        _answer("call/intact").as_json(),
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        with pytest.raises(ProposalError, match="throttled"):
            TranscriptStore.load(corpus, mode=REPLAY)

        fresh = TranscriptStore(mode=RECORD)
        fresh.put(_answer("call/new"))
        with pytest.raises(ProposalError, match="throttled"):
            fresh.save(corpus)

        on_disk = json.loads(corpus.read_text(encoding="utf-8"))
        assert sorted(call["address"] for call in on_disk["calls"]) == [
            "call/intact",
            "call/unreadable",
        ], "save() replaced a corpus it could not read"

    def test_a35_an_old_scheme_corpus_may_still_be_replaced(
        self, tmp_path: Path
    ) -> None:
        """The carve-out the narrow error class exists to preserve.

        ``_refuse_to_drop`` refuses to overwrite a corpus it cannot read --
        except one addressed under an older *scheme*, where every call would miss
        anyway and refusing would strand the file forever. Narrowing the clause
        to :class:`~sciagent.core.errors.TranscriptSchemeError` closed a
        truncation hole; nothing pinned the half it had to keep open, so both
        clauses could be deleted outright and the suite stayed green. Two
        reviewers found that independently.

        The permissive half and the strict half are one decision, and a test of
        only the strict half describes an implementation that strands every
        orphaned corpus.
        """
        corpus = tmp_path / "old.json"
        corpus.write_text(
            json.dumps(
                {
                    "version": "transcript/1",
                    "calls": [_answer("call/orphaned").as_json()],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        with pytest.raises(ProposalError, match="address scheme"):
            TranscriptStore.load(corpus, mode=REPLAY)

        fresh = TranscriptStore(mode=RECORD)
        fresh.put(_answer("call/new"))
        fresh.save(corpus)

        on_disk = json.loads(corpus.read_text(encoding="utf-8"))
        assert on_disk["version"] == ADDRESS_VERSION
        assert [call["address"] for call in on_disk["calls"]] == ["call/new"]

    def test_a35_a_pre_a35_corpus_round_trips_byte_identically(
        self, tmp_path: Path
    ) -> None:
        """Loading and saving a corpus of answers must not move its bytes.

        ``docs/CORPUS.md`` publishes a SHA-256 over the file, and
        ``run_matrix``'s ``checkpoint`` calls ``save`` after **every replicate**.
        So a record kind that wrote its keys unconditionally would rewrite the
        112-call section 9 corpus on the first recording pass that resumed
        against it, moving the digest the repository vouches for -- silently, and
        to the one artefact every LLM number rests on.

        A review reproduced exactly that (659,740 bytes to 667,356, and the
        digest with it) before :meth:`Transcript.as_json` was made to omit the
        keys a plain answer does not use. This test is what keeps it that way,
        and it asserts the *bytes* rather than the parsed content, because the
        published hash is over bytes.
        """
        corpus = tmp_path / "answers.json"
        original = json.dumps(
            {
                "version": ADDRESS_VERSION,
                "calls": [
                    {
                        "address": "call/0",
                        "brief": "brief",
                        "model": "scripted/1",
                        "payload": fixed_payload(3),
                        "provenance": {},
                        "provider": "scripted",
                        "settings": "",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        corpus.write_text(original + "\n", encoding="utf-8", newline="\n")
        before = corpus.read_bytes()
        digest_before = corpus_digest(corpus)

        reloaded = TranscriptStore.load(corpus, mode=RECORD)
        reloaded.save(corpus)

        assert corpus.read_bytes() == before, (
            "a load/save round trip rewrote a corpus of answers, so every "
            "digest docs/CORPUS.md publishes would go stale on the next "
            "recording pass that resumed against it"
        )
        assert corpus_digest(corpus) == digest_before

    def test_a35_a_corpus_recorded_before_refusals_still_loads(
        self, tmp_path: Path
    ) -> None:
        """A record carrying no ``outcome`` key reads as the answer it is.

        The three refusal fields are *defaulted reads*, not required ones, and
        that is the whole of what this exercises -- against a file written by
        hand rather than by :meth:`TranscriptStore.save`, so the defaults are
        exercised rather than the writer's own output.

        **The subject is the defaults, not the era.** A review of A36 pointed out
        that this docstring used to say "a corpus recorded before refusals were
        recordable", which after the scheme bump names a file this reader now
        refuses outright — an unrepresentable subject. The finding was right
        about the framing and the framing is fixed; it does not make the test
        vacuous, because :meth:`Transcript.as_json` writes the three keys **only
        for a refusal**, so a present-day corpus of answers omits them exactly as
        a pre-A35 one did. The defaults are load-bearing for every corpus, not
        for a historical one.

        **The scheme literal is asserted, and it has moved since A35 wrote this.**
        A review found that an earlier version built its "legacy" file with
        ``save`` and read it back with ``load``. That round trip is
        version-*agnostic* -- ``save`` stamps whatever
        :data:`~sciagent.systems.llm.transcripts.ADDRESS_VERSION` currently says
        and ``load`` compares against the same constant -- so it passed under
        ``"banana/99"`` while claiming a bump would fail here. The constant is
        therefore asserted directly, and the fixture carries a literal.

        **What A35 decided, and why the literal below is no longer
        ``transcript/2``.** ``docs/DECISIONS.md`` (2026-08-23) declined to bump
        the scheme for A35, because the constant doubles as the corpus file's
        format version and a bump orphans the recorded 112-call corpus that
        A40's re-derivation needed -- the very thing A35 existed to unblock. A40
        landed (``docs/BACKLOG.md`` rank 12), so that reason expired, and gate
        A36 bumped to ``transcript/3`` on purpose: the tool schema, the brief and
        the address's shape all changed. That entry's closing line said A36
        "still bumps the scheme deliberately, and still belongs after A40", which
        is what happened. This assertion keeps doing its job either way -- an
        *unplanned* bump is still a red here, and the value it names is the
        decision of record.
        """
        assert ADDRESS_VERSION == "transcript/3", (
            "the address scheme moved without a decision naming it. A bump "
            "orphans every recorded corpus, so it belongs to a gate that says "
            "so -- A36 was the last one -- and not to a passing edit"
        )

        corpus = tmp_path / "legacy.json"
        corpus.write_text(
            json.dumps(
                {
                    # A literal, not the constant. The review that produced this
                    # test measured that reading the constant back makes the
                    # fixture version-agnostic, so a bump would move both halves
                    # at once and neither would notice.
                    "version": "transcript/3",
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
        assert "outcome" not in corpus.read_text(encoding="utf-8")

        reloaded = TranscriptStore.load(corpus, mode=REPLAY)
        assert reloaded.get("call/0").outcome == "answered"
        assert reloaded.get("call/0").cause == ""
        assert reloaded.get("call/0").payload == fixed_payload(3)

    def test_a35_the_corpus_hash_is_resolvable_from_the_repository(
        self, tmp_path: Path
    ) -> None:
        """The gate's second clause: a third party can check what we replayed.

        The corpus itself stays out of git (``.cache/`` is ignored and the file
        is 660KB), so what the repository carries is its digest.

        **Which paths a conditional check is allowed to skip is the whole
        difficulty**, and a review found the first version got it wrong twice. It
        resolved the manifest's paths against the *worktree* root -- but
        ``.cache/`` lives in the shared checkout, so the comparison was dead in
        every editing session as well as on CI, and "verified wherever it is
        present" meant verified nowhere. And a conditional that skips absent
        paths passes forever on a manifest naming a path that never resolves: a
        typo is indistinguishable from an unfetched corpus.

        Both are closed. Paths resolve through ``git rev-parse
        --git-common-dir``, which is the door CLAUDE.md names for anything
        reading a cached artefact and is what makes every worktree find the main
        tree's ``.cache/``. And each named path is checked *structurally* --
        under ``.cache/transcripts/``, ending ``.json`` -- which runs everywhere
        and catches the typo the existence check cannot.

        What remains genuinely conditional, stated rather than hidden: on a fresh
        checkout no corpus has been recorded or fetched, so the **digest
        comparison** does not run there. It runs locally, which is where the
        corpus and the recording that produced it live.

        The format is literal ``sha256sum`` output so the check does not depend
        on this framework: a reader extracts the block and runs ``sha256sum -c``.
        """
        assert MANIFEST.exists(), "the repository records no corpus hash"
        text = MANIFEST.read_text(encoding="utf-8")
        entries = DIGEST_LINE.findall(text)
        assert entries, "the manifest names no corpus with a well-formed digest"

        # The function reproduces what the manifest format promises: a plain
        # sha256 over the file's bytes, which `save` makes stable by writing
        # sorted keys, indent=2 and LF regardless of platform.
        built = tmp_path / "built.json"
        store = TranscriptStore(mode=RECORD)
        store.put(_answer("call/0"))
        store.put(_refusal("call/1"))
        store.save(built)
        assert corpus_digest(built) == hashlib.sha256(built.read_bytes()).hexdigest()

        # Structural, and therefore unconditional: a path that could never name a
        # corpus is a defect in the manifest whether or not the file is here.
        for _digest, relative in entries:
            assert relative.startswith(".cache/transcripts/"), (
                f"{relative!r} is not where a recorded corpus lives"
            )
            assert relative.endswith(".json"), f"{relative!r} is not a corpus file"

        root = _shared_checkout()
        present = [
            (digest, root / relative)
            for digest, relative in entries
            if (root / relative).exists()
        ]
        for digest, path in present:
            assert corpus_digest(path) == digest, (
                f"{path} does not match the digest the repository records for it"
            )
