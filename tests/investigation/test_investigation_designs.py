"""Design generators in the tool layer (P4 Δ3).

``run_experiment`` reads a *design*: the experiment JSON with generators for
forced times (``{start, step, n}``) and patterns for marks, which
:func:`~sciagent.glm.designs.expand` turns into explicit arrays before
anything else sees them. The record holds only the arrays, so a generator
design and its explicit-array form are the same tool call, byte for byte.
AG-o can also name an intervention it wrote to ``/work`` (``{"from": ...}``);
the file is read under strict rules and its digest is recorded, so replay,
which recreates the file by re-running the sandbox calls, checks it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from investigation_support import POINTPROC, clean_environment, lab, scripted_run

from sciagent.glm.designs import DESIGN_SCHEMA
from sciagent.harness.errors import ReplayDivergenceError
from sciagent.harness.record import SessionRecord, digest
from sciagent.harness.scripted import Step
from sciagent.investigation import Lab, replay_investigation
from sciagent.investigation.tools import BUDGET_EXPERIMENTS, MAX_DESIGN_FILE_BYTES
from sciagent.investigation.view import Condition

#: Every 2.5 time units from 5, 40 events, size alternating 0.5 / 2.
GENERATOR: Final[dict[str, Any]] = {
    "intervention": {
        "type": "force_events",
        "times": {"start": 5.0, "step": 2.5, "n": 40},
        "marks": {"size": {"alternate": [0.5, 2.0]}, "sign": {"constant": 1.0}},
    },
    "horizon": 200.0,
}
ARRAYS: Final[dict[str, Any]] = {
    "intervention": {
        "type": "force_events",
        "times": [5.0 + k * 2.5 for k in range(40)],
        "marks": {
            "size": [0.5 if k % 2 == 0 else 2.0 for k in range(40)],
            "sign": [1.0] * 40,
        },
    },
    "horizon": 200.0,
}
#: The intervention alone, as AG-o would write it to a file.
FILE_INTERVENTION: Final = GENERATOR["intervention"]


def _published(schema: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in schema.items() if k not in ("$schema", "title")}


def _run_schema(the_lab: Lab) -> dict[str, Any]:
    spec = next(s for s in the_lab.layer().schemas() if s["name"] == "run_experiment")
    return dict(spec["input_schema"])


def _sandbox_lab(tmp_path: Path, **kwargs: Any) -> Lab:
    """An AG-o lab whose sandbox is never started (the image is only named)."""
    return lab(
        "AG-o",
        sandbox_root=tmp_path,
        image="sciagent-sandbox:unused",
        **kwargs,
    )


def _write(the_lab: Lab, name: str, payload: bytes) -> Path:
    assert the_lab.sandbox is not None
    path = the_lab.sandbox.work_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _from(path: str, horizon: float = 200.0) -> dict[str, Any]:
    return {"intervention": {"from": path}, "horizon": horizon}


# -- the schema ---------------------------------------------------------------


def test_the_constrained_tool_publishes_the_design_schema() -> None:
    assert _run_schema(lab("AG-c")) == _published(DESIGN_SCHEMA)
    # The anonymised condition publishes the same schema: it carries no
    # time-valued constant, so there is nothing to convert.
    assert _run_schema(lab("AG-c", "anon")) == _published(DESIGN_SCHEMA)


def test_the_open_tool_also_accepts_a_work_file(tmp_path: Path) -> None:
    schema = _run_schema(_sandbox_lab(tmp_path))
    base = _published(DESIGN_SCHEMA)
    assert {k: v for k, v in schema.items() if k != "properties"} == {
        k: v for k, v in base.items() if k != "properties"
    }
    options = schema["properties"]["intervention"]["oneOf"]
    assert options[0] == base["properties"]["intervention"]
    assert options[1]["required"] == ["from"]
    assert options[1]["additionalProperties"] is False


# -- generators -----------------------------------------------------------------


@pytest.mark.parametrize("condition", ["named", "anon"])
def test_a_generator_and_its_arrays_give_byte_identical_results(
    condition: Condition,
) -> None:
    outputs = []
    for design in (GENERATOR, ARRAYS):
        the_lab = lab("AG-c", condition)
        c = the_lab.view.time(1.0)
        args = _to_agent(design, c, condition)
        layer = the_lab.layer()
        out = layer.call("run_experiment", args)
        assert not out.is_error, out.text
        assert layer.meter(BUDGET_EXPERIMENTS).used == 1
        outputs.append(out)
    generated, explicit = outputs
    assert generated.as_json() == explicit.as_json()
    assert generated.digest == explicit.digest
    # The record holds the expanded arrays (native units), never a generator.
    assert generated.record is not None
    design = json.loads(generated.record["design"])
    assert design["times"] == ARRAYS["intervention"]["times"]
    assert design["marks"]["size"] == ARRAYS["intervention"]["marks"]["size"]


def _to_agent(design: dict[str, Any], c: float, condition: str) -> dict[str, Any]:
    if condition == "named":
        return design
    out = copy.deepcopy(design)
    node = out["intervention"]
    times = node["times"]
    if isinstance(times, dict):
        times["start"] *= c
        times["step"] *= c
    else:
        node["times"] = [t * c for t in times]
    node["marks"] = {
        {"size": "m1", "sign": "m2"}[k]: v for k, v in node["marks"].items()
    }
    out["horizon"] *= c
    return out


def test_a_pattern_that_does_not_tile_is_refused_uncharged() -> None:
    layer = lab("AG-c").layer()
    bad = copy.deepcopy(GENERATOR)
    bad["intervention"]["marks"] = {"size": {"repeat": [1.0, 2.0, 3.0]}}
    out = layer.call("run_experiment", bad)
    assert out.is_error
    assert "does not tile 40 times" in out.text, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0


# -- from: a file in /work (AG-o only) ----------------------------------------


def test_the_constrained_arm_cannot_read_a_file() -> None:
    layer = lab("AG-c").layer()
    out = layer.call("run_experiment", _from("/work/x.json"))
    assert out.is_error
    assert "only with the python sandbox" in out.text, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0


def test_a_work_file_runs_the_design_it_holds_and_records_its_digest(
    tmp_path: Path,
) -> None:
    payload = json.dumps(FILE_INTERVENTION).encode()
    from_file = _sandbox_lab(tmp_path / "a")
    _write(from_file, "designs/x.json", payload)
    layer = from_file.layer()
    out = layer.call("run_experiment", _from("/work/designs/x.json"))
    assert not out.is_error, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 1
    assert out.record is not None
    assert out.record["design_file"] == {
        "path": "/work/designs/x.json",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }
    # The same experiment as the inline design: only the file's provenance
    # is added to the record.
    inline = _sandbox_lab(tmp_path / "b").layer().call("run_experiment", GENERATOR)
    assert inline.record is not None
    assert out.text == inline.text
    assert {k: v for k, v in out.record.items() if k != "design_file"} == dict(
        inline.record
    )


def test_a_changed_file_changes_the_digest(tmp_path: Path) -> None:
    """What replay relies on: the recorded digest pins the file's bytes."""
    digests = []
    for k, sign in enumerate((1.0, -1.0)):
        design = copy.deepcopy(FILE_INTERVENTION)
        design["marks"]["sign"] = {"constant": sign}
        the_lab = _sandbox_lab(tmp_path / str(k))
        _write(the_lab, "x.json", json.dumps(design).encode())
        out = the_lab.layer().call("run_experiment", _from("/work/x.json"))
        assert not out.is_error, out.text
        digests.append(out.digest)
    assert digests[0] != digests[1]


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/data/obs.csv", "must be under /work/"),
        ("/work/../data/obs.csv", "must be under /work/"),
        ("/work/./x.json", "must be under /work/"),
        ("work/x.json", "must be under /work/"),
        ("/work/a\\b.json", "must be under /work/"),
        ("/work/", "must be under /work/"),
        ("/work/missing.json", "no file /work/missing.json"),
        ("/work/folder", "not a regular file"),
    ],
)
def test_a_path_outside_work_or_not_a_file_is_refused(
    tmp_path: Path, path: str, message: str
) -> None:
    the_lab = _sandbox_lab(tmp_path)
    assert the_lab.sandbox is not None
    (the_lab.sandbox.work_dir / "folder").mkdir()
    layer = the_lab.layer()
    out = layer.call("run_experiment", _from(path))
    assert out.is_error
    assert message in out.text, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0


