"""Real units through the campaign: every non-LLM system once, byte-identical
reruns, the spawn process pool, and a scripted LLM unit scored for real."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest
from campaign_support import FINGERPRINT, entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign import llm
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, run_baseline_job
from sciagent.campaign.llm import run_session_isolated
from sciagent.campaign.plan import (
    BASELINES,
    CampaignConfig,
    CampaignEnvironment,
    LLMArm,
    TruthEntry,
    Unit,
    baseline_key,
    score_key,
    session_key,
)
from sciagent.campaign.store import TIMING_FILE, cell_bytes
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
        fingerprint=FINGERPRINT,
    )
    summary = campaign.run()
    campaign.close()
    assert summary.recorded == {"B-lib": 1, "B-np": 1, "B-rand": 1, "ORACLE": 1}
    assert summary.bugs == 0 and not summary.failed
    # CPU time is a delta measured in the worker over each unit and each job:
    # an absolute figure would include the worker's start-up and imports (and,
    # for the fast group's later units, the earlier ones), exceeding the
    # interval's wall time; with one BLAS thread per worker, CPU cannot
    # outrun wall by more than noise.
    text = (tmp_path / TIMING_FILE).read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == 4
    for x in lines:
        assert 0.0 < x["cpu_s"] <= x["wall_s"] * 1.05 + 0.2, x
        assert 0.0 < x["job_cpu_s"] <= x["job_wall_s"] * 1.05 + 0.2, x
        assert x["cpu_children_s"] == 0.0 and x["job_cpu_children_s"] == 0.0, x
    assert sorted(x["job_units"] for x in lines) == [1, 3, 3, 3]


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
        fingerprint=FINGERPRINT,
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
    # Its time is the driver's clock, in the timing line, never the cell; the
    # harness's own time does not exist and says so (LOG 2026-10-27).
    assert "wall_s" not in out.reading
    assert out.timing == {
        "harness_wall_s": None,
        "duration_api_ms": None,
        "total_cost_usd": None,
    }
    assert out.wall_s >= 0.5


def _starts_a_client_and_hangs(
    conn: Connection,
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    sandbox_root: Path | None,
) -> None:
    """Stands in for a session stuck in a ``python`` call: a Docker client
    (here ``sleep``) running under the session's process."""
    client = subprocess.Popen(["sleep", "600"])
    (out_dir / "client.pid").write_text(str(client.pid), encoding="utf-8")
    time.sleep(600)


def _running(pid: int) -> bool:
    """Whether ``pid`` is a live process (a zombie is not)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def test_a_killed_session_takes_the_processes_it_started_with_it(
    tmp_path: Path,
) -> None:
    """A session killed at its hard deadline must not leave its Docker
    clients running: killing only the session's process orphans them."""
    if sys.platform != "linux":
        pytest.fail("this check reads /proc")
    config = tiny_config(seeds=(1,), wall_time_s=0.0)
    unit = Unit("hawkes", 1, "AG-c-H/named")
    out = llm._run_isolated(
        unit,
        entry("hawkes"),
        environment(),
        config,
        tmp_path,
        None,
        30.0,
        target=_starts_a_client_and_hangs,
    )
    assert out.detail["outcome"] == "hard_timeout"
    pid = int((tmp_path / "client.pid").read_text(encoding="utf-8"))
    try:
        deadline = time.monotonic() + 10.0
        while _running(pid):
            assert time.monotonic() < deadline, "the session's client outlived it"
            time.sleep(0.1)
    finally:
        if _running(pid):
            os.kill(pid, signal.SIGKILL)


#: What :func:`_answers_at_once` sends as the session's harness timing.
_TIMING = {"harness_wall_s": 0.25, "duration_api_ms": 7, "total_cost_usd": 0.5}


def _answers_at_once(
    conn: Connection,
    unit: Unit,
    truth: TruthEntry,
    env: CampaignEnvironment,
    config: CampaignConfig,
    out_dir: Path,
    sandbox_root: Path | None,
) -> None:
    """Stands in for a session that ends at once, claiming no time at all."""
    reading = {"status": 1.0, "void": 0.0}
    conn.send(
        llm.SessionOutcome(
            unit, False, reading, {"outcome": "submitted"}, None, (), 0.0, _TIMING
        )
    )
    conn.close()


def test_an_isolated_session_is_timed_by_the_driver_with_its_spawn(
    tmp_path: Path,
) -> None:
    """The unit's wall time is the driver's clock however the session ended
    (as for a hard timeout); the harness's times pass through untouched."""
    out = llm._run_isolated(
        Unit("hawkes", 1, "AG-c-H/named"),
        entry("hawkes"),
        environment(),
        tiny_config(seeds=(1,)),
        tmp_path,
        None,
        60.0,
        target=_answers_at_once,
    )
    assert out.detail["outcome"] == "submitted"
    assert out.wall_s > 0.0
    assert out.timing == _TIMING
