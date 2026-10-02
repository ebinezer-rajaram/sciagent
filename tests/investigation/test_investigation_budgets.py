"""Instrument test 7, part 1: budgets are enforced by the tool layer (SPEC §6.3).

E experiments, F fits (distinct fits only; a canonical repeat is free), one
submit, and the tier split: AG-c has no ``python`` or ``notebook``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from investigation_support import clean_environment, lab, plain_run, scripted_run

from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation import InvestigationSpec
from sciagent.investigation.tools import (
    BUDGET_EXPERIMENTS,
    BUDGET_FITS,
    FIT_CONFIG,
    LabError,
)


def test_tool_sets_split_by_tier() -> None:
    constrained = lab("AG-c").layer().names
    assert constrained == [
        "run_experiment",
        "diagnostic",
        "nonparam_kernels",
        "fit",
        "predict",
        "submit",
    ]
    assert "python" not in constrained
    assert "notebook" not in constrained


def test_budgets_are_the_configured_ones() -> None:
    layer = lab("AG-c", experiments=3, fits=4).layer()
    assert layer.budgets() == {"experiments": 3, "fits": 4, "submit": 1}


def test_experiment_budget_is_enforced() -> None:
    layer = lab("AG-c", experiments=2).layer()
    for k in range(2):
        out = layer.call("run_experiment", plain_run(200.0))
        assert not out.is_error, out.text
        assert f"experiment e{k} done" in out.text
    refused = layer.call("run_experiment", plain_run(200.0))
    assert refused.is_error
    assert "budget exhausted" in refused.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 2


def test_an_invalid_design_is_not_charged() -> None:
    layer = lab("AG-c", experiments=1).layer()
    bad = layer.call(
        "run_experiment",
        {"intervention": {"type": "censor", "window": [5.0, 1.0]}, "horizon": 100.0},
    )
    assert bad.is_error
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0
    assert not layer.call("run_experiment", plain_run(100.0)).is_error


def test_fit_budget_counts_distinct_fits_only() -> None:
    layer = lab("AG-c", fits=2).layer()
    first = layer.call("fit", {"structure": "null", "data_ids": ["obs"]})
    assert not first.is_error, first.text
    # The same canonical structure on the same data: free, and says so.
    repeat = layer.call(
        "fit", {"structure": "link=identity; null", "data_ids": ["obs", "obs"]}
    )
    assert repeat.is_error
    assert repeat.text.startswith("not charged")
    assert layer.meter(BUDGET_FITS).used == 1
    second = layer.call(
        "fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]}
    )
    assert not second.is_error, second.text
    assert layer.meter(BUDGET_FITS).used == 2
    third = layer.call("fit", {"structure": "link=exp; Trend", "data_ids": ["obs"]})
    assert third.is_error
    assert "budget exhausted" in third.text
    assert layer.meter(BUDGET_FITS).used == 2


def test_malformed_structure_costs_no_fit() -> None:
    layer = lab("AG-c", fits=1).layer()
    out = layer.call(
        "fit", {"structure": "Excite(ExpK, Mark(nope), all)", "data_ids": ["obs"]}
    )
    assert out.is_error
    out = layer.call(
        "fit", {"structure": "Excite(ExpK, One, all) + 2", "data_ids": ["obs"]}
    )
    assert out.is_error
    assert layer.meter(BUDGET_FITS).used == 0


def test_submit_once_ends_the_run() -> None:
    layer = lab("AG-c").layer()
    bad = layer.call("submit", {"structure": "Excite(", "report": "r"})
    assert bad.is_error
    assert not layer.finished
    ok = layer.call("submit", {"structure": "Excite(ExpK, One, all)", "report": "r"})
    assert not ok.is_error
    assert layer.finished
    again = layer.call("submit", {"structure": "null", "report": "r"})
    assert again.is_error
    assert "ended" in again.text
    after = layer.call("diagnostic", {"name": "mean_rate", "data_id": "obs"})
    assert after.is_error


def test_fit_configuration_is_pinned_framework_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit N4 / invariant 2: no agent argument reaches the fit configuration."""
    the_lab = lab("AG-c")
    layer = the_lab.layer()
    schema = next(s for s in layer.schemas() if s["name"] == "fit")
    assert set(schema["input_schema"]["properties"]) == {"structure", "data_ids"}
    assert schema["input_schema"]["additionalProperties"] is False
    for smuggled in ("config", "gap_tol_rel", "psi_full_grid_max", "quadrature"):
        out = layer.call(
            "fit", {"structure": "null", "data_ids": ["obs"], smuggled: 1.0}
        )
        assert out.is_error and "unknown argument" in out.text
    assert replace(the_lab.fit_config, workers=1) == FIT_CONFIG
    # The pinned configuration is recorded with the run and checked on replay.
    clean_environment(monkeypatch)
    outcome = scripted_run(
        [Step(calls=(("submit", {"structure": "null", "report": "r"}),))], tmp_path
    )
    record = SessionRecord.load(outcome.record_path)
    spec = record.of_kind("config")[0].body["investigation"]
    assert spec["fit_config"] == FIT_CONFIG.key()
    tampered = {**spec, "fit_config": "gap=0x1.0p-1"}
    with pytest.raises(LabError, match="fit configuration"):
        InvestigationSpec.from_json(tampered)
