"""The truth, as the scorer sees it: a GLM that can be simulated.

:class:`TruthLike` is the same shape as
:class:`sciagent.investigation.world.Truth` (structure, ψ, θ, channels and the
environment's mark sampler), so a ``TruthSpec`` from the investigation or a
truth from the sampler is accepted unchanged. It is a separate protocol so
that scoring does not depend on the investigation package.

:class:`TruthModel` exposes a truth through the one method interventional
similarity reads from any model (``simulate_experiment``), running it on
:func:`~sciagent.glm.interventions.run_experiment` exactly as the
investigation's world does. Framework-side only: nothing an agent can reach
holds one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from sciagent.glm.data import Dataset
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.interventions import Experiment, run_experiment
from sciagent.glm.simulate import Coefficients, MarkSampler, PsiAssignment

#: Event cap per simulated truth dataset; the investigation world's cap.
TRUTH_MAX_EVENTS: Final = 50_000


class TruthLike(Protocol):
    """A data-generating GLM and its environment (module docstring)."""

    @property
    def structure(self) -> Structure: ...

    @property
    def psi(self) -> PsiAssignment: ...

    @property
    def coef(self) -> Coefficients: ...

    @property
    def channels(self) -> tuple[ChannelSpec, ...]: ...

    @property
    def mark_sampler(self) -> MarkSampler: ...


def simulate_truth(
    truth: TruthLike,
    experiment: Experiment,
    rng: np.random.Generator,
    *,
    label: str = "experiment",
    max_events: int = TRUTH_MAX_EVENTS,
) -> Dataset:
    """``experiment`` run on the truth (as the investigation's world runs it)."""
    return run_experiment(
        truth.structure,
        truth.psi,
        truth.coef,
        truth.channels,
        truth.mark_sampler,
        experiment,
        rng,
        max_events=max_events,
        label=label,
    )


@dataclass(frozen=True)
class TruthModel:
    """A truth as a simulator: the side of the battery every model is held to."""

    truth: TruthLike
    name: str = "truth"
    max_events: int = TRUTH_MAX_EVENTS

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset:
        return simulate_truth(
            self.truth, experiment, rng, label=label, max_events=self.max_events
        )
