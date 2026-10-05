"""Pure host seams plus actual newly generated inherited preparation files."""
from copy import deepcopy
from dataclasses import replace
import json
import pickle
import threading
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from app.services.durable_native_launch_host import DurableNativeLaunchHost
from app.services.native_launch_models import BoundedNativeModelFactory,SharedCallBoundary,NativeModelBoundExceeded,validate_transport
from app.services.native_launch_client import NativeLaunchError,digest
from mirofish_execution.native_launch_contracts import LaunchAuthorityError
from mirofish_execution.native_launch_store import LaunchRecord,_record
from mirofish_execution.native_run_contracts import NativeRunDenied
from mirofish_execution.budget import ReservationState
from test_durable_preparation import offline_host,plan_request,status_request
from test_native_launch_api import reference


def scripted_native_backends(*,timeout_seconds,max_tokens,max_retries):
    """Top-level picklable child primitive, real CAMEL response/tool interface."""
    assert 0<timeout_seconds<=15 and max_retries==0
    from camel.models import BaseModelBackend
    from camel.utils.token_counting import BaseTokenCounter
    from openai.types.chat.chat_completion import ChatCompletion,ChatCompletionMessage,Choice
    from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall,Function
    class Counter(BaseTokenCounter):
        def count_tokens_from_messages(self,messages):
            return sum(len(json.dumps(m,ensure_ascii=False)) for m in messages)//4+1
        def encode(self,value):
            return [ord(c) for c in value]
        def decode(self,values):
            return ''.join(chr(v) for v in values)
    class Model(BaseModelBackend):
        def __init__(self):
            super().__init__(model_type='gpt-4o-mini',model_config_dict={'max_tokens':max_tokens,'stream':False})
            self.counter,self.calls=Counter(),0
            self._timeout,self._max_retries=timeout_seconds,max_retries
            self._url,self._base_url,self._api_key=None,None,None
        @property
        def token_counter(self):
            return self.counter
        def reply(self,tools):
            self.calls+=1
            actions=[ChatCompletionMessageToolCall(id='local-'+str(self.calls),type='function',
                function=Function(name='create_post',arguments=json.dumps({'content':'Scripted connected native post '+str(self.calls)})))] if tools else None
            return ChatCompletion(id='local-'+str(self.calls),object='chat.completion',created=1,model='scripted-local',
                choices=[Choice(index=0,finish_reason='tool_calls' if tools else 'stop',
                    message=ChatCompletionMessage(role='assistant',content=None if tools else 'scripted',tool_calls=actions))])
        def _run(self,messages,response_format=None,tools=None):
            return self.reply(tools)
        async def _arun(self,messages,response_format=None,tools=None):
            return self.reply(tools)
    return {'twitter':Model(),'reddit':Model()}


LIMITS={'max_calls':20,'max_input_bytes':262144,'max_output_tokens':4096,'max_run_seconds':120}


class MemoryLaunchStore:
    """Unit-only; production always constructs NativeLaunchStore."""
    def __init__(self):
        self.rows={}
        self.lock=threading.RLock()
    def get(self,principal,run_id,launch_sha256=None):
        row=self.rows.get(str(run_id))
        if row is None or row.principal!=principal:
            raise LaunchAuthorityError('not_found')
        if launch_sha256 is not None and row.launch_sha256!=launch_sha256:
            raise LaunchAuthorityError('conflict')
        return row
    def put(self,principal,declaration,identity,*,configuration=None):
        req=identity['request']
        row=LaunchRecord(UUID(req['run_id']),principal,UUID(identity['preparation']['operation_id']),req['simulation_id'],
            UUID(req['project_id']),req['project_revision'],digest(declaration),digest(identity),
            deepcopy({'identity':identity,'declaration':declaration,'configuration':configuration}),
            'planned',None,None,None,False,None)
        _record(tuple(row.__dict__.values()))
        self.rows[str(row.run_id)]=row
        return row
    def save(self,row):
        self.rows[str(row.run_id)]=row
        return row
    def queue(self,principal,run_id,sha,attempt):
        with self.lock:
            return self._queue(principal,run_id,sha,attempt)
    def _queue(self,principal,run_id,sha,attempt):
        row=self.get(principal,run_id,sha)
        if row.state!='planned':
            return row,False
        if any(other.dispatch_claimed and other.preparation_id==row.preparation_id for other in self.rows.values()):
            raise LaunchAuthorityError('conflict')
        return self.save(replace(row,state='queued',budget_attempt_id=attempt,dispatch_claimed=True)),True
    def workflow(self,principal,run_id,sha,value):
        return self.save(replace(self.get(principal,run_id,sha),workflow=value))
    def scheduling_uncertain(self,principal,run_id,sha):
        return self.save(replace(self.get(principal,run_id,sha),state='uncertain',error_code='native_launch_uncertain'))
    def cancel(self,principal,run_id,sha):
        with self.lock:
            row=self.get(principal,run_id,sha)
            return self.save(replace(row,cancel_requested=True,state='cancelled' if row.state=='planned' else row.state))
    def close_undispatched(self,principal,run_id,sha):
        with self.lock:
            row=self.get(principal,run_id,sha)
            if row.dispatch_claimed or row.budget_attempt_id is not None:return row,False
            if row.state=='planned':row=self.save(replace(row,state='cancelled',cancel_requested=True))
            return row,row.state=='cancelled' and row.cancel_requested


