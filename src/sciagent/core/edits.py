"""Typed structural edits, the edit grammar, its prefix code and its metric.

v1 SPEC §0 is binding here: the grammar is not neutral. What is expressible, what
counts as one edit rather than two, and what the prefix code charges per
construct are all author decisions that propagate into every score. They are
written down in this module so they can be inspected, versioned and varied in
sensitivity analysis (research question R5).

The four edit types
-------------------

``ChangeDistributionFamily``
    Replaces the *distributional family* of a component's output, keeping it
    memoryless within its structural class: ``poisson_homogeneous`` becomes
    ``mixture_of_poisson_2``. The conditional law changes; nothing gains memory.

``ReparameteriseComponent``
    Keeps the conditional law fixed and replaces a *constant* parameter with a
    deterministic function of exogenous state, typically time: a constant rate
    becomes a periodic rate. Arrivals remain conditionally Poisson.

``AddLatentVariable``
    Attaches an unobserved stochastic process that modulates the component.

``AddDependency``
    Adds a *lagged* edge, so the target at event ``i`` conditions on a parent's
    values at events ``< i``. Self-loops (Hawkes) and cross-component links
    (scenario S11's ``size -> arrival``) are the same construct; only the source
    differs. Because the edge is lagged it cannot create a cycle in the
    time-unrolled graph, which is what lets S11 coexist with v1 SPEC §3.1's
    acyclicity requirement (see ``sciagent.core.program``).

The first two are distinguished by *what changes*, not by how the code is
written: a stochastic-family change versus a deterministic-parameter-function
change. The boundary matters because the prefix code charges the two
differently and because ``distance`` treats a cross-type difference as larger
than a within-type parameter difference.

Compilation
-----------

``Component.family`` is derived, not declared. An edit states structure; the
grammar's resolution table maps ``(construct, target)`` to the family id the
component compiles to. The resolution table is stated relative to the reference
programme's base families, so a defect may carry **at most one edit per target
component**; stacking two edits on one component would leave the compiled family
ambiguous and raises :class:`InvalidEditError`.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from itertools import product
from types import MappingProxyType
from typing import Literal

import numpy as np
from scipy.optimize import linear_sum_assignment

from sciagent.core import reductions
from sciagent.core.errors import (
    EditNotInGrammarError,
    InvalidEditError,
    OffGridParameterError,
    UnknownParameterError,
)
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import (
    ComponentId,
    FamilyId,
    FrozenDict,
    GrammarVersion,
    KernelId,
    LatentSpec,
    LatentSpecId,
    Parameters,
)

# --------------------------------------------------------------------------
# Parameter quantisation
# --------------------------------------------------------------------------

type Spacing = Literal["log", "linear"]

#: Relative tolerance when testing grid membership. Grid values are produced by
#: :func:`numpy.linspace`/:func:`numpy.geomspace` and round-trip through the
#: literals frozen in an environment module, so the tolerance absorbs decimal
#: formatting only, never a genuinely different value.
GRID_TOLERANCE = 1e-9


@cache
def _grid_values(
    low: float, high: float, size: int, spacing: Spacing
) -> tuple[float, ...]:
    if spacing == "log":
        raw = np.geomspace(low, high, size)
    else:
        raw = np.linspace(low, high, size)
    return tuple(float(value) for value in raw)


@dataclass(frozen=True, slots=True)
class ParameterGrid:
    """The finite set of values one parameter may take.

    A prefix code cannot exist over the reals, so acceptance test A4 is only
    well posed once the edit space is enumerable. Quantisation is therefore part
    of the frozen prior, not an implementation detail: ``size`` fixes the cost in
    bits of naming a value of this parameter at ``log2(size)``.
    """

    name: str
    low: float
    high: float
    size: int
    spacing: Spacing = "log"

    def __post_init__(self) -> None:
        if self.size < 2:
            raise InvalidEditError(
                f"grid {self.name!r} must have at least 2 points, got {self.size}"
            )
        if self.spacing == "log" and self.low <= 0.0:
            raise InvalidEditError(
                f"log-spaced grid {self.name!r} needs a positive lower bound, "
                f"got {self.low}"
            )
        if self.high <= self.low:
            raise InvalidEditError(
                f"grid {self.name!r} needs high > low, got {self.low}..{self.high}"
            )

    @property
    def values(self) -> tuple[float, ...]:
        """Return the grid points, ascending."""
        return _grid_values(self.low, self.high, self.size, self.spacing)

    @property
    def bits(self) -> float:
        """Bits charged by the prefix code for naming one value of this grid."""
        return math.log2(self.size)

    def index(self, value: float) -> int:
        """Return the grid index of ``value``.

        Raises :class:`OffGridParameterError` if it is not a grid point.
        """
        values = self.values
        best = min(range(self.size), key=lambda i: abs(values[i] - value))
        scale = max(abs(values[best]), abs(value), 1.0)
        if abs(values[best] - value) > GRID_TOLERANCE * scale:
            raise OffGridParameterError(
                f"value {value!r} for parameter {self.name!r} is not a grid point; "
                f"nearest is {values[best]!r}"
            )
        return best

    def snap(self, value: float) -> float:
        """Return the nearest grid point to ``value``, without complaint.

        Used when freezing calibrated scenario parameters onto the grid. Never
        used inside :meth:`EditGrammar.code_length`, which must reject off-grid
        values rather than silently move them.
        """
        values = self.values
        return min(values, key=lambda candidate: abs(candidate - value))


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeDistributionFamily:
    """Replace the distributional family of ``target``'s output."""

    target: ComponentId
    family: FamilyId
    parameters: Parameters = field(default_factory=FrozenDict)


