"""The diagnostic catalogue of SPEC §4.3.

No acceptance criterion covers these, so the names deliberately do not follow
the ``test_aN_`` convention that ``scripts/status.py`` reads. What they check is
that each diagnostic returns its known null value on the reference programme,
and that the five discriminators actually discriminate: SPEC §4.2 assigns each
to a specific mechanism, and a diagnostic that fails to separate the mechanism
it is named for would silently make the three-stage plan impossible.
"""

from __future__ import annotations

import pytest

from environments.pointproc import (
    edit_grammar,
    mechanism_defect,
    reference_program,
)
from environments.pointproc.diagnostics import (
    count_autocorrelation,
    fano_factor,
    inter_arrival_dispersion,
    mean_high_run_length,
    mean_rate,
    phase_conditioned_dispersion,
    run_length_geometric_deviation,
    sign_autocorrelation,
    size_dispersion,
    size_mean,
    size_skewness,
    spectral_peak_frequency,
    spectral_peak_prominence,
)
from environments.pointproc.mechanisms import SEASONALITY
from sciagent.core.edits import Defect
from sciagent.core.types import EventLog, Seed

#: Long enough that the null values below are tight, short enough to stay quick.
N_EVENTS = 20_000
SEED = Seed(31337)

#: The calibrated seasonal period, read from the mechanism rather than repeated,
#: so that a recalibration cannot leave this file asserting a stale number.
SEASONAL_PERIOD = SEASONALITY.parameters["period"]


def run(defect: Defect, seed: Seed = SEED, n_events: int = N_EVENTS) -> EventLog:
    return edit_grammar().apply(reference_program(), defect).execute(seed, n_events)


@pytest.fixture(scope="module")
def null_log() -> EventLog:
    """The undefective reference programme: homogeneous Poisson, iid marks."""
    return reference_program().execute(SEED, N_EVENTS)


# ==========================================================================
# Null values on the reference programme
# ==========================================================================


class TestNullValues:
    """Every diagnostic has a known value under the reference programme."""

    def test_dispersion_and_fano_are_one(self, null_log: EventLog) -> None:
        assert mean_rate(null_log) == pytest.approx(1.0, abs=0.03)
        assert inter_arrival_dispersion(null_log) == pytest.approx(1.0, abs=0.05)
        for window in (0.5, 1.0, 2.0, 5.0):
            assert fano_factor(null_log, window) == pytest.approx(1.0, abs=0.08)

    def test_count_autocorrelation_is_zero(self, null_log: EventLog) -> None:
        assert count_autocorrelation(null_log, 2.0) == pytest.approx(0.0, abs=0.05)

    def test_sizes_are_exponential(self, null_log: EventLog) -> None:
        """Exponential marks: mean 1, squared CV 1, skewness 2."""
        assert size_mean(null_log) == pytest.approx(1.0, abs=0.03)
        assert size_dispersion(null_log) == pytest.approx(1.0, abs=0.06)
        assert size_skewness(null_log) == pytest.approx(2.0, abs=0.35)

    def test_signs_are_independent(self, null_log: EventLog) -> None:
        assert sign_autocorrelation(null_log) == pytest.approx(0.0, abs=0.05)
        assert sign_autocorrelation(null_log, lag=3) == pytest.approx(0.0, abs=0.05)

    def test_phase_conditioning_changes_nothing(self, null_log: EventLog) -> None:
        """With no periodic structure there is nothing for phase to explain."""
        conditioned = phase_conditioned_dispersion(null_log, SEASONAL_PERIOD)
        assert conditioned == pytest.approx(1.0, abs=0.1)

    def test_spectrum_has_no_dominant_line(self, null_log: EventLog) -> None:
        """A white spectrum: the largest bin is a fluctuation, not a line."""
        assert spectral_peak_prominence(null_log) < 25.0


# ==========================================================================
# The five discriminators actually discriminate
# ==========================================================================


