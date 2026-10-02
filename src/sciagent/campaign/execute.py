"""One job in a worker process: run systems on a (truth, seed) and score them.

Two kinds of job, both pure functions of picklable inputs, so they run in a
process pool and return plain results (the store is written by the driver):

- :func:`run_baseline_job`: rebuild the investigation's
  :class:`~sciagent.investigation.world.World` for (truth, seed), take its
  observational dataset (the same data every LLM arm starts from), run the
  requested non-LLM systems on it, and score each.
- :func:`run_score_job`: score an LLM session after the fact from the
  structures read out of its record (:mod:`sciagent.campaign.llm`).

**Scores** (SPEC §4.3), all against data no system saw:

- ``per_event``: held-out log-likelihood per counted event on the
  (truth, seed)'s held-out log (:func:`~sciagent.scoring.heldout.held_out_dataset`,
  the same for every system on that pair). The *gap* to ORACLE and the share
  closed against B-lib need the other systems' values, so they are computed at
  report time (:mod:`sciagent.campaign.analysis`) from these raw values;
- ``exact`` and ``distance``: structural recovery against the truth;
- ``similarity``: interventional similarity on the fixed battery;
- the efficiency curve: ``per_event`` of the system's own best-so-far model at
  each fit (the scorer's curve evaluated at an oracle value of 0, so its
  ``gap`` field is the raw per-event value).

**LLM sessions** (:func:`run_score_job`). The submitted structure is fitted by
the certified fitter on the observational dataset only, exactly as every
baseline's final model is, so the held-out comparison is about the structure,
not about how much data the arm collected. The efficiency curve refits each
*distinct charged* fit's structure on the observational data, in the order the
agent fitted them, and takes the best-so-far by BIC, the search baselines'
selection rule. It is therefore the curve of the agent's proposals under the
baselines' selection, comparable point for point with B-sym's; it is not a
replay of the agent's own fits (some of which used experimental data, on which
BIC is not comparable across fits).

**Failures.** A system that raises a framework error
(:class:`~sciagent.core.errors.SciAgentError`) or a numerical one
(``ValueError``, including ``LinAlgError``, or ``ArithmeticError``) on its
unit is recorded as a failed unit (``status = 0``) with the error's type and
message: it is a result about that system on that truth, and the campaign
goes on. Anything else is a bug and propagates to the driver, which logs it
and records nothing.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sciagent.campaign.plan import (
    B_LIB,
    B_NP,
    B_RAND,
    B_SPARSE,
    B_SYM,
    B_SYM_10F,
    ORACLE,
    PLANTED,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    search_seed,
)
from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitError, FitResult, fit
from sciagent.glm.grammar import Structure
from sciagent.glm.syntax import parse, render
from sciagent.investigation.world import OBSERVATIONAL, World
from sciagent.scoring.battery import interventional_similarity
from sciagent.scoring.efficiency import efficiency_curve
from sciagent.scoring.heldout import held_out_dataset
from sciagent.scoring.structure import structural_recovery
from sciagent.scoring.truth import TruthModel
from sciagent.systems.v2.models import FittedModel, GLMModel
from sciagent.systems.v2.search import BRand, BSym, PlantedHint
from sciagent.systems.v2.sparse import BSparse, SparseReport
from sciagent.systems.v2.systems import (
    BLib,
    BNp,
    InvestigationData,
    Oracle,
    SystemResult,
)

type Json = Any

#: The failures recorded as a unit's result (module docstring).
UNIT_FAILURES: Final = (SciAgentError, ValueError, ArithmeticError)


@dataclass(frozen=True)
class UnitResult:
    """One unit's reading and detail, plus wall times (never addressed)."""

    system: str
    ok: bool
    reading: dict[str, float]
    detail: dict[str, Json]
    wall_s: float
    timing: dict[str, float] | None = None


@dataclass(frozen=True)
class BaselineJob:
    """Non-LLM systems to run on one (truth, seed)."""

    truth: TruthEntry
    seed: int
    systems: tuple[str, ...]
    env: CampaignEnvironment
    config: CampaignConfig


@dataclass(frozen=True)
class ScoreJob:
    """An LLM session to score: its submission and charged fits, native DSL."""

    truth: TruthEntry
    seed: int
    system: str
    submission: str | None
    fitted: tuple[str, ...]
    env: CampaignEnvironment
    config: CampaignConfig


# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------


