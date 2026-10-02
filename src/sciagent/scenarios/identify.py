"""Identifiability: a truth must beat every library member on held-out data.

SPEC §3 requires that "the truth beats every library member on held-out
likelihood by ≥ δ at the experiment budget; checked by fitting, not assumed",
and SPEC §6.4's first positive control is the same comparison (ORACLE beats
B-lib on every test truth). :func:`check_identifiable` runs it: the truth's
structure and every library member are fitted on the same training data and
scored on independent held-out data; the margin against a member is

    (held-out log L of the fitted truth - that of the fitted member)
        / number of held-out events,

in nats per event, and every margin must be at least δ.

**Library members** are anything satisfying :class:`LibraryScorer`: fit on
training datasets, return a :class:`HeldOutScore`. :class:`GrammarScorer`
covers members expressible in the grammar (the certified fitter,
:func:`sciagent.glm.fit.fit`); :class:`ModelScorer` adapts an out-of-grammar
member of ``sciagent.library`` (regime switching, the Poisson mixture: SPEC
§2.1), whose ``structure`` is None. A member's fit that
is not certified voids the comparison, so the candidate is rejected rather
than judged on a fit that may have stalled.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, evaluate_log_likelihood, fit
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.simulate import PsiAssignment
from sciagent.library.base import LibraryModel
from sciagent.scenarios.calibrate import CandidateRejectedError


@dataclass(frozen=True)
class HeldOutScore:
    """A fitted model's held-out log-likelihood.

    ``log_likelihood`` is summed over the held-out datasets; ``n_events`` is
    the number of events counted by the likelihood (endogenous, outside
    excluded windows). ``psi`` is the fitted ψ for grammar members, else None.
    """

    log_likelihood: float
    n_events: int
    certified: bool
    psi: PsiAssignment | None


@runtime_checkable
class LibraryScorer(Protocol):
    """A library member the sampler can fit and score (module docstring)."""

    @property
    def name(self) -> str: ...

    @property
    def structure(self) -> Structure | None: ...

    def score(
        self, train: Sequence[Dataset], held_out: Sequence[Dataset]
    ) -> HeldOutScore: ...


def counted_events(data: Dataset) -> int:
    """Events contributing ``log λ``: endogenous, outside every excluded window."""
    keep = np.array(data.endogenous, dtype=np.bool_)
    for start, end in data.excluded:
        keep &= ~((data.log.times >= start) & (data.log.times <= end))
    return int(np.count_nonzero(keep))


@dataclass(frozen=True)
class GrammarScorer:
    """A grammar structure, fitted by the certified fitter."""

    name: str
    structure: Structure
    channels: tuple[ChannelSpec, ...]
    fit_config: FitConfig = field(default_factory=FitConfig)

    def score(
        self, train: Sequence[Dataset], held_out: Sequence[Dataset]
    ) -> HeldOutScore:
        result = fit(self.structure, train, self.channels, config=self.fit_config)
        per = evaluate_log_likelihood(
            result, held_out, self.channels, quadrature=self.fit_config.quadrature
        )
        return HeldOutScore(
            log_likelihood=math.fsum(per),
            n_events=sum(counted_events(d) for d in held_out),
            certified=result.certified,
            psi=tuple(dict(m) for m in result.psi),
        )


@dataclass(frozen=True)
class Identifiability:
    """The truth's and every member's held-out scores, and the margins.

    ``margins`` are in nats per held-out event, in library order; a member
    whose held-out log L is not finite has margin ``inf``.
    """

    truth: HeldOutScore
    members: tuple[tuple[str, HeldOutScore], ...]
    margins: tuple[tuple[str, float], ...]
    min_margin: float


def check_identifiable(
    truth: LibraryScorer,
    library: Sequence[LibraryScorer],
    train: Sequence[Dataset],
    held_out: Sequence[Dataset],
    *,
    delta: float,
) -> Identifiability:
    """Fit and score the truth and every member; raise unless every margin ≥ δ.

    Raises :class:`CandidateRejectedError` with reason ``uncertified`` (some
    fit is not certified), ``truth_not_finite`` or ``identifiability``.
    """
    if not library:
        raise CandidateRejectedError("identifiability", "the library is empty")
    t = truth.score(train, held_out)
    if not t.certified:
        raise CandidateRejectedError("uncertified", "the truth's fit is not certified")
    if not math.isfinite(t.log_likelihood) or t.n_events == 0:
        raise CandidateRejectedError("truth_not_finite", "truth held-out log L")
    members: list[tuple[str, HeldOutScore]] = []
    margins: list[tuple[str, float]] = []
    for member in library:
        s = member.score(train, held_out)
        if not s.certified:
            raise CandidateRejectedError(
                "uncertified", f"library member {member.name}'s fit is not certified"
            )
        members.append((member.name, s))
        margin = (
            (t.log_likelihood - s.log_likelihood) / t.n_events
            if math.isfinite(s.log_likelihood)
            else math.inf
        )
        margins.append((member.name, margin))
    worst = min(m for _, m in margins)
    if not worst >= delta:
        name = next(n for n, m in margins if m == worst)
        raise CandidateRejectedError(
            "identifiability", f"margin {worst:.4g} nats/event vs {name} < δ = {delta}"
        )
    return Identifiability(t, tuple(members), tuple(margins), worst)


@dataclass(frozen=True)
class ModelScorer:
    """An out-of-grammar member (``sciagent.library``), fitted by its own MLE.

    Those fits are multistart maximum likelihood with no optimality
    certificate, so ``certified`` is always True here: they are scored exactly
    as B-lib scores them. ``structure`` is None, so they take no part in the
    distance strata.
    """

    model: LibraryModel

    @property
    def name(self) -> str:
        return self.model.name

    @property
    def structure(self) -> Structure | None:
        return None

    def score(
        self, train: Sequence[Dataset], held_out: Sequence[Dataset]
    ) -> HeldOutScore:
        fitted = self.model.fit(train)
        return HeldOutScore(
            log_likelihood=fitted.held_out_log_likelihood(held_out),
            n_events=sum(counted_events(d) for d in held_out),
            certified=True,
            psi=None,
        )
