"""Project the wall time of the P3 non-LLM plan from a CPU-timed store.

    uv run python scripts/project_runtime.py --store .cache/mini-cpu \
        --workers 2 --truths 20 --seeds 1,2,3 --b10f-truths 10 \
        --assume B-sym@10F=10xB-sym

For the queue item "Where the baselines run" (docs/v2/QUEUE.md; LOG
2026-10-28, "The baselines' run-time projection uses CPU time, with the
wall/CPU ratio beside it"). Takes the plan options of ``scripts/pilot.py``
(the same parser) and enumerates the plan with the driver's own planner
(``pilot.campaign_config`` and ``sciagent.campaign.plan``), so the unit list
is the one a run with those options would queue. LLM units are listed as
excluded with their count, never projected.

A unit's cost is ``cpu_s + cpu_children_s`` of its ``baseline`` lines in
``<store>/timing.jsonl``; a line without either figure (an older store) has
no CPU cost (never 0). A job's data simulation is in its job figures
(``job_cpu_s + job_cpu_children_s``) and in no unit's, so the pool total
(the sum of the job figures over ``job_units``) is reported beside the unit
total, and a second basis adds to each unit an equal share of its job's
overhead (job CPU less its units' CPU). Each planned unit costs its system's
mean (and, separately, max) measured cost; the units are list-scheduled
longest-first onto ``--workers`` and the makespan is compared with the
48-hour line, in CPU hours and multiplied by the measured wall/CPU ratio. A
planned system with no CPU figure in the store is printed as not measured and
left out (the projection is then a lower bound), unless ``--assume
SYSTEM=FACTORxOTHER`` costs it as FACTOR times a measured system, which is
printed as an assumption. Read-only; the output is deterministic.

    uv run python scripts/project_runtime.py --store .cache/mini-cpu \
        --store .cache/strata --wall-from .cache/mini-a \
        --wall-from .cache/mini-b --by-stratum \
        --workers 2 --truths 20 --seeds 1,2,3 --b10f-truths 10

``--store`` may be repeated: the stores' ``baseline`` lines are pooled
(:func:`pool`). A unit measured (CPU and wall) in two or more stores is one
measurement, the mean of all its lines there, and the output counts them; a
unit measured in one store keeps every line, as with a single store. A store
without CPU figures contributes no CPU. ``--by-stratum`` adds a per-stratum
basis: a unit costs the mean of its system's measured units in its truth's
stratum (from the split), else its system's all-strata mean, and a table says
which, with n, for every planned (system, stratum). ``--wall-from`` (repeatable)
adds an uncontended estimate: each measured unit costs the minimum of its CPU
and every ``wall_s`` of it in any store given, an upper bound on its
uncontended cost under the hyperthreading bias (LOG 2026-10-28, "Correction:
CPU time under load is not a lower bound ..."); a unit that is only in a
``--wall-from`` store is not a measurement. With one ``--store`` and no
``--wall-from`` the output is the single-store script's, line for line.
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pilot import build_parser, campaign_config, load_split, select

TIMING_FILE: Final = "timing.jsonl"
LINE_HOURS: Final = 48.0
#: Cost bases, in report order: a unit's own CPU, and the same plus an equal
#: share of its pool job's overhead.
BASES: Final = ("unit mean", "unit max", "+overhead mean", "+overhead max")

JobKey = tuple[str, int, float, float, int]
#: (system, truth, seed): one planned or measured unit.
UnitKey = tuple[str, str, int]
#: The per-stratum bases (``--by-stratum``) and the uncontended estimate
#: (``--wall-from``), by the per-unit figure each averages.
STRATUM_BASES: Final = (("stratum mean", "cpu"), ("+overhead stratum mean", "full"))
UNCONTENDED: Final = "min(CPU, walls)"


@dataclass(frozen=True)
class TimingLine:
    """One ``baseline`` line of ``timing.jsonl``; None where a figure is absent."""

    truth: str
    seed: int
    system: str
    wall_s: float | None
    #: ``cpu_s + cpu_children_s``; None unless both are present.
    cpu_s: float | None
    #: The whole pool job (``job_cpu_s + job_cpu_children_s``, data
    #: simulation included), the same on every line of a multi-unit job.
    job_cpu_s: float | None
    job_wall_s: float | None
    job_units: int | None

    @property
    def job(self) -> JobKey | None:
        if self.job_cpu_s is None or self.job_wall_s is None or not self.job_units:
            return None
        return (self.truth, self.seed, self.job_cpu_s, self.job_wall_s, self.job_units)


@dataclass(frozen=True)
class Timing:
    lines: tuple[TimingLine, ...]
    other_kinds: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Stats:
    """One system's measured cost, over its lines with CPU and wall figures."""

    n: int
    mean: float
    max: float
    wall: float
    cpu: float
    #: Unit CPU plus the line's share of its job's overhead, where known.
    full_n: int
    full_mean: float | None
    full_max: float | None

    @property
    def ratio(self) -> float:
        return self.wall / self.cpu


