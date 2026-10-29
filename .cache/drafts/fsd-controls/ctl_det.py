"""Pre-F determinism unit (criteria.md v8 §7): one run of ``determinism_unit``.

It draws on the probe-only id ``ctl-det`` (no gate token needed). Run twice, in
separate processes, and compare the outputs with ``cmp``:

    cd /tmp && env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \\
      PYTHONPYCACHEPREFIX=$(mktemp -d) nice -n 10 \\
      uv run --project $WT --no-sync python -I $D/ctl_det.py OUT
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    sys.dont_write_bytecode = True
    sys.path.append(str(Path(__file__).resolve().parent))
    import controls

    controls.determinism_unit(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
