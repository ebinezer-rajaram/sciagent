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
- **ORACLE** fits the true structure; one fit.

**Trajectory.** ``trajectory[i]`` is the best-so-far model after ``i + 1``
fits (by the system's own criterion, BIC), so the scorer can draw the
efficiency curve (SPEC §4.3) by evaluating each point's model on held-out
data, which no system sees.

Domain-independent: the library is passed in (the environment owns which
structures and models make it up).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, FitError, fit
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.simulate import MarkSampler
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
    (failed or uncertified fits), in library order.
    """

    system: str
    submitted: str
    structure: Structure | None
    model: FittedModel
    fits_used: int
    trajectory: tuple[TrajectoryPoint, ...]
    skipped: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class Oracle:
    """Fit the true structure (SPEC §4.1, §6.4). ``structure`` is the truth's."""

    structure: Structure
    fit_config: FitConfig | None = None
    name: str = ORACLE

    def run(self, data: InvestigationData) -> SystemResult:
        result = fit(
            self.structure, data.observational, data.channels, config=self.fit_config
        )
        model = GLMModel(self.name, result, data.channels, data.marks)
        return SystemResult(
            system=self.name,
            submitted=self.name,
            structure=self.structure,
            model=model,
            fits_used=1,
            trajectory=(
                TrajectoryPoint(1, self.name, result.bic, self.name, result.bic, model),
            ),
        )


def run_all(
    systems: Sequence[System], data: InvestigationData
) -> tuple[SystemResult, ...]:
    """Run each system on the same data, in order."""
    return tuple(s.run(data) for s in systems)
