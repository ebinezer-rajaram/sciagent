"""Child process for acceptance test A1's cross-process arm.

Prints one ``name sha256`` line per programme. Deliberately a separate process:
hash randomisation, dict insertion order and set iteration order are all
per-process, so an in-process repeat loop cannot detect a dependence on them.
Not named ``test_*`` so pytest does not collect it.

Every programme is run three ways: unclamped, under a forced arrival prefix, and
with a component held fixed for the whole run. Clamped execution (backlog item 7)
is a second path through the event loop, and the argument that it is
determinism-safe -- streams are derived by name, and a clamp is looked up rather
than iterated -- is exactly the kind of argument this arm exists to check rather
than accept.

Two layers, and the second is the one that matters across platforms
-------------------------------------------------------------------

``<name> <sha256>`` lines digest the **event log**. ``metrics/<name> <sha256>``
lines digest every value the v1 SPEC §4.3 catalogue computes *from* that log.

The second layer is here because the first cannot settle the question this file
is nominated for. The project is pinned to Windows, so that question is not
pressing anybody -- this child is the instrument kept against a decision to lift
the pin, not a blocker on current work. It stays correct so that it is usable if
that day comes. ``docs/v1/DECISIONS.md`` records the suspected cause of the
Windows/Ubuntu split it would localise:
``np.dot`` in ``environments/pointproc/diagnostics.py`` against an OpenBLAS built
``DYNAMIC_ARCH``. That call is *downstream* of the log. A log digest is identical
whether or not BLAS sums a dot product in a different order, so the instrument
and the suspicion did not meet, and running the child would have produced a clean
diff that proved nothing about the layer the registry content-addresses over.

Metric values are digested as IEEE doubles rather than as text, matching
``ExperimentRecord.digest_of``: the point is a bit, not a rendering, and a
shortest-round-trip repr would hide a difference in the last place -- which is
exactly the size of difference a different summation order produces.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from collections.abc import Mapping

from environments.pointproc import (
    CONFOUNDED_MECHANISMS,
    SIZE_EXCITATION,
    arrival_burst,
    edit_grammar,
    mechanism_defect,
    reference_program,
)
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import ARRIVAL, SIGN
from sciagent.core.types import ComponentId, EventLog, Seed

N_EVENTS = 512
SEED = Seed(20240801)

#: The three execution modes, keyed by the suffix their digest is reported under.
#: ``forced`` is the ``ForceArrival`` realisation and ``held`` the
#: ``AblateComponent`` one, so between them every clamp shape the point-process
#: compiler emits is covered.
CLAMP_MODES: Mapping[str, Mapping[ComponentId, Mapping[int, float]] | None] = {
    "": None,
    "+forced": {ARRIVAL: arrival_burst(8, 0.01)},
    "+held": {SIGN: dict.fromkeys(range(N_EVENTS), 1.0)},
}


def _metric_digest(log: EventLog) -> str:
    """Return a digest over every catalogue metric's value on ``log``.

    Metrics are taken in sorted name order and packed as little-endian doubles,
    so the digest is a statement about the bits each estimator produced and not
    about the order the registry happens to hold them in.
    """
    registry = metric_registry()
    chunks: list[bytes] = []
    for name in sorted(registry.names):
        value = registry.spec(str(name)).compute(log)
        chunks.append(str(name).encode("utf-8"))
        chunks.append(struct.pack("<d", float(value)))
    return hashlib.sha256(b"".join(chunks)).hexdigest()


def digests() -> dict[str, str]:
    """Return one digest per (programme, execution mode), at both layers.

    ``<name>`` digests the event log; ``metrics/<name>`` digests what the §4.3
    catalogue computes from it. Diffing two platforms on the first alone would
    miss a divergence introduced by BLAS below it -- see the module docstring.
    """
    program = reference_program()
    grammar = edit_grammar()
    results: dict[str, str] = {}
    cases = {"reference": frozenset(), "size_excitation": frozenset({SIZE_EXCITATION})}
    cases.update({name: mechanism_defect(name) for name in CONFOUNDED_MECHANISMS})
    for name in sorted(cases):
        edited = grammar.apply(program, cases[name])
        for suffix in sorted(CLAMP_MODES):
            log = edited.execute(SEED, N_EVENTS, clamps=CLAMP_MODES[suffix])
            results[f"{name}{suffix}"] = hashlib.sha256(log.to_bytes()).hexdigest()
            results[f"metrics/{name}{suffix}"] = _metric_digest(log)
    return results


def main() -> int:
    for name, digest in digests().items():
        sys.stdout.write(f"{name} {digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
