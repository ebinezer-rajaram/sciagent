"""The science tool layer of an investigation (SPEC §4.0, §4.4, §5).

:class:`Lab` holds one investigation's agent-reachable state -- the
:class:`~sciagent.investigation.world.World` (through its public methods
only), the prediction ledger, the fit memo, the notebook and, for AG-o, the
sandbox -- and :meth:`Lab.layer` exposes it as a
:class:`~sciagent.harness.tools.ToolLayer`, which is the only door the agent
has. Budgets: ``experiments`` (E), ``fits`` (F) and ``submit`` (1).

Conventions every handler keeps:

- **Names and units.** Arguments arrive in the agent's names and units and go
  through the :class:`~sciagent.investigation.view.AgentView` on the way in;
  every text goes out through it. Diagnostics and kernel estimates are
  computed on the agent-view dataset; fits run natively and are converted.
- **Errors.** A bad request raises a :class:`~sciagent.core.errors.SciAgentError`
  (the layer shows its message, uncharged) or returns ``is_error`` output.
- **Records.** Each output's ``record`` carries what the framework needs later
  (native structure hashes, fit keys, sandbox digests); it is digested with
  the text, so replay compares it too, and it is never shown to the agent.

Fit charging. A fit is charged only when it is *new*: a successful fit of a
structure whose canonical form was already fitted on the same datasets
returns the memoised result as an ``is_error`` output (the layer's only
uncharged path), with text that says it is a free repeat. B-sym and the other
search baselines deduplicate canonically for free, so charging the agent for
a repeat would make the equal-budget comparison (SPEC §6.2 C1) unequal.

Prediction evaluation (SPEC §4.4, test 10). ``run_experiment`` seals the
ledger for its index *before* it draws, runs, then evaluates every committed
prediction for that index by computing the named diagnostic on the agent-view
dataset. The agent never supplies an observed value: :class:`Prediction`
has no field for one, and ``predict``'s schema has none either.

Truncation (P4 Δ2). The world truncates an experiment that reaches
:data:`~sciagent.investigation.world.MAX_EVENTS` at its cap's event, at τ,
instead of stopping it. The run is charged and its dataset is kept; the
record carries ``truncated_at`` (native τ) and ``analysis_horizon``. Agent-side
analysis -- diagnostics, kernels, the sandbox files, prediction evaluation --
reads the dataset clipped to ``[0, shown]``
(:func:`~sciagent.investigation.view.clip_to_analysis`), where ``shown`` is τ
unless an excluded window holds τ, in which case it is that window's start, so
the time of an unseen event is never shown. Fits read the native dataset,
whose likelihood already leaves out the tail after τ. The runaway guards
still stop a run; that path is unchanged.

Designs (P4 Δ3). ``run_experiment`` reads a design: the experiment JSON with
generators (:data:`~sciagent.glm.designs.DESIGN_SCHEMA`), expanded to arrays
before validation, so the record holds arrays only. AG-o may instead name a
JSON file it wrote in ``/work`` (``{"intervention": {"from": "/work/x.json"}}``):
a regular file, reached through no link, at most
:data:`MAX_DESIGN_FILE_BYTES`, whose sha256 is recorded. Replay re-runs the
sandbox calls that wrote it, so the digest checks the file.

Budget line (P4 Δ12). Every result -- success, error, or the layer's own
refusal -- ends with one line giving the experiments and fits left
(:func:`budget_line`), appended by the layer after charging.

Beliefs (P4 Δ4, SPEC §4.4). ``belief(favoured, rivals, confidence)`` is free
and records the agent's favoured structure, one to :data:`MAX_RIVALS` rivals
(distinct from it and from each other after canonicalisation) and a
confidence in (0, 1). Its record is ``{"belief": i, "favoured", "rivals",
"confidence", "experiments_run": k}`` with every structure as canonical native
DSL text, the input of :func:`~sciagent.investigation.timeline.timeline`. A
belief is *fresh* when no experiment has run since it was recorded;
``run_experiment`` and ``submit`` are refused, uncharged, without a fresh one.
The gate is checked before anything else in those handlers, so a refused call
reads no design, seals no ledger entry and draws nothing. ``submit`` also
records a ``stop_reason`` (:data:`~sciagent.investigation.prompts.STOP_REASONS`)
and an optional ``note``. ``predict``'s ``hypothesis`` is a structure only,
recorded as canonical native text; free commentary goes in ``note``, which is
recorded and never shown back. Every hypothesis label in the record is
``render(canonicalise(parse(text)))`` in native names, so one structure has one
label whatever the spelling or the naming condition.

Policy mode (P4 Δ11, SPEC §4.2). A lab built with a ``policy`` (AG-c only)
offers ``run_experiment`` with no arguments: after the belief gate, the
framework's policy (:mod:`sciagent.investigation.policy`) fits the latest
belief's structures (uncharged, unshown), chooses a design and a diagnostic
from its menus, and the design runs through the same path as an agent's design
(``_run``), so the charge, the ledger seal, truncation and the summary are the
same. The result adds the design (in the agent's names and units) and the
chosen diagnostic's value on the new dataset; the decision, with its scores,
weights and degeneracy summary, is in the output's ``record`` only, under
``policy``. Without a policy every schema, text and record is as before.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

import numpy as np

from sciagent.core.errors import GrammarError, SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.designs import DESIGN_SCHEMA
from sciagent.glm.fit import FitConfig, FitResult, fit, fit_cache_key
from sciagent.glm.grammar import ChannelKind, Structure, n_columns, psi_slots
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    Experiment,
    canonical_json,
)
from sciagent.glm.simulate import (
    ExplosionError,
    MarkSampler,
    NegativeIntensityError,
    SimulationError,
)
from sciagent.glm.syntax import render
from sciagent.harness.ledger import Prediction, PredictionLedger
from sciagent.harness.record import canonical_json as record_json
from sciagent.harness.tools import (
    BudgetMeter,
    Handler,
    ToolLayer,
    ToolOutput,
    ToolSpec,
)
from sciagent.investigation.policy import (
    DEFAULT_SETTINGS,
    PolicyDecision,
    PolicyMode,
    PolicySettings,
)
from sciagent.investigation.prompts import (
    MAX_DESIGN_FILE_BYTES,
    STOP_REASONS,
    WORK_PREFIX,
    Arm,
    PolicyName,
    PromptError,
    PromptVariant,
    as_policy,
    as_variant,
    budget_line,
    check_policy_arm,
    num,
)
from sciagent.investigation.view import (
    AgentView,
    ViewError,
    clip_to_analysis,
    shown_truncation,
)
from sciagent.investigation.world import (
    MAX_EVENTS,
    OBSERVATIONAL,
    World,
    experiment_id,
)
from sciagent.nonparam.wiener_hopf import ARRIVAL, estimate_kernels
from sciagent.sandbox import Sandbox, SandboxError, result_digest, write_dataset

__all__ = [
    "BUDGET_EXPERIMENTS",
    "BUDGET_FITS",
    "BUDGET_SUBMIT",
    "ENDOGENOUS_COLUMN",
    "FIT_CONFIG",
    "MAX_DESIGN_FILE_BYTES",
    "MAX_NOTE",
    "MAX_RIVALS",
    "SUMMARY_DIAGNOSTICS",
    "Lab",
    "LabError",
    "PredictionOutcome",
    "budget_line",
    "tool_specs",
]

#: The one fit configuration every ``fit`` call uses (``workers`` aside, which
#: changes wall time only). Its :meth:`~sciagent.glm.fit.FitConfig.key` is
#: recorded in the session config and checked on replay.
FIT_CONFIG: Final = FitConfig()

BUDGET_EXPERIMENTS: Final = "experiments"
BUDGET_FITS: Final = "fits"
BUDGET_SUBMIT: Final = "submit"

#: Column/array written beside the marks in the sandbox's data files.
ENDOGENOUS_COLUMN: Final = "endogenous"
#: The driver name shown for Wiener-Hopf's unweighted event driver.
EVENTS_DRIVER: Final = "events"
#: Diagnostics reported with every new dataset (native names).
SUMMARY_DIAGNOSTICS: Final = (
    "mean_rate",
    "inter_arrival_dispersion",
    "fano_factor",
)
_MAX_NOTEBOOK_APPEND: Final = 20_000
_MAX_CODE: Final = 50_000
#: Most rivals one belief may name (P4 Δ4).
MAX_RIVALS: Final = 3
#: Longest free ``note`` on ``predict`` or ``submit``, in characters.
MAX_NOTE: Final = 1_000
_DATA_ID: Final = re.compile(r"^(obs|e(0|[1-9][0-9]*))$")
#: A design file's path as the agent writes it: under /work, plain segments.
_WORK_PATH: Final = re.compile(r"^/work(/[A-Za-z0-9_.-]+)+$")
#: The key that names a design file in place of an inline intervention.
FROM_KEY: Final = "from"
#: ``st_file_attributes`` bit of a Windows reparse point (junction, link).
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x400


class LabError(SciAgentError):
    """A tool request the lab declines (shown to the agent, uncharged)."""


@dataclass(frozen=True)
class PredictionOutcome:
    """The framework's evaluation of one committed prediction."""

    commitment: int
    experiment: int
    statistic: str
    hypothesis: str
    interval: tuple[float, float]
    level: float
    value: float | None
    covered: bool
    error: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "commitment": self.commitment,
            "experiment": self.experiment,
            "statistic": self.statistic,
            "hypothesis": self.hypothesis,
            "interval": list(self.interval),
            "level": self.level,
            "value": self.value,
            "covered": self.covered,
            "error": self.error,
        }


