"""Positive controls for the FSD at-stop rule (criteria.md v8.1; §7 is the contract).

Run (criteria.md §7; the gate refuses any other environment)::

    R=/home/ubuntu/lanes/sciagent; D=$R/.cache/drafts/fsd-controls
    WT=$R/.claude/worktrees/fsd-dist
    env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \\
      PYTHONPYCACHEPREFIX=$(mktemp -d) \\
      nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py \\
      --worktree $WT --freeze-commit <F> [--d0-commit <D>] --control {A1,B} \\
      --phase {calibrate,test,score}

Phases: calibrate computes D0 in memory, pushes D (ctl_gate.push_d0) and only
then writes ``D0-<C>.json``; test writes ``runs-<C>.jsonl``; score writes
``score-<C>.json`` and ``score-<C>.txt`` (C is ``ctlA1`` or ``ctlB``). Seeds,
replicate counts, stream ids, rungs, thresholds, the URL and the ceiling rule
are constants or rules, never flags. Every output carries ``"status"`` and the
authoritative ``"result": {"code", "name"}``; the exit code signals it: 0 done,
10 gate, 11 STOP or INCOMPLETE, 12 VOID at construction, 13 not tested, 14 VOID
by test-time P1 breaches, 15 VOID by a determinism mismatch.

``python -I`` ignores every ``PYTHON*`` variable, so the first statements after
the standard-library and numpy imports set ``sys.dont_write_bytecode`` and
``sys.pycache_prefix``; every import from $WT or $D is local to a function.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import resource
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import numpy as np

if TYPE_CHECKING:
    from ctl_gate import GateResult, Paths
    from sciagent.systems.v2.policies import Design, DiagnosticChoice

    from sciagent.glm.data import EventLog
    from sciagent.glm.grammar import ChannelSpec
    from sciagent.investigation.world import TruthSpec

# Runtime (§7, E1): before any import from $D or $WT. An unset prefix leaves
# None here, and check_runtime then refuses (10).
sys.dont_write_bytecode = True
sys.pycache_prefix = os.environ.get("PYTHONPYCACHEPREFIX")

# --------------------------------------------------------------------------
# Constants (criteria.md v8 §0-§7)
# --------------------------------------------------------------------------

#: The criteria version and its sha256: every new version changes this file.
VERSION: Final = "8.1"
CRITERIA_SHA256: Final = (
    "963d14959d746242adb82ebc21350b0a028482599dd8b3de9cf348ea71386b41"
)
REPO: Final = Path("/home/ubuntu/lanes/sciagent")
DRAFTS_REL: Final = ".cache/drafts/fsd-controls"
WORKTREE: Final = REPO / ".claude" / "worktrees" / "fsd-dist"
WORKTREE_HEAD: Final = "bff61ecba3daed876e55896e329000704fb9626b"
#: CLI name -> control id C (criteria §0 "Streams").
CONTROLS: Final = {"A1": "ctlA1", "B": "ctlB"}
PHASES: Final = ("calibrate", "test", "score")

CAP: Final = 15_000
N_DESIGNS: Final = 8
N_DIAGNOSTICS: Final = 35
DECISIONS: Final = 8
R_TELLING: Final = 32
R_POLICY: Final = 16
R_CAL: Final = 256
R_HALF: Final = 128
N_BOOT: Final = 1000
MEAN_RATE: Final = 0
DESIGN_NAMES: Final = (
    "burst",
    "inject_sign_high",
    "inject_sign_low",
    "inject_size_high",
    "inject_size_low",
    "clamp_release",
    "silence_release",
    "censor_marked_burst",
)
E1: Final = 1  # inject_sign_high
E2: Final = 2  # inject_sign_low
E3: Final = 3  # inject_size_high
SEEDS: Final = {"ctlA1": tuple(range(101, 116)), "ctlB": tuple(range(301, 316))}

COVERAGE_COUNT: Final = 950  # 0.95 of N_BOOT, as counts
D0_MAX_LABELS: Final = 2
P3_FLOOR: Final = 0.95
CLAUSE_COUNT: Final = 990  # of N_BOOT
I_MIN: Final = 12
II_MIN: Final = 84
II_SHARE: Final = 0.7  # 84 / 120
B1_MIN: Final = 108
B3_MIN: Final = 12
BREACH_MAX_SEEDS: Final = 2
ALPHA: Final = 0.05
LEDGER_EVERY_S: Final = 30.0

EXIT_DONE: Final = 0
EXIT_GATE: Final = 10
EXIT_STOP: Final = 11
EXIT_VOID: Final = 12
EXIT_NOT_TESTED: Final = 13
EXIT_BREACH: Final = 14
EXIT_NONDET: Final = 15

GATED_PLUS: Final = "link=identity; Excite(ExpK, One, sign=+) + Excite(ExpK, One, all)"
GATED_MINUS: Final = "link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)"
SIZE_GATED: Final = (
    "link=identity; Excite(ExpK, One, all) + Excite(ExpK, Mark(size), all)"
)
HAWKES: Final = "link=identity; Excite(ExpK, One, all)"
MU: Final = 0.15

K_RULES: Final = ("K0", "K1", "K2z", "K3a", "K3h", "K4", "K5")
F_RULES: Final = ("Fa", "Fc", "Fz", "Fx")
OUTCOMES: Final = ("complete", "truncated", "cut0", "stopped")
RUN_FIELDS: Final = ("outcome", "tau", "n_events", "values", "none_other", "nonfinite")
TEST_KEY: Final = ("seed", "arm", "design", "side", "replicate")
CAL_KEY: Final = ("set", "rung", "design", "side", "replicate")

type Rule = Literal["a", "b", "c"]
type Run = Mapping[str, Any]
type Pair = tuple[Sequence[Run], Sequence[Run]]
type Pick = tuple[int, int] | None
type Key = tuple[Any, ...]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ControlsError(Exception):
    """Base of this file's typed errors."""


class LedgerError(ControlsError):
    """cpu-total.txt is missing or unparseable: the process refuses to start."""


class StopRequested(ControlsError):
    """STOP is present or the ceiling is reached (exit 11)."""


class StoreError(ControlsError):
    """A runs file is corrupt beyond a torn last line, or has duplicate keys."""


class HeaderError(ControlsError):
    """runs-<C>.jsonl has another {control, F, D} header (exit 10)."""


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------


def canonical(obj: Any) -> str:
    """criteria §7: sorted keys, compact, ASCII, no NaN, plus a newline."""
    return (
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path: Path, text: str, tmp_name: str) -> None:
    tmp = path.parent / tmp_name
    with tmp.open("w", encoding="ascii") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Models and rungs
# --------------------------------------------------------------------------


def all_rate(theta: float) -> float:
    """theta_all = 0.85 - theta/2 (criteria §1, §2)."""
    return 0.85 - theta / 2


@dataclass(frozen=True)
class ModelSpec:
    """A linear model of the grammar, exp_rate 1.0 on every kernel."""

    dsl: str
    thetas: tuple[float, ...]
    mu: float = MU

    def build(self) -> TruthSpec:
        from environments.pointproc import v2
        from sciagent.glm.canonical import canonicalise
        from sciagent.glm.grammar import PsiSlot
        from sciagent.glm.simulate import Coefficients
        from sciagent.glm.syntax import parse
        from sciagent.investigation.world import TruthSpec

        s = parse(self.dsl, v2.CHANNELS)
        if canonicalise(s) != s:
            raise ControlsError(f"not canonical: {self.dsl}")
        psi = tuple({PsiSlot((0,), "exp_rate"): 1.0} for _ in s.features)
        coef = Coefficients(self.mu, tuple((t,) for t in self.thetas))
        return TruthSpec(s, psi, coef, v2.CHANNELS, v2.mark_sampler)

    def record(self) -> dict[str, Any]:
        return {"dsl": self.dsl, "thetas": list(self.thetas), "mu": self.mu}


@dataclass(frozen=True)
class Rung:
    control: str
    index: int
    truth: ModelSpec
    planned: int  # the design where the truth truncates on every run
    channel: str  # the planted channel P2 requires

    def record(self) -> dict[str, Any]:
        return {
            "rung": self.index,
            "truth": self.truth.record(),
            "planned_design": self.planned,
            "channel": self.channel,
        }


MEMBER: Final = ModelSpec(HAWKES, (0.85,))
RUNGS: Final = {
    "ctlA1": tuple(
        Rung("ctlA1", j, ModelSpec(GATED_PLUS, (t, all_rate(t))), E1, "sign")
        for j, t in enumerate((0.5, 0.4, 0.6))
    ),
    "ctlB": (
        *(
            Rung("ctlB", j, ModelSpec(GATED_MINUS, (t, all_rate(t))), E2, "sign")
            for j, t in enumerate((0.5, 0.4, 0.6))
        ),
        Rung("ctlB", 3, ModelSpec(SIZE_GATED, (0.35, 0.5)), E3, "size"),
    ),
}
#: B's reference on e1: T_A at θ₊ = 0.5 ("ctlB-ref-r0"), else 0.6 ("ctlB-ref-r1").
REF_MODELS: Final = (
    ModelSpec(GATED_PLUS, (0.5, all_rate(0.5))),
    ModelSpec(GATED_PLUS, (0.6, all_rate(0.6))),
)


