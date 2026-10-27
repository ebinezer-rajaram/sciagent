"""An AG-o session's address pins its sandbox; an AG-c session's does not move.

The session key is how ``scripts/pilot.py`` decides a stored ``llm-session``
cell can be reused. An AG-o session ran its code under the sandbox's limits,
its handoff helper (in ``src/``, outside the image) and its image, so a change
to any of them must give a new address. AG-c has no sandbox: its session text,
and so every stored AG-c address, stays byte-identical.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import pytest
from campaign_support import entry, environment, resolve, tiny_config

from sciagent.campaign import plan
from sciagent.campaign.plan import (
    CampaignConfig,
    LLMArm,
    Unit,
    session_config_digest,
    session_key,
    session_sandbox_text,
    session_text,
)
from sciagent.investigation import runner as investigation_runner
from sciagent.investigation.runner import (
    EnvironmentSpec,
    InvestigationSpec,
    run_sandbox_limits,
    sandbox_json,
)
from sciagent.sandbox import HANDOFF_HELPER_SHA256, SandboxLimits, image_tag
from sciagent.sandbox.image import SANDBOX_CONTEXT

AG_C = LLMArm("AG-c", "named")
AG_C_ANON = LLMArm("AG-c", "anon")
AG_O = LLMArm("AG-o", "named")
AG_O_ANON = LLMArm("AG-o", "anon")


def _limits() -> SandboxLimits:
    return run_sandbox_limits()


#: A valid value different from the default, for every SandboxLimits field.
_ALTERED: dict[str, Any] = {
    "wall_seconds": 61.0,
    "memory_mb": 2049,
    "cpus": 2.0,
    "pids": 257,
    "output_bytes": 20_001,
    "max_file_mb": 257,
    "max_work_entries": 100_001,
    "max_work_path_bytes": 1023,
}


def test_every_sandbox_limits_field_has_an_altered_value() -> None:
    """The parametrisation below covers every field, including future ones."""
    assert sorted(f.name for f in dataclasses.fields(SandboxLimits)) == sorted(_ALTERED)


# --------------------------------------------------------------------------
# AG-c: byte-identical to the text before the sandbox entered the key
# --------------------------------------------------------------------------


def test_an_ag_c_session_text_is_unchanged() -> None:
    config = CampaignConfig()
    assert session_text(AG_C, config) == (
        "model=claude-sonnet-5-5; arm=AG-c; condition=named; experiments=8; "
        "fits=40; max_turns=100; wall_time_s=3600.0; harness=harness/1"
    )
    assert session_config_digest(AG_C, config) == (
        "33163b9115947c622ab34f4329536ea3fd54dbbd0599827e7b528e6a7db989fb"
    )
    assert session_config_digest(AG_C_ANON, config) == (
        "1f88aa599e18490691a4dd12b42e61e9cc7c5730882a82bb701c5d2ad509668b"
    )


# --------------------------------------------------------------------------
# AG-o: the sandbox text
# --------------------------------------------------------------------------


def test_an_ag_o_session_text_carries_the_sandbox_the_runner_uses() -> None:
    config = CampaignConfig()
    text = session_text(AG_O, config)
    prefix = (
        "model=claude-sonnet-5-5; arm=AG-o; condition=named; experiments=8; "
        "fits=40; max_turns=100; wall_time_s=3600.0; harness=harness/1; "
    )
    assert text.startswith(prefix)
    sandbox = session_sandbox_text(
        run_sandbox_limits(), HANDOFF_HELPER_SHA256, image_tag(SANDBOX_CONTEXT)
    )
    assert text == prefix + sandbox
    assert HANDOFF_HELPER_SHA256 in sandbox
    assert image_tag(SANDBOX_CONTEXT) in sandbox
    for value in dataclasses.asdict(run_sandbox_limits()).values():
        assert repr(value) in sandbox


def test_the_sandbox_text_is_a_fixed_rendering() -> None:
    """Pinned bytes: a reordering or reformatting would move every AG-o key."""
    text = session_sandbox_text(SandboxLimits(), "ab" * 32, "sciagent-sandbox:0123")
    assert text == (
        'sandbox={"handoff_helper_sha256":"' + "ab" * 32 + '","limits":'
        '{"cpus":1.0,"max_file_mb":256,"max_work_entries":100000,'
        '"max_work_path_bytes":1024,"memory_mb":2048,"output_bytes":20000,'
        '"pids":256,"wall_seconds":60}}; image=sciagent-sandbox:0123'
    )


@pytest.mark.parametrize("name", sorted(_ALTERED))
def test_changing_any_limit_changes_the_sandbox_text(name: str) -> None:
    base = session_sandbox_text(_limits(), HANDOFF_HELPER_SHA256, "img:1")
    other = dataclasses.replace(_limits(), **{name: _ALTERED[name]})
    assert session_sandbox_text(other, HANDOFF_HELPER_SHA256, "img:1") != base


def test_changing_the_helper_or_the_image_changes_the_sandbox_text() -> None:
    base = session_sandbox_text(_limits(), HANDOFF_HELPER_SHA256, "img:1")
    assert session_sandbox_text(_limits(), "0" * 64, "img:1") != base
    assert session_sandbox_text(_limits(), HANDOFF_HELPER_SHA256, "img:2") != base


# --------------------------------------------------------------------------
# The session key, end to end
# --------------------------------------------------------------------------


def _keys(config: CampaignConfig) -> tuple[str, str, str, str]:
    env, truth = environment(), entry("hawkes")
    out = []
    for arm in (AG_C, AG_C_ANON, AG_O, AG_O_ANON):
        unit = Unit("hawkes", 1, arm.system(config.llm_letter))
        out.append(str(session_key(unit, truth, env, config).digest))
    return out[0], out[1], out[2], out[3]


@pytest.mark.parametrize("name", sorted(_ALTERED))
def test_a_limit_change_moves_the_ag_o_key_only(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tiny_config()
    before = _keys(config)
    altered = dataclasses.replace(run_sandbox_limits(), **{name: _ALTERED[name]})
    monkeypatch.setattr(plan, "run_sandbox_limits", lambda: altered)
    after = _keys(config)
    assert after[:2] == before[:2]
    assert after[2] != before[2] and after[3] != before[3]


def test_a_runner_default_change_moves_the_ag_o_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key reads the runner's own defaults, not a copy of them."""
    config = tiny_config()
    before = _keys(config)
    monkeypatch.setattr(investigation_runner, "SANDBOX_MEMORY_MB", 4096)
    after = _keys(config)
    assert after[:2] == before[:2]
    assert after[2] != before[2] and after[3] != before[3]


