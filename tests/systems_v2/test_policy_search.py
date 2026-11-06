"""B-sym in two epochs around policy-chosen experiments (SPEC §4.2; P4 Δ11 WP9).

The policy arms hold proposal fixed (B-sym at budget F) and vary only the
choice of experiments, so the schedule is an instrument (SPEC §6.3): the fits
it charges, the data each fit sees, what the policy is shown and what is
submitted are checked here against a fake fitter whose BIC is a known function
of the structure (as ``test_search``) and a fake runner, so nothing is
simulated or fitted.
"""

from __future__ import annotations

import ast
import dataclasses
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np
import pytest

from environments.pointproc import v2
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.distance import structure_distance
from sciagent.glm.fit import FitConfig, FitError, FitResult, fit
from sciagent.glm.grammar import (
    ALL,
    ChannelSpec,
    Excite,
    Gate,
    KernelKind,
    LastMarkAbove,
    Link,
    Mark,
    One,
    Structure,
)
from sciagent.glm.interventions import Experiment
from sciagent.glm.syntax import render
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.policies import (
    Choice,
    DecisionContext,
    Policy,
    RandomPolicy,
)
from sciagent.systems.v2.policy_search import (
    PolicySearch,
    PolicySearchResult,
    epoch_budgets,
    random_choice,
)
from sciagent.systems.v2.search import BSym, GPConfig, SearchError
from sciagent.systems.v2.systems import InvestigationData, SystemRunError

CHANNELS = v2.CHANNELS
SEEDS: tuple[Structure, ...] = tuple(m.structure for m in v2.LIBRARY)
ROOT = Path(__file__).resolve().parents[2]
TARGET = canonicalise(
    Structure(
        (
            Excite(KernelKind.EXP, Mark("size"), ALL),
            Gate(Excite(KernelKind.EXP, One(), ALL), LastMarkAbove("size")),
        ),
        Link.IDENTITY,
    )
)
GP = GPConfig(population=3, offspring=3)


@cache
def _obs() -> Dataset:
    log = v2.TRUTHS["hawkes"].simulate(60.0, np.random.default_rng(0))
    return Dataset.observational(log)


def _data() -> InvestigationData:
    return InvestigationData((_obs(),), CHANNELS, v2.mark_sampler)


@cache
def _template() -> FitResult:
    return fit(v2.NULL, (_obs(),), CHANNELS)


@dataclass
class FakeFitter:
    """BIC = 1000 · distance to TARGET + features + 0.5 · (datasets - 1).

    Records the number of datasets each call saw, in call order.
    """

    calls: list[tuple[str, int]] = field(default_factory=list)
    #: Fits on this many datasets are uncertified.
    uncertified_sizes: frozenset[int] = frozenset()

    def __call__(
        self,
        structure: Structure,
        datasets: Sequence[Dataset],
        channels: tuple[ChannelSpec, ...],
        config: FitConfig | None,
    ) -> FitResult:
        key = structure_hash(canonicalise(structure))
        self.calls.append((key, len(datasets)))
        text = repr(structure)
        if "POWER" in text and "ExpOf" in text:
            raise FitError("fake failure")
        bic = (
            1000.0 * structure_distance(structure, TARGET)
            + len(structure.features)
            + 0.5 * (len(datasets) - 1)
        )
        return dataclasses.replace(
            _template(),
            structure=structure,
            link=structure.link,
            bic=bic,
            certified="GAMMA" not in text
            and len(datasets) not in self.uncertified_sizes,
        )


@dataclass
class FakeRunner:
    """Returns a fresh copy of the observational data, or None (stopped)."""

    stop: frozenset[int] = frozenset()
    calls: list[tuple[Experiment, str]] = field(default_factory=list)

    def run(self, experiment: Experiment, label: str) -> Dataset | None:
        index = len(self.calls)
        self.calls.append((experiment, label))
        if index in self.stop:
            return None
        return dataclasses.replace(_obs(), label=f"e{index}")


@dataclass
class RecordingPolicy:
    """Picks cell (decision mod designs, 0) and records what it was shown."""

    seed: int = 0
    name: str = "recording"
    seen: list[DecisionContext] = field(default_factory=list)

    @property
    def config(self) -> str:
        return '{"policy":"recording"}'

    def choose(self, context: DecisionContext, decision: int) -> Choice:
        self.seen.append(context)
        menu = context.menu
        e = decision % len(menu.designs)
        return Choice(
            self.name, decision, e, 0, menu.designs[e], menu.diagnostics[0], None
        )