# --------------------------------------------------------------------------
# Streams (criteria §0)
# --------------------------------------------------------------------------


def rng_of(seq: np.random.SeedSequence) -> np.random.Generator:
    from sciagent.scoring.seeds import generator

    return generator(seq)


REAL_IDS: Final = ("ctlA1", "ctlB")


def require_token(stream_id: str, token: object) -> None:
    """The real control ids, and every id derived from them, are drawn only
    with the token a passed gate builds (§7; not a security boundary)."""
    if not stream_id.startswith(REAL_IDS):
        return
    import ctl_gate

    if not isinstance(token, ctl_gate.GateToken):
        raise ControlsError(f"stream id {stream_id!r} needs the gate's token")


def telling_seed(
    control: str, s: int, side: int, e: int, r: int, *, token: object
) -> Any:
    from sciagent.scoring.seeds import TELLING, scorer_seed

    require_token(control, token)
    return scorer_seed(TELLING, control, s, side, e, r)


def decision_seed(s: int, k: int, index: int, r: int) -> Any:
    """policy_seed has no control id: draw_todo guards the real decision runs."""
    from sciagent.scoring.seeds import policy_seed

    return policy_seed(s, k, index, r)


def cal_seed(control: str, j: int, side: int, e: int, r: int, *, token: object) -> Any:
    from sciagent.scoring.seeds import TELLING, scorer_seed

    require_token(f"{control}-cal-r{j}", token)
    return scorer_seed(TELLING, f"{control}-cal-r{j}", 0, side, e, r)


def crn_seed(control: str, j: int, side: int, r: int, *, token: object) -> Any:
    from sciagent.scoring.seeds import TELLING, scorer_seed

    require_token(f"{control}-crn-r{j}", token)
    return scorer_seed(TELLING, f"{control}-crn-r{j}", 0, side, r)


def boot_seed(control: str, j: int, *, token: object) -> Any:
    from sciagent.scoring.seeds import TELLING, scorer_seed

    require_token(f"{control}-boot-r{j}", token)
    return scorer_seed(TELLING, f"{control}-boot-r{j}", 0)


def ref_seed(j: int, r: int, *, token: object) -> Any:
    from sciagent.scoring.seeds import TELLING, scorer_seed

    require_token(f"ctlB-ref-r{j}", token)
    return scorer_seed(TELLING, f"ctlB-ref-r{j}", 0, 0, E1, r)


def random_labels(seed: int, labels: Sequence[str]) -> tuple[str, ...]:
    """RandomPolicy's 8 labels for a seed (policies.py:968-972; criteria §0)."""
    from sciagent.systems.v2.policies import RANDOM_INDEX

    out: list[str] = []
    for k in range(DECISIONS):
        rng = rng_of(decision_seed(seed, k, RANDOM_INDEX, 0))
        _, d = divmod(int(rng.integers(N_DESIGNS * len(labels))), len(labels))
        out.append(labels[d])
    return tuple(out)


# --------------------------------------------------------------------------
# One run (criteria §0 "A run")
# --------------------------------------------------------------------------


def channels() -> tuple[ChannelSpec, ...]:
    from environments.pointproc import v2

    return v2.CHANNELS


def designs() -> tuple[Design, ...]:
    from sciagent.systems.v2.policies import design_menu

    out = design_menu(channels())
    if tuple(d.name for d in out) != DESIGN_NAMES:
        raise ControlsError(f"design menu changed: {[d.name for d in out]}")
    return out


def menu_for(truth: TruthSpec, seed: int) -> tuple[DiagnosticChoice, ...]:
    """diagnostic_menu on the observational log of World(truth, seed, truncate=True)."""
    from sciagent.systems.v2.policies import diagnostic_menu

    from sciagent.investigation.world import OBSERVATIONAL, World

    log = World(truth, seed, truncate=True).datasets[OBSERVATIONAL].log
    menu = diagnostic_menu(log, channels())
    if len(menu) != N_DIAGNOSTICS or menu[MEAN_RATE].label != "mean_rate":
        raise ControlsError("diagnostic menu changed")
    return menu


def read_values(
    diagnostics: Sequence[DiagnosticChoice],
    compute: Callable[[DiagnosticChoice], float | None],
) -> tuple[list[float | None], int, int]:
    """Every diagnostic; other DiagnosticErrors and non-finite values are None.

    Returns (values, none_other, nonfinite).
    """
    from sciagent.diagnostics import catalogue

    values: list[float | None] = []
    other = nonfinite = 0
    for choice in diagnostics:
        try:
            v = compute(choice)
        except catalogue.DiagnosticError:
            values.append(None)
            other += 1
            continue
        if v is None:
            values.append(None)
        elif not math.isfinite(v):
            values.append(None)
            nonfinite += 1
        else:
            values.append(float(v))
    return values, other, nonfinite


