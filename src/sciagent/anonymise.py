"""The anonymiser and the leak test (SPEC §5, Q2; instrument test §6.3 no. 7).

The anonymised condition rewrites, **consistently and reversibly**, every name
the agent can see: channel names (``size`` → ``m1``), diagnostic names
(``size_gap_correlation`` → ``d04``) and the time axis (times shown to the agent
are native times ``c``). DSL production names (``Excite``, ``Mark``, ``null``, link
and kernel names...) are never touched: they describe mathematics, not a
domain. Tool names (``fit``, ``run_experiment``) are domain-neutral and are not
renamed; their *descriptions* are the environment's to write neutrally, and
:func:`scan_json_for_leaks` / :func:`assert_no_leaks` are how that is checked.

This module is domain-independent. The environment supplies the channel
renames, the diagnostic names and its forbidden-term list; nothing here knows
what a channel means.

Direction conventions. *Forward* (native → agent-visible) is applied to
everything the framework shows the agent: tool results, data, prompts.
*Inverse* (agent-visible → native) is applied to everything the agent sends:
tool arguments, DSL text. The framework itself always works in native names and
units, so the forward view is derived and never fed back. The time factor is a
power of two, so rescaling is bit-exact and ``t * c / c == t``.

Time. Times are multiplied by ``c`` (:data:`DEFAULT_TIME_FACTOR` = 8, and any
factor must be an exact power of two) and rates are divided by it, so expected
counts (rate times duration) are unchanged. Marks are not rescaled. Numbers
embedded in free text cannot be rescaled reliably: framework text must carry
times through structured fields (``FieldKind.TIME``) or format them from
already-scaled values, and :func:`scan_for_numeric_leaks` is the backstop that
finds a native constant left in a rendered prompt.

Leak scan. :func:`scan_for_leaks` matches forbidden terms case-insensitively,
anchored at the start of a word segment (after a non-letter or at a camelCase
boundary), so ``size_gap_correlation``, ``mySizeGap`` and ``log_size`` hit
``size`` while ``resize`` does not. Terms of six or more letters also match as
a stem (``seismic`` hits ``seismicity``; ``earthquake`` hits ``earthquakes``);
shorter terms match whole segments, with an optional plural ``s``/``es``.

Everything here is pure and deterministic: no I/O, no randomness, and every
result is ordered by position, never by set or dict iteration.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from functools import cached_property
from typing import Any, Final, cast

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.grammar import (
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
    Structure,
    Trend,
)

__all__ = [
    "DEFAULT_FORBIDDEN",
    "DEFAULT_TIME_FACTOR",
    "DSL_RESERVED",
    "AnonymisationError",
    "FieldKind",
    "Leak",
    "LeakError",
    "NameMap",
    "anonymise_args",
    "anonymise_channel_specs",
    "anonymise_dataset",
    "anonymise_event_log",
    "anonymise_structure",
    "anonymise_text",
    "assert_no_leaks",
    "deanonymise_args",
    "deanonymise_dataset",
    "deanonymise_event_log",
    "deanonymise_structure",
    "deanonymise_text",
    "json_schema_paths",
    "make_name_map",
    "scan_for_leaks",
    "scan_for_numeric_leaks",
    "scan_json_for_leaks",
]


class AnonymisationError(SciAgentError):
    """A name map is ill-formed, or a name or field cannot be (de)anonymised."""


@dataclass(frozen=True)
class Leak:
    """One forbidden term (or native-unit number) found in rendered text.

    ``start``/``end`` index ``source``'s text (for JSON, the offending string
    leaf, whose path is ``source``); ``context`` is the surrounding text with
    whitespace collapsed.
    """

    term: str
    match: str
    start: int
    end: int
    context: str
    source: str = ""


class LeakError(AnonymisationError):
    """A rendered prompt, schema or result carries a forbidden term."""

    def __init__(self, leaks: Sequence[Leak]) -> None:
        self.leaks: tuple[Leak, ...] = tuple(leaks)
        lines = [f"{len(self.leaks)} leak(s) in the anonymised condition:"]
        for leak in self.leaks:
            where = leak.source or "<text>"
            lines.append(
                f"  [{where}] {leak.term!r} matched {leak.match!r} "
                f"at {leak.start}: ...{leak.context}..."
            )
        super().__init__("\n".join(lines))


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Words the DSL itself uses: productions, keywords, kernel and link names.
#: Derived from the grammar's enums where they exist so it cannot drift.
DSL_RESERVED: Final[frozenset[str]] = frozenset(
    {
        "Excite",
        "Periodic",
        "Trend",
        "Product",
        "Gate",
        "Mark",
        "Pow",
        "ExpOf",
        "Above",
        "One",
        "LastMarkAbove",
        "PhaseWindow",
        "null",
        "link",
        "all",
    }
    | {kernel.value for kernel in KernelKind}
    | {link.value for link in Link}
)

#: The factor applied to every time shown to the agent (rates scale by 1/c).
#: It must be an exact power of two (:class:`NameMap` enforces it): multiplying
#: or dividing a float by 2**k only changes its exponent, so it is bit-exact.
#: Rescaled event times, windows and bin edges then stay exactly consistent (an
#: event sitting on a bin edge stays on it), diagnostics transform covariantly
#: to the last bit, and ``t * c / c == t`` exactly (for normal floats). A
#: non-power-of-two factor can move an event across an edge. The constant is 8
#: because it is not a recognisable domain value: it is not 1 and not a
#: familiar unit (7, 10, 12, 24, 30, 60, 100, 365, 1440, 3600, 86400); c times
#: those is not within 2% of a round number (mantissa 1, 2, 2.5 or 5), nor is
#: 1/c = 0.125; and a time of 0.01 (Omori c) or 365.25 does not survive as a
#: recognisable value. ``tests/test_anonymise.py`` checks these properties.
DEFAULT_TIME_FACTOR: Final = 8.0

#: Baseline domain cues for the leak scan, sorted. An environment adds its own
#: names (channel and diagnostic names, tool-specific words) to this list.
DEFAULT_FORBIDDEN: Final[tuple[str, ...]] = (
    "aftershock",
    "earthquake",
    "epicenter",
    "epidemic",
    "etas",
    "foreshock",
    "gutenberg",
    "hawkes",
    "hypocenter",
    "magnitude",
    "mainshock",
    "neuron",
    "omori",
    "quake",
    "richter",
    "scedc",
    "seismic",
    "seismicity",
    "seismology",
    "spike",
    "spiking",
    "tectonic",
    "utsu",
)

_IDENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN: Final = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*")


# --------------------------------------------------------------------------
# NameMap
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NameMap:
    """A consistent, reversible renaming plus a time scale.

    ``channels`` and ``diagnostics`` are ``(native, anonymised)`` pairs sorted
    by native name. Construct through :func:`make_name_map`; direct
    construction is validated too (bijective, DSL identifiers, no DSL reserved
    words on either side, no clash between kinds, sorted).
    """

    channels: tuple[tuple[str, str], ...]
    diagnostics: tuple[tuple[str, str], ...]
    time_factor: float = DEFAULT_TIME_FACTOR

    def __post_init__(self) -> None:
        if not (math.isfinite(self.time_factor) and self.time_factor > 0):
            raise AnonymisationError(
                f"time factor must be positive and finite: {self.time_factor}"
            )
        if self.time_factor == 1.0:
            raise AnonymisationError("time factor 1 would not rescale time")
        if math.frexp(self.time_factor)[0] != 0.5:
            raise AnonymisationError(
                f"time factor must be an exact power of two, so that rescaling is "
                f"bit-exact: {self.time_factor}"
            )
        for kind, pairs in (
            ("channel", self.channels),
            ("diagnostic", self.diagnostics),
        ):
            natives = [n for n, _ in pairs]
            if natives != sorted(natives):
                raise AnonymisationError(f"{kind} pairs must be sorted by native name")
        natives_all = [n for n, _ in (*self.channels, *self.diagnostics)]
        anons_all = [a for _, a in (*self.channels, *self.diagnostics)]
        for name in (*natives_all, *anons_all):
            if _IDENT.fullmatch(name) is None:
                raise AnonymisationError(f"{name!r} is not a DSL identifier")
            if name in DSL_RESERVED:
                raise AnonymisationError(f"{name!r} is a DSL reserved word")
        if len(set(natives_all)) != len(natives_all):
            raise AnonymisationError(f"duplicate native names in {natives_all}")
        if len(set(anons_all)) != len(anons_all):
            raise AnonymisationError(f"anonymised names are not distinct: {anons_all}")

    # -- the four name tables ----------------------------------------------

    @cached_property
    def _channel_fwd(self) -> dict[str, str]:
        return dict(self.channels)

    @cached_property
    def _channel_inv(self) -> dict[str, str]:
        return {a: n for n, a in self.channels}

    @cached_property
    def _diag_fwd(self) -> dict[str, str]:
        return dict(self.diagnostics)

    @cached_property
    def _diag_inv(self) -> dict[str, str]:
        return {a: n for n, a in self.diagnostics}

    @cached_property
    def _text_fwd(self) -> dict[str, str]:
        return {**self._channel_fwd, **self._diag_fwd}

    @cached_property
    def _text_inv(self) -> dict[str, str]:
        return {**self._channel_inv, **self._diag_inv}

    # -- names ---------------------------------------------------------------

    def channel_forward(self, name: str) -> str:
        """Native channel name → agent-visible name."""
        return _lookup(self._channel_fwd, name, "native channel")

    def channel_inverse(self, name: str) -> str:
        """Agent-visible channel name → native name (a native name is refused)."""
        return _lookup(self._channel_inv, name, "anonymised channel")

    def diagnostic_forward(self, name: str) -> str:
        """Native diagnostic name → agent-visible name."""
        return _lookup(self._diag_fwd, name, "native diagnostic")

    def diagnostic_inverse(self, name: str) -> str:
        """Agent-visible diagnostic name → native name."""
        return _lookup(self._diag_inv, name, "anonymised diagnostic")

    # -- time and rates ------------------------------------------------------

    def time_forward[T: (float, Floats)](self, t: T) -> T:
        """Native duration or time -> agent-visible (times c)."""
        return cast("T", _mul(t, self.time_factor))

    def time_inverse[T: (float, Floats)](self, t: T) -> T:
        """Agent-visible time -> native (divided by c)."""
        return cast("T", _div(t, self.time_factor))

    def rate_forward[T: (float, Floats)](self, r: T) -> T:
        """Native rate -> agent-visible (divided by c)."""
        return cast("T", _div(r, self.time_factor))

    def rate_inverse[T: (float, Floats)](self, r: T) -> T:
        """Agent-visible rate -> native (times c)."""
        return cast("T", _mul(r, self.time_factor))


def _lookup(table: Mapping[str, str], name: str, what: str) -> str:
    try:
        return table[name]
    except KeyError:
        raise AnonymisationError(f"unknown {what} name {name!r}") from None


def _mul(x: float | Floats, k: float) -> float | Floats:
    if isinstance(x, np.ndarray):
        return np.asarray(x * k, dtype=np.float64)
    return float(x) * k


def _div(x: float | Floats, k: float) -> float | Floats:
    if isinstance(x, np.ndarray):
        return np.asarray(x / k, dtype=np.float64)
    return float(x) / k


def make_name_map(
    channels: Mapping[str, str] | Sequence[str],
    diagnostic_names: Iterable[str],
    time_factor: float = DEFAULT_TIME_FACTOR,
) -> NameMap:
    """Build a :class:`NameMap`.

    ``channels`` is either an explicit ``{native: anonymised}`` mapping (the
    environment's choice, e.g. ``{"size": "m1", "sign": "m2"}``) or a sequence
    of native names, numbered ``m1, m2, ...`` in the order given.
    ``diagnostic_names`` are numbered ``d01, d02, ...`` (wider past 99 names) in
    *sorted* native order, so the numbering never depends on input order.
    """
    if isinstance(channels, str):
        raise AnonymisationError("channels must be a mapping or a sequence of names")
    if isinstance(channels, Mapping):
        channel_pairs = dict(channels)
    else:
        names = list(channels)
        if len(set(names)) != len(names):
            raise AnonymisationError(f"duplicate channel names in {names}")
        channel_pairs = {name: f"m{i + 1}" for i, name in enumerate(names)}
    diag = list(diagnostic_names)
    if len(set(diag)) != len(diag):
        raise AnonymisationError(f"duplicate diagnostic names in {diag}")
    ordered = sorted(diag)
    width = max(2, len(str(len(ordered))))
    return NameMap(
        channels=tuple(sorted(channel_pairs.items())),
        diagnostics=tuple((d, f"d{i + 1:0{width}d}") for i, d in enumerate(ordered)),
        time_factor=float(time_factor),
    )


# --------------------------------------------------------------------------
# Free text
# --------------------------------------------------------------------------


def _rewrite(text: str, table: Mapping[str, str]) -> str:
    return _TOKEN.sub(lambda m: table.get(m.group(), m.group()), text)


def anonymise_text(text: str, name_map: NameMap) -> str:
    """Rename channel and diagnostic names in framework-produced text.

    Whole identifiers only (``sizes``, ``resize``, ``size2`` are not touched, and
    ``size_gap_correlation`` is replaced as one token), case-sensitively, in one
    simultaneous pass (a swap ``a↔b`` works). DSL production names are never in
    the table. Text that already contains an anonymised name which is not also a
    native name is refused, because the inverse could not tell it from a
    renamed one.
    """
    forward = name_map._text_fwd
    inverse = name_map._text_inv
    for token in _TOKEN.findall(text):
        if token in inverse and token not in forward:
            raise AnonymisationError(
                f"text already contains the anonymised name {token!r}; "
                "it would not be reversible"
            )
    return _rewrite(text, forward)


def deanonymise_text(text: str, name_map: NameMap) -> str:
    """The inverse of :func:`anonymise_text` (agent-visible → native names)."""
    return _rewrite(text, name_map._text_inv)


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------


def _map_mark(mark: MarkFn, f: Callable[[str], str]) -> MarkFn:
    match mark:
        case One():
            return mark
        case Mark(channel=c):
            return Mark(f(c))
        case Pow(channel=c):
            return Pow(f(c))
        case ExpOf(channel=c):
            return ExpOf(f(c))
        case Above(channel=c):
            return Above(f(c))


def _map_source(source: Source, f: Callable[[str], str]) -> Source:
    if source.channel is None:
        return source
    return Source(source.kind, f(source.channel))


def _map_cond(cond: Cond, f: Callable[[str], str]) -> Cond:
    match cond:
        case LastMarkAbove(channel=c):
            return LastMarkAbove(f(c))
        case PhaseWindow():
            return cond


def _map_feature(feature: Feature, f: Callable[[str], str]) -> Feature:
    match feature:
        case Excite(kernel=kernel, mark=mark, source=source):
            return Excite(kernel, _map_mark(mark, f), _map_source(source, f))
        case Periodic() | Trend():
            return feature
        case Product(left=left, right=right):
            return Product(_map_feature(left, f), _map_feature(right, f))
        case Gate(feature=inner, cond=cond):
            return Gate(_map_feature(inner, f), _map_cond(cond, f))


def anonymise_structure(structure: Structure, name_map: NameMap) -> Structure:
    """Rename every channel in the tree. Commutes with ``syntax.render``:
    ``render(anonymise_structure(s)) == anonymise_text(render(s))``."""
    return Structure(
        tuple(_map_feature(x, name_map.channel_forward) for x in structure.features),
        structure.link,
    )


def deanonymise_structure(structure: Structure, name_map: NameMap) -> Structure:
    """The inverse; a channel that is not an anonymised name is refused."""
    return Structure(
        tuple(_map_feature(x, name_map.channel_inverse) for x in structure.features),
        structure.link,
    )


def anonymise_channel_specs(
    channels: tuple[ChannelSpec, ...], name_map: NameMap
) -> tuple[ChannelSpec, ...]:
    """The channel set as the agent sees it (names changed; kind, location and
    scale as they were). Use it to ``parse`` agent-written DSL."""
    return tuple(
        replace(spec, name=name_map.channel_forward(spec.name)) for spec in channels
    )


# --------------------------------------------------------------------------
# Tool arguments and results
# --------------------------------------------------------------------------


class FieldKind(Enum):
    """How one JSON field is rewritten."""

    CHANNEL = "channel"  # a channel name
    DIAGNOSTIC = "diagnostic"  # a diagnostic name
    TIME = "time"  # a time or duration (* c); a number or nested lists of them
    RATE = "rate"  # a rate (divided by c); a number or nested lists of them
    TEXT = "text"  # free text or DSL text: channel and diagnostic names inside
    CHANNEL_KEYS = "channel_keys"  # an object whose keys are channel names
    DIAGNOSTIC_KEYS = "diagnostic_keys"  # an object whose keys are diagnostic names


def _leaf(kind: FieldKind, nm: NameMap, *, forward: bool) -> Callable[[Any], Any]:
    match kind:
        case FieldKind.CHANNEL:
            name_fn = nm.channel_forward if forward else nm.channel_inverse
            return lambda v: _on_str(v, name_fn, kind)
        case FieldKind.DIAGNOSTIC:
            name_fn = nm.diagnostic_forward if forward else nm.diagnostic_inverse
            return lambda v: _on_str(v, name_fn, kind)
        case FieldKind.TEXT:
            text_fn = (
                (lambda s: anonymise_text(s, nm))
                if forward
                else (lambda s: deanonymise_text(s, nm))
            )
            return lambda v: _on_str(v, text_fn, kind)
        case FieldKind.TIME:
            return lambda v: _on_number(
                v, nm.time_forward if forward else nm.time_inverse
            )
        case FieldKind.RATE:
            return lambda v: _on_number(
                v, nm.rate_forward if forward else nm.rate_inverse
            )
        case FieldKind.CHANNEL_KEYS:
            key_fn = nm.channel_forward if forward else nm.channel_inverse
            return lambda v: _on_keys(v, key_fn, kind)
        case FieldKind.DIAGNOSTIC_KEYS:
            key_fn = nm.diagnostic_forward if forward else nm.diagnostic_inverse
            return lambda v: _on_keys(v, key_fn, kind)


def _on_str(value: Any, fn: Callable[[str], str], kind: FieldKind) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnonymisationError(f"{kind.value} field must be a string: {value!r}")
    return fn(value)


def _on_number(value: Any, fn: Callable[[float], float]) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | str | Mapping):
        raise AnonymisationError(f"time/rate field must be numeric: {value!r}")
    if isinstance(value, list | tuple):
        return [_on_number(v, fn) for v in value]
    if isinstance(value, int | float):
        return fn(float(value))
    raise AnonymisationError(f"time/rate field must be numeric: {value!r}")


def _on_keys(value: Any, fn: Callable[[str], str], kind: FieldKind) -> Any:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise AnonymisationError(f"{kind.value} field must be an object: {value!r}")
    return {fn(k): v for k, v in value.items()}


def _apply(node: Any, segments: Sequence[str], leaf: Callable[[Any], Any]) -> Any:
    """Rebuild ``node`` with ``leaf`` applied where ``segments`` points.

    A missing key (or a null on the way) means "not present": untouched. A wrong
    container type on the way is an error.
    """
    if not segments:
        return leaf(node)
    if node is None:
        return None
    head, rest = segments[0], segments[1:]
    if head == "*":
        if isinstance(node, list):
            return [_apply(v, rest, leaf) for v in node]
        if isinstance(node, Mapping):
            return {k: _apply(v, rest, leaf) for k, v in node.items()}
        raise AnonymisationError(f"path segment '*' needs a list or object: {node!r}")
    if not isinstance(node, Mapping):
        raise AnonymisationError(f"path segment {head!r} needs an object: {node!r}")
    if head not in node:
        return node
    out = dict(node)
    out[head] = _apply(node[head], rest, leaf)
    return out


def _rewrite_json(
    obj: Mapping[str, Any],
    paths: Mapping[str, FieldKind],
    name_map: NameMap,
    *,
    forward: bool,
) -> dict[str, Any]:
    out: Any = copy.deepcopy(dict(obj))
    for path in sorted(paths):
        out = _apply(
            out, path.split("."), _leaf(paths[path], name_map, forward=forward)
        )
    assert isinstance(out, dict)
    return out


def json_schema_paths(
    *,
    channel_properties: Iterable[str] = (),
    time_properties: Iterable[str] = (),
) -> dict[str, FieldKind]:
    """``schema_paths`` for a tool's JSON ``input_schema`` (as ``args_schema`` builds).

    Every ``description`` is text; each name in ``channel_properties`` has an
    ``enum`` of channel names; each name in ``time_properties`` carries absolute
    ``minimum``, ``maximum`` and ``default`` values in time units (scaled by c).
    Other properties (integers, fractions) must not be listed.
    """
    paths: dict[str, FieldKind] = {
        "description": FieldKind.TEXT,
        "properties.*.description": FieldKind.TEXT,
    }
    for name in channel_properties:
        paths[f"properties.{name}.enum.*"] = FieldKind.CHANNEL
    for name in time_properties:
        for bound in ("minimum", "maximum", "default"):
            paths[f"properties.{name}.{bound}"] = FieldKind.TIME
    return paths


def anonymise_args(
    obj: Mapping[str, Any], schema_paths: Mapping[str, FieldKind], name_map: NameMap
) -> dict[str, Any]:
    """Native → agent-visible, for a tool's result (or any framework JSON).

    ``schema_paths`` maps a dotted path to how that field is rewritten:
    ``"events.*.t": FieldKind.TIME`` (``*`` is every element of a list or every
    value of an object; a missing key is skipped; a field of the wrong type is
    an :class:`AnonymisationError`). Fields not listed are copied untouched, so
    list every field that can carry a name or a time; :func:`scan_json_for_leaks`
    catches an omission. Paths must not overlap. The input is not mutated.
    """
    return _rewrite_json(obj, schema_paths, name_map, forward=True)


def deanonymise_args(
    obj: Mapping[str, Any], schema_paths: Mapping[str, FieldKind], name_map: NameMap
) -> dict[str, Any]:
    """Agent-visible → native, for a tool's arguments (the inverse of
    :func:`anonymise_args` on the same ``schema_paths``). A channel or
    diagnostic that is not an anonymised name is refused."""
    return _rewrite_json(obj, schema_paths, name_map, forward=False)


# --------------------------------------------------------------------------
# Event logs and datasets
# --------------------------------------------------------------------------


def anonymise_event_log(log: EventLog, name_map: NameMap) -> EventLog:
    """Times and horizon * c, channels renamed, mark values unchanged."""
    return _map_log(log, name_map, forward=True)


def deanonymise_event_log(log: EventLog, name_map: NameMap) -> EventLog:
    """The inverse of :func:`anonymise_event_log` (to rounding)."""
    return _map_log(log, name_map, forward=False)


def _map_log(log: EventLog, nm: NameMap, *, forward: bool) -> EventLog:
    rename = nm.channel_forward if forward else nm.channel_inverse
    scale = nm.time_forward if forward else nm.time_inverse
    marks = {rename(name): values for name, values in log.marks.items()}
    return EventLog.create(scale(log.times), marks, scale(log.horizon))


def anonymise_dataset(ds: Dataset, name_map: NameMap) -> Dataset:
    """The log as above, excluded intervals * c, and the label's names renamed;
    the ``endogenous`` mask is unchanged."""
    return _map_dataset(ds, name_map, forward=True)


def deanonymise_dataset(ds: Dataset, name_map: NameMap) -> Dataset:
    """The inverse of :func:`anonymise_dataset` (to rounding)."""
    return _map_dataset(ds, name_map, forward=False)


def _map_dataset(ds: Dataset, nm: NameMap, *, forward: bool) -> Dataset:
    scale = nm.time_forward if forward else nm.time_inverse
    text = anonymise_text if forward else deanonymise_text
    return Dataset.create(
        _map_log(ds.log, nm, forward=forward),
        ds.endogenous,
        tuple((scale(a), scale(b)) for a, b in ds.excluded),
        text(ds.label, nm),
    )


# --------------------------------------------------------------------------
# The leak scan
# --------------------------------------------------------------------------

_SEP: Final = r"[_\-\s]+"
_STEM_LENGTH: Final = 6  # letters at which a term also matches as a stem
_CONTEXT: Final = 30
# A word segment starts after a non-letter, or between a lower-case letter and
# an upper-case one. ``(?-i:...)`` makes the case test literal under IGNORECASE.
_LEFT: Final = r"(?:(?<![A-Za-z])|(?<=(?-i:[a-z]))(?=(?-i:[A-Z])))"
_RIGHT_WORD: Final = r"(?:(?:es|s)?(?![A-Za-z])|(?<=(?-i:[a-z]))(?=(?-i:[A-Z])))"
_NUMBER: Final = re.compile(
    r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![\w])"
)


def _term_pattern(term: str) -> re.Pattern[str]:
    parts = [p for p in re.split(_SEP, term.strip().lower()) if p]
    if not parts:
        raise AnonymisationError(f"empty forbidden term {term!r}")
    body = _SEP.join(re.escape(p) for p in parts)
    letters = sum(len(p) for p in parts)
    right = "" if letters >= _STEM_LENGTH else _RIGHT_WORD
    return re.compile(_LEFT + body + right, re.IGNORECASE)


def _context(text: str, start: int, end: int) -> str:
    window = text[max(0, start - _CONTEXT) : end + _CONTEXT]
    return " ".join(window.split())


def scan_for_leaks(
    text: str, forbidden: Iterable[str], *, source: str = ""
) -> tuple[Leak, ...]:
    """Every occurrence of a forbidden term in ``text``, ordered by position.

    Case-insensitive; anchored at word-segment starts (see the module docstring
    for the exact rule). Separators inside a multi-word term (``_``, ``-``,
    whitespace) match each other. Each term is reported separately, so
    ``size_gap_correlation`` hits both ``size`` and ``size_gap_correlation``
    when both are forbidden.
    """
    found: list[Leak] = []
    for term in dict.fromkeys(forbidden):
        for m in _term_pattern(term).finditer(text):
            found.append(
                Leak(
                    term=term,
                    match=m.group(),
                    start=m.start(),
                    end=m.end(),
                    context=_context(text, m.start(), m.end()),
                    source=source,
                )
            )
    return tuple(sorted(found, key=lambda x: (x.start, x.end, x.term)))


def scan_for_numeric_leaks(
    text: str,
    constants: Iterable[float],
    *,
    rel_tol: float = 1e-6,
    source: str = "",
) -> tuple[Leak, ...]:
    """Numbers in ``text`` equal (to ``rel_tol``) to a native-unit constant.

    Numbers inside identifiers (``d24``, ``m1``) are not numbers. A hit means a
    native-unit value reached the agent unscaled (a known period, say).
    """
    consts = [float(c) for c in constants]
    found: list[Leak] = []
    for m in _NUMBER.finditer(text):
        value = float(m.group())
        for c in consts:
            if abs(value - c) <= rel_tol * abs(c):
                found.append(
                    Leak(
                        term=repr(c),
                        match=m.group(),
                        start=m.start(),
                        end=m.end(),
                        context=_context(text, m.start(), m.end()),
                        source=source,
                    )
                )
    return tuple(sorted(found, key=lambda x: (x.start, x.end, x.term)))


def _json_leaves(obj: Any, path: str) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(path, "key"|"str"|"num", value)`` for every key and leaf."""
    if isinstance(obj, str):
        yield path, "str", obj
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, int | float):
        yield path, "num", obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            sub = f"{path}.{key}" if path else str(key)
            yield sub, "key", str(key)
            yield from _json_leaves(value, sub)
    elif isinstance(obj, list | tuple):
        for i, value in enumerate(obj):
            yield from _json_leaves(value, f"{path}[{i}]")


