"""The random arm's fixed picks (policies.py:968-972; no truth id), seeds 101-115 and 301-315.
Run: uv run --project $WT --no-sync python -I randpicks.py > random-picks.txt"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from power2 import LABELS, SIGN, random_picks

for lo in (101, 301):
    print(f"== seeds {lo}-{lo + 14}: (design index, label) for decisions 0-7 ==")
    for s in range(lo, lo + 15):
        print(s, random_picks(s))
    for lab in [*SIGN, "mean_rate"]:
        print(
            f"  hits on {lab}: {[s for s in range(lo, lo + 15) if lab in {x for _, x in random_picks(s)}]}"
        )
