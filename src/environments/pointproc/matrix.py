"""SPEC §9's first experiment matrix, as data.

Here rather than in ``sciagent/eval/matrix.py`` because this is the only part of
the matrix that names a system and a scenario. The framework's first invariant
is that ``sciagent`` never imports an environment, and a cell table holding
``"V7"`` and ``"S11"`` inside the framework would put the slice in it by another
route -- the driver would still not *import* an environment, but it would know
one. What is domain-independent is the driver; what is domain-specific is which
arms run on which worlds, and that is this file.

The matrix, from §9 exactly
---------------------------

===================================  =================  =====
systems                              scenarios          cells
===================================  =================  =====
V1 BOED, V7 Hybrid, B4 Retrieval,    all twelve         48
B5 Beam search
B1 PPC-only, Stage A only            S9, S11             2
V3 raw history, V4 graph             S8, S11, S12        6
===================================  =================  =====

**56 cells, twenty seeds each, 1,120 investigations.**

Do not add an arm, a scenario or a seed count. §9 says "small and interpretable",
and the frozen campaign is where breadth goes: a new arm is a
``docs/BACKLOG.md`` entry under SPEC §13's rule, not an edit here. Two entries
already waiting there -- the learned experiment-selection policy and the model
tier axis -- both say in as many words that they are additional arms *after* this
matrix, and both would be one line's worth of change to this tuple. That is
exactly why the rule is written down beside the tuple rather than somewhere
else.

The preregistered contrast
--------------------------

    On S11 Stage B, conditional on inadequacy detection, does V7 exceed **B4**
    on D3 (intervention-response similarity)?

B4 is the comparator by *prior* designation, because it is the baseline most
likely to deflate the claim. It is in this table for that reason and not because
retrieval is interesting on S11. Reporting the contrast against a different
baseline is permitted only if the report says that is what happened.

Slice results are **exploratory by construction**. They inform the frozen
campaign; they are not reportable as confirmatory findings, and anything quoting
these cells has to say so, along with which platform every cell ran on.
"""

from __future__ import annotations

from typing import Final

from sciagent.core.types import ScenarioId
from sciagent.eval.matrix import Cell

__all__ = ["ABLATION_SCENARIOS", "CORE_SYSTEMS", "REPLICATES", "SPEC9_CELLS"]

#: Seeds per cell. §9's "twenty seeds per cell", and the multiplier that turns 56
#: cells into ~1,120 investigations.
REPLICATES: Final = 20

#: The twelve slice scenarios, in SPEC §4.5's order.
_ALL_TWELVE: Final[tuple[str, ...]] = tuple(f"S{index}" for index in range(1, 13))

#: The four systems §9 runs on every scenario.
CORE_SYSTEMS: Final[tuple[str, ...]] = ("V1", "V7", "B4", "B5")

#: Where §9 puts B1. Stage A only: it holds no proposal layer, so it has nothing
#: to say about Stage B, and running it on all twelve would report a detection
#: rate that is a statement about holding the null rather than about adequacy.
_PPC_ONLY_SCENARIOS: Final[tuple[str, ...]] = ("S9", "S11")

#: Where §9 puts the item 13 memory ablation. Targeted, not broad: R2 asks what
#: the memory representation buys, and these are the three scenarios where a
#: representation could plausibly decide the answer.
ABLATION_SCENARIOS: Final[tuple[str, ...]] = ("S8", "S11", "S12")


def _cells() -> tuple[Cell, ...]:
    """Return §9's table, in reading order: core arms, then B1, then the ablation."""
    built: list[Cell] = []
    for system in CORE_SYSTEMS:
        built.extend(
            Cell(system, ScenarioId(scenario), REPLICATES) for scenario in _ALL_TWELVE
        )
    built.extend(
        Cell("B1", ScenarioId(scenario), REPLICATES) for scenario in _PPC_ONLY_SCENARIOS
    )
    for system in ("V3", "V4"):
        built.extend(
            Cell(system, ScenarioId(scenario), REPLICATES)
            for scenario in ABLATION_SCENARIOS
        )
    return tuple(built)


#: SPEC §9's matrix. A tuple, and built once: a campaign resuming across sessions
#: must visit the same cells in the same order, and a list somebody appended to
#: at import time would be a different matrix with the same name.
SPEC9_CELLS: Final[tuple[Cell, ...]] = _cells()
