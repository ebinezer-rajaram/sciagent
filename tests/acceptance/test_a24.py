"""Acceptance test A24 (SPEC §6.6): one-step-greedy BOED is a fair baseline.

A24 reads: "On scenarios with computable optimal policies, one-step-greedy
BOED's realised information gain is within 10% of the greedy optimum. Confirms
the implementation is a fair baseline rather than a straw man."

What "the greedy optimum" is
----------------------------

Three readings were available and the resolution is recorded in
``docs/DECISIONS.md``. The one measured here: the reference policy is
**one-step-greedy on the exact expected information gain**, computed from
outcome distributions known in closed form. Our BOED chooses from a finite
empirical table of the same distributions. The gap between them is therefore the
implementation's *estimation* error, which is what "the implementation is a fair
baseline" is a claim about.

The two rejected readings measure something else. A hindsight oracle -- picking
the design that maximises the gain actually realised on the outcome drawn -- is
not a target an expectation-maximiser can be held to within 10%. A non-myopic
dynamic-programming optimum measures the cost of *myopia*, which is a property of
greedy selection rather than of this implementation, and SPEC §11 assigns
exhaustive DP to item 11.

Why the scenarios are synthetic
-------------------------------

"Computable optimal policies" requires outcome distributions that are exactly
known. The slice's are not: they are what the empirical table estimates, so on
the slice there is no exact reference to compare against, only a
higher-replicate estimate of one. These scenarios are drawn instead, with the
distributions written down and the table then *simulated from them*, which is
the only configuration where the reference is genuinely exact. The slice
wire-up is checked separately in ``tests/test_boed.py``.

Two devices make the measurement mean something
-----------------------------------------------

**The comparison is coupled.** One outcome is pre-drawn for every ``(step,
design)`` pair from the true hypothesis, before either policy runs. Whichever
design a policy picks at step ``t``, it meets the outcome that was already
waiting there. Where the two policies agree their realised gain is then
identical rather than merely similar, so the aggregate difference is the effect
of the decisions and carries no outcome noise at all.

**Both trajectories are scored exactly.** The table policy plans on the table --
it maintains its own table-derived belief, because a real BOED run has nothing
else -- but the realised gain of both policies is computed by exact Bayes
updates on the closed-form distributions. Scoring a policy under its own
approximate belief would let a badly calibrated estimator report a large gain by
being confidently wrong.

**The criterion has teeth.** A gate that a broken implementation also passes is
not a gate. :meth:`TestA24GreedyOptimality.test_a24_the_bound_rejects_random_selection`
runs the identical harness with the design chosen at random and requires it to
*fail* the 10% bound, so the threshold is shown to discriminate rather than
assumed to.

The table is built at 200 replicates rather than the slice's 2000. A24 bounds
the estimation error of the selector, so it is measured where that error is
visible; at the deployed replicate count the two policies almost never diverge
and the bound would be met without testing anything.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from sciagent.core.types import (
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    Probability,
)
from sciagent.experiments.boed import (
    Predictive,
    PredictiveSource,
    Step,
    greedy,
)
from sciagent.inference.entropy import entropy_bits

#: Scenarios per measurement. The statistic A24 bounds is a ratio of two means,
#: and 200 holds its standard error well under the 10% the bound allows.
N_SCENARIOS = 200

N_HYPOTHESES = 5
N_DESIGNS = 6
N_CELLS = 8

#: Experiments per policy. Three is SPEC §4.2's minimum discriminating plan
#: length, and it is the shortest horizon on which sequencing can matter at all:
#: the belief after step one changes which design is best at step two.
HORIZON = 3

#: Replicates behind each table row. Deliberately an order of magnitude below the
#: slice's 2000 -- see this module's docstring.
REPLICATES = 200

#: How far each design's per-hypothesis outcome distributions are pushed apart.
#: One nearly useless design, a spread, and a deliberate near-tie at the top:
#: 0.78 against 0.80 is where a finite table is most likely to rank the two the
#: wrong way round, which is the case A24 exists to bound.
SEPARATION = (0.10, 0.25, 0.45, 0.60, 0.78, 0.80)

A24_SEED = 20260803

HYPOTHESES: tuple[HypothesisId, ...] = tuple(
    HypothesisId(f"h{index}") for index in range(N_HYPOTHESES)
)
TEMPLATES: tuple[ExperimentTemplateId, ...] = tuple(
    ExperimentTemplateId(f"d{index}") for index in range(N_DESIGNS)
)
DESIGN_INDEX: Mapping[ExperimentTemplateId, int] = {
    template: index for index, template in enumerate(TEMPLATES)
}
HYPOTHESIS_INDEX: Mapping[HypothesisId, int] = {
    hypothesis: index for index, hypothesis in enumerate(HYPOTHESES)
}


# ==========================================================================
# Scenarios with exactly known outcome distributions
# ==========================================================================


@dataclass(frozen=True, slots=True)
class Scenario:
    """One scenario whose greedy optimum is computable because nothing is estimated.

    ``exact`` is the closed-form outcome distribution of every (design,
    hypothesis) pair; ``counts`` is one simulation of it at
    :data:`REPLICATES`, which is all the table policy is allowed to see.
    """

    exact: np.ndarray
    """``[design, hypothesis, cell]`` probabilities. Rows sum to one."""

    counts: np.ndarray
    """``[design, hypothesis, cell]`` simulated counts. Rows sum to REPLICATES."""

    prior: np.ndarray
    """``[hypothesis]`` prior probabilities."""

    truth: int
    """Index of the hypothesis the outcomes were drawn from."""

    outcomes: np.ndarray
    """``[step, design]`` pre-drawn cell indices. See the module docstring on
    why an outcome exists for every design and not only for the chosen one."""


def build_scenario(rng: np.random.Generator) -> Scenario:
    """Draw one scenario, with every quantity from the generator passed in.

    Guarantees no randomness outside ``rng``: the same generator state produces
    the same scenario, which is what makes A24's measured ratio reproducible.
    """
    prior = rng.dirichlet(np.full(N_HYPOTHESES, 3.0))
    exact = np.empty((N_DESIGNS, N_HYPOTHESES, N_CELLS))
    for design, weight in enumerate(SEPARATION):
        # A shared base the design would return whatever the truth is, mixed
        # with a per-hypothesis signature. `weight` is how much of the second
        # survives, so it is exactly the design's informativeness. Both parts
        # are distributions, so the mixture needs no renormalisation and every
        # cell keeps positive probability.
        base = rng.dirichlet(np.full(N_CELLS, 2.0))
        for hypothesis in range(N_HYPOTHESES):
            signature = rng.dirichlet(np.full(N_CELLS, 0.5))
            exact[design, hypothesis] = (1.0 - weight) * base + weight * signature

    counts = np.empty((N_DESIGNS, N_HYPOTHESES, N_CELLS), dtype=np.int64)
    for design in range(N_DESIGNS):
        for hypothesis in range(N_HYPOTHESES):
            counts[design, hypothesis] = rng.multinomial(
                REPLICATES, exact[design, hypothesis]
            )

    truth = int(rng.choice(N_HYPOTHESES, p=prior))
    outcomes = np.empty((HORIZON, N_DESIGNS), dtype=np.int64)
    for design in range(N_DESIGNS):
        outcomes[:, design] = rng.choice(N_CELLS, size=HORIZON, p=exact[design, truth])
    return Scenario(
        exact=exact, counts=counts, prior=prior, truth=truth, outcomes=outcomes
    )


@lru_cache(maxsize=1)
def scenarios() -> tuple[Scenario, ...]:
    """Return the A24 benchmark, drawn once from :data:`A24_SEED`."""
    rng = np.random.default_rng(A24_SEED)
    return tuple(build_scenario(rng) for _ in range(N_SCENARIOS))


# ==========================================================================
# The two things a policy is given: what it believes, and what it observes
# ==========================================================================


def exact_source(scenario: Scenario) -> PredictiveSource:
    """Return the closed-form predictive: the reference policy's whole advantage."""

    def predict(hypothesis: HypothesisId, template: ExperimentTemplateId) -> Predictive:
        cells = scenario.exact[DESIGN_INDEX[template], HYPOTHESIS_INDEX[hypothesis]]
        return Predictive(cells=tuple(cells), samples=math.inf)

    return predict


