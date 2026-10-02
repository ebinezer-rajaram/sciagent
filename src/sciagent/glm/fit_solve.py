"""The inner problem of the certified fitter: θ at fixed ψ, with a certificate.

For fixed ψ the negative log-likelihood is convex in θ (SPEC §2.2). Written
over the stacked design of every dataset, with ``A`` the quadrature-node rows,
``w`` their weights, ``X`` the counted-event rows and ``c`` the exact column
integrals, it is

- identity: ``F(θ) = cᵀθ - Σᵢ log(xᵢθ)`` subject to ``Aθ ≥ 0``: λ ≥ 0 at
  every node and at every event's right limit ``tᵢ⁺`` (:func:`right_limit_rows`,
  appended to A by :func:`with_constraint_rows`), not only at events. A
  negative coefficient on a decreasing kernel puts λ's minimum at ``tᵢ⁺``,
  which no interior Gauss-Legendre node reaches;
- exp: ``F(θ) = Σ_q w_q exp(a_qθ) - Σᵢ xᵢθ``;
- softplus: ``F(θ) = Σ_q w_q softplus(a_qθ) - Σᵢ log softplus(xᵢθ)``.

``log ∘ softplus`` is concave (``log(1 + eᶻ) ≤ eᶻ`` gives
``sp·sp'' ≤ sp'²``) but is not DCP-expressible: CVXPY can only take ``log``
of a concave argument and ``softplus`` is convex. So CVXPY cannot pose the
softplus problem, and the certificate below is what certifies it.

**Solvers.** The default is a damped Newton method with an Armijo
backtracking line search, from a fixed cold start. It is fast (a ψ point
costs milliseconds, against seconds for Clarabel on ~10⁵ exponential cones,
measured) and its iterations are deterministic: every fold in it is the
fixed pairwise tree of :func:`reductions.pairwise_rows` (gradients and
Hessians fused block by block in cache, :func:`_row_sums`) or a fixed
sequential sum (:func:`_lin`), never a CPU-chosen BLAS or SIMD order. Those
folds are reproducible, not exact; every number the certificate reports is
exact (below). The identity link's node constraints are
handled by cutting planes: the problem without them is solved first, and only
if its optimum is negative at some candidate row are the most violated rows
(at most 64 per round, by violation relative to the row's size) handed, as
linear constraints scaled to unit ∞-norm, to CVXPY with Clarabel, adding
rows until none is violated. Handing tens of thousands of unscaled, nearly
parallel rows at once made Clarabel fail. A relaxation's optimum that is
feasible for the full problem is optimal for it. ``solver="clarabel"`` solves
the identity and exp problems with Clarabel throughout, as a cross-check.

**Certificate.** Whichever solver produced θ, the gap is computed here from
an explicit dual point, never taken from a solver's report. Write
``F(θ) = cᵀθ + Σₖ hₖ(rₖθ)`` over rows ``rₖ``. Its Fenchel dual is
``max -Σₖ hₖ*(uₖ)`` subject to ``Σₖ uₖ rₖ + c = 0``, and for every θ and
every dual-feasible u, ``F(θ) ≥ -Σ hₖ*(uₖ)``. The natural dual point
``uₖ = hₖ'(rₖθ)`` misses feasibility by ``∇F(θ)``; it is corrected by
``u' = u - hₖ''·(rₖδ)`` with ``Mδ = ∇F`` (M the Hessian of the corrected
rows), which zeroes the residual to first order: it is the dual image of a
Newton step. Since ``F(θ) ≥ D(u') + r'ᵀθ`` for every θ, with r' the remaining
dual residual ``Σ u'ₖrₖ + c``, the reported gap is
``F(θ̂) - D(u') + |r'|ᵀ|θ̂|``, its terms summed exactly (correctly rounded,
:class:`reductions.ExactSum`, equal to one :func:`math.fsum` bit for bit), as
are ``F(θ̂)`` and ``D(u')`` themselves; r' is a pairwise fold, padded by that
fold's rigorous error bound (:func:`_residual`), and the term is rounded
upward. It bounds ``F(θ̂) - F*`` to first order
(θ* ≈ θ̂), and the relative size of r' is reported beside it. Corrected rows: events
(identity), nodes (exp, softplus). Softplus event rows keep ``uᵢ = e'(zᵢ)``
so ``e*(uᵢ) = uᵢzᵢ - e(zᵢ)`` holds exactly, and no conjugate of
``-log softplus`` (which has no closed form) is ever needed. For the
constrained identity problem the multipliers nu ≥ 0 of the active node
constraints are Clarabel's duals, and ``h* = 0`` on those rows.

A solution is certified iff the solver stopped by its own convergence test
(Newton's decrement test; Clarabel ``optimal`` or ``optimal_inaccurate``,
see :data:`ACCEPTED_STATUS`), θ is primal feasible (every node, under the
identity link), the dual point is in the conjugates' domain, the relative
residual is at most ``RESIDUAL_TOL_REL`` and
``-1e-12 · max(1, |F|) ≤ gap ≤ gap_tol_rel · max(1, |F|)``: a gap below the
floor cannot be rounding, so it means the certificate itself is wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from operator import methodcaller
from typing import Final

import cvxpy as cp
import numpy as np
import numpy.typing as npt
from cvxpy.atoms.affine.binary_operators import multiply
from cvxpy.atoms.affine.sum import Sum
from cvxpy.atoms.elementwise.exp import exp as cp_exp
from cvxpy.atoms.elementwise.log import log as cp_log
from scipy import special

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError
from sciagent.glm.data import Dataset, Floats
from sciagent.glm.features import PsiAssignment, evaluate_columns
from sciagent.glm.grammar import ChannelSpec, Link, Structure

type Ints = npt.NDArray[np.intp]

#: Newton stops when half the squared Newton decrement (≈ F - F*) is below
#: this times max(1, |F|): four orders below the default gap tolerance and
#: three above the rounding of F.
NEWTON_TOL_REL: Final = 1e-12
#: Largest admissible dual residual, relative to the size of its terms.
RESIDUAL_TOL_REL: Final = 1e-9
#: Armijo sufficient-decrease fraction and backtracking factor.
_ARMIJO: Final = 0.25
_BACKTRACK: Final = 0.5
_MIN_STEP: Final = 1e-14
#: Eigenvalues below this fraction of the largest are dropped (collinear
#: columns, e.g. two identical Excite features at one ψ): minimum-norm step.
_EIG_CUTOFF: Final = 1e-12
#: Beyond this, exp(η) overflows float64.
_MAX_EXP: Final = 700.0
#: log(softplus(z)) and its derivatives use their series below these.
_LOG_SP_SERIES: Final = -30.0
_HESS_SERIES: Final = -15.0
#: Cutting-plane rounds for the identity link's node constraints.
_MAX_CUT_ROUNDS: Final = 60
#: Most violated rows added per cutting-plane round.
_MAX_NEW_CUTS: Final = 64
#: A node is a violator when λ < -_FEAS_TOL · max(1, θ₀).
_FEAS_TOL: Final = 1e-9
#: ... and a violation up to this (same scale) is absorbed by shifting θ₀.
_ABSORB: Final = 1e-7
#: Clarabel's own tolerances, tightened so its θ can pass the certificate.
_CLARABEL_TOL: Final = 1e-10
_DUAL_ROUNDS: Final = 4
#: The dual correction stops once the relative residual is at rounding level.
_RESIDUAL_FLOOR: Final = 1e-15
_UNIT_ROUNDOFF: Final = 2.0**-53
#: A gap below -this · max(1, |F|) cannot be rounding of the exact fold of
#: primal and dual terms, so the certificate is wrong and the point fails.
_GAP_FLOOR_REL: Final = 1e-12
#: Solver outcomes whose θ is handed to the certificate. Clarabel reports
#: ``optimal_inaccurate`` when it stops short of the tightened tolerances above
#: but within its reduced ones; the explicit gap below, not the solver's own
#: report, then decides whether the point is certified.
ACCEPTED_STATUS: Final = frozenset({"optimal", "optimal_inaccurate"})


class InnerSolveError(SciAgentError):
    """The inner problem is malformed (shapes, an unsupported link/solver)."""


@dataclass(frozen=True, eq=False)
class Problem:
    """The stacked design of every dataset at one ψ.

    ``nodes_t`` (p, m) and ``events_t`` (p, n) are the transposed node and
    counted-event rows, contiguous per column; ``integrals`` (p,) the exact
    column integrals summed over datasets (read by the identity link only).
    """

    link: Link
    nodes_t: Floats
    weights: Floats
    events_t: Floats
    integrals: Floats

    @property
    def p(self) -> int:
        return int(self.nodes_t.shape[0])


@dataclass(frozen=True)
class Solution:
    """θ and its certificate. ``primal`` is ``F(θ) = -log L``."""

    theta: tuple[float, ...]
    solver: str
    status: str
    iterations: int
    primal: float
    dual: float
    gap: float
    gap_rel: float
    residual: float
    certified: bool


# --------------------------------------------------------------------------
# Deterministic folds
# --------------------------------------------------------------------------

#: Columns per pass of :func:`_lin`; the result does not depend on it.
_LIN_BLOCK: Final = 16384


def _fold(x: Floats) -> float:
    """``Σx`` by the fixed pairwise tree of :func:`reductions.pairwise_rows`."""
    return float(reductions.pairwise_rows(x.reshape(1, -1))[0])


def _lin(rows_t: Floats, theta: Floats) -> Floats:
    """``Rθ`` from the transposed rows, summed column by column in order.

    Computed in cache-sized column blocks; every element is the same
    sequence of correctly rounded multiplies and adds whatever the blocking.
    """
    p, m = rows_t.shape
    out = np.empty(m)
    tmp = np.empty(min(m, _LIN_BLOCK))
    for a in range(0, m, _LIN_BLOCK):
        b = min(a + _LIN_BLOCK, m)
        o, t = out[a:b], tmp[: b - a]
        np.multiply(rows_t[0, a:b], theta[0], out=o)
        for j in range(1, p):
            np.multiply(rows_t[j, a:b], theta[j], out=t)
            np.add(o, t, out=o)
    return out


def _row_sums(
    rows_t: Floats,
    d: Floats | None,
    h: Floats | None,
    absolute: bool = False,
    unit_first: bool = False,
) -> tuple[Floats, Floats | None, Floats | None]:
    """``Rᵀd``, ``Rᵀ diag(h) R`` and (if ``absolute``) ``|R|ᵀ|d|``, fused.

    Every entry is :func:`reductions.pairwise_rows` of its products (row i
    times d, ``|r_i·d|``, or ``(r_i·h)·r_j`` for i ≤ j), but the products are
    formed block by block in cache and streamed into a
    :class:`reductions.PairwiseAccumulator`: one pass over R, the
    ``p(p+1)/2`` product rows never held whole, and every entry's fold order
    fixed by the row length alone. Returns ``(g, H, a)``; g is zeros when d
    is None, H and a are None when not asked for.

    ``unit_first`` says row 0 of R is all ones (the intercept). When also
    ``h is d``, the products ``(1·h)·r_j`` of H's first row equal g's
    ``r_j·d`` bit for bit (IEEE products commute), so H[0, :] is copied
    from g instead of being formed.
    """
    p, m = rows_t.shape
    nd = p if d is not None else 0
    na = p if d is not None and absolute else 0
    skip = 1 if unit_first and d is not None and h is d else 0
    iu, ju = np.triu_indices(p) if h is not None else (np.zeros(0, np.intp),) * 2
    iu, ju = iu[iu >= skip], ju[iu >= skip]
    k = nd + na + iu.size
    block = reductions.PAIRWISE_BLOCK
    width = min(block, max(m, 1))
    acc = reductions.PairwiseAccumulator(k)
    buf = np.empty((k, width))
    wh = np.empty((p, width))
    for a in range(0, m, block):
        b = min(a + block, m)
        c = b - a
        r = rows_t[:, a:b]
        out = buf[:, :c]
        if d is not None:
            np.multiply(r, d[a:b], out=out[:p])
            if na:
                np.abs(out[:p], out=out[p : 2 * p])
        if h is not None:
            if h is d:
                w = out[:p]
            else:
                w = wh[:, :c]
                np.multiply(r, h[a:b], out=w)
            row = nd + na
            for i in range(skip, p):  # rows (i, i..p-1), in triu_indices order
                np.multiply(r[i:], w[i], out=out[row : row + p - i])
                row += p - i
        acc.push(out)
    sums = acc.result()
    g = sums[:p].copy() if d is not None else np.zeros(p)
    absolute_sums = sums[p : 2 * p].copy() if na else None
    hess: Floats | None = None
    if h is not None:
        hess = np.zeros((p, p))
        hess[iu, ju] = sums[nd + na :]
        hess[ju, iu] = sums[nd + na :]
        if skip:
            hess[0, :] = g
            hess[:, 0] = g
    return g, hess, absolute_sums


def _exact_colsums(parts: list[tuple[Floats, Floats]], const: Floats) -> Floats:
    """``const + Σ Rᵀd`` over ``(rows_t, d)`` parts, each entry one exact sum."""
    p = const.shape[0]
    out = np.empty(p)
    for j in range(p):
        acc = reductions.ExactSum.of(const[j : j + 1])
        for rows_t, d in parts:
            acc = acc + reductions.ExactSum.of(rows_t[j] * d)
        out[j] = acc.value()
    return out


def _psd_solve(h: Floats, b: Floats) -> Floats:
    """Minimum-norm solution of ``Hx = b`` for symmetric PSD H.

    Jacobi-scaled, then eigendecomposed; eigenvalues below ``_EIG_CUTOFF``
    times the largest are dropped.
    """
    diag = np.diag(h)
    scale = np.where(diag > 0.0, 1.0 / np.sqrt(np.where(diag > 0.0, diag, 1.0)), 1.0)
    hs = h * scale[:, None] * scale[None, :]
    vals, vecs = np.linalg.eigh(hs)
    top = float(np.max(vals)) if vals.size else 0.0
    if not (math.isfinite(top) and top > 0.0):
        return np.zeros_like(b)
    keep = vals > _EIG_CUTOFF * top
    inv = np.where(keep, 1.0 / np.where(keep, vals, 1.0), 0.0)
    coeffs = reductions.matvec(np.ascontiguousarray(vecs.T), b * scale) * inv
    out: Floats = reductions.matvec(vecs, coeffs) * scale
    return out


# --------------------------------------------------------------------------
# Link terms
# --------------------------------------------------------------------------


def _softplus(z: Floats) -> Floats:
    out: Floats = np.logaddexp(0.0, z)
    return out


def _log_softplus(z: Floats) -> Floats:
    out = np.array(z, dtype=np.float64)
    big = z > _LOG_SP_SERIES
    out[big] = np.log(np.logaddexp(0.0, z[big]))
    small = ~big
    out[small] = z[small] - 0.5 * np.exp(z[small])
    return out


def _neg_log_softplus_d1(z: Floats) -> Floats:
    """``d/dz [-log softplus(z)] = -s/softplus(z)`` ∈ (-1, 0), s = expit(z)."""
    out = np.empty_like(z)
    big = z > _LOG_SP_SERIES
    out[big] = -special.expit(z[big]) / np.logaddexp(0.0, z[big])
    small = ~big
    out[small] = -(1.0 - 0.5 * np.exp(z[small]))
    return out


def _neg_log_softplus_d2(z: Floats) -> Floats:
    """``d²/dz² [-log softplus(z)] = s(s - (1-s)·sp)/sp² ≥ 0``, s = expit(z)."""
    out = np.empty_like(z)
    big = z > _HESS_SERIES
    zb = z[big]
    s = special.expit(zb)
    sp = np.logaddexp(0.0, zb)
    out[big] = np.maximum(s * (s - (1.0 - s) * sp), 0.0) / (sp * sp)
    small = ~big
    out[small] = 0.5 * np.exp(z[small])
    return out


@dataclass(frozen=True, eq=False)
class _Point:
    """``F`` at one θ by pairwise folds (``inf`` outside the domain), with the
    linear predictors and the exp link's node terms the derivatives reuse."""

    theta: Floats
    zn: Floats | None
    ze: Floats
    value: float
    en: Floats | None


