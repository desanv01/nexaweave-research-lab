"""Explicit disposable PG17/18 setting/rollback/timeout qualification."""
import os
import time
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from nexaweave_storage.transaction_settings import apply_runtime_settings, _can_batch

pytestmark = pytest.mark.postgres

LEDGER = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '2s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
)
STORAGE = (*LEDGER, "SET LOCAL TIME ZONE 'UTC'")
EXECUTION = (*LEDGER, 'SET LOCAL search_path = pg_catalog')
COMPACT = (
    "SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='2s'",
    "SET LOCAL idle_in_transaction_session_timeout='10s'", 'SET LOCAL search_path=pg_catalog',
)


@pytest.fixture(scope='module')
def connect():
    if os.environ.get('PROJECT_STORE_POSTGRES_INTEGRATION') != '1':
        pytest.fail('selected transaction setting proof requires disposable PostgreSQL')
    dsn = os.environ.get('PROJECT_STORE_POSTGRES_TEST_DSN')
    if not dsn or any(os.environ.get(name) for name in (
            'PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD',
            'PGSERVICE', 'PGSERVICEFILE', 'PGPASSFILE')):
        pytest.fail('explicit isolated fixture DSN required')
    values = conninfo_to_dict(dsn)
    if (values.get('host') != '127.0.0.1' or values.get('port') != '15432'
            or values.get('dbname') != 'mirofish_operations_test'
            or values.get('user') != 'mirofish_fixture' or not values.get('password')
            or set(values) - {'host', 'port', 'dbname', 'user', 'password', 'connect_timeout'}):
        pytest.fail('unapproved transaction setting fixture')
    def open_connection(**kwargs):
        conn = psycopg.connect(dsn, connect_timeout=3, autocommit=True, **kwargs)
        try:
            if type(conn.info.server_version) is not int or not 170000 <= conn.info.server_version < 190000:
                pytest.fail('actual PostgreSQL17 or18 required')
        except BaseException:
            conn.close()
            raise
        return conn
    return open_connection


def observed(conn):
    # Fixed test-only reads. No schema/row mutation or provider construction.
    return conn.execute("SELECT current_setting('statement_timeout'), current_setting('lock_timeout'), "
                        "current_setting('idle_in_transaction_session_timeout'), "
                        "current_setting('TimeZone'), current_setting('search_path')").fetchone()


def expected(profile, baseline):
    return ('5s', '2s', '10s', 'UTC' if profile == STORAGE else baseline[3],
            'pg_catalog' if profile in (EXECUTION, COMPACT) else baseline[4])


@pytest.mark.parametrize('profile', (LEDGER, STORAGE, EXECUTION, COMPACT))
def test_actual_batch_matches_serial_and_restores_after_commit_and_rollback(connect, profile):
    with connect() as conn:
        baseline = observed(conn)
        assert _can_batch(conn)
        with conn.transaction():
            for statement in profile:
                conn.execute(statement)
            serial = observed(conn)
        assert observed(conn) == baseline
        with conn.transaction():
            apply_runtime_settings(conn, profile)
            assert observed(conn) == serial == expected(profile, baseline)
            assert conn.execute('SELECT 1').fetchone() == (1,)
        assert observed(conn) == baseline
        with pytest.raises(psycopg.errors.DivisionByZero):
            with conn.transaction():
                apply_runtime_settings(conn, profile)
                assert observed(conn) == serial
                conn.execute('SELECT 1 / 0')
        assert observed(conn) == baseline


