"""Instrument tests for the design generators (P4 Δ3).

``expand`` turns a generator design (``times: {start, step, n}``, mark patterns)
into the explicit-array experiment JSON that the rest of the pipeline reads.
The expanded arrays are the experiment: they are what runs, what is recorded
and what replays, so they must be exact, bounded and canonical (SPEC §4.0).
"""

from __future__ import annotations

import copy
import importlib
import json
import math
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sciagent.glm import interventions as iv
from sciagent.glm.designs import DESIGN_SCHEMA, expand

_CAP = iv.MAX_FORCED_EVENTS
jsonschema = importlib.import_module("jsonschema")


def _text(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _design(times: object, marks: object | None = None) -> dict[str, Any]:
    part: dict[str, Any] = {"type": "force_events", "times": times}
    if marks is not None:
        part["marks"] = marks
    return {"intervention": part, "horizon": 100.0}


def _part(out: object) -> dict[str, Any]:
    assert isinstance(out, dict)
    part = out["intervention"]
    assert isinstance(part, dict)
    return part


# --------------------------------------------------------------------------
# Expansion is exact
# --------------------------------------------------------------------------


def test_times_expand_per_k_not_as_a_running_sum() -> None:
    out = expand(_design({"start": 0.1, "step": 0.1, "n": 50}))
    times = _part(out)["times"]
    assert times == [0.1 + k * 0.1 for k in range(50)]
    # A running sum drifts from start + k*step; the per-k form must not.
    running = [0.1]
    for _ in range(49):
        running.append(running[-1] + 0.1)
    assert running != times
    assert len(times) == 50


def test_times_integers_expand_exactly() -> None:
    out = expand(_design({"start": 5, "step": 2, "n": 4}))
    assert _part(out)["times"] == [5, 7, 9, 11]


def test_constant_alternate_and_repeat_patterns() -> None:
    out = expand(
        _design(
            {"start": 1.0, "step": 1.0, "n": 6},
            {
                "c": {"constant": 2.5},
                "a": {"alternate": [-1, 1]},
                "r": {"repeat": [1, 2, 3]},
            },
        )
    )
    assert _part(out)["marks"] == {
        "c": [2.5] * 6,
        "a": [-1, 1, -1, 1, -1, 1],
        "r": [1, 2, 3, 1, 2, 3],
    }


def test_alternate_with_odd_n_ends_on_the_first_value() -> None:
    out = expand(
        _design({"start": 0, "step": 1, "n": 5}, {"s": {"alternate": [1, -1]}})
    )
    assert _part(out)["marks"] == {"s": [1, -1, 1, -1, 1]}


def test_explicit_arrays_pass_through_unchanged() -> None:
    design = _design([0.5, 1.5, 4.0], {"x": [1, 2, 3]})
    assert expand(design) == design
    assert _text(expand(design)) == _text(design)


def test_explicit_times_with_a_pattern_use_the_array_length() -> None:
    out = expand(_design([0.5, 1.5, 4.0], {"x": {"constant": 7}}))
    assert _part(out)["marks"] == {"x": [7, 7, 7]}


def test_expand_does_not_mutate_its_input_and_is_idempotent() -> None:
    design = _design(
        {"start": 1.0, "step": 0.5, "n": 4}, {"x": {"alternate": [1.0, 2.0]}}
    )
    before = copy.deepcopy(design)
    once = expand(design)
    assert design == before
    assert expand(once) == once
    assert once is not design


def test_expand_walks_compose_parts_and_leaves_other_parts_alone() -> None:
    censor = {"type": "censor", "window": [10, 20]}
    design = {
        "intervention": {
            "type": "compose",
            "parts": [
                censor,
                {
                    "type": "force_events",
                    "times": {"start": 1, "step": 1, "n": 3},
                    "marks": {"x": {"constant": 1}},
                },
            ],
        },
        "horizon": 50,
    }
    out = expand(design)
    parts = _part(out)["parts"]
    assert parts[0] == censor
    assert parts[1]["times"] == [1, 2, 3]
    assert parts[1]["marks"] == {"x": [1, 1, 1]}
    assert out["horizon"] == 50


def test_expand_accepts_a_bare_intervention() -> None:
    out = expand({"type": "force_events", "times": {"start": 0, "step": 2, "n": 3}})
    assert out == {"type": "force_events", "times": [0, 2, 4]}


# --------------------------------------------------------------------------
# Bounds and refusals
# --------------------------------------------------------------------------


def test_n_at_the_cap_is_allowed_and_one_over_is_refused() -> None:
    out = expand(_design({"start": 0, "step": 1, "n": _CAP}))
    assert len(_part(out)["times"]) == _CAP
    with pytest.raises(iv.InvalidInterventionError, match=rf"at most {_CAP}"):
        expand(_design({"start": 0, "step": 1, "n": _CAP + 1}))


def test_a_huge_n_is_refused_before_anything_is_built() -> None:
    with pytest.raises(iv.InvalidInterventionError, match="at most"):
        expand(_design({"start": 0, "step": 1, "n": 10**12}))


@pytest.mark.parametrize("n", [0, -3, 2.5, True, "4", None])
def test_n_must_be_a_positive_integer(n: object) -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"times\.n"):
        expand(_design({"start": 0, "step": 1, "n": n}))


