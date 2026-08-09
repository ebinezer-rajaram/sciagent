"""What a model is shown, and what it is allowed to say back.

This module is the boundary SPEC F7 describes -- "the LLM writes structure and
prose, the framework writes numbers" -- expressed as a pair of total functions.
:func:`render_brief` turns an :class:`~sciagent.systems.base.Investigation` into
the text a model sees; :func:`decode` turns what it says back into a
:class:`~sciagent.core.edits.Defect`. Everything the invariants need is a
property of those two functions rather than of the prompt's wording.

Two properties carry the invariants
-----------------------------------

**The brief cannot leak the answer.** It is built from an ``Investigation`` and
from nothing else. That type has no public path to
:attr:`~sciagent.eval.scenarios.Scenario.truth` -- the ground truth reaches it as
a private attribute with no accessor -- so a brief that exposed the answer would
have to reach past a name-mangled slot to do it. The restriction is structural,
and it is why this module takes an investigation rather than a scenario.

**The model cannot write a number.** A proposal is a choice of *cell* in the
grammar's structural menu plus, per parameter, a **grid index**. Not a value: an
index into :attr:`~sciagent.core.edits.ParameterGrid.values`. Three things follow
that would each otherwise need a guard. A decoded proposal is on-grid by
construction, so it can never trip
:class:`~sciagent.core.errors.OffGridParameterError` and the prefix code is
always defined on it. The model has no way to express a magnitude at all, so no
prompt wording can coax a plausibility, a probability or a score out of it. And
the schema handed to the provider contains no ``number`` anywhere, which is a
mechanically checkable statement rather than a convention -- see
``tests/test_llm.py``.

The parameter grids are still *shown*, because a proposal made blind to what the
indices mean would be a lottery rather than a hypothesis. Reading a value and
choosing its index is a different act from writing one.

Why a menu rather than free-form structure
------------------------------------------

The alternative -- letting the model name a component, a family and a set of
parameters in prose -- was not taken. The grammar already enumerates every
licensed structural cell through
:meth:`~sciagent.core.edits.EditGrammar.structures`, so a menu is derived rather
than authored, it is exactly as expressive as the grammar and no more, and an
out-of-library structure is unproposable *because the menu has no entry for it*
rather than because a validator caught it afterwards. SPEC §4.5's S11 turns on
that distinction: the agent grammar licenses a self-excitation but not a
dependency of arrivals on prior mark sizes, and the menu it generates therefore
contains no way to say the latter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sciagent.core.edits import (
    AddDependency,
    AddLatentVariable,
    ChangeDistributionFamily,
    Defect,
    DependencyOption,
    Edit,
    EditGrammar,
    FamilyOption,
    LatentOption,
    Option,
    ParameterGrid,
    ReparameteriseComponent,
    build_edit,
    canonical,
)
from sciagent.core.errors import MalformedProposalError
from sciagent.core.types import ComponentId, FrozenDict
from sciagent.systems.base import Investigation

__all__ = [
    "EditDraft",
    "MenuEntry",
    "ProposalDraft",
    "decode",
    "render_brief",
    "structural_menu",
    "tool_schema",
]


# --------------------------------------------------------------------------
# The structural menu
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MenuEntry:
    """One structural cell of the grammar, as a model is offered it."""

    index: int
    """Position in the menu. What a draft names, so it is stable within a menu
    and meaningless outside one -- see :func:`structural_menu` on ordering."""

    edit_type: str
    """The edit type's class name, e.g. ``"AddDependency"``."""

    target: ComponentId
    construct: str
    """The option's own identity within the cell: a family, a latent spec, or a
    ``source|kernel`` pair for a dependency."""

    grids: tuple[ParameterGrid, ...]

    @property
    def arity(self) -> int:
        """Return how many grid indices a draft of this cell must supply."""
        return len(self.grids)


def structural_menu(grammar: EditGrammar) -> tuple[MenuEntry, ...]:
    """Return every structural cell the grammar licenses, in a fixed order.

    Guarantees the order is the grammar's own
    (:meth:`~sciagent.core.edits.EditGrammar.structures`, which iterates edit
    types in ``ordered_types`` and targets in table order), so two processes
    building a menu from one grammar agree on every index. That is what lets a
    menu index be the thing a recorded transcript refers to.

    Guarantees also that the menu is exactly as expressive as the grammar: one
    entry per cell, no cell omitted, nothing added. A structure outside the
    grammar has no index, so it cannot be named.
    """
    return tuple(
        MenuEntry(
            index=index,
            edit_type=edit_type.__name__,
            target=target,
            construct=_construct_of(option),
            grids=option.grids,
        )
        for index, (edit_type, target, option) in enumerate(grammar.structures())
    )


