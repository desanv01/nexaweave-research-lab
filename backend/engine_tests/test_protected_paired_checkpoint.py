"""Protected paired capture and continuation through PostgreSQL and Temporal."""

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
import json
from multiprocessing import active_children
from pathlib import Path
from uuid import uuid4

import pytest

from app import create_app
from app.services.durable_native_launch_host import DurableNativeLaunchHost
from app.services.knowledge_preparation_facade import KnowledgePreparationFacade
from app.services.knowledge_read_facade import ReadHostSettings
from app.services.native_launch_facade import NativeLaunchFacade
from app.services.native_launch_models import BoundedNativeModelFactory
from app.services.native_observations_facade import NativeObservationsFacade
from app.services.native_observations_host import NativeObservationsHost
from nexaweave_execution.native_launch_contracts import NativeBudgetReceipt
from nexaweave_execution.native_paired_checkpoint_binding import NativePairedCheckpointFactory
from nexaweave_execution.native_run_contracts import NativeRunDenied, RunState
from nexaweave_execution.temporal_native_host import TemporalNativeHost
from nexaweave_execution.temporal_preparation_host import TemporalPreparationHost
from test_connected_preparation_native_launch import CheckpointConnectedChat
from test_durable_native_launch import LIMITS, scripted_native_backends
from test_native_launch_api import reference
from test_native_launch_store import declaration, factory, migrate_all
from test_preparation_store import real_host, reference as preparation_reference, request
from test_temporal_connected_launch import loopback_client, temporal_bridge


