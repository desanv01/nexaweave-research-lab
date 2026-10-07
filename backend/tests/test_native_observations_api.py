"""SDK-free transport and reply admission, independently authored evidence bytes."""
from copy import deepcopy
import hashlib
import json
from uuid import UUID
import pytest
from app import create_app
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.native_observations_client import (NativeObservationsError, validate_payload, validate_result,
    digest, output_names)

SCOPE = {'schema_version': 1, 'workspace_id': str(UUID(int=1)), 'project_id': str(UUID(int=2)),
         'graph_id': str(UUID(int=3)), 'layer': 'source', 'run_id': None, 'branch_id': None}


def completed():
    operation, run = str(UUID(int=4)), str(UUID(int=5))
    sim = 'sim_' + UUID(operation).hex
    identity = {'schema_version': 1, 'display_graph_id': 'display-1', 'scope': deepcopy(SCOPE),
        'preparation': {'operation_id': operation, 'plan_sha256': 'a' * 64, 'simulation_id': sim, 'artifact_sha256': 'b' * 64},
        'request': {'schema_version': 1, 'principal': 'owner', 'project_id': SCOPE['project_id'], 'project_revision': 1,
            'simulation_id': sim, 'run_id': run, 'artifact_sha256': 'b' * 64, 'runtime_sha256': 'c' * 64,
            'platforms': ['twitter', 'reddit'], 'seed': 7, 'max_rounds': 1},
        'limits': {'max_calls': 20, 'max_input_bytes': 262144, 'max_output_tokens': 4096, 'max_run_seconds': 120},
        'ceiling_microusd': '4', 'model_label': 'scripted'}
    manifest = {'schema_version': 1, 'files': [{'name': name, 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
                                            for name in output_names(['twitter', 'reddit'])]}
    dto = dict(identity, launch_sha256=digest(identity), state='completed', error_code=None,
        authorization={'model_calls_enabled': False}, workflow=None, cancel_requested=False,
        cleanup={'known': False, 'pending': None, 'owner_thread_alive': None},
        receipt={'run_id': run, 'attempt_id': str(UUID(int=6)), 'instance_id': str(UUID(int=7)),
                 'request_fingerprint': digest(identity['request']), 'outcome': 'completed', 'evidence_sha256': digest(manifest)})
    return dto, manifest


def payload(dto=None, **changes):
    dto = dto or completed()[0]
    return dict({'schema_version': 1, 'launch_id': dto['request']['run_id'], 'launch_sha256': dto['launch_sha256'],
                 'platform': 'twitter', 'offset': 0, 'limit': 20}, **changes)


def reply():
    launch, manifest = completed()
    return {'schema_version': 1, 'launch': launch, 'manifest': manifest, 'platform': 'twitter', 'offset': 0, 'limit': 20,
        'total_records': 0, 'next_offset': None, 'counts': {'event_records': 0, 'action_records': 0,
        'successful_action_records': 0, 'failed_action_records': 0}, 'records': []}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    settings = ReadHostSettings('python', 'read_bootstrap.py', '0123456789abcdef' * 4, 'owner', 'display-1', deepcopy(SCOPE), {})
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: settings))
    class Facade:
        def __init__(self):
            self.calls, self.value, self.error = [], reply(), None
        def execute(self, graph, request):
            self.calls.append((graph, request))
            if self.error:
                raise NativeObservationsError(self.error)
            return deepcopy(self.value)
    facade = Facade()
    return create_app(native_observations_facade=facade).test_client(), facade, {'Authorization': 'Bearer ' + settings.token}, settings


def test_protected_cold_mode_and_source_scope(api, monkeypatch):
    http, facade, headers, settings = api
    route = '/api/native-observations/page/display-1'
    assert http.post(route, json=payload()).status_code == 401
    assert http.post(route, json=payload(), headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert not facade.calls
    response = http.post(route, json=payload(), headers=headers)
    assert response.status_code == 200 and response.json['data'] == facade.value
    assert response.headers['Cache-Control'] == 'no-store' and response.headers['X-Content-Type-Options'] == 'nosniff'
    assert http.post('/api/native-observations/page/other', json=payload(), headers=headers).status_code == 404
    cold = create_app().test_client().post(route, json=payload(), headers=headers)
    assert cold.status_code == 503 and cold.json['error']['code'] == 'observations_unavailable'
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'graphiti_readonly')
    assert create_app(native_observations_facade=facade).test_client().post(route, json=payload(), headers=headers).status_code == 404
    settings.scope['layer'] = 'simulation'
    assert http.post(route, json=payload(), headers=headers).status_code == 401


