"""Checksum/catalog-qualified PG report journal and atomic shared-account admission."""
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
import hashlib
import json
from uuid import UUID, uuid4
import psycopg
from psycopg.types.json import Jsonb
from .report_contracts import (ReportError, IDENTITY, digest, encoded, validate_identity,
    validate_payload, validate_result, validate_manifest, dispatch, budget_fingerprint,
    report_budget_episode, ReportBudgetReceipt)


@contextmanager
def transaction(factory):
    try:
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout='5s'")
                conn.execute("SET LOCAL lock_timeout='2s'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout='10s'")
                conn.execute('SET LOCAL search_path=pg_catalog')
                yield conn
    except psycopg.Error:
        raise ReportError('report_unavailable') from None


def _catalog(conn):
    # Same complete catalog categories as accepted stores, separate namespace.
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_report' AND c.relkind='r' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_report' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_report' AND c.relkind='r' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_report' AND t.relkind='r' ORDER BY t.relname,i.relname")
    return hashlib.sha256(json.dumps([conn.execute(q).fetchall() for q in queries], default=str, separators=(',', ':')).encode()).hexdigest()


def migrate(connection):
    try:
        sql = files('nexaweave_execution').joinpath('migrations/report_0001.sql').read_text('utf-8')
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout='10s'")
            connection.execute("SET LOCAL lock_timeout='5s'")
            connection.execute('SET LOCAL search_path=pg_catalog')
            connection.execute('SELECT pg_advisory_xact_lock(720105,9)')
            if connection.execute("SELECT to_regnamespace('mf_report') IS NOT NULL").fetchone()[0]:
                if not connection.execute("SELECT to_regclass('mf_report.schema_migrations') IS NOT NULL").fetchone()[0]:
                    raise ReportError('report_unavailable')
                if connection.execute('SELECT version,checksum,schema_checksum FROM mf_report.schema_migrations ORDER BY version').fetchall() != [(1, checksum, _catalog(connection))]:
                    raise ReportError('report_unavailable')
            else:
                connection.execute(sql)
                connection.execute('INSERT INTO mf_report.schema_migrations VALUES(1,%s,%s)', (checksum, _catalog(connection)))
    except (OSError, psycopg.Error):
        raise ReportError('report_unavailable') from None


def rollback(connection):
    migrate(connection)  # Refuse rollback of an unknown/drifted owned catalog.
    with connection.transaction():
        connection.execute('SELECT pg_advisory_xact_lock(720105,9)')
        if connection.execute("SELECT 1 FROM mf_report.plans WHERE state IN ('queued','generating','uncertain') LIMIT 1").fetchone():
            raise ReportError('busy')
        connection.execute(files('nexaweave_execution').joinpath('migrations/report_0001_down.sql').read_text('utf-8'))


@dataclass(frozen=True)
class ReportRecord:
    report_id: UUID
    principal: str
    plan_sha256: str
    declaration_sha256: str
    frozen: dict
    state: str
    attempt_id: UUID | None
    workflow: dict | None
    receipt: dict | None
    manifest: dict | None
    progress: dict
    cleanup: dict
    cancel_requested: bool
    dispatch_claimed: bool
    owner_claimed: bool
    first_possible_request: bool
    error_code: str | None

    def public(self, authorization=None):
        data = dict(self.frozen['identity'], plan_sha256=self.plan_sha256, state=self.state,
            authorization=authorization or dict(model_calls_enabled=False, budget_configured=False),
            progress=self.progress, workflow=self.workflow, receipt=self.receipt,
            receipt_sha256=digest(self.receipt) if self.receipt is not None else None,
            manifest=self.manifest, cleanup=self.cleanup, cancel_requested=self.cancel_requested, error_code=self.error_code)
        return validate_result(data, self.frozen['identity']['binding']['display_graph_id'],
            self.frozen['identity']['binding']['scope'], dict(schema_version=1, report_id=str(self.report_id), plan_sha256=self.plan_sha256),
            'status', self.principal)


COLUMNS = ','.join(ReportRecord.__dataclass_fields__)


