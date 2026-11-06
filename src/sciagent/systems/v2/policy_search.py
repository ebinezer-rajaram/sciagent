"""B-sym in two epochs around policy-chosen experiments (SPEC §4.2; P4 Δ11 WP9).

SPEC §4.2 holds proposal fixed and varies only who chooses the evidence. For
the B-sym arms (``B-sym+random``, ``B-sym+EIG``, ``B-sym+FSD`` and the control
``B-sym+truthFSD``) proposal is B-sym (``search.py``) at the fit budget F, and
the schedule is (P4 plan, Δ11 choice 9):

1. **Epoch 1.** ``F // 2`` fits of plain B-sym on the observational log, with
   the arm's search seed. It reads no experiment and no policy, so it is
   identical across the policies of one (truth, seed), and it is exactly
   :class:`~sciagent.systems.v2.search.BSym` at that budget.
2. **E decisions.** Decision ``k`` (the index of the experiment about to run)
   refits the *entertained set* on all the data so far (``obs`` and every
   experiment that left a dataset), asks the injected :class:`Policy` for a
   (design, diagnostic) cell of the menus, and runs the design through the
   injected :class:`~sciagent.systems.v2.policies.ExperimentRunner`. A run the
   runner stops (``None``) counts as used and gives no data (Δ11 choice 14).
3. **Epoch 2.** ``F - F // 2`` fits of B-sym on all the data, starting from
   epoch 1's top population (its best ``GPConfig.population`` structures by
   BIC, as B-sym ranks them) and drawing from a seed derived from the search
   seed (:func:`epoch_two_seed`). Its first fits are therefore those
   structures refitted on the new data, and they are charged: a fit on other
   data is another fit.
4. **Submission.** The best epoch-2 fit by BIC (epoch 1's BICs are on other
   data, so not comparable). With no certified epoch-2 fit the run fails.

**Entertained set.** The top :data:`~sciagent.systems.v2.policies.ENTERTAINED_MAX`
certified epoch-1 fits by BIC (ties to the earlier fit), fixed for the whole
run and refitted on all the data at every decision by the same fitter
(framework-internal and uncharged, as in the AG-c-S arms;
:func:`~sciagent.systems.v2.policies.fit_entertained`). The random policy reads
no entertained set, so it fits nothing. When none of them can be refitted and
certified, or epoch 1 certified nothing, the decision is the random policy's
draw (:func:`random_choice`) and ``fallback`` says why, as in the tool layer's
policy mode.

**Menus.** :func:`~sciagent.systems.v2.policies.design_menu` and
:func:`~sciagent.systems.v2.policies.diagnostic_menu`, frozen once on the
observational log.

**What is recorded** (:class:`Decision`, :meth:`PolicySearchResult.as_json`):
every decision's cell, the entertained set with its BICs and weights, the
policy's score table and EIG summary, and whether the run stopped. All of it is
framework-side: nothing here reaches an agent (invariant 2).

**Seeds.** The policy draws from its own ``policy_seed`` streams
(``scoring/seeds.py``); the search seeds epoch 1 and, through
:func:`epoch_two_seed`, epoch 2. The truth never enters this module: the
truth-aware control's policy is built by the caller and injected like any
other (``policy_controls.py``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from sciagent.diagnostics.catalogue import ArgValue
from sciagent.glm.canonical import structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, FitResult
from sciagent.glm.grammar import Structure
from sciagent.scoring.seeds import generator, policy_seed
from sciagent.systems.v2.policies import (
    ENTERTAINED_MAX,
    RANDOM_INDEX,
    Choice,
    DecisionContext,
    EIGSummary,
    EntertainedSet,
    ExperimentRunner,
    Menu,
    Policy,
    PolicyError,
    RandomPolicy,
    design_menu,
    diagnostic_menu,
    fit_entertained,
)
from sciagent.systems.v2.search import (
    Fitter,
    GPConfig,
    SearchError,
    _gp,
    _Ledger,
    _Record,
    _Settings,
    certified_fit,
)
from sciagent.systems.v2.systems import (
    InvestigationData,
    SystemResult,
    TrajectoryPoint,
)

#: The spawn word of epoch 2's search seed (:func:`epoch_two_seed`).
EPOCH_TWO: Final = 2

type Json = Any


def epoch_budgets(budget: int) -> tuple[int, int]:
    """``(F // 2, F - F // 2)``: an odd fit goes to epoch 2."""
    if budget < 2:
        raise SearchError(f"two epochs need a budget of at least 2, got {budget}")
    first = budget // 2
    return first, budget - first


def epoch_two_seed(seed: int) -> int:
    """Epoch 2's search seed: 63 bits of ``SeedSequence(seed, spawn_key=(2,))``."""
    state = np.random.SeedSequence(entropy=seed, spawn_key=(EPOCH_TWO,))
    return int(state.generate_state(1, dtype=np.uint64)[0]) >> 1


def random_choice(seed: int, decision: int, menu: Menu) -> Choice:
    """:class:`~sciagent.systems.v2.policies.RandomPolicy`'s draw, which needs
    no entertained set: one integer over the cells from
    ``policy_seed(seed, decision, RANDOM_INDEX, 0)``."""
    n_d = len(menu.diagnostics)
    rng = generator(policy_seed(seed, decision, RANDOM_INDEX, 0))
    e, d = divmod(int(rng.integers(len(menu.designs) * n_d)), n_d)
    return Choice("random", decision, e, d, menu.designs[e], menu.diagnostics[d], None)


def _json_value(value: ArgValue) -> ArgValue:
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)
    return float(value)


