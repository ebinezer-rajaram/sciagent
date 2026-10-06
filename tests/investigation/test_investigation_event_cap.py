"""An experiment's simulation cannot outrun the investigation (LOG 2026-10-02).

``run_experiment`` runs the simulator inside a tool call, which cannot be
cancelled, so the simulation's *work* has to be bounded, and deterministically
(invariant 3): a wall-clock limit would make the outcome depend on the
machine. The bound is the world's event cap, :data:`MAX_EVENTS`, together
with the simulator's candidate cap and runaway guard, which scale with it.

Since P4 Δ2 a run that reaches the cap is truncated at its cap's event, at τ,
and kept. These tests check that the cap is sized against the intervention
caps; that a truncated experiment yields its N events, is charged, is
described in the agent's terms (identical in both conditions apart from
units) and identical on every run, and replays; that agent-side analysis is
clipped to [0, τ]; that a τ hidden inside an excluded window is never shown;
that the runaway guard's stop is unchanged; and (``slow``) that an
adversarial design on a heavy power/gamma-kernel truth stops within minutes.
"""

from __future__ import annotations

import copy
import math
import time
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest
from investigation_support import (
    POINTPROC,
    clean_environment,
    lab,
    plain_run,
    scripted_run,
)

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.grammar import PsiSlot
from sciagent.glm.interventions import (
    MAX_CLAMP_EVENTS,
    MAX_FORCED_EVENTS,
    MAX_HORIZON,
    MAX_MARK_Z,
    Experiment,
    InjectMarks,
    rescale_dataset,
)
from sciagent.glm.simulate import Coefficients, ExplosionError
from sciagent.glm.syntax import parse
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation import TruthSpec, replay_investigation
from sciagent.investigation.prompts import num
from sciagent.investigation.runner import leak_terms
from sciagent.investigation.tools import BUDGET_EXPERIMENTS
from sciagent.investigation.view import (
    Condition,
    clip_to_analysis,
    shown_truncation,
)
from sciagent.investigation.world import MAX_EVENTS, World

#: Injecting size 3 makes ``size_excitation`` supercritical (branching ratio
#: 3 x 0.699 > 1): an ExpK truth, so it reaches the cap in about a second.
EXPLOSIVE: Final[dict[str, Any]] = {
    "intervention": {
        "type": "inject_marks",
        "window": [0.0, 300.0],
        "channel": "size",
        "value": 3.0,
    },
    "horizon": 300.0,
}


#: The budget line every tool result ends with (P4 Δ12).
def budget(experiments: int, of_e: int, fits: int, of_f: int) -> str:
    return f"budget left: {experiments} of {of_e} experiments, {fits} of {of_f} fits"


#: What a run stopped by the runaway guard or the candidate cap says (the
#: event cap itself now truncates, P4 Δ2).
STOP_TEXT: Final = (
    "experiment e0 was stopped and produced no dataset: the process's event "
    "rate grew without bound, so the simulation was stopped before it reached "
    f"the limit of {MAX_EVENTS} events. It counts against the budget."
)


def _lines(text: str) -> list[str]:
    return text.split("\n")


def test_the_cap_admits_every_valid_design_and_bounds_the_cost() -> None:
    # A valid design at the nominal mean rate of 1 (data.py) -- the longest
    # horizon, the clamp cap and the forced-event cap all at once -- expects
    # fewer events than the cap, so only a process that runs well above its
    # nominal rate is stopped.
    assert MAX_HORIZON + MAX_CLAMP_EVENTS + MAX_FORCED_EVENTS < MAX_EVENTS
    # PowerK/GammaK simulation is O(n) per candidate, so a run's cost grows as
    # the square of its events: 15,000 events is about a minute on the
    # reference machine for the heaviest dev truth (measured, LOG).
    assert MAX_EVENTS <= 15_000
    # Every successful experiment in the paused pilot had at most 12,181
    # events, so the cap changes none of their outcomes.
    assert MAX_EVENTS > 12_181


