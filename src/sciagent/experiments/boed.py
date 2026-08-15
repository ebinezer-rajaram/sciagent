"""One-step-greedy Bayesian optimal experimental design (SPEC §6.6, item 8).

This is variant **V1** of SPEC §5's baseline suite -- "BOED-only over the closed
set, one-step greedy" -- and the selection half of **V7**, where an LLM proposes
and revises structure and this module decides what to do next. It is the thing
the agent has to beat, so A24 exists to show it is a fair opponent rather than a
straw man.

What is maximised
-----------------

The expected information gain of a design is the mutual information between the
hypothesis and the outcome it would return::

    EIG(d) = H(P) - E_y[ H(P | y) ] = H(Y_d) - sum_h p(h) H(Y_d | h)

Both readings are the same number. The first is the one the name describes --
how much the posterior sharpens, averaged over outcomes -- and the second is the
one computed here, because it costs one entropy per hypothesis plus one for the
mixture, where the first costs a full Bayes update per outcome cell. The
identity is exact, so nothing is given up by preferring the cheap side; it is
checked directly in ``tests/test_boed.py`` rather than taken on faith.

Everything is in bits, matching :mod:`sciagent.inference.entropy` and the prefix
code that defines the prior.

Two beliefs, deliberately
-------------------------

A greedy policy needs a belief to score designs against, and it needs one after
every outcome. That belief is *planning* state: it is what :func:`update`
maintains, it lives for the length of a plan, and it is discarded. The
investigation's posterior of record remains the engine's, derived from the
registry by :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.posterior`
over experiments that were actually registered and charged.

The two agree -- one Bayes step on a cell probability is exactly what recording
that experiment does to the posterior -- and ``tests/test_boed.py`` measures the
agreement rather than asserting it. Keeping them separate is what lets a policy
be scored over hypothetical outcomes without a hypothetical experiment reaching
the record.

Where the numbers come from
---------------------------

No number here is ever supplied by a caller. A design's score is a function of
the predictive distributions and the posterior, both of which are framework
state, and the ranking is a total order fixed by
:func:`rank`. :func:`plan` is the entry point a research system should use: it
reads both the posterior and the predictive off the engine, so a system that
drives BOED has no argument through which a score could be authored.
:func:`greedy` takes them separately because A24 must drive the same policy from
closed-form distributions with no engine in existence, and it is framework code
calling framework code.

Determinism
-----------

Selection is a total order: descending gain, ties broken by ascending template
id. Two designs with numerically equal gain therefore resolve the same way in
every process, which the determinism invariant requires and floating-point
equality makes a live concern -- symmetric designs over a symmetric belief tie
exactly, not approximately.

``CompareCandidates``
---------------------

SPEC §4.4's sixth operation is realised here, by :func:`compare`, and it is
realised as *selection*. "Score candidate defects against each other" is
answered by restricting the belief to those candidates and ranking the designs
that would separate them. It measures nothing, so it stays without an executor
path: it registers no row, charges no budget and returns no
:data:`~sciagent.inference.binning.DiagnosticVector`.

What this is not
----------------

One step. SPEC §13 lists two-step-lookahead BOED in the backlog at freeze, and
research question R3 -- whether an LLM has headroom above lookahead, or merely
substitutes for its depth -- depends on that arriving later, not now.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from sciagent.core.edits import Defect
from sciagent.core.errors import (
    InferenceError,
    MalformedDesignError,
    UnknownHypothesisError,
)
from sciagent.core.types import (
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    Probability,
)
from sciagent.experiments.dsl import CompareCandidates, defect_key
from sciagent.inference.empirical import EmpiricalTableEngine
from sciagent.inference.entropy import entropy_bits, entropy_standard_error

__all__ = [
    "InformationGain",
    "OutcomeSource",
    "Predictive",
    "PredictiveSource",
    "Step",
    "candidate_hypotheses",
    "compare",
    "expected_information_gain",
    "greedy",
    "plan",
    "rank",
    "restrict",
    "select",
    "table_predictive",
    "update",
]


# --------------------------------------------------------------------------
# What a policy is given
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Predictive:
    """What one hypothesis says one design will return, over its outcome cells.

    Guarantees nothing about where the numbers came from, which is the point: a
    finite empirical table and a closed-form distribution are both expressible,
    and A24 compares a policy driven by one against the same policy driven by the
    other.

    ``samples`` is how many replicates stand behind ``cells``, and is
    :data:`math.inf` when the distribution is exact rather than estimated. It
    enters the reported :attr:`InformationGain.standard_error` and nothing else,
    so an exact source reports an error bar of zero and an estimated one reports
    the Monte Carlo error it actually carries.
    """

    cells: tuple[float, ...]
    samples: float = math.inf

    def __post_init__(self) -> None:
        if self.samples <= 0.0 or math.isnan(self.samples):
            raise InferenceError(
                f"a predictive must stand on a positive number of samples, got "
                f"{self.samples!r}; use math.inf for a distribution known in "
                f"closed form"
            )


#: What a hypothesis predicts a design will return. The framework's own view of
#: the environment, in the same shape as
#: :data:`~sciagent.inference.interface.Simulator`: injected, so that selection
#: is domain-independent while still being about a real environment.
type PredictiveSource = Callable[[HypothesisId, ExperimentTemplateId], Predictive]

#: Carries out the design chosen at step ``i`` and returns the outcome cell it
#: landed in. The step index is passed so that a caller can seed an execution by
#: position without BOED knowing how seeding works.
type OutcomeSource = Callable[[int, ExperimentTemplateId], int]


@dataclass(frozen=True, slots=True)
class InformationGain:
    """One design's expected information gain, and the two terms it is made of.

    ``bits`` is ``marginal - conditional``. Both terms are published because
    their difference is small next to either of them on an uninformative design,
    and a gain reported alone cannot be audited.
    """

    template: ExperimentTemplateId
    bits: float
    marginal: float
    """``H(Y)``: how uncertain the outcome is before knowing the hypothesis."""

    conditional: float
    """``sum_h p(h) H(Y | h)``: how uncertain it remains after knowing it."""

    standard_error: float
    """Monte Carlo error of ``bits``, zero for an exact predictive source.

    Not clamped, and neither is ``bits``: mutual information is non-negative in
    exact arithmetic but its estimate from a finite table is not, and a small
    negative gain is a true report that a design resolves nothing.
    """


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a greedy plan: what was chosen, what came back, what it bought."""

    template: ExperimentTemplateId
    expected: float
    """The gain :func:`select` expected, in bits, at the moment of choosing."""

    cell: int
    realised: float
    """``H`` before minus ``H`` after, in bits, under the planning belief.

    Negative whenever the outcome was one the belief did not favour, which is
    ordinary: an expectation is met on average and not on every draw.
    """

    posterior: FrozenDict[HypothesisId, Probability]
    """The planning belief *after* this step."""


