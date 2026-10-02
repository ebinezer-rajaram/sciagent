"""Shared helpers for ``tests/scoring``: pointproc v2 truths as scorer inputs.

Test-side, so it may import ``environments``; the framework never does.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from environments.pointproc import v2
from sciagent.glm.data import Dataset
from sciagent.investigation import TruthSpec
from sciagent.library.mmpp import MMPP2
from sciagent.systems.v2.systems import (
    GrammarMember,
    InvestigationData,
    LibraryEntry,
    ModelMember,
)

#: A cheap library for fast tests: no exp-link quadrature fits.
SMALL_LIBRARY: Final[tuple[LibraryEntry, ...]] = (
    GrammarMember("null", v2.NULL),
    GrammarMember("hawkes", v2.HAWKES),
    ModelMember("regime_switching", MMPP2()),
)


def truth(name: str) -> TruthSpec:
    t = v2.TRUTHS[name]
    return TruthSpec(t.structure, t.psi, t.coef, v2.CHANNELS, v2.mark_sampler)


def investigation_data(name: str, seed: int, horizon: float) -> InvestigationData:
    log = v2.TRUTHS[name].simulate(horizon, np.random.default_rng(seed))
    return InvestigationData(
        (Dataset.observational(log),), v2.CHANNELS, v2.mark_sampler
    )