def observational_data(
    truth: TruthEntry, seed: int, env: CampaignEnvironment
) -> InvestigationData:
    """The (truth, seed)'s observational dataset, as its world draws it."""
    world = World(truth.spec, seed)
    return InvestigationData((world.datasets[OBSERVATIONAL],), env.channels, env.marks)


def _failure(system: str, error: BaseException, wall: float) -> UnitResult:
    return UnitResult(
        system,
        False,
        {"status": 0.0},
        {"error": type(error).__name__, "message": str(error)[:2000]},
        wall,
    )


def _certified(model: FittedModel) -> float:
    if isinstance(model, GLMModel):
        return 1.0 if model.fit.certified else 0.0
    return math.nan


def score_submission(
    truth: TruthEntry,
    seed: int,
    structure: Structure | None,
    model: FittedModel,
    held: Dataset,
    env: CampaignEnvironment,
) -> tuple[dict[str, float], dict[str, Json]]:
    """Scores 1-3 of a submitted model (module docstring)."""
    per_event = model.held_out_per_event([held])
    st = structural_recovery(structure, truth.structure)
    sim = interventional_similarity(
        TruthModel(truth.spec), model, env.channels, truth.id, seed
    )
    reading = {
        "status": 1.0,
        "per_event": per_event,
        "exact": 1.0 if st.exact else 0.0,
        "distance": st.distance,
        "has_structure": 1.0 if st.submitted else 0.0,
        "similarity": sim.similarity,
        "certified": _certified(model),
    }
    detail: dict[str, Json] = {
        "structure": None if structure is None else render(structure),
        "model": model.name,
        "n_params": model.n_params,
        "similarity_by_experiment": [[e.name, e.discrepancy] for e in sim.experiments],
    }
    return reading, detail


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------


def _run_system(
    system: str, job: BaselineJob, data: InvestigationData
) -> tuple[SystemResult, dict[str, Json], float]:
    """Run one system; return its result, extra detail and internal wall time."""
    env, config, truth = job.env, job.config, job.truth
    fc = config.fit_config
    rng_seed = search_seed(truth.id, job.seed)
    extra: dict[str, Json] = {}
    start = time.perf_counter()
    match system:
        case "ORACLE":
            result = Oracle(truth.structure, fit_config=fc).run(data)
        case "B-lib":
            result = BLib(env.library, fit_config=fc).run(data)
        case "B-np":
            result = BNp().run(data)
        case "B-rand":
            result = BRand(
                env.prior, budget=config.fits, seed=rng_seed, fit_config=fc
            ).run(data)
        case "B-sym" | "B-sym@10F":
            factor = config.control_factor if system == B_SYM_10F else 1
            result = BSym(
                env.sym_seeds,
                config.gp,
                budget=config.fits * factor,
                seed=rng_seed,
                fit_config=fc,
                name=system,
            ).run(data)
        case "planted-hint":
            result = PlantedHint(
                truth.structure,
                env.sym_seeds,
                config.gp,
                budget=config.fits,
                seed=rng_seed,
                fit_config=fc,
            ).run(data)
        case "B-sparse":
            result, report = BSparse(config.sparse).run_with_report(data)
            extra = _sparse_detail(report)
        case _:
            raise CampaignError(f"unknown baseline system {system!r}")
    return result, extra, time.perf_counter() - start


def _sparse_detail(report: SparseReport) -> dict[str, Json]:
    return {
        "sparse": {
            "link": report.link.value,
            "capped": report.capped,
            "stop": report.stop,
            "selected_index": report.selected_index,
            "path_points": len(report.path),
            "n_groups": report.n_groups,
            "n_internal_fits": report.n_internal_fits,
            "final_certified": report.final_certified,
        }
    }


def _curve(result: SystemResult, held: Dataset) -> list[list[Json]]:
    # The scorer's curve at an oracle value of 0: its gap is the raw per-event
    # held-out value; the driver's report subtracts the ORACLE's.
    return [[p.fits_used, p.best, p.gap] for p in efficiency_curve(result, 0.0, [held])]


