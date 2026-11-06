"""The ``rescaled`` condition (P4 Δ9): names kept, time as the anonymised view has it.

C2 reads named against rescaled (time) and rescaled against anon (names), so
what an agent sees here must be two exact statements, each checked on the
prompt, every tool schema and every tool output:

1. **No native name changes.** Blank every number and the text is the named
   condition's, byte for byte. A renamed channel or diagnostic, or a reworded
   line, fails this.
2. **Every time value is the anonymised condition's.** Rename the channels and
   diagnostics of the rescaled text and it is the anon text, byte for byte. A
   time value left native fails this, since the anon number is 8 times it.

The two together leave nothing for rescaled to differ in but the numbers anon
changes. The softplus note is the one line kept out of (1): whether rescaled
shows it is not settled here, so nothing in this file pins it either way.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import pytest
from investigation_support import POINTPROC, lab

from environments.pointproc import v2
from sciagent.anonymise import DEFAULT_TIME_FACTOR, anonymise_text
from sciagent.diagnostics import catalogue
from sciagent.diagnostics.catalogue import ArgKind
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    MAX_HORIZON,
    Experiment,
    ForceEvents,
)
from sciagent.harness.record import canonical_json
from sciagent.investigation import InvestigationSpec
from sciagent.investigation.prompts import Arm, PromptVariant
from sciagent.investigation.runner import rendered_config, rendered_surface
from sciagent.investigation.view import (
    CONDITIONS,
    AgentView,
    Condition,
    ViewError,
    make_view,
)

IMAGE: Final = "sciagent-sandbox:unused"
ARMS: Final[tuple[Arm, ...]] = ("AG-c", "AG-o")
VARIANTS: Final[tuple[PromptVariant, ...]] = ("v1", "link-neutral")
#: A number as a whole token: not the 1 of ``m1``, ``d17`` or ``e0``.
NUMBER: Final = re.compile(
    r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![\w.])"
)
NOTE: Final = "note: softplus-link coefficients are reported as fitted"
#: How the one output that lists channels in agent-name order starts.
KERNELS: Final = "Wiener-Hopf"
#: A structure under each link the view can convert, and the size channel's name.
STRUCTURES: Final[tuple[str, ...]] = (
    "link=identity; Excite(ExpK, Mark({size}), all)",
    "link=exp; Excite(ExpK, One, all) + Trend",
    "link=identity; Periodic + Excite(PowerK, One, {sign}=+)",
)


def _blank(text: str) -> str:
    return NUMBER.sub("#", text)


def _numbers(text: str) -> Counter[str]:
    return Counter(NUMBER.findall(text))


def _json(value: object) -> str:
    """Canonical JSON, its escaped newlines opened so a number can start a line."""
    return canonical_json(value).replace("\\n", "\n")


def _rows(text: str) -> Counter[tuple[str, ...]]:
    """A text's lines, each as its sorted tokens: the same rows and the same
    numbers in each, whatever order the channels come in."""
    return Counter(
        tuple(sorted(re.split(r"[\s;,]+", line))) for line in text.splitlines()
    )


def _view(condition: Condition) -> AgentView:
    return make_view(condition, v2.CHANNELS, POINTPROC.anon_channels)


def _views() -> tuple[AgentView, AgentView, AgentView]:
    return _view("named"), _view("rescaled"), _view("anon")


def _renamed(text: str) -> str:
    """Rescaled text in the anonymised condition's names."""
    name_map = _view("anon").name_map
    assert name_map is not None
    return anonymise_text(text, name_map)


# --------------------------------------------------------------------------
# The view
# --------------------------------------------------------------------------


def test_the_conditions_are_three_named_first() -> None:
    assert CONDITIONS == ("named", "rescaled", "anon")


def test_the_factor_is_the_anonymised_conditions_and_it_is_eight() -> None:
    named, rescaled, anon = _views()
    assert rescaled.condition == "rescaled"
    assert rescaled.factor == anon.factor == DEFAULT_TIME_FACTOR == 8.0
    assert named.factor == 1.0


def test_names_are_the_native_ones_in_both_directions() -> None:
    named, rescaled, _ = _views()
    assert rescaled.channels == named.channels
    assert rescaled.diagnostics() == named.diagnostics()
    for spec in v2.CHANNELS:
        assert rescaled.channel(spec.name) == spec.name
        assert rescaled.channel_native(spec.name) == spec.name
    for name in catalogue.names():
        assert rescaled.diagnostic(name) == name
        assert rescaled.diagnostic_native(name) == name


