"""The truth sampler (SPEC §3): candidates through every constraint, into a split.

A **candidate** is identified by ``(split, dictionary kind, index)``; its
stream key prefix is ``f"{split}/{'ood' if out_of_dictionary else 'ind'}/{index}"``
and every draw it makes comes from ``stream(seed, f"{prefix}/<purpose>")``
(``scenarios.streams``). :func:`evaluate_candidate` is a pure function of
``(config, environment, split, seed, kind, index)``. Checks run cheapest
first, and the first failure rejects with a reason code:

1. **structure** from the prior (``prior.sample_structure``; purpose
   ``structure``);
2. **non-membership**: ``structure_distance`` to every in-grammar library
   member must exceed ε (``library_near``);
3. **ψ** on the truth grids (``psi``);
4. **θ** from the θ prior at the operating point (``reference``, ``theta``;
   ``degenerate_column``);
5. **simulability**: λ ≥ 0 under every history, before and after
   calibration (``not_simulable``; identity link only);
6. **calibration and stationarity** (``calibration/pilot<k>``; ``branching``,
   ``negative_intensity``, ``explosion``, ``no_convergence``, ``drift``,
   ``dispersion``);
7. **identifiability**: a training log and an independent held-out log at the
   experiment horizon (``train``, ``held_out``; ``explosion`` if either hits
   the cap), then the truth's structure and every library member fitted and
   scored (``uncertified``, ``truth_not_finite``, ``identifiability``,
   ``fit_failed``).

**Stratified sampling.** :func:`sample_split` needs ``n_ood =
round(share · n)`` (halves round up) out-of-dictionary truths and ``n - n_ood``
in-dictionary ones. Each kind has its own candidate sequence 0, 1, 2, …; the
accepted truths of a kind are the first ``n_kind`` accepted candidates in index
order, so the share holds exactly. Candidates may be evaluated in parallel
(``workers``) in batches, but acceptance reads results in index order, so the
split does not depend on the worker count or on timing. Records interleave
the kinds (in, ood, in, ood, …, then the remainder), so a prefix keeps the
share, and are numbered ``f"{split}-{j:03d}"``.

**Strata** by nearest-member distance d: ``strata`` is a tuple of ``(label,
upper bound)``; a truth is in the first stratum with ``d ≤ upper``. Lower than
ε never occurs (step 2).

**Outputs.** :class:`SplitResult` holds the records, a manifest (deterministic:
the records' SHA-256, the configuration digest, counts by kind, stratum and
link, and rejection counts over the candidates up to each kind's last accepted
one) and timing (wall clock; never hashed).

Wall time is never a rejection criterion, which would make the population
depend on the machine. Work is bounded deterministically instead: by the
pilots' event caps and draw budgets (``calibrate.bounded_simulate``) and by
``max_candidates`` per kind.
"""

from __future__ import annotations

import _thread
import hashlib
import math
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from functools import partial
from typing import Final

from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import structure_hash
from sciagent.glm.data import Dataset
from sciagent.glm.distance import D_MAX, structure_distance
from sciagent.glm.fit import FIT_VERSION, FitConfig, FitError
from sciagent.glm.fit_bounds import simulable
from sciagent.glm.grammar import ChannelSpec, Structure, depth
from sciagent.glm.grids import PSI_GRIDS
from sciagent.glm.simulate import Coefficients, MarkSampler, PsiAssignment
from sciagent.glm.syntax import render
from sciagent.library.base import LibraryError
from sciagent.scenarios.calibrate import (
    CalibrationSettings,
    CandidateRejectedError,
    MarkMean,
    ThetaPrior,
    bounded_simulate,
    calibrate,
    column_scales,
    sample_theta,
)
from sciagent.scenarios.identify import GrammarScorer, LibraryScorer, check_identifiable
from sciagent.scenarios.prior import (
    StructurePrior,
    is_out_of_dictionary,
    sample_psi,
    sample_structure,
    truth_grid,
)
from sciagent.scenarios.records import (
    CalibrationRecord,
    IdentifiabilityRecord,
    Json,
    TruthRecord,
    canonical_json,
    records_digest,
)
from sciagent.scenarios.streams import stream

#: Bump on any change that alters which truths a configuration produces.
SAMPLER_VERSION: Final = "sciagent.scenarios.sampler/1"
MANIFEST_SCHEMA: Final = "sciagent.scenarios.split/1"


