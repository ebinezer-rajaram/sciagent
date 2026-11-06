"""Write the P3 pilot report, ``docs/v2/PILOT.md``, from the pilot's store.

    uv run python scripts/pilot_report.py --root .cache/pilot

Reads the cells the pilot plan addresses (``sciagent.campaign.analysis``:
rows are selected by address, so an older configuration's cells are never
mixed in), derives gaps against ORACLE and B-lib per (truth, seed), and writes
per-system tables, efficiency curves, the SPEC §6.4 controls, the §7.1
verdict, the LLM cost table and the AG-o transcripts to read. The text between
``<!-- notes -->`` and ``<!-- /notes -->`` in an existing report is
hand-written (smoke findings, observations) and is carried over unchanged.

P3.1 (P4-PROPOSAL §2.2, §2.4): every score is read at its *current* address
(current fitter and metric versions). LLM sessions are found by enumeration
(``--sessions stored``, the default), so the fit/4 sessions are read and
scored by their rescore cells; ``--sessions current`` reads only sessions at
the current address. Void sessions are classified by the Δ8 policy
(``sciagent.campaign.voids``), dropped from scoring and counted in every
table; gap closed within its paired noise is shown as "flagged" (T4); control
1 needs zero ``oracle_underfit`` flags and no ORACLE at ``-inf`` on any
(truth, seed) (LOG 2026-10-27 item (c)); control 2 is the Δ10 rank rule.

    uv run python scripts/pilot_report.py --root .cache/pilot --interim \
        --out <scratch>/PILOT.md

SPEC §2.2: the certified share and the coordinate-optimal share come from each
row's score cell (:func:`row_cells`; a ``Row`` keeps neither, and ``analysis`` is
frozen by the search-miss check's guard). Uncertified fits stay in every number,
as the rules read them, and are flagged wherever an arm's final-fit scores are
printed. A cell's ``certified`` is 1, 0, NaN (no GLM fit) or absent, and only 1
and 0 enter a share.

Prereg 2026-11-05 §2.8: a split holding a truth outside dev is refused until
``reads.json`` exists (:func:`refuse_unread`), since on P4 the first reading of
the store is the miss script's terminal step. No option changes this.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, NamedTuple

from pilot import (
    ROOT,
    campaign_config,
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
#: Session-detail keys only an old-format session cell has (LOG 2026-10-27).
OLD_SESSION_DETAIL: Final = frozenset({"total_cost_usd", "duration_api_ms"})
#: The states of a score cell's ``certified`` reading (:func:`certified_state`).
CERTIFIED: Final = "certified"
UNCERTIFIED: Final = "uncertified"
NOT_A_FIT: Final = "not a GLM fit"
UNRECORDED: Final = "unrecorded"
#: The start of the line a softplus fit carries when its θ could not be converted
#: to the agent's units (``investigation/tools.py``, ``Lab._fit_text``).
SOFTPLUS_NOTE: Final = "note: softplus-link coefficients are reported as fitted"
#: The miss script's reads file (prereg 2026-11-05 §2.8, §2.10). A constant there
#: and here, and not in the store, which that check leaves untouched.
P4_READS: Final = Path(
    "/home/ubuntu/research/records/sciagent/p4-search-misses-F2/reads.json"
)

type RowKey = tuple[str, int, str]


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


def refuse_unread(records: Sequence[Any], reads: Path) -> None:
    """Exit unless every truth of the split is dev or ``reads`` exists.

    The split's records answer for the store: a truth's digest hashes its
    record, ``split`` included, and is in every address read below, so a
    relabelled record reaches no cell. The whole split is asked about, whatever
    ``--ids`` or ``--truths`` select. An unknown split counts as outside dev, as
    in :mod:`sciagent.campaign.freeze`. Only ``reads``'s existence is read.
    """
    from sciagent.campaign.freeze import DEV_SPLIT

    outside = sorted({r.split for r in records if r.split != DEV_SPLIT})
    if outside and not reads.is_file():
        print(
            f"refused: non-dev truths (split {outside}) and no {reads}: the "
            "contrasts stay unread until the miss script's terminal step writes it",
            file=sys.stderr,
        )
        raise SystemExit(2)


def timing_lines(root: Path) -> list[dict[str, Any]]:
    path = root / "timing.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _cpu(rec: Mapping[str, Any], field: str) -> float | None:
    """A CPU figure of a timing line; ``None`` where the line predates CPU
    time (LOG 2026-10-28) or states it absent, never 0."""
    value = rec.get(field)
    return None if value is None else float(value)


class Times(NamedTuple):
    """One kind:system's wall times and, where recorded, its CPU times (s)."""

    wall: list[float]
    cpu: list[float]
    children: list[float]


def timing(root: Path) -> dict[str, Times]:
    """Wall and CPU time per unit, by ``kind:system``, from ``timing.jsonl``.

    A line without ``cpu_s`` (written before CPU time was recorded, or with
    nothing measured) adds to the wall times only.
    """
    out: dict[str, Times] = {}
    for rec in timing_lines(root):
        t = out.setdefault(f"{rec['kind']}:{rec['system']}", Times([], [], []))
        t.wall.append(float(rec["wall_s"]))
        own, children = _cpu(rec, "cpu_s"), _cpu(rec, "cpu_children_s")
        if own is not None:
            t.cpu.append(own)
        if children is not None:
            t.children.append(children)
    return out


