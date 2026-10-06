"""B-lib, B-np and ORACLE on pointproc v2's named truths (SPEC §4.1, §6.4).

The slow tests are the positive controls the brief asks for: ORACLE beats
B-lib held out on S11's size excitation; B-lib picks Hawkes on the Hawkes
truth; B-np beats the null held out on the Hawkes truth. Each is a single seed
with the margin printed in the assertion message.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from environments.pointproc import v2
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, fit, parameter_log_likelihood
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    Gate,
    KernelKind,
    Link,
    Mark,
    One,
    PhaseWindow,
    Structure,
    psi_slots,
)
from sciagent.glm.simulate import Coefficients, PsiAssignment, simulate
from sciagent.library.mixture import PoissonMixture2
from sciagent.library.mmpp import MMPP2, MMPP2Params, simulate_mmpp2
from sciagent.systems.v2.models import GLMModel, LibraryFittedModel, WienerHopfModel
from sciagent.systems.v2.systems import (
    BLib,
    BNp,
    GrammarMember,
    InvestigationData,
    LibraryEntry,
    ModelMember,
    Oracle,
    SystemRunError,
    oracle_underfit,
)

HORIZON = 2000.0
FULL_LIBRARY: tuple[LibraryEntry, ...] = (
    *(GrammarMember(m.name, m.structure) for m in v2.LIBRARY),
    ModelMember("regime_switching", MMPP2()),
    ModelMember("poisson_mixture", PoissonMixture2()),
)
#: A cheap library for the fast tests: no exp-link quadrature fits.
SMALL_LIBRARY: tuple[LibraryEntry, ...] = (
    GrammarMember("null", v2.NULL),
    GrammarMember("hawkes", v2.HAWKES),
    ModelMember("regime_switching", MMPP2()),
)


def _data(truth: str, seed: int, horizon: float = HORIZON) -> InvestigationData:
    log = v2.TRUTHS[truth].simulate(horizon, np.random.default_rng(seed))
    return InvestigationData(
        (Dataset.observational(log),), v2.CHANNELS, v2.mark_sampler
    )


def _held_out(truth: str, seed: int, n: int = 2) -> list[Dataset]:
    rng = np.random.default_rng(seed)
    return [
        Dataset.observational(v2.TRUTHS[truth].simulate(HORIZON, rng), f"held{i}")
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# Fast: shape of the results
# --------------------------------------------------------------------------


def test_blib_result_shape_and_trajectory() -> None:
    data = _data("hawkes", 1, 400.0)
    result = BLib(SMALL_LIBRARY).run(data)
    assert result.system == "B-lib"
    assert result.fits_used == 3
    assert [p.fits_used for p in result.trajectory] == [1, 2, 3]
    assert [p.candidate for p in result.trajectory] == [
        "null",
        "hawkes",
        "regime_switching",
    ]
    bests = [p.best_criterion for p in result.trajectory]
    assert bests == sorted(bests, reverse=True)
    assert result.trajectory[-1].best == result.submitted
    assert result.trajectory[-1].best_model is result.model


def test_blib_submits_none_structure_when_out_of_grammar_wins() -> None:
    params = MMPP2Params(0.2, 4.0, 0.02, 0.05)
    log = simulate_mmpp2(
        params, 1000.0, np.random.default_rng(2), marks=v2.mark_sampler
    )
    data = InvestigationData(
        (Dataset.observational(log),), v2.CHANNELS, v2.mark_sampler
    )
    result = BLib(SMALL_LIBRARY).run(data)
    assert result.submitted == "regime_switching"
    assert result.structure is None
    assert isinstance(result.model, LibraryFittedModel)


def test_blib_is_deterministic() -> None:
    data = _data("hawkes", 3, 400.0)
    a = BLib(SMALL_LIBRARY).run(data)
    b = BLib(SMALL_LIBRARY).run(data)
    assert a.submitted == b.submitted
    assert [p.candidate_criterion for p in a.trajectory] == [
        p.candidate_criterion for p in b.trajectory
    ]


def test_oracle_fits_the_given_structure() -> None:
    data = _data("size_excitation", 4, 400.0)
    result = Oracle(v2.SIZE_EXCITED).run(data)
    assert result.structure == v2.SIZE_EXCITED
    assert result.fits_used == 1
    assert isinstance(result.model, GLMModel)
    direct = fit(v2.SIZE_EXCITED, data.observational, v2.CHANNELS)
    assert result.model.fit == direct


def test_bnp_submits_no_structure() -> None:
    result = BNp().run(_data("hawkes", 5, 400.0))
    assert result.structure is None
    assert result.fits_used == 0
    assert isinstance(result.model, WienerHopfModel)


def test_bnp_refuses_intervened_or_multiple_logs() -> None:
    data = _data("hawkes", 6, 300.0)
    obs = data.observational[0]
    two = InvestigationData((obs, obs), v2.CHANNELS, v2.mark_sampler)
    with pytest.raises(SystemRunError):
        BNp().run(two)
    windowed = Dataset.create(obs.log, obs.endogenous, ((1.0, 2.0),), "w")
    with pytest.raises(SystemRunError):
        BNp().run(InvestigationData((windowed,), v2.CHANNELS, v2.mark_sampler))


# --------------------------------------------------------------------------
# Slow: positive controls on the named truths
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_oracle_beats_blib_on_size_excitation() -> None:
    data = _data("size_excitation", 10)
    held = _held_out("size_excitation", 11)
    oracle = Oracle(v2.SIZE_EXCITED).run(data)
    blib = BLib(FULL_LIBRARY).run(data)
    gap = oracle.model.held_out_per_event(held) - blib.model.held_out_per_event(held)
    # Measured 0.040-0.046 nats/event over seeds 10, 40, 50, 60.
    assert gap > 0.01, (blib.submitted, gap)


@pytest.mark.slow
def test_blib_picks_hawkes_on_hawkes_truth() -> None:
    result = BLib(FULL_LIBRARY).run(_data("hawkes", 20))
    assert result.submitted == "hawkes", [
        (p.candidate, p.candidate_criterion) for p in result.trajectory
    ]
    assert result.structure == v2.HAWKES


@pytest.mark.slow
def test_bnp_beats_null_on_hawkes_truth() -> None:
    data = _data("hawkes", 30)
    held = _held_out("hawkes", 31)
    bnp = BNp().run(data)
    null = BLib((GrammarMember("null", v2.NULL),)).run(data)
    gap = bnp.model.held_out_per_event(held) - null.model.held_out_per_event(held)
    assert gap > 0.05, gap
    assert math.isfinite(gap)


# --------------------------------------------------------------------------
# ORACLE's truth-ψ check (P4-PROPOSAL §2.1 b)
# --------------------------------------------------------------------------

_SIZE: tuple[ChannelSpec, ...] = (
    ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),
)
_WINDOWS = Structure(
    (
        Gate(Excite(KernelKind.EXP, One(), ALL), PhaseWindow()),
        Gate(Excite(KernelKind.EXP, Mark("size"), ALL), PhaseWindow()),
    ),
    Link.SOFTPLUS,
)


def _windows_psi() -> PsiAssignment:
    values = (
        {"exp_rate": 0.25, "period": 5.0, "phase": 1.5 * math.pi},
        {"exp_rate": 1.0, "period": 10.0, "phase": 0.0},
    )
    return tuple(
        {s: v[s.name] for s in psi_slots(f)}
        for f, v in zip(_WINDOWS.features, values, strict=True)
    )


_WINDOWS_COEF = Coefficients(0.5, ((-2.0,), (0.8,)))
#: fit/4's search alone (per-slot line search, no screening). On the seeded
#: draw below it stops short of the truth's ψ point.
_WEAK = FitConfig(psi_line_start=True, psi_screen=0, psi_starts=0, psi_block_max=1)


def _size_marks(rng: np.random.Generator) -> dict[str, float]:
    return {"size": float(rng.exponential(1.0))}


def _windows_data() -> InvestigationData:
    log = simulate(
        _WINDOWS,
        _windows_psi(),
        _WINDOWS_COEF,
        _SIZE,
        _size_marks,
        300.0,
        np.random.default_rng(2),
    )
    return InvestigationData((Dataset.observational(log),), _SIZE, _size_marks)


def _oracle(config: FitConfig | None) -> Oracle:
    return Oracle(
        _WINDOWS,
        fit_config=config,
        truth_psi=_windows_psi(),
        truth_coefficients=_WINDOWS_COEF,
    )


def test_oracle_keeps_the_truth_psi_point_when_the_search_misses_it() -> None:
    data = _windows_data()
    searched = fit(_WINDOWS, data.observational, _SIZE, config=_WEAK)
    at_truth = fit(
        _WINDOWS, data.observational, _SIZE, config=_WEAK, psi=_windows_psi()
    )
    assert searched.log_likelihood < at_truth.log_likelihood - 1.0
    result = _oracle(_WEAK).run(data)
    assert isinstance(result.model, GLMModel)
    assert result.model.fit == at_truth
    assert result.details["oracle_psi_source"] == "truth"
    assert result.details["oracle_underfit"] is False
    assert result.trajectory[0].best_criterion == at_truth.bic


def test_oracle_keeps_its_search_when_it_reaches_the_truth_psi() -> None:
    data = _windows_data()
    result = _oracle(None).run(data)
    searched = fit(_WINDOWS, data.observational, _SIZE)
    assert isinstance(result.model, GLMModel)
    assert result.model.fit == searched
    assert result.details["oracle_psi_source"] == "search"
    assert result.details["oracle_underfit"] is False
    true_ll = math.fsum(
        parameter_log_likelihood(
            _WINDOWS, _windows_psi(), _WINDOWS_COEF, data.observational, _SIZE
        )
    )
    assert result.details["oracle_true_params_ll"] == true_ll
    assert searched.log_likelihood >= true_ll


def test_underfit_threshold() -> None:
    """Below the true parameters by more than 1e-9·|log L| is underfit."""
    assert not oracle_underfit(-1000.0, -1000.0)
    assert not oracle_underfit(-1000.0 - 0.9e-6, -1000.0)
    assert oracle_underfit(-1000.0 - 1.1e-6, -1000.0)
    assert not oracle_underfit(-999.0, -1000.0)


def test_oracle_needs_both_truth_parameters_or_neither() -> None:
    data = _windows_data()
    with pytest.raises(SystemRunError):
        Oracle(_WINDOWS, truth_coefficients=_WINDOWS_COEF).run(data)
    with pytest.raises(SystemRunError):
        Oracle(_WINDOWS, truth_psi=_windows_psi()).run(data)


def test_oracle_without_truth_parameters_is_the_plain_fit() -> None:
    data = _data("size_excitation", 4, 400.0)
    result = Oracle(v2.SIZE_EXCITED).run(data)
    assert result.details == {}


def test_oracle_with_an_off_grid_truth_psi_skips_the_refit() -> None:
    data = _windows_data()
    off = tuple(
        {s: (0.3 if s.name == "exp_rate" else v) for s, v in m.items()}
        for m in _windows_psi()
    )
    result = Oracle(_WINDOWS, truth_psi=off, truth_coefficients=_WINDOWS_COEF).run(data)
    assert result.details["oracle_truth_psi_on_grid"] is False
    assert result.details["oracle_underfit"] is False
    assert "oracle_truth_psi_ll" not in result.details
    assert result.details["oracle_psi_source"] == "search"
    assert isinstance(result.model, GLMModel)
    assert result.model.fit == fit(_WINDOWS, data.observational, _SIZE)