@dataclass(frozen=True, slots=True)
class ReparameteriseComponent:
    """Replace a constant parameter of ``target`` with a deterministic function."""

    target: ComponentId
    parameterisation: FamilyId
    parameters: Parameters = field(default_factory=FrozenDict)


@dataclass(frozen=True, slots=True)
class AddLatentVariable:
    """Attach an unobserved process ``spec`` to ``target``."""

    target: ComponentId
    spec: LatentSpecId
    parameters: Parameters = field(default_factory=FrozenDict)


@dataclass(frozen=True, slots=True)
class AddDependency:
    """Make ``target`` at event ``i`` depend on ``source``'s values before ``i``."""

    source: ComponentId
    target: ComponentId
    kernel: KernelId
    parameters: Parameters = field(default_factory=FrozenDict)


type Edit = (
    ChangeDistributionFamily
    | AddLatentVariable
    | AddDependency
    | ReparameteriseComponent
)
type Defect = frozenset[Edit]

#: All edit types, in a fixed order independent of import order or hashing.
EDIT_TYPES: tuple[type[Edit], ...] = (
    AddDependency,
    AddLatentVariable,
    ChangeDistributionFamily,
    ReparameteriseComponent,
)

NULL_DEFECT: Defect = frozenset()


def target_of(edit: Edit) -> ComponentId:
    """Return the component an edit modifies."""
    return edit.target


def _option_key(edit: Edit) -> str:
    """Return the construct an edit selects, within its (type, target) cell."""
    match edit:
        case ChangeDistributionFamily():
            return str(edit.family)
        case ReparameteriseComponent():
            return str(edit.parameterisation)
        case AddLatentVariable():
            return str(edit.spec)
        case AddDependency():
            return f"{edit.source}|{edit.kernel}"


def _target_key(edit: Edit) -> str:
    """Return the full target identity, including a dependency's source."""
    if isinstance(edit, AddDependency):
        return f"{edit.source}->{edit.target}"
    return str(edit.target)


