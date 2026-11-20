"""Main: actual Flask/installed child/PG/Neo4j/SDK, canned local model replies.

No injected facade, provider, transport or store. Canned responses prove dispatch
and provenance controls, not semantic extraction quality or paid-model readiness.
"""
import asyncio
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from neo4j import GraphDatabase
from psycopg.conninfo import conninfo_to_dict
from nexaweave_execution.budget import BudgetLedger, migrate
from nexaweave_knowledge.operations import Ledger
from nexaweave_storage import SourceStore
from test_source_bridge_postgres import factory, owned_fixture
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
def model_server(*, generation_only=False):
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
                if generation_only:
                    assert self.path == '/v1/chat/completions' and body['model'] == 'fixture-generation'
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
                    records[-1]['saved_example'] = any(
                        'Fixture saved example for Mira Vale' in message.get('content', '')
                        for message in body['messages'] if isinstance(message.get('content'), str))
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
def host(directory, scope, display, account, model_port, *, enabled=True, ceiling=4,
         embedding_port=None, omit_embedding_endpoint=False, closed_model_endpoints=False):
    from tools.run_unit_tests import _unit_environment
    assert type(ceiling) is int and 1 <= ceiling <= 4
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
        KNOWLEDGE_INGESTION_CEILING_MICROUSD=str(ceiling), KNOWLEDGE_OPERATING_PROFILE='local_only',
        KNOWLEDGE_LLM_BASE_URL=f'http://127.0.0.1:{model_port}/v1', KNOWLEDGE_LLM_MODEL='fixture-generation',
        KNOWLEDGE_LLM_API_KEY='explicit-local-dummy',
        KNOWLEDGE_EMBEDDING_BASE_URL=f'http://127.0.0.1:{model_port}/v1',
        KNOWLEDGE_EMBEDDING_MODEL='fixture-embedding', KNOWLEDGE_EMBEDDING_API_KEY='explicit-local-dummy',
        KNOWLEDGE_EMBEDDING_DIMENSION='1024', KNOWLEDGE_STRUCTURED_OUTPUT_MODE='json_schema',
        KNOWLEDGE_CALL_TIMEOUT_SECONDS='5', KNOWLEDGE_MAX_TOKENS='512',
        KNOWLEDGE_MAX_COROUTINES='1', KNOWLEDGE_TOTAL_LLM_CALL_BUDGET='32')
    if embedding_port is not None:
        assert type(embedding_port) is int and 1 <= embedding_port <= 65535
        assert (embedding_port == model_port) == closed_model_endpoints
        env.update(KNOWLEDGE_EMBEDDING_BASE_URL=f'http://127.0.0.1:{embedding_port}/v1',
                   KNOWLEDGE_EMBEDDING_MODEL='qwen3-embedding-0.6b-q8',
                   KNOWLEDGE_SEARCH_RECIPE='hybrid_rrf')
        if omit_embedding_endpoint:
            env.pop('KNOWLEDGE_EMBEDDING_BASE_URL')
    else:
        assert not omit_embedding_endpoint and not closed_model_endpoints
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



