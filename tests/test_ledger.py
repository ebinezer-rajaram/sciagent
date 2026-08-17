"""The campaign ledger: what a matrix has already run, and what it read.

SPEC §11 item 15 carries **no acceptance criterion**, so nothing here is named
``test_aN_``. Crediting these to a gate would tell ``scripts/status.py`` that a
criterion covers them when none does. What they check instead is stated per
class: the append-only guarantees A12 asks of the experiment store, held here by
the same :mod:`sciagent.registry.backing` machinery, plus the two things a
ledger needs and an experiment store does not -- a non-finite reading, and an
address that distinguishes a re-run from a correction.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from sciagent.core.errors import AppendOnlyViolationError, RegistryConflictError
from sciagent.core.types import (
    DataVersion,
    EnvVersion,
    FrozenDict,
    MetricVersion,
    Seed,
)
from sciagent.registry.ledger import CampaignLedger, LedgerEntry
from sciagent.registry.store import ExperimentKey


def key(**config: str) -> ExperimentKey:
    """Return a cell address whose config is exactly ``config``."""
    return ExperimentKey(
        env_version=EnvVersion("pointproc/1.0.0"),
        config=FrozenDict[str, str](config),
        data_version=DataVersion("slice/1"),
        metric_version=MetricVersion("1.2.0"),
        seed=Seed(20260911),
    )


READING = {"d1_structural_distance": 1.5, "d3_intervention_similarity": 0.96}


class TestTheLedgerHoldsWhatAnExperimentStoreCannot:
    """The reason the ledger is a sibling store and not a second registry."""

    def test_a_non_finite_reading_round_trips_exactly(self) -> None:
        # -inf is D2's documented value when the candidate ruled out something
        # that happens, and log_score's whenever the truth got zero mass -- B1's
        # ordinary case. nan is D2 and D3 on an empty held-out battery.
        # ExperimentStore.append refuses both, correctly, for an *experiment*.
        reading = {"d2": -math.inf, "d3": math.nan, "d1": 0.0, "d6": 12.5}
        with CampaignLedger.in_memory() as ledger:
            ledger.append(key(cell="V7/S11/07"), reading=reading)
            stored = ledger.get(key(cell="V7/S11/07").digest)
        assert stored is not None
        assert stored.reading["d2"] == -math.inf
        assert math.isnan(stored.reading["d3"])
        assert stored.reading["d1"] == 0.0
        assert stored.reading["d6"] == 12.5

    def test_a_reading_is_recovered_bit_for_bit(self) -> None:
        # Not "to within a tolerance": a resumed campaign reports the number the
        # first pass computed, and a rounded round-trip would make two passes
        # over one matrix disagree in the last place for no stated reason.
        reading = {"d3": 0.1 + 0.2, "d4": 1e-300, "d5": 2.0**-1074}
        with CampaignLedger.in_memory() as ledger:
            ledger.append(key(cell="V1/S1/00"), reading=reading)
            stored = ledger.get(key(cell="V1/S1/00").digest)
        assert stored is not None
        assert stored.reading == reading
        for name, value in reading.items():
            assert stored.reading[name].hex() == value.hex()

    def test_two_nans_are_the_same_reading(self) -> None:
        # Deliberate, and the reason the digest is over the hex text rather than
        # over the packed double. A nan means "the battery was empty"; its
        # payload bits carry nothing, and they are exactly the sort of thing the
        # measured Windows/Ubuntu divergence would move. Comparing by value
        # would be worse still -- nan != nan, so an idempotent re-append of an
        # honest rerun would raise.
        with CampaignLedger.in_memory() as ledger:
            first = ledger.append(key(cell="B1/S9/03"), reading={"d3": math.nan})
            again = ledger.append(key(cell="B1/S9/03"), reading={"d3": float("nan")})
        assert again.sequence == first.sequence
        assert again.reading_digest == first.reading_digest


class TestTheLedgerIsAppendOnly:
    """A12's three layers, on the store A12 does not name."""

    def test_no_update_or_delete_method_exists(self) -> None:
        # Layer 1. The one layer backing.py cannot hold, since it is a property
        # of this class's own surface.
        surface = dir(CampaignLedger)
        assert not [
            name
            for name in surface
            if any(verb in name for verb in ("update", "delete", "remove", "set_"))
        ]

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE cells SET reading = '{}'",
            "DELETE FROM cells",
            "INSERT INTO cells (digest) VALUES ('x')",
            "DROP TABLE cells",
            "PRAGMA writable_schema = ON",
        ],
    )
    def test_the_connection_refuses_every_mutation(self, statement: str) -> None:
        # Layer 2. Including PRAGMA: writable_schema would otherwise be a route
        # to the schema itself.
        with CampaignLedger.in_memory() as ledger:
            ledger.append(key(cell="V1/S1/00"), reading=READING)
            with pytest.raises(AppendOnlyViolationError):
                ledger.query(statement)

    def test_the_schema_refuses_a_connection_the_ledger_never_opened(
        self, tmp_path: object
    ) -> None:
        # Layer 3. A raw sqlite3 handle carries no authorizer, so only the
        # triggers stand between another tool and a registered row.
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        path = tmp_path / "matrix.sqlite"
        with CampaignLedger.open(path) as ledger:
            ledger.append(key(cell="V1/S1/00"), reading=READING)
        raw = sqlite3.connect(path)
        try:
            for statement in ("UPDATE cells SET reading = '{}'", "DELETE FROM cells"):
                with pytest.raises(sqlite3.IntegrityError):
                    raw.execute(statement)
        finally:
            raw.close()