def outcome_record(
    outcome: str,
    tau: float | None,
    n_events: int | None,
    values: list[float | None] | None,
    none_other: int = 0,
    nonfinite: int = 0,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ControlsError(f"unknown outcome {outcome!r}")
    return {
        "outcome": outcome,
        "tau": tau,
        "n_events": n_events,
        "values": values,
        "none_other": none_other,
        "nonfinite": nonfinite,
    }


def simulate_run(
    model: TruthSpec,
    design: Design,
    rng: np.random.Generator,
    diagnostics: Sequence[DiagnosticChoice],
) -> dict[str, Any]:
    """One run under the cap: complete, truncated, cut0 or stopped."""
    from sciagent.systems.v2.policies import (
        POLICY_MAX_EVENTS,
        STOPPED,
        diagnostic_value,
    )

    from sciagent.glm.interventions import run_experiment_truncating
    from sciagent.investigation.view import ViewError, clip_to_analysis

    if POLICY_MAX_EVENTS != CAP:
        raise ControlsError(f"POLICY_MAX_EVENTS is {POLICY_MAX_EVENTS}, not {CAP}")
    try:
        ds, tau = run_experiment_truncating(
            model.structure,
            model.psi,
            model.coef,
            model.channels,
            model.mark_sampler,
            design.experiment,
            rng,
            max_events=POLICY_MAX_EVENTS,
            label=design.name,
        )
    except STOPPED:
        return outcome_record("stopped", None, None, None)
    n = int(ds.log.n)
    log: EventLog = ds.log
    outcome = "complete"
    if tau is not None:
        try:
            log = clip_to_analysis(ds, tau).log
        except ViewError:
            return outcome_record("cut0", float(tau), n, [None] * len(diagnostics))
        outcome = "truncated"
    chans = model.channels

    def compute(c: DiagnosticChoice) -> float | None:
        return diagnostic_value(c, log, chans)

    values, other, nonfinite = read_values(diagnostics, compute)
    return outcome_record(
        outcome, None if tau is None else float(tau), n, values, other, nonfinite
    )


# --------------------------------------------------------------------------
# Runs files (resumable; criteria §7)
# --------------------------------------------------------------------------


class RunStore:
    """Runs keyed by ``key_fields``: an append-only JSONL file whose first line
    is ``header`` (test's runs-<C>.jsonl), or memory only (``path`` None:
    calibrate, which writes nothing before D is pushed)."""

    def __init__(
        self,
        path: Path | None,
        key_fields: Sequence[str],
        header: Mapping[str, Any] | None = None,
    ) -> None:
        if (path is None) != (header is None):
            raise StoreError("a runs file has a header; a memory store has none")
        self.path = path
        self.key_fields = tuple(key_fields)
        self.header = None if header is None else canonical(dict(header)).encode()
        self.records: dict[Key, dict[str, Any]] = {}
        self.raw: dict[Key, bytes] = {}

    def key(self, record: Mapping[str, Any]) -> Key:
        return tuple(record[f] for f in self.key_fields)

    def load(self) -> None:
        """Check the header, read every line; a torn last line is cut.

        A file whose header differs refuses (HeaderError, exit 10); an empty
        file, or one holding only a torn header, starts afresh.
        """
        self.records.clear()
        self.raw.clear()
        if self.path is None or self.header is None:
            return
        data = self.path.read_bytes() if self.path.exists() else b""
        if not data.startswith(self.header):
            if not self.header.startswith(data):
                raise HeaderError(f"{self.path}: header is not {self.header!r}")
            self.path.write_bytes(self.header)
            return
        chunks = data[len(self.header) :].splitlines(keepends=True)
        good = len(self.header)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            try:
                if not chunk.endswith(b"\n"):
                    raise ValueError("no newline")
                record = json.loads(chunk)
            except ValueError as error:
                if last:
                    break
                raise StoreError(f"{self.path}: line {i + 2} is corrupt") from error
            if not isinstance(record, dict):
                raise StoreError(f"{self.path}: line {i + 2} is not an object")
            k = self.key(record)
            if k in self.records:
                raise StoreError(f"{self.path}: duplicate key {k}")
            self.records[k] = record
            self.raw[k] = chunk
            good += len(chunk)
        if good != len(data):
            with self.path.open("r+b") as f:
                f.truncate(good)

    def __contains__(self, k: Key) -> bool:
        return k in self.records

    def append(self, record: dict[str, Any]) -> None:
        k = self.key(record)
        if k in self.records:
            raise StoreError(f"{self.path}: duplicate key {k}")
        text = canonical(record).encode("ascii")
        if self.path is not None:
            with self.path.open("ab") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        self.records[k] = record
        self.raw[k] = text


def run_line(key: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(key)
    for f in RUN_FIELDS:
        out[f] = result[f]
    return out


# --------------------------------------------------------------------------
# The simulation rules and the K-/F-rules (criteria §0)
# --------------------------------------------------------------------------


def is_truncated(run: Run) -> bool:
    """Truncated, cut0 included (cut0 counts as truncated, criteria §0)."""
    return run["outcome"] in ("truncated", "cut0")


def is_stopped(run: Run) -> bool:
    return bool(run["outcome"] == "stopped")


def column(runs: Sequence[Run], rule: Rule, d: int) -> list[float | None]:
    """One diagnostic's values over a side's runs under rule (a), (b) or (c)."""
    out: list[float | None] = []
    for run in runs:
        hit = is_truncated(run) or is_stopped(run)
        if rule == "b" and hit:
            continue
        if is_stopped(run) or (rule == "a" and hit):
            out.append(None)
            continue
        values = run["values"]
        out.append(None if values is None else values[d])
    return out


def he2_row(pair: Pair, rule: Rule, n_d: int) -> list[float]:
    from sciagent.scoring.battery import hellinger2

    a, b = pair
    if rule == "b":
        kept_a = any(not (is_truncated(x) or is_stopped(x)) for x in a)
        kept_b = any(not (is_truncated(x) or is_stopped(x)) for x in b)
        if not (kept_a and kept_b):
            return [0.0] * n_d
    return [hellinger2(column(a, rule, d), column(b, rule, d)) for d in range(n_d)]


def he2_table(
    pairs: Sequence[Pair],
    rule: Rule,
    n_d: int,
    skip: frozenset[int] = frozenset(),
) -> list[list[float]]:
    """He² per (e, d); rows in ``skip`` are not computed (0.0 placeholders,
    only ever used for excluded designs)."""
    return [
        [0.0] * n_d if e in skip else he2_row(pair, rule, n_d)
        for e, pair in enumerate(pairs)
    ]


def n_truncated(runs: Sequence[Run]) -> int:
    return sum(is_truncated(x) for x in runs)


def n_stopped(runs: Sequence[Run]) -> int:
    return sum(is_stopped(x) for x in runs)


def zero_mean_rate(table: list[list[float]], pairs: Sequence[Pair]) -> None:
    """K2z / Fz: mean_rate's cell is 0 where either side has a truncated run."""
    for e, (a, b) in enumerate(pairs):
        if n_truncated(a) or n_truncated(b):
            table[e][MEAN_RATE] = 0.0


def excluded_where(
    pairs: Sequence[Pair], test: Callable[[Sequence[Run]], bool]
) -> frozenset[int]:
    return frozenset(e for e, (a, b) in enumerate(pairs) if test(a) or test(b))


def any_truncated(runs: Sequence[Run]) -> bool:
    return n_truncated(runs) >= 1


def most_truncated(runs: Sequence[Run]) -> bool:
    return 2 * n_truncated(runs) > len(runs)


def any_truncated_or_stopped(runs: Sequence[Run]) -> bool:
    return n_truncated(runs) + n_stopped(runs) >= 1


@dataclass(frozen=True)
class Scored:
    """A rule's table and the designs it removes from the argmax."""

    table: tuple[tuple[float, ...], ...]
    excluded: frozenset[int]


def _scored(table: list[list[float]], excluded: frozenset[int]) -> Scored:
    return Scored(tuple(tuple(row) for row in table), excluded)


def k5_exclusion(telling_pairs: Sequence[Pair]) -> frozenset[int]:
    """X: designs where truth or member has a truncated or stopped run among
    telling's 32. Scoring-internal only (invariant 2)."""
    return excluded_where(telling_pairs, any_truncated_or_stopped)


def _k_scored(
    k_rule: str, pairs: Sequence[Pair], n_d: int, base: Rule, x: frozenset[int]
) -> Scored:
    if k_rule == "K0":
        return _scored(he2_table(pairs, base, n_d), frozenset())
    if k_rule == "K1":
        return _scored(he2_table(pairs, "c", n_d), frozenset())
    if k_rule == "K2z":
        table = he2_table(pairs, "c", n_d)
        zero_mean_rate(table, pairs)
        return _scored(table, frozenset())
    tests: dict[str, Callable[[Sequence[Run]], bool]] = {
        "K3a": any_truncated,
        "K3h": most_truncated,
        "K4": any_truncated_or_stopped,
    }
    if k_rule in tests:
        excluded = excluded_where(pairs, tests[k_rule])
    elif k_rule == "K5":
        excluded = x
    else:
        raise ControlsError(f"unknown K-rule {k_rule!r}")
    return _scored(he2_table(pairs, "c", n_d, excluded), excluded)


def telling_scored(k_rule: str, pairs: Sequence[Pair], n_d: int) -> Scored:
    """Telling's table under a K-rule; pairs are (truth, member) per design."""
    x = k5_exclusion(pairs) if k_rule == "K5" else frozenset()
    return _k_scored(k_rule, pairs, n_d, "b", x)


def truthfsd_scored(
    k_rule: str, pairs: Sequence[Pair], n_d: int, x: frozenset[int]
) -> Scored:
    """truthFSD's table; pairs are (member, truth); ``x`` is K5's X from
    telling's runs (unused by every other rule)."""
    return _k_scored(k_rule, pairs, n_d, "a", x)


def fsd_scored(f_rule: str, pairs: Sequence[Pair], n_d: int) -> Scored:
    """FSD's table; pairs are (parametric, reference)."""
    if f_rule == "Fa":
        return _scored(he2_table(pairs, "a", n_d), frozenset())
    if f_rule == "Fc":
        return _scored(he2_table(pairs, "c", n_d), frozenset())
    if f_rule == "Fz":
        table = he2_table(pairs, "c", n_d)
        zero_mean_rate(table, pairs)
        return _scored(table, frozenset())
    if f_rule == "Fx":
        excluded = excluded_where(pairs, any_truncated_or_stopped)
        return _scored(he2_table(pairs, "c", n_d, excluded), excluded)
    raise ControlsError(f"unknown F-rule {f_rule!r}")


def telling_pick(
    scored: Scored,
    design_menu: Sequence[Design],
    diagnostics: Sequence[DiagnosticChoice],
) -> Pick:
    """Telling's d-major argmax over the designs not excluded; None if all are
    (Telling.from_table is then never called)."""
    from sciagent.scoring.telling import Telling

    kept = [e for e in range(len(scored.table)) if e not in scored.excluded]
    if not kept:
        return None
    t = Telling.from_table(
        tuple(scored.table[e] for e in kept),
        tuple(design_menu[e] for e in kept),
        tuple(diagnostics),
    )
    return kept[t.design_index], t.diagnostic_index


def policy_pick(scored: Scored) -> Pick:
    """truthFSD's and FSD's e-major argmax (policies.choose) over the designs
    not excluded; None if all are."""
    from sciagent.systems.v2.policies import choose

    kept = [e for e in range(len(scored.table)) if e not in scored.excluded]
    if not kept:
        return None
    e, d = choose(tuple(scored.table[e] for e in kept))
    return kept[e], d


def label_of(pick: Pick, labels: Sequence[str]) -> str | None:
    return None if pick is None else labels[pick[1]]


# --------------------------------------------------------------------------
# Power formulas (power2.py, frozen; deterministic iteration order)
# --------------------------------------------------------------------------


def mcnemar_p(b: int, c: int) -> Fraction:
    n = b + c
    return Fraction(sum(math.comb(n, i) for i in range(b, n + 1)), 2**n)


def pass_iii(
    seeds: Sequence[int],
    p32: Mapping[str, float],
    h_of: Mapping[str, float],
    random_by_seed: Mapping[int, Sequence[str]],
    alpha: float = ALPHA,
) -> float:
    """P(policy_separation passes) given random's fixed picks (power2.pass_iii)."""
    order = sorted(p32)
    dist: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for s in seeds:
        labs = set(random_by_seed[s])
        p_b = math.fsum(p32[x] * h_of[x] for x in order if x not in labs)
        p_c = math.fsum(p32[x] * (1 - h_of[x]) for x in order if x in labs)
        nxt: dict[tuple[int, int], float] = {}
        for (b, c), w in sorted(dist.items()):
            for db, dc, q in ((1, 0, p_b), (0, 1, p_c), (0, 0, 1 - p_b - p_c)):
                if q > 0:
                    key = (b + db, c + dc)
                    nxt[key] = nxt.get(key, 0.0) + w * q
        dist = nxt
    return math.fsum(w for (b, c), w in sorted(dist.items()) if mcnemar_p(b, c) < alpha)


def pass_ii(
    n_seeds: int,
    p32: Mapping[str, float],
    p16: Mapping[str, float],
    share: float = II_SHARE,
) -> float:
    """P(Σ_s Bin(8, p16[label_s]) ≥ share·8·n), label_s ~ p32 (power2.pass_ii)."""
    per = [0.0] * (DECISIONS + 1)
    for lab in sorted(p32):
        p, q = p32[lab], p16.get(lab, 0.0)
        for j in range(DECISIONS + 1):
            per[j] += p * math.comb(DECISIONS, j) * q**j * (1 - q) ** (DECISIONS - j)
    dist = [1.0]
    for _ in range(n_seeds):
        new = [0.0] * (len(dist) + DECISIONS)
        for i, w in enumerate(dist):
            for j, q in enumerate(per):
                new[i + j] += w * q
        dist = new
    need = math.ceil(share * DECISIONS * n_seeds)
    return math.fsum(dist[need:])


def ever(p: float) -> float:
    return 1 - (1 - p) ** DECISIONS


# --------------------------------------------------------------------------
# CPU ledger (criteria §6)
# --------------------------------------------------------------------------


def process_cpu_seconds() -> float:
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    """cpu-total.txt under flock, an audit CSV, and STOP (criteria §6)."""

    def __init__(
        self,
        directory: Path,
        phase: str,
        control: str,
        ceiling_s: float,
        *,
        exempt: bool = False,
        coarse: bool = False,
        clock: Callable[[], float] = process_cpu_seconds,
    ) -> None:
        if not (math.isfinite(ceiling_s) and ceiling_s > 0):
            raise LedgerError(f"ceiling must be finite and > 0, got {ceiling_s}")
        self.directory = directory
        self.phase = phase
        self.control = control
        self.ceiling_s = ceiling_s
        self.exempt = exempt
        #: calibrate: charge only every 30 CPU s and at exit, with ``unit`` a
        #: counter of charges (not of runs), so the ledger shows neither per-run
        #: CPU nor the run rate before D is pushed (§6; s18b 9).
        self.coarse = coarse
        self.charges = 0
        self.clock = clock
        self.last = 0.0  # everything since the process started is charged
        self.total_path = directory / "cpu-total.txt"
        self.lock_path = directory / "cpu-total.lock"
        self.audit_path = directory / "cpu-ledger.csv"
        self.stop_path = directory / "STOP"

    def read_total(self) -> float:
        try:
            text = self.total_path.read_text(encoding="ascii")
        except FileNotFoundError as error:
            raise LedgerError(f"{self.total_path} is missing") from error
        except UnicodeDecodeError as error:
            raise LedgerError(f"{self.total_path} is not ASCII") from error
        try:
            total = float(text.strip())
        except ValueError as error:
            raise LedgerError(f"{self.total_path} is unparseable: {text!r}") from error
        if not (math.isfinite(total) and total >= 0):
            raise LedgerError(f"{self.total_path} holds {total}")
        return total

    def start(self) -> None:
        self.read_total()

    def charge(self, unit: str) -> float:
        now = self.clock()
        delta = now - self.last
        with self.lock_path.open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                total = self.read_total() + delta
                write_atomic(self.total_path, f"{total!r}\n", "cpu-total.txt.tmp")
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self.last = now
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerow(
            [
                self.phase,
                self.control,
                unit,
                repr(delta),
                repr(total),
                os.getpid(),
                utc_now(),
            ]
        )
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, buf.getvalue().encode("ascii"))
        finally:
            os.close(fd)
        return total

    def check_stop(self) -> None:
        """Before each step: STOP present, or the total at the ceiling."""
        if self.exempt:
            return
        if self.stop_path.exists():
            raise StopRequested(f"{self.stop_path} exists")
        total = self.read_total()
        if total >= self.ceiling_s:
            try:
                fd = os.open(self.stop_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                pass
            else:
                try:
                    os.write(fd, f"total={total!r} time={utc_now()}\n".encode("ascii"))
                finally:
                    os.close(fd)
            raise StopRequested(f"total {total} >= ceiling {self.ceiling_s}")

    def tick(self, unit: str) -> None:
        """Inside non-simulation loops: charge at least every 30 CPU s."""
        if self.clock() - self.last >= LEDGER_EVERY_S:
            self.charges += 1
            self.charge(f"charge-{self.charges}" if self.coarse else unit)
            self.check_stop()

    def after_run(self, unit: str) -> None:
        """After each run: charged per run, or coarsely in calibrate."""
        if self.coarse:
            self.tick(unit)
        else:
            self.charge(unit)

    def at_exit(self) -> float:
        self.charges += 1
        return self.charge(f"charge-{self.charges}-exit" if self.coarse else "exit")


class NullLedger(Ledger):
    """No files: for the stub tests and the pre-F determinism unit."""

    def __init__(self) -> None:
        super().__init__(Path("/nonexistent"), "none", "none", 1.0, exempt=True)

    def start(self) -> None:
        return None

    def charge(self, unit: str) -> float:
        return 0.0

    def check_stop(self) -> None:
        return None

    def tick(self, unit: str) -> None:
        return None

    def after_run(self, unit: str) -> None:
        return None

    def at_exit(self) -> float:
        return 0.0


def execute(phase: Callable[[], int], ledger: Ledger) -> int:
    """Run a phase; on STOP charge the ledger and exit 11; charge at exit."""
    try:
        code = phase()
    except StopRequested:
        ledger.at_exit()
        print("INCOMPLETE: STOP", file=sys.stderr)
        return EXIT_STOP
    ledger.at_exit()
    return code


# --------------------------------------------------------------------------
# Calibration (criteria §0 "Calibration", "Preconditions", "Rungs")
# --------------------------------------------------------------------------

SIDE_NAMES: Final = ("truth", "member")


@dataclass(frozen=True)
class CalRuns:
    """per_design[(e, side)][r] and crn[(e, side)][r], r < 256."""

    per_design: Mapping[tuple[int, int], Sequence[Run]]
    crn: Mapping[tuple[int, int], Sequence[Run]]


def draw_runs(
    store: RunStore,
    todo: Iterable[tuple[dict[str, Any], TruthSpec, Design, Callable[[], Any]]],
    diagnostics: Sequence[DiagnosticChoice],
    ledger: Ledger,
    unit: str,
) -> None:
    """Simulate every (key, model, design, seed) not yet stored."""
    for key, model, design, seed in todo:
        if store.key(key) in store:
            continue
        ledger.check_stop()
        result = simulate_run(model, design, rng_of(seed()), diagnostics)
        store.append(run_line(key, result))
        ledger.after_run(unit)


def cal_todo(
    rung: Rung,
    truth: TruthSpec,
    member: TruthSpec,
    menu: Sequence[Design],
    *,
    token: object,
) -> list[tuple[dict[str, Any], TruthSpec, Design, Callable[[], Any]]]:
    c, j = rung.control, rung.index
    require_token(c, token)
    out: list[tuple[dict[str, Any], TruthSpec, Design, Callable[[], Any]]] = []
    for kind in ("cal", "crn"):
        for e, design in enumerate(menu):
            for side, model in enumerate((truth, member)):
                for r in range(R_CAL):
                    key = {"set": kind, "rung": j, "design": e, "side": side}
                    key["replicate"] = r
                    if kind == "cal":

                        def seed(side: int = side, e: int = e, r: int = r) -> Any:
                            return cal_seed(c, j, side, e, r, token=token)

                    else:

                        def seed(side: int = side, e: int = e, r: int = r) -> Any:
                            return crn_seed(c, j, side, r, token=token)

                    out.append((key, model, design, seed))
    return out


def cal_runs(store: RunStore, j: int) -> CalRuns:
    sets: dict[str, dict[tuple[int, int], list[Run]]] = {"cal": {}, "crn": {}}
    for kind, runs in sets.items():
        for e in range(N_DESIGNS):
            for side in (0, 1):
                runs[(e, side)] = [
                    store.records[(kind, j, e, side, r)] for r in range(R_CAL)
                ]
    return CalRuns(sets["cal"], sets["crn"])


def p1_counts(runs: Mapping[tuple[int, int], Sequence[Run]]) -> list[list[int]]:
    """[e, side, truncated (cut0 included), cut0, stopped, n] per cell."""
    out = []
    for (e, side), rs in sorted(runs.items()):
        cut0 = sum(x["outcome"] == "cut0" for x in rs)
        out.append([e, side, n_truncated(rs), cut0, n_stopped(rs), len(rs)])
    return out


def p1_holds(rung: Rung, cal: CalRuns) -> bool:
    """P1: 256/256 truncated where planned; elsewhere nothing; the member never."""
    for runs in (cal.per_design, cal.crn):
        for (e, side), rs in runs.items():
            planned = side == 0 and e == rung.planned
            if n_stopped(rs):
                return False
            if planned and n_truncated(rs) != len(rs):
                return False
            if not planned and n_truncated(rs):
                return False
    return True


def e_cal(cal: CalRuns) -> frozenset[int]:
    return frozenset(
        e
        for runs in (cal.per_design, cal.crn)
        for (e, _), rs in runs.items()
        if n_truncated(rs) or n_stopped(rs)
    )


def subsample(rng: np.random.Generator, offset: int, k: int) -> list[int]:
    """k of the R_HALF indices offset..offset+R_HALF-1, without replacement, sorted."""
    picked = rng.choice(R_HALF, k, replace=False)
    return [offset + int(i) for i in sorted(picked)]


def pairs_at(
    runs: Mapping[tuple[int, int], Sequence[Run]],
    idx: Sequence[int],
    order: tuple[int, int] = (0, 1),
) -> list[Pair]:
    return [
        (
            [runs[(e, order[0])][r] for r in idx],
            [runs[(e, order[1])][r] for r in idx],
        )
        for e in range(N_DESIGNS)
    ]


def label_counts(picks: Sequence[str | None], labels: Sequence[str]) -> dict[str, int]:
    """Counts per label, in menu order; exclude-all draws (None) are not labels."""
    out: dict[str, int] = {}
    for lab in labels:
        n = sum(p == lab for p in picks)
        if n:
            out[lab] = n
    return out


def d_star0(counts: Mapping[str, int], labels: Sequence[str]) -> tuple[list[str], bool]:
    """Fewest labels, by decreasing frequency (menu order on ties), reaching 950."""
    order = sorted(counts, key=lambda x: (-counts[x], labels.index(x)))
    out: list[str] = []
    total = 0
    for lab in order:
        if total >= COVERAGE_COUNT:
            break
        out.append(lab)
        total += counts[lab]
    return out, total >= COVERAGE_COUNT


def carries(label: str, channel: str) -> bool:
    if "[" not in label or not label.endswith("]"):
        return False
    return channel in label[label.index("[") + 1 : -1].split(",")


def probabilities(counts: Mapping[str, int]) -> dict[str, float]:
    return {lab: c / N_BOOT for lab, c in counts.items()}


@dataclass(frozen=True)
class Bootstrap:
    d_star0_counts: dict[str, int]
    p32_counts: dict[str, int]
    p16_counts: dict[str, int]
    pf_counts: dict[str, int] | None
    clause_count: int


def bootstrap_rung(
    rung: Rung,
    cal: CalRuns,
    labels: Sequence[str],
    design_menu: Sequence[Design],
    diagnostics: Sequence[DiagnosticChoice],
    ledger: Ledger,
    *,
    token: object,
    n_boot: int | None = None,
) -> Bootstrap:
    """The rung's bootstrap, in the fixed order of criteria §0 (N_BOOT draws
    of each kind unless a stub test passes ``n_boot``)."""
    n_boot = N_BOOT if n_boot is None else n_boot
    rng = rng_of(boot_seed(rung.control, rung.index, token=token))
    ecal = e_cal(cal)
    n_d = len(labels)
    unit = f"boot-r{rung.index}"

    def telling_label(idx: list[int]) -> str | None:
        pairs = pairs_at(cal.per_design, idx)
        scored = _scored(he2_table(pairs, "c", n_d, ecal), ecal)
        return label_of(telling_pick(scored, design_menu, diagnostics), labels)

    def policy_label(idx: list[int], excluded: frozenset[int]) -> str | None:
        pairs = pairs_at(cal.crn, idx, (1, 0))  # (member, truth)
        scored = _scored(he2_table(pairs, "c", n_d, excluded), excluded)
        return label_of(policy_pick(scored), labels)

    picks: list[str | None] = []
    for _ in range(n_boot):
        picks.append(telling_label(subsample(rng, 0, R_TELLING)))
        ledger.tick(unit)
    sel = label_counts(picks, labels)
    picks = []
    for _ in range(n_boot):
        picks.append(telling_label(subsample(rng, R_HALF, R_TELLING)))
        ledger.tick(unit)
    p32 = label_counts(picks, labels)
    picks = []
    for _ in range(n_boot):
        picks.append(policy_label(subsample(rng, R_HALF, R_POLICY), ecal))
        ledger.tick(unit)
    p16 = label_counts(picks, labels)
    pf: dict[str, int] | None = None
    if rung.control == "ctlB":
        picks = []
        for _ in range(n_boot):
            idx = subsample(rng, R_HALF, R_POLICY)
            pairs = pairs_at(cal.crn, idx, (1, 0))
            # Fx with the reference truncating on e1 (ref check) and the truth
            # on the planned design: those two are always excluded.
            excluded = frozenset({E1, rung.planned}) | excluded_where(
                pairs, any_truncated_or_stopped
            )
            picks.append(policy_label(idx, excluded))
            ledger.tick(unit)
        pf = label_counts(picks, labels)
    clause = 0
    others = frozenset({rung.planned})
    for _ in range(n_boot):
        idx = subsample(rng, R_HALF, R_POLICY)
        pairs = pairs_at(cal.crn, idx, (1, 0))
        table = he2_table(pairs, "c", n_d, others)
        clause += all(
            x != 1.0 for e, row in enumerate(table) if e not in others for x in row
        )
        ledger.tick(unit)
    return Bootstrap(sel, p32, p16, pf, clause)


def read_rung(control: str, values: Mapping[str, Any]) -> str:
    """criteria §0 "Rungs and their reading"."""
    if not (values["p1"] and values["p2"] and values["clause99"]):
        return "unbuildable"
    if control == "ctlA1":
        if (
            values["h"] >= P3_FLOOR
            and values["pass_ii"] >= P3_FLOOR
            and values["pass_iii"] >= P3_FLOOR
        ):
            return "passes"
        return "R=16 shortfall"
    if values["h_f"] >= P3_FLOOR and values["pass_iii_f"] >= P3_FLOOR:
        return "passes"
    if values["h"] < P3_FLOOR:
        return "R=16 shortfall"
    if values["h_f"] < P3_FLOOR:
        return "cost of Fx's exclusion"
    return "separation shortfall"


def rung_values(
    rung: Rung,
    cal: CalRuns,
    boot: Bootstrap,
    labels: Sequence[str],
    random_by_seed: Mapping[int, Sequence[str]],
) -> dict[str, Any]:
    """Every P1/P2/P3(-B) value of a rung, and its reading."""
    seeds = SEEDS[rung.control]
    sel, ok = d_star0(boot.d_star0_counts, labels)
    p32 = probabilities(boot.p32_counts)
    p16 = probabilities(boot.p16_counts)
    support = sorted(set(p32) | set(p16))
    h_of = {lab: ever(p16.get(lab, 0.0)) for lab in support}
    h = math.fsum(p32[lab] * h_of[lab] for lab in sorted(p32))
    coverage = sum(boot.p32_counts.get(lab, 0) for lab in sel)
    p2 = {
        "size": len(sel) <= D0_MAX_LABELS,
        "channel": bool(sel) and all(carries(lab, rung.channel) for lab in sel),
        "no_mean_rate": "mean_rate" not in sel,
        "reached": ok,
        "coverage_count": coverage,
        "coverage": coverage >= COVERAGE_COUNT,
    }
    p2_pass = all(p2[k] for k in ("size", "channel", "no_mean_rate", "reached"))
    p2_pass = p2_pass and coverage >= COVERAGE_COUNT
    out: dict[str, Any] = {
        **rung.record(),
        "p1_counts": {"cal": p1_counts(cal.per_design), "crn": p1_counts(cal.crn)},
        "p1": p1_holds(rung, cal),
        "e_cal": sorted(e_cal(cal)),
        "d_star0": sel,
        "d_star0_counts": boot.d_star0_counts,
        "p2_parts": p2,
        "p2": p2_pass,
        "p32_counts": boot.p32_counts,
        "p16_counts": boot.p16_counts,
        "h_by_label": h_of,
        "h": h,
        "clause99_count": boot.clause_count,
        "clause99": boot.clause_count >= CLAUSE_COUNT,
    }
    if rung.control == "ctlA1":
        out["pass_ii"] = pass_ii(len(seeds), p32, p16)
        out["pass_iii"] = pass_iii(seeds, p32, h_of, random_by_seed)
        out["p3"] = (
            h >= P3_FLOOR
            and out["pass_ii"] >= P3_FLOOR
            and out["pass_iii"] >= P3_FLOOR
            and out["clause99"]
        )
    else:
        if boot.pf_counts is None:
            raise ControlsError("B needs p_F")
        pf = probabilities(boot.pf_counts)
        hf_of = {lab: ever(pf.get(lab, 0.0)) for lab in sorted(set(p32) | set(pf))}
        out["p_f_counts"] = boot.pf_counts
        out["h_f_by_label"] = hf_of
        out["h_f"] = math.fsum(p32[lab] * hf_of[lab] for lab in sorted(p32))
        out["pass_iii_f"] = pass_iii(seeds, p32, hf_of, random_by_seed)
        out["p3_b"] = (
            out["h_f"] >= P3_FLOOR and out["pass_iii_f"] >= P3_FLOOR and out["clause99"]
        )
    out["reading"] = read_rung(rung.control, out)
    out["buildable"] = out["reading"] != "unbuildable"
    return out


def calibration_result(
    rungs: Sequence[Mapping[str, Any]], ref_ok: bool = True
) -> tuple[int, int | None, str]:
    """(exit code, tested rung, name): 0 a rung passes; 12 VOID; 13 not tested."""
    if not ref_ok:
        return EXIT_VOID, None, "VOID: B's reference check failed"
    for r in rungs:
        if r["reading"] == "passes":
            return EXIT_DONE, int(r["rung"]), "calibrated"
    if any(r["buildable"] for r in rungs):
        return EXIT_NOT_TESTED, None, "not tested: no rung passes, one is buildable"
    return EXIT_VOID, None, "VOID: every rung is unbuildable"


def ref_check(
    store: RunStore, ledger: Ledger, *, token: object
) -> tuple[int | None, list[int]]:
    """B's reference check: T_A truncates on e1 in 256/256 (criteria §2)."""
    menu = designs()
    counts: list[int] = []
    for j, spec in enumerate(REF_MODELS):
        model = spec.build()
        diagnostics = menu_for(model, 0)
        todo = []
        for r in range(R_CAL):
            key = {"set": "ref", "rung": j, "design": E1, "side": 0, "replicate": r}

            def seed(j: int = j, r: int = r) -> Any:
                return ref_seed(j, r, token=token)

            todo.append((key, model, menu[E1], seed))
        draw_runs(store, todo, diagnostics, ledger, f"ref-r{j}")
        runs = [store.records[("ref", j, E1, 0, r)] for r in range(R_CAL)]
        n = sum(is_truncated(x) for x in runs)  # cut0 counts as truncated (§0)
        counts.append(n)
        if n == R_CAL:
            return j, counts
    return None, counts


def calibration_d0(control: str, ledger: Ledger, *, token: object) -> dict[str, Any]:
    """Every rung's values and the result, in memory only (§5 "D")."""
    require_token(control, token)
    store = RunStore(None, CAL_KEY)  # memory only: no calibration runs file
    menu = designs()
    member = MEMBER.build()
    d0: dict[str, Any] = {"control": control, "version": VERSION, "rungs": []}
    ref_ok = True
    if control == "ctlB":
        used, counts = ref_check(store, ledger, token=token)
        d0["ref_check"] = {"used": used, "truncated_counts": counts, "of": R_CAL}
        ref_ok = used is not None
    if ref_ok:
        for rung in RUNGS[control]:
            truth = rung.truth.build()
            diagnostics = menu_for(truth, 0)
            labels = [c.label for c in diagnostics]
            todo = cal_todo(rung, truth, member, menu, token=token)
            draw_runs(store, todo, diagnostics, ledger, "cal")
            cal = cal_runs(store, rung.index)
            boot = bootstrap_rung(
                rung, cal, labels, menu, diagnostics, ledger, token=token
            )
            randoms = {s: random_labels(s, labels) for s in SEEDS[control]}
            d0["rungs"].append(rung_values(rung, cal, boot, labels, randoms))
    code, tested, name = calibration_result(d0["rungs"], ref_ok)
    d0["result"] = {"code": code, "name": name}
    d0["tested_rung"] = tested
    return d0


def d0_bytes(d0: Mapping[str, Any], result: GateResult) -> bytes:
    """D0 with this attempt's status and records (no time, no O, no D)."""
    full = dict(d0)
    full["status"] = result.status
    full["records"] = result.record(score=False)
    return canonical(full).encode("ascii")


type Pusher = Callable[[Callable[[GateResult], bytes]], tuple[str, bytes, GateResult]]


def calibrate(
    paths: Paths, control: str, push: Pusher, ledger: Ledger, *, token: object
) -> int:
    """Compute D0 in memory, push D, then write D0 and print D (§5 "D").

    ``push`` is ctl_gate.push_d0 bound to this process's gate arguments.
    Nothing about any run reaches a file, stdout or stderr before it returns.
    """
    d0 = calibration_d0(control, ledger, token=token)
    ledger.check_stop()
    d, data, _ = push(lambda result: d0_bytes(d0, result))
    path = paths.drafts / f"D0-{control}.json"
    tmp = paths.drafts / f"D0-{control}.tmp.json"
    tmp.write_bytes(data)
    os.replace(tmp, path)
    print(f"D {d}", flush=True)
    return int(d0["result"]["code"])


# --------------------------------------------------------------------------
# Test phase (criteria §0 "Streams", §7)
# --------------------------------------------------------------------------


def decision_sides(control: str) -> tuple[str, str]:
    """(comparator, reference) side names of a decision's runs."""
    return ("member", "truth") if control == "ctlA1" else ("member", "ref")


def draw_todo(
    control: str,
    seed: int,
    truth: TruthSpec,
    member: TruthSpec,
    ref_e1: TruthSpec | None,
    menu: Sequence[Design],
    *,
    token: object,
) -> list[tuple[dict[str, Any], TruthSpec, Design, Callable[[], Any]]]:
    from sciagent.systems.v2.policies import PARAMETRIC_INDEX, REFERENCE_INDEX

    require_token(control, token)
    out: list[tuple[dict[str, Any], TruthSpec, Design, Callable[[], Any]]] = []
    for e, design in enumerate(menu):
        for side, model in enumerate((truth, member)):
            for r in range(R_TELLING):
                key = {"seed": seed, "arm": "telling", "design": e}
                key |= {"side": SIDE_NAMES[side], "replicate": r}

                def tseed(side: int = side, e: int = e, r: int = r) -> Any:
                    return telling_seed(control, seed, side, e, r, token=token)

                out.append((key, model, design, tseed))
    comp, ref = decision_sides(control)
    for k in range(DECISIONS):
        for e, design in enumerate(menu):
            reference = truth
            if ref == "ref" and e == E1:
                if ref_e1 is None:
                    raise ControlsError("B needs its reference model on e1")
                reference = ref_e1
            for name, model, index in (
                (comp, member, PARAMETRIC_INDEX),
                (ref, reference, REFERENCE_INDEX),
            ):
                for r in range(R_POLICY):
                    key = {"seed": seed, "arm": f"k{k}", "design": e}
                    key |= {"side": name, "replicate": r}

                    def pseed(k: int = k, index: int = index, r: int = r) -> Any:
                        return decision_seed(seed, k, index, r)

                    out.append((key, model, design, pseed))
    return out


def tested_models(
    control: str, d0: Mapping[str, Any]
) -> tuple[Rung, TruthSpec, TruthSpec, TruthSpec | None]:
    rung = RUNGS[control][int(d0["tested_rung"])]
    ref = None
    if control == "ctlB":
        ref = REF_MODELS[int(d0["ref_check"]["used"])].build()
    return rung, rung.truth.build(), MEMBER.build(), ref


def read_d0(paths: Paths, control: str) -> dict[str, Any]:
    d0: dict[str, Any] = json.loads(
        (paths.drafts / f"D0-{control}.json").read_text(encoding="ascii")
    )
    return d0


def runs_header(control: str, result: GateResult) -> dict[str, Any]:
    return {"control": control, "F": result.freeze, "D": result.d0_commit}


def draw_test(paths: Paths, control: str, result: GateResult, ledger: Ledger) -> int:
    d0 = read_d0(paths, control)
    code = int(d0["result"]["code"])
    if code in (EXIT_VOID, EXIT_NOT_TESTED):
        return code
    _, truth, member, ref = tested_models(control, d0)
    path = paths.drafts / f"runs-{control}.jsonl"
    store = RunStore(path, TEST_KEY, runs_header(control, result))
    store.load()
    menu = designs()
    for s in SEEDS[control]:
        todo = draw_todo(control, s, truth, member, ref, menu, token=result.token)
        if all(store.key(k) in store for k, _, _, _ in todo):
            continue
        diagnostics = menu_for(truth, s)
        draw_runs(store, todo, diagnostics, ledger, f"seed-{s}")
    return EXIT_DONE


# --------------------------------------------------------------------------
# Score (criteria §1, §2, §0 breaches, §7 determinism)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedRuns:
    """Telling's (truth, member) pairs, and per decision (comparator, reference)."""

    telling: Sequence[Pair]
    decisions: Sequence[Sequence[Pair]]


def seed_runs(records: Mapping[Key, Run], control: str, seed: int) -> SeedRuns:
    comp, ref = decision_sides(control)

    def side(arm: str, e: int, name: str, n: int) -> list[Run]:
        return [records[(seed, arm, e, name, r)] for r in range(n)]

    telling = [
        (
            side("telling", e, "truth", R_TELLING),
            side("telling", e, "member", R_TELLING),
        )
        for e in range(N_DESIGNS)
    ]
    decisions = [
        [
            (side(f"k{k}", e, comp, R_POLICY), side(f"k{k}", e, ref, R_POLICY))
            for e in range(N_DESIGNS)
        ]
        for k in range(DECISIONS)
    ]
    return SeedRuns(telling, decisions)


def expected_truncation(control: str, rung: Rung, arm: str, side: str, e: int) -> bool:
    """Where calibration had 256/256 truncation (else it had none)."""
    if side == "truth":
        return e == rung.planned
    if side == "ref":
        return e in (E1, rung.planned)
    return False


def p1_breaches(
    records: Mapping[Key, Run], control: str, rung: Rung
) -> dict[int, list[list[Any]]]:
    """Test-time P1 breaches by seed (criteria §0)."""
    out: dict[int, list[list[Any]]] = {}
    for key in sorted(records):
        run = records[key]
        seed, arm, e, side, r = key
        want = expected_truncation(control, rung, arm, side, e)
        got = run["outcome"]
        if (want and got not in ("truncated", "cut0")) or (
            not want and got != "complete"
        ):
            out.setdefault(int(seed), []).append([arm, e, side, r, got])
    return {s: out[s] for s in sorted(out)}


def separation(
    control: str, hits: Mapping[int, bool], random_hits: Mapping[int, bool]
) -> dict[str, Any]:
    from sciagent.scoring.controls_p4 import policy_separation

    v = policy_separation(
        [(control, s, hits[s]) for s in sorted(hits)],
        [(control, s, random_hits[s]) for s in sorted(random_hits)],
    )
    return {
        "passed": v.passed,
        "p_value": v.p_value,
        "units": v.units,
        "both": v.both,
        "arm_only": v.truth_only,
        "random_only": v.random_only,
        "neither": v.neither,
    }


def pick_record(pick: Pick, labels: Sequence[str]) -> list[Any] | None:
    return None if pick is None else [pick[0], pick[1], labels[pick[1]]]


@dataclass(frozen=True)
class Menus:
    designs: Sequence[Design]
    diagnostics: Mapping[int, Sequence[DiagnosticChoice]]
    labels: Mapping[int, Sequence[str]]


def telling_labels(
    k_rule: str, runs: Mapping[int, SeedRuns], menus: Menus
) -> dict[int, Pick]:
    out: dict[int, Pick] = {}
    for s in sorted(runs):
        scored = telling_scored(k_rule, runs[s].telling, len(menus.labels[s]))
        out[s] = telling_pick(scored, menus.designs, menus.diagnostics[s])
    return out


def score_a1(
    control: str, runs: Mapping[int, SeedRuns], menus: Menus, dstar0: Sequence[str]
) -> dict[str, Any]:
    """A1 criteria (i)-(iii) per K-rule (criteria §1)."""
    out: dict[str, Any] = {}
    for k_rule in K_RULES:
        tell = telling_labels(k_rule, runs, menus)
        per_seed: dict[str, Any] = {}
        i_count = ii_count = 0
        hits: dict[int, bool] = {}
        random_hits: dict[int, bool] = {}
        for s in sorted(runs):
            labels = menus.labels[s]
            t_label = label_of(tell[s], labels)
            x = k5_exclusion(runs[s].telling) if k_rule == "K5" else frozenset()
            picks = [
                policy_pick(truthfsd_scored(k_rule, pairs, len(labels), x))
                for pairs in runs[s].decisions
            ]
            p_labels = [label_of(p, labels) for p in picks]
            match = [t_label is not None and lab == t_label for lab in p_labels]
            i_count += t_label is not None and t_label in dstar0
            ii_count += sum(match)
            hits[s] = any(match)
            random_hits[s] = t_label is not None and t_label in random_labels(s, labels)
            per_seed[str(s)] = {
                "telling": pick_record(tell[s], labels),
                "truthfsd": [pick_record(p, labels) for p in picks],
                "matches": sum(match),
                "random_hit": random_hits[s],
            }
        sep = separation(control, hits, random_hits)
        out[k_rule] = {
            "seeds": per_seed,
            "i": {"count": i_count, "pass": i_count >= I_MIN},
            "ii": {"count": ii_count, "pass": ii_count >= II_MIN},
            "iii": sep,
            "pass": i_count >= I_MIN and ii_count >= II_MIN and sep["passed"],
        }
    return out


def score_b(
    control: str, runs: Mapping[int, SeedRuns], menus: Menus, dstar0: Sequence[str]
) -> dict[str, Any]:
    """B1-B3 per (F-rule, K-rule) pair (criteria §2)."""
    tell = {k: telling_labels(k, runs, menus) for k in K_RULES}
    fsd: dict[str, dict[int, list[Pick]]] = {}
    for f_rule in F_RULES:
        fsd[f_rule] = {
            s: [
                policy_pick(fsd_scored(f_rule, pairs, len(menus.labels[s])))
                for pairs in runs[s].decisions
            ]
            for s in sorted(runs)
        }
    telling_out: dict[str, Any] = {}
    for k in K_RULES:
        telling_out[k] = {
            "picks": {
                str(s): pick_record(tell[k][s], menus.labels[s])
                for s in sorted(tell[k])
            },
            "in_d_star0": sum(
                label_of(tell[k][s], menus.labels[s]) in dstar0 for s in tell[k]
            ),
        }
    fsd_out = {
        f: {
            str(s): [pick_record(p, menus.labels[s]) for p in fsd[f][s]]
            for s in sorted(fsd[f])
        }
        for f in F_RULES
    }
    pairs_out: dict[str, Any] = {}
    for f_rule in F_RULES:
        off_e1 = sum(
            p is not None and p[0] != E1 for s in fsd[f_rule] for p in fsd[f_rule][s]
        )
        for k_rule in K_RULES:
            credited: dict[int, bool] = {}
            random_hits: dict[int, bool] = {}
            only_e1: list[int] = []
            for s in sorted(runs):
                labels = menus.labels[s]
                t_label = label_of(tell[k_rule][s], labels)
                matched = [
                    p
                    for p in fsd[f_rule][s]
                    if t_label is not None and label_of(p, labels) == t_label
                ]
                credited[s] = bool(matched)
                if matched and all(p is not None and p[0] == E1 for p in matched):
                    only_e1.append(s)
                random_hits[s] = t_label is not None and t_label in random_labels(
                    s, labels
                )
            sep = separation(control, credited, random_hits)
            n_credited = sum(credited.values())
            b1 = off_e1 >= B1_MIN
            b2 = not only_e1
            b3 = n_credited >= B3_MIN and sep["passed"]
            pairs_out[f"{f_rule}|{k_rule}"] = {
                "B1": {"off_e1": off_e1, "pass": b1},
                "B2": {"credited_only_on_e1": only_e1, "pass": b2},
                "B3": {"credited": n_credited, "separation": sep, "pass": b3},
                "pass": b1 and b2 and b3,
            }
    return {"telling": telling_out, "fsd": fsd_out, "pairs": pairs_out}


def score_exit(determinism_ok: bool, breach_seeds: int) -> tuple[int, str]:
    """Determinism first, breaches second: 15 takes precedence over 14."""
    if not determinism_ok:
        return EXIT_NONDET, "VOID: nondeterministic"
    if breach_seeds > BREACH_MAX_SEEDS:
        return EXIT_BREACH, "VOID: test-time P1 breaches"
    return EXIT_DONE, "scored"


def determinism_rerun(
    control: str,
    rung: Rung,
    store: RunStore,
    truth: TruthSpec,
    member: TruthSpec,
    *,
    token: object,
) -> dict[str, Any]:
    """Seed 101/301's telling runs on the truth's truncating design, redrawn
    and compared byte for byte with the stored lines (criteria §7)."""
    require_token(control, token)
    seed = SEEDS[control][0]
    menu = designs()
    diagnostics = menu_for(truth, seed)
    e = rung.planned
    mismatches: list[dict[str, Any]] = []
    for side, model in enumerate((truth, member)):
        for r in range(R_TELLING):
            key = {"seed": seed, "arm": "telling", "design": e}
            key |= {"side": SIDE_NAMES[side], "replicate": r}
            rng = rng_of(telling_seed(control, seed, side, e, r, token=token))
            line = canonical(
                run_line(key, simulate_run(model, menu[e], rng, diagnostics))
            )
            stored = store.raw.get(store.key(key))
            if line.encode("ascii") != stored:
                mismatches.append(
                    {
                        "side": SIDE_NAMES[side],
                        "replicate": r,
                        "stored": None if stored is None else stored.decode("ascii"),
                        "redrawn": line,
                    }
                )
    return {
        "seed": seed,
        "design": e,
        "runs": 2 * R_TELLING,
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def render(score: Mapping[str, Any]) -> str:
    """A fixed-order text rendering of score-<C>.json."""
    lines = [
        f"control {score['control']}  status {score['status']}",
        f"result {score['result']['code']} {score['result']['name']}",
    ]
    rec = score["records"]
    for k in sorted(rec):
        lines.append(f"  {k}: {json.dumps(rec[k], sort_keys=True)}")
    for r in score.get("rungs", []):
        keys = ("p1", "p2", "clause99", "h", "pass_ii", "pass_iii", "h_f", "pass_iii_f")
        vals = " ".join(f"{k}={r[k]}" for k in keys if k in r)
        lines.append(f"rung {r['rung']} {r['reading']}: {vals} D*0={r['d_star0']}")
    if "tested_rung" in score:
        lines.append(f"tested rung {score['tested_rung']}")
    if "rules" in score:
        for k in K_RULES:
            x = score["rules"][k]
            lines.append(
                f"{k}: pass={x['pass']} (i) {x['i']['count']}/15 "
                f"(ii) {x['ii']['count']}/120 (iii) p={x['iii']['p_value']:.4g} "
                f"passed={x['iii']['passed']}"
            )
    if "pairs" in score:
        for name in sorted(score["pairs"]["pairs"]):
            x = score["pairs"]["pairs"][name]
            lines.append(
                f"{name}: pass={x['pass']} B1 {x['B1']['off_e1']}/120 "
                f"B2 only-e1 {x['B2']['credited_only_on_e1']} "
                f"B3 credited {x['B3']['credited']}/15 "
                f"p={x['B3']['separation']['p_value']:.4g}"
            )
    if "breaches" in score:
        lines.append(f"P1 breaches by seed: {json.dumps(score['breaches'])}")
    if "determinism" in score:
        lines.append(f"determinism: {json.dumps(score['determinism'], sort_keys=True)}")
    return "\n".join(lines) + "\n"


def write_score(paths: Paths, control: str, score: Mapping[str, Any]) -> None:
    base = paths.drafts / f"score-{control}"
    write_atomic(
        base.with_suffix(".json"), canonical(score), f"score-{control}.tmp.json"
    )
    write_atomic(base.with_suffix(".txt"), render(score), f"score-{control}.tmp.txt")


def score_phase(paths: Paths, control: str, result: GateResult, ledger: Ledger) -> int:
    """score: exits the terminal code after writing its files (§0)."""
    d0 = read_d0(paths, control)
    score: dict[str, Any] = {
        "control": control,
        "version": VERSION,
        "status": result.status,
        "records": result.record(score=True),
    }
    code = int(d0["result"]["code"])
    if code in (EXIT_VOID, EXIT_NOT_TESTED):
        score["rungs"] = d0["rungs"]
        if "ref_check" in d0:
            score["ref_check"] = d0["ref_check"]
        score["result"] = d0["result"]
        write_score(paths, control, score)
        return code
    rung, truth, member, ref = tested_models(control, d0)
    runs_path = paths.drafts / f"runs-{control}.jsonl"
    store = RunStore(runs_path, TEST_KEY, runs_header(control, result))
    if not runs_path.exists():  # test never ran: INCOMPLETE (11), as unfinished
        score["result"] = {"code": EXIT_STOP, "name": "INCOMPLETE: no runs file"}
        write_score(paths, control, score)
        return EXIT_STOP
    store.load()
    menu = designs()
    expected = [
        store.key(k)
        for s in SEEDS[control]
        for k, _, _, _ in draw_todo(
            control, s, truth, member, ref, menu, token=result.token
        )
    ]
    missing = [k for k in expected if k not in store]
    score["records"]["runs_sha256"] = sha256_file(runs_path)
    if missing or len(store.records) != len(expected):
        score["result"] = {
            "code": EXIT_STOP,
            "name": f"INCOMPLETE: {len(missing)} runs missing",
        }
        write_score(paths, control, score)
        return EXIT_STOP
    score["tested_rung"] = rung.index
    determinism = determinism_rerun(
        control, rung, store, truth, member, token=result.token
    )
    ledger.charge("score-determinism")
    breaches = p1_breaches(store.records, control, rung)
    code, name = score_exit(determinism["ok"], len(breaches))
    score["result"] = {"code": code, "name": name}
    if code == EXIT_NONDET:
        score["determinism"] = determinism  # the evidence only; no rule scored
        write_score(paths, control, score)
        return code
    score["breaches"] = {str(s): v for s, v in breaches.items()}
    if code == EXIT_BREACH:
        write_score(paths, control, score)  # the evidence only; no rule scored
        return code
    score["determinism"] = determinism
    score["rungs"] = d0["rungs"]
    diagnostics = {s: menu_for(truth, s) for s in SEEDS[control]}
    menus = Menus(
        menu, diagnostics, {s: [c.label for c in v] for s, v in diagnostics.items()}
    )
    ledger.charge("score-menus")
    runs = {s: seed_runs(store.records, control, s) for s in SEEDS[control]}
    dstar0 = d0["rungs"][rung.index]["d_star0"]
    if control == "ctlA1":
        score["rules"] = score_a1(control, runs, menus, dstar0)
    else:
        score["pairs"] = score_b(control, runs, menus, dstar0)
    ledger.charge("score-rules")
    write_score(paths, control, score)
    return code


# --------------------------------------------------------------------------
# Pre-F determinism unit ("ctl-det", criteria §7)
# --------------------------------------------------------------------------


def determinism_unit(out: Path, replicates: int = 3) -> None:
    """A real-simulator unit on the probe-only id ``ctl-det``: A1 rung 0's
    truth and member on every design, then every rule's table and pick."""
    from sciagent.scoring.seeds import TELLING, scorer_seed

    rung = RUNGS["ctlA1"][0]
    truth, member = rung.truth.build(), MEMBER.build()
    menu = designs()
    diagnostics = menu_for(truth, 0)
    labels = [c.label for c in diagnostics]
    lines: list[str] = []
    pairs: list[Pair] = []
    for e, design in enumerate(menu):
        sides: list[list[Run]] = []
        for side, model in enumerate((truth, member)):
            rs: list[Run] = []
            for r in range(replicates):
                rng = rng_of(scorer_seed(TELLING, "ctl-det", 0, side, e, r))
                key = {"seed": 0, "arm": "telling", "design": e}
                key |= {"side": SIDE_NAMES[side], "replicate": r}
                line = run_line(key, simulate_run(model, design, rng, diagnostics))
                lines.append(canonical(line))
                rs.append(line)
            sides.append(rs)
        pairs.append((sides[0], sides[1]))
    for k_rule in K_RULES:
        scored = telling_scored(k_rule, pairs, len(labels))
        pick = telling_pick(scored, menu, diagnostics)
        x = k5_exclusion(pairs)
        flipped = [(b, a) for a, b in pairs]
        tf = policy_pick(truthfsd_scored(k_rule, flipped, len(labels), x))
        lines.append(
            canonical(
                {
                    "rule": k_rule,
                    "table": [list(r) for r in scored.table],
                    "excluded": sorted(scored.excluded),
                    "telling": pick_record(pick, labels),
                    "truthfsd": pick_record(tf, labels),
                }
            )
        )
    for f_rule in F_RULES:
        scored = fsd_scored(f_rule, [(b, a) for a, b in pairs], len(labels))
        lines.append(
            canonical(
                {
                    "rule": f_rule,
                    "table": [list(r) for r in scored.table],
                    "fsd": pick_record(policy_pick(scored), labels),
                }
            )
        )
    out.write_text("".join(lines), encoding="ascii")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--freeze-commit", required=True)
    p.add_argument("--d0-commit")
    p.add_argument("--control", choices=sorted(CONTROLS), required=True)
    p.add_argument("--phase", choices=PHASES, required=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:  # -I leaves the script's directory out
        sys.path.append(str(here))
    import ctl_gate
    from ctl_gate import GateError

    control = CONTROLS[args.control]
    paths = ctl_gate.Paths(REPO, DRAFTS_REL, WORKTREE, WORKTREE_HEAD)
    try:
        if Path(ctl_gate.__file__).resolve().parent != here:
            raise GateError("imports", f"ctl_gate from {ctl_gate.__file__}")
        if args.worktree.resolve() != WORKTREE.resolve():
            raise GateError("worktree", f"--worktree must be {WORKTREE}")
        ctl_gate.check_runtime(os.environ, Path(__file__), paths)
        result = ctl_gate.gate(
            paths,
            args.phase,
            control,
            args.freeze_commit,
            args.d0_commit,
            os.environ,
            criteria_constant=CRITERIA_SHA256,
        )
        ledger = Ledger(
            paths.drafts,
            args.phase,
            control,
            result.ceiling_h * 3600.0,
            exempt=args.phase == "score",
            coarse=args.phase == "calibrate",
        )
        ledger.start()
    except (GateError, LedgerError) as error:
        print(error, file=sys.stderr)
        return EXIT_GATE

    def push(
        make_bytes: Callable[[GateResult], bytes],
    ) -> tuple[str, bytes, GateResult]:
        return ctl_gate.push_d0(
            paths,
            control,
            args.freeze_commit,
            os.environ,
            make_bytes,
            criteria_constant=CRITERIA_SHA256,
        )

    def phase() -> int:
        ledger.check_stop()
        if args.phase == "calibrate":
            return calibrate(paths, control, push, ledger, token=result.token)
        if args.phase == "test":
            return draw_test(paths, control, result, ledger)
        return score_phase(paths, control, result, ledger)

    try:
        return execute(phase, ledger)
    except (GateError, HeaderError) as error:
        print(error, file=sys.stderr)
        try:  # charged, then refused (10); calibrate wrote nothing
            ledger.at_exit()
        except LedgerError as second:
            print(second, file=sys.stderr)
        return EXIT_GATE
    except LedgerError as error:
        print(error, file=sys.stderr)
        return EXIT_GATE


if __name__ == "__main__":
    sys.exit(main())