def table_source(scenario: Scenario) -> PredictiveSource:
    """Return the predictive a finite table supports.

    Krichevsky-Trofimov, matching
    :meth:`sciagent.inference.empirical.EmpiricalTable.probabilities` exactly, so
    what is measured here is the estimator the slice actually deploys rather than
    a stand-in for it.
    """
    denominator = REPLICATES + 0.5 * N_CELLS

    def predict(hypothesis: HypothesisId, template: ExperimentTemplateId) -> Predictive:
        row = scenario.counts[DESIGN_INDEX[template], HYPOTHESIS_INDEX[hypothesis]]
        return Predictive(
            cells=tuple((count + 0.5) / denominator for count in row),
            samples=float(REPLICATES),
        )

    return predict


def observer(scenario: Scenario) -> Callable[[int, ExperimentTemplateId], int]:
    """Return the pre-drawn outcome of any design at any step."""

    def observe(step: int, template: ExperimentTemplateId) -> int:
        return int(scenario.outcomes[step, DESIGN_INDEX[template]])

    return observe


def prior_belief(scenario: Scenario) -> FrozenDict[HypothesisId, Probability]:
    """Return the scenario's prior as the belief a policy starts from."""
    return FrozenDict[HypothesisId, Probability](
        {
            hypothesis: Probability(float(scenario.prior[index]))
            for index, hypothesis in enumerate(HYPOTHESES)
        }
    )


