"""Short-term aftershock incompleteness, as a declared observation process.

This is the found-data counterpart of SPEC §4.5's S12 nuisance: structure the
environment carries, that shapes every measurement, and that nothing is scored
on. ``environments/pointproc/operations.py`` declares its censoring through a
component family and turns it into a restriction on the event log; here the
restriction is a predicate over the catalogue's own columns, because a found
catalogue has no programme to attach a family to.

Why this has to be declared rather than ignored
-----------------------------------------------

A template-matching catalogue cannot resolve small events inside the coda of a
large one -- their waveforms are buried in it. So the catalogue's completeness
magnitude is not constant: it jumps after every large event and decays back.
This is a property of the *catalogue*, not of the earthquake process.

Leaving it undeclared would be fatal to the transfer test rather than merely
untidy. Events thinning out after large marks is a correlation between mark size
and arrival rate -- which is the exact signature of ``AddDependency(size →
arrival)``, the consensus edit the system is supposed to detect. An undeclared
observation process would therefore *manufacture* the answer, with the sign
reversed: incompleteness suppresses events after a large mark where ETAS
triggering produces more of them. Either way, a detector reading the difference
would be reading the catalogue's instrument response and calling it seismology.

``docs/BACKLOG.md`` records the same hazard from the other end -- template
matching also produces false detections that cluster after large marks -- and
notes that bounding the detector's reading against an artifact model is
downstream work. What is settled here is only that the process is *named*,
*versioned* and *applied*, so that the downstream bound has something to move.

The model
---------

Helmstetter, Kagan and Jackson (2006), which is the standard short-term
incompleteness model for exactly this catalogue's region: the completeness
magnitude a time ``dt`` (in days) after a mainshock of magnitude ``M`` is

    ``Mc(M, dt) = M - deficit - slope * log10(dt)``

with ``deficit = 4.5`` and ``slope = 0.75``. An event below that floor is taken
to be one the catalogue could not have recorded, and is removed.

The parameters are literals here rather than fitted, and that is deliberate:
fitting them to this catalogue would make the observation process a function of
the data the investigation is about, which is the same class of error as
deriving bin edges from the scenario under test (see
``environments/pointproc/outcomes.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from sciagent.core.errors import MalformedDesignError

__all__ = ["CENSORING_VERSION", "DECLARED_CENSORING", "AftershockIncompleteness"]

#: Bumped whenever any number below moves. Enters the data address, so a segment
#: ingested under one observation process can never be read as one ingested
#: under another. Prefixed with its own name so that it cannot be confused with
#: -- or accidentally satisfy a containment check against -- the pipeline
#: version, which is a different string in the same address.
CENSORING_VERSION = "aftershock-incompleteness/1.0.0"

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class AftershockIncompleteness:
    """A time-varying completeness magnitude following large events.

    Guarantees that :meth:`removed` is a pure function of the two arrays it is
    given: no state, no ordering dependence beyond the ascending times it is
    documented to require, and no randomness.
    """

    trigger_magnitude: float
    """Only events at or above this generate a blind window, and none is ever
    itself censored -- see :meth:`removed`.

    An earlier version of this docstring claimed that lowering it "would cost a
    great deal of arithmetic and remove nothing". The preflight re-review
    measured that and it is false: over 21253 events, a trigger of 4.0 removes
    509 and a trigger of 2.0 removes **780**, because
    ``2.0 - 4.5 - 0.75*log10(1.157e-5) = 1.20``, which is above the M0.3 cut.

    Removal is in fact *non-monotone* in this parameter, and the immunity rule
    is why: lowering the trigger both opens more windows and immunises more
    events, and the two effects cross. At ``trigger_magnitude <= magnitude_cut``
    every event is a trigger, so the model removes nothing at all. The value is
    rendered into the data address, so none of this can be changed silently --
    but it is a real coupling and worth knowing before tuning it.
    """

    blind_days: float
    """Hard cap on how long after a trigger the model is applied.

    **This cap binds the answer for the largest events, and is meant to.** An
    earlier version of this docstring claimed it bound only the cost; that was
    measured false. The floor decays logarithmically, so for the catalogue's
    largest event -- the M7.2 of 2010-04-04 -- it is still 1.95 at ten days, far
    above the M0.3 cut, and would not fall below it for some 1585 days.

    Ten days is a statement about the model's validity, not about compute.
    Helmstetter et al. fit short-term incompleteness over hours to days; running
    their formula out to four years would be extrapolating a fitted curve by
    three orders of magnitude in time and calling the result an observation
    process. Truncating declares instead that beyond ten days this model has
    nothing to say, and the catalogue is taken at face value.

    The cost of that choice is real and should be visible to anyone reading a
    found-data result: over the first 400000 rows the declared model removes
    27738 of the 144049 events surviving the cut, about 19%.
    """

    deficit: float
    slope: float

    min_elapsed_days: float
    """Floor on ``dt`` inside the logarithm.

    ``log10(0)`` is ``-inf``, which would make the completeness floor infinite
    for an event recorded in the same instant as its trigger. Clamping is the
    honest reading: the model is not claimed to hold below a fraction of a
    second, and an unbounded floor would remove events on the strength of an
    asymptote rather than of seismology.
    """

    def completeness(
        self, trigger_size: Floats | float, elapsed_days: Floats
    ) -> Floats:
        """Return the completeness magnitude ``elapsed_days`` after a trigger.

        Guarantees an array broadcast over its inputs, finite everywhere, and
        monotonically decreasing in ``elapsed_days``.
        """
        clamped = np.maximum(
            np.asarray(elapsed_days, dtype=np.float64), self.min_elapsed_days
        )
        floor: Floats = (
            np.asarray(trigger_size, dtype=np.float64)
            - self.deficit
            - self.slope * np.log10(clamped)
        )
        return floor

    def removed(self, days: Floats, size: Floats) -> npt.NDArray[np.bool_]:
        """Return the mask of events the catalogue could not have recorded.

        ``days`` must be ascending; ``size`` is the magnitude of each event.
        Guarantees a boolean array of ``size``'s shape, and that an event is
        marked only when it lies strictly after a trigger, within
        :attr:`blind_days` of it, and below the completeness floor that trigger
        implies at that separation.

        Complexity is ``O(triggers x window)``, not ``O(n^2)``: only events at or
        above :attr:`trigger_magnitude` open a window, and each window is bounded
        by :attr:`blind_days`. On the real catalogue that is a few hundred
        windows over 1.81M events.
        """
        if days.shape != size.shape:
            raise MalformedDesignError(
                f"days and size describe different catalogues: {days.shape} "
                f"against {size.shape}"
            )
        if days.size and bool(np.any(np.diff(days) < 0.0)):
            raise MalformedDesignError(
                "the censoring model reads a catalogue in ascending time order; "
                "these days descend somewhere, so the window search would be wrong"
            )
        out = np.zeros(size.shape, dtype=np.bool_)
        triggers = np.flatnonzero(size >= self.trigger_magnitude)
        for index in triggers:
            start = int(np.searchsorted(days, days[index], side="right"))
            stop = int(
                np.searchsorted(days, days[index] + self.blind_days, side="right")
            )
            if stop <= start:
                continue
            elapsed = days[start:stop] - days[index]
            floor = self.completeness(float(size[index]), elapsed)
            out[start:stop] |= size[start:stop] < floor
        # An event large enough to open a window of its own is never inside
        # anyone else's. Without this the raw formula deletes real mainshocks:
        # measured on the primary catalogue, 112 events at M>=3.0 and one at
        # M>=4.0 (id 14607700, M4.03) fall inside the M7.2's blind radius and
        # are removed. That is defensible as physics -- an M4 genuinely can be
        # buried in an M7.2's coda -- and indefensible here, because those are
        # the events the found battery is drawn from and the size-arrival
        # coupling is read on. Deleting them would remove the signal the
        # transfer test exists to measure, in proportion to mark size, which is
        # the confound this whole module is written to avoid.
        #
        # Declared rather than silent: it is a stated truncation of Helmstetter
        # et al.'s floor at the trigger magnitude, and it enters the version.
        #
        # It rescues the found battery and no more than that. Events *below* the
        # trigger are still censored in proportion to how large a neighbour was:
        # measured on the full catalogue, 58080 of 579024 post-cut events are
        # removed, of which 1 sits in M[3.9,4.0) and none at or above M4.0. So
        # there is a hard discontinuity at exactly the trigger magnitude, and
        # the size-arrival confound is *bounded* here rather than removed. What
        # bounds it properly is the artifact-model arm `docs/BACKLOG.md`'s
        # successor entry names; this guard only keeps the battery intact.
        out[triggers] = False
        return out

    def address(self) -> str:
        """Return the fully-parameterised address component for this model.

        Every field is rendered, not just :data:`CENSORING_VERSION`. Rendering
        the version alone was a defect the preflight review demonstrated: two
        models differing in all five parameters produced one identical
        ``DataVersion`` over data they disagreed about by 2888 events out of
        2910. The version string is a human convention that someone must
        remember to bump; the parameters are the thing that actually moves the
        numbers, and invariant 4 addresses results by what moved them.

        ``repr`` on the floats rather than ``%g``: ``%g`` truncates to six
        significant figures, so two genuinely different cuts could still render
        one address.

        The **class name** is rendered too, and that is the same defect caught
        one level up. A subclass overriding :meth:`completeness` or
        :meth:`removed` is type-legal, inherits this method verbatim, and would
        otherwise render an address identical to its parent's: the re-review
        demonstrated two such configs disagreeing about 26277 of 65658 events
        under one byte-identical ``DataVersion``. Rendering the fields alone
        addresses the *declaration*; rendering the type addresses the thing that
        interprets them.
        """
        return (
            f"{CENSORING_VERSION}/{type(self).__qualname__}"
            f"(trigger={self.trigger_magnitude!r},blind={self.blind_days!r},"
            f"deficit={self.deficit!r},slope={self.slope!r},"
            f"min_elapsed={self.min_elapsed_days!r})"
        )


#: The declared model. Preregistered in the same sense the consensus edit is:
#: fixed in source before any segment is ingested, so it cannot be tuned after
#: seeing how a system performed on the data it shapes.
DECLARED_CENSORING = AftershockIncompleteness(
    trigger_magnitude=4.0,
    blind_days=10.0,
    deficit=4.5,
    slope=0.75,
    min_elapsed_days=1.0 / 86400.0,
)
