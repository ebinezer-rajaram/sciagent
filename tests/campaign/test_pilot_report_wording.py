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
import sys
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


def test_named_minus_anon_needs_its_root_to_be_printed(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No root, no draw and no section, so a report without it keeps its bytes."""
    assert "Named minus anon" not in _render(report, tmp_path, monkeypatch, _table())


def test_named_minus_anon_refuses_a_root_with_no_commit(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit):
        _render(
            report, tmp_path, monkeypatch, _table(), extra_argv=("--read-root", "11")
        )


def test_named_minus_anon_is_printed_with_its_root_and_a_commit(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(
        report,
        tmp_path,
        monkeypatch,
        _table(),
        extra_argv=("--read-root", "11", "--naming-commit", "no-such-commit"),
    )
    assert "### Named minus anon" in text
    assert "| AG-c-S | 0 | not reportable | - | - |" in text


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
    assert "median over 5 of the 5 kept truths (those with a result)" in line, line
    assert "declared" not in line, line


def test_control_2_still_reads_every_kept_truth(
    report: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _render(report, tmp_path, monkeypatch, _table())
    line = _line(text, "**2. Planted hint beats B-rand**")
    assert "over kept truths:" in line, line


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
