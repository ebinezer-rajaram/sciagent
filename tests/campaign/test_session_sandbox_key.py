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
from sciagent.campaign.audit import check_sessions
from sciagent.campaign.plan import (
    CampaignConfig,
    CampaignError,
    LLMArm,
    Unit,
    session_config_digest,
    session_key,
    session_sandbox_text,
    session_text,
)
from sciagent.campaign.store import ResultStore
from sciagent.investigation import runner as investigation_runner
from sciagent.investigation import tools as investigation_tools
from sciagent.investigation.runner import (
    EnvironmentSpec,
    InvestigationSpec,
    run_sandbox_limits,
    sandbox_json,
)
from sciagent.sandbox import (
    HANDOFF_HELPER_SHA256,
    SandboxLimits,
    SandboxResult,
    ensure_image,
    image_tag,
)
from sciagent.sandbox import dataset as sandbox_dataset
from sciagent.sandbox import runner as sandbox_runner
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
    assert text.startswith('sandbox={"data_format_sha256":"')
    assert (
        '"handoff_helper_sha256":"' + "ab" * 32 + '","limits":'
        '{"cpus":1.0,"max_file_mb":256,"max_work_entries":100000,'
        '"max_work_path_bytes":1024,"memory_mb":2048,"output_bytes":20000,'
        '"pids":256,"wall_seconds":60.0},"python_tool":{"max_code":50000,'
        '"max_notebook_append":20000,"output_format_sha256":'
    ) in text
    assert '"run_argv":["docker","run","--rm","-i","--init",' in text
    assert text.endswith('"python","-B","-"]}; image=sciagent-sandbox:0123')
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "8b4e45ef1879f8679b96fec91d9f51b0e0144d2742a3a94b6e8f88de568930dc"
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

    # The same with limits other than the spec's defaults: a run() that
    # ignored run_sandbox_limits() would pass the lines above, not these.
    altered = SandboxLimits(wall_seconds=61.0, memory_mb=4096)
    monkeypatch.setattr(investigation_runner, "run_sandbox_limits", lambda: altered)
    with pytest.raises(_Stop):
        investigation_runner.run(
            env, "t", "AG-o", "named", "m", 7, tmp_path / "out", driver=None
        )
    assert seen[-1].sandbox_limits() == altered


def _env() -> EnvironmentSpec:
    return EnvironmentSpec(
        name="pointproc", anon_channels={}, forbidden=(), resolve=resolve
    )


def test_build_lab_gives_the_sandbox_the_spec_limits(tmp_path: Path) -> None:
    spec = InvestigationSpec(
        environment="pointproc",
        truth_id="hawkes",
        arm="AG-o",
        condition="named",
        seed=3,
        sandbox_wall_seconds=61.0,
        sandbox_memory_mb=4096,
    )
    lab = investigation_runner.build_lab(
        spec, _env(), sandbox_root=tmp_path, image="img:1"
    )
    assert lab.sandbox is not None
    assert lab.sandbox.limits == spec.sandbox_limits()


def _fake_docker(tmp_path: Path) -> Path:
    """A ``docker`` that answers ``version`` and ``image inspect`` and nothing
    else (so the image counts as present and nothing is built)."""
    script = tmp_path / "docker"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = version ]; then echo 99.0; exit 0; fi\n'
        'if [ "$1" = image ] && [ "$2" = inspect ]; then '
        "echo sha256:0123; exit 0; fi\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    return script


def test_the_image_build_lab_uses_is_the_one_the_key_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = str(_fake_docker(tmp_path))
    real = ensure_image
    returned: list[str] = []

    def ensure() -> str:
        returned.append(real(docker=docker))
        return returned[-1]

    monkeypatch.setattr(investigation_runner, "ensure_image", ensure)
    spec = InvestigationSpec(
        environment="pointproc",
        truth_id="hawkes",
        arm="AG-o",
        condition="named",
        seed=3,
    )
    lab = investigation_runner.build_lab(spec, _env(), sandbox_root=tmp_path / "r")
    assert lab.sandbox is not None
    assert returned == [lab.sandbox.image]
    text = session_text(AG_O, CampaignConfig())
    assert text.endswith(f"; image={lab.sandbox.image}")


# --------------------------------------------------------------------------
# The key names what the agent can do or see (red-team finding 1)
# --------------------------------------------------------------------------

_real_build_run_argv = sandbox_runner.build_run_argv
_real_environment = sandbox_runner._environment


def _replace_in_argv(old: str, new: str) -> Any:
    def patched(**kwargs: Any) -> list[str]:
        return [a.replace(old, new) for a in _real_build_run_argv(**kwargs)]

    return patched


