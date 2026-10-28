"""``scripts/project_runtime.py``: the baselines' run-time projection, on
synthetic ``timing.jsonl`` files in a tmp dir and the dev split's plan."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SPLIT = Path(__file__).resolve().parents[2] / "data" / "v2" / "truths" / "dev"
#: sha256 of the adopted (Linux) dev split's ``truths.json`` (LOG 2026-10-27).
DEV_SPLIT_SHA256 = "604acec5359d9a33b8aedc500de8dff14600041225c386766ec3544b2e8d4420"
#: One truth, seed 1, no LLM arm: the smallest plan.
TINY = ("--ids", "dev-000", "--seeds", "1", "--arms", "")


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    digest = hashlib.sha256((SPLIT / "truths.json").read_bytes()).hexdigest()
    assert digest == DEV_SPLIT_SHA256, f"{SPLIT} is not the adopted dev split"
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("project_runtime")


def line(
    system: str,
    cpu: float | None,
    wall: float = 1.0,
    *,
    truth: str = "dev-000",
    seed: int = 1,
    job: tuple[float, int] | None = None,
    kind: str = "baseline",
) -> dict[str, object]:
    """A driver timing line; ``cpu=None`` drops the CPU fields (an older
    store); ``job`` is (job CPU, job units), else a one-unit job."""
    rec: dict[str, object] = {
        "kind": kind,
        "ok": True,
        "seed": seed,
        "system": system,
        "truth": truth,
        "wall_s": wall,
    }
    if cpu is not None:
        job_cpu, units = (cpu, 1) if job is None else job
        rec |= {
            "cpu_s": cpu,
            "cpu_children_s": 0.0,
            "job_cpu_s": job_cpu,
            "job_cpu_children_s": 0.0,
            "job_units": units,
            "job_wall_s": wall * units,
        }
    return rec


def store(root: Path, *records: dict[str, object]) -> Path:
    root.mkdir()
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    (root / "timing.jsonl").write_text(text, encoding="utf-8")
    return root


def run(module: ModuleType, root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(["--store", str(root), *args])
    return code, out.getvalue(), err.getvalue()


def test_lpt_makespan_on_hand_checked_cases(script: ModuleType) -> None:
    # 7->w0, 5->w1, 4->w1 (9), 3->w0 (10), 1->w1 (10)
    assert script.lpt_makespan([1.0, 3.0, 7.0, 4.0, 5.0], 2) == 10.0
    # LPT, not the optimum: 3,3 | 2,2 | 2 -> 7, where {3,3} {2,2,2} gives 6
    assert script.lpt_makespan([2.0, 3.0, 2.0, 3.0, 2.0], 2) == 7.0
    assert script.lpt_makespan([2.0, 3.0, 4.0], 1) == 9.0
    assert script.lpt_makespan([2.0, 3.0], 5) == 3.0
    assert script.lpt_makespan([], 2) == 0.0
    with pytest.raises(ValueError, match="workers"):
        script.lpt_makespan([1.0], 0)


def test_the_default_plan_is_the_pilot_planners(
    script: ModuleType, tmp_path: Path
) -> None:
    """The P3 plan (20 truths x 3 seeds, B-sym@10F on seed 1 of the first 10):
    7 systems x 60 + 10 non-LLM units, 4 arms x 60 LLM units, as the driver's
    own planner enumerates them."""
    from sciagent.campaign.plan import baseline_units, llm_units

    pilot = importlib.import_module("pilot")
    plan = ("--truths", "20", "--seeds", "1,2,3", "--b10f-truths", "10")
    args = pilot.build_parser().parse_args(list(plan))
    ids = [r.id for r in pilot.select(pilot.load_split(SPLIT), 20, None)]
    config = pilot.campaign_config(args)
    n_base = len(baseline_units(ids, config))
    n_llm = len(llm_units(ids, config))
    assert (n_base, n_llm) == (7 * 60 + 10, 4 * 60)

    root = store(tmp_path / "s", line("B-sym", 100.0))
    code, out, _ = run(script, root, *plan)
    assert code == 0
    assert (
        f"planned units: {n_base + n_llm}; non-LLM (projected): {n_base}; "
        f"LLM (excluded): {n_llm}"
    ) in out
    for arm in ("AG-c-S/named", "AG-o-S/named", "AG-c-S/anon", "AG-o-S/anon"):
        assert f"  excluded LLM arm {arm}: 60 units" in out
    assert "  B-sym@10F        10  not measured; left out of the projection" in out
    assert "  B-sym            60   1" in out


def test_lines_without_cpu_figures_are_not_zero_cost(
    script: ModuleType, tmp_path: Path
) -> None:
    """An older store's lines carry no CPU figures: they are counted as lines
    without them, never costed at 0 s; a null figure is the same."""
    null = line("B-rand", 50.0) | {"cpu_children_s": None}
    root = store(
        tmp_path / "s",
        line("B-sym", 3600.0, 7200.0),
        line("B-rand", None, 999.0),
        null,
        line("ORACLE", 1.0, kind="llm-score"),
    )
    timing = script.read_timing(root / "timing.jsonl")
    assert [x.cpu_s for x in timing.lines] == [3600.0, None, None]
    assert timing.other_kinds == (("llm-score", 1),)
    assert set(script.system_stats(timing.lines)) == {"B-sym"}

    code, out, _ = run(script, root, *TINY, "--systems", "B-sym,B-rand")
    assert code == 0
    assert "3 baseline lines (1 with CPU figures, 2 without)" in out
    assert "other kinds, not used: llm-score 1" in out
    assert (
        "  B-rand            1  not measured (2 lines without CPU figures); "
        "left out of the projection"
    ) in out
    assert "NOT MEASURED, left out: B-rand (1 units)" in out
    # B-sym alone: 3600 s on one of 2 workers, wall/CPU 2.00
    assert (
        "  unit mean: CPU 1.0 h <= 48 h (dm-devbox); x 2.00 wall/CPU 2.0 h "
        "<= 48 h (dm-devbox)"
    ) in out


def test_a_missing_system_is_listed_never_dropped(
    script: ModuleType, tmp_path: Path
) -> None:
    root = store(tmp_path / "s", line("B-sym", 3600.0, 3600.0))
    code, out, _ = run(script, root, *TINY)
    assert code == 0
    for system in ("ORACLE", "B-lib", "B-np", "B-rand", "B-sparse", "planted-hint"):
        assert f"  {system:<13}     1  not measured; left out" in out
    assert "  B-sym@10F         1  not measured; left out" in out
    assert (
        "NOT MEASURED, left out: ORACLE (1 units), B-lib (1 units), B-np (1 "
        "units), B-rand (1 units), B-sparse (1 units), planted-hint (1 units), "
        "B-sym@10F (1 units); every figure is a lower bound"
    ) in out
    assert "scheduling of 1 units onto 2 workers" in out


def test_assume_costs_an_unmeasured_system_and_says_so(
    script: ModuleType, tmp_path: Path
) -> None:
    """B-sym@10F = 10 x B-sym: 36000 s and 3600 s on 2 workers is 10 h of
    CPU, 20 h at the measured wall/CPU of 2; on 1 worker, 11 h and 22 h."""
    root = store(tmp_path / "s", line("B-sym", 3600.0, 7200.0))
    plan = (*TINY, "--systems", "B-sym,B-sym@10F")
    assume = ("--assume", "B-sym@10F=10xB-sym")
    code, out, _ = run(script, root, *plan, *assume)
    assert code == 0, out
    assert "ASSUMPTION (not measured): B-sym@10F = 10 x B-sym per unit" in out
    assert "not measured; ASSUMED B-sym@10F = 10 x B-sym" in out
    assert "NOT MEASURED" not in out
    assert (
        "  unit mean: CPU 10.0 h <= 48 h (dm-devbox); x 2.00 wall/CPU 20.0 h "
        "<= 48 h (dm-devbox)"
    ) in out
    code, out, _ = run(script, root, *plan, *assume, "--workers", "1")
    assert "  unit max: CPU 11.0 h <= 48 h (dm-devbox); x 2.00 wall/CPU 22.0 h" in out

    for bad, why in (
        ("B-sym=2xB-rand", "is measured in the store"),
        ("B-sym@10F=10xB-rand", "B-rand has no CPU figure"),
        ("B-sym@10F=tenxB-sym", "want SYSTEM=FACTORxOTHER"),
        ("B-sym@10F=0xB-sym", "FACTOR > 0"),
        ("B-sparse=2xB-sym", "B-sparse has no units in this plan"),
    ):
        code, out, err = run(script, root, *plan, "--assume", bad)
        assert (code, out) == (2, ""), bad
        assert why in err, (bad, err)


def test_job_overhead_is_shared_by_the_jobs_units(
    script: ModuleType, tmp_path: Path
) -> None:
    """A three-unit job of 30 s CPU whose units took 5, 6 and 7 s has 12 s of
    shared overhead (data simulation): 4 s more per unit. A job with a line
    missing from the store has no +overhead figure."""
    job = (30.0, 3)
    lines = (
        line("ORACLE", 5.0, job=job),
        line("B-lib", 6.0, job=job),
        line("B-np", 7.0, job=job),
        line("ORACLE", 1.0, truth="dev-001", job=(9.0, 3)),
    )
    timing = script.read_timing(store(tmp_path / "s", *lines) / "timing.jsonl")
    assert script.with_overhead(timing.lines) == [9.0, 10.0, 11.0, None]
    stats = script.system_stats(timing.lines)
    assert (stats["ORACLE"].mean, stats["ORACLE"].full_mean) == (3.0, 9.0)
    assert stats["ORACLE"].full_n == 1


def test_output_does_not_depend_on_line_order(
    script: ModuleType, tmp_path: Path
) -> None:
    lines = [
        line("B-sym", 900.0, 1800.0, truth="dev-013"),
        line("B-sym", 1100.0, 1300.0, truth="dev-008"),
        line("ORACLE", 50.0, 60.0, job=(70.0, 2)),
        line("B-lib", 10.0, 11.0, job=(70.0, 2)),
        line("B-rand", 2000.0, 4000.0, seed=2),
    ]
    a = run(script, store(tmp_path / "a", *lines), *TINY)
    b = run(script, store(tmp_path / "b", *reversed(lines)), *TINY)
    assert a[0] == b[0] == 0 and a[2] == b[2] == ""
    # the first line names the store's path; everything after it must match
    assert a[1].split("\n", 1)[1] == b[1].split("\n", 1)[1]
    assert "dev-008 (far), dev-013 (far)" in a[1]


#: A single store exercising every part of the original output: a multi-unit
#: job, a repeated unit, a line without CPU figures, another kind, a store
#: system not in the plan, and an assumption.
GOLDEN_LINES = (
    line("ORACLE", 5.0, 6.0, job=(30.0, 2)),
    line("B-lib", 7.0, 6.0, job=(30.0, 2)),
    line("B-sym", 900.0, 1800.0),
    line("B-sym", 1000.0, 1200.0),
    line("B-rand", None, 999.0, truth="dev-001", seed=2),
    line("B-sparse", 50.0, 70.0, truth="dev-001"),
    line("ORACLE", 1.0, kind="llm-score"),
)
GOLDEN_ARGS = (
    "--ids",
    "dev-000,dev-001",
    "--seeds",
    "1,2",
    "--arms",
    "",
    "--systems",
    "ORACLE,B-lib,B-sym,B-rand,B-sym@10F",
    "--assume",
    "B-sym@10F=3xB-sym",
)

#: The script's output on :data:`GOLDEN_LINES` with :data:`GOLDEN_ARGS`, as
#: the single-store script of v2 3e8bf91 printed it, after the first line
#: (which names the store path). One ``--store`` and none of the new options
#: must keep giving these bytes.
GOLDEN_OUTPUT = (
    "plan: 2 truths x seeds 1,2; B-sym@10F on control seeds 1, truths: all",
    "planned units: 18; non-LLM (projected): 18; LLM (excluded): 0",
    "store: 6 baseline lines (5 with CPU figures, 1 without); other kinds, "
    "not used: llm-score 1",
    "  repeated units in the store (every line counted): 1",
    "  store systems not in this plan: B-sparse",
    "",
    "per-unit CPU, cpu_s + cpu_children_s (s); +overhead adds an equal share "
    "of the job's CPU beyond its units' (data simulation)",
    "  system         plan   n      mean       max  wall/CPU  +ovh mean  +ovh max",
    "  ORACLE            4   1       5.0       5.0      1.20       14.0      14.0",
    "  B-lib             4   1       7.0       7.0      0.86       16.0      16.0",
    "  B-sym             4   2     950.0    1000.0      1.58      950.0    1000.0",
    "  B-rand            4  not measured (1 lines without CPU figures); left "
    "out of the projection",
    "  B-sym@10F         2  not measured; ASSUMED B-sym@10F = 3 x B-sym",
    "store totals: unit CPU 1962.0 s over 5 lines; pool CPU (sum of job CPU / "
    "job_units) 1980.0 s over 5 lines",
    "measured wall/CPU over the store: unit 1.57; job 1.56",
    "ASSUMPTION (not measured): B-sym@10F = 3 x B-sym per unit",
    "",
    "projection: makespan of longest-first list scheduling of 14 units onto 2 "
    "workers; 48-hour line",
    "  NOT MEASURED, left out: B-rand (4 units); every figure is a lower bound",
    "  unit mean: CPU 1.3 h <= 48 h (dm-devbox); x 1.57 wall/CPU 2.1 h <= 48 h "
    "(dm-devbox)",
    "  unit max: CPU 1.4 h <= 48 h (dm-devbox); x 1.57 wall/CPU 2.2 h <= 48 h "
    "(dm-devbox)",
    "  +overhead mean: CPU 1.3 h <= 48 h (dm-devbox); x 1.56 wall/CPU 2.1 h "
    "<= 48 h (dm-devbox)",
    "  +overhead max: CPU 1.4 h <= 48 h (dm-devbox); x 1.56 wall/CPU 2.2 h "
    "<= 48 h (dm-devbox)",
    "",
    "caveat: the store's truths are dev-000 (far), dev-001 (near); the plan's "
    "strata are far 1, near 1. Costs measured on far truths may overstate the "
    "near and mid ones (LOG 2026-10-28).",
    "caveat: units are scheduled one by one; the driver runs ORACLE, B-lib and "
    "B-np of a (truth, seed) as one job and at most --max-heavy=2 B-sparse jobs "
    "at once, neither modelled. CPU time measured under load is not a lower "
    "bound on uncontended wall time: on a hyperthreaded host a contended unit "
    "accrues more CPU seconds for the same work (LOG 2026-10-28).",
)


def test_single_store_output_is_unchanged(script: ModuleType, tmp_path: Path) -> None:
    root = store(tmp_path / "s", *GOLDEN_LINES)
    code, out, err = run(script, root, *GOLDEN_ARGS)
    assert (code, err) == (0, "")
    first, rest = out.split("\n", 1)
    assert first == (
        "Run-time projection of the P3 non-LLM plan at 2 workers, from "
        f"{root / 'timing.jsonl'}"
    )
    assert rest == "\n".join(GOLDEN_OUTPUT) + "\n"


def runs(module: ModuleType, *args: str) -> tuple[int, str, str]:
    """``main`` with the arguments as given (stores included)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(list(args))
    return code, out.getvalue(), err.getvalue()


