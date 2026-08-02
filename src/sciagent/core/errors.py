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


# --------------------------------------------------------------------------
# Registry, partitions and budget
# --------------------------------------------------------------------------


class RegistryError(SciAgentError):
    """Base for faults in the experiment registry."""


class AppendOnlyViolationError(RegistryError):
    """A statement attempted to update, delete or restructure registered rows.

    The registry is append-only by specification (SPEC §6.3 A12), so this is
    raised rather than silently refused, including when the attempt reaches past
    the store's API to raw SQL.
    """


class RegistryConflictError(RegistryError):
    """One content address was offered two different results.

    Since the address covers (env version, config, data version, metric version,
    seed), two disagreeing results mean the experiment is not reproducible, or
    that something outside the address influenced it. Either is a framework bug.
    """


class UnknownRecordError(RegistryError):
    """A digest was queried that the registry does not hold."""


class PartitionAccessError(RegistryError):
    """Sealed partition data was requested through an unprivileged path.

    HOLDOUT and TEST are reachable only by presenting a
    :class:`~sciagent.registry.partitions.SealedAccess` token (SPEC §6.3 A14).
    """


class MetricError(SciAgentError):
    """Base for faults in the versioned metric registry."""


class UnknownMetricError(MetricError):
    """A metric name was requested that the registry does not declare."""


class DuplicateMetricError(MetricError):
    """A metric name was registered twice under different definitions.

    The metric registry is versioned and append-only: redefining a name in place
    would silently change the meaning of every result already recorded under it.
    """


class BudgetError(SciAgentError):
    """Base for faults in budget accounting."""


class BudgetExhaustedError(BudgetError):
    """A charge exceeded the remaining budget."""
