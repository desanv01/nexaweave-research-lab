"""Batch only reviewed runtime SET LOCAL profiles on ordinary text connections."""
from __future__ import annotations

import psycopg
from psycopg import pq
from psycopg.rows import tuple_row

_Connection = psycopg.Connection
_Cursor = psycopg.Cursor
_execute = _Connection.execute
_transaction = _Connection.transaction
_cursor = _Connection.cursor
_cursor_execute = _Cursor.execute
_cursor_init = _Cursor.__init__

_SPACED = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '2s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
)
_COMPACT = (
    "SET LOCAL statement_timeout='5s'",
    "SET LOCAL lock_timeout='2s'",
    "SET LOCAL idle_in_transaction_session_timeout='10s'",
)
_PROFILES = (
    _SPACED,
    (*_SPACED, "SET LOCAL TIME ZONE 'UTC'"),
    (*_SPACED, 'SET LOCAL search_path = pg_catalog'),
    (*_COMPACT, 'SET LOCAL search_path=pg_catalog'),
)


def _can_batch(conn):
    if type(conn) is not _Connection:
        return False
    # Snapshots and bound-method checks reject class and instance overrides.
    if (_Connection.execute is not _execute
            or _Connection.transaction is not _transaction
            or _Connection.cursor is not _cursor
            or _Cursor.execute is not _cursor_execute
            or _Cursor.__init__ is not _cursor_init):
        return False
    try:
        if (getattr(conn.execute, '__func__', None) is not _execute
                or getattr(conn.transaction, '__func__', None) is not _transaction
                or getattr(conn.cursor, '__func__', None) is not _cursor
                or conn.cursor_factory is not _Cursor
                or conn.row_factory is not tuple_row):
            return False
        pipeline = conn.pgconn.pipeline_status
        version = conn.info.server_version
    except (AttributeError, TypeError):
        # Unknown metadata has no fast-path authority. No SQL was sent.
        return False
    return (type(pipeline) in (int, pq.PipelineStatus)
            and pipeline == pq.PipelineStatus.OFF
            and type(version) is int and 170000 <= version < 190000)


def apply_runtime_settings(conn, fixed_statement_tuple):
    """Apply the original statements inside the caller's existing transaction.

    No factory/transaction ownership, capability queries, cache or SQL retry.
    Custom connection protocols receive their unchanged serial execute calls.
    """
    if (type(fixed_statement_tuple) is not tuple
            or any(type(statement) is not str for statement in fixed_statement_tuple)
            or fixed_statement_tuple not in _PROFILES):
        raise ValueError('unrecognized runtime setting profile')
    if _can_batch(conn):
        conn.execute('; '.join(fixed_statement_tuple), binary=False, prepare=False)
    else:
        for statement in fixed_statement_tuple:
            conn.execute(statement)
