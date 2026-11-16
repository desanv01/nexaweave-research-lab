"""Explicit isolated PG launch schema; no accepted catalog is modified."""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from importlib.resources import files
import hashlib
import json
import psycopg
from psycopg.types.json import Jsonb
from nexaweave_storage.transaction_settings import apply_runtime_settings
from .native_launch_contracts import LaunchAuthorityError, validate_identity, validate_declaration, digest, identifier, sha
from .native_run_contracts import NativeRunRequest, NativeRunReceipt, InvalidNativeRun, NativeRunUncertain
from .native_run_store import NativeRunRecord, _SELECT as _NATIVE_COLUMNS, _row as _native_record
from uuid import UUID


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
        raise LaunchAuthorityError('native_launch_unavailable') from None


def _catalog(conn):
    queries = (
        "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_native_launch' AND c.relkind='r' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='mf_native_launch' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.condeferrable,x.condeferred FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_native_launch' AND c.relkind='r' ORDER BY c.relname,x.conname",
        "SELECT t.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='mf_native_launch' AND t.relkind='r' ORDER BY t.relname,i.relname")
    return hashlib.sha256(json.dumps([conn.execute(q).fetchall() for q in queries], default=str, separators=(',', ':')).encode()).hexdigest()


_MIGRATIONS = 'SELECT version,checksum,schema_checksum FROM mf_native_launch.schema_migrations ORDER BY version'
_SAVE_MIGRATION = 'INSERT INTO mf_native_launch.schema_migrations VALUES(%s,%s,%s)'


def _chained_catalog(connection, previous_receipt):
    return digest({'catalog_sha256': _catalog(connection), 'prior_migration': list(previous_receipt)})


def migrate(connection):
    try:
        directory = files('nexaweave_execution').joinpath('migrations')
        sql_v1 = directory.joinpath('native_launch_0001.sql').read_text('utf-8')
        sql_v2 = directory.joinpath('native_launch_0002.sql').read_text('utf-8')
        checksum_v1 = hashlib.sha256(sql_v1.encode()).hexdigest()
        checksum_v2 = hashlib.sha256(sql_v2.encode()).hexdigest()
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout='10s'")
            connection.execute("SET LOCAL lock_timeout='5s'")
            connection.execute('SET LOCAL search_path=pg_catalog')
            connection.execute('SELECT pg_advisory_xact_lock(720105,8)')
            if not connection.execute("SELECT to_regnamespace('mf_native_launch') IS NOT NULL").fetchone()[0]:
                connection.execute(sql_v1)
                connection.execute(_SAVE_MIGRATION, (1, checksum_v1, _catalog(connection)))
            if not connection.execute("SELECT to_regclass('mf_native_launch.schema_migrations') IS NOT NULL").fetchone()[0]:
                raise LaunchAuthorityError('native_launch_unavailable')
            rows = connection.execute(_MIGRATIONS).fetchall()
            versions = [row[0] for row in rows]
            if versions not in ([1], [1, 2]) or rows[0][1] != checksum_v1:
                raise LaunchAuthorityError('native_launch_unavailable')
            previous = rows[0]
            if versions == [1]:
                if previous[2] != _catalog(connection):
                    raise LaunchAuthorityError('native_launch_unavailable')
                connection.execute(sql_v2)
                connection.execute(_SAVE_MIGRATION, (2, checksum_v2, _chained_catalog(connection, previous)))
                rows = connection.execute(_MIGRATIONS).fetchall()
            if rows != [previous, (2, checksum_v2, _chained_catalog(connection, previous))]:
                raise LaunchAuthorityError('native_launch_unavailable')
    except (OSError, psycopg.Error):
        raise LaunchAuthorityError('native_launch_unavailable') from None


