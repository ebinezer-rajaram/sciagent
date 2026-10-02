"""Real units through the campaign: every non-LLM system once, byte-identical
reruns, the spawn process pool, and a scripted LLM unit scored for real."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from campaign_support import entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, run_baseline_job
from sciagent.campaign.llm import run_session_isolated
from sciagent.campaign.plan import (
    BASELINES,
    LLMArm,
    Unit,
    baseline_key,
    score_key,
    session_key,
)
from sciagent.campaign.store import cell_bytes
from sciagent.harness.scripted import ScriptedDriver
from sciagent.systems.v2.sparse import SparseConfig

pytestmark = pytest.mark.slow


def test_every_non_llm_system_runs_and_scores_one_real_unit() -> None:
    config = tiny_config(sparse=SparseConfig(n_lambdas=4))
    job = BaselineJob(entry("hawkes"), 1, BASELINES, environment(), config)
    results = {r.system: r for r in run_baseline_job(job)}
    assert set(results) == set(BASELINES)
    for system, r in results.items():
        assert r.ok, (system, r.detail)
        assert math.isfinite(r.reading["per_event"])
        assert 0.0 <= r.reading["similarity"] <= 1.0
    assert results["ORACLE"].reading["exact"] == 1.0
    assert results["B-np"].reading["has_structure"] == 0.0
    assert results["B-np"].detail["curve"] == []
    for system, budget in (("B-rand", 3), ("B-sym", 3), ("B-sym@10F", 6)):
        assert 1 <= results[system].reading["fits_used"] <= budget
        assert results[system].detail["curve"]
    assert results["B-lib"].reading["fits_used"] == 5.0
    assert "sparse" in results["B-sparse"].detail


def test_a_baseline_unit_is_byte_identical_on_rerun() -> None:
    env, config, truth = environment(), tiny_config(), entry("size_excitation")
    systems = ("ORACLE", "B-lib", "B-rand", "planted-hint")
    job = BaselineJob(truth, 2, systems, env, config)
    first, second = run_baseline_job(job), run_baseline_job(job)
    for a, b in zip(first, second, strict=True):
        key = baseline_key(Unit(truth.id, 2, a.system), truth, env, config)
        assert cell_bytes(key, a.reading, a.detail) == cell_bytes(
            key, b.reading, b.detail
        )


def test_the_spawn_pool_runs_and_records_real_units(tmp_path: Path) -> None:
    config = tiny_config(seeds=(1,), baselines=("ORACLE", "B-lib", "B-np", "B-rand"))
    campaign = Campaign(
        tmp_path,
        environment(),
        config,
        [entry("hawkes")],
        DriverSettings(workers=2, poll_s=0.5, run_llm=False),
    )
    summary = campaign.run()
    campaign.close()
    assert summary.recorded == {"B-lib": 1, "B-np": 1, "B-rand": 1, "ORACLE": 1}
    assert summary.bugs == 0 and not summary.failed


def test_a_scripted_llm_unit_is_scored_for_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    campaign = Campaign(
        tmp_path,
        environment(),
        config,
        [entry("size_excitation")],
        DriverSettings(
            workers=0,
            poll_s=0.05,
            run_baselines=False,
            driver_factory=lambda unit: ScriptedDriver(SCRIPT),
        ),
    )
    campaign.run()
    unit = Unit("size_excitation", 1, "AG-c-H/named")
    skey = session_key(unit, entry("size_excitation"), environment(), config)
    cell = campaign.store.cell(score_key(unit, skey, environment(), config).digest)
    campaign.close()
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    assert reading["status"] == 1.0 and reading["exact"] == 1.0
    assert math.isfinite(reading["per_event"])
    curve = cell["detail"]["result"]["curve"]
    assert [k for k, _, _ in curve] == [1, 2]


def test_an_isolated_session_past_its_hard_deadline_is_killed_and_voided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    config = tiny_config(seeds=(1,), wall_time_s=0.0)
    unit = Unit("hawkes", 1, "AG-c-H/named")
    out = run_session_isolated(
        unit, entry("hawkes"), environment(), config, tmp_path, grace_s=0.5
    )
    assert not out.retryable
    assert out.detail["outcome"] == "hard_timeout"
    assert out.reading["void"] == 1.0 and out.reading["status"] == 0.0
