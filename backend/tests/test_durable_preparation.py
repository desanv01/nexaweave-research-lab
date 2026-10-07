"""Offline inherited generator fixtures. PG durability is qualified separately."""
from copy import deepcopy
from dataclasses import replace
import csv
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services.durable_preparation_host import DurablePreparationHost, BoundedChat, validate_graph
from app.services.preparation_client import PreparationError, digest, validate_payload
from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
from nexaweave_execution.preparation_store import PreparationRecord, _record
from nexaweave_execution.preparation_contracts import PreparationAuthorityError
from nexaweave_execution.budget import ReservationState
from nexaweave_knowledge.contracts import KnowledgeScope
from test_provider_neutral_preparation import ByteClient, ScriptedChat, SCOPE, uid


def plan_request(source_revision=None):
    return {'schema_version': 1, 'operation_id': str(uuid4()),
            'source_revision': source_revision or str(uuid4()),
            'options': {'types': ['Person'], 'max_agents': 1, 'seed': 7,
                        'platforms': ['twitter', 'reddit'], 'max_rounds': 2,
                        'simulation_requirement': '讨论技术'}}


def status_request(dto):
    return {'schema_version': 1, 'operation_id': dto['operation_id'], 'plan_sha256': dto['plan_sha256']}


class MemoryStore:
    """Pure activity seam; never offered as durable production authority."""
    def __init__(self):
        self.rows = {}

    def get(self, principal, operation, plan_sha256=None):
        key = str(operation)
        if key not in self.rows or self.rows[key].principal != principal:
            raise PreparationAuthorityError('not_found')
        row = self.rows[key]
        if plan_sha256 is not None and row.plan_sha256 != plan_sha256:
            raise PreparationAuthorityError('conflict')
        return row

    def put(self, principal, frozen):
        public = frozen['public']
        row = PreparationRecord(UUID(public['operation_id']), principal, UUID(public['scope']['project_id']),
            public['project_revision'], public['display_graph_id'], digest(frozen['request']),
            public['plan_sha256'], deepcopy(frozen), 'planned', 'planned', 0, None, None, False, None, None)
        _record(tuple(row.__dict__.values()))
        self.rows[public['operation_id']] = row
        return row

    def queue(self, principal, operation, plan_sha256, budget_attempt_id):
        row = self.get(principal, operation, plan_sha256)
        if row.state != 'planned':
            return row, False
        row = replace(row, state='queued', stage='queued', attempt_id=uuid4(), budget_attempt_id=budget_attempt_id)
        self.rows[str(operation)] = row
        return row, True

    def claim(self, principal, dispatch):
        from nexaweave_execution.preparation_contracts import PreparationDispatch
        dispatch = PreparationDispatch.from_wire(dispatch)
        row = self.get(principal, dispatch.operation_id, dispatch.plan_sha256)
        if row.state != 'queued' or row.attempt_id != dispatch.attempt_id:
            raise PreparationAuthorityError('conflict')
        row = replace(row, state='preparing', stage='reading')
        self.rows[str(row.operation_id)] = row
        return row

    def update(self, principal, dispatch, **changes):
        row = self.get(principal, dispatch.operation_id, dispatch.plan_sha256)
        if row.state != 'preparing' or row.attempt_id != dispatch.attempt_id:
            raise PreparationAuthorityError('conflict')
        started = changes.pop('calls_started', False)
        row = replace(row, **changes, model_calls_started=row.model_calls_started or started)
        self.rows[str(row.operation_id)] = row
        return row


class MemoryBudget:
    def __init__(self):
        self.rows, self.starts, self.settles = {}, 0, 0

    def reserve_prepared(self, principal, account, scope, operation, plan, ceiling):
        self.rows.setdefault(str(operation), SimpleNamespace(state=ReservationState.reserved, attempt_id=uuid4()))
        return self.rows[str(operation)]

    def start(self, principal, account, operation, attempt):
        row = self.rows[str(operation)]
        assert row.state == ReservationState.reserved and row.attempt_id == attempt
        row.state = ReservationState.started
        self.starts += 1

    def settle_prepared(self, *args):
        self.settles += 1

    def mark_uncertain(self, principal, account, operation, attempt, code):
        self.rows[str(operation)].state = ReservationState.uncertain

    def release_undispatched(self, principal, account, operation, attempt):
        self.rows[str(operation)].state = ReservationState.released