def scan_json_for_leaks(
    obj: Any,
    forbidden: Iterable[str],
    *,
    numeric_constants: Iterable[float] = (),
    rel_tol: float = 1e-6,
    source: str = "",
) -> tuple[Leak, ...]:
    """Scan a JSON-like object (a tool schema, a tool result) leaf by leaf.

    Keys and string values are scanned as text, and number leaves are compared
    with ``numeric_constants``. Scanning ``json.dumps(obj)`` instead would miss
    a term after an escaped newline (``\\nsize``), so leaves are walked. Each
    leak's ``source`` is ``source`` plus the leaf's path.
    """
    terms = tuple(forbidden)
    consts = tuple(numeric_constants)
    found: list[Leak] = []
    for path, kind, value in _json_leaves(obj, ""):
        where = f"{source}:{path}" if source and path else (source or path)
        if kind == "num":
            found.extend(
                scan_for_numeric_leaks(
                    repr(value), consts, rel_tol=rel_tol, source=where
                )
            )
        else:
            found.extend(scan_for_leaks(value, terms, source=where))
            if kind == "str":
                found.extend(
                    scan_for_numeric_leaks(value, consts, rel_tol=rel_tol, source=where)
                )
    return tuple(sorted(found, key=lambda x: (x.source, x.start, x.end, x.term)))