@pytest.mark.postgres
@pytest.mark.native_launch_engine
@pytest.mark.asyncio
async def test_protected_paired_capture_and_continuation(factory, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('NEXAWEAVE_APP_MODE', 'research_local')
    monkeypatch.delenv('FLASK_HOST', raising=False)
    monkeypatch.delenv('NEXAWEAVE_ALLOWED_ORIGINS', raising=False)
    migrate_all(factory)
    temporal = await loopback_client()
    loop = asyncio.get_running_loop()
    prep, scope, retained, _, _, _ = real_host(factory, tmp_path, cap=12, chat=CheckpointConnectedChat())
    prep_worker = TemporalPreparationHost(
        client=temporal, task_queue='mf-paired-prep-' + uuid4().hex,
        trusted_host=prep, allow_dispatch=lambda: True,
    )
    prep.scheduler = prep_worker.scheduler_for(loop)
    platforms = ('twitter', 'reddit')
    model = BoundedNativeModelFactory(platforms, dict(LIMITS), scripted_native_backends,
                                     cooperative_transport=True)
    host = DurableNativeLaunchHost(
        preparation_host=prep, connection_factory=factory, runtime_sha256='c' * 64,
        model_label='scripted paired checkpoint', limits=LIMITS, model_factory=model,
        account_id=prep.account_id, ceiling_microusd=4, authorize=lambda: True,
    )
    supervisors = {}

    def construct(native_request):
        assert native_request.run_id not in supervisors
        supervisor = host.supervisor_factory(native_request)
        assert type(supervisor.coordinator.driver._factory) is NativePairedCheckpointFactory
        supervisors[native_request.run_id] = supervisor
        return supervisor

    native = TemporalNativeHost(
        client=temporal, task_queue='mf-paired-native-' + uuid4().hex,
        trusted_principal='owner', supervisor_factory=construct, allow_dispatch=lambda: True,
    )
    host.attach_temporal(native, temporal_bridge(native, loop))
    monkeypatch.setattr(ReadHostSettings, 'from_config', classmethod(lambda cls, config: prep.settings))
    http = create_app(
        preparation_facade=KnowledgePreparationFacade(prep.settings, host=prep),
        native_launch_facade=NativeLaunchFacade(prep.settings, host=host),
        native_observations_facade=NativeObservationsFacade(prep.settings, host=NativeObservationsHost(launch_host=host)),
    ).test_client()
    graph = prep.display_graph_id
    headers = {'Authorization': 'Bearer ' + prep.settings.token}

    async def post(route, payload, code=None):
        reply = await asyncio.wait_for(asyncio.to_thread(http.post, route, json=payload, headers=headers), 30)
        assert reply.headers['Cache-Control'] == 'no-store'
        if code is not None:
            assert reply.status_code == 409 and reply.json == {'success': False, 'error': {'code': code}}
            return None
        assert reply.status_code == 200 and reply.json['success'], reply.json
        return reply.json['data']

    async def run(planned):
        ref = reference(planned)
        start = await post(f'/api/native-launch/start/{graph}', ref)
        row = host.store.get('owner', ref['launch_id'])
        assert row.dispatch_claimed and start['workflow']['workflow_id'] == row.workflow['workflow_id']
        await asyncio.wait_for(temporal.get_workflow_handle(row.workflow['workflow_id']).result(), 100)
        cleanup = await asyncio.wait_for(native.retry_cleanup(row.request), 20)
        assert not cleanup.cleanup_pending and not cleanup.owner_thread_alive
        final = await post(f'/api/native-launch/status/{graph}', ref)
        assert final['state'] == 'completed'
        result = host.native.get('owner', row.run_id)
        assert result.state == RunState.completed and result.request == row.request
        assert result.receipt.to_wire() == final['receipt']
        assert result.child.process_id not in {child.pid for child in active_children()}
        driver = supervisors[row.run_id].coordinator.driver
        assert driver._closed and driver._owned is None
        with factory() as conn:
            budget = host.budget._row(conn, host.account_id, row.run_id)
        assert type(budget.receipt) is NativeBudgetReceipt and budget.state.value == 'settled'
        assert budget.receipt.native_receipt == result.receipt
        assert (await post(f'/api/native-launch/start/{graph}', ref))['receipt'] == final['receipt']
        return result, driver._factory

    @asynccontextmanager
    async def owned_workers():
        async with prep_worker.worker(), native.worker():
            try:
                yield
            finally:
                drained = await asyncio.wait_for(native.drain_retained(15), 20)
                assert drained.complete and drained.admission_closed and not drained.pending_run_ids
                assert drained.retained_count == drained.closed_count

    async with owned_workers():
        payload = request(retained)
        payload['options'].update(platforms=list(platforms), types=['Person', 'Organization'],
                                  seed=7, max_agents=2, max_rounds=2)
        planned = await post(f'/api/preparation/{graph}/plan', payload)
        prep_ref = preparation_reference(planned)
        await post(f'/api/preparation/{graph}/start', prep_ref)
        pending = prep.store.get('owner', planned['operation_id'])
        await asyncio.wait_for(temporal.get_workflow_handle(pending.dispatch.workflow_id).result(), 60)
        ready = await post(f'/api/preparation/{graph}/status', prep_ref)
        assert ready['state'] == 'ready' and len(ready['actors']) == 2
        prepared_root = prep.root / ready['receipt']['simulation_id']
        originals = {path.name: sha256(path.read_bytes()).hexdigest() for path in prepared_root.iterdir()}

        spec = dict(declaration(planned), schema_version=5, checkpoint={'mode': 'capture'})
        capture = await post(f'/api/native-launch/plan/{graph}', spec)
        capture_request = host.store.get('owner', capture['request']['run_id']).request
        with pytest.raises(NativeRunDenied):
            host.native.register(capture_request)
        producer, capture_factory = await run(capture)
        checkpoint = Path(capture_factory.checkpoint_dir)
        manifest_bytes = (checkpoint / 'manifest.json').read_bytes()
        stored = json.loads(manifest_bytes)
        assert stored['completed_round'] == 1 and producer.receipt.evidence_sha256 == sha256(manifest_bytes).hexdigest()
        assert stored['anchors']['scope']['knowledge_scope'] == scope.model_dump(mode='json')
        assert stored['anchors']['scope']['producer_pid'] == producer.child.process_id
        inventory = {path.name: sha256(path.read_bytes()).hexdigest() for path in checkpoint.iterdir()}

        selector = {'mode': 'continue', 'source_run_id': str(producer.request.run_id),
                    'checkpoint_sha256': producer.receipt.evidence_sha256}
        spec = dict(declaration(planned), schema_version=5, checkpoint=selector)
        await post(f'/api/native-launch/plan/{graph}',
                   dict(spec, checkpoint=dict(selector, checkpoint_sha256='0' * 64)), code='conflict')
        cancelled = await post(f'/api/native-launch/plan/{graph}', dict(spec, launch_id=str(uuid4())))
        cancel_ref = reference(cancelled)
        assert (await post(f'/api/native-launch/cancel/{graph}', cancel_ref))['state'] == 'cancelled'
        assert (await post(f'/api/native-launch/start/{graph}', cancel_ref))['state'] == 'cancelled'
        continuation = await post(f'/api/native-launch/plan/{graph}', spec)
        restored, restore_factory = await run(continuation)
        assert restored.request.schema_version == 5 and restored.request.source_run_id == producer.request.run_id
        assert restored.child.instance_id != producer.child.instance_id
        assert restored.child.process_id != producer.child.process_id
        assert restore_factory.expected_producer_instance_id == producer.child.instance_id
        output = Path(restore_factory.output_dir)
        for platform in platforms:
            prefix = (checkpoint / (platform + '.jsonl')).read_bytes()
            recording = (output / platform / 'actions.jsonl').read_bytes()
            assert recording.startswith(prefix)
            events = [json.loads(line) for line in recording[len(prefix):].splitlines()]
            assert any(event.get('round') == 2 and event.get('action_type') == 'CREATE_POST' for event in events)
            page = await post(f'/api/native-observations/page/{graph}',
                              dict(reference(continuation), platform=platform, offset=0, limit=20))
            assert page['schema_version'] == 5 and page['platform'] == platform
            assert page['counts']['action_records'] >= 2
        assert {path.name: sha256(path.read_bytes()).hexdigest() for path in checkpoint.iterdir()} == inventory
        assert {path.name: sha256(path.read_bytes()).hexdigest() for path in prepared_root.iterdir()} == originals
        assert len(supervisors) == 2 and host.budget.status('owner', host.account_id).accounted_ceiling_microusd == 12