def offline_host(tmp_path, *, chat=None, authorized=True):
    transport = ByteClient()
    settings = ReadHostSettings('python', 'read_bootstrap.py', 'fixture-token', 'owner', 'display-1', SCOPE, {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: transport)
    chat = chat or ScriptedChat()
    host = DurablePreparationHost(settings=settings, connection_factory=lambda: None,
        read_facade=facade, artifact_root=tmp_path, account_id=uuid4(), ceiling_microusd=4,
        authorize=lambda: authorized, chat_client_factory=lambda: chat,
        model_name='scripted', base_url='injected://fixture', scheduler=lambda wire: None)
    host.store, host.budget = MemoryStore(), MemoryBudget()
    scope = KnowledgeScope.model_validate_json(json.dumps(SCOPE))
    source = SimpleNamespace(name='retained', text='公开源文', text_sha256=hashlib.sha256('公开源文'.encode()).hexdigest())
    project = SimpleNamespace(revision=1)
    def owned(revision, *, expected_revision=None):
        if expected_revision is not None and project.revision != expected_revision:
            raise PreparationError('conflict')
        return scope, project, source
    host._owned = owned
    return host, chat, transport, project


def test_actual_inherited_generation_and_exact_native_binding(tmp_path):
    host, chat, transport, project = offline_host(tmp_path)
    request = plan_request()
    planned = host.plan(request)
    assert planned['state'] == 'planned' and not chat.calls
    transport.changed = True
    queued = host.start(status_request(planned))
    assert queued['state'] == 'queued' and not queued['model_calls_started']
    row = host.store.get('owner', request['operation_id'])
    heartbeat = []
    result = host.generate(row.dispatch.to_wire(), heartbeat=lambda: heartbeat.append(1))
    assert result['state'] == 'ready' and heartbeat
    ready = host.status(status_request(planned))
    assert ready['receipt']['simulation_id'] == 'sim_' + UUID(request['operation_id']).hex
    assert host.budget.starts == host.budget.settles == 1
    assert len(chat.calls) == 4 and all(call['timeout'] <= 15 and call['max_tokens'] == 4096 for call in chat.calls)
    root = tmp_path / ready['receipt']['simulation_id']
    grounding = json.loads((root / 'source_grounding.json').read_bytes())
    assert grounding[uid(10)]['summary'] == '公开摘要'
    assert grounding[uid(10)]['facts'][0]['fact'] == '艾丽丝认识机构'
    profiles = json.loads((root / 'reddit_profiles.json').read_bytes())
    assert profiles[0]['persona'] == '艾丽丝关注技术与社会。'
    native, factory = host.bind_native(status_request(planned), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)
    assert native.artifact_sha256 == ready['receipt']['artifact_sha256'] and native.seed == 7
    factory.validate(native)
    # A ready historical status is recoverable; native binding reauthorizes
    # BEFORE path operations at the new project revision.
    project.revision = 2
    assert host.status(status_request(planned))['state'] == 'ready'
    with pytest.raises(PreparationError) as denied:
        host.bind_native(status_request(planned), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)
    assert denied.value.code == 'conflict'


def test_duplicate_start_lost_ack_and_frozen_plan_recovery(tmp_path):
    host, chat, transport, _ = offline_host(tmp_path)
    request = plan_request()
    planned = host.plan(request)
    transport.changed = True
    assert host.plan(request)['plan_sha256'] == planned['plan_sha256']
    wires = []
    def lost(wire):
        wires.append(wire)
        raise RuntimeError('PRIVATE_ACK')
    host.scheduler = lost
    with pytest.raises(PreparationError):
        host.start(status_request(planned))
    assert host.status(status_request(planned))['state'] == 'queued'
    assert host.start(status_request(planned))['state'] == 'queued'
    assert len(wires) == 1 and not chat.calls
    host.generate(wires[0])
    with pytest.raises(PreparationAuthorityError):
        host.generate(wires[0])
    assert host.budget.starts == 1
    divergent = deepcopy(request)
    divergent['options']['seed'] += 1
    with pytest.raises(PreparationError) as conflict:
        host.plan(divergent)
    assert conflict.value.code == 'conflict'


@pytest.mark.parametrize('mode', ['broken_persona', 'broken_config', 'incomplete'])
def test_inherited_failure_holds_started_budget_and_never_publishes(tmp_path, mode):
    host, chat, _, _ = offline_host(tmp_path, chat=ScriptedChat(**{mode: True}))
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire())
    status = host.status(status_request(planned))
    assert status['state'] == 'uncertain' and status['receipt'] is None and status['model_calls_started']
    assert host.budget.rows[planned['operation_id']].state == ReservationState.uncertain
    assert host.budget.settles == 0 and not (tmp_path / ('sim_' + UUID(planned['operation_id']).hex)).exists()
    assert 'PRIVATE' not in json.dumps(status)


