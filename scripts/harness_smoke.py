"""Live smoke test of the agent harness: one tiny Haiku investigation.

Proves the live path end to end on the logged-in Claude subscription: the SDK
session starts with only the layer's MCP tools, the model calls them, the run is
recorded, saved, reloaded and replayed offline. It also asks the model to try a
shell tool, to show that none is reachable.

    uv run python scripts/harness_smoke.py [--out DIR] [--model haiku]

Costs one short session (a handful of turns). Do not set ANTHROPIC_API_KEY: the
contamination guard refuses to run if it is set.
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

from sciagent.harness.errors import (
    HarnessFaultError,
    HermeticityError,
    ServedModelError,
    SessionFailedError,
)
from sciagent.harness.live import MODELS, AgentConfig, run_investigation
from sciagent.harness.record import SessionRecord
from sciagent.harness.replay import replay
from sciagent.harness.tools import ToolLayer, ToolOutput, ToolSpec

SYSTEM = (
    "You are a careful assistant operating inside a test harness. Use only the "
    "tools you are given. Be brief."
)
PROMPT = (
    "Do these steps in order. (1) If you have any tool that runs shell commands "
    "(such as Bash), use it to run `echo hi`; if you have none, say so in one "
    "sentence and list the names of the tools you do have. (2) Use the add tool "
    "to compute 17 + 25. (3) Use the add tool to add 8 to that result. (4) Call "
    "submit with the final number as the answer."
)


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def _add(args: Mapping[str, Any]) -> ToolOutput:
    return ToolOutput(f"{float(args['a']) + float(args['b']):g}")


def _submit(args: Mapping[str, Any]) -> ToolOutput:
    return ToolOutput("submitted; the task is over", record={"answer": args["answer"]})


def smoke_layer(config: Mapping[str, Any] | None = None) -> ToolLayer:
    """The two-tool layer; also the replay factory (it needs nothing from config)."""
    number = {"type": "number"}
    return ToolLayer(
        [
            ToolSpec(
                "add", "Add two numbers.", _schema({"a": number, "b": number}), _add
            ),
            ToolSpec(
                "submit",
                "Submit the final answer. Ends the task.",
                _schema({"answer": {"type": "string"}}),
                _submit,
                budget="submit",
                terminal=True,
            ),
        ],
        {"submit": 1},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--model", choices=sorted(MODELS), default="haiku")
    args = parser.parse_args()

    config = AgentConfig(
        model=MODELS[args.model],
        system_prompt=SYSTEM,
        prompt=PROMPT,
        seed=0,
        max_turns=8,
        wall_time_s=300.0,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        result = run_investigation(config, smoke_layer())
    except (
        HarnessFaultError,
        HermeticityError,
        ServedModelError,
        SessionFailedError,
    ) as error:
        # Keep what the aborted run recorded: it is the evidence of why.
        error.record.save(
            args.out / f"harness-smoke-aborted-{error.record.head[:16]}.json"
        )
        raise
    record = result.record

    print(f"outcome        : {result.outcome}")
    print(f"submission     : {result.submission}")
    print(f"served models  : {result.served_models}")
    print(f"num_turns      : {result.num_turns}")
    print(f"foreign tools  : {list(result.foreign_tool_uses)} (void={result.void})")
    session = record.of_kind("session")[0].body
    print(f"cli version    : {session.get('claude_code_version')}")
    print(f"manifest tools : {session.get('tools')}")
    print(f"mcp servers    : {session.get('mcp_servers')}")
    print(f"permissionMode : {session.get('permissionMode')}")
    print(f"skills/agents  : {session.get('skills')} / {session.get('agents')}")

    timing = record.timing
    elapsed = timing["entries"]
    print(f"wall time (s)  : {timing.get('wall_s')}")
    print(f"api time (ms)  : {timing.get('duration_api_ms')}")
    assistant_times = [
        elapsed[e.index] for e in record.entries if e.kind == "assistant"
    ]
    gaps = [b - a for a, b in pairwise(assistant_times)]
    print(f"assistant at s : {assistant_times}")
    print(f"turn gaps (s)  : {[round(g, 2) for g in gaps]}")

    for entry in record.entries:
        if entry.kind == "assistant":
            for block in entry.body["content"]:
                if block["type"] == "text":
                    print(f"  [text]     {block['text'][:300]!r}")
                elif block["type"] == "tool_use":
                    print(f"  [tool_use] {block['name']} {block['input']}")
        elif entry.kind in (
            "tool_call",
            "foreign_tool_result",
            "unmatched_tool_result",
        ):
            body = entry.body
            print(
                f"  [{entry.kind}] {body['name']} -> {body['text']!r} "
                f"err={body['is_error']} shown_matches={body.get('shown_matches')}"
            )

    path = args.out / f"harness-smoke-{record.head[:16]}.json"
    record.save(path)
    reloaded = SessionRecord.load(path)
    report = replay(reloaded, smoke_layer)
    print(f"saved          : {path}")
    print(f"head           : {record.head}")
    print(
        f"replay         : PASS steps={report.steps} "
        f"submission={report.submission} budgets={report.budgets_used}"
    )


if __name__ == "__main__":
    main()
