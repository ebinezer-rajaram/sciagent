"""Instrument test 10b end to end, and positive control 6 (P4 Δ4, SPEC §4.4).

A scripted agent with a known belief timeline runs through the live loop
(:class:`ScriptedDriver` → :func:`run_investigation` → the real tool layer
over a real world). The saved :class:`SessionRecord` is read by
:func:`timeline`, and every §4.4 measure computed from it must equal the value
worked out by hand below. Outcomes do not depend on the simulated data: one
interval, ``[-2, -1]``, cannot hold a mean rate (never covered), and the other,
``[0, 1e6]``, always does.

The script (experiments budget 3; t = the experiment clock):

====  =====================================================  ======
call  what                                                   t
====  =====================================================  ======
1     run_experiment with no belief: refused, uncharged
2     belief  A, rivals [B], 0.6                             0
3     predict e0 under A: [-2, -1], level 0.9 (missed)
4     predict e0 under B (spelled differently): [0, 1e6], 0.8
5     run_experiment e0                                      0
6     run_experiment again: refused (stale belief)
7     belief  B, rivals [A, C], 0.7                          1
8     predict e1 under B: [0, 1e6], level 0.5 (covered)
9     run_experiment e1                                      1
10    belief  A, rivals [B], 0.55                            2
11    run_experiment e2 (no predictions)                     2
12    submit C with no fresh belief: refused
13    belief  C, rivals [A], 0.8                             3
14    submit C, stop_reason "budget"                         3
====  =====================================================  ======

By hand:

- **Calibration.** Three evaluated predictions, two covered: coverage 2/3,
  mean level (0.9 + 0.8 + 0.5) / 3 = 11/15, error 2/3 - 11/15 = -1/15. Per
  hypothesis: A n=1, coverage 0, level 0.9; B n=2, coverage 1, level 0.65.
- **Falsification-seeking.** e0: favoured A, runner-up B, disjoint intervals
  on one statistic, so it could refute. e1: B predicted alone, so
  confirmation-only. e2: unpredicted. Share 1/3.
- **Revision (k = 1).** One falsification: A on e0 at t = 0. The belief at
  t = 1 favours B, so it is revised. A is displaced (t = 1) and favoured again
  (t = 2): a zombie. Rates 1 and 1.
- **Favoured path** after 0..3 experiments: A, B, A, C.
- **Stopping**, with the held-out gap assigned per label as A -0.1, B -0.05,
  C 0: gaps (-0.1, -0.05, -0.1, 0); the last change is at 3, so it converged
  at 3, burned 0, not early (the budget was used), used share 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
from investigation_support import clean_environment, lab, plain_run, scripted_run

from environments.pointproc import v2
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation.timeline import canonical_label, timeline
from sciagent.investigation.view import Condition
from sciagent.scoring.behaviour import (
    Belief,
    ExperimentRun,
    StoppingRun,
    calibration,
    falsification_seeking,
    revision,
    stopping,
)

CHANNELS: Final = v2.CHANNELS
A_NATIVE: Final = "Excite(ExpK, Mark(size), all)"
B_NATIVE: Final = "link=identity; Excite(ExpK, One, all) + Trend"
B_SPELLED: Final = "Trend + Excite(ExpK, One, all)"
C_NATIVE: Final = "link=exp; Excite(ExpK, One, sign=+)"
A: Final = canonical_label(A_NATIVE, CHANNELS)
B: Final = canonical_label(B_NATIVE, CHANNELS)
C: Final = canonical_label(C_NATIVE, CHANNELS)
MISS: Final = (-2.0, -1.0)
HIT: Final = (0.0, 1e6)


def _agent(text: str, condition: Condition) -> str:
    if condition == "named":
        return text
    return text.replace("size", "m1").replace("sign", "m2")


def _script(condition: Condition, rate: str, horizon: float) -> list[Step]:
    def belief(fav: str, rivals: list[str], conf: float) -> Step:
        return Step(
            calls=(
                (
                    "belief",
                    {
                        "favoured": _agent(fav, condition),
                        "rivals": [_agent(r, condition) for r in rivals],
                        "confidence": conf,
                    },
                ),
            )
        )

    def predict(
        e: int, hyp: str, interval: tuple[float, float], level: float
    ) -> tuple[str, dict[str, Any]]:
        return (
            "predict",
            {
                "experiment": e,
                "diagnostic": rate,
                "hypothesis": _agent(hyp, condition),
                "interval": list(interval),
                "level": level,
                "note": "scripted",
            },
        )

    run = ("run_experiment", plain_run(horizon))
    return [
        Step(calls=(run,)),  # 1: refused, no belief
        belief(A_NATIVE, [B_NATIVE], 0.6),  # 2
        Step(
            calls=(
                predict(0, A_NATIVE, MISS, 0.9),  # 3
                predict(0, B_SPELLED, HIT, 0.8),  # 4
            )
        ),
        Step(calls=(run,)),  # 5: e0
        Step(calls=(run,)),  # 6: refused, stale
        belief(B_SPELLED, [A_NATIVE, C_NATIVE], 0.7),  # 7
        Step(calls=(predict(1, B_NATIVE, HIT, 0.5),)),  # 8
        Step(calls=(run,)),  # 9: e1
        belief(A_NATIVE, [B_NATIVE], 0.55),  # 10
        Step(calls=(run,)),  # 11: e2
        Step(  # 12: refused, stale
            calls=(
                (
                    "submit",
                    {
                        "structure": _agent(C_NATIVE, condition),
                        "report": "r",
                        "stop_reason": "budget",
                    },
                ),
            )
        ),
        belief(C_NATIVE, [A_NATIVE], 0.8),  # 13
        Step(  # 14
            calls=(
                (
                    "submit",
                    {
                        "structure": _agent(C_NATIVE, condition),
                        "report": "r",
                        "stop_reason": "budget",
                        "note": "three experiments used",
                    },
                ),
            )
        ),
        Step(text="done"),
    ]


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_10b_scripted_timeline_gives_the_hand_computed_measures(
    condition: Condition, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    view = lab("AG-c", condition).view
    script = _script(condition, view.diagnostic("mean_rate"), view.time(300.0))
    outcome = scripted_run(script, tmp_path, condition=condition, experiments=3)
    assert outcome.outcome == "submitted"
    assert outcome.experiments_used == 3
    record = SessionRecord.load(outcome.record_path)

    # The refusals are in the record, uncharged, and nothing else failed.
    calls = [e.body for e in record.of_kind("tool_call")]
    errors = [c["call"] for c in calls if c["is_error"]]
    assert errors == [0, 5, 11], [(c["call"], c["text"]) for c in calls]
    assert all(calls[i]["charged"] is None for i in errors)
    submit = calls[-1]
    assert submit["record"]["stop_reason"] == "budget"
    assert submit["record"]["note"] == "three experiments used"

    line = timeline(record, CHANNELS)
    assert line.experiments == (
        ExperimentRun(0, 0),
        ExperimentRun(1, 1),
        ExperimentRun(2, 2),
    )
    assert line.beliefs == (
        Belief(0, A, (B,), 0.6),
        Belief(1, B, (A, C), 0.7),
        Belief(2, A, (B,), 0.55),
        Belief(3, C, (A,), 0.8),
        Belief(3, C),
    )
    assert line.submitted == C
    assert line.favoured_path() == (A, B, A, C)
    assert [p.hypothesis for p, _ in line.predictions] == [A, B, B]

    cal = calibration(line.predictions)
    assert (cal.n, cal.unevaluated, cal.covered) == (3, 0, 2)
    assert cal.coverage == pytest.approx(2 / 3)
    assert cal.mean_level == pytest.approx(11 / 15)
    assert cal.error == pytest.approx(-1 / 15)
    per = {h.hypothesis: (h.n, h.coverage, h.mean_level) for h in cal.per_hypothesis}
    assert set(per) == {A, B}
    assert per[A][:2] == (1, 0.0) and per[A][2] == pytest.approx(0.9)
    assert per[B][:2] == (2, 1.0) and per[B][2] == pytest.approx(0.65)

    fal = falsification_seeking(line.predictions, line.experiments, line.beliefs)
    assert (fal.experiments, fal.refuting, fal.confirmation_only, fal.unpredicted) == (
        3,
        1,
        1,
        1,
    )
    assert fal.share == pytest.approx(1 / 3)
    assert [
        (r.index, r.favoured, r.runner_up, r.can_refute) for r in fal.per_experiment
    ] == [
        (0, A, B, True),
        (1, B, A, False),
        (2, A, B, False),
    ]

    rev = revision(line.predictions, line.experiments, line.beliefs)
    assert (rev.falsified, rev.revised, rev.zombies) == (1, 1, 1)
    assert (rev.rate, rev.zombie_rate) == (1.0, 1.0)
    (event,) = rev.events
    assert (event.experiment, event.turn, event.hypothesis) == (0, 0, A)

    gap = {A: -0.1, B: -0.05, C: 0.0}
    path = line.favoured_path()
    run = StoppingRun(
        experiments_used=3,
        budget=3,
        gaps=tuple(gap[label] for label in path if label is not None),
    )
    (stop,) = stopping([run]).runs
    assert (stop.converged_at, stop.burned, stop.early) == (3, 0, False)
    assert stop.used_share == 1.0
