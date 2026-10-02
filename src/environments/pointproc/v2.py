"""The pointproc environment for v2 (SPEC §2.1, §3): channels, marks, library, truths.

Time is in units where the mean rate is 1 (``sciagent.glm.data``). Every named
truth below is calibrated to that operating point *in theory*, not by search:
the stationary mean rate of each has a closed form, and the slow test in
``tests/environments/test_pointproc_v2.py`` confirms it by simulation.

**Channels.** ``sign`` (±1, a fair coin) and ``size`` (Exp(1), as in v1's
``program.py``: ``REFERENCE_MEAN_SIZE = 1``). ``size`` is standardised with
``location = 1.0`` and ``scale = 1.0``, the mean and standard deviation of
Exp(1). Mean rather than median (ln 2) so that ``z = (m - location) / scale``
has mean 0 and variance 1 under the reference distribution: ``Above`` then
thresholds in standard deviations above the mean, and ``Pow`` reads
``(m / location)^a``, the size relative to the mean size. ``ExpOf`` is
``e^{a(m - 1)}``, whose mean under Exp(1) is ``e^{-a} / (1 - a)``: finite only
for ``a < 1``, which the truth sampler's stationarity check must respect.

**Library** (SPEC §3: "v1's four mechanisms plus the null, fitted"). Three
members are in the grammar and are fitted like any proposal: the null (the
intercept-only structure with no features), Hawkes and seasonality. Two are
not functions of observable history (SPEC §2.1) and stay outside the grammar as
fitted special cases: two-state regime switching and the Poisson mixture.
:class:`LibraryModel` sketches the interface they need; neither is implemented
here.

**Truths.** v1's named sanity scenarios as GLMs: the null, in-library Hawkes and
seasonality, and S11's out-of-library size excitation. The v1 shape parameters
(decays, period, amplitude, branching) are kept; the baselines are set so the
stationary mean rate is exactly 1 rather than v1's searched ≈0.99.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from sciagent.glm.data import EventLog
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    KernelKind,
    Link,
    Mark,
    One,
    Periodic,
    PsiSlot,
    Structure,
)
from sciagent.glm.simulate import Coefficients, PsiAssignment, simulate
from sciagent.library.mixture import PoissonMixture2
from sciagent.library.mmpp import MMPP2
from sciagent.systems.v2.systems import GrammarMember, ModelMember

# --------------------------------------------------------------------------
# Channels and marks
# --------------------------------------------------------------------------

SIGN: Final = ChannelSpec("sign", ChannelKind.SIGN, location=0.0, scale=1.0)
SIZE: Final = ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0)
CHANNELS: Final = (SIGN, SIZE)


def mark_sampler(rng: np.random.Generator) -> Mapping[str, float]:
    """One event's marks: size ~ Exp(1), then sign a fair ±1.

    Two uniforms, always in that order (size first, as v1 drew ``size`` before
    ``sign``). Size is the inverse CDF ``-log1p(-u)``, exactly v1's
    ``exponential_variate``, so the stream is a documented function of
    ``rng.random()`` rather than of numpy's exponential sampler.
    """
    size = -math.log1p(-rng.random())
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return {"sign": sign, "size": size}


# --------------------------------------------------------------------------
# Library: in-grammar members
# --------------------------------------------------------------------------

#: The null: a homogeneous Poisson process, λ = θ₀.
NULL: Final = Structure((), Link.IDENTITY)
#: Self-excitation (v1's Hawkes mechanism).
HAWKES: Final = Structure((Excite(KernelKind.EXP, One(), ALL),), Link.IDENTITY)
#: Deterministic seasonality. v1's rate is ``b·exp(A cos 2πt/P)``, a log-linear
#: periodic rate, hence the exp link.
SEASONALITY: Final = Structure((Periodic(),), Link.EXP)
#: S11's out-of-library mechanism: arrivals excited in proportion to size.
SIZE_EXCITED: Final = Structure(
    (Excite(KernelKind.EXP, Mark("size"), ALL),), Link.IDENTITY
)


@dataclass(frozen=True)
class LibraryMember:
    """A library mechanism expressible in the grammar: fitted like any proposal."""

    name: str
    structure: Structure


LIBRARY: Final = (
    LibraryMember("null", NULL),
    LibraryMember("hawkes", HAWKES),
    LibraryMember("seasonality", SEASONALITY),
)


# --------------------------------------------------------------------------
# Library: members outside the grammar
# --------------------------------------------------------------------------

#: The out-of-grammar members (SPEC §2.1: fitted special cases): v1's latent
#: regime switching as a two-state MMPP, and v1's Poisson mixture as a renewal
#: process with two-component exponential gaps.
OUT_OF_GRAMMAR_LIBRARY: Final = (
    ModelMember("regime_switching", MMPP2()),
    ModelMember("poisson_mixture", PoissonMixture2()),
)

#: Everything B-lib fits (SPEC §3: v1's four mechanisms plus the null).
B_LIB_LIBRARY: Final[tuple[GrammarMember | ModelMember, ...]] = (
    *(GrammarMember(m.name, m.structure) for m in LIBRARY),
    *OUT_OF_GRAMMAR_LIBRARY,
)


# --------------------------------------------------------------------------
# Named truths (v1's sanity scenarios)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Truth:
    """A data-generating GLM: structure, ψ and θ, simulated with this environment."""

    name: str
    structure: Structure
    psi: PsiAssignment
    coef: Coefficients

    def simulate(
        self, horizon: float, rng: np.random.Generator, *, max_events: int = 100_000
    ) -> EventLog:
        return simulate(
            self.structure,
            self.psi,
            self.coef,
            CHANNELS,
            mark_sampler,
            horizon,
            rng,
            max_events=max_events,
        )


#: v1's Hawkes branching ratio and decay (``mechanisms.HAWKES``).
HAWKES_BRANCHING: Final = 0.699047619047619
HAWKES_DECAY: Final = 0.9283177667225556
#: v1's seasonality period and amplitude (``mechanisms.SEASONALITY``).
SEASONAL_PERIOD: Final = 11.559385768306628
SEASONAL_AMPLITUDE: Final = 2.0797810815359234
#: ``1 / I₀(A)``: the mean of ``b·exp(A cos)`` over a cycle is ``b·I₀(A)``.
SEASONAL_BASE: Final = 0.41471074243794825
#: v1's S11 excitation and decay (``mechanisms.SIZE_EXCITATION``). E[size] = 1,
#: so the branching ratio equals the coefficient.
SIZE_EXCITATION: Final = 0.699047619047619
SIZE_DECAY: Final = 0.5987154589440978

_EXP_RATE: Final = PsiSlot((0,), "exp_rate")
_PERIOD: Final = PsiSlot((), "period")

TRUTHS: Final[Mapping[str, Truth]] = {
    t.name: t
    for t in (
        Truth("null", NULL, (), Coefficients(1.0, ())),
        Truth(
            "hawkes",
            HAWKES,
            ({_EXP_RATE: HAWKES_DECAY},),
            Coefficients(1.0 - HAWKES_BRANCHING, ((HAWKES_BRANCHING,),)),
        ),
        Truth(
            "seasonality",
            SEASONALITY,
            ({_PERIOD: SEASONAL_PERIOD},),
            Coefficients(math.log(SEASONAL_BASE), ((0.0, SEASONAL_AMPLITUDE),)),
        ),
        Truth(
            "size_excitation",
            SIZE_EXCITED,
            ({_EXP_RATE: SIZE_DECAY},),
            Coefficients(1.0 - SIZE_EXCITATION, ((SIZE_EXCITATION,),)),
        ),
    )
}
