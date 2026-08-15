"""How many experiments an optimal policy needs (SPEC §11, item 11).

The number every agent result is read against. An investigation that identified
the truth in five experiments did well or badly depending on whether three were
enough, and "three" is not a matter of opinion: with a finite design set, a
finite outcome space and a hypothesis set the framework holds, the optimal
adaptive policy's expected length is computable.

What is being counted
---------------------

**Oracle policy length** is the minimum over adaptive policies of ``E[T]``,
where ``T`` is the first experiment after which the posterior mass on the true
structure exceeds :data:`IDENTIFIED`. The threshold is
:attr:`~sciagent.eval.scoring.ClosedWorldScore.identified`'s, deliberately: the
oracle and the systems it judges are then held to one criterion, and "the oracle
needed 3 and B4 needed 6" is a comparison rather than two unrelated numbers.

SPEC §6.6's A24 measures something else and the two should not be confused. A24
bounds *one-step-greedy's estimation error* against exact-EIG greedy over one
step. This module asks what a policy that plans over the whole horizon can do,
so the gap between :attr:`OraclePolicyLength.expected_steps` and
:attr:`greedy_expected_steps` is exactly the cost of myopia -- the quantity SPEC
§13 reserves for two-step lookahead, measured here without implementing it.

Two distributions, and why they differ
--------------------------------------

An outcome is drawn from the world the scenario actually runs -- the truth
*and* any nuisance -- and the belief is updated from what the hypotheses
predict. Where a scenario is well specified these are the same distribution and
the distinction is invisible. Where it is not, it is the whole scenario:

* **S12** draws from regime switching under a censoring observation process
  while every hypothesis predicts an uncensored world, so the oracle is misled
  exactly as a system would be, and its length counts the recovery.
* **S11** draws from a mechanism no hypothesis holds. The mass on the truth is
  then zero forever and :attr:`OraclePolicyLength.reach_probability` is zero --
  which is not a failure of the computation but the scenario's content:
  no policy over this hypothesis set identifies S11, so extending the set is
  necessary rather than merely a good idea.

Exact where tractable, bracketed where not
------------------------------------------

SPEC §11's gate is "exhaustive DP where tractable, planning-baseline lower bound
otherwise", and both halves are here:

``expected_steps``
    Backward induction over the belief tree to :data:`DEFAULT_HORIZON`. Every
    design times every outcome cell branches at each level, so the tree is
    ``(designs x cells)^horizon`` and the horizon is where tractability ends.
    A path still unresolved at the horizon is charged one further experiment --
    ``horizon + 1`` in total, the least any continuation could cost -- so the
    value is *exact* when :attr:`OraclePolicyLength.certain` holds and a
    **lower bound** otherwise.

``greedy_expected_steps`` with ``greedy_completion``
    What one-step-greedy actually achieves, by seeded rollout: the mean length
    of the rollouts that identified the truth, and the fraction that did. When
    the fraction is one the mean is an achievable expected length and therefore
    an upper bound on the optimum, and the pair brackets it. Below one it says
    what greedy costs when it works and how often that is, which is a different
    and equally worth-knowing thing.

``evidence_bound``
    A floor that does not depend on the horizon at all. To hold more than half
    the mass the truth must out-weigh *every* rival, and one experiment moves
    the odds against a given rival by at most the largest log-likelihood ratio
    any cell of any design affords. The prior odds divided by that per-step
    maximum is therefore a length no policy can beat.

    An earlier version of this bound divided the information gap by
    ``log2(cells)``, on the argument that no experiment carries more bits than
    its outcome space holds. That is a statement about *mutual information* and
    not about the odds on one hypothesis, which a single outcome can move by an
    arbitrary amount when one hypothesis nearly excludes a cell another
    frequents. It was measurably wrong -- it exceeded what greedy achieved on
    S2 -- and is recorded here because a bound that is quietly too large would
    have flattered every system measured against it.

Nothing here is reachable from a research system. The oracle reads the ground
truth, which is why it lives in :mod:`sciagent.eval` alongside
:class:`~sciagent.eval.scenarios.Scenario` and not in
:mod:`sciagent.experiments`, whose ``dsl`` is on the agent tool surface
(A14, A17).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from sciagent.core.errors import InferenceError, MalformedDesignError
from sciagent.core.types import (
    ExperimentTemplateId,
    HypothesisId,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.experiments.boed import PredictiveSource, select, update

__all__ = [
    "DEFAULT_HORIZON",
    "DEFAULT_SEED",
    "GREEDY_ROLLOUTS",
    "IDENTIFIED",
    "OraclePolicyLength",
    "World",
    "evidence_bound",
    "greedy_expected_steps",
    "oracle_policy_length",
]

#: What the scenario's environment actually returns, one distribution per
#: design. Read from the same empirical table every likelihood comes from, over
#: the *executed* defect -- truth and nuisance together -- so the world a policy
#: faces and the world the framework can compute in are the same world.
#:
#: A plain mapping rather than a
#: :data:`~sciagent.experiments.boed.PredictiveSource`, because it speaks for the
#: environment and not for anybody's belief about it: there is no hypothesis to
#: condition on.
type World = Mapping[ExperimentTemplateId, Sequence[float]]

#: Posterior mass on the true structure that counts as having identified it.
#: :attr:`~sciagent.eval.scoring.ClosedWorldScore.identified`'s threshold, so the
#: oracle answers the question the systems are scored on.
IDENTIFIED = 0.5

#: How deep the exhaustive dynamic programme goes. The tree branches by designs
#: times cells at every level -- on the slice, five designs over nine to fifteen
#: cells, so about sixty children per node and a quarter of a million nodes at
#: three. Four would be fifteen million and is where "where tractable" stops.
DEFAULT_HORIZON = 3

#: Seed the greedy rollouts are drawn under when a caller states none. Zero, and
#: a module-level constant rather than a call in a default argument, so the
#: default is one object shared by every call and not a fresh one per call.
DEFAULT_SEED = Seed(0)

#: Rollouts behind the greedy upper bound. Greedy is deterministic given the
#: outcomes, so the only randomness is which outcomes the world returns, and a
#: few thousand draws resolve a mean length of a few steps to a hundredth.
GREEDY_ROLLOUTS = 2000

#: Decimal places the dynamic programme's values are compared to. Two designs
#: agreeing to here are a tie, and a tie is broken by template id so the choice
#: is the same in every process. Nine places sits eight orders of magnitude
#: below the smallest margin evidence produces on the slice and eight above the
#: rounding of a float sum, so it separates what the world says from what the
#: order a metric happened to be summed in says.
VALUE_PLACES = 9


@dataclass(frozen=True, slots=True)
class OraclePolicyLength:
    """What an optimal policy needs on one scenario, and how well it is known.

    Never a bare number. An expected length computed to a finite horizon, a
    probability that the horizon was enough, an achievable length and a floor
    are four different statements, and collapsing them would hide which of them
    the reader is relying on.
    """

    scenario: ScenarioId
    horizon: int
    """How deep the exhaustive dynamic programme searched."""

    expected_steps: float
    """Expected experiments to identification under the optimal policy.

    Exact when :attr:`certain`; otherwise a lower bound, because a path still
    unresolved at the horizon was charged ``horizon + 1`` and cannot truly cost
    less.
    """

    certain: bool
    """Whether the optimal policy identifies the truth on *every* path.

    ``False`` leaves :attr:`expected_steps` a lower bound rather than a value.
    """

    reach_probability: float
    """Probability that the optimal policy identifies the truth within the
    horizon, under the distribution the world actually draws from."""

    greedy_expected_steps: float | None
    """Mean length of the one-step-greedy rollouts that identified the truth.

    An upper bound on the optimum **only** when :attr:`greedy_completion` is
    one, since it is otherwise a mean conditional on finishing. ``None`` where no
    rollout finished."""

    greedy_completion: float
    """Fraction of greedy rollouts that identified the truth within the limit.

    Below one it is the more informative of the two numbers: a policy that
    finishes nine times in ten and a policy that finishes twice in a hundred can
    report the same conditional mean and are not the same policy."""

    evidence_bound: float
    """Floor from the prior odds and the sharpest cell any design affords,
    independent of the horizon. ``inf`` where no evidence identifies the truth
    at all -- see :func:`evidence_bound`."""

    truth_mass_prior: float
    """Mass the prior put on the true structure, before any experiment."""

    first_design: ExperimentTemplateId | None
    """The design the optimal policy opens with.

    ``None`` if the truth is already identified before any experiment, or not
    reachable within the horizon -- which is to say whenever
    :attr:`identifiable` is ``False``. A saturated search scores every design at
    the ``horizon + 1`` floor and so has no preference to report; naming one
    anyway would be reporting the id sort as though it were the evidence."""

    @property
    def identifiable(self) -> bool:
        """Return whether any policy identifies the truth within the horizon."""
        return self.reach_probability > 0.0

    def exceeds(self, budget: float) -> bool:
        """Return whether ``budget`` experiments cannot buy identification.

        What SPEC §4.5's S10 -- "budget below the discriminating threshold" --
        is a claim about. Read off :attr:`expected_steps`, which is a lower
        bound when uncertain, so a ``True`` here is never an artefact of the
        horizon being short.
        """
        return not self.identifiable or self.expected_steps > budget


def _belief_key(belief: Mapping[HypothesisId, Probability]) -> tuple[int, ...]:
    """Return a coarse, order-independent key for memoising a belief.

    Rounded to a part in a million, which merges beliefs no further experiment
    could tell apart and keeps the memo table finite. It is a *cache* key: two
    beliefs that collide here differ by less than the Monte Carlo error of the
    table the beliefs came from, and their optimal continuations are identical
    to far more places than the value is reported to.
    """
    return tuple(round(float(belief[key]) * 1_000_000) for key in sorted(belief))


def _choice_key(
    value: float, reached: float, template: ExperimentTemplateId
) -> tuple[float, float, str]:
    """Return the order the dynamic programme picks a design by.

    Guarantees a total order that is a pure function of its arguments and
    independent of the order candidates are offered in. Rounding before
    comparing is what buys both: near-ties collapse to exact ties so the later
    keys settle them, and unlike a tolerance the relation stays transitive, so
    no chain of pairwise comparisons can turn on which candidate was seen first.

    Three keys, in order: fewer expected experiments, then *more* resolved
    probability mass, then the template id.

    The middle key is nearly redundant and is here for the case where it is not.
    An unresolved branch is charged the ``horizon + 1`` floor, so a design that
    resolves less mass already pays for it in ``value`` -- which is why adding
    this key changes no measured opening on the twelve slice scenarios. What it
    covers is the exact tie: two designs whose expected lengths agree to
    :data:`VALUE_PLACES` while one of them resolves the truth on more paths.
    Settling that by template id would let the alphabet pick the less
    informative experiment, and nothing about the alphabet is a scientific
    reason.
    """
    return (round(value, VALUE_PLACES), -round(reached, VALUE_PLACES), str(template))


def _mass_on(
    belief: Mapping[HypothesisId, Probability], truth: Sequence[HypothesisId]
) -> float:
    """Return the belief's total mass on the hypotheses holding the truth."""
    return math.fsum(float(belief[node_id]) for node_id in sorted(truth))