def _evaluate(problem: Problem, theta: Floats, zn: Floats | None, ze: Floats) -> _Point:
    match problem.link:
        case Link.IDENTITY:
            if ze.size and float(np.min(ze)) <= 0.0:
                return _Point(theta, zn, ze, math.inf, None)
            lin = _fold(problem.integrals * theta)
            return _Point(theta, zn, ze, lin - _fold(np.log(ze)), None)
        case Link.EXP:
            if zn is None:
                raise InnerSolveError("the exp link needs node values")
            if zn.size and float(np.max(zn)) > _MAX_EXP:
                return _Point(theta, zn, ze, math.inf, None)
            en = problem.weights * np.exp(zn)
            return _Point(theta, zn, ze, _fold(en) - _fold(ze), en)
        case Link.SOFTPLUS:
            if zn is None:
                raise InnerSolveError("the softplus link needs node values")
            comp = _fold(problem.weights * _softplus(zn))
            return _Point(theta, zn, ze, comp - _fold(_log_softplus(ze)), None)


def _derivatives(
    problem: Problem, pt: _Point, counts: Floats, unit_first: bool
) -> tuple[Floats, Floats, Floats]:
    """Gradient, Hessian, and the Hessian of the rows the certificate corrects
    (events under the identity link, nodes otherwise): its dual metric."""
    match problem.link:
        case Link.IDENTITY:
            inv = 1.0 / pt.ze
            g, hess, _ = _row_sums(problem.events_t, -inv, inv * inv)
            if hess is None:
                raise InnerSolveError("Hessian rows were not formed")
            return problem.integrals + g, hess, hess
        case Link.EXP:
            if pt.en is None:
                raise InnerSolveError("the exp link needs node terms")
            g, hess, _ = _row_sums(problem.nodes_t, pt.en, pt.en, unit_first=unit_first)
            if hess is None:
                raise InnerSolveError("Hessian rows were not formed")
            return g - counts, hess, hess
        case Link.SOFTPLUS:
            if pt.zn is None:
                raise InnerSolveError("the softplus link needs node values")
            s = special.expit(pt.zn)
            dn = problem.weights * s
            gn, hn, _ = _row_sums(problem.nodes_t, dn, dn * (1.0 - s))
            ge, he, _ = _row_sums(
                problem.events_t,
                _neg_log_softplus_d1(pt.ze),
                _neg_log_softplus_d2(pt.ze),
            )
            if hn is None or he is None:
                raise InnerSolveError("Hessian rows were not formed")
            return gn + ge, hn + he, hn