def _schema(
    properties: Mapping[str, Any], required: Sequence[str], **extra: Any
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
        **extra,
    }


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


@dataclass
class _FitEntry:
    number: int
    result: FitResult
    text: str
    record: dict[str, Any]


@dataclass
class _State:
    """Mutable lab state, touched only by handlers (under the layer's lock)."""

    fits: dict[str, _FitEntry] = field(default_factory=dict)
    notebook: list[str] = field(default_factory=list)
    outcomes: list[PredictionOutcome] = field(default_factory=list)
    submitted: Structure | None = None
    report: str | None = None
    infrastructure_faults: list[str] = field(default_factory=list)
    #: Beliefs recorded so far (the next belief's index).
    beliefs: int = 0
    #: ``World.experiments_run`` when the latest belief was recorded.
    belief_at: int | None = None
    #: The latest belief's structures, favoured first, as ``(native label,
    #: native structure)``: policy mode's entertained candidates.
    belief_structures: tuple[tuple[str, Structure], ...] = ()


class Lab:
    """One investigation's tools over a world, for one arm and one view."""

    def __init__(
        self,
        world: World,
        view: AgentView,
        arm: Arm,
        *,
        experiments: int,
        fits: int,
        sandbox: Sandbox | None = None,
        fit_workers: int = 1,
        prompt_variant: str = "v1",
        policy: str | None = None,
        policy_settings: PolicySettings | None = None,
        policy_seed: int | None = None,
        marks: MarkSampler | None = None,
    ) -> None:
        if arm not in ("AG-c", "AG-o"):
            raise LabError(f"arm must be 'AG-c' or 'AG-o', got {arm!r}")
        if (arm == "AG-o") != (sandbox is not None):
            raise LabError("AG-o needs a sandbox and AG-c must not have one")
        if experiments < 0 or fits < 0:
            raise LabError("budgets must be non-negative")
        try:
            variant = as_variant(prompt_variant)
            policy_name = as_policy(policy)
            check_policy_arm(arm, policy_name)
        except PromptError as error:
            raise LabError(str(error)) from None
        if policy_name is not None and policy_seed is None:
            raise LabError("policy mode needs a seed for the policy")
        self.world = world
        self.view = view
        self.arm: Arm = arm
        self.experiments = experiments
        self.fits = fits
        self.sandbox = sandbox
        #: The system prompt's variant (P4 Δ6). The tools do not depend on it;
        #: it is kept here so everything the agent is shown renders from the lab.
        self.prompt_variant: PromptVariant = variant
        # Pinned framework-side (invariant 2): no agent argument reaches the
        # fit configuration, so certification cannot be loosened by a request.
        self.fit_config = replace(FIT_CONFIG, workers=fit_workers)
        self.ledger = PredictionLedger()
        self.state = _State()
        #: The experiment-choice policy (P4 Δ11); None: the agent designs.
        self.policy: PolicyName | None = policy_name
        self.policy_mode: PolicyMode | None = None
        if policy_name is not None and policy_seed is not None:
            self.policy_mode = PolicyMode(
                policy_name,
                policy_seed,
                DEFAULT_SETTINGS if policy_settings is None else policy_settings,
                world.channels,
                view,
                world.datasets[OBSERVATIONAL],
                marks,
                self.fit_config,
            )
        if sandbox is not None:
            self._publish(OBSERVATIONAL)

    # -- read-outs for the runner (framework side) ------------------------------

    @property
    def submitted(self) -> Structure | None:
        return self.state.submitted

    @property
    def report(self) -> str | None:
        return self.state.report

    @property
    def prediction_outcomes(self) -> tuple[PredictionOutcome, ...]:
        return tuple(self.state.outcomes)

    @property
    def infrastructure_faults(self) -> tuple[str, ...]:
        return tuple(self.state.infrastructure_faults)

    @property
    def fits_used(self) -> int:
        return len(self.state.fits)

    # -- data helpers -------------------------------------------------------------

    def _agent_data(self, data_id: str) -> Dataset:
        """The dataset the agent analyses: clipped to ``[0, shown]`` if truncated."""
        native = self._native_data(data_id)
        return self.view.dataset(
            clip_to_analysis(native, self.world.truncated_at(data_id))
        )

    def _native_data(self, data_id: object) -> Dataset:
        if not isinstance(data_id, str):
            raise LabError(f"a data id must be a string, got {data_id!r}")
        datasets = self.world.datasets
        if data_id not in datasets:
            raise LabError(
                f"no dataset {data_id!r}; available: {self._ids_text(datasets)}"
            )
        return datasets[data_id]

    @staticmethod
    def _ids_text(datasets: Mapping[str, Dataset]) -> str:
        return ", ".join(_ordered_ids(datasets))

    def _publish(self, data_id: str) -> None:
        """Write a dataset (agent view) into the sandbox's read-only ``data/``."""
        assert self.sandbox is not None
        agent = self._agent_data(data_id)
        marks: dict[str, Any] = {
            name: values for name, values in agent.log.marks.items()
        }
        if ENDOGENOUS_COLUMN in marks:
            raise LabError(f"a channel is named {ENDOGENOUS_COLUMN!r}")
        marks[ENDOGENOUS_COLUMN] = agent.endogenous.astype(np.float64)
        write_dataset(self.sandbox.run_dir, data_id, agent.log.times, marks)

    def _summary(self, data_id: str) -> str:
        data = self._agent_data(data_id)
        log = data.log
        n_forced = int(np.count_nonzero(~data.endogenous))
        lines = [
            f"dataset `{data_id}`: {log.n} events observed on "
            f"[0, {num(log.horizon)}]"
            + (f", {n_forced} of them not generated by the process" if n_forced else "")
        ]
        if data.excluded:
            windows = ", ".join(f"[{num(a)}, {num(b)})" for a, b in data.excluded)
            lines.append(
                f"windows where the process's own rate was not observed: {windows}"
            )
        stats = []
        for native in SUMMARY_DIAGNOSTICS:
            name = self.view.diagnostic(native)
            try:
                value = catalogue.compute(native, log, self.view.channels, {})
                stats.append(f"{name} = {num(value)}")
            except catalogue.DiagnosticError as error:
                stats.append(f"{name}: not available ({error})")
        lines.append("summary: " + "; ".join(stats))
        if self.sandbox is not None:
            lines.append(f"files: /data/{data_id}.csv, /data/{data_id}.npz")
        return "\n".join(lines)

    # -- run_experiment -------------------------------------------------------

    def run_experiment(self, args: Mapping[str, Any]) -> ToolOutput:
        self._require_belief("an experiment")
        if self.policy_mode is not None:
            return self._run_policy(self.policy_mode, args)
        args, source = self._design_args(args)
        native = self.view.experiment_native(args)
        provenance = {} if source is None else {"design_file": source}
        return self._run(native, provenance)

    def _run_policy(self, mode: PolicyMode, args: Mapping[str, Any]) -> ToolOutput:
        """Policy mode: the framework chooses the experiment (module docstring)."""
        if args:
            raise LabError(
                "run_experiment takes no arguments here: the framework chooses "
                "the experiment. Nothing was run and nothing was charged."
            )
        datasets = self.world.datasets
        try:
            chosen = mode.decide(
                self.world.experiments_run,
                self.state.belief_structures,
                [datasets[i] for i in _ordered_ids(datasets)],
            )
        except SciAgentError as error:
            # A fault inside the framework's decision (the policy core, its
            # fits, simulations or diagnostics), not an agent act: its message
            # may carry policy numbers or native names, so it is recorded (and
            # voids the run), and the agent is told only that nothing ran. The
            # layer would otherwise show any SciAgentError's message verbatim.
            self.state.infrastructure_faults.append(
                f"policy {mode.name}: {type(error).__name__}: {error}"
            )
            return ToolOutput(
                "the framework could not choose an experiment; nothing was run "
                "and nothing was charged",
                is_error=True,
            )
        return self._run(chosen.experiment, {}, chosen)

    def _run(
        self,
        native: Experiment,
        provenance: Mapping[str, Any],
        chosen: PolicyDecision | None = None,
    ) -> ToolOutput:
        """Seal, run and summarise one experiment, chosen by the agent or, with
        ``chosen``, by the policy; charged alike (the layer charges a success)."""
        index = self.world.experiments_run
        if index != self.ledger.next_experiment:
            raise LabError("internal: ledger and world disagree on the next index")
        self.ledger.seal(index)
        design = canonical_json(native.intervention)
        try:
            index, _ = self.world.run(native)
        except SimulationError as error:
            # The simulator's message names native times, so it is not shown:
            # the stop is described in the agent's terms instead.
            reason = _stop_reason(error)
            self.ledger.evaluate(index, lambda _p: math.nan)
            self._record_outcomes(index, failed=reason)
            stopped = [
                f"experiment {experiment_id(index)} was stopped and produced no "
                f"dataset: {reason}. It counts against the budget."
            ]
            stopped_record: dict[str, Any] = {
                "experiment": index,
                "design": design,
                "horizon": native.horizon,
                "stopped": True,
                **provenance,
            }
            if chosen is not None:
                stopped.append(self._design_text(chosen))
                no_data = "the experiment was stopped"
                stopped.append(self._diagnostic_text(chosen, None, no_data))
                stopped_record["policy"] = self._policy_record(chosen, None, no_data)
            return ToolOutput("\n".join(stopped), record=stopped_record)
        data_id = experiment_id(index)
        dataset = self.world.datasets[data_id]
        tau = self.world.truncated_at(data_id)
        empty = False
        if tau is not None:
            try:
                clip_to_analysis(dataset, tau)
            except ViewError:
                empty = True  # cut at time 0: nothing to analyse
        if self.sandbox is not None and not empty:
            self._publish(data_id)
        values = self._evaluate(index, data_id, empty=empty)
        used = self.world.experiments_run
        text = [
            f"experiment {data_id} done ({used} of {self.experiments} experiments "
            f"used)."
        ]
        if chosen is not None:
            text.append(self._design_text(chosen))
        record: dict[str, Any] = {
            "experiment": index,
            "design": design,
            "horizon": native.horizon,
            "n_events": dataset.log.n,
        }
        if tau is not None:
            end = shown_truncation(dataset, tau)
            text.append(self._truncation_text(end, native.horizon))
            record["truncated_at"] = tau
            record["analysis_horizon"] = end
        text.append(
            f"dataset `{data_id}`: nothing was observed before the cut, so there "
            "is nothing to analyse; it can still be fitted."
            if empty
            else self._summary(data_id)
        )
        if chosen is not None and self.policy_mode is not None:
            shown, why = self.policy_mode.value(
                chosen, None if empty else self._agent_data(data_id).log
            )
            text.append(self._diagnostic_text(chosen, shown, why, data_id))
            record["policy"] = self._policy_record(chosen, shown, why)
        if values:
            text.append("committed predictions for this experiment:")
            text.extend(_outcome_line(o, self.view) for o in values)
        record["predictions"] = [o.as_json() for o in values]
        record.update(provenance)
        return ToolOutput("\n".join(text), record=record)

    # -- policy mode's texts and record (P4 Δ11) ----------------------------------

    @staticmethod
    def _design_text(chosen: PolicyDecision) -> str:
        return "design chosen by the framework: " + json.dumps(
            chosen.design, sort_keys=True
        )

    def _diagnostic_text(
        self,
        chosen: PolicyDecision,
        value: float | None,
        why: str | None,
        data_id: str | None = None,
    ) -> str:
        choice = chosen.diagnostic
        name = self.view.diagnostic(choice.name)
        head = (
            f"diagnostic chosen by the framework: {name} "
            f"(arguments: {_args_text(dict(choice.args))})"
        )
        if value is not None and data_id is not None:
            return f"{head} on {data_id} = {num(value)}"
        return f"{head}: not available ({why})"

    def _policy_record(
        self, chosen: PolicyDecision, value: float | None, why: str | None
    ) -> dict[str, Any]:
        """The framework-only decision, plus what was shown of it."""
        choice = chosen.diagnostic
        return {
            **chosen.record,
            "shown": {
                "design": chosen.design,
                "diagnostic": {
                    "name": self.view.diagnostic(choice.name),
                    "args": dict(choice.args),
                },
                "value": value,
                "error": why,
            },
        }

    def _truncation_text(self, end: float, horizon: float) -> str:
        shown = num(self.view.time(end))
        return (
            f"truncated: the run reached the limit of {MAX_EVENTS} events and was "
            f"cut short; this dataset covers [0, {shown}] of the planned horizon "
            f"{num(self.view.time(horizon))}, and nothing after {shown} was "
            "observed."
        )

    def _design_args(
        self, args: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], dict[str, Any] | None]:
        """Resolve ``{"intervention": {"from": path}}`` (AG-o) to the file's JSON.

        Returns the arguments with the intervention inlined, and the file's
        provenance for the record (None for an inline design).
        """
        intervention = args.get("intervention")
        if not isinstance(intervention, Mapping) or FROM_KEY not in intervention:
            return args, None
        if self.sandbox is None:
            raise LabError(
                "a design can be read from a file only with the python sandbox; "
                "give the intervention inline"
            )
        if len(intervention) != 1:
            raise LabError(
                f"an intervention read from a file has exactly one key, "
                f"{FROM_KEY!r}; got keys {sorted(map(str, intervention))}"
            )
        path = intervention[FROM_KEY]
        if not isinstance(path, str):
            raise LabError(f"{FROM_KEY!r} must be a path string, got {path!r}")
        payload = _read_work_file(self.sandbox.work_dir, path)
        try:
            loaded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise LabError(f"design file {path} is not valid JSON") from None
        if (
            not isinstance(loaded, dict)
            or "type" not in loaded
            or "intervention" in loaded
        ):
            raise LabError(
                f"design file {path} must hold one intervention object (with a "
                "'type' key), not an experiment; give the horizon as an argument"
            )
        inlined = {**args, "intervention": loaded}
        source = {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        return inlined, source

    def _evaluate(
        self, index: int, data_id: str, *, empty: bool = False
    ) -> list[PredictionOutcome]:
        log = None if empty else self._agent_data(data_id).log
        errors: dict[int, str] = {}

        def statistic(prediction: Prediction) -> float:
            if log is None:
                errors[id(prediction)] = "nothing was observed before the cut"
                return math.nan
            spec = json.loads(prediction.statistic)
            try:
                return catalogue.compute(
                    spec["diagnostic"],
                    log,
                    self.view.channels,
                    spec["args"],
                    check_bounds=False,
                )
            except catalogue.DiagnosticError as error:
                errors[id(prediction)] = str(error)
                return math.nan

        evaluations = self.ledger.evaluate(index, statistic)
        by_index = {c.index: c for c in self.ledger.committed}
        made: list[PredictionOutcome] = []
        for evaluation in evaluations:
            prediction = by_index[evaluation.commitment].prediction
            value = evaluation.value
            made.append(
                PredictionOutcome(
                    commitment=evaluation.commitment,
                    experiment=index,
                    statistic=prediction.statistic,
                    hypothesis=prediction.hypothesis,
                    interval=prediction.interval,
                    level=prediction.level,
                    value=None if math.isnan(value) else value,
                    covered=evaluation.covered,
                    error=errors.get(id(prediction)),
                )
            )
        self.state.outcomes.extend(made)
        return made

    def _record_outcomes(self, index: int, *, failed: str) -> None:
        for entry in self.ledger.committed:
            p = entry.prediction
            if p.experiment == index:
                self.state.outcomes.append(
                    PredictionOutcome(
                        entry.index,
                        index,
                        p.statistic,
                        p.hypothesis,
                        p.interval,
                        p.level,
                        None,
                        False,
                        f"experiment stopped: {failed}",
                    )
                )

    # -- diagnostic -------------------------------------------------------------

    def diagnostic(self, args: Mapping[str, Any]) -> ToolOutput:
        name = self.view.diagnostic_native(str(args["name"]))
        data_id = str(args["data_id"])
        given = args.get("args") or {}
        if not isinstance(given, Mapping):
            raise LabError("args must be an object")
        log = self._agent_data(data_id).log
        resolved = catalogue.resolve_args(name, given, log, self.view.channels)
        value = catalogue.compute(name, log, self.view.channels, resolved)
        shown = self.view.diagnostic(name)
        return ToolOutput(
            f"{shown} on {data_id} = {num(value)}  (arguments: {_args_text(resolved)})",
            record={"diagnostic": name, "data_id": data_id, "value": value},
        )

    # -- nonparam_kernels -----------------------------------------------------

    def nonparam_kernels(self, args: Mapping[str, Any]) -> ToolOutput:
        data_id = str(args["data_id"])
        log = self._agent_data(data_id).log
        estimate = estimate_kernels(log, self.view.channels)
        drivers = [EVENTS_DRIVER if d == ARRIVAL else d for d in estimate.drivers]
        lines = [
            f"Wiener-Hopf estimate on {data_id} ({log.n} events, average rate "
            f"{num(estimate.mean_rate)}): lambda(t) = mu + sum over drivers d of "
            f"the integral of phi_d(u) dX_d(t - u).",
            f"mu = {num(estimate.baseline)}",
            "drivers: " + "; ".join(_driver_text(d, self.view) for d in drivers),
            "kernel integrals: "
            + ", ".join(
                f"{d} {num(float(v))}"
                for d, v in zip(drivers, estimate.norms, strict=True)
            ),
            "kernel values (per unit time per unit driver weight) by lag bin:",
            "lag_from lag_to " + " ".join(drivers),
        ]
        edges = estimate.edges
        for j in range(edges.size - 1):
            row = [f"{float(edges[j]):.4g}", f"{float(edges[j + 1]):.4g}"]
            row.extend(
                f"{float(estimate.kernels[d, j]):.4g}" for d in range(len(drivers))
            )
            lines.append(" ".join(row))
        return ToolOutput(
            "\n".join(lines),
            record={
                "data_id": data_id,
                "norms": [float(v) for v in estimate.norms],
                "baseline": estimate.baseline,
            },
        )

    # -- fit ------------------------------------------------------------------

    def fit(self, args: Mapping[str, Any]) -> ToolOutput:
        structure = canonicalise(self.view.parse(str(args["structure"])))
        ids = _canonical_ids(args["data_ids"], self.world.datasets)
        datasets = [self.world.datasets[i] for i in ids]
        channels = self.world.channels
        key = fit_cache_key(structure, datasets, channels, self.fit_config)
        if key in self.state.fits:
            entry = self.state.fits[key]
            return ToolOutput(
                f"not charged: this structure was already fitted on these data "
                f"(fit {entry.number}); the result is repeated.\n{entry.text}",
                is_error=True,
                record={"repeat_of": entry.number, "key": key},
            )
        result = fit(structure, datasets, channels, config=self.fit_config)
        number = len(self.state.fits) + 1
        text = self._fit_text(number, result, ids, datasets)
        record = {
            "fit": number,
            "key": key,
            "structure_hash": structure_hash(structure),
            "data_ids": ids,
            "certified": result.certified,
            "log_likelihood": result.log_likelihood,
            "bic": result.bic,
            "n_events": result.n_events,
            "simulable": _simulable(result),
        }
        self.state.fits[key] = _FitEntry(number, result, text, record)
        return ToolOutput(text, record=record)

    def _fit_text(
        self,
        number: int,
        result: FitResult,
        ids: list[str],
        datasets: Sequence[Dataset],
    ) -> str:
        view = self.view
        n = result.n_events
        thetas, exact = view.theta(result.structure, result.theta)
        lines = [
            f"fit {number} ({number} of {self.fits} fits used)",
            f"structure (canonical order): {view.render(result.structure)}",
            f"data: {', '.join(ids)} ({n} events counted in the likelihood)",
            f"log-likelihood {num(view.log_likelihood(result.log_likelihood, n))}; "
            f"BIC {num(view.information_criterion(result.bic, n))}; "
            f"AIC {num(view.information_criterion(result.aic, n))}; "
            f"parameters {result.n_params}",
        ]
        per = []
        for data_id, data, ll in zip(
            ids, datasets, result.log_likelihood_per_dataset, strict=True
        ):
            per.append(f"{data_id} {num(view.log_likelihood(ll, _counted(data)))}")
        if len(per) > 1:
            lines.append("log-likelihood by dataset: " + "; ".join(per))
        ks = (
            "n/a"
            if not math.isfinite(result.ks_pvalue)
            else f"statistic {num(result.ks_statistic)}, p = {num(result.ks_pvalue)}"
        )
        lines.append(f"time-rescaling KS test: {ks}")
        search = (
            "globally optimal on the grid"
            if result.psi_search == "exhaustive"
            else "from a local search; may not be the best on the grid"
        )
        lines.append(
            f"certified: {'yes' if result.certified else 'NO'} (psi {search}, "
            f"{result.n_psi_points} grid points)"
        )
        simulable = _simulable(result)
        if simulable is not None:
            lines.append(
                "simulable: "
                + (
                    "yes"
                    if simulable
                    else "NO (the fitted rate can go negative; it is not a "
                    "valid process)"
                )
            )
        lines.append(f"theta0 (intercept) = {num(thetas[0])}")
        position = 1
        for k, (feature, psi) in enumerate(
            zip(result.structure.features, result.psi, strict=True), start=1
        ):
            slots = psi_slots(feature)
            width = n_columns(feature)
            coefs = ", ".join(num(v) for v in thetas[position : position + width])
            position += width
            names = [s.name for s in slots]
            shown_psi = ", ".join(
                f"{s.name}"
                + ("" if names.count(s.name) == 1 else "@" + ".".join(map(str, s.path)))
                + f" = {num(view.psi_value(s.name, psi[s]))}"
                for s in slots
            )
            lines.append(
                f"feature {k}: {view.render_feature(feature, result.structure.link)}"
                f"; theta = [{coefs}]" + (f"; {shown_psi}" if shown_psi else "")
            )
        if not exact:
            lines.append(
                "note: softplus-link coefficients are reported as fitted, not "
                "converted to your time units"
            )
        return "\n".join(lines)

    # -- predict ----------------------------------------------------------------

    def predict(self, args: Mapping[str, Any]) -> ToolOutput:
        experiment = args["experiment"]
        if isinstance(experiment, bool) or not isinstance(experiment, int | float):
            raise LabError("experiment must be an integer index")
        if float(experiment) != int(experiment):
            raise LabError("experiment must be an integer index")
        index = int(experiment)
        if index >= self.experiments:
            raise LabError(
                f"experiment {index} can never run: the budget allows "
                f"{self.experiments} experiments (indices 0 to "
                f"{self.experiments - 1})"
            )
        name = self.view.diagnostic_native(str(args["diagnostic"]))
        given = args.get("args") or {}
        if not isinstance(given, Mapping):
            raise LabError("args must be an object")
        observational = self._agent_data(OBSERVATIONAL).log
        catalogue.resolve_args(
            name, given, observational, self.view.channels, check_bounds=False
        )
        hypothesis = self._structure(
            args["hypothesis"],
            "hypothesis",
            hint="give the structure only and put any commentary in `note`",
        )
        note = _note(args.get("note"))
        interval = args["interval"]
        if not isinstance(interval, list | tuple) or len(interval) != 2:
            raise LabError("interval must be [low, high]")
        low, high = (_finite(v, "interval") for v in interval)
        level = _finite(args["level"], "level")
        statistic = record_json({"diagnostic": name, "args": dict(given)})
        prediction = Prediction(
            experiment=index,
            statistic=statistic,
            interval=(low, high),
            level=level,
            hypothesis=_native_text(hypothesis),
        )
        out = self.ledger.commit_tool(prediction)
        if out.is_error or out.record is None:
            return out
        return ToolOutput(
            out.text,
            record={
                **out.record,
                "hypothesis": prediction.hypothesis,
                "note": note,
            },
        )

    # -- belief (P4 Δ4) -----------------------------------------------------------

    def belief(self, args: Mapping[str, Any]) -> ToolOutput:
        favoured = canonicalise(self._structure(args["favoured"], "favoured"))
        given = args["rivals"]
        if not isinstance(given, list) or not 1 <= len(given) <= MAX_RIVALS:
            raise LabError(
                f"rivals must be a list of 1 to {MAX_RIVALS} structures, got {given!r}"
            )
        rivals = [
            canonicalise(self._structure(r, f"rival {k}"))
            for k, r in enumerate(given, start=1)
        ]
        label = _native_text(favoured)
        labels = [_native_text(r) for r in rivals]
        for k, rival in enumerate(labels, start=1):
            if rival == label:
                raise LabError(
                    f"rival {k} is the favoured structure written another way; "
                    "each rival must differ from the favoured structure"
                )
            if rival in labels[: k - 1]:
                raise LabError(
                    f"rival {k} repeats rival {labels.index(rival) + 1} (the same "
                    "structure written another way); rivals must be distinct"
                )
        confidence = _confidence(args["confidence"])
        index = self.state.beliefs
        run = self.world.experiments_run
        self.state.beliefs += 1
        self.state.belief_at = run
        self.state.belief_structures = (
            (label, favoured),
            *zip(labels, rivals, strict=True),
        )
        view = self.view
        lines = [
            f"belief {index} recorded (after {run} of {self.experiments} experiments).",
            f"favoured: {view.render(favoured)}",
            *(f"rival {k}: {view.render(r)}" for k, r in enumerate(rivals, start=1)),
            f"confidence: {num(confidence)}",
        ]
        return ToolOutput(
            "\n".join(lines),
            record={
                "belief": index,
                "favoured": label,
                "rivals": labels,
                "confidence": confidence,
                "experiments_run": run,
            },
        )

    def _require_belief(self, action: str) -> None:
        """Refuse ``action`` unless a belief was recorded since the last experiment."""
        if self.state.belief_at == self.world.experiments_run:
            return
        when = (
            "no belief has been recorded yet"
            if self.state.belief_at is None
            else "an experiment has run since your last belief"
        )
        raise LabError(
            f"record your current belief with `belief` before {action}: {when}. "
            "Nothing was run and nothing was charged."
        )

    def _structure(self, text: object, what: str, *, hint: str = "") -> Structure:
        """Agent DSL ``text`` as a native structure; errors name ``what``."""
        tail = f"; {hint}" if hint else ""
        if not isinstance(text, str):
            raise LabError(
                f"{what} must be a structure in the modelling language, got "
                f"{text!r}{tail}"
            )
        try:
            return self.view.parse(text)
        except (GrammarError, ViewError) as error:
            raise LabError(f"{what} is not a structure: {error}{tail}") from None

    # -- notebook and python (AG-o) ---------------------------------------------

    def notebook(self, args: Mapping[str, Any]) -> ToolOutput:
        text = str(args["append"])
        if len(text) > _MAX_NOTEBOOK_APPEND:
            raise LabError(f"append at most {_MAX_NOTEBOOK_APPEND} characters")
        self.state.notebook.append(text)
        body = "\n\n".join(
            f"[{i}] {entry}" for i, entry in enumerate(self.state.notebook, start=1)
        )
        return ToolOutput(f"notebook ({len(self.state.notebook)} entries):\n{body}")

    def python(self, args: Mapping[str, Any]) -> ToolOutput:
        assert self.sandbox is not None
        code = str(args["code"])
        if len(code) > _MAX_CODE:
            raise LabError(f"code must be at most {_MAX_CODE} characters")
        try:
            result = self.sandbox.run(code)
        except SandboxError as error:
            # The sandbox itself failed (Docker down, image missing): an
            # infrastructure fault, recorded so the runner voids the run.
            self.state.infrastructure_faults.append(f"{type(error).__name__}: {error}")
            return ToolOutput(
                "the sandbox is unavailable; this call did not run", is_error=True
            )
        status = f"exit code {result.exit_code}"
        if result.timed_out:
            status += " (stopped: time limit)"
        text = f"{status}\n--- stdout ---\n{result.stdout}"
        if result.stderr:
            text += f"\n--- stderr ---\n{result.stderr}"
        return ToolOutput(
            text,
            record={
                "sandbox_digest": result_digest(result),
                "image": self.sandbox.image,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            },
        )

    # -- submit -----------------------------------------------------------------

    def submit(self, args: Mapping[str, Any]) -> ToolOutput:
        self._require_belief("submitting")
        reason = args["stop_reason"]
        if not isinstance(reason, str) or reason not in STOP_REASONS:
            raise LabError(
                f"stop_reason must be one of {', '.join(STOP_REASONS)}; got {reason!r}"
            )
        note = _note(args.get("note"))
        structure = self.view.parse(str(args["structure"]))
        report = str(args["report"])
        self.state.submitted = structure
        self.state.report = report
        return ToolOutput(
            f"submitted: {self.view.render(structure)}. The investigation is over.",
            record={
                "structure": _native_text(structure),
                "structure_hash": structure_hash(structure),
                "stop_reason": reason,
                "note": note,
            },
        )

    # -- the layer --------------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return tool_specs(
            self.view,
            self.arm,
            experiments=self.experiments,
            fits=self.fits,
            policy=self.policy,
            handlers={
                "run_experiment": self.run_experiment,
                "diagnostic": self.diagnostic,
                "nonparam_kernels": self.nonparam_kernels,
                "fit": self.fit,
                "predict": self.predict,
                "belief": self.belief,
                "notebook": self.notebook,
                "python": self.python,
                "submit": self.submit,
            },
        )

    def budgets(self) -> dict[str, int]:
        return {
            BUDGET_EXPERIMENTS: self.experiments,
            BUDGET_FITS: self.fits,
            BUDGET_SUBMIT: 1,
        }

    def _footer(self, meters: Mapping[str, BudgetMeter]) -> str:
        experiments = meters[BUDGET_EXPERIMENTS]
        fits = meters[BUDGET_FITS]
        return budget_line(
            experiments.remaining, experiments.limit, fits.remaining, fits.limit
        )

    def layer(self) -> ToolLayer:
        return ToolLayer(self.specs(), self.budgets(), footer=self._footer)


