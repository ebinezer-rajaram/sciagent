"""The non-agent v2 systems: B-lib, B-np and ORACLE (SPEC §4.1).

Every system takes an :class:`InvestigationData` and returns a
:class:`SystemResult`. :class:`InvestigationData` holds exactly what a system
may see: the observational dataset(s), the channel specs and the
environment's mark sampler. There is no path to the truth through it; ORACLE
receives the true structure as an explicit constructor argument, which is the
one place truth enters a system, and is visible at the call site.

- **B-lib** fits every library member on the observational data, in library
  order: in-grammar members by the certified fitter
  (:func:`~sciagent.glm.fit.fit`), out-of-grammar members by their own
  ``fit`` (``sciagent.library``). It submits the lowest BIC, ties to the
  earlier member. An uncertified grammar fit is never selected (SPEC §2.2:
  uncertified fits are flagged, never silently used) but still counts as a
  fit used. When an out-of-grammar member wins, the submitted structure is
  None: it has no grammar form, so structural recovery treats it as a
  non-answer.
- **B-np** estimates the Wiener-Hopf filter on the observational log
  (exactly one, unintervened) and submits no structure. It uses no fits.
- **ORACLE** fits the true structure; one fit (by its budget). Given the
  truth's ψ and coefficients, it also θ-refits at the truth's ψ point and keeps
  that fit when it is certified and its training log L is strictly higher than
  the search's, so ORACLE never loses to its own truth on training data because
  the ψ search stalled (PILOT-AUDIT §1). A higher but uncertified refit is not
  kept (SPEC §2.2): the search fit stays, ``oracle_psi_source`` stays
  ``"search"``, ``oracle_underfit`` is computed as always against the true
  parameters, and ``oracle_truth_psi_certified`` is recorded as False (present
  only in that case). Its ``details`` record ``oracle_psi_source``
  (``"search"`` or ``"truth"``), ``oracle_underfit`` (the kept fit's training
  log L is below the true parameters' by more than ``1e-9·|log L|``), the
  training log Ls, and ``oracle_truth_psi_on_grid``: a truth whose ψ is off
  the grids (a test truth, not a sampled one) gets no truth-ψ refit and
  ``oracle_underfit`` False: no fit can reach its true parameters, so a
  deficit there measures the grid, not the search. The truth
  enters only here, on the framework side (invariant 2).

**Trajectory.** ``trajectory[i]`` is the best-so-far model after ``i + 1``
fits (by the system's own criterion, BIC), so the scorer can draw the
efficiency curve (SPEC §4.3) by evaluating each point's model on held-out
data, which no system sees.

Domain-independent: the library is passed in (the environment owns which
structures and models make it up).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset
from sciagent.glm.fit import (
    FitConfig,
    FitError,
    fit,
    on_grid,
    parameter_log_likelihood,
)
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.simulate import Coefficients, MarkSampler, PsiAssignment
from sciagent.library.base import LibraryError, LibraryModel
from sciagent.nonparam.wiener_hopf import DEFAULT, WienerHopfConfig
from sciagent.systems.v2.models import (
    FittedModel,
    GLMModel,
    LibraryFittedModel,
    WienerHopfModel,
)

B_LIB: Final = "B-lib"
B_NP: Final = "B-np"
ORACLE: Final = "ORACLE"


class SystemRunError(SciAgentError):
    """A system cannot run on the data it was given."""


@dataclass(frozen=True)
class InvestigationData:
    """What a system may see: observational data, channels, the mark law."""

    observational: tuple[Dataset, ...]
    channels: tuple[ChannelSpec, ...]
    marks: MarkSampler

    def __post_init__(self) -> None:
        if not self.observational:
            raise SystemRunError("need at least one observational dataset")


@dataclass(frozen=True)
class TrajectoryPoint:
    """The best-so-far model after ``fits_used`` fits."""

    fits_used: int
    candidate: str
    candidate_criterion: float
    best: str
    best_criterion: float
    best_model: FittedModel


@dataclass(frozen=True)
class SystemResult:
    """A system's submission: structure (or None), fitted model and its path.

    ``submitted`` names the submitted model (a library member's name, the
    oracle's, or B-np's). ``skipped`` lists members that could not be used
    (failed or uncertified fits), in library order. ``details`` holds
    system-specific diagnostics for the result record (ORACLE's truth-ψ check).
    """

    system: str
    submitted: str
    structure: Structure | None
    model: FittedModel
    fits_used: int
    trajectory: tuple[TrajectoryPoint, ...]
    skipped: tuple[str, ...] = ()
    details: Mapping[str, str | bool | float] = field(default_factory=dict)


class System(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, data: InvestigationData) -> SystemResult: ...


# --------------------------------------------------------------------------
# Library entries
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GrammarMember:
    """A library member written in the grammar, fitted like any proposal."""

    name: str
    structure: Structure


@dataclass(frozen=True)
class ModelMember:
    """An out-of-grammar library member (``sciagent.library``)."""

    name: str
    model: LibraryModel


type LibraryEntry = GrammarMember | ModelMember


# --------------------------------------------------------------------------
# Systems
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BLib:
    """Fit every library member; submit the best by BIC (module docstring)."""

    library: tuple[LibraryEntry, ...]
    fit_config: FitConfig | None = None
    name: str = B_LIB

    def run(self, data: InvestigationData) -> SystemResult:
        if not self.library:
            raise SystemRunError("B-lib needs a non-empty library")
        best: tuple[float, str, Structure | None, FittedModel] | None = None
        trajectory: list[TrajectoryPoint] = []
        skipped: list[str] = []
        for used, entry in enumerate(self.library, start=1):
            candidate = self._fit(entry, data)
            if candidate is None:
                skipped.append(entry.name)
                criterion = math.inf
            else:
                criterion, structure, model = candidate
                if best is None or criterion < best[0]:
                    best = (criterion, entry.name, structure, model)
            if best is not None:
                trajectory.append(
                    TrajectoryPoint(
                        used, entry.name, criterion, best[1], best[0], best[3]
                    )
                )
        if best is None:
            raise SystemRunError("no library member could be fitted")
        return SystemResult(
            system=self.name,
            submitted=best[1],
            structure=best[2],
            model=best[3],
            fits_used=len(self.library),
            trajectory=tuple(trajectory),
            skipped=tuple(skipped),
        )

    def _fit(
        self, entry: LibraryEntry, data: InvestigationData
    ) -> tuple[float, Structure | None, FittedModel] | None:
        match entry:
            case GrammarMember(name=name, structure=structure):
                try:
                    result = fit(
                        structure,
                        data.observational,
                        data.channels,
                        config=self.fit_config,
                    )
                except FitError:
                    return None
                if not result.certified:
                    return None
                return (
                    result.bic,
                    structure,
                    GLMModel(name, result, data.channels, data.marks),
                )
            case ModelMember(model=model):
                try:
                    fitted = model.fit(data.observational)
                except LibraryError:
                    return None
                return (
                    fitted.bic,
                    None,
                    LibraryFittedModel(fitted, data.channels, data.marks),
                )


@dataclass(frozen=True)
class BNp:
    """Wiener-Hopf kernels on the observational log; no structure, no fits."""

    config: WienerHopfConfig = DEFAULT
    name: str = B_NP

    def run(self, data: InvestigationData) -> SystemResult:
        if len(data.observational) != 1:
            raise SystemRunError("B-np estimates on exactly one observational log")
        (obs,) = data.observational
        if obs.excluded or not bool(obs.endogenous.all()):
            raise SystemRunError("B-np needs an unintervened log")
        model = WienerHopfModel.estimate(obs.log, data.channels, config=self.config)
        return SystemResult(
            system=self.name,
            submitted=model.name,
            structure=None,
            model=model,
            fits_used=0,
            trajectory=(),
        )


#: Relative tolerance of ``oracle_underfit``.
UNDERFIT_TOL_REL: Final = 1e-9


def oracle_underfit(fitted_ll: float, true_ll: float) -> bool:
    """Whether a fit's training log L is below the true parameters' by more
    than ``UNDERFIT_TOL_REL · |true_ll|``."""
    return fitted_ll < true_ll - UNDERFIT_TOL_REL * abs(true_ll)


@dataclass(frozen=True)
class Oracle:
    """Fit the true structure (SPEC §4.1, §6.4). ``structure`` is the truth's;
    ``truth_psi`` and ``truth_coefficients`` (both or neither) enable the
    truth-ψ check (module docstring)."""

    structure: Structure
    fit_config: FitConfig | None = None
    name: str = ORACLE
    truth_psi: PsiAssignment | None = None
    truth_coefficients: Coefficients | None = None

    def run(self, data: InvestigationData) -> SystemResult:
        if (self.truth_psi is None) != (self.truth_coefficients is None):
            raise SystemRunError(
                "ORACLE takes the truth's ψ and coefficients together, or neither"
            )
        obs, channels = data.observational, data.channels
        result = fit(self.structure, obs, channels, config=self.fit_config)
        details: dict[str, str | bool | float] = {}
        if self.truth_psi is not None and self.truth_coefficients is not None:
            quadrature = (self.fit_config or FitConfig()).quadrature
            true_ll = math.fsum(
                parameter_log_likelihood(
                    self.structure,
                    self.truth_psi,
                    self.truth_coefficients,
                    obs,
                    channels,
                    quadrature=quadrature,
                )
            )
            search_ll = result.log_likelihood
            source = "search"
            details = {"oracle_search_ll": search_ll, "oracle_true_params_ll": true_ll}
            gridded = on_grid(self.structure, self.truth_psi)
            details["oracle_truth_psi_on_grid"] = gridded
            if gridded:
                at_truth = fit(
                    self.structure,
                    obs,
                    channels,
                    config=self.fit_config,
                    psi=self.truth_psi,
                )
                details["oracle_truth_psi_ll"] = at_truth.log_likelihood
                if at_truth.log_likelihood > search_ll:
                    if at_truth.certified:
                        result, source = at_truth, "truth"
                    else:
                        # Never silently used (SPEC §2.2). Recorded only here,
                        # where it decided the outcome, so every other cell's
                        # detail bytes are unchanged.
                        details["oracle_truth_psi_certified"] = False
            details["oracle_psi_source"] = source
            # Off the grids no fit can reach the true parameters, so the flag
            # (a search failure) is only defined on them.
            details["oracle_underfit"] = gridded and oracle_underfit(
                result.log_likelihood, true_ll
            )
        model = GLMModel(self.name, result, channels, data.marks)
        return SystemResult(
            system=self.name,
            submitted=self.name,
            structure=self.structure,
            model=model,
            fits_used=1,
            trajectory=(
                TrajectoryPoint(1, self.name, result.bic, self.name, result.bic, model),
            ),
            details=details,
        )


def run_all(
    systems: Sequence[System], data: InvestigationData
) -> tuple[SystemResult, ...]:
    """Run each system on the same data, in order."""
    return tuple(s.run(data) for s in systems)
