"""The v2 intervention language and its execution on the GLM simulator (SPEC §4.0).

An agent's ``run_experiment`` call names an :class:`Experiment`: one
intervention and a horizon. Every experiment is a fresh run from empty history
on ``[0, horizon]``, drawn by :func:`~sciagent.glm.simulate.simulate_planned`.
The interventions, all on half-open windows ``[start, end)``:

- :class:`ForceEvents` inserts exogenous events at given times, with given
  marks or marks from the environment's sampler (any channel not given is
  sampled). They enter history, so they excite, but they are not endogenous:
  ``Dataset.endogenous`` is False for them. They stay visible even inside a
  censoring window, because the experimenter placed them.
- :class:`InjectMarks` is ``do(mark = value)`` on one channel for every event
  the process generates in the window, before the event enters history.
  Forced events keep their own marks.
- :class:`Censor` hides every generated event in the window from the returned
  log. The events still happen and still excite; only observation is off. The
  window goes into ``Dataset.excluded``.
- :class:`ClampRate` is ``do(λ = rate)`` on the window. Events generated there
  enter history, but they are not evidence about the model's λ, so they are not
  endogenous and the window goes into ``Dataset.excluded``.
- :class:`Compose` runs several at once; each part carries its own window or
  times, which is the schedule. Overlapping clamps, overlapping injections on
  one channel and a time forced twice conflict and are refused; everything
  else composes (a censored clamp, overlapping censors, injections on
  different channels). ``Compose(())`` is an unintervened run.

**Numbers.** Interventions are designs, so they contain numbers (times,
values, rates); invariant 2 covers scores, fitted parameters, posteriors and
held-out data, not designs. Every number is validated: finite, in range,
counts bounded. Constructors check what needs no context;
:func:`validate_experiment` checks the rest against the horizon, the channels
and the caps. Errors are :class:`InvalidInterventionError` with messages
written to be shown to the agent.

**JSON.** :func:`to_json` and :func:`from_json` are the wire form of the MCP
tool, described by :data:`INTERVENTION_SCHEMA` (and :data:`EXPERIMENT_SCHEMA`).
The form is canonical: a :class:`Compose` flattens nested composes and sorts
its parts by type and then canonical JSON text, so equal designs have equal JSON, and
``from_json(to_json(x)) == x``. :func:`from_json` additionally unwraps a
one-part compose and accepts integers for numbers.

**Time rescaling (SPEC §5).** The anonymised condition shows the agent times
multiplied by a fixed factor f. :func:`rescale`, :func:`rescale_experiment`
and :func:`rescale_dataset` apply it (times and windows * f, clamp rates ÷ f;
marks unchanged), and f must be a power of two so that rescaling by f and back
by 1/f is exact for every normal float: the agent's design maps to exactly
the native design the simulator runs. :func:`validate_experiment` takes the
same factor so caps and messages are in the agent's units.

Domain-independent: channels, the mark sampler and the truth come in as
arguments.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.grammar import ChannelKind, ChannelSpec, Structure
from sciagent.glm.simulate import (
    Coefficients,
    ForcedEvent,
    MarkOverride,
    MarkSampler,
    Plan,
    PsiAssignment,
    RateClamp,
    simulate_planned,
)

#: Default horizon. Times are in units where the nominal mean rate is 1
#: (``data.py``), so this makes an experiment about as long as the
#: 2,000-event observational log.
DEFAULT_HORIZON: Final = 2000.0
#: Longest experiment (native units): four observational logs.
MAX_HORIZON: Final = 8000.0
#: Largest clamp rate (native units: 50 * the nominal mean rate).
MAX_CLAMP_RATE: Final = 50.0
#: Cap on the expected number of clamp events, Σ rate * width (scale-free).
MAX_CLAMP_EVENTS: Final = 20_000.0
#: Cap on forced events in one experiment, over all its parts.
MAX_FORCED_EVENTS: Final = 500
#: Cap on the parts of a :class:`Compose`.
MAX_PARTS: Final = 16
#: Deepest nesting :func:`from_json` reads (nested composes are flattened).
MAX_JSON_DEPTH: Final = 4
#: Rescaling factors are 2**k with |k| ≤ this.
MAX_FACTOR_EXPONENT: Final = 30

type Window = tuple[float, float]


class InterventionError(SciAgentError):
    """Base for faults raised by the intervention layer."""


class InvalidInterventionError(InterventionError):
    """An intervention or experiment is malformed, out of range or conflicting."""


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def _number(value: object, what: str) -> float:
    """A finite real (int or float, never bool) as a float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInterventionError(f"{what} must be a number, got {value!r}")
    try:
        v = float(value)
    except OverflowError:
        raise InvalidInterventionError(f"{what} is too large: {value!r}") from None
    if not math.isfinite(v):
        raise InvalidInterventionError(f"{what} must be finite, got {value!r}")
    return v