def right_limit_times(data: Dataset) -> Floats:
    """``tᵢ⁺`` (the next float after every event, forced ones included) that lie
    in ``[0, T]`` and outside the excluded windows.

    Under the identity link a negative coefficient on a decreasing kernel
    makes λ smallest just *after* an event, where the event's own jump has
    landed; Gauss-Legendre nodes are interior to panels and miss that point.
    """
    t = np.nextafter(data.log.times, np.inf)
    keep = t <= data.log.horizon
    for a, b in data.excluded:
        keep &= ~((t >= a) & (t <= b))
    out: Floats = t[keep]
    return out


def right_limit_rows(
    structure: Structure,
    psi: PsiAssignment,
    data: Dataset,
    channels: tuple[ChannelSpec, ...],
) -> Floats:
    """The design rows at :func:`right_limit_times`, transposed: (p, L)."""
    rows = evaluate_columns(structure, psi, data, channels, right_limit_times(data))
    return np.ascontiguousarray(rows.T)


def with_constraint_rows(problem: Problem, rows_t: Floats) -> Problem:
    """The identity problem with ``rows_t`` added to its λ ≥ 0 candidates.

    Under the identity link the node rows are read only as constraints (the
    compensator uses the exact ``integrals``), so extra rows join them.
    """
    if problem.link is not Link.IDENTITY:
        raise InnerSolveError("only the identity link has positivity constraints")
    if rows_t.shape[0] != problem.p:
        raise InnerSolveError("constraint rows disagree with the design columns")
    nodes_t = np.ascontiguousarray(np.concatenate([problem.nodes_t, rows_t], axis=1))
    return Problem(
        problem.link, nodes_t, problem.weights, problem.events_t, problem.integrals
    )


