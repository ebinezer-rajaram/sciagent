"""The group-lasso solver behind B-sparse, on a working set of groups.

Minimises, over an unpenalised intercept θ₀ and group coefficients β,

    P(θ₀, β) = Σ_q w_q sp(η_q) - Σᵢ log sp(ηᵢ) + λ Σ_g ω_g ‖β_g‖,
    η = θ₀ + x̃ᵀβ,  sp(z) = log(1 + e^z),

the softplus-link negative log-likelihood, with its compensator on the
quadrature rule (SPEC §2.2), plus the group-lasso penalty. Both terms are
convex (``log ∘ softplus`` is concave; ``fit_solve.py``), so P is convex.
``sparse.py`` says why the link is softplus.

**Outer: proximal Newton** (Lee, Sun & Saunders 2014). At the iterate x, form
the gradient g and Hessian ``H = X̃ᵀ diag(w e^η) X̃`` (intercept included),
minimise the model ``q(z) = gᵀ(z-x) + ½(z-x)ᵀH(z-x) + λΣω_g‖z_g‖``, and take
an Armijo backtracking step along ``z - x`` on ``P``, with the model's
predicted decrease ``gᵀ(z-x) + pen(z) - pen(x)`` as the slope. It converges
quadratically near the solution: three to six outer steps per λ were
measured. Every operation on the ~10⁵ nodes happens here, once per step.

**Inner: active-set Newton on the model** (Roth & Fischer 2008, §4). Every
operation is on ``(p+1)``-vectors and ``(p+1)²`` matrices. On the set A of
non-zero groups the penalty is smooth, so q restricted to A is minimised by
damped Newton. The Hessian is H plus each group's penalty curvature
``λω_g (I - uuᵀ)/‖z_g‖`` with ``u = z_g/‖z_g‖``. The active set changes one
group at a time:

- **leave**: an active group is zeroed when its exact block test
  ``‖∇_g q - H_gg z_g‖ ≤ λω_g`` holds (q is quadratic in each block, so the
  block's minimiser is then zero);
- **enter**: once the active problem is solved, the zero group that most
  violates ``‖∇_g q‖ ≤ λω_g`` enters with its block proximal-gradient step
  ``-(1 - λω_g/‖∇_g q‖) ∇_g q / L_g`` (``L_g`` the largest eigenvalue of
  ``H_gg``), backtracked on q.

Coordinate descent (glmnet's method) was tried first. Products of one
``Excite`` at neighbouring ψ are correlated above 0.99, and block coordinate
descent then needed 10⁴-10⁵ Python-level group updates per solve, tens of
seconds on a 600-group toy dictionary. Entering every violator at once was
tried too: correlated groups then left one by one. Newton on a growing active
set is insensitive to the correlation.

**Stopping** is on the optimality conditions themselves, not on the objective:
``|∂P/∂θ₀| ≤ tol · n`` and, for each group, ``‖∇_g + λω_g β_g/‖β_g‖‖ ≤ tol ·
λω_g`` if ``β_g ≠ 0``, else ``‖∇_g‖ ≤ (1 + tol) λω_g``.
:attr:`LassoSolution.stationarity` is the largest of these relative
residuals.

Arithmetic is numpy and BLAS: deterministic run to run on one machine, which
is all the path needs. No reported number comes from here (``sparse.py``).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy import linalg, special

from sciagent.glm.data import Floats
from sciagent.systems.v2.sparse_dictionary import SparseError

type Ints = npt.NDArray[np.intp]

#: Below this, softplus terms use their series (``log`` of a denormal).
_SERIES: Final = -30.0
_ARMIJO: Final = 1e-4
_MIN_STEP: Final = 1e-12
#: Relative eigenvalue cutoff of the Newton system. Duplicate columns inside
#: a group (sin·cos twice in a product of two Periodic atoms) make it
#: singular along directions the objective does not depend on.
_EIG_CUTOFF: Final = 1e-12
#: An inner Newton step that lowers the model by less than this (relative)
#: is a stall; after ``_MAX_STALLS`` in a row the active problem is solved.
_STALL: Final = 1e-14
#: A Newton step "passes through zero" in a group when its closest approach
#: to the origin is within this fraction of the group's current norm.
_CROSS_TOL: Final = 1e-6
_MAX_STALLS: Final = 3


@dataclass(frozen=True, eq=False)
class WorkingProblem:
    """The working set's standardised node columns and what the objective needs.

    ``bounds`` has ``G + 1`` entries: group k owns columns
    ``bounds[k]:bounds[k+1]`` of ``nodes``; every group has a column.
    """

    nodes: Floats  # (M, p)
    weights: Floats  # (M,)
    events: Floats  # (n, p)
    bounds: Ints
    omega: Floats  # (G,)

    @property
    def p(self) -> int:
        return int(self.nodes.shape[1])

    @property
    def n_groups(self) -> int:
        return int(self.omega.size)

    @property
    def n_events(self) -> float:
        return float(self.events.shape[0])


@dataclass(frozen=True, eq=False)
class LassoSolution:
    intercept: float
    beta: Floats
    eta: Floats  # at the nodes
    eta_events: Floats
    objective: float
    iterations: int
    converged: bool
    stationarity: float


class _Groups:
    """Vectorised group operations on ``(p+1)``-vectors (index 0: intercept)."""

    def __init__(self, problem: WorkingProblem, lam: float) -> None:
        b = problem.bounds
        self.starts = b[:-1]
        self.widths = np.diff(b)
        self.slices = [
            slice(int(b[k]) + 1, int(b[k + 1]) + 1) for k in range(b.size - 1)
        ]
        self.bounds = lam * problem.omega
        self.n = problem.n_events

    def norms(self, v: Floats) -> Floats:
        if self.starts.size == 0:
            return np.empty(0)
        out: Floats = np.sqrt(np.add.reduceat(v[1:] * v[1:], self.starts))
        return out

    def penalty(self, z: Floats) -> float:
        return float(self.bounds @ self.norms(z))

    def residuals(self, z: Floats, grad: Floats) -> tuple[float, Floats, Bools]:
        """(active residual incl. intercept, per-group residual, active mask)."""
        nb = self.norms(z)
        active = nb > 0.0
        safe = np.where(active, nb, 1.0)
        r = (
            grad[1:]
            + np.repeat(np.where(active, self.bounds / safe, 0.0), self.widths) * z[1:]
        )
        rel = np.where(
            active, self.norms(np.concatenate([[0.0], r])) / self.bounds, 0.0
        )
        rel = np.where(active, rel, self.norms(grad) / self.bounds - 1.0)
        act = abs(float(grad[0])) / max(1.0, self.n)
        if active.any():
            act = max(act, float(np.max(rel[active])))
        return act, rel, active


type Bools = npt.NDArray[np.bool_]


# --------------------------------------------------------------------------
# The softplus link
# --------------------------------------------------------------------------


def softplus(z: Floats) -> Floats:
    out: Floats = np.logaddexp(0.0, z)
    return out


def log_softplus(z: Floats) -> Floats:
    """``log softplus(z)``, by its series ``z - e^z/2`` far below zero."""
    out = np.array(z, dtype=np.float64)
    big = z > _SERIES
    out[big] = np.log(np.logaddexp(0.0, z[big]))
    small = ~big
    out[small] = z[small] - 0.5 * np.exp(z[small])
    return out


def event_rate_ratio(z: Floats) -> Floats:
    """``-d/dz log softplus(z) ·(-1) = expit(z)/softplus(z)`` ∈ (0, 1]."""
    out = np.empty_like(z)
    big = z > _SERIES
    out[big] = special.expit(z[big]) / np.logaddexp(0.0, z[big])
    small = ~big
    out[small] = 1.0 - 0.5 * np.exp(z[small])
    return out


def neg_log_softplus_d2(z: Floats) -> Floats:
    """``d²/dz² [-log softplus(z)] = s(s - (1-s)·sp)/sp² ≥ 0``, s = expit(z)."""
    out = np.empty_like(z)
    big = z > _SERIES
    s = special.expit(z[big])
    sp = np.logaddexp(0.0, z[big])
    out[big] = np.maximum(s * (s - (1.0 - s) * sp), 0.0) / (sp * sp)
    small = ~big
    out[small] = 0.5 * np.exp(z[small])
    return out


def _armijo(
    value: Callable[[Floats], float], z: Floats, f0: float, d: Floats, slope: float
) -> tuple[Floats, float] | None:
    t = 1.0
    while t >= _MIN_STEP:
        zt = z + t * d
        ft = value(zt)
        if ft <= f0 + _ARMIJO * t * slope:
            return zt, ft
        t *= 0.5
    return None


# --------------------------------------------------------------------------
# The inner problem: the quadratic model, by active-set Newton
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Model:
    g: Floats
    h: Floats
    x: Floats
    groups: _Groups

    def value(self, z: Floats) -> float:
        d = z - self.x
        return (
            float(self.g @ d) + 0.5 * float(d @ (self.h @ d)) + self.groups.penalty(z)
        )

    def grad(self, z: Floats) -> Floats:
        out: Floats = self.g + self.h @ (z - self.x)
        return out


def _psd_solve(h: Floats, b: Floats) -> Floats:
    """``h⁻¹b`` by Cholesky; by a pseudo-inverse if ``h`` is not numerically
    positive definite."""
    try:
        chol = linalg.cho_factor(h, lower=True, check_finite=False)
    except linalg.LinAlgError:
        chol = None
    if chol is not None:
        diag = np.abs(np.diag(chol[0]))
        if float(np.min(diag)) > _EIG_CUTOFF * float(np.max(diag)):
            out: Floats = linalg.cho_solve(chol, b, check_finite=False)
            return out
    vals, vecs = np.linalg.eigh(h)
    keep = vals > _EIG_CUTOFF * max(float(vals[-1]), 1e-300)
    out = vecs[:, keep] @ ((vecs[:, keep].T @ b) / vals[keep])
    return out


def _newton_direction(
    m: _Model, grad: Floats, z: Floats, active: list[int]
) -> tuple[Floats, float]:
    """Newton direction of the smooth active problem and its slope."""
    gr = m.groups
    cols = [0]
    for k in active:
        cols.extend(range(gr.slices[k].start, gr.slices[k].stop))
    hh = m.h[np.ix_(cols, cols)].copy()
    gg = grad[cols].copy()
    pos = 1
    for k in active:
        s = gr.slices[k]
        w = s.stop - s.start
        b = z[s]
        nb = float(np.sqrt(b @ b))
        u = b / nb
        bound = float(gr.bounds[k])
        gg[pos : pos + w] += bound * u
        hh[pos : pos + w, pos : pos + w] += (bound / nb) * (np.eye(w) - np.outer(u, u))
        pos += w
    step = _psd_solve(hh, -gg)
    d = np.zeros_like(z)
    d[cols] = step
    return d, float(gg @ step)


def _crossing_step(
    m: _Model, z: Floats, f: float, d: Floats, slope: float, active: list[int]
) -> tuple[Floats, float] | None:
    """Stop at the first active group the Newton step drives through zero.

    The smooth active problem continues ``‖z_g‖`` linearly through the
    origin, but the penalty has a kink there, so a step that crosses it is
    rejected by the line search and Newton stalls. When ``z_g + t d_g`` passes
    (to rounding) through zero at some ``t ≤ 1``, the step is cut at the first
    such ``t`` and that group set to exactly zero: it leaves the active set
    (Roth & Fischer 2008's active-set rule). None if no group crosses or the
    cut step does not decrease q.
    """
    first: tuple[float, int] | None = None
    for k in active:
        sl = m.groups.slices[k]
        zg, dg = z[sl], d[sl]
        inner = float(zg @ dg)
        dd = float(dg @ dg)
        if inner >= 0.0 or dd == 0.0:
            continue
        t = -inner / dd
        if t > 1.0:
            continue
        miss = float(np.sqrt(np.sum((zg + t * dg) ** 2)))
        if miss <= _CROSS_TOL * float(np.sqrt(zg @ zg)) and (
            first is None or t < first[0]
        ):
            first = (t, k)
    if first is None:
        return None
    t, k = first
    zt = z + t * d
    zt[m.groups.slices[k]] = 0.0
    ft = m.value(zt)
    if ft <= f + _ARMIJO * t * slope:
        return zt, ft
    return None


def _leave(m: _Model, grad: Floats, z: Floats, active: list[int]) -> int | None:
    """The active group whose exact block test for zero passes best, if any."""
    best: tuple[float, int] | None = None
    for k in active:
        s = m.groups.slices[k]
        c = grad[s] - m.h[s, s] @ z[s]
        ratio = float(np.sqrt(c @ c)) / float(m.groups.bounds[k])
        if ratio <= 1.0 and (best is None or ratio < best[0]):
            best = (ratio, k)
    return None if best is None else best[1]


def _enter_direction(
    m: _Model, grad: Floats, z: Floats, k: int
) -> tuple[Floats, float]:
    s = m.groups.slices[k]
    g = grad[s]
    ng = float(np.sqrt(g @ g))
    bound = float(m.groups.bounds[k])
    lips = float(np.linalg.eigvalsh(m.h[s, s])[-1])
    d = np.zeros_like(z)
    if lips <= 0.0:
        return d, 0.0
    dk = -(1.0 - bound / ng) * g / lips
    d[s] = dk
    # Directional derivative of q at z_g = 0 along d: gᵀd + λω‖d‖.
    return d, float(g @ dk) + bound * float(np.sqrt(dk @ dk))


def _solve_model(m: _Model, tol: float, max_iter: int) -> Floats:
    """Minimise the quadratic model by active-set Newton (module docstring)."""
    z = m.x.copy()
    f = m.value(z)
    stalls = 0
    for _ in range(max_iter):
        grad = m.grad(z)
        act, rel, mask = m.groups.residuals(z, grad)
        active = [int(k) for k in np.flatnonzero(mask)]
        out = np.where(mask, -np.inf, rel)
        worst = int(np.argmax(out)) if out.size else -1
        violated = worst >= 0 and float(out[worst]) > tol
        if act <= tol and not violated:
            break
        if act > tol:
            k = _leave(m, grad, z, active)
            if k is not None:
                z = z.copy()
                z[m.groups.slices[k]] = 0.0
                f = m.value(z)
                continue
            d, slope = _newton_direction(m, grad, z, active)
            if slope < 0.0 and stalls < _MAX_STALLS:
                stepped = _crossing_step(m, z, f, d, slope, active)
                if stepped is None:
                    stepped = _armijo(m.value, z, f, d, slope)
                if stepped is not None:
                    gain = f - stepped[1]
                    stalls = stalls + 1 if gain <= _STALL * max(1.0, abs(f)) else 0
                    z, f = stepped
                    continue
            # The active problem is solved to rounding (a group sitting on
            # the boundary of its zero test makes Newton creep, not stop).
            if not violated:
                break
        d, slope = _enter_direction(m, grad, z, worst)
        if slope >= 0.0:
            break
        stepped = _armijo(m.value, z, f, d, slope)
        if stepped is None:
            break
        z, f = stepped
        stalls = 0
    return z


# --------------------------------------------------------------------------
# The outer problem: proximal Newton
# --------------------------------------------------------------------------


class _State:
    """Iterate, η (nodes and events), gradient and objective, kept consistent."""

    def __init__(self, problem: WorkingProblem, lam: float, x: Floats) -> None:
        self.problem = problem
        self.groups = _Groups(problem, lam)
        self.set(x)

    def value(self, x: Floats) -> tuple[float, Floats, Floats]:
        pr = self.problem
        eta = x[0] + pr.nodes @ x[1:]
        eta_ev = x[0] + pr.events @ x[1:]
        smooth = float(pr.weights @ softplus(eta)) - float(np.sum(log_softplus(eta_ev)))
        return smooth + self.groups.penalty(x), eta, eta_ev

    def set(
        self, x: Floats, cached: tuple[float, Floats, Floats] | None = None
    ) -> None:
        obj, eta, eta_ev = cached if cached is not None else self.value(x)
        if not math.isfinite(obj):
            raise SparseError("the objective is not finite")
        pr = self.problem
        self.x = x
        self.obj = obj
        self.eta = eta
        self.eta_ev = eta_ev
        self.node_d1 = pr.weights * special.expit(eta)
        self.event_d1 = event_rate_ratio(eta_ev)
        g = np.empty(x.size)
        g[0] = float(np.sum(self.node_d1)) - float(np.sum(self.event_d1))
        g[1:] = pr.nodes.T @ self.node_d1 - pr.events.T @ self.event_d1
        self.grad = g

    def stationarity(self) -> float:
        act, rel, mask = self.groups.residuals(self.x, self.grad)
        inactive = rel[~mask]
        return max(act, float(np.max(inactive)) if inactive.size else -math.inf)

    def hessian(self) -> Floats:
        pr = self.problem
        sig = special.expit(self.eta)
        dn = pr.weights * sig * (1.0 - sig)
        de = neg_log_softplus_d2(self.eta_ev)
        h = np.empty((pr.p + 1, pr.p + 1))
        h[0, 0] = float(np.sum(dn)) + float(np.sum(de))
        cross = pr.nodes.T @ dn + pr.events.T @ de
        h[0, 1:] = cross
        h[1:, 0] = cross
        h[1:, 1:] = pr.nodes.T @ (pr.nodes * dn[:, None]) + pr.events.T @ (
            pr.events * de[:, None]
        )
        return h

    def try_step(self, d: Floats, slope: float) -> bool:
        """Armijo backtracking along d on P; True if a step was taken."""
        t = 1.0
        while t >= _MIN_STEP:
            xt = self.x + t * d
            cand = self.value(xt)
            if cand[0] <= self.obj + _ARMIJO * t * slope:
                self.set(xt, cand)
                return True
            t *= 0.5
        return False


def solve_group_lasso(
    problem: WorkingProblem,
    lam: float,
    intercept: float,
    beta: Floats,
    *,
    tol: float = 1e-8,
    max_iter: int = 100,
    max_inner: int = 2_000,
) -> LassoSolution:
    """Proximal Newton from the warm start ``(intercept, beta)`` (module docstring)."""
    state = _State(problem, lam, np.concatenate([[intercept], beta]).astype(np.float64))
    stat = state.stationarity()
    it = 0
    while stat > tol and it < max_iter:
        it += 1
        model = _Model(state.grad, state.hessian(), state.x, state.groups)
        z = _solve_model(model, 0.01 * tol, max_inner)
        d = z - state.x
        pen = state.groups.penalty
        decrease = float(state.grad @ d) + pen(z) - pen(state.x)
        if decrease >= 0.0 or not state.try_step(d, decrease):
            break
        stat = state.stationarity()
    return LassoSolution(
        intercept=float(state.x[0]),
        beta=state.x[1:].copy(),
        eta=state.eta,
        eta_events=state.eta_ev,
        objective=state.obj,
        iterations=it,
        converged=stat <= tol,
        stationarity=stat,
    )
