"""B-sparse (SPEC §4.1): group lasso over every feature of depth ≤ 2 at every ψ.

The strongest non-search rival: one convex fit over a fixed dictionary, with
the regularisation chosen by held-out likelihood. It should succeed wherever
the truth is in the dictionary, and SPEC §7.1's "too easy" criterion reads it
on the out-of-dictionary truths.

**Dictionary** (``sparse_dictionary.py``). One group per canonical feature of
depth ≤ 2 per ψ grid point: atoms, products of two atoms, gated atoms. For
pointproc that is ~1.0M groups over ~1.4k unit columns. The design is never
materialised: the gradient of every group comes from one cross product of the
cached unit columns.

**Link: softplus.** The path is fitted under the softplus link
``λ = log(1 + e^η)``. Its negative log-likelihood is convex and
unconstrained (λ > 0 for every θ), like the exp link's. Unlike the exp link it
grows *linearly* in an excitation feature, as the identity link (Hawkes) does.
The exp link was tried first and failed on the S11 truth (2,133 events):
under ``λ = exp(θ·Σ k(t - tⱼ) f(mⱼ))`` a heavy-tailed mark function
(``ExpOf``) makes the relaxed refit explosive. One large mark in the
validation window drove η to ~190 and the validation log-likelihood to about
-10⁸³, and the path's selection was noise. The identity link would need
``λ ≥ 0`` at ~7·10⁴ nodes for every θ on the path, a constrained problem
whose feasible set changes with the active groups. The *submitted* link is
then chosen the way an agent can choose it: the selected feature set is
fitted by the certified fitter under each link in ``SparseConfig.links`` on
the training window, and the link with the best validation log-likelihood
wins.

**Data split.** Each observational dataset is split in time: the first
``train_fraction`` (75%) of its horizon trains, the rest validates
(:func:`split_dataset`). A point process cannot be split by random events:
the likelihood of every event depends on the whole past. A contiguous split
is the honest one-step-ahead test. The validation window still sees the full
history from time 0 as features (an excluded window, ``data.py``), so there
is no edge effect at the split. 75/25 keeps ~1,500 events to fit and ~500 to
validate at the default 2,000-event log: enough validation events that a
spurious group costs more held-out likelihood than it gains, without halving
the fitting data. The final model is refitted on all of the data.

**Path.** λ runs from ``λ_max`` (the smallest λ at which every group is zero,
``max_g ‖∇_g‖/ω_g`` at the intercept-only fit) down to ``lambda_min_ratio ·
λ_max``, ``n_lambdas`` log-spaced values, each warm-started from the last.
The penalty weight of a group is ``ω_g = √(columns)``, and columns are
standardised (``sparse_dictionary.py``). At each λ:

1. *Sequential strong rule* (Tibshirani et al. 2012): group g is a
   candidate unless ``‖∇_g(β̂(λ_prev))‖ < ω_g(2λ - λ_prev)``. The working set
   is the previous non-zero groups plus the strongest candidates (by
   ``‖∇_g‖/ω_g``) up to ``working_set_columns`` columns.
2. Solve on the working set (``sparse_solver.py``, proximal Newton).
3. *KKT check over every group of the dictionary*: one cross product gives
   ``‖∇_g‖`` for all ~10⁶ groups. Every group outside the working set with
   ``‖∇_g‖ > (1 + kkt_tol) λ ω_g`` is a violator, and the strongest violators
   join the working set, back to step 2. Without violators the point is exact
   (to ``kkt_tol``) for the *whole* dictionary, not just the working set.

The strong rule only chooses where to look. Correctness comes from step 3,
so a strong-rule failure costs one more round, never a wrong answer. A
violator replaces the zero groups of the previous round in the working set,
so a full working set cannot lock violators out. The path stops early
(:attr:`SparsePath.stop`) once more than ``max_active_columns`` (64) columns
are non-zero, as glmnet's ``pmax`` does. That is sixteen times the 4 features
a structure may have. It also stops when the solver cannot reach
``solver_tol`` at the next λ. That happens only deep in the path, with
dozens of near-collinear groups active (60 or more at 2,000 events), and
that point is dropped, so every recorded point is KKT-exact.

**Selection by held-out likelihood.** Each path point proposes a candidate
structure. Its non-zero groups are mapped to canonical features, ψ dropped,
and duplicates merged. A feature's score is the sum of its groups'
standardised coefficient norms (one feature at two adjacent ψ points is one
mechanism with an off-grid ψ). The top ``MAX_FEATURES`` (4) by score are kept,
each at the ψ of its largest group. The candidate is refitted unpenalised on
the training window under the softplus link at those ψ (the relaxed lasso,
Meinshausen 2007), certified by :func:`fit_solve.solve`, and scored by its
validation log-likelihood. The point with the best score wins, ties going to
the larger λ. Two reasons to score the relaxed refit rather than the shrunken
penalised fit: shrinkage biases held-out selection towards small λ and many
groups, and what is scored is then exactly what will be submitted.

**The cap is a real limitation.** The grammar allows at most 4 features, and
the lasso may select more. When it does at the chosen λ, the top 4 are
submitted and :attr:`SparseReport.capped` says so; the report keeps the
uncapped count.

**Submission.** The selected feature set, with the chosen link, is fitted by
the certified fitter (:func:`~sciagent.glm.fit.fit`) on all observational
data, ψ profiled afresh, so it is scored exactly like any other proposal.
The empty selection is the null, submitted under the identity link, which is
how the library writes it. For the efficiency curve B-sparse counts as **one
fit** (``fits_used = 1``), as SPEC §4.1 calls it. Its internal fits and its
wall time are reported in :class:`SparseReport`.

**Determinism.** Groups are enumerated in a fixed order. Every sort breaks
ties by group id, and every sum that feeds a reported or compared number
(event sums, validation log-likelihoods, the feature scores) is exactly
rounded (``sciagent.core.reductions``, ``math.fsum``). The cross products and
the solver use BLAS: deterministic run to run on one machine (tested), and
they only steer the path. Every reported fit is the certified fitter's.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

import numpy as np
import numpy.typing as npt
from scipy import special

from sciagent.core import reductions
from sciagent.glm.canonical import canonicalise, sort_key
from sciagent.glm.data import Dataset, Floats
from sciagent.glm.features import DEFAULT_QUADRATURE, QuadratureSpec
from sciagent.glm.fit import FitConfig, FitError, evaluate_log_likelihood, fit
from sciagent.glm.fit_solve import Problem, solve
from sciagent.glm.grammar import MAX_FEATURES, ChannelSpec, Feature, Link, Structure
from sciagent.glm.space import Alphabet
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.sparse_dictionary import GroupDictionary, SparseError
from sciagent.systems.v2.sparse_solver import (
    WorkingProblem,
    event_rate_ratio,
    log_softplus,
    softplus,
    solve_group_lasso,
)
from sciagent.systems.v2.systems import (
    InvestigationData,
    SystemResult,
    TrajectoryPoint,
)

B_SPARSE: Final = "B-sparse"
#: Share of each observational dataset's horizon used for fitting the path.
TRAIN_FRACTION: Final = 0.75


@dataclass(frozen=True)
class SparseConfig:
    """How B-sparse runs. ``workers`` changes wall time only."""

    train_fraction: float = TRAIN_FRACTION
    n_lambdas: int = 25
    lambda_min_ratio: float = 0.01
    max_active_columns: int = 64
    working_set_columns: int = 200
    kkt_tol: float = 1e-6
    solver_tol: float = 1e-7
    max_kkt_rounds: int = 50
    links: tuple[Link, ...] = (Link.EXP, Link.IDENTITY, Link.SOFTPLUS)
    alphabet: Alphabet | None = None
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE
    fit_config: FitConfig | None = None
    workers: int = 1

    def __post_init__(self) -> None:
        if not 0.0 < self.train_fraction < 1.0:
            raise SparseError(
                f"train_fraction must be in (0, 1): {self.train_fraction}"
            )
        if self.n_lambdas < 2:
            raise SparseError(f"n_lambdas must be ≥ 2: {self.n_lambdas}")
        if not 0.0 < self.lambda_min_ratio < 1.0:
            raise SparseError("lambda_min_ratio must be in (0, 1)")
        if self.max_active_columns < 1 or self.working_set_columns < 1:
            raise SparseError("column budgets must be ≥ 1")
        if not (self.kkt_tol > 0.0 and self.solver_tol > 0.0):
            raise SparseError("tolerances must be > 0")
        if self.solver_tol > self.kkt_tol:
            raise SparseError("solver_tol must not exceed kkt_tol")
        if self.max_kkt_rounds < 1 or self.workers < 1:
            raise SparseError("max_kkt_rounds and workers must be ≥ 1")
        if not self.links or len(set(self.links)) != len(self.links):
            raise SparseError("links must be non-empty and distinct")


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PathPoint:
    """The penalised solution at one λ (coefficients on standardised columns).

    ``max_kkt_ratio`` is ``max ‖∇_g‖ / (λω_g)`` over every zero group of the
    dictionary (≤ 1 + kkt_tol at an exact point); ``stationarity`` is the
    solver's largest relative KKT residual on the working set.
    """

    lam: float
    intercept: float
    groups: tuple[int, ...]
    beta: tuple[tuple[float, ...], ...]
    norms: tuple[float, ...]
    n_columns: int
    working_set: int
    kkt_rounds: int
    max_kkt_ratio: float
    stationarity: float
    solver_iterations: int
    converged: bool


#: Why the path ended: it reached ``lambda_min_ratio · λ_max``; more than
#: ``max_active_columns`` columns became non-zero; or the solver did not reach
#: ``solver_tol`` at the next λ (the unconverged point is not recorded, so
#: every recorded point is KKT-exact).
type StopReason = Literal["complete", "max_active_columns", "solver"]


@dataclass(frozen=True)
class SparsePath:
    lam_max: float
    points: tuple[PathPoint, ...]
    stop: StopReason
    n_cross_products: int


@dataclass(frozen=True)
class Candidate:
    """The structure one path point proposes and its validation score."""

    features: tuple[Feature, ...]
    groups: tuple[int, ...]
    n_features_before_cap: int
    validation_log_likelihood: float
    certified: bool


@dataclass(frozen=True)
class LinkScore:
    link: Link
    validation_log_likelihood: float
    certified: bool


@dataclass(frozen=True)
class SparseReport:
    """Everything B-sparse did. Equality ignores the wall times."""

    n_units: int
    n_duplicate_units: int
    n_indicators: int
    n_groups: int
    n_columns: int
    n_train_events: int
    n_train_nodes: int
    lam_max: float
    path: tuple[PathPoint, ...]
    candidates: tuple[Candidate, ...]
    selected_index: int
    capped: bool
    stop: StopReason
    link_scores: tuple[LinkScore, ...]
    link: Link
    n_cross_products: int
    n_internal_fits: int
    final_certified: bool
    wall_time_dictionary: float = field(compare=False)
    wall_time_path: float = field(compare=False)
    wall_time_selection: float = field(compare=False)
    wall_time_refit: float = field(compare=False)
    wall_time_total: float = field(compare=False)


# --------------------------------------------------------------------------
# Split
# --------------------------------------------------------------------------


def _merge(
    windows: tuple[tuple[float, float], ...], extra: tuple[float, float]
) -> tuple[tuple[float, float], ...]:
    """Sorted union of closed intervals (overlapping or touching ones merged)."""
    out: list[list[float]] = []
    for a, b in sorted([*windows, extra]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return tuple((a, b) for a, b in out)


def split_dataset(data: Dataset, fraction: float) -> tuple[Dataset, Dataset]:
    """``(train, validation)``: the same log, with ``[s, T]`` resp. ``[0, s]``
    excluded, ``s = fraction · T``. Every event stays history for both."""
    horizon = data.log.horizon
    s = fraction * horizon
    train = Dataset.create(
        data.log,
        data.endogenous,
        _merge(data.excluded, (s, horizon)),
        f"{data.label}/train",
    )
    val = Dataset.create(
        data.log,
        data.endogenous,
        _merge(data.excluded, (0.0, s)),
        f"{data.label}/validation",
    )
    return train, val


# --------------------------------------------------------------------------
# The path
# --------------------------------------------------------------------------


def _fill(
    keep: list[int],
    candidates: npt.NDArray[np.bool_],
    score: Floats,
    widths: Callable[[int], int],
    budget: int,
) -> list[int]:
    """``keep`` plus the strongest ``candidates`` (by ``score``, ties by id)
    while they fit in ``budget`` columns; at least one is added when any is
    offered. Sorted by id."""
    inside = set(keep)
    ids = np.array(
        [g for g in np.flatnonzero(candidates).tolist() if g not in inside],
        dtype=np.intp,
    )
    out = list(keep)
    if ids.size:
        order = ids[np.lexsort((ids, -score[ids]))]
        used = sum(widths(g) for g in keep)
        for g in order.tolist():
            w = widths(g)
            if len(out) > len(keep) and used + w > budget:
                break
            out.append(g)
            used += w
    return sorted(out)


def _working(d: GroupDictionary, ws: list[int]) -> WorkingProblem:
    nodes: list[Floats] = []
    events: list[Floats] = []
    bounds = [0]
    for g in ws:
        ev, nd = d.train_columns(g, scaled=True)
        nodes.append(nd)
        events.append(ev)
        bounds.append(bounds[-1] + nd.shape[1])
    return WorkingProblem(
        nodes=np.hstack(nodes) if nodes else np.empty((d.node_weights.size, 0)),
        weights=d.node_weights,
        events=np.hstack(events) if events else np.empty((d.n_events, 0)),
        bounds=np.array(bounds, dtype=np.intp),
        omega=np.array([d.omega[g] for g in ws]),
    )


def _norms(d: GroupDictionary, eta_nodes: Floats, eta_events: Floats) -> Floats:
    """Every group's gradient norm at η (softplus link)."""
    node_d = d.node_weights * special.expit(eta_nodes)
    return d.group_norms(node_d, event_rate_ratio(eta_events))


