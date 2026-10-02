"""Identifiability against the library, by fitting (SPEC §3, §6.4; test 6)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from environments.pointproc.truths_v2 import library_scorers
from environments.pointproc.v2 import (
    CHANNELS,
    LIBRARY,
    SEASONAL_AMPLITUDE,
    SEASONAL_BASE,
    SEASONALITY,
    TRUTHS,
    Truth,
)
from sciagent.glm.data import Dataset
from sciagent.glm.grammar import PsiSlot, Structure
from sciagent.glm.simulate import Coefficients
from sciagent.scenarios.calibrate import CandidateRejectedError
from sciagent.scenarios.identify import (
    GrammarScorer,
    HeldOutScore,
    LibraryScorer,
    check_identifiable,
    counted_events,
)
from sciagent.scenarios.streams import stream


def _data(name: str, key: str, horizon: float = 1500.0) -> list[Dataset]:
    return [Dataset.observational(TRUTHS[name].simulate(horizon, stream(0, key)))]


#: v2's seasonality truth has period 11.56, off the ψ grid, and its fitted
#: member then loses to Hawkes (by 0.29 nats/event at 1,500 events): the reason
#: sampled truths are on-grid. This one has the on-grid period 10.
ON_GRID_SEASONAL = Truth(
    "seasonal10",
    SEASONALITY,
    ({PsiSlot((), "period"): 10.0},),
    Coefficients(math.log(SEASONAL_BASE), ((0.0, SEASONAL_AMPLITUDE),)),
)


def _seasonal(key: str) -> list[Dataset]:
    return [Dataset.observational(ON_GRID_SEASONAL.simulate(1500.0, stream(0, key)))]


@dataclass(frozen=True)
class _Constant:
    """An out-of-grammar stand-in: scores every held-out log by a fixed rate."""

    name: str
    rate: float

    @property
    def structure(self) -> Structure | None:
        return None

    def score(
        self, train: Sequence[Dataset], held_out: Sequence[Dataset]
    ) -> HeldOutScore:
        n = sum(counted_events(d) for d in held_out)
        t = sum(d.log.horizon for d in held_out)
        return HeldOutScore(n * math.log(self.rate) - self.rate * t, n, True, None)


def test_pointproc_library_scorers_cover_the_in_grammar_library() -> None:
    scorers = library_scorers()
    assert [s.name for s in scorers] == [
        *(m.name for m in LIBRARY),
        "regime_switching",
        "poisson_mixture",
    ]
    assert [s.structure for s in scorers] == [m.structure for m in LIBRARY] + [None] * 2
    assert all(isinstance(s, LibraryScorer) for s in scorers)


def test_seasonal_truth_beats_null_and_hawkes() -> None:
    result = check_identifiable(
        GrammarScorer("truth", SEASONALITY, CHANNELS),
        library_scorers()[:2],  # null and Hawkes
        _seasonal("train"),
        _seasonal("held"),
        delta=0.01,
    )
    margins = dict(result.margins)
    assert margins["null"] > 0.1
    assert margins["hawkes"] > 0.05
    assert result.min_margin == min(margins.values())


def test_the_seasonal_truth_is_rejected_as_its_own_library_member() -> None:
    # Same structure, same data: margin exactly 0 < δ.
    with pytest.raises(CandidateRejectedError, match=r"margin 0 nats/event vs seas"):
        check_identifiable(
            GrammarScorer("truth", SEASONALITY, CHANNELS),
            library_scorers(),
            _seasonal("train"),
            _seasonal("held"),
            delta=0.01,
        )


def test_a_library_member_is_not_identifiable_from_the_library() -> None:
    with pytest.raises(CandidateRejectedError, match="identifiability"):
        check_identifiable(
            GrammarScorer("truth", TRUTHS["hawkes"].structure, CHANNELS),
            library_scorers(),
            _data("hawkes", "train"),
            _data("hawkes", "held"),
            delta=0.01,
        )


def test_out_of_grammar_members_plug_in_through_the_protocol() -> None:
    truth = TRUTHS["hawkes"]
    weak = _Constant("constant", 1.0)
    assert isinstance(weak, LibraryScorer)
    result = check_identifiable(
        GrammarScorer("truth", truth.structure, CHANNELS),
        (weak,),
        _data("hawkes", "train"),
        _data("hawkes", "held"),
        delta=0.01,
    )
    assert dict(result.margins)["constant"] > 0.1
    assert result.truth.psi is not None
