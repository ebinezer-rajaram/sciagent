"""Policy mode of the tool layer (P4 Δ11, SPEC §4.2; WP10): AG-c-S with the
random, EIG and FSD policies, and AG-S-boed = AG-c-S + EIG, named.

In policy mode ``run_experiment`` takes no design: the framework fits the
latest belief's structures (uncharged, never shown), lets the policy choose a
(design, diagnostic) cell, runs the design through the world exactly as an
agent-chosen experiment is run and charged, and shows the design and the
chosen diagnostic's value. These are instruments (a tool budget, the sandbox
of what the agent may see, replay), so each property is tested here:

- invariant 2: no EIG or FSD value, weight, BIC of the entertained set or menu
  ranking reaches any text the agent reads (with a positive control showing
  the scan finds a planted value);
- charges equal an agent-chosen run's, call for call;
- a policy session replays byte for byte and a forged policy field is caught;
- a double run is byte-identical;
- the world's truncation, not the policy's undefined-on-stop, decides what the
  real experiment gives;
- the framework-only decision (``EIGSummary`` and the table) is in the record.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest
from investigation_support import (
    BELIEF,
    POINTPROC,
    SMALL_POLICY,
    STOP,
    clean_environment,
    lab,
    plain_run,
    scripted_run,
)

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.core.errors import SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitError
from sciagent.glm.interventions import Experiment
from sciagent.glm.simulate import BoundViolationError, NegativeIntensityError
from sciagent.harness.errors import ReplayDivergenceError
from sciagent.harness.record import SessionRecord, digest
from sciagent.harness.scripted import Step
from sciagent.investigation import (
    InvestigationOutcome,
    InvestigationSpec,
    policy,
    replay_investigation,
    runner,
    world,
)
from sciagent.investigation.policy import PolicyModeError, PolicySettings
from sciagent.investigation.prompts import num
from sciagent.investigation.runner import leak_terms, rendered_surface
from sciagent.investigation.tools import LabError
from sciagent.systems.v2.policies import (
    DecisionContext,
    EntertainedSet,
    Hypothesis,
    Menu,
    PolicyError,
    RandomPolicy,
    design_menu,
    diagnostic_menu,
)

#: Words that would name a policy's internals in an agent-visible text.
INTERNALS: Final = re.compile(
    r"\b(eig|fsd|weights?|entropy|scores?|rank(ing|ed)?|bic|information gain|"
    r"posterior|degenerate|tied)\b",
    re.IGNORECASE,
)

SUBMIT: Final = (
    "submit",
    {"structure": "Excite(ExpK, One, all)", "report": "done", **STOP},
)


def _policy_script(experiments: int = 2) -> list[Step]:
    steps = [
        Step(
            text="look",
            calls=(("diagnostic", {"name": "mean_rate", "data_id": "obs"}),),
        )
    ]
    steps += [Step(calls=(BELIEF, ("run_experiment", {}))) for _ in range(experiments)]
    steps.append(Step(calls=(BELIEF, SUBMIT)))
    return steps


def _session(
    name: str, out: Path, *, settings: PolicySettings = SMALL_POLICY, **kw: Any
) -> InvestigationOutcome:
    return scripted_run(
        _policy_script(), out, policy=name, policy_settings=settings, **kw
    )


def _calls(outcome: InvestigationOutcome) -> list[Mapping[str, Any]]:
    record = SessionRecord.load(outcome.record_path)
    return [e.body for e in record.of_kind("tool_call")]


def _agent_texts(record: SessionRecord) -> list[str]:
    """Everything the agent reads: prompts, schemas and every tool result."""
    config = record.of_kind("config")[0].body
    texts = [config["system_prompt"], config["prompt"]]
    texts += [json.dumps(schema, sort_keys=True) for schema in config["tools"]]
    texts += [e.body["text"] for e in record.of_kind("tool_call")]
    return texts


def _floats(value: object) -> Iterator[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _floats(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _floats(v)


def _internal_numbers(decision: Mapping[str, Any]) -> set[float]:
    """The framework-only numbers of one decision: scores, weights, BICs, the
    EIG summary. 0 and 1 are left out (they appear legitimately anywhere)."""
    internal = {k: decision[k] for k in ("scores", "entertained", "eig")}
    return {x for x in _floats(internal) if math.isfinite(x) and x not in (0.0, 1.0)}


def _distinctive(token: str) -> bool:
    """A number's text is evidence only with at least four significant digits:
    ``num(0.9999997)`` is ``1``, which every text contains."""
    mantissa = token.lower().split("e")[0]
    return len(mantissa.replace("-", "").replace(".", "").lstrip("0")) >= 4


def _leaks(numbers: set[float], texts: list[str]) -> list[tuple[float, str]]:
    hits = []
    for x in sorted(numbers):
        for token in sorted({repr(x), num(x)}):
            if _distinctive(token) and any(token in t for t in texts):
                hits.append((x, token))
    return hits


def _decisions(record: SessionRecord) -> list[Mapping[str, Any]]:
    return [
        e.body["record"]["policy"]
        for e in record.of_kind("tool_call")
        if e.body["name"] == "run_experiment" and not e.body["is_error"]
    ]


# --------------------------------------------------------------------------
# Scripted sessions, one per policy
# --------------------------------------------------------------------------


def _check_session(outcome: InvestigationOutcome, name: str) -> SessionRecord:
    assert outcome.outcome == "submitted", outcome
    assert not outcome.void, outcome.void_reasons
    assert outcome.experiments_used == 2
    assert outcome.fits_used == 0, "policy fits are framework-internal, uncharged"
    record = SessionRecord.load(outcome.record_path)
    calls = [e.body for e in record.of_kind("tool_call")]
    assert not any(c["is_error"] for c in calls), [
        c["text"] for c in calls if c["is_error"]
    ]
    runs = [c for c in calls if c["name"] == "run_experiment"]
    assert [c["charged"] for c in runs] == ["experiments", "experiments"]
    texts = _agent_texts(record)
    for c in runs:
        assert "design chosen by the framework" in c["text"]
        assert "diagnostic chosen by the framework" in c["text"]
        assert not INTERNALS.search(c["text"]), c["text"]
    decisions = _decisions(record)
    assert [d["policy"] for d in decisions] == [name, name]
    assert [d["decision"] for d in decisions] == [0, 1]
    for d in decisions:
        numbers = _internal_numbers(d)
        assert not _leaks(numbers, texts)
        if name != "random":
            assert any(_distinctive(num(x)) for x in numbers), "nothing to scan for"
            assert d["entertained"]["names"] == [
                "link=identity; null",
                "link=exp; Trend",
            ]
            table = d["scores"]
            assert len(table) == 2 and all(len(row) == 2 for row in table)
        else:
            assert d["scores"] is None and d["entertained"] is None
    return record


def test_random_policy_session_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    _check_session(_session("random", tmp_path), "random")


@pytest.mark.slow
def test_eig_policy_session_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG-S-boed's policy; the decision's EIGSummary is in the record only."""
    clean_environment(monkeypatch)
    record = _check_session(_session("EIG", tmp_path), "EIG")
    for d in _decisions(record):
        summary = d["eig"]
        assert set(summary) == {
            "weights",
            "entropy",
            "max_eig",
            "tolerance",
            "tied",
            "degenerate",
        }
        assert summary["degenerate"] == (summary["max_eig"] <= summary["tolerance"])
        assert summary["weights"] == d["entertained"]["weights"]


