"""The agent's view of the world: names and units as the agent sees them (SPEC §5).

The framework works in native names and units throughout. Every number and
name the agent reads passes through an :class:`AgentView` on the way out, and
everything the agent writes (DSL text, experiment designs, channel and
diagnostic names) passes through it on the way in. The ``named`` view is the
identity. The ``anon`` view renames channels and diagnostics with a
:class:`~sciagent.anonymise.NameMap` and multiplies time by its factor ``c``
(a power of two, so every conversion is bit-exact).

What is computed *in* the agent's view
--------------------------------------
Catalogue diagnostics and Wiener-Hopf kernels are computed directly on the
agent-view dataset (times * c, channels renamed). Both are domain-independent
and covariant under ``t -> c t`` (the catalogue declares it; Wiener-Hopf bins
in mean gaps), so this gives exactly what the agent would compute itself from
the data it holds, and a prediction is evaluated on the same object it was
stated about.

What is converted
-----------------
Fits cannot be computed in the agent's units, because the ψ grids are fixed in
native (mean-rate-1) units. A fit runs natively and its numbers are converted:

- ψ: ``exp_rate`` is a rate (÷ c); ``power_c``, ``gamma_mean`` and ``period``
  are times (* c); the rest are dimensionless.
- log L shifts by ``-N log c`` (N counted events): the intensity is a density
  per unit time. BIC and AIC shift by ``+2 N log c``. KS is invariant.
- θ: a feature whose columns multiply k ``Excite`` atoms has columns of
  dimension ``time^-k`` (kernels are densities). Under the identity link
  ``θ0' = θ0 / c`` and ``θk' = θk c^(k-1)``; under the exp link
  ``θ0' = θ0 - log c`` and ``θk' = θk c^k``. The softplus link is not
  covariant under rescaling (``softplus(η) / c`` is not a softplus of an
  affine form), so its θ cannot be expressed in the agent's units: it is shown
  as fitted. That is a stated limit, not an oversight.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal

from sciagent.anonymise import (
    NameMap,
    anonymise_channel_specs,
    anonymise_dataset,
    anonymise_structure,
    deanonymise_structure,
    make_name_map,
)
from sciagent.core.errors import SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.glm import syntax
from sciagent.glm.data import Dataset
from sciagent.glm.grammar import (
    ChannelSpec,
    Excite,
    Feature,
    Gate,
    Link,
    Periodic,
    Product,
    Structure,
    Trend,
    n_columns,
)
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    ClampRate,
    Compose,
    Experiment,
    ForceEvents,
    InjectMarks,
    Intervention,
    experiment_from_json,
    rescale_experiment,
    validate_experiment,
)

__all__ = [
    "PSI_TIME_POWER",
    "AgentView",
    "Condition",
    "ViewError",
    "excite_count",
    "make_view",
]

type Condition = Literal["named", "anon"]


class ViewError(SciAgentError):
    """A name or value cannot be mapped between the native and the agent view."""


#: Power of time in each ψ slot's unit: +1 a time, -1 a rate, 0 dimensionless.
PSI_TIME_POWER: Final[Mapping[str, int]] = {
    "exp_rate": -1,
    "power_c": 1,
    "power_p": 0,
    "gamma_shape": 0,
    "gamma_mean": 1,
    "pow_exponent": 0,
    "exp_coef": 0,
    "above_z": 0,
    "period": 1,
    "phase": 0,
}


def excite_count(feature: Feature) -> int:
    """Excite atoms multiplied together in each of the feature's columns."""
    match feature:
        case Excite():
            return 1
        case Periodic() | Trend():
            return 0
        case Product(left=left, right=right):
            return excite_count(left) + excite_count(right)
        case Gate(feature=inner):
            return excite_count(inner)


