"""Policy mode of the tool layer: the framework chooses the experiments (P4 Δ11).

SPEC §4.2 holds proposal fixed and varies only who chooses the evidence:
random, EIG over the entertained set, FSD, or the agent. In policy mode an
AG-c agent still decides *whether and when* to run an experiment, but
``run_experiment`` takes no design. On each call (decision ``k`` is the index
of the experiment about to run) :meth:`PolicyMode.decide`

1. fits the latest belief's structures (the favoured one and its rivals, at
   most :data:`~sciagent.systems.v2.policies.ENTERTAINED_MAX`) on all the data
   so far: ``obs`` and every experiment that left a dataset, natively, with
   the lab's fit configuration. These fits are framework-internal: uncharged,
   never shown, not memoised with the agent's fits. The random policy does
   not read an entertained set, so it fits nothing;
2. asks the committed policy core (:mod:`sciagent.systems.v2.policies`) for a
   (design, diagnostic) cell of the menus, seeded by
   ``policy_seed(seed, k, ...)`` with the investigation seed;
3. hands the chosen native experiment back to the lab, which runs it through
   the same path as an agent-chosen design, so it is charged, sealed against
   predictions, truncated and summarised exactly the same way.

**What the agent sees** is the design (in its names and units,
:meth:`~sciagent.investigation.view.AgentView.experiment_json`) and the
chosen diagnostic with its frozen arguments (in its names and units) and its
value on the new dataset. Never an EIG or FSD value, a weight, a BIC of the
entertained set or the menu ranking (invariant 2). The decision itself
(:attr:`PolicyDecision.record`: the cell, the entertained set with its BICs
and weights, the score table and, for EIG, the
:class:`~sciagent.systems.v2.policies.EIGSummary`) goes in the tool output's
``record``, which is framework-only; it is digested, so replay recomputes and
checks it.

**Menus.** :func:`~sciagent.systems.v2.policies.design_menu` and
:func:`~sciagent.systems.v2.policies.diagnostic_menu`, frozen on the native
observational log once per lab. :class:`PolicySettings` may restrict both (by
name, menu order kept) and the replicates; the campaign uses the defaults, and
the restrictions exist so tests stay fast. They are recorded with the
investigation, so replay rebuilds the same menus. The agent's diagnostic is
the native one translated, not re-frozen on the agent's data: time arguments
times c (:func:`~sciagent.diagnostics.catalogue.rescale_args`), channel
arguments renamed. (The agent's channels are sorted by agent name, so the two
menus need not share an order; translating the chosen cell avoids relying on
one.)

**Stops and truncation.** The policy's own simulations keep WP5's semantics:
a replicate that stops, past
:data:`~sciagent.systems.v2.policies.POLICY_MAX_EVENTS` or on a runaway, is
*undefined* in its predictive. The real experiment follows the world (Δ2): it
is truncated at the world's cap and kept, and the shown value is read on the
agent's analysis window ``[0, τ]``, as every agent-side statistic is. The two
differ only for designs that reach the cap: there the policy predicted
"undefined" and the world returns a truncated dataset. A run the world stops
(a runaway guard) shows no value.

**Fallback.** If no structure of the belief can be fitted and certified, EIG
and FSD have no entertained set; the decision is then the random policy's
draw for that decision (:func:`random_cell`), and ``fallback`` in the record
says so. The agent sees an ordinary result.

The truth never reaches this module: it reads the world's public datasets
only (``tests/investigation`` checks the names statically), and the
truth-aware FSD of control 5 (``policy_controls``) is never imported here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sciagent.core.errors import SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.diagnostics.catalogue import ArgKind, ArgValue
from sciagent.glm.data import Dataset, EventLog
from sciagent.glm.fit import FitConfig
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.interventions import Experiment
from sciagent.glm.simulate import MarkSampler
from sciagent.investigation.prompts import PolicyName
from sciagent.investigation.view import AgentView
from sciagent.scoring.seeds import generator, policy_seed
from sciagent.systems.v2.policies import (
    ENTERTAINED_MAX,
    POLICY_HORIZON,
    RANDOM_INDEX,
    REPLICATES,
    DecisionContext,
    DiagnosticChoice,
    EIGPolicy,
    EIGSummary,
    EntertainedSet,
    FSDPolicy,
    Menu,
    Policy,
    PolicyError,
    RandomPolicy,
    design_menu,
    diagnostic_menu,
    fit_entertained,
)
from sciagent.systems.v2.search import certified_fit

__all__ = [
    "DEFAULT_SETTINGS",
    "NEEDS_MARKS",
    "PolicyDecision",
    "PolicyMode",
    "PolicyModeError",
    "PolicySettings",
    "make_policy",
    "policy_config",
    "random_cell",
]

#: Policies that simulate fitted models, so need the environment's mark law.
NEEDS_MARKS: Final[tuple[PolicyName, ...]] = ("EIG", "FSD")


class PolicyModeError(SciAgentError):
    """Policy mode is misconfigured (framework side; never an agent act)."""


def _names(value: object, what: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list | tuple) or not value:
        raise PolicyModeError(f"{what} must be None or a non-empty list of names")
    names = tuple(value)
    if not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise PolicyModeError(f"{what} must be distinct names, got {names!r}")
    return names


@dataclass(frozen=True)
class PolicySettings:
    """Replicates and optional menu restrictions (by name, menu order kept).

    The defaults are the campaign's: ``REPLICATES`` replicates and the whole
    menus. Recorded with the investigation (:meth:`as_json`) and part of
    :func:`policy_config`.
    """

    replicates: int = REPLICATES
    designs: tuple[str, ...] | None = None
    diagnostics: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        r = self.replicates
        if isinstance(r, bool) or not isinstance(r, int) or r < 2:
            raise PolicyModeError(f"replicates must be an integer >= 2, got {r!r}")
        object.__setattr__(self, "designs", _names(self.designs, "designs"))
        diagnostics = _names(self.diagnostics, "diagnostics")
        if diagnostics is not None:
            unknown = [n for n in diagnostics if n not in catalogue.names()]
            if unknown:
                raise PolicyModeError(f"no catalogue diagnostics {unknown}")
        object.__setattr__(self, "diagnostics", diagnostics)

    def as_json(self) -> dict[str, Any]:
        return {
            "replicates": self.replicates,
            "designs": None if self.designs is None else list(self.designs),
            "diagnostics": None if self.diagnostics is None else list(self.diagnostics),
        }

    @staticmethod
    def from_json(data: Mapping[str, Any]) -> PolicySettings:
        designs = data.get("designs")
        diagnostics = data.get("diagnostics")
        return PolicySettings(
            replicates=data["replicates"],
            designs=None if designs is None else tuple(designs),
            diagnostics=None if diagnostics is None else tuple(diagnostics),
        )


DEFAULT_SETTINGS: Final = PolicySettings()


def make_policy(name: PolicyName, seed: int, settings: PolicySettings) -> Policy:
    """The committed core's policy for ``name``."""
    match name:
        case "random":
            return RandomPolicy(seed)
        case "EIG":
            return EIGPolicy(seed, replicates=settings.replicates)
        case "FSD":
            return FSDPolicy(seed, replicates=settings.replicates)


