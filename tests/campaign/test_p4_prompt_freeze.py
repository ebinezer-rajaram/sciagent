"""P4's arms run the frozen prompt variant (Δ6; LOG 2026-10-27, 2026-10-28, 2026-10-29).

The owner chose (A) ``link-neutral``. The pilot's arms stay ``v1``: they name
the sessions the pilot ran, and the rescore finds those by that name
(``test_pilot_harness1_cells.py``). So the freeze is :data:`P4_ARMS` plus a guard
that does not depend on anyone choosing them: a campaign on truths outside the
dev split refuses an arm off the frozen variant, refuses a surface that is not
the one pinned, and refuses a store holding any session off the variant, planned
or not. The report's arm matching reads the arm, not the name's last characters.
"""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from campaign_support import FINGERPRINT, TRUTH_IDS, entry, environment, tiny_config
from test_campaign_analysis import row
from test_pilot_report_wording import (
    SEEDS,
    SPLIT,
    TRUTHS,
    _render,
    _table,
)

from sciagent.campaign.analysis import Session, derive
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.fingerprint import FINGERPRINT_FILE
from sciagent.campaign.freeze import (
    DEV_SPLIT,
    check_arms,
    check_stored,
    check_surfaces,
    surface_pin_key,
)
from sciagent.campaign.plan import (
    P4_ARMS,
    P4_PROMPT_VARIANT,
    PILOT_ARMS,
    CampaignConfig,
    CampaignError,
    LLMArm,
    TruthEntry,
    Unit,
    legacy_session_config_digest,
    session_key,
    surface_digest,
    system_variant,
)
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _test_truth(truth_id: str = "hawkes") -> TruthEntry:
    return dataclasses.replace(entry(truth_id), split="test")


def _pins(config: CampaignConfig) -> dict[str, str]:
    env = environment()
    return {
        surface_pin_key(a, config): surface_digest(a, config, env)
        for a in config.llm_arms
    }