def _outcome_distribution(
    template: ExperimentTemplateId, world: World
) -> tuple[float, ...]:
    """Return what the world -- not the belief -- says a design will return."""
    try:
        cells = tuple(float(value) for value in world[template])
    except KeyError as exc:
        raise InferenceError(
            f"the world has no distribution for design {template!r}; it covers "
            f"{sorted(world)!r}"
        ) from exc
    total = math.fsum(cells)
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise InferenceError(
            f"the world's distribution over {template!r} sums to {total!r}; an "
            f"outcome distribution must be normalised before a policy is "
            f"measured against it"
        )
    return cells


def oracle_policy_length(
    scenario: ScenarioId,
    templates: Sequence[ExperimentTemplateId],
    prior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
    *,
    truth: Sequence[HypothesisId],
    world: World,
    horizon: int = DEFAULT_HORIZON,
    rollouts: int = GREEDY_ROLLOUTS,
    seed: Seed = DEFAULT_SEED,
) -> OraclePolicyLength:
    """Return what an optimal policy needs to identify ``truth``.

    ``predict`` is what each hypothesis says a design will return, and drives
    the Bayes update; ``world`` is what the scenario's environment actually
    returns, and drives the expectation. Where the truth is in the hypothesis set
    and carries no nuisance the two agree cell for cell, which is the
    well-specified case; where they differ, the difference is the scenario.

    ``truth`` names the hypotheses holding the true structure -- plural, because
    two hypotheses may hold one structure under different names and the mass on
    a structure is the sum over them, and possibly **empty**, which is how an
    out-of-library truth (S11) is stated: no hypothesis holds it, so no policy
    over this set identifies it and :attr:`~OraclePolicyLength.identifiable` is
    ``False``.

    Guarantees the returned value is a pure function of its arguments: the
    dynamic programme is deterministic and the greedy rollouts are drawn from a
    generator seeded by ``seed``. Guarantees, too, that
    :attr:`~OraclePolicyLength.expected_steps` is never an over-estimate of the
    optimal expected length -- an unresolved path is charged the least it could
    cost, so a short horizon understates rather than flatters.
    """
    if not templates:
        raise MalformedDesignError(
            "an oracle policy has nothing to choose between; a scenario must "
            "offer at least one design"
        )
    if horizon < 1:
        raise MalformedDesignError(
            f"a horizon of {horizon} searches no experiment at all; the oracle "
            f"length of a scenario is at least one"
        )
    missing = sorted(set(truth) - set(prior))
    if missing:
        raise InferenceError(
            f"the truth names hypotheses {missing!r} the prior does not hold; a "
            f"policy cannot be measured against a target it cannot express"
        )

    ordered = tuple(templates)
    memo: dict[
        tuple[tuple[int, ...], int], tuple[float, float, ExperimentTemplateId]
    ] = {}

    def value(
        belief: Mapping[HypothesisId, Probability], depth: int
    ) -> tuple[float, float, ExperimentTemplateId | None]:
        """Return (expected *remaining* steps, reach probability, next design).

        A belief still short of the threshold at the horizon is charged one
        further experiment -- the least any continuation could cost -- so the
        value returned for such a path is ``horizon + 1`` in total and is a
        lower bound rather than an estimate.
        """
        if _mass_on(belief, truth) > IDENTIFIED:
            return 0.0, 1.0, None
        if depth == horizon:
            return 1.0, 0.0, None
        key = (_belief_key(belief), depth)
        cached = memo.get(key)
        if cached is not None:
            return cached[0], cached[1], cached[2]

        best: tuple[float, float, ExperimentTemplateId] | None = None
        for template in ordered:
            cells = _outcome_distribution(template, world)
            steps = 0.0
            reached = 0.0
            for cell, probability in enumerate(cells):
                if probability <= 0.0:
                    continue
                after = update(belief, template, cell, predict)
                sub_steps, sub_reached, _ = value(after, depth + 1)
                steps += probability * sub_steps
                reached += probability * sub_reached
            candidate = (1.0 + steps, reached, template)
            # Fewer experiments, then more resolved mass, then the template id,
            # so the choice is the same in every process (the determinism
            # invariant). Compared through _choice_key rather than on the raw
            # floats, which read a difference of one unit in the last place as a
            # strict win.
            if best is None or _choice_key(*candidate) < _choice_key(*best):
                best = candidate
        assert best is not None  # ordered is non-empty, checked above
        memo[key] = best
        return best

    steps, reached, opening = value(prior, 0)
    prior_mass = _mass_on(prior, truth)
    greedy, completion = greedy_expected_steps(
        ordered,
        prior,
        predict,
        truth=truth,
        world=world,
        limit=max(horizon * 4, 8),
        rollouts=rollouts,
        seed=seed,
    )
    return OraclePolicyLength(
        scenario=scenario,
        horizon=horizon,
        expected_steps=steps,
        certain=math.isclose(reached, 1.0, rel_tol=0.0, abs_tol=1e-12),
        reach_probability=reached,
        greedy_expected_steps=greedy,
        greedy_completion=completion,
        evidence_bound=evidence_bound(ordered, prior, predict, truth=truth),
        truth_mass_prior=prior_mass,
        # A search that resolved no path has no optimum, so it has no opening to
        # name: every design scored the horizon's floor and the one returned is
        # whichever sorted first, not one the evidence preferred. What the field
        # has documented all along; it simply never returned it.
        first_design=opening if reached > 0.0 else None,
    )