def test_an_anonymised_name_is_unknown_here() -> None:
    _, rescaled, _ = _views()
    with pytest.raises(ViewError):
        rescaled.channel_native("m1")
    with pytest.raises(ViewError):
        rescaled.diagnostic_native("d01")


def test_a_structure_reads_and_writes_as_named() -> None:
    named, rescaled, _ = _views()
    for template in STRUCTURES:
        text = template.format(size="size", sign="sign")
        assert rescaled.parse(text) == named.parse(text)
        assert rescaled.render(named.parse(text)) == named.render(named.parse(text))


def test_the_dataset_is_the_native_one_times_eight_bit_for_bit() -> None:
    world = lab("AG-c", "named").world
    native = world.datasets["obs"]
    _, rescaled, anon = _views()
    shown = rescaled.dataset(native)
    assert np.array_equal(shown.log.times, native.log.times * 8.0)
    assert np.array_equal(shown.log.times / 8.0, native.log.times)
    assert shown.log.horizon == native.log.horizon * 8.0
    assert shown.excluded == tuple((a * 8.0, b * 8.0) for a, b in native.excluded)
    assert sorted(shown.log.marks) == sorted(native.log.marks)
    for name, values in native.log.marks.items():
        assert np.array_equal(shown.log.marks[name], values)
    assert np.array_equal(shown.endogenous, native.endogenous)
    assert shown.label == native.label
    assert np.array_equal(shown.log.times, anon.dataset(native).log.times)


def test_a_design_runs_the_same_native_experiment_in_every_condition() -> None:
    views = _views()

    def design(view: AgentView) -> dict[str, Any]:
        return {
            "intervention": {
                "type": "force_events",
                "times": {"start": view.time(10.0), "step": view.time(5.0), "n": 4},
                "marks": {view.channel("size"): {"constant": 2.0}},
            },
            "horizon": view.time(40.0),
        }

    natives = [v.experiment_native(design(v)) for v in views]
    assert natives[0] == natives[1] == natives[2]


def test_a_framework_design_is_shown_in_rescaled_units_and_native_names() -> None:
    _, rescaled, _ = _views()
    native = Experiment(ForceEvents((1.5, 2.5), {"size": (3.0, 4.0)}), 10.0)
    shown = rescaled.experiment_json(native)
    assert shown["horizon"] == 80.0
    intervention = shown["intervention"]
    assert isinstance(intervention, dict)
    assert intervention["times"] == [12.0, 20.0]
    assert intervention["marks"] == {"size": [3.0, 4.0]}


def test_fitted_numbers_convert_as_anonymised() -> None:
    _, rescaled, anon = _views()
    for name in ("exp_rate", "power_c", "power_p", "period", "phase"):
        assert rescaled.psi_value(name, 3.0) == anon.psi_value(name, 3.0)
    assert rescaled.log_likelihood(-5.0, 7) == anon.log_likelihood(-5.0, 7)
    assert rescaled.information_criterion(9.0, 7) == anon.information_criterion(9.0, 7)
    assert rescaled.time(1.5) == 12.0 and rescaled.rate(1.5) == 0.1875
    structure = rescaled.parse("link=exp; Excite(ExpK, One, all)")
    assert rescaled.theta(structure, (0.5, 0.25)) == anon.theta(structure, (0.5, 0.25))


def test_a_condition_outside_the_three_is_refused() -> None:
    with pytest.raises(ViewError, match="rescaled"):
        _view(cast(Condition, "other"))


def test_a_record_of_a_rescaled_session_reads_back() -> None:
    spec = InvestigationSpec(POINTPROC.name, "hawkes", "AG-c", "rescaled", 1, 3, 4)
    assert InvestigationSpec.from_json(spec.as_json()).condition == "rescaled"


# --------------------------------------------------------------------------
# The rendered surface
# --------------------------------------------------------------------------


def _surface(arm: Arm, condition: Condition, variant: PromptVariant) -> dict[str, Any]:
    return rendered_config(
        POINTPROC,
        v2.CHANNELS,
        arm,
        condition,
        experiments=8,
        fits=40,
        wall_time_s=3600.0,
        prompt_variant=variant,
    )


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_the_surface_differs_from_named_in_numbers_only(
    arm: Arm, variant: PromptVariant
) -> None:
    named = _surface(arm, "named", variant)
    rescaled = _surface(arm, "rescaled", variant)
    assert named != rescaled
    assert sorted(named) == sorted(rescaled)
    for label in sorted(named):
        assert _blank(_json(rescaled[label])) == _blank(_json(named[label])), label


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_the_surfaces_numbers_are_the_anonymised_surfaces(
    arm: Arm, variant: PromptVariant
) -> None:
    rescaled = _surface(arm, "rescaled", variant)
    anon = _surface(arm, "anon", variant)
    assert sorted(rescaled) == sorted(anon)
    for label in sorted(anon):
        assert _numbers(_json(rescaled[label])) == _numbers(_json(anon[label])), label