def test_preflight_is_scoped_and_never_reads(api):
    http, facade, _, _ = api
    route = '/api/native-observations/page/display-1'
    headers = {'Origin': 'http://localhost:3000', 'Access-Control-Request-Method': 'POST',
               'Access-Control-Request-Headers': 'Authorization, Content-Type'}
    response = http.options(route, headers=headers)
    assert response.status_code == 204 and response.headers['Access-Control-Allow-Origin'] == headers['Origin']
    assert http.options(route, headers={**headers, 'Access-Control-Request-Method': 'DELETE'}).status_code == 403
    assert not facade.calls


@pytest.mark.parametrize('body', [b'{}', b'[]', b'\xff', b'{"a":NaN}', b'{"a":Infinity}',
    b'{"schema_version":1,"schema_version":1}', b'{' + b' ' * 4096 + b'}'],
    ids=['empty', 'array', 'utf8', 'nan', 'inf', 'dup', 'bound'])
def test_bad_transport_before_facade(api, body):
    http, facade, headers, _ = api
    response = http.post('/api/native-observations/page/display-1', data=body, content_type='application/json', headers=headers)
    assert response.status_code == 400 and not facade.calls


@pytest.mark.parametrize('changes', [{'schema_version': True}, {'launch_id': '../private'}, {'launch_sha256': 'A' * 64},
    {'platform': 'other'}, {'offset': True}, {'offset': -1}, {'offset': 10001}, {'limit': 0}, {'limit': 21}, {'path': 'private'}],
    ids=['bool-schema', 'uuid', 'sha', 'platform', 'bool-offset', 'negative', 'offset-bound', 'zero', 'limit-bound', 'extra'])
def test_exact_request(changes):
    request = payload(); request.update(changes)
    with pytest.raises(NativeObservationsError) as error:
        validate_payload(request)
    assert error.value.code == 'invalid_request'


@pytest.mark.parametrize('change', ['query', 'encoding', 'transfer', 'type'], ids=['query', 'encoding', 'transfer', 'type'])
def test_transport_metadata(api, change):
    http, facade, headers, _ = api
    route, content_type = '/api/native-observations/page/display-1', 'application/json'
    if change == 'query': route += '?offset=1'
    if change == 'encoding': headers = {**headers, 'Content-Encoding': 'gzip'}
    if change == 'transfer': headers = {**headers, 'Transfer-Encoding': 'chunked'}
    if change == 'type': content_type = 'text/plain'
    response = http.post(route, data=json.dumps(payload()), content_type=content_type, headers=headers)
    assert response.status_code == 400 and not facade.calls


@pytest.mark.parametrize('corruption', ['principal', 'receipt', 'manifest', 'file', 'order', 'size', 'offset', 'counts', 'next', 'state'],
                         ids=['owner', 'receipt', 'manifest', 'file', 'order', 'size', 'offset', 'counts', 'next', 'state'])
def test_untrusted_facade_reply(api, corruption):
    http, facade, headers, _ = api
    value = facade.value
    if corruption == 'principal': value['launch']['request']['principal'] = 'other'
    if corruption == 'receipt': value['launch']['receipt']['attempt_id'] = 'not-a-uuid'
    if corruption == 'manifest': value['launch']['receipt']['evidence_sha256'] = 'd' * 64
    if corruption == 'file': value['manifest']['files'][0]['name'] = '../private'
    if corruption == 'order': value['manifest']['files'].reverse()
    if corruption == 'size': value['manifest']['files'][0]['size'] = True
    if corruption == 'offset': value['offset'] = True
    if corruption == 'counts': value['counts']['action_records'] = 1
    if corruption == 'next': value['next_offset'] = 0
    if corruption == 'state': value['launch']['state'] = 'running'
    response = http.post('/api/native-observations/page/display-1', json=payload(), headers=headers)
    assert response.status_code == 502 and response.json == {'success': False, 'error': {'code': 'invalid_reply'}}


