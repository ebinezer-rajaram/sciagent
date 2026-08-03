"""The slice's binned outcome spaces, experiment templates and simulator.

``catalogue.py`` says what the diagnostics *are*; this module says what a
posterior engine is allowed to see of them. Three things live here, and all three
are environment decisions that the domain-independent engine must not be making
for itself:

**Bin edges.** Frozen literals, chosen once from a pilot of 300 runs per
structure at the reference operating point, and never derived from the scenario
under investigation. The pilot is reproducible -- ``scripts/status.py`` is not
where it lives, the numbers in ``docs/DECISIONS.md`` are -- but the edges are
inputs to every likelihood the framework computes, so they are literals here
rather than a computation anywhere.

**Templates.** Each names exactly one diagnostic. That is deliberate: two
diagnostics read off the same execution are correlated, and the engine's
likelihood factorises across experiments only because each experiment is a
separate execution under its own seed. One diagnostic per template keeps that
factorisation exact rather than approximately true. The four here are the
smallest set that separates the closed set of SPEC §4.2:

+--------------------------------+----------------------------------------+
| Template                       | What it decides                        |
+================================+========================================+
| ``inter_arrival_dispersion``   | undefective, or not                    |
| ``count_autocorrelation_w2``   | clustered in time, or merely           |
|                                | overdispersed; seasonality sits between|
| ``phase_conditioned_dispersion``| phase-locked, or not                  |
| ``size_dispersion``            | the arrival mechanisms cannot touch it,|
|                                | so it is the check's control channel   |
+--------------------------------+----------------------------------------+

Hawkes and regime switching are separated by *none* of them, which is correct and
measured: ``docs/DECISIONS.md`` records the pair as indistinguishable by any
dispersion diagnostic, which is what leaves intervention as the only route and
what scenario S10's non-identifiability rests on.

**The simulator.** The adapter that turns "apply this defect and measure this
template" into an execution. It caches the most recent execution, because the
table asks every template for the same ``(defect, seed)`` in turn and re-running
the programme once per diagnostic would multiply the build cost by four for no
change in any number.
"""

from __future__ import annotations

from collections.abc import Mapping

from environments.pointproc.catalogue import metric_registry
from environments.pointproc.grammar import edit_grammar
from environments.pointproc.mechanisms import CONFOUNDED_MECHANISMS
from environments.pointproc.program import reference_program
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.program import GenerativeProgram
from sciagent.core.types import EventLog, ExperimentTemplateId, MetricName, Seed
from sciagent.inference.binning import DiagnosticVector, Discretisation, OutcomeSpace
from sciagent.inference.interface import ExperimentTemplate, Simulator

#: Events per execution. Long enough that every diagnostic in the catalogue is
#: estimable -- the phase-conditioned dispersion needs several windows per phase
#: bin, which at the reference rate of one event per unit time means a span of
#: some hundreds -- and short enough that a table of a few thousand replicates
#: costs minutes rather than hours.
N_EVENTS = 512

#: Interior bin edges per metric, in the metric's own units. Read the pilot
#: quantiles in ``docs/DECISIONS.md`` alongside these: the edges are placed to
#: resolve the region where the closed set actually differs, and to lump the
#: region where it does not. Extra resolution where every hypothesis agrees costs
#: replicates and buys nothing.
_EDGES: Mapping[str, tuple[float, ...]] = {
    # Undefective runs sit below 1.3 and every mechanism above 2.2. The first
    # bin therefore holds the whole reference distribution, and the remaining
    # ten resolve the mechanisms against each other.
    "inter_arrival_dispersion": (
        1.3,
        2.2,
        2.6,
        2.9,
        3.15,
        3.4,
        3.65,
        3.95,
        4.35,
        5.0,
    ),
    # Three regimes: no temporal correlation (undefective, Poisson mixture) near
    # zero, seasonality near 0.21, self-excitation and regime switching near
    # 0.47. The edges are finest between them.
    "count_autocorrelation_w2": (
        -0.06,
        0.0,
        0.06,
        0.13,
        0.19,
        0.24,
        0.30,
        0.36,
        0.42,
        0.47,
        0.52,
        0.58,
        0.65,
    ),
    # Conditioning on phase collapses a deterministic seasonal rate to about 1
    # and leaves everything else where it was, so the edges below 1.2 carry the
    # decision and those above it separate the mixture from the rest.
    "phase_conditioned_dispersion": (
        0.95,
        1.02,
        1.09,
        1.16,
        1.5,
        1.85,
        2.0,
        2.13,
        2.25,
        2.4,
        2.6,
        2.85,
        3.1,
        3.4,
    ),
    # No arrival mechanism perturbs the size component at all -- the size stream
    # is derived from its own name, so a fixed seed yields identical marks
    # whatever was done to arrivals. Every closed-set hypothesis therefore
    # predicts the same distribution here, and everything above 1.25 is where a
    # size-component defect lands.
    #
    # Deliberately coarse above 1.25. No closed-set hypothesis puts any mass
    # there, so extra edges buy no discrimination -- and they cost detection
    # power: the posterior predictive check's tail is the sum over every cell no
    # more likely than the observed one, and each unreached cell contributes the
    # rule-of-three floor. Splitting that region into thirteen cells rather than
    # three would multiply the smallest attainable p-value by four for nothing.
    "size_dispersion": (0.9, 0.95, 1.0, 1.05, 1.12, 1.25, 3.0, 8.0),
}


