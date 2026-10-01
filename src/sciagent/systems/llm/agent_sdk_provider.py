"""The Claude Agent SDK backend, authenticated by subscription.

The same job as :mod:`~sciagent.systems.llm.anthropic_provider` -- render a
request, constrain the response to a schema, hand back the payload -- reached
through a different door. The Agent SDK spawns a Claude Code process which
authenticates with ``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``,
drawing on a Claude subscription rather than on API credits.

It is a sibling rather than a replacement, and the two are meant to coexist. The
provider id is part of every call address, so a corpus recorded through here and
one recorded through the Messages API cannot resolve each other's calls -- which
is correct, and is also the reason to choose a backend *before* recording rather
than after.

Hermeticity is the whole difficulty
-----------------------------------

Claude Code is built to pick up context from its surroundings: ``~/.claude``, the
project's ``.claude/``, ``CLAUDE.md``, skills, hooks, MCP servers. Every one of
those would be an input to the model that the brief does not mention and the
address therefore does not cover, which would make
:func:`~sciagent.systems.llm.transcripts.call_address` a hash over something that
no longer determines the request. A recorded corpus would then be reproducible
only on the machine that recorded it.

``--bare`` is Claude Code's own answer to this and cannot be used whole: bare mode
never reads OAuth credentials, so the flag is mutually exclusive with the
subscription auth that is the point of this module. The option set in
:meth:`_options` is that guarantee assembled by hand, and every field in it is
load-bearing. Three are subtler than they look:

* ``skills=[]`` states the intent the SDK documents -- ``[]`` suppresses skills
  from the listing, while ``None`` is its *no-op* and leaves the CLI's defaults
  in place -- but it buys nothing observable here, and the honest version is
  worth recording. The SDK emits no ``--skills`` flag: ``options.skills`` only
  feeds ``--allowedTools`` and a ``setting_sources`` default, so ``[]`` and
  ``None`` produce an identical CLI invocation under this option set, and a live
  session lists the same sixteen bundled skills either way. What actually keeps
  them out of the model's context is the replaced system prompt and the absent
  ``Skill`` tool -- see the 593-token measurement below. ``[]`` is kept because
  it is what the SDK says to write and costs nothing; it is not the guarantee.
* ``system_prompt`` is passed as a bare string, which *replaces* Claude Code's
  own prompt. The preset form (``{"type": "preset", ...}``) would prepend it.
* There is deliberately **no** ``env={"CLAUDE_CODE_SIMPLE": "1"}`` here, and it
  is worth saying why so nobody adds it. ``--bare``'s help says it "Sets
  CLAUDE_CODE_SIMPLE=1", which suggests the context-skipping could be had
  without the flag's auth restriction. Setting it does drop ``memory_paths``
  from the session manifest while ``apiKeySource`` stays ``"none"``, so it looks
  like it works -- and then **every turn fails**: ``is_error=True`` with a
  ``success`` subtype, no output, and ``total_cost_usd == 0``, meaning no model
  call happened at all. A control turn without the variable succeeds. The two
  halves of bare mode are not separable; a manifest that looks right is not a
  turn that works.

Verified against live sessions rather than assumed. With this option set the CLI
reports ``tools == ["StructuredOutput"]`` -- every built-in tool off -- no project
skills, no project agents, no MCP servers and no plugins, and a total prompt of
593 cached tokens. That last number is the useful one: Claude Code's own system
prompt plus sixteen bundled skill descriptions would be thousands of tokens, so
the manifest still *listing* bundled skills reflects what the session knows about,
not what reaches the model.

The one thing the manifest does still report is a resolved ``memory_paths.auto``,
the per-project auto-memory directory -- which is where a Claude Code session is
told to write memories, so an apparent contamination path that would arm itself
later. It does not leak: a canary file planted in that directory and a session
asked to report any such token back answered ``"none"``. Resolved in the manifest,
not read into the prompt. If that ever changes, the canary is how to find out.

Three choices carried over unchanged from the Messages API backend, because they
follow from what the framework is for rather than from which door was used:
structured output rather than an agentic loop, no sampling parameters, and no
refusal fallback.

And one that is new here. The Agent SDK reports which models actually served a
turn, and this module refuses a response whose served model is not the one the
provider is pinned to. An address names a model; a payload produced by a
substitute must not be stored under it. That is the same reasoning as the refusal
rule, applied to a backend that has more ways to serve a different model than a
direct API call does.

What the address still does not cover
-------------------------------------

The version of the Claude Code binary the SDK spawns. It is part of what produced
the artefact and is not part of its identity, which is a real gap and is recorded
here rather than hidden: a corpus recorded through this backend should be
regarded as reproducible given a comparable binary, not given any binary.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from sciagent.core.errors import (
    ProviderError,
    ProviderUnavailableError,
    SciAgentError,
    SystemConfigurationError,
)
from sciagent.systems.llm.transcripts import Completion

if TYPE_CHECKING:  # pragma: no cover - import cost, not behaviour
    from collections.abc import AsyncIterator

    from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage

__all__ = ["DEFAULT_MODEL", "AgentSdkProvider", "QueryFn"]

#: The model proposals are recorded against. Deliberately the same default as the
#: Messages API backend: the point of a second door is a different credential, not
#: a different model, and a corpus that differed in both would confound them.
DEFAULT_MODEL = "claude-opus-5"

#: Environment variables that would change what a recording run produces, and why.
#:
#: The SDK builds the child's environment as ``{**os.environ, **options.env}``, so
#: a merge can add but never remove: every one of these is inherited from whatever
#: shell the run started in and none can be unset from here. Detecting them is the
#: only lever available, which is why this is a refusal rather than a correction.
#: See :meth:`AgentSdkProvider._refuse_a_contaminated_environment`.
CONTAMINATING_VARIABLES: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC_API_KEY", "would bill API credits instead of the subscription"),
    ("ANTHROPIC_AUTH_TOKEN", "would authenticate as a different account"),
    ("ANTHROPIC_BASE_URL", "would send the request somewhere else entirely"),
    ("CLAUDE_CODE_USE_BEDROCK", "would serve the model from Amazon Bedrock"),
    ("CLAUDE_CODE_USE_VERTEX", "would serve the model from Google Cloud"),
    ("CLAUDE_CODE_USE_FOUNDRY", "would serve the model from Microsoft Foundry"),
    ("CLAUDE_CODE_USE_ANTHROPIC_AWS", "would serve the model from AWS"),
    ("CLAUDE_CODE_SIMPLE", "makes every turn fail with no model call at all"),
    ("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "would change the artefact between machines"),
)

#: What :attr:`ModelUsage.provider` must say if it says anything. A response served
#: by another provider is not the artefact this backend claims to record, and the
#: field is the only evidence of it that survives into the response.
FIRST_PARTY = "firstParty"

#: Turn ceiling for one proposal. A real brief measured two turns consistently --
#: the answer, then the structured-output call -- so this is that with headroom.
#: See :meth:`AgentSdkProvider._options` for why it is not part of the address.
MAX_TURNS = 4

#: The shape of :func:`claude_agent_sdk.query`, so a test can supply one without
#: spawning a process. Lazily evaluated (PEP 695), so the annotations it names
#: need not exist at runtime.
type QueryFn = Callable[..., AsyncIterator[Message]]

#: The effort levels the SDK accepts. Restated here rather than imported so the
#: constructor's signature is checkable without the SDK installed -- and typed
#: rather than left as ``str``, unlike the Messages API backend, because here a
#: wrong value is rejected by a dataclass at call time rather than by the API.
type Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentSdkProvider:
    """A provider backed by the Claude Agent SDK, on a Claude subscription.

    Constructs nothing at import or ``__init__``: the SDK is imported on the
    first completion, exactly as the Messages API backend builds its client
    lazily, so a provider can be built, inspected and hashed into an address in
    an environment that has neither the SDK nor a credential. That is what lets
    a recorded transcript be replayed where the call could not have been made.
    """

    __slots__ = ("_effort", "_model", "_require_subscription", "_runner")

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        effort: Effort = "high",
        require_subscription: bool = True,
        runner: QueryFn | None = None,
    ) -> None:
        self._model = model
        self._effort = effort
        self._require_subscription = require_subscription
        self._runner = runner

    @property
    def id(self) -> str:
        """Return the backend identifier, which is part of every address.

        Distinct from the Messages API backend's ``"anthropic"``, which is what
        keeps the two corpora from resolving each other's calls.
        """
        return "claude-agent-sdk"

    @property
    def model(self) -> str:
        """Return the model identifier, which is part of every address."""
        return self._model

    @property
    def settings(self) -> str:
        """Return the request settings that could change the answer.

        ``effort``, and a marker when the environment guard is off.

        The marker is not bookkeeping. ``require_subscription=False`` disables
        :meth:`_refuse_a_contaminated_environment`, and that guard is the only
        thing standing between an inherited ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` --
        which this module's own table calls out as changing the artefact between
        machines -- and the model. An unguarded run is therefore a run whose
        answer has an input nothing else records, and it must not be able to
        share an address with a guarded one.

        Only present when the guard is off, so the ordinary case addresses as it
        would have anyway and no corpus is disturbed by this rule existing.
        """
        if self._require_subscription:
            return f"effort={self._effort}"
        return f"effort={self._effort};unguarded"

    def complete(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> Completion:
        """Return a payload conforming to ``schema``.

        Raises :class:`~sciagent.core.errors.ProviderError` for a refusal, a
        truncated response, a failed run, or a body that is not the JSON object
        the schema demands. Each carries a ``cause`` naming which of those it
        was; the class is what ``Hybrid`` catches and records, the cause is what
        a report can say about it.

        Raises :class:`~sciagent.core.errors.SystemConfigurationError` when the
        *machine* is at fault rather than the model: a contaminated environment,
        a call made from inside a running event loop, a turn served by another
        provider, a session whose provenance cannot be verified, or a response
        served by a model other than the one this provider is pinned to. Gate
        A44 moved these five out of ``ProviderError``, because a fault of the
        harness recorded as a proposal outcome is a scored datum about nothing.
        Like the class below it, this one propagates -- which callers holding
        billed artefacts must handle, since propagating past them loses the
        corpus; see the guard in ``scripts/rate_limit_pilot.py``.

        Raises :class:`~sciagent.core.errors.ProviderUnavailableError` when the
        session could not be reached or died under us -- a missing CLI, a dead
        child process, a stream that will not decode. That one propagates rather
        than being recorded; see the guard in :meth:`_call`.
        """
        self._refuse_a_contaminated_environment()
        result, manifest = self._call(system, brief, schema)
        self._refuse_a_substitute_model(result)
        return Completion(
            payload=_payload_of(result, self._model),
            provenance={
                # Which binary answered. Deliberately not in the address: Claude
                # Code auto-updates, and a corpus must not expire on somebody
                # else's release schedule.
                "claude_code_version": str(manifest.get("claude_code_version", "")),
                "served_models": ",".join(_served_models(result)),
            },
        )

    def _refuse_a_contaminated_environment(self) -> None:
        """Raise if an inherited variable would change what this run produces.

        The hermetic option set covers what is *sent*; it cannot cover what is
        *inherited*. The SDK builds the child's environment as
        ``{**os.environ, **options.env}``, and a merge adds but never removes, so
        none of :data:`CONTAMINATING_VARIABLES` can be unset from here. Detecting
        them is the only lever there is.

        The two kinds are refused together because their consequences converge.
        A credential redirects the billing or the account, which is the failure
        this backend was chosen to avoid. A behaviour variable changes the
        artefact itself -- ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` would have two
        machines record different answers under one address, and
        ``CLAUDE_CODE_SIMPLE`` makes every turn fail with no model call at all.
        Neither is something to guess at mid-run.

        Pass ``require_subscription=False`` to proceed anyway. The transcript is
        unaffected either way: the address covers the model, not the environment,
        which is exactly why the environment has to be checked here instead.
        """
        if not self._require_subscription:
            return
        found = [
            (name, why) for name, why in CONTAMINATING_VARIABLES if os.environ.get(name)
        ]
        if not found:
            return
        listed = "; ".join(f"{name} ({why})" for name, why in found)
        raise SystemConfigurationError(
            f"the environment carries {len(found)} variable(s) that would change "
            f"what this run produces: {listed}. They are inherited by the spawned "
            f"process and cannot be unset from here, so the call is refused rather "
            f"than recorded under an address that does not cover them. Unset them, "
            f"or construct the provider with require_subscription=False to proceed "
            f"deliberately"
        )

    def _options(self, system: str, schema: Mapping[str, Any]) -> ClaudeAgentOptions:
        """Return the hermetic option set for one call.

        Every field is load-bearing; see this module's docstring for what
        ``skills`` does and does not buy, and for why ``system_prompt`` is a bare
        string. Separate from :meth:`_call` so a test can read what would be sent
        without anything being sent.
        """
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            model=self._model,
            effort=self._effort,
            # A bare string replaces Claude Code's own system prompt. The preset
            # form would prepend it, and the brief would stop being the whole
            # input.
            system_prompt=system,
            output_format={"type": "json_schema", "schema": dict(schema)},
            # One structured answer, not an agentic loop. Also removes every
            # tool through which the surrounding filesystem could reach the
            # model.
            tools=[],
            # Measured, not guessed: a real brief takes two turns -- the model
            # answers, then emits the structured-output call -- consistently
            # across runs. `1` looked right, passed a trivial probe twice, and
            # failed a real proposal with "Reached maximum number of turns (1)".
            # Four leaves headroom without admitting an agentic loop; with
            # tools=[] there is nothing to loop over anyway.
            #
            # Deliberately *not* in `settings`. A turn ceiling either yields the
            # answer or raises, so it cannot produce a different one, and putting
            # it in the address would invalidate a corpus the next time the
            # headroom is adjusted -- the same reasoning that keeps the binary
            # version out.
            max_turns=MAX_TURNS,
            # No ~/.claude, no .claude/, no CLAUDE.md.
            setting_sources=[],
            # What the SDK documents for "no skills". Inert under this option
            # set -- no --skills flag is emitted either way; see the docstring.
            skills=[],
            mcp_servers={},
            strict_mcp_config=True,
        )

    def _call(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> tuple[ResultMessage, dict[str, Any]]:
        """Run one query to completion; return its result and session manifest."""
        try:
            from claude_agent_sdk import ResultMessage, query
        except ImportError as error:  # pragma: no cover - dependency present
            # ``ProviderUnavailableError``, matching the Messages API backend. A
            # missing dependency means the model was never reached, so it belongs
            # to the propagating class -- and it took the recorded class until
            # review caught the asymmetry, which would have had ``Hybrid`` score
            # a run as ``"refused"`` because a package was absent.
            raise ProviderUnavailableError(
                "the claude-agent-sdk package is not installed, so no call can "
                "be made; replaying a recorded transcript does not need it"
            ) from error

        from claude_agent_sdk import SystemMessage

        runner: QueryFn = query if self._runner is None else self._runner
        options = self._options(system, schema)

        async def drain() -> tuple[ResultMessage | None, dict[str, Any]]:
            """Consume the whole stream, keeping the result and the manifest.

            The ``init`` event is the session's manifest -- what it loaded, and
            which Claude Code built it. The version is the provenance worth
            keeping; the rest is what the hermeticity of this option set was
            verified against, and is how it would be checked again.
            """
            seen: ResultMessage | None = None
            manifest: dict[str, Any] = {}
            async for message in runner(prompt=brief, options=options):
                if isinstance(message, ResultMessage):
                    seen = message
                elif isinstance(message, SystemMessage) and message.subtype == "init":
                    manifest = dict(message.data)
            return seen, manifest

        # asyncio.run refuses to nest, and catching the RuntimeError it raises
        # would also catch one raised by the call itself. Ask first instead.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise SystemConfigurationError(
                "a completion was requested from inside a running event loop; "
                "this provider drives the async SDK with asyncio.run, which "
                "cannot nest. Call it from a worker thread, or drive the SDK "
                "directly"
            )

        try:
            result, manifest = asyncio.run(drain())
        except (SciAgentError, MemoryError):
            # Ours passes through untouched, and so does ``MemoryError``, which
            # is not ours but is equally not a statement about a model. This
            # machine is recorded dying at around 300 half-investigations in one
            # process and CLAUDE.md records ``-n auto`` failing three tests on
            # exactly this, so it is a case with a history here rather than a
            # hypothetical.
            raise
        except Exception as error:
            # Catching ``Exception`` is against this project's standing rule, and
            # it is done here on purpose, once, with the reason recorded.
            #
            # The rule forbids *suppressing* an error to make a check pass. This
            # suppresses nothing: the original is chained on ``__cause__``, its
            # type name is in the message, and the clause above guarantees no
            # framework error can enter here at all. What it does is translate a
            # foreign process's failures at the one seam where they arrive.
            #
            # It cannot be narrowed to ``ClaudeSDKError``, which is the tidy
            # answer and the one the Messages backend gets to use.
            # ``claude_agent_sdk`` raises bare ``Exception`` from seven sites in
            # ``_internal/query.py``, and one of them stopped item 15's V4/S11
            # cell with ``Exception: Claude Code returned an error result:
            # success`` -- recorded in ``docs/v1/DECISIONS.md`` (2026-08-18) as
            # transient on one observation, because nothing here could tell it
            # apart from one. A guard on the SDK root converts none of the seven.
            #
            # ``ProviderUnavailableError``, **not** ``ProviderError``, and that
            # class's docstring carries the reasoning. In short: ``Hybrid``
            # catches the latter and records ``"refused"``, so a dead session
            # would leave a completed, *scored*, permanently-ledgered replicate
            # that no transcript can reproduce. Propagating keeps the
            # checkpointing this guard was written for and drops the scoring.
            raise ProviderUnavailableError(
                f"the {self._model} session could not be completed: "
                f"{type(error).__name__}: {error}. Nothing was recorded, so this "
                f"replicate can be re-run once the cause has cleared"
            ) from error
        if result is None:
            raise ProviderError(
                f"the {self._model} session ended without a result message; "
                f"this is recorded as a transport refusal and replays as one",
                cause="transport",
            )
        self._refuse_a_failed_run(result)
        return result, manifest

    def _refuse_a_failed_run(self, result: ResultMessage) -> None:
        """Raise unless the session ended in a way that can carry a proposal.

        The refusal and ceiling wordings are the Messages API backend's,
        deliberately: the outcome is the same event and a caller comparing two
        recording runs should not have to learn two vocabularies for it.
        """
        if result.stop_reason == "refusal":
            raise ProviderError(
                f"{self._model} declined to answer. This is recorded as a "
                f"refusal and replays as one: a refusal is an outcome of the "
                f"investigation, not a call to be retried on a different model",
                cause="declined",
            )
        if result.stop_reason == "max_tokens":
            raise ProviderError(
                f"{self._model} hit its output ceiling before finishing; "
                f"thinking counts against the same allowance, so raise the "
                f"ceiling rather than lowering effort",
                cause="output_ceiling",
            )
        if result.is_error or result.subtype != "success":
            raise ProviderError(
                f"the {self._model} session ended as {result.subtype!r} "
                f"(terminal reason {result.terminal_reason!r}, api status "
                f"{result.api_error_status!r}): {result.errors!r}",
                cause="transport",
            )

    def _refuse_a_substitute_model(self, result: ResultMessage) -> None:
        """Raise unless the model this provider names is the one that answered.

        An address names a model, so a payload another model produced must not be
        stored under it. Absence of evidence is refused too: a result carrying no
        per-model usage does not say the right model answered, and recording it
        would put an unverified claim in the provenance chain.
        """
        foreign = _foreign_providers(result)
        if foreign:
            raise SystemConfigurationError(
                f"part of this turn was served by {foreign!r} rather than "
                f"{FIRST_PARTY!r}. That is a different account and a different "
                f"serving path from the subscription this backend records "
                f"against, and the address covers neither"
            )
        served = _served_models(result)
        if not served:
            raise SystemConfigurationError(
                f"the session reported no per-model usage, so there is nothing "
                f"to check {self._model!r} against. A transcript address names a "
                f"model and this response cannot show which one produced it"
            )
        if self._model not in served:
            raise SystemConfigurationError(
                f"the session was served by {served!r}, not by {self._model!r} "
                f"which this provider is pinned to. Nothing is recorded: an "
                f"address names a model, and a response another model produced "
                f"would be stored under a model that did not produce it"
            )


def _served_models(result: ResultMessage) -> tuple[str, ...]:
    """Return every model the result attributes usage to, sorted.

    ``canonicalModel`` **decides where it is present**, and the raw key is used
    only where it is absent. The two are not interchangeable evidence: the key is
    the model string the CLI was invoked with -- an echo of what the caller asked
    for -- while the canonical id is what pricing resolved it to. Accepting either
    would let a provider pinned to ``"opus"`` be satisfied by usage keyed
    ``"opus"`` whose canonical id was ``claude-sonnet-5``, which is exactly the
    substitution the check exists to catch.

    Sorted rather than a set, because the result is interpolated into an error
    message and set iteration order varies with ``PYTHONHASHSEED``.
    """
    usage = result.model_usage or {}
    names = {entry.get("canonicalModel") or key for key, entry in usage.items()}
    return tuple(sorted(names))


def _foreign_providers(result: ResultMessage) -> tuple[str, ...]:
    """Return any non-first-party API provider that served part of this turn.

    ``provider`` is ``NotRequired``, so absence is not evidence of anything and is
    ignored. Where it is present and disagrees, the response was served by
    Bedrock, Vertex or another platform -- a different account and a different
    serving path from the subscription this backend claims to record against.
    """
    usage = result.model_usage or {}
    served = {
        entry["provider"]
        for entry in usage.values()
        if entry.get("provider") and entry["provider"] != FIRST_PARTY
    }
    return tuple(sorted(served))


def _payload_of(result: ResultMessage, model: str) -> Mapping[str, Any]:
    """Return the JSON object a schema-constrained result carries.

    ``output_format`` makes the SDK validate the response against the schema and
    expose it already parsed, so this is a read rather than a parse. It still
    checks, because a guarantee that is never asserted is a guarantee nobody
    notices the loss of.
    """
    payload = result.structured_output
    if payload is None:
        raise ProviderError(
            f"{model} returned no structured output to read a proposal from, "
            f"though a JSON schema was set; the session said {result.result!r}",
            cause="malformed_response",
        )
    if not isinstance(payload, dict):
        raise ProviderError(
            f"{model} returned a {type(payload).__name__} where the schema "
            f"declares an object",
            cause="malformed_response",
        )
    return payload