def test_stores_pool_and_a_unit_in_two_stores_is_their_mean(
    script: ModuleType, tmp_path: Path
) -> None:
    """B-sym dev-000 is in stores a (1000 s CPU, 2000 s wall) and b (1400 s,
    1400 s): one unit of 1200 s CPU and 1700 s wall. Store c has no CPU
    figures: it adds nothing to CPU, and its B-rand dev-001 is no unit."""
    a = store(
        tmp_path / "a", line("B-sym", 1000.0, 2000.0), line("B-rand", 3000.0, 3000.0)
    )
    b = store(
        tmp_path / "b",
        line("B-sym", 1400.0, 1400.0),
        line("B-sym", 600.0, 900.0, truth="dev-001"),
    )
    c = store(
        tmp_path / "c",
        line("B-sym", None, 100.0, truth="dev-001"),
        line("B-rand", None, 50.0, truth="dev-001"),
    )
    timings = [script.read_timing(s / "timing.jsonl").lines for s in (a, b, c)]
    measured, duplicates = script.pool(timings)
    assert duplicates == 1
    merged = [m for m in measured if (m.system, m.truth) == ("B-sym", "dev-000")]
    assert [(m.cpu_s, m.wall_s, m.stores) for m in merged] == [(1200.0, 1700.0, 2)]
    assert sorted((m.system, m.truth) for m in measured) == [
        ("B-rand", "dev-000"),
        ("B-sym", "dev-000"),
        ("B-sym", "dev-001"),
    ]

    stores = ("--store", str(a), "--store", str(b), "--store", str(c))
    code, out, err = runs(script, *stores, *TINY, "--systems", "B-sym,B-rand")
    assert (code, err) == (0, ""), err
    assert out.startswith(
        "Run-time projection of the P3 non-LLM plan at 2 workers, from "
        f"{a / 'timing.jsonl'}, {b / 'timing.jsonl'}, {c / 'timing.jsonl'}\n"
    )
    assert (
        f"store {c / 'timing.jsonl'}: 2 baseline lines (0 with CPU figures, 2 "
        "without); other kinds, not used: none; contributes no CPU"
    ) in out
    assert (
        "pooled CPU: 3 measured units from 4 lines with CPU figures; units "
        "measured in more than one store: 1, each one unit at the mean of its "
        "lines' CPU and wall"
    ) in out
    assert "  B-sym             1   2     900.0    1200.0      1.44" in out
    assert "  B-rand            1   1    3000.0    3000.0      1.00" in out
    # 3000 s and 900 s on 2 workers; wall/CPU over the CPU lines 7300/6000
    assert (
        "  unit mean: CPU 0.8 h <= 48 h (dm-devbox); x 1.22 wall/CPU 1.0 h "
        "<= 48 h (dm-devbox)"
    ) in out
    assert "uncontended estimate" not in out

    code, out, err = runs(script, "--store", str(a), "--store", str(a), *TINY)
    assert (code, out) == (2, "")
    assert "given twice" in err


