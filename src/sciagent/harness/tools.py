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

An optional ``footer`` (P4 Δ12) builds one line from the budget meters *after*
the call has been charged; it is appended to every output -- a success, a
handler's error, a refusal by the layer -- before the output is logged and
returned, so the logged text is the text the agent read and replay digests it.

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

Closing. A worker thread cannot be cancelled, so a session cut at its wall time
can leave a handler running and more calls queued on the lock.
:meth:`ToolLayer.close` ends the log at once: it does not wait for the running
handler, whose output is then dropped uncharged, and a queued call runs nothing.
Neither is logged. Commits and :meth:`~ToolLayer.close` share a short gate, so
every call is either wholly in the log (and so in the session's record) or
not there at all. ``on_commit`` is called inside that gate with each logged
call, so what a caller keeps from it stops moving when the layer closes.

Framework time (P4b D5). A handler may spend time on the framework's behalf
rather than the agent's: policy mode's decision inside ``run_experiment`` fits,
simulates and scores the entertained set before any experiment runs. The
handler wraps that work in :meth:`FrameworkClock.measure` on the layer's
:attr:`ToolLayer.clock`; each logged call carries the time measured during it
(:attr:`ToolCall.framework_s`, 0.0 for a call that measured nothing), and the
live runner excludes it from the session's wall limit. It is a measurement, not
an output: it is in no digest and no hashed record entry, so it moves no
address and replay does not compare it.

This module imports nothing from the Agent SDK.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sciagent.core.errors import SciAgentError
from sciagent.harness.errors import ToolLayerError
from sciagent.harness.record import canonical_json, digest

__all__ = [
    "BudgetMeter",
    "Footer",
    "FrameworkClock",
    "Handler",
    "ToolCall",
    "ToolLayer",
    "ToolOutput",
    "ToolSpec",
]

#: Tool names become ``mcp__<server>__<name>``; keep them to what every layer of
#: that pipeline accepts.
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
#: The refusal after a terminal tool has succeeded, or after the layer closed.
_ENDED = "the investigation has ended; no further tools run"


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
#: Builds the line appended to every output from the meters after charging.
type Footer = Callable[[Mapping[str, "BudgetMeter"]], str]


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
    """One logged call, in execution order. ``framework_s`` is the time the
    handler spent inside :meth:`FrameworkClock.measure` (module docstring); it
    is a measurement, in no digest."""

    index: int
    name: str
    args: Mapping[str, Any]
    output: ToolOutput
    charged: str | None
    budgets_after: Mapping[str, int]
    framework_s: float = 0.0


class FrameworkClock:
    """Measures the time handlers spend on the framework's behalf (P4b D5).

    Thread-safe: a handler measures in a worker thread while the live runner
    reads :meth:`paused_s` and :attr:`measuring` from its event loop. A
    listener, if set, is called (from the measuring thread, under the clock's
    lock) each time a measurement ends, so the runner can re-arm its deadline;
    clearing it with ``None`` waits for a running notification to finish.
    Measurements do not nest.
    """

    __slots__ = ("_listener", "_lock", "_now", "_since", "_total")

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._since: float | None = None
        self._total = 0.0
        self._listener: Callable[[], None] | None = None

    @contextmanager
    def measure(self) -> Iterator[None]:
        """Count the time spent in the block as framework time."""
        with self._lock:
            if self._since is not None:
                raise ToolLayerError("framework time measurements do not nest")
            since = self._now()
            self._since = since
        try:
            yield
        finally:
            with self._lock:
                self._total += self._now() - since
                self._since = None
                if self._listener is not None:
                    self._listener()

    @property
    def measuring(self) -> bool:
        """Whether a measurement is running now."""
        with self._lock:
            return self._since is not None

    def total_s(self) -> float:
        """Framework time of every finished measurement."""
        with self._lock:
            return self._total

    def paused_s(self) -> float:
        """Framework time so far, a running measurement included."""
        with self._lock:
            running = 0.0 if self._since is None else self._now() - self._since
            return self._total + running

    def listen(self, listener: Callable[[], None] | None) -> None:
        """Set (or, with ``None``, clear) the end-of-measurement listener."""
        with self._lock:
            self._listener = listener


class ToolLayer:
    """Holds the tool specs and budgets; the only door to environment state."""

    __slots__ = (
        "_calls",
        "_clock",
        "_closed",
        "_finished",
        "_footer",
        "_gate",
        "_lock",
        "_meters",
        "_on_commit",
        "_specs",
        "_submission",
    )

    def __init__(
        self,
        specs: Sequence[ToolSpec],
        budgets: Mapping[str, int],
        *,
        footer: Footer | None = None,
        on_commit: Callable[[ToolCall], None] | None = None,
        clock: FrameworkClock | None = None,
    ) -> None:
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
        self._footer = footer
        self._on_commit = on_commit
        self._clock = FrameworkClock() if clock is None else clock
        self._closed = False
        #: Held for the whole of a call, handler included.
        self._lock = threading.Lock()
        #: Held only to commit or to close, so closing never waits for a handler.
        self._gate = threading.Lock()

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
    def clock(self) -> FrameworkClock:
        """The clock handlers measure framework time on (module docstring)."""
        return self._clock

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
        """Run one tool call through every check; log it; return its output.

        On a closed layer nothing runs and nothing is logged, and a call that
        was running when the layer closed is dropped (module docstring).
        """
        with self._lock:
            if self._closed:
                return _refusal(_ENDED)
            before = self._clock.total_s()
            output, charged, spec = self._dispatch(name, args)
            framework_s = self._clock.total_s() - before
            with self._gate:
                if self._closed:
                    return _refusal(_ENDED)
                return self._commit(name, args, output, charged, spec, framework_s)

    def close(self) -> None:
        """End the log now, without waiting for a running handler. Idempotent."""
        with self._gate:
            self._closed = True

    def idle(self, wait_s: float) -> bool:
        """Whether no handler is running, waiting up to ``wait_s`` for one to end.

        On a closed layer the answer stays true once it is true.
        """
        if not self._lock.acquire(timeout=wait_s):
            return False
        self._lock.release()
        return True

    def _commit(
        self,
        name: str,
        args: Mapping[str, Any],
        output: ToolOutput,
        charged: str | None,
        spec: ToolSpec | None,
        framework_s: float,
    ) -> ToolOutput:
        """Charge and log one call; return the output as logged."""
        if charged is not None:
            meter = self._meters[charged]
            self._meters[charged] = BudgetMeter(meter.name, meter.limit, meter.used + 1)
        if self._footer is not None:
            line = self._footer(dict(self._meters))
            output = ToolOutput(
                f"{output.text}\n{line}", output.is_error, output.record
            )
        logged = ToolCall(
            index=len(self._calls),
            name=name,
            args=dict(args),
            output=output,
            charged=charged,
            budgets_after={n: m.used for n, m in self._meters.items()},
            framework_s=framework_s,
        )
        self._calls.append(logged)
        if spec is not None and spec.terminal and not output.is_error:
            self._finished = True
            self._submission = logged
        if self._on_commit is not None:
            self._on_commit(logged)
        return output

    def _dispatch(
        self, name: str, args: Mapping[str, Any]
    ) -> tuple[ToolOutput, str | None, ToolSpec | None]:
        """Return (output, budget charged, spec) without mutating the log."""
        if self._finished:
            return _refusal(_ENDED), None, None
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