def tool_specs(
    view: AgentView,
    arm: Arm,
    *,
    experiments: int,
    fits: int,
    handlers: Mapping[str, Handler] | None = None,
    policy: str | None = None,
) -> list[ToolSpec]:
    """Every tool the agent is offered, for one view, arm and budget.

    A pure function of its arguments and this module's constants: no world is
    read, so the schemas render from configuration alone
    (:func:`~sciagent.investigation.runner.rendered_config`, the session key's
    input, P4 Δ7). :meth:`Lab.specs` passes its bound handlers; without
    ``handlers`` each tool's handler refuses (a rendering, not a lab).
    ``policy`` (P4 Δ11, AG-c only) gives ``run_experiment`` policy mode's
    description and an empty schema; every other spec is unchanged.
    """
    try:
        policy_name = as_policy(policy)
        check_policy_arm(arm, policy_name)
    except PromptError as error:
        raise LabError(str(error)) from None

    def handler(name: str) -> Handler:
        if handlers is None:
            return _render_only
        return handlers[name]

    data_id = _string("A dataset id: `obs`, or `e0`, `e1`, ... for experiments.")
    diagnostic_names = [agent for agent, _ in view.diagnostics()]
    specs = [
        _run_experiment_spec(view, arm, experiments, policy_name, handler),
        ToolSpec(
            "diagnostic",
            "Compute one catalogue statistic on one dataset. Time arguments "
            "are in the dataset's time units and default to multiples of its "
            "mean gap between events. Catalogue:\n" + _catalogue_text(view),
            _schema(
                {
                    "name": {"type": "string", "enum": diagnostic_names},
                    "args": {
                        "type": "object",
                        "description": "The statistic's arguments; omit for defaults.",
                    },
                    "data_id": data_id,
                },
                ["name", "data_id"],
            ),
            handler("diagnostic"),
        ),
        ToolSpec(
            "nonparam_kernels",
            "Model-free linear estimate of how past events raise the rate "
            "(Wiener-Hopf equations solved from second-order statistics, no "
            "structure assumed). Drivers: `events` (every past event, weight "
            "1) and one per mark channel (weight z for real-valued channels, "
            "the value for -1/+1 channels). Reports each driver's kernel on a "
            "lag grid and its integral. Sees only linear second-order "
            "structure: gates, thresholds and non-identity links are "
            "invisible to it.",
            _schema({"data_id": data_id}, ["data_id"]),
            handler("nonparam_kernels"),
        ),
        ToolSpec(
            "fit",
            "Certified maximum-likelihood fit of a structure (modelling-language "
            "text) jointly over the named datasets: log-likelihood, BIC, "
            "time-rescaling KS test, fitted coefficients and shape parameters. "
            f"Costs one of the {fits} fits unless the same structure "
            "was already fitted on the same data.",
            _schema(
                {
                    "structure": _string("A structure in the modelling language."),
                    "data_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Dataset ids to fit jointly.",
                    },
                },
                ["structure", "data_ids"],
            ),
            handler("fit"),
            budget=BUDGET_FITS,
        ),
        ToolSpec(
            "predict",
            "Commit a prediction for an experiment that has not run yet. The "
            "framework computes the named catalogue diagnostic on that "
            "experiment's data when it runs and reports whether it fell in "
            "your interval. Refused for an experiment that already ran.",
            _schema(
                {
                    "experiment": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Experiment index (0 for e0, ...).",
                    },
                    "diagnostic": {"type": "string", "enum": diagnostic_names},
                    "args": {
                        "type": "object",
                        "description": "The diagnostic's arguments; omit for "
                        "defaults (resolved on the experiment's data).",
                    },
                    "hypothesis": _string(
                        "The structure this prediction follows from: "
                        "modelling-language text only (commentary goes in "
                        "`note`)."
                    ),
                    "interval": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                        "description": "[low, high], a central interval.",
                    },
                    "level": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "exclusiveMaximum": 1,
                        "description": "Probability the interval holds the value.",
                    },
                    "note": _string(
                        f"Optional free text (at most {MAX_NOTE} characters), "
                        "e.g. why you expect this. Recorded, not shown back."
                    ),
                },
                ["experiment", "diagnostic", "hypothesis", "interval", "level"],
            ),
            handler("predict"),
        ),
        ToolSpec(
            "belief",
            "Record your current belief: the structure you favour, 1 to "
            f"{MAX_RIVALS} rival structures you have not ruled out (the first "
            "is your runner-up), and your confidence, a probability strictly "
            "between 0 and 1, that the favoured structure is the true "
            "mechanism. All must be different structures. Free. Needed "
            "before every experiment (after the previous one) and before "
            "`submit`.",
            _schema(
                {
                    "favoured": _string("The structure you currently favour."),
                    "rivals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": MAX_RIVALS,
                        "description": "Rival structures, runner-up first.",
                    },
                    "confidence": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "exclusiveMaximum": 1,
                        "description": "Probability that the favoured "
                        "structure is the true mechanism.",
                    },
                },
                ["favoured", "rivals", "confidence"],
            ),
            handler("belief"),
        ),
    ]
    if arm == "AG-o":
        specs += [
            ToolSpec(
                "notebook",
                "Append to your lab notebook; the whole notebook is shown back.",
                _schema({"append": _string("Text to append.")}, ["append"]),
                handler("notebook"),
            ),
            ToolSpec(
                "python",
                "Run Python code in the isolated sandbox (fresh process per "
                "call; data read-only in /data; persist files in /work). "
                "Returns the exit code, stdout and stderr.",
                _schema({"code": _string("Python source to run.")}, ["code"]),
                handler("python"),
            ),
        ]
    specs.append(
        ToolSpec(
            "submit",
            "Submit your final structure and a short report, with why you are "
            "stopping. Once; ends the investigation. Refused, uncharged, "
            "unless you have recorded a `belief` since the last experiment.",
            _schema(
                {
                    "structure": _string("The structure in the modelling language."),
                    "report": _string("A short report of what you found and why."),
                    "stop_reason": {
                        "type": "string",
                        "enum": list(STOP_REASONS),
                        "description": "Why you are stopping now.",
                    },
                    "note": _string(
                        f"Optional free text (at most {MAX_NOTE} characters) "
                        "on the stopping decision. Recorded, not shown back."
                    ),
                },
                ["structure", "report", "stop_reason"],
            ),
            handler("submit"),
            budget=BUDGET_SUBMIT,
            terminal=True,
        )
    )
    return specs