# --------------------------------------------------------------------------
# Scoring one design
# --------------------------------------------------------------------------


def _weights(
    posterior: Mapping[HypothesisId, Probability],
) -> tuple[tuple[HypothesisId, float], ...]:
    """Return the hypotheses carrying mass, in a fixed order, with their weights.

    Zero-mass hypotheses are dropped rather than carried: they contribute
    nothing to either entropy term, and dropping them keeps a rejected
    hypothesis out of the mixture without a second notion of "live".
    """
    total = math.fsum(posterior[key] for key in sorted(posterior))
    if not math.isfinite(total) or not math.isclose(
        total, 1.0, rel_tol=1e-9, abs_tol=1e-12
    ):
        raise InferenceError(
            f"a belief must be normalised before it can score a design, got a "
            f"total of {total!r}"
        )
    live = tuple(
        (key, float(posterior[key]))
        for key in sorted(posterior)
        if posterior[key] > 0.0
    )
    if not live:
        raise InferenceError(
            "no hypothesis carries mass; there is nothing a design could resolve"
        )
    return live


def expected_information_gain(
    template: ExperimentTemplateId,
    posterior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
) -> InformationGain:
    """Return what one design is expected to teach, in bits.

    Guarantees the reported ``bits`` is ``H(Y) - sum_h p(h) H(Y | h)`` over the
    design's outcome cells, summed by :func:`math.fsum` over hypotheses in sorted
    order, so the value does not depend on the mapping's iteration order.

    Raises :class:`~sciagent.core.errors.InferenceError` if the belief is not
    normalised, or if the source returns predictives of differing length -- two
    hypotheses disagreeing about how many cells a design has is a
    discretisation fault, and averaging over it would produce a confident number
    from incoherent inputs.
    """
    live = _weights(posterior)
    predictives = tuple(predict(hypothesis, template) for hypothesis, _ in live)
    widths = {len(p.cells) for p in predictives}
    if len(widths) != 1:
        raise InferenceError(
            f"design {template!r} has predictives of {sorted(widths)!r} cells "
            f"across {len(live)} hypotheses; they must share one outcome space"
        )
    cells = widths.pop()

    # entropy_bits checks normalisation and non-negativity, so every predictive
    # is validated by being used rather than by a separate pass.
    conditionals = tuple(entropy_bits(p.cells) for p in predictives)
    marginal = [
        math.fsum(
            weight * predictives[index].cells[cell]
            for index, (_, weight) in enumerate(live)
        )
        for cell in range(cells)
    ]
    marginal_bits = entropy_bits(marginal)
    conditional_bits = math.fsum(
        weight * conditionals[index] for index, (_, weight) in enumerate(live)
    )

    # The mixture's variance is sum_h w^2 Var(q_h), so it stands on an effective
    # sample size of 1 / sum_h (w^2 / M_h) -- larger than any single row, which
    # is why the marginal term is the better resolved of the two. All-exact
    # sources give a zero sum and therefore an infinite effective size.
    inverse = math.fsum(
        weight * weight / predictives[index].samples
        for index, (_, weight) in enumerate(live)
    )
    marginal_samples = math.inf if inverse <= 0.0 else 1.0 / inverse
    variance = math.fsum(
        (
            entropy_standard_error(marginal, marginal_samples) ** 2,
            *(
                (weight * entropy_standard_error(predictives[index].cells, p.samples))
                ** 2
                for index, ((_, weight), p) in enumerate(
                    zip(live, predictives, strict=True)
                )
            ),
        )
    )
    return InformationGain(
        template=template,
        bits=marginal_bits - conditional_bits,
        marginal=marginal_bits,
        conditional=conditional_bits,
        standard_error=math.sqrt(variance),
    )


