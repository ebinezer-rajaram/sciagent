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

``U = Σ_j w_j u_j`` and ``c_j = log((1-u_j) N_j(y) / ((1-u_k) N_k(y)))``. The
expectation is taken by Gauss-Hermite quadrature with :data:`GH_NODES` nodes,
deterministic. A component identical to ``p_k`` contributes ``c_j = 0``
exactly, so identical predictives give EIG = 0 exactly. Each KL is clamped
at 0 from below (quadrature can only err by rounding there).

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

**Choice** (:func:`choose`). The argmax over the table, ties to menu order
(designs first, then diagnostics).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np
from numpy.polynomial.hermite import hermgauss

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
#: Gauss-Hermite nodes of the EIG quadrature.
GH_NODES: Final = 256
#: Reserved model indices ``k`` of :func:`~sciagent.scoring.seeds.policy_seed`.
REFERENCE_INDEX: Final = 2**32 - 1
RANDOM_INDEX: Final = 2**32 - 2
NULL_INDEX: Final = 2**32 - 3
PARAMETRIC_INDEX: Final = 2**32 - 4
#: Relative variance floor of a predictive (as ``battery.hellinger2``).
_FLOOR: Final = 1e-9

_X, _H = hermgauss(GH_NODES)
#: Nodes and weights for E_{N(0,1)}: y = μ + sqrt(2 v) x, weight h / sqrt(π).
_NODES: Final = np.asarray(_X, dtype=np.float64)
_WEIGHTS: Final = np.asarray(_H, dtype=np.float64) / math.sqrt(math.pi)

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


def bic_weights(bics: Sequence[float]) -> tuple[float, ...]:
    """``w_k ∝ exp(-BIC_k / 2)``, shifted by the smallest BIC."""
    if not bics:
        raise PolicyError("no BICs to weigh")
    if not all(math.isfinite(b) for b in bics):
        raise PolicyError(f"BICs must be finite, got {tuple(bics)}")
    low = min(bics)
    raw = [math.exp(-(b - low) / 2.0) for b in bics]
    total = math.fsum(raw)
    return tuple(x / total for x in raw)


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
        hypotheses: Sequence[Hypothesis], skipped: Sequence[str] = ()
    ) -> EntertainedSet:
        if not hypotheses:
            raise PolicyError("the entertained set is empty")
        return EntertainedSet(
            tuple(hypotheses),
            bic_weights([h.bic for h in hypotheses]),
            tuple(skipped),
        )

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
    return EntertainedSet.of(kept, skipped)


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
        x = _NODES
        y = pk.mean + math.sqrt(2.0 * pk.var) * x
        columns: list[np.ndarray] = []
        mix: list[float] = []
        for w, p in zip(weights, ps, strict=True):
            if w == 0.0 or p.undefined >= 1.0:
                continue
            if p == pk:
                c = np.zeros_like(x)
            else:
                shift = math.log((1.0 - p.undefined) / (1.0 - pk.undefined))
                shift -= 0.5 * math.log(p.var / pk.var)
                c = shift - (y - p.mean) ** 2 / (2.0 * p.var) + x**2
            columns.append(c)
            mix.append(w)
        logs = np.stack(columns, axis=1)
        top = np.maximum(logs.max(axis=1), 0.0)
        scaled = (
            np.exp(logs - top[:, None]) * np.asarray(mix, dtype=np.float64)[None, :]
        )
        log_mix = top + np.log(reductions.row_totals(scaled))
        kl += (1.0 - pk.undefined) * reductions.dot(_WEIGHTS, -log_mix)
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


def choose(table: Table) -> tuple[int, int]:
    """``(e, d)`` of the largest entry; ties to menu order (designs first)."""
    if not table or not table[0]:
        raise PolicyError("an empty score table")
    best: tuple[int, int] | None = None
    value = -math.inf
    for e, row in enumerate(table):
        for d, x in enumerate(row):
            if not math.isfinite(x):
                raise PolicyError(f"score ({e}, {d}) is not finite: {x}")
            if best is None or x > value:
                best, value = (e, d), x
    assert best is not None
    return best


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


def _choice(
    policy: str, decision: int, menu: Menu, e: int, d: int, scores: Table | None
) -> Choice:
    return Choice(policy, decision, e, d, menu.designs[e], menu.diagnostics[d], scores)


class Policy(Protocol):
    @property
    def name(self) -> str: ...

    def choose(self, context: DecisionContext, decision: int) -> Choice: ...


@dataclass(frozen=True)
class RandomPolicy:
    """Uniform over (e, d), with replacement, seeded (module docstring)."""

    seed: int
    name: str = "random"

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        menu = context.menu
        n_d = len(menu.diagnostics)
        rng = generator(policy_seed(self.seed, decision, RANDOM_INDEX, 0))
        e, d = divmod(int(rng.integers(len(menu.designs) * n_d)), n_d)
        return _choice(self.name, decision, menu, e, d, None)


@dataclass(frozen=True)
class EIGPolicy:
    """The argmax of :func:`eig_table`."""

    seed: int
    replicates: int = REPLICATES
    max_events: int = POLICY_MAX_EVENTS
    name: str = "EIG"

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        table = eig_table(
            context,
            self.seed,
            decision,
            replicates=self.replicates,
            max_events=self.max_events,
        )
        e, d = choose(table)
        return _choice(self.name, decision, context.menu, e, d, table)


@dataclass(frozen=True)
class FSDPolicy:
    """The argmax of :func:`fsd_table`; ``reference`` defaults to Wiener-Hopf."""

    seed: int
    replicates: int = REPLICATES
    reference: Simulator | None = None
    max_events: int = POLICY_MAX_EVENTS
    name: str = "FSD"

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