def _search(
    policy: Policy, fitter: FakeFitter, *, budget: int = 12, experiments: int = 3
) -> PolicySearch:
    return PolicySearch(
        SEEDS,
        policy,
        policy_seed=5,
        experiments=experiments,
        config=GP,
        budget=budget,
        seed=7,
        fitter=fitter,
        name="B-sym+test",
    )


def _run(
    policy: Policy,
    *,
    budget: int = 12,
    experiments: int = 3,
    stop: frozenset[int] = frozenset(),
    fitter: FakeFitter | None = None,
) -> tuple[PolicySearchResult, FakeFitter, FakeRunner]:
    fitter = FakeFitter() if fitter is None else fitter
    runner = FakeRunner(stop)
    out = _search(policy, fitter, budget=budget, experiments=experiments).run(
        _data(), runner
    )
    return out, fitter, runner


# --------------------------------------------------------------------------
# The schedule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("budget", "split"), [(2, (1, 1)), (12, (6, 6)), (13, (6, 7))])
def test_the_budget_splits_in_half_with_the_odd_fit_in_epoch_two(
    budget: int, split: tuple[int, int]
) -> None:
    assert epoch_budgets(budget) == split


def test_epoch_one_is_plain_bsym_at_half_the_budget_on_the_observational_log() -> None:
    out, fitter, _ = _run(RecordingPolicy())
    plain = BSym(SEEDS, GP, budget=6, seed=7, fitter=FakeFitter()).run(_data())
    assert out.epochs[0] == plain.fits_used == 6
    names = [render(s) for s in out.charged[:6]]
    tail = [p.candidate for p in plain.trajectory]
    assert tail and names[-len(tail) :] == tail
    assert [n for _, n in fitter.calls[:6]] == [1] * 6


def test_epoch_one_and_the_first_decision_are_identical_across_policies() -> None:
    a, b = RecordingPolicy(), RecordingPolicy(name="other")
    out_a, _, _ = _run(a)
    out_b, _, _ = _run(RandomPolicy(seed=5))
    out_c, _, _ = _run(b)
    assert out_a.charged[:6] == out_b.charged[:6] == out_c.charged[:6]
    assert a.seen[0].entertained == b.seen[0].entertained
    assert a.seen[0].menu == b.seen[0].menu


def test_the_policy_sees_epoch_one_top_k_refitted_on_all_data_so_far() -> None:
    policy = RecordingPolicy()
    out, _, _ = _run(policy, experiments=3)
    assert len(policy.seen) == 3
    names = [h.name for h in policy.seen[0].entertained.hypotheses]
    assert 1 <= len(names) <= 4
    # Top K by BIC among epoch 1's certified fits, best first.
    assert list(names) == list(out.entertained_names)
    bics = [h.bic for h in policy.seen[0].entertained.hypotheses]
    assert bics == sorted(bics)
    # Decision k refits them on obs plus the k datasets collected so far.
    for k, ctx in enumerate(policy.seen):
        assert {h.name for h in ctx.entertained.hypotheses} == set(names)
        assert ctx.entertained.hypotheses[0].bic == pytest.approx(
            policy.seen[0].entertained.hypotheses[0].bic + 0.5 * k
        )
        assert ctx.observational is _obs()


def test_the_runner_runs_each_chosen_design_and_epoch_two_fits_all_data() -> None:
    policy = RecordingPolicy()
    out, fitter, runner = _run(policy, experiments=3)
    assert [label for _, label in runner.calls] == [
        d.choice.design.name for d in out.decisions
    ]
    assert [e for e, _ in runner.calls] == [
        d.choice.design.experiment for d in out.decisions
    ]
    epoch_two = fitter.calls[-out.epochs[1] :]
    assert out.epochs[1] == 6
    assert all(n == 4 for _, n in epoch_two)  # obs + 3 experiments


def test_a_stopped_experiment_counts_as_used_and_gives_no_data() -> None:
    policy = RecordingPolicy()
    out, fitter, runner = _run(policy, experiments=3, stop=frozenset({1}))
    assert len(runner.calls) == 3 and len(out.decisions) == 3
    assert [d.stopped for d in out.decisions] == [False, True, False]
    assert [len(c.entertained.hypotheses) > 0 for c in policy.seen] == [True] * 3
    # Decision 2 saw obs + e0 only; epoch 2 fits obs + e0 + e2.
    assert policy.seen[2].entertained.hypotheses[0].bic == pytest.approx(
        policy.seen[0].entertained.hypotheses[0].bic + 0.5
    )
    assert all(n == 3 for _, n in fitter.calls[-out.epochs[1] :])
    assert out.experiments_used == 3


def test_epoch_two_starts_from_epoch_one_top_population() -> None:
    out, _, _ = _run(RecordingPolicy())
    n1 = out.epochs[0]
    assert out.charged[n1 : n1 + GP.population] == out.epoch_two_initial
    assert len(out.epoch_two_initial) == GP.population


