"""The defect-hunt scripts end to end, each on a tiny store in a tmp dir.

The three store scripts run in-process through their ``main`` (a fresh
interpreter costs ~5 s here); the report check spawns ``pilot_report.py`` per
hash seed by design, so it is slow."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from audit_support import clean_store, put_baseline

from sciagent.campaign.store import DETAILS_DIR, ResultStore

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def run(script: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
    """``script``'s ``main`` in this process, as a completed process."""
    spec = importlib.util.spec_from_file_location(script[:-3], SCRIPTS / script)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main([str(a) for a in args])
    return subprocess.CompletedProcess(script, code, out.getvalue(), err.getvalue())


def spawn(script: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_hunt_gap_passes_a_clean_store_and_fails_a_gap_above(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    ok = run("hunt_gap.py", "--store", root, "--env", "fit/5")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "dev-008 s1 B-lib: LL -0.8000 ORACLE -0.7000 gap -0.1000" in ok.stdout
    assert ok.stdout.rstrip().endswith("GATE PASS")
    with ResultStore(root) as store:
        put_baseline(store, "B-sparse", -0.68)
    bad = run("hunt_gap.py", "--store", root, "--env", "fit/5")
    assert bad.returncode == 1
    assert "gap above +0.01: 1" in bad.stdout
    assert bad.stdout.rstrip().endswith("GATE FAIL")


def test_store_check_passes_then_finds_a_rewritten_detail(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    ok = run("store_check.py", "--store", root)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "cells: 8; issues: 0; warnings: 0" in ok.stdout
    path = sorted((root / DETAILS_DIR).glob("*.json"))[0]
    path.write_bytes(path.read_bytes().replace(b"\n", b" \n"))
    bad = run("store_check.py", "--store", root)
    assert bad.returncode == 1
    assert f"ISSUE noncanonical {path.stem}" in bad.stdout


def test_store_check_refuses_a_path_that_is_not_a_store(tmp_path: Path) -> None:
    done = run("store_check.py", "--store", tmp_path / "nope")
    assert done.returncode == 2
    assert not (tmp_path / "nope").exists()


def test_store_compare_reports_identity_and_difference(tmp_path: Path) -> None:
    a = clean_store(tmp_path / "a")
    b = clean_store(tmp_path / "b")
    c = clean_store(tmp_path / "c", blib=-0.81)
    same = run("store_compare.py", a, b)
    assert same.returncode == 0, same.stdout + same.stderr
    assert same.stdout.rstrip().endswith("IDENTICAL")
    differ = run("store_compare.py", a, c)
    assert differ.returncode == 1
    assert "differing: 2" in differ.stdout


@pytest.mark.slow
def test_report_determinism_renders_pilot_report_identically(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    done = spawn(
        "report_determinism.py",
        "--root",
        root,
        "--hash-seeds",
        "0,1",
        "--",
        "--resamples",
        "50",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.rstrip().endswith("REPORT IDENTICAL")