def _uses_nodes(link: Link) -> bool:
    return link is not Link.IDENTITY


def initial_theta(problem: Problem) -> Floats:
    """θ₀ at the homogeneous-Poisson rate, every other coefficient 0."""
    n = problem.events_t.shape[1]
    measure = _fold(problem.weights) if problem.weights.size else 1.0
    rate = max(float(n), 0.5) / max(measure, 1e-300)
    theta = np.zeros(problem.p)
    match problem.link:
        case Link.IDENTITY:
            theta[0] = rate
        case Link.EXP:
            theta[0] = math.log(rate)
        case Link.SOFTPLUS:
            theta[0] = rate + math.log(-math.expm1(-rate))
    return theta


# --------------------------------------------------------------------------
# Newton
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Primal:
    """Newton's θ. When Newton stopped by its own test, ``point`` is F at θ
    (with ``z = Rθ`` and the exp link's node terms) and ``metric`` the
    certificate's metric there, both reused by the certificate; else None."""

    theta: Floats
    status: str
    iterations: int
    metric: Floats | None
    point: _Point | None


def _at(problem: Problem, theta: Floats) -> _Point:
    """:func:`_evaluate` at θ, with ``z = Rθ`` formed afresh."""
    zn = _lin(problem.nodes_t, theta) if _uses_nodes(problem.link) else None
    return _evaluate(problem, theta, zn, _lin(problem.events_t, theta))


