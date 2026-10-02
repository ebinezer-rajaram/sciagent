"""Shared helpers for ``tests/campaign``: pointproc's named truths as a campaign.

Test-side, so it may import ``environments``; the framework never does.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Final

from environments.pointproc import v2
from environments.pointproc.truths_v2 import STRUCTURE_PRIOR
from sciagent.campaign.plan import CampaignConfig, CampaignEnvironment, TruthEntry
from sciagent.investigation import EnvironmentSpec, TruthSpec
from sciagent.investigation.world import Truth
from sciagent.systems.v2.search import GPConfig, ScenarioPrior
from sciagent.systems.v2.systems import GrammarMember

ANON_CHANNELS: Final = {"size": "m1", "sign": "m2"}
TRUTH_IDS: Final = ("hawkes", "size_excitation")
MODEL: Final = "claude-haiku-4-5-20251001"


def spec(truth_id: str) -> TruthSpec:
    t = v2.TRUTHS[truth_id]
    return TruthSpec(t.structure, t.psi, t.coef, v2.CHANNELS, v2.mark_sampler)


def resolve(truth_id: str) -> Truth:
    return spec(truth_id)


def entry(truth_id: str) -> TruthEntry:
    return TruthEntry(
        id=truth_id,
        digest=hashlib.sha256(truth_id.encode()).hexdigest(),
        spec=spec(truth_id),
        in_dictionary=True,
        stratum="near",
        nearest_distance=0.18,
        dsl=truth_id,
    )


def environment() -> CampaignEnvironment:
    return CampaignEnvironment(
        name="pointproc-v2",
        channels=v2.CHANNELS,
        marks=v2.mark_sampler,
        library=v2.B_LIB_LIBRARY,
        sym_seeds=tuple(
            m.structure for m in v2.B_LIB_LIBRARY if isinstance(m, GrammarMember)
        ),
        prior=ScenarioPrior(STRUCTURE_PRIOR, v2.CHANNELS, 0.5),
        prior_id="test-prior",
        investigation=EnvironmentSpec(
            name="pointproc-v2",
            anon_channels=ANON_CHANNELS,
            forbidden=("size", "sign", "arrival"),
            resolve=resolve,
        ),
    )


def tiny_config(**overrides: Any) -> CampaignConfig:
    """Small budgets so a real unit runs in seconds."""
    base = CampaignConfig(
        seeds=(1, 2),
        control_seeds=(1,),
        fits=3,
        experiments=2,
        control_factor=2,
        llm_model=MODEL,
        llm_letter="H",
        max_turns=20,
        wall_time_s=300.0,
        gp=GPConfig(population=3, offspring=2),
    )
    return dataclasses.replace(base, **overrides)
