"""ψ search of the certified fitter (SPEC §2.2; P4-PROPOSAL §2.1, fit/5).

- **T1.** On a dev-008 draw (two ``Gate(…, PhaseWindow)`` features, 115,200
  ψ points), the fit reaches the θ-refit at the truth's own ψ point.
- **T2.** On grids over the exhaustive cap, the fit is at least as good as k
  seeded random grid points.
- **T3.** The fit is byte-identical run to run, and the exhaustive path
  (≤ 512 points) is bit-for-bit what fit/4 returned (reference digests
  captured from fit/4 before the change).
- **T5.** Nested monotonicity: a superset structure never fits worse than its
  subset on the same data (the extra coefficients can be zero), under every
  link. Reproduces the pilot's dev-013 s2 failure (fit 21 vs fit 23).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from environments.pointproc import v2
from sciagent.glm.canonical import canonicalise
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import (
    FitConfig,
    FitConfigError,
    FitResult,
    fit,
    parameter_log_likelihood,
)
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    Feature,
    Gate,
    KernelKind,
    Link,
    Mark,
    One,
    Periodic,
    PhaseWindow,
    Product,
    PsiSlot,
    Structure,
    n_columns,
    psi_slots,
)
from sciagent.glm.grids import grid
from sciagent.glm.interventions import Compose, Experiment
from sciagent.glm.simulate import Coefficients, PsiAssignment, simulate
from sciagent.glm.syntax import parse
from sciagent.investigation.world import OBSERVATIONAL, TruthSpec, World
from sciagent.scenarios.records import read_split

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "data" / "v2" / "truths" / "dev"

CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
)
EXP_K = Excite(KernelKind.EXP, One(), ALL)
SIZE_K = Excite(KernelKind.EXP, Mark("size"), ALL)
POWER_K = Excite(KernelKind.POWER, One(), ALL)
WINDOW = Gate(Periodic(), PhaseWindow())


def marks(rng: np.random.Generator) -> Mapping[str, float]:
    return {"size": float(rng.exponential(1.0))}


def psi_of(feature: Feature, values: Mapping[str, float]) -> dict[PsiSlot, float]:
    return {slot: values[slot.name] for slot in psi_slots(feature)}


@cache
def hawkes_log(horizon: float = 400.0, seed: int = 1) -> EventLog:
    structure = Structure((EXP_K,), Link.IDENTITY)
    psi: PsiAssignment = (psi_of(EXP_K, {"exp_rate": 1.0}),)
    coef = Coefficients(0.5, ((0.5,),))
    return simulate(
        structure, psi, coef, CHANNELS, marks, horizon, np.random.default_rng(seed)
    )


@cache
def periodic_log(horizon: float = 400.0, seed: int = 2) -> EventLog:
    """``λ = exp(0.3 - 0.6·φ_ExpK(β=2) + 0.5 sin - 0.3 cos)``, P = 25."""
    structure = Structure((EXP_K, Periodic()), Link.EXP)
    psi: PsiAssignment = (
        psi_of(EXP_K, {"exp_rate": 2.0}),
        psi_of(Periodic(), {"period": 25.0}),
    )
    coef = Coefficients(0.3, ((-0.6,), (0.5, -0.3)))
    return simulate(
        structure, psi, coef, CHANNELS, marks, horizon, np.random.default_rng(seed)
    )


def obs(log: EventLog) -> tuple[Dataset, ...]:
    return (Dataset.observational(log),)


# --------------------------------------------------------------------------
# A canonical byte serialisation of a fit (every field but wall_time)
# --------------------------------------------------------------------------


def _psi_json(psi: PsiAssignment) -> list[list[list[object]]]:
    return [
        [[list(s.path), s.name, v.hex()] for s, v in sorted(m.items(), key=repr)]
        for m in psi
    ]


def fit_bytes(r: FitResult) -> bytes:
    def f(x: float) -> str:
        return float(x).hex()

    record = {
        "structure": repr(r.structure),
        "link": r.link.value,
        "psi": _psi_json(r.psi),
        "theta_labels": list(r.theta_labels),
        "theta": [f(t) for t in r.theta],
        "ll": f(r.log_likelihood),
        "ll_per": [f(x) for x in r.log_likelihood_per_dataset],
        "labels": list(r.dataset_labels),
        "ints": [r.n_events, r.n_params, r.iterations, r.n_psi_points],
        "counts": [r.n_uncertified_points, r.n_infeasible_points],
        "bic": f(r.bic),
        "aic": f(r.aic),
        "flags": [r.certified, r.simulable],
        "solver": [r.solver, r.solver_status, r.psi_search],
        "cert": [
            f(r.primal_objective),
            f(r.dual_objective),
            f(r.duality_gap),
            f(r.duality_gap_rel),
            f(r.dual_residual),
        ],
        "gof": [f(r.quadrature_error), f(r.ks_statistic), f(r.ks_pvalue)],
    }
    return json.dumps(record, sort_keys=True).encode()


def fit_digest(r: FitResult) -> str:
    return hashlib.sha256(fit_bytes(r)).hexdigest()


def reference_cases() -> dict[str, tuple[Structure, tuple[Dataset, ...]]]:
    """Exhaustive-path fits (≤ 512 ψ points) whose fit/4 output is pinned."""
    two = (
        Dataset.observational(hawkes_log(), "a"),
        Dataset.observational(hawkes_log(300.0, 5), "b"),
    )
    return {
        "hawkes-identity": (Structure((EXP_K,), Link.IDENTITY), obs(hawkes_log())),
        "power-identity": (Structure((POWER_K,), Link.IDENTITY), obs(hawkes_log())),
        "periodic-exp": (
            Structure((EXP_K, Periodic()), Link.EXP),
            obs(periodic_log()),
        ),
        "periodic-softplus": (
            Structure((SIZE_K, Periodic()), Link.SOFTPLUS),
            obs(periodic_log()),
        ),
        "window-exp": (Structure((WINDOW,), Link.EXP), obs(periodic_log())),
        "two-datasets": (Structure((EXP_K,), Link.IDENTITY), two),
    }


#: ``fit_digest`` of each reference case under fit/4 (git tag ``p3-fit4``),
#: captured before the search changed.
FIT4_DIGESTS: dict[str, str] = {
    "hawkes-identity": (
        "2c9ebbea97e5d96f199b261edeb81a6663e0d898952e2b57b6ed5c143e84e4c7"
    ),
    "periodic-exp": (
        "3abbe355e9242a3ba46e8975f613d75f6e2cbf79fe7eeef1e976ae8b089ad6e3"
    ),
    "periodic-softplus": (
        "93ee844698215973baa54b8a57dff23252311f486efa4b59d332e00f8766f7a1"
    ),
    "power-identity": (
        "6cb6f6f1dc0f0f11f185c04be904c4b4acdd669c5eb3da06baba7395c3e4a9bf"
    ),
    "two-datasets": (
        "3076274037a3cf6e88c36fa64297879f8caf236a0f9a56308d6859e1fb364b36"
    ),
    "window-exp": "b2ce799dd1f61c3fd54a14c8b67db64dee11a5d792318317e9e103818bc0d7a2",
}


# --------------------------------------------------------------------------
# Dev truths
# --------------------------------------------------------------------------


@cache
def dev_truth(truth_id: str) -> TruthSpec:
    records, _ = read_split(DEV)
    (record,) = [r for r in records if r.id == truth_id]
    return TruthSpec(
        record.structure(v2.CHANNELS),
        record.psi_assignment(),
        record.coefficients(),
        v2.CHANNELS,
        v2.mark_sampler,
    )


def assert_at_least(value: float, reference: float, rel: float = 1e-9) -> None:
    assert value >= reference - rel * max(1.0, abs(reference)), (value, reference)


# --------------------------------------------------------------------------
# T1: the truth's ψ point is never beaten on training data
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_dev008_fit_reaches_the_truth_psi_refit() -> None:
    truth = dev_truth("dev-008")
    data = (World(truth, 1).datasets[OBSERVATIONAL],)
    searched = fit(truth.structure, data, truth.channels)
    at_truth = fit(truth.structure, data, truth.channels, psi=truth.psi)
    assert searched.psi_search == "coordinate"
    assert at_truth.psi_search == "fixed"
    assert at_truth.n_psi_points == 1
    assert_at_least(searched.log_likelihood, at_truth.log_likelihood)
    # And so the true parameters cannot beat it either.
    true_ll = math.fsum(
        parameter_log_likelihood(
            truth.structure, truth.psi, truth.coef, data, truth.channels
        )
    )
    assert_at_least(searched.log_likelihood, true_ll)


def test_fixed_psi_fit_is_the_search_fit_at_that_point() -> None:
    structure = Structure((EXP_K, Periodic()), Link.EXP)
    data = obs(periodic_log())
    searched = fit(structure, data, CHANNELS)
    fixed = fit(structure, data, CHANNELS, psi=searched.psi)
    assert fixed.psi == searched.psi
    assert fixed.theta == searched.theta
    assert fixed.log_likelihood == searched.log_likelihood


def test_fixed_psi_must_be_on_the_grid() -> None:
    structure = Structure((EXP_K,), Link.IDENTITY)
    with pytest.raises(FitConfigError):
        fit(
            structure,
            obs(hawkes_log()),
            CHANNELS,
            psi=(psi_of(EXP_K, {"exp_rate": 0.3}),),
        )


def test_parameter_log_likelihood_is_the_fit_value_at_the_fit() -> None:
    structure = Structure((EXP_K, Periodic()), Link.EXP)
    data = obs(periodic_log())
    r = fit(structure, data, CHANNELS)
    intercept, *rest = r.theta
    coef = Coefficients(intercept, ((rest[0],), (rest[1], rest[2])))
    per = parameter_log_likelihood(structure, r.psi, coef, data, CHANNELS)
    assert per == r.log_likelihood_per_dataset


# --------------------------------------------------------------------------
# T2: no worse than seeded random grid points
# --------------------------------------------------------------------------

#: Structures over the 512-point cap, small enough to fit in seconds.
BIG: tuple[tuple[Feature, ...], ...] = (
    (Gate(EXP_K, PhaseWindow()), Gate(SIZE_K, PhaseWindow())),  # 120² = 14400
    (EXP_K, SIZE_K, WINDOW),  # 6·6·100 = 3600
    (Gate(POWER_K, PhaseWindow()), WINDOW),  # 240·100 = 24000
    (POWER_K, WINDOW),  # 12·100 = 1200
)


def _random_point(structure: Structure, rng: np.random.Generator) -> PsiAssignment:
    return tuple(
        {s: grid(s.name)[int(rng.integers(len(grid(s.name))))] for s in psi_slots(f)}
        for f in structure.features
    )


def _random_coefficients(
    structure: Structure, rng: np.random.Generator
) -> Coefficients:
    """Negative coefficients on non-negative columns: inhibition, which never
    explodes under exp or softplus (``WINDOW``'s sin/cos are kept small)."""
    return Coefficients(
        0.3,
        tuple(
            tuple(float(rng.uniform(-1.5, -0.5)) for _ in range(n_columns(f)))
            if not isinstance(f, Gate) or not isinstance(f.feature, Periodic)
            else (0.4, -0.3)
            for f in structure.features
        ),
    )


def t2_gap(which: int, link: str, seed: int) -> float:
    """How far the best of k = 4 grid points (the generating ψ and 3 random
    ones, each θ-refitted) beats the fit; ≤ 0 when the fit wins. Data drawn
    at the generating point. Module-level so worker processes can run it."""
    structure = Structure(BIG[which], Link(link))
    rng = np.random.default_rng(seed)
    generating = _random_point(structure, rng)
    coef = _random_coefficients(structure, rng)
    log = simulate(structure, generating, coef, CHANNELS, marks, 300.0, rng)
    data = obs(log)
    result = fit(structure, data, CHANNELS)
    assert result.psi_search == "coordinate"
    points = (generating, *(_random_point(structure, rng) for _ in range(3)))
    best = max(fit(structure, data, CHANNELS, psi=p).log_likelihood for p in points)
    return best - result.log_likelihood


def t2_examples(n: int) -> list[tuple[int, str, int]]:
    """The first ``n`` examples of a fixed derandomised Hypothesis profile."""
    out: list[tuple[int, str, int]] = []

    @settings(
        max_examples=n,
        deadline=None,
        derandomize=True,
        database=None,
        phases=[Phase.generate],
        suppress_health_check=list(HealthCheck),
    )
    @given(
        which=st.integers(0, len(BIG) - 1),
        link=st.sampled_from([Link.EXP, Link.SOFTPLUS]),
        seed=st.integers(0, 2**32 - 1),
    )
    def collect(which: int, link: Link, seed: int) -> None:
        out.append((which, link.value, seed))

    collect()
    return list(dict.fromkeys(out))


#: T2 cases fit/5's search once lost (LOG 2026-10-03), kept as regressions.
T2_REGRESSIONS: tuple[tuple[int, str, int], ...] = (
    (0, "exp", 0),
    (0, "softplus", 196),
    (0, "softplus", 52579),
    (0, "softplus", 530621),
    (1, "exp", 14),
    (1, "exp", 11666686),
    (1, "exp", 4294967295),
    (2, "exp", 2),
    (2, "exp", 4294967295),
    (2, "softplus", 12),
    (2, "softplus", 12362),
    (2, "softplus", 33914),
    (3, "exp", 624),
    (3, "exp", 2104),
    (3, "exp", 2162),
    (3, "exp", 4403),
    (3, "exp", 7595),
    (3, "exp", 32719),
    (3, "exp", 799215651),
    (3, "softplus", 3),
    (3, "softplus", 512),
    (3, "softplus", 1024),
    (3, "softplus", 3744),
    (3, "softplus", 417465741),
)


@pytest.mark.slow
@pytest.mark.parametrize("case", T2_REGRESSIONS, ids=str)
def test_t2_regression_cases(case: tuple[int, str, int]) -> None:
    assert t2_gap(*case) <= 1e-9 * 300


@pytest.mark.slow
def test_fit_beats_random_grid_points() -> None:
    """T2, strict: on each of 300 derandomised examples the fit is at least
    as good as every one of the k = 4 grid points (LOG 2026-10-06: the
    default search loses on none of them)."""
    examples = t2_examples(300)
    assert len(examples) == 300
    with ProcessPoolExecutor(4) as pool:
        gaps = list(pool.map(t2_gap, *zip(*examples, strict=True)))
    losses = [(g, e) for g, e in zip(gaps, examples, strict=True) if g > 1e-9 * 300]
    assert not losses, sorted(losses, reverse=True)


# --------------------------------------------------------------------------
# T3: determinism, and the exhaustive path is unchanged from fit/4
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(reference_cases()))
def test_exhaustive_path_is_unchanged_from_fit4(name: str) -> None:
    structure, data = reference_cases()[name]
    result = fit(structure, data, CHANNELS)
    assert result.psi_search == "exhaustive"
    assert fit_digest(result) == FIT4_DIGESTS[name]