def _construct_of(option: Option) -> str:
    """Return an option's identity within its cell."""
    match option:
        case FamilyOption():
            return str(option.family)
        case LatentOption():
            return str(option.spec)
        case DependencyOption():
            return f"{option.source}|{option.kernel}"


# --------------------------------------------------------------------------
# What a model says back
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditDraft:
    """One proposed edit: a menu cell, and a grid index per parameter.

    Integers only, by design. See this module's docstring for why the parameter
    channel is an index rather than a value.
    """

    structure: int
    parameters: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    """What a provider returns: structure and prose, and nothing else.

    ``rationale`` is SPEC §3.3's ``rationale`` field -- LLM prose, never scored.
    ``name`` becomes part of a :class:`~sciagent.core.types.HypothesisId` and is
    slugged by the caller; neither field can carry a number into a score, since
    neither is read by anything that computes one.
    """

    edits: tuple[EditDraft, ...]
    name: str = "proposal"
    rationale: str = ""


def decode(
    grammar: EditGrammar, menu: Sequence[MenuEntry], draft: ProposalDraft
) -> Defect:
    """Return the defect a draft denotes, or raise.

    Guarantees the result is on-grid, since every parameter comes from a grid
    index rather than from a value, and that it is licensed by ``grammar``,
    since every structure comes from a menu built from that grammar. What it does
    *not* guarantee is that the defect is coherent as a set --
    :meth:`~sciagent.core.edits.EditGrammar.validate_defect` decides that, and
    :meth:`~sciagent.hypothesis.graph.HypothesisGraph.propose` runs it -- because
    a draft naming two edits on one component is a proposal that should be
    refused with a reason, not one that should fail to parse.

    Raises :class:`~sciagent.core.errors.MalformedProposalError` for an empty
    draft, an unknown menu index, the wrong number of parameters for a cell, or a
    grid index outside its grid. Every one names what was offered, because a
    provider that has just produced an unusable draft is the thing being
    debugged.
    """
    if not draft.edits:
        raise MalformedProposalError(
            f"proposal {draft.name!r} names no edit; the empty defect is the null "
            f"hypothesis, which every investigation already holds, so proposing "
            f"it is never what a system means"
        )
    edits: list[Edit] = []
    for position, item in enumerate(draft.edits):
        entry = _entry(menu, item, position, draft.name)
        edits.append(
            build_edit(
                _edit_type(grammar, entry, draft.name),
                entry.target,
                _option(grammar, entry, draft.name),
                _parameters(entry, item, position, draft.name),
            )
        )
    return frozenset(edits)


def _entry(
    menu: Sequence[MenuEntry], item: EditDraft, position: int, name: str
) -> MenuEntry:
    """Return the menu entry an edit draft names."""
    if not 0 <= item.structure < len(menu):
        raise MalformedProposalError(
            f"proposal {name!r} edit {position} names structure {item.structure}, "
            f"but the menu offers {len(menu)} (0 to {len(menu) - 1})"
        )
    return menu[item.structure]


def _parameters(
    entry: MenuEntry, item: EditDraft, position: int, name: str
) -> FrozenDict[str, float]:
    """Return the parameter assignment a draft's grid indices denote."""
    if len(item.parameters) != entry.arity:
        raise MalformedProposalError(
            f"proposal {name!r} edit {position} supplies {len(item.parameters)} "
            f"parameter(s) for {entry.edit_type} on {entry.target!r} "
            f"({entry.construct}), which takes {entry.arity}: "
            f"{[grid.name for grid in entry.grids]!r}"
        )
    assignment: dict[str, float] = {}
    for grid, index in zip(entry.grids, item.parameters, strict=True):
        if not 0 <= index < grid.size:
            raise MalformedProposalError(
                f"proposal {name!r} edit {position} names index {index} for "
                f"parameter {grid.name!r}, whose grid has {grid.size} points "
                f"(0 to {grid.size - 1})"
            )
        assignment[grid.name] = grid.values[index]
    return FrozenDict[str, float](assignment)


def _edit_type(grammar: EditGrammar, entry: MenuEntry, name: str) -> type[Edit]:
    """Return the edit type an entry's declared type name refers to."""
    for edit_type in grammar.ordered_types:
        if edit_type.__name__ == entry.edit_type:
            return edit_type
    raise MalformedProposalError(
        f"proposal {name!r} names edit type {entry.edit_type!r}, which this "
        f"grammar does not license; it licenses "
        f"{[t.__name__ for t in grammar.ordered_types]!r}"
    )


