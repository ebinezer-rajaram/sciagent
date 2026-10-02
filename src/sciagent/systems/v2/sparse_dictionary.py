"""B-sparse's dictionary (SPEC §4.1): every feature of depth ≤ 2 at every ψ.

The dictionary is too large to materialise. For pointproc it holds about a
million groups (LOG.md 2026-10-01: ~1.03M columns), each a column block over
~7·10⁴ quadrature nodes, so a dense design would be hundreds of gigabytes.
This module never builds it. It holds only the depth-1 columns and computes
everything the group lasso needs about the depth-2 columns from them.

**Units and groups.** A *unit* is an atom (``Excite``, ``Periodic``, ``Trend``)
at one point of its ψ grid; it has ``n_columns(atom)`` columns (1, or 2 for
``Periodic``). An *indicator* is a gate condition at one point of its ψ grid.
By the canonical form (``canonical.py``), a depth-≤2 feature is one atom, a
``Product`` of two atoms, or a ``Gate`` of one atom, so the groups are

- ATOM ``u``: the unit's columns;
- PRODUCT ``{u, v}`` (unordered, squares included): every column of ``u``
  times every column of ``v``;
- GATE ``(u, κ)``: the unit's columns where indicator κ holds, else 0.

Two ψ points of one feature whose columns coincide (``Product(a@ψ₁, a@ψ₂)``
and ``Product(a@ψ₂, a@ψ₁)``) are one group here, so the count is about 3%
below :func:`~sciagent.glm.space.dictionary_size`'s, which counts ordered ψ
pairs. A unit whose columns equal an earlier unit's byte for byte (``Pow``
with exponent 1 is ``Mark`` when the channel's location is 1) is a duplicate:
it and every group containing it are dropped, so the lasso never splits one
column between two names.

**The extended matrix.** Let ``A`` be the unit columns at the training rows,
``I`` the indicator columns (1.0/0.0) and ``1`` a column of ones, and
``B = [A | I | 1]`` (``C + K + 1`` columns). Every dictionary column is the
elementwise product of a column of ``A`` with a column of ``B``, so for any
node weights ``d`` the weighted cross products of *all* dictionary columns with
the data are the entries of one matrix, ``Aᵀ diag(d) B``. Three uses:

- the gradient of the likelihood at any η has the form ``Σ_q d_q x(q) -
  Σᵢ cᵢ x(tᵢ)`` (softplus link: ``d = w·expit(η)``, ``c = expit(η)/sp(η)``),
  so for every group at once it is ``Aᵀ diag(d) B - A_evᵀ diag(c) B_ev``
  entrywise: the KKT check over the whole dictionary costs one
  ``(C x M) · (M x (C+K+1))`` product (about 4 s for pointproc at 2,000
  events), not a pass over 10⁶ materialised groups;
- the column means ``Aᵀ diag(w) B / T`` and mean squares
  ``(A∘A)ᵀ diag(w) (B∘B) / T`` give every column's time variance, its
  standardisation;
- a group's columns are materialised only when it enters the working set.

These products use BLAS (numpy ``@``) and are deterministic run to run on one
machine, not exactly rounded: they decide which groups the solver looks at,
never a reported number (SPEC §4.1's numbers come from the certified refit).

**Standardisation.** Each column is divided by its standard deviation over
time on the training window (``s² = ∫x²/T - (∫x/T)²``), as glmnet standardises
by default. Centering is not needed: with the intercept unpenalised and at its
optimum, its derivative ``Σ_q d_q - Σᵢ cᵢ`` is zero, so a column's gradient is
unchanged by subtracting a constant. Under the null model (λ constant) the
Fisher
information of a standardised column is the same for every column, so the
penalty treats every column alike at the start of the path. A column with
time variance below ``VAR_REL_TOL`` of its mean square is constant on the
data (collinear with the intercept); its groups are dropped.

**Rows.** The training rows are those of :func:`features.quadrature_rule` for
one structure that contains every atom and condition of the alphabet, so the
rule resolves every kernel scale, period and phase switch of the dictionary
(the rule depends on the structure's ψ *grids*, not on ψ). Unit columns come
from :func:`features.leaf_columns`, the same code the fitter uses, and a group
column is the same elementwise product or ``np.where`` that
:class:`features.BlockCache` applies, so a group's columns equal its canonical
feature's design columns byte for byte (tested). Indicator columns are
recomputed here from ``grammar.py``'s semantics, exactly as ``features.py``
evaluates conditions (tested through the gate columns).
"""