@pytest.mark.slow
def test_fsd_policy_session_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    record = _check_session(_session("FSD", tmp_path), "FSD")
    for d in _decisions(record):
        assert d["eig"] is None
        assert d["config"].startswith('{"max_events"')
        assert '"reference":"wiener_hopf"' in d["config"]


@pytest.mark.slow
def test_the_scan_would_catch_a_planted_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the leak scan: a weight or a BIC planted in an
    agent text is found."""
    clean_environment(monkeypatch)
    record = SessionRecord.load(_session("EIG", tmp_path).record_path)
    decision = _decisions(record)[0]
    numbers = _internal_numbers(decision)
    planted = next(x for x in sorted(numbers) if _distinctive(num(x)))
    texts = [*_agent_texts(record), f"note: {num(planted)}"]
    assert _leaks(numbers, texts)
    assert INTERNALS.search("expected information gain 0.3")
    assert INTERNALS.search("FSD = 0.2")


@pytest.mark.slow
def test_a_policy_session_replays_and_detects_a_forged_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    first = _session("EIG", tmp_path / "a")
    second = _session("EIG", tmp_path / "b")
    assert first.head == second.head, "a double run must be byte-identical"
    record = SessionRecord.load(first.record_path)
    report = replay_investigation(record, POINTPROC)
    assert report.budgets_used == {"experiments": 2, "fits": 0, "submit": 1}

    call = next(
        e.body
        for e in record.of_kind("tool_call")
        if e.body["name"] == "run_experiment"
    )
    forged_record = json.loads(json.dumps(call["record"]))
    forged_record["policy"]["design"]["index"] = (
        1 - forged_record["policy"]["design"]["index"]
    )
    forged = SessionRecord()
    for entry in record.entries:
        body = json.loads(json.dumps(entry.body))
        if entry.kind == "tool_call" and body["call"] == call["call"]:
            body["record"] = forged_record
            body["digest"] = digest(
                {
                    "text": body["text"],
                    "is_error": body["is_error"],
                    "record": forged_record,
                }
            )
        forged.append(entry.kind, body)
    with pytest.raises(ReplayDivergenceError) as caught:
        replay_investigation(forged, POINTPROC)
    assert caught.value.step == call["call"]


def test_random_policy_double_run_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    a = _session("random", tmp_path / "a")
    b = _session("random", tmp_path / "b")
    assert a.head == b.head
    replay_investigation(SessionRecord.load(a.record_path), POINTPROC)


# --------------------------------------------------------------------------
# Charges
# --------------------------------------------------------------------------


def test_charges_are_those_of_agent_chosen_experiments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same call pattern -- a refused experiment (no belief), two runs, one
    past the budget, a submit -- charges identically with and without a
    policy."""
    clean_environment(monkeypatch)

    def script(run: Mapping[str, Any]) -> list[Step]:
        return [
            Step(calls=(("run_experiment", run),)),  # refused: no belief
            Step(calls=(BELIEF, ("run_experiment", run))),
            Step(calls=(BELIEF, ("run_experiment", run))),
            Step(calls=(BELIEF, ("run_experiment", run))),  # budget exhausted
            Step(calls=(BELIEF, SUBMIT)),
        ]

    chosen = scripted_run(script(plain_run(2000.0)), tmp_path / "agent", experiments=2)
    picked = scripted_run(
        script({}),
        tmp_path / "policy",
        experiments=2,
        policy="random",
        policy_settings=SMALL_POLICY,
    )

    def charges(o: InvestigationOutcome) -> list[tuple[str, Any, Any, bool]]:
        return [
            (c["name"], c["charged"], c["budgets_after"], c["is_error"])
            for c in _calls(o)
        ]

    assert charges(picked) == charges(chosen)
    assert picked.experiments_used == chosen.experiments_used == 2
    refused = _calls(picked)[0]
    assert refused["is_error"] and "belief" in refused["text"]
    assert "policy" not in (refused["record"] or {})