def test_the_submission_is_the_best_bic_of_epoch_two() -> None:
    out, _, _ = _run(RecordingPolicy())
    result = out.result
    assert result.fits_used == sum(out.epochs) <= 12
    assert result.structure is not None
    n1 = out.epochs[0]
    epoch_two = out.charged[n1:]
    assert canonicalise(result.structure) in epoch_two
    model = result.model
    assert isinstance(model, GLMModel) and model.fit.certified
    best = min(
        1000.0 * structure_distance(s, TARGET) + len(s.features)
        for s in epoch_two
        if "GAMMA" not in repr(s) and not ("POWER" in repr(s) and "ExpOf" in repr(s))
    )
    assert model.fit.bic == pytest.approx(best + 0.5 * 3)


def test_no_fittable_entertained_set_falls_back_to_the_random_draw() -> None:
    # Refits on obs + 1 or obs + 2 datasets are uncertified: decisions 1 and 2
    # have no entertained set; decision 0 (obs only) and epoch 2 (4) do.
    fitter = FakeFitter(uncertified_sizes=frozenset({2, 3}))
    policy = RecordingPolicy()
    out, _, _ = _run(policy, fitter=fitter)
    assert len(policy.seen) == 1
    first, *rest = out.decisions
    assert first.fallback is None and first.entertained is not None
    menu = policy.seen[0].menu
    for d in rest:
        assert d.entertained is None and d.fallback is not None
        assert d.fallback.startswith("random")
        want = random_choice(5, d.decision, menu)
        assert (d.choice.design_index, d.choice.diagnostic_index) == (
            want.design_index,
            want.diagnostic_index,
        )


def test_nothing_certified_anywhere_is_a_failed_run() -> None:
    fitter = FakeFitter(uncertified_sizes=frozenset({1, 2, 3, 4}))
    policy = RecordingPolicy()
    with pytest.raises(SystemRunError):
        _run(policy, fitter=fitter)
    assert policy.seen == []


def test_random_choice_is_the_random_policys_draw() -> None:
    policy = RecordingPolicy()
    _run(policy)
    ctx = policy.seen[0]
    for decision in range(5):
        mine = random_choice(5, decision, ctx.menu)
        theirs = RandomPolicy(seed=5).choose(ctx, decision)
        assert (mine.design_index, mine.diagnostic_index) == (
            theirs.design_index,
            theirs.diagnostic_index,
        )


def test_the_random_policy_fits_no_entertained_set() -> None:
    out, fitter, _ = _run(RandomPolicy(seed=5))
    assert all(d.entertained is None and d.fallback is None for d in out.decisions)
    assert len(fitter.calls) == sum(out.epochs)


def test_a_run_is_byte_identical_twice() -> None:
    a, _, _ = _run(RandomPolicy(seed=5))
    b, _, _ = _run(RandomPolicy(seed=5))
    assert a.as_json() == b.as_json()
    assert [d.as_json() for d in a.decisions] == [d.as_json() for d in b.decisions]
    c, _, _ = _run(RecordingPolicy())
    d, _, _ = _run(RecordingPolicy())
    assert c.as_json() == d.as_json()


def test_the_decision_record_holds_the_cell_and_framework_numbers() -> None:
    out, _, _ = _run(RecordingPolicy())
    record = out.decisions[0].as_json()
    assert record["decision"] == 0
    assert record["design"]["name"] == out.decisions[0].choice.design.name
    assert record["diagnostic"]["name"] == out.decisions[0].choice.diagnostic.name
    assert record["entertained"]["names"] == list(out.entertained_names)
    assert record["stopped"] is False
    assert all(math.isfinite(b) for b in record["entertained"]["bics"])


def test_misconfiguration_is_refused() -> None:
    with pytest.raises(SearchError):
        _search(RecordingPolicy(), FakeFitter(), budget=1)
    with pytest.raises(SearchError):
        _search(RecordingPolicy(), FakeFitter(), experiments=-1)
    two = InvestigationData((_obs(), _obs()), CHANNELS, v2.mark_sampler)
    with pytest.raises(SearchError):
        _search(RecordingPolicy(), FakeFitter()).run(two, FakeRunner())


def test_policy_search_never_names_the_truth() -> None:
    path = ROOT / "src" / "sciagent" / "systems" / "v2" / "policy_search.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    forbidden = {"truth", "TruthModel", "TruthSpec", "World", "coef", "resolve"}
    assert not names & forbidden
    assert not any(
        m.startswith(
            ("environments", "sciagent.scoring.truth", "sciagent.investigation")
        )
        or "policy_controls" in m
        for m in modules
    )
