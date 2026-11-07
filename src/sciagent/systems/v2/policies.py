r"""Evidence-selection policies: random, EIG and FSD (SPEC §2.4, §4.2; P4 Δ11).

A policy decides what to observe next: one *design* ``e`` (an experiment) and
one *diagnostic* ``d`` (a statistic read on that experiment's data). It sees
the channels, the observational data, the entertained set (structures fitted
by the framework on all the data so far) and the menus, never the truth. The
runner that executes the chosen experiment is injected
(:class:`ExperimentRunner`), so this module never imports ``environments/``
or the world. No policy score is ever meant for an agent (Δ11 invariant: the
framework writes numbers).

**Menus.** Both are deterministic functions of public inputs:

- :func:`design_menu`: the interventional battery's shapes
  (:func:`~sciagent.scoring.battery.standard_battery`) at
  :data:`POLICY_HORIZON` = 2000, the default horizon of an agent's
  experiment; 8 designs for pointproc, in battery order.
- :func:`diagnostic_menu`: the whole catalogue, in catalogue order. A
  diagnostic with channel arguments appears once per allowed channel, in
  channel order (with two channel arguments, once per pair ``i < j``). Every
  argument is resolved once on the observational log and then frozen
  (time defaults in units of its mean gap), so every replicate computes the
  same statistic. The only required non-channel argument in the catalogue,
  a ``period``, is frozen at ``1 / spectral_peak_frequency`` of the
  observational log, clipped to the argument's bounds.

**Entertained set** (:func:`fit_entertained`). Up to :data:`ENTERTAINED_MAX`
structures, each refitted on all the data at every decision by the certified
fitter (framework-internal, uncharged). A failed or uncertified fit is
dropped (SPEC §2.2). Weights are ``w_k ∝ exp(-BIC_k / 2)``, computed shifted by
the smallest BIC (:func:`bic_weights`).

**Simulation sets.** For a design ``e`` and a simulated model with index
``k``, replicate ``r`` is drawn from
``policy_seed(seed, decision, k, r)`` (``scoring/seeds.py``), run with the
event cap :data:`POLICY_MAX_EVENTS` through
:func:`~sciagent.scoring.battery.simulate_capped`, and *every* menu
diagnostic is read on that one dataset (the whole log, as the tool layer
reads it). A run that stops (past the cap, or a negative identity-link
intensity), and a diagnostic that cannot be computed
(``InsufficientDataError``), are *undefined*. The key has no design index,
so all designs of one decision use common random numbers; ``k`` indexes the
entertained hypotheses, and :data:`REFERENCE_INDEX`,
:data:`PARAMETRIC_INDEX`, :data:`RANDOM_INDEX`, :data:`NULL_INDEX` and
:data:`TIE_INDEX` are reserved for the reference model, an injected parametric
model, the random draw, the FSD null and EIG's strict-subset tie draw.

**Predictive** (:func:`predictive`). The R values of one (e, k, d) become an
atom on "undefined" with weight ``u`` (the undefined share) plus a Gaussian
with the defined values' mean and sample variance (ddof 1), floored at
``(1e-9 (1 + |μ|))²`` as in ``battery.hellinger2``.

**EIG** (:func:`eig`). ``EIG(e, d) = Σ_k w_k KL(p_k ‖ p_w)``, the mutual
information between hypothesis and outcome (evidence-wall note, eq. 0), with
``p_w = Σ_j w_j p_j``. For atom-plus-Gaussian predictives,

    KL(p_k ‖ p_w) = u_k log(u_k / U) + (1 - u_k) E_{N_k}[-log Σ_j w_j e^{c_j(y)}],

``U = Σ_j w_j u_j`` and ``c_j = log((1-u_j) N_j(y) / ((1-u_k) N_k(y)))``.

*Quadrature.* The expectation is a composite Gauss-Legendre rule
(:data:`GL_NODES` nodes per panel) on ``y = μ_k + s_k x``. Panels break every
:data:`PANEL_STEP` standard deviations (sd) of ``p_k`` over ``x ∈ [-12, 12]``
(the standard normal's mass outside is 3.6e-33); for every other component
``j``, every :data:`PANEL_STEP` sd of ``p_j`` within ``±12 s_j`` of ``μ_j``; and
around each point where ``w_j p_j`` crosses ``w_k p_k``, every
:data:`PANEL_STEP` of ``1 / |slope|`` of their log ratio there, over ±12 such
units (clipped to the other panels' span). A narrower component puts a spike of
width ``s_j`` into ``log p_w``; Gauss-Hermite on ``p_k`` steps over it (WP5's
256-node rule gave 0.5009 against 0.6408 at variance ratio 1e4, shift 10 sd),
and its panels resolve it. A wider one carries mass past ``p_k``'s 12 sd, which
counts wherever ``-log p_w`` is still linear in its weight (out to 23 sd of
``p_k`` at w = 1e-113, variance ratio 100), and the crossing is where
``log p_w`` turns, within 0.04 sd there. The rule is deterministic. A component
identical to ``p_k`` contributes ``c_j = 0`` exactly, so identical predictives
give EIG = 0 exactly. Each KL is clamped at 0 from below.

*Precision* (Δ11 item 2, LOG 2026-10-27). On dev the BIC weights reach
``(1 - η, η)`` with η ≈ 1e-113, and EIG ≈ η·KL(p_runner ‖ p_top). Forming
``log Σ_j w_j e^{c_j}`` as ``top + log(Σ …)`` rounds at ε·c, which made the
leader's term 3.1e-18 against a true EIG of 8.1e-114. Here ``g - 1 = Σ_{j≠k}
w_j expm1(c_j)`` (``Σ w = 1``) is summed exactly, never ``1 - w_top``; where
``|log g| < 1/2`` the integrand is ``-(φ (g - 1)) · log1p(g - 1) / (g - 1)``
with ``φ (g - 1)`` taken from each component's own density, elsewhere
``-φ log g`` by log-sum-exp; and the atom's ``log(U / u_k)`` is ``log1p`` of
``Σ_{j≠k} w_j (u_j / u_k - 1)`` near 1. The weights' logarithms
(:func:`bic_log_weights`) give H(w) with the leader's ``-η`` and the KL pick's
``v`` where a float weight underflows. Against a dense log1p trapezoid, EIG's
relative error measured 5e-10 at most over variance ratios 1e-2 to 1e4, shifts
0 to 10 sd and η from 1e-3 down to 1e-113 (2026-11-07); the tests pin 1e-8 or
tighter (``tests/systems_v2/test_policies_eig_precision.py``).

*Tie floor* (:data:`EIG_TIE_TOL`). Against an independent dense-grid reference
(trapezoid, 2·10⁶ points per component), the rule's largest absolute error over
306 two- and three-component cases (variance ratios 10⁻⁴ to 10⁴, shifts 0 to 10
sd, atoms 0 to 0.4, w₁ 0.5 to 0.999, both orders) is
:data:`EIG_QUADRATURE_ERROR` = 2.2e-16, one ulp at 1: the rule is exact to
rounding there. (WP5's Gauss-Hermite rule erred by up to 0.14 on the same
grid.) The tolerance takes a factor of 100 over that, for regimes the grid does
not cover and for the rounding of the sums at EIG's scale (up to log K),
rounded up to a power of ten: ``EIG_TIE_TOL`` = 1e-13 nats. (That grid was
measured on the 2026-10-06 rule; the current rule meets the same dense
reference to the floor, ``test_policies_eig_ties.py``.) EIG values closer
than that to the table's maximum are exact ties (:func:`eig_choice`, Δ11 item
3, adopted 2026-10-28): a *full tie*, every cell within the floor of the
maximum, takes the random arm's draw for the decision (:func:`random_draw` on
:data:`RANDOM_INDEX`), so on it the EIG arm and the random arm of the same
(truth, seed) pick the same cell and their paired contrast is exactly zero; a
*strict-subset tie* draws one tied cell, uniform in menu order, on
:data:`TIE_INDEX`. When the BIC weights are so concentrated that ε·H(w), which
bounds every EIG (the evidence wall itself), is below the floor, the decision
is *degenerate*, hence a full tie; dev-008 at n ≈ 2000 has H(w) ≈ 1e-111. A
posterior only nearly as concentrated (dev-013: H(w) ≈ 3e-13) can leave cells
above the floor, and its pick then follows EIG values that are computed
correctly but are negligible: that is a statement about the weights, not about
the numerics, and :class:`Weighting` is the lever for it. The decision reports
``H(w)``, the maximum EIG, the tie kind and the KL-ranked pick
(:class:`EIGSummary`) so the analysis can split degenerate decisions (item 4).

*KL pick* (:func:`kl_pick`, item 5). ``Σ_{k≠top} v_k KL(p_k ‖ p_top)``, the
η → 0 limit of EIG, from the same predictives; logged on every EIG decision,
never chosen. It is recorded as computed: KL is ``inf`` whenever a rival
stops in a replicate and the leader never does, and stays ``inf``.

*Weights* (:class:`Weighting`). ``"bic"`` (the default, SPEC §2.4's
posterior weights by the BIC approximation): ``w ∝ exp(-ΔBIC/2)``.
``"tempered"`` with temperature T: ``w ∝ exp(-ΔBIC/(2T))``; T = 1 is ``"bic"``
bit for bit, and T → ∞ tends to uniform. The EIG policy applies its own
strategy to the entertained set, and the strategy is part of its
:attr:`EIGPolicy.config`.

**FSD** (:func:`fsd_table`). ``FSD(e, d) = He²(q_par, q_np)``
(``battery.hellinger2`` on the two replicate samples): ``q_par`` is the
top-weight entertained fit (first on ties), ``q_np`` the Wiener-Hopf model
estimated on the observational log with iid empirical marks
(:meth:`~sciagent.systems.v2.models.WienerHopfModel.estimate`). The raw
value is used for selection. :func:`fsd_null` is its negative control
(LOG 2026-10-01): FSD when the fitted model is the truth, by a parametric
bootstrap that re-estimates Wiener-Hopf on observational logs simulated from
the top fit. Even with no estimation error, two R-samples of one law give
``E He² ≈ 1/(4R)`` from the means plus ``≈ 1/(4R)`` from the scales, about
``1/(2R)`` for Gaussian statistics.

**Random.** Uniform over the ``(e, d)`` cells, with replacement, one draw
from ``policy_seed(seed, decision, RANDOM_INDEX, 0)``.

**Choice.** FSD and the truth-aware control take :func:`choose`, the first
cell in menu order (designs first, then diagnostics) at the table's maximum;
EIG takes :func:`eig_choice` with :data:`EIG_TIE_TOL` (above). FSD needs no
floor: He² is
closed-form in the replicate moments (``battery.hellinger2``), so no
numerical method stands between the samples and the value; cell-to-cell
differences are Monte Carlo signal, and exact ties (He² saturated at 1 on a
design where one side always stops) already go to menu order.

**Config.** Each policy's :attr:`config` is canonical JSON (sorted keys) of
everything that changes its choices except the seed, for the run key.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

import numpy as np
from numpy.polynomial.legendre import leggauss

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.diagnostics.catalogue import ArgKind, ArgSpec, ArgValue
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import FitConfig, FitError
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.interventions import Compose, Experiment
from sciagent.glm.simulate import (
    ExplosionError,
    MarkSampler,
    NegativeIntensityError,
)
from sciagent.library.base import LibrarySimulationError
from sciagent.scoring.battery import (
    Simulator,
    hellinger2,
    simulate_capped,
    standard_battery,
)
from sciagent.scoring.seeds import generator, policy_seed
from sciagent.systems.v2.models import GLMModel, ModelError, WienerHopfModel
from sciagent.systems.v2.search import Fitter, certified_fit

#: Horizon of every menu design (native units; an experiment's default).
POLICY_HORIZON: Final = 2000.0
#: Replicates per (design, simulated model).
REPLICATES: Final = 16
#: Largest entertained set.
ENTERTAINED_MAX: Final = 4
#: Event cap of a policy simulation; past it the run is undefined. It is the
#: world's experiment cap (``investigation/world.py`` MAX_EVENTS).
POLICY_MAX_EVENTS: Final = 15_000
#: Gauss-Legendre nodes per panel, and the panel width in standard deviations,
#: of the EIG quadrature (module docstring).
GL_NODES: Final = 8
PANEL_STEP: Final = 0.5
#: Half-width of the integration range, in standard deviations of ``p_k``.
_RANGE: Final = 12.0
#: Identifies the quadrature in a policy's config.
EIG_QUADRATURE: Final = f"composite-gauss-legendre/{GL_NODES}/{PANEL_STEP}/{_RANGE}"
#: Largest |EIG - dense reference| measured over the module docstring's grid
#: (2026-10-06; one ulp at 1, i.e. rounding).
EIG_QUADRATURE_ERROR: Final = 2.220446049250313e-16
#: EIG values within this of the maximum are ties: 100 x the measured error,
#: rounded up to a power of ten (module docstring).
EIG_TIE_TOL: Final = 1e-13
#: Reserved model indices ``k`` of :func:`~sciagent.scoring.seeds.policy_seed`.
REFERENCE_INDEX: Final = 2**32 - 1
RANDOM_INDEX: Final = 2**32 - 2
NULL_INDEX: Final = 2**32 - 3
PARAMETRIC_INDEX: Final = 2**32 - 4
#: The EIG arm's draw among cells tied at the maximum when not every cell
#: ties (Δ11 item 3; a full tie takes :data:`RANDOM_INDEX`'s draw).
TIE_INDEX: Final = 2**32 - 5
#: Relative variance floor of a predictive (as ``battery.hellinger2``).
_FLOOR: Final = 1e-9

_GL_X, _GL_W = leggauss(GL_NODES)
#: Gauss-Legendre nodes and weights on [-1, 1].
_GL_NODES: Final = np.asarray(_GL_X, dtype=np.float64)
_GL_WEIGHTS: Final = np.asarray(_GL_W, dtype=np.float64)
#: Breakpoints of one component's own panels, in its standard deviations.
_STEPS: Final = np.arange(-_RANGE, _RANGE + PANEL_STEP / 2.0, PANEL_STEP)
_LOG_SQRT_2PI: Final = 0.5 * math.log(2.0 * math.pi)

type Table = tuple[tuple[float, ...], ...]
type Values = tuple[tuple[float | None, ...], ...]
#: ``[e][d][k]``: hypothesis k's predictive of cell (e, d).
type PredictiveTable = tuple[tuple[tuple[Predictive, ...], ...], ...]


class PolicyError(SciAgentError):
    """A policy cannot decide on what it was given."""


# --------------------------------------------------------------------------
# Menus
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Design:
    """A named experiment of the design menu."""

    name: str
    experiment: Experiment


@dataclass(frozen=True)
class DiagnosticChoice:
    """A catalogue diagnostic with every argument frozen, sorted by name."""

    name: str
    args: tuple[tuple[str, ArgValue], ...] = ()

    @property
    def label(self) -> str:
        """``name``, or ``name[channel,...]`` for channel arguments."""
        spec = catalogue.get(self.name)
        kinds = {a.name: a.kind for a in spec.args}
        chans = [str(v) for k, v in self.args if kinds.get(k) is ArgKind.CHANNEL]
        return f"{self.name}[{','.join(chans)}]" if chans else self.name

    @property
    def channel_args(self) -> tuple[tuple[str, ArgValue], ...]:
        spec = catalogue.get(self.name)
        kinds = {a.name: a.kind for a in spec.args}
        return tuple((k, v) for k, v in self.args if kinds.get(k) is ArgKind.CHANNEL)


@dataclass(frozen=True)
class Menu:
    """The design and diagnostic menus of one decision."""

    designs: tuple[Design, ...]
    diagnostics: tuple[DiagnosticChoice, ...]

    def __post_init__(self) -> None:
        if not self.designs or not self.diagnostics:
            raise PolicyError("both menus must be non-empty")


def design_menu(
    channels: tuple[ChannelSpec, ...], *, horizon: float = POLICY_HORIZON
) -> tuple[Design, ...]:
    """The battery's shapes at ``horizon``, in battery order (module docstring)."""
    return tuple(
        Design(b.name, b.experiment)
        for b in standard_battery(channels, horizon=horizon)
    )