# ==========================================================================
# Scoring: exact Bayes, independent of the implementation under test
# ==========================================================================


def realised_bits(scenario: Scenario, steps: Sequence[Step]) -> float:
    """Return the information a chosen sequence actually gained, in bits.

    Computed by exact Bayes updates on the closed-form distributions, in plain
    numpy. Deliberately not routed through
    :func:`sciagent.experiments.boed.update`: a test that scored the
    implementation with the implementation would report agreement between a
    function and itself.
    """
    belief = np.array(scenario.prior, dtype=float)
    before = entropy_bits(list(belief))
    for step in steps:
        design = DESIGN_INDEX[step.template]
        belief = belief * scenario.exact[design, :, step.cell]
        total = float(belief.sum())
        assert total > 0.0, "an exact predictive assigns no cell zero probability"
        belief = belief / total
    return before - entropy_bits(list(belief))


def random_steps(scenario: Scenario, rng: np.random.Generator) -> tuple[Step, ...]:
    """Return a trajectory that picks uniformly at random rather than by gain.

    The straw man A24's threshold has to be able to tell apart from the real
    selector. It sees the same pre-drawn outcomes, so it differs from the greedy
    policies in the choice and in nothing else.
    """
    observe = observer(scenario)
    steps: list[Step] = []
    for index in range(HORIZON):
        template = TEMPLATES[int(rng.integers(N_DESIGNS))]
        steps.append(
            Step(
                template=template,
                expected=0.0,
                cell=observe(index, template),
                realised=0.0,
                posterior=prior_belief(scenario),
            )
        )
    return tuple(steps)


def run_policies() -> tuple[float, float, float, float]:
    """Return mean realised bits for the exact, table and random policies.

    The fourth value is the fraction of steps on which the table policy chose
    the design the exact policy chose. Reported rather than asserted: it is what
    explains the ratio, and A24 bounds the ratio.
    """
    exact_total = 0.0
    table_total = 0.0
    random_total = 0.0
    agreements = 0
    rng = np.random.default_rng(A24_SEED + 1)
    for scenario in scenarios():
        belief = prior_belief(scenario)
        observe = observer(scenario)
        by_exact = greedy(
            TEMPLATES, belief, exact_source(scenario), observe, steps=HORIZON
        )
        by_table = greedy(
            TEMPLATES, belief, table_source(scenario), observe, steps=HORIZON
        )
        exact_total += realised_bits(scenario, by_exact)
        table_total += realised_bits(scenario, by_table)
        random_total += realised_bits(scenario, random_steps(scenario, rng))
        agreements += sum(
            1
            for a, b in zip(by_exact, by_table, strict=True)
            if a.template == b.template
        )
    n = float(N_SCENARIOS)
    return (
        exact_total / n,
        table_total / n,
        random_total / n,
        agreements / (n * HORIZON),
    )


@lru_cache(maxsize=1)
def measured() -> tuple[float, float, float, float]:
    """Return :func:`run_policies`, computed once for both A24 tests."""
    return run_policies()


# ==========================================================================
# A24
# ==========================================================================


class TestA24GreedyOptimality:
    """SPEC §6.6 A24: greedy BOED is within 10% of the greedy optimum."""

    def test_a24_greedy_boed_is_within_ten_percent_of_the_greedy_optimum(self) -> None:
        """The table-driven selector realises at least 90% of the exact one's gain."""
        exact, table, _, agreement = measured()

        assert exact > 0.5, (
            f"the reference policy gained {exact:.3f} bits on average; a "
            f"benchmark where the optimum learns nothing cannot bound anything"
        )
        assert table >= 0.9 * exact, (
            f"one-step-greedy BOED realised {table:.4f} bits against the greedy "
            f"optimum's {exact:.4f} ({table / exact:.1%}); A24 requires 90%. "
            f"The two policies chose the same design on {agreement:.1%} of steps"
        )

    def test_a24_the_bound_rejects_random_selection(self) -> None:
        """The same harness, choosing at random, must fail the same bound.

        Not a property of BOED. It is the evidence that A24's threshold
        discriminates: were random selection also inside 10%, the design space
        would carry no decision worth making and the criterion would pass
        regardless of what the selector did.
        """
        exact, _, random_choice, _ = measured()

        assert random_choice < 0.9 * exact, (
            f"choosing a design at random realised {random_choice:.4f} bits "
            f"against the greedy optimum's {exact:.4f} "
            f"({random_choice / exact:.1%}), which is inside the 10% A24 allows. "
            f"A24 is then not measuring the selector"
        )