class TestOneAddressHoldsOneReading:
    def test_an_identical_reading_re_appends_to_the_same_row(self) -> None:
        with CampaignLedger.in_memory() as ledger:
            first = ledger.append(key(cell="V7/S11/07"), reading=READING)
            again = ledger.append(key(cell="V7/S11/07"), reading=dict(READING))
        assert again.sequence == first.sequence
        assert again == first

    def test_a_disagreeing_reading_at_one_address_raises(self) -> None:
        # This is invariant 3 at matrix scale. The address covers everything
        # that should determine the cell, so a second pass producing a different
        # number means something outside it moved -- the platform, a library, an
        # unpinned version. It is a framework bug and never a finding, so it
        # must raise rather than append a second row.
        with CampaignLedger.in_memory() as ledger:
            ledger.append(key(cell="V7/S11/07"), reading={"d3": 0.960})
            with pytest.raises(RegistryConflictError):
                ledger.append(key(cell="V7/S11/07"), reading={"d3": 0.961})

    def test_a_reading_that_gained_a_field_is_a_disagreement(self) -> None:
        # A named payload rather than a positional vector, so this is caught
        # rather than silently re-read against the wrong names.
        with CampaignLedger.in_memory() as ledger:
            ledger.append(key(cell="V7/S11/07"), reading={"d3": 0.960})
            with pytest.raises(RegistryConflictError):
                ledger.append(key(cell="V7/S11/07"), reading={"d3": 0.960, "d1": 1.5})

    def test_the_reading_digest_does_not_depend_on_insertion_order(self) -> None:
        left = LedgerEntry.digest_of({"d1": 1.5, "d3": 0.96})
        right = LedgerEntry.digest_of({"d3": 0.96, "d1": 1.5})
        assert left == right


class TestReading:
    def test_entries_come_back_in_insertion_order(self) -> None:
        with CampaignLedger.in_memory() as ledger:
            for index in range(3):
                ledger.append(key(cell=f"V1/S1/{index:02d}"), reading=READING)
            entries = ledger.entries()
        assert [entry.key.config["cell"] for entry in entries] == [
            "V1/S1/00",
            "V1/S1/01",
            "V1/S1/02",
        ]
        assert [entry.sequence for entry in entries] == sorted(
            entry.sequence for entry in entries
        )

    def test_an_unregistered_address_is_absent_rather_than_an_error(self) -> None:
        with CampaignLedger.in_memory() as ledger:
            assert ledger.get(key(cell="V7/S11/07").digest) is None
            assert not ledger.contains(key(cell="V7/S11/07").digest)
            assert ledger.count() == 0
