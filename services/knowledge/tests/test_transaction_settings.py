"""Runtime setting dispatch, unsupported protocols and original wrapper lifecycle."""
import importlib
from functools import partial
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import pq
from psycopg.rows import tuple_row

from nexaweave_storage import transaction_settings as settings

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
WRAPPERS = (
    ('nexaweave_storage.store', '_transaction', STORAGE),
    ('nexaweave_storage.source', '_transaction', STORAGE),
    ('nexaweave_knowledge.operations', '_transaction', LEDGER),
    ('nexaweave_execution.budget', '_transaction', EXECUTION),
    ('nexaweave_execution.native_run_store', '_transaction', EXECUTION),
    ('nexaweave_execution.preparation_store', 'transaction', COMPACT),
    ('nexaweave_execution.native_launch_store', 'transaction', COMPACT),
    ('nexaweave_execution.report_store', 'transaction', COMPACT),
    ('nexaweave_execution.followup_store', 'transaction', COMPACT),
)


class RecordingConnection:
    def __init__(self, failure=None, fail_at=0):
        self.calls = []
        self.events = []
        self.failure = failure
        self.fail_at = fail_at

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.failure is not None and len(self.calls) == self.fail_at:
            raise self.failure

    def __enter__(self):
        self.events.append('connection-enter')
        return self

    def __exit__(self, kind, value, traceback):
        self.events.append(('connection-exit', value))

    def transaction(self):
        owner = self
        class Transaction:
            def __enter__(self):
                owner.events.append('transaction-enter')
            def __exit__(self, kind, value, traceback):
                owner.events.append(('transaction-exit', value))
        return Transaction()


@pytest.fixture
def standard_model(monkeypatch):
    # Unit-only eligibility model; real psycopg Connection behavior is qualified
    # separately against explicitly admitted PostgreSQL17/18, never claimed here.
    class Cursor:
        def __init__(self):
            pass
        def execute(self):
            pass
    class Connection(RecordingConnection):
        cursor_factory = Cursor
        row_factory = staticmethod(tuple_row)
        def __init__(self):
            super().__init__()
            self.pgconn = SimpleNamespace(pipeline_status=pq.PipelineStatus.OFF)
            self.info = SimpleNamespace(server_version=180000)
        def cursor(self):
            return None
    monkeypatch.setattr(settings, '_Connection', Connection)
    monkeypatch.setattr(settings, '_Cursor', Cursor)
    for name, value in (('_execute', Connection.execute), ('_transaction', Connection.transaction),
                        ('_cursor', Connection.cursor), ('_cursor_execute', Cursor.execute),
                        ('_cursor_init', Cursor.__init__)):
        monkeypatch.setattr(settings, name, value)
    return Connection


@pytest.mark.parametrize('profile', (LEDGER, STORAGE, EXECUTION, COMPACT))
def test_eligible_model_sends_one_parameterless_text_unprepared_query(standard_model, profile):
    conn = standard_model()
    settings.apply_runtime_settings(conn, profile)
    assert conn.calls == [(('; '.join(profile),), {'binary': False, 'prepare': False})]


@pytest.mark.parametrize('change', ('subclass', 'execute', 'transaction', 'cursor', 'cursor-factory',
                                  'binary-factory', 'rows', 'pipeline-on', 'pipeline-unknown',
                                  'pipeline-bool', 'pg16', 'pg19', 'version-bool', 'missing-info'))
def test_unsupported_model_preserves_original_serial_protocol(standard_model, change):
    conn = standard_model()
    if change == 'subclass':
        class Subclass(standard_model):
            pass
        conn = Subclass()
    elif change in ('execute', 'transaction', 'cursor'):
        if change == 'execute':
            conn.execute = lambda *args, **kwargs: conn.calls.append((args, kwargs))
        else:
            setattr(conn, change, lambda: None)
    elif change == 'cursor-factory':
        conn.cursor_factory = lambda *args, **kwargs: None
    elif change == 'binary-factory':
        conn.cursor_factory = partial(settings._Cursor, binary=True)
    elif change == 'rows':
        conn.row_factory = lambda cursor: None
    elif change.startswith('pipeline'):
        conn.pgconn.pipeline_status = {'pipeline-on': pq.PipelineStatus.ON,
                                      'pipeline-unknown': None, 'pipeline-bool': False}[change]
    elif change == 'missing-info':
        conn.info = SimpleNamespace()
    else:
        conn.info.server_version = {'pg16': 160000, 'pg19': 190000, 'version-bool': True}[change]
    settings.apply_runtime_settings(conn, STORAGE)
    assert conn.calls == [((statement,), {}) for statement in STORAGE]


@pytest.mark.parametrize('method', ('execute', 'transaction', 'cursor'))
def test_class_override_is_not_canonical(standard_model, monkeypatch, method):
    conn = standard_model()
    if method == 'execute':
        replacement = lambda self, *args, **kwargs: self.calls.append((args, kwargs))
    else:
        replacement = lambda self: None
    monkeypatch.setattr(standard_model, method, replacement)
    settings.apply_runtime_settings(conn, LEDGER)
    assert conn.calls == [((statement,), {}) for statement in LEDGER]


@pytest.mark.parametrize('method', ('execute', '__init__'))
def test_cursor_class_override_is_not_canonical(standard_model, monkeypatch, method):
    conn = standard_model()
    monkeypatch.setattr(settings._Cursor, method, lambda *args, **kwargs: None)
    settings.apply_runtime_settings(conn, STORAGE)
    assert conn.calls == [((statement,), {}) for statement in STORAGE]