def sort_key(edit: Edit) -> tuple[str, str, str, tuple[tuple[str, float], ...]]:
    """Return the canonical ordering key for an edit.

    Total and process-independent, so a ``Defect`` (an unordered set) has one
    canonical encoding and one canonical application order.

    Parameter values are normalised by ``float(value) + 0.0``, which is what
    makes this key agree with ``Defect`` equality. Two defects are equal when
    their parameters compare equal, but the callers that *render* this key --
    ``structure_key`` and ``defect_key``, which address table rows and registry
    fields -- render through ``repr``. Equality and ``repr`` disagree in exactly
    two places: ``1 == 1.0`` across the numeric tower, and ``-0.0 == 0.0`` across
    signed zeros. Without normalisation two equal defects could be addressed by
    two different strings, and a memoised renderer keyed on equality would return
    whichever was rendered first -- making a content address depend on call order,
    which the determinism invariant forbids.

    The normalisation is currently a no-op and is here to stay that way: every
    parameter reaching an edit comes from ``ParameterGrid.values``, which builds
    each value with ``float(...)``. Measured over all 23 grids in both grammars,
    1472 values, plus the closed set's own 13: no non-float, no signed zero. This
    enforces that rather than trusting it.
    """
    return (
        type(edit).__name__,
        _target_key(edit),
        _option_key(edit),
        tuple((name, float(value) + 0.0) for name, value in edit.parameters.items()),
    )


def canonical(defect: Defect) -> tuple[Edit, ...]:
    """Return the edits of ``defect`` in canonical order."""
    return tuple(sorted(defect, key=sort_key))


# --------------------------------------------------------------------------
# Grammar option tables
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FamilyOption:
    """A family (or parameterisation) a component may be changed to."""

    family: FamilyId
    grids: tuple[ParameterGrid, ...]


@dataclass(frozen=True, slots=True)
class LatentOption:
    """A latent process a component may acquire, and the family it compiles to."""

    spec: LatentSpecId
    resolved_family: FamilyId
    grids: tuple[ParameterGrid, ...]


@dataclass(frozen=True, slots=True)
class DependencyOption:
    """A lagged edge a component may acquire, and the family it compiles to."""

    source: ComponentId
    kernel: KernelId
    resolved_family: FamilyId
    grids: tuple[ParameterGrid, ...]


type Option = FamilyOption | LatentOption | DependencyOption


def _grids_of(option: Option) -> tuple[ParameterGrid, ...]:
    return option.grids


