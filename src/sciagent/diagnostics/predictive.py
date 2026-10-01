"""Predictive p-values on a named diagnostic (SPEC §2.2 "Goodness of fit").

``fit`` reports, for any diagnostic the agent names, how extreme the observed
value is among replicate logs simulated from the fitted model. The simulator is
supplied by the caller (a closure over the fitted structure, ψ, θ, channels and
horizon), so this module stays independent of how replicates are generated.

**Fixed statistic.** Arguments are resolved once, on the observed log, and the
same absolute values are applied to every replicate (time defaults are in units
of the *observed* mean gap). Otherwise a default window would mean a different
statistic on each replicate.

**p-value.** With R scored replicates ``y_r`` and observed ``y``, the two-sided
Monte Carlo p-value is ``min(1, 2 · min(1 + #{y_r ≤ y}, 1 + #{y_r ≥ y}) / (R + 1))``,
which is valid (super-uniform under the model) for any R and never zero.

**Failed replicates.** A replicate on which the diagnostic raises
:class:`InsufficientDataError` (too few events, say) is counted in
``n_failed`` and excluded from R; if none is scored the check raises. Any other
error propagates.

**Determinism.** Replicate ``r`` draws from the ``r``-th child of
``rng.spawn(n_rep)``, so the result depends only on the generator's seed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from sciagent.diagnostics.catalogue import (
    Args,
    ArgValue,
    DiagnosticArgumentError,
    InsufficientDataError,
    compute,
    resolve_args,
)
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import ChannelSpec

MAX_REPLICATES: Final = 10_000

type Simulator = Callable[[np.random.Generator], EventLog]


@dataclass(frozen=True)
class PredictiveCheck:
    """A diagnostic's observed value against its replicate distribution."""

    name: str
    args: Mapping[str, ArgValue]
    observed: float
    replicates: tuple[float, ...]
    n_failed: int
    p_value: float


def two_sided_pvalue(observed: float, replicates: tuple[float, ...]) -> float:
    """``min(1, 2·min(1 + #≤, 1 + #≥) / (R + 1))`` (see the module docstring)."""
    r = len(replicates)
    below = sum(1 for y in replicates if y <= observed)
    above = sum(1 for y in replicates if y >= observed)
    return min(1.0, 2.0 * (1 + min(below, above)) / (r + 1))


def predictive_check(
    name: str,
    args: Args,
    observed_log: EventLog,
    simulate_fn: Simulator,
    n_rep: int,
    rng: np.random.Generator,
    *,
    channels: tuple[ChannelSpec, ...],
) -> PredictiveCheck:
    """Simulate ``n_rep`` replicates; compare diagnostic ``name`` with the observed."""
    if not 1 <= n_rep <= MAX_REPLICATES:
        raise DiagnosticArgumentError(f"n_rep must be in [1, {MAX_REPLICATES}]")
    resolved = resolve_args(name, args, observed_log, channels)
    observed = compute(name, observed_log, channels, resolved)
    values: list[float] = []
    failed = 0
    for child in rng.spawn(n_rep):
        replicate = simulate_fn(child)
        try:
            values.append(
                compute(name, replicate, channels, resolved, check_bounds=False)
            )
        except InsufficientDataError:
            failed += 1
    if not values:
        raise InsufficientDataError(f"{name} failed on all {n_rep} replicates")
    scored = tuple(values)
    return PredictiveCheck(
        name=name,
        args=resolved,
        observed=observed,
        replicates=scored,
        n_failed=failed,
        p_value=two_sided_pvalue(observed, scored),
    )


def predictive_pvalue(
    name: str,
    args: Args,
    observed_log: EventLog,
    simulate_fn: Simulator,
    n_rep: int,
    rng: np.random.Generator,
    *,
    channels: tuple[ChannelSpec, ...],
) -> float:
    """The two-sided predictive p-value of :func:`predictive_check`."""
    return predictive_check(
        name, args, observed_log, simulate_fn, n_rep, rng, channels=channels
    ).p_value
