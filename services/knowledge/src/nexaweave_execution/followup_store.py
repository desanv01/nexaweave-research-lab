"""Owned follow-up journal, completed history chain, and shared-budget admission."""
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
import hashlib
import json
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from nexaweave_storage.transaction_settings import apply_runtime_settings

from .followup_contracts import (FollowupError, FollowupBudgetReceipt, IDENTITY,
    budget_episode, budget_fingerprint, digest, empty_head, encoded, next_head,
    validate_history, validate_identity, validate_manifest, validate_receipt,
    validate_result, _validate_completed_pair, dispatch, exact, sha, uuid_string,
    validate_answer_references)
from .report_contracts import ReportError
from .report_store import ReportStore


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
        raise FollowupError('followup_unavailable') from None


def _catalog(conn):
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_followup' AND c.relkind='r' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_followup' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_followup' AND c.relkind='r' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_followup' AND t.relkind='r' ORDER BY t.relname,i.relname")
    return hashlib.sha256(json.dumps([conn.execute(q).fetchall() for q in queries], default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def migrate(connection):
    try:
        sql = files('nexaweave_execution').joinpath('migrations/followup_0001.sql').read_text('utf-8')
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout='10s'")
            connection.execute("SET LOCAL lock_timeout='5s'")
            connection.execute('SET LOCAL search_path=pg_catalog')
            connection.execute('SELECT pg_advisory_xact_lock(720105,10)')
            if connection.execute("SELECT to_regnamespace('mf_followup') IS NOT NULL").fetchone()[0]:
                if not connection.execute("SELECT to_regclass('mf_followup.schema_migrations') IS NOT NULL").fetchone()[0]:
                    raise FollowupError('followup_unavailable')
                if connection.execute('SELECT version,checksum,schema_checksum FROM mf_followup.schema_migrations ORDER BY version').fetchall() != [(1, checksum, _catalog(connection))]:
                    raise FollowupError('followup_unavailable')
            else:
                connection.execute(sql)
                connection.execute('INSERT INTO mf_followup.schema_migrations VALUES(1,%s,%s)', (checksum, _catalog(connection)))
    except (OSError, psycopg.Error):
        raise FollowupError('followup_unavailable') from None


def rollback(connection):
    migrate(connection)
    with connection.transaction():
        connection.execute('SELECT pg_advisory_xact_lock(720105,10)')
        if connection.execute("SELECT 1 FROM mf_followup.turns WHERE state IN ('queued','generating','uncertain') LIMIT 1").fetchone():
            raise FollowupError('busy')
        connection.execute(files('nexaweave_execution').joinpath('migrations/followup_0001_down.sql').read_text('utf-8'))


@dataclass(frozen=True)
class TurnRecord:
    turn_id: UUID
    principal: str
    report_id: UUID
    plan_sha256: str
    frozen: dict
    state: str
    attempt_id: UUID | None
    workflow: dict | None
    receipt: dict | None
    manifest: dict | None
    answer_sha256: str | None
    answer_prefix: str | None
    answer_characters: int | None
    published_head_sha256: str | None
    progress: dict
    cleanup: dict
    cancel_requested: bool
    dispatch_claimed: bool
    owner_claimed: bool
    first_possible_request: bool
    error_code: str | None

    def public(self, authorization=None):
        value = dict(self.frozen['identity'], plan_sha256=self.plan_sha256,
            authorization=authorization or dict(model_calls_enabled=False, budget_configured=False),
            state=self.state, progress=self.progress, workflow=self.workflow,
            receipt=self.receipt, receipt_sha256=digest(self.receipt) if self.receipt else None,
            manifest=self.manifest, published_history_head_sha256=self.published_head_sha256,
            cleanup=self.cleanup, cancel_requested=self.cancel_requested, error_code=self.error_code)
        return validate_result(value, value['binding']['display_graph_id'], value['binding']['scope'],
            dict(schema_version=1, turn_id=str(self.turn_id), plan_sha256=self.plan_sha256),
            'status', self.principal)


COLUMNS = ','.join(TurnRecord.__dataclass_fields__)


def _record(raw):
    if raw is None:
        raise FollowupError('not_found')
    try:
        row = TurnRecord(*raw)
        if type(row.frozen) is not dict or set(row.frozen) != {'identity','context','configuration'}:
            raise ValueError
        identity = validate_identity(row.frozen['identity'])
        config=exact(row.frozen['configuration'],('account_id','factory_sha256'))
        sha(config['factory_sha256'])
        if config['account_id'] is not None:
            uuid_string(config['account_id'])
        if (row.turn_id != UUID(identity['turn_id']) or row.report_id != UUID(identity['binding']['report']['report_id'])
                or row.principal != identity['binding']['principal'] or row.plan_sha256 != digest(identity)
                or row.frozen['context']['binding'] != identity['binding']['native_binding']
                or digest(row.frozen['context']) != identity['context_sha256']
                or digest(row.frozen['context']['graph']) != identity['source_projection_sha256']):
            raise ValueError
        if any(type(v) is not bool for v in (row.cancel_requested,row.dispatch_claimed,row.owner_claimed,row.first_possible_request)):
            raise ValueError
        if row.dispatch_claimed != (type(row.attempt_id) is UUID) or row.owner_claimed and not row.dispatch_claimed or row.first_possible_request and not row.owner_claimed:
            raise ValueError
        if row.state in ('queued','generating','completed','failed','uncertain') and not row.dispatch_claimed:
            raise ValueError
        if row.state == 'completed' and not (row.owner_claimed and row.first_possible_request):
            raise ValueError
        row.public()
        return row
    except (ValueError, TypeError, KeyError, FollowupError):
        raise FollowupError('followup_uncertain') from None


class FollowupStore:
    def __init__(self, connection_factory):
        if not callable(connection_factory):
            raise FollowupError('followup_unavailable')
        self.connect = connection_factory

    @staticmethod
    def _get(conn, principal, turn_id, lock=False):
        return _record(conn.execute(f'SELECT {COLUMNS} FROM mf_followup.turns WHERE principal=%s AND turn_id=%s' + (' FOR UPDATE' if lock else ''),
            (principal, UUID(str(turn_id)))).fetchone())

    @staticmethod
    def _parent(conn, principal, report_id, report_plan_sha256):
        try:
            parent = ReportStore._get(conn, principal, report_id, True)
            ReportStore._authority(conn, parent.frozen['identity']['binding'])
        except ReportError:
            raise FollowupError('conflict') from None
        if parent.state != 'completed' or parent.plan_sha256 != report_plan_sha256:
            raise FollowupError('conflict')
        return parent

    @staticmethod
    def _bound_parent(parent, report):
        if (digest(parent.receipt)!=report['receipt_sha256']
                or digest(parent.manifest)!=report['manifest_sha256']
                or next(f['sha256'] for f in parent.manifest['files'] if f['name']=='full_report.md')!=report['full_report_sha256']):
            raise FollowupError('conflict')
        return parent

    @staticmethod
    def _head(conn, principal, report_id, parent_plan, lock=False, allow_empty=False):
        query = ('SELECT principal,report_plan_sha256,head_sha256,total_completed,active_turn_id '
                 'FROM mf_followup.histories WHERE report_id=%s' + (' FOR UPDATE' if lock else ''))
        row = conn.execute(query, (UUID(str(report_id)),)).fetchone()
        if row is None:
            if allow_empty:
                return principal,parent_plan,empty_head(str(report_id),parent_plan),0,None
            raise FollowupError('followup_unavailable')
        if row[0] != principal or row[1] != parent_plan or not 0 <= row[3] <= 1000:
            raise FollowupError('followup_uncertain')
        try:
            sha(row[2])
        except ValueError:
            raise FollowupError('followup_uncertain') from None
        return row

    @staticmethod
    def _pairs(conn, principal, report_id, end):
        if end <= 0:
            return []
        rows = conn.execute('SELECT ' + COLUMNS + " FROM mf_followup.turns WHERE principal=%s AND report_id=%s AND state='completed' AND ((receipt->>'ordinal')::integer) BETWEEN %s AND %s ORDER BY ((receipt->>'ordinal')::integer)",
            (principal, UUID(str(report_id)), max(1,end-4), end)).fetchall()
        if len(rows) != min(5,end):
            raise FollowupError('followup_uncertain')
        pairs = []
        first=max(1,end-4)
        if first==1:
            previous=empty_head(str(report_id),_record(rows[0]).frozen['identity']['binding']['report']['plan_sha256'])
        else:
            prior=conn.execute("SELECT published_head_sha256 FROM mf_followup.turns WHERE principal=%s AND report_id=%s AND state='completed' AND ((receipt->>'ordinal')::integer)=%s",
                (principal,UUID(str(report_id)),first-1)).fetchone()
            if prior is None or type(prior[0]) is not str:
                raise FollowupError('followup_uncertain')
            previous=prior[0]
        for raw in rows:
            row = _record(raw)
            answer = row.answer_prefix
            pair = dict(turn_id=str(row.turn_id), plan_sha256=row.plan_sha256,
                ordinal=row.receipt['ordinal'], question=row.frozen['identity']['options']['question'],
                answer_sha256=row.answer_sha256, answer_prefix=answer,
                answer_prefix_sha256=hashlib.sha256(answer.encode('utf-8')).hexdigest(),
                answer_characters=row.answer_characters, admitted_characters=len(answer),
                truncated=row.answer_characters > len(answer), receipt_sha256=digest(row.receipt),
                predecessor_head_sha256=row.receipt['history_head_sha256'],
                published_head_sha256=row.published_head_sha256)
            _validate_completed_pair(pair, str(report_id), row.frozen['identity']['binding']['report']['plan_sha256'])
            if pair['predecessor_head_sha256']!=previous:
                raise FollowupError('followup_uncertain')
            previous=pair['published_head_sha256']
            pairs.append(pair)
        return pairs

    def history(self, principal, report_id, report_plan_sha256, before_ordinal=None):
        with transaction(self.connect) as conn:
            parent = self._parent(conn, principal, report_id, report_plan_sha256)
            head = self._head(conn, principal, report_id, report_plan_sha256, allow_empty=True)
            if before_ordinal is not None and before_ordinal > head[3] + 1:
                raise FollowupError('invalid_request')
            end = head[3] if before_ordinal is None else before_ordinal - 1
            pairs = self._pairs(conn, principal, report_id, end)
            native = parent.frozen['identity']['binding']
            return dict(schema_version=1, report_id=str(report_id), report_plan_sha256=report_plan_sha256,
                binding=dict(display_graph_id=native['display_graph_id'], principal=principal, scope=native['scope'],
                    report=dict(report_id=str(report_id), plan_sha256=report_plan_sha256,
                        receipt_sha256=digest(parent.receipt), manifest_sha256=digest(parent.manifest),
                        full_report_sha256=next(f['sha256'] for f in parent.manifest['files'] if f['name']=='full_report.md')),
                    native_binding=native), head_sha256=head[2], total_completed=head[3],
                before_ordinal=before_ordinal, pairs=pairs)

    def latest(self, principal, report_id, report_plan_sha256):
        value = self.history(principal, report_id, report_plan_sha256)
        pairs = value['pairs']
        result = dict(head_sha256=value['head_sha256'], total_completed=value['total_completed'],
            window_start=value['total_completed']-len(pairs)+1, pairs=pairs)
        try:
            validate_history(result, str(report_id), report_plan_sha256)
        except (ValueError, KeyError):
            raise FollowupError('followup_uncertain') from None
        return result

    def completed_rows(self, principal, report_id, report_plan_sha256):
        """A bounded, ordered journal projection; full answers stay in artifacts."""
        with transaction(self.connect) as conn:
            self._parent(conn, principal, report_id, report_plan_sha256)
            head = self._head(conn, principal, report_id, report_plan_sha256)
            rows = conn.execute('SELECT ' + COLUMNS + " FROM mf_followup.turns WHERE principal=%s AND report_id=%s AND state='completed' ORDER BY ((receipt->>'ordinal')::integer)",
                (principal, UUID(str(report_id)))).fetchall()
            if len(rows) != head[3]:
                raise FollowupError('followup_uncertain')
            previous = empty_head(str(report_id), report_plan_sha256)
            result = []
            for ordinal, raw in enumerate(rows, 1):
                row = _record(raw)
                if row.receipt['ordinal'] != ordinal or row.receipt['history_head_sha256'] != previous:
                    raise FollowupError('followup_uncertain')
                previous = row.published_head_sha256
                result.append(row)
            if previous != head[2]:
                raise FollowupError('followup_uncertain')
            return result

    def get(self, principal, turn_id, plan_sha256=None):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, turn_id)
            if plan_sha256 is not None and row.plan_sha256 != plan_sha256:
                raise FollowupError('turn_conflict')
            return row

    def put(self, principal, identity, context, configuration):
        identity = validate_identity(identity)
        if identity['binding']['principal'] != principal or len(encoded(context)) > 2097152:
            raise FollowupError('unauthorized')
        frozen = json.loads(encoded(dict(identity=identity, context=context, configuration=configuration)))
        report = identity['binding']['report']
        with transaction(self.connect) as conn:
            # Make the FK parent if needed, then claim the immutable turn row
            # before parent/head locks. Queue/claim/finish use the same order.
            conn.execute('INSERT INTO mf_followup.histories(report_id,principal,report_plan_sha256,head_sha256) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (UUID(report['report_id']), principal, report['plan_sha256'], empty_head(report['report_id'], report['plan_sha256'])))
            progress = dict(stage='planned', percent=0, completed_sections=0, total_sections=0)
            cleanup = dict(known=True, pending=False, owner_thread_alive=False)
            inserted = conn.execute("INSERT INTO mf_followup.turns(turn_id,principal,report_id,plan_sha256,frozen,state,progress,cleanup) VALUES(%s,%s,%s,%s,%s,'planned',%s,%s) ON CONFLICT DO NOTHING RETURNING turn_id",
                (UUID(identity['turn_id']), principal, UUID(report['report_id']), digest(identity), Jsonb(frozen), Jsonb(progress), Jsonb(cleanup))).fetchone()
            try:
                row = self._get(conn, principal, identity['turn_id'], True)
            except FollowupError:
                raise FollowupError('turn_conflict') from None
            if row.plan_sha256 != digest(identity) or row.frozen != frozen:
                raise FollowupError('turn_conflict')
            parent = self._parent(conn, principal, report['report_id'], report['plan_sha256'])
            if (digest(parent.receipt) != report['receipt_sha256'] or digest(parent.manifest) != report['manifest_sha256']
                    or identity['binding']['native_binding'] != parent.frozen['identity']['binding']
                    or context != parent.frozen['context']):
                raise FollowupError('conflict')
            if inserted is None:
                return row
            head = self._head(conn, principal, report['report_id'], report['plan_sha256'], True)
            if head[3] >= 1000:
                raise FollowupError('result_too_large')
            current = dict(head_sha256=head[2], total_completed=head[3],
                window_start=head[3]-min(5,head[3])+1,
                pairs=self._pairs(conn, principal, report['report_id'], head[3]))
            if current != identity['history']:
                raise FollowupError('history_changed')
            return row

    def queue(self, principal, turn_id, plan_sha256, scope, account_id):
        """Replay resolves first; new dispatch and shared allowance lock commit together."""
        from .budget import BudgetLedger
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, turn_id, True)
            if row.plan_sha256 != plan_sha256:
                raise FollowupError('turn_conflict')
            if row.state != 'planned' or row.cancel_requested:
                return row, False
            report = row.frozen['identity']['binding']['report']
            self._bound_parent(self._parent(conn, principal, row.report_id, report['plan_sha256']),report)
            head = self._head(conn, principal, row.report_id, report['plan_sha256'], True)
            if head[2] != row.frozen['identity']['history']['head_sha256'] or head[3] != row.frozen['identity']['history']['total_completed']:
                raise FollowupError('history_changed')
            if head[4] is not None:
                raise FollowupError('followup_active')
            account = BudgetLedger._account(conn, principal, account_id, lock=True)
            if (account[2] != scope.project_id or scope.model_dump(mode='json') != row.frozen['identity']['binding']['scope']
                    or str(account_id) != row.frozen['configuration']['account_id']):
                raise FollowupError('budget_denied')
            ceiling = row.frozen['identity']['ceiling_microusd']
            if ceiling is None or BudgetLedger._row(conn, account_id, row.turn_id) is not None:
                raise FollowupError('budget_denied')
            accounted, reserved, _ = BudgetLedger._totals(conn, account_id)
            if ceiling > account[3] - accounted - reserved:
                raise FollowupError('budget_denied')
            attempt = uuid4()
            conn.execute("INSERT INTO mf_execution.reservations(account_id,operation_id,fingerprint,ceiling_microusd,state,attempt_id,scope_group_id,episode_id,evidence_ids) VALUES(%s,%s,%s,%s,'started',%s,%s,%s,%s)",
                (account_id,row.turn_id,budget_fingerprint(plan_sha256),ceiling,attempt,scope.group_id,
                 budget_episode(scope.group_id,row.turn_id),Jsonb([])))
            conn.execute("UPDATE mf_followup.histories SET active_turn_id=%s,updated_at=now() WHERE report_id=%s",
                (row.turn_id,row.report_id))
            conn.execute("UPDATE mf_followup.turns SET state='queued',dispatch_claimed=true,attempt_id=%s,cleanup=%s,progress=%s,owner_deadline=now()+(%s * interval '1 second'),updated_at=now() WHERE turn_id=%s",
                (attempt,Jsonb(dict(known=False,pending=None,owner_thread_alive=None)),
                 Jsonb(dict(stage='queued',percent=0,completed_sections=0,total_sections=0)),
                 row.frozen['identity']['limits']['max_run_seconds']+30,row.turn_id))
            return self._get(conn, principal, row.turn_id), True

    def claim(self, principal, wire):
        dispatch(wire)
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['turn_id'], True)
            if row.plan_sha256 != wire['plan_sha256'] or str(row.attempt_id) != wire['attempt_id'] or row.state != 'queued' or row.owner_claimed:
                raise FollowupError('turn_conflict')
            report=row.frozen['identity']['binding']['report']
            self._bound_parent(self._parent(conn, principal, row.report_id, report['plan_sha256']),report)
            conn.execute("UPDATE mf_followup.turns SET owner_claimed=true,state='generating',progress=%s,updated_at=now() WHERE turn_id=%s",
                (Jsonb(dict(stage='generating',percent=0,completed_sections=0,total_sections=0)),row.turn_id))
            return self._get(conn, principal, row.turn_id)

    def workflow(self, principal, turn_id, value):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, turn_id, True)
            if not row.dispatch_claimed or row.workflow is not None and row.workflow != value:
                raise FollowupError('turn_conflict')
            conn.execute('UPDATE mf_followup.turns SET workflow=%s,updated_at=now() WHERE turn_id=%s',(Jsonb(value),row.turn_id))
            return self._get(conn, principal, turn_id)

    def first_request(self, principal, wire):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['turn_id'], True)
            if row.plan_sha256 != wire['plan_sha256'] or str(row.attempt_id) != wire['attempt_id'] or row.state != 'generating' or row.cancel_requested:
                raise FollowupError('followup_cancelled')
            conn.execute('UPDATE mf_followup.turns SET first_possible_request=true,updated_at=now() WHERE turn_id=%s',(row.turn_id,))

    def cancel(self, principal, turn_id, plan_sha256):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, turn_id, True)
            if row.plan_sha256 != plan_sha256:
                raise FollowupError('turn_conflict')
            conn.execute("UPDATE mf_followup.turns SET cancel_requested=true,state=CASE WHEN state='planned' THEN 'cancelled' ELSE state END,progress=CASE WHEN state='planned' THEN %s ELSE progress END,error_code=CASE WHEN state='planned' THEN 'followup_cancelled' ELSE error_code END,updated_at=now() WHERE turn_id=%s",
                (Jsonb(dict(stage='cancelled',percent=0,completed_sections=0,total_sections=0)),row.turn_id))
            return self._get(conn, principal, turn_id)

    def recover_expired(self, principal, turn_id, plan_sha256):
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, turn_id, True)
            if row.plan_sha256 != plan_sha256:
                raise FollowupError('turn_conflict')
            expired = conn.execute('SELECT owner_deadline IS NOT NULL AND owner_deadline<now() FROM mf_followup.turns WHERE turn_id=%s',(row.turn_id,)).fetchone()[0]
        if expired and row.state in ('queued','generating'):
            return self.finish(principal,dict(schema_version=1,turn_id=str(row.turn_id),plan_sha256=plan_sha256,
                attempt_id=str(row.attempt_id)),state='uncertain',error_code='followup_uncertain',
                cleanup=dict(known=False,pending=None,owner_thread_alive=None))
        return row

    def finish(self, principal, wire, *, state, cleanup, error_code=None, receipt=None,
               manifest=None, answer=None, verify_output=None):
        from .budget import BudgetLedger, ReservationState
        from nexaweave_knowledge.contracts import KnowledgeScope
        dispatch(wire)
        with transaction(self.connect) as conn:
            row = self._get(conn, principal, wire['turn_id'], True)
            if row.plan_sha256 != wire['plan_sha256'] or str(row.attempt_id) != wire['attempt_id']:
                raise FollowupError('turn_conflict')
            if row.state in ('completed','failed','cancelled'):
                return row
            if state not in ('completed','failed','cancelled','uncertain'):
                raise FollowupError('invalid_request')
            identity = row.frozen['identity']
            report = identity['binding']['report']
            if state == 'completed':
                self._bound_parent(self._parent(conn, principal, row.report_id, report['plan_sha256']),report)
            head = self._head(conn, principal, row.report_id, report['plan_sha256'], True)
            if head[4] != row.turn_id:
                raise FollowupError('turn_conflict')
            completed = state == 'completed'
            closed = cleanup == dict(known=True,pending=False,owner_thread_alive=False)
            if completed:
                if (row.cancel_requested or not closed
                        or head[2] != identity['history']['head_sha256']
                        or head[3] != identity['history']['total_completed'] or head[3]>=1000):
                    raise FollowupError('history_changed')
                if type(answer) is not str or not answer.strip() or len(answer.encode('utf-8')) > 16384:
                    raise FollowupError('result_too_large')
                validate_answer_references(answer,identity)
                validate_manifest(manifest); validate_receipt(receipt, identity, manifest)
                answer_hash = hashlib.sha256(answer.encode('utf-8')).hexdigest()
                if next(f['sha256'] for f in manifest['files'] if f['name']=='answer.md') != answer_hash:
                    raise FollowupError('conflict')
                published = next_head(str(row.report_id), report['plan_sha256'], head[2],
                    head[3]+1,str(row.turn_id),row.plan_sha256,identity['options']['question'],
                    answer_hash,digest(receipt))
                if not callable(verify_output):
                    raise FollowupError('conflict')
                verify_output()
                conn.execute("UPDATE mf_followup.histories SET head_sha256=%s,total_completed=total_completed+1,active_turn_id=NULL,updated_at=now() WHERE report_id=%s",
                    (published,row.report_id))
            elif closed and state != 'uncertain':
                conn.execute('UPDATE mf_followup.histories SET active_turn_id=NULL,updated_at=now() WHERE report_id=%s',(row.report_id,))
            progress = dict(stage=state,percent=100 if completed else row.progress['percent'],
                            completed_sections=0,total_sections=0)
            conn.execute('UPDATE mf_followup.turns SET state=%s,cleanup=%s,progress=%s,error_code=%s,receipt=%s,manifest=%s,answer_sha256=%s,answer_prefix=%s,answer_characters=%s,published_head_sha256=%s,updated_at=now() WHERE turn_id=%s',
                (state,Jsonb(cleanup),Jsonb(progress),error_code,Jsonb(receipt) if completed else None,
                 Jsonb(manifest) if completed else None,answer_hash if completed else None,
                 answer[:4000] if completed else None,len(answer) if completed else None,
                 published if completed else None,row.turn_id))
            account_id = UUID(row.frozen['configuration']['account_id'])
            BudgetLedger._account(conn, principal, account_id, lock=True)
            reservation = BudgetLedger._row(conn, account_id, row.turn_id)
            scope = KnowledgeScope.model_validate_json(json.dumps(identity['binding']['scope']))
            if (reservation is None or reservation.attempt_id != row.attempt_id
                    or reservation.fingerprint != budget_fingerprint(row.plan_sha256)
                    or reservation.scope_group_id != scope.group_id
                    or reservation.episode_id != budget_episode(scope.group_id,row.turn_id)
                    or reservation.evidence_ids or reservation.ceiling_microusd != identity['ceiling_microusd']):
                raise FollowupError('budget_denied')
            budget_state = 'settled' if completed else ('released' if closed and not row.first_possible_request and state != 'uncertain' else 'uncertain')
            proof = None
            if completed:
                proof = FollowupBudgetReceipt.from_wire(dict(kind='connected_followup_budget_v1',
                    operation_id=str(row.turn_id),attempt_id=str(row.attempt_id),
                    fingerprint=budget_fingerprint(row.plan_sha256),plan_sha256=row.plan_sha256,
                    followup_receipt=receipt,followup_receipt_sha256=digest(receipt)))
            conn.execute('UPDATE mf_execution.reservations SET state=%s,receipt=%s,error_code=%s,updated_at=now() WHERE account_id=%s AND operation_id=%s',
                (budget_state,Jsonb(proof.json_value()) if proof else None,
                 'dispatch_uncertain' if budget_state=='uncertain' else None,account_id,row.turn_id))
            if completed:
                saved = BudgetLedger._row(conn, account_id, row.turn_id)
                if saved.state != ReservationState.settled or saved.receipt != proof:
                    raise FollowupError('budget_denied')
            return self._get(conn, principal, row.turn_id)
