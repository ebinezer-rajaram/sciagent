"""Puts ``tests/`` on ``sys.path`` so ``slice_tables`` is importable from
anywhere in the suite, and registers the Hypothesis profile the property gates
run under. Deliberately holds nothing else: a shared table is built by the
module that asks for it, not by collection.

Why the profile is registered here, in the module body
------------------------------------------------------

``@given`` captures ``settings.default`` when the decorator runs, and the six
property sites in ``tests/acceptance/`` are decorated at *import*, during
collection. A profile loaded any later -- from an autouse session fixture, most
plausibly -- governs none of them, while ``settings()`` goes on reporting the
profile correctly to anyone who asks at run time. That failure is silent in both
directions, which is why gate A33 pins it against a probe decorated at import
rather than inside a test body.

A ``conftest.py`` module body is the earliest hook that runs for every test under
``tests/``, so this is the placement, not a convenience.

Why it samples randomly and keeps a committed corpus
----------------------------------------------------

``docs/BACKLOG.md``'s A33 entry asked for ``derandomize`` *and* a committed
example database. Hypothesis refuses that pairing outright::

    InvalidArgument: derandomize=True implies database=None, so passing
    database=DirectoryBasedExampleDatabase(...) too is invalid.

So it is one or the other. **Do not "fix" this back into the invalid pairing.**

Derandomising buys cross-machine agreement by freezing the sample: every run on
every machine would try the identical examples forever, so the gates would
certify one fixed sample rather than one random one and repeated runs would never
widen coverage. Random sampling with a *tracked* corpus keeps the widening and
still carries a counterexample between machines -- the minimal failing example is
written into ``tests/regressions/`` and committed, which is what the entry wanted
derandomising for. ``print_blob`` supplies the other half it named, "a recorded
seed printed on failure": a failure carries a ``@reproduce_failure`` decorator in
its notes, which pytest prints.

Tracking the corpus is safe, and it was measured rather than assumed: a *passing*
run writes zero files into the database. Only a failure writes, so the corpus
cannot churn the working tree between ``suite-freshness.sh begin`` and
``record``.

``deadline`` is off because these properties simulate. A 200ms budget is a
statement about speed, and a property gate that fails on it reports something
other than a counterexample.
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
