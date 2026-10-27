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
budgets, sandbox limits). That is what :func:`layer_factory` rebuilds from.
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
from sciagent.investigation.prompts import Arm, opening_prompt, system_prompt
from sciagent.investigation.tools import FIT_CONFIG, Lab, LabError, PredictionOutcome
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
    :data:`~sciagent.anonymise.DEFAULT_FORBIDDEN`.
    """

    name: str
    anon_channels: Mapping[str, str]
    forbidden: tuple[str, ...]
    resolve: Callable[[str], Truth]


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

    def as_json(self) -> dict[str, Any]:
        return {
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
        }

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
    """What one finished investigation produced (framework-side)."""

    record_path: Path
    head: str
    outcome: str
    submission: Structure | None
    report: str | None
    experiments_used: int
    fits_used: int
    void: bool
    void_reasons: tuple[str, ...]
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
    world = World(truth, spec.seed)
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
        lab.view, lab.arm, experiments=lab.experiments, fits=lab.fits
    )
    opening = opening_prompt(lab.view, observational.log.n, observational.log.horizon)
    return system, opening


def rendered_surface(lab: Lab) -> dict[str, Any]:
    """Everything the agent is shown before its first call, by label.

    The system prompt, the opening prompt and every tool schema: the input to
    the anonymisation leak test.
    """
    system, opening = _prompts(lab)
    surface: dict[str, Any] = {"system_prompt": system, "opening_prompt": opening}
    for schema in lab.layer().schemas():
        surface[f"tool:{schema['name']}"] = schema
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
) -> InvestigationOutcome:
    """Run one investigation, save its record under ``out_dir``, and return it.

    A run aborted by the harness (hermeticity, served model, session failure,
    a tool bug) saves its partial record as ``aborted-<head>.json`` and
    re-raises. An AG-o sandbox is recovered and removed afterwards
    (:func:`_discard_sandbox`); if that fails, the record is still saved and
    the run is void for an infrastructure fault (``sandbox cleanup``).
    """
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
    try:
        result = run_investigation(config, lab.layer(), driver=driver)
    except (
        HarnessFaultError,
        HermeticityError,
        ServedModelError,
        SessionFailedError,
    ) as error:
        error.record.save(out_dir / f"aborted-{error.record.head[:16]}.json")
        raise
    finally:
        if lab.sandbox is not None:
            cleanup_fault = _discard_sandbox(lab.sandbox, keep=keep_sandbox)
    record = result.record
    path = out_dir / f"investigation-{record.head[:16]}.json"
    record.save(path)
    reasons = [f"foreign tool: {name}" for name in result.foreign_tool_uses]
    reasons += [f"infrastructure: {f}" for f in lab.infrastructure_faults]
    if cleanup_fault is not None:
        reasons.append(f"infrastructure: sandbox cleanup: {cleanup_fault}")
    reasons += [f"out of bounds: {h}" for h in out_of_bounds(record)]
    results = record.of_kind("result")
    usage = results[-1].body.get("usage") if results else None
    wall = record.timing.get("wall_s")
    return InvestigationOutcome(
        record_path=path,
        head=record.head,
        outcome=result.outcome,
        submission=lab.submitted,
        report=lab.report,
        experiments_used=lab.world.experiments_run,
        fits_used=lab.fits_used,
        void=bool(reasons),
        void_reasons=tuple(reasons),
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
