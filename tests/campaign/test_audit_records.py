"""The checks ported from the Windows hunt scripts (``origin/windows-salvage``,
``salvage/windows-2026-10/hunt/``) that the first rebuild lacked:

- hunt B ``store_check.py`` §3: budget accounting per session record
  (:func:`~sciagent.campaign.audit.check_budgets`);
- hunt B ``store_check.py`` §2 and §5: every stored session found by the
  rescore's enumeration, and the void policy against the stored flag
  (:func:`~sciagent.campaign.audit.check_sessions`);
- hunt B ``store_check.py`` §6: the key fields each kind of cell needs;
- hunt A ``p3_gap.py``: no system beats the truth's own parameters on the
  held-out log (the leakage flag of ``gap_check``'s ``truth`` reference).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from audit_support import (
    CALLS,
    call,
    clean_store,
    make_key,
    put_baseline,
    put_score,
    put_session,
    sha,
    unit_json,
)
from campaign_support import TRUTH_IDS, entry, environment, tiny_config
from test_audit_driver import run_campaign

from sciagent.campaign.audit import (
    check_budgets,
    check_sessions,
    check_store,
    gap_check,
)
from sciagent.campaign.plan import (
    CampaignError,
    LLMArm,
    Unit,
    session_key,
    session_text,
)
from sciagent.campaign.store import ResultStore


def _kinds(findings: tuple[Any, ...]) -> list[str]:
    return [f.kind for f in findings]


# --------------------------------------------------------------------------
# Budget accounting (store_check.py §3)
# --------------------------------------------------------------------------


def _one_session(root: Path, **kw: Any) -> Path:
    with ResultStore(root) as store:
        put_session(store, "AG-o-S/named", **kw)
    return root


def test_a_record_whose_charges_match_its_calls_passes(tmp_path: Path) -> None:
    check = check_budgets(clean_store(tmp_path / "s"))
    assert check.issues == () and check.warnings == ()
    assert check.cells == 1
    assert check.passed


def test_a_refused_call_that_was_charged_is_found(tmp_path: Path) -> None:
    calls = list(CALLS)
    calls[2] = {
        **calls[2],
        "charged": "fits",
        "budgets_after": {**calls[2]["budgets_after"], "fits": 2},
    }
    calls[3] = {**calls[3], "budgets_after": {"experiments": 1, "fits": 2, "submit": 0}}
    calls[4] = {**calls[4], "budgets_after": {"experiments": 1, "fits": 2, "submit": 1}}
    check = check_budgets(_one_session(tmp_path / "s", calls=tuple(calls)))
    assert "budget_charge" in _kinds(check.issues)
    assert not check.passed


def test_a_successful_budgeted_call_left_uncharged_is_found(tmp_path: Path) -> None:
    calls = list(CALLS)
    calls[0] = {
        **calls[0],
        "charged": None,
        "budgets_after": {"experiments": 0, "fits": 0, "submit": 0},
    }
    check = check_budgets(_one_session(tmp_path / "s", calls=tuple(calls)))
    assert "budget_charge" in _kinds(check.issues)


def test_budgets_after_that_disagree_with_the_running_count_are_found(
    tmp_path: Path,
) -> None:
    calls = list(CALLS)
    calls[3] = {**calls[3], "budgets_after": {"experiments": 1, "fits": 0, "submit": 0}}
    check = check_budgets(_one_session(tmp_path / "s", calls=tuple(calls)))
    assert _kinds(check.issues) == ["budgets_after"]


def test_a_call_over_its_budget_is_found(tmp_path: Path) -> None:
    calls = (
        call(0, "submit", "submit", (0, 0, 1)),
        call(1, "submit", "submit", (0, 0, 2)),
    )
    check = check_budgets(_one_session(tmp_path / "s", calls=calls))
    assert "over_budget" in _kinds(check.issues)


def test_an_exhaustion_refusal_below_the_limit_is_found(tmp_path: Path) -> None:
    calls = (
        call(
            0,
            "fit",
            None,
            (0, 0, 0),
            is_error=True,
            text="budget exhausted: fit draws on budget 'fits', which allows 2",
        ),
    )
    check = check_budgets(_one_session(tmp_path / "s", calls=calls))
    assert _kinds(check.issues) == ["early_exhaustion"]


def test_call_indices_out_of_order_are_found(tmp_path: Path) -> None:
    calls = (
        call(0, "diagnostic", None, (0, 0, 0)),
        call(2, "diagnostic", None, (0, 0, 0)),
    )
    check = check_budgets(_one_session(tmp_path / "s", calls=calls))
    assert _kinds(check.issues) == ["call_index"]


def test_charged_fits_without_sequential_fit_numbers_are_found(tmp_path: Path) -> None:
    calls = (
        call(0, "fit", "fits", (0, 1, 0), record={"fit": 2}),
        call(1, "fit", "fits", (0, 2, 0), record={"fit": 1}),
    )
    check = check_budgets(_one_session(tmp_path / "s", calls=calls))
    assert _kinds(check.issues) == ["fit_numbers"]


@pytest.mark.parametrize(
    ("reading", "session", "kind"),
    [
        ({"fits_used": 2.0}, None, "reading_used"),
        ({"experiments_used": 0.0}, None, "reading_used"),
        ({"tool_calls": 4.0}, None, "reading_calls"),
        (None, {"tool_calls": {"fit": 2, "submit": 1}}, "detail_calls"),
        (None, {"fitted": []}, "detail_fitted"),
    ],
)
def test_counts_in_the_cell_that_disagree_with_the_record_are_found(
    tmp_path: Path,
    reading: dict[str, float] | None,
    session: dict[str, Any] | None,
    kind: str,
) -> None:
    check = check_budgets(
        _one_session(tmp_path / "s", reading=reading, session=session)
    )
    assert _kinds(check.issues) == [kind]


def test_an_unreadable_record_is_an_issue(tmp_path: Path) -> None:
    root = _one_session(tmp_path / "s")
    (record,) = (root / "llm").glob("*/record.json")
    record.write_text("{}", encoding="utf-8")
    check = check_budgets(root)
    assert _kinds(check.issues) == ["unreadable_record"]


def test_a_session_that_did_not_complete_is_not_audited(tmp_path: Path) -> None:
    root = _one_session(tmp_path / "s", reading={"status": 0.0}, make_record=False)
    check = check_budgets(root)
    assert check.cells == 0 and check.passed


# --------------------------------------------------------------------------
# Required key fields (store_check.py §6)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "drop"),
    [("baseline", "truth_digest"), ("llm-score", "fit_config")],
)
def test_a_cell_without_a_key_field_its_kind_needs_is_found(
    tmp_path: Path, kind: str, drop: str
) -> None:
    root = clean_store(tmp_path / "s")
    with ResultStore(root) as store:
        session = next(
            c["digest"]
            for c in store.cells()
            if c["key"]["config"]["kind"] == "llm-session"
        )
        fields = (
            {"truth_digest": sha("dev-009"), "system_config": sha("config of B-lib")}
            if kind == "baseline"
            else {"session": session}
        )
        key = make_key(
            kind,
            "B-lib" if kind == "baseline" else "AG-o-S/named",
            "dev-009",
            1,
            fields,
        )
        config = {k: v for k, v in key.config.items() if k != drop}
        key = dataclasses.replace(key, config=type(key.config)(config))
        detail: dict[str, Any] = {
            "unit": unit_json(kind, key.config["system"], "dev-009", 1),
            "result": {"structure": "Base()", "curve": []},
        }
        if kind == "baseline":
            detail["system_config"] = "config of B-lib"
        store.put(key, {"status": 1.0, "per_event": -0.8}, detail)
    check = check_store(root)
    assert [(f.kind, drop in f.message) for f in check.issues] == [
        ("missing_field", True)
    ]


# --------------------------------------------------------------------------
# Stored sessions and the void policy (store_check.py §2, §5)
# --------------------------------------------------------------------------


def _sessions(root: Path, config: Any = None, truths: Any = None) -> Any:
    config = config or tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truths = truths if truths is not None else [entry(t) for t in TRUTH_IDS]
    with ResultStore(root) as store:
        return check_sessions(store, environment(), config, truths)


def test_every_session_the_driver_wrote_is_found_at_its_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = run_campaign(tmp_path / "s", monkeypatch)
    check = _sessions(root)
    assert check.issues == () and check.warnings == ()
    assert check.sessions == check.found == check.at_current == 1
    assert check.passed
    assert check_budgets(root).passed  # a real record, written by the harness


def test_a_session_under_another_session_config_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = run_campaign(tmp_path / "s", monkeypatch)
    other = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),), max_turns=21)
    check = _sessions(root, config=other)
    assert _kinds(check.issues) == ["not_found"]
    assert "session_config" in check.issues[0].message
    assert check.found == 0 and not check.passed


@pytest.mark.parametrize("change", ["truth", "truth_digest", "seed", "system"])
def test_the_reason_a_session_is_not_found_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root = run_campaign(tmp_path / "s", monkeypatch)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truths = [entry(t) for t in TRUTH_IDS]
    if change == "truth":
        with ResultStore(root) as store:
            (mine,) = {
                c["key"]["config"]["truth"]
                for c in store.cells()
                if c["key"]["config"]["kind"] == "llm-session"
            }
        truths = [t for t in truths if t.id != mine]
    elif change == "truth_digest":
        truths = [dataclasses.replace(t, digest=sha("other")) for t in truths]
    elif change == "seed":
        config = dataclasses.replace(config, seeds=(2,), control_seeds=(2,))
    else:
        config = dataclasses.replace(config, llm_arms=(LLMArm("AG-o", "named"),))
    check = _sessions(root, config=config, truths=truths)
    assert _kinds(check.issues) == ["not_found"]
    assert change in check.issues[0].message


def test_a_stored_void_the_policy_does_not_void_is_a_warning(tmp_path: Path) -> None:
    """The pilot's harness voided sessions the policy later reclassified
    (``campaign.voids``); the check lists them, as hunt B §5 did."""
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truth = entry("hawkes")
    unit = Unit("hawkes", 1, "AG-c-H/named")
    key = session_key(unit, truth, environment(), config)
    root = tmp_path / "s"
    with ResultStore(root) as store:
        store.put(
            key,
            {"status": 1.0, "void": 1.0},
            {
                "unit": unit_json("llm-session", unit.system, "hawkes", 1),
                "session_config": session_text(config.arm_of(unit.system), config),
                "session": {
                    "outcome": "submitted",
                    "record": "record.json",
                    "record_dir": "llm/x",
                    "void_reasons": [],
                },
            },
        )
    check = _sessions(root, config=config, truths=[truth])
    assert check.issues == ()
    assert [(w.kind, w.where) for w in check.warnings] == [
        ("void_reclassified", str(key.digest))
    ]
    assert check.passed


def test_two_stored_sessions_for_one_unit_are_an_issue(tmp_path: Path) -> None:
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    truth = entry("hawkes")
    unit = Unit("hawkes", 1, "AG-c-H/named")
    root = tmp_path / "s"
    with ResultStore(root) as store:
        for fit in ("sciagent.glm.fit/OLD1", "sciagent.glm.fit/OLD2"):
            key = session_key(unit, truth, environment(), config)
            key = dataclasses.replace(
                key, env_version=type(key.env_version)(f"pointproc-v2|{fit}")
            )
            store.put(
                key,
                {"status": 1.0, "void": 0.0},
                {
                    "unit": unit_json("llm-session", unit.system, "hawkes", 1),
                    "session_config": session_text(config.arm_of(unit.system), config),
                    "session": {"outcome": "submitted", "record_dir": "llm/x"},
                },
            )
    check = _sessions(root, config=config, truths=[truth])
    assert _kinds(check.issues) == ["ambiguous"]


# --------------------------------------------------------------------------
# Beating the truth's own parameters (p3_gap.py's leakage flag)
# --------------------------------------------------------------------------


def _store(root: Path, oracle: float, blib: float) -> Path:
    with ResultStore(root) as store:
        put_baseline(store, "ORACLE", oracle)
        put_baseline(store, "B-lib", blib)
    return root


def test_a_system_above_the_truths_parameters_fails_even_below_oracle(
    tmp_path: Path,
) -> None:
    root = _store(tmp_path / "s", -0.70, -0.705)
    with ResultStore(root) as store:
        plain = gap_check(store, "fit/5")
        flagged = gap_check(store, "fit/5", truth={("dev-008", 1): -0.72})
    assert plain.passed and plain.beats_truth == ()
    assert not flagged.passed
    assert [
        (u.system, round(u.over_truth or 0.0, 12)) for u in flagged.beats_truth
    ] == [
        ("B-lib", 0.015),
        ("ORACLE", 0.02),
    ]
    assert {u.truth_ll for u in flagged.units} == {-0.72}


def test_a_system_at_the_truth_plus_the_threshold_passes(tmp_path: Path) -> None:
    step = 2.0**-7
    root = _store(tmp_path / "s", -1.0, -1.0)
    with ResultStore(root) as store:
        at = gap_check(
            store, "fit/5", threshold=step, truth={("dev-008", 1): -1.0 - step}
        )
        above = gap_check(
            store, "fit/5", threshold=step, truth={("dev-008", 1): -1.0 - 2 * step}
        )
    assert at.passed and at.beats_truth == ()
    assert not above.passed and len(above.beats_truth) == 2


def test_a_truth_reference_missing_for_a_scored_unit_is_refused(tmp_path: Path) -> None:
    root = _store(tmp_path / "s", -0.70, -0.80)
    with ResultStore(root) as store:
        put_score(store, "AG-o-S/named", "0" * 64, -0.72, seed=2)
        with pytest.raises(CampaignError, match="dev-008 s2"):
            gap_check(store, "fit/5", truth={("dev-008", 1): -0.60})
