"""The defect-hunt checks on stores the campaign driver writes itself.

The driver runs in-process with fake job functions (as in ``test_campaign``)
and one scripted LLM session over the real tool layer, so every cell, record
dir and timing line has the layout a real run produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from campaign_support import FINGERPRINT, TRUTH_IDS, entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign import driver as driver_module
from sciagent.campaign.audit import check_store, compare_stores, gap_check
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, ScoreJob, UnitResult
from sciagent.campaign.plan import ORACLE, LLMArm
from sciagent.campaign.store import ResultStore
from sciagent.glm.fit import FIT_VERSION
from sciagent.harness.scripted import ScriptedDriver

LL = {ORACLE: -0.70, "B-lib": -0.80}


def fake_baselines(job: BaselineJob) -> tuple[UnitResult, ...]:
    out = []
    for s in job.systems:
        detail: dict[str, object] = {"structure": "Base()", "curve": []}
        if s == ORACLE:
            detail |= {"oracle_underfit": False, "oracle_psi_source": "search"}
        reading = {"status": 1.0, "per_event": LL.get(s, -0.75), "exact": 0.0}
        out.append(UnitResult(s, True, reading, detail, 0.0))
    return tuple(out)


def fake_score(job: ScoreJob) -> UnitResult:
    reading = {"status": 1.0, "per_event": -0.72, "exact": 0.0}
    return UnitResult(job.system, True, reading, {"structure": "Base()"}, 0.0)


def run_campaign(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clean(monkeypatch)
    monkeypatch.setattr(driver_module, "run_baseline_job", fake_baselines)
    monkeypatch.setattr(driver_module, "run_score_job", fake_score)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    campaign = Campaign(
        root,
        environment(),
        config,
        [entry(t) for t in TRUTH_IDS],
        DriverSettings(
            workers=0,
            poll_s=0.05,
            llm_limit=1,
            driver_factory=lambda unit: ScriptedDriver(SCRIPT),
        ),
        fingerprint=FINGERPRINT,
    )
    summary = campaign.run()
    campaign.close()
    assert summary.bugs == 0
    return root


def test_the_checks_pass_on_a_store_the_driver_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = run_campaign(tmp_path / "run1", monkeypatch)
    check = check_store(root)
    assert check.issues == () and check.warnings == ()
    with ResultStore(root) as store:
        kinds = sorted({c["key"]["config"]["kind"] for c in store.cells()})
        report = gap_check(store, FIT_VERSION.rsplit(".", 1)[-1])
    assert kinds == ["baseline", "llm-score", "llm-session"]
    assert report.passed
    assert report.digest_issues == ()
    assert {(u.llm, u.truth_digest is not None) for u in report.units} == {
        (False, True),
        (True, True),
    }
    assert report.underfit.checked == len(TRUTH_IDS)
    gaps = {(u.truth, u.system): u.gap for u in report.units}
    assert gaps[("hawkes", "B-lib")] == -0.80 - -0.70
    assert [u.gap for u in report.units if u.llm] == [-0.72 - -0.70]


def test_two_driver_runs_give_byte_identical_cells_of_every_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the scripted model driver, every cell, the session cell included,
    is byte-identical across two runs: the session's wall time and the API's
    figures live in ``timing.jsonl``, which is not compared (LOG 2026-10-27)."""
    a = run_campaign(tmp_path / "a", monkeypatch)
    b = run_campaign(tmp_path / "b", monkeypatch)
    every = compare_stores(a, b)
    assert every.identical, every
    assert every.differing == ()
    assert every.same == check_store(a).cells
    with ResultStore(a) as store:
        kinds = sorted({c["key"]["config"]["kind"] for c in store.cells()})
    assert kinds == ["baseline", "llm-score", "llm-session"]