def timing_rows(times: Mapping[str, Times]) -> list[list[str]]:
    """The wall-time table's rows: wall, then CPU of the lines that have it."""
    return [
        [
            k,
            str(len(t.wall)),
            fmt(median(t.wall), ".0f"),
            fmt(max(t.wall), ".0f"),
            f"{len(t.cpu)}/{len(t.wall)}",
            fmt(median(t.cpu), ".0f"),
            fmt(max(t.cpu) if t.cpu else None, ".0f"),
            fmt(median(t.children), ".0f"),
        ]
        for k, t in sorted(times.items())
    ]


class PoolCpu(NamedTuple):
    """Pool jobs' CPU in total (s), over the ``of`` lines that carry it."""

    cpu_s: float
    children_s: float
    lines: int
    of: int


def pool_cpu(root: Path) -> PoolCpu:
    """Total CPU of the pool jobs, from their lines' whole-job figures.

    A multi-unit job's figures are on each of its ``job_units`` lines, so each
    line contributes ``job_cpu_s / job_units``. Counts only lines that carry
    ``job_cpu_s``; ``of`` is every pool-job line.
    """
    own = children = 0.0
    lines = of = 0
    for rec in timing_lines(root):
        if rec.get("kind") not in ("baseline", "llm-score"):
            continue
        of += 1
        job = _cpu(rec, "job_cpu_s")
        if job is None:
            continue
        lines += 1
        share = int(rec["job_units"])
        own += job / share
        children += (_cpu(rec, "job_cpu_children_s") or 0.0) / share
    return PoolCpu(own, children, lines, of)


def _part_text(values: Sequence[float | None], total: int, what: str) -> str:
    """The median of the known ``values``, and how many of ``total`` it has."""
    have = [v for v in values if v is not None]
    return fmt(median(have), ".0f") + (
        "" if len(have) == total else f" ({len(have)}/{total} {what})"
    )


def timed_text(values: Sequence[float | None], total: int) -> str:
    """The median of the measured ``values``, and how many of ``total`` it has."""
    return _part_text(values, total, "timed")


def recorded_text(values: Sequence[float | None], total: int) -> str:
    """The median of the SDK counts ``values`` (``None``: not recorded), and
    how many of ``total`` it has."""
    return _part_text(values, total, "recorded")


def session_record_path(root: Path, session: Any, unknown: str) -> Path:
    """The session's stored record under ``root`` (the detail's ``record_dir``
    and ``record``). A cell that names none raises, saying what stays ``unknown``.
    """
    from sciagent.campaign.plan import CampaignError

    detail = session.detail
    if not isinstance(detail.get("record_dir"), str) or not isinstance(
        detail.get("record"), str
    ):
        raise CampaignError(
            f"session cell {session.digest} ({session.system} {session.truth} "
            f"seed {session.seed}) names no record; {unknown}"
        )
    return root / str(detail["record_dir"]) / str(detail["record"])


def counts_recorded(root: Path, session: Any) -> bool:
    """Whether the session's stored record has an SDK ``result`` entry, which
    carries its turn count and token usage (a ``wall_time`` end has none).

    Read from the record under ``root``, never from the outcome or a zero
    reading.
    """
    from sciagent.harness.record import SessionRecord

    path = session_record_path(
        root, session, "its turn and token counts cannot be told recorded or not"
    )
    return bool(SessionRecord.load(path).of_kind("result"))


def shown_softplus_note(root: Path, session: Any) -> bool:
    """Whether a ``fit`` result in the session's record carries the note.

    Only ``fit``: the note is the framework's, and an agent can put the same
    words into a ``notebook`` or ``python`` output itself. A free repeat repeats
    the fit's text, so it counts; a refused fit has no note to show.
    """
    from sciagent.harness.record import SessionRecord

    path = session_record_path(
        root, session, "whether it was shown the softplus note cannot be told"
    )
    return any(
        entry.body["name"] == "fit" and SOFTPLUS_NOTE in entry.body["text"]
        for entry in SessionRecord.load(path).of_kind("tool_call")
    )


def softplus_note_table(root: Path, systems: Sequence[str], tab: Any) -> list[str]:
    """The sessions shown the softplus note, per arm with sessions, and what the
    count means (LOG 2026-11-05, session 34). Void sessions count among the
    sessions shown it; the scored columns are what a contrast reads."""
    rows = []
    for s in systems:
        sess = [x for x in tab.sessions if x.system == s]
        if not sess:
            continue
        shown = [shown_softplus_note(root, x) for x in sess]
        scored = [n for x, n in zip(sess, shown, strict=True) if not x.void.void]
        rows.append(
            [s, str(len(sess)), str(sum(shown)), str(len(scored)), str(sum(scored))]
        )
    return [
        *table(
            [
                "arm",
                "sessions",
                "shown the note",
                "scored sessions",
                "scored, shown the note",
            ],
            rows,
        ),
        f"Sessions with at least one `fit` result reading `{SOFTPLUS_NOTE}`, from "
        "the records. Softplus θ cannot be converted to the agent's time units, so "
        "the rescaled and anonymised conditions are told, and the named condition, "
        "whose units are not converted, is not: C2's time reads include this note "
        "and its names reads do not (LOG 2026-11-05, 2026-11-06).",
        "",
    ]


