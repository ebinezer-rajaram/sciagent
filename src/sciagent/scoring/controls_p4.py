"""Positive controls 4-6 (P4-PROPOSAL §4; P4 Δ11 choices 17-18).

SPEC §6.4's three controls (``scoring/gonogo.py``) show the gap and the
efficiency curve can separate systems. These three gate the P4 contrasts that
rest on other instruments. Each returns a typed verdict, pass or fail plus the
numbers it read, computed by framework code from framework-written records
only: nothing here reads an agent's argument as a number (CLAUDE.md
invariant 2). The controls are preregistered in ``docs/v2/LOG.md``, not in
SPEC (choice 18).

**Control 4, the naming null (gates C2).** A scripted planted-hint agent
(:func:`planted_hint_script`) runs through the tool layer once per naming
condition on the same (truth, seed). The anon view renames channels and
diagnostics and multiplies time by a power of two, so every conversion is
bit-exact and the framework's native numbers must not move:
:func:`naming_null` passes only when the canonical submission and every fit's
``(structure, data, log-likelihood)`` are *exactly* equal (float equality, no
tolerance) between each condition and ``named``. The script proposes the
truth's :func:`~sciagent.systems.v2.search.hint` first, as SPEC §6.4's
planted-hint agent does, fits it and the truth on the observational log, runs
one unintervened experiment (so a design crosses the view's time conversion),
fits both again on ``obs + e0`` and submits the truth (the hint keeps no mark
function, so only the truth's text carries channel names through the
anonymiser). **Δ9 hook:** the rescaled condition (names kept, time x8) is not
built yet. When it is, it joins :data:`NAMING_CONDITIONS` and nothing else
here changes: every condition is compared with :data:`NAMING_REFERENCE`.

**Control 5, policy separation (gates C3/C5).** ``B-sym+truthFSD`` (FSD
against the truth, ``systems/v2/policy_controls.py``) must observe the
telling diagnostic more often than ``B-sym+random``. :func:`policy_separation`
pairs the two arms' ``(truth, seed, observed)`` read-outs
(``campaign.analysis.telling_observed``) by (truth, seed) and applies the
one-sided exact McNemar test (:func:`mcnemar_exact_greater`): with ``b`` the
units only truthFSD observed and ``c`` those only random observed, ``p =
P(Binom(b + c, 1/2) >= b)``, counted with integers. It passes when ``p <``
:data:`CONTROL_5_ALPHA`. Concordant units carry no information about the
difference and do not enter ``p``; ``p >= 2**-(b + c)``, so it cannot pass on
fewer than 5 discordant units. Both telling rates are reported truth-weighted,
as ``telling_rate`` computes them.

**Control 6, the behaviour instrument (gates C6).** A scripted agent with a
known belief timeline (:func:`behaviour_script`, the 10b script of WP3/WP7)
runs through the live loop; :func:`behaviour_control` reads its record with
``investigation.timeline`` and computes every §4.4 measure, which must equal
the values worked out by hand (:func:`expected_behaviour`). The predictions'
outcomes do not depend on the data: one interval, ``[-2, -1]``, cannot hold a
mean rate and the other, ``[0, 1e6]``, always does. Stopping needs held-out
gaps, which a script cannot know in advance, so the control assigns them per
hypothesis (:data:`BEHAVIOUR_GAPS`): it tests the reading of the favoured path,
not the scorer. The script (budget 3 experiments; t is the experiment clock):

====  =====================================================  ======
call  what                                                   t
====  =====================================================  ======
1     run_experiment with no belief: refused, uncharged
2     belief  A, rivals [B], 0.6                             0
3     predict e0 under A: [-2, -1], level 0.9 (missed)
4     predict e0 under B (respelled): [0, 1e6], level 0.8
5     run_experiment e0                                      0
6     run_experiment again: refused (stale belief)
7     belief  B, rivals [A, C], 0.7                          1
8     predict e1 under B: [0, 1e6], level 0.5 (covered)
9     run_experiment e1                                      1
10    belief  A, rivals [B], 0.55                            2
11    run_experiment e2 (no predictions)                     2
12    submit C with no fresh belief: refused
13    belief  C, rivals [A], 0.8                             3
14    submit C, stop_reason "budget"                         3
====  =====================================================  ======

By hand: coverage 2/3 at mean level 11/15 (error -1/15); A n=1 coverage 0
level 0.9, B n=2 coverage 1 level 0.65; falsification-seeking 1 of 3 (e0
refuting, e1 confirmation-only, e2 unpredicted); one falsification (A on e0
at t=0), revised, and a zombie (A favoured again at t=2); favoured path A, B,
A, C; stopping with gaps A -0.1, B -0.05, C 0 converges at 3, burns 0, is not
early and uses the whole budget.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from fractions import Fraction
from math import comb
from typing import Any, Final

import numpy as np

from sciagent.core import reductions
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.grammar import ChannelSpec, Structure, Trend
from sciagent.glm.syntax import render
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation.timeline import timeline
from sciagent.investigation.view import AgentView
from sciagent.scoring.behaviour import (
    StoppingRun,
    calibration,
    falsification_seeking,
    revision,
    stopping,
)
from sciagent.scoring.errors import ScoringError
from sciagent.systems.v2.search import hint

type Json = Any

#: An unintervened experiment's native horizon in both scripts.
SCRIPT_HORIZON: Final = 300.0


def _plain_run(view: AgentView, horizon: float) -> dict[str, Json]:
    return {
        "intervention": {"type": "compose", "parts": []},
        "horizon": view.time(horizon),
    }


def _calls(record: SessionRecord) -> list[Mapping[str, Json]]:
    """Every tool call's body, in the layer's call order."""
    bodies = [e.body for e in record.of_kind("tool_call")]
    return sorted(bodies, key=lambda b: int(b["call"]))


# --------------------------------------------------------------------------
# Control 4: the naming null
# --------------------------------------------------------------------------

#: The condition every other is compared with.
NAMING_REFERENCE: Final = "named"
#: The preregistered conditions. Δ9 adds "rescaled" here (module docstring).
NAMING_CONDITIONS: Final = ("named", "anon")
#: Fits the planted script spends (its session's fit budget).
PLANTED_FITS: Final = 4


def _rival(favoured: Structure, candidates: Sequence[Structure]) -> Structure:
    fav = structure_hash(favoured)
    for c in candidates:
        if structure_hash(c) != fav:
            return c
    raise ScoringError(f"no rival distinct from {render(favoured)}")  # unreachable


def planted_hint_script(
    truth: Structure, view: AgentView, *, horizon: float = SCRIPT_HORIZON
) -> tuple[Step, ...]:
    """The planted-hint agent of control 4, in ``view``'s names and units.

    ``truth`` enters here only, as it does :class:`PlantedHint`'s
    constructor. Needs :data:`PLANTED_FITS` fits and one experiment.
    """
    truth = canonicalise(truth)
    planted = canonicalise(hint(truth))
    spares = (
        Structure((), truth.link),
        Structure((Trend(),), truth.link),
    )
    first_rival = _rival(planted, (truth, *spares))
    second_rival = _rival(truth, (planted, *spares))

    def belief(fav: Structure, rival: Structure) -> Step:
        args = {
            "favoured": view.render(fav),
            "rivals": [view.render(rival)],
            "confidence": 0.5,
        }
        return Step(calls=(("belief", args),))

    def fits(ids: list[str]) -> Step:
        return Step(
            calls=tuple(
                ("fit", {"structure": view.render(s), "data_ids": ids})
                for s in (planted, truth)
            )
        )

    submit = {
        "structure": view.render(truth),
        "report": "planted hint",
        "stop_reason": "confident",
    }
    return (
        belief(planted, first_rival),
        fits(["obs"]),
        Step(calls=(("run_experiment", _plain_run(view, horizon)),)),
        belief(truth, second_rival),
        fits(["obs", "e0"]),
        Step(calls=(("submit", submit),)),
        Step(text="done"),
    )


@dataclass(frozen=True)
class FitFact:
    """One charged fit, as the framework recorded it (native units)."""

    structure_hash: str
    data_ids: tuple[str, ...]
    log_likelihood: float


@dataclass(frozen=True)
class SessionFacts:
    """What control 4 compares: the canonical submission and the fits."""

    submission: str | None
    submission_hash: str | None
    fits: tuple[FitFact, ...]


def session_facts(record: SessionRecord) -> SessionFacts:
    """The framework-written submission and fit records of a session."""
    submission: str | None = None
    submission_hash: str | None = None
    out: list[FitFact] = []
    for body in _calls(record):
        if body["is_error"]:
            continue
        rec = body["record"]
        if body["name"] == "fit":
            out.append(
                FitFact(
                    str(rec["structure_hash"]),
                    tuple(str(i) for i in rec["data_ids"]),
                    float(rec["log_likelihood"]),
                )
            )
        elif body["name"] == "submit":
            submission = str(rec["structure"])
            submission_hash = str(rec["structure_hash"])
    return SessionFacts(submission, submission_hash, tuple(out))


@dataclass(frozen=True)
class NamingNullVerdict:
    """Control 4: ``passed`` when every condition matches the reference."""

    passed: bool
    conditions: tuple[str, ...]
    facts: tuple[tuple[str, SessionFacts], ...]
    mismatches: tuple[str, ...]


def _same(a: float, b: float) -> bool:
    """Exact float equality, ``nan`` included (as a bit pattern)."""
    return a.hex() == b.hex()


def naming_null(records: Mapping[str, SessionRecord]) -> NamingNullVerdict:
    """Control 4 over one planted-hint session per naming condition."""
    if set(records) != set(NAMING_CONDITIONS):
        raise ScoringError(
            f"control 4 needs exactly the conditions {list(NAMING_CONDITIONS)}, "
            f"got {sorted(records)}"
        )
    facts = tuple((c, session_facts(records[c])) for c in NAMING_CONDITIONS)
    by = dict(facts)
    ref = by[NAMING_REFERENCE]
    bad: list[str] = []
    if ref.submission is None:
        bad.append(f"{NAMING_REFERENCE}: no submission")
    if not ref.fits:
        bad.append(f"{NAMING_REFERENCE}: no fit")
    for c, got in facts:
        if c == NAMING_REFERENCE:
            continue
        if (got.submission, got.submission_hash) != (
            ref.submission,
            ref.submission_hash,
        ):
            bad.append(f"submission: {c} {got.submission!r} != {ref.submission!r}")
        if len(got.fits) != len(ref.fits):
            bad.append(f"fit count: {c} {len(got.fits)} != {len(ref.fits)}")
        for i, (g, r) in enumerate(zip(got.fits, ref.fits, strict=False)):
            if (g.structure_hash, g.data_ids) != (r.structure_hash, r.data_ids):
                bad.append(f"fit {i + 1}: {c} fitted a different structure or data")
            elif not _same(g.log_likelihood, r.log_likelihood):
                bad.append(
                    f"fit {i + 1}: {c} log L {g.log_likelihood!r} != "
                    f"{r.log_likelihood!r}"
                )
    return NamingNullVerdict(not bad, NAMING_CONDITIONS, facts, tuple(bad))


# --------------------------------------------------------------------------
# Control 5: policy separation
# --------------------------------------------------------------------------

#: One-sided level of control 5's exact McNemar test (Δ11 choice 17).
CONTROL_5_ALPHA: Final = 0.05


def mcnemar_exact_greater(b: int, c: int) -> float:
    """One-sided exact McNemar p-value: ``P(Binom(b + c, 1/2) >= b)``.

    ``b`` counts the pairs only the first arm succeeded on, ``c`` those only
    the second did. Counted in integers, then rounded once to a float. No
    discordant pair gives 1.
    """
    if b < 0 or c < 0:
        raise ScoringError(f"discordant counts must be >= 0, got b={b}, c={c}")
    n = b + c
    tail = sum(comb(n, i) for i in range(b, n + 1))
    return float(Fraction(tail, 2**n))


@dataclass(frozen=True)
class PolicySeparationVerdict:
    """Control 5 (module docstring). Units are (truth, seed) pairs."""

    passed: bool
    p_value: float
    alpha: float
    units: int
    truths: int
    both: int
    truth_only: int
    random_only: int
    neither: int
    truth_rate: float
    random_rate: float


def _by_unit(
    observed: Sequence[tuple[str, int, bool]], arm: str
) -> dict[tuple[str, int], bool]:
    out: dict[tuple[str, int], bool] = {}
    for truth, seed, hit in observed:
        if (truth, seed) in out:
            raise ScoringError(f"{arm}: unit ({truth!r}, {seed}) appears twice")
        out[(truth, seed)] = bool(hit)
    return out


def _truth_rate(units: Mapping[tuple[str, int], bool]) -> float:
    per: dict[str, list[bool]] = {}
    for (truth, _), hit in sorted(units.items()):
        per.setdefault(truth, []).append(hit)
    shares = [sum(v) / len(v) for _, v in sorted(per.items())]
    return reductions.mean(np.asarray(shares, dtype=np.float64))


def policy_separation(
    truth_fsd: Sequence[tuple[str, int, bool]],
    random: Sequence[tuple[str, int, bool]],
) -> PolicySeparationVerdict:
    """Control 5: truthFSD against random on the telling rate.

    Each input is ``telling_observed``'s ``(truth, seed, observed)`` list for
    one arm. The two must cover the same units: an unpaired unit is refused
    rather than dropped, since which units go missing is not random.
    """
    t = _by_unit(truth_fsd, "truthFSD")
    r = _by_unit(random, "random")
    if not t:
        raise ScoringError("control 5 has no unit to read")
    if set(t) != set(r):
        only = sorted(set(t) ^ set(r))
        raise ScoringError(f"control 5's arms cover different units: {only[:5]}")
    keys = sorted(t)
    both = sum(t[k] and r[k] for k in keys)
    b = sum(t[k] and not r[k] for k in keys)
    c = sum(r[k] and not t[k] for k in keys)
    neither = len(keys) - both - b - c
    p = mcnemar_exact_greater(b, c)
    return PolicySeparationVerdict(
        passed=p < CONTROL_5_ALPHA,
        p_value=p,
        alpha=CONTROL_5_ALPHA,
        units=len(keys),
        truths=len({k[0] for k in keys}),
        both=both,
        truth_only=b,
        random_only=c,
        neither=neither,
        truth_rate=_truth_rate(t),
        random_rate=_truth_rate(r),
    )


# --------------------------------------------------------------------------
# Control 6: the behaviour instrument
# --------------------------------------------------------------------------

#: The experiments budget the behaviour script needs.
BEHAVIOUR_EXPERIMENTS: Final = 3
#: The prediction intervals: one never holds a mean rate, one always does.
MISS: Final = (-2.0, -1.0)
HIT: Final = (0.0, 1e6)
#: Held-out gaps assigned per hypothesis for the stopping measure.
BEHAVIOUR_GAPS: Final = {"a": -0.1, "b": -0.05, "c": 0.0}
#: Relative tolerance on the measures that are means of floats.
FLOAT_TOLERANCE: Final = 1e-12


@dataclass(frozen=True)
class BehaviourHypotheses:
    """The script's three hypotheses: distinct structures, and ``b`` with at
    least two features, so a reordered spelling of it differs from its
    canonical text (the script states it both ways)."""

    a: Structure
    b: Structure
    c: Structure

    def __post_init__(self) -> None:
        hashes = {structure_hash(s) for s in (self.a, self.b, self.c)}
        if len(hashes) != 3:
            raise ScoringError("the behaviour script needs three distinct structures")
        if render(self.respelled_b()) == render(canonicalise(self.b)):
            raise ScoringError(
                f"b = {render(self.b)} has no second spelling; give it two features"
            )

    def respelled_b(self) -> Structure:
        """``b`` with its canonical features in reverse order (not canonical)."""
        canon = canonicalise(self.b)
        return Structure(tuple(reversed(canon.features)), canon.link)


def behaviour_script(
    h: BehaviourHypotheses,
    view: AgentView,
    *,
    diagnostic: str = "mean_rate",
    horizon: float = SCRIPT_HORIZON,
) -> tuple[Step, ...]:
    """The 10b script (module docstring) in ``view``'s names and units."""
    a, b, b2, c = h.a, h.b, h.respelled_b(), h.c
    rate = view.diagnostic(diagnostic)

    def belief(fav: Structure, rivals: list[Structure], conf: float) -> Step:
        args = {
            "favoured": view.render(fav),
            "rivals": [view.render(r) for r in rivals],
            "confidence": conf,
        }
        return Step(calls=(("belief", args),))

    def predict(
        e: int, hyp: Structure, interval: tuple[float, float], level: float
    ) -> tuple[str, dict[str, Json]]:
        return (
            "predict",
            {
                "experiment": e,
                "diagnostic": rate,
                "hypothesis": view.render(hyp),
                "interval": list(interval),
                "level": level,
                "note": "scripted",
            },
        )

    def submit(note: str | None) -> Step:
        args: dict[str, Json] = {
            "structure": view.render(c),
            "report": "r",
            "stop_reason": "budget",
        }
        if note is not None:
            args["note"] = note
        return Step(calls=(("submit", args),))

    run = ("run_experiment", _plain_run(view, horizon))
    return (
        Step(calls=(run,)),  # 1: refused, no belief
        belief(a, [b], 0.6),  # 2
        Step(calls=(predict(0, a, MISS, 0.9), predict(0, b2, HIT, 0.8))),  # 3, 4
        Step(calls=(run,)),  # 5: e0
        Step(calls=(run,)),  # 6: refused, stale
        belief(b2, [a, c], 0.7),  # 7
        Step(calls=(predict(1, b, HIT, 0.5),)),  # 8
        Step(calls=(run,)),  # 9: e1
        belief(a, [b], 0.55),  # 10
        Step(calls=(run,)),  # 11: e2
        submit(None),  # 12: refused, stale
        belief(c, [a], 0.8),  # 13
        submit("three experiments used"),  # 14
        Step(text="done"),
    )


