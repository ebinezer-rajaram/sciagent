"""The section 10 note of the frozen pilot miss check, from its records.

    uv run python scripts/p3_report/miss_note.py --records <directory>

The rule is ``docs/v2/prereg/2026-11-04_pilot-search-misses.md``; a section
number below means that file. This reads ``decision.json``, ``units.jsonl`` and
``timing.jsonl`` from ``--records`` and prints Markdown to stdout. It reads no
other file (``journal.jsonl`` above all: section 9), writes nothing and decides
nothing.

**Copied.** Everything ``decision.json`` holds is copied, the label and its
wording included. No branch here depends on which label it is. A field that is
not there prints as "not recorded" and is listed at the end of the note, and a
float prints as the shortest decimal that gives the same float back.

**Computed.** Section 4's exploratory table only, from ``units.jsonl``, and the
sums of ``timing.jsonl``. The table's terms are fixed here, before the numbers
exist, because the text does not spell them out:

- A miss is a row with ``call`` equal to ``miss``, on every system, LLM rows
  included. Fits are read as the script's ``fit_views`` reads them, so a job
  that died in R2 keeps R1's result.
- The bands of G are the text's own edges: R2's 14,400 (section 2.2), and
  24,000 and 3,456,000 (section 2.7).
- Sharing of ``period`` slots is read twice. A structure has 0, 1, or 2 or more
  ``period`` slots. Each slot has its own index on one grid, so slots share a
  period at a point when two of them hold the same index, and for miss rows on
  fits with 2 or more slots the table gives that at the stored point and at the
  chosen point.
- Same (truth, seed, structure) across systems means one fit, by its stored
  text (section 2.1).

Tallies are counts: no rate and no ordering by size, since no difference
between systems is claimed (section 4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sciagent.glm.grammar import psi_slots

# ``parse`` also validates against a run's channels, which no record carries.
# Only the slot order is wanted here, and that needs the text alone.
from sciagent.glm.syntax import DslSyntaxError, _Parser

type Json = Any

NOT_RECORDED: Final = "not recorded"
DECISION: Final = "decision.json"
UNITS: Final = "units.jsonl"
TIMING: Final = "timing.jsonl"
#: Every line of ``units.jsonl`` has these (``write_units`` keys on them).
UNIT_KEYS: Final = ("type", "job")
ORACLE: Final = "ORACLE"
S3_BASE: Final = "s3://delaymargin-results-482913/sciagent/p3-linux"

# --------------------------------------------------------------------------
# Fixed words, as the frozen text has them (the test holds them to the file)
# --------------------------------------------------------------------------

NOT_COVERED_HEADER: Final = "| System | Step that used fit/5 | Handling |"
NOT_COVERED_RULE: Final = "|---|---|---|"
NOT_COVERED_ROWS: Final = (
    "| B-rand | BIC over 40 proposals, drawn with no fit result | **Not covered.** "
    "It could be redone in principle, but B-rand's recorded CPU was 36.84 hours and "
    "the reference is several times fit/5's points, so hundreds of CPU hours "
    "(section 8) |",
    "| B-sym, B-sym@10F, planted-hint | BIC selection and the GP's fitness at every "
    "generation | **Not covered, and cannot be by substitution.** A better fit "
    "changes the population and every later proposal, so it is a different run |",
    "| LLM arms | The agent read fit/5's numbers during its session | **Not covered** "
    "(section 2.6) |",
)
STATED_PLAINLY: Final = (
    "Stated plainly: for B-rand, B-sym, B-sym@10F, planted-hint and the LLM arms, the "
    'corrected reading answers "given the structure this system submitted, what does '
    'a better fit of it score?". It does not answer "what would it have submitted?". '
    "The direction of the omitted effect is unknown for scores. For recovery, `exact` "
    "depends on the structure only, so substitution moves no system's `exact` flag. A "
    "recovery share can move only through which truths are kept: a substituted ORACLE "
    "reading can change `kept` (`analysis.py:601`), and recovery is computed over kept "
    "truths (`analysis.py:685-689`). That is the only route by which the too-easy "
    "clause on recovery can react to this check, for B-rand or B-lib."
)
UNKNOWNS: Final = (
    "**What stays unknown either way:** misses R1 did not find on the 140 searched "
    "fits; everything in the not-covered rows of section 2.5; the curves; and sampling "
    "noise, since each reading is one held-out log per (truth, seed)."
)
CURVES: Final = (
    "PILOT.md's curve tables therefore stay unchecked, and the report says so."
)
NO_DIFFERENCE: Final = (
    "Per-system tallies are shown as counts only. No difference between systems is "
    "claimed, and no control supports one."
)
STORED_STAY: Final = (
    "Stored readings, the stored verdict and PILOT.md's generated sections stay as "
    "they are. A corrected reading is never presented as the pilot's reading."
)
PROVISIONAL: Final = (
    "The label at the end of tier 1 is **provisional** until the run ends, because a "
    "byte failure later still gives `BYTE_FAIL`."
)

# --------------------------------------------------------------------------
# What the note lays out
# --------------------------------------------------------------------------

#: Upper edge of each band of G (``None``: no edge), and its name.
G_BANDS: Final = (
    (14_400, "G ≤ 14,400"),
    (24_000, "14,400 < G ≤ 24,000"),
    (3_456_000, "24,000 < G ≤ 3,456,000"),
    (None, "G > 3,456,000"),
)
#: Section 2.1's classes as ``classify_row`` names them, in the text's order.
CLASSES: Final = (
    ("coordinate", "Coordinate path"),
    ("exhaustive", "Exhaustive, `certified` = 1"),
    ("exhaustive_uncertified", "Exhaustive, `certified` = 0"),
    ("no_search_np", "No ψ search: B-np"),
    ("no_search_lib", "No ψ search: B-lib, out-of-grammar member won"),
    ("no_submission", "No submission"),
    ("failed", "Failed"),
)
#: Each §7.1 statistic ``gate_values`` keeps, in its order.
STATISTICS: Final = (
    ("Outcome", ("outcome",)),
    ("too_easy flag", ("too_easy", "flag")),
    ("too_easy recovery B-lib", ("too_easy", "recovery_b_lib")),
    ("too_easy recovery B-rand", ("too_easy", "recovery_b_rand")),
    ("too_easy B-sparse out-of-dictionary close", ("too_easy", "sparse_ood_close")),
    ("too_hard flag", ("too_hard", "flag")),
    ("too_hard close", ("too_hard", "close")),
    ("C1 kept", ("C1", "kept")),
    ("C1 dropped", ("C1", "dropped")),
    ("C1 underfit passed", ("C1", "underfit_passed")),
    ("C1 ORACLE at -inf", ("C1", "oracle_inf")),
    ("C2 passed", ("C2", "passed")),
    ("C2 wins", ("C2", "wins")),
    ("C2 p value", ("C2", "p_value")),
    ("C2 median", ("C2", "median")),
    ("C3 passed", ("C3", "passed")),
    ("C3 median gap", ("C3", "median_gap")),
    ("Reasons", ("reasons",)),
    ("Inconclusive", ("inconclusive",)),
)
#: Section 4's S3, by ``_s3``'s names.
S3_MEASURES: Final = (
    ("Training gain, nats", "gain_nats"),
    ("Training gain, per event", "gain_per_event"),
    ("Δ", "delta"),
    ("Δ above 0", "delta_positive"),
    ("Δ below 0", "delta_negative"),
    ("Moves to or from -inf", "delta_infinite_move"),
    ("Near ties", "near_ties"),
    ("`uncertified_better`", "uncertified_better"),
    ("`uncertified_better`, identity link", "uncertified_better_identity"),
    ("`capped` fits", "capped"),
    ("Completed starts", "starts_completed"),
    ("`stored_uncertified` rows", "stored_uncertified"),
    ("ORACLE search misses", "oracle_search_misses"),
    ("ORACLE stored search below truth ψ", "oracle_search_below_truth_psi"),
)
#: Asked for by the text, carried by no record, so "not recorded" in every note.
NEVER_RECORDED: Final = (
    "Coverage: capped rows, and fits not applicable (`coverage` has `capped_fits` "
    "and `not_applicable_rows` only)",
    "Section 2.1's split of each class into baseline and LLM rows",
    "The rows behind each clause that moved (`clauses_moved` has the clause and its "
    "consequence only)",
    "S4: each §7.1 statistic's limit",
    "The kernel's CPU count and the run's wall time (`cpu.json`, which this note "
    "does not read)",
    "The LEDGER row, whether the S3 copy was made, and departures from the "
    "pre-registration",
    "Whether anyone read `journal.jsonl` before the run ended (section 9)",
)

_HEX_FLOAT: Final = re.compile(r"-?0x[0-9a-f]+(\.[0-9a-f]+)?p[+-]\d+|-?inf|nan")


def cell(value: Json) -> str:
    """A record's value as text. Floats are ``float.hex`` strings in the records
    (``dumps_record``) and print as their shortest round-trip decimal."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return repr(float.fromhex(value)) if _HEX_FLOAT.fullmatch(value) else value
    if isinstance(value, Mapping):
        return ", ".join(f"{k} {cell(value[k])}" for k in sorted(value)) or "none"
    if isinstance(value, list):
        parts = [f"({cell(v)})" if isinstance(v, list) else cell(v) for v in value]
        return ", ".join(parts) or "none"
    return str(value)