def test_default_denial_and_reauthorization_precede_files(tmp_path):
    host, chat, _, project = offline_host(tmp_path, authorized=False)
    planned = host.plan(plan_request())
    with pytest.raises(PreparationError) as denied:
        host.start(status_request(planned))
    assert denied.value.code == 'model_calls_disabled' and not chat.calls
    assert list(tmp_path.iterdir()) == []
    host.authorize = lambda: True
    host.start(status_request(planned))
    project.revision = 2
    row = host.store.get('owner', planned['operation_id'])
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire())
    assert not chat.calls and list(tmp_path.iterdir()) == []


def test_corrupt_publication_and_existing_destination_never_grant_ready(tmp_path):
    host, chat, _, _ = offline_host(tmp_path)
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    root = tmp_path / ('sim_' + UUID(planned['operation_id']).hex)
    root.mkdir()
    (root / 'sentinel').write_text('preserve', encoding='utf-8')
    row = host.store.get('owner', planned['operation_id'])
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire())
    assert host.status(status_request(planned))['state'] == 'uncertain'
    assert (root / 'sentinel').read_text() == 'preserve' and host.budget.settles == 0


def test_bounded_chat_total_calls_and_late_response(monkeypatch):
    import time
    clock = [1.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    calls = []
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: calls.append(kw))))
    first = []
    bounded = BoundedChat(client, checkpoint=lambda: None, first_call=lambda: first.append(1), deadline=5, max_calls=1)
    bounded.create(messages=[])
    with pytest.raises(PreparationError):
        bounded.create(messages=[])
    assert len(calls) == len(first) == 1
    def late(**kw):
        clock[0] = 6
        return None
    client.chat.completions.create = late
    bounded = BoundedChat(client, checkpoint=lambda: None, first_call=lambda: None, deadline=5)
    with pytest.raises(PreparationError):
        bounded.create(messages=[])


def test_frozen_projection_detaches_and_rejects_dangling_edges(tmp_path):
    host, _, _, _ = offline_host(tmp_path)
    graph = host.reader.graph_data('display-1')
    frozen = validate_graph(graph, 'display-1')
    graph['nodes'][0]['attributes']['new'] = 'changed'
    assert not frozen['nodes'][0]['attributes']
    frozen['edges'][0]['source_node_uuid'] = str(uuid4())
    with pytest.raises(PreparationError):
        validate_graph(frozen, 'display-1')


