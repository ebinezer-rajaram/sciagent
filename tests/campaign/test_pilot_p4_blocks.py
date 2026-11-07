"""``scripts/pilot.py`` runs ``p4_plan``'s blocks, and refuses ``--reuse-from`` on the
P4 store (prereg 2026-11-05 section 2.10). Stand-in truths only: no store opens and
no unit runs.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest
from campaign_support import entry

from sciagent.campaign.p4plan import (
    P4_BLOCKS,
    P4_SWEEP_BUDGETS,
    P4_TRUTHS,
    p4_block,
    p4_plan,
)
from sciagent.campaign.plan import CampaignError, TruthEntry

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FINGERPRINT = "commit=abc,tree=clean"


def standin(n: int = P4_TRUTHS) -> tuple[TruthEntry, ...]:
    return tuple(
        dataclasses.replace(
            entry("hawkes"),
            id=f"test-{k:03d}",
            in_dictionary=k % 2 == 0,
            split="test",
        )
        for k in range(n)
    )


@pytest.fixture
def pilot(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("pilot")
    monkeypatch.setattr(module, "load_split", lambda _: ())
    monkeypatch.setattr(module, "truth_entries", lambda _: standin())
    return module


def parsed(pilot: ModuleType, *argv: str) -> object:
    return pilot.build_parser().parse_args(list(argv))


def test_every_config_of_the_plan_is_one_block_in_the_plans_order() -> None:
    truths = standin()
    assert len(P4_BLOCKS) == len(p4_plan(truths))
    assert tuple(p4_block(name, truths) for name in P4_BLOCKS) == p4_plan(truths)


def test_the_blocks_are_named_for_what_they_hold() -> None:
    truths = standin()
    assert p4_block("sonnet", truths).llm_letter == "S"
    assert p4_block("haiku", truths).llm_letter == "H"
    assert p4_block("opus", truths).llm_letter == "O"
    for e, fits in P4_SWEEP_BUDGETS:
        config = p4_block(f"sweep-e{e}-f{fits}", truths)
        assert (config.experiments, config.fits) == (e, fits)


def test_an_unknown_block_is_refused() -> None:
    with pytest.raises(CampaignError):
        p4_block("sonnet2", standin())


def test_the_wrong_number_of_truths_is_refused() -> None:
    with pytest.raises(CampaignError):
        p4_block("sonnet", standin(20))


def test_campaign_config_gives_the_blocks_config(pilot: ModuleType) -> None:
    for name in P4_BLOCKS:
        args = parsed(pilot, "--p4-block", name)
        assert pilot.campaign_config(args) == p4_block(name, standin())


def test_with_no_block_the_config_is_as_before(pilot: ModuleType) -> None:
    config = pilot.campaign_config(parsed(pilot))
    assert config.seeds == (1, 2, 3)
    assert config.llm_letter == "S"


def test_an_unknown_block_stops_at_the_parser(
    pilot: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        parsed(pilot, "--p4-block", "nope")
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [
        ("--truths", "3"),
        ("--ids", "test-000"),
        ("--seeds", "1"),
        ("--control-seeds", "2"),
        ("--b10f-truths", "4"),
        ("--systems", "B-sym"),
        ("--max-turns", "50"),
        ("--arms", "AG-o/anon"),
        ("--arm-set", "p4"),
    ],
)
def test_a_block_takes_no_option_that_narrows_or_changes_it(
    pilot: ModuleType, extra: tuple[str, str]
) -> None:
    args = parsed(pilot, "--p4-block", "sonnet", *extra)
    with pytest.raises(SystemExit, match="refused"):
        pilot.campaign_config(args)


def test_reuse_is_refused_on_a_block(pilot: ModuleType) -> None:
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, "sonnet", ())


def test_reuse_is_refused_on_a_split_outside_dev(pilot: ModuleType) -> None:
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, None, standin())


def test_unrecorded_reuse_is_refused_too(pilot: ModuleType) -> None:
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse("unrecorded", None, standin())


def test_reuse_on_dev_with_no_block_stays_allowed(pilot: ModuleType) -> None:
    dev = tuple(dataclasses.replace(t, split="dev") for t in standin(2))
    pilot.refuse_p4_reuse(FINGERPRINT, None, dev)


def test_no_reuse_asked_is_never_refused(pilot: ModuleType) -> None:
    pilot.refuse_p4_reuse(None, "sonnet", standin())


def test_a_frozen_amendment_is_the_only_way_through(
    pilot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No commit is named today, and naming one is what lets it through."""
    assert frozenset() == pilot.P4_REUSE_NAMED
    monkeypatch.setattr(pilot, "P4_REUSE_NAMED", frozenset({FINGERPRINT}))
    pilot.refuse_p4_reuse(FINGERPRINT, "sonnet", standin())
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse("commit=other,tree=clean", "sonnet", standin())


def test_main_refuses_before_any_store_opens(
    pilot: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal is wired: ``Campaign`` is never reached."""
    root = tmp_path / "store"
    monkeypatch.setattr(pilot, "code_fingerprint", lambda: FINGERPRINT)
    monkeypatch.setattr(pilot, "pointproc_environment", lambda _: None)
    argv = ["pilot.py", "--root", str(root), "--p4-block", "sonnet"]
    monkeypatch.setattr(sys, "argv", [*argv, "--reuse-from", FINGERPRINT])
    with pytest.raises(SystemExit, match="refused"):
        pilot.main()
    assert not root.exists()
