"""Running, recording and replaying one AG-c or AG-o investigation (SPEC §4).

:func:`run` builds the world and the lab for one (truth, arm, condition, seed),
renders the prompts, runs the session through
:func:`sciagent.harness.live.run_investigation`, saves the hash-chained record
and returns an :class:`InvestigationOutcome`. :func:`replay_investigation`
rebuilds the lab from the record alone and re-issues every recorded tool call
(:func:`sciagent.harness.replay.replay`), re-executing sandbox code, so a
changed tool, a numerics drift or a perturbed sandbox result is detected.

What the record's config holds. The harness records ``AgentConfig.as_json()``;
:class:`InvestigationAgentConfig` adds an ``investigation`` field with the
:class:`InvestigationSpec` (environment name, truth id, arm, condition, seed,
budgets, sandbox limits, and in policy mode the policy and its settings).
That is what :func:`layer_factory` rebuilds from.
The config is framework-side: the model sees only the system prompt, the
opening prompt and the tool schemas, none of which carry the truth id. The
truth itself is never in the record; it is resolved by id from the
environment's registry at replay time.

Sandbox run directories are created with :func:`tempfile.mkdtemp` (an opaque
random name) under ``sandbox_root``: ``/proc/self/mountinfo`` shows the
container the host path, so the path must not carry the truth, seed, arm or
condition (LOG 2026-10-01, sandbox). The directory name never reaches a tool
output, so its randomness cannot move a recorded byte unless agent code
prints its own mount table -- which the out-of-bounds scan flags.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from sciagent.anonymise import DEFAULT_FORBIDDEN
from sciagent.diagnostics import catalogue
from sciagent.glm.grammar import ChannelSpec, Structure
from sciagent.glm.simulate import MarkSampler
from sciagent.glm.syntax import parse
from sciagent.harness.errors import (
    HarnessFaultError,
    HermeticityError,
    ServedModelError,
    SessionFailedError,
)
from sciagent.harness.live import AgentConfig, SessionDriver, run_investigation
from sciagent.harness.record import SessionRecord
from sciagent.harness.replay import LayerFactory, ReplayReport, replay
from sciagent.harness.tools import ToolLayer
from sciagent.harness.voids import INFRASTRUCTURE, assess, foreign_reasons
from sciagent.harness.voids import OUT_OF_BOUNDS as OUT_OF_BOUNDS_REASON
from sciagent.investigation.policy import NEEDS_MARKS, PolicySettings
from sciagent.investigation.prompts import (
    Arm,
    PolicyName,
    PromptError,
    PromptVariant,
    as_policy,
    as_variant,
    check_policy_arm,
    opening_prompt,
    system_prompt,
)
from sciagent.investigation.tools import (
    FIT_CONFIG,
    Lab,
    LabError,
    PredictionOutcome,
    tool_specs,
)
from sciagent.investigation.view import Condition, make_view
from sciagent.investigation.world import OBSERVATIONAL, Truth, World
from sciagent.sandbox import (
    HANDOFF_HELPER_SHA256,
    Sandbox,
    SandboxError,
    SandboxLimits,
    ensure_image,
    image_id,
)
from sciagent.scoring.seeds import truth_policy_seed

__all__ = [
    "OUT_OF_BOUNDS",
    "SANDBOX_MEMORY_MB",
    "SANDBOX_WALL_SECONDS",
    "EnvironmentSpec",
    "InvestigationAgentConfig",
    "InvestigationOutcome",
    "InvestigationSpec",
    "build_lab",
    "check_image_id",
    "investigation_json",
    "layer_factory",
    "leak_terms",
    "out_of_bounds",
    "rendered_config",
    "rendered_surface",
    "replay_investigation",
    "run",
    "run_sandbox_limits",
    "sandbox_json",
    "submitted_structure",
]

_log = logging.getLogger(__name__)

#: Patterns in ``python`` code that reach outside the analysis (SPEC §9). A
#: hit voids the run and is reported; it is a scan, not the isolation itself
#: (that is the sandbox, tested by SPEC §6.3 no. 8).
OUT_OF_BOUNDS: Final = (
    r"/proc\b",
    r"/sys\b",
    r"/etc\b",
    r"/var/run",
    r"mountinfo",
    r"\bsocket\b",
    r"\bsubprocess\b",
    r"\bctypes\b",
    r"/run/",
    r"\.\./",
)


#: The two sandbox limits an :class:`InvestigationSpec` records and may set
#: (the others are :class:`~sciagent.sandbox.SandboxLimits`' defaults): the
#: spec's defaults, and what :func:`run` gives every AG-o run.
SANDBOX_WALL_SECONDS: Final = 60.0
SANDBOX_MEMORY_MB: Final = 2048
#: Seconds a finished session waits for a tool call that outlived it before
#: cleanup. ``asyncio.run`` has already joined the worker threads for up to
#: 300 s by then, so this is only the last call's way out of the layer.
IDLE_WAIT_S: Final = 5.0


def run_sandbox_limits() -> SandboxLimits:
    """The limits of every ``python`` call of an AG-o run :func:`run` starts.

    Their one source: :func:`run` builds its spec from them, and an AG-o
    session's address (:func:`sciagent.campaign.plan.session_text`) names them.
    """
    return SandboxLimits(wall_seconds=SANDBOX_WALL_SECONDS, memory_mb=SANDBOX_MEMORY_MB)


def sandbox_json(limits: SandboxLimits, helper_sha256: str) -> dict[str, Any]:
    """What fixes a sandbox's behaviour besides its image (whose tag is
    recorded with every result): every limit, and the handoff helper's code,
    which enforces the ``work/`` limits and lives outside the image.
    """
    return {"limits": asdict(limits), "handoff_helper_sha256": helper_sha256}


@dataclass(frozen=True)
class EnvironmentSpec:
    """What an environment supplies to an investigation, by injection.

    ``resolve`` maps a truth id to a :class:`Truth` (a registry lookup, so
    replay needs only the id). ``anon_channels`` maps native channel names to
    anonymised ones; ``forbidden`` adds the environment's own leak terms to
    :data:`~sciagent.anonymise.DEFAULT_FORBIDDEN`. ``marks`` is the
    environment's mark law, which the EIG and FSD policies need to simulate
    fitted structures (P4 Δ11, as B-sym's policies and the scorer use it);
    an environment without it cannot run them.
    """

    name: str
    anon_channels: Mapping[str, str]
    forbidden: tuple[str, ...]
    resolve: Callable[[str], Truth]
    marks: MarkSampler | None = None


@dataclass(frozen=True)
class InvestigationSpec:
    """The framework-side description of one investigation (recorded)."""

    environment: str
    truth_id: str
    arm: Arm
    condition: Condition
    seed: int
    experiments: int = 8
    fits: int = 40
    sandbox_wall_seconds: float = SANDBOX_WALL_SECONDS
    sandbox_memory_mb: int = SANDBOX_MEMORY_MB
    #: The system prompt's variant (P4 Δ6); a record without it is ``v1``.
    prompt_variant: PromptVariant = "v1"
    #: The experiment-choice policy (P4 Δ11); a record without it has none.
    policy: PolicyName | None = None
    #: Policy mode's settings; None is the defaults (recorded explicitly).
    policy_settings: PolicySettings | None = None

    def as_json(self) -> dict[str, Any]:
        """The recorded form. Without a policy it holds exactly the keys it
        held before policy mode, so a non-policy record is unchanged."""
        data: dict[str, Any] = {
            "environment": self.environment,
            "truth_id": self.truth_id,
            "arm": self.arm,
            "condition": self.condition,
            "seed": self.seed,
            "experiments": self.experiments,
            "fits": self.fits,
            "sandbox_wall_seconds": self.sandbox_wall_seconds,
            "sandbox_memory_mb": self.sandbox_memory_mb,
            "fit_config": FIT_CONFIG.key(),
            # AG-c has no sandbox, and its records keep their bytes.
            **({"sandbox": self.sandbox_json()} if self.arm == "AG-o" else {}),
            "prompt_variant": self.prompt_variant,
        }
        if self.policy is not None:
            settings = self.policy_settings or PolicySettings()
            data["policy"] = self.policy
            data["policy_settings"] = settings.as_json()
        return data

    def sandbox_limits(self) -> SandboxLimits:
        """The limits of every ``python`` call of an AG-o run."""
        return SandboxLimits(
            wall_seconds=self.sandbox_wall_seconds, memory_mb=self.sandbox_memory_mb
        )

    def sandbox_json(self) -> dict[str, Any]:
        """:func:`sandbox_json` of this spec's limits and the current helper."""
        return sandbox_json(self.sandbox_limits(), HANDOFF_HELPER_SHA256)

    @staticmethod
    def from_json(data: Mapping[str, Any]) -> InvestigationSpec:
        arm = data["arm"]
        condition = data["condition"]
        if arm not in ("AG-c", "AG-o") or condition not in ("named", "anon"):
            raise LabError(f"bad arm/condition in record: {arm!r}, {condition!r}")
        if data.get("fit_config") != FIT_CONFIG.key():
            raise LabError(
                f"the record was made with fit configuration "
                f"{data.get('fit_config')!r}, this code pins {FIT_CONFIG.key()!r}"
            )
        try:
            variant = as_variant(data.get("prompt_variant", "v1"))
            policy = as_policy(data.get("policy"))
        except PromptError as error:
            raise LabError(f"bad prompt variant or policy in record: {error}") from None
        settings = data.get("policy_settings")
        spec = InvestigationSpec(
            environment=str(data["environment"]),
            truth_id=str(data["truth_id"]),
            arm=arm,
            condition=condition,
            seed=int(data["seed"]),
            experiments=int(data["experiments"]),
            fits=int(data["fits"]),
            sandbox_wall_seconds=float(data["sandbox_wall_seconds"]),
            sandbox_memory_mb=int(data["sandbox_memory_mb"]),
            prompt_variant=variant,
            policy=policy,
            policy_settings=None
            if policy is None or settings is None
            else PolicySettings.from_json(settings),
        )
        if arm == "AG-o" and data.get("sandbox") != spec.sandbox_json():
            raise LabError(
                f"the record was made with sandbox {data.get('sandbox')!r}, "
                f"this code builds {spec.sandbox_json()!r}"
            )
        return spec


@dataclass(frozen=True)
class InvestigationAgentConfig(AgentConfig):
    """:class:`AgentConfig` plus the recorded :class:`InvestigationSpec`."""

    investigation: Mapping[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {**super().as_json(), "investigation": dict(self.investigation)}


@dataclass(frozen=True)
class InvestigationOutcome:
    """What one finished investigation produced (framework-side).

    ``void`` and ``void_reasons`` follow the void policy of
    :mod:`sciagent.harness.voids` (P4 Δ8); ``unknown_tool_attempts`` counts the
    denied calls to unknown tool names, which do not void the run."""

    record_path: Path
    head: str
    outcome: str
    submission: Structure | None
    report: str | None
    experiments_used: int
    fits_used: int
    void: bool
    void_reasons: tuple[str, ...]
    unknown_tool_attempts: int
    predictions: tuple[PredictionOutcome, ...]
    num_turns: int | None
    wall_s: float | None
    usage: Mapping[str, Any] | None


# --------------------------------------------------------------------------
# Building the lab
# --------------------------------------------------------------------------


def build_lab(
    spec: InvestigationSpec,
    env: EnvironmentSpec,
    *,
    sandbox_root: Path | None = None,
    fit_workers: int = 1,
    image: str | None = None,
) -> Lab:
    """A fresh lab: the world from (truth id, seed), the view, the sandbox.

    ``image`` names the sandbox image without asking Docker (for building a
    lab only to render its prompts); by default :func:`ensure_image` is used.
    """
    if spec.environment != env.name:
        raise LabError(
            f"the investigation is in environment {spec.environment!r}, "
            f"not {env.name!r}"
        )
    truth = env.resolve(spec.truth_id)
    # P4 Δ2: an experiment that reaches the event cap is truncated and kept.
    world = World(truth, spec.seed, truncate=True)
    view = make_view(spec.condition, world.channels, env.anon_channels)
    sandbox = None
    if spec.arm == "AG-o":
        tag = ensure_image() if image is None else image
        if sandbox_root is not None:
            sandbox_root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(dir=sandbox_root))
        sandbox = Sandbox(
            run_dir,
            image=tag,
            limits=spec.sandbox_limits(),
            seed=spec.seed,
        )
    return Lab(
        world,
        view,
        spec.arm,
        experiments=spec.experiments,
        fits=spec.fits,
        sandbox=sandbox,
        fit_workers=fit_workers,
        prompt_variant=spec.prompt_variant,
        policy=spec.policy,
        policy_settings=spec.policy_settings,
        policy_seed=truth_policy_seed(spec.truth_id, spec.seed),
        marks=env.marks,
    )


def layer_factory(
    env: EnvironmentSpec, *, sandbox_root: Path | None = None, fit_workers: int = 1
) -> LayerFactory:
    """The replay factory: a fresh layer from a recorded config entry."""

    def factory(config: Mapping[str, Any]) -> ToolLayer:
        spec = InvestigationSpec.from_json(config["investigation"])
        lab = build_lab(spec, env, sandbox_root=sandbox_root, fit_workers=fit_workers)
        check_image_id(config["investigation"], lab)
        return lab.layer()

    return factory


def investigation_json(spec: InvestigationSpec, lab: Lab) -> dict[str, Any]:
    """The ``investigation`` section of a run's config: the spec and, for an
    AG-o run, the ID of the image its sandbox runs (``sandbox_image_id``).

    The ID is recorded, not put in the session's address: the tag hashes the
    build recipe, and a rebuild of it need not give the same bytes. Replay
    checks it (:func:`check_image_id`). AG-c records keep their bytes.
    """
    out = spec.as_json()
    if lab.sandbox is not None:
        out["sandbox_image_id"] = image_id(lab.sandbox.image, docker=lab.sandbox.docker)
    return out


def check_image_id(recorded: Mapping[str, Any], lab: Lab) -> None:
    """Refuse to replay an AG-o record on an image other than the one it ran.

    Records made before the ID was recorded carry none and are not checked.

    Raises:
        LabError: the lab's image has another ID than the recorded one.
    """
    want = recorded.get("sandbox_image_id")
    if want is None or lab.sandbox is None:
        return
    have = image_id(lab.sandbox.image, docker=lab.sandbox.docker)
    if have != want:
        raise LabError(
            f"the record ran on image {want}; {lab.sandbox.image} here is {have} "
            "(rebuilt or changed): replay would not be comparable"
        )


def _prompts(lab: Lab) -> tuple[str, str]:
    observational = lab.world.datasets[OBSERVATIONAL]
    system = system_prompt(
        lab.view,
        lab.arm,
        experiments=lab.experiments,
        fits=lab.fits,
        variant=lab.prompt_variant,
        policy=lab.policy,
    )
    opening = opening_prompt(lab.view, observational.log.n, observational.log.horizon)
    return system, opening


def rendered_surface(lab: Lab) -> dict[str, Any]:
    """Everything the agent is shown before its first call, by label.

    The system prompt, the opening prompt and every tool schema: the input to
    the anonymisation leak test. Without the opening prompt it equals
    :func:`rendered_config` for the lab's configuration.
    """
    system, opening = _prompts(lab)
    surface: dict[str, Any] = {"system_prompt": system, "opening_prompt": opening}
    for schema in lab.layer().schemas():
        surface[f"tool:{schema['name']}"] = schema
    return surface


def rendered_config(
    env: EnvironmentSpec,
    channels: tuple[ChannelSpec, ...],
    arm: Arm,
    condition: Condition,
    *,
    experiments: int,
    fits: int,
    prompt_variant: str = "v1",
    policy: str | None = None,
) -> dict[str, Any]:
    """The session surface from configuration alone (P4 Δ7).

    :func:`rendered_surface` without the opening prompt (which reads the
    observational log's size and horizon), rendered without resolving a truth
    or building a world: the system prompt and every tool schema, by label.
    Its inputs are the channel specs, the anonymised names, the arm, the
    condition, the budgets, the prompt variant, the policy (P4 Δ11; it is
    validated here, and runs on AG-c only) and this package's constants; the
    truth, the seed and the sandbox limits are not among them. The campaign's
    session key hashes it, so a prompt or schema edit moves the key. A lab
    built from the same configuration shows exactly this.
    """
    if arm not in ("AG-c", "AG-o"):
        raise LabError(f"arm must be 'AG-c' or 'AG-o', got {arm!r}")
    if experiments < 0 or fits < 0:
        raise LabError("budgets must be non-negative")
    try:
        variant = as_variant(prompt_variant)
        policy_name = as_policy(policy)
        check_policy_arm(arm, policy_name)
    except PromptError as error:
        raise LabError(str(error)) from None
    view = make_view(condition, channels, env.anon_channels)
    surface: dict[str, Any] = {
        "system_prompt": system_prompt(
            view,
            arm,
            experiments=experiments,
            fits=fits,
            variant=variant,
            policy=policy_name,
        )
    }
    specs = tool_specs(
        view, arm, experiments=experiments, fits=fits, policy=policy_name
    )
    for spec in specs:
        surface[f"tool:{spec.name}"] = spec.schema()
    return surface


def leak_terms(env: EnvironmentSpec, channels: tuple[str, ...]) -> tuple[str, ...]:
    """The forbidden-term list for the anonymised condition.

    The baseline domain cues, the environment's own terms, the native channel
    names and every native diagnostic name.
    """
    return tuple(
        dict.fromkeys(
            (*DEFAULT_FORBIDDEN, *env.forbidden, *channels, *catalogue.names())
        )
    )


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def out_of_bounds(record: SessionRecord) -> tuple[str, ...]:
    """Out-of-bounds patterns found in the record's ``python`` calls."""
    hits: list[str] = []
    for entry in record.of_kind("tool_call"):
        body = entry.body
        if body.get("name") != "python":
            continue
        code = str(body.get("args", {}).get("code", ""))
        for pattern in OUT_OF_BOUNDS:
            if re.search(pattern, code):
                hits.append(f"call {body['call']}: {pattern}")
    return tuple(hits)


def _discard_sandbox(sandbox: Sandbox, *, keep: bool) -> str | None:
    """Recover the sandbox's ``work/`` and remove its run directory.

    Never raises: it runs while the run's record is still unsaved (and in
    the ``finally`` of an aborted run, whose own error must propagate). A
    failure is returned as a short reason -- the error's type only, since
    its message holds the random run-directory path and the reason goes into
    the session cell -- and logged in full with the path, which is left in
    place for the operator. ``keep`` keeps the directory (still recovered).
    """
    try:
        sandbox.recover()
        if not keep:
            shutil.rmtree(sandbox.run_dir)
    except (SandboxError, OSError) as error:
        _log.warning(
            "sandbox cleanup of %s failed, directory left in place: %s: %s",
            sandbox.run_dir,
            type(error).__name__,
            error,
        )
        return type(error).__name__
    return None


def run(
    env: EnvironmentSpec,
    truth_id: str,
    arm: Arm,
    condition: Condition,
    model: str,
    seed: int,
    out_dir: Path,
    *,
    experiments: int = 8,
    fits: int = 40,
    max_turns: int = 60,
    wall_time_s: float = 3600.0,
    driver: SessionDriver | None = None,
    sandbox_root: Path | None = None,
    keep_sandbox: bool = False,
    fit_workers: int = 1,
    prompt_variant: str = "v1",
    policy: str | None = None,
    policy_settings: PolicySettings | None = None,
) -> InvestigationOutcome:
    """Run one investigation, save its record under ``out_dir``, and return it.

    A run aborted by the harness (hermeticity, served model, session failure,
    a tool bug) saves its partial record as ``aborted-<head>.json`` and
    re-raises. ``prompt_variant`` picks the system prompt's variant (P4 Δ6).
    ``policy`` names an experiment-choice policy (P4 Δ11: ``random``, ``EIG``
    or ``FSD``, AG-c only), which puts the lab in policy mode
    (:mod:`sciagent.investigation.policy`); ``policy_settings`` overrides its
    replicates and menus (tests). An unknown policy, a policy on AG-o, or EIG
    or FSD in an environment without a mark law is refused before anything
    runs. An AG-o sandbox is recovered and removed afterwards
    (:func:`_discard_sandbox`); if that fails, the record is still saved and
    the run is void for an infrastructure fault (``sandbox cleanup``). A tool
    call that outlives the session (a worker thread cannot be cancelled) leaves
    the sandbox in place, since removing it under a running call could fail
    and void the run for a call the record does not hold: the session's owner
    recovers it once the process is gone
    (:func:`sciagent.campaign.llm.run_session_isolated`).

    Everything returned is read from the record or from the lab's committed
    read-outs (:class:`~sciagent.investigation.tools.Committed`), so a call
    with no ``tool_call`` entry reaches no field of the outcome.
    """
    try:
        variant = as_variant(prompt_variant)
        policy_name = as_policy(policy)
        check_policy_arm(arm, policy_name)
    except PromptError as error:
        raise LabError(str(error)) from None
    if policy_name in NEEDS_MARKS and env.marks is None:
        raise LabError(
            f"policy {policy_name!r} needs the environment's mark law "
            "(EnvironmentSpec.marks)"
        )
    limits = run_sandbox_limits()
    spec = InvestigationSpec(
        environment=env.name,
        truth_id=truth_id,
        arm=arm,
        condition=condition,
        seed=seed,
        experiments=experiments,
        fits=fits,
        sandbox_wall_seconds=limits.wall_seconds,
        sandbox_memory_mb=limits.memory_mb,
        prompt_variant=variant,
        policy=policy_name,
        policy_settings=policy_settings,
    )
    lab = build_lab(spec, env, sandbox_root=sandbox_root, fit_workers=fit_workers)
    system, opening = _prompts(lab)
    try:
        investigation = investigation_json(spec, lab)
    except SandboxError:
        if lab.sandbox is not None:
            _discard_sandbox(lab.sandbox, keep=keep_sandbox)
        raise
    config = InvestigationAgentConfig(
        model=model,
        system_prompt=system,
        prompt=opening,
        seed=seed,
        max_turns=max_turns,
        wall_time_s=wall_time_s,
        investigation=investigation,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    cleanup_fault: str | None = None
    layer = lab.layer()
    try:
        result = run_investigation(config, layer, driver=driver)
    except (
        HarnessFaultError,
        HermeticityError,
        ServedModelError,
        SessionFailedError,
    ) as error:
        error.record.save(out_dir / f"aborted-{error.record.head[:16]}.json")
        raise
    finally:
        layer.close()
        if lab.sandbox is not None:
            if layer.idle(IDLE_WAIT_S):
                cleanup_fault = _discard_sandbox(lab.sandbox, keep=keep_sandbox)
            else:
                _log.warning(
                    "a tool call outlived the session; %s is left for the "
                    "session's owner to recover",
                    lab.sandbox.run_dir,
                )
    record = result.record
    path = out_dir / f"investigation-{record.head[:16]}.json"
    record.save(path)
    # The void policy (P4 Δ8) over every raw reason at once, so the reasons
    # come out in the order the analysis gets from a cell storing these raw
    # reasons (``sciagent.campaign.voids``).
    cleanup = () if cleanup_fault is None else (f"sandbox cleanup: {cleanup_fault}",)
    verdict = assess(
        [
            *foreign_reasons(result.foreign_tool_uses),
            *(INFRASTRUCTURE + f for f in lab.infrastructure_faults),
            *(INFRASTRUCTURE + f for f in cleanup),
            *(OUT_OF_BOUNDS_REASON + h for h in out_of_bounds(record)),
        ],
        record,
    )
    results = record.of_kind("result")
    usage = results[-1].body.get("usage") if results else None
    wall = record.timing.get("wall_s")
    return InvestigationOutcome(
        record_path=path,
        head=record.head,
        outcome=result.outcome,
        submission=lab.submitted,
        report=lab.report,
        experiments_used=lab.experiments_used,
        fits_used=lab.fits_used,
        void=verdict.void,
        void_reasons=verdict.reasons,
        unknown_tool_attempts=verdict.unknown_tool_attempts,
        predictions=lab.prediction_outcomes,
        num_turns=result.num_turns,
        wall_s=None if wall is None else float(wall),
        usage=usage,
    )


def replay_investigation(
    record: SessionRecord,
    env: EnvironmentSpec,
    *,
    sandbox_root: Path | None = None,
    fit_workers: int = 1,
) -> ReplayReport:
    """Replay a recorded investigation offline (no model, no SDK session).

    AG-o replays re-execute every ``python`` call in a fresh sandbox under a
    temporary directory, removed afterwards.
    """
    if sandbox_root is not None:
        return replay(
            record,
            layer_factory(env, sandbox_root=sandbox_root, fit_workers=fit_workers),
        )
    with tempfile.TemporaryDirectory() as tmp:
        return replay(
            record,
            layer_factory(env, sandbox_root=Path(tmp), fit_workers=fit_workers),
        )


def submitted_structure(
    record: SessionRecord, channels: tuple[ChannelSpec, ...]
) -> Structure | None:
    """The native submitted structure, read back from a record's submit call."""
    for entry in record.of_kind("tool_call"):
        body = entry.body
        if body.get("name") == "submit" and not body.get("is_error"):
            text = body["record"]["structure"]
            return parse(str(text), channels)
    return None
