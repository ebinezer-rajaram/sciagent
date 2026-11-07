"""``pilot_report.py`` refuses a store with non-dev truths and no ``reads.json``.

Prereg 2026-11-05 §2.8: on P4 the first reading of the store is the terminal
step, and the report prints gap means and calls ``analysis.verdict``, so until
``reads.json`` exists it must not run on anything but dev truths. Whether the
truths are dev is the truth records' ``split``, the field the prompt freeze
reads (``campaign/freeze.py``). The file is looked for at the constant output
path (§2.10), not in the store, which §2.8 leaves untouched; only its existence
is read, and no option names it.

A relabelled record cannot get round this: the truth's digest hashes the record,
``split`` included, and the digest is in every address the report reads.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from sciagent.campaign import analysis
from sciagent.campaign import store as store_module
from sciagent.campaign.store import ResultStore
from sciagent.scenarios.records import read_split, records_digest, records_json

ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPTS: Final = ROOT / "scripts"
SPLIT: Final = ROOT / "data" / "v2" / "truths" / "dev"


class Reached(Exception):
    """The report got as far as reading the store."""


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("pilot_report")


def _relabelled(tmp_path: Path, labels: Mapping[str, str]) -> Path:
    """A copy of the dev split with some truths' ``split`` changed."""
    records, manifest = read_split(SPLIT)
    changed = tuple(replace(r, split=labels.get(r.id, r.split)) for r in records)
    out = tmp_path / "split"
    out.mkdir()
    (out / "truths.json").write_text(records_json(changed), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps({**manifest, "records_sha256": records_digest(changed)}),
        encoding="utf-8",
    )
    return out


def _all_test(tmp_path: Path) -> Path:
    records, _ = read_split(SPLIT)
    return _relabelled(tmp_path, {r.id: "test" for r in records})


def _argv(root: Path, split: Path, out: Path, *extra: str) -> list[str]:
    return [
        "pilot_report.py",
        *("--root", str(root), "--split", str(split), "--out", str(out)),
        *("--seeds", "1", "--resamples", "50"),
        *extra,
    ]


def _main(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    split: Path,
    reads: Path,
) -> None:
    """``main()`` on an empty store; reading the store raises :class:`Reached`."""
    root = tmp_path / "store"
    with ResultStore(root):
        pass

    def reached(*_: Any, **__: Any) -> Any:
        raise Reached

    monkeypatch.setattr(sys, "argv", _argv(root, split, tmp_path / "PILOT.md"))
    monkeypatch.setattr(report, "P4_READS", reads)
    monkeypatch.setattr(analysis, "load_rows", reached)
    report.main()


def _assert_refused(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    split: Path,
    *extra: str,
) -> None:
    """Exit 2 naming the file, with the store unopened and nothing written."""
    root = tmp_path / "store"
    with ResultStore(root):
        pass

    def opened(*_: Any, **__: Any) -> Any:
        raise AssertionError("the store was opened before the refusal")

    out = tmp_path / "PILOT.md"
    monkeypatch.setattr(store_module, "ResultStore", opened)
    monkeypatch.setattr(sys, "argv", _argv(root, split, out, *extra))
    monkeypatch.setattr(report, "P4_READS", tmp_path / "reads.json")
    with pytest.raises(SystemExit) as stop:
        report.main()
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert "reads.json" in err and "non-dev" in err
    assert not out.exists()


def test_the_constant_is_the_miss_scripts_output_path(report: ModuleType) -> None:
    assert (
        Path("/home/ubuntu/research/records/sciagent/p4-search-misses-F2/reads.json")
        == report.P4_READS
    )


def test_a_non_dev_split_without_reads_is_refused(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _assert_refused(report, monkeypatch, tmp_path, capsys, _all_test(tmp_path))


def test_one_non_dev_truth_is_enough(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    split = _relabelled(tmp_path, {"dev-019": "test"})
    _assert_refused(report, monkeypatch, tmp_path, capsys, split)


def test_an_unknown_split_counts_as_outside_dev(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    split = _relabelled(tmp_path, {"dev-000": "holdout"})
    _assert_refused(report, monkeypatch, tmp_path, capsys, split)


@pytest.mark.parametrize(
    "extra",
    [("--ids", "dev-000"), ("--truths", "0"), ("--interim",)],
    ids=["ids", "no-truths", "interim"],
)
def test_no_selection_or_mode_gets_past_the_refusal(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
) -> None:
    """``--ids`` picks a dev truth out of a split that holds a test one, and
    ``--truths 0`` picks none: the whole split is what is asked about."""
    split = _relabelled(tmp_path, {"dev-019": "test"})
    _assert_refused(report, monkeypatch, tmp_path, capsys, split, *extra)


def test_a_non_dev_split_with_reads_gets_past_this_refusal_to_the_block_one(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With ``reads.json`` the next stop is that a split outside dev needs
    ``--p4-block`` (red team 2026-11-07, finding 4). A block needs P4's 40 truths,
    which dev has not, so a block run that reaches the store is in
    ``test_pilot_report_models``, on stand-in truths."""
    reads = tmp_path / "reads.json"
    reads.write_text("not read", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        _main(report, monkeypatch, tmp_path, _all_test(tmp_path), reads)
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert "--p4-block" in err
    assert "reads.json" not in err


def test_a_dev_split_goes_on_to_the_store(
    report: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(Reached):
        _main(report, monkeypatch, tmp_path, SPLIT, tmp_path / "reads.json")


def test_reads_json_is_a_file_not_a_directory(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reads = tmp_path / "reads.json"
    reads.mkdir()
    with pytest.raises(SystemExit) as stop:
        _main(report, monkeypatch, tmp_path, _all_test(tmp_path), reads)
    assert stop.value.code == 2


def test_a_reads_json_in_the_store_does_not_count(
    report: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§2.8 leaves the store untouched, so a file there is nobody's reads."""
    root = tmp_path / "store"
    with ResultStore(root):
        pass
    (root / "reads.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        _main(
            report,
            monkeypatch,
            tmp_path,
            _all_test(tmp_path),
            tmp_path / "absent.json",
        )
    assert stop.value.code == 2


def test_a_dev_split_needs_no_reads(
    report: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(Reached):
        _main(report, monkeypatch, tmp_path, SPLIT, tmp_path / "absent.json")


def test_no_option_names_the_reads_file(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["pilot_report.py", "--help"])
    with pytest.raises(SystemExit) as stop:
        report.main()
    assert stop.value.code == 0
    assert "reads" not in capsys.readouterr().out.lower()


def test_relabelling_a_truth_moves_its_digest_so_no_cell_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the record's ``split`` can be trusted: every address the report reads
    carries this digest, so a test truth relabelled dev reads no test cell."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    pilot = importlib.import_module("pilot")
    records, _ = read_split(SPLIT)
    record = records[0]
    (kept,) = pilot.truth_entries((record,))
    (moved,) = pilot.truth_entries((replace(record, split="test"),))
    assert kept.split == "dev" and moved.split == "test"
    assert kept.digest != moved.digest
