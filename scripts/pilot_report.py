"""Write the P3 pilot report, ``docs/v2/PILOT.md``, from the pilot's store.

    uv run python scripts/pilot_report.py --root .cache/pilot

Reads the cells the pilot plan addresses (``sciagent.campaign.analysis``:
rows are selected by address, so an older configuration's cells are never
mixed in), derives gaps against ORACLE and B-lib per (truth, seed), and writes
per-system tables, efficiency curves, the SPEC §6.4 controls, the §7.1
verdict, the LLM cost table and the AG-o transcripts to read. The text between
``<!-- notes -->`` and ``<!-- /notes -->`` in an existing report is
hand-written (smoke findings, observations) and is carried over unchanged.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from pilot import (
    ROOT,
    load_split,
    pointproc_environment,
    select,
    truth_entries,
)

OUT: Final = ROOT / "docs" / "v2" / "PILOT.md"
NOTES_OPEN: Final = "<!-- notes -->"
NOTES_CLOSE: Final = "<!-- /notes -->"
#: Fewer truths than this in a cell of a table: the cell is labelled thin.
THIN: Final = 5


def fmt(x: float | None, spec: str = ".4f") -> str:
    return "-" if x is None else format(x, spec)


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return [*out, ""]


def shown(path: Path) -> str:
    return (path.relative_to(ROOT) if path.is_relative_to(ROOT) else path).as_posix()


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def old_notes(path: Path) -> str:
    if not path.exists():
        return "_No notes yet._"
    text = path.read_text(encoding="utf-8")
    if NOTES_OPEN in text and NOTES_CLOSE in text:
        return text.split(NOTES_OPEN, 1)[1].split(NOTES_CLOSE, 1)[0].strip()
    return "_No notes yet._"


def timing(root: Path) -> dict[str, list[float]]:
    path = root / "timing.jsonl"
    out: dict[str, list[float]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        out.setdefault(f"{rec['kind']}:{rec['system']}", []).append(
            float(rec["wall_s"])
        )
    return out


def interim_controls(tab: Any, resamples: int) -> list[str]:
    """§6.4 controls on completed units only, without the drop rule (interim)."""
    import numpy as np

    from sciagent.campaign import analysis
    from sciagent.scoring import gonogo

    srows = analysis.score_rows(tab.rows, tab.truths)
    have: dict[str, set[str]] = {}
    for r in srows:
        have.setdefault(r.system, set()).add(r.truth)
    out = [
        "### Interim read of the controls (completed units only)",
        "",
        "Not the preregistered read: the ORACLE-beats-B-lib drop rule needs two "
        "seeds per truth and is not applied, and every truth with the units is "
        "used. Inconclusive by construction; shown to see whether the "
        "instrument is heading the right way.",
        "",
    ]
    pairs = []
    for t in sorted(have.get("ORACLE", set()) & have.get("B-lib", set())):
        o = [r.gap for r in srows if r.truth == t and r.system == "ORACLE"]
        b = [r.gap for r in srows if r.truth == t and r.system == "B-lib"]
        pairs.append((t, statistics.fmean(o) - statistics.fmean(b), len(o)))
    if pairs:
        wins = sum(m > 0 for _, m, _ in pairs)
        out += [
            f"- ORACLE - B-lib held-out gap > 0 on {wins} of {len(pairs)} truths "
            "(single seed each; no SE): "
            + ", ".join(f"{t} {m:+.3f}" for t, m, _ in pairs)
            + ".",
        ]
    both = sorted(have.get(gonogo.PLANTED, set()) & have.get(gonogo.B_RAND, set()))
    if len(both) >= 2:
        pc = gonogo.planted_beats_rand(
            srows, both, rng=np.random.default_rng(20261002), resamples=resamples
        )
        idx = {(r.truth, r.system): r.gap for r in srows}
        diffs = sorted(
            statistics.fmean(
                [g for (t2, sy), g in idx.items() if t2 == t and sy == gonogo.PLANTED]
            )
            - statistics.fmean(
                [g for (t2, sy), g in idx.items() if t2 == t and sy == gonogo.B_RAND]
            )
            for t in both
        )
        out += [
            "- Planted hint - B-rand per-truth differences: "
            + ", ".join(f"{d:+.3f}" for d in diffs)
            + f" (median {statistics.median(diffs):+.4f}); a mean is dominated "
            "by any B-rand unit whose best-BIC model predicts held-out data "
            "catastrophically.",
            f"- Planted hint - B-rand over {len(both)} truths: mean "
            f"{fmt(pc.ci.mean)}, bootstrap 95% CI [{fmt(pc.ci.low)}, "
            f"{fmt(pc.ci.high)}] (would {'pass' if pc.passed else 'fail'}; "
            f"inconclusive at n = {len(both)}).",
        ]
    c10 = sorted(have.get(gonogo.B_SYM_10F, set()))
    if c10:
        bc = gonogo.bsym_approaches_oracle(srows, c10)
        out += [
            f"- B-sym@10F median gap over {len(c10)} truths: "
            f"{fmt(bc.median_gap)} (threshold -{gonogo.BSYM_TOLERANCE}; would "
            f"{'pass' if bc.passed else 'fail'}; inconclusive at n = {len(c10)}).",
        ]
    return [*out, ""]


def main() -> None:
    from pilot import build_parser, reconfigure_stdout

    parser = build_parser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument(
        "--interim", action="store_true", help="mark the report INTERIM"
    )
    reconfigure_stdout()
    args = parser.parse_args()

    import numpy as np

    from sciagent.campaign import analysis
    from sciagent.campaign.plan import B_SYM_10F, BASELINES, CampaignConfig
    from sciagent.campaign.store import ResultStore
    from sciagent.scoring import gonogo

    records = select(load_split(args.split), args.truths, args.ids)
    all_records = load_split(args.split)
    env = pointproc_environment(all_records)
    seeds = tuple(int(s) for s in args.seeds.split(","))
    control = tuple(int(s) for s in args.control_seeds.split(",") if int(s) in seeds)
    config = CampaignConfig(
        seeds=seeds,
        control_seeds=control,
        max_turns=args.max_turns,
        control_truths=args.b10f_truths,
    )
    truths = truth_entries(records)
    with ResultStore(args.root) as store:
        tab = analysis.load_rows(store, env, config, truths)
    rows = tab.rows
    llm_systems = config.llm_systems()
    systems = [*BASELINES, *llm_systems]
    verdict = analysis.verdict(
        tab, np.random.default_rng(20261002), resamples=args.resamples
    )

    baseline_missing = sum(tab.missing.get(s, 0) for s in BASELINES)
    complete = baseline_missing == 0
    L: list[str] = []
    L += ["# P3 pilot report" + (" (INTERIM)" if args.interim else ""), ""]
    if args.interim or not complete:
        L += [
            f"**INTERIM: the pilot is paused/incomplete; {baseline_missing} "
            "non-LLM units and "
            f"{sum(tab.missing.get(s, 0) for s in llm_systems)} LLM units have "
            "not run. Every number below is over completed units only; read "
            "the coverage table first.**",
            "",
        ]
    L += [
        "Generated by `scripts/pilot_report.py` from the pilot store "
        f"(`{shown(args.root)}`). "
        "Do not edit outside the notes block; rerun the script.",
        "",
        f"- Split: `{shown(args.split)}`, "
        f"{len(truths)} truths ({sum(t.in_dictionary for t in truths)} in-dictionary, "
        f"{sum(not t.in_dictionary for t in truths)} out-of-dictionary; strata "
        + ", ".join(
            f"{s} {sum(t.stratum == s for t in truths)}" for s in ("near", "mid", "far")
        )
        + ").",
        f"- Seeds {list(seeds)}; B-sym@10F on seeds {list(control)} only"
        + (
            ""
            if config.control_truths is None
            else f" and on the first {config.control_truths} truths in split "
            f"order only ({', '.join(t.id for t in truths[: config.control_truths])})"
        )
        + " (declared: the 10xF positive control runs on this subset to save "
        "compute).",
        f"- Budgets: F = {config.fits} fits, E = {config.experiments} experiments "
        f"(LLM arms), max {config.max_turns} turns, wall {config.wall_time_s:.0f} s; "
        f"LLM model `{config.llm_model}`.",
        "- Non-LLM systems see the observational log only (the same log the LLM arms "
        "start from: the investigation's world for that seed), never experiments.",
        "- Held-out per-event log-likelihood on one fresh 2,000-unit log per "
        "(truth, seed), shared by every system on it. gap = LL - LL(ORACLE); "
        "gap closed = (LL - LL(B-lib)) / (LL(ORACLE) - LL(B-lib)). Recovery = exact "
        "canonical match (LOG 2026-10-02).",
        "",
        "## Coverage",
        "",
    ]
    cov_rows = []
    for s in systems:
        mine = [r for r in rows if r.system == s]
        sess = [x for x in tab.sessions if x.system == s]
        cov_rows.append(
            [
                s,
                str(tab.planned.get(s, 0)),
                str(len(sess)) if s in llm_systems else "",
                str(len(mine)),
                str(sum(not r.ok for r in mine)),
                str(tab.missing.get(s, 0)),
            ]
        )
    L += table(
        ["system", "planned", "sessions", "scored", "failed", "not run"], cov_rows
    )

    L += ["## Notes", "", NOTES_OPEN, old_notes(args.out), NOTES_CLOSE, ""]

    # -- verdict -----------------------------------------------------------
    L += ["## Go/no-go (SPEC §7.1)", ""]
    if complete:
        L += [f"**Verdict: {verdict.outcome.upper()}**", ""]
    else:
        L += [
            "**Verdict: PENDING.** The §7.1 read needs every non-LLM system on "
            "every planned (truth, seed). The read below is computed on the "
            f"completed units only (it would say: {verdict.outcome}) and is "
            "not a verdict.",
            "",
        ]
    L += [
        f"- Truths kept after the ORACLE-beats-B-lib rule: {len(verdict.kept)}; "
        f"dropped: {list(verdict.dropped) or 'none'}.",
        f"- Controls pass: {verdict.controls_pass}; too easy: {verdict.too_easy}; "
        f"too hard: {verdict.too_hard}.",
    ]
    L += [f"- {r}" for r in verdict.reasons]
    L += [f"- Inconclusive: {r}" for r in verdict.inconclusive]
    L += [
        "",
        "Shares over kept truths (each truth weighted equally; failed units count "
        "as neither recovering nor closing):",
        "",
    ]
    L += table(
        ["system", "recovers (exact)", "closes (≥ 0.9)"],
        [
            [s, pct(verdict.recovery.get(s)), pct(verdict.close.get(s))]
            for s in BASELINES
            if s != "ORACLE"
        ],
    )
    L += [
        f"B-sparse closes the gap on {pct(verdict.sparse_ood_close)} of the kept "
        "out-of-dictionary truths (too-easy threshold "
        f"{gonogo.TOO_EASY_SHARE:.0%}).",
        "",
    ]

    # -- controls ----------------------------------------------------------
    L += ["## Positive controls (SPEC §6.4)", ""]
    L += [
        "**1. ORACLE beats B-lib** per truth: mean over seeds of gap(ORACLE) - "
        "gap(B-lib) must exceed its paired SE.",
        "",
    ]
    by_id = {t.id: t for t in truths}
    L += table(
        ["truth", "stratum", "dict", "margin (nats/event)", "SE", "pass"],
        [
            [
                c.truth,
                by_id[c.truth].stratum,
                "in" if by_id[c.truth].in_dictionary else "out",
                fmt(c.margin),
                fmt(c.se),
                "PASS" if c.passed else "FAIL",
            ]
            for c in verdict.oracle_checks
        ],
    )
    if verdict.planted is not None:
        ci = verdict.planted.ci
        L += [
            "**2. Planted hint beats B-rand**: paired bootstrap 95% CI of the "
            "per-truth mean gap difference (planted - B-rand) over kept truths: "
            f"mean {fmt(ci.mean)}, CI [{fmt(ci.low)}, {fmt(ci.high)}], n = {ci.n} → "
            f"**{'PASS' if verdict.planted.passed else 'FAIL'}**.",
            "",
        ]
    else:
        L += ["**2. Planted hint beats B-rand**: not computable (inconclusive).", ""]
    if verdict.bsym is not None:
        L += [
            "**3. B-sym at 10xF approaches ORACLE**: median over kept truths of the "
            f"per-truth gap = {fmt(verdict.bsym.median_gap)} (pass if ≥ "
            f"-{gonogo.BSYM_TOLERANCE}) → "
            f"**{'PASS' if verdict.bsym.passed else 'FAIL'}** "
            f"(seeds {list(control)} only).",
            "",
        ]
    else:
        L += ["**3. B-sym at 10xF**: no results (inconclusive).", ""]

    # -- per-system ---------------------------------------------------------
    if not complete:
        L += interim_controls(tab, args.resamples)
    L += ["## Per-system scores (all truths)", ""]
    L += [
        "Recovery and close shares are truth-weighted; gaps and similarity are over "
        "units (truth x seed) that produced a score. B-np proposes no structure, so "
        "its recovery is 0 by construction and its distance is not shown. B-sparse "
        "often submits `Gate(Excite, PhaseWindow)` for a `Product(Excite, Periodic)` "
        "truth, a predictive equivalent: read its gap closed beside its exact "
        "recovery.",
        "",
    ]
    srows = []
    for s in systems:
        m = analysis.summarise(rows, s)
        if not m.units:
            continue
        thin = " (thin)" if m.truths < THIN else ""
        srows.append(
            [
                s + thin,
                str(m.units),
                str(m.failed),
                str(m.nonfinite),
                str(m.no_submission),
                str(m.truths),
                pct(m.recovery),
                pct(m.close),
                fmt(m.gap_mean),
                fmt(m.gap_median),
                fmt(m.closed_median, ".2f"),
                fmt(m.similarity_mean, ".3f"),
                fmt(m.distance_mean, ".3f"),
            ]
        )
    L += table(
        [
            "system",
            "units",
            "failed",
            "-inf",
            "no submit",
            "truths",
            "recovers",
            "closes",
            "mean gap",
            "median gap",
            "median closed",
            "similarity",
            "distance",
        ],
        srows,
    )

    def grouped(title: str, key: Callable[[str], str], groups: Sequence[str]) -> None:
        nonlocal L
        L += [f"### By {title}", ""]
        header = ["system"] + [f"{g}: recovers / closes / median gap" for g in groups]
        out = []
        for s in systems:
            cells = []
            any_rows = False
            for g in groups:
                mine = [r for r in rows if r.system == s and key(r.truth) == g]
                if mine:
                    any_rows = True
                m = analysis.summarise(mine, s)
                thin = "*" if 0 < m.truths < THIN else ""
                cells.append(
                    f"{pct(m.recovery)} / {pct(m.close)} / {fmt(m.gap_median, '.3f')}"
                    f" (n={m.truths}{thin})"
                )
            if any_rows:
                out.append([s, *cells])
        L += table(header, out)

    grouped("stratum", lambda t: by_id[t].stratum, ("near", "mid", "far"))
    grouped(
        "dictionary membership",
        lambda t: "in" if by_id[t].in_dictionary else "out",
        ("in", "out"),
    )
    L += [f"`*` fewer than {THIN} truths: inconclusive.", ""]

    # -- efficiency ---------------------------------------------------------
    L += [
        "## Efficiency (SPEC §4.3 score 4)",
        "",
        "Median over units of the best-so-far held-out gap (the system's own "
        "BIC-best model after k fits) at k fits; n = units with a model by then. "
        "LLM arms: each distinct charged fit's structure refitted on the "
        "observational log, best-so-far by BIC (the baselines' rule), so the curve "
        "is the agent's proposals under the baselines' selection.",
        "",
    ]
    points = analysis.EFFICIENCY_POINTS
    erows = []
    for s in systems:
        pts = points + (analysis.CONTROL_POINTS if s == B_SYM_10F else ())
        eff = analysis.efficiency(rows, s, pts)
        if not any(n for _, _, n in eff):
            continue
        erows.append(
            [s]
            + [f"{fmt(m, '.4f')} ({n})" for _, m, n in eff[: len(points)]]
            + (
                [", ".join(f"{p}: {fmt(m, '.4f')}" for p, m, _ in eff[len(points) :])]
                if s == B_SYM_10F
                else [""]
            )
        )
    L += table(["system", *[f"k={p}" for p in points], "10xF"], erows)

    # -- LLM ---------------------------------------------------------------
    L += ["## LLM arms: cost and behaviour (under Max)", ""]
    crows = []
    for s in llm_systems:
        sess = [x for x in tab.sessions if x.system == s]
        if not sess:
            continue
        rd = [x.reading for x in sess]

        def med(field: str, rd: Sequence[Mapping[str, float]] = rd) -> str:
            return fmt(median([r.get(field, 0.0) for r in rd]), ".0f")

        outcomes: dict[str, int] = {}
        for x in sess:
            o = str(x.detail.get("outcome"))
            outcomes[o] = outcomes.get(o, 0) + 1
        evaluated = sum(r.get("predictions_evaluated", 0.0) for r in rd)
        covered = sum(r.get("predictions_covered", 0.0) for r in rd)
        crows.append(
            [
                s,
                str(len(sess)),
                ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())),
                med("turns"),
                med("tool_calls"),
                med("output_tokens"),
                med("input_tokens"),
                med("cache_read_input_tokens"),
                fmt(median([r.get("wall_s", 0.0) for r in rd]), ".0f"),
                med("fits_used"),
                med("experiments_used"),
                pct(sum(r.get("experiments_used", 0.0) == 0.0 for r in rd) / len(rd)),
                pct(sum(r.get("predictions", 0.0) > 0.0 for r in rd) / len(rd)),
                str(int(sum(r.get("void", 0.0) for r in rd))),
                f"{int(covered)}/{int(evaluated)}",
            ]
        )
    L += table(
        [
            "arm",
            "sessions",
            "outcomes",
            "turns",
            "tool calls",
            "out tokens",
            "in tokens",
            "cache read",
            "wall s",
            "fits",
            "experiments",
            "no experiment",
            "any prediction",
            "void",
            "predictions covered/evaluated",
        ],
        crows,
    )
    L += [
        "Medians per investigation. Prediction coverage is SPEC §4.4 calibration "
        "raw; falsification-seeking, revision and stopping need a belief timeline "
        "the record does not carry explicitly, and are not computed here.",
        "",
    ]
    voids = [x for x in tab.sessions if x.reading.get("void", 0.0) == 1.0]
    L += ["### Void runs", ""]
    L += [
        f"- {x.system} {x.truth} seed {x.seed}: "
        f"{x.detail.get('void_reasons') or x.detail.get('error')}"
        for x in voids
    ] or ["- none"]
    L += [""]

    # -- transcripts ---------------------------------------------------------
    L += ["### AG-o transcripts to read in full", ""]
    scored = {(r.truth, r.seed, r.system): r for r in rows if r.llm}
    ago = [
        x
        for x in tab.sessions
        if x.system.startswith("AG-o") and (x.truth, x.seed, x.system) in scored
    ]
    picks: list[tuple[str, Any]] = []

    def pick(why: str, candidates: Sequence[Any], key: Callable[[Any], float]) -> None:
        chosen = {id(p) for _, p in picks}
        pool = [c for c in candidates if id(c) not in chosen]
        if pool:
            picks.append((why, max(pool, key=key)))

    def closed(x: Any) -> float:
        r = scored.get((x.truth, x.seed, x.system))
        return -1e9 if r is None or r.gap_closed is None else r.gap_closed

    named = [x for x in ago if x.system.endswith("/named")]
    anon = [x for x in ago if x.system.endswith("/anon")]

    def has_closed(x: Any) -> bool:
        r = scored.get((x.truth, x.seed, x.system))
        return r is not None and r.gap_closed is not None

    pick("best gap closed (named)", [x for x in named if has_closed(x)], closed)
    pick(
        "worst gap closed (named)",
        [x for x in named if has_closed(x)],
        lambda x: -closed(x),
    )
    pick(
        "most sandbox use",
        ago,
        lambda x: float(x.detail.get("tool_calls", {}).get("python", 0)),
    )
    pick("best gap closed (anonymised)", [x for x in anon if has_closed(x)], closed)
    for why, x in picks:
        r = scored.get((x.truth, x.seed, x.system))
        path = (
            f"{shown(args.root)}/{x.detail.get('record_dir')}/{x.detail.get('record')}"
        )
        L += [
            f"- **{why}**: {x.system} {x.truth} seed {x.seed}, gap closed "
            f"{fmt(None if r is None else r.gap_closed, '.2f')}, exact "
            f"{None if r is None else r.exact}, record `{path}`; submitted "
            f"`{x.detail.get('submission')}` vs truth `{by_id[x.truth].dsl}`."
        ]
    if not picks:
        L += ["- no AG-o session yet"]
    L += [""]

    # -- wall time ------------------------------------------------------------
    L += ["## Wall time per unit (this machine, one BLAS thread per process)", ""]
    tm = timing(args.root)
    L += table(
        ["unit", "n", "median s", "max s"],
        [
            [k, str(len(v)), fmt(median(v), ".0f"), fmt(max(v), ".0f")]
            for k, v in sorted(tm.items())
        ],
    )

    L += ["## Truths", ""]
    L += table(
        ["truth", "stratum", "dict", "nearest d", "DSL"],
        [
            [
                t.id,
                t.stratum,
                "in" if t.in_dictionary else "out",
                fmt(t.nearest_distance, ".3f"),
                f"`{t.dsl}`",
            ]
            for t in truths
        ],
    )
    args.out.write_text("\n".join(L).rstrip() + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}; verdict {verdict.outcome}")


if __name__ == "__main__":
    main()
