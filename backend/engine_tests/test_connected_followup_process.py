"""Actual owned inherited-chat child with scripted local SDK transport only."""
from dataclasses import dataclass
import hashlib
import json
import os
import pickle
import re
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest

from test_connected_report_process import frozen_report


@dataclass(frozen=True)
class FollowupTransportFactory:
    mode: str='ok'
    trace_path: str | None=None

    def __call__(self,*,max_retries,timeout):
        assert max_retries==0 and 0<timeout<=15
        return FollowupTransport(self.mode,self.trace_path)


class FollowupTransport:
    max_retries=0
    def __init__(self,mode,trace_path):
        self.mode,self.trace_path,self.calls=mode,trace_path,0
        self.chat=SimpleNamespace(completions=self)

    def create(self,**kwargs):
        self.calls+=1
        assert kwargs['stream'] is False and 0<kwargs['timeout']<=15
        system=kwargs['messages'][0]['content']
        native=re.search(r'native:(twitter|reddit):(\d+):[0-9a-f]{64}',system)
        source=re.search(r'source:[0-9a-f-]{36}',system)
        assert native and source
        if self.trace_path:
            with open(self.trace_path,'a',encoding='utf-8') as output:
                output.write(json.dumps(dict(event='request',pid=os.getpid(),ordinal=self.calls,
                    cwd=os.getcwd(),source=bool(source),native=bool(native)))+'\n')
        if self.mode=='transport_failure':
            raise RuntimeError('secret local scripted error')
        if self.calls==1:
            content='<tool_call>'+json.dumps(dict(name='recorded_native_events',parameters=dict(
                platform=native.group(1),offset=int(native.group(2)),limit=1)))+'</tool_call>'
        elif self.calls==2:
            content='<tool_call>'+json.dumps(dict(name='quick_search',parameters=dict(query='rain',limit=2)))+'</tool_call>'
        elif self.mode=='invented_reference':
            content='Unsupported [[source:'+str(uuid4())+']]'
        else:
            content='Answer [['+source.group(0)+']] and [['+native.group(0)+']].'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),finish_reason='stop')])

    def close(self):
        pass


def child_fixture(tmp_path,*,mode='ok'):
    from app.services.report_process import ReportProcess,report_files
    from app.services.report_models import BoundedReportModelFactory
    from app.services.connected_followup_client import DEFAULT_LIMITS,digest,empty_head
    report_frozen,report_factory=frozen_report()
    report_root=tmp_path/'parent'; report_root.mkdir()
    report_outcome=ReportProcess(frozen=report_frozen,model_factory=report_factory,
        operation_root=report_root).run(checkpoint=lambda:None,first_call=lambda:None,
        progress=lambda _:None,cancelled=lambda:False)
    assert report_outcome.state=='completed'
    report_id=report_frozen['identity']['report_id']
    with report_files(report_root/'output'/report_id,report_outcome.manifest) as files:
        parent_bundle={name:bytes(raw) for name,raw in files.items()}
    full=parent_bundle['full_report.md'].decode()
    prefix=full[:15000]
    context=report_frozen['context']; native=context['binding']
    parent_receipt=dict(schema_version=1,report_id=report_id,
        plan_sha256=digest(report_frozen['identity']),context_sha256=digest(context),
        manifest_sha256=digest(report_outcome.manifest),output_language='en',
        reference_integrity='validated',semantic_support_status='not_reviewed')
    report=dict(report_id=report_id,plan_sha256=digest(report_frozen['identity']),
        receipt_sha256=digest(parent_receipt),manifest_sha256=digest(report_outcome.manifest),
        full_report_sha256=hashlib.sha256(parent_bundle['full_report.md']).hexdigest())
    binding=dict(display_graph_id=native['display_graph_id'],principal=native['principal'],
        scope=native['scope'],report=report,native_binding=native)
    history=dict(head_sha256=empty_head(report_id,report['plan_sha256']),
        total_completed=0,window_start=1,pairs=[])
    limits=dict(DEFAULT_LIMITS,max_run_seconds=120)
    identity=dict(schema_version=1,turn_id=str(uuid4()),binding=binding,
        options=dict(question='What do the admitted source and native records show?',
            output_language='en',expected_history_sha256=None),history=history,
        report_context=dict(file_sha256=report['full_report_sha256'],
            prefix_sha256=hashlib.sha256(prefix.encode()).hexdigest(),
            prefix_characters=len(prefix),total_characters=len(full),truncated=len(full)>15000),
        context_sha256=digest(context),source_projection_sha256=digest(context['graph']),
        model_label='scripted-local',limits=limits,ceiling_microusd=4)
    trace=tmp_path/'trace.jsonl'
    factory=BoundedReportModelFactory(FollowupTransportFactory(mode,str(trace)),'scripted-local',limits)
    frozen=dict(identity=identity,context=context,
        configuration=dict(account_id=str(uuid4()),factory_sha256=hashlib.sha256(pickle.dumps(factory)).hexdigest()),
        parent_manifest=report_outcome.manifest,
        parent_requirement=report_frozen['identity']['options']['requirement'])
    return frozen,factory,parent_bundle,report_root,trace


