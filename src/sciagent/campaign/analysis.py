"""Tables, positive controls and the go/no-go read, from a campaign's store.

**Selection by address.** Rows are read from the cells the *current* plan
addresses (:func:`load_rows`): for every planned unit its key is recomputed
and its cell looked up. A cell left by an older configuration has another
address and is never read, so a report cannot silently mix configurations
(the ledger's own docstring asks the report layer for exactly this). The one
declared exception is ``sessions="stored"`` (P4-PROPOSAL §2.2, rescore): LLM
*sessions* are then found by enumeration (:mod:`~sciagent.campaign.rescore`),
so sessions conducted under an older fitter are read, but their *scores* are
still read at the current address only (the old session's digest plus the
current versions). Each :class:`Session` carries the ``env_version`` it ran
under.

**Void sessions** (P4-PROPOSAL Δ8, :mod:`~sciagent.campaign.voids`). Every
session is classified by the void policy from its stored detail and record
(no cell is edited). A void session's score row is dropped from every table
even if a score cell exists (the pilot scored voids); the session itself stays
in :attr:`Table.sessions` so reports count it (:func:`void_counts`).

**Non-finite scores.** A model that gives a held-out event zero intensity
scores ``-inf`` per event. That is kept as a result: its gap is ``-inf``, its
gap closed undefined (so it never closes), and it is excluded from means (and
counted in ``nonfinite``). In the §6.4 controls it ranks lowest (control 2 and
3, by rank; Δ10); control 1 reads finite gaps only.

A *reference* at ``-inf`` on a (truth, seed) leaves undefined whatever is
measured from it there. B-lib at ``-inf``: every gap closed on that seed is
undefined (None); B-lib's own gap is ``-inf``, which control 1 does not read
(preregistered: a ``-inf`` seed carries no SE), so the truth is checked on its
other seeds. ORACLE at ``-inf``: every gap, gap closed and curve gap on that
seed is undefined (None), since nothing can be referenced to it; and the
ORACLE is then not an upper bound for the truth, so the truth fails control 1
(margin ``-inf``) and is dropped (:func:`verdict`), whatever its other seeds
show.

**Gap closed within noise** (P4-PROPOSAL T4). The denominator
``LL(ORACLE) - LL(B-lib)`` of one (truth, seed) can be tiny, and the ratio is
then noise (audit §1: 37.32 on a denominator of 0.0029). The rule, in
:func:`derive`: for a truth with at least 2 seeds on which both ORACLE and
B-lib have finite scores, let ``d_s = LL(ORACLE)_s - LL(B-lib)_s`` and
``noise = sd(d, ddof=1) / sqrt(n)``, the paired standard error control 1 uses
for the same difference. A row's gap closed on seed ``s`` is **flagged**
(:attr:`Row.closed_flagged`) when ``|d_s| <= noise``: at control 1's own
resolution the denominator cannot be told from 0. With fewer than 2 such seeds
the noise is not estimable and nothing is flagged. A flagged ratio is kept
(the §7.1 shares read it as preregistered) but is never printed as a ratio,
never enters a median gap closed, and never chooses a transcript.

**Derived scores** (SPEC §4.3), per (truth, seed), from the stored raw
held-out values: ``gap = per_event - per_event[ORACLE]`` and
``gap closed = (per_event - per_event[B-lib]) / (per_event[ORACLE] -
per_event[B-lib])`` (:func:`~sciagent.scoring.heldout.gap_closed`, None when
ORACLE does not beat B-lib). Efficiency curves become gaps the same way.

**Shares** are over truths, each truth weighted equally (its value is the
fraction of its seeds with the property), as in
:mod:`sciagent.scoring.gonogo`.

**The verdict** (:func:`verdict`) is SPEC §7.1 with the definitions,
thresholds and precedence of :func:`sciagent.scoring.gonogo.go_no_go`, built
from that module's public pieces (:func:`~sciagent.scoring.gonogo.oracle_beats_lib`,
:func:`~sciagent.scoring.gonogo.planted_beats_rand`,
:func:`~sciagent.scoring.gonogo.bsym_approaches_oracle`, ``recovers``,
``closes``). ``go_no_go`` itself requires the B-sym@10F control on every
(truth, seed); the pilot runs it on one seed only (a declared cost saving),
so the control reads the per-truth mean over the seeds it has. Two further
departures, both stated in the report: a *failed* unit (a system that raised
on its unit) counts as neither recovering nor closing, and has no gap; and
the "too hard" clause reads every non-LLM system (the LLM arms do not cover
every truth and seed in the pilot) -- their close shares are reported beside
it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal

import numpy as np

from sciagent.campaign.plan import (
    B_LIB,
    B_RAND,
    B_SPARSE,
    B_SYM_10F,
    BASELINES,
    ORACLE,
    PLANTED,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    baseline_key,
    baseline_units,
    llm_units,
    score_key,
    session_key,
)
from sciagent.campaign.rescore import stored_sessions
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus, decoded, session_void
from sciagent.core import reductions
from sciagent.scoring import gonogo
from sciagent.scoring.heldout import gap_closed
from sciagent.scoring.stats import DEFAULT_RESAMPLES

type Json = Any

#: Fit budgets at which the efficiency table reads the best-so-far gap.
EFFICIENCY_POINTS: Final = (1, 5, 10, 20, 40)
#: Extra points for the 10xF control.
CONTROL_POINTS: Final = (100, 200, 400)


@dataclass(frozen=True)
class Row:
    """One scored unit. ``ok`` is False for a failed unit (no scores)."""

    truth: str
    seed: int
    system: str
    llm: bool
    ok: bool
    submitted: bool
    per_event: float | None
    exact: bool
    distance: float | None
    has_structure: bool
    similarity: float | None
    fits_used: int | None
    structure: str | None
    curve: tuple[tuple[int, float], ...]
    error: str | None
    gap: float | None = None
    gap_closed: float | None = None
    curve_gaps: tuple[tuple[int, float], ...] = ()
    #: ORACLE rows: the fit/5 ``oracle_underfit`` flag (None: not recorded).
    oracle_underfit: bool | None = None
    #: ORACLE rows: where the chosen ψ came from, ``"search"`` or ``"truth"``.
    oracle_psi_source: str | None = None
    #: The gap closed's denominator is within its paired noise (docstring).
    closed_flagged: bool = False


@dataclass(frozen=True)
class Session:
    """One stored LLM session (scored or not), under the void policy."""

    truth: str
    seed: int
    system: str
    reading: Mapping[str, float]
    detail: Mapping[str, Json]
    void: VoidStatus
    env_version: str
    #: The session cell's address: its ``timing.jsonl`` line's key.
    digest: str


@dataclass(frozen=True)
class Table:
    """Everything the report reads."""

    truths: Mapping[str, TruthEntry]
    rows: tuple[Row, ...]
    sessions: tuple[Session, ...]
    planned: Mapping[str, int]
    missing: Mapping[str, int] = field(default_factory=dict)


def _f(reading: Mapping[str, str], name: str) -> float | None:
    v = reading.get(name)
    return None if v is None else float.fromhex(v)


_PSI_SOURCES: Final = ("search", "truth")


def _oracle_fields(
    system: str, detail: Mapping[str, Json]
) -> tuple[bool | None, str | None]:
    """The ORACLE's underfit flag and ψ source; None where a cell predates them."""
    if system != ORACLE:
        return None, None
    flag = detail.get("oracle_underfit")
    source = detail.get("oracle_psi_source")
    if flag is not None and not isinstance(flag, bool):
        raise CampaignError(f"oracle_underfit must be a bool, got {flag!r}")
    if source is not None and source not in _PSI_SOURCES:
        raise CampaignError(f"oracle_psi_source must be one of {_PSI_SOURCES}")
    return flag, source


