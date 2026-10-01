"""The exact log-likelihood of a point-process GLM (SPEC §2.2).

``log L(θ) = Σᵢ log λ(tᵢ) - ∫₀ᵀ λ(t) dt`` with ``λ = g(Xθ)``.

- **Identity link.** The compensator is linear in θ, ``θ · ∫X``, and uses the
  design's column integrals, which are closed form for every column not under
  a ``Product`` (:attr:`~sciagent.glm.features.Design.integral_exact`). A
  non-positive intensity at any event gives ``-inf``; the fitter owns
  feasibility.
- **Exp and softplus links.** ``∫ g(Xθ)`` is not linear in per-column
  integrals, so it is the design's composite Gauss-Legendre rule.
  :func:`compensator_error` estimates that rule's error a posteriori (the rule
  against one with doubled nodes per panel), so a fit can be certified up to a
  stated quadrature error.

Every fold that reaches a returned number goes through
:mod:`sciagent.core.reductions` (exactly rounded), so values are
byte-identical across runs.
"""

from __future__ import annotations

import math
from typing import Final

import numpy as np
import numpy.typing as npt

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError
from sciagent.glm.data import EventLog, Floats
from sciagent.glm.features import (
    DEFAULT_QUADRATURE,
    Data,
    Design,
    PsiAssignment,
    QuadratureSpec,
    cumulative_columns,
    evaluate_columns,
    has_closed_form,
    quadrature_rule,
)
from sciagent.glm.grammar import ChannelSpec, Link, Structure, n_columns

#: Above this, ``exp`` overflows float64.
_MAX_EXP: Final = 709.0
#: Below this, ``log(softplus(η))`` is computed by its series ``η - e^η / 2``.
_SOFTPLUS_SERIES: Final = -30.0


class LikelihoodError(SciAgentError):
    """A likelihood was requested with inconsistent or non-finite inputs."""


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------


def _softplus(eta: Floats) -> Floats:
    out: Floats = np.logaddexp(0.0, eta)
    return out


def _log_softplus(eta: Floats) -> Floats:
    out = np.array(eta, dtype=np.float64)
    big = eta > _SOFTPLUS_SERIES
    out[big] = np.log(np.logaddexp(0.0, eta[big]))
    small = ~big
    out[small] = eta[small] - 0.5 * np.exp(eta[small])
    return out


def _theta(theta: npt.ArrayLike, p: int) -> Floats:
    th = np.array(theta, dtype=np.float64).reshape(-1)
    if th.shape != (p,):
        raise LikelihoodError(f"θ has {th.size} entries; the design has {p} columns")
    if not np.all(np.isfinite(th)):
        raise LikelihoodError(f"θ is not finite: {th}")
    return th


def _closed_form_columns(structure: Structure) -> npt.NDArray[np.bool_]:
    """Per column: is its integral closed form (as in ``features.design``)?"""
    flags = [True]
    for feature in structure.features:
        flags.extend([has_closed_form(feature)] * n_columns(feature))
    return np.array(flags, dtype=np.bool_)


def _horizon(data: Data) -> float:
    return data.horizon if isinstance(data, EventLog) else data.log.horizon


def _check_link(structure: Structure, link: Link) -> None:
    if structure.link is not link:
        raise LikelihoodError(
            f"link {link} differs from the structure's {structure.link}"
        )


def _integrate(eta: Floats, weights: Floats, link: Link) -> float:
    """``Σ_q w_q g(η_q)``; ``inf`` if the exp link overflows."""
    match link:
        case Link.IDENTITY:
            return reductions.total(weights * eta)
        case Link.EXP:
            if eta.size and float(np.max(eta)) > _MAX_EXP:
                return math.inf
            return reductions.total(weights * np.exp(eta))
        case Link.SOFTPLUS:
            return reductions.total(weights * _softplus(eta))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def integrated_intensity(theta: npt.ArrayLike, design: Design, link: Link) -> float:
    """``∫₀ᵀ λ(t) dt``: exact integrals under identity, the rule otherwise.

    ``inf`` if the exp link overflows at a node.
    """
    th = _theta(theta, design.n_columns)
    if link is Link.IDENTITY:
        return reductions.dot(th, design.integrals)
    return _integrate(reductions.matvec(design.at_nodes, th), design.weights, link)


def log_likelihood(theta: npt.ArrayLike, design: Design, link: Link) -> float:
    """``Σᵢ log λ(tᵢ) - ∫₀ᵀ λ``; ``-inf`` where λ is infeasible or overflows.

    Never ``nan``: identity with ``λ(tᵢ) ≤ 0`` at any event and exp with
    ``η > 709`` anywhere both return ``-inf``.
    """
    th = _theta(theta, design.n_columns)
    eta = reductions.matvec(design.at_events, th)
    match link:
        case Link.IDENTITY:
            if eta.size and float(np.min(eta)) <= 0.0:
                return -math.inf
            events = reductions.total(np.log(eta))
        case Link.EXP:
            if eta.size and float(np.max(eta)) > _MAX_EXP:
                return -math.inf
            events = reductions.total(eta)
        case Link.SOFTPLUS:
            events = reductions.total(_log_softplus(eta))
    comp = integrated_intensity(th, design, link)
    if math.isinf(comp):
        return -math.inf
    return events - comp