def lasso_path(dictionary: GroupDictionary, config: SparseConfig) -> SparsePath:
    """The regularisation path, KKT-exact over the whole dictionary at each λ."""
    d = dictionary
    valid = d.valid
    omega = d.omega
    n = float(d.n_events)
    # The null fit: softplus(θ₀) = n / T.
    intercept = math.log(math.expm1(n / d.observed_time))
    norms = _norms(
        d, np.full(d.node_weights.size, intercept), np.full(d.n_events, intercept)
    )
    grams = 1
    ratio = np.where(valid, norms / omega, 0.0)
    lam_max = float(np.max(ratio)) if ratio.size else 0.0
    null = PathPoint(lam_max, intercept, (), (), (), 0, 0, 0, 1.0, 0.0, 0, True)
    if lam_max <= 0.0:
        return SparsePath(lam_max, (null,), "complete", grams)
    steps = np.arange(config.n_lambdas, dtype=np.float64) / (config.n_lambdas - 1)
    lams = lam_max * config.lambda_min_ratio**steps
    points = [null]
    beta: dict[int, Floats] = {}
    prev = lam_max
    stop: StopReason = "complete"
    budget = config.working_set_columns
    for lam in (float(x) for x in lams[1:]):
        active = sorted(beta)
        strong = valid & (norms >= omega * (2.0 * lam - prev))
        ws = _fill(active, strong, norms / omega, d.group_width, budget)
        rounds = 0
        while True:
            problem = _working(d, ws)
            start = (
                np.concatenate([beta.get(g, np.zeros(d.group_width(g))) for g in ws])
                if ws
                else np.empty(0)
            )
            sol = solve_group_lasso(
                problem, lam, intercept, start, tol=config.solver_tol
            )
            norms = _norms(d, sol.eta, sol.eta_events)
            grams += 1
            intercept = sol.intercept
            b = problem.bounds
            beta = {
                g: sol.beta[b[k] : b[k + 1]].copy()
                for k, g in enumerate(ws)
                if np.any(sol.beta[b[k] : b[k + 1]] != 0.0)
            }
            in_ws = np.zeros(valid.size, dtype=np.bool_)
            in_ws[ws] = True
            violators = valid & ~in_ws & (norms > lam * omega * (1.0 + config.kkt_tol))
            if not violators.any():
                break
            rounds += 1
            if rounds > config.max_kkt_rounds:
                raise SparseError(f"KKT check did not settle at λ = {lam}")
            # Violators take the place of the zero groups of this round.
            ws = _fill(sorted(beta), violators, norms / omega, d.group_width, budget)
        if not sol.converged:
            # Not KKT-exact: the path ends at the previous λ.
            stop = "solver"
            break
        active = sorted(beta)
        zero = valid.copy()
        zero[active] = False
        max_ratio = (
            float(np.max(norms[zero] / (lam * omega[zero]))) if zero.any() else 0.0
        )
        n_cols = sum(d.group_width(g) for g in active)
        points.append(
            PathPoint(
                lam=lam,
                intercept=intercept,
                groups=tuple(active),
                beta=tuple(tuple(float(x) for x in beta[g]) for g in active),
                norms=tuple(float(np.sqrt(beta[g] @ beta[g])) for g in active),
                n_columns=n_cols,
                working_set=len(ws),
                kkt_rounds=rounds,
                max_kkt_ratio=max_ratio,
                stationarity=sol.stationarity,
                solver_iterations=sol.iterations,
                converged=sol.converged,
            )
        )
        prev = lam
        if n_cols > config.max_active_columns:
            stop = "max_active_columns"
            break
    return SparsePath(lam_max, tuple(points), stop, grams)


