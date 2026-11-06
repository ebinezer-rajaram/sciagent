"""P4's real campaign plan (P4-PROPOSAL §4 under §5; prereg 2026-11-05 §7).

Stand-in truths only: no test split exists, and no store is opened. Red team
finding 15 (``docs/v2/redteam/2026-11-06_contrast-code.md``) is why this is here:
the 1,000 tier-1 rows had held only on a stand-in plan.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import pytest
from campaign_support import FINGERPRINT, entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign import execute
from sciagent.campaign.analysis import load_rows
from sciagent.campaign.contrasts import (
    AG_C_ANON,
    AG_C_NAMED,
    AG_C_RESCALED,
    AG_O_ANON,
    AG_O_NAMED,
    AG_O_RESCALED,
    CONTRAST_SYSTEMS,
    READS,
    contrast_rows,
)
from sciagent.campaign.driver import Campaign, DriverSettings
from sciagent.campaign.execute import BaselineJob, run_baseline_job
from sciagent.campaign.freeze import check_arms, surface_pin_key
from sciagent.campaign.p4plan import (
    P4_SEEDS,
    P4_SWEEP_BUDGETS,
    P4_SWEEP_TRUTHS,
    P4_TRUTHS,
    p4_plan,
    sweep_truths,
)
from sciagent.campaign.plan import (
    B_LIB,
    B_NP,
    B_RAND,
    B_SPARSE,
    B_SYM,
    B_SYM_10F,
    B_SYM_F80,
    BASELINES,
    KNOWN_BASELINES,
    ORACLE,
    P4_ARMS,
    P4_SONNET_ARMS,
    PLANTED,
    CampaignConfig,
    CampaignError,
    LLMArm,
    TruthEntry,
    Unit,
    baseline_key,
    baseline_units,
    legacy_session_config_digest,
    llm_units,
    search_seed,
    session_config_digest,
    session_key,
    surface_digest,
    system_config_text,
    system_variant,
)
from sciagent.campaign.store import ResultStore
from sciagent.harness.live import MODELS
from sciagent.harness.scripted import ScriptedDriver

#: Sonnet's, Haiku's and Opus's named arms on the frozen variant, by tier.
SUFFIX: Final = "+link-neutral"
#: The sweep's five budgets, as the system names state them.
SWEEP_TAGS: Final = (
    "+E=0,F=40",
    "+E=2,F=40",
    "+E=16,F=40",
    "+E=8,F=20",
    "+E=8,F=80",
)


def standin(n: int = P4_TRUTHS) -> list[TruthEntry]:
    """A split as the sampler lays it out: in, ood, in, ood, numbered."""
    return [
        dataclasses.replace(
            entry("hawkes"),
            id=f"test-{k:03d}",
            in_dictionary=k % 2 == 0,
            split="test",
        )
        for k in range(n)
    ]


def units(
    plan: Sequence[CampaignConfig], truths: Sequence[TruthEntry]
) -> list[tuple[Unit, CampaignConfig]]:
    """Every unit of the plan, with the config that runs it."""
    ids = [t.id for t in truths]
    return [
        (unit, config)
        for config in plan
        for unit in (*baseline_units(ids, config), *llm_units(ids, config))
    ]


def counts() -> Counter[str]:
    truths = standin()
    return Counter(unit.system for unit, _ in units(p4_plan(truths), truths))


def shuffled(truths: Sequence[TruthEntry], seed: int = 20261106) -> list[TruthEntry]:
    order = np.random.default_rng(seed).permutation(len(truths))
    return [truths[int(k)] for k in order]


def sessions(counted: Counter[str]) -> int:
    return sum(n for s, n in counted.items() if s.startswith("AG-"))


# --------------------------------------------------------------------------
# The unit counts per block
# --------------------------------------------------------------------------


def test_the_plan_is_1320_sessions_620_baselines_1940_rows() -> None:
    counted = counts()
    assert sessions(counted) == 1320
    assert sum(counted.values()) - sessions(counted) == 620
    assert sum(counted.values()) == 1940


@pytest.mark.parametrize("letter", ["H", "S", "O"])
@pytest.mark.parametrize("tier", ["AG-c", "AG-o"])
@pytest.mark.parametrize("condition", ["named", "anon"])
def test_the_main_grid_is_80_worlds_per_arm(
    letter: str, tier: str, condition: str
) -> None:
    assert counts()[f"{tier}-{letter}/{condition}{SUFFIX}"] == 80


@pytest.mark.parametrize("system", [AG_C_RESCALED, AG_O_RESCALED])
def test_the_rescaled_arms_are_80_worlds(system: str) -> None:
    assert counts()[system] == 80


@pytest.mark.parametrize("letter", ["H", "O"])
def test_rescaled_is_sonnet_only(letter: str) -> None:
    assert not [s for s in counts() if s.startswith(f"AG-c-{letter}/rescaled")]
    assert not [s for s in counts() if s.startswith(f"AG-o-{letter}/rescaled")]


@pytest.mark.parametrize("tag", SWEEP_TAGS)
@pytest.mark.parametrize("tier", ["AG-c", "AG-o"])
def test_the_sweep_is_20_sessions_per_arm_and_budget(tag: str, tier: str) -> None:
    assert counts()[f"{tier}-S/named{SUFFIX}{tag}"] == 20


def test_the_sweep_is_sonnet_and_named_only() -> None:
    swept = [s for s in counts() if "+E=" in s]
    assert len(swept) == 10
    assert all(s.startswith(("AG-c-S/named", "AG-o-S/named")) for s in swept)


@pytest.mark.parametrize(
    "system", [ORACLE, B_LIB, B_NP, B_RAND, B_SYM, B_SPARSE, PLANTED]
)
def test_each_baseline_runs_on_the_80_worlds(system: str) -> None:
    assert counts()[system] == 80


def test_the_two_budget_baselines() -> None:
    assert counts()[B_SYM_F80] == 20
    assert counts()[B_SYM_10F] == 40


def test_the_systems_are_exactly_the_blocks() -> None:
    expected = {
        *(
            f"{tier}-{letter}/{condition}{SUFFIX}"
            for tier, letter, condition in itertools.product(
                ("AG-c", "AG-o"), "HSO", ("named", "anon")
            )
        ),
        AG_C_RESCALED,
        AG_O_RESCALED,
        *(
            f"{tier}-S/named{SUFFIX}{tag}"
            for tier, tag in itertools.product(("AG-c", "AG-o"), SWEEP_TAGS)
        ),
        *BASELINES,
        B_SYM_F80,
    }
    assert set(counts()) == expected


def test_no_policy_arm_is_planned() -> None:
    """Decision 12: AG-S-boed and the §4.2 rows are P4b's."""
    truths = standin()
    plan = p4_plan(truths)
    assert not [s for s in counts() if "policy" in s or "+EIG" in s or "+FSD" in s]
    assert all(a.policy is None for c in plan for a in c.llm_arms)


