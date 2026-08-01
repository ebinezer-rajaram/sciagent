"""The reference generative programme for the point-process slice (SPEC §4.1).

+-------------+-------------+------------------------+-------------+
| Component   | Kind        | Family                 | Parameters  |
+=============+=============+========================+=============+
| ``arrival`` | arrival     | ``poisson_homogeneous``| rate        |
| ``size``    | size        | ``exponential``        | mean        |
| ``sign``    | sign        | ``iid_bernoulli``      | p = 0.5     |
| ``obs``     | observation | ``identity``           | -           |
+-------------+-------------+------------------------+-------------+

Edges: ``arrival -> size``, ``arrival -> sign``, ``{size, sign} -> obs``. All
four are instantaneous: they are read at the same event index. The reference
programme has no history edges; every one of the four mechanisms of SPEC §4.2
adds structure to ``arrival`` and only ``AddDependency`` adds an edge at all.
"""

from __future__ import annotations

from environments.pointproc.components import (
    ARRIVAL,
    EXPONENTIAL,
    IDENTITY,
    IID_BERNOULLI,
    LIBRARY,
    OBS,
    POISSON_HOMOGENEOUS,
    SIGN,
    SIZE,
)
from sciagent.core.program import Component, GenerativeProgram
from sciagent.core.types import ComponentId, FrozenDict, parameters

#: Reference arrival rate. Sets the time unit: one event per unit time on
#: average. Every mechanism in ``mechanisms.py`` is calibrated to preserve it.
REFERENCE_RATE = 1.0

#: Reference mean mark size. Sets the size unit.
REFERENCE_MEAN_SIZE = 1.0

#: Reference sign probability. Symmetric by construction (SPEC §4.1).
REFERENCE_SIGN_PROBABILITY = 0.5


def reference_program() -> GenerativeProgram:
    """Return the undefective reference programme.

    Guarantees a programme with no history edges, an acyclic instantaneous edge
    set, and every family implemented by the point-process library.
    """
    components = FrozenDict[ComponentId, Component](
        {
            ARRIVAL: Component(
                id=ARRIVAL,
                kind="arrival",
                family=POISSON_HOMOGENEOUS,
                parameters=parameters(rate=REFERENCE_RATE),
            ),
            SIZE: Component(
                id=SIZE,
                kind="size",
                family=EXPONENTIAL,
                parameters=parameters(mean=REFERENCE_MEAN_SIZE),
            ),
            SIGN: Component(
                id=SIGN,
                kind="sign",
                family=IID_BERNOULLI,
                parameters=parameters(p=REFERENCE_SIGN_PROBABILITY),
            ),
            OBS: Component(
                id=OBS,
                kind="observation",
                family=IDENTITY,
            ),
        }
    )
    edges = frozenset(
        {
            (ARRIVAL, SIZE),
            (ARRIVAL, SIGN),
            (SIZE, OBS),
            (SIGN, OBS),
        }
    )
    return GenerativeProgram(
        components=components,
        edges=edges,
        library=LIBRARY,
        history_edges=frozenset(),
    )
