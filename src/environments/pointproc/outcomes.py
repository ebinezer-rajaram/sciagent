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

**Designs.** Each names exactly one diagnostic. That is deliberate: two
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
design" into an execution. Since backlog item 7 it is an
:class:`~sciagent.experiments.executor.Executor` with no registry attached: the
engine's ten thousand table-building executions are its internal arithmetic and
not experiments, so they travel the executor's ``measure`` path and never its
``run`` path. Sharing the path is the point -- a likelihood computed by a
different route than the observation it scores would hide a discrepancy between
them. The executor keeps the single-slot execution cache this module used to
keep, because the table asks every design for the same ``(defect, seed)`` in turn
and re-running the programme once per diagnostic would multiply the build cost by
four for no change in any number.
"""

from __future__ import annotations

from collections.abc import Mapping

from environments.pointproc.catalogue import metric_registry
from environments.pointproc.components import LIBRARY_VERSION
from environments.pointproc.grammar import GRAMMAR_VERSION, edit_grammar
from environments.pointproc.mechanisms import CONFOUNDED_MECHANISMS
from environments.pointproc.operations import compiler
from environments.pointproc.program import reference_program
from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.types import DataVersion, EnvVersion, MetricName
from sciagent.experiments.dsl import ExperimentDesign, QueryDiagnostic
from sciagent.experiments.executor import Executor
from sciagent.inference.binning import Discretisation, OutcomeSpace
from sciagent.inference.interface import ExperimentTemplate, Simulator
from sciagent.registry.budget import Budget
from sciagent.registry.partitions import DataPartition
from sciagent.registry.store import ExperimentStore

#: SPEC §3.2 defines ``EnvVersion`` as a content hash of code plus reference
#: programme. Until the environment protocol lands, the grammar and family
#: library versions stand in: between them they cover every construct a
#: programme can hold and every semantics it can be executed under, which is
#: what a registered result has to be addressed by.
ENV_VERSION = EnvVersion(f"pointproc/{GRAMMAR_VERSION}+{LIBRARY_VERSION}")

#: The slice generates its own data, so there is no external dataset to version.
#: The reference operating point is what a run is relative to, and it moves only
#: when the mechanisms are recalibrated.
DATA_VERSION = DataVersion("pointproc/generated/1.0.0")

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


def _design(name: str) -> ExperimentDesign:
    return ExperimentDesign(
        operation=QueryDiagnostic(),
        outcome=OutcomeSpace(axes=(discretisation(name),)),
        n_events=N_EVENTS,
    )


def slice_designs() -> tuple[ExperimentDesign, ...]:
    """Return the slice's experiment designs, in a fixed order.

    Every one is an observation under no manipulation, which is what the closed
    set of SPEC §4.2 can be separated by -- all but the Hawkes/regime-switching
    pair, whose only discriminator is a forced arrival and therefore belongs to a
    scenario's plan rather than to the table the posterior is calibrated on.

    Guarantees a stable set of ids and outcome spaces, and therefore a stable
    :attr:`~sciagent.inference.empirical.EmpiricalTable.version`.
    """
    return tuple(_design(name) for name in sorted(_EDGES))


def slice_templates() -> tuple[ExperimentTemplate, ...]:
    """Return the posterior engine's view of :func:`slice_designs`."""
    return tuple(design.template() for design in slice_designs())


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


def executor(
    grammar: EditGrammar | None = None,
    *,
    store: ExperimentStore | None = None,
    partition: DataPartition = DataPartition.DEV,
    budget: Budget | None = None,
) -> Executor:
    """Return an :class:`~sciagent.experiments.executor.Executor` for this slice.

    Guarantees the environment's own versions, grammar, metric catalogue and
    operation compiler. With no ``store``, ``run`` refuses and only ``measure``
    is available -- which is the configuration the posterior engine wants, since
    a table-building execution is not an experiment.
    """
    return Executor(
        reference=reference_program(),
        grammar=grammar if grammar is not None else edit_grammar(),
        compile=compiler(),
        metrics=metric_registry(),
        env_version=ENV_VERSION,
        data_version=DATA_VERSION,
        store=store,
        partition=partition,
        budget=budget,
    )


def simulator(grammar: EditGrammar | None = None) -> Simulator:
    """Return a :data:`~sciagent.inference.interface.Simulator` for this slice.

    Guarantees the returned callable is a pure function of
    ``(defect, template, seed)``: the executor's cache is keyed on exactly those
    inputs that determine an execution, so it changes how long a call takes and
    never what it returns. Nothing it does is registered or charged -- see this
    module's docstring.
    """
    return executor(grammar).simulator(slice_designs())


def metric_names() -> tuple[MetricName, ...]:
    """Return the diagnostics the slice's templates measure, in template order."""
    return tuple(template.outcome.metrics[0].name for template in slice_templates())