def session_wall_times(root: Path, sessions: Sequence[Any]) -> dict[str, float]:
    """The driver's wall time (s) of each session, by cell digest
    (:func:`session_times`)."""
    return {d: float(rec["wall_s"]) for d, rec in session_times(root, sessions).items()}


def session_times(root: Path, sessions: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Each session's ``timing.jsonl`` line, by cell digest (LOG 2026-10-27:
    times are not in the session cell): its wall time and, if recorded, CPU.

    A session with no timing line (a crash between its cell and its line) is
    absent from the result, never counted as 0. A session cell that still
    carries ``wall_s`` in its reading was written in the old format, and two
    timing lines for one session are ambiguous; either raises.
    """
    from sciagent.campaign.plan import CampaignError

    wanted: set[str] = set()
    for x in sessions:
        if "wall_s" in x.reading or OLD_SESSION_DETAIL & set(x.detail):
            raise CampaignError(
                f"session cell {x.digest} ({x.system} {x.truth} seed {x.seed}) is "
                "in the old format (times in its reading or detail); this report "
                "reads session wall times from timing.jsonl only"
            )
        wanted.add(x.digest)
    out: dict[str, dict[str, Any]] = {}
    for rec in timing_lines(root):
        digest = rec.get("digest")
        if rec.get("kind") != "llm-session" or digest not in wanted:
            continue
        if digest in out:
            raise CampaignError(
                f"session {digest} has two timing lines in "
                f"{root / 'timing.jsonl'}; its wall time is ambiguous"
            )
        out[digest] = rec
    return out


def row_cells(
    store: Any, env: Any, config: Any, truths: Sequence[Any], *, sessions: str
) -> dict[RowKey, Mapping[str, Any]]:
    """Each row's score cell, by the addresses ``analysis.load_rows`` reads.

    A cell of another configuration has another address and is never read.
    """
    from sciagent.campaign.plan import (
        baseline_key,
        baseline_units,
        llm_units,
        score_key,
        session_key,
    )
    from sciagent.campaign.rescore import stored_sessions

    by_id = {t.id: t for t in truths}
    order = [t.id for t in truths]
    found = stored_sessions(store, env, config, truths) if sessions == "stored" else {}
    out: dict[RowKey, Mapping[str, Any]] = {}
    for u in baseline_units(order, config):
        key = baseline_key(u, by_id[u.truth_id], env, config)
        if store.done(key):
            out[(u.truth_id, u.seed, u.system)] = store.cell(key.digest)
    for u in llm_units(order, config):
        if sessions == "stored":
            skey = found[u][0] if u in found else None
        else:
            skey = session_key(u, by_id[u.truth_id], env, config)
        if skey is None or not store.done(skey):
            continue
        ckey = score_key(u, skey, env, config)
        if store.done(ckey):
            out[(u.truth_id, u.seed, u.system)] = store.cell(ckey.digest)
    return out


def certified_state(reading: Mapping[str, str]) -> str:
    """The state of a cell's ``certified`` reading (hex, as stored).

    A cell without the field says nothing about its fit, so it is
    :data:`UNRECORDED`, never certified. NaN is what a model that is not a GLM
    fit stores (``execute._certified``).
    """
    from sciagent.campaign.plan import CampaignError

    raw = reading.get("certified")
    if raw is None:
        return UNRECORDED
    value = float.fromhex(raw)
    if math.isnan(value):
        return NOT_A_FIT
    if value not in (0.0, 1.0):
        raise CampaignError(f"certified must be 0, 1 or NaN, got {raw!r}")
    return CERTIFIED if value == 1.0 else UNCERTIFIED


def psi_search_of(
    r: Any, detail: Mapping[str, Any], channels: Any, full_grid_max: int
) -> str:
    """``FitResult.psi_search`` of a row's final fit, which no cell stores.

    Exact, not estimated. ``fit``'s ``_search`` branches on the grid size alone,
    the limit is in the cell's address (``FitConfig.key``), and the one final fit
    that takes no search is ORACLE's kept truth-ψ refit.
    """
    from sciagent.campaign.plan import CampaignError
    from sciagent.glm.fit import _Slots
    from sciagent.glm.syntax import parse

    if r.structure is None:
        raise CampaignError(f"{r.system} {r.truth} seed {r.seed}: no structure")
    if detail.get("oracle_psi_source") == "truth":
        return "fixed"
    size = _Slots.of(parse(r.structure, channels)).size
    return "exhaustive" if size <= full_grid_max else "coordinate"


class FitFlag(NamedTuple):
    """One row's SPEC §2.2 flags."""

    state: str
    #: ``FitResult.psi_search``; ``None``: not a certificate's answer.
    psi_search: str | None


