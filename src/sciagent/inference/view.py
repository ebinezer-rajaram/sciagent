"""The posterior engine as a research system is allowed to see it.

SPEC's second invariant -- the framework writes numbers, agents write structure
-- made a capability boundary rather than a docstring. An
:class:`~sciagent.inference.empirical.EmpiricalTableEngine` has a public
recording surface, and it must have one: :class:`~sciagent.eval.campaign`
records the Stage A probe through it and
:meth:`~sciagent.systems.base.Investigation.run` records every experiment
through it. What must not happen is a *system* reaching that surface, because
the posterior is a pure function of what has been recorded, so a system that
records writes the belief it is scored on.

The distinction this module draws is therefore between two callers of one
object, and a view is how you say that in Python: the framework keeps the
engine, the system is handed an :class:`EngineView` of it, and the difference
between them is which methods exist at all.

What a view withholds
---------------------

``record``, ``record_probe``, ``expand``, ``ensure_structure`` and ``ppc``.

The first four mutate. ``ppc`` does not, and is withheld for a different reason:
``ppc(experiments={...})`` lets its caller choose which readings the adequacy
verdict is computed from, and SPEC §4.6's Stage A is a question about whether
the entertained space is adequate *at all*, asked of the framework-chosen
reading. :meth:`~sciagent.systems.base.Investigation.ppc` is the sanctioned path
and does that scoping; forwarding the engine's own method would hand the choice
straight back.

Not the only defence
--------------------

A view is a boundary, and a boundary with nothing behind it is what the previous
arrangement was. :func:`~sciagent.eval.campaign.run_scenario` also reconciles
what the engine holds -- both its evidence and the hypotheses it entertains --
against what the investigation charged for and what the graph admitted, and
raises :class:`~sciagent.core.errors.EngineTamperError` on disagreement. So a
system that reaches an engine some other way is caught on every quantity that
check names.

Treat that second defence as the load-bearing one, and its docstring as the
statement of what is actually covered. This class makes tampering deliberate;
the reconciliation is what refuses to score it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sciagent.core.edits import Defect
from sciagent.core.types import ExperimentId, HypothesisId, Probability
from sciagent.inference.binning import DiagnosticVector
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    opaque_table,
)
from sciagent.inference.interface import LikelihoodEstimate, Observation

__all__ = ["EngineView", "ReadableEngine"]


class ReadableEngine(Protocol):
    """The belief and the structures behind it, however the caller came by them.

    What :mod:`sciagent.experiments.boed` needs of an engine, and no more, so
    that framework code holding an :class:`EmpiricalTableEngine` and a system
    holding an :class:`EngineView` can drive the same planner.

    It lives here rather than beside
    :class:`~sciagent.inference.interface.PosteriorEngine`, where a protocol
    would otherwise belong, for a mechanical reason: ``table`` returns an
    :class:`~sciagent.inference.empirical.EmpiricalTable`, and
    ``inference.interface`` is what ``inference.empirical`` imports *from*.
    """

    @property
    def table(self) -> EmpiricalTable:
        """Return the table likelihoods are read from."""
        ...

    def program_edit(self, h: HypothesisId) -> Defect:
        """Return the edit set of one hypothesis."""
        ...

    def posterior(self) -> Mapping[HypothesisId, Probability]:
        """Return the belief over every hypothesis held."""
        ...


@dataclass(frozen=True, slots=True)
class EngineView:
    """Every question a system may ask an engine, and no method that answers one.

    Guarantees exactly this much: **no method on this class writes**, so the
    mutating surface is not reachable by calling anything here.

    It does not guarantee that the wrapped engine is unreachable, and saying so
    would be the very thing SPEC's second invariant forbids -- a comment holding
    a line it cannot hold. ``_engine`` is a private field, the same arrangement
    :class:`~sciagent.systems.base.Investigation` uses for ground truth, and it
    stops an accident rather than a determined caller.

    Nor is the claim "the reconciliation catches the determined caller" one this
    docstring is entitled to make, which an audit pointed out after an earlier
    wording made it. :func:`~sciagent.eval.campaign._reconcile` checks named
    quantities -- the evidence recorded, the structures entertained, which of
    them are live -- against what the run charged for and the graph admitted. It
    is a check with a stated scope, not a proof of containment, and one thing it
    does **not** cover is replacement of the engine's table, which legitimately
    changes under ``ensure_structure`` and so cannot be compared against a fixed
    expectation. See that function for the current scope; treat this class as
    what makes tampering deliberate rather than accidental, and read the scope
    there rather than a summary here that would drift from it.

    Delegation is explicit rather than through ``__getattr__``, deliberately. A
    forwarding fallback would re-expose every method the engine grows later, so
    the boundary would silently widen with the class it wraps; here, admitting a
    new capability is an edit to this file.
    """

    _engine: EmpiricalTableEngine

    # -- what the record holds ---------------------------------------------

    @property
    def table(self) -> EmpiricalTable:
        """Return the table the engine reads its likelihoods from, opaquely keyed.

        Every number in it is the engine's own, and every lookup a system makes
        -- which is always by a ``Defect`` it already holds -- returns exactly
        what the engine's table returns. What it does not return is the *list* of
        structures: those keys are digests here, and renderings on the engine's
        own table.

        Gate A41 is why. :func:`~sciagent.inference.empirical.structure_key`
        renders an edit set completely -- type, target, construct, every
        parameter -- so ``structures`` on the engine's table reads out every
        structure it was built over. The §9 campaign threads one table from cell
        to cell, so that list is every scenario's truth, S11's out-of-library one
        included; a system could have read the answer off the artefact instead of
        investigating for it. The projection is memoised per table, so this
        property stays a read rather than a rebuild.
        """
        return opaque_table(self._engine.table)

    @property
    def observations(self) -> tuple[Observation, ...]:
        """Return every recorded experiment, in the order it was recorded.

        Experiments only, as on the engine: a Stage A probe is a reading about
        the space rather than evidence in it, and is invisible here.
        """
        return self._engine.observations

    def observation(self, e: ExperimentId) -> Observation:
        """Return one recorded experiment, or raise a typed error."""
        return self._engine.observation(e)

    # -- what is entertained -----------------------------------------------

    @property
    def hypotheses(self) -> tuple[HypothesisId, ...]:
        """Return every hypothesis the engine holds, in a fixed order."""
        return self._engine.hypotheses

    @property
    def live(self) -> tuple[HypothesisId, ...]:
        """Return the hypotheses a posterior is distributed over."""
        return self._engine.live

    def program_edit(self, h: HypothesisId) -> Defect:
        """Return the edit set of one hypothesis, or raise a typed error."""
        return self._engine.program_edit(h)

    # -- what the numbers are ----------------------------------------------

    def estimate(self, h: HypothesisId, e: ExperimentId) -> LikelihoodEstimate:
        """Return the full likelihood estimate of experiment ``e`` under ``h``."""
        return self._engine.estimate(h, e)

    def log_likelihood(
        self, h: HypothesisId, e: ExperimentId, result: DiagnosticVector
    ) -> tuple[float, float]:
        """Return ``(log-likelihood, Monte Carlo standard error)`` in nats."""
        return self._engine.log_likelihood(h, e, result)

    def log_prior(self, h: HypothesisId) -> float:
        """Return the unnormalised structural log-prior of ``h``, in nats."""
        return self._engine.log_prior(h)

    def log_likelihood_total(self, h: HypothesisId) -> float:
        """Return the summed log-likelihood of every recorded experiment under ``h``."""
        return self._engine.log_likelihood_total(h)

    def log_posterior_weights(self) -> Mapping[HypothesisId, float]:
        """Return each live hypothesis's unnormalised log-posterior, in nats."""
        return self._engine.log_posterior_weights()

    def posterior(self) -> Mapping[HypothesisId, Probability]:
        """Return the engine's current belief. Recomputed, never cached."""
        return self._engine.posterior()
