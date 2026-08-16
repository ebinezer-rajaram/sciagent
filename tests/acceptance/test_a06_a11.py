"""Acceptance tests A6-A11 (SPEC §6.2): the posterior engine.

One test per criterion, named for it. These are the contract for backlog item 6,
and SPEC §11 is explicit that nothing proceeds until they pass: "a weak posterior
engine must never be able to masquerade as weak agent performance".

Three readings had to be settled before these could be written, and each is
recorded in ``docs/DECISIONS.md`` as well as here.

**A6 versus A7.** Both compare an estimate against a known value at the two
standard error level, and taken literally they would be the same test twice. They
are separated here by what they hold fixed: A6 fixes the *standard* -- agreement
with a closed-form likelihood, on programmes whose sampling distribution is
exactly known -- and A7 fixes the *number*, the 93% empirical coverage of the
reported error bar, plus the entropy clause. A6 additionally checks that the mean
deviation is small against one standard error, which is a bias check that no
coverage statement makes.

That bias check is deliberately loose, at half a standard error rather than at
the two standard errors of the mean that 500 trials would support. Estimating a
*log* likelihood from a finite sample carries an unavoidable Jensen term of order
``-(1 - p) / (2 M p)``; it shrinks with the replicate count but never vanishes,
and tightening the tolerance would only measure it more precisely, not remove it.
The measured size is in ``docs/DECISIONS.md``.

**A8's prior.** A8 asks for calibration over scenarios with known ground truth.
Calibration in the Bayesian sense requires the scenarios to be drawn from the
prior the posterior uses, and SPEC §0 forbids exactly that: the structural
complexity prior is a statement about parsimony, "independent of how often each
defect type happens to appear in the benchmark", and conflating the two "would
have made the posterior an artefact of scenario sampling". Under that prior the
null hypothesis costs one bit and every mechanism twenty-three or more, so a
benchmark drawn from it would be 99.9999% nulls and would measure nothing.

A8 therefore measures the calibration of the *likelihood*, over a balanced
benchmark, using :meth:`EmpiricalTableEngine.log_likelihood_total` and a flat
prior. That is the configuration in which any miscalibration is attributable to
the estimator, which is what A8 exists to gate. The calibration error of the
deployed structural-prior posterior over the same balanced benchmark is measured
too and reported, so the size of SPEC §0's deliberate mismatch is a recorded
number rather than a later surprise.

**A8's "credible intervals".** The slice's posterior is over a finite hypothesis
set, where the analogue of a credible interval is a credible *set*: the smallest
set of hypotheses whose mass reaches the nominal level. Expected calibration
error is computed classwise, over every (hypothesis, probability) pair rather
than over the top-ranked one only. Top-1 calibration error on 200 scenarios
carries about 0.09 of pure binomial noise, so A8's 0.05 threshold would be
unmeasurable that way and would fail a perfectly calibrated engine.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from itertools import pairwise

import numpy as np
import pytest
from scipy import optimize, stats
from slice_tables import (
    CLOSED_SET,
    REPLICATES,
    STRUCTURE_NAMES,
    TABLE_SEED,
    slice_table,
)

from environments.pointproc import edit_grammar, mechanism_defect
from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import MIXTURE_OF_POISSON_2
from environments.pointproc.diagnostics import inter_arrival_times, mean_rate
from environments.pointproc.mechanisms import SIZE_EXCITATION, SIZE_MIXTURE
from environments.pointproc.outcomes import simulator, slice_templates
from environments.pointproc.program import REFERENCE_RATE, reference_program
from sciagent.core.conditions import Compare
from sciagent.core.edits import ChangeDistributionFamily, Defect
from sciagent.core.errors import TableError
from sciagent.core.program import stable_key
from sciagent.core.types import (
    EventLog,
    ExperimentId,
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    MetricName,
    MetricRef,
    Prediction,
    PredictionId,
    Probability,
    Seed,
)
from sciagent.hypothesis.graph import HypothesisGraph
from sciagent.inference.binning import Discretisation, OutcomeSpace
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    ExperimentTemplate,
    structure_key,
)
from sciagent.inference.entropy import miller_madow_entropy, plugin_entropy
from sciagent.inference.interface import DiagnosticVector, Simulator
from sciagent.registry.metrics import MetricRegistry, MetricSpec

GRAMMAR = edit_grammar()
METRICS: MetricRegistry = metric_registry()
TEMPLATES = slice_templates()


def _prediction(name: str) -> Prediction:
    """Return a refutable prediction, so the graph will admit the hypothesis."""
    return Prediction(
        id=PredictionId(f"{name}/dispersion"),
        hypothesis_id=HypothesisId(name),
        diagnostic=METRICS.spec("inter_arrival_dispersion").ref,
        condition=Compare(">", 1.3) if name != "null" else Compare("<=", 1.3),
        under=ExperimentTemplateId("query:inter_arrival_dispersion"),
        refutation=Compare("<=", 1.3) if name != "null" else Compare(">", 1.3),
    )


@lru_cache(maxsize=1)
def closed_graph() -> HypothesisGraph:
    """Return a hypothesis graph over the slice's closed set."""
    graph = HypothesisGraph.empty(GRAMMAR, METRICS)
    for name in STRUCTURE_NAMES:
        graph = graph.propose(
            HypothesisId(name),
            program_edit=CLOSED_SET[name],
            predictions=(_prediction(name),),
            rationale=f"{name} as specified in SPEC §4.2",
        )
    return graph


def scenario_seed(kind: str, index: int, template: ExperimentTemplate) -> Seed:
    """Return the seed of one experiment in one scenario.

    Namespaced away from ``table/...``, which is what
    :func:`sciagent.inference.empirical.replicate_seed` derives its seeds under.
    An observation drawn under a table replicate's seed would be one of the
    table's own draws, and its likelihood would be evaluated against a sample it
    belongs to.
    """
    return Seed(stable_key(f"scenario/{kind}/{index}/{template.id}") % (1 << 63))