def test_actual_inherited_two_tools_private_sidecar_and_immutable_parent(tmp_path):
    from app.services.followup_process import FollowupProcess,followup_files
    from app.services.report_agent import ReportManager
    from app.utils.locale import get_locale
    frozen,factory,bundle,parent_root,trace=child_fixture(tmp_path)
    original={name:bytes(raw) for name,raw in bundle.items()}
    before=os.getcwd(),ReportManager.REPORTS_DIR,get_locale()
    root=tmp_path/'turn'; root.mkdir(); first=[]
    authority=[]; owner_thread=threading.get_ident()
    def reauthorize():
        assert threading.get_ident()==owner_thread
        authority.append([json.loads(line)['ordinal'] for line in trace.read_text().splitlines()] if trace.exists() else [])
    outcome=FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:False,reauthorize=reauthorize)
    assert outcome.state=='completed' and outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)
    assert first==[True] and (os.getcwd(),ReportManager.REPORTS_DIR,get_locale())==before
    with followup_files(root/'output',outcome.manifest) as content:
        answer=content['answer.md'].decode()
        assert '[[source:' in answer and '[[native:' in answer
        tools=json.loads(content['tool_trace.json'])['executed']
        assert [entry['tool'] for entry in tools]==['recorded_native_events','quick_search']
        conversation=json.loads(content['conversation.json'])
        assert conversation['pairs'][-1]['answer']==answer
        assert conversation['pairs'][-1]['receipt_sha256'] is None
        assert conversation['pairs'][-1]['published_head_sha256'] is None
        turn=json.loads(content['turn.json'])
        assert turn['publication_proof_scope']=='external_current_receipt_and_head'
        assert not set(turn)&{'receipt','manifest','published_history_head_sha256'}
    assert bundle==original
    for file in frozen['parent_manifest']['files']:
        assert (parent_root/'output'/frozen['identity']['binding']['report']['report_id']/file['name']).read_bytes()==original[file['name']]
    assert [json.loads(line)['ordinal'] for line in trace.read_text().splitlines()]==[1,2,3]
    assert authority==[[],[],[1],[1,2]]


@pytest.mark.parametrize('mode',['invented_reference','transport_failure'])
def test_actual_failed_child_does_not_publish(mode,tmp_path):
    from app.services.followup_process import FollowupProcess
    frozen,factory,bundle,_,trace=child_fixture(tmp_path,mode=mode)
    root=tmp_path/'turn'; root.mkdir(); first=[]
    outcome=FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:False,reauthorize=lambda:None)
    assert outcome.state in ('failed','uncertain') and outcome.manifest is None
    assert first==[True] and outcome.cleanup['known'] and not outcome.cleanup['pending']


def test_late_context_corruption_refuses_before_provider_factory(tmp_path):
    from app.services.followup_process import FollowupProcess
    frozen,factory,bundle,_,trace=child_fixture(tmp_path)
    frozen['context']['native_records']['reddit'][-1]['raw_json']='{"bad":true}'
    root=tmp_path/'turn'; root.mkdir(); first=[]
    outcome=FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:False,reauthorize=lambda:None)
    assert outcome.manifest is None and not first and not trace.exists()
    assert outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)


def test_cancellation_after_first_possible_request_keeps_owned_cleanup(tmp_path):
    from app.services.followup_process import FollowupProcess
    frozen,factory,bundle,_,_=child_fixture(tmp_path)
    root=tmp_path/'turn'; root.mkdir(); first=[]
    outcome=FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:bool(first),reauthorize=lambda:None)
    assert first==[True] and outcome.state=='cancelled' and outcome.manifest is None
    assert outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)


