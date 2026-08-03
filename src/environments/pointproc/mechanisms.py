"""The four confounded mechanisms of SPEC §4.2, as grammar edits.

Every mechanism is a single edit on ``arrival``, of a different edit type, and
all four are calibrated to a common operating point: mean rate 1.0,
inter-arrival dispersion and count Fano factor matched. The calibration is the
substance of SPEC §4.2's claim that they are "indistinguishable under basic
dispersion diagnostics" -- if any one of them were separable by a single moment
the whole slice would be worthless, so the numbers are measured, not assumed
(see ``scripts/confounding_check.py``).

+--------------------------+------------------------------+
| Mechanism                | Edit type                    |
+==========================+==============================+
| Hawkes self-excitation   | ``AddDependency``            |
| Latent regime switching  | ``AddLatentVariable``        |
| Deterministic seasonality| ``ReparameteriseComponent``  |
| Poisson mixture          | ``ChangeDistributionFamily`` |
+--------------------------+------------------------------+

Parameters are grid points of the grammar in ``grammar.py``, chosen by the
search in ``scripts/calibrate_mechanisms.py``, then frozen here as literals.
"""

from __future__ import annotations

from collections.abc import Sequence

from environments.pointproc.components import (
    ARRIVAL,
    MIXTURE_OF_EXPONENTIAL_2,
    MIXTURE_OF_POISSON_2,
    POISSON_PERIODIC,
    SIZE,
    TWO_STATE_MARKOV,
)
from environments.pointproc.grammar import (
    EXPONENTIAL_KERNEL,
    EXPONENTIAL_MIXTURE_GRIDS,
    HAWKES_GRIDS,
    PERIODIC_GRIDS,
    POISSON_MIXTURE_GRIDS,
    SIZE_EXCITED_GRIDS,
    TWO_STATE_GRIDS,
)
from sciagent.core.edits import (
    AddDependency,
    AddLatentVariable,
    ChangeDistributionFamily,
    Defect,
    Edit,
    ParameterGrid,
    ReparameteriseComponent,
)
from sciagent.core.types import FrozenDict, Parameters


def on_grid(grids: Sequence[ParameterGrid], **values: float) -> Parameters:
    """Return ``values`` snapped to their declared grids.

    Guarantees the result is grammar-valid. Calibration works in the continuum
    and lands here; the literals below are already grid points, so snapping is
    an assertion rather than a correction.
    """
    lookup = {grid.name: grid for grid in grids}
    return FrozenDict[str, float](
        {name: lookup[name].snap(value) for name, value in values.items()}
    )


# --------------------------------------------------------------------------
# The four mechanisms
# --------------------------------------------------------------------------

#: Hawkes self-excitation. Mean rate is ``base_rate/(1 - branching)``.
#: Discriminated by ``ForceArrival``: a forced arrival raises the subsequent
#: rate. Unique to this mechanism.
#: Calibrated: rate 0.994, cv2 3.500, F2 3.330.
HAWKES: Edit = AddDependency(
    source=ARRIVAL,
    target=ARRIVAL,
    kernel=EXPONENTIAL_KERNEL,
    parameters=on_grid(
        HAWKES_GRIDS,
        base_rate=0.29935772947204886,
        branching=0.699047619047619,
        decay=0.9283177667225556,
    ),
)

#: Latent two-state regime switching. Discriminated by the geometric run-length
#: distribution of high-rate periods, and by conditioning on the inferred state.
#: Calibrated: rate 1.001, cv2 3.551, F2 3.264, count autocorrelation 0.480.
#: The autocorrelation is matched to the Hawkes value deliberately: SPEC §4.2
#: assigns that pair to stage 3 of the minimum plan, so no dispersion
#: diagnostic may separate them. Calibrating without that constraint left them
#: separable at about four standard deviations on autocorrelation alone.
REGIME_SWITCHING: Edit = AddLatentVariable(
    target=ARRIVAL,
    spec=TWO_STATE_MARKOV,
    parameters=on_grid(
        TWO_STATE_GRIDS,
        mult_low=0.2714417616594907,
        mult_high=2.8737714334780025,
        switch_rate=0.2924017738212867,
        p_high=0.27904761904761904,
    ),
)

