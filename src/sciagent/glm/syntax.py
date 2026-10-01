"""The DSL's text form: what an agent writes and reads (SPEC §2.1).

::

    link=exp; Excite(ExpK, Mark(size), all)
      + Gate(Product(Periodic, Excite(PowerK, One, sign=+)), LastMarkAbove(size))

A structure is an optional ``link=<identity|exp|softplus>;`` prefix (default
``identity``) and either one or more features separated by ``+`` or the keyword
``null``, the intercept-only model with no features (``link=exp; null``).
Features are
``Excite(<kernel>, <mark>, <source>)``, ``Periodic``, ``Trend``,
``Product(<feature>, <feature>)`` and ``Gate(<feature>, <cond>)``. A mark is
``One`` or ``Mark|Pow|ExpOf|Above(<channel>)``; a source is ``all`` or
``<sign channel>=+`` / ``<sign channel>=-``; a condition is
``LastMarkAbove(<channel>)`` or ``PhaseWindow``. Whitespace is insignificant.

There are **no numeric literals**: any digit that starts a token is a syntax
error, so an agent cannot write a number (invariant 2). Channel names are
identifiers (letters, digits and underscores, not starting with a digit).

:func:`parse` is a hand-written recursive-descent parser. Ill-formed text raises
:class:`DslSyntaxError` carrying the offending position and what was expected;
well-formed text that is not a valid structure for the channels (unknown
channel, too deep, too many features) raises
:class:`~sciagent.glm.grammar.InvalidStructureError` from ``validate``.
``parse(render(s), channels) == s`` for every valid ``s``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sciagent.core.errors import GrammarError
from sciagent.glm.grammar import (
    ALL,
    Above,
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
    validate,
)

#: Deepest nesting the parser follows. The grammar's ``MAX_DEPTH`` is far
#: smaller; this only keeps hostile input from exhausting the Python stack.
MAX_NESTING: Final = 64

_IDENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_START: Final = re.compile(r"[+-]?\.?[0-9]")
_PUNCT: Final = "(),;=+-"

_NUMBERS_MESSAGE: Final = "the DSL has no numbers; the framework fits them"

_FEATURE_EXPECTED: Final = "a feature (Excite, Periodic, Trend, Product or Gate)"
_KERNELS: Final = {k.value: k for k in KernelKind}
_LINKS: Final = {link.value: link for link in Link}
_MARKS: Final[dict[str, Callable[[str], MarkFn]]] = {
    "Mark": Mark,
    "Pow": Pow,
    "ExpOf": ExpOf,
    "Above": Above,
}


class DslSyntaxError(GrammarError):
    """The text is not well-formed DSL.

    ``position`` is the 0-based character offset of the offending token (the
    length of the text when it ended too early); ``expected`` says what would
    have been accepted there.
    """

    def __init__(self, message: str, position: int, expected: str) -> None:
        super().__init__(f"{message} at position {position}")
        self.position = position
        self.expected = expected


@dataclass(frozen=True)
class _Token:
    kind: str  # "ident", "punct" or "end"
    text: str
    start: int

    def describe(self) -> str:
        return "end of input" if self.kind == "end" else repr(self.text)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _name(channel: str) -> str:
    if _IDENT.fullmatch(channel) is None:
        raise DslSyntaxError(
            f"channel name {channel!r} cannot be written in the DSL", 0, "an identifier"
        )
    return channel


def _render_mark(mark: MarkFn) -> str:
    match mark:
        case One():
            return "One"
        case Mark(channel=c):
            return f"Mark({_name(c)})"
        case Pow(channel=c):
            return f"Pow({_name(c)})"
        case ExpOf(channel=c):
            return f"ExpOf({_name(c)})"
        case Above(channel=c):
            return f"Above({_name(c)})"


def _render_source(source: Source) -> str:
    if source.kind is SourceKind.ALL:
        return "all"
    if source.channel is None:
        raise DslSyntaxError("a signed source needs a sign channel", 0, "a channel")
    return f"{_name(source.channel)}={source.kind.value}"


def _render_cond(cond: Cond) -> str:
    match cond:
        case LastMarkAbove(channel=c):
            return f"LastMarkAbove({_name(c)})"
        case PhaseWindow():
            return "PhaseWindow"


def _render_feature(feature: Feature) -> str:
    match feature:
        case Excite(kernel=kernel, mark=mark, source=source):
            return (
                f"Excite({kernel.value}, {_render_mark(mark)}, "
                f"{_render_source(source)})"
            )
        case Periodic():
            return "Periodic"
        case Trend():
            return "Trend"
        case Product(left=left, right=right):
            return f"Product({_render_feature(left)}, {_render_feature(right)})"
        case Gate(feature=inner, cond=cond):
            return f"Gate({_render_feature(inner)}, {_render_cond(cond)})"


def render(structure: Structure) -> str:
    """The DSL text of a structure, always with an explicit ``link=`` prefix."""
    body = " + ".join(_render_feature(f) for f in structure.features) or "null"
    return f"link={structure.link.value}; {body}"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


class _Parser:
    """Recursive descent with one token of lookahead, scanned on demand.

    Scanning lazily means the first fault in the text, by position, is the one
    reported, whether it is a numeric literal or a misplaced token.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0
        self._nesting = 0
        self._tok = self._scan()

    def _scan(self) -> _Token:
        text = self._text
        while self._pos < len(text) and text[self._pos].isspace():
            self._pos += 1
        start = self._pos
        if start >= len(text):
            return _Token("end", "", start)
        if _NUMBER_START.match(text, start):
            raise DslSyntaxError(_NUMBERS_MESSAGE, start, "no numeric literal")
        ident = _IDENT.match(text, start)
        if ident is not None:
            self._pos = ident.end()
            return _Token("ident", ident.group(), start)
        if text[start] in _PUNCT:
            self._pos = start + 1
            return _Token("punct", text[start], start)
        raise DslSyntaxError(
            f"unexpected character {text[start]!r}", start, "a valid token"
        )

    def _advance(self) -> _Token:
        tok = self._tok
        self._tok = self._scan()
        return tok

    def _fail(self, expected: str) -> DslSyntaxError:
        tok = self._tok
        return DslSyntaxError(
            f"expected {expected}, found {tok.describe()}", tok.start, expected
        )

    def _punct(self, char: str) -> None:
        if self._tok.kind != "punct" or self._tok.text != char:
            raise self._fail(repr(char))
        self._advance()

    def _is_punct(self, char: str) -> bool:
        return self._tok.kind == "punct" and self._tok.text == char

    def _ident(self, expected: str) -> str:
        if self._tok.kind != "ident":
            raise self._fail(expected)
        return self._advance().text

    def structure(self) -> Structure:
        link = Link.IDENTITY
        if self._tok.kind == "ident" and self._tok.text == "link":
            self._advance()
            self._punct("=")
            name = self._tok
            if name.kind != "ident" or name.text not in _LINKS:
                raise self._fail("a link ('identity', 'exp' or 'softplus')")
            link = _LINKS[self._advance().text]
            self._punct(";")
        if self._tok.kind == "ident" and self._tok.text == "null":
            self._advance()
            if self._tok.kind != "end":
                raise self._fail("end of input (null has no features)")
            return Structure((), link)
        features = [self.feature()]
        while self._is_punct("+"):
            self._advance()
            features.append(self.feature())
        if self._tok.kind != "end":
            raise self._fail("'+' or end of input")
        return Structure(tuple(features), link)

    def feature(self) -> Feature:
        self._nesting += 1
        if self._nesting > MAX_NESTING:
            raise DslSyntaxError(
                f"nesting deeper than {MAX_NESTING}",
                self._tok.start,
                "a shallower tree",
            )
        try:
            return self._feature()
        finally:
            self._nesting -= 1

    def _feature(self) -> Feature:
        tok = self._tok
        if tok.kind != "ident":
            raise self._fail(_FEATURE_EXPECTED)
        match tok.text:
            case "Periodic":
                self._advance()
                return Periodic()
            case "Trend":
                self._advance()
                return Trend()
            case "Excite":
                self._advance()
                self._punct("(")
                kernel = self._kernel()
                self._punct(",")
                mark = self._mark()
                self._punct(",")
                source = self._source()
                self._punct(")")
                return Excite(kernel, mark, source)
            case "Product":
                self._advance()
                self._punct("(")
                left = self.feature()
                self._punct(",")
                right = self.feature()
                self._punct(")")
                return Product(left, right)
            case "Gate":
                self._advance()
                self._punct("(")
                inner = self.feature()
                self._punct(",")
                cond = self._cond()
                self._punct(")")
                return Gate(inner, cond)
            case _:
                raise self._fail(_FEATURE_EXPECTED)

    def _kernel(self) -> KernelKind:
        tok = self._tok
        if tok.kind != "ident" or tok.text not in _KERNELS:
            raise self._fail("a kernel (ExpK, PowerK or GammaK)")
        return _KERNELS[self._advance().text]

    def _mark(self) -> MarkFn:
        tok = self._tok
        expected = "a mark function (One, Mark, Pow, ExpOf or Above)"
        if tok.kind != "ident":
            raise self._fail(expected)
        if tok.text == "One":
            self._advance()
            return One()
        if tok.text in _MARKS:
            self._advance()
            self._punct("(")
            channel = self._ident("a channel name")
            self._punct(")")
            return _MARKS[tok.text](channel)
        raise self._fail(expected)

    def _source(self) -> Source:
        tok = self._tok
        expected = "a source ('all' or '<sign channel>=+' / '<sign channel>=-')"
        if tok.kind != "ident":
            raise self._fail(expected)
        self._advance()
        if self._is_punct("="):
            self._advance()
            if self._is_punct("+"):
                self._advance()
                return Source(SourceKind.POSITIVE, tok.text)
            if self._is_punct("-"):
                self._advance()
                return Source(SourceKind.NEGATIVE, tok.text)
            raise self._fail("'+' or '-'")
        if tok.text == "all":
            return ALL
        raise DslSyntaxError(
            f"expected {expected}, found {tok.describe()}", tok.start, expected
        )

    def _cond(self) -> Cond:
        tok = self._tok
        expected = "a condition (LastMarkAbove or PhaseWindow)"
        if tok.kind != "ident":
            raise self._fail(expected)
        if tok.text == "PhaseWindow":
            self._advance()
            return PhaseWindow()
        if tok.text == "LastMarkAbove":
            self._advance()
            self._punct("(")
            channel = self._ident("a channel name")
            self._punct(")")
            return LastMarkAbove(channel)
        raise self._fail(expected)


def parse(text: str, channels: tuple[ChannelSpec, ...]) -> Structure:
    """Parse DSL text into a structure valid for ``channels``.

    Raises :class:`DslSyntaxError` for ill-formed text and
    :class:`~sciagent.glm.grammar.InvalidStructureError` for a well-formed
    structure that is not valid for ``channels``.
    """
    structure = _Parser(text).structure()
    validate(structure, channels)
    return structure