@contextmanager
def browser_two_hop_seed(scope_dto, ingestion):
    from nexaweave_knowledge.contracts import KnowledgeScope
    from neo4j import Query

    scope = KnowledgeScope.model_validate(scope_dto)
    assert os.environ.get('KNOWLEDGE_INTEGRATION') == '1'
    password = os.environ['KNOWLEDGE_TEST_PASSWORD']
    assert password and password.lower() not in {'password', 'neo4j', 'test', 'changeme'}
    first, second = ingestion[:2]
    assert all(item['state'] == 'completed' and item['scope'] == scope_dto for item in (first, second))
    episodes = [item['episode_id'] for item in (first, second)]
    evidence = [sorted(item['evidence_ids']) for item in (first, second)]
    assert len(set(episodes)) == 2 and all(1 <= len(ids) <= 100 for ids in evidence)
    nodes = [str(uuid4()) for _ in range(3)]
    edges = [str(uuid4()) for _ in range(2)]
    with GraphDatabase.driver('bolt://127.0.0.1:17687', auth=('neo4j', password), connection_timeout=3,
                              connection_acquisition_timeout=3, max_transaction_retry_time=0) as driver:
        def run(query, **parameters):
            return driver.execute_query(Query(query, timeout=3), group=scope.group_id, **parameters)

        try:
            run(
                "CREATE (a:Entity {uuid:$nodes[0],group_id:$group,name:'Cited path start',"
                "summary:'',created_at:datetime()}), "
                "(b:Entity {uuid:$nodes[1],group_id:$group,name:'Cited path midpoint',"
                "summary:'',created_at:datetime()}), "
                "(c:Entity {uuid:$nodes[2],group_id:$group,name:'Cited path destination',"
                "summary:'',created_at:datetime()}) "
                "CREATE (a)-[:RELATES_TO {uuid:$edges[0],group_id:$group,name:'RELATES_TO',"
                "fact:'The start leads to the midpoint.',episodes:[$episodes[0]],"
                "created_at:datetime(),valid_at:datetime()}]->(b), "
                "(b)-[:RELATES_TO {uuid:$edges[1],group_id:$group,name:'RELATES_TO',"
                "fact:'The midpoint leads to the destination.',episodes:[$episodes[1]],"
                "created_at:datetime(),valid_at:datetime()}]->(c)",
                nodes=nodes, edges=edges, episodes=episodes,
            )
            yield {'nodes': nodes, 'edges': edges, 'evidence': evidence}
        finally:
            try:
                run('MATCH ()-[r:RELATES_TO]->() WHERE r.group_id=$group AND r.uuid IN $ids DELETE r', ids=edges)
            finally:
                run('MATCH (n:Entity) WHERE n.group_id=$group AND n.uuid IN $ids DELETE n', ids=nodes)


def verify_browser_two_hop(outputs, seed, graph, scope):
    assert len(outputs) == 2, 'expected exactly two successful protected two-hop reads'
    found, reverse = outputs
    source, middle, target = seed['nodes']
    for result in outputs:
        scan = result['scope_coverage']
        assert result['schema_version'] == 2 and result['operation'] == 'two_hop'
        assert result['historical'] is False and scan['display_graph_id'] == graph and scan['scope'] == scope
        assert 2 <= scan['scanned'] <= 500 and type(scan['truncated']) is bool
    assert (found['source_node_id'], found['target_node_id']) == (source, target)
    assert found['path_status'] == 'found' and found['scope_coverage']['returned'] == 2
    assert found['path']['intermediate_node_id'] == middle
    hops = found['path']['hops']
    assert [(hop['source_node_id'], hop['target_node_id']) for hop in hops] == [(source, middle), (middle, target)]
    assert [hop['provider_id'] for hop in hops] == seed['edges']
    for hop, evidence in zip(hops, seed['evidence'], strict=True):
        assert hop['evidence_ids'] == evidence == [item['evidence_id'] for item in hop['citations']]
    assert (reverse['source_node_id'], reverse['target_node_id']) == (target, source)
    assert reverse['path_status'] == 'no_path_in_scan' and reverse['path'] is None
    assert reverse['scope_coverage']['returned'] == 0 and reverse['passage_coverage'] == []


