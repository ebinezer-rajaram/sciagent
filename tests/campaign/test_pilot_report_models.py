"""Named minus anon is printed for every model (SPEC §6.2, control room B2), so on a
P4 block the report gives it the merge of every config of ``p4_plan``: the Haiku and
Opus configs hold no ORACLE, and the Sonnet config no Haiku or Opus row.

Stand-in truths and tables only: ``load_rows`` is replaced per config, and the store
is an empty one, as in ``test_pilot_report_wording``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
from campaign_support import environment
from test_campaign_analysis import row
from test_table_merge import standin

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.contrasts import ScriptedControls, same_read
from sciagent.campaign.p4plan import p4_plan
from sciagent.campaign.plan import CampaignConfig, TruthEntry
from sciagent.campaign.secondaries import read_named_minus_anon
from sciagent.campaign.store import ResultStore
from sciagent.campaign.tables import TableSource, merge_tables

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
TRUTHS = standin()
READS = ("AG-c-H", "AG-c-O", "AG-c-S", "AG-o-H", "AG-o-O", "AG-o-S")


@pytest.fixture
def report(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``pilot_report`` in-process, on the stand-in split (no split file is read)."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "pilot_report", SCRIPTS / "pilot_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pilot = sys.modules["pilot"]
    for target in (module, pilot):
        monkeypatch.setattr(target, "load_split", lambda _: ())
        monkeypatch.setattr(target, "truth_entries", lambda _: TRUTHS)
    monkeypatch.setattr(module, "pointproc_environment", lambda _: environment())
    return module


def arms(letter: str) -> list[Row]:
    out: list[Row] = []
    for t in TRUTHS:
        for seed in (1, 2):
            for tier in ("c", "o"):
                for condition, v in (("named", -0.71), ("anon", -0.72)):
                    system = f"AG-{tier}-{letter}/{condition}+link-neutral"
                    out.append(row(t.id, seed, system, v, llm=True))
    return out


def sonnet() -> list[Row]:
    out = arms("S")
    for t in TRUTHS:
        for seed in (1, 2):
            base = -0.7 - 0.001 * seed
            out += [
                row(t.id, seed, "ORACLE", base),
                row(t.id, seed, "B-lib", base - 0.05),
            ]
    return out


def tabled(rows: list[Row]) -> Table:
    return Table({t.id: t for t in TRUTHS}, tuple(derive(rows)), (), {}, {})


def load_rows(
    store: object,
    env: object,
    config: CampaignConfig,
    truths: tuple[TruthEntry, ...],
    **_: object,
) -> Table:
    """Each config's own rows, as ``load_rows`` would give them."""
    if config.only_truths is not None:
        return tabled([])
    if config.llm_letter == "S":
        return tabled(sonnet())
    return tabled(arms(config.llm_letter))


def render(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str
) -> str:
    store = tmp_path / "store"
    with ResultStore(store):
        pass
    out = tmp_path / "REPORT.md"
    argv = [
        "pilot_report.py",
        *("--root", str(store), "--out", str(out), "--resamples", "200"),
        *("--read-root", "11", "--naming-commit", "no-such-commit"),
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(analysis, "load_rows", load_rows)
    report.main()
    return out.read_text(encoding="utf-8")


def truths_read(text: str) -> dict[str, int]:
    """Each named-minus-anon read's ``truths`` column."""
    section = text.split("### Named minus anon, per model", 1)[1]
    found = re.findall(r"^\| (AG-[co]-[HOS]) \| (\d+) \|", section, re.MULTILINE)
    return {name: int(n) for name, n in found}


def test_on_a_p4_block_every_model_is_read_from_the_merged_plan(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = render(report, tmp_path, monkeypatch, "--p4-block", "sonnet")
    assert truths_read(text) == {name: len(TRUTHS) for name in READS}


def test_any_block_gives_the_same_reads(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secondary reads the whole plan whichever block the report is asked for."""
    text = render(report, tmp_path, monkeypatch, "--p4-block", "haiku")
    assert truths_read(text) == {name: len(TRUTHS) for name in READS}


def test_the_sonnet_reads_are_those_of_the_sonnet_table_alone() -> None:
    """Only the table changes: the contrast rows are the Sonnet config's either way,
    so the gates, floors and Sonnet reads are the same."""
    alone = tabled(sonnet())
    merged = merge_tables(
        [
            TableSource(load_rows(None, None, config, TRUTHS), Path("/p4"), "f")
            for config in p4_plan(TRUTHS)
        ]
    )
    controls = ScriptedControls(naming_null=True)
    before = read_named_minus_anon(alone, controls, 11, 200)
    after = read_named_minus_anon(merged, controls, 11, 200)
    for name in ("AG-c-S", "AG-o-S"):
        assert before[name].n == len(TRUTHS)
        assert same_read(before[name], after[name])
    assert {before[name].n for name in ("AG-c-H", "AG-o-O")} == {0}