def _sequence(value: object, what: str) -> Sequence[object]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise InvalidInterventionError(f"{what} must be a list, got {value!r}")
    return value


def _window(value: object, what: str) -> Window:
    seq = _sequence(value, what)
    if len(seq) != 2:
        raise InvalidInterventionError(f"{what} must be [start, end], got {value!r}")
    start = _number(seq[0], f"{what} start")
    end = _number(seq[1], f"{what} end")
    if start < 0.0:
        raise InvalidInterventionError(f"{what} start must be ≥ 0, got {start}")
    if not start < end:
        raise InvalidInterventionError(
            f"{what} must have start < end, got [{start}, {end}]"
        )
    return (start, end)


def _channel(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidInterventionError(f"{what} must be a channel name, got {value!r}")
    return value


def _overlap(a: Window, b: Window) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _fmt(w: Window) -> str:
    return f"[{w[0]}, {w[1]})"


# --------------------------------------------------------------------------
# The language
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ForceEvents:
    """Exogenous events at ``times`` (strictly increasing, ≥ 0).

    ``marks`` maps a channel to one value per time; channels not given are
    drawn from the environment's sampler. An empty mapping is stored as None.
    """

    times: tuple[float, ...]
    marks: Mapping[str, tuple[float, ...]] | None = None

    def __post_init__(self) -> None:
        seq = _sequence(self.times, "ForceEvents times")
        if not seq:
            raise InvalidInterventionError("ForceEvents needs at least one time")
        if len(seq) > MAX_FORCED_EVENTS:
            raise InvalidInterventionError(
                f"ForceEvents has {len(seq)} times; at most {MAX_FORCED_EVENTS}"
            )
        times = tuple(_number(t, "ForceEvents time") for t in seq)
        if times[0] < 0.0:
            raise InvalidInterventionError(
                f"ForceEvents times must be ≥ 0, got {times[0]}"
            )
        for a, b in pairwise(times):
            if not a < b:
                raise InvalidInterventionError(
                    f"ForceEvents times must be strictly increasing; {a} then {b}"
                )
        object.__setattr__(self, "times", times)
        marks: dict[str, tuple[float, ...]] | None = None
        if self.marks is not None:
            if not isinstance(self.marks, Mapping):
                raise InvalidInterventionError(
                    f"ForceEvents marks must map channel to values, got {self.marks!r}"
                )
            marks = {}
            for name in sorted(self.marks, key=str):
                channel = _channel(name, "ForceEvents marks key")
                values = _sequence(self.marks[name], f"ForceEvents marks[{channel!r}]")
                if len(values) != len(times):
                    raise InvalidInterventionError(
                        f"ForceEvents marks[{channel!r}] has {len(values)} values "
                        f"for {len(times)} times"
                    )
                marks[channel] = tuple(
                    _number(v, f"ForceEvents marks[{channel!r}] value") for v in values
                )
        object.__setattr__(self, "marks", marks or None)

    def __hash__(self) -> int:
        return hash(canonical_json(self))


@dataclass(frozen=True)
class InjectMarks:
    """``do(channel = value)`` for every generated event in ``window``."""

    window: Window
    channel: str
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "window", _window(self.window, "InjectMarks window"))
        object.__setattr__(
            self, "channel", _channel(self.channel, "InjectMarks channel")
        )
        object.__setattr__(self, "value", _number(self.value, "InjectMarks value"))