def test_policy_mode_takes_no_design(tmp_path: Path) -> None:
    the_lab = lab("AG-c", "named", policy="random", policy_settings=SMALL_POLICY)
    layer = the_lab.layer()
    schema = next(s for s in layer.schemas() if s["name"] == "run_experiment")
    assert schema["input_schema"]["properties"] == {}
    assert schema["input_schema"]["required"] == []
    out = layer.call(*BELIEF)
    assert not out.is_error
    out = layer.call("run_experiment", plain_run(300.0))
    assert out.is_error, "a design is refused in policy mode"
    assert the_lab.world.experiments_run == 0


# --------------------------------------------------------------------------
# What the agent is shown, and what the record keeps
# --------------------------------------------------------------------------


def test_the_shown_design_and_value_are_in_the_agents_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the anonymised condition the design is shown in the agent's names
    and units, and the diagnostic's value is the one the `diagnostic` tool
    gives for the shown arguments on the new dataset."""
    clean_environment(monkeypatch)
    the_lab = lab("AG-c", "anon", policy="random", policy_settings=SMALL_POLICY)
    layer = the_lab.layer()
    assert not layer.call(*BELIEF).is_error
    out = layer.call("run_experiment", {})
    assert not out.is_error, out.text
    assert out.record is not None
    decision = out.record["policy"]
    shown = decision["shown"]
    assert shown["design"]["horizon"] == pytest.approx(2000.0 * the_lab.view.factor)
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    assert_no_leaks({"run_experiment": out.text}, terms)
    again = layer.call(
        "diagnostic",
        {
            "name": shown["diagnostic"]["name"],
            "args": shown["diagnostic"]["args"],
            "data_id": "e0",
        },
    )
    assert not again.is_error, again.text
    assert again.record is not None
    assert again.record["value"] == shown["value"]
    assert num(shown["value"]) in out.text


def test_the_real_experiment_follows_the_world_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The policy's own simulations treat a stop as undefined (WP5), but the
    chosen design runs in the world, which truncates at its cap (Δ2): the run
    is kept and the value is read on [0, τ]."""
    the_lab = lab("AG-c", "named", policy="random", policy_settings=SMALL_POLICY)
    # After the lab is built: the observational log is never truncated.
    monkeypatch.setattr(world, "MAX_EVENTS", 200)
    layer = the_lab.layer()
    assert not layer.call(*BELIEF).is_error
    out = layer.call("run_experiment", {})
    assert not out.is_error, out.text
    assert out.record is not None
    assert "truncated_at" in out.record
    assert "truncated" in out.text
    assert out.record["policy"]["shown"]["value"] is not None
    assert the_lab.world.experiments_run == 1


