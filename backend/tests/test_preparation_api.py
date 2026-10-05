"""Actual Flask boundary, injected-result revalidation and cold import fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app import create_app
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.preparation_client import PreparationError, digest, validate_payload, validate_result
from test_durable_preparation import plan_request, status_request
from test_provider_neutral_preparation import SCOPE, uid


def planned(payload):
    result = {'schema_version': 1, 'display_graph_id': 'display-1', 'scope': SCOPE,
        'project_revision': 1, 'operation_id': payload['operation_id'],
        'source': {'source_revision': payload['source_revision'], 'source_name': 'retained', 'source_sha256': 'b' * 64},
        'options': payload['options'], 'actors': [{'source_entity_uuid': uid(10), 'name': 'Alice', 'labels': ['Person']}],
        'projection_sha256': 'c' * 64}
    result['plan_sha256'] = digest(result)
    result.update(state='planned', progress={'stage': 'planned', 'completed': 0, 'total': 100},
                  error_code=None, authorization={'model_calls_enabled': False, 'ceiling_microusd': None},
                  receipt=None, graph_snapshot_atomic=False, model_calls_started=False, simulation_executed=False)
    return result


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('MIROFISH_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
    token = '0123456789abcdef' * 4
    settings = ReadHostSettings('python', 'read_bootstrap.py', token, 'owner', 'display-1', SCOPE, {})
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: settings))
    class Facade:
        def __init__(self):
            self.calls, self.corrupt, self.last, self.denial = [], None, None, None
        def execute(self, method, graph_id, payload):
            self.calls.append((method, payload))
            if method == 'start' and self.denial is not None:
                raise PreparationError(self.denial)
            if method == 'plan':
                self.last = planned(payload)
            result = deepcopy(self.last)
            if self.corrupt:
                self.corrupt(result)
            return result
    facade = Facade()
    app = create_app(preparation_facade=facade)
    app.config['TESTING'] = True
    return app.test_client(), facade, {'Authorization': 'Bearer ' + token}, settings


def test_auth_origin_preflight_and_finite_plan(api):
    http, facade, headers, _ = api
    route = '/api/preparation/display-1/plan'
    request = plan_request()
    assert http.post(route, json=request).status_code == 401
    assert http.post(route, json=request, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert http.options(route, headers={'Origin': 'http://localhost:3000', 'Access-Control-Request-Method': 'DELETE'}).status_code == 403
    assert not facade.calls
    reply = http.post(route, json=request, headers=headers)
    assert reply.status_code == 200 and reply.json['data']['state'] == 'planned'
    assert reply.headers['Cache-Control'] == 'no-store'
    assert reply.headers['X-Content-Type-Options'] == 'nosniff'
    refresh = http.post('/api/preparation/display-1/status', json=status_request(reply.json['data']), headers=headers)
    assert refresh.status_code == 200
    assert http.post('/api/preparation/other/plan', json=request, headers=headers).status_code == 404


@pytest.mark.parametrize('code,status', [('model_calls_disabled', 409), ('budget_denied', 409),
                                      ('unauthorized', 401), ('origin_denied', 403)])
def test_actual_flask_policy_denial_preserves_plan_recovery(api, code, status):
    http, facade, headers, _ = api
    planned_reply = http.post('/api/preparation/display-1/plan', json=plan_request(), headers=headers)
    assert planned_reply.status_code == 200
    reference = status_request(planned_reply.json['data'])
    facade.denial = code
    denied = http.post('/api/preparation/display-1/start', json=reference, headers=headers)
    assert denied.status_code == status
    assert denied.json == {'success': False, 'error': {'code': code}}
    assert denied.headers['Cache-Control'] == 'no-store'
    assert denied.headers['X-Content-Type-Options'] == 'nosniff'
    recovered = http.post('/api/preparation/display-1/status', json=reference, headers=headers)
    assert recovered.status_code == 200 and recovered.json['data']['state'] == 'planned'
    assert status_request(recovered.json['data']) == reference


@pytest.mark.parametrize('body', [b'{}', b'{"schema_version":1,"schema_version":1}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff', b'[]', b'{' + b' ' * 65536 + b'}'],
                         ids=['empty', 'duplicate-key', 'nan', 'infinity', 'invalid-utf8', 'array', 'oversized-65538-bytes'])
def test_strict_json_and_size(api, body):
    http, facade, headers, _ = api
    reply = http.post('/api/preparation/display-1/plan', data=body, content_type='application/json', headers=headers)
    assert reply.status_code == 400 and reply.json['error']['code'] == 'invalid_request'
    assert not facade.calls


@pytest.mark.parametrize('mutate', [lambda p: p.update(project_id=uid(2)),
    lambda p: p.update(operation_id=uid(0xab).upper()),
    lambda p: p['options'].update(max_agents=True), lambda p: p['options'].update(seed=-1),
    lambda p: p['options'].update(types=['Entity']), lambda p: p['options'].update(types=['Person', 'Person']),
    lambda p: p['options'].update(platforms=['reddit', 'twitter']), lambda p: p['options'].update(max_rounds=25),
    lambda p: p['options'].update(simulation_requirement=' '), lambda p: p['options'].update(simulation_requirement='\ud800')])
def test_closed_payload_contract(api, mutate):
    http, facade, headers, _ = api
    payload = plan_request()
    mutate(payload)
    body = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode('ascii')
    reply = http.post('/api/preparation/display-1/plan', data=body,
                      content_type='application/json', headers=headers)
    assert reply.status_code == 400 and not facade.calls


@pytest.mark.parametrize('extra_headers', [{'Content-Encoding': 'gzip'}, {'Transfer-Encoding': 'chunked'}])
def test_encoded_or_chunked_rejected(api, extra_headers):
    http, facade, headers, _ = api
    reply = http.post('/api/preparation/display-1/plan', json=plan_request(), headers={**headers, **extra_headers})
    assert reply.status_code == 400 and not facade.calls


def test_query_params_rejected(api):
    http, facade, headers, _ = api
    assert http.post('/api/preparation/display-1/plan?x=1', json=plan_request(), headers=headers).status_code == 400
    assert not facade.calls


@pytest.mark.parametrize('mutate', [lambda v: v.update(source_text='PRIVATE'), lambda v: v.update(model_calls_started=0),
    lambda v: v.update(graph_snapshot_atomic=True), lambda v: v.update(simulation_executed=True),
    lambda v: v.update(plan_sha256='a' * 64), lambda v: v['actors'][0].update(name='changed'),
    lambda v: v.update(state='ready'), lambda v: v.update(receipt={'simulation_id': '../PRIVATE'}),
    lambda v: v['authorization'].update(ceiling_microusd='01')])
def test_injected_result_never_bypasses_schema(api, mutate):
    http, facade, headers, _ = api
    facade.corrupt = mutate
    reply = http.post('/api/preparation/display-1/plan', json=plan_request(), headers=headers)
    assert reply.status_code == 502 and reply.json['error']['code'] == 'invalid_reply'
    assert 'PRIVATE' not in reply.get_data(as_text=True)


def test_unconfigured_host_is_honest_and_cold(api):
    _, _, headers, settings = api
    app = create_app()
    reply = app.test_client().post('/api/preparation/display-1/plan', json=plan_request(), headers=headers)
    assert reply.status_code == 503 and reply.json['error']['code'] == 'preparation_unavailable'


def test_preparation_absent_from_readonly_mode(api, monkeypatch):
    _, _, headers, settings = api
    monkeypatch.setenv('MIROFISH_APP_MODE', 'graphiti_readonly')
    reply = create_app().test_client().post('/api/preparation/display-1/plan', json=plan_request(), headers=headers)
    assert reply.status_code == 404


def test_cold_preparation_surface_does_not_import_optional_engines():
    script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'temporalio','psycopg','openai','camel','oasis','zep_cloud','mirofish_execution','mirofish_storage'}:
        raise AssertionError('cold optional dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import app.preparation_api
import app.services.knowledge_preparation_facade
import app.services.preparation_client
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
