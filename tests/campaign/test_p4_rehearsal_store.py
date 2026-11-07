"""``scripts/p4_rehearsal_store.py``: the store section 2.10's rehearsal runs on.

The rule is ``docs/v2/prereg/2026-11-05_p4-search-misses.md``, section 2.10. The
maker takes its plan from ``p4_search_misses.rehearsal_world``, so what it writes
and what the script reads are one address. These tests hold that with the script's
own readers. Jobs are stubbed unless the test says otherwise, no session reaches a
model, and no test here touches a P4 unit or draws a frozen root.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
from campaign_support import entry, environment, tiny_config
from test_campaign import clean

from sciagent.campaign import driver as driver_module
from sciagent.campaign.analysis import load_rows
from sciagent.campaign.contrasts import contrast_rows
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import UnitResult
from sciagent.campaign.llm import SessionOutcome
from sciagent.campaign.plan import (
    P4_PROMPT_VARIANT,
    LLMArm,
    Unit,
    baseline_key,
    baseline_units,
    llm_units,
    score_key,
    session_key,
)
from sciagent.campaign.store import ResultStore
from sciagent.glm.canonical import canonicalise
from sciagent.glm.syntax import render
from sciagent.harness.scripted import ScriptedDriver
from sciagent.investigation.view import Condition

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
maker: Any = importlib.import_module("p4_rehearsal_store")
p4m: Any = importlib.import_module("p4_search_misses")

DEV_IDS: Final = tuple(f"dev-{i:03d}" for i in range(20))
FIRST_EIGHT: Final = DEV_IDS[:8]
FINGERPRINT: Final = "commit=abc,tree=clean"
ONES: Final = dict.fromkeys(p4m.BLAS_VARIABLES, "1")
#: A budget the planted script can run on, and no other number matters here.
BUDGET: Final = {
    "fits": 4,
    "experiments": 1,
    "control_factor": 2,
    "gp_population": 3,
    "gp_offspring": 2,
}
#: Baselines on two seeds, B-sym@10F on seed 1, and six arms on two seeds.
EIGHT_TRUTHS_BASELINES: Final = 8 * (7 * 2 + 1)
EIGHT_TRUTHS_SESSIONS: Final = 8 * 6 * 2


@dataclass(frozen=True)
class World:
    repo: Path
    split: Path
    store: Path
    plan: Path
    env: Any
    records: tuple[Any, ...]


def truth_of(record: Any) -> Any:
    index = int(record.id.removeprefix("dev-"))
    return replace(entry("hawkes"), id=record.id, in_dictionary=index % 2 == 0)


def write_plan(world: World, **over: Any) -> None:
    spec = {"truths": list(FIRST_EIGHT), "seeds": [1, 2], **BUDGET, **over}
    world.plan.write_text(json.dumps(spec))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A checkout with no freeze file, a stand-in dev split, and a plan."""
    repo = tmp_path / "repo"
    store = repo / ".cache" / "drafts" / "rehearsal" / "store"
    store.parent.mkdir(parents=True)
    split = tmp_path / "dev"
    split.mkdir()
    (split / "truths.json").write_text("{}")
    records = tuple(SimpleNamespace(id=x, split="dev") for x in DEV_IDS)
    env = environment()
    monkeypatch.setattr(p4m, "SPLIT_SHA256", p4m.split_digest(split, p4m.DEV_RELATIVE))
    monkeypatch.setattr(p4m.pilot, "load_split", lambda path: records)
    monkeypatch.setattr(p4m.pilot, "pointproc_environment", lambda given: env)
    monkeypatch.setattr(
        p4m.pilot, "truth_entries", lambda given: tuple(truth_of(r) for r in given)
    )
    monkeypatch.setattr(p4m.pilot, "code_fingerprint", lambda repo: FINGERPRINT)
    monkeypatch.setattr(p4m, "initial_environment", lambda: dict(ONES))
    built = World(repo, split, store, tmp_path / "plan.json", env, records)
    write_plan(built)
    return built


def toy_text() -> str:
    return render(canonicalise(entry("hawkes").structure))


