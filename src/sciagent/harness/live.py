"""The live runner: one investigation as one Claude Agent SDK session.

SPEC §4 ("Harness"). An investigation is one ``ClaudeSDKClient`` session in one
asyncio loop. The tool layer's tools are exposed to the model as an in-process
MCP server (``create_sdk_mcp_server``), and nothing else is: every built-in
Claude Code tool is removed, and the session is told to deny anything not
pre-approved. The message stream is converted, as it arrives, into a
:class:`~sciagent.harness.record.SessionRecord`.

Hermeticity
-----------
The option set is :class:`~sciagent.systems.llm.agent_sdk_provider.AgentSdkProvider`'s,
extended for tools; read that module's docstring for why each field is there.
What changes here:

* ``tools=[]`` still removes every built-in tool; the layer's tools arrive as
  ``mcp__<server>__<name>`` and are the only names in ``allowed_tools``.
* ``permission_mode="dontAsk"``: a call to anything not pre-approved is denied
  without a prompt (there is nobody to answer one).
* ``ENABLE_TOOL_SEARCH=false`` in the child's environment, so the MCP tools are
  loaded into the model's context directly rather than deferred behind a
  ``ToolSearch`` tool the model would have to call first (and which ``tools=[]``
  removes anyway).
* ``MAX_MCP_OUTPUT_TOKENS`` and the ``anthropic/maxResultSizeChars`` tool hint
  are raised, so a large tool result (an event log) is shown to the model whole
  rather than truncated or spilled to a file the model has no tool to read.
  Every tool-call entry records whether what the model was shown equals what the
  layer returned (``shown_matches``), so a truncation cannot pass unnoticed.

The session manifest (the ``init`` message) is recorded and checked: if it
lists any tool that is not one of the layer's, the run is refused with
:class:`~sciagent.harness.errors.HermeticityError`. A tool use naming anything
else in the stream is recorded and voids the run (SPEC §9: "any hit voids the
run"), and every served model is checked against the pinned one as the
provider does.

Driving the loop without a model
--------------------------------
The loop consumes any :data:`SessionDriver`: a callable that takes the prompt,
the options and the MCP tool objects and yields SDK messages. :func:`sdk_driver`
is the real one. :class:`~sciagent.harness.scripted.ScriptedDriver` yields a
fixed sequence and calls the same ``SdkMcpTool`` handlers the in-process MCP
server would, so conversion, matching, recording, budgets and outcome rules are
exercised by tests without a model -- and a scripted agent is also how the
planted-hint positive control (SPEC §6.4) can be run.

Tool handlers run in a worker thread (``asyncio.to_thread``), so a sandbox call
that takes seconds does not stall the SDK's message pump; the layer's lock fixes
the call order.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    Message,
    ResultMessage,
    SdkMcpTool,
    ServerToolUseBlock,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
)
from claude_agent_sdk._cli_version import __cli_version__
from mcp.types import ToolAnnotations

from sciagent.core.errors import SciAgentError, SystemConfigurationError
from sciagent.harness.errors import (
    HarnessFaultError,
    HermeticityError,
    ServedModelError,
    SessionFailedError,
)
from sciagent.harness.record import SessionRecord, canonical_json
from sciagent.harness.tools import ToolCall, ToolLayer, ToolOutput
from sciagent.systems.llm.agent_sdk_provider import (
    CONTAMINATING_VARIABLES,
    _foreign_providers,
    _served_models,
)

__all__ = [
    "HARNESS_VERSION",
    "MODELS",
    "AgentConfig",
    "InvestigationResult",
    "SessionDriver",
    "arun_investigation",
    "build_options",
    "run_investigation",
    "sdk_driver",
]

#: Recorded in every config entry; bump when the record's meaning changes.
HARNESS_VERSION: Final = "harness/1"

#: The three models of SPEC §4.1, by tier letter's name.
MODELS: Final[Mapping[str, str]] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}

#: Large enough for a 2,000-event log as text; see the module docstring.
MAX_RESULT_CHARS: Final = 400_000
MAX_MCP_OUTPUT_TOKENS: Final = "200000"

type Effort = Literal["low", "medium", "high", "xhigh", "max"]

#: The awaitable bridge from an MCP handler to the tool layer.
type Dispatch = Callable[[str, Mapping[str, Any]], Awaitable[ToolOutput]]

#: Yields the session's messages. See the module docstring.
type SessionDriver = Callable[
    [str, ClaudeAgentOptions, Mapping[str, SdkMcpTool[Any]]], AsyncGenerator[Message]
]


@dataclass(frozen=True)
class AgentConfig:
    """Everything about a run that is not the tool layer.

    ``seed`` is not used by the harness: it is recorded so a replay factory can
    rebuild the same layer. ``max_turns`` is enforced by Claude Code;
    ``wall_time_s`` by this loop.
    """

    model: str
    system_prompt: str
    prompt: str
    seed: int
    max_turns: int = 60
    wall_time_s: float = 3600.0
    effort: Effort | None = None
    server_name: str = "lab"
    require_subscription: bool = True

    def as_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "system_prompt": self.system_prompt,
            "prompt": self.prompt,
            "seed": self.seed,
            "max_turns": self.max_turns,
            "wall_time_s": self.wall_time_s,
            "effort": self.effort,
            "server_name": self.server_name,
            "require_subscription": self.require_subscription,
        }


@dataclass(frozen=True)
class InvestigationResult:
    """A finished run. ``outcome`` is one of ``submitted``, ``max_turns``,
    ``wall_time``, ``refused``, ``output_ceiling``, ``session_error``,
    ``ended_without_submit``."""

    record: SessionRecord
    outcome: str
    submission: Mapping[str, Any] | None
    served_models: tuple[str, ...]
    foreign_tool_uses: tuple[str, ...]
    num_turns: int | None

    @property
    def void(self) -> bool:
        """True if the model reached for any tool the layer does not provide."""
        return bool(self.foreign_tool_uses)


def _undated(model: str) -> str:
    """``claude-haiku-4-5-20251001`` -> ``claude-haiku-4-5``.

    ``canonicalModel`` is the id *pricing* resolved the model to, and for a
    dated snapshot that is the undated family id (measured live: a session
    pinned to ``claude-haiku-4-5-20251001`` reports ``claude-haiku-4-5``). The
    per-message ``model`` field, checked first and exactly, carries the dated
    id, so accepting the undated form here does not admit a different model.
    """
    return re.sub(r"-\d{8}$", "", model)


def _full_name(server: str, name: str) -> str:
    return f"mcp__{server}__{name}"


def _env() -> dict[str, str]:
    return {
        "ENABLE_TOOL_SEARCH": "false",
        "MAX_MCP_OUTPUT_TOKENS": MAX_MCP_OUTPUT_TOKENS,
    }


def _default_dispatch(layer: ToolLayer) -> Dispatch:
    async def dispatch(name: str, args: Mapping[str, Any]) -> ToolOutput:
        return await asyncio.to_thread(layer.call, name, args)

    return dispatch


def build_options(
    config: AgentConfig, layer: ToolLayer, dispatch: Dispatch | None = None
) -> tuple[ClaudeAgentOptions, dict[str, SdkMcpTool[Any]]]:
    """Return the hermetic option set and the MCP tools, keyed by full name."""
    bridge = _default_dispatch(layer) if dispatch is None else dispatch
    annotations = ToolAnnotations.model_validate(
        {"maxResultSizeChars": MAX_RESULT_CHARS}
    )

    def make(name: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            output = await bridge(name, args)
            return {
                "content": [{"type": "text", "text": output.text}],
                "is_error": output.is_error,
            }

        return handler

    tools: dict[str, SdkMcpTool[Any]] = {}
    for schema in layer.schemas():
        name = schema["name"]
        tools[_full_name(config.server_name, name)] = SdkMcpTool(
            name=name,
            description=schema["description"],
            input_schema=schema["input_schema"],
            handler=make(name),
            annotations=annotations,
        )
    server = create_sdk_mcp_server(
        config.server_name, version=HARNESS_VERSION, tools=list(tools.values())
    )
    options = ClaudeAgentOptions(
        model=config.model,
        system_prompt=config.system_prompt,
        tools=[],
        allowed_tools=list(tools),
        permission_mode="dontAsk",
        mcp_servers={config.server_name: server},
        strict_mcp_config=True,
        setting_sources=[],
        skills=[],
        max_turns=config.max_turns,
        env=_env(),
    )
    if config.effort is not None:
        options.effort = config.effort
    return options, tools


async def sdk_driver(
    prompt: str, options: ClaudeAgentOptions, tools: Mapping[str, SdkMcpTool[Any]]
) -> AsyncGenerator[Message]:
    """The real driver: one ``ClaudeSDKClient`` session, one prompt."""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            yield message


def _refuse_contaminated_environment(config: AgentConfig) -> None:
    """Refuse an inherited variable that would change the run.

    The provider's guard (``AgentSdkProvider._refuse_a_contaminated_environment``)
    over the same table, restated because that one is a private method.
    """
    if not config.require_subscription:
        return
    found = [(n, why) for n, why in CONTAMINATING_VARIABLES if os.environ.get(n)]
    if found:
        listed = "; ".join(f"{n} ({why})" for n, why in found)
        raise SystemConfigurationError(
            f"the environment carries variable(s) that would change what this "
            f"run produces: {listed}. They are inherited by the spawned process "
            f"and cannot be unset from here; unset them, or set "
            f"require_subscription=False to proceed deliberately"
        )


def _block_json(block: Any) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.signature,
        }
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ServerToolUseBlock):
        return {
            "type": "server_tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    return {"type": type(block).__name__}


def _shown_text(content: str | list[dict[str, Any]] | None) -> str:
    """The text the model was shown for a tool result."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(
        str(item.get("text", "")) for item in content if item.get("type") == "text"
    )


