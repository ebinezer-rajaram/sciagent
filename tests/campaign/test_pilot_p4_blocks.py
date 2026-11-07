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

from sciagent.campaign.driver import DriverSettings
from sciagent.campaign.p4plan import (
    P4_BLOCKS,
    P4_SWEEP_BUDGETS,
    P4_TRUTHS,
    p4_block,
    p4_plan,
)
from sciagent.campaign.plan import CampaignError, TruthEntry
from sciagent.campaign.store import ResultStore, key_from_json

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FINGERPRINT = "commit=abc,tree=clean"


def dev_split(n: int) -> tuple[TruthEntry, ...]:
    """Stand-in dev truths, named as the sampler names a split's truths."""
    return tuple(
        dataclasses.replace(t, id=f"dev-{k:03d}", split="dev")
        for k, t in enumerate(standin(n))
    )


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


def test_reuse_is_refused_on_a_block(pilot: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, "sonnet", (), tmp_path)


def test_reuse_is_refused_on_a_split_outside_dev(
    pilot: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, None, standin(), tmp_path)


def test_unrecorded_reuse_is_refused_too(pilot: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse("unrecorded", None, standin(), tmp_path)


def test_reuse_on_dev_with_no_block_stays_allowed(
    pilot: ModuleType, tmp_path: Path
) -> None:
    pilot.refuse_p4_reuse(FINGERPRINT, None, dev_split(2), tmp_path)


def test_no_reuse_asked_is_never_refused(pilot: ModuleType, tmp_path: Path) -> None:
    pilot.refuse_p4_reuse(None, "sonnet", standin(), tmp_path)


def test_a_frozen_amendment_is_the_only_way_through(
    pilot: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No commit is named today, and naming one is what lets it through."""
    assert frozenset() == pilot.P4_REUSE_NAMED
    monkeypatch.setattr(pilot, "P4_REUSE_NAMED", frozenset({FINGERPRINT}))
    pilot.refuse_p4_reuse(FINGERPRINT, "sonnet", standin(), tmp_path)
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse("commit=other,tree=clean", "sonnet", standin(), tmp_path)


# -- red team findings 5 and 9 (2026-11-07): stand-in truths, toy stores ---------


def store_with(root: Path, truth_ids: tuple[str, ...]) -> None:
    """A toy store holding one cell of each of ``truth_ids``."""
    with ResultStore(root) as store:
        for truth in truth_ids:
            config = {"kind": "baseline", "system": "B-lib", "truth": truth}
            key = key_from_json(
                {
                    "env_version": "toy",
                    "config": config,
                    "data_version": "toy",
                    "metric_version": "toy",
                    "seed": 1,
                }
            )
            store.put(key, {"per_event": -1.0}, {})


def test_reuse_is_refused_on_a_store_holding_a_truth_outside_the_split(
    pilot: ModuleType, tmp_path: Path
) -> None:
    """A dev run pointed at the P4 store: the store's cells say what it is."""
    store_with(tmp_path, ("dev-000", "test-000"))
    with pytest.raises(SystemExit, match=r"refused.*test-000"):
        pilot.refuse_p4_reuse(FINGERPRINT, None, dev_split(2), tmp_path)


def test_reuse_reads_the_whole_split_not_the_selected_truths(
    pilot: ModuleType, tmp_path: Path
) -> None:
    store_with(tmp_path, ("dev-000", "dev-001"))
    pilot.refuse_p4_reuse(FINGERPRINT, None, dev_split(2), tmp_path)
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, None, dev_split(2)[:1], tmp_path)
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, None, (), tmp_path)


def test_reading_the_store_leaves_it_as_it_was(
    pilot: ModuleType, tmp_path: Path
) -> None:
    store_with(tmp_path, ("test-000",))
    before = sorted((p.relative_to(tmp_path), p.read_bytes()) for p in files(tmp_path))
    with pytest.raises(SystemExit, match="refused"):
        pilot.refuse_p4_reuse(FINGERPRINT, None, dev_split(2), tmp_path)
    after = sorted((p.relative_to(tmp_path), p.read_bytes()) for p in files(tmp_path))
    assert before == after


def files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


@dataclasses.dataclass
class Launched:
    """What ``main`` handed ``Campaign``, if it got that far."""

    calls: list[dict[str, object]]


