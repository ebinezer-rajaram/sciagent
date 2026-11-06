"""The prompt states the session's wall limit (LOG 2026-11-05, session 34).

``wall_time_s`` was in the config and in no prompt, and on the pilot it bound 3 of
240 sessions, each ending with no submission. P4's prompt, the ``link-neutral``
variant, states it: the same sentence in both arms, every condition and with or
without a policy, and its number is the session's own configuration. No signature
here has a default for it, so no text can carry a number nobody passed. The
pilot's variant, ``v1``, stays as it was and states no limit.

The limit is real seconds, which is not a time of the process, so neither the
rescaled nor the anonymised view rescales it: that is the one number in the prompt
the time factor leaves alone, and the reason every condition reads the same
sentence.
"""

from __future__ import annotations

import inspect
import itertools
import re
from pathlib import Path
from typing import Final

import pytest
from investigation_support import (
    BELIEF,
    POINTPROC,
    STOP,
    WALL_SENTENCE,
    clean_environment,
    lab,
    scripted_run,
)

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation.prompts import Arm, PromptVariant, system_prompt
from sciagent.investigation.runner import (
    leak_terms,
    rendered_config,
    rendered_surface,
)
from sciagent.investigation.view import CONDITIONS, Condition

SENTENCE: Final = WALL_SENTENCE
FOUND: Final = re.compile(r"The whole investigation has a limit.*?submitted\.", re.S)
ARMS: Final[tuple[Arm, ...]] = ("AG-c", "AG-o")
#: The variant that states the limit.
P4: Final[PromptVariant] = "link-neutral"


def _prompt(
    arm: Arm,
    condition: Condition,
    variant: PromptVariant,
    wall: float,
    policy: str | None = None,
) -> str:
    surface = rendered_config(
        POINTPROC,
        v2.CHANNELS,
        arm,
        condition,
        experiments=8,
        fits=40,
        wall_time_s=wall,
        prompt_variant=variant,
        policy=policy,
    )
    text = surface["system_prompt"]
    assert isinstance(text, str)
    return text


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("condition", CONDITIONS)
def test_the_prompt_states_the_limit_once(arm: Arm, condition: Condition) -> None:
    text = _prompt(arm, condition, P4, 3600.0)
    assert FOUND.findall(text) == [SENTENCE.format(wall="3600")]


@pytest.mark.parametrize("condition", CONDITIONS)
@pytest.mark.parametrize(
    ("arm", "policy"), [("AG-c", None), ("AG-o", None), ("AG-c", "random")]
)
def test_the_pilots_variant_states_no_limit(
    arm: Arm, condition: Condition, policy: str | None
) -> None:
    text = _prompt(arm, condition, "v1", 3600.0, policy)
    assert FOUND.findall(text) == []
    assert "real time" not in text
    assert text == _prompt(arm, condition, "v1", 600.0, policy)


def test_the_words_are_the_same_for_every_arm_condition_and_policy() -> None:
    plain = [
        _prompt(arm, condition, P4, 3600.0)
        for arm, condition in itertools.product(ARMS, CONDITIONS)
    ]
    policies = [
        _prompt("AG-c", condition, P4, 3600.0, policy)
        for condition, policy in itertools.product(CONDITIONS, ("random", "EIG", "FSD"))
    ]
    found = {sentence for t in (*plain, *policies) for sentence in FOUND.findall(t)}
    assert found == {SENTENCE.format(wall="3600")}


@pytest.mark.parametrize("wall", [600.0, 1234.5, 7200.0])
def test_the_number_is_the_configurations(wall: float) -> None:
    text = _prompt("AG-c", "anon", "link-neutral", wall)
    assert f"limit of {wall:g} seconds" in text
    assert "3600" not in text


def test_the_limit_changes_that_sentence_and_nothing_else() -> None:
    a = _prompt("AG-o", "anon", "link-neutral", 3600.0)
    b = _prompt("AG-o", "anon", "link-neutral", 600.0)
    assert a != b
    assert FOUND.sub("", a) == FOUND.sub("", b)


@pytest.mark.parametrize("function", [system_prompt, rendered_config, rendered_surface])
def test_no_signature_can_supply_the_number_for_the_caller(function: object) -> None:
    assert callable(function)
    parameter = inspect.signature(function).parameters["wall_time_s"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("arm", ARMS)
def test_the_sentence_leaks_nothing(arm: Arm) -> None:
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    sentence = FOUND.findall(_prompt(arm, "anon", "link-neutral", 3600.0))
    assert_no_leaks({"sentence": sentence[0]}, terms)


@pytest.mark.parametrize("wall", [600.0, 900.0])
def test_a_session_is_told_the_limit_it_runs_under(
    wall: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: the record's config, the prompt it holds, and what the
    configuration alone renders are one number and one text."""
    clean_environment(monkeypatch)
    submit = {"structure": "null", "report": "probe", **STOP}
    script = [Step(calls=(BELIEF, ("submit", submit)))]
    outcome = scripted_run(
        script, tmp_path, condition="anon", wall_time_s=wall, prompt_variant=P4
    )
    config = SessionRecord.load(outcome.record_path).of_kind("config")[0].body
    assert config["wall_time_s"] == wall
    assert f"limit of {wall:g} seconds" in config["system_prompt"]
    surface = rendered_config(
        POINTPROC,
        v2.CHANNELS,
        "AG-c",
        "anon",
        experiments=3,
        fits=4,
        wall_time_s=wall,
        prompt_variant=P4,
    )
    assert config["system_prompt"] == surface["system_prompt"]


def test_a_live_lab_shows_the_limit_it_was_given() -> None:
    the_lab = lab("AG-c", "anon", prompt_variant=P4)
    surface = rendered_surface(the_lab, wall_time_s=900.0)
    assert "limit of 900 seconds" in surface["system_prompt"]
