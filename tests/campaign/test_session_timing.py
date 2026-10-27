"""Session wall time lives in ``timing.jsonl``, never in the session cell.

LOG 2026-10-27 ("Session wall time leaves the ``llm-session`` cell"): the
record's ``timing`` section is not hashed, so a cell holding any of it would
differ between two runs of the same record. The session outcome carries those
values beside the cell (:attr:`SessionOutcome.timing`), the driver writes them
to the cell's timing line, and the pilot report reads session wall times from
there, refusing a session cell written in the old format.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from audit_support import put_session, unit_json
from campaign_support import entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign import llm
from sciagent.campaign.analysis import Session
from sciagent.campaign.llm import SESSION_TIMING_FIELDS, run_session
from sciagent.campaign.plan import CampaignError, Unit
from sciagent.campaign.store import TIMING_FILE, ResultStore
from sciagent.campaign.voids import VoidStatus
from sciagent.harness.errors import HarnessFaultError
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import ScriptedDriver

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
#: The values the record's unhashed ``timing`` section holds.
UNHASHED = ("wall_s", "duration_api_ms", "total_cost_usd")


def test_the_timing_fields_are_the_harness_time_and_the_api_figures() -> None:
    assert SESSION_TIMING_FIELDS == (
        "harness_wall_s",
        "duration_api_ms",
        "total_cost_usd",
    )


def test_a_completed_session_keeps_its_times_out_of_the_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    config = tiny_config(seeds=(1,))
    unit = Unit("size_excitation", 1, "AG-c-H/named")
    out = run_session(
        unit,
        entry("size_excitation"),
        environment(),
        config,
        tmp_path,
        driver=ScriptedDriver(SCRIPT),
    )
    assert not out.retryable and out.detail["outcome"] == "submitted"
    assert not set(UNHASHED) & set(out.reading)
    assert not set(UNHASHED) & set(out.detail)
    assert tuple(out.timing) == SESSION_TIMING_FIELDS
    harness = out.timing["harness_wall_s"]
    assert isinstance(harness, float) and 0.0 <= harness <= out.wall_s + 1e-6
    assert out.timing["duration_api_ms"] == 0  # the scripted driver's result


@pytest.mark.parametrize("harness_wall_s", [2.5, None])
def test_an_aborted_session_states_its_harness_time_or_its_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness_wall_s: float | None
) -> None:
    record = SessionRecord()
    record.append("config", {"model": "m"}, elapsed_s=0.0)
    if harness_wall_s is not None:
        record.set_timing("wall_s", harness_wall_s)

    def aborts(*args: object, **kwargs: object) -> None:
        raise HarnessFaultError("a tool handler raised KeyError", record=record)

    monkeypatch.setattr(llm, "run", aborts)
    unit = Unit("hawkes", 1, "AG-c-H/named")
    out = run_session(
        unit, entry("hawkes"), environment(), tiny_config(seeds=(1,)), tmp_path
    )
    assert out.detail["outcome"] == "aborted" and not out.retryable
    assert not set(UNHASHED) & set(out.reading)
    assert not set(UNHASHED) & set(out.detail)
    assert out.timing == {
        "harness_wall_s": harness_wall_s,
        "duration_api_ms": None,
        "total_cost_usd": None,
    }
    assert out.wall_s >= 0.0


# --------------------------------------------------------------------------
# The pilot report reads session wall times from timing.jsonl
# --------------------------------------------------------------------------


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` loaded in-process, with ``scripts/`` importable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "pilot_report", SCRIPTS / "pilot_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session(store: ResultStore, digest: str, seed: int) -> Session:
    cell = store.cell(digest)
    return Session(
        "dev-008",
        seed,
        "AG-o-S/named",
        {k: float.fromhex(v) for k, v in cell["reading"].items()},
        cell["detail"]["session"],
        VoidStatus(False, (), 0, False),
        str(cell["key"]["env_version"]),
        digest,
    )


def _timing_line(
    store: ResultStore, digest: str, seed: int, wall_s: float, **extra: object
) -> None:
    store.log_timing(
        {
            **unit_json("llm-session", "AG-o-S/named", "dev-008", seed),
            "digest": digest,
            "wall_s": wall_s,
            "attempt": 1,
            "harness_wall_s": None,
            "duration_api_ms": None,
            "total_cost_usd": None,
            **extra,
        }
    )


def test_the_report_reads_session_wall_times_from_the_timing_log(
    tmp_path: Path, report: ModuleType
) -> None:
    with ResultStore(tmp_path) as store:
        keys = [
            str(put_session(store, "AG-o-S/named", seed=s).digest) for s in (1, 2, 3)
        ]
        _timing_line(store, keys[0], 1, 30.0, harness_wall_s=29.5)
        _timing_line(store, keys[1], 2, 10.0)  # an abort: no harness time
        # Another kind's line is not a session's wall time.
        store.log_timing(
            {
                **unit_json("baseline", "B-lib", "dev-008", 1),
                "digest": "0" * 64,
                "ok": True,
                "wall_s": 5.0,
            }
        )
        sessions = [_session(store, k, s) for k, s in zip(keys, (1, 2, 3), strict=True)]
    walls = report.session_wall_times(tmp_path, sessions)
    # The third session has no timing line: it is absent, never counted as 0.
    assert walls == {keys[0]: 30.0, keys[1]: 10.0}
    assert report.median([walls[x.digest] for x in sessions if x.digest in walls]) == (
        20.0
    )


@pytest.mark.parametrize(
    ("reading", "session"),
    [
        ({"wall_s": 12.0}, {"total_cost_usd": 0.1, "duration_api_ms": 900}),
        ({"wall_s": 12.0}, {}),  # an old hard timeout: no detail times
        ({}, {"total_cost_usd": None, "duration_api_ms": None}),
    ],
)
def test_the_report_refuses_a_session_cell_in_the_old_format(
    tmp_path: Path,
    report: ModuleType,
    reading: dict[str, float],
    session: dict[str, object],
) -> None:
    with ResultStore(tmp_path) as store:
        key = put_session(store, "AG-o-S/named", reading=reading, session=session)
        old = str(key.digest)
        _timing_line(store, old, 1, 13.0)
        sessions = [_session(store, old, 1)]
    with pytest.raises(CampaignError, match="old format"):
        report.session_wall_times(tmp_path, sessions)


def test_the_report_refuses_two_timing_lines_for_one_session(
    tmp_path: Path, report: ModuleType
) -> None:
    with ResultStore(tmp_path) as store:
        key = str(put_session(store, "AG-o-S/named").digest)
        _timing_line(store, key, 1, 13.0)
        _timing_line(store, key, 1, 14.0)
        sessions = [_session(store, key, 1)]
    with pytest.raises(CampaignError, match="timing lines"):
        report.session_wall_times(tmp_path, sessions)


def test_the_driver_writes_the_session_times_to_the_timing_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_audit_driver import run_campaign

    root = run_campaign(tmp_path / "run", monkeypatch)
    lines = [
        json.loads(line)
        for line in (root / TIMING_FILE).read_text(encoding="utf-8").splitlines()
    ]
    (line,) = [x for x in lines if x["kind"] == "llm-session"]
    assert {"wall_s", "attempt", *SESSION_TIMING_FIELDS} <= set(line)
    assert isinstance(line["harness_wall_s"], float)
    assert line["harness_wall_s"] <= line["wall_s"] + 1e-3
    with ResultStore(root) as store:
        cell = store.cell(line["digest"])
    assert not set(UNHASHED) & set(cell["reading"])
    assert not set(UNHASHED) & set(cell["detail"]["session"])
