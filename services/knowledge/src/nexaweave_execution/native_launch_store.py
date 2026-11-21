"""Explicit isolated PG launch schema; no accepted catalog is modified."""
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from importlib.resources import files
import hashlib
import json
import psycopg
from psycopg.types.json import Jsonb
from nexaweave_storage.transaction_settings import apply_runtime_settings
from .native_launch_contracts import (LaunchAuthorityError, validate_identity, validate_declaration,
    digest, identifier, sha, verify_fork_archive, native_budget_episode, native_budget_fingerprint)
from .native_run_contracts import NativeRunRequest, NativeRunReceipt, InvalidNativeRun, NativeRunUncertain
from .native_run_store import (NativeRunRecord, _catalog_v3_receipt, _lock_checkpoint_run_id,
    _SELECT as _NATIVE_COLUMNS, _row as _native_record)
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
        sql_v3 = directory.joinpath('native_launch_0003.sql').read_text('utf-8')
        sql_v4 = directory.joinpath('native_launch_0004.sql').read_text('utf-8')
        checksum_v1 = hashlib.sha256(sql_v1.encode()).hexdigest()
        checksum_v2 = hashlib.sha256(sql_v2.encode()).hexdigest()
        checksum_v3 = hashlib.sha256(sql_v3.encode()).hexdigest()
        checksum_v4 = hashlib.sha256(sql_v4.encode()).hexdigest()
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
            if versions not in ([1], [1, 2], [1, 2, 3], [1, 2, 3, 4]) or rows[0][1] != checksum_v1:
                raise LaunchAuthorityError('native_launch_unavailable')
            previous = rows[0]
            if versions == [1]:
                if previous[2] != _catalog(connection):
                    raise LaunchAuthorityError('native_launch_unavailable')
                connection.execute(sql_v2)
                connection.execute(_SAVE_MIGRATION, (2, checksum_v2, _chained_catalog(connection, previous)))
                rows = connection.execute(_MIGRATIONS).fetchall()
            if rows[0] != previous or rows[1][:2] != (2, checksum_v2):
                raise LaunchAuthorityError('native_launch_unavailable')
            history = rows[:2]
            if len(rows) == 2:
                if history[1][2] != _chained_catalog(connection, previous):
                    raise LaunchAuthorityError('native_launch_unavailable')
                connection.execute(sql_v3)
                chain = _catalog_v3_receipt(connection, 'mf_native_launch', history, _catalog)
                connection.execute(_SAVE_MIGRATION, (3, checksum_v3, chain))
                rows = connection.execute(_MIGRATIONS).fetchall()
            if rows[:2] != history or rows[2][:2] != (3, checksum_v3):
                raise LaunchAuthorityError('native_launch_unavailable')
            history_v3 = rows[:3]
            if len(rows) == 3:
                if rows[2][2] != _catalog_v3_receipt(connection, 'mf_native_launch', history, _catalog):
                    raise LaunchAuthorityError('native_launch_unavailable')
                connection.execute(sql_v4)
                chain = _catalog_v3_receipt(connection, 'mf_native_launch', history_v3, _catalog)
                connection.execute(_SAVE_MIGRATION, (4, checksum_v4, chain))
                rows = connection.execute(_MIGRATIONS).fetchall()
            chain = _catalog_v3_receipt(connection, 'mf_native_launch', history_v3, _catalog)
            if rows != [*history_v3, (4, checksum_v4, chain)]:
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
    intervention: dict | None = None

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
                or result.source_run_id != req.source_run_id
                or result.intervention != (None if req.intervention is None else req.intervention.to_wire())):
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


def _seed_member(conn, preparation_id):
    if not conn.execute("SELECT to_regclass('mf_preparation.seed_ensemble_members') IS NOT NULL").fetchone()[0]:
        return None
    return conn.execute(
        'SELECT m.ensemble_id,m.ordinal,m.launch_id,m.seed,e.account_id '
        'FROM mf_preparation.seed_ensemble_members m '
        'JOIN mf_preparation.plans p ON p.operation_id=m.operation_id AND p.seed_ensemble_id=m.ensemble_id '
        'JOIN mf_preparation.seed_ensembles e ON e.ensemble_id=m.ensemble_id AND e.principal=p.principal '
        'WHERE m.operation_id=%s', (preparation_id,),
    ).fetchone()


