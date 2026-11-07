"""Control 4's runner and its record (prereg 2026-11-05 §2.7, "Control 4's source").

``scripts/p4_naming_null.py`` runs the planted-hint script in the three naming
conditions on two truths fixed by rule and writes one record;
``scripts/naming_null_record.py`` is what the miss script reads it with. The
reader's value gates the four C2 reads, so every doubt about the file is False,
and no option can set it. No test split exists: everything real here runs on
dev truths, into a temporary directory.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import numpy as np
import pytest

from sciagent.campaign.execute import UnitResult
from sciagent.campaign.llm import SessionOutcome
from sciagent.campaign.plan import CampaignError, Unit
from sciagent.harness.record import SessionRecord
from sciagent.scenarios.records import TruthRecord, read_split
from sciagent.scoring.controls_p4 import (
    NAMING_CONDITIONS,
    FitFact,
    NamingNullVerdict,
    SessionFacts,
    naming_null,
)
from sciagent.scoring.errors import ScoringError
from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES

ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPTS: Final = ROOT / "scripts"
SPLIT: Final = ROOT / "data" / "v2" / "truths" / "dev"
COMMIT: Final = "0123456789abcdef0123456789abcdef01234567"
OTHER: Final = "89abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def record_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("naming_null_record")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("p4_naming_null")


def _body(record_module: ModuleType, split: str = "test") -> dict[str, Any]:
    return {
        "schema": record_module.SCHEMA,
        "commit": COMMIT,
        "split": split,
        "seed": 1,
        "verdicts": [
            {"truth": f"{split}-000", "half": "in", "passed": True},
            {"truth": f"{split}-001", "half": "out", "passed": True},
        ],
    }


def _written(path: Path, body: Mapping[str, Any]) -> Path:
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The truth rule
# --------------------------------------------------------------------------


def _records() -> tuple[TruthRecord, ...]:
    records, _ = read_split(SPLIT)
    return records


def test_the_rule_takes_the_first_by_sorted_id_in_each_half(runner: ModuleType) -> None:
    first_in, first_out = runner.rule_truths(_records())
    assert (first_in.id, first_out.id) == ("dev-000", "dev-001")
    assert not first_in.out_of_dictionary
    assert first_out.out_of_dictionary


def test_the_rule_does_not_depend_on_the_records_order(runner: ModuleType) -> None:
    records = _records()
    order = np.random.default_rng(7).permutation(len(records))
    shuffled = tuple(records[int(i)] for i in order)
    assert shuffled[0].id != "dev-000"
    expected = runner.rule_truths(records)
    assert runner.rule_truths(shuffled) == expected
    assert runner.rule_truths(tuple(reversed(_records()))) == expected


def test_the_rule_reads_the_half_from_the_record_not_from_the_id(
    runner: ModuleType,
) -> None:
    """Flipping every record's half must swap what the rule picks."""
    flipped = tuple(
        replace(r, out_of_dictionary=not r.out_of_dictionary) for r in _records()
    )
    first_in, first_out = runner.rule_truths(flipped)
    assert (first_in.id, first_out.id) == ("dev-001", "dev-000")


@pytest.mark.parametrize("missing", [False, True])
def test_the_rule_refuses_a_split_with_one_half(
    runner: ModuleType, missing: bool
) -> None:
    left = tuple(r for r in _records() if r.out_of_dictionary != missing)
    with pytest.raises(ScoringError, match="half"):
        runner.rule_truths(left)


# --------------------------------------------------------------------------
# The reader: everything but a pass on both truths at the commit is False
# --------------------------------------------------------------------------


def test_a_pass_on_both_truths_at_the_commit_is_true(
    record_module: ModuleType, tmp_path: Path
) -> None:
    path = _written(tmp_path / "r.json", _body(record_module))
    assert record_module.read_naming_null(COMMIT, path=path) is True


def test_the_reader_defaults_to_the_constant_of_section_2_7(
    record_module: ModuleType,
) -> None:
    assert (
        Path("/home/ubuntu/research/records/sciagent/p4-naming-null/naming-null.json")
        == record_module.NAMING_NULL_RECORD
    )
    defaults = record_module.read_naming_null.__kwdefaults__
    assert defaults["path"] == record_module.NAMING_NULL_RECORD
    assert defaults["split"] == "test"