def observe(
    defect: Defect, kind: str, index: int, simulate: Simulator
) -> tuple[tuple[ExperimentId, ExperimentTemplate, DiagnosticVector], ...]:
    """Carry out one experiment per template, each under its own seed.

    Separate seeds are what make the experiments independent given the
    hypothesis, which is what lets the engine multiply their likelihoods without
    an approximation.
    """
    return tuple(
        (
            ExperimentId(f"{kind}/{index}/{template.id}"),
            template,
            simulate(defect, template, scenario_seed(kind, index, template)),
        )
        for template in TEMPLATES
    )


def engine_over(
    records: Sequence[tuple[ExperimentId, ExperimentTemplate, DiagnosticVector]],
    *,
    graph: HypothesisGraph | None = None,
    table: EmpiricalTable | None = None,
    simulate: Simulator | None = None,
) -> EmpiricalTableEngine:
    """Return an engine with ``records`` already recorded."""
    engine = EmpiricalTableEngine(
        graph if graph is not None else closed_graph(),
        table if table is not None else slice_table(),
        simulate=simulate,
    )
    for experiment, template, result in records:
        engine.record(experiment, template, result)
    return engine


#: 200 correctly-specified scenarios: ground truth cycles through the closed set,
#: so each mechanism appears exactly 40 times. A balanced assignment rather than a
#: sampled one, because multinomial noise in the class counts would widen every
#: interval below for no gain -- what is under test is the estimator, not the
#: benchmark's own sampling.
BENCHMARK_SIZE = 200


@lru_cache(maxsize=1)
def benchmark() -> tuple[
    tuple[str, tuple[tuple[ExperimentId, ExperimentTemplate, DiagnosticVector], ...]],
    ...,
]:
    """Return the DEV benchmark: ground truth plus its observed experiments."""
    simulate = simulator(GRAMMAR)
    return tuple(
        (
            STRUCTURE_NAMES[index % len(STRUCTURE_NAMES)],
            observe(
                CLOSED_SET[STRUCTURE_NAMES[index % len(STRUCTURE_NAMES)]],
                "dev",
                index,
                simulate,
            ),
        )
        for index in range(BENCHMARK_SIZE)
    )


def flat_prior_posterior(engine: EmpiricalTableEngine) -> Mapping[HypothesisId, float]:
    """Return the posterior the engine's likelihoods imply under a flat prior.

    The engine publishes :meth:`log_likelihood_total` and this function does the
    normalisation; nothing here writes a number into the framework. See the
    module docstring for why A8 is measured this way.
    """
    totals = {node_id: engine.log_likelihood_total(node_id) for node_id in engine.live}
    ceiling = max(totals[node_id] for node_id in sorted(totals))
    weights = {
        node_id: math.exp(totals[node_id] - ceiling) for node_id in sorted(totals)
    }
    mass = math.fsum(weights[node_id] for node_id in sorted(weights))
    return {node_id: weights[node_id] / mass for node_id in sorted(weights)}


# ==========================================================================
# A6  Likelihood estimation
# ==========================================================================

#: Trials behind A6 and A7. Each is an independent table over an independent
#: simulation budget, so the 500 estimates are independent in the way both
#: criteria assume.
TRIALS = 500

#: Replicates per trial table. Small on purpose: the coverage claim A7 makes is
#: hardest to satisfy when the Monte Carlo error is large, so testing it at a
#: fifth of the slice's replicate count is the demanding direction.
TRIAL_REPLICATES = 200

#: Cells per analytic outcome space, placed at the exact octiles of the law under
#: test. Equal-probability bins make the Krichevsky-Trofimov estimator exactly
#: unbiased -- its shrinkage is towards the uniform distribution over cells, which
#: is the true one here -- so what A6 measures is the Monte Carlo behaviour of the
#: estimator and not the smoothing choice.
ANALYTIC_CELLS = 8

#: Two events is all the analytic cases need: the first inter-arrival gap, and
#: the realised mean rate over a span that is the sum of two gaps.
ANALYTIC_EVENTS = 2

_FIRST_GAP = MetricName("first_inter_arrival")
_MEAN_RATE = MetricName("mean_rate")
_ANALYTIC_VERSION = "a6/1.0.0"


def first_inter_arrival(log: EventLog) -> float:
    """Return the gap between the first two arrivals.

    Not a scientific diagnostic and not in the slice's catalogue. It is here
    because its sampling distribution is exactly known under both programmes A6
    names -- Exponential under a homogeneous Poisson process, a two-component
    hyperexponential under a Poisson mixture -- which is what "synthetic cases
    with analytically tractable likelihoods" asks for.
    """
    return float(inter_arrival_times(log)[0])


def analytic_registry() -> MetricRegistry:
    """Return the metric registry A6's synthetic cases are measured under."""
    return MetricRegistry.of(
        (
            MetricSpec(
                ref=MetricRef(name=_FIRST_GAP, version=_ANALYTIC_VERSION),
                compute=first_inter_arrival,
                low=0.0,
                high=math.inf,
            ),
            MetricSpec(
                ref=MetricRef(name=_MEAN_RATE, version=_ANALYTIC_VERSION),
                compute=mean_rate,
                low=0.0,
                high=math.inf,
            ),
        )
    )


def _mixture_parameters() -> tuple[float, float, float]:
    """Return ``(rate_low, rate_high, weight_high)`` of the Poisson mixture."""
    (edit,) = mechanism_defect("poisson_mixture")
    assert isinstance(edit, ChangeDistributionFamily)
    assert edit.family == MIXTURE_OF_POISSON_2
    return (
        edit.parameters["rate_low"],
        edit.parameters["rate_high"],
        edit.parameters["weight_high"],
    )


