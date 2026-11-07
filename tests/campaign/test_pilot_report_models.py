"""The P4 report through ``main()``, with LLM rows (red team 2026-11-07, findings 2 to
4, 7 and 13).

Named minus anon is printed for every model (SPEC §6.2, control room B2), so on a
P4 block the report gives it the merge of every config of ``p4_plan``: the Haiku and
Opus configs hold no ORACLE, and the Sonnet config no Haiku or Opus row. R2's scope
line reads the same merged table over the whole split, for the stored table and the
corrected one ``reads.json`` holds. Nothing the operator types decides the gate:
control 2's root and resamples are the miss script's frozen ones, and control 4 is
read at the commit of the store's single ``establish`` line.

Stand-in truths and tables only: ``load_rows`` is replaced per config, the store is
an empty one with a fingerprint sidecar, and control 4's record and ``reads.json``
are scratch files under ``tmp_path``. No real store or record is read.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from campaign_support import environment
from test_campaign_analysis import row
from test_table_merge import standin

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.contrasts import (
    NAMING_NULL_FAILED,
    ControlsRead,
    ScriptedControls,
    read_controls,
    same_read,
)
from sciagent.campaign.fingerprint import guard_store
from sciagent.campaign.p4plan import p4_plan
from sciagent.campaign.plan import CampaignConfig, TruthEntry
from sciagent.campaign.secondaries import read_named_minus_anon
from sciagent.campaign.store import ResultStore
from sciagent.campaign.tables import TableSource, merge_tables

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
TRUTHS = standin()
READS = ("AG-c-H", "AG-c-O", "AG-c-S", "AG-o-H", "AG-o-O", "AG-o-S")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FINGERPRINT = f"commit={COMMIT},tree=clean"
#: Planted-hint minus B-rand per truth, in 1024ths. Control 2 passes on the reads'
#: frozen root and fails on that root plus 4: found by a seeded stand-in search
#: (``SeedSequence([4242, 7])``, 2026-11-07), and checked in the test that uses it.
BORDERLINE = (
    *(3, 7, 13, -3, 8, 13, 1, 2, -4, -1, 4, 8, -6, -5, 5, -1, 11, 6, -4, -6),
    *(11, 10, 11, -6, 1, 7, 12, 11, -2, 0, 13, 3, -5, 1, 10, -6, 13, 3, -4, 7),
)
#: The out-of-dictionary truths (the odd ones).
OUT = tuple(t.id for t in TRUTHS if not t.in_dictionary)


@dataclass(frozen=True)
class Record:
    """What the report reads of a split's record: its id and its split."""

    id: str
    split: str


RECORDS = tuple(Record(t.id, t.split) for t in TRUTHS)


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
        monkeypatch.setattr(target, "load_split", lambda _: RECORDS)
        monkeypatch.setattr(target, "truth_entries", lambda _: TRUTHS)
    monkeypatch.setattr(module, "pointproc_environment", lambda _: environment())
    return module


def misses() -> Any:
    return importlib.import_module("p4_search_misses")


def arms(letter: str, named: float = -0.71, anon: float = -0.72) -> list[Row]:
    out: list[Row] = []
    for t in TRUTHS:
        for seed in (1, 2):
            for tier in ("c", "o"):
                for condition, v in (("named", named), ("anon", anon)):
                    system = f"AG-{tier}-{letter}/{condition}+link-neutral"
                    out.append(row(t.id, seed, system, v, llm=True))
    return out


def references() -> list[Row]:
    """ORACLE and B-lib only: no planted-hint, so control 2 is not computable."""
    out: list[Row] = []
    for t in TRUTHS:
        for seed in (1, 2):
            base = -0.7 - 0.001 * seed
            out += [
                row(t.id, seed, "ORACLE", base),
                row(t.id, seed, "B-lib", base - 0.05),
            ]
    return out


def controls(
    planted: Sequence[int] = (64,) * len(TRUTHS), dropped: Sequence[str] = ()
) -> list[Row]:
    """The rows controls 1 to 3 read. ``planted`` is planted-hint minus B-rand per
    truth in 1024ths, and on a ``dropped`` truth B-lib beats ORACLE."""
    out: list[Row] = []
    for t, d in zip(TRUTHS, planted, strict=True):
        for seed in (1, 2):
            out += [
                row(t.id, seed, "ORACLE", -0.5),
                row(t.id, seed, "B-lib", -0.4 if t.id in dropped else -0.6875),
                row(t.id, seed, "B-rand", -0.75),
                row(t.id, seed, "planted-hint", -0.75 + d / 1024),
            ]
        out.append(row(t.id, 1, "B-sym@10F", -0.5))
    return out


