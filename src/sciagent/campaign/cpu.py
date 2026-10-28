"""CPU time of a stretch of work in this process, for ``timing.jsonl``.

Wall times on a shared machine swing with other load (JOURNAL session 6: one
ORACLE unit took 75.6 s in one run and 860.5 s in the next), so the campaign
driver also records CPU time per job, measured as ``getrusage`` *deltas*
around the work: a pool worker runs many jobs in its lifetime (the driver's
module docstring), so absolute figures would include every earlier job.

:func:`cpu_mark` before the work, :func:`cpu_since` after; both must run in
the process that does the work. The two figures (:data:`CPU_FIELDS`):

- ``cpu_s``: user + system time of ``RUSAGE_SELF``, that is of every thread of
  this process, BLAS and solver threads included. It is the job's own only
  when nothing else runs in the process meanwhile: true of a pool worker
  (one job at a time) and of an isolated session's process, not of the
  driver's own process (``workers = 0``, or an in-thread session), whose
  other threads would be counted too; the driver states the in-thread
  session's figures as absent rather than misattribute them.
- ``cpu_children_s``: user + system time of ``RUSAGE_CHILDREN``: child
  processes that have **terminated and been waited for** within the interval,
  each including its own waited-for descendants (the ``claude`` CLI of an LLM
  session, the ``docker`` clients of sandboxed calls). Not covered: a child
  still running, or exited but not yet reaped, at :func:`cpu_since` (if it is
  reaped later in the same process, a later interval counts it); a descendant
  orphaned by its parent and reaped by init; and anything that is not a
  descendant at all, notably the processes inside a Docker container (they
  are children of the container runtime, so the CPU of the agent's sandboxed
  code is in neither figure, only its ``docker`` client's).

The figures are timing, like wall time: they go to ``timing.jsonl`` only,
never into a cell, a reading, a detail or an address (LOG 2026-10-28).
Where ``getrusage`` does not exist (Windows), :func:`cpu_mark` gives ``None``
and :func:`cpu_since` states both figures as ``None``, never 0.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Final

if sys.platform != "win32":
    import resource

#: The per-interval figures, in the order :func:`cpu_since` gives them.
CPU_FIELDS: Final = ("cpu_s", "cpu_children_s")
#: Decimal places kept (``getrusage`` resolves microseconds).
DIGITS: Final = 6


@dataclass(frozen=True)
class CpuMark:
    """Cumulative CPU seconds of this process and of its reaped children."""

    self_s: float
    children_s: float


def cpu_mark() -> CpuMark | None:
    """This process's cumulative CPU time now; ``None`` without ``getrusage``."""
    if sys.platform == "win32":
        return None
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return CpuMark(own.ru_utime + own.ru_stime, children.ru_utime + children.ru_stime)


def cpu_since(mark: CpuMark | None) -> dict[str, float | None]:
    """:data:`CPU_FIELDS` over the interval since ``mark`` (module docstring)."""
    now = cpu_mark()
    if mark is None or now is None:
        return dict.fromkeys(CPU_FIELDS)
    return {
        "cpu_s": round(now.self_s - mark.self_s, DIGITS),
        "cpu_children_s": round(now.children_s - mark.children_s, DIGITS),
    }