def test_lost_ready_cas_retains_orphan_but_never_grants_binding(tmp_path, monkeypatch):
    host, _, _, _ = offline_host(tmp_path)
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    original = host.store.update
    def lose_ready(principal, dispatch, **changes):
        if changes.get('state') == 'ready':
            raise PreparationAuthorityError('preparation_unavailable')
        return original(principal, dispatch, **changes)
    monkeypatch.setattr(host.store, 'update', lose_ready)
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire())
    status = host.status(status_request(planned))
    assert status['state'] == 'uncertain' and status['receipt'] is None
    assert (tmp_path / ('sim_' + UUID(planned['operation_id']).hex) / 'state.json').exists()
    with pytest.raises(PreparationError):
        host.bind_native(status_request(planned), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)


@pytest.mark.parametrize('name', ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'])
def test_corrupt_published_bytes_denied_by_native_binder(tmp_path, name):
    host, _, _, _ = offline_host(tmp_path)
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    host.generate(row.dispatch.to_wire())
    path = tmp_path / ('sim_' + UUID(planned['operation_id']).hex) / name
    path.chmod(0o644)
    path.write_bytes(b'{}')
    with pytest.raises((PreparationError, ValueError, TypeError)):
        host.bind_native(status_request(planned), run_id=uuid4(), runtime_sha256='a' * 64, model_factory=dict)


def test_activity_cancellation_after_first_call_holds_ceiling(tmp_path):
    chat = ScriptedChat()
    host, _, _, _ = offline_host(tmp_path, chat=chat)
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire(), cancelled=lambda: bool(chat.calls))
    status = host.status(status_request(planned))
    assert status['state'] == 'uncertain' and status['model_calls_started'] and status['receipt'] is None
    assert host.budget.rows[planned['operation_id']].state == ReservationState.uncertain


def test_unproven_client_cleanup_retains_host_and_denies_ready(tmp_path):
    chat = ScriptedChat()
    closed = []
    def close():
        closed.append(1)
        raise RuntimeError('PRIVATE_CLOSE')
    chat.close = close
    host, _, _, _ = offline_host(tmp_path, chat=chat)
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire())
    assert host.status(status_request(planned))['state'] == 'uncertain'
    assert host.drain_cleanup() is False
    chat.close = lambda: closed.append(1)
    assert host.drain_cleanup() is True and len(chat.calls) == 4


class ScriptedBoth(ScriptedChat):
    """Two actual inherited profiles, including the organization prompt path."""
    def create(self, **kwargs):
        if 'agent_configs' in kwargs['messages'][-1]['content']:
            self.calls.append(kwargs)
            data = {'agent_configs': [{'agent_id': i, 'activity_level': 0.5,
                'posts_per_hour': 0.3, 'comments_per_hour': 0.4, 'active_hours': [9, 10],
                'response_delay_min': 5, 'response_delay_max': 20, 'sentiment_bias': 0,
                'stance': 'neutral', 'influence_weight': 1.0} for i in range(2)]}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)), finish_reason='stop')])
        return super().create(**kwargs)


@pytest.mark.parametrize('platforms', [['twitter'], ['reddit'], ['twitter', 'reddit']])
def test_inherited_individual_organization_and_platform_correspondence(tmp_path, platforms):
    host, chat, _, _ = offline_host(tmp_path, chat=ScriptedBoth())
    payload = plan_request()
    payload['options'].update(types=None, max_agents=2, platforms=platforms)
    planned = host.plan(payload)
    assert [a['labels'] for a in planned['actors']] == [['Person'], ['Organization']]
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    host.generate(row.dispatch.to_wire())
    ready = host.status(status_request(planned))
    expected = ['state.json', 'simulation_config.json', 'source_grounding.json'] + [
        p + '_profiles.' + ('csv' if p == 'twitter' else 'json') for p in platforms]
    assert [f['name'] for f in ready['receipt']['files']] == expected
    assert len(chat.calls) == 5
    root = tmp_path / ready['receipt']['simulation_id']
    config = json.loads((root / 'simulation_config.json').read_bytes())
    assert [a['entity_uuid'] for a in config['agent_configs']] == [uid(10), uid(11)]
    assert set(json.loads((root / 'source_grounding.json').read_bytes())) == {uid(10), uid(11)}