def row_from_cell(
    unit_truth: str, seed: int, system: str, llm: bool, cell: Mapping[str, Json]
) -> Row:
    """One stored score cell as a :class:`Row` (gaps not derived yet)."""
    reading = cell["reading"]
    detail = cell["detail"]["result"]
    underfit, psi_source = _oracle_fields(system, detail)
    status = _f(reading, "status")
    ok = status is not None and status >= 1.0
    submitted = status == 1.0
    per_event = _f(reading, "per_event") if submitted else None
    fits = _f(reading, "fits_used")
    return Row(
        truth=unit_truth,
        seed=seed,
        system=system,
        llm=llm,
        ok=ok,
        submitted=submitted,
        per_event=per_event,
        exact=_f(reading, "exact") == 1.0,
        distance=_f(reading, "distance"),
        has_structure=_f(reading, "has_structure") == 1.0,
        similarity=_f(reading, "similarity"),
        fits_used=None if fits is None else int(fits),
        structure=detail.get("structure"),
        curve=tuple((int(k), float(v)) for k, _, v in detail.get("curve", [])),
        error=None if ok else str(detail.get("error")),
        oracle_underfit=underfit,
        oracle_psi_source=psi_source,
    )


def load_rows(
    store: ResultStore,
    env: CampaignEnvironment,
    config: CampaignConfig,
    truths: Sequence[TruthEntry],
    *,
    sessions: Literal["current", "stored"] = "current",
) -> Table:
    """Every planned unit's cell, by address (module docstring).

    ``sessions="stored"`` finds LLM sessions by enumeration (rescore); their
    scores are still read at the current address only.
    """
    by_id = {t.id: t for t in truths}
    order = [t.id for t in truths]
    found = stored_sessions(store, env, config, truths) if sessions == "stored" else {}
    rows: list[Row] = []
    found_sessions: list[Session] = []
    planned: dict[str, int] = {}
    missing: dict[str, int] = {}
    for u in baseline_units(order, config):
        planned[u.system] = planned.get(u.system, 0) + 1
        key = baseline_key(u, by_id[u.truth_id], env, config)
        if not store.done(key):
            missing[u.system] = missing.get(u.system, 0) + 1
            continue
        rows.append(
            row_from_cell(u.truth_id, u.seed, u.system, False, store.cell(key.digest))
        )
    for u in llm_units(order, config):
        planned[u.system] = planned.get(u.system, 0) + 1
        if sessions == "stored":
            hit = found.get(u)
        else:
            skey = session_key(u, by_id[u.truth_id], env, config)
            hit = (skey, store.cell(skey.digest)) if store.done(skey) else None
        if hit is None:
            missing[u.system] = missing.get(u.system, 0) + 1
            continue
        skey, cell = hit
        reading = decoded(cell["reading"])
        detail = cell["detail"]["session"]
        void = session_void(store.root, reading, detail)
        found_sessions.append(
            Session(
                u.truth_id,
                u.seed,
                u.system,
                reading,
                detail,
                void,
                str(skey.env_version),
                str(skey.digest),
            )
        )
        ckey = score_key(u, skey, env, config)
        if store.done(ckey) and not void.void:
            rows.append(
                row_from_cell(
                    u.truth_id, u.seed, u.system, True, store.cell(ckey.digest)
                )
            )
    return Table(by_id, tuple(derive(rows)), tuple(found_sessions), planned, missing)


