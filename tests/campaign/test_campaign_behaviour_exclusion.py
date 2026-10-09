"""Predictions made in a policy arm are excluded from the §4.4 behaviour
measures (P4 Δ11, plan choice 10; LOG 2026-10-06).

In a policy arm (``InvestigationSpec.policy`` set; ``LLMArm.policy`` in the
campaign) the framework chose every experiment, so the agent's predictions
about them say nothing about how *it* seeks falsification. The analysis keys
the exclusion on the arm's recorded policy, never on the system name; an
excluded session stays in :attr:`Table.sessions` and is counted, as a void is;
a non-policy session reads exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from campaign_support import entry, environment, tiny_config

from sciagent.campaign.analysis import (
    PredictionTally,
    Session,
    Table,
    behaviour_sessions,
    load_rows,
    prediction_tally,
    void_counts,
)
from sciagent.campaign.plan import LLMArm, Unit, score_key, session_key
from sciagent.campaign.store import ResultStore
from sciagent.harness.voids import VoidStatus

PLAIN = LLMArm("AG-c", "named")
RANDOM = LLMArm("AG-c", "named", policy="random")
CLEAN = VoidStatus(False, (), 0, False)
VOID = VoidStatus(True, ("out of bounds: call 1: /proc\\b",), 0, True)


def _reading(predictions: float, evaluated: float, covered: float) -> dict[str, float]:
    return {
        "status": 1.0,
        "void": 0.0,
        "submitted": 1.0,
        "predictions": predictions,
        "predictions_evaluated": evaluated,
        "predictions_covered": covered,
    }


def _session(
    system: str,
    seed: int,
    reading: dict[str, float],
    *,
    policy: str | None,
    void: VoidStatus = CLEAN,
) -> Session:
    return Session("t", seed, system, reading, {}, void, "env", policy)


def test_a_policy_session_contributes_no_predictions_and_is_counted() -> None:
    system = "AG-c-S/named+policy=EIG"
    table = Table(
        {},
        (),
        (
            _session(system, 1, _reading(3.0, 2.0, 1.0), policy="EIG"),
            _session(system, 2, _reading(0.0, 0.0, 0.0), policy="EIG"),
        ),
        {},
    )
    read, excluded = behaviour_sessions(table, system)
    assert read == () and [x.seed for x in excluded] == [1, 2]
    assert prediction_tally(table, system) == PredictionTally(
        sessions=2,
        excluded=2,
        excluded_predictions=3,
        read=0,
        predicting=0,
        predictions=0,
        evaluated=0,
        covered=0,
    )


def test_the_exclusion_is_keyed_on_the_policy_not_the_system_name() -> None:
    """A session is excluded by its recorded policy; a name that merely looks
    like a policy arm's changes nothing, and a policy under a plain-looking
    name is still excluded."""
    looks = "AG-c-S/named+policy=EIG"
    table = Table(
        {},
        (),
        (
            _session(looks, 1, _reading(2.0, 2.0, 2.0), policy=None),
            _session("AG-c-S/named", 1, _reading(2.0, 2.0, 2.0), policy="FSD"),
        ),
        {},
    )
    assert prediction_tally(table, looks).excluded == 0
    assert prediction_tally(table, looks).predictions == 2
    assert prediction_tally(table, "AG-c-S/named").excluded == 1
    assert prediction_tally(table, "AG-c-S/named").predictions == 0


def test_a_non_policy_system_reads_as_before_voids_included() -> None:
    """Without a policy the tally is the plain sum over the system's sessions
    that the report computed before (void sessions are counted in it, as they
    were)."""
    system = "AG-c-S/named"
    sessions = (
        _session(system, 1, _reading(3.0, 2.0, 1.0), policy=None),
        _session(system, 2, _reading(0.0, 0.0, 0.0), policy=None),
        _session(system, 3, _reading(1.0, 1.0, 1.0), policy=None, void=VOID),
    )
    table = Table({}, (), sessions, {})
    read, excluded = behaviour_sessions(table, system)
    assert read == sessions and excluded == ()
    assert prediction_tally(table, system) == PredictionTally(
        sessions=3,
        excluded=0,
        excluded_predictions=0,
        read=3,
        predicting=2,
        predictions=4,
        evaluated=3,
        covered=2,
    )


def _put_session(
    store: ResultStore, unit: Unit, reading: dict[str, float], config: Any
) -> None:
    truth, env = entry(unit.truth_id), environment()
    detail = {
        "unit": {"truth": unit.truth_id, "seed": unit.seed, "system": unit.system},
        "session": {"outcome": "submitted", "void_reasons": [], "usage": {}},
    }
    skey = session_key(unit, truth, env, config)
    store.put(skey, reading, detail)
    store.put(
        score_key(unit, skey, env, config),
        {"status": 1.0, "per_event": -0.7},
        {"unit": {}, "result": {}},
    )


def test_load_rows_records_each_sessions_policy_from_its_arm(tmp_path: Path) -> None:
    """Through the store: the policy comes from the addressed arm (the policy
    is in the session's address); the policy session keeps its session and
    its score row, and only its predictions are excluded."""
    config = tiny_config(seeds=(1,), llm_arms=(PLAIN, RANDOM))
    plain, policy = (Unit("hawkes", 1, a.system("H")) for a in (PLAIN, RANDOM))
    with ResultStore(tmp_path) as store:
        for unit in (plain, policy):
            _put_session(store, unit, _reading(2.0, 2.0, 1.0), config)
        table = load_rows(store, environment(), config, [entry("hawkes")])
    by_system = {x.system: x for x in table.sessions}
    assert by_system[plain.system].policy is None
    assert by_system[policy.system].policy == "random"
    assert sorted(r.system for r in table.rows) == sorted([plain.system, policy.system])
    assert void_counts(table) == {}
    assert prediction_tally(table, plain.system) == PredictionTally(
        sessions=1,
        excluded=0,
        excluded_predictions=0,
        read=1,
        predicting=1,
        predictions=2,
        evaluated=2,
        covered=1,
    )
    assert prediction_tally(table, policy.system) == PredictionTally(
        sessions=1,
        excluded=1,
        excluded_predictions=2,
        read=0,
        predicting=0,
        predictions=0,
        evaluated=0,
        covered=0,
    )
