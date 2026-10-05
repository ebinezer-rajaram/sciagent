"""Design generators for ``run_experiment`` (P4 Δ3).

An agent's ``force_events`` call is mostly arithmetic: a regular grid of times
and a mark pattern. Writing a few hundred numbers by hand drifts (in the pilot,
134 of 155 rejected calls were arrays whose counts disagreed), so the design
may state the rule instead:

- ``times`` is an array, or ``{"start": s, "step": d, "n": n}`` for
  ``[s + k*d for k in range(n)]``, computed per k and never as a running sum,
  so the grid is exact and does not drift with n;
- a channel's marks are an array, or a pattern: ``{"constant": v}``,
  ``{"alternate": [a, b]}`` (a, b, a, b, ...) or ``{"repeat": [...]}`` (the
  block, repeated; its length must divide n). Every pattern expands to exactly
  one value per time.

:func:`expand` is a pure JSON-to-JSON transform. It turns a design into the
ordinary explicit-array experiment JSON that
:func:`~sciagent.glm.interventions.experiment_from_json` reads, so everything
downstream (validation, rescaling, the record, replay) sees only arrays, and a
generator design and the equivalent array design are the same experiment. It
does not validate what the existing parser validates (ranges, ordering, caps on
totals); it checks only what it must to build the arrays: the generator's
shape, ``n`` against :data:`~sciagent.glm.interventions.MAX_FORCED_EVENTS`
(before anything is built), and that every mark array has one value per time.
Errors are :class:`~sciagent.glm.interventions.InvalidInterventionError` that
name the JSON path of the fault.

Rescaling (SPEC §5) commutes with expansion: for a power of two c, expanding
and then multiplying by c is byte-identical to expanding with ``start`` and
``step`` multiplied by c, because ``c*(s + k*d) == c*s + k*(c*d)`` exactly in
floating point when c is a power of two (barring overflow and subnormals).

Domain-independent: nothing here knows channels or the simulator.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any, Final

from sciagent.glm.interventions import (
    EXPERIMENT_SCHEMA,
    MAX_FORCED_EVENTS,
    MAX_JSON_DEPTH,
    MAX_PARTS,
    InvalidInterventionError,
)

_GENERATOR_KEYS: Final = frozenset({"start", "step", "n"})
_PATTERNS: Final = ("constant", "alternate", "repeat")


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInterventionError(f"{path} must be a number, got {value!r}")
    try:
        v = float(value)
    except OverflowError:
        raise InvalidInterventionError(f"{path} is too large: {value!r}") from None
    if not math.isfinite(v):
        raise InvalidInterventionError(f"{path} must be finite, got {value!r}")
    return v


def _count(value: object, path: str) -> int:
    """``n``: a whole number in ``[1, MAX_FORCED_EVENTS]``."""
    whole = (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value == int(value)
    )
    if not whole or not isinstance(value, int | float):
        raise InvalidInterventionError(
            f"{path} must be a whole number of events, got {value!r}"
        )
    n = int(value)
    if n < 1:
        raise InvalidInterventionError(f"{path} must be at least 1, got {value!r}")
    if n > MAX_FORCED_EVENTS:
        raise InvalidInterventionError(
            f"{path} is {n}; at most {MAX_FORCED_EVENTS} forced events"
        )
    return n


def _expand_times(gen: Mapping[object, object], path: str) -> list[object]:
    keys = {k for k in gen if isinstance(k, str)}
    missing = sorted(_GENERATOR_KEYS - keys)
    extra = sorted(str(k) for k in gen if k not in _GENERATOR_KEYS)
    if missing or extra:
        raise InvalidInterventionError(
            f"{path}: a generator is {{'start', 'step', 'n'}}; "
            f"missing keys {missing}, unexpected keys {extra}"
        )
    n = _count(gen["n"], f"{path}.n")
    start = _number(gen["start"], f"{path}.start")
    step = _number(gen["step"], f"{path}.step")
    times: list[object] = []
    for k in range(n):
        t = start + k * step
        if not math.isfinite(t):
            raise InvalidInterventionError(
                f"{path}: start + k*step overflows at k={k} "
                f"(start={start!r}, step={step!r})"
            )
        times.append(t)
    return times


def _pattern_values(
    pattern: Mapping[object, object], n: int, path: str
) -> list[object]:
    keys = sorted(str(k) for k in pattern)
    if len(pattern) != 1 or keys[0] not in _PATTERNS:
        raise InvalidInterventionError(
            f"{path}: a mark pattern is exactly one of {list(_PATTERNS)}, "
            f"got keys {keys}"
        )
    kind = keys[0]
    arg = pattern[kind]
    if kind == "constant":
        _number(arg, f"{path}.constant")
        return [arg] * n
    if not isinstance(arg, list):
        raise InvalidInterventionError(f"{path}.{kind} must be a list, got {arg!r}")
    for i, v in enumerate(arg):
        _number(v, f"{path}.{kind}[{i}]")
    if kind == "alternate":
        if len(arg) != 2:
            raise InvalidInterventionError(
                f"{path}.alternate must be [a, b], got {len(arg)} values"
            )
        return [arg[k % 2] for k in range(n)]
    if not arg:
        raise InvalidInterventionError(f"{path}.repeat must not be empty")
    if n % len(arg) != 0:
        raise InvalidInterventionError(
            f"{path}.repeat has {len(arg)} values, which does not tile {n} times; "
            f"n must be a multiple of {len(arg)}"
        )
    return [arg[k % len(arg)] for k in range(n)]


def _expand_marks(marks: object, n: int, path: str) -> dict[object, object]:
    if not isinstance(marks, Mapping):
        raise InvalidInterventionError(
            f"{path}.marks must be an object mapping channel to values, got {marks!r}"
        )
    out: dict[object, object] = {}
    for name, spec in marks.items():
        where = f"{path}.marks[{name!r}]"
        if isinstance(spec, list):
            if len(spec) != n:
                raise InvalidInterventionError(
                    f"{where} has {len(spec)} values for {n} times"
                )
            out[name] = list(spec)
        elif isinstance(spec, Mapping):
            out[name] = _pattern_values(spec, n, where)
        else:
            raise InvalidInterventionError(
                f"{where} must be a list or a pattern object, got {spec!r}"
            )
    return out


def _expand_force_events(
    node: Mapping[object, object], path: str
) -> dict[object, object]:
    out = dict(node)
    if "times" not in node:
        return out  # the parser reports the missing key
    times = node["times"]
    if isinstance(times, Mapping):
        times = _expand_times(times, f"{path}.times")
    elif isinstance(times, list):
        times = list(times)
    else:
        raise InvalidInterventionError(
            f"{path}.times must be a list or a {{'start', 'step', 'n'}} generator, "
            f"got {times!r}"
        )
    out["times"] = times
    if "marks" in node and node["marks"] is not None:
        out["marks"] = _expand_marks(node["marks"], len(times), path)
    return out


def _expand_intervention(node: object, path: str, depth: int) -> dict[object, object]:
    if not isinstance(node, Mapping):
        raise InvalidInterventionError(f"{path} must be a JSON object, got {node!r}")
    kind = node.get("type")
    if not isinstance(kind, str):
        raise InvalidInterventionError(f"{path}: 'type' must be a string, got {kind!r}")
    if kind == "force_events":
        return _expand_force_events(node, path)
    if kind == "compose" and "parts" in node:
        parts = node["parts"]
        if not isinstance(parts, list):
            raise InvalidInterventionError(
                f"{path}.parts must be a list, got {parts!r}"
            )
        if depth >= MAX_JSON_DEPTH:
            raise InvalidInterventionError(
                f"{path}: composes nested deeper than {MAX_JSON_DEPTH}"
            )
        if len(parts) > MAX_PARTS:
            raise InvalidInterventionError(
                f"{path}.parts has {len(parts)} parts; at most {MAX_PARTS}"
            )
        out = dict(node)
        out["parts"] = [
            _expand_intervention(p, f"{path}.parts[{i}]", depth + 1)
            for i, p in enumerate(parts)
        ]
        return out
    return copy.deepcopy(dict(node))  # other types hold no generators


def expand(obj: object) -> dict[str, object]:
    """Expand generators in a design to explicit arrays (see the module docstring).

    ``obj`` is an experiment (``{"intervention": ..., "horizon": h}``) or a bare
    intervention; the result has the same shape, with every ``force_events``
    ``times`` an array and every mark an array of the same length. Arrays pass
    through unchanged. The input is not modified.
    """
    if not isinstance(obj, Mapping):
        raise InvalidInterventionError(f"design must be a JSON object, got {obj!r}")
    if "intervention" in obj:
        out = {str(k): copy.deepcopy(v) for k, v in obj.items()}
        out["intervention"] = _expand_intervention(
            obj["intervention"], "experiment.intervention", 0
        )
        return out
    if "type" in obj:
        return {
            str(k): v for k, v in _expand_intervention(obj, "intervention", 0).items()
        }
    raise InvalidInterventionError(
        "design must be an experiment with an 'intervention' key or an "
        f"intervention with a 'type' key, got keys {sorted(str(k) for k in obj)}"
    )


def _design_schema() -> dict[str, object]:
    schema: dict[str, Any] = copy.deepcopy(EXPERIMENT_SCHEMA)
    schema["title"] = "Design"
    defs: dict[str, Any] = schema["$defs"]
    number = {"type": "number"}
    force = defs["force_events"]["properties"]
    force["times"] = {
        "oneOf": [
            force["times"],
            {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "minimum": 0},
                    "step": {"type": "number"},
                    "n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_FORCED_EVENTS,
                    },
                },
                "required": ["start", "step", "n"],
                "additionalProperties": False,
                "description": "[start + k*step for k in range(n)]",
            },
        ],
        "description": "strictly increasing, ≤ horizon; an array or a generator",
    }
    values = force["marks"]["additionalProperties"]
    force["marks"]["additionalProperties"] = {
        "oneOf": [
            values,
            {
                "type": "object",
                "properties": {"constant": number},
                "required": ["constant"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "alternate": {
                        "type": "array",
                        "items": number,
                        "minItems": 2,
                        "maxItems": 2,
                    }
                },
                "required": ["alternate"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "repeat": {"type": "array", "items": number, "minItems": 1}
                },
                "required": ["repeat"],
                "additionalProperties": False,
            },
        ],
        "description": (
            "one value per time, or a pattern {constant: v}, {alternate: [a, b]} "
            "or {repeat: [...]} whose length divides n"
        ),
    }
    return schema


#: JSON Schema (2020-12) of the design :func:`expand` reads: the experiment
#: schema with ``force_events`` allowed a generator for ``times`` and patterns
#: for marks. Context checks (ordering, horizon, channels, caps) are not in it.
DESIGN_SCHEMA: Final[dict[str, object]] = _design_schema()