def void_counts(table: Table) -> dict[str, int]:
    """Void sessions per system (their scores are dropped; module docstring)."""
    out: dict[str, int] = {}
    for x in table.sessions:
        if x.void.void:
            out[x.system] = out.get(x.system, 0) + 1
    return dict(sorted(out.items()))


def _denominator_noise(
    ref: Mapping[tuple[str, int, str], float],
) -> tuple[dict[tuple[str, int], float], dict[str, float]]:
    """Per (truth, seed) ``LL(ORACLE) - LL(B-lib)`` and per truth its paired SE."""
    d: dict[tuple[str, int], float] = {}
    for (t, seed, system), oracle in ref.items():
        lib = ref.get((t, seed, B_LIB))
        if (
            system == ORACLE
            and lib is not None
            and math.isfinite(oracle)
            and math.isfinite(lib)
        ):
            d[(t, seed)] = oracle - lib
    by_truth: dict[str, list[float]] = {}
    for (t, _), v in sorted(d.items()):
        by_truth.setdefault(t, []).append(v)
    noise = {
        t: reductions.deviation(np.asarray(v, dtype=np.float64)) / math.sqrt(len(v))
        for t, v in by_truth.items()
        if len(v) >= 2
    }
    return d, noise


def derive(rows: Sequence[Row]) -> list[Row]:
    """Gaps, gaps closed (flagged within noise) and curve gaps against the
    same (truth, seed)'s references (module docstring)."""
    ref: dict[tuple[str, int, str], float] = {}
    for r in rows:
        if r.system in (ORACLE, B_LIB) and r.per_event is not None:
            ref[(r.truth, r.seed, r.system)] = r.per_event
    denominator, noise = _denominator_noise(ref)
    out: list[Row] = []
    for r in rows:
        oracle = ref.get((r.truth, r.seed, ORACLE))
        lib = ref.get((r.truth, r.seed, B_LIB))
        gap = None
        closed = None
        curve: tuple[tuple[int, float], ...] = ()
        # A -inf reference leaves what is measured from it undefined (docstring).
        if oracle is not None and math.isfinite(oracle):
            if r.per_event is not None:
                gap = r.per_event - oracle
                if (
                    lib is not None
                    and math.isfinite(lib)
                    and math.isfinite(r.per_event)
                ):
                    closed = gap_closed(r.per_event, lib, oracle)
            curve = tuple((k, v - oracle) for k, v in r.curve)
        d = denominator.get((r.truth, r.seed))
        tol = noise.get(r.truth)
        flagged = (
            closed is not None and d is not None and tol is not None and abs(d) <= tol
        )
        out.append(
            replace(
                r,
                gap=gap,
                gap_closed=closed,
                curve_gaps=curve,
                closed_flagged=flagged,
            )
        )
    return out


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return reductions.mean(np.asarray(values, dtype=np.float64))


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def truth_share(rows: Sequence[Row], predicate: str) -> tuple[float | None, int]:
    """Truth-weighted share of rows with ``predicate``; and the truths counted.

    ``predicate`` is ``"recovers"`` (exact canonical match) or ``"closes"``
    (gap closed ≥ :data:`~sciagent.scoring.gonogo.CLOSE_FRACTION`). Failed
    units count as neither.
    """
    per_truth: dict[str, list[bool]] = {}
    for r in rows:
        if predicate == "recovers":
            hit = r.ok and r.exact
        else:
            hit = (
                r.ok
                and r.gap_closed is not None
                and r.gap_closed >= gonogo.CLOSE_FRACTION
            )
        per_truth.setdefault(r.truth, []).append(hit)
    if not per_truth:
        return None, 0
    shares = [sum(v) / len(v) for _, v in sorted(per_truth.items())]
    return _mean(shares), len(shares)