@pytest.mark.parametrize('mutation', ['twitter_name', 'reddit_name', 'swapped_names',
    'duplicate_header', 'foreign_header', 'reordered_header', 'extra_column',
    'twitter_username', 'reddit_username', 'twitter_user_char', 'twitter_description',
    'reddit_bio', 'reddit_persona'])
def test_profile_correspondence_mutations_deny_artifacts_before_publication(tmp_path, monkeypatch, mutation):
    host, _, _, _ = offline_host(tmp_path, chat=ScriptedBoth())
    payload = plan_request()
    payload['options'].update(types=None, max_agents=2)
    planned = host.plan(payload)
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    original = host._artifacts
    observed = []

    def mutate_before_validation(current, root):
        assert '_attempts' in root.parts
        twitter_path, reddit_path = root / 'twitter_profiles.csv', root / 'reddit_profiles.json'
        with twitter_path.open('r', encoding='utf-8', newline='') as stream:
            twitter = list(csv.reader(stream))
        reddit = json.loads(reddit_path.read_bytes())
        if mutation == 'duplicate_header':
            twitter[0].append('username')
            for item in twitter[1:]:
                item.append(item[2])
        elif mutation == 'foreign_header':
            twitter[0][4] = 'foreign_description'
        elif mutation == 'reordered_header':
            for item in twitter:
                item[3], item[4] = item[4], item[3]
        elif mutation == 'extra_column':
            twitter[1].append('foreign cell')
        elif mutation == 'swapped_names':
            twitter[1][1], twitter[2][1] = twitter[2][1], twitter[1][1]
            reddit[0]['name'], reddit[1]['name'] = reddit[1]['name'], reddit[0]['name']
        elif mutation.startswith('twitter_'):
            column = {'twitter_name': 1, 'twitter_username': 2,
                      'twitter_user_char': 3, 'twitter_description': 4}[mutation]
            twitter[1][column] += ' divergent'
        else:
            field = mutation.removeprefix('reddit_')
            reddit[0][field] += ' divergent'
        with twitter_path.open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerows(twitter)
        reddit_path.write_text(json.dumps(reddit, ensure_ascii=False), encoding='utf-8')
        with pytest.raises(PreparationError) as denied:
            original(current, root)
        assert denied.value.code == 'preparation_failed'
        observed.append(mutation)
        raise denied.value

    monkeypatch.setattr(host, '_artifacts', mutate_before_validation)
    with pytest.raises(PreparationError):
        host.generate(row.dispatch.to_wire())
    assert observed == [mutation]
    status = host.status(status_request(planned))
    assert status['state'] == 'uncertain' and status['receipt'] is None
    assert host.budget.settles == 0
    assert not (tmp_path / ('sim_' + UUID(planned['operation_id']).hex)).exists()


def test_cross_platform_mapping_uses_inherited_cr_lf_normalization(tmp_path):
    chat = ScriptedChat(persona_fields={'bio': 'line one\r\nline two', 'persona': 'role one\nrole two'})
    host, _, _, _ = offline_host(tmp_path, chat=chat)
    planned = host.plan(plan_request())
    host.start(status_request(planned))
    row = host.store.get('owner', planned['operation_id'])
    host.generate(row.dispatch.to_wire())
    ready = host.status(status_request(planned))
    root = tmp_path / ready['receipt']['simulation_id']
    reddit = json.loads((root / 'reddit_profiles.json').read_bytes())[0]
    with (root / 'twitter_profiles.csv').open('r', encoding='utf-8', newline='') as stream:
        twitter = list(csv.reader(stream))[1]
    from app.services.oasis_profile_generator import OasisProfileGenerator
    profile = SimpleNamespace(name=reddit['name'], user_name=reddit['username'],
                              bio=reddit['bio'], persona=reddit['persona'])
    assert twitter == [str(value) for value in OasisProfileGenerator.twitter_loader_row(profile, 0)]
    assert twitter[4] == 'line one  line two' and twitter[3] == 'line one  line two role one role two'