class TestDiscriminators:
    """Each discriminator separates the mechanism SPEC §4.2 assigns it to."""

    def test_phase_conditioning_collapses_only_seasonality(self) -> None:
        """The diagnostic that rules seasonality in or out.

        Conditioning on phase leaves a homogeneous Poisson process if and only
        if the rate is a deterministic function of phase, so the ratio of
        conditioned to unconditioned dispersion collapses for seasonality and
        stays near 1 for mechanisms whose clustering is not phase-locked.
        """
        ratios = {}
        for name in ("seasonality", "hawkes", "regime_switching", "poisson_mixture"):
            log = run(mechanism_defect(name))
            unconditioned = fano_factor(log, 1.0)
            conditioned = phase_conditioned_dispersion(log, SEASONAL_PERIOD)
            ratios[name] = conditioned / unconditioned

        assert ratios["seasonality"] < 0.6, ratios
        for name in ("hawkes", "regime_switching", "poisson_mixture"):
            assert ratios[name] > 0.85, ratios
        assert ratios["seasonality"] < min(
            ratios[other] for other in ("hawkes", "regime_switching", "poisson_mixture")
        )

    def test_spectral_peak_finds_the_seasonal_period(self) -> None:
        """A fixed spectral peak at 1/period, unique to seasonality."""
        log = run(mechanism_defect("seasonality"))
        expected = 1.0 / SEASONAL_PERIOD
        assert spectral_peak_frequency(log) == pytest.approx(expected, rel=0.1)

    def test_spectral_prominence_ranks_seasonality_highest(self) -> None:
        prominences = {
            name: spectral_peak_prominence(run(mechanism_defect(name)))
            for name in ("seasonality", "hawkes", "regime_switching", "poisson_mixture")
        }
        assert prominences["seasonality"] == max(prominences.values()), prominences
        assert prominences["seasonality"] > 3.0 * max(
            prominences[other]
            for other in ("hawkes", "regime_switching", "poisson_mixture")
        ), prominences

    def test_seasonal_peak_is_reproducible_across_seeds(self) -> None:
        """What makes it a signature: the location is stable, not the height."""
        expected = 1.0 / SEASONAL_PERIOD
        for seed in (Seed(1), Seed(2), Seed(3)):
            log = run(mechanism_defect("seasonality"), seed=seed)
            assert spectral_peak_frequency(log) == pytest.approx(expected, rel=0.1)

    def test_run_lengths_separate_temporal_from_static_clustering(self) -> None:
        """Above-average runs are geometric only when windows are independent.

        The reference Poisson process and the renewal mixture both satisfy that,
        so their deviation is near zero; every mechanism that clusters in time
        departs from it. This is the statistic's real content, and it is the
        opposite of what a naive reading suggests -- see the docstring for why
        the regime's *own* sojourn law is not recoverable by thresholding.

        Thresholds are set well inside the spread measured over eight seeds:
        the two independent-window cases stayed at or below 0.036, and the three
        clustered ones at or above 0.148.
        """
        deviations = {
            name: run_length_geometric_deviation(run(mechanism_defect(name)))
            for name in ("hawkes", "regime_switching", "seasonality", "poisson_mixture")
        }
        deviations["reference"] = run_length_geometric_deviation(run(frozenset()))

        for name in ("reference", "poisson_mixture"):
            assert deviations[name] < 0.06, deviations
        for name in ("hawkes", "regime_switching", "seasonality"):
            assert deviations[name] > 0.10, deviations

    def test_high_runs_are_longer_under_clustering(self) -> None:
        """The mixture has no temporal structure, so its runs are shortest."""
        lengths = {
            name: mean_high_run_length(run(mechanism_defect(name)))
            for name in ("regime_switching", "hawkes", "poisson_mixture")
        }
        assert lengths["poisson_mixture"] < lengths["regime_switching"], lengths
        assert lengths["poisson_mixture"] < 1.8, lengths

    def test_arrival_mechanisms_leave_marks_and_signs_alone(self) -> None:
        """Negative control: no SPEC §4.2 mechanism touches size or sign.

        A non-null value here would mean a defect leaked across components, or
        that seed derivation is not independent per component.
        """
        for name in ("hawkes", "regime_switching", "seasonality", "poisson_mixture"):
            log = run(mechanism_defect(name))
            assert size_dispersion(log) == pytest.approx(1.0, abs=0.08), name
            assert sign_autocorrelation(log) == pytest.approx(0.0, abs=0.05), name


# ==========================================================================
# Guard rails
# ==========================================================================


class TestGuards:
    """Degenerate inputs raise typed errors rather than returning nonsense."""

    @pytest.mark.parametrize("period", [0.0, -1.0])
    def test_non_positive_period_is_rejected(
        self, null_log: EventLog, period: float
    ) -> None:
        from sciagent.core.errors import ExecutionError

        with pytest.raises(ExecutionError):
            phase_conditioned_dispersion(null_log, period)

    def test_too_few_phase_bins_is_rejected(self, null_log: EventLog) -> None:
        from sciagent.core.errors import ExecutionError

        with pytest.raises(ExecutionError):
            phase_conditioned_dispersion(null_log, 10.0, n_phase_bins=1)

    def test_zero_lag_is_rejected(self, null_log: EventLog) -> None:
        from sciagent.core.errors import ExecutionError

        with pytest.raises(ExecutionError):
            sign_autocorrelation(null_log, lag=0)

    def test_diagnostics_are_deterministic(self) -> None:
        """Same seed, same numbers: these feed the registry's content hashes."""
        first = run(mechanism_defect("seasonality"), n_events=4000)
        second = run(mechanism_defect("seasonality"), n_events=4000)
        assert spectral_peak_frequency(first) == spectral_peak_frequency(second)
        assert phase_conditioned_dispersion(
            first, SEASONAL_PERIOD
        ) == phase_conditioned_dispersion(second, SEASONAL_PERIOD)
        assert run_length_geometric_deviation(first) == run_length_geometric_deviation(
            second
        )