def reference_gap_cdf(value: float) -> float:
    """Exact CDF of one inter-arrival gap under the reference programme."""
    return float(stats.expon.cdf(value, scale=1.0 / REFERENCE_RATE))


def mixture_gap_cdf(value: float) -> float:
    """Exact CDF of one inter-arrival gap under the two-component mixture.

    The kernel draws the rate afresh for every event and then an exponential
    under it, so a single gap is hyperexponential:
    ``F(x) = 1 - (1 - w) exp(-rate_low x) - w exp(-rate_high x)``.
    """
    low, high, weight = _mixture_parameters()
    return float(
        1.0 - (1.0 - weight) * np.exp(-low * value) - weight * np.exp(-high * value)
    )


def reference_mean_rate_cdf(value: float) -> float:
    """Exact CDF of the realised mean rate over two reference arrivals.

    The span of two events is the sum of two independent Exponential gaps, so it
    is Gamma-distributed with shape 2, and the mean rate is ``2 / span``.
    """
    if value <= 0.0:
        return 0.0
    span = ANALYTIC_EVENTS / value
    return float(stats.gamma.sf(span, a=ANALYTIC_EVENTS, scale=1.0 / REFERENCE_RATE))


#: The three (structure, diagnostic) pairs whose likelihood is known in closed
#: form. SPEC A6 names homogeneous Poisson and the Poisson mixture; the third is
#: the same reference programme read through a second diagnostic, and costs
#: nothing because it shares the execution.
type _Cdf = Callable[[float], float]
ANALYTIC_CASES: tuple[tuple[str, str, MetricName, _Cdf], ...] = (
    ("homogeneous Poisson gap", "null", _FIRST_GAP, reference_gap_cdf),
    ("homogeneous Poisson rate", "null", _MEAN_RATE, reference_mean_rate_cdf),
    ("Poisson mixture gap", "poisson_mixture", _FIRST_GAP, mixture_gap_cdf),
)


def _quantile(cdf: _Cdf, level: float) -> float:
    """Return the value at which ``cdf`` reaches ``level``."""
    return float(optimize.brentq(lambda x: cdf(x) - level, 1e-12, 1e6, xtol=1e-14))


def equal_probability_edges(cdf: _Cdf, cells: int) -> tuple[float, ...]:
    """Return interior edges putting exactly ``1 / cells`` mass in every bin."""
    return tuple(_quantile(cdf, k / cells) for k in range(1, cells))


def analytic_template(
    label: str, metric: MetricName, cdf: _Cdf, registry: MetricRegistry
) -> ExperimentTemplate:
    """Return the equal-probability template for one analytic case.

    Identified by the *case*, not by the metric. Two cases can measure the same
    diagnostic under different programmes -- the first inter-arrival gap is
    Exponential under the reference and hyperexponential under the mixture -- and
    their equal-probability edges are then different. Keying a template by its
    metric alone would silently give one case the other's bins, and the resulting
    likelihoods would be wrong without anything raising.
    """
    spec = registry.spec(str(metric))
    return ExperimentTemplate(
        id=ExperimentTemplateId(f"analytic:{label.replace(' ', '_')}"),
        outcome=OutcomeSpace(
            axes=(
                Discretisation(
                    metric=spec.ref,
                    interior=equal_probability_edges(cdf, ANALYTIC_CELLS),
                    low=spec.low,
                    high=spec.high,
                ),
            )
        ),
        n_events=ANALYTIC_EVENTS,
    )


def analytic_simulator(registry: MetricRegistry) -> Simulator:
    """Return a simulator over ``registry``, for A6's synthetic templates."""
    reference = reference_program()
    compiled: dict[Defect, object] = {}
    cache: dict[tuple[Defect, int, int], EventLog] = {}

    def simulate(
        defect: Defect, template: ExperimentTemplate, seed: Seed
    ) -> DiagnosticVector:
        program = compiled.get(defect)
        if program is None:
            program = GRAMMAR.apply(reference, defect)
            compiled[defect] = program
        key = (defect, int(seed), template.n_events)
        log = cache.get(key)
        if log is None:
            log = program.execute(seed, template.n_events)  # type: ignore[attr-defined]
            cache.clear()
            cache[key] = log
        return tuple(
            registry.spec(str(metric.name)).compute(log)
            for metric in template.outcome.metrics
        )

    return simulate


@lru_cache(maxsize=1)
def analytic_trials() -> Mapping[str, tuple[tuple[float, float, float], ...]]:
    """Return ``(estimate, standard error, exact)`` per trial, per analytic case.

    Each trial is an independent table over its own simulation budget and an
    independent observation drawn from the true process, so the 500 triples of a
    case are independent. Shared by A6 and A7, which read different things off
    them: A6 whether the estimate is right, A7 whether the error bar is.
    """
    registry = analytic_registry()
    simulate = analytic_simulator(registry)
    templates = {
        label: analytic_template(label, metric, cdf, registry)
        for label, _, metric, cdf in ANALYTIC_CASES
    }
    exact = math.log(1.0 / ANALYTIC_CELLS)
    results: dict[str, list[tuple[float, float, float]]] = {
        label: [] for label, _, _, _ in ANALYTIC_CASES
    }
    ordered = tuple(templates[label] for label, _, _, _ in ANALYTIC_CASES)

    for trial in range(TRIALS):
        table, _ = EmpiricalTable.build(
            defects=[CLOSED_SET["null"], CLOSED_SET["poisson_mixture"]],
            templates=ordered,
            simulate=simulate,
            replicates=TRIAL_REPLICATES,
            seed=Seed(500_000 + trial),
        )
        for label, structure, _, _ in ANALYTIC_CASES:
            defect = CLOSED_SET[structure]
            template = templates[label]
            drawn = simulate(
                defect,
                template,
                Seed(stable_key(f"a6-observation/{label}/{trial}") % (1 << 63)),
            )
            estimate = table.estimate(defect, template, drawn)
            results[label].append(
                (estimate.log_likelihood, estimate.standard_error, exact)
            )
    return {label: tuple(rows) for label, rows in results.items()}


