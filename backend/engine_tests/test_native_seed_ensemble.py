"""Protected PostgreSQL/Temporal seed ensemble with actual owned native children."""
import asyncio
from uuid import uuid4

import pytest

from app import create_app
from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.native_launch_facade import NativeLaunchFacade
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
from nexaweave_execution.native_run_store import NativeRunStore
from nexaweave_execution.preparation_store import migrate
from nexaweave_execution.temporal_native_host import NativeWorkflowRef, TemporalNativeHost
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from test_connected_preparation_native_launch import RichConnectedChat
from test_native_launch_api import reference
from test_native_launch_store import factory, launch_host, migrate_all
from test_preparation_store import real_host, reference as preparation_reference, request
from test_temporal_connected_launch import loopback_client, temporal_bridge


pytestmark = [pytest.mark.postgres, pytest.mark.native_launch_engine]


@pytest.mark.asyncio
async def test_protected_seed_ensemble_settles_in_order_and_replays(factory, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    migrate_all(factory)
    with factory() as connection:
        migrate(connection, seed_ensembles=True)
        migrate(connection)
    loop = asyncio.get_running_loop()
    temporal = await loopback_client()
    prep, _, retained, chat, _, transport = real_host(factory, tmp_path, cap=12, chat=RichConnectedChat())
    host = launch_host(prep, factory)
    prep_worker = TemporalPreparationHost(
        client=temporal, task_queue='mf-seed-ensemble-prep-' + uuid4().hex,
        trusted_host=prep, allow_dispatch=lambda: True,
    )
    prep.scheduler = prep_worker.scheduler_for(loop)
    supervisors = {}
    launched = []

    def construct(native_request):
        assert native_request.run_id not in supervisors
        supervisor = host.supervisor_factory(native_request)
        supervisors[native_request.run_id] = supervisor
        return supervisor

    native = TemporalNativeHost(
        client=temporal, task_queue='mf-seed-ensemble-' + uuid4().hex, trusted_principal='owner',
        supervisor_factory=construct, allow_dispatch=lambda: True,
    )
    host.attach_temporal(native, temporal_bridge(native, loop))
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: prep.settings))
    headers = {'Authorization': 'Bearer ' + prep.settings.token}
    graph = prep.display_graph_id

    def http_client(preparation, launch):
        return create_app(
            preparation_facade=KnowledgePreparationFacade(prep.settings, host=preparation),
            native_launch_facade=NativeLaunchFacade(prep.settings, host=launch),
        ).test_client()

    async def post(client, route, payload, *, status=200, code=None):
        response = await asyncio.wait_for(
            asyncio.to_thread(client.post, route, json=payload, headers=headers), 30,
        )
        assert response.status_code == status
        assert response.headers['Cache-Control'] == 'no-store'
        if code is not None:
            assert response.json == {'success': False, 'error': {'code': code}}
        else:
            assert response.json['success'] is True
            return response.json['data']

    client = http_client(prep, host)
    prep_route = f'/api/preparation/{graph}'
    launch_route = '/api/native-launch'
    try:
        async with prep_worker.worker(), native.worker():
            payload = request(retained)
            payload['options'].update(types=['Person', 'Organization'], max_agents=2, max_rounds=1)
            plan = await post(client, prep_route + '/plan', payload)
            parent_ref = preparation_reference(plan)
            await post(client, prep_route + '/start', parent_ref)
            parent = prep.store.get('owner', plan['operation_id'])
            await asyncio.wait_for(temporal.get_workflow_handle(parent.dispatch.workflow_id).result(), 60)
            ready = await post(client, prep_route + '/status', parent_ref)
            assert ready['state'] == 'ready'
            parent = prep.store.get('owner', plan['operation_id'])
            parent_root = tmp_path / ready['receipt']['simulation_id']
            original = {pin['name']: (parent_root / pin['name']).read_bytes() for pin in ready['receipt']['files']}
            calls = (len(chat.calls), transport.calls)
            seed = payload['options']['seed']
            declaration = {**parent_ref, 'idempotency_key': str(uuid4()),
                           'seeds': [(seed + 1) % 2**32, (seed + 2) % 2**32]}
            route = prep_route + '/seed_ensemble'
            pair = await post(client, route, declaration)
            assert pair['state'] == 'ready' and [member['seed'] for member in pair['members']] == declaration['seeds']
            assert (len(chat.calls), transport.calls) == calls
            await post(client, route, {**declaration, 'seeds': declaration['seeds'][::-1]},
                       status=409, code='conflict')
            plans = []
            for member in pair['members']:
                preparation = {key: member[key] for key in ('operation_id', 'plan_sha256')}
                launch = {'schema_version': 1, 'launch_id': member['launch_id'], 'preparation': preparation}
                dto = await post(client, f'{launch_route}/plan/{graph}', launch)
                assert dto['request']['seed'] == member['seed']
                assert dto['request']['artifact_sha256'] == member['receipt']['artifact_sha256']
                row = prep.store.get('owner', member['operation_id'], member['plan_sha256'])
                assert row.state == 'ready' and row.receipt == member['receipt']
                assert row.attempt_id is row.budget_attempt_id is None and not row.model_calls_started
                plans.append(dto)

            await post(client, f'{launch_route}/start/{graph}', reference(plans[1]), status=503, code='busy')
            with factory() as connection:
                reserved = connection.execute(
                    'SELECT operation_id FROM mf_execution.reservations WHERE account_id=%s', (host.account_id,),
                ).fetchall()
            assert reserved == [(parent.operation_id,)] and not supervisors

            children = []
            for dto, member in zip(plans, pair['members'], strict=True):
                ref = reference(dto)
                await post(client, f'{launch_route}/start/{graph}', ref)
                row = host.store.get('owner', member['launch_id'])
                launched.append(row.request)
                receipt = await asyncio.wait_for(native.result(row.request, NativeWorkflowRef(**row.workflow)), 180)
                assert receipt.outcome == 'completed'
                local = await asyncio.wait_for(native.retry_cleanup(row.request), 20)
                assert not local.cleanup_pending and not local.owner_thread_alive
                final = await post(client, f'{launch_route}/status/{graph}', ref)
                record = NativeRunStore(factory).get('owner', row.run_id)
                assert final['state'] == 'completed' and final['receipt'] == record.receipt.to_wire() == receipt.to_wire()
                assert record.child.instance_id == receipt.instance_id
                children.append(record.child.instance_id)
                with factory() as connection:
                    budget = host.budget._row(connection, host.account_id, row.run_id)
                assert budget.state.value == 'settled' and budget.attempt_id == row.budget_attempt_id
                assert type(budget.receipt) is NativeBudgetReceipt
                assert budget.receipt.launch_sha256 == row.launch_sha256 and budget.receipt.native_receipt == receipt
                driver = supervisors[row.run_id].coordinator.driver
                assert driver._closed and driver._owned is None
                root = tmp_path / member['receipt']['simulation_id']
                assert (root / '.native_prepared_start_claim').is_file()
                assert all((root / (platform + '_simulation.db')).is_file() for platform in row.request.platforms)
            assert len(set(children)) == 2
            assert len({ready['receipt']['simulation_id'], *(member['receipt']['simulation_id']
                                                              for member in pair['members'])}) == 3
            assert not (parent_root / '.native_prepared_start_claim').exists()

            reopened = type(prep)(
                settings=prep.settings, connection_factory=factory, read_facade=prep.reader,
                artifact_root=prep.root, account_id=prep.account_id,
            )
            recovered_host = launch_host(reopened, factory)
            recovered_host.attach_temporal(native, temporal_bridge(native, loop))
            recovered = http_client(reopened, recovered_host)
            assert await post(recovered, route, declaration) == pair
            for dto in plans:
                replay = await post(recovered, f'{launch_route}/start/{graph}', reference(dto))
                assert replay['state'] == 'completed'
            assert len(supervisors) == 2 and prep.store.get('owner', parent.operation_id) == parent
            assert {name: (parent_root / name).read_bytes() for name in original} == original
    finally:
        for supervisor in supervisors.values():
            assert await asyncio.to_thread(supervisor.close, 20)
        for native_request in launched:
            local = await asyncio.wait_for(native.retry_cleanup(native_request), 20)
            assert not local.cleanup_pending and not local.owner_thread_alive