def test_each_main_system_holds_every_truth_on_both_seeds() -> None:
    truths = standin()
    worlds = {(t.id, s) for t in truths for s in (1, 2)}
    by_system: dict[str, set[tuple[str, int]]] = {}
    for unit, _ in units(p4_plan(truths), truths):
        by_system.setdefault(unit.system, set()).add((unit.truth_id, unit.seed))
    for system, held in by_system.items():
        if counts()[system] == 80:
            assert held == worlds, system


def test_control_3_is_every_truth_on_seed_1() -> None:
    truths = standin()
    held = {
        (u.truth_id, u.seed)
        for u, _ in units(p4_plan(truths), truths)
        if u.system == B_SYM_10F
    }
    assert held == {(t.id, 1) for t in truths}


def test_the_budgets_and_models_of_each_config() -> None:
    plan = p4_plan(standin())
    sonnet, haiku, opus, *sweep = plan
    assert [(c.llm_letter, c.llm_model) for c in (sonnet, haiku, opus)] == [
        ("S", MODELS["sonnet"]),
        ("H", MODELS["haiku"]),
        ("O", MODELS["opus"]),
    ]
    assert all((c.experiments, c.fits) == (8, 40) for c in (sonnet, haiku, opus))
    assert [(c.experiments, c.fits) for c in sweep] == [
        (0, 40),
        (2, 40),
        (16, 40),
        (8, 20),
        (8, 80),
    ]
    assert tuple((c.experiments, c.fits) for c in sweep) == P4_SWEEP_BUDGETS
    assert all(c.llm_letter == "S" for c in sweep)
    assert all(c.llm_model == MODELS["sonnet"] for c in sweep)
    assert sonnet.llm_arms == P4_SONNET_ARMS
    assert haiku.llm_arms == opus.llm_arms == P4_ARMS
    assert sonnet.baselines == BASELINES
    assert all(c.seeds == P4_SEEDS == (1, 2) for c in (sonnet, haiku, opus))
    assert all(c.seeds == (1,) for c in sweep)