def test_a_measured_b10f_is_costed_like_any_system(
    script: ModuleType, tmp_path: Path
) -> None:
    """B-sym@10F measured in a second store is costed from it, no --assume:
    36000 s and 3600 s on 2 workers is 10 h; --assume for it is refused."""
    a = store(tmp_path / "a", line("B-sym", 3600.0, 3600.0))
    b = store(tmp_path / "b", line("B-sym@10F", 36000.0, 36000.0))
    args = ("--store", str(a), "--store", str(b), *TINY)
    plan = ("--systems", "B-sym,B-sym@10F")
    code, out, err = runs(script, *args, *plan)
    assert (code, err) == (0, ""), err
    assert "  B-sym@10F         1   1   36000.0   36000.0      1.00" in out
    assert "NOT MEASURED" not in out
    assert "  unit mean: CPU 10.0 h <= 48 h (dm-devbox)" in out
    code, out, err = runs(script, *args, *plan, "--assume", "B-sym@10F=10xB-sym")
    assert (code, out) == (2, "")
    assert "B-sym@10F is measured in the store" in err


def test_by_stratum_costs_a_unit_from_its_stratum_and_names_the_fallback(
    script: ModuleType, tmp_path: Path
) -> None:
    """Plan: dev-000 (far), dev-001 (near), dev-010 (mid), seed 1. Measured:
    B-sym far 3600 s and near 7200 s, B-rand far 36000 s and near 72000 s;
    mid falls back to each system's all-strata mean (5400 s, 54000 s).
    By stratum, LPT on 2 workers: 72000 | 54000 + 36000 -> 90000 s, 25 h.
    All-strata means: 54000 x 3 and 5400 x 3 -> 108000 s, 30 h."""
    root = store(
        tmp_path / "s",
        line("B-sym", 3600.0, 3600.0),
        line("B-sym", 7200.0, 7200.0, truth="dev-002"),
        line("B-rand", 36000.0, 36000.0),
        line("B-rand", 72000.0, 72000.0, truth="dev-002"),
    )
    ids = ("--ids", "dev-000,dev-001,dev-010", "--seeds", "1", "--arms", "")
    plan = (*ids, "--systems", "B-sym,B-rand")
    code, out, err = run(script, root, *plan, "--by-stratum")
    assert (code, err) == (0, ""), err
    assert "  system       stratum  plan   n      mean  +ovh mean  source" in out, out
    assert "  B-sym        far         1   1    3600.0     3600.0  this stratum" in out
    assert "  B-sym        near        1   1    7200.0     7200.0  this stratum" in out
    assert (
        "  B-sym        mid         1   0    5400.0     5400.0  all strata, n 2 "
        "(mid not measured)"
    ) in out
    assert (
        "  B-rand       mid         1   0   54000.0    54000.0  all strata, n 2 "
        "(mid not measured)"
    ) in out
    assert "  unit mean: CPU 30.0 h <= 48 h (dm-devbox); x 1.00 wall/CPU" in out
    assert (
        "  stratum mean: CPU 25.0 h <= 48 h (dm-devbox); x 1.00 wall/CPU 25.0 h "
        "<= 48 h (dm-devbox)"
    ) in out
    assert (
        "  +overhead stratum mean: CPU 25.0 h <= 48 h (dm-devbox); x 1.00 "
        "wall/CPU 25.0 h <= 48 h (dm-devbox)"
    ) in out

    code, out, _ = run(script, root, *plan)
    assert "stratum mean" not in out