def assert_no_leaks(
    rendered: Mapping[str, Any] | Iterable[Any],
    forbidden: Iterable[str],
    *,
    numeric_constants: Iterable[float] = (),
    rel_tol: float = 1e-6,
) -> None:
    """Raise :class:`LeakError` if any rendered prompt carries a leak.

    ``rendered`` is the full set of what the agent will see in the anonymised
    condition: a ``{label: text}`` mapping (labels appear in the error), or an
    iterable of texts. A value that is not a string (a tool's ``schema()``) is
    scanned as JSON. The error lists every hit with its source and context.
    """
    terms = tuple(forbidden)
    consts = tuple(numeric_constants)
    items: list[tuple[str, Any]]
    if isinstance(rendered, Mapping):
        items = [(str(k), v) for k, v in rendered.items()]
    else:
        items = [(f"#{i}", v) for i, v in enumerate(rendered)]
    leaks: list[Leak] = []
    for label, value in items:
        if isinstance(value, str):
            leaks.extend(scan_for_leaks(value, terms, source=label))
            leaks.extend(
                scan_for_numeric_leaks(value, consts, rel_tol=rel_tol, source=label)
            )
        else:
            leaks.extend(
                scan_json_for_leaks(
                    value,
                    terms,
                    numeric_constants=consts,
                    rel_tol=rel_tol,
                    source=label,
                )
            )
    if leaks:
        raise LeakError(leaks)