def test_every_arm_is_on_the_frozen_variant() -> None:
    truths = standin()
    for config in p4_plan(truths):
        check_arms(config, truths)


# --------------------------------------------------------------------------
# contrast_rows over the plan
# --------------------------------------------------------------------------


def amendment_rows(truths: Sequence[TruthEntry]) -> set[tuple[str, int, str]]:
    """Prereg §7's table, written out from the text and not from the plan."""
    arms = (AG_C_NAMED, AG_C_RESCALED, AG_C_ANON, AG_O_NAMED, AG_O_RESCALED, AG_O_ANON)
    baselines = (B_SYM, B_SPARSE, ORACLE, B_LIB, PLANTED, B_RAND)
    main = {
        (t.id, seed, system)
        for t in truths
        for seed in (1, 2)
        for system in (*arms, *baselines)
    }
    return main | {(t.id, 1, B_SYM_10F) for t in truths}


def test_contrast_rows_are_exactly_what_section_7_names() -> None:
    truths = standin()
    keys = contrast_rows(p4_plan(truths), truths)
    assert set(keys) == amendment_rows(truths)
    assert len(keys) == len(set(keys)) == 1000
    assert keys == tuple(sorted(keys))


def test_every_contrast_row_is_a_row_of_the_sonnet_config() -> None:
    """So ``load_rows`` on that one config holds the whole of tier 1."""
    truths = standin()
    plan = p4_plan(truths)
    assert contrast_rows(plan[:1], truths) == contrast_rows(plan, truths)


def test_every_side_of_every_read_has_rows_in_the_plan() -> None:
    truths = standin()
    held = {key[2] for key in contrast_rows(p4_plan(truths), truths)}
    assert {s for spec in READS for s in (spec.first, spec.second)} <= held


def test_no_row_key_is_shared_across_the_plan() -> None:
    truths = standin()
    keys = [(u.truth_id, u.seed, u.system) for u, _ in units(p4_plan(truths), truths)]
    assert len(keys) == len(set(keys)) == 1940


def test_each_system_has_one_model_one_budget_and_one_config() -> None:
    truths = standin()
    held: dict[str, set[tuple[str, int, int]]] = {}
    for unit, c in units(p4_plan(truths), truths):
        held.setdefault(unit.system, set()).add((c.llm_model, c.experiments, c.fits))
    assert all(len(v) == 1 for v in held.values())


def test_sweep_and_other_budget_rows_stay_out_of_the_contrasts() -> None:
    truths = standin()
    plan = p4_plan(truths)
    keys = contrast_rows(plan, truths)
    assert all("+E=" not in system for _, _, system in keys)
    assert B_SYM_F80 not in CONTRAST_SYSTEMS
    assert not [k for k in keys if k[2] == B_SYM_F80]
    assert not [s for s in CONTRAST_SYSTEMS if "+E=" in s]


def test_every_contrast_row_runs_at_the_main_budget() -> None:
    truths = standin()
    plan = p4_plan(truths)
    wanted = set(contrast_rows(plan, truths))
    for unit, config in units(plan, truths):
        if (unit.truth_id, unit.seed, unit.system) in wanted:
            assert (config.experiments, config.fits) == (8, 40), unit


