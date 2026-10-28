"""A real baseline unit through the campaign driver under different hash seeds
gives byte-identical cells (CLAUDE.md invariant 3: no dict/set iteration-order
dependence in output).

Hunt B's "byte-identical across hash seeds" (STATUS-2026-10-05) was checked on
the report; this is the same check on the cells themselves. Each run is a fresh
process (``hash_seed_child.py``) with its own ``PYTHONHASHSEED`` writing its own
store, and the stores are compared by :func:`sciagent.campaign.audit.compare_stores`,
the code path of ``scripts/store_compare.py``.

The unit is B-lib on ``hawkes``, seed 1: it fits every library member, picks
one, and scores it on held-out data, so the fitter, the selection among fitted
structures and the scorer all run, besides the driver's planning, keying and
store writes. It was the cheapest system measured here that does all of that
(one run on dm-devbox under load: B-lib 16 s, ORACLE 28 s, B-np 18 s but it
fits no structure; B-rand ran past 10 minutes and was stopped). B-rand, B-sym
and planted-hint, which sample from the prior or run a GP search, are not
covered: they would multiply this test's cost.

The control: each child prints ``hash("sciagent")``, and the test requires
the three values to differ, so the hash seeds demonstrably took effect.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sciagent.campaign.audit import compare_stores

CHILD = Path(__file__).resolve().parent / "hash_seed_child.py"
HASH_SEEDS = ("0", "1", "random")
SYSTEMS = "B-lib"
TRUTH = "hawkes"


def _run(root: Path, hash_seed: str) -> dict[str, str]:
    """One child run into ``root``; its printed lines, by first word."""
    done = subprocess.run(
        [sys.executable, str(CHILD), str(root), SYSTEMS, TRUTH],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr[-4000:]
    return dict(line.split(" ", 1) for line in done.stdout.splitlines())


@pytest.mark.slow
def test_a_baseline_unit_is_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    roots = [tmp_path / f"hash-{seed}" for seed in HASH_SEEDS]
    outs = [_run(root, seed) for root, seed in zip(roots, HASH_SEEDS, strict=True)]
    for out in outs:
        assert out["recorded"] == "[('B-lib', 1)]", out
        assert out["bugs"] == "0 failed 0", out
    hashes = [out["hash"] for out in outs]
    assert len(set(hashes)) == len(HASH_SEEDS), hashes
    for root in roots[1:]:
        diff = compare_stores(roots[0], root)
        assert diff.identical, (root.name, diff)
        assert diff.same == 1, (root.name, diff)
