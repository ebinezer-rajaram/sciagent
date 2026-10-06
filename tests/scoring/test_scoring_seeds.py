"""The random streams' disjointness argument (``scoring/seeds.py``).

The scorer's streams (held-out, battery, telling) and the policies' streams
(``policy_seed``) must never coincide with each other or with the
investigation's world streams. The argument is about the words SeedSequence
assembles, so these tests check it on the assembled words and on the draws.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from sciagent.scoring import seeds
from sciagent.scoring.errors import ScoringError
from sciagent.scoring.seeds import (
    BATTERY,
    HELDOUT,
    POLICY,
    TELLING,
    policy_seed,
    scorer_seed,
)


def _uniforms(seq: np.random.SeedSequence, n: int = 8) -> list[float]:
    return [float(x) for x in np.random.default_rng(seq).random(n)]


def _words(seq: np.random.SeedSequence) -> tuple[int, ...]:
    """The words SeedSequence hashes, rebuilt by numpy's documented rule.

    The integer entropy splits into little-endian 32-bit words; with a spawn
    key they are zero-padded to the 4-word pool and the key's words follow.
    The rebuild is checked against numpy itself: a SeedSequence given those
    words as plain entropy must generate the same state.
    """
    raw = seq.entropy
    assert raw is not None
    values = [int(v) for v in raw] if isinstance(raw, Sequence) else [int(raw)]
    words: list[int] = []
    for value in values:
        words.append(value % 2**32)
        value >>= 32
        while value:
            words.append(value % 2**32)
            value >>= 32
    if seq.spawn_key and len(words) < seq.pool_size:
        words += [0] * (seq.pool_size - len(words))
    words += [int(k) for k in seq.spawn_key]
    rebuilt = np.random.SeedSequence(entropy=words)
    assert rebuilt.generate_state(8).tolist() == seq.generate_state(8).tolist()
    return tuple(words)


def test_tags_are_distinct_and_fixed() -> None:
    assert (HELDOUT, BATTERY, POLICY, TELLING) == (2, 3, 4, 5)
    assert len({HELDOUT, BATTERY, POLICY, TELLING}) == 4


def test_policy_seed_is_the_documented_spawn_key() -> None:
    seq = policy_seed(7, 3, 1, 15)
    assert seq.entropy == 7
    assert seq.spawn_key == (POLICY, 3, 1, 15)
    extra = policy_seed(7, 3, 1, 15, 2)
    assert extra.spawn_key == (POLICY, 3, 1, 15, 2)


def test_policy_seed_is_deterministic_and_keyed() -> None:
    a = _uniforms(policy_seed(1, 0, 0, 0))
    assert a == _uniforms(policy_seed(1, 0, 0, 0))
    for other in (
        policy_seed(2, 0, 0, 0),
        policy_seed(1, 1, 0, 0),
        policy_seed(1, 0, 1, 0),
        policy_seed(1, 0, 0, 1),
        policy_seed(1, 0, 0, 0, 0),
    ):
        assert a != _uniforms(other)


def test_policy_seed_rejects_bad_input() -> None:
    with pytest.raises(ScoringError):
        policy_seed(-1, 0, 0, 0)
    with pytest.raises(ScoringError):
        policy_seed(True, 0, 0, 0)
    with pytest.raises(ScoringError):
        policy_seed(1, -1, 0, 0)
    with pytest.raises(ScoringError):
        policy_seed(1, 0, 2**32, 0)


@pytest.mark.parametrize("seed", [0, 1, 7, 2**40, 2**63 - 1])
def test_every_stream_family_assembles_disjoint_words(seed: int) -> None:
    """World (no spawn key, at most 5 words) vs scorer/telling/policy (>= 8)."""
    world = {
        _words(np.random.SeedSequence([seed, stream, index]))
        for stream in (0, 1)
        for index in range(8)
    }
    scorer = {
        _words(scorer_seed(tag, truth, seed, *extra))
        for tag in (HELDOUT, BATTERY, TELLING)
        for truth in ("hawkes", "dev-008")
        for extra in ((), (0,), (1, 2, 3))
    }
    policy = {
        _words(policy_seed(seed, decision, k, r))
        for decision in range(3)
        for k in (0, 1, 2**32 - 1)
        for r in range(3)
    }
    assert all(len(w) <= 5 for w in world)
    assert all(len(w) >= 7 for w in scorer | policy)
    assert not world & scorer
    assert not world & policy
    assert not scorer & policy
    # The tag is the first spawn-key word, right after the 4-word entropy pool.
    assert {w[4] for w in scorer} == {HELDOUT, BATTERY, TELLING}
    assert {w[4] for w in policy} == {POLICY}


@pytest.mark.parametrize("seed", [0, 1, 7, 2**40])
def test_policy_and_telling_draws_differ_from_the_world_and_the_battery(
    seed: int,
) -> None:
    policy = _uniforms(policy_seed(seed, 0, 0, 0))
    telling = _uniforms(scorer_seed(TELLING, "size_excitation", seed, 0, 0, 0))
    battery = _uniforms(scorer_seed(BATTERY, "size_excitation", seed, 0, 0, 0))
    assert len({tuple(policy), tuple(telling), tuple(battery)}) == 3
    for stream in (0, 1):
        for index in range(16):
            world = _uniforms(np.random.SeedSequence([seed, stream, index]))
            assert world not in (policy, telling)


def test_module_documents_every_tag() -> None:
    doc = seeds.__doc__ or ""
    for name in ("HELDOUT", "BATTERY", "POLICY", "TELLING"):
        assert name in doc