def _option(grammar: EditGrammar, entry: MenuEntry, name: str) -> Option:
    """Return the grammar option an entry refers to."""
    for option in grammar.options(_edit_type(grammar, entry, name), entry.target):
        if _construct_of(option) == entry.construct:
            return option
    raise MalformedProposalError(
        f"proposal {name!r} names construct {entry.construct!r} on "
        f"{entry.target!r}, which the grammar no longer offers; the menu and the "
        f"grammar have come apart"
    )


# --------------------------------------------------------------------------
# The schema a provider constrains its output to
# --------------------------------------------------------------------------


def tool_schema(menu: Sequence[MenuEntry]) -> dict[str, Any]:
    """Return the JSON schema a provider must constrain a draft to.

    Strict by construction: ``additionalProperties`` is false everywhere and
    every field is required, so a conforming payload decodes without a second
    validation pass.

    Guarantees the schema contains **no** ``"number"`` type anywhere. That is the
    mechanically checkable form of SPEC's second invariant at this boundary: a
    model constrained by this schema has no channel through which a real value
    could reach the framework, so "agents do not write numbers" is a property of
    the wire format rather than of the prompt.

    What the schema does *not* bound is a parameter index from above. ``structure``
    carries a ``maximum``, because one menu length covers every entry; a parameter
    index cannot, because its bound is the grid's size and that varies by
    structure, which is not expressible in a schema written before the structure
    is chosen. The wire format therefore constrains a parameter to a non-negative
    **integer**, which is the whole of what this boundary claims, and
    :func:`_parameters` refuses an index past the end of its grid at decode.
    Nothing off-grid can reach the prefix code either way; the difference is only
    whether the provider or the decoder catches it.
    """
    largest = max((entry.arity for entry in menu), default=0)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "rationale", "edits"],
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "A short slug naming the mechanism, e.g. 'self_excitation'."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Why this structure explains the residual. Prose; it is "
                    "recorded and never scored."
                ),
            },
            "edits": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["structure", "parameters"],
                    "properties": {
                        "structure": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": max(0, len(menu) - 1),
                            "description": (
                                "Index into the structural menu in the brief."
                            ),
                        },
                        "parameters": {
                            "type": "array",
                            "minItems": 0,
                            "maxItems": largest,
                            "items": {"type": "integer", "minimum": 0},
                            "description": (
                                "One grid index per parameter of the chosen "
                                "structure, in the order the brief lists them. "
                                "An index, never a value."
                            ),
                        },
                    },
                },
            },
        },
    }


# --------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Section:
    """One titled block of the brief. Ordered, so the rendering is stable."""

    title: str
    lines: tuple[str, ...] = field(default_factory=tuple)


def render_brief(
    investigation: Investigation,
    menu: Sequence[MenuEntry],
    *,
    max_grid_values: int = 8,
) -> str:
    """Return the brief a model is shown for this investigation.

    Assembled from what an ``Investigation`` exposes and nothing else, which is
    what makes leaking the ground truth structurally impossible rather than
    merely unintended -- see this module's docstring.

    Guarantees the rendering is a pure function of its arguments and is stable
    across processes: every mapping is walked in sorted key order and no value is
    formatted through a container's own ``repr``. That matters more than it
    looks, because the brief is hashed into a transcript's content address, so an
    unstable rendering would be an unreproducible run.

    ``max_grid_values`` abbreviates a long grid to its first few points, its last
    point and its size. The slice's grids have 64 points, and listing all of them
    for every cell would crowd out the observations without telling a model
    anything it needs: what it must know is the range, the direction and how many
    indices there are.
    """
    sections = (
        _menu_section(menu, max_grid_values),
        _designs_section(investigation),
        _observations_section(investigation),
        _hypotheses_section(investigation),
        _check_section(investigation),
        _budget_section(investigation),
    )
    blocks = []
    for section in sections:
        body = "\n".join(section.lines) if section.lines else "(none)"
        blocks.append(f"## {section.title}\n{body}")
    return "\n\n".join(blocks)


def _menu_section(menu: Sequence[MenuEntry], max_values: int) -> _Section:
    """Render the structural menu: every structure that can be proposed."""
    lines = []
    for entry in menu:
        grids = "; ".join(_render_grid(grid, max_values) for grid in entry.grids)
        lines.append(
            f"[{entry.index}] {entry.edit_type} on {entry.target} "
            f"({entry.construct}) -- parameters: {grids or 'none'}"
        )
    return _Section("Structures you may propose", tuple(lines))


