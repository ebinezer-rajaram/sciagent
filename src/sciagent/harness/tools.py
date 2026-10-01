"""The tool layer: the agent's only path to environment state.

SPEC §4.0 and invariant 2 ("the framework writes numbers; agents write
structure"). An agent acts on the world only by calling tools, and every call
goes through :meth:`ToolLayer.call`, which

* resolves the tool by name, and answers an unknown name with an error text;
* checks the argument *shape* against the tool's schema (required keys present,
  no unknown keys when ``additionalProperties`` is false) -- value checks are the
  handler's job;
* enforces the tool's named budget **before** the handler runs: an exhausted
  budget means the handler is never called and the agent is told so in words;
* refuses every call after a terminal tool (``submit``) has succeeded;
* logs the call, its output, what it was charged to and the budget state after.

Charging rule. A metered call is charged iff its handler returns
``is_error=False``. A malformed request (bad rate, unknown model) costs nothing,
so an agent is never billed for the framework rejecting its input -- and a
refusal by the layer itself, which never reaches the handler, is never charged.

Handlers are plain synchronous callables. A handler that raises a
:class:`~sciagent.core.errors.SciAgentError` has its message returned to the
agent as an error text (uncharged): that is the framework declining a request.
Anything else propagates, because it is a bug in the tool, and the runner stops
the investigation rather than letting the model read a traceback.

The layer is serialised by a lock, so a runner that dispatches handlers to
worker threads still gets one well-defined call order, and that order -- the
``index`` of each :class:`ToolCall` -- is the order replay re-issues.

This module imports nothing from the Agent SDK.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sciagent.core.errors import SciAgentError
from sciagent.harness.errors import ToolLayerError
from sciagent.harness.record import canonical_json, digest

__all__ = [
    "BudgetMeter",
    "Handler",
    "ToolCall",
    "ToolLayer",
    "ToolOutput",
    "ToolSpec",
]

#: Tool names become ``mcp__<server>__<name>``; keep them to what every layer of
#: that pipeline accepts.
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass(frozen=True)
class ToolOutput:
    """What a tool returns: the text the agent reads, and optional structure.

    ``record`` is for the framework (an experiment's data handle, a fit's
    parameters): it is logged and digested with the text, never shown to the
    agent. It must be canonical-JSON-serialisable.
    """

    text: str
    is_error: bool = False
    record: Mapping[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "is_error": self.is_error,
            "record": None if self.record is None else dict(self.record),
        }

    @property
    def digest(self) -> str:
        """SHA-256 of the canonical JSON of :meth:`as_json`."""
        return digest(self.as_json())


type Handler = Callable[[Mapping[str, Any]], ToolOutput]


@dataclass(frozen=True)
class ToolSpec:
    """One tool: its name, the model-facing description and JSON schema, the
    handler, the budget it draws on (``None`` for unmetered) and whether a
    successful call ends the run."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Handler = field(repr=False, compare=False)
    budget: str | None = None
    terminal: bool = False

    def schema(self) -> dict[str, Any]:
        """The JSON-native description recorded in the config and sent to the model."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "budget": self.budget,
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class BudgetMeter:
    """A named budget: ``limit`` calls, ``used`` so far."""

    name: str
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


@dataclass(frozen=True)
class ToolCall:
    """One logged call, in execution order."""

    index: int
    name: str
    args: Mapping[str, Any]
    output: ToolOutput
    charged: str | None
    budgets_after: Mapping[str, int]


class ToolLayer:
    """Holds the tool specs and budgets; the only door to environment state."""

    __slots__ = ("_calls", "_finished", "_lock", "_meters", "_specs", "_submission")

    def __init__(self, specs: Sequence[ToolSpec], budgets: Mapping[str, int]) -> None:
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ToolLayerError(f"duplicate tool names in {names}")
        for spec in specs:
            if not _NAME.match(spec.name):
                raise ToolLayerError(
                    f"tool name {spec.name!r} must match {_NAME.pattern}"
                )
            if spec.budget is not None and spec.budget not in budgets:
                raise ToolLayerError(
                    f"tool {spec.name!r} draws on budget {spec.budget!r}, which "
                    f"is not among {sorted(budgets)}"
                )
            canonical_json(spec.schema())
        for name, limit in budgets.items():
            if limit < 0:
                raise ToolLayerError(f"budget {name!r} has negative limit {limit}")
        self._specs: dict[str, ToolSpec] = {s.name: s for s in specs}
        self._meters: dict[str, BudgetMeter] = {
            name: BudgetMeter(name, int(budgets[name])) for name in sorted(budgets)
        }
        self._calls: list[ToolCall] = []
        self._finished = False
        self._submission: ToolCall | None = None
        self._lock = threading.Lock()

    # -- introspection ------------------------------------------------------

    @property
    def names(self) -> list[str]:
        """Tool names, in the order the specs were given."""
        return list(self._specs)

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._specs.values()]

    def budgets(self) -> dict[str, int]:
        """Budget limits by name, sorted."""
        return {name: m.limit for name, m in self._meters.items()}

    def meter(self, name: str) -> BudgetMeter:
        return self._meters[name]

    @property
    def calls(self) -> tuple[ToolCall, ...]:
        return tuple(self._calls)

    @property
    def finished(self) -> bool:
        """True once a terminal tool has succeeded."""
        return self._finished

    @property
    def submission(self) -> ToolCall | None:
        """The successful terminal call, if any."""
        return self._submission

    # -- the one door -------------------------------------------------------

    def call(self, name: str, args: Mapping[str, Any]) -> ToolOutput:
        """Run one tool call through every check; log it; return its output."""
        with self._lock:
            output, charged, spec = self._dispatch(name, args)
            if charged is not None:
                meter = self._meters[charged]
                self._meters[charged] = BudgetMeter(
                    meter.name, meter.limit, meter.used + 1
                )
            logged = ToolCall(
                index=len(self._calls),
                name=name,
                args=dict(args),
                output=output,
                charged=charged,
                budgets_after={n: m.used for n, m in self._meters.items()},
            )
            self._calls.append(logged)
            if spec is not None and spec.terminal and not output.is_error:
                self._finished = True
                self._submission = logged
            return output

    def _dispatch(
        self, name: str, args: Mapping[str, Any]
    ) -> tuple[ToolOutput, str | None, ToolSpec | None]:
        """Return (output, budget charged, spec) without mutating the log."""
        if self._finished:
            return (
                _refusal("the investigation has ended; no further tools run"),
                None,
                None,
            )
        spec = self._specs.get(name)
        if spec is None:
            return (
                _refusal(f"unknown tool {name!r}; tools are {self.names}"),
                None,
                None,
            )
        problem = _shape_problem(spec.input_schema, args)
        if problem:
            return _refusal(f"{name}: {problem}"), None, spec
        if spec.budget is not None:
            meter = self._meters[spec.budget]
            if meter.exhausted:
                return (
                    _refusal(
                        f"budget exhausted: {name} draws on budget '{meter.name}', "
                        f"which allows {meter.limit} and all {meter.limit} have "
                        f"been used; {name} was not run"
                    ),
                    None,
                    spec,
                )
        try:
            output = spec.handler(args)
        except SciAgentError as error:
            return _refusal(f"{name}: {error}"), None, spec
        if not isinstance(output, ToolOutput):
            raise ToolLayerError(
                f"handler of {name!r} returned {type(output).__name__}, not ToolOutput"
            )
        canonical_json(output.as_json())
        charged = spec.budget if not output.is_error else None
        return output, charged, spec


def _refusal(text: str) -> ToolOutput:
    return ToolOutput(text, is_error=True)


def _shape_problem(schema: Mapping[str, Any], args: Mapping[str, Any]) -> str:
    """Return a description of what is wrong with ``args``'s keys, or ``""``."""
    if not isinstance(args, Mapping):
        return "arguments must be an object"
    required = list(schema.get("required", []))
    missing = [key for key in required if key not in args]
    if missing:
        return f"missing required argument(s) {missing}"
    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}))
        extra = sorted(key for key in args if key not in allowed)
        if extra:
            return f"unknown argument(s) {extra}; allowed are {sorted(allowed)}"
    return ""