def _run_experiment_spec(
    view: AgentView,
    arm: Arm,
    experiments: int,
    policy: PolicyName | None,
    handler: Callable[[str], Handler],
) -> ToolSpec:
    """``run_experiment``: the agent's design, or policy mode's no arguments."""
    if policy is not None:
        return ToolSpec(
            "run_experiment",
            "Run the next experiment, chosen by the framework (see the system "
            "prompt): a design, run on a fresh realisation of the process, and "
            "one catalogue diagnostic to read on its data. Takes no arguments. "
            f"Costs one of the {experiments} experiments. A run that reaches "
            f"the limit of {MAX_EVENTS} events is truncated there and kept. "
            "Returns the design that was run, a summary, the chosen "
            "diagnostic's value and the new dataset's id, and evaluates any "
            "predictions committed for this experiment. Refused, uncharged, "
            "unless you have recorded a `belief` since the last experiment.",
            _schema({}, []),
            handler("run_experiment"),
            budget=BUDGET_EXPERIMENTS,
        )
    return ToolSpec(
        "run_experiment",
        "Run one new experiment: a fresh realisation of the process from "
        "empty history on [0, horizon] under an intervention (see the "
        f"system prompt). Costs one of the {experiments} experiments. "
        f"Default horizon {num(view.time(DEFAULT_HORIZON))}. Forced times "
        "may be a generator {start, step, n} and marks a pattern "
        "{constant}, {alternate} or {repeat}. A run that reaches the "
        f"limit of {MAX_EVENTS} events is truncated there and kept. "
        "Returns a summary and the new dataset's id, and evaluates any "
        "predictions committed for this experiment. Refused, uncharged, "
        "unless you have recorded a `belief` since the last experiment.",
        _design_schema(arm),
        handler("run_experiment"),
        budget=BUDGET_EXPERIMENTS,
    )


