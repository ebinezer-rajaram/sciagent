"""Sample the memory of a pilot run and its descendants on Linux.

    nice -n 10 uv run python scripts/mem_sampler.py --pid <pilot pid> \
        --out .cache/minipilot-mem.csv --store .cache/minipilot
    uv run python scripts/mem_sampler.py --summary .cache/minipilot-mem.csv --workers 2

The Linux replacement for the Windows ``memsampler.ps1`` of the 2026-10
mini-pilot (``origin/windows-salvage``). Every ``--interval`` seconds it walks
``/proc`` for the process tree under ``--pid`` and appends one CSV row per
process: its ``VmRSS`` and ``VmHWM`` (the kernel's own peak RSS, so a peak
between two samples is not missed while the process lives), and whether it is
a pool worker (a ``multiprocessing`` spawn child). It stops when ``--pid`` is
gone. When ``MemAvailable`` falls below ``--brake-gb`` it writes the store's
``STOP`` file, as the Windows sampler did on low commit headroom.

``--summary`` prints the mini-pilot gate's memory figure: the peak ``VmHWM``
of any pool worker times the worker count (HANDOFF-2026-10-06; at most 6 GB
on dm-devbox per the queue), and the lowest ``MemAvailable`` seen.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PROC = Path("/proc")
FIELDS = ["time", "available_kb", "pid", "ppid", "kind", "rss_kb", "hwm_kb", "cmd"]
WORKER_MARK = "multiprocessing.spawn"


@dataclass(frozen=True)
class Row:
    pid: int
    ppid: int
    kind: str  # "root", "worker" or "other"
    rss_kb: int
    hwm_kb: int
    cmd: str


@dataclass(frozen=True)
class Summary:
    peak_worker_kb: int
    peak_worker_pid: int
    worker_bound_kb: int
    min_available_kb: int


def _read(pid: int, proc: Path) -> tuple[int, int, int, str] | None:
    """``(ppid, rss_kb, hwm_kb, cmd)`` of ``pid``, or None if it has gone."""
    try:
        status = (proc / str(pid) / "status").read_text()
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    fields: dict[str, str] = {}
    for line in status.splitlines():
        key, _, value = line.partition(":")
        fields[key] = value.strip()
    if "PPid" not in fields:
        return None
    # Kernel threads and zombies have no Vm* lines.
    rss = int(fields.get("VmRSS", "0 kB").split()[0])
    hwm = int(fields.get("VmHWM", "0 kB").split()[0])
    cmd = " ".join(part.decode(errors="replace") for part in raw.split(b"\0") if part)
    return int(fields["PPid"]), rss, hwm, cmd


def sample(root: int, proc: Path = PROC) -> list[Row]:
    """``root`` and its descendants, in ascending pid order."""
    found: dict[int, tuple[int, int, int, str]] = {}
    for entry in proc.iterdir():
        if entry.name.isdigit():
            info = _read(int(entry.name), proc)
            if info is not None:
                found[int(entry.name)] = info
    if root not in found:
        return []
    keep = {root}
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        for pid, info in found.items():
            if info[0] == parent and pid not in keep:
                keep.add(pid)
                frontier.append(pid)
    rows = []
    for pid in sorted(keep):
        ppid, rss, hwm, cmd = found[pid]
        kind = "root" if pid == root else "worker" if WORKER_MARK in cmd else "other"
        rows.append(Row(pid, ppid, kind, rss, hwm, cmd))
    return rows


def mem_available_kb(proc: Path = PROC) -> int:
    for line in (proc / "meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    raise ValueError(f"no MemAvailable line in {proc / 'meminfo'}")


def append_rows(out: Path, stamp: str, available_kb: int, rows: list[Row]) -> None:
    new = not out.exists()
    with out.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(FIELDS)
        for r in rows:
            writer.writerow(
                [
                    stamp,
                    available_kb,
                    r.pid,
                    r.ppid,
                    r.kind,
                    r.rss_kb,
                    r.hwm_kb,
                    r.cmd[:160],
                ]
            )


def brake(store: Path, available_kb: int, brake_kb: int) -> bool:
    """Write ``store/STOP`` if memory is below the brake; True if it did."""
    if available_kb >= brake_kb:
        return False
    (store / "STOP").touch()
    return True


def summarise(path: Path, workers: int) -> Summary:
    peak, peak_pid, min_available = 0, -1, -1
    with path.open(newline="") as fh:
        for rec in csv.DictReader(fh):
            available = int(rec["available_kb"])
            if min_available < 0 or available < min_available:
                min_available = available
            if rec["kind"] == "worker" and int(rec["hwm_kb"]) > peak:
                peak, peak_pid = int(rec["hwm_kb"]), int(rec["pid"])
    return Summary(peak, peak_pid, peak * workers, min_available)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pid", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--store", type=Path, help="pilot store; gets STOP on low memory"
    )
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--brake-gb", type=float, default=2.0)
    parser.add_argument("--summary", type=Path, help="summarise a sample CSV and exit")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    if args.summary is not None:
        s = summarise(args.summary, args.workers)
        gb = 1024 * 1024
        peak = s.peak_worker_kb / gb
        print(f"peak worker VmHWM {peak:.3f} GB (pid {s.peak_worker_pid})")
        print(f"x {args.workers} workers = {s.worker_bound_kb / gb:.3f} GB")
        print(f"lowest MemAvailable {s.min_available_kb / gb:.3f} GB")
        return 0
    if args.pid is None or args.out is None:
        parser.error("--pid and --out are required unless --summary is given")
    brake_kb = int(args.brake_gb * 1024 * 1024)
    while True:
        rows = sample(args.pid)
        if not rows:
            return 0
        available = mem_available_kb()
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        append_rows(args.out, stamp, available, rows)
        if args.store is not None and brake(args.store, available, brake_kb):
            print(f"{stamp} MemAvailable {available} kB: wrote STOP", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
