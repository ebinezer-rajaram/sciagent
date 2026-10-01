"""Tests for the anonymiser (SPEC §5, Q2) and the leak test (SPEC §6.3 no. 7).

The anonymiser is an instrument: if it misses a name the named/anonymised gap
(Q2) is measured against a condition that is not anonymous, and if it is not
reversible the framework cannot read what the agent wrote. So the properties
here are the contract: the maps are bijective, the rewriters round-trip and
commute with the DSL's ``render``/``parse``, DSL production names are never
touched, and the leak scanner catches what it must and nothing it need not.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "glm"))
from strategies import MAGNITUDE_ONLY, SIZE_SIGN, structures

from sciagent.anonymise import (
    DEFAULT_FORBIDDEN,
    DEFAULT_TIME_FACTOR,
    DSL_RESERVED,
    AnonymisationError,
    FieldKind,
    Leak,
    LeakError,
    NameMap,
    anonymise_args,
    anonymise_channel_specs,
    anonymise_dataset,
    anonymise_event_log,
    anonymise_structure,
    anonymise_text,
    assert_no_leaks,
    deanonymise_args,
    deanonymise_dataset,
    deanonymise_event_log,
    deanonymise_structure,
    deanonymise_text,
    json_schema_paths,
    make_name_map,
    scan_for_leaks,
    scan_for_numeric_leaks,
    scan_json_for_leaks,
)
from sciagent.core.errors import SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.syntax import parse, render

DIAGNOSTICS = (
    "size_gap_correlation",
    "interevent_cv",
    "rate_autocorrelation",
    "burstiness",
)

ENV_FORBIDDEN = ("size", "sign", "arrival", "size_gap_correlation", *DEFAULT_FORBIDDEN)


def size_sign_map(factor: float = DEFAULT_TIME_FACTOR) -> NameMap:
    return make_name_map({"size": "m1", "sign": "m2"}, DIAGNOSTICS, factor)


# --------------------------------------------------------------------------
# NameMap
# --------------------------------------------------------------------------


def test_diagnostics_are_numbered_in_sorted_order() -> None:
    nm = size_sign_map()
    assert nm.diagnostic_forward("burstiness") == "d01"
    assert nm.diagnostic_forward("interevent_cv") == "d02"
    assert nm.diagnostic_forward("rate_autocorrelation") == "d03"
    assert nm.diagnostic_forward("size_gap_correlation") == "d04"


def test_diagnostic_numbering_ignores_input_order() -> None:
    a = make_name_map({"size": "m1"}, ["b", "a", "c"], 8.0)
    b = make_name_map({"size": "m1"}, ["c", "b", "a"], 8.0)
    assert a == b
    assert hash(a) == hash(b)


def test_diagnostic_width_grows_with_count() -> None:
    names = [f"diag_{i:03d}" for i in range(120)]
    nm = make_name_map({"x": "m1"}, names, 8.0)
    assert nm.diagnostic_forward("diag_000") == "d001"
    assert nm.diagnostic_forward("diag_119") == "d120"


def test_channel_sequence_is_numbered_in_given_order() -> None:
    nm = make_name_map(["size", "sign"], [], 8.0)
    assert nm.channel_forward("size") == "m1"
    assert nm.channel_forward("sign") == "m2"
    assert nm == make_name_map({"size": "m1", "sign": "m2"}, [], 8.0)


def test_magnitude_environment_maps_to_m1() -> None:
    nm = make_name_map(["magnitude"], [], 8.0)
    assert nm.channel_forward("magnitude") == "m1"


def test_unknown_names_raise() -> None:
    nm = size_sign_map()
    with pytest.raises(AnonymisationError):
        nm.channel_forward("magnitude")
    with pytest.raises(AnonymisationError):
        nm.channel_inverse("size")  # a native name is not an anonymised one
    with pytest.raises(AnonymisationError):
        nm.diagnostic_forward("nope")
    with pytest.raises(AnonymisationError):
        nm.diagnostic_inverse("d99")


_NAMES = st.from_regex(r"[a-z][a-z_]{2,9}", fullmatch=True).filter(
    lambda s: s not in DSL_RESERVED
)


@given(
    channels=st.lists(_NAMES, min_size=1, max_size=4, unique=True),
    diagnostics=st.lists(_NAMES, max_size=30, unique=True),
)
def test_name_map_is_bijective(channels: list[str], diagnostics: list[str]) -> None:
    if set(channels) & set(diagnostics):
        return
    nm = make_name_map(channels, diagnostics, 8.0)
    for c in channels:
        assert nm.channel_inverse(nm.channel_forward(c)) == c
    for d in diagnostics:
        assert nm.diagnostic_inverse(nm.diagnostic_forward(d)) == d
    assert len({nm.channel_forward(c) for c in channels}) == len(channels)
    assert len({nm.diagnostic_forward(d) for d in diagnostics}) == len(diagnostics)


def test_reserved_dsl_names_cannot_be_renamed_or_targeted() -> None:
    for word in ("Mark", "Excite", "null", "all", "link", "exp", "PhaseWindow"):
        with pytest.raises(AnonymisationError):
            make_name_map({word: "m1"}, [], 8.0)
        with pytest.raises(AnonymisationError):
            make_name_map({"size": word}, [], 8.0)
        with pytest.raises(AnonymisationError):
            make_name_map({"size": "m1"}, [word], 8.0)


def test_every_production_in_the_grammar_is_reserved() -> None:
    for word in [
        "Excite",
        "ExpK",
        "PowerK",
        "GammaK",
        "Mark",
        "Pow",
        "ExpOf",
        "Above",
        "One",
        "Periodic",
        "Trend",
        "Product",
        "Gate",
        "LastMarkAbove",
        "PhaseWindow",
        "null",
        "link",
        "all",
        "identity",
        "exp",
        "softplus",
    ]:
        assert word in DSL_RESERVED


def test_invalid_maps_are_rejected() -> None:
    with pytest.raises(AnonymisationError):
        make_name_map({"a": "m1", "b": "m1"}, [], 8.0)  # not injective
    with pytest.raises(AnonymisationError):
        make_name_map({"a": "9x"}, [], 8.0)  # not a DSL identifier
    with pytest.raises(AnonymisationError):
        make_name_map({"a": "m1"}, ["a"], 8.0)  # native name used twice
    with pytest.raises(AnonymisationError):
        make_name_map({"a": "d01"}, ["z"], 8.0)  # anonymised names collide
    with pytest.raises(AnonymisationError):
        make_name_map(["a", "a"], [], 8.0)
    with pytest.raises(AnonymisationError):
        make_name_map({"a": "m1"}, ["z", "z"], 8.0)


@pytest.mark.parametrize(
    "bad", [0.0, -2.0, 1.0, math.inf, math.nan, 3.0, 3.6931, 6.0, 0.75, 10.0]
)
def test_bad_time_factor_is_rejected(bad: float) -> None:
    with pytest.raises(AnonymisationError):
        make_name_map({"a": "m1"}, [], bad)


def test_default_time_factor_is_eight_and_a_power_of_two() -> None:
    assert DEFAULT_TIME_FACTOR == 8.0
    for ok in (0.25, 0.5, 2.0, 4.0, 8.0, 16.0, 1024.0):
        assert size_sign_map(ok).time_factor == ok


@given(st.floats(min_value=1e-300, max_value=1e300))
def test_power_of_two_rescaling_is_bit_exact(t: float) -> None:
    nm = size_sign_map()
    assert nm.time_inverse(nm.time_forward(t)) == t
    assert nm.rate_inverse(nm.rate_forward(t)) == t


def test_default_time_factor_hides_familiar_values() -> None:
    """c is not 1, 10, 24, 60, 365 and does not turn them into round numbers."""
    c = DEFAULT_TIME_FACTOR
    familiar = [1.0, 7.0, 10.0, 12.0, 24.0, 30.0, 60.0, 100.0, 365.0, 365.25]
    familiar += [1000.0, 1440.0, 3600.0, 86400.0, 0.01, 0.1]

    def near(a: float, b: float, tol: float = 0.02) -> bool:
        return abs(a - b) <= tol * abs(b)

    def round_number(x: float) -> bool:
        e = math.floor(math.log10(x))
        mantissa = x / 10.0**e
        return any(near(mantissa, m) for m in (1.0, 2.0, 2.5, 5.0, 10.0))

    for f in familiar:
        assert not near(c, f) and not near(1 / c, f), f
        assert not near(c * f, f), f
        assert not round_number(c * f), (f, c * f)
    for const in (math.e, math.pi, math.sqrt(2), 2.0, 4.0):
        assert not near(c, const, 0.05)
    assert not round_number(c) and not round_number(1 / c)


def test_time_and_rate_scaling() -> None:
    nm = size_sign_map(8.0)
    assert nm.time_forward(10.0) == 80.0
    assert nm.time_inverse(80.0) == 10.0
    assert nm.rate_forward(8.0) == 1.0
    assert nm.rate_inverse(1.0) == 8.0
    t = np.array([0.5, 1.5, 9.0])
    np.testing.assert_array_equal(nm.time_inverse(nm.time_forward(t)), t)
    # a rate times a duration (an expected count) is invariant
    assert nm.rate_forward(2.0) * nm.time_forward(5.0) == pytest.approx(10.0)


# --------------------------------------------------------------------------
# Free text
# --------------------------------------------------------------------------


def test_text_renames_channels_and_diagnostics() -> None:
    nm = size_sign_map()
    text = "size_gap_correlation of size and sign is 0.3; sign=+ sources"
    out = anonymise_text(text, nm)
    assert out == "d04 of m1 and m2 is 0.3; m2=+ sources"
    assert deanonymise_text(out, nm) == text


def test_text_is_word_boundary_safe() -> None:
    nm = size_sign_map()
    for untouched in ("sizes", "resize", "size2", "my_size", "1size", "signal", "Size"):
        assert anonymise_text(untouched, nm) == untouched
    # a diagnostic that contains a channel name is replaced whole, not in parts
    assert anonymise_text("size_gap_correlation", nm) == "d04"
    assert anonymise_text("(size)", nm) == "(m1)"
    assert anonymise_text("size,sign.", nm) == "m1,m2."


def test_text_never_touches_dsl_production_names() -> None:
    nm = size_sign_map()
    dsl = (
        "link=exp; Excite(ExpK, Mark(size), all) + Gate(Product(Periodic, "
        "Excite(PowerK, One, sign=+)), LastMarkAbove(size)) + Trend"
    )
    out = anonymise_text(dsl, nm)
    assert out == dsl.replace("size", "m1").replace("sign", "m2")
    for word in DSL_RESERVED:
        assert anonymise_text(word, nm) == word
        assert deanonymise_text(word, nm) == word


def test_text_swap_is_simultaneous() -> None:
    nm = make_name_map({"a": "b", "b": "a"}, [], 8.0)
    assert anonymise_text("a b", nm) == "b a"
    assert deanonymise_text("b a", nm) == "a b"


def test_text_already_containing_an_anonymised_name_is_refused() -> None:
    """Otherwise the inverse could not tell which ``m1`` was original."""
    nm = size_sign_map()
    with pytest.raises(AnonymisationError):
        anonymise_text("size and m1", nm)
    with pytest.raises(AnonymisationError):
        anonymise_text("d01 and burstiness", nm)


@given(
    st.lists(
        st.sampled_from(
            ["size", "sign", "size_gap_correlation", "burstiness", "the", "of", "x1"]
        )
        | st.sampled_from([" ", ", ", "=+", "(", ")", "\n"]),
        max_size=12,
    )
)
def test_text_round_trips(parts: list[str]) -> None:
    nm = size_sign_map()
    text = "".join(parts)
    assert deanonymise_text(anonymise_text(text, nm), nm) == text


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------


def _anon_specs(
    channels: tuple[ChannelSpec, ...], nm: NameMap
) -> tuple[ChannelSpec, ...]:
    return anonymise_channel_specs(channels, nm)


def test_channel_specs_are_renamed_only() -> None:
    nm = size_sign_map()
    anon = anonymise_channel_specs(SIZE_SIGN, nm)
    assert [s.name for s in anon] == ["m1", "m2"]
    for a, n in zip(anon, SIZE_SIGN, strict=True):
        assert (a.kind, a.location, a.scale) == (n.kind, n.location, n.scale)


@given(structures(SIZE_SIGN))
def test_structure_round_trip_size_sign(s: Structure) -> None:
    nm = size_sign_map()
    assert deanonymise_structure(anonymise_structure(s, nm), nm) == s


@given(structures(MAGNITUDE_ONLY))
def test_structure_round_trip_magnitude(s: Structure) -> None:
    nm = make_name_map(["magnitude"], [], 8.0)
    assert deanonymise_structure(anonymise_structure(s, nm), nm) == s


@given(structures(SIZE_SIGN))
def test_anonymisation_commutes_with_render_and_parse(s: Structure) -> None:
    nm = size_sign_map()
    anon = anonymise_structure(s, nm)
    assert render(anon) == anonymise_text(render(s), nm)
    specs = _anon_specs(SIZE_SIGN, nm)
    assert parse(render(anon), specs) == anon
    assert deanonymise_structure(parse(render(anon), specs), nm) == s
    # the agent->framework text direction agrees with the AST direction
    assert parse(deanonymise_text(render(anon), nm), SIZE_SIGN) == s


@given(structures(SIZE_SIGN))
def test_anonymised_rendering_has_no_native_channel_names(s: Structure) -> None:
    nm = size_sign_map()
    text = render(anonymise_structure(s, nm))
    assert scan_for_leaks(text, ("size", "sign")) == ()


def test_structure_with_unmapped_channel_raises() -> None:
    nm = make_name_map({"size": "m1"}, [], 8.0)
    s = parse("Excite(ExpK, Mark(size), sign=+)", SIZE_SIGN)
    with pytest.raises(AnonymisationError):
        anonymise_structure(s, nm)


def test_deanonymise_structure_rejects_native_names() -> None:
    nm = size_sign_map()
    s = parse("Excite(ExpK, Mark(size), all)", SIZE_SIGN)
    with pytest.raises(AnonymisationError):
        deanonymise_structure(s, nm)


# --------------------------------------------------------------------------
# JSON tool arguments and results
# --------------------------------------------------------------------------

PATHS: Mapping[str, FieldKind] = {
    "feature_set": FieldKind.TEXT,
    "channel": FieldKind.CHANNEL,
    "diagnostic": FieldKind.DIAGNOSTIC,
    "window": FieldKind.TIME,
    "rate": FieldKind.RATE,
    "marks": FieldKind.CHANNEL_KEYS,
    "stats": FieldKind.DIAGNOSTIC_KEYS,
    "events.*.t": FieldKind.TIME,
    "events.*.channel": FieldKind.CHANNEL,
    "note": FieldKind.TEXT,
}


def _payload() -> dict[str, Any]:
    return {
        "feature_set": "link=identity; Excite(ExpK, Mark(size), sign=+)",
        "channel": "size",
        "diagnostic": "burstiness",
        "window": [10.0, 20.0],
        "rate": 0.5,
        "marks": {"size": [1.0, 2.0], "sign": [1, -1]},
        "stats": {"burstiness": 0.2, "interevent_cv": 1.1},
        "events": [{"t": 1.0, "channel": "sign"}, {"t": 2.0, "channel": "size"}],
        "note": "size_gap_correlation is high",
        "n_events": 2,
        "optional": None,
    }


def test_args_forward() -> None:
    nm = size_sign_map(4.0)
    out = anonymise_args(_payload(), PATHS, nm)
    assert out["feature_set"] == "link=identity; Excite(ExpK, Mark(m1), m2=+)"
    assert out["channel"] == "m1"
    assert out["diagnostic"] == "d01"
    assert out["window"] == [40.0, 80.0]
    assert out["rate"] == pytest.approx(0.125)
    assert out["marks"] == {"m1": [1.0, 2.0], "m2": [1, -1]}
    assert out["stats"] == {"d01": 0.2, "d02": 1.1}
    assert out["events"] == [
        {"t": 4.0, "channel": "m2"},
        {"t": 8.0, "channel": "m1"},
    ]
    assert out["note"] == "d04 is high"
    assert out["n_events"] == 2 and out["optional"] is None  # unlisted: untouched


def test_args_round_trip() -> None:
    nm = size_sign_map()
    payload = _payload()
    back = deanonymise_args(anonymise_args(payload, PATHS, nm), PATHS, nm)
    assert back["window"] == pytest.approx(payload["window"], rel=1e-12)
    assert back["rate"] == pytest.approx(payload["rate"], rel=1e-12)
    assert [e["t"] for e in back["events"]] == pytest.approx([1.0, 2.0], rel=1e-12)
    exact = {k: v for k, v in payload.items() if k not in ("window", "rate", "events")}
    assert {k: back[k] for k in exact} == exact
    assert [e["channel"] for e in back["events"]] == ["sign", "size"]


def test_args_do_not_mutate_input_and_are_deterministic() -> None:
    nm = size_sign_map()
    payload = _payload()
    before = json.dumps(payload, sort_keys=True)
    a = anonymise_args(payload, PATHS, nm)
    b = anonymise_args(payload, PATHS, nm)
    assert json.dumps(payload, sort_keys=True) == before
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_args_missing_paths_are_skipped_and_wrong_types_raise() -> None:
    nm = size_sign_map()
    assert anonymise_args({}, PATHS, nm) == {}
    with pytest.raises(AnonymisationError):
        anonymise_args({"channel": 3}, PATHS, nm)
    with pytest.raises(AnonymisationError):
        anonymise_args({"window": "soon"}, PATHS, nm)
    with pytest.raises(AnonymisationError):
        anonymise_args({"window": True}, PATHS, nm)
    with pytest.raises(AnonymisationError):
        anonymise_args({"marks": ["size"]}, PATHS, nm)
    with pytest.raises(AnonymisationError):
        anonymise_args({"events": 3}, PATHS, nm)


def test_args_agent_direction_rejects_native_channel() -> None:
    nm = size_sign_map()
    with pytest.raises(AnonymisationError):
        deanonymise_args({"channel": "size"}, PATHS, nm)
    assert deanonymise_args({"channel": "m1"}, PATHS, nm)["channel"] == "size"


def test_args_time_none_and_nonfinite_pass_through() -> None:
    nm = size_sign_map()
    out = anonymise_args({"window": [None, math.inf]}, PATHS, nm)
    assert out["window"] == [None, math.inf]


# --------------------------------------------------------------------------
# Event logs and datasets
# --------------------------------------------------------------------------


def _log() -> EventLog:
    return EventLog.create(
        [0.5, 1.25, 3.0, 7.5],
        {"size": [1.0, 2.0, 1.5, 3.0], "sign": [1, -1, 1, 1]},
        horizon=10.0,
    )


def test_event_log_scaling_and_renaming() -> None:
    nm = size_sign_map(4.0)
    anon = anonymise_event_log(_log(), nm)
    np.testing.assert_allclose(anon.times, [2.0, 5.0, 12.0, 30.0])
    assert anon.horizon == pytest.approx(40.0)
    assert sorted(anon.marks) == ["m1", "m2"]
    np.testing.assert_array_equal(anon.marks["m1"], _log().marks["size"])
    np.testing.assert_array_equal(anon.marks["m2"], _log().marks["sign"])


def test_event_log_round_trip() -> None:
    nm = size_sign_map()
    log = _log()
    back = deanonymise_event_log(anonymise_event_log(log, nm), nm)
    np.testing.assert_allclose(back.times, log.times, rtol=1e-12)
    assert back.horizon == pytest.approx(log.horizon, rel=1e-12)
    assert sorted(back.marks) == ["sign", "size"]
    for name in log.marks:
        np.testing.assert_array_equal(back.marks[name], log.marks[name])


def test_event_log_unmapped_channel_raises() -> None:
    nm = make_name_map({"size": "m1"}, [], 8.0)
    with pytest.raises(AnonymisationError):
        anonymise_event_log(_log(), nm)


def test_dataset_scaling_round_trip() -> None:
    nm = size_sign_map(4.0)
    ds = Dataset.create(
        _log(),
        [True, True, False, True],
        ((1.0, 2.0), (5.0, 6.0)),
        "clamp size window",
    )
    anon = anonymise_dataset(ds, nm)
    assert anon.excluded == ((4.0, 8.0), (20.0, 24.0))
    assert anon.label == "clamp m1 window"
    np.testing.assert_array_equal(anon.endogenous, ds.endogenous)
    back = deanonymise_dataset(anon, nm)
    assert back.label == ds.label
    np.testing.assert_allclose(back.log.times, ds.log.times, rtol=1e-12)
    np.testing.assert_allclose(np.array(back.excluded), np.array(ds.excluded))
    np.testing.assert_array_equal(back.endogenous, ds.endogenous)


@given(st.lists(st.floats(min_value=1e-3, max_value=3.0), max_size=30))
def test_event_log_times_stay_increasing(gaps: list[float]) -> None:
    nm = make_name_map(["size"], [], DEFAULT_TIME_FACTOR)
    times = np.cumsum(gaps)
    log = EventLog.create(times, {"size": np.ones(len(gaps))}, horizon=100.0)
    anon = anonymise_event_log(log, nm)
    assert bool(np.all(np.diff(anon.times) > 0))
    assert anon.horizon == pytest.approx(100.0 * DEFAULT_TIME_FACTOR)
    back = deanonymise_event_log(anon, nm)
    np.testing.assert_allclose(back.times, log.times, rtol=1e-12)


# --------------------------------------------------------------------------
# Leak scan
# --------------------------------------------------------------------------


def hits(text: str, forbidden: tuple[str, ...] = ENV_FORBIDDEN) -> list[str]:
    return [leak.term for leak in scan_for_leaks(text, forbidden)]


@pytest.mark.parametrize(
    "text",
    [
        "This is an earthquake catalogue",
        "EARTHQUAKES cluster",
        "a seismic process; seismicity is high",
        "a Hawkes process",
        "ETAS-like behaviour",
        "the etas model",
        "aftershocks follow",
        "Omori's law",
        "magnitude of events",
        "Richter scale",
        "Gutenberg-Richter",
        "an epidemic spreads",
        "each neuron fires; neurons",
        "a spike train",
        "size_gap_correlation",
        "the size_gap_correlation statistic",
        "mySizeGap",
        "SIZE",
        "log_size",
        "size2",
        "Mark(size)",
        "size-gap correlation",
        "see:\nsize matters",
        "SeismicCatalog",
        "isEarthquake",
        "ARRIVAL times",
    ],
)
def test_leak_positives(text: str) -> None:
    assert scan_for_leaks(text, ENV_FORBIDDEN), text


@pytest.mark.parametrize(
    "text",
    [
        "resize the window",
        "metastasis",
        "planetas",
        "a sizeable effect",  # 'size' is short: only the whole word counts
        "designer",
        "Excite(ExpK, One, all) + Trend + Periodic",
        "link=softplus; null",
        "d01 d17 m1 m2 fit run_experiment",
        "the process has a branching ratio below one",
        "an intensity depends on past events and their marks",
        "",
    ],
)
def test_leak_negatives(text: str) -> None:
    assert not scan_for_leaks(text, ENV_FORBIDDEN), text


def test_leak_reports_term_match_position_and_context() -> None:
    text = "Intro text. An Earthquake happened here, and more text follows."
    (leak,) = scan_for_leaks(text, ("earthquake",), source="system_prompt")
    assert isinstance(leak, Leak)
    assert leak.term == "earthquake"
    assert leak.match == "Earthquake"
    assert text[leak.start : leak.end] == "Earthquake"
    assert "Earthquake" in leak.context and "Intro" in leak.context
    assert leak.source == "system_prompt"


def test_leak_scan_is_deterministic_and_ordered() -> None:
    text = "seismic then earthquake then seismic"
    a = scan_for_leaks(text, ("seismic", "earthquake"))
    b = scan_for_leaks(text, ("earthquake", "seismic"))
    assert a == b
    assert [x.start for x in a] == sorted(x.start for x in a)
    assert len(a) == 3


def test_leak_scan_overlapping_terms_report_each_term() -> None:
    found = {
        leak.term for leak in scan_for_leaks("size_gap_correlation", ENV_FORBIDDEN)
    }
    assert {"size", "size_gap_correlation"} <= found


def test_empty_forbidden_term_is_rejected() -> None:
    with pytest.raises(AnonymisationError):
        scan_for_leaks("text", ("",))


def test_default_forbidden_covers_the_spec_list() -> None:
    needed = [
        "earthquake",
        "seismic",
        "hawkes",
        "etas",
        "aftershock",
        "omori",
        "magnitude",
        "richter",
        "gutenberg",
        "epidemic",
        "neuron",
        "spike",
    ]
    for word in needed:
        assert word in DEFAULT_FORBIDDEN, word
    assert tuple(sorted(set(DEFAULT_FORBIDDEN))) == DEFAULT_FORBIDDEN


def test_json_schema_scan_catches_a_leak_in_a_description() -> None:
    schema = {
        "name": "fit",
        "description": "Fit a model to the event log.\nThe magnitude channel is m1.",
        "input_schema": {
            "type": "object",
            "properties": {"size_gap_correlation": {"type": "number"}},
        },
    }
    found = {leak.term for leak in scan_json_for_leaks(schema, ENV_FORBIDDEN)}
    assert "magnitude" in found  # inside a string, after a newline
    assert "size_gap_correlation" in found  # a dict key
    # the naive scan of json.dumps misses what follows an escaped newline
    naive = scan_for_leaks(json.dumps({"d": "x\nsize"}), ("size",))
    assert naive == ()
    assert scan_json_for_leaks({"d": "x\nsize"}, ("size",))


def test_neutral_schema_is_clean() -> None:
    schema = {
        "name": "run_experiment",
        "description": (
            "Run an experiment under an intervention and return a new event log. "
            "Each event has a time and marks m1, m2. Costs one experiment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"intervention": {"type": "string"}},
            "required": ["intervention"],
            "additionalProperties": False,
        },
        "budget": "E",
        "terminal": False,
    }
    assert scan_json_for_leaks(schema, ENV_FORBIDDEN) == ()
    assert scan_for_leaks(json.dumps(schema), ENV_FORBIDDEN) == ()


def test_assert_no_leaks_lists_every_hit_with_its_source() -> None:
    prompts = {
        "system": "You study an earthquake catalogue.",
        "tool:fit": "Fits a Hawkes model; also seismic.",
        "tool:ok": "Fits a model to events with marks m1.",
    }
    with pytest.raises(LeakError) as info:
        assert_no_leaks(prompts, ENV_FORBIDDEN)
    err = info.value
    assert isinstance(err, SciAgentError)
    assert {leak.term for leak in err.leaks} == {"earthquake", "hawkes", "seismic"}
    msg = str(err)
    assert "system" in msg and "tool:fit" in msg and "tool:ok" not in msg
    assert "earthquake" in msg.lower()


def test_assert_no_leaks_passes_on_clean_text_and_accepts_iterables() -> None:
    assert_no_leaks({"p": "Excite(ExpK, Mark(m1), all)"}, ENV_FORBIDDEN)
    assert_no_leaks(["nothing here", "or here"], ENV_FORBIDDEN)
    with pytest.raises(LeakError):
        assert_no_leaks(["fine", "an aftershock"], ENV_FORBIDDEN)


def test_anonymised_framework_text_has_no_leaks() -> None:
    nm = size_sign_map()
    native = (
        "size_gap_correlation of size and sign: fit Excite(ExpK, Mark(size), sign=+)"
    )
    assert scan_for_leaks(native, ENV_FORBIDDEN)
    assert_no_leaks([anonymise_text(native, nm)], ENV_FORBIDDEN)


def test_numeric_leaks() -> None:
    found = scan_for_numeric_leaks("period is 24.0 and 24 events; d24, m24", (24.0,))
    assert [leak.match for leak in found] == ["24.0", "24"]  # not d24 / m24
    assert scan_for_numeric_leaks("value 24.0000001", (24.0,), rel_tol=1e-6)
    assert not scan_for_numeric_leaks("value 24.1", (24.0,), rel_tol=1e-6)
    assert scan_for_numeric_leaks("rate 1e1 here", (10.0,))
    assert not scan_for_numeric_leaks("nothing 3.7", (24.0, 365.25))


def test_numeric_scan_of_json_leaves() -> None:
    obj = {"periods": [24.0, 88.8], "name": "x24"}
    found = scan_json_for_leaks(obj, (), numeric_constants=(24.0,))
    assert [leak.term for leak in found] == ["24.0"]
    assert not scan_json_for_leaks({"p": True}, (), numeric_constants=(1.0,))


def test_assert_no_leaks_with_numeric_constants() -> None:
    with pytest.raises(LeakError):
        assert_no_leaks(["period 365.25"], (), numeric_constants=(365.25,))
    assert_no_leaks(["period 1351.9"], (), numeric_constants=(365.25,))


# --------------------------------------------------------------------------
# The diagnostic catalogue (sciagent.diagnostics): names, schemas, covariance
# --------------------------------------------------------------------------


def _catalogue_log() -> EventLog:
    rng = np.random.default_rng(20260901)
    times = np.cumsum(rng.exponential(1.0, 400))
    return EventLog.create(
        times,
        {
            "size": rng.exponential(1.0, 400),
            "sign": rng.choice([-1.0, 1.0], 400),
        },
        horizon=float(times[-1]) + 1.0,
    )


def _catalogue_map() -> NameMap:
    return make_name_map(["size", "sign"], catalogue.names())


def test_catalogue_names_are_renamed_bijectively_and_leak_free() -> None:
    nm = _catalogue_map()
    names = catalogue.names()
    assert len(names) >= 20
    anon = [nm.diagnostic_forward(n) for n in names]
    assert sorted(anon) == [f"d{i:02d}" for i in range(1, len(names) + 1)]
    assert [nm.diagnostic_inverse(a) for a in anon] == list(names)
    # numbering follows the sorted native name
    assert nm.diagnostic_forward(sorted(names)[0]) == "d01"
    text = " ".join(names)
    assert anonymise_text(text, nm) == " ".join(anon)
    assert_no_leaks([" ".join(anon)], ENV_FORBIDDEN)


def test_catalogue_descriptions_and_names_carry_no_default_forbidden_term() -> None:
    """The catalogue's own text is domain-neutral (nothing to anonymise there)."""
    for name in catalogue.names():
        spec = catalogue.get(name)
        texts = {name: spec.description}
        for arg in spec.args:
            texts[f"{name}.{arg.name}"] = arg.description
        assert_no_leaks(texts, DEFAULT_FORBIDDEN)