def _channel_assignments(
    args: Sequence[ArgSpec], channels: tuple[ChannelSpec, ...]
) -> list[dict[str, ArgValue]]:
    """Channel arguments filled in channel order; several take ``i < j`` pairs."""
    if not args:
        return [{}]
    out: list[dict[str, ArgValue]] = []
    for combo in itertools.combinations(channels, len(args)):
        if all(c.kind in a.channel_kinds for a, c in zip(args, combo, strict=True)):
            out.append({a.name: c.name for a, c in zip(args, combo, strict=True)})
    return out


def _frozen_period(
    log: EventLog, channels: tuple[ChannelSpec, ...], arg: ArgSpec
) -> float:
    frequency = catalogue.compute("spectral_peak_frequency", log, channels, {})
    low = arg.low * catalogue.mean_gap(log)
    high = arg.high * log.horizon
    return min(max(1.0 / frequency, low), high)


def diagnostic_menu(
    log: EventLog, channels: tuple[ChannelSpec, ...]
) -> tuple[DiagnosticChoice, ...]:
    """The catalogue with arguments frozen on ``log`` (module docstring)."""
    out: list[DiagnosticChoice] = []
    for name in catalogue.names():
        spec = catalogue.get(name)
        channel_args = [a for a in spec.args if a.kind is ArgKind.CHANNEL]
        required = [
            a for a in spec.args if a.kind is not ArgKind.CHANNEL and a.default is None
        ]
        for given in _channel_assignments(channel_args, channels):
            for arg in required:
                if arg.kind is not ArgKind.TIME:
                    raise PolicyError(f"{name}: no frozen value for {arg.name!r}")
                given[arg.name] = _frozen_period(log, channels, arg)
            resolved = catalogue.resolve_args(name, given, log, channels)
            out.append(DiagnosticChoice(name, tuple(sorted(resolved.items()))))
    return tuple(out)