@pytest.mark.parametrize(
    "gen",
    [
        {"start": 0, "step": 1},
        {"start": 0, "n": 3},
        {"step": 1, "n": 3},
        {"start": 0, "step": 1, "n": 3, "stop": 9},
    ],
)
def test_generator_keys_are_exact(gen: dict[str, object]) -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"times"):
        expand(_design(gen))


@pytest.mark.parametrize("bad", [True, "1", None, math.inf, math.nan, [1]])
def test_start_and_step_must_be_finite_numbers(bad: object) -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"times\.start"):
        expand(_design({"start": bad, "step": 1, "n": 3}))
    with pytest.raises(iv.InvalidInterventionError, match=r"times\.step"):
        expand(_design({"start": 0, "step": bad, "n": 3}))


def test_a_generated_time_that_overflows_is_refused() -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"times"):
        expand(_design({"start": 0, "step": 1e308, "n": 3}))


def test_pattern_length_mismatch_names_the_path() -> None:
    with pytest.raises(iv.InvalidInterventionError) as err:
        expand(
            _design(
                {"start": 0, "step": 1, "n": 5},
                {"x": {"repeat": [1, 2]}},
            )
        )
    message = str(err.value)
    assert "experiment.intervention.marks['x']" in message
    assert "5" in message and "2" in message


def test_explicit_mark_array_length_mismatch_names_the_path() -> None:
    with pytest.raises(iv.InvalidInterventionError) as err:
        expand(_design({"start": 0, "step": 1, "n": 4}, {"x": [1, 2, 3]}))
    assert "experiment.intervention.marks['x']" in str(err.value)
    assert "3 values for 4 times" in str(err.value)
    with pytest.raises(iv.InvalidInterventionError, match=r"marks\['y'\]"):
        expand(_design([1.0, 2.0], {"x": [1, 2], "y": [1]}))


def test_the_path_inside_a_compose_names_the_part() -> None:
    design = {
        "intervention": {
            "type": "compose",
            "parts": [
                {"type": "censor", "window": [1, 2]},
                {
                    "type": "force_events",
                    "times": {"start": 5, "step": 1, "n": 3},
                    "marks": {"x": {"repeat": [1, 2]}},
                },
            ],
        }
    }
    with pytest.raises(iv.InvalidInterventionError) as err:
        expand(design)
    assert "experiment.intervention.parts[1].marks['x']" in str(err.value)


def test_the_path_of_a_bare_intervention_starts_at_intervention() -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"^intervention\.times"):
        expand({"type": "force_events", "times": {"start": 0, "step": 1, "n": 0}})