def fit_flags(
    rows: Sequence[Any],
    cells: Mapping[RowKey, Mapping[str, Any]],
    channels: Any,
    full_grid_max: int,
) -> dict[RowKey, FitFlag]:
    """Each row's flags from its cell; a row with no cell has none recorded."""
    out: dict[RowKey, FitFlag] = {}
    for r in rows:
        key = (r.truth, r.seed, r.system)
        cell = cells.get(key)
        if cell is None:
            out[key] = FitFlag(UNRECORDED, None)
            continue
        state = certified_state(cell["reading"])
        search = (
            psi_search_of(r, cell["detail"]["result"], channels, full_grid_max)
            if state in (CERTIFIED, UNCERTIFIED)
            else None
        )
        out[key] = FitFlag(state, search)
    return out


def flag_of(r: Any, flags: Mapping[RowKey, FitFlag]) -> FitFlag:
    return flags[(r.truth, r.seed, r.system)]


def uncertified_rows(rows: Sequence[Any], flags: Mapping[RowKey, FitFlag]) -> list[Any]:
    """The rows among ``rows`` whose final fit is uncertified, in a fixed order."""
    found = [r for r in rows if flag_of(r, flags).state == UNCERTIFIED]
    return sorted(found, key=lambda r: (r.system, r.truth, r.seed))


def flagged(text: str, rows: Sequence[Any], flags: Mapping[RowKey, FitFlag]) -> str:
    """``text`` as printed for ``rows``, marked if any of them is uncertified."""
    n = len(uncertified_rows(rows, flags))
    return f"{text} ({n} uncertified)" if n else text


def share_text(part: int, whole: int) -> str:
    """A share and its counts; ``-`` when there is nothing to take it over."""
    return f"{part / whole:.1%} ({part}/{whole})" if whole else "-"


def certification_row(
    name: str, mine: Sequence[Any], flags: Mapping[RowKey, FitFlag]
) -> list[str]:
    """One row of the certification table, over the rows ``mine``."""
    states = [flag_of(r, flags) for r in mine]
    certified = sum(f.state == CERTIFIED for f in states)
    uncertified = sum(f.state == UNCERTIFIED for f in states)
    unrecorded = [r for r in mine if flag_of(r, flags).state == UNRECORDED]
    no_fit = sum(not (r.ok and r.submitted) for r in unrecorded)
    coordinate = sum(
        f.state == CERTIFIED and f.psi_search == "coordinate" for f in states
    )
    return [
        name,
        str(len(mine)),
        str(certified),
        str(uncertified),
        str(sum(f.state == NOT_A_FIT for f in states)),
        f"{len(unrecorded)}" + (f" ({no_fit} with no fit)" if no_fit else ""),
        share_text(certified, certified + uncertified),
        share_text(coordinate, certified + uncertified),
    ]


def certification_lines(
    systems: Sequence[str],
    rows: Sequence[Any],
    flags: Mapping[RowKey, FitFlag],
    full_grid_max: int,
) -> list[str]:
    """The SPEC §2.2 section: both shares per system, and each uncertified fit."""
    body = [
        certification_row(s, mine, flags)
        for s in systems
        if (mine := [r for r in rows if r.system == s])
    ]
    out = [
        "## Fit certification (SPEC §2.2)",
        "",
        "Read from each scored row's `certified` reading: the final fit behind the "
        "row's submitted model, under the current fitter. The fits inside a "
        "search, a selection or a curve are not in the rows. Certified means the "
        "inner solve at the chosen ψ and at every ψ point evaluated is certified: "
        "a certificate in θ. On a grid searched exhaustively, that makes ψ the best "
        "point of the grid; on one searched coordinate-wise, it does not. "
        "Shares are over units (truth x seed), not truths, of the rows reading 0 "
        "or 1. No GLM fit (the reading is NaN: B-np, and B-lib when an "
        "out-of-grammar member won) and unrecorded (no `certified` field) enter "
        "neither side of a share. A no-submission or failed row has no field "
        "because it has no fit, and is counted among the unrecorded as such.",
        "",
        'The coordinate-optimal share is of fits "certified in θ, coordinate-optimal '
        "in ψ\". No cell stores `psi_search`, so it is recomputed from the row's "
        f"structure, exactly: `fit` searches a grid of at most {full_grid_max} points "
        "exhaustively and any larger one coordinate-wise. An uncertified fit and "
        "ORACLE's kept truth-ψ refit (ψ fixed, no search) are not in it, whatever "
        "their grid.",
        "",
    ]
    out += table(
        [
            "system",
            "units",
            "certified",
            "uncertified",
            "no GLM fit",
            "unrecorded",
            "certified share",
            "coordinate-optimal share",
        ],
        [*body, certification_row("all", rows, flags)],
    )
    bad = uncertified_rows(rows, flags)
    if not bad:
        return [*out, "No uncertified fit.", ""]
    out += [
        f"Uncertified fits: {len(bad)}, marked `(n uncertified)` where an arm's "
        "final-fit scores are printed: the per-system and "
        "group tables and the transcript picks. Each fit stays in every number, "
        "as the rules read it. Non-LLM rows among them (the only rows the §7.1 "
        f"verdict reads): {sum(not r.llm for r in bad)}.",
        "",
    ]
    out += [
        f"- {r.system} {r.truth} seed {r.seed}: gap {fmt(r.gap)}, gap closed "
        f"{closed_text(r)}."
        for r in bad
    ]
    return [*out, ""]


