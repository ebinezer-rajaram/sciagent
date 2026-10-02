"""The world of one investigation: the truth and the data drawn from it so far.

Framework-side and never agent-reachable (CLAUDE.md invariant 2, SPEC §4.0).
A :class:`World` holds the truth (privately), a seed, the free observational
dataset, and the experiments run so far -- nothing else. In particular it
holds no held-out data: scoring happens after ``submit``, elsewhere, from
data no tool has seen.

The truth is reachable only from :meth:`World.__init__` and
:meth:`World._simulate`, and every public method returns simulated data
(:class:`~sciagent.glm.data.Dataset`), the channel specs, or counts. The tool
layer (:mod:`sciagent.investigation.tools`) talks to a world only through
those methods; ``tests/investigation`` checks both facts statically.

Randomness. Each draw has its own generator, built from
``SeedSequence([seed, stream, index])`` with stream 0 the observational log
and stream 1 the experiments, so experiment k's data depend only on the seed,
k and the design -- never on how many diagnostics or fits ran before it, or on
the order of anything else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.interventions import Compose, Experiment, run_experiment
from sciagent.glm.simulate import Coefficients, MarkSampler, PsiAssignment

__all__ = [
    "MAX_EVENTS",
    "OBSERVATIONAL",
    "OBSERVATIONAL_HORIZON",
    "Truth",
    "TruthSpec",
    "World",
    "WorldError",
    "experiment_id",
]

#: The observational dataset's id, as the agent sees it.
OBSERVATIONAL: Final = "obs"
#: Native horizon of the observational log: about 2,000 events at mean rate 1.
OBSERVATIONAL_HORIZON: Final = 2000.0
#: Event cap per simulated dataset; a run that exceeds it is stopped
#: (:class:`~sciagent.glm.simulate.ExplosionError`). It is what bounds an
#: experiment's *work*, deterministically: a tool call cannot be cancelled,
#: and a wall-clock limit would make outcomes machine-dependent (invariant 3).
#: The simulator's candidate cap (50 per event) and runaway-bound guard scale
#: with it. PowerK and GammaK columns are summed over the whole history on
#: every thinning candidate, so cost grows as the square of the events:
#: 15,000 events take about a minute on the reference machine for the
#: heaviest dev truth, where 50,000 took up to half an hour in the pilot.
#: Every valid design expects fewer at the nominal mean rate of 1 -- the
#: longest horizon (8,000), the clamp cap (5,000) and the forced-event cap
#: (500) together -- so only a process running well above its nominal rate
#: is stopped. A constant, not a function of the design or the session
#: config, so a session's address and every uncapped draw are unchanged.
MAX_EVENTS: Final = 15_000

_STREAM_OBSERVATIONAL: Final = 0
_STREAM_EXPERIMENT: Final = 1


class WorldError(SciAgentError):
    """A request to the world is inconsistent (a framework bug, not an agent act)."""


class Truth(Protocol):
    """What the world needs of a truth: a GLM and its environment's marks.

    The concurrent truth sampler (``sciagent.scenarios``) and the named truths
    of an environment both satisfy it through :class:`TruthSpec`.
    """

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


@dataclass(frozen=True)
class TruthSpec:
    """A concrete :class:`Truth`."""

    structure: Structure
    psi: PsiAssignment
    coef: Coefficients
    channels: tuple[ChannelSpec, ...]
    mark_sampler: MarkSampler


def experiment_id(index: int) -> str:
    """The agent-visible id of experiment ``index`` (0-based)."""
    return f"e{index}"


def _generator(seed: int, stream: int, index: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, stream, index]))


class World:
    """The truth, its seeded draws, and the datasets drawn so far."""

    __slots__ = ("_datasets", "_experiments_run", "_seed", "_truth")

    def __init__(self, truth: Truth, seed: int) -> None:
        if isinstance(seed, bool) or not 0 <= seed < 2**63:
            raise WorldError(f"seed must be an integer in [0, 2**63), got {seed!r}")
        self._truth = truth
        self._seed = seed
        self._experiments_run = 0
        self._datasets: dict[str, Dataset] = {}
        observational = self._simulate(
            Experiment(Compose(()), OBSERVATIONAL_HORIZON),
            _generator(seed, _STREAM_OBSERVATIONAL, 0),
            OBSERVATIONAL,
        )
        self._datasets[OBSERVATIONAL] = observational

    # -- the only code that touches the truth ---------------------------------

    def _simulate(
        self, experiment: Experiment, rng: np.random.Generator, label: str
    ) -> Dataset:
        truth = self._truth
        return run_experiment(
            truth.structure,
            truth.psi,
            truth.coef,
            truth.channels,
            truth.mark_sampler,
            experiment,
            rng,
            max_events=MAX_EVENTS,
            label=label,
        )

    # -- public surface: data, channels, counts ---------------------------------

    @property
    def channels(self) -> tuple[ChannelSpec, ...]:
        """The environment's mark channels (native names)."""
        return tuple(self._truth.channels)

    @property
    def experiments_run(self) -> int:
        """Experiments started so far, including any whose run was stopped."""
        return self._experiments_run

    @property
    def datasets(self) -> Mapping[str, Dataset]:
        """Every dataset drawn so far, by id: ``obs`` then ``e0``, ``e1``, ..."""
        return dict(self._datasets)

    def run(self, experiment: Experiment) -> tuple[int, Dataset]:
        """Run the next experiment; return its index and its (native) dataset.

        The index is consumed before the draw, so a run stopped by the
        simulator (a :class:`~sciagent.glm.simulate.SimulationError`, e.g.
        more than :data:`MAX_EVENTS` events) still uses up its index and
        leaves no dataset; the error propagates to the caller.
        """
        index = self._experiments_run
        self._experiments_run += 1
        label = experiment_id(index)
        data = self._simulate(
            experiment, _generator(self._seed, _STREAM_EXPERIMENT, index), label
        )
        self._datasets[label] = data
        return index, data
