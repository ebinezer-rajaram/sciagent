"""Typed errors of the AG-o Python sandbox.

Every one is a fault of the *harness* (Docker missing, a path that cannot be
mounted, a dataset written twice with different bytes), never of the agent's
code. What the agent's code does -- raise, exit non-zero, run out of time or
memory -- is a :class:`~sciagent.sandbox.runner.SandboxResult`, recorded and
replayed like any other result, and is never raised here.
"""

from __future__ import annotations

from sciagent.core.errors import SciAgentError

__all__ = [
    "DatasetConflictError",
    "SandboxConfigError",
    "SandboxError",
    "SandboxImageError",
    "SandboxPathError",
    "SandboxUnavailableError",
]


class SandboxError(SciAgentError):
    """Base for faults in the sandbox harness."""


class SandboxUnavailableError(SandboxError):
    """Docker cannot be reached: the binary is missing or the daemon is down.

    Propagates rather than becoming a tool result, for the reason
    :class:`~sciagent.core.errors.ProviderUnavailableError` does: a sandbox
    that never ran says nothing about the agent, and recording it as an
    outcome would score a broken harness as a finding.
    """


class SandboxImageError(SandboxError):
    """The sandbox image could not be built, or its build context is missing."""


class SandboxConfigError(SandboxError):
    """Limits, a seed, a run directory or a dataset that cannot be used as given."""


class SandboxPathError(SandboxError):
    """A path or name the sandbox cannot represent safely.

    Raised for a host path Docker's ``--mount`` syntax cannot carry (a comma,
    a quote or a control character) and for a dataset or channel name outside
    ``[A-Za-z0-9_]``, which could otherwise name a file outside ``data/``.
    """


class DatasetConflictError(SandboxError):
    """A dataset name was written twice with different contents.

    ``data/`` is what the agent has collected; rewriting an entry in place
    would change, under a transcript that already read it, what an earlier
    ``python`` call saw, and replay would then re-execute against data the
    original call never had.
    """