def test_haiku_and_opus_rows_stay_out_of_the_contrasts() -> None:
    truths = standin()
    keys = contrast_rows(p4_plan(truths), truths)
    assert not [k for k in keys if "-H/" in k[2] or "-O/" in k[2]]
    assert not [k for k in keys if k[2] == B_NP]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_result_does_not_depend_on_truth_order(seed: int) -> None:
    truths = standin()
    other = shuffled(truths, seed)
    assert [t.id for t in other] != [t.id for t in truths]
    plan = p4_plan(truths)
    assert p4_plan(other) == plan
    assert contrast_rows(p4_plan(other), other) == contrast_rows(plan, truths)
    assert contrast_rows(plan, other) == contrast_rows(plan, truths)


def test_the_result_does_not_depend_on_config_order() -> None:
    truths = standin()
    plan = p4_plan(truths)
    assert contrast_rows(plan[::-1], truths) == contrast_rows(plan, truths)


def test_a_plan_that_reads_truth_order_has_no_contrast_rows() -> None:
    """``control_truths`` takes a prefix of the given order, so with it set the
    rows would follow the order (red team finding 15): refused, not sorted, since
    the campaign itself runs that prefix."""
    truths = standin(4)
    config = CampaignConfig(seeds=(1,), llm_arms=(), control_truths=2)
    with pytest.raises(CampaignError, match="control_truths"):
        contrast_rows((config,), truths)


def test_the_plan_sets_no_control_truths() -> None:
    assert all(c.control_truths is None for c in p4_plan(standin()))


# --------------------------------------------------------------------------
# Which 20 truths
# --------------------------------------------------------------------------


def test_the_sweep_takes_the_first_20_ids() -> None:
    truths = standin()
    assert P4_SWEEP_TRUTHS == 20
    assert sweep_truths(truths) == tuple(f"test-{k:03d}" for k in range(20))


def test_the_sweep_keeps_the_split_share() -> None:
    truths = standin()
    chosen = set(sweep_truths(truths))
    assert sum(t.in_dictionary for t in truths if t.id in chosen) == 10


def test_the_sweep_and_b_sym_at_f80_share_their_truths_and_seed() -> None:
    truths = standin()
    expected = {(tid, 1) for tid in sweep_truths(truths)}
    held: dict[str, set[tuple[str, int]]] = {}
    for unit, _ in units(p4_plan(truths), truths):
        if "+E=" in unit.system or unit.system == B_SYM_F80:
            held.setdefault(unit.system, set()).add((unit.truth_id, unit.seed))
    assert len(held) == 11
    assert all(v == expected for v in held.values())


class _IdAndKind:
    """A truth that fails on any read but its id and its dictionary kind."""

    def __init__(self, truth_id: str, in_dictionary: bool) -> None:
        self.id = truth_id
        self.in_dictionary = in_dictionary

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the plan read {name!r} of a truth")


def test_the_rule_reads_only_the_id_and_the_kind() -> None:
    truths = cast(
        "list[TruthEntry]",
        [_IdAndKind(f"test-{k:03d}", k % 2 == 0) for k in range(P4_TRUTHS)],
    )
    plan = p4_plan(truths)
    assert sweep_truths(truths) == tuple(f"test-{k:03d}" for k in range(20))
    assert len(contrast_rows(plan, truths)) == 1000


def test_the_rule_ignores_everything_a_truth_is_scored_by() -> None:
    truths = standin()
    other = [
        dataclasses.replace(
            t,
            digest=f"{k:064x}",
            stratum="far",
            nearest_distance=float(k),
            dsl=f"dsl-{k}",
        )
        for k, t in enumerate(truths)
    ]
    assert p4_plan(other) == p4_plan(truths)


def test_the_plan_is_refused_on_any_other_number_of_truths() -> None:
    with pytest.raises(CampaignError, match="40"):
        p4_plan(standin(39))
    with pytest.raises(CampaignError, match="40"):
        p4_plan(standin(41))


