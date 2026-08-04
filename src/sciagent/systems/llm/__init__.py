"""The LLM proposal layer (SPEC §11 item 12).

SPEC F5 divides the labour: conventional methods own posterior updating,
statistical computation, experiment selection within a fixed space, and
inadequacy *detection*; the LLM owns hypothesis-space construction and revision.
This package is the second half of that sentence, and nothing more. It proposes
structure. It does not choose experiments, does not compute a posterior, and has
no channel through which it could state a number.

The modules, in dependency order:

* :mod:`~sciagent.systems.llm.encoding` -- what a model is shown and what it may
  say back. The structural menu, the brief, the schema, and the decoder.
* :mod:`~sciagent.systems.llm.transcripts` -- content-addressed record and
  replay, which is what makes an LLM run satisfy SPEC §1's determinism invariant.
* :mod:`~sciagent.systems.llm.provider` -- the ``Provider`` protocol and the
  ``ProposalLayer`` that holds all the reproducible machinery.
* :mod:`~sciagent.systems.llm.scripted` -- a deterministic backend, for tests and
  for A17's negative control.
* :mod:`~sciagent.systems.llm.anthropic_provider` -- the live backend. Not
  imported here: a replay must not need the SDK, or an environment without it
  could not reproduce a recorded run.

The whole package is inside ``AGENT_TOOL_SURFACE``, so A14 and A17 analyse every
path through it.
"""

from __future__ import annotations

from sciagent.systems.llm.encoding import (
    EditDraft,
    MenuEntry,
    ProposalDraft,
    decode,
    draft_from_payload,
    render_brief,
    structural_menu,
    tool_schema,
)
from sciagent.systems.llm.provider import Proposal, ProposalLayer, Provider
from sciagent.systems.llm.scripted import ScriptedProvider, fixed_payload
from sciagent.systems.llm.transcripts import (
    RECORD,
    REPLAY,
    Transcript,
    TranscriptStore,
    call_address,
)

__all__ = [
    "RECORD",
    "REPLAY",
    "EditDraft",
    "MenuEntry",
    "Proposal",
    "ProposalDraft",
    "ProposalLayer",
    "Provider",
    "ScriptedProvider",
    "Transcript",
    "TranscriptStore",
    "call_address",
    "decode",
    "draft_from_payload",
    "fixed_payload",
    "render_brief",
    "structural_menu",
    "tool_schema",
]
