"""``scripts/p4_search_misses.py``: the search-miss check on P4 (SPEC §6.3 instrument).

The rule is ``docs/v2/prereg/2026-11-05_p4-search-misses.md``, which amends
``docs/v2/prereg/2026-11-04_pilot-search-misses.md``; its section 2.10 lists what
these tests must cover. No test here touches a P4 unit. Every table, store and log
is a toy.

**Streams.** 20261110, 20261111 and 20261112 are the launch's and run once, so no
test draws them. The script's roots are always passed in, every test passes
:data:`ROOTS`, and :func:`no_frozen_stream` fails any test that reaches numpy with
a frozen root, in a forked job too. That is a tripwire of this file's own: no
guard is removed or bypassed for a test.

**Toy world.** Six truths on two seeds, every value a multiple of 1/16, as in
``test_contrasts.py``: ORACLE -0.5, B-lib -0.6875, B-rand -0.75, planted-hint -0.5,
B-sym@10F (seed 1) -0.5, B-sym, B-sparse and the arms -0.625, so controls 1 to 3
pass and every read starts at 0.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib
import inspect
import json
import math
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from functools import cache
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import numpy as np
import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import analysis, contrasts
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.contrasts import ContrastRead, ScriptedControls
from sciagent.campaign.plan import baseline_key, baseline_units
from sciagent.campaign.store import ResultStore
from sciagent.campaign.voids import VoidStatus
from sciagent.glm.canonical import canonicalise
from sciagent.glm.data import Dataset
from sciagent.glm.fit import FitConfig, _ascend, _Profile, _search_blocks, _Slots, fit
from sciagent.glm.grammar import (
    ALL,
    Excite,
    Feature,
    Gate,
    KernelKind,
    Link,
    One,
    Periodic,
    PhaseWindow,
    Structure,
    psi_slots,
)
from sciagent.glm.grids import grid
from sciagent.glm.simulate import Coefficients, simulate
from sciagent.glm.syntax import parse, render
from sciagent.systems.v2.models import GLMModel
from sciagent.systems.v2.sparse import SparseConfig, _choose_link, split_dataset

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
p4m: Any = importlib.import_module("p4_search_misses")
record_module: Any = importlib.import_module("naming_null_record")

#: Every R1, hunt log and read in this file runs under these. Not the launch's.
TEST_ROOT: Final = 20261107
TEST_LOG_ROOT: Final = 20261108
TEST_READS_ROOT: Final = 20261109
FROZEN_STREAMS: Final = frozenset({20261110, 20261111, 20261112})
ROOTS: Final = p4m.Roots(TEST_ROOT, TEST_LOG_ROOT, TEST_READS_ROOT)

TRUTHS: Final = ("t1", "t2", "t3", "t4", "t5", "t6")
OUT_OF_DICTIONARY: Final = ("t1", "t2")
SEEDS: Final = (1, 2)

AGC: Final = "AG-c-S/named+link-neutral"
AGC_RESCALED: Final = "AG-c-S/rescaled+link-neutral"
AGC_ANON: Final = "AG-c-S/anon+link-neutral"
AGO: Final = "AG-o-S/named+link-neutral"
AGO_RESCALED: Final = "AG-o-S/rescaled+link-neutral"
AGO_ANON: Final = "AG-o-S/anon+link-neutral"
ARMS: Final = (AGC, AGC_RESCALED, AGC_ANON, AGO, AGO_RESCALED, AGO_ANON)
HAIKU: Final = "AG-c-H/named+link-neutral"
ORACLE: Final = "ORACLE"
B_LIB: Final = "B-lib"
B_NP: Final = "B-np"
B_RAND: Final = "B-rand"
B_SYM: Final = "B-sym"
B_SPARSE: Final = "B-sparse"
PLANTED: Final = "planted-hint"
B_SYM_10F: Final = "B-sym@10F"

PASS: Final = ScriptedControls(naming_null=True)
READ_NAMES: Final = (
    "C1",
    "C2-AG-c-names",
    "C2-AG-c-time",
    "C2-AG-o-names",
    "C2-AG-o-time",
    "C3-free",
    "C4",
)
IDENTITY: Final = {
    "F2": "f" * 40,
    "R2": "a" * 40,
    "S": "c" * 40,
    "T": "d" * 40,
    "prereg_sha256": "b" * 64,
}
#: A stored reading, as a byte failure's reason would carry it.
SCORE_TEXT: Final = "-0x1.4051a13cc7023p-1"

type Key = tuple[str, int, str]
type Rec = dict[str, Any]


#: Ids the tripwire watches beside the frozen roots: its own test uses one, so
#: that a frozen root is never handed to numpy, even to be refused (INBOX 119eff3).
WATCHED: Final[set[int]] = set()


def holds_frozen(value: Any) -> bool:
    if isinstance(value, list | tuple):
        return any(holds_frozen(v) for v in value)
    return isinstance(value, int) and (value in FROZEN_STREAMS or value in WATCHED)


@pytest.fixture(autouse=True)
def no_frozen_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A draw on a frozen root fails the test (module docstring)."""
    real_sequence = np.random.SeedSequence
    real_rng = np.random.default_rng

    def seed_sequence(entropy: Any = None, **kw: Any) -> Any:
        assert not holds_frozen(entropy), f"frozen stream drawn: {entropy}"
        return real_sequence(entropy, **kw)

    def default_rng(seed: Any = None) -> np.random.Generator:
        assert not holds_frozen(seed), f"frozen stream drawn: {seed}"
        return real_rng(seed)

    monkeypatch.setattr(np.random, "SeedSequence", seed_sequence)
    monkeypatch.setattr(np.random, "default_rng", default_rng)


def test_the_tripwire_holds_each_frozen_root_by_membership_alone() -> None:
    """Set membership, with nothing handed to numpy."""
    assert all(holds_frozen(root) for root in sorted(FROZEN_STREAMS))
    assert all(holds_frozen([root, 7]) for root in sorted(FROZEN_STREAMS))
    assert not holds_frozen([TEST_ROOT, TEST_LOG_ROOT, TEST_READS_ROOT])


def test_the_tripwire_fires_on_a_watched_id_and_on_nothing_else() -> None:
    """A non-control id stands in for the frozen roots."""
    stand_in = 11
    WATCHED.add(stand_in)
    try:
        with pytest.raises(AssertionError, match="frozen stream"):
            p4m.reference_rng("toy", stand_in)
    finally:
        WATCHED.discard(stand_in)
    assert p4m.reference_rng("toy", TEST_ROOT).integers(1 << 30) >= 0


# --------------------------------------------------------------------------
# Constants fixed by the text
# --------------------------------------------------------------------------


def test_the_test_roots_are_not_the_launchs_roots() -> None:
    assert p4m.SEED_ROOT == 20261111
    assert p4m.HUNT_LOG_ROOT == 20261112
    assert p4m.READS_ROOT == 20261110
    launch = {p4m.SEED_ROOT, p4m.HUNT_LOG_ROOT, p4m.READS_ROOT}
    assert {TEST_ROOT, TEST_LOG_ROOT, TEST_READS_ROOT}.isdisjoint(launch)
    assert launch == FROZEN_STREAMS
    assert ROOTS == p4m.REHEARSAL_ROOTS
    assert p4m.Roots(20261111, 20261112, 20261110) == p4m.FROZEN_ROOTS


def test_the_freeze_constants_are_f2s() -> None:
    assert p4m.F2_COMMIT.startswith("9031943")
    assert len(p4m.F2_COMMIT) == 40
    assert p4m.PREREG_SHA256 == (
        "4fc674dc4fbcd782dce8e9b96726ff5a2074b89848805b302d6e82f732025188"
    )
    assert p4m.PREREG_PATH == "docs/v2/prereg/2026-11-05_p4-search-misses.md"


def test_the_limits_are_section_8s() -> None:
    assert p4m.CPU_STOP_HOURS == 172
    assert p4m.CPU_CEILING_HOURS == 178
    assert p4m.JOB_CPU_HOURS == 3
    assert p4m.WALL_HOURS == 120
    assert p4m.READS_RESAMPLES == 10_000


def test_only_killed_and_terminated_can_resume() -> None:
    assert set(p4m.RESUMABLE_ENDS) == {"killed", "terminated"}
    assert set(p4m.TERMINAL_ENDS) == {"complete", "cpu_stop", "wall_limit", "stop"}


def test_the_labels_and_exit_codes_are_section_5s() -> None:
    assert p4m.EXIT_CODES == {
        "BYTE_FAIL": 3,
        "CONTRASTS_CHANGE": 2,
        "INCOMPLETE": 5,
        "CONTROL_FAIL": 4,
        "CONTRASTS_SURVIVE_FOUND_MISSES": 0,
    }
    assert set(p4m.NEXT) == set(p4m.EXIT_CODES)
    assert p4m.NO_LABEL_EXIT not in p4m.EXIT_CODES.values()


# --------------------------------------------------------------------------
# Toy tables and planted records
# --------------------------------------------------------------------------


def row(truth: str, seed: int, system: str, per_event: float) -> Row:
    return Row(
        truth=truth,
        seed=seed,
        system=system,
        llm=system.startswith("AG-"),
        ok=True,
        submitted=True,
        per_event=per_event,
        exact=False,
        distance=0.5,
        has_structure=True,
        similarity=0.9,
        fits_used=1,
        structure=None,
        curve=(),
        error=None,
        oracle_underfit=False if system == ORACLE else None,
    )


def world() -> dict[Key, Row]:
    values = {
        ORACLE: -0.5,
        B_LIB: -0.6875,
        B_RAND: -0.75,
        PLANTED: -0.5,
        B_SYM: -0.625,
        B_SPARSE: -0.625,
        B_NP: -2.0,
        HAIKU: -3.0,
        **dict.fromkeys(ARMS, -0.625),
    }
    rows: dict[Key, Row] = {}
    for t in TRUTHS:
        for seed in SEEDS:
            for system, value in values.items():
                rows[(t, seed, system)] = row(t, seed, system, value)
        rows[(t, 1, B_SYM_10F)] = row(t, 1, B_SYM_10F, -0.5)
    return rows


def tabled(rows: dict[Key, Row] | None = None) -> Table:
    given = world() if rows is None else rows
    truths = {
        t: replace(entry("hawkes"), id=t, in_dictionary=t not in OUT_OF_DICTIONARY)
        for t in TRUTHS
    }
    return Table(truths, tuple(derive(list(given.values()))), (), {})


def plan_rows(table: Table) -> tuple[Key, ...]:
    return tuple(
        sorted(
            (r.truth, r.seed, r.system)
            for r in table.rows
            if r.system in contrasts.CONTRAST_SYSTEMS
        )
    )


def info(r: Row, klass: str, text: str | None) -> Any:
    size = {"coordinate": 1000, "exhaustive": 100}.get(klass)
    return p4m.RowInfo(
        truth=r.truth,
        seed=r.seed,
        system=r.system,
        llm=r.llm,
        klass=klass,
        text=text,
        grid_size=size,
        link="exp" if text else None,
        certified=klass != "exhaustive_uncertified",
        expect=p4m.RowExpect(r.system, r.llm, "", None),
    )


def planted_census(table: Table, *, uncertified: bool = False) -> Any:
    rows = []
    for r in table.rows:
        if r.system == B_NP:
            rows.append(info(r, "no_search_np", None))
        elif r.system == B_LIB:
            klass = "exhaustive_uncertified" if uncertified else "exhaustive"
            rows.append(info(r, klass, f"{r.system} {r.truth} {r.seed}"))
        else:
            rows.append(info(r, "coordinate", f"{r.system} {r.truth} {r.seed}"))
    return p4m.Census(tuple(rows), frozenset(plan_rows(table)))


def row_result(
    system: str,
    *,
    kind: str = "none",
    llm: bool = False,
    corrected: float | None = None,
    stored: float = -0.625,
    underfit: bool | None = None,
    gain: float = 0.0,
) -> Rec:
    delta, delta_kind = (None, None)
    if corrected is not None:
        delta, delta_kind = p4m.held_out_delta(stored, corrected)
    return {
        "system": system,
        "llm": llm,
        "call": kind,
        "gain": gain,
        "tol": 3e-7,
        "stored_uncertified": False,
        "uncertified_better": 0,
        "stored_ll": -100.0,
        "stored_gap": 1e-9,
        "stored_certified": True,
        "chosen_ll": -100.0 + gain,
        "stored_per_event": stored,
        "corrected_per_event": corrected,
        "delta": delta,
        "delta_kind": delta_kind,
        "corrected_underfit": underfit,
        "corrected_similarity": None,
        "oracle": None,
        "stored_quadrature_error": 1e-6,
        "chosen_quadrature_error": 1e-6,
        "within_quadrature": False,
    }


def miss(key: Key, corrected: float, stored: float = -0.625) -> dict[Key, Rec]:
    """A reading miss on ``key``."""
    system = key[2]
    return {
        key: row_result(
            system,
            kind="miss",
            llm=system.startswith("AG-"),
            corrected=corrected,
            stored=stored,
            gain=4.0,
        )
    }


def done_records(
    census: Any,
    *,
    evidence: str = "searched",
    rows: dict[Key, Rec] | None = None,
    skip: Callable[[Any], bool] = lambda f: False,
) -> list[Rec]:
    rows = rows or {}
    out: list[Rec] = []
    for f in census.fits():
        if skip(f):
            continue
        job = p4m.fit_job_id(f.truth, f.seed, f.text)
        results = [
            rows.get((f.truth, f.seed, r.system), row_result(r.system, llm=r.llm))
            for r in f.rows
        ]
        out.append(
            {
                "type": "r1",
                "job": job,
                "best": {"ll": -100.0, "gap": 1e-9, "point": [0], "score": -100.0},
                "capped": False,
                "n_points": 10,
            }
        )
        out.append(
            {
                "type": "done",
                "job": job,
                "kind": "fit",
                "truth": f.truth,
                "seed": f.seed,
                "text": f.text,
                "evidence": evidence,
                "capped": False,
                "cpu_cut": False,
                "starts_completed": 48,
                "r2_complete": evidence == "exact",
                "grid_size": 1000 if evidence == "exact" else 20_000,
                "link": "exp",
                "n_uncertified": 0,
                "rows": results,
            }
        )
    return out


def control_records(*, a: int = 6, b: bool = True, d: bool = True) -> list[Rec]:
    """Group A with ``a`` caught, then B and D. The chosen value is always the
    known one, so a control that read it would always pass."""
    known = {"ll": -100.0, "gap": 1e-9, "certified": True}
    fit5 = {"ll": -105.0, "gap": 1e-9, "certified": True}
    out: list[Rec] = []

    def r1(job: str, caught: bool) -> None:
        ll = -100.0 if caught else -103.0
        out.append(
            {
                "type": "r1",
                "job": job,
                "best": {"ll": ll, "gap": 1e-9, "point": [0], "score": ll},
                "capped": False,
                "n_points": 10,
            }
        )

    for i, case in enumerate(p4m.T2_CASES):
        job = f"A{i}"
        r1(job, i < a)
        out.append(
            {
                "type": "done",
                "job": job,
                "kind": "control",
                "group": "A",
                "case": list(case),
                "fit5": fit5,
                "known": known,
                "chosen": known,
            }
        )
    r1("B", b)
    out.append(
        {
            "type": "done",
            "job": "B",
            "kind": "fit",
            "truth": p4m.GROUP_B[0],
            "seed": p4m.GROUP_B[1],
            "text": "group B",
            "r2_complete": False,
            "grid_size": 20_736,
            "capped": False,
            "rows": [],
            "control": {
                "group": "B",
                "case": list(p4m.GROUP_B),
                "fit5": fit5,
                "known": known,
            },
        }
    )
    best = known if d else {"ll": -103.0, "gap": 1e-9, "certified": True}
    out.append(
        {
            "type": "done",
            "job": "D",
            "kind": "control",
            "group": "D",
            "case": list(p4m.T2_CASES[0]),
            "fit5": fit5,
            "known": known,
            "r2_best": best,
            "chosen": known,
        }
    )
    return out


def inputs_of(
    table: Table | None = None,
    *,
    census: Any = None,
    controls: ScriptedControls = PASS,
    **over: Any,
) -> Any:
    table = tabled() if table is None else table
    options: dict[str, Any] = {
        "census": census or planted_census(table),
        "table": table,
        "plan_rows": plan_rows(table),
        "controls": controls,
        "roots": ROOTS,
        "resamples": 200,
        **over,
    }
    return p4m.Inputs(**options)


def clean(table: Table, **kw: Any) -> list[Rec]:
    rows = kw.pop("rows", None)
    return [
        *control_records(**kw),
        *done_records(planted_census(table), rows=rows),
    ]


def plant(paths: Any, records: Sequence[Rec], end: str | None = "complete") -> None:
    """Records left by an earlier unit, with that unit's ``cpu.json``."""
    paths.root.mkdir(parents=True, exist_ok=True)
    p4m.CpuLedger.begin(paths.cpu)
    journal = p4m.Journal(paths.journal)
    journal.append({"type": "run_start"})
    for record in records:
        journal.append(record)
    if end is not None:
        journal.append({"type": "run_end", "reason": end})


def finalise(paths: Any, inputs: Any = None, **over: Any) -> Any:
    options: dict[str, Any] = {
        "identity": IDENTITY,
        "manifest_failures": (),
        "end_flag": False,
        **over,
    }
    return p4m.finalise(paths, inputs or inputs_of(), **options)


def decision_of(paths: Any) -> Rec:
    body: Rec = json.loads(paths.decision.read_text(encoding="utf-8"))
    return body


def reads_of(paths: Any) -> Rec:
    body: Rec = json.loads(paths.reads.read_text(encoding="utf-8"))
    return body


