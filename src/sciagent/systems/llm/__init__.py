"""LLM backends and their record/replay layer.

* :mod:`~sciagent.systems.llm.transcripts` -- content-addressed record and
  replay, which is what makes an LLM run deterministic.
* :mod:`~sciagent.systems.llm.anthropic_provider` -- a live backend over the
  Messages API, billed to API credits.
* :mod:`~sciagent.systems.llm.agent_sdk_provider` -- a live backend over the
  Claude Agent SDK, billed to a Claude subscription.

Neither live backend is imported here: a replay must not need an SDK, or an
environment without one could not reproduce a recorded run.
"""

from __future__ import annotations

from sciagent.systems.llm.transcripts import (
    RECORD,
    REPLAY,
    Completion,
    Transcript,
    TranscriptStore,
    call_address,
)

__all__ = [
    "RECORD",
    "REPLAY",
    "Completion",
    "Transcript",
    "TranscriptStore",
    "call_address",
]