def test_a_link_in_the_path_is_refused(tmp_path: Path) -> None:
    the_lab = _sandbox_lab(tmp_path / "lab")
    assert the_lab.sandbox is not None
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.json").write_text(json.dumps(FILE_INTERVENTION))
    link = the_lab.sandbox.work_dir / "link"
    # A junction needs no privilege on Windows, where symbolic links do.
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        os.symlink(outside, link)
    layer = the_lab.layer()
    out = layer.call("run_experiment", _from("/work/link/x.json"))
    assert out.is_error
    assert "a link" in out.text, out.text
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0


def test_an_oversized_or_malformed_file_is_refused(tmp_path: Path) -> None:
    the_lab = _sandbox_lab(tmp_path)
    big = json.dumps(
        {"type": "compose", "parts": [], "pad": "x" * MAX_DESIGN_FILE_BYTES}
    )
    _write(the_lab, "big.json", big.encode())
    _write(the_lab, "bad.json", b"{not json")
    _write(the_lab, "deep.json", b"[" * 100_000 + b"]" * 100_000)
    _write(the_lab, "experiment.json", json.dumps(GENERATOR).encode())
    _write(
        the_lab,
        "nested.json",
        b'{"type": "compose", "parts": [{"from": "/work/x.json"}]}',
    )
    layer = the_lab.layer()
    cases = {
        "/work/big.json": f"larger than {MAX_DESIGN_FILE_BYTES} bytes",
        "/work/bad.json": "is not valid JSON",
        "/work/deep.json": "is not valid JSON",
        "/work/experiment.json": "must hold one intervention object",
        "/work/nested.json": "'type' must be a string",
    }
    for path, message in cases.items():
        out = layer.call("run_experiment", _from(path))
        assert out.is_error, path
        assert message in out.text, (path, out.text)
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0


