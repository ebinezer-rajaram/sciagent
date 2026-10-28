"""Worker CPU time per job in ``timing.jsonl``, never in a cell.

Wall times on a shared machine swing more than 10x with other load (JOURNAL
session 6), so each pool job's timing line also carries the CPU time the
worker spent on it, measured with ``getrusage`` deltas inside the worker
(workers are reused across jobs). Like wall time, CPU time is not a property
of the cell: it must never enter a reading, a detail or an address, so two
runs whose CPU figures differ still give byte-identical stores.
"""

from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from audit_support import put_session, unit_json
from campaign_support import entry, environment, tiny_config
from test_audit_driver import run_campaign
from test_campaign import clean

from sciagent.campaign import cpu, execute, llm
from sciagent.campaign import driver as driver_module
from sciagent.campaign.audit import compare_stores
from sciagent.campaign.execute import BaselineJob, ScoreJob
from sciagent.campaign.plan import Unit
from sciagent.campaign.store import TIMING_FILE, ResultStore
from sciagent.core.errors import SciAgentError

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
#: Per unit, over the same interval as the unit's ``wall_s``.
UNIT_FIELDS = ("cpu_s", "cpu_children_s")
#: Per pool job, over the whole job in the worker (shared by its units' lines).
JOB_FIELDS = ("job_wall_s", "job_cpu_s", "job_cpu_children_s", "job_units")

#: Busy-loops for about ``s`` seconds of CPU in a child Python.
CHILD_BURN = "import time\nt=time.process_time()\nwhile time.process_time()-t<{s}:pass"


def _burn(seconds: float) -> None:
    start = time.process_time()
    while time.process_time() - start < seconds:
        pass


def _lines(root: Path) -> list[dict[str, Any]]:
    text = (root / TIMING_FILE).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------


def test_the_fields_are_cpu_self_and_children() -> None:
    assert cpu.CPU_FIELDS == UNIT_FIELDS


def test_cpu_time_counts_this_process_and_its_reaped_children_apart() -> None:
    mark = cpu.cpu_mark()
    _burn(0.2)
    own = cpu.cpu_since(mark)
    assert own["cpu_s"] is not None and own["cpu_s"] >= 0.15
    assert own["cpu_children_s"] is not None and own["cpu_children_s"] < 0.05

    mark = cpu.cpu_mark()
    subprocess.run([sys.executable, "-c", CHILD_BURN.format(s=0.3)], check=True)
    child = cpu.cpu_since(mark)
    assert child["cpu_children_s"] is not None and child["cpu_children_s"] >= 0.25
    assert child["cpu_s"] is not None and child["cpu_s"] < 0.25


def test_a_child_is_counted_only_once_it_has_been_waited_for() -> None:
    """``RUSAGE_CHILDREN`` holds only reaped children: a child that has exited
    but not been waited for is not in the delta yet (the documented gap)."""
    mark = cpu.cpu_mark()
    proc = subprocess.Popen([sys.executable, "-c", CHILD_BURN.format(s=0.3)])
    # Wait for the exit without reaping the child.
    os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
    unreaped = cpu.cpu_since(mark)
    assert unreaped["cpu_children_s"] is not None
    assert unreaped["cpu_children_s"] < 0.05
    assert proc.wait() == 0
    reaped = cpu.cpu_since(mark)
    assert reaped["cpu_children_s"] is not None and reaped["cpu_children_s"] >= 0.25


def test_cpu_time_without_a_mark_is_stated_absent_not_zero() -> None:
    assert cpu.cpu_since(None) == {"cpu_s": None, "cpu_children_s": None}


# --------------------------------------------------------------------------
# Per unit, in the job functions
# --------------------------------------------------------------------------


def test_a_baseline_unit_carries_its_cpu_time_beside_its_wall_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_system(system: str, job: BaselineJob, data: Any) -> Any:
        _burn(0.2)
        raise SciAgentError("no proposal produced a certified fit")

    monkeypatch.setattr(execute, "_run_system", run_system)
    job = BaselineJob(entry("hawkes"), 1, ("B-rand",), environment(), tiny_config())
    (result,) = execute.run_baseline_job(job)
    assert not result.ok
    assert result.timing is not None
    assert tuple(result.timing) == UNIT_FIELDS
    used = result.timing["cpu_s"]
    assert used is not None and 0.15 <= used <= result.wall_s + 0.05


def test_a_real_baseline_unit_carries_run_and_cpu_time() -> None:
    job = BaselineJob(entry("hawkes"), 1, ("B-lib",), environment(), tiny_config())
    (result,) = execute.run_baseline_job(job)
    assert result.ok, result.detail
    assert result.timing is not None
    assert {"run_s", *UNIT_FIELDS} == set(result.timing)
    used = result.timing["cpu_s"]
    assert used is not None and used > 0.0
    assert not {*UNIT_FIELDS} & set(_keys(result.detail))
    assert not {*UNIT_FIELDS} & set(result.reading)


