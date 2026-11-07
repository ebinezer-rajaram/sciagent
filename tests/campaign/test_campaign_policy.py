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

from sciagent.campaign import execute, plan
from sciagent.campaign.analysis import (
    Row,
    Table,
    TellingCell,
    load_rows,
    row_from_cell,
    telling_cell,
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
from sciagent.scoring.errors import ScoringError
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
#: tiny_config()).digest``, first pinned at 98205b4 before WP9. Re-pinned by F5
#: point 2 (LOG 2026-11-05): every key now carries the constants' digests and
#: the domain's text. A new unit must still move none of these; a change to a
#: constant moves them all, by design, and is re-pinned saying why.
PINNED_BASELINES = {
    "ORACLE": "b9e08b1dafcfc4775a9fb7e7ce7033a23b2fce3d06d3e9f9e42809a4fd65d140",
    "B-lib": "4e81ef10b4edadb06a0db011b3f0a8dc2eb8fbaf4885939d3d81df84b4eb9e15",
    "B-np": "c26db4d7fca4a85d5398fab069f828e2ba47d619ae05a55d40906e4ea70ef56d",
    "B-rand": "f08ee5ff3b0f1f26e5ee41b0886fc4c669e6b623d42a94a59920263015177f13",
    "B-sym": "80300c22a691c9c6b443ec01b4e4e28705fd2d2ce773adbd7d3273e15c56001c",
    "B-sparse": "59219f6f6356b85314c59580485b2b8960055f32d5b4d1b8e44fdaf498fa5ac0",
    "planted-hint": "09e85b3d816a0cfab462bf28a92ceb81c1ee9e747fa9a436d93b22263207c53e",
    "B-sym@10F": "5593b739cf333ee6aab7576a4d87561fa48423576121f93a355af4d5f13f8315",
}
#: Session keys of four arms at 98205b4 (tiny_config with these arms). The
#: AG-o key is re-pinned at the merge into v2: its harness/2 text ends with
#: v2's sandbox block (``plan._with_sandbox``), as its harness/1 text did.
#: The EIG and FSD keys are re-pinned when the arm settings' cap and EIG
#: weighting reach AG-c-S: the recorded policy settings name both. They are
#: re-pinned again by C5: ``policy_config`` names the truth-keyed seed rule,
#: and the two plain arms stay as they were. All four are re-pinned by F5 point
#: 2, as the baselines are; :data:`PINNED_SESSION_CONFIG` did not move. All
#: four are ``v1`` arms, so the prompt's wall-limit sentence (LOG 2026-11-05,
#: session 34), which only P4's variant states, leaves them as they were.
#: The EIG key and its ``session_config`` are re-pinned by Δ11 items 2, 3 and 5
#: (LOG 2026-10-27, adopted 2026-10-28; built 2026-11-07): ``EIGPolicy.config``
#: now names the tie rule, the
#: relative-precision computation and the logged KL pick, and ``policy_config``
#: holds it. No EIG session has run. The other three did not move.
PINNED_ARMS = (
    LLMArm("AG-c", "named"),
    LLMArm("AG-o", "anon"),
    LLMArm("AG-c", "named", policy="EIG"),
    LLMArm("AG-c", "named", policy="FSD"),
)
PINNED_SESSIONS = {
    "AG-c-H/named": "160e1721cab333ad7bc1a1cd632951299608b793a4c569263ec41d25031fbddc",
    "AG-o-H/anon": "b3afe49d0f78c00aa28e73d757db1a6d8f95dd31f8970d276cb16ea945aca3b1",
    "AG-c-H/named+policy=EIG": (
        "161f16f57fd46ccb718b72c078f4befa65afe09d9b5bc3b0db3b0eb08dce91ed"
    ),
    "AG-c-H/named+policy=FSD": (
        "badd4173b33de541dc18bab4aa17a0e6211d32e0112f1e255a6a16aa25c93028"
    ),
}


#: What F5 point 2 left in place, each from 2205433, the last head that reads
#: the pilot's store: the ``system_config`` of the six baselines whose text
#: already read what runs, so their configuration did not change, only their
#: versions. ORACLE and B-lib are absent: their text is what F5 replaced.
PINNED_SYSTEM_CONFIG = {
    "B-np": "92efec386c2d74dafab6e2143d462e70de60656c6b4b4a6bd4b0f4b420918399",
    "B-rand": "f7b3708fe8b36a9cc5c4bdff7655acc51c45b2b454ca2787e98fa9399b2edb65",
    "B-sym": "0147372cb9b5705f1696ea1451b8a3db23a345bdd6d03cfa140d72bd80c20f4d",
    "B-sparse": "971c13da34185444c0c71f2bdd5a9b798898fdf1ef33f50f0db54bfb122416a2",
    "planted-hint": "0147372cb9b5705f1696ea1451b8a3db23a345bdd6d03cfa140d72bd80c20f4d",
    "B-sym@10F": "2ee1ce0bb881cfb217e05fb8a701b227c31556be38126211ed7b811b5090948a",
}
#: The two texts F5 replaced, as they were at 2205433 and as they are now.
MOVED_SYSTEM_CONFIG = {
    "ORACLE": (
        "216303d4371cfcf102764ab67a1d1401c01369d4c1cf7bb2a395b01d90d96fbf",
        "4d701af2781e5ff43100d200c907066d0d05aae8eee278dfdf002bb2cba1351d",
    ),
    "B-lib": (
        "59bed42a6319cfa9efb38b3d8d38e48b1a3b43b90cb0f8ae766d2f1ae759f127",
        "8c4ff49f1a2e551c35eabe9ca9285165f2c7eaac8e71a8d30e8bc24775794841",
    ),
}
#: Each arm's ``session_config`` at 2205433: F5 point 2 changes a session's
#: versions, not what it ran, and these ``v1`` arms state no wall limit, so these
#: stand. EIG's is re-pinned by Δ11 items 2, 3 and 5 (LOG 2026-10-27): what an
#: EIG session runs did change.
PINNED_SESSION_CONFIG = {
    "AG-c-H/named": "87c18124f53bb21ec6afd8a7666873330f4009531cb2c85ef8a0d52296fd4133",
    "AG-o-H/anon": "828ae3fa9ca1f0aef2071aac280f2f946805e94616934ac08a931b449fc73c50",
    "AG-c-H/named+policy=EIG": (
        "0d7cc6b06776bdd7595a7f3aa0402f5a72c7962f32085647a42a6ce5326d3049"
    ),
    "AG-c-H/named+policy=FSD": (
        "d07a77a8a7f5df76a15622fbe7d2385e30c0fe345ca049d966fe1debeafb1d94"
    ),
}


def test_what_a_baseline_runs_did_not_move_with_its_versions() -> None:
    env, config, truth = environment(), tiny_config(), entry("hawkes")
    for system, digest in PINNED_SYSTEM_CONFIG.items():
        key = baseline_key(Unit("hawkes", 1, system), truth, env, config)
        assert key.config["system_config"] == digest, system


def test_oracles_and_b_libs_text_moved_as_f5_says() -> None:
    env, config, truth = environment(), tiny_config(), entry("hawkes")
    assert set(PINNED_SYSTEM_CONFIG) | set(MOVED_SYSTEM_CONFIG) == set(BASELINES)
    for system, (before, after) in MOVED_SYSTEM_CONFIG.items():
        key = baseline_key(Unit("hawkes", 1, system), truth, env, config)
        assert key.config["system_config"] == after != before, system


def test_what_a_session_ran_did_not_move_with_its_versions() -> None:
    env, truth = environment(), entry("hawkes")
    config = tiny_config(llm_arms=PINNED_ARMS)
    for arm in PINNED_ARMS:
        system = arm.system("H")
        key = session_key(Unit("hawkes", 1, system), truth, env, config)
        assert key.config["session_config"] == PINNED_SESSION_CONFIG[system], system


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
        _row("c", 1, B_SYM_FSD, hit),  # no telling cell: not read (control 5: plan)
        _row("a", 1, B_SYM_RANDOM, (), ok=False),  # failed: no reading (None)
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
    assert telling_observed(table, B_SYM_RANDOM) == [("a", 1, None)]
    assert telling_rate(table, B_SYM_RANDOM) == (0.0, 1)  # the rate: not observed
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

    monkeypatch.setattr(plan, "BLib", FakeBLib)
    monkeypatch.setattr(execute, "telling_diagnostic", fake_telling)
    data = execute.observational_data(truth, 1, env)
    job = BaselineJob(truth, 1, (TELLING,), env, config)
    told = execute._telling_unit(job, data, 0.0, None)
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
    analysis read-out; a failed truthFSD unit counts as not observed."""
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
        telling_observed(table, B_SYM_TRUTH_FSD),
        telling_observed(table, B_SYM_RANDOM),
        planned=[(t, 1) for t in truths],
    )
    assert (v.units, v.both, v.truth_only, v.random_only, v.neither) == (7, 1, 5, 1, 0)
    # P(Binom(6, 1/2) >= 5) = 7/64 > 0.05: five against one does not pass.
    assert v.p_value == 7 / 64 and not v.passed
    assert v.truth_rate == pytest.approx(6 / 7)
    assert v.random_rate == pytest.approx(2 / 7)


# --------------------------------------------------------------------------
# Control 5 and the crash rule (red team 2026-11-07, findings 1, 2 and 4)
# --------------------------------------------------------------------------

#: The red team's stand-in: 10 truths at seed 1; truthFSD observes t01 to t06
#: and random t06 and t07, so b = 5, c = 1, p = 7/64: control 5 fails.
_C5_TRUTHS = tuple(f"t{i:02d}" for i in range(1, 11))
_C5_PLAN = tuple((t, 1) for t in _C5_TRUTHS)
_C5_TARGET: tuple[str, dict[str, Any]] = ("mark_moment", {"channel": "a"})
_C5_HITS = {
    B_SYM_TRUTH_FSD: frozenset(f"t{i:02d}" for i in range(1, 7)),
    B_SYM_RANDOM: frozenset({"t06", "t07"}),
}
_C5_FAILED: dict[str, Any] = {
    "reading": {"status": (0.0).hex()},
    "detail": {"result": {"error": "ValueError", "message": "boom"}},
}
_C5_TOLD: dict[str, Any] = {
    "reading": {"status": (1.0).hex()},
    "detail": {
        "result": {"diagnostic": {"name": _C5_TARGET[0], "channel_args": _C5_TARGET[1]}}
    },
}

type _Edit = dict[str, frozenset[Any]]


def _c5_cell(hit: bool, computed: bool = True) -> dict[str, Any]:
    chosen = [list(_C5_TARGET)] if hit else [["rate", {"channel": "b"}]]
    result: dict[str, Any] = {"structure": "s", "curve": []}
    if computed:
        result["computed"] = chosen
    reading = {
        "status": 1.0,
        "per_event": -1.0,
        "has_structure": 1.0,
        "fits_used": 40.0,
    }
    return {
        "reading": {k: v.hex() for k, v in reading.items()},
        "detail": {"result": result},
    }


def _c5_table(
    hits: dict[str, frozenset[str]] = _C5_HITS,
    /,
    *,
    failed: frozenset[tuple[str, str]] = frozenset(),
    removed: frozenset[tuple[str, str]] = frozenset(),
    uncomputed: frozenset[tuple[str, str]] = frozenset(),
    telling_failed: frozenset[str] = frozenset(),
    telling_removed: frozenset[str] = frozenset(),
) -> Table:
    """Stored cells read as ``load_rows`` reads them. ``(arm, truth)`` pairs
    fail (status 0), have no cell, or are ``ok`` without ``computed``."""
    rows: list[Row] = []
    telling: dict[tuple[str, int], TellingCell] = {}
    for t in _C5_TRUTHS:
        if t not in telling_removed:
            found = telling_cell(_C5_FAILED if t in telling_failed else _C5_TOLD)
            if found is not None:
                telling[(t, 1)] = found
        for arm in (B_SYM_TRUTH_FSD, B_SYM_RANDOM):
            if (arm, t) in removed:
                continue
            cell = (
                _C5_FAILED
                if (arm, t) in failed
                else _c5_cell(t in hits[arm], (arm, t) not in uncomputed)
            )
            rows.append(row_from_cell(t, 1, arm, False, cell))
    return Table({}, tuple(rows), (), {}, telling=telling)


def _c5(table: Table) -> Any:
    return policy_separation(
        telling_observed(table, B_SYM_TRUTH_FSD),
        telling_observed(table, B_SYM_RANDOM),
        planned=_C5_PLAN,
    )


def _arms_on(t: str) -> frozenset[tuple[str, str]]:
    return frozenset({(B_SYM_TRUTH_FSD, t), (B_SYM_RANDOM, t)})


def test_the_control_5_stand_in_fails_as_built_and_passes_without_t07() -> None:
    v = _c5(_c5_table())
    assert (v.passed, v.p_value, v.units) == (False, 7 / 64, 10)
    assert (v.truth_only, v.random_only) == (5, 1)
    passing = {**_C5_HITS, B_SYM_RANDOM: frozenset({"t06"})}
    v = _c5(_c5_table(passing))
    assert (v.passed, v.p_value, v.truth_only, v.random_only) == (True, 1 / 32, 5, 0)


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param({"failed": frozenset({(B_SYM_RANDOM, "t07")})}, id="f1-random"),
        pytest.param({"failed": _arms_on("t07")}, id="f1-both-failed"),
        pytest.param({"telling_failed": frozenset({"t07"})}, id="f2-telling-failed"),
        pytest.param({"telling_removed": frozenset({"t07"})}, id="f2-no-telling"),
        pytest.param({"removed": _arms_on("t07")}, id="f4-both-missing"),
        pytest.param({"uncomputed": _arms_on("t07")}, id="f4-no-computed"),
    ],
)
def test_the_red_team_s_crashes_keep_control_5_failing(edit: _Edit) -> None:
    """As coded these passed at p = 1/32 on 9 or 10 units. A failure ranks
    against the pass, so each reads b = 5, c = 1, p = 7/64 on all 10 units."""
    v = _c5(_c5_table(**edit))
    assert (v.passed, v.p_value, v.units) == (False, 7 / 64, 10)
    assert (v.truth_only, v.random_only) == (5, 1)


def test_random_failing_on_a_truth_fsd_only_unit_moves_it_to_both() -> None:
    """The red team's case 8: random failed on t07 and on t01."""
    failed = frozenset({(B_SYM_RANDOM, "t07"), (B_SYM_RANDOM, "t01")})
    v = _c5(_c5_table(failed=failed))
    assert (v.passed, v.both, v.truth_only, v.random_only) == (False, 2, 4, 1)


def _c5_sweep() -> list[tuple[str, _Edit]]:
    cases: list[tuple[str, _Edit]] = []
    for t in _C5_TRUTHS:
        for arm in (B_SYM_TRUTH_FSD, B_SYM_RANDOM):
            one = frozenset({(arm, t)})
            cases += [
                (f"{arm} {t} failed", {"failed": one}),
                (f"{arm} {t} removed", {"removed": one}),
                (f"{arm} {t} without computed", {"uncomputed": one}),
            ]
        cases += [
            (f"both arms {t} failed", {"failed": _arms_on(t)}),
            (f"both arms {t} removed", {"removed": _arms_on(t)}),
            (f"both arms {t} without computed", {"uncomputed": _arms_on(t)}),
            (f"telling {t} failed", {"telling_failed": frozenset({t})}),
            (f"telling {t} removed", {"telling_removed": frozenset({t})}),
        ]
    return cases


def test_no_single_crash_or_missing_reading_makes_control_5_pass() -> None:
    """The crash rule (INBOX 2026-11-07): on a stand-in where control 5 fails,
    fail or remove each unit of each arm and each telling cell, one at a time.
    Each must still fail with p no smaller than the stand-in's, or be refused
    (one arm's unit without the other's voids the control)."""
    assert len(_c5_sweep()) == 10 * 11
    improved: list[str] = []
    refused: list[str] = []
    for label, edit in _c5_sweep():
        try:
            v = _c5(_c5_table(**edit))
        except ScoringError:
            refused.append(label)
            continue
        if v.passed or v.p_value < 7 / 64:
            improved.append(f"{label}: p={v.p_value}")
    assert improved == []
    # Unpaired: one arm's unit removed, or ok without computed (2 x 2 x 10).
    assert len(refused) == 40, refused
