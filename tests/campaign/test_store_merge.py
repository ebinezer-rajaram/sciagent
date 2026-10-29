"""Merging stores written in parallel (``sciagent.campaign.merge``).

The pilot's non-LLM and LLM halves can run as two drivers on two stores from
one commit; the readers take one root. The merge's contract: the new store
holds exactly the cells one driver running both halves would have written,
byte for byte, or nothing is written at all.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from audit_support import clean_store, put_baseline, put_score, put_session
from campaign_support import FINGERPRINT, entry, environment, tiny_config

from sciagent.campaign import driver as driver_module
from sciagent.campaign.audit import check_store, compare_stores
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, ScoreJob, UnitResult
from sciagent.campaign.fingerprint import (
    FingerprintEntry,
    guard_store,
    read_fingerprints,
)
from sciagent.campaign.merge import merge_stores
from sciagent.campaign.plan import CampaignError, LLMArm
from sciagent.campaign.store import (
    DETAILS_DIR,
    TIMING_FILE,
    Json,
    ResultStore,
    cell_bytes,
    key_from_json,
)
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.store import ExperimentKey
from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "store_merge.py"
SCRIPT = (
    Step(
        calls=(("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]}),)
    ),
    Step(calls=(("submit", {"structure": "Excite(ExpK, One, all)", "report": "x"}),)),
    Step(text="submitted"),
)


def _fake_baselines(job: BaselineJob) -> tuple[UnitResult, ...]:
    return tuple(
        UnitResult(s, True, {"status": 1.0, "per_event": -0.7}, {"s": s}, 0.0)
        for s in job.systems
    )


def _fake_score(job: ScoreJob) -> UnitResult:
    return UnitResult(job.system, True, {"status": 1.0, "per_event": -0.71}, {}, 0.0)


def _run(root: Path, *, baselines: bool, llm: bool) -> None:
    campaign = Campaign(
        root,
        environment(),
        tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),)),
        [entry("hawkes")],
        DriverSettings(
            workers=0,
            poll_s=0.05,
            run_baselines=baselines,
            run_llm=llm,
            driver_factory=lambda unit: ScriptedDriver(SCRIPT),
        ),
        fingerprint=FINGERPRINT,
    )
    try:
        campaign.run()
    finally:
        campaign.close()


def test_two_halves_merged_hold_the_cells_of_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control of the parallel pilot: baselines in one store, the
    LLM arm in another, merged, equal cell for cell (bytes and ledger
    readings) the store of one driver that ran both."""
    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(driver_module, "run_baseline_job", _fake_baselines)
    monkeypatch.setattr(driver_module, "run_score_job", _fake_score)
    _run(tmp_path / "one", baselines=True, llm=True)
    _run(tmp_path / "base", baselines=True, llm=False)
    _run(tmp_path / "llm", baselines=False, llm=True)
    summary = merge_stores((tmp_path / "base", tmp_path / "llm"), tmp_path / "merged")

    diff = compare_stores(tmp_path / "one", tmp_path / "merged")
    assert diff.identical and diff.same > 0, diff
    assert summary.fingerprint == FINGERPRINT
    assert summary.cells == (diff.same - 2, 2)  # the session and its score
    assert check_store(tmp_path / "merged").passed
    assert read_fingerprints(tmp_path / "merged") == (
        FingerprintEntry("establish", FINGERPRINT, None),
    )
    with ResultStore(tmp_path / "llm", read_only=True) as store:
        (session,) = (
            c for c in store.cells() if c["key"]["config"]["kind"] == "llm-session"
        )
    record = (
        Path(session["detail"]["session"]["record_dir"])
        / session["detail"]["session"]["record"]
    )
    assert (tmp_path / "merged" / record).read_bytes() == (
        tmp_path / "llm" / record
    ).read_bytes()
    timing = [
        (tmp_path / s / TIMING_FILE).read_text(encoding="utf-8")
        for s in ("base", "llm")
    ]
    assert (tmp_path / "merged" / TIMING_FILE).read_text(encoding="utf-8") == "".join(
        timing
    )
    assert summary.record_dirs == 1 and summary.timing_lines == len(
        "".join(timing).splitlines()
    )


