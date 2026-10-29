"""Instrument test 10b, offline half (P4 Δ4): the belief timeline.

A hand-built :class:`SessionRecord` (a scripted agent's ``tool_call`` entries,
written the way the tool layer writes them) is read into the SPEC §4.4 inputs,
and every §4.4 measure computed from it is compared with values worked out by
hand below. Nothing here runs a model or a simulator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest

from environments.pointproc import v2
from sciagent.glm.canonical import canonicalise
from sciagent.glm.syntax import parse, render
from sciagent.harness.errors import RecordFormatError
from sciagent.harness.record import SessionRecord
from sciagent.investigation.timeline import Timeline, timeline
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
STAT: Final = '{"args":{},"diagnostic":"mean_rate"}'


def label(text: str) -> str:
    """The canonical native text, computed independently of the timeline."""
    return render(canonicalise(parse(text, CHANNELS)))


# Three hypotheses. Each is written below in several spellings; the timeline
# must give each one label.
A: Final = label("link=identity; Excite(ExpK, One, all)")
B: Final = label("link=identity; Excite(ExpK, One, all) + Trend")
C: Final = label("link=exp; Excite(ExpK, Mark(size), all)")
B_SPELLED: Final = "Trend + Excite(ExpK, One, all)"  # non-canonical B
assert B_SPELLED != B and label(B_SPELLED) == B


# --------------------------------------------------------------------------
# Writing tool_call entries the way the tool layer writes them
# --------------------------------------------------------------------------


class Script:
    """Builds a record call by call; ``call`` is the layer's call index."""

    def __init__(self) -> None:
        self.record = SessionRecord()
        self.record.append("config", {"script": "10b"})
        self.calls = 0
        self.experiments = 0
        self.commitments = 0
        self.pending: dict[int, list[dict[str, Any]]] = {}

    def _call(
        self,
        name: str,
        args: dict[str, Any],
        record: dict[str, Any] | None,
        *,
        is_error: bool = False,
    ) -> None:
        self.record.append(
            "tool_call",
            {
                "call": self.calls,
                "tool_use_id": f"toolu_{self.calls}",
                "name": name,
                "args": args,
                "text": "shown to the agent",
                "is_error": is_error,
                "record": record,
                "digest": "0" * 64,
                "charged": name == "run_experiment",
                "budgets_after": {},
                "shown_matches": True,
            },
        )
        self.calls += 1

    def belief(
        self,
        favoured: str,
        rivals: list[str],
        confidence: float,
        *,
        args_extra: dict[str, Any] | None = None,
    ) -> None:
        args = {"favoured": favoured, "rivals": rivals, "confidence": confidence}
        args.update(args_extra or {})
        index = sum(1 for e in self.record.of_kind("tool_call") if _ok(e, "belief"))
        self._call(
            "belief",
            args,
            {
                "belief": index,
                "favoured": favoured,
                "rivals": rivals,
                "confidence": confidence,
                "experiments_run": self.experiments,
            },
        )

    def predict(
        self, experiment: int, hypothesis: str, low: float, high: float, level: float
    ) -> int:
        commitment = self.commitments
        self.commitments += 1
        self._call(
            "predict",
            {
                "experiment": experiment,
                "diagnostic": "mean_rate",
                "hypothesis": hypothesis,
                "interval": [low, high],
                "level": level,
            },
            {
                "commitment": commitment,
                "experiment": experiment,
                "hypothesis": hypothesis,
            },
        )
        self.pending.setdefault(experiment, []).append(
            {
                "commitment": commitment,
                "experiment": experiment,
                "statistic": STAT,
                "hypothesis": hypothesis,
                "interval": [low, high],
                "level": level,
            }
        )
        return commitment

    def run_experiment(self, values: dict[int, float | None]) -> None:
        """Run the next experiment; ``values`` maps commitment -> observed value."""
        index = self.experiments
        outcomes = []
        for p in self.pending.pop(index, []):
            value = values[p["commitment"]]
            low, high = p["interval"]
            covered = value is not None and low <= value <= high
            outcomes.append({**p, "value": value, "covered": covered, "error": None})
        self._call(
            "run_experiment",
            {"horizon": 100},
            {
                "experiment": index,
                "design": "{}",
                "horizon": 100.0,
                "n_events": 50,
                "predictions": outcomes,
            },
        )
        self.experiments += 1

    def submit(self, structure: str) -> None:
        self._call(
            "submit",
            {"structure": structure, "report": "done"},
            {"structure": structure, "structure_hash": "x"},
        )

    def error(self, name: str, args: dict[str, Any]) -> None:
        self._call(name, args, None, is_error=True)


def _ok(entry: Any, name: str) -> bool:
    return bool(entry.body["name"] == name and not entry.body["is_error"])


