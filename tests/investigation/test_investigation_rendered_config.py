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

from pathlib import Path
from typing import Any, Final

import pytest
from investigation_support import POINTPROC, lab

from environments.pointproc import v2
from sciagent.investigation import EnvironmentSpec, runner, tools
from sciagent.investigation.prompts import Arm, PromptVariant
from sciagent.investigation.runner import rendered_config, rendered_surface
from sciagent.investigation.tools import LabError
from sciagent.investigation.view import Condition

IMAGE: Final = "sciagent-sandbox:unused"


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


def test_a_policy_is_refused_until_policy_arms_exist(tmp_path: Path) -> None:
    """``run`` takes the arm's policy (P4 Δ7 plumbing for WP10) but no policy
    mode exists yet: it refuses before any world, lab or record is made."""
    with pytest.raises(LabError, match="policy"):
        runner.run(
            POINTPROC,
            "hawkes",
            "AG-c",
            "named",
            "unused-model",
            1,
            tmp_path / "out",
            policy="EIG",
        )
    assert not (tmp_path / "out").exists()
