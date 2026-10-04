"""Lean HTTP boundaries; source-authored mocked DTOs, no live acceptance claim."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app import create_app
from app.config import Config
from app.services.knowledge_read_facade import ReadHostSettings
from test_native_experiment_http_contract import PROJECT, PIN, fixtures, selector


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setenv('MIROFISH_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST', str(tmp_path/'never-opened.json'))
    monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', PIN)
    token = '0123456789abcdef'*4
    scope = {'schema_version': 1, 'workspace_id': str(UUID(int=1)), 'project_id': PROJECT,
        'graph_id': str(UUID(int=3)), 'run_id': None, 'branch_id': None, 'layer': 'source'}
    pg = {f'KNOWLEDGE_PG_{k}': 'literal' for k in ('HOST', 'PORT', 'DATABASE', 'USER', 'PASSWORD')}
    settings = ReadHostSettings('python', 'read_bootstrap.py', token, 'owner', 'display', scope, pg)
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: settings))
    class LocalConfig(Config):
        DEBUG = False
        MIROFISH_ALLOWED_ORIGINS = ('http://localhost:3000',)
    class Facade:
        calls = []
        corrupt = None
        def execute(self, method, value):
            self.calls.append((method, value))
            cat, _, comp = fixtures()
            result = {'manifest_sha256': PIN, 'catalog': cat}
            if method == 'compare':
                result['comparison'] = comp
            if self.corrupt:
                self.corrupt(result)
            return result
    facade = Facade()
    app = create_app(LocalConfig, experiment_facade=facade)
    return app, app.test_client(), facade, {'Authorization': 'Bearer '+token}, settings, LocalConfig


def test_catalog_comparison_and_health(host):
    app, client, facade, auth, _, _ = host
    response = client.get('/api/experiments/catalog', headers=auth)
    assert response.status_code == 200
    assert response.json['data']['project_id'] == PROJECT
    response = client.post('/api/experiments/compare', headers=auth, json=selector())
    assert response.status_code == 200 and response.json['data']['members'][1]['seed'] == str(2**63-1)
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert len(facade.calls) == 2
    assert not any('experiment' in c for c in client.get('/health').json['capabilities'])


@pytest.mark.parametrize('method,path,options,status', [
    ('GET', '/api/experiments/catalog', {'headers': {}}, 401),
    ('GET', '/api/experiments/catalog', {'headers': {'Origin': 'https://foreign.example'}}, 403),
    ('POST', '/api/experiments/catalog', {}, 405),
    ('HEAD', '/api/experiments/catalog', {}, 400),
    ('GET', '/api/experiments/catalog?x=1', {}, 400),
    ('GET', '/api/experiments/catalog?', {}, 200),
    ('GET', '/api/experiments/catalog', {'data': b'{}'}, 400),
    ('POST', '/api/experiments/compare?x=1', {'json': selector()}, 400),
    ('POST', '/api/experiments/compare', {'data': b'{}'}, 400),
    ('POST', '/api/experiments/compare', {'data': b'{}', 'content_type': 'text/plain'}, 400),
    ('POST', '/api/experiments/compare', {'data': b'{"version":1,"title":"x","title":"x","member_ids":["one"]}', 'content_type': 'application/json'}, 400),
    ('POST', '/api/experiments/compare', {'data': b'{"version":1,"title":NaN,"member_ids":["one"]}', 'content_type': 'application/json'}, 400),
    ('POST', '/api/experiments/compare', {'data': b' '*8193, 'content_type': 'application/json'}, 400),
    ('POST', '/api/experiments/compare', {'json': {**selector(), 'DSN': 'secret'}}, 400),
    ('POST', '/api/experiments/compare', {'json': selector(), 'headers': {'Content-Encoding': 'gzip'}}, 400),
    ('POST', '/api/experiments/compare', {'json': selector(), 'headers': {'Transfer-Encoding': 'chunked'}}, 400),
])
def test_denials_before_facade(host, method, path, options, status):
    _, client, facade, auth, _, _ = host
    options = deepcopy(options)
    headers = options.pop('headers', auth)
    if headers and 'Authorization' not in headers:
        headers = {**auth, **headers}
    response = client.open(path, method=method, headers=headers, **options)
    assert response.status_code == status
    assert len(facade.calls) == (1 if status == 200 else 0)
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_preflight_actual_rules_without_auth_or_private_access(host):
    _, client, facade, _, _, _ = host
    headers = {'Origin': 'http://localhost:3000', 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'Authorization, Content-Type'}
    response = client.options('/api/experiments/compare', headers=headers)
    assert response.status_code == 204 and not facade.calls
    for path, altered in [('/api/experiments/catalog', headers),
            ('/api/experiments/compare', {**headers, 'Access-Control-Request-Headers': 'X-Principal'}),
            ('/api/experiments/compare', {**headers, 'Origin': 'null'})]:
        assert client.options(path, headers=altered).status_code == 403
    assert not facade.calls


@pytest.mark.parametrize('length', ['', '0', '-1', '01', '999', '8193'])
def test_missing_or_incorrect_content_length_precedes_facade(host, length):
    _, client, facade, auth, _, _ = host
    response = client.post('/api/experiments/compare', headers=auth,
        data=b'{}', content_type='application/json', environ_overrides={'CONTENT_LENGTH': length})
    assert response.status_code == 400 and not facade.calls


@pytest.mark.parametrize('config', ['absent', 'partial', 'hash', 'relative'])
def test_bad_optional_config_only_disables_experiments(host, monkeypatch, config):
    _, _, facade, auth, _, config_class = host
    if config == 'absent':
        monkeypatch.delenv('KNOWLEDGE_EXPERIMENT_MANIFEST')
        monkeypatch.delenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256')
    elif config == 'partial':
        monkeypatch.delenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256')
    elif config == 'hash':
        monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST_SHA256', PIN.upper())
    else:
        monkeypatch.setenv('KNOWLEDGE_EXPERIMENT_MANIFEST', 'relative.json')
    app = create_app(config_class)
    client = app.test_client()
    assert client.get('/health').status_code == 200
    assert client.get('/api/experiments/catalog', headers=auth).json == {'success': False, 'error': {'code': 'experiment_unavailable'}}
    assert client.get('/api/experiments/catalog', headers=auth).status_code == 503
    assert not facade.calls


def test_nonresearch_modes_omit_experiment_routes(host, monkeypatch):
    _, _, facade, auth, _, config_class = host
    monkeypatch.setenv('MIROFISH_APP_MODE', 'graphiti_readonly')
    app = create_app(config_class, experiment_facade=facade)
    assert app.test_client().get('/api/experiments/catalog', headers=auth).status_code == 404
    assert not facade.calls


def test_corrupt_injected_facade_reply_redacted(host):
    _, client, facade, auth, _, _ = host
    facade.corrupt = lambda v: v['catalog'].update(private_path='C:/private/password')
    response = client.get('/api/experiments/catalog', headers=auth)
    assert response.status_code == 503 and response.json == {'success': False, 'error': {'code': 'experiment_unavailable'}}
    assert b'private' not in response.data and b'password' not in response.data


def test_factory_denials_never_construct_facade(host, monkeypatch):
    _, _, _, auth, _, config_class = host
    import app.experiment_api as api
    def forbidden(*args, **kwargs):
        raise AssertionError('facade must not be created')
    monkeypatch.setattr(api, 'NativeExperimentFacade', forbidden)
    client = create_app(config_class).test_client()
    assert client.get('/api/experiments/catalog').status_code == 401
    assert client.post('/api/experiments/compare', headers=auth, json={}).status_code == 400
    assert client.get('/api/experiments/catalog?x=1', headers=auth).status_code == 400


def test_cold_experiment_module_import_has_no_native_or_provider_bootstrap():
    source = '''
import sys
import app.experiment_api
for name in ('mirofish_execution','psycopg','oasis','camel','graphiti_core','neo4j','openai','temporalio'):
    assert not any(m == name or m.startswith(name+'.') for m in sys.modules), name
'''
    proc = subprocess.run([sys.executable, '-c', source], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, timeout=20, check=False)
    assert proc.returncode == 0, proc.stderr.decode(errors='replace')