def test_the_plan_is_refused_on_a_repeated_truth() -> None:
    truths = standin()
    with pytest.raises(CampaignError, match="distinct"):
        p4_plan([*truths[:-1], truths[0]])


def test_a_prefix_that_lost_the_share_is_refused() -> None:
    """The rule rests on the sampler's interleave: without it, say."""
    truths = [
        dataclasses.replace(t, in_dictionary=k < 20) for k, t in enumerate(standin())
    ]
    with pytest.raises(CampaignError, match="share"):
        p4_plan(truths)


# --------------------------------------------------------------------------
# A config's truth selection
# --------------------------------------------------------------------------

IDS: Final = ["t0", "t1", "t2", "t3"]


def test_only_truths_narrows_the_units_and_keeps_the_given_order() -> None:
    config = CampaignConfig(
        seeds=(1,),
        baselines=(B_SYM,),
        llm_arms=P4_ARMS[:1],
        only_truths=("t3", "t1"),
    )
    assert [u.truth_id for u in baseline_units(IDS, config)] == ["t1", "t3"]
    assert [u.truth_id for u in llm_units(IDS, config)] == ["t1", "t3"]


def test_no_selection_is_every_truth() -> None:
    config = CampaignConfig(seeds=(1,), baselines=(B_SYM,), llm_arms=P4_ARMS[:1])
    assert config.only_truths is None
    assert [u.truth_id for u in baseline_units(IDS, config)] == IDS
    assert [u.truth_id for u in llm_units(IDS, config)] == IDS


def test_a_selected_truth_the_campaign_lacks_is_refused() -> None:
    """Silently fewer units would undercount tier 1 and the planned rows."""
    config = CampaignConfig(seeds=(1,), only_truths=("t1", "gone"))
    with pytest.raises(CampaignError, match="gone"):
        baseline_units(IDS, config)
    with pytest.raises(CampaignError, match="gone"):
        llm_units(IDS, config)


@pytest.mark.parametrize("chosen", [(), ("t1", "t1")])
def test_an_empty_or_repeated_selection_is_refused(chosen: tuple[str, ...]) -> None:
    with pytest.raises(CampaignError, match="only_truths"):
        CampaignConfig(only_truths=chosen)


def test_control_truths_count_within_the_selection() -> None:
    config = CampaignConfig(
        seeds=(1,),
        baselines=(B_SYM_10F,),
        llm_arms=(),
        only_truths=("t2", "t3"),
        control_truths=1,
    )
    assert [u.truth_id for u in baseline_units(IDS, config)] == ["t2"]


def test_the_selection_is_no_part_of_an_address() -> None:
    env = environment()
    truth = entry("hawkes")
    open_config = tiny_config(llm_arms=P4_ARMS[:1])
    narrow = dataclasses.replace(open_config, only_truths=(truth.id,))
    arm = open_config.llm_arms[0]
    base = Unit(truth.id, 1, B_SYM)
    llm = Unit(truth.id, 1, arm.system(open_config.llm_letter))
    assert baseline_key(base, truth, env, narrow) == baseline_key(
        base, truth, env, open_config
    )
    assert session_key(llm, truth, env, narrow) == session_key(
        llm, truth, env, open_config
    )


# --------------------------------------------------------------------------
# A budget in an arm's name
# --------------------------------------------------------------------------

NAMED: Final = LLMArm("AG-c", "named", "link-neutral")
SWEPT: Final = dataclasses.replace(NAMED, budget=(0, 40))


def test_a_swept_arm_states_its_budget_last() -> None:
    assert SWEPT.system("S") == "AG-c-S/named+link-neutral+E=0,F=40"


def test_an_arm_without_a_budget_keeps_its_name() -> None:
    assert NAMED.budget is None
    assert NAMED.system("S") == "AG-c-S/named+link-neutral"
    assert LLMArm("AG-c", "named").system("S") == "AG-c-S/named"
    assert LLMArm("AG-c", "named", policy="EIG").system("S") == (
        "AG-c-S/named+policy=EIG"
    )


