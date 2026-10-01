"""Versioned identities for the slice's diagnostics (v1 SPEC §4.3, §6.3).

``diagnostics.py`` holds the computations; this module holds their *identities*.
The split matters because the identity is what a registered result is addressed
by: :attr:`MetricRegistry.version` enters every
:class:`~sciagent.registry.store.ExperimentKey`, so revising an estimator without
revising its version would silently change the meaning of rows already recorded.

Several diagnostics take a window, a period or a lag. A metric in the registry is
a function of an event log and nothing else, so each is instantiated here at a
declared operating point and named for it -- ``fano_factor_w2`` is the Fano
factor at window 2.0, and it is a different metric from the same estimator at
window 0.5, because it answers a different question. The reference window is the
one the four mechanisms are calibrated at (see ``docs/v1/DECISIONS.md``), so a
result recorded against it is comparable across all of them.
"""

from __future__ import annotations

import math
from functools import partial

from environments.pointproc import diagnostics
from environments.pointproc.mechanisms import SEASONALITY
from sciagent.core.types import MetricName
from sciagent.registry.metrics import (
    MetricCompute,
    MetricRef,
    MetricRegistry,
    MetricSpec,
)

#: The window the mechanisms are calibrated at. Their dispersion agrees here and
#: nowhere else, so it is the window at which "confounded" is a true statement.
REFERENCE_WINDOW = 2.0

#: Window for run-length and phase statistics. Narrower than the reference
#: window, so that a 256-event run still yields enough windows per phase bin.
RUN_WINDOW = 1.0

#: Bin width for the spectral estimates, in time units.
SPECTRAL_BIN = 0.25

#: The period a seasonality hypothesis proposes. Phase-conditioning is only
#: meaningful relative to a candidate period, and this is the one the hypothesis
#: under test supplies -- it is not privileged knowledge of the ground truth,
#: since an investigator reads it off the spectral peak.
CANDIDATE_PERIOD = SEASONALITY.parameters["period"]

#: Version borne by every metric below. Bump a metric's own version when its
#: estimator changes; the registry version then changes with it.
#:
#: Bumped to 1.1.0 on 2026-08-15. Every estimator in ``diagnostics.py`` now folds
#: through :mod:`sciagent.core.reductions` rather than ``np.mean``, ``np.var``
#: and ``np.dot``, so no value here depends on which SIMD or BLAS kernel the CPU
#: dispatched to. The values move in the last places, which is exactly why this
#: is a version event and not a refactor: a result stored under 1.0.0 was
#: computed by an estimator whose summation order the machine chose, and must
#: not be compared against one computed by an estimator that folds exactly.
#:
#: This does *not* make every estimator portable, and the note is here so the
#: version is not read as claiming more than it does. The two spectral metrics
#: still route through ``np.fft.rfft``, for which no exact-rounding substitute
#: exists, and any estimator consuming a transcendental inherits that function's
#: rounding. See :mod:`sciagent.core.reductions` for what is and is not closed.
#:
#: Bumped to 1.2.0 on 2026-08-16, when ``size_gap_correlation`` joined the
#: catalogue. This is an *addition* rather than a change to an estimator, so
#: every value stored under 1.1.0 is still the number 1.2.0 would compute -- but
#: the registry addresses an experiment over the whole catalogue, and a result
#: recorded against a catalogue that could not see the mark-arrival coupling was
#: produced by a system that could not run the experiment which detects S11. The
#: two are not comparable, so the version moves. v1 SPEC §4.3 gains an entry with
#: it; ``docs/v1/DECISIONS.md`` records the measurement that licensed the change.
METRIC_VERSION = "1.2.0"


def _spec(
    name: str,
    compute: MetricCompute,
    *,
    low: float = 0.0,
    high: float = math.inf,
) -> MetricSpec:
    return MetricSpec(
        ref=MetricRef(name=MetricName(name), version=METRIC_VERSION),
        compute=compute,
        low=low,
        high=high,
    )


def metric_registry() -> MetricRegistry:
    """Return the slice's metric catalogue.

    Guarantees a fixed set of metrics at a fixed set of operating points, and
    therefore a stable :attr:`MetricRegistry.version`: the content address of
    every experiment registered against this catalogue depends on it.
    """
    return MetricRegistry.of(
        (
            # -- the three matched dispersion diagnostics ------------------
            _spec("mean_rate", diagnostics.mean_rate),
            _spec("inter_arrival_dispersion", diagnostics.inter_arrival_dispersion),
            _spec(
                "fano_factor_w2",
                partial(diagnostics.fano_factor, window=REFERENCE_WINDOW),
            ),
            _spec(
                "count_autocorrelation_w2",
                partial(
                    diagnostics.count_autocorrelation, window=REFERENCE_WINDOW, lag=1
                ),
                low=-1.0,
                high=1.0,
            ),
            # -- seasonality's discriminators ------------------------------
            _spec(
                "spectral_peak_frequency",
                partial(diagnostics.spectral_peak_frequency, bin_width=SPECTRAL_BIN),
            ),
            _spec(
                "spectral_peak_prominence",
                partial(diagnostics.spectral_peak_prominence, bin_width=SPECTRAL_BIN),
                low=1.0,
            ),
            _spec(
                "phase_conditioned_dispersion",
                partial(
                    diagnostics.phase_conditioned_dispersion,
                    period=CANDIDATE_PERIOD,
                    window=RUN_WINDOW,
                ),
            ),
            # -- regime switching's discriminators -------------------------
            _spec(
                "mean_high_run_length",
                partial(diagnostics.mean_high_run_length, window=RUN_WINDOW),
                low=1.0,
            ),
            _spec(
                "run_length_geometric_deviation",
                partial(diagnostics.run_length_geometric_deviation, window=RUN_WINDOW),
            ),
            # -- the non-arrival components --------------------------------
            _spec("size_mean", diagnostics.size_mean),
            _spec("size_dispersion", diagnostics.size_dispersion),
            _spec("size_skewness", diagnostics.size_skewness, low=-math.inf),
            _spec(
                "sign_autocorrelation",
                partial(diagnostics.sign_autocorrelation, lag=1),
                low=-1.0,
                high=1.0,
            ),
            # -- the mark-arrival coupling ---------------------------------
            # The only cross-component statistic in the catalogue, and the one
            # that makes S11's out-of-library mechanism visible to Stage A at
            # all. See diagnostics.size_gap_correlation and docs/v1/DECISIONS.md.
            _spec(
                "size_gap_correlation",
                diagnostics.size_gap_correlation,
                low=-1.0,
                high=1.0,
            ),
        )
    )
