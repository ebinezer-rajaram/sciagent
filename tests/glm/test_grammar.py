"""Tests for the validity rules of the v2 grammar (SPEC §2.1).

Only the rules added after the first contract are pinned here; the canonical
form and the other modules exercise the rest through their strategies.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from strategies import SIZE_SIGN, features

from sciagent.glm.grammar import (
    ALL,
    MAX_FEATURES,
    ChannelKind,
    ChannelSpec,
    Excite,
    Feature,
    Gate,
    InvalidStructureError,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    One,
    Product,
    Source,
    SourceKind,
    Structure,
    Trend,
    validate,
)

POSITIVE_SIGN = Source(SourceKind.POSITIVE, "sign")
NEGATIVE_SIGN = Source(SourceKind.NEGATIVE, "sign")


def _ok(feature: Excite | Product | Gate) -> None:
    validate(Structure((feature,)), SIZE_SIGN)


class TestMarkOfTheSourceChannel:
    """``Mark(c)`` on a source filtered by sign channel ``c`` is a constant.

    On the ``+`` source ``Mark(sign)`` is always +1, on ``-`` always -1, so the
    feature equals ``One`` up to the sign of θ. The grammar forbids it rather
    than carrying a redundant copy of every such feature.
    """

    @pytest.mark.parametrize("source", [POSITIVE_SIGN, NEGATIVE_SIGN])
    @pytest.mark.parametrize("kernel", list(KernelKind))
    def test_is_rejected_and_the_message_says_use_one(
        self, kernel: KernelKind, source: Source
    ) -> None:
        with pytest.raises(InvalidStructureError, match="One"):
            _ok(Excite(kernel, Mark("sign"), source))

    def test_is_rejected_inside_products_and_gates(self) -> None:
        bad = Excite(KernelKind.EXP, Mark("sign"), POSITIVE_SIGN)
        good = Excite(KernelKind.EXP, One(), ALL)
        with pytest.raises(InvalidStructureError):
            _ok(Product(good, bad))
        with pytest.raises(InvalidStructureError):
            _ok(Gate(bad, LastMarkAbove("size")))

    def test_the_mark_on_source_all_stays_legal(self) -> None:
        _ok(Excite(KernelKind.EXP, Mark("sign"), ALL))

    def test_a_mark_of_another_channel_on_a_signed_source_stays_legal(self) -> None:
        _ok(Excite(KernelKind.EXP, Mark("size"), POSITIVE_SIGN))

    def test_one_on_a_signed_source_stays_legal(self) -> None:
        _ok(Excite(KernelKind.EXP, One(), POSITIVE_SIGN))

    def test_two_sign_channels_only_the_matching_one_is_rejected(self) -> None:
        channels = (
            ChannelSpec("s1", ChannelKind.SIGN, 0.0, 1.0),
            ChannelSpec("s2", ChannelKind.SIGN, 0.0, 1.0),
        )
        validate(
            Structure(
                (Excite(KernelKind.EXP, Mark("s2"), Source(SourceKind.POSITIVE, "s1")),)
            ),
            channels,
        )
        with pytest.raises(InvalidStructureError):
            validate(
                Structure(
                    (
                        Excite(
                            KernelKind.EXP,
                            Mark("s1"),
                            Source(SourceKind.NEGATIVE, "s1"),
                        ),
                    )
                ),
                channels,
            )

    @given(features(SIZE_SIGN))
    def test_the_strategies_never_generate_it(self, feature: Feature) -> None:
        validate(Structure((feature,)), SIZE_SIGN)


class TestNullStructure:
    """The intercept-only model (SPEC §3's null) is a valid structure."""

    @pytest.mark.parametrize("link", list(Link))
    def test_zero_features_is_valid_for_every_link(self, link: Link) -> None:
        validate(Structure((), link), SIZE_SIGN)
        validate(Structure((), link), ())

    def test_the_default_structure_is_null_identity(self) -> None:
        validate(Structure(()), SIZE_SIGN)
        assert Structure(()).link is Link.IDENTITY

    def test_the_upper_bound_still_holds(self) -> None:
        validate(Structure((Trend(),) * MAX_FEATURES), SIZE_SIGN)
        with pytest.raises(InvalidStructureError, match=r"0\.\.4"):
            validate(Structure((Trend(),) * (MAX_FEATURES + 1)), SIZE_SIGN)