from __future__ import annotations

import hashlib
import itertools
import math
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Final

import numpy as np
import numpy.typing as npt

from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import canonical_feature
from sciagent.glm.data import Dataset, EventLog, Floats
from sciagent.glm.features import (
    DEFAULT_QUADRATURE,
    LeafColumns,
    QuadratureSpec,
    leaf_columns,
    quadrature_rule,
)
from sciagent.glm.grammar import (
    ChannelSpec,
    Cond,
    Feature,
    Gate,
    LastMarkAbove,
    Link,
    PhaseWindow,
    Product,
    PsiSlot,
    Structure,
    Trend,
    n_columns,
    psi_slots,
)
from sciagent.glm.grids import grid
from sciagent.glm.space import Alphabet, Atom, alphabet
from sciagent.library.base import counted_mask

type Bools = npt.NDArray[np.bool_]
type Ints = npt.NDArray[np.intp]

#: A column whose time variance is below this fraction of its mean square is
#: constant on the data (collinear with the intercept) and is dropped.
VAR_REL_TOL: Final = 1e-10
#: Rows per BLAS block of the cross products (bounds the temporary to
#: ``_CHUNK x (C + K + 1)`` doubles, ~46 MB for pointproc).
_CHUNK: Final = 4096


class SparseError(SciAgentError):
    """B-sparse cannot run on this data or configuration."""


class GroupKind(Enum):
    ATOM = "atom"
    PRODUCT = "product"
    GATE = "gate"


@dataclass(frozen=True)
class Unit:
    """An atom at one ψ grid point; columns ``offset .. offset + width``."""

    atom: Atom
    psi: tuple[tuple[PsiSlot, float], ...]
    offset: int
    width: int


@dataclass(frozen=True)
class Indicator:
    """A gate condition at one ψ grid point (parameters by name)."""

    cond: Cond
    params: tuple[tuple[str, float], ...]


# --------------------------------------------------------------------------
# Enumeration
# --------------------------------------------------------------------------


def _grid_points(slots: tuple[PsiSlot, ...]) -> list[tuple[tuple[PsiSlot, float], ...]]:
    """Every grid point of ``slots``, in slot order, lexicographic in the grids."""
    grids = [grid(s.name) for s in slots]
    return [tuple(zip(slots, vals, strict=True)) for vals in itertools.product(*grids)]


def _units(atoms: Sequence[Atom]) -> list[Unit]:
    out: list[Unit] = []
    offset = 0
    for atom in atoms:
        width = n_columns(atom)
        for psi in _grid_points(psi_slots(atom)):
            out.append(Unit(atom, psi, offset, width))
            offset += width
    return out


def _indicators(conds: Sequence[Cond]) -> list[Indicator]:
    out: list[Indicator] = []
    for cond in conds:
        slots = tuple(s for s in psi_slots(Gate(Trend(), cond)) if s.path == (1,))
        for point in _grid_points(slots):
            out.append(Indicator(cond, tuple((s.name, v) for s, v in point)))
    return out


def rule_structure(alpha: Alphabet) -> Structure:
    """One structure holding every atom and condition of the alphabet.

    Used only to lay out the quadrature rule (:func:`features.quadrature_rule`
    reads the ψ grids of the nodes it contains and never validates the
    feature count), so one rule serves every group of the dictionary.
    """
    feats: list[Feature] = list(alpha.atoms)
    feats.extend(Gate(Trend(), c) for c in alpha.conds)
    return Structure(tuple(feats), Link.EXP)


def indicator_values(
    indicator: Indicator,
    log: EventLog,
    channels: tuple[ChannelSpec, ...],
    t: Floats,
) -> Bools:
    """Whether the condition holds at each ``t`` (``grammar.py`` semantics).

    ``LastMarkAbove``: the most recent event strictly before t has ``z > q``
    (false before the first event); ``PhaseWindow``: ``sin(2πt/P - φ) ≥ 0``,
    evaluated as ``features.py`` does (the fractional phase ≤ ½).
    """
    params = dict(indicator.params)
    match indicator.cond:
        case LastMarkAbove(channel=c):
            spec = {s.name: s for s in channels}[c]
            z = (log.marks[c] - spec.location) / spec.scale
            k = np.searchsorted(log.times, t, side="left")
            on = np.zeros(t.size, dtype=np.bool_)
            has = k > 0
            on[has] = z[k[has] - 1] > params["above_z"]
            return on
        case PhaseWindow():
            u = t / params["period"] - params["phase"] / (2.0 * math.pi)
            frac: Bools = (u - np.floor(u)) <= 0.5
            return frac


