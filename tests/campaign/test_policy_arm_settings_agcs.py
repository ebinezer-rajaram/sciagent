"""The arm settings reach AG-c-S's policy (LOG 2026-10-28 and 2026-10-29).

``CampaignConfig.policy`` (:class:`~sciagent.campaign.plan.PolicyArmSettings`)
sets the policies' replicates, event cap and EIG weighting. The B-sym policy
arms read it (``build_policy``); an AG-c-S policy session must read the same
values, through the settings the campaign hands the session, the record that
replays it, and :func:`~sciagent.investigation.policy.make_policy`. Its session
key names them, so a change moves the address. The defaults stay the core's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import llm
from sciagent.campaign.plan import (
    LLMArm,
    PolicyArmSettings,
    Unit,
    session_config_digest,
    session_policy_settings,
)
from sciagent.investigation.policy import (
    DEFAULT_SETTINGS,
    PolicySettings,
    make_policy,
)
from sciagent.investigation.prompts import as_policy
from sciagent.systems.v2.policies import (
    BIC_WEIGHTS,
    POLICY_MAX_EVENTS,
    REPLICATES,
    EIGPolicy,
    FSDPolicy,
    tempered,
)

AG_C_FSD = LLMArm("AG-c", "named", policy="FSD")
AG_C_EIG = LLMArm("AG-c", "named", policy="EIG")
CHANGED = PolicyArmSettings(max_events=1_234, weighting=tempered(2.0))


class _Captured(Exception):
    """Stops ``run`` once its arguments are seen."""


def _session_settings(
    arm: LLMArm, settings: PolicyArmSettings, tmp_path: Path, monkeypatch: Any
) -> PolicySettings:
    """The policy settings the campaign hands an AG-c-S session's ``run``."""
    seen: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Captured

    monkeypatch.setattr(llm, "run", fake_run)
    config = tiny_config(seeds=(1,), llm_arms=(arm,), policy=settings)
    unit = Unit("hawkes", 1, arm.system(config.llm_letter))
    with pytest.raises(_Captured):
        llm.run_session(unit, entry("hawkes"), environment(), config, tmp_path)
    out = seen["policy_settings"]
    assert isinstance(out, PolicySettings)
    return out


def test_the_defaults_are_the_cores() -> None:
    assert DEFAULT_SETTINGS.max_events == POLICY_MAX_EVENTS == 15_000
    assert DEFAULT_SETTINGS.weighting == BIC_WEIGHTS
    assert DEFAULT_SETTINGS.replicates == REPLICATES
    assert session_policy_settings(PolicyArmSettings()) == DEFAULT_SETTINGS


def test_make_policy_builds_with_the_cap_and_weighting() -> None:
    settings = PolicySettings(max_events=1_234, weighting=tempered(2.0))
    fsd = make_policy("FSD", 7, settings)
    eig = make_policy("EIG", 7, settings)
    assert isinstance(fsd, FSDPolicy) and isinstance(eig, EIGPolicy)
    assert fsd.max_events == 1_234
    assert eig.max_events == 1_234
    assert eig.weighting == tempered(2.0)


def test_the_settings_round_trip_through_the_record() -> None:
    settings = PolicySettings(replicates=4, max_events=1_234, weighting=tempered(2.0))
    assert PolicySettings.from_json(settings.as_json()) == settings


@pytest.mark.parametrize("arm", [AG_C_FSD, AG_C_EIG], ids=["FSD", "EIG"])
def test_an_ag_c_s_session_gets_the_arm_settings(
    arm: LLMArm, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _session_settings(arm, CHANGED, tmp_path, monkeypatch)
    name = as_policy(arm.policy)
    assert name is not None
    policy = make_policy(name, 1, settings)
    assert isinstance(policy, FSDPolicy | EIGPolicy)
    assert policy.max_events == 1_234
    if isinstance(policy, EIGPolicy):
        assert policy.weighting == tempered(2.0)


def test_a_non_policy_session_gets_no_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Captured

    monkeypatch.setattr(llm, "run", fake_run)
    arm = LLMArm("AG-c", "named")
    config = tiny_config(seeds=(1,), llm_arms=(arm,), policy=CHANGED)
    unit = Unit("hawkes", 1, arm.system(config.llm_letter))
    with pytest.raises(_Captured):
        llm.run_session(unit, entry("hawkes"), environment(), config, tmp_path)
    assert seen["policy_settings"] is None


def test_the_session_key_names_the_arm_settings() -> None:
    env = environment()
    base = tiny_config(llm_arms=(AG_C_FSD, AG_C_EIG))
    cap = tiny_config(
        llm_arms=(AG_C_FSD, AG_C_EIG), policy=PolicyArmSettings(max_events=1_234)
    )
    weights = tiny_config(
        llm_arms=(AG_C_FSD, AG_C_EIG),
        policy=PolicyArmSettings(weighting=tempered(2.0)),
    )
    for arm in (AG_C_FSD, AG_C_EIG):
        before = session_config_digest(arm, base, env)
        assert session_config_digest(arm, cap, env) != before
    assert session_config_digest(AG_C_EIG, weights, env) != session_config_digest(
        AG_C_EIG, base, env
    )