def policy_config(name: PolicyName, settings: PolicySettings = DEFAULT_SETTINGS) -> str:
    """Canonical JSON of everything in policy mode that changes a choice except
    the seed: the core policy's own config, the settings, the entertained-set
    cap, the design horizon and the fallback (P4 Δ11 decision 16)."""
    return json.dumps(
        {
            "policy": json.loads(make_policy(name, 0, settings).config),
            "settings": settings.as_json(),
            "entertained_max": ENTERTAINED_MAX,
            "design_horizon": POLICY_HORIZON,
            "fallback": "random",
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def random_cell(seed: int, decision: int, menu: Menu) -> tuple[int, int]:
    """:class:`~sciagent.systems.v2.policies.RandomPolicy`'s draw: one integer
    over the cells from ``policy_seed(seed, decision, RANDOM_INDEX, 0)``.

    It needs no entertained set, so the random policy and the fallback use it
    directly (``tests/investigation`` checks it against ``RandomPolicy``).
    """
    n_d = len(menu.diagnostics)
    rng = generator(policy_seed(seed, decision, RANDOM_INDEX, 0))
    e, d = divmod(int(rng.integers(len(menu.designs) * n_d)), n_d)
    return e, d


def _json_value(value: ArgValue) -> ArgValue:
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)
    return float(value)


def _eig_json(summary: EIGSummary) -> dict[str, Any]:
    return {
        "weights": list(summary.weights),
        "entropy": summary.entropy,
        "max_eig": summary.max_eig,
        "tolerance": summary.tolerance,
        "tied": summary.tied,
        "degenerate": summary.degenerate,
    }


def _entertained_json(entertained: EntertainedSet) -> dict[str, Any]:
    return {
        "names": [h.name for h in entertained.hypotheses],
        "bics": [h.bic for h in entertained.hypotheses],
        "weights": list(entertained.weights),
        "skipped": list(entertained.skipped),
    }


@dataclass(frozen=True)
class PolicyDecision:
    """One decision: the native experiment to run, the design and diagnostic
    to show (in the agent's names and units) and the framework-only record."""

    experiment: Experiment
    design: dict[str, object]
    diagnostic: DiagnosticChoice
    record: dict[str, Any]


class PolicyMode:
    """The policy, its frozen menus and its inputs, for one lab."""

    def __init__(
        self,
        name: PolicyName,
        seed: int,
        settings: PolicySettings,
        channels: tuple[ChannelSpec, ...],
        view: AgentView,
        observational: Dataset,
        marks: MarkSampler | None,
        fit_config: FitConfig,
    ) -> None:
        if name in NEEDS_MARKS and marks is None:
            raise PolicyModeError(
                f"policy {name!r} simulates fitted models and needs the "
                "environment's mark law (EnvironmentSpec.marks)"
            )
        self.name: PolicyName = name
        self.seed = seed
        self.settings = settings
        self.channels = channels
        self.view = view
        self.observational = observational
        self.marks = marks
        self.fit_config = fit_config
        self.policy = make_policy(name, seed, settings)
        designs = design_menu(channels)
        if settings.designs is not None:
            known = [d.name for d in designs]
            unknown = [n for n in settings.designs if n not in known]
            if unknown:
                raise PolicyModeError(f"no menu designs {unknown}; the menu is {known}")
            designs = tuple(d for d in designs if d.name in settings.designs)
        diagnostics = diagnostic_menu(observational.log, channels)
        if settings.diagnostics is not None:
            diagnostics = tuple(
                d for d in diagnostics if d.name in settings.diagnostics
            )
        try:
            self.menu = Menu(designs, diagnostics)
        except PolicyError as error:
            raise PolicyModeError(str(error)) from None

    @property
    def config(self) -> str:
        return policy_config(self.name, self.settings)

    def _agent_diagnostic(self, native: DiagnosticChoice) -> DiagnosticChoice:
        """The native frozen diagnostic in the agent's names and units."""
        kinds = {a.name: a.kind for a in catalogue.get(native.name).args}
        scaled = catalogue.rescale_args(
            native.name, dict(native.args), self.view.factor
        )
        args: dict[str, ArgValue] = {}
        for key, value in scaled.items():
            if kinds.get(key) is ArgKind.CHANNEL:
                args[key] = self.view.channel(str(value))
            else:
                args[key] = value
        return DiagnosticChoice(native.name, tuple(sorted(args.items())))

    def _entertain(
        self,
        candidates: Sequence[tuple[str, Structure]],
        datasets: Sequence[Dataset],
    ) -> tuple[EntertainedSet | None, str | None]:
        """Step 1: the entertained set, or None and why (the fallback)."""
        if self.marks is None:
            raise PolicyModeError(f"policy {self.name!r} has no mark law")
        try:
            entertained = fit_entertained(
                candidates,
                datasets,
                self.channels,
                self.marks,
                fitter=certified_fit,
                config=self.fit_config,
            )
        except PolicyError as error:
            return None, f"random: {error}"
        return entertained, None

    def decide(
        self,
        decision: int,
        candidates: Sequence[tuple[str, Structure]],
        datasets: Sequence[Dataset],
    ) -> PolicyDecision:
        """Choose decision ``decision``'s cell (module docstring, steps 1-2).

        Raises :class:`~sciagent.systems.v2.policies.PolicyError` only for a
        fault of the core; a belief none of whose structures can be fitted
        falls back to the random draw.
        """
        entertained: EntertainedSet | None = None
        fallback: str | None = None
        if self.name != "random":
            entertained, fallback = self._entertain(candidates, datasets)
        scores: tuple[tuple[float, ...], ...] | None = None
        summary: EIGSummary | None = None
        if entertained is None:
            e, d = random_cell(self.seed, decision, self.menu)
        else:
            context = DecisionContext(
                self.channels, self.observational, entertained, self.menu
            )
            choice = self.policy.choose(context, decision)
            e, d = choice.design_index, choice.diagnostic_index
            scores, summary = choice.scores, choice.eig
        if scores is not None and not all(
            math.isfinite(x) for row in scores for x in row
        ):
            raise PolicyError("the score table is not finite")
        design = self.menu.designs[e]
        native = self.menu.diagnostics[d]
        record: dict[str, Any] = {
            "policy": self.name,
            "decision": decision,
            "config": self.policy.config,
            "design": {"index": e, "name": design.name},
            "diagnostic": {
                "index": d,
                "name": native.name,
                "args": {k: _json_value(v) for k, v in native.args},
            },
            "entertained": None
            if entertained is None
            else _entertained_json(entertained),
            "scores": None if scores is None else [list(row) for row in scores],
            "eig": None if summary is None else _eig_json(summary),
            "fallback": fallback,
        }
        return PolicyDecision(
            design.experiment,
            self.view.experiment_json(design.experiment),
            self._agent_diagnostic(native),
            record,
        )

    def value(
        self, decision: PolicyDecision, log: EventLog | None
    ) -> tuple[float | None, str | None]:
        """The chosen diagnostic on the agent's (clipped) log of the new data.

        Its arguments are frozen, so no log-relative bound is checked (as in
        the policy's own simulations and the prediction ledger). ``(None,
        why)`` when it cannot be computed.
        """
        if log is None:
            return None, "nothing was observed before the cut"
        choice = decision.diagnostic
        try:
            value = catalogue.compute(
                choice.name,
                log,
                self.view.channels,
                dict(choice.args),
                check_bounds=False,
            )
        except catalogue.DiagnosticError as error:
            return None, str(error)
        return value, None
