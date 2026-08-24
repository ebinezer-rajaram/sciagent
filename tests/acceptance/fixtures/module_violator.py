"""Negative control for acceptance test A32. **Never imported by ``src``.**

The module-scope twin of :mod:`holdout_violator`. That fixture plants its
violation inside a function, which is the shape A14's analyser was built to
walk; this one plants it where nothing is called at all -- at module scope,
where the statements run once, at *import* time, and reach a sealed partition
just as effectively.

The whole file is deliberately free of ``def``. That is the blind spot's second
half: a module contributing no functions did not appear in the analyser's module
set either, so its surface pattern came back *unmatched* and the planted
violation reported clean with no entry points. A fixture that also defined a
function would hide that, because the function alone would put the module on the
map.

The class body is here for the same reason as the assignment above it. A class
body executes at import exactly as a module body does, and it is not a function,
so the analyser has to reach it by the same route.
"""

from __future__ import annotations

from sciagent.registry.partitions import DataPartition, SealedAccess

TOKEN = SealedAccess("A32 module-scope negative control")

POOL = DataPartition.HOLDOUT

#: The partition's *value*, not its name. A sealed partition is as reachable by
#: the string the database stores as by the enum member, and only the enum member
#: was ever declared sealed. A32 asserts this line is found, so the declaration
#: cannot be reverted with the gate staying green.
LABEL = "holdout"


class Holder:
    """A class body, evaluated at import. Not a function, and still a path."""

    pool = DataPartition.TEST
