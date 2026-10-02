"""SPEC §4.3 score 1: the held-out predictive gap, and the share of it closed.

**Held-out data.** One observational log from the truth on
``[0, HELDOUT_HORIZON]`` (about 2,000 events at the mean-rate-1 operating
point, like the investigation's free log), unintervened, drawn on the stream
:func:`heldout_seed` names. That stream is disjoint from every stream the
investigation draws (``scoring/seeds.py`` gives the argument), so no system
has seen a single held-out draw. It depends on the truth id and the seed only,
so every system on a (truth, seed) is scored on the same data: the contrast is
paired.

**The gap.** ``gap = LL_sub - LL_oracle``, each LL the model's held-out
log-likelihood per counted event (``FittedModel.held_out_per_event``), with
ORACLE the true structure fitted on the same observational data (SPEC §4.1).
Higher is better; the ORACLE's own gap is exactly 0 and a better-than-oracle
gap (positive) is possible by sampling noise. The log score is strictly
proper, so in expectation over held-out data the gap is maximised only by
the truth's own predictive distribution; per-event normalisation makes gaps
comparable across truths whose held-out logs differ in length.

**Gap closed.** ``(LL_sub - LL_lib) / (LL_oracle - LL_lib)``, with LL_lib B-lib's
held-out per-event log-likelihood: 0 at B-lib, 1 at the ORACLE, unclipped (a
system worse than B-lib is negative; one better than the ORACLE exceeds 1). It
is undefined (None) when ``LL_oracle ≤ LL_lib``: then the truth is not
identifiable against the library at this budget and SPEC §6.4 drops it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from sciagent.glm.data import Dataset
from sciagent.glm.interventions import Compose, Experiment
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.seeds import HELDOUT, generator, scorer_seed
from sciagent.scoring.truth import TRUTH_MAX_EVENTS, TruthLike, simulate_truth
from sciagent.systems.v2.models import FittedModel

#: Horizon of the held-out log: the investigation's observational horizon.
HELDOUT_HORIZON: Final = 2000.0
#: Label of the held-out dataset.
HELDOUT_LABEL: Final = "held_out"


def heldout_seed(truth_id: str, seed: int) -> np.random.SeedSequence:
    """The held-out stream of (truth, seed): disjoint from the investigation's."""
    return scorer_seed(HELDOUT, truth_id, seed)


def held_out_dataset(
    truth: TruthLike,
    truth_id: str,
    seed: int,
    *,
    horizon: float = HELDOUT_HORIZON,
    max_events: int = TRUTH_MAX_EVENTS,
) -> Dataset:
    """Fresh observational data from the truth on the held-out stream."""
    rng = generator(heldout_seed(truth_id, seed))
    return simulate_truth(
        truth,
        Experiment(Compose(()), horizon),
        rng,
        label=HELDOUT_LABEL,
        max_events=max_events,
    )


@dataclass(frozen=True)
class PredictiveScore:
    """Held-out per-event log-likelihoods and ``gap = per_event - oracle``."""

    per_event: float
    oracle_per_event: float
    gap: float


def predictive_gap(
    model: FittedModel, oracle: FittedModel, held_out: Sequence[Dataset]
) -> PredictiveScore:
    """Score 1 for ``model`` against the ORACLE's fit, on ``held_out``."""
    if not held_out:
        raise ScoringError("no held-out data")
    sub = model.held_out_per_event(held_out)
    ref = oracle.held_out_per_event(held_out)
    if not (math.isfinite(sub) and math.isfinite(ref)):
        raise ScoringError(f"non-finite held-out log-likelihood: {sub}, {ref}")
    return PredictiveScore(per_event=sub, oracle_per_event=ref, gap=sub - ref)


def gap_closed(sub: float, lib: float, oracle: float) -> float | None:
    """``(sub - lib) / (oracle - lib)``; None when ``oracle <= lib`` (docstring)."""
    if not all(math.isfinite(v) for v in (sub, lib, oracle)):
        raise ScoringError(f"non-finite log-likelihoods: {sub}, {lib}, {oracle}")
    if oracle <= lib:
        return None
    return (sub - lib) / (oracle - lib)
