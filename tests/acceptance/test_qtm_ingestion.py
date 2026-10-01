"""Acceptance test A25: QTM ingestion is deterministic and declared.

A25 is a post-freeze gate. v1 SPEC §6 stops at A24, so the criterion is stated in
``docs/v1/BACKLOG.md`` under *"Real-data grounding on the SCEDC QTM catalog"*, and
reads:

    ``test_qtm_ingestion_is_deterministic_and_declared`` -- snapshot-hash →
    byte-identical ``EventLog`` segments across processes; the declared
    censoring model is applied and versioned; the consensus edit is
    preregistered in the environment before any system runs on a segment.

What this gate is for
---------------------

Every defect the framework has investigated so far it wrote itself. Grounding on
the QTM catalogue (Ross et al. 2019) buys a transfer test, because QTM's
consensus mechanism -- ETAS magnitude-gated triggering -- is *exactly* one edit
already in the slice's library: ``AddDependency(size → arrival)``, which is
scenario S11's out-of-library mechanism.

That only means anything if the data is pinned. A found-data track has a failure
mode a generated one does not: the input can move without anything saying so.
The three clauses below are the three ways it could move silently.

**Clause 1 -- the address is tied to the bytes, and the pipeline to neither
process nor platform.** ``DATA_VERSION`` carries the snapshot's SHA-256, so a
different catalogue cannot be recorded under the same address; and ingesting the
same bytes twice, in two processes, yields byte-identical
:class:`~sciagent.core.types.EventLog` segments. The cross-process arm is not
ceremony: hash randomisation and dict/set iteration order are per-process, and
invariant 3 forbids output that depends on them. An in-process loop cannot see
that class of bug at all, which is why the child runs under three
``PYTHONHASHSEED`` values including ``random``, exactly as A1's does.

**Clause 2 -- the observation process is declared, not assumed.** Short-term
aftershock incompleteness is real and is *not* a property of the earthquake
process: it is a property of the catalogue, which cannot resolve small events in
the coda of a large one. Left undeclared it is indistinguishable from the very
mechanism under test -- events clustering after large marks is the signature of
``AddDependency(size → arrival)``. So it is modelled explicitly, carried as an
S12-style nuisance that is executed and never scored, and its version enters the
address.

**Clause 3 -- the consensus edit is preregistered.** The point of the transfer
test is that seismology fixed the answer before we looked. An edit chosen after
seeing how a system performed would be a different and much weaker claim.

    A review of this module in its first form observed that "preregistered
    before any system runs on a segment" is a *temporal* claim, and that no
    assertion can distinguish it from "is a module constant" unless the
    environment makes it structural. It is now structural:
    :func:`~environments.qtm.snapshot.data_version` mixes
    ``PREREGISTRATION_DIGEST`` into every segment's address, so a segment
    cannot be addressed -- and therefore cannot be recorded against -- without
    the preregistration already being fixed. The test asserts that mixing.

What the fixture is, and why it is not the real catalogue
---------------------------------------------------------

The determinism arm runs against a synthetic file in the real schema, written by
``qtm_ingest_child.write_fixture``. That is deliberate, and it is the arm that
must run everywhere: the real snapshot is 287MB, is gitignored rather than
redistributed (SCEDC publishes no licence text), and so is absent from a fresh
checkout and from CI. A gate that only ran where someone had already downloaded
300MB would be green by default in exactly the situation where it is not being
checked. Only two tests below need the snapshot; the other nine do not.

The fixture carries the properties the pipeline's determinism actually turns on
-- the header line, duplicate timestamps *above the cut on both sides*,
magnitudes either side of the cut, and events above the censoring trigger --
because those are the inputs that make the tie rule, the cut and the observation
process do something rather than be reached and skipped.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from environments.pointproc.mechanisms import SIZE_EXCITATION
from environments.qtm.censoring import (
    CENSORING_VERSION,
    DECLARED_CENSORING,
    AftershockIncompleteness,
)
from environments.qtm.ingest import (
    ARRIVAL,
    N_EVENTS,
    PIPELINE_VERSION,
    REFERENCE_CONFIG,
    SIZE,
    Rescale,
    ingest_segments,
    parse_catalogue,
)
from environments.qtm.preregistration import (
    CONSENSUS_EDIT,
    PREREGISTRATION_DIGEST,
    preregistration_digest,
)
from environments.qtm.snapshot import (
    PRIMARY,
    SENSITIVITY,
    SNAPSHOT_SHA256,
    data_version,
    digest_of,
    snapshot_path,
)
from sciagent.core.errors import (
    MalformedDesignError,
    SnapshotMismatchError,
    SnapshotMissingError,
)

CHILD = Path(__file__).parent / "qtm_ingest_child.py"

#: v1 SPEC §4.5's segment length, written as a literal. Importing ``N_EVENTS`` and
#: comparing a segment against it would be satisfied by ``N_EVENTS = 8``: the
#: implementation would be grading its own homework.
DECLARED_SEGMENT_LENGTH = 512


def _child_module() -> Any:
    sys.path.insert(0, str(CHILD.parent))
    try:
        import qtm_ingest_child

        return qtm_ingest_child
    finally:
        sys.path.pop(0)


@pytest.fixture(scope="module")
def fixture_catalogue(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A synthetic catalogue in the QTM schema.

    Module-scoped: it is 50000 rows, and every test in the class reads the same
    bytes. Nothing below mutates it -- the two tests that need a *different*
    catalogue write their own beside it.
    """
    path = tmp_path_factory.mktemp("qtm") / "qtm_fixture.hypo"
    _child_module().write_fixture(path)
    return path


