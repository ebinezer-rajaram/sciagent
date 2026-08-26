"""The QTM ingestion pipeline: catalogue text in, ``EventLog`` segments out.

The schema, as the files themselves declare it
----------------------------------------------

SCEDC publishes no format description for these files. Both carry their own
header line, and this is it, copied from ``qtm_final_9.5dev.hypo`` byte 0 --
wrapped here at the arrow, one line each in the file::

    YEAR MONTH DAY HOUR MINUTE SECOND EVENTID LATITUDE LONGITUDE DEPTH
      -> MAGNITUDE MAGP5 MAGP95 TEMPLATEID STACKCC MAD RELOCATED?
    2008 01 01 00 01 31.150            1 36.00636 -117.80533  1.694
      -> -0.33 -0.65  0.09     14234568  0.260  0.022 0

Seventeen whitespace-separated columns. This pipeline reads five of them: the
six date and time fields, ``EVENTID`` and ``MAGNITUDE``. Location and depth are
not read, because the slice's programmes have no spatial component; the polygon
sensitivity arm ``docs/BACKLOG.md`` calls for would read them, and is downstream
work.

The four stages, in the order they must run
-------------------------------------------

``docs/BACKLOG.md`` names them: magnitude cut, deterministic tie rule, rescale
to mean gap 1.0, disjoint 512-event segments keyed by seed index. The order here
is not the order they are listed in, and the difference matters:

1. **Sort under a total order** -- by ``(time, event id)``. The tie rule comes
   first because everything after it is defined on a sequence.

   Both halves are measured on the real file rather than assumed. **It is not
   delivered in time order** -- ``np.all(np.diff(days) >= 0)`` is ``False`` over
   all 1811362 rows -- so a sort is required, not merely tidy. And it contains a
   tied timestamp, though only *one* pair in 1811362 rows, so time alone is not
   a total order and a sort keyed on it would resolve that pair by whatever the
   sort's stability and the file's row order happened to produce. ``EVENTID`` is
   unique across every row, so ``(time, id)`` is total and the result does not
   depend on the input order at all.

   One pair in 1.8M is rare enough that a fixture relying on the real file's
   ties would be testing nothing; ``qtm_ingest_child.write_fixture`` plants 250
   of them, on both sides of the magnitude cut.
2. **Magnitude cut**, at the catalogue's completeness magnitude.
3. **Censoring** -- see :mod:`environments.qtm.censoring`. It runs here, on real
   days and after the cut, because its completeness floor is a function of
   elapsed *time*, which the next stage destroys.
4. **Segment, then rescale within each segment.** Blocks of 512, disjoint,
   ``segment k`` being events ``[512k, 512(k+1))``.

Rescaling is a parameter, and both settings cost something
-----------------------------------------------------------

``docs/BACKLOG.md`` lists the rescale before the segmentation, which reads as one
global scale factor. Both readings are implemented, both are addressed, and
:data:`RESCALES` states what each one costs. The default is ``per-segment``.

**Neither is free, and an earlier version of this docstring claimed otherwise.**
It argued for ``per-segment`` on the grounds that the empirical tables are built
at ``pointproc``'s operating point of one event per unit time, and said "nothing
is lost". The preflight review showed what is lost: normalising 512 events to a
mean gap of 1.0 forces the span to exactly 511.0 by arithmetic, so *every* found
segment has identical duration while simulated logs of the same length span
511 +/- 23. Total duration alone then separates found data from simulated with
certainty -- in a track whose entire purpose is to compare the two.

That is a research decision rather than a coding one, and it is deliberately
left open here: ``docs/BACKLOG.md``'s successor entry carries it, and gate A25
requires only that the choice be declared, addressed and actually read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

from environments.qtm.censoring import (
    DECLARED_CENSORING,
    AftershockIncompleteness,
)
from sciagent.core.errors import (
    MalformedDesignError,
    SnapshotMismatchError,
    SnapshotMissingError,
)
from sciagent.core.types import ComponentId, EventLog, FrozenDict

__all__ = [
    "N_EVENTS",
    "PIPELINE_VERSION",
    "REFERENCE_CONFIG",
    "RESCALES",
    "Catalogue",
    "IngestionConfig",
    "Rescale",
    "ingest_segments",
    "parse_catalogue",
]

#: Bumped when any stage's behaviour moves. Enters the data address. Prefixed
#: with its own name so a containment check against it cannot be satisfied by
#: the censoring version, which is a different string in the same address.
PIPELINE_VERSION = "pipeline/1.0.0"

#: Events per segment. The same 512 ``environments/pointproc/outcomes.py`` uses,
#: and for the same reason: every diagnostic in the catalogue must be estimable
#: on one segment, and the phase-conditioned dispersion needs several windows per
#: phase bin. A found segment that did not match would not be comparable with a
#: simulated one.
N_EVENTS = 512

#: QTM's completeness magnitude. Ross et al. (2019) report the catalogue as
#: complete above M0.3 across the network; below it the detection rate depends on
#: station geometry, which is a spatial nuisance the slice cannot express.
COMPLETENESS_MAGNITUDE = 0.3

ARRIVAL = ComponentId("arrival")
SIZE = ComponentId("size")

Floats = npt.NDArray[np.float64]

Rescale = Literal["per-segment", "global"]

#: The rescaling schemes, and the trade between them. **Both are degenerate in
#: some respect, which is why this is a parameter and not a constant.**
#:
#: ``per-segment`` divides each segment by its own realised mean gap. Every
#: segment then sits exactly at the reference operating point -- and, as the
#: preflight review demonstrated, at *exactly* the same total duration:
#: ``arrival[-1]`` is pinned to 511.0 for every segment, because normalising the
#: mean gap of 512 events to 1.0 forces the span to 511 by arithmetic. Simulated
#: ``pointproc`` logs of the same length span 511 +/- 23, so total duration alone
#: separates found data from simulated with certainty. Anything scored on a
#: window measured in rescaled units inherits that.
#:
#: ``global`` divides every segment by one factor, the whole retained
#: catalogue's mean gap. Segment spans then vary as the real rate varies, which
#: is the property ``per-segment`` destroys -- at the cost that Southern
#: California's rate spans orders of magnitude between a quiet month and an
#: aftershock sequence, so most segments sit nowhere near the operating point the
#: empirical tables were built at.
#:
#: Which one the found-data track should run at is a research decision and is
#: **not settled here**; ``docs/BACKLOG.md``'s successor entry carries it. What
#: is settled is that the choice is declared, addressed, and actually read.
RESCALES: Final[frozenset[str]] = frozenset({"per-segment", "global"})

#: ``_days_from_civil(2008, 1, 1)``, the catalogue's first day, in the
#: days-since-1970 convention that function returns. Measured, not assumed: an
#: earlier value here was ``date(2008, 1, 1).toordinal()``, which is the
#: *proleptic* ordinal and a different convention by 719163 days. Nothing caught
#: it, and nothing would have: the offset is constant, and both consumers of
#: ``days`` take differences -- the censoring model within a window, the rescale
#: within a segment -- so it cancels in every number the pipeline produces. It
#: was still wrong, because :class:`Catalogue` documents ``days`` as days since
#: 2008-01-01 and anything reading that column directly would have got 1969.
_EPOCH_DAYS = 13879

#: The columns read, and the header row skipped. Named rather than inline so
#: that :meth:`IngestionConfig.address` can render them: the re-review pointed
#: out that these, ``N_EVENTS`` and ``_EPOCH_DAYS`` all change what is ingested
#: while sitting outside the address, covered only by the hand-maintained
#: ``PIPELINE_VERSION`` literal -- which is the exact hazard gate A33 built
#: ``SIMULATOR_DIGEST`` for one directory away, after a bugfix landed without
#: anyone remembering to bump a version string.
#:
#: Rendering the four constants closes what was demonstrated. It is weaker than
#: A33's answer, which digests the environment's whole source and so catches an
#: edit to *any* line rather than to a named few; adopting that here means
#: sharing ``simulator_digest`` out of ``environments/pointproc/tables.py``,
#: which is a refactor of a load-bearing tested function and belongs with the
#: successor entry rather than inside gate A25.
_USECOLS = (0, 1, 2, 3, 4, 5, 6, 10)
_HEADER_ROWS = 1


@dataclass(frozen=True, slots=True)
class Catalogue:
    """A parsed, cut, censored catalogue, in ascending time order.

    Guarantees all three arrays share one length and one ordering, that ``days``
    ascends, and that ``event_id`` is unique -- which is what makes the ordering
    a total one rather than a convention.
    """

    days: Floats
    """Time in days since 2008-01-01T00:00:00Z. Real time, never rescaled."""

    size: Floats
    """Magnitude, passed through untouched."""

    event_id: npt.NDArray[np.int64]
    """The catalogue's own identifier, and the tie-break key."""


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    """Every choice the pipeline makes, in one addressable value.

    Guarantees that two catalogues ingested under equal configs went through
    identical arithmetic: nothing below reads a default from anywhere else, and
    :func:`~environments.qtm.snapshot.data_version` renders all of it.
    """

    magnitude_cut: float
    censoring: AftershockIncompleteness | None
    rescale: Rescale = "per-segment"

    max_rows: int | None = None
    """Bound the *parse* to a prefix of the file, or ``None`` for all of it.

    A config field rather than a call argument, and that is the whole point: it
    changes which events are ingested, so leaving it as an argument put it
    outside :meth:`address` where the preflight review found it. A bounded read
    now cannot be recorded under a full-catalogue address, because the two
    render differently.

    Reading a prefix is well defined but not for the reason it first appears.
    The catalogue is **not** delivered in time order -- 14 inversions in the
    first 300000 rows, the largest 4.4 seconds backwards -- so a prefix of the
    *file* is not exactly a prefix of the *sorted* catalogue. It is one to
    within those inversions, which is why the bounded smoke test works; it is
    not an identity, and nothing here claims it is.
    """

    def __post_init__(self) -> None:
        if self.rescale not in RESCALES:
            raise MalformedDesignError(
                f"rescale={self.rescale!r} is not one of {sorted(RESCALES)}; an "
                f"unrecognised value would be rendered into the data address "
                f"while the pipeline silently did something else"
            )
        if self.max_rows is not None and self.max_rows < 2:
            # Typed, per this project's convention that no module raises a bare
            # ValueError. Left unvalidated, `max_rows=-5` escaped as a numpy
            # ValueError and `max_rows=10**12` as a 58 TiB allocation attempt.
            raise MalformedDesignError(
                f"max_rows={self.max_rows!r} cannot yield a catalogue; a rate "
                f"needs at least two events to be estimated from"
            )

    def without_censoring(self) -> IngestionConfig:
        """Return this config with the observation process removed.

        Exists for the arm of gate A25 that shows the censoring model changes
        what is ingested: a model that is declared, versioned and never applied
        would otherwise be indistinguishable from one that is.
        """
        return replace(self, censoring=None)

    def with_magnitude_cut(self, cut: float) -> IngestionConfig:
        """Return this config at a different completeness magnitude."""
        return replace(self, magnitude_cut=cut)

    def with_max_rows(self, rows: int | None) -> IngestionConfig:
        """Return this config reading only the first ``rows`` rows of the file."""
        return replace(self, max_rows=rows)

    def with_rescale(self, rescale: Rescale) -> IngestionConfig:
        """Return this config under a different rescaling scheme."""
        return replace(self, rescale=rescale)

    def address(self) -> str:
        """Return the part of the data address this config is responsible for.

        Guarantees that two configs producing different data render different
        strings. Every field is rendered by ``repr`` rather than by ``%g``: the
        preflight review demonstrated that ``%g`` collapses ``0.3`` and
        ``0.30000001`` onto one address, and that rendering the censoring
        model's *version* while omitting its five parameters collapsed two
        models that disagreed about 2888 of 2910 events.
        """
        parts = [
            PIPELINE_VERSION,
            f"parse(n={N_EVENTS},epoch={_EPOCH_DAYS},"
            f"cols={_USECOLS},skip={_HEADER_ROWS})",
            f"mc{self.magnitude_cut!r}",
            f"rescale/{self.rescale}",
            "rows/all" if self.max_rows is None else f"rows/{self.max_rows}",
            "censoring/none" if self.censoring is None else self.censoring.address(),
        ]
        return "+".join(parts)


