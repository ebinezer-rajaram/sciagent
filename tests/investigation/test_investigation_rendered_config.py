"""The session surface rendered without a world (P4 Δ7).

:func:`~sciagent.investigation.runner.rendered_config` is the system prompt and
every tool schema for (channels, arm, condition, budgets, prompt variant),
rendered from configuration alone: no truth is resolved and no world is built.
It is what the campaign's session key hashes, so it must equal what a live lab
shows the agent (:func:`~sciagent.investigation.runner.rendered_surface` minus
the opening prompt, which depends on the observational log), and it must not
depend on the truth or the seed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Final

import pytest
from investigation_support import POINTPROC, lab

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.glm.interventions import DEFAULT_HORIZON, MAX_CLAMP_RATE, MAX_HORIZON
from sciagent.harness.record import canonical_json
from sciagent.investigation import EnvironmentSpec, runner, tools
from sciagent.investigation.prompts import Arm, PolicyName, PromptVariant
from sciagent.investigation.runner import leak_terms, rendered_config, rendered_surface
from sciagent.investigation.tools import LabError
from sciagent.investigation.view import Condition, make_view

IMAGE: Final = "sciagent-sandbox:unused"
#: Native-unit constants that must not survive into the anonymised view.
NATIVE_CONSTANTS: Final = (DEFAULT_HORIZON, MAX_HORIZON, MAX_CLAMP_RATE)


def _config(
    arm: Arm,
    condition: Condition,
    variant: PromptVariant = "v1",
    *,
    experiments: int = 3,
    fits: int = 4,
    env: EnvironmentSpec = POINTPROC,
) -> dict[str, Any]:
    return rendered_config(
        env,
        v2.CHANNELS,
        arm,
        condition,
        experiments=experiments,
        fits=fits,
        prompt_variant=variant,
    )


def _without_opening(surface: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in surface.items() if k != "opening_prompt"}


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
@pytest.mark.parametrize("condition", ["named", "anon"])
@pytest.mark.parametrize("variant", ["v1", "link-neutral"])
def test_the_rendered_config_is_what_a_live_lab_shows(
    arm: Arm,
    condition: Condition,
    variant: PromptVariant,
    tmp_path: Path,
) -> None:
    the_lab = lab(
        arm, condition, sandbox_root=tmp_path, image=IMAGE, prompt_variant=variant
    )
    shown = _without_opening(rendered_surface(the_lab))
    assert _config(arm, condition, variant) == shown
    assert "opening_prompt" not in _config(arm, condition, variant)
    assert set(shown) >= {"system_prompt", "tool:run_experiment", "tool:submit"}


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
def test_two_truths_and_seeds_render_the_same_surface(arm: Arm, tmp_path: Path) -> None:
    """Truth and seed never enter the surface: two truths of one environment,
    on two seeds, show the agent the same system prompt and schemas."""
    a = lab(arm, "anon", truth="hawkes", seed=1, sandbox_root=tmp_path, image=IMAGE)
    b = lab(
        arm,
        "anon",
        truth="size_excitation",
        seed=2,
        sandbox_root=tmp_path,
        image=IMAGE,
    )
    assert a.world.datasets["obs"].log.n != b.world.datasets["obs"].log.n
    assert _without_opening(rendered_surface(a)) == _without_opening(
        rendered_surface(b)
    )
    assert _without_opening(rendered_surface(a)) == _config(arm, "anon")


def test_rendering_needs_no_truth_and_no_world(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_truth(truth_id: str) -> Any:
        raise AssertionError(f"rendering resolved truth {truth_id!r}")

    class NoWorld:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("rendering built a world")

    monkeypatch.setattr(runner, "World", NoWorld)
    monkeypatch.setattr(tools, "World", NoWorld)
    env = EnvironmentSpec(
        POINTPROC.name, POINTPROC.anon_channels, POINTPROC.forbidden, no_truth
    )
    for arm in ("AG-c", "AG-o"):
        surface = _config(arm, "anon", env=env)
        assert surface["system_prompt"]


def test_the_parts_of_the_configuration_reach_the_surface() -> None:
    base = _config("AG-c", "named")
    assert _config("AG-c", "named") == base
    assert _config("AG-c", "anon") != base
    assert _config("AG-o", "named") != base
    assert _config("AG-c", "named", "link-neutral") != base
    assert _config("AG-c", "named", experiments=4) != base
    assert _config("AG-c", "named", fits=5) != base
    assert set(_config("AG-o", "named")) - set(base) == {"tool:notebook", "tool:python"}


def test_bad_configurations_are_refused() -> None:
    bad_arm: Any = "AG-x"
    bad_variant: Any = "v2"
    with pytest.raises(LabError):
        _config(bad_arm, "named")
    with pytest.raises(LabError):
        _config("AG-c", "named", bad_variant)
    with pytest.raises(LabError):
        _config("AG-c", "named", experiments=-1)


#: SHA-256 of the canonical JSON of the eight non-policy surfaces (experiments
#: 3, fits 4), taken before policy mode was added (P4 WP10, 2026-10-06):
#: policy mode is purely additive, so these must not move with it.
PRE_POLICY_DIGESTS: Final = {
    ("AG-c", "named", "v1"): (
        "719a2a156f14da3c16be5c11da6f432013cd1852d326c8cdc58b7ab10308b4c3"
    ),
    ("AG-c", "named", "link-neutral"): (
        "dc96a373f324105b208fc8ffe4ec94579af0a7b1fb9506d1fa69b27a4db10ef4"
    ),
    ("AG-c", "anon", "v1"): (
        "cfae4bfadbe3be6f71bce3746f0d339c2ef206e334fb330f7920c9db78603f4a"
    ),
    ("AG-c", "anon", "link-neutral"): (
        "aaf754a18a0e56194ceed2e1524d542d27f052ef48b74393a2dc88d6ba798031"
    ),
    ("AG-o", "named", "v1"): (
        "7b318c3a013e23dcae447930b7f4a62e053f509baccef25d27ca79c16de65380"
    ),
    ("AG-o", "named", "link-neutral"): (
        "27c7e3b302b8488070b720d58b1a94e4308ebe4dc56f7c4b80a1f9bf69f8e1e6"
    ),
    ("AG-o", "anon", "v1"): (
        "cb9e895a12c6f5a3d981f310f14ebacdde568fa052ea96c0c0a113c98087e47e"
    ),
    ("AG-o", "anon", "link-neutral"): (
        "7f1b4c799e0f25c2dc3965dbfe361f0a9ab132763d6fc0a1a3fbe0b0605275f6"
    ),
}


@pytest.mark.parametrize("combination", sorted(PRE_POLICY_DIGESTS))
def test_non_policy_surfaces_are_byte_identical_to_before_policy_mode(
    combination: tuple[Arm, Condition, PromptVariant],
) -> None:
    arm, condition, variant = combination
    surface = _config(arm, condition, variant)
    assert surface == rendered_config(
        POINTPROC,
        v2.CHANNELS,
        arm,
        condition,
        experiments=3,
        fits=4,
        prompt_variant=variant,
        policy=None,
    )
    got = hashlib.sha256(canonical_json(surface).encode("utf-8")).hexdigest()
    assert got == PRE_POLICY_DIGESTS[combination]


@pytest.mark.parametrize("condition", ["named", "anon"])
@pytest.mark.parametrize("variant", ["v1", "link-neutral"])
@pytest.mark.parametrize("policy", ["random", "EIG", "FSD"])
def test_the_rendered_config_is_what_a_live_policy_lab_shows(
    condition: Condition, variant: PromptVariant, policy: PolicyName
) -> None:
    the_lab = lab("AG-c", condition, prompt_variant=variant, policy=policy)
    shown = _without_opening(rendered_surface(the_lab))
    config = rendered_config(
        POINTPROC,
        v2.CHANNELS,
        "AG-c",
        condition,
        experiments=3,
        fits=4,
        prompt_variant=variant,
        policy=policy,
    )
    assert config == shown
    plain = _config("AG-c", condition, variant)
    assert config != plain
    assert set(config) == set(plain)
    changed = {k for k in config if config[k] != plain[k]}
    assert changed == {"system_prompt", "tool:run_experiment"}


def test_anonymised_policy_surface_does_not_leak() -> None:
    surface = rendered_surface(lab("AG-c", "anon", policy="EIG"))
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    assert_no_leaks(surface, terms, numeric_constants=NATIVE_CONSTANTS)


def test_policy_is_validated_where_the_surface_is_rendered() -> None:
    bad: Any = "greedy"
    with pytest.raises(LabError, match="policy"):
        rendered_config(
            POINTPROC, v2.CHANNELS, "AG-c", "named", experiments=3, fits=4, policy=bad
        )
    with pytest.raises(LabError, match="AG-c"):
        rendered_config(
            POINTPROC, v2.CHANNELS, "AG-o", "named", experiments=3, fits=4, policy="EIG"
        )
    view = make_view("named", v2.CHANNELS, POINTPROC.anon_channels)
    with pytest.raises(LabError, match="policy"):
        tools.tool_specs(view, "AG-c", experiments=3, fits=4, policy=bad)