@dataclass(frozen=True)
class Censor:
    """Generated events in ``window`` happen and excite but are not observed."""

    window: Window

    def __post_init__(self) -> None:
        object.__setattr__(self, "window", _window(self.window, "Censor window"))


@dataclass(frozen=True)
class ClampRate:
    """``do(λ = rate)`` on ``window``; ``rate ≥ 0`` (0 silences the window)."""

    window: Window
    rate: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "window", _window(self.window, "ClampRate window"))
        rate = _number(self.rate, "ClampRate rate")
        if rate < 0.0:
            raise InvalidInterventionError(f"ClampRate rate must be ≥ 0, got {rate}")
        object.__setattr__(self, "rate", rate)


type Atom = ForceEvents | InjectMarks | Censor | ClampRate


@dataclass(frozen=True)
class Compose:
    """Several interventions in one run; ``()`` is an unintervened run.

    Nested composes are flattened and parts sorted canonically. A single part
    is refused (use the part itself), as are more than :data:`MAX_PARTS`
    parts and the conflicts listed in the module docstring.
    """

    parts: tuple[Intervention, ...]

    def __post_init__(self) -> None:
        flat: list[Atom] = []
        for part in _sequence(self.parts, "Compose parts"):
            if isinstance(part, Compose):
                flat.extend(_atoms(part))
            elif isinstance(part, ForceEvents | InjectMarks | Censor | ClampRate):
                flat.append(part)
            else:
                raise InvalidInterventionError(
                    f"Compose parts must be interventions, got {part!r}"
                )
        if len(flat) == 1:
            raise InvalidInterventionError(
                "a Compose needs zero or at least two parts; use the part itself"
            )
        if len(flat) > MAX_PARTS:
            raise InvalidInterventionError(
                f"Compose has {len(flat)} parts; at most {MAX_PARTS}"
            )
        _check_conflicts(flat)
        flat.sort(key=_order)
        object.__setattr__(self, "parts", tuple(flat))


type Intervention = ForceEvents | InjectMarks | Censor | ClampRate | Compose


def _atoms(intervention: Intervention) -> tuple[Atom, ...]:
    if isinstance(intervention, Compose):
        return tuple(p for p in intervention.parts if not isinstance(p, Compose))
    return (intervention,)


def atoms(intervention: Intervention) -> tuple[Atom, ...]:
    """The non-compose parts of an intervention, in canonical order."""
    return _atoms(intervention)


def _check_conflicts(parts: Sequence[Atom]) -> None:
    clamps = sorted(p.window for p in parts if isinstance(p, ClampRate))
    for a, b in pairwise(clamps):
        if _overlap(a, b):
            raise InvalidInterventionError(
                f"ClampRate windows {_fmt(a)} and {_fmt(b)} overlap; "
                "clamps in one experiment must not overlap"
            )
    injects = sorted((p.channel, p.window) for p in parts if isinstance(p, InjectMarks))
    for (ca, a), (cb, b) in pairwise(injects):
        if ca == cb and _overlap(a, b):
            raise InvalidInterventionError(
                f"InjectMarks windows {_fmt(a)} and {_fmt(b)} on channel {ca!r} "
                "overlap; injections on one channel must not overlap"
            )
    forced = sorted(t for p in parts if isinstance(p, ForceEvents) for t in p.times)
    if len(forced) > MAX_FORCED_EVENTS:
        raise InvalidInterventionError(
            f"{len(forced)} forced events in one experiment; at most "
            f"{MAX_FORCED_EVENTS}"
        )
    for t0, t1 in pairwise(forced):
        if t0 == t1:
            raise InvalidInterventionError(f"time {t0} is forced twice")


@dataclass(frozen=True)
class Experiment:
    """One run from empty history on ``[0, horizon]`` under ``intervention``."""

    intervention: Intervention
    horizon: float = DEFAULT_HORIZON

    def __post_init__(self) -> None:
        if not isinstance(
            self.intervention, ForceEvents | InjectMarks | Censor | ClampRate | Compose
        ):
            raise InvalidInterventionError(
                f"not an intervention: {self.intervention!r}"
            )
        horizon = _number(self.horizon, "horizon")
        if horizon <= 0.0:
            raise InvalidInterventionError(f"horizon must be > 0, got {horizon}")
        object.__setattr__(self, "horizon", horizon)