def _render_grid(grid: ParameterGrid, max_values: int) -> str:
    """Render one parameter grid as a range, a size and a few example points."""
    values = grid.values
    if len(values) <= max_values:
        shown = ", ".join(f"{index}={value:g}" for index, value in enumerate(values))
    else:
        head = ", ".join(
            f"{index}={values[index]:g}" for index in range(max_values - 1)
        )
        shown = f"{head}, ..., {len(values) - 1}={values[-1]:g}"
    return f"{grid.name} ({grid.size} points, {grid.spacing}-spaced) [{shown}]"


def _designs_section(investigation: Investigation) -> _Section:
    """Render the experiments this scenario offers."""
    return _Section(
        "Experiments available",
        tuple(
            f"- {design.id} measures "
            f"{', '.join(str(ref.name) for ref in design.template().outcome.metrics)}"
            for design in investigation.designs
        ),
    )


def _observations_section(investigation: Investigation) -> _Section:
    """Render what has actually been measured, in the order it was measured."""
    lines = []
    for step, execution in enumerate(investigation.history):
        metrics = execution.design.template().outcome.metrics
        readings = ", ".join(
            f"{ref.name}={value:.6g}"
            for ref, value in zip(metrics, execution.result, strict=True)
        )
        lines.append(f"- step {step}: {execution.design.id} -> {readings}")
    return _Section("What has been observed", tuple(lines))


def _hypotheses_section(investigation: Investigation) -> _Section:
    """Render the structures already entertained, with their posterior mass.

    The mass is read off the engine, which is the framework computing it. A
    system is *shown* a number it did not write; SPEC F7 forbids an agent
    setting one, not seeing one, and a proposal made blind to which existing
    explanations are already doing well would be proposing into the dark.
    """
    posterior = investigation.posterior()
    graph = investigation.graph
    lines = []
    for node_id in sorted(posterior):
        node = graph.node(node_id)
        structure = node.program_edit
        rendered = (
            _render_defect(structure) if structure else "the null (nothing wrong)"
        )
        lines.append(
            f"- {node_id}: {rendered} -- posterior {float(posterior[node_id]):.4f}"
        )
    return _Section("Hypotheses already entertained", tuple(lines))


def _render_defect(defect: Defect) -> str:
    """Render a defect as text, in the canonical edit order.

    Canonical rather than set order: a ``Defect`` is a ``frozenset`` and its
    iteration order is not stable across processes, so rendering it directly
    would put a process-dependent string into a transcript's content address.
    """
    return " + ".join(_render_edit(edit) for edit in canonical(defect))


def _render_edit(edit: Edit) -> str:
    """Render one edit's construct, target and parameters, in sorted key order."""
    parameters = ", ".join(
        f"{key}={edit.parameters[key]:g}" for key in sorted(edit.parameters)
    )
    match edit:
        case AddDependency():
            head = f"{edit.source} -> {edit.target} via {edit.kernel}"
        case ChangeDistributionFamily():
            head = f"{edit.target} to {edit.family}"
        case ReparameteriseComponent():
            head = f"{edit.target} as {edit.parameterisation}"
        case AddLatentVariable():
            head = f"{edit.target} with {edit.spec}"
    return f"{type(edit).__name__}({head}, {parameters or 'no parameters'})"


def _check_section(investigation: Investigation) -> _Section:
    """Render the posterior predictive check: the conventional Stage A verdict.

    Per SPEC F5 and F6, detecting that the hypothesis space is inadequate is the
    framework's job and not the model's. The model is told the verdict so that it
    can act on it, which is the division of labour working rather than being
    bypassed.
    """
    result = investigation.ppc()
    verdict = (
        "the entertained hypotheses do NOT explain what was observed"
        if result.inadequate
        else "the entertained hypotheses explain what was observed adequately"
    )
    lines = [
        f"- combined p-value {result.p_value:.4g} against alpha {result.alpha:g}",
        f"- verdict: {verdict}",
    ]
    per_experiment = result.per_experiment
    for key in sorted(per_experiment):
        lines.append(f"- experiment {key[:12]}: p={per_experiment[key]:.4g}")
    return _Section("Posterior predictive check", tuple(lines))


def _budget_section(investigation: Investigation) -> _Section:
    """Render what is left to spend."""
    budget = investigation.budget
    return _Section(
        "Budget",
        (
            f"- {budget.remaining:g} experiment(s) remaining of {budget.total:g}",
            f"- {len(investigation.history)} already run",
        ),
    )