def test_the_prompts_time_values_are_scaled_by_name() -> None:
    text = _surface("AG-c", "rescaled", "link-neutral")["system_prompt"]
    assert f"default {DEFAULT_HORIZON * 8:g}" in text
    assert f"The horizon is at most {MAX_HORIZON * 8:g}" in text


@pytest.mark.parametrize("arm", ARMS)
def test_the_opening_turn_states_the_horizon_in_rescaled_units(
    arm: Arm, tmp_path: Path
) -> None:
    surfaces = {
        c: rendered_surface(
            lab(arm, c, sandbox_root=tmp_path / c, image=IMAGE), wall_time_s=900.0
        )
        for c in ("named", "rescaled", "anon")
    }
    opening = {c: s["opening_prompt"] for c, s in surfaces.items()}
    assert _blank(opening["rescaled"]) == _blank(opening["named"])
    assert opening["rescaled"] == opening["anon"]


# --------------------------------------------------------------------------
# The tool outputs
# --------------------------------------------------------------------------


def _diagnostic_args(native: str, view: AgentView) -> dict[str, Any]:
    """Every required argument, in the view's names and units."""
    args: dict[str, Any] = {}
    for arg in catalogue.get(native).args:
        if arg.default is not None:
            continue
        if arg.kind is ArgKind.CHANNEL:
            args[arg.name] = view.channel("size")
        elif arg.kind is ArgKind.TIME:
            args[arg.name] = view.time(2.0)
    return args


def _calls(view: AgentView) -> list[tuple[str, dict[str, Any]]]:
    """One session touching every tool, written by the view it runs in."""
    time, channel = view.time, view.channel
    size, sign = channel("size"), channel("sign")
    calls: list[tuple[str, dict[str, Any]]] = [
        (
            "diagnostic",
            {
                "name": view.diagnostic(native),
                "args": _diagnostic_args(native, view),
                "data_id": "obs",
            },
        )
        for native in catalogue.names()
    ]
    calls.append(("nonparam_kernels", {"data_id": "obs"}))
    calls += [
        (
            "fit",
            {"structure": template.format(size=size, sign=sign), "data_ids": ["obs"]},
        )
        for template in STRUCTURES
    ]
    belief = {
        "favoured": STRUCTURES[0].format(size=size, sign=sign),
        "rivals": [STRUCTURES[1].format(size=size, sign=sign)],
        "confidence": 0.5,
    }
    mean_rate = view.diagnostic("mean_rate")
    calls += [
        (
            "predict",
            {
                "experiment": 0,
                "diagnostic": mean_rate,
                "args": {},
                "hypothesis": belief["favoured"],
                "interval": [view.rate(0.25), view.rate(40.0)],
                "level": 0.9,
            },
        ),
        ("belief", belief),
        (
            "run_experiment",
            {
                "intervention": {
                    "type": "compose",
                    "parts": [
                        {
                            "type": "force_events",
                            "times": {"start": time(10.0), "step": time(5.0), "n": 6},
                            "marks": {size: {"constant": 2.0}},
                        },
                        {
                            "type": "censor",
                            "start": time(60.0),
                            "end": time(70.0),
                        },
                        {
                            "type": "clamp_rate",
                            "start": time(80.0),
                            "end": time(90.0),
                            "rate": view.rate(0.5),
                        },
                    ],
                },
                "horizon": time(120.0),
            },
        ),
        ("diagnostic", {"name": mean_rate, "args": {}, "data_id": "e0"}),
        (
            "fit",
            {
                "structure": STRUCTURES[0].format(size=size, sign=sign),
                "data_ids": ["obs", "e0"],
            },
        ),
        # Refusals carry numbers too: past the horizon and past the rate cap.
        ("belief", belief),
        (
            "run_experiment",
            {
                "intervention": {"type": "compose", "parts": []},
                "horizon": time(MAX_HORIZON * 2.0),
            },
        ),
        (
            "run_experiment",
            {
                "intervention": {
                    "type": "clamp_rate",
                    "start": time(1.0),
                    "end": time(2.0),
                    "rate": view.rate(1e9),
                },
                "horizon": time(10.0),
            },
        ),
        ("diagnostic", {"name": mean_rate, "args": {}, "data_id": "nope"}),
        ("belief", belief),
        (
            "submit",
            {
                "structure": belief["favoured"],
                "report": "a report",
                "stop_reason": "confident",
            },
        ),
    ]
    return calls


