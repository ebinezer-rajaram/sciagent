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
- Otherwise (``"coordinate"``, fit/5): block-coordinate ascent from a set
  of starts. Every option below is a :class:`FitConfig` field in ``key()``.

  *Blocks.* A block is one feature's slots when the feature's grid has at
  most ``psi_block_max`` (128) points. A larger feature is split by leaf (an
  ``Excite``'s kernel and mark slots, a ``Periodic``'s period, a condition's
  slots), a leaf over the cap into single slots, and consecutive leaves are
  merged while the product stays within the cap. So coupled slots of one leaf
  (``PhaseWindow`` period and phase, ``PowerK`` c and p) move together, and no
  block exceeds the cap however deep the feature.

  *Ascent.* Each cycle visits the blocks in order and evaluates the block's
  joint grid with all other slots fixed, moving only to a strictly better
  point (the lowest index tuple among the best), until a cycle with no move
  (``max_psi_cycles`` is a backstop).

  *Swap check.* After the ascent, every pair of slot groups with the same
  slot names (two whole features, or two leaves anywhere, also inside one
  nested feature) is tried with its values exchanged: one evaluation per
  pair. From the best strictly improving swap the ascent is rerun; this
  repeats at most ``psi_swap_rounds`` (4) times. It recovers fits that
  converged with two features' ψ interchanged.

  *Starts*, in order, duplicates dropped:

  1. if ``psi_line_start``, the end of fit/4's search (an exhaustive start
     sub-grid, below, then per-slot line searches);
  2. the ``psi_starts`` best points of the start sub-grid: per slot with L
     grid values the indices ``{0, ⌊(L-1)/2⌋, L-1}`` (s = 3), else
     ``{⌊(L-1)/3⌋, ⌊2(L-1)/3⌋}`` (s = 2), else ``{⌊(L-1)/2⌋}`` (s = 1),
     using the largest s whose product is within ``psi_full_grid_max``;
  3. if ``psi_screen`` = r > 0 (default 2), **screening**: each feature alone (the
     intercept plus that feature, on the fitted structure's rule, sharing its
     block cache) is ascended from the best point of its own sub-grid of at
     most ``psi_block_max`` points. Its r best points are kept; the joint
     start takes every feature's best, and for r ≥ 2 one more start per
     feature swaps in that feature's next-best. Cost: the sum over features,
     not the product;
  4. if ``psi_loo_starts``, for each feature m, the ψ of the structure
     without m (searched one level down with screening starts only), with m's
     slots from its screening.

  ``psi_max_sweeps`` > 0 caps all inner solves of the search at that many
  sweeps (one sweep evaluates every block once); 0 is no cap. The cap is a
  solve count, so the result is deterministic. The fit is the best evaluated
  point (ties to the lowest index tuple): "certified in θ,
  block-coordinate-optimal in ψ", not globally optimal on the grid
  (LOG 2026-10-03 has the benchmark).