def test_a_capped_experiment_is_truncated_charged_and_deterministic() -> None:
    outputs = []
    for _ in range(2):
        the_lab = lab("AG-c", experiments=2)
        layer = the_lab.layer()
        out = layer.call("run_experiment", EXPLOSIVE)
        assert not out.is_error, out.text
        assert layer.meter(BUDGET_EXPERIMENTS).used == 1
        outputs.append(out)
        # The dataset is kept: N events, the planned horizon, the tail excluded.
        native = the_lab.world.datasets["e0"]
        assert native.log.n == MAX_EVENTS
        assert native.log.horizon == EXPLOSIVE["horizon"]
        tau = the_lab.world.truncated_at("e0")
        assert tau is not None and native.log.times[-1] == tau
        # Agent-side analysis sees [0, tau]: a diagnostic on e0 runs there.
        rate = layer.call("diagnostic", {"name": "mean_rate", "data_id": "e0"})
        assert not rate.is_error, rate.text
        assert f"mean_rate on e0 = {num(MAX_EVENTS / tau)}" in rate.text
        nxt = layer.call("run_experiment", plain_run(100.0))
        assert "experiment e1 done" in nxt.text, nxt.text
    first, second = outputs
    assert first.record is not None
    tau = first.record["truncated_at"]
    assert first.record["n_events"] == MAX_EVENTS
    assert first.record["analysis_horizon"] == tau
    assert "stopped" not in first.record
    lines = _lines(first.text)
    assert lines[0] == "experiment e0 done (1 of 2 experiments used)."
    assert lines[1] == (
        f"truncated: the run reached the limit of {MAX_EVENTS} events and was cut "
        f"short; this dataset covers [0, {num(tau)}] of the planned horizon "
        f"{num(300.0)}, and nothing after {num(tau)} was observed."
    )
    assert lines[2] == f"dataset `e0`: {MAX_EVENTS} events observed on [0, {num(tau)}]"
    assert lines[-1] == budget(1, 2, 4, 4)
    assert (first.text, first.record) == (second.text, second.record)