class TestA6LikelihoodEstimation:
    """A6: estimated log-likelihood agrees with the analytically exact value."""

    def test_a6_equal_probability_bins_are_exact(self) -> None:
        """The analytic discretisations really do carry 1/8 of the mass each.

        A6's whole standard is the exact cell probability, so the edges that
        define it are checked before anything is compared against them. Without
        this, an error in the edge solver would move the target rather than
        register as a failure.
        """
        registry = analytic_registry()
        for label, _, metric, cdf in ANALYTIC_CASES:
            edges = (
                analytic_template(label, metric, cdf, registry).outcome.axes[0].interior
            )
            spec = registry.spec(str(metric))
            boundaries = (spec.low, *edges, spec.high)
            for lower, upper in pairwise(boundaries):
                mass = (
                    cdf(upper) - cdf(lower)
                    if math.isfinite(upper)
                    else 1.0 - cdf(lower)
                )
                assert mass == pytest.approx(1.0 / ANALYTIC_CELLS, abs=1e-9)

    def test_a6_estimate_is_within_two_standard_errors_of_exact(self) -> None:
        """Across 500 trials the estimate sits within two reported SEs of exact."""
        for label, rows in analytic_trials().items():
            assert len(rows) == TRIALS
            within = sum(
                1
                for estimate, error, exact in rows
                if abs(estimate - exact) <= 2.0 * error
            )
            assert within / TRIALS >= 0.93, (
                f"{label}: only {within}/{TRIALS} estimates fell within two "
                f"standard errors of the analytically exact log-likelihood"
            )

    def test_a6_estimator_carries_no_material_bias(self) -> None:
        """The mean deviation is small against one standard error.

        Loose on purpose, at half a standard error. Estimating a log from a
        finite sample carries a Jensen term of order ``-(1 - p) / (2 M p)`` which
        is real, is reported in ``docs/DECISIONS.md``, and does not go away; a
        tighter threshold would measure that term rather than test the estimator.
        """
        for label, rows in analytic_trials().items():
            deviations = [estimate - exact for estimate, _, exact in rows]
            typical = math.fsum(error for _, error, _ in rows) / TRIALS
            mean_deviation = math.fsum(deviations) / TRIALS
            assert abs(mean_deviation) < 0.5 * typical, (
                f"{label}: mean deviation {mean_deviation:.4f} nats against a "
                f"typical standard error of {typical:.4f}"
            )


# ==========================================================================
# A7  Monte Carlo error
# ==========================================================================

#: Distributions whose entropy is known in closed form, for the Miller-Madow
#: arm. Each is a distribution over eight cells; the first is uniform, and the
#: rest are progressively more concentrated, so the set spans the range of
#: occupancies the correction has to behave over.
KNOWN_ENTROPY_CASES: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("uniform", tuple([1.0 / 8] * 8)),
    ("geometric", (0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625, 0.0078125, 0.0078125)),
    ("two-thirds", (0.4, 0.2, 0.1, 0.1, 0.075, 0.05, 0.05, 0.025)),
    ("near-degenerate", (0.9, 0.04, 0.02, 0.015, 0.01, 0.008, 0.005, 0.002)),
)

#: Samples per entropy estimate. Small enough that the plug-in bias is visible,
#: which is the point: a correction cannot be shown to work at a sample size
#: where the thing it corrects is invisible.
ENTROPY_SAMPLES = 200

#: Independent count vectors per case.
ENTROPY_REPEATS = 400


def _multinomial_counts(
    probabilities: Sequence[float], samples: int, key: str
) -> tuple[int, ...]:
    """Draw one count vector, through a generator derived by name."""
    rng = np.random.Generator(
        np.random.PCG64(np.random.SeedSequence(entropy=1, spawn_key=(stable_key(key),)))
    )
    draws = rng.choice(len(probabilities), size=samples, p=list(probabilities))
    return tuple(
        int(np.count_nonzero(draws == cell)) for cell in range(len(probabilities))
    )


