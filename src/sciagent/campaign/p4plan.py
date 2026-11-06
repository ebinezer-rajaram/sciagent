"""P4's campaign plan: the configs :func:`~sciagent.campaign.contrasts.contrast_rows`
takes (P4-PROPOSAL §4 under the decisions of §5; prereg 2026-11-05 §7).

===============  ===========================================  ============
block            design                                       units
===============  ===========================================  ============
main grid        AG-c, AG-o; H, S, O; named, anon; 80 worlds  960 sessions
Δ9 ``rescaled``  AG-c-S, AG-o-S; 80 worlds                    160 sessions
budget sweep     AG-c-S, AG-o-S named; 20 truths, seed 1;     200 sessions
                 E ∈ {0, 2, 16} at F = 40, F ∈ {20, 80} at
                 E = 8
non-LLM          :data:`~sciagent.campaign.plan.BASELINES`    560 units
                 but B-sym@10F; 80 worlds
B-sym@F=80       20 truths, seed 1                            20 units
control 3        B-sym@10F; 40 truths, seed 1                 40 units
===============  ===========================================  ============

So 1,320 sessions and 620 baseline units, 1,940 rows. The 80 worlds are 40 truths
on seeds 1 and 2. Decision 12 sends AG-S-boed and the §4.2 policy rows to P4b, so
the plan holds neither.

**The configs.** One config holds one model letter and one budget, so the plan is
eight of them: Sonnet with every baseline, Haiku, Opus, and one per swept budget.
Every contrast row is the Sonnet config's, so ``load_rows`` on that one config
holds tier 1. A sweep arm states its budget in its name
(:attr:`~sciagent.campaign.plan.LLMArm.budget`), as B-sym@F=80 does, and no such
name is a contrast system.

**Which 20 truths** (:func:`sweep_truths`). The :data:`P4_SWEEP_TRUTHS` lowest ids,
for the sweep and B-sym@F=80 alike, so those rows pair on the same worlds. Neither
P4-PROPOSAL nor the LOG fixes this. It reads no outcome, and it has nothing to
tune: the count is decision 15's, and ids are given when the split is sampled,
before any unit. The sampler interleaves the kinds so that a prefix keeps the
share, and each kind's accepted candidates are independent draws in index order,
so the prefix is the first half of each kind. The ids are sorted first, so the
order the truths arrive in does not matter. If the prefix did not keep the share
the rule's reason would be gone, so that is refused.

The plan reads a truth's id and kind and nothing else, and sets no
``control_truths``, so neither it nor its contrast rows depend on truth order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from sciagent.campaign.plan import (
    B_SYM_F80,
    B_SYM_F80_FITS,
    P4_ARMS,
    P4_SONNET_ARMS,
    CampaignConfig,
    CampaignError,
    TruthEntry,
)
from sciagent.harness.live import MODELS

#: The test split's size (decision 14).
P4_TRUTHS: Final = 40
#: The seeds of the main grid and the baselines (decision 14).
P4_SEEDS: Final = (1, 2)
#: Control 3's seeds and the sweep's seeds.
P4_SINGLE_SEED: Final = (1,)
#: How many truths the sweep and B-sym@F=80 take: the lowest ids (decision 15, and
#: the module docstring for which ones).
P4_SWEEP_TRUTHS: Final = 20
#: The swept (experiments, fits): E at F = 40, then F at E = 8 (decision 15).
P4_SWEEP_BUDGETS: Final = ((0, 40), (2, 40), (16, 40), (8, 20), (8, 80))


def sweep_truths(truths: Sequence[TruthEntry]) -> tuple[str, ...]:
    """The ids the sweep and B-sym@F=80 run on (module docstring), sorted."""
    ids = sorted(t.id for t in truths)
    if len(set(ids)) != len(ids):
        raise CampaignError("truth ids must be distinct")
    chosen = ids[:P4_SWEEP_TRUTHS]
    kept = set(chosen)
    inside = sum(t.in_dictionary for t in truths if t.id in kept)
    if 2 * inside != len(chosen):
        raise CampaignError(
            f"the first {len(chosen)} ids hold {inside} in-dictionary truths: they "
            f"do not keep the split's share, so the sweep's rule has no ground"
        )
    return tuple(chosen)


def p4_plan(truths: Sequence[TruthEntry]) -> tuple[CampaignConfig, ...]:
    """Every config of P4 on the test split ``truths`` (module docstring)."""
    if len(truths) != P4_TRUTHS:
        raise CampaignError(f"P4 runs on {P4_TRUTHS} truths, not {len(truths)}")
    chosen = sweep_truths(truths)
    sonnet = CampaignConfig(
        seeds=P4_SEEDS,
        control_seeds=P4_SINGLE_SEED,
        llm_arms=P4_SONNET_ARMS,
        llm_model=MODELS["sonnet"],
        llm_letter="S",
    )
    others = tuple(
        replace(
            sonnet,
            baselines=(),
            llm_arms=P4_ARMS,
            llm_model=MODELS[model],
            llm_letter=letter,
        )
        for letter, model in (("H", "haiku"), ("O", "opus"))
    )
    named = tuple(a for a in P4_ARMS if a.condition == "named")
    sweep = tuple(
        replace(
            sonnet,
            seeds=P4_SINGLE_SEED,
            only_truths=chosen,
            baselines=(B_SYM_F80,) if fits == B_SYM_F80_FITS else (),
            llm_arms=tuple(replace(a, budget=(e, fits)) for a in named),
            experiments=e,
            fits=fits,
        )
        for e, fits in P4_SWEEP_BUDGETS
    )
    return (sonnet, *others, *sweep)