def test_diagnostic_args_schemas_anonymise_to_the_schema_of_the_anonymised_log() -> (
    None
):
    """Anonymising a native ``args_schema`` equals building it on the anonymised
    log and channel set: channel enums renamed, absolute time bounds times c."""
    nm = _catalogue_map()
    log = _catalogue_log()
    anon_log = anonymise_event_log(log, nm)
    anon_channels = anonymise_channel_specs(SIZE_SIGN, nm)
    for name in catalogue.names():
        spec = catalogue.get(name)
        native = catalogue.args_schema(name, SIZE_SIGN, log)
        paths = json_schema_paths(
            channel_properties=[
                a.name for a in spec.args if a.kind is catalogue.ArgKind.CHANNEL
            ],
            time_properties=[
                a.name for a in spec.args if a.kind is catalogue.ArgKind.TIME
            ],
        )
        got = anonymise_args(native, paths, nm)
        want = catalogue.args_schema(name, anon_channels, anon_log)
        assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True), name
        assert_no_leaks({name: got}, ("size", "sign"))
        back = deanonymise_args(got, paths, nm)
        assert json.dumps(back, sort_keys=True) == json.dumps(native, sort_keys=True)


def test_diagnostic_args_round_trip_with_the_catalogue_rescaling() -> None:
    nm = _catalogue_map()
    log = _catalogue_log()
    tau = catalogue.mean_gap(log)
    native: dict[str, float | int | str] = {"channel": "size", "window": 20.0 * tau}
    paths = {"channel": FieldKind.CHANNEL, "window": FieldKind.TIME}
    anon = anonymise_args(native, paths, nm)
    assert anon == {"channel": "m1", "window": 20.0 * tau * nm.time_factor}
    assert (
        anon["window"]
        == catalogue.rescale_args("fano_factor", native, nm.time_factor)["window"]
    )
    assert deanonymise_args(anon, paths, nm) == native