def run_baseline_job(job: BaselineJob) -> tuple[UnitResult, ...]:
    """Run and score ``job.systems`` on one (truth, seed) (module docstring)."""
    unknown = [s for s in job.systems if s not in _KNOWN]
    if unknown:
        raise CampaignError(f"unknown baseline systems {unknown}")
    start = time.perf_counter()
    try:
        data = observational_data(job.truth, job.seed, job.env)
        held = held_out_dataset(job.truth.spec, job.truth.id, job.seed)
    except UNIT_FAILURES as error:
        wall = time.perf_counter() - start
        return tuple(_failure(s, error, wall) for s in job.systems)
    out: list[UnitResult] = []
    for system in job.systems:
        t0 = time.perf_counter()
        try:
            result, extra, run_s = _run_system(system, job, data)
            reading, detail = score_submission(
                job.truth, job.seed, result.structure, result.model, held, job.env
            )
            curve = _curve(result, held)
        except UNIT_FAILURES as error:
            out.append(_failure(system, error, time.perf_counter() - t0))
            continue
        reading["fits_used"] = float(result.fits_used)
        detail.update(extra)
        detail.update(
            {
                "submitted": result.submitted,
                "skipped": list(result.skipped),
                "curve": curve,
                "observational_events": data.observational[0].log.n,
                "held_out_events": held.log.n,
            }
        )
        out.append(
            UnitResult(
                system,
                True,
                reading,
                detail,
                time.perf_counter() - t0,
                {"run_s": run_s},
            )
        )
    return tuple(out)


_KNOWN: Final = frozenset(
    {ORACLE, B_LIB, B_NP, B_RAND, B_SYM, B_SYM_10F, PLANTED, B_SPARSE}
)


# --------------------------------------------------------------------------
# LLM scoring
# --------------------------------------------------------------------------


def _fit_obs(
    structure: Structure, data: InvestigationData, job: ScoreJob
) -> FitResult | None:
    try:
        return fit(
            structure, data.observational, data.channels, config=job.config.fit_config
        )
    except FitError:
        return None


def _criterion(result: FitResult | None) -> float:
    if result is None or not result.certified or not math.isfinite(result.bic):
        return math.inf
    return result.bic


def llm_curve(
    fitted: Sequence[Structure],
    data: InvestigationData,
    held: Dataset,
    job: ScoreJob,
    cache: dict[str, FitResult | None],
) -> list[list[Json]]:
    """Best-so-far held-out per event over the agent's charged fits (docstring)."""
    curve: list[list[Json]] = []
    best: tuple[float, str, FitResult] | None = None
    values: dict[str, float] = {}
    for k, structure in enumerate(fitted, start=1):
        key = structure_hash(structure)
        if key not in cache:
            cache[key] = _fit_obs(structure, data, job)
        result = cache[key]
        crit = _criterion(result)
        if (
            result is not None
            and math.isfinite(crit)
            and (best is None or crit < best[0])
        ):
            best = (crit, key, result)
        if best is None:
            continue
        if best[1] not in values:
            model = GLMModel(
                render(best[2].structure), best[2], data.channels, data.marks
            )
            values[best[1]] = model.held_out_per_event([held])
        curve.append([k, render(best[2].structure), values[best[1]]])
    return curve


def run_score_job(job: ScoreJob) -> UnitResult:
    """Score one LLM session (module docstring)."""
    start = time.perf_counter()
    channels = job.env.channels
    try:
        data = observational_data(job.truth, job.seed, job.env)
        held = held_out_dataset(job.truth.spec, job.truth.id, job.seed)
        fitted = [canonicalise(parse(text, channels)) for text in job.fitted]
        cache: dict[str, FitResult | None] = {}
        curve = llm_curve(fitted, data, held, job, cache)
        if job.submission is None:
            return UnitResult(
                job.system,
                True,
                {"status": 2.0, "exact": 0.0, "has_structure": 0.0},
                {"structure": None, "curve": curve, "no_submission": True},
                time.perf_counter() - start,
            )
        structure = canonicalise(parse(job.submission, channels))
        key = structure_hash(structure)
        if key not in cache:
            cache[key] = _fit_obs(structure, data, job)
        result = cache[key]
        if result is None:
            raise CampaignError(f"the submitted structure could not be fitted: {key}")
        model = GLMModel(job.system, result, channels, job.env.marks)
        reading, detail = score_submission(
            job.truth, job.seed, structure, model, held, job.env
        )
    except UNIT_FAILURES as error:
        return _failure(job.system, error, time.perf_counter() - start)
    reading["fits_used"] = float(len(job.fitted))
    detail.update(
        {
            "curve": curve,
            "observational_events": data.observational[0].log.n,
            "held_out_events": held.log.n,
        }
    )
    return UnitResult(job.system, True, reading, detail, time.perf_counter() - start)