def _session(arm: Arm, condition: Condition, tmp_path: Path) -> list[tuple[str, bool]]:
    the_lab = lab(
        arm,
        condition,
        experiments=4,
        fits=8,
        sandbox_root=tmp_path / condition,
        image=IMAGE,
    )
    layer = the_lab.layer()
    outputs = [layer.call(name, args) for name, args in _calls(the_lab.view)]
    return [(o.text, o.is_error) for o in outputs]


@pytest.fixture(scope="module")
def sessions(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[Condition, list[tuple[str, bool]]]:
    root = tmp_path_factory.mktemp("rescaled")
    return {c: _session("AG-c", c, root) for c in ("named", "rescaled", "anon")}


@pytest.mark.slow
def test_the_session_exercises_every_tool_and_both_kinds_of_result(
    sessions: dict[Condition, list[tuple[str, bool]]],
) -> None:
    named = sessions["named"]
    assert any(error for _, error in named)
    assert any("value" in text.lower() for text, _ in named)
    assert sum(not error for _, error in named) > len(catalogue.names())
    assert named[-1][1] is False, named[-1][0]


@pytest.mark.slow
def test_no_native_name_changes_in_any_tool_output(
    sessions: dict[Condition, list[tuple[str, bool]]],
) -> None:
    pairs = zip(sessions["named"], sessions["rescaled"], strict=True)
    for (named, named_error), (rescaled, rescaled_error) in pairs:
        assert rescaled_error == named_error
        assert _blank(rescaled) == _blank(named)


@pytest.mark.slow
def test_every_time_value_in_every_tool_output_is_the_anonymised_one(
    sessions: dict[Condition, list[tuple[str, bool]]],
) -> None:
    pairs = zip(sessions["rescaled"], sessions["anon"], strict=True)
    for (rescaled, rescaled_error), (anon, anon_error) in pairs:
        assert rescaled_error == anon_error
        if rescaled.startswith(KERNELS):
            # Channels come in agent-name order: rescaled keeps named's.
            assert _rows(_renamed(rescaled)) == _rows(anon)
        else:
            assert _renamed(rescaled) == anon


@pytest.mark.slow
def test_a_time_value_really_moved_between_named_and_rescaled(
    sessions: dict[Condition, list[tuple[str, bool]]],
) -> None:
    """The two statements above would hold of three copies of one text."""
    moved = [
        named
        for (named, _), (rescaled, _) in zip(
            sessions["named"], sessions["rescaled"], strict=True
        )
        if named != rescaled
    ]
    assert len(moved) >= 8


def _softplus_fit(condition: Condition) -> str:
    the_lab = lab("AG-c", condition)
    structure = "link=softplus; Excite(ExpK, One, all)"
    out = the_lab.layer().call("fit", {"structure": structure, "data_ids": ["obs"]})
    assert not out.is_error, out.text
    return out.text


def _without_note(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if NOTE not in line)


@pytest.mark.slow
def test_a_softplus_fit_matches_apart_from_the_note_which_is_not_settled() -> None:
    named, rescaled, anon = (
        _without_note(_softplus_fit(c)) for c in ("named", "rescaled", "anon")
    )
    assert _blank(rescaled) == _blank(named)
    assert _renamed(rescaled) == anon


# --------------------------------------------------------------------------
# The files an AG-o agent reads
# --------------------------------------------------------------------------


def _staged(condition: Condition, tmp_path: Path) -> dict[str, np.ndarray]:
    the_lab = lab("AG-o", condition, sandbox_root=tmp_path / condition, image=IMAGE)
    assert the_lab.sandbox is not None
    with np.load(the_lab.sandbox.data_dir / "obs.npz") as arrays:
        return {name: arrays[name] for name in arrays.files}


def test_the_files_have_native_columns_and_rescaled_times(tmp_path: Path) -> None:
    named = _staged("named", tmp_path)
    rescaled = _staged("rescaled", tmp_path)
    assert sorted(rescaled) == sorted(named)
    assert np.array_equal(rescaled["times"], named["times"] * 8.0)
    for name in sorted(named):
        if name != "times":
            assert np.array_equal(rescaled[name], named[name])
