"""Named random streams for the truth sampler (invariant 3).

Every draw of the sampler comes from ``stream(seed, key)``: a PCG64 generator
seeded by ``SeedSequence(entropy=seed, spawn_key=(stable_key(key),))``, the
same by-name derivation as :func:`sciagent.core.program.derive_generator`. A
stream depends only on ``(seed, key)``, never on how many other streams were
drawn before it, so candidates can be evaluated in any order or process and
give the same result.
"""

from __future__ import annotations

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.core.program import stable_key


class StreamError(SciAgentError):
    """A stream was requested with an invalid seed."""


def stream(seed: int, key: str) -> np.random.Generator:
    """The generator for stream ``key`` under ``seed`` (a non-negative int)."""
    if seed < 0:
        raise StreamError(f"seed must be non-negative, got {seed}")
    sequence = np.random.SeedSequence(entropy=int(seed), spawn_key=(stable_key(key),))
    return np.random.Generator(np.random.PCG64(sequence))