class TestA7MonteCarloError:
    """A7: the reported error bar is right, and entropy is corrected."""

    def test_a7_reported_standard_error_has_nominal_coverage(self) -> None:
        """Across 500 repeats the exact value is inside +/- 2 SE at least 93%."""
        for label, rows in analytic_trials().items():
            covered = sum(
                1
                for estimate, error, exact in rows
                if abs(exact - estimate) <= 2.0 * error
            )
            assert covered / TRIALS >= 0.93, (
                f"{label}: reported standard errors covered the exact value in "
                f"{covered}/{TRIALS} repeats, below the 93% A7 requires"
            )

    def test_a7_standard_error_is_not_merely_wide(self) -> None:
        """Coverage is not bought by an error bar that covers everything.

        A standard error ten times too large would pass the coverage criterion
        and be useless. The realised spread of the estimates is compared against
        the reported error, and the two must agree to within a factor of 1.5.
        """
        for label, rows in analytic_trials().items():
            deviations = [estimate - exact for estimate, _, exact in rows]
            realised = float(np.std(deviations, ddof=1))
            reported = math.fsum(error for _, error, _ in rows) / TRIALS
            assert 1 / 1.5 <= reported / realised <= 1.5, (
                f"{label}: reported standard error {reported:.4f} against a "
                f"realised spread of {realised:.4f}"
            )

    def test_a7_miller_madow_removes_most_of_the_plug_in_bias(self) -> None:
        """The correction shrinks the bias, on distributions of known entropy."""
        for label, probabilities in KNOWN_ENTROPY_CASES:
            exact = -math.fsum(p * math.log2(p) for p in probabilities if p > 0)
            plugin = []
            corrected = []
            for repeat in range(ENTROPY_REPEATS):
                counts = _multinomial_counts(
                    probabilities, ENTROPY_SAMPLES, f"entropy/{label}/{repeat}"
                )
                plugin.append(plugin_entropy(counts).bits - exact)
                corrected.append(miller_madow_entropy(counts).bits - exact)
            plugin_bias = abs(math.fsum(plugin) / ENTROPY_REPEATS)
            corrected_bias = abs(math.fsum(corrected) / ENTROPY_REPEATS)
            assert corrected_bias < plugin_bias, (
                f"{label}: the correction did not reduce the bias "
                f"({corrected_bias:.5f} against {plugin_bias:.5f} bits)"
            )

    def test_a7_residual_entropy_bias_is_under_five_percent_of_separation(
        self,
    ) -> None:
        """Residual bias is under 5% of the between-hypothesis entropy spread.

        "Between-hypothesis separation" is read as the spread of the true
        entropies across the cases under test -- the scale on which an entropy
        difference has to be resolved for one hypothesis's predictive
        distribution to be told from another's, which is what backlog item 8's
        expected information gain is a difference of.
        """
        exact = {
            label: -math.fsum(p * math.log2(p) for p in probabilities if p > 0)
            for label, probabilities in KNOWN_ENTROPY_CASES
        }
        separation = max(exact.values()) - min(exact.values())
        assert separation > 0.0
        for label, probabilities in KNOWN_ENTROPY_CASES:
            deviations = [
                miller_madow_entropy(
                    _multinomial_counts(
                        probabilities, ENTROPY_SAMPLES, f"entropy/{label}/{repeat}"
                    )
                ).bits
                - exact[label]
                for repeat in range(ENTROPY_REPEATS)
            ]
            residual = abs(math.fsum(deviations) / ENTROPY_REPEATS)
            assert residual < 0.05 * separation, (
                f"{label}: residual bias {residual:.5f} bits is not under 5% of "
                f"the {separation:.4f} bit separation"
            )


# ==========================================================================
# A8  Posterior calibration
# ==========================================================================


def expected_calibration_error(
    pairs: Sequence[tuple[float, bool]], n_bins: int = 10
) -> float:
    """Return the classwise expected calibration error over ``pairs``.

    Each pair is a reported probability and whether the hypothesis it was
    reported for was the ground truth. Equal-width bins on ``[0, 1]``.
    """
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for probability, hit in pairs:
        buckets[min(int(probability * n_bins), n_bins - 1)].append((probability, hit))
    total = len(pairs)
    return math.fsum(
        len(bucket)
        / total
        * abs(
            math.fsum(1.0 for _, hit in bucket if hit) / len(bucket)
            - math.fsum(probability for probability, _ in bucket) / len(bucket)
        )
        for bucket in buckets
        if bucket
    )


def credible_set(
    posterior: Mapping[HypothesisId, float], mass: float
) -> frozenset[HypothesisId]:
    """Return the smallest set of hypotheses whose posterior mass reaches ``mass``.

    The discrete analogue of a credible interval. Ties are broken by hypothesis
    id so the set is a function of the posterior alone.
    """
    order = sorted(posterior, key=lambda node_id: (-posterior[node_id], node_id))
    chosen: list[HypothesisId] = []
    accumulated = 0.0
    for node_id in order:
        chosen.append(node_id)
        accumulated += posterior[node_id]
        if accumulated >= mass:
            break
    return frozenset(chosen)


@lru_cache(maxsize=1)
def calibration_posteriors() -> tuple[
    tuple[str, Mapping[HypothesisId, float], Mapping[HypothesisId, Probability]], ...
]:
    """Return, per benchmark scenario, the truth and both posteriors."""
    return tuple(
        (truth, flat_prior_posterior(engine), engine.posterior())
        for truth, records in benchmark()
        for engine in (engine_over(records),)
    )


class TestA8PosteriorCalibration:
    """A8: the posterior is calibrated. This gates every downstream metric."""

    def test_a8_expected_calibration_error_is_under_five_percent(self) -> None:
        """Classwise ECE over 200 scenarios is under 0.05."""
        pairs = [
            (float(posterior[node_id]), str(node_id) == truth)
            for truth, posterior, _ in calibration_posteriors()
            for node_id in sorted(posterior)
        ]
        assert len(pairs) == BENCHMARK_SIZE * len(STRUCTURE_NAMES)
        error = expected_calibration_error(pairs)
        assert error < 0.05, f"classwise expected calibration error {error:.4f}"

    @pytest.mark.parametrize("level", [0.5, 0.8, 0.9])
    def test_a8_credible_sets_achieve_nominal_coverage(self, level: float) -> None:
        """Credible sets contain the truth at least at their nominal level.

        The tolerance is three binomial standard errors at 200 scenarios, which
        is Monte Carlo error in the *benchmark*, not slack in the criterion. A
        discrete credible set is conservative -- the smallest set reaching the
        level usually overshoots it -- so the realised coverage should sit above
        nominal rather than at it.
        """
        posteriors = calibration_posteriors()
        covered = sum(
            1
            for truth, posterior, _ in posteriors
            if HypothesisId(truth) in credible_set(posterior, level)
        )
        realised = covered / len(posteriors)
        tolerance = 3.0 * math.sqrt(level * (1.0 - level) / len(posteriors))
        assert realised >= level - tolerance, (
            f"{level:.0%} credible sets covered the truth {realised:.1%} of the "
            f"time, below {level - tolerance:.1%}"
        )

    def test_a8_structural_prior_mismatch_is_measured_not_assumed(self) -> None:
        """The deployed posterior's calibration error over a balanced benchmark.

        SPEC §0 makes the structural prior deliberately independent of how often
        a defect appears, so on a balanced benchmark the deployed posterior is
        *expected* to be miscalibrated -- the null costs one bit and every
        mechanism twenty-three or more. This records how large that is rather
        than leaving it to be discovered later, and asserts only that the
        likelihood still dominates: the truth must remain the modal hypothesis at
        least as often as chance would give.
        """
        pairs = [
            (float(deployed[node_id]), str(node_id) == truth)
            for truth, _, deployed in calibration_posteriors()
            for node_id in sorted(deployed)
        ]
        error = expected_calibration_error(pairs)
        modal = sum(
            1
            for truth, _, deployed in calibration_posteriors()
            if max(sorted(deployed), key=lambda h: (deployed[h], h)) == truth
        )
        assert error >= 0.0
        assert modal / len(calibration_posteriors()) > 1.0 / len(STRUCTURE_NAMES), (
            f"the structural prior overwhelmed the evidence: the truth was modal "
            f"in only {modal}/{len(calibration_posteriors())} scenarios "
            f"(deployed ECE {error:.4f})"
        )


