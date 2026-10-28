"""The defect-hunt scripts end to end, each on a tiny store in a tmp dir.

The three store scripts run in-process through their ``main`` (a fresh
interpreter costs ~5 s here); the report check spawns ``pilot_report.py`` per
hash seed by design, so it is slow."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from audit_support import CALLS, clean_store, put_baseline, put_session

from sciagent.campaign.store import DETAILS_DIR, ResultStore

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SPLIT = Path(__file__).resolve().parents[2] / "data" / "v2" / "truths" / "dev"
#: sha256 of the adopted (Linux) dev split's ``truths.json`` (LOG 2026-10-27).
DEV_SPLIT_SHA256 = "604acec5359d9a33b8aedc500de8dff14600041225c386766ec3544b2e8d4420"


def load(script: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(script[:-3], SCRIPTS / script)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(script: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
    """``script``'s ``main`` in this process, as a completed process."""
    module = load(script)
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


def test_hunt_gap_refuses_to_run_without_the_split(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The beats-truth half of the gate needs the truths: without ``--split``
    the gate is not run at all, rather than passed without it."""
    root = clean_store(tmp_path / "s")
    main = load("hunt_gap.py").main
    with pytest.raises(SystemExit) as stopped:
        main(["--store", str(root), "--env", "fit/5"])
    assert stopped.value.code == 2
    assert "--split" in capsys.readouterr().err


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


def test_store_check_audits_session_budgets(tmp_path: Path) -> None:
    root = clean_store(tmp_path / "s")
    ok = run("store_check.py", "--store", root)
    assert "sessions audited: 1; budget issues: 0" in ok.stdout
    calls = list(CALLS)
    calls[2] = {**calls[2], "charged": "fits"}  # a refusal, charged
    with ResultStore(root) as store:
        session = put_session(store, "AG-c-S/named", calls=tuple(calls))
    bad = run("store_check.py", "--store", root)
    assert bad.returncode == 1
    assert f"ISSUE budget_charge {session.digest}: call 2 fit" in bad.stdout
    assert bad.stdout.rstrip().endswith("STORE INCONSISTENT")


def test_store_check_reports_the_code_fingerprint_history(tmp_path: Path) -> None:
    from sciagent.campaign.fingerprint import FINGERPRINT_FILE, UNRECORDED, guard_store

    root = clean_store(tmp_path / "s")  # cells written without a sidecar
    legacy = run("store_check.py", "--store", root)
    assert legacy.returncode == 0, legacy.stdout
    assert "code fingerprint: none recorded" in legacy.stdout
    guard_store(root, "fp-a", reuse_from=UNRECORDED)
    guard_store(root, "fp-b", reuse_from="fp-a")
    ok = run("store_check.py", "--store", root)
    assert ok.returncode == 0, ok.stdout
    assert "code fingerprint 1: reuse fp-a from unrecorded" in ok.stdout
    assert "code fingerprint 2: reuse fp-b from fp-a" in ok.stdout
    with (root / FINGERPRINT_FILE).open("a", encoding="utf-8") as f:
        f.write('{"event": "establish", "fingerprint": "fp-c"}\n')
    bad = run("store_check.py", "--store", root)
    assert bad.returncode == 1
    assert "ISSUE fingerprint" in bad.stdout
    assert bad.stdout.rstrip().endswith("STORE INCONSISTENT")


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


def _hunt_gap(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``hunt_gap`` with ``scripts/`` importable (its reference imports
    ``pilot``)."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return load("hunt_gap.py")


def _split_records() -> dict[str, Any]:
    """The dev split's records by id, after checking it is the Linux split."""
    from sciagent.scenarios.records import read_split

    digest = hashlib.sha256((SPLIT / "truths.json").read_bytes()).hexdigest()
    assert digest == DEV_SPLIT_SHA256, f"{SPLIT} is not the adopted dev split"
    records, _ = read_split(SPLIT)
    return {r.id: r for r in records}


def _record_digest(record: Any) -> str:
    from sciagent.scenarios.records import canonical_json

    return hashlib.sha256(canonical_json(record.to_json()).encode()).hexdigest()


@pytest.mark.slow
def test_hunt_gap_checks_the_gap_the_truth_and_the_truth_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planted numbers around the real dev-008 reference: a clean store
    passes; a gap above ORACLE fails; units within +0.01 of ORACLE but above
    the truth's own parameters fail (p3_gap's "BEATS-TRUTH"); cells whose
    ``truth_digest`` is not the split's record fail by name, even below it."""
    hunt_gap = _hunt_gap(monkeypatch)
    digest = _record_digest(_split_records()["dev-008"])
    reference = hunt_gap.truth_reference([("dev-008", 1)], SPLIT)
    ref = reference[("dev-008", 1)]
    assert set(reference) == {("dev-008", 1)} and -10.0 < ref.per_event < 10.0
    assert ref.digest == digest
    args = ("--env", "fit/5", "--split", SPLIT)

    clean = tmp_path / "clean"
    with ResultStore(clean) as store:
        put_baseline(store, "ORACLE", ref.per_event - 0.1, truth_digest=digest)
        put_baseline(store, "B-lib", ref.per_event - 0.2, truth_digest=digest)
    ok = run("hunt_gap.py", "--store", clean, *args)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "B-lib: LL" in ok.stdout and "gap -0.1000" in ok.stdout
    assert f"truths.json sha256 {DEV_SPLIT_SHA256}" in ok.stdout
    assert "not the split's truth record: 0" in ok.stdout
    assert ok.stdout.rstrip().endswith("GATE PASS")
    with ResultStore(clean) as store:
        put_baseline(store, "B-sparse", ref.per_event - 0.08, truth_digest=digest)
    gap = run("hunt_gap.py", "--store", clean, *args)
    assert gap.returncode == 1
    assert "gap above +0.01: 1" in gap.stdout
    assert gap.stdout.rstrip().endswith("GATE FAIL")

    beats = tmp_path / "beats"
    with ResultStore(beats) as store:
        put_baseline(store, "ORACLE", ref.per_event + 0.015, truth_digest=digest)
        put_baseline(store, "B-lib", ref.per_event + 0.014, truth_digest=digest)
    flagged = run("hunt_gap.py", "--store", beats, *args)
    assert flagged.returncode == 1, flagged.stdout + flagged.stderr
    assert "gap above +0.01: 0" in flagged.stdout
    assert "above the truth's own parameters by > +0.01: 2" in flagged.stdout
    assert flagged.stdout.rstrip().endswith("GATE FAIL")

    elsewhere = tmp_path / "elsewhere"  # same id, another split's record
    with ResultStore(elsewhere) as store:
        put_baseline(store, "ORACLE", ref.per_event - 0.1)
        put_baseline(store, "B-lib", ref.per_event - 0.2)
    other = run("hunt_gap.py", "--store", elsewhere, *args)
    assert other.returncode == 1, other.stdout + other.stderr
    assert "not the split's truth record: 2" in other.stdout
    assert "above the truth's own parameters by > +0.01: 0" in other.stdout
    for system in ("B-lib", "ORACLE"):
        assert f"  dev-008 s1 {system}: " in other.stdout
    assert other.stdout.rstrip().endswith("GATE FAIL")


