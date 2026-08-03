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
"""

from __future__ import annotations

import hashlib
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
from environments.pointproc.components import ARRIVAL, SIGN
from sciagent.core.types import ComponentId, Seed

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


def digests() -> dict[str, str]:
    """Return one digest per (programme, execution mode)."""
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
    return results


def main() -> int:
    for name, digest in digests().items():
        sys.stdout.write(f"{name} {digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