def test_the_variant_is_still_read_from_a_swept_name() -> None:
    """The stored-session guard reads it (``freeze.check_stored``)."""
    assert system_variant(SWEPT.system("S")) == "link-neutral"


@pytest.mark.parametrize(("experiments", "fits"), [(2, 40), (0, 80)])
def test_a_name_that_misstates_the_config_is_refused(
    experiments: int, fits: int
) -> None:
    with pytest.raises(CampaignError, match="budget"):
        CampaignConfig(llm_arms=(SWEPT,), experiments=experiments, fits=fits)


def test_the_config_finds_a_swept_arm_by_its_name() -> None:
    config = CampaignConfig(llm_arms=(SWEPT,), experiments=0, fits=40)
    assert config.arm_of(SWEPT.system("S")) == SWEPT


def test_a_swept_session_differs_from_a_main_session_at_equal_budget_in_name_only() -> (
    None
):
    """Its text is the budget's; only the key's system tells them apart, so a
    tag never moves a text, and so never an address of an arm without one."""
    env = environment()
    truth = entry("hawkes")
    swept = tiny_config(llm_arms=(SWEPT,), experiments=0, fits=40)
    plain = tiny_config(llm_arms=(NAMED,), experiments=0, fits=40)
    assert session_config_digest(SWEPT, swept, env) == session_config_digest(
        NAMED, plain, env
    )
    tagged = Unit(truth.id, 1, SWEPT.system(swept.llm_letter))
    untagged = Unit(truth.id, 1, NAMED.system(plain.llm_letter))
    assert session_key(tagged, truth, env, swept) != session_key(
        untagged, truth, env, plain
    )


def test_the_pilot_could_not_have_run_a_swept_arm() -> None:
    arm = dataclasses.replace(LLMArm("AG-c", "named"), budget=(8, 40))
    assert legacy_session_config_digest(arm, CampaignConfig(llm_arms=(arm,))) is None


# --------------------------------------------------------------------------
# B-sym@F=80
# --------------------------------------------------------------------------


def f80_config(**overrides: Any) -> CampaignConfig:
    return tiny_config(baselines=(B_SYM_F80,), llm_arms=(), fits=80, **overrides)


def test_b_sym_at_f80_is_known_and_no_default_baseline() -> None:
    assert B_SYM_F80 == "B-sym@F=80"
    assert B_SYM_F80 in KNOWN_BASELINES
    assert B_SYM_F80 not in BASELINES


def test_every_baseline_a_config_accepts_is_one_a_job_runs() -> None:
    assert set(KNOWN_BASELINES) == execute._KNOWN


@pytest.mark.parametrize("fits", [40, 79, 81])
def test_b_sym_at_f80_is_refused_at_any_other_budget(fits: int) -> None:
    with pytest.raises(CampaignError, match=f"80 fits, not {fits}"):
        CampaignConfig(baselines=(B_SYM_F80,), llm_arms=(), fits=fits)


def test_its_text_is_b_syms_at_80_fits() -> None:
    env = environment()
    config = f80_config()
    text = system_config_text(B_SYM_F80, env, config)
    assert text.startswith("budget=80; ")
    assert text == system_config_text(B_SYM, env, config)


def test_its_address_is_its_own() -> None:
    env = environment()
    truth = entry("hawkes")
    config = f80_config()
    key = baseline_key(Unit(truth.id, 1, B_SYM_F80), truth, env, config)
    plain = tiny_config(baselines=(B_SYM,), llm_arms=(), fits=40)
    assert key != baseline_key(Unit(truth.id, 1, B_SYM), truth, env, plain)
    assert key.config["system"] == B_SYM_F80