# --------------------------------------------------------------------------
# Candidates and selection
# --------------------------------------------------------------------------


def rank_features(
    groups: Sequence[int],
    norms: Sequence[float],
    feature_of: Callable[[int], Feature],
) -> tuple[list[tuple[Feature, int]], int]:
    """The top ``MAX_FEATURES`` canonical features of a set of non-zero groups.

    A feature's score is the exactly rounded sum of its groups' norms; ties
    go to the earlier feature in ``sort_key`` order. Its representative group
    (whose ψ the relaxed refit uses) is its largest-norm group, ties to the
    lower id. Returns the ranked ``(feature, group)`` pairs and the number of
    distinct features before the cap.
    """
    by_feature: dict[Feature, list[tuple[float, int]]] = {}
    for g, v in zip(groups, norms, strict=True):
        by_feature.setdefault(feature_of(g), []).append((v, g))
    scored = sorted(
        by_feature.items(),
        key=lambda kv: (-math.fsum(v for v, _ in kv[1]), sort_key(kv[0])),
    )
    top: list[tuple[Feature, int]] = []
    for feature, members in scored[:MAX_FEATURES]:
        rep = min(members, key=lambda vg: (-vg[0], vg[1]))[1]
        top.append((feature, rep))
    return top, len(scored)


def _relaxed(
    d: GroupDictionary, groups: tuple[int, ...], fit_config: FitConfig
) -> tuple[float, bool]:
    """Validation log L of the unpenalised softplus refit at the groups' ψ."""
    w = d.node_weights
    ev_cols: list[Floats] = [np.ones((d.n_events, 1))]
    nd_cols: list[Floats] = [np.ones((w.size, 1))]
    for g in groups:
        ev, nd = d.train_columns(g, scaled=False)
        ev_cols.append(ev)
        nd_cols.append(nd)
    x_ev = np.hstack(ev_cols)
    x_nd = np.hstack(nd_cols)
    integrals = np.array(
        [reductions.total(w * x_nd[:, c]) for c in range(x_nd.shape[1])]
    )
    problem = Problem(
        link=Link.SOFTPLUS,
        nodes_t=np.ascontiguousarray(x_nd.T),
        weights=w,
        events_t=np.ascontiguousarray(x_ev.T),
        integrals=integrals,
    )
    sol = solve(
        problem,
        solver="newton",
        max_iter=fit_config.max_iter,
        gap_tol_rel=fit_config.gap_tol_rel,
    )
    if not sol.certified:
        return -math.inf, False
    theta = np.array(sol.theta)
    vw = d.validation_weights
    v_ev: list[Floats] = [np.ones((d.n_validation_events, 1))]
    v_nd: list[Floats] = [np.ones((vw.size, 1))]
    for g in groups:
        ev, nd = d.validation_columns(g)
        v_ev.append(ev)
        v_nd.append(nd)
    eta_e = reductions.matvec(np.hstack(v_ev), theta)
    eta_n = reductions.matvec(np.hstack(v_nd), theta)
    ll = reductions.total(log_softplus(eta_e)) - reductions.total(vw * softplus(eta_n))
    return ll, True