@dataclass(frozen=True)
class BehaviourValues:
    """The §4.4 measures control 6 checks, with labels ``a``, ``b``, ``c``."""

    refused_calls: tuple[int, ...]
    refusals_charged: tuple[int | None, ...]
    experiments: int
    favoured_path: tuple[str | None, ...]
    submitted: str | None
    predictions: int
    unevaluated: int
    covered: int
    coverage: float | None
    mean_level: float | None
    calibration_error: float | None
    per_hypothesis: tuple[tuple[str, int, float, float], ...]
    refuting: int
    confirmation_only: int
    unpredicted: int
    falsification_share: float | None
    can_refute: tuple[tuple[int, str | None, str | None, bool], ...]
    falsified: int
    revised: int
    zombies: int
    revision_rate: float | None
    zombie_rate: float | None
    falsifications: tuple[tuple[int, int, str], ...]
    converged_at: int
    burned: int
    early: bool
    used_share: float


def expected_behaviour() -> BehaviourValues:
    """The values worked out by hand for :func:`behaviour_script`."""
    return BehaviourValues(
        refused_calls=(0, 5, 11),
        refusals_charged=(None, None, None),
        experiments=3,
        favoured_path=("a", "b", "a", "c"),
        submitted="c",
        predictions=3,
        unevaluated=0,
        covered=2,
        coverage=2 / 3,
        mean_level=11 / 15,
        calibration_error=2 / 3 - 11 / 15,
        per_hypothesis=(("a", 1, 0.0, 0.9), ("b", 2, 1.0, 0.65)),
        refuting=1,
        confirmation_only=1,
        unpredicted=1,
        falsification_share=1 / 3,
        can_refute=((0, "a", "b", True), (1, "b", "a", False), (2, "a", "b", False)),
        falsified=1,
        revised=1,
        zombies=1,
        revision_rate=1.0,
        zombie_rate=1.0,
        falsifications=((0, 0, "a"),),
        converged_at=3,
        burned=0,
        early=False,
        used_share=1.0,
    )