def test_a_stopped_policy_experiment_is_charged_and_shows_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run the world stops (a runaway guard) uses its index and its budget
    and shows the design, with no diagnostic value, as an agent's would."""

    def stopped(self: world.World, experiment: Experiment) -> Any:
        self._experiments_run += 1
        raise NegativeIntensityError("forced stop")

    the_lab = lab("AG-c", "named", policy="random", policy_settings=SMALL_POLICY)
    layer = the_lab.layer()
    assert not layer.call(*BELIEF).is_error
    monkeypatch.setattr(world.World, "run", stopped)
    out = layer.call("run_experiment", {})
    assert not out.is_error, out.text
    assert layer.meter("experiments").used == 1
    assert out.record is not None and out.record["stopped"] is True
    assert "design chosen by the framework" in out.text
    assert "not available (the experiment was stopped)" in out.text
    assert out.record["policy"]["shown"]["value"] is None


def test_no_fittable_belief_falls_back_to_the_random_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no structure of the belief can be fitted, EIG/FSD cannot decide: the
    framework uses the random draw for that decision and says so in the
    record only."""

    def fails(*args: Any, **kwargs: Any) -> Any:
        raise FitError("forced")

    monkeypatch.setattr(policy, "certified_fit", fails)
    the_lab = lab("AG-c", "named", policy="EIG", policy_settings=SMALL_POLICY)
    layer = the_lab.layer()
    assert not layer.call(*BELIEF).is_error
    out = layer.call("run_experiment", {})
    assert not out.is_error, out.text
    assert out.record is not None
    decision = out.record["policy"]
    assert decision["fallback"] is not None and "random" in decision["fallback"]
    assert decision["scores"] is None and decision["eig"] is None
    assert "fallback" not in out.text and not INTERNALS.search(out.text)


class _NeverRun:
    """A simulator the random policy never calls (it reads the menu only)."""

    def simulate_experiment(
        self, experiment: Experiment, rng: np.random.Generator, *, label: str = "x"
    ) -> Dataset:
        raise AssertionError("the random policy simulated a hypothesis")


def test_the_random_draw_is_the_committed_random_policy() -> None:
    """The helper policy mode uses for the random policy and the fallback is
    :class:`RandomPolicy`'s draw, cell for cell."""
    the_lab = lab("AG-c", "named")
    obs = the_lab.world.datasets["obs"]
    menu = Menu(design_menu(v2.CHANNELS), diagnostic_menu(obs.log, v2.CHANNELS))
    entertained = EntertainedSet.of([Hypothesis("p", _NeverRun(), 0.0)])
    context = DecisionContext(v2.CHANNELS, obs, entertained, menu)
    for decision in range(20):
        want = RandomPolicy(7).choose(context, decision)
        got = policy.random_cell(7, decision, menu)
        assert got == (want.design_index, want.diagnostic_index)


def test_policy_settings_and_spec_round_trip() -> None:
    spec = InvestigationSpec(
        POINTPROC.name,
        "hawkes",
        "AG-c",
        "named",
        1,
        policy="EIG",
        policy_settings=SMALL_POLICY,
    )
    data = spec.as_json()
    assert data["policy"] == "EIG"
    assert InvestigationSpec.from_json(data) == spec
    default = InvestigationSpec(
        POINTPROC.name, "hawkes", "AG-c", "named", 1, policy="FSD"
    )
    back = InvestigationSpec.from_json(default.as_json())
    assert back.policy == "FSD" and back.policy_settings == PolicySettings()
    plain = InvestigationSpec(POINTPROC.name, "hawkes", "AG-c", "named", 1)
    # A record without a policy (every pre-WP10 record) reads back as None,
    # and a non-policy spec writes exactly the keys it wrote before.
    assert "policy" not in plain.as_json()
    assert "policy_settings" not in plain.as_json()
    assert InvestigationSpec.from_json(plain.as_json()).policy is None
    with pytest.raises(LabError):
        InvestigationSpec.from_json({**data, "policy": "greedy"})


