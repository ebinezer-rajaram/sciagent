"""Typed exception hierarchy.

Every error raised anywhere in ``sciagent`` derives from :class:`SciAgentError`.
No module raises a bare ``ValueError``/``KeyError``; callers can therefore
distinguish framework faults from interpreter faults by type alone.
"""

from __future__ import annotations


class SciAgentError(Exception):
    """Base of every error the framework raises deliberately."""


# --------------------------------------------------------------------------
# Programme construction and execution
# --------------------------------------------------------------------------


class ProgramError(SciAgentError):
    """A generative programme is malformed or cannot be executed."""


class UnknownComponentError(ProgramError):
    """An edge, edit or query referenced a component id not in the programme."""


class CyclicDependencyError(ProgramError):
    """The instantaneous edge set contains a cycle.

    History (lagged) edges are exempt: they connect distinct event indices and
    so cannot form a cycle in the time-unrolled graph.
    """


class ExecutionError(ProgramError):
    """Execution failed: unknown family, missing parameter, or bad arguments."""


class DeterminismError(ProgramError):
    """A determinism invariant was violated (e.g. non-finite draw, bad seed)."""


# --------------------------------------------------------------------------
# Grammar and edits
# --------------------------------------------------------------------------


class GrammarError(SciAgentError):
    """Base for faults in the edit grammar or in an edit applied through it."""


class EditNotInGrammarError(GrammarError):
    """The edit is well-formed but lies outside this grammar's licensed space.

    This is the mechanism behind the out-of-library definition in SPEC §3.2:
    ``ground_truth_edit in edit_grammar() \\ agent_grammar()``.
    """


class InvalidEditError(GrammarError):
    """The edit is not well-formed, or conflicts with another edit in the defect."""


class OffGridParameterError(GrammarError):
    """A parameter value is not a point of its declared quantisation grid.

    The prefix code (SPEC §6.1 A4) is only well posed over an enumerable space,
    so grammar-valid parameters must lie exactly on the declared grid.
    """


class UnknownParameterError(GrammarError):
    """An edit supplied a parameter name the target construct does not declare."""
