"""CPU cost of one He2 table (8 designs x 35 diagnostics) built from stored values by index subsets,
as calibration bootstraps, the Delta_pred simulation and scoring do. Synthetic seeded values; no
simulation. Run: nice -n 10 uv run --project $WT --no-sync python -I bench.py > bench-out.txt"""

import time
import numpy as np
from sciagent.scoring.battery import hellinger2
from sciagent.scoring.seeds import TELLING, generator, scorer_seed

rng = generator(scorer_seed(TELLING, "bench", 0))
store = {
    (e, s): rng.normal(size=(256, 35)).tolist() for e in range(8) for s in range(2)
}


def table(ia, ib):
    return [
        [
            hellinger2(
                [store[(e, 0)][r][d] for r in ia], [store[(e, 1)][r][d] for r in ib]
            )
            for d in range(35)
        ]
        for e in range(8)
    ]


for k, n in ((32, 60), (16, 60)):
    t0 = time.process_time()
    for _ in range(n):
        idx = rng.choice(256, k, replace=False).tolist()
        table(idx, idx)
    per = (time.process_time() - t0) / n
    print(f"R={k}: {per * 1000:.1f} ms CPU per 8x35 table")