def seed(factory):
    from test_integration_neo4j import fixture_data
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
        ontology_url = f'/api/source/ontology/{display}'
        reviewed = {key: deepcopy(payload['ontology'][key]) for key in ('entity_types', 'edge_types')}
        for kind in ('entity_types', 'edge_types'):
            for definition in reviewed[kind]:
                definition['attributes'] = [{'name':'fixture_note', 'type':'text',
                                             'description':'A short fixture note'}]
        with host(tmp_path/'restart',scope,display,account,model_port) as (port,token,events):
            assert _exchange(port,'POST',execute_url,payload,token=token)[2]['data'] == data
            assert _exchange(port,'GET',status_url,token=token)[2]['data'] == data
            assert len(records) == before
            changed = {**payload, 'ontology':{**payload['ontology'], 'revision':str(uuid4())}}
            assert _exchange(port,'POST',execute_url,changed,token=token)[0] == 409
            assert len(records) == before
            current = _exchange(port,'GET',ontology_url,token=token)[2]['data']
            save = {'schema_version':1, 'expected_revision':current['project_revision'], 'ontology':reviewed}
            saved_status, _, saved_reply = _exchange(port,'POST',ontology_url,save,token=token)
            assert saved_status == 200, saved_reply
            saved = saved_reply['data']
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

        saved_operation = uuid4()
        saved_status_url = f'/api/source/ingestion/operation/{display}/{saved_operation}'
        ledger = BudgetLedger(factory)
        with host(tmp_path/'saved-reopen',scope,display,account,model_port) as (port,token,events):
            assert _exchange(port,'GET',ontology_url,token=token)[2]['data'] == saved
            selector = {'schema_version':4, 'source_revision':str(retained.source_revision),
                        'project_revision':saved['project_revision'], 'operation_id':str(saved_operation)}
            baseline_budget = ledger.status('owner',account)
            planned_status, _, planned_reply = _exchange(port,'POST',plan_url,selector,token=token)
            assert planned_status == 200, planned_reply
            draft = planned_reply['data']
            assert draft['schema_version'] == 4 and draft['project_revision'] == selector['project_revision']
            assert draft['fingerprint'] != draft['execution_fingerprint']
            assert draft['spending_authorized'] is draft['model_calls_made'] is False
            stale = dict(selector, fingerprint=draft['fingerprint'], execution_fingerprint=draft['execution_fingerprint'])

            outside = deepcopy(reviewed)
            outside['entity_types'][0]['attributes'][0]['description'] = 'a' * 501
            outside_save = dict(save, expected_revision=saved['project_revision'], ontology=outside)
            outside_status, _, outside_reply = _exchange(port,'POST',ontology_url,outside_save,token=token)
            assert outside_status == 200, outside_reply
            outside_selector = dict(selector, project_revision=outside_reply['data']['project_revision'])
            outside_execute = dict(stale, project_revision=outside_selector['project_revision'])
            for url, body in ((plan_url, outside_selector), (execute_url, outside_execute)):
                refused, _, refusal = _exchange(port,'POST',url,body,token=token)
                assert refused == 422
                assert refusal == {'success':False, 'error':{'code':'ontology_ineligible'}}
            assert _exchange(port,'POST',execute_url,stale,token=token)[0] == 409
            assert _exchange(port,'GET',saved_status_url,token=token)[0] == 404
            assert len(records) == before and ledger.status('owner',account) == baseline_budget
            with factory() as conn:
                assert ledger._row(conn, account, saved_operation) is None

            revised = deepcopy(reviewed)
            revised['entity_types'][0]['examples'] = ['Fixture saved example for Mira Vale']
            revised['edge_types'][0]['source_targets'].append({'source':'Entity', 'target':'Organization'})
            save = dict(save, expected_revision=outside_selector['project_revision'], ontology=revised)
            updated_status, _, updated_reply = _exchange(port,'POST',ontology_url,save,token=token)
            assert updated_status == 200, updated_reply
            assert updated_reply['data']['ontology'] == revised
            assert _exchange(port,'POST',execute_url,stale,token=token)[0] == 409
            assert _exchange(port,'GET',saved_status_url,token=token)[0] == 404
            assert len(records) == before and ledger.status('owner',account) == baseline_budget
            with factory() as conn:
                assert ledger._row(conn, account, saved_operation) is None

            selector['project_revision'] = updated_reply['data']['project_revision']
            draft_status, _, draft_reply = _exchange(port,'POST',plan_url,selector,token=token)
            assert draft_status == 200, draft_reply
            current_draft = draft_reply['data']
            assert all(current_draft[key] != draft[key] for key in ('fingerprint','execution_fingerprint'))
            execute = dict(selector, fingerprint=current_draft['fingerprint'],
                           execution_fingerprint=current_draft['execution_fingerprint'])
            for key in ('fingerprint','execution_fingerprint'):
                assert _exchange(port,'POST',execute_url,dict(execute, **{key: '0' * 64}),token=token)[0] == 409
            assert _exchange(port,'GET',saved_status_url,token=token)[0] == 404
            assert len(records) == before and ledger.status('owner',account) == baseline_budget
            finished_status, _, finished_reply = _exchange(port,'POST',execute_url,execute,token=token)
            assert finished_status == 200, finished_reply
            finished = finished_reply['data']
            assert finished['schema_version'] == 1 and finished['state'] == 'completed'
            assert finished['budget_state'] == 'settled' and finished['fingerprint'] == execute['execution_fingerprint']
            assert finished['receipt']['fingerprint'] == execute['execution_fingerprint']
            assert finished['episode_id'] == str(scope.episode_uuid(saved_operation))
            assert any(call.get('saved_example') for call in records[before:]) and errors == []
            with factory() as conn:
                reservation = ledger._row(conn, account, saved_operation)
            assert reservation.receipt.json_value() == finished['receipt']
            assert Ledger(factory).get(scope, saved_operation).receipt == reservation.receipt
            after_saved = len(records)
            assert _exchange(port,'GET',saved_status_url,token=token)[2]['data'] == finished
            assert _exchange(port,'POST',execute_url,execute,token=token)[2]['data'] == finished
            assert len(records) == after_saved
            with factory() as conn:
                assert ledger._row(conn, account, saved_operation).attempt_id == reservation.attempt_id
        markers, _, _ = driver.execute_query(
            'MATCH (m:MiroFishIngest {uuid:$episode}) RETURN m.fingerprint AS fingerprint',
            episode=finished['episode_id'], routing_='r')
        assert [marker['fingerprint'] for marker in markers] == [execute['execution_fingerprint']]
        assert ledger.status('owner',account).accounted_ceiling_microusd == 8