def closed_text(r: Any) -> str:
    """A row's gap closed for printing: the ratio, or "flagged" (T4)."""
    if r is None or r.gap_closed is None:
        return "-"
    return "flagged" if r.closed_flagged else f"{r.gap_closed:.2f}"


def planted_text(pc: Any) -> str:
    ci = pc.median_ci
    return (
        f"{pc.wins} wins / {pc.ties} ties / {pc.losses} losses over {pc.n} truths; "
        f"one-sided Wilcoxon signed-rank p = {pc.p_value:.4g}; median difference "
        f"{fmt(ci.median)}, bootstrap 95% CI [{fmt(ci.low)}, {fmt(ci.high)}] "
        f"(-inf ranks lowest). Beside them only: mean over the "
        f"{pc.n - pc.n_infinite} finite truths {fmt(pc.mean_finite)}"
        f"{f', {pc.n_infinite} infinite' if pc.n_infinite else ''}"
    )


def control_1_headline(
    verdict: Any,
    truths: Sequence[str],
    underfit: Any,
    oracle_inf: Sequence[tuple[str, int]],
) -> str:
    """Control 1's label, read from the verdict's own checks of all three parts.

    The margin part is ``verdict.oracle_checks`` (truths with ≥ 2 paired
    finite seeds); a truth failing it is dropped and reported, and none kept
    fails the control, as in ``analysis.verdict``. A truth without the pairs
    is neither kept nor dropped: named as not checkable. With no truth checked
    the label is NOT CHECKABLE, never PASS; any failing part makes it FAIL.
    """
    from sciagent.campaign import analysis
    from sciagent.scoring import gonogo

    checked = [c.truth for c in verdict.oracle_checks]
    unchecked = [t for t in truths if t not in checked]
    not_checkable = (
        f"margin not checkable (< 2 paired finite seeds of ORACLE and B-lib) "
        f"for {unchecked}"
    )
    fail = gonogo.underfit_reason(underfit) + analysis.oracle_inf_reason(oracle_inf)
    if checked and not verdict.kept:
        fail.append(
            f"no truth kept: margin ≤ SE on every checked truth {list(verdict.dropped)}"
        )
    if fail:
        return "**FAIL**: " + "; ".join(
            [*fail, *([not_checkable] if unchecked else [])]
        )
    if not checked:
        return (
            f"**NOT CHECKABLE** (inconclusive): {not_checkable}; the other parts: "
            f"no flag and no -inf on {underfit.checked} units"
        )
    parts = [
        f"margin > SE on {len(verdict.kept)} of {len(truths)} truths",
        *(
            [f"dropped by the preregistered rule: {list(verdict.dropped)}"]
            if verdict.dropped
            else []
        ),
        *([not_checkable + " (inconclusive there)"] if unchecked else []),
        f"no flag and no -inf on {underfit.checked} units",
    ]
    return "**PASS** (" + "; ".join(parts) + ")"


def verdict_line(outcome: str) -> str:
    """The §7.1 headline of a complete store: the rules' output, named as that."""
    return (
        f"**Verdict (computed): {outcome.upper()}.** This is what the rules "
        "compute. It is not the decision: P4-PROPOSAL §2.4 closes P3 on a GO plus "
        "the owner's transcript read."
    )