def _snapshot_or_skip(name: str) -> Path:
    path = snapshot_path(name)
    if not path.is_file():
        pytest.skip(
            f"{name} is not present at {path}; it is gitignored rather than "
            f"redistributed. Run `uv run python scripts/fetch_qtm.py` to obtain it."
        )
    return path


class TestQtmIngestion:
    """Gate A25: the found-data pipeline is deterministic, addressed and declared."""

    # ---- Clause 1: byte-identical segments across processes ----------------

    @pytest.mark.slow
    def test_qtm_ingestion_is_deterministic_and_declared(
        self, fixture_catalogue: Path
    ) -> None:
        """The headline: same bytes in, same segments out, in three child processes.

        Each child runs under a different ``PYTHONHASHSEED``, ``random``
        included. A pipeline that derived anything from :func:`hash` or walked a
        ``set`` would agree with itself in-process and disagree here; running a
        single child under the inherited environment would not detect it, since
        an exported seed would make parent and child agree by construction.

        ``check=False`` with an explicit status assertion, not ``check=True``:
        ``docs/v1/DECISIONS.md`` records (2026-08-19) that ``check=True`` left a
        dead child reporting only its exit status with stderr captured and never
        shown, and A1 was changed for exactly this reason.
        """
        child = _child_module()
        expected = child.digests(fixture_catalogue)
        assert len(expected) >= 3, (
            f"the fixture must exercise segmentation; it produced "
            f"{len(expected)} segment(s), so a comparison of empty results "
            f"could pass and an off-by-one in segmentation could hide"
        )
        rendered = "".join(f"{name} {digest}\n" for name, digest in expected.items())
        for hash_seed in ("0", "1", "random"):
            environment = dict(os.environ, PYTHONHASHSEED=hash_seed)
            completed = subprocess.run(
                [sys.executable, str(CHILD), str(fixture_catalogue)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
            assert completed.returncode == 0, (
                f"{CHILD.name} exited {completed.returncode} under "
                f"PYTHONHASHSEED={hash_seed}\n"
                f"--- stderr ---\n{completed.stderr}\n"
                f"--- stdout ---\n{completed.stdout}"
            )
            assert completed.stdout == rendered, (
                f"segment digests differ under PYTHONHASHSEED={hash_seed}"
            )

    def test_every_choice_that_moves_the_data_moves_the_address(
        self, fixture_catalogue: Path
    ) -> None:
        """The address is injective over the config. Each variant, one at a time.

        This exists because the preflight re-review proved the fixes it was
        written for had **no** regression protection at all: with
        ``AftershockIncompleteness.address`` monkeypatched back to returning the
        bare version string, and with ``magnitude_cut``, ``rescale`` and
        ``max_rows`` stripped out of ``IngestionConfig.address``, all sixteen
        tests still passed. Containment checks on version literals -- which is
        all the test below does -- cannot see any of that, because the literal
        is still there.

        The pairing matters: each variant must move the address **and** the
        data. A variant that moved only the address would be a false
        distinction, which is the mirror defect and equally a lie about what a
        row holds.
        """
        base = REFERENCE_CONFIG
        assert base.censoring is not None
        harsher = replace(base.censoring, blind_days=base.censoring.blind_days * 3.0)
        variants = {
            "magnitude_cut": base.with_magnitude_cut(base.magnitude_cut + 0.4),
            "rescale": base.with_rescale("global"),
            "max_rows": base.with_max_rows(20_000),
            "censoring off": base.without_censoring(),
            "censoring parameters": replace(base, censoring=harsher),
        }
        reference = ingest_segments(fixture_catalogue, base)
        for name, variant in variants.items():
            assert variant.address() != base.address(), (
                f"{name} changes what is ingested but renders the same address"
            )
            assert ingest_segments(fixture_catalogue, variant) != reference, (
                f"{name} renders a different address but ingests the same data"
            )

    def test_the_parse_constants_reach_the_address(self) -> None:
        """The four constants that shape a parse are rendered, by value.

        ``N_EVENTS``, the epoch offset, the columns read and the header rows
        skipped all change what is ingested, and none of them is reachable
        through :class:`IngestionConfig` -- so the test above cannot see them and
        the re-review found them covered only by the hand-maintained
        ``PIPELINE_VERSION`` literal. That is the hazard gate A33 built
        ``SIMULATOR_DIGEST`` for one directory away.

        Asserted against **literals**, not against the imported constants.
        Importing ``N_EVENTS`` and asserting it appears would pass however it
        changed: the implementation would again be grading its own homework, the
        same trap ``DECLARED_SEGMENT_LENGTH`` exists to avoid. Written this way,
        changing a constant *or* dropping it from the address both fail here --
        and a deliberate change is meant to fail, because it moves every address
        the environment has ever issued.
        """
        address = REFERENCE_CONFIG.address()
        assert "n=512" in address
        assert "epoch=13879" in address
        assert "cols=(0, 1, 2, 3, 4, 5, 6, 10)" in address
        assert "skip=1" in address

    def test_a_subclassed_censoring_model_cannot_borrow_the_address(self) -> None:
        """A subclass renders its own address, not its parent's.

        Found by the re-review as the same defect one level up: rendering the
        five parameters fixed the fields but not the *class that interprets
        them*, and two configs disagreeing about 26277 of 65658 events shared one
        byte-identical address. ``removed`` and ``completeness`` are both
        overridable, so this is reachable without touching this package.
        """

        class Harsher(AftershockIncompleteness):
            def completeness(self, trigger_size: Any, elapsed_days: Any) -> Any:
                return super().completeness(trigger_size, elapsed_days) + 2.0

        assert REFERENCE_CONFIG.censoring is not None
        parent = REFERENCE_CONFIG.censoring
        child = Harsher(**{f.name: getattr(parent, f.name) for f in fields(parent)})
        assert child.address() != parent.address()

    def test_a_snapshot_that_is_not_there_is_named_as_missing(
        self, tmp_path: Path
    ) -> None:
        """A missing catalogue raises this project's own error type.

        Not numpy's ``FileNotFoundError``: ``sciagent.core.errors`` exists so a
        caller can tell a framework fault from an interpreter one by type alone,
        and a missing snapshot has a remedy worth naming in the message.
        """
        with pytest.raises(SnapshotMissingError):
            parse_catalogue(tmp_path / "absent.hypo", REFERENCE_CONFIG)

    def test_the_address_carries_the_snapshot_hash(
        self, fixture_catalogue: Path, tmp_path: Path
    ) -> None:
        """``DATA_VERSION`` moves when the bytes move, and names every version.

        A found-data result is addressed by the catalogue it was computed from.
        If two different catalogues could share an address, the registry's
        content-addressing claim (invariant 4) is false for this environment.
        """
        before = data_version(fixture_catalogue, REFERENCE_CONFIG)
        assert PIPELINE_VERSION in before
        assert CENSORING_VERSION in before
        assert digest_of(fixture_catalogue)[:12] in before

        # Change one magnitude, in the MAGNITUDE column specifically -- the
        # narrowest edit that alters what the pipeline reads. Column 10 of the
        # first data row; see the header the fixture writes.
        lines = fixture_catalogue.read_text(encoding="ascii").splitlines()
        fields = lines[1].split()
        fields[10] = f"{float(fields[10]) + 1.0:.2f}"
        moved = tmp_path / "moved.hypo"
        moved.write_text(
            "\n".join([lines[0], " ".join(fields), *lines[2:]]) + "\n", encoding="ascii"
        )
        assert data_version(moved, REFERENCE_CONFIG) != before

    def test_segments_are_disjoint_and_of_the_declared_length(
        self, fixture_catalogue: Path
    ) -> None:
        """Segments *partition* the retained catalogue: 512 long, and disjoint.

        ``docs/v1/BACKLOG.md`` specifies "disjoint 512-event segments keyed by seed
        index". Disjointness is what makes two segments independent evidence; a
        pipeline that overlapped them would inflate every replicate count in the
        campaign without changing any visible number.

        Review of this test's first form showed why ordering assertions are not
        enough: a sliding window of stride 256 yields ten segments that are each
        512 long, each internally ascending, with strictly increasing and
        distinct start times -- and shares 256 events with each neighbour. So
        this asserts *coverage*, not order: the concatenated segments must be
        exactly the first ``512 * k`` retained events, in order and without
        repetition, which no overlapping scheme satisfies.

        Checked on ``size`` rather than on ``arrival`` because arrivals are
        rescaled **per segment** -- see
        ``test_arrivals_are_rescaled_to_mean_gap_one`` -- so every segment
        starts at zero and an arrival-keyed comparison against the source
        catalogue could not hold for any implementation at all. Magnitudes pass
        through the pipeline untouched, so they identify which events a segment
        actually drew.
        """
        assert N_EVENTS == DECLARED_SEGMENT_LENGTH
        segments = ingest_segments(fixture_catalogue, REFERENCE_CONFIG)
        assert len(segments) >= 3
        assert all(log.n_events == DECLARED_SEGMENT_LENGTH for log in segments)

        retained = parse_catalogue(fixture_catalogue, REFERENCE_CONFIG)
        for index, log in enumerate(segments):
            start = index * DECLARED_SEGMENT_LENGTH
            np.testing.assert_array_equal(
                np.asarray(log.values[SIZE]),
                retained.size[start : start + DECLARED_SEGMENT_LENGTH],
                err_msg=f"segment {index} is not the {index}-th disjoint block",
            )

    def test_the_magnitude_cut_is_applied(self, fixture_catalogue: Path) -> None:
        """Lowering the cut must retain strictly more events.

        The cut is the first pipeline stage ``docs/v1/BACKLOG.md`` names, and it is
        the one a green gate could most easily be bought by dropping: without
        this, an implementation that ignored ``magnitude_cut`` entirely would
        pass every other test in the class.
        """
        retained = parse_catalogue(fixture_catalogue, REFERENCE_CONFIG)
        assert float(retained.size.min()) >= REFERENCE_CONFIG.magnitude_cut
        lower = REFERENCE_CONFIG.with_magnitude_cut(
            REFERENCE_CONFIG.magnitude_cut - 1.0
        )
        deeper = parse_catalogue(fixture_catalogue, lower)
        assert deeper.size.size > retained.size.size

    def test_arrivals_are_rescaled_to_mean_gap_one(
        self, fixture_catalogue: Path
    ) -> None:
        """Each segment sits at the reference operating point: one event per unit time.

        ``docs/v1/BACKLOG.md`` names "rescale to mean gap 1.0". It matters because
        the empirical tables every diagnostic is scored against were built at
        ``pointproc``'s reference rate; a segment carrying raw seconds would be
        compared against a distribution it has no relation to.
        """
        for log in ingest_segments(fixture_catalogue, REFERENCE_CONFIG):
            arrivals = np.asarray(log.values[ARRIVAL])
            gaps = np.diff(arrivals)
            assert gaps.size == DECLARED_SEGMENT_LENGTH - 1
            assert float(np.mean(gaps)) == pytest.approx(1.0, abs=1e-9)

    def test_the_rescale_choice_is_read_and_not_merely_addressed(
        self, fixture_catalogue: Path
    ) -> None:
        """Changing ``rescale`` must change the data, not only the label.

        The preflight review found the first version of this field written into
        the address and read nowhere: ``rescale="global"`` produced a *different*
        address over byte-identical segments, so a row would have claimed
        globally-rescaled data while holding per-segment data. That is the
        mirror-image of an unaddressed parameter and just as bad -- the address
        asserting a distinction the pipeline does not implement.

        Both schemes are declared in ``environments.qtm.ingest.RESCALES``, and
        which one the found-data track should use is deliberately left open.
        """
        per_segment = ingest_segments(fixture_catalogue, REFERENCE_CONFIG)
        globally = ingest_segments(
            fixture_catalogue, REFERENCE_CONFIG.with_rescale("global")
        )
        assert per_segment != globally

        # Under `global` the segments must span different amounts of time,
        # because the real rate varies. Under `per-segment` they cannot: the
        # arithmetic pins every span to exactly 511.
        spans = {float(np.asarray(log.values[ARRIVAL])[-1]) for log in globally}
        assert len(spans) > 1
        # Pinned to 511 to within float rounding: this is the degeneracy, and
        # the point of asserting it is that it is a *property of the scheme*
        # rather than an accident of this fixture.
        for log in per_segment:
            span = float(np.asarray(log.values[ARRIVAL])[-1])
            assert span == pytest.approx(DECLARED_SEGMENT_LENGTH - 1, abs=1e-9)

        with pytest.raises(MalformedDesignError):
            REFERENCE_CONFIG.with_rescale(cast("Rescale", "nonsense"))

    def test_the_tie_rule_is_total_and_declared(
        self, fixture_catalogue: Path, tmp_path: Path
    ) -> None:
        """Rows sharing a timestamp order by event id, not by file order.

        The fixture plants exact ties whose two magnitudes differ, so ordering a
        pair the wrong way round changes the ``size`` bytes and is visible in the
        digest. A sort keyed on time alone is not a total order, so its result
        depends on the sort's stability and on the order rows happened to arrive
        -- precisely the hidden ordering dependence invariant 3 forbids.
        """
        lines = fixture_catalogue.read_text(encoding="ascii").splitlines()
        header, rows = lines[0], lines[1:]
        rng = np.random.default_rng(7)
        shuffled = tmp_path / "shuffled.hypo"
        shuffled.write_text(
            "\n".join([header, *(rows[i] for i in rng.permutation(len(rows)))]) + "\n",
            encoding="ascii",
        )
        assert ingest_segments(shuffled, REFERENCE_CONFIG) == ingest_segments(
            fixture_catalogue, REFERENCE_CONFIG
        )

    # ---- Clause 2: the censoring model is applied and versioned ------------

    def test_the_censoring_model_is_applied_and_versioned(
        self, fixture_catalogue: Path
    ) -> None:
        """Declaring the observation process must change what is ingested.

        The test that matters is not that a version string exists but that
        turning the model off produces *different* data. A censoring model that
        is declared, versioned, addressed and never applied would satisfy every
        weaker form of this check.
        """
        uncensored = REFERENCE_CONFIG.without_censoring()
        assert uncensored.censoring is None
        assert ingest_segments(fixture_catalogue, uncensored) != ingest_segments(
            fixture_catalogue, REFERENCE_CONFIG
        )
        assert CENSORING_VERSION in data_version(fixture_catalogue, REFERENCE_CONFIG)
        assert CENSORING_VERSION not in data_version(fixture_catalogue, uncensored)

    def test_censoring_removes_small_events_after_large_ones(
        self, fixture_catalogue: Path
    ) -> None:
        """The model excises the aftershock coda, and *only* there.

        Direction, not merely difference. Review of this test's first form
        showed that asserting ``removed.size > 0`` plus two positive scalars is
        satisfied by a ``removed()`` that returns every even index with the
        parameters never read. So every removed event is checked against the
        model's own predicate: it must be below the completeness magnitude
        implied by some earlier trigger, and no event at or above the trigger
        magnitude may be removed at all.
        """
        catalogue = parse_catalogue(
            fixture_catalogue, REFERENCE_CONFIG.without_censoring()
        )
        removed = DECLARED_CENSORING.removed(catalogue.days, catalogue.size)
        assert removed.dtype == np.bool_
        assert removed.shape == catalogue.size.shape
        assert int(removed.sum()) > 0, (
            "the fixture's planted mainshocks censored nothing"
        )

        model = DECLARED_CENSORING
        triggers = np.flatnonzero(catalogue.size >= model.trigger_magnitude)
        assert triggers.size > 0, "the fixture must contain events above the trigger"

        # No trigger-sized event is ever removed: incompleteness is a failure to
        # resolve *small* events, and a model that dropped large ones would be
        # deleting the very signal the transfer test reads.
        assert not bool(removed[triggers].any())

        for index in np.flatnonzero(removed):
            elapsed = catalogue.days[index] - catalogue.days[triggers]
            in_window = (elapsed > 0.0) & (elapsed <= model.blind_days)
            assert in_window.any(), (
                f"event {index} was removed with no trigger in the preceding "
                f"{model.blind_days} days"
            )
            floor = model.completeness(
                catalogue.size[triggers][in_window], elapsed[in_window]
            )
            assert float(catalogue.size[index]) < float(floor.max()), (
                f"event {index} was removed although its magnitude "
                f"{catalogue.size[index]} is above every completeness floor "
                f"its triggers imply"
            )

    def test_censoring_removes_everything_the_model_condemns(
        self, fixture_catalogue: Path
    ) -> None:
        """The converse: nothing below a floor survives.

        The test above checks every *removal* was justified, which the preflight
        review showed is only half the property -- it is satisfied by a model
        that censors one event per window, or by ``blind_days`` cut to 0.01.
        Both directions together pin the mask exactly: an event is removed if and
        only if some trigger's floor condemns it.

        Computed independently of :meth:`removed`, by the definition rather than
        by the implementation, so this is a second opinion and not a restatement.
        """
        catalogue = parse_catalogue(
            fixture_catalogue, REFERENCE_CONFIG.without_censoring()
        )
        model = DECLARED_CENSORING
        removed = model.removed(catalogue.days, catalogue.size)

        expected = np.zeros(catalogue.size.shape, dtype=np.bool_)
        triggers = np.flatnonzero(catalogue.size >= model.trigger_magnitude)
        for index in triggers:
            elapsed = catalogue.days - catalogue.days[index]
            window = (elapsed > 0.0) & (elapsed <= model.blind_days)
            floor = model.completeness(float(catalogue.size[index]), elapsed)
            expected |= window & (catalogue.size < floor)
        expected[triggers] = False

        np.testing.assert_array_equal(removed, expected)
        assert int(expected.sum()) > 0

    def test_a_trigger_sized_event_is_never_censored_on_the_real_catalogue(
        self,
    ) -> None:
        """The property above, on data that actually stresses it.

        The fixture's three mainshocks are thousands of events apart and never
        fall inside one another's windows, so on the fixture the "no trigger is
        removed" assertion passes without the guard that enforces it. The real
        catalogue does stress it: the preflight review measured 112 events at
        M>=3.0 and one at M>=4.0 inside the M7.2 of 2010-04-04's blind radius,
        which the raw Helmstetter floor condemns. Those are the events the found
        battery is drawn from, so losing them in proportion to mark size is the
        confound this module exists to avoid.
        """
        path = _snapshot_or_skip(PRIMARY)
        catalogue = parse_catalogue(
            path, REFERENCE_CONFIG.without_censoring().with_max_rows(400_000)
        )
        model = DECLARED_CENSORING
        removed = model.removed(catalogue.days, catalogue.size)
        triggers = catalogue.size >= model.trigger_magnitude
        assert int(triggers.sum()) > 0
        assert not bool((removed & triggers).any()), (
            f"{int((removed & triggers).sum())} trigger-magnitude events were "
            f"censored on the real catalogue"
        )
        # And the guard is load-bearing rather than vacuous here: without it the
        # raw floor does condemn some of them.
        condemned = 0
        for index in np.flatnonzero(triggers):
            elapsed = catalogue.days[index] - catalogue.days[triggers]
            window = (elapsed > 0.0) & (elapsed <= model.blind_days)
            if not window.any():
                continue
            floor = model.completeness(
                catalogue.size[triggers][window], elapsed[window]
            )
            condemned += int(float(catalogue.size[index]) < float(floor.max()))
        assert condemned > 0, (
            "no trigger-magnitude event is condemned by the raw floor on this "
            "prefix, so this test would pass without the guard it exists to check"
        )

    # ---- Clause 3: the consensus edit is preregistered ---------------------

    def test_the_consensus_edit_is_preregistered(self, fixture_catalogue: Path) -> None:
        """The ETAS edit is fixed at import, *is* S11's mechanism, and gates the
        address.

        Equality with :data:`SIZE_EXCITATION` is the claim, not similarity: the
        transfer test rests on the answer being one the library already contains
        and seismology already settled.

        The temporal half -- "before any system runs on a segment" -- is carried
        by the address: ``data_version`` mixes in the preregistration digest, so
        no segment can be addressed without the preregistration being fixed
        first. That is a structural fact about the module, not a convention.
        """
        assert CONSENSUS_EDIT == SIZE_EXCITATION
        assert preregistration_digest(CONSENSUS_EDIT) == PREREGISTRATION_DIGEST
        assert len(PREREGISTRATION_DIGEST) == 64
        assert PREREGISTRATION_DIGEST[:12] in data_version(
            fixture_catalogue, REFERENCE_CONFIG
        )

    # ---- The real snapshot -------------------------------------------------

    def test_the_snapshot_declaration_is_well_formed(self) -> None:
        """Both catalogues are declared, with digests of the right shape.

        Unconditional, and deliberately so: the two tests below skip on a
        checkout that has not fetched 287MB, and without this an empty
        ``SNAPSHOT_SHA256`` would pass everywhere the snapshot is absent.
        """
        assert set(SNAPSHOT_SHA256) == {PRIMARY, SENSITIVITY}
        for name, digest in SNAPSHOT_SHA256.items():
            assert len(digest) == 64, name
            assert set(digest) <= set("0123456789abcdef"), name

    def test_a_snapshot_whose_bytes_moved_is_refused(
        self, fixture_catalogue: Path
    ) -> None:
        """Ingesting under a declared digest that does not match raises.

        Refusal rather than a warning: a mismatched snapshot cannot be scored
        against a recorded table, so continuing would write a row addressed to a
        catalogue that did not produce it.
        """
        wrong = hashlib.sha256(b"not this catalogue").hexdigest()
        with pytest.raises(SnapshotMismatchError):
            ingest_segments(fixture_catalogue, REFERENCE_CONFIG, expect_sha256=wrong)

    def test_the_pinned_snapshot_digests_match(self) -> None:
        """Both catalogue files hash to their pinned literals.

        The artefact this is really about: SCEDC could republish under the same
        name, and nothing else in the repository would notice.
        """
        for name in (PRIMARY, SENSITIVITY):
            path = _snapshot_or_skip(name)
            assert digest_of(path) == SNAPSHOT_SHA256[name]

    def test_the_real_catalogue_ingests_to_declared_segments(self) -> None:
        """End to end on the real snapshot, where it has been fetched.

        Reads a bounded prefix rather than all 201MB: ``config.max_rows`` caps
        the parse, and the whole-file digest is still verified, so this checks
        that the real schema parses and segments without adding a minute to
        every suite run.

        An earlier version of this docstring justified the prefix by saying the
        catalogue "is written in time order". ``ingest.py`` measured the
        opposite, and the re-review re-measured it: 14 inversions in the first
        300000 rows, the largest 4.36 seconds backwards. So a prefix of the file
        is a prefix of the sorted catalogue only to within those inversions --
        good enough for a smoke test that the schema parses, and not an identity
        this or anything else may rely on.
        """
        path = _snapshot_or_skip(PRIMARY)
        segments = ingest_segments(
            path,
            REFERENCE_CONFIG.with_max_rows(200_000),
            expect_sha256=SNAPSHOT_SHA256[PRIMARY],
            limit=3,
        )
        assert len(segments) == 3
        assert all(log.n_events == DECLARED_SEGMENT_LENGTH for log in segments)
        assert set(segments[0].values) == {ARRIVAL, SIZE}
