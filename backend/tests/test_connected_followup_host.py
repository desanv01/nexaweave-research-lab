"""Protected old-turn reads, lost-start fencing and export size boundaries."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import UUID
import pytest
from test_connected_followup_client import completed, planned, request


def file_host(tmp_path):
    from app.services.durable_followup_host import DurableFollowupHost
    dto,answer,raws=completed()
    root=tmp_path/UUID(dto['turn_id']).hex/'output'
    root.mkdir(parents=True)
    for file,raw in zip(dto['manifest']['files'],raws):
        (root/file['name']).write_bytes(raw)
    row=SimpleNamespace(turn_id=UUID(dto['turn_id']),plan_sha256=dto['plan_sha256'],
        state='completed',manifest=dto['manifest'],receipt=dto['receipt'],
        frozen=dict(identity={key:dto[key] for key in ('schema_version','turn_id','binding','options',
            'history','report_context','context_sha256','source_projection_sha256',
            'model_label','limits','ceiling_microusd')}),
        public=lambda auth=None:deepcopy(dto))
    host=object.__new__(DurableFollowupHost)
    host.root,host.principal,host.display_graph_id,host.scope_dto=tmp_path,'owner','display-1',dto['binding']['scope']
    host.store=SimpleNamespace(get=lambda *args:row)
    host.authorization=lambda r=None:dict(model_calls_enabled=False,budget_configured=True)
    host._reauthorize=lambda r,**kw:r
    def forbidden(*args,**kw): pytest.fail('protected read touched model, poll, scheduler or budget mutation')
    host.model_factory=host.scheduler=host.authorize=forbidden
    host.store.recover_expired=host.store.queue=host.store.finish=host.store.cancel=forbidden
    return host,row,dto,root


def test_disabled_old_turn_read_and_all_six_exports_no_generation(tmp_path):
    from app.services.durable_followup_host import DurableFollowupHost
    from app.services.connected_followup_client import validate_download
    host,row,dto,root=file_host(tmp_path)
    ref=request(dto)
    assert DurableFollowupHost.read(host,ref)['content']==(root/'answer.md').read_text()
    for kind in ('answer','metadata','conversation','evidence','native_evidence','tools'):
        payload=request(dto,kind)
        response=DurableFollowupHost.download(host,payload)
        assert validate_download(response,payload,dto)==response


def test_changed_unselected_artifact_blocks_answer_and_metadata(tmp_path):
    from app.services.durable_followup_host import DurableFollowupHost
    from app.services.connected_followup_client import FollowupError
    host,row,dto,root=file_host(tmp_path)
    (root/'tool_trace.json').write_bytes(b'{"changed":true}')
    with pytest.raises(FollowupError): DurableFollowupHost.read(host,request(dto))
    with pytest.raises(FollowupError): DurableFollowupHost.download(host,request(dto,'metadata'))


def test_lost_start_never_redispatches_same_turn(tmp_path):
    from app.services.durable_followup_host import DurableFollowupHost
    from app.services.connected_followup_client import FollowupError
    dto=planned(); row=SimpleNamespace(turn_id=UUID(dto['turn_id']),plan_sha256=dto['plan_sha256'],
        state='planned',attempt_id=UUID(int=91),frozen=dict(identity=dto),public=lambda auth=None:deepcopy(dto))
    host=object.__new__(DurableFollowupHost)
    host.principal,host.display_graph_id,host.scope_dto,host.account_id='owner','display-1',dto['binding']['scope'],UUID(int=20)
    host._row=lambda _:row; host._reauthorize=lambda value:value
    host.authorization=lambda value=None:dict(model_calls_enabled=True,budget_configured=True)
    host.root=tmp_path
    report=dto['binding']['report']
    calls=[]
    def queue(*args):
        calls.append('claim'); row.state='queued'; return row,True
    def scheduler(wire):
        calls.append('schedule'); raise TimeoutError('lost reply')
    def finish(*args,**kwargs):
        calls.append('uncertain'); row.state='uncertain'; dto.update(state='uncertain',
            progress=dict(stage='uncertain',percent=0,completed_sections=0,total_sections=0),
            error_code='followup_uncertain',cleanup=kwargs['cleanup']); return row
    host.store=SimpleNamespace(queue=queue,finish=finish)
    host.scheduler=scheduler
    # The store's immutable planned history is read before queue. A local
    # preflight stub supplies a valid empty full-conversation snapshot.
    import app.services.durable_followup_host as module
    original=module.prior_conversation, module.reserve_conversation
    module.prior_conversation=lambda *args:[]
    module.reserve_conversation=lambda *args:None
    try:
        with pytest.raises(FollowupError) as error:
            host.start(request(dto))
        assert error.value.code=='followup_uncertain'
        assert host.start(request(dto))['state']=='uncertain'
    finally:
        module.prior_conversation,module.reserve_conversation=original
    assert calls==['claim','schedule','uncertain']


def parent_copy_fixture(tmp_path):
    from test_connected_report_client import public_result,fixture_context
    from app.services.connected_report_client import encoded,digest
    parent_dto,prose=public_result('completed')
    root=tmp_path/'parent'; root.mkdir()
    for file in parent_dto['manifest']['files']:
        raw=prose.encode() if file['name'].endswith('.md') else encoded(dict(schema_version=1))
        (root/file['name']).write_bytes(raw)
        file.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    parent_dto['receipt']['manifest_sha256']=digest(parent_dto['manifest'])
    parent=SimpleNamespace(state='completed',receipt=parent_dto['receipt'],manifest=parent_dto['manifest'],
        frozen=dict(identity=dict(binding=parent_dto['binding']),context=fixture_context()))
    reads=[]; checks=[]
    def read(payload):
        reads.append(payload)
        return deepcopy(parent)
    def reauthorize(row,**kwargs):
        checks.append(kwargs)
        if kwargs.get('tick') is not None: kwargs['tick']()
        return row
    host=SimpleNamespace(_row=read,_reauthorize=reauthorize,_output_root=lambda _:root)
    identity=planned()
    identity['binding']=dict(display_graph_id=parent_dto['binding']['display_graph_id'],principal='owner',
        scope=parent_dto['binding']['scope'],native_binding=parent_dto['binding'],
        report=dict(report_id=parent_dto['report_id'],plan_sha256=parent_dto['plan_sha256'],
            receipt_sha256=digest(parent.receipt),manifest_sha256=digest(parent.manifest),
            full_report_sha256=hashlib.sha256(prose.encode()).hexdigest()))
    identity['report_context']=dict(file_sha256=hashlib.sha256(prose.encode()).hexdigest(),
        prefix_sha256=hashlib.sha256(prose[:15000].encode()).hexdigest(),prefix_characters=len(prose[:15000]),
        total_characters=len(prose),truncated=len(prose)>15000)
    return host,parent,dict(identity=identity,context=fixture_context()),root,reads,checks


def test_verified_parent_returns_same_checked_bundle_and_preserves_two_authority_fences(tmp_path):
    from app.services.connected_followup_context import verify_frozen_parent
    host,parent,frozen,root,reads,checks=parent_copy_fixture(tmp_path)
    ticks=[]
    verified=verify_frozen_parent(host,frozen,tick=lambda:ticks.append(True))
    assert len(verified)==5 and verified[0]==parent
    assert verified[1]==frozen['identity']['binding'] and verified[2]==frozen['context']
    assert verified[3]==frozen['identity']['report_context']
    original=(root/'full_report.md').read_bytes()
    assert verified[4]['full_report.md']==original and len(reads)==2 and len(checks)==2 and ticks
    (root/'full_report.md').write_bytes(b'changed')
    assert verified[4]['full_report.md']==original
    from app.services.connected_followup_client import FollowupError
    with pytest.raises(FollowupError): verify_frozen_parent(host,frozen)


@pytest.mark.parametrize('field',['binding','context','provenance','context_sha256','source_projection_sha256'])
def test_verified_copy_never_returns_unmatched_frozen_inputs(tmp_path,field):
    from app.services.connected_followup_context import verify_frozen_parent
    from app.services.connected_followup_client import FollowupError
    host,_,frozen,_,_,_=parent_copy_fixture(tmp_path)
    if field=='binding': frozen['identity']['binding']['principal']='another-owner'
    elif field=='context': frozen['context']['source_text']='changed'
    elif field=='provenance': frozen['identity']['report_context']['total_characters']+=1
    else: frozen['identity'][field]='0'*64
    with pytest.raises(FollowupError) as error: verify_frozen_parent(host,frozen)
    assert error.value.code=='conflict'


@pytest.mark.parametrize('field',['state','receipt','manifest','frozen'])
def test_parent_journal_change_during_copy_is_refused(tmp_path,monkeypatch,field):
    from contextlib import contextmanager
    from app.services import connected_followup_context as module
    from app.services.connected_followup_client import FollowupError
    host,parent,frozen,_,reads,checks=parent_copy_fixture(tmp_path)
    original=module.report_files
    @contextmanager
    def mutate_after_copy(*args):
        with original(*args) as content: yield content
        if field=='state': parent.state='failed'
        elif field=='receipt': parent.receipt['context_sha256']='0'*64
        elif field=='manifest': parent.manifest['files'][0]['sha256']='0'*64
        else: parent.frozen['context']['source_text']='changed'
    monkeypatch.setattr(module,'report_files',mutate_after_copy)
    with pytest.raises(FollowupError) as error: module.verify_frozen_parent(host,frozen)
    assert error.value.code=='conflict' and len(reads)==2 and len(checks)==1


@pytest.mark.parametrize('code,expected',[('timeout','timeout'),('unauthorized','unauthorized'),
    ('model_calls_disabled','model_calls_disabled'),('report_cancelled','followup_cancelled'),
    ('report_uncertain','followup_uncertain')])
def test_parent_report_control_errors_preserve_followup_meaning(tmp_path,code,expected):
    from app.services.connected_followup_context import verify_frozen_parent
    from app.services.connected_report_client import ReportError
    from app.services.connected_followup_client import FollowupError
    host,_,frozen,_,_,_=parent_copy_fixture(tmp_path)
    def refuse(*args,**kwargs): raise ReportError(code)
    host._reauthorize=refuse
    with pytest.raises(FollowupError) as error: verify_frozen_parent(host,frozen)
    assert error.value.code==expected


def test_followup_tick_cancellation_and_expired_copy_do_not_become_conflict(tmp_path,monkeypatch):
    from app.services import connected_followup_context as module
    from app.services.connected_followup_client import FollowupError
    from app.services.knowledge_transport import KnowledgeCooperativeAbort
    host,_,frozen,_,_,_=parent_copy_fixture(tmp_path)
    assert KnowledgeCooperativeAbort('followup_cancelled').code=='followup_cancelled'
    def cancelled(): raise FollowupError('followup_cancelled')
    with pytest.raises(FollowupError) as error: module.verify_frozen_parent(host,frozen,tick=cancelled)
    assert error.value.code=='followup_cancelled'
    monkeypatch.setattr(module.time,'monotonic',lambda:10)
    with pytest.raises(FollowupError) as error: module.verify_frozen_parent(host,frozen,deadline=10)
    assert error.value.code=='timeout'


@pytest.mark.parametrize('code',['followup_cancelled','followup_uncertain','model_calls_disabled','unauthorized'])
def test_parent_projection_private_abort_preserves_tick_control_code(tmp_path,code):
    from app.services.connected_followup_context import verify_frozen_parent
    from app.services.connected_report_client import ReportError
    from app.services.connected_followup_client import FollowupError
    from app.services.knowledge_transport import KnowledgeCooperativeAbort
    host,_,frozen,_,_,_=parent_copy_fixture(tmp_path)
    projecting=[False]
    def tick():
        if projecting[0]: raise FollowupError(code)
    def project(row,**kwargs):
        projecting[0]=True
        try:
            try: kwargs['tick']()
            except BaseException as error:
                raise KnowledgeCooperativeAbort(error.code) from None
        except KnowledgeCooperativeAbort as error:
            raise ReportError(error.code) from None
        return row
    host._reauthorize=project
    with pytest.raises(FollowupError) as error: verify_frozen_parent(host,frozen,tick=tick)
    assert error.value.code==code


def test_parent_copy_lease_exit_cannot_cross_whole_deadline(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from app.services import connected_followup_context as module
    from app.services.connected_followup_client import FollowupError
    host,_,frozen,_,reads,checks=parent_copy_fixture(tmp_path)
    clock=[0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    original=module.report_files
    @contextmanager
    def expire_after_copy(*args):
        with original(*args) as content: yield content
        clock[0]=10
    monkeypatch.setattr(module,'report_files',expire_after_copy)
    with pytest.raises(FollowupError) as error: module.verify_frozen_parent(host,frozen,deadline=10)
    assert error.value.code=='timeout' and len(reads)==1 and len(checks)==1


@pytest.mark.parametrize('expire_final',[False,True])
def test_generation_reuses_verified_tuple_keeps_fresh_fences_and_checks_final_lease(tmp_path,monkeypatch,expire_final):
    from contextlib import contextmanager
    import json
    from test_connected_report_client import fixture_context
    from app.services import durable_followup_host as module
    from app.services.connected_followup_client import IDENTITY,encoded
    from app.services.followup_process import ProcessOutcome
    dto,answer,raws=completed()
    identity={key:deepcopy(dto[key]) for key in IDENTITY}
    row=SimpleNamespace(turn_id=UUID(dto['turn_id']),plan_sha256=dto['plan_sha256'],cancel_requested=False,
        frozen=dict(identity=identity,context=fixture_context()),public=lambda auth=None:dict(state='completed'))
    host=object.__new__(module.DurableFollowupHost)
    host.root,host.principal,host.model_factory=tmp_path,'owner',None
    host._row=lambda _:row
    host.authorization=lambda current:dict(model_calls_enabled=True,budget_configured=True)
    clock=[0]; deadline=[]; verified=[]; first=[]; committed=[]; finishes=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    parent=SimpleNamespace(manifest={'parent':'manifest'},frozen=dict(identity=dict(options=dict(requirement='bound'))))
    bundle={'native_evidence.json':b'{}','retrieval_evidence.json':b'{"retrievals":[]}'}
    def verification(current,*,deadline,tick):
        assert current is row and callable(tick)
        tick(); verified.append(True)
        return parent,identity['binding'],row.frozen['context'],identity['report_context'],bundle
    host._verified_parent=verification
    def forbidden(*args,**kwargs): pytest.fail('generation performed a second unverified parent copy')
    monkeypatch.setattr(module,'freeze_parent',forbidden)
    monkeypatch.setattr(module,'prior_conversation',lambda *args:[])
    class Process:
        def __init__(self,**kwargs):
            assert kwargs['parent_bundle'] is bundle
            assert kwargs['frozen']['parent_manifest'] is parent.manifest
        def run(self,**kwargs):
            deadline.append(kwargs['deadline'])
            kwargs['reauthorize']()  # READY
            for number in range(1,4):
                kwargs['reauthorize']()  # Each SDK request
                if number==1: kwargs['first_call']()
            return ProcessOutcome('completed',dict(known=True,pending=False,owner_thread_alive=False),None,dto['manifest'])
    monkeypatch.setattr(module,'FollowupProcess',Process)
    content=dict(zip((file['name'] for file in dto['manifest']['files']),raws))
    content['turn.json']=encoded(dict(identity=identity,publication_proof_scope='external_current_receipt_and_head'))
    content['retrieval_evidence.json']=bundle['retrieval_evidence.json']
    content['tool_trace.json']=encoded(dict(schema_version=1,executed=[]))
    leases=[]
    @contextmanager
    def files(*args):
        leases.append(True); yield content
        if expire_final and len(leases)==2: clock[0]=deadline[0]
    monkeypatch.setattr(module,'followup_files',files)
    def finish(*args,**kwargs):
        finishes.append(kwargs['state'])
        if kwargs['state']=='completed':
            kwargs['verify_output'](); committed.append(True)
        else:
            assert kwargs['error_code']=='timeout'
        return row
    host.store=SimpleNamespace(claim=lambda *args:row,first_request=lambda *args:first.append(True),finish=finish)
    wire=dict(schema_version=1,turn_id=dto['turn_id'],plan_sha256=dto['plan_sha256'],attempt_id=str(UUID(int=99)))
    host.generate(wire,heartbeat=lambda:None,cancelled=lambda:False)
    assert len(verified)==7 and first==[True] and len(leases)==2
    assert committed==([] if expire_final else [True])
    assert finishes==(['completed','uncertain'] if expire_final else ['completed'])
