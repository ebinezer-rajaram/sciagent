"""A campaign outside the dev split runs the frozen prompt and nothing else (Δ6).

The owner froze ``link-neutral`` for P4 (LOG 2026-10-29), and the guard listed on
2026-10-28 enforces it in code rather than by a choice of arm list. The split
is what decides: :attr:`~sciagent.campaign.plan.TruthEntry.split`, threaded
from the truth's record. A truth outside :data:`DEV_SPLIT` (an unknown split
counts as outside) brings three refusals, all raised by
:class:`~sciagent.campaign.driver.Campaign` before a unit runs:

- :func:`check_arms`: every planned arm carries
  :data:`~sciagent.campaign.plan.P4_PROMPT_VARIANT`;
- :func:`check_surfaces`: each arm's rendered surface
  (:func:`~sciagent.campaign.plan.surface_digest`) equals the digest pinned for
  it at its budgets, so a later edit of a prompt or schema is refused at launch
  and not only moved to a new address;
- :func:`check_stored`: every ``llm-session`` cell in the store is on the
  variant, planned or not, so a session from an earlier arm list cannot sit
  beside the frozen ones.

Dev campaigns are untouched: the anchoring check runs both variants there, and
the pilot's ``v1`` sessions stay readable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from sciagent.campaign.plan import (
    P4_PROMPT_VARIANT,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    LLMArm,
    TruthEntry,
    surface_digest,
    system_variant,
)
from sciagent.campaign.store import LEDGER_FILE, ResultStore

#: The one split a campaign may run any prompt variant on.
DEV_SPLIT: Final = "dev"


def _outside_dev(truths: Sequence[TruthEntry]) -> bool:
    return any(t.split != DEV_SPLIT for t in truths)


def surface_pin_key(arm: LLMArm, config: CampaignConfig) -> str:
    """The key of an arm's pin: what its surface is rendered from, other than
    the environment (the model letter is not in the surface)."""
    policy = "none" if arm.policy is None else arm.policy
    return (
        f"{arm.arm}/{arm.condition}|{arm.prompt_variant}|{policy}|"
        f"experiments={config.experiments}|fits={config.fits}"
    )


def check_arms(config: CampaignConfig, truths: Sequence[TruthEntry]) -> None:
    """Refuse a planned arm off the frozen variant (module docstring)."""
    if not _outside_dev(truths):
        return
    wrong = [
        a.system(config.llm_letter)
        for a in config.llm_arms
        if a.prompt_variant != P4_PROMPT_VARIANT
    ]
    if wrong:
        raise CampaignError(
            f"truths outside the {DEV_SPLIT} split run the frozen prompt variant "
            f"{P4_PROMPT_VARIANT!r} only; these arms are not on it: {wrong}"
        )


def check_surfaces(
    config: CampaignConfig,
    env: CampaignEnvironment,
    truths: Sequence[TruthEntry],
    pins: Mapping[str, str],
) -> None:
    """Refuse an arm whose surface is not its pinned one (module docstring)."""
    if not _outside_dev(truths):
        return
    for arm in config.llm_arms:
        system = arm.system(config.llm_letter)
        key = surface_pin_key(arm, config)
        pin = pins.get(key)
        if pin is None:
            raise CampaignError(f"{system} has no pinned surface ({key})")
        digest = surface_digest(arm, config, env)
        if digest != pin:
            raise CampaignError(
                f"{system}: the surface is {digest}, the pin ({key}) is {pin}"
            )


def check_store(root: Path, truths: Sequence[TruthEntry]) -> None:
    """:func:`check_stored` on the store at ``root``, if there is one.

    Read-only, and it reads no cell for a dev campaign; the driver calls it
    before the code-fingerprint guard, so a refusal leaves the store as it was.
    """
    if not _outside_dev(truths) or not (root / LEDGER_FILE).is_file():
        return
    with ResultStore(root, read_only=True) as store:
        check_stored(store.cells(), truths)


def check_stored(
    cells: Iterable[Mapping[str, Any]], truths: Sequence[TruthEntry]
) -> None:
    """Refuse a store holding a session off the frozen variant (module docstring).

    ``cells`` is every cell of the store, not the planned ones.
    """
    if not _outside_dev(truths):
        return
    wrong = sorted(
        {
            str(fields["system"])
            for fields in (c["key"]["config"] for c in cells)
            if fields.get("kind") == "llm-session"
            and system_variant(str(fields["system"])) != P4_PROMPT_VARIANT
        }
    )
    if wrong:
        raise CampaignError(
            f"the store holds sessions off the frozen prompt variant "
            f"{P4_PROMPT_VARIANT!r}: {wrong}"
        )
