"""Recompute SPEC §7.1 inputs and the §6.4 controls from the cells alone.

    uv run python scripts/p3_report/independent_recompute.py --root .cache/pilot-merged

A cross-check of ``scripts/pilot_report.py`` for docs/v2/PILOT.md. It reads
``ResultStore.cells()`` and ``truths.json`` beside this file (truth id to
in-dictionary, written out so that this script needs no split reader), and
imports neither ``campaign.analysis`` nor ``scoring.gonogo``. Its own seeded
bootstrap, scipy's Wilcoxon. Read-only.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
from scipy import stats

from sciagent.campaign.store import ResultStore

TRUTHS: Final = Path(__file__).with_name("truths.json")
SEEDS: Final = (1, 2, 3)
#: The B-sym@10F control runs on this seed only (declared, LOG 2026-10-02).
CONTROL_SEED: Final = 1
BOOTSTRAP_SEED: Final = 7
BOOTSTRAP_RESAMPLES: Final = 20_000

type Key = tuple[str, int, str]


def ranked(counts: Counter[Any]) -> dict[str, int]:
    """``counts`` with a printed order that depends on nothing but its content."""
    return {str(k): counts[k] for k in sorted(counts, key=str)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    in_dictionary: dict[str, bool] = json.loads(TRUTHS.read_text(encoding="utf-8"))
    with ResultStore(args.root, read_only=True) as store:
        cells = sorted(store.cells(), key=lambda c: str(c["digest"]))

    readings: dict[Key, dict[str, float]] = {}
    results: dict[Key, dict[str, Any]] = {}
    for c in cells:
        config = c["key"]["config"]
        if config.get("kind") == "llm-session":
            continue
        key = (config["truth"], int(c["key"]["seed"]), config["system"])
        readings[key] = {x: float.fromhex(v) for x, v in c["reading"].items()}
        results[key] = c["detail"]["result"]

    truths = sorted({t for t, _, _ in readings})
    systems = sorted({s for _, _, s in readings})
    print("systems", ranked(Counter(s for _, _, s in readings)))
    key_sets = Counter(tuple(sorted(r)) for r in readings.values())
    print("reading key sets", ranked(key_sets))
    print("status != 1:", sorted(k for k, r in readings.items() if r["status"] != 1.0))
    ll = {k: r["per_event"] for k, r in readings.items() if "per_event" in r}
    print("per_event missing:", sorted(k for k in readings if k not in ll))
    print("-inf units:", sorted(k for k, v in ll.items() if not math.isfinite(v)))

    def gap(k: Key) -> float:
        return ll[k] - ll[(k[0], k[1], "ORACLE")]

    # Control 1.
    margins: dict[str, tuple[float, float, list[float]]] = {}
    for t in truths:
        d = [ll[(t, n, "ORACLE")] - ll[(t, n, "B-lib")] for n in SEEDS]
        margins[t] = (st.fmean(d), st.stdev(d) / math.sqrt(len(d)), d)
    passed = sum(m > se for m, se, _ in margins.values())
    print(
        f"control 1 margin: {passed}/{len(truths)}; min m/se",
        min((m / se, t) for t, (m, se, _) in margins.items()),
    )
    print("  min margin", min((m, t) for t, (m, _, _) in margins.items()))
    oracle = sorted((k, r) for k, r in results.items() if k[2] == "ORACLE")
    print(
        "oracle_underfit values",
        ranked(Counter(str(r.get("oracle_underfit")) for _, r in oracle)),
        "psi source",
        ranked(Counter(str(r.get("oracle_psi_source")) for _, r in oracle)),
        "truth psi on grid",
        ranked(Counter(str(r.get("oracle_truth_psi_on_grid")) for _, r in oracle)),
    )
    print(
        "  search_ll - true_params_ll min",
        min(r["oracle_search_ll"] - r["oracle_true_params_ll"] for _, r in oracle),
    )
    for k, r in oracle:
        if r["oracle_search_ll"] < r["oracle_true_params_ll"]:
            search, true = r["oracle_search_ll"], r["oracle_true_params_ll"]
            kept = (
                r["oracle_truth_psi_ll"]
                if r["oracle_psi_source"] == "truth"
                else search
            )
            print(
                f"  below the true parameters: {k[0]} s{k[1]}: "
                f"search {search:.3f}, true parameters {true:.3f}, "
                f"truth-psi refit {r['oracle_truth_psi_ll']:.3f}, "
                f"kept ({r['oracle_psi_source']}) {kept:.3f}; "
                f"search - true {search - true:.2f}, "
                f"kept - true {kept - true:.2f}, kept - search {kept - search:.2f}"
            )
    oracle_rows = [r for k, r in readings.items() if k[2] == "ORACLE"]
    print(
        "ORACLE certified",
        ranked(Counter(r.get("certified") for r in oracle_rows)),
        "exact",
        ranked(Counter(r.get("exact") for r in oracle_rows)),
    )
    print("ORACLE skipped non-empty", sum(bool(r.get("skipped")) for _, r in oracle))

    # Control 2.
    diffs: dict[str, float] = {}
    for t in truths:
        planted = st.fmean(ll[(t, n, "planted-hint")] for n in SEEDS)
        rand = st.fmean(ll[(t, n, "B-rand")] for n in SEEDS)
        diffs[t] = 0.0 if planted == rand else planted - rand
    print("control 2 d_t:", {t: round(v, 4) for t, v in diffs.items()})
    d_all = np.array([diffs[t] for t in truths])
    ranks = np.where(np.isinf(d_all), np.sign(d_all) * 1e9, d_all)
    p_value = stats.wilcoxon(ranks, alternative="greater", method="exact").pvalue
    print(
        "  wins/losses",
        sum(v > 0 for v in diffs.values()),
        sum(v < 0 for v in diffs.values()),
        "wilcoxon exact greater p",
        p_value,
        "median",
        float(np.median(d_all)),
    )
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    boot = np.median(
        rng.choice(ranks, size=(BOOTSTRAP_RESAMPLES, len(ranks))),
        axis=1,
    )
    print(
        "  own bootstrap 95% CI of median",
        np.percentile(boot, [2.5, 97.5]).round(4),
    )

    # Control 3.
    subset = sorted(t for t in truths if (t, CONTROL_SEED, "B-sym@10F") in readings)
    gaps = {t: gap((t, CONTROL_SEED, "B-sym@10F")) for t in subset}
    print(
        "control 3 truths",
        subset,
        "gaps sorted",
        [round(x, 4) for x in sorted(gaps.values())],
        "median",
        st.median(gaps.values()),
    )
    print("   per truth", {t: round(g, 4) for t, g in gaps.items()})

    # Shares.
    def closes(k: Key) -> bool:
        lo, li, value = ll[(k[0], k[1], "ORACLE")], ll[(k[0], k[1], "B-lib")], ll.get(k)
        if value is None or not math.isfinite(value) or lo <= li:
            return False
        return (value - li) / (lo - li) >= 0.9

    def share(system: str, among: Sequence[str]) -> tuple[int, float, float]:
        recovers: list[float] = []
        close: list[float] = []
        for t in among:
            seeds = [n for n in SEEDS if (t, n, system) in readings]
            if seeds:
                recovers.append(
                    st.fmean(
                        readings[(t, n, system)].get("exact", 0.0) == 1.0 for n in seeds
                    )
                )
                close.append(st.fmean(closes((t, n, system)) for n in seeds))
        return len(recovers), st.fmean(recovers), st.fmean(close)

    for system in systems:
        n, recovers, close = share(system, truths)
        print(f"{system:14s} truths {n:2d} recovers {recovers:.4f} closes {close:.4f}")

    # T4.
    flagged = sum(abs(x) <= margins[t][1] for t in truths for x in margins[t][2])
    print(
        "T4: (truth,seed) with |LL(O)-LL(lib)| <= SE:",
        flagged,
        "; min |diff|/SE",
        min(abs(x) / margins[t][1] for t in truths for x in margins[t][2]),
    )
    out_of_dictionary = [t for t in truths if not in_dictionary[t]]
    n, _, close = share("B-sparse", out_of_dictionary)
    print("B-sparse OOD close", n, close)
    no_structure = Counter(
        k[2] for k, r in readings.items() if r.get("has_structure") == 0.0
    )
    print("no structure, by system:", ranked(no_structure))
    uncertified = Counter(
        k[2] for k, r in readings.items() if r.get("certified") == 0.0
    )
    print("uncertified rows by system:", ranked(uncertified))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