# --------------------------------------------------------------------------
# Context checks
# --------------------------------------------------------------------------


def _check_factor(factor: object) -> float:
    """A power of two 2**k, |k| ≤ MAX_FACTOR_EXPONENT, so rescaling is exact."""
    f = _number(factor, "time factor")
    mantissa, exponent = math.frexp(f)
    if mantissa != 0.5 or abs(exponent - 1) > MAX_FACTOR_EXPONENT:
        raise InvalidInterventionError(
            f"time factor must be a power of two within 2**±{MAX_FACTOR_EXPONENT}, "
            f"got {factor!r}"
        )
    return f


def _check_mark(spec: ChannelSpec, value: float, what: str) -> None:
    if spec.kind is ChannelKind.POSITIVE and not value > 0.0:
        raise InvalidInterventionError(
            f"{what}: channel {spec.name!r} takes values > 0, got {value}"
        )
    if spec.kind is ChannelKind.SIGN and value not in (-1.0, 1.0):
        raise InvalidInterventionError(
            f"{what}: channel {spec.name!r} takes values -1 or 1, got {value}"
        )


def validate_experiment(
    experiment: Experiment,
    channels: tuple[ChannelSpec, ...],
    *,
    time_factor: float = 1.0,
) -> None:
    """Check an experiment against its horizon, the channels and the caps.

    ``time_factor`` is the anonymisation factor when the experiment is in the
    agent's rescaled units: the horizon cap is then * f and the rate cap ÷ f,
    so a design passes here exactly when its native form passes with f = 1.
    """
    f = _check_factor(time_factor)
    by_name = {spec.name: spec for spec in channels}
    known = sorted(by_name)
    horizon = experiment.horizon
    if horizon > MAX_HORIZON * f:
        raise InvalidInterventionError(
            f"horizon {horizon} exceeds the maximum {MAX_HORIZON * f}"
        )

    def window(w: Window, what: str) -> None:
        if w[1] > horizon:
            raise InvalidInterventionError(
                f"{what} {_fmt(w)} ends after the horizon {horizon}"
            )

    def spec(name: str, what: str) -> ChannelSpec:
        if name not in by_name:
            raise InvalidInterventionError(
                f"{what}: unknown mark channel {name!r}; channels are {known}"
            )
        return by_name[name]

    clamp_events: list[float] = []
    for part in atoms(experiment.intervention):
        match part:
            case ForceEvents(times=times, marks=marks):
                if times[-1] > horizon:
                    raise InvalidInterventionError(
                        f"ForceEvents time {times[-1]} is after the horizon {horizon}"
                    )
                for name, values in sorted((marks or {}).items()):
                    s = spec(name, "ForceEvents marks")
                    for v in values:
                        _check_mark(s, v, "ForceEvents marks")
            case InjectMarks(window=w, channel=c, value=v):
                window(w, "InjectMarks window")
                _check_mark(spec(c, "InjectMarks"), v, "InjectMarks")
            case Censor(window=w):
                window(w, "Censor window")
            case ClampRate(window=w, rate=rate):
                window(w, "ClampRate window")
                if rate > MAX_CLAMP_RATE / f:
                    raise InvalidInterventionError(
                        f"ClampRate rate {rate} exceeds the maximum "
                        f"{MAX_CLAMP_RATE / f}"
                    )
                clamp_events.append(rate * (w[1] - w[0]))
    expected = math.fsum(clamp_events)
    if expected > MAX_CLAMP_EVENTS:
        raise InvalidInterventionError(
            f"clamps would generate about {expected:.0f} events (rate * width "
            f"summed); at most {MAX_CLAMP_EVENTS:.0f}"
        )


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def to_json(intervention: Intervention) -> dict[str, object]:
    """The canonical JSON object of an intervention (see the module docstring)."""
    match intervention:
        case ForceEvents(times=times, marks=marks):
            out: dict[str, object] = {"type": "force_events", "times": list(times)}
            if marks:
                out["marks"] = {k: list(marks[k]) for k in sorted(marks)}
            return out
        case InjectMarks(window=w, channel=c, value=v):
            return {"type": "inject_marks", "window": list(w), "channel": c, "value": v}
        case Censor(window=w):
            return {"type": "censor", "window": list(w)}
        case ClampRate(window=w, rate=rate):
            return {"type": "clamp_rate", "window": list(w), "rate": rate}
        case Compose(parts=parts):
            return {"type": "compose", "parts": [to_json(p) for p in parts]}


