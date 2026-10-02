"""The search-based comparators: B-rand, B-sym and the planted-hint control
(SPEC §4.1, §6.4, §9).

All three propose structures and have them fitted by the certified fitter
(:func:`~sciagent.glm.fit.fit`), under the same budget rule as the agent's
``fit`` tool (``sciagent.investigation.tools``): **a fit is charged once per
distinct canonical structure**. A proposal whose
:func:`~sciagent.glm.canonical.structure_hash` was already fitted in this run
costs nothing and is never refitted. Every structure is canonicalised before it
is fitted, as ``fit_cache_key`` requires. The data never change within a run,
so the structure hash is the run's dedupe key. The optional ``memo``, keyed by
:func:`~sciagent.glm.fit.fit_cache_key`, shares fitted results *across* runs
(B-sym at F and at 10 F on one dataset, say). It saves compute only and never
changes what a run is charged.

**Selection** is by BIC, as in B-lib. A fit that raises
:class:`~sciagent.glm.fit.FitError` or is uncertified still counts as a fit
used, but it is never selected (SPEC §2.2) and is listed in ``skipped``.
``trajectory`` has one point per fit, in fit order, from the first fit that
produced a selectable model, so the scorer can draw the efficiency curve
(SPEC §4.3 item 4) exactly as for B-lib.

**B-rand** draws structures from a :class:`StructureSampler` (the truth prior's
structure distribution, SPEC §4.1) until it holds ``budget`` distinct ones, or
``max_draws_factor · budget`` draws have been made, and fits them all. It
reads no fit result while proposing, so it is a proposal-free control.

**B-sym** is a (μ + λ) genetic program over canonical structures (Koza-style
tree GP with tournament selection, as in PySR's evolutionary core), with BIC
as the fitness. It is the serious comparator, so it must not be a strawman
(SPEC §9):

1. Generation 0 is the ``seeds``, normally the grammar members of the library.
   That is fair, because the agent also knows the library.
2. Each generation proposes ``offspring`` structures that have not been fitted
   yet. A parent is chosen by a tournament of ``tournament`` draws (with
   replacement) from the population. With probability ``crossover_prob`` a
   child is the crossover of two parents (a random subset of their pooled
   features and one parent's link). Otherwise it is one :class:`Mutation` of
   one parent, the operator drawn by ``mutation_weights``. A child that is
   invalid, identical to its parent or already fitted is discarded and
   redrawn, up to ``max_attempts`` per wanted child.
3. The generation is fitted as a batch (in parallel when ``workers > 1``;
   results are kept in proposal order, so the run is byte-identical for every
   worker count). The new population is the best ``population`` certified
   structures fitted so far. Ties go to the earlier fit.
4. It stops when the budget is spent, or when a whole generation of attempts
   finds nothing new.

The hyperparameters live in :class:`GPConfig`. **Its defaults are untuned.**
SPEC §9 requires them to be tuned on the dev truths in P3, with the same
effort as the agent prompt, and frozen before the test campaign (SPEC §6.1).
The 10 F positive control (SPEC §6.4) is B-sym with ``budget = 10 · F``.

**Planted-hint** is the SPEC §6.4 control: "scripted to propose the truth's
top-level production first". It receives the truth **only through its
constructor**, as ORACLE does, so the truth is visible at the call site. It
fits :func:`hint` of the truth first, then runs B-sym with the hint added to
the seeds. It is a control, never a system under test.

Every random choice draws from one ``np.random.default_rng(seed)`` in a fixed
order (invariant 3). Domain-independent: channels come from the data, and the
alphabet from :func:`~sciagent.glm.space.alphabet`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from typing import Final, Protocol

import numpy as np

from sciagent.glm.canonical import canonical_feature, canonicalise, structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, FitError, FitResult, fit, fit_cache_key
from sciagent.glm.grammar import (
    ALL,
    MAX_DEPTH,
    MAX_FEATURES,
    ChannelSpec,
    Cond,
    Excite,
    Feature,
    Gate,
    InvalidStructureError,
    KernelKind,
    Link,
    One,
    Periodic,
    Product,
    Structure,
    Trend,
    depth,
    validate,
)
from sciagent.glm.space import Alphabet, alphabet, feasible_shapes
from sciagent.glm.syntax import render
from sciagent.scenarios.prior import StructurePrior, sample_structure
from sciagent.systems.v2.models import FittedModel, GLMModel
from sciagent.systems.v2.systems import (
    InvestigationData,
    SystemResult,
    SystemRunError,
    TrajectoryPoint,
)

B_RAND: Final = "B-rand"
B_SYM: Final = "B-sym"
PLANTED_HINT: Final = "planted-hint"

type Atom = Excite | Periodic | Trend
type Path = tuple[int, ...]
#: The fitting function: ``fit`` with a positional config. It must be a
#: module-level function when ``workers > 1``, so worker processes can import it.
type Fitter = Callable[
    [Structure, Sequence[Dataset], tuple[ChannelSpec, ...], FitConfig | None],
    FitResult,
]


class SearchError(SystemRunError):
    """A search system is misconfigured."""


def certified_fit(
    structure: Structure,
    datasets: Sequence[Dataset],
    channels: tuple[ChannelSpec, ...],
    config: FitConfig | None,
) -> FitResult:
    """The default :data:`Fitter`: the certified fitter."""
    return fit(structure, datasets, channels, config=config)


# --------------------------------------------------------------------------
# Structure priors (B-rand's proposal distribution)
# --------------------------------------------------------------------------


class StructureSampler(Protocol):
    """A distribution over structures; draws only from ``rng``."""

    def sample(self, rng: np.random.Generator) -> Structure: ...


@cache
def _alphabet(channels: tuple[ChannelSpec, ...]) -> Alphabet:
    return alphabet(channels)


def _assemble(atoms: Sequence[Atom], conds: Sequence[Cond]) -> Feature:
    tree: Feature = atoms[0]
    for atom in atoms[1:]:
        tree = Product(tree, atom)
    for cond in conds:
        tree = Gate(tree, cond)
    return canonical_feature(tree)


@dataclass(frozen=True)
class UniformShapePrior:
    """A simple structure prior, uniform at every level of the canonical form.

    The link is uniform over ``links``, the number of features uniform over
    ``1..max_features``. Each feature's ``(n_atoms, n_gates)`` shape is
    uniform over :func:`~sciagent.glm.space.feasible_shapes` at
    ``max_depth``, and its atoms and conditions are uniform over the
    channels' alphabet. The fallback when no truth prior is injected; prefer
    :class:`ScenarioPrior`, which is the truth prior SPEC §4.1 names.
    """

    channels: tuple[ChannelSpec, ...]
    max_features: int = 2
    max_depth: int = MAX_DEPTH
    links: tuple[Link, ...] = tuple(Link)

    def __post_init__(self) -> None:
        if not 1 <= self.max_features <= MAX_FEATURES:
            raise SearchError(f"max_features {self.max_features} outside 1..4")
        if not 1 <= self.max_depth <= MAX_DEPTH:
            raise SearchError(f"max_depth {self.max_depth} outside 1..{MAX_DEPTH}")
        if not self.links:
            raise SearchError("need at least one link")

    def sample(self, rng: np.random.Generator) -> Structure:
        alpha = _alphabet(self.channels)
        shapes = feasible_shapes(self.max_depth)
        link = self.links[int(rng.integers(len(self.links)))]
        k = 1 + int(rng.integers(self.max_features))
        features: list[Feature] = []
        for _ in range(k):
            n_atoms, n_gates = shapes[int(rng.integers(len(shapes)))]
            if n_gates and not alpha.conds:
                n_gates = 0
            atoms = [
                alpha.atoms[int(rng.integers(len(alpha.atoms)))] for _ in range(n_atoms)
            ]
            conds = [
                alpha.conds[int(rng.integers(len(alpha.conds)))] for _ in range(n_gates)
            ]
            features.append(_assemble(atoms, conds))
        return canonicalise(Structure(tuple(features), link))


@dataclass(frozen=True)
class ScenarioPrior:
    """The truth sampler's structure prior (``sciagent.scenarios.prior``).

    The sampler decides in-/out-of-dictionary by stratification; a single
    draw here takes out-of-dictionary with probability
    ``out_of_dictionary_share`` (one uniform), which is the same marginal.
    """

    prior: StructurePrior
    channels: tuple[ChannelSpec, ...]
    out_of_dictionary_share: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.out_of_dictionary_share <= 1.0:
            raise SearchError("out_of_dictionary_share must lie in [0, 1]")

    def sample(self, rng: np.random.Generator) -> Structure:
        ood = bool(rng.random() < self.out_of_dictionary_share)
        return sample_structure(self.prior, self.channels, rng, out_of_dictionary=ood)


# --------------------------------------------------------------------------
# Tree surgery
# --------------------------------------------------------------------------


def _paths(feature: Feature, path: Path = ()) -> Iterator[tuple[Path, Feature]]:
    """Every feature node with its path, pre-order (Gate's child is 0)."""
    yield path, feature
    match feature:
        case Product(left=left, right=right):
            yield from _paths(left, (*path, 0))
            yield from _paths(right, (*path, 1))
        case Gate(feature=inner):
            yield from _paths(inner, (*path, 0))
        case Excite() | Periodic() | Trend():
            return


def _replace(feature: Feature, path: Path, new: Feature) -> Feature:
    if not path:
        return new
    head, rest = path[0], path[1:]
    match feature:
        case Product(left=left, right=right):
            if head == 0:
                return Product(_replace(left, rest, new), right)
            return Product(left, _replace(right, rest, new))
        case Gate(feature=inner, cond=cond):
            return Gate(_replace(inner, rest, new), cond)
        case Excite() | Periodic() | Trend():
            raise SearchError(f"path {path} runs past a leaf")


# --------------------------------------------------------------------------
# Variation operators
# --------------------------------------------------------------------------


class Mutation(Enum):
    """The AST mutation operators of B-sym."""

    ADD_FEATURE = "add_feature"  # append a random atom as a new feature
    REMOVE_FEATURE = "remove_feature"
    REPLACE_ATOM = "replace_atom"  # any atom → a random atom
    SWAP_KERNEL = "swap_kernel"  # an Excite's kernel
    CHANGE_MARK = "change_mark"  # an Excite's mark function
    CHANGE_SOURCE = "change_source"  # an Excite's source
    CHANGE_COND = "change_cond"  # a Gate's condition
    WRAP_GATE = "wrap_gate"  # f → Gate(f, cond)
    UNWRAP = "unwrap"  # Gate(f, c) → f; Product(a, b) → a or b
    MULTIPLY = "multiply"  # f → Product(f, Periodic or an Excite)
    CHANGE_LINK = "change_link"


def _pick[T](items: Sequence[T], rng: np.random.Generator) -> T:
    return items[int(rng.integers(len(items)))]


def _excite_variants(atom: Excite, alpha: Alphabet, op: Mutation) -> list[Excite]:
    """Valid atoms differing from ``atom`` in exactly the attribute ``op`` names."""
    out: list[Excite] = []
    for a in alpha.atoms:
        if not isinstance(a, Excite) or a == atom:
            continue
        same_kernel = a.kernel is atom.kernel
        same_mark = a.mark == atom.mark
        same_source = a.source == atom.source
        if (
            (op is Mutation.SWAP_KERNEL and same_mark and same_source)
            or (op is Mutation.CHANGE_MARK and same_kernel and same_source)
            or (op is Mutation.CHANGE_SOURCE and same_kernel and same_mark)
        ):
            out.append(a)
    return out


def _finish(
    parent: Structure, features: Sequence[Feature], link: Link
) -> Structure | None:
    """Canonicalise; None if invalid (depth, size) or equivalent to the parent."""
    child = canonicalise(Structure(tuple(features), link))
    try:
        _validate_shape(child)
    except InvalidStructureError:
        return None
    if structure_hash(child) == structure_hash(parent):
        return None
    return child


def _validate_shape(structure: Structure) -> None:
    """Depth and size checks; atoms and conditions come from the alphabet,
    so they are valid for the channels by construction."""
    if len(structure.features) > MAX_FEATURES:
        raise InvalidStructureError("too many features")
    for f in structure.features:
        if depth(f) > MAX_DEPTH:
            raise InvalidStructureError("too deep")


def mutate(
    parent: Structure,
    op: Mutation,
    alpha: Alphabet,
    rng: np.random.Generator,
    max_features: int,
) -> Structure | None:
    """One mutation of a canonical structure, or None if ``op`` does not apply
    or yields an invalid structure or one equivalent to ``parent``.

    The target (feature, node) is drawn uniformly from the nodes the operator
    applies to, in pre-order over the canonical feature tuple.
    """
    feats = list(parent.features)
    link = parent.link
    match op:
        case Mutation.ADD_FEATURE:
            if len(feats) >= max_features:
                return None
            feats.append(_pick(alpha.atoms, rng))
        case Mutation.REMOVE_FEATURE:
            if not feats:
                return None
            del feats[int(rng.integers(len(feats)))]
        case Mutation.CHANGE_LINK:
            link = _pick([lk for lk in Link if lk is not link], rng)
        case Mutation.WRAP_GATE | Mutation.MULTIPLY:
            if not feats or (op is Mutation.WRAP_GATE and not alpha.conds):
                return None
            i = int(rng.integers(len(feats)))
            if op is Mutation.WRAP_GATE:
                feats[i] = Gate(feats[i], _pick(alpha.conds, rng))
            else:
                excites = [a for a in alpha.atoms if isinstance(a, Excite)]
                other: Feature = (
                    Periodic()
                    if not excites or rng.random() < 0.5
                    else _pick(excites, rng)
                )
                feats[i] = Product(feats[i], other)
        case _:
            targets = _targets(feats, op, alpha)
            if not targets:
                return None
            i, path, choices = _pick(targets, rng)
            feats[i] = _replace(feats[i], path, _pick(choices, rng))
    if len(feats) > max_features:
        return None
    return _finish(parent, feats, link)


def _targets(
    feats: Sequence[Feature], op: Mutation, alpha: Alphabet
) -> list[tuple[int, Path, list[Feature]]]:
    """(feature index, node path, replacements) for the node-level operators."""
    out: list[tuple[int, Path, list[Feature]]] = []
    for i, f in enumerate(feats):
        for path, node in _paths(f):
            choices: list[Feature] = []
            match op, node:
                case Mutation.REPLACE_ATOM, Excite() | Periodic() | Trend():
                    choices = [a for a in alpha.atoms if a != node]
                case (
                    Mutation.SWAP_KERNEL
                    | Mutation.CHANGE_MARK
                    | Mutation.CHANGE_SOURCE,
                    Excite(),
                ):
                    choices = list(_excite_variants(node, alpha, op))
                case Mutation.CHANGE_COND, Gate(feature=inner, cond=cond):
                    choices = [Gate(inner, c) for c in alpha.conds if c != cond]
                case Mutation.UNWRAP, Gate(feature=inner):
                    choices = [inner]
                case Mutation.UNWRAP, Product(left=left, right=right):
                    choices = [left, right]
                case _:
                    pass
            if choices:
                out.append((i, path, choices))
    return out


def crossover(
    a: Structure, b: Structure, rng: np.random.Generator, max_features: int
) -> Structure | None:
    """A child of two structures: a random subset of their pooled features.

    The pool is ``a``'s features then ``b``'s; the child takes the first k of
    a random permutation, k uniform on ``1..min(|pool|, max_features)``, and
    the link of ``a`` or ``b`` with probability ½ each. None when the pool is
    empty or the child equals ``a``.
    """
    pool = [*a.features, *b.features]
    if not pool:
        return None
    k = 1 + int(rng.integers(min(len(pool), max_features)))
    order = rng.permutation(len(pool))
    link = a.link if rng.random() < 0.5 else b.link
    feats = [pool[int(j)] for j in order[:k]]
    return _finish(a, feats, link)


# --------------------------------------------------------------------------
# The planted hint
# --------------------------------------------------------------------------

_DEFAULT_EXCITE: Final = Excite(KernelKind.EXP, One(), ALL)


def _default_atom(feature: Feature) -> Atom:
    """The leftmost atom of a tree, with an Excite reduced to its default."""
    match feature:
        case Excite():
            return _DEFAULT_EXCITE
        case Periodic() | Trend():
            return feature
        case Product(left=left):
            return _default_atom(left)
        case Gate(feature=inner):
            return _default_atom(inner)


def _hint_feature(feature: Feature) -> Feature:
    match feature:
        case Excite() | Periodic() | Trend():
            return _default_atom(feature)
        case Product(left=left, right=right):
            return Product(_default_atom(left), _default_atom(right))
        case Gate(feature=inner, cond=cond):
            return Gate(_default_atom(inner), cond)


def hint(truth: Structure) -> Structure:
    """The truth's top-level productions with default children (SPEC §6.4).

    Of the truth's canonical form: the link is kept, and each feature keeps its
    root node type. An ``Excite`` becomes ``Excite(ExpK, One, all)``;
    ``Periodic`` and ``Trend`` stay. A ``Product`` keeps two children, each
    reduced to its leftmost atom made default. A ``Gate`` keeps its condition,
    and its child is reduced the same way. So the hint names which
    productions to use, never the kernels, mark functions or sources.
    """
    canon = canonicalise(truth)
    return canonicalise(
        Structure(tuple(_hint_feature(f) for f in canon.features), canon.link)
    )


# --------------------------------------------------------------------------
# The fit ledger: budget accounting shared by every search
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Record:
    number: int
    key: str
    structure: Structure
    name: str
    result: FitResult | None

    @property
    def criterion(self) -> float:
        r = self.result
        if r is None or not r.certified or not math.isfinite(r.bic):
            return math.inf
        return r.bic


def _fit_job(
    fitter: Fitter,
    structure: Structure,
    datasets: tuple[Dataset, ...],
    channels: tuple[ChannelSpec, ...],
    config: FitConfig | None,
) -> FitResult | None:
    try:
        return fitter(structure, datasets, channels, config)
    except FitError:
        return None


@contextmanager
def _pool(workers: int) -> Iterator[ProcessPoolExecutor | None]:
    """Worker processes for fitting, or None for in-process fitting.

    Each worker keeps its own feature-block cache, so memory grows with
    ``workers``: a 2,000-event run with 6 workers ran out of memory on a
    loaded 16 GB machine (``BrokenProcessPool`` / ``MemoryError``).
    """
    if workers <= 1:
        yield None
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield pool


class _Ledger:
    """Fits distinct canonical structures, in order, up to the budget."""

    def __init__(
        self,
        data: InvestigationData,
        budget: int,
        settings: _Settings,
        pool: ProcessPoolExecutor | None,
    ) -> None:
        self.data = data
        self.budget = budget
        self.settings = settings
        self.pool = pool
        self.records: list[_Record] = []
        self.seen: set[str] = set()

    @property
    def remaining(self) -> int:
        return self.budget - len(self.records)

    def evaluate(self, proposals: Sequence[Structure]) -> None:
        """Fit the novel proposals, first come first fitted, within budget."""
        batch: list[tuple[str, Structure]] = []
        pending: set[str] = set()
        for s in proposals:
            if len(batch) >= self.remaining:
                break
            canon = canonicalise(s)
            key = structure_hash(canon)
            if key in self.seen or key in pending:
                continue
            batch.append((key, canon))
            pending.add(key)
        results = self._fit([s for _, s in batch])
        for (key, canon), result in zip(batch, results, strict=True):
            self.seen.add(key)
            self.records.append(
                _Record(len(self.records) + 1, key, canon, render(canon), result)
            )

    def _fit(self, structures: list[Structure]) -> list[FitResult | None]:
        s = self.settings
        obs = self.data.observational
        channels = self.data.channels
        memo = s.memo
        keys = [
            fit_cache_key(x, obs, channels, s.fit_config) if memo is not None else ""
            for x in structures
        ]
        todo = [i for i, k in enumerate(keys) if memo is None or k not in memo]
        jobs = [(s.fitter, structures[i], obs, channels, s.fit_config) for i in todo]
        if self.pool is not None and len(jobs) > 1:
            fitted = list(self.pool.map(_fit_job, *zip(*jobs, strict=True)))
        else:
            fitted = [_fit_job(*job) for job in jobs]
        out: list[FitResult | None] = [None] * len(structures)
        done = dict(zip(todo, fitted, strict=True))
        for i, k in enumerate(keys):
            if i in done:
                out[i] = done[i]
                fitted_i = done[i]
                if memo is not None and fitted_i is not None:
                    memo[k] = fitted_i
            elif memo is not None:
                out[i] = memo[k]
        return out

    def result(self, system: str) -> SystemResult:
        best: tuple[_Record, FittedModel] | None = None
        trajectory: list[TrajectoryPoint] = []
        skipped: list[str] = []
        for rec in self.records:
            crit = rec.criterion
            if math.isinf(crit):
                skipped.append(rec.name)
            elif best is None or crit < best[0].criterion:
                assert rec.result is not None
                model = GLMModel(
                    rec.name, rec.result, self.data.channels, self.data.marks
                )
                best = (rec, model)
            if best is not None:
                trajectory.append(
                    TrajectoryPoint(
                        rec.number,
                        rec.name,
                        crit,
                        best[0].name,
                        best[0].criterion,
                        best[1],
                    )
                )
        if best is None:
            raise SystemRunError(f"{system}: no proposal produced a certified fit")
        return SystemResult(
            system=system,
            submitted=best[0].name,
            structure=best[0].structure,
            model=best[1],
            fits_used=len(self.records),
            trajectory=tuple(trajectory),
            skipped=tuple(skipped),
        )


@dataclass(frozen=True)
class _Settings:
    fit_config: FitConfig | None
    fitter: Fitter
    memo: dict[str, FitResult] | None


def _check_common(budget: int, workers: int) -> None:
    if budget < 1:
        raise SearchError(f"budget must be ≥ 1: {budget}")
    if workers < 1:
        raise SearchError(f"workers must be ≥ 1: {workers}")


# --------------------------------------------------------------------------
# B-rand
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BRand:
    """F distinct structures from ``prior``, best by BIC (module docstring)."""

    prior: StructureSampler
    budget: int = 40
    seed: int = 0
    workers: int = 1
    max_draws_factor: int = 100
    fit_config: FitConfig | None = None
    fitter: Fitter = certified_fit
    memo: dict[str, FitResult] | None = field(default=None, compare=False, hash=False)
    name: str = B_RAND

    def __post_init__(self) -> None:
        _check_common(self.budget, self.workers)
        if self.max_draws_factor < 1:
            raise SearchError("max_draws_factor must be ≥ 1")

    def run(self, data: InvestigationData) -> SystemResult:
        rng = np.random.default_rng(self.seed)
        proposals: list[Structure] = []
        seen: set[str] = set()
        for _ in range(self.max_draws_factor * self.budget):
            if len(proposals) >= self.budget:
                break
            s = canonicalise(self.prior.sample(rng))
            validate(s, data.channels)
            key = structure_hash(s)
            if key not in seen:
                seen.add(key)
                proposals.append(s)
        settings = _Settings(self.fit_config, self.fitter, self.memo)
        with _pool(self.workers) as pool:
            ledger = _Ledger(data, self.budget, settings, pool)
            ledger.evaluate(proposals)
        return ledger.result(self.name)


# --------------------------------------------------------------------------
# B-sym
# --------------------------------------------------------------------------

#: Untuned defaults (2026-10-02): tune on dev in P3, freeze before test.
DEFAULT_MUTATION_WEIGHTS: Final[tuple[tuple[Mutation, float], ...]] = (
    (Mutation.ADD_FEATURE, 2.0),
    (Mutation.REMOVE_FEATURE, 1.0),
    (Mutation.REPLACE_ATOM, 1.0),
    (Mutation.SWAP_KERNEL, 1.5),
    (Mutation.CHANGE_MARK, 2.0),
    (Mutation.CHANGE_SOURCE, 1.0),
    (Mutation.CHANGE_COND, 0.5),
    (Mutation.WRAP_GATE, 1.0),
    (Mutation.UNWRAP, 1.0),
    (Mutation.MULTIPLY, 1.0),
    (Mutation.CHANGE_LINK, 1.0),
)


@dataclass(frozen=True)
class GPConfig:
    """B-sym's hyperparameters (module docstring). Defaults are **untuned**:
    SPEC §9 requires tuning on dev in P3 and freezing before test."""

    population: int = 8
    offspring: int = 8
    tournament: int = 3
    crossover_prob: float = 0.2
    mutation_weights: tuple[tuple[Mutation, float], ...] = DEFAULT_MUTATION_WEIGHTS
    max_features: int = MAX_FEATURES
    max_attempts: int = 200

    def __post_init__(self) -> None:
        for name in ("population", "offspring", "tournament", "max_attempts"):
            if getattr(self, name) < 1:
                raise SearchError(f"{name} must be ≥ 1")
        if not 0.0 <= self.crossover_prob <= 1.0:
            raise SearchError("crossover_prob must lie in [0, 1]")
        if not 1 <= self.max_features <= MAX_FEATURES:
            raise SearchError(f"max_features must lie in 1..{MAX_FEATURES}")
        weights = [w for _, w in self.mutation_weights]
        if (
            any(w < 0.0 or not math.isfinite(w) for w in weights)
            or math.fsum(weights) <= 0
        ):
            raise SearchError("mutation weights must be finite, ≥ 0, not all 0")
        ops = [op for op, _ in self.mutation_weights]
        if len(set(ops)) != len(ops):
            raise SearchError("a mutation operator is weighted twice")


def _gp(
    initial: Sequence[Structure],
    data: InvestigationData,
    config: GPConfig,
    budget: int,
    seed: int,
    workers: int,
    settings: _Settings,
) -> _Ledger:
    rng = np.random.default_rng(seed)
    alpha = _alphabet(data.channels)
    ops = [op for op, _ in config.mutation_weights]
    cdf = _cdf([w for _, w in config.mutation_weights])
    for s in initial:
        validate(canonicalise(s), data.channels)
    with _pool(workers) as pool:
        ledger = _Ledger(data, budget, settings, pool)
        ledger.evaluate(initial or (Structure(()),))
        while ledger.remaining > 0:
            ranked = sorted(ledger.records, key=lambda r: (r.criterion, r.number))
            selectable = [r for r in ranked if math.isfinite(r.criterion)]
            pop = [r.structure for r in (selectable or ranked)[: config.population]]
            wanted = min(config.offspring, ledger.remaining)
            children: list[Structure] = []
            pending: set[str] = set()
            for _ in range(config.max_attempts * wanted):
                if len(children) >= wanted:
                    break
                child = _offspring(pop, config, ops, cdf, alpha, rng)
                if child is None:
                    continue
                key = structure_hash(child)
                if key in ledger.seen or key in pending:
                    continue
                try:
                    validate(child, data.channels)
                except InvalidStructureError:
                    continue
                children.append(child)
                pending.add(key)
            if not children:
                break
            ledger.evaluate(children)
    return ledger


def _cdf(weights: Sequence[float]) -> tuple[float, ...]:
    """Cumulative probabilities by exact prefix sums; the last is exactly 1."""
    total = math.fsum(weights)
    cdf = [math.fsum(weights[: i + 1]) / total for i in range(len(weights))]
    cdf[-1] = 1.0
    return tuple(cdf)


def _offspring(
    pop: Sequence[Structure],
    config: GPConfig,
    ops: Sequence[Mutation],
    cdf: Sequence[float],
    alpha: Alphabet,
    rng: np.random.Generator,
) -> Structure | None:
    def tournament() -> Structure:
        # pop is ranked best first, so the smallest drawn index wins.
        return pop[int(rng.integers(len(pop), size=config.tournament).min())]

    if len(pop) >= 2 and rng.random() < config.crossover_prob:
        return crossover(tournament(), tournament(), rng, config.max_features)
    u = rng.random()
    op = ops[next((i for i, c in enumerate(cdf) if u < c), len(cdf) - 1)]
    return mutate(tournament(), op, alpha, rng, config.max_features)


@dataclass(frozen=True)
class BSym:
    """Genetic programming over the DSL with F fits (module docstring)."""

    seeds: tuple[Structure, ...]
    config: GPConfig = GPConfig()
    budget: int = 40
    seed: int = 0
    workers: int = 1
    fit_config: FitConfig | None = None
    fitter: Fitter = certified_fit
    memo: dict[str, FitResult] | None = field(default=None, compare=False, hash=False)
    name: str = B_SYM

    def __post_init__(self) -> None:
        _check_common(self.budget, self.workers)

    def run(self, data: InvestigationData) -> SystemResult:
        settings = _Settings(self.fit_config, self.fitter, self.memo)
        ledger = _gp(
            self.seeds,
            data,
            self.config,
            self.budget,
            self.seed,
            self.workers,
            settings,
        )
        return ledger.result(self.name)


@dataclass(frozen=True)
class PlantedHint:
    """CONTROL (SPEC §6.4): B-sym that proposes :func:`hint` of the truth first.

    ``truth`` enters only here, as ORACLE's does. Never a system under test.
    """

    truth: Structure
    seeds: tuple[Structure, ...]
    config: GPConfig = GPConfig()
    budget: int = 40
    seed: int = 0
    workers: int = 1
    fit_config: FitConfig | None = None
    fitter: Fitter = certified_fit
    memo: dict[str, FitResult] | None = field(default=None, compare=False, hash=False)
    name: str = PLANTED_HINT

    def __post_init__(self) -> None:
        _check_common(self.budget, self.workers)

    def run(self, data: InvestigationData) -> SystemResult:
        settings = _Settings(self.fit_config, self.fitter, self.memo)
        initial = (hint(self.truth), *self.seeds)
        ledger = _gp(
            initial, data, self.config, self.budget, self.seed, self.workers, settings
        )
        return ledger.result(self.name)