# ==========================================================================
# A9  Posterior predictive checks
# ==========================================================================

#: Nominal size of the check. A9 bounds the realised false-positive rate at twice
#: this.
ALPHA = 0.05

#: Scenarios per misspecified defect type.
MISSPECIFIED = 100

#: The defect types A9 reports power against. Neither is in the closed set the
#: engine holds, so both are genuine misspecifications; they are chosen to
#: bracket the range. ``size_mixture`` perturbs a component no arrival mechanism
#: touches and should be caught nearly always. ``size_excitation`` is scenario
#: S11's out-of-library mechanism, calibrated to the same operating point as the
#: four it hides among, and is expected to be caught rarely -- that is the
#: measured Stage A floor SPEC §6.2 says the LLM must not be credited with.
MISSPECIFICATIONS: tuple[tuple[str, Defect], ...] = (
    ("size_mixture", frozenset({SIZE_MIXTURE})),
    ("size_excitation", frozenset({SIZE_EXCITATION})),
)


#: The design SPEC §4.6's Stage A gate actually reads, named the way
#: ``slice_scenarios`` names it. Power measured over every template is an upper
#: bound no budgeted run attains; power measured over this one is what the gate
#: can do, and therefore what SPEC §6.2 means by the rate the LLM must not be
#: credited with.
PROBE_TEMPLATE = "query:size_gap_correlation"


def probe_of(
    records: Sequence[tuple[ExperimentId, ExperimentTemplate, DiagnosticVector]],
) -> ExperimentId:
    """Return the id of the Stage A reading among ``records``."""
    for experiment, template, _ in records:
        if str(template.id) == PROBE_TEMPLATE:
            return experiment
    raise AssertionError(
        f"no {PROBE_TEMPLATE!r} reading among {[str(t.id) for _, t, _ in records]!r}; "
        f"the Stage A design is no longer in the slice's templates"
    )


@lru_cache(maxsize=1)
def ppc_outcomes() -> Mapping[str, tuple[float, ...]]:
    """Return the check's p-value per scenario, per defect type, in two regimes.

    ``"correct"`` holds the correctly-specified arm, whose ground truth is in the
    hypothesis set the engine holds.

    Each misspecification is reported twice. ``label`` is the check over every
    template -- what the engine could see given the whole catalogue at once --
    and ``label/probe`` is the same runs checked over the Stage A design alone.
    The second is the operative one and the first is an upper bound: no budgeted
    run records every template, and since 2026-08-16 the gate a system acts on is
    scoped to the probe. Reporting only the first would credit the check with
    power no investigation can draw on, which is exactly the direction SPEC §6.2
    warns about.
    """
    simulate = simulator(GRAMMAR)
    outcomes: dict[str, tuple[float, ...]] = {
        "correct": tuple(
            engine_over(records).ppc().p_value
            for _, records in benchmark()[:MISSPECIFIED]
        ),
        "correct/probe": tuple(
            engine_over(records).ppc(experiments={probe_of(records)}).p_value
            for _, records in benchmark()[:MISSPECIFIED]
        ),
    }
    for label, defect in MISSPECIFICATIONS:
        arms = tuple(
            observe(defect, label, index, simulate) for index in range(MISSPECIFIED)
        )
        outcomes[label] = tuple(engine_over(records).ppc().p_value for records in arms)
        outcomes[f"{label}/probe"] = tuple(
            engine_over(records).ppc(experiments={probe_of(records)}).p_value
            for records in arms
        )
    return outcomes