def discretisation(name: str) -> Discretisation:
    """Return the frozen discretisation of one diagnostic.

    Guarantees the bin edges lie inside the range the metric registry declares
    for that diagnostic, so a value the metric can legitimately return always has
    a bin.
    """
    spec = metric_registry().spec(name)
    return Discretisation(
        metric=spec.ref, interior=_EDGES[name], low=spec.low, high=spec.high
    )


def _template(name: str) -> ExperimentTemplate:
    return ExperimentTemplate(
        id=ExperimentTemplateId(f"query:{name}"),
        outcome=OutcomeSpace(axes=(discretisation(name),)),
        n_events=N_EVENTS,
    )


def slice_templates() -> tuple[ExperimentTemplate, ...]:
    """Return the slice's experiment templates, in a fixed order.

    Guarantees a stable set of ids and outcome spaces, and therefore a stable
    :attr:`~sciagent.inference.empirical.EmpiricalTable.version`.
    """
    return tuple(_template(name) for name in sorted(_EDGES))


def closed_set() -> Mapping[str, Defect]:
    """Return the slice's closed hypothesis set: the null plus SPEC §4.2's four.

    The null -- the empty edit set -- is a hypothesis and not an absence of one.
    Scenario S9 asks for correct abstention when nothing is wrong, which is only
    expressible if "nothing is wrong" is something the posterior can put mass on.
    """
    return {
        "null": frozenset(),
        **{
            name: frozenset({edit})
            for name, edit in sorted(CONFOUNDED_MECHANISMS.items())
        },
    }


def simulator(grammar: EditGrammar | None = None) -> Simulator:
    """Return a :data:`~sciagent.inference.interface.Simulator` for this slice.

    Guarantees the returned callable is a pure function of
    ``(defect, template, seed)``: the cache it keeps is keyed on exactly those
    inputs that determine an execution, so it changes how long a call takes and
    never what it returns.
    """
    resolved = grammar if grammar is not None else edit_grammar()
    reference = reference_program()
    registry = metric_registry()
    compiled: dict[Defect, GenerativeProgram] = {}
    cache: dict[tuple[Defect, int, int], EventLog] = {}

    def simulate(
        defect: Defect, template: ExperimentTemplate, seed: Seed
    ) -> DiagnosticVector:
        program = compiled.get(defect)
        if program is None:
            program = resolved.apply(reference, defect)
            compiled[defect] = program
        key = (defect, int(seed), template.n_events)
        log = cache.get(key)
        if log is None:
            log = program.execute(seed, template.n_events)
            # One entry: the table asks every template for the same
            # (defect, seed) in turn, so a single slot is the whole win, and
            # holding more would grow without bound over a table build.
            cache.clear()
            cache[key] = log
        return tuple(
            registry.spec(str(metric.name)).compute(log)
            for metric in template.outcome.metrics
        )

    return simulate


def metric_names() -> tuple[MetricName, ...]:
    """Return the diagnostics the slice's templates measure, in template order."""
    return tuple(template.outcome.metrics[0].name for template in slice_templates())