#: Manifest keys worth keeping as evidence of what the session could see. ``cwd``
#: and session ids are machine- or run-specific and stay out of the hash.
_MANIFEST_KEYS: Final = (
    "agents",
    "apiKeySource",
    "claude_code_version",
    "mcp_servers",
    "model",
    "output_style",
    "permissionMode",
    "plugins",
    "skills",
    "slash_commands",
    "tools",
)


class _Session:
    """The state of one run's message loop."""

    def __init__(self, config: AgentConfig, layer: ToolLayer) -> None:
        self.config = config
        self.layer = layer
        self.record = SessionRecord()
        self.started = time.monotonic()
        self.names = {_full_name(config.server_name, n): n for n in layer.names}
        self.uses: dict[str, tuple[str, Any]] = {}
        self.recorded: set[int] = set()
        self.fault: BaseException | None = None
        self.foreign: list[str] = []
        self.assistant_models: list[str] = []
        self.result: ResultMessage | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def append(self, kind: str, body: Mapping[str, Any]) -> None:
        self.record.append(kind, body, elapsed_s=round(self.elapsed(), 6))

    async def dispatch(self, name: str, args: Mapping[str, Any]) -> ToolOutput:
        try:
            return await asyncio.to_thread(self.layer.call, name, args)
        except Exception as error:
            # Not suppressed: kept so the loop can stop the run with this as the
            # cause, then re-raised to whoever called the handler. Without the
            # note, the MCP server would turn a tool bug into an error text and
            # the model would carry on reading it as an outcome.
            self.fault = error
            raise

    # -- conversion ---------------------------------------------------------

    def on_system(self, message: SystemMessage) -> None:
        if message.subtype != "init":
            return
        data = message.data
        manifest: dict[str, Any] = {k: data[k] for k in _MANIFEST_KEYS if k in data}
        self.append("session", manifest)
        extra = sorted(set(manifest.get("tools", [])) - set(self.names))
        if extra:
            self.finish("hermeticity", {"unexpected_tools": extra})
            raise HermeticityError(
                f"the session offers the model tools the layer did not provide: "
                f"{extra}. The option set was meant to leave only "
                f"{sorted(self.names)}",
                record=self.record,
            )

    def on_assistant(self, message: AssistantMessage) -> None:
        self.assistant_models.append(message.model)
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                self.uses[block.id] = (block.name, block.input)
                if block.name not in self.names:
                    self.foreign.append(block.name)
            elif isinstance(block, ServerToolUseBlock):
                self.foreign.append(f"server:{block.name}")
        self.append(
            "assistant",
            {
                "model": message.model,
                "content": [_block_json(b) for b in message.content],
                "stop_reason": message.stop_reason,
                "message_id": message.message_id,
                "usage": message.usage,
                "error": message.error,
                "parent_tool_use_id": message.parent_tool_use_id,
            },
        )

    def on_user(self, message: UserMessage) -> None:
        if isinstance(message.content, str):
            return
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                self.on_tool_result(block)

    def on_tool_result(self, block: ToolResultBlock) -> None:
        name, args = self.uses.get(block.tool_use_id, ("", None))
        short = self.names.get(name)
        call = None if short is None else self.match(short, args)
        if call is None:
            self.append(
                "foreign_tool_result" if short is None else "unmatched_tool_result",
                {
                    "tool_use_id": block.tool_use_id,
                    "name": name,
                    "text": _shown_text(block.content),
                    "is_error": bool(block.is_error),
                },
            )
            return
        self.append_call(call, block.tool_use_id, _shown_text(block.content))

    def match(self, short: str, args: Any) -> ToolCall | None:
        """The earliest unrecorded layer call with this name and these args."""
        wanted = canonical_json(args)
        for call in self.layer.calls:
            if call.index in self.recorded or call.name != short:
                continue
            if canonical_json(dict(call.args)) == wanted:
                return call
        return None

    def append_call(
        self, call: ToolCall, tool_use_id: str | None, shown: str | None
    ) -> None:
        self.recorded.add(call.index)
        content = call.output.as_json()
        self.append(
            "tool_call",
            {
                "call": call.index,
                "tool_use_id": tool_use_id,
                "name": call.name,
                "args": dict(call.args),
                **content,
                "digest": call.output.digest,
                "charged": call.charged,
                "budgets_after": dict(call.budgets_after),
                "shown_matches": shown is not None and shown == call.output.text,
            },
        )

    def on_result(self, message: ResultMessage) -> None:
        self.result = message
        self.append(
            "result",
            {
                "subtype": message.subtype,
                "is_error": message.is_error,
                "num_turns": message.num_turns,
                "stop_reason": message.stop_reason,
                "terminal_reason": message.terminal_reason,
                "usage": message.usage,
                "model_usage": message.model_usage,
                "served_models": list(_served_models(message)),
                "errors": message.errors,
                "api_error_status": message.api_error_status,
            },
        )
        self.record.set_timing("duration_ms", message.duration_ms)
        self.record.set_timing("duration_api_ms", message.duration_api_ms)
        self.record.set_timing("total_cost_usd", message.total_cost_usd)

    # -- ending -------------------------------------------------------------

    def flush(self) -> None:
        """Record layer calls the stream never showed a result for, in order."""
        for call in self.layer.calls:
            if call.index not in self.recorded:
                self.append_call(call, None, None)

    def finish(self, reason: str, extra: Mapping[str, Any] | None = None) -> None:
        self.flush()
        submission = self.layer.submission
        self.append(
            "outcome",
            {
                "reason": reason,
                "submission": None if submission is None else dict(submission.args),
                "submit_call": None if submission is None else submission.index,
                "foreign_tool_uses": list(self.foreign),
                "tool_calls": len(self.layer.calls),
                **(extra or {}),
            },
        )
        self.record.set_timing("wall_s", round(self.elapsed(), 6))

    def classify(self) -> str:
        if self.layer.finished:
            return "submitted"
        result = self.result
        if result is None:
            return "session_error"
        if result.stop_reason == "refusal":
            return "refused"
        if result.subtype == "error_max_turns":
            return "max_turns"
        if result.stop_reason == "max_tokens":
            return "output_ceiling"
        if result.is_error or result.subtype != "success":
            return "session_error"
        return "ended_without_submit"

    def check_served_model(self) -> tuple[str, ...]:
        pinned = self.config.model
        others = sorted({m for m in self.assistant_models if m != pinned})
        if others:
            raise ServedModelError(
                f"assistant turns were produced by {others}, not by {pinned!r}",
                record=self.record,
            )
        if self.result is None:
            return ()
        foreign = _foreign_providers(self.result)
        if foreign:
            raise ServedModelError(
                f"part of this session was served by {foreign!r}, not first-party",
                record=self.record,
            )
        # The per-message check above is the strong one: every assistant turn
        # carried exactly the pinned id. Here it is enough that some served
        # canonical id names the pinned model's family. Measured live:
        # ``claude-sonnet-5-5`` reports ``canonicalModel`` ``claude-sonnet-5``,
        # and Claude Code adds a small auxiliary Haiku call that produces no
        # assistant turn; both are recorded in ``served_models``.
        served = _served_models(self.result)
        undated = _undated(pinned)
        if not any(
            canonical in (pinned, undated) or undated.startswith(canonical + "-")
            for canonical in served
        ):
            raise ServedModelError(
                f"the session was served by {served!r}, not by {pinned!r}",
                record=self.record,
            )
        return served

    # -- the loop -----------------------------------------------------------

    async def run(self, driver: SessionDriver) -> InvestigationResult:
        options, tools = build_options(self.config, self.layer, self.dispatch)
        self.append(
            "config",
            {
                **self.config.as_json(),
                "harness_version": HARNESS_VERSION,
                "tools": self.layer.schemas(),
                "budgets": self.layer.budgets(),
                "sdk_version": claude_agent_sdk.__version__,
                "bundled_cli_version": __cli_version__,
                "options": {
                    "tools": [],
                    "allowed_tools": sorted(tools),
                    "permission_mode": "dontAsk",
                    "setting_sources": [],
                    "skills": [],
                    "strict_mcp_config": True,
                    "env": _env(),
                    "max_result_chars": MAX_RESULT_CHARS,
                },
            },
        )
        reason: str | None = None
        try:
            async with asyncio.timeout(self.config.wall_time_s):
                async with contextlib.aclosing(
                    driver(self.config.prompt, options, tools)
                ) as stream:
                    async for message in stream:
                        if self.fault is not None:
                            break
                        self.consume(message)
        except TimeoutError:
            reason = "wall_time"
        except SciAgentError:
            raise
        except Exception as error:
            # The provider's translation seam, for the same reason (see
            # ``AgentSdkProvider._call``): the SDK raises bare ``Exception`` from
            # several sites, so nothing narrower converts them. Chained, not
            # suppressed. A tool bug noted by :meth:`dispatch` takes precedence,
            # since that is what actually stopped the session.
            if self.fault is not None:
                self.finish("harness_fault", {"error": type(self.fault).__name__})
                raise HarnessFaultError(
                    f"a tool handler raised {type(self.fault).__name__}: {self.fault}",
                    record=self.record,
                ) from self.fault
            self.finish("session_failed", {"error": type(error).__name__})
            raise SessionFailedError(
                f"the {self.config.model} session could not be completed: "
                f"{type(error).__name__}: {error}",
                record=self.record,
            ) from error
        if self.fault is not None:
            self.finish("harness_fault", {"error": type(self.fault).__name__})
            raise HarnessFaultError(
                f"a tool handler raised {type(self.fault).__name__}: {self.fault}",
                record=self.record,
            ) from self.fault
        if reason is None or self.layer.finished:
            reason = self.classify()
        self.finish(reason)
        served = self.check_served_model()
        submission = self.layer.submission
        return InvestigationResult(
            record=self.record,
            outcome=reason,
            submission=None if submission is None else dict(submission.args),
            served_models=served,
            foreign_tool_uses=tuple(self.foreign),
            num_turns=None if self.result is None else self.result.num_turns,
        )

    def consume(self, message: Message) -> None:
        if isinstance(message, SystemMessage):
            self.on_system(message)
        elif isinstance(message, AssistantMessage):
            self.on_assistant(message)
        elif isinstance(message, UserMessage):
            self.on_user(message)
        elif isinstance(message, ResultMessage):
            self.on_result(message)


async def arun_investigation(
    config: AgentConfig, layer: ToolLayer, *, driver: SessionDriver | None = None
) -> InvestigationResult:
    """Run one investigation in the current event loop."""
    _refuse_contaminated_environment(config)
    return await _Session(config, layer).run(sdk_driver if driver is None else driver)


def run_investigation(
    config: AgentConfig, layer: ToolLayer, *, driver: SessionDriver | None = None
) -> InvestigationResult:
    """Run one investigation in a fresh event loop (one loop per investigation)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise SystemConfigurationError(
            "run_investigation was called inside a running event loop; await "
            "arun_investigation instead"
        )
    return asyncio.run(arun_investigation(config, layer, driver=driver))