def test_a_score_job_carries_its_cpu_time() -> None:
    job = ScoreJob(
        entry("hawkes"), 1, "AG-c-H/named", None, (), environment(), tiny_config()
    )
    result = execute.run_score_job(job)
    assert result.ok and result.reading["status"] == 2.0
    assert result.timing is not None and tuple(result.timing) == UNIT_FIELDS
    assert result.timing["cpu_s"] is not None and result.timing["cpu_s"] >= 0.0


# --------------------------------------------------------------------------
# Per job, in the pool's entry point; the driver's timing lines
# --------------------------------------------------------------------------


def test_the_pool_entry_point_adds_the_jobs_cpu_time_to_each_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def three(job: BaselineJob) -> tuple[execute.UnitResult, ...]:
        _burn(0.2)
        return tuple(
            execute.UnitResult(s, True, {"status": 1.0}, {}, 0.01, {"run_s": 0.01})
            for s in job.systems
        )

    monkeypatch.setattr(driver_module, "run_baseline_job", three)
    job = BaselineJob(
        entry("hawkes"),
        1,
        ("ORACLE", "B-lib", "B-np"),
        environment(),
        tiny_config(),
    )
    results = driver_module._run_job(job)
    assert [r.system for r in results] == ["ORACLE", "B-lib", "B-np"]
    first = results[0].timing
    assert first is not None
    assert {"run_s", *JOB_FIELDS} == set(first)
    assert first["job_units"] == 3
    job_cpu, job_wall = first["job_cpu_s"], first["job_wall_s"]
    assert job_cpu is not None and job_cpu >= 0.15
    assert job_wall is not None and job_wall >= job_cpu - 0.05
    # One job's figures, shared by its units' lines.
    assert all(r.timing == first for r in results)


def test_every_job_line_states_its_cpu_time_and_cells_never_hold_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = run_campaign(tmp_path / "run", monkeypatch)
    lines = _lines(root)
    jobs = [x for x in lines if x["kind"] in ("baseline", "llm-score")]
    assert jobs and {x["kind"] for x in jobs} == {"baseline", "llm-score"}
    for x in jobs:
        assert {*UNIT_FIELDS, *JOB_FIELDS} <= set(x), x
        assert isinstance(x["job_cpu_s"], float) and x["job_cpu_s"] >= 0.0
        assert isinstance(x["job_units"], int) and x["job_units"] >= 1
    # The fake jobs measure nothing per unit: stated absent, never 0.
    assert {x["cpu_s"] for x in jobs} == {None}
    (session,) = [x for x in lines if x["kind"] == "llm-session"]
    # An in-thread session shares the driver's process: not attributable.
    assert session["cpu_s"] is None and session["cpu_children_s"] is None
    with ResultStore(root) as store:
        for cell in store.cells():
            assert not {"cpu_s", "cpu_children_s", *JOB_FIELDS} & set(_keys(cell))


