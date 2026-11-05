"""The analysis scripts PILOT.md's notes cite (``scripts/p3_report/``).

They print figures for the notes, not numbers the evaluation reads, so these
are ordinary tests of what could go wrong quietly: the cross-check starting to
use the code it checks, its ``truths.json`` drifting from the split, the
nondeterminism rule moving after the replay was read, and the counts the notes
quote.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from audit_support import put_session

from sciagent.campaign.store import ResultStore
from sciagent.harness.record import SessionRecord
from sciagent.scenarios.records import read_split

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "p3_report"
SPLIT = ROOT / "data" / "v2" / "truths" / "dev"
ARM = "AG-o-S/named"


def load(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``scripts/p3_report/<name>.py`` as a module (registered: a dataclass
    looks its own module up by name)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found |= {node.module} | {f"{node.module}.{a.name}" for a in node.names}
    return found


def test_the_cross_check_imports_neither_rule_module() -> None:
    names = _imports(SCRIPTS / "independent_recompute.py")
    assert "sciagent.campaign.store" in names
    assert not {n for n in names if n.endswith(("analysis", "gonogo"))}, names


def test_truths_json_is_the_splits_dictionary_membership() -> None:
    records, _ = read_split(SPLIT)
    stated = json.loads((SCRIPTS / "truths.json").read_text(encoding="utf-8"))
    assert stated == {r.id: not r.out_of_dictionary for r in records}


@pytest.mark.parametrize(
    ("code", "kinds"),
    [
        ("t0=time.time()\nprint(time.time()-t0)", ["clock"]),
        ("from time import perf_counter\nperf_counter()", ["clock"]),
        ("datetime.now()", ["clock"]),
        ("rng=np.random.default_rng()", ["rng_noseed"]),
        ("rng=np.random.default_rng(1)", []),
        ("np.random.rand(3)", ["np_random_global"]),
        ("np.random.seed(0)\nnp.random.rand(3)", []),
        ("random.random()", ["py_random"]),
        ("random.seed(0)\nrandom.random()", []),
        ("rng.random(3)", []),
        ("os.urandom(4)", ["os_entropy"]),
        ("print(json.dumps(times))", []),
    ],
)
def test_the_nondeterminism_rule_as_fixed(
    code: str, kinds: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load("sandbox_nondeterminism", monkeypatch)
    assert script.candidate_kinds(code) == kinds


def test_the_run_log_reader_takes_session_lines_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load("sizing", monkeypatch)
    found = script.run_log_sessions(
        [
            "2026-10-29 12:12:57,446 llm-session AG-c-S/named dev-000 s1 submitted "
            "turns=17.0 out_tokens=2654.0 fits=10.0 exp=0.0 179s",
            "2026-10-29 12:12:58,000 llm-score AG-c-S/named dev-000 s1 ok 49s",
            "The executor did not finishing joining its threads within 300 seconds",
        ]
    )
    assert found == {
        ("AG-c-S/named", "dev-000", 1): script.Finished(
            "2026-10-29 12:12:57", 179, "submitted"
        )
    }


def test_turns_are_read_against_tool_uses_and_message_ids(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = load("sizing", monkeypatch)

    def session(outcome: str, messages: int, uses: int, result: bool) -> Any:
        turns = float(uses + 1) if result else 0.0
        return script.Session(
            "dev-000 s1", outcome, 1.0, 1.0, messages, uses, result, turns, 0.0
        )

    script.turns_against_messages(
        {
            "a": [session("submitted", 2, 3, True), session("submitted", 5, 14, True)],
            "b": [session("wall_time", 7, 7, False)],
        }
    )
    out = capsys.readouterr().out
    assert "turns == tool_uses + 1: 2 of 2 sessions with a count" in out
    assert "median per session 2.50, pooled 2.71" in out


def _session(
    store: ResultStore, truth: str, reading: dict[str, float]
) -> dict[str, Any]:
    """A session cell whose record has one answered fit, one fit no layer call
    answered, an unparsed and a parsed run_experiment, none answered."""
    key = put_session(
        store,
        ARM,
        truth=truth,
        make_record=False,
        reading={"predictions": 0.0, "submitted": 0.0, "tool_calls": 1.0, **reading},
        session={"submission": None, "report": None},
    )
    cell = store.cell(key.digest)
    detail = cell["detail"]["session"]
    record = SessionRecord()
    uses = [
        ("u1", "mcp__lab__fit", {}),
        ("u2", "mcp__lab__fit", {}),
        ("u3", "mcp__lab__run_experiment", {"__unparsedToolInput": {"len": 1}}),
        ("u4", "mcp__lab__run_experiment", {"horizon": 1}),
    ]
    record.append(
        "assistant",
        {
            "message_id": "m1",
            "content": [
                {"type": "tool_use", "id": i, "name": n, "input": x} for i, n, x in uses
            ],
        },
    )
    record.append(
        "tool_call",
        {"name": "fit", "tool_use_id": "u1", "charged": "fits", "call": 1},
    )
    record.append(
        "outcome", {"reason": "submitted", "tool_calls": 1, "submission": None}
    )
    path = store.root / detail["record_dir"] / detail["record"]
    path.parent.mkdir(parents=True)
    record.save(path)
    return cell


def test_the_audit_names_a_reading_the_record_does_not_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load("wall_time_audit", monkeypatch)
    root = tmp_path / "s"
    with ResultStore(root) as store:
        agrees = _session(store, "dev-001", {"fits_used": 1.0, "experiments_used": 0.0})
        differs = _session(
            store, "dev-002", {"fits_used": 2.0, "experiments_used": 0.0}
        )
    assert script.audit(agrees, root, None).disagreements == []
    assert script.audit(differs, root, None).disagreements == [
        "fits_used: cell 2.0 record 1.0"
    ]


def test_the_tally_counts_unanswered_tool_uses_by_tool_and_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = load("wall_time_audit", monkeypatch)
    root = tmp_path / "s"
    with ResultStore(root) as store:
        cell = _session(store, "dev-001", {"fits_used": 1.0, "experiments_used": 0.0})
    script.tally([script.audit(cell, root, None)])
    out = capsys.readouterr().out
    assert f"mcp__lab__fit {ARM}: 1" in out
    assert "by tool: {'mcp__lab__fit': 1, 'mcp__lab__run_experiment': 2}" in out
    assert "1 with __unparsedToolInput, 1 with parsed input" in out