def test_real_protected_http_selected_retained_passage(factory, tmp_path):
    from backend.app.services.knowledge_ingestion_client import (
        KnowledgeTransportFailure, validate_result,
    )
    native_port = None
    if 'NEXAWEAVE_W63_EMBEDDING_PORT' in os.environ:
        from tools.run_source_ingestion_tests import embedding_port
        native_port = embedding_port()
    local_embedding = native_port is not None

    display, scope, other_source, account, v1 = seed(factory)
    prefix = 'a' * 32765 + '猫'
    excerpt = 'Mira Vale works for Harbor Labs. 雪'
    text = prefix + excerpt
    start = len(prefix)
    evidence_id = str(uuid4())
    sources = SourceStore(factory)
    retained = sources.ingest_text('owner', scope.project_id, uuid4(), 'two windows', text, [
        {'evidence_id': str(uuid4()), 'start': 0, 'end': start},
        {'evidence_id': evidence_id, 'start': start, 'end': len(text)},
    ])
    assert len(prefix.encode('utf-8')) == 32768 and start < 32768 < len(text)
    assert len(retained.passages) == 2 and retained.passages[1].excerpt == excerpt
    payload = {'schema_version': 2, 'source_revision': str(retained.source_revision),
               'evidence_id': evidence_id, 'ontology': v1['ontology']}
    excerpt_hash = hashlib.sha256(excerpt.encode('utf-8')).hexdigest()
    retained_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    window = {
        'evidence_id': evidence_id, 'start': start, 'end': len(text), 'page': None,
        'ordinal': 2, 'count': 2, 'sha256': excerpt_hash, 'retained_sha256': retained_hash,
        'retained_byte_length': len(text.encode('utf-8')), 'retained_codepoint_length': len(text),
    }
    operation_name = json.dumps(
        [scope.group_id, payload['source_revision'], retained_hash, start, len(text),
         evidence_id, excerpt_hash, payload['ontology']['revision']],
        ensure_ascii=True, separators=(',', ':'),
    )
    operation = uuid5(NAMESPACE_URL, 'mirofish:retained-window:v1:' + operation_name)
    episode = str(scope.episode_uuid(operation))
    status_url = f'/api/source/ingestion/operation/{display}/{operation}'
    plan_url, execute_url = (f'/api/source/ingestion/{method}/{display}' for method in ('plan', 'execute'))
    budget = BudgetLedger(factory)
    ledger = Ledger(factory)

    def reservations():
        with factory() as connection:
            return connection.execute(
                'SELECT r.operation_id,r.attempt_id,r.state,to_jsonb(r) '
                'FROM mf_execution.reservations AS r WHERE r.account_id=%s',
                (account,),
            ).fetchall()

    with neo_scope(scope) as driver, ExitStack() as provider:
        model_port, records, errors, _state = provider.enter_context(model_server(generation_only=local_embedding))
        if local_embedding:
            before = budget.status('owner', account)
            with host(tmp_path/'missing-embedding', scope, display, account, model_port,
                      embedding_port=native_port, omit_embedding_endpoint=True) as (port, token, _):
                assert _exchange(port, 'POST', plan_url, payload, token=token)[0] == 200
                status, _, reply = _exchange(port, 'POST', execute_url, payload, token=token)
                assert status >= 400 and reply == {'success': False, 'error': {'code': 'model_calls_disabled'}}
            assert records == [] and errors == [] and reservations() == []
            assert budget.status('owner', account) == before
        with host(tmp_path/'selected', scope, display, account, model_port,
                  embedding_port=native_port) as (port, token, _events_path):
            def post(route, body):
                return _exchange(port, 'POST', route, body, token=token)

            invalid = dict(payload, operation_id=str(operation))
            status, _, reply = post(plan_url, invalid)
            assert status == 400 and reply['error']['code'] == 'invalid_request'

            status, headers, reply = post(plan_url, payload)
            assert status == 200, reply
            assert headers['Cache-Control'] == 'no-store'
            plan = reply['data']
            assert plan['schema_version'] == 2 and plan['window'] == window
            assert plan['operation_id'] == str(operation) and plan['episode_id'] == episode
            assert plan['evidence_ids'] == [evidence_id] and plan['source_sha256'] == excerpt_hash
            assert plan['source_codepoint_length'] == len(excerpt)
            assert plan['source_byte_length'] == len(excerpt.encode('utf-8'))
            assert plan['model_calls_made'] is plan['spending_authorized'] is False

            prefix_payload = dict(payload, evidence_id=str(retained.passages[0].evidence_id))
            status, _, reply = post(plan_url, prefix_payload)
            assert status == 200 and reply['data']['source_byte_length'] == 32768
            oversized_plan = dict(reply['data'], source_byte_length=32769)
            with pytest.raises(KnowledgeTransportFailure):
                validate_result('plan', oversized_plan, scope.model_dump(mode='json'), prefix_payload)
            assert records == [] and reservations() == []

            foreign_evidence = dict(payload, evidence_id=str(other_source.passages[0].evidence_id))
            status, _, reply = post(execute_url, foreign_evidence)
            assert status == 404 and reply['error']['code'] == 'not_found'
            assert records == [] and reservations() == []

            status, _, reply = post(execute_url, payload)
            assert status == 200, reply
            executed = reply['data']
            assert executed['schema_version'] == 2 and executed['window'] == window
            assert executed['state'] == 'completed' and executed['budget_state'] == 'settled'
            assert executed['operation_id'] == str(operation) and executed['episode_id'] == episode
            assert executed['fingerprint'] == plan['fingerprint']
            assert executed['receipt'] == {
                'group_id': scope.group_id, 'episode_id': episode,
                'fingerprint': plan['fingerprint'], 'evidence_ids': [evidence_id],
            }
            settled = reservations()
            assert len(settled) == 1 and settled[0][0] == operation
            assert isinstance(settled[0][1], UUID) and settled[0][2] == 'settled'
            assert records and errors == []
            request_count = len(records)

            status, _, reply = post(execute_url, payload)
            assert status == 200 and reply['data'] == executed
            status, _, reply = _exchange(port, 'GET', status_url, token=token)
            status_expected = {key: value for key, value in executed.items() if key != 'window'}
            status_expected['schema_version'] = 1
            assert status == 200 and reply['data'] == status_expected

            changed = deepcopy(payload)
            changed['ontology']['entity_types'][0]['description'] = 'Changed fixture entity'
            status, _, reply = post(execute_url, changed)
            assert status == 409 and reply['error']['code'] == 'conflict'
            assert len(records) == request_count and errors == []
            assert reservations() == settled
            model_trace = deepcopy(records)
            operation_record = ledger.get(scope, operation)
            assert operation_record.receipt is not None
            budget_status = budget.status('owner', account)
            if local_embedding:
                assert all(call['path'] == '/v1/chat/completions' and call['model'] == 'fixture-generation'
                           and call['schema'] for call in model_trace)
                with factory() as connection:
                    reservation = budget._row(connection, account, operation)
                assert reservation.receipt == operation_record.receipt
                assert reservation.receipt.json_value() == executed['receipt']

        # The first host has passed its normal stop and child cleanup checks.
        # Route both providers to the closed fixture port during exact replay.
        # Keep the explicit local policy and model identities valid at admission.
        provider.close()
        with host(tmp_path/'selected-restart', scope, display, account, model_port,
                  embedding_port=model_port if local_embedding else None,
                  closed_model_endpoints=local_embedding) as (port, token, events):
            status, _, reply = _exchange(port, 'GET', status_url)
            assert status == 401 and reply == {'success': False, 'error': {'code': 'unauthorized'}}
            assert not [event for event in _events(events) if event['event'] == 'spawn']

            status, _, reply = _exchange(port, 'GET', status_url, token=token)
            assert status == 200 and reply == {'success': True, 'data': status_expected}
            status, _, reply = _exchange(port, 'POST', execute_url, payload, token=token)
            assert status == 200 and reply == {'success': True, 'data': executed}

        assert records == model_trace and errors == []
        assert ledger.get(scope, operation) == operation_record
        assert reservations() == settled and budget.status('owner', account) == budget_status

        rows, _, _ = driver.execute_query(
            'MATCH (e:Episodic {group_id:$group}), (m:MiroFishIngest {uuid:$episode}) '
            'RETURN e.uuid AS episode_id, e.content AS content, m.status AS state, '
            'm.fingerprint AS fingerprint, m.evidence_ids AS evidence_ids',
            group=scope.group_id, episode=episode, routing_='r',
        )
        assert [row.data() for row in rows] == [{
            'episode_id': episode, 'content': excerpt, 'state': 'complete',
            'fingerprint': plan['fingerprint'], 'evidence_ids': [evidence_id],
        }]
        assert sources.get_source('owner', scope.project_id, retained.source_revision) == retained
        assert budget.status('owner', account).accounted_ceiling_microusd == 4
        if local_embedding:
            # The synthetic server rejected all embedding requests. The installed
            # local-only path had exactly one embedding route for this scoped graph.
            vectors, _, _ = driver.execute_query(
                'MATCH ()-[r:RELATES_TO]->() WHERE r.group_id=$group AND $episode IN r.episodes '
                'AND r.fact=$fact AND r.fact_embedding IS NOT NULL RETURN r.fact_embedding AS vector LIMIT 2',
                group=scope.group_id, episode=episode, fact='Mira Vale works for Harbor Labs', routing_='r',
            )
            assert 1 <= len(vectors) <= 2
            for row in vectors:
                vector = row['vector']
                assert len(vector) == 1024 and all(math.isfinite(value) for value in vector) and any(vector)


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