def _candidates(
    d: GroupDictionary, path: SparsePath, fit_config: FitConfig
) -> tuple[list[Candidate], int]:
    """One candidate per path point; relaxed refits are shared across points
    proposing the same groups. Returns the candidates and the refit count."""
    scored: dict[tuple[int, ...], tuple[float, bool]] = {}
    out: list[Candidate] = []
    for point in path.points:
        top, before = rank_features(
            point.groups, point.norms, lambda g: d.feature(g)[0]
        )
        groups = tuple(g for _, g in top)
        key = tuple(sorted(groups))
        if key not in scored:
            scored[key] = _relaxed(d, key, fit_config)
        ll, cert = scored[key]
        out.append(Candidate(tuple(f for f, _ in top), groups, before, ll, cert))
    return out, len(scored)


def _select(candidates: Sequence[Candidate]) -> int:
    """Best validation log L; ties (and an all ``-inf`` path) to the larger λ."""
    best = 0
    for i, c in enumerate(candidates):
        if c.validation_log_likelihood > candidates[best].validation_log_likelihood:
            best = i
    return best


# --------------------------------------------------------------------------
# The system
# --------------------------------------------------------------------------


def _choose_link(
    features: tuple[Feature, ...],
    d: GroupDictionary,
    channels: tuple[ChannelSpec, ...],
    config: SparseConfig,
    fit_config: FitConfig,
) -> tuple[Link, tuple[LinkScore, ...]]:
    if not features:
        return Link.IDENTITY, ()
    scores: list[LinkScore] = []
    for link in config.links:
        structure = canonicalise(Structure(features, link))
        try:
            result = fit(structure, d.train_datasets, channels, config=fit_config)
        except FitError:
            scores.append(LinkScore(link, -math.inf, False))
            continue
        if not result.certified:
            scores.append(LinkScore(link, -math.inf, False))
            continue
        ll = math.fsum(
            evaluate_log_likelihood(
                result,
                d.validation_datasets,
                channels,
                quadrature=fit_config.quadrature,
            )
        )
        scores.append(LinkScore(link, ll, True))
    best = 0
    for i, s in enumerate(scores):
        if s.validation_log_likelihood > scores[best].validation_log_likelihood:
            best = i
    return scores[best].link, tuple(scores)


