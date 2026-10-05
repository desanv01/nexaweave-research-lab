"""Installed-package authority on explicitly guarded disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
import threading
from uuid import uuid4
import pytest
from mirofish_execution.native_launch_store import NativeLaunchStore,migrate
from mirofish_execution.native_launch_contracts import LaunchAuthorityError
from mirofish_execution.native_run_store import migrate_native_runs
from mirofish_execution.budget import migrate as migrate_budget
from mirofish_execution.preparation_store import migrate as migrate_preparation
from test_source_bridge_postgres import factory as original_factory

pytestmark=pytest.mark.postgres


@pytest.fixture(scope='module')
def factory():
    if os.getenv('PROJECT_STORE_POSTGRES_INTEGRATION')!='1':
        pytest.fail('selected native launch case requires disposable PostgreSQL')
    return original_factory.__wrapped__()


def migrate_all(factory):
    with factory() as conn:
        migrate_budget(conn);migrate_preparation(conn);migrate_native_runs(conn);migrate(conn)


def ready_host(factory,tmp_path,*,cap=40):
    migrate_all(factory)
    from test_preparation_store import real_host,request,reference
    prep,scope,retained,chat,wires,_=real_host(factory,tmp_path,cap=cap)
    plan=prep.plan(request(retained));prep.start(reference(plan));prep.generate(wires[0])
    return prep,scope,plan


def launch_host(prep,factory,*,enabled=True):
    from app.services.durable_native_launch_host import DurableNativeLaunchHost
    from app.services.native_launch_models import BoundedNativeModelFactory
    from test_durable_native_launch import scripted_native_backends,LIMITS
    model=BoundedNativeModelFactory(('twitter','reddit'),dict(LIMITS),scripted_native_backends,cooperative_transport=True)
    return DurableNativeLaunchHost(preparation_host=prep,connection_factory=factory,runtime_sha256='c'*64,
        model_label='scripted',limits=LIMITS,model_factory=model,account_id=prep.account_id,
        ceiling_microusd=4 if enabled else None,authorize=lambda:enabled)


def declaration(plan):
    return {'schema_version':1,'launch_id':str(uuid4()),'preparation':{'operation_id':plan['operation_id'],'plan_sha256':plan['plan_sha256']}}


def test_reviews_are_immutable_bounded_and_do_not_register_native(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory,enabled=False)
    payload=declaration(plan);disabled=host.plan(payload)
    assert disabled['ceiling_microusd'] is None and not disabled['authorization']['model_calls_enabled']
    assert host.plan(payload)==disabled
    assert NativeLaunchStore(factory).get('owner',payload['launch_id']).state=='planned'
    with factory() as conn:
        assert conn.execute('SELECT count(*) FROM mf_native_execution.runs WHERE project_id=%s',(scope.project_id,)).fetchone()[0]==0
    host.ceiling=4
    new=host.plan(declaration(plan))
    assert new['request']['run_id']!=disabled['request']['run_id'] and new['ceiling_microusd']=='4'
    row=host.store.get('owner',new['request']['run_id'])
    wrong=deepcopy(row.frozen['identity']);wrong['model_label']='changed'
    with pytest.raises(LaunchAuthorityError):host.store.put('owner',row.frozen['declaration'],wrong)
    for _ in range(98):host.plan(declaration(plan))
    with pytest.raises(LaunchAuthorityError) as full:host.plan(declaration(plan))
    assert full.value.code=='busy'
    assert host.plan(payload)==disabled


def test_distinct_review_queue_race_has_one_permanent_claim(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory)
    plans=[host.plan(declaration(plan)) for _ in range(2)]
    def queue(dto):
        try:
            row=host.store.get('owner',dto['request']['run_id'])
            reservation=host.budget.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
            queued,won=host.store.queue('owner',row.run_id,row.launch_sha256,reservation.attempt_id)
            return queued if won else None
        except LaunchAuthorityError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(queue,plans))
    assert sum(row is not None for row in rows)==1
    winner=next(row for row in rows if row is not None)
    assert winner.dispatch_claimed and NativeLaunchStore(factory).get('owner',winner.run_id).dispatch_claimed
    host.store.scheduling_uncertain('owner',winner.run_id,winner.launch_sha256)
    alternate=host.store.get('owner',host.plan(declaration(plan))['request']['run_id'])
    with pytest.raises(LaunchAuthorityError):host.store.queue('owner',alternate.run_id,alternate.launch_sha256,uuid4())


def test_project_revision_and_ready_receipt_reauthorization_before_queue(factory,tmp_path):
    from mirofish_storage import ProjectStore
    from test_project_store import snapshot
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory);dto=host.plan(declaration(plan))
    row=host.store.get('owner',dto['request']['run_id'])
    changed=snapshot();changed['name']='new revision'
    ProjectStore(factory).update('owner',scope.project_id,1,changed)
    with pytest.raises(LaunchAuthorityError):host.store.queue('owner',row.run_id,row.launch_sha256,uuid4())
    assert not host.store.get('owner',row.run_id).dispatch_claimed
    root=prep.root/row.request.simulation_id
    assert not (root/'.native_prepared_start_claim').exists()


def test_own_migration_catalog_drift_and_previous_checksums(factory):
    migrate_all(factory)
    with factory() as conn:
        old=conn.execute('SELECT checksum FROM mf_execution.schema_migrations').fetchall()
        migrate(conn)
        with pytest.raises(LaunchAuthorityError):
            with conn.transaction():
                conn.execute('CREATE TABLE mf_native_launch.unexpected(id integer)');migrate(conn)
        assert conn.execute('SELECT checksum FROM mf_execution.schema_migrations').fetchall()==old


def configure_offline_scheduler(host):
    """PG admission fixture seam, no Temporal/native execution claim."""
    from mirofish_execution.temporal_native_host import NativeWorkflowRef
    calls=[]
    host.temporal=object()
    def call(method,request,ref):
        if method=='local_status':raise LaunchAuthorityError('native_launch_unavailable')
        calls.append(request)
        return NativeWorkflowRef('mf-native-v1-'+request.run_id.hex+'-'+request.fingerprint,str(uuid4()),str(request.run_id))
    host.temporal_call=call
    return calls


def test_pg_same_id_queue_error_does_not_release_a_concurrent_winner(factory,tmp_path):
    from test_native_launch_api import reference
    from mirofish_execution.budget import ReservationState
    prep,scope,plan=ready_host(factory,tmp_path);host=launch_host(prep,factory)
    calls=configure_offline_scheduler(host);dto=host.plan(declaration(plan))
    failed_queue=threading.Event();winner_queued=threading.Event()
    actual_queue=host.store.queue;actual_close=host.store.close_undispatched;actual_call=host.temporal_call
    def queue(principal,run,sha,attempt):
        if threading.current_thread().name.startswith('failed-start'):
            failed_queue.set();raise LaunchAuthorityError('conflict')
        assert failed_queue.wait(5)
        return actual_queue(principal,run,sha,attempt)
    def close(principal,run,sha):
        assert winner_queued.wait(10)
        return actual_close(principal,run,sha)
    def call(method,request,ref):
        result=actual_call(method,request,ref)
        if method=='start':winner_queued.set()
        return result
    host.store.queue=queue;host.store.close_undispatched=close;host.temporal_call=call
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='failed-start') as first:
            failure=first.submit(host.start,reference(dto));assert failed_queue.wait(5)
            queued=host.start(reference(dto))
            with pytest.raises(LaunchAuthorityError):failure.result(timeout=10)
        row=host.store.get('owner',dto['request']['run_id'])
        reservation=host.budget.native_reservation('owner',host.account_id,row.request,row.launch_sha256)
        assert queued['state']=='queued' and len(calls)==1 and row.dispatch_claimed
        assert reservation.state==ReservationState.started and reservation.attempt_id==row.budget_attempt_id
        assert actual_close('owner',row.run_id,row.launch_sha256)[1] is False
    finally:winner_queued.set()


def test_pg_locked_close_wins_before_stale_same_id_queue_and_release(factory,tmp_path):
    from mirofish_execution.budget import ReservationState
    prep,scope,plan=ready_host(factory,tmp_path);host=launch_host(prep,factory)
    dto=host.plan(declaration(plan));row=host.store.get('owner',dto['request']['run_id'])
    reservation=host.budget.reserve_native('owner',host.account_id,scope,row.request,row.launch_sha256,4)
    stale_read=threading.Event();closed=threading.Event()
    def stale_queue():
        stale=host.store.get('owner',row.run_id,row.launch_sha256)
        assert stale.state=='planned';stale_read.set();assert closed.wait(5)
        return host.store.queue('owner',stale.run_id,stale.launch_sha256,reservation.attempt_id)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            queued=pool.submit(stale_queue);assert stale_read.wait(5)
            proof,proven=host.store.close_undispatched('owner',row.run_id,row.launch_sha256)
            assert proven and proof.state=='cancelled' and proof.cancel_requested and not proof.dispatch_claimed
            assert proof.budget_attempt_id is None
            host.budget.release_undispatched('owner',host.account_id,row.run_id,reservation.attempt_id)
            closed.set();reply,won=queued.result(timeout=5)
        assert not won and reply.state=='cancelled' and not reply.dispatch_claimed
        assert host.budget.native_reservation('owner',host.account_id,row.request,row.launch_sha256).state==ReservationState.released
    finally:closed.set()


def test_pg_distinct_review_start_race_releases_only_permanently_closed_loser(factory,tmp_path):
    from test_native_launch_api import reference
    from mirofish_execution.budget import ReservationState
    prep,scope,plan=ready_host(factory,tmp_path);host=launch_host(prep,factory)
    calls=configure_offline_scheduler(host);plans=[host.plan(declaration(plan)) for _ in range(2)]
    ready=threading.Barrier(2);actual=host.store.queue
    def queue(*args):
        ready.wait(timeout=5)
        return actual(*args)
    host.store.queue=queue
    def start(dto):
        try:return host.start(reference(dto))
        except LaunchAuthorityError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(start,plans))
    assert sum(type(reply) is dict for reply in replies)==1 and replies.count('conflict')==1 and len(calls)==1
    rows=[host.store.get('owner',dto['request']['run_id']) for dto in plans]
    winner=next(row for row in rows if row.dispatch_claimed)
    loser=next(row for row in rows if not row.dispatch_claimed)
    assert winner.state=='queued' and loser.state=='cancelled' and loser.cancel_requested and loser.budget_attempt_id is None
    assert host.budget.native_reservation('owner',host.account_id,winner.request,winner.launch_sha256).state==ReservationState.started
    assert host.budget.native_reservation('owner',host.account_id,loser.request,loser.launch_sha256).state==ReservationState.released