def test_by_stratum_with_an_assumption_and_an_unmeasured_system(
    script: ModuleType, tmp_path: Path
) -> None:
    root = store(tmp_path / "s", line("B-sym", 3600.0, 3600.0))
    plan = (*TINY, "--systems", "B-sym,B-sym@10F,B-rand", "--by-stratum")
    code, out, err = run(script, root, *plan, "--assume", "B-sym@10F=10xB-sym")
    assert (code, err) == (0, ""), err
    assert (
        "  B-sym@10F    far         1   0   36000.0    36000.0  ASSUMED B-sym@10F "
        "= 10 x B-sym (B-sym: this stratum)"
    ) in out
    assert (
        "  B-rand       far         1   0         -          -  not measured; "
        "left out of the projection"
    ) in out
    assert "  stratum mean: CPU 10.0 h <= 48 h (dm-devbox)" in out

    # with --wall-from: B-sym min(3600, 3600, 1800) = 1800 s, so 18000 s
    wall = store(tmp_path / "w", line("B-sym", None, 1800.0))
    code, out, err = run(
        script, root, *plan, "--assume", "B-sym@10F=10xB-sym", "--wall-from", str(wall)
    )
    assert (code, err) == (0, ""), err
    assert (
        "  B-sym@10F      not measured; ASSUMED B-sym@10F = 10 x B-sym: min mean "
        "18000.0 s"
    ) in out
    assert "  uncontended stratum mean, min(CPU, walls): 5.0 h" in out


