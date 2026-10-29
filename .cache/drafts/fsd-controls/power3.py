"""(ii) clustered power at N=15 and N=24 for the reference cases. Run with the worktree interpreter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from power2 import pass_ii

cases = {
    "p32 .95/.05, p16 .84/.12": ({"x": 0.95, "y": 0.05}, {"x": 0.84, "y": 0.12}),
    "p32 .8/.2, p16 1/0": ({"x": 0.8, "y": 0.2}, {"x": 1.0, "y": 0.0}),
    "p32 1, p16 .75": ({"x": 1.0}, {"x": 0.75}),
}
for n in (15, 24):
    for k, (a, b) in cases.items():
        print(f"N={n} {k}: P(ii passes, >= 0.7 of 8N) = {pass_ii(n, a, b):.3f}")