def test_the_merge_script_reports_and_refuses(tmp_path: Path) -> None:
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        put_baseline(b, "B-lib", -0.8)
    spec = importlib.util.spec_from_file_location("store_merge", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = [str(tmp_path / "a"), str(tmp_path / "b"), "--out", str(tmp_path / "m")]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert module.main(args) == 0
    assert f"merged 2 stores (1 + 1 cells) under {FINGERPRINT}" in out.getvalue()
    assert check_store(tmp_path / "m").passed
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert module.main(args) == 2
    assert "exists" in err.getvalue()


def _store(root: Path, fingerprint: str | None = FINGERPRINT) -> ResultStore:
    if fingerprint is not None:
        guard_store(root, fingerprint)
    return ResultStore(root)


def test_a_merge_is_byte_deterministic_and_a_snapshot_of_committed_cells(
    tmp_path: Path,
) -> None:
    """A record dir no committed cell names (a session still running) and a
    timing line without its newline (being written) are not copied; two merges
    of the same sources give the same bytes."""
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        session = put_session(b, "AG-o-S/named")
        put_score(b, "AG-o-S/named", str(session.digest), -0.72)
        b.log_timing({"digest": str(session.digest), "wall_s": 1.5})
    (tmp_path / "b" / "llm" / "inflight").mkdir()
    with (tmp_path / "b" / TIMING_FILE).open("a", encoding="utf-8") as f:
        f.write('{"digest": "partial"')
    for out in ("m1", "m2"):
        merge_stores((tmp_path / "a", tmp_path / "b"), tmp_path / out)
    assert not (tmp_path / "m1" / "llm" / "inflight").exists()
    lines = (tmp_path / "m1" / TIMING_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["wall_s"] for x in lines] == [1.5]
    for name in sorted(p.name for p in (tmp_path / "m1" / DETAILS_DIR).iterdir()):
        assert (tmp_path / "m1" / DETAILS_DIR / name).read_bytes() == (
            tmp_path / "m2" / DETAILS_DIR / name
        ).read_bytes()
    with (
        ResultStore(tmp_path / "m1", read_only=True) as m1,
        ResultStore(tmp_path / "m2", read_only=True) as m2,
    ):
        assert m1.ledger.entries() == m2.ledger.entries()
        assert len(m1.ledger.entries()) == 3


def _refused(sources: tuple[Path, ...], dest: Path, match: str) -> None:
    with pytest.raises(CampaignError, match=match):
        merge_stores(sources, dest)
    assert not dest.exists()


def test_a_merge_refuses_sources_of_different_code(tmp_path: Path) -> None:
    guard_store(tmp_path / "a", FINGERPRINT)
    clean_store(tmp_path / "a")
    with _store(tmp_path / "b", "commit=other,tree=clean") as b:
        put_baseline(b, "ORACLE", -0.7, truth="dev-009")
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "fingerprint")


def test_a_merge_refuses_a_source_without_a_single_established_fingerprint(
    tmp_path: Path,
) -> None:
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    clean_store(tmp_path / "b")  # cells, no sidecar
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "fingerprint")
    guard_store(tmp_path / "b", FINGERPRINT, reuse_from="unrecorded")
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "fingerprint")


def test_a_merge_refuses_a_cell_in_two_sources(tmp_path: Path) -> None:
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        put_baseline(b, "ORACLE", -0.7)
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "in two sources")


def test_a_merge_refuses_two_records_of_one_truth(tmp_path: Path) -> None:
    """Truth ids repeat across splits (LOG 2026-10-27): one id, one digest."""
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        put_baseline(b, "B-lib", -0.8, truth_digest="0" * 64)
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "truth dev-008")