class TestA9PosteriorPredictiveChecks:
    """A9: the check's size is bounded and its power is reported per defect."""

    @pytest.mark.parametrize("arm", ["correct", "correct/probe"])
    def test_a9_false_positive_rate_is_within_twice_nominal(self, arm: str) -> None:
        """On correctly-specified scenarios the check fires at most 2 alpha.

        Both regimes are bounded, and the scoped one has to be: it is the check a
        system now gates on, so its size is the false-positive rate of every
        proposal an investigation makes. A bound that held only over the whole
        catalogue would bound nothing anyone acts on.
        """
        p_values = ppc_outcomes()[arm]
        assert len(p_values) == MISSPECIFIED
        false_positives = sum(1 for p in p_values if p < ALPHA)
        rate = false_positives / len(p_values)
        assert rate <= 2.0 * ALPHA, (
            f"in the {arm!r} regime the check fired on {false_positives}/"
            f"{len(p_values)} correctly specified scenarios, a rate of "
            f"{rate:.1%} against a nominal {ALPHA:.0%}"
        )

    def test_a9_detection_power_is_reported_per_defect_type(self) -> None:
        """Power is measured for every misspecification, and is real for one.

        A9 asks for power to be *reported*, not to clear a threshold, because the
        honest number for an out-of-library mechanism calibrated to hide among
        the closed set may well be near zero. What must not be true is that the
        check has no power at all, which would make baseline B1 a straw man and
        SPEC §4.6's first slice requirement unmeasurable -- so the control arm is
        asserted and the hard arm is only recorded.

        **Both regimes, since 2026-08-16, and neither dominates.** Measured at
        100 scenarios each:

        =====================  ==============  ==========
        misspecification       all templates   probe only
        =====================  ==============  ==========
        ``size_excitation``    52%             **90%**
        ``size_mixture``       100%            **3%**
        =====================  ==============  ==========

        The Stage A probe is a *directional* instrument, and that is the finding
        this test exists to keep visible. Against S11's own mechanism, scoping
        nearly doubles power -- the combination rule scales by ``1 + ln(n)``, so
        eight readings that say nothing about the mark-arrival coupling dilute
        the one that does. Against a size-distribution mixture, which the probe
        does not measure, scoping costs almost all of it.

        So the number SPEC §6.2 says the LLM must not be credited with is 90%,
        not 52%, and the price of it is written in the second row.

        **What is asserted did not change, and that is deliberate.** The control
        arm is asserted; the hard arm is recorded and bounded, exactly as before
        the second regime existed. ``size_excitation`` is S11's own mechanism and
        therefore the arm V7 is graded against, so a threshold placed on it here
        -- in the same session that measured V7 against the gate it describes --
        would be an acceptance bar written after seeing the system pass it. The
        two-regime *reporting* is the finding; asserting on it is what the
        invariant behind SPEC §11's LLM ordering forbids, and the discipline this
        test was already written with says the same thing in its own words.
        """
        outcomes = ppc_outcomes()
        power = {
            label: sum(1 for p in outcomes[label] if p < ALPHA) / MISSPECIFIED
            for label in outcomes
        }
        assert power["size_mixture"] > 0.9, (
            f"the catalogue detected a defect in a component no closed-set "
            f"hypothesis touches only {power['size_mixture']:.0%} of the time; "
            f"the probe cannot cover this direction and nothing else would"
        )
        for arm in ("size_excitation", "size_excitation/probe", "size_mixture/probe"):
            assert 0.0 <= power[arm] <= 1.0

    def test_a9_check_reports_a_p_value_for_every_experiment(self) -> None:
        """Every recorded experiment is checked, not just the worst one."""
        _, records = benchmark()[0]
        result = engine_over(records).ppc()
        assert set(result.per_experiment) == {
            experiment for experiment, _, _ in records
        }
        assert all(0.0 < p <= 1.0 for p in result.per_experiment.values())


# ==========================================================================
# A10  Hypothesis-space expansion
# ==========================================================================

#: Constructed cases behind A10 and A11, drawn from the benchmark.
EXPANSION_CASES = 50

#: Replicates behind A11's cost-accounting arm. That criterion is an identity
#: between a reported figure and a counter, and it holds at any table size, so it
#: is checked on a cheap table rather than on two full builds of the slice's.
COST_REPLICATES = 50

#: The hypothesis withheld and then admitted mid-investigation. Regime switching
#: is the most expensive structure in the closed set at 29 bits, so it is the one
#: whose prior a normalisation mistake would most visibly disturb.
LATE = "regime_switching"


def graph_without(name: str) -> HypothesisGraph:
    """Return a graph over the closed set with one hypothesis withheld."""
    graph = HypothesisGraph.empty(GRAMMAR, METRICS)
    for other in STRUCTURE_NAMES:
        if other == name:
            continue
        graph = graph.propose(
            HypothesisId(other),
            program_edit=CLOSED_SET[other],
            predictions=(_prediction(other),),
        )
    return graph


class TestA10HypothesisSpaceExpansion:
    """A10: lateness costs a hypothesis nothing (SPEC F9)."""

    def test_a10_late_hypothesis_matches_one_present_from_the_start(self) -> None:
        """The expanded posterior equals the from-scratch one on 50 cases.

        Equality here is exact, not "within Monte Carlo error". The late
        hypothesis's table row is derived from the structure and the table seed
        alone, so it is the identical row; the prior is its own code length,
        which does not depend on when it arrived; and both posteriors sum the
        same likelihoods in the same order. Anything short of bit-equality would
        mean one of those three had failed.
        """
        withheld = graph_without(LATE)
        node = closed_graph().node(HypothesisId(LATE))
        for truth, records in benchmark()[:EXPANSION_CASES]:
            late = engine_over(records, graph=withheld)
            cost = late.expand(node)
            assert cost.experiments_reevaluated == len(records)
            expanded = late.posterior()
            from_start = engine_over(records).posterior()
            assert sorted(expanded) == sorted(from_start)
            for node_id in sorted(from_start):
                assert expanded[node_id] == from_start[node_id], (
                    f"scenario with truth {truth!r}: {node_id} at "
                    f"{expanded[node_id]!r} when admitted late against "
                    f"{from_start[node_id]!r} when present from the start"
                )

    def test_a10_expansion_renormalises_over_the_enlarged_set(self) -> None:
        """The posterior sums to one before and after a hypothesis is admitted."""
        _, records = benchmark()[0]
        engine = engine_over(records, graph=graph_without(LATE))
        before = engine.posterior()
        assert math.fsum(before[key] for key in sorted(before)) == pytest.approx(1.0)
        assert LATE not in before
        engine.expand(closed_graph().node(HypothesisId(LATE)))
        after = engine.posterior()
        assert math.fsum(after[key] for key in sorted(after)) == pytest.approx(1.0)
        assert HypothesisId(LATE) in after


# ==========================================================================
# A11  Retrospective re-evaluation
# ==========================================================================