class MemoryNativeBudget:
    def __init__(self):
        self.rows={}
    def reserve_native(self,principal,account,scope,request,sha,ceiling):
        self.rows.setdefault(request.run_id,SimpleNamespace(state=ReservationState.reserved,attempt_id=uuid4()))
        return self.rows[request.run_id]
    def start(self,principal,account,run,attempt):
        assert self.rows[run].attempt_id==attempt
        self.rows[run].state=ReservationState.started
    def mark_uncertain(self,principal,account,run,attempt,code):
        self.rows[run].state=ReservationState.uncertain
    def release_undispatched(self,principal,account,run,attempt):
        assert self.rows[run].state==ReservationState.reserved
        self.rows[run].state=ReservationState.released


def host_fixture(tmp_path):
    prep,chat,transport,project=offline_host(tmp_path)
    plan=prep.plan(plan_request());prep.start(status_request(plan))
    prep.generate(prep.store.get('owner',plan['operation_id']).dispatch.to_wire())
    model=BoundedNativeModelFactory(('twitter','reddit'),dict(LIMITS),scripted_native_backends,cooperative_transport=True)
    host=DurableNativeLaunchHost(preparation_host=prep,connection_factory=lambda:None,runtime_sha256='c'*64,
        limits=LIMITS,model_factory=model,model_label='scripted',account_id=prep.account_id,
        ceiling_microusd=4,authorize=lambda:True)
    host.store,host.budget=MemoryLaunchStore(),MemoryNativeBudget()
    class Native:
        def reconcile_expired(self,*args):
            raise NativeRunDenied()
        def request_cancel(self,*args):
            raise NativeRunDenied()
    host.native=Native()
    host.temporal=SimpleNamespace()
    calls=[]
    def bridge(method,request,ref):
        if method=='local_status':
            raise NativeLaunchError('native_launch_unavailable')
        from mirofish_execution.temporal_native_host import NativeWorkflowRef
        calls.append(request)
        return NativeWorkflowRef('mf-native-v1-'+request.run_id.hex+'-'+request.fingerprint,str(uuid4()),str(request.run_id))
    host.temporal_call=bridge
    payload={'schema_version':1,'launch_id':str(uuid4()),'preparation':{'operation_id':plan['operation_id'],'plan_sha256':plan['plan_sha256']}}
    return host,payload,calls,project


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


@pytest.mark.parametrize('change',['disabled','limits','label','factory','runtime','ceiling'])
def test_policy_changes_deny_before_dispatch(tmp_path,change):
    host,payload,calls,_=host_fixture(tmp_path)
    planned=host.plan(payload)
    if change=='disabled':host.authorize=lambda:False
    elif change=='limits':host.limits=dict(LIMITS,max_calls=19)
    elif change=='label':host.model_label='other'
    elif change=='factory':host.model_factory=replace(host.model_factory,transport_timeout_seconds=14)
    elif change=='runtime':host.runtime_sha256='d'*64
    else:host.ceiling=5
    with pytest.raises(NativeLaunchError) as error:host.start(reference(planned))
    assert error.value.code=='model_calls_disabled' and not calls and not host.budget.rows


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


def test_input_output_and_time_bounds():
    now=[0]
    boundary=SharedCallBoundary(dict(LIMITS,max_input_bytes=50,max_run_seconds=1),clock=lambda:now[0])
    with pytest.raises(NativeModelBoundExceeded):boundary.admit([{'content':'a'*51}],None,None)
    now[0]=1
    with pytest.raises(NativeModelBoundExceeded):boundary.admit([],None,None)
    boundary=SharedCallBoundary(LIMITS)
    response=SimpleNamespace(choices=[],usage=SimpleNamespace(completion_tokens=4097))
    with pytest.raises(NativeModelBoundExceeded):boundary.accept(response,SimpleNamespace())


@pytest.mark.parametrize('change',[{'_timeout':None},{'_timeout':16},{'_timeout':float('inf')},
    {'_max_retries':1},{'_max_retries':False},{'_url':'https://api.example/v1'},
    {'_api_key':'PRIVATE'},{'_client':object()}],ids=['missing','late','inf','retry','bool','endpoint','key','sdk'])
def test_scripted_transport_must_really_be_offline_finite_and_no_retry(change):
    backend=SimpleNamespace(_timeout=15,_max_retries=0,_url=None,_base_url=None,_api_key=None,
                            model_config_dict={'max_tokens':4096,'stream':False})
    for k,v in change.items():setattr(backend,k,v)
    with pytest.raises(NativeModelBoundExceeded):validate_transport(backend,mode='scripted',timeout_seconds=15,max_tokens=4096)