def scripted_session(*, bogus_turns: bool = False) -> SessionRecord:
    """The 10b session. Hand-computed values are in the tests below.

    ``bogus_turns`` adds agent arguments that claim a turn or an experiment
    count; they must change nothing.
    """
    extra = {"turn": 99, "experiments_run": 7} if bogus_turns else None
    s = Script()
    # Turn 0 (no experiment run yet): favours A over B and C.
    s.belief(A, [B, C], 0.6, args_extra=extra)
    s.predict(0, A, 0.0, 1.0, 0.9)  # commitment 0
    s.predict(0, B, 2.0, 3.0, 0.9)  # commitment 1: disjoint from A's
    s.record.append("assistant", {"text": "not a tool call"})
    # Experiment 0 at turn 0: A is falsified (2.5 not in [0, 1]), B covered.
    s.run_experiment({0: 2.5, 1: 2.5})
    # Turn 1: revised to B (a non-canonical spelling), A is the runner-up.
    s.belief(B_SPELLED, ["Excite(ExpK,One,all)"], 0.7, args_extra=extra)
    s.predict(1, B, 2.0, 4.0, 0.5)  # commitment 2
    s.predict(1, A, 3.0, 5.0, 0.5)  # commitment 3: overlaps B's
    s.error("belief", {"favoured": "garbage"})  # refused: not in the timeline
    # Experiment 1 at turn 1: B falsified (4.5 not in [2, 4]), A covered.
    s.run_experiment({2: 4.5, 3: 4.5})
    # Turn 2: B kept, against C.
    s.belief(B, [C], 0.55, args_extra=extra)
    s.error("run_experiment", {"horizon": -1})  # refused: not an experiment
    # Experiment 2 at turn 2: no predictions.
    s.run_experiment({})
    # Turn 3: A comes back after it was falsified and displaced (a zombie).
    s.belief(A, [B], 0.8, args_extra=extra)
    s.predict(3, A, 0.0, 1.0, 0.9)  # commitment 4: experiment 3 never runs
    s.submit(A)
    return s.record


# --------------------------------------------------------------------------
# The timeline itself
# --------------------------------------------------------------------------


def test_timeline_reads_experiments_beliefs_and_submission() -> None:
    t = timeline(scripted_session(), CHANNELS)
    assert t.experiments == (
        ExperimentRun(0, 0),
        ExperimentRun(1, 1),
        ExperimentRun(2, 2),
    )
    assert t.beliefs == (
        Belief(0, A, (B, C), confidence=0.6),
        Belief(1, B, (A,), confidence=0.7),
        Belief(2, B, (C,), confidence=0.55),
        Belief(3, A, (B,), confidence=0.8),
        Belief(3, A),  # the submission, the last statement of belief
    )
    assert t.submitted == A
    assert t.favoured_path() == (A, B, B, A)


def test_timeline_reads_predictions_in_commitment_order() -> None:
    t = timeline(scripted_session(), CHANNELS)
    got = [
        (
            p.experiment,
            p.hypothesis,
            p.interval,
            p.level,
            None if e is None else (e.commitment, e.value, e.covered),
        )
        for p, e in t.predictions
    ]
    assert got == [
        (0, A, (0.0, 1.0), 0.9, (0, 2.5, False)),
        (0, B, (2.0, 3.0), 0.9, (1, 2.5, True)),
        (1, B, (2.0, 4.0), 0.5, (2, 4.5, False)),
        (1, A, (3.0, 5.0), 0.5, (3, 4.5, True)),
        (3, A, (0.0, 1.0), 0.9, None),
    ]


def test_non_canonical_spellings_get_one_label() -> None:
    s = Script()
    s.belief("Trend + Excite(ExpK, One, all)", ["Product(Trend, Periodic)"], 0.5)
    s.predict(0, "link=identity;Excite(ExpK,One,all)+Trend", 0.0, 1.0, 0.9)
    s.predict(0, "Product(Trend,Periodic)", 2.0, 3.0, 0.9)
    s.run_experiment({0: 0.5, 1: 0.5})
    s.submit("  link=identity ; Trend+Excite( ExpK , One , all )")
    t = timeline(s.record, CHANNELS)
    product = label("Product(Periodic, Trend)")
    assert t.beliefs[0].favoured == B
    assert t.beliefs[0].rivals == (product,)
    assert [p.hypothesis for p, _ in t.predictions] == [B, product]
    assert t.submitted == B
    # So the pair is recognised as favoured vs runner-up, and could refute.
    f = falsification_seeking(t.predictions, t.experiments, t.beliefs)
    assert f.refuting == 1


def test_turns_ignore_agent_arguments() -> None:
    plain = timeline(scripted_session(), CHANNELS)
    bogus = timeline(scripted_session(bogus_turns=True), CHANNELS)
    assert [b.turn for b in bogus.beliefs] == [0, 1, 2, 3, 3]
    assert bogus.beliefs == plain.beliefs
    assert bogus.experiments == plain.experiments


def test_turns_follow_call_order_not_entry_order() -> None:
    # Two results recorded out of call order (parallel tool use): the clock is
    # the layer's call index, which is the order the framework executed them.
    s = Script()
    s.run_experiment({})
    s.belief(A, [B], 0.5)
    entries = [e.body for e in s.record.of_kind("tool_call")]
    swapped = SessionRecord()
    for body in reversed(entries):
        swapped.append("tool_call", body)
    t = timeline(swapped, CHANNELS)
    assert t.beliefs == (Belief(1, A, (B,), confidence=0.5),)


