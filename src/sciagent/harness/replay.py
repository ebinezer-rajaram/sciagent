"""Offline replay of a recorded investigation (instrument test 9).

The model is not replayed: its turns are in the record and are what they are.
What is replayed is everything the *framework* contributed. Given a record and a
factory that builds a fresh tool layer from the recorded config, :func:`replay`

1. checks the fresh layer's tool schemas and budgets equal the recorded ones,
   so a changed tool definition is caught before any call;
2. re-issues every recorded tool call, in recorded execution order (the layer's
   call index), with the recorded arguments;
3. compares each fresh result's digest with the recorded digest, and the
   recorded digest with the recorded content (so a record whose text was edited
   but whose digest was not is caught too);
4. compares what each call was charged to and the budget state after it;
5. compares the final submission (or its absence) with the recorded outcome.

Any difference raises :class:`~sciagent.harness.errors.ReplayDivergenceError`
naming the step and field. Byte-identical results everywhere means pass.

No import of the Agent SDK happens on this path -- this module, ``record`` and
``tools`` import none -- so a record replays where no model could be called.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sciagent.harness.errors import ReplayDivergenceError
from sciagent.harness.record import SessionRecord, canonical_json, digest
from sciagent.harness.tools import ToolLayer

__all__ = ["LayerFactory", "ReplayReport", "replay"]

#: Builds a fresh tool layer from a recorded ``config`` entry's body.
type LayerFactory = Callable[[Mapping[str, Any]], ToolLayer]


@dataclass(frozen=True)
class ReplayReport:
    """What a passing replay established."""

    head: str
    steps: int
    submission: Mapping[str, Any] | None
    budgets_used: Mapping[str, int]


def _diverge(step: int, field: str, recorded: Any, fresh: Any) -> ReplayDivergenceError:
    shown_r = canonical_json(recorded)[:200]
    shown_f = canonical_json(fresh)[:200]
    where = f"tool call {step}" if step >= 0 else "the run"
    return ReplayDivergenceError(
        f"replay diverged at {where}, field {field!r}: recorded {shown_r}, "
        f"replayed {shown_f}",
        step=step,
        field=field,
    )


def replay(record: SessionRecord, factory: LayerFactory) -> ReplayReport:
    """Re-issue every recorded call through a fresh layer; raise on any difference."""
    configs = record.of_kind("config")
    if len(configs) != 1 or record.entries[0].kind != "config":
        raise _diverge(-1, "config", "exactly one leading config entry", len(configs))
    config = configs[0].body
    layer = factory(config)

    if layer.schemas() != config["tools"]:
        raise _diverge(-1, "tools", config["tools"], layer.schemas())
    if layer.budgets() != config["budgets"]:
        raise _diverge(-1, "budgets", config["budgets"], layer.budgets())

    calls = sorted(
        (e.body for e in record.of_kind("tool_call")), key=lambda b: b["call"]
    )
    for expected, body in enumerate(calls):
        if body["call"] != expected:
            raise _diverge(expected, "call", expected, body["call"])
        recorded_content = {
            "text": body["text"],
            "is_error": body["is_error"],
            "record": body["record"],
        }
        if digest(recorded_content) != body["digest"]:
            raise _diverge(expected, "digest", body["digest"], digest(recorded_content))
        output = layer.call(body["name"], body["args"])
        if output.digest != body["digest"]:
            raise _diverge(expected, "digest", recorded_content, output.as_json())
        fresh = layer.calls[-1]
        if fresh.charged != body["charged"]:
            raise _diverge(expected, "charged", body["charged"], fresh.charged)
        if dict(fresh.budgets_after) != body["budgets_after"]:
            raise _diverge(
                expected, "budgets_after", body["budgets_after"], fresh.budgets_after
            )

    if len(layer.calls) != len(calls):  # pragma: no cover - the loop is 1:1
        raise _diverge(-1, "steps", len(calls), len(layer.calls))

    submission = None if layer.submission is None else dict(layer.submission.args)
    outcomes = record.of_kind("outcome")
    recorded_submission = outcomes[-1].body.get("submission") if outcomes else None
    if submission != recorded_submission:
        raise _diverge(-1, "submission", recorded_submission, submission)

    return ReplayReport(
        head=record.head,
        steps=len(calls),
        submission=submission,
        budgets_used={name: layer.meter(name).used for name in layer.budgets()},
    )
