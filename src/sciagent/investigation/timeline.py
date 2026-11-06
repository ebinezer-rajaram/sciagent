"""The belief timeline: SPEC §4.4's inputs, read from a session record (P4 Δ4).

:mod:`sciagent.scoring.behaviour` computes the §4.4 measures from three plain
sequences. This module builds them from a :class:`SessionRecord`'s
``tool_call`` entries, so the measures are a pure function of the record.

What is read
------------
Only successful calls (``is_error`` false) count, and only their ``record``
field, which the framework writes. Agent arguments (``args``) are read for one
thing only: the stated interval, level and diagnostic of a prediction whose
experiment never ran, which no measure scores beyond counting it unevaluated.

- ``belief``: ``{"belief", "favoured", "rivals", "confidence",
  "experiments_run"}`` becomes a :class:`Belief`.
- ``predict``: ``{"commitment", "experiment", "hypothesis"}``.
- ``run_experiment``: ``{"experiment", "predictions": [...]}``, the framework's
  evaluation of every prediction committed for it. A stopped experiment
  (``"stopped": true``) has no ``predictions``; its commitments stay
  unevaluated.
- ``submit``: ``{"structure"}`` becomes a final :class:`Belief` with no rivals
  and no confidence: the submission is the agent's last statement of belief.

The clock
---------
Calls are taken in the layer's call order (``body["call"]``, the order the
framework executed them, which is also the order replay re-issues them). A
belief's turn is the number of experiments that had run when it was called,
counted from the ``run_experiment`` records; experiment j runs at turn j. So a
belief stated after experiment j has turn j + 1, and the belief in force at
experiment j is the last one stated before it. The ``experiments_run`` field
the framework writes is checked against the count and a disagreement refuses
the record; nothing an agent writes in ``args`` reaches a turn.

Labels
------
Every hypothesis (favoured, rivals, a prediction's hypothesis, the submitted
structure) is re-parsed against the native channels and labelled by the text of
its canonical form, ``render(canonicalise(parse(text)))``. Two spellings of one
structure (features reordered, products commuted, whitespace) are one label,
which is what makes "the same hypothesis came back" mean the same structure.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sciagent.core.errors import GrammarError
from sciagent.glm.canonical import canonicalise
from sciagent.glm.grammar import ChannelSpec
from sciagent.glm.syntax import parse, render
from sciagent.harness.errors import RecordFormatError
from sciagent.harness.ledger import Evaluation, Prediction
from sciagent.harness.record import SessionRecord, canonical_json
from sciagent.scoring.behaviour import (
    Belief,
    ExperimentRun,
    PredictionRecord,
    favoured_at,
)
from sciagent.scoring.errors import ScoringError

__all__ = ["Timeline", "canonical_label", "timeline"]


@dataclass(frozen=True)
class Timeline:
    """The §4.4 inputs of one investigation.

    ``predictions`` are in commitment order; ``experiments`` and ``beliefs`` in
    turn order (beliefs within a turn in call order). ``submitted`` is the
    canonical label of the submitted structure, None if the run never
    submitted.
    """

    predictions: tuple[PredictionRecord, ...]
    experiments: tuple[ExperimentRun, ...]
    beliefs: tuple[Belief, ...]
    submitted: str | None

    def favoured_path(self) -> tuple[str | None, ...]:
        """The structure favoured after i experiments, for i = 0..n.

        ``path[i]`` is the belief in force at turn i, so ``path[n]`` is the
        submission when there was one. This is the sequence whose held-out gaps
        make a :class:`~sciagent.scoring.behaviour.StoppingRun`.
        """
        path: list[str | None] = []
        for i in range(len(self.experiments) + 1):
            belief = favoured_at(self.beliefs, i)
            path.append(None if belief is None else belief.favoured)
        return tuple(path)


def canonical_label(text: str, channels: tuple[ChannelSpec, ...]) -> str:
    """The canonical native text of DSL ``text``: one label per structure."""
    return render(canonicalise(parse(text, channels)))


def _label(text: object, channels: tuple[ChannelSpec, ...], what: str) -> str:
    if not isinstance(text, str):
        raise RecordFormatError(f"{what} must be DSL text, got {text!r}")
    try:
        return canonical_label(text, channels)
    except GrammarError as error:
        raise RecordFormatError(
            f"{what} {text!r} is not a structure: {error}"
        ) from error


def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecordFormatError(f"{what} must be an integer, got {value!r}")
    return value


def _float(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RecordFormatError(f"{what} must be a number, got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise RecordFormatError(f"{what} must be finite, got {value!r}")
    return out


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RecordFormatError(f"{what} must be an object, got {value!r}")
    return value


def _calls(record: SessionRecord) -> list[Mapping[str, Any]]:
    """Successful tool calls, in the layer's call order."""
    bodies = [
        _mapping(e.body, "a tool_call entry") for e in record.of_kind("tool_call")
    ]
    ok = [b for b in bodies if not b.get("is_error")]
    calls = [_int(b.get("call"), "a tool call's index") for b in ok]
    if len(set(calls)) != len(calls):
        raise RecordFormatError("a tool call index is recorded twice")
    return [b for _, b in sorted(zip(calls, ok, strict=True), key=lambda x: x[0])]


