"""Tests for the DSL text syntax: render, parse, and the errors an agent sees.

The text form is the only way an agent writes a structure, so the parser is a
boundary: it must round-trip every valid structure, reject every numeric
literal (invariant 2: the agent writes no numbers), and say where and what
when the input is malformed.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from strategies import MAGNITUDE_ONLY, SIZE_SIGN, structures

from sciagent.core.errors import GrammarError
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    Gate,
    InvalidStructureError,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    One,
    Periodic,
    Product,
    Source,
    SourceKind,
    Structure,
    Trend,
)
from sciagent.glm.syntax import DslSyntaxError, parse, render

ANONYMISED: tuple[ChannelSpec, ...] = (
    ChannelSpec("m1", ChannelKind.POSITIVE, 1.0, 1.0),
    ChannelSpec("m2", ChannelKind.SIGN, 0.0, 1.0),
)

SPEC_EXAMPLE = (
    "link=exp; Excite(ExpK, Mark(size), all) + "
    "Gate(Product(Periodic, Excite(PowerK, One, sign=+)), LastMarkAbove(size))"
)
SPEC_STRUCTURE = Structure(
    (
        Excite(KernelKind.EXP, Mark("size"), ALL),
        Gate(
            Product(
                Periodic(),
                Excite(KernelKind.POWER, One(), Source(SourceKind.POSITIVE, "sign")),
            ),
            LastMarkAbove("size"),
        ),
    ),
    Link.EXP,
)


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


@given(structures(SIZE_SIGN))
def test_round_trip_size_sign(s: Structure) -> None:
    text = render(s)
    assert parse(text, SIZE_SIGN) == s
    assert render(parse(text, SIZE_SIGN)) == text


@given(structures(MAGNITUDE_ONLY))
def test_round_trip_real_channel_only(s: Structure) -> None:
    assert parse(render(s), MAGNITUDE_ONLY) == s


@given(structures(ANONYMISED))
def test_round_trip_anonymised_channel_names(s: Structure) -> None:
    assert parse(render(s), ANONYMISED) == s


def test_spec_example_parses_and_renders() -> None:
    assert parse(SPEC_EXAMPLE, SIZE_SIGN) == SPEC_STRUCTURE
    assert render(SPEC_STRUCTURE) == SPEC_EXAMPLE


def test_link_prefix_is_optional_and_defaults_to_identity() -> None:
    s = parse("Trend", SIZE_SIGN)
    assert s == Structure((Trend(),), Link.IDENTITY)
    assert parse("link=identity; Trend", SIZE_SIGN) == s
    assert parse("link=softplus; Trend", SIZE_SIGN).link is Link.SOFTPLUS


def test_negative_source_round_trips() -> None:
    s = Structure(
        (Excite(KernelKind.GAMMA, One(), Source(SourceKind.NEGATIVE, "sign")),)
    )
    assert "sign=-" in render(s)
    assert parse(render(s), SIZE_SIGN) == s


def test_whitespace_is_insignificant() -> None:
    spaced = "  link = exp ;\n Product(\n Trend ,\tPeriodic )  +\n Trend "
    tight = "link=exp;Product(Trend,Periodic)+Trend"
    assert parse(spaced, SIZE_SIGN) == parse(tight, SIZE_SIGN)


def test_render_rejects_channel_names_it_cannot_write() -> None:
    s = Structure((Excite(KernelKind.EXP, Mark("a b"), ALL),))
    with pytest.raises(DslSyntaxError):
        render(s)


# --------------------------------------------------------------------------
# Malformed input: the error says where and what was expected
# --------------------------------------------------------------------------

MALFORMED = [
    ("", 0, "feature"),
    ("Trend +", 7, "feature"),
    ("Trend Periodic", 6, "'+'"),
    ("Trend)", 5, "'+'"),
    ("Excite(ExpK, One)", 16, "','"),
    ("Excite(Gaussian, One, all)", 7, "ExpK"),
    ("Excite(ExpK, Mark, all)", 17, "'('"),
    ("Excite(ExpK, Wobble, all)", 13, "mark function"),
    ("Excite(ExpK, One, size)", 18, "source"),
    ("Excite(ExpK, One, sign=all)", 23, "'+' or '-'"),
    ("Excite(ExpK, One, all", 21, "')'"),
    ("Gate(Trend, Trend)", 12, "condition"),
    ("Product(Trend)", 13, "','"),
    ("Product(Trend, Periodic", 23, "')'"),
    ("Frobnicate", 0, "feature"),
    ("link=cubic; Trend", 5, "identity"),
    ("link=exp Trend", 9, "';'"),
    ("link=exp;", 9, "feature"),
    ("Trend $", 6, "unexpected character"),
    ("Trend ++ Trend", 7, "feature"),
]


@pytest.mark.parametrize(("text", "position", "expected"), MALFORMED)
def test_malformed_input_reports_position_and_expectation(
    text: str, position: int, expected: str
) -> None:
    with pytest.raises(DslSyntaxError) as info:
        parse(text, SIZE_SIGN)
    err = info.value
    assert err.position == position, str(err)
    assert expected in err.expected or expected in str(err), str(err)
    assert f"position {position}" in str(err)


def test_syntax_error_is_a_grammar_error() -> None:
    assert issubclass(DslSyntaxError, GrammarError)


def test_runaway_nesting_is_a_syntax_error_not_a_crash() -> None:
    with pytest.raises(DslSyntaxError):
        parse("Product(" * 5000 + "Trend", SIZE_SIGN)


# --------------------------------------------------------------------------
# No numeric literals, anywhere
# --------------------------------------------------------------------------

NUMERIC = [
    "Trend + 3",
    "Trend + 0.5",
    "Trend + .5",
    "Trend + 1e-3",
    "link=exp; Excite(ExpK, Mark(2), all)",
    "Excite(ExpK, One, sign=+1)",
    "Product(Trend, 2)",
    "Gate(Trend, LastMarkAbove(0.5))",
    "7",
    "Excite(ExpK, One, all) + -1",
]


@pytest.mark.parametrize("text", NUMERIC)
def test_numeric_literals_are_rejected(text: str) -> None:
    with pytest.raises(DslSyntaxError, match="no numbers"):
        parse(text, SIZE_SIGN)


def test_digits_inside_identifiers_are_fine() -> None:
    parsed = parse("Excite(ExpK, Mark(m1), all)", ANONYMISED)
    assert parsed.features[0] == Excite(KernelKind.EXP, Mark("m1"), ALL)


# --------------------------------------------------------------------------
# Well-formed text that is not a valid structure
# --------------------------------------------------------------------------

INVALID = [
    "Excite(ExpK, Mark(energy), all)",  # unknown channel
    "Excite(ExpK, Pow(sign), all)",  # Pow on a sign channel
    "Excite(ExpK, One, size=+)",  # signed source on a non-sign channel
    "Gate(Gate(Gate(Trend, PhaseWindow), PhaseWindow), PhaseWindow)",  # depth 4
    "Trend + Trend + Trend + Trend + Trend",  # five features
    "Gate(Trend, LastMarkAbove(sign))",  # condition on a sign channel
]


@pytest.mark.parametrize("text", INVALID)
def test_valid_syntax_but_invalid_structure_is_rejected_by_validate(text: str) -> None:
    with pytest.raises(InvalidStructureError):
        parse(text, SIZE_SIGN)


class TestNullStructure:
    """``null`` is the intercept-only model: no features."""

    @pytest.mark.parametrize("link", list(Link))
    def test_render_and_round_trip(self, link: Link) -> None:
        null = Structure((), link)
        assert render(null) == f"link={link.value}; null"
        assert parse(render(null), SIZE_SIGN) == null

    def test_link_prefix_is_optional(self) -> None:
        assert parse("null", SIZE_SIGN) == Structure((), Link.IDENTITY)
        assert parse("  null ", ()) == Structure((), Link.IDENTITY)

    @pytest.mark.parametrize(
        "text", ["null + Trend", "Trend + null", "null null", "link=exp;", "link=exp"]
    )
    def test_null_is_alone_and_a_structure_is_not_empty_text(self, text: str) -> None:
        with pytest.raises(DslSyntaxError):
            parse(text, SIZE_SIGN)

    def test_error_position_for_a_feature_after_null(self) -> None:
        with pytest.raises(DslSyntaxError) as info:
            parse("link=exp; null + Trend", SIZE_SIGN)
        assert info.value.position == len("link=exp; null ")
