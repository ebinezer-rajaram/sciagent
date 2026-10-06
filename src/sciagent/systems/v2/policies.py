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
:data:`PARAMETRIC_INDEX`, :data:`RANDOM_INDEX` and :data:`NULL_INDEX` are
reserved for the reference model, an injected parametric model, the random
draw and the FSD null.

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
(:data:`GL_NODES` nodes per panel) on ``y = μ_k + s_k x``, ``x ∈ [-12, 12]``
(the standard normal's mass outside is 3.6e-33). Panels break every
:data:`PANEL_STEP` standard deviations (sd) of ``p_k`` itself and, for every
narrower component ``j``, every :data:`PANEL_STEP` standard deviations of
``p_j`` within ``±12 s_j`` of ``μ_j``. A narrower component puts a spike of
width ``s_j`` into ``log p_w``; Gauss-Hermite on ``p_k`` steps over it (WP5's
256-node rule gave 0.5009 against 0.6408 at variance ratio 1e4, shift 10 sd),
and these panels resolve it. The rule is deterministic. A component identical
to ``p_k`` contributes ``c_j = 0`` exactly, so identical predictives give
EIG = 0 exactly. Each KL is clamped at 0 from below.

*Tie floor* (:data:`EIG_TIE_TOL`). Against an independent dense-grid reference
(trapezoid, 2·10⁶ points per component), the rule's largest absolute error over
306 two- and three-component cases (variance ratios 10⁻⁴ to 10⁴, shifts 0 to 10
sd, atoms 0 to 0.4, w₁ 0.5 to 0.999, both orders) is
:data:`EIG_QUADRATURE_ERROR` = 2.2e-16, one ulp at 1: the rule is exact to
rounding there. (WP5's Gauss-Hermite rule erred by up to 0.14 on the same
grid.) The tolerance takes a factor of 100 over that, for regimes the grid does
not cover and for the rounding of the sums at EIG's scale (up to log K),
rounded up to a power of ten: ``EIG_TIE_TOL`` = 1e-13 nats. EIG values closer
than that to the table's maximum cannot be ordered by the computation, so they
are exact ties and go to menu order (:func:`choose`). When the BIC weights are
so concentrated that ε·H(w), which bounds every EIG (the evidence wall itself),
is below the floor, every cell ties and the pick is the menu-first cell, not
quadrature noise; dev-008 at n ≈ 2000 has H(w) ≈ 1e-111. A posterior only
nearly as concentrated (dev-013: H(w) ≈ 3e-13) can leave cells above the floor,
and its pick then follows EIG values that are computed correctly but are
negligible: that is a statement about the weights, not about the numerics, and
:class:`Weighting` is the lever for it. The decision reports ``H(w)`` and the
maximum EIG (:class:`EIGSummary`) so the analysis can count degenerate
decisions.

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

**Choice** (:func:`choose`). The first cell in menu order (designs first, then
diagnostics) within ``tolerance`` of the table's maximum: 0 for FSD and the
truth-aware control, :data:`EIG_TIE_TOL` for EIG. FSD needs no floor: He² is
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


def _nodes(
    k: int, ps: Sequence[Predictive], live: Sequence[bool]
) -> tuple[np.ndarray, np.ndarray]:
    """Composite Gauss-Legendre nodes ``x`` and weights for E_{N(0,1)}.

    In ``p_k``'s standard units: its own panels every :data:`PANEL_STEP`, and
    each narrower live component's panels every :data:`PANEL_STEP` of its
    standard deviations (module docstring).
    """
    pk = ps[k]
    sk = math.sqrt(pk.var)
    breaks = [_STEPS]
    for j, p in enumerate(ps):
        if j == k or not live[j] or p.var >= pk.var:
            continue
        own = (p.mean - pk.mean + math.sqrt(p.var) * _STEPS) / sk
        breaks.append(np.clip(own, -_RANGE, _RANGE))
    edges = np.unique(np.concatenate(breaks))
    lo, hi = edges[:-1], edges[1:]
    keep = hi > lo
    lo, hi = lo[keep], hi[keep]
    half = 0.5 * (hi - lo)
    x = ((lo + hi) * 0.5)[:, None] + half[:, None] * _GL_NODES[None, :]
    w = half[:, None] * _GL_WEIGHTS[None, :]
    x, w = x.reshape(-1), w.reshape(-1)
    return x, w * np.exp(-0.5 * x * x - _LOG_SQRT_2PI)


def _kl_to_mixture(k: int, weights: Sequence[float], ps: Sequence[Predictive]) -> float:
    """``KL(p_k ‖ p_w)`` (module docstring)."""
    pk = ps[k]
    kl = 0.0
    if pk.undefined > 0.0:
        ratio = math.fsum(
            w * (p.undefined / pk.undefined) for w, p in zip(weights, ps, strict=True)
        )
        kl -= pk.undefined * math.log(ratio)
    if pk.undefined < 1.0:
        live = [w > 0.0 and p.undefined < 1.0 for w, p in zip(weights, ps, strict=True)]
        x, qw = _nodes(k, ps, live)
        y = pk.mean + math.sqrt(pk.var) * x
        columns: list[np.ndarray] = []
        mix: list[float] = []
        for w, p, alive in zip(weights, ps, live, strict=True):
            if not alive:
                continue
            if p == pk:
                c = np.zeros_like(x)
            else:
                shift = math.log((1.0 - p.undefined) / (1.0 - pk.undefined))
                shift -= 0.5 * math.log(p.var / pk.var)
                c = shift - (y - p.mean) ** 2 / (2.0 * p.var) + 0.5 * x * x
            columns.append(c)
            mix.append(w)
        logs = np.stack(columns, axis=1)
        top = np.maximum(logs.max(axis=1), 0.0)
        scaled = (
            np.exp(logs - top[:, None]) * np.asarray(mix, dtype=np.float64)[None, :]
        )
        log_mix = top + np.log(reductions.row_totals(scaled))
        kl += (1.0 - pk.undefined) * reductions.dot(qw, -log_mix)
    return max(kl, 0.0)


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


def choose(table: Table, *, tolerance: float = 0.0) -> tuple[int, int]:
    """The first ``(e, d)`` in menu order within ``tolerance`` of the maximum."""
    if not table or not table[0]:
        raise PolicyError("an empty score table")
    if not (math.isfinite(tolerance) and tolerance >= 0.0):
        raise PolicyError(f"tolerance must be finite and >= 0, got {tolerance}")
    for e, row in enumerate(table):
        for d, x in enumerate(row):
            if not math.isfinite(x):
                raise PolicyError(f"score ({e}, {d}) is not finite: {x}")
    best = max(max(row) for row in table)
    for e, row in enumerate(table):
        for d, x in enumerate(row):
            if best - x <= tolerance:
                return e, d
    raise PolicyError("no cell attains the maximum")


@dataclass(frozen=True)
class EIGSummary:
    """Framework-only facts of one EIG decision, for the degeneracy count.

    ``entropy`` is H(w) in nats; ``max_eig`` the table's maximum; ``tied`` the
    number of cells within ``tolerance`` of it. ``degenerate``: every cell is
    within the tolerance of 0, so the pick is the menu-first cell.
    """

    weights: tuple[float, ...]
    entropy: float
    max_eig: float
    tolerance: float
    tied: int

    @property
    def degenerate(self) -> bool:
        return self.max_eig <= self.tolerance


def weight_entropy(weights: Sequence[float]) -> float:
    """``H(w) = -Σ w log w`` in nats (0 log 0 = 0)."""
    return -math.fsum(w * math.log(w) for w in weights if w > 0.0)


def eig_summary(weights: Sequence[float], table: Table, tolerance: float) -> EIGSummary:
    """The :class:`EIGSummary` of an EIG table."""
    best = max(max(row) for row in table)
    tied = sum(1 for row in table for x in row if best - x <= tolerance)
    return EIGSummary(tuple(weights), weight_entropy(weights), best, tolerance, tied)


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
    ent = context.entertained
    diagnostics = context.menu.diagnostics
    rows: list[tuple[float, ...]] = []
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
                eig(ent.weights, [predictive(values[d]) for values in per_k])
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
        n_d = len(menu.diagnostics)
        rng = generator(policy_seed(self.seed, decision, RANDOM_INDEX, 0))
        e, d = divmod(int(rng.integers(len(menu.designs) * n_d)), n_d)
        return _choice(self.name, decision, menu, e, d, None)


@dataclass(frozen=True)
class EIGPolicy:
    """The argmax of :func:`eig_table` under its weighting, ties within the floor.

    The entertained set is reweighted by ``weighting`` before the table is
    computed; ties within ``tolerance`` of the maximum go to menu order, and
    the choice carries an :class:`EIGSummary`.
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
                "quadrature": EIG_QUADRATURE,
            }
        )

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        entertained = context.entertained.reweighted(self.weighting)
        weighted = dataclasses.replace(context, entertained=entertained)
        table = eig_table(
            weighted,
            self.seed,
            decision,
            replicates=self.replicates,
            max_events=self.max_events,
        )
        e, d = choose(table, tolerance=self.tolerance)
        summary = eig_summary(entertained.weights, table, self.tolerance)
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