Every ψ point is solved from the same cold start, so a point's θ does not
depend on the search path, and both searches return byte-identical results
whenever they choose the same ψ. The quadrature rule does not depend on ψ, so
each leaf of a feature tree (``Excite``, ``Periodic``, ``Trend``) is evaluated
once per (dataset, leaf subtree, leaf ψ) in a
:class:`~sciagent.glm.features.BlockCache`, and a feature block is combined
from cached leaves (products and gates are elementwise): a profile costs Σ
over leaves, not Π over a feature's slots (LOG.md 2026-10-01).
The combined feature blocks (one per dataset, feature and feature ψ, so Π
over a feature's slots) are kept in a least-recently-used cache bounded by
``block_cache_bytes``; a dropped block is rebuilt from the leaves byte for
byte, so the bound changes wall time and memory only (LOG 2026-10-06).
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
from collections import OrderedDict
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
    Excite,
    Feature,
    Gate,
    Link,
    Product,
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
#: Largest ψ block (product of its slots' grid sizes) the coordinate search
#: evaluates jointly. 128 keeps a ``Gate(Periodic, PhaseWindow)`` (100 points:
#: the period and the window are coupled) in one block (LOG 2026-10-03).
PSI_BLOCK_MAX: Final = 128
#: Default bound on the bytes of feature blocks one fit keeps (LOG
#: 2026-10-06, "fit/5 block cache"). Peak memory on the dev-013 superset
#: (three long datasets) is about this plus 0.5 GB; unbounded it passed 11 GB.
BLOCK_CACHE_BYTES: Final = 1 << 30
#: Part of every fit's content address; bump on any change to fitted numbers.
#: /3: Lomax (PowerK) history sums by a sum of exponentials (features.py).
#: /4: inner-solve folds by the blocked pairwise tree; exact sums by ExactSum.
#: /5: screening-start block-coordinate ψ search with a swap check (LOG
#:     2026-10-03, defaults LOG 2026-10-06).
FIT_VERSION: Final = "sciagent.glm.fit/5"
#: Default certificate tolerance: ``gap ≤ 1e-8 · max(1, |log L|)``. At the
#: operating point (|log L| ≈ 10³-10⁴) that is ≤ 10⁻⁴ nats, four orders below
#: the half-log-N ≈ 4 nats a BIC parameter costs, and well above the rounding
#: of the exactly folded objective. Clarabel reaches it at tightened tolerances.
GAP_TOL_REL: Final = 1e-8

type PsiSearch = Literal["exhaustive", "coordinate", "fixed"]
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
    """How a fit is computed. Every field but ``workers`` and
    ``block_cache_bytes`` is in the cache key.

    ``max_iter`` caps Newton steps (or Clarabel iterations per solve).
    ``workers`` > 1 computes feature blocks in that many processes; it changes
    wall time only. ``block_cache_bytes`` bounds the feature blocks the ψ
    search keeps (least recently used are dropped first; 0 keeps none); a
    dropped block is rebuilt from the leaf cache byte for byte, so it changes
    wall time and memory only.
    """

    quadrature: QuadratureSpec = DEFAULT_QUADRATURE
    inner_solver: str = "newton"
    max_iter: int = 100
    gap_tol_rel: float = GAP_TOL_REL
    psi_full_grid_max: int = PSI_FULL_GRID_MAX
    max_psi_cycles: int = 50
    psi_starts: int = 1
    psi_block_max: int = PSI_BLOCK_MAX
    psi_line_start: bool = False
    psi_screen: int = 2
    psi_loo_starts: bool = False
    psi_max_sweeps: int = 0
    psi_swap_rounds: int = 4
    workers: int = 1
    block_cache_bytes: int = BLOCK_CACHE_BYTES

    def __post_init__(self) -> None:
        if self.inner_solver not in ("newton", "clarabel"):
            raise FitConfigError(f"unknown inner solver {self.inner_solver!r}")
        if self.max_iter < 1:
            raise FitConfigError(f"max_iter must be ≥ 1: {self.max_iter}")
        if not (math.isfinite(self.gap_tol_rel) and self.gap_tol_rel > 0):
            raise FitConfigError(f"gap_tol_rel must be > 0: {self.gap_tol_rel}")
        if self.psi_full_grid_max < 1 or self.max_psi_cycles < 1:
            raise FitConfigError("psi_full_grid_max and max_psi_cycles must be ≥ 1")
        if (
            min(
                self.psi_starts,
                self.psi_screen,
                self.psi_max_sweeps,
                self.psi_swap_rounds,
            )
            < 0
        ):
            raise FitConfigError("psi_starts, psi_screen, psi_max_sweeps must be ≥ 0")
        if self.psi_block_max < 1:
            raise FitConfigError(f"psi_block_max must be ≥ 1: {self.psi_block_max}")
        if self.workers < 1:
            raise FitConfigError(f"workers must be ≥ 1: {self.workers}")
        if self.block_cache_bytes < 0:
            raise FitConfigError(
                f"block_cache_bytes must be ≥ 0: {self.block_cache_bytes}"
            )

    def key(self) -> str:
        """The fields that can change a fitted number, as a fixed string.

        ``workers`` and ``block_cache_bytes`` are left out: neither can.
        """
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
                f"starts={self.psi_starts}",
                f"block={self.psi_block_max}",
                f"line={int(self.psi_line_start)}",
                f"screen={self.psi_screen}",
                f"loo={int(self.psi_loo_starts)}",
                f"sweeps={self.psi_max_sweeps}",
                f"swap_rounds={self.psi_swap_rounds}",
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
    #: Per slot, the path of its leaf: an ``Excite`` (kernel and mark slots
    #: together) or the node itself (``Periodic``, a condition).
    leaves: tuple[tuple[int, ...], ...]

    @staticmethod
    def of(structure: Structure) -> _Slots:
        slots: list[tuple[int, PsiSlot]] = []
        by_feature: list[tuple[int, ...]] = []
        leaves: list[tuple[int, ...]] = []
        for k, feature in enumerate(structure.features):
            start = len(slots)
            for s in psi_slots(feature):
                slots.append((k, s))
                parent = s.path[:-1]
                is_excite = bool(s.path) and isinstance(_node(feature, parent), Excite)
                leaves.append(parent if is_excite else s.path)
            by_feature.append(tuple(range(start, len(slots))))
        grids = tuple(grid(s.name) for _, s in slots)
        return _Slots(tuple(slots), grids, tuple(by_feature), tuple(leaves))

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

    def point(self, psi: PsiAssignment) -> _Point:
        """The grid indices of an on-grid assignment (FitConfigError if not)."""
        if len(psi) != len(self.by_feature):
            raise FitConfigError(
                f"ψ has {len(psi)} feature(s); the structure has {len(self.by_feature)}"
            )
        out: list[int] = []
        for k, positions in enumerate(self.by_feature):
            given = psi[k]
            wanted = {self.slots[i][1] for i in positions}
            if set(given) != wanted:
                raise FitConfigError(f"ψ of feature {k} does not match its slots")
            for i in positions:
                value = given[self.slots[i][1]]
                values = self.grids[i]
                if value not in values:
                    raise FitConfigError(
                        f"ψ value {value!r} of {self.slots[i][1].name} is off its grid"
                    )
                out.append(values.index(value))
        return tuple(out)

    def assignment(self, point: _Point) -> PsiAssignment:
        return tuple(
            self.feature_psi(k, self.feature_key(k, point))
            for k in range(len(self.by_feature))
        )


def _node(feature: Feature, path: tuple[int, ...]) -> object:
    """The node at ``path`` (:class:`~sciagent.glm.grammar.PsiSlot` addressing)."""
    node: object = feature
    for step in path:
        match node:
            case Product(left=left, right=right):
                node = (left, right)[step]
            case Gate(feature=inner, cond=cond):
                node = (inner, cond)[step]
            case Excite(kernel=kernel, mark=mark):
                node = (kernel, mark)[step]
            case _:
                raise AssertionError(f"no child {step} of {node!r}")
    return node


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


class _BlockLRU:
    """Feature blocks by key, holding at most ``limit`` bytes of block arrays.

    The least recently used block is dropped first; a block larger than the
    limit is not kept. Recency follows the sequence of :meth:`get` and
    :meth:`put` calls only (never timing or object ids), so what is kept is
    deterministic. A dropped block is rebuilt by
    :meth:`~sciagent.glm.features.BlockCache.block` from the leaf cache, which
    is never bounded (it holds Σ over leaf grids, not Π over a feature's
    slots), and the rebuild is byte-identical: the bound changes wall time and
    memory only. ``None`` (a block whose values are not finite) costs 0 bytes.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.nbytes = 0
        self._items: OrderedDict[_BlockKey, tuple[FeatureBlock | None, int]] = (
            OrderedDict()
        )

    def __contains__(self, key: _BlockKey) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: _BlockKey) -> FeatureBlock | None:
        """The cached block (KeyError if absent), now the most recent."""
        block, _ = self._items[key]
        self._items.move_to_end(key)
        return block

    def put(self, key: _BlockKey, block: FeatureBlock | None) -> None:
        size = (
            0
            if block is None
            else block.at_events.nbytes + block.at_nodes.nbytes + block.integrals.nbytes
        )
        if size > self.limit:
            return
        self._items[key] = (block, size)
        self.nbytes += size
        while self.nbytes > self.limit:
            _, (_, dropped) = self._items.popitem(last=False)
            self.nbytes -= dropped


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
        self.blocks = _BlockLRU(config.block_cache_bytes)
        self.solutions: dict[_Point, Solution | None] = {}
        #: Local feature k is feature ``index[k]`` of the fitted structure;
        #: block keys use the fitted structure's indices.
        self.index: tuple[int, ...] = tuple(range(len(structure.features)))
        self.top_features = structure.features
        #: Per-feature screening results, by fitted-structure feature index.
        self.screens: dict[int, list[tuple[int, ...]]] = {}
        #: Inner solves of this profile and all its views (the search budget).
        self.solves = [0]

    def view(self, features: tuple[int, ...]) -> _Profile:
        """The sub-structure of ``features`` (indices into this profile's own
        features, in order), evaluated on this profile's rule and sharing its
        block cache: a sub-structure costs solves only, no new leaves."""
        sub = object.__new__(_Profile)
        sub.structure = Structure(
            tuple(self.structure.features[k] for k in features), self.structure.link
        )
        sub.datasets, sub.channels = self.datasets, self.channels
        sub.config, sub.pool = self.config, self.pool
        sub.slots = _Slots.of(sub.structure)
        sub.rules, sub.caches, sub.blocks = self.rules, self.caches, self.blocks
        sub.screens = self.screens
        sub.solves = self.solves
        sub.solutions = {}
        sub.index = tuple(self.index[k] for k in features)
        sub.top_features = self.top_features
        return sub

    def _keys(self, point: _Point) -> list[_BlockKey]:
        return [
            (d, self.index[k], self.slots.feature_key(k, point))
            for d in range(len(self.datasets))
            for k in range(len(self.structure.features))
        ]

    def _ensure_leaves(self, points: Sequence[_Point]) -> None:
        """Compute every leaf that the blocks of ``points`` not in the block
        cache need and no leaf cache holds, in one batch (in the pool, if
        any). Blocks are built later, one point at a time (:meth:`designs`),
        so a batch of points never holds all its blocks at once."""
        needed = sorted({key for p in points for key in self._keys(p)})
        missing = [key for key in needed if key not in self.blocks]
        # Every leaf the missing blocks need that no cache holds, each once, in
        # a fixed order (sorted block keys, then each tree's pre-order).
        leaf_keys: list[tuple[int, LeafKey]] = []
        jobs: list[_LeafJob] = []
        local_k = {top: k for k, top in enumerate(self.index)}
        for d, top, fkey in missing:
            feature = self.top_features[top]
            psi = self.slots.feature_psi(local_k[top], fkey)
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

    def _block(self, d: int, k: int, point: _Point) -> FeatureBlock | None:
        """Local feature k's block on dataset d at ``point``: from the block
        cache, else built from the leaf cache (None if not finite)."""
        fkey = self.slots.feature_key(k, point)
        key = (d, self.index[k], fkey)
        if key in self.blocks:
            return self.blocks.get(key)
        try:
            block: FeatureBlock | None = self.caches[d].block(
                self.structure.features[k], self.slots.feature_psi(k, fkey)
            )
        except FeatureNumericsError:
            block = None
        self.blocks.put(key, block)
        return block

    def designs(self, point: _Point) -> list[Design] | None:
        designs = []
        for d, data in enumerate(self.datasets):
            blocks = []
            for k in range(len(self.structure.features)):
                block = self._block(d, k, point)
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
        self.solves[0] += len(todo)
        self._ensure_leaves(todo)
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


def _search_blocks(slots: _Slots, cap: int) -> tuple[tuple[int, ...], ...]:
    """The slot positions searched jointly, feature by feature (module docstring).

    A feature is one block when its grid is within ``cap``. Otherwise its
    slots are grouped by leaf (an ``Excite``'s kernel and mark slots, a
    ``Periodic``'s period, a condition's slots: consecutive in pre-order), a
    leaf group over ``cap`` is split into single slots, and consecutive groups
    are merged while the product of their grid sizes stays within ``cap``.
    """

    def size(block: Sequence[int]) -> int:
        return math.prod(len(slots.grids[i]) for i in block)

    blocks: list[tuple[int, ...]] = []
    for positions in slots.by_feature:
        groups: list[list[int]] = []
        for i in positions:
            leaf = slots.leaves[i]
            if groups and slots.leaves[groups[-1][0]] == leaf:
                groups[-1].append(i)
            else:
                groups.append([i])
        split: list[list[int]] = []
        for g in groups:
            split.extend([[i] for i in g] if size(g) > cap else [g])
        merged: list[list[int]] = []
        for g in split:
            if merged and size(merged[-1] + g) <= cap:
                merged[-1] = merged[-1] + g
            else:
                merged.append(g)
        blocks.extend(tuple(b) for b in merged)
    return tuple(blocks)


class _Budget(Exception):
    """The refinement's point budget is spent (internal control flow)."""


def _line_search(profile: _Profile, start: _Point) -> _Point:
    """fit/4's per-slot coordinate cycles from ``start`` (module docstring)."""
    ranges = [range(len(g)) for g in profile.slots.grids]
    current = start
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
    return current


def _block_points(
    grids: tuple[tuple[float, ...], ...], current: _Point, block: tuple[int, ...]
) -> list[_Point]:
    points: list[_Point] = []
    for values in product(*(range(len(grids[i])) for i in block)):
        moved_to = list(current)
        for i, j in zip(block, values, strict=True):
            moved_to[i] = j
        points.append(tuple(moved_to))
    return points


def _ascend(
    profile: _Profile,
    start: _Point,
    blocks: tuple[tuple[int, ...], ...],
    limit: int,
) -> None:
    """Block-coordinate ascent from ``start``, strict improvements only.

    Each cycle visits the blocks in order. Raises :class:`_Budget` before
    evaluating a block that would take the search past ``limit`` inner
    solves.
    """
    grids = profile.slots.grids

    def best_in(current: _Point, block: tuple[int, ...]) -> _Point:
        points = _block_points(grids, current, block)
        new = sum(p not in profile.solutions for p in points)
        if profile.solves[0] + new > limit:
            raise _Budget
        profile.evaluate(points)
        best, value = current, profile.score(current)
        for p in points:
            if profile.score(p) > value:
                best, value = p, profile.score(p)
        return best

    current = start
    for _ in range(profile.config.max_psi_cycles):
        moved = False
        for block in blocks:
            best = best_in(current, block)
            if best != current:
                current, moved = best, True
        if not moved:
            return


def _search(profile: _Profile) -> tuple[_Point, PsiSearch]:
    if profile.slots.size <= profile.config.psi_full_grid_max:
        grids = profile.slots.grids
        return profile.best(list(product(*(range(len(g)) for g in grids)))), (
            "exhaustive"
        )
    return _coordinate(profile, top=True), "coordinate"


def _ranked(profile: _Profile) -> list[_Point]:
    return sorted(profile.solutions, key=lambda p: (-profile.score(p), p))


def _screen(profile: _Profile, k: int) -> list[tuple[int, ...]]:
    """Feature k alone (on this rule, sharing the block cache): block-coordinate
    ascent from the best point of a start sub-grid of at most
    ``psi_block_max`` points; its ``psi_screen`` best points. Memoised per
    feature of the fitted structure, so views share it."""
    top = profile.index[k]
    if top not in profile.screens:
        alone = profile.view((k,))
        cap = alone.config.psi_block_max
        start = alone.best(list(product(*_start_subgrid(alone.slots, cap))))
        _ascend(alone, start, _search_blocks(alone.slots, cap), 1 << 62)
        profile.screens[top] = _ranked(alone)[: alone.config.psi_screen]
    return profile.screens[top]


def _coordinate(profile: _Profile, top: bool) -> _Point:
    """The coordinate search (module docstring) on the fitted structure
    (``top``) or on one leave-one-out sub-structure (screening starts only)."""
    slots, config = profile.slots, profile.config
    n = len(profile.structure.features)
    starts: list[_Point] = []
    sub: list[_Point] = []
    wants_sub = config.psi_line_start or config.psi_starts > 0 if top else False
    if wants_sub or config.psi_screen == 0:
        sub = list(product(*_start_subgrid(slots, config.psi_full_grid_max)))
        profile.evaluate(sub)
        sub.sort(key=lambda p: (-profile.score(p), p))
        if top and config.psi_line_start:
            starts.append(_line_search(profile, sub[0]))
        starts.extend(sub[: max(config.psi_starts, 0 if top else 1)])
    screened = [_screen(profile, k) for k in range(n)] if config.psi_screen else []

    def joint(choice: Sequence[int]) -> _Point:
        out: list[int] = []
        for k, r in enumerate(choice):
            out.extend(screened[k][min(r, len(screened[k]) - 1)])
        return tuple(out)

    if screened:
        starts.append(joint([0] * n))
        for k in range(n):
            for r in range(1, config.psi_screen):
                starts.append(joint([r if m == k else 0 for m in range(n)]))
    if top and config.psi_loo_starts and n > 1:
        # Each (n-1)-feature sub-structure's own fit, one level down, with
        # the left-out feature's slots from its screening (or the sub-grid).
        base = joint([0] * n) if screened else sub[0]
        for m in range(n):
            rest = tuple(k for k in range(n) if k != m)
            view = profile.view(rest)
            end = _coordinate(view, top=False)
            merged = list(base)
            for local, k in enumerate(rest):
                inner = view.slots.by_feature[local]
                for i, x in zip(slots.by_feature[k], inner, strict=True):
                    merged[i] = end[x]
            starts.append(tuple(merged))
    blocks = _search_blocks(slots, config.psi_block_max)
    # The budget, in sweeps: one sweep evaluates every block once.
    sweep = sum(math.prod(len(slots.grids[i]) for i in b) for b in blocks)
    limit = config.psi_max_sweeps * sweep if config.psi_max_sweeps else 1 << 62
    unique = list(dict.fromkeys(starts))
    profile.evaluate(unique)
    try:
        for start in unique:
            _ascend(profile, start, blocks, limit)
        pairs = _swap_pairs(slots)
        for _ in range(config.psi_swap_rounds if top else 0):
            best = _ranked(profile)[0]
            swapped = [_swapped(best, a, b) for a, b in pairs]
            profile.evaluate(swapped)
            better = [p for p in swapped if profile.score(p) > profile.score(best)]
            if not better:
                break
            _ascend(
                profile,
                min(better, key=lambda p: (-profile.score(p), p)),
                blocks,
                limit,
            )
    except _Budget:
        pass
    return _ranked(profile)[0]


def _swap_pairs(slots: _Slots) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Pairs of slot groups whose values can be exchanged: two whole features
    with the same slot names in order, and two leaves (anywhere, also inside
    one nested feature) with the same slot names. Fixed order."""

    def names(group: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(slots.slots[i][1].name for i in group)

    leaves: dict[tuple[int, tuple[int, ...]], list[int]] = {}
    for i, (k, _) in enumerate(slots.slots):
        leaves.setdefault((k, slots.leaves[i]), []).append(i)
    groups = [g for g in slots.by_feature if g] + [tuple(g) for g in leaves.values()]
    pairs: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    for x, a in enumerate(groups):
        for b in groups[x + 1 :]:
            if names(a) == names(b) and not set(a) & set(b):
                pairs.append((a, b))
    return list(dict.fromkeys(pairs))


def _swapped(point: _Point, a: tuple[int, ...], b: tuple[int, ...]) -> _Point:
    out = list(point)
    for i, j in zip(a, b, strict=True):
        out[i], out[j] = point[j], point[i]
    return tuple(out)


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
    psi: PsiAssignment | None = None,
) -> FitResult:
    """Fit θ (shared across ``datasets``) and profile ψ over its grid.

    See the module docstring for the search, the certificate and every
    reported quantity. Uncertified fits are returned flagged, never raised:
    use :meth:`FitResult.require_certified` where only a certified fit will do.

    With ``psi`` (an on-grid assignment), ψ is fixed there and only θ is
    fitted (``psi_search == "fixed"``): the θ-refit at one ψ point, e.g. the
    truth's, computed exactly as the search computes that point.
    """
    start = time.perf_counter()
    config = config or FitConfig()
    data = _check(structure, datasets, channels, config)
    link = structure.link
    with _pool(config.workers) as pool:
        profile = _Profile(structure, data, channels, config, pool)
        if psi is None:
            point, search = _search(profile)
        else:
            point, search = profile.slots.point(psi), "fixed"
            profile.evaluate([point])
        sol = profile.solutions[point]
        designs = profile.designs(point)
        if sol is None or designs is None:
            raise FitError("no ψ point of this structure can be evaluated on the data")
        chosen = profile.slots.assignment(point)
        theta = np.array(sol.theta)
        jobs = [
            (theta, structure, chosen, d, channels, config.quadrature) for d in data
        ]
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
                theta, structure, chosen, d, des, channels, config.quadrature
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
        psi=chosen,
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


def on_grid(structure: Structure, psi: PsiAssignment) -> bool:
    """Whether ``psi`` is an assignment of ``structure``'s slots on their grids
    (so ``fit(..., psi=psi)`` accepts it)."""
    try:
        _Slots.of(structure).point(psi)
    except FitConfigError:
        return False
    return True


def parameter_log_likelihood(
    structure: Structure,
    psi: PsiAssignment,
    coefficients: Coefficients,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    *,
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
) -> tuple[float, ...]:
    """log L of given parameters (e.g. a truth's) on each dataset (the inverse
    of :func:`to_coefficients`). For on-grid ψ this is the rule and the folds
    a fit of ``structure`` uses; an off-grid ψ also resolves its own scales.
    """
    theta = np.array(
        [coefficients.intercept, *(c for f in coefficients.per_feature for c in f)]
    )
    return tuple(
        log_likelihood(
            theta,
            design(
                structure,
                psi,
                d,
                channels,
                quadrature=quadrature,
                allow_off_grid=True,
            ),
            structure.link,
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
    canonical structure hash, the config (all but ``workers`` and
    ``block_cache_bytes``), the channel
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