def test_rehashed_raw_numeric_and_count_correspondence():
    value = reply()
    raw = '{"action_type":"POST","success":true,"n":1.0,"e":1e2,"z":-0,"big":9007199254740993,"unknown":"猫😀"}'
    value.update(total_records=1, records=[{'index': 0, 'raw_json': raw, 'record_sha256': hashlib.sha256(raw.encode()).hexdigest()}])
    value['counts'].update(action_records=1, successful_action_records=1)
    file = value['manifest']['files'][1]
    file.update(size=len(raw.encode()), sha256=hashlib.sha256(raw.encode()).hexdigest())
    value['launch']['receipt']['evidence_sha256'] = digest(value['manifest'])
    assert validate_result(value, 'display-1', SCOPE, payload(), 'owner')['records'][0]['raw_json'] == raw
    for changed in ['hash', 'duplicate', 'count', 'index']:
        bad = deepcopy(value)
        if changed == 'hash': bad['records'][0]['record_sha256'] = '0' * 64
        if changed == 'duplicate':
            bad['records'][0]['raw_json'] = '{"a":1,"a":2}'
            bad['records'][0]['record_sha256'] = hashlib.sha256(bad['records'][0]['raw_json'].encode()).hexdigest()
        if changed == 'count': bad['counts']['successful_action_records'] = 0
        if changed == 'index': bad['records'][0]['index'] = 1
        with pytest.raises(NativeObservationsError): validate_result(bad, 'display-1', SCOPE, payload(), 'owner')


@pytest.mark.parametrize('category,displayed,unseen,counts', [
    ('event_records', '{}', '{"event_type":"end"}',
     {'event_records': 1, 'action_records': 0, 'successful_action_records': 0, 'failed_action_records': 0}),
    ('action_records', '{}', '{"action_type":"POST"}',
     {'event_records': 0, 'action_records': 1, 'successful_action_records': 0, 'failed_action_records': 0}),
    ('successful_action_records', '{"action_type":"POST"}', '{"action_type":"POST","success":true}',
     {'event_records': 0, 'action_records': 2, 'successful_action_records': 1, 'failed_action_records': 0}),
    ('failed_action_records', '{"action_type":"POST"}', '{"action_type":"POST","success":false}',
     {'event_records': 0, 'action_records': 2, 'successful_action_records': 0, 'failed_action_records': 1}),
], ids=['events', 'actions', 'successful', 'failed'])
def test_independently_rehashed_partial_page_unseen_count_boundary(category, displayed, unseen, counts):
    # A real two-line parser fixture, independently hashed here. Its first line
    # has zero records in the tested category; the one unseen line contributes one.
    # These bytes exercise DTO admission only, not connected engine qualification.
    value = reply()
    log = (displayed + '\n' + unseen + '\n').encode('utf-8')
    value.update(limit=1, total_records=2, next_offset=1, counts=deepcopy(counts), records=[{
        'index': 0, 'raw_json': displayed, 'record_sha256': hashlib.sha256(displayed.encode('utf-8')).hexdigest()}])
    value['manifest']['files'][1].update(size=len(log), sha256=hashlib.sha256(log).hexdigest())
    value['launch']['receipt']['evidence_sha256'] = digest(value['manifest'])
    request = payload(limit=1)
    admitted = validate_result(value, 'display-1', SCOPE, request, 'owner')
    assert admitted['counts'][category] == 1 and admitted['next_offset'] == 1
    impossible = deepcopy(value)
    impossible['counts'][category] = 2
    with pytest.raises(NativeObservationsError) as error:
        validate_result(impossible, 'display-1', SCOPE, request, 'owner')
    assert error.value.code == 'invalid_reply'


@pytest.mark.parametrize('raw', ['{"n":1' + '0' * 309 + '}', '{}\r', '{\r"n":1}', '{\n"n":1}'],
                         ids=['integer-overflow', 'trailing-cr', 'literal-cr', 'literal-lf'])
def test_rehashed_raw_still_requires_portable_number_and_line_admission(raw):
    value = reply()
    value.update(total_records=1, records=[{'index': 0, 'raw_json': raw, 'record_sha256': hashlib.sha256(raw.encode()).hexdigest()}])
    value['manifest']['files'][1].update(size=len(raw.encode()), sha256=hashlib.sha256(raw.encode()).hexdigest())
    value['launch']['receipt']['evidence_sha256'] = digest(value['manifest'])
    with pytest.raises(NativeObservationsError) as error: validate_result(value, 'display-1', SCOPE, payload(), 'owner')
    assert error.value.code == 'invalid_reply'


@pytest.mark.parametrize('code,status', [('conflict', 409), ('tombstoned', 410), ('busy', 503),
    ('result_too_large', 413), ('evidence_invalid', 502), ('observations_unavailable', 503)], ids=['conflict', 'gone', 'busy', 'large', 'evidence', 'cold'])
def test_closed_error_envelopes(api, code, status):
    http, facade, headers, _ = api; facade.error = code
    response = http.post('/api/native-observations/page/display-1', json=payload(), headers=headers)
    assert response.status_code == status and response.json == {'success': False, 'error': {'code': code}}