# --------------------------------------------------------------------------
# Leaf jobs (optionally in worker processes)
# --------------------------------------------------------------------------

type _LeafJob = tuple[Atom, dict[PsiSlot, float], Dataset, Floats]

_WORKER: dict[str, tuple[ChannelSpec, ...]] = {}


def _init_worker(channels: tuple[ChannelSpec, ...]) -> None:
    _WORKER["channels"] = channels


def _leaf_job(job: _LeafJob) -> LeafColumns:
    atom, psi, data, nodes = job
    return leaf_columns(atom, psi, data, _WORKER["channels"], nodes)


def _compute_leaves(
    jobs: list[_LeafJob], channels: tuple[ChannelSpec, ...], workers: int
) -> list[LeafColumns]:
    """Every job's leaf columns, in job order (workers change wall time only)."""
    if workers <= 1:
        return [leaf_columns(a, p, d, channels, n) for a, p, d, n in jobs]
    with ProcessPoolExecutor(
        workers, initializer=_init_worker, initargs=(channels,)
    ) as pool:
        return list(pool.map(_leaf_job, jobs, chunksize=8))


# --------------------------------------------------------------------------
# Rows of one side (training or validation)
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Side:
    datasets: tuple[Dataset, ...]
    rules: tuple[tuple[Floats, Floats], ...]
    rows: tuple[Ints, ...]  # counted events of each dataset
    weights: Floats  # all nodes, datasets concatenated
    indicators_ev: Floats  # (n, K)
    indicators_nd: Floats  # (M, K)

    @property
    def n_events(self) -> int:
        return sum(r.size for r in self.rows)


def _side(
    datasets: Sequence[Dataset],
    structure: Structure,
    indicators: Sequence[Indicator],
    channels: tuple[ChannelSpec, ...],
    quadrature: QuadratureSpec,
) -> _Side:
    rules = tuple(
        quadrature_rule(structure, d, quadrature=quadrature) for d in datasets
    )
    rows = tuple(np.flatnonzero(counted_mask(d)).astype(np.intp) for d in datasets)
    weights = np.concatenate([w for _, w in rules]) if rules else np.empty(0)
    k = len(indicators)
    ev_parts: list[Floats] = []
    nd_parts: list[Floats] = []
    for d, (nodes, _), r in zip(datasets, rules, rows, strict=True):
        times = d.log.times[r]
        ev = np.empty((times.size, k))
        nd = np.empty((nodes.size, k))
        for j, ind in enumerate(indicators):
            ev[:, j] = indicator_values(ind, d.log, channels, times)
            nd[:, j] = indicator_values(ind, d.log, channels, nodes)
        ev_parts.append(ev)
        nd_parts.append(nd)
    return _Side(
        tuple(datasets),
        rules,
        rows,
        weights,
        np.concatenate(ev_parts) if ev_parts else np.empty((0, k)),
        np.concatenate(nd_parts) if nd_parts else np.empty((0, k)),
    )


def _cross(
    mat: Floats, d: Floats, left: int, transform: Callable[[Floats], Floats] | None
) -> Floats:
    """``T(mat)[:, :left]ᵀ diag(d) T(mat)`` in row blocks (``T`` = ``transform``)."""
    out = np.zeros((left, mat.shape[1]))
    for start in range(0, mat.shape[0], _CHUNK):
        block = mat[start : start + _CHUNK]
        if transform is not None:
            block = transform(block)
        out += (block[:, :left] * d[start : start + _CHUNK, None]).T @ block
    return out


# --------------------------------------------------------------------------
# The dictionary
# --------------------------------------------------------------------------