def rank(
    templates: Sequence[ExperimentTemplateId],
    posterior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
) -> tuple[InformationGain, ...]:
    """Return every design scored, best first.

    Guarantees a total order: descending gain, ties broken by ascending template
    id. Exact ties are not hypothetical -- symmetric designs under a symmetric
    belief produce them -- so the tiebreak is what makes selection reproducible
    rather than a property of the sort's stability.

    Raises :class:`~sciagent.core.errors.MalformedDesignError` on an empty or
    duplicated design set.
    """
    if not templates:
        raise MalformedDesignError(
            "cannot select from an empty design set; a policy with nothing to "
            "choose between has not been given a decision to make"
        )
    if len(set(templates)) != len(templates):
        raise MalformedDesignError(
            f"the design set repeats a template: {sorted(templates)!r}. Scoring "
            f"one design twice would let it win a tiebreak against itself"
        )
    gains = [
        expected_information_gain(template, posterior, predict)
        for template in templates
    ]
    return tuple(sorted(gains, key=lambda gain: (-gain.bits, str(gain.template))))


def select(
    templates: Sequence[ExperimentTemplateId],
    posterior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
) -> InformationGain:
    """Return the design with the greatest expected information gain."""
    return rank(templates, posterior, predict)[0]


# --------------------------------------------------------------------------
# Believing what came back
# --------------------------------------------------------------------------


