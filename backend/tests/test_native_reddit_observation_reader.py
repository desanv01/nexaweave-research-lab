"""Focused receipt-bound Reddit trace projection and refusal regression."""

from contextlib import closing
from hashlib import sha256
import json
import sqlite3

import pytest

from app.services.native_observations_client import NativeObservationsError
from app.services.native_reddit_observation_reader import NativeRedditObservationReader


def test_receipt_bound_reddit_trace_is_paged_redacted_and_closed(tmp_path):
    reader = NativeRedditObservationReader()
    path = tmp_path / 'native.sqlite3'
    rows = [
        (0, 'sign_up', {'name': 'private fixture username', 'bio': 'private fixture bio'}, '2026-01-01 00:00:00'),
        (1, 'sign_up', {'name': 'another private username'}, '2026-01-01 00:00:00'),
        (0, 'create_post', {'post_id': 1, 'content': 'Seed'}, '2026-01-01 00:01:00'),
        (1, 'create_comment', {'comment_id': 1, 'content': 'Reply'}, '2026-01-01 00:02:00'),
    ]
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('CREATE TABLE trace(user_id INTEGER, action TEXT, info TEXT, created_at TEXT)')
        db.executemany('INSERT INTO trace VALUES (?, ?, ?, ?)',
                       [(actor, action, json.dumps(data), at) for actor, action, data, at in rows])
        original = db.serialize()
        path.write_bytes(original)
        pin = sha256(original).hexdigest()
        with reader.page(tmp_path, 1, 2, 7, pin) as page:
            assert page['total_records'] == 4 and page['next_offset'] == 3
            assert page['records'] == [
                dict(index=1, trace_id=2, actor_id=1, action='sign_up',
                     simulated_at='2026-01-01 00:00:00', data={}),
                dict(index=2, trace_id=3, actor_id=0, action='create_post',
                     simulated_at='2026-01-01 00:01:00', data={'post_id': 1, 'content': 'Seed'}),
            ]
            assert page['evidence'] == {
                'kind': 'reddit_sqlite_trace_v1', 'file': {'name': path.name, 'sha256': pin, 'size': len(original)},
            }
        with reader.page(tmp_path, 4, 1, 7, pin) as end:
            assert end['records'] == [] and end['next_offset'] is None
        with pytest.raises(NativeObservationsError, match='^evidence_invalid$'):
            with reader.page(tmp_path, 0, 4, 7, '0' * 64):
                pytest.fail('unauthenticated database yielded evidence')
        with pytest.raises(NativeObservationsError, match='^evidence_invalid$'):
            with reader.page(tmp_path, 0, 4, 7, pin):
                (tmp_path / 'native.sqlite3-wal').write_bytes(b'fixture sidecar')
        assert path.read_bytes() == original
        (tmp_path / 'native.sqlite3-wal').unlink()

        db.execute("UPDATE trace SET info=? WHERE action='create_comment'",
                   ('{"comment_id":1,"content":"a","content":"b"}',))
        changed = db.serialize()
        path.write_bytes(changed)
        with pytest.raises(NativeObservationsError, match='^evidence_invalid$'):
            with reader.page(tmp_path, 0, 1, 7, sha256(changed).hexdigest()):
                pytest.fail('malformed off-page trace yielded evidence')