class GroupDictionary:
    """The depth-≤2 group dictionary on a training and a validation side.

    Group ids are flat indices ``u · S + s`` into a ``U x S`` table with
    ``S = U + K + 1``: slot ``s < U`` is the product with unit ``s`` (only
    ``s ≥ u`` is a group), ``U ≤ s < U + K`` the gate with indicator ``s - U``,
    and ``s = U + K`` the atom alone. :attr:`valid` marks the groups that
    exist; :meth:`group_ids` lists them in increasing id order.
    """

    def __init__(
        self,
        units: list[Unit],
        indicators: list[Indicator],
        train: _Side,
        validation: _Side,
        ev: Floats,
        nd: Floats,
        channels: tuple[ChannelSpec, ...],
    ) -> None:
        self.units: Final = tuple(units)
        self.indicators: Final = tuple(indicators)
        self._train = train
        self._val = validation
        self._ev = ev
        self._nd = nd
        self._channels = channels
        self._val_cache: dict[int, tuple[Floats, Floats]] = {}
        self._features: dict[int, tuple[Feature, dict[PsiSlot, float]]] = {}
        u, k = len(units), len(indicators)
        c = units[-1].offset + units[-1].width if units else 0
        self._c, self._k, self._u = c, k, u
        self._s = u + k + 1
        self.observed_time: Final = math.fsum(train.weights.tolist())
        self.n_events: Final = train.n_events
        # Duplicate units: identical columns at every training row.
        self.duplicate: Final = self._duplicates()
        # Standardisation.
        w = train.weights
        mean = _cross(nd, w, c, None) / self.observed_time
        msq = _cross(nd, w, c, self._square) / self.observed_time
        var = msq - mean * mean
        col_valid = (msq > 0.0) & (var > VAR_REL_TOL * msq)
        self._col_valid: Final = col_valid
        self._scale: Final = np.where(col_valid, np.sqrt(np.maximum(var, 0.0)), 1.0)
        # Group table.
        self._row_offsets = np.array([x.offset for x in units], dtype=np.intp)
        self._slot_offsets = np.concatenate(
            [self._row_offsets, c + np.arange(k + 1, dtype=np.intp)]
        )
        bad = self._reduce((~col_valid).astype(np.float64)) > 0.0
        table = ~bad
        upper = np.triu(np.ones((u, u), dtype=np.bool_))
        table[:, :u] &= upper
        dup = self.duplicate
        table[dup, :] = False
        table[:, :u][:, dup] = False
        self._valid: Final = table.ravel()
        widths_row = np.array([x.width for x in units], dtype=np.float64)
        widths_slot = np.concatenate([widths_row, np.ones(k + 1)])
        self._width: Final = np.outer(widths_row, widths_slot).ravel()
        self._omega: Final = np.sqrt(self._width)

    # ---- construction -----------------------------------------------------

    @staticmethod
    def build(
        train: Sequence[Dataset],
        validation: Sequence[Dataset],
        channels: tuple[ChannelSpec, ...],
        *,
        alphabet: Alphabet | None = None,
        quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
        workers: int = 1,
    ) -> GroupDictionary:
        """Compute every unit's training columns and the cross products.

        ``alphabet`` defaults to the full alphabet of ``channels``
        (:func:`~sciagent.glm.space.alphabet`); tests pass reduced ones.
        Validation columns are computed lazily, only for groups that need them.
        """
        alpha = alphabet if alphabet is not None else _full_alphabet(channels)
        if not alpha.atoms:
            raise SparseError("the alphabet has no atoms")
        units = _units(alpha.atoms)
        indicators = _indicators(alpha.conds)
        structure = rule_structure(alpha)
        tr = _side(train, structure, indicators, channels, quadrature)
        va = _side(validation, structure, indicators, channels, quadrature)
        if tr.n_events == 0:
            raise SparseError("no counted events in the training window")
        c = units[-1].offset + units[-1].width
        k = len(indicators)
        width = c + k + 1
        n, m = tr.n_events, tr.weights.size
        ev = np.empty((n, width))
        nd = np.empty((m, width))
        jobs: list[_LeafJob] = []
        for unit in units:
            for d, (nodes, _) in zip(tr.datasets, tr.rules, strict=True):
                jobs.append((unit.atom, dict(unit.psi), d, nodes))
        leaves = iter(_compute_leaves(jobs, channels, workers))
        for unit in units:
            r0 = q0 = 0
            cols = slice(unit.offset, unit.offset + unit.width)
            for rows, (nodes, _) in zip(tr.rows, tr.rules, strict=True):
                leaf = next(leaves)
                ev[r0 : r0 + rows.size, cols] = leaf.at_events
                nd[q0 : q0 + nodes.size, cols] = leaf.at_nodes
                r0 += rows.size
                q0 += nodes.size
        ev[:, c : c + k] = tr.indicators_ev
        nd[:, c : c + k] = tr.indicators_nd
        ev[:, c + k] = 1.0
        nd[:, c + k] = 1.0
        return GroupDictionary(units, indicators, tr, va, ev, nd, channels)

    def _square(self, block: Floats) -> Floats:
        out = block.copy()
        out[:, : self._c] *= block[:, : self._c]
        return out

    def _duplicates(self) -> Bools:
        dup = np.zeros(len(self.units), dtype=np.bool_)
        seen: dict[bytes, int] = {}
        for i, unit in enumerate(self.units):
            cols = slice(unit.offset, unit.offset + unit.width)
            ev = np.ascontiguousarray(self._ev[:, cols])
            nd = np.ascontiguousarray(self._nd[:, cols])
            key = hashlib.sha256(ev.tobytes() + nd.tobytes()).digest()
            first = seen.get(key)
            if first is None:
                seen[key] = i
                continue
            other = self.units[first]
            ocols = slice(other.offset, other.offset + other.width)
            if np.array_equal(self._ev[:, ocols], ev) and np.array_equal(
                self._nd[:, ocols], nd
            ):
                dup[i] = True
        return dup

    def _reduce(self, col_matrix: Floats) -> Floats:
        """Sum a ``C x (C+K+1)`` column matrix into the ``U x S`` group table."""
        rows = np.add.reduceat(col_matrix, self._row_offsets, axis=0)
        out: Floats = np.add.reduceat(rows, self._slot_offsets, axis=1)
        return out

    # ---- sizes ------------------------------------------------------------

    @property
    def n_units(self) -> int:
        return self._u

    @property
    def n_indicators(self) -> int:
        return self._k

    @property
    def n_unit_columns(self) -> int:
        return self._c

    @property
    def n_slots_total(self) -> int:
        """Size of the flat group table (``U · S``), valid or not."""
        return self._u * self._s

    @property
    def n_duplicate_units(self) -> int:
        return int(np.count_nonzero(self.duplicate))

    @property
    def n_groups(self) -> int:
        return int(np.count_nonzero(self._valid))

    @property
    def n_columns(self) -> int:
        """Columns of the (never materialised) design: Σ group widths."""
        return int(np.sum(self._width[self._valid]))

    @property
    def valid(self) -> Bools:
        return self._valid

    @property
    def omega(self) -> Floats:
        """Penalty weight of each group: √(group width)."""
        return self._omega

    @property
    def node_weights(self) -> Floats:
        return self._train.weights

    @property
    def train_datasets(self) -> tuple[Dataset, ...]:
        return self._train.datasets

    @property
    def train_rules(self) -> tuple[tuple[Floats, Floats], ...]:
        return self._train.rules

    @property
    def validation_weights(self) -> Floats:
        return self._val.weights

    @property
    def validation_datasets(self) -> tuple[Dataset, ...]:
        return self._val.datasets

    @property
    def n_validation_events(self) -> int:
        return self._val.n_events

    def group_ids(self) -> list[int]:
        return [int(g) for g in np.flatnonzero(self._valid)]

    # ---- one group ----------------------------------------------------------

    def _split(self, g: int) -> tuple[int, int]:
        return divmod(g, self._s)

    def group_kind(self, g: int) -> GroupKind:
        _, s = self._split(g)
        if s < self._u:
            return GroupKind.PRODUCT
        if s < self._u + self._k:
            return GroupKind.GATE
        return GroupKind.ATOM

    def group_width(self, g: int) -> int:
        return int(self._width[g])

    def _pairs(self, g: int) -> list[tuple[int, int]]:
        """(unit column, extended column) of each group column, row-major."""
        u, s = self._split(g)
        unit = self.units[u]
        rows = range(unit.offset, unit.offset + unit.width)
        if s < self._u:
            other = self.units[s]
            cols: range | list[int] = range(other.offset, other.offset + other.width)
        else:
            cols = [self._c + (s - self._u)]
        return [(i, j) for i in rows for j in cols]

    def _combine(self, a: Floats, b: Floats, j: int) -> Floats:
        if j < self._c:
            return a * b
        if j < self._c + self._k:
            out: Floats = np.where(b > 0.5, a, 0.0)
            return out
        return a.copy()

    def train_columns(self, g: int, *, scaled: bool) -> tuple[Floats, Floats]:
        """The group's columns at the training events and nodes, ``(n, w)``
        and ``(M, w)``; divided by their standard deviations if ``scaled``."""
        pairs = self._pairs(g)
        ev = np.empty((self._ev.shape[0], len(pairs)))
        nd = np.empty((self._nd.shape[0], len(pairs)))
        for col, (i, j) in enumerate(pairs):
            ev[:, col] = self._combine(self._ev[:, i], self._ev[:, j], j)
            nd[:, col] = self._combine(self._nd[:, i], self._nd[:, j], j)
            if scaled:
                ev[:, col] /= self._scale[i, j]
                nd[:, col] /= self._scale[i, j]
        return ev, nd

    def validation_columns(self, g: int) -> tuple[Floats, Floats]:
        """The group's raw columns at the validation events and nodes."""
        u, s = self._split(g)
        a_ev, a_nd = self._val_unit(u)
        if s < self._u:
            b_ev, b_nd = self._val_unit(s)
            j = 0
        elif s < self._u + self._k:
            col = s - self._u
            b_ev = self._val.indicators_ev[:, col : col + 1]
            b_nd = self._val.indicators_nd[:, col : col + 1]
            j = self._c
        else:
            return a_ev.copy(), a_nd.copy()
        ev = np.empty((a_ev.shape[0], a_ev.shape[1] * b_ev.shape[1]))
        nd = np.empty((a_nd.shape[0], ev.shape[1]))
        col = 0
        for p in range(a_ev.shape[1]):
            for q in range(b_ev.shape[1]):
                ev[:, col] = self._combine(a_ev[:, p], b_ev[:, q], j)
                nd[:, col] = self._combine(a_nd[:, p], b_nd[:, q], j)
                col += 1
        return ev, nd

    def _val_unit(self, u: int) -> tuple[Floats, Floats]:
        cached = self._val_cache.get(u)
        if cached is not None:
            return cached
        unit = self.units[u]
        evs: list[Floats] = []
        nds: list[Floats] = []
        for d, (nodes, _) in zip(self._val.datasets, self._val.rules, strict=True):
            leaf = leaf_columns(unit.atom, dict(unit.psi), d, self._channels, nodes)
            evs.append(leaf.at_events)
            nds.append(leaf.at_nodes)
        out = (
            np.concatenate(evs) if evs else np.empty((0, unit.width)),
            np.concatenate(nds) if nds else np.empty((0, unit.width)),
        )
        self._val_cache[u] = out
        return out

    def feature(self, g: int) -> tuple[Feature, dict[PsiSlot, float]]:
        """The group's canonical feature and its ψ (rooted at the feature)."""
        cached = self._features.get(g)
        if cached is not None:
            return cached
        u, s = self._split(g)
        unit = self.units[u]
        out: tuple[Feature, dict[PsiSlot, float]]
        if s < self._u:
            other = self.units[s]
            canon = canonical_feature(Product(unit.atom, other.atom))
            left, right = (
                (unit, other)
                if canon == Product(unit.atom, other.atom)
                else (other, unit)
            )
            psi = {PsiSlot((0, *p.path), p.name): v for p, v in left.psi}
            psi.update({PsiSlot((1, *p.path), p.name): v for p, v in right.psi})
            out = (canon, psi)
        elif s < self._u + self._k:
            ind = self.indicators[s - self._u]
            canon = canonical_feature(Gate(unit.atom, ind.cond))
            psi = {PsiSlot((0, *p.path), p.name): v for p, v in unit.psi}
            psi.update({PsiSlot((1,), name): v for name, v in ind.params})
            out = (canon, psi)
        else:
            out = (unit.atom, dict(unit.psi))
        self._features[g] = out
        return out

    # ---- every group at once -------------------------------------------------

    def group_norms(self, node_d: Floats, event_c: Floats) -> Floats:
        """‖∇_g P‖ of every group (flat, 0 where invalid) for standardised
        columns, for a gradient of the form ``Σ_q d_q x̃(q) - Σᵢ cᵢ x̃(tᵢ)``
        (node weights ``d`` on the training rule, event weights ``c`` on the
        counted training events), from one cross product."""
        if node_d.shape != self._train.weights.shape or event_c.shape != (
            self.n_events,
        ):
            raise SparseError("weights do not match the training rows")
        ev = self._ev
        node = _cross(self._nd, node_d, self._c, None)
        event = (ev[:, : self._c] * event_c[:, None]).T @ ev
        grad = (node - event) / self._scale
        grad[~self._col_valid] = 0.0
        sq = self._reduce(grad * grad).ravel()
        out = np.sqrt(sq)
        out[~self._valid] = 0.0
        return out


def _full_alphabet(channels: tuple[ChannelSpec, ...]) -> Alphabet:
    return alphabet(channels)