@pytest.mark.parametrize('code',['conflict','model_calls_disabled','followup_cancelled','timeout'])
def test_second_sdk_request_requires_fresh_owner_authority_before_transport(tmp_path,code):
    from app.services.followup_process import FollowupProcess
    from app.services.connected_followup_client import FollowupError
    frozen,factory,bundle,_,trace=child_fixture(tmp_path)
    root=tmp_path/'turn'; root.mkdir(); first=[]; checks=[]
    owner_thread=threading.get_ident()
    def reauthorize():
        assert threading.get_ident()==owner_thread
        checks.append(True)
        if len(checks)==3:  # READY, request 1, then request 2.
            assert [json.loads(line)['ordinal'] for line in trace.read_text().splitlines()]==[1]
            raise FollowupError(code)
    outcome=FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:False,reauthorize=reauthorize)
    assert first==[True] and len(checks)==3
    assert [json.loads(line)['ordinal'] for line in trace.read_text().splitlines()]==[1]
    assert outcome.state==('cancelled' if code=='followup_cancelled' else 'uncertain')
    assert outcome.error_code==code and outcome.manifest is None
    assert outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)


def protocol_child(connection,cancelled,frozen,factory,operation,bundle,prior,deadline):
    """Private malformed-frame fixture; performs no SDK work."""
    if os.name!='nt': os.setsid()
    connection.send(('ready',))
    assert connection.recv()==('checking','go') and connection.recv()==('go',)
    connection.send(('request',1))
    assert connection.recv()==('checking','admitted',1) and connection.recv()==('admitted',1)
    mode=factory.transport_factory.mode
    number={'duplicate_frame':1,'out_of_order_frame':3,'boolean_frame':True}[mode]
    connection.send(('request',number))
    while not cancelled.is_set(): cancelled.wait(0.1)
    connection.close()


@pytest.mark.parametrize('mode',['duplicate_frame','out_of_order_frame','boolean_frame'])
def test_numbered_owner_protocol_refuses_duplicate_skipped_or_boolean_requests(tmp_path,monkeypatch,mode):
    from app.services import followup_process as module
    frozen,factory,bundle,_,trace=child_fixture(tmp_path,mode=mode)
    root=tmp_path/'turn'; root.mkdir(); first=[]; checks=[]
    monkeypatch.setattr(module,'_child',protocol_child)
    outcome=module.FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:False,
        reauthorize=lambda:checks.append(True))
    assert first==[True] and len(checks)==2 and not trace.exists()
    assert outcome.state=='uncertain' and outcome.manifest is None
    assert outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)


@pytest.mark.parametrize('frames',[
    [('checking','admitted',1),('admitted',2)],
    [('checking','admitted',True),('admitted',1)],
    [('checking','admitted',1),('admitted',True)],
])
def test_child_admission_requires_exact_numbered_acknowledgments(frames):
    from app.services.followup_process import _await_control
    from app.services.connected_followup_client import FollowupError
    connection=SimpleNamespace(poll=lambda _:True,recv=lambda:frames.pop(0))
    with pytest.raises(FollowupError) as error:
        _await_control(connection,threading.Event(),'admitted',time.monotonic()+10,request_number=1)
    assert error.value.code=='followup_uncertain'


def test_child_ack_deadline_and_cancellation_refuse_before_go_or_request():
    from app.services.followup_process import _await_control
    from app.services.connected_followup_client import FollowupError
    def forbidden(*args): pytest.fail('expired or cancelled acknowledgment read the pipe')
    connection=SimpleNamespace(poll=forbidden,recv=forbidden)
    with pytest.raises(FollowupError) as error:
        _await_control(connection,threading.Event(),'go',time.monotonic()-1)
    assert error.value.code=='timeout'
    event=threading.Event(); event.set()
    with pytest.raises(FollowupError) as error:
        _await_control(connection,event,'admitted',time.monotonic()+10,request_number=1)
    assert error.value.code=='followup_cancelled'


def test_missing_owner_authority_callback_refuses_before_child_spawn(tmp_path):
    from app.services.followup_process import FollowupProcess
    frozen,factory,bundle,_,trace=child_fixture(tmp_path)
    root=tmp_path/'turn'; root.mkdir(); first=[]
    outcome=FollowupProcess(frozen=frozen,model_factory=factory,operation_root=root,
        parent_bundle=bundle,prior=[]).run(checkpoint=lambda:None,
        first_call=lambda:first.append(True),cancelled=lambda:False)
    assert not first and not trace.exists() and outcome.state=='uncertain' and outcome.manifest is None
    assert outcome.cleanup==dict(known=True,pending=False,owner_thread_alive=False)
