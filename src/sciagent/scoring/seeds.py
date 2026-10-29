"""The scorer's and the policies' random streams, disjoint from the world's (SPEC §4.3).

Held-out data must come from "fresh data from the truth": no draw the agent
or any system saw may be reused. The investigation's world draws
experiment k of seed s from ``SeedSequence([s, stream, k])`` with stream 0
(observational) or 1 (experiments), no spawn key
(``sciagent/investigation/world.py``). Every scorer stream is instead

    SeedSequence(entropy=seed, spawn_key=(tag, key_hi, key_lo, *extra))

with ``tag`` a scorer stream id (:data:`HELDOUT`, :data:`BATTERY` or
:data:`TELLING`), ``key_hi, key_lo`` the two 32-bit halves of
:func:`~sciagent.core.program.stable_key` of the truth id, and ``extra``
small non-negative indices (replicate, side, experiment).

The evidence policies (``systems/v2/policies.py``, Δ11) simulate their
entertained models from

    SeedSequence(entropy=seed, spawn_key=(POLICY, decision, k, r, *extra))

(:func:`policy_seed`): ``decision`` is the policy decision's index, ``k`` the
simulated model's index (an entertained hypothesis, or a reserved index for
the model-free reference and the random draw), ``r`` the replicate. A policy
never sees the truth, so its key carries no truth id: two truths on one seed
share policy streams, which only pairs them.

Why the streams are disjoint:

- **from the world's.** numpy assembles a SeedSequence's input as the entropy
  words, zero-padded to the 4-word pool when a spawn key is present, followed
  by the spawn-key words. A scorer stream therefore always assembles at least
  4 + 3 = 7 words and a policy stream at least 4 + 4 = 8, while a world
  stream (seed ≤ 2 words, stream, index) assembles at most 5 words with no
  spawn key; no scorer or policy input equals a world input, so they are
  distinct inputs to SeedSequence's hash, whose documented guarantee is that
  distinct inputs give independent streams.
- **from each other.** The tag is the first spawn-key word (word 4 of the
  assembled input), and it differs between the held-out (:data:`HELDOUT`),
  battery (:data:`BATTERY`), policy (:data:`POLICY`) and telling
  (:data:`TELLING`) families; within one tag the truth key and the indices
  differ. Inputs of different lengths are also distinct: every input here is
  longer than the 4-word pool, and words past the pool are each mixed in.

Each spawn-key element is a non-negative integer below 2**32, so each is
exactly one word and no two different keys assemble to the same words. Seeds
are integers in ``[0, 2**63)``, the world's range.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from sciagent.core.program import stable_key
from sciagent.scoring.errors import ScoringError

#: Spawn-key tag of the held-out observational stream.
HELDOUT: Final = 2
#: Spawn-key tag of the interventional battery's streams.
BATTERY: Final = 3
#: Spawn-key tag of the evidence policies' simulation streams (Δ11).
POLICY: Final = 4
#: Spawn-key tag of the telling-diagnostic streams (``scoring/telling.py``).
TELLING: Final = 5

_WORD: Final = 2**32


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
        raise ScoringError(f"seed must be an integer in [0, 2**63), got {seed!r}")


def _check_words(values: tuple[int, ...]) -> None:
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ScoringError(f"stream index {value!r} is not an integer")
        if not 0 <= value < _WORD:
            raise ScoringError(f"stream index {value} is outside [0, 2**32)")


def scorer_seed(
    tag: int, truth_id: str, seed: int, *extra: int
) -> np.random.SeedSequence:
    """The SeedSequence of one scorer stream (module docstring)."""
    _check_seed(seed)
    if not truth_id:
        raise ScoringError("truth_id must be a non-empty string")
    _check_words((tag, *extra))
    key = stable_key(truth_id)
    words = (tag, (key >> 32) % _WORD, key % _WORD, *extra)
    return np.random.SeedSequence(entropy=seed, spawn_key=words)


def policy_seed(
    seed: int, decision: int, k: int, r: int, *extra: int
) -> np.random.SeedSequence:
    """``SeedSequence(seed, spawn_key=(POLICY, decision, k, r, *extra))``."""
    _check_seed(seed)
    words = (POLICY, decision, k, r, *extra)
    _check_words(words)
    return np.random.SeedSequence(entropy=seed, spawn_key=words)


def generator(seq: np.random.SeedSequence) -> np.random.Generator:
    """A PCG64 generator on ``seq`` (the world uses ``default_rng``, also PCG64)."""
    return np.random.Generator(np.random.PCG64(seq))