def _replace_in_environment(name: str, new: str) -> Any:
    def patched(seed: int) -> list[tuple[str, str]]:
        return [(k, new if k == name else v) for k, v in _real_environment(seed)]

    return patched


def _hex_seed(seed: int) -> list[tuple[str, str]]:
    return [
        (k, hex(seed) if k == "SANDBOX_SEED" else v) for k, v in _real_environment(seed)
    ]


def _six_digit_csv(columns: list[tuple[str, Any]]) -> bytes:
    lines = [",".join(name for name, _ in columns)]
    for i in range(columns[0][1].size):
        lines.append(",".join(f"{float(col[i]):.6g}" for _, col in columns))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _other_python_text(result: SandboxResult) -> str:
    return f"exit {result.exit_code}\n{result.stdout}{result.stderr}"


#: name -> (module, attribute, replacement): each a change to what the agent's
#: code can do or what the agent sees, made where the real code lives.
_MUTATIONS: dict[str, tuple[Any, str, Any]] = {
    "tmpfs size": (
        sandbox_runner,
        "_TMPFS",
        "/tmp:rw,noexec,nosuid,nodev,size=65m",
    ),
    "network": (sandbox_runner, "build_run_argv", _replace_in_argv("none", "host")),
    "nofile": (
        sandbox_runner,
        "build_run_argv",
        _replace_in_argv("nofile=1024:1024", "nofile=4096:4096"),
    ),
    "threads": (
        sandbox_runner,
        "_environment",
        _replace_in_environment("OMP_NUM_THREADS", "2"),
    ),
    "hashseed": (
        sandbox_runner,
        "_environment",
        _replace_in_environment("PYTHONHASHSEED", "1"),
    ),
    "tz": (sandbox_runner, "_environment", _replace_in_environment("TZ", "CET")),
    "locale": (sandbox_runner, "_environment", _replace_in_environment("LC_ALL", "C")),
    "sandbox seed": (sandbox_runner, "_environment", _hex_seed),
    "csv precision": (sandbox_dataset, "_csv_bytes", _six_digit_csv),
    "code cap": (investigation_tools, "_MAX_CODE", 50_001),
    "notebook cap": (investigation_tools, "_MAX_NOTEBOOK_APPEND", 20_001),
    "python output": (investigation_tools, "python_tool_text", _other_python_text),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_what_the_agent_can_do_or_see_moves_the_ag_o_key_only(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tiny_config()
    before = _keys(config)
    module, attribute, replacement = _MUTATIONS[name]
    monkeypatch.setattr(module, attribute, replacement)
    after = _keys(config)
    assert after[:2] == before[:2]
    assert after[2] != before[2] and after[3] != before[3]


def test_the_python_tool_shows_python_tool_text() -> None:
    """The output format the key samples is the format the tool returns."""
    result = SandboxResult("out\n", "err\n", 1, True, False, 0.5)
    assert investigation_tools.python_tool_text(result) == (
        "exit code 1 (stopped: time limit)\n--- stdout ---\nout\n"
        "\n--- stderr ---\nerr\n"
    )
    quiet = SandboxResult("x", "", 0, False, False, 0.1)
    assert investigation_tools.python_tool_text(quiet) == (
        "exit code 0\n--- stdout ---\nx"
    )


# --------------------------------------------------------------------------
# Float versus int (red-team finding 5)
# --------------------------------------------------------------------------


def test_an_integral_float_limit_renders_like_its_int() -> None:
    a = session_sandbox_text(SandboxLimits(wall_seconds=60, cpus=1), "ab" * 32, "i:1")
    b = session_sandbox_text(
        SandboxLimits(wall_seconds=60.0, cpus=1.0), "ab" * 32, "i:1"
    )
    assert a == b
    assert '"wall_seconds":60.0' in a


# --------------------------------------------------------------------------
# A broken build context is a CampaignError (red-team finding 4)
# --------------------------------------------------------------------------


def test_a_broken_build_context_is_a_campaign_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan, "SANDBOX_CONTEXT", tmp_path)
    assert session_text(AG_C, CampaignConfig())
    with pytest.raises(CampaignError, match="sandbox image"):
        session_text(AG_O, CampaignConfig())


def test_the_session_check_reports_a_broken_build_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan, "SANDBOX_CONTEXT", tmp_path / "ctx")
    config = tiny_config(seeds=(1,), llm_arms=(AG_O,))
    with ResultStore(tmp_path / "store") as store:
        check = check_sessions(store, environment(), config, [entry("hawkes")])
    assert [f.kind for f in check.issues] == ["session_config"]
    assert "sandbox image" in check.issues[0].message
