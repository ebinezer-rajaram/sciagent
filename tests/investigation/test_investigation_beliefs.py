"""The belief timeline in the tool layer (P4 Δ4; SPEC §4.0, §4.4).

``belief(favoured, rivals, confidence)`` is free and records the agent's
favoured structure, one to three rivals and a confidence in (0, 1). The layer
refuses ``run_experiment`` and ``submit`` unless a belief was recorded after
the latest experiment, and ``submit`` also needs a ``stop_reason`` from a
fixed set. ``predict``'s ``hypothesis`` is a structure only, labelled by its
canonical native text; commentary goes in a free ``note`` that is recorded and
never shown. Every refusal here is uncharged.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Final

import pytest
from investigation_support import (
    BELIEF_ARGS,
    POINTPROC,
    STOP,
    believe,
    lab,
    plain_run,
)

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.harness.tools import ToolLayer
from sciagent.investigation.prompts import Arm, system_prompt
from sciagent.investigation.runner import leak_terms
from sciagent.investigation.timeline import canonical_label
from sciagent.investigation.tools import BUDGET_EXPERIMENTS, BUDGET_FITS
from sciagent.investigation.view import Condition

CHANNELS: Final = v2.CHANNELS
#: Two spellings of one structure, and its canonical native text.
B_SPELLED: Final = "Trend + Excite(ExpK, One, all)"
B_OTHER: Final = "link=identity; Excite(ExpK, One, all) + Trend"
B: Final = canonical_label(B_SPELLED, CHANNELS)
A_TEXT: Final = "Excite(ExpK, Mark(size), all)"
A: Final = canonical_label(A_TEXT, CHANNELS)
C_TEXT: Final = "link=exp; Periodic"
C: Final = canonical_label(C_TEXT, CHANNELS)


def _meters(layer: ToolLayer) -> tuple[int, int, int]:
    return (
        layer.meter(BUDGET_EXPERIMENTS).used,
        layer.meter(BUDGET_FITS).used,
        layer.meter("submit").used,
    )


def _belief(favoured: str, rivals: list[str], confidence: float) -> dict[str, Any]:
    return {"favoured": favoured, "rivals": rivals, "confidence": confidence}


def _predict(experiment: int, hypothesis: str, **extra: Any) -> dict[str, Any]:
    return {
        "experiment": experiment,
        "diagnostic": "mean_rate",
        "hypothesis": hypothesis,
        "interval": [0.0, 1e6],
        "level": 0.8,
        **extra,
    }


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
def test_belief_is_a_free_tool_in_both_tiers(arm: Arm, tmp_path: Path) -> None:
    the_lab = lab(arm, sandbox_root=tmp_path, image="sciagent-sandbox:unused")
    schema = next(s for s in the_lab.layer().schemas() if s["name"] == "belief")
    assert schema["budget"] is None
    assert not schema["terminal"]
    inputs = schema["input_schema"]
    assert set(inputs["properties"]) == {"favoured", "rivals", "confidence"}
    assert set(inputs["required"]) == {"favoured", "rivals", "confidence"}
    assert inputs["additionalProperties"] is False
    rivals = inputs["properties"]["rivals"]
    assert (rivals["minItems"], rivals["maxItems"]) == (1, 3)


def test_belief_record_is_canonical_and_counts_experiments() -> None:
    layer = lab("AG-c").layer()
    out = layer.call("belief", _belief(A_TEXT, [B_SPELLED, C_TEXT], 0.6))
    assert not out.is_error, out.text
    assert out.record == {
        "belief": 0,
        "favoured": A,
        "rivals": [B, C],
        "confidence": 0.6,
        "experiments_run": 0,
    }
    assert _meters(layer) == (0, 0, 0)
    assert not layer.call("run_experiment", plain_run(200.0)).is_error
    again = layer.call("belief", _belief(B_OTHER, [A_TEXT], 0.75))
    assert again.record == {
        "belief": 1,
        "favoured": B,
        "rivals": [A],
        "confidence": 0.75,
        "experiments_run": 1,
    }
    assert _meters(layer) == (1, 0, 0)


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (_belief(A_TEXT, [], 0.5), "rival"),
        (_belief(A_TEXT, [B_SPELLED, C_TEXT, "null", "link=exp; Trend"], 0.5), "rival"),
        # A rival equal to the favoured after canonicalisation.
        (_belief(B_SPELLED, [B_OTHER], 0.5), "differ"),
        # Two rivals equal after canonicalisation.
        (_belief(A_TEXT, [B_SPELLED, B_OTHER], 0.5), "distinct"),
        (_belief(A_TEXT, [B_SPELLED], 0.0), "confidence"),
        (_belief(A_TEXT, [B_SPELLED], 1.0), "confidence"),
        (_belief(A_TEXT, [B_SPELLED], 1.5), "confidence"),
        (_belief(A_TEXT, [B_SPELLED], -0.1), "confidence"),
        (_belief(A_TEXT, [B_SPELLED], math.nan), "confidence"),
        ({**_belief(A_TEXT, [B_SPELLED], 0.5), "confidence": True}, "confidence"),
        ({**_belief(A_TEXT, [B_SPELLED], 0.5), "confidence": "0.5"}, "confidence"),
        ({**_belief(A_TEXT, [B_SPELLED], 0.5), "rivals": B_SPELLED}, "rival"),
        ({**_belief(A_TEXT, [B_SPELLED], 0.5), "rivals": [3]}, "rival"),
        (_belief("Excite(ExpK, One, all) because", [B_SPELLED], 0.5), "favoured"),
        (_belief(A_TEXT, ["Excite(ExpK, Mark(nope), all)"], 0.5), "rival"),
        ({**_belief(A_TEXT, [B_SPELLED], 0.5), "favoured": 7}, "favoured"),
    ],
)
def test_a_bad_belief_is_refused_uncharged_and_not_recorded(
    args: dict[str, Any], words: str
) -> None:
    layer = lab("AG-c").layer()
    out = layer.call("belief", args)
    assert out.is_error
    assert words in out.text.split("\n")[0], out.text
    assert out.record is None
    assert _meters(layer) == (0, 0, 0)
    # Nothing was recorded, so an experiment is still refused ...
    assert layer.call("run_experiment", plain_run(200.0)).is_error
    # ... and the next good belief is number 0.
    first = layer.call("belief", BELIEF_ARGS)
    assert first.record is not None and first.record["belief"] == 0


# --------------------------------------------------------------------------
# The gate on run_experiment
# --------------------------------------------------------------------------


def test_run_experiment_needs_a_belief_first() -> None:
    the_lab = lab("AG-c")
    layer = the_lab.layer()
    refused = layer.call("run_experiment", plain_run(200.0))
    assert refused.is_error
    assert "belief" in refused.text.split("\n")[0]
    assert _meters(layer) == (0, 0, 0)
    assert the_lab.world.experiments_run == 0
    # The ledger was not sealed: a prediction for e0 is still accepted.
    assert not layer.call("predict", _predict(0, A_TEXT)).is_error
    believe(layer)
    assert not layer.call("run_experiment", plain_run(200.0)).is_error
    assert _meters(layer) == (1, 0, 0)


def test_the_gate_comes_before_the_design_is_read() -> None:
    """A malformed design without a belief gets the belief refusal."""
    layer = lab("AG-c").layer()
    out = layer.call("run_experiment", {"intervention": {"type": "nope"}})
    assert out.is_error
    assert "belief" in out.text.split("\n")[0]
    assert "nope" not in out.text


def test_a_belief_is_spent_by_an_experiment_only() -> None:
    the_lab = lab("AG-c", experiments=3)
    layer = the_lab.layer()
    believe(layer)
    # A refused design does not spend the belief.
    bad = layer.call(
        "run_experiment",
        {"intervention": {"type": "censor", "window": [5.0, 1.0]}, "horizon": 100.0},
    )
    assert bad.is_error and "belief" not in bad.text.split("\n")[0]
    assert not layer.call("run_experiment", plain_run(200.0)).is_error
    # A run does: the next one is refused until a new belief, uncharged.
    stale = layer.call("run_experiment", plain_run(200.0))
    assert stale.is_error and "belief" in stale.text.split("\n")[0]
    assert _meters(layer) == (1, 0, 0)
    assert the_lab.world.experiments_run == 1
    # Free tools do not refresh it either.
    layer.call("diagnostic", {"name": "mean_rate", "data_id": "e0"})
    layer.call("predict", _predict(1, A_TEXT))
    assert layer.call("run_experiment", plain_run(200.0)).is_error
    believe(layer)
    assert not layer.call("run_experiment", plain_run(200.0)).is_error
    assert _meters(layer) == (2, 0, 0)


# --------------------------------------------------------------------------
# submit
# --------------------------------------------------------------------------


def _submit(**extra: Any) -> dict[str, Any]:
    return {"structure": A_TEXT, "report": "r", **STOP, **extra}


def test_submit_needs_a_fresh_belief() -> None:
    layer = lab("AG-c").layer()
    refused = layer.call("submit", _submit())
    assert refused.is_error and "belief" in refused.text.split("\n")[0]
    assert _meters(layer) == (0, 0, 0)
    assert not layer.finished
    believe(layer)
    assert not layer.call("run_experiment", plain_run(200.0)).is_error
    stale = layer.call("submit", _submit())
    assert stale.is_error and "belief" in stale.text.split("\n")[0]
    assert not layer.finished
    believe(layer)
    ok = layer.call("submit", _submit())
    assert not ok.is_error, ok.text
    assert layer.finished


@pytest.mark.parametrize(
    "reason", ["confident", "budget", "evidence_unlikely_to_change", "other"]
)
def test_submit_records_the_stop_reason_and_note(reason: str) -> None:
    layer = lab("AG-c").layer()
    believe(layer)
    out = layer.call(
        "submit",
        {
            "structure": "Excite(ExpK, One, all) + Trend",
            "report": "r",
            "stop_reason": reason,
            "note": "the trend fit was decisive",
        },
    )
    assert not out.is_error, out.text
    assert out.record is not None
    assert out.record["stop_reason"] == reason
    assert out.record["note"] == "the trend fit was decisive"
    # The recorded structure is canonical native text.
    assert out.record["structure"] == B
    assert "the trend fit was decisive" not in out.text


def test_submit_note_is_optional() -> None:
    layer = lab("AG-c").layer()
    believe(layer)
    out = layer.call("submit", _submit())
    assert not out.is_error, out.text
    assert out.record is not None
    assert out.record["stop_reason"] == "confident"
    assert out.record["note"] is None


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ({"structure": A_TEXT, "report": "r"}, "stop_reason"),
        (_submit(stop_reason="tired"), "stop_reason"),
        (_submit(stop_reason=1), "stop_reason"),
        (_submit(note=5), "note"),
        (_submit(note="x" * 5000), "note"),
    ],
)
def test_a_bad_submit_is_refused_uncharged(args: dict[str, Any], words: str) -> None:
    layer = lab("AG-c").layer()
    believe(layer)
    out = layer.call("submit", args)
    assert out.is_error
    assert words in out.text.split("\n")[0], out.text
    assert _meters(layer) == (0, 0, 0)
    assert not layer.finished


def test_submit_schema_lists_the_stop_reasons() -> None:
    schema = next(s for s in lab("AG-c").layer().schemas() if s["name"] == "submit")
    props = schema["input_schema"]["properties"]
    assert props["stop_reason"]["enum"] == [
        "confident",
        "budget",
        "evidence_unlikely_to_change",
        "other",
    ]
    assert set(schema["input_schema"]["required"]) == {
        "structure",
        "report",
        "stop_reason",
    }
    assert "note" in props


# --------------------------------------------------------------------------
# predict: structure-only hypothesis, free note
# --------------------------------------------------------------------------


def test_predict_records_the_canonical_hypothesis_and_the_note() -> None:
    layer = lab("AG-c").layer()
    out = layer.call("predict", _predict(0, B_SPELLED, note="trend predicts more"))
    assert not out.is_error, out.text
    assert out.record == {
        "commitment": 0,
        "experiment": 0,
        "hypothesis": B,
        "note": "trend predicts more",
    }
    assert "trend predicts more" not in out.text
    plain = layer.call("predict", _predict(0, B_OTHER))
    assert plain.record is not None
    assert plain.record["hypothesis"] == B
    assert plain.record["note"] is None
    # The evaluation carries the same canonical label.
    believe(layer)
    run = layer.call("run_experiment", plain_run(300.0))
    assert run.record is not None
    assert [p["hypothesis"] for p in run.record["predictions"]] == [B, B]


def test_commentary_in_the_hypothesis_is_refused_with_a_pointer_to_note() -> None:
    layer = lab("AG-c").layer()
    out = layer.call("predict", _predict(0, A_TEXT + ", sign forced -1: lambda=2"))
    assert out.is_error
    assert "note" in out.text.split("\n")[0]
    bad_note = layer.call("predict", _predict(0, A_TEXT, note=["x"]))
    assert bad_note.is_error and "note" in bad_note.text.split("\n")[0]


def test_predict_schema_has_a_note() -> None:
    schema = next(s for s in lab("AG-c").layer().schemas() if s["name"] == "predict")
    props = schema["input_schema"]["properties"]
    assert "note" in props
    assert "note" not in schema["input_schema"]["required"]


# --------------------------------------------------------------------------
# Labels: belief, predict and the timeline agree
# --------------------------------------------------------------------------


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_belief_and_predict_labels_match_for_one_structure(
    condition: Condition,
) -> None:
    the_lab = lab("AG-c", condition)
    view = the_lab.view
    layer = the_lab.layer()

    def shown(native_text: str) -> str:
        """The agent's spelling of a native DSL text (anon names in anon)."""
        if condition == "named":
            return native_text
        return native_text.replace("size", "m1").replace("sign", "m2")

    # Spell the same structures differently in belief and predict.
    belief = layer.call(
        "belief",
        _belief(shown(A_TEXT), [shown(B_SPELLED)], 0.5),
    )
    assert not belief.is_error, belief.text
    predict_a = layer.call(
        "predict",
        {
            **_predict(0, shown("link=identity; Excite(ExpK, Mark(size), all)")),
            "diagnostic": view.diagnostic("mean_rate"),
        },
    )
    predict_b = layer.call(
        "predict",
        {**_predict(0, shown(B_OTHER)), "diagnostic": view.diagnostic("mean_rate")},
    )
    assert belief.record is not None
    assert predict_a.record is not None and predict_b.record is not None
    assert belief.record["favoured"] == predict_a.record["hypothesis"] == A
    assert belief.record["rivals"] == [predict_b.record["hypothesis"]] == [B]
    # Native labels, whatever the condition.
    assert "size" in A and "m1" not in A
    assert canonical_label(belief.record["favoured"], CHANNELS) == A


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
def test_anonymised_belief_predict_and_submit_texts_do_not_leak(
    arm: Arm, tmp_path: Path
) -> None:
    the_lab = lab(arm, "anon", sandbox_root=tmp_path, image="sciagent-sandbox:unused")
    view = the_lab.view
    layer = the_lab.layer()
    rate = view.diagnostic("mean_rate")
    a = "Excite(ExpK, Mark(m1), m2=+)"
    b = "Trend + Excite(ExpK, Mark(m1), m2=+)"
    texts = {
        "refused run": layer.call("run_experiment", plain_run(view.time(200.0))).text,
        "refused submit": layer.call(
            "submit", {"structure": a, "report": "r", **STOP}
        ).text,
        "bad belief": layer.call("belief", _belief(a, [a], 0.5)).text,
        "belief": layer.call("belief", _belief(a, [b, "null"], 0.5)).text,
        "predict": layer.call(
            "predict", {**_predict(0, b, note="n"), "diagnostic": rate}
        ).text,
        "bad predict": layer.call(
            "predict", {**_predict(0, a + " because"), "diagnostic": rate}
        ).text,
        "submit": layer.call(
            "submit", {"structure": a, "report": "r", **STOP, "note": "n"}
        ).text,
    }
    terms = leak_terms(POINTPROC, tuple(c.name for c in CHANNELS))
    assert_no_leaks(texts, terms)
    assert "m1" in texts["belief"]


# --------------------------------------------------------------------------
# Prompt text
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
@pytest.mark.parametrize("condition", ["named", "anon"])
def test_the_prompt_asks_for_beliefs_and_a_rival_prediction(
    arm: Arm, condition: Condition, tmp_path: Path
) -> None:
    the_lab = lab(
        arm, condition, sandbox_root=tmp_path, image="sciagent-sandbox:unused"
    )
    text = system_prompt(the_lab.view, the_lab.arm, experiments=8, fits=40)
    assert "`belief(favoured, rivals, confidence)`" in text
    assert "before every experiment" in text
    assert "at least one prediction under a rival" in text
    for reason in ("confident", "budget", "evidence_unlikely_to_change", "other"):
        assert f"`{reason}`" in text
    assert "`note`" in text
