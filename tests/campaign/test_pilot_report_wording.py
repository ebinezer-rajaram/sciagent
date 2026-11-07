"""Three sentences of the pilot report say only what the store supports.

The red team's findings on the Linux pilot's report (session 26; PILOT.md's
notes carry them by hand until the report is regenerated):

- Control 3 printed "median over kept truths", but it reads the declared
  B-sym@10F subset: on the pilot 20 truths are kept and 10 are read.
- The per-system text said B-sparse "often submits" a form "for a
  ``Product(Excite, Periodic)`` truth, a predictive equivalent". That sentence
  came from the Windows run. No dev truth has that form, and the equivalence
  was never shown.
- "Verdict: GO" read as a decision. It is the rules' computed output, and P3
  closes only on a GO plus the owner's transcript read (P4-PROPOSAL §2.4).

Each test renders the whole report through ``main()``: only the store read is
replaced, by a real :class:`Table`, because a store at the plan's addresses
needs a full campaign. The verdict and every number come from the real
``analysis.verdict``.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from campaign_support import entry
from test_campaign_analysis import row

from sciagent.campaign import analysis
from sciagent.campaign.analysis import Row, Table, derive
from sciagent.campaign.store import ResultStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SPLIT = ROOT / "data" / "v2" / "truths" / "dev"
#: Five truths: control 2 cannot pass on fewer (2**-n must be below 0.05).
TRUTHS = ("dev-000", "dev-001", "dev-002", "dev-003", "dev-004")
#: B-sym@10F runs on a declared subset, as in the pilot.
SUBSET = 3
SEEDS = (1, 2, 3)


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


def _table(*, planted: float = 0.0, missing: int = 0, subset: int = SUBSET) -> Table:
    """Every baseline on every planned unit; ``planted`` below 0 fails control 2,
    ``missing`` marks non-LLM units as not run."""
    rows: list[Row] = []
    for i, t in enumerate(TRUTHS):
        for seed in SEEDS:
            base = -0.7 - 0.001 * seed
            rows += [
                row(t, seed, "ORACLE", base, exact=True, curve=((1, base),)),
                row(t, seed, "B-lib", base - 0.05),
                row(t, seed, "B-np", base - 0.03),
                row(t, seed, "B-rand", base - 0.04, curve=((1, base - 0.06),)),
                row(t, seed, "B-sym", base - 0.01),
                row(t, seed, "B-sparse", base - 0.02),
                row(t, seed, "planted-hint", base + planted, exact=planted == 0.0),
            ]
            if seed == 1 and i < subset:
                rows.append(row(t, seed, "B-sym@10F", base - 0.002))
    return Table(
        {t: entry("hawkes") for t in TRUTHS},
        tuple(derive(rows)),
        (),
        {},
        {"B-np": missing} if missing else {},
    )


def _render(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: Table,
    *,
    subset: int | None = SUBSET,
    extra_argv: tuple[str, ...] = (),
) -> str:
    """The report ``main()`` writes for ``table``."""
    store = tmp_path / "store"
    with ResultStore(store):
        pass
    out = tmp_path / "PILOT.md"
    argv = [
        "pilot_report.py",
        *("--root", str(store), "--split", str(SPLIT), "--out", str(out)),
        *("--truths", str(len(TRUTHS)), "--seeds", "1,2,3", "--resamples", "200"),
        *extra_argv,
    ]
    if subset is not None:
        argv += ["--b10f-truths", str(subset)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(analysis, "load_rows", lambda *_, **__: table)
    report.main()
    return out.read_text(encoding="utf-8")


def _line(text: str, start: str) -> str:
    found = [line for line in text.splitlines() if line.startswith(start)]
    assert len(found) == 1, (start, found)
    return found[0]


def test_named_minus_anon_is_a_p4_blocks_section(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a P4 block, no draw and no section, so a dev report keeps its bytes;
    the P4 path is ``test_pilot_report_models``."""
    text = _render(report, tmp_path, monkeypatch, _table())
    assert "Named minus anon" not in text
    assert "Truths that left the reads" not in text


@pytest.mark.parametrize(
    "option",
    [("--read-root", "11"), ("--naming-commit", "no-such-commit")],
    ids=["read-root", "naming-commit"],
)
def test_the_secondarys_root_and_commit_are_not_options(
    report: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: tuple[str, str],
) -> None:
    """Red team 2026-11-07 finding 2: neither can be typed."""
    with pytest.raises(SystemExit):
        _render(report, tmp_path, monkeypatch, _table(), extra_argv=option)