def test_cpu_time_never_reaches_a_cell_or_an_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two driver runs whose CPU figures differ give byte-identical cells
    under the same addresses; only their timing lines differ."""
    readings = iter(range(1, 10_000))

    def fake_mark() -> cpu.CpuMark:
        step = float(next(readings))
        return cpu.CpuMark(self_s=step * scale, children_s=2 * step * scale)

    monkeypatch.setattr(cpu, "cpu_mark", fake_mark)
    scale = 1.0
    a = run_campaign(tmp_path / "a", monkeypatch)
    scale = 7.0
    b = run_campaign(tmp_path / "b", monkeypatch)
    every = compare_stores(a, b)
    assert every.identical, every
    assert every.differing == ()
    cpu_a = [x["job_cpu_s"] for x in _lines(a) if "job_cpu_s" in x]
    cpu_b = [x["job_cpu_s"] for x in _lines(b) if "job_cpu_s" in x]
    assert cpu_a and len(cpu_a) == len(cpu_b)
    assert all(x > 0 for x in cpu_a) and cpu_a != cpu_b
    for root in (a, b):
        details = sorted((root / "details").glob("*.json"))
        assert details
        for path in details:
            assert b"cpu" not in path.read_bytes(), path


# --------------------------------------------------------------------------
# An isolated session measures itself in its own process
# --------------------------------------------------------------------------


def test_an_isolated_sessions_process_measures_its_own_cpu_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    unit = Unit("hawkes", 1, "AG-c-H/named")
    harness = {"harness_wall_s": 0.5, "duration_api_ms": 7, "total_cost_usd": 0.1}

    def session(*args: object, **kwargs: object) -> llm.SessionOutcome:
        _burn(0.2)
        subprocess.run([sys.executable, "-c", CHILD_BURN.format(s=0.3)], check=True)
        reading = {"status": 1.0, "void": 0.0}
        return llm.SessionOutcome(
            unit, False, reading, {"outcome": "submitted"}, None, (), 0.0, harness
        )

    monkeypatch.setattr(llm, "run_session", session)
    receive, send = multiprocessing.Pipe(duplex=False)
    llm._child(
        send, unit, entry("hawkes"), environment(), tiny_config(), tmp_path, None
    )
    out: llm.SessionOutcome = receive.recv()
    receive.close()
    assert tuple(out.timing) == (*llm.SESSION_TIMING_FIELDS, *UNIT_FIELDS)
    assert {k: out.timing[k] for k in harness} == harness
    assert out.timing["cpu_s"] >= 0.15
    assert out.timing["cpu_children_s"] >= 0.25
    assert not {*UNIT_FIELDS} & set(out.reading) and not {*UNIT_FIELDS} & set(
        _keys(out.detail)
    )


def test_a_retryable_isolated_attempt_carries_no_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = Unit("hawkes", 1, "AG-c-H/named")

    def session(*args: object, **kwargs: object) -> llm.SessionOutcome:
        return llm.SessionOutcome(
            unit, True, {}, {"outcome": "retry"}, None, (), 0.0, {}
        )

    monkeypatch.setattr(llm, "run_session", session)
    receive, send = multiprocessing.Pipe(duplex=False)
    llm._child(
        send, unit, entry("hawkes"), environment(), tiny_config(), tmp_path, None
    )
    out: llm.SessionOutcome = receive.recv()
    receive.close()
    assert out.retryable and out.timing == {}


# --------------------------------------------------------------------------
# The pilot report shows CPU time beside wall time
# --------------------------------------------------------------------------


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` loaded in-process, with ``scripts/`` importable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "pilot_report", SCRIPTS / "pilot_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline_line(system: str, wall_s: float, **extra: object) -> dict[str, Any]:
    return {
        **unit_json("baseline", system, "dev-008", 1),
        "digest": "0" * 64,
        "ok": True,
        "wall_s": wall_s,
        **extra,
    }


def test_the_report_shows_cpu_time_and_reads_lines_without_it(
    tmp_path: Path, report: ModuleType
) -> None:
    job = {"job_wall_s": 30.0, "job_cpu_s": 6.0, "job_cpu_children_s": 0.0}
    with ResultStore(tmp_path) as store:
        # A line written before CPU time was recorded.
        store.log_timing(_baseline_line("B-lib", 5.0, run_s=4.0))
        store.log_timing(
            _baseline_line(
                "B-lib", 9.0, cpu_s=4.0, cpu_children_s=0.0, job_units=3, **job
            )
        )
        store.log_timing(
            _baseline_line(
                "B-lib", 11.0, cpu_s=6.0, cpu_children_s=0.5, job_units=3, **job
            )
        )
        # A fake job's line: per-unit CPU stated absent.
        store.log_timing(
            _baseline_line(
                "B-np", 2.0, cpu_s=None, cpu_children_s=None, job_units=1, **job
            )
        )
    times = report.timing(tmp_path)
    assert times["baseline:B-lib"].wall == [5.0, 9.0, 11.0]
    assert times["baseline:B-lib"].cpu == [4.0, 6.0]
    assert times["baseline:B-lib"].children == [0.0, 0.5]
    assert times["baseline:B-np"].cpu == []
    assert report.timing_rows(times) == [
        ["baseline:B-lib", "3", "9", "11", "2/3", "5", "6", "0"],
        ["baseline:B-np", "1", "2", "2", "0/1", "-", "-", "-"],
    ]
    pool = report.pool_cpu(tmp_path)
    # Each job's figures are shared by its units' lines: 6/3 + 6/3 + 6/1.
    assert (pool.cpu_s, pool.children_s, pool.lines, pool.of) == (10.0, 0.0, 3, 4)


def test_the_report_reads_session_cpu_times_beside_their_wall_times(
    tmp_path: Path, report: ModuleType
) -> None:
    from test_session_timing import _session, _timing_line

    with ResultStore(tmp_path) as store:
        keys = [
            str(put_session(store, "AG-o-S/named", seed=s).digest) for s in (1, 2, 3)
        ]
        _timing_line(store, keys[0], 1, 30.0, cpu_s=12.0, cpu_children_s=20.0)
        _timing_line(store, keys[1], 2, 10.0)  # an old line: no CPU time
        _timing_line(store, keys[2], 3, 20.0, cpu_s=None, cpu_children_s=None)
        sessions = [_session(store, k, s) for k, s in zip(keys, (1, 2, 3), strict=True)]
    lines = report.session_times(tmp_path, sessions)
    assert report.session_wall_times(tmp_path, sessions) == {
        keys[0]: 30.0,
        keys[1]: 10.0,
        keys[2]: 20.0,
    }
    assert (
        report.timed_text([lines[k].get("cpu_s") for k in keys], 3) == "12 (1/3 timed)"
    )
    assert report.timed_text([lines[k]["wall_s"] for k in keys], 3) == "20"
    assert report.timed_text([], 2) == "- (0/2 timed)"