#: The only keys a conforming payload may carry, matching :func:`tool_schema`'s
#: ``required`` lists. Declared here so the decoder and the schema state one
#: contract rather than two that can drift apart.
_PAYLOAD_KEYS = frozenset({"name", "rationale", "edits"})
_EDIT_KEYS = frozenset({"structure", "parameters"})


def draft_from_payload(payload: Mapping[str, Any]) -> ProposalDraft:
    """Return the draft a schema-conforming tool payload denotes.

    Total on payloads that conform to :func:`tool_schema`, and raises
    :class:`~sciagent.core.errors.MalformedProposalError` on anything else rather
    than coercing. A provider that returns a float where the schema says integer
    has violated the one guarantee this boundary exists to make, so it is refused
    here instead of being rounded into acceptability.

    **An unknown key is refused, not ignored.** The schema declares
    ``additionalProperties: false`` at every level, so a payload carrying an
    extra field is not merely uninteresting -- it is non-conforming, and a
    decoder that dropped it quietly would be more permissive than the contract it
    published. The field that matters is ``plausibility``: a model that emits one
    has tried to write a number, and that is worth failing loudly on rather than
    tolerating in silence. It could never reach a score either way, since
    :class:`ProposalDraft` has no field for it, but "the number was refused" and
    "the number was dropped on the floor" are different things to be able to say
    afterwards.

    **A missing key is refused too**, for the symmetric reason. ``name``,
    ``rationale``, ``edits`` and an edit's ``parameters`` are all in
    :func:`tool_schema`'s ``required`` lists, and defaulting them here would make
    the decoder more permissive than the published contract in the one direction
    the paragraph above refuses to be permissive in. A parameterless structure
    still sends ``parameters: []``; an absent key is a provider that did not
    conform, and the arity check in :func:`_parameters` would report it as the
    wrong number of parameters rather than as the missing field it is.
    """
    unknown = sorted(set(payload) - _PAYLOAD_KEYS)
    if unknown:
        raise MalformedProposalError(
            f"a proposal payload carries {unknown!r}, which the schema does not "
            f"declare; it declares {sorted(_PAYLOAD_KEYS)!r} and nothing else. "
            f"Nothing outside that set can become part of a proposal"
        )
    absent = sorted(_PAYLOAD_KEYS - set(payload))
    if absent:
        raise MalformedProposalError(
            f"a proposal payload omits {absent!r}, which the schema requires; it "
            f"requires {sorted(_PAYLOAD_KEYS)!r} and a payload missing one of "
            f"them did not conform to the schema it was given"
        )
    edits = payload.get("edits")
    if not isinstance(edits, list):
        raise MalformedProposalError(
            f"a proposal payload must carry a list of edits, got {type(edits).__name__}"
        )
    drafted: list[EditDraft] = []
    for position, item in enumerate(edits):
        if not isinstance(item, Mapping):
            raise MalformedProposalError(
                f"edit {position} of the payload is {type(item).__name__}, "
                f"not an object"
            )
        extra = sorted(set(item) - _EDIT_KEYS)
        if extra:
            raise MalformedProposalError(
                f"edit {position} carries {extra!r}, which the schema does not "
                f"declare; an edit is a structure index and its grid indices"
            )
        lacking = sorted(_EDIT_KEYS - set(item))
        if lacking:
            raise MalformedProposalError(
                f"edit {position} omits {lacking!r}, which the schema requires; a "
                f"structure taking no parameter still names an empty list"
            )
        structure = item.get("structure")
        parameters = item.get("parameters")
        if not isinstance(structure, int) or isinstance(structure, bool):
            raise MalformedProposalError(
                f"edit {position} names structure {structure!r}, which is not an "
                f"integer; the parameter channel is an index and never a value"
            )
        if not isinstance(parameters, list) or any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in parameters
        ):
            raise MalformedProposalError(
                f"edit {position} supplies parameters {parameters!r}, which are not "
                f"all integers; the parameter channel is an index and never a value"
            )
        drafted.append(EditDraft(structure=structure, parameters=tuple(parameters)))
    name = payload["name"]
    rationale = payload["rationale"]
    if not isinstance(name, str) or not isinstance(rationale, str):
        raise MalformedProposalError(
            f"a proposal's name and rationale must be strings, got "
            f"{type(name).__name__} and {type(rationale).__name__}"
        )
    return ProposalDraft(edits=tuple(drafted), name=name, rationale=rationale)