def test_catalogue_values_transform_exactly_on_the_anonymised_log() -> None:
    """Every diagnostic on the anonymised log (explicit time args rescaled)
    equals its native value transformed by its declared covariance."""
    nm = _catalogue_map()
    log = _catalogue_log()
    anon_log = anonymise_event_log(log, nm)
    anon_channels = anonymise_channel_specs(SIZE_SIGN, nm)
    checked = 0
    for name in catalogue.names():
        spec = catalogue.get(name)
        args: dict[str, Any] = {
            a.name: "size" for a in spec.args if a.kind is catalogue.ArgKind.CHANNEL
        }
        if any(
            a.default is None and a.kind is not catalogue.ArgKind.CHANNEL
            for a in spec.args
        ):
            continue  # a required non-channel argument: no generic value to use
        anon_args = anonymise_args(
            catalogue.rescale_args(name, args, nm.time_factor),
            {k: FieldKind.CHANNEL for k in args},
            nm,
        )
        try:
            native_value = catalogue.compute(name, log, SIZE_SIGN, args)
        except catalogue.DiagnosticError:
            continue
        anon_value = catalogue.compute(name, anon_log, anon_channels, anon_args)
        want = catalogue.transform_value(name, native_value, nm.time_factor)
        assert anon_value == pytest.approx(want, rel=1e-9, abs=1e-12), name
        checked += 1
    assert checked >= 15
