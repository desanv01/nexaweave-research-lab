"""Opt-in disposable real-PG authority + actual scripted inherited generation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from uuid import UUID, uuid4

import pytest

from nexaweave_execution.budget import BudgetLedger, BudgetDenied, ReservationState, migrate as migrate_budget
from nexaweave_execution.preparation_store import PreparationStore, migrate
from nexaweave_execution.preparation_contracts import PreparedBudgetReceipt, PreparationAuthorityError
from nexaweave_storage import ProjectStore
from nexaweave_knowledge.operations import Ledger
from test_source_bridge_postgres import factory, owned_fixture, ontology
from test_project_store import snapshot

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def migrated(factory):
    with factory() as conn:
        migrate_budget(conn)
        migrate(conn)


def real_host(factory, tmp_path, *, cap=12, authorized=True, chat=None, ceiling=4):
    assert type(ceiling) is int and 1 <= ceiling <= 4
    from app.services.durable_preparation_host import DurablePreparationHost
    from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
    from test_provider_neutral_preparation import ScriptedChat, fact
    display, scope, retained = owned_fixture(factory, text='PRIVATE_SOURCE_SENTINEL')
    bound = scope.model_dump(mode='json')
    class ProjectedClient:
        def __init__(self):
            self.calls, self.changed = 0, False
        def call(self, raw):
            request = json.loads(raw)
            self.calls += 1
            facts = ([fact(10, summary='drift' if self.changed else '公开摘要'),
                      fact(11, name='机构', labels=['Entity', 'Organization'])]
                     if request['payload']['kind'] == 'node' else [fact(20, 'edge')])
            for item in facts:
                item['scope'] = bound
                item['evidence_ids'] = [str(v.evidence_id) for v in retained.passages]
            return json.dumps({'version': 1, 'request_id': request['request_id'], 'ok': True,
                'result': {'schema_version': 1, 'facts': facts, 'next_cursor': None}}, ensure_ascii=False).encode()
    transport = ProjectedClient()
    settings = ReadHostSettings('python', 'read_bootstrap.py', 'fixture-token', 'owner', display, bound, {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    chat = chat or ScriptedChat()
    wires = []
    account = uuid4()
    budget = BudgetLedger(factory)
    budget.create_account('owner', scope.project_id, account, cap)
    host = DurablePreparationHost(settings=settings, connection_factory=factory,
        read_facade=facade, artifact_root=tmp_path, account_id=account, ceiling_microusd=ceiling,
        authorize=lambda: authorized, chat_client_factory=lambda: chat,
        model_name='scripted', base_url='injected://fixture', scheduler=wires.append)
    return host, scope, retained, chat, wires, transport


def request(retained):
    from test_durable_preparation import plan_request
    return plan_request(str(retained.source_revision))


def reference(dto):
    return {'schema_version': 1, 'operation_id': dto['operation_id'], 'plan_sha256': dto['plan_sha256']}


def test_pg_v2_selected_sources_reload_generation_and_full_snapshot_tamper(factory, tmp_path, capsys):
    from nexaweave_storage import SourceStore
    from nexaweave_execution.preparation_contracts import document_envelope
    physical_opens = 0

    def counted_factory():
        nonlocal physical_opens
        connection = factory()
        physical_opens += 1
        return connection

    host, scope, first, chat, wires, transport = real_host(counted_factory, tmp_path)
    text = 'Second owned DOCX 雪 😀'
    second = SourceStore(factory).ingest_text('owner', scope.project_id, uuid4(), 'second document', text,
        [{'evidence_id': str(uuid4()), 'start': 0, 'end': len(text)}])
    original = transport.call
    def projected(raw):
        response = json.loads(original(raw))
        for fact in response['result']['facts']:
            fact['evidence_ids'].extend(str(p.evidence_id) for p in second.passages)
        return json.dumps(response, ensure_ascii=False).encode()
    transport.call = projected
    payload = dict(request(first), schema_version=2,
        source_revisions=[str(first.source_revision), str(second.source_revision)])
    planned = host.plan(payload)
    reloaded = PreparationStore(factory).get('owner', payload['operation_id'], planned['plan_sha256'])
    assert reloaded.frozen['source_text'] == document_envelope(reloaded.frozen['sources'])
    assert [s['source_revision'] for s in reloaded.frozen['public']['sources']] == payload['source_revisions']
    assert host.plan(payload)['plan_sha256'] == planned['plan_sha256']

    revisions = tuple(payload['source_revisions'])
    project_revision = planned['project_revision']
    before_singletons = physical_opens
    singletons = tuple(host._owned(revision, expected_revision=project_revision) for revision in revisions)
    singleton_opens = physical_opens - before_singletons

    before_batch = physical_opens
    batch_scope, batch_project, batch_records = host._owned_sources(revisions, expected_revision=project_revision)
    batched_opens = physical_opens - before_batch
    assert batch_scope == scope and batch_project.revision == project_revision
    assert all((owned_scope, project) == (batch_scope, batch_project)
               for owned_scope, project, _ in singletons)
    assert batch_records == tuple(record for _, _, record in singletons) == (first, second)
    assert (singleton_opens, batched_opens) == (6, 5)

    host.start(reference(planned)); host.generate(wires[0])
    assert PreparationStore(factory).get('owner', payload['operation_id']).state == 'ready'
    config = [call['messages'][-1]['content'] for call in chat.calls
              if any(key in call['messages'][-1]['content'] for key in ('total_simulation_hours', 'hot_topics'))]
    assert len(config) == 2 and all(reloaded.frozen['source_text'] in prompt for prompt in config)
    for path, changed in [(['sources', '1', 'text'], 'corrupt'), (['source_text'], first.text)]:
        with factory() as conn:
            conn.execute('UPDATE mf_preparation.plans SET frozen=jsonb_set(frozen,%s,%s::jsonb) WHERE operation_id=%s',
                (path, json.dumps(changed), UUID(payload['operation_id'])))
        with pytest.raises(PreparationAuthorityError) as caught:
            PreparationStore(factory).get('owner', payload['operation_id'])
        assert caught.value.code == 'preparation_uncertain'
        with factory() as conn:
            from psycopg.types.json import Jsonb
            conn.execute('UPDATE mf_preparation.plans SET frozen=%s WHERE operation_id=%s',
                (Jsonb(reloaded.frozen), UUID(payload['operation_id'])))
    with capsys.disabled():
        print('NEXAWEAVE_W84_SOURCE_SET_OPENS ' + json.dumps(dict(
            source_count=len(revisions), singleton_opens=singleton_opens, batched_opens=batched_opens),
            sort_keys=True), flush=True)


@pytest.mark.parametrize('version', [1, 2])
def test_pg_settled_ready_seed_pair_retains_frozen_sources_and_exact_replay(factory, tmp_path, monkeypatch, version):
    from app.config import Config
    from app.knowledge_read_app import create_read_app
    from app.services.durable_preparation_host import DurablePreparationHost
    from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
    from app.services.knowledge_read_facade import ReadHostSettings
    from app.services.preparation_client import digest
    from nexaweave_storage import SourceStore
    from psycopg.types.json import Jsonb

    host, scope, first, chat, wires, transport = real_host(factory, tmp_path)
    declaration = request(first)
    if version == 2:
        text = 'Owned second document'
        second = SourceStore(factory).ingest_text('owner', scope.project_id, uuid4(), 'second', text,
            [{'evidence_id': str(uuid4()), 'start': 0, 'end': len(text)}])
        original_call = transport.call

        def projection(raw):
            reply = json.loads(original_call(raw))
            for fact in reply['result']['facts']:
                fact['evidence_ids'].extend(str(p.evidence_id) for p in second.passages)
            return json.dumps(reply).encode()

        transport.call = projection
        declaration.update(schema_version=2, source_revisions=[
            str(first.source_revision), str(second.source_revision)])
    plan = host.plan(declaration)
    host.start(reference(plan))
    host.generate(wires[0])
    donor = host.store.get('owner', plan['operation_id'], plan['plan_sha256'])
    assert donor.state == 'ready'
    root = tmp_path / donor.receipt['simulation_id']
    originals = {pin['name']: (root / pin['name']).read_bytes() for pin in donor.receipt['files']}
    seed = (declaration['options']['seed'] + 1) % (2**32)
    payload = dict(reference(plan), idempotency_key=str(uuid4()), alternate_seed=seed)
    with factory() as conn:
        settlement = host.budget._row(conn, host.account_id, donor.operation_id)
    assert settlement.state == ReservationState.settled
    assert type(settlement.receipt) is PreparedBudgetReceipt
    assert settlement.receipt.operation_id == donor.operation_id
    assert settlement.receipt.attempt_id == donor.budget_attempt_id
    assert settlement.receipt.fingerprint == settlement.fingerprint
    assert settlement.receipt.artifact_sha256 == donor.receipt['artifact_sha256']
    baseline = (len(chat.calls), transport.calls, list(wires))

    class FixtureConfig(Config):
        DEBUG = False
        NEXAWEAVE_ALLOWED_ORIGINS = ('http://localhost:3000',)

    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: host.settings))

    def app_for(current):
        facade = KnowledgePreparationFacade(current.settings, host=current)
        return create_read_app(FixtureConfig, preparation_facade=facade, mode='research_local')

    route = '/api/preparation/' + host.display_graph_id + '/seed_pair'
    auth = {'Authorization': 'Bearer ' + host.settings.token}

    def set_budget_receipt(receipt):
        with factory() as conn:
            conn.execute('UPDATE mf_execution.reservations SET receipt=%s WHERE account_id=%s AND operation_id=%s',
                         (Jsonb(receipt), host.account_id, donor.operation_id))

    with app_for(host).test_client() as client:
        assert client.post(route, json=payload).status_code == 401
        assert client.post(route.replace(host.display_graph_id, 'foreign-graph'), headers=auth, json=payload).status_code == 404
        assert client.post(route, headers=auth, json={**payload, 'alternate_seed': True}).status_code == 400
        assert client.post(route, headers=auth, json={**payload, 'alternate_seed': declaration['options']['seed']}).status_code == 409
        set_budget_receipt({**settlement.receipt.json_value(), 'artifact_sha256': 'f' * 64})
        refused = client.post(route, headers=auth, json=payload)
        assert refused.status_code == 409
        set_budget_receipt(settlement.receipt.json_value())
        with factory() as conn:
            assert conn.execute('SELECT count(*) FROM mf_preparation.seed_pairs WHERE donor_operation_id=%s',
                                (donor.operation_id,)).fetchone() == (0,)
        response = client.post(route, headers=auth, json=payload)
        assert response.status_code == 200
        pair = response.json['data']
        assert pair['state'] == 'ready'
        assert pair['donor'] == dict(operation_id=str(donor.operation_id), plan_sha256=donor.plan_sha256,
            seed=declaration['options']['seed'], artifact_sha256=donor.receipt['artifact_sha256'])
        assert client.post(route, headers=auth, json={**payload, 'alternate_seed': (seed + 1) % (2**32)}).status_code == 409
        assert client.post(route, headers=auth, json={**payload, 'idempotency_key': str(uuid4())}).status_code == 409

    fresh = DurablePreparationHost(settings=host.settings, read_facade=host.reader, artifact_root=tmp_path,
                                   connection_factory=factory, account_id=host.account_id)
    alternate = pair['alternate']
    assert alternate['seed'] == seed
    with factory() as conn:
        frozen, saved = conn.execute(
            'SELECT alternate_frozen,receipt FROM mf_preparation.seed_pairs WHERE donor_operation_id=%s',
            (donor.operation_id,)).fetchone()
        assert saved == pair
        assert host.budget._row(conn, host.account_id, donor.operation_id) == settlement
        assert host.budget._row(conn, host.account_id, UUID(alternate['operation_id'])) is None
    expected = deepcopy(donor.frozen)
    for item in (expected['public'], expected['request'], expected['plan_identity']):
        item['operation_id'] = alternate['operation_id']
        item['options']['seed'] = seed
    expected['public']['plan_sha256'] = digest(expected['plan_identity'])
    assert frozen == expected and alternate['plan_sha256'] == expected['public']['plan_sha256']
    with pytest.raises(PreparationAuthorityError) as collision:
        fresh.store.put('owner', frozen)
    assert collision.value.code == 'conflict'
    assert host.store.get('owner', donor.operation_id) == donor
    assert all((root / name).read_bytes() == raw for name, raw in originals.items())
    sibling = tmp_path / alternate['receipt']['simulation_id']
    for name, raw in originals.items():
        copied = (sibling / name).read_bytes()
        if name in {'state.json', 'simulation_config.json'}:
            assert json.loads(copied) == {**json.loads(raw), 'simulation_id': sibling.name}
        else:
            assert copied == raw
    with app_for(fresh).test_client() as client:
        replay = client.post(route, headers=auth, json=payload)
        assert replay.status_code == 200 and replay.json == response.json
        assert replay.data == response.data
        if version == 2:
            original_snapshot = fresh._source_snapshot
            with monkeypatch.context() as revoked:
                def stale_snapshot(retained, check):
                    snapshot = original_snapshot(retained, check)
                    if retained.source_revision == second.source_revision:
                        snapshot['source_name'] = 'Changed second source'
                    return snapshot

                revoked.setattr(fresh, '_source_snapshot', stale_snapshot)
                assert client.post(route, headers=auth, json=payload).status_code == 409
        ProjectStore(factory).update('owner', scope.project_id, donor.project_revision, snapshot())
        assert client.post(route, headers=auth, json=payload).status_code == 409
    assert (len(chat.calls), transport.calls, wires) == baseline


def _expected_prepared_profile(host, ready, original, ordinal):
    import csv
    import io

    actor = ready['actors'][ordinal]
    selector = {**reference(ready), 'source_entity_uuid': actor['source_entity_uuid']}
    twitter = list(csv.DictReader(io.StringIO(original['twitter_profiles.csv'].decode('utf-8'), newline='')))
    reddit = json.loads(original['reddit_profiles.json'])
    grounding = json.loads(original['source_grounding.json'])
    assert len(ready['actors']) == len(twitter) == len(reddit)
    return {'success': True, 'data': {
        **selector, 'display_graph_id': host.display_graph_id, 'scope': host.scope_dto,
        'project_revision': ready['project_revision'], 'artifact_sha256': ready['receipt']['artifact_sha256'],
        'actor': {'ordinal': ordinal, 'name': actor['name'], 'labels': actor['labels']},
        'generated_profiles': [
            {'platform': 'twitter', 'handle': twitter[ordinal]['username'],
             'description': twitter[ordinal]['description'], 'persona': twitter[ordinal]['user_char']},
            {'platform': 'reddit', 'handle': reddit[ordinal]['username'],
             'description': reddit[ordinal]['bio'], 'persona': reddit[ordinal]['persona']},
        ],
        'frozen_support': grounding[actor['source_entity_uuid']],
    }}


def configuration_reference(ready):
    return {**reference(ready), 'source_revision': ready['source']['source_revision'],
            'artifact_sha256': ready['receipt']['artifact_sha256']}


def _expected_prepared_configuration(host, ready, original):
    config = json.loads(original['simulation_config.json'])
    options = ready['options']
    topics = config['event_config']['hot_topics']
    return {'success': True, 'data': {
        **configuration_reference(ready), 'display_graph_id': host.display_graph_id, 'scope': host.scope_dto,
        'project_revision': ready['project_revision'], 'goal': config['simulation_requirement'],
        'time_activity': config['time_config'], 'hot_topics': topics[:64],
        'hot_topics_omitted': max(0, len(topics) - 64),
        'platform_controls': [config[platform + '_config'] for platform in options['platforms']],
        'effective_controls': {key: options[key] for key in ('platforms', 'seed', 'max_rounds')},
    }}


def _installed_profile_bootstrap(reader_python, tmp_path, *, distinct, expected_bootstrap):
    import subprocess
    import sys
    from pathlib import Path
    from tools.run_unit_tests import _unit_environment

    assert reader_python.is_absolute() and reader_python.is_file()
    probe = """