def test_a_missing_file_is_false(record_module: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "absent.json"
    assert record_module.read_naming_null(COMMIT, path=path) is False


def test_a_directory_is_false(record_module: ModuleType, tmp_path: Path) -> None:
    assert record_module.read_naming_null(COMMIT, path=tmp_path) is False


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"not json",
        b"\xff\xfe\x00",
        b"null",
        b"[]",
        b"true",
        b'"passed"',
        b"[" * 200_000,
    ],
    ids=[
        "empty",
        "truncated",
        "text",
        "not-utf8",
        "null",
        "list",
        "bool",
        "string",
        "too-deep",
    ],
)
def test_an_unreadable_file_is_false(
    record_module: ModuleType, tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "r.json"
    path.write_bytes(raw)
    assert record_module.read_naming_null(COMMIT, path=path) is False


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def edit(body: dict[str, Any]) -> None:
        body[key] = value

    return edit


def _drop(key: str) -> Callable[[dict[str, Any]], None]:
    def edit(body: dict[str, Any]) -> None:
        del body[key]

    return edit


def _entry(index: int, key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def edit(body: dict[str, Any]) -> None:
        body["verdicts"][index][key] = value

    return edit


def _drop_entry_key(index: int, key: str) -> Callable[[dict[str, Any]], None]:
    def edit(body: dict[str, Any]) -> None:
        del body["verdicts"][index][key]

    return edit


def _keep_verdicts(*indices: int) -> Callable[[dict[str, Any]], None]:
    def edit(body: dict[str, Any]) -> None:
        body["verdicts"] = [body["verdicts"][i] for i in indices]

    return edit


def _both_failed(body: dict[str, Any]) -> None:
    for verdict in body["verdicts"]:
        verdict["passed"] = False


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(_set("commit", OTHER), id="another-commit"),
        pytest.param(_set("commit", COMMIT[:12]), id="short-commit"),
        pytest.param(_set("commit", None), id="null-commit"),
        pytest.param(_drop("commit"), id="no-commit"),
        pytest.param(_set("split", "dev"), id="another-split"),
        pytest.param(_drop("split"), id="no-split"),
        pytest.param(_set("seed", 2), id="another-seed"),
        pytest.param(_drop("seed"), id="no-seed"),
        pytest.param(_set("schema", "sciagent.p4.naming-null/0"), id="another-schema"),
        pytest.param(_drop("schema"), id="no-schema"),
        pytest.param(_drop("verdicts"), id="no-verdicts"),
        pytest.param(_set("verdicts", {}), id="verdicts-not-a-list"),
        pytest.param(_set("verdicts", ["passed", "passed"]), id="verdicts-of-text"),
        pytest.param(_keep_verdicts(), id="no-truth"),
        pytest.param(_keep_verdicts(0), id="in-half-only"),
        pytest.param(_keep_verdicts(1), id="out-half-only"),
        pytest.param(_keep_verdicts(0, 1, 1), id="three-entries"),
        pytest.param(_keep_verdicts(0, 0), id="in-half-twice"),
        pytest.param(_entry(1, "half", "in"), id="same-half"),
        pytest.param(_entry(1, "truth", "test-000"), id="same-truth"),
        pytest.param(_entry(0, "truth", 0), id="truth-not-text"),
        pytest.param(_entry(0, "passed", False), id="in-half-failed"),
        pytest.param(_entry(1, "passed", False), id="out-half-failed"),
        pytest.param(_both_failed, id="both-failed"),
        pytest.param(_entry(0, "passed", "true"), id="passed-as-text"),
        pytest.param(_entry(1, "passed", 1), id="passed-as-one"),
        pytest.param(_entry(1, "passed", None), id="passed-null"),
        pytest.param(_drop_entry_key(0, "passed"), id="no-passed"),
        pytest.param(_drop_entry_key(0, "truth"), id="no-truth-id"),
        pytest.param(_drop_entry_key(1, "half"), id="no-half"),
    ],
)
def test_anything_but_a_pass_on_both_truths_at_the_commit_is_false(
    record_module: ModuleType,
    tmp_path: Path,
    edit: Callable[[dict[str, Any]], None],
) -> None:
    body = _body(record_module)
    edit(body)
    path = _written(tmp_path / "r.json", body)
    assert record_module.read_naming_null(COMMIT, path=path) is False


def test_an_empty_expected_commit_matches_nothing(
    record_module: ModuleType, tmp_path: Path
) -> None:
    body = _body(record_module)
    body["commit"] = ""
    path = _written(tmp_path / "r.json", body)
    assert record_module.read_naming_null("", path=path) is False


def test_a_dev_record_reads_true_only_when_dev_is_asked_for(
    record_module: ModuleType, tmp_path: Path
) -> None:
    path = _written(tmp_path / "r.json", _body(record_module, "dev"))
    assert record_module.read_naming_null(COMMIT, split="dev", path=path) is True
    assert record_module.read_naming_null(COMMIT, path=path) is False