def _newton(
    problem: Problem, max_iter: int, counts: Floats, unit_first: bool
) -> _Primal:
    """Damped Newton from :func:`initial_theta`; ``max_iter`` counts steps.

    Every trial point's linear predictors are ``Rθ`` formed afresh (as cheap
    as updating ``z + t·Δz``), so the accepted point is exactly what the
    certificate would compute from θ, and it reuses it.
    """
    pt = _at(problem, initial_theta(problem))
    for it in range(max_iter + 1):
        if not math.isfinite(pt.value):
            return _Primal(pt.theta, "numerics", it, None, None)
        grad, hess, metric = _derivatives(problem, pt, counts, unit_first)
        step = _psd_solve(hess, -grad)
        decrement = -reductions.dot(grad, step)
        if not math.isfinite(decrement):
            return _Primal(pt.theta, "numerics", it, None, None)
        if 0.5 * decrement <= NEWTON_TOL_REL * max(1.0, abs(pt.value)):
            return _Primal(pt.theta, "optimal", it, metric, pt)
        if it == max_iter:
            break
        t = 1.0
        while True:
            trial = _at(problem, pt.theta + t * step)
            if trial.value <= pt.value - _ARMIJO * t * decrement:
                break
            t *= _BACKTRACK
            if t < _MIN_STEP:
                return _Primal(pt.theta, "stalled", it, None, None)
        pt = trial
    return _Primal(pt.theta, "max_iter", max_iter, None, None)


# --------------------------------------------------------------------------
# Clarabel (through CVXPY)
# --------------------------------------------------------------------------


def _affine(rows_t: Floats, th: cp.Variable) -> cp.Expression:
    """``Rθ`` as a CVXPY expression, built column by column."""
    expr: cp.Expression = multiply(rows_t[0], th[0])
    for j in range(1, rows_t.shape[0]):
        expr = expr + multiply(rows_t[j], th[j])
    return expr


@dataclass(frozen=True, eq=False)
class _Conic:
    theta: Floats | None
    duals: Floats | None
    status: str
    iterations: int


def _clarabel(problem: Problem, active: Ints, max_iter: int) -> _Conic:
    """Identity (with node constraints at ``active``) or exp, by Clarabel."""
    th = cp.Variable(problem.p)
    n = problem.events_t.shape[1]
    constraints: list[cp.Constraint] = []
    row_scale = np.ones(0)
    match problem.link:
        case Link.IDENTITY:
            log_terms = cp_log(_affine(problem.events_t, th))
            objective = Sum(multiply(problem.integrals, th)) - Sum(
                multiply(np.ones(n), log_terms)
            )
            if active.size:
                rows = problem.nodes_t[:, active]
                row_scale = np.max(np.abs(rows), axis=0)
                row_scale = np.where(row_scale > 0.0, row_scale, 1.0)
                constraints.append(_affine(rows / row_scale, th) >= 0)
        case Link.EXP:
            exp_terms = cp_exp(_affine(problem.nodes_t, th))
            counts = _exact_colsums(
                [(problem.events_t, np.ones(n))], np.zeros(problem.p)
            )
            objective = Sum(multiply(problem.weights, exp_terms)) - Sum(
                multiply(counts, th)
            )
        case Link.SOFTPLUS:
            raise InnerSolveError("log∘softplus is not DCP; use the Newton solver")
    conic = cp.Problem(cp.Minimize(objective), constraints)
    try:
        # Problem.solve is untyped in CVXPY 1.9; methodcaller keeps the call
        # out of mypy's untyped-call check without silencing anything else.
        methodcaller(
            "solve",
            solver=cp.CLARABEL,
            max_iter=max_iter,
            tol_gap_abs=_CLARABEL_TOL,
            tol_gap_rel=_CLARABEL_TOL,
            tol_feas=_CLARABEL_TOL,
        )(conic)
    except cp.error.SolverError:
        return _Conic(None, None, "solver_error", 0)
    stats = conic.solver_stats
    iterations = int(stats.num_iters) if stats and stats.num_iters is not None else 0
    status = str(conic.status)
    value = th.value
    if status not in ACCEPTED_STATUS or value is None:
        return _Conic(None, None, status, iterations)
    duals = None
    if constraints:
        raw = constraints[0].dual_value
        # Multipliers of the scaled rows, mapped back to the unscaled ones.
        duals = np.maximum(np.asarray(raw, dtype=np.float64).reshape(-1), 0.0)
        duals = duals / row_scale
    return _Conic(np.asarray(value, dtype=np.float64), duals, status, iterations)