@pytest.mark.slow
def test_an_honest_oracle_fit_stays_below_its_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the beats-truth check on real data (SPEC §6.4):
    ORACLE fitted through the pilot's own configuration on dev-006 (k = 3,
    the cheapest dev truth) at seed 1, stored at the driver's address, is
    below the truth's own parameters on the held-out log, and the whole gate
    passes on it. The planted test above shows the failing side."""
    from sciagent.campaign.execute import BaselineJob, run_baseline_job
    from sciagent.campaign.plan import Unit, baseline_key, system_config_text

    hunt_gap = _hunt_gap(monkeypatch)
    pilot = importlib.import_module("pilot")
    _split_records()
    records = pilot.load_split(SPLIT)
    env = pilot.pointproc_environment(records)
    argv = ["--ids", "dev-006", "--seeds", "1", "--systems", "ORACLE", "--no-llm"]
    config = pilot.campaign_config(pilot.build_parser().parse_args(argv))
    (truth,) = [t for t in pilot.truth_entries(records) if t.id == "dev-006"]
    (result,) = run_baseline_job(BaselineJob(truth, 1, ("ORACLE",), env, config))
    assert result.ok, result.detail
    root = tmp_path / "s"
    with ResultStore(root) as store:
        store.put(
            baseline_key(Unit("dev-006", 1, "ORACLE"), truth, env, config),
            result.reading,
            {
                "unit": {
                    "truth": "dev-006",
                    "seed": 1,
                    "system": "ORACLE",
                    "kind": "baseline",
                },
                "result": result.detail,
                "system_config": system_config_text("ORACLE", env, config),
            },
        )
    ref = hunt_gap.truth_reference([("dev-006", 1)], SPLIT)[("dev-006", 1)]
    over = result.reading["per_event"] - ref.per_event
    assert ref.digest == truth.digest
    assert -0.01 < over < 0.0, over
    done = run("hunt_gap.py", "--store", root, "--env", "fit/5", "--split", SPLIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "above the truth's own parameters by > +0.01: 0" in done.stdout
    assert "not the split's truth record: 0" in done.stdout
    assert done.stdout.rstrip().endswith("GATE PASS")


@pytest.mark.slow
def test_session_check_names_a_stored_session_the_rescore_would_miss(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "e"
    with ResultStore(empty) as store:
        put_baseline(store, "ORACLE", -0.7)
    args = ("--ids", "dev-008", "--seeds", "1", "--arms", "AG-o/named")
    ok = spawn("session_check.py", "--root", empty, *args)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "sessions: 0; found: 0" in ok.stdout
    root = clean_store(tmp_path / "s")  # its session has a made-up config
    bad = spawn("session_check.py", "--root", root, *args)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "ISSUE not_found" in bad.stdout
    assert bad.stdout.rstrip().endswith("SESSIONS INCOMPLETE")