@dataclass(frozen=True)
class SystemSummary:
    system: str
    units: int
    failed: int
    nonfinite: int
    no_submission: int
    truths: int
    recovery: float | None
    close: float | None
    gap_mean: float | None
    gap_median: float | None
    closed_median: float | None
    similarity_mean: float | None
    distance_mean: float | None
    #: Units whose gap closed is flagged within noise (left out of the median).
    closed_flagged: int = 0


def summarise(rows: Sequence[Row], system: str) -> SystemSummary:
    mine = [r for r in rows if r.system == system]
    gaps = [r.gap for r in mine if r.gap is not None and math.isfinite(r.gap)]
    closed = [
        r.gap_closed for r in mine if r.gap_closed is not None and not r.closed_flagged
    ]
    sims = [r.similarity for r in mine if r.ok and r.similarity is not None]
    dists = [
        r.distance for r in mine if r.ok and r.has_structure and r.distance is not None
    ]
    recovery, n = truth_share(mine, "recovers")
    close, _ = truth_share(mine, "closes")
    return SystemSummary(
        system=system,
        units=len(mine),
        failed=sum(not r.ok for r in mine),
        nonfinite=sum(r.gap is not None and not math.isfinite(r.gap) for r in mine),
        no_submission=sum(r.ok and not r.submitted for r in mine),
        truths=n,
        recovery=recovery,
        close=close,
        gap_mean=_mean(gaps),
        gap_median=_median(gaps),
        closed_median=_median(closed),
        similarity_mean=_mean(sims),
        distance_mean=_mean(dists),
        closed_flagged=sum(r.closed_flagged for r in mine),
    )


def gap_at(curve: Sequence[tuple[int, float]], fits: int) -> float | None:
    """Best-so-far gap within ``fits`` fits (None before the first point)."""
    value: float | None = None
    for k, v in curve:
        if k > fits:
            break
        value = v
    return value


def efficiency(
    rows: Sequence[Row], system: str, points: Sequence[int]
) -> list[tuple[int, float | None, int]]:
    """Median best-so-far gap at each budget, over units with a value there."""
    mine = [r for r in rows if r.system == system and r.ok and r.curve_gaps]
    out: list[tuple[int, float | None, int]] = []
    for p in points:
        values = [v for r in mine if (v := gap_at(r.curve_gaps, p)) is not None]
        out.append((p, _median(values), len(values)))
    return out