#: Deterministic periodic rate. Discriminated by phase-conditioning, which
#: removes the overdispersion entirely, and by a fixed spectral peak.
#: Calibrated: rate 1.004, cv2 3.378, F2 3.111.
SEASONALITY: Edit = ReparameteriseComponent(
    target=ARRIVAL,
    parameterisation=POISSON_PERIODIC,
    parameters=on_grid(
        PERIODIC_GRIDS,
        base_rate=0.4159562163071847,
        amplitude=2.0797810815359234,
        period=11.559385768306628,
    ),
)

#: Two-component Poisson mixture: the rate is redrawn independently at every
#: event. Discriminated by the absence of temporal correlation and the absence
#: of any response to a forced arrival.
#: Calibrated: rate 1.005, cv2 3.200, F2 3.122.
POISSON_MIXTURE: Edit = ChangeDistributionFamily(
    target=ARRIVAL,
    family=MIXTURE_OF_POISSON_2,
    parameters=on_grid(
        POISSON_MIXTURE_GRIDS,
        rate_low=0.47227444482419484,
        rate_high=32.247333855188096,
        weight_high=0.5380952380952381,
    ),
)

#: Scenario S11's out-of-library mechanism: the arrival rate is excited by the
#: sizes of preceding marks. Licensed by ``edit_grammar`` and not by
#: ``agent_grammar``. Calibrated to the same operating point as the other four,
#: so that detecting it is a matter of model inadequacy rather than of noticing
#: an odd summary statistic. Calibrated: rate 0.990, cv2 3.290, F2 3.444.
SIZE_EXCITATION: Edit = AddDependency(
    source=SIZE,
    target=ARRIVAL,
    kernel=EXPONENTIAL_KERNEL,
    parameters=on_grid(
        SIZE_EXCITED_GRIDS,
        base_rate=0.29935772947204886,
        excitation=0.699047619047619,
        decay=0.5987154589440978,
    ),
)


#: The size-distribution mixture of scenario S8, which pairs it with seasonality
#: (SPEC §4.5). Calibrated only in the one respect that matters for it to be a
#: fair test: the mean mark size is preserved at 0.9988, within 0.2% of the
#: reference, so the defect does not announce itself through a nuisance moment.
#: Its squared coefficient of variation is 7.45 against the reference's 1.0.
#:
#: Unlike the arrival mechanisms this one is not calibrated to be *confounded*
#: with anything -- S8 tests decomposition, not discrimination, and nothing in
#: the slice is meant to be mistaken for it. That is what makes it the control
#: arm of acceptance test A9: a misspecification the posterior predictive check
#: should detect nearly always, standing against ``SIZE_EXCITATION``, which it
#: should barely detect at all.
SIZE_MIXTURE: Edit = ChangeDistributionFamily(
    target=SIZE,
    family=MIXTURE_OF_EXPONENTIAL_2,
    parameters=on_grid(
        EXPONENTIAL_MIXTURE_GRIDS,
        mean_low=0.315811383485066,
        mean_high=5.70784988166157,
        weight_high=0.12666666666666665,
    ),
)


#: The four mechanisms of SPEC §4.2, keyed by their scenario name. Insertion
#: order is the order of the table in the specification.
CONFOUNDED_MECHANISMS: dict[str, Edit] = {
    "hawkes": HAWKES,
    "regime_switching": REGIME_SWITCHING,
    "seasonality": SEASONALITY,
    "poisson_mixture": POISSON_MIXTURE,
}


def defect(*edits: Edit) -> Defect:
    """Return a defect from the given edits."""
    return frozenset(edits)


def mechanism_defect(name: str) -> Defect:
    """Return the single-edit defect for a named mechanism."""
    return frozenset({CONFOUNDED_MECHANISMS[name]})