# --------------------------------------------------------------------------
# Grammar
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditGrammar:
    """The licensed edit space, its prefix code and its metric (v1 SPEC §3.1).

    Out-of-library is defined by grammar membership and nothing else:
    ``ground_truth_edit in edit_grammar() \\ agent_grammar()`` (v1 SPEC §3.2).

    Note that :meth:`code_length` is *grammar-relative*: the same edit is charged
    differently under a grammar with a larger licensed space, because naming the
    construct costs more bits. The ground-truth grammar and the agent grammar
    therefore induce different priors, which is correct -- each is a prior over
    its own hypothesis space -- but it means any reported D1 or D6 figure must
    state which grammar produced it.
    """

    version: GrammarVersion
    allowed: frozenset[type[Edit]]
    families: FrozenDict[ComponentId, tuple[FamilyOption, ...]] = field(
        default_factory=FrozenDict
    )
    parameterisations: FrozenDict[ComponentId, tuple[FamilyOption, ...]] = field(
        default_factory=FrozenDict
    )
    latent_specs: FrozenDict[ComponentId, tuple[LatentOption, ...]] = field(
        default_factory=FrozenDict
    )
    dependencies: FrozenDict[ComponentId, tuple[DependencyOption, ...]] = field(
        default_factory=FrozenDict
    )

    # -- structure ---------------------------------------------------------

    @property
    def targets(self) -> Mapping[type[Edit], frozenset[ComponentId]]:
        """Return the licensed target components per edit type (v1 SPEC §3.1).

        Iteration order is :attr:`ordered_types`, which is fixed and independent
        of hashing, so walking this mapping cannot perturb anything downstream.
        """
        return MappingProxyType(
            {
                edit_type: frozenset(self._table(edit_type))
                for edit_type in self.ordered_types
            }
        )

    @property
    def ordered_types(self) -> tuple[type[Edit], ...]:
        """Return the allowed edit types in canonical order."""
        return tuple(t for t in EDIT_TYPES if t in self.allowed)

    def _table(self, edit_type: type[Edit]) -> Mapping[ComponentId, tuple[Option, ...]]:
        if edit_type not in self.allowed:
            raise EditNotInGrammarError(
                f"grammar {self.version} does not allow {edit_type.__name__}"
            )
        match edit_type.__name__:
            case "ChangeDistributionFamily":
                return self.families
            case "ReparameteriseComponent":
                return self.parameterisations
            case "AddLatentVariable":
                return self.latent_specs
            case "AddDependency":
                return self.dependencies
        raise InvalidEditError(f"unknown edit type {edit_type!r}")

    def options(self, edit_type: type[Edit], target: ComponentId) -> tuple[Option, ...]:
        """Return the constructs licensed for ``(edit_type, target)``."""
        table = self._table(edit_type)
        if target not in table:
            raise EditNotInGrammarError(
                f"grammar {self.version} does not allow {edit_type.__name__} "
                f"on {target!r}"
            )
        return table[target]

    def option_of(self, edit: Edit) -> Option:
        """Return the grammar option an edit selects, or raise a typed error."""
        for option in self.options(type(edit), edit.target):
            if _option_id(option) == _option_key(edit):
                return option
        raise EditNotInGrammarError(
            f"grammar {self.version} does not license {type(edit).__name__} "
            f"{_option_key(edit)!r} on {edit.target!r}"
        )

    # -- validation --------------------------------------------------------

    def validate(self, edit: Edit) -> None:
        """Raise unless ``edit`` lies in this grammar's licensed space.

        Checks the type is allowed, the target and construct are licensed, the
        parameter names match the construct's declaration exactly, and every
        value is a point of its declared grid.
        """
        option = self.option_of(edit)
        grids = {grid.name: grid for grid in _grids_of(option)}
        supplied = set(edit.parameters)
        if supplied != set(grids):
            raise UnknownParameterError(
                f"{type(edit).__name__} on {edit.target!r} declares parameters "
                f"{sorted(grids)!r} but was given {sorted(supplied)!r}"
            )
        for name, value in edit.parameters.items():
            grids[name].index(value)

    def contains(self, edit: Edit) -> bool:
        """Return whether ``edit`` lies in this grammar's licensed space."""
        try:
            self.validate(edit)
        except (EditNotInGrammarError, UnknownParameterError, OffGridParameterError):
            return False
        return True

    def validate_defect(self, defect: Defect) -> None:
        """Raise unless every edit is licensed and no two edits share a target."""
        seen: dict[ComponentId, Edit] = {}
        for edit in canonical(defect):
            self.validate(edit)
            if edit.target in seen:
                raise InvalidEditError(
                    f"defect contains two edits on {edit.target!r} "
                    f"({type(seen[edit.target]).__name__} and "
                    f"{type(edit).__name__}); the compiled family would be ambiguous"
                )
            seen[edit.target] = edit

    # -- application -------------------------------------------------------

    def apply(self, p: GenerativeProgram, d: Defect) -> GenerativeProgram:
        """Return ``p`` with defect ``d`` applied.

        Guarantees the result is a valid, executable programme whenever ``d`` is
        licensed by this grammar (acceptance test A2), that ``p`` is unmodified,
        and that the result depends only on the *set* ``d``, never on iteration
        order: edits are applied in :func:`canonical` order.
        """
        self.validate_defect(d)
        components = dict(p.components)
        edges = set(p.edges)
        history = set(p.history_edges)

        for edit in canonical(d):
            if edit.target not in components:
                raise EditNotInGrammarError(
                    f"edit targets {edit.target!r}, absent from the programme"
                )
            component = components[edit.target]
            option = self.option_of(edit)
            match edit:
                case ChangeDistributionFamily() | ReparameteriseComponent():
                    assert isinstance(option, FamilyOption)
                    components[edit.target] = replace(
                        component, family=option.family, parameters=edit.parameters
                    )
                case AddLatentVariable():
                    assert isinstance(option, LatentOption)
                    components[edit.target] = replace(
                        component,
                        family=option.resolved_family,
                        latents=(
                            *component.latents,
                            LatentSpec(id=edit.spec, parameters=edit.parameters),
                        ),
                    )
                case AddDependency():
                    assert isinstance(option, DependencyOption)
                    if edit.source not in components:
                        raise EditNotInGrammarError(
                            f"dependency source {edit.source!r} is absent from "
                            f"the programme"
                        )
                    components[edit.target] = replace(
                        component,
                        family=option.resolved_family,
                        parameters=edit.parameters,
                    )
                    edges.add((edit.source, edit.target))
                    history.add((edit.source, edit.target))

        return GenerativeProgram(
            components=FrozenDict(components),
            edges=frozenset(edges),
            library=p.library,
            history_edges=frozenset(history),
        )

    # -- prefix code -------------------------------------------------------

    def code_length(self, d: Defect) -> float:
        """Return the description length of ``d`` in bits.

        This defines the structural complexity prior ``p(D) proportional to
        2**-L(D)`` (v1 SPEC §0). It is frozen: it encodes a belief about parsimony
        derived from the representation, and must never be tuned to benchmark
        prevalence.

        **Bit allocation.** A defect is encoded as a count followed by its edits
        in :func:`canonical` order::

            L(D) = L_count(|D|) + sum over edits of L(edit)

        ``L_count(k) = 2*floor(log2(k+1)) + 1``
            Elias gamma over ``k+1``, so the empty defect costs 1 bit and each
            doubling of the edit count costs 2 further bits. Elias gamma is
            complete: ``sum over k>=0 of 2**-L_count(k) == 1`` exactly.

        ``L(edit) = L_type + L_target + L_option + L_parameters``

        ``L_type = log2(|allowed|)``
            Uniform over the licensed edit types. Deliberately uniform: a
            non-uniform type code would smuggle benchmark prevalence into the
            prior, which v1 SPEC §0 forbids.

        ``L_target = log2(|targets(type)|)``
            Uniform over the components this edit type may modify. For
            ``AddDependency`` the target names the modified component; the source
            is charged inside ``L_option``, because which parent a dependency
            draws from is part of choosing the construct.

        ``L_option = log2(|options(type, target)|)``
            Uniform over the constructs available in that cell: the candidate
            families, parameterisations, latent specs, or (source, kernel) pairs.

        ``L_parameters = sum over the construct's grids of log2(grid.size)``
            Uniform over each parameter's quantisation grid. A construct with
            more free parameters is charged more, which is the whole content of
            the parsimony prior: a four-parameter regime-switching mechanism is
            a priori less probable than a three-parameter Hawkes kernel by
            exactly the bits its extra parameter costs.

        Every level is a uniform code over a finite set, so each level's Kraft
        sum is exactly 1 and the single-edit space sums to exactly 1. Sequences
        of edits therefore also sum to 1 at each length, and the Elias gamma
        length prefix bounds the total by 1. Restricting to canonically ordered
        sets discards the ``k!`` orderings of each set, so the realised sum is
        strictly below 1 (acceptance test A4).
        """
        self.validate_defect(d)
        total = _elias_gamma_length(len(d) + 1)
        for edit in canonical(d):
            total += self.edit_code_length(edit)
        return total

    def edit_code_length(self, edit: Edit) -> float:
        """Return the description length of a single edit in bits."""
        self.validate(edit)
        edit_type = type(edit)
        bits = math.log2(len(self.allowed))
        bits += math.log2(len(self._table(edit_type)))
        bits += math.log2(len(self.options(edit_type, edit.target)))
        for grid in _grids_of(self.option_of(edit)):
            bits += grid.bits
        return bits

    def kraft_sum_single_edits(self) -> float:
        """Return ``sum over the single-edit space of 2**-L(edit)``, exactly.

        Computed by grouped enumeration -- every structural cell is visited and
        its parameter combinations counted rather than materialised -- so the
        result covers the entire space, not a sample.
        """
        total = 0.0
        type_bits = math.log2(len(self.allowed))
        for edit_type in self.ordered_types:
            table = self._table(edit_type)
            target_bits = math.log2(len(table))
            for target in table:
                option_bits = math.log2(len(table[target]))
                for option in table[target]:
                    grids = _grids_of(option)
                    combinations = math.prod(grid.size for grid in grids)
                    parameter_bits = math.fsum(grid.bits for grid in grids)
                    length = type_bits + target_bits + option_bits + parameter_bits
                    total += combinations * 2.0**-length
        return total

    def edit_space_size(self) -> int:
        """Return the exact number of distinct single edits this grammar licenses."""
        total = 0
        for edit_type in self.ordered_types:
            table = self._table(edit_type)
            for target in table:
                for option in table[target]:
                    total += math.prod(grid.size for grid in _grids_of(option))
        return total

    # -- enumeration -------------------------------------------------------

    def structures(self) -> Iterator[tuple[type[Edit], ComponentId, Option]]:
        """Yield every structural cell of the edit space, parameters aside."""
        for edit_type in self.ordered_types:
            table = self._table(edit_type)
            for target in table:
                for option in table[target]:
                    yield edit_type, target, option

    def enumerate_edits(self, max_per_structure: int | None = None) -> Iterator[Edit]:
        """Yield licensed edits.

        Exhaustive over structural cells always. Within a cell, exhaustive over
        parameter combinations when there are at most ``max_per_structure`` of
        them; otherwise a deterministic stratified subset of that size, always
        including every corner of the grid box. ``None`` means fully exhaustive.
        """
        for edit_type, target, option in self.structures():
            grids = _grids_of(option)
            for combination in _parameter_combinations(grids, max_per_structure):
                yield build_edit(edit_type, target, option, combination)

    # -- metric ------------------------------------------------------------

    def distance(self, a: Defect, b: Defect) -> float:
        """Return the distance between two defects, in units of one edit.

        A metric by construction rather than by inspection. The ground distance
        on single edits is::

            0                    identical
            p in (0, 1]          same type, target and construct; p is the mean
                                 normalised grid-index displacement
            1.5                  same target, different type or construct
            2                    different target

        which is a metric (within-cell it is a scaled L1 metric; across cells it
        is a constant), and it is bounded by ``2 * DELETION_COST``. Defect
        distance is then the minimum-cost partial matching between the two edit
        sets, with unmatched edits charged ``DELETION_COST = 1``. A partial
        matching distance with constant insertion cost over a ground metric
        bounded by twice that cost is itself a metric on finite sets, giving
        identity, symmetry and the triangle inequality without appeal to
        sampling (acceptance test A3 verifies rather than discovers this).

        Both defects must be licensed by this grammar, since the parameter term
        is defined by grid indices.
        """
        self.validate_defect(a)
        self.validate_defect(b)
        left = canonical(a)
        right = canonical(b)
        if not left and not right:
            return 0.0

        n, m = len(left), len(right)
        size = n + m
        blocked = _BLOCKED
        cost = np.full((size, size), blocked, dtype=np.float64)
        for i in range(n):
            for j in range(m):
                cost[i, j] = self._ground_distance(left[i], right[j])
        for i in range(n):
            cost[i, m + i] = DELETION_COST
        for j in range(m):
            cost[n + j, j] = DELETION_COST
        cost[n:, m:] = 0.0

        rows, columns = linear_sum_assignment(cost)
        # `reductions.total`, not `.sum()`: this is v1 SPEC §8's D1, so the value
        # is reported and stored. `_ground_distance` below already folds with
        # `math.fsum` for exactly this reason; the aggregate three lines up did
        # not, which is the kind of half-migration only a check can find.
        return reductions.total(cost[rows, columns])

    def _ground_distance(self, left: Edit, right: Edit) -> float:
        if left == right:
            return 0.0
        if type(left) is not type(right) or _target_key(left) != _target_key(right):
            if target_of(left) != target_of(right):
                return 2.0 * DELETION_COST
            return 1.5 * DELETION_COST
        if _option_key(left) != _option_key(right):
            return 1.5 * DELETION_COST
        grids = {grid.name: grid for grid in _grids_of(self.option_of(left))}
        if not grids:
            return 1.5 * DELETION_COST
        displacement = math.fsum(
            abs(grid.index(left.parameters[name]) - grid.index(right.parameters[name]))
            / (grid.size - 1)
            for name, grid in grids.items()
        )
        return DELETION_COST * displacement / len(grids)