def test_bad_policies_are_refused(tmp_path: Path) -> None:
    for bad in ("greedy", "eig", ""):
        with pytest.raises(LabError, match="policy"):
            runner.run(
                POINTPROC,
                "hawkes",
                "AG-c",
                "named",
                "unused-model",
                1,
                tmp_path / "out",
                policy=bad,
            )
    with pytest.raises(LabError, match="AG-c"):
        runner.run(
            POINTPROC,
            "hawkes",
            "AG-o",
            "named",
            "unused-model",
            1,
            tmp_path / "out",
            policy="EIG",
        )
    assert not (tmp_path / "out").exists()
    with pytest.raises(PolicyModeError):
        PolicySettings(replicates=1)
    with pytest.raises(PolicyModeError):
        PolicySettings(diagnostics=())
    with pytest.raises(PolicyModeError):
        PolicySettings(diagnostics=("no_such_diagnostic",))
    with pytest.raises(PolicyModeError, match="no_such_design"):
        lab(
            "AG-c",
            "named",
            policy="random",
            policy_settings=PolicySettings(designs=("no_such_design",)),
        )


def test_eig_and_fsd_need_the_environments_mark_law(tmp_path: Path) -> None:
    import dataclasses

    no_marks = dataclasses.replace(POINTPROC, marks=None)
    with pytest.raises(LabError, match="mark"):
        runner.run(
            no_marks,
            "hawkes",
            "AG-c",
            "named",
            "unused-model",
            1,
            tmp_path / "out",
            policy="EIG",
        )


def test_policy_surface_names_no_policy_and_is_the_live_labs(tmp_path: Path) -> None:
    """The prompt says the framework chooses the experiments; it does not name
    the rule, so the three policies show the agent the same surface."""
    surfaces = {
        name: rendered_surface(lab("AG-c", "named", policy=name))
        for name in ("random", "EIG", "FSD")
    }
    assert surfaces["random"] == surfaces["EIG"] == surfaces["FSD"]
    plain = rendered_surface(lab("AG-c", "named"))
    added = set(surfaces["EIG"]["system_prompt"].splitlines()) - set(
        plain["system_prompt"].splitlines()
    )
    assert added
    assert not INTERNALS.search("\n".join(added)), added
    description = surfaces["EIG"]["tool:run_experiment"]["description"]
    assert not INTERNALS.search(description)
    assert "chosen by the framework" in surfaces["EIG"]["system_prompt"]


# --------------------------------------------------------------------------
# Wall cost (reported, not asserted)
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_per_decision_wall_cost_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clean_environment(monkeypatch)
    for name in ("random", "EIG", "FSD"):
        start = time.perf_counter()
        outcome = _session(name, tmp_path / name)
        wall = time.perf_counter() - start
        assert outcome.outcome == "submitted"
        with capsys.disabled():
            print(f"\n{name}: scripted session with 2 decisions, {wall:.1f} s")


# --------------------------------------------------------------------------
# A fault inside the policy never reaches the agent's text
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fault",
    [
        PolicyError("score (0, 1) is not finite: 0.7316442"),
        BoundViolationError("intensity of trade exceeded 123.4567 on [0, 2000]"),
        catalogue.DiagnosticArgumentError("trade: window 0.7316442 out of range"),
    ],
    ids=["policy", "simulation", "diagnostic"],
)
def test_a_policy_fault_is_recorded_and_the_agent_told_only_nothing_ran(
    fault: SciAgentError, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any typed error from inside the policy's decision (its fits, its
    simulations, its diagnostics) may carry policy numbers or native names, so
    it is a recorded infrastructure fault (it voids the run); the agent reads
    only that nothing ran, and nothing is charged or sealed."""

    def raises(*args: Any, **kwargs: Any) -> Any:
        raise fault

    monkeypatch.setattr(policy.PolicyMode, "decide", raises)
    the_lab = lab("AG-c", "anon", policy="EIG", policy_settings=SMALL_POLICY)
    layer = the_lab.layer()
    assert not layer.call(*BELIEF).is_error
    out = layer.call("run_experiment", {})
    assert out.is_error
    assert "nothing was run" in out.text
    assert str(fault) not in out.text
    assert "0.7316" not in out.text and "123.4567" not in out.text
    assert "trade" not in out.text
    assert layer.meter("experiments").used == 0
    assert the_lab.world.experiments_run == 0
    assert the_lab.ledger.next_experiment == 0
    assert any(str(fault) in f for f in the_lab.infrastructure_faults)
