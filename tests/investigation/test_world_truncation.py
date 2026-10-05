"""The world's truncation at the event cap (P4 Δ2), opt-in per world.

With ``truncate=True`` a run that reaches :data:`MAX_EVENTS` is kept up to
the cap's event (``run_experiment_truncating``) instead of stopped, and
:meth:`World.truncated_at` reports τ. The default stays the stop, which the
tool layer still expects until it is wired in. Below the cap the two agree
byte for byte.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

import numpy as np
import pytest

from sciagent.glm.data import Dataset
from sciagent.glm.grammar import (
    ALL,
    ChannelKind,
    ChannelSpec,
    Excite,
    KernelKind,
    Link,
    Mark,
    PsiSlot,
    Structure,
)
from sciagent.glm.interventions import Compose, Experiment, InjectMarks
from sciagent.glm.simulate import Coefficients, ExplosionError
from sciagent.investigation import TruthSpec
from sciagent.investigation.world import (
    MAX_EVENTS,
    OBSERVATIONAL,
    World,
    WorldError,
)

CHANNELS: Final = (ChannelSpec("size", ChannelKind.POSITIVE, location=1.0, scale=1.0),)


def _sizes(rng: np.random.Generator) -> Mapping[str, float]:
    return {"size": float(rng.exponential(1.0))}


#: Size-excited Hawkes, branching ratio 0.5 at mean size 1; injecting size 3
#: makes it supercritical, so it reaches the cap quickly (ExpK only).
TRUTH: Final = TruthSpec(
    Structure((Excite(KernelKind.EXP, Mark("size"), ALL),), Link.IDENTITY),
    ({PsiSlot((0,), "exp_rate"): 2.0},),
    Coefficients(0.5, ((0.5,),)),
    CHANNELS,
    _sizes,
)

EXPLOSIVE: Final = Experiment(InjectMarks((0.0, 2000.0), "size", 3.0), 2000.0)
PLAIN: Final = Experiment(Compose(()), 300.0)


def _same(a: Dataset, b: Dataset) -> bool:
    return (
        a.log.times.tobytes() == b.log.times.tobytes()
        and all(
            a.log.marks[k].tobytes() == b.log.marks[k].tobytes() for k in a.log.marks
        )
        and a.endogenous.tobytes() == b.endogenous.tobytes()
        and a.excluded == b.excluded
        and a.log.horizon == b.log.horizon
        and a.label == b.label
    )


def test_below_the_cap_truncation_changes_nothing() -> None:
    old, new = World(TRUTH, 3), World(TRUTH, 3, truncate=True)
    assert _same(old.datasets[OBSERVATIONAL], new.datasets[OBSERVATIONAL])
    _, a = old.run(PLAIN)
    _, b = new.run(PLAIN)
    assert _same(a, b)
    assert new.truncated_at(OBSERVATIONAL) is None
    assert new.truncated_at("e0") is None
    assert old.truncated_at("e0") is None


def test_unknown_data_id_is_a_world_error() -> None:
    with pytest.raises(WorldError):
        World(TRUTH, 3).truncated_at("e0")


@pytest.mark.slow
def test_capped_experiment_is_truncated_and_reported() -> None:
    world = World(TRUTH, 3, truncate=True)
    index, ds = world.run(EXPLOSIVE)
    tau = world.truncated_at("e0")
    assert index == 0 and world.experiments_run == 1
    assert tau is not None and 0.0 < tau < EXPLOSIVE.horizon
    assert ds.log.n == MAX_EVENTS and ds.log.times[-1] == tau
    assert ds.log.horizon == EXPLOSIVE.horizon
    assert ds.excluded == ((math.nextafter(tau, math.inf), EXPLOSIVE.horizon),)
    assert world.datasets["e0"] is ds
    # Deterministic, and the default world still stops the same design.
    again = World(TRUTH, 3, truncate=True)
    _, ds2 = again.run(EXPLOSIVE)
    assert _same(ds, ds2) and again.truncated_at("e0") == tau
    stopping = World(TRUTH, 3)
    with pytest.raises(ExplosionError):
        stopping.run(EXPLOSIVE)
    assert stopping.experiments_run == 1 and "e0" not in stopping.datasets
