"""The posterior engine protocol and the value types it returns (SPEC §3.5).

Two implementations are planned: :class:`~sciagent.inference.empirical
.EmpiricalTableEngine`, which is the slice's, and a likelihood-free engine later.
SPEC F12 stages them deliberately, and §6.2 requires each to pass A6-A11 on its
own before any agent result depends on it. The agent never learns which is in
use, so everything an investigation can observe about an engine is here.

Units
-----

``log_likelihood`` and everything derived from it are in **nats**, the
conventional reading of "log-likelihood". Entropies and code lengths are in
**bits**, matching the prefix code that defines the prior. The conversion is a
single factor of ``ln 2`` and appears in exactly one place, where the engine
turns a code length into a prior log-weight.

Simulation is injected
----------------------

An engine reaches its environment only through a :data:`Simulator`, in the same
way that a programme reaches its family semantics through a
:class:`~sciagent.core.program.FamilyLibrary` and a metric reaches its estimator
through :class:`~sciagent.registry.metrics.MetricSpec`. That is what lets the
engine be domain-independent while still being able to simulate a defect.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from sciagent.core.edits import Defect
from sciagent.core.errors import InferenceError
from sciagent.core.types import (
    ExperimentId,
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    Probability,
    Seed,
)
from sciagent.hypothesis.graph import HypothesisNode
from sciagent.inference.binning import DiagnosticVector, OutcomeSpace

__all__ = [
    "DiagnosticVector",
    "ExpansionCost",
    "ExperimentTemplate",
    "LikelihoodEstimate",
    "Observation",
    "PPCResult",
    "PosteriorEngine",
    "Simulator",
]


@dataclass(frozen=True, slots=True)
class ExperimentTemplate:
    """A repeatable experimental design, identified by what it measures.

    The engine needs exactly two things from a template: a stable identity, so a
    table row can be addressed by it, and the finite outcome space its result
    falls in. *How* it is carried out -- which parameters are perturbed, which
    component is ablated -- is
    :class:`~sciagent.experiments.dsl.ExperimentDesign`'s business, and a
    template is what :meth:`~sciagent.experiments.dsl.ExperimentDesign.template`
    projects a design down to. Keeping the projection one-way is the point: the
    engine cannot come to condition on what was *done*, only on what was
    measured and how finely.
    """

    id: ExperimentTemplateId
    outcome: OutcomeSpace
    n_events: int
    """Events per execution. Part of the design: a diagnostic's sampling
    distribution depends on how much data it saw, so two run lengths are two
    templates."""

    def __post_init__(self) -> None:
        if self.n_events <= 0:
            raise InferenceError(
                f"template {self.id!r} declares n_events={self.n_events}; a "
                f"design must run for a positive number of events"
            )


#: Executes one experiment: applies ``defect`` to the environment's reference
#: programme, runs it under ``seed``, and returns the template's diagnostics.
#:
#: The seed is passed rather than drawn, so the caller owns reproducibility. An
#: implementation may cache executions across templates that share a seed --
#: several diagnostics can be read off one run -- and the engine's accounting is
#: written in terms of *calls to this callable*, not executions, precisely so
#: that such caching cannot make the reported cost wrong.
type Simulator = Callable[[Defect, ExperimentTemplate, Seed], DiagnosticVector]


@dataclass(frozen=True, slots=True)
class LikelihoodEstimate:
    """One estimated log-likelihood and its Monte Carlo standard error.

    ``log_likelihood`` is in nats. ``standard_error`` is the standard error *of
    that estimate*, so A7's coverage statement -- the exact value falls within
    two standard errors at least 93% of the time -- is a statement about this
    pair and nothing else.
    """

    log_likelihood: float
    standard_error: float
    cell: int
    """Index of the outcome cell the observation fell in."""

    count: int
    """Replicates that landed in that cell."""

    replicates: int

    def as_tuple(self) -> tuple[float, float]:
        """Return ``(log-likelihood, standard error)``, SPEC §3.5's return type."""
        return (self.log_likelihood, self.standard_error)


@dataclass(frozen=True, slots=True)
class Observation:
    """One experiment that has been carried out, and what it returned."""

    experiment: ExperimentId
    template: ExperimentTemplate
    result: DiagnosticVector


@dataclass(frozen=True, slots=True)
class ExpansionCost:
    """What it cost to admit a hypothesis mid-investigation (SPEC §6.2 A11).

    Reported so that the price of hypothesis-space expansion is a measured
    quantity rather than an assumption. A11 checks ``simulator_calls`` against a
    counter wrapped round the injected :data:`Simulator`, so the field counts
    calls and not programme executions -- a caching simulator may perform fewer
    of the latter, and the reported number must still be exactly right.
    """

    simulator_calls: int
    experiments_reevaluated: int


@dataclass(frozen=True, slots=True)
class PPCResult:
    """The verdict of a posterior predictive check (SPEC §6.2 A9).

    ``inadequate`` is the Stage A detection of SPEC F6: the current hypothesis
    space does not explain what was seen. It is a conventional, non-agentic
    judgement, and its measured rate is the floor the LLM must beat rather than
    be credited with.
    """

    alpha: float
    p_value: float
    """Multiplicity-corrected tail probability across every experiment checked."""

    per_experiment: FrozenDict[ExperimentId, float]
    inadequate: bool


class PosteriorEngine(Protocol):
    """What an investigation may ask of a posterior engine (SPEC §3.5)."""

    def log_likelihood(
        self, h: HypothesisId, e: ExperimentId, result: DiagnosticVector
    ) -> tuple[float, float]:
        """Return ``(log-likelihood, Monte Carlo standard error)`` in nats."""
        ...

    def posterior(self) -> Mapping[HypothesisId, Probability]:
        """Return the posterior over every live hypothesis."""
        ...

    def expand(self, h: HypothesisNode) -> ExpansionCost:
        """Register a hypothesis, retro-evaluate it, and renormalise.

        The node arrives already validated and already priced by its graph. The
        engine reads its structure and its status and ignores its
        ``plausibility``: the prior is a function of the code length, and the
        engine re-derives its own normalisation over whatever hypothesis set it
        now holds (SPEC F7).
        """
        ...

    def ppc(self) -> PPCResult:
        """Return a posterior predictive check over the recorded experiments."""
        ...
