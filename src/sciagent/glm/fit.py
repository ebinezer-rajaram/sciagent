"""The certified fitter (SPEC §2.2, §4.0 ``fit``): the instrument behind "a
proposal loses only because it is a worse structure".

:func:`fit` takes a :class:`~sciagent.glm.grammar.Structure` and one or more
:class:`~sciagent.glm.data.Dataset` s (observational plus experiments) and
returns a :class:`FitResult`: θ shared across datasets, the profiled ψ, the
total and per-dataset log-likelihood, BIC and AIC, the optimality certificate,
the a-posteriori quadrature error and a time-rescaling KS test. The agent
supplies structure only; every number here is computed by the framework
(invariant 2).

**Objective.** ``log L(θ, ψ) = Σ_d log L_d(θ, ψ)``: each dataset keeps its own
history from time 0, its own quadrature rule and its own design.

**Inner problem.** For fixed ψ the problem is convex in θ, and
:mod:`sciagent.glm.fit_solve` solves it and certifies the result with an
explicit Fenchel-dual point (read that module's docstring for the
construction, and why the default solver is Newton rather than Clarabel).

**ψ profiling** (SPEC §2.2, amended 2026-10-01). Slots are ordered by feature,
then by :func:`~sciagent.glm.grammar.psi_slots` pre-order; a ψ point is a
tuple of grid indices, and points compare by log L, ties going to the
lexicographically lowest index tuple.

- At most ``psi_full_grid_max`` (512) points: every point of the product grid
  is evaluated; the fit is globally optimal on the grid (``"exhaustive"``).
- Otherwise (``"coordinate"``): an exhaustive search over a sub-grid, then
  cycles of per-slot line searches. The sub-grid takes, per slot with L grid
  values, the indices ``{0, ⌊(L-1)/2⌋, L-1}`` (s = 3), else
  ``{⌊(L-1)/3⌋, ⌊2(L-1)/3⌋}`` (s = 2), else ``{⌊(L-1)/2⌋}`` (s = 1), using
  the largest s whose product is within the cap (all of a slot's values when
  L ≤ s). From its best point, each cycle visits the slots in order and
  evaluates every value of the slot with the others fixed, moving only to a
  strictly better value (the lowest index among the best). The search stops
  after a cycle with no move (log L strictly increases on a finite grid, so it
  terminates; ``max_psi_cycles`` is a backstop). The fit is then "certified in
  θ, coordinate-optimal in ψ".

Every ψ point is solved from the same cold start, so a point's θ does not
depend on the search path, and both searches return byte-identical results
whenever they choose the same ψ. The quadrature rule does not depend on ψ, so
each leaf of a feature tree (``Excite``, ``Periodic``, ``Trend``) is evaluated
once per (dataset, leaf subtree, leaf ψ) in a
:class:`~sciagent.glm.features.BlockCache`, and a feature block is combined
from cached leaves (products and gates are elementwise): a profile costs Σ
over leaves, not Π over a feature's slots (LOG.md 2026-10-01).
Leaves can be computed in worker processes (``workers``); each is the same
deterministic computation wherever it runs, and results are placed by key,
so the output is unchanged.

**Certification.** ``certified`` is True iff the inner solve at the chosen ψ
is certified *and* every evaluated ψ point was: a point that failed to solve
could have been the best one. ψ points whose features cannot be evaluated
(``FeatureNumericsError``, e.g. ``ExpOf`` overflowing at an extreme grid value)
are not models and are skipped, counted in ``n_infeasible_points``.

**Parameter count.** ``n_params = len(θ) + number of ψ slots``. ψ is fitted (by
profiling), so it is a free parameter; counting it charges a structure for its
shape freedom (a ``PowerK`` costs one slot more than an ``ExpK``).
``BIC = n_params · log N - 2 log L`` with N the number of counted events over
all datasets; ``AIC = 2 n_params - 2 log L``.

**Goodness of fit.** Under the fitted model the compensator increments
between consecutive counted events (endogenous, outside excluded windows,
with the windows removed from Λ) are iid Exp(1) (time-rescaling theorem).
The increments of every dataset are pooled into one KS test. Identity-link
fits use :func:`~sciagent.glm.likelihood.compensator`; exp and softplus use
the design's own rule, which already has a panel edge at every event, so it is
the same rule that ``compensator`` builds.

**Reported numbers** are folded exactly (:mod:`sciagent.core.reductions`):
the per-dataset log L is :func:`~sciagent.glm.likelihood.log_likelihood` on
the design at the chosen ψ. θ is the solver's output; its iterations use only
fixed-order folds, so it is byte-identical run to run.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import product
from typing import Final, Literal

import numpy as np
from scipy import stats

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import structure_hash
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.features import (
    DEFAULT_QUADRATURE,
    BlockCache,
    Design,
    FeatureBlock,
    FeatureNumericsError,
    LeafColumns,
    LeafKey,
    PsiAssignment,
    QuadratureSpec,
    assemble,
    design,
    leaf_columns,
    quadrature_rule,
)
from sciagent.glm.fit_bounds import simulable
from sciagent.glm.fit_solve import (
    Problem,
    Solution,
    right_limit_rows,
    solve,
    with_constraint_rows,
)
from sciagent.glm.grammar import (
    ChannelSpec,
    Feature,
    Link,
    PsiSlot,
    Structure,
    n_columns,
    psi_slots,
    validate,
)
from sciagent.glm.grids import grid
from sciagent.glm.likelihood import compensator, compensator_error, log_likelihood
from sciagent.glm.simulate import Coefficients

#: Largest product ψ grid searched exhaustively (SPEC §2.2).
PSI_FULL_GRID_MAX: Final = 512
#: Part of every fit's content address; bump on any change to fitted numbers.
#: /3: Lomax (PowerK) history sums by a sum of exponentials (features.py).
#: /4: inner-solve folds by the blocked pairwise tree; exact sums by ExactSum.
FIT_VERSION: Final = "sciagent.glm.fit/4"
#: Default certificate tolerance: ``gap ≤ 1e-8 · max(1, |log L|)``. At the
#: operating point (|log L| ≈ 10³-10⁴) that is ≤ 10⁻⁴ nats, four orders below
#: the half-log-N ≈ 4 nats a BIC parameter costs, and well above the rounding
#: of the exactly folded objective. Clarabel reaches it at tightened tolerances.
GAP_TOL_REL: Final = 1e-8

type PsiSearch = Literal["exhaustive", "coordinate"]
type InnerSolver = Literal["newton", "clarabel"]
type _Point = tuple[int, ...]
type _BlockKey = tuple[int, int, tuple[int, ...]]
type _LeafJob = tuple[
    Feature, dict[PsiSlot, float], Dataset, tuple[ChannelSpec, ...], Floats
]


class FitError(SciAgentError):
    """Base for faults raised by the fitter."""


class FitConfigError(FitError):
    """A fit configuration or request is invalid."""


class UncertifiedFitError(FitError):
    """An uncertified fit was used where a certified one is required."""


@dataclass(frozen=True)
class FitConfig:
    """How a fit is computed. Every field but ``workers`` is in the cache key.

    ``max_iter`` caps Newton steps (or Clarabel iterations per solve).
    ``workers`` > 1 computes feature blocks in that many processes; it changes
    wall time only.
    """

    quadrature: QuadratureSpec = DEFAULT_QUADRATURE
    inner_solver: str = "newton"
    max_iter: int = 100
    gap_tol_rel: float = GAP_TOL_REL
    psi_full_grid_max: int = PSI_FULL_GRID_MAX
    max_psi_cycles: int = 50
    workers: int = 1

    def __post_init__(self) -> None:
        if self.inner_solver not in ("newton", "clarabel"):
            raise FitConfigError(f"unknown inner solver {self.inner_solver!r}")
        if self.max_iter < 1:
            raise FitConfigError(f"max_iter must be ≥ 1: {self.max_iter}")
        if not (math.isfinite(self.gap_tol_rel) and self.gap_tol_rel > 0):
            raise FitConfigError(f"gap_tol_rel must be > 0: {self.gap_tol_rel}")
        if self.psi_full_grid_max < 1 or self.max_psi_cycles < 1:
            raise FitConfigError("psi_full_grid_max and max_psi_cycles must be ≥ 1")
        if self.workers < 1:
            raise FitConfigError(f"workers must be ≥ 1: {self.workers}")

    def key(self) -> str:
        """The fields that can change a fitted number, as a fixed string."""
        q = self.quadrature
        return "|".join(
            [
                f"q={q.nodes_per_panel},{q.first_panel.hex()},{q.growth.hex()},"
                f"{q.max_panel.hex()},{q.panels_per_period}",
                f"solver={self.inner_solver}",
                f"max_iter={self.max_iter}",
                f"gap={self.gap_tol_rel.hex()}",
                f"full={self.psi_full_grid_max}",
                f"cycles={self.max_psi_cycles}",
            ]
        )


@dataclass(frozen=True)
class FitResult:
    """A certified (or flagged) fit. Equality ignores ``wall_time`` only.

    ``psi`` is the chosen assignment, one plain-float mapping per feature, in
    the form :func:`~sciagent.glm.features.design` and
    :func:`~sciagent.glm.simulate.simulate` take. ``theta`` follows
    ``theta_labels`` (the design's column labels). ``duality_gap`` is
    ``F(θ) - D(u) + |r|ᵀ|θ|`` for ``F = -log L`` (as the certificate computed
    it) and dual residual r, an upper bound on ``F(θ) - F*`` to first order;
    ``duality_gap_rel`` is the same over ``max(1, |F|)`` and ``dual_residual``
    the relative size of r. Under the identity link λ ≥ 0 is enforced at every
    quadrature node and every event's right limit of the fitted data;
    ``simulable`` says whether λ ≥ 0 under *every* history
    (:mod:`sciagent.glm.fit_bounds`), which an inhibitory fit may not be.
    """

    structure: Structure
    link: Link
    psi: PsiAssignment
    theta_labels: tuple[str, ...]
    theta: tuple[float, ...]
    log_likelihood: float
    log_likelihood_per_dataset: tuple[float, ...]
    dataset_labels: tuple[str, ...]
    n_events: int
    n_params: int
    bic: float
    aic: float
    certified: bool
    solver: str
    solver_status: str
    iterations: int
    primal_objective: float
    dual_objective: float
    duality_gap: float
    duality_gap_rel: float
    dual_residual: float
    psi_search: PsiSearch
    n_psi_points: int
    n_uncertified_points: int
    n_infeasible_points: int
    quadrature_error: float
    ks_statistic: float
    ks_pvalue: float
    simulable: bool
    wall_time: float = field(compare=False)

    def require_certified(self) -> FitResult:
        """Return self, or raise :class:`UncertifiedFitError`."""
        if not self.certified:
            raise UncertifiedFitError(
                f"fit not certified: status {self.solver_status}, gap "
                f"{self.duality_gap_rel:.3g}, {self.n_uncertified_points} "
                f"uncertified ψ point(s)"
            )
        return self

    @property
    def theta_by_label(self) -> dict[str, float]:
        return dict(zip(self.theta_labels, self.theta, strict=True))


@dataclass(frozen=True)
class PredictiveCheck:
    """A predictive p-value for one named diagnostic on one dataset."""

    name: str
    dataset: str
    observed: float
    p_value: float
    n_rep: int


#: ``simulate_fn(structure, psi, coefficients, dataset, rng)`` draws a log
#: like ``dataset`` (its horizon, and in P2 its intervention) from the model.
type SimulateFn = Callable[
    [Structure, PsiAssignment, Coefficients, Dataset, np.random.Generator], EventLog
]


# --------------------------------------------------------------------------
# ψ grid bookkeeping
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Slots:
    """Global ψ slots: (feature index, slot), their grids, per-feature positions."""

    slots: tuple[tuple[int, PsiSlot], ...]
    grids: tuple[tuple[float, ...], ...]
    by_feature: tuple[tuple[int, ...], ...]

    @staticmethod
    def of(structure: Structure) -> _Slots:
        slots: list[tuple[int, PsiSlot]] = []
        by_feature: list[tuple[int, ...]] = []
        for k, feature in enumerate(structure.features):
            start = len(slots)
            slots.extend((k, s) for s in psi_slots(feature))
            by_feature.append(tuple(range(start, len(slots))))
        grids = tuple(grid(s.name) for _, s in slots)
        return _Slots(tuple(slots), grids, tuple(by_feature))

    @property
    def size(self) -> int:
        return math.prod(len(g) for g in self.grids)

    def feature_key(self, k: int, point: _Point) -> tuple[int, ...]:
        return tuple(point[i] for i in self.by_feature[k])

    def feature_psi(self, k: int, key: tuple[int, ...]) -> dict[PsiSlot, float]:
        return {
            self.slots[i][1]: self.grids[i][j]
            for i, j in zip(self.by_feature[k], key, strict=True)
        }

    def assignment(self, point: _Point) -> PsiAssignment:
        return tuple(
            self.feature_psi(k, self.feature_key(k, point))
            for k in range(len(self.by_feature))
        )


def _subgrid(length: int, s: int) -> tuple[int, ...]:
    if length <= s:
        return tuple(range(length))
    last = length - 1
    picks: tuple[int, ...]
    match s:
        case 3:
            picks = (0, last // 2, last)
        case 2:
            picks = (last // 3, (2 * last) // 3)
        case _:
            picks = (last // 2,)
    return tuple(sorted(set(picks)))


def _start_subgrid(slots: _Slots, cap: int) -> tuple[tuple[int, ...], ...]:
    for s in (3, 2, 1):
        sub = tuple(_subgrid(len(g), s) for g in slots.grids)
        if math.prod(len(x) for x in sub) <= cap or s == 1:
            return sub
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------
# Feature-block cache
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Rule:
    nodes: Floats
    weights: Floats


def _leaf_job(
    leaf: Feature,
    psi: dict[PsiSlot, float],
    data: Dataset,
    channels: tuple[ChannelSpec, ...],
    nodes: Floats,
) -> LeafColumns | None:
    """One leaf's columns, or None if its values are not finite (worker-safe)."""
    try:
        return leaf_columns(leaf, psi, data, channels, nodes)
    except FeatureNumericsError:
        return None


class _Profile:
    """One fit's ψ profile: rules, the block cache and the evaluated points."""

    def __init__(
        self,
        structure: Structure,
        datasets: tuple[Dataset, ...],
        channels: tuple[ChannelSpec, ...],
        config: FitConfig,
        pool: ProcessPoolExecutor | None,
    ) -> None:
        self.structure = structure
        self.datasets = datasets
        self.channels = channels
        self.config = config
        self.pool = pool
        self.slots = _Slots.of(structure)
        self.rules = tuple(
            _Rule(*quadrature_rule(structure, d, quadrature=config.quadrature))
            for d in datasets
        )
        self.caches = tuple(
            BlockCache(d, channels, r.nodes, r.weights)
            for d, r in zip(datasets, self.rules, strict=True)
        )
        self.blocks: dict[_BlockKey, FeatureBlock | None] = {}
        self.solutions: dict[_Point, Solution | None] = {}

    def _keys(self, point: _Point) -> list[_BlockKey]:
        return [
            (d, k, self.slots.feature_key(k, point))
            for d in range(len(self.datasets))
            for k in range(len(self.structure.features))
        ]

    def _ensure_blocks(self, points: Sequence[_Point]) -> None:
        missing = sorted(
            {key for p in points for key in self._keys(p)} - set(self.blocks)
        )
        # Every leaf the missing blocks need that no cache holds, each once, in
        # a fixed order (sorted block keys, then each tree's pre-order).
        leaf_keys: list[tuple[int, LeafKey]] = []
        jobs: list[_LeafJob] = []
        for d, k, fkey in missing:
            feature = self.structure.features[k]
            psi = self.slots.feature_psi(k, fkey)
            for key, leaf, local in self.caches[d].missing_leaves(feature, psi):
                if (d, key) in leaf_keys:
                    continue
                leaf_keys.append((d, key))
                jobs.append(
                    (leaf, local, self.datasets[d], self.channels, self.rules[d].nodes)
                )
        if self.pool is not None and len(jobs) > 1:
            results = list(self.pool.map(_leaf_job, *zip(*jobs, strict=True)))
        else:
            results = [_leaf_job(*job) for job in jobs]
        for (d, key), columns in zip(leaf_keys, results, strict=True):
            self.caches[d].put(key, columns)
        for d, k, fkey in missing:
            feature = self.structure.features[k]
            try:
                block = self.caches[d].block(feature, self.slots.feature_psi(k, fkey))
            except FeatureNumericsError:
                block = None
            self.blocks[(d, k, fkey)] = block

    def designs(self, point: _Point) -> list[Design] | None:
        designs = []
        for d, data in enumerate(self.datasets):
            blocks = []
            for k in range(len(self.structure.features)):
                block = self.blocks[(d, k, self.slots.feature_key(k, point))]
                if block is None:
                    return None
                blocks.append(block)
            rule = self.rules[d]
            designs.append(
                assemble(self.structure, tuple(blocks), data, rule.nodes, rule.weights)
            )
        return designs

    def evaluate(self, points: Sequence[_Point]) -> None:
        todo = [p for p in points if p not in self.solutions]
        self._ensure_blocks(todo)
        for point in todo:
            designs = self.designs(point)
            if designs is None:
                self.solutions[point] = None
                continue
            problem = _stack(designs, self.structure.link)
            if self.structure.link is Link.IDENTITY:
                # λ ≥ 0 also at every event's right limit (fit_solve).
                psi = self.slots.assignment(point)
                limits = [
                    right_limit_rows(self.structure, psi, d, self.channels)
                    for d in self.datasets
                ]
                problem = with_constraint_rows(problem, np.concatenate(limits, axis=1))
            self.solutions[point] = solve(
                problem,
                solver=self.config.inner_solver,
                max_iter=self.config.max_iter,
                gap_tol_rel=self.config.gap_tol_rel,
            )

    def score(self, point: _Point) -> float:
        sol = self.solutions[point]
        if sol is None or not math.isfinite(sol.primal):
            return -math.inf
        return -sol.primal

    def best(self, points: Sequence[_Point]) -> _Point:
        """The highest-scoring point; ties to the lowest index tuple."""
        self.evaluate(points)
        chosen = min(points)
        value = self.score(chosen)
        for p in sorted(points):
            if self.score(p) > value:
                chosen, value = p, self.score(p)
        return chosen


def _stack(designs: list[Design], link: Link) -> Problem:
    nodes_t = np.ascontiguousarray(np.concatenate([d.at_nodes for d in designs]).T)
    events_t = np.ascontiguousarray(np.concatenate([d.at_events for d in designs]).T)
    weights = np.concatenate([d.weights for d in designs])
    integrals = reductions.row_totals(np.stack([d.integrals for d in designs], axis=1))
    return Problem(link, nodes_t, weights, events_t, integrals)


def _search(profile: _Profile) -> tuple[_Point, PsiSearch]:
    slots = profile.slots
    ranges = [range(len(g)) for g in slots.grids]
    if slots.size <= profile.config.psi_full_grid_max:
        return profile.best(list(product(*ranges))), "exhaustive"
    sub = _start_subgrid(slots, profile.config.psi_full_grid_max)
    current = profile.best(list(product(*sub)))
    for _ in range(profile.config.max_psi_cycles):
        moved = False
        for i, values in enumerate(ranges):
            line = [(*current[:i], j, *current[i + 1 :]) for j in values]
            profile.evaluate(line)
            best_j, best_v = current[i], profile.score(current)
            for j, p in zip(values, line, strict=True):
                if profile.score(p) > best_v:
                    best_j, best_v = j, profile.score(p)
            if best_j != current[i]:
                current = (*current[:i], best_j, *current[i + 1 :])
                moved = True
        if not moved:
            break
    return current, "coordinate"


@contextmanager
def _pool(workers: int) -> Iterator[ProcessPoolExecutor | None]:
    if workers <= 1:
        yield None
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield pool


# --------------------------------------------------------------------------
# Goodness of fit
# --------------------------------------------------------------------------


def _quadrature_error_job(
    theta: Floats,
    structure: Structure,
    psi: PsiAssignment,
    data: Dataset,
    channels: tuple[ChannelSpec, ...],
    quadrature: QuadratureSpec,
) -> float:
    """:func:`~sciagent.glm.likelihood.compensator_error` (worker-safe)."""
    return compensator_error(
        theta, structure, psi, data, channels, structure.link, quadrature=quadrature
    )


def _rescaled_increments(
    theta: Floats,
    structure: Structure,
    psi: PsiAssignment,
    data: Dataset,
    des: Design,
    channels: tuple[ChannelSpec, ...],
    quadrature: QuadratureSpec,
) -> Floats:
    """``Λ(tᵢ) - Λ(tᵢ₋₁)`` over the counted events (Λ(t₀) = 0)."""
    times = data.log.times[des.event_index]
    link = structure.link
    if link is Link.IDENTITY:
        big = compensator(
            theta, structure, psi, data, channels, link, times, quadrature=quadrature
        )
    else:
        eta = reductions.matvec(des.at_nodes, theta)
        if link is Link.EXP:
            values = des.weights * np.exp(eta)
        else:
            values = des.weights * np.logaddexp(0.0, eta)
        cuts = np.searchsorted(des.nodes, times, side="left").tolist()
        big = np.empty(times.size)
        running = 0.0
        lo = 0
        for i, hi in enumerate(cuts):
            running += reductions.total(values[lo:hi])
            big[i] = running
            lo = hi
    out: Floats = np.diff(np.concatenate([[0.0], big]))
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _check(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    config: FitConfig,
) -> tuple[Dataset, ...]:
    validate(structure, channels)
    data = tuple(datasets)
    if not data:
        raise FitConfigError("fit needs at least one dataset")
    if not all(isinstance(d, Dataset) for d in data):
        raise FitConfigError("datasets must be Dataset instances")
    if config.inner_solver == "clarabel" and structure.link is Link.SOFTPLUS:
        raise FitConfigError(
            "log∘softplus is not DCP-expressible, so Clarabel cannot pose the "
            "softplus problem; use the Newton solver"
        )
    return data


def fit(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    *,
    config: FitConfig | None = None,
) -> FitResult:
    """Fit θ (shared across ``datasets``) and profile ψ over its grid.

    See the module docstring for the search, the certificate and every
    reported quantity. Uncertified fits are returned flagged, never raised:
    use :meth:`FitResult.require_certified` where only a certified fit will do.
    """
    start = time.perf_counter()
    config = config or FitConfig()
    data = _check(structure, datasets, channels, config)
    link = structure.link
    with _pool(config.workers) as pool:
        profile = _Profile(structure, data, channels, config, pool)
        point, search = _search(profile)
        sol = profile.solutions[point]
        designs = profile.designs(point)
        if sol is None or designs is None:
            raise FitError("no ψ point of this structure can be evaluated on the data")
        psi = profile.slots.assignment(point)
        theta = np.array(sol.theta)
        jobs = [(theta, structure, psi, d, channels, config.quadrature) for d in data]
        if pool is not None and len(jobs) > 1:
            errors = list(pool.map(_quadrature_error_job, *zip(*jobs, strict=True)))
        else:
            errors = [_quadrature_error_job(*job) for job in jobs]
    per = tuple(log_likelihood(theta, d, link) for d in designs)
    total = math.fsum(per)
    quad = math.fsum(errors)
    tau = np.concatenate(
        [
            _rescaled_increments(
                theta, structure, psi, d, des, channels, config.quadrature
            )
            for d, des in zip(data, designs, strict=True)
        ]
    )
    if tau.size:
        ks = stats.kstest(tau, "expon")
        ks_stat, ks_p = float(ks.statistic), float(ks.pvalue)
    else:
        ks_stat, ks_p = math.nan, math.nan
    n_events = sum(d.at_events.shape[0] for d in designs)
    k = len(sol.theta) + len(profile.slots.slots)
    solved = [s for s in profile.solutions.values() if s is not None]
    uncertified = sum(not s.certified for s in solved)
    infeasible = len(profile.solutions) - len(solved)
    return FitResult(
        structure=structure,
        link=link,
        psi=psi,
        theta_labels=designs[0].labels,
        theta=sol.theta,
        log_likelihood=total,
        log_likelihood_per_dataset=per,
        dataset_labels=tuple(d.label for d in data),
        n_events=n_events,
        n_params=k,
        bic=k * math.log(max(n_events, 1)) - 2.0 * total,
        aic=2.0 * k - 2.0 * total,
        certified=sol.certified and uncertified == 0,
        solver=sol.solver,
        solver_status=sol.status,
        iterations=sol.iterations,
        primal_objective=sol.primal,
        dual_objective=sol.dual,
        duality_gap=sol.gap,
        duality_gap_rel=sol.gap_rel,
        dual_residual=sol.residual,
        psi_search=search,
        n_psi_points=len(profile.solutions),
        n_uncertified_points=uncertified,
        n_infeasible_points=infeasible,
        quadrature_error=quad,
        ks_statistic=ks_stat,
        ks_pvalue=ks_p,
        simulable=simulable(structure, sol.theta, channels),
        wall_time=time.perf_counter() - start,
    )


def evaluate_log_likelihood(
    result: FitResult,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    *,
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
) -> tuple[float, ...]:
    """The fitted model's log L on each of ``datasets`` (e.g. held-out data)."""
    theta = np.array(result.theta)
    return tuple(
        log_likelihood(
            theta,
            design(result.structure, result.psi, d, channels, quadrature=quadrature),
            result.link,
        )
        for d in datasets
    )


def to_coefficients(result: FitResult) -> tuple[PsiAssignment, Coefficients]:
    """ψ and θ in the simulator's form (:class:`~sciagent.glm.simulate.Coefficients`).

    θ₀ is the intercept; each feature takes its ``n_columns`` contiguous
    coefficients in design order (a ``Product`` row-major, as in
    ``grammar.py``), which is the order the simulator reads.
    """
    per_feature: list[tuple[float, ...]] = []
    start = 1
    for feature in result.structure.features:
        width = n_columns(feature)
        per_feature.append(tuple(result.theta[start : start + width]))
        start += width
    psi = tuple(dict(m) for m in result.psi)
    return psi, Coefficients(result.theta[0], tuple(per_feature))


def predictive_pvalues(
    result: FitResult,
    datasets: Sequence[Dataset],
    diagnostics: Mapping[str, Callable[[EventLog], float]],
    simulate_fn: SimulateFn,
    n_rep: int,
    rng: np.random.Generator,
) -> tuple[PredictiveCheck, ...]:
    """Plug-in predictive p-values of named diagnostics (SPEC §2.2, §4.0).

    For each dataset, ``n_rep`` logs are drawn from the fitted model (θ and ψ
    fixed at the fit: a parametric bootstrap, not a posterior predictive) and
    every diagnostic is evaluated on each. The p-value is two-sided with the
    +1 correction: ``min(1, 2·min(P≥, P≤))``, ``P≥ = (1 + #{T_rep ≥ T_obs}) /
    (n_rep + 1)``. ``rng`` is consumed dataset by dataset, replicate by
    replicate, so the output is deterministic. Ordered by (name, dataset).
    """
    if n_rep < 1:
        raise FitConfigError(f"n_rep must be ≥ 1: {n_rep}")
    psi, coef = to_coefficients(result)
    names = sorted(diagnostics)
    rows: list[tuple[str, int, PredictiveCheck]] = []
    for index, data in enumerate(datasets):
        observed = {name: float(diagnostics[name](data.log)) for name in names}
        reps: dict[str, list[float]] = {name: [] for name in names}
        for _ in range(n_rep):
            log = simulate_fn(result.structure, psi, coef, data, rng)
            for name in names:
                reps[name].append(float(diagnostics[name](log)))
        for name in names:
            values = np.array(reps[name])
            obs = observed[name]
            upper = (1 + int(np.count_nonzero(values >= obs))) / (n_rep + 1)
            lower = (1 + int(np.count_nonzero(values <= obs))) / (n_rep + 1)
            p = min(1.0, 2.0 * min(upper, lower))
            rows.append((name, index, PredictiveCheck(name, data.label, obs, p, n_rep)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return tuple(r[2] for r in rows)


def fit_cache_key(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    config: FitConfig | None = None,
) -> str:
    """The content address of a fit: SHA-256 over the fit version, the
    canonical structure hash, the config (all but ``workers``), the channel
    standardisation and every dataset's bytes, in order.

    Equivalent structures share a key, but θ is laid out in the order of the
    structure that was fitted: memoise ``fit(canonicalise(structure), …)``.
    """
    h = hashlib.sha256()

    def put(part: bytes) -> None:
        h.update(len(part).to_bytes(8, "little"))
        h.update(part)

    put(FIT_VERSION.encode())
    put(structure_hash(structure).encode())
    put((config or FitConfig()).key().encode())
    for spec in sorted(channels, key=lambda c: c.name):
        put(
            f"{spec.name}|{spec.kind.value}|{spec.location.hex()}|{spec.scale.hex()}".encode()
        )
    for data in datasets:
        log = data.log
        put(data.label.encode())
        put(log.horizon.hex().encode())
        put(np.ascontiguousarray(log.times, dtype="<f8").tobytes())
        put(np.ascontiguousarray(data.endogenous, dtype=np.uint8).tobytes())
        put(";".join(f"{a.hex()},{b.hex()}" for a, b in data.excluded).encode())
        for name in sorted(log.marks):
            put(name.encode())
            put(np.ascontiguousarray(log.marks[name], dtype="<f8").tobytes())
    return h.hexdigest()
