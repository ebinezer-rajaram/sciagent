"""List AG-o python calls whose code could vary between runs, read from the records.

    uv run python scripts/p3_report/sandbox_nondeterminism.py --root .cache/pilot-merged

Rule fixed before reading replay outcomes: a call is a candidate if its code names
a clock (time.time, perf_counter, monotonic, process_time, datetime.now/utcnow,
date.today), unseeded generators (default_rng() with no argument, np.random.<draw>
with no seed call in the same call, random.<draw>), os.urandom, uuid, secrets, or
tempfile names. A stdout digest failure at such a step is the agent's own
nondeterminism; any other failure is a harness replay defect until shown otherwise.

Also prints the sessions with the most python calls. Read-only.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Final

from sciagent.campaign.store import ResultStore
from sciagent.harness.record import SessionRecord

PATTERNS: Final = {
    "clock": (
        r"\btime\.(time|perf_counter|monotonic|process_time)\s*\(|"
        r"\bperf_counter\s*\(|datetime\.(now|utcnow)|date\.today"
    ),
    "rng_noseed": r"default_rng\(\s*\)|\bSeedSequence\(\s*\)",
    "np_random_global": (
        r"\bnp\.random\.(rand|randn|random|choice|normal|uniform|poisson|"
        r"exponential|shuffle|permutation|randint)\b"
    ),
    "py_random": (
        r"(?<![\w.])random\.(random|choice|shuffle|gauss|uniform|randint|sample)\b"
    ),
    "os_entropy": r"os\.urandom|\buuid\b|\bsecrets\b|tempfile\.",
}
#: Sessions listed by python calls, most first.
TOP: Final = 3


def candidate_kinds(code: str) -> list[str]:
    """The rule's kinds that ``code`` matches; a seed call in the same code
    clears the matching global generator."""
    kinds = [name for name, pattern in PATTERNS.items() if re.search(pattern, code)]
    if "np_random_global" in kinds and re.search(r"np\.random\.seed\(", code):
        kinds.remove("np_random_global")
    if "py_random" in kinds and re.search(r"(?<![\w.])random\.seed\(", code):
        kinds.remove("py_random")
    return kinds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    with ResultStore(args.root, read_only=True) as store:
        cells = sorted(
            (
                c
                for c in store.cells()
                if c["key"]["config"].get("kind") == "llm-session"
                and c["key"]["config"]["system"].startswith("AG-o")
            ),
            key=lambda c: str(c["digest"]),
        )
    calls = 0
    hits: list[tuple[object, ...]] = []
    per_session: list[tuple[int, str]] = []
    for c in cells:
        session = c["detail"]["session"]
        config = c["key"]["config"]
        seed = c["key"]["seed"]
        path = args.root / session["record_dir"] / session["record"]
        mine = 0
        for entry in SessionRecord.load(path).of_kind("tool_call"):
            body = entry.body
            if body["name"] != "python":
                continue
            mine += 1
            kinds = candidate_kinds(str(body["args"].get("code", "")))
            if kinds:
                hits.append(
                    (
                        str(c["digest"])[:16],
                        config["system"],
                        config["truth"],
                        seed,
                        body["call"],
                        entry.index,
                        kinds,
                        body.get("is_error"),
                    )
                )
        calls += mine
        per_session.append((mine, f"{config['system']} {config['truth']} s{seed}"))
    print(
        f"AG-o sessions {len(cells)}, python calls {calls}, candidate calls "
        f"{len(hits)}, records with one {len({h[0] for h in hits})}"
    )
    for hit in hits:
        print(*hit)
    print("most python calls:")
    for n, name in sorted(per_session, key=lambda x: (-x[0], x[1]))[:TOP]:
        print(f"  {n} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
