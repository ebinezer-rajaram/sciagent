"""The point-process edit grammar: ground truth and the agent's subset.

Two grammars are declared. They differ in exactly one entry:
``edit_grammar`` licenses lagged dependencies from ``size`` to ``arrival``;
``agent_grammar`` licenses self-loops only. That single difference is what makes
scenario S11 out-of-library, by the mechanical definition of SPEC §3.2 rather
than by anyone's judgement::

    AddDependency(size -> arrival) in edit_grammar() \\ agent_grammar()

Every parameter is quantised onto a grid of ``GRID_SIZE`` points, so each
parameter costs exactly ``log2(GRID_SIZE)`` bits under the prefix code and the
edit space is finite and enumerable (acceptance test A4). Ranges are wide enough
to contain plainly implausible values as well as plausible ones: narrowing a
range to the region where the answer lies would be exactly the kind of tuning
SPEC §0 forbids.
"""

from __future__ import annotations

from environments.pointproc.components import (
    ARRIVAL,
    HAWKES_EXPONENTIAL,
    MIXTURE_OF_EXPONENTIAL_2,
    MIXTURE_OF_POISSON_2,
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

GRAMMAR_VERSION = GrammarVersion("pointproc/1.0.0")
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

# --------------------------------------------------------------------------
# Option tables
# --------------------------------------------------------------------------

_FAMILIES = FrozenDict[ComponentId, tuple[FamilyOption, ...]](
    {
        ARRIVAL: (FamilyOption(MIXTURE_OF_POISSON_2, POISSON_MIXTURE_GRIDS),),
        SIZE: (FamilyOption(MIXTURE_OF_EXPONENTIAL_2, EXPONENTIAL_MIXTURE_GRIDS),),
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

    Guarantees a superset of :func:`agent_grammar`, differing only by the
    ``size -> arrival`` dependency option.
    """
    return EditGrammar(
        version=GRAMMAR_VERSION,
        allowed=_ALL_TYPES,
        families=_FAMILIES,
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
    tests (SPEC §4.5, §4.6).
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
