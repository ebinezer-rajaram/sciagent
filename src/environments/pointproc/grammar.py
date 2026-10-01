"""The point-process edit grammar: ground truth and the agent's subset.

Two grammars are declared. They differ in two entries, one per scenario that
turns on the difference, and in nothing else::

    AddDependency(size -> arrival)            in edit_grammar() \\ agent_grammar()
    ChangeDistributionFamily(obs, censored)   in edit_grammar() \\ agent_grammar()

The first is scenario S11's mechanism: ``edit_grammar`` licenses lagged
dependencies from ``size`` to ``arrival`` and ``agent_grammar`` licenses
self-loops only, which is what makes S11 out-of-library by the mechanical
definition of v1 SPEC §3.2 rather than by anyone's judgement.

The second is scenario S12's *nuisance*, an observation process that censors a
window of every cycle. It sits on the same side of the line for a different
reason: it is not a defect anyone is being asked to find, and a system that could
propose it would be a system that could explain S12's garden path away instead of
recovering from it. Keeping it out of ``agent_grammar`` is what makes that
structural rather than a matter of the agent's restraint.

Every parameter is quantised onto a grid of ``GRID_SIZE`` points, so each
parameter costs exactly ``log2(GRID_SIZE)`` bits under the prefix code and the
edit space is finite and enumerable (acceptance test A4). Ranges are wide enough
to contain plainly implausible values as well as plausible ones: narrowing a
range to the region where the answer lies would be exactly the kind of tuning
v1 SPEC §0 forbids.

The code is grammar-relative, so adding the ``obs`` option lengthens every
``ChangeDistributionFamily`` edit under ``edit_grammar`` by the bit it now costs
to say which of three components is meant. Nothing an agent is scored on is
computed under this grammar -- a hypothesis graph carries ``agent_grammar`` --
and ``docs/v1/DECISIONS.md`` records the shift.
"""

from __future__ import annotations

from environments.pointproc.components import (
    ARRIVAL,
    HAWKES_EXPONENTIAL,
    IDENTITY_PERIODIC_CENSORED,
    MIXTURE_OF_EXPONENTIAL_2,
    MIXTURE_OF_POISSON_2,
    OBS,
    POISSON_MODULATED_2STATE,
    POISSON_PERIODIC,
    SIZE,
    SIZE_EXCITED_EXPONENTIAL,
    TWO_STATE_MARKOV,
)
from sciagent.core.edits import (
    AddDependency,
    AddLatentVariable,
    ChangeDistributionFamily,
    DependencyOption,
    EditGrammar,
    FamilyOption,
    LatentOption,
    ParameterGrid,
    ReparameteriseComponent,
)
from sciagent.core.types import ComponentId, FrozenDict, GrammarVersion, KernelId

#: Points per parameter grid. Fixes the prefix code's per-parameter cost at
#: ``log2(64) = 6`` bits.
#:
#: Chosen for representational adequacy, not for any scenario's outcome. At 32
#: points the rate-like grids span three decades in 25% steps, and the best
#: mean rate the periodic mechanism could reach was 4.3% from the reference --
#: enough that the marginal rate alone partly identified it, leaking mechanism
#: identity through a quantity that is a nuisance parameter rather than a
#: discriminator. At 64 points the same mechanism reaches 0.3%. The cost is one
#: extra bit per parameter, applied uniformly, so relative code lengths between
#: mechanisms are unchanged except through the parameter counts that the
#: parsimony prior is meant to charge for.
GRID_SIZE = 64

EXPONENTIAL_KERNEL = KernelId("exponential")

#: Bumped to 1.1.0 at backlog item 11, which licensed scenario S12's censoring
#: observation process. It enters ``ENV_VERSION`` and every registered
#: experiment's content address, and it changes the code length of every
#: ``ChangeDistributionFamily`` edit under this grammar, so a result addressed
#: under 1.0.0 was computed under a different prior and must not be read as
#: though it were this one.
GRAMMAR_VERSION = GrammarVersion("pointproc/1.1.0")

#: Unchanged: nothing an agent may express has moved.
AGENT_GRAMMAR_VERSION = GrammarVersion("pointproc-agent/1.0.0")

# --------------------------------------------------------------------------
# Parameter grids
# --------------------------------------------------------------------------

HAWKES_GRIDS = (
    ParameterGrid("base_rate", 0.01, 10.0, GRID_SIZE, "log"),
    ParameterGrid("branching", 0.02, 0.95, GRID_SIZE, "linear"),
    ParameterGrid("decay", 0.02, 20.0, GRID_SIZE, "log"),
)

PERIODIC_GRIDS = (
    ParameterGrid("base_rate", 0.01, 10.0, GRID_SIZE, "log"),
    ParameterGrid("amplitude", 0.05, 5.0, GRID_SIZE, "log"),
    ParameterGrid("period", 0.2, 200.0, GRID_SIZE, "log"),
)

POISSON_MIXTURE_GRIDS = (
    ParameterGrid("rate_low", 0.002, 5.0, GRID_SIZE, "log"),
    ParameterGrid("rate_high", 0.05, 50.0, GRID_SIZE, "log"),
    ParameterGrid("weight_high", 0.02, 0.98, GRID_SIZE, "linear"),
)

TWO_STATE_GRIDS = (
    ParameterGrid("mult_low", 0.02, 1.0, GRID_SIZE, "log"),
    ParameterGrid("mult_high", 1.0, 50.0, GRID_SIZE, "log"),
    ParameterGrid("switch_rate", 0.001, 5.0, GRID_SIZE, "log"),
    ParameterGrid("p_high", 0.02, 0.98, GRID_SIZE, "linear"),
)

