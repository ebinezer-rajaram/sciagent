"""The units of a campaign, their content addresses, and the order they run in.

**Units.** A unit is one (truth, seed, system). Non-LLM systems (SPEC §4.1):
ORACLE, B-lib, B-np, B-rand, B-sym, B-sparse, the planted-hint control and the
B-sym at 10xF control (:data:`B_SYM_10F`, run on :attr:`CampaignConfig.control_seeds`
only). LLM arms are ``<arm>-<letter>/<condition>``, e.g. ``AG-c-S/named``.

**Data.** Every system on a (truth, seed) sees the same observational dataset:
the one :class:`~sciagent.investigation.world.World` draws for that seed, so a
baseline and an LLM arm are paired on identical data. Non-LLM systems use that
observational log only (no experiments): B-lib, B-rand, B-sym, B-sparse and the
planted hint are defined over observational data (SPEC §4.1), and an
interventional design for them would be a policy (SPEC §4.2), not a baseline.

**Search seeds.** B-rand, B-sym, planted-hint and B-sym@10F draw from one seed
per (truth, seed), :func:`search_seed`. B-sym@10F therefore runs the same
genetic program as B-sym with ten times the budget, which is what the §6.4
control asks: the same search, longer.

**Addresses** (:func:`baseline_key`, :func:`session_key`, :func:`score_key`).
An :class:`~sciagent.registry.store.ExperimentKey` with

- ``env_version``: the environment name and ``FIT_VERSION``;
- ``config``: the unit kind, system, truth id and the SHA-256 of the truth's
  record, the budgets, the fit configuration's key and the SHA-256 of the
  system's full configuration text (kept verbatim in the cell's detail);
- ``data_version``: :data:`DATA_VERSION` (the world's observational horizon and
  the scorer's held-out horizon);
- ``metric_version``: :data:`METRIC_VERSION`;
- ``seed``: the investigation seed.

An LLM unit has two cells: the *session* (the expensive, non-reproducible
investigation; no metric version, so a scorer change never reruns a model) and
its *score* (keyed by the session's address plus :data:`METRIC_VERSION`).

**The session surface** (P4 Δ7). A session's configuration text
(:func:`session_text`) ends with the SHA-256 of the canonical JSON of the
rendered surface (:func:`~sciagent.investigation.runner.rendered_config`: the
system prompt and every tool schema for the arm, condition, budgets and prompt
variant), so an edit to any prompt or schema text gives every session a new
address instead of resuming old sessions under new text. The pilot's
``harness/1`` sessions were addressed by a text without it;
:func:`legacy_session_text_v1` keeps that text, frozen, so the rescore can
still find them (:mod:`sciagent.campaign.rescore`).

**Variants and policies.** An :class:`LLMArm` with a non-default prompt
variant or a policy has its own system name (:meth:`LLMArm.system`), so two
of them never share a unit.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from sciagent.core.errors import SciAgentError
from sciagent.core.program import stable_key
from sciagent.core.types import DataVersion, EnvVersion, FrozenDict, MetricVersion, Seed
from sciagent.glm.fit import FIT_VERSION, FitConfig
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.simulate import MarkSampler
from sciagent.glm.syntax import render
from sciagent.harness.live import HARNESS_VERSION
from sciagent.harness.record import canonical_json
from sciagent.investigation.policy import policy_config
from sciagent.investigation.prompts import (
    Arm,
    PromptError,
    PromptVariant,
    as_policy,
    as_variant,
    check_policy_arm,
)
from sciagent.investigation.runner import EnvironmentSpec, rendered_config
from sciagent.investigation.tools import FIT_CONFIG as AGENT_FIT_CONFIG
from sciagent.investigation.view import Condition
from sciagent.investigation.world import OBSERVATIONAL_HORIZON, TruthSpec
from sciagent.nonparam.wiener_hopf import DEFAULT as WIENER_HOPF_DEFAULT
from sciagent.registry.store import ExperimentKey
from sciagent.scoring import battery
from sciagent.scoring.heldout import HELDOUT_HORIZON
from sciagent.systems.v2.search import GPConfig, StructureSampler
from sciagent.systems.v2.sparse import SparseConfig
from sciagent.systems.v2.systems import GrammarMember, LibraryEntry, ModelMember

ORACLE: Final = "ORACLE"
B_LIB: Final = "B-lib"
B_NP: Final = "B-np"
B_RAND: Final = "B-rand"
B_SYM: Final = "B-sym"
B_SPARSE: Final = "B-sparse"
PLANTED: Final = "planted-hint"
B_SYM_10F: Final = "B-sym@10F"

#: Every non-LLM system, in report order.
BASELINES: Final = (ORACLE, B_LIB, B_NP, B_RAND, B_SYM, B_SPARSE, PLANTED, B_SYM_10F)

#: The scorer's version: held-out gap, structure, battery, efficiency curve.
#: Bump when any scoring code or constant changes. /2 (2026-10-03): the
#: control-2 rank rule, the gap-closed noise flag and the ORACLE underfit
#: requirement of control 1 (P4-PROPOSAL Δ10, T4, §2.4) change what is read
#: from a score, so fit/5 scores are addressed apart from fit/4's.
METRIC_VERSION: Final = (
    f"sciagent.campaign.score/2|battery:{battery.BATTERY_HORIZON}"
    f"x{battery.REPLICATES}cap{battery.BATTERY_MAX_EVENTS}"
)
#: The data every unit sees: the world's observational log and the held-out log.
DATA_VERSION: Final = (
    f"world-obs:{OBSERVATIONAL_HORIZON}|heldout:{HELDOUT_HORIZON}|world/1"
)

type UnitKind = Literal["baseline", "llm-session", "llm-score"]


class CampaignError(SciAgentError):
    """A campaign is misconfigured, or its store is inconsistent."""


# --------------------------------------------------------------------------
# What the environment supplies
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TruthEntry:
    """One truth of the split, as a campaign needs it.

    ``digest`` is the SHA-256 of the truth's canonical record (it enters every
    address); ``spec`` is the simulable truth; the rest is report metadata.
    """

    id: str
    digest: str
    spec: TruthSpec
    in_dictionary: bool
    stratum: str
    nearest_distance: float
    dsl: str

    @property
    def structure(self) -> Structure:
        return self.spec.structure


@dataclass(frozen=True)
class CampaignEnvironment:
    """The environment, injected (``sciagent`` never imports ``environments``).

    ``prior`` is B-rand's structure distribution (the truth prior, SPEC §4.1)
    and ``prior_id`` names it in addresses (a config digest). ``sym_seeds``
    seed B-sym and the planted hint (the library's grammar members).
    ``investigation`` is what the LLM runner needs. Every field but
    ``investigation`` is pickled into worker processes.
    """

    name: str
    channels: tuple[ChannelSpec, ...]
    marks: MarkSampler
    library: tuple[LibraryEntry, ...]
    sym_seeds: tuple[Structure, ...]
    prior: StructureSampler
    prior_id: str
    investigation: EnvironmentSpec | None = field(default=None, compare=False)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


#: What a policy name may hold: it is part of a system name.
_POLICY_NAME: Final = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class LLMArm:
    """One LLM arm: tier and naming condition, on the campaign's model.

    ``prompt_variant`` is the system prompt's variant (P4 Δ6); ``policy`` names
    an experiment-choice policy (P4 Δ11; ``None``: the agent designs its own
    experiments): one of ``random``, ``EIG`` or ``FSD``, on AG-c only. Both
    enter the session key and, when not the default, the system name.
    AG-S-boed (SPEC §4.1) is ``LLMArm("AG-c", "named", policy="EIG")`` on the
    Sonnet letter: its system name is ``AG-c-S/named+policy=EIG``.
    """

    arm: Arm
    condition: Condition
    prompt_variant: PromptVariant = "v1"
    policy: str | None = None

    def __post_init__(self) -> None:
        try:
            as_variant(self.prompt_variant)
        except PromptError as error:
            raise CampaignError(str(error)) from None
        if self.policy is not None and not _POLICY_NAME.fullmatch(self.policy):
            raise CampaignError(
                f"policy name {self.policy!r} must match {_POLICY_NAME.pattern}"
            )
        try:
            check_policy_arm(self.arm, as_policy(self.policy))
        except PromptError as error:
            raise CampaignError(str(error)) from None

    def system(self, letter: str) -> str:
        """``<arm>-<letter>/<condition>``, plus ``+<variant>`` for a non-default
        variant and ``+policy=<name>`` for a policy (the pilot's arms keep
        their names)."""
        name = f"{self.arm}-{letter}/{self.condition}"
        if self.prompt_variant != "v1":
            name += f"+{self.prompt_variant}"
        if self.policy is not None:
            name += f"+policy={self.policy}"
        return name


#: The pilot's four arms, named first (the priority order of SPEC P3's brief).
PILOT_ARMS: Final = (
    LLMArm("AG-c", "named"),
    LLMArm("AG-o", "named"),
    LLMArm("AG-c", "anon"),
    LLMArm("AG-o", "anon"),
)


@dataclass(frozen=True)
class CampaignConfig:
    """Budgets and settings shared by every unit (all of it is addressed)."""

    seeds: tuple[int, ...] = (1, 2, 3)
    control_seeds: tuple[int, ...] = (1,)
    #: Run B-sym@10F on the first ``control_truths`` truths (in the order the
    #: campaign is given them) only; None: every truth. Not addressed: it
    #: selects which units run, never what a unit computes.
    control_truths: int | None = None
    fits: int = 40
    experiments: int = 8
    control_factor: int = 10
    baselines: tuple[str, ...] = BASELINES
    llm_arms: tuple[LLMArm, ...] = PILOT_ARMS
    llm_model: str = "claude-sonnet-5-5"
    llm_letter: str = "S"
    max_turns: int = 100
    wall_time_s: float = 3600.0
    gp: GPConfig = field(default_factory=GPConfig)
    sparse: SparseConfig = field(default_factory=SparseConfig)
    fit_config: FitConfig = field(default_factory=FitConfig)

    def __post_init__(self) -> None:
        if not self.seeds or len(set(self.seeds)) != len(self.seeds):
            raise CampaignError("seeds must be non-empty and distinct")
        if not set(self.control_seeds) <= set(self.seeds):
            raise CampaignError("control seeds must be campaign seeds")
        unknown = sorted(set(self.baselines) - set(BASELINES))
        if unknown:
            raise CampaignError(f"unknown baseline systems {unknown}")
        if self.control_truths is not None and self.control_truths < 0:
            raise CampaignError("control_truths must be ≥ 0")
        if self.fits < 1 or self.experiments < 0 or self.control_factor < 1:
            raise CampaignError("budgets must be positive")
        if self.sparse.workers != 1 or self.fit_config.workers != 1:
            raise CampaignError("units run single-threaded: workers must be 1")
        # Agents' fits always use the lab's FIT_CONFIG (the runner takes no
        # fit configuration), but a session's address records this config's
        # key: any other key would mislabel every session.
        if self.llm_arms and self.fit_config.key() != AGENT_FIT_CONFIG.key():
            raise CampaignError(
                "fit_config differs from investigation.tools.FIT_CONFIG, which "
                "every agent fit uses: an LLM session would be addressed under a "
                "fit configuration it did not run. Use the default fit_config, "
                "or no llm_arms"
            )
        systems = self.llm_systems()
        if len(set(systems)) != len(systems):
            raise CampaignError(f"LLM arms must have distinct systems: {systems}")

    def llm_systems(self) -> tuple[str, ...]:
        return tuple(a.system(self.llm_letter) for a in self.llm_arms)

    def arm_of(self, system: str) -> LLMArm:
        for a in self.llm_arms:
            if a.system(self.llm_letter) == system:
                return a
        raise CampaignError(f"{system!r} is not an LLM arm of this campaign")


@dataclass(frozen=True, order=True)
class Unit:
    """One (truth, seed, system)."""

    truth_id: str
    seed: int
    system: str


def search_seed(truth_id: str, seed: int) -> int:
    """The search systems' RNG seed for (truth, seed) (module docstring)."""
    return stable_key(f"{truth_id}|{seed}|search") % 2**63


# --------------------------------------------------------------------------
# Plan expansion
# --------------------------------------------------------------------------


def baseline_units(
    truth_ids: Sequence[str], config: CampaignConfig
) -> tuple[Unit, ...]:
    """Non-LLM units, seed-major then truth, in ``config.baselines`` order.

    B-sym@10F runs on the control seeds, and on the first
    ``config.control_truths`` truths when that is set.
    """
    units: list[Unit] = []
    k = config.control_truths
    controlled = set(truth_ids if k is None else truth_ids[:k])
    for seed in config.seeds:
        for t in truth_ids:
            for s in config.baselines:
                if s == B_SYM_10F and (
                    seed not in config.control_seeds or t not in controlled
                ):
                    continue
                units.append(Unit(t, seed, s))
    return tuple(units)


def llm_units(truth_ids: Sequence[str], config: CampaignConfig) -> tuple[Unit, ...]:
    """LLM units in priority order: coverage breadth first.

    For each seed in order, for each condition in arm order (named first in
    the pilot), every truth with each arm of that condition interleaved. So
    seed 1 named covers every truth before any anonymised run, and seed 1 is
    complete before seed 2 starts.
    """
    conditions = list(dict.fromkeys(a.condition for a in config.llm_arms))
    units: list[Unit] = []
    for seed in config.seeds:
        for condition in conditions:
            arms = [a for a in config.llm_arms if a.condition == condition]
            for t in truth_ids:
                for a in arms:
                    units.append(Unit(t, seed, a.system(config.llm_letter)))
    return tuple(units)


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _library_text(library: Sequence[LibraryEntry]) -> str:
    parts: list[str] = []
    for entry in library:
        match entry:
            case GrammarMember(name=name, structure=structure):
                parts.append(f"{name}={render(structure)}")
            case ModelMember(name=name, model=model):
                parts.append(f"{name}={type(model).__name__}")
    return ";".join(parts)


def system_config_text(
    system: str, env: CampaignEnvironment, config: CampaignConfig
) -> str:
    """The full configuration of a non-LLM system as text (addressed by hash)."""
    seeds = "|".join(render(s) for s in env.sym_seeds)
    budget = config.fits * (config.control_factor if system == B_SYM_10F else 1)
    match system:
        case "ORACLE":
            return "oracle: fit the true structure"
        case "B-lib":
            return f"library: {_library_text(env.library)}"
        case "B-np":
            return f"wiener_hopf: {WIENER_HOPF_DEFAULT!r}"
        case "B-rand":
            return f"budget={budget}; max_draws_factor=100; prior={env.prior_id}"
        case "B-sym" | "B-sym@10F" | "planted-hint":
            return f"budget={budget}; gp={config.gp!r}; seeds={seeds}"
        case "B-sparse":
            return f"sparse: {config.sparse!r}"
    raise CampaignError(f"unknown system {system!r}")


def _key(
    env: CampaignEnvironment,
    config: CampaignConfig,
    seed: int,
    fields: Mapping[str, str],
    metric: str,
) -> ExperimentKey:
    base = {"fit_config": config.fit_config.key()}
    return ExperimentKey(
        env_version=EnvVersion(f"{env.name}|{FIT_VERSION}"),
        config=FrozenDict[str, str]({**base, **fields}),
        data_version=DataVersion(DATA_VERSION),
        metric_version=MetricVersion(metric),
        seed=Seed(seed),
    )


def baseline_key(
    unit: Unit, truth: TruthEntry, env: CampaignEnvironment, config: CampaignConfig
) -> ExperimentKey:
    """The address of a non-LLM unit's scored result."""
    if unit.truth_id != truth.id:
        raise CampaignError(f"unit truth {unit.truth_id!r} is not {truth.id!r}")
    text = system_config_text(unit.system, env, config)
    return _key(
        env,
        config,
        unit.seed,
        {
            "kind": "baseline",
            "system": unit.system,
            "truth": truth.id,
            "truth_digest": truth.digest,
            "system_config": _sha(text),
        },
        METRIC_VERSION,
    )


def surface_digest(
    arm: LLMArm, config: CampaignConfig, env: CampaignEnvironment
) -> str:
    """SHA-256 of the canonical JSON of the session's rendered surface.

    The surface is :func:`~sciagent.investigation.runner.rendered_config` for
    the environment's channels and anonymised names, the arm, the condition,
    the budgets, the prompt variant and the policy: no truth and no seed. A
    policy arm's digest also covers the policy's framework-only configuration
    (:func:`~sciagent.investigation.policy.policy_config`: replicates, weights,
    tie tolerance, quadrature, menus; P4 Δ11 decision 16), which the agent
    never sees, so a change to it moves the key too. A non-policy arm's digest
    is the surface's alone, as before.
    """
    if env.investigation is None:
        raise CampaignError("the environment has no investigation spec")
    surface = rendered_config(
        env.investigation,
        env.channels,
        arm.arm,
        arm.condition,
        experiments=config.experiments,
        fits=config.fits,
        prompt_variant=arm.prompt_variant,
        policy=arm.policy,
    )
    policy = as_policy(arm.policy)
    if policy is None:
        return _sha(canonical_json(surface))
    return _sha(
        canonical_json({"surface": surface, "policy_config": policy_config(policy)})
    )


def session_text(arm: LLMArm, config: CampaignConfig, env: CampaignEnvironment) -> str:
    """The full configuration of an LLM session as text (module docstring)."""
    return (
        f"model={config.llm_model}; arm={arm.arm}; condition={arm.condition}; "
        f"prompt_variant={arm.prompt_variant}; "
        f"policy={'none' if arm.policy is None else arm.policy}; "
        f"experiments={config.experiments}; fits={config.fits}; "
        f"max_turns={config.max_turns}; wall_time_s={config.wall_time_s!r}; "
        f"harness={HARNESS_VERSION}; surface={surface_digest(arm, config, env)}"
    )


def session_config_digest(
    arm: LLMArm, config: CampaignConfig, env: CampaignEnvironment
) -> str:
    """The SHA-256 of :func:`session_text` (the session key's config field)."""
    return _sha(session_text(arm, config, env))


#: The harness version the pilot's sessions were addressed under.
LEGACY_HARNESS_VERSION: Final = "harness/1"


def legacy_session_text_v1(arm: LLMArm, config: CampaignConfig) -> str:
    """The pilot's (``harness/1``) session text, frozen: do not edit.

    It holds no prompt variant, policy or surface digest; every pilot session
    ran a default arm. Only :mod:`sciagent.campaign.rescore` reads it.
    """
    return (
        f"model={config.llm_model}; arm={arm.arm}; condition={arm.condition}; "
        f"experiments={config.experiments}; fits={config.fits}; "
        f"max_turns={config.max_turns}; wall_time_s={config.wall_time_s!r}; "
        f"harness={LEGACY_HARNESS_VERSION}"
    )


def legacy_session_config_digest(arm: LLMArm, config: CampaignConfig) -> str | None:
    """The SHA-256 of :func:`legacy_session_text_v1`; ``None`` for an arm the
    pilot could not have run (a non-default variant or a policy)."""
    if arm.prompt_variant != "v1" or arm.policy is not None:
        return None
    return _sha(legacy_session_text_v1(arm, config))


def session_key(
    unit: Unit, truth: TruthEntry, env: CampaignEnvironment, config: CampaignConfig
) -> ExperimentKey:
    """The address of an LLM unit's session (no metric version)."""
    if unit.truth_id != truth.id:
        raise CampaignError(f"unit truth {unit.truth_id!r} is not {truth.id!r}")
    if tuple(truth.spec.channels) != tuple(env.channels):
        raise CampaignError(
            f"truth {truth.id!r} has channels {truth.spec.channels}, the "
            f"environment {env.channels}: the session surface is rendered from "
            f"the environment's"
        )
    arm = config.arm_of(unit.system)
    return _key(
        env,
        config,
        unit.seed,
        {
            "kind": "llm-session",
            "system": unit.system,
            "truth": truth.id,
            "truth_digest": truth.digest,
            "session_config": session_config_digest(arm, config, env),
        },
        "none",
    )


def score_key(
    unit: Unit,
    session: ExperimentKey,
    env: CampaignEnvironment,
    config: CampaignConfig,
) -> ExperimentKey:
    """The address of an LLM session's score."""
    return _key(
        env,
        config,
        unit.seed,
        {
            "kind": "llm-score",
            "system": unit.system,
            "truth": unit.truth_id,
            "session": str(session.digest),
        },
        METRIC_VERSION,
    )