@dataclass(frozen=True)
class LaunchRecord:
    run_id: object
    principal: str
    preparation_id: object
    simulation_id: str
    project_id: object
    project_revision: int
    request_sha256: str
    launch_sha256: str
    frozen: dict
    state: str
    budget_attempt_id: object
    workflow: dict | None
    receipt: dict | None
    cancel_requested: bool
    error_code: str | None
    dispatch_claimed: bool = False
    source_run_id: UUID | None = None

    @property
    def request(self):
        return NativeRunRequest.from_wire(self.frozen['identity']['request'])


_COLUMNS = ','.join(LaunchRecord.__dataclass_fields__)


def _configuration(value):
    if type(value) is not dict or set(value)!={'account_id','factory_sha256'}:
        raise ValueError
    sha(value['factory_sha256'])
    if value['account_id'] is not None:
        if type(value['account_id']) is not str:
            raise ValueError
        identifier(value['account_id'])
    return value


def _coherent_request(declaration, identity):
    request = NativeRunRequest.from_wire(identity['request'])
    if (declaration['schema_version'] != identity['schema_version']
            or declaration.get('checkpoint') != identity.get('checkpoint')
            or declaration['launch_id'] != str(request.run_id)
            or declaration['preparation'] != {
                key: identity['preparation'][key] for key in ('operation_id', 'plan_sha256')
            }):
        raise ValueError
    return request


def _record(row):
    if row is None:
        raise LaunchAuthorityError('not_found')
    try:
        result = LaunchRecord(*row)
        if (type(result.frozen) is not dict or set(result.frozen)!={'identity','declaration','configuration'}
                or len(json.dumps(result.frozen,ensure_ascii=True,allow_nan=False,separators=(',',':'))) > 70000):
            raise ValueError
        configuration = _configuration(result.frozen['configuration'])
        identity = validate_identity(result.frozen['identity'])
        declaration = validate_declaration(result.frozen['declaration'])
        req = _coherent_request(declaration, identity)
        if (digest(identity) != result.launch_sha256 or digest(declaration) != result.request_sha256
                or req.run_id != result.run_id or req.principal != result.principal or req.project_id != result.project_id
                or req.project_revision != result.project_revision or req.simulation_id != result.simulation_id
                or identifier(identity['preparation']['operation_id']) != result.preparation_id
                or (result.source_run_id is not None and type(result.source_run_id) is not UUID)
                or result.source_run_id != req.source_run_id):
            raise ValueError
        if (any(type(v) is not UUID for v in (result.run_id,result.project_id,result.preparation_id))
                or type(result.project_revision) is not int
                or type(result.dispatch_claimed) is not bool or type(result.cancel_requested) is not bool
                or type(result.state) is not str or result.state not in {'planned','queued','starting','running','completed','failed','cancelled','uncertain'}):
            raise ValueError
        if result.dispatch_claimed:
            if (result.state=='planned' or type(result.budget_attempt_id) is not UUID
                    or configuration['account_id'] is None or identity['ceiling_microusd'] is None):
                raise ValueError
            if (result.state in {'completed','failed','cancelled'}) != (result.receipt is not None):
                raise ValueError
        elif (result.budget_attempt_id is not None or result.workflow is not None or result.receipt is not None
                or result.state not in {'planned','cancelled'}
                or result.cancel_requested != (result.state=='cancelled')):
            raise ValueError
        if result.error_code != ('native_launch_uncertain' if result.state=='uncertain' else None):
            raise ValueError
        if result.workflow is not None:
            workflow = result.workflow
            if (type(workflow) is not dict or set(workflow) != {'workflow_id','temporal_run_id','native_run_id'}
                    or workflow['workflow_id'] != 'mf-native-v1-' + req.run_id.hex + '-' + req.fingerprint
                    or workflow['native_run_id'] != str(req.run_id) or type(workflow['temporal_run_id']) is not str):
                raise ValueError
            identifier(workflow['temporal_run_id'])
        if result.receipt is not None:
            if type(result.receipt) is not dict:
                raise ValueError
            receipt = NativeRunReceipt.from_wire(result.receipt)
            if receipt.run_id != req.run_id or receipt.request_fingerprint != req.fingerprint or receipt.outcome != result.state:
                raise ValueError
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, InvalidNativeRun, LaunchAuthorityError):
        raise LaunchAuthorityError('native_launch_uncertain') from None


