"""``scripts/mem_sampler.py``: peak memory of a pilot's processes on Linux.

Runs against a fake ``/proc`` tree in a tmp dir, plus one sample of this
process's real ``/proc`` entry."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mem_sampler", SCRIPTS / "mem_sampler.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


def fake_proc(
    root: Path, pid: int, ppid: int, rss_kb: int, hwm_kb: int, cmd: list[str]
) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "status").write_text(
        f"Name:\tpython3\nPPid:\t{ppid}\nVmHWM:\t{hwm_kb} kB\nVmRSS:\t{rss_kb} kB\n"
    )
    (d / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmd) + b"\0")


def tree(root: Path) -> None:
    (root / "meminfo").write_text(
        "MemTotal:       16000000 kB\nMemAvailable:    1500000 kB\n"
    )
    fake_proc(root, 100, 1, 300_000, 400_000, ["python", "scripts/pilot.py"])
    worker = [
        "python",
        "-c",
        "from multiprocessing.spawn import spawn_main; spawn_main()",
    ]
    fake_proc(root, 101, 100, 900_000, 1_700_000, worker)
    fake_proc(root, 102, 100, 500_000, 600_000, worker)
    fake_proc(root, 103, 102, 10_000, 20_000, ["docker", "run"])
    fake_proc(root, 200, 1, 5_000_000, 5_000_000, ["unrelated"])
    (root / "self").mkdir()  # non-numeric entries are skipped


def test_sample_covers_descendants_only_and_marks_pool_workers(tmp_path: Path) -> None:
    m = load()
    tree(tmp_path)
    rows = m.sample(100, proc=tmp_path)
    assert [r.pid for r in rows] == [100, 101, 102, 103]
    kinds = {r.pid: r.kind for r in rows}
    assert kinds == {100: "root", 101: "worker", 102: "worker", 103: "other"}
    w = next(r for r in rows if r.pid == 101)
    assert (w.ppid, w.rss_kb, w.hwm_kb) == (100, 900_000, 1_700_000)


def test_mem_available(tmp_path: Path) -> None:
    m = load()
    tree(tmp_path)
    assert m.mem_available_kb(tmp_path) == 1_500_000


def test_summary_reports_peak_worker_times_workers(tmp_path: Path) -> None:
    m = load()
    tree(tmp_path)
    out = tmp_path / "mem.csv"
    m.append_rows(out, "2026-10-27T00:00:00", 1_500_000, m.sample(100, proc=tmp_path))
    s = m.summarise(out, workers=2)
    assert s.peak_worker_kb == 1_700_000
    assert s.peak_worker_pid == 101
    assert s.worker_bound_kb == 3_400_000
    assert s.min_available_kb == 1_500_000


def test_brake_writes_stop_once_below_threshold(tmp_path: Path) -> None:
    m = load()
    store = tmp_path / "store"
    store.mkdir()
    assert not m.brake(store, available_kb=3_000_000, brake_kb=2_000_000)
    assert not (store / "STOP").exists()
    assert m.brake(store, available_kb=1_000_000, brake_kb=2_000_000)
    assert (store / "STOP").exists()


def test_a_vanished_process_is_skipped(tmp_path: Path) -> None:
    m = load()
    tree(tmp_path)
    (tmp_path / "102" / "status").unlink()
    assert [r.pid for r in m.sample(100, proc=tmp_path)] == [100, 101]


def test_real_proc_sample_of_this_process() -> None:
    m = load()
    rows = m.sample(os.getpid())
    assert rows[0].pid == os.getpid()
    assert rows[0].kind == "root"
    assert rows[0].hwm_kb >= rows[0].rss_kb > 0
