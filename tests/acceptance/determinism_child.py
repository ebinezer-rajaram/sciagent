"""Child process for acceptance test A1's cross-process arm.

Prints one ``name sha256`` line per programme. Deliberately a separate process:
hash randomisation, dict insertion order and set iteration order are all
per-process, so an in-process repeat loop cannot detect a dependence on them.
Not named ``test_*`` so pytest does not collect it.
"""

from __future__ import annotations

import hashlib
import sys

from environments.pointproc import (
    CONFOUNDED_MECHANISMS,
    SIZE_EXCITATION,
    edit_grammar,
    mechanism_defect,
    reference_program,
)
from sciagent.core.types import Seed

N_EVENTS = 512
SEED = Seed(20240801)


def digests() -> dict[str, str]:
    """Return one digest per programme, keyed by mechanism name."""
    program = reference_program()
    grammar = edit_grammar()
    results: dict[str, str] = {}
    cases = {"reference": frozenset(), "size_excitation": frozenset({SIZE_EXCITATION})}
    cases.update({name: mechanism_defect(name) for name in CONFOUNDED_MECHANISMS})
    for name in sorted(cases):
        edited = grammar.apply(program, cases[name])
        log = edited.execute(SEED, N_EVENTS)
        results[name] = hashlib.sha256(log.to_bytes()).hexdigest()
    return results


def main() -> int:
    for name, digest in digests().items():
        sys.stdout.write(f"{name} {digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