def test_coordinate_fit_is_byte_identical_run_to_run() -> None:
    structure = Structure((EXP_K, SIZE_K, WINDOW), Link.EXP)
    data = obs(periodic_log(200.0, 4))
    a = fit(structure, data, CHANNELS)
    b = fit(structure, data, CHANNELS)
    assert a.psi_search == "coordinate"
    assert fit_bytes(a) == fit_bytes(b)


def test_search_config_enters_the_key() -> None:
    base = FitConfig()
    variants = [
        FitConfig(psi_starts=base.psi_starts + 1),
        FitConfig(psi_block_max=base.psi_block_max + 1),
        FitConfig(psi_line_start=not base.psi_line_start),
        FitConfig(psi_screen=base.psi_screen + 1),
        FitConfig(psi_loo_starts=not base.psi_loo_starts),
        FitConfig(psi_max_sweeps=base.psi_max_sweeps + 1),
    ]
    keys = {base.key(), *(v.key() for v in variants)}
    assert len(keys) == len(variants) + 1
    with pytest.raises(FitConfigError):
        FitConfig(psi_starts=-1)
    with pytest.raises(FitConfigError):
        FitConfig(psi_block_max=0)


# --------------------------------------------------------------------------
# T5: nested monotonicity
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_dev013_superset_never_fits_worse_than_its_subset() -> None:
    """The pilot's AG-o anon dev-013 s2 pair (fits 21 and 23, 22).

    fit/4 gave log L -12930.9 for A and -14217.6 for A + Excite(ExpK, One,
    all), -13107.5 for A + a third gated product, on data obs, e0, e1.
    """
    truth = dev_truth("dev-013")
    world = World(truth, 2)
    for _ in range(2):
        world.run(Experiment(Compose(()), 8000.0))
    data = tuple(world.datasets[i] for i in ("obs", "e0", "e1"))
    base = (
        "link=exp; Gate(Gate(Periodic, PhaseWindow), PhaseWindow) + "
        "Gate(Product(Periodic, Periodic), PhaseWindow)"
    )

    def ll(text: str) -> float:
        structure = canonicalise(parse(text, truth.channels))
        return fit(structure, data, truth.channels).log_likelihood

    subset = ll(base)
    assert_at_least(ll(base + " + Excite(ExpK, One, all)"), subset, 1e-7)
    assert_at_least(
        ll(base + " + Gate(Product(Periodic, Periodic), PhaseWindow)"), subset, 1e-7
    )