def _shift_feasible(problem: Problem, theta: Floats) -> Floats:
    """Raise θ₀ until λ ≥ 0 at every node (the intercept column is all ones)."""
    out = theta.copy()
    for _ in range(60):
        low = float(np.min(_lin(problem.nodes_t, out))) if problem.nodes_t.size else 0.0
        if low >= 0.0:
            return out
        out[0] += max(-2.0 * low, 1e-300)
    return out


def _worst(problem: Problem, z: Floats, candidates: Ints) -> Ints:
    """At most ``_MAX_NEW_CUTS`` of ``candidates``: the most violated relative
    to their row's size, ties to the lower index.

    Handing every violator to Clarabel at once (tens of thousands of nearly
    parallel rows) made it fail with a solver error; the few most violated
    rows carry the binding constraints, and later rounds add any others.
    """
    if candidates.size <= _MAX_NEW_CUTS:
        return candidates
    size = np.max(np.abs(problem.nodes_t[:, candidates]), axis=0)
    rel = z[candidates] / np.where(size > 0.0, size, 1.0)
    order = np.lexsort((candidates, rel))
    out: Ints = np.sort(candidates[order[:_MAX_NEW_CUTS]])
    return out


def _cutting_planes(
    problem: Problem, start: Ints, max_iter: int
) -> tuple[_Conic, Ints]:
    active = start
    iterations = 0
    status = cp.OPTIMAL
    for _ in range(_MAX_CUT_ROUNDS):
        sol = _clarabel(problem, active, max_iter)
        iterations += sol.iterations
        if sol.status != cp.OPTIMAL:
            status = sol.status
        if sol.theta is None:
            return _Conic(None, None, sol.status, iterations), active
        zn = _lin(problem.nodes_t, sol.theta)
        tol = _FEAS_TOL * max(1.0, abs(float(sol.theta[0])))
        violators = np.flatnonzero(zn < -tol).astype(np.intp)
        worst = -float(np.min(zn)) if zn.size else 0.0
        # Violations at the solver's own accuracy are absorbed by the θ₀ shift
        # (the certificate then prices them); cutting them off would chase
        # Clarabel's feasibility tolerance node by node.
        if violators.size == 0 or worst <= _ABSORB * tol / _FEAS_TOL:
            return _Conic(sol.theta, sol.duals, status, iterations), active
        active = np.union1d(active, _worst(problem, zn, violators)).astype(np.intp)
    return _Conic(None, None, "cut_rounds", iterations), active


# --------------------------------------------------------------------------
# Certificate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Cert:
    primal: float
    dual: float
    gap: float
    residual: float
    ok: bool


def _failed(primal: float) -> _Cert:
    return _Cert(primal, -math.inf, math.inf, math.inf, False)


def _primal_terms(
    problem: Problem, theta: Floats, zn: Floats, ze: Floats
) -> tuple[Floats, Floats]:
    """The addends of ``F(θ)``, compensator part and event part."""
    match problem.link:
        case Link.IDENTITY:
            return problem.integrals * theta, -np.log(ze)
        case Link.EXP:
            return problem.weights * np.exp(zn), -ze
        case Link.SOFTPLUS:
            return problem.weights * _softplus(zn), -_log_softplus(ze)


def _conjugate(link: Link, u: Floats, w: Floats) -> tuple[Floats, bool]:
    """``h*(u)`` of the corrected rows, and whether u is in its domain."""
    match link:
        case Link.IDENTITY:  # h = -log z on events: h*(u) = -1 - log(-u), u < 0
            if u.size and float(np.max(u)) >= 0.0:
                return u, False
            out: Floats = -1.0 - np.log(-u)
            return out, True
        case Link.EXP:  # h = w eᶻ on nodes: h*(u) = u log(u/w) - u, u ≥ 0
            low = float(np.min(u)) if u.size else 1.0
            if low < 0.0:
                return u, False
            if low > 0.0:  # the usual case: no zero to guard, in place
                out = u / w
                np.log(out, out=out)
                np.subtract(out, 1.0, out=out)
                np.multiply(out, u, out=out)
                return out, True
            pos = u > 0.0
            safe = np.where(pos, u, 1.0)
            out = np.where(pos, u * (np.log(safe / w) - 1.0), 0.0)
            return out, True
        case Link.SOFTPLUS:  # h = w·sp on nodes: w[rho log rho + (1-rho) log(1-rho)]
            rho = u / w
            if u.size and (float(np.min(rho)) < 0.0 or float(np.max(rho)) > 1.0):
                return u, False
            out = w * (special.xlogy(rho, rho) + special.xlogy(1.0 - rho, 1.0 - rho))
            return out, True


