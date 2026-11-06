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
import importlib
import json
import multiprocessing
import os
import signal
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

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
p4m: Any = importlib.import_module("p4_search_misses")

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