@pytest.fixture
def pilot(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("pilot")


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` loaded in-process, with ``scripts/`` importable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("pilot_report")


# --------------------------------------------------------------------------
# The arms
# --------------------------------------------------------------------------


def test_the_frozen_variant_is_the_owners_choice() -> None:
    assert P4_PROMPT_VARIANT == "link-neutral"


def test_every_p4_arm_is_a_pilot_arm_on_the_frozen_variant() -> None:
    assert [(a.arm, a.condition) for a in P4_ARMS] == [
        (a.arm, a.condition) for a in PILOT_ARMS
    ]
    assert {a.prompt_variant for a in P4_ARMS} == {"link-neutral"}
    assert [a.system("S") for a in P4_ARMS] == [
        "AG-c-S/named+link-neutral",
        "AG-o-S/named+link-neutral",
        "AG-c-S/anon+link-neutral",
        "AG-o-S/anon+link-neutral",
    ]


def test_the_pilot_arms_stay_what_the_pilot_ran() -> None:
    assert {a.prompt_variant for a in PILOT_ARMS} == {"v1"}
    assert LLMArm("AG-c", "named").prompt_variant == "v1"
    assert CampaignConfig().llm_arms == PILOT_ARMS
    for arm in P4_ARMS:
        assert legacy_session_config_digest(arm, CampaignConfig()) is None


@pytest.mark.parametrize(
    ("system", "variant"),
    [
        ("AG-c-S/named", "v1"),
        ("AG-o-S/anon+policy=EIG", "v1"),
        ("AG-c-S/named+link-neutral", "link-neutral"),
        ("AG-c-H/anon+link-neutral+policy=FSD", "link-neutral"),
    ],
)
def test_a_systems_variant_is_read_from_its_name(system: str, variant: str) -> None:
    assert system_variant(system) == variant


@pytest.mark.parametrize("arm", P4_ARMS)
def test_system_variant_inverts_the_name(arm: LLMArm) -> None:
    assert system_variant(arm.system("S")) == arm.prompt_variant


# --------------------------------------------------------------------------
# The split reaches the campaign
# --------------------------------------------------------------------------


def test_the_truths_split_reaches_the_campaign(pilot: ModuleType) -> None:
    records = pilot.load_split(SPLIT)[:2]
    entries = pilot.truth_entries(records)
    assert [t.split for t in entries] == [r.split for r in records]
    assert {t.split for t in entries} == {DEV_SPLIT}


def test_a_truth_entry_must_name_its_split() -> None:
    fields = {f.name: f for f in dataclasses.fields(TruthEntry)}
    assert fields["split"].default is dataclasses.MISSING


# --------------------------------------------------------------------------
# The guard: the planned arms
# --------------------------------------------------------------------------


def test_a_dev_campaign_may_run_any_variant() -> None:
    check_arms(tiny_config(), [entry("hawkes")])
    check_arms(tiny_config(llm_arms=P4_ARMS), [entry("hawkes")])


def test_a_test_truth_refuses_an_arm_off_the_frozen_variant() -> None:
    with pytest.raises(CampaignError) as refused:
        check_arms(tiny_config(), [entry("hawkes"), _test_truth("size_excitation")])
    assert "AG-c-H/named" in str(refused.value)
    assert "link-neutral" in str(refused.value)


def test_one_v1_arm_among_frozen_ones_is_refused() -> None:
    arms = (*P4_ARMS, LLMArm("AG-c", "named"))
    with pytest.raises(CampaignError):
        check_arms(tiny_config(llm_arms=arms), [_test_truth()])


def test_a_test_campaign_on_the_frozen_variant_passes() -> None:
    check_arms(tiny_config(llm_arms=P4_ARMS), [_test_truth()])


def test_an_unnamed_split_counts_as_outside_dev() -> None:
    other = dataclasses.replace(entry("hawkes"), split="holdout-2")
    with pytest.raises(CampaignError):
        check_arms(tiny_config(), [other])


# --------------------------------------------------------------------------
# The guard: the surface at launch
# --------------------------------------------------------------------------


def test_the_pinned_surfaces_pass() -> None:
    config = tiny_config(llm_arms=P4_ARMS)
    check_surfaces(config, environment(), [_test_truth()], _pins(config))


def test_a_surface_with_no_pin_is_refused() -> None:
    config = tiny_config(llm_arms=P4_ARMS)
    with pytest.raises(CampaignError) as refused:
        check_surfaces(config, environment(), [_test_truth()], {})
    assert "no pinned surface" in str(refused.value)


def test_a_surface_that_differs_from_its_pin_is_refused() -> None:
    config = tiny_config(llm_arms=P4_ARMS)
    pins = _pins(config)
    pins[surface_pin_key(P4_ARMS[1], config)] = "0" * 64
    with pytest.raises(CampaignError) as refused:
        check_surfaces(config, environment(), [_test_truth()], pins)
    assert "AG-o-H/named+link-neutral" in str(refused.value)


def test_a_pin_is_per_budget() -> None:
    config = tiny_config(llm_arms=P4_ARMS)
    other = dataclasses.replace(config, fits=config.fits + 1)
    with pytest.raises(CampaignError):
        check_surfaces(other, environment(), [_test_truth()], _pins(config))


def test_a_pin_is_per_wall_limit() -> None:
    """The prompt states the limit, so the surface is rendered from it: another
    limit has no pin, rather than a pin it fails to match."""
    config = tiny_config(llm_arms=P4_ARMS)
    other = dataclasses.replace(config, wall_time_s=config.wall_time_s + 1.0)
    with pytest.raises(CampaignError) as refused:
        check_surfaces(other, environment(), [_test_truth()], _pins(config))
    assert "no pinned surface" in str(refused.value)


def test_the_wall_limit_enters_the_surface_digest() -> None:
    config = tiny_config(llm_arms=P4_ARMS)
    other = dataclasses.replace(config, wall_time_s=config.wall_time_s + 1.0)
    env = environment()
    for arm in P4_ARMS:
        assert surface_digest(arm, config, env) != surface_digest(arm, other, env)


def test_a_dev_campaign_needs_no_pin() -> None:
    check_surfaces(tiny_config(), environment(), [entry("hawkes")], {})


# --------------------------------------------------------------------------
# The guard: every stored session, planned or not
# --------------------------------------------------------------------------


def _cell(system: str, kind: str = "llm-session") -> dict[str, Any]:
    return {"digest": "d" * 64, "key": {"config": {"kind": kind, "system": system}}}


def test_a_stored_session_off_the_variant_is_refused_though_unplanned() -> None:
    cells = [_cell("AG-c-S/named+link-neutral"), _cell("AG-o-H/anon")]
    with pytest.raises(CampaignError) as refused:
        check_stored(cells, [_test_truth()])
    assert "AG-o-H/anon" in str(refused.value)


def test_stored_cells_of_other_kinds_are_not_sessions() -> None:
    check_stored(
        [_cell("B-sym", "baseline"), _cell("AG-c-S/named", "llm-score")],
        [_test_truth()],
    )


def test_a_dev_campaign_does_not_read_the_stored_variants() -> None:
    check_stored([_cell("AG-c-S/named")], [entry("hawkes")])


# --------------------------------------------------------------------------
# The guard in the driver
# --------------------------------------------------------------------------


def _campaign(
    root: Path,
    config: CampaignConfig,
    truths: list[TruthEntry],
    pins: dict[str, str] | None = None,
    *,
    reuse_from: str | None = None,
) -> Campaign:
    return Campaign(
        root,
        environment(),
        config,
        truths,
        DriverSettings(workers=0, poll_s=0.05, run_llm=False),
        fingerprint=FINGERPRINT,
        reuse_from=reuse_from,
        surface_pins={} if pins is None else pins,
    )


def test_the_driver_refuses_before_writing_anything(tmp_path: Path) -> None:
    root = tmp_path / "s"
    with pytest.raises(CampaignError):
        _campaign(root, tiny_config(), [_test_truth()])
    assert not root.exists()


def test_the_driver_refuses_an_unpinned_surface_before_writing(tmp_path: Path) -> None:
    root = tmp_path / "s"
    with pytest.raises(CampaignError):
        _campaign(root, tiny_config(llm_arms=P4_ARMS), [_test_truth()])
    assert not root.exists()


def test_the_driver_refuses_a_store_holding_a_v1_session(tmp_path: Path) -> None:
    root = tmp_path / "s"
    plain = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    unit = Unit("hawkes", 1, "AG-c-H/named")
    key = session_key(unit, entry("hawkes"), environment(), plain)
    with ResultStore(root) as store:
        store.put(key, {"status": 1.0}, {"unit": {}})
    frozen = tiny_config(seeds=(1,), llm_arms=P4_ARMS)
    with pytest.raises(CampaignError) as refused:
        _campaign(root, frozen, [_test_truth()], _pins(frozen))
    assert "AG-c-H/named" in str(refused.value)
    assert not (root / FINGERPRINT_FILE).exists()
    # The refusal let go of the store: a dev campaign can open it at once.
    _campaign(
        root, plain, [entry(t) for t in TRUTH_IDS], reuse_from="unrecorded"
    ).close()


def test_the_driver_opens_a_frozen_test_campaign(tmp_path: Path) -> None:
    config = tiny_config(llm_arms=P4_ARMS)
    _campaign(tmp_path / "s", config, [_test_truth()], _pins(config)).close()


def test_a_dev_campaign_is_untouched(tmp_path: Path) -> None:
    _campaign(tmp_path / "s", tiny_config(), [entry("hawkes")]).close()


# --------------------------------------------------------------------------
# scripts/pilot.py
# --------------------------------------------------------------------------


def _args(pilot: ModuleType, *argv: str) -> Any:
    return pilot.build_parser().parse_args(["--root", "unused", *argv])


def test_the_default_arm_set_is_the_pilots(pilot: ModuleType) -> None:
    assert pilot.campaign_config(_args(pilot)).llm_arms == PILOT_ARMS


def test_the_p4_arm_set(pilot: ModuleType) -> None:
    config = pilot.campaign_config(_args(pilot, "--arm-set", "p4"))
    assert config.llm_arms == P4_ARMS


def test_arms_select_within_the_arm_set(pilot: ModuleType) -> None:
    args = _args(pilot, "--arm-set", "p4", "--arms", "AG-o/anon")
    (arm,) = pilot.campaign_config(args).llm_arms
    assert arm == LLMArm("AG-o", "anon", "link-neutral")


def test_an_unknown_arm_name_is_refused(pilot: ModuleType) -> None:
    args = _args(pilot, "--arms", "AG-c/named,AG-x/anon")
    with pytest.raises(SystemExit) as refused:
        pilot.campaign_config(args)
    assert "AG-x/anon" in str(refused.value)


def test_the_committed_pins_are_the_frozen_arms_surfaces(pilot: ModuleType) -> None:
    config = CampaignConfig(llm_arms=P4_ARMS)
    assert _pins(config) == pilot.P4_SURFACES


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def _session(truth: str, seed: int, system: str) -> Session:
    return Session(
        truth,
        seed,
        system,
        {"status": 1.0},
        {"tool_calls": {"python": 1}, "record_dir": "llm", "record": "r.json"},
        VoidStatus(False, (), 0, False),
        "env",
        f"{truth}{seed}{system}",
    )


def test_the_reports_picks_see_a_variant_arm(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pick lines read ``/named`` and ``/anon`` by the arm, so a
    ``+link-neutral`` session is not dropped from them."""
    systems = ("AG-o-S/named+link-neutral", "AG-o-S/anon+link-neutral")
    table = _table()
    extra = [
        row(t, s, system, -0.72, llm=True)
        for t in TRUTHS
        for s in SEEDS
        for system in systems
    ]
    table = dataclasses.replace(
        table,
        rows=tuple(derive([*table.rows, *extra])),
        sessions=tuple(
            _session(t, s, system) for t in TRUTHS for s in SEEDS for system in systems
        ),
    )
    monkeypatch.setattr(report, "counts_recorded", lambda root, session: False)
    monkeypatch.setattr(report, "shown_softplus_note", lambda root, session: False)
    text = _render(report, tmp_path, monkeypatch, table, extra_argv=("--arm-set", "p4"))
    assert "**best gap closed (named)**" in text
    assert "**best gap closed (anonymised)**" in text