def tabled(rows: list[Row]) -> Table:
    return Table({t.id: t for t in TRUTHS}, tuple(derive(rows)), (), {}, {})


def loader(baselines: list[Row]) -> Callable[..., Table]:
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
            return tabled([*arms("S", -0.625, -0.6875), *baselines])
        return tabled(arms(config.llm_letter))

    return load_rows


def merged(baselines: list[Row]) -> Table:
    load = loader(baselines)
    return merge_tables(
        [
            TableSource(load(None, None, config, TRUTHS), Path("/p4"), "f")
            for config in p4_plan(TRUTHS)
        ]
    )


def stored_controls(baselines: list[Row], naming_null: bool) -> ControlsRead:
    """The miss script's stored controls: its root, its resamples."""
    m = misses()
    return read_controls(
        merged(baselines),
        ScriptedControls(naming_null),
        m.READS_ROOT,
        m.READS_RESAMPLES,
    )


@dataclass(frozen=True)
class Store:
    root: Path
    out: Path


def prepare(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    baselines: list[Row],
    *,
    record_commit: str = COMMIT,
    fingerprints: Sequence[str] = (FINGERPRINT,),
    stored: ControlsRead | None = None,
    fixed: ControlsRead | None = None,
) -> Store:
    """A scratch store, control 4's record at ``record_commit`` and ``reads.json``
    as the miss script would write it at the store's commit."""
    store = tmp_path / "store"
    store.mkdir()
    previous: str | None = None
    for fingerprint in fingerprints:
        guard_store(store, fingerprint, reuse_from=previous)
        previous = fingerprint
    with ResultStore(store):
        pass
    records: Any = importlib.import_module("naming_null_record")
    path = tmp_path / "naming-null.json"
    path.write_bytes(
        records.record_bytes(
            {
                "schema": records.SCHEMA,
                "commit": record_commit,
                "split": "test",
                "seed": records.NAMING_SEED,
                "verdicts": [
                    {"truth": TRUTHS[0].id, "half": "in", "passed": True},
                    {"truth": TRUTHS[1].id, "half": "out", "passed": True},
                ],
            }
        )
    )
    original = records.read_naming_null

    def read_naming_null(commit: str, **_: object) -> bool:
        return bool(original(commit, path=path))

    monkeypatch.setattr(records, "read_naming_null", read_naming_null)
    naming_null = read_naming_null(COMMIT)
    first = stored or stored_controls(baselines, naming_null)
    m = misses()
    reads = tmp_path / "reads.json"
    reads.write_text(
        json.dumps(
            {
                "controls": {
                    "stored": m.controls_json(first, {}),
                    "fixed": m.controls_json(fixed or first, {}),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(report, "P4_READS", reads)
    monkeypatch.setattr(analysis, "load_rows", loader(baselines))
    return Store(store, tmp_path / "REPORT.md")


def run(
    report: ModuleType, monkeypatch: pytest.MonkeyPatch, store: Store, *extra: str
) -> str:
    argv = [
        "pilot_report.py",
        *("--root", str(store.root), "--out", str(store.out)),
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    report.main()
    return store.out.read_text(encoding="utf-8")


def render(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    baselines: list[Row] | None = None,
    record_commit: str = COMMIT,
) -> str:
    rows = references() if baselines is None else baselines
    store = prepare(report, tmp_path, monkeypatch, rows, record_commit=record_commit)
    return run(report, monkeypatch, store, *extra)


def truths_read(text: str) -> dict[str, int]:
    """Each named-minus-anon read's ``truths`` column."""
    section = text.split("### Named minus anon, per model", 1)[1]
    found = re.findall(r"^\| (AG-[co]-[HOS]) \| (\d+) \|", section, re.MULTILINE)
    return {name: int(n) for name, n in found}


def read_line(text: str, name: str) -> list[str]:
    section = text.split("### Named minus anon, per model", 1)[1]
    line = next(x for x in section.splitlines() if x.startswith(f"| {name} |"))
    return [c.strip() for c in line.strip("|").split("|")]


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
    rows = controls()
    alone = loader(rows)(None, None, p4_plan(TRUTHS)[0], TRUTHS)
    together = merged(rows)
    scripted = ScriptedControls(naming_null=True)
    before = read_named_minus_anon(alone, scripted, 11, 200)
    after = read_named_minus_anon(together, scripted, 11, 200)
    for name in ("AG-c-S", "AG-o-S"):
        assert before[name].n == len(TRUTHS)
        assert before[name].reportable
        assert same_read(before[name], after[name])
    assert {before[name].n for name in ("AG-c-H", "AG-o-O")} == {0}


def test_a_p4_report_needs_a_block(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 4: without a block the report reads one config, so a split outside
    dev is refused without one."""
    with pytest.raises(SystemExit):
        render(
            report, tmp_path, monkeypatch, "--arm-set", "p4-sonnet", "--seeds", "1,2"
        )


@pytest.mark.parametrize(
    "narrowing", [("--ids", "test-007"), ("--truths", "3")], ids=["ids", "truths"]
)
def test_a_block_takes_no_narrowing(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    narrowing: tuple[str, str],
) -> None:
    """Finding 3(b): the scope's denominator is the whole split."""
    with pytest.raises(SystemExit):
        render(report, tmp_path, monkeypatch, "--p4-block", "sonnet", *narrowing)


@pytest.mark.parametrize(
    "option",
    [("--read-root", "11"), ("--naming-commit", COMMIT), ("--resamples", "200")],
    ids=["read-root", "naming-commit", "resamples"],
)
def test_no_typed_option_reaches_the_gate(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: tuple[str, str],
) -> None:
    """Finding 2: the root, the resamples and the commit are not options on P4."""
    with pytest.raises(SystemExit):
        render(report, tmp_path, monkeypatch, "--p4-block", "sonnet", *option)


def test_the_gate_draws_from_the_frozen_root(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2(a), mutant M3: on this table control 2 passes on the miss script's
    root and fails on another, so a report reading any other root disagrees with
    ``reads.json`` and refuses, or prints the reads as not reportable."""
    m = misses()
    rows = controls(BORDERLINE)
    table = merged(rows)
    scripted = ScriptedControls(naming_null=True)
    assert read_controls(table, scripted, m.READS_ROOT, m.READS_RESAMPLES).controls_pass
    assert not read_controls(
        table, scripted, m.READS_ROOT + 4, m.READS_RESAMPLES
    ).controls_pass
    text = render(report, tmp_path, monkeypatch, "--p4-block", "sonnet", baselines=rows)
    assert read_line(text, "AG-c-S")[:5] == [
        "AG-c-S",
        "40",
        "0.0625",
        "0.0625",
        "0.0625",
    ]
    planted = next(x for x in text.splitlines() if x.startswith("**2. Planted hint"))
    assert planted.endswith("**PASS**.")


def test_control_4_is_read_at_the_stores_commit(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2(b), mutant M1: a record that passed at another commit does not open
    the gate, as it does not in the miss script."""
    text = render(
        report,
        tmp_path,
        monkeypatch,
        "--p4-block",
        "sonnet",
        baselines=controls(),
        record_commit="f" * 40,
    )
    for name in READS:
        assert f"not reportable: {NAMING_NULL_FAILED}" in read_line(text, name)[2]


def test_the_record_at_the_stores_commit_opens_the_gate(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = render(
        report, tmp_path, monkeypatch, "--p4-block", "haiku", baselines=controls()
    )
    assert read_line(text, "AG-c-S")[:3] == ["AG-c-S", "40", "0.0625"]
    assert read_line(text, "AG-c-H")[:3] == ["AG-c-H", "40", "0.0100"]


@pytest.mark.parametrize(
    "fingerprints",
    [(), (FINGERPRINT, "commit=" + "e" * 40 + ",tree=clean"), ("no-commit-here",)],
    ids=["no-sidecar", "a-reuse-line", "no-commit"],
)
def test_a_store_without_one_establish_line_at_a_commit_is_refused(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fingerprints: tuple[str, ...],
) -> None:
    store = prepare(
        report, tmp_path, monkeypatch, controls(), fingerprints=fingerprints
    )
    with pytest.raises(SystemExit):
        run(report, monkeypatch, store, "--p4-block", "sonnet")


def test_reads_json_must_hold_the_reports_controls(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report's gate is the miss script's: a ``reads.json`` whose stored
    controls differ from the report's is refused, not printed beside."""
    rows = controls()
    real = stored_controls(rows, True)
    other = ControlsRead(real.kept, real.dropped, real.unchecked, False, True)
    store = prepare(report, tmp_path, monkeypatch, rows, stored=other)
    with pytest.raises(SystemExit):
        run(report, monkeypatch, store, "--p4-block", "sonnet")


def scope_section(text: str) -> str:
    return text.split("### Truths that left the reads", 1)[1].split("###", 1)[0]


def test_the_scope_is_the_whole_plans_on_any_block(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 3(a): on a block with no ORACLE the scope is still the merged
    table's, so no truth is counted as unchecked for lack of one."""
    text = render(
        report, tmp_path, monkeypatch, "--p4-block", "opus", baselines=controls()
    )
    section = scope_section(text)
    assert "stored table: 0 of 40 truths left the reads" in section
    assert "not confirmatory" not in section


def headline_section(text: str) -> str:
    """The go/no-go headline and controls 1 to 3: from the go/no-go heading to the
    per-system table, less the shares table, whose arm names carry the block's own
    fit flags."""
    body = text.split("## Go/no-go (SPEC §7.1)", 1)[1].split("## Per-system", 1)[0]
    return "\n".join(x for x in body.splitlines() if not x.startswith("|"))


@pytest.mark.parametrize("block", ["haiku", "opus", "sweep-e16-f40"])
def test_the_headline_and_controls_read_the_merged_table_on_any_block(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block: str
) -> None:
    """Red team round 2, finding 2: a block with no ORACLE printed INCOMPLETE and
    control 1 FAIL while the gate, on the merged table, passed. The headline and
    controls of every block are now the Sonnet block's."""
    rows = controls()
    (tmp_path / "s").mkdir()
    (tmp_path / "o").mkdir()
    sonnet = headline_section(
        render(
            report, tmp_path / "s", monkeypatch, "--p4-block", "sonnet", baselines=rows
        )
    )
    other = headline_section(
        render(report, tmp_path / "o", monkeypatch, "--p4-block", block, baselines=rows)
    )
    assert "- Controls pass: True;" in sonnet, sonnet
    assert "**1. ORACLE beats B-lib**" in sonnet and "**PASS**" in sonnet
    assert other == sonnet


def test_the_scope_counts_c4s_own_truths(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutant M2: three of the twenty out-of-dictionary truths leaving puts C4, and
    only C4, past its limit."""
    rows = controls(dropped=OUT[:3])
    text = render(report, tmp_path, monkeypatch, "--p4-block", "sonnet", baselines=rows)
    section = scope_section(text)
    assert "stored table: 3 of 40 truths left the reads" in section
    assert "3 of 20 of them out of dictionary" in section
    assert "On the stored table C4 is not confirmatory" in section
    assert "the reads are not confirmatory" not in section


def test_the_scope_is_given_for_both_tables(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 3(c): the corrected table's controls from ``reads.json`` get the
    limit too."""
    rows = controls()
    real = stored_controls(rows, True)
    ins = tuple(t.id for t in TRUTHS if t.in_dictionary)[:5]
    fixed = ControlsRead(
        tuple(t for t in real.kept if t not in ins), ins, (), True, True
    )
    store = prepare(report, tmp_path, monkeypatch, rows, fixed=fixed)
    section = scope_section(run(report, monkeypatch, store, "--p4-block", "sonnet"))
    assert "stored table: 0 of 40 truths left the reads" in section
    assert "fixed table: 5 of 40 truths left the reads" in section
    assert "On the fixed table the reads are not confirmatory" in section


def test_the_scope_refuses_a_table_short_of_the_split(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = controls()
    store = prepare(report, tmp_path, monkeypatch, rows)
    whole = merged(rows)
    short = Table(
        {t: e for t, e in whole.truths.items() if t != TRUTHS[0].id},
        tuple(r for r in whole.rows if r.truth != TRUTHS[0].id),
        (),
        {},
        {},
    )
    monkeypatch.setattr(report, "p4_merged_table", lambda *_, **__: short)
    with pytest.raises(SystemExit):
        run(report, monkeypatch, store, "--p4-block", "sonnet")


def test_the_merge_refuses_a_table_short_of_the_split_by_itself(
    report: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Red team round 2, finding 7: the size check had no test of its own, as the
    ``reads.json`` refusal caught the same case. Here nothing else can refuse."""
    whole = merged(controls())
    short = Table(
        {t: e for t, e in whole.truths.items() if t != TRUTHS[0].id},
        tuple(r for r in whole.rows if r.truth != TRUTHS[0].id),
        (),
        {},
        {},
    )
    monkeypatch.setattr(report, "store_commit", lambda _: (FINGERPRINT, COMMIT))
    monkeypatch.setattr(report, "p4_merged_table", lambda *_, **__: short)
    with pytest.raises(SystemExit):
        report.p4_merged(Path("/p4"), None, TRUTHS, "stored", split_truths=len(TRUTHS))
    assert (
        "the merged table holds 39 truths, not the split's" in capsys.readouterr().err
    )
    monkeypatch.setattr(report, "p4_merged_table", lambda *_, **__: whole)
    got = report.p4_merged(Path("/p4"), None, TRUTHS, "stored", split_truths=40)
    assert got == (whole, COMMIT)


def test_the_header_gives_the_blocks_seeds(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 13: the block's seeds, not the parser's default."""
    text = render(report, tmp_path, monkeypatch, "--p4-block", "sonnet")
    assert "- Seeds [1, 2]; B-sym@10F on seeds [1] only" in text