def test_a_helper_change_moves_the_ag_o_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tiny_config()
    before = _keys(config)
    monkeypatch.setattr(plan, "HANDOFF_HELPER_SHA256", "0" * 64)
    after = _keys(config)
    assert after[:2] == before[:2]
    assert after[2] != before[2] and after[3] != before[3]


def test_an_image_change_moves_the_ag_o_key_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed build context (no Docker involved) gives a new AG-o key."""
    config = tiny_config()
    before = _keys(config)
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    for name in ("Dockerfile", "requirements.lock"):
        (ctx / name).write_bytes((SANDBOX_CONTEXT / name).read_bytes())
    monkeypatch.setattr(plan, "SANDBOX_CONTEXT", ctx)
    assert _keys(config) == before
    with (ctx / "requirements.lock").open("ab") as f:
        f.write(b"# changed\n")
    after = _keys(config)
    assert after[:2] == before[:2]
    assert after[2] != before[2] and after[3] != before[3]


def test_the_session_key_is_deterministic() -> None:
    config = tiny_config()
    assert _keys(config) == _keys(config)
    assert session_text(AG_O, config) == session_text(AG_O, config)
    digest = hashlib.sha256(session_text(AG_O, config).encode()).hexdigest()
    assert session_config_digest(AG_O, config) == digest


# --------------------------------------------------------------------------
# The limits in the key are the limits the runner builds
# --------------------------------------------------------------------------


class _Stop(Exception):
    """Raised by the patched ``build_lab`` once it has seen the spec."""


def test_run_builds_the_sandbox_the_key_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[InvestigationSpec] = []

    def capture(spec: InvestigationSpec, *args: object, **kwargs: object) -> None:
        seen.append(spec)
        raise _Stop

    monkeypatch.setattr(investigation_runner, "build_lab", capture)
    env = EnvironmentSpec(
        name="pointproc", anon_channels={}, forbidden=(), resolve=resolve
    )
    with pytest.raises(_Stop):
        investigation_runner.run(
            env, "t", "AG-o", "named", "m", 7, tmp_path / "out", driver=None
        )
    (spec,) = seen
    assert spec.sandbox_limits() == run_sandbox_limits()
    assert spec.sandbox_json() == sandbox_json(
        run_sandbox_limits(), HANDOFF_HELPER_SHA256
    )
