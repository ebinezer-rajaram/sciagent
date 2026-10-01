"""Registers the Hypothesis profile every property test runs under.

It is registered here, in the module body, because ``@given`` captures
``settings.default`` at decoration time, during collection; a profile loaded
later (an autouse fixture, say) would silently govern nothing.

Sampling stays random, and failures are kept in a committed corpus under
``tests/regressions/``. Hypothesis forbids ``derandomize=True`` together with a
database, and random sampling keeps widening coverage across runs, while the
committed corpus still carries any counterexample between machines. A passing
run writes nothing there. ``deadline`` is off because these properties simulate.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

#: The committed corpus. Tracked, unlike Hypothesis's default ``.hypothesis/``,
#: which writes a ``.gitignore`` holding ``*`` into itself -- so a counterexample
#: found there can never be committed and dies with the working tree.
REGRESSIONS = Path(__file__).resolve().parent / "regressions"

settings.register_profile(
    "sciagent",
    derandomize=False,
    deadline=None,
    print_blob=True,
    database=DirectoryBasedExampleDatabase(REGRESSIONS),
)
settings.load_profile("sciagent")
