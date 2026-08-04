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
the system prompt, the rendered brief, the tool schema, and the index of the call
within the investigation. The last one matters because a system may ask twice
with an identical brief -- after an experiment that moved nothing, say -- and the
two calls are different events that may legitimately get different answers.

It deliberately does **not** cover the scenario id or the seed. Those reach the
address only through the brief, which is where they belong: two scenarios that
present a model with the identical brief are, as far as the model is concerned,
the same question, and giving them different addresses would record the same
answer twice and hide that fact.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sciagent.core.errors import ProposalError, TranscriptMissError
from sciagent.core.program import stable_key

__all__ = [
    "RECORD",
    "REPLAY",
    "Transcript",
    "TranscriptMode",
    "TranscriptStore",
    "call_address",
]

type TranscriptMode = Literal["replay", "record"]

#: Refuse to call out; a missing address is an error. What evaluation runs under.
REPLAY: TranscriptMode = "replay"

#: Call out on a miss and store the answer. A deliberate, separate act.
RECORD: TranscriptMode = "record"

#: Version of the address scheme itself. Mixed into every address, so that a
#: change to *what* is hashed invalidates stored transcripts rather than silently
#: matching them against a differently-computed key -- the same promise
#: ``OPERATIONS_VERSION`` makes for the empirical table's cache.
ADDRESS_VERSION = "transcript/1"


def call_address(
    *,
    provider: str,
    model: str,
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
    """
    payload = "\x00".join(
        (
            ADDRESS_VERSION,
            provider,
            model,
            system,
            brief,
            json.dumps(schema, sort_keys=True, separators=(",", ":")),
            str(index),
        )
    )
    return f"call/{stable_key(payload) % (1 << 64):016x}"


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

    def as_json(self) -> dict[str, Any]:
        """Return the JSON form written to disk."""
        return {
            "address": self.address,
            "provider": self.provider,
            "model": self.model,
            "brief": self.brief,
            "payload": dict(self.payload),
        }


class TranscriptStore:
    """An append-only store of recorded model calls.

    Append-only for the reason the registry is (SPEC §6.3 A12): a transcript
    that could be overwritten is a record of what the model says *now*, and the
    whole point is a record of what it said when the result was measured. Storing
    a different payload at an existing address raises rather than replacing.

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
        """Store a call. Raises if the address already holds a different one."""
        existing = self._entries.get(transcript.address)
        if existing is not None:
            if existing.payload != transcript.payload:
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
        call: object,
        *,
        provider: str,
        model: str,
        brief: str,
    ) -> Transcript:
        """Return the response at ``address``, calling out only if permitted.

        ``call`` is invoked exactly once, and only on a miss in :data:`RECORD`
        mode. In :data:`REPLAY` a miss raises, which is the whole guarantee: an
        evaluation run cannot quietly become a live one because someone had
        credentials in their environment.
        """
        if address in self._entries:
            return self._entries[address]
        if self._mode != RECORD:
            raise TranscriptMissError(
                f"no recorded response at {address} and the store is in "
                f"{self._mode!r} mode, so it will not call out. Record the "
                f"transcript deliberately, or run against a store that holds it"
            )
        if not callable(call):
            raise ProposalError(
                f"resolving {address} needs a callable to produce the response, "
                f"got {type(call).__name__}"
            )
        payload = call()
        if not isinstance(payload, Mapping):
            raise ProposalError(
                f"a provider must return a mapping payload, got "
                f"{type(payload).__name__}"
            )
        transcript = Transcript(
            address=address,
            provider=provider,
            model=model,
            brief=brief,
            payload=dict(payload),
        )
        self.put(transcript)
        self._misses += 1
        return transcript

    # -- persistence -------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write the store to ``path`` as JSON, sorted by address.

        Sorted and indented so that a recorded transcript is a reviewable diff
        rather than one long line whose changes cannot be read.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": ADDRESS_VERSION,
            "calls": [self._entries[key].as_json() for key in sorted(self._entries)],
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path, *, mode: TranscriptMode = REPLAY) -> TranscriptStore:
        """Return the store recorded at ``path``.

        Raises :class:`~sciagent.core.errors.ProposalError` if the file was
        written under a different address scheme, on the same reasoning as
        :meth:`~sciagent.inference.empirical.EmpiricalTable.load` refusing a
        table whose content address disagrees: a file that cannot be addressed
        the way this process addresses things is not this store, and reading it
        anyway would produce misses that look like the model having changed.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("version")
        if version != ADDRESS_VERSION:
            raise ProposalError(
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
                payload=dict(call["payload"]),
            )
            for call in raw.get("calls", [])
        }
        return cls(entries, mode=mode)

    def __iter__(self) -> Iterator[Transcript]:
        """Yield stored calls in address order."""
        for address in sorted(self._entries):
            yield self._entries[address]