def diagnostic_value(
    choice: DiagnosticChoice, log: EventLog, channels: tuple[ChannelSpec, ...]
) -> float | None:
    """The frozen diagnostic on ``log``; None when undefined."""
    try:
        return catalogue.compute(
            choice.name, log, channels, dict(choice.args), check_bounds=False
        )
    except catalogue.InsufficientDataError:
        return None


# --------------------------------------------------------------------------
# Entertained set
# --------------------------------------------------------------------------


def bic_weights(
    bics: Sequence[float], *, temperature: float = 1.0
) -> tuple[float, ...]:
    """``w_k ∝ exp(-(BIC_k - min BIC) / (2 T))``; T = 1 is the BIC posterior."""
    if not bics:
        raise PolicyError("no BICs to weigh")
    if not all(math.isfinite(b) for b in bics):
        raise PolicyError(f"BICs must be finite, got {tuple(bics)}")
    if not (math.isfinite(temperature) and temperature > 0.0):
        raise PolicyError(f"temperature must be finite and > 0, got {temperature}")
    low = min(bics)
    raw = [math.exp(-(b - low) / (2.0 * temperature)) for b in bics]
    total = math.fsum(raw)
    return tuple(x / total for x in raw)


def bic_log_weights(
    bics: Sequence[float], *, temperature: float = 1.0
) -> tuple[float, ...]:
    """``log w_k`` of :func:`bic_weights`, computed in log space.

    ``z_k = -(BIC_k - min BIC) / (2 T)`` and ``log w_k = z_k - log Σ e^{z_j}``,
    the normaliser taken as ``log1p`` of the sum without the first minimum's
    term (which is exactly 1). So the leader keeps ``log w_top = -η`` where its
    float weight rounds to 1, and a rival keeps a finite logarithm where its
    float weight underflows to 0 (ΔBIC past about 1490).
    """
    bic_weights(bics, temperature=temperature)  # the same input checks
    low = min(bics)
    z = [-(b - low) / (2.0 * temperature) for b in bics]
    first = z.index(max(z))
    norm = math.log1p(math.fsum(math.exp(x) for j, x in enumerate(z) if j != first))
    return tuple(x - norm for x in z)


type WeightKind = Literal["bic", "tempered"]