@pytest.mark.parametrize('url',['https://example.com/v1','http://localhost:8000/v1',
    'http://user@127.0.0.1:8000/v1','http://127.0.0.1:8000/v1?key=x','http://[::1]:8000/v1#x',
    'file:///v1','http://127.0.0.1:99999/v1'])
def test_local_transport_rejects_external_or_ambiguous_endpoint(url):
    backend=SimpleNamespace(_timeout=5,_max_retries=0,_url=url,_base_url=None,
                            model_config_dict={'max_tokens':100,'stream':False})
    with pytest.raises(NativeModelBoundExceeded):validate_transport(backend,mode='local',timeout_seconds=15,max_tokens=4096)


def test_literal_loopback_local_transport_is_explicitly_accepted():
    for url in ('http://127.0.0.1:8000/v1','http://[::1]:8000/v1'):
        backend=SimpleNamespace(_timeout=5,_max_retries=0,_url=url,_base_url=None,
                                model_config_dict={'max_tokens':100,'stream':False})
        validate_transport(backend,mode='local',timeout_seconds=15,max_tokens=4096)


def test_budget_adapter_keeps_failed_close_owner_retained_until_same_owner_retry():
    from test_native_run_supervisor import request,MemoryStore,ControlledDriver,ScriptedCoordinator
    from mirofish_execution.budgeted_native_supervisor import BudgetedNativeSupervisor
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


@pytest.mark.parametrize('mutate',[
    lambda row:replace(row,frozen=None),
    lambda row:replace(row,frozen=dict(row.frozen,private_path='PRIVATE')),
    lambda row:replace(row,frozen=dict(row.frozen,configuration=None)),
    lambda row:replace(row,frozen=dict(row.frozen,configuration={'account_id':'bad','factory_sha256':'a'*64})),
    lambda row:replace(row,frozen=dict(row.frozen,configuration={'account_id':None,'factory_sha256':'A'*64})),
    lambda row:replace(row,frozen=dict(row.frozen,configuration=dict(row.frozen['configuration'],extra=True))),
    lambda row:replace(row,state='running'),
    lambda row:replace(row,state='planned',cancel_requested=True),
    lambda row:replace(row,state='cancelled',cancel_requested=False),
    lambda row:replace(row,dispatch_claimed=1),
    lambda row:replace(row,cancel_requested=1),
    lambda row:replace(row,dispatch_claimed=True),
    lambda row:replace(row,budget_attempt_id=uuid4()),
    lambda row:replace(row,workflow={}),
    lambda row:replace(row,receipt={}),
    lambda row:replace(row,error_code='PRIVATE'),
    lambda row:replace(row,state='uncertain',dispatch_claimed=True,budget_attempt_id=uuid4(),error_code=None),
    lambda row:replace(row,state='completed',dispatch_claimed=True,budget_attempt_id=uuid4())],
    ids=['frozen-null','frozen-extra','config-null','account','factory-hash','config-extra','running-unclaimed',
         'planned-cancel','closed-no-intent','claim-bool','cancel-bool','claim-no-attempt','unclaimed-attempt',
         'unclaimed-workflow','unclaimed-receipt','error','uncertain-error','terminal-no-receipt'])
def test_malformed_authoritative_launch_rows_fail_closed(tmp_path,mutate):
    host,payload,_,_=host_fixture(tmp_path)
    host.plan(payload);row=host.store.get('owner',payload['launch_id'])
    with pytest.raises(LaunchAuthorityError) as invalid:
        wrong=mutate(row)
        _record(tuple(wrong.__dict__.values()))
    assert invalid.value.code=='native_launch_uncertain'


def test_authoritative_row_validates_closed_and_claimed_states(tmp_path):
    host,payload,_,_=host_fixture(tmp_path);host.plan(payload)
    row=host.store.get('owner',payload['launch_id'])
    closed=replace(row,state='cancelled',cancel_requested=True)
    assert _record(tuple(closed.__dict__.values()))==closed
    uncertain=replace(row,state='uncertain',dispatch_claimed=True,budget_attempt_id=uuid4(),error_code='native_launch_uncertain')
    assert _record(tuple(uncertain.__dict__.values()))==uncertain


def test_spawn_gate_module_import_is_lightweight():
    import subprocess
    import sys
    from pathlib import Path
    gate_directory=Path(__file__).resolve().parents[2]/'services'/'knowledge'/'tests'
    code=(f'import sys; sys.path.insert(0,{str(gate_directory)!r}); '
          'import test_temporal_connected_launch; '
          "forbidden={'temporalio','psycopg','camel','oasis','app','test_native_launch_store',"
          "'test_preparation_store','test_native_run_supervisor_integration','test_native_launch_api'}; "
          "found=sorted(forbidden & set(sys.modules)); print('gate_import_forbidden_modules='+','.join(found)); "
          'assert not found')
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10,check=False)
    assert result.returncode==0,'lightweight_gate_import_failed'
    assert result.stdout.strip()=='gate_import_forbidden_modules='


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
