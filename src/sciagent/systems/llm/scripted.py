"""A deterministic provider, for driving the layer without a model.

**It makes the layer testable offline.** Everything reproducible lives in
:class:`~sciagent.systems.llm.provider.ProposalLayer` -- addressing, replay,
decoding, grammar validation -- so a scripted backend exercises all of it. A
test that a malformed payload is refused, or that two runs agree exactly, does
not need a model and should not need one.

**It is a floor, not a straw man.** :class:`ScriptedProvider` can be handed a
policy that reads the brief, so a scripted system that proposes the mechanism
the residual points at is expressible. That matters for the same reason SPEC §5
insists B4 and B5 be built strong: a comparison against a deliberately weak
stand-in would answer R1 by default.

**A17's negative control is deliberately not here.** A helper that builds a
payload carrying a ``plausibility`` belongs in the test suite, not in the shipped
package: this module is inside ``AGENT_TOOL_SURFACE``, so the analyser reads the
string constant and reports the module as an agent-reachable path that mentions a
sealed symbol. It is right to. A module whose only purpose is to construct the
thing an invariant forbids has no business being importable by the systems it
constrains, and the control reads better in ``tests/test_llm.py`` beside the
assertion it exists for.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sciagent.core.errors import ProviderError

__all__ = [
    "ScriptedProvider",
    "fixed_payload",
]

#: What a scripted policy is: it sees the brief and returns a tool payload.
type Policy = Callable[[str], Mapping[str, Any]]


class ScriptedProvider:
    """A provider whose answers come from a policy rather than from a model.

    Guarantees the answer is a pure function of the brief and of the call index,
    so a scripted run is bit-reproducible without a transcript store at all --
    which is what lets determinism tests separate "the layer is deterministic"
    from "the transcript replayed".
    """

    __slots__ = ("_calls", "_id", "_model", "_policy")

    def __init__(
        self,
        policy: Policy | Sequence[Mapping[str, Any]],
        *,
        provider_id: str = "scripted",
        model: str = "scripted/1",
    ) -> None:
        self._policy = policy
        self._id = provider_id
        self._model = model
        self._calls = 0

    @property
    def id(self) -> str:
        """Return the backend identifier."""
        return self._id

    @property
    def model(self) -> str:
        """Return the model identifier."""
        return self._model

    @property
    def calls(self) -> int:
        """Return how many completions have been requested."""
        return self._calls

    def complete(
        self, system: str, brief: str, schema: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Return the scripted payload for this brief.

        A sequence policy is consumed in order and raises
        :class:`~sciagent.core.errors.ProviderError` when exhausted, rather than
        wrapping around: a test that asked for more proposals than it scripted
        has found a real difference between what it expected and what happened,
        and silently repeating the last answer would hide it.
        """
        index = self._calls
        self._calls += 1
        if callable(self._policy):
            return self._policy(brief)
        if index >= len(self._policy):
            raise ProviderError(
                f"scripted provider was asked for completion {index} but only "
                f"{len(self._policy)} were scripted"
            )
        return self._policy[index]


def fixed_payload(
    structure: int,
    parameters: Sequence[int] = (),
    *,
    name: str = "scripted",
    rationale: str = "",
) -> dict[str, Any]:
    """Return a well-formed payload naming one structure.

    The shape a conforming provider returns, written once here so a test that
    needs a valid payload does not restate the wire format and drift from it.
    """
    return {
        "name": name,
        "rationale": rationale or f"scripted proposal of structure {structure}",
        "edits": [{"structure": structure, "parameters": list(parameters)}],
    }