# --------------------------------------------------------------------------
# Controls and verdict
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    outcome: str
    controls_pass: bool
    too_easy: bool
    too_hard: bool
    kept: tuple[str, ...]
    dropped: tuple[str, ...]
    oracle_checks: tuple[gonogo.OracleCheck, ...]
    planted: gonogo.PlantedCheck | None
    bsym: gonogo.BsymCheck | None
    recovery: Mapping[str, float | None]
    close: Mapping[str, float | None]
    sparse_ood_close: float | None
    reasons: tuple[str, ...]
    inconclusive: tuple[str, ...]
    underfit: gonogo.UnderfitCheck | None = None


def score_rows(
    rows: Sequence[Row], truths: Mapping[str, TruthEntry]
) -> list[gonogo.ScoreRow]:
    """``gonogo.ScoreRow`` for every non-LLM unit with a finite or ``-inf`` gap."""
    return [
        gonogo.ScoreRow(
            truth=r.truth,
            seed=r.seed,
            system=r.system,
            gap=r.gap,
            gap_closed=r.gap_closed,
            exact=r.exact,
            in_dictionary=truths[r.truth].in_dictionary,
            oracle_underfit=r.oracle_underfit,
        )
        for r in rows
        if not r.llm
        and r.gap is not None
        and (math.isfinite(r.gap) or r.gap == -math.inf)
    ]


def underfit_check(rows: Sequence[Row]) -> gonogo.UnderfitCheck:
    """Control 1's underfit half over every ORACLE unit of ``rows``.

    A failed unit has no fit, hence no flag: it counts as unrecorded (a
    missing flag is not "zero flags"), never as skipped.
    """
    return gonogo.oracle_underfit_check(
        [
            (r.truth, r.seed, r.oracle_underfit if r.ok else None)
            for r in rows
            if r.system == ORACLE and not r.llm
        ]
    )


def oracle_not_finite(rows: Sequence[Row]) -> list[str]:
    """Truths on which a non-LLM ORACLE unit scored a non-finite held-out
    log-likelihood (module docstring): sorted."""
    return sorted(
        {
            r.truth
            for r in rows
            if r.system == ORACLE
            and not r.llm
            and r.per_event is not None
            and not math.isfinite(r.per_event)
        }
    )


