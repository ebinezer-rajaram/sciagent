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
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

import numpy as np

from sciagent.core.errors import SciAgentError
from sciagent.diagnostics import catalogue
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, FitResult, fit, fit_cache_key
from sciagent.glm.grammar import ChannelKind, Structure, n_columns, psi_slots
from sciagent.glm.interventions import (
    DEFAULT_HORIZON,
    EXPERIMENT_SCHEMA,
    canonical_json,
)
from sciagent.glm.simulate import (
    ExplosionError,
    NegativeIntensityError,
    SimulationError,
)
from sciagent.glm.syntax import render
from sciagent.harness.ledger import Prediction, PredictionLedger
from sciagent.harness.record import canonical_json as record_json
from sciagent.harness.tools import ToolLayer, ToolOutput, ToolSpec
from sciagent.investigation.prompts import Arm, num
from sciagent.investigation.view import AgentView
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
    "SUMMARY_DIAGNOSTICS",
    "Lab",
    "LabError",
    "PredictionOutcome",
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
_DATA_ID: Final = re.compile(r"^(obs|e(0|[1-9][0-9]*))$")


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
    ) -> None:
        if arm not in ("AG-c", "AG-o"):
            raise LabError(f"arm must be 'AG-c' or 'AG-o', got {arm!r}")
        if (arm == "AG-o") != (sandbox is not None):
            raise LabError("AG-o needs a sandbox and AG-c must not have one")
        if experiments < 0 or fits < 0:
            raise LabError("budgets must be non-negative")
        self.world = world
        self.view = view
        self.arm: Arm = arm
        self.experiments = experiments
        self.fits = fits
        self.sandbox = sandbox
        # Pinned framework-side (invariant 2): no agent argument reaches the
        # fit configuration, so certification cannot be loosened by a request.
        self.fit_config = replace(FIT_CONFIG, workers=fit_workers)
        self.ledger = PredictionLedger()
        self.state = _State()
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
        native = self._native_data(data_id)
        return self.view.dataset(native)

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
        native = self.view.experiment_native(args)
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
            return ToolOutput(
                f"experiment {experiment_id(index)} was stopped and produced no "
                f"dataset: {reason}. It counts against the budget.",
                record={
                    "experiment": index,
                    "design": design,
                    "horizon": native.horizon,
                    "stopped": True,
                },
            )
        data_id = experiment_id(index)
        if self.sandbox is not None:
            self._publish(data_id)
        values = self._evaluate(index, data_id)
        used = self.world.experiments_run
        text = [
            f"experiment {data_id} done ({used} of {self.experiments} experiments "
            f"used).",
            self._summary(data_id),
        ]
        if values:
            text.append("committed predictions for this experiment:")
            text.extend(_outcome_line(o, self.view) for o in values)
        return ToolOutput(
            "\n".join(text),
            record={
                "experiment": index,
                "design": design,
                "horizon": native.horizon,
                "n_events": self.world.datasets[data_id].log.n,
                "predictions": [o.as_json() for o in values],
            },
        )

    def _evaluate(self, index: int, data_id: str) -> list[PredictionOutcome]:
        log = self._agent_data(data_id).log
        errors: dict[int, str] = {}

        def statistic(prediction: Prediction) -> float:
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
            else "coordinate-optimal on the grid"
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
        hypothesis = self.view.parse(str(args["hypothesis"]))
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
        return self.ledger.commit_tool(prediction)

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
        structure = self.view.parse(str(args["structure"]))
        report = str(args["report"])
        self.state.submitted = structure
        self.state.report = report
        return ToolOutput(
            f"submitted: {self.view.render(structure)}. The investigation is over.",
            record={
                "structure": _native_text(structure),
                "structure_hash": structure_hash(structure),
            },
        )

    # -- the layer --------------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        view = self.view
        experiment_schema = {
            k: v for k, v in EXPERIMENT_SCHEMA.items() if k not in ("$schema", "title")
        }
        data_id = _string("A dataset id: `obs`, or `e0`, `e1`, ... for experiments.")
        diagnostic_names = [agent for agent, _ in view.diagnostics()]
        specs = [
            ToolSpec(
                "run_experiment",
                "Run one new experiment: a fresh realisation of the process from "
                "empty history on [0, horizon] under an intervention (see the "
                f"system prompt). Costs one of the {self.experiments} experiments. "
                f"Default horizon {num(view.time(DEFAULT_HORIZON))}. Returns a "
                "summary and the new dataset's id, and evaluates any predictions "
                "committed "
                "for this experiment.",
                experiment_schema,
                self.run_experiment,
                budget=BUDGET_EXPERIMENTS,
            ),
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
                            "description": "The statistic's arguments; omit for "
                            "defaults.",
                        },
                        "data_id": data_id,
                    },
                    ["name", "data_id"],
                ),
                self.diagnostic,
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
                self.nonparam_kernels,
            ),
            ToolSpec(
                "fit",
                "Certified maximum-likelihood fit of a structure (modelling-language "
                "text) jointly over the named datasets: log-likelihood, BIC, "
                "time-rescaling KS test, fitted coefficients and shape parameters. "
                f"Costs one of the {self.fits} fits unless the same structure "
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
                self.fit,
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
                            "The structure this prediction follows from."
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
                    },
                    ["experiment", "diagnostic", "hypothesis", "interval", "level"],
                ),
                self.predict,
            ),
        ]
        if self.arm == "AG-o":
            specs += [
                ToolSpec(
                    "notebook",
                    "Append to your lab notebook; the whole notebook is shown back.",
                    _schema({"append": _string("Text to append.")}, ["append"]),
                    self.notebook,
                ),
                ToolSpec(
                    "python",
                    "Run Python code in the isolated sandbox (fresh process per "
                    "call; data read-only in /data; persist files in /work). "
                    "Returns the exit code, stdout and stderr.",
                    _schema({"code": _string("Python source to run.")}, ["code"]),
                    self.python,
                ),
            ]
        specs.append(
            ToolSpec(
                "submit",
                "Submit your final structure and a short report. Once; ends the "
                "investigation.",
                _schema(
                    {
                        "structure": _string(
                            "The structure in the modelling language."
                        ),
                        "report": _string("A short report of what you found and why."),
                    },
                    ["structure", "report"],
                ),
                self.submit,
                budget=BUDGET_SUBMIT,
                terminal=True,
            )
        )
        return specs

    def budgets(self) -> dict[str, int]:
        return {
            BUDGET_EXPERIMENTS: self.experiments,
            BUDGET_FITS: self.fits,
            BUDGET_SUBMIT: 1,
        }

    def layer(self) -> ToolLayer:
        return ToolLayer(self.specs(), self.budgets())


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
    return render(structure)


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


def _stop_reason(error: SimulationError) -> str:
    if isinstance(error, ExplosionError):
        return f"the process produced more than {MAX_EVENTS} events"
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