def control_3_scope(
    kept: Sequence[str], srows: Sequence[Any], *, declared: bool
) -> str:
    """The truths control 3 reads, against the kept ones: the kept truths with a
    B-sym@10F score (``analysis.verdict``), which a declared subset narrows."""
    from sciagent.campaign.plan import B_SYM_10F

    have = {r.truth for r in srows if r.system == B_SYM_10F}
    read = sum(t in have for t in kept)
    which = "its declared subset" if declared else "those with a result"
    return f"{read} of the {len(kept)} kept truths ({which})"


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
        out += [
            "- Planted hint - B-rand per-truth differences: "
            + ", ".join(f"{d:+.3f}" for d in sorted(pc.differences))
            + ".",
            f"- Planted hint - B-rand (rank rule): {planted_text(pc)} (would "
            f"{'pass' if pc.passed else 'fail'}; inconclusive at n = {len(both)}).",
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


def cost_table(root: Path, systems: Sequence[str], tab: Any) -> list[str]:
    """The LLM arms' cost and behaviour table: a row per arm with sessions.

    Turns and tokens come from the SDK's ``result`` message only; a session
    without one has no such reading fields, or 0 in a cell written before
    that (``campaign.llm.run_session``). They are medians over the sessions
    whose record has a ``result`` entry (:func:`counts_recorded`), so both
    forms read alike, with ``(n/total recorded)`` when some have none, and a
    note then says so. Prediction columns read ``analysis.prediction_tally``,
    which leaves policy-arm sessions out (P4 Δ11).
    """
    from sciagent.campaign import analysis

    stimes = session_times(root, tab.sessions)
    crows = []
    unrecorded = 0
    for s in systems:
        sess = [x for x in tab.sessions if x.system == s]
        if not sess:
            continue
        rd = [x.reading for x in sess]
        recorded = [counts_recorded(root, x) for x in sess]
        unrecorded += recorded.count(False)

        def med(field: str, rd: Sequence[Mapping[str, float]] = rd) -> str:
            return fmt(median([r.get(field, 0.0) for r in rd]), ".0f")

        def counted(
            field: str,
            rd: Sequence[Mapping[str, float]] = rd,
            recorded: Sequence[bool] = recorded,
        ) -> str:
            pairs = zip(rd, recorded, strict=True)
            return recorded_text(
                [r.get(field, 0.0) if ok else None for r, ok in pairs], len(rd)
            )

        outcomes: dict[str, int] = {}
        for x in sess:
            o = str(x.detail.get("outcome"))
            outcomes[o] = outcomes.get(o, 0) + 1
        lines = [stimes.get(x.digest, {}) for x in sess]
        wall_text = timed_text([line.get("wall_s") for line in lines], len(sess))
        cpu_text = timed_text([_cpu(line, "cpu_s") for line in lines], len(sess))
        child_text = timed_text(
            [_cpu(line, "cpu_children_s") for line in lines], len(sess)
        )
        tally = analysis.prediction_tally(tab, s)
        if tally.read:
            any_prediction = pct(tally.predicting / tally.read)
            coverage = f"{tally.covered}/{tally.evaluated}"
        else:  # every session is a policy arm's: its predictions are not read
            any_prediction = coverage = "excl."
        crows.append(
            [
                s,
                str(len(sess)),
                ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())),
                counted("turns"),
                med("tool_calls"),
                counted("output_tokens"),
                counted("input_tokens"),
                counted("cache_read_input_tokens"),
                wall_text,
                cpu_text,
                child_text,
                med("fits_used"),
                med("experiments_used"),
                pct(sum(r.get("experiments_used", 0.0) == 0.0 for r in rd) / len(rd)),
                any_prediction,
                str(sum(x.void.void for x in sess)),
                str(sum(x.void.stored_void for x in sess)),
                str(sum(x.void.unknown_tool_attempts for x in sess)),
                coverage,
                f"{tally.excluded} ({tally.excluded_predictions})",
            ]
        )
    out = table(
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
            "cpu s",
            "child cpu s",
            "fits",
            "experiments",
            "no experiment",
            "any prediction",
            "void (policy)",
            "void (stored)",
            "unknown-tool attempts",
            "predictions covered/evaluated",
            "policy (excl.): sessions (predictions)",
        ],
        crows,
    )
    if not unrecorded:
        return out
    return [
        *out,
        f"Turns and tokens: {unrecorded} session(s) ended with no `result` "
        "message from the SDK (e.g. a wall-time end), so their counts were not "
        "recorded; those medians are over the sessions with one, and "
        "`(n/m recorded)` says how many.",
        "",
    ]


