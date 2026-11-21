"""Actual protected Flask boundary; injected DTOs cannot bypass validation."""
from copy import deepcopy
import json
from uuid import uuid4, UUID
import pytest
from app import create_app
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.native_launch_client import NativeLaunchError, IDENTITY, IDENTITY_V2, digest, validate_result
from test_provider_neutral_preparation import SCOPE


def declaration():
    return {'schema_version':1,'launch_id':str(uuid4()),
            'preparation':{'operation_id':str(uuid4()),'plan_sha256':'a'*64}}


def reference(dto):
    return {'schema_version':dto['schema_version'],'launch_id':dto['request']['run_id'],
            'launch_sha256':dto['launch_sha256']}


def planned(payload):
    prep = dict(payload['preparation'],simulation_id='sim_'+UUID(payload['preparation']['operation_id']).hex,
                artifact_sha256='b'*64)
    value = {'schema_version':1,'display_graph_id':'display-1','scope':deepcopy(SCOPE),'preparation':prep,
        'request':{'schema_version':1,'principal':'owner','project_id':SCOPE['project_id'],'project_revision':1,
                   'simulation_id':prep['simulation_id'],'run_id':payload['launch_id'],'artifact_sha256':'b'*64,
                   'runtime_sha256':'c'*64,'platforms':['twitter','reddit'],'seed':7,'max_rounds':2},
        'limits':{'max_calls':10,'max_input_bytes':262144,'max_output_tokens':4096,'max_run_seconds':120},
        'ceiling_microusd':'4','model_label':'scripted 猫😀'}
    if payload['schema_version'] in (2, 4):
        version = payload['schema_version']
        value['schema_version'] = version
        value['checkpoint'] = deepcopy(payload['checkpoint'])
        value['request'].update(platforms=['reddit' if version == 4 else 'twitter'], max_rounds=1)
        if value['checkpoint']['mode'] == 'continue':
            value['request'].update(schema_version=version, source_run_id=value['checkpoint']['source_run_id'],
                                    checkpoint_sha256=value['checkpoint']['checkpoint_sha256'])
    value.update(launch_sha256=digest(value),state='planned',error_code=None,
                 authorization={'model_calls_enabled':False},workflow=None,receipt=None,cancel_requested=False,
                 cleanup={'known':False,'pending':None,'owner_thread_alive':None})
    return value


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('NEXAWEAVE_APP_MODE','research_local')
    monkeypatch.delenv('FLASK_HOST',raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS',raising=False)
    settings = ReadHostSettings('python','read_bootstrap.py','0123456789abcdef'*4,'owner','display-1',deepcopy(SCOPE),{})
    monkeypatch.setattr(ReadHostSettings,'from_config',classmethod(lambda cls,config:settings))
    class Facade:
        def __init__(self):
            self.calls,self.last,self.corrupt,self.denial = [],None,None,None
        def execute(self,method,graph_id,payload):
            self.calls.append((method,payload))
            if method=='plan':
                self.last=planned(payload)
            if method=='start' and self.denial:
                raise NativeLaunchError(self.denial)
            reply=deepcopy(self.last)
            if self.corrupt:
                self.corrupt(reply)
            return reply
    facade=Facade()
    app=create_app(native_launch_facade=facade)
    return app.test_client(),facade,{'Authorization':'Bearer '+settings.token},settings


def test_protected_auth_origin_scope_and_cold_host(api):
    http,facade,headers,settings=api
    route='/api/native-launch/plan/display-1'
    payload=declaration()
    assert http.post(route,json=payload).status_code==401
    assert http.post(route,json=payload,headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert http.options(route,headers={'Origin':'http://localhost:3000','Access-Control-Request-Method':'DELETE'}).status_code==403
    assert not facade.calls
    reply=http.post(route,json=payload,headers=headers)
    assert reply.status_code==200 and reply.headers['Cache-Control']=='no-store'
    assert reply.headers['X-Content-Type-Options']=='nosniff'
    assert http.post('/api/native-launch/plan/other',json=payload,headers=headers).status_code==404
    cold=create_app().test_client().post(route,json=payload,headers=headers)
    assert cold.status_code==503 and cold.json['error']['code']=='native_launch_unavailable'
    settings.scope['layer']='simulation'
    assert http.post(route,json=payload,headers=headers).status_code==401


@pytest.mark.parametrize('body',[b'{}',b'[]',b'\xff',b'{"a":NaN}',b'{"schema_version":1,"schema_version":1}',b'{'+b' '*4096+b'}'],
                         ids=['empty','array','utf8','nan','duplicate','large'])
def test_malformed_json_is_closed(api,body):
    http,facade,headers,_=api
    reply=http.post('/api/native-launch/plan/display-1',data=body,content_type='application/json',headers=headers)
    assert reply.status_code==400 and not facade.calls


@pytest.mark.parametrize('change',[{'schema_version':True},{'launch_id':'../PRIVATE'},{'run_id':str(uuid4())},
                                 {'path':'PRIVATE'},{'preparation':{'operation_id':str(uuid4()),'plan_sha256':'A'*64}}],
                         ids=['bool','path-id','extra-run','extra-path','sha'])
def test_exact_request_fields(api,change):
    http,facade,headers,_=api
    reply=http.post('/api/native-launch/plan/display-1',json=dict(declaration(),**change),headers=headers)
    assert reply.status_code==400 and not facade.calls


@pytest.mark.parametrize('method',['plan','start','status','cancel'])
def test_all_methods_refuse_query_encodings(api,method):
    http,facade,headers,_=api
    payload=declaration() if method=='plan' else reference(planned(declaration()))
    route='/api/native-launch/'+method+'/display-1'
    for suffix,extra in [('?x=1',{}),('',{'Content-Encoding':'gzip'}),('',{'Transfer-Encoding':'chunked'})]:
        assert http.post(route+suffix,json=payload,headers={**headers,**extra}).status_code==400
    assert not facade.calls


@pytest.mark.parametrize('code,status',[('model_calls_disabled',409),('budget_denied',409),('native_launch_uncertain',503)])
def test_policy_or_lost_reply_keeps_exact_status_recovery(api,code,status):
    http,facade,headers,_=api
    dto=http.post('/api/native-launch/plan/display-1',json=declaration(),headers=headers).json['data']
    ref=reference(dto)
    facade.denial=code
    denied=http.post('/api/native-launch/start/display-1',json=ref,headers=headers)
    assert denied.status_code==status and denied.json=={'success':False,'error':{'code':code}}
    recovered=http.post('/api/native-launch/status/display-1',json=ref,headers=headers)
    assert recovered.status_code==200 and reference(recovered.json['data'])==ref


@pytest.mark.parametrize('mutate',[
    lambda v:v.update(private_path='PRIVATE'),lambda v:v.update(launch_sha256='f'*64),
    lambda v:v['limits'].update(max_calls=True),lambda v:v['limits'].update(max_output_tokens=4097),
    lambda v:v.update(model_label='\ud800'),lambda v:v.update(ceiling_microusd='04'),
    lambda v:v.update(authorization={'model_calls_enabled':1}),
    lambda v:v.update(cleanup={'known':False,'pending':False,'owner_thread_alive':False}),
    lambda v:v.update(workflow={'workflow_id':'other','temporal_run_id':str(uuid4()),'native_run_id':v['request']['run_id']}),
    lambda v:v.update(state='completed'),lambda v:v['request'].update(seed=-1)],
    ids=['private','digest','bool','tokens','surrogate','ceiling','auth','cleanup','workflow','receiptless','seed'])
def test_untrusted_dto_is_revalidated_without_private_error(api,mutate):
    http,facade,headers,_=api
    facade.corrupt=mutate
    reply=http.post('/api/native-launch/plan/display-1',json=declaration(),headers=headers)
    assert reply.status_code==502 and reply.json['error']['code']=='invalid_reply'
    assert 'PRIVATE' not in reply.get_data(as_text=True)


def test_exact_native_receipt_and_cancellation_correspondence():
    payload=declaration(); dto=planned(payload)
    dto['state']='cancelled'; dto['cancel_requested']=True
    dto['receipt']={'run_id':payload['launch_id'],'attempt_id':str(uuid4()),'instance_id':str(uuid4()),
        'request_fingerprint':digest(dto['request']),'outcome':'cancelled','evidence_sha256':'e'*64}
    assert validate_result(dto,'display-1',SCOPE,reference(dto),'cancel')['receipt']==dto['receipt']
    for field,value in [('run_id',str(uuid4())),('request_fingerprint','a'*64),('outcome','completed')]:
        wrong=deepcopy(dto);wrong['receipt'][field]=value
        with pytest.raises(NativeLaunchError):
            validate_result(wrong,'display-1',SCOPE,reference(dto),'status')


def test_validly_hashed_other_principal_dto_cannot_cross_flask_authority(api):
    http,facade,headers,_=api
    def corrupt(value):
        value['request']['principal']='other'
        value['launch_sha256']=digest({k:value[k] for k in IDENTITY})
    facade.corrupt=corrupt
    reply=http.post('/api/native-launch/plan/display-1',json=declaration(),headers=headers)
    assert reply.status_code==502


def checkpoint_declaration(mode):
    checkpoint = {'mode': mode}
    if mode == 'continue':
        checkpoint.update(source_run_id=str(uuid4()), checkpoint_sha256='c' * 64)
    return dict(declaration(), schema_version=2, checkpoint=checkpoint)


@pytest.mark.parametrize('mode,request_version', [('capture', 1), ('continue', 2)])
def test_v2_checkpoint_plan_and_reference_round_trip(api, mode, request_version):
    http, facade, headers, _ = api
    route = '/api/native-launch/{}/display-1'
    payload = checkpoint_declaration(mode)
    response = http.post(route.format('plan'), json=payload, headers=headers)
    assert response.status_code == 200
    dto = response.json['data']
    assert dto['checkpoint'] == payload['checkpoint']
    assert dto['launch_sha256'] == digest({key: dto[key] for key in IDENTITY_V2})
    req = dto['request']
    assert req['schema_version'] == request_version
    assert req['platforms'] == ['twitter'] and req['max_rounds'] == 1
    if mode == 'continue':
        assert {key: req[key] for key in ('source_run_id', 'checkpoint_sha256')} == {
            key: dto['checkpoint'][key] for key in ('source_run_id', 'checkpoint_sha256')}
    else:
        assert 'source_run_id' not in req and 'checkpoint_sha256' not in req

    ref = reference(dto)
    start = http.post(route.format('start'), json=ref, headers=headers)
    assert start.status_code == 200 and start.json['data'] == dto
    run_id = req['run_id']
    facade.last.update(
        state='cancelled', cancel_requested=True,
        workflow={'workflow_id': 'mf-native-v1-' + UUID(run_id).hex + '-' + digest(req),
                  'temporal_run_id': str(uuid4()), 'native_run_id': run_id},
        receipt={'run_id': run_id, 'attempt_id': str(uuid4()), 'instance_id': str(uuid4()),
                 'request_fingerprint': digest(req), 'outcome': 'cancelled',
                 'evidence_sha256': 'd' * 64},
    )
    for method in ('status', 'cancel'):
        response = http.post(route.format(method), json=ref, headers=headers)
        assert response.status_code == 200 and response.json['data'] == facade.last
    assert facade.calls == [('plan', payload), ('start', ref), ('status', ref), ('cancel', ref)]


def test_v2_checkpoint_request_refuses_extra_and_unbound_fields(api):
    http, facade, headers, _ = api
    payload = checkpoint_declaration('continue')
    checkpoint = payload['checkpoint']
    for invalid in (
        {key: value for key, value in payload.items() if key != 'checkpoint'},
        dict(payload, schema_version=1),
        dict(payload, checkpoint={'mode': 'restore'}),
        dict(payload, checkpoint=dict(checkpoint, mode='capture')),
        dict(payload, checkpoint=dict(checkpoint, checkpoint_sha256='A' * 64)),
        dict(payload, checkpoint=dict(checkpoint, source_run_id=payload['launch_id'])),
        dict(payload, checkpoint=dict(checkpoint, path='PRIVATE')),
        dict(payload, checkpoint=dict(checkpoint, process_id=42)),
    ):
        reply = http.post('/api/native-launch/plan/display-1', json=invalid, headers=headers)
        assert reply.status_code == 400 and reply.json['error']['code'] == 'invalid_request'

    ref = reference(planned(payload))
    for method in ('start', 'status', 'cancel'):
        reply = http.post('/api/native-launch/' + method + '/display-1',
                          json=dict(ref, checkpoint=checkpoint), headers=headers)
        assert reply.status_code == 400 and reply.json['error']['code'] == 'invalid_request'
    assert not facade.calls


def test_v2_checkpoint_reply_refuses_misbound_identity_even_with_valid_digest(api):
    http, facade, headers, _ = api
    route = '/api/native-launch/{}/display-1'
    payload = checkpoint_declaration('continue')

    def change_source(dto):
        source = str(uuid4())
        dto['checkpoint']['source_run_id'] = source
        dto['request']['source_run_id'] = source

    for mutate in (
        change_source,
        lambda dto: dto['request'].update(checkpoint_sha256='e' * 64),
        lambda dto: dto['request'].update(schema_version=1),
        lambda dto: dto['request'].update(max_rounds=2),
        lambda dto: dto['request'].update(principal='other'),
        lambda dto: dto['checkpoint'].update(path='PRIVATE'),
    ):
        def corrupt(dto):
            mutate(dto)
            dto['launch_sha256'] = digest({key: dto[key] for key in IDENTITY_V2})

        facade.corrupt = corrupt
        reply = http.post(route.format('plan'), json=payload, headers=headers)
        assert reply.status_code == 502 and reply.json['error']['code'] == 'invalid_reply'
        assert 'PRIVATE' not in reply.get_data(as_text=True)

    facade.corrupt = None
    for plan in (declaration(), payload):
        dto = http.post(route.format('plan'), json=plan, headers=headers).json['data']
        wrong_version = dict(reference(dto), schema_version=3 - dto['schema_version'])
        reply = http.post(route.format('status'), json=wrong_version, headers=headers)
        assert reply.status_code == 502 and reply.json['error']['code'] == 'invalid_reply'


def test_reddit_checkpoint_protected_v4_keeps_exact_platform_and_lineage(api):
    http, facade, headers, _ = api
    route = '/api/native-launch/plan/display-1'
    capture = dict(declaration(), schema_version=4, checkpoint={'mode': 'capture'})
    response = http.post(route, json=capture, headers=headers)
    assert response.status_code == 200
    assert response.json['data']['request']['platforms'] == ['reddit']
    assert response.json['data']['request']['schema_version'] == 1
    restore = dict(declaration(), schema_version=4, checkpoint={
        'mode': 'continue', 'source_run_id': capture['launch_id'], 'checkpoint_sha256': 'e' * 64,
    })
    response = http.post(route, json=restore, headers=headers)
    assert response.status_code == 200
    assert response.json['data']['request']['schema_version'] == 4
    assert response.json['data']['request']['source_run_id'] == capture['launch_id']

    def mixed_platform(reply):
        reply['request']['platforms'] = ['twitter']
        reply['launch_sha256'] = digest({key: reply[key] for key in IDENTITY_V2})

    facade.corrupt = mixed_platform
    assert http.post(route, json=restore, headers=headers).status_code == 502
    bad = dict(restore, checkpoint=dict(restore['checkpoint'], mode='fork'))
    assert http.post(route, json=bad, headers=headers).status_code == 400