def table(header: Sequence[str], rows: Iterable[Sequence[Json]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(cell(v).replace("|", "\\|") for v in row) + " |")
    return lines


def tally(counts: Mapping[str, int]) -> str:
    """Counts by name, in name order, never by size."""
    return ", ".join(f"{k} {counts[k]}" for k in sorted(counts)) or "none"


class Fields:
    """``decision.json``, read so that what is missing is said and remembered."""

    def __init__(self, decision: Mapping[str, Json]) -> None:
        self.decision = decision
        self.absent: set[str] = set()

    def get(self, *path: str) -> Json:
        """A field, or :data:`NOT_RECORDED`. A null on the way is a value: the
        script writes ``C2`` as null when the verdict has no such gate."""
        value: Json = self.decision
        for depth, name in enumerate(path):
            if value is None:
                return None
            if not isinstance(value, Mapping) or name not in value:
                self.absent.add(".".join(path[: depth + 1]))
                return NOT_RECORDED
            value = value[name]
        return value

    def of(self, row: Mapping[str, Json], where: str, name: str) -> Json:
        """A field of one row of a list."""
        if name not in row:
            self.absent.add(f"{where}[].{name}")
            return NOT_RECORDED
        return row[name]


# --------------------------------------------------------------------------
# Reading the three files
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Lines:
    """One ``.jsonl`` file. A last line with no newline is a torn write, as in
    the script's ``read_records``; any other bad line is left out and said."""

    records: tuple[Mapping[str, Json], ...]
    torn: int
    unreadable: int


def parse_lines(data: bytes, required: tuple[str, ...] = ()) -> Lines:
    *whole, tail = data.split(b"\n")
    records: list[Mapping[str, Json]] = []
    unreadable = 0
    for line in whole:
        try:
            value = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        if isinstance(value, dict) and all(k in value for k in required):
            records.append(value)
        else:
            unreadable += 1
    return Lines(tuple(records), int(bool(tail)), unreadable)


def read_file(root: Path, name: str) -> bytes | None:
    path = root / name
    return path.read_bytes() if path.is_file() else None


def read_decision(data: bytes | None) -> Mapping[str, Json] | None:
    if data is None:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def input_line(name: str, data: bytes | None, *, readable: bool = True) -> str:
    if data is None:
        return f"- `{name}`: absent"
    state = "" if readable else "unreadable, "
    return f"- `{name}`: {state}sha256 {hashlib.sha256(data).hexdigest()}"


def damage_lines(name: str, lines: Lines | None) -> list[str]:
    if lines is None:
        return []
    out = []
    if lines.torn:
        out.append(f"- `{name}`: {lines.torn} torn last line left out")
    if lines.unreadable:
        out.append(f"- `{name}`: {lines.unreadable} unreadable line left out")
    return out


# --------------------------------------------------------------------------
# The exploratory table (section 4)
# --------------------------------------------------------------------------

#: A value of one shape: its place in the table, then its name.
type Bucket = tuple[int, str]

UNKNOWN: Final[Bucket] = (10**9, NOT_RECORDED)


@dataclass(frozen=True)
class FitShape:
    """One fit, with the shape section 4 asks about."""

    job: str
    truth: Json
    seed: Json
    text: Json
    link: Bucket
    features: Bucket
    band: Bucket
    periods: Bucket
    positions: tuple[int, ...] | None
    rows: tuple[Mapping[str, Json], ...]

    @property
    def misses(self) -> tuple[Mapping[str, Json], ...]:
        return tuple(r for r in self.rows if r.get("call") == "miss")


def fit_views(records: Sequence[Mapping[str, Json]]) -> list[Mapping[str, Json]]:
    """Each fit's last stage on record, as the script's ``fit_views``: its
    ``done``, or else R1's result (section 6)."""
    views: dict[str, Mapping[str, Json]] = {
        r["job"]: r for r in records if r["type"] == "done" and r.get("kind") == "fit"
    }
    for r in records:
        if r["type"] == "r1" and "result" in r and r["job"] not in views:
            views[r["job"]] = {**r["result"], "job": r["job"]}
    return [views[job] for job in sorted(views)]


def period_positions(text: str) -> tuple[int, ...] | None:
    """Where the ``period`` slots sit in a point, in fit/5's slot order; ``None``
    for text that does not parse."""
    try:
        structure = _Parser(text).structure()
    except DslSyntaxError:
        return None
    names = [s.name for feature in structure.features for s in psi_slots(feature)]
    return tuple(i for i, name in enumerate(names) if name == "period")


def g_band(size: int) -> str:
    for edge, name in G_BANDS:
        if edge is None or size <= edge:
            return name
    raise ValueError(f"no band holds {size}")


def _band_bucket(size: Json) -> Bucket:
    if not isinstance(size, int):
        return UNKNOWN
    name = g_band(size)
    return ([n for _, n in G_BANDS].index(name), name)


def _period_bucket(positions: tuple[int, ...] | None) -> Bucket:
    if positions is None:
        return UNKNOWN
    return (min(len(positions), 2), ("0", "1", "2 or more")[min(len(positions), 2)])


def fit_shape(view: Mapping[str, Json]) -> FitShape:
    text = view.get("text")
    positions = period_positions(text) if isinstance(text, str) else None
    link = view.get("link")
    features = view.get("n_features")
    return FitShape(
        job=view["job"],
        truth=view.get("truth"),
        seed=view.get("seed"),
        text=text,
        link=(0, link) if isinstance(link, str) else UNKNOWN,
        features=(features, str(features)) if isinstance(features, int) else UNKNOWN,
        band=_band_bucket(view.get("grid_size")),
        periods=_period_bucket(positions),
        positions=positions,
        rows=tuple(view.get("rows") or ()),
    )


def shares_period(point: Json, positions: tuple[int, ...]) -> str:
    """ "yes" if two ``period`` slots hold one index at this point. All of them
    index the one ``period`` grid, so the same index is the same period."""
    if not isinstance(point, list) or not positions or max(positions) >= len(point):
        return NOT_RECORDED
    indices = [point[i] for i in positions]
    return "yes" if len(set(indices)) < len(indices) else "no"


def _stored_point(r0: Mapping[str, Json], system: Json) -> Json:
    """The point behind a row's stored fit: ORACLE reads the kept fit (section
    2.3), every other row the search fit."""
    fit = r0.get("kept" if system == ORACLE else "search")
    return fit.get("point") if isinstance(fit, Mapping) else None


def _shape_rows(fits: Sequence[FitShape], axis: str) -> list[list[Json]]:
    buckets = sorted({getattr(f, axis) for f in fits})
    rows: list[list[Json]] = []
    for bucket in buckets:
        members = [f for f in fits if getattr(f, axis) == bucket]
        systems = Counter(str(r.get("system")) for f in members for r in f.misses)
        rows.append(
            [
                bucket[1],
                len(members),
                sum(bool(f.misses) for f in members),
                sum(systems.values()),
                tally(systems),
            ]
        )
    return rows


def _sharing_rows(
    fits: Sequence[FitShape], r0: Mapping[str, Mapping[str, Json]]
) -> list[list[Json]]:
    counts: dict[tuple[str, str], Counter[str]] = {
        (a, b): Counter() for a in ("yes", "no") for b in ("yes", "no")
    }
    for f in fits:
        if f.positions is None or len(f.positions) < 2:
            continue
        for row in f.misses:
            system = row.get("system")
            stored = _stored_point(r0.get(f.job, {}), system)
            key = (
                shares_period(stored, f.positions),
                shares_period(row.get("chosen_point"), f.positions),
            )
            counts.setdefault(key, Counter())[str(system)] += 1
    fixed = list(counts)[:4]
    rest = sorted(k for k in counts if k not in fixed)
    return [
        [*key, sum(counts[key].values()), tally(counts[key])] for key in (*fixed, *rest)
    ]


def _fit_rows(fits: Sequence[FitShape]) -> list[list[Json]]:
    def order(f: FitShape) -> tuple[str, int, str]:
        seed = f.seed if isinstance(f.seed, int) else -1
        return (cell(f.truth), seed, cell(f.text))

    rows: list[list[Json]] = []
    for f in sorted((f for f in fits if f.misses), key=order):
        names = sorted(str(r.get("system")) for r in f.misses)
        others = sorted(
            f"{r.get('system')} {r.get('call')}"
            for r in f.rows
            if r.get("call") != "miss"
        )
        rows.append(
            [
                f.truth,
                f.seed,
                f"`{cell(f.text)}`",
                f.link[1],
                f.features[1],
                f.band[1],
                f.periods[1],
                ", ".join(names),
                ", ".join(others) or "none",
            ]
        )
    return rows


def exploratory(units: Lines | None) -> list[str]:
    """Section 4's exploratory table, marked as such."""
    head = [
        "### Exploratory",
        "",
        "**Exploratory: no decision reads this table.** " + NO_DIFFERENCE,
        "",
    ]
    if units is None:
        return [*head, f"{NOT_RECORDED}: `{UNITS}` is absent."]
    fits = [fit_shape(v) for v in fit_views(units.records)]
    r0 = {r["job"]: r for r in units.records if r["type"] == "r0"}
    failed = sum(r["type"] == "reference_failed" for r in units.records)
    out = [
        *head,
        f"Fits read from `{UNITS}`: {len(fits)}. `reference_failed` records, which "
        f"hold no result and are not in these tables: {failed}.",
        "",
    ]
    if not any(f.misses for f in fits):
        out += [f"No miss rows in `{UNITS}`.", ""]
    header = ("Fits read", "Fits with a miss", "Miss rows", "Miss rows per system")
    for title, axis in (
        ("Link", "link"),
        ("Number of features", "features"),
        ("G", "band"),
        ("`period` slots", "periods"),
    ):
        out += [*table((title, *header), _shape_rows(fits, axis)), ""]
    if not any(f.misses for f in fits):
        return out[:-1]
    many = sum(len({r.get("system") for r in f.misses}) > 1 for f in fits)
    one = sum(bool(f.misses) for f in fits) - many
    return [
        *out,
        "Miss rows on fits with 2 or more `period` slots, by whether two slots hold "
        "one period:",
        "",
        *table(
            ("At the stored point", "At the chosen point", "Miss rows", "Per system"),
            _sharing_rows(fits, r0),
        ),
        "",
        "The same (truth, seed, structure) across systems. "
        f"Fits with a miss on one system: {one}. On two or more systems: {many}.",
        "",
        *table(
            (
                "Truth",
                "Seed",
                "Structure",
                "Link",
                "Features",
                "G",
                "`period` slots",
                "Systems with a miss",
                "Other systems on this fit, with their call",
            ),
            _fit_rows(fits),
        ),
    ]


def disagreement(fields: Fields, units: Lines | None) -> str | None:
    """The table and ``decision.json`` count the same rows; say so if they do not."""
    if units is None:
        return None
    fits = [fit_shape(v) for v in fit_views(units.records)]
    rows = sum(len(f.misses) for f in fits)
    with_miss = sum(bool(f.misses) for f in fits)
    wanted = (fields.decision.get("M_rows"), fields.decision.get("M_fits"))
    if None in wanted or wanted == (rows, with_miss):
        return None
    return (
        f"**`{UNITS}` and `{DECISION}` disagree:** the exploratory table reads "
        f"{rows} miss rows on {with_miss} fits, and `{DECISION}` gives "
        f"{cell(wanted[0])} rows on {cell(wanted[1])} fits."
    )


# --------------------------------------------------------------------------
# Section 10, bullet by bullet
# --------------------------------------------------------------------------


def _hypothesis(fields: Fields, name: str, *, can_be_vacuous: bool) -> str:
    falsified = fields.get(name, "falsified")
    if not isinstance(falsified, bool):
        return NOT_RECORDED
    if falsified:
        return "falsified"
    if can_be_vacuous and fields.get(name, "vacuous") is True:
        return "not falsified, vacuously (M = 0)"
    return "not falsified"


def label_section(fields: Fields) -> list[str]:
    statement = fields.get("statement")
    if statement is None:
        worded = f"Statement: none in `{DECISION}`."
    elif statement == NOT_RECORDED:
        worded = f"Statement: {NOT_RECORDED}."
    else:
        worded = f"> {cell(statement)}"
    out = [
        "## 1. Label and hypotheses",
        "",
        *table(("Field", "Value"), [("Label", fields.get("label"))]),
        "",
        f"Label line: {cell(fields.get('label_line'))}",
        "",
        worded,
        "",
        f"- `provisional`: {cell(fields.get('provisional'))}",
        f"- `partial`: {cell(fields.get('partial'))}",
        f"- `exit_code`: {cell(fields.get('exit_code'))}",
        f"- `end`: {cell(fields.get('end'))}",
        f"- Did the computed GO survive: {cell(fields.get('go_no_go', 'survived'))}",
        f"- Next: {cell(fields.get('go_no_go', 'next'))}",
        f"- Stop note: {cell(fields.get('stop_note'))}",
        f"- Byte failures: {cell(fields.get('byte_failures'))}",
        f"- tier 1 fits missing: {cell(fields.get('shortfall', 'tier1_missing'))}",
        f"- tier 2 fits missing: {cell(fields.get('shortfall', 'tier2_missing'))}",
        f"- S2 rows missing: {cell(fields.get('shortfall', 's2_missing'))}",
        "",
        *table(
            ("Hypothesis", "State"),
            [
                ("H1", _hypothesis(fields, "H1", can_be_vacuous=False)),
                ("H2", _hypothesis(fields, "H2", can_be_vacuous=False)),
                ("H3", _hypothesis(fields, "H3", can_be_vacuous=True)),
                ("H4", _hypothesis(fields, "H4", can_be_vacuous=True)),
            ],
        ),
        "",
        f"H1: group A {cell(fields.get('H1', 'group_a_caught'))} of "
        f"{cell(fields.get('H1', 'group_a_of'))}, group B caught "
        f"{cell(fields.get('H1', 'group_b_caught'))}, group D caught "
        f"{cell(fields.get('H1', 'group_d_caught'))}.",
        f"H3: largest |Δ| among the misses found: {cell(fields.get('max_abs_delta'))}.",
        f"H4: `v.outcome`: {cell(fields.get('H4', 'outcome'))}.",
    ]
    if fields.get("H2", "rests_on_within_quadrature") is True:
        out.append("H2 rests on `within_quadrature` rows only.")
    return out


def controls_section(fields: Fields) -> list[str]:
    out = [
        "## 2. Controls",
        "",
        "For group D the best value is R2's, as section 2.9 defines that group.",
        "",
    ]
    cases = fields.get("H1", "cases")
    if isinstance(cases, list) and cases:
        where = "H1.cases"
        out += table(
            ("Group", "Case", "Caught", "fit/5", "Known better value", "R1's own best"),
            [
                [
                    fields.of(c, where, n)
                    for n in ("group", "case", "caught", "fit5", "known", "own_best")
                ]
                for c in cases
            ],
        )
    else:
        out.append(f"Cases: {cell(cases)}.")
    out += ["", "Group C, per stratum:", ""]
    hunt = fields.get("hunt")
    if not isinstance(hunt, Mapping):
        return [*out, f"Group C: {cell(hunt)}."]
    names = (
        "completed",
        "found_losses",
        "r1_run",
        "caught",
        "not_run",
        "uncertified",
        "sim_failed",
    )
    rows = [
        [
            name,
            *[fields.of(hunt[name], f"hunt.{name}", n) for n in names],
            fields.get("control_state", name),
        ]
        for name in sorted(hunt)
    ]
    return [*out, *table(("Stratum", *names, "Control state"), rows)]


def classes_section(fields: Fields) -> list[str]:
    classes = fields.get("classes")
    out = ["## 3. Classes", ""]
    if not isinstance(classes, Mapping):
        return [*out, f"Classes: {cell(classes)}."]
    known = [name for name, _ in CLASSES]
    rows = [(title, classes.get(name, 0)) for name, title in CLASSES]
    rows += [(name, classes[name]) for name in sorted(classes) if name not in known]
    return [*out, *table(("Class", "Rows"), rows)]


def coverage_section(fields: Fields) -> list[str]:
    def rows(name: str) -> Json:
        return fields.get("coverage", f"{name}_rows")

    def fits(name: str) -> Json:
        return fields.get("coverage", f"{name}_fits")

    out = [
        "## 4. Coverage",
        "",
        *table(
            ("Coverage", "Rows", "Fits"),
            [
                ("Exact (R2 or exhaustive)", rows("exact"), fits("exact")),
                ("Searched (R1 only)", rows("searched"), fits("searched")),
                ("Unchecked", rows("unchecked"), fits("unchecked")),
                ("Capped", NOT_RECORDED, fits("capped")),
                ("Not applicable", rows("not_applicable"), NOT_RECORDED),
            ],
        ),
        "",
        "Rows each §7.1 gate reads:",
        "",
    ]
    gates = fields.get("gates")
    if isinstance(gates, Mapping):
        out += table(("Gate", "Rows"), [(k, gates[k]) for k in sorted(gates)])
    else:
        out.append(f"Gates: {cell(gates)}.")
    out += [
        "",
        "Searched tier 1 fits by stratum, and that stratum's control result:",
        "",
    ]
    strata = fields.get("strata")
    if not isinstance(strata, Mapping):
        return [*out, f"Strata: {cell(strata)}."]
    where = "strata"
    rows_of = [
        [
            k,
            fields.of(strata[k], where, "searched_fits"),
            fields.of(strata[k], where, "control"),
        ]
        for k in sorted(strata)
    ]
    return [*out, *table(("Stratum", "Searched fits", "Control"), rows_of)]


def _miss_row(fields: Fields, row: Mapping[str, Json]) -> list[Json]:
    where = "miss_rows"
    key = fields.of(row, where, "key")
    truth, seed, system = key if isinstance(key, list) else (NOT_RECORDED,) * 3
    delta = fields.of(row, where, "delta")
    if delta is None:
        delta = fields.of(row, where, "delta_kind")
    return [
        truth,
        seed,
        system,
        fields.of(row, where, "gain"),
        delta,
        fields.of(row, where, "stored_per_event"),
        fields.of(row, where, "corrected_per_event"),
        fields.of(row, where, "evidence"),
        fields.of(row, where, "stored_uncertified"),
        fields.of(row, where, "within_quadrature"),
        fields.of(row, where, "stored_quadrature_error"),
        fields.of(row, where, "chosen_quadrature_error"),
    ]


def misses_section(fields: Fields) -> list[str]:
    out = [
        "## 5. Misses",
        "",
        f"M = {cell(fields.get('M_rows'))} rows, {cell(fields.get('M_fits'))} fits. "
        "M is a lower bound (section 2.2).",
        "",
        "`uncertified_better`: "
        f"{cell(fields.get('s3', 'uncertified_better'))}. "
        "`uncertified_better` on the identity link: "
        f"{cell(fields.get('s3', 'uncertified_better_identity'))}.",
        f"largest |Δ| among the misses found: {cell(fields.get('max_abs_delta'))}.",
        f"`within_quadrature` rows: {cell(fields.get('within_quadrature_rows'))}.",
        "",
    ]
    by_system = fields.get("M_by_system")
    if isinstance(by_system, Mapping) and by_system:
        out += table(
            ("System", "Miss rows"), [(k, by_system[k]) for k in sorted(by_system)]
        )
    else:
        out.append(f"Per system: {cell(by_system)}.")
    out.append("")
    rows = fields.get("miss_rows")
    if isinstance(rows, list) and rows:
        out += table(
            (
                "Truth",
                "Seed",
                "System",
                "Training gain (nats)",
                "Δ",
                "Stored reading",
                "Corrected reading",
                "Evidence class",
                "`stored_uncertified`",
                "`within_quadrature`",
                "Stored quadrature error",
                "Chosen quadrature error",
            ),
            [_miss_row(fields, r) for r in rows],
        )
    elif isinstance(rows, list):
        out.append("Miss rows: none.")
    else:
        out.append(f"Miss rows: {cell(rows)}.")
    return [
        *out,
        "",
        "### ORACLE search misses",
        "",
        f"Search misses on ORACLE: {cell(fields.get('s3', 'oracle_search_misses'))}; "
        "units with the stored search below truth ψ: "
        f"{cell(fields.get('s3', 'oracle_search_below_truth_psi'))}.",
    ]


def verdict_section(fields: Fields) -> list[str]:
    rows = [
        (
            name,
            fields.get("stored_gates", *path),
            fields.get("corrected_gates", *path),
        )
        for name, path in STATISTICS
    ]
    out = [
        "## 6. Verdict on corrected readings",
        "",
        *table(("Statistic", "Stored", "Corrected"), rows),
        "",
    ]
    moved = fields.get("clauses_moved")
    if isinstance(moved, list) and moved:
        where = "clauses_moved"
        out += table(
            ("Clause moved", "Consequence", "Rows behind it"),
            [
                (
                    fields.of(m, where, "clause"),
                    fields.of(m, where, "consequence"),
                    NOT_RECORDED,
                )
                for m in moved
            ],
        )
    else:
        out.append(f"Clauses moved: {cell(moved)}.")
    note = fields.get("not_put_forward")
    if note is not None:
        out += ["", cell(note)]
    return out


def s3_rows(fields: Fields) -> list[tuple[str, Json]]:
    return [(title, fields.get("s3", name)) for title, name in S3_MEASURES]


def s4_lines(fields: Fields) -> list[str]:
    out = [
        "### S4",
        "",
        f"Each statistic's limit: {NOT_RECORDED}. Each §7.1 statistic stored and "
        "corrected is in section 6 of this note.",
        f"Corrected gaps above +0.01: {cell(fields.get('s4', 'gaps_above_gate'))}.",
        "",
    ]
    rows = fields.get("s4", "rows")
    if not isinstance(rows, list) or not rows:
        return [*out, f"S4 rows: {cell(rows)}."]
    names = (
        "gap_stored",
        "gap_corrected",
        "gap_closed_stored",
        "gap_closed_corrected",
        "t4_flag_stored",
        "t4_flag_corrected",
        "similarity_stored",
        "similarity_corrected",
    )
    body = []
    for row in rows:
        key = fields.of(row, "s4.rows", "key")
        truth, seed, system = key if isinstance(key, list) else (NOT_RECORDED,) * 3
        body.append(
            [truth, seed, system, *[fields.of(row, "s4.rows", n) for n in names]]
        )
    return [*out, *table(("Truth", "Seed", "System", *names), body)]


def secondary_section(fields: Fields, units: Lines | None) -> list[str]:
    return [
        "## 7. Secondary and exploratory analyses",
        "",
        "### S1",
        "",
        f"`s1_status`: {cell(fields.get('s1_status'))}. Misses R1 had found: "
        f"{cell(fields.get('s1', 'found_by_r1'))}, of "
        f"{cell(fields.get('s1', 'misses'))} on fits where R2 ran (distinct fits).",
        "",
        "### S2",
        "",
        f"S2 rows: {cell(fields.get('s2'))}.",
        f"`v2` outcome: {cell(fields.get('v2_outcome'))}.",
        "",
        "### S3",
        "",
        *table(("Measure", "Value"), s3_rows(fields)),
        "",
        *s4_lines(fields),
        "",
        *exploratory(units),
    ]


def unknowns_section() -> list[str]:
    return [
        "## 8. Not covered and unknown",
        "",
        "Fixed words of the pre-registration, whatever M is.",
        "",
        NOT_COVERED_HEADER,
        NOT_COVERED_RULE,
        *NOT_COVERED_ROWS,
        "",
        STATED_PLAINLY,
        "",
        UNKNOWNS,
        "",
        CURVES,
        "",
        "The 140 is the text's planned count; section 4 of this note gives the final "
        "one.",
    ]


def timing_lines(timing: Lines | None) -> list[str]:
    if timing is None:
        return [f"CPU hours: {NOT_RECORDED}: `{TIMING}` is absent."]

    def hours(records: Sequence[Mapping[str, Json]], name: str) -> str:
        values = [r.get(name) for r in records]
        total = math.fsum(v for v in values if isinstance(v, int | float))
        return f"{total / 3600.0:.3f}"

    def row(name: str, records: Sequence[Mapping[str, Json]]) -> list[Json]:
        return [
            name,
            len(records),
            hours(records, "cpu_s"),
            hours(records, "cpu_children_s"),
            hours(records, "wall_s"),
        ]

    stages = sorted({str(r.get("stage")) for r in timing.records})
    rows = [
        row(s, [r for r in timing.records if str(r.get("stage")) == s]) for s in stages
    ]
    blank = sum(not isinstance(r.get("cpu_s"), int | float) for r in timing.records)
    return [
        f"Sums over the jobs in `{TIMING}`. A job writes its line when it finishes, "
        "so a job that was killed is not here, and the sum of job wall times is not "
        "the run's wall time.",
        "",
        *table(
            ("Stage", "Jobs", "CPU hours", "Children's CPU hours", "Job wall hours"),
            [*rows, row("All", timing.records)],
        ),
        "",
        f"Lines with no CPU figure: {blank}.",
    ]


def cost_section(fields: Fields, timing: Lines | None) -> list[str]:
    commit = fields.get("F")
    path = f"{S3_BASE}/search-misses-{commit}/" if commit != NOT_RECORDED else commit
    return [
        "## 9. Cost, records and departures",
        "",
        *timing_lines(timing),
        "",
        f"- Kernel CPU count and run wall time: {NOT_RECORDED} in these files.",
        f"- Clock stops: {cell(fields.get('clock_stops'))}.",
        f"- LEDGER row: {NOT_RECORDED}.",
        f"- S3 path, as section 2.10 fixes it: {path} (whether the copy was made: "
        f"{NOT_RECORDED}).",
        f"- `F`: {cell(commit)}",
        f"- `R`: {cell(fields.get('R'))}",
        f"- Pre-registration sha256: {cell(fields.get('prereg_sha256'))}",
        f"- Departures from the pre-registration: {NOT_RECORDED}.",
        f"- Whether anyone read `journal.jsonl` before the run ended: {NOT_RECORDED}.",
    ]


def absent_section(fields: Fields) -> list[str]:
    names = [f"- `{name}`" for name in sorted(fields.absent)]
    return [
        "## Fields not recorded",
        "",
        f"Fields this note looked for in `{DECISION}` and did not find:",
        "",
        *(names or ["- none"]),
        "",
        "Asked for by section 10 or the sections it draws on, and carried by no "
        "record this note reads:",
        "",
        *[f"- {item}" for item in NEVER_RECORDED],
    ]


def note_of(root: Path) -> str:
    """The note: a function of the three files' bytes and nothing else."""
    decision_bytes = read_file(root, DECISION)
    units_bytes = read_file(root, UNITS)
    timing_bytes = read_file(root, TIMING)
    decision = read_decision(decision_bytes)
    units = None if units_bytes is None else parse_lines(units_bytes, UNIT_KEYS)
    timing = None if timing_bytes is None else parse_lines(timing_bytes)
    fields = Fields(decision or {})
    body = [
        *label_section(fields),
        "",
        *controls_section(fields),
        "",
        *classes_section(fields),
        "",
        *coverage_section(fields),
        "",
        *misses_section(fields),
        "",
        *verdict_section(fields),
        "",
        *secondary_section(fields, units),
        "",
        *unknowns_section(),
        "",
        *cost_section(fields, timing),
        "",
        *absent_section(fields),
    ]
    head = [
        "# Pilot search misses: the section 10 note",
        "",
        "**Status: unreviewed.** Generated from the run's records, and unreviewed "
        "until a red team has read it (section 10).",
        "",
        STORED_STAY,
        "",
        "```sh",
        "uv run python scripts/p3_report/miss_note.py --records <records directory>",
        "```",
        "",
        "Files read:",
        "",
        input_line(DECISION, decision_bytes, readable=decision is not None),
        input_line(UNITS, units_bytes),
        input_line(TIMING, timing_bytes),
        *damage_lines(UNITS, units),
        *damage_lines(TIMING, timing),
        "",
    ]
    provisional = fields.decision.get("provisional")
    if provisional is True:
        head += [f"**PROVISIONAL.** {PROVISIONAL}", ""]
    elif provisional is None:
        head += [f"Whether this label is provisional: {NOT_RECORDED}.", ""]
    conflict = disagreement(fields, units)
    if conflict is not None:
        head += [conflict, ""]
    return "\n".join([*head, *body]) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.records.is_dir():
        print(f"no such directory: {args.records}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(note_of(args.records).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
