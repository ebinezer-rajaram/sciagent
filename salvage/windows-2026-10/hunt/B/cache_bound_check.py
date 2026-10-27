"""Defect hunt B, property 6: block_cache_bytes changes no fitted number.

    uv run python .cache/hunt/B/cache_bound_check.py dev-004 dev-008

Fits each truth's own structure (and the truth plus one extra Excite where
possible) on its seed-1 observational data at bounds 0, 64 KiB and default;
compares every FitResult field except wall time.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot import load_split, pointproc_environment, reconfigure_stdout, truth_entries


def main() -> None:
    reconfigure_stdout()
    from sciagent.campaign.execute import observational_data
    from sciagent.glm.fit import FitConfig, fit

    ids = sys.argv[1:] or ["dev-004", "dev-008"]
    records = [
        r for r in load_split(ROOT / "data" / "v2" / "truths" / "dev") if r.id in ids
    ]
    env = pointproc_environment(tuple(records))
    bad = 0
    for t in truth_entries(tuple(records)):
        data = observational_data(t, 1, env)
        outs = {}
        for bound in (0, 1 << 16, FitConfig().block_cache_bytes):
            r = fit(
                t.structure,
                data.observational,
                data.channels,
                config=FitConfig(block_cache_bytes=bound),
            )
            d = {
                f.name: getattr(r, f.name)
                for f in dataclasses.fields(r)
                if f.name != "wall_time"
            }
            outs[bound] = repr(d)
        same = len(set(outs.values())) == 1
        bad += not same
        print(
            f"{t.id} {t.dsl[:60]}: identical across bounds {sorted(outs)}: {same}; FitConfig keys equal: "
            f"{len({FitConfig(block_cache_bytes=b).key() for b in outs}) == 1}"
        )
    print("PASS" if not bad else f"FAIL {bad}")


if __name__ == "__main__":
    main()
