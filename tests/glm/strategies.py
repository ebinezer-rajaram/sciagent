"""Hypothesis strategies for v2 grammar objects, shared by the glm test modules.

Everything generated is **valid** for the channel set it was built from:
``validate(s, channels)`` passes, depth is at most ``max_depth`` (default
:data:`MAX_DEPTH`), and a structure has 1..``MAX_FEATURES`` features. The
alphabets are deliberately small so that Hypothesis finds collisions
(repeated features, equal sub-trees) often; those are where canonicalisation
can go wrong.

Import as ``from strategies import features, structures`` (``tests/glm`` is on
``sys.path`` because it has no ``__init__.py``).
"""

from __future__ import annotations

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from sciagent.glm.grammar import (
    ALL,
    MAX_DEPTH,
    MAX_FEATURES,
    Above,
    ChannelKind,
    ChannelSpec,
    Cond,
    Excite,
    ExpOf,
    Feature,
    Gate,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    MarkFn,
    One,
    Periodic,
    PhaseWindow,
    Pow,
    Product,
    Source,
    SourceKind,
    Structure,
    Trend,
)

#: A channel set used by many tests: a positive size and a sign.
SIZE_SIGN: tuple[ChannelSpec, ...] = (
    ChannelSpec("size", ChannelKind.POSITIVE, 1.0, 1.0),
    ChannelSpec("sign", ChannelKind.SIGN, 0.0, 1.0),
)

#: A second set, with a real channel and no sign channel.
MAGNITUDE_ONLY: tuple[ChannelSpec, ...] = (
    ChannelSpec("magnitude", ChannelKind.REAL, 0.0, 1.0),
)


def mark_fns(channels: tuple[ChannelSpec, ...]) -> list[MarkFn]:
    """Every mark function valid on ``channels``, in a fixed order."""
    out: list[MarkFn] = [One()]
    for spec in channels:
        out.append(Mark(spec.name))
        if spec.kind is ChannelKind.POSITIVE:
            out.append(Pow(spec.name))
        if spec.kind is not ChannelKind.SIGN:
            out.append(ExpOf(spec.name))
            out.append(Above(spec.name))
    return out


def sources(channels: tuple[ChannelSpec, ...]) -> list[Source]:
    """``ALL`` plus ``+`` / ``-`` on every sign channel."""
    out = [ALL]
    for spec in channels:
        if spec.kind is ChannelKind.SIGN:
            out.append(Source(SourceKind.POSITIVE, spec.name))
            out.append(Source(SourceKind.NEGATIVE, spec.name))
    return out


def conds(channels: tuple[ChannelSpec, ...]) -> list[Cond]:
    """``PhaseWindow`` plus ``LastMarkAbove`` on every non-sign channel."""
    out: list[Cond] = [PhaseWindow()]
    for spec in channels:
        if spec.kind is not ChannelKind.SIGN:
            out.append(LastMarkAbove(spec.name))
    return out


def _not_constant_mark(feature: Excite) -> bool:
    """Drop ``Mark(c)`` on a source filtered by ``c``: validate forbids it."""
    return not (
        isinstance(feature.mark, Mark)
        and feature.source.kind is not SourceKind.ALL
        and feature.mark.channel == feature.source.channel
    )


def atoms(channels: tuple[ChannelSpec, ...]) -> SearchStrategy[Feature]:
    """Depth-1 features: ``Excite``, ``Periodic``, ``Trend``."""
    excite = st.builds(
        Excite,
        st.sampled_from(list(KernelKind)),
        st.sampled_from(mark_fns(channels)),
        st.sampled_from(sources(channels)),
    ).filter(_not_constant_mark)
    plain: SearchStrategy[Feature] = st.sampled_from([Periodic(), Trend()])
    return st.one_of(excite, plain)


def features(
    channels: tuple[ChannelSpec, ...], max_depth: int = MAX_DEPTH
) -> SearchStrategy[Feature]:
    """Valid feature trees of depth at most ``max_depth``."""
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    base = atoms(channels)
    if max_depth == 1:
        return base
    inner = features(channels, max_depth - 1)
    products: SearchStrategy[Feature] = st.builds(Product, inner, inner)
    gates: SearchStrategy[Feature] = st.builds(
        Gate, inner, st.sampled_from(conds(channels))
    )
    return st.one_of(base, products, gates)


@st.composite
def structures(
    draw: st.DrawFn,
    channels: tuple[ChannelSpec, ...],
    max_depth: int = MAX_DEPTH,
    max_features: int = MAX_FEATURES,
    min_features: int = 0,
) -> Structure:
    """Valid structures: any link, ``min_features``..``max_features`` features.

    Zero features is the null (intercept-only) model, which is valid.

    Each feature after the first is, half the time, a copy of an earlier one,
    so repeated features (the case de-duplication is about) are common.
    """
    n = draw(st.integers(min_value=min_features, max_value=max_features))
    chosen: list[Feature] = []
    for _ in range(n):
        reuse = draw(st.booleans())
        if chosen and reuse:
            chosen.append(draw(st.sampled_from(chosen)))
        else:
            chosen.append(draw(features(channels, max_depth)))
    link = draw(st.sampled_from(list(Link)))
    return Structure(tuple(chosen), link)
