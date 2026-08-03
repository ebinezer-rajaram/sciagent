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


class ClampError(ProgramError):
    """An execution clamp does not denote an intervention on this programme.

    Raised for a clamped component the programme does not hold, an event index
    outside the run, or a non-finite forced value. A clamp is ``do(X = x)``, and
    every one of those is a statement about a variable or a time that does not
    exist -- which would otherwise be absorbed silently and produce a log that
    looks like an intervention and is not.
    """


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


# --------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------


class ExperimentError(SciAgentError):
    """Base for faults in an experiment design or its execution."""


class UnknownOperationError(ExperimentError):
    """An operation reached a compiler or an executor that cannot carry it out.

    Distinct from a malformed operation. SPEC §4.4's operation set is fixed, but
    an environment need not realise all of it, and one operation
    (``CompareCandidates``) is realised by backlog item 8 rather than by the
    executor. Refusing loudly is the point: an operation that silently measured
    something other than what it names would be indistinguishable from a result.
    """


class MalformedDesignError(ExperimentError):
    """An experiment design does not denote a single repeatable measurement.

    Raised for a non-positive run length, for an identifier carrying a character
    the design's canonical rendering reserves, and for an operation whose fields
    do not describe an act that could be performed.
    """


class ConditionError(SciAgentError):
    """A condition does not denote a set of real values.

    Raised for a NaN endpoint, an unknown comparison operator, or a declared
    diagnostic range that is empty. Satisfiability (SPEC §6.4 A16) is decided by
    interval arithmetic, which is only well posed on conditions that denote.
    """


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------


class InferenceError(SciAgentError):
    """Base for faults in the posterior engine and its supporting estimators."""


class OutOfRangeError(InferenceError):
    """A diagnostic value fell outside the range its metric declares.

    The declared range is what makes a discretisation total (SPEC §6.2 A6), so a
    value outside it means either the metric's declaration is wrong or the
    estimator returned something it should have raised on. Both are framework
    faults, and neither may be absorbed by clamping the value into the nearest
    bin: that would silently move probability mass between hypotheses.
    """


class DiscretisationError(InferenceError):
    """Bin edges do not denote a partition of a metric's declared range."""


class TableError(InferenceError):
    """The empirical table holds no row for a requested (hypothesis, template)."""


class UnknownExperimentError(InferenceError):
    """An experiment id was referenced that the engine has not recorded."""


# --------------------------------------------------------------------------
# Hypotheses
# --------------------------------------------------------------------------


class HypothesisError(SciAgentError):
    """Base for faults in the hypothesis graph and its validation."""


class UnfalsifiableHypothesisError(HypothesisError):
    """A hypothesis carries no prediction that any outcome could refute.

    Either it has no predictions at all, or a prediction's ``refutation`` is
    unsatisfiable over its diagnostic's declared range (SPEC §6.4 A16).
    """


class DuplicateHypothesisError(HypothesisError):
    """A structurally identical edit set is already in the graph (SPEC §6.4 A18).

    Raised for a duplicate of a *rejected* hypothesis too: re-proposing something
    already refuted is how a zombie hypothesis enters a graph, and SPEC §12 asks
    for zero of those.
    """


class PlausibilityWriteError(HypothesisError):
    """A stored ``plausibility`` is not the value the framework derives.

    ``plausibility`` is the structural prior, normalised from
    :meth:`~sciagent.core.edits.EditGrammar.code_length`. Nothing supplies it, so
    a disagreement means a number was planted by reaching past the constructor
    (SPEC §6.4 A17).
    """


class UnknownHypothesisError(HypothesisError):
    """A hypothesis id was referenced that the graph does not hold."""


# --------------------------------------------------------------------------
# Research systems
# --------------------------------------------------------------------------


class ResearchSystemError(SciAgentError):
    """Base for faults in a research system or in the diagnosis it returns.

    Not named ``SystemError``: that is a builtin, and shadowing it in a module
    every other module imports from would make ``except SystemError`` mean
    something different depending on the imports in scope.
    """


class DiagnosisError(ResearchSystemError):
    """A ``Diagnosis`` does not describe a distribution.

    Raised on construction rather than tolerated, because a diagnosis is what a
    scenario is scored on: an unnormalised one would be scored as though it were
    a belief and would silently move every figure computed from it.
    """


class InvestigationError(ResearchSystemError):
    """A system asked for something the investigation does not permit.

    Covers a design outside the scenario's set and a system that returned no
    conclusion at all. Distinct from
    :class:`BudgetExhaustedError`, which is a scenario running out of allowance
    and is an ordinary end to an investigation rather than a fault.
    """