class TestA11RetrospectiveReevaluation:
    """A11: retro-evaluation reproduces a from-scratch run, at a measured cost."""

    def test_a11_retro_likelihoods_match_a_from_scratch_run(self) -> None:
        """Every prior experiment is re-scored to the value it would have had."""
        withheld = graph_without(LATE)
        node = closed_graph().node(HypothesisId(LATE))
        for truth, records in benchmark()[:EXPANSION_CASES]:
            late = engine_over(records, graph=withheld)
            late.expand(node)
            scratch = engine_over(records)
            for experiment, _, result in records:
                retro = late.log_likelihood(HypothesisId(LATE), experiment, result)
                fresh = scratch.log_likelihood(HypothesisId(LATE), experiment, result)
                assert retro == fresh, (
                    f"scenario with truth {truth!r}, experiment {experiment}: "
                    f"retro-evaluated {retro!r} against from-scratch {fresh!r}"
                )

    def test_a11_expansion_cost_matches_measured_simulator_calls(self) -> None:
        """``ExpansionCost.simulator_calls`` equals the calls actually made.

        The engine is given a table that does not cover the late hypothesis, so
        its row has to be simulated, and the simulator is wrapped in a counter.
        The reported figure must equal the counter exactly -- an expansion cost
        that is an estimate would be useless for the budget accounting SPEC §5's
        baselines are compared under.

        Built at :data:`COST_REPLICATES` rather than at the slice's count. What
        is under test is an accounting identity, which holds at any table size,
        and building the full table twice over would add two and a half minutes
        to every run of the suite to check the same equality.
        """
        calls = 0
        inner = simulator(GRAMMAR)

        def counting(
            defect: Defect, template: ExperimentTemplate, seed: Seed
        ) -> DiagnosticVector:
            nonlocal calls
            calls += 1
            return inner(defect, template, seed)

        partial, _ = EmpiricalTable.build(
            defects=[CLOSED_SET[name] for name in STRUCTURE_NAMES if name != LATE],
            templates=TEMPLATES,
            simulate=counting,
            replicates=COST_REPLICATES,
            seed=TABLE_SEED,
        )
        _, records = benchmark()[0]
        engine = engine_over(
            records, graph=graph_without(LATE), table=partial, simulate=counting
        )
        calls = 0
        cost = engine.expand(closed_graph().node(HypothesisId(LATE)))
        assert cost.simulator_calls == calls
        assert cost.simulator_calls == COST_REPLICATES * len(TEMPLATES)
        assert cost.experiments_reevaluated == len(records)

    def test_a11_expansion_is_free_when_the_table_already_covers_it(self) -> None:
        """Admitting a structure the table holds costs no simulation."""
        _, records = benchmark()[0]
        engine = engine_over(records, graph=graph_without(LATE))
        cost = engine.expand(closed_graph().node(HypothesisId(LATE)))
        assert cost.simulator_calls == 0
        assert cost.experiments_reevaluated == len(records)

    def test_a11_expansion_without_a_simulator_is_refused(self) -> None:
        """An engine that cannot simulate says so rather than guessing a row."""
        partial = EmpiricalTable(
            templates=slice_table().templates,
            counts=FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]](
                {
                    key: row
                    for key, row in slice_table().counts.items()
                    if key[0] != structure_key(CLOSED_SET[LATE])
                }
            ),
            replicates=REPLICATES,
            seed=TABLE_SEED,
        )
        _, records = benchmark()[0]
        engine = engine_over(records, graph=graph_without(LATE), table=partial)
        with pytest.raises(TableError, match="without a simulator"):
            engine.expand(closed_graph().node(HypothesisId(LATE)))


# ==========================================================================
# Supporting properties
# ==========================================================================


class TestEngineInvariants:
    """Properties the criteria above rest on, checked directly."""

    def test_the_table_is_reproducible_across_builds(self) -> None:
        """Rebuilding one structure's rows reproduces them exactly."""
        table = slice_table()
        rebuilt, _ = EmpiricalTable.build(
            defects=[CLOSED_SET["seasonality"]],
            templates=TEMPLATES,
            simulate=simulator(GRAMMAR),
            replicates=REPLICATES,
            seed=TABLE_SEED,
        )
        for template in TEMPLATES:
            assert rebuilt.row(CLOSED_SET["seasonality"], template.id) == table.row(
                CLOSED_SET["seasonality"], template.id
            )

    def test_no_cell_is_assigned_zero_probability(self) -> None:
        """A finite simulation budget never eliminates a hypothesis outright."""
        table = slice_table()
        for name in STRUCTURE_NAMES:
            for template in TEMPLATES:
                probabilities = table.probabilities(CLOSED_SET[name], template.id)
                assert all(p > 0.0 for p in probabilities)
                assert math.fsum(probabilities) == pytest.approx(1.0)

    def test_rejected_hypotheses_hold_no_posterior_mass(self) -> None:
        """Rejection zeroes a hypothesis in the posterior, not in the prior."""
        graph = closed_graph()
        from sciagent.core.types import RejectionCode

        rejected = graph.reject(
            HypothesisId("seasonality"), RejectionCode.UNSATISFIABLE_CONDITION
        )
        _, records = benchmark()[0]
        engine = engine_over(records, graph=rejected)
        posterior = engine.posterior()
        assert posterior[HypothesisId("seasonality")] == 0.0
        assert math.fsum(posterior[key] for key in sorted(posterior)) == pytest.approx(
            1.0
        )
        assert (
            rejected.node(HypothesisId("seasonality")).plausibility
            == graph.node(HypothesisId("seasonality")).plausibility
        )

    def test_a_second_result_for_one_experiment_is_refused(self) -> None:
        """An experiment id addresses one registered row and one result."""
        _, records = benchmark()[0]
        engine = engine_over(records)
        experiment, _, result = records[0]
        with pytest.raises(Exception, match="recorded with result"):
            engine.log_likelihood(
                HypothesisId("null"), experiment, tuple(v + 1.0 for v in result)
            )
