"""Prompt variants (P4 Δ6): ``"v1"`` and ``"link-neutral"``.

The link-neutral variant differs from v1 in exactly two places: the grammar's
examples (rotated across the identity, exp and softplus links, plus one
depth-3 example) and the scoring paragraph (restated as held-out prediction
and structural distance). Everything else is the same text. The variant is
part of the recorded :class:`InvestigationSpec`; a record made before it
existed reads as v1.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest
from investigation_support import (
    BELIEF,
    POINTPROC,
    STOP,
    clean_environment,
    lab,
    scripted_run,
)

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.core.errors import SciAgentError
from sciagent.glm.grammar import MAX_DEPTH, Link, depth
from sciagent.glm.interventions import DEFAULT_HORIZON, MAX_CLAMP_RATE, MAX_HORIZON
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation import InvestigationSpec, Lab
from sciagent.investigation.prompts import (
    PROMPT_VARIANTS,
    Arm,
    PromptVariant,
    examples,
    system_prompt,
)
from sciagent.investigation.runner import leak_terms, rendered_surface
from sciagent.investigation.tools import LabError
from sciagent.investigation.view import Condition

V1_SCORING: Final = """\
You are scored after you submit, on fresh data you never see: how well your
submitted structure, fitted by the framework, predicts that data, and how close
it is to the true mechanism. A simpler structure that is right beats a larger
one that merely fits."""

LINK_NEUTRAL_SCORING: Final = """\
You are scored after you submit on held-out prediction and structural
distance: how well your submitted structure, fitted by the framework, predicts
fresh data you never see, and how close its structure is to the true mechanism."""

IMAGE: Final = "sciagent-sandbox:unused"
#: Native-unit constants that must not survive into the anonymised view.
NATIVE_CONSTANTS: Final = (DEFAULT_HORIZON, MAX_HORIZON, MAX_CLAMP_RATE)


def test_the_variants_are_v1_and_link_neutral() -> None:
    assert PROMPT_VARIANTS == ("v1", "link-neutral")


# --------------------------------------------------------------------------
# The recorded spec
# --------------------------------------------------------------------------


def _spec(prompt_variant: PromptVariant = "v1") -> InvestigationSpec:
    return InvestigationSpec(
        POINTPROC.name, "hawkes", "AG-c", "named", 1, prompt_variant=prompt_variant
    )


def test_the_variant_is_recorded_and_read_back() -> None:
    assert _spec().prompt_variant == "v1"
    assert _spec().as_json()["prompt_variant"] == "v1"
    neutral = _spec(prompt_variant="link-neutral")
    data = neutral.as_json()
    assert data["prompt_variant"] == "link-neutral"
    assert InvestigationSpec.from_json(data) == neutral


def test_a_record_from_before_the_variant_reads_as_v1() -> None:
    data = _spec().as_json()
    del data["prompt_variant"]
    assert InvestigationSpec.from_json(data).prompt_variant == "v1"


def test_an_unknown_variant_is_refused() -> None:
    data = {**_spec().as_json(), "prompt_variant": "v2"}
    with pytest.raises(LabError, match="prompt variant"):
        InvestigationSpec.from_json(data)
    with pytest.raises(LabError, match="prompt variant"):
        the_lab = lab("AG-c")
        Lab(
            the_lab.world,
            the_lab.view,
            "AG-c",
            experiments=1,
            fits=1,
            prompt_variant="v2",
        )
    the_lab = lab("AG-c")
    with pytest.raises(SciAgentError, match="prompt variant"):
        system_prompt(the_lab.view, "AG-c", experiments=8, fits=40, variant="neutral")


# --------------------------------------------------------------------------
# The examples
# --------------------------------------------------------------------------


@pytest.mark.parametrize("condition", ["named", "anon"])
@pytest.mark.parametrize("variant", ["v1", "link-neutral"])
def test_every_example_parses(condition: Condition, variant: PromptVariant) -> None:
    view = lab("AG-c", condition).view
    for text in examples(view, variant):
        view.parse(text)


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_link_neutral_has_one_depth_3_example(condition: Condition) -> None:
    view = lab("AG-c", condition).view
    assert MAX_DEPTH == 3
    depths = [
        max(depth(f) for f in view.parse(t).features)
        for t in examples(view, "link-neutral")
    ]
    assert depths.count(MAX_DEPTH) == 1
    v1_depths = [
        max(depth(f) for f in view.parse(t).features) for t in examples(view, "v1")
    ]
    assert MAX_DEPTH not in v1_depths


def test_link_neutral_examples_rotate_the_links() -> None:
    view = lab("AG-c").view
    neutral = [view.parse(t).link for t in examples(view, "link-neutral")]
    assert set(neutral) == {Link.IDENTITY, Link.EXP, Link.SOFTPLUS}
    assert neutral[0] is not Link.IDENTITY
    v1 = [view.parse(t).link for t in examples(view, "v1")]
    assert v1[0] is Link.IDENTITY and Link.SOFTPLUS not in v1


# --------------------------------------------------------------------------
# The two variants differ in those two places only
# --------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return text.split()


#: The grammar's example block: between "Examples:" and the grids.
_EXAMPLES: Final = re.compile(r"Examples:\n\n(.*?)\n\nShape-parameter grids", re.S)


def _cut(prompt: str, scoring: str) -> tuple[str, str]:
    """``prompt`` with its example block and scoring paragraph replaced by
    placeholders, and the example block."""
    assert prompt.count(scoring) == 1
    match = _EXAMPLES.search(prompt)
    assert match is not None
    rest = prompt[: match.start(1)] + "<EXAMPLES>" + prompt[match.end(1) :]
    return rest.replace(scoring, "<SCORING>"), match.group(1)


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
@pytest.mark.parametrize("condition", ["named", "anon"])
def test_the_variants_differ_only_in_examples_and_scoring(
    arm: Arm, condition: Condition, tmp_path: Path
) -> None:
    view = lab(arm, condition, sandbox_root=tmp_path, image=IMAGE).view
    old = system_prompt(view, arm, experiments=8, fits=40)
    new = system_prompt(view, arm, experiments=8, fits=40, variant="link-neutral")
    old_rest, old_examples = _cut(old, V1_SCORING)
    new_rest, new_examples = _cut(new, LINK_NEUTRAL_SCORING)
    assert old_rest == new_rest
    assert old != new
    # The example blocks hold exactly the examples (long ones wrap).
    assert _words(old_examples) == _words(" ".join(examples(view, "v1")))
    assert _words(new_examples) == _words(" ".join(examples(view, "link-neutral")))


# --------------------------------------------------------------------------
# Leaks, and the variant reaching the session
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
@pytest.mark.parametrize("variant", ["v1", "link-neutral"])
def test_anonymised_render_of_each_variant_does_not_leak(
    arm: Arm, variant: PromptVariant, tmp_path: Path
) -> None:
    the_lab = lab(
        arm, "anon", sandbox_root=tmp_path, image=IMAGE, prompt_variant=variant
    )
    surface = rendered_surface(the_lab)
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    assert_no_leaks(surface, terms, numeric_constants=NATIVE_CONSTANTS)
    assert ("structural\ndistance" in surface["system_prompt"]) is (
        variant == "link-neutral"
    )


def test_a_link_neutral_session_records_its_variant_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    script = [
        Step(calls=(BELIEF,)),
        Step(
            calls=(("submit", {"structure": "null", "report": "r", **STOP}),),
        ),
        Step(text="done"),
    ]
    outcome = scripted_run(script, tmp_path, prompt_variant="link-neutral")
    assert outcome.outcome == "submitted"
    config = SessionRecord.load(outcome.record_path).of_kind("config")[0].body
    assert config["investigation"]["prompt_variant"] == "link-neutral"
    the_lab = lab("AG-c", prompt_variant="link-neutral")
    assert config["system_prompt"] == system_prompt(
        the_lab.view, "AG-c", experiments=3, fits=4, variant="link-neutral"
    )