def _verify_current_preparation(conn, request, identity):
    descriptor = identity['preparation']
    member = _seed_member(conn, identifier(descriptor['operation_id']))
    reddit = identity['schema_version'] == 4
    if request.intervention is not None or member is not None or reddit:
        from nexaweave_knowledge.bindings import ScopeBindingStore
        from nexaweave_knowledge.contracts import KnowledgeScope
        from nexaweave_knowledge.operations import LedgerError

        # Match guarded project writes: scope/binding before project, then keep
        # the established project/preparation/producer launch/native order.
        scope = KnowledgeScope.model_validate_json(json.dumps(identity['scope']))
        try:
            ScopeBindingStore(lambda: nullcontext(conn)).resolve_for_write(
                conn, request.principal, identity['display_graph_id'], scope,
            )
        except (LedgerError, ValueError):
            raise LaunchAuthorityError('conflict') from None
    project = conn.execute(
        'SELECT current_revision,workspace_id FROM mf_app.projects WHERE principal=%s AND project_id=%s FOR UPDATE',
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
    if member is not None and (identity['schema_version'] != 1 or request.run_id != member[2]
                               or request.seed != member[3] or request.source_run_id is not None
                               or 'checkpoint' in identity):
        raise LaunchAuthorityError('conflict')
    if identity['schema_version'] >= 2 or member is not None:
        options = prep[7]
        if (prep[3] != request.project_id or prep[4] != request.project_revision
                or prep[5] != identity['display_graph_id'] or prep[6] != identity['scope']
                or prep[2].get('simulation_id') != request.simulation_id
                or type(options) is not dict or options.get('platforms') != list(request.platforms)
                or type(options.get('seed')) is not int or options['seed'] != request.seed
                or type(options.get('max_rounds')) is not int or options['max_rounds'] != request.max_rounds):
            raise LaunchAuthorityError('conflict')
    if reddit != (type(prep[7]) is dict and 'reddit_checkpoint' in prep[7]):
        raise LaunchAuthorityError('conflict')
    if ((request.intervention is not None or member is not None or reddit)
            and project[1] != identifier(identity['scope']['workspace_id'])):
        raise LaunchAuthorityError('conflict')
    return member


def _verify_checkpoint_source(conn, request, identity, configuration=None):
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
                       source_run_id=None, checkpoint_sha256=None, intervention=None)
    reddit = identity['schema_version'] == 4
    if (source_identity['schema_version'] != (4 if reddit else 2)
            or source_identity.get('checkpoint') != {'mode': 'capture'}
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
    if (request.intervention is not None or reddit) and (
            native.child is None or native.child.instance_id != receipt.instance_id
            or source_identity['display_graph_id'] != identity['display_graph_id']
            or source.frozen['configuration'] != configuration
            or any(source_identity[key] != identity[key] for key in ('limits', 'ceiling_microusd', 'model_label'))):
        raise LaunchAuthorityError('conflict')
    return native


def verify_checkpoint_dispatch(conn, request):
    """Verify the queued authority and exact started budget before a protected checkpoint claim."""
    from nexaweave_knowledge.contracts import KnowledgeScope

    row = NativeLaunchStore._get(conn, request.principal, request.run_id, True)
    if (row.request != request or not row.dispatch_claimed or row.cancel_requested
            or row.state not in {'queued', 'starting', 'running'}):
        raise LaunchAuthorityError('conflict')
    identity, configuration = row.frozen['identity'], row.frozen['configuration']
    if request.intervention is None and identity['schema_version'] != 4:
        raise LaunchAuthorityError('conflict')
    _verify_current_preparation(conn, request, identity)
    if request.source_run_id is not None:
        _verify_checkpoint_source(conn, request, identity, configuration)
    scope = KnowledgeScope.model_validate_json(json.dumps(identity['scope']))
    budget = conn.execute(
        'SELECT r.attempt_id,r.fingerprint,r.scope_group_id,r.episode_id,r.ceiling_microusd,r.evidence_ids '
        'FROM mf_execution.reservations r JOIN mf_execution.accounts a USING (account_id) '
        'WHERE r.account_id=%s AND r.operation_id=%s AND a.principal=%s AND a.project_id=%s '
        "AND r.state='started' FOR SHARE OF r",
        (identifier(configuration['account_id']), request.run_id, request.principal, request.project_id),
    ).fetchone()
    if budget != (row.budget_attempt_id, native_budget_fingerprint(row.launch_sha256),
                  scope.group_id, native_budget_episode(scope.group_id, request.run_id),
                  int(identity['ceiling_microusd']), []):
        raise LaunchAuthorityError('conflict')


def _reject_occupied_checkpoint_run_id(conn, run_id):
    _lock_checkpoint_run_id(conn, run_id)
    if conn.execute('SELECT 1 FROM mf_native_execution.runs WHERE run_id=%s', (run_id,)).fetchone() is not None:
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

    def seed_member(self, preparation_id):
        with transaction(self._connect) as conn:
            return _seed_member(conn, identifier(preparation_id))

    def put(self, principal, declaration, identity, *, configuration=None, fork_archive=None):
        identity = validate_identity(identity)
        declaration = validate_declaration(declaration)
        try:
            _configuration(configuration)
            req = _coherent_request(declaration, identity)
        except (ValueError, TypeError, KeyError, InvalidNativeRun):
            raise LaunchAuthorityError('invalid_request') from None
        if principal != req.principal:
            raise LaunchAuthorityError('unauthorized')
        if req.intervention is None and fork_archive is not None:
            raise LaunchAuthorityError('invalid_request')
        frozen = json.loads(json.dumps({'declaration': declaration, 'identity': identity,
                                      'configuration': configuration}, ensure_ascii=True, allow_nan=False))
        request_sha256, launch_sha256 = digest(declaration), digest(identity)
        descriptor = identity['preparation']
        preparation_id = identifier(descriptor['operation_id'])
        version = declaration['schema_version']
        with transaction(self._connect) as conn:
            seed_member = _seed_member(conn, preparation_id)
            if seed_member is not None and configuration['account_id'] != str(seed_member[4]):
                raise LaunchAuthorityError('conflict')
            if version == 1 and seed_member is None:
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
            if seed_member is not None or (version >= 2 and (prior is None or prior[2] == 'planned')):
                _verify_current_preparation(conn, req, identity)
                # A same-ID plan can appear while waiting for preparation ownership.
                prior = conn.execute(read_prior, (req.run_id, principal)).fetchone()
            if prior is not None and prior[:2] != (request_sha256, launch_sha256):
                raise LaunchAuthorityError('conflict')
            if version >= 2:
                if req.source_run_id is not None and (prior is None or prior[2] == 'planned' or fork_archive is not None):
                    producer = _verify_checkpoint_source(conn, req, identity, configuration)
                    if req.intervention is not None and (prior is None or fork_archive is not None):
                        verify_fork_archive(fork_archive, req, identity['display_graph_id'], producer.child.process_id)
                if prior is not None:
                    saved = self._get(conn, principal, req.run_id)
                    if (req.intervention is not None or version == 4) and saved.frozen['configuration'] != configuration:
                        raise LaunchAuthorityError('conflict')
                    return saved
            if req.intervention is not None or version == 4:
                _reject_occupied_checkpoint_run_id(conn, req.run_id)
            count = conn.execute('SELECT count(*) FROM mf_native_launch.plans WHERE principal=%s AND preparation_id=%s',
                                 (principal, preparation_id)).fetchone()[0]
            if prior is None and count>=100:
                raise LaunchAuthorityError('busy')
            conn.execute(
                "INSERT INTO mf_native_launch.plans(run_id,principal,preparation_id,simulation_id,project_id,"
                "project_revision,request_sha256,launch_sha256,frozen,state,source_run_id,intervention) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'planned',%s,%s) ON CONFLICT DO NOTHING",
                (req.run_id, principal, preparation_id, req.simulation_id, req.project_id,
                 req.project_revision, request_sha256, launch_sha256, Jsonb(frozen), req.source_run_id,
                 None if req.intervention is None else Jsonb(req.intervention.to_wire())),
            )
            try:
                row = self._get(conn, principal, req.run_id)
            except LaunchAuthorityError:
                raise LaunchAuthorityError('conflict') from None
            if (row.request_sha256 != request_sha256 or row.launch_sha256 != launch_sha256
                    or (req.intervention is not None or version == 4) and row.frozen['configuration'] != configuration):
                raise LaunchAuthorityError('conflict')
            return row

    def queue_fork(self, principal, run_id, launch_sha256):
        """Reserve and claim the reviewed fork together, after current authority checks."""
        return self._queue_checkpoint(principal, run_id, launch_sha256, reddit=False)

    def queue_reddit(self, principal, run_id, launch_sha256):
        return self._queue_checkpoint(principal, run_id, launch_sha256, reddit=True)

    def _queue_checkpoint(self, principal, run_id, launch_sha256, *, reddit):
        from nexaweave_knowledge.contracts import KnowledgeScope
        from .budget import BudgetError, BudgetLedger, ReservationState

        try:
            run_id, launch_sha256 = identifier(run_id), sha(launch_sha256)
        except ValueError:
            raise LaunchAuthorityError('invalid_request') from None
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            req = row.request
            identity, configuration = row.frozen['identity'], row.frozen['configuration']
            expected_version = 4 if reddit else 3
            if row.launch_sha256 != launch_sha256 or identity['schema_version'] != expected_version:
                raise LaunchAuthorityError('conflict')
            if row.state != 'planned' or row.cancel_requested:
                return row, False
            _verify_current_preparation(conn, req, identity)
            if req.source_run_id is not None:
                _verify_checkpoint_source(conn, req, identity, configuration)
            _reject_occupied_checkpoint_run_id(conn, req.run_id)
            if reddit:
                if req.source_run_id is None:
                    occupied = conn.execute(
                        'SELECT 1 FROM mf_native_launch.plans WHERE principal=%s AND preparation_id=%s '
                        'AND source_run_id IS NULL AND dispatch_claimed', (principal, row.preparation_id),
                    ).fetchone()
                else:
                    occupied = conn.execute(
                        'SELECT 1 FROM mf_native_launch.plans WHERE source_run_id=%s '
                        'AND intervention IS NULL AND dispatch_claimed', (req.source_run_id,),
                    ).fetchone()
                if occupied is not None:
                    raise LaunchAuthorityError('conflict')
            if configuration['account_id'] is None or identity['ceiling_microusd'] is None:
                raise LaunchAuthorityError('model_calls_disabled')
            scope = KnowledgeScope.model_validate_json(json.dumps(identity['scope']))
            try:
                reservation = BudgetLedger(lambda: nullcontext(conn)).reserve_native(
                    principal, configuration['account_id'], scope, req,
                    row.launch_sha256, int(identity['ceiling_microusd']),
                )
            except BudgetError as error:
                raise LaunchAuthorityError('budget_denied' if error.code == 'budget_denied' else 'conflict') from None
            if reservation.state != ReservationState.reserved:
                raise LaunchAuthorityError('conflict')
            return self._claim_queue(conn, row, reservation.attempt_id), True

    def _claim_queue(self, conn, row, attempt_id):
        conn.execute("UPDATE mf_native_launch.plans SET state='queued',dispatch_claimed=true,budget_attempt_id=%s,updated_at=now() WHERE run_id=%s",
                     (identifier(attempt_id), row.run_id))
        return self._get(conn, row.principal, row.run_id)

    def queue(self, principal, run_id, launch_sha256, budget_attempt_id=None):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            if (row.launch_sha256 != sha(launch_sha256) or row.intervention is not None
                    or row.frozen['identity']['schema_version'] == 4):
                raise LaunchAuthorityError('conflict')
            if row.state != 'planned' or row.cancel_requested:
                return row, False
            req = row.request
            identity = row.frozen['identity']
            member = _verify_current_preparation(conn, req, identity)
            if row.source_run_id is None:
                other = conn.execute(
                    'SELECT run_id FROM mf_native_launch.plans WHERE principal=%s AND preparation_id=%s '
                    'AND source_run_id IS NULL AND dispatch_claimed',
                    (principal, row.preparation_id),
                ).fetchone()
            else:
                _verify_checkpoint_source(conn, req, identity)
                other = conn.execute(
                    'SELECT run_id FROM mf_native_launch.plans WHERE source_run_id=%s AND intervention IS NULL AND dispatch_claimed',
                    (row.source_run_id,),
                ).fetchone()
            if other is not None:
                raise LaunchAuthorityError('conflict')
            if member is not None:
                if budget_attempt_id is not None:
                    raise LaunchAuthorityError('conflict')
                queued = self._queue_seed_member(conn, row, member)
                if queued is not None:
                    return queued
            else:
                if budget_attempt_id is None:
                    raise LaunchAuthorityError('conflict')
                return self._claim_queue(conn, row, budget_attempt_id), True
        # The unclaimed refusal is committed before reporting it to the caller.
        raise LaunchAuthorityError('budget_denied')

    def _verify_seed_sequence(self, conn, row, member):
        from nexaweave_knowledge.contracts import KnowledgeScope
        from .budget import BudgetError, BudgetLedger, ReservationState
        from .native_launch_contracts import NativeBudgetReceipt

        identity = row.frozen['identity']
        configuration = row.frozen['configuration']
        account_id = configuration['account_id']
        if account_id is None or identity['ceiling_microusd'] is None:
            raise LaunchAuthorityError('model_calls_disabled')
        ensemble, ordinal, _, _, ensemble_account = member
        if account_id != str(ensemble_account):
            raise LaunchAuthorityError('conflict')
        siblings = conn.execute(
            'SELECT ordinal,operation_id,launch_id FROM mf_preparation.seed_ensemble_members '
            'WHERE ensemble_id=%s ORDER BY ordinal', (ensemble,),
        ).fetchall()
        if [sibling[0] for sibling in siblings] != [1, 2]:
            raise LaunchAuthorityError('conflict')
        _, preparation_id, launch_id = siblings[2 - ordinal]
        # Never wait on the other launch with project/preparation locks held.
        # Member one reads the review without taking member two's lock.
        lock = ' FOR SHARE NOWAIT' if ordinal == 2 else ''
        try:
            data = conn.execute(
                f'SELECT {_COLUMNS} FROM mf_native_launch.plans WHERE principal=%s AND run_id=%s' + lock,
                (row.principal, launch_id),
            ).fetchone()
            if data is None:
                raise LaunchAuthorityError('conflict')
            other = _record(data)
            if (other.preparation_id != preparation_id
                    or other.frozen['configuration'] != configuration
                    or other.frozen['identity']['scope'] != identity['scope']
                    or other.frozen['identity']['display_graph_id'] != identity['display_graph_id']
                    or other.request.project_id != row.request.project_id
                    or other.request.project_revision != row.request.project_revision
                    or other.cancel_requested):
                raise LaunchAuthorityError('conflict')
            if ordinal == 1:
                if other.state != 'planned' or other.dispatch_claimed:
                    raise LaunchAuthorityError('conflict')
                return
            if other.state in {'planned', 'queued', 'starting', 'running', 'uncertain'}:
                raise LaunchAuthorityError('busy')
            if other.state != 'completed' or not other.dispatch_claimed or other.receipt is None:
                raise LaunchAuthorityError('conflict')
            raw = conn.execute(
                f'SELECT {_NATIVE_COLUMNS} FROM mf_native_execution.runs '
                'WHERE principal=%s AND run_id=%s FOR SHARE NOWAIT', (row.principal, other.run_id),
            ).fetchone()
            if raw is None:
                raise LaunchAuthorityError('conflict')
            native = _native_record(raw)
            receipt = NativeRunReceipt.from_wire(other.receipt)
            if (receipt.outcome != 'completed' or receipt.run_id != other.run_id
                    or receipt.request_fingerprint != other.request.fingerprint
                    or native.state.value != 'completed' or native.cancel_requested
                    or native.request != other.request or native.attempt_id != receipt.attempt_id
                    or native.receipt != receipt or native.child is None
                    or native.child.instance_id != receipt.instance_id):
                raise LaunchAuthorityError('conflict')
            scope = KnowledgeScope.model_validate_json(json.dumps(identity['scope']))
            account = BudgetLedger._account(conn, row.principal, account_id, lock=True)
            budget = BudgetLedger._row(conn, account_id, other.run_id)
            if budget is None or budget.state != ReservationState.settled:
                raise LaunchAuthorityError('busy')
            expected = NativeBudgetReceipt(other.run_id, other.budget_attempt_id,
                                           native_budget_fingerprint(other.launch_sha256), other.launch_sha256, receipt)
            if (account[2] != row.request.project_id or budget.receipt != expected
                    or budget.attempt_id != other.budget_attempt_id or budget.fingerprint != expected.fingerprint
                    or budget.ceiling_microusd != int(other.frozen['identity']['ceiling_microusd'])
                    or budget.scope_group_id != scope.group_id or budget.evidence_ids
                    or budget.episode_id != native_budget_episode(scope.group_id, other.run_id)):
                raise LaunchAuthorityError('conflict')
        except psycopg.errors.LockNotAvailable:
            raise LaunchAuthorityError('busy') from None
        except (BudgetError, InvalidNativeRun, NativeRunUncertain, ValueError, TypeError, KeyError):
            raise LaunchAuthorityError('conflict') from None

    def _queue_seed_member(self, conn, row, member):
        from nexaweave_knowledge.contracts import KnowledgeScope
        from .budget import BudgetError, BudgetLedger, ReservationState

        self._verify_seed_sequence(conn, row, member)
        identity = row.frozen['identity']
        scope = KnowledgeScope.model_validate_json(json.dumps(identity['scope']))
        try:
            reservation = BudgetLedger(lambda: nullcontext(conn)).reserve_native(
                row.principal, row.frozen['configuration']['account_id'], scope, row.request,
                row.launch_sha256, int(identity['ceiling_microusd']),
            )
        except BudgetError as error:
            if error.code != 'budget_denied':
                raise LaunchAuthorityError('conflict') from None
            _, closed = self._close_undispatched(conn, row)
            if not closed:
                raise LaunchAuthorityError('native_launch_uncertain') from None
            return None
        if reservation.state != ReservationState.reserved:
            raise LaunchAuthorityError('conflict')
        return self._claim_queue(conn, row, reservation.attempt_id), True

    def verify_seed_dispatch(self, principal, run_id, launch_sha256):
        with transaction(self._connect) as conn:
            row = self._get(conn, principal, run_id, True)
            if row.launch_sha256 != launch_sha256 or not row.dispatch_claimed or row.cancel_requested:
                raise LaunchAuthorityError('conflict')
            member = _verify_current_preparation(conn, row.request, row.frozen['identity'])
            if member is None or row.frozen['configuration']['account_id'] != str(member[4]):
                raise LaunchAuthorityError('conflict')
            if member[1] == 2:
                self._verify_seed_sequence(conn, row, member)

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
            return self._close_undispatched(conn, row)

    def _close_undispatched(self, conn, row):
        if row.dispatch_claimed or row.budget_attempt_id is not None:
            return row, False
        if row.state == 'planned':
            conn.execute(
                "UPDATE mf_native_launch.plans SET state='cancelled',cancel_requested=true,updated_at=now() WHERE run_id=%s",
                (row.run_id,),
            )
            row = self._get(conn, row.principal, row.run_id)
        return row, (row.state == 'cancelled' and row.cancel_requested and not row.dispatch_claimed
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
            if (row.frozen['identity']['schema_version'] == 4 or row.intervention is not None
                    or _seed_member(conn, row.preparation_id) is not None):
                if row.receipt is not None:
                    if receipt is None:
                        return row
                    if state != row.state or receipt != row.receipt:
                        raise LaunchAuthorityError('conflict')
                    if row.cancel_requested or not native_record.cancel_requested:
                        return row
                if receipt is None and (row.state == 'uncertain'
                        or (row.state == 'running' and state == 'starting')):
                    return row
                authoritative = conn.execute(
                    f'SELECT {_NATIVE_COLUMNS} FROM mf_native_execution.runs '
                    'WHERE principal=%s AND run_id=%s FOR SHARE', (principal, row.run_id),
                ).fetchone()
                if authoritative is None:
                    raise LaunchAuthorityError('conflict')
                try:
                    current = _native_record(authoritative)
                except NativeRunUncertain:
                    raise LaunchAuthorityError('native_launch_uncertain') from None
                if (current.request, current.state, current.attempt_id, current.owner_id, current.child, current.receipt) != (
                        native_record.request, native_record.state, native_record.attempt_id,
                        native_record.owner_id, native_record.child, native_record.receipt):
                    return row
                native_record = current
                if row.state == state and (row.cancel_requested or not current.cancel_requested):
                    return row
            conn.execute('UPDATE mf_native_launch.plans SET state=%s,receipt=%s,cancel_requested=cancel_requested OR %s,error_code=%s,updated_at=now() WHERE run_id=%s',
                (state,Jsonb(receipt) if receipt else None,native_record.cancel_requested,
                 'native_launch_uncertain' if state=='uncertain' else None,row.run_id))
            return self._get(conn, principal, run_id)