def test_wall_from_gives_an_uncontended_estimate(
    script: ModuleType, tmp_path: Path
) -> None:
    """Each measured unit costs min(its CPU, every wall of it in any store):
    B-sym min(3600, 7200, 1800) = 1800 s, B-rand min(36000, 40000, 50000,
    18000) = 18000 s, so 5 h on 2 workers against 10 h of CPU. B-sym dev-001
    is only in the wall-only store and is not a unit. B-sym dev-002 has no
    wall but its own: min(1800, 1800), counted under "one run"."""
    a = store(
        tmp_path / "a",
        line("B-sym", 3600.0, 7200.0),
        line("B-rand", 36000.0, 40000.0),
        line("B-sym", 1800.0, 1800.0, truth="dev-002"),
    )
    c = store(tmp_path / "c", line("B-rand", None, 18000.0))
    w = store(
        tmp_path / "w",
        line("B-sym", None, 1800.0),
        line("B-rand", None, 50000.0),
        line("B-sym", None, 10.0, truth="dev-001"),
    )
    args = ("--store", str(a), "--store", str(c), "--wall-from", str(w), *TINY)
    plan = ("--systems", "B-sym,B-rand")
    code, out, err = runs(script, *args, *plan)
    assert (code, err) == (0, ""), err
    assert (
        f"wall only (--wall-from) {w / 'timing.jsonl'}: 3 baseline lines; "
        "its CPU figures are not used"
    ) in out
    assert "  B-sym             1   2    2700.0    3600.0      1.67" in out
    assert (
        "uncontended estimate: each measured unit costs min(its CPU, its wall_s "
        "in every store that has it); an upper bound on its uncontended cost "
        "under the hyperthreading bias (LOG 2026-10-28 correction)"
    ) in out
    assert (
        "  system          n  CPU mean  min mean   min/CPU  wall<CPU   one run"
    ) in out
    assert (
        "  B-sym           2    2700.0    1800.0      0.67         1         1" in out
    )
    assert (
        "  B-rand          1   36000.0   18000.0      0.50         1         0" in out
    )
    assert "  unit mean: CPU 10.0 h <= 48 h (dm-devbox)" in out
    assert (
        "  uncontended unit mean, min(CPU, walls): 5.0 h <= 48 h (dm-devbox); "
        "an upper bound on uncontended cost"
    ) in out
    assert "uncontended stratum mean" not in out

    code, out, err = runs(script, *args, *plan, "--by-stratum")
    assert (code, err) == (0, ""), err
    assert (
        "  uncontended stratum mean, min(CPU, walls): 5.0 h <= 48 h (dm-devbox); "
        "an upper bound on uncontended cost"
    ) in out

    code, out, err = runs(script, "--store", str(a), "--wall-from", str(a), *TINY)
    assert (code, out) == (2, "")
    assert "given twice" in err


