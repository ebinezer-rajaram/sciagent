"""Every policy stream is keyed by truth (C5; LOG 2026-10-27, adopted 2026-10-28).

``policy_seed(seed, decision, k, r)`` carries no truth, and the menu size
depends only on the channels, so on a seed every truth's random arm drew the
same cells. The framework now derives one opaque integer per (truth, seed),
:func:`~sciagent.scoring.seeds.truth_policy_seed`, and passes it where the
investigation seed went: the B-sym policy arms (``execute.py``) and AG-c policy
sessions (``runner.build_lab``). Within a (truth, seed) all arms and both
naming conditions still share streams. The address text says so.

The non-policy units never read the key, and their addresses are pinned in
``test_campaign_policy.py`` and ``test_pilot_harness1_cells.py``.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from campaign_support import entry, environment, tiny_config

from environments.pointproc import v2 as pointproc_v2
from sciagent.campaign import execute, plan
from sciagent.campaign.execute import BaselineJob
from sciagent.campaign.plan import (
    B_SYM_EIG,
    B_SYM_FSD,
    B_SYM_RANDOM,
    B_SYM_TRUTH_FSD,
    POLICY_ARMS,
    LLMArm,
    Unit,
    baseline_key,
    search_seed,
    session_text,
    system_config_text,
)
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.interventions import Experiment
from sciagent.investigation.policy import policy_config, random_cell
from sciagent.investigation.prompts import PolicyName
from sciagent.investigation.runner import InvestigationSpec, build_lab
from sciagent.investigation.world import OBSERVATIONAL, World
from sciagent.scoring.seeds import POLICY_SEED_RULE, truth_policy_seed
from sciagent.systems.v2.policies import (
    Choice,
    DecisionContext,
    EIGPolicy,
    EntertainedSet,
    Hypothesis,
    Menu,
    design_menu,
    diagnostic_menu,
)
from sciagent.systems.v2.policy_search import random_choice

POLICY_NAMES: tuple[PolicyName, ...] = ("random", "EIG", "FSD")


class Reached(Exception):
    """The search was built: the seeds under test are known."""


def _seeds_of_a_policy_unit(
    system: str, truth_id: str, seed: int, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    """The seeds ``_policy_unit`` builds its policy and its search with."""
    seen: dict[str, int] = {}

    class Picked:
        model: Any = None
        submitted = "stub"

    class FakeBLib:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, data: Any) -> Picked:
            return Picked()

    def spy_policy(system: str, seed: int, *args: Any, **kwargs: Any) -> str:
        seen["policy"] = seed
        return "policy"

    def spy_search(*args: Any, **kwargs: Any) -> Any:
        seen["policy_seed"] = kwargs["policy_seed"]
        seen["search"] = kwargs["seed"]
        raise Reached

    monkeypatch.setattr(plan, "BLib", FakeBLib)
    monkeypatch.setattr(execute, "build_policy", spy_policy)
    monkeypatch.setattr(plan, "PolicySearch", spy_search)
    job = BaselineJob(entry(truth_id), seed, (system,), environment(), tiny_config())
    held: Any = None  # read only after the search has run
    with pytest.raises(Reached):
        execute._policy_unit(system, job, held, 0.0, None)
    return seen


@pytest.mark.parametrize("system", POLICY_ARMS)
def test_a_policy_arm_is_seeded_with_the_truth_keyed_seed(
    system: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _seeds_of_a_policy_unit(system, "hawkes", 2, monkeypatch)
    key = truth_policy_seed("hawkes", 2)
    assert seen["policy"] == key
    assert seen["policy_seed"] == key


def test_the_search_seed_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _seeds_of_a_policy_unit(B_SYM_RANDOM, "hawkes", 2, monkeypatch)
    assert seen["search"] == search_seed("hawkes", 2)


def test_two_truths_on_a_seed_no_longer_share_a_policy_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _seeds_of_a_policy_unit(B_SYM_RANDOM, "hawkes", 1, monkeypatch)
    b = _seeds_of_a_policy_unit(B_SYM_RANDOM, "size_excitation", 1, monkeypatch)
    assert a["policy_seed"] != b["policy_seed"]


def test_all_arms_of_a_truth_and_seed_share_the_policy_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = {
        _seeds_of_a_policy_unit(s, "hawkes", 1, monkeypatch)["policy_seed"]
        for s in POLICY_ARMS
    }
    assert seen == {truth_policy_seed("hawkes", 1)}


def _lab(truth: str, condition: Any = "named", policy: PolicyName = "random") -> Any:
    spec = InvestigationSpec("pointproc-v2", truth, "AG-c", condition, 1, policy=policy)
    env = environment()
    assert env.investigation is not None
    return build_lab(spec, dataclasses.replace(env.investigation, marks=env.marks))


@pytest.mark.parametrize("policy", POLICY_NAMES)
def test_an_agent_policy_session_is_seeded_with_the_truth_keyed_seed(
    policy: PolicyName,
) -> None:
    mode = _lab("hawkes", policy=policy).policy_mode
    assert mode is not None and mode.seed == truth_policy_seed("hawkes", 1)


def test_both_naming_conditions_share_the_seed_and_truths_do_not() -> None:
    named = _lab("hawkes", "named").policy_mode
    anon = _lab("hawkes", "anon").policy_mode
    other = _lab("size_excitation").policy_mode
    assert named is not None and anon is not None and other is not None
    assert named.seed == anon.seed
    assert named.seed != other.seed


def test_the_spec_keeps_the_investigation_seed() -> None:
    # The world draws from the investigation seed; only the policy is keyed.
    theirs = _lab("hawkes").world.datasets[OBSERVATIONAL].log.times
    ours = World(entry("hawkes").spec, 1).datasets[OBSERVATIONAL].log.times
    assert np.array_equal(theirs, ours)


@pytest.mark.parametrize("system", POLICY_ARMS)
def test_a_policy_arms_text_names_the_seed_rule(system: str) -> None:
    text = system_config_text(system, environment(), tiny_config())
    assert f"policy_seed={POLICY_SEED_RULE}" in text
    assert "investigation seed" not in text


@pytest.mark.parametrize("policy", POLICY_NAMES)
def test_policy_config_names_the_seed_rule(policy: PolicyName) -> None:
    assert json.loads(policy_config(policy))["policy_seed"] == POLICY_SEED_RULE


def test_the_rule_enters_a_policy_sessions_key_and_only_a_policy_sessions() -> None:
    env, config = environment(), tiny_config()
    plain = session_text(LLMArm("AG-c", "named"), config, env)
    assert "policy=none" in plain
    assert POLICY_SEED_RULE not in plain


def test_the_seed_rule_moves_the_policy_arms_and_no_other_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env, config, truth = environment(), tiny_config(), entry("hawkes")

    def digest(system: str) -> str:
        return str(baseline_key(Unit("hawkes", 1, system), truth, env, config).digest)

    systems = (B_SYM_RANDOM, B_SYM_EIG, B_SYM_FSD, B_SYM_TRUTH_FSD)
    before = {s: digest(s) for s in (*systems, "B-sym", "ORACLE", "telling")}
    monkeypatch.setattr(plan, "POLICY_SEED_RULE", "another rule")
    after = {s: digest(s) for s in before}
    for system in systems:
        assert before[system] != after[system], system
    for system in ("B-sym", "ORACLE", "telling"):
        assert before[system] == after[system], system


# --------------------------------------------------------------------------
# Δ11 item 3: a fully tied EIG decision is the random arm's pick, on both paths
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Poisson:
    """A homogeneous Poisson fake that ignores the design (cheap to simulate)."""

    rate: float
    name: str = "poisson"

    def simulate_experiment(
        self, experiment: Experiment, rng: np.random.Generator, *, label: str = "x"
    ) -> Dataset:
        n = int(rng.poisson(self.rate * experiment.horizon))
        times = np.sort(rng.uniform(0.0, experiment.horizon, n))
        rows = [pointproc_v2.mark_sampler(rng) for _ in range(n)]
        marks = {
            c.name: np.array([r[c.name] for r in rows], dtype=np.float64)
            for c in pointproc_v2.CHANNELS
        }
        return Dataset.observational(
            EventLog.create(times, marks, experiment.horizon), label
        )


def _degenerate_context() -> DecisionContext:
    """Two hypotheses 600 BIC apart: every EIG is below the floor."""
    channels = pointproc_v2.CHANNELS
    exp = design_menu(channels, horizon=200.0)[0].experiment
    log = _Poisson(1.0).simulate_experiment(exp, np.random.default_rng(0)).log
    entertained = EntertainedSet.of(
        (
            Hypothesis("a", _Poisson(1.0, "a"), 0.0),
            Hypothesis("b", _Poisson(4.0, "b"), 600.0),
        )
    )
    menu = Menu(design_menu(channels, horizon=60.0)[:2], diagnostic_menu(log, channels))
    return DecisionContext(channels, Dataset.observational(log), entertained, menu)


def _cell(choice: Choice) -> tuple[int, int]:
    return choice.design_index, choice.diagnostic_index


@pytest.mark.parametrize("truth_id", ["hawkes", "size_excitation"])
def test_b_sym_eigs_full_tie_is_b_sym_randoms_pick(
    truth_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeds ``execute`` gives ``B-sym+EIG`` and ``B-sym+random`` for one
    (truth, seed) are the same truth-keyed key, so on a fully tied decision the
    EIG arm's cell is the random arm's, and the search's random fallback's."""
    eig_seeds = _seeds_of_a_policy_unit(B_SYM_EIG, truth_id, 2, monkeypatch)
    random_seeds = _seeds_of_a_policy_unit(B_SYM_RANDOM, truth_id, 2, monkeypatch)
    assert eig_seeds["policy"] == random_seeds["policy"] == eig_seeds["policy_seed"]
    settings = dataclasses.replace(tiny_config().policy, replicates=4)
    eig_arm = plan.build_policy(B_SYM_EIG, eig_seeds["policy"], settings)
    random_arm = plan.build_policy(B_SYM_RANDOM, random_seeds["policy"], settings)
    assert isinstance(eig_arm, EIGPolicy)
    ctx = _degenerate_context()
    for decision in range(4):
        mine = eig_arm.choose(ctx, decision)
        assert mine.eig is not None and mine.eig.full_tie and mine.eig.degenerate
        assert _cell(mine) == _cell(random_arm.choose(ctx, decision))
        fallback = random_choice(eig_seeds["policy_seed"], decision, ctx.menu)
        assert _cell(mine) == _cell(fallback)


@pytest.mark.parametrize("truth_id", ["hawkes", "size_excitation"])
def test_ag_eigs_full_tie_is_the_ag_random_arms_pick(truth_id: str) -> None:
    """An AG-c EIG session's policy draws from the lab's truth-keyed seed,
    which is the AG-c random session's, so on a fully tied decision it takes
    the cell the random session's policy mode draws (``random_cell``)."""
    eig_mode = _lab(truth_id, policy="EIG").policy_mode
    random_mode = _lab(truth_id, policy="random").policy_mode
    assert eig_mode is not None and random_mode is not None
    assert eig_mode.seed == random_mode.seed == truth_policy_seed(truth_id, 1)
    assert isinstance(eig_mode.policy, EIGPolicy)
    assert eig_mode.policy.seed == eig_mode.seed
    policy = dataclasses.replace(eig_mode.policy, replicates=4)
    ctx = _degenerate_context()
    for decision in range(4):
        mine = policy.choose(ctx, decision)
        assert mine.eig is not None and mine.eig.full_tie and mine.eig.degenerate
        assert _cell(mine) == random_cell(random_mode.seed, decision, ctx.menu)
        assert _cell(mine) == _cell(random_mode.policy.choose(ctx, decision))
