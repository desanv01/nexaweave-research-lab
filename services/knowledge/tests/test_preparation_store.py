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


def real_host(factory, tmp_path, *, cap=12, authorized=True, chat=None):
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
        read_facade=facade, artifact_root=tmp_path, account_id=account, ceiling_microusd=4,
        authorize=lambda: authorized, chat_client_factory=lambda: chat,
        model_name='scripted', base_url='injected://fixture', scheduler=wires.append)
    return host, scope, retained, chat, wires, transport


def request(retained):
    from test_durable_preparation import plan_request
    return plan_request(str(retained.source_revision))


def reference(dto):
    return {'schema_version': 1, 'operation_id': dto['operation_id'], 'plan_sha256': dto['plan_sha256']}


def test_pg_restart_duplicate_race_and_actual_inherited_publication(factory, tmp_path):
    host, scope, retained, chat, wires, transport = real_host(factory, tmp_path)
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
        migrate(conn)
    with factory() as conn:
        with pytest.raises(PreparationAuthorityError):
            with conn.transaction():
                conn.execute('CREATE TABLE mf_preparation.unexpected(id integer)')
                migrate(conn)
    with factory() as conn:
        assert conn.execute('SELECT version,checksum,schema_checksum FROM mf_execution.schema_migrations').fetchall() == legacy
        migrate(conn)
