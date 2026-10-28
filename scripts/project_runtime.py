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


def system_stats(lines: Sequence[TimingLine]) -> dict[str, Stats]:
    """Per system with at least one CPU figure: its cost statistics."""
    full = with_overhead(lines)
    out: dict[str, Stats] = {}
    for system in sorted({x.system for x in lines}):
        timed = [
            (x.cpu_s, x.wall_s, f)
            for x, f in zip(lines, full, strict=True)
            if x.system == system and x.cpu_s is not None and x.wall_s is not None
        ]
        if not timed:
            continue
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


def report(args: argparse.Namespace, assumptions: Sequence[Assumption]) -> list[str]:
    from sciagent.campaign.plan import baseline_units, llm_units

    split = load_split(args.split)
    records = select(split, args.truths, args.ids)
    stratum = {r.id: r.stratum for r in split}
    config = campaign_config(args)
    ids = tuple(r.id for r in records)
    plan = baseline_units(ids, config)
    llm = llm_units(ids, config)
    timing = read_timing(args.store / TIMING_FILE)
    lines = timing.lines
    stats = system_stats(lines)
    per_system = Counter(u.system for u in plan)
    planned = tuple(s for s in config.baselines if per_system[s])
    check_assumptions(assumptions, planned, stats)
    per_llm = Counter(u.system for u in llm)
    out: list[str] = []

    out.append(
        f"Run-time projection of the P3 non-LLM plan at {args.workers} workers, "
        f"from {args.store / TIMING_FILE}"
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
    others = ", ".join(f"{k} {v}" for k, v in timing.other_kinds) or "none"
    out.append(
        f"store: {len(lines)} baseline lines ({timed} with CPU figures, "
        f"{len(lines) - timed} without); other kinds, not used: {others}"
    )
    repeats = sum(
        n - 1 for n in Counter((x.truth, x.seed, x.system) for x in lines).values()
    )
    if repeats:
        out.append(f"  repeated units in the store (every line counted): {repeats}")
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
        f"store totals: unit CPU {unit_total:.1f} s over {timed} lines; pool CPU "
        f"(sum of job CPU / job_units) {sum(shares):.1f} s over {len(shares)} lines"
    )
    measured = ratios(lines)
    out.append(
        "measured wall/CPU over the store: "
        + "; ".join(
            f"{k} {'-' if r is None else f'{r:.2f}'}" for k, r in measured.items()
        )
    )
    for a in assumptions:
        out.append(f"ASSUMPTION (not measured): {a.text()} per unit")

    costs = unit_costs(stats, assumptions)
    left_out = {s: per_system[s] for s in planned if s not in costs}
    costed = sorted(u for u in plan if u.system in costs)
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
        span = lpt_makespan([costs[u.system][basis] for u in costed], args.workers)
        ratio = measured["unit" if basis.startswith("unit") else "job"]
        wall = (
            "no wall/CPU ratio"
            if ratio is None
            else f"x {ratio:.2f} wall/CPU {_verdict(span * ratio)}"
        )
        out.append(f"  {basis}: CPU {_verdict(span)}; {wall}")

    out.append("")
    truths = sorted({x.truth for x in lines if x.cpu_s is not None})
    store_strata = ", ".join(f"{t} ({stratum.get(t, 'not in split')})" for t in truths)
    plan_strata = ", ".join(
        f"{k} {v}" for k, v in sorted(Counter(stratum[t] for t in ids).items())
    )
    out.append(
        f"caveat: the store's truths are {store_strata or 'none'}; the plan's "
        f"strata are {plan_strata}. Costs measured on far truths may overstate "
        "the near and mid ones (LOG 2026-10-28)."
    )
    out.append(
        "caveat: units are scheduled one by one; the driver runs ORACLE, B-lib "
        "and B-np of a (truth, seed) as one job and at most "
        f"--max-heavy={args.max_heavy} B-sparse jobs at once, neither modelled. "
        "CPU time is a lower bound on uncontended wall time per worker."
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.description = __doc__.split("\n\n")[0]
    parser.set_defaults(workers=2)
    parser.add_argument("--store", type=Path, required=True, help="a campaign root")
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
    if not (args.store / TIMING_FILE).is_file():
        print(f"{args.store}: no {TIMING_FILE}", file=sys.stderr)
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
