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
from nexaweave_storage.transaction_settings import apply_runtime_settings
from .preparation_contracts import (PreparationAuthorityError, PreparationDispatch, PreparedBudgetReceipt,
    digest, identifier, sha, validate_preparation_sources)


@contextmanager
def transaction(factory):
    try:
        with factory() as conn:
            with conn.transaction():
                apply_runtime_settings(conn, (
                    "SET LOCAL statement_timeout='5s'",
                    "SET LOCAL lock_timeout='2s'",
                    "SET LOCAL idle_in_transaction_session_timeout='10s'",
                    "SET LOCAL search_path=pg_catalog",
                ))
                yield conn
    except psycopg.Error:
        raise PreparationAuthorityError('preparation_unavailable') from None


def _catalog(conn, *, legacy=False):
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_preparation' AND c.relkind='r' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_preparation' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_preparation' AND c.relkind='r' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_preparation' AND t.relkind='r' ORDER BY t.relname,i.relname")
    catalog = [conn.execute(q).fetchall() for q in queries]
    if legacy:
        catalog = [[row for row in records if row[0] != 'seed_pairs'] for records in catalog]
    return hashlib.sha256(json.dumps(catalog, default=str, separators=(',', ':')).encode()).hexdigest()


def migrate(connection, *, seed_ensembles=False):
    """Verify the preparation schema and optionally install ensemble authority."""
    try:
        migrations = files('nexaweave_execution').joinpath('migrations')
        original = migrations.joinpath('preparations_0001.sql').read_text('utf-8')
        additive = migrations.joinpath('preparations_0002.sql').read_text('utf-8')
        checksum = hashlib.sha256(original.encode()).hexdigest()
        additive_checksum = hashlib.sha256(additive.encode()).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout='10s'")
            connection.execute("SET LOCAL lock_timeout='5s'")
            connection.execute("SET LOCAL search_path=pg_catalog")
            connection.execute('SELECT pg_advisory_xact_lock(720105, 7)')
            if not connection.execute("SELECT to_regnamespace('mf_preparation') IS NOT NULL").fetchone()[0]:
                connection.execute(original)
                connection.execute('INSERT INTO mf_preparation.schema_migrations VALUES(1,%s,%s)', (checksum, _catalog(connection)))
            if not connection.execute("SELECT to_regclass('mf_preparation.schema_migrations') IS NOT NULL").fetchone()[0]:
                raise PreparationAuthorityError('preparation_unavailable')
            rows = connection.execute('SELECT version,checksum,schema_checksum FROM mf_preparation.schema_migrations ORDER BY version').fetchall()
            catalog = _catalog(connection)
            if len(rows) == 3 or seed_ensembles:
                ensemble = migrations.joinpath('preparations_0003.sql').read_text('utf-8')
                ensemble_checksum = hashlib.sha256(ensemble.encode()).hexdigest()
            if len(rows) == 3:
                if ([row[:2] for row in rows] != [(1, checksum), (2, additive_checksum), (3, ensemble_checksum)]
                        or rows[2][2] != _ensemble_catalog(catalog, rows[:2])):
                    raise PreparationAuthorityError('preparation_unavailable')
                return
            if rows == [(1, checksum, catalog)]:
                connection.execute(additive)
                catalog = _catalog(connection)
                connection.execute('INSERT INTO mf_preparation.schema_migrations VALUES(2,%s,%s)',
                                   (additive_checksum, catalog))
                rows.append((2, additive_checksum, catalog))
            elif rows != [(1, checksum, _catalog(connection, legacy=True)), (2, additive_checksum, catalog)]:
                raise PreparationAuthorityError('preparation_unavailable')
            if seed_ensembles:
                connection.execute(ensemble)
                connection.execute('INSERT INTO mf_preparation.schema_migrations VALUES(3,%s,%s)',
                                   (ensemble_checksum, _ensemble_catalog(_catalog(connection), rows)))
    except (OSError, psycopg.Error):
        raise PreparationAuthorityError('preparation_unavailable') from None