def _verify_current_preparation(conn, request, identity):
    descriptor = identity['preparation']
    project = conn.execute(
        'SELECT current_revision FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE',
        (request.principal, request.project_id),
    ).fetchone()
    prep = conn.execute(
        'SELECT state,plan_sha256,receipt,project_id,project_revision,display_graph_id,'
        "frozen #> '{public,scope}',frozen #> '{public,options}' FROM mf_preparation.plans "
        'WHERE principal=%s AND operation_id=%s FOR UPDATE',
        (request.principal, identifier(descriptor['operation_id'])),
    ).fetchone()
    if (project is None or project[0] != request.project_revision or prep is None
            or prep[0] != 'ready' or prep[1] != descriptor['plan_sha256']
            or type(prep[2]) is not dict or prep[2].get('artifact_sha256') != descriptor['artifact_sha256']):
        raise LaunchAuthorityError('conflict')
    if identity['schema_version'] == 2:
        options = prep[7]
        if (prep[3] != request.project_id or prep[4] != request.project_revision
                or prep[5] != identity['display_graph_id'] or prep[6] != identity['scope']
                or prep[2].get('simulation_id') != request.simulation_id
                or type(options) is not dict or options.get('platforms') != list(request.platforms)
                or type(options.get('seed')) is not int or options['seed'] != request.seed
                or type(options.get('max_rounds')) is not int or options['max_rounds'] != request.max_rounds):
            raise LaunchAuthorityError('conflict')


def _verify_checkpoint_source(conn, request, identity):
    source_row = conn.execute(
        f"SELECT {_COLUMNS} FROM mf_native_launch.plans "
        "WHERE principal=%s AND run_id=%s AND state='completed' FOR UPDATE",
        (request.principal, request.source_run_id),
    ).fetchone()
    if source_row is None:
        raise LaunchAuthorityError('conflict')
    source = _record(source_row)
    source_identity = source.frozen['identity']
    original = replace(request, schema_version=1, run_id=request.source_run_id,
                       source_run_id=None, checkpoint_sha256=None)
    if (source_identity.get('checkpoint') != {'mode': 'capture'}
            or source_identity['scope'] != identity['scope']
            or source_identity['preparation'] != identity['preparation']
            or source.request != original or not source.dispatch_claimed):
        raise LaunchAuthorityError('conflict')
    receipt = NativeRunReceipt.from_wire(source.receipt)
    if receipt.evidence_sha256 != request.checkpoint_sha256:
        raise LaunchAuthorityError('conflict')
    native_row = conn.execute(
        f"SELECT {_NATIVE_COLUMNS} FROM mf_native_execution.runs "
        "WHERE principal=%s AND run_id=%s AND state='completed' FOR SHARE",
        (request.principal, request.source_run_id),
    ).fetchone()
    if native_row is None:
        raise LaunchAuthorityError('conflict')
    try:
        native = _native_record(native_row)
    except NativeRunUncertain:
        raise LaunchAuthorityError('native_launch_uncertain') from None
    if native.request != original or native.receipt != receipt:
        raise LaunchAuthorityError('conflict')


