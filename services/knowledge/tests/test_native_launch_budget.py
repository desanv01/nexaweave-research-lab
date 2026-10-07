"""Pure tagged receipt semantics and three-purpose shared real-PG admission."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import pickle
import threading
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt,LaunchAuthorityError,native_budget_fingerprint
from nexaweave_execution.native_run_contracts import NativeRunReceipt,NativeChildIdentity
from nexaweave_execution.native_run_store import NativeRunStore
from nexaweave_execution.budget import BudgetDenied,BudgetConflict,BudgetUncertain,BudgetBusy,ReservationState
from nexaweave_execution.preparation_contracts import PreparedBudgetReceipt
from nexaweave_knowledge.operations import CompletionReceipt
from test_native_launch_store import factory,ready_host,launch_host,declaration
from test_durable_native_launch import host_fixture,LIMITS,scripted_native_backends
from test_native_launch_api import reference
from app.services.native_launch_models import BoundedNativeModelFactory,NativeModelBoundExceeded
from app.services.native_launch_client import NativeLaunchError

# These seven unchanged fixtures require the dedicated installed native unit
# environment (CAMEL/Temporal and knowledge fixture path). They are deliberately
# unmarked unit cases, with no skip or test aliases in the lean backend module.


def test_ready_binding_one_shot_cancellation_and_configuration_freeze(tmp_path):
    host,payload,calls,project=host_fixture(tmp_path)
    planned=host.plan(payload)
    assert not calls and planned['request']['max_rounds']==2
    assert host.plan(payload)==planned
    queued=host.start(reference(planned))
    assert queued['state']=='queued' and len(calls)==1
    assert host.start(reference(planned))['request']==planned['request'] and len(calls)==1
    cancelled=host.cancel(reference(planned))
    assert cancelled['cancel_requested'] and cancelled['state']=='queued' and cancelled['receipt'] is None
    assert cancelled['cleanup']=={'known':False,'pending':None,'owner_thread_alive':None}
    alternate=host.plan(dict(payload,launch_id=str(uuid4())))
    with pytest.raises(LaunchAuthorityError):
        host.start(reference(alternate))
    project.revision=2
    from app.services.preparation_client import PreparationError
    with pytest.raises(PreparationError):
        host.status(reference(planned))


def test_lost_ack_recovery_cannot_reschedule_or_release(tmp_path):
    host,payload,calls,_=host_fixture(tmp_path)
    planned=host.plan(payload)
    original=host.temporal_call
    def lost(method,request,ref):
        value=original(method,request,ref)
        if method=='start':
            raise RuntimeError('PRIVATE_LOST_ACK')
        return value
    host.temporal_call=lost
    with pytest.raises(NativeLaunchError) as error:
        host.start(reference(planned))
    assert error.value.code=='native_launch_uncertain'
    recovered=host.status(reference(planned))
    assert recovered['state']=='uncertain' and recovered['workflow'] is None and len(calls)==1
    assert host.start(reference(planned))['state']=='uncertain' and len(calls)==1
    assert host.budget.rows[UUID(payload['launch_id'])].state==ReservationState.uncertain
    alternate=host.plan(dict(payload,launch_id=str(uuid4())))
    with pytest.raises(LaunchAuthorityError):host.start(reference(alternate))
    assert len(calls)==1


def test_disabled_review_does_not_spend_artifact_and_new_review_can_start(tmp_path):
    host,payload,calls,_=host_fixture(tmp_path)
    host.ceiling=None;host.authorize=lambda:False
    disabled=host.plan(payload)
    assert disabled['ceiling_microusd'] is None and not disabled['authorization']['model_calls_enabled']
    with pytest.raises(NativeLaunchError):host.start(reference(disabled))
    assert not calls and not host.budget.rows
    host.ceiling=4;host.authorize=lambda:True
    fresh=host.plan(dict(payload,launch_id=str(uuid4())))
    assert fresh['launch_sha256']!=disabled['launch_sha256']
    assert host.start(reference(fresh))['state']=='queued' and len(calls)==1
    assert host.status(reference(disabled))['ceiling_microusd'] is None


def test_shared_sync_async_call_bound_and_picklable_parameters():
    factory=BoundedNativeModelFactory(('twitter','reddit'),dict(LIMITS,max_calls=2),scripted_native_backends,cooperative_transport=True)
    assert pickle.loads(pickle.dumps(factory)).configured()
    models=factory()
    models['twitter'].run([{'role':'user','content':'one'}])
    import asyncio
    asyncio.run(models['reddit'].arun([{'role':'user','content':'two'}]))
    with pytest.raises(NativeModelBoundExceeded):models['twitter'].run([{'role':'user','content':'three'}])
    assert not replace(factory,mode='provider').configured()
    assert not replace(factory,sdk_retries=1).configured()


def test_budget_adapter_keeps_failed_close_owner_retained_until_same_owner_retry():
    from test_native_run_supervisor import request,MemoryStore,ControlledDriver,ScriptedCoordinator
    from nexaweave_execution.budgeted_native_supervisor import BudgetedNativeSupervisor
    req=request();store=MemoryStore(req);driver=ControlledDriver()
    driver.allow_launch.set();driver.finish.set();driver.fail_first_close=True
    coordinator=ScriptedCoordinator(req,store,driver)
    class Ledger:
        def __init__(self):self.receipts=[]
        def settle_native(self,principal,account,scope,request,attempt,sha,receipt):
            assert store.value.receipt==receipt and request==req
            self.receipts.append(receipt)
    ledger=Ledger()
    supervisor=BudgetedNativeSupervisor(req,coordinator,ledger=ledger,account_id=uuid4(),scope=object(),
        budget_attempt_id=uuid4(),launch_sha256='a'*64,poll_seconds=0.05)
    try:
        supervisor.start(5);result=supervisor.wait(5)
        assert result.receipt is not None and ledger.receipts
        assert driver.first_close_failed.wait(5)
        pending=supervisor.status()
        assert pending.cleanup_pending and pending.thread_alive
        assert supervisor.close(5)
        closed=supervisor.status()
        assert not closed.cleanup_pending and not closed.thread_alive and driver.closes==2
    finally:
        driver.allow_launch.set();driver.allow_close.set();assert supervisor.close(5)


def test_same_id_queue_failure_close_proof_cannot_release_concurrent_queued_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    host,payload,calls,_=host_fixture(tmp_path)
    dto=host.plan(payload)
    first_queue=threading.Event();winner_queued=threading.Event()
    actual_queue=host.store.queue;actual_close=host.store.close_undispatched
    original_bridge=host.temporal_call
    def queue(principal,run,sha,attempt):
        if threading.current_thread().name.startswith('failed-start'):
            first_queue.set()
            raise LaunchAuthorityError('conflict')
        assert first_queue.wait(5)
        return actual_queue(principal,run,sha,attempt)
    def close(principal,run,sha):
        assert winner_queued.wait(5)
        return actual_close(principal,run,sha)
    def bridge(method,request,ref):
        result=original_bridge(method,request,ref)
        if method=='start':winner_queued.set()
        return result
    host.store.queue=queue;host.store.close_undispatched=close;host.temporal_call=bridge
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='failed-start') as first:
            failure=first.submit(host.start,reference(dto))
            assert first_queue.wait(5)
            queued=host.start(reference(dto))
            with pytest.raises(LaunchAuthorityError):failure.result(timeout=5)
        assert queued['state']=='queued' and len(calls)==1
        row=host.store.get('owner',payload['launch_id'])
        assert row.dispatch_claimed and row.budget_attempt_id is not None
        assert host.budget.rows[row.run_id].state==ReservationState.started
        assert actual_close('owner',row.run_id,row.launch_sha256)[1] is False
    finally:
        winner_queued.set()


def test_gate_failure_diagnostics_do_not_refresh_authority_or_leak_exception_text():
    from test_temporal_connected_launch import gate_diagnostics
    class ClosedProcess:
        def is_alive(self):raise ValueError('PRIVATE_CREDENTIAL_ENDPOINT_PATH')
    driver=SimpleNamespace(_spent=True,_closed=True,_owned=None,_pending=(ClosedProcess(),None,None))
    class Supervisor:
        coordinator=SimpleNamespace(driver=driver)
        def _snapshot(self,*,refresh):
            assert refresh is False
            return SimpleNamespace(phase='error',run_state=SimpleNamespace(value='uncertain'),
                error_code='native_run_uncertain',cancel_requested=False,cleanup_pending=True,thread_alive=True)
    diagnostic=gate_diagnostics([Supervisor()])
    assert diagnostic['owners'][0]['cached_run_state']=='uncertain'
    assert diagnostic['owners'][0]['error_code']=='native_run_uncertain'
    assert diagnostic['owners'][0]['cleanup_pending'] and diagnostic['owners'][0]['owner_thread_alive']
    assert not diagnostic['owners'][0]['process_known'] and diagnostic['owners'][0]['process_alive'] is None
    assert 'PRIVATE' not in json.dumps(diagnostic)


def test_native_receipt_domain_is_distinct_and_exact():
    operation,attempt=uuid4(),uuid4();launch='a'*64
    native=NativeRunReceipt(operation,uuid4(),uuid4(),'b'*64,'completed','c'*64)
    receipt=NativeBudgetReceipt(operation,attempt,native_budget_fingerprint(launch),launch,native)
    assert NativeBudgetReceipt.from_wire(receipt.json_value())==receipt
    assert receipt.kind=='native_run_budget_v1'
    prepared=PreparedBudgetReceipt(operation,attempt,'d'*64,'e'*64)
    with pytest.raises(LaunchAuthorityError):NativeBudgetReceipt.from_wire(prepared.json_value())
    for change in ({'kind':'prepared_budget_v1'},{'operation_id':str(uuid4())},{'fingerprint':'f'*64},{'bill_microusd':1}):
        with pytest.raises(LaunchAuthorityError):NativeBudgetReceipt.from_wire(dict(receipt.json_value(),**change))
    assert native_budget_fingerprint('a'*64)!=native_budget_fingerprint('b'*64)


@pytest.mark.postgres
def test_three_domain_cap_race_and_same_uuid_domain_isolation(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path,cap=9)
    host=launch_host(prep,factory);dto=host.plan(declaration(plan));row=host.store.get('owner',dto['request']['run_id'])
    ledger=host.budget
    def reserve_native():return ledger.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
    def reserve_prepared():return ledger.reserve_prepared('owner',prep.account_id,scope,uuid4(),'d'*64,4)
    def reserve_ingest():return ledger.reserve('owner',prep.account_id,scope,uuid4(),'e'*64,4,())
    def try_reserve(fn):
        try:return fn()
        except BudgetDenied:return None
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(try_reserve,[reserve_native,reserve_prepared,reserve_ingest]))
    assert sum(row is not None for row in rows)==1
    assert ledger.status('owner',prep.account_id).accounted_ceiling_microusd==4
    assert ledger.status('owner',prep.account_id).remaining_microusd==1
    winner=next(row for row in rows if row is not None)
    if winner.operation_id==row.run_id:
        with pytest.raises(BudgetConflict):ledger.reserve_prepared('owner',prep.account_id,scope,row.run_id,row.launch_sha256,4)
        with pytest.raises(BudgetConflict):ledger.reserve('owner',prep.account_id,scope,row.run_id,winner.fingerprint,4,())


def authoritative_receipt(factory,request):
    """Store-only fixture: typed trusted observation, no engine claim."""
    store=NativeRunStore(factory);store.register(request);owner=uuid4()
    claim=store.claim_start('owner',request.run_id,owner,60)
    child=NativeChildIdentity(uuid4(),1,'d'*64)
    store.attach('owner',request.run_id,claim.attempt_id,owner,child,60)
    receipt=NativeRunReceipt(request.run_id,claim.attempt_id,child.instance_id,request.fingerprint,'completed','e'*64)
    store.settle('owner',request.run_id,claim.attempt_id,owner,receipt)
    return receipt


@pytest.mark.postgres
def test_native_full_ceiling_settlement_requires_exact_pg_proof_and_rejects_injection(factory,tmp_path):
    prep,scope,plan=ready_host(factory,tmp_path)
    host=launch_host(prep,factory);dto=host.plan(declaration(plan));row=host.store.get('owner',dto['request']['run_id'])
    ledger=host.budget;res=ledger.reserve_native('owner',prep.account_id,scope,row.request,row.launch_sha256,4)
    ledger.start('owner',prep.account_id,row.run_id,res.attempt_id)
    fake=NativeRunReceipt(row.run_id,uuid4(),uuid4(),row.request.fingerprint,'completed','e'*64)
    with pytest.raises(BudgetUncertain):ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,fake)
    with pytest.raises(BudgetUncertain):ledger.settle_prepared('owner',prep.account_id,scope,row.run_id,res.attempt_id,row.launch_sha256,'e'*64)
    graph_receipt=CompletionReceipt(scope.group_id,scope.episode_uuid(row.run_id),res.fingerprint,())
    with pytest.raises(BudgetUncertain):ledger.settle('owner',prep.account_id,scope,row.run_id,res.attempt_id,res.fingerprint,(),graph_receipt)
    ledger.mark_uncertain('owner',prep.account_id,row.run_id,res.attempt_id,'dispatch_uncertain')
    with pytest.raises(BudgetBusy):ledger.release_undispatched('owner',prep.account_id,row.run_id,res.attempt_id)
    native=authoritative_receipt(factory,row.request)
    settled=ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,native)
    assert type(settled.receipt) is NativeBudgetReceipt and settled.state==ReservationState.settled
    assert ledger.status('owner',prep.account_id).accounted_ceiling_microusd==8
    assert ledger.status('owner',prep.account_id).actual_usage_microusd is None
    assert ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,native)==settled
    with pytest.raises(BudgetUncertain):ledger.settle_native('owner',prep.account_id,scope,row.request,uuid4(),row.launch_sha256,native)
    with pytest.raises(BudgetUncertain):ledger.settle_native('owner',prep.account_id,scope,row.request,res.attempt_id,row.launch_sha256,replace(native,evidence_sha256='f'*64))