@pytest.mark.parametrize(
    "pattern",
    [
        {"constant": 1, "alternate": [1, 2]},
        {"alternate": [1, 2, 3]},
        {"alternate": [1]},
        {"repeat": []},
        {"repeat": "ab"},
        {"constant": [1]},
        {"constant": True},
        {"constant": math.inf},
        {"alternate": [1, "a"]},
        {"cycle": [1, 2]},
        {},
        "constant",
        3,
    ],
)
def test_malformed_mark_patterns_are_refused_with_a_path(pattern: object) -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"marks\['x'\]"):
        expand(_design({"start": 0, "step": 1, "n": 4}, {"x": pattern}))


def test_a_repeat_that_does_not_tile_n_is_refused() -> None:
    with pytest.raises(iv.InvalidInterventionError, match="multiple"):
        expand(_design({"start": 0, "step": 1, "n": 5}, {"x": {"repeat": [1, 2]}}))


def test_structure_errors_are_typed_not_raw() -> None:
    bads: list[object] = [None, 3, [], "x", {"horizon": 5}, {"intervention": 3}]
    for bad in [*bads, {"type": 4}]:
        with pytest.raises(iv.InvalidInterventionError):
            expand(bad)


def test_marks_must_be_an_object() -> None:
    with pytest.raises(iv.InvalidInterventionError, match=r"intervention\.marks"):
        expand(_design({"start": 0, "step": 1, "n": 2}, [1, 2]))


# --------------------------------------------------------------------------
# The result is an ordinary experiment, recorded as explicit arrays
# --------------------------------------------------------------------------


def test_the_expansion_parses_and_round_trips_canonically() -> None:
    design = _design(
        {"start": 0.25, "step": 0.75, "n": 8},
        {"b": {"alternate": [1.0, -1.0]}, "a": {"constant": 3}},
    )
    out = expand(design)
    experiment = iv.experiment_from_json(out)
    recorded = iv.experiment_to_json(experiment)
    # Explicit arrays, no generator remains.
    part = recorded["intervention"]
    assert isinstance(part, dict)
    assert part["times"] == [0.25 + k * 0.75 for k in range(8)]
    assert part["marks"] == {"a": [3.0] * 8, "b": [1.0, -1.0] * 4}
    assert iv.experiment_from_json(recorded) == experiment
    # Canonical: recording the recording changes nothing, byte for byte.
    assert _text(iv.experiment_to_json(iv.experiment_from_json(recorded))) == _text(
        recorded
    )


def test_generator_and_explicit_designs_give_the_same_experiment() -> None:
    generated = _design(
        {"start": 2, "step": 3, "n": 4},
        {"x": {"alternate": [1, 2]}, "y": {"constant": 0}},
    )
    explicit = _design([2, 5, 8, 11], {"x": [1, 2, 1, 2], "y": [0, 0, 0, 0]})
    a = iv.experiment_from_json(expand(generated))
    b = iv.experiment_from_json(expand(explicit))
    assert a == b
    assert _text(iv.experiment_to_json(a)) == _text(iv.experiment_to_json(b))


def test_a_non_increasing_generator_is_refused_by_the_existing_parser() -> None:
    out = expand(_design({"start": 1, "step": 0, "n": 3}))
    with pytest.raises(iv.InvalidInterventionError, match="strictly increasing"):
        iv.experiment_from_json(out)


# --------------------------------------------------------------------------
# DESIGN_SCHEMA
# --------------------------------------------------------------------------