def evidence_bound(
    templates: Sequence[ExperimentTemplateId],
    prior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
    *,
    truth: Sequence[HypothesisId],
) -> float:
    """Return the floor the prior odds and the sharpest available cell imply.

    Holding more than half the mass means out-weighing every rival at once, so
    it means out-weighing *each* rival -- a necessary condition, which is what
    makes a bound derived from it valid. Against one rival ``h`` the odds start
    at ``prior(truth)/prior(h)`` and each experiment multiplies them by at most
    ``max`` over designs and cells of ``p_truth(cell)/p_h(cell)``, whatever the
    policy and whatever the world returns. The rival needing the most steps
    under that most-favourable-possible arithmetic is the floor.

    Guarantees a lower bound on the optimal expected length under *any* policy,
    with no horizon behind it: it assumes every experiment returns the single
    most discriminating cell available, which no world obliges. ``inf`` when the
    truth carries no prior mass, or when some rival is nowhere out-weighed --
    both of which mean no evidence can identify the truth at all.
    """
    mass = _mass_on(prior, truth)
    if mass <= 0.0:
        return math.inf
    if mass > IDENTIFIED:
        return 0.0
    wanted = frozenset(truth)
    steps = 0.0
    for rival in sorted(prior):
        if rival in wanted or prior[rival] <= 0.0:
            continue
        deficit = math.log2(float(prior[rival]) / mass)
        if deficit <= 0.0:
            continue
        best = -math.inf
        for template in templates:
            mine = predict(sorted(wanted)[0], template).cells
            theirs = predict(rival, template).cells
            best = max(
                best,
                max(
                    math.log2(ours / other)
                    for ours, other in zip(mine, theirs, strict=True)
                ),
            )
        if best <= 0.0:
            return math.inf
        steps = max(steps, deficit / best)
    return steps