def compensator(
    theta: npt.ArrayLike,
    structure: Structure,
    psi: PsiAssignment,
    data: Data,
    channels: tuple[ChannelSpec, ...],
    link: Link,
    t: npt.ArrayLike,
    *,
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
    allow_off_grid: bool = False,
) -> Floats:
    """``Λ(t) = ∫₀ᵗ λ`` at each of ``t`` (any order, each in ``[0, T]``).

    For a :class:`~sciagent.glm.data.Dataset`, the excluded windows are left
    out of the integral.

    Identity link with every column in closed form: exact. Otherwise the
    design's rule, with every ``t`` added as a panel edge so each ``Λ(t)`` is a
    sum over whole panels. Raises :class:`LikelihoodError` if the exp link
    overflows.
    """
    _check_link(structure, link)
    tt = np.array(t, dtype=np.float64).reshape(-1)
    horizon = _horizon(data)
    if not np.all((tt >= 0.0) & (tt <= horizon)):
        raise LikelihoodError(f"compensator times must lie in [0, {horizon}]")
    if link is Link.IDENTITY:
        closed = cumulative_columns(
            structure, psi, data, channels, tt, allow_off_grid=allow_off_grid
        )
        if closed is not None:
            th = _theta(theta, closed.shape[1])
            return reductions.matvec(closed, th)
    nodes, weights = quadrature_rule(
        structure,
        data,
        quadrature=quadrature,
        breakpoints=tt,
        psi=psi,
        allow_off_grid=allow_off_grid,
    )
    at_nodes = evaluate_columns(
        structure, psi, data, channels, nodes, allow_off_grid=allow_off_grid
    )
    th = _theta(theta, at_nodes.shape[1])
    eta = reductions.matvec(at_nodes, th)
    if link is Link.EXP and eta.size and float(np.max(eta)) > _MAX_EXP:
        raise LikelihoodError("the intensity overflows under the exp link")
    # Every t is a panel edge, so Λ(t) is a sum over the nodes below it. Sum
    # each stretch between consecutive sorted cuts exactly, then accumulate
    # the stretches in time order: O(m), not O(m·len(t)).
    order = np.argsort(tt, kind="stable")
    cuts = np.searchsorted(nodes, tt[order], side="left").tolist()
    sorted_out = np.empty(tt.size)
    running = 0.0
    lo = 0
    for i, hi in enumerate(cuts):
        running += _integrate(eta[lo:hi], weights[lo:hi], link)
        sorted_out[i] = running
        lo = hi
    out = np.empty(tt.size)
    out[order] = sorted_out
    return out


def compensator_error(
    theta: npt.ArrayLike,
    structure: Structure,
    psi: PsiAssignment,
    data: Data,
    channels: tuple[ChannelSpec, ...],
    link: Link,
    *,
    quadrature: QuadratureSpec = DEFAULT_QUADRATURE,
    allow_off_grid: bool = False,
) -> float:
    """A-posteriori error estimate of the design's ``∫₀ᵀ λ`` at ``theta``.

    ``2 · |rule - refined rule|``, the refined rule having the same panels and
    twice the Gauss-Legendre nodes per panel. The difference alone tracks the
    actual error to a few digits but can fall just short of it (the refined
    rule is not exact), hence the safety factor of 2. Under the identity link
    only the columns integrated by quadrature contribute, so it is ``0.0``
    when every column has a closed form. ``inf`` if the exp link overflows.
    """
    _check_link(structure, link)

    def rule(spec: QuadratureSpec) -> tuple[Floats, Floats]:
        nodes, weights = quadrature_rule(
            structure, data, quadrature=spec, psi=psi, allow_off_grid=allow_off_grid
        )
        at = evaluate_columns(
            structure, psi, data, channels, nodes, allow_off_grid=allow_off_grid
        )
        return at, weights

    if link is Link.IDENTITY:
        end = cumulative_columns(
            structure,
            psi,
            data,
            channels,
            [_horizon(data)],
            allow_off_grid=allow_off_grid,
        )
        if end is not None:
            _theta(theta, end.shape[1])
            return 0.0
    coarse_at, coarse_w = rule(quadrature)
    fine_at, fine_w = rule(quadrature.refined())
    th = _theta(theta, coarse_at.shape[1])
    if link is Link.IDENTITY:
        # The design integrates closed-form columns exactly; only the columns
        # it integrates by quadrature (those under a Product) carry error.
        th = np.where(_closed_form_columns(structure), 0.0, th)
    a = _integrate(reductions.matvec(coarse_at, th), coarse_w, link)
    b = _integrate(reductions.matvec(fine_at, th), fine_w, link)
    if math.isinf(a) or math.isinf(b):
        return math.inf
    return 2.0 * abs(a - b)