def _prediction(
    out: Mapping[str, Any], channels: tuple[ChannelSpec, ...], experiment: int
) -> tuple[int, PredictionRecord]:
    commitment = _int(out.get("commitment"), "a prediction's commitment")
    if _int(out.get("experiment"), "a prediction's experiment") != experiment:
        raise RecordFormatError(
            f"commitment {commitment} evaluated on experiment {experiment} "
            f"but committed for {out.get('experiment')!r}"
        )
    interval = out.get("interval")
    if not isinstance(interval, list) or len(interval) != 2:
        raise RecordFormatError(f"commitment {commitment}: interval {interval!r}")
    prediction = Prediction(
        experiment=experiment,
        statistic=str(out.get("statistic")),
        interval=(_float(interval[0], "interval"), _float(interval[1], "interval")),
        level=_float(out.get("level"), "level"),
        hypothesis=_label(out.get("hypothesis"), channels, "a prediction's hypothesis"),
    )
    value = out.get("value")
    evaluation = (
        None
        if value is None
        else Evaluation(commitment, _float(value, "value"), bool(out.get("covered")))
    )
    return commitment, (prediction, evaluation)


def _unevaluated(
    body: Mapping[str, Any], channels: tuple[ChannelSpec, ...]
) -> tuple[int, PredictionRecord]:
    """A commitment whose experiment never produced data: stated values only."""
    rec = _mapping(body.get("record"), "a predict record")
    args = _mapping(body.get("args"), "predict arguments")
    commitment = _int(rec.get("commitment"), "a prediction's commitment")
    interval = args.get("interval")
    if not isinstance(interval, list) or len(interval) != 2:
        raise RecordFormatError(f"commitment {commitment}: interval {interval!r}")
    statistic = canonical_json(
        {"diagnostic": args.get("diagnostic"), "args": args.get("args") or {}}
    )
    prediction = Prediction(
        experiment=_int(rec.get("experiment"), "a prediction's experiment"),
        statistic=statistic,
        interval=(_float(interval[0], "interval"), _float(interval[1], "interval")),
        level=_float(args.get("level"), "level"),
        hypothesis=_label(rec.get("hypothesis"), channels, "a prediction's hypothesis"),
    )
    return commitment, (prediction, None)


def _belief(
    rec: Mapping[str, Any], turn: int, channels: tuple[ChannelSpec, ...]
) -> Belief:
    stated = _int(rec.get("experiments_run"), "a belief's experiments_run")
    if stated != turn:
        raise RecordFormatError(
            f"belief {rec.get('belief')!r} records experiments_run={stated} but "
            f"{turn} experiments had run before it"
        )
    rivals = rec.get("rivals")
    if not isinstance(rivals, list):
        raise RecordFormatError(f"a belief's rivals must be a list, got {rivals!r}")
    try:
        return Belief(
            turn,
            _label(rec.get("favoured"), channels, "a belief's favoured"),
            tuple(_label(r, channels, "a belief's rival") for r in rivals),
            confidence=_float(rec.get("confidence"), "a belief's confidence"),
        )
    except ScoringError as error:
        raise RecordFormatError(f"belief {rec.get('belief')!r}: {error}") from error


def timeline(record: SessionRecord, channels: tuple[ChannelSpec, ...]) -> Timeline:
    """Read the §4.4 inputs from ``record`` (module docstring).

    ``channels`` are the environment's native channels. Raises
    :class:`RecordFormatError` for a record that does not follow the contract.
    """
    run = 0
    experiments: list[ExperimentRun] = []
    beliefs: list[Belief] = []
    committed: dict[int, PredictionRecord] = {}
    evaluated: dict[int, PredictionRecord] = {}
    submitted: str | None = None
    for body in _calls(record):
        name = body.get("name")
        if name == "run_experiment":
            rec = _mapping(body.get("record"), "a run_experiment record")
            index = _int(rec.get("experiment"), "an experiment index")
            if index != run:
                raise RecordFormatError(
                    f"experiment {index} recorded as the experiment numbered {run}"
                )
            experiments.append(ExperimentRun(index, run))
            for raw in rec.get("predictions") or []:
                commitment, pair = _prediction(
                    _mapping(raw, "an evaluated prediction"), channels, index
                )
                evaluated[commitment] = pair
            run += 1
        elif name == "belief":
            rec = _mapping(body.get("record"), "a belief record")
            beliefs.append(_belief(rec, run, channels))
        elif name == "predict":
            commitment, pair = _unevaluated(body, channels)
            committed[commitment] = pair
        elif name == "submit":
            rec = _mapping(body.get("record"), "a submit record")
            submitted = _label(
                rec.get("structure"), channels, "the submitted structure"
            )
            beliefs.append(Belief(run, submitted))
    for commitment, pair in evaluated.items():
        if commitment in committed and committed[commitment][0].hypothesis != (
            pair[0].hypothesis
        ):
            raise RecordFormatError(
                f"commitment {commitment}: the predict and run_experiment records "
                "name different hypotheses"
            )
    merged = {**committed, **evaluated}
    predictions = tuple(merged[c] for c in sorted(merged))
    return Timeline(predictions, tuple(experiments), tuple(beliefs), submitted)
