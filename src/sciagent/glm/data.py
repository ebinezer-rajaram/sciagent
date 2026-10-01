"""Event logs: the data every GLM instrument reads.

Times are in units where the environment's nominal mean rate is 1 (the truth
sampler calibrates to that operating point, SPEC §3); ψ grids assume it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from sciagent.core.errors import SciAgentError

type Floats = npt.NDArray[np.float64]


class EventLogError(SciAgentError):
    """An event log is malformed."""


@dataclass(frozen=True, eq=False)
class EventLog:
    """Strictly increasing event times on ``[0, horizon]`` with per-event marks.

    ``marks`` maps a channel name to one float per event. Construct through
    :meth:`create`, which validates and freezes the arrays.
    """

    times: Floats
    marks: Mapping[str, Floats]
    horizon: float

    @staticmethod
    def create(
        times: npt.ArrayLike, marks: Mapping[str, npt.ArrayLike], horizon: float
    ) -> EventLog:
        t = np.array(times, dtype=np.float64)
        if t.ndim != 1:
            raise EventLogError("times must be one-dimensional")
        if not np.isfinite(horizon) or horizon <= 0:
            raise EventLogError(f"horizon must be positive and finite: {horizon}")
        if t.size and (t[0] < 0 or t[-1] > horizon):
            raise EventLogError("event times must lie in [0, horizon]")
        if t.size > 1 and not np.all(np.diff(t) > 0):
            raise EventLogError("event times must be strictly increasing")
        frozen: dict[str, Floats] = {}
        for name in sorted(marks):
            m = np.array(marks[name], dtype=np.float64)
            if m.shape != t.shape:
                raise EventLogError(f"mark {name!r} has shape {m.shape}, not {t.shape}")
            if not np.all(np.isfinite(m)):
                raise EventLogError(f"mark {name!r} has non-finite values")
            m.setflags(write=False)
            frozen[name] = m
        t.setflags(write=False)
        return EventLog(times=t, marks=frozen, horizon=float(horizon))

    @property
    def n(self) -> int:
        return int(self.times.size)
