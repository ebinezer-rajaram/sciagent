"""Positive controls 4 and 6 through the live loop (P4 WP11; Δ11 choice 18).

Each control runs a scripted session (:class:`ScriptedDriver` → the live
runner → the real tool layer over a real world) and reads its saved
:class:`SessionRecord` with framework code only. Each must also separate what
it claims to: control 4 fails when the two conditions saw different data,
control 6 fails when one evaluated prediction comes out the other way.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Final

import pytest
from investigation_support import (
    MODEL,
    POINTPROC,
    SEED,
    clean_environment,
    lab,
    scripted_run,
)

from environments.pointproc import v2
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.syntax import parse, render
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.investigation import run
from sciagent.investigation.view import Condition
from sciagent.scoring.controls_p4 import (
    BEHAVIOUR_EXPERIMENTS,
    NAMING_CONDITIONS,
    PLANTED_FITS,
    BehaviourHypotheses,
    behaviour_control,
    behaviour_script,
    naming_null,
    planted_hint_script,
)
from sciagent.scoring.errors import ScoringError

CHANNELS: Final = v2.CHANNELS
TRUTH: Final = "size_excitation"

# --------------------------------------------------------------------------
# Control 4: the naming null
# --------------------------------------------------------------------------


def _planted(condition: Condition, out: Path, seed: int = SEED) -> SessionRecord:
    view = lab("AG-c", condition, truth=TRUTH).view
    script = planted_hint_script(v2.TRUTHS[TRUTH].structure, view)
    outcome = run(
        POINTPROC,
        TRUTH,
        "AG-c",
        condition,
        MODEL,
        seed,
        out,
        experiments=3,
        fits=PLANTED_FITS,
        max_turns=40,
        wall_time_s=600.0,
        driver=ScriptedDriver(script),
    )
    assert outcome.outcome == "submitted", outcome
    return SessionRecord.load(outcome.record_path)


def test_control_4_named_and_anon_give_identical_submissions_and_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    records = {c: _planted(c, tmp_path / c) for c in NAMING_CONDITIONS}
    v = naming_null(records)
    assert v.passed, v.mismatches
    assert v.conditions == NAMING_CONDITIONS == ("named", "anon")
    named = dict(v.facts)["named"]
    truth = canonicalise(v2.TRUTHS[TRUTH].structure)
    assert named.submission == render(truth)
    assert named.submission_hash == structure_hash(truth)
    # The hint first, then the truth, on obs and then on obs + e0: four fits,
    # every one finite, and the anon run's are the same floats bit for bit.
    assert len(named.fits) == PLANTED_FITS == 4
    assert [f.data_ids for f in named.fits] == [
        ("obs",),
        ("obs",),
        ("obs", "e0"),
        ("obs", "e0"),
    ]
    anon = dict(v.facts)["anon"]
    assert [f.log_likelihood.hex() for f in anon.fits] == [
        f.log_likelihood.hex() for f in named.fits
    ]


def test_control_4_fails_when_the_conditions_saw_different_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    records = {
        "named": _planted("named", tmp_path / "named"),
        "anon": _planted("anon", tmp_path / "anon", seed=SEED + 1),
    }
    v = naming_null(records)
    assert not v.passed
    assert any("fit" in m for m in v.mismatches), v.mismatches
    # Same submission (the script submits the truth either way).
    facts = dict(v.facts)
    assert facts["named"].submission == facts["anon"].submission


def test_control_4_needs_exactly_the_preregistered_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    named = _planted("named", tmp_path / "named")
    with pytest.raises(ScoringError):
        naming_null({"named": named})
    with pytest.raises(ScoringError):
        naming_null({"named": named, "anon": named, "rescaled": named})


def test_the_planted_script_proposes_the_hint_first() -> None:
    view = lab("AG-c", "anon", truth=TRUTH).view
    script = planted_hint_script(v2.TRUTHS[TRUTH].structure, view)
    calls = [call for step in script for call in step.calls]
    first_fit = next(args for name, args in calls if name == "fit")
    # The hint of Excite(ExpK, Mark(size), all) drops the mark function.
    assert canonicalise(view.parse(str(first_fit["structure"]))) == canonicalise(
        parse("link=identity; Excite(ExpK, One, all)", CHANNELS)
    )
    # In anon, no native channel name reaches the agent's text.
    assert "size" not in repr(calls)


# --------------------------------------------------------------------------
# Control 6: the behaviour instrument
# --------------------------------------------------------------------------

HYPOTHESES: Final = BehaviourHypotheses(
    a=parse("Excite(ExpK, Mark(size), all)", CHANNELS),
    b=parse("link=identity; Excite(ExpK, One, all) + Trend", CHANNELS),
    c=parse("link=exp; Excite(ExpK, One, sign=+)", CHANNELS),
)


def _behaviour(
    condition: Condition,
    out: Path,
    edit: int | None = None,
) -> SessionRecord:
    view = lab("AG-c", condition).view
    script = list(behaviour_script(HYPOTHESES, view))
    if edit is not None:
        # Turn the covered prediction of call 8 into a missed one.
        step = script[edit]
        ((name, args),) = step.calls
        assert name == "predict"
        script[edit] = dataclasses.replace(
            step, calls=((name, {**args, "interval": [-2.0, -1.0]}),)
        )
    outcome = scripted_run(
        script, out, condition=condition, experiments=BEHAVIOUR_EXPERIMENTS
    )
    assert outcome.outcome == "submitted"
    return SessionRecord.load(outcome.record_path)


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_control_6_reproduces_the_hand_computed_measures(
    condition: Condition, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    record = _behaviour(condition, tmp_path)
    v = behaviour_control(record, CHANNELS, HYPOTHESES)
    assert v.passed, v.mismatches
    assert v.observed.coverage == pytest.approx(2 / 3)
    assert v.observed.calibration_error == pytest.approx(-1 / 15)
    assert v.observed.falsification_share == pytest.approx(1 / 3)
    assert (v.observed.revision_rate, v.observed.zombie_rate) == (1.0, 1.0)


def test_control_6_fails_when_an_outcome_flips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    # Step 6 of the script holds call 8 (the covered prediction on e1).
    record = _behaviour("named", tmp_path, edit=6)
    v = behaviour_control(record, CHANNELS, HYPOTHESES)
    assert not v.passed
    assert v.observed.covered == 1
    assert any(m.startswith("covered") for m in v.mismatches), v.mismatches


def test_control_6_refuses_hypotheses_it_cannot_tell_apart() -> None:
    a = HYPOTHESES.a
    with pytest.raises(ScoringError):
        BehaviourHypotheses(a=a, b=HYPOTHESES.b, c=a)
    with pytest.raises(ScoringError):  # b needs two features to be respelled
        BehaviourHypotheses(a=HYPOTHESES.c, b=a, c=HYPOTHESES.b)


def test_control_6_script_respells_b() -> None:
    view = lab("AG-c", "named").view
    texts = [
        str(args["hypothesis"])
        for step in behaviour_script(HYPOTHESES, view)
        for name, args in step.calls
        if name == "predict"
    ]
    b = render(canonicalise(HYPOTHESES.b))
    assert texts[1] != b and canonicalise(parse(texts[1], CHANNELS)) == canonicalise(
        HYPOTHESES.b
    )


def test_step_type_is_the_harness_step() -> None:
    view = lab("AG-c", "named").view
    assert all(isinstance(s, Step) for s in behaviour_script(HYPOTHESES, view))
