"""Actual PG journal/migration/race qualification, lazy explicit disposable opt-in.

The seeded completed native journal below tests report-store SQL authority only;
it is not an OASIS/native receipt qualification. Main owns the actual combined
source/preparation/native/report body in test_connected_native_report.py.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import os
from uuid import UUID, uuid4
import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture(scope='module')
def factory():
    if os.environ.get('PROJECT_STORE_POSTGRES_INTEGRATION') != '1':
        pytest.fail('selected report store requires the guarded disposable PostgreSQL')
    from test_native_launch_store import factory as accepted_factory
    return accepted_factory.__wrapped__()


def journal_fixture(factory, tmp_path, *, cap=20):
    from test_native_launch_store import ready_host, launch_host, declaration
    from test_connected_report_client import fixture_context
    from mirofish_execution.report_store import ReportStore, migrate
    from mirofish_execution.report_contracts import DEFAULT_LIMITS, digest
    from psycopg.types.json import Jsonb
    prep, scope, ready = ready_host(factory, tmp_path, cap=cap)
    launch = launch_host(prep, factory)
    declared = launch.plan(declaration(ready))
    native_row = launch.store.get('owner', declared['request']['run_id'])
    context = fixture_context()
    binding = dict(display_graph_id=prep.display_graph_id, principal='owner', scope=scope.model_dump(mode='json'),
        project_revision=native_row.project_revision, source=prep.store.get('owner', ready['operation_id']).frozen['public']['source'],
        preparation=declared['preparation'], native=dict(run_id=str(native_row.run_id), launch_sha256=native_row.launch_sha256,
            request_fingerprint=native_row.request.fingerprint, evidence_sha256=digest(context['native_manifest']), platforms=['twitter','reddit']),
        coverage=context['binding']['coverage'], reference_keys=context['binding']['reference_keys'])
    context['binding'] = binding
    context['graph']['graph_id'] = prep.display_graph_id
    # No claim that this seeded journal represents execution or output files.
    receipt = dict(run_id=str(native_row.run_id), attempt_id=str(uuid4()), instance_id=str(uuid4()),
        request_fingerprint=native_row.request.fingerprint, outcome='completed', evidence_sha256=binding['native']['evidence_sha256'])
    with factory() as conn:
        migrate(conn)
        conn.execute("UPDATE mf_native_launch.plans SET state='completed',dispatch_claimed=true,budget_attempt_id=%s,receipt=%s WHERE run_id=%s",
                     (uuid4(), Jsonb(receipt), native_row.run_id))
    store = ReportStore(factory)
    def review():
        payload = dict(schema_version=1, report_id=str(uuid4()), launch_id=str(native_row.run_id),
            launch_sha256=native_row.launch_sha256, requirement='Journal test only', output_language='en', native_windows=None)
        identity = dict(schema_version=1, report_id=payload['report_id'], binding=deepcopy(binding),
            options={k: payload[k] for k in ('requirement','output_language','native_windows')}, context_sha256=digest(context),
            source_projection_sha256=digest(context['graph']), model_label='journal-only', limits=dict(DEFAULT_LIMITS), ceiling_microusd=4)
        return store.put('owner', payload, identity, context, dict(account_id=str(prep.account_id), factory_sha256='f'*64))
    return store, prep, scope, review


def wire(row):
    return dict(schema_version=1, report_id=str(row.report_id), plan_sha256=row.plan_sha256, attempt_id=str(row.attempt_id))


def test_actual_planned_cancel_has_safe_code_without_dispatch_or_budget(factory, tmp_path):
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    planned = review()
    budget_before = prep.budget.status('owner', prep.account_id)
    cancelled = store.cancel('owner', planned.report_id, planned.plan_sha256)
    assert cancelled.state == 'cancelled' and cancelled.error_code == 'report_cancelled'
    assert cancelled.cancel_requested is True
    assert cancelled.dispatch_claimed is False and cancelled.owner_claimed is False
    assert cancelled.first_possible_request is False and cancelled.attempt_id is None
    assert cancelled.receipt is None and cancelled.manifest is None and cancelled.workflow is None
    assert cancelled.cleanup == dict(known=True, pending=False, owner_thread_alive=False)
    assert cancelled.frozen == planned.frozen and cancelled.plan_sha256 == planned.plan_sha256
    assert cancelled.public()['error_code'] == 'report_cancelled'
    assert store.cancel('owner', planned.report_id, planned.plan_sha256) == cancelled
    recovered, dispatched = store.queue('owner', planned.report_id, planned.plan_sha256, scope, prep.account_id)
    assert dispatched is False and recovered == cancelled
    assert prep.budget.status('owner', prep.account_id) == budget_before
    with factory() as conn:
        assert prep.budget._row(conn, prep.account_id, planned.report_id) is None


def test_real_pg_same_id_race_and_lost_reply_one_dispatch(factory, tmp_path):
    from mirofish_execution.report_store import ReportStore
    from mirofish_execution.report_contracts import ReportError
    from mirofish_execution.budget import ReservationState
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    row = review()
    def queue(_):
        return store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(queue, range(2)))
    assert sum(won for _, won in replies) == 1
    queued = ReportStore(factory).get('owner', row.report_id)
    assert queued.dispatch_claimed and queued.state == 'queued'
    claimed = store.claim('owner', wire(queued))
    with pytest.raises(ReportError):
        store.claim('owner', wire(claimed))
    with pytest.raises(ReportError):
        store.get('other', row.report_id)
    with factory() as conn:
        reservation = prep.budget._row(conn, prep.account_id, row.report_id)
    assert reservation.state == ReservationState.started and reservation.attempt_id == claimed.attempt_id
    uncertain = store.finish('owner', wire(claimed), state='uncertain', error_code='report_uncertain',
        cleanup=dict(known=False, pending=None, owner_thread_alive=None))
    assert uncertain.state == 'uncertain'
    assert store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)[1] is False
    with factory() as conn:
        assert prep.budget._row(conn, prep.account_id, row.report_id).state == ReservationState.uncertain


def test_distinct_reports_share_existing_capacity_lock(factory, tmp_path):
    from mirofish_execution.report_contracts import ReportError
    store, prep, scope, review = journal_fixture(factory, tmp_path, cap=9)
    rows = [review(), review()]
    def queue(row):
        try:
            return store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)[1]
        except ReportError as error:
            assert error.code == 'budget_denied'; return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        wins = list(pool.map(queue, rows))
    assert wins.count(True) == 1
    status = prep.budget.status('owner', prep.account_id)
    assert status.remaining_microusd == 1 and status.reserved_microusd == 4


@pytest.mark.parametrize('first_possible', [False, True])
def test_known_cleanup_no_call_release_or_unknown_spend_hold(factory, tmp_path, first_possible):
    from mirofish_execution.budget import ReservationState
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    row = review()
    row, _ = store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)
    row = store.claim('owner', wire(row))
    if first_possible:
        store.first_request('owner', wire(row))
    failed = store.finish('owner', wire(row), state='failed', error_code='report_failed',
        cleanup=dict(known=True, pending=False, owner_thread_alive=False))
    assert failed.state == 'failed' and failed.receipt is None and failed.manifest is None
    with factory() as conn:
        reservation = prep.budget._row(conn, prep.account_id, row.report_id)
    assert reservation.state == (ReservationState.uncertain if first_possible else ReservationState.released)


def test_restart_expiry_is_uncertain_and_never_resumes_partial_work(factory, tmp_path):
    from mirofish_execution.report_contracts import ReportError
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    row=review();row,_=store.queue('owner',row.report_id,row.plan_sha256,scope,prep.account_id)
    store.claim('owner',wire(row))
    with factory() as conn:
        conn.execute("UPDATE mf_report.plans SET owner_deadline=now()-interval '1 second' WHERE report_id=%s",(row.report_id,))
    expired=store.recover_expired('owner',row.report_id,row.plan_sha256)
    assert expired.state=='uncertain' and expired.cleanup==dict(known=False,pending=None,owner_thread_alive=None)
    with pytest.raises(ReportError):store.claim('owner',wire(row))


def test_corrupt_immutable_context_is_not_recovered_as_valid(factory,tmp_path):
    from mirofish_execution.report_contracts import ReportError
    store,prep,scope,review=journal_fixture(factory,tmp_path);row=review()
    with factory() as conn:
        conn.execute("UPDATE mf_report.plans SET frozen=jsonb_set(frozen,'{context,source_text}','\"changed\"'::jsonb) WHERE report_id=%s",(row.report_id,))
    with pytest.raises(ReportError) as error:store.get('owner',row.report_id)
    assert error.value.code=='report_uncertain'


def test_owned_catalog_drift_and_checksum_refused_without_changing_accepted_schema(factory):
    from mirofish_execution.report_store import migrate, _catalog
    from mirofish_execution.report_contracts import ReportError
    with factory() as conn:
        migrate(conn);before=_catalog(conn)
        with conn.transaction(force_rollback=True):
            conn.execute('ALTER TABLE mf_report.plans ADD COLUMN foreign_field text')
            with pytest.raises(ReportError):migrate(conn)
        assert _catalog(conn)==before
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE mf_report.schema_migrations SET checksum=repeat('0',64)")
            with pytest.raises(ReportError):migrate(conn)
        migrate(conn)


def completed_artifact_proof(row, tmp_path):
    """Actual file/hash proof for PG settlement; not an inherited-agent claim."""
    from mirofish_execution.report_contracts import encoded, digest
    from app.services.report_process import output_manifest, report_files
    from app.services.connected_report_context import validate_references
    context, identity = row.frozen['context'], row.frozen['identity']
    root = tmp_path / 'report-artifact-proof'; root.mkdir()
    prose = 'Source [[' + context['binding']['reference_keys'][0] + ']]. Native [[' + context['binding']['reference_keys'][-1] + ']].'
    contents = {'meta.json': encoded(dict(report_id=str(row.report_id), status='completed')),
        'outline.json': encoded(dict(title='Journal artifact proof', sections=[dict(title='Recorded evidence')])),
        'full_report.md': prose.encode('utf-8'), 'retrieval_evidence.json': encoded(dict(schema_version=1, fixture='journal-only')),
        'native_evidence.json': encoded(dict(schema_version=1, context_sha256=identity['context_sha256'],
            native=identity['binding']['native'], coverage=identity['binding']['coverage'])),
        'section_01.md': prose.encode('utf-8')}
    for name, raw in contents.items():
        (root / name).write_bytes(raw)
    manifest = output_manifest(root, 1)
    receipt = dict(schema_version=1, report_id=str(row.report_id), plan_sha256=row.plan_sha256,
        context_sha256=identity['context_sha256'], manifest_sha256=digest(manifest),
        output_language=identity['options']['output_language'], reference_integrity='validated', semantic_support_status='not_reviewed')
    def verify():
        with report_files(root, manifest) as files:
            validate_references(files['full_report.md'].decode('utf-8'), context)
    return root, manifest, receipt, verify


def test_actual_completed_report_settles_typed_proof_on_same_twelve_cap(factory, tmp_path):
    from mirofish_execution.report_contracts import ReportBudgetReceipt, budget_episode, budget_fingerprint, digest, ReportError
    from mirofish_execution.budget import ReservationState
    store, prep, scope, review = journal_fixture(factory, tmp_path, cap=12)
    sibling = prep.budget.reserve_prepared('owner', prep.account_id, scope, uuid4(), 'a'*64, 4)
    row = review(); row, won = store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)
    assert won and prep.budget.status('owner', prep.account_id).remaining_microusd == 0
    row = store.claim('owner', wire(row)); store.first_request('owner', wire(row))
    root, manifest, receipt, verify = completed_artifact_proof(row, tmp_path)
    completed = store.finish('owner', wire(row), state='completed', cleanup=dict(known=True, pending=False, owner_thread_alive=False),
        receipt=receipt, manifest=manifest, verify_output=verify)
    with factory() as conn:
        saved = prep.budget._row(conn, prep.account_id, row.report_id)
    assert saved.state == ReservationState.settled and type(saved.receipt) is ReportBudgetReceipt
    assert saved.receipt.operation_id == row.report_id and saved.receipt.attempt_id == row.attempt_id
    assert saved.receipt.fingerprint == budget_fingerprint(row.plan_sha256)
    assert saved.receipt.plan_sha256 == completed.plan_sha256
    assert saved.episode_id == budget_episode(scope.group_id, row.report_id) and saved.evidence_ids == ()
    assert saved.receipt.report_receipt == completed.receipt == receipt
    assert saved.receipt.report_receipt['context_sha256'] == row.frozen['identity']['context_sha256']
    assert saved.receipt.report_receipt['manifest_sha256'] == digest(completed.manifest)
    assert saved.receipt.report_receipt_sha256 == completed.public()['receipt_sha256'] == digest(receipt)
    assert ReportBudgetReceipt.from_wire(saved.receipt.json_value()) == saved.receipt
    detached = saved.receipt.report_receipt; detached['context_sha256'] = 'f'*64
    assert saved.receipt.report_receipt == receipt
    status = prep.budget.status('owner', prep.account_id)
    assert status.cap_microusd == 12 and status.accounted_ceiling_microusd == 8
    assert status.reserved_microusd == 4 and status.remaining_microusd == 0 and status.actual_usage_microusd is None
    assert store.finish('owner', wire(row), state='completed', cleanup=completed.cleanup,
        receipt=receipt, manifest=manifest, verify_output=verify) == completed
    assert prep.budget.status('owner', prep.account_id) == status
    another = review()
    with pytest.raises(ReportError) as denied:
        store.queue('owner', another.report_id, another.plan_sha256, scope, prep.account_id)
    assert denied.value.code == 'budget_denied'
    with factory() as conn:
        assert prep.budget._row(conn, prep.account_id, another.report_id) is None
        assert prep.budget._row(conn, prep.account_id, sibling.operation_id) == sibling


@pytest.mark.parametrize('fault', ['receipt-context', 'receipt-manifest', 'wrong-attempt', 'file-proof'],
                         ids=['context', 'manifest', 'attempt', 'file'])
def test_invalid_report_settlement_proof_rolls_back_journal_and_budget(factory, tmp_path, fault):
    from mirofish_execution.report_contracts import ReportError
    from mirofish_execution.budget import ReservationState
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    row = review(); row, _ = store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)
    row = store.claim('owner', wire(row)); store.first_request('owner', wire(row))
    before = store.get('owner', row.report_id)
    with factory() as conn:
        before_budget = prep.budget._row(conn, prep.account_id, row.report_id)
    root, manifest, receipt, verify = completed_artifact_proof(row, tmp_path)
    dispatch = wire(row)
    if fault == 'receipt-context': receipt['context_sha256'] = 'f'*64
    if fault == 'receipt-manifest': receipt['manifest_sha256'] = 'e'*64
    if fault == 'wrong-attempt': dispatch['attempt_id'] = str(uuid4())
    if fault == 'file-proof': (root / 'full_report.md').write_bytes(b'changed after manifest')
    with pytest.raises(ReportError):
        store.finish('owner', dispatch, state='completed', cleanup=dict(known=True, pending=False, owner_thread_alive=False),
            receipt=receipt, manifest=manifest, verify_output=verify)
    after = store.get('owner', row.report_id)
    assert after == before and after.state == 'generating' and after.receipt is None and after.manifest is None
    with factory() as conn:
        after_budget = prep.budget._row(conn, prep.account_id, row.report_id)
    assert after_budget == before_budget and after_budget.state == ReservationState.started and after_budget.receipt is None


def test_existing_settlement_apis_cannot_inject_other_domains_into_report_reservation(factory, tmp_path):
    from dataclasses import replace
    from mirofish_execution.budget import BudgetUncertain, BudgetConflict, BudgetDenied
    from mirofish_knowledge.operations import CompletionReceipt
    from mirofish_execution.native_launch_store import NativeLaunchStore
    from mirofish_execution.native_run_contracts import NativeRunReceipt
    from mirofish_execution.report_contracts import budget_fingerprint
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    row = review(); row, _ = store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)
    with factory() as conn:
        before = prep.budget._row(conn, prep.account_id, row.report_id)
    fingerprint = budget_fingerprint(row.plan_sha256)
    source_receipt = CompletionReceipt(scope.group_id, scope.episode_uuid(row.report_id), fingerprint, ())
    with pytest.raises(BudgetUncertain):
        prep.budget.settle('owner', prep.account_id, scope, row.report_id, row.attempt_id, fingerprint, (), source_receipt)
    with pytest.raises(BudgetUncertain):
        prep.budget.settle_prepared('owner', prep.account_id, scope, row.report_id, row.attempt_id, row.plan_sha256, 'b'*64)
    with pytest.raises(BudgetConflict):
        prep.budget.reserve('owner', prep.account_id, scope, row.report_id, fingerprint, 4, ())
    with pytest.raises(BudgetConflict):
        prep.budget.reserve_prepared('owner', prep.account_id, scope, row.report_id, row.plan_sha256, 4)
    native_row = NativeLaunchStore(factory).get('owner', row.frozen['identity']['binding']['native']['run_id'])
    request = replace(native_row.request, run_id=row.report_id)
    with pytest.raises(BudgetConflict):
        prep.budget.reserve_native('owner', prep.account_id, scope, request, native_row.launch_sha256, 4)
    with pytest.raises(BudgetDenied):
        prep.budget.native_reservation('owner', prep.account_id, request, native_row.launch_sha256)
    native_receipt = NativeRunReceipt.from_wire(dict(run_id=str(row.report_id), attempt_id=str(row.attempt_id),
        instance_id=str(uuid4()), request_fingerprint=request.fingerprint, outcome='completed', evidence_sha256='c'*64))
    with pytest.raises(BudgetUncertain):
        prep.budget.settle_native('owner', prep.account_id, scope, request, row.attempt_id, native_row.launch_sha256, native_receipt)
    assert store.get('owner', row.report_id) == row
    with factory() as conn:
        assert prep.budget._row(conn, prep.account_id, row.report_id) == before


@pytest.mark.parametrize('kind', ['source', 'preparation', 'native'], ids=['source', 'preparation', 'native'])
def test_actual_report_episode_refuses_injected_foreign_receipt_with_rollback(factory, tmp_path, kind):
    from psycopg.types.json import Jsonb
    from mirofish_execution.budget import BudgetUncertain
    from mirofish_execution.report_contracts import budget_fingerprint
    store, prep, scope, review = journal_fixture(factory, tmp_path)
    row = review(); row, _ = store.queue('owner', row.report_id, row.plan_sha256, scope, prep.account_id)
    with factory() as conn:
        before = prep.budget._row(conn, prep.account_id, row.report_id)
        receipts = dict(source=dict(group_id=scope.group_id, episode_id=str(before.episode_id), fingerprint=before.fingerprint, evidence_ids=[]),
            preparation=dict(kind='prepared_budget_v1', operation_id=str(row.report_id), attempt_id=str(row.attempt_id),
                fingerprint=before.fingerprint, artifact_sha256='a'*64),
            native=dict(kind='native_run_budget_v1', operation_id=str(row.report_id), attempt_id=str(row.attempt_id),
                fingerprint=before.fingerprint, launch_sha256='b'*64, native_receipt={}))
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE mf_execution.reservations SET state='settled',receipt=%s WHERE account_id=%s AND operation_id=%s",
                (Jsonb(receipts[kind]), prep.account_id, row.report_id))
            with pytest.raises(BudgetUncertain):
                prep.budget._row(conn, prep.account_id, row.report_id)
        assert prep.budget._row(conn, prep.account_id, row.report_id) == before
    assert store.get('owner', row.report_id) == row


def rollback_snapshot(conn):
    """Small witnesses for retained rows and every accepted execution catalog."""
    from psycopg import sql
    from mirofish_execution.report_store import _catalog as report_catalog
    from mirofish_execution.preparation_store import _catalog as preparation_catalog
    from mirofish_execution.native_run_store import _catalog as native_catalog
    from mirofish_execution.native_launch_store import _catalog as launch_catalog
    from mirofish_execution.budget import _catalog as budget_catalog
    catalogs = dict(mf_preparation=preparation_catalog, mf_native_execution=native_catalog,
                    mf_native_launch=launch_catalog, mf_execution=budget_catalog)
    accepted = {}
    for namespace, catalog in catalogs.items():
        tables = conn.execute("SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r' ORDER BY c.relname", (namespace,)).fetchall()
        counts = [(name, conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(namespace, name))).fetchone()[0])
                  for (name,) in tables]
        accepted[namespace] = (catalog(conn), counts)
    present = conn.execute("SELECT to_regnamespace('mf_report') IS NOT NULL").fetchone()[0]
    report = None
    if present:
        report = dict(catalog=report_catalog(conn),
            migrations=conn.execute('SELECT version,checksum,schema_checksum FROM mf_report.schema_migrations ORDER BY version').fetchall(),
            rows=conn.execute('SELECT report_id,md5(row_to_json(p)::text) FROM mf_report.plans p ORDER BY report_id').fetchall())
    return dict(accepted=accepted, report=report)


def insert_rollback_state(conn, state):
    """Transaction-local journal state only; never a native/model execution claim."""
    from psycopg.types.json import Jsonb
    from test_connected_report_client import public_result, fixture_context, declaration
    from mirofish_execution.report_contracts import IDENTITY, digest
    from mirofish_execution.report_store import ReportStore
    public, _ = public_result()
    identity = {key: deepcopy(public[key]) for key in IDENTITY}
    report_id = uuid4()
    identity['report_id'] = str(report_id)
    payload = declaration(); payload['report_id'] = str(report_id)
    frozen = dict(identity=identity, declaration=payload, context=fixture_context(),
        configuration=dict(account_id=str(uuid4()), factory_sha256='f'*64))
    conn.execute("INSERT INTO mf_report.plans(report_id,principal,plan_sha256,declaration_sha256,frozen,state,attempt_id,progress,cleanup,dispatch_claimed,owner_claimed,error_code) VALUES(%s,'owner',%s,%s,%s,%s,%s,%s,%s,true,%s,%s)",
        (report_id, digest(identity), digest(payload), Jsonb(frozen), state, uuid4(),
         Jsonb(dict(stage=state, percent=0, completed_sections=0, total_sections=0)),
         Jsonb(dict(known=False, pending=None, owner_thread_alive=None)), state != 'queued',
         'report_uncertain' if state == 'uncertain' else None))
    assert ReportStore._get(conn, 'owner', report_id).state == state
    return report_id


@pytest.mark.parametrize('state', ['queued', 'generating', 'uncertain'], ids=['queued', 'generating', 'uncertain'])
def test_actual_rollback_refuses_each_active_state_and_preserves_catalog_rows(factory, state):
    from mirofish_execution.report_store import migrate, rollback
    from mirofish_execution.report_contracts import ReportError
    with factory() as conn:
        before = rollback_snapshot(conn)
        with conn.transaction(force_rollback=True):
            migrate(conn)
            # Isolate this guard witness from historical active rows, solely
            # inside the forced-rollback transaction and solely in mf_report.
            conn.execute("UPDATE mf_report.plans SET state='failed' WHERE state IN ('queued','generating','uncertain')")
            report_id = insert_rollback_state(conn, state)
            assert conn.execute("SELECT report_id,state FROM mf_report.plans WHERE state IN ('queued','generating','uncertain') ORDER BY report_id").fetchall() == [(report_id, state)]
            active = rollback_snapshot(conn)
            assert active['accepted'] == before['accepted']
            with pytest.raises(ReportError) as error:
                rollback(conn)
            assert error.value.code == 'busy'
            assert rollback_snapshot(conn) == active
        # Includes every pre-existing report row hash and migration/catalog
        # witness, not only the count of this transaction's synthetic record.
        assert rollback_snapshot(conn) == before


def test_actual_quiescent_rollback_up_roundtrip_removes_only_report_and_restores_history(factory):
    from mirofish_execution.report_store import migrate, rollback, _catalog
    with factory() as conn:
        before = rollback_snapshot(conn)
        with conn.transaction(force_rollback=True):
            migrate(conn)
            conn.execute("UPDATE mf_report.plans SET state='failed' WHERE state IN ('queued','generating','uncertain')")
            assert conn.execute("SELECT count(*) FROM mf_report.plans WHERE state IN ('queued','generating','uncertain')").fetchone()[0] == 0
            quiescent = rollback_snapshot(conn)
            assert quiescent['accepted'] == before['accepted']
            rollback(conn)
            assert conn.execute("SELECT to_regnamespace('mf_report'),to_regclass('mf_report.plans'),to_regclass('mf_report.schema_migrations')").fetchone() == (None, None, None)
            removed = rollback_snapshot(conn)
            assert removed['report'] is None and removed['accepted'] == before['accepted']
            migrate(conn)
            rebuilt = rollback_snapshot(conn)
            assert rebuilt['accepted'] == before['accepted']
            assert rebuilt['report']['catalog'] == quiescent['report']['catalog'] == _catalog(conn)
            assert rebuilt['report']['migrations'] == quiescent['report']['migrations']
            assert rebuilt['report']['rows'] == []
        assert rollback_snapshot(conn) == before
