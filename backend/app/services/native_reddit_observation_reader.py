"""Read a restored Reddit trace from receipt-bound SQLite bytes only."""

from contextlib import closing, contextmanager
import json
import sqlite3

from .native_observation_reader import _unique, read_owned_blob
from .native_observations_client import (
    NativeObservationsError, REDDIT_TRACE_BYTES, REDDIT_TRACE_FILE, REDDIT_TRACE_RECORDS,
    validate_reddit_trace_record,
)

_READS = {
    'sqlite_master': {'type', 'name', 'rootpage'},
    'sqlite_schema': {'type', 'name', 'rootpage'},
    'trace': {'rowid', 'user_id', 'action', 'info', 'created_at'},
}


def _authorize(action, name, column, database, source):
    if source is None and (action == sqlite3.SQLITE_SELECT
            or (action == sqlite3.SQLITE_READ and database == 'main' and column is not None
                and column.lower() in _READS.get(name, ()))):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _trace_page(blob, offset, limit, seed):
    try:
        # Only the rollback-format main file is admitted. SQLite never receives
        # its path or an external WAL, journal, or auxiliary database.
        if (type(blob) is not bytes or not 512 <= len(blob) <= REDDIT_TRACE_BYTES
                or blob[:16] != b'SQLite format 3\0' or blob[18:20] != b'\x01\x01'):
            raise ValueError
        with closing(sqlite3.connect(':memory:')) as db:
            if not hasattr(db, 'deserialize'):
                raise NativeObservationsError('observations_unavailable')
            db.execute('PRAGMA temp_store=MEMORY')
            db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
            db.deserialize(blob)
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA query_only=ON')
            db.set_authorizer(_authorize)
            steps = 200

            def consume_steps():
                nonlocal steps
                steps -= 1
                return steps <= 0

            db.set_progress_handler(consume_steps, 1000)
            kind = db.execute(
                "SELECT type,rootpage FROM main.sqlite_schema WHERE name='trace' LIMIT 2",
            ).fetchall()
            if len(kind) != 1 or not (kind[0][0] == 'table' and type(kind[0][1]) is int and kind[0][1] > 0):
                raise ValueError
            rows = db.execute(
                "SELECT rowid,user_id,action,created_at,CASE WHEN action='sign_up' THEN NULL ELSE info END "
                "FROM main.trace ORDER BY rowid LIMIT ?", (REDDIT_TRACE_RECORDS + 1,),
            ).fetchall()
        total = len(rows)
        if not 4 <= total <= REDDIT_TRACE_RECORDS:
            raise ValueError
        records = []
        for index, (trace_id, actor_id, action, simulated_at, info) in enumerate(rows):
            if action == 'sign_up':
                data = {}
            else:
                if type(info) is not str or not 1 <= len(info.encode('utf-8')) <= 4096:
                    raise ValueError
                data = json.loads(info, object_pairs_hook=_unique)
            record = dict(index=index, trace_id=trace_id, actor_id=actor_id, action=action,
                          simulated_at=simulated_at, data=data)
            validate_reddit_trace_record(record, index, total, seed)
            records.append(record)
        if not 0 <= offset <= total:
            raise NativeObservationsError('invalid_request')
        end = min(total, offset + limit)
        return dict(records=records[offset:end], total_records=total,
                    next_offset=end if end < total else None)
    except NativeObservationsError:
        raise
    except (sqlite3.Error, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise NativeObservationsError('evidence_invalid') from None


class NativeRedditObservationReader:
    @contextmanager
    def page(self, root, offset, limit, seed, evidence_sha256):
        with read_owned_blob(root, REDDIT_TRACE_FILE, REDDIT_TRACE_BYTES, evidence_sha256) as blob:
            yield dict(_trace_page(blob, offset, limit, seed), platform='reddit', offset=offset, limit=limit,
                       evidence={'kind': 'reddit_sqlite_trace_v1',
                                 'file': {'name': REDDIT_TRACE_FILE, 'sha256': evidence_sha256, 'size': len(blob)}})