def main() -> None:
    from pilot import build_parser, reconfigure_stdout

    parser = build_parser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument(
        "--interim", action="store_true", help="mark the report INTERIM"
    )
    parser.add_argument(
        "--sessions",
        choices=("stored", "current"),
        default="stored",
        help="find LLM sessions by enumeration (rescore) or at the current address",
    )
    reconfigure_stdout()
    args = parser.parse_args()
    if not (args.root / "ledger.sqlite").is_file():
        # A reader: the store is opened read-only below and never created.
        print(f"{args.root}: not a campaign store", file=sys.stderr)
        raise SystemExit(2)

    import numpy as np

    from sciagent.campaign import analysis
    from sciagent.campaign.plan import (
        B_SYM_10F,
        BASELINES,
        METRIC_VERSION,
    )
    from sciagent.campaign.store import ResultStore
    from sciagent.glm.fit import FIT_VERSION
    from sciagent.scoring import gonogo

    records = select(load_split(args.split), args.truths, args.ids)
    all_records = load_split(args.split)
    refuse_unread(all_records, P4_READS)
    env = pointproc_environment(all_records)
    seeds = tuple(int(s) for s in args.seeds.split(","))
    control = tuple(int(s) for s in args.control_seeds.split(",") if int(s) in seeds)
    config = campaign_config(args)
    truths = truth_entries(records)
    with ResultStore(args.root, read_only=True) as store:
        tab = analysis.load_rows(store, env, config, truths, sessions=args.sessions)
        full_grid_max = config.fit_config.psi_full_grid_max
        flags = fit_flags(
            tab.rows,
            row_cells(store, env, config, truths, sessions=args.sessions),
            env.channels,
            full_grid_max,
        )
    rows = tab.rows

    def arm(system: str) -> str:
        """An arm as the tables of its final-fit scores print it (SPEC §2.2)."""
        return flagged(system, [r for r in rows if r.system == system], flags)

    voided = analysis.void_counts(tab)
    session_versions: dict[str, int] = {}
    for x in tab.sessions:
        session_versions[x.env_version] = session_versions.get(x.env_version, 0) + 1
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
        f"- Scores are read at the current address only (`{METRIC_VERSION}`, "
        f"`{FIT_VERSION}`). LLM sessions ({args.sessions}): "
        + (
            ", ".join(
                f"{n} conducted under `{v}`"
                for v, n in sorted(session_versions.items())
            )
            or "none found"
        )
        + ". Sessions conducted under an older fitter saw that fitter's fits; "
        "their submissions are refitted and scored under the current one "
        "(declared, P4-PROPOSAL §2.2).",
        "- Void policy (P4-PROPOSAL Δ8): a session is void only if a foreign tool "
        "executed, on server tool use, an out-of-bounds hit or an infrastructure "
        "fault; a denied unknown tool name is an unknown-tool attempt. Void "
        "sessions are dropped from scoring and counted in every table.",
        "- Gap closed is shown as `flagged` when |LL(ORACLE) - LL(B-lib)| on that "
        "(truth, seed) is within the truth's paired SE of that difference over "
        "seeds (T4); flagged ratios never enter a median or choose a transcript.",
        "",
        "## Coverage",
        "",
    ]
    cov_rows = []
    for s in systems:
        mine = [r for r in rows if r.system == s]
        sess = [x for x in tab.sessions if x.system == s]
        llm = s in llm_systems
        cov_rows.append(
            [
                s,
                str(tab.planned.get(s, 0)),
                str(len(sess)) if llm else "",
                str(voided.get(s, 0)) if llm else "",
                str(sum(x.void.unknown_tool_attempts for x in sess)) if llm else "",
                str(len(mine)),
                str(sum(not r.ok for r in mine)),
                str(tab.missing.get(s, 0)),
            ]
        )
    L += table(
        [
            "system",
            "planned",
            "sessions",
            "void (dropped)",
            "unknown-tool attempts",
            "scored",
            "failed",
            "not run",
        ],
        cov_rows,
    )

    L += ["## Notes", "", NOTES_OPEN, old_notes(args.out), NOTES_CLOSE, ""]

    # -- verdict -----------------------------------------------------------
    L += ["## Go/no-go (SPEC §7.1)", ""]
    if complete:
        L += [verdict_line(verdict.outcome), ""]
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
            [arm(s), pct(verdict.recovery.get(s)), pct(verdict.close.get(s))]
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
    uf = analysis.underfit_check([r for r in rows if not r.llm])
    oracle_inf = analysis.oracle_inf_units([r for r in rows if not r.llm])
    L += [
        "**1. ORACLE beats B-lib** per truth: mean over seeds of gap(ORACLE) - "
        "gap(B-lib) must exceed its paired SE (finite seeds only), **and** zero "
        "`oracle_underfit` flags on every ORACLE unit (P4-PROPOSAL §2.4), "
        "**and** no ORACLE scoring -inf per event on held-out data on any "
        "(truth, seed) (not an upper bound; LOG 2026-10-27 item (c)): "
        + control_1_headline(verdict, sorted(tab.truths), uf, oracle_inf)
        + ".",
        "",
    ]
    by_id = {t.id: t for t in truths}

    def underfit_cell(t: str) -> str:
        flagged = [f"s{s}" for tt, s in uf.flagged if tt == t]
        unrecorded = sum(tt == t for tt, _ in uf.unrecorded)
        parts = [f"flagged {', '.join(flagged)}"] if flagged else []
        if unrecorded:
            parts.append(f"{unrecorded} unrecorded")
        return "; ".join(parts) or "none"

    L += table(
        [
            "truth",
            "stratum",
            "dict",
            "margin (nats/event)",
            "SE",
            "margin pass",
            "oracle_underfit",
        ],
        [
            [
                c.truth,
                by_id[c.truth].stratum,
                "in" if by_id[c.truth].in_dictionary else "out",
                fmt(c.margin),
                fmt(c.se),
                "PASS" if c.passed else "FAIL",
                underfit_cell(c.truth),
            ]
            for c in verdict.oracle_checks
        ],
    )
    if verdict.planted is not None:
        L += [
            "**2. Planted hint beats B-rand** (Δ10 rank rule: one-sided Wilcoxon "
            f"p < {gonogo.CONTROL_2_ALPHA} **and** the median difference's CI "
            "lower end > 0), per-truth mean gap difference planted - B-rand over "
            f"kept truths: {planted_text(verdict.planted)} → "
            f"**{'PASS' if verdict.planted.passed else 'FAIL'}**.",
            "",
        ]
    else:
        L += ["**2. Planted hint beats B-rand**: not computable (inconclusive).", ""]
    if verdict.bsym is not None:
        L += [
            "**3. B-sym at 10xF approaches ORACLE**: median over "
            + control_3_scope(
                verdict.kept,
                analysis.score_rows(rows, tab.truths),
                declared=config.control_truths is not None,
            )
            + " of the "
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
        "its recovery is 0 by construction and its distance is not shown.",
        "",
    ]
    srows = []
    for s in systems:
        m = analysis.summarise(rows, s)
        if not m.units and not voided.get(s):
            continue
        thin = " (thin)" if m.truths < THIN else ""
        srows.append(
            [
                arm(s) + thin,
                str(m.units),
                str(voided.get(s, 0)) if s in llm_systems else "",
                str(m.failed),
                str(m.nonfinite),
                str(m.no_submission),
                str(m.truths),
                pct(m.recovery),
                pct(m.close),
                fmt(m.gap_mean),
                fmt(m.gap_median),
                fmt(m.closed_median, ".2f"),
                str(m.closed_flagged),
                fmt(m.similarity_mean, ".3f"),
                fmt(m.distance_mean, ".3f"),
            ]
        )
    L += table(
        [
            "system",
            "units",
            "void (excl.)",
            "failed",
            "-inf",
            "no submit",
            "truths",
            "recovers",
            "closes",
            "mean gap",
            "median gap",
            "median closed",
            "closed flagged",
            "similarity",
            "distance",
        ],
        srows,
    )

    def grouped(title: str, key: Callable[[str], str], groups: Sequence[str]) -> None:
        nonlocal L
        L += [f"### By {title}", ""]
        header = [
            "system",
            *[f"{g}: recovers / closes / median gap" for g in groups],
            "void (excl.)",
        ]
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
                text = (
                    f"{pct(m.recovery)} / {pct(m.close)} / {fmt(m.gap_median, '.3f')}"
                    f" (n={m.truths}{thin})"
                )
                cells.append(flagged(text, mine, flags))
            if any_rows:
                v = str(voided.get(s, 0)) if s in llm_systems else ""
                out.append([s, *cells, v])
        L += table(header, out)

    grouped("stratum", lambda t: by_id[t].stratum, ("near", "mid", "far"))
    grouped(
        "dictionary membership",
        lambda t: "in" if by_id[t].in_dictionary else "out",
        ("in", "out"),
    )
    L += [f"`*` fewer than {THIN} truths: inconclusive.", ""]

    # -- SPEC §2.2 ----------------------------------------------------------
    L += certification_lines(systems, rows, flags, full_grid_max)

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
            + [str(voided.get(s, 0)) if s in llm_systems else ""]
        )
    L += table(["system", *[f"k={p}" for p in points], "10xF", "void (excl.)"], erows)

    # -- LLM ---------------------------------------------------------------
    L += ["## LLM arms: cost and behaviour (under Max)", ""]
    L += cost_table(args.root, llm_systems, tab)
    L += [
        "Medians per investigation. Prediction coverage is SPEC §4.4 calibration "
        "raw; falsification-seeking, revision and stopping need a belief timeline "
        "the record does not carry explicitly, and are not computed here. "
        "Predictions made in a policy arm are not read (P4 Δ11: the agent did "
        "not choose the experiment); the last column counts what was left out.",
        "",
        "C3 (AG-o against AG-c): AG-o can read a clock through its own code and "
        "AG-c cannot, so the wall limit the prompt states helps the two arms "
        "unequally, and C3 reads with that difference (LOG 2026-11-05).",
        "",
    ]
    L += ["### The softplus note", ""]
    L += softplus_note_table(args.root, llm_systems, tab)
    flagged_runs = [x for x in tab.sessions if x.void.void or x.void.stored_void]
    L += [
        "### Void runs",
        "",
        "Stored = the harness's flag at the time; policy = Δ8, read from the "
        "record. Reclassified runs are scored; void runs are not.",
        "",
    ]
    L += [
        f"- {x.system} {x.truth} seed {x.seed}: "
        + (
            f"**void**: {list(x.void.reasons)}"
            if x.void.void
            else f"not void (reclassified; {x.void.unknown_tool_attempts} denied "
            f"unknown-tool attempt(s)); stored reasons "
            f"{x.detail.get('void_reasons') or x.detail.get('error')}"
        )
        for x in flagged_runs
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

    named = [x for x in ago if config.arm_of(x.system).condition == "named"]
    anon = [x for x in ago if config.arm_of(x.system).condition == "anon"]

    def has_closed(x: Any) -> bool:
        # The corrected rule (T4): a flagged gap closed never chooses a pick.
        r = scored.get((x.truth, x.seed, x.system))
        return r is not None and r.gap_closed is not None and not r.closed_flagged

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
        unit = flagged(
            f"{x.system} {x.truth} seed {x.seed}", [] if r is None else [r], flags
        )
        path = (
            f"{shown(args.root)}/{x.detail.get('record_dir')}/{x.detail.get('record')}"
        )
        L += [
            f"- **{why}**: {unit}, gap closed {closed_text(r)}, exact "
            f"{None if r is None else r.exact}, record `{path}`; submitted "
            f"`{x.detail.get('submission')}` vs truth `{by_id[x.truth].dsl}`."
        ]
    if not picks:
        L += ["- no AG-o session yet"]
    L += [""]

    # -- wall time ------------------------------------------------------------
    L += [
        "## Wall and CPU time per unit (this machine, one BLAS thread per process)",
        "",
    ]
    L += table(
        [
            "unit",
            "n",
            "median s",
            "max s",
            "with cpu",
            "median cpu s",
            "max cpu s",
            "median child cpu s",
        ],
        timing_rows(timing(args.root)),
    )
    pool = pool_cpu(args.root)
    L += [
        "CPU s is the worker's own CPU over the unit's wall-time interval "
        "(`cpu_s`); child cpu s is that of its reaped child processes "
        "(`cpu_children_s`; not the processes inside a Docker container). Lines "
        "written before CPU time was recorded count in n only. Pool jobs in "
        f"total, data simulation included: {pool.cpu_s:.0f} CPU s (children "
        f"{pool.children_s:.0f} s) over {pool.lines} of {pool.of} unit lines.",
        "",
    ]

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
