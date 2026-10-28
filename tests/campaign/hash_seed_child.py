"""One campaign of real baseline units into a fresh store, in its own process.

    python tests/campaign/hash_seed_child.py <root> <systems> <truth>

The child of ``test_hash_seed_pair.py``: the parent sets ``PYTHONHASHSEED``,
so string hashing, and with it the iteration order of every set of strings,
differs between two children. It prints ``hash("sciagent")`` so the parent
can see that the hash seeds really differed (the control), then the run's
summary. Not collected by pytest (no ``test_`` prefix); run as a script, so its
directory, and with it ``campaign_support``, is on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from campaign_support import FINGERPRINT, entry, environment, tiny_config

from sciagent.campaign.driver import Campaign, DriverSettings


def main(argv: list[str]) -> int:
    root, systems, truth = Path(argv[0]), tuple(argv[1].split(",")), argv[2]
    campaign = Campaign(
        root,
        environment(),
        tiny_config(seeds=(1,), baselines=systems),
        [entry(truth)],
        DriverSettings(workers=0, poll_s=0.05, run_llm=False),
        fingerprint=FINGERPRINT,
    )
    try:
        summary = campaign.run()
    finally:
        campaign.close()
    print(f"hash {hash('sciagent')}")
    print(f"recorded {sorted(summary.recorded.items())}")
    print(f"bugs {summary.bugs} failed {len(summary.failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
