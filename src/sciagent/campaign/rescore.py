"""Stored LLM sessions, found by enumeration rather than by address (P4-PROPOSAL §2.2).

A session's address carries ``FIT_VERSION`` and the fit configuration's key
(:func:`~sciagent.campaign.plan.session_key`), so a fitter change moves every
session to a new address and the normal plan would *relaunch* every live
session. To rescore sessions conducted under an older fitter, the stored
``llm-session`` cells are enumerated instead (their key fields are stored in
each detail file) and matched to this campaign's LLM units on everything that
defines the *investigation*:

- the system (arm, condition, and any prompt variant or policy) is one of this
  campaign's LLM systems;
- the truth is one of the campaign's truths, with the same record digest;
- the seed is one of the campaign's seeds;
- the session configuration digest equals this campaign's
  (:func:`~sciagent.campaign.plan.session_config_digest`: model, arm,
  condition, variant, policy, budgets, turns, wall time, harness version and
  the rendered surface's digest), or, for a default arm only, the pilot's
  ``harness/1`` digest (:func:`~sciagent.campaign.plan.legacy_session_config_digest`),
  so the pilot's sessions stay reachable for rescoring after the P4 key change.

What may differ is the environment version (``FIT_VERSION``) and the fit
configuration the session ran under: that is what a rescore re-references. The
score is then addressed by :func:`~sciagent.campaign.plan.score_key` with the
old session's digest and the *current* versions, so it never collides with a
score of the same session under the old fitter.

Which session a unit gets, in order: the one at its current address; else the
one matching the current session digest; else the one matching the legacy
digest. Within a tier there must be exactly one; two would be ambiguous and
raise :class:`~sciagent.campaign.plan.CampaignError` (nothing is guessed). So
a ``harness/1`` session is used only for a unit that has no current-harness
session.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sciagent.campaign.plan import (
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    Unit,
    legacy_session_config_digest,
    session_config_digest,
    session_key,
)
from sciagent.campaign.store import ResultStore, key_from_json
from sciagent.registry.store import ExperimentKey

type Json = Any

#: Match tiers, best first (module docstring).
_CURRENT, _DIGEST, _LEGACY = 0, 1, 2


def stored_sessions(
    store: ResultStore,
    env: CampaignEnvironment,
    config: CampaignConfig,
    truths: Sequence[TruthEntry],
) -> dict[Unit, tuple[ExperimentKey, dict[str, Json]]]:
    """Every LLM unit of the campaign that has a stored session (module docstring)."""
    by_id = {t.id: t for t in truths}
    systems = set(config.llm_systems())
    digests = {s: session_config_digest(config.arm_of(s), config, env) for s in systems}
    legacy = {
        s: legacy_session_config_digest(config.arm_of(s), config) for s in systems
    }
    found: dict[Unit, list[tuple[int, ExperimentKey, dict[str, Json]]]] = {}
    for cell in store.cells():
        key = key_from_json(cell["key"])
        fields = key.config
        if fields.get("kind") != "llm-session":
            continue
        if str(key.digest) != cell["digest"]:
            raise CampaignError(f"cell {cell['digest']}: digest does not match its key")
        system = fields.get("system", "")
        truth = by_id.get(fields.get("truth", ""))
        if (
            system not in systems
            or truth is None
            or fields.get("truth_digest") != truth.digest
            or int(key.seed) not in config.seeds
        ):
            continue
        digest = fields.get("session_config")
        if digest == digests[system]:
            tier = _DIGEST
        elif legacy[system] is not None and digest == legacy[system]:
            tier = _LEGACY
        else:
            continue
        unit = Unit(truth.id, int(key.seed), system)
        found.setdefault(unit, []).append((tier, key, cell))
    out: dict[Unit, tuple[ExperimentKey, dict[str, Json]]] = {}
    for unit in sorted(found):
        current = session_key(unit, by_id[unit.truth_id], env, config)
        hits = [
            (_CURRENT if k == current else tier, k, c) for tier, k, c in found[unit]
        ]
        best = min(tier for tier, _, _ in hits)
        top = [(k, c) for tier, k, c in hits if tier == best]
        if len(top) != 1:
            raise CampaignError(
                f"{unit}: {len(top)} stored sessions match equally and none is "
                f"at the current address: "
                f"{sorted(str(k.digest)[:16] for k, _ in top)}"
            )
        out[unit] = top[0]
    return out