import importlib.util, json, sys
from pathlib import Path
prefix = Path(sys.prefix).resolve()
for name in ('psycopg', 'nexaweave_execution', 'nexaweave_storage', 'nexaweave_knowledge'):
    origin = Path(importlib.util.find_spec(name).origin).resolve()
    assert 'site-packages' in origin.parts and origin.is_relative_to(prefix)
bootstrap = origin.with_name('read_bootstrap.py')
assert bootstrap.is_file() and not bootstrap.is_symlink()
print(json.dumps([str(prefix), str(bootstrap)]))
"""
    installed = subprocess.run([str(reader_python), '-I', '-B', '-c', probe],
        cwd=tmp_path, env=_unit_environment(tmp_path), stdin=subprocess.DEVNULL,
        capture_output=True, timeout=15, check=True)
    assert not installed.stderr and 0 < len(installed.stdout) <= 4096
    reader_prefix, bootstrap = json.loads(installed.stdout)
    assert (Path(reader_prefix) != Path(sys.prefix).resolve()) == distinct
    if expected_bootstrap:
        assert Path(bootstrap) == Path(expected_bootstrap)
    return bootstrap


def assert_installed_prepared_profile(host, ready, original, source_revision, monkeypatch, tmp_path):
    import os
    from pathlib import Path
    import sys
    from app import create_app
    from app.services.knowledge_transport import OwnedProcess
    from app.services.preparation_client import ENVELOPE_OVERHEAD, RESULT_BYTES

    settings = host.settings
    reader_python = Path(os.environ['KNOWLEDGE_PYTHON'])
    assert reader_python == Path(settings.python) and reader_python != Path(sys.executable).absolute()
    assert settings.bootstrap == os.environ['KNOWLEDGE_BOOTSTRAP_SCRIPT']
    bootstrap = _installed_profile_bootstrap(reader_python, tmp_path, distinct=True,
                                            expected_bootstrap=settings.bootstrap)
    assert settings.principal == host.principal == 'owner'
    assert settings.scope == host.scope_dto
    assert ready['state'] == 'ready'
    row = host.store.get(settings.principal, ready['operation_id'], ready['plan_sha256'])
    assert row.state == 'ready' and row.receipt == ready['receipt']
    assert row.display_graph_id == settings.display_graph_id == host.display_graph_id
    assert row.frozen['public']['source']['source_revision'] == ready['source']['source_revision'] == source_revision
    assert row.frozen['public']['actors'] == ready['actors']
    assert row.project_revision == ready['project_revision']

    token = 'profile-fixture-owner-0123456789abcdef'
    environment = dict(settings.child_environment, NEXAWEAVE_APP_MODE='research_local',
                       NEXAWEAVE_ALLOWED_ORIGINS='http://localhost:3000', FLASK_HOST='127.0.0.1',
                       KNOWLEDGE_PYTHON=str(reader_python), KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap,
                       KNOWLEDGE_READ_TOKEN=token, KNOWLEDGE_PREPARATION_ARTIFACT_ROOT=str(host.root.resolve()))
    expected = _expected_prepared_profile(host, ready, original, 0)
    selector = {**reference(ready), 'source_entity_uuid': ready['actors'][0]['source_entity_uuid']}
    route = '/api/preparation/' + host.display_graph_id
    auth = {'Authorization': 'Bearer ' + token, 'Origin': 'http://localhost:3000'}

    started = []
    start = OwnedProcess.start
    def observe_start(owner, popen, argv, **kwargs):
        child = start(owner, popen, argv, **kwargs)
        started.append((owner, child, tuple(argv), Path(kwargs['cwd']), set(kwargs['env'])))
        return child

    with monkeypatch.context() as configured:
        for key, value in environment.items():
            configured.setenv(key, value)
        configured.delenv('MIROFISH_APP_MODE', raising=False)
        configured.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
        configured.setattr(OwnedProcess, 'start', observe_start)
        with create_app().test_client() as client:
            assert not started
            response = client.post(route + '/profiles', headers=auth, json=selector)
            assert response.status_code == 200
            assert 0 < len(response.data) <= RESULT_BYTES + ENVELOPE_OVERHEAD
            assert response.headers['Cache-Control'] == 'no-store'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            assert response.headers['Access-Control-Allow-Origin'] == auth['Origin']
            assert response.json == expected
            assert len(started) == 1
            config_selector = configuration_reference(ready)
            config_url = route + '/configuration'
            assert client.post(config_url, json=config_selector).status_code == 401
            assert client.post(config_url, headers=auth, json={**config_selector, 'path': 'simulation_config.json'}).status_code == 400
            assert len(started) == 1
            config = client.post(config_url, headers=auth, json=config_selector)
            assert config.status_code == 200 and config.json == _expected_prepared_configuration(host, ready, original)
            assert 0 < len(config.data) <= RESULT_BYTES + ENVELOPE_OVERHEAD
            assert config.headers['Cache-Control'] == 'no-store'
            assert config.headers['X-Content-Type-Options'] == 'nosniff'
    assert len(started) == 2
    for owner, child, argv, cwd, keys in started:
        assert Path(argv[0]) == reader_python and argv[1] == '-I' and set(argv[2:4]) == {'-B', '-u'}
        assert len(argv) == 5 and Path(argv[4]).name == 'prepared_profile_inspection_child.py'
        assert owner.closed and child.poll() == 0 and not cwd.exists()
        assert not {'KNOWLEDGE_READ_TOKEN', 'KNOWLEDGE_NEO4J_PASSWORD', 'PYTHONPATH'} & keys


def test_pg_restart_duplicate_race_and_actual_inherited_publication(factory, tmp_path, monkeypatch):
    import builtins
    import os
    import stat
    from pathlib import Path
    import sys
    from hashlib import sha256
    from psycopg.conninfo import conninfo_to_dict
    from app import create_app
    from app.config import Config
    from app.knowledge_read_app import create_read_app
    from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
    from app.services.knowledge_read_facade import ReadHostSettings
    from app.services.knowledge_transport import OwnedProcess
    from test_provider_neutral_preparation import ScriptedChat

    topics = [f'fixture-topic-{index:02d}' for index in range(65)]

    class ManyTopicsChat(ScriptedChat):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if 'hot_topics' in kwargs['messages'][-1]['content']:
                message = response.choices[0].message
                event = json.loads(message.content)
                event['hot_topics'] = topics
                message.content = json.dumps(event)
            return response

    host, scope, retained, chat, wires, transport = real_host(factory, tmp_path, chat=ManyTopicsChat())
    payload = request(retained)
    planned = host.plan(payload)
    assert not chat.calls and transport.calls == 2
    transport.changed = True
    assert host.plan(payload)['plan_sha256'] == planned['plan_sha256'] and transport.calls == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(host.start, [reference(planned), reference(planned)]))
    assert len(wires) == 1 and all(v['state'] == 'queued' for v in replies)
    fresh = PreparationStore(factory)
    row = fresh.get('owner', payload['operation_id'], planned['plan_sha256'])
    assert row.state == 'queued' and not row.model_calls_started
    with pytest.raises(PreparationAuthorityError):
        fresh.get('other', payload['operation_id'])
    host.generate(wires[0])
    ready = host.status(reference(planned))
    assert ready['state'] == 'ready' and ready['receipt'] and ready['model_calls_started']
    with factory() as conn:
        settled = host.budget._row(conn, host.account_id, UUID(payload['operation_id']))
    assert type(settled.receipt) is PreparedBudgetReceipt and settled.state == ReservationState.settled
    assert host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 4
    assert PreparationStore(factory).get('owner', payload['operation_id']).receipt == ready['receipt']
    with pytest.raises(PreparationAuthorityError):
        host.generate(wires[0])
    assert len(chat.calls) == 4
    assert ready['options']['platforms'] == ['twitter', 'reddit']

    single_options = dict(payload['options'], platforms=['twitter'], max_rounds=1)
    single_options.pop('twitter_to_reddit_seed_index', None)
    single_planned = host.plan({**payload, 'operation_id': str(uuid4()), 'options': single_options})
    host.start(reference(single_planned))
    assert len(wires) == 2
    host.generate(wires[1])
    single_ready = host.status(reference(single_planned))
    single_receipt = single_ready['receipt']
    assert single_ready['state'] == 'ready'
    assert fresh.get('owner', single_ready['operation_id']).receipt == single_receipt
    single_root = tmp_path / single_receipt['simulation_id']
    single_original = {pin['name']: (single_root / pin['name']).read_bytes() for pin in single_receipt['files']}

    class FixtureConfig(Config):
        DEBUG = False
        NEXAWEAVE_ALLOWED_ORIGINS = ('http://localhost:3000',)

    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    facade = KnowledgePreparationFacade(host.settings, host=host)
    with monkeypatch.context() as injected:
        injected.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: host.settings))
        injected_app = create_read_app(FixtureConfig, preparation_facade=facade, mode='research_local')
    with injected_app.test_client() as client:
        receipt = ready['receipt']
        root = tmp_path / receipt['simulation_id']
        original = {file['name']: (root / file['name']).read_bytes() for file in receipt['files']}
        actor, = ready['actors']
        assert actor['labels'] == ['Person']
        selector = {**reference(ready), 'source_entity_uuid': actor['source_entity_uuid']}
        route = '/api/preparation/' + host.display_graph_id + '/profiles'
        auth = {'Authorization': 'Bearer ' + host.settings.token}
        prior_calls = (len(chat.calls), transport.calls, len(wires))
        assert client.post(route, json=selector).status_code == 401
        response = client.post(route, headers=auth, json=selector)
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        expected = _expected_prepared_profile(host, ready, original, 0)
        assert response.json == expected
        foreign = client.post(route, headers=auth, json={**selector, 'source_entity_uuid': str(uuid4())})
        assert foreign.status_code == 404
        assert foreign.json == {'success': False, 'error': {'code': 'not_found'}}
        stale = client.post(route, headers=auth, json={**selector, 'plan_sha256': '0' * 64})
        assert stale.status_code == 409
        assert stale.json == {'success': False, 'error': {'code': 'conflict'}}
        assert client.post(route, headers=auth, json={**selector, 'platform': 'twitter'}).status_code == 400

    config_selector = configuration_reference(ready)
    expected_config = _expected_prepared_configuration(host, ready, original)
    assert json.loads(original['simulation_config.json'])['event_config']['hot_topics'] == topics
    assert expected_config['data']['hot_topics'] == topics[:64]
    assert expected_config['data']['hot_topics_omitted'] == 1
    native, _ = host.bind_native(reference(ready), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)
    assert expected_config['data']['effective_controls'] == {
        'platforms': list(native.platforms), 'seed': native.seed, 'max_rounds': native.max_rounds}
    single_expected = _expected_prepared_configuration(host, single_ready, single_original)
    single_native, _ = host.bind_native(reference(single_ready), run_id=uuid4(),
                                        runtime_sha256='a' * 64, model_factory=dict)
    assert tuple(single_native.platforms) == ('twitter',) and single_native.max_rounds == 1
    assert single_expected['data']['effective_controls'] == {
        'platforms': ['twitter'], 'seed': single_native.seed, 'max_rounds': 1}

    configured_python = os.environ.get('KNOWLEDGE_PYTHON')
    reader_python = Path(configured_python or sys.executable)
    bootstrap = _installed_profile_bootstrap(reader_python, tmp_path, distinct=bool(configured_python),
                                            expected_bootstrap=os.environ.get('KNOWLEDGE_BOOTSTRAP_SCRIPT'))

    pg = conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    token = 'profile-fixture-owner-0123456789abcdef'
    settings = {
        'NEXAWEAVE_APP_MODE': 'research_local', 'NEXAWEAVE_ALLOWED_ORIGINS': 'http://localhost:3000',
        'FLASK_HOST': '127.0.0.1', 'KNOWLEDGE_PYTHON': str(reader_python),
        'KNOWLEDGE_BOOTSTRAP_SCRIPT': bootstrap, 'KNOWLEDGE_READ_TOKEN': token,
        'KNOWLEDGE_PRINCIPAL': 'owner', 'KNOWLEDGE_DISPLAY_GRAPH_ID': host.display_graph_id,
        'KNOWLEDGE_BOUND_SCOPE_JSON': json.dumps(host.scope_dto),
        'KNOWLEDGE_PG_HOST': pg['host'], 'KNOWLEDGE_PG_PORT': pg['port'],
        'KNOWLEDGE_PG_DATABASE': pg['dbname'], 'KNOWLEDGE_PG_USER': pg['user'],
        'KNOWLEDGE_PG_PASSWORD': pg['password'], 'KNOWLEDGE_NEO4J_URI': 'bolt://127.0.0.1:9',
        'KNOWLEDGE_NEO4J_USER': 'neo4j', 'KNOWLEDGE_NEO4J_PASSWORD': 'unused-local-fixture',
        'KNOWLEDGE_PREPARATION_ARTIFACT_ROOT': str(tmp_path.resolve()),
    }
    base = route.removesuffix('/profiles')
    config_url = base + '/configuration'
    auth = {'Authorization': 'Bearer ' + token, 'Origin': 'http://localhost:3000'}
    native_import = builtins.__import__
    parent_dependencies = {'psycopg', 'nexaweave_knowledge', 'nexaweave_execution', 'nexaweave_storage'}
    blocked_imports = []
    def protected_import(name, *args, **kwargs):
        if name.partition('.')[0] in parent_dependencies:
            blocked_imports.append(name)
            raise AssertionError('default profile route imported a split-reader dependency')
        return native_import(name, *args, **kwargs)

    started = []
    start = OwnedProcess.start
    def observe_start(owner, popen, argv, **kwargs):
        child = start(owner, popen, argv, **kwargs)
        started.append((owner, child, tuple(argv), Path(kwargs['cwd']), set(kwargs['env'])))
        return child

    def refuse_tamper(client, url, selection, filename, changed=None):
        tampered = root / filename
        sealed_mode = stat.S_IMODE(tampered.stat().st_mode)
        editable_mode = sealed_mode | stat.S_IWUSR
        try:
            tampered.chmod(editable_mode)
            tampered.write_bytes(original[filename] + b'\n' if changed is None else changed)
            tampered.chmod(sealed_mode)
            refused = client.post(url, headers=auth, json=selection)
            assert refused.status_code == 409
            assert refused.json == {'success': False, 'error': {'code': 'conflict'}}
        finally:
            try:
                tampered.chmod(editable_mode)
                tampered.write_bytes(original[filename])
            finally:
                tampered.chmod(sealed_mode)

    with monkeypatch.context() as configured:
        for key, value in settings.items():
            configured.setenv(key, value)
        configured.delenv('MIROFISH_APP_MODE', raising=False)
        configured.delenv('MIROFISH_ALLOWED_ORIGINS', raising=False)
        configured.setattr(builtins, '__import__', protected_import)
        configured.setattr(OwnedProcess, 'start', observe_start)
        with create_app().test_client() as client:
            assert client.post(route, json=selector).status_code == 401
            denied_origin = {**auth, 'Origin': 'http://denied.invalid'}
            assert client.post(route, headers=denied_origin, json=selector).status_code == 403
            assert client.post(route, headers=auth, json={**selector, 'platform': 'twitter'}).status_code == 400
            assert client.post(config_url, json=config_selector).status_code == 401
            assert client.post(config_url, headers=denied_origin, json=config_selector).status_code == 403
            assert client.post(config_url, headers=auth, json={**config_selector, 'path': 'other.json'}).status_code == 400
            foreign_url = '/api/preparation/graph_foreign/configuration'
            assert client.post(foreign_url, headers=auth, json=config_selector).status_code == 404
            for method, body in [('plan', payload), ('start', reference(ready)), ('status', reference(ready))]:
                unavailable = client.post(base + '/' + method, headers=auth, json=body)
                assert unavailable.status_code == 503
            assert not started
            response = client.post(route, headers=auth, json=selector)
            assert response.status_code == 200 and response.json == expected
            assert response.headers['Cache-Control'] == 'no-store'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            assert response.headers['Access-Control-Allow-Origin'] == auth['Origin']
            assert len(started) == 1
            foreign = client.post(route, headers=auth, json={**selector, 'source_entity_uuid': str(uuid4())})
            assert foreign.status_code == 404 and foreign.json == {'success': False, 'error': {'code': 'not_found'}}
            stale = client.post(route, headers=auth, json={**selector, 'plan_sha256': '0' * 64})
            assert stale.status_code == 409 and stale.json == {'success': False, 'error': {'code': 'conflict'}}
            refuse_tamper(client, route, selector, 'twitter_profiles.csv')
            config = client.post(config_url, headers=auth, json=config_selector)
            assert config.status_code == 200 and config.json == expected_config
            assert config.headers['Cache-Control'] == 'no-store'
            assert config.headers['X-Content-Type-Options'] == 'nosniff'
            for field, replacement in [('source_revision', str(uuid4())), ('artifact_sha256', '0' * 64)]:
                stale = client.post(config_url, headers=auth, json={**config_selector, field: replacement})
                assert stale.status_code == 409
                assert stale.json == {'success': False, 'error': {'code': 'conflict'}}
            single_response = client.post(config_url, headers=auth, json=configuration_reference(single_ready))
            assert single_response.status_code == 200 and single_response.json == single_expected
            raw_config = original['simulation_config.json']
            changed = raw_config.replace(b'fixture-topic-00', b'fixture-topic-XX', 1)
            assert len(changed) == len(raw_config) and changed != raw_config
            assert json.loads(changed)['event_config']['hot_topics'] == ['fixture-topic-XX', *topics[1:]]
            config_pin = next(pin for pin in receipt['files'] if pin['name'] == 'simulation_config.json')
            assert config_pin['size'] == len(changed) and config_pin['sha256'] == sha256(raw_config).hexdigest()
            assert sha256(changed).hexdigest() != config_pin['sha256']
            refuse_tamper(client, config_url, config_selector, 'simulation_config.json', changed)
            # Revoke only this fixture binding, keeping the owner READY row intact.
            with configured.context() as fixture_store:
                fixture_store.setattr(builtins, '__import__', native_import)
                with factory() as conn:
                    removed = conn.execute(
                        'DELETE FROM mf_knowledge.scope_bindings '
                        'WHERE principal=%s AND display_graph_id=%s AND group_id=%s',
                        ('owner', host.display_graph_id, scope.group_id))
                    assert removed.rowcount == 1
            try:
                closed = client.post(route, headers=auth, json=selector)
                assert closed.status_code == 503
                assert closed.json == {'success': False, 'error': {'code': 'preparation_unavailable'}}
                closed_config = client.post(config_url, headers=auth, json=config_selector)
                assert closed_config.status_code == 503
                assert closed_config.json == {'success': False, 'error': {'code': 'preparation_unavailable'}}
            finally:
                with configured.context() as fixture_store:
                    fixture_store.setattr(builtins, '__import__', native_import)
                    assert host.bindings.bind('owner', host.display_graph_id, scope).scope == scope
            with configured.context() as fixture_store:
                fixture_store.setattr(builtins, '__import__', native_import)
                updated = snapshot()
                updated['name'] += ' revision'
                ProjectStore(factory).update('owner', scope.project_id, ready['project_revision'], updated)
            stale_project = client.post(config_url, headers=auth, json=config_selector)
            assert stale_project.status_code == 409
            assert stale_project.json == {'success': False, 'error': {'code': 'conflict'}}
    assert not blocked_imports
    assert len(started) == 12
    for owner, child, argv, cwd, keys in started:
        assert Path(argv[0]) == reader_python and argv[1] == '-I' and set(argv[2:4]) == {'-B', '-u'}
        assert len(argv) == 5 and Path(argv[4]).name == 'prepared_profile_inspection_child.py'
        assert owner.closed and child.poll() is not None and not cwd.exists()
        assert os.name != 'nt' or owner.tree_empty
        assert not {'KNOWLEDGE_READ_TOKEN', 'KNOWLEDGE_NEO4J_PASSWORD', 'PYTHONPATH'} & keys
    assert (len(chat.calls), transport.calls, len(wires)) == prior_calls
    assert {name: (root / name).read_bytes() for name in original} == original
    assert {name: (single_root / name).read_bytes() for name in single_original} == single_original
    with factory() as conn:
        assert host.budget._row(conn, host.account_id, UUID(payload['operation_id'])) == settled
    assert fresh.get('owner', selector['operation_id'], selector['plan_sha256']).receipt == receipt


def test_shared_ingestion_preparation_cap_race(factory, tmp_path):
    host, scope, retained, _, _, _ = real_host(factory, tmp_path, cap=5)
    from nexaweave_knowledge.source_bridge import SourceIngestionBridge
    ingestion = SourceIngestionBridge(factory).plan('owner', host.display_graph_id, retained.source_revision, uuid4(), ontology())
    planned = host.plan(request(retained))
    def prepare():
        return host.budget.reserve_prepared('owner', host.account_id, scope,
            UUID(planned['operation_id']), planned['plan_sha256'], 4)
    def ingest():
        return host.budget.reserve('owner', host.account_id, scope, ingestion.source.operation_id,
            ingestion.request_fingerprint, 4, ingestion.source.evidence_ids)
    def try_reserve(function):
        try:
            return function()
        except BudgetDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(try_reserve, [prepare, ingest]))
    assert sum(row is not None for row in rows) == 1
    assert host.budget.status('owner', host.account_id).remaining_microusd == 1


def test_pg_attempt_fence_crash_and_lost_schedule_ack(factory, tmp_path):
    host, scope, retained, chat, wires, _ = real_host(factory, tmp_path)
    planned = host.plan(request(retained))
    def lost(wire):
        wires.append(wire)
        raise RuntimeError('PRIVATE_ACK')
    host.scheduler = lost
    from app.services.preparation_client import PreparationError
    with pytest.raises(PreparationError):
        host.start(reference(planned))
    assert host.status(reference(planned))['state'] == 'queued'
    assert host.start(reference(planned))['state'] == 'queued' and len(wires) == 1
    row = host.store.claim('owner', wires[0])
    with pytest.raises(PreparationAuthorityError):
        PreparationStore(factory).claim('owner', row.dispatch)
    with pytest.raises(PreparationAuthorityError):
        host.generate(wires[0])
    assert not chat.calls
    assert host.budget.status('owner', host.account_id).reserved_microusd == 4


def test_stale_project_tombstone_and_binding_denied_before_files(factory, tmp_path, monkeypatch):
    host, scope, retained, chat, wires, _ = real_host(factory, tmp_path)
    planned = host.plan(request(retained))
    ProjectStore(factory).update('owner', scope.project_id, 1, snapshot())
    from app.services.preparation_client import PreparationError
    with pytest.raises(PreparationError) as stale:
        host.start(reference(planned))
    assert stale.value.code == 'conflict' and not wires and not chat.calls
    assert list(tmp_path.iterdir()) == []
    Ledger(factory).tombstone_scope(scope)
    with pytest.raises(PreparationError) as tombstoned:
        host.status(reference(planned))
    assert tombstoned.value.code == 'tombstoned'


def test_native_binder_reauthorizes_before_any_artifact_read(factory, tmp_path, monkeypatch):
    host, scope, retained, chat, wires, _ = real_host(factory, tmp_path)
    planned = host.plan(request(retained))
    host.start(reference(planned)); host.generate(wires[0])
    native, binding = host.bind_native(reference(planned), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)
    binding.validate(native)
    ProjectStore(factory).update('owner', scope.project_id, 1, snapshot())
    def forbidden(*args):
        raise AssertionError('filesystem touched before authority')
    monkeypatch.setattr(host, '_artifacts', forbidden)
    from app.services.preparation_client import PreparationError
    with pytest.raises(PreparationError) as denied:
        host.bind_native(reference(planned), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)
    assert denied.value.code == 'conflict'
    assert host.status(reference(planned))['state'] == 'ready'


def test_corrupt_frozen_store_is_quarantined(factory, tmp_path):
    host, scope, retained, _, _, _ = real_host(factory, tmp_path)
    planned = host.plan(request(retained))
    with factory() as conn:
        conn.execute("UPDATE mf_preparation.plans SET frozen=jsonb_set(frozen,'{source_text}', '\"tampered\"') WHERE operation_id=%s", (UUID(planned['operation_id']),))
    with pytest.raises(PreparationAuthorityError) as corrupt:
        host.store.get('owner', planned['operation_id'])
    assert corrupt.value.code == 'preparation_uncertain'


def test_own_migration_checksum_catalog_drift_and_legacy_unchanged(factory):
    with factory() as conn:
        legacy = conn.execute('SELECT version,checksum,schema_checksum FROM mf_execution.schema_migrations').fetchall()
        preparation_versions = conn.execute(
            'SELECT version,checksum,schema_checksum FROM mf_preparation.schema_migrations ORDER BY version').fetchall()
        assert [row[0] for row in preparation_versions] == [1, 2]
        # Recreate the legacy catalog in this disposable fixture before re-upgrade.
        conn.execute('DROP TABLE mf_preparation.seed_pairs')
        conn.execute('DELETE FROM mf_preparation.schema_migrations WHERE version=2')
        migrate(conn)
        assert conn.execute(
            'SELECT version,checksum,schema_checksum FROM mf_preparation.schema_migrations ORDER BY version'
        ).fetchall() == preparation_versions
    with factory() as conn:
        with pytest.raises(PreparationAuthorityError):
            with conn.transaction():
                conn.execute('CREATE TABLE mf_preparation.unexpected(id integer)')
                migrate(conn)
    with factory() as conn:
        assert conn.execute('SELECT version,checksum,schema_checksum FROM mf_execution.schema_migrations').fetchall() == legacy
        migrate(conn)
