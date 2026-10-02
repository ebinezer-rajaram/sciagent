"""Typed errors of the scorer (SPEC §4.3, §4.4, §7.1)."""

from __future__ import annotations

from sciagent.core.errors import SciAgentError


class ScoringError(SciAgentError):
    """A score cannot be computed from what was given (a caller or data fault)."""