def _residual(
    parts: list[tuple[Floats, Floats]], const: Floats
) -> tuple[Floats, Floats, Floats]:
    """The dual residual ``const + Σ Rᵀu`` over parts, the size of its terms
    ``|const| + Σ |R|ᵀ|u|``, and a rigorous bound on the residual's rounding.

    Each part's sums are one fused pass (:func:`_row_sums`); parts are then
    added to ``const`` in order. Every term passes through one rounded
    product, at most :func:`reductions.pairwise_depth` adds in its part's
    tree and at most ``len(parts)`` adds after it: ``K`` roundings in all, so
    the error is at most ``gamma_K · Σ|terms|`` (``gamma_K = K·u/(1 - K·u)``)
    and the exact size at most ``size / (1 - gamma_K)``. ``2·K·u·size``
    exceeds ``gamma_K/(1 - gamma_K)·size`` for every K below 10¹⁴, with room
    for the rounding of the bound itself. (The previous bound,
    ``(log₂ m + 2)·u·size``, left out the product's rounding and the adds
    across parts.)
    """
    residual = const.copy()
    size = np.abs(const)
    depth = 0
    for rows_t, d in parts:
        g, _, a = _row_sums(rows_t, d, None, absolute=True)
        if a is None:
            raise InnerSolveError("absolute sums were not formed")
        residual = residual + g
        size = size + a
        depth = max(depth, reductions.pairwise_depth(rows_t.shape[1]))
    rounds = 1 + depth + len(parts)
    bound = 2.0 * rounds * _UNIT_ROUNDOFF * size
    return residual, size, bound


def _relative(residual: Floats, size: Floats) -> float:
    return float(np.max(np.abs(residual) / np.maximum(size, 1.0)))


def _upward_dot(a: Floats, b: Floats) -> float:
    """An upper bound on ``aᵀb`` for non-negative a, b (every rounding upward)."""
    prods = np.nextafter(a * b, math.inf)
    return math.nextafter(reductions.ExactSum.of(prods).value(), math.inf)


def _certify(
    problem: Problem,
    theta: Floats,
    counts: Floats,
    active: Ints | None = None,
    nu: Floats | None = None,
    metric: Floats | None = None,
    point: _Point | None = None,
) -> _Cert:
    """The certificate at θ (module docstring). ``metric`` is the Hessian of
    the corrected rows, if the caller already has it near θ; any PSD matrix
    gives a valid certificate, since the residual left is measured. ``point``
    is :func:`_at` of this very θ, if the caller has it."""
    link = problem.link
    if point is None or point.theta is not theta:
        point = None
    zn = point.zn if point is not None else None
    if zn is None:
        zn = _lin(problem.nodes_t, theta)
    ze = point.ze if point is not None else _lin(problem.events_t, theta)
    if link is Link.IDENTITY:
        if ze.size and float(np.min(ze)) <= 0.0:
            return _failed(math.inf)
        if zn.size and float(np.min(zn)) < 0.0:
            return _failed(math.inf)
    if link is Link.EXP and zn.size and float(np.max(zn)) > _MAX_EXP:
        return _failed(math.inf)
    if link is Link.EXP and point is not None and point.en is not None:
        comp, ev = point.en, -ze  # the same expressions as _primal_terms
    else:
        comp, ev = _primal_terms(problem, theta, zn, ze)
    primal_sum = reductions.ExactSum.of(comp) + reductions.ExactSum.of(ev)
    primal = primal_sum.value()

    # Rows whose dual is corrected (rows_t, h', h''), and fixed parts.
    fixed: list[tuple[Floats, Floats]] = []
    fixed_conj: list[Floats] = []
    match link:
        case Link.IDENTITY:
            rows_t = problem.events_t
            u = -1.0 / ze
            curv = 1.0 / (ze * ze)
            const = problem.integrals
            if active is not None and nu is not None and active.size:
                fixed.append((problem.nodes_t[:, active], -nu))  # h* = 0 there
        case Link.EXP:
            rows_t = problem.nodes_t
            u = comp  # w·exp(zn), the same array the primal summed
            curv = u
            const = -counts
        case Link.SOFTPLUS:
            rows_t = problem.nodes_t
            s = special.expit(zn)
            u = problem.weights * s
            curv = u * (1.0 - s)
            const = np.zeros(problem.p)
            ue = _neg_log_softplus_d1(ze)
            fixed.append((problem.events_t, ue))
            fixed_conj.append(ue * ze + _log_softplus(ze))  # e*(e'(z)), exact
    if metric is None:
        _, metric, _ = _row_sums(rows_t, None, curv)
        if metric is None:
            raise InnerSolveError("the dual metric was not formed")
    residual, size, bound = _residual([(rows_t, u), *fixed], const)
    rel = _relative(residual, size)
    for _ in range(_DUAL_ROUNDS):
        if rel <= _RESIDUAL_FLOOR:
            break
        delta = _psd_solve(metric, residual)
        u = u - curv * _lin(rows_t, delta)
        residual, size, bound = _residual([(rows_t, u), *fixed], const)
        rel = _relative(residual, size)
    conj, in_domain = _conjugate(link, u, problem.weights)
    if not in_domain:
        return _Cert(primal, -math.inf, math.inf, rel, False)
    conj_sum = reductions.ExactSum.of(conj)
    for c in fixed_conj:
        conj_sum = conj_sum + reductions.ExactSum.of(c)
    dual = (-conj_sum).value()
    # F(θ) ≥ D(u) + rᵀθ for every θ (the Lagrangian bound), so
    # F(θ̂) - F* ≤ F(θ̂) - D(u) + |r|ᵀ|θ*|, with θ* ≈ θ̂ at a certified point.
    # r is a pairwise fold: pad it by that fold's error bound, and round the
    # whole term upward, so it stays an upper bound.
    pad = np.nextafter(np.abs(residual) + bound, math.inf)
    slack = _upward_dot(pad, np.abs(theta))
    gap_sum = primal_sum + conj_sum + reductions.ExactSum.of(np.array([slack]))
    gap = gap_sum.value()
    floor = -_GAP_FLOOR_REL * max(1.0, abs(primal))
    return _Cert(primal, dual, gap, rel, rel <= RESIDUAL_TOL_REL and gap >= floor)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _solution(
    theta: Floats,
    solver: str,
    status: str,
    iterations: int,
    cert: _Cert,
    gap_tol_rel: float,
) -> Solution:
    scale = max(1.0, abs(cert.primal)) if math.isfinite(cert.primal) else 1.0
    gap_rel = cert.gap / scale
    certified = (
        status in ACCEPTED_STATUS
        and cert.ok
        and math.isfinite(cert.gap)
        and gap_rel <= gap_tol_rel
    )
    return Solution(
        theta=tuple(float(x) for x in theta),
        solver=solver,
        status=status,
        iterations=iterations,
        primal=cert.primal,
        dual=cert.dual,
        gap=cert.gap,
        gap_rel=gap_rel,
        residual=cert.residual,
        certified=certified,
    )