@dataclass(frozen=True)
class BSparse:
    """Group lasso over the depth-≤2 dictionary (module docstring)."""

    config: SparseConfig = field(default_factory=SparseConfig)
    name: str = B_SPARSE

    def run(self, data: InvestigationData) -> SystemResult:
        return self.run_with_report(data)[0]

    def run_with_report(
        self, data: InvestigationData
    ) -> tuple[SystemResult, SparseReport]:
        start = time.perf_counter()
        config = self.config
        fit_config = config.fit_config or FitConfig()
        splits = [
            split_dataset(obs, config.train_fraction) for obs in data.observational
        ]
        d = GroupDictionary.build(
            [t for t, _ in splits],
            [v for _, v in splits],
            data.channels,
            alphabet=config.alphabet,
            quadrature=config.quadrature,
            workers=config.workers,
        )
        t_dict = time.perf_counter()
        path = lasso_path(d, config)
        t_path = time.perf_counter()
        candidates, n_relaxed = _candidates(d, path, fit_config)
        index = _select(candidates)
        chosen = candidates[index]
        link, link_scores = _choose_link(
            chosen.features, d, data.channels, config, fit_config
        )
        t_select = time.perf_counter()
        structure = canonicalise(Structure(chosen.features, link))
        result = fit(structure, data.observational, data.channels, config=fit_config)
        model = GLMModel(self.name, result, data.channels, data.marks)
        end = time.perf_counter()
        report = SparseReport(
            n_units=d.n_units,
            n_duplicate_units=d.n_duplicate_units,
            n_indicators=d.n_indicators,
            n_groups=d.n_groups,
            n_columns=d.n_columns,
            n_train_events=d.n_events,
            n_train_nodes=int(d.node_weights.size),
            lam_max=path.lam_max,
            path=path.points,
            candidates=tuple(candidates),
            selected_index=index,
            capped=chosen.n_features_before_cap > MAX_FEATURES,
            stop=path.stop,
            link_scores=link_scores,
            link=link,
            n_cross_products=path.n_cross_products,
            n_internal_fits=n_relaxed + len(link_scores) + 1,
            final_certified=result.certified,
            wall_time_dictionary=t_dict - start,
            wall_time_path=t_path - t_dict,
            wall_time_selection=t_select - t_path,
            wall_time_refit=end - t_select,
            wall_time_total=end - start,
        )
        system = SystemResult(
            system=self.name,
            submitted=self.name,
            structure=structure,
            model=model,
            fits_used=1,
            trajectory=(
                TrajectoryPoint(1, self.name, result.bic, self.name, result.bic, model),
            ),
        )
        return system, report
