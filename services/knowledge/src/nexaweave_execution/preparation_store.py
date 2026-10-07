"""PostgreSQL frozen plans and one-shot attempt fences; explicit own migration."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
import hashlib
import json
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from .preparation_contracts import PreparationAuthorityError, PreparationDispatch, digest, identifier, sha


@contextmanager
def transaction(factory):
    try:
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout='5s'")
                conn.execute("SET LOCAL lock_timeout='2s'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout='10s'")
                conn.execute("SET LOCAL search_path=pg_catalog")
                yield conn
    except psycopg.Error:
        raise PreparationAuthorityError('preparation_unavailable') from None


def _catalog(conn):
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_preparation' AND c.relkind='r' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_preparation' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_preparation' AND c.relkind='r' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_preparation' AND t.relkind='r' ORDER BY t.relname,i.relname")
    return hashlib.sha256(json.dumps([conn.execute(q).fetchall() for q in queries], default=str, separators=(',', ':')).encode()).hexdigest()


def migrate(connection):
    try:
        sql = files('nexaweave_execution').joinpath('migrations/preparations_0001.sql').read_text('utf-8')
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout='10s'")
            connection.execute("SET LOCAL lock_timeout='5s'")
            connection.execute("SET LOCAL search_path=pg_catalog")
            connection.execute('SELECT pg_advisory_xact_lock(720105, 7)')
            if connection.execute("SELECT to_regnamespace('mf_preparation') IS NOT NULL").fetchone()[0]:
                if not connection.execute("SELECT to_regclass('mf_preparation.schema_migrations') IS NOT NULL").fetchone()[0]:
                    raise PreparationAuthorityError('preparation_unavailable')
                rows = connection.execute('SELECT version,checksum,schema_checksum FROM mf_preparation.schema_migrations ORDER BY version').fetchall()
                if rows != [(1, checksum, _catalog(connection))]:
                    raise PreparationAuthorityError('preparation_unavailable')
            else:
                connection.execute(sql)
                connection.execute('INSERT INTO mf_preparation.schema_migrations VALUES(1,%s,%s)', (checksum, _catalog(connection)))
    except (OSError, psycopg.Error):
        raise PreparationAuthorityError('preparation_unavailable') from None


@dataclass(frozen=True)
class PreparationRecord:
    operation_id: object
    principal: str
    project_id: object
    project_revision: int
    display_graph_id: str
    request_sha256: str
    plan_sha256: str
    frozen: dict
    state: str
    stage: str
    completed: int
    attempt_id: object
    budget_attempt_id: object
    model_calls_started: bool
    receipt: dict | None
    error_code: str | None

    @property
    def dispatch(self):
        if self.attempt_id is None:
            raise PreparationAuthorityError('conflict')
        return PreparationDispatch(self.operation_id, self.attempt_id, self.plan_sha256)


_COLUMNS = ','.join(PreparationRecord.__dataclass_fields__)


def _record(row):
    if row is None:
        raise PreparationAuthorityError('not_found')
    result = PreparationRecord(*row)
    try:
        public = result.frozen['public']
        if (digest(result.frozen['plan_identity']) != result.plan_sha256
                or digest(result.frozen['graph']) != public['projection_sha256']
                or result.plan_sha256 != public['plan_sha256']
                or str(result.operation_id) != public['operation_id']
                or str(result.project_id) != public['scope']['project_id']
                or result.project_revision != public['project_revision']
                or result.display_graph_id != public['display_graph_id']
                or result.frozen['plan_identity'] != {k: v for k, v in public.items() if k != 'plan_sha256'}
                or result.request_sha256 != digest(result.frozen['request'])
                or hashlib.sha256(result.frozen['source_text'].encode('utf-8')).hexdigest() != public['source']['source_sha256']):
            raise ValueError
        sha(result.plan_sha256); identifier(result.operation_id)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise PreparationAuthorityError('preparation_uncertain') from None
    return result


class PreparationStore:
    def __init__(self, connection_factory):
        if not callable(connection_factory):
            raise PreparationAuthorityError('invalid_request')
        self._connect = connection_factory

    @staticmethod
    def _get(conn, principal, operation_id, lock=False):
        return _record(conn.execute(f'SELECT {_COLUMNS} FROM mf_preparation.plans WHERE principal=%s AND operation_id=%s' + (' FOR UPDATE' if lock else ''), (principal, identifier(operation_id))).fetchone())

    def get(self, principal, operation_id, plan_sha256=None):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, operation_id)
            if plan_sha256 is not None and row.plan_sha256 != sha(plan_sha256):
                raise PreparationAuthorityError('conflict')
            return row

    def put(self, principal, frozen):
        # Detach caller memory and require finite bounded JSON before persistence.
        raw = json.dumps(frozen, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        if len(raw) > 16 * 1024 * 1024:
            raise PreparationAuthorityError('result_too_large')
        frozen = json.loads(raw)
        public = frozen['public']
        operation = identifier(public['operation_id'])
        project = identifier(public['scope']['project_id'])
        request_hash = digest(frozen['request'])
        with transaction(self._connect) as conn:
            conn.execute('INSERT INTO mf_preparation.plans(operation_id,principal,project_id,project_revision,display_graph_id,request_sha256,plan_sha256,frozen,state,stage,completed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,\'planned\',\'planned\',0) ON CONFLICT DO NOTHING',
                         (operation, principal, project, public['project_revision'], public['display_graph_id'], request_hash, public['plan_sha256'], Jsonb(frozen)))
            row = self._get(conn, principal, operation)
            if row.request_sha256 != request_hash:
                raise PreparationAuthorityError('conflict')
            return row

    def queue(self, principal, operation_id, plan_sha256, budget_attempt_id):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, operation_id, True)
            if row.plan_sha256 != sha(plan_sha256):
                raise PreparationAuthorityError('conflict')
            if row.state != 'planned':
                return row, False
            # Lock actual authority at dispatch admission, preventing a revision
            # update racing the host's earlier source/binding reauthorization.
            project = conn.execute('SELECT current_revision FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE', (principal, row.project_id)).fetchone()
            if project is None or project[0] != row.project_revision:
                raise PreparationAuthorityError('conflict')
            attempt = uuid4()
            conn.execute("UPDATE mf_preparation.plans SET state='queued',stage='queued',attempt_id=%s,budget_attempt_id=%s,updated_at=now() WHERE operation_id=%s", (attempt, identifier(budget_attempt_id), row.operation_id))
            return self._get(conn, principal, row.operation_id), True

    def claim(self, principal, dispatch):
        dispatch = PreparationDispatch.from_wire(dispatch)
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, dispatch.operation_id, True)
            if row.plan_sha256 != dispatch.plan_sha256 or row.attempt_id != dispatch.attempt_id or row.state != 'queued':
                raise PreparationAuthorityError('conflict')
            conn.execute("UPDATE mf_preparation.plans SET state='preparing',stage='reading',updated_at=now() WHERE operation_id=%s", (row.operation_id,))
            return self._get(conn, principal, row.operation_id)

    def update(self, principal, dispatch, *, state='preparing', stage='reading', completed=0,
               calls_started=False, receipt=None, error_code=None):
        dispatch = PreparationDispatch.from_wire(dispatch)
        if (state not in {'preparing', 'ready', 'failed', 'cancelled', 'uncertain'}
                or type(completed) is not int or not 0 <= completed <= 100
                or stage not in {'reading', 'generating_profiles', 'generating_config', 'publishing', 'ready', 'failed', 'cancelled', 'uncertain'}
                or (state == 'ready') != (receipt is not None)):
            raise PreparationAuthorityError('invalid_request')
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, dispatch.operation_id, True)
            if row.state != 'preparing' or row.attempt_id != dispatch.attempt_id or row.plan_sha256 != dispatch.plan_sha256:
                raise PreparationAuthorityError('conflict')
            if state == 'ready' and (not row.model_calls_started or completed != 100 or stage != 'ready' or error_code is not None):
                raise PreparationAuthorityError('conflict')
            conn.execute('UPDATE mf_preparation.plans SET state=%s,stage=%s,completed=%s,model_calls_started=%s,receipt=%s,error_code=%s,updated_at=now() WHERE operation_id=%s',
                         (state, stage, completed, row.model_calls_started or calls_started, Jsonb(receipt) if receipt else None, error_code, row.operation_id))
            return self._get(conn, principal, row.operation_id)
