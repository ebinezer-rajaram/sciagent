"""The units of a campaign, their content addresses, and the order they run in.

**Units.** A unit is one (truth, seed, system). Non-LLM systems (SPEC §4.1):
ORACLE, B-lib, B-np, B-rand, B-sym, B-sparse, the planted-hint control and the
B-sym at 10xF control (:data:`B_SYM_10F`, run on :attr:`CampaignConfig.control_seeds`
only). LLM arms are ``<arm>-<letter>/<condition>``, e.g. ``AG-c-S/named``.
:attr:`CampaignConfig.only_truths` narrows a config to some truths of the
campaign, as the budget sweep needs (:mod:`sciagent.campaign.p4plan`).

**A budget in a name.** A row key has no budget field, so a system at a budget
other than a plan's main one carries it in its name: :data:`B_SYM_F80`, and
:attr:`LLMArm.budget`. :class:`CampaignConfig` refuses a name that misstates its
budget. Neither moves an address that exists: B-sym@F=80 is a new system with
its own key, and an arm with no budget keeps its name and text.

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

- ``env_version``: the environment name, the SHA-256 of the domain's own text
  (:attr:`CampaignEnvironment.domain_text`) and ``FIT_VERSION``, last;
- ``config``: the unit kind, system, truth id and the SHA-256 of the truth's
  record, the budgets, the fit configuration's key and the SHA-256 of the
  system's full configuration text (kept verbatim in the cell's detail);
- ``data_version``: :data:`DATA_VERSION` (the world's observational horizon, the
  scorer's held-out horizon and the digest of the data's constants);
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

**The B-sym policy arms and the telling unit** (SPEC §4.2; P4 Δ11, WP9):
:data:`POLICY_UNITS`, run only when named in ``CampaignConfig.baselines`` (the
default plan is unchanged). ``B-sym+{random,EIG,FSD}`` run B-sym in two epochs
around ``experiments`` policy-chosen experiments
(:mod:`sciagent.systems.v2.policy_search`) on the (truth, seed)'s world;
``B-sym+truthFSD`` is the control-5 arm (FSD against the truth, with B-lib's
pick as the parametric side); ``telling`` computes the (truth, seed)'s telling
diagnostic (:mod:`sciagent.scoring.telling`). Their configuration text holds
the policy's own config (canonical JSON, no seed, no truth; P4 Δ11 decision 16)
and :class:`PolicyArmSettings`, so a change to either moves their addresses and
never an existing unit's. The telling unit's key has ``kind = "telling"``.

**Text read from what runs** (F5). ORACLE, B-lib, B-np, B-rand and the policy
arms are built by one constructor shared with ``execute.py``
(:func:`oracle_system`, :func:`b_lib_system`, :func:`b_np_system`,
:func:`b_rand_system`, :func:`policy_search_system`), and their text reads that
object, so a changed default moves the address. :data:`METRIC_VERSION` and
:data:`DATA_VERSION` end with the digests of :mod:`sciagent.campaign.versions`.

**The pilot's store is not read from here on.** F5 moved every baseline and
score address, as its entry said it would (LOG 2026-11-05): baselines and scores
of the pilot are read only from the commit that reads them. The pilot's
sessions can still be found by enumeration (:mod:`sciagent.campaign.rescore`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Final, Literal

import numpy as np

from sciagent.campaign.versions import data_digest, metric_digest
from sciagent.campaign.versions import render as render_value
from sciagent.core.errors import SciAgentError
from sciagent.core.program import stable_key
from sciagent.core.types import DataVersion, EnvVersion, FrozenDict, MetricVersion, Seed
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FIT_VERSION, FitConfig
from sciagent.glm.grammar import ChannelSpec, Link, Structure
from sciagent.glm.interventions import Experiment
from sciagent.glm.simulate import Coefficients, MarkSampler, PsiAssignment
from sciagent.glm.syntax import render
from sciagent.harness.live import HARNESS_VERSION
from sciagent.harness.record import canonical_json
from sciagent.investigation.policy import PolicySettings, policy_config
from sciagent.investigation.prompts import (
    PROMPT_VARIANTS,
    Arm,
    PromptError,
    PromptVariant,
    as_policy,
    as_variant,
    check_policy_arm,
)
from sciagent.investigation.runner import (
    EnvironmentSpec,
    rendered_config,
    run_sandbox_limits,
    sandbox_json,
)
from sciagent.investigation.tools import FIT_CONFIG as AGENT_FIT_CONFIG
from sciagent.investigation.tools import python_tool_json
from sciagent.investigation.view import Condition
from sciagent.investigation.world import MAX_EVENTS as WORLD_MAX_EVENTS
from sciagent.investigation.world import OBSERVATIONAL_HORIZON, TruthSpec
from sciagent.registry.store import ExperimentKey
from sciagent.sandbox import (
    HANDOFF_HELPER_SHA256,
    SandboxImageError,
    SandboxLimits,
    image_tag,
)
from sciagent.sandbox.dataset import format_sha256 as dataset_format_sha256
from sciagent.sandbox.image import SANDBOX_CONTEXT
from sciagent.sandbox.runner import canonical_run_argv
from sciagent.scoring import battery
from sciagent.scoring.battery import Simulator
from sciagent.scoring.heldout import HELDOUT_HORIZON
from sciagent.scoring.seeds import POLICY_SEED_RULE, SEARCH_SEED_TAG
from sciagent.scoring.telling import TELLING_REPLICATES
from sciagent.systems.v2.policies import (
    BIC_WEIGHTS,
    ENTERTAINED_MAX,
    POLICY_HORIZON,
    POLICY_MAX_EVENTS,
    REPLICATES,
    EIGPolicy,
    FSDPolicy,
    Policy,
    RandomPolicy,
    Weighting,
)
from sciagent.systems.v2.policy_controls import TruthFSDPolicy
from sciagent.systems.v2.policy_search import PolicySearch, epoch_budgets
from sciagent.systems.v2.search import BRand, GPConfig, StructureSampler
from sciagent.systems.v2.sparse import SparseConfig
from sciagent.systems.v2.systems import (
    BLib,
    BNp,
    GrammarMember,
    LibraryEntry,
    ModelMember,
    Oracle,
)

ORACLE: Final = "ORACLE"
B_LIB: Final = "B-lib"
B_NP: Final = "B-np"
B_RAND: Final = "B-rand"
B_SYM: Final = "B-sym"
B_SPARSE: Final = "B-sparse"
PLANTED: Final = "planted-hint"
B_SYM_10F: Final = "B-sym@10F"
#: B-sym at the sweep's largest fit budget (P4-PROPOSAL §4). A config running it
#: must have this many fits, so the name cannot misstate the budget. It is no
#: default baseline: it runs only when a config names it.
B_SYM_F80_FITS: Final = 80
B_SYM_F80: Final = "B-sym@F=80"

#: Every non-LLM system, in report order.
BASELINES: Final = (ORACLE, B_LIB, B_NP, B_RAND, B_SYM, B_SPARSE, PLANTED, B_SYM_10F)

#: The B-sym policy arms (SPEC §4.2; P4 Δ11) and control 5's truth-aware arm.
B_SYM_RANDOM: Final = "B-sym+random"
B_SYM_EIG: Final = "B-sym+EIG"
B_SYM_FSD: Final = "B-sym+FSD"
B_SYM_TRUTH_FSD: Final = "B-sym+truthFSD"
#: The (truth, seed)'s telling diagnostic (SPEC §4.2; P4 Δ11 choice 12).
TELLING: Final = "telling"
#: Each policy arm's policy.
ARM_POLICIES: Final = {
    B_SYM_RANDOM: "random",
    B_SYM_EIG: "EIG",
    B_SYM_FSD: "FSD",
    B_SYM_TRUTH_FSD: "truthFSD",
}
POLICY_ARMS: Final = (B_SYM_RANDOM, B_SYM_EIG, B_SYM_FSD, B_SYM_TRUTH_FSD)
#: Units run only when named in ``CampaignConfig.baselines`` (module docstring).
POLICY_UNITS: Final = (*POLICY_ARMS, TELLING)
#: Every system ``CampaignConfig.baselines`` may name.
KNOWN_BASELINES: Final = (*BASELINES, *POLICY_UNITS, B_SYM_F80)

#: The scorer's version: held-out gap, structure, battery, efficiency curve.
#: Its digest covers every value constant a system or the scorer reads
#: (:mod:`sciagent.campaign.versions`, F5), so a changed constant moves it by
#: itself; bump the label when scoring *code* changes. /2 (2026-10-03): the
#: control-2 rank rule, the gap-closed noise flag and the ORACLE underfit
#: requirement of control 1 (P4-PROPOSAL Δ10, T4, §2.4) change what is read
#: from a score, so fit/5 scores are addressed apart from fit/4's.
METRIC_VERSION: Final = (
    f"sciagent.campaign.score/2|battery:{battery.BATTERY_HORIZON}"
    f"x{battery.REPLICATES}cap{battery.BATTERY_MAX_EVENTS}"
    f"|constants:{metric_digest()}"
)
#: The data every unit sees: the world's observational log and the held-out log.
#: Its digest covers their constants (:mod:`sciagent.campaign.versions`).
DATA_VERSION: Final = (
    f"world-obs:{OBSERVATIONAL_HORIZON}|heldout:{HELDOUT_HORIZON}|world/1"
    f"|constants:{data_digest()}"
)

#: ORACLE's text is built on this: the truth's own structure is in no text.
_NO_STRUCTURE: Final = Structure((), Link.IDENTITY)

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
    #: The split the truth's record belongs to (``TruthRecord.split``). Not
    #: addressed (the digest hashes the record, which holds it): the guard of
    #: :mod:`sciagent.campaign.freeze` reads it.
    split: str

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
    #: The domain's own text for its channels and mark law, derived by the
    #: environment; its digest enters the environment version of every key.
    domain_text: str
    investigation: EnvironmentSpec | None = field(default=None, compare=False)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyArmSettings:
    """The B-sym policy arms' and the telling unit's settings (all addressed).

    The defaults are the policy core's (``systems/v2/policies.py``) and
    ``scoring/telling.py``'s. ``max_events`` is the event cap of the policies'
    own simulations (past it a replicate is undefined): the lever on FSD's cost
    on exploding designs (handoff 2026-10-06, decision 3), left at the core's
    default until that decision is taken. ``weighting`` is the EIG policy's
    (BIC weights, or tempered; handoff decision 2). ``entertained`` is the
    entertained set's size. The telling unit has its own replicates and cap.
    """

    replicates: int = REPLICATES
    max_events: int = POLICY_MAX_EVENTS
    weighting: Weighting = BIC_WEIGHTS
    entertained: int = ENTERTAINED_MAX
    telling_replicates: int = TELLING_REPLICATES
    telling_max_events: int = POLICY_MAX_EVENTS

    def __post_init__(self) -> None:
        for name in ("replicates", "telling_replicates"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise CampaignError(f"{name} must be an integer ≥ 2, got {value!r}")
        for name in ("max_events", "telling_max_events"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise CampaignError(f"{name} must be an integer ≥ 1, got {value!r}")
        if not 1 <= self.entertained <= ENTERTAINED_MAX:
            raise CampaignError(
                f"entertained must lie in 1..{ENTERTAINED_MAX}, got {self.entertained}"
            )


class _ConfigOnly:
    """Stands in for the truth and B-lib's pick when only a policy's
    configuration is read (it never simulates)."""

    def simulate_experiment(
        self,
        experiment: Experiment,
        rng: np.random.Generator,
        *,
        label: str = "experiment",
    ) -> Dataset:
        raise CampaignError("a configuration placeholder cannot simulate")


def session_policy_settings(settings: PolicyArmSettings) -> PolicySettings:
    """The policy settings of an AG-c-S policy session: the arm settings'
    replicates, event cap and EIG weighting (as :func:`build_policy` gives the
    B-sym policy arms), and the whole menus. The entertained-set size and the
    telling unit's settings are not policy mode's."""
    return PolicySettings(
        replicates=settings.replicates,
        max_events=settings.max_events,
        weighting=settings.weighting,
    )


def build_policy(
    system: str,
    seed: int,
    settings: PolicyArmSettings,
    *,
    truth: Simulator | None = None,
    comparator: Simulator | None = None,
) -> Policy:
    """The policy of a B-sym policy arm; ``seed`` is the (truth, seed)'s key
    (:func:`~sciagent.scoring.seeds.truth_policy_seed`), not the investigation
    seed.

    ``truth`` and ``comparator`` (B-lib's pick on the observational log) are
    required by, and read only by, ``B-sym+truthFSD``: the truth enters there,
    at the caller (``execute.py``), as it does for ORACLE.
    """
    match ARM_POLICIES.get(system):
        case "random":
            return RandomPolicy(seed)
        case "EIG":
            return EIGPolicy(
                seed,
                replicates=settings.replicates,
                max_events=settings.max_events,
                weighting=settings.weighting,
            )
        case "FSD":
            return FSDPolicy(
                seed, replicates=settings.replicates, max_events=settings.max_events
            )
        case "truthFSD":
            if truth is None or comparator is None:
                raise CampaignError(
                    f"{system} needs the truth and B-lib's pick as its comparator"
                )
            return TruthFSDPolicy(
                truth,
                seed,
                comparator=comparator,
                replicates=settings.replicates,
                max_events=settings.max_events,
            )
    raise CampaignError(f"{system!r} is not a policy arm")


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
    Sonnet letter: its system name is ``AG-c-S/named+policy=EIG``. The condition
    is ``named``, ``rescaled`` (P4 Δ9) or ``anon``.

    ``budget`` is the ``(experiments, fits)`` a swept arm states in its name
    (module docstring; ``None``: it states none). The session's text holds the
    config's budgets already, so this enters the key through the name alone, and
    :class:`CampaignConfig` refuses one that is not its own.
    """

    arm: Arm
    condition: Condition
    prompt_variant: PromptVariant = "v1"
    policy: str | None = None
    budget: tuple[int, int] | None = None

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
        variant, ``+policy=<name>`` for a policy and ``+E=<e>,F=<f>`` for a
        stated budget (the pilot's arms keep their names)."""
        name = f"{self.arm}-{letter}/{self.condition}"
        if self.prompt_variant != "v1":
            name += f"+{self.prompt_variant}"
        if self.policy is not None:
            name += f"+policy={self.policy}"
        if self.budget is not None:
            experiments, fits = self.budget
            name += f"+E={experiments},F={fits}"
        return name


#: The pilot's four arms, named first (the priority order of SPEC P3's brief).
PILOT_ARMS: Final = (
    LLMArm("AG-c", "named"),
    LLMArm("AG-o", "named"),
    LLMArm("AG-c", "anon"),
    LLMArm("AG-o", "anon"),
)

#: The prompt variant P4's arms are frozen on (Δ6: the owner's (A), LOG 2026-10-29).
P4_PROMPT_VARIANT: Final[PromptVariant] = "link-neutral"
#: The pilot's arms on the frozen variant. :data:`PILOT_ARMS` and the field
#: default of :attr:`LLMArm.prompt_variant` stay ``v1``: they name the sessions
#: the pilot ran, which the rescore finds under those names. A campaign outside
#: the dev split cannot run anything else (:mod:`sciagent.campaign.freeze`).
P4_ARMS: Final = tuple(
    replace(arm, prompt_variant=P4_PROMPT_VARIANT) for arm in PILOT_ARMS
)


#: Δ9's arms: names kept, time as anon has it, both tiers. On Sonnet only
#: (decision 11, A), so they are not in :data:`P4_ARMS`, the grid every model runs.
P4_RESCALED_ARMS: Final = (
    LLMArm("AG-c", "rescaled", P4_PROMPT_VARIANT),
    LLMArm("AG-o", "rescaled", P4_PROMPT_VARIANT),
)
#: P4's Sonnet config: the grid, then Δ9's arms, so they add units after each
#: seed's grid and reorder none.
P4_SONNET_ARMS: Final = (*P4_ARMS, *P4_RESCALED_ARMS)


def system_variant(system: str) -> PromptVariant:
    """The prompt variant an LLM system's name states (:meth:`LLMArm.system`)."""
    for part in system.split("+")[1:]:
        if part in PROMPT_VARIANTS:
            return as_variant(part)
    return "v1"


@dataclass(frozen=True)
class CampaignConfig:
    """Budgets and settings shared by every unit (all of it is addressed)."""

    seeds: tuple[int, ...] = (1, 2, 3)
    control_seeds: tuple[int, ...] = (1,)
    #: Run B-sym@10F on the first ``control_truths`` truths (in the order the
    #: campaign is given them) only; None: every truth. Not addressed: it
    #: selects which units run, never what a unit computes.
    control_truths: int | None = None
    #: Run on these truths of the campaign only, in the order the campaign is
    #: given them; None: every truth. Not addressed, as ``control_truths``. A
    #: named truth the campaign lacks is refused (:func:`baseline_units`), and
    #: unlike ``control_truths`` the units do not depend on the order given.
    only_truths: tuple[str, ...] | None = None
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
    #: The B-sym policy arms' and the telling unit's settings; addressed in
    #: those units only.
    policy: PolicyArmSettings = field(default_factory=PolicyArmSettings)

    def __post_init__(self) -> None:
        if not self.seeds or len(set(self.seeds)) != len(self.seeds):
            raise CampaignError("seeds must be non-empty and distinct")
        if not set(self.control_seeds) <= set(self.seeds):
            raise CampaignError("control seeds must be campaign seeds")
        unknown = sorted(set(self.baselines) - set(KNOWN_BASELINES))
        if unknown:
            raise CampaignError(f"unknown baseline systems {unknown}")
        if set(self.baselines) & set(POLICY_ARMS) and self.fits < 2:
            raise CampaignError("the B-sym policy arms need fits ≥ 2 (two epochs)")
        if self.control_truths is not None and self.control_truths < 0:
            raise CampaignError("control_truths must be ≥ 0")
        chosen = self.only_truths
        if chosen is not None and (not chosen or len(set(chosen)) != len(chosen)):
            raise CampaignError("only_truths must be non-empty and distinct")
        if self.fits < 1 or self.experiments < 0 or self.control_factor < 1:
            raise CampaignError("budgets must be positive")
        if B_SYM_F80 in self.baselines and self.fits != B_SYM_F80_FITS:
            raise CampaignError(
                f"{B_SYM_F80} runs at {B_SYM_F80_FITS} fits, not {self.fits}"
            )
        for arm in self.llm_arms:
            if arm.budget not in (None, (self.experiments, self.fits)):
                raise CampaignError(
                    f"{arm.system(self.llm_letter)} states a budget this config "
                    f"does not run: experiments={self.experiments}, fits={self.fits}"
                )
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
    return stable_key(f"{truth_id}|{seed}|{SEARCH_SEED_TAG}") % 2**63


# --------------------------------------------------------------------------
# Plan expansion
# --------------------------------------------------------------------------


def _config_truths(truth_ids: Sequence[str], config: CampaignConfig) -> Sequence[str]:
    """The campaign's truths this config runs on, in the order given."""
    if config.only_truths is None:
        return truth_ids
    missing = sorted(set(config.only_truths) - set(truth_ids))
    if missing:
        raise CampaignError(f"only_truths names truths not in the campaign: {missing}")
    chosen = set(config.only_truths)
    return [t for t in truth_ids if t in chosen]


def baseline_units(
    truth_ids: Sequence[str], config: CampaignConfig
) -> tuple[Unit, ...]:
    """Non-LLM units, seed-major then truth, in ``config.baselines`` order.

    B-sym@10F runs on the control seeds, and on the first
    ``config.control_truths`` truths when that is set.
    """
    units: list[Unit] = []
    ids = _config_truths(truth_ids, config)
    k = config.control_truths
    controlled = set(ids if k is None else ids[:k])
    for seed in config.seeds:
        for t in ids:
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
    ids = _config_truths(truth_ids, config)
    units: list[Unit] = []
    for seed in config.seeds:
        for condition in conditions:
            arms = [a for a in config.llm_arms if a.condition == condition]
            for t in ids:
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
                parts.append(f"{name}={render_value(model)}")
    return ";".join(parts)


def oracle_system(
    structure: Structure,
    config: CampaignConfig,
    *,
    psi: PsiAssignment | None = None,
    coefficients: Coefficients | None = None,
) -> Oracle:
    """ORACLE as a unit runs it and as its address text reads it (F5)."""
    return Oracle(
        structure,
        fit_config=config.fit_config,
        truth_psi=psi,
        truth_coefficients=coefficients,
    )


def b_lib_system(env: CampaignEnvironment, config: CampaignConfig) -> BLib:
    """B-lib as a unit runs it and as its address text reads it (F5)."""
    return BLib(env.library, fit_config=config.fit_config)


def b_np_system() -> BNp:
    """B-np as a unit runs it and as its address text reads it (F5)."""
    return BNp()


def b_rand_system(
    env: CampaignEnvironment, config: CampaignConfig, *, seed: int = 0
) -> BRand:
    """B-rand as a unit runs it and as its address text reads it (F5).

    ``seed`` is the unit's search seed, which is no part of the text.
    """
    return BRand(env.prior, budget=config.fits, seed=seed, fit_config=config.fit_config)


def policy_search_system(
    system: str,
    env: CampaignEnvironment,
    config: CampaignConfig,
    policy: Policy,
    *,
    policy_key: int,
    seed: int,
) -> PolicySearch:
    """A B-sym policy arm as a unit runs it and as its address text reads it
    (F5). ``policy_key`` and ``seed`` are the unit's, no part of the text."""
    return PolicySearch(
        env.sym_seeds,
        policy,
        policy_seed=policy_key,
        experiments=config.experiments,
        config=config.gp,
        budget=config.fits,
        seed=seed,
        fit_config=config.fit_config,
        entertained=config.policy.entertained,
        name=system,
    )


def system_config_text(
    system: str, env: CampaignEnvironment, config: CampaignConfig
) -> str:
    """The full configuration of a non-LLM system as text (addressed by hash)."""
    seeds = "|".join(render(s) for s in env.sym_seeds)
    budget = config.fits * (config.control_factor if system == B_SYM_10F else 1)
    match system:
        case "ORACLE":
            # The truth's own fields are placeholders here: a truth enters an
            # address by its digest, and this text is the same for every truth.
            return f"oracle: {render_value(oracle_system(_NO_STRUCTURE, config))}"
        case "B-lib":
            return f"library: {_library_text(b_lib_system(env, config).library)}"
        case "B-np":
            return f"wiener_hopf: {b_np_system().config!r}"
        case "B-rand":
            b_rand = b_rand_system(env, config)
            return (
                f"budget={b_rand.budget}; "
                f"max_draws_factor={b_rand.max_draws_factor}; prior={env.prior_id}"
            )
        case "B-sym" | "B-sym@10F" | "B-sym@F=80" | "planted-hint":
            return f"budget={budget}; gp={config.gp!r}; seeds={seeds}"
        case "B-sparse":
            return f"sparse: {config.sparse!r}"
        case "B-sym+random" | "B-sym+EIG" | "B-sym+FSD" | "B-sym+truthFSD":
            return _policy_arm_text(system, env, config, seeds)
        case "telling":
            s = config.policy
            return (
                f"telling: member=B-lib; library: {_library_text(env.library)}; "
                f"replicates={s.telling_replicates}; "
                f"max_events={s.telling_max_events}; "
                f"design_horizon={POLICY_HORIZON!r}; menus=full, frozen on obs; "
                f"argmax=diagnostic then design, ties to menu order; "
                f"stopped runs dropped"
            )
    raise CampaignError(f"unknown system {system!r}")


def _policy_arm_text(
    system: str, env: CampaignEnvironment, config: CampaignConfig, seeds: str
) -> str:
    placeholder = _ConfigOnly()
    policy = build_policy(
        system, 0, config.policy, truth=placeholder, comparator=placeholder
    )
    search = policy_search_system(system, env, config, policy, policy_key=0, seed=0)
    first, second = epoch_budgets(search.budget)
    text = (
        f"budget={search.budget}; epochs={first}+{second}; "
        f"experiments={search.experiments}; gp={search.config!r}; seeds={seeds}; "
        f"policy={search.policy.config}; policy_seed={POLICY_SEED_RULE}; "
        f"world_cap={WORLD_MAX_EVENTS}, truncating; stopped=used, no data"
    )
    if system == B_SYM_RANDOM:
        return text + "; entertained=none"
    text += (
        f"; entertained=top {search.entertained} of epoch 1 by BIC, refitted on all "
        f"data; design_horizon={POLICY_HORIZON!r}; menus=full, frozen on obs; "
        f"fallback=random"
    )
    if system == B_SYM_TRUTH_FSD:
        text += f"; comparator=B-lib: {_library_text(env.library)}"
    return text


def _key(
    env: CampaignEnvironment,
    config: CampaignConfig,
    seed: int,
    fields: Mapping[str, str],
    metric: str,
) -> ExperimentKey:
    base = {"fit_config": config.fit_config.key()}
    return ExperimentKey(
        env_version=EnvVersion(
            f"{env.name}|domain:{_sha(env.domain_text)}|{FIT_VERSION}"
        ),
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
            "kind": "telling" if unit.system == TELLING else "baseline",
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
    the budgets, the wall limit, the prompt variant and the policy: no truth and
    no seed. A
    policy arm's digest also covers the policy's framework-only configuration
    (:func:`~sciagent.investigation.policy.policy_config`: replicates, event
    cap, weights, tie tolerance, quadrature, menus; P4 Δ11 decision 16) under
    the arm settings (:func:`session_policy_settings`), which the agent never
    sees, so a change to it moves the key too. A non-policy arm's digest is the
    surface's alone, as before.
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
        wall_time_s=config.wall_time_s,
        prompt_variant=arm.prompt_variant,
        policy=arm.policy,
    )
    policy = as_policy(arm.policy)
    if policy is None:
        return _sha(canonical_json(surface))
    return _sha(
        canonical_json(
            {
                "surface": surface,
                "policy_config": policy_config(
                    policy, session_policy_settings(config.policy)
                ),
            }
        )
    )


def _with_sandbox(arm: LLMArm, text: str) -> str:
    """``text``, and for an AG-o arm its sandbox (:func:`session_sandbox_text`):
    the limits :func:`~sciagent.investigation.runner.run` gives it, the
    handoff helper's SHA-256 and the image tag computed from the build context
    (no Docker call). So a change to any of them moves the session's address
    and a stored session is not reused across it. AG-c has no sandbox, and its
    text is ``text`` unchanged."""
    if arm.arm != "AG-o":
        return text
    try:
        image = image_tag(SANDBOX_CONTEXT)
    except SandboxImageError as error:
        raise CampaignError(
            f"cannot name the sandbox image of an AG-o session: {error}"
        ) from error
    sandbox = session_sandbox_text(run_sandbox_limits(), HANDOFF_HELPER_SHA256, image)
    return f"{text}; {sandbox}"


def session_text(arm: LLMArm, config: CampaignConfig, env: CampaignEnvironment) -> str:
    """The full configuration of an LLM session as text (module docstring).

    An AG-o session's text ends with its sandbox (:func:`_with_sandbox`).
    """
    return _with_sandbox(
        arm,
        f"model={config.llm_model}; arm={arm.arm}; condition={arm.condition}; "
        f"prompt_variant={arm.prompt_variant}; "
        f"policy={'none' if arm.policy is None else arm.policy}; "
        f"experiments={config.experiments}; fits={config.fits}; "
        f"max_turns={config.max_turns}; wall_time_s={config.wall_time_s!r}; "
        f"harness={HARNESS_VERSION}; surface={surface_digest(arm, config, env)}",
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
    ran a default arm. Only :mod:`sciagent.campaign.rescore` reads it. It is
    ``session_text`` as v2 had it before the P4 merge, so an AG-o text ends
    with its sandbox (:func:`_with_sandbox`), as the Linux pilot's AG-o
    sessions were addressed; an AG-c text is the Windows pilot's byte for byte.
    """
    return _with_sandbox(
        arm,
        f"model={config.llm_model}; arm={arm.arm}; condition={arm.condition}; "
        f"experiments={config.experiments}; fits={config.fits}; "
        f"max_turns={config.max_turns}; wall_time_s={config.wall_time_s!r}; "
        f"harness={LEGACY_HARNESS_VERSION}",
    )


def session_sandbox_text(limits: SandboxLimits, helper_sha256: str, image: str) -> str:
    """The sandbox and tools an AG-o session's code ran under, as text.

    One JSON object, keys sorted: every limit and the handoff helper's SHA-256
    (as :func:`~sciagent.investigation.runner.sandbox_json` records them; a
    limit typed ``float`` is rendered as a float, so ``60`` and ``60.0`` give
    the same text); the ``docker run`` command line of a call
    (:func:`~sciagent.sandbox.runner.canonical_run_argv`: flags, ``/tmp``,
    ulimits, network, environment); the SHA-256 of the ``/data`` formats
    (:func:`~sciagent.sandbox.dataset.format_sha256`); and the ``python`` and
    notebook tools' caps and output format
    (:func:`~sciagent.investigation.tools.python_tool_json`). Then the image
    tag. Each is computed from the code that acts, not copied from it.
    """
    block = sandbox_json(limits, helper_sha256)
    block["limits"] = _float_fields(limits)
    block["run_argv"] = canonical_run_argv(limits)
    block["data_format_sha256"] = dataset_format_sha256()
    block["python_tool"] = python_tool_json()
    rendered = json.dumps(block, sort_keys=True, separators=(",", ":"))
    return f"sandbox={rendered}; image={image}"


def _float_fields(limits: SandboxLimits) -> dict[str, int | float]:
    """``limits`` as a dict, its ``float`` fields as floats (an ``int`` passed
    for one would otherwise render without the ``.0``)."""
    out: dict[str, int | float] = {}
    for f in fields(limits):
        value = getattr(limits, f.name)
        out[f.name] = float(value) if f.type in ("float", float) else int(value)
    return out


def legacy_session_config_digest(arm: LLMArm, config: CampaignConfig) -> str | None:
    """The SHA-256 of :func:`legacy_session_text_v1`; ``None`` for an arm the
    pilot could not have run (a non-default variant, a policy, a stated budget
    or ``rescaled``)."""
    if arm.prompt_variant != "v1" or arm.policy is not None or arm.budget is not None:
        return None
    if arm.condition == "rescaled":
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