#: Cost of leaving one edit unmatched. Sets the unit of :meth:`EditGrammar.distance`:
#: a distance of 1 is "one edit wholly missing or wholly spurious".
DELETION_COST = 1.0

#: Finite stand-in for an impossible assignment. Must exceed any achievable
#: total cost; scipy's solver handles large finite costs but not infinities.
_BLOCKED = 1e9


def _option_id(option: Option) -> str:
    match option:
        case FamilyOption():
            return str(option.family)
        case LatentOption():
            return str(option.spec)
        case DependencyOption():
            return f"{option.source}|{option.kernel}"


def build_edit(
    edit_type: type[Edit],
    target: ComponentId,
    option: Option,
    parameters: Parameters,
) -> Edit:
    """Return the edit a structural cell and a parameter assignment denote.

    The inverse of reading an edit's ``(type, target, option)`` back off it, and
    the only supported way to construct an edit from a grammar cell. Public
    because a proposal layer decodes a *choice* of cell into an edit, and doing
    that by instantiating the dataclasses directly would put a second copy of
    the mapping from option kind to edit type into the codebase -- where
    :class:`FamilyOption` resolving to either ``ChangeDistributionFamily`` or
    ``ReparameteriseComponent`` depending on the requested type is exactly the
    kind of detail that would drift.
    """
    match option:
        case FamilyOption() if edit_type is ChangeDistributionFamily:
            return ChangeDistributionFamily(target, option.family, parameters)
        case FamilyOption():
            return ReparameteriseComponent(target, option.family, parameters)
        case LatentOption():
            return AddLatentVariable(target, option.spec, parameters)
        case DependencyOption():
            return AddDependency(option.source, target, option.kernel, parameters)