def test_a_merge_refuses_a_noncanonical_detail(tmp_path: Path) -> None:
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        key = put_baseline(b, "B-lib", -0.8)
    path = tmp_path / "b" / DETAILS_DIR / f"{key.digest}.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b" \n"))
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "canonical")


def test_a_merge_refuses_an_existing_destination_or_one_source(tmp_path: Path) -> None:
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        put_baseline(b, "B-lib", -0.8)
    (tmp_path / "m").mkdir()
    with pytest.raises(CampaignError, match="exists"):
        merge_stores((tmp_path / "a", tmp_path / "b"), tmp_path / "m")
    assert not any((tmp_path / "m").iterdir())
    _refused((tmp_path / "a",), tmp_path / "n", "two or more")
    _refused((tmp_path / "a", tmp_path / "a"), tmp_path / "n", "two or more")


def test_a_merge_killed_partway_leaves_no_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is built beside ``dest`` and renamed into place only when
    complete: a failure during the copy leaves no ``dest`` (which would pass
    ``check_store`` holding some of the cells) and no build dir, so a rerun
    goes ahead."""
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
        put_baseline(a, "B-lib", -0.8)
    with _store(tmp_path / "b") as b:
        put_baseline(b, "B-nonpar", -0.9)
    put = ResultStore.put
    calls: list[str] = []

    def failing_put(
        store: ResultStore, key: ExperimentKey, reading: dict[str, float], detail: Json
    ) -> LedgerEntry:
        calls.append(str(key.digest))
        if len(calls) == 2:
            raise OSError("killed partway")
        return put(store, key, reading, detail)

    monkeypatch.setattr(ResultStore, "put", failing_put)
    sources = (tmp_path / "a", tmp_path / "b")
    with pytest.raises(OSError, match="killed partway"):
        merge_stores(sources, tmp_path / "m")
    assert not (tmp_path / "m").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b"]
    monkeypatch.undo()
    assert merge_stores(sources, tmp_path / "m").cells == (2, 1)
    assert check_store(tmp_path / "m").passed


def test_a_merge_refuses_a_detail_whose_reading_is_not_the_ledgers(
    tmp_path: Path,
) -> None:
    """A detail rewritten canonically with another reading fails ``check_store``
    on its source; the merge rebuilds the ledger from the details, so it must
    refuse rather than launder it into a store that passes."""
    with _store(tmp_path / "a") as a:
        put_baseline(a, "ORACLE", -0.7)
    with _store(tmp_path / "b") as b:
        key = put_baseline(b, "B-lib", -0.8)
        cell = b.cell(key.digest)
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    reading["per_event"] = -0.5
    path = tmp_path / "b" / DETAILS_DIR / f"{key.digest}.json"
    path.write_bytes(cell_bytes(key_from_json(cell["key"]), reading, cell["detail"]))
    assert not check_store(tmp_path / "b").passed
    _refused((tmp_path / "a", tmp_path / "b"), tmp_path / "m", "reading")


def test_a_merge_copies_only_the_timing_lines_of_copied_cells(
    tmp_path: Path,
) -> None:
    """The ledger is read before ``timing.jsonl``: a cell committed in between
    has its timing line but not its cell in the snapshot. That line is not
    copied (``pilot_report`` counts every line)."""
    with _store(tmp_path / "a") as a:
        key = put_baseline(a, "ORACLE", -0.7)
        a.log_timing({"digest": str(key.digest), "wall_s": 1.5})
        a.log_timing({"digest": "f" * 64, "wall_s": 9.0})
    with _store(tmp_path / "b") as b:
        put_baseline(b, "B-lib", -0.8)
    summary = merge_stores((tmp_path / "a", tmp_path / "b"), tmp_path / "m")
    lines = (tmp_path / "m" / TIMING_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["digest"] for x in lines] == [str(key.digest)]
    assert summary.timing_lines == 1
    assert check_store(tmp_path / "m").warnings == ()
