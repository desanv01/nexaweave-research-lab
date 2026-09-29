"""Opt-in real PostgreSQL checks against the dedicated disposable fixture."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from mirofish_knowledge.contracts import KnowledgeScope, Layer
from mirofish_knowledge.bindings import ScopeBindingStore, _stored
from mirofish_knowledge.operations import (
    Busy, CompletionReceipt, Conflict, InvalidTransition, Ledger, MigrationMismatch,
    NotFound, OperationState, StaleAttempt, StorageError, Tombstoned, migrate,
)
from mirofish_knowledge import operations as operations_module

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


class _RollbackFixture(Exception):
    pass


def test_migration_v1_upgrade_preserves_data_and_checksum(factory):
    one = scope()
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                conn.execute("DROP TABLE mf_knowledge.scope_bindings")
                conn.execute("DELETE FROM mf_knowledge.schema_migrations WHERE version = 2")
                old = conn.execute("SELECT checksum, schema_checksum FROM mf_knowledge.schema_migrations WHERE version = 1").fetchone()
                conn.execute("INSERT INTO mf_knowledge.scopes(group_id, canonical_scope) VALUES (%s, %s)",
                             (one.group_id, Jsonb(one.model_dump(mode="json"))))
                migrate(conn)
                assert conn.execute("SELECT checksum, schema_checksum FROM mf_knowledge.schema_migrations WHERE version = 1").fetchone() == old
                assert conn.execute("SELECT count(*) FROM mf_knowledge.scopes WHERE group_id = %s", (one.group_id,)).fetchone()[0] == 1
                assert [row[0] for row in conn.execute("SELECT version FROM mf_knowledge.schema_migrations ORDER BY version")] == [1, 2]
                migrate(conn)
                raise _RollbackFixture


def test_fresh_install_and_idempotence_are_atomic(factory):
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                conn.execute("DROP SCHEMA mf_knowledge CASCADE")
                migrate(conn)
                assert [row[0] for row in conn.execute("SELECT version FROM mf_knowledge.schema_migrations ORDER BY version")] == [1, 2]
                migrate(conn)
                assert conn.execute("SELECT to_regclass('mf_knowledge.scope_bindings') IS NOT NULL").fetchone()[0]
                raise _RollbackFixture


@pytest.mark.parametrize("change", [
    "historical_checksum", "latest_checksum", "missing_first", "missing_all", "future", "latest_shape",
])
def test_migration_records_and_latest_shape_fail_closed(factory, change):
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                if change == "historical_checksum":
                    conn.execute("UPDATE mf_knowledge.schema_migrations SET checksum = %s WHERE version = 1", ("0" * 64,))
                elif change == "latest_checksum":
                    conn.execute("UPDATE mf_knowledge.schema_migrations SET checksum = %s WHERE version = 2", ("0" * 64,))
                elif change == "missing_first":
                    conn.execute("DELETE FROM mf_knowledge.schema_migrations WHERE version = 1")
                elif change == "missing_all":
                    conn.execute("DELETE FROM mf_knowledge.schema_migrations")
                elif change == "future":
                    conn.execute("INSERT INTO mf_knowledge.schema_migrations(version, checksum, schema_checksum) VALUES (3, %s, %s)",
                                 ("a" * 64, "b" * 64))
                else:
                    conn.execute("ALTER TABLE mf_knowledge.scope_bindings ADD COLUMN fixture_drift text")
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                raise _RollbackFixture


def test_failed_v1_upgrade_rolls_back_pending_version(factory, monkeypatch):
    one = scope()
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                conn.execute("DROP TABLE mf_knowledge.scope_bindings")
                conn.execute("DELETE FROM mf_knowledge.schema_migrations WHERE version = 2")
                old = conn.execute("SELECT checksum, schema_checksum FROM mf_knowledge.schema_migrations WHERE version = 1").fetchone()
                conn.execute("INSERT INTO mf_knowledge.scopes(group_id, canonical_scope) VALUES (%s, %s)",
                             (one.group_id, Jsonb(one.model_dump(mode="json"))))
                original_checksum = operations_module._schema_checksum

                def fail_after_pending_ddl(connection):
                    if connection.execute("SELECT to_regclass('mf_knowledge.scope_bindings') IS NOT NULL").fetchone()[0]:
                        raise psycopg.Error("private migration diagnostic")
                    return original_checksum(connection)

                monkeypatch.setattr(operations_module, "_schema_checksum", fail_after_pending_ddl)
                with pytest.raises(StorageError) as error:
                    migrate(conn)
                assert "private" not in str(error.value)
                assert [row[0] for row in conn.execute("SELECT version FROM mf_knowledge.schema_migrations ORDER BY version")] == [1]
                assert conn.execute("SELECT to_regclass('mf_knowledge.scope_bindings') IS NULL").fetchone()[0]
                assert conn.execute("SELECT checksum, schema_checksum FROM mf_knowledge.schema_migrations WHERE version = 1").fetchone() == old
                assert conn.execute("SELECT count(*) FROM mf_knowledge.scopes WHERE group_id = %s", (one.group_id,)).fetchone()[0] == 1
                raise _RollbackFixture


def test_v1_shape_drift_prevents_upgrade(factory):
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                conn.execute("DROP TABLE mf_knowledge.scope_bindings")
                conn.execute("DELETE FROM mf_knowledge.schema_migrations WHERE version = 2")
                conn.execute("ALTER TABLE mf_knowledge.scopes ADD COLUMN fixture_v1_drift text")
                with pytest.raises(MigrationMismatch):
                    migrate(conn)
                assert [row[0] for row in conn.execute("SELECT version FROM mf_knowledge.schema_migrations ORDER BY version")] == [1]
                raise _RollbackFixture


def test_bindings_idempotence_conflicts_and_principal_isolation(factory):
    store = ScopeBindingStore(factory)
    one, two, three, four = scope(), scope(), scope(), scope()
    display = "graph-" + uuid4().hex
    first = store.bind("owner!+@", display, one)
    assert ScopeBindingStore(factory).bind("owner!+@", display, one) == first
    assert store.resolve("owner!+@", display) == first
    with pytest.raises(Conflict):
        store.bind("owner!+@", display, two)
    with pytest.raises(Conflict):
        store.bind("owner!+@", "graph-" + uuid4().hex, one)
    with pytest.raises(Conflict):
        store.bind("other", display, one)
    with pytest.raises(Conflict):
        store.bind("other", "graph-" + uuid4().hex, one)
    second = store.bind("other", display, three)
    assert second.scope == three
    assert store.bind("Owner!+@", display, four).scope == four
    assert store.resolve("owner!+@", display).scope == one
    with pytest.raises(NotFound):
        store.resolve("third", display)
    with factory() as conn:
        assert conn.execute("SELECT count(*) FROM mf_knowledge.scope_bindings WHERE group_id = %s", (one.group_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM mf_knowledge.scopes WHERE group_id = %s", (two.group_id,)).fetchone()[0] == 0
    Ledger(factory).tombstone_scope(one)
    with pytest.raises(Tombstoned):
        store.resolve("owner!+@", display)
    with pytest.raises(Tombstoned):
        store.bind("owner!+@", display, one)


def test_binding_sql_checks_id_domains_and_exact_case_in_rollback(factory):
    valid, invalid, upper, lower = scope(), scope(), scope(), scope()
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                for item in (valid, invalid, upper, lower):
                    Ledger._scope(conn, item, create=True)
                conn.execute(
                    "INSERT INTO mf_knowledge.scope_bindings(principal, display_graph_id, group_id) VALUES (%s, %s, %s)",
                    ("!" + "A" * 127, "g" * 128, valid.group_id),
                )
                for principal in ("", " ", "a\n", "é", "x" * 129):
                    with pytest.raises(psycopg.errors.CheckViolation):
                        with conn.transaction():
                            conn.execute(
                                "INSERT INTO mf_knowledge.scope_bindings(principal, display_graph_id, group_id) VALUES (%s, %s, %s)",
                                (principal, "valid", invalid.group_id),
                            )
                for display in ("", "a/b", "é", "x" * 129):
                    with pytest.raises(psycopg.errors.CheckViolation):
                        with conn.transaction():
                            conn.execute(
                                "INSERT INTO mf_knowledge.scope_bindings(principal, display_graph_id, group_id) VALUES (%s, %s, %s)",
                                ("valid", display, invalid.group_id),
                            )
                conn.execute(
                    "INSERT INTO mf_knowledge.scope_bindings(principal, display_graph_id, group_id) VALUES (%s, %s, %s)",
                    ("Owner", "same", upper.group_id),
                )
                conn.execute(
                    "INSERT INTO mf_knowledge.scope_bindings(principal, display_graph_id, group_id) VALUES (%s, %s, %s)",
                    ("owner", "same", lower.group_id),
                )
                assert conn.execute("SELECT count(*) FROM mf_knowledge.scope_bindings WHERE display_graph_id = %s",
                                    ("same",)).fetchone()[0] == 2
                assert conn.execute("SELECT group_id FROM mf_knowledge.scope_bindings WHERE principal = %s AND display_graph_id = %s",
                                    ("Owner", "same")).fetchone()[0] == upper.group_id
                raise _RollbackFixture


def test_binding_stored_canonical_tamper_is_rejected_in_rollback(factory):
    one = scope()
    store = ScopeBindingStore(factory)
    display = "graph-" + uuid4().hex
    store.bind("tamper", display, one)
    with factory() as conn:
        with pytest.raises(_RollbackFixture):
            with conn.transaction():
                conn.execute("UPDATE mf_knowledge.scopes SET canonical_scope = %s WHERE group_id = %s",
                             (Jsonb({**one.model_dump(mode="json"), "layer": "analysis"}), one.group_id))
                row = conn.execute(
                    "SELECT b.principal, b.display_graph_id, b.group_id, b.created_at, s.canonical_scope, s.tombstoned "
                    "FROM mf_knowledge.scope_bindings b JOIN mf_knowledge.scopes s ON s.group_id = b.group_id "
                    "WHERE b.principal = %s AND b.display_graph_id = %s", ("tamper", display),
                ).fetchone()
                with pytest.raises(Conflict):
                    _stored(row)
                raise _RollbackFixture


@pytest.mark.parametrize("same_scope", [True, False], ids=["identical", "competing"])
def test_concurrent_binding_winner_and_no_orphan_scope(factory, same_scope):
    one, two = scope(), scope()
    display = "graph-" + uuid4().hex
    barrier = Barrier(2)

    def simultaneous(target):
        barrier.wait(timeout=5)
        return ScopeBindingStore(factory).bind("race", display, target)

    targets = (one, one if same_scope else two)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(simultaneous, target) for target in targets]
        wins, conflicts = [], 0
        for future in futures:
            try:
                wins.append(future.result(timeout=8))
            except Conflict:
                conflicts += 1
    assert len(wins) == (2 if same_scope else 1)
    assert conflicts == (0 if same_scope else 1)
    persisted = ScopeBindingStore(factory).resolve("race", display)
    assert all(item == persisted for item in wins)
    with factory() as conn:
        assert conn.execute("SELECT count(*) FROM mf_knowledge.scope_bindings WHERE principal = %s AND display_graph_id = %s",
                            ("race", display)).fetchone()[0] == 1
        if not same_scope:
            loser = two if persisted.scope == one else one
            assert conn.execute("SELECT count(*) FROM mf_knowledge.scopes WHERE group_id = %s",
                                (loser.group_id,)).fetchone()[0] == 0


def test_readers_share_and_writer_waits_without_open_transaction(factory):
    one, other, operation = scope(), scope(), uuid4()
    ledger = Ledger(factory)
    ledger.admit(one, operation, "a" * 64)
    ledger.register_scope(other)
    captured = []

    def dedicated():
        connection = factory()
        captured.append(connection)
        return connection

    reader_ledger = Ledger(dedicated)
    with reader_ledger.read_scope(one):
        assert captured[0].execute("SHOW statement_timeout").fetchone()[0] == "5s"
        assert captured[0].execute("SHOW lock_timeout").fetchone()[0] == "2s"
        with Ledger(factory).read_scope(one):
            with pytest.raises(Busy):
                ledger.claim(one, operation)
            with pytest.raises(Busy):
                ledger.tombstone_scope(one)
            with Ledger(factory).read_scope(other):
                pass
            with factory() as observer:
                state = observer.execute(
                    "SELECT state, xact_start FROM pg_stat_activity WHERE pid = %s",
                    (captured[0].info.backend_pid,),
                ).fetchone()
                assert state[0] == "idle" and state[1] is None
    assert captured[0].closed
    claimed = ledger.claim(one, operation)
    with pytest.raises(Busy):
        with Ledger(factory).read_scope(one):
            pass
    ledger.mark_uncertain(one, operation, claimed.attempt_id, "response_lost")
    with pytest.raises(Busy):
        with Ledger(factory).read_scope(one):
            pass


def test_read_guard_releases_on_exception_and_tombstone(factory):
    one = scope()
    ledger = Ledger(factory)
    ledger.register_scope(one)
    with pytest.raises(RuntimeError):
        with ledger.read_scope(one):
            raise RuntimeError("synthetic provider failure")
    with ledger.read_scope(one):
        pass
    ledger.tombstone_scope(one)
    with pytest.raises(Tombstoned):
        with ledger.read_scope(one):
            pass