def _record(raw):
    if raw is None:
        raise ReportError('not_found')
    try:
        row = ReportRecord(*raw)
        frozen = row.frozen
        if type(frozen) is not dict or set(frozen) != {'identity', 'declaration', 'context', 'configuration'}:
            raise ValueError
        identity = validate_identity(frozen['identity'])
        validate_payload('plan', frozen['declaration'])
        configuration = frozen['configuration']
        if type(configuration) is not dict or set(configuration) != {'account_id', 'factory_sha256'}:
            raise ValueError
        from .report_contracts import sha, uuid_string
        sha(configuration['factory_sha256'])
        if configuration['account_id'] is not None:
            uuid_string(configuration['account_id'])
        if (identity['report_id'] != str(row.report_id) or identity['binding']['principal'] != row.principal
                or digest(identity) != row.plan_sha256 or digest(frozen['declaration']) != row.declaration_sha256
                or digest(frozen['context']) != identity['context_sha256']
                or digest(frozen['context']['graph']) != identity['source_projection_sha256']
                or frozen['context']['binding'] != identity['binding']):
            raise ValueError
        declaration = frozen['declaration']
        if (declaration['report_id'] != str(row.report_id)
                or declaration['launch_id'] != identity['binding']['native']['run_id']
                or declaration['launch_sha256'] != identity['binding']['native']['launch_sha256']
                or {k: declaration[k] for k in ('requirement', 'output_language', 'native_windows')} != identity['options']):
            raise ValueError
        if any(type(v) is not bool for v in (row.cancel_requested, row.dispatch_claimed, row.owner_claimed, row.first_possible_request)):
            raise ValueError
        if row.dispatch_claimed != (type(row.attempt_id) is UUID) or row.owner_claimed and not row.dispatch_claimed or row.first_possible_request and not row.owner_claimed:
            raise ValueError
        if row.state in ('queued', 'generating', 'completed', 'failed', 'uncertain') and not row.dispatch_claimed:
            raise ValueError
        row.public()
        return row
    except (ValueError, TypeError, KeyError, UnicodeError, ReportError):
        raise ReportError('report_uncertain') from None


