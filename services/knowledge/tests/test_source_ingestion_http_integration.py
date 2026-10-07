"""Main: actual Flask/installed child/PG/Neo4j/SDK, canned local model replies.

No injected facade, provider, transport or store. Canned responses prove dispatch
and provenance controls, not semantic extraction quality or paid-model readiness.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from neo4j import GraphDatabase
from psycopg.conninfo import conninfo_to_dict
from nexaweave_execution.budget import BudgetLedger, migrate
from nexaweave_knowledge.operations import Ledger
from nexaweave_storage import SourceStore
from test_source_bridge_postgres import factory, owned_fixture
from test_integration_neo4j import fixture_data
from test_source_library_http_integration import _HTTP_CHILD, _events, _exchange

pytestmark = [pytest.mark.postgres, pytest.mark.neo4j]
ROOT = Path(__file__).resolve().parents[3]


def canned(schema):
    name = schema['name']
    values = {
        'ExtractedEntities': {'extracted_entities': [
            {'name': 'Mira Vale', 'entity_type_id': 1, 'episode_indices': [0]},
            {'name': 'Harbor Labs', 'entity_type_id': 2, 'episode_indices': [0]}]},
        'ExtractedEdges': {'edges': [{'source_entity_name': 'Mira Vale',
            'target_entity_name': 'Harbor Labs', 'relation_type': 'WORKS_FOR',
            'fact': 'Mira Vale works for Harbor Labs', 'valid_at': None,
            'invalid_at': None, 'episode_indices': [0]}]},
        'NodeResolutions': {'entity_resolutions': [
            {'id': 0, 'name': 'Mira Vale', 'duplicate_candidate_id': -1},
            {'id': 1, 'name': 'Harbor Labs', 'duplicate_candidate_id': -1}]},
        'EdgeDuplicate': {'duplicate_facts': [], 'contradicted_facts': []},
        'EntitySummary': {'summary': 'Synthetic fixture entity.'},
        'SummarizedEntities': {'summaries': []},
        'BatchEdgeTimestamps': {'timestamps': [{'valid_at': None, 'invalid_at': None}]},
        'EdgeTimestamps': {'valid_at': None, 'invalid_at': None},
    }
    if name in values:
        return values[name]
    if not schema['schema'].get('required'):
        return {key: None for key in schema['schema'].get('properties', {})}
    raise AssertionError('unexpected pinned Graphiti model schema: ' + name)


@contextmanager
def model_server():
    records, errors, state = [], [], {'fail': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                assert 0 < length <= 1024 * 1024
                body = json.loads(self.rfile.read(length))
                assert self.headers.get('Authorization') == 'Bearer explicit-local-dummy'
                assert body['model'] in {'fixture-generation', 'fixture-embedding'}
                records.append({'path': self.path, 'model': body['model']})
                if state['fail']:
                    self.send_error(500, 'synthetic unavailable')
                    return
                if self.path == '/v1/embeddings':
                    inputs = body['input']
                    if not isinstance(inputs, list) or not inputs or isinstance(inputs[0], int):
                        inputs = [inputs]
                    data = []
                    for index, value in enumerate(inputs):
                        digest = hashlib.sha256(str(value).encode()).digest()
                        data.append({'object': 'embedding', 'index': index,
                            'embedding': [(digest[i % 32] / 255) - .5 for i in range(1024)]})
                    reply = {'object': 'list', 'model': body['model'], 'data': data,
                             'usage': {'prompt_tokens': 1, 'total_tokens': 1}}
                else:
                    assert self.path == '/v1/chat/completions'
                    assert body['response_format']['type'] == 'json_schema'
                    schema = body['response_format']['json_schema']
                    records[-1]['schema'] = schema['name']
                    reply = {'id': 'local-synthetic', 'object': 'chat.completion',
                        'created': 1, 'model': body['model'], 'choices': [{'index': 0,
                        'message': {'role': 'assistant', 'content': json.dumps(canned(schema))},
                        'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                raw = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            except Exception as error:
                errors.append(type(error).__name__)
                self.send_error(500, 'synthetic protocol mismatch')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    original = server.get_request
    def get_request():
        connection, address = original(); connection.settimeout(5)
        return connection, address
    server.get_request = get_request
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': .05}, daemon=True)
    thread.start()
    try:
        yield server.server_port, records, errors, state
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        assert not thread.is_alive()


@contextmanager
def host(directory, scope, display, account, model_port, *, enabled=True):
    from tools.run_unit_tests import _unit_environment
    backend, python, bootstrap = (os.environ[k] for k in
        ('NEXAWEAVE_WORKBENCH_BACKEND_PYTHON', 'KNOWLEDGE_PYTHON', 'KNOWLEDGE_BOOTSTRAP_SCRIPT'))
    assert Path(backend).is_absolute() and Path(backend).is_file()
    assert Path(python).is_absolute() and Path(python).is_file()
    assert 'site-packages' in Path(bootstrap).parts
    assert Path(bootstrap).name == 'read_bootstrap.py'
    assert Path(bootstrap).with_name('source_ingestion_bootstrap.py').is_file()
    directory.mkdir()
    env = _unit_environment(directory)
    for key in list(env):
        if key.startswith(('KNOWLEDGE_', 'LLM_', 'OPENAI_', 'DEEPSEEK_', 'ZEP_')) or key.upper() in {
                'PYTHONPATH','PYTHONHOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                'PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGOPTIONS'}:
            env.pop(key, None)
    pg = conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    token = '0123456789abcdef' * 4
    env.update(SOURCE_HTTP_TEST_ROOT=str(ROOT), NEXAWEAVE_APP_MODE='research_local',
        PYTHON_DOTENV_DISABLED='1', FLASK_HOST='127.0.0.1', FLASK_DEBUG='0',
        NEXAWEAVE_ALLOWED_ORIGINS='http://localhost:3000', KNOWLEDGE_READ_TOKEN=token,
        KNOWLEDGE_PYTHON=python, KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap, KNOWLEDGE_PRINCIPAL='owner',
        KNOWLEDGE_DISPLAY_GRAPH_ID=display, KNOWLEDGE_BOUND_SCOPE_JSON=scope.model_dump_json(),
        KNOWLEDGE_PG_HOST=pg['host'], KNOWLEDGE_PG_PORT=pg['port'], KNOWLEDGE_PG_DATABASE=pg['dbname'],
        KNOWLEDGE_PG_USER=pg['user'], KNOWLEDGE_PG_PASSWORD=pg['password'],
        KNOWLEDGE_NEO4J_URI='bolt://127.0.0.1:17687', KNOWLEDGE_NEO4J_USER='neo4j',
        KNOWLEDGE_NEO4J_PASSWORD=os.environ['KNOWLEDGE_TEST_PASSWORD'],
        KNOWLEDGE_INGESTION_ENABLED='true' if enabled else 'false',
        KNOWLEDGE_MODEL_CALLS_AUTHORIZED='true', KNOWLEDGE_INGESTION_ACCOUNT_ID=str(account),
        KNOWLEDGE_INGESTION_CEILING_MICROUSD='4', KNOWLEDGE_OPERATING_PROFILE='local_only',
        KNOWLEDGE_LLM_BASE_URL=f'http://127.0.0.1:{model_port}/v1', KNOWLEDGE_LLM_MODEL='fixture-generation',
        KNOWLEDGE_LLM_API_KEY='explicit-local-dummy',
        KNOWLEDGE_EMBEDDING_BASE_URL=f'http://127.0.0.1:{model_port}/v1',
        KNOWLEDGE_EMBEDDING_MODEL='fixture-embedding', KNOWLEDGE_EMBEDDING_API_KEY='explicit-local-dummy',
        KNOWLEDGE_EMBEDDING_DIMENSION='1024', KNOWLEDGE_STRUCTURED_OUTPUT_MODE='json_schema',
        KNOWLEDGE_CALL_TIMEOUT_SECONDS='5', KNOWLEDGE_MAX_TOKENS='512',
        KNOWLEDGE_MAX_COROUTINES='1', KNOWLEDGE_TOTAL_LLM_CALL_BUDGET='32')
    # Observe the real fixed child; no facade/provider injection or script override.
    extra = {key for key in env if key.startswith('KNOWLEDGE_')} - {
        'KNOWLEDGE_PYTHON', 'KNOWLEDGE_BOOTSTRAP_SCRIPT', 'KNOWLEDGE_READ_TOKEN'}
    child = _HTTP_CHILD.replace("and Path(args[3]).name=='source_bootstrap.py'",
        "and Path(args[3]).name in {'source_bootstrap.py','source_ingestion_bootstrap.py'}")
    child = child.replace('and not set(env)-allowed)',
                          'and not set(env)-(allowed|' + repr(extra) + '))')
    spec = importlib.util.spec_from_file_location('main_source_ingestion_owner',
                                                 ROOT/'backend/app/utils/owned_process.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    owner, process = helper.OwnedProcess(), None
    output, errors = directory/'events.jsonl', directory/'private-errors.log'
    try:
        with output.open('wb') as out, errors.open('wb') as err:
            process = owner.start(subprocess.Popen, [backend,'-I','-u','-c',child],
                stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=directory,
                env=env, shell=False, close_fds=True)
            deadline = time.monotonic() + 20
            ready = []
            while time.monotonic() < deadline and process.poll() is None:
                ready = [e for e in _events(output) if e.get('event') == 'ready']
                if ready: break
                time.sleep(.05)
            assert ready, 'Main synthetic HTTP readiness failed; private diagnostics retained'
            yield ready[0]['port'], token, output
            process.stdin.write(b'x'); process.stdin.flush(); process.stdin.close()
            process.wait(timeout=10); assert process.returncode == 0
    finally:
        if process is not None and process.poll() is None:
            try:
                process.stdin.write(b'x'); process.stdin.flush(); process.stdin.close()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired): pass
        owner.stop([])
        assert owner.closed and (os.name != 'nt' or owner.tree_empty)
        if process is not None and process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
    events = _events(output)
    spawned = [e for e in events if e.get('event') == 'spawn']
    cleaned = [e for e in events if e.get('event') == 'cleanup']
    assert spawned and len(spawned) == len(cleaned) and all(e['safe'] for e in spawned)
    assert all(e['closed'] for e in cleaned)
    assert {'event':'shutdown','loopback_blocked':0} in events


@contextmanager
def neo_scope(scope):
    assert os.environ.get('KNOWLEDGE_INTEGRATION') == '1'
    password = os.environ['KNOWLEDGE_TEST_PASSWORD']
    assert password and password.lower() not in {'password','neo4j','test','changeme'}
    with GraphDatabase.driver('bolt://127.0.0.1:17687', auth=('neo4j',password)) as driver:
        try:
            yield driver
        finally:
            driver.execute_query('MATCH (n) WHERE n.group_id=$group DETACH DELETE n',
                                 group=scope.group_id)
            rows, _, _ = driver.execute_query('MATCH (n) WHERE n.group_id=$group RETURN count(n) AS n',
                                              group=scope.group_id, routing_='r')
            assert rows[0]['n'] == 0


def seed(factory):
    with factory() as connection: migrate(connection)
    display, scope, retained = owned_fixture(factory, text='Mira Vale works for Harbor Labs.')
    account = uuid4()
    BudgetLedger(factory).create_account('owner', scope.project_id, account, 10)
    ontology = fixture_data()[2]
    payload = {'schema_version':1, 'source_revision':str(retained.source_revision),
               'operation_id':str(uuid4()), 'ontology':ontology.model_dump(mode='json')}
    return display, scope, retained, account, payload


def test_real_production_http_sdk_ingestion_restart_and_denials(factory, tmp_path):
    display, scope, retained, account, payload = seed(factory)
    plan_url, execute_url = (f'/api/source/ingestion/{method}/{display}' for method in ('plan','execute'))
    status_url = f'/api/source/ingestion/operation/{display}/{payload["operation_id"]}'
    with neo_scope(scope) as driver, model_server() as (model_port, records, errors, state):
        with host(tmp_path/'disabled',scope,display,account,model_port,enabled=False) as (port,token,events):
            assert _exchange(port,'POST',plan_url,payload)[0] == 401
            assert _exchange(port,'POST',plan_url,payload,token=token,origin='https://denied.example')[0] == 403
            status, headers, planned = _exchange(port,'POST',plan_url,payload,token=token)
            assert status == 200 and planned['data']['model_calls_made'] is False
            assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
            assert planned['data']['source_sha256'] == retained.text_sha256
            assert _exchange(port,'POST',execute_url,payload,token=token)[2]['error']['code'] == 'model_calls_disabled'
            assert records == [] and errors == []
            assert BudgetLedger(factory).status('owner',account).remaining_microusd == 10
        with host(tmp_path/'enabled',scope,display,account,model_port) as (port,token,events):
            status, _, result = _exchange(port,'POST',execute_url,payload,token=token)
            assert status == 200, result
            data = result['data']
            assert data['state'] == 'completed' and data['budget_state'] == 'settled'
            assert data['model_calls_made'] is None and data['actual_usage_microusd'] is None
            assert data['receipt']['evidence_ids'] == [str(p.evidence_id) for p in retained.passages]
            assert data['fingerprint'] == planned['data']['fingerprint']
            assert records and errors == []
            assert {'fixture-generation','fixture-embedding'} == {r['model'] for r in records}
            before = len(records)
            assert _exchange(port,'GET',status_url,token=token)[2]['data'] == data
            assert _exchange(port,'POST',execute_url,payload,token=token)[2]['data'] == data
            assert len(records) == before
        with host(tmp_path/'restart',scope,display,account,model_port) as (port,token,events):
            assert _exchange(port,'POST',execute_url,payload,token=token)[2]['data'] == data
            assert _exchange(port,'GET',status_url,token=token)[2]['data'] == data
            assert len(records) == before
            changed = {**payload, 'ontology':fixture_data()[2].model_dump(mode='json')}
            assert _exchange(port,'POST',execute_url,changed,token=token)[0] == 409
            assert len(records) == before
        rows, _, _ = driver.execute_query(
            'MATCH (e:Episodic {uuid:$episode}), (m:MiroFishIngest {uuid:$episode}) '
            'RETURN e.group_id AS group_id, e.content AS content, m.status AS state, '
            'm.fingerprint AS fingerprint, m.evidence_ids AS evidence_ids',
            episode=data['episode_id'], routing_='r')
        assert len(rows) == 1 and rows[0]['group_id'] == scope.group_id
        assert rows[0]['content'] == retained.text and rows[0]['state'] == 'complete'
        assert rows[0]['fingerprint'] == data['fingerprint'] and rows[0]['evidence_ids'] == data['evidence_ids']
        facts, _, _ = driver.execute_query('MATCH ()-[r:RELATES_TO]->() WHERE r.group_id=$group RETURN r.fact AS fact',
                                          group=scope.group_id, routing_='r')
        assert any('Mira Vale works for Harbor Labs' in row['fact'] for row in facts)
        assert SourceStore(factory).get_source('owner',scope.project_id,retained.source_revision) == retained
        assert BudgetLedger(factory).status('owner',account).accounted_ceiling_microusd == 4


def test_real_sdk_failure_holds_money_and_never_retries(factory, tmp_path):
    display, scope, retained, account, payload = seed(factory)
    with neo_scope(scope), model_server() as (model_port, records, errors, state):
        state['fail'] = True
        with host(tmp_path/'failure',scope,display,account,model_port) as (port,token,events):
            route = f'/api/source/ingestion/execute/{display}'
            response = _exchange(port,'POST',route,payload,token=token)
            assert response[0] == 503 and response[2]['error']['code'] == 'uncertain'
            assert records and errors == []
            before = len(records)
            status = _exchange(port,'GET',f'/api/source/ingestion/operation/{display}/{payload["operation_id"]}',token=token)[2]['data']
            assert status['state'] == 'uncertain' and status['budget_state'] == 'uncertain'
            assert status['receipt'] is None and status['actual_usage_microusd'] is None
            assert _exchange(port,'POST',route,payload,token=token)[2]['error']['code'] == 'uncertain'
            assert len(records) == before
        assert BudgetLedger(factory).status('owner',account).uncertain_microusd == 4
        assert Ledger(factory).get(scope,UUID(payload['operation_id'])).receipt is None
