"""Registry behaviour that no acceptance criterion covers.

A12-A15 are the contract for the store, the content address and the partition
boundary. `budget.py` and the duplicate-name rule in `metrics.py` have no gate,
so they are checked here. The names deliberately do not follow the ``test_aN_``
convention that ``scripts/status.py`` reads: crediting these to a criterion would
overstate what that criterion checks.
"""

from __future__ import annotations

import gc
import sqlite3
import threading
import weakref

import pytest

from environments.pointproc.catalogue import METRIC_VERSION, metric_registry
from sciagent.core.errors import (
    BudgetError,
    BudgetExhaustedError,
    DuplicateMetricError,
    RegistryError,
    UnknownMetricError,
)
from sciagent.core.types import MetricName
from sciagent.registry.budget import Budget
from sciagent.registry.metrics import MetricRef, MetricRegistry, MetricSpec
from sciagent.registry.store import ExperimentStore


class TestBudget:
    def test_charging_is_monotone_and_conserves_the_total(self) -> None:
        budget = Budget(total=10.0)
        after = budget.charge(3.0).charge(4.0)
        assert after.spent == 7.0
        assert after.remaining == 3.0
        assert after.total == budget.total
        assert budget.spent == 0.0, "charge must not mutate the budget it is given"

    def test_overrun_raises_rather_than_clamping(self) -> None:
        """A system that silently got less than it asked for would look like
        weak reasoning under a sufficient budget."""
        with pytest.raises(BudgetExhaustedError):
            Budget(total=1.0).charge(1.5)

    def test_a_negative_charge_is_refused(self) -> None:
        with pytest.raises(BudgetError):
            Budget(total=1.0).charge(-0.5)

    def test_an_overrun_budget_cannot_be_constructed(self) -> None:
        with pytest.raises(BudgetError):
            Budget(total=1.0, spent=2.0)

    def test_exhaustion_and_affordability_agree(self) -> None:
        budget = Budget(total=2.0).charge(2.0)
        assert budget.exhausted
        assert not budget.affords(0.1)
        assert budget.affords(0.0)


class TestMetricRegistry:
    def test_version_is_stable_across_calls(self) -> None:
        """The content address of every registered experiment depends on this."""
        assert metric_registry().version == metric_registry().version

    def test_version_changes_when_the_catalogue_changes(self) -> None:
        base = metric_registry()
        extended = base.with_metric(
            MetricSpec(
                ref=MetricRef(name=MetricName("invented"), version=METRIC_VERSION),
                compute=lambda log: float(log.n_events),
            )
        )
        assert extended.version != base.version
        assert len(extended.names) == len(base.names) + 1

    def test_readding_an_identical_spec_is_a_no_op(self) -> None:
        base = metric_registry()
        spec = base.spec("mean_rate")
        assert base.with_metric(spec) is base

    def test_redefining_a_name_is_refused(self) -> None:
        """A metric name is a promise about what recorded numbers mean."""
        base = metric_registry()
        with pytest.raises(DuplicateMetricError):
            base.with_metric(
                MetricSpec(
                    ref=MetricRef(name=MetricName("mean_rate"), version="2.0.0"),
                    compute=lambda log: 0.0,
                )
            )

    def test_an_unknown_metric_raises_a_typed_error(self) -> None:
        with pytest.raises(UnknownMetricError):
            metric_registry().spec("no_such_metric")

    def test_an_empty_range_is_refused(self) -> None:
        with pytest.raises(UnknownMetricError):
            MetricSpec(
                ref=MetricRef(name=MetricName("degenerate"), version="1.0.0"),
                compute=lambda log: 0.0,
                low=1.0,
                high=1.0,
            )

    def test_every_catalogue_metric_declares_a_usable_range(self) -> None:
        registry = metric_registry()
        assert registry.names, "the slice catalogue is empty"
        for name in registry.names:
            spec = registry.spec(name)
            assert spec.high > spec.low, f"{name} declares an empty range"

    def test_an_empty_registry_has_its_own_version(self) -> None:
        assert MetricRegistry().version != metric_registry().version


class TestTheConnectionIsReleasedByRefcountingAlone:
    """The authorizer must not keep the store, and the store not the handle.

    The authorizer closure is installed on the connection and has to read the
    append grant. Capturing the *store* to do that made
    ``connection -> closure -> store -> connection`` a cycle, so the sqlite
    handle outlived its last reference and waited on the cyclic collector.
    Nothing was wrong with the append-only guarantee; what suffered was release
    timing, and on Windows a still-open handle is what makes a temporary
    directory refuse to go away. :class:`~sciagent.registry.store._AppendGrant`
    exists to break that edge.
    """

    def test_the_authorizer_does_not_keep_the_store_alive(self) -> None:
        """A dropped store is freed with the cyclic collector switched off.

        Disabling the collector is the whole test: with it on, a cycle is
        collected eventually and the assertion passes either way, which is
        exactly the reassurance that let the cycle in. Refcounting frees an
        object only when nothing references it, so this can only pass if the
        closure on the connection does not reach the store.
        """
        gc.disable()
        try:
            store = ExperimentStore.in_memory()
            reference = weakref.ref(store)
            assert reference() is not None
            del store
            assert reference() is None, (
                "the store survived its last reference with the cyclic "
                "collector off, so something on the connection still reaches it"
            )
        finally:
            gc.enable()

    def test_the_grant_reaches_neither_the_store_nor_the_connection(self) -> None:
        """The object the closure captures is a leaf, which is why this works."""
        store = ExperimentStore.in_memory()
        reachable = gc.get_referents(store._grant)
        assert not any(isinstance(item, ExperimentStore) for item in reachable)
        assert not any(isinstance(item, sqlite3.Connection) for item in reachable)
        store.close()

    def test_the_connection_refuses_a_second_thread(self) -> None:
        """``check_same_thread`` is what lets the grant be a plain flag.

        The grant is open for the whole of an append rather than for one
        statement, and the argument that nothing can use that window is that no
        other thread can reach the connection at all. That argument was only ever
        written in a docstring; this is it as a check. A future
        ``check_same_thread=False`` for a worker pool would widen the append
        window to anything a second thread could issue, and would fail here
        rather than silently.
        """
        store = ExperimentStore.in_memory()
        refusals: list[RegistryError] = []

        def read() -> None:
            try:
                store.count()
            except RegistryError as error:
                refusals.append(error)

        thread = threading.Thread(target=read)
        thread.start()
        thread.join()
        store.close()
        assert refusals, "a second thread reached the connection and read from it"
        assert "same thread" in str(refusals[0]), (
            f"refused, but not for the reason the grant's safety rests on: "
            f"{refusals[0]}"
        )