class ReportStore:
    def __init__(self, connection_factory):
        if not callable(connection_factory):
            raise ReportError('report_unavailable')
        self.connect = connection_factory

    @staticmethod
    def _get(conn, principal, report_id, lock=False):
        return _record(conn.execute(f'SELECT {COLUMNS} FROM mf_report.plans WHERE principal=%s AND report_id=%s' + (' FOR UPDATE' if lock else ''),
            (principal, UUID(str(report_id)))).fetchone())

    def get(self, principal, report_id, plan_sha256=None):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, report_id)
            if plan_sha256 is not None and row.plan_sha256 != plan_sha256:
                raise ReportError('conflict')
            return row

    def put(self, principal, declaration, identity, context, configuration):
        identity = validate_identity(identity)
        declaration = validate_payload('plan', declaration)
        frozen = json.loads(encoded(dict(identity=identity, declaration=declaration, context=context, configuration=configuration)))
        binding = identity['binding']
        if principal != binding['principal']:
            raise ReportError('unauthorized')
        with transaction(self.connect) as conn:
            self._authority(conn, binding)
            count = conn.execute('SELECT count(*) FROM mf_report.plans WHERE principal=%s', (principal,)).fetchone()[0]
            prior = conn.execute('SELECT plan_sha256 FROM mf_report.plans WHERE report_id=%s', (UUID(identity['report_id']),)).fetchone()
            if prior is None and count >= 100:
                raise ReportError('busy')
            progress = dict(stage='planned', percent=0, completed_sections=0, total_sections=0)
            cleanup = dict(known=True, pending=False, owner_thread_alive=False)
            conn.execute("INSERT INTO mf_report.plans(report_id,principal,plan_sha256,declaration_sha256,frozen,state,progress,cleanup) VALUES(%s,%s,%s,%s,%s,'planned',%s,%s) ON CONFLICT DO NOTHING",
                (UUID(identity['report_id']), principal, digest(identity), digest(declaration), Jsonb(frozen), Jsonb(progress), Jsonb(cleanup)))
            try:
                row = self._get(conn, principal, identity['report_id'])
            except ReportError:
                raise ReportError('conflict') from None
            if row.plan_sha256 != digest(identity) or row.declaration_sha256 != digest(declaration):
                raise ReportError('conflict')
            return row

    @staticmethod
    def _authority(conn, binding):
        project = conn.execute('SELECT current_revision FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE',
            (binding['principal'], UUID(binding['scope']['project_id']))).fetchone()
        prep = conn.execute('SELECT state,plan_sha256,receipt FROM mf_preparation.plans WHERE principal=%s AND operation_id=%s FOR UPDATE',
            (binding['principal'], UUID(binding['preparation']['operation_id']))).fetchone()
        native = conn.execute('SELECT state,launch_sha256,receipt FROM mf_native_launch.plans WHERE principal=%s AND run_id=%s FOR UPDATE',
            (binding['principal'], UUID(binding['native']['run_id']))).fetchone()
        if (project is None or project[0] != binding['project_revision'] or prep is None or prep[0] != 'ready'
                or prep[1] != binding['preparation']['plan_sha256'] or prep[2] is None
                or prep[2]['artifact_sha256'] != binding['preparation']['artifact_sha256']
                or native is None or native[0] != 'completed' or native[1] != binding['native']['launch_sha256']
                or native[2] is None or native[2]['evidence_sha256'] != binding['native']['evidence_sha256']):
            raise ReportError('conflict')

    def queue(self, principal, report_id, plan_sha256, scope, account_id):
        """Same account row lock/totals as BudgetLedger, one transaction with dispatch."""
        from .budget import BudgetLedger, ReservationState
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, report_id, True)
            if row.plan_sha256 != plan_sha256:
                raise ReportError('conflict')
            if row.state != 'planned' or row.cancel_requested:
                return row, False
            self._authority(conn, row.frozen['identity']['binding'])
            account = BudgetLedger._account(conn, principal, account_id, lock=True)
            if (account[2] != scope.project_id or str(account_id) != row.frozen['configuration']['account_id']
                    or scope.model_dump(mode='json') != row.frozen['identity']['binding']['scope']):
                raise ReportError('budget_denied')
            ceiling = row.frozen['identity']['ceiling_microusd']
            prior = BudgetLedger._row(conn, account_id, row.report_id)
            if prior is not None:
                raise ReportError('conflict')
            accounted, reserved, _ = BudgetLedger._totals(conn, account_id)
            if ceiling is None or ceiling > account[3] - accounted - reserved:
                raise ReportError('budget_denied')
            attempt = uuid4()
            conn.execute("INSERT INTO mf_execution.reservations(account_id,operation_id,fingerprint,ceiling_microusd,state,attempt_id,scope_group_id,episode_id,evidence_ids) VALUES(%s,%s,%s,%s,'started',%s,%s,%s,%s)",
                (account_id, row.report_id, budget_fingerprint(plan_sha256), ceiling, attempt, scope.group_id, report_budget_episode(scope.group_id, row.report_id), Jsonb([])))
            conn.execute("UPDATE mf_report.plans SET state='queued',dispatch_claimed=true,attempt_id=%s,cleanup=%s,progress=%s,owner_deadline=now()+(%s * interval '1 second'),updated_at=now() WHERE report_id=%s",
                (attempt, Jsonb(dict(known=False, pending=None, owner_thread_alive=None)),
                 Jsonb(dict(stage='queued', percent=0, completed_sections=0, total_sections=0)),
                 row.frozen['identity']['limits']['max_run_seconds'] + 30, row.report_id))
            return self._get(conn, principal, report_id), True

    def claim(self, principal, wire):
        dispatch(wire)
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['report_id'], True)
            if (row.plan_sha256 != wire['plan_sha256'] or str(row.attempt_id) != wire['attempt_id']
                    or row.state != 'queued' or row.owner_claimed):
                raise ReportError('conflict')
            self._authority(conn, row.frozen['identity']['binding'])
            conn.execute("UPDATE mf_report.plans SET owner_claimed=true,state='generating',updated_at=now() WHERE report_id=%s", (row.report_id,))
            return self._get(conn, principal, row.report_id)

    def workflow(self, principal, report_id, value):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, report_id, True)
            if not row.dispatch_claimed or row.workflow is not None and row.workflow != value:
                raise ReportError('conflict')
            conn.execute('UPDATE mf_report.plans SET workflow=%s,updated_at=now() WHERE report_id=%s', (Jsonb(value), row.report_id))
            return self._get(conn, principal, report_id)

    def first_request(self, principal, wire):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['report_id'], True)
            if row.plan_sha256 != wire['plan_sha256'] or str(row.attempt_id) != wire['attempt_id'] or row.state != 'generating' or row.cancel_requested:
                raise ReportError('report_cancelled')
            conn.execute('UPDATE mf_report.plans SET first_possible_request=true,updated_at=now() WHERE report_id=%s', (row.report_id,))

    def cancel(self, principal, report_id, plan_sha256):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, report_id, True)
            if row.plan_sha256 != plan_sha256:
                raise ReportError('conflict')
            conn.execute("UPDATE mf_report.plans SET cancel_requested=true,state=CASE WHEN state='planned' THEN 'cancelled' ELSE state END,error_code=CASE WHEN state='planned' THEN 'report_cancelled' ELSE error_code END,updated_at=now() WHERE report_id=%s", (row.report_id,))
            return self._get(conn, principal, report_id)

    def recover_expired(self, principal, report_id, plan_sha256):
        """Restart recovery never recreates a process or claims partial work resumed."""
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, report_id, True)
            if row.plan_sha256 != plan_sha256:
                raise ReportError('conflict')
            expired = conn.execute('SELECT owner_deadline IS NOT NULL AND owner_deadline<now() FROM mf_report.plans WHERE report_id=%s', (row.report_id,)).fetchone()[0]
        if expired and row.state in ('queued', 'generating'):
            wire = dict(schema_version=1, report_id=str(row.report_id), plan_sha256=plan_sha256, attempt_id=str(row.attempt_id))
            return self.finish(principal, wire, state='uncertain', error_code='report_uncertain',
                               cleanup=dict(known=False, pending=None, owner_thread_alive=None))
        return row

    def progress(self, principal, wire, value):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['report_id'], True)
            if row.state != 'generating' or str(row.attempt_id) != wire['attempt_id']:
                raise ReportError('conflict')
            conn.execute('UPDATE mf_report.plans SET progress=%s,updated_at=now() WHERE report_id=%s', (Jsonb(value), row.report_id))
            return self._get(conn, principal, row.report_id)

    def finish(self, principal, wire, *, state, cleanup, error_code=None, receipt=None, manifest=None, verify_output=None):
        """Publish only after owned cleanup; model-spent failures retain full capacity."""
        from .budget import BudgetLedger, ReservationState
        from nexaweave_knowledge.contracts import KnowledgeScope
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['report_id'], True)
            if row.plan_sha256 != wire['plan_sha256'] or str(row.attempt_id) != wire['attempt_id']:
                raise ReportError('conflict')
            if row.state in ('completed', 'failed', 'cancelled'):
                return row
            if state == 'completed':
                self._authority(conn, row.frozen['identity']['binding'])
                if row.cancel_requested or cleanup != dict(known=True, pending=False, owner_thread_alive=False):
                    raise ReportError('conflict')
                validate_manifest(manifest)
            if state not in ('completed', 'failed', 'cancelled', 'uncertain'):
                raise ReportError('invalid_request')
            total = len(manifest['files']) - 5 if manifest else row.progress['total_sections']
            progress = dict(stage=state, percent=100 if state == 'completed' else row.progress['percent'],
                completed_sections=total if state == 'completed' else row.progress['completed_sections'], total_sections=total)
            conn.execute('UPDATE mf_report.plans SET state=%s,cleanup=%s,progress=%s,error_code=%s,receipt=%s,manifest=%s,updated_at=now() WHERE report_id=%s',
                (state, Jsonb(cleanup), Jsonb(progress), error_code, Jsonb(receipt) if receipt else None, Jsonb(manifest) if manifest else None, row.report_id))
            updated = self._get(conn, principal, row.report_id)
            account_id = UUID(row.frozen['configuration']['account_id'])
            BudgetLedger._account(conn, principal, account_id, lock=True)
            reservation = BudgetLedger._row(conn, account_id, row.report_id)
            scope = KnowledgeScope.model_validate_json(json.dumps(row.frozen['identity']['binding']['scope']))
            if (reservation is None or reservation.attempt_id != row.attempt_id
                    or reservation.fingerprint != budget_fingerprint(row.plan_sha256)
                    or reservation.scope_group_id != scope.group_id
                    or reservation.episode_id != report_budget_episode(scope.group_id, row.report_id)
                    or reservation.evidence_ids or reservation.ceiling_microusd != row.frozen['identity']['ceiling_microusd']):
                raise ReportError('budget_denied')
            closed = cleanup == dict(known=True, pending=False, owner_thread_alive=False)
            # Fourth-purpose proof settles the full ceiling without forging
            # knowledge/preparation/native evidence or changing shared SQL.
            budget_state = 'settled' if state == 'completed' else ('released' if closed and not row.first_possible_request and state != 'uncertain' else 'uncertain')
            budget_receipt = None
            if state == 'completed':
                budget_receipt = ReportBudgetReceipt.from_wire(dict(kind='connected_report_budget_v1', operation_id=str(row.report_id),
                    attempt_id=str(row.attempt_id), fingerprint=budget_fingerprint(row.plan_sha256), plan_sha256=row.plan_sha256,
                    report_receipt=updated.receipt, report_receipt_sha256=digest(updated.receipt)))
            conn.execute('UPDATE mf_execution.reservations SET state=%s,receipt=%s,error_code=%s,updated_at=now() WHERE account_id=%s AND operation_id=%s',
                (budget_state, Jsonb(budget_receipt.json_value()) if budget_receipt is not None else None,
                 'dispatch_uncertain' if budget_state == 'uncertain' else None, account_id, row.report_id))
            if state == 'completed':
                saved = BudgetLedger._row(conn, account_id, row.report_id)
                if saved.state != ReservationState.settled or saved.receipt != budget_receipt:
                    raise ReportError('budget_denied')
                if not callable(verify_output):
                    raise ReportError('conflict')
                # File proof (including the lease's final checks) runs before
                # committing either publication or shared-account settlement.
                verify_output()
            return updated