def _entertained_json(entertained: EntertainedSet) -> dict[str, Json]:
    return {
        "names": [h.name for h in entertained.hypotheses],
        "bics": [h.bic for h in entertained.hypotheses],
        "weights": list(entertained.weights),
        "skipped": list(entertained.skipped),
    }


def _eig_json(summary: EIGSummary) -> dict[str, Json]:
    return {
        "weights": list(summary.weights),
        "entropy": summary.entropy,
        "max_eig": summary.max_eig,
        "tolerance": summary.tolerance,
        "tied": summary.tied,
        "degenerate": summary.degenerate,
    }


@dataclass(frozen=True)
class Decision:
    """One decision: the cell, what the policy saw, and whether the run stopped."""

    decision: int
    choice: Choice
    entertained: EntertainedSet | None
    fallback: str | None
    stopped: bool
    n_events: int | None

    def as_json(self) -> dict[str, Json]:
        c = self.choice
        return {
            "decision": self.decision,
            "design": {"index": c.design_index, "name": c.design.name},
            "diagnostic": {
                "index": c.diagnostic_index,
                "name": c.diagnostic.name,
                "args": {k: _json_value(v) for k, v in c.diagnostic.args},
            },
            "entertained": None
            if self.entertained is None
            else _entertained_json(self.entertained),
            "scores": None if c.scores is None else [list(row) for row in c.scores],
            "eig": None if c.eig is None else _eig_json(c.eig),
            "fallback": self.fallback,
            "stopped": self.stopped,
            "n_events": self.n_events,
        }


@dataclass(frozen=True)
class PolicySearchResult:
    """The submission and the run's record.

    ``result.model`` is the submission fitted on all the data, and
    ``result.trajectory`` is epoch 1's best-so-far (on the observational log)
    followed by epoch 2's (on all the data, fit numbers continuing), so the
    best-so-far restarts at epoch 2. ``charged`` is every charged structure in
    fit order across both epochs, and ``observational_fits`` holds epoch 1's
    fits (on the observational log) by structure hash, for a scorer that
    refits on that log.
    """

    result: SystemResult
    decisions: tuple[Decision, ...]
    epochs: tuple[int, int]
    charged: tuple[Structure, ...]
    epoch_two_initial: tuple[Structure, ...]
    entertained_names: tuple[str, ...]
    observational_fits: dict[str, FitResult | None]
    policy_config: str

    @property
    def experiments_used(self) -> int:
        return len(self.decisions)

    def as_json(self) -> dict[str, Json]:
        """The framework-side record of the run (module docstring)."""
        return {
            "policy_config": self.policy_config,
            "epochs": list(self.epochs),
            "entertained": list(self.entertained_names),
            "epoch_two_initial": [structure_hash(s) for s in self.epoch_two_initial],
            "charged": [structure_hash(s) for s in self.charged],
            "submitted": self.result.submitted,
            "decisions": [d.as_json() for d in self.decisions],
        }


def _ranked(records: Sequence[_Record]) -> list[_Record]:
    """B-sym's ranking: by BIC, ties to the earlier fit; uncertified last."""
    return sorted(records, key=lambda r: (r.criterion, r.number))