#: Candidate features for nested pairs: atoms and depth-2 trees with ψ.
EXTRA: tuple[Feature, ...] = (
    EXP_K,
    SIZE_K,
    POWER_K,
    Periodic(),
    WINDOW,
    Gate(SIZE_K, PhaseWindow()),
    Product(EXP_K, Periodic()),
)


@pytest.mark.slow
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    derandomize=True,
)
@given(
    base=st.lists(st.sampled_from(EXTRA), min_size=1, max_size=2),
    extra=st.sampled_from(EXTRA),
    link=st.sampled_from(list(Link)),
)
def test_superset_never_fits_worse(
    base: list[Feature], extra: Feature, link: Link
) -> None:
    data = obs(
        hawkes_log(300.0, 3) if link is Link.IDENTITY else periodic_log(300.0, 3)
    )
    subset = canonicalise(Structure(tuple(base), link))
    superset = canonicalise(Structure((*base, extra), link))
    a = fit(subset, data, CHANNELS)
    b = fit(superset, data, CHANNELS)
    # The superset contains the subset's model at θ_extra = 0, up to the
    # certificate tolerance and the two structures' quadrature rules.
    tol = (
        1e-7 * max(1.0, abs(a.log_likelihood)) + a.quadrature_error + b.quadrature_error
    )
    assert b.log_likelihood >= a.log_likelihood - tol, (subset, superset)