@dataclass(frozen=True)
class AgentView:
    """Names and units as the agent sees them; ``name_map`` None is the identity."""

    condition: Condition
    native_channels: tuple[ChannelSpec, ...]
    name_map: NameMap | None

    # -- basics --------------------------------------------------------------

    @property
    def factor(self) -> float:
        """Agent time per native time (1 in the named condition)."""
        return 1.0 if self.name_map is None else self.name_map.time_factor

    @property
    def channels(self) -> tuple[ChannelSpec, ...]:
        """The channel specs with agent-visible names, in agent-name order."""
        if self.name_map is None:
            specs = self.native_channels
        else:
            specs = anonymise_channel_specs(self.native_channels, self.name_map)
        return tuple(sorted(specs, key=lambda s: s.name))

    def channel(self, native: str) -> str:
        return (
            native if self.name_map is None else self.name_map.channel_forward(native)
        )

    def channel_native(self, agent: str) -> str:
        if self.name_map is None:
            if agent not in {c.name for c in self.native_channels}:
                raise ViewError(f"unknown mark channel {agent!r}")
            return agent
        try:
            return self.name_map.channel_inverse(agent)
        except SciAgentError:
            raise ViewError(f"unknown mark channel {agent!r}") from None

    def diagnostic(self, native: str) -> str:
        if self.name_map is None:
            return native
        return self.name_map.diagnostic_forward(native)

    def diagnostic_native(self, agent: str) -> str:
        if self.name_map is None:
            if agent not in catalogue.CATALOGUE:
                raise ViewError(f"no diagnostic named {agent!r}")
            return agent
        try:
            return self.name_map.diagnostic_inverse(agent)
        except SciAgentError:
            raise ViewError(f"no diagnostic named {agent!r}") from None

    def diagnostics(self) -> tuple[tuple[str, str], ...]:
        """``(agent name, native name)`` for the whole catalogue, by agent name."""
        return tuple(sorted((self.diagnostic(n), n) for n in catalogue.names()))

    def time(self, native: float) -> float:
        return native * self.factor

    def rate(self, native: float) -> float:
        return native / self.factor

    # -- data and structures -------------------------------------------------

    def dataset(self, native: Dataset) -> Dataset:
        """The dataset as the agent holds it."""
        if self.name_map is None:
            return native
        return anonymise_dataset(native, self.name_map)

    def parse(self, text: str) -> Structure:
        """Agent DSL text -> native structure (validated for the channels)."""
        structure = syntax.parse(text, self.channels)
        if self.name_map is None:
            return structure
        return deanonymise_structure(structure, self.name_map)

    def render(self, native: Structure) -> str:
        """Native structure -> agent DSL text."""
        if self.name_map is None:
            return syntax.render(native)
        return syntax.render(anonymise_structure(native, self.name_map))

    def render_feature(self, feature: Feature, link: Link) -> str:
        text = self.render(Structure((feature,), link))
        prefix = f"link={link.value};"
        return text[len(prefix) :].strip() if text.startswith(prefix) else text

    def experiment_native(self, args: Mapping[str, object]) -> Experiment:
        """Agent experiment JSON -> the native experiment the simulator runs.

        Validated in the agent's units (caps and messages in its units), then
        rescaled by ``1/c`` (exact) and its channel names mapped back.
        """
        agent = experiment_from_json(
            dict(args), default_horizon=self.time(DEFAULT_HORIZON)
        )
        validate_experiment(agent, self.channels, time_factor=self.factor)
        native = rescale_experiment(agent, 1.0 / self.factor)
        return Experiment(
            _rename_intervention(native.intervention, self.channel_native),
            native.horizon,
        )

    # -- fitted numbers ------------------------------------------------------

    def psi_value(self, name: str, native: float) -> float:
        return native * self.factor ** PSI_TIME_POWER[name]

    def log_likelihood(self, native: float, n_counted: int) -> float:
        return native - n_counted * math.log(self.factor)

    def information_criterion(self, native: float, n_counted: int) -> float:
        """BIC or AIC: ``-2 log L`` plus a penalty, so ``+2 N log c``."""
        return native + 2.0 * n_counted * math.log(self.factor)

    def theta(
        self, structure: Structure, theta: Sequence[float]
    ) -> tuple[tuple[float, ...], bool]:
        """θ in the agent's units, and whether the conversion was exact.

        See the module docstring; softplus is returned unconverted (False).
        """
        c = self.factor
        if c == 1.0:
            return tuple(theta), True
        if structure.link is Link.SOFTPLUS:
            return tuple(theta), False
        out = [_intercept(theta[0], structure.link, c)]
        position = 1
        for feature in structure.features:
            k = excite_count(feature)
            width = n_columns(feature)
            power = k - 1 if structure.link is Link.IDENTITY else k
            for value in theta[position : position + width]:
                out.append(value * c**power)
            position += width
        return tuple(out), True


def _intercept(value: float, link: Link, c: float) -> float:
    if link is Link.IDENTITY:
        return value / c
    return value - math.log(c)


def _rename_intervention(
    intervention: Intervention, rename: Callable[[str], str]
) -> Intervention:
    match intervention:
        case ForceEvents(times=times, marks=marks):
            if not marks:
                return intervention
            return ForceEvents(times, {rename(k): v for k, v in sorted(marks.items())})
        case InjectMarks(channel=channel):
            return replace(intervention, channel=rename(channel))
        case ClampRate():
            return intervention
        case Compose(parts=parts):
            if not parts:
                return intervention
            return Compose(tuple(_rename_intervention(p, rename) for p in parts))
        case _:
            return intervention


def make_view(
    condition: Condition,
    channels: tuple[ChannelSpec, ...],
    anon_channels: Mapping[str, str],
) -> AgentView:
    """The view for a condition. ``anon_channels`` maps native -> anonymised."""
    if condition == "named":
        return AgentView("named", channels, None)
    if condition != "anon":
        raise ViewError(f"condition must be 'named' or 'anon', got {condition!r}")
    missing = sorted({c.name for c in channels} - set(anon_channels))
    if missing:
        raise ViewError(f"no anonymised name for channel(s) {missing}")
    used = {c.name: anon_channels[c.name] for c in channels}
    return AgentView("anon", channels, make_name_map(used, catalogue.names()))