def update(
    posterior: Mapping[HypothesisId, Probability],
    template: ExperimentTemplateId,
    cell: int,
    predict: PredictiveSource,
) -> FrozenDict[HypothesisId, Probability]:
    """Return the planning belief after one design returned one outcome cell.

    Guarantees the arithmetic of
    :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.posterior`: log
    space, a maximum shift before exponentiating, :func:`math.fsum` in sorted
    order. Recording the same result against the same engine therefore produces
    the same distribution to floating-point tolerance, which
    ``tests/test_boed.py`` measures.

    Hypotheses at zero stay at zero. A belief that has excluded a hypothesis
    cannot be argued back into it by an outcome, which is what makes rejection
    the evidential judgement the engine treats it as.
    """
    live = _weights(posterior)
    logs: dict[HypothesisId, float] = {}
    for hypothesis, weight in live:
        cells = predict(hypothesis, template).cells
        if not 0 <= cell < len(cells):
            raise InferenceError(
                f"design {template!r} returned cell {cell}, which is outside the "
                f"{len(cells)} cells {hypothesis!r} predicts over"
            )
        probability = cells[cell]
        if probability <= 0.0:
            raise InferenceError(
                f"{hypothesis!r} assigns zero probability to cell {cell} of "
                f"{template!r}; a finite simulation budget must not be able to "
                f"rule a hypothesis out outright, so a predictive is required to "
                f"be strictly positive"
            )
        logs[hypothesis] = math.log(weight) + math.log(probability)
    # One sort, not four. `logs` and `shifted` share a key set, so the three
    # passes below were re-sorting the same list; `posterior` is sorted once for
    # the result. The arithmetic is untouched -- the same values are exponentiated
    # and folded in the same sorted order -- and this is on the update every
    # planning search calls once per node per outcome cell.
    live_order = sorted(logs)
    ceiling = max(logs[key] for key in live_order)
    shifted = {key: math.exp(logs[key] - ceiling) for key in live_order}
    total = math.fsum(shifted[key] for key in live_order)
    return FrozenDict[HypothesisId, Probability](
        {key: Probability(shifted.get(key, 0.0) / total) for key in sorted(posterior)}
    )


def restrict(
    posterior: Mapping[HypothesisId, Probability],
    to: Iterable[HypothesisId],
) -> FrozenDict[HypothesisId, Probability]:
    """Return the belief conditioned on the truth lying in ``to``.

    Everything outside ``to`` goes to zero and the rest is renormalised. This is
    a *question*, not a judgement: it asks which design best separates these
    candidates, and it leaves the engine's posterior untouched.
    """
    chosen = frozenset(to)
    missing = sorted(chosen - frozenset(posterior))
    if missing:
        raise UnknownHypothesisError(
            f"cannot restrict a belief to {missing!r}: it holds {sorted(posterior)!r}"
        )
    total = math.fsum(posterior[key] for key in sorted(chosen))
    if total <= 0.0:
        raise InferenceError(
            f"the belief puts no mass on {sorted(chosen)!r}, so there is no "
            f"distribution to condition on"
        )
    return FrozenDict[HypothesisId, Probability](
        {
            key: Probability(posterior[key] / total if key in chosen else 0.0)
            for key in sorted(posterior)
        }
    )


# --------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------


def greedy(
    templates: Sequence[ExperimentTemplateId],
    posterior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
    observe: OutcomeSource,
    *,
    steps: int,
) -> tuple[Step, ...]:
    """Run one-step-greedy selection for ``steps`` experiments.

    At each step the design of greatest expected gain is chosen, carried out
    through ``observe``, and the planning belief updated. Designs are chosen with
    replacement: a repeated design is a fresh execution under a fresh seed and
    therefore genuinely informative, and forbidding repeats would be a budget
    policy rather than an inference one.

    Guarantees the returned trajectory is a pure function of its arguments, so
    two runs of the same policy over the same outcomes are identical.

    ``posterior`` is framework state -- see this module's docstring on where
    numbers come from. :func:`plan` is the entry point that takes it off an
    engine rather than off a caller.
    """
    if steps < 1:
        raise MalformedDesignError(
            f"a plan of {steps} step(s) performs no experiment; ask for at least one"
        )
    belief: Mapping[HypothesisId, Probability] = posterior
    trajectory: list[Step] = []
    for index in range(steps):
        best = select(templates, belief, predict)
        before = entropy_bits([belief[key] for key in sorted(belief)])
        cell = observe(index, best.template)
        after = update(belief, best.template, cell, predict)
        trajectory.append(
            Step(
                template=best.template,
                expected=best.bits,
                cell=cell,
                realised=before - entropy_bits([after[key] for key in sorted(after)]),
                posterior=after,
            )
        )
        belief = after
    return tuple(trajectory)


