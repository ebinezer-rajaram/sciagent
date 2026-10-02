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

import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
from sciagent.sandbox import Sandbox, SandboxLimits, ensure_image

__all__ = [
    "OUT_OF_BOUNDS",
    "EnvironmentSpec",
    "InvestigationAgentConfig",
    "InvestigationOutcome",
    "InvestigationSpec",
    "build_lab",
    "layer_factory",
    "leak_terms",
    "out_of_bounds",
    "rendered_surface",
    "replay_investigation",
    "run",
    "submitted_structure",
]

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
    sandbox_wall_seconds: float = 60.0
    sandbox_memory_mb: int = 2048

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
        }

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
        return InvestigationSpec(
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
            limits=SandboxLimits(
                wall_seconds=spec.sandbox_wall_seconds,
                memory_mb=spec.sandbox_memory_mb,
            ),
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
        return build_lab(
            spec, env, sandbox_root=sandbox_root, fit_workers=fit_workers
        ).layer()

    return factory


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
    re-raises.
    """
    spec = InvestigationSpec(
        environment=env.name,
        truth_id=truth_id,
        arm=arm,
        condition=condition,
        seed=seed,
        experiments=experiments,
        fits=fits,
    )
    lab = build_lab(spec, env, sandbox_root=sandbox_root, fit_workers=fit_workers)
    system, opening = _prompts(lab)
    config = InvestigationAgentConfig(
        model=model,
        system_prompt=system,
        prompt=opening,
        seed=seed,
        max_turns=max_turns,
        wall_time_s=wall_time_s,
        investigation=spec.as_json(),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
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
        if lab.sandbox is not None and not keep_sandbox:
            shutil.rmtree(lab.sandbox.run_dir, ignore_errors=False)
    record = result.record
    path = out_dir / f"investigation-{record.head[:16]}.json"
    record.save(path)
    reasons = [f"foreign tool: {name}" for name in result.foreign_tool_uses]
    reasons += [f"infrastructure: {f}" for f in lab.infrastructure_faults]
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