class SamplerConfigError(SciAgentError):
    """A sampler configuration is invalid."""


class SamplerExhaustedError(SciAgentError):
    """``max_candidates`` were evaluated without filling a split."""


class CandidateTimeoutError(SciAgentError):
    """A candidate ran past the wall-clock cap. The run is aborted: a candidate
    is never *rejected* on wall time, which would make the split depend on the
    machine."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SamplerConfig:
    """Every declared number of a truth population (see the environment's file).

    ``identifiability_horizon`` is the training and held-out log length (the
    experiment budget, SPEC §3); ``delta`` the identifiability margin in nats
    per held-out event; ``epsilon`` the non-membership distance;
    ``strata`` ordered ``(label, inclusive upper bound)`` pairs, the last
    reaching ``D_MAX``; ``max_candidates`` the cap per dictionary kind.
    """

    structure_prior: StructurePrior
    truth_psi_grids: tuple[tuple[str, tuple[float, ...]], ...]
    theta_prior: ThetaPrior
    calibration: CalibrationSettings
    identifiability_horizon: float
    delta: float
    epsilon: float
    strata: tuple[tuple[str, float], ...]
    out_of_dictionary_share: float
    fit_config: FitConfig
    max_candidates: int

    def __post_init__(self) -> None:
        for name, _ in self.truth_psi_grids:
            truth_grid(name, dict(self.truth_psi_grids))
        if not self.identifiability_horizon > 0.0:
            raise SamplerConfigError("identifiability_horizon must be positive")
        if not (math.isfinite(self.delta) and self.delta > 0.0):
            raise SamplerConfigError("delta must be positive")
        if not self.strata or self.strata[-1][1] < D_MAX:
            raise SamplerConfigError(f"the last stratum must reach {D_MAX}")
        bounds = [b for _, b in self.strata]
        if bounds != sorted(set(bounds)) or len({s for s, _ in self.strata}) != len(
            self.strata
        ):
            raise SamplerConfigError("strata bounds must strictly increase")
        if not 0.0 <= self.epsilon < bounds[0]:
            raise SamplerConfigError("epsilon must lie in [0, first stratum bound)")
        if not 0.0 <= self.out_of_dictionary_share <= 1.0:
            raise SamplerConfigError("out_of_dictionary_share must lie in [0, 1]")
        if self.max_candidates < 1:
            raise SamplerConfigError("max_candidates must be ≥ 1")
        if self.fit_config.workers != 1:
            raise SamplerConfigError(
                "fit_config.workers must be 1: candidates are parallelised instead"
            )


@dataclass(frozen=True)
class SamplerEnvironment:
    """What the sampler needs from an environment. Must be picklable.

    ``name`` identifies the environment's code (channels, mark law, library)
    in :func:`config_digest`; change it when any of them changes.
    """

    name: str
    channels: tuple[ChannelSpec, ...]
    mark_sampler: MarkSampler
    mark_mean: MarkMean
    library: tuple[LibraryScorer, ...]


def _describe(obj: object) -> Json:
    """A JSON description of a configuration value, for hashing."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, FitConfig):
        return obj.key()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _describe(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, tuple | list):
        return [_describe(x) for x in obj]
    if isinstance(obj, float):
        return obj.hex()
    if isinstance(obj, bool | int | str) or obj is None:
        return obj
    raise SamplerConfigError(f"cannot describe {type(obj).__name__} for hashing")


def config_digest(config: SamplerConfig, env: SamplerEnvironment) -> str:
    """SHA-256 over the sampler version, the fit version, the fitting grids, the
    configuration, the environment name, its channels and library names."""
    description = {
        "sampler": SAMPLER_VERSION,
        "fit": FIT_VERSION,
        "psi_grids": {k: [v.hex() for v in PSI_GRIDS[k]] for k in sorted(PSI_GRIDS)},
        "config": _describe(config),
        "environment": env.name,
        "channels": _describe(env.channels),
        "library": [
            [m.name, None if m.structure is None else structure_hash(m.structure)]
            for m in env.library
        ],
    }
    return hashlib.sha256(canonical_json(description).encode("utf-8")).hexdigest()


def stratum_of(distance: float, strata: Sequence[tuple[str, float]]) -> str:
    """The first stratum whose inclusive upper bound is ≥ ``distance``."""
    for label, upper in strata:
        if distance <= upper:
            return label
    raise SamplerConfigError(f"distance {distance} beyond the last stratum")


# --------------------------------------------------------------------------
# One candidate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateOutcome:
    """A candidate's result: a record (accepted) or a reason (rejected)."""

    out_of_dictionary: bool
    index: int
    record: TruthRecord | None
    reason: str
    detail: str
    wall_time: float = field(compare=False)


def candidate_prefix(split: str, out_of_dictionary: bool, index: int) -> str:
    return f"{split}/{'ood' if out_of_dictionary else 'ind'}/{index}"


def _psi_rows(
    psi: PsiAssignment,
) -> tuple[tuple[tuple[tuple[int, ...], str, float], ...], ...]:
    return tuple(
        tuple((slot.path, slot.name, float(v)) for slot, v in assignment.items())
        for assignment in psi
    )


def _same_psi(a: PsiAssignment, b: PsiAssignment) -> bool:
    return len(a) == len(b) and all(
        dict(x) == dict(y) for x, y in zip(a, b, strict=True)
    )


def flat_theta(coef: Coefficients) -> tuple[float, ...]:
    """θ in design order: the intercept, then each feature's columns."""
    return (coef.intercept, *(t for f in coef.per_feature for t in f))


def _require_simulable(
    structure: Structure, coef: Coefficients, channels: tuple[ChannelSpec, ...]
) -> None:
    """Truths are simulable by construction: λ ≥ 0 under every history.

    :func:`~sciagent.glm.fit_bounds.simulable` is a history-free bound, so a
    truth that passes it can never raise ``NegativeIntensityError`` in any
    experiment. Under the identity link the knob scales θ₀ and the exogenous
    features together and leaves ``Excite`` coefficients alone, so the bound's
    sign is the same before and after calibration; it is checked at both.
    """
    if not simulable(structure, flat_theta(coef), channels):
        raise CandidateRejectedError(
            "not_simulable", "λ can go negative on some history (identity link)"
        )


def _evaluate(
    config: SamplerConfig,
    env: SamplerEnvironment,
    split: str,
    seed: int,
    out_of_dictionary: bool,
    index: int,
) -> TruthRecord:
    prefix = candidate_prefix(split, out_of_dictionary, index)

    channels = env.channels
    structure: Structure = sample_structure(
        config.structure_prior,
        channels,
        stream(seed, f"{prefix}/structure"),
        out_of_dictionary=out_of_dictionary,
    )
    distances = tuple(
        (m.name, structure_distance(structure, m.structure))
        for m in env.library
        if m.structure is not None
    )
    nearest_name, nearest = (
        min(distances, key=lambda nd: nd[1]) if distances else ("", D_MAX)
    )
    if not nearest > config.epsilon:
        raise CandidateRejectedError(
            "library_near", f"distance {nearest:.4g} to {nearest_name} ≤ ε"
        )
    psi = sample_psi(
        structure, dict(config.truth_psi_grids), stream(seed, f"{prefix}/psi")
    )
    scales = column_scales(
        structure,
        psi,
        channels,
        env.mark_sampler,
        stream(seed, f"{prefix}/reference"),
        config.theta_prior.reference_horizon,
        burn_in=config.calibration.burn_in,
    )
    coef0 = sample_theta(
        structure, scales, config.theta_prior, stream(seed, f"{prefix}/theta")
    )
    _require_simulable(structure, coef0, channels)
    cal = calibrate(
        structure,
        psi,
        coef0,
        channels,
        env.mark_sampler,
        config.calibration,
        lambda stage: stream(seed, f"{prefix}/calibration/{stage}"),
        env.mark_mean,
    )
    _require_simulable(structure, cal.coef, channels)
    horizon = config.identifiability_horizon
    logs = []
    for purpose in ("train", "held_out"):
        log = bounded_simulate(
            structure,
            psi,
            cal.coef,
            channels,
            env.mark_sampler,
            horizon,
            stream(seed, f"{prefix}/{purpose}"),
            config.calibration,
        )
        if log is None:
            raise CandidateRejectedError("explosion", f"the {purpose} log hit the cap")
        logs.append(log)
    train, held_out = logs
    try:
        ident = check_identifiable(
            GrammarScorer("truth", structure, channels, config.fit_config),
            env.library,
            [Dataset.observational(train, "train")],
            [Dataset.observational(held_out, "held_out")],
            delta=config.delta,
        )
    except (FitError, LibraryError) as exc:
        raise CandidateRejectedError("fit_failed", str(exc)) from exc
    point = cal.point
    return TruthRecord(
        id="",
        split=split,
        seed=seed,
        stream=prefix,
        candidate_index=index,
        out_of_dictionary=is_out_of_dictionary(structure),
        dsl=render(structure),
        link=structure.link.value,
        structure_hash=structure_hash(structure),
        depths=tuple(depth(f) for f in structure.features),
        psi=_psi_rows(psi),
        intercept=cal.coef.intercept,
        per_feature=cal.coef.per_feature,
        nearest_member=nearest_name,
        nearest_distance=nearest,
        member_distances=distances,
        stratum=stratum_of(nearest, config.strata),
        calibration=CalibrationRecord(
            knob=cal.knob,
            iterations=cal.iterations,
            horizon=cal.horizon,
            mean_rate=point.mean_rate,
            fano=point.fano,
            cv=point.cv,
            drift=point.drift,
            n_events=point.n_events,
            branching_bound=cal.branching,
        ),
        identifiability=IdentifiabilityRecord(
            horizon=horizon,
            train_events=train.n,
            held_out_events=ident.truth.n_events,
            train_mean_rate=train.n / horizon,
            held_out_mean_rate=held_out.n / horizon,
            truth_log_likelihood=ident.truth.log_likelihood,
            truth_certified=ident.truth.certified,
            truth_psi_recovered=ident.truth.psi is not None
            and _same_psi(ident.truth.psi, psi),
            member_log_likelihoods=tuple(
                (name, s.log_likelihood) for name, s in ident.members
            ),
            margins=ident.margins,
            min_margin=ident.min_margin,
        ),
    )


@contextmanager
def wall_clock_cap(seconds: float | None, label: str) -> Iterator[None]:
    """Abort the block with :class:`CandidateTimeoutError` after ``seconds``.

    Belt and braces behind the deterministic bounds (event caps, draw budgets,
    the simulator's runaway check): a timer interrupts the main thread, and
    the interrupt is turned into a typed error that aborts the whole run. It
    acts only on the main thread (where candidates run, in-process or in a
    pool worker); elsewhere, and for ``seconds`` None, it does nothing.
    """
    if seconds is None or threading.current_thread() is not threading.main_thread():
        yield
        return
    lock = threading.Lock()
    state = {"done": False, "fired": False}

    def fire() -> None:
        with lock:
            if state["done"]:
                return
            state["fired"] = True
        _thread.interrupt_main()

    timer = threading.Timer(seconds, fire)
    timer.daemon = True
    timer.start()
    try:
        yield
    except KeyboardInterrupt:
        if state["fired"]:
            raise CandidateTimeoutError(f"{label} exceeded {seconds} s") from None
        raise
    finally:
        with lock:
            state["done"] = True
        timer.cancel()


def evaluate_candidate(
    config: SamplerConfig,
    env: SamplerEnvironment,
    split: str,
    seed: int,
    out_of_dictionary: bool,
    timeout_s: float | None,
    index: int,
) -> CandidateOutcome:
    """Run one candidate through every constraint (module docstring).

    ``timeout_s`` is the :func:`wall_clock_cap`; exceeding it raises
    :class:`CandidateTimeoutError` rather than rejecting.
    """
    start = time.perf_counter()
    label = candidate_prefix(split, out_of_dictionary, index)
    try:
        with wall_clock_cap(timeout_s, label):
            record = _evaluate(config, env, split, seed, out_of_dictionary, index)
    except CandidateRejectedError as exc:
        return CandidateOutcome(
            out_of_dictionary,
            index,
            None,
            exc.reason,
            exc.detail,
            time.perf_counter() - start,
        )
    return CandidateOutcome(
        out_of_dictionary, index, record, "accepted", "", time.perf_counter() - start
    )


# --------------------------------------------------------------------------
# A split
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitResult:
    """The records, the deterministic manifest, and the (unhashed) timing."""

    records: tuple[TruthRecord, ...]
    manifest: Mapping[str, Json]
    timing: Mapping[str, Json]


@contextmanager
def _pool(workers: int) -> Iterator[ProcessPoolExecutor | None]:
    if workers <= 1:
        yield None
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield pool


def _fill(
    config: SamplerConfig,
    env: SamplerEnvironment,
    split: str,
    seed: int,
    out_of_dictionary: bool,
    need: int,
    pool: ProcessPoolExecutor | None,
    batch: int,
    timeout_s: float | None,
) -> list[CandidateOutcome]:
    """Outcomes in index order up to (and including) the ``need``-th acceptance."""
    run = partial(
        evaluate_candidate, config, env, split, seed, out_of_dictionary, timeout_s
    )
    outcomes: list[CandidateOutcome] = []
    accepted = 0
    index = 0
    while accepted < need:
        if index >= config.max_candidates:
            raise SamplerExhaustedError(
                f"{split}: {accepted}/{need} "
                f"{'out-of' if out_of_dictionary else 'in'}-dictionary truths after "
                f"{config.max_candidates} candidates"
            )
        indices = range(index, min(index + batch, config.max_candidates))
        results = list(pool.map(run, indices)) if pool else [run(i) for i in indices]
        for outcome in results:
            if accepted == need:
                break
            outcomes.append(outcome)
            accepted += outcome.record is not None
        index = indices.stop
    return outcomes


def _interleave(
    ind: Sequence[TruthRecord], ood: Sequence[TruthRecord]
) -> list[TruthRecord]:
    out: list[TruthRecord] = []
    for j in range(max(len(ind), len(ood))):
        out.extend(r[j] for r in (ind, ood) if j < len(r))
    return out


def _counts(values: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in sorted(values):
        out[v] = out.get(v, 0) + 1
    return out


def sample_split(
    config: SamplerConfig,
    env: SamplerEnvironment,
    *,
    split: str,
    n: int,
    seed: int,
    workers: int = 1,
    candidate_timeout_s: float | None = None,
) -> SplitResult:
    """Sample ``n`` truths for ``split`` (module docstring).

    ``workers`` and ``candidate_timeout_s`` change wall time only, never the
    result; a candidate past the cap aborts the run (:func:`wall_clock_cap`).
    """
    if n < 1 or seed < 0 or not split or "/" in split:
        raise SamplerConfigError(f"invalid request: split={split!r} n={n} seed={seed}")
    start = time.perf_counter()
    n_ood = math.floor(config.out_of_dictionary_share * n + 0.5)
    # Several candidates per worker per batch keep the pool busy while a slow
    # candidate finishes; acceptance still reads results in index order.
    batch = 1 if workers <= 1 else 3 * workers
    with _pool(workers) as pool:
        by_kind = {
            flag: _fill(
                config, env, split, seed, flag, need, pool, batch, candidate_timeout_s
            )
            for flag, need in ((False, n - n_ood), (True, n_ood))
        }
    accepted = {
        flag: [o.record for o in outs if o.record is not None]
        for flag, outs in by_kind.items()
    }
    ordered = _interleave(accepted[False], accepted[True])
    records = tuple(replace(r, id=f"{split}-{j:03d}") for j, r in enumerate(ordered))
    kinds = {False: "in_dictionary", True: "out_of_dictionary"}
    manifest: dict[str, Json] = {
        "schema": MANIFEST_SCHEMA,
        "sampler_version": SAMPLER_VERSION,
        "split": split,
        "n": n,
        "seed": seed,
        "environment": env.name,
        "config_digest": config_digest(config, env),
        "records_file": "truths.json",
        "records_sha256": records_digest(records),
        "out_of_dictionary": {
            "declared_share": config.out_of_dictionary_share,
            "count": n_ood,
        },
        "strata": {
            "thresholds": [[label, upper] for label, upper in config.strata],
            "counts": _counts([r.stratum for r in records]),
        },
        "links": _counts([r.link for r in records]),
        "candidates": {
            kinds[flag]: {
                "evaluated": len(outs),
                "accepted": len(accepted[flag]),
                "outcomes": _counts([o.reason for o in outs]),
            }
            for flag, outs in by_kind.items()
        },
    }
    outcomes = [o for outs in by_kind.values() for o in outs]
    wall = time.perf_counter() - start
    timing: dict[str, Json] = {
        "workers": workers,
        "wall_time_s": wall,
        "candidate_cpu_s": math.fsum(o.wall_time for o in outcomes),
        "accepted_cpu_s": math.fsum(o.wall_time for o in outcomes if o.record),
        "wall_time_per_accepted_s": wall / max(1, len(records)),
        "per_candidate": [
            [
                candidate_prefix(split, o.out_of_dictionary, o.index),
                o.reason,
                o.wall_time,
            ]
            for o in outcomes
        ],
    }
    return SplitResult(records, manifest, timing)