@dataclass(frozen=True)
class PolicySearch:
    """B-sym in two epochs around ``experiments`` policy decisions.

    ``policy`` is any :class:`~sciagent.systems.v2.policies.Policy` (the
    truth-aware control included, built by the caller); ``policy_seed`` is the
    seed of the random fallback and must be the policy's own seed.
    ``seed`` is the search seed. ``fitter`` fits both the search and the
    entertained set.
    """

    seeds: tuple[Structure, ...]
    policy: Policy
    policy_seed: int
    experiments: int
    config: GPConfig = field(default_factory=GPConfig)
    budget: int = 40
    seed: int = 0
    workers: int = 1
    fit_config: FitConfig | None = None
    fitter: Fitter = certified_fit
    entertained: int = ENTERTAINED_MAX
    name: str = "B-sym+policy"

    def __post_init__(self) -> None:
        epoch_budgets(self.budget)
        if self.experiments < 0:
            raise SearchError(f"experiments must be ≥ 0, got {self.experiments}")
        if self.workers < 1:
            raise SearchError(f"workers must be ≥ 1, got {self.workers}")
        if not 1 <= self.entertained <= ENTERTAINED_MAX:
            raise SearchError(
                f"the entertained set holds 1..{ENTERTAINED_MAX}, "
                f"got {self.entertained}"
            )

    def _settings(self) -> _Settings:
        return _Settings(self.fit_config, self.fitter, None)

    def _decide(
        self,
        decision: int,
        candidates: Sequence[tuple[str, Structure]],
        datasets: Sequence[Dataset],
        data: InvestigationData,
        menu: Menu,
    ) -> tuple[Choice, EntertainedSet | None, str | None]:
        if isinstance(self.policy, RandomPolicy):
            return random_choice(self.policy_seed, decision, menu), None, None
        if not candidates:
            reason = "random: epoch 1 certified no structure"
            return random_choice(self.policy_seed, decision, menu), None, reason
        try:
            entertained = fit_entertained(
                candidates,
                datasets,
                data.channels,
                data.marks,
                fitter=self.fitter,
                config=self.fit_config,
                max_size=self.entertained,
            )
        except PolicyError as error:
            reason = f"random: {error}"
            return random_choice(self.policy_seed, decision, menu), None, reason
        context = DecisionContext(data.channels, datasets[0], entertained, menu)
        choice = self.policy.choose(context, decision)
        if choice.scores is not None and not all(
            math.isfinite(x) for row in choice.scores for x in row
        ):
            raise PolicyError("the score table is not finite")
        return choice, entertained, None

    def run(
        self, data: InvestigationData, runner: ExperimentRunner
    ) -> PolicySearchResult:
        if len(data.observational) != 1:
            raise SearchError(
                "the policy arms start from exactly one observational log"
            )
        (obs,) = data.observational
        first, second = epoch_budgets(self.budget)
        settings = self._settings()
        one = _gp(
            self.seeds, data, self.config, first, self.seed, self.workers, settings
        )
        ranked = _ranked(one.records)
        certified = [r for r in ranked if math.isfinite(r.criterion)]
        candidates = [(r.name, r.structure) for r in certified[: self.entertained]]
        menu = Menu(design_menu(data.channels), diagnostic_menu(obs.log, data.channels))
        collected: list[Dataset] = []
        decisions: list[Decision] = []
        for k in range(self.experiments):
            datasets = (obs, *collected)
            choice, entertained, fallback = self._decide(
                k, candidates, datasets, data, menu
            )
            got = runner.run(choice.design.experiment, choice.design.name)
            if got is not None:
                collected.append(got)
            decisions.append(
                Decision(
                    k,
                    choice,
                    entertained,
                    fallback,
                    got is None,
                    None if got is None else got.log.n,
                )
            )
        initial = tuple(
            r.structure for r in (certified or ranked)[: self.config.population]
        )
        everything = InvestigationData((obs, *collected), data.channels, data.marks)
        two: _Ledger = _gp(
            initial,
            everything,
            self.config,
            second,
            epoch_two_seed(self.seed),
            self.workers,
            settings,
        )
        submitted = two.result(self.name)
        before = one.result(self.name).trajectory if certified else ()
        n1 = len(one.records)
        trajectory = (
            *before,
            *(
                TrajectoryPoint(
                    p.fits_used + n1,
                    p.candidate,
                    p.candidate_criterion,
                    p.best,
                    p.best_criterion,
                    p.best_model,
                )
                for p in submitted.trajectory
            ),
        )
        result = SystemResult(
            system=self.name,
            submitted=submitted.submitted,
            structure=submitted.structure,
            model=submitted.model,
            fits_used=n1 + submitted.fits_used,
            trajectory=trajectory,
            skipped=(
                *(r.name for r in one.records if math.isinf(r.criterion)),
                *submitted.skipped,
            ),
        )
        return PolicySearchResult(
            result=result,
            decisions=tuple(decisions),
            epochs=(n1, len(two.records)),
            charged=tuple(r.structure for r in (*one.records, *two.records)),
            epoch_two_initial=initial,
            entertained_names=tuple(name for name, _ in candidates),
            observational_fits={r.key: r.result for r in one.records},
            policy_config=self.policy.config,
        )