def stamp(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.fixture
def paths(tmp_path: Path) -> Any:
    out = p4m.OutPaths(tmp_path / "out")
    out.root.mkdir()
    return out


@pytest.fixture
def spy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every read function leaves a line in a file, so a forked driver's call
    shows too. The real functions still run."""
    marker = tmp_path / "reads-called"

    def wrap(name: str, module: Any) -> None:
        real = getattr(module, name)

        def called(*args: Any, **kwargs: Any) -> Any:
            with marker.open("a") as f:
                f.write(f"{name}\n")
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, called)

    for name in ("read_contrasts", "read_floors", "read_controls"):
        wrap(name, contrasts)
    wrap("verdict", analysis)
    yield marker


# --------------------------------------------------------------------------
# The toy world itself
# --------------------------------------------------------------------------


def test_the_toy_world_passes_its_controls_and_every_read_starts_at_zero() -> None:
    table = tabled()
    reads = contrasts.read_contrasts(table, PASS, TEST_READS_ROOT, 200)
    assert list(reads) == list(READ_NAMES)
    assert all(r.reportable and r.point == 0.0 for r in reads.values())


# --------------------------------------------------------------------------
# Section 5: the label
# --------------------------------------------------------------------------


def label(**over: bool) -> str:
    options = {
        "byte_fail": False,
        "changed": False,
        "tier1_complete": True,
        "h1": True,
        "any_reportable": True,
        **over,
    }
    result: str = p4m.decide_label(**options)
    return result


def test_rule_6_survival_needs_every_condition() -> None:
    assert label() == "CONTRASTS_SURVIVE_FOUND_MISSES"


def test_rule_1_byte_fail_comes_first() -> None:
    assert label(byte_fail=True, changed=True, h1=False) == "BYTE_FAIL"


def test_rule_2_a_change_outranks_every_shortfall_and_does_not_need_h1() -> None:
    got = label(changed=True, tier1_complete=False, h1=False, any_reportable=False)
    assert got == "CONTRASTS_CHANGE"


def test_rule_3_incomplete_outranks_control_fail() -> None:
    assert label(tier1_complete=False, h1=False) == "INCOMPLETE"


def test_rule_4_h1_falsified_is_control_fail() -> None:
    assert label(h1=False) == "CONTROL_FAIL"


def test_rule_5_no_read_reportable_on_both_tables_is_control_fail() -> None:
    assert label(any_reportable=False) == "CONTROL_FAIL"


# --------------------------------------------------------------------------
# The ends: decision.json, reads.json and the terminal step (sections 2.8, 5)
# --------------------------------------------------------------------------


def test_a_complete_end_writes_the_reads_and_the_label(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table))
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert d["exit_code"] == 0
    assert d["end"] == "complete"
    assert decision_of(paths)["label"] == d["label"]
    assert set(reads_of(paths)["stored"]) == set(READ_NAMES)
    assert set(reads_of(paths)["fixed"]) == set(READ_NAMES)
    assert paths.units.exists()


@pytest.mark.parametrize("end", ["complete", "cpu_stop", "wall_limit", "stop"])
def test_each_terminal_end_reaches_the_terminal_step_with_nothing_chosen(
    paths: Any, end: str
) -> None:
    table = tabled()
    plant(paths, clean(table), end=end)
    d = finalise(paths, inputs_of(table))
    assert d["end"] == end
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert paths.reads.exists()


@pytest.mark.parametrize("end", ["cpu_stop", "wall_limit", "stop"])
def test_a_stop_inside_tier_1_is_incomplete_and_partial(paths: Any, end: str) -> None:
    table = tabled()
    census = planted_census(table)
    records = [
        *control_records(),
        *done_records(census, skip=lambda f: f.truth == "t1"),
    ]
    plant(paths, records, end=end)
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "INCOMPLETE"
    assert d["partial"] is True
    assert paths.reads.exists()


def test_a_stop_after_tier_1_does_not_alter_the_label(paths: Any) -> None:
    table = tabled()
    census = planted_census(table)
    records = [
        *control_records(),
        *done_records(census, skip=lambda f: f.tier == 2),
    ]
    plant(paths, records, end="cpu_stop")
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert d["partial"] is False
    assert d["shortfall"]["tier2_missing"] > 0


def test_a_complete_end_with_a_reference_failed_tier_1_fit_is_incomplete(
    paths: Any,
) -> None:
    table = tabled()
    census = planted_census(table)
    lost = next(f for f in census.fits() if f.tier == 1)
    job = p4m.fit_job_id(lost.truth, lost.seed, lost.text)
    records = [
        *control_records(),
        *done_records(census, skip=lambda f: f == lost),
        {"type": "reference_failed", "job": job, "reason": "cpu_limit"},
    ]
    plant(paths, records)
    assert finalise(paths, inputs_of(table))["label"] == "INCOMPLETE"


def test_a_changed_read_outranks_a_short_tier_1_and_says_partial(paths: Any) -> None:
    table = tabled()
    census = planted_census(table)
    shift = {
        (t, s, AGC): row_result(AGC, kind="miss", llm=True, corrected=-0.5, gain=4.0)
        for t in TRUTHS
        for s in SEEDS
    }
    records = [
        *control_records(),
        *done_records(
            census, rows=shift, skip=lambda f: f.tier == 2 or f.truth == "t6"
        ),
    ]
    plant(paths, records, end="cpu_stop")
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "CONTRASTS_CHANGE"
    assert d["partial"] is True


def test_the_stop_note_is_copied_and_an_empty_one_says_no_reason(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table), end="stop")
    paths.stop.write_text("")
    assert finalise(paths, inputs_of(table))["stop_note"] == "no reason was given"


def test_a_stop_file_alone_counts_as_a_stop_after_a_kill(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    paths.stop.write_text("control room: stopped for the night\n")
    d = finalise(paths, inputs_of(table))
    assert d["end"] == "stop"
    assert d["stop_note"] == "control room: stopped for the night"


def test_bug_writes_no_read_and_no_label_and_carries_the_end_alone(
    paths: Any, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table), end="bug")
    d = finalise(paths, inputs_of(table))
    assert d["end"] == "bug"
    assert "label" not in d
    assert "exit_code" not in d
    assert set(d) - set(IDENTITY) == {"end"}
    assert decision_of(paths) == json.loads(p4m.dumps_record(d))
    assert not paths.reads.exists()
    assert not spy.exists()


def test_a_bug_with_a_miss_on_record_still_reads_nothing(paths: Any, spy: Path) -> None:
    table = tabled()
    rows = miss(("t1", 1, AGC), -0.5)
    plant(paths, clean(table, rows=rows), end="bug")
    d = finalise(paths, inputs_of(table))
    assert "label" not in d
    assert not paths.reads.exists()
    assert not spy.exists()


@pytest.mark.parametrize("end", ["complete", "byte_fail", "killed", "stop"])
def test_a_byte_failure_on_record_is_byte_fail_and_writes_no_read(
    paths: Any, spy: Path, end: str
) -> None:
    table = tabled()
    fault = {"type": "byte_fail", "job": "x", "reason": f"per_event {SCORE_TEXT}"}
    plant(paths, [*clean(table), fault], end=None if end == "killed" else end)
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "BYTE_FAIL"
    assert d["exit_code"] == 3
    assert not paths.reads.exists()
    assert not spy.exists()


def test_byte_fail_writes_no_score_into_the_decision(paths: Any) -> None:
    table = tabled()
    fault = {"type": "byte_fail", "job": "x", "reason": f"per_event {SCORE_TEXT}"}
    plant(paths, [*clean(table), fault], end="byte_fail")
    finalise(paths, inputs_of(table))
    assert SCORE_TEXT not in paths.decision.read_text(encoding="utf-8")
    assert decision_of(paths)["byte_failures"] == ["byte_check"]


def test_a_manifest_difference_is_byte_fail_and_writes_no_read(
    paths: Any, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table))
    d = finalise(paths, inputs_of(table), manifest_failures=("changed a.json",))
    assert d["label"] == "BYTE_FAIL"
    assert d["byte_failures"] == ["store_manifest"]
    assert not paths.reads.exists()
    assert not spy.exists()


def test_two_different_records_for_a_job_are_byte_fail_and_write_no_read(
    paths: Any,
) -> None:
    table = tabled()
    records = clean(table)
    done = next(r for r in records if r["type"] == "done" and r["kind"] == "fit")
    plant(paths, [*records, {**done, "capped": True}])
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "BYTE_FAIL"
    assert d["byte_failures"] == ["units"]
    assert not paths.reads.exists()


def test_a_second_finalise_rewrites_neither_file(paths: Any, spy: Path) -> None:
    table = tabled()
    plant(paths, clean(table))
    finalise(paths, inputs_of(table))
    before = stamp(paths.reads), stamp(paths.decision), stamp(paths.units)
    calls = spy.read_text()
    rows = miss(("t1", 1, AGC), -0.5)
    p4m.Journal(paths.journal).append(done_records(planted_census(table), rows=rows)[1])
    again = finalise(paths, inputs_of(table))
    assert again["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert (stamp(paths.reads), stamp(paths.decision), stamp(paths.units)) == before
    assert spy.read_text() == calls


@pytest.mark.parametrize("end", ["bug", "byte_fail"])
def test_a_second_finalise_after_a_fault_rewrites_nothing(paths: Any, end: str) -> None:
    table = tabled()
    extra = [{"type": "byte_fail", "job": "x", "reason": "x"}] if end != "bug" else []
    plant(paths, [*clean(table), *extra], end=end)
    finalise(paths, inputs_of(table))
    before = stamp(paths.decision)
    finalise(paths, inputs_of(table))
    assert stamp(paths.decision) == before
    assert not paths.reads.exists()


def test_reads_land_before_the_decision(
    paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = tabled()
    plant(paths, clean(table))
    order: list[str] = []
    real = p4m.publish

    def spying(temporary: Path, path: Path) -> None:
        assert temporary.exists() and temporary != path
        order.append(path.name)
        real(temporary, path)

    monkeypatch.setattr(p4m, "publish", spying)
    finalise(paths, inputs_of(table))
    assert order == ["reads.json", "decision.json"]


def test_a_crash_between_the_renames_is_finished_without_rewriting_the_reads(
    paths: Any, monkeypatch: pytest.MonkeyPatch, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table))
    real = p4m.publish

    def dies(temporary: Path, path: Path) -> None:
        if path == paths.decision:
            raise KeyboardInterrupt
        real(temporary, path)

    monkeypatch.setattr(p4m, "publish", dies)
    with pytest.raises(KeyboardInterrupt):
        finalise(paths, inputs_of(table))
    monkeypatch.undo()
    assert paths.reads.exists() and not paths.decision.exists()
    before = stamp(paths.reads)
    calls = spy.read_text()
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert decision_of(paths)["label"] == d["label"]
    assert stamp(paths.reads) == before
    assert spy.read_text() == calls


def test_reads_with_no_decision_and_no_temporary_is_refused(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table))
    paths.reads.write_text("{}\n")
    with pytest.raises(p4m.ResumeRefused):
        finalise(paths, inputs_of(table))
    assert not paths.decision.exists()


# --------------------------------------------------------------------------
# --finalise on a run that could still resume, and --end (section 2.8)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("end", ["terminated", None])
def test_finalise_on_a_run_that_could_resume_refuses_without_end(
    paths: Any, spy: Path, end: str | None
) -> None:
    table = tabled()
    plant(paths, clean(table), end=end)
    with pytest.raises(p4m.ResumeRefused, match="--end"):
        finalise(paths, inputs_of(table))
    assert not paths.decision.exists()
    assert not paths.reads.exists()
    assert not spy.exists()


@pytest.mark.parametrize("text", [None, "", "  \n"])
def test_end_refuses_without_its_note(paths: Any, spy: Path, text: str | None) -> None:
    table = tabled()
    plant(paths, clean(table), end="terminated")
    if text is not None:
        paths.end_note.write_text(text)
    with pytest.raises(p4m.ResumeRefused, match="note"):
        finalise(paths, inputs_of(table), end_flag=True)
    assert not paths.reads.exists()
    assert not spy.exists()
    assert p4m.journal_end(p4m.read_records(paths.journal)) == "terminated"


def test_end_with_its_note_reaches_the_terminal_step_and_copies_the_note(
    paths: Any,
) -> None:
    table = tabled()
    plant(paths, clean(table), end="terminated")
    paths.end_note.write_text("control room: a stuck unit, abandoned\n")
    d = finalise(paths, inputs_of(table), end_flag=True)
    assert d["end"] == p4m.AUTHORISED_END
    assert d["end_note"] == "control room: a stuck unit, abandoned"
    assert paths.reads.exists()


def test_end_on_a_run_that_cannot_resume_is_refused(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table), end="complete")
    paths.end_note.write_text("who and why\n")
    with pytest.raises(p4m.ResumeRefused, match="nothing to choose"):
        finalise(paths, inputs_of(table), end_flag=True)
    assert not paths.reads.exists()


def test_an_end_interrupted_by_a_crash_needs_no_second_choice(
    paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The choice is made once: the journal holds it before any read."""
    table = tabled()
    plant(paths, clean(table), end="terminated")
    paths.end_note.write_text("owner: stuck\n")

    def dies(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(p4m, "terminal_step", dies)
    with pytest.raises(KeyboardInterrupt):
        finalise(paths, inputs_of(table), end_flag=True)
    monkeypatch.undo()
    d = finalise(paths, inputs_of(table))
    assert d["end"] == p4m.AUTHORISED_END
    assert d["end_note"] == "owner: stuck"
    with pytest.raises(p4m.ResumeRefused):
        p4m.check_start(paths)


@pytest.mark.parametrize(
    ("usec", "wall", "end"),
    [
        (172 * 3600 * 10**6, 0.0, "cpu_stop"),
        (1, 120 * 3600.0, "wall_limit"),
    ],
)
def test_a_kill_past_a_limit_cannot_resume_so_needs_no_end(
    paths: Any, usec: int, wall: float, end: str
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    p4m.CpuLedger.begin(paths.cpu).update(usage_usec=usec, wall_s=wall)
    d = finalise(paths, inputs_of(table))
    assert d["end"] == end
    assert paths.reads.exists()


# --------------------------------------------------------------------------
# A resume (sections 2.8, 9)
# --------------------------------------------------------------------------


def test_a_resume_is_allowed_after_a_kill(paths: Any) -> None:
    plant(paths, [{"type": "done", "job": "a"}], end=None)
    p4m.check_start(paths)


def test_a_resume_is_allowed_after_a_reboots_sigterm(paths: Any) -> None:
    plant(paths, [{"type": "done", "job": "a"}], end="terminated")
    p4m.check_start(paths)


def test_a_resume_is_refused_once_reads_json_exists(paths: Any) -> None:
    plant(paths, [{"type": "done", "job": "a"}], end="terminated")
    paths.reads.write_text("{}\n")
    with pytest.raises(p4m.ResumeRefused, match=r"reads\.json"):
        p4m.check_start(paths)


@pytest.mark.parametrize(
    "end", ["complete", "cpu_stop", "wall_limit", "stop", "bug", "byte_fail"]
)
def test_a_resume_is_refused_after_every_other_end(paths: Any, end: str) -> None:
    plant(paths, [{"type": "done", "job": "a"}], end=end)
    with pytest.raises(p4m.ResumeRefused):
        p4m.check_start(paths)


def test_a_byte_failure_left_by_a_kill_refuses_a_resume(paths: Any) -> None:
    plant(paths, [{"type": "byte_fail", "job": "a", "reason": "x"}], end=None)
    with pytest.raises(p4m.ResumeRefused):
        p4m.check_start(paths)


def test_a_start_with_stop_present_is_refused(paths: Any) -> None:
    plant(paths, [], end=None)
    paths.stop.write_text("x")
    with pytest.raises(p4m.ResumeRefused):
        p4m.check_start(paths)


def test_a_fresh_start_in_a_directory_with_records_is_refused(paths: Any) -> None:
    p4m.Journal(paths.journal).append({"type": "run_start"})
    with pytest.raises(p4m.ResumeRefused):
        p4m.check_start(paths)


def test_a_fresh_start_in_an_empty_directory_is_allowed(paths: Any) -> None:
    p4m.check_start(paths)


# --------------------------------------------------------------------------
# refuse(): the refusal alone
# --------------------------------------------------------------------------


def test_a_refusal_writes_the_refusal_alone_and_no_label(paths: Any, spy: Path) -> None:
    code = p4m.refuse(paths, "the tree is not clean")
    assert code == p4m.NO_LABEL_EXIT
    assert decision_of(paths) == {"refusal": "the tree is not clean"}
    assert not paths.reads.exists()
    assert not spy.exists()


def test_a_refusal_does_not_block_the_terminal_step(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table))
    p4m.refuse(paths, "a guard")
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert "refusal" not in decision_of(paths)


@pytest.mark.parametrize("end", ["complete", "bug"])
def test_a_refusal_never_overwrites_a_finished_decision(paths: Any, end: str) -> None:
    table = tabled()
    plant(paths, clean(table), end=end)
    finalise(paths, inputs_of(table))
    before = stamp(paths.decision)
    p4m.refuse(paths, "a guard")
    assert stamp(paths.decision) == before


# --------------------------------------------------------------------------
# The driver and the ends it reaches (sections 6, 8, 9)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToyJob:
    """A job for the driver's tests; ``mode`` says how its process ends."""

    name: str
    mode: str = "ok"
    marker: str = ""

    @property
    def id(self) -> str:
        return self.name

    def run(self, sink: Any) -> None:
        if self.marker:
            with Path(self.marker).open("a") as f:
                f.write(f"{self.name} {os.getpid()}\n")
        match self.mode:
            case "bug":
                raise KeyError("a bug")
            case "byte_fail":
                sink.record("byte_fail", {"reason": "planted"})
                return
            case "kill":
                os.kill(os.getpid(), signal.SIGKILL)
            case "slow":
                time.sleep(0.5)
            case "hang":
                time.sleep(60)
        sink.record("done", {"kind": "toy"})


def driver(root: Path, stages: Sequence[Any], *, workers: int = 2, **kw: Any) -> Any:
    options: dict[str, Any] = {
        "read_usage_usec": lambda: 0,
        "read_mem_gib": lambda: 16.0,
        "cpu_limit_s": 3600,
        "poll_s": 0.005,
        "cpu_poll_s": 0.0,
        "start_method": "fork",
        **kw,
    }
    return p4m.Driver(p4m.OutPaths(root), tuple(stages), workers=workers, **options)


def stage(*jobs: Any, name: str = "tier1", **kw: Any) -> Any:
    return p4m.Stage(name, tuple(jobs), **kw)


def wait_for(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def conclude(paths: Any, inputs: Any, reason: str) -> Any:
    return p4m.conclude(paths, inputs, reason, identity=IDENTITY, manifest_failures=())


def test_the_driver_ends_complete_and_the_terminal_step_follows(
    paths: Any, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    reason = driver(paths.root, [stage(ToyJob("a"))]).run()
    assert reason == "complete"
    assert not spy.exists()
    d = conclude(paths, inputs_of(table), reason)
    assert d["end"] == "complete"
    assert paths.reads.exists()


def test_the_cpu_stop_reaches_the_terminal_step(paths: Any, spy: Path) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    over = 172 * 3600 * 10**6
    run = driver(paths.root, [stage(ToyJob("a"))], read_usage_usec=lambda: over)
    reason = run.run()
    assert reason == "cpu_stop"
    assert not spy.exists()
    assert conclude(paths, inputs_of(table), reason)["end"] == "cpu_stop"
    assert paths.reads.exists()


def test_stop_lets_the_job_in_flight_finish_and_reaches_the_terminal_step(
    paths: Any, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    started = paths.root / "started"
    run = driver(
        paths.root,
        [stage(ToyJob("a", "slow", str(started)), ToyJob("b", marker=str(started)))],
        workers=1,
    )

    def stop_once_a_runs() -> float:
        if started.exists():
            paths.stop.write_text("owner: enough\n")
        return 16.0

    run.read_mem_gib = stop_once_a_runs
    assert run.run() == "stop"
    assert [line.split()[0] for line in started.read_text().splitlines()] == ["a"]
    done = [r for r in p4m.read_records(paths.journal) if r.get("job") == "a"]
    assert [r["type"] for r in done] == ["done"]
    assert not spy.exists()
    d = conclude(paths, inputs_of(table), "stop")
    assert d["end"] == "stop"
    assert d["stop_note"] == "owner: enough"
    assert paths.reads.exists()


def _driven(root: Path, stages: Sequence[Any], table: Table) -> None:
    """A driver and what ``main`` does after it, in a process of its own."""
    os.setsid()
    paths = p4m.OutPaths(root)
    reason = driver(root, stages).run()
    conclude(paths, inputs_of(table), reason)


def sigterm_a_driver(paths: Any, table: Table) -> list[Rec]:
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=_driven, args=(paths.root, [stage(ToyJob("h", "hang"))], table)
    )
    process.start()
    wait_for(
        lambda: (
            paths.journal.exists() and paths.journal.read_text().count("run_start") >= 2
        )
    )
    time.sleep(0.2)
    assert process.pid is not None
    os.kill(process.pid, signal.SIGTERM)
    process.join(20)
    assert process.exitcode == 0
    records: list[Rec] = p4m.read_records(paths.journal)
    assert [r for r in records if r.get("job") == "h"] == []
    return records


def test_sigterm_with_time_left_is_terminated_and_writes_nothing(
    paths: Any, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    records = sigterm_a_driver(paths, table)
    assert p4m.journal_end(records) == "terminated"
    assert not paths.reads.exists()
    assert not paths.decision.exists()
    assert not spy.exists()


def test_sigterm_at_120_hours_is_the_wall_limit_and_reaches_the_terminal_step(
    paths: Any,
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    p4m.CpuLedger.begin(paths.cpu).update(usage_usec=1, wall_s=120 * 3600.0 - 100.0)
    records = sigterm_a_driver(paths, table)
    assert p4m.journal_end(records) == "wall_limit"
    assert paths.reads.exists()
    assert decision_of(paths)["end"] == "wall_limit"


def test_sigterm_between_stop_reason_and_the_check_cannot_finish_a_stage(
    paths: Any,
) -> None:
    """LOG 2026-11-05: the pilot's ``_run_stage`` returned the ``None`` it had
    read before the signal, so the stage counted as finished. On the last stage
    that is a ``complete`` end with a job killed and unrecorded, so a stage before
    it would hide the fault: the next stage's first read would see the signal."""
    started = paths.root / "started"
    ends: list[str] = []
    run = driver(
        paths.root,
        [stage(ToyJob("a", "hang", str(started)), name="tier2")],
        after_stage=ends.append,
    )
    real = run._stop_reason

    def signal_lands_after_the_read() -> str | None:
        reason: str | None = real()
        run.terminating = started.exists()
        return reason

    run._stop_reason = signal_lands_after_the_read
    assert run.run() == "terminated"
    assert ends == []
    records = p4m.read_records(paths.journal)
    assert p4m.journal_end(records) == "terminated"
    assert [r for r in records if r.get("job") == "a"] == []


def test_a_bug_in_a_job_stops_the_run_and_writes_no_read(paths: Any, spy: Path) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    reason = driver(paths.root, [stage(ToyJob("a", "bug"))]).run()
    assert reason == "bug"
    d = conclude(paths, inputs_of(table), reason)
    assert "label" not in d
    assert d["end"] == "bug"
    assert not paths.reads.exists()
    assert not spy.exists()


def test_a_byte_failure_in_a_job_stops_at_once_and_writes_no_read(
    paths: Any, spy: Path
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    reason = driver(paths.root, [stage(ToyJob("a", "byte_fail"))]).run()
    assert reason == "byte_fail"
    d = conclude(paths, inputs_of(table), reason)
    assert d["label"] == "BYTE_FAIL"
    assert not paths.reads.exists()
    assert not spy.exists()


@pytest.mark.parametrize("reason", ["killed", "terminated"])
def test_a_resumable_end_concludes_nothing(paths: Any, spy: Path, reason: str) -> None:
    table = tabled()
    plant(paths, clean(table), end=reason if reason != "killed" else None)
    assert conclude(paths, inputs_of(table), reason) is None
    assert not paths.reads.exists()
    assert not paths.decision.exists()
    assert not spy.exists()


def test_a_stages_end_reads_nothing(paths: Any, spy: Path) -> None:
    ends: list[str] = []
    run = driver(
        paths.root,
        [stage(ToyJob("a"), name="group_a"), stage(ToyJob("b"), name="tier1")],
        after_stage=ends.append,
    )
    assert run.run() == "complete"
    assert ends == ["group_a", "tier1"]
    assert not spy.exists()
    assert not paths.reads.exists()
    assert not paths.decision.exists()


def test_the_end_is_on_the_journal_before_the_terminal_step_can_run(
    paths: Any,
) -> None:
    """So a terminal step that dies leaves an end that ``--finalise`` can read."""
    assert driver(paths.root, [stage(ToyJob("a"))]).run() == "complete"
    assert p4m.journal_end(p4m.read_records(paths.journal)) == "complete"


# --------------------------------------------------------------------------
# No read before the terminal step, by construction
# --------------------------------------------------------------------------


def calls_in(function: ast.AST, names: set[str]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute | ast.Name):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name in names:
                out.add(name)
    return out


def test_the_read_functions_are_named_in_compute_reads_alone() -> None:
    """``analysis.verdict`` is control 1 to 3's read, so it counts as one."""
    tree = ast.parse((REPO / "scripts/p4_search_misses.py").read_text("utf-8"))
    reads = {"read_contrasts", "read_floors", "read_controls", "verdict"}
    named = {
        node.name: calls_in(node, reads)
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }
    assert {k for k, v in named.items() if v} == {"compute_reads"}
    outside = [
        node
        for node in tree.body
        if not isinstance(node, ast.FunctionDef | ast.ClassDef)
    ]
    assert not any(calls_in(node, reads) for node in outside)


def test_compute_reads_is_called_by_the_terminal_step_alone() -> None:
    tree = ast.parse((REPO / "scripts/p4_search_misses.py").read_text("utf-8"))
    callers = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
        and node.name != "compute_reads"
        and calls_in(node, {"compute_reads"})
    }
    assert callers == {"terminal_step"}


def test_the_label_is_written_by_the_terminal_step_alone() -> None:
    tree = ast.parse((REPO / "scripts/p4_search_misses.py").read_text("utf-8"))
    callers = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
        and node.name not in {"decide", "decide_label"}
        and calls_in(node, {"decide", "decide_label"})
    }
    assert callers == {"terminal_step"}


def read_of(point: float, low: float, high: float, reportable: bool = True) -> Any:
    return ContrastRead(point, low, high, reportable, 6, ())


# --------------------------------------------------------------------------
# A read changes (section 2.7)
# --------------------------------------------------------------------------

NAN: Final = float("nan")


@pytest.mark.parametrize(
    ("a", "b", "changed"),
    [
        # Sign, with the interval's relation to zero unchanged.
        (read_of(0.1, -0.2, 0.3), read_of(-0.1, -0.3, 0.2), True),
        # Whether the interval excludes zero, with the sign unchanged.
        (read_of(0.1, 0.01, 0.3), read_of(0.1, -0.01, 0.3), True),
        (read_of(-0.1, -0.3, -0.01), read_of(-0.1, -0.3, 0.01), True),
        # Reportable.
        (read_of(0.1, 0.01, 0.3), read_of(NAN, NAN, NAN, False), True),
        (read_of(NAN, NAN, NAN, False), read_of(0.1, 0.01, 0.3), True),
        # Exactly zero has its own sign, and only ever adds a change.
        (read_of(0.0, -0.1, 0.1), read_of(0.1, -0.1, 0.2), True),
        (read_of(-0.1, -0.2, 0.1), read_of(0.0, -0.1, 0.1), True),
        (read_of(0.0, -0.1, 0.1), read_of(-0.0, -0.2, 0.2), False),
        # A move within its class is no change, however large.
        (read_of(0.1, 0.01, 0.3), read_of(0.9, 0.5, 1.3), False),
        (read_of(0.1, -0.2, 0.3), read_of(0.2, -0.5, 0.9), False),
        # Not reportable on both tables: nothing to compare.
        (read_of(NAN, NAN, NAN, False), read_of(NAN, NAN, NAN, False), False),
    ],
)
def test_a_read_changes_by_sign_by_zero_or_by_reportable(
    a: Any, b: Any, changed: bool
) -> None:
    assert p4m.read_changed(a, b) is changed
    assert p4m.read_changed(b, a) is changed


# --------------------------------------------------------------------------
# The substitution (section 2.7)
# --------------------------------------------------------------------------


def run_terminal(
    tmp_path: Path, name: str, table: Table, **clean_kw: Any
) -> tuple[Any, Any]:
    """A complete end on planted records in a fresh directory."""
    out = p4m.OutPaths(tmp_path / name)
    plant(out, clean(table, **clean_kw))
    return out, finalise(out, inputs_of(table))


def all_of(system: str, value: float, truths: Sequence[str] = TRUTHS) -> dict[Key, Rec]:
    """A miss on each of ``system``'s rows of ``truths``, at ``value``."""
    rows: dict[Key, Rec] = {}
    for t in truths:
        for s in SEEDS:
            rows |= miss((t, s, system), value)
    return rows


def as_float(read: Rec, name: str) -> float:
    return float.fromhex(read[name])


def test_the_corrected_table_substitutes_every_row_in_one_pass_and_derives() -> None:
    table = tabled()
    key_oracle, key_arm = ("t1", 1, ORACLE), ("t1", 1, AGC)
    new = p4m.corrected_table(table, {key_oracle: -0.25, key_arm: -0.375}, {})
    rows = {p4m.row_key(r): r for r in new.rows}
    assert rows[key_oracle].per_event == -0.25
    assert rows[key_arm].per_event == -0.375
    # The reference moved, so every gap on that (truth, seed) moved with it.
    assert rows[key_arm].gap == -0.125
    assert rows[("t1", 1, B_SYM)].gap == -0.375
    assert rows[("t1", 2, B_SYM)].gap == -0.125


def test_the_table_with_nothing_substituted_is_the_table() -> None:
    table = tabled()
    assert p4m.corrected_table(table, {}, {}).rows == table.rows


def test_a_better_fit_can_clear_an_underfit_flag_and_never_raise_one() -> None:
    rows = world()
    key = ("t1", 1, ORACLE)
    rows[key] = replace(rows[key], oracle_underfit=True)
    table = tabled(rows)
    new = p4m.corrected_table(table, {key: -0.25}, {key: False})
    assert {p4m.row_key(r): r for r in new.rows}[key].oracle_underfit is False
    with pytest.raises(p4m.ByteFailure, match="underfit"):
        p4m.corrected_table(tabled(), {key: -0.25}, {key: True})


def test_both_tables_are_read_with_the_same_controls_root_and_resamples(
    paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = tabled()
    plant(paths, clean(table))
    seen: list[tuple[Any, ...]] = []
    real = contrasts.read_contrasts

    def spying(t: Table, *rest: Any) -> Any:
        seen.append(rest)
        return real(t, *rest)

    monkeypatch.setattr(contrasts, "read_contrasts", spying)
    finalise(paths, inputs_of(table, controls=ScriptedControls(naming_null=False)))
    assert len(seen) == 4  # stored, the empty substitution, fixed, fixed2
    assert set(seen) == {(ScriptedControls(False), TEST_READS_ROOT, 200)}


def test_the_empty_substitution_equals_stored_or_it_is_byte_fail(
    paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = tabled()
    plant(paths, clean(table))
    real = contrasts.read_contrasts
    calls = iter(range(100))

    def second_differs(t: Table, *rest: Any) -> Any:
        reads = dict(real(t, *rest))
        if next(calls) == 1:
            reads["C1"] = replace(reads["C1"], high=reads["C1"].high + 2.0**-40)
        return reads

    monkeypatch.setattr(contrasts, "read_contrasts", second_differs)
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "BYTE_FAIL"
    assert d["byte_failures"] == ["empty_corrected"]
    assert not paths.reads.exists()


def test_the_empty_substitution_passes_when_nan_meets_nan(paths: Any) -> None:
    """The naming null failed, so four reads are ``nan``: equal by hex."""
    table = tabled()
    plant(paths, clean(table))
    d = finalise(paths, inputs_of(table, controls=ScriptedControls(False)))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    stored = reads_of(paths)["stored"]
    assert stored["C2-AG-c-time"]["point"] == "nan"
    assert stored["C2-AG-c-time"]["reportable"] is False


@pytest.mark.parametrize("name", ["read_contrasts", "read_floors", "read_controls"])
@pytest.mark.parametrize("error", [ValueError, KeyError, ZeroDivisionError])
def test_an_exception_from_a_read_function_is_byte_fail_and_writes_no_read(
    paths: Any,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    error: type[Exception],
) -> None:
    table = tabled()
    plant(paths, clean(table))

    def raises(*args: Any, **kwargs: Any) -> Any:
        raise error(SCORE_TEXT)

    monkeypatch.setattr(contrasts, name, raises)
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "BYTE_FAIL"
    assert d["exit_code"] == 3
    assert d["byte_failures"] == ["read_exception"]
    assert not paths.reads.exists()
    assert not paths.reads_temporary.exists()
    assert SCORE_TEXT not in paths.decision.read_text(encoding="utf-8")
    faults = [
        r for r in p4m.read_records(paths.journal) if r["type"] == "terminal_fault"
    ]
    assert [r["kind"] for r in faults] == ["read_exception"]


def test_a_nan_reading_in_a_table_is_an_instrument_fault(paths: Any) -> None:
    """``read_contrasts`` refuses it with a ``ScoringError``: no patch here."""
    rows = world()
    rows[("t1", 1, B_SYM)] = replace(rows[("t1", 1, B_SYM)], per_event=float("nan"))
    table = tabled(rows)
    plant(paths, clean(table))
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "BYTE_FAIL"
    assert d["byte_failures"] == ["read_exception"]


def test_a_contrast_row_outside_the_plan_is_byte_fail_and_writes_no_read(
    paths: Any,
) -> None:
    table = tabled()
    plant(paths, clean(table))
    short = tuple(k for k in plan_rows(table) if k != ("t1", 1, B_SYM))
    d = finalise(paths, inputs_of(table, plan_rows=short))
    assert d["label"] == "BYTE_FAIL"
    assert d["byte_failures"] == ["rows_outside_plan"]
    assert not paths.reads.exists()


def test_every_reads_rows_are_held_to_the_plan_on_every_table(
    paths: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row in ``rows`` that no table row explains is caught by itself."""
    table = tabled()
    plant(paths, clean(table))
    real = contrasts.read_contrasts
    calls = iter(range(100))

    def fixed_strays(t: Table, *rest: Any) -> Any:
        reads = dict(real(t, *rest))
        if next(calls) == 2:
            reads["C4"] = replace(reads["C4"], rows=(("t9", 1, B_SYM),))
        return reads

    monkeypatch.setattr(contrasts, "read_contrasts", fixed_strays)
    d = finalise(paths, inputs_of(table))
    assert d["byte_failures"] == ["rows_outside_plan"]


def test_a_row_of_another_system_is_outside_the_subset_rule(paths: Any) -> None:
    """B-np and the Haiku arm are in the table and in no read."""
    table = tabled()
    assert any(r.system in (B_NP, HAIKU) for r in table.rows)
    plant(paths, clean(table))
    assert finalise(paths, inputs_of(table))["label"] != "BYTE_FAIL"


# --------------------------------------------------------------------------
# A planted shift flips each clause on a toy table (section 2.10)
# --------------------------------------------------------------------------


def changed_reads(paths: Any, d: Rec) -> list[str]:
    assert d["label"] == "CONTRASTS_CHANGE"
    names: list[str] = d["H4"]["changed"]
    assert d["H4"]["falsified"] is True
    assert paths.reads.exists()
    return names


def test_a_shift_that_flips_a_sign_changes_the_read(tmp_path: Path) -> None:
    """Every AG-c-S/named row up by 1/8: C1's point goes from 0 to +1/8 exactly."""
    table = tabled()
    out, d = run_terminal(tmp_path, "a", table, rows=all_of(AGC, -0.5))
    got = reads_of(out)
    assert as_float(got["stored"]["C1"], "point") == 0.0
    assert as_float(got["fixed"]["C1"], "point") == 0.125
    assert {"C1", "C4", "C2-AG-c-time"} <= set(changed_reads(out, d))
    assert "C2-AG-o-names" not in d["H4"]["changed"]


def test_a_shift_that_only_moves_the_interval_off_zero_changes_the_read(
    tmp_path: Path,
) -> None:
    """A world where C1's stored point is positive with zero inside the interval,
    and a row of one truth moves the low end above it, with the sign unchanged."""
    rows = world()
    for t, value in zip(TRUTHS, (-0.5, -0.5, -0.5, -0.5, -0.5, -1.0), strict=True):
        for s in SEEDS:
            rows[(t, s, AGC)] = replace(rows[(t, s, AGC)], per_event=value)
    table = tabled(rows)
    plant_rows = all_of(AGC, -0.5, ["t6"])
    out, d = run_terminal(tmp_path, "a", table, rows=plant_rows)
    got = reads_of(out)
    stored, fixed = got["stored"]["C1"], got["fixed"]["C1"]
    assert as_float(stored, "point") > 0.0 and as_float(fixed, "point") > 0.0
    assert as_float(stored, "low") < 0.0 < as_float(fixed, "low")
    assert "C1" in changed_reads(out, d)


def test_a_control_that_fails_only_on_the_corrected_table_is_contrasts_change(
    tmp_path: Path,
) -> None:
    """planted-hint falls below B-rand on the corrected table only, so control 2
    fails there and every read stops being reportable."""
    rows = {
        **{
            (t, s, PLANTED): row_result(
                PLANTED, kind="miss", corrected=-0.75, stored=-0.5, gain=4.0
            )
            for t in TRUTHS
            for s in SEEDS
        },
        **{
            (t, s, B_RAND): row_result(
                B_RAND, kind="miss", corrected=-0.5, stored=-0.75, gain=4.0
            )
            for t in TRUTHS
            for s in SEEDS
        },
    }
    table = tabled()
    out, d = run_terminal(tmp_path, "a", table, rows=rows)
    got = reads_of(out)
    assert got["controls"]["stored"]["controls_pass"] is True
    assert got["controls"]["fixed"]["controls_pass"] is False
    assert all(not r["reportable"] for r in got["fixed"].values())
    assert set(changed_reads(out, d)) == set(READ_NAMES)


def test_a_failed_control_still_changes_the_label_when_h1_failed_too(
    tmp_path: Path,
) -> None:
    table = tabled()
    _, d = run_terminal(tmp_path, "a", table, rows=all_of(AGC, -0.5), a=0)
    assert d["label"] == "CONTRASTS_CHANGE"
    assert d["H1"]["falsified"] is True


def test_no_change_with_a_miss_on_record_survives_and_counts_it(tmp_path: Path) -> None:
    table = tabled()
    one = miss(("t1", 1, B_RAND), -0.6875, stored=-0.75)
    _, d = run_terminal(tmp_path, "a", table, rows=one)
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert d["M_rows"] == 1
    assert d["H2"]["falsified"] is True
    assert d["H4"] == {"falsified": False, "changed": [], "vacuous": False}


def test_h3_is_falsified_by_a_large_delta_and_says_nothing_else(tmp_path: Path) -> None:
    table = tabled()
    one = miss(("t1", 1, B_RAND), -0.6875, stored=-0.75)
    _, d = run_terminal(tmp_path, "a", table, rows=one)
    assert d["H3"]["falsified"] is True
    assert d["max_abs_delta"] == 0.0625


# --------------------------------------------------------------------------
# Not reportable on both tables (sections 2.7, 5)
# --------------------------------------------------------------------------


def controls_fail(rows: dict[Key, Row]) -> dict[Key, Row]:
    """planted-hint below B-rand on every truth, so control 2 fails."""
    for t in TRUTHS:
        for s in SEEDS:
            rows[(t, s, PLANTED)] = replace(rows[(t, s, PLANTED)], per_event=-0.875)
    return rows


def test_no_read_reportable_on_both_tables_is_control_fail_with_the_reason(
    tmp_path: Path,
) -> None:
    table = tabled(controls_fail(world()))
    out, d = run_terminal(tmp_path, "a", table)
    assert d["label"] == "CONTROL_FAIL"
    assert d["exit_code"] == 4
    assert d["reason"] == "no read is reportable on both tables"
    assert d["statement"] is None
    assert out.reads.exists()


def test_a_read_not_reportable_on_both_is_named_and_left_out(tmp_path: Path) -> None:
    table = tabled()
    out = p4m.OutPaths(tmp_path / "a")
    plant(out, clean(table))
    d = finalise(out, inputs_of(table, controls=ScriptedControls(False)))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    names = {"C2-AG-c-names", "C2-AG-c-time", "C2-AG-o-names", "C2-AG-o-time"}
    assert set(d["no_statement"]) == names
    for name in names:
        assert f"{name}: not reportable, no statement" in d["label_line"]
    assert "C1: not reportable" not in d["label_line"]
    lines = {x["read"]: x for x in d["reads"]}
    assert lines["C2-AG-c-time"]["status"] == "not reportable, no statement"
    assert lines["C1"]["status"] == "unchanged"


def test_h1_falsified_is_control_fail_with_its_reason_and_no_wording(
    tmp_path: Path,
) -> None:
    table = tabled()
    _, d = run_terminal(tmp_path, "a", table, a=4)
    assert d["label"] == "CONTROL_FAIL"
    assert d["reason"] == "H1 is falsified"
    assert d["statement"] is None


@pytest.mark.parametrize(
    "over", [{"a": 5}, {"a": 6}], ids=["five of six", "six of six"]
)
def test_h1_stands_at_five_of_six(tmp_path: Path, over: dict[str, int]) -> None:
    table = tabled()
    _, d = run_terminal(tmp_path, "a", table, **over)
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"


@pytest.mark.parametrize("failed", ["b", "d"])
def test_group_b_and_d_must_both_be_caught(tmp_path: Path, failed: str) -> None:
    table = tabled()
    _, d = run_terminal(tmp_path, "a", table, **{failed: False})
    assert d["label"] == "CONTROL_FAIL"


# --------------------------------------------------------------------------
# The wording, the per-read line, S2, S5 and S6
# --------------------------------------------------------------------------


def test_the_survival_wording_is_the_texts_with_its_counts(tmp_path: Path) -> None:
    table = tabled()
    one = miss(("t1", 1, B_RAND), -0.6875, stored=-0.75)
    other = miss(("t1", 1, HAIKU), -0.625, stored=-3.0)
    _, d = run_terminal(tmp_path, "a", table, rows=one | other)
    assert d["statement"].startswith(
        "The reportable contrasts survive substitution of the 0 misses found "
        "(N counts rows a read uses). Of the "
    )
    assert d["statement"].endswith(
        "that the search did not find, or about what any agent would have done "
        "under a better fit."
    )
    assert d["M_rows"] == 2


def test_n_counts_only_the_misses_a_read_uses(tmp_path: Path) -> None:
    """AG-c-S/named at -0.5, so C1 starts at +1/8 and a small move keeps its sign:
    in this world's zero reads any move at all is a change."""
    rows = world()
    for t in TRUTHS:
        for s in SEEDS:
            rows[(t, s, AGC)] = replace(rows[(t, s, AGC)], per_event=-0.5)
    table = tabled(rows)
    one = miss(("t3", 1, B_SYM), -0.5625)
    _, d = run_terminal(tmp_path, "a", table, rows=one)
    assert "substitution of the 1 misses found" in d["statement"]
    lines = {x["read"]: x for x in d["reads"]}
    assert lines["C1"]["n"] == 1
    assert lines["C4"]["n"] == 0


def test_a_read_is_counted_over_the_union_of_its_rows_on_both_tables(
    tmp_path: Path,
) -> None:
    """A row raised off a floor is only in ``stored``'s rows, and N and T say so."""
    rows = world()
    key = ("t3", 1, B_SYM)
    rows[key] = replace(rows[key], per_event=-math.inf, exact=False)
    table = tabled(rows)
    setter = ("t3", 1, B_RAND)
    plant_rows = miss(key, -0.625, stored=-math.inf) | miss(
        setter, -0.6875, stored=-0.75
    )
    out, d = run_terminal(tmp_path, "a", table, rows=plant_rows)
    got = reads_of(out)
    stored_rows = {tuple(k) for k in got["stored"]["C1"]["rows"]}
    fixed_rows = {tuple(k) for k in got["fixed"]["C1"]["rows"]}
    assert setter in stored_rows
    assert setter not in fixed_rows
    assert {x["read"]: x for x in d["reads"]}["C1"]["n"] == 2


def test_s5_gives_each_reads_substituted_llm_rows_as_counts(tmp_path: Path) -> None:
    table = tabled()
    one = miss(("t3", 1, AGC), -0.5625) | miss(("t3", 1, B_SYM), -0.5625)
    _, d = run_terminal(tmp_path, "a", table, rows=one)
    c1 = {x["read"]: x for x in d["reads"]}["C1"]
    assert c1["llm_substituted"] == 1
    assert c1["rows"] > 2
    assert d["llm_caveat"]["reads"] == sorted(
        x["read"] for x in d["reads"] if x["llm_substituted"]
    )
    assert "not what the agent did" in d["llm_caveat"]["text"]


def test_no_llm_row_substituted_names_no_read_in_the_caveat(tmp_path: Path) -> None:
    table = tabled()
    _, d = run_terminal(tmp_path, "a", table)
    assert d["llm_caveat"]["reads"] == []


def test_the_reads_file_holds_every_part_section_2_8_lists(tmp_path: Path) -> None:
    table = tabled()
    out, _ = run_terminal(tmp_path, "a", table)
    got = reads_of(out)
    assert set(got) >= {"stored", "fixed", "fixed2", "floors", "controls", "matched"}
    assert set(got["floors"]) == {"stored", "fixed"}
    assert set(got["controls"]) == {"stored", "fixed"}
    assert set(got["floors"]["stored"]) == set(READ_NAMES)
    one = got["floors"]["fixed"]["C1"]
    assert set(one) == {"floored", "no_common_seed", "without"}
    assert set(got["controls"]["stored"]) == {
        "kept",
        "dropped",
        "unchecked",
        "controls_pass",
        "naming_null",
        "why_not",
    }
    assert got["controls"]["stored"]["kept"] == list(TRUTHS)
    assert got["hard_timeouts"] == {}


def test_s6_counts_floored_rows_and_gives_the_read_with_them_left_out(
    tmp_path: Path,
) -> None:
    rows = world()
    key = ("t3", 1, AGC)
    rows[key] = replace(rows[key], submitted=False, per_event=None)
    table = tabled(rows)
    out, _ = run_terminal(tmp_path, "a", table)
    s6 = reads_of(out)["floors"]["stored"]["C1"]
    assert s6["floored"] == 1
    assert as_float(s6["without"], "point") == 0.0


def test_each_reads_matched_pairs_are_counted_from_its_rows(tmp_path: Path) -> None:
    table = tabled()
    out, _ = run_terminal(tmp_path, "a", table)
    matched = reads_of(out)["matched"]
    assert matched["stored"]["C1"] == len(TRUTHS) * len(SEEDS)
    assert matched["stored"]["C4"] == len(OUT_OF_DICTIONARY) * len(SEEDS)
    assert matched["fixed"]["C1"] == len(TRUTHS) * len(SEEDS)


def test_hard_timeouts_are_counted_per_arm_and_only_when_void() -> None:
    def session(system: str, outcome: str, void: bool) -> Any:
        return analysis.Session(
            "t1",
            1,
            system,
            {},
            {"outcome": outcome},
            VoidStatus(void, (), 0, void),
            "env",
            "digest",
        )

    table = replace(
        tabled(),
        sessions=(
            session(AGO, "hard_timeout", True),
            session(AGO, "hard_timeout", True),
            session(AGC, "hard_timeout", True),
            session(AGC, "wall_time", False),
        ),
    )
    assert p4m.hard_timeouts(table) == {AGC: 1, AGO: 2}


def s2_done(truth: str, seed: int, value: float | None, status: str) -> Rec:
    return {
        "type": "done",
        "job": f"s2 {truth} {seed}",
        "kind": "s2",
        "truth": truth,
        "seed": seed,
        "text": "x",
        "stored_link": "exp",
        "status": status,
        "corrected_per_event": value,
    }


def test_s2s_rows_reach_fixed2_alone_and_the_label_line_says_it_differs(
    paths: Any,
) -> None:
    table = tabled()
    extra = [s2_done(t, s, -0.5, "differs") for t in OUT_OF_DICTIONARY for s in SEEDS]
    plant(paths, [*clean(table), *extra])
    d = finalise(paths, inputs_of(table))
    got = reads_of(paths)
    assert as_float(got["fixed"]["C4"], "point") == 0.0
    assert as_float(got["fixed2"]["C4"], "point") == -0.125
    assert d["fixed2_differs"] == ["C4"]
    assert "fixed2 differs from fixed: C4" in d["label_line"]
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"


def test_fixed2_equal_to_fixed_is_not_mentioned(paths: Any) -> None:
    table = tabled()
    plant(paths, [*clean(table), s2_done("t1", 1, None, "same")])
    d = finalise(paths, inputs_of(table))
    assert d["fixed2_differs"] == []
    assert "fixed2" not in d["label_line"]


def test_an_s2_link_mismatch_cannot_move_a_read(paths: Any) -> None:
    table = tabled()
    plant(paths, [*clean(table), s2_done("t1", 1, -0.5, "link_mismatch")])
    d = finalise(paths, inputs_of(table))
    assert d["s2"]["link_mismatch"] == 1
    assert d["fixed2_differs"] == []


def test_the_reads_and_the_label_are_byte_identical_on_a_rerun(tmp_path: Path) -> None:
    table = tabled()
    rows = all_of(AGC, -0.5, ["t1"])
    a, _ = run_terminal(tmp_path, "a", table, rows=rows)
    b, _ = run_terminal(tmp_path, "b", table, rows=rows)
    assert a.reads.read_bytes() == b.reads.read_bytes()
    assert a.decision.read_bytes() == b.decision.read_bytes()
    assert a.units.read_bytes() == b.units.read_bytes()


def test_the_reads_do_not_depend_on_the_order_records_arrived(tmp_path: Path) -> None:
    table = tabled()
    rows = all_of(AGC, -0.5, ["t1"])
    forward = clean(table, rows=rows)
    a = p4m.OutPaths(tmp_path / "a")
    b = p4m.OutPaths(tmp_path / "b")
    plant(a, forward)
    plant(b, list(reversed(forward)))
    finalise(a, inputs_of(table))
    finalise(b, inputs_of(table))
    assert a.reads.read_bytes() == b.reads.read_bytes()
    assert a.decision.read_bytes() == b.decision.read_bytes()


def test_group_e_goes_on_the_label_line_with_its_denominator(paths: Any) -> None:
    table = tabled()
    census = planted_census(table)
    e_rows = tuple(
        replace(r, group_e=True) if r.system == ORACLE and r.truth == "t1" else r
        for r in census.rows
    )
    census = replace(census, rows=e_rows)
    records = done_records(census)
    known = {"ll": -100.0, "gap": 1e-9, "certified": True}
    fit5 = {"ll": -105.0, "gap": 1e-9, "certified": True}
    for f in census.fits():
        if f.group_e:
            job = p4m.fit_job_id(f.truth, f.seed, f.text)
            done = next(r for r in records if r["job"] == job and r["type"] == "done")
            done["control"] = {
                "group": "E",
                "case": [f.truth, f.seed],
                "fit5": fit5,
                "known": known,
            }
            if f.seed == 2:
                r1 = next(r for r in records if r["job"] == job and r["type"] == "r1")
                r1["best"] = {**r1["best"], "ll": -104.0}
    plant(paths, [*control_records(), *records])
    d = finalise(paths, inputs_of(table, census=census))
    assert d["group_e"] == {"caught": 1, "of": 2}
    assert "group E 1 of 2" in d["label_line"]
    assert d["H1"]["falsified"] is False


# --------------------------------------------------------------------------
# The guards (section 2.10): a real chain of commits, in a toy repository
# --------------------------------------------------------------------------

GIT: Final = (
    "git",
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@example.org",
    "-c",
    "commit.gpgsign=false",
)
ONES: Final = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}
TOY_TEST_SPLIT_SHA: Final = "e" * 64


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [*GIT, *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def write(repo: Path, relative: str, text: str = "x\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Chain:
    repo: Path
    base: str
    f2: str
    r2: str
    s: str
    t: str
    store: Path

    @property
    def sidecar(self) -> Path:
        return self.store / "fingerprint.jsonl"


def build_chain(
    root: Path,
    *,
    log: str | None = None,
    freeze: str | None = None,
    in_t: Sequence[str] = (),
    pushed: bool = True,
) -> Chain:
    """base, F2, R2, a LOG line naming R2, S, T. ``log`` replaces S's LOG (``{r2}``
    filled in), ``freeze`` the file's line, ``in_t`` adds paths to T's commit."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "v2")
    for path in (*p4m.FIT_GUARD_PATHS, p4m.REDUCTIONS_PATH, "uv.lock"):
        write(repo, path, "old\n")
    base = commit(repo, "base")
    write(repo, p4m.FIT_GUARD_PATHS[0], "one changed line\n")
    write(repo, p4m.REDUCTIONS_PATH, "new\n")
    commit(repo, "the one-line diff")
    write(repo, p4m.PREREG_PATH, "the rule\n")
    f2 = commit(repo, "F2")
    write(repo, p4m.SCRIPT_PATH, "script\n")
    write(repo, p4m.TEST_PATH, "test\n")
    hashes = f"sha256: {sha_of('script\n')}\nsha256: {sha_of('test\n')}\n"
    write(repo, p4m.LOG_PATH, hashes)
    r2 = commit(repo, "R2")
    text = f"{hashes}`R2` = {r2}\n" if log is None else log.format(r2=r2, f2=f2)
    write(repo, p4m.LOG_PATH, text)
    write(repo, p4m.TEST_SPLIT_HASH_PATH, f"{TOY_TEST_SPLIT_SHA}\n")
    s = commit(repo, "S")
    write(repo, p4m.FREEZE_PATH, f"{freeze or s}\n")
    for path in in_t:
        write(repo, path)
    t = commit(repo, "T")
    if pushed:
        git(repo, "update-ref", "refs/remotes/origin/v2", t)
    store = root / "store"
    store.mkdir()
    line = json.dumps({"event": "establish", "fingerprint": f"commit={t},tree=clean"})
    (store / "fingerprint.jsonl").write_text(f"{line}\n", encoding="utf-8")
    return Chain(repo, base, f2, r2, s, t, store)


def guards(chain: Chain, **over: Any) -> Any:
    options: dict[str, Any] = {
        "prereg_sha": sha_of("the rule\n"),
        "f2_commit": chain.f2,
        "base_commit": chain.base,
        "fit_diff_sha": p4m.fit_diff_digest(chain.repo, chain.base),
        "reductions_blob": git(chain.repo, "rev-parse", f"HEAD:{p4m.REDUCTIONS_PATH}"),
        "environ": ONES,
        "store": chain.store,
        "launch_copy": chain.sidecar.read_bytes,
        "packages": (),
        **over,
    }
    return p4m.run_guards(chain.repo, **options)


@pytest.fixture
def chain(tmp_path: Path) -> Chain:
    return build_chain(tmp_path)


def test_the_guards_pass_at_t_and_name_every_commit(chain: Chain) -> None:
    assert guards(chain) == {
        "F2": chain.f2,
        "R2": chain.r2,
        "S": chain.s,
        "T": chain.t,
        "prereg_sha256": sha_of("the rule\n"),
    }


def test_a_commit_after_t_is_refused(chain: Chain) -> None:
    write(chain.repo, "docs/v2/LOG2.md")
    commit(chain.repo, "after T")
    with pytest.raises(p4m.GuardFailure, match="HEAD"):
        guards(chain)


def test_a_repointed_freeze_file_is_refused(chain: Chain) -> None:
    """A second commit on the file: repointing it after the fact."""
    write(chain.repo, p4m.FREEZE_PATH, f"{chain.r2}\n")
    head = commit(chain.repo, "repoint")
    git(chain.repo, "update-ref", "refs/remotes/origin/v2", head)
    with pytest.raises(p4m.GuardFailure, match="exactly one commit"):
        guards(chain)


def test_a_freeze_file_naming_another_commit_than_its_parent_is_refused(
    tmp_path: Path,
) -> None:
    other = build_chain(tmp_path, freeze="0" * 40)
    with pytest.raises(p4m.GuardFailure, match="names"):
        guards(other)


def test_a_freeze_file_of_two_lines_is_refused(tmp_path: Path) -> None:
    other = build_chain(tmp_path, freeze="abc1234\nabc1234")
    with pytest.raises(p4m.GuardFailure, match="one line"):
        guards(other)


def test_no_freeze_file_is_refused(tmp_path: Path) -> None:
    chain = build_chain(tmp_path)
    git(chain.repo, "checkout", "-q", chain.s)
    with pytest.raises(p4m.GuardFailure, match="exactly one commit"):
        guards(chain)


def test_ts_diff_must_be_that_file_alone(tmp_path: Path) -> None:
    other = build_chain(tmp_path, in_t=("docs/v2/other.md",))
    with pytest.raises(p4m.GuardFailure, match="alone"):
        guards(other)


def test_t_must_lie_on_origin_v2s_first_parent_chain(tmp_path: Path) -> None:
    other = build_chain(tmp_path, pushed=False)
    git(other.repo, "update-ref", "refs/remotes/origin/v2", other.s)
    with pytest.raises(p4m.GuardFailure, match="origin/v2"):
        guards(other)


def test_t_on_a_merged_side_branch_is_not_on_the_first_parent_chain(
    tmp_path: Path,
) -> None:
    other = build_chain(tmp_path, pushed=False)
    git(other.repo, "checkout", "-q", "-b", "main-line", other.s)
    write(other.repo, "docs/x.md")
    commit(other.repo, "main line")
    git(other.repo, "merge", "-q", "--no-ff", "-m", "m", "v2")
    git(other.repo, "update-ref", "refs/remotes/origin/v2", "HEAD")
    git(other.repo, "checkout", "-q", other.t)
    with pytest.raises(p4m.GuardFailure, match="origin/v2"):
        guards(other)


def test_no_origin_v2_is_refused(tmp_path: Path) -> None:
    other = build_chain(tmp_path, pushed=False)
    with pytest.raises(p4m.GuardFailure, match="origin/v2"):
        guards(other)


def test_a_wrong_prereg_hash_is_refused(chain: Chain) -> None:
    with pytest.raises(p4m.GuardFailure, match="prereg"):
        guards(chain, prereg_sha="0" * 64)


def test_f2_must_be_an_ancestor_of_head(chain: Chain) -> None:
    with pytest.raises(p4m.GuardFailure, match="F2"):
        guards(chain, f2_commit="1" * 40)


def test_the_prereg_at_f2_must_be_the_frozen_text(tmp_path: Path) -> None:
    """A tree that matches and an F2 that does not: the file changed after F2."""
    chain = build_chain(tmp_path)
    with pytest.raises(p4m.GuardFailure, match="F2"):
        guards(chain, f2_commit=chain.base)


@pytest.mark.parametrize(
    "log",
    [
        "`R2` = {r2}\n",
        f"sha256: {sha_of('script\n')}\n`R2` = {{r2}}\n",
        f"sha256: {sha_of('test\n')}\n`R2` = {{r2}}\n",
    ],
    ids=["neither", "no test", "no script"],
)
def test_the_hashes_must_be_in_ss_log(tmp_path: Path, log: str) -> None:
    other = build_chain(tmp_path, log=log)
    with pytest.raises(p4m.GuardFailure, match="LOG"):
        guards(other)


def test_the_working_log_does_not_count(tmp_path: Path) -> None:
    """Hashes added to the LOG after S are in the tree and not in S's LOG."""
    other = build_chain(tmp_path, log="`R2` = {r2}\n")
    assert sha_of("script\n") in git(other.repo, "show", f"{other.r2}:{p4m.LOG_PATH}")
    write(other.repo, p4m.LOG_PATH, f"sha256: {sha_of('script\n')}\n")
    with pytest.raises(p4m.GuardFailure):
        guards(other)


def test_a_script_changed_after_r2_is_refused(tmp_path: Path) -> None:
    other = build_chain(tmp_path, in_t=(p4m.SCRIPT_PATH,))
    with pytest.raises(p4m.GuardFailure):
        guards(other)


def test_r2_must_be_named_by_ss_log(tmp_path: Path) -> None:
    hashes = f"sha256: {sha_of('script\n')}\nsha256: {sha_of('test\n')}\n"
    other = build_chain(tmp_path, log=hashes)
    with pytest.raises(p4m.GuardFailure, match="R2"):
        guards(other)


def test_two_different_r2s_in_the_log_are_refused(tmp_path: Path) -> None:
    hashes = f"sha256: {sha_of('script\n')}\nsha256: {sha_of('test\n')}\n"
    other = build_chain(tmp_path, log=hashes + "`R2` = {r2}\n`R2` = abcdef1\n")
    with pytest.raises(p4m.GuardFailure, match="R2"):
        guards(other)


def test_r2_must_hold_the_two_files_it_is_named_for(tmp_path: Path) -> None:
    hashes = f"sha256: {sha_of('script\n')}\nsha256: {sha_of('test\n')}\n"
    chain = build_chain(tmp_path, log=hashes + "`R2` = {f2}\n")
    with pytest.raises(p4m.GuardFailure, match="R2"):
        guards(chain)


def test_an_r2_that_is_no_commit_here_is_refused(tmp_path: Path) -> None:
    hashes = f"sha256: {sha_of('script\n')}\nsha256: {sha_of('test\n')}\n"
    chain = build_chain(tmp_path, log=hashes + f"`R2` = {'a' * 40}\n")
    with pytest.raises(p4m.GuardFailure, match="R2"):
        guards(chain)


def test_a_store_with_no_sidecar_is_refused(chain: Chain) -> None:
    copy = chain.sidecar.read_bytes()
    chain.sidecar.unlink()
    with pytest.raises(p4m.GuardFailure, match="establish"):
        guards(chain, launch_copy=lambda: copy)


def test_a_reuse_line_is_refused_for_good(chain: Chain) -> None:
    line = json.dumps(
        {
            "event": "reuse",
            "fingerprint": f"commit={chain.s},tree=clean",
            "from": f"commit={chain.t},tree=clean",
        }
    )
    with chain.sidecar.open("a") as f:
        f.write(f"{line}\n")
    with pytest.raises(p4m.GuardFailure, match="establish"):
        guards(chain)


def test_a_store_begun_at_another_commit_is_refused(chain: Chain) -> None:
    line = json.dumps(
        {"event": "establish", "fingerprint": f"commit={chain.s},tree=clean"}
    )
    chain.sidecar.write_text(f"{line}\n")
    with pytest.raises(p4m.GuardFailure, match="witness"):
        guards(chain)


def test_an_unreadable_sidecar_is_refused(chain: Chain) -> None:
    chain.sidecar.write_text("not json\n")
    with pytest.raises(p4m.GuardFailure, match="fingerprint"):
        guards(chain)


def test_a_sidecar_unlike_the_launch_copy_is_refused(chain: Chain) -> None:
    with pytest.raises(p4m.GuardFailure, match="launch copy"):
        guards(chain, launch_copy=lambda: b"other\n")


def test_a_launch_copy_that_cannot_be_fetched_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fails(*args: Any, **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(args, 1, b"", b"no such key")

    monkeypatch.setattr(p4m.subprocess, "run", fails)
    with pytest.raises(p4m.GuardFailure, match="launch copy"):
        p4m.read_launch_copy()


def test_the_launch_copy_is_read_from_beside_the_results() -> None:
    assert p4m.LAUNCH_FINGERPRINT_URI == (
        "s3://delaymargin-results-482913/sciagent/p4/fingerprint.jsonl"
    )


def test_a_fit_diff_other_than_the_constant_is_refused(chain: Chain) -> None:
    with pytest.raises(p4m.GuardFailure, match="fit"):
        guards(chain, fit_diff_sha="0" * 64)


def test_another_reductions_blob_is_refused(chain: Chain) -> None:
    with pytest.raises(p4m.GuardFailure, match="reductions"):
        guards(chain, reductions_blob="0" * 40)


def test_another_fit_version_is_refused(
    chain: Chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p4m, "FIT_VERSION", "sciagent.glm.fit/6")
    with pytest.raises(p4m.GuardFailure, match="fit/5"):
        guards(chain)


def test_the_fit_constants_are_this_repositorys() -> None:
    """Git only: the one-line diff and the blob, read at this head."""
    assert p4m.fit_diff_digest(REPO, p4m.BASE_COMMIT) == p4m.FIT_DIFF_SHA256
    assert p4m.FIT_DIFF_SHA256.startswith("5ab0f61d")
    blob = subprocess.run(
        ["git", "rev-parse", f"HEAD:{p4m.REDUCTIONS_PATH}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert blob == p4m.REDUCTIONS_BLOB
    assert p4m.FIT_VERSION_EXPECTED == "sciagent.glm.fit/5"
    assert p4m.FIT_VERSION == p4m.FIT_VERSION_EXPECTED


def test_a_modified_tracked_file_is_refused(chain: Chain) -> None:
    write(chain.repo, "uv.lock", "edited\n")
    with pytest.raises(p4m.GuardFailure, match="clean"):
        guards(chain)


def test_an_untracked_file_is_refused(chain: Chain) -> None:
    write(chain.repo, "stray.txt")
    with pytest.raises(p4m.GuardFailure, match="clean"):
        guards(chain)


@pytest.mark.parametrize("name", sorted(ONES))
@pytest.mark.parametrize("value", [None, "2"])
def test_blas_threads_must_be_one(chain: Chain, name: str, value: str | None) -> None:
    environ = {k: v for k, v in ONES.items() if k != name}
    if value is not None:
        environ[name] = value
    with pytest.raises(p4m.GuardFailure, match=name):
        guards(chain, environ=environ)


def test_the_blas_guard_reads_the_environment_the_process_started_with() -> None:
    assert p4m.initial_environment().get("PATH") == os.environ.get("PATH")


def test_the_output_is_a_constant_and_the_reports_reads_path_is_inside_it() -> None:
    report = importlib.import_module("pilot_report")
    assert Path("/home/ubuntu/research/records/sciagent/p4-search-misses-F2") == (
        p4m.OUT_DIR
    )
    assert p4m.OutPaths(p4m.OUT_DIR).reads == report.P4_READS


def test_the_options_are_the_texts_and_none_sets_a_number() -> None:
    parser = p4m.build_parser()
    options = {o for a in parser._actions for o in a.option_strings}
    assert {"--out", "--naming-null", "--controls", "--root"}.isdisjoint(options)
    assert options >= {
        "--split",
        "--test-split",
        "--store",
        "--workers",
        "--finalise",
        "--end",
        "--print-hashes",
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["--out", "/tmp/x"])


def test_more_than_two_workers_is_refused() -> None:
    with pytest.raises(SystemExit):
        p4m.build_parser().parse_args(["--workers", "3"])


# --------------------------------------------------------------------------
# Paths and the two splits (section 2.10)
# --------------------------------------------------------------------------


def toy_split(root: Path, kind: str = "dev") -> Path:
    split = root / "data" / "v2" / "truths" / kind
    for name, text in (
        ("manifest.json", "{}"),
        ("dev-001.json", "a"),
        ("sub/dev-002.json", "d"),
        ("Z.json", "c"),
    ):
        path = split / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return split


@pytest.mark.parametrize("kind", ["dev", "test"])
def test_the_split_hash_is_the_texts_shell_pipeline(tmp_path: Path, kind: str) -> None:
    toy_split(tmp_path, kind)
    relative = f"data/v2/truths/{kind}"
    line = f"find {relative} -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum"
    done = subprocess.run(
        ["bash", "-c", line],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    got = p4m.split_digest(tmp_path / relative, relative)
    assert got == done.stdout.split()[0]


def test_the_two_splits_hash_under_their_own_names(tmp_path: Path) -> None:
    dev = toy_split(tmp_path, "dev")
    assert p4m.split_digest(dev, p4m.DEV_RELATIVE) != p4m.split_digest(
        dev, p4m.TEST_RELATIVE
    )
    assert p4m.DEV_RELATIVE == "data/v2/truths/dev"
    assert p4m.TEST_RELATIVE == "data/v2/truths/test"


def test_the_dev_constant_is_the_pilot_rules() -> None:
    assert p4m.SPLIT_SHA256 == (
        "31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577"
    )


def test_a_split_hash_mismatch_stops(tmp_path: Path) -> None:
    split = toy_split(tmp_path)
    with pytest.raises(p4m.GuardFailure, match="split hash"):
        p4m.check_split(split, "0" * 64, p4m.DEV_RELATIVE)
    good = p4m.split_digest(split, p4m.DEV_RELATIVE)
    p4m.check_split(split, good, p4m.DEV_RELATIVE)


def test_the_test_splits_hash_comes_from_s(chain: Chain) -> None:
    assert p4m.test_split_expected(chain.repo, chain.s) == TOY_TEST_SPLIT_SHA


def test_no_hash_at_s_or_a_malformed_one_is_refused(tmp_path: Path) -> None:
    chain = build_chain(tmp_path)
    with pytest.raises(p4m.GuardFailure, match="test split"):
        p4m.test_split_expected(chain.repo, chain.f2)
    write(chain.repo, p4m.TEST_SPLIT_HASH_PATH, "not a hash\n")
    with pytest.raises(p4m.GuardFailure, match="test split"):
        p4m.test_split_expected(chain.repo, commit(chain.repo, "bad"))


def test_every_path_must_be_absolute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    options = {
        "split": tmp_path / "s",
        "test_split": tmp_path / "t",
        "store": tmp_path / "st",
        "out": tmp_path / "o",
        "repo": repo,
    }
    p4m.check_paths(**options)
    for name in ("split", "test_split", "store"):
        with pytest.raises(p4m.GuardFailure, match="absolute"):
            p4m.check_paths(**{**options, name: Path("relative")})


def test_the_test_split_must_be_outside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    with pytest.raises(p4m.GuardFailure, match="outside"):
        p4m.check_paths(
            split=tmp_path / "s",
            test_split=repo / "data" / "test",
            store=tmp_path / "st",
            out=tmp_path / "o",
            repo=repo,
        )


@pytest.mark.parametrize("inside", ["repo", "store"])
def test_the_output_must_be_outside_the_repository_and_the_store(
    tmp_path: Path, inside: str
) -> None:
    repo, store = tmp_path / "repo", tmp_path / "store"
    with pytest.raises(p4m.GuardFailure, match="outside"):
        p4m.check_paths(
            split=tmp_path / "s",
            test_split=tmp_path / "t",
            store=store,
            out=(repo if inside == "repo" else store) / "o",
            repo=repo,
        )


def test_the_manifest_sees_a_changed_an_added_and_a_removed_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    root.mkdir()
    (root / "a").write_text("1")
    (root / "b").write_text("2")
    before = p4m.store_manifest(root)
    (root / "a").write_text("3")
    (root / "b").unlink()
    (root / "c").write_text("4")
    assert p4m.manifest_diff(before, p4m.store_manifest(root)) == [
        "added c",
        "changed a",
        "removed b",
    ]


# --------------------------------------------------------------------------
# Control 4's source (section 2.7)
# --------------------------------------------------------------------------


def naming_record(**over: Any) -> Rec:
    return {
        "schema": record_module.SCHEMA,
        "commit": "t" * 40,
        "split": "test",
        "seed": 1,
        "verdicts": [
            {"truth": "a", "half": "in", "passed": True},
            {"truth": "b", "half": "out", "passed": True},
        ],
        **over,
    }


def test_a_pass_on_both_truths_at_t_is_true(tmp_path: Path) -> None:
    path = tmp_path / "naming-null.json"
    record_module.write_record(path, naming_record())
    assert p4m.scripted_controls("t" * 40, path=path) == ScriptedControls(True)


def test_a_missing_record_gives_false(tmp_path: Path) -> None:
    got = p4m.scripted_controls("t" * 40, path=tmp_path / "none.json")
    assert got == ScriptedControls(False)


def test_an_unreadable_record_gives_false(tmp_path: Path) -> None:
    path = tmp_path / "naming-null.json"
    path.write_text("{not json")
    assert p4m.scripted_controls("t" * 40, path=path) == ScriptedControls(False)


def test_a_record_of_another_commit_gives_false(tmp_path: Path) -> None:
    path = tmp_path / "naming-null.json"
    record_module.write_record(path, naming_record(commit="u" * 40))
    assert p4m.scripted_controls("t" * 40, path=path) == ScriptedControls(False)


def test_anything_but_a_pass_on_both_gives_false(tmp_path: Path) -> None:
    path = tmp_path / "naming-null.json"
    failed = [
        {"truth": "a", "half": "in", "passed": True},
        {"truth": "b", "half": "out", "passed": False},
    ]
    record_module.write_record(path, naming_record(verdicts=failed))
    assert p4m.scripted_controls("t" * 40, path=path) == ScriptedControls(False)


def test_the_default_path_is_the_records_constant() -> None:
    signature = inspect.signature(p4m.scripted_controls)
    assert signature.parameters["path"].default == record_module.NAMING_NULL_RECORD
    assert signature.parameters["split"].default == "test"


# --------------------------------------------------------------------------
# The reference search, copied from the pilot (PR 2.2, 2.3, 2.4; tests as PR 2.10)
# --------------------------------------------------------------------------

EXP_K: Final = Excite(KernelKind.EXP, One(), ALL)
SMALL: Final = (EXP_K, Periodic())  # 6 · 5 = 30 points, sweep 11
MIDDLE: Final = (EXP_K, Gate(Periodic(), PhaseWindow()))  # 600 points, sweep 106


def structure_of(
    features: tuple[Feature, ...] = SMALL, link: Link = Link.SOFTPLUS
) -> Structure:
    return Structure(features, link)


@cache
def toy_data(
    features: tuple[Feature, ...] = SMALL, link: Link = Link.SOFTPLUS, seed: int = 3
) -> tuple[Dataset, ...]:
    structure = structure_of(features, link)
    rng = np.random.default_rng(seed)
    psi = tuple(
        {s: grid(s.name)[int(rng.integers(len(grid(s.name))))] for s in psi_slots(f)}
        for f in structure.features
    )
    coef = Coefficients(0.3, ((-1.0,), (0.4, -0.3)))
    log = simulate(structure, psi, coef, p4m.T2_CHANNELS, p4m.t2_marks, 60.0, rng)
    return (Dataset.observational(log),)


def toy_search(*, k: int = 2, cap: int = 10**6, r2_max: int = 0, **kw: Any) -> Any:
    return p4m.reference_search(
        structure_of(),
        toy_data(),
        p4m.T2_CHANNELS,
        p4m.reference_rng("toy", TEST_ROOT),
        k=k,
        cap=cap,
        r2_max=r2_max,
        **kw,
    )


def valued(ll: float, gap: float = 0.0, certified: bool = True) -> Any:
    return p4m.Valued(ll, gap, certified)


def pv(point: tuple[int, ...], score: float, certified: bool = True) -> Any:
    return p4m.PointValue(point, score, 0.0, certified)


def test_the_search_constants_are_the_texts() -> None:
    """Section 2.2's caps stay as PR 2.2."""
    assert p4m.K_STARTS == 48
    assert p4m.S2_STARTS == 16
    assert p4m.R1_POINT_CAP == 8_000
    assert p4m.R1_POINT_CAP_IDENTITY == 3_000
    assert p4m.S2_POINT_CAP == 2_000
    assert p4m.R2_GRID_MAX == 14_400
    assert p4m.TOL_FLOOR == 3e-7
    assert p4m.H3_LIMIT == 0.01
    assert p4m.HUNT_SIZE == 150
    assert p4m.HUNT_R1_PER_STRATUM == 25
    assert p4m.HUNT_CPU_HOURS == 6.0


def test_the_case_strings_are_the_texts() -> None:
    assert p4m.fit_case("t", 1, "x") == "t|1|x"
    assert p4m.t2_case(0, "exp", 7) == "t2|0|exp|7"
    assert p4m.hunt_case("id-large", 1003) == "hunt|id-large|1003"


@pytest.mark.parametrize("c", ["t|1|x", "t2|0|exp|7", "hunt|id-large|1003"])
def test_the_stream_is_the_first_16_hex_digits_of_sha256(c: str) -> None:
    h = int(hashlib.sha256(c.encode()).hexdigest()[:16], 16)
    want = np.random.default_rng(np.random.SeedSequence([TEST_ROOT, h]))
    got = p4m.reference_rng(c, TEST_ROOT)
    assert got.integers(1 << 40) == want.integers(1 << 40)


def test_the_root_is_always_stated_so_no_call_can_fall_into_a_frozen_one() -> None:
    for function in (p4m.reference_rng, p4m.hunt_log):
        for parameter in inspect.signature(function).parameters.values():
            if "root" in parameter.name:
                assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    ("chosen", "stored", "kind"),
    [
        ((-10.0, 0.0), (-10.0 - 1e-7, 0.0), "near_tie"),
        ((-10.0, 0.0), (-11.0, 0.0), "miss"),
        ((-10.0, 2e-3), (-10.0 - 1e-3, 2e-3), "near_tie"),
        ((-10.0, 0.0), (-10.0, 0.0), "none"),
        ((-11.0, 0.0), (-10.0, 0.0), "none"),
    ],
    ids=["inside the floor", "beyond it", "gaps widen it", "equal", "worse"],
)
def test_the_miss_rule(
    chosen: tuple[float, float], stored: tuple[float, float], kind: str
) -> None:
    assert p4m.call_miss(valued(*chosen), valued(*stored)).kind == kind


@pytest.mark.parametrize("gap", [math.inf, math.nan])
def test_a_non_finite_stored_gap_is_dropped_so_the_row_can_still_miss(
    gap: float,
) -> None:
    call = p4m.call_miss(valued(-10.0, 1e-9), valued(-11.0, gap))
    assert call.kind == "miss"
    assert call.stored_uncertified is True
    assert call.tol == 3e-7


def test_an_uncertified_stored_fit_loses_its_gap_from_the_tolerance() -> None:
    call = p4m.call_miss(valued(-10.0), valued(-10.1, 5.0, certified=False))
    assert call.kind == "miss"


def test_a_stored_fit_at_minus_infinity_is_a_miss_not_nan() -> None:
    assert p4m.call_miss(valued(-10.0), valued(-math.inf)).kind == "miss"


def test_a_gain_at_or_below_both_quadrature_errors_is_listed() -> None:
    assert p4m.within_quadrature(1e-6, 5e-7, 5e-7)
    assert not p4m.within_quadrature(2e-6, 5e-7, 5e-7)


def test_the_highest_certified_point_is_chosen() -> None:
    assert p4m.choose_point(pv((0,), -10.0), pv((1,), -9.0)).point == (1,)


def test_a_higher_uncertified_r0_is_not_chosen() -> None:
    got = p4m.choose_point(pv((0,), -9.0, False), pv((1,), -10.0))
    assert got.point == (1,)


def test_ties_go_to_the_lowest_index_tuple() -> None:
    assert p4m.choose_point(pv((3,), -9.0), pv((1,), -9.0)).point == (1,)


def test_nothing_is_chosen_when_nothing_is_certified() -> None:
    assert p4m.choose_point(pv((0,), -9.0, False), None) is None


def test_higher_uncertified_points_are_counted() -> None:
    values = [pv((1,), -8.0, False), pv((2,), -11.0, False), pv((3,), -8.0)]
    assert p4m.count_uncertified_better(values, pv((0,), -9.0)) == 1


def replayed_points(k: int, c: str = "toy") -> set[tuple[int, ...]]:
    profile = p4m.fresh_profile(structure_of(), toy_data(), p4m.T2_CHANNELS)
    rng = p4m.reference_rng(c, TEST_ROOT)
    blocks = _search_blocks(profile.slots, profile.config.psi_block_max)
    for start in p4m.draw_starts(profile.slots, rng, k):
        _ascend(profile, start, blocks, 10**6)
    return set(profile.solutions)


@pytest.mark.parametrize("k", [2, 5])
def test_every_point_in_the_profile_was_reached_by_the_runs_own_starts(k: int) -> None:
    assert set(toy_search(k=k).profile.solutions) == replayed_points(k)


def test_r1_refuses_a_profile_that_is_not_empty() -> None:
    profile = p4m.fresh_profile(structure_of(), toy_data(), p4m.T2_CHANNELS)
    profile.evaluate([(0, 0, 0)][:1] if len(profile.slots.grids) == 3 else [(0, 0)])
    with pytest.raises(p4m.ContaminatedProfile):
        p4m.run_r1(profile, p4m.reference_rng("toy", TEST_ROOT), k=1, cap=10)


def test_the_reference_takes_no_psi_and_no_coefficients() -> None:
    names = set(inspect.signature(p4m.reference_search).parameters)
    assert names.isdisjoint({"psi", "truth", "coefficients", "known", "start"})


def test_r2_finishes_the_grid_on_the_same_profile() -> None:
    out = toy_search(k=1, r2_max=14_400)
    assert out.r2_complete
    assert len(out.profile.solutions) == out.profile.slots.size


def test_r2_does_not_run_above_its_limit() -> None:
    out = toy_search(k=1, r2_max=1)
    assert not out.r2_complete
    assert len(out.profile.solutions) == out.r1.n_points


def test_r1s_result_is_handed_over_before_r2_starts() -> None:
    seen: list[int] = []
    out = toy_search(k=1, r2_max=14_400, on_r1=lambda r1: seen.append(r1.n_points))
    assert seen == [out.r1.n_points]
    assert seen[0] < out.profile.slots.size


def test_r1s_own_best_does_not_see_r2() -> None:
    alone = toy_search(k=1)
    both = toy_search(k=1, r2_max=14_400)
    assert both.r1.best == alone.best


def test_r1_is_deterministic() -> None:
    a, b = toy_search(k=3), toy_search(k=3)
    assert a.r1.starts == b.r1.starts
    assert a.best == b.best


def test_the_cap_stops_r1_deterministically_and_flags_it() -> None:
    a, b = toy_search(k=8, cap=5), toy_search(k=8, cap=5)
    assert a.r1.capped and b.r1.capped
    assert a.r1.n_ascents == b.r1.n_ascents < 8
    assert a.r1.n_points == b.r1.n_points


def test_a_capped_fit_still_goes_on_to_r2() -> None:
    out = toy_search(k=8, cap=5, r2_max=14_400)
    assert out.r1.capped and out.r2_complete


def test_the_job_cut_stops_between_starts_and_leaves_r2_unstarted() -> None:
    out = toy_search(k=8, r2_max=14_400, should_stop=lambda: True)
    assert out.r1.n_ascents == 0
    assert out.r1.cpu_cut and out.cpu_cut and not out.r2_complete


def test_the_job_cut_reads_this_processs_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(p4m.time, "process_time", lambda: 2.5 * 3600.0)
    assert p4m.past_job_cut()
    monkeypatch.setattr(p4m.time, "process_time", lambda: 2.5 * 3600.0 - 1.0)
    assert not p4m.past_job_cut()


def test_the_grid_class_boundary_is_512_513() -> None:
    assert p4m.is_exhaustive(512)
    assert not p4m.is_exhaustive(513)


def test_the_exhaustive_fit_is_the_brute_force_maximum() -> None:
    structure = structure_of()
    stored = fit(structure, toy_data(), p4m.T2_CHANNELS, config=FitConfig())
    slots = _Slots.of(structure)
    best = max(
        fit(
            structure,
            toy_data(),
            p4m.T2_CHANNELS,
            config=FitConfig(),
            psi=slots.assignment(point),
        ).log_likelihood
        for point in product(*(range(len(g)) for g in slots.grids))
    )
    assert stored.log_likelihood == best


def oracle_expect(**over: Any) -> Any:
    fields = {
        "search_ll": (-100.5).hex(),
        "truth_psi_ll": (-100.0).hex(),
        "psi_source": "truth",
        "underfit": False,
        **over,
    }
    return p4m.OracleExpect(**fields)


def expect(system: str = B_SYM, oracle: Any = None, value: float = -0.75) -> Any:
    return p4m.RowExpect(system, False, value.hex(), oracle)


def test_an_equal_reading_passes() -> None:
    assert p4m.byte_check(expect(), (-0.75).hex(), None) == []


def test_one_ulp_off_fails() -> None:
    off = math.nextafter(-0.75, 0.0).hex()
    assert p4m.byte_check(expect(), off, None)


def test_minus_infinity_compares_as_text() -> None:
    assert p4m.byte_check(expect(value=-math.inf), (-math.inf).hex(), None) == []


@pytest.mark.parametrize(
    "name",
    [
        "oracle_search_ll",
        "oracle_truth_psi_ll",
        "oracle_psi_source",
        "oracle_underfit",
    ],
)
def test_the_oracle_fields_must_match_too(name: str) -> None:
    good = {
        "oracle_search_ll": -100.5,
        "oracle_truth_psi_ll": -100.0,
        "oracle_psi_source": "truth",
        "oracle_underfit": False,
    }
    row = expect(ORACLE, oracle_expect())
    assert p4m.byte_check(row, (-0.75).hex(), good) == []
    wrong = {
        **good,
        name: {"oracle_psi_source": "search", "oracle_underfit": True}.get(name, -9.0),
    }
    assert p4m.byte_check(row, (-0.75).hex(), wrong)


def test_delta_is_signed_and_minus_infinity_has_its_rules() -> None:
    assert p4m.held_out_delta(-0.5, -0.25) == (0.25, "finite")
    assert p4m.held_out_delta(-0.25, -0.5) == (-0.25, "finite")
    assert p4m.held_out_delta(-math.inf, -math.inf) == (0.0, "finite")
    assert p4m.held_out_delta(-math.inf, -0.5) == (None, "infinite_move")
    assert p4m.held_out_delta(-0.5, -math.inf) == (None, "infinite_move")


def test_h3_counts_an_infinite_move_as_above_the_limit() -> None:
    assert p4m.h3_falsified([(None, "infinite_move")])
    assert p4m.h3_falsified([(0.02, "finite")])
    assert not p4m.h3_falsified([(0.01, "finite"), (-0.01, "finite")])


# --------------------------------------------------------------------------
# Records (as PR 2.10)
# --------------------------------------------------------------------------


def test_floats_are_written_as_hex() -> None:
    assert json.loads(p4m.dumps_record({"x": [0.5]})) == {"x": ["0x1.0000000000000p-1"]}


def test_a_torn_last_line_is_not_a_record(tmp_path: Path) -> None:
    path = tmp_path / "j"
    path.write_bytes(b'{"type":"a"}\n{"type":"b')
    assert p4m.read_records(path) == [{"type": "a"}]


def test_a_torn_line_in_the_middle_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "j"
    path.write_bytes(b'{"type":"a\n{"type":"b"}\n')
    with pytest.raises(p4m.ByteFailure):
        p4m.read_records(path)


def test_opening_for_a_resume_removes_only_the_torn_fragment(tmp_path: Path) -> None:
    path = tmp_path / "j"
    path.write_bytes(b'{"type":"a"}\n{"type":"b')
    p4m.repair_tail(path)
    assert path.read_bytes() == b'{"type":"a"}\n'


def test_units_are_sorted_and_keep_no_duplicates() -> None:
    a = {"type": "done", "job": "b"}
    b = {"type": "r1", "job": "a"}
    other = {"type": "run_start"}
    lines = p4m.units_bytes([a, b, a, other]).decode().splitlines()
    assert [json.loads(x)["job"] for x in lines] == ["a", "b"]


# --------------------------------------------------------------------------
# The driver, as PR 2.10 lists it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FateJob:
    """A job whose process ends the way ``mode`` says, for the driver's tests."""

    name: str
    mode: str = "ok"
    marker: str = ""

    @property
    def id(self) -> str:
        return self.name

    def run(self, sink: Any) -> None:
        if self.marker:
            with Path(self.marker).open("a") as f:
                f.write(f"{self.name} {os.getpid()}\n")
        match self.mode:
            case "burn":
                while True:
                    pass
            case "kill":
                os.kill(os.getpid(), signal.SIGKILL)
            case "kill_once":
                if len(Path(self.marker).read_text().splitlines()) == 1:
                    os.kill(os.getpid(), signal.SIGKILL)
            case "exit_clean":
                return
            case "r1_then_kill" | "r1_then_burn":
                sink.record("r1", {"best": None, "result": {"kind": "fit"}})
                if self.mode == "r1_then_kill":
                    os.kill(os.getpid(), signal.SIGKILL)
                while True:
                    pass
        sink.record("done", {"kind": "toy"})


def types_of(path: Path, job: str) -> list[str]:
    return [r["type"] for r in p4m.read_records(path) if r.get("job") == job]


def test_every_job_runs_in_its_own_process(paths: Any) -> None:
    marker = paths.root / "pids"
    jobs = [FateJob(n, marker=str(marker)) for n in "abc"]
    assert driver(paths.root, [stage(*jobs)]).run() == "complete"
    pids = [line.split()[1] for line in marker.read_text().splitlines()]
    assert len(set(pids)) == 3
    assert str(os.getpid()) not in pids


def test_the_driver_uses_no_shared_pool() -> None:
    source = (REPO / "scripts/p4_search_misses.py").read_text("utf-8")
    assert "ProcessPool" not in source
    assert ".Pool(" not in source


def test_a_killed_job_does_not_touch_its_sibling(paths: Any) -> None:
    run = driver(paths.root, [stage(FateJob("a", "kill"), FateJob("b"))])
    assert run.run() == "complete"
    assert types_of(paths.journal, "b") == ["done"]


def test_a_lost_worker_is_retried_once_after_the_stage(paths: Any) -> None:
    marker = paths.root / "m"
    jobs = [FateJob("a", "kill_once", str(marker)), FateJob("b", marker=str(marker))]
    assert driver(paths.root, [stage(*jobs)], workers=1).run() == "complete"
    assert types_of(paths.journal, "a") == ["job_lost", "done"]
    assert [x.split()[0] for x in marker.read_text().splitlines()] == ["a", "b", "a"]


def test_a_second_loss_is_reference_failed_worker_lost(paths: Any) -> None:
    assert driver(paths.root, [stage(FateJob("a", "kill"))]).run() == "complete"
    records = [r for r in p4m.read_records(paths.journal) if r.get("job") == "a"]
    assert [r["type"] for r in records] == ["job_lost", "reference_failed"]
    assert records[-1]["reason"] == "worker_lost"


def test_an_exit_without_a_result_counts_as_lost(paths: Any) -> None:
    assert driver(paths.root, [stage(FateJob("a", "exit_clean"))]).run() == "complete"
    assert types_of(paths.journal, "a")[0] == "job_lost"


def test_the_cpu_limit_is_per_job_and_not_retried(paths: Any) -> None:
    run = driver(paths.root, [stage(FateJob("a", "burn"))], cpu_limit_s=1)
    assert run.run() == "complete"
    records = [r for r in p4m.read_records(paths.journal) if r.get("job") == "a"]
    assert [(r["type"], r["reason"]) for r in records] == [
        ("reference_failed", "cpu_limit")
    ]


def test_a_job_lost_after_r1_is_r2_lost_not_reference_failed(paths: Any) -> None:
    assert driver(paths.root, [stage(FateJob("a", "r1_then_kill"))]).run() == "complete"
    assert types_of(paths.journal, "a")[-1] == "r2_lost"


def test_the_cpu_limit_after_r1_is_r2_lost_and_not_retried(paths: Any) -> None:
    run = driver(paths.root, [stage(FateJob("a", "r1_then_burn"))], cpu_limit_s=1)
    assert run.run() == "complete"
    assert types_of(paths.journal, "a") == ["r1", "r2_lost"]


def test_exit_codes_are_read_as_the_text_says() -> None:
    assert p4m.exit_kind(0) == "ok"
    assert p4m.exit_kind(-signal.SIGXCPU) == "cpu_limit"
    assert p4m.exit_kind(-signal.SIGTERM) == "terminated"
    assert p4m.exit_kind(-signal.SIGKILL) == "lost"
    assert p4m.exit_kind(1) == "bug"


def test_stages_run_in_order_with_a_barrier(paths: Any) -> None:
    marker = paths.root / "m"
    stages = [
        stage(FateJob("a", marker=str(marker)), name="one"),
        stage(FateJob("b", marker=str(marker)), name="two"),
    ]
    assert driver(paths.root, stages).run() == "complete"
    assert [x.split()[0] for x in marker.read_text().splitlines()] == ["a", "b"]


def test_memory_pauses_dispatch_and_stops_nothing(paths: Any) -> None:
    readings = iter([1.0, 1.0, 16.0])
    run = driver(
        paths.root,
        [stage(FateJob("a"))],
        read_mem_gib=lambda: next(readings, 16.0),
    )
    assert run.run() == "complete"
    assert types_of(paths.journal, "a") == ["done"]


def test_the_memory_gate_is_8_gib() -> None:
    assert p4m.may_start(running=0, workers=2, mem_gib=8.0)
    assert not p4m.may_start(running=0, workers=2, mem_gib=7.9)
    assert not p4m.may_start(running=2, workers=2, mem_gib=16.0)


def test_a_resume_appends_and_skips_what_is_done(paths: Any) -> None:
    marker = paths.root / "m"
    plant(paths, [{"type": "done", "job": "a"}], end=None)
    jobs = [FateJob("a", marker=str(marker)), FateJob("b", marker=str(marker))]
    before = paths.journal.read_bytes()
    assert driver(paths.root, [stage(*jobs)]).run() == "complete"
    assert paths.journal.read_bytes().startswith(before)
    assert [x.split()[0] for x in marker.read_text().splitlines()] == ["b"]


def test_a_stage_cap_stops_dispatch_and_is_named(paths: Any) -> None:
    with paths.timing.open("w") as f:
        f.write(json.dumps({"stage": "hunt", "cpu_s": 7.0}) + "\n")
    jobs = stage(FateJob("a"), name="hunt", cpu_cap_s=6.0)
    assert driver(paths.root, [jobs]).run() == "complete"
    records = p4m.read_records(paths.journal)
    assert types_of(paths.journal, "a") == []
    assert [r for r in records if r["type"] == "stage_cap"]


def test_follow_up_jobs_run_after_the_stages_jobs(paths: Any) -> None:
    marker = paths.root / "m"

    def due(records: Sequence[Any]) -> list[Any]:
        return [FateJob("later", marker=str(marker))]

    jobs = stage(FateJob("a", marker=str(marker)), follow_up=due)
    assert driver(paths.root, [jobs]).run() == "complete"
    assert [x.split()[0] for x in marker.read_text().splitlines()] == ["a", "later"]


def test_the_ledger_adds_the_earlier_units(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    p4m.CpuLedger.begin(path).update(usage_usec=10, wall_s=2.0)
    second = p4m.CpuLedger.begin(path)
    second.update(usage_usec=5, wall_s=1.0)
    assert second.total_usec == 15
    assert second.wall_s == 3.0


def test_a_resume_at_the_stop_is_refused_and_just_below_is_allowed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cpu.json"
    ledger = p4m.CpuLedger.begin(path)
    ledger.update(usage_usec=172 * 3600 * 10**6 - 1, wall_s=0.0)
    p4m.CpuLedger.load(path)
    ledger.update(usage_usec=172 * 3600 * 10**6, wall_s=0.0)
    with pytest.raises(p4m.ResumeRefused, match="172"):
        p4m.CpuLedger.load(path)


def test_a_resume_with_no_wall_time_left_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    p4m.CpuLedger.begin(path).update(usage_usec=1, wall_s=120 * 3600.0)
    with pytest.raises(p4m.ResumeRefused, match="120"):
        p4m.CpuLedger.load(path)


def test_a_start_under_another_script_hash_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cpu.json"
    p4m.CpuLedger.begin(path, "a" * 64)
    with pytest.raises(p4m.ResumeRefused, match="script hash"):
        p4m.check_script(path, "b" * 64)


def test_the_driver_reads_the_cgroup_usage(tmp_path: Path) -> None:
    proc = tmp_path / "cgroup"
    proc.write_text("0::/unit\n")
    stat = tmp_path / "unit" / "cpu.stat"
    stat.parent.mkdir()
    stat.write_text("usage_usec 42\nuser_usec 1\n")
    assert p4m.read_usage_usec(proc, tmp_path) == 42


def test_a_missing_cgroup_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(p4m.CpuAccountingError):
        p4m.read_usage_usec(tmp_path / "no", tmp_path)


def test_the_launch_line_gives_the_remaining_wall_time() -> None:
    line = p4m.launch_line(
        3600.0, Path("/w"), Path("/split"), Path("/test"), Path("/store")
    )
    assert f"-p RuntimeMaxSec={120 * 3600 - 3600} " in line
    assert "--nice=10" in line
    assert "--split /split --test-split /test --store /store" in line
    assert "--out" not in line


def test_no_launch_line_is_offered_when_a_resume_would_be_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cpu.json"
    p4m.CpuLedger.begin(path).update(usage_usec=172 * 3600 * 10**6, wall_s=0.0)
    got = p4m.resume_line(path, Path("/w"), Path("/s"), Path("/t"), Path("/st"))
    assert got is None


def test_finalise_works_from_records_left_by_a_real_kill(paths: Any) -> None:
    """``--end`` is needed: a run killed with limits unspent could still resume."""
    table = tabled()
    plant(paths, clean(table), end=None)
    context = multiprocessing.get_context("fork")
    jobs = [stage(ToyJob("first"), ToyJob("second", "hang"))]
    process = context.Process(target=_killable, args=(paths.root, jobs))
    process.start()
    wait_for(lambda: '"first"' in paths.journal.read_text())
    assert process.pid is not None
    os.killpg(process.pid, signal.SIGKILL)
    process.join(20)
    with paths.journal.open("ab") as f:
        f.write(b'{"type":"done","job":"sec')  # a torn write, as a crash leaves
    assert p4m.end_state(p4m.read_records(paths.journal), paths) == "killed"
    with pytest.raises(p4m.ResumeRefused, match="--end"):
        finalise(paths, inputs_of(table))
    paths.end_note.write_text("control room: killed, not resumed\n")
    p4m.repair_tail(paths.journal)
    d = finalise(paths, inputs_of(table), end_flag=True)
    assert d["end"] == p4m.AUTHORISED_END
    assert paths.units.exists() and paths.reads.exists()


def _killable(root: Path, stages: Sequence[Any]) -> None:
    os.setsid()
    driver(root, stages).run()


def test_a_second_driver_on_one_output_is_refused(paths: Any) -> None:
    with p4m.driver_lock(paths), pytest.raises(p4m.ResumeRefused):
        p4m.driver_lock(paths).__enter__()


# --------------------------------------------------------------------------
# The population (sections 2.1, 2.2): toy stores, read only
# --------------------------------------------------------------------------

GOOD_FIT_CONFIG: Final = "x|full=512|sweeps=0"


def good_cell(**over: Any) -> Rec:
    return {
        "key": {"config": {"fit_config": GOOD_FIT_CONFIG}},
        "reading": {"per_event": (-0.75).hex(), "certified": (1.0).hex()},
        "detail": {"result": {}},
        **over,
    }


def small_table(**over: Any) -> tuple[Table, dict[Key, Rec]]:
    rows = (row("t1", 1, B_SYM, -0.75), row("t2", 1, B_SYM, -0.75))
    table = Table(
        {t: entry("hawkes") for t in ("t1", "t2")},
        rows,
        (),
        {B_SYM: 2},
    )
    cells = {p4m.row_key(r): good_cell() for r in rows}
    return replace(table, **over), cells


def session(truth: str, system: str, void: bool, outcome: str = "submitted") -> Any:
    return analysis.Session(
        truth,
        1,
        system,
        {},
        {"outcome": outcome},
        VoidStatus(void, (), 0, void),
        "env",
        "digest",
    )


def test_a_complete_table_passes() -> None:
    p4m.check_table(*small_table())


def test_a_unit_with_no_cell_refuses_with_no_label() -> None:
    table, cells = small_table(missing={B_SYM: 1})
    with pytest.raises(p4m.GuardFailure, match="missing"):
        p4m.check_table(table, cells)


def test_a_non_void_session_with_no_score_cell_is_missing() -> None:
    table, cells = small_table(
        sessions=(session("t1", AGC, False),), planned={B_SYM: 2, AGC: 1}
    )
    with pytest.raises(p4m.GuardFailure, match="no score cell"):
        p4m.check_table(table, cells)


def test_a_void_session_is_counted_and_is_not_missing() -> None:
    table, cells = small_table(
        sessions=(session("t1", AGC, True),), planned={B_SYM: 2, AGC: 1}
    )
    p4m.check_table(table, cells)


def test_a_failed_unit_is_counted_and_is_not_missing() -> None:
    table, cells = small_table()
    rows = (replace(table.rows[0], ok=False, submitted=False), table.rows[1])
    p4m.check_table(replace(table, rows=rows), cells)


def test_a_planned_unit_unaccounted_for_refuses() -> None:
    table, cells = small_table(planned={B_SYM: 3})
    with pytest.raises(p4m.GuardFailure, match="planned"):
        p4m.check_table(table, cells)


def test_the_telling_units_have_no_row_and_are_not_expected_to() -> None:
    table, cells = small_table(planned={B_SYM: 2, "telling": 4})
    p4m.check_table(table, cells)


def test_a_cell_with_another_grid_cap_refuses() -> None:
    table, cells = small_table()
    cells[("t1", 1, B_SYM)] = good_cell(
        key={"config": {"fit_config": "x|full=64|sweeps=0"}}
    )
    with pytest.raises(p4m.GuardFailure, match="full=512"):
        p4m.check_table(table, cells)


def toy_store(root: Path, config: Any = None) -> tuple[Any, Any, Any]:
    env = environment()
    config = config or tiny_config(llm_arms=(), seeds=(1,), baselines=("B-rand",))
    truths = (entry("hawkes"),)
    with ResultStore(root) as store:
        for unit in baseline_units(["hawkes"], config):
            store.put(
                baseline_key(unit, truths[0], env, config),
                {"status": 1.0, "per_event": -0.75, "certified": 1.0},
                {"result": {"structure": "x"}},
            )
    return env, config, truths


def test_every_config_of_the_plan_is_read_into_one_table(tmp_path: Path) -> None:
    root = tmp_path / "store"
    one = tiny_config(llm_arms=(), seeds=(1,), baselines=("B-rand",))
    two = tiny_config(llm_arms=(), seeds=(1,), baselines=("B-sym",))
    env, _, truths = toy_store(root, one)
    toy_store(root, two)
    table, cells = p4m.load_store(root, env, (one, two), truths)
    assert {r.system for r in table.rows} == {"B-rand", "B-sym"}
    assert set(cells) == {p4m.row_key(r) for r in table.rows}
    assert table.planned == {"B-rand": 1, "B-sym": 1}


def test_a_missing_unit_of_any_config_shows_in_the_merged_table(tmp_path: Path) -> None:
    root = tmp_path / "store"
    one = tiny_config(llm_arms=(), seeds=(1,), baselines=("B-rand",))
    two = tiny_config(llm_arms=(), seeds=(1,), baselines=("B-sym",))
    env, _, truths = toy_store(root, one)
    table, cells = p4m.load_store(root, env, (one, two), truths)
    assert table.missing == {"B-sym": 1}
    with pytest.raises(p4m.GuardFailure, match="missing"):
        p4m.check_table(table, cells)


def test_opening_the_store_read_only_leaves_the_manifest_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    env, config, truths = toy_store(root)
    before = p4m.store_manifest(root)
    p4m.load_store(root, env, (config,), truths)
    assert p4m.store_manifest(root) == before


def test_the_store_is_only_ever_opened_read_only() -> None:
    tree = ast.parse((REPO / "scripts/p4_search_misses.py").read_text("utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "ResultStore"
    ]
    assert calls
    for call in calls:
        flags = {k.arg: k.value for k in call.keywords}
        value = flags["read_only"]
        assert isinstance(value, ast.Constant)
        assert value.value is True


def text_of(features: tuple[Feature, ...], link: Link = Link.EXP) -> str:
    return render(canonicalise(Structure(features, link)))


def census_row(
    truth: str,
    system: str,
    text: str | None,
    *,
    seed: int = 1,
    **over: Any,
) -> Row:
    base = row(truth, seed, system, -0.75)
    return replace(base, structure=text, **over)


def census_of(rows: Sequence[Row], tier1: Sequence[Key], **details: Any) -> Any:
    cells = {p4m.row_key(r): good_cell(detail={"result": dict(details)}) for r in rows}
    table = Table({}, tuple(rows), (), {})
    return p4m.build_census(table, cells, environment().channels, frozenset(tier1))


def test_every_row_lands_in_exactly_one_class() -> None:
    assert p4m.classify_row(
        row("a", 1, B_NP, -1.0), grid_size=None, certified=None
    ) == ("no_search_np")
    assert p4m.classify_row(
        row("a", 1, B_LIB, -1.0), grid_size=None, certified=None
    ) == ("no_search_lib")
    assert p4m.classify_row(
        row("a", 1, B_SYM, -1.0), grid_size=513, certified=True
    ) == ("coordinate")
    assert p4m.classify_row(
        row("a", 1, B_SYM, -1.0), grid_size=512, certified=True
    ) == ("exhaustive")
    assert p4m.classify_row(
        row("a", 1, B_SYM, -1.0), grid_size=512, certified=False
    ) == ("exhaustive_uncertified")
    no_submission = replace(row("a", 1, AGO, -1.0), submitted=False)
    assert p4m.classify_row(no_submission, grid_size=None, certified=None) == (
        "no_submission"
    )
    failed = replace(row("a", 1, AGO, -1.0), ok=False, submitted=False)
    assert p4m.classify_row(failed, grid_size=None, certified=None) == "failed"


def test_a_contrast_row_is_tier_1_and_llm_rows_are_not_tier_2_for_being_llm() -> None:
    text = text_of(MIDDLE)
    rows = [census_row("a", AGC, text), census_row("b", HAIKU, text)]
    census = census_of(rows, [("a", 1, AGC)])
    assert {f.truth: f.tier for f in census.fits()} == {"a": 1, "b": 2}


def test_a_fit_shared_with_a_contrast_row_is_tier_1() -> None:
    text = text_of(MIDDLE)
    rows = [census_row("a", HAIKU, text), census_row("a", B_SYM, text)]
    (fit_group,) = census_of(rows, [("a", 1, B_SYM)]).fits()
    assert fit_group.tier == 1
    assert len(fit_group.rows) == 2


def test_rows_with_the_same_text_on_the_same_data_share_one_fit() -> None:
    text = text_of(MIDDLE)
    rows = [
        census_row("a", B_SYM, text),
        census_row("a", B_RAND, text),
        census_row("a", B_SPARSE, text_of(MIDDLE, Link.SOFTPLUS)),
    ]
    assert len(census_of(rows, []).fits()) == 2


def test_the_oracle_cell_fields_reach_the_expectation() -> None:
    rows = [census_row("a", ORACLE, text_of(MIDDLE))]
    census = census_of(
        rows,
        [],
        oracle_search_ll=-100.5,
        oracle_truth_psi_ll=-100.0,
        oracle_psi_source="truth",
        oracle_underfit=False,
    )
    got = census.rows[0].expect.oracle
    assert got == oracle_expect()


def test_group_e_is_every_oracle_unit_whose_search_lost_to_truth_psi() -> None:
    text = text_of(MIDDLE)
    rows = [
        census_row("a", ORACLE, text, oracle_psi_source="truth"),
        census_row("b", ORACLE, text, oracle_psi_source="search"),
        census_row("c", ORACLE, text_of(SMALL), oracle_psi_source="truth"),
        census_row("d", B_SYM, text),
    ]
    census = census_of(rows, [])
    assert [r.truth for r in census.rows if r.group_e] == ["a", "c"]
    assert census.rows[2].klass == "exhaustive"
    assert census.rows[2].expect is not None


def test_the_census_prints_counts_and_no_score(
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = tabled()
    p4m.print_counts(planted_census(table), table)
    out = capsys.readouterr().out
    assert "coordinate" in out
    assert "0x" not in out and "-0." not in out
    assert "group E" not in out


def stage_setup(monkeypatch: pytest.MonkeyPatch) -> Any:
    texts = [text_of(MIDDLE, link) for link in (Link.EXP, Link.SOFTPLUS)]
    rows = [
        census_row("a", ORACLE, texts[0], oracle_psi_source="truth"),
        census_row("b", B_SYM, texts[0]),
        census_row("c", B_SYM, texts[1]),
        census_row("d", B_SPARSE, texts[0]),
        census_row("e", B_SPARSE, texts[0]),
        census_row("f", AGC, texts[1]),
        census_row("g", HAIKU, texts[1]),
        census_row("h", ORACLE, text_of(SMALL), oracle_psi_source="truth"),
    ]
    tier1 = [p4m.row_key(r) for r in rows if r.system != HAIKU]
    census = census_of(
        rows,
        tier1,
        sparse={"link": "exp"},
        oracle_search_ll=-1.0,
        oracle_psi_source="truth",
        oracle_underfit=False,
    )
    truths = {
        t: replace(entry("hawkes"), id=t, in_dictionary=t != "e") for t in "abcdefgh"
    }
    env = environment()
    group_b = p4m.group_b_job(entry("hawkes"), env, TEST_ROOT)
    return p4m.plan_stages(census, truths, env, group_b, roots=ROOTS, hunt_size=2)


def test_stages_are_in_the_texts_order_and_sorted_by_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = stage_setup(monkeypatch)
    assert [s.name for s in stages] == [
        "group_a",
        "group_d",
        "hunt",
        "group_b",
        "tier1",
        "s2",
        "tier2",
    ]
    group_a, group_d, hunt, group_b, tier1, s2, tier2 = (x.jobs for x in stages)
    assert [j.group for j in group_a] == ["A"] * 6
    assert [j.with_r2 for j in group_a] == [False] * 6
    assert [j.k for j in group_a] == [48] * 6
    (d,) = group_d
    assert (d.group, d.with_r2, d.k) == ("D", True, 0)
    (b,) = group_b
    assert (b.group, b.seed, b.root) == ("B", 3, TEST_ROOT)
    assert len(hunt) == 8
    assert all(j.log_root == TEST_LOG_ROOT for j in hunt)
    assert stages[2].follow_up is not None
    assert stages[2].cpu_cap_s == 6.0 * 3600.0
    order = [p4m.fit_job_id(j.truth.id, j.seed, j.text) for j in tier1]
    assert order == sorted(order)
    assert {j.truth.id for j in tier1} == {"a", "b", "c", "d", "e", "f", "h"}
    assert [j.truth.id for j in tier2] == ["g"]
    assert [j.truth.id for j in s2] == ["e", "d"]  # out-of-dictionary first


def test_group_e_runs_in_tier_1_blind_and_not_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exhaustive unit has no coordinate fit, so it gets a job of its own."""
    stages = stage_setup(monkeypatch)
    by_truth = {j.truth.id: j for j in stages[4].jobs}
    assert by_truth["a"].group == "E"
    assert by_truth["h"].group == "E"
    assert by_truth["b"].group is None
    names = [s.name for s in stages]
    assert names.index("group_b") < names.index("tier1")


def test_a_fits_cap_follows_its_link(monkeypatch: pytest.MonkeyPatch) -> None:
    text = text_of(MIDDLE, Link.IDENTITY)
    rows = [census_row("b", B_SYM, text), census_row("c", B_SYM, text_of(MIDDLE))]
    census = census_of(rows, [p4m.row_key(r) for r in rows])
    truths = {t: replace(entry("hawkes"), id=t) for t in "bc"}
    env = environment()
    group_b = p4m.group_b_job(entry("hawkes"), env, TEST_ROOT)
    stages = p4m.plan_stages(census, truths, env, group_b, roots=ROOTS, hunt_size=1)
    assert {j.truth.id: j.cap for j in stages[4].jobs} == {"b": 3_000, "c": 8_000}


# --------------------------------------------------------------------------
# Group B, fresh and off the store (section 2.9)
# --------------------------------------------------------------------------


def test_group_b_holds_the_three_constants_and_asserts_the_two_fields() -> None:
    job = p4m.group_b_job(entry("hawkes"), environment(), TEST_ROOT)
    (expected,) = job.rows
    assert expected.system == ORACLE
    assert expected.per_event == "-0x1.4051a13cc7023p-1"
    assert expected.oracle.search_ll == "-0x1.86fd12dec695ap+9"
    assert expected.oracle.truth_psi_ll == "-0x1.84b974f87ed40p+9"
    assert expected.oracle.psi_source == "truth"
    assert expected.oracle.underfit is False
    assert job.bit_check == ("-0x1.86fd12dec695ap+9", "-0x1.84b974f87ed40p+9")
    assert (job.seed, job.group, job.k, job.r2_max) == (3, "B", 48, 14_400)
    assert job.text == p4m.GROUP_B_TEXT


def test_group_bs_bit_check_reads_the_fits_themselves() -> None:
    fits = SimpleNamespace(
        search=SimpleNamespace(log_likelihood=-1.5),
        kept=SimpleNamespace(log_likelihood=-1.25),
    )
    good = ((-1.5).hex(), (-1.25).hex())
    assert p4m.bit_check_failures(good, fits) == []
    off = (math.nextafter(-1.5, 0.0).hex(), (-1.25).hex())
    assert p4m.bit_check_failures(off, fits)
    assert p4m.bit_check_failures(((-1.5).hex(), (-2.0).hex()), fits)


def test_group_bs_row_is_a_dev_unit_and_reaches_no_p4_count(paths: Any) -> None:
    table = tabled()
    records = clean(table)
    done = next(r for r in records if r.get("job") == "B" and r["type"] == "done")
    done["rows"] = [row_result(ORACLE, kind="miss", corrected=-0.25, gain=9.0)]
    plant(paths, records)
    d = finalise(paths, inputs_of(table))
    assert d["M_rows"] == 0
    assert d["H1"]["group_b_caught"] is True
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"


def test_controls_json_is_written_when_the_controls_end_and_holds_no_p4_row(
    paths: Any,
) -> None:
    table = tabled()
    plant(paths, [*control_records(), *done_records(planted_census(table))], end=None)
    p4m.write_controls(paths, "group_a")
    assert not paths.controls.exists()
    p4m.write_controls(paths, "group_b")
    body = json.loads(paths.controls.read_text())
    assert body["H1"]["falsified"] is False
    assert body["H1"]["group_a_caught"] == 6
    assert set(body["hunt"]) == set(p4m.HUNT_STRATUM_NAMES)
    text = paths.controls.read_text()
    assert not any(t in text for t in ('"t1"', AGC, "per_event"))


# --------------------------------------------------------------------------
# T2 and the hunt, copied (PR 2.9)
# --------------------------------------------------------------------------


def test_group_a_is_the_texts_six_cases() -> None:
    assert p4m.T2_CASES == (
        (0, "softplus", 103648458),
        (2, "exp", 1499614403),
        (0, "softplus", 1074),
        (0, "softplus", 3273957339),
        (0, "softplus", 358866363),
        (0, "softplus", 2006407976),
    )


def test_the_hunt_strata_are_the_texts() -> None:
    names = [(s.name, s.link) for s in p4m.HUNT_STRATA]
    assert names == [
        ("id-24000", Link.IDENTITY),
        ("id-large", Link.IDENTITY),
        ("large-exp", Link.EXP),
        ("large-softplus", Link.SOFTPLUS),
    ]
    sizes = [_Slots.of(Structure(s.features, s.link)).size for s in p4m.HUNT_STRATA]
    assert sizes == [24_000, 3_456_000, 3_456_000, 3_456_000]
    assert tuple(x.name for x in p4m.HUNT_STRATA) == p4m.HUNT_STRATUM_NAMES


@pytest.mark.parametrize("s", [0, 2])
def test_a_hunt_case_draws_in_the_texts_order(s: int) -> None:
    """On a test root and a short horizon, from the stream the text names."""
    spec = p4m.HUNT_STRATA[s]
    j = 7
    structure = Structure(spec.features, spec.link)
    rng = np.random.default_rng(np.random.SeedSequence([TEST_LOG_ROOT, 1000 * s + j]))
    generating = p4m.t2_random_point(structure, rng)
    coefficients = spec.coefficients(structure, rng)
    log = simulate(
        structure, generating, coefficients, p4m.T2_CHANNELS, p4m.t2_marks, 30.0, rng
    )
    case = p4m.hunt_log(spec.name, j, TEST_LOG_ROOT, horizon=30.0)
    assert case.generating == generating
    assert case.log.times.tobytes() == log.times.tobytes()


def test_hunt_cases_run_round_robin() -> None:
    jobs = p4m.hunt_jobs(log_root=TEST_LOG_ROOT, size=2)
    assert [(j.stratum, j.j) for j in jobs[:5]] == [
        ("id-24000", 0),
        ("id-large", 0),
        ("large-exp", 0),
        ("large-softplus", 0),
        ("id-24000", 1),
    ]


def hunt_done(stratum: str, j: int, loss: bool = True) -> Rec:
    return {
        "type": "done",
        "job": p4m.hunt_job_id(stratum, j),
        "kind": "hunt",
        "stratum": stratum,
        "j": j,
        "sim_failed": False,
        "found_loss": loss,
        "uncertified_loss": False,
        "fit5": {"ll": (-105.0).hex(), "gap": (0.0).hex(), "certified": True},
        "known": {"ll": (-100.0).hex(), "gap": (0.0).hex(), "certified": True},
    }


def due(records: list[Rec], size: int = 150) -> list[tuple[str, int]]:
    jobs = p4m.hunt_r1_jobs(records, root=TEST_ROOT, log_root=TEST_LOG_ROOT, size=size)
    return [(j.stratum, j.j) for j in jobs]


def test_r1_runs_on_the_first_25_found_losses_of_a_stratum() -> None:
    records = [hunt_done("large-exp", j) for j in range(30)]
    assert due(records) == [("large-exp", j) for j in range(25)]


def test_an_unfinished_earlier_case_holds_the_later_ones_back() -> None:
    assert due([hunt_done("large-exp", 1)]) == []


def test_what_is_due_does_not_depend_on_arrival_order() -> None:
    records = [hunt_done("large-exp", j) for j in range(3)]
    assert due(records) == due(list(reversed(records)))


def test_a_failed_case_does_not_hold_the_stratum_back() -> None:
    failed = {
        "type": "reference_failed",
        "job": p4m.hunt_job_id("large-exp", 0),
        "reason": "x",
    }
    assert due([failed, hunt_done("large-exp", 1)]) == [("large-exp", 1)]


def test_a_rehearsal_hunt_of_five_stops_at_its_size() -> None:
    records = [hunt_done("large-exp", j) for j in range(5)]
    assert len(due(records, size=5)) == 5


def test_group_c_is_summarised_from_r1s_own_record() -> None:
    j = 0
    r1 = {
        "type": "r1",
        "job": p4m.hunt_r1_job_id("large-exp", j),
        "best": {"ll": (-100.0).hex(), "gap": (0.0).hex()},
    }
    done = {
        "type": "done",
        "job": r1["job"],
        "kind": "hunt_r1",
        "stratum": "large-exp",
        "j": j,
    }
    got = p4m.hunt_summary([hunt_done("large-exp", j), r1, done])
    assert got["large-exp"]["caught"] == 1
    assert got["large-exp"]["r1_run"] == 1
    assert got["id-large"]["found_losses"] == 0


def test_a_found_loss_needs_the_tolerance_and_a_certified_generating_point() -> None:
    assert p4m.found_loss(valued(-105.0, certified=False), valued(-100.0))
    assert not p4m.found_loss(valued(-105.0), valued(-100.0, certified=False))
    assert not p4m.found_loss(valued(-100.0 - 1e-8), valued(-100.0))


def test_control_catch_needs_the_known_value_and_a_gain_over_fit5() -> None:
    fit5, known = valued(-105.0), valued(-100.0)
    assert p4m.control_caught(valued(-100.0), fit5, known)
    assert not p4m.control_caught(valued(-103.0), fit5, known)
    assert not p4m.control_caught(None, fit5, known)
    assert not p4m.control_caught(valued(-100.0), fit5, None)
    assert not p4m.control_caught(valued(-105.0), fit5, valued(-105.0))


def test_a_searched_fits_stratum_is_fixed_by_link_and_grid() -> None:
    assert p4m.stratum_of("exp", 24_000) == "groups_a_b"
    assert p4m.stratum_of("softplus", 24_001) == "large-softplus"
    assert p4m.stratum_of("identity", 24_000) == "id-24000"
    assert p4m.stratum_of("identity", 3_456_000) == "id-large"
    assert p4m.stratum_of("exp", 3_456_001) == "no_control"


def test_s1_status_floors() -> None:
    assert p4m.s1_status(found=4, total=4) == "not_estimable"
    assert p4m.s1_status(found=3, total=5) == "below_floor"
    assert p4m.s1_status(found=4, total=5) == "at_floor"


def test_the_link_choice_is_choose_links_rule() -> None:
    (obs,) = toy_data()
    train, validation = split_dataset(obs, 0.75)
    config = SparseConfig()
    d: Any = SimpleNamespace(train_datasets=(train,), validation_datasets=(validation,))
    want, _ = _choose_link(SMALL, d, p4m.T2_CHANNELS, config, FitConfig())
    got, scores = p4m.link_choice(
        SMALL,
        (train,),
        (validation,),
        p4m.T2_CHANNELS,
        config.links,
        lambda s: fit(s, (train,), p4m.T2_CHANNELS, config=FitConfig()),
    )
    assert got is want
    assert [s[0] for s in scores] == [link.value for link in config.links]


# --------------------------------------------------------------------------
# main: guards first, the terminal step last (sections 2.8, 2.10)
# --------------------------------------------------------------------------


def toy_setup(paths: Any, **over: Any) -> Any:
    env = environment()
    options: dict[str, Any] = {
        "identity": IDENTITY,
        "paths": paths,
        "roots": ROOTS,
        "hunt_size": 1,
        "env": env,
        "plan": (),
        "truths": (),
        "controls": PASS,
        "group_b": None,
        "store": paths.root / "store",
        "resamples": 200,
        **over,
    }
    return p4m.Setup(**options)


@dataclass(frozen=True)
class Args:
    workers: int = 2
    finalise: bool = False
    end: bool = False
    split: Path = Path("/split")
    test_split: Path = Path("/test")


@pytest.fixture
def toy_run(paths: Any, monkeypatch: pytest.MonkeyPatch) -> Callable[[Any, Any], Any]:
    """``execute`` on the toy table, a toy store directory and toy stages."""
    table = tabled()
    store = paths.root / "store"
    store.mkdir()
    (store / "cell").write_text("x")
    census = planted_census(table)

    def load(*args: Any) -> Any:
        return table, {}

    monkeypatch.setattr(p4m, "load_store", load)
    monkeypatch.setattr(p4m, "check_table", lambda *a: None)
    monkeypatch.setattr(p4m, "build_census", lambda *a: census)
    monkeypatch.setattr(p4m, "contrast_rows", lambda *a: plan_rows(table))

    def fork_driver(p: Any, stages: Any, *, workers: int, **kw: Any) -> Any:
        return real_driver(
            p,
            stages,
            workers=workers,
            read_usage_usec=lambda: 0,
            read_mem_gib=lambda: 16.0,
            poll_s=0.005,
            cpu_poll_s=0.0,
            start_method="fork",
            **kw,
        )

    real_driver = p4m.Driver
    monkeypatch.setattr(p4m, "Driver", fork_driver)

    def run(stages: Any, args: Any, **over: Any) -> Any:
        monkeypatch.setattr(p4m, "plan_stages", lambda *a, **k: stages)
        return p4m.execute(toy_setup(paths, **over), args)

    return run


def test_execute_runs_the_stages_then_the_terminal_step(
    paths: Any, toy_run: Any
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    code = toy_run((stage(ToyJob("a")),), Args())
    assert code == 0
    assert decision_of(paths)["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert paths.reads.exists()
    assert paths.manifest.exists()


def test_execute_refuses_a_resume_once_the_reads_exist_and_changes_nothing(
    paths: Any, toy_run: Any
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    toy_run((stage(ToyJob("a")),), Args())
    before = stamp(paths.reads), stamp(paths.decision), stamp(paths.journal)
    assert toy_run((stage(ToyJob("b")),), Args()) == p4m.NO_LABEL_EXIT
    assert (stamp(paths.reads), stamp(paths.decision), stamp(paths.journal)) == before


def test_a_second_finalise_returns_the_same_code_and_rewrites_nothing(
    paths: Any, toy_run: Any
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    first = toy_run((stage(ToyJob("a")),), Args())
    before = stamp(paths.reads), stamp(paths.decision)
    assert toy_run((), Args(finalise=True)) == first
    assert (stamp(paths.reads), stamp(paths.decision)) == before


def test_a_store_changed_during_the_run_is_byte_fail(paths: Any, toy_run: Any) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)

    @dataclass(frozen=True)
    class Touches:
        id: str = "t"

        def run(self, sink: Any) -> None:
            (paths.root / "store" / "cell").write_text("changed")
            sink.record("done", {"kind": "toy"})

    code = toy_run((stage(Touches()),), Args())
    assert code == 3
    assert decision_of(paths)["byte_failures"] == ["store_manifest"]
    assert not paths.reads.exists()


def test_a_terminated_run_exits_with_no_label_and_offers_a_resume(
    paths: Any,
    toy_run: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = tabled()
    plant(paths, clean(table), end=None)
    monkeypatch.setattr(p4m.Driver, "run", lambda self: "terminated", raising=False)
    wrapped = SimpleNamespace(run=lambda: "terminated")
    monkeypatch.setattr(p4m, "Driver", lambda *a, **k: wrapped)
    assert toy_run((), Args()) == p4m.NO_LABEL_EXIT
    assert "systemd-run" in capsys.readouterr().out
    assert not paths.decision.exists()


def test_nothing_is_touched_before_the_guards_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    toy_store(store)
    before = p4m.store_manifest(store)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("touched before the guards passed")

    out = tmp_path / "out"
    monkeypatch.setattr(p4m, "OUT_DIR", out)
    monkeypatch.setattr(p4m, "PREREG_SHA256", "0" * 64)
    monkeypatch.setattr(p4m, "ResultStore", forbidden)
    monkeypatch.setattr(p4m.pilot, "load_split", forbidden)
    monkeypatch.setattr(p4m, "split_digest", forbidden)
    monkeypatch.setattr(p4m, "store_manifest", forbidden)
    code = p4m.main(
        [
            "--split",
            str(tmp_path / "split"),
            "--test-split",
            str(tmp_path / "test"),
            "--store",
            str(store),
        ]
    )
    monkeypatch.undo()
    assert code == p4m.NO_LABEL_EXIT
    assert p4m.store_manifest(store) == before
    assert list(json.loads((out / "decision.json").read_text())) == ["refusal"]


def test_the_naming_null_is_read_at_t_and_not_at_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def controls(commit: str) -> Any:
        seen.append(commit)
        return PASS

    monkeypatch.setattr(p4m, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(p4m, "run_guards", lambda *a, **k: dict(IDENTITY))
    monkeypatch.setattr(p4m, "check_paths", lambda **k: None)
    monkeypatch.setattr(p4m, "check_split", lambda *a: None)
    monkeypatch.setattr(p4m, "test_split_expected", lambda *a: "e" * 64)
    monkeypatch.setattr(p4m, "scripted_controls", controls)
    monkeypatch.setattr(p4m, "p4_inputs", lambda p: ("env", ("plan",), ("truths",)))
    monkeypatch.setattr(p4m, "group_b_inputs", lambda p: ("truth", "env"))
    monkeypatch.setattr(p4m, "group_b_job", lambda *a: "job")
    args = SimpleNamespace(
        split=Path("/s"),
        test_split=Path("/t"),
        store=Path("/st"),
    )
    setup = p4m.frozen_setup(args)
    assert seen == [IDENTITY["T"]]
    assert setup.roots == p4m.FROZEN_ROOTS
    assert setup.paths.root == tmp_path / "out"
    assert setup.controls == PASS


def test_the_hashes_for_the_log_entry_are_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert p4m.main(["--print-hashes"]) == 0
    out = capsys.readouterr().out
    assert p4m.SCRIPT_PATH in out and p4m.TEST_PATH in out


def test_split_test_split_and_store_are_needed_for_a_run() -> None:
    with pytest.raises(SystemExit):
        p4m.main([])


# --------------------------------------------------------------------------
# Whole jobs on a toy truth (slow tier; test root only)
# --------------------------------------------------------------------------


class ListSink:
    """Collects a job's records in this process."""

    def __init__(self) -> None:
        self.records: list[Rec] = []

    def record(self, type_: str, payload: dict[str, Any]) -> None:
        self.records.append(
            json.loads(p4m.dumps_record({"type": type_, "job": "j", **payload}))
        )

    def types(self) -> list[str]:
        return [r["type"] for r in self.records]

    def only(self, type_: str) -> Rec:
        (found,) = [r for r in self.records if r["type"] == type_]
        return found


def toy_job(
    per_event: str | None = None,
    *,
    k: int = 1,
    cap: int = 150,
    r2_max: int = 0,
    **over: Any,
) -> Any:
    truth = entry("hawkes")
    env = environment()
    structure = canonicalise(Structure(MIDDLE, Link.EXP))
    text = render(structure)
    if per_event is None:
        data = p4m.observational_data(truth, 1, env)
        stored = fit(structure, data.observational, env.channels, config=FitConfig())
        held = p4m.held_out_dataset(truth.spec, truth.id, 1)
        model = GLMModel(B_SYM, stored, env.channels, env.marks)
        per_event = model.held_out_per_event([held]).hex()
    expected = p4m.RowExpect(system=B_SYM, llm=False, per_event=per_event, oracle=None)
    return p4m.FitJob(
        truth=truth,
        seed=1,
        text=text,
        rows=(expected,),
        env=env,
        root=TEST_ROOT,
        k=k,
        cap=cap,
        r2_max=r2_max,
        **over,
    )


@pytest.mark.slow
def test_a_whole_job_writes_r0_then_r1_then_done_and_stays_blind() -> None:
    profiles: list[Any] = []
    real = p4m.fresh_profile

    def capture(*args: Any) -> Any:
        profiles.append(real(*args))
        return profiles[-1]

    job = toy_job()
    sink = ListSink()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(p4m, "fresh_profile", capture)
        p4m.run_fit_job(job, sink)
    assert sink.types() == ["r0", "r1", "done"]
    assert sink.only("done")["evidence"] == "searched"
    (profile,) = profiles
    data = p4m.observational_data(job.truth, job.seed, job.env)
    second = _Profile(
        parse(job.text, job.env.channels),
        tuple(data.observational),
        job.env.channels,
        FitConfig(),
        None,
    )
    rng = p4m.reference_rng(p4m.fit_case("hawkes", 1, job.text), TEST_ROOT)
    blocks = _search_blocks(second.slots, second.config.psi_block_max)
    with contextlib.suppress(p4m._Budget):
        for start in p4m.draw_starts(second.slots, rng, job.k):
            _ascend(second, start, blocks, job.cap)
    assert set(profile.solutions) == set(second.solutions)


@pytest.mark.slow
def test_a_byte_mismatch_ends_the_job_before_r1() -> None:
    job = toy_job()
    value = float.fromhex(job.rows[0].per_event)
    off = replace(job.rows[0], per_event=math.nextafter(value, 0.0).hex())
    sink = ListSink()
    p4m.run_fit_job(replace(job, rows=(off,)), sink)
    assert sink.types() == ["byte_fail"]


@pytest.mark.slow
def test_group_bs_bit_check_ends_the_job_before_r1() -> None:
    job = replace(toy_job(), bit_check=((-1.0).hex(), (-1.0).hex()))
    sink = ListSink()
    p4m.run_fit_job(job, sink)
    assert sink.types() == ["byte_fail"]


@pytest.mark.slow
def test_an_r0_that_fails_is_a_byte_failure_not_reference_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = toy_job((-0.75).hex())

    def fail(*args: Any, **kwargs: Any) -> None:
        raise p4m.FitError("no point")

    monkeypatch.setattr(p4m, "fit", fail)
    sink = ListSink()
    p4m.run_fit_job(job, sink)
    assert sink.types() == ["byte_fail"]


@pytest.mark.slow
def test_a_failing_r1_is_reference_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    job = toy_job()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("numerics")

    monkeypatch.setattr(p4m, "reference_search", fail)
    sink = ListSink()
    p4m.run_fit_job(job, sink)
    assert sink.types() == ["r0", "reference_failed"]
    assert sink.only("reference_failed")["reason"] == "ValueError"


@pytest.mark.slow
def test_any_other_exception_in_r1_is_a_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    job = toy_job()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise KeyError("a bug")

    monkeypatch.setattr(p4m, "reference_search", fail)
    with pytest.raises(KeyError):
        p4m.run_fit_job(job, ListSink())


@pytest.mark.slow
def test_a_fit_job_survives_the_trip_to_a_fresh_interpreter(tmp_path: Path) -> None:
    job = toy_job()
    run = driver(tmp_path, [stage(job)], start_method=p4m.START_METHOD)
    assert run.run() == "complete"
    journal = p4m.OutPaths(tmp_path).journal
    assert types_of(journal, job.id) == ["r0", "r1", "done"]


def test_r1_does_not_depend_on_worker_count_or_order(tmp_path: Path) -> None:
    names = ["s1", "s2", "s3"]

    def points(root: Path, order: list[str], workers: int) -> dict[str, Any]:
        root.mkdir()
        jobs = [SearchJob(n) for n in order]
        assert driver(root, [stage(*jobs)], workers=workers).run() == "complete"
        records = p4m.read_records(p4m.OutPaths(root).journal)
        return {r["job"]: r["points"] for r in records if r["type"] == "done"}

    one = points(tmp_path / "one", names, 1)
    two = points(tmp_path / "two", list(reversed(names)), 2)
    assert one == two


@dataclass(frozen=True)
class SearchJob:
    """A real R1 on a toy log, for the worker-count and order test."""

    name: str

    @property
    def id(self) -> str:
        return self.name

    def run(self, sink: Any) -> None:
        out = p4m.reference_search(
            structure_of(),
            toy_data(),
            p4m.T2_CHANNELS,
            p4m.reference_rng(self.name, TEST_ROOT),
            k=2,
            cap=10**6,
            r2_max=0,
        )
        sink.record("done", {"kind": "toy", "points": sorted(out.profile.solutions)})


def t2_job(case: tuple[int, str, int], group: str, with_r2: bool) -> Any:
    return p4m.T2Job(
        *case,
        group,
        with_r2,
        TEST_ROOT,
        0 if group == "D" else p4m.K_STARTS,
        p4m.r1_cap(case[1]),
    )


@pytest.mark.slow
def test_t2_control_1_with_r2_reaches_the_known_value_by_enumeration() -> None:
    sink = ListSink()
    p4m.run_t2_job(t2_job(p4m.T2_CASES[0], "D", True), sink)
    assert sink.types() == ["r0", "r2", "done"]
    done = sink.only("done")
    known = p4m.valued_of(done["known"])
    fit5 = p4m.valued_of(done["fit5"])
    assert known.ll > fit5.ll
    assert p4m.control_caught(p4m.valued_of(done["r2_best"]), fit5, known)


@pytest.mark.slow
def test_t2_control_2_is_caught_by_r1_alone() -> None:
    sink = ListSink()
    p4m.run_t2_job(t2_job(p4m.T2_CASES[1], "A", False), sink)
    done = sink.only("done")
    best = p4m.valued_of(sink.only("r1")["best"])
    assert p4m.control_caught(
        best, p4m.valued_of(done["fit5"]), p4m.valued_of(done["known"])
    )


# --------------------------------------------------------------------------
# --rehearsal: a positive allowlist, each part tested (section 2.10)
# --------------------------------------------------------------------------

DEV_SHA: Final = "9" * 64


def dev_record(truth: str, split: str = "dev") -> Any:
    return SimpleNamespace(id=truth, split=split)


@dataclass(frozen=True)
class Rehearsal:
    repo: Path
    split: Path
    store: Path
    plan: Path


@pytest.fixture
def rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rehearsal:
    """A checkout with no freeze file, a dev split and a store under ``drafts``."""
    repo = tmp_path / "repo"
    store = repo / ".cache" / "drafts" / "store"
    store.mkdir(parents=True)
    split = toy_split(tmp_path)
    monkeypatch.setattr(p4m, "SPLIT_SHA256", p4m.split_digest(split, p4m.DEV_RELATIVE))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"truths": ["dev-001", "dev-002"], "seeds": [1, 2]}))
    return Rehearsal(repo, split, store, plan)


def allow(r: Rehearsal, **over: Any) -> None:
    options: dict[str, Any] = {
        "split": r.split,
        "store": r.store,
        "test_split": None,
        **over,
    }
    p4m.check_rehearsal(r.repo, **options)


def test_a_rehearsal_on_the_allowlist_passes(rehearsal: Rehearsal) -> None:
    allow(rehearsal)


def test_a_rehearsal_refuses_a_split_that_is_not_the_dev_constant(
    rehearsal: Rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p4m, "SPLIT_SHA256", "0" * 64)
    with pytest.raises(p4m.GuardFailure, match="split hash"):
        allow(rehearsal)


def test_a_rehearsal_refuses_a_store_outside_drafts(
    rehearsal: Rehearsal, tmp_path: Path
) -> None:
    with pytest.raises(p4m.GuardFailure, match="drafts"):
        allow(rehearsal, store=tmp_path / "elsewhere")
    with pytest.raises(p4m.GuardFailure, match="drafts"):
        allow(rehearsal, store=rehearsal.repo / ".cache" / "pilot-merged")


def test_a_rehearsal_refuses_the_drafts_directory_itself(rehearsal: Rehearsal) -> None:
    with pytest.raises(p4m.GuardFailure, match="drafts"):
        allow(rehearsal, store=rehearsal.repo / ".cache" / "drafts")


def test_a_link_under_drafts_to_a_store_elsewhere_is_refused(
    rehearsal: Rehearsal, tmp_path: Path
) -> None:
    real = tmp_path / "real-store"
    real.mkdir()
    link = rehearsal.repo / ".cache" / "drafts" / "link"
    link.symlink_to(real)
    with pytest.raises(p4m.GuardFailure, match="drafts"):
        allow(rehearsal, store=link)


def test_a_relative_store_or_split_is_refused(rehearsal: Rehearsal) -> None:
    with pytest.raises(p4m.GuardFailure, match="absolute"):
        allow(rehearsal, store=Path(".cache/drafts/store"))
    with pytest.raises(p4m.GuardFailure, match="absolute"):
        allow(rehearsal, split=Path("data/v2/truths/dev"))


def test_a_rehearsal_refuses_if_the_freeze_file_exists(rehearsal: Rehearsal) -> None:
    """So it cannot run at ``T``."""
    write(rehearsal.repo, p4m.FREEZE_PATH, "abc1234\n")
    with pytest.raises(p4m.GuardFailure, match="FREEZE"):
        allow(rehearsal)


def test_a_rehearsal_refuses_the_test_split(rehearsal: Rehearsal) -> None:
    with pytest.raises(p4m.GuardFailure, match="test split"):
        allow(rehearsal, test_split=Path("/somewhere/test"))


def plan_of(r: Rehearsal, records: Sequence[Any] | None = None) -> Any:
    given = records or [dev_record("dev-001"), dev_record("dev-002")]
    return p4m.rehearsal_records(r.plan, given)


def test_the_plans_truths_are_records_of_the_split(rehearsal: Rehearsal) -> None:
    chosen, spec = plan_of(rehearsal)
    assert [r.id for r in chosen] == ["dev-001", "dev-002"]
    assert spec["seeds"] == [1, 2]


def test_a_truth_that_is_no_record_of_the_split_is_refused(
    rehearsal: Rehearsal,
) -> None:
    with pytest.raises(p4m.GuardFailure, match="record"):
        plan_of(rehearsal, [dev_record("dev-001")])


@pytest.mark.parametrize("split", ["test", "calibration"])
def test_a_truth_of_another_split_is_refused(rehearsal: Rehearsal, split: str) -> None:
    records = [dev_record("dev-001"), dev_record("dev-002", split)]
    with pytest.raises(p4m.GuardFailure, match="split"):
        plan_of(rehearsal, records)


def test_a_plan_with_an_unknown_key_is_refused(rehearsal: Rehearsal) -> None:
    rehearsal.plan.write_text(json.dumps({"truths": ["dev-001"], "roots": [1]}))
    with pytest.raises(p4m.GuardFailure, match="roots"):
        plan_of(rehearsal)


@pytest.mark.parametrize("text", ["{not json", "[]", "{}", '{"truths": []}'])
def test_an_unreadable_or_empty_plan_is_refused(
    rehearsal: Rehearsal, text: str
) -> None:
    rehearsal.plan.write_text(text)
    with pytest.raises(p4m.GuardFailure, match="plan"):
        plan_of(rehearsal)


def test_a_missing_plan_file_is_refused(rehearsal: Rehearsal) -> None:
    rehearsal.plan.unlink()
    with pytest.raises(p4m.GuardFailure, match="plan"):
        plan_of(rehearsal)


def test_the_plans_config_is_the_sonnet_config_at_the_plans_budget() -> None:
    spec = {
        "truths": ["a"],
        "seeds": [1, 2],
        "fits": 5,
        "experiments": 2,
        "control_factor": 3,
        "gp_population": 4,
        "gp_offspring": 2,
    }
    (config,) = p4m.rehearsal_configs(spec)
    assert config.seeds == (1, 2)
    assert (config.fits, config.experiments, config.control_factor) == (5, 2, 3)
    assert (config.gp.population, config.gp.offspring) == (4, 2)
    assert config.llm_letter == "S"
    assert {a.system("S") for a in config.llm_arms} >= set(ARMS)


def test_rehearsal_and_its_plan_come_together() -> None:
    with pytest.raises(SystemExit):
        p4m.main(["--rehearsal", "--split", "/s", "--store", "/st"])
    with pytest.raises(SystemExit):
        p4m.main(["--rehearsal-plan", "/p", "--split", "/s", "--store", "/st"])


def setup_of(
    r: Rehearsal, monkeypatch: pytest.MonkeyPatch, calls: list[Any] | None = None
) -> Any:
    log = [] if calls is None else calls

    def note(name: str, value: Any = None) -> Callable[..., Any]:
        def called(*args: Any, **kwargs: Any) -> Any:
            log.append((name, args, kwargs))
            return value

        return called

    records = [dev_record("dev-001"), dev_record("dev-015"), dev_record("dev-002")]
    truth = replace(entry("hawkes"), id="dev-015")
    monkeypatch.setattr(p4m.pilot, "load_split", lambda p: records)
    monkeypatch.setattr(p4m.pilot, "pointproc_environment", lambda x: environment())
    monkeypatch.setattr(
        p4m.pilot,
        "truth_entries",
        lambda chosen: tuple(replace(truth, id=x.id) for x in chosen),
    )
    monkeypatch.setattr(p4m, "check_tree", note("check_tree"))
    monkeypatch.setattr(p4m, "check_fit", note("check_fit"))
    monkeypatch.setattr(p4m, "check_blas", note("check_blas"))
    monkeypatch.setattr(p4m, "initial_environment", lambda: dict(ONES))
    monkeypatch.setattr(p4m, "head_commit", lambda repo: "h" * 40)
    monkeypatch.setattr(p4m, "run_guards", note("run_guards"))
    monkeypatch.setattr(p4m, "check_chain", note("check_chain"))
    monkeypatch.setattr(p4m, "check_hashes", note("check_hashes"))
    monkeypatch.setattr(p4m, "check_store_witness", note("check_store_witness"))
    monkeypatch.setattr(p4m, "scripted_controls", note("controls", PASS))
    args = SimpleNamespace(
        split=r.split,
        test_split=None,
        store=r.store,
        rehearsal_plan=r.plan,
    )
    return p4m.rehearsal_setup(args, r.repo)


def test_the_mode_changes_exactly_the_texts_list(
    rehearsal: Rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    setup = setup_of(rehearsal, monkeypatch, calls)
    names = [c[0] for c in calls]
    # The commit-chain guards do not run; the fit, tree and BLAS guards do.
    assert {"check_tree", "check_fit", "check_blas"} <= set(names)
    assert {"run_guards", "check_chain", "check_hashes"}.isdisjoint(names)
    assert "check_store_witness" not in names
    # The roots, the hunt, the output, the truths and the plan.
    assert setup.roots == p4m.REHEARSAL_ROOTS
    assert setup.group_b.root == TEST_ROOT
    assert setup.hunt_size == 5
    assert setup.rehearsal is True
    drafts = rehearsal.repo / ".cache" / "drafts"
    assert setup.paths.root.is_relative_to(drafts)
    assert setup.paths.root != p4m.OUT_DIR
    assert [t.id for t in setup.truths] == ["dev-001", "dev-002"]
    assert len(setup.plan) == 1


def test_control_4_in_a_rehearsal_is_a_dev_record_at_its_own_path(
    rehearsal: Rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    setup_of(rehearsal, monkeypatch, calls)
    (call,) = [c for c in calls if c[0] == "controls"]
    assert call[1] == ("h" * 40,)
    assert call[2]["split"] == "dev"
    drafts = rehearsal.repo / ".cache" / "drafts"
    assert call[2]["path"].is_relative_to(drafts)
    assert call[2]["path"] != record_module.NAMING_NULL_RECORD


def test_the_output_moves_with_the_script_and_the_plan(
    rehearsal: Rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fix means rehearsing again, and a repeat is not a fresh start over
    another run's records."""
    first = setup_of(rehearsal, monkeypatch).paths.root
    rehearsal.plan.write_text(json.dumps({"truths": ["dev-001"], "fits": 9}))
    second = setup_of(rehearsal, monkeypatch).paths.root
    assert first != second


def test_no_option_sets_a_root_or_the_mode_beyond_its_plan() -> None:
    parser = p4m.build_parser()
    options = {o for a in parser._actions for o in a.option_strings}
    assert options >= {"--rehearsal", "--rehearsal-plan"}
    assert not {o for o in options if "root" in o or "seed" in o or "shift" in o}


def rehearsal_inputs(table: Table | None = None, **over: Any) -> Any:
    return inputs_of(table, rehearsal=True, **over)


def nonzero_world() -> dict[Key, Row]:
    """AG-c-S/named at -0.5, so reads against it start away from zero."""
    rows = world()
    for t in TRUTHS:
        for s in SEEDS:
            rows[(t, s, AGC)] = replace(rows[(t, s, AGC)], per_event=-0.5)
    return rows


def test_a_rehearsal_plants_a_shift_that_flips_a_read(paths: Any) -> None:
    table = tabled(nonzero_world())
    plant(paths, clean(table))
    d = finalise(paths, rehearsal_inputs(table))
    got = reads_of(paths)
    planted = got["planted_shift"]
    assert planted["read"] == "C1"
    assert d["label"] == "REHEARSAL CONTRASTS_CHANGE"
    assert d["exit_code"] == 2
    assert as_float(got["stored"]["C1"], "point") > 0.0
    assert as_float(got["fixed"]["C1"], "point") < 0.0
    assert "C1" in d["H4"]["changed"]


def test_the_shift_reaches_fixed2_too_and_only_in_a_rehearsal(tmp_path: Path) -> None:
    table = tabled(nonzero_world())
    real, _ = run_terminal(tmp_path, "a", table)
    assert "planted_shift" not in reads_of(real)
    out = p4m.OutPaths(tmp_path / "b")
    plant(out, clean(table))
    finalise(out, rehearsal_inputs(table))
    got = reads_of(out)
    assert got["fixed"]["C1"]["point"] == got["fixed2"]["C1"]["point"]


def test_no_read_reportable_plants_nothing(paths: Any) -> None:
    table = tabled(controls_fail(nonzero_world()))
    plant(paths, clean(table))
    d = finalise(paths, rehearsal_inputs(table))
    assert reads_of(paths)["planted_shift"] is None
    assert d["label"] == "REHEARSAL CONTROL_FAIL"


def test_a_rehearsal_on_any_root_but_the_test_roots_is_refused(paths: Any) -> None:
    """The mode holds only the test roots. Tested on non-control ids: the rule
    refuses everything else, so a frozen root need not be named here."""
    table = tabled()
    plant(paths, clean(table))
    with pytest.raises(p4m.GuardFailure, match="root"):
        finalise(paths, rehearsal_inputs(table, roots=p4m.Roots(1, 2, 3)))
    assert not paths.reads.exists()


def test_every_label_of_a_rehearsal_is_prefixed(paths: Any) -> None:
    table = tabled()
    fault = {"type": "byte_fail", "job": "x", "reason": "x"}
    plant(paths, [*clean(table), fault])
    d = finalise(paths, rehearsal_inputs(table))
    assert d["label"] == "REHEARSAL BYTE_FAIL"
    assert d["exit_code"] == 3


def test_the_bar_is_at_least_6_kept_2_out_of_dictionary_and_1_reportable(
    tmp_path: Path,
) -> None:
    table = tabled(nonzero_world())
    out = p4m.OutPaths(tmp_path / "a")
    plant(out, clean(table))
    d = finalise(out, rehearsal_inputs(table))
    assert d["rehearsal"] == {
        "kept": 6,
        "kept_out_of_dictionary": 2,
        "reportable_on_both": 7,
        "shown": True,
    }


@pytest.mark.parametrize(
    ("kept", "ood", "reportable"), [(5, 2, 1), (6, 1, 1), (6, 2, 0)]
)
def test_a_rehearsal_short_of_the_bar_has_shown_nothing(
    kept: int, ood: int, reportable: int
) -> None:
    assert p4m.rehearsal_shown(kept, ood, reportable) is False


def test_the_bar_is_met_exactly() -> None:
    assert p4m.rehearsal_shown(6, 2, 1) is True


def test_a_rehearsal_refused_at_a_guard_writes_nothing_to_the_real_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    repo = tmp_path / "repo"
    write(repo, p4m.FREEZE_PATH, "abc1234\n")
    monkeypatch.setattr(p4m, "OUT_DIR", real)
    monkeypatch.setattr(p4m.pilot, "ROOT", repo)
    code = p4m.main(
        [
            "--rehearsal",
            "--rehearsal-plan",
            str(tmp_path / "plan.json"),
            "--split",
            str(tmp_path / "split"),
            "--store",
            str(repo / ".cache" / "drafts" / "store"),
        ]
    )
    assert code == p4m.NO_LABEL_EXIT
    assert not real.exists()
    base = p4m.rehearsal_paths(repo)
    assert list(decision_of(base)) == ["refusal"]


def test_end_without_finalise_is_refused_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flag that needs the owner's authority must not fall through to a run."""
    monkeypatch.setattr(p4m, "OUT_DIR", tmp_path / "out")
    with pytest.raises(SystemExit):
        p4m.main(["--end", "--split", "/s", "--test-split", "/t", "--store", "/st"])
    assert not (tmp_path / "out").exists()


#: Read at import, so a test that moves ``OUT_DIR`` does not move this check.
REAL_OUT: Final = p4m.OUT_DIR


@pytest.fixture(autouse=True)
def no_test_reaches_the_real_output() -> Iterator[None]:
    """``main`` writes a refusal to the constant, so a test that forgot to move it
    would write into the launch's records."""
    before = REAL_OUT.exists()
    yield
    assert REAL_OUT.exists() == before, f"a test touched {REAL_OUT}"


def test_the_end_note_is_copied_when_the_terminal_step_is_byte_fail(paths: Any) -> None:
    """Section 2.8: the note goes to ``decision.json``, on every end it authorises."""
    table = tabled()
    plant(paths, clean(table), end="terminated")
    paths.end_note.write_text("owner: abandoned\n")
    d = finalise(paths, inputs_of(table), end_flag=True, manifest_failures=("x",))
    assert d["label"] == "BYTE_FAIL"
    assert d["end_note"] == "owner: abandoned"
    assert decision_of(paths)["end_note"] == "owner: abandoned"
    assert not paths.reads.exists()


def test_the_stop_note_is_copied_when_a_stopped_run_is_byte_fail(paths: Any) -> None:
    table = tabled()
    plant(paths, clean(table), end="stop")
    paths.stop.write_text("owner: enough\n")
    d = finalise(paths, inputs_of(table), manifest_failures=("x",))
    assert d["stop_note"] == "owner: enough"


def test_a_control_cannot_pass_on_the_chosen_point() -> None:
    """Every planted record's ``chosen`` is the known value; R1's own best below
    it must fail regardless."""
    planted = json.loads(p4m.dumps_record(control_records(a=0, b=False)))
    status = p4m.h1_status(planted)
    assert status.a_caught == 0
    assert status.b_caught is False
    assert status.stands is False


def r2_lost_records(table: Table) -> tuple[list[Rec], str]:
    """A fit whose ``done`` never came: R1's result, on the ``r1`` record."""
    census = planted_census(table)
    misses = miss(("t1", 1, B_RAND), -0.6875, stored=-0.75)
    records = done_records(census, rows=misses)
    target = p4m.fit_job_id("t1", 1, f"{B_RAND} t1 1")
    done = next(r for r in records if r["type"] == "done" and r["job"] == target)
    records.remove(done)
    r1 = next(r for r in records if r["type"] == "r1" and r["job"] == target)
    r1["result"] = done
    return records, target


def test_r1s_result_stands_when_the_job_dies_in_r2(paths: Any) -> None:
    """Searched, the miss counts, and no shortfall."""
    table = tabled()
    records, target = r2_lost_records(table)
    tail = {"type": "r2_lost", "job": target, "reason": "cpu_limit"}
    plant(paths, [*control_records(), *records, tail])
    d = finalise(paths, inputs_of(table))
    assert d["label"] == "CONTRASTS_SURVIVE_FOUND_MISSES"
    assert d["M_rows"] == 1
    assert d["coverage"]["unchecked_fits"] == 0
    assert d["clock_stops"]["r2_lost"] == 1


def test_a_job_killed_in_r2_with_no_terminal_record_keeps_r1_too(paths: Any) -> None:
    table = tabled()
    records, _ = r2_lost_records(table)
    plant(paths, [*control_records(), *records], end="cpu_stop")
    d = finalise(paths, inputs_of(table))
    assert d["partial"] is False
    assert d["M_rows"] == 1


def hawkes_setup() -> tuple[Any, Any, Any, Dataset, str]:
    truth = entry("hawkes")
    env = environment()
    data = p4m.observational_data(truth, 1, env)
    held = p4m.held_out_dataset(truth.spec, truth.id, 1)
    return truth, env, data, held, render(canonicalise(truth.structure))


def hawkes_job(rows: tuple[Any, ...], group: str | None = None) -> Any:
    truth, env, _, _, text = hawkes_setup()
    return p4m.FitJob(
        truth=truth,
        seed=1,
        text=text,
        rows=rows,
        env=env,
        root=TEST_ROOT,
        k=2,
        cap=10**6,
        r2_max=14_400,
        group=group,
    )


def score_of(fitted: Any, env: Any, held: Dataset) -> str:
    model = GLMModel(B_SYM, fitted, env.channels, env.marks)
    return str(model.held_out_per_event([held]).hex())


def planted_poor(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
    """R0 planted at the grid's worst point: the row, both fits, and the score."""
    truth, env, data, held, _ = hawkes_setup()
    slots = _Slots.of(truth.structure)
    fits = [
        fit(
            truth.structure,
            data.observational,
            env.channels,
            config=FitConfig(),
            psi=slots.assignment((i,)),
        )
        for i in range(slots.size)
    ]
    poor = min(fits, key=lambda f: f.log_likelihood)
    best = max(fits, key=lambda f: f.log_likelihood)
    monkeypatch.setattr(p4m, "stored_fits", lambda *a: p4m.StoredFits(poor, poor, None))
    row_expect = p4m.RowExpect(B_SYM, False, score_of(poor, env, held), None)
    return row_expect, poor, best, score_of(best, env, held)


@pytest.mark.slow
def test_a_real_miss_is_refitted_scored_and_sized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored fit is planted at the grid's worst point, so the search really
    beats it; everything after R0 is the real path."""
    expected, poor, best, corrected_hex = planted_poor(monkeypatch)
    sink = ListSink()
    p4m.run_fit_job(hawkes_job((expected,)), sink)
    assert sink.only("done")["starts_completed"] == 2
    (result,) = sink.only("done")["rows"]
    assert result["call"] == "miss"
    assert float.fromhex(result["chosen_ll"]) == best.log_likelihood
    corrected = float.fromhex(result["corrected_per_event"])
    assert corrected == float.fromhex(corrected_hex)
    stored = float.fromhex(expected.per_event)
    assert float.fromhex(result["delta"]) == corrected - stored
    assert float.fromhex(result["stored_quadrature_error"]) == poor.quadrature_error
    assert float.fromhex(result["chosen_quadrature_error"]) == best.quadrature_error


@pytest.mark.slow
def test_a_job_dying_in_r2_leaves_r1s_miss_on_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, *_ = planted_poor(monkeypatch)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("in R2")

    monkeypatch.setattr(p4m, "run_r2", fail)
    sink = ListSink()
    p4m.run_fit_job(hawkes_job((expected,)), sink)
    assert sink.types() == ["r0", "r1", "r2_lost"]
    result = sink.only("r1")["result"]
    assert result["evidence"] == "searched"
    assert result["rows"][0]["call"] == "miss"


@pytest.mark.slow
def test_a_job_past_the_cut_is_flagged_and_searched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, *_ = planted_poor(monkeypatch)
    answers = iter([False])
    monkeypatch.setattr(p4m, "past_job_cut", lambda: next(answers, True))
    sink = ListSink()
    p4m.run_fit_job(hawkes_job((expected,)), sink)
    done = sink.only("done")
    assert done["cpu_cut"] is True
    assert done["evidence"] == "searched"


@pytest.mark.slow
def test_a_group_e_job_records_truth_psi_as_its_known_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, poor, best, _ = planted_poor(monkeypatch)
    details = {"oracle_truth_psi_ll": best.log_likelihood}
    monkeypatch.setattr(
        p4m, "stored_fits", lambda *a: p4m.StoredFits(poor, best, details)
    )
    sink = ListSink()
    p4m.run_fit_job(hawkes_job((expected,), group="E"), sink)
    control = sink.only("done")["control"]
    assert control["group"] == "E"
    assert float.fromhex(control["known"]["ll"]) == best.log_likelihood
    assert float.fromhex(control["fit5"]["ll"]) == poor.log_likelihood


@pytest.mark.slow
def test_an_s2_job_reports_a_stored_link_it_cannot_reproduce() -> None:
    truth, env, *_, text = hawkes_setup()
    sink = ListSink()
    p4m.S2Job(truth, 1, text, "no-such-link", env, TEST_ROOT).run(sink)
    done = sink.only("done")
    assert done["status"] == "link_mismatch"
    assert done["corrected_per_event"] is None


@pytest.mark.slow
def test_an_s2_job_with_the_same_choice_changes_nothing() -> None:
    truth, env, *_, text = hawkes_setup()
    first = ListSink()
    p4m.S2Job(truth, 1, text, "no-such-link", env, TEST_ROOT).run(first)
    link = first.only("done")["r0_link"]
    sink = ListSink()
    p4m.S2Job(truth, 1, text, link, env, TEST_ROOT).run(sink)
    assert sink.only("done")["status"] == "same"


@pytest.mark.slow
def test_an_s2_job_whose_reference_differs_fits_and_scores_that_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth, env, *_, text = hawkes_setup()
    real = p4m.link_choice
    calls: list[Any] = []

    def second_differs(*args: Any, **kwargs: Any) -> Any:
        link, scores = real(*args, **kwargs)
        calls.append(link)
        if len(calls) == 2:
            other = next(x for x in SparseConfig().links if x is not link)
            return other, scores
        return link, scores

    monkeypatch.setattr(p4m, "link_choice", second_differs)
    first = ListSink()
    p4m.S2Job(truth, 1, text, "no-such-link", env, TEST_ROOT).run(first)
    calls.clear()
    sink = ListSink()
    link = first.only("done")["r0_link"]
    p4m.S2Job(truth, 1, text, link, env, TEST_ROOT).run(sink)
    done = sink.only("done")
    assert done["status"] == "differs"
    assert done["reference_link"] != link
    assert math.isfinite(float.fromhex(done["corrected_per_event"]))


def toy_case() -> Any:
    return SimpleNamespace(
        structure=structure_of(), generating=None, log=toy_data()[0].log
    )


def test_known_values_are_computed_after_the_result_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order of PR 2.2, on a toy case standing in for the T2 log."""
    case = toy_case()
    monkeypatch.setattr(p4m, "t2_log", lambda *a: case)
    sink = ListSink()
    seen: list[list[str]] = []

    def known(*args: Any) -> Any:
        seen.append(sink.types())
        return valued(0.0)

    monkeypatch.setattr(p4m, "known_value", known)
    p4m.run_t2_job(p4m.T2Job(0, "softplus", 1, "A", False, TEST_ROOT, 1, 10**6), sink)
    assert seen == [["r0", "r1"]]
    assert sink.types() == ["r0", "r1", "done"]


def test_group_d_is_r2_alone_on_an_empty_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = toy_case()
    monkeypatch.setattr(p4m, "t2_log", lambda *a: case)
    monkeypatch.setattr(p4m, "known_value", lambda *a: valued(0.0))
    captured: list[Any] = []
    real = p4m.fresh_profile

    def capture(*args: Any) -> Any:
        captured.append(real(*args))
        return captured[-1]

    monkeypatch.setattr(p4m, "fresh_profile", capture)
    sink = ListSink()
    p4m.run_t2_job(p4m.T2Job(0, "softplus", 1, "D", True, TEST_ROOT, 0, 10**6), sink)
    assert sink.types() == ["r0", "r2", "done"]
    assert len(captured[0].solutions) == 30
    assert sink.only("done")["r2_complete"] is True


def test_a_failed_simulation_is_counted_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fails(*args: Any, **kwargs: Any) -> Any:
        raise p4m.SimulationError("too many events")

    monkeypatch.setattr(p4m, "hunt_log", fails)
    sink = ListSink()
    p4m.run_hunt_job(p4m.HuntJob("large-exp", 0, TEST_LOG_ROOT), sink)
    assert sink.only("done")["sim_failed"] is True


def test_a_rehearsal_on_a_toy_store_reads_only_at_its_terminal_step(
    paths: Any, toy_run: Any, spy: Path
) -> None:
    """``execute`` in the mode, end to end on a toy table."""
    table = tabled()
    plant(paths, clean(table), end=None)
    run = toy_run((stage(ToyJob("a")),), Args(), rehearsal=True)
    assert run == 2
    assert decision_of(paths)["label"].startswith("REHEARSAL ")
    assert "read_contrasts" in spy.read_text()
