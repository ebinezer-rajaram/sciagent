"""The pointproc truth-sampler configuration (SPEC §3): preregistration material.

Everything the domain-independent sampler (``sciagent.scenarios``) needs from
pointproc, and every declared number of the truth population. Changing any
value here changes the population, so it is a LOG.md decision and part of the
SPEC §6.1 freeze (``sampler.config_digest`` hashes it).
"""

from __future__ import annotations

import math
from typing import Final

from environments.pointproc.v2 import CHANNELS, LIBRARY, mark_sampler
from sciagent.glm.fit import FitConfig
from sciagent.glm.grammar import (
    Above,
    ExpOf,
    InvalidStructureError,
    KernelKind,
    Link,
    Mark,
    MarkFn,
    One,
    Pow,
    Source,
    SourceKind,
)
from sciagent.library.mixture import PoissonMixture2
from sciagent.library.mmpp import MMPP2
from sciagent.scenarios.calibrate import CalibrationSettings, ThetaPrior
from sciagent.scenarios.identify import GrammarScorer, LibraryScorer, ModelScorer
from sciagent.scenarios.prior import StructurePrior
from sciagent.scenarios.sampler import SamplerConfig, SamplerEnvironment

# --------------------------------------------------------------------------
# Structure prior
# --------------------------------------------------------------------------

#: The generative grammar's declared probabilities (``scenarios/prior.py``).
#:
#: - Links: identity is the classical (Hawkes) form and gets half; exp and
#:   softplus share the rest.
#: - K ∈ 1..4, weighted to 2-3 features: one feature is usually near the
#:   library, four make every fit slow (P1 timings: ~10-20 s per feature).
#: - In-dictionary features are depth 1 or 2 evenly. Depth-3 shapes favour one
#:   gate on a product (``(2, 1)``) and two gates on an atom; products of three
#:   or four atoms are rarer, because a product of excitations is polynomial in
#:   history and mostly fails stationarity.
#: - ``Trend`` has probability 0: ``t / T`` depends on the horizon of the data
#:   being simulated, so a truth containing it is a different process in every
#:   experiment, and it is non-stationary by construction.
#: - Kernels favour ``ExpK`` because ``PowerK`` and ``GammaK`` are O(n²) to
#:   simulate and fit; both still appear in half the excitations.
STRUCTURE_PRIOR: Final = StructurePrior(
    link_probs=((Link.IDENTITY, 0.5), (Link.EXP, 0.25), (Link.SOFTPLUS, 0.25)),
    n_features_probs=((1, 0.25), (2, 0.4), (3, 0.25), (4, 0.1)),
    depth_probs=((1, 0.5), (2, 0.5)),
    shape_probs=(
        ((1, 0), 1.0 / 3.0),  # depth 1
        ((1, 1), 1.0 / 6.0),  # depth 2: a gated atom
        ((2, 0), 1.0 / 6.0),  # depth 2: a product
        ((1, 2), 0.25 / 3.0),  # depth 3: shares within the class below
        ((2, 1), 0.35 / 3.0),
        ((2, 2), 0.1 / 3.0),
        ((3, 0), 0.15 / 3.0),
        ((3, 1), 0.1 / 3.0),
        ((4, 0), 0.05 / 3.0),
    ),
    atom_kind_probs=(("Excite", 0.75), ("Periodic", 0.25), ("Trend", 0.0)),
    kernel_probs=(
        (KernelKind.EXP, 0.5),
        (KernelKind.GAMMA, 0.25),
        (KernelKind.POWER, 0.25),
    ),
    mark_probs=(
        ("One", 0.3),
        ("Mark", 0.25),
        ("Pow", 0.15),
        ("ExpOf", 0.1),
        ("Above", 0.2),
    ),
    source_probs=(
        (SourceKind.ALL, 0.6),
        (SourceKind.POSITIVE, 0.2),
        (SourceKind.NEGATIVE, 0.2),
    ),
    cond_probs=(("PhaseWindow", 0.5), ("LastMarkAbove", 0.5)),
)

