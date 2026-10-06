"""The LLM session key hashes the rendered surface (P4 Δ7).

A session's address carries the SHA-256 of the canonical JSON of what the
agent is shown before its first call -- the system prompt and every tool
schema (:func:`~sciagent.investigation.runner.rendered_config`) -- so any edit
to a prompt or a schema moves every session to a new address instead of
silently resuming old sessions under new text. The pilot's ``harness/1``
sessions stay reachable for rescoring through
:func:`~sciagent.campaign.plan.legacy_session_text_v1`, the old text frozen.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import driver as driver_module
from sciagent.campaign import plan
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import ScoreJob, UnitResult
from sciagent.campaign.plan import (
    PILOT_ARMS,
    CampaignConfig,
    CampaignError,
    LLMArm,
    Unit,
    legacy_session_config_digest,
    legacy_session_text_v1,
    score_key,
    session_config_digest,
    session_key,
    session_text,
)
from sciagent.campaign.rescore import stored_sessions
from sciagent.campaign.store import ResultStore
from sciagent.glm.grammar import ChannelKind
from sciagent.harness.live import HARNESS_VERSION
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.investigation import prompts, tools
from sciagent.investigation.runner import rendered_config
from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

#: A real pilot session cell's ``session_config`` text and its digest
#: (``.cache/pilot``, AG-c-S/anon, dev-019, seed 1).
PILOT_TEXT: Final = (
    "model=claude-sonnet-5-5; arm=AG-c; condition=anon; experiments=8; "
    "fits=40; max_turns=100; wall_time_s=3600.0; harness=harness/1"
)
PILOT_DIGEST: Final = "1f88aa599e18490691a4dd12b42e61e9cc7c5730882a82bb701c5d2ad509668b"


def _key(arm: LLMArm, **overrides: Any) -> str:
    config = tiny_config(seeds=(1,), llm_arms=(arm,), **overrides)
    unit = Unit("hawkes", 1, arm.system(config.llm_letter))
    return str(session_key(unit, entry("hawkes"), environment(), config).digest)


AG_C_NAMED: Final = LLMArm("AG-c", "named")


# --------------------------------------------------------------------------
# The key moves with the surface
# --------------------------------------------------------------------------


def test_the_harness_version_is_2() -> None:
    assert HARNESS_VERSION == "harness/2"


def test_one_character_of_the_system_prompt_changes_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _key(AG_C_NAMED)
    text = prompts._KIND_TEXT[ChannelKind.SIGN]
    edited = text[:-1] + ("2" if text[-1] != "2" else "3")
    with monkeypatch.context() as m:
        m.setitem(prompts._KIND_TEXT, ChannelKind.SIGN, edited)
        after = _key(AG_C_NAMED)
    assert after != before
    assert _key(AG_C_NAMED) == before


def test_one_character_of_one_tool_schema_changes_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = environment()
    spec = env.investigation
    assert spec is not None

    def surface() -> dict[str, Any]:
        return rendered_config(
            spec, env.channels, "AG-c", "named", experiments=2, fits=3
        )

    before_key, before = _key(AG_C_NAMED), surface()
    # tools.py reads STOP_REASONS through its own binding (imported from prompts).
    reasons = (*prompts.STOP_REASONS[:-1], prompts.STOP_REASONS[-1][:-1] + "X")
    with monkeypatch.context() as m:
        m.setattr(tools, "STOP_REASONS", reasons)
        after_key, after = _key(AG_C_NAMED), surface()
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"tool:submit"}
    assert after_key != before_key
    assert _key(AG_C_NAMED) == before_key


def test_the_same_text_gives_the_same_key() -> None:
    assert _key(AG_C_NAMED) == _key(LLMArm("AG-c", "named"))
    assert _key(AG_C_NAMED) == _key(LLMArm("AG-c", "named", "v1", None))


def test_each_part_of_the_configuration_enters_the_key() -> None:
    keys = {
        "base": _key(AG_C_NAMED),
        "anon": _key(LLMArm("AG-c", "anon")),
        "AG-o": _key(LLMArm("AG-o", "named")),
        "link-neutral": _key(LLMArm("AG-c", "named", "link-neutral")),
        "policy": _key(LLMArm("AG-c", "named", policy="EIG")),
        "experiments": _key(AG_C_NAMED, experiments=3),
        "fits": _key(AG_C_NAMED, fits=4),
        "turns": _key(AG_C_NAMED, max_turns=21),
    }
    assert len(set(keys.values())) == len(keys), keys


def test_budgets_enter_the_rendered_surface_not_only_the_text() -> None:
    env = environment()
    assert env.investigation is not None
    a = rendered_config(
        env.investigation, env.channels, "AG-c", "named", experiments=2, fits=3
    )
    b = rendered_config(
        env.investigation, env.channels, "AG-c", "named", experiments=3, fits=3
    )
    assert a["system_prompt"] != b["system_prompt"]
    assert a["tool:run_experiment"] != b["tool:run_experiment"]


def test_the_session_text_names_the_surface_digest() -> None:
    config, env = tiny_config(), environment()
    text = session_text(AG_C_NAMED, config, env)
    assert "harness=harness/2" in text
    assert "prompt_variant=v1" in text and "policy=none" in text
    surface = text.rsplit("surface=", 1)[1]
    assert len(surface) == 64 and int(surface, 16) >= 0
    assert session_config_digest(AG_C_NAMED, config, env) == (
        hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def test_a_truth_whose_channels_differ_from_the_environment_is_refused() -> None:
    import dataclasses

    truth = entry("hawkes")
    other = dataclasses.replace(
        truth, spec=dataclasses.replace(truth.spec, channels=truth.spec.channels[:1])
    )
    config = tiny_config(seeds=(1,), llm_arms=(AG_C_NAMED,))
    unit = Unit("hawkes", 1, AG_C_NAMED.system(config.llm_letter))
    with pytest.raises(CampaignError, match="channels"):
        session_key(unit, other, environment(), config)


# --------------------------------------------------------------------------
# Arms and their units
# --------------------------------------------------------------------------


def test_default_arms_keep_their_pilot_names() -> None:
    assert [a.system("S") for a in PILOT_ARMS] == [
        "AG-c-S/named",
        "AG-o-S/named",
        "AG-c-S/anon",
        "AG-o-S/anon",
    ]


def test_variants_and_policies_never_share_a_unit() -> None:
    arms = (
        AG_C_NAMED,
        LLMArm("AG-c", "named", "link-neutral"),
        LLMArm("AG-c", "named", policy="EIG"),
        LLMArm("AG-c", "named", "link-neutral", "EIG"),
        LLMArm("AG-c", "named", policy="FSD"),
    )
    config = tiny_config(seeds=(1,), llm_arms=arms)
    systems = config.llm_systems()
    assert len(set(systems)) == len(arms)
    assert systems[1] == "AG-c-H/named+link-neutral"
    assert systems[2] == "AG-c-H/named+policy=EIG"
    assert [config.arm_of(s) for s in systems] == list(arms)
    keys = {
        session_key(Unit("hawkes", 1, s), entry("hawkes"), environment(), config)
        for s in systems
    }
    assert len(keys) == len(arms)


def test_bad_arms_are_refused() -> None:
    bad: Any = "v2"
    with pytest.raises(CampaignError):
        LLMArm("AG-c", "named", bad)
    for policy in ("", "a+b", "has space"):
        with pytest.raises(CampaignError):
            LLMArm("AG-c", "named", policy=policy)
    with pytest.raises(CampaignError):
        tiny_config(llm_arms=(AG_C_NAMED, LLMArm("AG-c", "named")))


# --------------------------------------------------------------------------
# The pilot's harness/1 sessions
# --------------------------------------------------------------------------


def test_the_legacy_text_is_the_pilot_text_byte_for_byte() -> None:
    arm = LLMArm("AG-c", "anon")
    assert legacy_session_text_v1(arm, CampaignConfig()) == PILOT_TEXT
    assert legacy_session_config_digest(arm, CampaignConfig()) == PILOT_DIGEST


def test_only_default_arms_have_a_legacy_digest() -> None:
    config = CampaignConfig()
    assert (
        legacy_session_config_digest(LLMArm("AG-c", "anon", "link-neutral"), config)
        is None
    )
    assert (
        legacy_session_config_digest(LLMArm("AG-c", "anon", policy="EIG"), config)
        is None
    )


def test_new_keys_never_equal_legacy_keys() -> None:
    config, env = CampaignConfig(), environment()
    for arm in PILOT_ARMS:
        new = session_config_digest(arm, config, env)
        assert new != legacy_session_config_digest(arm, config)
        assert "harness/1" not in session_text(arm, config, env)


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


SCRIPT: Final = (
    Step(
        calls=(
            (
                "belief",
                {"favoured": "null", "rivals": ["link=exp; Trend"], "confidence": 0.5},
            ),
        )
    ),
    Step(
        calls=(
            (
                "submit",
                {
                    "structure": "Excite(ExpK, One, all)",
                    "report": "self-exciting",
                    "stop_reason": "confident",
                },
            ),
        )
    ),
    Step(text="done"),
)


def _legacy_text(arm: LLMArm, config: CampaignConfig, env: Any = None) -> str:
    return legacy_session_text_v1(arm, config)


def test_a_pilot_format_harness_1_cell_is_found_and_rescored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pilot store's form, written by the driver as the P3 code wrote it
    (the legacy text, an older fitter), is found by rescore through the legacy
    digest and scored at a new address; the old cells stay byte-identical."""
    _clean(monkeypatch)
    scored: list[ScoreJob] = []

    def fake_score(job: ScoreJob) -> UnitResult:
        scored.append(job)
        return UnitResult(job.system, True, {"status": 1.0}, {"s": 1}, 0.0)

    monkeypatch.setattr(driver_module, "run_score_job", fake_score)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truth = entry("size_excitation")
    unit = Unit("size_excitation", 1, "AG-c-H/named")

    def campaign(**settings: Any) -> Campaign:
        return Campaign(
            tmp_path,
            environment(),
            config,
            [truth],
            DriverSettings(workers=0, poll_s=0.05, run_baselines=False, **settings),
        )

    with monkeypatch.context() as old:
        old.setattr(plan, "FIT_VERSION", "sciagent.glm.fit/4")
        old.setattr(plan, "session_text", _legacy_text)
        old.setattr(driver_module, "session_text", _legacy_text)
        first = campaign(driver_factory=lambda u: ScriptedDriver(SCRIPT))
        first.run()
        first.close()
        old_skey = session_key(unit, truth, environment(), config)
    assert len(scored) == 1
    cell = ResultStore(tmp_path).cell(old_skey.digest)
    assert cell["key"]["config"]["session_config"] == legacy_session_config_digest(
        AG_C_NAMED, config
    )
    assert cell["detail"]["session_config"].endswith("harness=harness/1")
    assert "fit/4" in cell["key"]["env_version"]
    # The current address is new: the normal plan would launch it again.
    assert session_key(unit, truth, environment(), config) != old_skey

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("rescore mode must never launch a session")

    monkeypatch.setattr(driver_module, "run_session", refuse)
    monkeypatch.setattr(driver_module, "run_session_isolated", refuse)
    store = ResultStore(tmp_path)
    found = stored_sessions(store, environment(), config, [truth])
    store.close()
    assert found[unit][0] == old_skey
    rescore = campaign(rescore_llm=True, driver_factory=refuse)
    rescore.run()
    assert rescore.store.done(score_key(unit, old_skey, environment(), config))
    rescore.close()
    assert len(scored) == 2