def verdict(
    table: Table, rng: np.random.Generator, *, resamples: int = DEFAULT_RESAMPLES
) -> Verdict:
    """SPEC §7.1 over the non-LLM systems (module docstring)."""
    rows = [r for r in table.rows if not r.llm]
    srows = score_rows(rows, table.truths)
    underfit = underfit_check(rows)
    reasons: list[str] = []
    inconclusive: list[str] = []
    seeds_of: dict[tuple[str, str], set[int]] = {}
    for r in srows:
        if math.isfinite(r.gap):  # control 1 reads finite gaps only
            seeds_of.setdefault((r.truth, r.system), set()).add(r.seed)
    truths_with_pairs = [
        t
        for t in sorted(table.truths)
        if len(seeds_of.get((t, ORACLE), set()) & seeds_of.get((t, B_LIB), set())) >= 2
    ]
    pair_rows = [r for r in srows if r.truth in truths_with_pairs]
    checks = gonogo.oracle_beats_lib(pair_rows) if pair_rows else ()
    # An ORACLE at -inf on any seed is not an upper bound: margin -inf, SE
    # undefined, the truth fails control 1 (module docstring).
    no_bound = [t for t in oracle_not_finite(rows) if t in table.truths]
    if no_bound:
        checks = tuple(
            sorted(
                [c for c in checks if c.truth not in no_bound]
                + [gonogo.OracleCheck(t, -math.inf, math.nan, False) for t in no_bound],
                key=lambda c: c.truth,
            )
        )
        reasons.append(
            f"ORACLE scores -inf per event on held-out data (not an upper "
            f"bound) for {no_bound}"
        )
    kept = tuple(c.truth for c in checks if c.passed)
    dropped = tuple(c.truth for c in checks if not c.passed)
    checked = {c.truth for c in checks}
    unchecked = [t for t in sorted(table.truths) if t not in checked]
    if unchecked:
        inconclusive.append(
            f"ORACLE vs B-lib not checkable (< 2 paired seeds) for {unchecked}"
        )
    if dropped:
        reasons.append(f"dropped as not identifiable (ORACLE ≤ B-lib): {list(dropped)}")
    reasons += gonogo.underfit_reason(underfit)
    if not checks:
        return Verdict(
            "incomplete",
            False,
            False,
            False,
            (),
            (),
            (),
            None,
            None,
            {},
            {},
            None,
            ("no truth has ORACLE and B-lib on 2 seeds yet",),
            tuple(inconclusive),
            underfit,
        )
    if not kept:
        reasons.append("no truth kept: ORACLE never shown to beat B-lib")
        return Verdict(
            "controls fail",
            False,
            False,
            False,
            kept,
            dropped,
            checks,
            None,
            None,
            {},
            {},
            None,
            tuple(reasons),
            tuple(inconclusive),
            underfit,
        )

    def covered(system: str) -> list[str]:
        have = {r.truth for r in srows if r.system == system}
        return [t for t in kept if t in have]

    planted: gonogo.PlantedCheck | None = None
    both = [t for t in covered(PLANTED) if t in covered(B_RAND)]
    if len(both) >= 2:
        planted = gonogo.planted_beats_rand(srows, both, rng=rng, resamples=resamples)
        if not planted.passed:
            reasons.append(gonogo.planted_reason(planted))
    else:
        inconclusive.append("planted-hint vs B-rand: fewer than 2 truths with both")
    bsym: gonogo.BsymCheck | None = None
    c10 = covered(B_SYM_10F)
    if c10:
        bsym = gonogo.bsym_approaches_oracle(srows, c10)
        if not bsym.passed:
            reasons.append(
                f"B-sym at 10xF median gap {bsym.median_gap:.4g} < "
                f"-{gonogo.BSYM_TOLERANCE}"
            )
    else:
        inconclusive.append("B-sym@10F: no kept truth has a result")
    controls = (
        planted is not None
        and planted.passed
        and bsym is not None
        and bsym.passed
        and underfit.passed
    )

    kept_rows = [r for r in rows if r.truth in kept]
    systems = [s for s in BASELINES if s != ORACLE]
    recovery = {
        s: truth_share([r for r in kept_rows if r.system == s], "recovers")[0]
        for s in systems
    }
    close = {
        s: truth_share([r for r in kept_rows if r.system == s], "closes")[0]
        for s in systems
    }
    ood = [t for t in kept if not table.truths[t].in_dictionary]
    sparse_ood = truth_share(
        [r for r in kept_rows if r.system == B_SPARSE and r.truth in ood], "closes"
    )[0]
    too_easy = False
    for s in (B_LIB, B_RAND):
        share = recovery.get(s)
        if share is not None and share >= gonogo.TOO_EASY_SHARE:
            too_easy = True
            reasons.append(f"{s} recovers {share:.0%} of truths")
    if sparse_ood is not None and sparse_ood >= gonogo.TOO_EASY_SHARE:
        too_easy = True
        reasons.append(
            f"B-sparse closes the gap on {sparse_ood:.0%} of out-of-dictionary truths"
        )
    # Too hard (P4-PROPOSAL §2.4, literally): every non-LLM system closes on
    # < 10% of truths. The ORACLE is the reference the gap is measured from
    # (it closes by definition), so "every" is every other non-LLM system.
    present = [s for s in systems if close.get(s) is not None]
    absent = [s for s in systems if s not in present]
    if absent:
        inconclusive.append(f"too hard read without {absent} (no kept rows)")
    too_hard = bool(present) and all(
        (close[s] or 0.0) < gonogo.TOO_HARD_SHARE for s in present
    )
    if too_hard:
        reasons.append(
            f"no non-LLM system closes the gap on ≥ {gonogo.TOO_HARD_SHARE:.0%} "
            f"of truths"
        )
    if not controls:
        outcome = "controls fail"
    elif too_easy:
        outcome = "too easy"
    elif too_hard:
        outcome = "too hard"
    else:
        outcome = "go"
    return Verdict(
        outcome,
        controls,
        too_easy,
        too_hard,
        kept,
        dropped,
        checks,
        planted,
        bsym,
        recovery,
        close,
        sparse_ood,
        tuple(reasons),
        tuple(inconclusive),
        underfit,
    )


def finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)
