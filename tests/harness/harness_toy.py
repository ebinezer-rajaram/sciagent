"""A toy, deterministic tool layer for exercising the harness without science.

Four tools stand in for the real ones the later P2 agents build:

* ``run_experiment(rate)`` -- metered by ``E``; seals the prediction ledger for
  its index *before* it draws, draws five Poisson counts from a generator seeded
  by ``(seed, index)``, then has the framework evaluate every prediction that
  targeted it. The agent never supplies an evaluated value.
* ``fit(model)`` -- metered by ``F``; a deterministic score of the model string
  against the data collected so far.
* ``predict(experiment, low, high, level, hypothesis)`` -- unmetered; commits a
  prediction of the mean count of a future experiment.
* ``submit(answer, report)`` -- metered by ``submit`` (once); ends the run.

``add(a, b)`` is there for the live smoke script.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import numpy as np

from sciagent.harness.ledger import Prediction, PredictionLedger
from sciagent.harness.tools import ToolLayer, ToolOutput, ToolSpec

NUMBER: dict[str, Any] = {"type": "number"}


class ToyLab:
    """The toy environment: the only state the tools can reach."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.ledger = PredictionLedger()
        self.data: list[list[int]] = []

    def run_experiment(self, args: Mapping[str, Any]) -> ToolOutput:
        rate = float(args["rate"])
        if not 0.0 < rate <= 50.0:
            return ToolOutput(f"rate must be in (0, 50], got {rate}", is_error=True)
        index = len(self.data)
        self.ledger.seal(index)
        rng = np.random.default_rng([self.seed, index])
        counts = [int(c) for c in rng.poisson(rate, size=5)]
        self.data.append(counts)
        evaluations = self.ledger.evaluate(
            index, lambda prediction: float(np.mean(self.data[prediction.experiment]))
        )
        covered = [e.covered for e in evaluations]
        return ToolOutput(
            json.dumps({"experiment": index, "counts": counts}),
            record={"experiment": index, "counts": counts, "covered": covered},
        )

    def fit(self, args: Mapping[str, Any]) -> ToolOutput:
        model = str(args["model"])
        blob = json.dumps([model, self.data]).encode("utf-8")
        score = int(hashlib.sha256(blob).hexdigest()[:8], 16) / 2**32
        return ToolOutput(f"score={score:.6f}", record={"score": score})

    def predict(self, args: Mapping[str, Any]) -> ToolOutput:
        prediction = Prediction(
            experiment=int(args["experiment"]),
            statistic="mean_count",
            interval=(float(args["low"]), float(args["high"])),
            level=float(args["level"]),
            hypothesis=str(args["hypothesis"]),
        )
        return self.ledger.commit_tool(prediction)

    def submit(self, args: Mapping[str, Any]) -> ToolOutput:
        return ToolOutput(
            "submission recorded; the investigation is over",
            record={"answer": str(args["answer"])},
        )


def add(args: Mapping[str, Any]) -> ToolOutput:
    total = float(args["a"]) + float(args["b"])
    return ToolOutput(f"{total:g}")


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def toy_layer(seed: int, *, experiments: int = 3, fits: int = 2) -> ToolLayer:
    """Return a fresh toy layer. Same seed => byte-identical tool results."""
    lab = ToyLab(seed)
    specs = [
        ToolSpec(
            "run_experiment",
            "Run one experiment at a given rate; returns five counts.",
            _schema({"rate": NUMBER}),
            lab.run_experiment,
            budget="E",
        ),
        ToolSpec(
            "fit",
            "Score a model.",
            _schema({"model": {"type": "string"}}),
            lab.fit,
            "F",
        ),
        ToolSpec(
            "predict",
            "Commit a prediction of a future experiment's mean count.",
            _schema(
                {
                    "experiment": {"type": "integer"},
                    "low": NUMBER,
                    "high": NUMBER,
                    "level": NUMBER,
                    "hypothesis": {"type": "string"},
                }
            ),
            lab.predict,
            None,
        ),
        ToolSpec(
            "add", "Add two numbers.", _schema({"a": NUMBER, "b": NUMBER}), add, None
        ),
        ToolSpec(
            "submit",
            "Submit the final answer; ends the investigation.",
            _schema({"answer": {"type": "string"}, "report": {"type": "string"}}),
            lab.submit,
            budget="submit",
            terminal=True,
        ),
    ]
    return ToolLayer(specs, {"E": experiments, "F": fits, "submit": 1})


def toy_factory(config: Mapping[str, Any]) -> ToolLayer:
    """Rebuild the toy layer from a recorded config, as replay does."""
    budgets = config["budgets"]
    return toy_layer(int(config["seed"]), experiments=budgets["E"], fits=budgets["F"])
