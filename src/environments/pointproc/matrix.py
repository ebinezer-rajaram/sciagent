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

The rule held under the one case that tested it. SPEC §12 criterion 5 names an
arm -- "B6-equivalent random structured generation" -- that §5 defers to the full
benchmark, so the criterion could not be measured at all until gate A28 built it.
That arm is :data:`CRITERION5_CELLS`, a *separate* declaration, and not a
fifty-seventh row here. What §9 recorded stays what §9 recorded; a criterion-5
reading and a §9 reading are different claims and keep different cell sets.
:data:`ALL_CELLS` is the union, for the runner that has to visit both.

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
from sciagent.eval.report import Preregistration

__all__ = [
    "ABLATION_SCENARIOS",
    "ALL_CELLS",
    "CORE_SYSTEMS",
    "CRITERION5_CELLS",
    "REPLICATES",
    "SPEC9_CELLS",
    "SPEC9_CONTRAST",
]

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

#: SPEC §12 criterion 5's comparator cell, which is **not** part of §9's matrix.
#:
#: Criterion 5 reads: "Proposes an S11 extension exceeding B6-equivalent random
#: structured generation on D3, with a non-overlapping 95% interval." It names an
#: arm SPEC §5 defers to the full benchmark, so until gate A28 the criterion had
#: a comparator by name and nothing to compare against. This is that comparator.
#:
#: Separate from :data:`SPEC9_CELLS` rather than appended to it, and the reason is
#: written three lines above that tuple: *do not add an arm*. §9's 56 cells are
#: what the recorded campaign was addressed under, and a §9 that quietly became 57
#: would make every "the matrix" statement ambiguous about which matrix. A
#: criterion-5 reading and a §9 reading are different claims; keeping the cell sets
#: apart is what keeps a report of one from reading as a report of the other.
#:
#: One scenario and not twelve. Criterion 5 is about an *S11* extension, so B6
#: anywhere else answers a question nobody asked and costs twenty investigations
#: to do it. Twenty replicates because the criterion asks for a 95% interval, and
#: an interval computed at a different replicate count than the V7 cell it
#: deflates is not comparable with it.
CRITERION5_CELLS: Final[tuple[Cell, ...]] = (Cell("B6", ScenarioId("S11"), REPLICATES),)

#: Every cell this environment can run: §9's, plus criterion 5's comparator.
#:
#: What ``scripts/run_matrix.py`` selects from. Ordered §9-first so that a pass
#: over everything visits the preregistered matrix in the order it was recorded
#: in, and appends rather than interleaves.
ALL_CELLS: Final[tuple[Cell, ...]] = SPEC9_CELLS + CRITERION5_CELLS

#: SPEC §9's preregistered primary contrast, as data rather than as prose.
#:
#: Here for the same reason :data:`SPEC9_CELLS` is: it names two systems and a
#: scenario, and the framework may not know one. What lives in
#: :mod:`sciagent.eval.report` is :class:`~sciagent.eval.report.Preregistration`,
#: the type.
#:
#: **This single instance is the thing with authority**, and the test that pins it
#: against drift is what gives it that. A contrast against any other comparator, or
#: one run unconditionally, comes back with ``preregistered=False`` when checked
#: against this -- so the rule above has something to be stated with, instead of
#: being merely written down.
#:
#: Not a guarantee of provenance. `contrast()` compares its arguments against
#: whichever declaration it is handed, and both come from the same caller, so the
#: flag catches an *accidental* mislabel rather than a fabricated one. See
#: :class:`~sciagent.eval.report.Preregistration` for why that cannot be closed
#: from inside the framework.
SPEC9_CONTRAST: Final = Preregistration(
    scenario=ScenarioId("S11"),
    treatment="V7",
    comparator="B4",
    dimension="d3_intervention_similarity",
    conditional_on_inadequacy=True,
    residual_asymmetries=(
        "Pre-proposal selection. V7 entertains its library before it selects, so "
        "the first half of its budget is chosen by expected information gain. B4 "
        "must observe before it can retrieve, so at its own first half the belief "
        "holds only the null, every design's gain is exactly zero, and the budget "
        "is spent on a rotation through the design space instead. The two arms "
        "select identically over the half that follows and not over the half "
        "before it. One consequence is worth naming on its own: the Stage A "
        "design is in the scenario's design set, so a rotating arm spends budget "
        "on it while a selecting arm can decline it -- over the pre-proposal "
        "half B4 and B5 still do, and V7 still need not. V1-vs-B4 bounds what "
        "remains: V1 selects that half by information gain over the same library "
        "B4 retrieves from, so the V1-B4 gap contains the pre-proposal selection "
        "effect the V7-B4 gap also carries.",
        "Relevance declarations. V7 names the live hypotheses each experiment is "
        "aimed at; B4 and B5 name none. SPEC §7.1 clause 1 reads those targets, "
        "so the arms' claims are not equally gradeable on relevance. It is not a "
        "selection input -- nothing in the BOED plan reads it -- so it does not "
        "reach D3 through the experiments chosen, only through what the verifier "
        "can say about the claims that follow.",
        "Library coverage. B4's fixed mechanism library excludes S11's truth by "
        "construction, S11 being the out-of-library scenario. The comparator "
        "therefore cannot retrieve the answer on the one scenario its designation "
        "exists for, and its D3 is bounded above by the best its library can do. "
        "This is a property of the comparison §9 asks for rather than a defect: "
        "R1 is whether generation beats retrieval where retrieval cannot reach.",
    ),
)
