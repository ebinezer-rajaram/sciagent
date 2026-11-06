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
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest
from campaign_support import entry

from sciagent.campaign import analysis, contrasts
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.contrasts import ContrastRead, ScriptedControls
from sciagent.campaign.voids import VoidStatus

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


@pytest.fixture(autouse=True)
def no_frozen_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A draw on a frozen root fails the test (module docstring)."""
    real_sequence = np.random.SeedSequence
    real_rng = np.random.default_rng

    def holds_frozen(value: Any) -> bool:
        if isinstance(value, list | tuple):
            return any(holds_frozen(v) for v in value)
        return isinstance(value, int) and value in FROZEN_STREAMS

    def seed_sequence(entropy: Any = None, **kw: Any) -> Any:
        assert not holds_frozen(entropy), f"frozen stream drawn: {entropy}"
        return real_sequence(entropy, **kw)

    def default_rng(seed: Any = None) -> np.random.Generator:
        assert not holds_frozen(seed), f"frozen stream drawn: {seed}"
        return real_rng(seed)

    monkeypatch.setattr(np.random, "SeedSequence", seed_sequence)
    monkeypatch.setattr(np.random, "default_rng", default_rng)


def test_the_tripwire_fires_on_each_frozen_root_and_on_nothing_else() -> None:
    for root in sorted(FROZEN_STREAMS):
        with pytest.raises(AssertionError, match="frozen stream"):
            p4m.reference_rng("toy", root)
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