#: Truth ψ grids that differ from the fitting grids (``glm/grids.py``).
#: ``ExpOf(size)`` is ``e^{a(m-1)}`` with ``m ~ Exp(1)``, whose mean
#: ``e^{-a}/(1-a)`` is finite only for ``a < 1`` (LOG 2026-10-01): at any
#: larger grid value a single large mark carries unbounded excitation, so the
#: branching ratio is infinite. Truths use ``a = 0.5`` only; fits still profile
#: the whole grid.
TRUTH_PSI_GRIDS: Final[dict[str, tuple[float, ...]]] = {"exp_coef": (0.5,)}


# --------------------------------------------------------------------------
# Mark moments (for the identity-link branching bound)
# --------------------------------------------------------------------------


def mark_mean(mark: MarkFn, psi: float | None, source: Source) -> float:
    """``E[|f(m)| · 1[event in source]]`` under :func:`v2.mark_sampler`, exactly.

    ``size ~ Exp(1)`` and ``sign`` a fair ±1, independent, so a signed source
    halves the moment. With ``location = scale = 1``: ``E[size^a] = Γ(1 + a)``;
    ``E[e^{a(size - 1)}] = e^{-a} / (1 - a)`` for ``a < 1`` and infinite
    otherwise; ``P(size - 1 > q) = e^{-(1 + q)}``; ``|sign| = 1``.
    """
    share = 1.0 if source.kind is SourceKind.ALL else 0.5
    match mark:
        case One() | Mark():
            moment = 1.0
        case Pow():
            moment = math.gamma(1.0 + _psi(psi))
        case ExpOf():
            a = _psi(psi)
            moment = math.exp(-a) / (1.0 - a) if a < 1.0 else math.inf
        case Above():
            moment = math.exp(-(1.0 + _psi(psi)))
    return share * moment


def _psi(psi: float | None) -> float:
    if psi is None:
        raise InvalidStructureError("this mark function needs its ψ value")
    return psi


# --------------------------------------------------------------------------
# θ prior and calibration
# --------------------------------------------------------------------------

#: θ prior (``scenarios/calibrate.py``). Identity: features carry 30-80% of the
#: rate before calibration, spanning weak to strong clustering (the library's
#: Hawkes and S11 truths have branching 0.70). Exp/softplus: each feature moves
#: the log-rate by 0.3-1.2 standard deviations, a third of them inhibitory. The
#: reference log is a rate-1 Poisson process 1,000 mean gaps long.
THETA_PRIOR: Final = ThetaPrior(
    identity_total=(0.3, 0.8),
    effect=(0.3, 1.2),
    negative_prob=1.0 / 3.0,
    reference_horizon=1000.0,
)

#: Calibration (``scenarios/calibrate.py``).
#:
#: - Two pilot stages: 500 mean gaps to ±10%, then 5,000 to ±3%, each with
#:   common random numbers. At 5,000 a Hawkes(0.7) log's rate has sd ≈ 0.05
#:   between seeds, so ±3% is a statement about this pilot, not the population
#:   rate; the identifiability logs re-measure it on fresh seeds.
#: - The event cap is three times the horizon (an explosion is read as rate ≥ 3).
#: - The first 10% of every pilot is burn-in.
#: - Dispersion: Fano factor of counts in windows of 2 mean gaps, in [2, 5].
#:   Measured on 20,000-gap logs of v2's library truths (P2): null 1.0;
#:   Hawkes 3.2-3.3; seasonality 3.1; S11 3.3-3.6. The band brackets the
#:   clustered library members by a factor of about 1.5 each way, so a truth is
#:   as overdispersed as the library at this scale and dispersion alone does
#:   not separate them; it excludes near-Poisson truths (which the null nearly
#:   explains) and extreme burstiness.
#: - Drift: the rate on the second half of the 5,000-gap pilot within a factor
#:   1.5 of the first half.
#: - Identity branching bound below 0.9: margin below criticality, where the
#:   rate's variance diverges and calibration becomes noise.
CALIBRATION: Final = CalibrationSettings(
    pilot_horizons=(500.0, 5000.0),
    rate_tols=(0.1, 0.03),
    max_iter=8,
    event_cap_factor=3.0,
    burn_in=0.1,
    fano_window=2.0,
    fano_band=(2.0, 5.0),
    max_drift=1.5,
    max_branching=0.9,
)