#: dev-000 softplus (far), dev-001 identity (near), dev-014 exp (near).
LINK_PLAN = (
    "--ids",
    "dev-000,dev-001,dev-014",
    "--seeds",
    "1",
    "--arms",
    "",
    "--systems",
    "B-sym,B-rand",
)


def link_store(root: Path) -> Path:
    """B-sym and B-rand measured on dev-008 (softplus, far) and dev-010
    (identity, mid): no exp truth, and no near one."""
    return store(
        root,
        line("B-sym", 3600.0, 3600.0, truth="dev-008"),
        line("B-sym", 7200.0, 7200.0, truth="dev-010"),
        line("B-rand", 36000.0, 36000.0, truth="dev-008"),
        line("B-rand", 72000.0, 72000.0, truth="dev-010"),
    )


def test_by_link_costs_a_unit_from_its_link_and_names_the_fallback(
    script: ModuleType, tmp_path: Path
) -> None:
    """dev-001 (identity, near) costs dev-010's figures (identity, mid) and
    dev-000 dev-008's; exp falls back to each system's all-links mean (5400 s,
    54000 s). LPT on 2 workers: 72000 | 54000 + 36000 -> 90000 s, 25 h."""
    root = link_store(tmp_path / "s")
    code, out, err = run(script, root, *LINK_PLAN, "--by-link")
    assert (code, err) == (0, ""), err
    assert (
        "by link (--by-link): a planned unit costs the mean of its system's "
        "measured units on truths of its truth's link (from the split); where "
        "there are none, the system's all-links mean, as the source says (s)"
    ) in out
    assert "  system       link     plan   n      mean  +ovh mean  source" in out, out
    assert (
        "  B-sym        exp         1   0    5400.0     5400.0  all links, n 2 "
        "(exp not measured)"
    ) in out
    assert "  B-sym        identity    1   1    7200.0     7200.0  this link" in out
    assert "  B-sym        softplus    1   1    3600.0     3600.0  this link" in out
    assert (
        "  B-rand       exp         1   0   54000.0    54000.0  all links, n 2 "
        "(exp not measured)"
    ) in out
    assert (
        "  link mean: CPU 25.0 h <= 48 h (dm-devbox); x 1.00 wall/CPU 25.0 h "
        "<= 48 h (dm-devbox)"
    ) in out
    assert (
        "  +overhead link mean: CPU 25.0 h <= 48 h (dm-devbox); x 1.00 wall/CPU "
        "25.0 h <= 48 h (dm-devbox)"
    ) in out
    assert (
        "caveat: the store's truths' links are dev-008 (softplus), dev-010 "
        "(identity); the plan's links are exp 1, identity 1, softplus 1."
    ) in out
    assert "stratum mean" not in out

    # by stratum the same store says something else: dev-001 is near, and no
    # near truth is measured
    code, out, err = run(script, root, *LINK_PLAN, "--by-stratum")
    assert (code, err) == (0, ""), err
    assert "link mean" not in out
    assert "by link" not in out
    assert "links are" not in out


