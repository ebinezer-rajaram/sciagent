"""The B-sym policy arms and the telling unit in a campaign (P4 Δ11, WP9).

Addresses: the new units are addressed by their full configuration (the
policy's included), and every existing unit keeps its address byte for byte
(pinned below against the digests at 98205b4). Execution: the World-backed
runner, and a fast end-to-end policy unit and telling unit on tiny budgets
with the expensive pieces replaced; the real ones are in the slow tests.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import execute
from sciagent.campaign.analysis import (
    Row,
    Table,
    TellingCell,
    load_rows,
    telling_observed,
    telling_rate,
)
from sciagent.campaign.execute import BaselineJob, WorldRunner
from sciagent.campaign.plan import (
    B_SYM_EIG,
    B_SYM_FSD,
    B_SYM_RANDOM,
    B_SYM_TRUTH_FSD,
    BASELINES,
    POLICY_UNITS,
    TELLING,
    CampaignError,
    LLMArm,
    PolicyArmSettings,
    Unit,
    baseline_key,
    baseline_units,
    session_key,
    system_config_text,
)
from sciagent.campaign.store import ResultStore
from sciagent.glm.interventions import Compose, Experiment
from sciagent.investigation.world import World
from sciagent.scoring.controls_p4 import policy_separation
from sciagent.scoring.telling import Telling
from sciagent.systems.v2.policies import (
    POLICY_MAX_EVENTS,
    REPLICATES,
    design_menu,
    tempered,
)

# --------------------------------------------------------------------------
# Existing addresses are unchanged
# --------------------------------------------------------------------------

#: ``baseline_key(Unit("hawkes", 1, s), entry("hawkes"), environment(),
#: tiny_config()).digest`` at 98205b4, before WP9.
PINNED_BASELINES = {
    "ORACLE": "83943197dfc8ece0bfd1f9bd4d20dddc5bf805df2ed2204838cffd2f5b96b722",
    "B-lib": "4eefb39d79406a75cd9b87717abcaa6b7e974ebdb74bbada292d08e7c837365d",
    "B-np": "14f97e09b91f27587cbe61e686c12a299a913785d880167d22de8edd3085134b",
    "B-rand": "bf5eda01ca80f43d34424edefc763ee68067fe083d48c5c56918f233640ad319",
    "B-sym": "24af2e6203209af5e4dc920a33e9e6175a9c1befcc7473f5b843c85ac0387b77",
    "B-sparse": "4a1031b32c6a7f7c6e37f081f398c20058b6e76a24cf6c61999cb12bca72d27b",
    "planted-hint": "9363d08e5b556689046f5c5978f78325b45924bbacd62b75dc246428c3d892c5",
    "B-sym@10F": "511ddc30fbb12f46920b989128258477c95094077b3a04eff5dad7cf9c8ba960",
}
#: Session keys of four arms at 98205b4 (tiny_config with these arms).
PINNED_ARMS = (
    LLMArm("AG-c", "named"),
    LLMArm("AG-o", "anon"),
    LLMArm("AG-c", "named", policy="EIG"),
    LLMArm("AG-c", "named", policy="FSD"),
)
PINNED_SESSIONS = {
    "AG-c-H/named": "8b0c82b5f8a5e779e0223bb579214d67eb3b92d7033bb0bc80aac1ecf2e7b5e9",
    "AG-o-H/anon": "20072a02c10b3d9f11df9e7d95f8b3ece56eae8c3db34c23cb65123e070f58a6",
    "AG-c-H/named+policy=EIG": (
        "0642f2e4a363649246a1d65bb85a98799f66ed8df38cebfa090764a40f445462"
    ),
    "AG-c-H/named+policy=FSD": (
        "0975bf56c229280092f10419567c3cd1a7a426e6d4d06e4c4300b0b5b83dd266"
    ),
}


def test_existing_baseline_addresses_are_unchanged() -> None:
    env, config, truth = environment(), tiny_config(), entry("hawkes")
    assert set(PINNED_BASELINES) == set(BASELINES)
    for system, digest in PINNED_BASELINES.items():
        key = baseline_key(Unit("hawkes", 1, system), truth, env, config)
        assert str(key.digest) == digest, system


def test_existing_session_addresses_are_unchanged() -> None:
    env, truth = environment(), entry("hawkes")
    config = tiny_config(llm_arms=PINNED_ARMS)
    for arm in PINNED_ARMS:
        system = arm.system("H")
        key = session_key(Unit("hawkes", 1, system), truth, env, config)
        assert str(key.digest) == PINNED_SESSIONS[system], system


# --------------------------------------------------------------------------
# The new units
# --------------------------------------------------------------------------


def test_policy_units_are_known_but_not_in_the_default_plan() -> None:
    assert POLICY_UNITS == (
        B_SYM_RANDOM,
        B_SYM_EIG,
        B_SYM_FSD,
        B_SYM_TRUTH_FSD,
        TELLING,
    )
    assert not set(POLICY_UNITS) & set(BASELINES)
    assert tiny_config().baselines == BASELINES
    config = tiny_config(baselines=(*BASELINES, *POLICY_UNITS))
    units = baseline_units(("hawkes",), config)
    assert {u.system for u in units} >= set(POLICY_UNITS)
    with pytest.raises(CampaignError):
        tiny_config(baselines=("B-sym+nonsense",))


def _digest(system: str, **overrides: Any) -> str:
    config = tiny_config(**overrides)
    key = baseline_key(
        Unit("hawkes", 1, system), entry("hawkes"), environment(), config
    )
    return str(key.digest)


@pytest.mark.parametrize(
    "system", [B_SYM_RANDOM, B_SYM_EIG, B_SYM_FSD, B_SYM_TRUTH_FSD]
)
def test_a_policy_arm_is_addressed_by_its_search_and_its_experiments(
    system: str,
) -> None:
    base = _digest(system)
    assert base != _digest(B_SYM_RANDOM if system != B_SYM_RANDOM else B_SYM_EIG)
    assert base != _digest(system, fits=4)
    assert base != _digest(system, experiments=3)
    text = system_config_text(system, environment(), tiny_config())
    assert "epochs=1+2" in text and "experiments=2" in text


def test_the_policy_config_enters_the_key_of_the_arms_that_read_it() -> None:
    cheaper = PolicyArmSettings(max_events=5_000)
    fewer = PolicyArmSettings(replicates=8)
    warm = PolicyArmSettings(weighting=tempered(10.0))
    for system in (B_SYM_EIG, B_SYM_FSD, B_SYM_TRUTH_FSD):
        assert _digest(system) != _digest(system, policy=cheaper), system
        assert _digest(system) != _digest(system, policy=fewer), system
    # Weights are EIG's alone; the random draw reads no setting.
    assert _digest(B_SYM_EIG) != _digest(B_SYM_EIG, policy=warm)
    assert _digest(B_SYM_FSD) == _digest(B_SYM_FSD, policy=warm)
    assert _digest(B_SYM_RANDOM) == _digest(B_SYM_RANDOM, policy=cheaper)
    # The policy settings never move an existing unit.
    for system in BASELINES:
        assert _digest(system) == _digest(system, policy=cheaper)


def test_the_defaults_are_the_policy_cores() -> None:
    settings = PolicyArmSettings()
    assert settings.replicates == REPLICATES
    assert settings.max_events == POLICY_MAX_EVENTS
    text = system_config_text(B_SYM_FSD, environment(), tiny_config())
    assert f'"max_events":{POLICY_MAX_EVENTS}' in text


def test_the_telling_unit_is_its_own_kind_and_addresses_its_settings() -> None:
    env, truth = environment(), entry("hawkes")
    key = baseline_key(Unit("hawkes", 1, TELLING), truth, env, tiny_config())
    assert key.config["kind"] == "telling"
    assert _digest(TELLING) != _digest(
        TELLING, policy=PolicyArmSettings(telling_replicates=8)
    )
    # The arms' own settings do not move it.
    assert _digest(TELLING) == _digest(
        TELLING, policy=PolicyArmSettings(replicates=8, max_events=5_000)
    )
    assert _digest(TELLING) == _digest(TELLING, experiments=5)


# --------------------------------------------------------------------------
# The World-backed runner
# --------------------------------------------------------------------------


def test_the_world_runner_runs_the_agent_world_and_logs_each_run() -> None:
    world = World(entry("hawkes").spec, 1, truncate=True)
    runner = WorldRunner(world)
    data = runner.run(Experiment(Compose(()), 30.0), "short")
    assert data is not None and world.experiments_run == 1
    twin = World(entry("hawkes").spec, 1, truncate=True)
    _, same = twin.run(Experiment(Compose(()), 30.0))
    assert np.array_equal(data.log.times, same.log.times)
    assert runner.log == [
        {
            "experiment": 0,
            "design": "short",
            "stopped": False,
            "n_events": data.log.n,
            "truncated_at": None,
        }
    ]


def test_a_stopped_world_run_is_none_and_still_uses_its_index() -> None:
    class Stopping:
        experiments_run = 0

        def run(self, experiment: Experiment) -> Any:
            from sciagent.glm.simulate import ExplosionError

            self.experiments_run += 1
            raise ExplosionError("past the cap")

        def truncated_at(self, data_id: str) -> float | None:
            raise AssertionError("never read for a stopped run")

    runner = WorldRunner(Stopping())  # a duck-typed world
    assert runner.run(Experiment(Compose(()), 30.0), "x") is None
    assert runner.log == [
        {"experiment": 0, "design": "x", "stopped": True, "error": "ExplosionError"}
    ]


# --------------------------------------------------------------------------
# Units end to end (slow: real fits and simulations on tiny budgets)
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_a_random_policy_unit_runs_end_to_end() -> None:
    config = tiny_config(fits=2, experiments=1)
    job = BaselineJob(entry("hawkes"), 1, (B_SYM_RANDOM,), environment(), config)
    (result,) = execute.run_baseline_job(job)
    assert result.ok, result.detail
    assert result.reading["fits_used"] == 2.0
    assert result.reading["experiments_used"] == 1.0
    detail = result.detail
    assert detail["policy"]["epochs"] == [1, 1]
    assert len(detail["policy"]["decisions"]) == 1
    assert len(detail["runs"]) == 1
    assert detail["fit_on"] == "observational"
    computed = detail["computed"]
    decision = detail["policy"]["decisions"][0]
    if decision["stopped"]:
        assert computed == []
    else:
        assert computed == [
            [decision["diagnostic"]["name"], decision["diagnostic"]["args"]]
        ]


# --------------------------------------------------------------------------
# Analysis: the telling rate
# --------------------------------------------------------------------------


def _row(truth: str, seed: int, system: str, computed: Any, ok: bool = True) -> Row:
    return Row(
        truth=truth,
        seed=seed,
        system=system,
        llm=False,
        ok=ok,
        submitted=ok,
        per_event=-0.7 if ok else None,
        exact=False,
        distance=0.5,
        has_structure=True,
        similarity=0.9,
        fits_used=4,
        structure=None,
        curve=(),
        error=None if ok else "SciAgentError",
        computed=computed,
    )


def test_the_telling_rate_matches_by_name_and_channel_arguments() -> None:
    target = TellingCell("next_gap_after_large_mark", (("channel", "size"),))
    other = TellingCell("mean_rate", ())
    hit = (("next_gap_after_large_mark", (("channel", "size"), ("quantile", 0.9))),)
    wrong_channel = (("next_gap_after_large_mark", (("channel", "sign"),)),)
    rows = (
        _row("a", 1, B_SYM_FSD, hit),
        _row("a", 2, B_SYM_FSD, wrong_channel),
        _row("b", 1, B_SYM_FSD, (("mean_rate", ()),)),
        _row("c", 1, B_SYM_FSD, hit),  # no telling cell: not counted
        _row("a", 1, B_SYM_RANDOM, (), ok=False),  # failed: not observed
        _row("a", 1, "B-sym", None),  # not a policy arm: not read
    )
    table = Table(
        {},
        rows,
        (),
        {},
        telling={("a", 1): target, ("a", 2): target, ("b", 1): other},
    )
    seen = telling_observed(table, B_SYM_FSD)
    assert seen == [("a", 1, True), ("a", 2, False), ("b", 1, True)]
    share, n = telling_rate(table, B_SYM_FSD)
    assert n == 2 and share == pytest.approx((0.5 + 1.0) / 2)
    assert telling_observed(table, B_SYM_RANDOM) == [("a", 1, False)]
    assert telling_observed(table, "B-sym") == []


def test_policy_settings_validate() -> None:
    with pytest.raises(CampaignError):
        PolicyArmSettings(replicates=1)
    with pytest.raises(CampaignError):
        PolicyArmSettings(max_events=0)
    with pytest.raises(CampaignError):
        PolicyArmSettings(entertained=5)
    assert dataclasses.replace(PolicyArmSettings(), max_events=2000).max_events == 2000


@pytest.mark.slow
@pytest.mark.parametrize("system", [B_SYM_EIG, B_SYM_FSD, B_SYM_TRUTH_FSD])
def test_a_simulating_policy_unit_runs_end_to_end(system: str) -> None:
    config = tiny_config(
        fits=2,
        experiments=1,
        policy=PolicyArmSettings(replicates=2, max_events=2_000),
    )
    job = BaselineJob(entry("hawkes"), 1, (system,), environment(), config)
    (result,) = execute.run_baseline_job(job)
    assert result.ok, result.detail
    decision = result.detail["policy"]["decisions"][0]
    assert decision["scores"] is not None or decision["fallback"] is not None
    if system == B_SYM_TRUTH_FSD:
        assert result.detail["comparator"]


@pytest.mark.slow
def test_the_telling_unit_runs_end_to_end() -> None:
    config = tiny_config(policy=PolicyArmSettings(telling_replicates=2))
    job = BaselineJob(entry("hawkes"), 1, (TELLING,), environment(), config)
    (result,) = execute.run_baseline_job(job)
    assert result.ok, result.detail
    assert 0.0 <= result.reading["value"] <= 1.0
    assert result.detail["member"]


# --------------------------------------------------------------------------
# Store round trip: unit detail -> analysis (fast: fakes for the heavy parts)
# --------------------------------------------------------------------------


def test_a_telling_and_a_policy_cell_round_trip_through_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, truth = environment(), entry("hawkes")
    config = tiny_config(seeds=(1,), baselines=(B_SYM_FSD, TELLING))

    @dataclass(frozen=True)
    class Picked:
        model: Any = None
        submitted: str = "hawkes"

    class FakeBLib:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, data: Any) -> Picked:
            return Picked()

    def fake_telling(
        truth_model: Any, member: Any, menu: Any, *a: Any, **k: Any
    ) -> Any:
        target = next(
            d for d, c in enumerate(menu.diagnostics) if c.name == "mark_mean"
        )
        table = tuple(
            tuple(
                1.0 if d == target and e == 2 else 0.0
                for d in range(len(menu.diagnostics))
            )
            for e in range(len(menu.designs))
        )
        return Telling.from_table(table, menu.designs, menu.diagnostics)

    monkeypatch.setattr(execute, "BLib", FakeBLib)
    monkeypatch.setattr(execute, "telling_diagnostic", fake_telling)
    data = execute.observational_data(truth, 1, env)
    job = BaselineJob(truth, 1, (TELLING,), env, config)
    told = execute._telling_unit(job, data, 0.0)
    assert told.ok
    assert told.detail["design"] == design_menu(env.channels)[2].name
    target = told.detail["diagnostic"]
    assert target["name"] == "mark_mean" and target["channel_args"]

    computed = [["mark_mean", dict(target["args"])], ["mean_rate", {}]]
    policy_reading = {"status": 1.0, "per_event": -0.7, "fits_used": 2.0}
    policy_detail = {"structure": None, "curve": [], "computed": computed}
    with ResultStore(tmp_path) as store:
        for system, reading, detail in (
            (TELLING, told.reading, told.detail),
            (B_SYM_FSD, policy_reading, policy_detail),
        ):
            key = baseline_key(Unit(truth.id, 1, system), truth, env, config)
            store.put(key, reading, {"unit": {}, "result": detail})
        table = load_rows(store, env, config, [truth])
    assert TELLING not in {r.system for r in table.rows}
    assert table.telling[(truth.id, 1)].name == "mark_mean"
    assert telling_observed(table, B_SYM_FSD) == [(truth.id, 1, True)]


def test_the_pilot_environment_carries_the_mark_law_for_eig_and_fsd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scripts/pilot.py must pass pointproc's marks before any EIG or FSD run."""
    import importlib.util
    import sys

    from environments.pointproc import v2

    path = Path(__file__).resolve().parents[2] / "scripts" / "pilot.py"
    spec = importlib.util.spec_from_file_location("pilot_script_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # for its dataclasses
    spec.loader.exec_module(module)
    env = module.pointproc_environment(())
    assert env.investigation is not None
    assert env.investigation.marks is v2.mark_sampler


def test_control_5_reads_the_two_arms_telling_observations() -> None:
    """Control 5 (WP11) pairs truthFSD and random by (truth, seed) on the
    analysis read-out; a failed unit counts as not observed."""
    target = TellingCell("mean_rate", ())
    hit = (("mean_rate", ()),)
    miss = (("fano_factor", ()),)
    truths = [f"t{i}" for i in range(7)]
    rows = (
        *(_row(t, 1, B_SYM_TRUTH_FSD, hit) for t in truths[:6]),
        _row(truths[6], 1, B_SYM_TRUTH_FSD, (), ok=False),
        *(_row(t, 1, B_SYM_RANDOM, miss) for t in truths[:5]),
        *(_row(t, 1, B_SYM_RANDOM, hit) for t in truths[5:]),
    )
    table = Table({}, rows, (), {}, telling={(t, 1): target for t in truths})
    v = policy_separation(
        telling_observed(table, B_SYM_TRUTH_FSD), telling_observed(table, B_SYM_RANDOM)
    )
    assert (v.units, v.both, v.truth_only, v.random_only, v.neither) == (7, 1, 5, 1, 0)
    # P(Binom(6, 1/2) >= 5) = 7/64 > 0.05: five against one does not pass.
    assert v.p_value == 7 / 64 and not v.passed
    assert v.truth_rate == pytest.approx(6 / 7)
    assert v.random_rate == pytest.approx(2 / 7)
