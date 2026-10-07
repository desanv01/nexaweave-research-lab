"""Real Temporal/PG launch identity and retained owner using an offline gate.

The actual OASIS case lives in the separately selected engine fixture.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4
import pytest

# Spawn unpickles ConnectedGateFactory by importing this module. Keep imports
# here limited to stdlib and pytest: no Temporal/app/preparation/native fixture
# transitive imports before the accepted child protocol can send READY.

pytestmark=[pytest.mark.postgres,pytest.mark.native_launch_temporal]


@pytest.fixture(scope='module')
def factory():
    from test_native_launch_store import factory as guarded_factory
    return guarded_factory.__wrapped__()


async def loopback_client():
    from temporalio.client import Client
    if os.getenv('TEMPORAL_EXECUTION_INTEGRATION')!='1' or os.getenv('TEMPORAL_TEST_ADDRESS')!='127.0.0.1:17233':
        pytest.fail('selected native launch requires explicit disposable loopback Temporal')
    return await asyncio.wait_for(Client.connect('127.0.0.1:17233'),15)


def temporal_bridge(host,loop,*,lost_start=False):
    """Owned loop, finite call, lost start acknowledgement never redispatched."""
    def call(method,request,ref):
        if method in {'start','local_status','retry_cleanup'}:
            coroutine=getattr(host,method)(request)
        else:coroutine=getattr(host,method)(request,ref)
        future=asyncio.run_coroutine_threadsafe(coroutine,loop)
        result=future.result(timeout=15)
        if lost_start and method=='start':raise RuntimeError('PRIVATE_LOST_ACK')
        return result
    return call


@dataclass
class ConnectedGateSession:
    started: str
    release: str
    finished: str

    def start(self):
        Path(self.started).write_text('started',encoding='ascii')
        deadline=time.monotonic()+40
        while not Path(self.release).exists():
            if time.monotonic()>=deadline:
                raise TimeoutError('fixture_gate_timeout')
            time.sleep(0.05)
        Path(self.finished).write_text('finished',encoding='ascii')

    def close(self):
        pass


@dataclass
class ConnectedGateFactory:
    started: str
    release: str
    finished: str
    expected_fingerprint: str

    def validate(self,request):
        if request.fingerprint!=self.expected_fingerprint:raise ValueError('binding mismatch')

    def create_session(self,request):
        return ConnectedGateSession(self.started,self.release,self.finished)

    def evidence(self,request):
        return hashlib.sha256(Path(self.finished).read_bytes()).hexdigest()


def gate_diagnostics(created):
    """Bounded nonblocking cached owner/driver observations; no private text.

    Do not refresh the database or invoke driver operations while diagnosing a
    missing marker. At most four local snapshots and process polls are emitted.
    Closed/released process handles remain unknown, rather than invented clean.
    """
    phases={'new','starting','declared','running','completed','failed','cancelled',
            'uncertain','error','stopped_before_launch','closed_before_start'}
    errors={'native_run_denied','native_run_conflict','native_run_busy','native_run_uncertain',
            'native_run_unavailable','native_run_migration_mismatch','native_supervisor_unavailable',
            'native_supervisor_thread_unavailable','native_supervisor_wait_timeout'}
    values=[]
    for supervisor in tuple(created[:4]):
        value={'phase':None,'cached_run_state':None,'error_code':None,'cancel_requested':None,
               'cleanup_pending':None,'owner_thread_alive':None,'driver_spent':None,'driver_closed':None,
               'process_known':False,'process_id':None,'process_alive':None,'process_exitcode':None,
               'reader_alive':None,'reader_overflow':None}
        try:
            status=supervisor._snapshot(refresh=False)
            value.update(phase=status.phase if status.phase in phases else 'unknown',
                cached_run_state=status.run_state.value if status.run_state is not None and status.run_state.value in phases else None,
                error_code=status.error_code if status.error_code in errors else 'unknown' if status.error_code is not None else None,
                cancel_requested=status.cancel_requested,cleanup_pending=status.cleanup_pending,
                owner_thread_alive=status.thread_alive)
            driver=supervisor.coordinator.driver
            value.update(driver_spent=driver._spent,driver_closed=driver._closed)
            owned=driver._owned
            pending=driver._pending
            process=owned.process if owned is not None else pending[0] if pending is not None else None
            reader=owned.reader if owned is not None else pending[2] if pending is not None else None
            if reader is not None:
                value.update(reader_alive=reader.thread.is_alive(),reader_overflow=reader.overflow)
            if process is not None:
                # multiprocessing process polling has no wait or join here.
                alive=process.is_alive();pid=process.pid;exitcode=process.exitcode
                if type(alive) is bool and (pid is None or type(pid) is int) and (exitcode is None or type(exitcode) is int):
                    value.update(process_known=True,process_id=pid,process_alive=alive,process_exitcode=exitcode)
        except Exception:
            # Safe fields already observed remain useful; no exception text,
            # executable, source, endpoint, credentials or path is serialized.
            pass
        values.append(value)
    return {'created_supervisors':min(len(created),4),'owners':values}


def await_gate_file(path,created,seconds=10):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if path.exists():return
        time.sleep(0.02)
    pytest.fail('offline fixture did not reach bounded marker; safe_cached_native_diagnostics='+
                json.dumps(gate_diagnostics(created),sort_keys=True,separators=(',',':')))


def gate_constructor(host,factory,scope,root,created):
    from nexaweave_execution.native_run_store import NativeRunStore
    from nexaweave_execution.native_run_coordinator import NativeRunCoordinator
    from nexaweave_execution.native_process_driver import NativeProcessDriver
    from nexaweave_execution.budgeted_native_supervisor import BudgetedNativeSupervisor
    from nexaweave_execution.native_launch_contracts import LaunchAuthorityError
    def construct(request):
        row=host.store.get('owner',request.run_id)
        host._current(row)
        if row.request!=request or not row.dispatch_claimed:raise LaunchAuthorityError('conflict')
        paths=[str(root/name) for name in ('started','release','finished')]
        driver=NativeProcessDriver(ConnectedGateFactory(*paths,request.fingerprint),observe_seconds=0.02,
                                  grace_seconds=0.1,join_seconds=0.2,go_timeout_seconds=30)
        coordinator=NativeRunCoordinator('owner',NativeRunStore(factory),driver,dispatch_allowed=lambda _:True,lease_seconds=20)
        supervisor=BudgetedNativeSupervisor(request,coordinator,ledger=host.budget,account_id=host.account_id,
            scope=scope,budget_attempt_id=row.budget_attempt_id,launch_sha256=row.launch_sha256,
            poll_seconds=0.1,call_budget_seconds=5)
        created.append(supervisor)
        return supervisor
    return construct


@pytest.mark.asyncio
async def test_real_temporal_pg_completion_exact_recovery_and_owner_cleanup(factory,tmp_path):
    from temporalio.worker import Replayer
    from nexaweave_execution.temporal_native_host import TemporalNativeHost,NativeWorkflowRef
    from nexaweave_execution.temporal_native_workflow import NativeExecutionWorkflow
    from test_native_launch_store import ready_host,launch_host,declaration
    from test_native_launch_api import reference
    temporal=await loopback_client()
    prep,scope,plan=await asyncio.to_thread(ready_host,factory,tmp_path)
    host=launch_host(prep,factory)
    created=[]
    native=TemporalNativeHost(client=temporal,task_queue='mf-connected-'+uuid4().hex,trusted_principal='owner',
        supervisor_factory=gate_constructor(host,factory,scope,tmp_path,created),allow_dispatch=lambda:True)
    loop=asyncio.get_running_loop();host.attach_temporal(native,temporal_bridge(native,loop))
    try:
        async with native.worker():
            dto=await asyncio.to_thread(host.plan,declaration(plan))
            await asyncio.to_thread(host.start,reference(dto))
            row=host.store.get('owner',dto['request']['run_id'])
            await asyncio.to_thread(await_gate_file,tmp_path/'started',created)
            (tmp_path/'release').write_text('go',encoding='ascii')
            receipt=await asyncio.wait_for(native.result(row.request,NativeWorkflowRef(**row.workflow)),45)
            status=await asyncio.to_thread(host.status,reference(dto))
            assert status['state']=='completed' and status['receipt']==receipt.to_wire()
            assert host.budget.status('owner',host.account_id).accounted_ceiling_microusd==8
            assert (await asyncio.to_thread(host.start,reference(dto)))['receipt']==receipt.to_wire() and len(created)==1
            history=await temporal.get_workflow_handle(row.workflow['workflow_id'],run_id=row.workflow['temporal_run_id']).fetch_history()
            await Replayer(workflows=[NativeExecutionWorkflow]).replay_workflow(history)
            assert 'PRIVATE_SOURCE_SENTINEL' not in history.to_json() and str(tmp_path) not in history.to_json()
            local=await native.retry_cleanup(row.request)
            assert not local.cleanup_pending and not local.owner_thread_alive
            status=await asyncio.to_thread(host.status,reference(dto))
            assert status['cleanup']=={'known':True,'pending':False,'owner_thread_alive':False}
    finally:
        (tmp_path/'release').write_text('release',encoding='ascii')
        for supervisor in created:assert await asyncio.to_thread(supervisor.close,10)


@pytest.mark.asyncio
async def test_real_lost_ack_has_no_second_run_and_durable_cancel_receipt(factory,tmp_path):
    from nexaweave_execution.temporal_native_host import TemporalNativeHost
    from nexaweave_execution.native_launch_contracts import LaunchAuthorityError
    from test_native_launch_store import ready_host,launch_host,declaration
    from test_native_launch_api import reference
    temporal=await loopback_client();prep,scope,plan=await asyncio.to_thread(ready_host,factory,tmp_path)
    host=launch_host(prep,factory);created=[]
    native=TemporalNativeHost(client=temporal,task_queue='mf-connected-lost-'+uuid4().hex,trusted_principal='owner',
        supervisor_factory=gate_constructor(host,factory,scope,tmp_path,created),allow_dispatch=lambda:True)
    host.attach_temporal(native,temporal_bridge(native,asyncio.get_running_loop(),lost_start=True))
    try:
        async with native.worker():
            dto=await asyncio.to_thread(host.plan,declaration(plan))
            from app.services.native_launch_client import NativeLaunchError
            with pytest.raises(NativeLaunchError):await asyncio.to_thread(host.start,reference(dto))
            await asyncio.to_thread(await_gate_file,tmp_path/'started',created)
            # Recover exact PG/native authority although workflow acknowledgement
            # was deliberately lost. No second schedule to discover the run.
            status=await asyncio.to_thread(host.status,reference(dto))
            assert status['state']=='running' and status['workflow'] is None
            await asyncio.to_thread(host.start,reference(dto))
            alternate=await asyncio.to_thread(host.plan,declaration(plan))
            with pytest.raises(LaunchAuthorityError):await asyncio.to_thread(host.start,reference(alternate))
            cancelled=await asyncio.to_thread(host.cancel,reference(dto))
            assert cancelled['cancel_requested'] and len(created)==1
            outcome=await asyncio.to_thread(created[0].wait,20)
            assert outcome.receipt.outcome=='cancelled'
            final=await asyncio.to_thread(host.status,reference(dto))
            assert final['state']=='cancelled' and final['receipt']['outcome']=='cancelled'
            assert host.budget.status('owner',host.account_id).accounted_ceiling_microusd==8
    finally:
        (tmp_path/'release').write_text('release',encoding='ascii')
        for supervisor in created:assert await asyncio.to_thread(supervisor.close,10)
