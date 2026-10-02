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
measured) and its iterations are deterministic: every fold in it is a fixed
pairwise tree (:func:`_fold`) or a fixed sequential sum (:func:`_lin`), never
a CPU-chosen BLAS or SIMD order. The identity link's node constraints are
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
``F(θ̂) - D(u') + |r'|ᵀ|θ̂|``, folded exactly in one :func:`math.fsum`, with r'
padded by its own fold's error bound. It bounds ``F(θ̂) - F*`` to first order
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


def _fold(x: Floats) -> Floats:
    """Sum along the last axis by a fixed pairwise tree.

    Padded with zeros to a power of two, then halved in place: element i is
    always added to element i + half. Every add is an elementwise, correctly
    rounded numpy operation, so the result does not depend on the CPU.
    Pairwise, so the rounding error grows as log₂ of the length.
    """
    n = x.shape[-1]
    size = 1 << max(0, n - 1).bit_length()
    buf = np.zeros((*x.shape[:-1], size))
    buf[..., :n] = x
    while size > 1:
        half = size // 2
        np.add(buf[..., :half], buf[..., half:size], out=buf[..., :half])
        size = half
    out: Floats = buf[..., 0].copy()
    return out


def _lin(rows_t: Floats, theta: Floats) -> Floats:
    """``Rθ`` from the transposed rows, summed column by column in order."""
    out: Floats = rows_t[0] * theta[0]
    tmp = np.empty_like(out)
    for j in range(1, rows_t.shape[0]):
        np.multiply(rows_t[j], theta[j], out=tmp)
        np.add(out, tmp, out=out)
    return out


def _gram(rows_t: Floats, d: Floats) -> Floats:
    """``Rᵀ diag(d) R`` with every entry a :func:`_fold`."""
    p = rows_t.shape[0]
    out = np.zeros((p, p))
    if rows_t.shape[1] == 0:
        return out
    weighted = rows_t * d
    for j in range(p):
        row = _fold(weighted[j:] * rows_t[j])
        out[j, j:] = row
        out[j:, j] = row
    return out


def _colsums(rows_t: Floats, d: Floats) -> Floats:
    """``Rᵀd`` by :func:`_fold`."""
    if rows_t.shape[1] == 0:
        return np.zeros(rows_t.shape[0])
    return _fold(rows_t * d)


def _exact_colsums(parts: list[tuple[Floats, Floats]], const: Floats) -> Floats:
    """``const + Σ Rᵀd`` over ``(rows_t, d)`` parts, each entry one exact fsum."""
    p = const.shape[0]
    out = np.empty(p)
    for j in range(p):
        pieces = [const[j : j + 1], *(rows_t[j] * d for rows_t, d in parts)]
        out[j] = reductions.total(np.concatenate(pieces))
    return out


def _fast_colsums(parts: list[tuple[Floats, Floats]], const: Floats) -> Floats:
    """``const + Σ Rᵀd`` by :func:`_fold`: deterministic, pairwise-accurate.

    Used for the dual residual, which is judged relative to the size of its
    terms (:func:`_abs_colsums`); the pairwise rounding, at most about
    log₂(m)·ε of that size, is six orders below ``RESIDUAL_TOL_REL``, so an
    exact fold would buy nothing there and cost a third of a certificate.
    """
    out = const.copy()
    for rows_t, d in parts:
        out = out + _colsums(rows_t, d)
    return out


def _abs_colsums(parts: list[tuple[Floats, Floats]], const: Floats) -> Floats:
    """``|const| + Σ |R|ᵀ|d|``: the size of the residual's terms."""
    out = np.abs(const)
    for rows_t, d in parts:
        out = out + _colsums(np.abs(rows_t), np.abs(d))
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
class _Terms:
    """``F`` (fast fold), and first / second derivatives per row."""

    value: float
    d_nodes: Floats | None
    h_nodes: Floats | None
    d_events: Floats | None
    h_events: Floats | None


def _value(problem: Problem, theta: Floats, zn: Floats | None, ze: Floats) -> float:
    """``F`` by fast folds; ``inf`` outside the domain."""
    match problem.link:
        case Link.IDENTITY:
            if ze.size and float(np.min(ze)) <= 0.0:
                return math.inf
            lin = float(_fold(problem.integrals * theta))
            return lin - float(_fold(np.log(ze)))
        case Link.EXP:
            if zn is None:
                raise InnerSolveError("the exp link needs node values")
            if zn.size and float(np.max(zn)) > _MAX_EXP:
                return math.inf
            return float(_fold(problem.weights * np.exp(zn))) - float(_fold(ze))
        case Link.SOFTPLUS:
            if zn is None:
                raise InnerSolveError("the softplus link needs node values")
            comp = float(_fold(problem.weights * _softplus(zn)))
            return comp - float(_fold(_log_softplus(ze)))


def _terms(problem: Problem, theta: Floats, zn: Floats | None, ze: Floats) -> _Terms:
    value = _value(problem, theta, zn, ze)
    match problem.link:
        case Link.IDENTITY:
            inv = 1.0 / ze
            return _Terms(value, None, None, -inv, inv * inv)
        case Link.EXP:
            if zn is None:
                raise InnerSolveError("the exp link needs node values")
            en = problem.weights * np.exp(np.minimum(zn, _MAX_EXP))
            return _Terms(value, en, en, -np.ones_like(ze), None)
        case Link.SOFTPLUS:
            if zn is None:
                raise InnerSolveError("the softplus link needs node values")
            s = special.expit(zn)
            dn = problem.weights * s
            return _Terms(
                value,
                dn,
                dn * (1.0 - s),
                _neg_log_softplus_d1(ze),
                _neg_log_softplus_d2(ze),
            )


def _gradient(problem: Problem, t: _Terms) -> Floats:
    g = problem.integrals.copy() if problem.link is Link.IDENTITY else None
    if g is None:
        g = np.zeros(problem.p)
    if t.d_nodes is not None:
        g = g + _colsums(problem.nodes_t, t.d_nodes)
    if t.d_events is not None:
        g = g + _colsums(problem.events_t, t.d_events)
    return g


def _hessian(problem: Problem, t: _Terms) -> Floats:
    h = np.zeros((problem.p, problem.p))
    if t.h_nodes is not None:
        h = h + _gram(problem.nodes_t, t.h_nodes)
    if t.h_events is not None:
        h = h + _gram(problem.events_t, t.h_events)
    return h


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
    measure = reductions.total(problem.weights) if problem.weights.size else 1.0
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
    theta: Floats
    status: str
    iterations: int


def _newton(problem: Problem, max_iter: int) -> _Primal:
    """Damped Newton from :func:`initial_theta`; ``max_iter`` counts steps."""
    theta = initial_theta(problem)
    nodes = _uses_nodes(problem.link)
    for it in range(max_iter + 1):
        zn = _lin(problem.nodes_t, theta) if nodes else None
        ze = _lin(problem.events_t, theta)
        terms = _terms(problem, theta, zn, ze)
        if not math.isfinite(terms.value):
            return _Primal(theta, "numerics", it)
        grad = _gradient(problem, terms)
        step = _psd_solve(_hessian(problem, terms), -grad)
        decrement = -reductions.dot(grad, step)
        if not math.isfinite(decrement):
            return _Primal(theta, "numerics", it)
        if 0.5 * decrement <= NEWTON_TOL_REL * max(1.0, abs(terms.value)):
            return _Primal(theta, "optimal", it)
        if it == max_iter:
            break
        dn = _lin(problem.nodes_t, step) if nodes else None
        de = _lin(problem.events_t, step)
        t = 1.0
        while True:
            trial = theta + t * step
            value = _value(
                problem,
                trial,
                None if zn is None or dn is None else zn + t * dn,
                ze + t * de,
            )
            if value <= terms.value - _ARMIJO * t * decrement:
                break
            t *= _BACKTRACK
            if t < _MIN_STEP:
                return _Primal(theta, "stalled", it)
        theta = trial
    return _Primal(theta, "max_iter", max_iter)


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


def _primal_terms(problem: Problem, theta: Floats, zn: Floats, ze: Floats) -> Floats:
    """The addends of ``F(θ)``, for one exact fold."""
    match problem.link:
        case Link.IDENTITY:
            out: Floats = np.concatenate([problem.integrals * theta, -np.log(ze)])
        case Link.EXP:
            out = np.concatenate([problem.weights * np.exp(zn), -ze])
        case Link.SOFTPLUS:
            out = np.concatenate([problem.weights * _softplus(zn), -_log_softplus(ze)])
    return out


def _conjugate(link: Link, u: Floats, w: Floats) -> tuple[Floats, bool]:
    """``h*(u)`` of the corrected rows, and whether u is in its domain."""
    match link:
        case Link.IDENTITY:  # h = -log z on events: h*(u) = -1 - log(-u), u < 0
            if u.size and float(np.max(u)) >= 0.0:
                return u, False
            out: Floats = -1.0 - np.log(-u)
            return out, True
        case Link.EXP:  # h = w eᶻ on nodes: h*(u) = u log(u/w) - u, u ≥ 0
            if u.size and float(np.min(u)) < 0.0:
                return u, False
            pos = u > 0.0
            safe = np.where(pos, u, 1.0)
            out = np.where(pos, u * (np.log(safe) - np.log(w) - 1.0), 0.0)
            return out, True
        case Link.SOFTPLUS:  # h = w·sp on nodes: w[rho log rho + (1-rho) log(1-rho)]
            rho = u / w
            if u.size and (float(np.min(rho)) < 0.0 or float(np.max(rho)) > 1.0):
                return u, False
            out = w * (special.xlogy(rho, rho) + special.xlogy(1.0 - rho, 1.0 - rho))
            return out, True


def _certify(
    problem: Problem,
    theta: Floats,
    active: Ints | None = None,
    nu: Floats | None = None,
) -> _Cert:
    link = problem.link
    zn = _lin(problem.nodes_t, theta)
    ze = _lin(problem.events_t, theta)
    if link is Link.IDENTITY:
        if ze.size and float(np.min(ze)) <= 0.0:
            return _failed(math.inf)
        if zn.size and float(np.min(zn)) < 0.0:
            return _failed(math.inf)
    if link is Link.EXP and zn.size and float(np.max(zn)) > _MAX_EXP:
        return _failed(math.inf)
    primal_terms = _primal_terms(problem, theta, zn, ze)
    primal = reductions.total(primal_terms)

    # Rows whose dual is corrected (rows_t, z, h', h''), and fixed parts.
    fixed: list[tuple[Floats, Floats]] = []
    fixed_conj: list[Floats] = []
    match link:
        case Link.IDENTITY:
            rows_t, z = problem.events_t, ze
            u = -1.0 / z
            curv = 1.0 / (z * z)
            const = problem.integrals
            if active is not None and nu is not None and active.size:
                fixed.append((problem.nodes_t[:, active], -nu))  # h* = 0 there
        case Link.EXP:
            rows_t, z = problem.nodes_t, zn
            u = problem.weights * np.exp(zn)
            curv = u.copy()
            n = problem.events_t.shape[1]
            const = -_exact_colsums(
                [(problem.events_t, np.ones(n))], np.zeros(problem.p)
            )
        case Link.SOFTPLUS:
            rows_t, z = problem.nodes_t, zn
            s = special.expit(zn)
            u = problem.weights * s
            curv = u * (1.0 - s)
            const = np.zeros(problem.p)
            ue = _neg_log_softplus_d1(ze)
            fixed.append((problem.events_t, ue))
            fixed_conj.append(ue * ze + _log_softplus(ze))  # e*(e'(z)), exact
    del z
    metric = _gram(rows_t, curv)
    for _ in range(_DUAL_ROUNDS):
        residual = _fast_colsums([(rows_t, u), *fixed], const)
        size = _abs_colsums([(rows_t, u), *fixed], const)
        rel = float(np.max(np.abs(residual) / np.maximum(size, 1.0)))
        if rel <= _RESIDUAL_FLOOR:
            break
        delta = _psd_solve(metric, residual)
        u = u - curv * _lin(rows_t, delta)
    residual = _fast_colsums([(rows_t, u), *fixed], const)
    size = _abs_colsums([(rows_t, u), *fixed], const)
    rel = float(np.max(np.abs(residual) / np.maximum(size, 1.0)))
    conj, in_domain = _conjugate(link, u, problem.weights)
    if not in_domain:
        return _Cert(primal, -math.inf, math.inf, rel, False)
    dual_terms = -np.concatenate([conj, *fixed_conj])
    dual = reductions.total(dual_terms)
    # F(θ) ≥ D(u) + rᵀθ for every θ (the Lagrangian bound), so
    # F(θ̂) - F* ≤ F(θ̂) - D(u) + |r|ᵀ|θ*|, with θ* ≈ θ̂ at a certified point.
    # The residual itself is a pairwise fold: pad it by that fold's error bound
    # so the term stays an upper bound.
    length = max(rows_t.shape[1] + sum(r.shape[1] for r, _ in fixed), 2)
    fold_error = (math.log2(length) + 2.0) * _UNIT_ROUNDOFF * size
    slack = reductions.dot(np.abs(residual) + fold_error, np.abs(theta))
    gap = reductions.total(np.concatenate([primal_terms, -dual_terms, [slack]]))
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
    if solver == "newton":
        primal = _newton(problem, max_iter)
        if problem.link is not Link.IDENTITY:
            cert = _certify(problem, primal.theta)
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
                cert = _certify(problem, theta)
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
        cert = _certify(problem, conic.theta)
        return _solution(
            conic.theta, label, conic.status, conic.iterations, cert, gap_tol_rel
        )
    conic, active = _cutting_planes(problem, start, max_iter)
    if conic.theta is None:
        return _failure(problem, label, conic.status, offset + conic.iterations)
    theta = _shift_feasible(problem, conic.theta)
    cert = _certify(problem, theta, active, conic.duals)
    return _solution(
        theta, label, conic.status, offset + conic.iterations, cert, gap_tol_rel
    )
