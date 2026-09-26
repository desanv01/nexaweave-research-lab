"""Opt-in real PostgreSQL checks against the dedicated disposable fixture."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from mirofish_knowledge.contracts import KnowledgeScope, Layer
from mirofish_knowledge.operations import (
    Busy, CompletionReceipt, Conflict, InvalidTransition, Ledger, MigrationMismatch,
    OperationState, StaleAttempt, Tombstoned, migrate,
)

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def factory():
    if os.getenv("KNOWLEDGE_POSTGRES_INTEGRATION") != "1":
        pytest.skip("disposable PostgreSQL integration disabled")
    dsn = os.getenv("KNOWLEDGE_POSTGRES_TEST_DSN")
    if not dsn:
        pytest.fail("fixture-only PostgreSQL DSN required when integration is enabled")
    if any(os.getenv(name) for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")):
        pytest.fail("PostgreSQL connection override is not allowed for this fixture")
    settings = conninfo_to_dict(dsn)
    permitted = {"dbname", "host", "port", "user", "password", "connect_timeout"}
    if (settings.get("dbname") != "mirofish_operations_test"
            or settings.get("host") != "127.0.0.1"
            or settings.get("port") != "15432"
            or settings.get("user") != "mirofish_fixture"
            or not settings.get("password")
            or set(settings) - permitted):
        pytest.fail("PostgreSQL DSN is not the approved loopback fixture")

    def connect():
        return psycopg.connect(dsn, connect_timeout=3)

    with connect() as conn:
        migrate(conn)
    return connect


def scope():
    return KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)


def receipt(scope, operation_id, fingerprint):
    return CompletionReceipt(scope.group_id, scope.episode_uuid(operation_id), fingerprint, (uuid4(),))


def test_same_identity_and_changed_input_roll_back(factory):
    ledger, one, op = Ledger(factory), scope(), uuid4()
    first = ledger.admit(one, op, "a" * 64)
    assert ledger.admit(one, op, "a" * 64) == first
    with pytest.raises(Conflict):
        ledger.admit(one, op, "b" * 64)
    assert ledger.get(one, op) == first


def test_competing_claims_and_independent_scopes(factory):
    one, two, op, other = scope(), scope(), uuid4(), uuid4()
    ledger = Ledger(factory)
    for target, identity in ((one, op), (one, other), (two, op)):
        ledger.admit(target, identity, "a" * 64)
    barrier = Barrier(2)

    def simultaneous_claim():
        barrier.wait(timeout=5)
        return Ledger(factory).claim(one, op)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(simultaneous_claim) for _ in range(2)]
        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=8))
            except Busy:
                pass
    assert len(results) == 1
    with factory() as conn:
        assert conn.execute("SELECT count(*) FROM mf_knowledge.scope_admissions WHERE group_id = %s", (one.group_id,)).fetchone()[0] == 1
    with pytest.raises(Busy):
        Ledger(factory).claim(one, other)
    independent = Ledger(factory).claim(two, op)
    assert independent.operation.state == OperationState.running
    ledger.fail_no_effect(one, op, results[0].attempt_id, "no_dispatch")
    ledger.fail_no_effect(two, op, independent.attempt_id, "no_dispatch")


def test_concurrent_changed_fingerprint_admits_only_one_identity(factory):
    one, op = scope(), uuid4()
    barrier = Barrier(2)

    def simultaneous_admit(fingerprint):
        barrier.wait(timeout=5)
        return Ledger(factory).admit(one, op, fingerprint)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(simultaneous_admit, value * 64) for value in ("a", "b")]
        winners, conflicts = [], 0
        for future in futures:
            try:
                winners.append(future.result(timeout=8))
            except Conflict:
                conflicts += 1
    assert len(winners) == conflicts == 1
    assert Ledger(factory).get(one, op) == winners[0]
    with factory() as conn:
        assert conn.execute("SELECT count(*) FROM mf_knowledge.scope_admissions WHERE group_id = %s", (one.group_id,)).fetchone()[0] == 0


def test_uncertain_admission_stale_token_and_fresh_visibility(factory):
    ledger, one, op, other = Ledger(factory), scope(), uuid4(), uuid4()
    ledger.admit(one, op, "a" * 64)
    ledger.admit(one, other, "b" * 64)
    first = ledger.claim(one, op)
    with pytest.raises(StaleAttempt):
        ledger.heartbeat(one, op, uuid4())
    assert ledger.mark_uncertain(one, op, first.attempt_id, "response_lost").state == OperationState.uncertain
    with pytest.raises(Busy):
        Ledger(factory).claim(one, other)
    with pytest.raises(InvalidTransition):
        ledger.fail_no_effect(one, op, first.attempt_id, "no_dispatch")
    assert Ledger(factory).get(one, op).state == OperationState.uncertain
    with pytest.raises(InvalidTransition):
        ledger.complete(one, op, first.attempt_id, receipt(one, op, "a" * 64))
    with pytest.raises(Busy):
        Ledger(factory).claim(one, other)


def test_no_effect_retry_uses_new_token_and_preserves_history(factory):
    ledger, one, op = Ledger(factory), scope(), uuid4()
    ledger.admit(one, op, "a" * 64)
    first = ledger.claim(one, op)
    ledger.fail_no_effect(one, op, first.attempt_id, "before_dispatch")
    second = Ledger(factory).claim(one, op)
    assert second.attempt_id != first.attempt_id
    with pytest.raises(StaleAttempt):
        ledger.heartbeat(one, op, first.attempt_id)
    with factory() as conn:
        count = conn.execute("SELECT count(*) FROM mf_knowledge.attempts WHERE group_id = %s AND operation_id = %s",
                             (one.group_id, op)).fetchone()[0]
    assert count == 2
    ledger.fail_no_effect(one, op, second.attempt_id, "before_dispatch")


def test_pending_cancel_and_tombstone_during_running(factory):
    ledger, one, pending, running = Ledger(factory), scope(), uuid4(), uuid4()
    ledger.admit(one, pending, "a" * 64)
    ledger.admit(one, running, "b" * 64)
    assert ledger.cancel_pending(one, pending).state == OperationState.cancelled
    with pytest.raises(InvalidTransition):
        ledger.claim(one, pending)
    claim = ledger.claim(one, running)
    with pytest.raises(InvalidTransition):
        ledger.cancel_pending(one, running)
    ledger.tombstone_scope(one)
    with pytest.raises(Tombstoned):
        ledger.admit(one, uuid4(), "c" * 64)
    with pytest.raises(Tombstoned):
        ledger.claim(one, running)
    assert ledger.complete(one, running, claim.attempt_id, receipt(one, running, "b" * 64)).state == OperationState.completed


def test_duplicate_completion_and_conflicting_receipt(factory):
    ledger, one, op = Ledger(factory), scope(), uuid4()
    ledger.admit(one, op, "a" * 64)
    claimed = ledger.claim(one, op)
    with pytest.raises(ValueError):
        ledger.complete(one, op, claimed.attempt_id,
                        CompletionReceipt("wrong", one.episode_uuid(op), "a" * 64))
    assert ledger.get(one, op).state == OperationState.running
    first_receipt = receipt(one, op, "a" * 64)
    completed = ledger.complete(one, op, claimed.attempt_id, first_receipt)
    assert ledger.complete(one, op, claimed.attempt_id, first_receipt) == completed
    with pytest.raises(Conflict):
        ledger.complete(one, op, claimed.attempt_id, receipt(one, op, "a" * 64))
    with pytest.raises(StaleAttempt):
        ledger.complete(one, op, uuid4(), first_receipt)
    assert Ledger(factory).get(one, op).receipt == first_receipt


def test_migration_checksum_mismatch_rejected(factory):
    # Restore the fixture's migration record even if the assertion fails.
    with factory() as conn:
        original = conn.execute("SELECT checksum FROM mf_knowledge.schema_migrations WHERE version = 1").fetchone()[0]
        conn.execute("UPDATE mf_knowledge.schema_migrations SET checksum = %s WHERE version = 1", ("0" * 64,))
    try:
        with factory() as conn:
            with pytest.raises(MigrationMismatch):
                migrate(conn)
    finally:
        with factory() as conn:
            conn.execute("UPDATE mf_knowledge.schema_migrations SET checksum = %s WHERE version = 1", (original,))


def test_migration_schema_drift_rejected(factory):
    with pytest.raises(MigrationMismatch):
        with factory() as conn:
            with conn.transaction():
                conn.execute("ALTER TABLE mf_knowledge.scopes ADD COLUMN fixture_drift text")
                migrate(conn)