@dataclass(frozen=True)
class Weighting:
    """How BICs become weights (module docstring): ``"bic"`` or ``"tempered"``."""

    kind: WeightKind = "bic"
    temperature: float = 1.0

    def __post_init__(self) -> None:
        if self.kind not in ("bic", "tempered"):
            raise PolicyError(f"unknown weighting {self.kind!r}")
        t = self.temperature
        if isinstance(t, bool) or not isinstance(t, int | float):
            raise PolicyError(f"temperature must be a number, got {t!r}")
        if not (math.isfinite(t) and t > 0.0):
            raise PolicyError(f"temperature must be finite and > 0, got {t}")
        if self.kind == "bic" and t != 1.0:
            raise PolicyError("the bic weighting has temperature 1; use tempered")
        object.__setattr__(self, "temperature", float(t))

    def weights(self, bics: Sequence[float]) -> tuple[float, ...]:
        return bic_weights(bics, temperature=self.temperature)

    def log_weights(self, bics: Sequence[float]) -> tuple[float, ...]:
        return bic_log_weights(bics, temperature=self.temperature)

    @property
    def config(self) -> dict[str, object]:
        return {"kind": self.kind, "temperature": self.temperature}


#: The default: the BIC approximation to the posterior weights (SPEC §2.4).
BIC_WEIGHTS: Final = Weighting()


def tempered(temperature: float) -> Weighting:
    """``w ∝ exp(-ΔBIC / (2 T))``."""
    return Weighting("tempered", temperature)


@dataclass(frozen=True)
class Hypothesis:
    """One entertained hypothesis: a fitted model and its BIC."""

    name: str
    model: Simulator
    bic: float


@dataclass(frozen=True)
class EntertainedSet:
    """Hypotheses with BIC weights; ``skipped`` names the dropped candidates."""

    hypotheses: tuple[Hypothesis, ...]
    weights: tuple[float, ...]
    skipped: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.hypotheses:
            raise PolicyError("the entertained set is empty")
        if len(self.weights) != len(self.hypotheses):
            raise PolicyError("one weight per hypothesis")

    @staticmethod
    def of(
        hypotheses: Sequence[Hypothesis],
        skipped: Sequence[str] = (),
        *,
        weighting: Weighting = BIC_WEIGHTS,
    ) -> EntertainedSet:
        if not hypotheses:
            raise PolicyError("the entertained set is empty")
        return EntertainedSet(
            tuple(hypotheses),
            weighting.weights([h.bic for h in hypotheses]),
            tuple(skipped),
        )

    def reweighted(self, weighting: Weighting) -> EntertainedSet:
        """The same hypotheses, weighted by ``weighting``."""
        return EntertainedSet.of(self.hypotheses, self.skipped, weighting=weighting)

    @property
    def top(self) -> int:
        """Index of the top-weight hypothesis, the first on ties."""
        best = 0
        for k, w in enumerate(self.weights):
            if w > self.weights[best]:
                best = k
        return best


def fit_entertained(
    candidates: Sequence[tuple[str, Structure]],
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    marks: MarkSampler,
    *,
    fitter: Fitter = certified_fit,
    config: FitConfig | None = None,
    max_size: int = ENTERTAINED_MAX,
    weighting: Weighting = BIC_WEIGHTS,
) -> EntertainedSet:
    """Refit each candidate on ``datasets``; drop failed or uncertified fits."""
    if not candidates:
        raise PolicyError("no candidate structures")
    if len(candidates) > max_size:
        raise PolicyError(f"{len(candidates)} candidates; at most {max_size}")
    kept: list[Hypothesis] = []
    skipped: list[str] = []
    for name, structure in candidates:
        try:
            result = fitter(structure, datasets, channels, config)
        except FitError:
            skipped.append(name)
            continue
        if not result.certified:
            skipped.append(name)
            continue
        kept.append(
            Hypothesis(name, GLMModel(name, result, channels, marks), result.bic)
        )
    if not kept:
        raise PolicyError(f"no candidate could be fitted: {skipped}")
    return EntertainedSet.of(kept, skipped, weighting=weighting)


# --------------------------------------------------------------------------
# Predictives and EIG
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Predictive:
    """An atom on "undefined" of weight ``undefined`` plus N(mean, var)."""

    undefined: float
    mean: float
    var: float


def predictive(values: Sequence[float | None]) -> Predictive:
    """The atom-plus-Gaussian fit of replicate values (module docstring)."""
    if not values:
        raise PolicyError("a predictive needs at least one replicate")
    defined = np.array([v for v in values if v is not None], dtype=np.float64)
    undefined = (len(values) - defined.size) / len(values)
    if defined.size == 0:
        return Predictive(1.0, 0.0, 1.0)
    if not bool(np.isfinite(defined).all()):
        raise PolicyError("replicate values must be finite or None")
    mean = reductions.mean(defined)
    var = reductions.variance(defined) if defined.size > 1 else 0.0
    floor = (_FLOOR * (1.0 + abs(mean))) ** 2
    return Predictive(undefined, mean, max(var, floor))


def _check_weights(weights: Sequence[float], n: int) -> None:
    if len(weights) != n:
        raise PolicyError(f"{len(weights)} weights for {n} predictives")
    if not all(math.isfinite(w) and w >= 0.0 for w in weights):
        raise PolicyError(f"weights must be finite and non-negative: {tuple(weights)}")
    if abs(math.fsum(weights) - 1.0) > 1e-9:
        raise PolicyError(f"weights must sum to 1, got {math.fsum(weights)}")


def _crossings(
    k: int, j: int, weights: Sequence[float], ps: Sequence[Predictive]
) -> list[float]:
    """The ``x`` (in ``p_k``'s standard units) where ``w_j e^{c_j(x)} = w_k``.

    ``log(w_j / w_k) + c_j(x)`` is quadratic in x: ``a x²/2 + b x + c``.
    """
    pk, p = ps[k], ps[j]
    d = pk.mean - p.mean
    a = 1.0 - pk.var / p.var
    b = -d * math.sqrt(pk.var) / p.var
    c = (
        math.log((1.0 - p.undefined) / (1.0 - pk.undefined))
        - 0.5 * math.log(p.var / pk.var)
        - d * d / (2.0 * p.var)
        + math.log(weights[j])
        - math.log(weights[k])
    )
    if a == 0.0:
        return [] if b == 0.0 else [-c / b]
    disc = b * b - 2.0 * a * c
    if disc < 0.0:
        return []
    q = -(b + math.copysign(math.sqrt(disc), b))
    return [0.0] if q == 0.0 else [q / a, 2.0 * c / q]


