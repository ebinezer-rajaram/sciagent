"""The Linux pilot's ``harness/1`` sessions keep their addresses after the P4 merge.

The full P3 pilot ran its LLM half at v2 900e66a under ``harness/1``
(``.cache/pilot-llm`` on dm-devbox). After P4 moves the harness to
``harness/2``, the rescore finds those sessions only through
:func:`~sciagent.campaign.plan.legacy_session_config_digest`, so the legacy
text must stay byte-identical to what 900e66a computed, including an AG-o
session's sandbox block (limits, handoff helper, image tag, ``docker run``
argv, ``/data`` format and the ``python`` tool's caps; LOG 2026-10-27/28).

The pins are real: the ``session_config`` field and the cell digest of three
``llm-session`` cells read from a snapshot of that store (2026-10-29 13:37 UTC),
and the AG-o/anon digest computed by 900e66a's own ``session_text`` (no cell
of that arm existed yet). The other key fields are copied from the cells, not
derived, so a later fitter or data-version change (which a rescore is for)
does not touch these tests; only the legacy session text and the key's
encoding do.
"""

from __future__ import annotations

from typing import Final

import pytest

from sciagent.campaign.plan import (
    CampaignConfig,
    LLMArm,
    legacy_session_config_digest,
)
from sciagent.core.types import DataVersion, EnvVersion, FrozenDict, MetricVersion, Seed
from sciagent.registry.store import ExperimentKey

#: ``session_config`` of each default pilot arm as v2 900e66a computes it
#: (``harness/1``). AG-c/named and AG-o/named are also the stored fields of
#: the pilot's cells; AG-c/anon is the Windows pilot's cell
#: (``test_campaign_session_key.PILOT_DIGEST``).
V2_900E66A_DIGESTS: Final = (
    (
        LLMArm("AG-c", "named"),
        "33163b9115947c622ab34f4329536ea3fd54dbbd0599827e7b528e6a7db989fb",
    ),
    (
        LLMArm("AG-c", "anon"),
        "1f88aa599e18490691a4dd12b42e61e9cc7c5730882a82bb701c5d2ad509668b",
    ),
    (
        LLMArm("AG-o", "named"),
        "2bc4146fee82891d96b25e4394a9d28b15c917dc69a56b85addb2aac9ff77d34",
    ),
    (
        LLMArm("AG-o", "anon"),
        "3cac71232ffbb1c5ea1cf83845afc6eb8c2cac2ed80cc3a5467df41665ab1600",
    ),
)

#: The key fields every pilot ``llm-session`` cell shares, as stored.
_ENV_VERSION: Final = "pointproc-v2|sciagent.glm.fit/5"
_DATA_VERSION: Final = "world-obs:2000.0|heldout:2000.0|world/1"
_FIT_CONFIG: Final = (
    "q=8,0x1.0000000000000p-2,0x1.8000000000000p+1,0x1.0000000000000p+0,8"
    "|solver=newton|max_iter=100|gap=0x1.5798ee2308c3ap-27|full=512|cycles=50"
    "|starts=1|block=128|line=0|screen=2|loo=0|sweeps=0|swap_rounds=4"
)

#: (arm, system, truth, truth_digest, cell digest), seed 1: ledger sequences
#: 1, 2 and 15 of the snapshot.
PILOT_CELLS: Final = (
    (
        LLMArm("AG-c", "named"),
        "AG-c-S/named",
        "dev-000",
        "2837d50fbf52507285800d291d05195529f5dd054cd595ef0c979a0d1787e327",
        "910f66ffe15c84fc68a530d85786c5b3fa9ba92cf1f8c707f24221ba225a2de4",
    ),
    (
        LLMArm("AG-o", "named"),
        "AG-o-S/named",
        "dev-000",
        "2837d50fbf52507285800d291d05195529f5dd054cd595ef0c979a0d1787e327",
        "f60c5fb2070e8cb607c8e0ea462d8307af2f9fa6ce3d475eb9f99bcdb61383ac",
    ),
    (
        LLMArm("AG-o", "named"),
        "AG-o-S/named",
        "dev-003",
        "d9330f67b7410a5701940a9f7a62fef7a02f0e2cc43517adc4fd10bceddd7186",
        "50037735b0dbf90f2d7cd2eca31a66259fbc922785493927c3c72f674768623a",
    ),
)


@pytest.mark.parametrize(("arm", "expected"), V2_900E66A_DIGESTS)
def test_the_legacy_digest_is_900e66as(arm: LLMArm, expected: str) -> None:
    """Byte-identical to 900e66a, the AG-o sandbox block included."""
    assert legacy_session_config_digest(arm, CampaignConfig()) == expected


@pytest.mark.parametrize(
    ("arm", "system", "truth", "truth_digest", "cell"), PILOT_CELLS
)
def test_a_pilot_session_cell_address_is_rederived(
    arm: LLMArm, system: str, truth: str, truth_digest: str, cell: str
) -> None:
    """The stored cell's digest from the legacy ``session_config`` and the
    cell's other fields: what the rescore's enumeration matches on."""
    session_config = legacy_session_config_digest(arm, CampaignConfig())
    assert session_config is not None
    key = ExperimentKey(
        env_version=EnvVersion(_ENV_VERSION),
        config=FrozenDict[str, str](
            {
                "fit_config": _FIT_CONFIG,
                "kind": "llm-session",
                "system": system,
                "truth": truth,
                "truth_digest": truth_digest,
                "session_config": session_config,
            }
        ),
        data_version=DataVersion(_DATA_VERSION),
        metric_version=MetricVersion("none"),
        seed=Seed(1),
    )
    assert str(key.digest) == cell