def reading_of(per_event: float) -> dict[str, float]:
    return {
        "status": 1.0,
        "per_event": per_event,
        "certified": 1.0,
        "exact": 0.0,
        "distance": 0.5,
        "has_structure": 1.0,
        "similarity": 0.9,
        "fits_used": 1.0,
    }


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The jobs answer at once, and each session shows the driver it was given."""
    drivers: list[Any] = []

    def baseline(job: Any) -> tuple[UnitResult, ...]:
        detail = {"structure": toy_text()}
        return tuple(
            UnitResult(s, True, reading_of(-0.5), detail, 0.0) for s in job.systems
        )

    def score(job: Any) -> UnitResult:
        detail = {"structure": job.submission}
        return UnitResult(job.system, True, reading_of(-0.625), detail, 0.0)

    def session(unit: Unit, *args: Any, driver: Any, **kw: Any) -> SessionOutcome:
        drivers.append(driver)
        text = toy_text()
        detail = {
            "outcome": "submitted",
            "record": "record.json",
            "submission": text,
            "fitted": [text],
        }
        reading = {"status": 1.0, "void": 0.0}
        return SessionOutcome(unit, False, reading, detail, text, (text,), 0.0, {})

    def no_live_session(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a live session was launched")

    monkeypatch.setattr(driver_module, "run_baseline_job", baseline)
    monkeypatch.setattr(driver_module, "run_score_job", score)
    monkeypatch.setattr(driver_module, "run_session", session)
    monkeypatch.setattr(driver_module, "run_session_isolated", no_live_session)
    return drivers


def make(world: World, only: Sequence[str] = ()) -> Any:
    return maker.make_store(
        world.repo,
        split=world.split,
        store=world.store,
        plan=world.plan,
        only=tuple(only),
        workers=0,
        poll_s=0.01,
    )


def read_back(world: World) -> tuple[Any, Any, Any, Any]:
    """The store as the script sees it, from the plan as the script builds it."""
    env, plan, truths = p4m.rehearsal_world(world.plan, world.split)
    table, cells = p4m.load_store(world.store, env, plan, truths)
    return table, cells, plan, truths


# --------------------------------------------------------------------------
# Every address is the one the script reads
# --------------------------------------------------------------------------


def test_every_unit_is_read_back_by_the_scripts_own_readers(
    world: World, stubbed: list[Any]
) -> None:
    make(world)
    table, cells, plan, truths = read_back(world)
    assert table.missing == {}
    assert len(table.sessions) == EIGHT_TRUTHS_SESSIONS
    assert len(table.rows) == EIGHT_TRUTHS_BASELINES + EIGHT_TRUTHS_SESSIONS
    p4m.check_table(table, cells)
    assert {r.truth for r in table.rows} == set(FIRST_EIGHT)
    assert {r.seed for r in table.rows} == {1, 2}
    census = p4m.build_census(
        table, cells, world.env.channels, frozenset(contrast_rows(plan, truths))
    )
    assert len(census.rows) == len(table.rows)


def test_the_plan_the_script_starts_with_is_the_one_the_maker_wrote(
    world: World, stubbed: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through ``rehearsal_setup``, the script's own start."""
    monkeypatch.setattr(p4m, "check_tree", lambda *a: None)
    monkeypatch.setattr(p4m, "check_fit", lambda *a: None)
    monkeypatch.setattr(p4m, "head_commit", lambda repo: "h" * 40)
    args = SimpleNamespace(
        split=world.split,
        test_split=None,
        store=world.store,
        rehearsal_plan=world.plan,
    )
    setup = p4m.rehearsal_setup(args, world.repo)
    summary = make(world)
    assert setup.plan == summary.plan
    assert [t.id for t in setup.truths] == [t.id for t in summary.truths]
    (config,) = setup.plan
    by_id = {t.id: t for t in setup.truths}
    ids = list(by_id)
    with ResultStore(world.store, read_only=True) as store:
        for unit in baseline_units(ids, config):
            key = baseline_key(unit, by_id[unit.truth_id], setup.env, config)
            assert store.done(key), unit
        for unit in llm_units(ids, config):
            session = session_key(unit, by_id[unit.truth_id], setup.env, config)
            assert store.done(session), unit
            assert store.done(score_key(unit, session, setup.env, config)), unit


