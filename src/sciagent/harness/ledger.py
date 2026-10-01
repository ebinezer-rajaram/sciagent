"""The prediction ledger: committed predictions, sealed before their experiment.

SPEC §4.4 and instrument test 10. An agent may commit a prediction -- a
statistic, the experiment it applies to, the hypothesis it follows from, a
central interval at a stated level -- through the ``predict`` tool. The
scientific-behaviour read-outs (calibration, falsification-seeking, revision)
are only worth anything if two rules hold, and this class is where they hold:

1. **No prediction after the fact.** Experiments are numbered 0, 1, 2, ... in the
   order they run. The experiment tool calls :meth:`PredictionLedger.seal` with
   the index it is *about* to run, before drawing anything; from then on a
   :meth:`~PredictionLedger.commit` targeting that index or an earlier one is
   refused. Sealing is strictly sequential, so there is no index the agent can
   name that is "not yet sealed" but already has data.
2. **The framework evaluates.** :class:`Prediction` has no field for an observed
   value. :meth:`~PredictionLedger.evaluate` takes a framework-supplied callable
   that computes the statistic from the experiment's actual data, and is only
   allowed once the experiment is sealed, once per experiment.

The ledger is mutated only through tool handlers, so it is rebuilt exactly by
replay re-issuing the recorded calls; it needs no record of its own.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from sciagent.harness.errors import PredictionRefusedError
from sciagent.harness.tools import ToolOutput

__all__ = ["Commitment", "Evaluation", "Prediction", "PredictionLedger"]


@dataclass(frozen=True)
class Prediction:
    """What the agent states. Deliberately carries no observed value."""

    experiment: int
    statistic: str
    interval: tuple[float, float]
    level: float
    hypothesis: str

    def __post_init__(self) -> None:
        low, high = self.interval
        if not (math.isfinite(low) and math.isfinite(high)) or low > high:
            raise PredictionRefusedError(
                f"interval must be finite with low <= high, got {self.interval}"
            )
        if not 0.0 < self.level < 1.0:
            raise PredictionRefusedError(f"level must be in (0, 1), got {self.level}")
        if self.experiment < 0:
            raise PredictionRefusedError(f"experiment index {self.experiment} < 0")
        if not self.statistic:
            raise PredictionRefusedError("a prediction must name its statistic")


@dataclass(frozen=True)
class Commitment:
    """A committed prediction; ``sealed_before`` is how many experiments were
    sealed when it was committed (always <= ``prediction.experiment``)."""

    index: int
    prediction: Prediction
    sealed_before: int


@dataclass(frozen=True)
class Evaluation:
    """The framework's evaluation of one commitment on its experiment's data."""

    commitment: int
    value: float
    covered: bool


class PredictionLedger:
    """Committed predictions, the sealed-experiment counter, and evaluations."""

    __slots__ = ("_committed", "_evaluated", "_evaluations", "_sealed")

    def __init__(self) -> None:
        self._committed: list[Commitment] = []
        self._sealed = 0
        self._evaluated: list[int] = []
        self._evaluations: list[Evaluation] = []

    @property
    def next_experiment(self) -> int:
        """The index the next experiment will run as (= number sealed)."""
        return self._sealed

    @property
    def committed(self) -> tuple[Commitment, ...]:
        return tuple(self._committed)

    @property
    def evaluations(self) -> tuple[Evaluation, ...]:
        return tuple(self._evaluations)

    def commit(self, prediction: Prediction) -> Commitment:
        """Record ``prediction``; refuse it if its experiment is already sealed."""
        if prediction.experiment < self._sealed:
            raise PredictionRefusedError(
                f"experiment {prediction.experiment} has already been run (or is "
                f"running); a prediction must be committed before its experiment. "
                f"The next experiment is {self._sealed}"
            )
        entry = Commitment(len(self._committed), prediction, self._sealed)
        self._committed.append(entry)
        return entry

    def commit_tool(self, prediction: Prediction) -> ToolOutput:
        """:meth:`commit` for a tool handler: a refusal becomes an error text."""
        try:
            entry = self.commit(prediction)
        except PredictionRefusedError as error:
            return ToolOutput(f"prediction refused: {error}", is_error=True)
        return ToolOutput(
            f"prediction {entry.index} committed for experiment "
            f"{prediction.experiment}",
            record={"commitment": entry.index, "experiment": prediction.experiment},
        )

    def seal(self, experiment: int) -> None:
        """Mark ``experiment`` as running. Must be called before it draws data."""
        if experiment != self._sealed:
            raise PredictionRefusedError(
                f"experiments are sealed in order: expected {self._sealed}, got "
                f"{experiment}"
            )
        self._sealed += 1

    def evaluate(
        self, experiment: int, statistic: Callable[[Prediction], float]
    ) -> tuple[Evaluation, ...]:
        """Evaluate every commitment targeting ``experiment``, once.

        ``statistic`` is the framework's: it computes the predicted statistic
        from the experiment's actual data. Coverage is ``low <= value <= high``.
        """
        if experiment >= self._sealed:
            raise PredictionRefusedError(
                f"experiment {experiment} has not run; nothing to evaluate against"
            )
        if experiment in self._evaluated:
            raise PredictionRefusedError(
                f"experiment {experiment} was already evaluated"
            )
        self._evaluated.append(experiment)
        made: list[Evaluation] = []
        for entry in self._committed:
            if entry.prediction.experiment != experiment:
                continue
            value = float(statistic(entry.prediction))
            low, high = entry.prediction.interval
            made.append(Evaluation(entry.index, value, low <= value <= high))
        self._evaluations.extend(made)
        return tuple(made)