def _failure(problem: Problem, solver: str, status: str, iterations: int) -> Solution:
    theta = initial_theta(problem)
    return Solution(
        theta=tuple(float(x) for x in theta),
        solver=solver,
        status=status,
        iterations=iterations,
        primal=math.inf,
        dual=-math.inf,
        gap=math.inf,
        gap_rel=math.inf,
        residual=math.inf,
        certified=False,
    )


def solve(
    problem: Problem, *, solver: str, max_iter: int, gap_tol_rel: float
) -> Solution:
    """Minimise ``F`` at this ψ and certify the result (module docstring)."""
    if problem.nodes_t.shape[0] != problem.events_t.shape[0] or problem.p < 1:
        raise InnerSolveError("node and event designs disagree on the columns")
    counts = np.zeros(problem.p)
    if problem.link is Link.EXP:  # Σᵢ xᵢ: the exp link's linear term, exactly
        n = problem.events_t.shape[1]
        counts = _exact_colsums([(problem.events_t, np.ones(n))], counts)
    nodes = problem.nodes_t
    unit_first = nodes.shape[1] > 0 and bool(np.all(nodes[0] == 1.0))
    if solver == "newton":
        primal = _newton(problem, max_iter, counts, unit_first)
        if problem.link is not Link.IDENTITY:
            cert = _certify(
                problem, primal.theta, counts, metric=primal.metric, point=primal.point
            )
            return _solution(
                primal.theta,
                "newton",
                primal.status,
                primal.iterations,
                cert,
                gap_tol_rel,
            )
        if primal.status == "optimal":
            zn = _lin(problem.nodes_t, primal.theta)
            tol = _FEAS_TOL * max(1.0, abs(float(primal.theta[0])))
            low = float(np.min(zn)) if zn.size else 0.0
            if low >= -tol:
                theta = _shift_feasible(problem, primal.theta)
                cert = _certify(problem, theta, counts, metric=primal.metric)
                return _solution(
                    theta, "newton", "optimal", primal.iterations, cert, gap_tol_rel
                )
            start = _worst(problem, zn, np.flatnonzero(zn < -tol).astype(np.intp))
        else:
            m = problem.nodes_t.shape[1]
            start = np.unique(np.linspace(0, m - 1, min(m, 512)).astype(np.intp))
        offset = primal.iterations
        label = "newton+clarabel"
    elif solver == "clarabel":
        if problem.link is Link.SOFTPLUS:
            raise InnerSolveError("log∘softplus is not DCP; use the Newton solver")
        start = np.empty(0, dtype=np.intp)
        offset = 0
        label = "clarabel"
    else:
        raise InnerSolveError(f"unknown inner solver {solver!r}")

    if problem.link is Link.EXP:
        conic = _clarabel(problem, start, max_iter)
        if conic.theta is None:
            return _failure(problem, label, conic.status, conic.iterations)
        cert = _certify(problem, conic.theta, counts)
        return _solution(
            conic.theta, label, conic.status, conic.iterations, cert, gap_tol_rel
        )
    conic, active = _cutting_planes(problem, start, max_iter)
    if conic.theta is None:
        return _failure(problem, label, conic.status, offset + conic.iterations)
    theta = _shift_feasible(problem, conic.theta)
    cert = _certify(problem, theta, counts, active, conic.duals)
    return _solution(
        theta, label, conic.status, offset + conic.iterations, cert, gap_tol_rel
    )