def plan(
    engine: EmpiricalTableEngine,
    templates: Sequence[ExperimentTemplateId],
    observe: OutcomeSource,
    *,
    steps: int,
) -> tuple[Step, ...]:
    """Run :func:`greedy` against an engine, taking both beliefs off the engine.

    The entry point a research system should use. Nothing a caller supplies can
    become a score: the belief comes from
    :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.posterior` and the
    predictive from the engine's table, so a system driving BOED has no argument
    through which a number could be authored (SPEC's second invariant).
    """
    return greedy(
        templates, engine.posterior(), table_predictive(engine), observe, steps=steps
    )


def table_predictive(engine: EmpiricalTableEngine) -> PredictiveSource:
    """Return the predictive an empirical table supports.

    The cell probabilities are
    :meth:`~sciagent.inference.empirical.EmpiricalTable.probabilities` --
    Krichevsky-Trofimov, the same estimator the engine's likelihood uses, so
    :func:`update` and the engine cannot come to disagree about what an outcome
    is worth.

    The table is read on every call rather than captured, so a hypothesis
    admitted mid-plan by
    :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.expand` is visible
    to the next selection.
    """

    def predict(hypothesis: HypothesisId, template: ExperimentTemplateId) -> Predictive:
        table = engine.table
        return Predictive(
            cells=table.probabilities(engine.program_edit(hypothesis), template),
            samples=float(table.replicates),
        )

    return predict


# --------------------------------------------------------------------------
# CompareCandidates (SPEC §4.4)
# --------------------------------------------------------------------------


def candidate_hypotheses(
    operation: CompareCandidates,
    program_edit: Mapping[HypothesisId, Defect],
) -> tuple[HypothesisId, ...]:
    """Return the hypotheses whose structure the operation names, in a fixed order.

    Matching is by :func:`~sciagent.experiments.dsl.defect_key`, the canonical
    rendering of an edit set, so two hypotheses proposed under different names
    for the same structure are one candidate and are reported as such. A
    candidate no hypothesis holds raises: comparing against a structure the
    belief has never scored would silently drop it from the comparison.
    """
    by_structure: dict[str, list[HypothesisId]] = {}
    for hypothesis in sorted(program_edit):
        by_structure.setdefault(defect_key(program_edit[hypothesis]), []).append(
            hypothesis
        )
    found: list[HypothesisId] = []
    for defect in operation.candidates:
        key = defect_key(defect)
        if key not in by_structure:
            raise UnknownHypothesisError(
                f"no hypothesis holds the structure {key!r}; the belief covers "
                f"{sorted(by_structure)!r}. Expand it before comparing against it"
            )
        found.extend(by_structure[key])
    return tuple(sorted(set(found)))


def compare(
    operation: CompareCandidates,
    templates: Sequence[ExperimentTemplateId],
    posterior: Mapping[HypothesisId, Probability],
    predict: PredictiveSource,
    *,
    program_edit: Mapping[HypothesisId, Defect],
) -> tuple[InformationGain, ...]:
    """Answer a ``CompareCandidates`` by ranking the designs that separate them.

    SPEC §4.4's sixth operation, realised as selection rather than as execution:
    it returns which experiment would best tell the named candidates apart, and
    it performs none of them. See this module's docstring for why it has no
    executor path.

    Guarantees the engine's posterior is not touched. The ranking is computed
    against the belief :func:`restrict`\\ ed to the candidates, which is the
    question "given that one of these is right, what separates them" and not a
    claim that one of them is.
    """
    return rank(
        templates,
        restrict(posterior, candidate_hypotheses(operation, program_edit)),
        predict,
    )