def test_control_3_counts_a_failed_unit_as_the_verdict_does(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 8: a failed B-sym@10F unit is ranked by the verdict (ruling R3), so
    the line counts its truth."""
    table = _table()
    rows = tuple(
        row(r.truth, r.seed, r.system, None, ok=False)
        if (r.truth, r.system) == (TRUTHS[0], "B-sym@10F")
        else r
        for r in table.rows
    )
    failed = Table(table.truths, tuple(derive(list(rows))), (), {}, {})
    line = _line(
        _render(report, tmp_path, monkeypatch, failed),
        "**3. B-sym at 10xF approaches ORACLE**",
    )
    assert "median over 3 of the 5 kept truths (its declared subset)" in line, line


def test_control_3_names_the_subset_it_reads(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table())
    line = _line(text, "**3. B-sym at 10xF approaches ORACLE**")
    assert "median over kept truths" not in text
    assert "median over 3 of the 5 kept truths (its declared subset)" in line, line
    assert "of the per-truth gap = -0.0020 (pass if ≥ -0.01)" in line, line
    assert "**PASS** (seeds [1] only)" in line, line


def test_control_3_without_a_declared_subset_does_not_claim_one(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(
        report, tmp_path, monkeypatch, _table(subset=len(TRUTHS)), subset=None
    )
    line = _line(text, "**3. B-sym at 10xF approaches ORACLE**")
    assert "median over 5 of the 5 kept truths (those with a unit" in line, line
    assert "declared" not in line, line


def test_control_2_still_reads_every_kept_truth(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table())
    line = _line(text, "**2. Planted hint beats B-rand**")
    assert "over 5 truths (5 kept):" in line, line


def _unchecked(table: Table, truth: str) -> Table:
    """``table`` with ``truth``'s B-lib NaN on seeds 2 and 3: unchecked."""
    rows = [
        replace(r, per_event=math.nan)
        if (r.truth, r.system) == (truth, "B-lib") and r.seed != 1
        else r
        for r in table.rows
    ]
    return replace(table, rows=tuple(derive(rows)))


def test_controls_2_and_3_name_the_unchecked_truths_they_leave_out(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INBOX 2026-11-07: an unchecked truth enters controls 2 and 3 only where it
    counts against them. Planted-hint wins and B-sym@10F is near ORACLE here, so it
    is left out of both, and each line says which truths it read."""
    text = _render(report, tmp_path, monkeypatch, _unchecked(_table(), TRUTHS[0]))
    two = _line(text, "**2. Planted hint beats B-rand**")
    assert (
        "over 4 truths (4 kept; left out, as they could only help it pass: "
        "unchecked ['dev-000']):" in two
    ), two
    three = _line(text, "**3. B-sym at 10xF approaches ORACLE**")
    assert "median over 2 of the 4 kept truths (its declared subset; left out" in (
        three
    ), three
    assert "unchecked ['dev-000'])" in three, three


def test_control_2_states_its_limit_when_an_unchecked_loss_enters(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _unchecked(_table(), TRUTHS[4])
    rows = [
        replace(r, per_event=-1.0)
        if (r.truth, r.system) == (TRUTHS[4], "planted-hint")
        else r
        for r in table.rows
    ]
    text = _render(
        report, tmp_path, monkeypatch, replace(table, rows=tuple(derive(rows)))
    )
    two = _line(text, "**2. Planted hint beats B-rand**")
    assert "over 5 truths (4 kept, 1 unchecked against it):" in two, two
    assert "the signed-rank p is not monotone" in text, text


def test_b_sparse_carries_no_claim_from_the_windows_run(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table())
    line = _line(text, "Recovery and close shares are truth-weighted")
    assert "predictive equivalent" not in text
    assert "often submits" not in text
    assert "Product(Excite, Periodic)" not in text
    assert line.endswith("and its distance is not shown."), line


def test_c3_says_the_wall_limit_helps_the_two_arms_unequally(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOG 2026-11-05 (session 34): the prompt states the limit in the same words,
    but only one arm can read a clock, so C3 reads with that difference. The
    sentence stands beside the arms' cost and wall figures, once."""
    text = _render(report, tmp_path, monkeypatch, _table())
    line = _line(text, "C3 (AG-o against AG-c):")
    assert "AG-o can read a clock through its own code and AG-c cannot" in line
    assert "wall limit" in line
    llm = text.index("## LLM arms: cost and behaviour")
    assert llm < text.index(line) < text.index("### Void runs")


def test_go_is_labelled_as_computed_not_decided(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table())
    assert "**Verdict: GO**" not in text
    line = _line(text, "**Verdict (computed): GO.**")
    assert "This is what the rules compute." in line, line
    assert "It is not the decision:" in line, line
    assert "closes P3 on a GO plus the owner's transcript read" in line, line


def test_a_computed_outcome_other_than_go_carries_the_same_label(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table(planted=-0.05))
    line = _line(text, "**Verdict (computed): CONTROLS FAIL.**")
    assert "It is not the decision:" in line, line


def test_an_incomplete_store_stays_pending(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table(missing=1))
    assert "**Verdict: PENDING.**" in text
    assert "Verdict (computed)" not in text