def _order(part: Atom) -> tuple[str, str]:
    """Canonical order of compose parts: by type, then by canonical text."""
    return (str(to_json(part)["type"]), canonical_json(part))


def canonical_json(intervention: Intervention) -> str:
    """Compact, key-sorted JSON text: equal designs give equal text."""
    return json.dumps(
        to_json(intervention), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


_KEYS: Final[dict[str, tuple[frozenset[str], frozenset[str]]]] = {
    "force_events": (frozenset({"type", "times"}), frozenset({"marks"})),
    "inject_marks": (frozenset({"type", "window", "channel", "value"}), frozenset()),
    "censor": (frozenset({"type", "window"}), frozenset()),
    "clamp_rate": (frozenset({"type", "window", "rate"}), frozenset()),
    "compose": (frozenset({"type", "parts"}), frozenset()),
}


def _object(obj: object, what: str) -> Mapping[str, object]:
    if not isinstance(obj, Mapping) or not all(isinstance(k, str) for k in obj):
        raise InvalidInterventionError(f"{what} must be a JSON object, got {obj!r}")
    return obj


def _numbers(obj: object, what: str) -> tuple[float, ...]:
    seq = _sequence(obj, what)
    if len(seq) > MAX_FORCED_EVENTS:
        raise InvalidInterventionError(
            f"{what} has {len(seq)} values; at most {MAX_FORCED_EVENTS}"
        )
    return tuple(_number(v, f"{what} value") for v in seq)


def _parse(obj: object, path: str, depth: int) -> Intervention:
    data = _object(obj, path)
    kind = data.get("type")
    if not isinstance(kind, str) or kind not in _KEYS:
        raise InvalidInterventionError(
            f"{path}: 'type' must be one of {sorted(_KEYS)}, got {kind!r}"
        )
    required, optional = _KEYS[kind]
    missing = sorted(required - set(data))
    extra = sorted(set(data) - required - optional)
    if missing or extra:
        raise InvalidInterventionError(
            f"{path} ({kind}): missing keys {missing}, unexpected keys {extra}"
        )
    try:
        match kind:
            case "force_events":
                times = _numbers(data["times"], "times")
                marks = data.get("marks")
                return ForceEvents(
                    times,
                    None
                    if marks is None
                    else {
                        k: _numbers(v, f"marks[{k!r}]")
                        for k, v in _object(marks, "marks").items()
                    },
                )
            case "inject_marks":
                return InjectMarks(
                    _window(data["window"], "window"),
                    _channel(data["channel"], "channel"),
                    _number(data["value"], "value"),
                )
            case "censor":
                return Censor(_window(data["window"], "window"))
            case "clamp_rate":
                return ClampRate(
                    _window(data["window"], "window"), _number(data["rate"], "rate")
                )
            case _:
                if depth >= MAX_JSON_DEPTH:
                    raise InvalidInterventionError(
                        f"composes nested deeper than {MAX_JSON_DEPTH}"
                    )
                raw = _sequence(data["parts"], "parts")
                if len(raw) > MAX_PARTS:
                    raise InvalidInterventionError(
                        f"{len(raw)} parts; at most {MAX_PARTS}"
                    )
                parts = tuple(
                    _parse(p, f"{path}.parts[{i}]", depth + 1)
                    for i, p in enumerate(raw)
                )
                flat = tuple(a for p in parts for a in _atoms(p))
                return flat[0] if len(flat) == 1 else Compose(flat)
    except InvalidInterventionError as err:
        if str(err).startswith(path):
            raise
        raise InvalidInterventionError(f"{path} ({kind}): {err}") from None


def from_json(obj: object) -> Intervention:
    """Parse the JSON form; raises :class:`InvalidInterventionError` with a path."""
    return _parse(obj, "intervention", 0)


def experiment_to_json(experiment: Experiment) -> dict[str, object]:
    return {
        "intervention": to_json(experiment.intervention),
        "horizon": experiment.horizon,
    }


def experiment_from_json(
    obj: object, *, default_horizon: float = DEFAULT_HORIZON
) -> Experiment:
    """Parse ``{"intervention": ..., "horizon": h}``; ``horizon`` is optional."""
    data = _object(obj, "experiment")
    extra = sorted(set(data) - {"intervention", "horizon"})
    if "intervention" not in data or extra:
        raise InvalidInterventionError(
            f"experiment needs key 'intervention' and optionally 'horizon'; "
            f"unexpected keys {extra}"
        )
    horizon = _number(data.get("horizon", default_horizon), "horizon")
    return Experiment(from_json(data["intervention"]), horizon)


def _schema_defs() -> dict[str, object]:
    def obj(kind: str, props: dict[str, object], required: list[str]) -> object:
        return {
            "type": "object",
            "properties": {"type": {"const": kind}, **props},
            "required": ["type", *required],
            "additionalProperties": False,
        }

    window = {
        "type": "array",
        "items": {"type": "number", "minimum": 0},
        "minItems": 2,
        "maxItems": 2,
        "description": "[start, end) with start < end ≤ horizon",
    }
    numbers = {"type": "array", "items": {"type": "number"}}
    return {
        "intervention": {
            "oneOf": [
                {"$ref": f"#/$defs/{k}"}
                for k in ("force_events", "inject_marks", "censor", "clamp_rate")
            ]
            + [{"$ref": "#/$defs/compose"}]
        },
        "force_events": obj(
            "force_events",
            {
                "times": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0},
                    "minItems": 1,
                    "maxItems": MAX_FORCED_EVENTS,
                    "description": "strictly increasing, ≤ horizon",
                },
                "marks": {
                    "type": "object",
                    "additionalProperties": numbers,
                    "description": "channel → one value per time; others sampled",
                },
            },
            ["times"],
        ),
        "inject_marks": obj(
            "inject_marks",
            {
                "window": window,
                "channel": {"type": "string"},
                "value": {"type": "number"},
            },
            ["window", "channel", "value"],
        ),
        "censor": obj("censor", {"window": window}, ["window"]),
        "clamp_rate": obj(
            "clamp_rate",
            {"window": window, "rate": {"type": "number", "minimum": 0}},
            ["window", "rate"],
        ),
        "compose": obj(
            "compose",
            {
                "parts": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/intervention"},
                    "maxItems": MAX_PARTS,
                }
            },
            ["parts"],
        ),
    }