def _put(store: ResultStore, key: Any) -> None:
    store.put(key, {"status": 1.0}, {"cell": str(key.digest)[:8]})


def test_rescore_prefers_a_harness_2_session_and_keeps_variants_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    neutral = LLMArm("AG-c", "named", "link-neutral")
    config = tiny_config(seeds=(1,), llm_arms=(AG_C_NAMED, neutral))
    env, truth = environment(), entry("hawkes")
    v1_unit = Unit("hawkes", 1, AG_C_NAMED.system(config.llm_letter))
    neutral_unit = Unit("hawkes", 1, neutral.system(config.llm_letter))
    store = ResultStore(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(plan, "FIT_VERSION", "sciagent.glm.fit/4")
        m.setattr(plan, "session_text", _legacy_text)
        legacy = session_key(v1_unit, truth, env, config)
    _put(store, legacy)
    assert stored_sessions(store, env, config, [truth]) == {
        v1_unit: (legacy, store.cell(legacy.digest))
    }
    with monkeypatch.context() as m:
        m.setattr(plan, "FIT_VERSION", "sciagent.glm.fit/OLD2")
        newer = session_key(v1_unit, truth, env, config)
        neutral_key = session_key(neutral_unit, truth, env, config)
    _put(store, newer)
    _put(store, neutral_key)
    found = stored_sessions(store, env, config, [truth])
    assert found[v1_unit][0] == newer
    assert found[neutral_unit][0] == neutral_key
    # At the current address, that session wins over both.
    current = session_key(v1_unit, truth, env, config)
    _put(store, current)
    assert stored_sessions(store, env, config, [truth])[v1_unit][0] == current
    store.close()


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

_CHILD: Final = """
from campaign_support import entry, environment, tiny_config
from sciagent.campaign.plan import PILOT_ARMS, LLMArm, Unit, session_key

arms = (*PILOT_ARMS, LLMArm("AG-c", "anon", "link-neutral"))
config = tiny_config(seeds=(1,), llm_arms=arms)
truth, env = entry("hawkes"), environment()
for s in config.llm_systems():
    print(session_key(Unit("hawkes", 1, s), truth, env, config).digest)
"""


@pytest.mark.slow
def test_the_key_is_identical_across_processes() -> None:
    here = Path(__file__).parent
    outputs = []
    for hash_seed in ("0", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(here)}
        done = subprocess.run(
            [sys.executable, "-c", _CHILD],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        outputs.append(done.stdout.split())
    arms = (*PILOT_ARMS, LLMArm("AG-c", "anon", "link-neutral"))
    config = tiny_config(seeds=(1,), llm_arms=arms)
    mine = [
        str(
            session_key(
                Unit("hawkes", 1, s), entry("hawkes"), environment(), config
            ).digest
        )
        for s in config.llm_systems()
    ]
    assert outputs[0] == outputs[1] == mine
    assert len(set(mine)) == len(arms)