#: ``excitation`` is a branching ratio per unit mark size, so its range mirrors
#: ``branching`` above: at the reference mean mark size of 1.0 the process is
#: stationary exactly when it is below 1. Deliberately parallel to
#: ``HAWKES_GRIDS`` -- the two mechanisms should be as similar as their
#: structures allow, since telling them apart is what scenario S11 tests.
SIZE_EXCITED_GRIDS = (
    ParameterGrid("base_rate", 0.01, 10.0, GRID_SIZE, "log"),
    ParameterGrid("excitation", 0.02, 0.95, GRID_SIZE, "linear"),
    ParameterGrid("decay", 0.02, 20.0, GRID_SIZE, "log"),
)

EXPONENTIAL_MIXTURE_GRIDS = (
    ParameterGrid("mean_low", 0.01, 5.0, GRID_SIZE, "log"),
    ParameterGrid("mean_high", 0.1, 50.0, GRID_SIZE, "log"),
    ParameterGrid("weight_high", 0.02, 0.98, GRID_SIZE, "linear"),
)

#: Scenario S12's censoring window. ``period`` shares the periodic mechanism's
#: range deliberately -- a censoring cycle and a seasonal cycle are the same kind
#: of quantity, and the scenario's whole content is that one can be mistaken for
#: the other. ``duty`` is the fraction of each cycle during which events are
#: recorded, on the same linear range as every other fraction in this grammar.
CENSORING_GRIDS = (
    ParameterGrid("period", 0.2, 200.0, GRID_SIZE, "log"),
    ParameterGrid("duty", 0.02, 0.98, GRID_SIZE, "linear"),
)

# --------------------------------------------------------------------------
# Option tables
# --------------------------------------------------------------------------

_FAMILIES = FrozenDict[ComponentId, tuple[FamilyOption, ...]](
    {
        ARRIVAL: (FamilyOption(MIXTURE_OF_POISSON_2, POISSON_MIXTURE_GRIDS),),
        SIZE: (FamilyOption(MIXTURE_OF_EXPONENTIAL_2, EXPONENTIAL_MIXTURE_GRIDS),),
    }
)

#: The ground-truth family table: the agent's, plus the censoring observation
#: process of scenario S12. See this module's docstring for why it is on this
#: side of the line.
_GROUND_FAMILIES = FrozenDict[ComponentId, tuple[FamilyOption, ...]](
    {
        **_FAMILIES,
        OBS: (FamilyOption(IDENTITY_PERIODIC_CENSORED, CENSORING_GRIDS),),
    }
)

_PARAMETERISATIONS = FrozenDict[ComponentId, tuple[FamilyOption, ...]](
    {ARRIVAL: (FamilyOption(POISSON_PERIODIC, PERIODIC_GRIDS),)}
)

_LATENTS = FrozenDict[ComponentId, tuple[LatentOption, ...]](
    {
        ARRIVAL: (
            LatentOption(TWO_STATE_MARKOV, POISSON_MODULATED_2STATE, TWO_STATE_GRIDS),
        )
    }
)

_SELF_EXCITATION = DependencyOption(
    source=ARRIVAL,
    kernel=EXPONENTIAL_KERNEL,
    resolved_family=HAWKES_EXPONENTIAL,
    grids=HAWKES_GRIDS,
)

_SIZE_EXCITATION = DependencyOption(
    source=SIZE,
    kernel=EXPONENTIAL_KERNEL,
    resolved_family=SIZE_EXCITED_EXPONENTIAL,
    grids=SIZE_EXCITED_GRIDS,
)

_ALL_TYPES = frozenset(
    {
        ChangeDistributionFamily,
        ReparameteriseComponent,
        AddLatentVariable,
        AddDependency,
    }
)


def edit_grammar() -> EditGrammar:
    """Return the ground-truth grammar: every mechanism the environment can hold.

    Guarantees a superset of :func:`agent_grammar`, differing by the
    ``size -> arrival`` dependency option (scenario S11's mechanism) and the
    censoring observation process on ``obs`` (scenario S12's nuisance), and by
    nothing else.
    """
    return EditGrammar(
        version=GRAMMAR_VERSION,
        allowed=_ALL_TYPES,
        families=_GROUND_FAMILIES,
        parameterisations=_PARAMETERISATIONS,
        latent_specs=_LATENTS,
        dependencies=FrozenDict[ComponentId, tuple[DependencyOption, ...]](
            {ARRIVAL: (_SELF_EXCITATION, _SIZE_EXCITATION)}
        ),
    )


def agent_grammar() -> EditGrammar:
    """Return the agent's grammar: cross-component dependencies excluded.

    The agent may hypothesise that arrivals excite themselves, but has no way to
    express that arrivals are excited by preceding mark *sizes*. Detecting that
    its hypothesis space is inadequate, and extending it, is what scenario S11
    tests (v1 SPEC §4.5, §4.6).
    """
    return EditGrammar(
        version=AGENT_GRAMMAR_VERSION,
        allowed=_ALL_TYPES,
        families=_FAMILIES,
        parameterisations=_PARAMETERISATIONS,
        latent_specs=_LATENTS,
        dependencies=FrozenDict[ComponentId, tuple[DependencyOption, ...]](
            {ARRIVAL: (_SELF_EXCITATION,)}
        ),
    )