_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"

#: JSON Schema (2020-12) of :func:`to_json`'s output and :func:`from_json`'s
#: input. Context checks (horizon, channels, caps, conflicts) are not in it.
INTERVENTION_SCHEMA: Final[dict[str, object]] = {
    "$schema": _SCHEMA_DIALECT,
    "title": "Intervention",
    "$defs": _schema_defs(),
    "$ref": "#/$defs/intervention",
}

#: JSON Schema (2020-12) of :func:`experiment_to_json` / :func:`experiment_from_json`.
EXPERIMENT_SCHEMA: Final[dict[str, object]] = {
    "$schema": _SCHEMA_DIALECT,
    "title": "Experiment",
    "$defs": _schema_defs(),
    "type": "object",
    "properties": {
        "intervention": {"$ref": "#/$defs/intervention"},
        "horizon": {"type": "number", "exclusiveMinimum": 0},
    },
    "required": ["intervention"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Time rescaling (SPEC §5)
# --------------------------------------------------------------------------


def _scale(w: Window, f: float) -> Window:
    return (w[0] * f, w[1] * f)


def rescale(intervention: Intervention, factor: float) -> Intervention:
    """Times and windows * factor, clamp rates ÷ factor (a power of two)."""
    f = _check_factor(factor)
    match intervention:
        case ForceEvents(times=times, marks=marks):
            return ForceEvents(tuple(t * f for t in times), marks)
        case InjectMarks(window=w, channel=c, value=v):
            return InjectMarks(_scale(w, f), c, v)
        case Censor(window=w):
            return Censor(_scale(w, f))
        case ClampRate(window=w, rate=rate):
            return ClampRate(_scale(w, f), rate / f)
        case Compose(parts=parts):
            return Compose(tuple(rescale(p, f) for p in parts))


def rescale_experiment(experiment: Experiment, factor: float) -> Experiment:
    """:func:`rescale` of the intervention, and the horizon * factor."""
    f = _check_factor(factor)
    return Experiment(rescale(experiment.intervention, f), experiment.horizon * f)


def rescale_dataset(dataset: Dataset, factor: float) -> Dataset:
    """Event times, horizon and excluded windows * factor; marks and masks kept."""
    f = _check_factor(factor)
    log = dataset.log
    scaled = EventLog.create(log.times * f, log.marks, log.horizon * f)
    excluded = tuple(_scale(w, f) for w in dataset.excluded)
    return Dataset.create(scaled, dataset.endogenous, excluded, dataset.label)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def to_plan(experiment: Experiment) -> Plan:
    """The simulator schedule of an experiment (censoring is applied later)."""
    parts = atoms(experiment.intervention)
    forced: list[ForcedEvent] = []
    for p in parts:
        if isinstance(p, ForceEvents):
            for i, t in enumerate(p.times):
                given = {k: v[i] for k, v in sorted((p.marks or {}).items())}
                forced.append(ForcedEvent(t, given))
    forced.sort(key=lambda e: e.time)
    clamps = sorted(
        (
            RateClamp(p.window[0], p.window[1], p.rate)
            for p in parts
            if isinstance(p, ClampRate)
        ),
        key=lambda c: c.start,
    )
    overrides = sorted(
        (
            MarkOverride(p.window[0], p.window[1], p.channel, p.value)
            for p in parts
            if isinstance(p, InjectMarks)
        ),
        key=lambda o: (o.start, o.channel),
    )
    return Plan(tuple(forced), tuple(clamps), tuple(overrides))


def _union(windows: Sequence[Window]) -> tuple[Window, ...]:
    """Sorted, merged (overlapping or touching) windows."""
    merged: list[Window] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def run_experiment(
    structure: Structure,
    psi: PsiAssignment,
    coef: Coefficients,
    channels: tuple[ChannelSpec, ...],
    marks: MarkSampler,
    experiment: Experiment,
    rng: np.random.Generator,
    *,
    max_events: int = 100_000,
    label: str = "experiment",
) -> Dataset:
    """Run an experiment on a truth; the observed :class:`Dataset`.

    ``log`` holds every observed event (forced ones included, censored ones
    not); ``endogenous`` is True exactly for events generated by the model's λ
    (not forced, not clamped); ``excluded`` is the union of censoring and clamp
    windows. Native units: validates with time factor 1. Deterministic given
    the generator's state (draw order in ``simulate.py``'s docstring).
    """
    validate_experiment(experiment, channels)
    run = simulate_planned(
        structure,
        psi,
        coef,
        channels,
        marks,
        experiment.horizon,
        rng,
        to_plan(experiment),
        max_events=max_events,
    )
    parts = atoms(experiment.intervention)
    times = run.log.times
    hidden = np.zeros(times.size, dtype=np.bool_)
    for p in parts:
        if isinstance(p, Censor):
            hidden |= (times >= p.window[0]) & (times < p.window[1])
    seen = ~hidden | run.forced
    log = EventLog.create(
        times[seen],
        {name: values[seen] for name, values in run.log.marks.items()},
        experiment.horizon,
    )
    endogenous = (~run.forced & ~run.clamped)[seen]
    excluded = _union([p.window for p in parts if isinstance(p, Censor | ClampRate)])
    return Dataset.create(log, endogenous, excluded, label)