def test_by_link_with_wall_from_adds_an_uncontended_link_line(
    script: ModuleType, tmp_path: Path
) -> None:
    """B-rand on dev-008 has a wall of 18000 s: softplus 18000, exp the
    all-links mean (18000 + 72000) / 2 = 45000. With B-sym 3600, 7200, 5400:
    LPT 72000 | 45000 + 18000 + 7200 + 5400 -> 75600 s, 21 h."""
    root = link_store(tmp_path / "s")
    wall = store(tmp_path / "w", line("B-rand", None, 18000.0, truth="dev-008"))
    code, out, err = run(
        script, root, *LINK_PLAN, "--by-link", "--wall-from", str(wall)
    )
    assert (code, err) == (0, ""), err
    assert (
        "  uncontended link mean, min(CPU, walls): 21.0 h <= 48 h (dm-devbox); "
        "an upper bound on uncontended cost"
    ) in out
    assert "uncontended stratum mean" not in out


def test_assume_link_costs_an_unmeasured_link_as_another_and_says_so(
    script: ModuleType, tmp_path: Path
) -> None:
    """--assume-link exp=softplus: exp units cost the softplus figures (3600
    s, 36000 s). LPT: 72000 | 36000 + 36000 + 3600 + 3600, w0 + 7200 ->
    79200 s, 22 h."""
    root = link_store(tmp_path / "s")
    args = (*LINK_PLAN, "--by-link", "--assume-link", "exp=softplus")
    code, out, err = run(script, root, *args)
    assert (code, err) == (0, ""), err
    assert "ASSUMPTION (not measured): link exp costed as link softplus" in out
    assert (
        "  B-sym        exp         1   0    3600.0     3600.0  ASSUMED link exp "
        "= softplus (softplus: this link)"
    ) in out
    assert (
        "  B-rand       exp         1   0   36000.0    36000.0  ASSUMED link exp "
        "= softplus (softplus: this link)"
    ) in out
    assert "  link mean: CPU 22.0 h <= 48 h (dm-devbox)" in out

    for bad, why in (
        ("identity=softplus", "identity is measured in the store"),
        ("exp=probit", "probit has no measured units"),
        ("exp", "want LINK=OTHER"),
        ("exp=exp", "cannot be costed by itself"),
        ("logit=softplus", "logit has no units in this plan"),
    ):
        code, out, err = run(
            script, root, *LINK_PLAN, "--by-link", "--assume-link", bad
        )
        assert (code, out) == (2, ""), bad
        assert why in err, (bad, err)
    code, out, err = run(script, root, *LINK_PLAN, "--assume-link", "exp=softplus")
    assert (code, out) == (2, "")
    assert "--assume-link needs --by-link" in err