#: The pipeline's declared starting point -- **not** a settled operating point.
#:
#: An earlier comment here called it "the operating point the found-data track
#: runs at", which the preflight re-review pointed out makes the open rescale
#: question decorative: the scheme documented as separating found data from
#: simulated *with certainty* is what every caller silently inherits, and an
#: escalation that sits in a backlog file while the default quietly answers it
#: is not an escalation. ``rescale`` keeps a default so that gate A25's own
#: tests have something to run against; the found-data track must state it.
REFERENCE_CONFIG = IngestionConfig(
    magnitude_cut=COMPLETENESS_MAGNITUDE,
    censoring=DECLARED_CENSORING,
)


def _days_from_civil(
    year: npt.NDArray[np.int64],
    month: npt.NDArray[np.int64],
    day: npt.NDArray[np.int64],
) -> npt.NDArray[np.int64]:
    """Return days since 1970-01-01 for a proleptic Gregorian date, vectorised.

    Guarantees exact integer arithmetic and no dependence on the local timezone,
    which is why this is here rather than ``datetime.timestamp()``: that method
    interprets a naive datetime in the *machine's* zone, so the same catalogue
    would ingest differently in London and Los Angeles -- a platform dependence
    of exactly the kind invariant 3 forbids, and one no test on a single machine
    would ever show.

    Howard Hinnant's ``days_from_civil``, which is the standard branch-free form.
    """
    shifted = year - (month <= 2)
    era = np.floor_divide(shifted, 400)
    year_of_era = shifted - era * 400
    month_shift = np.where(month > 2, -3, 9)
    day_of_year = (153 * (month + month_shift) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    days: npt.NDArray[np.int64] = era * 146097 + day_of_era - 719468
    return days


def parse_catalogue(path: Path, config: IngestionConfig) -> Catalogue:
    """Return the catalogue at ``path``, sorted, cut and censored.

    Guarantees a result that is a pure function of ``path``'s bytes and
    ``config`` -- no ordering dependence on how the rows arrived, no timezone
    dependence, no randomness -- and that everything which shaped it is rendered
    by :meth:`IngestionConfig.address`. ``config.max_rows`` bounds the parse to a
    prefix, and is a config field for exactly that reason.

    Raises :class:`~sciagent.core.errors.SnapshotMissingError` rather than
    letting numpy raise ``FileNotFoundError``: a missing catalogue is this
    environment's own failure mode and has a remedy worth naming.
    """
    if not path.is_file():
        raise SnapshotMissingError(
            f"{path} is not present. If this is the QTM snapshot it is "
            f"gitignored rather than redistributed; run "
            f"`uv run python scripts/fetch_qtm.py` to obtain it."
        )
    columns = np.loadtxt(
        path,
        skiprows=_HEADER_ROWS,
        usecols=_USECOLS,
        dtype=np.float64,
        max_rows=config.max_rows,
        ndmin=2,
    )
    if columns.size == 0:
        raise MalformedDesignError(f"{path.name} holds no data rows below its header")

    whole = columns[:, :5].astype(np.int64)
    civil = _days_from_civil(whole[:, 0], whole[:, 1], whole[:, 2])
    seconds_of_day = (
        whole[:, 3].astype(np.float64) * 3600.0
        + whole[:, 4].astype(np.float64) * 60.0
        + columns[:, 5]
    )
    days = (civil - _EPOCH_DAYS).astype(np.float64) + seconds_of_day / 86400.0
    event_id = columns[:, 6].astype(np.int64)
    size = columns[:, 7]

    if np.unique(event_id).size != event_id.size:
        raise MalformedDesignError(
            f"{path.name} repeats an event id; the tie rule breaks ties by id and "
            f"would not be a total order over it"
        )

    # Stage 1: a total order. `lexsort` takes the *last* key as primary.
    order = np.lexsort((event_id, days))
    days, size, event_id = days[order], size[order], event_id[order]

    # Stage 2: the magnitude cut.
    kept = size >= config.magnitude_cut
    days, size, event_id = days[kept], size[kept], event_id[kept]

    # Stage 3: the declared observation process, on real days.
    if config.censoring is not None:
        survives = ~config.censoring.removed(days, size)
        days, size, event_id = days[survives], size[survives], event_id[survives]

    return Catalogue(days=days, size=size, event_id=event_id)


def _mean_gap(days: Floats) -> float:
    """Return the mean inter-event gap of ``days``, which must ascend.

    The emptiness check comes *before* the indexing it guards. Written the other
    way round -- ``span = float(days[-1] - days[0])`` first, ``days.size < 2``
    second -- the guard was dead code, since indexing an empty array raises
    ``IndexError`` before the test it was there to make. Found by the preflight
    re-review, which reached it through :func:`ingest_segments`' ``global``
    branch on a catalogue that retained nothing.
    """
    if days.size < 2:
        raise MalformedDesignError(
            f"a rate needs at least two events to be estimated from; this span "
            f"holds {days.size}"
        )
    span = float(days[-1] - days[0])
    if span <= 0.0:
        raise MalformedDesignError(
            "a span of no time at all has no rate to rescale to; every event in "
            "it shares one timestamp"
        )
    return span / float(days.size - 1)


def _rescaled(days: Floats, mean_gap: float) -> Floats:
    """Return ``days`` shifted to zero and divided by ``mean_gap``.

    Whose ``mean_gap`` decides the scheme: the segment's own gives
    ``per-segment``, the whole retained catalogue's gives ``global``. See
    :data:`RESCALES` for what each costs -- neither is free, which is why the
    caller supplies it rather than this function choosing.
    """
    return np.asarray((days - days[0]) / mean_gap, dtype=np.float64)


def ingest_segments(
    path: Path,
    config: IngestionConfig,
    *,
    expect_sha256: str | None = None,
    limit: int | None = None,
) -> tuple[EventLog, ...]:
    """Return the disjoint 512-event segments of the catalogue at ``path``.

    Guarantees byte-identical :class:`~sciagent.core.types.EventLog` values for
    the same bytes and config in any process, on any interpreter run: every
    stage is integer arithmetic, a total sort, or a vectorised float operation
    over an order fixed before it. Segment ``k`` is retained events
    ``[512k, 512(k+1))``; a trailing partial block is dropped rather than padded.

    ``expect_sha256`` declares the snapshot the caller believes it is reading and
    is verified **before** anything is parsed; a mismatch raises
    :class:`~sciagent.core.errors.SnapshotMismatchError` rather than warning,
    because a result computed from the wrong catalogue would be recorded under an
    address that names the right one.
    """
    if expect_sha256 is not None:
        from environments.qtm.snapshot import digest_of

        actual = digest_of(path)
        if actual != expect_sha256:
            raise SnapshotMismatchError(
                f"{path.name} hashes to {actual[:12]}... but was declared as "
                f"{expect_sha256[:12]}...; the snapshot is not the one this "
                f"address names"
            )

    catalogue = parse_catalogue(path, config)
    total = catalogue.days.size // N_EVENTS
    count = total if limit is None else min(limit, total)

    # No full segment means no segments, under either scheme. Returning early
    # rather than falling through: the `global` branch below asks the catalogue
    # for a mean gap, which an empty or near-empty catalogue cannot supply, so
    # without this a cut that retained nothing raised out of `_mean_gap` under
    # `global` while `per-segment` returned `()`. Two schemes disagreeing about
    # whether "nothing survived" is an error was a defect the re-review found in
    # the fix that introduced the second scheme.
    if count == 0:
        return ()

    # `limit` bounds how many segments are *returned* and nothing else. It is
    # therefore absent from the address, unlike `config.max_rows`: segment k is
    # the same 512 events whether one segment was asked for or two hundred, so
    # two runs differing only in `limit` agree on every segment they share.
    catalogue_gap = (
        _mean_gap(catalogue.days) if config.rescale == "global" else float("nan")
    )

    segments: list[EventLog] = []
    for index in range(count):
        start = index * N_EVENTS
        stop = start + N_EVENTS
        window = catalogue.days[start:stop]
        mean_gap = catalogue_gap if config.rescale == "global" else _mean_gap(window)
        segments.append(
            EventLog(
                n_events=N_EVENTS,
                values=FrozenDict(
                    {
                        ARRIVAL: _rescaled(window, mean_gap),
                        SIZE: np.array(catalogue.size[start:stop], dtype=np.float64),
                    }
                ),
            )
        )
    return tuple(segments)
