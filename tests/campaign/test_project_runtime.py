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