def _design_schema(arm: Arm) -> dict[str, Any]:
    """``run_experiment``'s input schema: the design schema, plus for AG-o
    the ``{"from": "/work/..."}`` alternative for the intervention.

    It carries no time-valued constant, so it is the same in both
    conditions (times are in the agent's units by construction).
    """
    schema: dict[str, Any] = {
        k: copy.deepcopy(v)
        for k, v in DESIGN_SCHEMA.items()
        if k not in ("$schema", "title")
    }
    if arm == "AG-o":
        properties: dict[str, Any] = schema["properties"]
        properties["intervention"] = {
            "oneOf": [
                properties["intervention"],
                {
                    "type": "object",
                    "properties": {
                        FROM_KEY: {
                            "type": "string",
                            "pattern": _WORK_PATH.pattern,
                            "description": "A JSON file you wrote under "
                            f"{WORK_PREFIX} holding one intervention "
                            "(generators allowed); at most "
                            f"{MAX_DESIGN_FILE_BYTES} bytes.",
                        }
                    },
                    "required": [FROM_KEY],
                    "additionalProperties": False,
                },
            ]
        }
    return schema


def _render_only(args: Mapping[str, Any]) -> ToolOutput:
    raise LabError("these tool specs were rendered without a lab; they run nothing")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ordered_ids(datasets: Mapping[str, Dataset]) -> list[str]:
    return sorted(datasets, key=_id_order)