def test_a_unit_runs_b_sym_with_the_configs_budget_and_its_search_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: dict[str, Any] = {}

    class Recorder:
        def __init__(self, seeds: object, gp: object, **kwargs: Any) -> None:
            built.update(kwargs)

        def run(self, data: object) -> str:
            return "ran"

    monkeypatch.setattr(execute, "BSym", Recorder)
    config = f80_config()
    job = BaselineJob(entry("hawkes"), 1, (B_SYM_F80,), environment(), config)
    data: Any = object()
    execute._run_system(B_SYM_F80, job, data)
    assert built["budget"] == 80
    assert built["name"] == B_SYM_F80
    assert built["seed"] == search_seed(job.truth.id, 1)


@pytest.mark.slow
def test_a_real_b_sym_at_f80_unit_runs_and_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At 3 fits: 80 took 267 s, and the budget has its own tests above."""
    monkeypatch.setattr("sciagent.campaign.plan.B_SYM_F80_FITS", 3)
    config = tiny_config(baselines=(B_SYM_F80,), llm_arms=())
    job = BaselineJob(entry("hawkes"), 1, (B_SYM_F80,), environment(), config)
    (result,) = run_baseline_job(job)
    assert result.ok, result.detail
    assert result.system == B_SYM_F80
    assert 1 <= result.reading["fits_used"] <= 3
    assert math.isfinite(result.reading["per_event"])


@pytest.mark.slow
def test_two_configs_share_a_store_and_each_reads_its_own_planned_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A narrowed config with a swept arm at E = 0, beside a main config: the
    real driver, a scripted agent, and ``load_rows`` on each config."""
    clean(monkeypatch)
    env = environment()
    truths = [entry("hawkes"), entry("size_excitation")]
    main = tiny_config(seeds=(1,), baselines=(B_LIB,), llm_arms=())
    arm = dataclasses.replace(LLMArm("AG-c", "named"), budget=(0, 3))
    swept = tiny_config(
        seeds=(1,),
        baselines=(),
        llm_arms=(arm,),
        experiments=0,
        only_truths=("size_excitation",),
    )
    for config in (main, swept):
        campaign = Campaign(
            tmp_path,
            env,
            config,
            truths,
            DriverSettings(
                workers=0,
                poll_s=0.05,
                driver_factory=lambda unit: ScriptedDriver(SCRIPT),
            ),
            fingerprint=FINGERPRINT,
        )
        summary = campaign.run()
        campaign.close()
        assert summary.bugs == 0 and not summary.failed
    system = arm.system(swept.llm_letter)
    assert system == "AG-c-H/named+E=0,F=3"
    with ResultStore(tmp_path, read_only=True) as store:
        narrowed = load_rows(store, env, swept, truths)
        whole = load_rows(store, env, main, truths)
    assert narrowed.planned == {system: 1}
    assert not narrowed.missing
    assert [(r.truth, r.seed, r.system) for r in narrowed.rows] == [
        ("size_excitation", 1, system)
    ]
    assert whole.planned == {B_LIB: 2}
    assert not whole.missing


# --------------------------------------------------------------------------
# What a launch would read
# --------------------------------------------------------------------------


def test_every_arm_of_the_plan_renders_a_surface_of_its_own() -> None:
    """E = 0 included. The model letter is no part of a surface, so Haiku and
    Opus share Sonnet's pins: 6 at the main budget and 10 in the sweep."""
    env = environment()
    digests: dict[str, str] = {}
    for config in p4_plan(standin()):
        for arm in config.llm_arms:
            digests[surface_pin_key(arm, config)] = surface_digest(arm, config, env)
    assert len(digests) == 16
    assert len(set(digests.values())) == 16


def test_every_session_config_of_the_plan_differs() -> None:
    env = environment()
    digests = [
        session_config_digest(arm, config, env)
        for config in p4_plan(standin())
        for arm in config.llm_arms
    ]
    assert len(digests) == 24
    assert len(set(digests)) == 24


def test_every_baseline_address_of_the_plan_differs() -> None:
    env = environment()
    truths = standin()
    by_id = {t.id: t for t in truths}
    keys = [
        baseline_key(unit, by_id[unit.truth_id], env, config)
        for config in p4_plan(truths)
        for unit in baseline_units([t.id for t in truths], config)
    ]
    assert len(keys) == 620
    assert len({key.digest for key in keys}) == 620
