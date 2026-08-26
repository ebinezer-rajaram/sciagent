"""Child process for acceptance test A25's cross-process arm.

Prints one ``segment/<k> <sha256>`` line per ingested segment. Deliberately a
separate process, for the same reason ``determinism_child.py`` is: hash
randomisation, dict insertion order and set iteration order are all per-process,
so an in-process repeat loop cannot detect a dependence on them. Not named
``test_*`` so pytest does not collect it.

The parent writes the fixture and passes its path, rather than each side
generating its own: what A25 claims is that *the same bytes* ingest to the same
segments in any process, and regenerating the input on both sides would test the
generator instead of the pipeline.

The digest is over :meth:`~sciagent.core.types.EventLog.to_bytes`, which is the
canonical serialisation the registry content-addresses over -- so a difference
this arm can see is a difference that would move a recorded result.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import numpy as np

from environments.qtm.ingest import REFERENCE_CONFIG, ingest_segments

#: Events in the synthetic fixture, **before** the magnitude cut.
#:
#: Sized against the cut rather than against the segment length, which is the
#: mistake an earlier version made. The magnitudes below are Gutenberg-Richter
#: with ``b = 1`` from a floor of -1.0, so the fraction surviving a cut at
#: ``m`` is ``10 ** -(m + 1)``: at QTM's completeness magnitude of 0.3 that is
#: 5.01%, and 3000 draws would leave 150 events -- not one full segment. The
#: pipeline would then be *correct* and the gate *red*, and the cheapest way to
#: green would be to drop the cut, which is precisely the defect the cut is
#: there to prevent.
#:
#: 50000 leaves ~2500, less ~60 the censoring model excises, for four full
#: segments and a remainder. Four is deliberate: two would let an off-by-one in
#: segmentation survive.
FIXTURE_EVENTS = 50000

#: The fixture's generator seed. Explicit and passed, per invariant 3.
FIXTURE_SEED = 20260826

_HEADER = (
    "YEAR MONTH DAY HOUR MINUTE SECOND EVENTID LATITUDE LONGITUDE DEPTH "
    "MAGNITUDE MAGP5 MAGP95 TEMPLATEID STACKCC MAD RELOCATED?"
)

_EPOCH_ORDINAL = date(2008, 1, 1).toordinal()


def write_fixture(path: Path, *, n_events: int = FIXTURE_EVENTS) -> None:
    """Write a synthetic catalogue in the QTM schema to ``path``.

    Guarantees a file that is byte-identical for a given ``n_events``, that
    carries the real header line, that contains **duplicate timestamps** so the
    tie rule is exercised rather than assumed, and that contains events above
    the censoring model's trigger magnitude so the declared observation process
    has something to act on.

    The schema is the one the real files declare in their own first line; it is
    reproduced here rather than guessed, and ``environments/qtm/ingest.py``
    records it verbatim.
    """
    rng = np.random.default_rng(FIXTURE_SEED)

    # Seconds from 2008-01-01T00:00:00, ascending, drawn as exponential gaps so
    # the catalogue looks like a point process rather than a grid.
    gaps = rng.exponential(scale=180.0, size=n_events)
    seconds = np.cumsum(gaps)

    # Gutenberg-Richter magnitudes, b = 1, floored near the real file's minimum.
    magnitudes = -1.0 + rng.exponential(scale=1.0 / np.log(10.0), size=n_events)

    # Exact ties, planted *above the magnitude cut on both sides*. QTM has rows
    # sharing a timestamp, and a sort keyed on time alone is not a total order,
    # so its result depends on the sort's stability and on the order rows
    # happened to arrive. Ties drawn at random would almost all be cut away
    # before the tie rule ever saw them -- only 5.01% of draws survive a cut at
    # 0.3, so both halves of a random pair survive with probability 0.25%, which
    # over 1000 pairs leaves about two and makes the tie arm depend on luck.
    # These survive by construction, and the two magnitudes differ so that
    # ordering a pair the wrong way round changes the `size` bytes and is
    # therefore visible in the digest.
    tie_at = np.arange(100, n_events - 1, 200)
    seconds[tie_at + 1] = seconds[tie_at]
    magnitudes[tie_at] = 1.1
    magnitudes[tie_at + 1] = 0.9

    # Mainshocks driving the aftershock censoring model, spread across the span
    # rather than crowded into its head, so the censored windows do not all
    # fall inside the first segment.
    magnitudes[[n_events // 10, (9 * n_events) // 10]] = [4.6, 4.1]

    # A mainshock with a trigger-magnitude event inside its own blind window.
    #
    # Without this the fixture cannot see the guard that keeps large events out
    # of the censoring mask: the preflight re-review deleted
    # ``out[triggers] = False`` from ``AftershockIncompleteness.removed`` and
    # gate A25 stayed green on any machine without the 287MB snapshot, because
    # the other mainshocks are thousands of events apart and never fall inside
    # one another's windows. This pair reproduces what the real catalogue does
    # around the M7.2 of 2010-04-04.
    #
    # The arithmetic has to bite, so the gap is set rather than drawn: at 300s
    # the floor is ``7.2 - 4.5 - 0.75*log10(300/86400) = 4.55``, above the
    # aftershock's 4.1, so the raw formula condemns it and only the guard saves
    # it. Shortening this gap keeps monotonicity, since it only moves the event
    # earlier than the draw already placed it.
    mainshock = n_events // 2
    magnitudes[mainshock] = 7.2
    seconds[mainshock + 1] = seconds[mainshock] + 300.0
    magnitudes[mainshock + 1] = 4.1

    lat = 33.0 + rng.random(n_events) * 3.0
    lon = -119.0 + rng.random(n_events) * 3.0
    depth = rng.random(n_events) * 20.0
    stackcc = 0.2 + rng.random(n_events) * 0.5
    mad = 0.01 + rng.random(n_events) * 0.03
    relocated = rng.integers(0, 2, size=n_events)

    lines = [_HEADER]
    for i in range(n_events):
        total = float(seconds[i])
        day, rem = divmod(int(total), 86400)
        hour, rem = divmod(rem, 3600)
        minute = rem // 60
        second = total - (day * 86400 + hour * 3600 + minute * 60)
        civil = date.fromordinal(_EPOCH_ORDINAL + day)
        lines.append(
            f"{civil.year:4d} {civil.month:02d} {civil.day:02d} "
            f"{hour:02d} {minute:02d} {second:6.3f} {i + 1:12d} "
            f"{lat[i]:8.5f} {lon[i]:10.5f} {depth[i]:6.3f} {magnitudes[i]:5.2f} "
            f"{magnitudes[i] - 0.2:5.2f} {magnitudes[i] + 0.2:5.2f} "
            f"{10000000 + i:12d} {stackcc[i]:6.3f} {mad[i]:6.3f} {relocated[i]:d}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def digests(fixture: Path) -> dict[str, str]:
    """Return ``{"segment/<k>": sha256}`` per segment ingested from ``fixture``."""
    segments = ingest_segments(fixture, REFERENCE_CONFIG)
    return {
        f"segment/{index}": hashlib.sha256(log.to_bytes()).hexdigest()
        for index, log in enumerate(segments)
    }


def main() -> None:
    """Print one ``segment/<k> <sha256>`` line per segment of ``sys.argv[1]``."""
    for name, digest in digests(Path(sys.argv[1])).items():
        sys.stdout.write(f"{name} {digest}\n")


if __name__ == "__main__":
    main()