def test_by_link_with_an_assumed_and_an_unmeasured_system(
    script: ModuleType, tmp_path: Path
) -> None:
    root = store(tmp_path / "s", line("B-sym", 3600.0, 3600.0))
    plan = (*TINY, "--systems", "B-sym,B-sym@10F,B-rand", "--by-link")
    code, out, err = run(script, root, *plan, "--assume", "B-sym@10F=10xB-sym")
    assert (code, err) == (0, ""), err
    assert (
        "  B-sym@10F    softplus    1   0   36000.0    36000.0  ASSUMED B-sym@10F "
        "= 10 x B-sym (B-sym: this link)"
    ) in out
    assert (
        "  B-rand       softplus    1   0         -          -  not measured; "
        "left out of the projection"
    ) in out
    assert "  link mean: CPU 10.0 h <= 48 h (dm-devbox)" in out


def test_by_link_leaves_the_by_stratum_output_in_place(
    script: ModuleType, tmp_path: Path
) -> None:
    """Every line --by-stratum prints is printed, in order, with --by-link
    added: the link lines only interleave."""
    root = link_store(tmp_path / "s")
    wall = store(tmp_path / "w", line("B-rand", None, 18000.0, truth="dev-008"))
    common = (*LINK_PLAN, "--wall-from", str(wall), "--by-stratum")
    code, alone, err = run(script, root, *common)
    assert (code, err) == (0, ""), err
    code, both, err = run(script, root, *common, "--by-link")
    assert (code, err) == (0, ""), err
    rest = iter(both.splitlines())
    for want in alone.splitlines():
        assert any(got == want for got in rest), want
    assert len(both.splitlines()) > len(alone.splitlines())