class NativeLaunchStore:
    def __init__(self, connection_factory):
        if not callable(connection_factory):
            raise LaunchAuthorityError('invalid_request')
        self._connect = connection_factory

    @staticmethod
    def _get(conn, principal, run_id, lock=False):
        return _record(conn.execute(f'SELECT {_COLUMNS} FROM mf_native_launch.plans WHERE principal=%s AND run_id=%s' + (' FOR UPDATE' if lock else ''),
                                    (principal, identifier(run_id))).fetchone())

    def get(self, principal, run_id, launch_sha256=None):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id)
            if launch_sha256 is not None and row.launch_sha256 != sha(launch_sha256):
                raise LaunchAuthorityError('conflict')
            return row

    def put(self, principal, declaration, identity, *, configuration=None):
        identity = validate_identity(identity)
        declaration = validate_declaration(declaration)
        try:
            _configuration(configuration)
            req = _coherent_request(declaration, identity)
        except (ValueError, TypeError, KeyError, InvalidNativeRun):
            raise LaunchAuthorityError('invalid_request') from None
        if principal != req.principal:
            raise LaunchAuthorityError('unauthorized')
        frozen = json.loads(json.dumps({'declaration': declaration, 'identity': identity,
                                      'configuration': configuration}, ensure_ascii=True, allow_nan=False))
        request_sha256, launch_sha256 = digest(declaration), digest(identity)
        descriptor = identity['preparation']
        preparation_id = identifier(descriptor['operation_id'])
        version = declaration['schema_version']
        with transaction(self._connect) as conn:
            if version == 1:
                # Keep the existing serialized preparation check for v1 review.
                prep = conn.execute(
                    'SELECT state FROM mf_preparation.plans WHERE principal=%s AND operation_id=%s FOR UPDATE',
                    (principal, preparation_id),
                ).fetchone()
                if prep is None or prep[0] != 'ready':
                    raise LaunchAuthorityError('conflict')
            read_prior = ('SELECT request_sha256,launch_sha256,state FROM mf_native_launch.plans '
                          'WHERE run_id=%s AND principal=%s')
            prior = conn.execute(read_prior, (req.run_id, principal)).fetchone()
            if version == 2 and (prior is None or prior[2] == 'planned'):
                _verify_current_preparation(conn, req, identity)
                # A same-ID plan can appear while waiting for preparation ownership.
                prior = conn.execute(read_prior, (req.run_id, principal)).fetchone()
            if prior is not None and prior[:2] != (request_sha256, launch_sha256):
                raise LaunchAuthorityError('conflict')
            if version == 2:
                if (prior is None or prior[2] == 'planned') and req.source_run_id is not None:
                    _verify_checkpoint_source(conn, req, identity)
                if prior is not None:
                    return self._get(conn, principal, req.run_id)
            count = conn.execute('SELECT count(*) FROM mf_native_launch.plans WHERE principal=%s AND preparation_id=%s',
                                 (principal, preparation_id)).fetchone()[0]
            if prior is None and count>=100:
                raise LaunchAuthorityError('busy')
            conn.execute(
                "INSERT INTO mf_native_launch.plans(run_id,principal,preparation_id,simulation_id,project_id,"
                "project_revision,request_sha256,launch_sha256,frozen,state,source_run_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'planned',%s) ON CONFLICT DO NOTHING",
                (req.run_id, principal, preparation_id, req.simulation_id, req.project_id,
                 req.project_revision, request_sha256, launch_sha256, Jsonb(frozen), req.source_run_id),
            )
            try:
                row = self._get(conn, principal, req.run_id)
            except LaunchAuthorityError:
                raise LaunchAuthorityError('conflict') from None
            if row.request_sha256 != request_sha256 or row.launch_sha256 != launch_sha256:
                raise LaunchAuthorityError('conflict')
            return row

    def queue(self, principal, run_id, launch_sha256, budget_attempt_id):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            if row.launch_sha256 != sha(launch_sha256):
                raise LaunchAuthorityError('conflict')
            if row.state != 'planned' or row.cancel_requested:
                return row, False
            req = row.request
            identity = row.frozen['identity']
            _verify_current_preparation(conn, req, identity)
            if row.source_run_id is None:
                other = conn.execute(
                    'SELECT run_id FROM mf_native_launch.plans WHERE principal=%s AND preparation_id=%s '
                    'AND source_run_id IS NULL AND dispatch_claimed',
                    (principal, row.preparation_id),
                ).fetchone()
            else:
                _verify_checkpoint_source(conn, req, identity)
                other = conn.execute(
                    'SELECT run_id FROM mf_native_launch.plans WHERE source_run_id=%s AND dispatch_claimed',
                    (row.source_run_id,),
                ).fetchone()
            if other is not None:
                raise LaunchAuthorityError('conflict')
            conn.execute("UPDATE mf_native_launch.plans SET state='queued',dispatch_claimed=true,budget_attempt_id=%s,updated_at=now() WHERE run_id=%s", (identifier(budget_attempt_id),row.run_id))
            return self._get(conn, principal, run_id), True

    def workflow(self, principal, run_id, launch_sha256, value):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            if row.launch_sha256 != sha(launch_sha256) or row.state == 'planned' or row.workflow is not None and row.workflow != value:
                raise LaunchAuthorityError('conflict')
            conn.execute('UPDATE mf_native_launch.plans SET workflow=%s,updated_at=now() WHERE run_id=%s', (Jsonb(value),row.run_id))
            return self._get(conn, principal, run_id)

    def scheduling_uncertain(self, principal, run_id, launch_sha256):
        with transaction(self._connect) as conn:
            row = self._get(conn,principal,run_id,True)
            if row.launch_sha256 != sha(launch_sha256) or row.budget_attempt_id is None:
                raise LaunchAuthorityError('conflict')
            if row.state == 'queued':
                conn.execute("UPDATE mf_native_launch.plans SET state='uncertain',error_code='native_launch_uncertain',updated_at=now() WHERE run_id=%s", (row.run_id,))
            return self._get(conn,principal,run_id)

    def cancel(self, principal, run_id, launch_sha256):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            if row.launch_sha256 != sha(launch_sha256):
                raise LaunchAuthorityError('conflict')
            conn.execute("UPDATE mf_native_launch.plans SET cancel_requested=true,state=CASE WHEN state='planned' THEN 'cancelled' ELSE state END,updated_at=now() WHERE run_id=%s", (row.run_id,))
            return self._get(conn, principal, run_id)

    def close_undispatched(self, principal, run_id, launch_sha256):
        """Permanent row-lock proof BEFORE releasing a shared reservation.

        A stale read of planned is insufficient. This transition serializes
        with queue and makes that same review permanently ineligible to queue.
        The returned boolean is true only for an authoritative closed,
        unclaimed row; queued/lost-ack/uncertain rows are never release proof.
        """
        with transaction(self._connect) as conn:
            row=self._get(conn,principal,run_id,True)
            if row.launch_sha256!=sha(launch_sha256):
                raise LaunchAuthorityError('conflict')
            if row.dispatch_claimed or row.budget_attempt_id is not None:
                return row,False
            if row.state=='planned':
                conn.execute("UPDATE mf_native_launch.plans SET state='cancelled',cancel_requested=true,updated_at=now() WHERE run_id=%s",(row.run_id,))
                row=self._get(conn,principal,run_id)
            return row,(row.state=='cancelled' and row.cancel_requested and not row.dispatch_claimed
                        and row.budget_attempt_id is None)

    def observe(self, principal, run_id, launch_sha256, native_record):
        if not isinstance(native_record, NativeRunRecord):
            raise LaunchAuthorityError('native_launch_uncertain')
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            if row.launch_sha256 != sha(launch_sha256) or row.request != native_record.request or not row.dispatch_claimed:
                raise LaunchAuthorityError('conflict')
            state = native_record.state.value
            if state == 'declared':
                return row
            receipt = native_record.receipt.to_wire() if native_record.receipt is not None else None
            conn.execute('UPDATE mf_native_launch.plans SET state=%s,receipt=%s,cancel_requested=cancel_requested OR %s,error_code=%s,updated_at=now() WHERE run_id=%s',
                (state,Jsonb(receipt) if receipt else None,native_record.cancel_requested,
                 'native_launch_uncertain' if state=='uncertain' else None,row.run_id))
            return self._get(conn, principal, run_id)