@pytest.fixture
def launcher(
    pilot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> tuple[ModuleType, Launched]:
    """``pilot.main`` with ``Campaign`` replaced by a recorder: nothing runs."""
    import sciagent.campaign.driver as driver

    launched = Launched([])

    class Recorder:
        def __init__(self, *args: object, **kwargs: object) -> None:
            names = ("root", "env", "config", "truths", "settings")
            launched.calls.append({**dict(zip(names, args, strict=True)), **kwargs})

        def run(self) -> str:
            return "recorded"

        def close(self) -> None:
            pass

    monkeypatch.setattr(driver, "Campaign", Recorder)
    monkeypatch.setattr(pilot, "truth_entries", lambda records: tuple(records))
    monkeypatch.setattr(pilot, "code_fingerprint", lambda: FINGERPRINT)
    monkeypatch.setattr(pilot, "pointproc_environment", lambda _: None)
    return pilot, launched


def run_main(
    pilot: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    split: tuple[TruthEntry, ...],
    *argv: str,
) -> None:
    monkeypatch.setattr(pilot, "load_split", lambda _: split)
    monkeypatch.setattr(sys, "argv", ["pilot.py", *argv])
    pilot.main()


def test_a_block_launches_the_whole_split_with_every_unit(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pilot, launched = launcher
    split = standin()
    run_main(pilot, monkeypatch, split, "--root", str(tmp_path), "--p4-block", "sonnet")
    (call,) = launched.calls
    assert call["truths"] == split
    assert call["config"] == p4_block("sonnet", split)
    settings = call["settings"]
    assert isinstance(settings, DriverSettings)
    assert settings.run_llm and settings.run_baselines
    assert settings.llm_limit is None
    assert call["reuse_from"] is None


def test_every_option_of_mains_parser_is_classified_for_a_block(
    pilot: ModuleType,
) -> None:
    """Red team round 2, finding 4: ``--rescore-llm`` was added in ``main()`` only
    and slipped past the block's checks. Every option of the parser ``main()``
    uses is now fixed by a block, fixed as a run setting, or one a block takes."""
    options = set(vars(pilot.run_parser().parse_args([])))
    classes = (pilot.P4_BLOCK_FIXED, pilot.P4_BLOCK_RUN_FIXED, pilot.P4_BLOCK_FREE)
    named = [name for group in classes for name in group]
    assert len(named) == len(set(named))
    assert options == set(named)
    assert "rescore_llm" in pilot.P4_BLOCK_RUN_FIXED


def test_an_option_no_one_classified_refuses_a_block(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pilot, launched = launcher
    original = pilot.run_parser

    def grown() -> object:
        parser = original()
        parser.add_argument("--new-thing", default=None)
        return parser

    monkeypatch.setattr(pilot, "run_parser", grown)
    with pytest.raises(SystemExit, match=r"refused.*new_thing"):
        run_main(
            pilot,
            monkeypatch,
            standin(),
            *("--root", str(tmp_path / "s"), "--p4-block", "sonnet"),
        )
    assert not launched.calls


@pytest.mark.parametrize(
    "extra",
    [("--no-llm",), ("--no-baselines",), ("--llm-limit", "4"), ("--rescore-llm",)],
)
def test_a_block_runs_every_unit_of_it(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra: tuple[str, ...],
) -> None:
    pilot, launched = launcher
    with pytest.raises(SystemExit, match="refused"):
        run_main(
            pilot,
            monkeypatch,
            standin(),
            "--root",
            str(tmp_path / "s"),
            "--p4-block",
            "sonnet",
            *extra,
        )
    assert not launched.calls
    assert not (tmp_path / "s").exists()


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("--truths", "0", "--reuse-from", FINGERPRINT),
        ("--arm-set", "p4-sonnet", "--seeds", "1,2", "--truths", "3"),
        ("--arm-set", "p4-sonnet", "--seeds", "1", "--b10f-truths", "2"),
        ("--no-llm", "--no-baselines"),
    ],
    ids=["plain", "truths-0-reuse", "sonnet-narrowed", "b10f-narrowed", "nothing"],
)
def test_a_run_outside_dev_needs_a_block(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    pilot, launched = launcher
    with pytest.raises(SystemExit, match=r"refused.*--p4-block"):
        run_main(pilot, monkeypatch, standin(), "--root", str(tmp_path / "s"), *argv)
    assert not launched.calls
    assert not (tmp_path / "s").exists()


def test_one_truth_outside_dev_makes_the_split_outside_dev(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pilot, launched = launcher
    split = (*dev_split(3), standin(1)[0])
    with pytest.raises(SystemExit, match=r"refused.*--p4-block"):
        run_main(pilot, monkeypatch, split, "--root", str(tmp_path), "--truths", "1")
    assert not launched.calls


@pytest.mark.parametrize("arm_set", ["p4", "p4-sonnet"])
def test_a_p4_arm_set_needs_a_block_on_dev_too(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arm_set: str,
) -> None:
    pilot, launched = launcher
    with pytest.raises(SystemExit, match=r"refused.*--p4-block"):
        run_main(
            pilot,
            monkeypatch,
            dev_split(3),
            "--root",
            str(tmp_path),
            "--arm-set",
            arm_set,
        )
    assert not launched.calls


def test_main_refuses_a_dev_run_reusing_the_p4_store(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Route 2 of finding 5: no fingerprint line is written, so a later block run
    still needs the flag."""
    pilot, launched = launcher
    store_with(tmp_path, ("test-000",))
    with pytest.raises(SystemExit, match="refused"):
        run_main(
            pilot,
            monkeypatch,
            dev_split(3),
            "--root",
            str(tmp_path),
            "--truths",
            "0",
            "--reuse-from",
            FINGERPRINT,
        )
    assert not launched.calls


OLD = "commit=" + "0" * 40 + ",tree=clean"


def established(root: Path, truth_ids: tuple[str, ...]) -> None:
    """A store whose sidecar establishes ``OLD``, holding a cell of each id."""
    from sciagent.campaign.fingerprint import guard_store

    root.mkdir(parents=True, exist_ok=True)
    guard_store(root, OLD)
    store_with(root, truth_ids)


def test_reuse_is_refused_on_an_established_store_with_no_cell(
    pilot: ModuleType, tmp_path: Path
) -> None:
    """Red team round 2, finding 3: there is nothing to carry, and the reuse line
    would make a later block run on that store need no flag."""
    established(tmp_path, ())
    with pytest.raises(SystemExit, match=r"refused.*no cell"):
        pilot.refuse_p4_reuse(OLD, None, dev_split(2), tmp_path)


def test_reuse_is_refused_on_an_established_sidecar_with_no_ledger(
    pilot: ModuleType, tmp_path: Path
) -> None:
    from sciagent.campaign.fingerprint import guard_store

    guard_store(tmp_path, OLD)
    with pytest.raises(SystemExit, match=r"refused.*no cell"):
        pilot.refuse_p4_reuse(OLD, None, dev_split(2), tmp_path)


def test_reuse_of_an_established_dev_store_with_cells_stays_allowed(
    pilot: ModuleType, tmp_path: Path
) -> None:
    established(tmp_path, ("dev-000",))
    pilot.refuse_p4_reuse(OLD, None, dev_split(2), tmp_path)


def test_main_refuses_reuse_onto_an_empty_established_store(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The red team's case B through ``main``: no reuse line is written."""
    from sciagent.campaign.fingerprint import read_fingerprints

    pilot, launched = launcher
    root = tmp_path / "store-p4-empty"
    established(root, ())
    with pytest.raises(SystemExit, match="refused"):
        run_main(
            pilot,
            monkeypatch,
            dev_split(3),
            *("--root", str(root), "--truths", "0", "--reuse-from", OLD),
            *("--no-llm", "--no-baselines", "--workers", "0"),
        )
    assert not launched.calls
    assert [e.event for e in read_fingerprints(root)] == ["establish"]


def test_main_passes_the_whole_split_to_the_reuse_refusal(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A dev store of every dev truth, reused by a run on one of them."""
    pilot, launched = launcher
    split = dev_split(3)
    store_with(tmp_path, tuple(t.id for t in split))
    run_main(
        pilot,
        monkeypatch,
        split,
        "--root",
        str(tmp_path),
        "--truths",
        "1",
        "--reuse-from",
        FINGERPRINT,
    )
    (call,) = launched.calls
    assert call["truths"] == split[:1]
    assert call["reuse_from"] == FINGERPRINT


#: The dev commands of ``pilot.py``'s docstring, and the narrowing options a dev
#: run keeps.
DEV_COMMANDS = (
    ("--truths", "20", "--workers", "4", "--llm-concurrency", "1"),
    ("--truths", "2", "--seeds", "1", "--llm-limit", "4"),
    ("--truths", "20", "--b10f-truths", "10", "--workers", "4", "--rescore-llm"),
    ("--truths", "2", "--seeds", "1", "--no-llm", "--no-baselines"),
    ("--ids", "dev-001", "--systems", "B-sym", "--arms", "AG-o/anon"),
)


@pytest.mark.parametrize("argv", DEV_COMMANDS, ids=lambda a: " ".join(a))
def test_dev_runs_launch_as_before(
    launcher: tuple[ModuleType, Launched],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    pilot, launched = launcher
    split = dev_split(20)
    run_main(pilot, monkeypatch, split, "--root", str(tmp_path), *argv)
    (call,) = launched.calls
    truths = call["truths"]
    assert isinstance(truths, tuple) and truths
    assert all(t.split == "dev" for t in truths)


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
