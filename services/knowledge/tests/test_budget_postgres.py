"""Disposable fixture qualification for durable budget admission and bridge."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from mirofish_execution import (BudgetBusy, BudgetConflict, BudgetDenied, BudgetLedger,
                                BudgetUncertain, BudgetUnavailable, BudgetedIngestion, MigrationMismatch,
                                ReservationState, migrate)
from mirofish_knowledge.ingestion import KnowledgeIngestionCoordinator
from mirofish_knowledge.operations import Ledger
from mirofish_knowledge.source_bridge import SourceIngestionBridge
from test_source_bridge_postgres import (FakeProvider, factory, ontology,
                                         owned_fixture)

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def migrated(factory):
    with factory() as conn:
        migrate(conn)


def setup(factory, cap=10):
    display, scope, retained = owned_fixture(factory)
    account = uuid4()
    ledger = BudgetLedger(factory)
    ledger.create_account("owner", scope.project_id, account, cap)
    return display, scope, retained, account, ledger


def test_migration_idempotence_and_catalog_drift(factory):
    with factory() as conn:
        migrate(conn)
        original = conn.execute("SELECT schema_checksum FROM mf_execution.schema_migrations WHERE version=1").fetchone()[0]
    with factory() as conn:
        with pytest.raises(MigrationMismatch):
            with conn.transaction():
                conn.execute("CREATE TABLE mf_execution.unexpected(id integer)")
                migrate(conn)
    with factory() as conn:
        assert conn.execute("SELECT schema_checksum FROM mf_execution.schema_migrations WHERE version=1").fetchone()[0] == original


def test_default_denied_owner_isolation_and_restart(factory):
    display, scope, retained = owned_fixture(factory)
    plan = SourceIngestionBridge(factory).plan("owner", display, retained.source_revision, uuid4(), ontology())
    ledger = BudgetLedger(factory)
    with pytest.raises(BudgetDenied):
        ledger.reserve("owner", uuid4(), scope, plan.source.operation_id,
                       plan.request_fingerprint, 1, plan.source.evidence_ids)
    account = uuid4()
    ledger.create_account("owner", scope.project_id, account, 5)
    with pytest.raises(BudgetDenied):
        ledger.status("other", account)
    with pytest.raises(BudgetDenied):
        ledger.create_account("other", scope.project_id, uuid4(), 5)
    with pytest.raises(BudgetConflict):
        ledger.create_account("owner", scope.project_id, account, 6)
    first = ledger.reserve("owner", account, scope, plan.source.operation_id,
                           plan.request_fingerprint, 4, plan.source.evidence_ids)
    assert BudgetLedger(factory).status("owner", account).remaining_microusd == 1
    assert ledger.reserve("owner", account, scope, plan.source.operation_id,
                          plan.request_fingerprint, 4, plan.source.evidence_ids) == first
    with pytest.raises(BudgetConflict):
        ledger.reserve("owner", account, scope, plan.source.operation_id,
                       plan.request_fingerprint, 3, plan.source.evidence_ids)
    assert ledger.release_undispatched("owner", account, plan.source.operation_id,
                                       first.attempt_id).state == ReservationState.released
    assert ledger.status("owner", account).remaining_microusd == 5


def test_parallel_reservations_and_attempt_fencing(factory):
    display, scope, retained, account, ledger = setup(factory, 5)
    bridge = SourceIngestionBridge(factory)
    plans = [bridge.plan("owner", display, retained.source_revision, uuid4(), ontology()) for _ in range(2)]
    def reserve(plan):
        try:
            return ledger.reserve("owner", account, scope, plan.source.operation_id,
                                  plan.request_fingerprint, 4, plan.source.evidence_ids)
        except BudgetDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(reserve, plans))
    assert sum(row is not None for row in rows) == 1
    winner = next(row for row in rows if row is not None)
    assert ledger.start("owner", account, winner.operation_id, winner.attempt_id).state == ReservationState.started
    with pytest.raises(BudgetBusy):
        ledger.start("owner", account, winner.operation_id, winner.attempt_id)
    with pytest.raises(BudgetConflict):
        ledger.mark_uncertain("owner", account, winner.operation_id, uuid4(), "dispatch_uncertain")
    with pytest.raises(BudgetBusy):
        ledger.release_undispatched("owner", account, winner.operation_id, winner.attempt_id)
    ledger.mark_uncertain("owner", account, winner.operation_id, winner.attempt_id, "dispatch_uncertain")
    assert ledger.status("owner", account).uncertain_microusd == 4
    with pytest.raises(BudgetBusy):
        ledger.release_undispatched("owner", account, winner.operation_id, winner.attempt_id)


def test_corrupt_saved_receipt_fails_closed(factory):
    display, scope, retained, account, ledger = setup(factory, 5)
    plan = SourceIngestionBridge(factory).plan("owner", display, retained.source_revision,
                                               uuid4(), ontology())
    row = ledger.reserve("owner", account, scope, plan.source.operation_id,
                         plan.request_fingerprint, 2, plan.source.evidence_ids)
    ledger.start("owner", account, row.operation_id, row.attempt_id)
    with factory() as conn:
        conn.execute("UPDATE mf_execution.reservations SET state='settled',receipt=%s "
                     "WHERE account_id=%s AND operation_id=%s",
                     (Jsonb({"group_id": "wrong", "episode_id": "wrong",
                             "fingerprint": "wrong", "evidence_ids": []}),
                      account, row.operation_id))
    with pytest.raises(BudgetUncertain):
        ledger.reserve("owner", account, scope, row.operation_id,
                       plan.request_fingerprint, 2, plan.source.evidence_ids)
    assert ledger.status("owner", account).remaining_microusd == 3


@pytest.mark.asyncio
async def test_real_retained_bridge_accounting_duplicate_and_failure(factory):
    display, scope, retained, account, ledger = setup(factory, 10)
    provider = FakeProvider()
    bridge = SourceIngestionBridge(factory)
    coordinator = KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)
    service = BudgetedIngestion(bridge, ledger, coordinator)
    spec, operation = ontology(), uuid4()
    receipt = await service.ingest("owner", display, retained.source_revision, operation,
                                   spec, account_id=account, ceiling_microusd=4)
    assert receipt.evidence_ids == tuple(v.evidence_id for v in retained.passages)
    assert provider.calls == 1
    assert await service.ingest("owner", display, retained.source_revision, operation,
                                spec, account_id=account, ceiling_microusd=4) == receipt
    assert provider.calls == 1
    status = BudgetLedger(factory).status("owner", account)
    assert (status.accounted_ceiling_microusd, status.remaining_microusd,
            status.actual_usage_microusd) == (4, 6, None)
    failed = FakeProvider(fail=True)
    failing = BudgetedIngestion(bridge, ledger,
                KnowledgeIngestionCoordinator(Ledger(factory), failed, timeout_seconds=5))
    failed_op = uuid4()
    with pytest.raises(BudgetUncertain):
        await failing.ingest("owner", display, retained.source_revision, failed_op,
                             spec, account_id=account, ceiling_microusd=5)
    assert failed.calls == 1
    assert ledger.status("owner", account).remaining_microusd == 1
    with pytest.raises(BudgetBusy):
        await failing.ingest("owner", display, retained.source_revision, failed_op,
                             spec, account_id=account, ceiling_microusd=5)
    assert failed.calls == 1


@pytest.mark.asyncio
async def test_cancellation_keeps_ceiling_unavailable(factory):
    display, scope, retained, account, ledger = setup(factory, 5)
    entered = asyncio.Event()
    class WaitingProvider(FakeProvider):
        async def ingest(self, scope, source, ontology):
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()
    provider = WaitingProvider()
    service = BudgetedIngestion(SourceIngestionBridge(factory), ledger,
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=30))
    spec, operation = ontology(), uuid4()
    task = asyncio.create_task(service.ingest("owner", display, retained.source_revision,
                               operation, spec, account_id=account, ceiling_microusd=5))
    await asyncio.wait_for(entered.wait(), 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.status("owner", account).remaining_microusd == 0
    with pytest.raises(BudgetBusy):
        await service.ingest("owner", display, retained.source_revision,
                             operation, spec, account_id=account, ceiling_microusd=5)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_concurrent_same_operation_dispatch_once(factory):
    display, scope, retained, account, ledger = setup(factory, 5)
    entered, release = asyncio.Event(), asyncio.Event()
    class HeldProvider(FakeProvider):
        attempts = 0
        async def ingest(self, scope, source, ontology):
            self.attempts += 1
            entered.set()
            await release.wait()
            return await super().ingest(scope, source, ontology)
    provider = HeldProvider()
    service = BudgetedIngestion(SourceIngestionBridge(factory), ledger,
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=30))
    spec, operation = ontology(), uuid4()
    first = asyncio.create_task(service.ingest("owner", display, retained.source_revision,
                                operation, spec, account_id=account, ceiling_microusd=5))
    await asyncio.wait_for(entered.wait(), 15)
    second = asyncio.create_task(service.ingest("owner", display, retained.source_revision,
                                 operation, spec, account_id=account, ceiling_microusd=5))
    with pytest.raises(BudgetBusy):
        await asyncio.wait_for(second, 15)
    assert provider.attempts == 1
    release.set()
    receipt = await asyncio.wait_for(first, 15)
    assert await service.ingest("owner", display, retained.source_revision,
                                operation, spec, account_id=account,
                                ceiling_microusd=5) == receipt
    assert provider.calls == 1
    assert provider.attempts == 1
    assert ledger.status("owner", account).accounted_ceiling_microusd == 5


@pytest.mark.asyncio
async def test_cancel_during_start_never_dispatches(factory):
    display, scope, retained, account, _ = setup(factory, 5)
    entered, allow_start, quarantined = Event(), Event(), Event()
    class StartBarrierLedger(BudgetLedger):
        def start(self, *args):
            entered.set()
            assert allow_start.wait(15)
            return super().start(*args)
        def mark_uncertain(self, *args):
            try:
                return super().mark_uncertain(*args)
            finally:
                quarantined.set()
    ledger = StartBarrierLedger(factory)
    provider = FakeProvider()
    service = BudgetedIngestion(SourceIngestionBridge(factory), ledger,
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5))
    spec, operation = ontology(), uuid4()
    task = asyncio.create_task(service.ingest("owner", display, retained.source_revision,
                               operation, spec, account_id=account, ceiling_microusd=5))
    assert await asyncio.to_thread(entered.wait, 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    allow_start.set()
    assert await asyncio.to_thread(quarantined.wait, 15)
    assert provider.calls == 0
    status = BudgetLedger(factory).status("owner", account)
    assert status.remaining_microusd == 0
    with pytest.raises(BudgetBusy):
        await BudgetedIngestion(SourceIngestionBridge(factory), BudgetLedger(factory),
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)).ingest(
                  "owner", display, retained.source_revision, operation, spec,
                  account_id=account, ceiling_microusd=5)
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_settlement_commits_while_caller_cancels(factory):
    display, scope, retained, account, _ = setup(factory, 5)
    settling, allow_settle, settled, quarantine_attempted = Event(), Event(), Event(), Event()
    class SettleBarrierLedger(BudgetLedger):
        def settle(self, *args):
            settling.set()
            assert allow_settle.wait(15)
            try:
                return super().settle(*args)
            finally:
                settled.set()
        def mark_uncertain(self, *args):
            assert settled.wait(15)
            try:
                return super().mark_uncertain(*args)
            finally:
                quarantine_attempted.set()
    ledger = SettleBarrierLedger(factory)
    provider = FakeProvider()
    service = BudgetedIngestion(SourceIngestionBridge(factory), ledger,
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5))
    spec, operation = ontology(), uuid4()
    task = asyncio.create_task(service.ingest("owner", display, retained.source_revision,
                               operation, spec, account_id=account, ceiling_microusd=5))
    assert await asyncio.to_thread(settling.wait, 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    allow_settle.set()
    assert await asyncio.to_thread(quarantine_attempted.wait, 15)
    status = BudgetLedger(factory).status("owner", account)
    assert (status.accounted_ceiling_microusd, status.remaining_microusd) == (5, 0)
    await BudgetedIngestion(SourceIngestionBridge(factory), BudgetLedger(factory),
          KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)).ingest(
              "owner", display, retained.source_revision, operation, spec,
              account_id=account, ceiling_microusd=5)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_failed_uncertainty_write_still_holds_reservation(factory):
    display, scope, retained, account, _ = setup(factory, 5)
    entered, failed_write = asyncio.Event(), Event()
    class FailingLedger(BudgetLedger):
        def mark_uncertain(self, *args):
            failed_write.set()
            raise BudgetUnavailable()
    class WaitingProvider(FakeProvider):
        async def ingest(self, scope, source, ontology):
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()
    ledger = FailingLedger(factory)
    provider = WaitingProvider()
    service = BudgetedIngestion(SourceIngestionBridge(factory), ledger,
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5))
    spec, operation = ontology(), uuid4()
    task = asyncio.create_task(service.ingest("owner", display, retained.source_revision,
                               operation, spec, account_id=account, ceiling_microusd=5))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(failed_write.wait, 15)
    status = BudgetLedger(factory).status("owner", account)
    assert (status.reserved_microusd, status.remaining_microusd) == (5, 0)
    with pytest.raises(BudgetBusy):
        await BudgetedIngestion(SourceIngestionBridge(factory), BudgetLedger(factory),
              KnowledgeIngestionCoordinator(Ledger(factory), provider, timeout_seconds=5)).ingest(
                  "owner", display, retained.source_revision, operation, spec,
                  account_id=account, ceiling_microusd=5)
    assert provider.calls == 1