def test_a_record_whose_count_disagrees_is_refused() -> None:
    s = Script()
    s.experiments = 2  # the belief record claims two experiments had run
    s.belief(A, [B], 0.5)
    with pytest.raises(RecordFormatError, match="experiments_run"):
        timeline(s.record, CHANNELS)


def test_experiments_out_of_sequence_are_refused() -> None:
    s = Script()
    s.experiments = 1
    s.run_experiment({})  # records experiment 1 as the first experiment
    with pytest.raises(RecordFormatError, match="experiment"):
        timeline(s.record, CHANNELS)


def test_an_unparseable_hypothesis_is_refused() -> None:
    s = Script()
    s.belief("Excite(ExpK, One, all), I think", [B], 0.5)
    with pytest.raises(RecordFormatError, match="favoured"):
        timeline(s.record, CHANNELS)


def test_an_empty_session_gives_an_empty_timeline() -> None:
    t = timeline(SessionRecord(), CHANNELS)
    assert t == Timeline((), (), (), None)
    assert t.favoured_path() == (None,)


# --------------------------------------------------------------------------
# 10b: known §4.4 values from the scripted session
# --------------------------------------------------------------------------


def test_10b_calibration() -> None:
    t = timeline(scripted_session(), CHANNELS)
    c = calibration(t.predictions)
    # Evaluated: commitments 0..3; covered: 1 and 3. Levels .9 .9 .5 .5.
    assert (c.n, c.unevaluated, c.covered) == (4, 1, 2)
    assert c.coverage == 0.5
    assert c.mean_level == pytest.approx(0.7)
    assert c.error == pytest.approx(-0.2)
    per = {h.hypothesis: h for h in c.per_hypothesis}
    assert list(per) == sorted([A, B])
    # A: commitment 0 missed (.9), 3 covered (.5). B: 1 covered (.9), 2 missed (.5).
    assert (per[A].n, per[A].coverage) == (2, 0.5)
    assert (per[B].n, per[B].coverage) == (2, 0.5)
    assert per[A].mean_level == pytest.approx(0.7)
    assert per[B].mean_level == pytest.approx(0.7)


def test_10b_falsification_seeking() -> None:
    t = timeline(scripted_session(), CHANNELS)
    f = falsification_seeking(t.predictions, t.experiments, t.beliefs)
    # e0: A vs runner-up B, [0,1] vs [2,3] disjoint -> refuting.
    # e1: B vs runner-up A, [2,4] vs [3,5] overlap -> confirmation only.
    # e2: B favoured, no predictions -> unpredicted.
    assert (f.experiments, f.refuting, f.confirmation_only, f.unpredicted) == (
        3,
        1,
        1,
        1,
    )
    assert f.share == pytest.approx(1 / 3)
    assert [(r.favoured, r.runner_up) for r in f.per_experiment] == [
        (A, B),
        (B, A),
        (B, C),
    ]


def test_10b_revision_and_zombies() -> None:
    t = timeline(scripted_session(), CHANNELS)
    r = revision(t.predictions, t.experiments, t.beliefs, k=1)
    # Falsified: A on e0 (turn 0) and B on e1 (turn 1).
    # A: the turn-1 belief favours B -> revised; A is back at turn 3 -> zombie.
    # B: the turn-2 belief still favours B -> not revised within k = 1; it is
    #    displaced at turn 3 and never returns -> not a zombie.
    assert [(e.experiment, e.hypothesis, e.revised, e.zombie) for e in r.events] == [
        (0, A, True, True),
        (1, B, False, False),
    ]
    assert (r.falsified, r.revised, r.zombies) == (2, 1, 1)
    assert r.rate == 0.5 and r.zombie_rate == 0.5
    # With k = 2 the turn-3 belief is in B's window.
    r2 = revision(t.predictions, t.experiments, t.beliefs, k=2)
    assert (r2.revised, r2.rate) == (2, 1.0)


def test_10b_stopping() -> None:
    # The gap of each favoured structure would come from a held-out refit;
    # here they are given, so the read-out is known: path A, B, B, A.
    t = timeline(scripted_session(), CHANNELS)
    gap = {A: -0.1, B: -0.002}
    path = t.favoured_path()
    run = StoppingRun(
        len(t.experiments), 8, tuple(gap[h] for h in path if h is not None)
    )
    s = stopping([run])
    out = s.runs[0]
    # Final gap -0.1; the gap after 2 experiments (-0.002) is 0.098 away, so
    # it converged only at 3: nothing burned, and it stopped early (3 < 8 with
    # a final gap below -0.005).
    assert (out.converged_at, out.burned, out.early) == (3, 0, True)
    assert out.used_share == pytest.approx(3 / 8)
    assert s.early_share == 1.0


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_timeline_is_deterministic_through_save_and_load(tmp_path: Path) -> None:
    record = scripted_session()
    path = tmp_path / "session.json"
    record.save(path)
    loaded = SessionRecord.load(path)
    first = timeline(record, CHANNELS)
    second = timeline(loaded, CHANNELS)
    third = timeline(scripted_session(), CHANNELS)
    assert repr(first) == repr(second) == repr(third)