def test_from_is_only_the_whole_intervention(tmp_path: Path) -> None:
    the_lab = _sandbox_lab(tmp_path)
    _write(the_lab, "x.json", json.dumps(FILE_INTERVENTION).encode())
    layer = the_lab.layer()
    out = layer.call(
        "run_experiment",
        {"intervention": {"from": "/work/x.json", "type": "compose"}},
    )
    assert out.is_error
    assert "exactly one key" in out.text, out.text
    out = layer.call(
        "run_experiment",
        {
            "intervention": {
                "type": "compose",
                "parts": [{"from": "/work/x.json"}],
            }
        },
    )
    assert out.is_error
    assert layer.meter(BUDGET_EXPERIMENTS).used == 0


# -- replay recreates the file (needs Docker) ------------------------------------

_WRITE_DESIGN: Final = (
    "import json\n"
    "design = {'type': 'force_events', "
    "'times': {'start': 5.0, 'step': 2.5, 'n': 40}, "
    "'marks': {'size': {'alternate': [0.5, 2.0]}}}\n"
    "open('/work/x.json', 'w').write(json.dumps(design))\n"
    "print('written')\n"
)


@pytest.mark.slow
def test_an_open_run_with_a_work_file_replays(
    sandbox_image: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    script = (
        Step(calls=(("python", {"code": _WRITE_DESIGN}),)),
        Step(calls=(("run_experiment", _from("/work/x.json")),)),
        Step(calls=(("submit", {"structure": "null", "report": "r"}),)),
    )
    outcome = scripted_run(
        script, tmp_path / "out", arm="AG-o", sandbox_root=tmp_path / "sbx"
    )
    assert outcome.outcome == "submitted", outcome
    assert outcome.experiments_used == 1
    record = SessionRecord.load(outcome.record_path)
    calls = {e.body["call"]: e.body for e in record.of_kind("tool_call")}
    assert not any(b["is_error"] for b in calls.values()), [
        b["text"] for b in calls.values() if b["is_error"]
    ]
    run = next(b for b in calls.values() if b["name"] == "run_experiment")
    assert len(run["record"]["design_file"]["sha256"]) == 64
    report = replay_investigation(record, POINTPROC, sandbox_root=tmp_path / "re")
    assert report.budgets_used == {"experiments": 1, "fits": 0, "submit": 1}
    # A record whose file digest was edited no longer replays.
    forged = _forge_digest(record, int(run["call"]))
    with pytest.raises(ReplayDivergenceError) as caught:
        replay_investigation(forged, POINTPROC, sandbox_root=tmp_path / "f")
    assert caught.value.step == run["call"]


def _forge_digest(record: SessionRecord, step: int) -> SessionRecord:
    """A consistently re-chained copy whose file digest was edited."""
    forged = SessionRecord()
    for entry in record.entries:
        body = json.loads(json.dumps(entry.body))
        if entry.kind == "tool_call" and body["call"] == step:
            body["record"]["design_file"]["sha256"] = "0" * 64
            body["digest"] = digest(
                {
                    "text": body["text"],
                    "is_error": body["is_error"],
                    "record": body["record"],
                }
            )
        forged.append(entry.kind, body)
    return forged
