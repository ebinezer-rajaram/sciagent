"""Acceptance test A40: re-derivation selects by metric version, and replays.

A40 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Re-derivation of the recorded matrix under fixed
metrics"*, and reads:

    ``test_a40_rederivation_selects_by_metric_version`` -- a ledger holding rows
    under two metric versions renders them separately, refuses to pool them, and
    the replayed campaign reports ``store.misses == 0``.

The entry's **Idea.** is what that line abbreviates, and it is the standard these
tests are written to: *"bump ``METRIC_VERSION``, re-run the 38 conventional cells
(deterministic), and replay the 18 LLM cells from the transcript corpus --
``REPLAY`` raises on any miss, so this doubles as the first full-corpus replay
audit. Old rows stay (append-only); the report selects by metric version and
labels the re-derivation as such."*

One premise of that entry is superseded, and it changes what has to be tested
--------------------------------------------------------------------------

The entry was written 2026-08-18. ``DIMENSION_VERSION`` landed the next day, at
gate A26, and :data:`~sciagent.eval.scoring.DIMENSION_VERSION`'s own comment
records -- measured, not argued -- that bumping ``METRIC_VERSION`` for an
eval-layer re-scoring is the *wrong* instrument: it reaches every
:class:`~sciagent.inference.binning.Discretisation`'s content hash, so it
invalidates every cached empirical table and forces a full rebuild on every
machine and in every worktree, for a change that touches no estimator.

The recorded campaign needs no such bump to stay separable. Every one of its
1,120 rows carries ``battery = None`` and ``dimensions = None``, so
:func:`~sciagent.eval.report._at_address` already excludes all of them on two
terms. The decision taken with this gate is therefore **not to bump**, and the
re-derivation rides ``dimensions`` and ``battery`` instead.

That decision is what makes the labelling half of the entry load-bearing rather
than cosmetic. ``render``'s header prints the *metric* version -- which under
this decision is the one term that does **not** move between the two generations
-- and prints the dimension reading nowhere, so a re-derived report was
indistinguishable in its own text from one built on the superseded generation.
:attr:`~sciagent.eval.report.CellSummary.battery` was already rendered per cell
at gate A27; the dimension reading was not rendered at all.

Selection by metric version is nonetheless what the gate names, and it is tested
as written. It is not hypothetical: ``scripts/report_matrix.py`` takes
``--metric-version`` on the command line, so it is the one generation term an
operator can actually ask for.

Why ``store.misses == 0`` is the weakest clause in the gate line
---------------------------------------------------------------

Taken alone it asserts nothing. :meth:`TranscriptStore.resolve` increments
``_misses`` only on the branch that **calls out**, which is reachable only in
:data:`RECORD` mode; a :data:`REPLAY` store raises
:class:`~sciagent.core.errors.TranscriptMissError` instead. So ``misses == 0``
holds of every REPLAY store that ever existed, including one holding nothing that
replayed no campaign at all, and a test asserting only that would pass against an
implementation that never opened the corpus.

What makes the number mean something is the pair of facts either side of it, and
both are tested here: that the campaign **completed** under a store in REPLAY
mode, and that a corpus missing one call **stops** the campaign rather than
quietly filling it. The second has a live failure mode.
:class:`~sciagent.core.errors.TranscriptMissError` and
:class:`~sciagent.core.errors.ProviderError` are sibling
:class:`~sciagent.core.errors.ProposalError` subclasses, and
``sciagent/systems/hybrid.py``'s ``except ProviderError`` -- the clause that
turns a provider failure into a *recorded refusal* -- therefore does not catch a
miss today. Nothing asserted that at campaign level. Were it ever to change, a
replay would report a full matrix in which some cells were scored on refusals the
harness invented.

The identity a replay has to carry
----------------------------------

A transcript address is a hash over the brief **and** the backend's ``id``,
``model`` and ``settings``. The recorded corpus holds 112 calls under exactly one
triple, ``("claude-agent-sdk", "claude-opus-5", "effort=high")``. A replay
offering any other identity misses on every address -- so the provider a replay
supplies is not free to be a stand-in under a stand-in's name, and ``--replay``
must take its identity from the corpus.

Cost
----

Nothing here writes a research artefact. The campaign helper runs one replicate
of one cell on the *gate* table, and ``save_matrix_table`` is reachable only from
``run_matrix.py``'s ``checkpoint``, which no test in this class lets run. The two
tests that drive ``main`` do load ``matrix_table()`` before they stop, since the
CLI builds its runner from it -- so the claim is that the shared table is never
*grown*, not that it is never *read*. A review corrected an earlier version of
this note that claimed the latter.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest
from slice_tables import gate_table

from environments.pointproc.outcomes import held_out_designs
from environments.pointproc.runner import MatrixRunner, scenario_battery, scenario_seed
from sciagent.core.errors import (
    MalformedDesignError,
    SciAgentError,
    TranscriptMissError,
)
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    GrammarVersion,
    MetricVersion,
    Probability,
    ScenarioId,
    Seed,
)
from sciagent.eval.matrix import (
    CampaignAddress,
    Cell,
    CellReading,
    CellTask,
    battery_key,
    cell_key,
    run_matrix,
)
from sciagent.eval.report import MatrixReport, render, summarise
from sciagent.eval.scenarios import ScenarioClass
from sciagent.eval.scoring import DIMENSION_VERSION, ClosedWorldScore, DimensionVector
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.partitions import DataPartition
from sciagent.systems.llm import (
    RECORD,
    REPLAY,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
)

#: The generation the recorded campaign was scored under.
RECORDED = CampaignAddress(
    env_version=EnvVersion("pointproc/1.0.0"),
    data_version=DataVersion("slice/1"),
    metric_version=MetricVersion("1.2.0"),
    partition=DataPartition.DEV,
)

#: The generation a re-derivation under a moved metric version lands at. Differs
#: from :data:`RECORDED` in that one term and in nothing else, which is what
#: makes a test of it a test of selection rather than of the other five terms.
REDERIVED = CampaignAddress(
    env_version=RECORDED.env_version,
    data_version=RECORDED.data_version,
    metric_version=MetricVersion("1.3.0"),
    partition=RECORDED.partition,
)

BATTERY = held_out_designs()
PLATFORM = "Windows-11-x86_64"
GRAMMAR = GrammarVersion("pointproc-edits/1.0.0")

#: The cheapest cell that actually calls a provider. V3 is one of the two
#: ablation arms, and S11 is the out-of-library scenario -- which is not an
#: arbitrary choice among the three §9 runs them on. A ``Hybrid`` proposes only
#: where Stage A finds the closed set inadequate, so on S8 the arm completes
#: without a single model call and there is nothing for a corpus to hold. That
#: was the first version of this file, and it failed by recording an empty
#: corpus and replaying it successfully -- a green replay test over no replay.
ABLATION_ARM = "V3"
ABLATION_SCENARIO = "S11"

#: How many calls one replicate of a proposing arm makes, and therefore how many
#: a complete corpus for it holds. Not a magic number: it is
#: :class:`~sciagent.systems.hybrid.Hybrid`'s ``max_proposals`` default, which
#: ``memory_ablation`` passes through to V3 and V4.
#:
#: Asserted exactly rather than as ``> 0``, and a review is why. ``_extend``
#: breaks out of the proposal loop at the first ``"refused"``, so an arm that
#: came to make one call instead of two would leave a corpus of one, keep every
#: ``> 0`` guard green, and exercise half the proposal loop while the module
#: claimed a full-corpus replay audit. The exact count is what notices.
MAX_PROPOSALS = 2

#: A payload per proposal the arm may make, as ``tests/test_hybrid.py`` scripts
#: them. What they propose does not matter here; that a call is *made*, and
#: therefore recorded, does.
SCRIPT = (
    fixed_payload(3, (20, 30, 40), name="size_mixture"),
    fixed_payload(1, (10, 20, 30, 40), name="latent_regime"),
)


def _reading(d1: float = 1.0) -> CellReading:
    """Return a reading carrying stated numbers and nothing derived."""
    return CellReading(
        dimensions=DimensionVector(
            d1_structural_distance=d1,
            d2_held_out_predictive=-2.0,
            d3_intervention_similarity=0.5,
            d4_explanatory_coverage=0.25,
            d5_enabled_experiment_value=0.75,
            d6_complexity=12.0,
            n_held_out=3,
        ),
        score=ClosedWorldScore(
            truth_mass=Probability(0.5),
            log_score=-1.0,
            leading_mass=Probability(0.5),
            correct=True,
            identified=False,
        ),
        ppc_p_value=0.2,
        inadequate=False,
        experiments=8,
        structural_distance=1.0,
        battery=battery_key(BATTERY),
    )


def _rows(
    address: CampaignAddress,
    *,
    replicates: int = 2,
    d1: float = 1.0,
) -> tuple[LedgerEntry, ...]:
    """Return ``replicates`` ledger rows for one cell, at ``address``.

    ``d1`` varies so that two generations of the same cell are distinguishable by
    their *numbers* and not only by their address. Were both generations to carry
    identical readings, a report that pooled them would render the same point
    estimate as one that selected correctly, and the headline assertion would
    pass against the defect it exists to catch.
    """
    cell = Cell("V1", ScenarioId("S1"), replicates)
    return tuple(
        LedgerEntry(
            key=cell_key(
                CellTask(cell=cell, replicate=index, seed=Seed(index)),
                address,
                battery=BATTERY,
            ),
            reading=FrozenDict[str, float](dict(_reading(d1).as_payload())),
            sequence=index,
        )
        for index in range(replicates)
    )


def _summarise(
    entries: Sequence[LedgerEntry], address: CampaignAddress
) -> MatrixReport:
    """Summarise ``entries`` at ``address`` with the provenance a report needs."""
    fallback: ScenarioClass = "single"
    return summarise(
        tuple(entries),
        address=address,
        scenario_class=lambda _target: fallback,
        battery=lambda _target: BATTERY,
        platform=PLATFORM,
        grammar=GRAMMAR,
    )


@cache
def _run_matrix_cli() -> ModuleType:
    """Load ``scripts/run_matrix.py`` as a module, for its wiring.

    By path, because ``scripts/`` is not a package and pytest's prepend mode puts
    only ``tests/`` on the path. The same loader ``tests/acceptance/test_a28.py``
    uses, and for the same reason.
    """
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "run_matrix_cli_a40", root / "scripts" / "run_matrix.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _campaign(
    ledger_path: Path, store: TranscriptStore, provider: ScriptedProvider
) -> None:
    """Run one replicate of one ablation cell into ``ledger_path``.

    The real :class:`~environments.pointproc.runner.MatrixRunner` and the real
    :func:`~sciagent.eval.matrix.run_matrix`, on the *gate* table: this is a
    campaign and not a stand-in for one. Nothing here persists a table, so the
    shared campaign artefact is untouched by the suite.
    """
    runner = MatrixRunner(gate_table(), provider=lambda: provider, store=store)
    with CampaignLedger.open(ledger_path) as ledger:
        run_matrix(
            (Cell(ABLATION_ARM, ScenarioId(ABLATION_SCENARIO), 1),),
            address=runner.address,
            scenario_seed=scenario_seed,
            battery=scenario_battery,
            execute=runner.execute,
            ledger=ledger,
        )


def _readings(ledger_path: Path) -> tuple[FrozenDict[str, float], ...]:
    """Return every reading a ledger holds, in sequence order."""
    with CampaignLedger.open(ledger_path) as ledger:
        rows = sorted(ledger.entries(), key=lambda entry: entry.sequence)
    return tuple(entry.reading for entry in rows)


def _corpus_at(path: Path, ledger: Path) -> TranscriptStore:
    """Record one campaign, save its calls to ``path``, return the store."""
    recorded = TranscriptStore(mode=RECORD)
    _campaign(ledger, recorded, ScriptedProvider(list(SCRIPT)))
    recorded.save(path)
    return recorded


class TestA40RederivationSelectsByMetricVersion:
    """The gate: two generations stay apart, and the replay is a replay.

    The criterion's three clauses -- selection, the refusal to pool, and the
    replay -- plus the labelling the entry's **Idea.** requires and the gate line
    abbreviates away.
    """

    def test_a40_rederivation_selects_by_metric_version(self) -> None:
        """The headline: each generation reports its own rows and only those.

        Both generations of one cell sit in one ledger, differing in the metric
        version and in ``d1``. Each report must carry its own reading; one that
        pooled would carry the mean of the two.
        """
        entries = _rows(RECORDED, d1=1.0) + _rows(REDERIVED, d1=5.0)

        old = _summarise(entries, RECORDED)
        new = _summarise(entries, REDERIVED)

        assert old.cells[0].dimensions["d1_structural_distance"].point == 1.0
        assert new.cells[0].dimensions["d1_structural_distance"].point == 5.0

    def test_a40_the_two_generations_are_not_pooled(self) -> None:
        """Neither report's replicate count includes the other's rows.

        Stated separately from the reading because a pooled implementation and a
        correct one can agree on the *point estimate* -- two generations whose
        numbers happened to match would -- while the count always separates them.
        """
        entries = _rows(RECORDED, replicates=2) + _rows(REDERIVED, replicates=3)

        assert [cell.replicates for cell in _summarise(entries, RECORDED).cells] == [2]
        assert [cell.replicates for cell in _summarise(entries, REDERIVED).cells] == [3]

    def test_a40_a_report_names_the_dimension_reading_it_was_scored_under(
        self,
    ) -> None:
        """The rendered report says which reading of §8 produced these numbers.

        Under the decision not to bump ``METRIC_VERSION``, the dimension reading
        is the term separating a re-derivation from the campaign it re-derives. A
        report naming the metric version but not this one is labelled by the
        single generation term that did not move.

        **The assertion is deliberately over the whole rendering**, not over the
        header block, and an earlier docstring claiming "header" overstated it.
        The standard asks that the re-derivation be labelled; a per-cell line
        would satisfy that, and is the precedent
        :attr:`~sciagent.eval.report.CellSummary.battery` already sets. Narrowing
        the assertion to the header would test more than the standard requires
        and pin a layout decision this gate has no business pinning.
        """
        rendered = render(_summarise(_rows(RECORDED), RECORDED))

        assert DIMENSION_VERSION in rendered

    def test_a40_an_empty_selection_names_the_metric_versions_present(self) -> None:
        """Asking for a generation the ledger lacks says which one it holds.

        ``_at_address`` *excludes* a row at another metric version, which is
        right -- the caller chose the version they asked for. What is not right
        is telling an operator "no row matches" about a ledger full of rows one
        term away, while naming every term except the one that moved. The
        dimension reading already gets this treatment; the metric version, which
        ``scripts/report_matrix.py`` takes on the command line, did not.
        """
        with pytest.raises(MalformedDesignError, match=r"1\.2\.0"):
            _summarise(_rows(RECORDED), REDERIVED)

    def test_a40_a_replayed_campaign_reports_no_misses(self, tmp_path: Path) -> None:
        """A campaign re-run against its own corpus replays it and completes.

        ``misses == 0`` is the observable the gate names and the weakest of these
        assertions -- see this module's docstring. The load-bearing ones are that
        the campaign *completed* under a REPLAY store, that the provider was
        never consulted, and that the replayed readings are the recorded ones
        rather than fresh work.
        """
        recorded = TranscriptStore(mode=RECORD)
        recording_provider = ScriptedProvider(list(SCRIPT))
        _campaign(tmp_path / "record.db", recorded, recording_provider)
        assert len(recorded) == MAX_PROPOSALS, "the recording pass is short of calls"
        assert recording_provider.calls == MAX_PROPOSALS

        store = TranscriptStore({t.address: t for t in recorded}, mode=REPLAY)
        replaying_provider = ScriptedProvider(list(SCRIPT))
        _campaign(tmp_path / "replay.db", store, replaying_provider)

        assert store.misses == 0
        assert replaying_provider.calls == 0, "the replay consulted the provider"
        assert _readings(tmp_path / "replay.db") == _readings(tmp_path / "record.db")

    def test_a40_a_replay_stops_on_a_miss_rather_than_recording_a_refusal(
        self, tmp_path: Path
    ) -> None:
        """A corpus missing a call ends the campaign; it does not invent one.

        The failure this forbids is silent. ``hybrid.py`` catches
        :class:`~sciagent.core.errors.ProviderError` and turns it into a
        *recorded refusal*, and :class:`TranscriptMissError` is its sibling
        rather than its subclass -- so today a miss propagates. Were that ever to
        become a subclass, or the clause widened to ``ProposalError``, a replay
        against an incomplete corpus would report a complete matrix scored partly
        on refusals nobody made.
        """
        with pytest.raises(TranscriptMissError):
            _campaign(
                tmp_path / "replay.db",
                TranscriptStore(mode=REPLAY),
                ScriptedProvider(list(SCRIPT)),
            )

    def test_a40_the_cli_replays_rather_than_recording(self, tmp_path: Path) -> None:
        """``--replay`` opens the corpus in REPLAY mode, not in RECORD.

        The wiring is a separate failure from the logic, exactly as
        ``test_a28_the_cli_consults_the_guard_before_it_records`` argues: a
        ``--replay`` loading the corpus in ``RECORD`` would pass every other test
        in this class while calling out on the first address the corpus lacked.
        The runner is replaced, so no cell executes and nothing simulates.

        **The store's contents are asserted, not only its mode**, and that is the
        half a review had to add. A ``--replay`` that parses the flag, builds an
        empty ``TranscriptStore(mode=REPLAY)`` and never opens the file passes a
        mode check, passes the identity test below -- which hands in a store it
        loaded itself -- and passes a byte-comparison of the corpus, because a
        file never opened is a file never changed. It replays none of the 18
        cells: the first ``resolve`` misses and raises. That implementation is
        worse than the RECORD-mode one this test was written for, and nothing
        here caught it until the addresses were compared.
        """
        cli = _run_matrix_cli()
        corpus = tmp_path / "corpus.json"
        recorded = _corpus_at(corpus, tmp_path / "record.db")
        seen: list[TranscriptStore] = []

        def capture(_table: object, **kwargs: object) -> object:
            store = kwargs["store"]
            assert isinstance(store, TranscriptStore)
            seen.append(store)
            raise _Stop("stopping before any cell runs")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cli, "MatrixRunner", capture)
            code = cli.main(_replay_argv(tmp_path, corpus))

        assert code != 3, "--replay was refused for want of a live provider"
        assert [store.mode for store in seen] == [REPLAY]
        assert [store.addresses() for store in seen] == [recorded.addresses()]

    def test_a40_a_replay_carries_the_recorded_backends_identity(
        self, tmp_path: Path
    ) -> None:
        """The provider a replay supplies answers to the corpus's own name.

        A transcript address hashes the backend's ``id``, ``model`` and
        ``settings`` along with the brief, so a replay offering a stand-in under
        a stand-in's name misses on every address in the corpus. Not a cosmetic
        property: it is the difference between a replay that runs and one that
        cannot.
        """
        cli = _run_matrix_cli()
        corpus = tmp_path / "corpus.json"
        recorded = _corpus_at(corpus, tmp_path / "record.db")
        one = next(iter(recorded))

        provider = cli._replay_provider(TranscriptStore.load(corpus, mode=REPLAY))()

        assert (provider.id, provider.model, provider.settings) == (
            one.provider,
            one.model,
            one.settings,
        )

    def test_a40_a_replay_refuses_a_recording_target(self, tmp_path: Path) -> None:
        """``--replay`` and ``--transcripts`` are not combinable.

        ``run_matrix.py`` checkpoints the transcript store to ``--transcripts``
        after every replicate, which is right for a recording pass and wrong for a
        replay: the corpus is the artefact being audited, and a pass that rewrote
        it would destroy the evidence of what it had audited. Refusing the
        combination is what keeps that from being reachable.

        **This replaces a byte-comparison of the corpus, which could not fail.**
        ``store.save`` is reachable only from ``checkpoint``, which only
        ``execute`` calls, which only ``run_matrix`` drives -- and a test that
        patches ``run_matrix`` out has removed the only path to the write it
        claims to forbid. Worse, the comparison is unfalsifiable even unpatched:
        ``save`` sorts its addresses and fixes its separators and newline, and a
        REPLAY store gains no entries, so re-saving an unchanged corpus is
        byte-identical by construction. A review established both points; what
        survives is the refusal, which is falsifiable and closes the same hazard
        at the one place it can still arise.
        """
        cli = _run_matrix_cli()
        corpus = tmp_path / "corpus.json"
        _corpus_at(corpus, tmp_path / "record.db")

        code = cli.main([*_replay_argv(tmp_path, corpus), "--transcripts", str(corpus)])

        assert code == 3


class _Stop(SciAgentError):
    """Ends a CLI run once the wiring under test has been observed.

    A :class:`~sciagent.core.errors.SciAgentError` **and not a bare exception**,
    which is the difference between a test that can go green and one that cannot.
    ``main`` catches ``SciAgentError`` where it builds the runner and where it
    drives the campaign, and catches nothing else; a plain sentinel therefore
    escapes ``main`` altogether, so ``cli.main(...)`` never returns and no
    assertion after it is ever reached. The first version of this file used
    ``Exception`` and the two tests below were red against a *working*
    ``--replay`` for that reason alone -- an unreachable assertion behind a red
    that came from argparse. ``test_a28_the_cli_consults_the_guard_before_it
    _records`` works because ``ValueError`` is likewise caught.
    """


def _replay_argv(tmp_path: Path, corpus: Path) -> list[str]:
    """Return the argv for a one-replicate replay of the ablation cell."""
    return [
        str(tmp_path / "replay.db"),
        "--systems",
        ABLATION_ARM,
        "--scenarios",
        ABLATION_SCENARIO,
        "--replicates",
        "1",
        "--replay",
        str(corpus),
    ]