def test_the_maker_builds_its_config_with_rehearsal_configs_itself(
    world: World, stubbed: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a second copy: a changed config moves both ends together."""
    real = p4m.rehearsal_configs
    moved = {"fits": 5}

    def changed(spec: Any) -> Any:
        return real({**spec, **moved})

    monkeypatch.setattr(p4m, "rehearsal_configs", changed)
    make(world)
    table, cells, *_ = read_back(world)
    assert table.missing == {}
    assert read_back(world)[2][0].fits == 5
    p4m.check_table(table, cells)


def test_only_a_part_of_the_plan_keeps_its_addresses_and_stays_missing_for_the_rest(
    world: World, stubbed: list[Any]
) -> None:
    """``--only`` selects units and addresses none: the script reads those cells,
    and refuses the store until every other unit is there."""
    make(world, only=("dev-000",))
    table, cells, *_ = read_back(world)
    assert {r.truth for r in table.rows} == {"dev-000"}
    assert len(table.rows) == (EIGHT_TRUTHS_BASELINES + EIGHT_TRUTHS_SESSIONS) // 8
    assert sum(table.missing.values()) == 7 * len(table.rows)
    with pytest.raises(p4m.GuardFailure, match="missing"):
        p4m.check_table(table, cells)


def test_a_second_run_writes_nothing_new(world: World, stubbed: list[Any]) -> None:
    make(world)
    before = p4m.store_manifest(world.store / "details")
    again = make(world)
    assert p4m.store_manifest(world.store / "details") == before
    assert again.run.skipped_done > 0
    assert not again.run.recorded


# --------------------------------------------------------------------------
# No LLM call: every session is scripted
# --------------------------------------------------------------------------


def test_every_session_runs_on_a_scripted_driver(
    world: World, stubbed: list[Any]
) -> None:
    make(world)
    assert len(stubbed) == EIGHT_TRUTHS_SESSIONS
    assert all(isinstance(d, ScriptedDriver) for d in stubbed)


def test_the_factory_never_hands_back_no_driver(world: World) -> None:
    """``None`` is what makes the campaign launch a live session."""
    _, (config,), truths = p4m.rehearsal_world(world.plan, world.split)
    factory = maker.scripted_factory(config, truths)
    for unit in llm_units([t.id for t in truths], config):
        assert isinstance(factory(unit), ScriptedDriver)


@pytest.mark.parametrize("condition", ["named", "rescaled", "anon"])
def test_the_script_submits_the_truth_in_the_units_own_names(
    condition: Condition,
) -> None:
    arm = LLMArm("AG-c", condition, P4_PROMPT_VARIANT)
    config = tiny_config(llm_arms=(arm,), fits=4, experiments=1)
    truth = entry("hawkes")
    driver = maker.scripted_factory(config, (truth,))(
        Unit(truth.id, 1, arm.system(config.llm_letter))
    )
    names = [call[0] for step in driver.script for call in step.calls]
    assert names.count("submit") == 1
    assert names.count("fit") == maker.PLANTED_FITS
    assert names.count("run_experiment") == 1


# --------------------------------------------------------------------------
# What it refuses, before anything is opened
# --------------------------------------------------------------------------


@pytest.fixture
def nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a campaign was built before the refusal")

    monkeypatch.setattr(maker, "Campaign", forbidden)


@pytest.mark.parametrize("seeds", [[4, 5], [1, 3], [5, 6], [2, 1]])
def test_seeds_other_than_one_and_two_are_refused(
    world: World, nothing_runs: None, seeds: list[int]
) -> None:
    """Seeds 4 to 6 stay undrawn, whatever a plan says. ``control_seeds`` follows,
    or the config would refuse first and this check would go untested."""
    write_plan(world, seeds=seeds, control_seeds=seeds[:1])
    with pytest.raises(p4m.GuardFailure, match="seeds 1 and 2"):
        make(world)


def test_seeds_with_no_control_seed_among_them_are_refused_by_the_config(
    world: World, nothing_runs: None
) -> None:
    write_plan(world, seeds=[4, 5])
    with pytest.raises(p4m.CampaignError, match="control seeds"):
        make(world)


def test_any_truths_but_the_first_eight_are_refused(
    world: World, nothing_runs: None
) -> None:
    write_plan(world, truths=list(DEV_IDS[13:20]))
    with pytest.raises(p4m.GuardFailure, match="first eight"):
        make(world)


@pytest.mark.parametrize(
    ("key", "value"), [("fits", maker.PLANTED_FITS - 1), ("experiments", 0)]
)
def test_a_budget_the_script_cannot_run_on_is_refused(
    world: World, nothing_runs: None, key: str, value: int
) -> None:
    write_plan(world, **{key: value})
    with pytest.raises(p4m.GuardFailure, match="planted script"):
        make(world)


def test_only_a_truth_of_the_plan_may_be_named(
    world: World, nothing_runs: None
) -> None:
    with pytest.raises(p4m.GuardFailure, match="dev-013"):
        make(world, only=("dev-013",))


def test_a_store_outside_drafts_is_refused(
    world: World, nothing_runs: None, tmp_path: Path
) -> None:
    moved = replace(world, store=tmp_path / "elsewhere")
    with pytest.raises(p4m.GuardFailure, match="drafts"):
        make(moved)


def test_the_freeze_file_refuses_the_store_too(
    world: World, nothing_runs: None
) -> None:
    path = world.repo / p4m.FREEZE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("abc1234\n")
    with pytest.raises(p4m.GuardFailure, match="FREEZE"):
        make(world)


def test_a_split_that_is_not_the_dev_constant_is_refused(
    world: World, nothing_runs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p4m, "SPLIT_SHA256", "0" * 64)
    with pytest.raises(p4m.GuardFailure, match="split hash"):
        make(world)


def test_the_blas_variables_must_be_one_at_start(
    world: World, nothing_runs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p4m, "initial_environment", lambda: {})
    with pytest.raises(p4m.GuardFailure, match="must be 1"):
        make(world)


def test_a_dirty_tree_refuses_through_the_fingerprint(
    world: World, nothing_runs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def dirty(repo: Path) -> str:
        raise p4m.CampaignError("the working tree is not clean")

    monkeypatch.setattr(p4m.pilot, "code_fingerprint", dirty)
    with pytest.raises(p4m.CampaignError, match="not clean"):
        make(world)


def test_the_options_set_no_seed_no_root_and_no_budget() -> None:
    """The plan is the input, as for the script: a number comes in one way."""
    parser = maker.build_parser()
    options = {o for a in parser._actions for o in a.option_strings}
    assert options == {
        "-h",
        "--help",
        "--split",
        "--store",
        "--rehearsal-plan",
        "--only",
        "--workers",
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["--workers", "3"])


def test_main_turns_a_refusal_into_a_message_and_an_exit_code(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    write_plan(world, seeds=[4, 5], control_seeds=[4])
    code = maker.main(
        [
            "--split",
            str(world.split),
            "--store",
            str(world.store),
            "--rehearsal-plan",
            str(world.plan),
        ],
        repo=world.repo,
    )
    assert code == maker.REFUSED_EXIT
    assert "seeds 1 and 2" in capsys.readouterr().out


# --------------------------------------------------------------------------
# A real scripted unit, end to end (slow tier; toy truth, no model)
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_a_real_scripted_session_lands_where_the_scripts_reader_finds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing stubbed: the tool layer, the scorer and the store, on AG-c. An AG-o
    session needs the sandbox image, so a real run shows that arm."""
    clean(monkeypatch)
    arm = LLMArm("AG-c", "named", P4_PROMPT_VARIANT)
    config = dataclasses.replace(
        tiny_config(seeds=(1,), baselines=("ORACLE",), llm_arms=(arm,)),
        fits=maker.PLANTED_FITS,
        experiments=1,
    )
    truth = entry("hawkes")
    env = environment()
    root = tmp_path / "store"
    campaign = Campaign(
        root,
        env,
        config,
        [truth],
        DriverSettings(
            workers=0,
            poll_s=0.01,
            max_attempts=1,
            driver_factory=maker.scripted_factory(config, (truth,)),
        ),
        fingerprint=FINGERPRINT,
    )
    campaign.run()
    campaign.close()
    table, cells = p4m.load_store(root, env, (config,), (truth,))
    p4m.check_table(table, cells)
    system = arm.system(config.llm_letter)
    (row,) = [r for r in table.rows if r.system == system]
    assert row.submitted and row.per_event is not None
    (found,) = table.sessions
    assert not found.void.void
    with ResultStore(root, read_only=True) as store:
        assert load_rows(store, env, config, [truth], sessions="stored").missing == {}