def test_schema_is_the_experiment_schema_with_generators_allowed() -> None:
    validator = jsonschema.Draft202012Validator(DESIGN_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(DESIGN_SCHEMA)
    gen = _design(
        {"start": 0.5, "step": 1, "n": 10},
        {
            "a": {"constant": 1},
            "b": {"alternate": [1, -1]},
            "c": {"repeat": [1, 2, 3]},
            "d": [1, 2, 3],
        },
    )
    assert not list(validator.iter_errors(gen))
    explicit = _design([1, 2, 3], {"a": [1, 2, 3]})
    assert not list(validator.iter_errors(explicit))
    compose = {
        "intervention": {
            "type": "compose",
            "parts": [
                {"type": "censor", "window": [1, 2]},
                {"type": "force_events", "times": {"start": 5, "step": 1, "n": 3}},
            ],
        }
    }
    assert not list(validator.iter_errors(compose))
    # The plain experiment schema is not widened: it refuses a generator.
    plain = jsonschema.Draft202012Validator(iv.EXPERIMENT_SCHEMA)
    assert list(plain.iter_errors(gen))


@pytest.mark.parametrize(
    "bad",
    [
        _design({"start": 0, "step": 1}),
        _design({"start": 0, "step": 1, "n": 0}),
        _design({"start": 0, "step": 1, "n": _CAP + 1}),
        _design({"start": 0, "step": 1, "n": 2.5}),
        _design({"start": 0, "step": 1, "n": 3, "extra": 1}),
        _design([1, 2], {"a": {"alternate": [1]}}),
        _design([1, 2], {"a": {"repeat": []}}),
        _design([1, 2], {"a": {"constant": 1, "alternate": [1, 2]}}),
        _design([1, 2], {"a": {"cycle": [1]}}),
    ],
)
def test_schema_refuses_malformed_generators(bad: dict[str, Any]) -> None:
    assert list(jsonschema.Draft202012Validator(DESIGN_SCHEMA).iter_errors(bad))


def test_schema_does_not_alias_the_experiment_schema() -> None:
    assert DESIGN_SCHEMA is not iv.EXPERIMENT_SCHEMA
    assert DESIGN_SCHEMA["$defs"] is not iv.EXPERIMENT_SCHEMA["$defs"]
    # Building the design schema left the experiment schema's force_events
    # arrays-only.
    defs = iv.EXPERIMENT_SCHEMA["$defs"]
    assert isinstance(defs, dict)
    times = defs["force_events"]["properties"]["times"]
    assert times["type"] == "array"


# --------------------------------------------------------------------------
# Rescaling commutes with expansion (property)
# --------------------------------------------------------------------------

# Powers of two within the range where start, step and their products stay
# normal floats for the magnitudes below (no overflow, no subnormals).
_powers = st.integers(min_value=-20, max_value=20).map(lambda k: 2.0**k)
_starts = st.one_of(
    st.just(0.0), st.floats(min_value=1e-3, max_value=1e6, allow_nan=False)
)
_steps = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False)
_counts = st.integers(min_value=1, max_value=_CAP)


@settings(max_examples=200)
@given(_starts, _steps, _counts, _powers)
def test_expanding_then_scaling_equals_scaling_then_expanding(
    start: float, step: float, n: int, c: float
) -> None:
    unit = _part(expand(_design({"start": start, "step": step, "n": n})))["times"]
    native = _part(expand(_design({"start": start * c, "step": step * c, "n": n})))[
        "times"
    ]
    scaled = [t * c for t in unit]
    assert _text(scaled) == _text(native)


@settings(max_examples=100)
@given(_starts, _steps, st.integers(min_value=1, max_value=60), _powers)
def test_expansion_commutes_with_the_rescale_of_the_parsed_design(
    start: float, step: float, n: int, c: float
) -> None:
    marks = {"x": {"alternate": [1.0, 2.0]}}
    agent = iv.experiment_from_json(
        expand(_design({"start": start, "step": step, "n": n}, marks))
    )
    native = iv.experiment_from_json(
        expand(_design({"start": start * c, "step": step * c, "n": n}, marks))
    )
    scaled = iv.rescale(agent.intervention, c)
    assert _text(iv.to_json(scaled)) == _text(iv.to_json(native.intervention))
