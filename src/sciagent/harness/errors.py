"""Typed errors of the agent harness.

Kept local to the package rather than added to :mod:`sciagent.core.errors`
because that module is shared and other agents are editing near it; every class
here still derives from :class:`~sciagent.core.errors.SciAgentError`, so a
caller can tell a harness fault from an interpreter fault by type alone.

Three of these carry the partial :class:`~sciagent.harness.record.SessionRecord`
of the run they aborted (``.record``). An aborted run is not scored, but what it
did is evidence about the harness, and an exception that dropped it would leave
nothing to read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sciagent.core.errors import (
    ProviderUnavailableError,
    SciAgentError,
    SystemConfigurationError,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle, not behaviour
    from sciagent.harness.record import SessionRecord

__all__ = [
    "HarnessError",
    "HarnessFaultError",
    "HermeticityError",
    "PredictionRefusedError",
    "RecordError",
    "RecordFormatError",
    "RecordIntegrityError",
    "RecordOverwriteError",
    "ReplayDivergenceError",
    "ServedModelError",
    "SessionFailedError",
    "ToolLayerError",
]


class HarnessError(SciAgentError):
    """Base for faults in the agent harness."""


class ToolLayerError(HarnessError):
    """A tool layer was configured inconsistently (a programming error).

    Never raised for anything an agent does: an agent's bad call is answered
    with an error *text*, because the agent is the one who must read it.
    """


class PredictionRefusedError(HarnessError):
    """A prediction, seal or evaluation broke the ledger's ordering rule.

    The ``predict`` tool turns this into an error text for the agent; the
    framework side (seal, evaluate) lets it propagate, since there it is a bug.
    """


class RecordError(HarnessError):
    """Base for faults in a session record."""


class RecordFormatError(RecordError):
    """A value cannot be written as canonical JSON (NaN, a non-JSON object)."""


class RecordIntegrityError(RecordError):
    """A loaded record's hash chain does not verify: it was edited or reordered."""


class RecordOverwriteError(RecordError):
    """A save would replace a different record; records are append-only."""


class ReplayDivergenceError(HarnessError):
    """A replayed tool call did not reproduce the recorded one.

    ``step`` is the tool-call index (0-based, in execution order) or ``-1`` for a
    check made before the first call or after the last; ``field`` names what
    differed (``"digest"``, ``"budgets_after"``, ``"submission"``, ...).
    """

    def __init__(self, message: str, *, step: int, field: str) -> None:
        super().__init__(message)
        self.step = step
        self.field = field


class _CarriesRecord:
    """Mixin: the partial record of the run the error aborted."""

    record: SessionRecord


class HarnessFaultError(HarnessError, _CarriesRecord):
    """A tool handler raised something other than a framework error.

    That is a bug in the tool, not an outcome of the investigation, so the run
    stops instead of the agent being shown a traceback and carrying on.
    """

    def __init__(self, message: str, *, record: SessionRecord) -> None:
        super().__init__(message)
        self.record = record


class ServedModelError(SystemConfigurationError, _CarriesRecord):
    """The session was served (in part) by a model or provider it was not pinned to."""

    def __init__(self, message: str, *, record: SessionRecord) -> None:
        super().__init__(message)
        self.record = record


class SessionFailedError(ProviderUnavailableError, _CarriesRecord):
    """The Claude Code session could not be completed (transport, dead child).

    A :class:`~sciagent.core.errors.ProviderUnavailableError`, so it propagates
    like the provider's: the replicate is re-run, never scored.
    """

    def __init__(self, message: str, *, record: SessionRecord) -> None:
        super().__init__(message)
        self.record = record


class HermeticityError(SystemConfigurationError, _CarriesRecord):
    """The session manifest shows a tool or context the harness did not provide.

    The option set is meant to leave the model exactly the MCP tools of the
    layer; a manifest listing anything else (a built-in tool, another MCP
    server) means that guarantee did not hold on this machine.
    """

    def __init__(self, message: str, *, record: SessionRecord) -> None:
        super().__init__(message)
        self.record = record
