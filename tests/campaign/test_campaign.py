"""The campaign driver (P3): plan expansion, addresses, the append-only store,
resumption, failure capture, and a scripted LLM unit end to end.

Fast: job functions are replaced by cheap fakes and run in-process
(``DriverSettings(workers=0)``); the LLM session is a scripted driver over the
real tool layer. The slow file runs real units.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from campaign_support import TRUTH_IDS, entry, environment, tiny_config

from sciagent.campaign import driver as driver_module
from sciagent.campaign import execute
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, ScoreJob, UnitResult
from sciagent.campaign.plan import (
    B_SYM_10F,
    BASELINES,
    METRIC_VERSION,
    PLANTED,
    LLMArm,
    Unit,
    baseline_key,
    baseline_units,
    llm_units,
    score_key,
    session_key,
)
from sciagent.campaign.store import ResultStore, cell_bytes
from sciagent.core.errors import RegistryConflictError, SciAgentError
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


def test_baseline_plan_runs_the_control_on_control_seeds_only() -> None:
    config = tiny_config(seeds=(1, 2, 3), control_seeds=(1,))
    units = baseline_units(TRUTH_IDS, config)
    per_seed = len(BASELINES) - 1
    assert len(units) == 2 * (3 * per_seed + 1)
    assert {u.seed for u in units if u.system == B_SYM_10F} == {1}
    assert len(set(units)) == len(units)
    assert [u.seed for u in units] == sorted(u.seed for u in units)


def test_llm_plan_is_breadth_first() -> None:
    config = tiny_config(seeds=(1, 2))
    units = llm_units(("a", "b"), config)
    names = [(u.seed, u.truth_id, u.system) for u in units]
    assert names[:4] == [
        (1, "a", "AG-c-H/named"),
        (1, "a", "AG-o-H/named"),
        (1, "b", "AG-c-H/named"),
        (1, "b", "AG-o-H/named"),
    ]
    assert names[4][2].endswith("/anon")
    assert all(s == 1 for s, _, _ in names[:8])
    assert len(units) == 2 * 2 * 4


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------


def test_addresses_are_stable_and_cover_what_determines_a_cell() -> None:
    env, config, truth = environment(), tiny_config(), entry("hawkes")
    unit = Unit("hawkes", 1, PLANTED)
    key = baseline_key(unit, truth, env, config)
    assert baseline_key(unit, entry("hawkes"), environment(), tiny_config()) == key
    assert key.digest == baseline_key(unit, truth, env, config).digest
    others = {
        baseline_key(Unit("hawkes", 2, PLANTED), truth, env, config).digest,
        baseline_key(Unit("hawkes", 1, "B-sym"), truth, env, config).digest,
        baseline_key(unit, truth, env, tiny_config(fits=4)).digest,
        baseline_key(unit, dataclasses.replace(truth, digest="x"), env, config).digest,
    }
    assert key.digest not in others and len(others) == 4
    assert key.metric_version == METRIC_VERSION


def test_a_session_address_has_no_metric_version_and_its_score_points_at_it() -> None:
    env, config, truth = environment(), tiny_config(), entry("hawkes")
    unit = Unit("hawkes", 1, LLMArm("AG-c", "named").system("H"))
    skey = session_key(unit, truth, env, config)
    assert skey.metric_version == "none"
    ckey = score_key(unit, skey, env, config)
    assert ckey.config["session"] == str(skey.digest)
    assert ckey.metric_version == METRIC_VERSION
    assert session_key(unit, truth, env, tiny_config(max_turns=21)) != skey


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


def _key() -> Any:
    return baseline_key(
        Unit("hawkes", 1, "ORACLE"), entry("hawkes"), environment(), tiny_config()
    )


def test_store_accepts_a_faithful_rerun_and_refuses_a_different_one(
    tmp_path: Path,
) -> None:
    key = _key()
    with ResultStore(tmp_path) as store:
        store.put(key, {"status": 1.0, "x": float("nan")}, {"a": 1})
        store.put(key, {"status": 1.0, "x": float("nan")}, {"a": 1})
        assert store.ledger.count() == 1
        with pytest.raises(RegistryConflictError):
            store.put(key, {"status": 1.0, "x": float("nan")}, {"a": 2})
        with pytest.raises(RegistryConflictError):
            store.put(key, {"status": 0.0}, {"a": 1})
        assert store.ledger.count() == 1
        assert store.cell(key.digest)["detail"] == {"a": 1}


def test_store_recovers_a_detail_whose_row_was_not_committed(tmp_path: Path) -> None:
    key = _key()
    with ResultStore(tmp_path) as store:
        store.detail_path(key.digest).write_bytes(
            cell_bytes(key, {"status": 1.0}, {"a": 1})
        )
        assert not store.done(key)
        assert store.recover() == 1
        assert store.done(key)
        assert store.recover() == 0


# --------------------------------------------------------------------------
# Driver: resumption and failures
# --------------------------------------------------------------------------


def _fake_job(
    calls: list[tuple[str, int, tuple[str, ...]]],
) -> Callable[[BaselineJob], tuple[UnitResult, ...]]:
    def fake(job: BaselineJob) -> tuple[UnitResult, ...]:
        calls.append((job.truth.id, job.seed, job.systems))
        return tuple(
            UnitResult(s, True, {"status": 1.0, "per_event": -0.7}, {"s": s}, 0.0)
            for s in job.systems
        )

    return fake


def _campaign(root: Path, **settings: Any) -> Campaign:
    return Campaign(
        root,
        environment(),
        tiny_config(),
        [entry(t) for t in TRUTH_IDS],
        DriverSettings(workers=0, poll_s=0.05, **settings),
    )


def test_a_resumed_campaign_runs_nothing_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, tuple[str, ...]]] = []
    monkeypatch.setattr(driver_module, "run_baseline_job", _fake_job(calls))
    first = _campaign(tmp_path, run_llm=False)
    summary = first.run()
    first.close()
    n_units = len(baseline_units(TRUTH_IDS, tiny_config()))
    assert sum(summary.recorded.values()) == n_units
    assert ("hawkes", 1, ("ORACLE", "B-lib", "B-np")) in calls
    n_jobs = len(calls)
    second = _campaign(tmp_path, run_llm=False)
    again = second.run()
    second.close()
    assert len(calls) == n_jobs
    assert again.skipped_done == n_units and not again.recorded


def test_a_failing_system_is_recorded_as_a_failed_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_system(system: str, job: BaselineJob, data: Any) -> Any:
        if system == "B-rand":
            raise SciAgentError("no proposal produced a certified fit")
        raise AssertionError("only B-rand is run")

    monkeypatch.setattr(execute, "_run_system", run_system)
    job = BaselineJob(entry("hawkes"), 1, ("B-rand",), environment(), tiny_config())
    (result,) = execute.run_baseline_job(job)
    assert not result.ok and result.reading == {"status": 0.0}
    assert result.detail["error"] == "SciAgentError"


def test_a_job_that_raises_a_bug_is_logged_not_recorded_and_the_rest_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, tuple[str, ...]]] = []
    fake = _fake_job(calls)

    def flaky(job: BaselineJob) -> tuple[UnitResult, ...]:
        if job.truth.id == "hawkes" and job.systems == (PLANTED,):
            raise KeyError("a bug")
        return fake(job)

    monkeypatch.setattr(driver_module, "run_baseline_job", flaky)
    campaign = _campaign(tmp_path, run_llm=False)
    summary = campaign.run()
    campaign.close()
    n_units = len(baseline_units(TRUTH_IDS, tiny_config()))
    assert summary.bugs == 2  # hawkes x 2 seeds
    assert sum(summary.recorded.values()) == n_units - 2
    assert "BUG in job" in (tmp_path / "progress.log").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# A scripted LLM unit end to end
# --------------------------------------------------------------------------

SCRIPT = (
    Step(calls=(("diagnostic", {"name": "mean_rate", "data_id": "obs"}),)),
    Step(
        calls=(
            ("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]}),
            (
                "fit",
                {"structure": "Excite(ExpK, Mark(size), all)", "data_ids": ["obs"]},
            ),
        )
    ),
    Step(
        calls=(
            (
                "submit",
                {"structure": "Excite(ExpK, Mark(size), all)", "report": "done"},
            ),
        )
    ),
    Step(text="submitted"),
)


def clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_a_scripted_llm_unit_is_recorded_scored_and_not_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    scored: list[ScoreJob] = []

    def fake_score(job: ScoreJob) -> UnitResult:
        scored.append(job)
        return UnitResult(job.system, True, {"status": 1.0}, {"s": 1}, 0.0)

    monkeypatch.setattr(driver_module, "run_score_job", fake_score)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    launched: list[Unit] = []

    def factory(unit: Unit) -> ScriptedDriver:
        launched.append(unit)
        return ScriptedDriver(SCRIPT)

    def campaign() -> Campaign:
        return Campaign(
            tmp_path,
            environment(),
            config,
            [entry("size_excitation")],
            DriverSettings(
                workers=0, poll_s=0.05, run_baselines=False, driver_factory=factory
            ),
        )

    c = campaign()
    summary = c.run()
    unit = Unit("size_excitation", 1, "AG-c-H/named")
    skey = session_key(unit, entry("size_excitation"), environment(), config)
    cell = c.store.cell(skey.digest)
    c.close()
    assert launched == [unit] and summary.bugs == 0
    reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
    assert reading["fits_used"] == 2.0 and reading["submitted"] == 1.0
    assert reading["void"] == 0.0 and reading["turns"] == 4.0
    session = cell["detail"]["session"]
    assert session["outcome"] == "submitted"
    assert session["fitted"] == [
        "link=identity; Excite(ExpK, One, all)",
        "link=identity; Excite(ExpK, Mark(size), all)",
    ]
    record = tmp_path / session["record_dir"] / session["record"]
    assert json.loads(record.read_text(encoding="utf-8"))["entries"]
    (job,) = scored
    assert job.submission == "link=identity; Excite(ExpK, Mark(size), all)"
    assert job.fitted == tuple(session["fitted"])

    again = campaign()
    rerun = again.run()
    again.close()
    assert launched == [unit] and rerun.skipped_done == 1 and len(scored) == 1


def test_a_non_finite_score_is_stored_not_refused(tmp_path: Path) -> None:
    key = _key()
    with ResultStore(tmp_path) as store:
        store.put(key, {"per_event": float("-inf")}, {"curve": [[1, "x", -math.inf]]})
        cell = store.cell(key.digest)
    assert cell["detail"]["curve"] == [[1, "x", "-inf"]]
    assert float.fromhex(cell["reading"]["per_event"]) == -math.inf


def test_a_failure_to_record_is_logged_and_the_campaign_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, tuple[str, ...]]] = []
    fake = _fake_job(calls)

    def bad_detail(job: BaselineJob) -> tuple[UnitResult, ...]:
        out = fake(job)
        if job.truth.id == "hawkes" and job.systems == (PLANTED,):
            return (UnitResult(PLANTED, True, {"status": 1.0}, {"x": object()}, 0.0),)
        return out

    monkeypatch.setattr(driver_module, "run_baseline_job", bad_detail)
    campaign = _campaign(tmp_path, run_llm=False)
    summary = campaign.run()
    campaign.close()
    n_units = len(baseline_units(TRUTH_IDS, tiny_config()))
    assert summary.bugs == 2
    assert sum(summary.recorded.values()) == n_units - 2


def test_a_unit_another_process_ran_after_planning_is_not_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, tuple[str, ...]]] = []
    monkeypatch.setattr(driver_module, "run_baseline_job", _fake_job(calls))
    late = _campaign(tmp_path, run_llm=False)
    late._plan_baselines()
    planned = list(late.pool_queue)
    early = _campaign(tmp_path, run_llm=False)
    early.run()
    early.close()
    assert planned and all(late._pending(pj) is None for pj in planned)
    late.close()


def test_the_control_can_be_limited_to_the_first_k_truths() -> None:
    config = tiny_config(seeds=(1, 2), control_seeds=(1,), control_truths=1)
    units = baseline_units(TRUTH_IDS, config)
    assert [u for u in units if u.system == B_SYM_10F] == [
        Unit(TRUTH_IDS[0], 1, B_SYM_10F)
    ]
    full = baseline_units(TRUTH_IDS, tiny_config(seeds=(1, 2)))
    assert len(full) == len(units) + 1


def test_heavy_jobs_wait_for_a_heavy_slot(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, run_llm=False, max_heavy=1)
    campaign._plan_baselines()
    heavy = next(pj for pj in campaign.pool_queue if driver_module._is_heavy(pj))
    campaign.pool_queue.remove(heavy)
    campaign.pool_queue.appendleft(heavy)
    assert campaign._next_job({}) is heavy  # a free heavy slot: first in line
    campaign.pool_queue.appendleft(heavy)
    nxt = campaign._next_job({0: heavy})  # the slot is taken: skip it
    campaign.close()
    assert not driver_module._is_heavy(nxt)
    assert campaign.pool_queue[0] is heavy  # it keeps its place


def test_a_retryable_session_drops_llm_concurrency_to_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sciagent.campaign.llm import SessionOutcome

    def rate_limited(unit: Unit, *args: Any, **kwargs: Any) -> SessionOutcome:
        return SessionOutcome(
            unit, True, {}, {"outcome": "session_error"}, None, (), 0.0
        )

    monkeypatch.setattr(driver_module, "run_session", rate_limited)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    campaign = Campaign(
        tmp_path,
        environment(),
        config,
        [entry(t) for t in TRUTH_IDS],
        DriverSettings(
            workers=0,
            poll_s=0.05,
            run_baselines=False,
            llm_concurrency=2,
            max_attempts=1,
            backoff_after=99,
            driver_factory=lambda unit: ScriptedDriver(SCRIPT),
        ),
    )
    summary = campaign.run()
    campaign.close()
    assert campaign.llm_concurrency == 1
    assert summary.retries == 2 and not summary.recorded
    lines = (tmp_path / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