def test_a_run_stopped_by_the_runaway_guard_is_charged_and_gives_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The candidate cap and the runaway-bound guard still stop a run (the
    # simulator raises before the cap); that path is unchanged.
    real_run = World.run

    def runaway(self: World, experiment: Experiment) -> Any:
        real_run(self, Experiment(experiment.intervention, 1.0))  # uses the index
        raise ExplosionError("intensity bound inf on (0, 1] is runaway")

    monkeypatch.setattr(World, "run", runaway)
    texts = []
    for condition in ("named", "anon"):
        the_lab = lab("AG-c", condition, experiments=2)
        layer = the_lab.layer()
        horizon = 100.0 * the_lab.view.time(1.0)
        out = layer.call("run_experiment", plain_run(horizon))
        assert not out.is_error
        assert layer.meter(BUDGET_EXPERIMENTS).used == 1
        assert out.record is not None and out.record["stopped"] is True
        texts.append(out.text)
    # Event counts are unit-free and the simulator's own message (native
    # times) is never shown, so the text carries nothing condition-specific.
    assert texts[0] == texts[1] == STOP_TEXT + "\n" + budget(1, 2, 4, 4)


@pytest.mark.parametrize(
    "intervention",
    [
        {
            "type": "inject_marks",
            "window": [0.0, 50.0],
            "channel": "size",
            "value": 1e4,
        },
        {"type": "force_events", "times": [1.0], "marks": {"size": [12.5]}},
    ],
)
def test_a_mark_far_outside_the_channel_scale_is_refused_uncharged(
    intervention: dict[str, Any],
) -> None:
    # Injected size 1e4 on dev-005/dev-009 made the thinning bound so loose
    # that one capped run took five minutes; |z| > MAX_MARK_Z is refused.
    layer = lab("AG-c", experiments=1).layer()
    out = layer.call("run_experiment", {"intervention": intervention, "horizon": 50.0})
    assert out.is_error
    assert f"at most {MAX_MARK_Z:g} scale units" in out.text, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0
    # The largest value any pilot design used (forced size 10) stays valid.
    ok = {"type": "force_events", "times": [1.0], "marks": {"size": [10.0]}}
    assert not layer.call(
        "run_experiment", {"intervention": ok, "horizon": 50.0}
    ).is_error


def test_the_truncation_text_differs_between_conditions_only_in_units() -> None:
    named = lab("AG-c", "named")
    anon = lab("AG-c", "anon")
    c = anon.view.time(1.0)
    assert c != 1.0, "the anonymised condition must rescale time here"
    out_named = named.layer().call("run_experiment", EXPLOSIVE)
    out_anon = anon.layer().call("run_experiment", _anon_explosive(c))
    assert out_named.record is not None and out_anon.record is not None
    # The same native experiment: the same draw, the same native record.
    assert out_anon.record == out_named.record
    tau = out_named.record["truncated_at"]
    expected = [
        line.replace(num(tau), num(tau * c)).replace(num(300.0), num(300.0 * c))
        for line in _lines(out_named.text)
    ]
    got = _lines(out_anon.text)
    summary = next(i for i, line in enumerate(got) if line.startswith("summary:"))
    # Every line but the summary statistics (rates are per unit time) is the
    # named line with its times rescaled.
    assert got[:summary] + got[summary + 1 :] == (
        expected[:summary] + expected[summary + 1 :]
    )
    # tau goes through the same rescaling as the data (rescale_dataset).
    native = clip_to_analysis(named.world.datasets["e0"], tau)
    agent = anon.view.dataset(clip_to_analysis(anon.world.datasets["e0"], tau))
    assert agent.log.horizon == rescale_dataset(native, c).log.horizon == tau * c
    assert num(tau * c) in out_anon.text and num(tau) not in out_anon.text


def _anon_explosive(c: float) -> dict[str, Any]:
    return {
        "intervention": {
            "type": "inject_marks",
            "window": [0.0, 300.0 * c],
            "channel": "m1",
            "value": 3.0,
        },
        "horizon": 300.0 * c,
    }


#: A censoring window that covers tau (about 21.1 for this seed): the cap's
#: event is unobserved, so its time must never be shown (WP1 -> WP6 note).
CENSORED_TAIL: Final[dict[str, Any]] = {
    "intervention": {
        "type": "compose",
        "parts": [
            EXPLOSIVE["intervention"],
            {"type": "censor", "window": [10.0, 300.0]},
        ],
    },
    "horizon": 300.0,
}


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_a_hidden_tau_is_never_shown(condition: Condition) -> None:
    the_lab = lab("AG-c", condition, experiments=2)
    c = the_lab.view.time(1.0)
    args = CENSORED_TAIL if condition == "named" else _scaled(CENSORED_TAIL, c)
    layer = the_lab.layer()
    out = layer.call("run_experiment", args)
    assert not out.is_error, out.text
    assert out.record is not None
    tau = out.record["truncated_at"]
    # Censoring does not change the draw, so tau falls inside the window.
    assert 10.0 < tau < 300.0
    native = the_lab.world.datasets["e0"]
    assert native.excluded == ((10.0, 300.0),)
    # Shown instead: the start of the merged excluded tail, which the agent
    # can see anyway (its own censoring window).
    assert out.record["analysis_horizon"] == 10.0
    assert num(tau * c) not in out.text
    assert f"covers [0, {num(10.0 * c)}]" in out.text
    agent = the_lab.view.dataset(clip_to_analysis(native, tau))
    assert agent.log.horizon == 10.0 * c
    assert agent.excluded == ()
    assert float(agent.log.times.max()) <= 10.0 * c
    for name in ("mean_rate", "inter_arrival_dispersion"):
        shown = the_lab.view.diagnostic(name)
        diag = layer.call("diagnostic", {"name": shown, "data_id": "e0"})
        assert not diag.is_error and num(tau * c) not in diag.text
    kernels = layer.call("nonparam_kernels", {"data_id": "e0"})
    assert num(tau * c) not in kernels.text


def _scaled(design: dict[str, Any], c: float) -> dict[str, Any]:
    parts = []
    for part in design["intervention"]["parts"]:
        part = dict(part)
        part["window"] = [w * c for w in part["window"]]
        if part.get("channel") == "size":
            part["channel"] = "m1"
        parts.append(part)
    return {
        "intervention": {"type": "compose", "parts": parts},
        "horizon": design["horizon"] * c,
    }


def test_anonymised_truncation_texts_do_not_leak() -> None:
    the_lab = lab("AG-c", "anon", experiments=3)
    c = the_lab.view.time(1.0)
    layer = the_lab.layer()
    outputs = {
        "truncated": layer.call("run_experiment", _anon_explosive(c)).text,
        "censored": layer.call("run_experiment", _scaled(CENSORED_TAIL, c)).text,
        "kernels": layer.call("nonparam_kernels", {"data_id": "e1"}).text,
    }
    assert all("truncated:" in outputs[k] for k in ("truncated", "censored"))
    terms = leak_terms(POINTPROC, tuple(ch.name for ch in v2.CHANNELS))
    assert_no_leaks(outputs, terms, numeric_constants=(300.0,))


def test_shown_truncation_rule() -> None:
    """The rule on its own: tau, unless an excluded window holds it."""
    log = EventLog.create(np.array([1.0, 2.0, 5.0]), {}, 10.0)
    plain = Dataset.create(log, [True] * 3, ((math.nextafter(5.0, 10.0), 10.0),), "x")
    assert shown_truncation(plain, 5.0) == 5.0
    hidden = Dataset.create(log, [True] * 3, ((3.0, 10.0),), "x")
    assert shown_truncation(hidden, 7.5) == 3.0
    # A window closed at tau (events at a window's end are excluded too).
    edge = Dataset.create(log, [True] * 3, ((4.0, 5.0),), "x")
    assert shown_truncation(edge, 5.0) == 4.0
    clipped = clip_to_analysis(hidden, 7.5)
    assert clipped.log.horizon == 3.0 and clipped.log.n == 2
    assert clipped.excluded == ()
    early = Dataset.create(log, [True] * 3, ((0.5, 1.5), (3.0, 10.0)), "x")
    assert clip_to_analysis(early, 7.5).excluded == ((0.5, 1.5),)


def test_predictions_and_sandbox_files_see_the_clipped_dataset(
    tmp_path: Path,
) -> None:
    the_lab = lab("AG-o", sandbox_root=tmp_path, image="sciagent-sandbox:unused")
    assert the_lab.sandbox is not None
    layer = the_lab.layer()
    committed = layer.call(
        "predict",
        {
            "experiment": 0,
            "diagnostic": "mean_rate",
            "hypothesis": "null",
            "interval": [0.0, 1e6],
            "level": 0.9,
        },
    )
    assert not committed.is_error, committed.text
    out = layer.call("run_experiment", CENSORED_TAIL)
    assert not out.is_error, out.text
    assert out.record is not None
    native = the_lab.world.datasets["e0"]
    clipped = clip_to_analysis(native, out.record["truncated_at"])
    assert clipped.log.horizon == 10.0
    # The prediction is evaluated on [0, 10], not on the planned [0, 300].
    value = out.record["predictions"][0]["value"]
    assert value == clipped.log.n / 10.0
    # The sandbox's data file holds exactly the clipped events.
    with np.load(the_lab.sandbox.data_dir / "e0.npz") as files:
        assert files["times"].tobytes() == clipped.log.times.tobytes()


def test_a_run_cut_inside_a_window_from_time_zero_has_nothing_to_analyse() -> None:
    # The whole run censored: tau is hidden and the window holding it starts
    # at 0, so nothing is shown before the cut. The experiment is still
    # charged and kept (it can be fitted); analysis is refused in words.
    the_lab = lab("AG-c", experiments=2)
    layer = the_lab.layer()
    committed = layer.call(
        "predict",
        {
            "experiment": 0,
            "diagnostic": "mean_rate",
            "hypothesis": "null",
            "interval": [0.5, 2.0],
            "level": 0.5,
        },
    )
    assert not committed.is_error, committed.text
    design = copy.deepcopy(CENSORED_TAIL)
    design["intervention"]["parts"][1]["window"] = [0.0, 300.0]
    out = layer.call("run_experiment", design)
    assert not out.is_error, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 1
    assert out.record is not None
    tau = out.record["truncated_at"]
    assert out.record["analysis_horizon"] == 0.0
    assert num(tau) not in out.text
    assert "nothing was observed before the cut" in out.text
    assert out.record["predictions"][0]["value"] is None
    for tool, args in (
        ("diagnostic", {"name": "mean_rate", "data_id": "e0"}),
        ("nonparam_kernels", {"data_id": "e0"}),
    ):
        refused = layer.call(tool, args)
        assert refused.is_error and "holds nothing to analyse" in refused.text
        assert num(tau) not in refused.text


def test_a_capped_experiment_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    script = (
        Step(calls=(("run_experiment", EXPLOSIVE),)),
        Step(calls=(("diagnostic", {"name": "mean_rate", "data_id": "e0"}),)),
        Step(
            calls=(
                (
                    "submit",
                    {"structure": "Excite(ExpK, Mark(size), all)", "report": "done"},
                ),
            )
        ),
    )
    first = scripted_run(script, tmp_path / "a")
    assert first.experiments_used == 1
    record = SessionRecord.load(first.record_path)
    runs = [
        e.body
        for e in record.of_kind("tool_call")
        if e.body["name"] == "run_experiment"
    ]
    assert len(runs) == 1 and not runs[0]["is_error"]
    assert runs[0]["record"]["n_events"] == MAX_EVENTS
    assert runs[0]["record"]["truncated_at"] > 0.0
    report = replay_investigation(record, POINTPROC)
    assert report.budgets_used == {"experiments": 1, "fits": 0, "submit": 1}


# -- the timing guard -------------------------------------------------------

#: dev-005 of the dev split (seed 2026), the truth whose stopped experiments
#: ran 11-28 minutes each in the pilot: two GammaK and one PowerK excitation,
#: all summed over the whole history on every thinning candidate.
_HEAVY_DSL: Final = (
    "link=softplus; Excite(GammaK, Mark(sign), all) + Product(Periodic, "
    "Gate(Excite(PowerK, One, all), LastMarkAbove(size))) + "
    "Gate(Excite(GammaK, Mark(size), sign=-), PhaseWindow)"
)


def _heavy_truth() -> TruthSpec:
    psi = (
        {
            PsiSlot((0,), "gamma_shape"): 2.0,
            PsiSlot((0,), "gamma_mean"): 1.0,
        },
        {
            PsiSlot((0,), "period"): 10.0,
            PsiSlot((1, 0, 0), "power_c"): 1.0,
            PsiSlot((1, 0, 0), "power_p"): 2.0,
            PsiSlot((1, 1), "above_z"): 1.5,
        },
        {
            PsiSlot((0, 0), "gamma_shape"): 3.0,
            PsiSlot((0, 0), "gamma_mean"): 2.0,
            PsiSlot((1,), "period"): 10.0,
            PsiSlot((1,), "phase"): math.pi,
        },
    )
    coef = Coefficients(
        0.413723651321775,
        (
            (1.606948973390006,),
            (2.251615006896558, -3.4702210902270076),
            (-0.8160513361658541,),
        ),
    )
    return TruthSpec(
        parse(_HEAVY_DSL, v2.CHANNELS), psi, coef, v2.CHANNELS, v2.mark_sampler
    )


@pytest.mark.slow
def test_an_adversarial_design_on_a_heavy_truth_stops_within_minutes() -> None:
    world = World(_heavy_truth(), seed=1)
    # The longest horizon with every generated event's size forced to 3: the
    # process runs away slowly, which under the old 50,000-event cap took
    # (under the pilot's load) up to half an hour.
    design = Experiment(InjectMarks((0.0, MAX_HORIZON), "size", 3.0), MAX_HORIZON)
    start = time.perf_counter()
    with pytest.raises(ExplosionError):
        world.run(design)
    elapsed = time.perf_counter() - start
    assert world.experiments_run == 1
    assert elapsed < 180.0, f"a capped experiment took {elapsed:.0f} s"
