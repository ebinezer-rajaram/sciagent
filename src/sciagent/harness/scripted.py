"""A scripted model: drives the live runner's loop with a fixed sequence of turns.

It yields the Agent SDK's own message types in the order a Claude Code session
does (``init``; then per turn an assistant message, and if it used tools a user
message carrying their results; finally a result message), and it executes tool
uses by awaiting the very ``SdkMcpTool`` handlers that ``create_sdk_mcp_server``
would call. What it skips is the CLI and the MCP JSON transport -- nothing of
the harness's own code.

Uses: tests of the loop and of replay (instrument test 9), and scripted agents
such as the planted-hint positive control (SPEC §6.4).

Deterministic by construction: tool-use ids are ``toolu_<turn>_<k>``, there are
no timestamps, and usage numbers are fixed, so two runs of one script over two
fresh, equal layers record the same head.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    SdkMcpTool,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import ContentBlock, ModelUsage

__all__ = ["ScriptedDriver", "Step"]


@dataclass(frozen=True)
class Step:
    """One assistant turn. ``calls`` are ``(tool name, args)``; names are the
    layer's short names unless ``raw_names`` (to emulate a built-in tool such as
    ``Bash``). ``delay_s`` is slept before the turn is emitted."""

    text: str | None = None
    thinking: str | None = None
    calls: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    raw_names: bool = False
    delay_s: float = 0.0


class ScriptedDriver:
    """A :data:`~sciagent.harness.live.SessionDriver` that plays a script.

    A turn with no tool calls ends the session, as it does for a real model.
    Turns beyond ``options.max_turns`` end it with ``error_max_turns``, as
    Claude Code does. ``served_model`` (the per-message model) defaults to
    ``options.model``; ``canonical_model`` (the result's pricing id) to that.
    """

    def __init__(
        self,
        script: Sequence[Step],
        *,
        served_model: str | None = None,
        stop_reason: str = "end_turn",
        canonical_model: str | None = None,
    ) -> None:
        self.script = tuple(script)
        self.served_model = served_model
        self.stop_reason = stop_reason
        self.canonical_model = canonical_model
        self.turns = 0

    def __call__(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        tools: Mapping[str, SdkMcpTool[Any]],
    ) -> AsyncGenerator[Message]:
        return self._play(options, tools)

    async def _play(
        self, options: ClaudeAgentOptions, tools: Mapping[str, SdkMcpTool[Any]]
    ) -> AsyncGenerator[Message]:
        model = self.served_model or options.model or ""
        servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
        server = next(iter(servers), "")
        yield SystemMessage(
            subtype="init",
            data={
                "tools": sorted(tools),
                "model": model,
                "claude_code_version": "scripted",
                "mcp_servers": [{"name": server, "status": "connected"}],
                "permissionMode": options.permission_mode,
            },
        )
        stop = self.stop_reason
        last_text = ""
        for step in self.script:
            if options.max_turns is not None and self.turns >= options.max_turns:
                yield self._result(model, "error_max_turns", None, "")
                return
            self.turns += 1
            if step.delay_s:
                await asyncio.sleep(step.delay_s)
            content: list[ContentBlock] = []
            if step.thinking is not None:
                content.append(
                    ThinkingBlock(thinking=step.thinking, signature="scripted")
                )
            if step.text is not None:
                content.append(TextBlock(text=step.text))
                last_text = step.text
            uses: list[ToolUseBlock] = []
            for k, (name, args) in enumerate(step.calls):
                full = name if step.raw_names else f"mcp__{server}__{name}"
                uses.append(
                    ToolUseBlock(
                        id=f"toolu_{self.turns:03d}_{k}", name=full, input=dict(args)
                    )
                )
            content.extend(uses)
            yield AssistantMessage(
                content=content,
                model=model,
                stop_reason="tool_use" if uses else stop,
                usage={"input_tokens": 10, "output_tokens": 5},
                message_id=f"msg_{self.turns:03d}",
            )
            if not uses:
                yield self._result(model, "success", stop, last_text)
                return
            results: list[ContentBlock] = []
            for use in uses:
                spec = tools.get(use.name)
                if spec is None:
                    results.append(
                        ToolResultBlock(
                            tool_use_id=use.id,
                            content=f"<tool_use_error>Error: No such tool available: "
                            f"{use.name}</tool_use_error>",
                            is_error=True,
                        )
                    )
                    continue
                reply = await spec.handler(dict(use.input))
                results.append(
                    ToolResultBlock(
                        tool_use_id=use.id,
                        content=list(reply["content"]),
                        is_error=bool(reply.get("is_error", False)),
                    )
                )
            yield UserMessage(content=results)
        yield self._result(model, "success", stop, last_text)

    def _result(
        self, model: str, subtype: str, stop: str | None, text: str
    ) -> ResultMessage:
        usage: ModelUsage = {
            "inputTokens": 10 * self.turns,
            "outputTokens": 5 * self.turns,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "webSearchRequests": 0,
            "costUSD": 0.0,
            "contextWindow": 200000,
            "maxOutputTokens": 32000,
            "canonicalModel": self.canonical_model or model,
        }
        return ResultMessage(
            subtype=subtype,
            duration_ms=0,
            duration_api_ms=0,
            is_error=subtype != "success",
            num_turns=self.turns,
            session_id="scripted",
            stop_reason=stop,
            usage={"input_tokens": 10 * self.turns, "output_tokens": 5 * self.turns},
            result=text,
            model_usage={model: usage},
        )
