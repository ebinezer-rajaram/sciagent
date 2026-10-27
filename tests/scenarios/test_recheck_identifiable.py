"""scripts/recheck_identifiable.py reproduces the sampler's acceptance.

On a tiny split sampled under this tree's fitter, the recheck must rebuild the
same training and held-out logs and get the recorded scores and margins bit
for bit: that is what makes a status change under another fitter meaningful.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from environments.pointproc.truths_v2 import (
    SAMPLER_CONFIG,
    STRUCTURE_PRIOR,
    THETA_PRIOR,
    environment,
)
from sciagent.glm.grammar import KernelKind
from sciagent.scenarios.calibrate import CalibrationSettings
from sciagent.scenarios.records import read_split, write_split
from sciagent.scenarios.sampler import sample_split

ROOT = Path(__file__).resolve().parents[2]

# The tiny configuration of test_truth_sampler.py (ExpK only, short horizons).
TINY = replace(
    SAMPLER_CONFIG,
    structure_prior=replace(
        STRUCTURE_PRIOR,
        n_features_probs=((1, 0.5), (2, 0.5), (3, 0.0), (4, 0.0)),
        kernel_probs=(
            (KernelKind.EXP, 1.0),
            (KernelKind.GAMMA, 0.0),
            (KernelKind.POWER, 0.0),
        ),
    ),
    theta_prior=replace(THETA_PRIOR, reference_horizon=300.0),
    calibration=CalibrationSettings(
        pilot_horizons=(200.0, 1000.0),
        rate_tols=(0.15, 0.08),
        max_iter=8,
        event_cap_factor=3.0,
        burn_in=0.1,
        fano_window=2.0,
        fano_band=(1.2, 20.0),
        max_drift=1.6,
        max_branching=0.9,
    ),
    identifiability_horizon=600.0,
    max_candidates=400,
)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "recheck_identifiable", ROOT / "scripts" / "recheck_identifiable.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.slow
def test_recheck_reproduces_the_sampler_bit_for_bit(tmp_path: Path) -> None:
    env = environment()
    write_split(tmp_path, sample_split(TINY, env, split="dev", n=2, seed=7))
    records, _ = read_split(tmp_path)  # through JSON, as the script reads them
    script = _script()
    for rec in records:
        row = script.recheck(rec, TINY, env)
        ident = rec.identifiability
        assert row.status == "pass", row.detail
        assert row.data_matches
        assert row.train_events == ident.train_events
        assert row.held_out_events == ident.held_out_events
        assert row.truth_log_likelihood == ident.truth_log_likelihood
        assert row.member_log_likelihoods == ident.member_log_likelihoods
        assert row.margins == ident.margins
        assert row.min_margin == ident.min_margin
        assert row.truth_psi_recovered == ident.truth_psi_recovered