@dataclass(frozen=True)
class Assumption:
    system: str
    factor: float
    other: str

    def text(self) -> str:
        return f"{self.system} = {self.factor:g} x {self.other}"


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"not a number: {value!r}")
    return float(value)


def _sum(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a + b


def read_timing(path: Path) -> Timing:
    """The ``baseline`` lines of ``path``, and a count of every other kind."""
    lines: list[TimingLine] = []
    other: Counter[str] = Counter()
    text = path.read_text(encoding="utf-8")
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
            kind = str(rec["kind"])
            if kind != "baseline":
                other[kind] += 1
                continue
            units = _number(rec.get("job_units"))
            lines.append(
                TimingLine(
                    truth=str(rec["truth"]),
                    seed=int(rec["seed"]),
                    system=str(rec["system"]),
                    wall_s=_number(rec.get("wall_s")),
                    cpu_s=_sum(
                        _number(rec.get("cpu_s")), _number(rec.get("cpu_children_s"))
                    ),
                    job_cpu_s=_sum(
                        _number(rec.get("job_cpu_s")),
                        _number(rec.get("job_cpu_children_s")),
                    ),
                    job_wall_s=_number(rec.get("job_wall_s")),
                    job_units=None if units is None else int(units),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    return Timing(tuple(lines), tuple(sorted(other.items())))


def with_overhead(lines: Sequence[TimingLine]) -> list[float | None]:
    """Per line: its CPU plus an equal share of its job's overhead (job CPU
    less the CPU of the job's units). None where the line or any line of its
    job lacks a figure, or the store does not hold all ``job_units`` lines."""
    jobs: dict[JobKey, list[TimingLine]] = {}
    for line in lines:
        if line.job is not None:
            jobs.setdefault(line.job, []).append(line)
    out: list[float | None] = []
    for line in lines:
        key = line.job
        group = [] if key is None else jobs[key]
        cpus = [x.cpu_s for x in group if x.cpu_s is not None]
        if (
            key is None
            or line.cpu_s is None
            or len(group) != key[4]
            or len(cpus) != len(group)
        ):
            out.append(None)
            continue
        out.append(line.cpu_s + (key[2] - sum(cpus)) / key[4])
    return out


@dataclass(frozen=True)
class Measured:
    """One measurement of a unit: a line with CPU and wall figures, or, for a
    unit with such lines in more than one store, the mean of all of them."""

    truth: str
    seed: int
    system: str
    cpu_s: float
    wall_s: float
    #: Unit CPU plus its share of its job's overhead (:func:`with_overhead`);
    #: for a pooled unit the mean over its lines that have it; else None.
    full_s: float | None
    #: How many stores with CPU figures measured the unit.
    stores: int


def _key(line: TimingLine) -> UnitKey:
    return (line.system, line.truth, line.seed)


def pool(stores: Sequence[Sequence[TimingLine]]) -> tuple[list[Measured], int]:
    """The measurements of the lines of several stores, and how many units
    were measured (with CPU and wall figures) in more than one store.

    A unit with such lines in one store only keeps every line as its own
    measurement, as the single-store script did with a repeated unit. A unit
    with such lines in two or more stores is one measurement: the mean of all
    of its lines' CPU, wall and (where known) +overhead figures. A store
    without CPU figures contributes nothing. Order: stores as given, then
    file order, a pooled unit at its first line."""
    entries: list[tuple[int, TimingLine, float, float, float | None]] = []
    for index, lines in enumerate(stores):
        for x, full in zip(lines, with_overhead(lines), strict=True):
            if x.cpu_s is not None and x.wall_s is not None:
                entries.append((index, x, x.cpu_s, x.wall_s, full))
    where: dict[UnitKey, set[int]] = {}
    for index, x, _, _, _ in entries:
        where.setdefault(_key(x), set()).add(index)
    out: list[Measured] = []
    done: set[UnitKey] = set()
    for _, x, cpu, wall, full in entries:
        key = _key(x)
        n = len(where[key])
        if n == 1:
            out.append(Measured(x.truth, x.seed, x.system, cpu, wall, full, 1))
            continue
        if key in done:
            continue
        done.add(key)
        group = [(c, w, f) for _, y, c, w, f in entries if _key(y) == key]
        fulls = [f for _, _, f in group if f is not None]
        out.append(
            Measured(
                x.truth,
                x.seed,
                x.system,
                sum(c for c, _, _ in group) / len(group),
                sum(w for _, w, _ in group) / len(group),
                sum(fulls) / len(fulls) if fulls else None,
                n,
            )
        )
    return out, sum(1 for s in where.values() if len(s) > 1)


def system_stats(lines: Sequence[TimingLine]) -> dict[str, Stats]:
    """Per system with at least one CPU figure: its cost statistics."""
    return stats_of(pool([lines])[0])


def stats_of(measured: Sequence[Measured]) -> dict[str, Stats]:
    """Per system of ``measured``: its cost statistics."""
    out: dict[str, Stats] = {}
    for system in sorted({m.system for m in measured}):
        timed = [(m.cpu_s, m.wall_s, m.full_s) for m in measured if m.system == system]
        cpu = [c for c, _, _ in timed]
        known = [f for _, _, f in timed if f is not None]
        out[system] = Stats(
            n=len(cpu),
            mean=sum(cpu) / len(cpu),
            max=max(cpu),
            wall=sum(w for _, w, _ in timed),
            cpu=sum(cpu),
            full_n=len(known),
            full_mean=sum(known) / len(known) if known else None,
            full_max=max(known) if known else None,
        )
    return out


def lpt_makespan(costs: Sequence[float], workers: int) -> float:
    """Longest-processing-time-first list scheduling: each cost, largest
    first, goes to the least-loaded worker (lowest index on a tie)."""
    if workers < 1:
        raise ValueError("workers must be ≥ 1")
    loads = [(0.0, w) for w in range(workers)]
    for cost in sorted(costs, reverse=True):
        load, w = heapq.heappop(loads)
        heapq.heappush(loads, (load + cost, w))
    return max(load for load, _ in loads)


def parse_assumption(text: str) -> Assumption:
    system, eq, rhs = text.partition("=")
    factor, x, other = rhs.partition("x")
    try:
        value = float(factor)
    except ValueError as error:
        raise ValueError(f"--assume {text!r}: want SYSTEM=FACTORxOTHER") from error
    if not (eq and x and system and other) or value <= 0:
        raise ValueError(f"--assume {text!r}: want SYSTEM=FACTORxOTHER, FACTOR > 0")
    if system == other:
        raise ValueError(f"--assume {text!r}: a system cannot be costed by itself")
    return Assumption(system, value, other)


def check_assumptions(
    assumptions: Sequence[Assumption],
    planned: Sequence[str],
    stats: Mapping[str, Stats],
) -> None:
    seen: set[str] = set()
    for a in assumptions:
        if a.system in seen:
            raise ValueError(f"--assume: {a.system} is assumed twice")
        seen.add(a.system)
        if a.system not in planned:
            raise ValueError(f"--assume: {a.system} has no units in this plan")
        if a.system in stats:
            raise ValueError(
                f"--assume: {a.system} is measured in the store; a measured "
                "system is never overridden"
            )
        if a.other not in stats:
            raise ValueError(f"--assume: {a.other} has no CPU figure in the store")


def unit_costs(
    stats: Mapping[str, Stats], assumptions: Sequence[Assumption]
) -> dict[str, dict[str, float]]:
    """Cost per unit by basis (:data:`BASES`) for every costed system; a
    basis is absent where the store has no figure for it."""
    costs: dict[str, dict[str, float]] = {}
    for system in sorted(stats):
        s = stats[system]
        costs[system] = {"unit mean": s.mean, "unit max": s.max}
        if s.full_mean is not None and s.full_max is not None:
            costs[system] |= {
                "+overhead mean": s.full_mean,
                "+overhead max": s.full_max,
            }
    for a in assumptions:
        costs[a.system] = {k: a.factor * v for k, v in costs[a.other].items()}
    return costs


def uncontended(
    measured: Sequence[Measured], walls: Sequence[Sequence[TimingLine]]
) -> list[float]:
    """Per measured unit: the minimum of its CPU and every ``wall_s`` of the
    same unit in ``walls`` (all stores, with or without CPU figures). A unit
    that is only in ``walls`` is not a measurement and is not added."""
    seen: dict[UnitKey, list[float]] = {}
    for lines in walls:
        for x in lines:
            if x.wall_s is not None:
                seen.setdefault(_key(x), []).append(x.wall_s)
    return [
        min([m.cpu_s, *seen.get((m.system, m.truth, m.seed), [])]) for m in measured
    ]


def wall_runs(
    measured: Sequence[Measured], walls: Sequence[Sequence[TimingLine]]
) -> list[int]:
    """Per measured unit: how many of the stores ``walls`` hold a ``wall_s``
    for it. At 1 its uncontended figure is min(CPU, the wall of the same run
    or runs of one store), which carries that store's contention."""
    seen: dict[UnitKey, set[int]] = {}
    for index, lines in enumerate(walls):
        for x in lines:
            if x.wall_s is not None:
                seen.setdefault(_key(x), set()).add(index)
    return [len(seen.get((m.system, m.truth, m.seed), set())) for m in measured]


@dataclass(frozen=True)
class Cell:
    """The cost of one (system, stratum) under ``--by-stratum``: per figure
    (``cpu``, ``full``, ``min``) its per-unit mean, None where unknown."""

    n: int
    costs: Mapping[str, float | None]
    source: str


def _mean(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def stratum_cells(
    measured: Sequence[Measured],
    figures: Mapping[str, Sequence[float | None]],
    stratum_of: Mapping[str, str],
    cells: Sequence[tuple[str, str]],
    assumptions: Sequence[Assumption],
) -> dict[tuple[str, str], Cell]:
    """Per planned (system, stratum): the mean of each figure over the
    system's measured units in that stratum; where the system has none
    there, its all-strata mean (said in ``source``); an assumed system is
    FACTOR times its other system's cell; an unmeasured one has no costs."""

    def own(system: str, stratum: str) -> Cell | None:
        index = [i for i, m in enumerate(measured) if m.system == system]
        if not index:
            return None
        here = [i for i in index if stratum_of.get(measured[i].truth) == stratum]
        use = here or index
        costs = {k: _mean([v[i] for i in use]) for k, v in figures.items()}
        source = (
            "this stratum"
            if here
            else f"all strata, n {len(index)} ({stratum} not measured)"
        )
        return Cell(len(here), costs, source)

    assumed = {a.system: a for a in assumptions}
    out: dict[tuple[str, str], Cell] = {}
    for system, stratum in cells:
        cell = own(system, stratum)
        if cell is None and system in assumed:
            a = assumed[system]
            other = own(a.other, stratum)
            if other is None:
                raise ValueError(f"--assume: {a.other} has no CPU figure")
            cell = Cell(
                0,
                {
                    k: None if v is None else a.factor * v
                    for k, v in other.costs.items()
                },
                f"ASSUMED {a.text()} ({a.other}: {other.source})",
            )
        if cell is None:
            cell = Cell(
                0,
                dict.fromkeys(figures),
                "not measured; left out of the projection",
            )
        out[(system, stratum)] = cell
    return out


def ratios(lines: Sequence[TimingLine]) -> dict[str, float | None]:
    """Measured wall/CPU: of the units (``wall_s`` over unit CPU) and of the
    pool jobs (``job_wall_s`` over job CPU, each job once)."""
    unit = [(x.wall_s, x.cpu_s) for x in lines]
    jobs = {x.job: (x.job_wall_s, x.job_cpu_s) for x in lines if x.job is not None}
    out: dict[str, float | None] = {}
    for name, pairs in (("unit", unit), ("job", [jobs[k] for k in sorted(jobs)])):
        known = [(w, c) for w, c in pairs if w is not None and c is not None]
        cpu = sum(c for _, c in known)
        out[name] = sum(w for w, _ in known) / cpu if cpu > 0 else None
    return out


def _verdict(seconds: float) -> str:
    hours = seconds / 3600
    side = (
        f"<= {LINE_HOURS:g} h (dm-devbox)"
        if hours <= LINE_HOURS
        else (f"> {LINE_HOURS:g} h (stop point)")
    )
    return f"{hours:.1f} h {side}"


def _projection_line(
    basis: str, costs: Sequence[float], workers: int, ratio: float | None
) -> str:
    span = lpt_makespan(costs, workers)
    wall = (
        "no wall/CPU ratio"
        if ratio is None
        else f"x {ratio:.2f} wall/CPU {_verdict(span * ratio)}"
    )
    return f"  {basis}: CPU {_verdict(span)}; {wall}"


def report(args: argparse.Namespace, assumptions: Sequence[Assumption]) -> list[str]:
    from sciagent.campaign.plan import baseline_units, llm_units

    split = load_split(args.split)
    records = select(split, args.truths, args.ids)
    stratum = {r.id: r.stratum for r in split}
    config = campaign_config(args)
    ids = tuple(r.id for r in records)
    plan = baseline_units(ids, config)
    llm = llm_units(ids, config)
    paths: list[Path] = args.store
    wall_paths: list[Path] = args.wall_from
    #: One store and no --wall-from: the original output, line for line.
    single = len(paths) == 1 and not wall_paths
    timings = [read_timing(p / TIMING_FILE) for p in paths]
    wall_timings = [read_timing(p / TIMING_FILE) for p in wall_paths]
    lines = tuple(x for t in timings for x in t.lines)
    measured, duplicates = pool([t.lines for t in timings])
    stats = stats_of(measured)
    per_system = Counter(u.system for u in plan)
    planned = tuple(s for s in config.baselines if per_system[s])
    check_assumptions(assumptions, planned, stats)
    per_llm = Counter(u.system for u in llm)
    out: list[str] = []

    out.append(
        f"Run-time projection of the P3 non-LLM plan at {args.workers} workers, "
        f"from {', '.join(str(p / TIMING_FILE) for p in paths)}"
    )
    b10f = "all" if config.control_truths is None else config.control_truths
    out.append(
        f"plan: {len(ids)} truths x seeds {','.join(map(str, config.seeds))}; "
        f"B-sym@10F on control seeds {','.join(map(str, config.control_seeds))}, "
        f"truths: {b10f}"
    )
    out.append(
        f"planned units: {len(plan) + len(llm)}; non-LLM (projected): {len(plan)}; "
        f"LLM (excluded): {len(llm)}"
    )
    for system in config.llm_systems():
        out.append(f"  excluded LLM arm {system}: {per_llm[system]} units")
    timed = sum(1 for x in lines if x.cpu_s is not None)
    if single:
        others = ", ".join(f"{k} {v}" for k, v in timings[0].other_kinds) or "none"
        out.append(
            f"store: {len(lines)} baseline lines ({timed} with CPU figures, "
            f"{len(lines) - timed} without); other kinds, not used: {others}"
        )
        repeats = sum(
            n - 1 for n in Counter((x.truth, x.seed, x.system) for x in lines).values()
        )
        if repeats:
            out.append(f"  repeated units in the store (every line counted): {repeats}")
    else:
        for path, timing in zip(paths, timings, strict=True):
            with_cpu = sum(1 for x in timing.lines if x.cpu_s is not None)
            others = ", ".join(f"{k} {v}" for k, v in timing.other_kinds) or "none"
            out.append(
                f"store {path / TIMING_FILE}: {len(timing.lines)} baseline lines "
                f"({with_cpu} with CPU figures, "
                f"{len(timing.lines) - with_cpu} without); "
                f"other kinds, not used: {others}"
                + ("; contributes no CPU" if with_cpu == 0 else "")
            )
            repeats = sum(n - 1 for n in Counter(map(_key, timing.lines)).values())
            if repeats:
                out.append(
                    f"  repeated units in this store: {repeats} (every line "
                    "counted, unless the unit is also in another store)"
                )
        for path, timing in zip(wall_paths, wall_timings, strict=True):
            out.append(
                f"wall only (--wall-from) {path / TIMING_FILE}: "
                f"{len(timing.lines)} baseline lines; its CPU figures are not used"
            )
        out.append(
            f"pooled CPU: {len(measured)} measured units from {timed} lines with "
            f"CPU figures; units measured in more than one store: {duplicates}, "
            "each one unit at the mean of its lines' CPU and wall"
        )
    extra = sorted({x.system for x in lines} - set(planned))
    if extra:
        out.append(f"  store systems not in this plan: {', '.join(extra)}")

    out.append("")
    out.append(
        "per-unit CPU, cpu_s + cpu_children_s (s); +overhead adds an equal "
        "share of the job's CPU beyond its units' (data simulation)"
    )
    out.append(
        f"  {'system':<13}{'plan':>6}{'n':>4}{'mean':>10}{'max':>10}"
        f"{'wall/CPU':>10}{'+ovh mean':>11}{'+ovh max':>10}"
    )
    assumed = {a.system: a for a in assumptions}
    for system in planned:
        head = f"  {system:<13}{per_system[system]:>6}"
        s = stats.get(system)
        if s is None:
            seen = sum(1 for x in lines if x.system == system)
            note = f" ({seen} lines without CPU figures)" if seen else ""
            why = (
                f"ASSUMED {assumed[system].text()}"
                if system in assumed
                else "left out of the projection"
            )
            out.append(f"{head}  not measured{note}; {why}")
            continue
        full = (
            f"{s.full_mean:>11.1f}{s.full_max:>10.1f}"
            if s.full_mean is not None and s.full_max is not None
            else f"{'-':>11}{'-':>10}"
        )
        out.append(f"{head}{s.n:>4}{s.mean:>10.1f}{s.max:>10.1f}{s.ratio:>10.2f}{full}")
    unit_total = sum(x.cpu_s for x in lines if x.cpu_s is not None)
    shares = [
        x.job_cpu_s / x.job_units
        for x in lines
        if x.job_cpu_s is not None and x.job_units
    ]
    out.append(
        f"{'store' if single else 'stores (every line, before pooling)'} totals: "
        f"unit CPU {unit_total:.1f} s over {timed} lines; pool CPU "
        f"(sum of job CPU / job_units) {sum(shares):.1f} s over {len(shares)} lines"
    )
    measured_ratio = ratios(lines)
    out.append(
        f"measured wall/CPU over the {'store' if single else 'stores'}"
        f"{'' if single else ' (every CPU line, before pooling)'}: "
        + "; ".join(
            f"{k} {'-' if r is None else f'{r:.2f}'}" for k, r in measured_ratio.items()
        )
    )
    for a in assumptions:
        out.append(f"ASSUMPTION (not measured): {a.text()} per unit")

    costs = unit_costs(stats, assumptions)
    left_out = {s: per_system[s] for s in planned if s not in costs}
    costed = sorted(u for u in plan if u.system in costs)
    mins = (
        uncontended(measured, [t.lines for t in (*timings, *wall_timings)])
        if wall_paths
        else None
    )
    figures: dict[str, Sequence[float | None]] = {
        "cpu": [m.cpu_s for m in measured],
        "full": [m.full_s for m in measured],
    }
    if mins is not None:
        figures["min"] = mins

    cells: dict[tuple[str, str], Cell] = {}
    if args.by_stratum:
        wanted = sorted(
            {(u.system, stratum[u.truth_id]) for u in plan},
            key=lambda c: (planned.index(c[0]), c[1]),
        )
        cells = stratum_cells(measured, figures, stratum, wanted, assumptions)
        per_cell = Counter((u.system, stratum[u.truth_id]) for u in plan)
        out.append("")
        out.append(
            "by stratum (--by-stratum): a planned unit costs the mean of its "
            "system's measured units in its truth's stratum (from the split); "
            "where there are none, the system's all-strata mean, as the source "
            "says (s)"
        )
        out.append(
            f"  {'system':<13}{'stratum':<9}{'plan':>4}{'n':>4}{'mean':>10}"
            f"{'+ovh mean':>11}  source"
        )
        for key in wanted:
            cell = cells[key]
            cell_cpu, cell_full = cell.costs["cpu"], cell.costs["full"]
            out.append(
                f"  {key[0]:<13}{key[1]:<9}{per_cell[key]:>4}{cell.n:>4}"
                f"{'-' if cell_cpu is None else f'{cell_cpu:.1f}':>10}"
                f"{'-' if cell_full is None else f'{cell_full:.1f}':>11}  {cell.source}"
            )

    min_costs: dict[str, float] = {}
    if mins is not None:
        runs = wall_runs(measured, [t.lines for t in (*timings, *wall_timings)])
        out.append("")
        out.append(
            "uncontended estimate: each measured unit costs min(its CPU, its "
            "wall_s in every store that has it); an upper bound on its "
            "uncontended cost under the hyperthreading bias (LOG 2026-10-28 "
            "correction)"
        )
        out.append(
            "  wall<CPU: units with a wall below their CPU; one run: units with "
            "a wall in one store only, whose figure is min(CPU, a wall of that "
            "store's run), so it keeps that run's contention"
        )
        out.append(
            f"  {'system':<13}{'n':>4}{'CPU mean':>10}{'min mean':>10}"
            f"{'min/CPU':>10}{'wall<CPU':>10}{'one run':>10}"
        )
        pairs: dict[str, list[tuple[float, float, int]]] = {}
        for m, low, n in zip(measured, mins, runs, strict=True):
            pairs.setdefault(m.system, []).append((m.cpu_s, low, n))
        for system in sorted(pairs):
            own = pairs[system]
            min_costs[system] = sum(v for _, v, _ in own) / len(own)
        for a in assumptions:
            min_costs[a.system] = a.factor * min_costs[a.other]
        for system in planned:
            own = pairs.get(system, [])
            if own:
                cpu = sum(c for c, _, _ in own) / len(own)
                low = min_costs[system]
                below = sum(1 for c, v, _ in own if v < c)
                single_run = sum(1 for _, _, n in own if n == 1)
                out.append(
                    f"  {system:<13}{len(own):>4}{cpu:>10.1f}{low:>10.1f}"
                    f"{low / cpu:>10.2f}{below:>10}{single_run:>10}"
                )
            elif system in assumed:
                out.append(
                    f"  {system:<13}  not measured; ASSUMED "
                    f"{assumed[system].text()}: min mean {min_costs[system]:.1f} s"
                )
            else:
                out.append(f"  {system:<13}  not measured; left out of the projection")

    out.append("")
    out.append(
        f"projection: makespan of longest-first list scheduling of "
        f"{len(costed)} units onto {args.workers} workers; 48-hour line"
    )
    if left_out:
        missing = ", ".join(f"{s} ({n} units)" for s, n in left_out.items())
        out.append(
            f"  NOT MEASURED, left out: {missing}; every figure is a lower bound"
        )
    for basis in BASES:
        if any(basis not in costs[u.system] for u in costed):
            out.append(f"  {basis}: no job figures for every costed system")
            continue
        ratio = measured_ratio["unit" if basis.startswith("unit") else "job"]
        out.append(
            _projection_line(
                basis, [costs[u.system][basis] for u in costed], args.workers, ratio
            )
        )
    if args.by_stratum:
        for basis, figure in STRATUM_BASES:
            per_unit = [
                cells[(u.system, stratum[u.truth_id])].costs[figure] for u in costed
            ]
            known = [c for c in per_unit if c is not None]
            if len(known) != len(per_unit):
                out.append(
                    f"  {basis}: no job figures for every costed (system, stratum)"
                )
                continue
            ratio = measured_ratio["unit" if figure == "cpu" else "job"]
            out.append(_projection_line(basis, known, args.workers, ratio))
    if mins is not None:
        bases: list[tuple[str, list[float | None]]] = [
            ("uncontended unit mean", [min_costs[u.system] for u in costed])
        ]
        if args.by_stratum:
            bases.append(
                (
                    "uncontended stratum mean",
                    [
                        cells[(u.system, stratum[u.truth_id])].costs["min"]
                        for u in costed
                    ],
                )
            )
        for basis, per_unit in bases:
            known = [c for c in per_unit if c is not None]
            if len(known) != len(per_unit):
                raise ValueError(f"{basis}: a costed unit has no {UNCONTENDED} figure")
            span = lpt_makespan(known, args.workers)
            out.append(
                f"  {basis}, {UNCONTENDED}: {_verdict(span)}; an upper bound on "
                "uncontended cost"
            )

    out.append("")
    truths = sorted({x.truth for x in lines if x.cpu_s is not None})
    store_strata = ", ".join(f"{t} ({stratum.get(t, 'not in split')})" for t in truths)
    plan_strata = ", ".join(
        f"{k} {v}" for k, v in sorted(Counter(stratum[t] for t in ids).items())
    )
    whose = "store's" if single else "stores'"
    out.append(
        f"caveat: the {whose} truths are {store_strata or 'none'}; the plan's "
        f"strata are {plan_strata}. Costs measured on far truths may overstate "
        "the near and mid ones (LOG 2026-10-28)."
    )
    out.append(
        "caveat: units are scheduled one by one; the driver runs ORACLE, B-lib "
        "and B-np of a (truth, seed) as one job and at most "
        f"--max-heavy={args.max_heavy} B-sparse jobs at once, neither modelled. "
        "CPU time measured under load is not a lower bound on uncontended wall "
        "time: on a hyperthreaded host a contended unit accrues more CPU "
        "seconds for the same work (LOG 2026-10-28)."
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.description = __doc__.split("\n\n")[0]
    parser.set_defaults(workers=2)
    parser.add_argument(
        "--store",
        type=Path,
        action="append",
        required=True,
        help="a campaign root; repeat to pool several stores' baseline lines",
    )
    parser.add_argument(
        "--wall-from",
        type=Path,
        action="append",
        default=[],
        metavar="STORE",
        help="a campaign root whose wall times (only) enter the uncontended "
        "estimate; adds that estimate",
    )
    parser.add_argument(
        "--by-stratum",
        action="store_true",
        help="also cost each unit by its system's mean in its truth's stratum",
    )
    parser.add_argument(
        "--assume",
        action="append",
        default=[],
        metavar="SYSTEM=FACTORxOTHER",
        help="cost an unmeasured system as FACTOR times a measured one "
        "(printed as an assumption), e.g. B-sym@10F=10xB-sym",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be ≥ 1")
    roots = [*args.store, *args.wall_from]
    resolved = Counter(p.resolve() for p in roots)
    for root in roots:
        if resolved[root.resolve()] > 1:
            print(f"{root}: given twice (--store, --wall-from)", file=sys.stderr)
            return 2
        if not (root / TIMING_FILE).is_file():
            print(f"{root}: no {TIMING_FILE}", file=sys.stderr)
            return 2
    try:
        assumptions = [parse_assumption(a) for a in args.assume]
        out = report(args, assumptions)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