@dataclass(frozen=True)
class BehaviourVerdict:
    """Control 6: ``passed`` when every observed value is the expected one."""

    passed: bool
    observed: BehaviourValues
    expected: BehaviourValues
    mismatches: tuple[str, ...]


def _close(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def behaviour_control(
    record: SessionRecord,
    channels: tuple[ChannelSpec, ...],
    h: BehaviourHypotheses,
) -> BehaviourVerdict:
    """Control 6: read ``record`` and compare its measures with the hand values.

    ``channels`` are the native channels; labels are mapped back to ``a``,
    ``b``, ``c`` (anything else keeps its canonical text, and so mismatches).
    """
    names = {
        render(canonicalise(s)): k for k, s in (("a", h.a), ("b", h.b), ("c", h.c))
    }

    def name(label: str | None) -> str | None:
        return None if label is None else names.get(label, label)

    calls = _calls(record)
    refused = tuple(int(b["call"]) for b in calls if b["is_error"])
    charged = tuple(b["charged"] for b in calls if b["is_error"])
    line = timeline(record, channels)
    path = line.favoured_path()
    cal = calibration(line.predictions)
    fal = falsification_seeking(line.predictions, line.experiments, line.beliefs)
    rev = revision(line.predictions, line.experiments, line.beliefs)
    gaps = tuple(BEHAVIOUR_GAPS.get(name(p) or "", math.nan) for p in path)
    used = len(line.experiments)
    if (
        used <= BEHAVIOUR_EXPERIMENTS
        and len(gaps) == used + 1
        and all(math.isfinite(g) for g in gaps)
    ):
        (stop,) = stopping([StoppingRun(used, BEHAVIOUR_EXPERIMENTS, gaps)]).runs
        stop_values = (stop.converged_at, stop.burned, stop.early, stop.used_share)
    else:  # the path cannot be read as a stopping run: it cannot match
        stop_values = (-1, -1, True, math.nan)
    observed = BehaviourValues(
        refused_calls=refused,
        refusals_charged=charged,
        experiments=used,
        favoured_path=tuple(name(p) for p in path),
        submitted=name(line.submitted),
        predictions=cal.n,
        unevaluated=cal.unevaluated,
        covered=cal.covered,
        coverage=cal.coverage,
        mean_level=cal.mean_level,
        calibration_error=cal.error,
        per_hypothesis=tuple(
            (name(x.hypothesis) or "", x.n, x.coverage, x.mean_level)
            for x in cal.per_hypothesis
        ),
        refuting=fal.refuting,
        confirmation_only=fal.confirmation_only,
        unpredicted=fal.unpredicted,
        falsification_share=fal.share,
        can_refute=tuple(
            (r.index, name(r.favoured), name(r.runner_up), r.can_refute)
            for r in fal.per_experiment
        ),
        falsified=rev.falsified,
        revised=rev.revised,
        zombies=rev.zombies,
        revision_rate=rev.rate,
        zombie_rate=rev.zombie_rate,
        falsifications=tuple(
            (e.experiment, e.turn, name(e.hypothesis) or "") for e in rev.events
        ),
        converged_at=stop_values[0],
        burned=stop_values[1],
        early=stop_values[2],
        used_share=stop_values[3],
    )
    expected = expected_behaviour()
    bad = tuple(
        f"{f.name}: observed {getattr(observed, f.name)!r}, "
        f"expected {getattr(expected, f.name)!r}"
        for f in fields(BehaviourValues)
        if not _close(getattr(observed, f.name), getattr(expected, f.name))
    )
    return BehaviourVerdict(not bad, observed, expected, bad)