# --------------------------------------------------------------------------
# Library scorers
# --------------------------------------------------------------------------


def library_scorers(fit_config: FitConfig | None = None) -> tuple[LibraryScorer, ...]:
    """The whole library as identifiability scorers, in a fixed order.

    The in-grammar members of ``v2.LIBRARY`` (null, Hawkes, seasonality) are
    fitted by the certified fitter; regime switching and the Poisson mixture
    (``sciagent.library``) by their own maximum likelihood. Out-of-grammar
    members have no structure, so they take part in identifiability (SPEC §3,
    §6.4: ORACLE beats B-lib) but not in the distance strata.
    """
    config = fit_config or FitConfig()
    grammar: tuple[LibraryScorer, ...] = tuple(
        GrammarScorer(m.name, m.structure, CHANNELS, config) for m in LIBRARY
    )
    return (*grammar, ModelScorer(MMPP2()), ModelScorer(PoissonMixture2()))


# --------------------------------------------------------------------------
# The sampler configuration
# --------------------------------------------------------------------------

#: The pointproc truth population (``scenarios/sampler.py``).
#:
#: - Identifiability on a 2,000-gap training log and an independent 2,000-gap
#:   held-out log (``interventions.DEFAULT_HORIZON``, the observational budget).
#:   δ = 0.01 nats per held-out event: 20 nats over the log, about five BIC
#:   parameters' worth (½ log 2000 ≈ 3.8 nats each), and roughly 1.5 standard
#:   errors of a per-event log-likelihood difference whose per-event sd is
#:   ~0.3. Smaller margins would admit truths that ORACLE cannot reliably tell
#:   from B-lib, failing SPEC §6.4's first control by chance.
#: - ε = 0.1. The smallest non-zero distance from the library under this prior
#:   is 0.178 (one relabelled leaf, e.g. ``PowerK`` Hawkes) and v1's own
#:   out-of-library truth (S11) is 0.18 from Hawkes, so ε only excludes the
#:   library members themselves; anything a single edit away is a legitimate
#:   near truth.
#: - Strata by nearest-member distance d: near d ≤ 0.6, mid ≤ 0.72, far above.
#:   Over 4,000 prior draws (half out-of-dictionary) with d > ε, these cut at
#:   about 37% / 29% / 34%; 0.6 is itself a heavy atom (one shared feature plus
#:   one unmatched under a different link), kept in near so the cut is not on
#:   a tie.
#: - Out-of-dictionary share 0.5, enforced exactly by stratified sampling: half
#:   the truths are beyond B-sparse's depth-2 dictionary (SPEC §4.1, C4).
#: - Fits use the default certified fitter; candidates run in parallel instead.
SAMPLER_CONFIG: Final = SamplerConfig(
    structure_prior=STRUCTURE_PRIOR,
    truth_psi_grids=tuple(sorted(TRUTH_PSI_GRIDS.items())),
    theta_prior=THETA_PRIOR,
    calibration=CALIBRATION,
    identifiability_horizon=2000.0,
    delta=0.01,
    epsilon=0.1,
    strata=(("near", 0.6), ("mid", 0.72), ("far", 1.0)),
    out_of_dictionary_share=0.5,
    fit_config=FitConfig(),
    max_candidates=2000,
)

#: Names the environment code the sampler reads (channels, mark law and its
#: moments, library); part of ``config_digest``. Bump when any of them changes.
ENVIRONMENT_NAME: Final = "pointproc/v2"


def environment(fit_config: FitConfig | None = None) -> SamplerEnvironment:
    """The pointproc environment as the sampler needs it."""
    return SamplerEnvironment(
        name=ENVIRONMENT_NAME,
        channels=CHANNELS,
        mark_sampler=mark_sampler,
        mark_mean=mark_mean,
        library=library_scorers(fit_config),
    )