@pytest.mark.parametrize('profile', (LEDGER, STORAGE, EXECUTION, COMPACT))
def test_actual_nested_savepoint_restores_outer_local_settings(connect, profile):
    with connect() as conn:
        baseline = observed(conn)
        with conn.transaction():
            # Distinct, transaction-local test values expose savepoint restoration;
            # no session defaults or production constants are changed.
            conn.execute("SET LOCAL statement_timeout='8s'")
            conn.execute("SET LOCAL lock_timeout='4s'")
            conn.execute("SET LOCAL idle_in_transaction_session_timeout='20s'")
            conn.execute("SET LOCAL TIME ZONE 'Pacific/Honolulu'")
            conn.execute('SET LOCAL search_path=pg_catalog,public')
            outer = observed(conn)
            with pytest.raises(psycopg.errors.DivisionByZero):
                with conn.transaction():
                    apply_runtime_settings(conn, profile)
                    assert observed(conn) == expected(profile, outer)
                    conn.execute('SELECT 1 / 0')
            assert observed(conn) == outer
            with conn.transaction():
                apply_runtime_settings(conn, profile)
                assert observed(conn) == expected(profile, outer)
            # Releasing a successful savepoint retains inner changes until the
            # owning transaction ends, just like the original serial statements.
            assert observed(conn) == expected(profile, outer)
        assert observed(conn) == baseline


def test_actual_late_simple_query_error_is_not_ignored_or_retried(connect):
    with connect() as conn:
        baseline = observed(conn)
        with pytest.raises(psycopg.errors.UndefinedObject):
            with conn.transaction():
                # Test-only invalid later SET proves the real multi-result error
                # path. The production helper accepts only its fixed profiles.
                conn.execute("SET LOCAL statement_timeout='5s'; "
                             "SET LOCAL nexaweave_missing_setting='invalid'; "
                             "SET LOCAL lock_timeout='2s'", binary=False, prepare=False)
                pytest.fail('body admitted after a failed later statement')
        assert observed(conn) == baseline


@pytest.mark.parametrize('batched', (False, True))
def test_actual_original_statement_timeout_aborts_transaction(connect, batched):
    with connect() as conn:
        baseline = observed(conn)
        started = time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            with conn.transaction():
                if batched:
                    apply_runtime_settings(conn, EXECUTION)
                else:
                    for statement in EXECUTION:
                        conn.execute(statement)
                conn.execute('SELECT pg_sleep(6)')
        assert 4.5 <= time.monotonic() - started < 10
        assert observed(conn) == baseline


@pytest.mark.parametrize('batched', (False, True))
def test_actual_original_lock_timeout_refuses_contention_and_restores(connect, batched):
    # Random advisory lock is scoped to these two transactions; no row writes.
    key = uuid4().int & ((1 << 63) - 1)
    with connect() as holder, connect() as contender:
        baseline = observed(contender)
        with holder.transaction():
            holder.execute('SELECT pg_advisory_xact_lock(%s)', (key,))
            started = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with contender.transaction():
                    if batched:
                        apply_runtime_settings(contender, EXECUTION)
                    else:
                        for statement in EXECUTION:
                            contender.execute(statement)
                    contender.execute('SELECT pg_advisory_xact_lock(%s)', (key,))
            assert 1.5 <= time.monotonic() - started < 5
            assert observed(contender) == baseline
        # The released original lock admits a later fresh transaction normally.
        with contender.transaction():
            if batched:
                apply_runtime_settings(contender, EXECUTION)
            else:
                for statement in EXECUTION:
                    contender.execute(statement)
            assert contender.execute('SELECT pg_try_advisory_xact_lock(%s)', (key,)).fetchone() == (True,)
        assert observed(contender) == baseline


def test_actual_pipeline_uses_serial_compatible_statements(connect):
    with connect() as conn:
        baseline = observed(conn)
        with conn.transaction():
            with conn.pipeline():
                assert not _can_batch(conn)
                apply_runtime_settings(conn, STORAGE)
                assert observed(conn) == expected(STORAGE, baseline)
        assert observed(conn) == baseline


def test_actual_binary_cursor_factory_keeps_serial_protocol(connect):
    def binary_cursor(connection, *, row_factory=None):
        cursor = psycopg.Cursor(connection, row_factory=row_factory)
        cursor.format = psycopg.pq.Format.BINARY
        return cursor
    with connect(cursor_factory=binary_cursor) as conn:
        baseline = observed(conn)
        cursor = conn.execute('SELECT 1')
        assert cursor.format == psycopg.pq.Format.BINARY
        assert cursor.fetchone() == (1,)
        assert not _can_batch(conn)
        with conn.transaction():
            apply_runtime_settings(conn, EXECUTION)
            assert observed(conn) == expected(EXECUTION, baseline)
        assert observed(conn) == baseline
