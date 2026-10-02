"""The truth sampler and the splits: SPEC §6.3 instrument test 6.

"The truth sampler is deterministic, every test truth passes its constraints,
none is library-near, and the declared out-of-dictionary share holds."

The slow tests run a tiny configuration (ExpK only, short horizons) end to end
and re-verify every record with :func:`independent_check`, which recomputes
each constraint with its own code from the record alone: the structure from
its DSL text, the distances, the stratum, the calibration pilot (re-simulated
from the record's θ, ψ and stream key) and the identifiability margins.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from environments.pointproc.truths_v2 import (
    CALIBRATION,
    SAMPLER_CONFIG,
    STRUCTURE_PRIOR,
    THETA_PRIOR,
    environment,
)
from environments.pointproc.v2 import CHANNELS, mark_sampler
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.distance import structure_distance
from sciagent.glm.fit_bounds import simulable
from sciagent.glm.grammar import KernelKind, Link, depth, psi_slots, validate
from sciagent.glm.grids import PSI_GRIDS
from sciagent.glm.simulate import simulate
from sciagent.glm.syntax import parse
from sciagent.scenarios.calibrate import CalibrationSettings, branching_bound
from sciagent.scenarios.records import (
    SplitIntegrityError,
    TruthRecord,
    canonical_json,
    read_split,
    records_digest,
    write_split,
)
from sciagent.scenarios.sampler import (
    CandidateTimeoutError,
    SamplerConfig,
    SamplerConfigError,
    SamplerEnvironment,
    SplitResult,
    config_digest,
    sample_split,
    stratum_of,
    wall_clock_cap,
)
from sciagent.scenarios.streams import stream

# --------------------------------------------------------------------------
# A tiny configuration
# --------------------------------------------------------------------------

TINY_PRIOR = replace(
    STRUCTURE_PRIOR,
    n_features_probs=((1, 0.5), (2, 0.5), (3, 0.0), (4, 0.0)),
    kernel_probs=(
        (KernelKind.EXP, 1.0),
        (KernelKind.GAMMA, 0.0),
        (KernelKind.POWER, 0.0),
    ),
)
TINY_CALIBRATION = CalibrationSettings(
    pilot_horizons=(200.0, 1000.0),
    rate_tols=(0.15, 0.08),
    max_iter=8,
    event_cap_factor=3.0,
    burn_in=0.1,
    fano_window=2.0,
    fano_band=(1.2, 20.0),
    max_drift=1.6,
    max_branching=0.9,
)
TINY = replace(
    SAMPLER_CONFIG,
    structure_prior=TINY_PRIOR,
    theta_prior=replace(THETA_PRIOR, reference_horizon=300.0),
    calibration=TINY_CALIBRATION,
    identifiability_horizon=600.0,
    max_candidates=400,
)
ENV = environment()


def _run(seed: int, *, split: str = "dev", n: int = 4, workers: int = 1) -> SplitResult:
    return sample_split(TINY, ENV, split=split, n=n, seed=seed, workers=workers)


@pytest.fixture(scope="module")
def tiny() -> SplitResult:
    return _run(7)


# --------------------------------------------------------------------------
# The independent checker
# --------------------------------------------------------------------------


def _stratum(d: float, strata: tuple[tuple[str, float], ...]) -> str:
    lower = 0.0
    for label, upper in strata:
        if lower < d <= upper:
            return label
        lower = upper
    raise AssertionError(f"distance {d} in no stratum")


def independent_check(
    rec: TruthRecord, config: SamplerConfig, env: SamplerEnvironment
) -> None:
    """Every SPEC §3 constraint, recomputed from the record with separate code."""
    s = parse(rec.dsl, env.channels)
    validate(s, env.channels)
    assert canonicalise(s) == s
    assert structure_hash(s) == rec.structure_hash
    assert s.link is Link(rec.link)
    # Dictionary flag: some feature deeper than 2.
    depths = [depth(f) for f in s.features]
    assert tuple(depths) == rec.depths
    assert (max(depths) > 2) is rec.out_of_dictionary
    # ψ on the fitting grid and the truth grid, in slot order.
    psi = rec.psi_assignment()
    grids = dict(config.truth_psi_grids)
    for feature, assignment in zip(s.features, psi, strict=True):
        assert tuple(assignment) == psi_slots(feature)
        for slot, value in assignment.items():
            assert value in PSI_GRIDS[slot.name]
            assert value in grids.get(slot.name, PSI_GRIDS[slot.name])
    # Non-membership and the stratum.
    members = [(m.name, m.structure) for m in env.library if m.structure is not None]
    dists = {name: structure_distance(s, m) for name, m in members}
    assert dists == dict(rec.member_distances)
    nearest = min(dists.values())
    assert nearest > config.epsilon
    assert rec.nearest_distance == nearest
    assert dists[rec.nearest_member] == nearest
    assert rec.stratum == _stratum(nearest, config.strata)
    # Calibration: re-simulate the final pilot from the record and re-measure.
    cal = config.calibration
    coef = rec.coefficients()
    stage = len(cal.pilot_horizons) - 1
    h = cal.pilot_horizons[-1]
    log = simulate(
        s,
        psi,
        coef,
        env.channels,
        env.mark_sampler,
        h,
        stream(rec.seed, f"{rec.stream}/calibration/pilot{stage}"),
        max_events=int(cal.event_cap_factor * h),
    )
    t = log.times[log.times >= cal.burn_in * h]
    rate = t.size / (h - cal.burn_in * h)
    assert rate == pytest.approx(rec.calibration.mean_rate, rel=1e-12)
    assert abs(rate - 1.0) <= cal.rate_tols[-1]
    edges = cal.burn_in * h + cal.fano_window * np.arange(
        int((h - cal.burn_in * h) // cal.fano_window) + 1
    )
    counts = np.histogram(t, bins=edges)[0]
    fano = counts.var() / counts.mean()
    assert fano == pytest.approx(rec.calibration.fano, rel=1e-9)
    assert cal.fano_band[0] <= fano <= cal.fano_band[1]
    mid = cal.burn_in * h + 0.5 * (h - cal.burn_in * h)
    drift = np.count_nonzero(t >= mid) / np.count_nonzero(t < mid)
    assert 1.0 / cal.max_drift <= drift <= cal.max_drift
    theta = (coef.intercept, *(t for f in coef.per_feature for t in f))
    assert simulable(s, theta, env.channels)
    bound = branching_bound(s, psi, coef, env.mark_mean)
    assert bound == rec.calibration.branching_bound
    if bound is not None:
        assert bound < cal.max_branching
    # Identifiability: the recorded scores give the recorded margins, all ≥ δ.
    ident = rec.identifiability
    assert ident.truth_certified
    names = [m.name for m in env.library]
    assert [n for n, _ in ident.member_log_likelihoods] == names
    for (name, ll), (name2, margin) in zip(
        ident.member_log_likelihoods, ident.margins, strict=True
    ):
        assert name == name2
        expected = (ident.truth_log_likelihood - ll) / ident.held_out_events
        assert margin == pytest.approx(expected, rel=1e-12)
        assert margin >= config.delta
    assert ident.min_margin == min(m for _, m in ident.margins)


# --------------------------------------------------------------------------
# Slow: the sampler end to end
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_every_record_passes_the_independent_check(tiny: SplitResult) -> None:
    assert len(tiny.records) == 4
    for rec in tiny.records:
        independent_check(rec, TINY, ENV)


@pytest.mark.slow
def test_declared_out_of_dictionary_share_holds_exactly(tiny: SplitResult) -> None:
    flags = [r.out_of_dictionary for r in tiny.records]
    assert sum(flags) == round(TINY.out_of_dictionary_share * len(flags))
    # Interleaved, so any even prefix keeps the share.
    assert flags == [False, True, False, True]
    assert tiny.manifest["out_of_dictionary"]["count"] == 2


@pytest.mark.slow
def test_sampler_is_deterministic_and_independent_of_workers(tiny: SplitResult) -> None:
    again = _run(7, workers=2)
    assert canonical_json([r.to_json() for r in again.records]) == canonical_json(
        [r.to_json() for r in tiny.records]
    )
    assert canonical_json(again.manifest) == canonical_json(tiny.manifest)
    assert records_digest(again.records) == records_digest(tiny.records)


@pytest.mark.slow
def test_a_different_seed_gives_a_different_split(tiny: SplitResult) -> None:
    other = _run(8, n=2)
    assert {r.structure_hash for r in other.records} != {
        r.structure_hash for r in tiny.records[:2]
    }


@pytest.mark.slow
def test_test_split_hash_commits_to_the_records(
    tiny: SplitResult, tmp_path: Path
) -> None:
    result = _run(3, split="test", n=2)
    out = tmp_path / "test"
    write_split(out, result)
    data = (out / "truths.json").read_bytes()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    # The committed hash is sha256 over the canonical JSON file, recomputable
    # with any sha256 tool.
    assert manifest["records_sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["records_sha256"] == records_digest(result.records)
    assert manifest["split"] == "test"
    records, read_manifest = read_split(out)
    assert records == result.records
    assert read_manifest == manifest
    for rec in records:
        independent_check(rec, TINY, ENV)
    # Any change to a record is detected.
    (out / "truths.json").write_bytes(data.replace(b'"stratum":"', b'"stratum":"x', 1))
    with pytest.raises(SplitIntegrityError):
        read_split(out)


# --------------------------------------------------------------------------
# Fast
# --------------------------------------------------------------------------


def test_canonical_json_is_compact_sorted_and_finite() -> None:
    assert canonical_json({"b": 1.5, "a": [1, "x"]}) == '{"a":[1,"x"],"b":1.5}'
    with pytest.raises(ValueError):
        canonical_json({"a": math.nan})


def test_stratum_thresholds() -> None:
    strata = SAMPLER_CONFIG.strata
    assert [label for label, _ in strata] == ["near", "mid", "far"]
    assert stratum_of(0.4, strata) == "near"
    assert stratum_of(0.6, strata) == "near"
    assert stratum_of(0.65, strata) == "mid"
    assert stratum_of(0.9, strata) == "far"


def test_config_digest_tracks_every_declared_number() -> None:
    base = config_digest(SAMPLER_CONFIG, ENV)
    assert base == config_digest(SAMPLER_CONFIG, ENV)
    assert config_digest(replace(SAMPLER_CONFIG, delta=0.02), ENV) != base
    cal = replace(CALIBRATION, fano_band=(2.0, 5.5))
    assert config_digest(replace(SAMPLER_CONFIG, calibration=cal), ENV) != base
    assert config_digest(SAMPLER_CONFIG, replace(ENV, name="other")) != base


def test_invalid_configs_are_refused() -> None:
    with pytest.raises(SamplerConfigError):
        replace(SAMPLER_CONFIG, out_of_dictionary_share=1.5)
    with pytest.raises(SamplerConfigError):
        replace(SAMPLER_CONFIG, strata=(("near", 0.6), ("far", 0.9)))
    with pytest.raises(SamplerConfigError):
        replace(SAMPLER_CONFIG, epsilon=0.7)


def test_pointproc_declarations() -> None:
    assert SAMPLER_CONFIG.out_of_dictionary_share == 0.5
    assert SAMPLER_CONFIG.delta == 0.01
    assert SAMPLER_CONFIG.identifiability_horizon == 2000.0
    # ε sits below S11's distance to Hawkes (0.18), v1's out-of-library truth.
    assert SAMPLER_CONFIG.epsilon < 0.178
    assert [s.name for s in ENV.library] == [
        "null",
        "hawkes",
        "seasonality",
        "regime_switching",
        "poisson_mixture",
    ]
    assert ENV.channels == CHANNELS
    assert ENV.mark_sampler is mark_sampler


def test_wall_clock_cap_aborts_with_a_typed_error() -> None:
    with (
        pytest.raises(CandidateTimeoutError, match="slow-candidate"),
        wall_clock_cap(0.2, "slow-candidate"),
    ):
        end = time.perf_counter() + 5.0
        while time.perf_counter() < end:
            pass
    # A block that finishes in time is untouched, and no interrupt leaks out.
    with wall_clock_cap(5.0, "fast"):
        total = sum(range(1000))
    time.sleep(0.05)
    assert total == 499500


def test_script_refuses_a_test_split_inside_the_repository(tmp_path: Path) -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "sample_truths", root / "scripts" / "sample_truths.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = root / "data" / "v2" / "truths" / "test"
    args = ["--split", "test", "--n", "2", "--seed", "1", "--out", str(out)]
    with pytest.raises(SystemExit):
        module.main(args)
    assert not out.exists()