def _parameter_combinations(
    grids: Sequence[ParameterGrid], max_count: int | None
) -> Iterator[Parameters]:
    """Yield parameter assignments for ``grids``, exhaustively or stratified.

    The stratified subset is deterministic: each grid is thinned to an evenly
    spaced subset that always retains its first and last point, so every corner
    of the grid box is covered and the sample never depends on a random seed.
    """
    if not grids:
        yield FrozenDict[str, float]()
        return
    total = math.prod(grid.size for grid in grids)
    if max_count is None or total <= max_count:
        chosen = [grid.values for grid in grids]
    else:
        per_grid = max(2, round(max_count ** (1.0 / len(grids))))
        chosen = [_thin(grid.values, per_grid) for grid in grids]
    for combination in product(*chosen):
        yield FrozenDict[str, float](
            dict(zip((grid.name for grid in grids), combination, strict=True))
        )


def _thin(values: tuple[float, ...], count: int) -> tuple[float, ...]:
    if count >= len(values):
        return values
    indices = np.linspace(0, len(values) - 1, count)
    return tuple(values[round(position)] for position in indices)


def _elias_gamma_length(m: int) -> float:
    """Return the Elias gamma code length for ``m >= 1``, in bits.

    Elias gamma is complete over the positive integers:
    ``sum over m>=1 of 2**-length(m) == 1`` exactly, which is what lets the
    defect-length prefix carry the whole Kraft budget.
    """
    if m < 1:
        raise InvalidEditError(f"Elias gamma is defined for m >= 1, got {m}")
    return float(2 * m.bit_length() - 1)
