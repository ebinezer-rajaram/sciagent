"""The Anthropic-backed provider.

The only module in the framework that talks to a network, and it is deliberately
thin: it renders a request, constrains the response to a schema, and hands back
the payload. Everything that has to be reproducible -- addressing, replay,
decoding, grammar validation -- is
:class:`~sciagent.systems.llm.provider.ProposalLayer`'s, which is why a recorded
transcript can be replayed with this module never imported.

Three choices are worth stating, because each is the opposite of the usual
default and each follows from what this framework is for.

**Structured output, not tool use.** The response is constrained by
``output_config.format`` with the schema
:func:`~sciagent.systems.llm.encoding.tool_schema` builds. One structured object
is wanted, not an agentic loop, and the JSON-schema path makes the "no numbers"
guarantee a property of the wire format: the schema contains no ``number``
anywhere, so a conforming response cannot carry one.

**No sampling parameters.** ``temperature``, ``top_p`` and ``top_k`` are not
sent, because the models targeted here reject them outright. This is the fact
that forces the transcript store to exist: there is no setting that makes two
calls with one prompt agree, so a recorded response is the reproducible artefact
rather than a cache of one. See
:mod:`sciagent.systems.llm.transcripts`.

**No refusal fallback.** A model refusal raises
:class:`~sciagent.core.errors.ProviderError` and nothing is recorded. Falling
back to another model would be the ordinary advice and is wrong here: a
transcript's address covers the model id, so a response served by a substitute
would be stored under an address naming a model that did not produce it. A
provenance chain that quietly lies about which model answered is worse for this
framework than a run that stops.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sciagent.core.errors import ProviderError, ProviderUnavailableError
from sciagent.systems.llm.transcripts import Completion

if TYPE_CHECKING:  # pragma: no cover - import cost, not behaviour
    from anthropic import Anthropic

__all__ = ["DEFAULT_MODEL", "AnthropicProvider"]

#: The model proposals are recorded against. Named here rather than passed at
#: every call site so that a recorded corpus has one model behind it and a change
#: is a single reviewable edit that invalidates every address.
DEFAULT_MODEL = "claude-opus-5"

#: Output allowance. Thinking is on by default on this model family and counts
#: against the same ceiling, so this is sized for the reasoning rather than for
#: the payload, which is a few hundred bytes of JSON.
DEFAULT_MAX_TOKENS = 16000


class AnthropicProvider:
    """A provider backed by the Anthropic Messages API.

    The client is constructed lazily, on the first completion, so that a provider
    can be built, inspected and have its identity hashed into an address in an
    environment with no credentials at all. That is what lets the address of a
    call be computed -- and a replay satisfied -- without the ability to make one.
    """

    __slots__ = ("_client", "_effort", "_max_tokens", "_model")

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = "high",
        client: Anthropic | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._client = client

    @property
    def id(self) -> str:
        """Return the backend identifier, which is part of every address."""
        return "anthropic"

    @property
    def model(self) -> str:
        """Return the model identifier, which is part of every address."""
        return self._model

    @property
    def settings(self) -> str:
        """Return the request settings that could change the answer.

        ``effort`` only. ``max_tokens`` is deliberately excluded, and the two
        cases are worth separating because they look alike: effort changes how
        the model reasons, and so changes the answer; the token ceiling either
        yields that answer or **raises** (see :meth:`complete`), so it can never
        produce a different recorded payload. Addressing it would mean a bump to
        :data:`DEFAULT_MAX_TOKENS` turning every replay into a miss and re-billing
        a whole recorded matrix for headroom nobody's answer depended on.

        Same rule as the Agent SDK backend's turn ceiling: a bound that aborts is
        not a bound that alters.
        """
        return f"effort={self._effort}"

    def complete(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> Completion:
        """Return a payload conforming to ``schema``.

        Raises :class:`~sciagent.core.errors.ProviderError` for a refusal, a
        truncated response, or a body that is not the JSON object the schema
        demands. Each is reported with what actually came back, because a
        provider failure during a recording run is the thing being debugged and
        a bare "no proposal" would not be enough to debug it.

        Raises :class:`~sciagent.core.errors.ProviderUnavailableError` when the
        API could not be reached at all -- a 429, a 529, a dropped connection, a
        credential the account no longer honours, or the SDK not being installed.
        That one propagates rather than being recorded; see the guard below.
        """
        try:
            from anthropic import AnthropicError
        except ImportError as error:  # pragma: no cover - dependency present
            # Guarded for the same reason ``_messages`` guards its own import: a
            # bare ``ImportError`` is not a ``SciAgentError`` and would cross
            # ``run_matrix``'s handler, which is the escape shape this module was
            # just fixed for. Missing the SDK is a fault of the machine, so it
            # takes the propagating class rather than the recorded one.
            raise ProviderUnavailableError(
                "the anthropic SDK is not installed, so no call can be made; "
                "replaying a recorded transcript does not need it"
            ) from error

        try:
            response = self._messages().create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": dict(schema)},
                },
                messages=[{"role": "user", "content": brief}],
            )
        except AnthropicError as error:
            # The transport clause of this exception's own contract, which was
            # unhonoured until 2026-08-18: a 429, a 529 or a dropped connection
            # arrived as an ``anthropic`` exception, which is not a
            # ``SciAgentError`` and so crossed both of ``run_matrix``'s handlers.
            # Neither checkpoints, so the in-flight replicate's transcripts went
            # with it -- the case that runner's docstring calls unrecoverable.
            #
            # ``AnthropicError`` is the SDK's root and the guard is deliberately
            # written there rather than at the status classes: 529 is
            # ``OverloadedError``, a *sibling* of ``InternalServerError`` and not
            # a subclass, so a guard reasoning from status codes misses the one
            # failure a long recording run is likeliest to meet. This SDK raises
            # nothing bare, so its root is both floor and ceiling.
            #
            # ``ProviderUnavailableError``, **not** ``ProviderError``. The first
            # draft of this guard raised the latter, which ``Hybrid`` catches and
            # records as ``"refused"`` -- so the investigation completed, was
            # scored, and ``run_matrix`` checkpointed a reading into an
            # append-only ledger that a 429 had degraded. That is worse than the
            # crash it replaced: the crash lost transcripts, this wrote a wrong
            # number and could not even be replayed, since a failed call stores
            # nothing. Propagating keeps the checkpointing that motivated the
            # guard and drops the scoring that came with it.
            raise ProviderUnavailableError(
                f"the call to {self._model} could not be completed: "
                f"{type(error).__name__}: {error}. Nothing was recorded, so this "
                f"replicate can be re-run once the cause has cleared"
            ) from error
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ProviderError(
                f"{self._model} declined to answer "
                f"(category {getattr(details, 'category', None)!r}). Nothing is "
                f"recorded: a refusal is an outcome of the investigation, not a "
                f"call to be retried on a different model",
                cause="declined",
            )
        if response.stop_reason == "max_tokens":
            raise ProviderError(
                f"{self._model} hit the {self._max_tokens}-token ceiling before "
                f"finishing; thinking counts against the same allowance, so raise "
                f"max_tokens rather than lowering effort",
                cause="output_ceiling",
            )
        return Completion(
            payload=_payload_of(response, self._model),
            # What the API says served the request, which need not be the alias
            # that was asked for. Recorded, never addressed.
            provenance={"response_model": str(getattr(response, "model", ""))},
        )

    def _messages(self) -> Any:
        """Return the Messages resource, constructing the client on first use."""
        if self._client is None:
            # Unguarded on purpose, and this is the *only* place the client is
            # built. :meth:`complete` is the sole caller and has already imported
            # from ``anthropic`` behind its own ``ImportError`` guard, so the
            # package is importable by the time this runs. A second guard here
            # was unreachable code raising a *different* class for the same
            # condition, which is how the two answers to "SDK missing" came
            # to disagree.
            from anthropic import Anthropic

            self._client = Anthropic()
        return self._client.messages


def _payload_of(response: Any, model: str) -> Mapping[str, Any]:
    """Return the JSON object a structured-output response carries.

    ``output_config.format`` guarantees the first text block is valid JSON
    matching the schema, so this is a read rather than a parse of free prose.
    It still checks, because a guarantee that is never asserted is a guarantee
    nobody notices the loss of.
    """
    text = next(
        (
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        ),
        None,
    )
    if text is None:
        kinds = [getattr(block, "type", "?") for block in response.content]
        raise ProviderError(
            f"{model} returned no text block to read a proposal from; the "
            f"response carried {kinds!r}",
            cause="malformed_response",
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProviderError(
            f"{model} returned a text block that is not JSON, though "
            f"output_config.format was set: {text[:200]!r}",
            cause="malformed_response",
        ) from error
    if not isinstance(payload, dict):
        raise ProviderError(
            f"{model} returned a JSON {type(payload).__name__} where the schema "
            f"declares an object",
            cause="malformed_response",
        )
    return payload