@pytest.mark.parametrize('control_type', (KeyboardInterrupt, SystemExit))
def test_capability_control_propagates_before_any_sql(standard_model, control_type):
    conn = standard_model()
    error = control_type(23)
    class ControlledInfo:
        @property
        def server_version(self):
            raise error
    conn.info = ControlledInfo()
    with pytest.raises(control_type) as raised:
        settings.apply_runtime_settings(conn, STORAGE)
    assert raised.value is error and conn.calls == []


def test_same_connection_reapplies_settings_without_cache(standard_model):
    conn = standard_model()
    settings.apply_runtime_settings(conn, EXECUTION)
    settings.apply_runtime_settings(conn, EXECUTION)
    assert conn.calls == [(('; '.join(EXECUTION),), {'binary': False, 'prepare': False})] * 2


@pytest.mark.parametrize('error_type', (psycopg.Error, RuntimeError, KeyboardInterrupt, SystemExit))
def test_failed_batch_never_retries_or_replaces_original_error(standard_model, error_type):
    conn = standard_model()
    error = error_type('setting failure')
    conn.failure, conn.fail_at = error, 1
    with pytest.raises(error_type) as raised:
        settings.apply_runtime_settings(conn, EXECUTION)
    assert raised.value is error
    assert conn.calls == [(('; '.join(EXECUTION),), {'binary': False, 'prepare': False})]


@pytest.mark.parametrize('position', (1, 2, 3, 4))
def test_custom_serial_failure_stops_at_actual_failed_statement(position):
    error = psycopg.Error('serial setting failure')
    conn = RecordingConnection(error, position)
    with pytest.raises(psycopg.Error) as raised:
        settings.apply_runtime_settings(conn, STORAGE)
    assert raised.value is error
    assert conn.calls == [((statement,), {}) for statement in STORAGE[:position]]


@pytest.mark.parametrize('invalid', (list(STORAGE), (), (*LEDGER, 'SELECT 1'), ('SET statement_timeout=0',)))
def test_unrecognized_profile_refused_before_any_sql(invalid):
    conn = RecordingConnection()
    with pytest.raises(ValueError):
        settings.apply_runtime_settings(conn, invalid)
    assert conn.calls == []


@pytest.mark.parametrize('module_name,function,profile', WRAPPERS)
def test_each_original_wrapper_keeps_settings_before_body_and_closes(module_name, function, profile):
    module = importlib.import_module(module_name)
    conn = RecordingConnection()
    with getattr(module, function)(lambda: conn) as supplied:
        assert supplied is conn
        assert conn.calls == [((statement,), {}) for statement in profile]
        assert conn.events == ['connection-enter', 'transaction-enter']
        conn.events.append('body')
    assert conn.events == ['connection-enter', 'transaction-enter', 'body',
                           ('transaction-exit', None), ('connection-exit', None)]


@pytest.mark.parametrize('module_name,function,profile', WRAPPERS)
@pytest.mark.parametrize('control_type', (KeyboardInterrupt, SystemExit))
def test_each_original_wrapper_preserves_control_and_rolls_back_without_body(module_name, function, profile, control_type):
    module = importlib.import_module(module_name)
    error = control_type(23)
    conn = RecordingConnection(error, 2)
    with pytest.raises(control_type) as raised:
        with getattr(module, function)(lambda: conn):
            pytest.fail('body admitted after failed settings')
    assert raised.value is error
    assert conn.calls == [((statement,), {}) for statement in profile[:2]]
    assert conn.events == ['connection-enter', 'transaction-enter',
                           ('transaction-exit', error), ('connection-exit', error)]


@pytest.mark.parametrize('module_name,function,profile', WRAPPERS)
def test_each_original_wrapper_keeps_driver_error_mapping_and_no_body(module_name, function, profile):
    module = importlib.import_module(module_name)
    mapped_name = {
        'nexaweave_storage.store': 'StorageError', 'nexaweave_storage.source': 'StorageError',
        'nexaweave_knowledge.operations': 'StorageError', 'nexaweave_execution.budget': 'BudgetUnavailable',
        'nexaweave_execution.native_run_store': 'NativeRunUnavailable',
        'nexaweave_execution.preparation_store': 'PreparationAuthorityError',
        'nexaweave_execution.native_launch_store': 'LaunchAuthorityError',
        'nexaweave_execution.report_store': 'ReportError', 'nexaweave_execution.followup_store': 'FollowupError',
    }[module_name]
    original = psycopg.Error('private driver error not a public message')
    conn = RecordingConnection(original, 2)
    with pytest.raises(getattr(module, mapped_name)) as raised:
        with getattr(module, function)(lambda: conn):
            pytest.fail('body admitted after failed settings')
    assert raised.value is not original
    assert raised.value.__suppress_context__
    assert 'private driver error' not in str(raised.value)
    assert conn.calls == [((statement,), {}) for statement in profile[:2]]
    assert conn.events == ['connection-enter', 'transaction-enter',
                           ('transaction-exit', original), ('connection-exit', original)]


def test_source_unique_violation_still_maps_to_conflict():
    module = importlib.import_module('nexaweave_storage.source')
    original = psycopg.errors.UniqueViolation('private uniqueness failure')
    conn = RecordingConnection(original, 1)
    with pytest.raises(module.Conflict):
        with module._transaction(lambda: conn):
            pytest.fail('body admitted after failed settings')
    assert conn.events[-2:] == [('transaction-exit', original), ('connection-exit', original)]
