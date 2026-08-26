"""Typed exception hierarchy.

Every error raised anywhere in ``sciagent`` derives from :class:`SciAgentError`.
No module raises a bare ``ValueError``/``KeyError``; callers can therefore
distinguish framework faults from interpreter faults by type alone.
"""

from __future__ import annotations

from typing import Any, Final


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
# External data snapshots
# --------------------------------------------------------------------------


class SnapshotError(SciAgentError):
    """Base for faults in an external, byte-frozen data snapshot."""


class SnapshotMismatchError(SnapshotError):
    """A snapshot's bytes do not hash to the digest declared for them.

    Raised rather than warned: a result computed from a catalogue that is not
    the one its address names is a row attributed to data that did not produce
    it, which is the found-data form of the confusion invariant 4 exists to
    prevent. There is no recovery inside the process -- the snapshot has to be
    re-fetched or the declaration corrected.
    """


class SnapshotMissingError(SnapshotError):
    """A declared snapshot is not present on disk.

    Separate from :class:`SnapshotMismatchError` because the remedies differ: a
    missing snapshot is fetched, a mismatched one is a question about which
    catalogue is the right one.
    """


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


class StructureNotMeasurableError(MetricError):
    """A structure compiles and executes, but a diagnostic cannot be estimated on it.

    Distinct from :class:`ExecutionError`, which says the programme itself could
    not be run. Here the programme runs and the *catalogue* cannot describe what
    came out: acceptance test A2 guarantees every grammar-valid edit compiles,
    and nothing guarantees the environment's own diagnostics have a value on the
    result.

    The slice's case is a genuine mismatch rather than a defect in either half. A
    run is a fixed number of *events*, while the window-based diagnostics are
    functions of its *duration*, so a structure with a very high effective rate
    -- ``base_rate=10.0`` under ``branching=0.95`` is some two hundred events per
    unit time -- packs the whole run into a span too short to hold two windows in
    every phase bin. The grammar expresses such structures; the catalogue cannot
    measure them.

    Raised rather than papered over because a system searching structures must be
    able to tell "this candidate is unmeasurable here" from "this candidate fits
    badly", and a silently dropped candidate is indistinguishable from one that
    was considered and rejected.
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


class DuplicateExperimentError(InferenceError):
    """One experiment id was recorded twice.

    An experiment id is a registry content address, so a second result under it
    would mean the address had failed to determine the outcome. Distinct from
    :class:`DuplicateHypothesisError`, which is about structure: these are two
    different faults and were once reported as one.
    """


class NoLiveHypothesisError(InferenceError):
    """Every hypothesis has been rejected, so no posterior is defined.

    Not a malformed distribution but an empty hypothesis space: there is nothing
    left for mass to be distributed over. Raised where the condition arises so
    that it is not met three frames later as a distribution that fails to
    normalise.
    """


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


class EngineTamperError(ResearchSystemError):
    """The posterior engine holds evidence the investigation never charged for.

    SPEC's second invariant made a runtime check rather than a docstring. The
    posterior is a pure function of what the engine has recorded, so a system
    that adds an observation writes its own belief and therefore its own score,
    and ``_audit`` cannot see it: that check re-derives the expected diagnosis
    from the same engine object the system was handed, so a poisoned state is
    compared against itself and agrees.

    Raised by :func:`~sciagent.eval.campaign.run_scenario` when the engine's
    observations disagree with the experiments the investigation ran. It is a
    second line rather than the first --
    :class:`~sciagent.inference.view.EngineView` withholds the recording surface
    from a system in the first place -- and it exists because a boundary with no
    check behind it is how the last one was believed for as long as it was.
    """


class SystemConfigurationError(ResearchSystemError):
    """A research system was built with arguments it cannot be run under.

    Covers a negative proposal allowance, a proposal allowance above
    :data:`~sciagent.systems.hybrid.MAX_PROPOSALS`, a system with no SPEC §5
    identifier, and a library naming a structure that does not exist. Those are
    faults in how the system was *constructed*, decided before any investigation
    begins and without reference to a model, a grammar or a draft.

    **Gate A44 widened it past construction time, and the name now covers more
    than it says.** Five conditions in
    :mod:`sciagent.systems.llm.agent_sdk_provider` raise it *mid-call*: a
    contaminated environment, a call from inside a running event loop, a turn
    served by a foreign provider, a session reporting no per-model usage, and a
    response served by a substitute model. The last three are emphatically
    *about* a model, so the clause above no longer describes the whole class.

    What unifies it is the consequence rather than the timing: **this class sits
    outside** :class:`ProposalError`, so ``Hybrid._propose_once`` cannot catch it
    and no fault reaching here can be recorded as a proposal outcome and scored.
    That is the property T1a needed and the reason these five were moved here
    rather than into a new sibling of their own.

    Split out from :class:`MalformedProposalError`, which these once shared.
    That class is for a draft that failed to decode into a licensed structure --
    a thing the model did -- so reporting a configuration mistake through it put
    the blame for a typo in a constructor on the proposal layer, and made
    ``except MalformedProposalError`` around a call to the model catch a fault
    that could only have happened before it.
    """


def _rebuild_provider_error(
    cls: type[ProviderError], message: str, cause: str
) -> ProviderError:
    """Reconstruct a :class:`ProviderError` from its pickled parts."""
    return cls(message, cause=cause)


class ProposalError(ResearchSystemError):
    """Base for faults in the LLM proposal layer (SPEC §11 item 12)."""


class MalformedProposalError(ProposalError):
    """A proposal draft does not denote a structure the grammar licenses.

    Raised for a menu index outside the declared structural menu, a parameter
    count that does not match the option's grids, a grid index outside its grid,
    and an empty edit set. Every one of these is a *decoding* failure rather than
    a scientific judgement: the draft never became a defect, so there is nothing
    to entertain and nothing to score.

    Distinct from
    :class:`~sciagent.core.errors.EditNotInGrammarError`, which is a well-formed
    structure outside the licensed space -- that is SPEC §3.2's out-of-library
    condition and a finding, not a fault.

    Carries the fixed cause ``"undecodable"``. Fixed rather than passed, because
    every way this class is raised is the same diagnostic fact -- the draft never
    became a defect -- so a per-site tag would be a parameter with one value.
    :class:`ProviderError`'s is passed, because its raise sites mean genuinely
    different things.
    """

    cause: Final = "undecodable"


class TranscriptMissError(ProposalError):
    """A replay-only transcript store holds no response for a content address.

    Bit-exact determinism (SPEC §1 invariant 3) is what makes this an error and
    not a cache miss to be filled. A model call cannot be reproduced -- the
    sampling parameters that would pin it are rejected by the models in question
    -- so a recorded response *is* the reproducible artefact. Silently calling
    out on a miss would make the run depend on when it happened.
    """


class TranscriptSchemeError(ProposalError):
    """A transcript corpus was recorded under a different address scheme.

    Narrower than :class:`ProposalError` on purpose, and the narrowness is what
    it is *for*. ``TranscriptStore._refuse_to_drop`` treats exactly one
    read failure as safe to overwrite -- a corpus addressed under an older
    scheme, every call in which would miss anyway, so refusing there would
    strand it forever. **Every other failure to read must propagate**, because a
    corpus that is present but malformed may still hold recoverable calls and
    quietly treating it as "drops nothing" truncates the one file nobody can
    reconstruct.

    That distinction was expressed as ``except ProposalError`` while this class
    was the only thing ``load`` raised. Gate A35 added a second: a record kind
    the reading process does not implement. Under the broad clause a corpus
    holding one silently lost **every** call in the file, including well-formed
    ones -- reproduced before this class existed. A subclass rather than a
    sibling, so callers asserting the documented ``ProposalError`` are unchanged.
    """


class ProviderUnavailableError(ProposalError):
    """A model backend could not be reached, or the session died under it.

    A rate limit, a 5xx, a dropped connection, an expired credential, a Claude
    Code process that exited. Deliberately **not** a
    :class:`ProviderError`, and the distinction is the whole point of the class.

    A ``ProviderError`` is a scientific event: the model was asked and produced
    nothing, which is a fact about the investigation and is recorded as one.
    ``Hybrid._propose_once`` catches it, the run completes, and a reading is
    written to the ledger. **None of that is true of a 429.** A transport
    failure says nothing about the model's ability to propose, so a run scored
    after one reports a degraded result as though the system had earned it --
    permanently, since the ledger is append-only, and unreproducibly, since a
    call that failed leaves no transcript to replay.

    So this one propagates. It is still a :class:`~sciagent.core.errors.SciAgentError`,
    which is what makes the difference from letting the SDK's own exception
    escape: ``run_matrix`` catches it, reports "stopped after N replicate(s)",
    and every completed replicate is already checkpointed. The campaign stops
    cleanly and resumes into the same cell once the cause has cleared.

    Same standing as :class:`TranscriptMissError` and for the same reason --
    a fault of the harness or its surroundings is not a finding, and swallowing
    one turns a broken run into a quietly worse result.
    """


class ProviderError(ProposalError):
    """A model provider could not produce a draft at all.

    A refusal, an output ceiling reached, or a response carrying no tool call:
    the model was reached, answered, and the answer holds no proposal. That is a
    legitimate outcome for a research system to have and for the harness to
    record, which is why ``Hybrid`` catches this one and scores the run.

    Deliberately distinct from :class:`MalformedProposalError`, which means
    something *was* proposed and did not denote — and, since 2026-08-18, from
    :class:`ProviderUnavailableError`, which means the model was never reached
    at all. This docstring read "a refusal, **a transport failure**, or a
    response carrying no tool call" until then, and that middle clause was the
    defect rather than the contract: a 429 is not an outcome of an
    investigation, and recording one as though it were is what the split fixes.

    Every raise site states a ``cause``, and the argument is required rather than
    defaulted. Gate A44 keeps :class:`~sciagent.eval.agency.ProposalRecord`'s
    five scoring fields frozen and carries the diagnostic question in a parallel
    breakdown beside them (``docs/DECISIONS.md``, 2026-08-21, **T3**), so the tag
    is what tells a model that declined apart from a session that died -- both of
    which are recorded as ``"refused"`` and must stay that way, since moving
    either would move ``yield_fraction``'s denominator.

    A default would defeat the whole of it: un-tagged raise sites would pool into
    one bin that reads as a measurement of nothing, which is the conflation this
    class was split for, one level further down.
    :data:`sciagent.eval.agency.PROPOSAL_CAUSES` is the vocabulary and
    :func:`~sciagent.eval.agency.proposal_causes` refuses anything outside it.
    The tag is a plain string here rather than that tuple's member type because
    ``core`` may not import from ``eval``, which is the same shape as ``outcome``
    on :class:`~sciagent.systems.hybrid.ProposalAttempt`.
    """

    def __init__(self, message: str, *, cause: str) -> None:
        super().__init__(message)
        self.cause = cause

    def __reduce__(self) -> tuple[Any, ...]:
        """Return a picklable reconstruction, ``cause`` included.

        The default for an exception rebuilds it from ``args``, which holds the
        message alone -- so without this, ``pickle`` and ``copy`` of any
        instance raise ``TypeError`` for the missing keyword. Nothing pickles
        exceptions today; the guard is here because the failure would surface as
        an unrelated ``TypeError`` in whatever first did, and the cost of not
        needing to diagnose that is three lines.
        """
        return (_rebuild_provider_error, (type(self), str(self), self.cause))


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


class VerificationError(SciAgentError):
    """Base for faults in the claim verifier (SPEC §6.5).

    Raised only for claims the verifier cannot *adjudicate at all* because they
    do not denote. A claim that denotes and is wrong is not an error: it gets a
    :class:`~sciagent.verify.verdict.Verdict` recording why, because a refused
    claim is a datum about the system that made it and must survive into the
    record rather than escaping as an exception.
    """


class MalformedClaimError(VerificationError):
    """A claim's fields do not describe a claim any evidence could bear on.

    Raised for a causal claim carrying no estimand, a claim citing an experiment
    the evidence index does not hold, and a scope whose parameter range is empty.
    Distinct from a claim that is merely unsupported, which is a verdict.
    """


class EstimandError(VerificationError):
    """An estimand does not denote an effect on the programme's DAG.

    Raised for a path naming a component the programme lacks, a path whose
    consecutive pairs are not edges, and an estimand whose target equals its
    outcome. SPEC §7.2's licensing rules quantify over paths, so an estimand that
    does not denote one would be licensed or refused arbitrarily.
    """