def greedy_expected_steps(
    templates: Sequence[ExperimentTemplateId],
    prior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
    *,
    truth: Sequence[HypothesisId],
    world: World,
    limit: int,
    rollouts: int = GREEDY_ROLLOUTS,
    seed: Seed = DEFAULT_SEED,
) -> tuple[float | None, float]:
    """Return one-step-greedy's mean length by rollout, and how often it finished.

    The mean is over the rollouts that identified the truth within ``limit``
    experiments, and is ``None`` when none did. The second value is the fraction
    that identified it.

    Both are needed and neither is enough. A mean over the finishers alone is
    conditional -- it says what greedy costs *when it works* -- and would flatter
    a policy that works rarely; a mean with the failures charged at the limit
    would understate the cost of myopia in exactly the cases where it is largest.
    Only when the fraction is one is the mean an unconditional expected length,
    and only then is it an upper bound on the optimum.

    Reporting ``None`` at the first failed rollout, which an earlier version did,
    was worse than either: at two thousand rollouts it turned "succeeds 999 times
    in a thousand" into "does not succeed". It was caught by V1 recovering
    scenario S12 on a real run that this function called unreachable.

    Guarantees reproducibility: outcomes are drawn from a
    :class:`numpy.random.Generator` seeded by ``seed`` alone, and greedy's
    choices are a deterministic function of the outcomes it saw.
    """
    generator = np.random.default_rng(int(seed))
    lengths: list[int] = []
    for _ in range(rollouts):
        belief: Mapping[HypothesisId, Probability] = prior
        for step in range(1, limit + 1):
            if _mass_on(belief, truth) > IDENTIFIED:
                lengths.append(step - 1)
                break
            template = select(templates, belief, predict).template
            cells = _outcome_distribution(template, world)
            cell = int(generator.choice(len(cells), p=np.asarray(cells)))
            belief = update(belief, template, cell, predict)
        else:
            if _mass_on(belief, truth) > IDENTIFIED:
                lengths.append(limit)
    if not lengths:
        return None, 0.0
    return math.fsum(lengths) / len(lengths), len(lengths) / rollouts