def _nodes(
    k: int, ps: Sequence[Predictive], live: Sequence[bool], weights: Sequence[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Composite Gauss-Legendre nodes ``x``, panel weights and ``log φ(x)``.

    In ``p_k``'s standard units: its own panels every :data:`PANEL_STEP` over
    ``±``:data:`_RANGE`; each other live component's panels every
    :data:`PANEL_STEP` of its standard deviations over its own ``±``:data:`_RANGE`;
    and, around each point where that component's weighted density crosses
    ``w_k p_k`` (where ``log p_w`` turns), panels every :data:`PANEL_STEP` over
    ``±``:data:`_RANGE` in units of ``1 / |slope|`` of the log ratio there
    (module docstring). ``E_{N(0,1)}[f] ≈ Σ weights · e^{log φ} · f``.
    """
    pk = ps[k]
    sk = math.sqrt(pk.var)
    breaks = [_STEPS]
    rivals = [j for j, p in enumerate(ps) if j != k and live[j] and p != pk]
    for j in rivals:
        p = ps[j]
        breaks.append((p.mean - pk.mean + math.sqrt(p.var) * _STEPS) / sk)
    lowest = min(float(b[0]) for b in breaks)
    highest = max(float(b[-1]) for b in breaks)
    for j in rivals if weights[k] > 0.0 else ():
        a = 1.0 - pk.var / ps[j].var
        b = -(pk.mean - ps[j].mean) * sk / ps[j].var
        for root in _crossings(k, j, weights, ps):
            slope = abs(a * root + b)
            if slope > 0.0 and lowest <= root <= highest:
                breaks.append(np.clip(root + _STEPS / slope, lowest, highest))
    edges = np.unique(np.concatenate(breaks))
    lo, hi = edges[:-1], edges[1:]
    keep = hi > lo
    lo, hi = lo[keep], hi[keep]
    half = 0.5 * (hi - lo)
    x = ((lo + hi) * 0.5)[:, None] + half[:, None] * _GL_NODES[None, :]
    w = half[:, None] * _GL_WEIGHTS[None, :]
    x, w = x.reshape(-1), w.reshape(-1)
    return x, w, -0.5 * x * x - _LOG_SQRT_2PI


def _kl_to_mixture(k: int, weights: Sequence[float], ps: Sequence[Predictive]) -> float:
    """``KL(p_k ‖ p_w)`` to relative precision (module docstring, *Precision*)."""
    pk = ps[k]
    kl = 0.0
    if pk.undefined > 0.0:
        # U / u_k - 1 = Σ_{j≠k} w_j (u_j / u_k - 1), as Σ w = 1: log1p of it
        # near 1, and the log of the (non-negative) sum itself elsewhere.
        pairs = list(enumerate(zip(weights, ps, strict=True)))
        excess = math.fsum(
            w * (p.undefined / pk.undefined - 1.0) for j, (w, p) in pairs if j != k
        )
        if excess > -0.5:
            kl -= pk.undefined * math.log1p(excess)
        else:
            ratio = math.fsum(w * (p.undefined / pk.undefined) for _, (w, p) in pairs)
            kl -= pk.undefined * math.log(ratio)
    if pk.undefined < 1.0:
        kl += (1.0 - pk.undefined) * _gaussian_part(k, weights, ps)
    return max(kl, 0.0)


def _gaussian_part(k: int, weights: Sequence[float], ps: Sequence[Predictive]) -> float:
    """``E_{N_k}[-log g]``, ``g = Σ_j w_j e^{c_j}`` (module docstring).

    ``g - 1 = Σ_{j≠k} w_j expm1(c_j)`` (``c_k = 0`` and ``Σ w = 1``). Where
    ``|log g| < 1/2`` the integrand is ``-φ log1p(g - 1)``, written as
    ``-(φ (g - 1)) · log1p(g - 1) / (g - 1)`` with ``φ (g - 1)`` summed from
    each component's own density, so it neither rounds against 1 nor
    underflows with φ; elsewhere it is ``-φ log g`` by log-sum-exp.
    """
    pk = ps[k]
    live = [w > 0.0 and p.undefined < 1.0 for w, p in zip(weights, ps, strict=True)]
    x, base, log_phi = _nodes(k, ps, live, weights)
    y = pk.mean + math.sqrt(pk.var) * x
    logs: list[np.ndarray] = []
    rivals: list[tuple[float, np.ndarray | None]] = []
    for j, (w, p, alive) in enumerate(zip(weights, ps, live, strict=True)):
        if w <= 0.0:
            continue
        if not alive:
            rivals.append((w, None))  # all undefined: e^{c_j} = 0
            continue
        if j == k or p == pk:
            c = np.zeros_like(x)
        else:
            shift = math.log((1.0 - p.undefined) / (1.0 - pk.undefined))
            shift -= 0.5 * math.log(p.var / pk.var)
            c = shift - (y - p.mean) ** 2 / (2.0 * p.var) + 0.5 * x * x
        logs.append(math.log(w) + c)
        if j != k:
            rivals.append((w, c))
    a = np.stack(logs, axis=1)
    top = a.max(axis=1)
    log_g = top + np.log(reductions.row_totals(np.exp(a - top[:, None])))
    near = np.abs(log_g) < 0.5
    integrand = -np.exp(log_phi) * log_g
    if bool(near.any()):
        phi = log_phi[near]
        excess: list[np.ndarray] = []  # w_j expm1(c_j), each term ≤ g < 2
        dense: list[np.ndarray] = []  # w_j φ expm1(c_j)
        for w, rival_c in rivals:
            if rival_c is None:
                excess.append(np.full(phi.shape, -w))
                dense.append(-w * np.exp(phi))
                continue
            # w expm1(c) as w expm1(c) for c < 0 and as w e^c (1 - e^{-c})
            # otherwise: both finite on these nodes (w e^c ≤ g), both exact
            # to rounding, and 0 at c = 0.
            cn = rival_c[near]
            low = cn < 0.0
            neg = np.expm1(np.minimum(cn, 0.0))
            pos = -np.expm1(-np.maximum(cn, 0.0))
            own = math.log(w) + np.maximum(cn, 0.0)
            excess.append(np.where(low, w * neg, np.exp(own) * pos))
            dense.append(np.where(low, w * np.exp(phi) * neg, np.exp(own + phi) * pos))
        if excess:
            gm1 = reductions.row_totals(np.stack(excess, axis=1))
            phi_gm1 = reductions.row_totals(np.stack(dense, axis=1))
        else:
            gm1 = np.zeros(phi.shape)
            phi_gm1 = np.zeros(phi.shape)
        nonzero = gm1 != 0.0
        ratio = np.ones(phi.shape)
        ratio[nonzero] = np.log1p(gm1[nonzero]) / gm1[nonzero]
        integrand[near] = -phi_gm1 * ratio
    return reductions.dot(base, integrand)


def eig(weights: Sequence[float], predictives: Sequence[Predictive]) -> float:
    """``Σ_k w_k KL(p_k ‖ p_w)`` for atom-plus-Gaussian predictives."""
    _check_weights(weights, len(predictives))
    terms = [
        w * _kl_to_mixture(k, weights, predictives)
        for k, w in enumerate(weights)
        if w > 0.0
    ]
    return math.fsum(terms)


# --------------------------------------------------------------------------
# Simulation sets
# --------------------------------------------------------------------------

#: What a stopped simulation raises (as in the battery).
STOPPED: Final = (
    ExplosionError,
    NegativeIntensityError,
    LibrarySimulationError,
    ModelError,
)


@dataclass(frozen=True)
class Runs:
    """``values[d][r]`` of one simulation set, and which replicates stopped."""

    values: Values
    stopped: tuple[bool, ...]


def replicate_runs(
    simulator: Simulator,
    design: Design,
    diagnostics: Sequence[DiagnosticChoice],
    channels: tuple[ChannelSpec, ...],
    rng_for: Callable[[int], np.random.Generator],
    replicates: int,
    max_events: int = POLICY_MAX_EVENTS,
) -> Runs:
    """Replicate r drawn from ``rng_for(r)``; every diagnostic read on it."""
    if replicates < 1:
        raise PolicyError(f"need at least one replicate, got {replicates}")
    values: list[list[float | None]] = [[] for _ in diagnostics]
    stopped: list[bool] = []
    for r in range(replicates):
        try:
            data: Dataset | None = simulate_capped(
                simulator, design.experiment, rng_for(r), design.name, max_events
            )
        except STOPPED:
            data = None
        stopped.append(data is None)
        for d, choice in enumerate(diagnostics):
            values[d].append(
                None if data is None else diagnostic_value(choice, data.log, channels)
            )
    return Runs(tuple(tuple(v) for v in values), tuple(stopped))


def replicate_values(
    simulator: Simulator,
    design: Design,
    diagnostics: Sequence[DiagnosticChoice],
    channels: tuple[ChannelSpec, ...],
    rng_for: Callable[[int], np.random.Generator],
    replicates: int,
    max_events: int = POLICY_MAX_EVENTS,
) -> Values:
    """``values[d][r]``: a stopped replicate is undefined on every diagnostic."""
    return replicate_runs(
        simulator, design, diagnostics, channels, rng_for, replicates, max_events
    ).values


def simulate_values(
    simulator: Simulator,
    design: Design,
    diagnostics: Sequence[DiagnosticChoice],
    channels: tuple[ChannelSpec, ...],
    seed: int,
    decision: int,
    k: int,
    replicates: int,
    *,
    extra: tuple[int, ...] = (),
    max_events: int = POLICY_MAX_EVENTS,
) -> Values:
    """One simulation set on ``policy_seed(seed, decision, k, r, *extra)``."""

    def rng_for(r: int) -> np.random.Generator:
        return generator(policy_seed(seed, decision, k, r, *extra))

    return replicate_values(
        simulator, design, diagnostics, channels, rng_for, replicates, max_events
    )


# --------------------------------------------------------------------------
# Decision context, tables and choice
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionContext:
    """What a policy sees at one decision (module docstring)."""

    channels: tuple[ChannelSpec, ...]
    observational: Dataset
    entertained: EntertainedSet
    menu: Menu


def _check_table(table: Table, tolerance: float) -> None:
    if not table or not table[0]:
        raise PolicyError("an empty score table")
    if not (math.isfinite(tolerance) and tolerance >= 0.0):
        raise PolicyError(f"tolerance must be finite and >= 0, got {tolerance}")
    for e, row in enumerate(table):
        for d, x in enumerate(row):
            if not math.isfinite(x):
                raise PolicyError(f"score ({e}, {d}) is not finite: {x}")


def choose(table: Table, *, tolerance: float = 0.0) -> tuple[int, int]:
    """The first ``(e, d)`` in menu order within ``tolerance`` of the maximum."""
    return tied_cells(table, tolerance)[0]


def tied_cells(table: Table, tolerance: float) -> list[tuple[int, int]]:
    """Every ``(e, d)`` within ``tolerance`` of the maximum, in menu order."""
    _check_table(table, tolerance)
    best = max(max(row) for row in table)
    return [
        (e, d)
        for e, row in enumerate(table)
        for d, x in enumerate(row)
        if best - x <= tolerance
    ]


def random_draw(
    seed: int, decision: int, n_designs: int, n_diagnostics: int
) -> tuple[int, int]:
    """The random arm's cell: one integer over the cells from
    ``policy_seed(seed, decision, RANDOM_INDEX, 0)``, designs first."""
    rng = generator(policy_seed(seed, decision, RANDOM_INDEX, 0))
    return divmod(int(rng.integers(n_designs * n_diagnostics)), n_diagnostics)


def eig_choice(
    table: Table, tolerance: float, seed: int, decision: int
) -> tuple[int, int]:
    """EIG's cell (Δ11 item 3): the unique cell within ``tolerance`` of the
    maximum; on a full tie the random arm's draw (:func:`random_draw`); on a
    strict-subset tie one tied cell, uniform in menu order, from
    ``policy_seed(seed, decision, TIE_INDEX, 0)``."""
    tied = tied_cells(table, tolerance)
    n_e, n_d = len(table), len(table[0])
    if len(tied) == n_e * n_d:
        return random_draw(seed, decision, n_e, n_d)
    if len(tied) == 1:
        return tied[0]
    rng = generator(policy_seed(seed, decision, TIE_INDEX, 0))
    return tied[int(rng.integers(len(tied)))]


# --------------------------------------------------------------------------
# The KL-ranked pick (a logged diagnostic; never a choice)
# --------------------------------------------------------------------------


def kl_divergence(p: Predictive, q: Predictive) -> float:
    """``KL(p ‖ q)`` for atom-plus-Gaussian predictives; ``inf`` where p puts
    mass q does not (an atom against none, or a Gaussian part against q's
    certain stop). Recorded as computed (LOG 2026-10-27 item 5)."""
    if p == q:
        return 0.0
    if (p.undefined > 0.0 and q.undefined == 0.0) or (
        p.undefined < 1.0 and q.undefined == 1.0
    ):
        return math.inf
    kl = 0.0
    if p.undefined > 0.0:
        kl += p.undefined * math.log(p.undefined / q.undefined)
    if p.undefined < 1.0:
        r = p.var / q.var
        gauss = (r - 1.0) - math.log1p(r - 1.0) + (p.mean - q.mean) ** 2 / q.var
        kl += (1.0 - p.undefined) * (
            math.log((1.0 - p.undefined) / (1.0 - q.undefined)) + 0.5 * gauss
        )
    return kl


@dataclass(frozen=True)
class KLPick:
    """The cell maximising ``Σ_{k≠top} v_k KL(p_k ‖ p_top)``, ``v_k = w_k / (1 -
    w_top)`` from log weights; the first in menu order on ties. ``value`` may
    be ``inf``; ``infinite`` counts the cells whose value is."""

    top: int
    design_index: int
    diagnostic_index: int
    value: float
    infinite: int

    def as_json(self) -> dict[str, object]:
        return {
            "top": self.top,
            "design": self.design_index,
            "diagnostic": self.diagnostic_index,
            "value": "inf" if math.isinf(self.value) else self.value,
            "infinite": self.infinite,
        }


def kl_pick(
    log_weights: Sequence[float], top: int, predictives: PredictiveTable
) -> KLPick | None:
    """The KL-ranked pick (:class:`KLPick`); None with no rival to rank."""
    rivals = [k for k in range(len(log_weights)) if k != top]
    if not rivals:
        return None
    norm = max(log_weights[k] for k in rivals)
    norm += math.log(math.fsum(math.exp(log_weights[k] - norm) for k in rivals))
    v = {k: math.exp(log_weights[k] - norm) for k in rivals}
    best: tuple[float, int, int] | None = None
    infinite = 0
    for e, row in enumerate(predictives):
        for d, ps in enumerate(row):
            kls = [kl_divergence(ps[k], ps[top]) for k in rivals]
            if any(math.isinf(x) for x in kls):
                score = math.inf
                infinite += 1
            else:
                score = math.fsum(v[k] * x for k, x in zip(rivals, kls, strict=True))
            if best is None or score > best[0]:
                best = (score, e, d)
    if best is None:
        raise PolicyError("an empty predictive table")
    return KLPick(top, best[1], best[2], best[0], infinite)


@dataclass(frozen=True)
class EIGSummary:
    """Framework-only facts of one EIG decision, for the degeneracy split.

    ``weights`` and ``log_weights`` are the entertained set's (the second
    finite where the first underflows); ``entropy`` is H(w) in nats from the
    log weights; ``max_eig`` the table's maximum; ``tied`` the number of its
    ``cells`` within ``tolerance`` of it. ``degenerate``: every cell is within
    the tolerance of 0 (LOG 2026-10-27 item 4 splits C5 on it), which makes
    the decision a ``full_tie``, so the pick is the random arm's draw. ``kl``
    is the KL-ranked pick, logged only (item 5).
    """

    weights: tuple[float, ...]
    entropy: float
    max_eig: float
    tolerance: float
    tied: int
    cells: int
    log_weights: tuple[float, ...]
    kl: KLPick | None

    @property
    def degenerate(self) -> bool:
        return self.max_eig <= self.tolerance

    @property
    def full_tie(self) -> bool:
        return self.tied == self.cells

    @property
    def tie(self) -> Literal["none", "partial", "full"]:
        if self.full_tie:
            return "full"
        return "none" if self.tied == 1 else "partial"

    def as_json(self) -> dict[str, object]:
        return {
            "weights": list(self.weights),
            "log_weights": list(self.log_weights),
            "entropy": self.entropy,
            "max_eig": self.max_eig,
            "tolerance": self.tolerance,
            "tied": self.tied,
            "cells": self.cells,
            "degenerate": self.degenerate,
            "full_tie": self.full_tie,
            "tie": self.tie,
            "kl": None if self.kl is None else self.kl.as_json(),
        }


def weight_entropy(weights: Sequence[float]) -> float:
    """``H(w) = -Σ w log w`` in nats (0 log 0 = 0)."""
    return -math.fsum(w * math.log(w) for w in weights if w > 0.0)


def log_weight_entropy(log_weights: Sequence[float]) -> float:
    """``H(w) = -Σ e^{l} l`` from log weights: the leader's ``-η`` is kept."""
    return -math.fsum(math.exp(lw) * lw for lw in log_weights)


def eig_summary(
    weights: Sequence[float],
    log_weights: Sequence[float],
    table: Table,
    tolerance: float,
    kl: KLPick | None = None,
) -> EIGSummary:
    """The :class:`EIGSummary` of an EIG table."""
    return EIGSummary(
        tuple(weights),
        log_weight_entropy(log_weights),
        max(max(row) for row in table),
        tolerance,
        len(tied_cells(table, tolerance)),
        len(table) * len(table[0]),
        tuple(log_weights),
        kl,
    )


def _config(fields: Mapping[str, object]) -> str:
    """Canonical JSON: sorted keys, no spaces."""
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def eig_table(
    context: DecisionContext,
    seed: int,
    decision: int,
    *,
    replicates: int = REPLICATES,
    max_events: int = POLICY_MAX_EVENTS,
) -> Table:
    """EIG of every (e, d): one simulation set per (e, k) for all d."""
    predictives = eig_predictives(
        context, seed, decision, replicates=replicates, max_events=max_events
    )
    return _eig_rows(context.entertained.weights, predictives)


def _eig_rows(weights: Sequence[float], predictives: PredictiveTable) -> Table:
    return tuple(tuple(eig(weights, ps) for ps in row) for row in predictives)


def eig_predictives(
    context: DecisionContext,
    seed: int,
    decision: int,
    *,
    replicates: int = REPLICATES,
    max_events: int = POLICY_MAX_EVENTS,
) -> PredictiveTable:
    """``[e][d][k]``: one simulation set per (e, k), read for every d."""
    ent = context.entertained
    diagnostics = context.menu.diagnostics
    rows: list[tuple[tuple[Predictive, ...], ...]] = []
    for design in context.menu.designs:
        per_k = [
            simulate_values(
                h.model,
                design,
                diagnostics,
                context.channels,
                seed,
                decision,
                k,
                replicates,
                max_events=max_events,
            )
            for k, h in enumerate(ent.hypotheses)
        ]
        rows.append(
            tuple(
                tuple(predictive(values[d]) for values in per_k)
                for d in range(len(diagnostics))
            )
        )
    return tuple(rows)


def reference_model(context: DecisionContext) -> WienerHopfModel:
    """FSD's model-free reference: Wiener-Hopf on the observational log."""
    return WienerHopfModel.estimate(context.observational.log, context.channels)


def _hellinger_table(
    context: DecisionContext,
    par: Simulator,
    par_k: int,
    reference: Simulator,
    seed: int,
    decision: int,
    replicates: int,
    max_events: int,
    reference_k: int = REFERENCE_INDEX,
    reference_extra: tuple[int, ...] = (),
) -> Table:
    diagnostics = context.menu.diagnostics
    rows: list[tuple[float, ...]] = []
    for design in context.menu.designs:
        a = simulate_values(
            par,
            design,
            diagnostics,
            context.channels,
            seed,
            decision,
            par_k,
            replicates,
            max_events=max_events,
        )
        b = simulate_values(
            reference,
            design,
            diagnostics,
            context.channels,
            seed,
            decision,
            reference_k,
            replicates,
            extra=reference_extra,
            max_events=max_events,
        )
        rows.append(tuple(hellinger2(a[d], b[d]) for d in range(len(diagnostics))))
    return tuple(rows)


def fsd_table(
    context: DecisionContext,
    seed: int,
    decision: int,
    *,
    replicates: int = REPLICATES,
    reference: Simulator | None = None,
    parametric: Simulator | None = None,
    max_events: int = POLICY_MAX_EVENTS,
) -> Table:
    """``He²(q_par, q_ref)`` of every (e, d) (module docstring).

    ``reference`` defaults to :func:`reference_model`; ``parametric`` to the
    top-weight entertained fit (simulated on its own index ``k``). An
    injected ``parametric`` model draws on :data:`PARAMETRIC_INDEX`.
    """
    ref = reference if reference is not None else reference_model(context)
    if parametric is None:
        k = context.entertained.top
        par = context.entertained.hypotheses[k].model
    else:
        k, par = PARAMETRIC_INDEX, parametric
    return _hellinger_table(
        context, par, k, ref, seed, decision, replicates, max_events
    )


@dataclass(frozen=True)
class FsdNull:
    """FSD tables under the null (fitted model = truth), and their mean."""

    tables: tuple[Table, ...]
    mean: Table


def fsd_null(
    context: DecisionContext,
    seed: int,
    decision: int,
    *,
    replicates: int = REPLICATES,
    bootstraps: int = 8,
    max_events: int = POLICY_MAX_EVENTS,
) -> FsdNull:
    """FSD's parametric-bootstrap null (module docstring).

    Bootstrap ``b`` simulates an observational log (no intervention, as long
    as the real one) from the top fit on
    ``policy_seed(seed, decision, NULL_INDEX, b)``, re-estimates Wiener-Hopf
    on it, and computes the FSD table against the same parametric replicates
    as :func:`fsd_table`; the reference replicates draw on
    ``policy_seed(seed, decision, NULL_INDEX, r, b)``.
    """
    if bootstraps < 1:
        raise PolicyError(f"need at least one bootstrap, got {bootstraps}")
    k = context.entertained.top
    top = context.entertained.hypotheses[k].model
    observe = Experiment(Compose(()), context.observational.log.horizon)
    tables: list[Table] = []
    for b in range(bootstraps):
        rng = generator(policy_seed(seed, decision, NULL_INDEX, b))
        try:
            log = simulate_capped(top, observe, rng, "null", max_events).log
        except STOPPED as error:
            raise PolicyError(f"null bootstrap {b} could not be simulated") from error
        boot = WienerHopfModel.estimate(log, context.channels)
        tables.append(
            _hellinger_table(
                context,
                top,
                k,
                boot,
                seed,
                decision,
                replicates,
                max_events,
                reference_k=NULL_INDEX,
                reference_extra=(b,),
            )
        )
    n_e, n_d = len(context.menu.designs), len(context.menu.diagnostics)
    mean = tuple(
        tuple(math.fsum(t[e][d] for t in tables) / bootstraps for d in range(n_d))
        for e in range(n_e)
    )
    return FsdNull(tuple(tables), mean)


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """A policy's pick at one decision; ``scores`` is its table (None: random)."""

    policy: str
    decision: int
    design_index: int
    diagnostic_index: int
    design: Design
    diagnostic: DiagnosticChoice
    scores: Table | None
    eig: EIGSummary | None = None


def _choice(
    policy: str,
    decision: int,
    menu: Menu,
    e: int,
    d: int,
    scores: Table | None,
    eig: EIGSummary | None = None,
) -> Choice:
    return Choice(
        policy, decision, e, d, menu.designs[e], menu.diagnostics[d], scores, eig
    )


class Policy(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def config(self) -> str: ...

    def choose(self, context: DecisionContext, decision: int) -> Choice: ...


@dataclass(frozen=True)
class RandomPolicy:
    """Uniform over (e, d), with replacement, seeded (module docstring)."""

    seed: int
    name: str = "random"

    @property
    def config(self) -> str:
        return _config({"policy": self.name})

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        menu = context.menu
        e, d = random_draw(
            self.seed, decision, len(menu.designs), len(menu.diagnostics)
        )
        return _choice(self.name, decision, menu, e, d, None)


@dataclass(frozen=True)
class EIGPolicy:
    """The argmax of :func:`eig_table` under its weighting, ties within the floor.

    The entertained set is reweighted by ``weighting`` before the table is
    computed; ties within ``tolerance`` of the maximum are broken by
    :func:`eig_choice` (a full tie is the random arm's draw for the decision,
    a strict-subset tie a draw on :data:`TIE_INDEX`), and the choice carries an
    :class:`EIGSummary` with the KL-ranked pick logged beside it. ``seed`` must
    be the truth-keyed policy seed the random arm of the same (truth, seed)
    receives, so that a full tie is that arm's pick.
    """

    seed: int
    replicates: int = REPLICATES
    max_events: int = POLICY_MAX_EVENTS
    weighting: Weighting = BIC_WEIGHTS
    tolerance: float = EIG_TIE_TOL
    name: str = "EIG"

    @property
    def config(self) -> str:
        return _config(
            {
                "policy": self.name,
                "replicates": self.replicates,
                "max_events": self.max_events,
                "weights": self.weighting.config,
                "tie_tolerance": self.tolerance,
                "ties": {
                    "full": {"draw": "random arm", "index": RANDOM_INDEX},
                    "partial": {
                        "draw": "uniform over tied cells, menu order",
                        "index": TIE_INDEX,
                    },
                },
                "quadrature": EIG_QUADRATURE,
                "precision": (
                    "relative: log weights; log1p where p_w/p_k is near 1; "
                    "panels of every component and at each crossing"
                ),
                "diagnostic": "kl_pick: sum_k v_k KL(p_k||p_top), logged only",
            }
        )

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        entertained = context.entertained.reweighted(self.weighting)
        weighted = dataclasses.replace(context, entertained=entertained)
        log_weights = self.weighting.log_weights(
            [h.bic for h in entertained.hypotheses]
        )
        predictives = eig_predictives(
            weighted,
            self.seed,
            decision,
            replicates=self.replicates,
            max_events=self.max_events,
        )
        table = _eig_rows(entertained.weights, predictives)
        e, d = eig_choice(table, self.tolerance, self.seed, decision)
        kl = kl_pick(log_weights, entertained.top, predictives)
        summary = eig_summary(
            entertained.weights, log_weights, table, self.tolerance, kl
        )
        return _choice(self.name, decision, context.menu, e, d, table, summary)


@dataclass(frozen=True)
class FSDPolicy:
    """The argmax of :func:`fsd_table`; ``reference`` defaults to Wiener-Hopf."""

    seed: int
    replicates: int = REPLICATES
    reference: Simulator | None = None
    max_events: int = POLICY_MAX_EVENTS
    name: str = "FSD"

    @property
    def config(self) -> str:
        return _config(
            {
                "policy": self.name,
                "replicates": self.replicates,
                "max_events": self.max_events,
                "reference": "wiener_hopf" if self.reference is None else "injected",
            }
        )

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        table = fsd_table(
            context,
            self.seed,
            decision,
            replicates=self.replicates,
            reference=self.reference,
            max_events=self.max_events,
        )
        e, d = choose(table)
        return _choice(self.name, decision, context.menu, e, d, table)


class ExperimentRunner(Protocol):
    """Runs a chosen experiment in the world; None when the run was stopped."""

    def run(self, experiment: Experiment, label: str) -> Dataset | None: ...


def decide(
    policy: Policy, context: DecisionContext, decision: int, runner: ExperimentRunner
) -> tuple[Choice, Dataset | None]:
    """Choose, then run the chosen design through the injected runner."""
    choice = policy.choose(context, decision)
    return choice, runner.run(choice.design.experiment, choice.design.name)
