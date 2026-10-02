"""An experiment's simulation cannot outrun the investigation (LOG 2026-10-02).

``run_experiment`` runs the simulator inside a tool call, which cannot be
cancelled, so the simulation's *work* has to be bounded, and deterministically
(invariant 3): a wall-clock limit would make the outcome depend on the
machine. The bound is the world's event cap, :data:`MAX_EVENTS`, together
with the simulator's candidate cap and runaway guard, which scale with it.
These tests check that the cap is sized against the intervention caps, that a
capped experiment is charged, described in the agent's terms and identical on
every run, that its record replays, and (``slow``) that an adversarial design
on a heavy power/gamma-kernel truth stops within minutes.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Final

import pytest
from investigation_support import POINTPROC, clean_environment, lab, scripted_run

from environments.pointproc import v2
from sciagent.glm.grammar import PsiSlot
from sciagent.glm.interventions import (
    MAX_CLAMP_EVENTS,
    MAX_FORCED_EVENTS,
    MAX_HORIZON,
    MAX_MARK_Z,
    Experiment,
    InjectMarks,
)
from sciagent.glm.simulate import Coefficients, ExplosionError
from sciagent.glm.syntax import parse
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation import TruthSpec, replay_investigation
from sciagent.investigation.tools import BUDGET_EXPERIMENTS
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

STOP_TEXT: Final = (
    "experiment e0 was stopped and produced no dataset: the process exceeded "
    f"the simulation limit of {MAX_EVENTS} events per experiment (it produced "
    "more, or its event rate grew without bound). It counts against the budget."
)


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


def test_a_capped_experiment_is_charged_neutral_and_deterministic() -> None:
    outputs = []
    for _ in range(2):
        layer = lab("AG-c", experiments=2).layer()
        out = layer.call("run_experiment", EXPLOSIVE)
        assert layer.meter(BUDGET_EXPERIMENTS).used == 1
        outputs.append(out)
        # The index is consumed and no dataset is left behind: the next
        # experiment is e1, and e0 is not a data id.
        nxt = layer.call(
            "run_experiment",
            {"intervention": {"type": "compose", "parts": []}, "horizon": 100.0},
        )
        assert "experiment e1 done" in nxt.text, nxt.text
        missing = layer.call("diagnostic", {"name": "mean_rate", "data_id": "e0"})
        assert missing.is_error and "no dataset 'e0'" in missing.text
    first, second = outputs
    assert not first.is_error
    assert first.text == STOP_TEXT
    assert first.record is not None and first.record["stopped"] is True
    assert (first.text, first.record) == (second.text, second.record)


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


def test_the_stop_text_is_the_same_in_both_conditions() -> None:
    anon = lab("AG-c", "anon")
    factor = anon.view.time(1.0)
    assert factor != 1.0, "the anonymised condition must rescale time here"
    args = {
        "intervention": {
            "type": "inject_marks",
            "window": [0.0, 300.0 * factor],
            "channel": "m1",
            "value": 3.0,
        },
        "horizon": 300.0 * factor,
    }
    out = anon.layer().call("run_experiment", args)
    # Event counts are unit-free and the simulator's own message (native
    # times) is never shown, so the text carries nothing condition-specific.
    assert out.text == STOP_TEXT


def test_a_capped_experiment_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    script = (
        Step(calls=(("run_experiment", EXPLOSIVE),)),
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
    stopped = [
        e.body
        for e in record.of_kind("tool_call")
        if e.body["name"] == "run_experiment"
    ]
    assert [b["record"]["stopped"] for b in stopped] == [True]
    assert stopped[0]["text"] == STOP_TEXT
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