def _id_order(data_id: str) -> tuple[int, int]:
    if data_id == OBSERVATIONAL:
        return (0, 0)
    return (1, int(data_id[1:]))


def _canonical_ids(value: object, datasets: Mapping[str, Dataset]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise LabError("data_ids must be a non-empty list of dataset ids")
    ids: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not _DATA_ID.match(item)
            or item not in datasets
        ):
            raise LabError(
                f"no dataset {item!r}; available: {', '.join(_ordered_ids(datasets))}"
            )
        if item not in ids:
            ids.append(item)
    return sorted(ids, key=_id_order)


def _simulable(result: FitResult) -> bool | None:
    """The fitter's simulability flag, when the fitter reports one.

    Read by name because the flag is being added to :class:`FitResult`
    concurrently; until then fits report None and nothing is shown.
    """
    flag = getattr(result, "simulable", None)
    return None if flag is None else bool(flag)


def _counted(data: Dataset) -> int:
    """Events that enter the likelihood: endogenous, outside excluded windows."""
    times = data.log.times
    keep = np.asarray(data.endogenous, dtype=np.bool_).copy()
    for start, end in data.excluded:
        keep &= ~((times >= start) & (times < end))
    return int(np.count_nonzero(keep))


def _finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LabError(f"{what} must be a number, got {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise LabError(f"{what} must be finite, got {value!r}")
    return v


def _native_text(structure: Structure) -> str:
    """The record's label of a structure: its canonical form, in native names."""
    return render(canonicalise(structure))


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LabError(f"confidence must be a number in (0, 1), got {value!r}")
    c = float(value)
    if not (math.isfinite(c) and 0.0 < c < 1.0):
        raise LabError(f"confidence must be strictly between 0 and 1, got {value!r}")
    return c


def _note(value: object) -> str | None:
    """An optional free ``note``: recorded, never shown back."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise LabError(f"note must be text, got {value!r}")
    if len(value) > MAX_NOTE:
        raise LabError(f"note must be at most {MAX_NOTE} characters")
    return value


def _args_text(args: Mapping[str, object]) -> str:
    if not args:
        return "none"
    return ", ".join(
        f"{k}={num(v) if isinstance(v, float) else v}" for k, v in args.items()
    )


#: Catalogue descriptions that spell out another diagnostic's native name
#: (``fano_factor``, ``burstiness``, ``mean_rate``), which the anonymised
#: condition must not show. Reworded identically in both conditions, so the
#: two differ only in names and units. The wording belongs in the catalogue;
#: this table goes once it is changed there.
_REWORDING: Final = (
    ("Fano factor of windowed counts", "Variance over mean of windowed counts"),
    (
        "Slope of the Fano factor against",
        "Slope of the count variance over mean against",
    ),
    ("Burstiness of the gaps,", "Irregularity of the gaps,"),
    ("the count the mean rate predicts", "the count the average rate predicts"),
)


def _reworded(text: str) -> str:
    for old, new in _REWORDING:
        text = text.replace(old, new)
    return text


def _catalogue_text(view: AgentView) -> str:
    lines = []
    for agent, native in view.diagnostics():
        spec = catalogue.get(native)
        arg_text = (
            "; args: "
            + ", ".join(
                f"{a.name} ({a.kind.value}): {a.description}" for a in spec.args
            )
            if spec.args
            else ""
        )
        lines.append(f"- {agent}: {_reworded(spec.description)}{arg_text}")
    return "\n".join(lines)


def _driver_text(driver: str, view: AgentView) -> str:
    if driver == EVENTS_DRIVER:
        return f"{EVENTS_DRIVER} (weight 1)"
    spec = next(s for s in view.channels if s.name == driver)
    if spec.kind is ChannelKind.SIGN:
        return f"{driver} (weight = its -1/+1 value)"
    return (
        f"{driver} (weight z = ({driver} - {num(spec.location)}) / {num(spec.scale)})"
    )


def _read_work_file(work_dir: Path, path: str) -> bytes:
    """The bytes of the agent's design file ``path`` (``/work/...``), checked.

    Refused (:class:`LabError`): a path not of the form ``/work/<segments>``
    (no ``.``/``..`` segments, no backslashes), a missing file, any link or
    Windows reparse point on the way (every component is ``lstat``-ed, none is
    followed), anything but a regular file, and a file over
    :data:`MAX_DESIGN_FILE_BYTES`. The size is checked again on the bytes read.
    """
    segments = path.split("/")[2:]
    if not _WORK_PATH.match(path) or any(s in (".", "..") for s in segments):
        raise LabError(
            f"a design file must be under {WORK_PREFIX} (a path such as "
            f"{WORK_PREFIX}design.json, without '.' or '..' parts), got {path!r}"
        )
    current = work_dir
    for k, segment in enumerate(segments):
        current = current / segment
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            raise LabError(f"no file {path}") from None
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise LabError(f"design file {path} is reached through a link; refused")
        last = k == len(segments) - 1
        if not last and not stat.S_ISDIR(info.st_mode):
            raise LabError(f"no file {path}")
        if last and not stat.S_ISREG(info.st_mode):
            raise LabError(f"design file {path} is not a regular file")
        if last and info.st_size > MAX_DESIGN_FILE_BYTES:
            raise LabError(
                f"design file {path} is larger than {MAX_DESIGN_FILE_BYTES} bytes"
            )
    with current.open("rb") as handle:
        payload = handle.read(MAX_DESIGN_FILE_BYTES + 1)
    if len(payload) > MAX_DESIGN_FILE_BYTES:
        raise LabError(
            f"design file {path} is larger than {MAX_DESIGN_FILE_BYTES} bytes"
        )
    return payload


def _stop_reason(error: SimulationError) -> str:
    if isinstance(error, ExplosionError):
        # Reaching the event cap truncates the run (P4 Δ2); what still raises
        # is the candidate cap and the runaway-bound guard. Event counts are
        # unit-free, so the text is the same in every condition.
        return (
            "the process's event rate grew without bound, so the simulation "
            f"was stopped before it reached the limit of {MAX_EVENTS} events"
        )
    if isinstance(error, NegativeIntensityError):
        return "the event rate became negative under this design"
    return f"the simulation could not continue ({type(error).__name__})"


def _outcome_line(outcome: PredictionOutcome, view: AgentView) -> str:
    spec = json.loads(outcome.statistic)
    name = view.diagnostic(spec["diagnostic"])
    low, high = outcome.interval
    if outcome.value is None:
        verdict = f"could not be evaluated ({outcome.error})"
    else:
        verdict = (
            f"value {num(outcome.value)}: "
            + ("inside" if outcome.covered else "OUTSIDE")
            + f" [{num(low)}, {num(high)}]"
        )
    level = num(outcome.level)
    return f"- prediction {outcome.commitment} ({name}, level {level}): {verdict}"