def _ensemble_catalog(catalog, prior):
    return digest({'catalog_sha256': catalog, 'prior_migrations': [list(row) for row in prior]})


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
                or result.request_sha256 != digest(result.frozen['request'])):
            raise ValueError
        if (public['schema_version'] == 2 or 'sources' in result.frozen
                or type(result.frozen['request']) is dict and result.frozen['request'].get('schema_version') == 2):
            validate_preparation_sources(result.frozen)
        elif hashlib.sha256(result.frozen['source_text'].encode('utf-8')).hexdigest() != public['source']['source_sha256']:
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
            self._lock_operation(conn, operation)
            if conn.execute('SELECT 1 FROM mf_preparation.seed_pairs WHERE alternate_operation_id=%s',
                            (operation,)).fetchone() is not None:
                raise PreparationAuthorityError('conflict')
            conn.execute('INSERT INTO mf_preparation.plans(operation_id,principal,project_id,project_revision,display_graph_id,request_sha256,plan_sha256,frozen,state,stage,completed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,\'planned\',\'planned\',0) ON CONFLICT DO NOTHING',
                         (operation, principal, project, public['project_revision'], public['display_graph_id'], request_hash, public['plan_sha256'], Jsonb(frozen)))
            row = self._get(conn, principal, operation)
            if row.request_sha256 != request_hash:
                raise PreparationAuthorityError('conflict')
            return row

    @staticmethod
    def _lock_operation(conn, operation):
        # Both ordinary Plan and alternate publication claim the same operation namespace.
        conn.execute("SELECT pg_advisory_xact_lock(720105, hashtext(%s))", (str(operation),))

    def issue_seed_pair(self, donor, account_id, scope, payload, alternate_frozen, publish):
        """Validate donor authority and commit one immutable relation and receipt."""
        operation = identifier(alternate_frozen['public']['operation_id'])
        key = identifier(payload['idempotency_key'])
        request_hash = digest(payload)
        raw = json.dumps(alternate_frozen, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        if len(raw) > 16 * 1024 * 1024:
            raise PreparationAuthorityError('result_too_large')
        alternate_frozen = json.loads(raw)
        with transaction(self._connect) as conn:
            # Serialize key conflicts across donors before obtaining either row.
            conn.execute("SELECT pg_advisory_xact_lock(720106, hashtext(%s))", (str(key),))
            self._lock_operation(conn, operation)
            self._lock_settled_seed_donor(conn, donor, account_id, scope)
            saved = conn.execute(
                'SELECT principal,idempotency_key,donor_operation_id,alternate_operation_id,request_sha256,alternate_frozen,receipt '
                'FROM mf_preparation.seed_pairs WHERE (principal=%s AND idempotency_key=%s) OR donor_operation_id=%s',
                (donor.principal, key, donor.operation_id)).fetchall()
            identity = (donor.principal, key, donor.operation_id, operation, request_hash, alternate_frozen)
            if saved:
                if len(saved) != 1 or saved[0][:-1] != identity:
                    raise PreparationAuthorityError('conflict')
                receipt = publish(replay=True)
                if receipt != saved[0][-1]:
                    raise PreparationAuthorityError('preparation_uncertain')
                return receipt
            if conn.execute('SELECT 1 FROM mf_preparation.plans WHERE operation_id=%s', (operation,)).fetchone():
                raise PreparationAuthorityError('conflict')
            receipt = publish(replay=False)
            conn.execute(
                'INSERT INTO mf_preparation.seed_pairs(principal,idempotency_key,donor_operation_id,alternate_operation_id,'
                'request_sha256,alternate_frozen,receipt) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (*identity[:-1], Jsonb(alternate_frozen), Jsonb(receipt)))
            return receipt

    def _lock_settled_seed_donor(self, conn, donor, account_id, scope):
        from .budget import BudgetLedger, ReservationState

        if self._get(conn, donor.principal, donor.operation_id, True) != donor:
            raise PreparationAuthorityError('conflict')
        project = conn.execute(
            'SELECT current_revision FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE',
            (donor.principal, donor.project_id)).fetchone()
        if project is None or project[0] != donor.project_revision:
            raise PreparationAuthorityError('conflict')
        account = BudgetLedger._account(conn, donor.principal, account_id, lock=True)
        prior = BudgetLedger._row(conn, account_id, donor.operation_id)
        fingerprint = hashlib.sha256(('prepared_budget_v1:' + donor.plan_sha256).encode('ascii')).hexdigest()
        expected = PreparedBudgetReceipt(donor.operation_id, donor.budget_attempt_id,
                                         fingerprint, donor.receipt['artifact_sha256'])
        if (account[2] != donor.project_id or prior is None or prior.state != ReservationState.settled
                or prior.attempt_id != donor.budget_attempt_id or prior.fingerprint != fingerprint
                or prior.scope_group_id != scope.group_id or prior.episode_id != scope.episode_uuid(donor.operation_id)
                or prior.evidence_ids or prior.receipt != expected):
            raise PreparationAuthorityError('conflict')

    def issue_seed_ensemble(self, donor, account_id, scope, payload, ensemble_id, members, publish):
        key = identifier(payload['idempotency_key'])
        identity = (ensemble_id, donor.principal, key, identifier(account_id), donor.operation_id, digest(payload))
        expected_members = [
            (ordinal, variant.operation_id, launch_id, variant.frozen['public']['options']['seed'])
            for ordinal, (variant, launch_id) in enumerate(members, 1)
        ]
        operations = tuple(member[1] for member in expected_members)
        if len(operations) != 2 or len(set(operations)) != 2 or donor.operation_id in operations:
            raise PreparationAuthorityError('conflict')
        if any(len(json.dumps(variant.frozen, ensure_ascii=True, allow_nan=False,
                              separators=(',', ':'))) > 16 * 1024 * 1024 for variant, _ in members):
            raise PreparationAuthorityError('result_too_large')
        with transaction(self._connect) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(720106, hashtext(%s))", (str(key),))
            for operation in sorted(operations):
                self._lock_operation(conn, operation)
            self._lock_settled_seed_donor(conn, donor, account_id, scope)
            saved = conn.execute(
                'SELECT ensemble_id,principal,idempotency_key,account_id,donor_operation_id,request_sha256,receipt '
                'FROM mf_preparation.seed_ensembles WHERE (principal=%s AND idempotency_key=%s) '
                'OR donor_operation_id=%s',
                (donor.principal, key, donor.operation_id)).fetchall()
            if saved:
                if len(saved) != 1 or saved[0][:-1] != identity:
                    raise PreparationAuthorityError('conflict')
                actual_members = conn.execute(
                    'SELECT ordinal,operation_id,launch_id,seed FROM mf_preparation.seed_ensemble_members '
                    'WHERE ensemble_id=%s ORDER BY ordinal', (ensemble_id,)).fetchall()
                if actual_members != expected_members:
                    raise PreparationAuthorityError('preparation_uncertain')
                result = publish(replay=True)
                if result != saved[0][-1]:
                    raise PreparationAuthorityError('preparation_uncertain')
                for (variant, _), member in zip(members, result['members'], strict=True):
                    if self._get(conn, donor.principal, variant.operation_id) != replace(variant, receipt=member['receipt']):
                        raise PreparationAuthorityError('preparation_uncertain')
                return result
            if conn.execute(
                'SELECT operation_id FROM mf_preparation.plans WHERE operation_id=ANY(%s) '
                'UNION ALL SELECT alternate_operation_id FROM mf_preparation.seed_pairs '
                'WHERE alternate_operation_id=ANY(%s)', (list(operations), list(operations)),
            ).fetchone():
                raise PreparationAuthorityError('conflict')
            result = publish(replay=False)
            conn.execute(
                'INSERT INTO mf_preparation.seed_ensembles('
                'ensemble_id,principal,idempotency_key,account_id,donor_operation_id,request_sha256,receipt) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s)', (*identity, Jsonb(result)))
            for (variant, _), member, metadata in zip(members, result['members'], expected_members, strict=True):
                conn.execute(
                    'INSERT INTO mf_preparation.plans(operation_id,principal,project_id,project_revision,'
                    'display_graph_id,request_sha256,plan_sha256,frozen,state,stage,completed,receipt,seed_ensemble_id) '
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'ready','ready',100,%s,%s)",
                    (variant.operation_id, variant.principal, variant.project_id,
                     variant.project_revision, variant.display_graph_id, variant.request_sha256,
                     variant.plan_sha256, Jsonb(variant.frozen), Jsonb(member['receipt']), ensemble_id))
                conn.execute(
                    'INSERT INTO mf_preparation.seed_ensemble_members(ensemble_id,ordinal,operation_id,launch_id,seed) '
                    'VALUES(%s,%s,%s,%s,%s)', (ensemble_id, *metadata))
            return result

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