def test_the_reader_imports_without_the_runner_or_numpy() -> None:
    code = (
        "import sys, naming_null_record\n"
        "assert 'p4_naming_null' not in sys.modules\n"
        "assert 'numpy' not in sys.modules\n"
        "assert 'sciagent' not in sys.modules\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_no_option_sets_the_value_or_the_commit(runner: ModuleType) -> None:
    parser = runner.build_parser()
    dests = {a.dest for a in parser._actions}
    assert dests == {"help", "split", "record"}


# --------------------------------------------------------------------------
# The record is written once
# --------------------------------------------------------------------------


def test_write_record_refuses_an_existing_record_and_keeps_its_bytes(
    record_module: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "deep" / "er" / "r.json"
    first = _body(record_module)
    record_module.write_record(path, first)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        record_module.write_record(path, {**first, "commit": OTHER})
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["r.json"]


def test_write_record_gives_what_the_reader_passes(
    record_module: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "r.json"
    record_module.write_record(path, _body(record_module))
    assert record_module.read_naming_null(COMMIT, path=path) is True
    assert path.read_bytes().endswith(b"}\n")


def test_write_record_bytes_do_not_depend_on_key_order(
    record_module: ModuleType, tmp_path: Path
) -> None:
    body = _body(record_module)
    record_module.write_record(tmp_path / "a.json", body)
    record_module.write_record(tmp_path / "b.json", dict(reversed(body.items())))
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_write_record_refuses_a_number_json_cannot_hold(
    record_module: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "r.json"
    with pytest.raises(ValueError, match="JSON"):
        record_module.write_record(path, {"gap": math.nan})
    assert list(tmp_path.iterdir()) == []


def _refused(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("the runner went on past its refusal")

    monkeypatch.setattr(runner, "run_truth", boom)
    assert runner.main(argv) == 2


def test_main_refuses_an_existing_record_before_anything_runs(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "r.json"
    path.write_text("kept", encoding="utf-8")
    _refused(runner, monkeypatch, ["--split", str(SPLIT), "--record", str(path)])
    assert path.read_text(encoding="utf-8") == "kept"
    assert "exists" in capsys.readouterr().err


def test_main_refuses_a_dangling_link_at_the_record_path(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "r.json"
    path.symlink_to(tmp_path / "nowhere.json")
    _refused(runner, monkeypatch, ["--split", str(SPLIT), "--record", str(path)])


def test_main_needs_a_split(runner: ModuleType) -> None:
    with pytest.raises(SystemExit) as stop:
        runner.main([])
    assert stop.value.code == 2


def test_main_refuses_a_dirty_tree_before_anything_runs(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The record's commit must describe the code that ran."""

    def dirty() -> str:
        raise CampaignError("the working tree at x is not clean")

    monkeypatch.setattr(runner, "code_fingerprint", dirty)
    path = tmp_path / "r.json"
    _refused(runner, monkeypatch, ["--split", str(SPLIT), "--record", str(path)])
    assert not path.exists()
    assert "not clean" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Where a record may go
# --------------------------------------------------------------------------


def test_a_test_split_writes_the_constant_and_nowhere_else(
    runner: ModuleType, record_module: ModuleType, tmp_path: Path
) -> None:
    """The miss script reads the constant, and test readings stay out of the
    repository: a test-split record at any other path has no reader."""
    test = tuple(replace(r, split="test") for r in _records())
    assert runner.check_destination(test, record_module.NAMING_NULL_RECORD) == "test"
    with pytest.raises(ScoringError, match="record"):
        runner.check_destination(test, tmp_path / "r.json")


def test_a_dev_run_stays_off_the_constant(
    runner: ModuleType, record_module: ModuleType, tmp_path: Path
) -> None:
    """It would pass as control 4 at ``T``, and block the real run for good."""
    assert runner.check_destination(_records(), tmp_path / "r.json") == "dev"
    with pytest.raises(ScoringError, match="record"):
        runner.check_destination(_records(), record_module.NAMING_NULL_RECORD)


def test_a_link_to_the_constant_is_the_constant(
    runner: ModuleType, record_module: ModuleType, tmp_path: Path
) -> None:
    link = tmp_path / "r.json"
    link.symlink_to(record_module.NAMING_NULL_RECORD)
    with pytest.raises(ScoringError, match="record"):
        runner.check_destination(_records(), link)


def test_a_mixed_or_unknown_split_is_refused(
    runner: ModuleType, record_module: ModuleType, tmp_path: Path
) -> None:
    records = _records()
    mixed = (replace(records[0], split="test"), *records[1:])
    with pytest.raises(ScoringError, match="split"):
        runner.check_destination(mixed, tmp_path / "r.json")
    with pytest.raises(ScoringError, match="split"):
        runner.check_destination((), tmp_path / "r.json")


# --------------------------------------------------------------------------
# Gaps and the verdict's form
# --------------------------------------------------------------------------


def _cell(ok: bool, reading: dict[str, float]) -> UnitResult:
    return UnitResult("AG-c-S/named", ok, reading, {}, 0.0)


def test_a_gap_is_the_cell_against_the_oracle_cell(runner: ModuleType) -> None:
    """``analysis.derive``'s rule: ``per_event - oracle`` on one (truth, seed)."""
    gap = runner.cell_gap(
        _cell(True, {"per_event": -1.25}), _cell(True, {"per_event": -1.0})
    )
    assert gap == -0.25


@pytest.mark.parametrize(
    ("cell", "oracle"),
    [
        (_cell(False, {}), _cell(True, {"per_event": -1.0})),
        (_cell(True, {"status": 2.0}), _cell(True, {"per_event": -1.0})),
        (_cell(True, {"per_event": -1.0}), _cell(False, {})),
        (_cell(True, {"per_event": -1.0}), _cell(True, {"status": 2.0})),
        (_cell(False, {"per_event": -1.0}), _cell(True, {"per_event": -1.0})),
    ],
    ids=["failed", "no-reading", "failed-oracle", "oracle-no-reading", "failed-read"],
)
def test_a_cell_without_a_reading_gives_a_gap_control_4_cannot_pass(
    runner: ModuleType, cell: UnitResult, oracle: UnitResult
) -> None:
    assert math.isnan(runner.cell_gap(cell, oracle))


def _verdict(gap: float, passed: bool) -> NamingNullVerdict:
    fact = SessionFacts(
        "link=exp; Trend", "abc", (FitFact("def", ("obs", "e0"), -1234.5625),)
    )
    return NamingNullVerdict(
        passed,
        NAMING_CONDITIONS,
        tuple((c, fact) for c in NAMING_CONDITIONS),
        tuple((c, gap) for c in NAMING_CONDITIONS),
        () if passed else ("gap: anon nan != nan",),
    )


def test_a_verdict_is_written_with_exact_floats(runner: ModuleType) -> None:
    body = runner.verdict_json("test-000", "in", _verdict(-0.1, True))
    assert body["truth"] == "test-000"
    assert body["half"] == "in"
    assert body["passed"] is True
    assert body["mismatches"] == []
    assert body["conditions"] == list(NAMING_CONDITIONS)
    assert body["gaps"]["anon"] == (-0.1).hex()
    assert float.fromhex(body["gaps"]["anon"]) == -0.1
    fit = body["facts"]["named"]["fits"][0]
    assert fit["log_likelihood"] == (-1234.5625).hex()
    assert fit["data_ids"] == ["obs", "e0"]


def test_a_failed_verdict_keeps_its_reasons_and_survives_nan(
    runner: ModuleType, record_module: ModuleType, tmp_path: Path
) -> None:
    body = runner.verdict_json("test-001", "out", _verdict(math.nan, False))
    assert body["passed"] is False
    assert body["mismatches"] == ["gap: anon nan != nan"]
    assert body["gaps"]["named"] == "nan"
    path = tmp_path / "r.json"
    record_module.write_record(path, body)
    assert json.loads(path.read_text(encoding="utf-8"))["passed"] is False


# --------------------------------------------------------------------------
# A session control 4 cannot read is refused (red team 2026-11-07, finding 3)
# --------------------------------------------------------------------------


def _outcome(
    *,
    retryable: bool = False,
    void: float | None = 0.0,
    outcome: str | None = "submitted",
) -> SessionOutcome:
    reading = {} if void is None else {"status": 1.0, "void": void}
    detail: dict[str, Any] = {"record": "investigation-x.json"}
    if outcome is not None:
        detail["outcome"] = outcome
    unit = Unit("dev-000", 1, "AG-c-S/named")
    return SessionOutcome(unit, retryable, reading, detail, "s", (), 0.0, {})


def test_a_submitted_unvoided_session_is_read(runner: ModuleType) -> None:
    runner.check_session(_outcome())


@pytest.mark.parametrize(
    "done",
    [
        pytest.param(_outcome(retryable=True), id="retryable"),
        pytest.param(_outcome(void=1.0), id="void"),
        pytest.param(_outcome(void=None), id="no-void-reading"),
        pytest.param(_outcome(outcome="aborted", void=1.0), id="aborted"),
        pytest.param(_outcome(outcome="max_turns"), id="max-turns"),
        pytest.param(_outcome(outcome="wall_time"), id="wall-time"),
        pytest.param(_outcome(outcome="ended_without_submit"), id="no-submit"),
        pytest.param(_outcome(outcome=None), id="no-outcome"),
    ],
)
def test_a_session_that_is_void_or_did_not_submit_is_refused(
    runner: ModuleType, done: SessionOutcome
) -> None:
    with pytest.raises(ScoringError):
        runner.check_session(done)


def test_run_condition_refuses_a_void_session_before_reading_it(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No record is loaded and no score cell is computed: the run stops, so no
    control 4 record is written and the answer stays False."""
    import sciagent.campaign.llm as llm

    records = _records()
    (truth,) = runner.truth_entries(records[:1])
    config = runner.planted_config()
    monkeypatch.setattr(llm, "run_session", lambda *a, **k: _outcome(void=1.0))
    with pytest.raises(ScoringError, match="void"):
        runner.run_condition(
            config.llm_arms[0],
            truth,
            runner.pointproc_environment(records),
            config,
            tmp_path,
        )


# --------------------------------------------------------------------------
# One real run on dev truths
# --------------------------------------------------------------------------


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _session(sessions: Path, truth: str, condition: str) -> SessionRecord:
    (found,) = (sessions / truth / condition).glob("investigation-*.json")
    return SessionRecord.load(found)


@pytest.mark.slow
def test_a_real_run_end_to_end_on_dev_truths(
    runner: ModuleType,
    record_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` once, about 9 CPU minutes, so everything that needs a real record
    is this one test: a shared fixture would run again on each ``-n`` worker.
    The tree may be dirty here, so the fingerprint alone is stubbed."""
    _clean(monkeypatch)
    monkeypatch.setattr(
        runner, "code_fingerprint", lambda: f"commit={COMMIT},tree=clean"
    )
    path = tmp_path / "naming-null.json"
    argv = ["--split", str(SPLIT), "--record", str(path)]
    assert runner.main(argv) == 0

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["schema"] == record_module.SCHEMA
    assert body["commit"] == COMMIT
    assert body["split"] == "dev"
    assert body["seed"] == 1
    assert [(v["truth"], v["half"]) for v in body["verdicts"]] == [
        ("dev-000", "in"),
        ("dev-001", "out"),
    ]
    for verdict in body["verdicts"]:
        assert verdict["passed"] is True, verdict["mismatches"]
        assert verdict["conditions"] == list(NAMING_CONDITIONS)
        gaps = [float.fromhex(verdict["gaps"][c]) for c in NAMING_CONDITIONS]
        assert all(math.isfinite(g) for g in gaps)
        assert len({g.hex() for g in gaps}) == 1
        named = verdict["facts"]["named"]
        assert len(named["fits"]) == 4
        assert [f["data_ids"] for f in named["fits"]] == [
            ["obs"],
            ["obs"],
            ["obs", "e0"],
            ["obs", "e0"],
        ]
        assert named["submission"] is not None

    # The reader: true at its commit and split, and nowhere else.
    read = record_module.read_naming_null
    assert read(COMMIT, split="dev", path=path) is True
    assert read(OTHER, split="dev", path=path) is False
    assert read(COMMIT, path=path) is False

    # A second run is refused and leaves the bytes.
    before = path.read_bytes()
    assert runner.main(argv) == 2
    assert path.read_bytes() == before

    # The negative case, on real sessions at no further simulation: a condition
    # that saw another truth's world fails the control, and so does the record.
    sessions = tmp_path / "naming-null-sessions"
    crossed = {
        "named": _session(sessions, "dev-000", "named"),
        "rescaled": _session(sessions, "dev-001", "rescaled"),
        "anon": _session(sessions, "dev-000", "anon"),
    }
    verdict = naming_null(crossed, dict.fromkeys(NAMING_CONDITIONS, 0.0))
    assert not verdict.passed
    assert any("rescaled" in m for m in verdict.mismatches), verdict.mismatches
    failed = {
        **body,
        "verdicts": [
            runner.verdict_json("dev-000", "in", verdict),
            body["verdicts"][1],
        ],
    }
    bad = tmp_path / "failed.json"
    record_module.write_record(bad, failed)
    assert read(COMMIT, split="dev", path=bad) is False
