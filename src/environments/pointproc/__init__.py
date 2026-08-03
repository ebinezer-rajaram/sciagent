"""The point-process vertical slice (SPEC §4)."""

from __future__ import annotations

from environments.pointproc.grammar import agent_grammar, edit_grammar
from environments.pointproc.mechanisms import (
    CONFOUNDED_MECHANISMS,
    HAWKES,
    POISSON_MIXTURE,
    REGIME_SWITCHING,
    SEASONALITY,
    SIZE_EXCITATION,
    mechanism_defect,
)
from environments.pointproc.operations import arrival_burst, compiler
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
    slice_templates,
)
from environments.pointproc.program import reference_program

__all__ = [
    "CONFOUNDED_MECHANISMS",
    "HAWKES",
    "POISSON_MIXTURE",
    "REGIME_SWITCHING",
    "SEASONALITY",
    "SIZE_EXCITATION",
    "agent_grammar",
    "arrival_burst",
    "closed_set",
    "compiler",
    "edit_grammar",
    "executor",
    "mechanism_defect",
    "reference_program",
    "simulator",
    "slice_designs",
    "slice_templates",
]
